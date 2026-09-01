"""替沒有 CC64 來源的譜面生成踏板。

來源 MIDI 沒踩過踏板的譜面有 30 幾份（掃過整個曲庫確認：不是解析漏掉，也沒有
改用 CC66/67/69，更沒有把延音寫成長音符——那些檔案就是量化過的鋼琴捲簾匯出）。
遊戲內的 `AutoPedal` 可以即時生一份，但它是固定每拍／每小節踩放，不看音樂。
這支工具改用**和聲**決定何時換踏板，寫成正常的 `pedal_data`。

生成的踏板會標記 `pedal_origin: "auto"`，因為它是猜的：hardcore 模式（音訊跟著
玩家的腳走）不該拿猜出來的譜當標準答案。

用法：
    python generate_pedal.py --evaluate        # 拿有真踏板的譜面驗證產生器
    python generate_pedal.py                   # 看看會替哪些譜面生成什麼
    python generate_pedal.py --apply           # 真的寫回
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from qt_editor.models import NoteModel

# 踩放之間的空隙。實測真人的 CC64 只有 1–2ms（syuten、Chronomia 都是），但那種
# 空隙在畫面上落不到一個 frame；20ms 是遊戲內 AutoPedal 用的值，聽感上一樣是
# 「換踏板」，視覺上又看得見。
LIFT_MS = 20

# 兩次換踏板至少要隔這麼久。實測 83 份真踏板譜面的 6203 個踩點間隔，只有 0.9%
# 短於 300ms（中位數 1132ms），所以這個下限只擋掉真人也不會做的事。快歌尤其
# 需要它：Tempestissimo 是 231 BPM，一拍才 260ms，不擋就變成每拍都換。
MIN_CHANGE_MS = 300

# 一段踏板最長就這麼久。這個上限不是音樂考量而是聲音預算：實測 per-bar 的
# 自動踏板會讓同時發聲數衝到 240，超過 160 的 voice pool，鋼琴就開始被搶斷。
MAX_SPAN_MS = 2000

# 撐過這麼久就不管有沒有長音，硬切。長音否決權讓踏板可以壓得很久（實測最長到
# 18.6s），而那會把同時發聲數推到 132；4 秒這條線把最壞的情況壓回 100，代價是
# 平均只有 1.0% 的踩點落在長音中間（真人是 5.9%），等於沒有代價。
HARD_MAX_SPAN_MS = 4000

# 超過這麼久沒有音符就放開踏板，不要讓殘響拖過休止。
SILENCE_MS = 900

# 低於這個音高才算「低音」。低音換了幾乎一定要換踏板，中高音區的來回跑動則不用。
BASS_CEILING = 60  # C4

# 有這麼長的音正橫跨候選點時，不在那裡換踏板。
#
# 實測 8112 個真人踩點只有 5.9% 落在長音中間，而隨便一條拍線是 14.6%——真人是
# 刻意避開的，差 2.5 倍。道理也對：手還按著的那顆長音**就是**當下的和聲，它還
# 沒放開就代表和聲還沒翻頁；這時候放踏板，那顆音自己不會斷（鍵還按著），斷的
# 是它底下所有已經放開的音，等於把伴奏抽掉只留旋律。
HOLD_MS = 300
HOLD_PAD_MS = 25   # 剛好在踩點上起音或結束的不算「橫跨」

SKIP_NAMES = {"register.json", "songlist.json", "piano_samples.json", "library.json"}


def _clashes(new_pcs: Sequence[int], held_pcs: Sequence[int]) -> bool:
    """新進來的音和踏板還壓著的音是否撞在一起。

    只看小二度（含大七度，同一件事差一個八度）。這正是踏板糊掉時聽得出來的
    那個音——三度六度疊再多都還是和聲，半音疊上去就是髒的。
    """
    for a in new_pcs:
        for b in held_pcs:
            if (a - b) % 12 in (1, 11):
                return True
    return False


def generate_pedal_spans(notes, beats: Optional[Sequence[int]] = None, *,
                         lift_ms: int = LIFT_MS,
                         min_change_ms: int = MIN_CHANGE_MS,
                         max_span_ms: int = MAX_SPAN_MS,
                         hold_ms: int = HOLD_MS) -> List[List[float]]:
    """用和聲變化決定踩放點，但只准在拍線上換。

    兩件事各自負責一半：**什麼時候可以換**由拍子決定，**這一拍要不要換**由和聲
    決定。只看和聲會換得太碎——實測那樣生出來的段數是真人的 3～5 倍，因為經過音
    每一顆都在撞半音，而真人是踩著讓它糊過去的。真人的踩點有 86% 落在拍線
    ±30ms 內，換踏板的間隔中位數 1182ms，也就是一拍上下。

    這一拍要不要換，判準是三條「踩不下去了」：新進來的音和踏板還壓著的音撞到
    半音、低音的音級換了（和聲真的翻頁了）、或者壓超過 max_span_ms。

    再蓋一條否決權：**有長音橫跨的點不換**（見 HOLD_MS）。這條是最有效的一條，
    精確度從 53% 拉到 64%，段數比從 1.41 降到 1.17——它砍掉的整批踩點，剛好是
    真人幾乎不會做的那種。
    """
    groups: Dict[int, List[int]] = {}
    for note in notes:
        pitch = getattr(note, "pitch", None)
        if pitch is None:
            continue
        groups.setdefault(int(note.start), []).append(int(pitch))
    onsets = sorted(groups)
    if not onsets:
        return []

    note_end: Dict[int, int] = {}
    for note in notes:
        note_end[int(note.start)] = max(note_end.get(int(note.start), 0), int(note.end))

    # 沒有拍線資料就退回「每個起音都是候選」——結果會碎，但總比沒有好。
    marks = sorted({int(b) for b in beats}) if beats else onsets
    marks = [m for m in marks if m > onsets[0]]

    import bisect



    # 長音的 (起, 迄)，照起音排序，用來判斷候選點有沒有被長音橫跨。
    holds = sorted((int(n.start), int(n.end)) for n in notes
                   if int(n.end) - int(n.start) >= hold_ms)
    hold_starts = [s for s, _e in holds]

    def inside_hold(when: int) -> bool:
        # 只要往前找到第一顆「還沒結束」的長音就夠了；長音不多，線性掃很快。
        i = bisect.bisect_left(hold_starts, when)
        for start, end in reversed(holds[:i]):
            if end > when + HOLD_PAD_MS and start < when - HOLD_PAD_MS:
                return True
            if when - start > 12000:      # 再往前的都結束很久了
                break
        return False

    def legal(when: int) -> bool:
        return not inside_hold(when)

    def content(lo: int, hi: int) -> Tuple[set, Optional[int]]:
        """[lo, hi) 之間響起來的音級，以及最低音。"""
        i = bisect.bisect_left(onsets, lo)
        j = bisect.bisect_left(onsets, hi)
        pcs, bass = set(), None
        for when in onsets[i:j]:
            for pitch in groups[when]:
                pcs.add(pitch % 12)
                bass = pitch if bass is None else min(bass, pitch)
        return pcs, bass


    spans: List[List[float]] = []
    pending = False          # 想換但太靠近上一次，等下一個合法的拍點
    press = onsets[0]
    held_pcs, held_bass = content(press, marks[0] if marks else press + 1)
    if held_bass is None:
        held_pcs = {p % 12 for p in groups[press]}
        held_bass = min(groups[press])

    for index, mark in enumerate(marks):
        nxt = marks[index + 1] if index + 1 < len(marks) else mark + (mark - press or 1)
        new_pcs, bass = content(mark, nxt)
        if not new_pcs:
            # 這一拍沒有音。空太久就收掉踏板，不要讓殘響拖過休止。
            last = onsets[bisect.bisect_left(onsets, mark) - 1]
            if mark - last >= SILENCE_MS and press < last:
                spans.append([float(press), float(max(note_end.get(last, last), press + 1))])
                nxt_onset = bisect.bisect_left(onsets, mark)
                if nxt_onset >= len(onsets):
                    return _cap(spans, lift_ms, max_span_ms, marks, legal)
                press = onsets[nxt_onset]
                held_pcs, held_bass = content(press, press + 1)
                pending = False
            continue

        bass_turned = (bass is not None and held_bass is not None
                       and bass % 12 != held_bass % 12
                       and min(bass, held_bass) < BASS_CEILING)
        overdue = mark - press >= max_span_ms
        want = _clashes(new_pcs, held_pcs) or bass_turned or overdue

        # 這個點准不准換：離上一次夠遠，而且沒有長音橫跨。想換但不准的**往後推，
        # 不是刪掉**——刪掉等於說和聲根本沒翻頁，而推遲正是鋼琴家本來就會的踩法
        # （切分踏板）。壓太久那條例外照樣過，因為它是聲音預算不是音樂。
        allowed = mark - press >= min_change_ms and not inside_hold(mark)
        if want and not allowed:
            pending = True

        if (want or pending) and allowed:
            spans.append([float(press), float(mark - lift_ms)])
            press, held_pcs, held_bass = mark, new_pcs, bass
            pending = False
        else:
            held_pcs |= new_pcs
            if bass is not None and (held_bass is None or bass < held_bass):
                held_bass = bass

    tail = max(max(note_end.values()), press + 1)
    spans.append([float(press), float(tail)])
    return _cap(spans, lift_ms, max_span_ms, marks, legal)


def fill_gaps(real: Sequence[Sequence[float]], made: Sequence[Sequence[float]],
              lift_ms: int = LIFT_MS) -> List[List[float]]:
    """保留真人的踩法，只把它沒蓋到的空隙補上生成的。

    有些來源 MIDI 的 CC64 是真的，但只踩了一小部分：Testify 的 28 段只覆蓋 21%，
    Grievous_Lady 只有 6 段。那些譜面有 80% 以上的音符在放開琴鍵時沒有踏板接住，
    聽起來就是每個音都被切掉——而音符本身的長度是照 MIDI 的按鍵長度來的（實測
    98~100%），本來就短，延音本來就該由踏板負責。

    真人踩過的地方一格都不動：那是人的決定，比猜的可信。只補空隙。
    """
    if not real:
        return [list(span) for span in made]
    keep = sorted([float(a), float(b)] for a, b in real)
    out = [list(span) for span in keep]
    for lo, hi in made:
        # 把生成的這一段減去所有和真人重疊的部分，剩下的片段才補進去。
        pieces = [[float(lo), float(hi)]]
        for ra, rb in keep:
            nxt = []
            for pa, pb in pieces:
                if rb <= pa or ra >= pb:
                    nxt.append([pa, pb])
                    continue
                if pa < ra - lift_ms:
                    nxt.append([pa, ra - lift_ms])
                if pb > rb + lift_ms:
                    nxt.append([rb + lift_ms, pb])
            pieces = nxt
            if not pieces:
                break
        for pa, pb in pieces:
            if pb - pa >= MIN_CHANGE_MS:
                out.append([pa, pb])
    out.sort()
    return out


def _cap(spans: List[List[float]], lift_ms: int, max_span_ms: int,
         marks: Sequence[int] = (), legal=None,
         hard_max_ms: int = HARD_MAX_SPAN_MS) -> List[List[float]]:
    """把過長的段落切開——但只切在合法的點上。

    這條規則不是音樂考量而是聲音預算：實測 per-bar 的自動踏板會讓同時發聲數衝到
    240，超過 160 的 voice pool，鋼琴就開始被搶斷。

    切點優先挑合法的（沒有長音橫跨）。早期版本是等分切，實測那些切點有 81.8%
    落在長音中間——最後一個長和弦被剁成四段正是這麼來的。

    但等不到合法切點也不能無限期拖：長音否決權會讓踏板壓到 18 秒，同時發聲數
    衝到 132。撐過 `hard_max_ms` 就照切，那時候是引擎要壞了，不是音樂問題。
    """
    result: List[List[float]] = []
    for lo, hi in spans:
        if hi - lo <= max_span_ms:
            result.append([lo, hi])
            continue
        cut = lo
        for mark in marks:
            if mark >= hi or mark - cut < max_span_ms:
                continue
            if legal is not None and not legal(mark) and mark - cut < hard_max_ms:
                continue
            result.append([cut, mark - lift_ms])
            cut = mark
        result.append([cut, hi])
    return result


# ----------------------------------------------------------------------
# 驗證：拿有真 CC64 的譜面當標準答案
# ----------------------------------------------------------------------

def compare(real: Sequence[Sequence[float]], made: Sequence[Sequence[float]],
            tolerance_ms: int = 150) -> Tuple[float, float]:
    """生成的踩點和真人的踩點對得上多少（recall, precision）。"""
    import bisect
    real_press = sorted(int(s[0]) for s in real)
    made_press = sorted(int(s[0]) for s in made)
    if not real_press or not made_press:
        return (0.0, 0.0)

    def hit(needles, haystack):
        found = 0
        for value in needles:
            i = bisect.bisect_left(haystack, value)
            near = haystack[max(0, i - 1):i + 2]
            if near and min(abs(x - value) for x in near) <= tolerance_ms:
                found += 1
        return found / len(needles)

    return (hit(real_press, made_press), hit(made_press, real_press))


def peak_voices(notes, spans: Sequence[Sequence[float]],
                max_voice_ms: int = 8000) -> int:
    """同時發聲數的尖峰。

    模型和 PianoVoiceManager 一樣：音一發就開始響，放開琴鍵時如果踏板還壓著就
    繼續響到放開踏板，最長 maxVoiceSeconds。voice pool 是 160，超過就開始搶。
    """
    releases = sorted(s[1] for s in spans)
    import bisect
    events: List[Tuple[float, int]] = []
    for note in notes:
        start, end = float(note.start), float(note.end)
        covering = None
        for lo, hi in spans:
            if lo <= end <= hi:
                covering = hi
                break
        if covering is None:
            i = bisect.bisect_left(releases, end)
            covering = end
        stop = min(max(end, covering), start + max_voice_ms)
        events.append((start, 1))
        events.append((stop, -1))
    events.sort()
    live = peak = 0
    for _when, delta in events:
        live += delta
        peak = max(peak, live)
    return peak


def beat_ms(model: NoteModel) -> List[int]:
    """譜面的拍線時間。"""
    return [int(ms) for _idx, ms in model.get_beat_entries()]


def change_marks(model: NoteModel) -> List[int]:
    """踩點可以落在哪些時間點——**小節線**。

    `beat_timings` 在這個曲庫裡有兩種意思：59 份是一小節一條，31 份是一個四分
    音符一條。產生器原本直接把它當成候選點，所以在後者上面可以換得四倍快，實測
    這批的精確度只有 40%、段數是真人的 1.84 倍（小節線那批是 78% / 0.93 倍）。
    Hemisphere 就是這樣變成「每 311ms 換一次」的：那不是和聲判斷的結果，是格線
    比較細而已。

    對照同樣音符密度的真人譜面，這幾份每秒換踏是真人的 2.4～3.1 倍，一段踏板只
    蓋 6.6 顆音（真人 16.7 顆）——使用者說「快速運指段不會換踏這麼快」是對的。

    所以四分音符格線要抽成小節線。抽完之後這批的精確度 40%→72%、段數比
    1.84→0.75，和本來就是小節線的那批同一個檔次。相位（哪一條四分是小節線）由
    起音重量決定：小節第一拍的音本來就比較多。

    半小節試過，比較差（精確 45%、段數比 1.38）——真人的換踏就是小節級的。
    """
    marks = beat_ms(model)
    bpm = model.json_meta.get("bpm") or model.json_meta.get("first_bpm")
    if len(marks) < 8 or not bpm:
        return marks
    quarter = 60000.0 / float(bpm)
    steps = sorted(marks[i + 1] - marks[i] for i in range(len(marks) - 1))
    step = steps[len(steps) // 2]
    if step >= quarter * 1.6:
        return marks                      # 本來就是小節線（或更粗），不動

    num = model.json_meta.get("time_signature_numerator") or 4
    n = int(num) if 2 <= int(num) <= 7 else 4

    weight: Dict[int, int] = {}
    for note in model.notes_tree:
        if getattr(note, "pitch", None) is None:
            continue
        weight[int(note.start)] = weight.get(int(note.start), 0) + 1
    if not weight:
        return marks[::n]
    keys = sorted(weight)

    import bisect
    best_phase, best_score = 0, -1
    for phase in range(n):
        score = 0
        for i in range(phase, len(marks), n):
            lo = bisect.bisect_left(keys, marks[i] - 40)
            hi = bisect.bisect_right(keys, marks[i] + 40)
            score += sum(weight[keys[j]] for j in range(lo, hi))
        if score > best_score:
            best_phase, best_score = phase, score
    return marks[best_phase::n]


def is_auto(model: NoteModel, path: Path) -> bool:
    """這份踏板是本工具生成的嗎。驗證時要排除掉，否則等於拿自己的答案改自己的考卷。"""
    if str(model.json_meta.get("pedal_origin", "")) == "auto":
        return True
    if path.suffix.lower() == ".xml":
        try:
            return '<pedal_data origin="auto"' in path.read_text(encoding="utf-8")
        except Exception:
            return False
    return False


def load(path: Path) -> Optional[NoteModel]:
    model = NoteModel()
    try:
        if path.suffix.lower() == ".json":
            model.load_json(str(path))
        else:
            model.load_xml(str(path))
    except Exception:
        return None
    return model if model.notes_tree else None


def charts(root: Path) -> List[Path]:
    found = [p for p in sorted(root.rglob("*.json")) if p.name not in SKIP_NAMES]
    found += sorted(root.rglob("*.xml"))
    return found


def registered(root: Path) -> set:
    """遊戲真的會載入的譜面。

    每首歌的 `register.json` 用 `chartFileName` 指名檔案（不含副檔名）。`raw/`、
    `source/` 底下那些是匯入時留的原始檔，不在名單上——生成的踏板寫進去只會讓
    「原始檔」不再是原始檔。
    """
    import json
    paths = set()
    for meta in sorted(root.rglob("register.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in data.get("difficulties") or []:
            name = str(entry.get("chartFileName") or "")
            if not name:
                continue
            # "songs/Chronomia/Master/chronomia--lime" → UserSongs 底下的相對路徑
            rel = name.split("/", 1)[1] if name.startswith("songs/") else name
            for suffix in (".json", ".xml"):
                candidate = root / (rel + suffix)
                if candidate.exists():
                    paths.add(candidate.resolve())
    return paths


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真的寫回譜面")
    parser.add_argument("--regenerate", action="store_true",
                        help="把已經標成 auto 的踏板重算一次（覆蓋率檢查不算數）")
    parser.add_argument("--evaluate", action="store_true",
                        help="不生成，改成拿有真踏板的譜面驗證產生器準不準")
    parser.add_argument("--songs", default=str(Path(__file__).resolve().parents[1]
                                               / "Nostalgia-clone" / "UserSongs"))
    parser.add_argument("--chart", action="append", default=None,
                        help="只處理路徑含這段字的譜面")
    parser.add_argument("--fill-gaps", type=float, default=None, metavar="COVERAGE",
                        help="踏板覆蓋率低於這個比例的譜面，**保留真人的踩法**、只補"
                             "它沒蓋到的空隙。0.6 會涵蓋 Testify(21%%)、Grievous_Lady 那種"
                             "「有真 CC64 但只踩了一小段」的譜面")
    parser.add_argument("--min-coverage", type=float, default=0.0,
                        help="踏板覆蓋率低於這個比例的譜面也視為「沒有踏板」而重新生成。"
                             "0.15 會涵蓋 Testify_mv 那種只有 6 段、覆蓋 4%% 的殘缺踏板")
    parser.add_argument("--include-unregistered", action="store_true",
                        help="連 register.json 沒列到的檔案（raw/、source/ 的原始檔）"
                             "一起處理")
    args = parser.parse_args(argv)

    root = Path(args.songs)
    playable = registered(root)
    targets: List[Tuple[Path, NoteModel, List[List[float]]]] = []
    # 「原本就有真人踏板嗎」必須在**收集階段**記下來：apply 會把 pedal_spans 換掉，
    # 事後再問永遠是 True，標記就會全部變成 mixed。
    had_real: Dict[Path, bool] = {}

    if args.evaluate:
        print("%-46s %5s %5s %6s %6s %6s %5s" % (
            "有真踏板的譜面", "真", "生成", "召回", "精確", "覆蓋", "尖峰"))
        print("-" * 92)
        scores = []
        for path in charts(root):
            if args.chart and not any(c in str(path) for c in args.chart):
                continue
            model = load(path)
            if model is None or not model.pedal_spans or is_auto(model, path):
                continue
            made = generate_pedal_spans(model.notes_tree, change_marks(model))
            if not made:
                continue
            recall, precision = compare(model.pedal_spans, made)
            end = max(n.end for n in model.notes_tree)
            cover = sum(b - a for a, b in made) / max(1, end)
            scores.append((recall, precision))
            print("%-46s %5d %5d %5.0f%% %5.0f%% %5.0f%% %5d" % (
                str(path.relative_to(root))[:46], len(model.pedal_spans), len(made),
                100 * recall, 100 * precision, 100 * cover,
                peak_voices(model.notes_tree, made)))
        if scores:
            print("-" * 92)
            print("平均：召回 %.0f%%　精確 %.0f%%（%d 份）" % (
                100 * sum(s[0] for s in scores) / len(scores),
                100 * sum(s[1] for s in scores) / len(scores), len(scores)))
        return 0

    for path in charts(root):
        if args.chart and not any(c in str(path) for c in args.chart):
            continue
        if not args.include_unregistered and path.resolve() not in playable:
            continue
        model = load(path)
        if model is None:
            continue
        if not any(n.pitch is not None for n in model.notes_tree):
            continue  # 沒有音高就無從判斷和聲
        if model.pedal_spans and args.regenerate and is_auto(model, path):
            # 自己生的可以重生：格線改成小節線之後，舊的那份是照四分音符換的。
            model.pedal_spans = []
        if model.pedal_spans:
            # 有踏板就不動——除非它形同虛設。Testify_mv 的來源 MIDI 只有 12 個
            # CC64 事件、6 段全擠在 8 秒內，覆蓋 4%：那不是「有踏板」。
            end = int(model.music_end_ms) or max(n.end for n in model.notes_tree)
            covered = sum(b - a for a, b in model.pedal_spans) / max(1, end)
            if args.fill_gaps is not None and covered < args.fill_gaps:
                pass            # 走補空隙那條路，真人的踩法保留
            elif covered >= args.min_coverage:
                continue
            else:
                print("  覆蓋僅 %.0f%%，視為沒有踏板：%s"
                      % (100 * covered, path.relative_to(root)))
        made = generate_pedal_spans(model.notes_tree, change_marks(model))
        had_real[path] = bool(model.pedal_spans)
        if made and model.pedal_spans and args.fill_gaps is not None:
            before = len(model.pedal_spans)
            made = fill_gaps(model.pedal_spans, made)
            print("  補空隙 %-46s 真人 %3d 段 → 共 %3d 段"
                  % (str(path.relative_to(root))[:46], before, len(made)))
        if made:
            targets.append((path, model, made))

    print("%-52s %6s %6s %6s %6s" % ("譜面", "段數", "覆蓋", "中位長", "尖峰"))
    print("-" * 84)
    for path, model, made in targets:
        # 用歌曲長度當分母，不用最後一顆音符——彩云追月有一顆離群音符落在 620 秒，
        # 拿它當曲長會讓覆蓋率看起來只有 25%。
        end = int(model.music_end_ms) or max(n.end for n in model.notes_tree)
        lengths = sorted(b - a for a, b in made)
        print("%-52s %6d %5.0f%% %5.0fms %6d" % (
            str(path.relative_to(root))[:52], len(made),
            100 * sum(lengths) / max(1, end), lengths[len(lengths) // 2],
            peak_voices(model.notes_tree, made)))
    print("-" * 84)
    print("共 %d 份" % len(targets))

    if args.apply:
        backup = root.parent / ("UserSongs_backup_" + time.strftime("%Y%m%d_%H%M%S"))
        for path, model, made in targets:
            target = backup / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            model.pedal_spans = [[float(a), float(b)] for a, b in made]
            # 生成的踏板要標記出來：它是猜的，hardcore 模式不該拿它當標準答案。
            model.json_meta["pedal_origin"] = "mixed" if had_real.get(path) else "auto"
            if path.suffix.lower() == ".json":
                model.save_json(str(path))
            else:
                model.save_xml(str(path))
                # XML 這邊 json_meta 不會被寫出去，記號改掛在 <pedal_data> 上。
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace("<pedal_data>", '<pedal_data origin="auto">', 1),
                                encoding="utf-8")
        print("已寫回 %d 份，原檔備份在 %s" % (len(targets), backup))
    else:
        print("（分析模式，未寫檔。加 --apply 才會真的寫回）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
