"""排列分布：把一段的鍵道配置（哪顆音放哪個鍵道、哪隻手）套到另一段相似的段落。

用法是右鍵「複製排列分布」→ 選另一段 →「套用排列分布」。兩段不必一模一樣：
重複的樂句常常多一顆、少一顆、移了調、或整段快慢差一點點，這裡都要能對上。

比對方法
--------
1. 兩段各自把時間換成「離第一顆音符多遠」，音符照 (時間, 音高) 排好。
2. 候選的「移調量」：時間上靠得近的音符之間，最常見的音高差（加上 0）；
   候選的「快慢比例」：1.0 和兩段長度比（差太多就不算）。
3. 每組 (移調, 比例) 做一次序列對齊（DP）：
   - 兩顆配對的代價 = 時間差 / 容許時間差 + 音高差 / 容許音高差
   - 某一邊多出來、沒配到 = 固定代價 1（所以「有一點點不同」也對得上）
   - 時間差超過容許值 3 倍的不准配對
4. 挑總代價最小的那組。相似度 = Σ(1 − 配對代價) ／ 兩段比較長的那段顆數。

套用只改鍵道（min_key / max_key）和左右手，時間、音高、類型都不動。顫音不參與
（它的鍵道還連著每一格子音符，要改得整顆一起搬，另外處理）。
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

#: 音高差多少算「差很多」（半音）
PITCH_TOLERANCE = 2.0
#: 時間容許值的上下限（ms）；實際值跟著樂句的音符間隔走
MIN_TIME_TOLERANCE = 40.0
MAX_TIME_TOLERANCE = 150.0
#: 沒配到（某一邊多一顆）的代價
GAP_COST = 1.0
#: DP 太大時用的時間帶寬（只在這個範圍內找配對）
MAX_CELLS = 600_000


def _is_trill(note: Any) -> bool:
    return bool(int(getattr(note, 'note_type', 0) or 0) & 0x40)


@dataclass
class Shape:
    """來源段落裡一顆音符的「排列」：相對時間、音高，和它放的位置。"""
    offset: float
    pitch: Optional[int]
    hand: int
    min_key: int
    max_key: int


@dataclass
class Distribution:
    shapes: List[Shape]
    span_ms: float

    def __len__(self) -> int:
        return len(self.shapes)


@dataclass
class Match:
    pairs: List[Tuple[Shape, Any, float]] = field(default_factory=list)
    unmatched: List[Any] = field(default_factory=list)
    similarity: float = 0.0
    transpose: int = 0
    scale: float = 1.0


def capture(notes: Sequence[Any]) -> Distribution:
    """把選起來的音符記成一份排列分布（顫音不算）。"""
    picked = sorted((n for n in notes if not _is_trill(n)),
                    key=lambda n: (int(n.start), _pitch_key(n)))
    if not picked:
        return Distribution([], 0.0)
    first = int(picked[0].start)
    shapes = [Shape(float(int(n.start) - first),
                    None if n.pitch is None else int(n.pitch),
                    int(getattr(n, 'hand', 0) or 0), int(n.min_key), int(n.max_key))
              for n in picked]
    return Distribution(shapes, float(int(picked[-1].start) - first))


def _pitch_key(note: Any) -> int:
    return -1 if note.pitch is None else int(note.pitch)


def _time_tolerance(offsets: Sequence[float]) -> float:
    """容許的時間差：樂句裡相鄰「不同時間」音符間隔的中位數的 35%。"""
    onsets = sorted(set(round(o) for o in offsets))
    gaps = [b - a for a, b in zip(onsets, onsets[1:]) if b - a > 5]
    if not gaps:
        return MAX_TIME_TOLERANCE
    gaps.sort()
    median = gaps[len(gaps) // 2]
    return max(MIN_TIME_TOLERANCE, min(MAX_TIME_TOLERANCE, 0.35 * median))


def _cost(shape: Shape, offset: float, pitch: Optional[int], transpose: int,
          tol_t: float) -> float:
    dt = abs(shape.offset - offset)
    if dt > 3.0 * tol_t:
        return float('inf')
    if shape.pitch is None or pitch is None:
        dp = 0.5
    else:
        dp = min(2.0, abs((shape.pitch + transpose) - pitch) / PITCH_TOLERANCE)
    return min(1.0, dt / tol_t) + dp


def _align(src: Distribution, targets: List[Tuple[float, Optional[int], Any]],
           transpose: int, tol_t: float) -> Tuple[float, List[Tuple[int, int, float]]]:
    """序列對齊（Needleman–Wunsch，只在時間帶寬內算配對）。回傳 (總代價, 配對)。"""
    n, m = len(src.shapes), len(targets)
    inf = float('inf')
    offsets = [t[0] for t in targets]
    window = 3.0 * tol_t
    if n * m > MAX_CELLS:
        window = min(window, max(tol_t, MAX_CELLS / max(1, n) * tol_t / max(1, m) * 4))
    # 每一列只算時間帶寬內的格子；帶寬外當成只能靠「沒配到」走過去
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]     # 0 配對 1 來源多 2 目標多
    for j in range(m + 1):
        dp[0][j] = j * GAP_COST
        back[0][j] = 2
    for i in range(1, n + 1):
        dp[i][0] = i * GAP_COST
        back[i][0] = 1
        shape = src.shapes[i - 1]
        lo = bisect.bisect_left(offsets, shape.offset - window)
        hi = bisect.bisect_right(offsets, shape.offset + window)
        row, prev = dp[i], dp[i - 1]
        brow = back[i]
        for j in range(1, m + 1):
            best, how = prev[j] + GAP_COST, 1
            gap = row[j - 1] + GAP_COST
            if gap < best:
                best, how = gap, 2
            if lo <= j - 1 < hi:
                off, pitch, _note = targets[j - 1]
                c = _cost(shape, off, pitch, transpose, tol_t)
                if c < inf and prev[j - 1] + c < best:
                    best, how = prev[j - 1] + c, 0
            row[j] = best
            brow[j] = how
    pairs: List[Tuple[int, int, float]] = []
    i, j = n, m
    while i > 0 or j > 0:
        how = back[i][j]
        if i > 0 and j > 0 and how == 0:
            shape = src.shapes[i - 1]
            off, pitch, _note = targets[j - 1]
            pairs.append((i - 1, j - 1, _cost(shape, off, pitch, transpose, tol_t)))
            i, j = i - 1, j - 1
        elif i > 0 and (how == 1 or j == 0):
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return dp[n][m], pairs


def match(src: Distribution, notes: Sequence[Any]) -> Match:
    """把目標段落的音符和來源排列分布對起來。"""
    picked = sorted((n for n in notes if not _is_trill(n)),
                    key=lambda n: (int(n.start), _pitch_key(n)))
    if not src.shapes or not picked:
        return Match(unmatched=list(picked))
    first = int(picked[0].start)
    span = float(int(picked[-1].start) - first)

    scales = [1.0]
    if src.span_ms > 0 and span > 0:
        ratio = span / src.span_ms
        if 0.8 <= ratio <= 1.25 and abs(ratio - 1.0) > 0.01:
            scales.append(ratio)
    tol_t = _time_tolerance([s.offset for s in src.shapes])

    best: Optional[Tuple[float, List[Tuple[int, int, float]], int, float, list]] = None
    for scale in scales:
        targets = [((int(n.start) - first) / scale, None if n.pitch is None else int(n.pitch), n)
                   for n in picked]
        for transpose in _transpose_candidates(src, targets, tol_t):
            cost, pairs = _align(src, targets, transpose, tol_t)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, pairs, transpose, scale, targets)
    _cost_total, pairs, transpose, scale, targets = best
    result = Match(transpose=transpose, scale=scale)
    used = set()
    score = 0.0
    for si, tj, c in pairs:
        result.pairs.append((src.shapes[si], targets[tj][2], c))
        used.add(tj)
        score += max(0.0, 1.0 - min(c, 1.0))
    result.unmatched = [targets[j][2] for j in range(len(targets)) if j not in used]
    result.similarity = score / max(len(src.shapes), len(targets))
    return result


def _transpose_candidates(src: Distribution, targets, tol_t: float) -> List[int]:
    """時間上靠得近的音符之間最常見的音高差（移調的樂句），一定包含 0。"""
    offsets = [t[0] for t in targets]
    diffs: Counter = Counter()
    for shape in src.shapes:
        if shape.pitch is None:
            continue
        lo = bisect.bisect_left(offsets, shape.offset - tol_t)
        hi = bisect.bisect_right(offsets, shape.offset + tol_t)
        for off, pitch, _note in targets[lo:hi]:
            if pitch is not None and abs(pitch - shape.pitch) <= 24:
                diffs[pitch - shape.pitch] += 1
    out = [0]
    for diff, _count in diffs.most_common(3):
        if diff not in out:
            out.append(diff)
    return out


def apply(model: Any, result: Match, copy_hand: bool = True) -> int:
    """照配對結果改鍵道（和左右手）。呼叫端負責 push_history / rebuild。回傳改了幾顆。"""
    changed = 0
    midi = bool(getattr(model, 'is_midi_mode', lambda: False)())
    for shape, note, _cost_value in result.pairs:
        new_min, new_max = int(shape.min_key), int(shape.max_key)
        different = (int(note.min_key), int(note.max_key)) != (new_min, new_max)
        note.min_key, note.max_key = new_min, new_max
        if copy_hand and int(getattr(note, 'hand', 0) or 0) in (0, 1) \
                and int(note.hand) != int(shape.hand) and shape.hand in (0, 1):
            note.hand = int(shape.hand)
            different = True
            if midi:
                # MIDI 模式左右手是音軌決定的，只改 hand 排譜時會被改回去
                track = model.midi_track_for_hand(int(shape.hand), [note])
                note.track = track
                note.channel = model._default_midi_channel_for_track(track)
        if different:
            changed += 1
    return changed
