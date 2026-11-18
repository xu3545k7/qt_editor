# -*- coding: utf-8 -*-
"""只採一小段：把 AI 轉譜的結果裡**選定的那幾小節**搬進現有譜面。

使用者：「加入一個 只採某個小部分的音檔生成的 MIDI 部分」。

原本的 AI 轉譜是全有全無：`transcribe_audio_ai` 轉完**整首**、設 BPM、吸附
小節線，然後開成一份新譜面。手寫了一半的譜面要借用其中幾小節（那串跑動聽不
出來、那疊和絃抓不到音），沒有路可以走——只能整首轉成另一份譜再手動複製。

這支提供的是那條路：**對位、挑一段、其餘一律不碰**。和「以音檔補上踏板」
（`audio_pedal`）是同一個家族，連對位都用它那一套；差別是那支只搬 CC64，
這支只搬**某一段的音符**。

## 處境和補踏板不一樣：要採的地方是空的

補踏板時譜面上的音符是完整的，對位有一大把證據。這裡恰恰相反——會想採的那
幾小節通常**還沒寫**（正是因為寫不出來才要 AI），對位只能靠範圍外的音符。

所以第一件事是量「拿掉一段還對得準嗎」。實測（五首他的手排譜，每 8 小節一
段，把那一段藏起來、只用範圍外的音符對位，再和他原本寫的比）：**位移和用
整首算出來的完全一樣**（68 段裡 62 段差 0 ms，其餘 ±4 ms）。

**不要改成只用附近的音符對位。** 直覺上一小段只需要附近幾秒的位移，而且不受
整首漂移影響——實測推翻：±4 / ±8 / ±15 / ±30 秒四種鄰域，召回全部比用整首更差
（整首 24%、±15 秒 12%、±8 秒 8%、±4 秒 5%），位移動輒跳到 ±7 秒。音符一少，
粗對位的直方圖就堆不出真正的峰。對位要用**整首**的證據，範圍只用來挑音符。

## 這份轉譜值不值得用：`reproduced`

對位信心（`Alignment.confidence`）只回答「是不是同一首歌」，不回答「聽得準不
準」。這裡多量一個數字：**這份轉譜在他已經寫好的地方重現了幾成**（同音高、
±80 ms）。那是唯一有標準答案的地方。

實測它幾乎就是空白段落會採到的比例（左邊是重現率、右邊是逐段召回）：

    Abiogenesis 100% → 97%      Rrhar'il     90% → 89%
    Chronomia    99% → 98%      Myosotis     98% → 97%
    Marigold     98% → 97%      anima        34% → 33%

anima 那一首是拿到別人翻奏版的音檔（長度都不一樣），重現率立刻掉到三成——
這個數字就是用來擋那種情形的，所以介面一定要顯示它。曲庫裡唯一現成的
`.ai.mid` 也只有一成多，因為它是從**混音**轉的（混音轉譜 F1 本來就只有 29%，
見 `piano_separate`）。低就該換音檔或先分離鋼琴，不是硬採一段進來。

## 精確只有一半，而那不是錯音

同一批段落，採進來的音符有多少是他自己也會寫的：**Expert 64%、Hard 38%、
Normal 31%**。數字照難度掉，因為採進來的是**完整的鋼琴演奏**，而他的譜面是
它的減量版（見 `difficulty-ladder`）。多出來的是他刻意省掉的內聲部，不是
模型聽錯。所以這裡不做任何自動減量，只把這三個數字講給使用者聽。

試過兩種減量，都不划算：

* **力度門檻**（≥20/30/40/50/60）：精確從頭到尾都是 57%，一點都沒動，召回
  反而開始掉。transkun 的力度分不出「他留下的」和「他省掉的」。
* **只留每一刻的冠音與低音**：Expert 召回 94%→68% 換精確 64%→70%、
  Hard 96%→78% 換 38%→46%、Normal 97%→78% 換 31%→35%。每個難度都是賠的
  （召回掉的是精確換到的 3~4 倍）。

## 「這一段在對位錨點外」不是警告

本來打算在範圍落在漂移錨點之外時警告使用者（位移是沿用最近那個錨點外推的）。
實測不預測任何東西：錨點外的段落 Marigold 召回 99%、Myosotis 97%、
Rrhar'il 81%，和錨點內（96%／97%／91%）同一個水準。唯一真的採不到東西的是
「音檔那一段根本沒有音符」，而那在 `Pick` 的數量上一看就知道。所以沒有這個旗標。

## 這支不碰 Qt，也不碰 NoteModel

吃的是數字、吐的是數字。擺鍵道（`arrange_by_example` 的學習映射＋`melody_line`
的冠音帶頭）、分手（`hand_split`）、裁踏板殘響
（`NoteModel.trim_pedal_sustained_holds`）都是既有的東西，由呼叫端串起來。
那一串的實測見 `main_window._insert_audio_section`。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from . import audio_pedal as AP
from .models import TOTAL_GAME_KEYS

#: 判斷「同一顆音符」的容差。沿用 `audio_pedal.MATCH_MS`：吸附到格線上的譜面
#: 本身就會偏半個十六分音符（160 BPM 下 ±47 ms），收得太緊會把本來就寫對的
#: 那幾顆當成新音符再放一次。
MATCH_MS = AP.MATCH_MS

#: 比這更短的音就不要——模型在密的段落會吐出幾毫秒的碎音。
#:
#: 掃出來的（10 份譜面、23546 顆）：
#:
#:     下限      召回   精確   採到
#:      0 ms     95%    45%   23546
#:     20 ms     95%    46%   23234      ← 取這個
#:     30 ms     93%    46%   22517
#:     40 ms     89%    47%   21203
#:
#: 0 到 20 ms 是免費的（召回一點都沒掉，少了 312 顆垃圾），30 開始就在丟真的
#: 音符了。原本憑感覺設 40，實測是賠 6 個百分點的召回換 2 點精確。
MIN_MS = 20

#: 長條的門檻，和 `NoteModel.load_midi` 同一條規則——採進來的一段應該和整首
#: 匯入的行為一樣，不要自己另立一套。
LONG_MS = 500


@dataclass
class Note:
    """一顆音符的四個數字（毫秒／MIDI 音高／力度）。

    `read_notes` 讀出來的是**音檔**時間軸，`mapped` 與 `pick` 回傳的是換算過的
    **譜面**時間軸。
    """
    start: float
    end: float
    pitch: int
    velocity: int

    @property
    def duration(self) -> float:
        return float(self.end) - float(self.start)

    @property
    def long(self) -> bool:
        return self.duration >= LONG_MS


@dataclass
class Pick:
    """挑出來的一段，以及**沒有**被挑進來的各有幾顆。

    每一種都要數出來報給使用者看：採進來 12 顆而丟掉 300 顆，和採進來 12 顆、
    音檔那一段本來就只有 12 顆，是完全不同的兩件事。
    """
    notes: List[Note] = field(default_factory=list)
    #: 起音不在範圍內（絕大多數——整首只採一段）
    outside: int = 0
    #: 太短，當成模型的雜訊（見 `MIN_MS`）
    short: int = 0
    #: 範圍內已經有同一刻、同音高的音符
    duplicate: int = 0
    #: 尾巴伸出範圍外，被切在邊界上
    clipped: int = 0

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def inside(self) -> int:
        """範圍內音檔總共有幾顆（含被跳過的）。"""
        return len(self.notes) + self.short + self.duplicate


# ── 讀檔 ──────────────────────────────────────────────────────────────

def read_notes(path: str) -> List[Note]:
    """AI 轉出的 MIDI → 音檔時間軸上的音符。

    走 `ai_grid.read_ai_midi` 而不是 `NoteModel.load_midi`：這裡要的是原始的
    時間與音高，排譜、分手、鍵道那一整套跑了也用不到。
    """
    from .ai_grid import read_ai_midi

    notes, _pedals = read_ai_midi(path)
    return [Note(float(s) * 1000.0, float(e) * 1000.0, int(p), int(v))
            for s, e, p, v in notes]


def onsets(notes: Sequence[Note]) -> List[float]:
    return [float(n.start) for n in notes]


# ── 對位 ──────────────────────────────────────────────────────────────

def align(chart_onsets: Sequence[float], source: Sequence[Note]) -> AP.Alignment:
    """量出「音檔時間 → 譜面時間」。**一定要餵整首的起音**，見模組說明。"""
    return AP.align(list(chart_onsets), onsets(source))


def mapped(source: Sequence[Note], alignment: AP.Alignment) -> List[Note]:
    """整份換算到譜面時間軸，照起音排好。"""
    spans = AP.remap([[n.start, n.end] for n in source], alignment)
    out = [Note(float(a), float(b), int(n.pitch), int(n.velocity))
           for (a, b), n in zip(spans, source)]
    out.sort(key=lambda n: (n.start, n.pitch))
    return out


def _by_pitch(pairs: Iterable[Tuple[float, int]]) -> Dict[int, List[float]]:
    out: Dict[int, List[float]] = {}
    for when, pitch in pairs:
        out.setdefault(int(pitch), []).append(float(when))
    for times in out.values():
        times.sort()
    return out


def reproduced(chart: Sequence[Tuple[float, int]], source: Sequence[Note],
               tolerance_ms: float = MATCH_MS) -> Tuple[int, int]:
    """這份轉譜重現了譜面上幾顆音符（同音高、容差內）→ `(對上, 總共)`。

    `chart` 是譜面的 `(起音毫秒, MIDI 音高)`，`source` 是**已經換算到譜面時間
    軸**的轉譜音符（`mapped` 的輸出）。這是「這份轉譜聽得準不準」唯一有標準
    答案的量法，見模組說明的那張對照表。

    同一顆轉譜音符只能認領一顆譜面音符（兩邊排好序、同音高之內雙指標前進），
    不然一串顫音會把附近所有同音高的都算成對上。
    """
    want = _by_pitch(chart)
    have = _by_pitch((n.start, n.pitch) for n in source)
    tol = float(tolerance_ms)
    hit = 0
    total = 0
    for pitch, times in want.items():
        total += len(times)
        others = have.get(pitch)
        if not others:
            continue
        index = 0
        for when in times:
            while index < len(others) and others[index] < when - tol:
                index += 1
            if index < len(others) and others[index] <= when + tol:
                hit += 1
                index += 1
    return hit, total


# ── 挑一段 ────────────────────────────────────────────────────────────

def pick(source: Sequence[Note], alignment: AP.Alignment,
         lo_ms: float, hi_ms: float, *,
         min_ms: float = MIN_MS,
         avoid: Sequence[Tuple[float, int]] = (),
         avoid_ms: float = MATCH_MS,
         clip: bool = True) -> Pick:
    """從轉譜結果裡挑出 `[lo_ms, hi_ms)` 這一段，換算成譜面時間。

    `avoid` 是範圍內**已經有**的音符 `(起音, 音高)`：和它們同一刻同音高的就不
    放第二次（「留著現有的、只補沒有的」那個模式）。空的就全放。

    `clip`：尾巴伸出範圍外的切在邊界上。採進來的是一段，不該有東西壓到範圍外
    他自己寫的音符上面——而 AI 的音長是**踏板長度**（見 `hold_length_fix`），
    一顆就可能蓋過去好幾小節。
    """
    lo = float(lo_ms)
    hi = float(hi_ms)
    result = Pick()
    held = _by_pitch(avoid)
    where = {pitch: 0 for pitch in held}
    tol = float(avoid_ms)

    for raw in mapped(source, alignment):
        # **先收成整數毫秒再判斷範圍。** 譜面的時間是整數，所以起音 37894.6 的
        # 那一顆進了譜面就是 37895——如果邊界剛好是 37895，它就站在範圍外緣，
        # 而那裡有他自己寫的音符。實測它會把對方的鍵道推走（5 份譜有 2 份被推
        # 走 2~3 顆），正是「只採一小段」最不該發生的事。
        note = Note(float(round(raw.start)), float(round(raw.end)),
                    raw.pitch, raw.velocity)
        if not (lo <= note.start < hi):
            result.outside += 1
            continue
        # 太短的先丟掉，而且**不要**讓它去認領 `avoid` 裡的音符——那一顆要留給
        # 後面真的會被放進來的候選。
        if note.duration < float(min_ms):
            result.short += 1
            continue
        times = held.get(note.pitch)
        if times:
            index = where[note.pitch]
            while index < len(times) and times[index] < note.start - tol:
                index += 1
            where[note.pitch] = index
            if index < len(times) and times[index] <= note.start + tol:
                where[note.pitch] = index + 1
                result.duplicate += 1
                continue
        end = note.end
        if clip and end > hi:
            end = hi
            result.clipped += 1
        # 起音已經夾在 hi 之前（而且是整數），所以 start + 1 不會超出邊界
        result.notes.append(Note(note.start, max(note.start + 1.0, end),
                                 note.pitch, note.velocity))
    return result


# ── 擺進譜面之前 ──────────────────────────────────────────────────────

def settle(made: Sequence[Any], walls: Iterable[Any] = (),
           total_lanes: int = TOTAL_GAME_KEYS, onset_ms: float = 35.0) -> int:
    """把採進來的音符從**別人身上**挪開。只動 `made`，回傳動了幾顆。

    為什麼要自己寫一個：`arrange_by_example.spread_group` 是「整組照音高推開，
    疊到就往右讓」，而它不知道誰不准動——實測五份譜裡有兩份被它把使用者原本
    寫好的音符推走（第 37895 ms 那一刻三顆各往右 2~5 格）。那正是「只採一小段」
    最不該發生的事。

    這裡的規則不一樣：`walls` 是**牆**，一格都不准動；採進來的照音高順序一顆
    一顆找離原位最近的空位。真的塞不下就原地重疊——寧可重疊也不要出界（鍵道
    28 以上是壞資料，存檔驗證會擋；重疊只是不好打，而且採進來的都選起來了，
    看一眼就知道要刪哪顆）。
    """
    items = [(float(n.start), 1, n) for n in made]
    items += [(float(n.start), 0, n) for n in walls]
    items.sort(key=lambda row: (row[0], row[1]))
    clusters: List[Tuple[float, List[Tuple[int, Any]]]] = []
    for start, flag, note in items:
        if clusters and start - clusters[-1][0] <= float(onset_ms):
            clusters[-1][1].append((flag, note))
        else:
            clusters.append((start, [(flag, note)]))

    moved = 0
    for _start, members in clusters:
        mine = [note for flag, note in members if flag]
        if not mine:
            continue
        taken = [(int(n.min_key), int(n.max_key)) for flag, n in members if not flag]

        def free(lo: int, width: int) -> bool:
            hi = lo + width - 1
            return all(hi < a or b < lo for a, b in taken)

        for note in sorted(mine, key=lambda n: (int(getattr(n, 'pitch', 0) or 0),
                                                int(n.min_key))):
            width = int(note.max_key) - int(note.min_key) + 1
            home = int(note.min_key)
            spot = None
            for step in range(int(total_lanes)):
                for lo in ((home,) if not step else (home - step, home + step)):
                    if lo < 0 or lo + width > int(total_lanes):
                        continue
                    if free(lo, width):
                        spot = lo
                        break
                if spot is not None:
                    break
            if spot is not None and spot != home:
                note.min_key, note.max_key = spot, spot + width - 1
                moved += 1
            taken.append((int(note.min_key), int(note.max_key)))
    return moved


def dominant_width(notes: Iterable[Any], fallback: int = 3) -> int:
    """譜面現有音符最常見的鍵寬。採進來的那一段要沿用它。

    **這是鍵道上最大的單一改善。** 實測（5 份譜面 6278 顆配對，和他自己在那
    一段寫的鍵道比）：寫死寬 3 是完全相同 20.8%／±1 格 64.1%，沿用譜面的鍵寬
    是 **30.2%／68.8%**。原因很實際——他的 Hard 譜整份都是寬 4（兩份都 100%
    一致），寬 4 的中心落在 x.5、寬 3 落在 x.0，**永遠不可能相同**，所以那兩
    份的「完全相同」本來是 0.0%。Expert 譜本來就是寬 3，數字一格都沒動。
    """
    counts = Counter()
    for note in notes:
        try:
            width = int(note.max_key) - int(note.min_key) + 1
        except (AttributeError, TypeError, ValueError):
            continue
        if width > 0:
            counts[width] += 1
    if not counts:
        return int(fallback)
    return int(counts.most_common(1)[0][0])
