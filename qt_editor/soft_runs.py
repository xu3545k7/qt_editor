"""連續的 soft 依「在畫面上怎麼走」轉成滑奏或顫音（輸出 Hiraeth 歌曲包用）。

Hiraeth（PAN）沒有 soft，以前一律變成點擊。使用者的規則：**垂直的就是顫音、斜著一路
走的就是滑奏**——看的是鍵道，不是音高。音高常常有一兩顆跳出來（八度重複的起音、
換手的音），但鍵道是排譜時就排好的走向，畫面上看到的就是它。

判斷（同一隻手、前後相接的 soft）：

1. **先找顫音（垂直）**：不往前走的片段——來回跳（至少換兩次方向）或完全停在原地——
   至少 4 顆、間隔 ≤ 120ms、左右 ≤ 8 格。官方顫音子音間隔中位 70ms、90% ≤ 93ms；兩音
   距離最常見的是八度（佔三分之一），所以寬的震音也算。只看範圍小不夠：慢慢往上爬
   的線範圍也小，但它是斜的；滑奏開頭停在同一格的幾顆也不算（沒有來回）。
2. **剩下的依鍵道走向切段**：一路往同一邊（可以原地踏步）就是同一段；至少 2 顆、
   移動 ≥ 1 格、每步間隔 ≤ 90ms → 滑奏鏈。官方 Real 的 310 條滑奏鏈**全部**是鍵道單向，
   間隔中位 44ms、90% ≤ 82ms；各難度 7660 條裡 3 顆的 2121 條、2 顆的也有 276 條。
   顫音一定要先找：否則來回跳的每一對都會被當成 2 顆的滑奏。
3. 其他照舊是點擊（單獨一顆、左右大跳又太慢的分散和弦）。

同一時刻同一隻手有好幾顆 soft（八度一起滑）時，依高低拆成聲部各自判斷，
變成平行的滑奏鏈——官方同手同一時刻兩條滑奏也有 39 處。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence

from .models import SOFT_NOTE_TYPE, chain_slide_notes, make_trill_from_notes

SLIDE_MAX_GAP_MS = 90
TRILL_MAX_GAP_MS = 120
MIN_NOTES = 4          # 顫音
MIN_SLIDE_NOTES = 2
SAME_TIME_MS = 5
SLIDE_MIN_TRAVEL = 1.0
TRILL_MAX_SPREAD = 8.0


@dataclass
class SoftRunResult:
    slide_chains: int = 0
    slide_notes: int = 0
    trills: int = 0
    trill_notes: int = 0


def _centre(note: Any) -> float:
    """判斷走向用的位置：右緣。不用中心——寬度 2、3 格交替時中心會差 0.5，一路往下滑
    的音看起來像在來回抖，會被誤判成顫音。右緣是排譜的基準，收窄時不動。"""
    return float(int(note.max_key))


def soft_runs(notes: Sequence[Any], max_gap_ms: int) -> List[List[Any]]:
    """同一隻手、前後相接的 soft 串（中間沒有同手的其他音、間隔 ≤ max_gap_ms）。

    同一時刻有好幾顆 soft 時依高低拆成聲部：最高的一條、第二高的一條……各自成串。
    """
    runs: List[List[Any]] = []
    # 隱藏音符（低難度看不見、存在寄主的 sub_note 裡）不參與：串進滑奏或包進顫音
    # 都會讓它變成看得見的音
    notes = [n for n in notes if not getattr(n, 'hidden', False)]
    for hand in sorted({int(getattr(n, 'hand', 0)) for n in notes}):
        seq = sorted((n for n in notes if int(getattr(n, 'hand', 0)) == hand),
                     key=lambda n: (int(n.start), int(n.min_key)))
        # 同一時刻的音併成一格；格裡有非 soft 的音就是斷點
        slots: List[List[Any]] = []
        for note in seq:
            if slots and int(note.start) - int(slots[-1][0].start) <= SAME_TIME_MS:
                slots[-1].append(note)
            else:
                slots.append([note])
        stretch: List[List[Any]] = []
        for slot in slots + [None]:
            usable = (slot is not None
                      and all(int(n.note_type) == SOFT_NOTE_TYPE for n in slot)
                      and (not stretch or int(slot[0].start) - int(stretch[-1][0].start) <= max_gap_ms))
            if usable:
                stretch.append(slot)
                continue
            runs.extend(_voice_runs(stretch, max_gap_ms))
            stretch = [slot] if (slot is not None and all(
                int(n.note_type) == SOFT_NOTE_TYPE for n in slot)) else []
    return runs


def _voice_runs(stretch: Sequence[List[Any]], max_gap_ms: int) -> List[List[Any]]:
    """一段連續的 soft 格子拆成聲部（由高到低），每個聲部再依間隔切成串。"""
    out: List[List[Any]] = []
    if not stretch:
        return out
    depth = max(len(slot) for slot in stretch)
    for voice in range(depth):
        line = [sorted(slot, key=lambda n: (-int(n.max_key), -int(n.min_key)))[voice]
                for slot in stretch if len(slot) > voice]
        run: List[Any] = []
        for note in line:
            if run and int(note.start) - int(run[-1].start) > max_gap_ms:
                if len(run) >= MIN_SLIDE_NOTES:
                    out.append(run)
                run = []
            run.append(note)
        if len(run) >= MIN_SLIDE_NOTES:
            out.append(run)
    return out


def _direction_segments(run: Sequence[Any]) -> List[List[Any]]:
    """依鍵道中心的走向切段：方向第一次確定之後，反方向的一步就開新的一段。"""
    segments: List[List[Any]] = []
    current: List[Any] = [run[0]]
    direction = 0
    for prev, note in zip(run, run[1:]):
        step = _centre(note) - _centre(prev)
        sign = (step > 0) - (step < 0)
        if sign and direction and sign != direction:
            segments.append(current)
            current = [note]
            direction = 0
            continue
        if sign:
            direction = sign
        current.append(note)
    segments.append(current)
    return segments


def _gaps_ok(notes: Sequence[Any], limit: int) -> bool:
    return all(0 < int(b.start) - int(a.start) <= limit for a, b in zip(notes, notes[1:]))


def _vertical_windows(notes: Sequence[Any]) -> List[tuple]:
    """不往前走的片段 → [(起點, 終點)]（含終點）。

    相鄰兩步不能往同一個方向；而且要真的來回（換方向 ≥ 2 次）或完全停在原地。
    """
    out: List[tuple] = []
    i = 0
    while i < len(notes):
        j = i
        last = 0
        while j + 1 < len(notes):
            if not (0 < int(notes[j + 1].start) - int(notes[j].start) <= TRILL_MAX_GAP_MS):
                break
            step = _centre(notes[j + 1]) - _centre(notes[j])
            sign = (step > 0) - (step < 0)
            if sign and sign == last:
                break
            if sign:
                last = sign
            j += 1
        window = notes[i:j + 1]
        centres = [_centre(n) for n in window]
        signs = [s for s in ((b > a) - (b < a) for a, b in zip(centres, centres[1:])) if s]
        reversals = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        if (len(window) >= MIN_NOTES and max(centres) - min(centres) <= TRILL_MAX_SPREAD
                and (reversals >= 2 or not signs)):
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def classify_run(run: Sequence[Any]) -> List[tuple]:
    """回傳 [('slide' | 'trill', [音符…]), …]；沒列出的音符維持點擊。"""
    out: List[tuple] = []
    run = list(run)
    taken = [False] * len(run)
    for i, j in _vertical_windows(run):
        out.append(('trill', run[i:j + 1]))
        for k in range(i, j + 1):
            taken[k] = True
    # 顫音以外的部分，依相連的片段分別找滑奏
    piece: List[Any] = []
    for index, note in enumerate(run + [None]):
        if note is not None and not taken[index]:
            piece.append(note)
            continue
        if piece:
            for segment in _direction_segments(piece):
                travel = abs(_centre(segment[-1]) - _centre(segment[0]))
                if (len(segment) >= MIN_SLIDE_NOTES and travel >= SLIDE_MIN_TRAVEL
                        and _gaps_ok(segment, SLIDE_MAX_GAP_MS)):
                    out.append(('slide', list(segment)))
        piece = []
    return out


def convert_soft_runs(model: Any) -> SoftRunResult:
    """把 model 裡連續的 soft 轉成滑奏鏈／顫音（就地修改）。其他 soft 不動。"""
    result = SoftRunResult()
    notes = list(model.notes_tree)
    remove = set()
    added: List[Any] = []
    for run in soft_runs(notes, max(SLIDE_MAX_GAP_MS, TRILL_MAX_GAP_MS)):
        for kind, group in classify_run(run):
            if kind == 'slide':
                chain_slide_notes(model.notes_tree, group)
                result.slide_chains += 1
                result.slide_notes += len(group)
            else:
                trill = make_trill_from_notes(list(group), int(group[0].hand))
                if trill is None:
                    continue
                remove.update(id(n) for n in group)
                added.append(trill)
                result.trills += 1
                result.trill_notes += len(group)
    if remove or added:
        model.notes_tree = [n for n in model.notes_tree if id(n) not in remove] + added
    if result.slide_notes or result.trills:
        model.rebuild_display_cache()
    return result
