"""把所有找得到來源 MIDI 的譜面，一次還原力度與延音踏板。

轉譜時力度和 CC64 踏板會被丟掉（獨立的 midi_to_xml_converter 根本沒讀 CC64），
所以合成鋼琴模式在沒還原過的譜面上只會聽到「每個音都被切掉」。這支工具把
`UserSongs` 底下的譜面逐一配對來源 MIDI 並還原。

**配對不靠檔名**。檔名幾乎對不上（syuten ↔ syūten_midi.mid、Melodiniq ↔
Melodiniq (piano version).mid），而且猜錯的代價是把整份錯的力度寫進譜面。改成
拿還原器自己當裁判：候選 MIDI 全部試一遍，用「音高完全吻合的音符比例」評分，
只有分數夠高的才會被寫入。配錯的 MIDI 分數會趨近 0，天然被擋掉。

用法：
    python batch_restore_expression.py            # 只分析，不寫檔
    python batch_restore_expression.py --apply    # 真的寫回譜面
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from qt_editor.models import NoteModel

# 低於這個「音高完全吻合」比例就不採用——寧可留著沒還原，也不要寫入錯的力度。
MIN_EXACT_RATIO = 0.90

# 有些譜面連逐音音高都沒有（純鍵道譜），音高比對無從下手。這種譜在合成鋼琴模式
# 下是「一個音都不會響」，不是少了表情而已，所以更需要救。
#
# 起音時間單獨看**不夠**：譜面都量化在格點上，實測拿完全不相干的曲子去比也有
# 66% 的起音會撞上（Croatian_Rhapsody 對到 anima 的 MIDI）。所以再加一道「音符
# 排列」檢定——譜面的鍵道和 MIDI 的音高都編碼了旋律走向，把配對到的音算相關
# 係數，對的曲子是 +0.89，不相干的都在 +0.2 附近。有這道閘門之後，起音的門檻
# 反而可以放寬，讓刪過音符的譜面也過得了。
MIN_ONSET_RATIO = 0.70
MIN_CONTOUR_CORRELATION = 0.70
ONSET_TOLERANCE_MS = 5
# 相關係數要有意義所需的最少配對數。
MIN_CONTOUR_SAMPLES = 30
# 候選預篩：音符數與長度差太多的直接跳過，省下逐一比對的時間。
MAX_NOTE_COUNT_RATIO = 3.0
MAX_DURATION_RATIO = 2.5

SKIP_NAMES = {"register.json", "songlist.json", "piano_samples.json"}


def find_midis(roots: Sequence[Path]) -> List[Path]:
    found: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*.mid", "*.midi"):
            found.extend(sorted(root.rglob(pattern)))
    # 同名檔案只留一份
    seen = set()
    unique = []
    for path in found:
        key = path.name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_charts(songs_root: Path) -> List[Path]:
    charts = [p for p in sorted(songs_root.rglob("*.json")) if p.name not in SKIP_NAMES]
    charts += sorted(songs_root.rglob("*.xml"))
    return charts


def load_chart(path: Path) -> Optional[NoteModel]:
    model = NoteModel()
    try:
        if path.suffix.lower() == ".json":
            model.load_json(str(path))
        else:
            model.load_xml(str(path))
    except Exception:
        return None
    return model if model.notes_tree else None


class MidiSource:
    """一份載入好的來源 MIDI，重複使用避免反覆解析。"""

    def __init__(self, path: Path, model: NoteModel):
        self.path = path
        self.notes = model.notes_tree
        self.pedal = model.pedal_spans
        self.note_count = len(self.notes)
        self.duration = max((n.end for n in self.notes), default=0.0)
        self.has_velocity = any(n.velocity is not None for n in self.notes)
        self.pitch_groups: Dict[int, List[int]] = {}
        for note in self.notes:
            if note.pitch is not None:
                self.pitch_groups.setdefault(int(note.start), []).append(int(note.pitch))
        self.onsets = sorted(self.pitch_groups)


def estimate_offset(chart_onsets: Sequence[int], source_onsets: Sequence[int],
                    max_offset_ms: int = 40_000) -> int:
    """猜譜面和 MIDI 之間的固定時間位移（ms）。

    **方向**：回傳的是「**譜面**時間要加多少才落到**來源**上」，也就是
    `chart + offset ≈ source`。要把來源搬到譜面的時間軸上時得取負號。
    這個號差我寫錯過一次，症狀是配對率 100% 但音高一顆都套不上——因為來源被
    往反方向搬了兩倍的距離。

    有些譜面整份被平移過——Testify_mv 就比它的來源 MIDI 晚 6700ms，起音其實
    1437/1437 全對得上，但按絕對時間比只剩 15%，配對器因此判定「找不到來源」。

    **要對全體投票，不能只看附近。** 第一版是抓每個譜面起音「最近的幾個」MIDI
    起音去投票，那只找得到幾百 ms 以內的位移——6.7 秒的真答案根本進不了那個窗，
    結果回報 +42ms（對上 33%），比不修還糟。現在是抽樣的譜面起音對**所有** MIDI
    起音投票，範圍限制在 ±40 秒。

    真的只差一個位移時這個峰非常尖銳（Testify_mv 是第二名的 11 倍）；不相干的
    兩首歌則攤平，找不出峰就回 0，退回原本的行為。
    """
    from collections import Counter

    if not chart_onsets or not source_onsets:
        return 0

    # 抽樣夠用了，而且把成本壓在 200 × M。
    sample = chart_onsets[::max(1, len(chart_onsets) // 200)]
    votes: Counter = Counter()
    for when in sample:
        for candidate in source_onsets:
            delta = candidate - when
            if -max_offset_ms <= delta <= max_offset_ms:
                votes[delta // 10 * 10] += 1
    if not votes:
        return 0

    # 不拿直方圖的高度當判準。音樂本來就重複，差一個小節的位移也會對上一大堆
    # ——Testify_mv 的真答案是 224 票，第二名 148 票，用「要高過第二名幾倍」
    # 這種規則不是擋掉真的就是放進假的。改成把排前面的幾個桶各自細調到 1ms，
    # **直接量哪個位移真的對得最準**，那個數字才是我們要的東西。
    source_set = set(source_onsets)
    best_delta, best_hits = 0, 0
    for coarse, _count in votes.most_common(8):
        for delta in range(coarse - 12, coarse + 13):
            hits = sum(1 for when in sample if when + delta in source_set)
            if hits > best_hits:
                best_delta, best_hits = delta, hits

    # 對不到一半就不是同一首歌，別硬套一個位移。真的是同一首時這個值接近 100%。
    if best_hits < max(8, len(sample) * 0.5):
        return 0
    return int(best_delta)


def shifted_notes(notes: Sequence[Any], delta: int) -> List[Any]:
    """把來源音符整份平移。位移是加在**來源**上，讓它對齊譜面的時間軸。"""
    if not delta:
        return list(notes)
    moved = []
    for note in notes:
        clone = copy.copy(note)
        clone.start = int(note.start) + delta
        clone.end = int(note.end) + delta
        moved.append(clone)
    return moved


def onset_agreement(chart: NoteModel, source: MidiSource) -> Tuple[float, float]:
    """比對起音時間，並檢查旋律走向是否一致。

    回傳 (起音吻合率, 鍵道↔音高相關係數)。第二個值才是真正能分辨真假的：
    量化過的譜面拿去跟任何一首歌比，起音都會撞上一大堆，但只有真的同一首，
    「哪個時間點彈得比較高」才會一致。
    """
    import bisect
    import math

    chart_groups: Dict[int, List[float]] = {}
    for note in chart.notes_tree:
        chart_groups.setdefault(int(note.start), []).append(
            (int(note.min_key) + int(note.max_key)) / 2.0)
    chart_onsets = sorted(chart_groups)
    if not chart_onsets or not source.onsets:
        return (0.0, 0.0)

    # 整份被平移過的譜面要先對齊，不然按絕對時間比會全部落空。
    offset = estimate_offset(chart_onsets, source.onsets)

    hit = 0
    lanes: List[float] = []
    pitches: List[float] = []
    for when in chart_onsets:
        shifted = when + offset
        index = bisect.bisect_left(source.onsets, shifted)
        window = source.onsets[max(0, index - 2):index + 3]
        if not window:
            continue
        nearest = min(window, key=lambda candidate: abs(candidate - shifted))
        if abs(nearest - shifted) > ONSET_TOLERANCE_MS:
            continue
        hit += 1
        group = chart_groups[when]
        lanes.append(sum(group) / len(group))
        source_group = source.pitch_groups[nearest]
        pitches.append(sum(source_group) / len(source_group))

    rate = hit / len(chart_onsets)
    if len(lanes) < MIN_CONTOUR_SAMPLES:
        return (rate, 0.0)

    mean_lane = sum(lanes) / len(lanes)
    mean_pitch = sum(pitches) / len(pitches)
    covariance = sum((a - mean_lane) * (b - mean_pitch) for a, b in zip(lanes, pitches))
    lane_spread = math.sqrt(sum((a - mean_lane) ** 2 for a in lanes))
    pitch_spread = math.sqrt(sum((b - mean_pitch) ** 2 for b in pitches))
    if lane_spread <= 0 or pitch_spread <= 0:
        return (rate, 0.0)
    return (rate, covariance / (lane_spread * pitch_spread))


def chart_has_pitch(chart: NoteModel) -> bool:
    return any(n.pitch is not None for n in chart.notes_tree)


def load_midis(paths: Sequence[Path]) -> List[MidiSource]:
    sources: List[MidiSource] = []
    for path in paths:
        model = NoteModel()
        try:
            model.load_midi(str(path), auto_arrange=False)
        except Exception as exc:
            print("  略過 %s（無法讀取：%s）" % (path.name, exc))
            continue
        if not model.notes_tree:
            continue
        source = MidiSource(path, model)
        if not source.has_velocity and not source.pedal:
            continue  # 沒有可還原的東西
        sources.append(source)
    return sources


def plausible(chart: NoteModel, source: MidiSource) -> bool:
    chart_notes = len(chart.notes_tree)
    if chart_notes == 0 or source.note_count == 0:
        return False
    ratio = max(chart_notes, source.note_count) / min(chart_notes, source.note_count)
    if ratio > MAX_NOTE_COUNT_RATIO:
        return False
    chart_duration = max((n.end for n in chart.notes_tree), default=0.0)
    if chart_duration > 0 and source.duration > 0:
        span = max(chart_duration, source.duration) / min(chart_duration, source.duration)
        if span > MAX_DURATION_RATIO:
            return False
    return True


def score_source(probe: NoteModel, source: MidiSource) -> float:
    """這份 MIDI 能對上譜面多少音符（音高完全吻合的比例）。

    評分用同一個 probe 模型重複跑，不必每次重新讀檔：分數只看音高對位，前一次
    嘗試寫進去的力度不影響結果。真正要保留的那一份會另外從乾淨的檔案重跑。
    """
    stats = probe.apply_midi_expression_from_source(source.notes, source.pedal)
    return stats["matched_exact"] / max(1, stats["total_notes"])


def describe_pedal(model: NoteModel) -> str:
    spans = model.pedal_spans
    if not spans:
        return "踏板 0 段"
    lengths = sorted(b - a for a, b in spans)
    return "踏板 %3d 段（最長 %.1fs）" % (len(spans), lengths[-1] / 1000.0)


def write_report(path: Path, rows: Sequence[Dict[str, object]], applied: bool) -> None:
    """把逐份譜面的狀態寫成 markdown。

    分兩張表，因為兩種缺法要做的事完全不同：**配不到 MIDI** 的只要把來源找回來
    就有救；**來源沒有 CC64** 的是那份 MIDI 根本沒踩過踏板，再找同一份也沒用，
    要嘛換一份有踏板的錄音，要嘛就只能讓遊戲用 AutoPedal 生。
    """
    def flag(value: object) -> str:
        return "有" if value else "**無**"

    total = len(rows)
    with_pitch = sum(1 for r in rows if r["pitch"])
    with_velocity = sum(1 for r in rows if r["velocity"])
    with_pedal = sum(1 for r in rows if r["pedal"])

    out: List[str] = []
    out.append("# 譜面表情還原狀態")
    out.append("")
    out.append("自動產生 — `python qt_editor/batch_restore_expression.py --report`"
               "（%s）。" % ("已寫回譜面" if applied else "分析模式，未寫檔"))
    out.append("")
    out.append("## 目前狀態")
    out.append("")
    out.append("| | 份數 |")
    out.append("|---|---:|")
    out.append("| 譜面總數 | %d |" % total)
    out.append("| 有音高 | %d |" % with_pitch)
    out.append("| **可用合成鋼琴**（有音高＋力度） | **%d** |"
               % sum(1 for r in rows if r["pitch"] and r["velocity"]))
    auto = sum(1 for r in rows if r["pedal"] and r["auto_pedal"])
    out.append("| 其中有踏板 | %d |" % with_pedal)
    out.append("| ├ 來自來源 MIDI 的 CC64 | %d |" % (with_pedal - auto))
    out.append("| └ generate_pedal.py 生成 | %d |" % auto)
    out.append("| 尚未還原（缺力度） | %d |" % (total - with_velocity))
    out.append("| 缺踏板 | %d |" % (total - with_pedal))
    out.append("")
    out.append("沒有力度的譜面在 Performance / Hardcore 模式下不會有鋼琴聲；"
               "沒有音高的更是完全不發聲。缺踏板不會沒聲音，但每個音都在放開鍵的"
               "瞬間斷掉，聽起來像沒有共鳴的電子琴。")
    out.append("")

    no_cc64 = [r for r in rows if r["outcome"] == "來源沒有 CC64"]
    unmatched = [r for r in rows if r["outcome"] in ("配不到 MIDI", "沒有相近 MIDI")]

    if no_cc64:
        out.append("## 來源 MIDI 本身沒有 CC64（%d 份）" % len(no_cc64))
        out.append("")
        out.append("配對是準的（吻合率就在右邊），但這些 MIDI 從頭到尾沒有踏板事件——"
                   "多半是照譜打出來的，不是真人彈的錄音。要真踏板只能另外找一份有"
                   "延音的版本；否則就留給遊戲內的 AutoPedal。")
        out.append("")
        out.append("| 譜面 | 來源 MIDI | 吻合 |")
        out.append("|---|---|---:|")
        for row in sorted(no_cc64, key=lambda r: str(r["label"])):
            out.append("| `%s` | `%s` | %.0f%% |"
                       % (row["label"], row["midi"], 100 * float(row["ratio"])))
        out.append("")

    if unmatched:
        out.append("## 配不到來源 MIDI（%d 份）" % len(unmatched))
        out.append("")
        out.append("找到 MIDI 後放進 `Downloads`，然後：")
        out.append("")
        out.append("```")
        out.append("cd qt_editor")
        out.append("python batch_restore_expression.py --only-missing            # 先看配對率")
        out.append("python batch_restore_expression.py --only-missing --apply    # 確認後寫入")
        out.append("```")
        out.append("")
        out.append("配對不靠檔名，而是用**起音時間＋旋律走向相關性**驗證，"
                   "配錯的 MIDI 分數會趨近 0。")
        out.append("")
        out.append("| 譜面 | 音符 | 音高 | 力度 | 最佳吻合 |")
        out.append("|---|---:|:---:|:---:|---:|")
        for row in sorted(unmatched, key=lambda r: str(r["label"])):
            out.append("| `%s` | %d | %s | %s | %.0f%% |"
                       % (row["label"], row["notes"], flag(row["pitch"]),
                          flag(row["velocity"]), 100 * float(row["ratio"])))
        out.append("")

    path.write_text("\n".join(out), encoding="utf-8")


def onset_groups(notes: Sequence[Any]) -> List[List[Any]]:
    """照起音分組，回傳按時間排序的組清單。"""
    buckets: Dict[int, List[Any]] = {}
    for note in notes:
        buckets.setdefault(int(note.start), []).append(note)
    return [buckets[key] for key in sorted(buckets)]


def transfer_from_sibling(target: NoteModel, source: NoteModel) -> Dict[str, int]:
    """把同曲另一份譜面的音高／力度／踏板照**序位**搬過來。

    給「時間軸被拉伸過」的變體用。Recollect Lines 的 `_ele` 版和本體是同一份譜面
    （1554 組對 1554 組、每組音符數 100% 相同、lane 排列 99.2% 相同），但逐點時間差
    從 0 到 2559ms 都有、比值在 1.013~1.246 之間跑——那是伸縮不是平移，所以
    `estimate_offset` 那條路救不了它（最佳吻合只有 5%）。

    序位配對就完全不受影響：第 i 組對第 i 組，組內按 lane 排序後逐一對應。踏板則交給
    `apply_midi_expression_from_source`，它在時間配對失敗時會退回序位對位，再用分段
    線性把踏板時間重映射到目標的時間軸上。

    前提很嚴：**每一組的音符數都要相同**。不同就代表不是同一份譜面，寧可不搬。
    """
    stats = {'groups': 0, 'pitch': 0, 'velocity': 0, 'lane_mismatch': 0,
             'pedal_before': len(target.pedal_spans), 'pedal_after': 0}
    src_groups = onset_groups(source.notes_tree)
    dst_groups = onset_groups(target.notes_tree)
    if len(src_groups) != len(dst_groups):
        return stats
    if any(len(a) != len(b) for a, b in zip(src_groups, dst_groups)):
        return stats

    for src, dst in zip(src_groups, dst_groups):
        stats['groups'] += 1
        # 組內用鍵道排序當配對依據——同一份譜面的同一組，鍵道排列本來就一樣。
        src_sorted = sorted(src, key=lambda n: (int(n.min_key), int(n.pitch or 0)))
        dst_sorted = sorted(dst, key=lambda n: int(n.min_key))
        if [int(n.min_key) for n in src_sorted] != [int(n.min_key) for n in dst_sorted]:
            stats['lane_mismatch'] += 1
        for s_note, d_note in zip(src_sorted, dst_sorted):
            if s_note.pitch is None:
                continue
            d_note.pitch = int(s_note.pitch)
            stats['pitch'] += 1
            if s_note.velocity is not None:
                d_note.velocity = int(s_note.velocity)
                stats['velocity'] += 1

    # 踏板：重用已經測過的序位對位＋分段線性重映射。
    target.apply_midi_expression_from_source(
        source.notes_tree, source.pedal_spans, restore_velocity=False)
    stats['pedal_after'] = len(target.pedal_spans)

    # 來源的踏板如果本身是猜出來的，這個旗標要跟著搬——不然生成的踏板會被洗成
    # 看起來像真的，hardcore 的閘門就分不出來了。
    origin = str(source.json_meta.get('pedal_origin', ''))
    if stats['pedal_after'] and origin in ('auto', 'mixed'):
        target.json_meta['pedal_origin'] = origin
    return stats


def run_sibling_transfer(songs_root: Path, apply: bool) -> int:
    """替「完全沒有音高、但同曲有結構相同的來源」的譜面搬表情。"""
    charts = [p for p in find_charts(songs_root) if p.suffix.lower() == '.json']
    loaded: Dict[Path, NoteModel] = {}
    for path in charts:
        model = load_chart(path)
        if model is not None:
            loaded[path] = model

    jobs = []
    for path, model in loaded.items():
        if chart_has_pitch(model):
            continue
        song = path.relative_to(songs_root).parts[0]
        for other, source in loaded.items():
            if other == path or other.relative_to(songs_root).parts[0] != song:
                continue
            if not chart_has_pitch(source):
                continue
            if [len(g) for g in onset_groups(source.notes_tree)] !=                [len(g) for g in onset_groups(model.notes_tree)]:
                continue
            jobs.append((path, model, other, source))
            break

    print("可用同曲來源搬表情的譜面：%d 份" % len(jobs))
    print("")
    results = []
    for path, model, other, source in jobs:
        stats = transfer_from_sibling(model, source)
        label = str(path.relative_to(songs_root))
        print("  %-46s ← %-40s 音高 %d/%d　力度 %d　踏板 %d→%d%s"
              % (label[:46], str(other.relative_to(songs_root))[:40],
                 stats['pitch'], len(model.notes_tree), stats['velocity'],
                 stats['pedal_before'], stats['pedal_after'],
                 "　（%d 組鍵道排列不同）" % stats['lane_mismatch']
                 if stats['lane_mismatch'] else ""))
        if stats['pitch']:
            results.append((path, model))

    if not apply:
        print("")
        print("（分析模式，未寫檔。加 --apply 才會真的寫回）")
        return 0

    import shutil
    backup_root = songs_root.parent / ("UserSongs_backup_" + time.strftime("%Y%m%d_%H%M%S"))
    for path, _model in results:
        target = backup_root / path.relative_to(songs_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for path, model in results:
        model.save_json(str(path))
    print("")
    print("已寫回 %d 份，原檔備份在 %s" % (len(results), backup_root))
    return 0


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真的寫回譜面（預設只分析）")
    parser.add_argument("--only-missing", action="store_true",
                        help="跳過已經有力度的譜面。增量補做時用這個，"
                             "才不會把先前（可能是手動對位的）成果蓋掉")
    parser.add_argument("--only-missing-pedal", action="store_true",
                        help="只補踏板：跳過已經有踏板的譜面，也只考慮本身帶 CC64 的"
                             "來源。已經有力度的譜面**不會**被重寫力度")
    parser.add_argument("--songs", default=str(Path(__file__).resolve().parents[1]
                                               / "Nostalgia-clone" / "UserSongs"))
    parser.add_argument("--midi-dir", action="append", default=None,
                        help="額外的 MIDI 搜尋路徑，可重複指定")
    parser.add_argument("--chart", action="append", default=None,
                        help="只處理路徑含這段字的譜面，可重複指定。"
                             "放寬門檻搶救個別譜面時用，免得整批跟著放寬")
    parser.add_argument("--min-exact", type=float, default=MIN_EXACT_RATIO,
                        help="音高吻合門檻（預設 %.2f）" % MIN_EXACT_RATIO)
    parser.add_argument("--min-correlation", type=float, default=MIN_CONTOUR_CORRELATION,
                        help="無音高譜面的旋律走向相關係數門檻（預設 %.2f）"
                             % MIN_CONTOUR_CORRELATION)
    parser.add_argument("--from-sibling", action="store_true",
                        help="不配對 MIDI，改從**同曲另一份結構相同的譜面**照序位搬"
                             "音高／力度／踏板。給時間軸被拉伸過的變體用（例如 "
                             "Recollect Lines 的 _ele 版），那種情況位移搜尋救不了")
    parser.add_argument("--report", nargs="?", default=None,
                        const=str(Path(__file__).resolve().parents[1] / "unmatched-charts.md"),
                        help="把逐份譜面的力度／踏板狀態寫成 markdown"
                             "（不給路徑就寫回 unmatched-charts.md）")
    args = parser.parse_args(argv)

    songs_root = Path(args.songs)
    if args.from_sibling:
        return run_sibling_transfer(songs_root, args.apply)
    midi_roots = [Path(p) for p in (args.midi_dir or [])]
    if not midi_roots:
        midi_roots = [Path(os.path.expanduser("~")) / "Downloads", songs_root]

    midi_paths = find_midis(midi_roots)
    print("找到 %d 份 MIDI，載入中…" % len(midi_paths))
    started = time.time()
    sources = load_midis(midi_paths)
    print("可用來源 %d 份（耗時 %.1fs）\n" % (len(sources), time.time() - started))

    charts = find_charts(songs_root)
    print("譜面 %d 份\n" % len(charts))

    restored: List[Tuple[str, str, float, dict, NoteModel]] = []
    skipped: List[Tuple[str, str]] = []
    rows: List[Dict[str, object]] = []

    def record(label: str, state: Optional[Dict[str, object]], outcome: str,
               midi: str = "", ratio: float = 0.0) -> None:
        """逐份譜面的狀態，給 --report 用。

        `state` 必須是評分**之前**抓的：評分會拿同一個模型反覆試候選，跑完之後
        上面已經沾著最後一個候選寫進去的力度和踏板了，當場再讀就全是假的。
        """
        row: Dict[str, object] = {"label": label, "notes": 0, "pitch": False,
                                  "velocity": False, "pedal": 0, "auto_pedal": False}
        row.update(state or {})
        row.update({"outcome": outcome, "midi": midi, "ratio": ratio})
        rows.append(row)

    for chart_path in charts:
        label = str(chart_path.relative_to(songs_root))
        if args.chart and not any(pattern in label for pattern in args.chart):
            continue
        chart = load_chart(chart_path)
        if chart is None:
            skipped.append((label, "讀不到或沒有音符"))
            record(label, None, "讀不到")
            continue

        has_velocity = any(n.velocity is not None for n in chart.notes_tree)
        has_pitch = chart_has_pitch(chart)
        state: Dict[str, object] = {
            "notes": len(chart.notes_tree), "pitch": has_pitch,
            "velocity": has_velocity, "pedal": len(chart.pedal_spans),
            # generate_pedal.py 生的踏板要和真人的 CC64 分開算——它是猜的。
            # XML 沒有 json_meta，記號掛在 <pedal_data origin="auto"> 上。
            "auto_pedal": (str(chart.json_meta.get("pedal_origin", "")) == "auto"
                           or (chart_path.suffix.lower() == ".xml"
                               and '<pedal_data origin="auto"'
                               in chart_path.read_text(encoding="utf-8", errors="ignore")))}

        if args.only_missing and has_velocity:
            skipped.append((label, "已經有力度，跳過"))
            record(label, state, "未檢查")
            continue
        if args.only_missing_pedal and chart.pedal_spans:
            skipped.append((label, "已經有踏板，跳過"))
            record(label, state, "未檢查")
            continue

        candidates = [s for s in sources if plausible(chart, s)]
        if not candidates:
            skipped.append((label, "沒有尺寸相近的 MIDI"))
            record(label, state, "沒有相近 MIDI")
            continue

        needs_pitch = not has_pitch
        threshold = MIN_ONSET_RATIO if needs_pitch else args.min_exact
        print("  [%3d/%3d] %-40s %s 候選 %2d …"
              % (len(restored) + len(skipped) + 1, len(charts), label[:39],
                 "時間" if needs_pitch else "音高", len(candidates)), end="", flush=True)

        best_source = None
        best_ratio = 0.0
        best_correlation = 0.0
        best_offset = 0
        for source in candidates:
            if needs_pitch:
                # 沒有音高就無從用音高驗證。起音時間單獨看會被格點量化騙過去，
                # 所以拿「旋律走向是否一致」當主要依據，起音率只是輔助。
                ratio, correlation = onset_agreement(chart, source)
                rank = ratio * max(0.0, correlation)
            else:
                ratio, correlation = score_source(chart, source), 1.0
                rank = ratio
            if best_source is None or rank > best_ratio * max(0.0, best_correlation):
                best_ratio, best_correlation, best_source = ratio, correlation, source
                # 位移只有「靠時間配對」那條路會用到；有音高時是按音高比，不需要。
                best_offset = estimate_offset(
                    sorted({int(n.start) for n in chart.notes_tree}),
                    source.onsets) if needs_pitch else 0

        accepted = (best_source is not None and best_ratio >= threshold
                    and (not needs_pitch or best_correlation >= args.min_correlation))
        if not accepted:
            detail = "%.0f%%" % (100 * best_ratio)
            if needs_pitch:
                detail += " 相關 %+.2f" % best_correlation
            print(" 最佳 %s，略過" % detail)
            skipped.append((label, "最佳吻合僅 " + detail))
            record(label, state, "配不到 MIDI",
                   best_source.path.name if best_source else "", best_ratio)
            continue

        if args.only_missing_pedal and not best_source.pedal:
            # 配得再準也補不出踏板——這份 MIDI 本身就沒踩過 CC64。這不是配對失敗，
            # 是這首歌的來源真的沒有踏板資料，只能靠 AutoPedal 生。
            print(" %.0f%% ← %s（來源沒有 CC64）"
                  % (100 * best_ratio, best_source.path.name[:26]))
            skipped.append((label, "來源沒有 CC64（%s）" % best_source.path.name))
            record(label, state, "來源沒有 CC64", best_source.path.name, best_ratio)
            continue

        # 評分用的模型已經被前面的候選污染過，勝出的那一份從乾淨的檔案重跑。
        model = load_chart(chart_path)
        if model is None:
            skipped.append((label, "重新讀取失敗"))
            record(label, state, "重讀失敗", best_source.path.name, best_ratio)
            print(" 重讀失敗")
            continue

        # estimate_offset 給的是 chart→source 的方向，搬來源要取負號。
        to_chart = -best_offset
        source_notes = shifted_notes(best_source.notes, to_chart)
        source_pedal = [[a + to_chart, b + to_chart] for a, b in best_source.pedal]

        note = " 相關 %+.2f" % best_correlation if needs_pitch else ""
        if to_chart:
            note += " 位移 %+dms" % to_chart
        if needs_pitch:
            # 這種譜在合成鋼琴模式下一個音都不會響，要先把音高補回來。左右手不動
            # ——那是排譜時的人工決定，不該被來源覆蓋。
            pitch_stats = model.apply_midi_pitches_from_source_notes(
                source_notes, time_tolerance_ms=ONSET_TOLERANCE_MS, apply_hand=False)
            note += " +音高 %d" % pitch_stats.get("matched_notes", 0)

        # 補踏板時不重寫已經有的力度：那份力度是同一套流程配出來的，重跑只會
        # 得到一樣的值，但萬一這次配到的是別份 MIDI，就會把好的力度換成壞的。
        keep_velocity = args.only_missing_pedal and has_velocity
        stats = model.apply_midi_expression_from_source(
            source_notes, source_pedal, restore_velocity=not keep_velocity)
        print(" %.0f%%%s ← %s" % (100 * best_ratio, note, best_source.path.name[:26]))
        restored.append((label, best_source.path.name, best_ratio, stats, model))
        # `model` 是乾淨重讀後只套過勝出來源的那一份，所以這裡讀到的就是寫回去
        # 之後的樣子（--apply 時）。
        state["pedal"] = len(model.pedal_spans)
        state["velocity"] = any(n.velocity is not None for n in model.notes_tree)
        state["pitch"] = chart_has_pitch(model)
        record(label, state, "已還原", best_source.path.name, best_ratio)

    print("=" * 100)
    print("%-46s %-30s %6s %7s %s" % ("譜面", "來源 MIDI", "吻合", "力度", "踏板"))
    print("-" * 100)
    for label, midi_name, ratio, stats, model in restored:
        print("%-46s %-30s %5.0f%% %7d %s"
              % (label[:45], midi_name[:29], 100 * ratio,
                 stats["velocity_applied"], describe_pedal(model)))

    if args.apply:
        # 存檔會重建整份 note_data（XML 尤其如此），批次改 60 幾個檔案之前先留一份
        # 原樣，出事才有得比對、有得還原。
        import shutil
        backup_root = songs_root.parent / ("UserSongs_backup_" + time.strftime("%Y%m%d_%H%M%S"))
        print("\n備份原檔到 %s" % backup_root)
        for label, _midi, _ratio, _stats, _model in restored:
            source_path = songs_root / label
            target = backup_root / label
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source_path, target)
            except Exception as exc:
                print("  備份失敗 %s：%s" % (label, exc))

        print("寫回檔案…")
        written = 0
        for label, _midi, _ratio, _stats, model in restored:
            path = songs_root / label
            try:
                if path.suffix.lower() == ".json":
                    model.save_json(str(path))
                else:
                    model.save_xml(str(path))
                written += 1
            except Exception as exc:
                print("  寫入失敗 %s：%s" % (label, exc))
        print("已寫回 %d / %d 份" % (written, len(restored)))
    else:
        print("\n（分析模式，未寫檔。加 --apply 才會真的寫回）")

    print("\n未還原 %d 份：" % len(skipped))
    reasons: Dict[str, int] = {}
    for _label, reason in skipped:
        key = reason.split("僅")[0].strip()
        reasons[key] = reasons.get(key, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d 份" % (reason, count))

    if args.report:
        write_report(Path(args.report), rows, applied=args.apply)
        print("\n狀態已寫到 %s" % args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
