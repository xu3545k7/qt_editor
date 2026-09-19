"""修改 BPM 選「不調整音符」時的「吸附小節線」：從段落開頭做一個很小的縮放，
讓音符對上新的小節線，而不是像「調整音符」那樣整段跟著變慢／變快。

兩種做法，照順序試：

B. **貼合新拍格**（BPM 原本設錯的情況）
   音符其實是照真實速度放的（例如 89.7），只是 BPM 寫成 120。改成 90 之後，找一個
   很接近 1 的比例 s，讓 `開頭 + s ×（音符時間 − 開頭）` 最貼合新拍格的 16 分音符
   格點。越拖越大的誤差會被這個比例吃掉，每一顆都貼回格子上。

A. **段落結尾落在整數小節**（B 找不到明顯好的比例時）
   原本這段的長度換算成新 BPM 通常不是整數小節（34 秒 = 12.75 個 90BPM 小節），
   就把音符等比例微調到剛好 13 小節，後面才不會錯位。

兩種都只在比例夠接近 1（±`MAX_DEVIATION`）時才做，否則什麼都不動並回報原因。
縮放的是「開頭之後的所有音符」（含踏板、強弱記號），拍格本身不動 —— 那正是剛設好
的新 BPM。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, List, Optional

#: 縮放比例最多離 1 多遠才算「微調」
MAX_DEVIATION = 0.08
#: B 算成功的條件：平均離格點的距離（以半個格子為 1）小於這個值
GRID_GOOD = 0.22
#: …而且要比不縮放時明顯好
GRID_IMPROVEMENT = 0.06


@dataclass
class SnapResult:
    method: str          # 'grid' / 'bars' / 'none'
    factor: float = 1.0
    error_before: float = 0.0
    error_after: float = 0.0
    bars: int = 0
    reason: str = ''


def grid_points(model: Any, first_measure: int, last_measure: int) -> List[float]:
    """新拍格的 16 分音符格點（ms），只取修改範圍內的小節。"""
    points: List[float] = []
    for mi in range(first_measure, last_measure + 1):
        start, end = model.get_measure_time_range(mi)
        if start is None or end is None or end <= start:
            continue
        beats = max(1, int(model.get_beats_per_bar_at_ms(float(start)) or 4))
        steps = beats * 4
        for k in range(steps):
            points.append(start + (end - start) * k / steps)
    last = model.get_measure_time_range(last_measure)[1]
    if last is not None:
        points.append(float(last))
    return sorted(points)


def _error(onsets: List[float], anchor: float, factor: float, points: List[float]) -> float:
    """音符離最近格點的平均距離，以「半個格子」為 1（亂放的期望值約 0.5）。"""
    if not onsets or len(points) < 2:
        return 1.0
    total = 0.0
    for t in onsets:
        x = anchor + factor * (t - anchor)
        i = bisect.bisect_left(points, x)
        lo = points[i - 1] if i > 0 else points[0]
        hi = points[i] if i < len(points) else points[-1]
        half = max(1e-6, (hi - lo) / 2.0) if hi > lo else max(
            1e-6, (points[1] - points[0]) / 2.0)
        total += min(1.0, min(abs(x - lo), abs(hi - x)) / half)
    return total / len(onsets)


def fit_grid(onsets: List[float], anchor: float, points: List[float]) -> SnapResult:
    """B：找最貼合新拍格的縮放比例。"""
    if len(onsets) < 4 or len(points) < 2:
        return SnapResult('none', reason='音符太少，量不出貼不貼合')
    before = _error(onsets, anchor, 1.0, points)
    span = max(onsets) - anchor
    step_ms = (points[1] - points[0]) if len(points) > 1 else 50.0
    if span <= 0:
        return SnapResult('none', error_before=before, reason='段落沒有長度')
    # 比例每走一步，最後一顆音符最多移動 1/4 格：粗掃再細掃
    fine = max(1e-5, (step_ms / 4.0) / span)
    best_f, best_e = 1.0, before
    coarse = max(fine, MAX_DEVIATION / 200.0)
    f = 1.0 - MAX_DEVIATION
    while f <= 1.0 + MAX_DEVIATION + 1e-12:
        e = _error(onsets, anchor, f, points)
        if e < best_e - 1e-9:
            best_f, best_e = f, e
        f += coarse
    lo, hi = max(1.0 - MAX_DEVIATION, best_f - coarse), min(1.0 + MAX_DEVIATION, best_f + coarse)
    f = lo
    while f <= hi + 1e-12:
        e = _error(onsets, anchor, f, points)
        if e < best_e - 1e-9:
            best_f, best_e = f, e
        f += fine
    if best_e <= GRID_GOOD and (before - best_e >= GRID_IMPROVEMENT or before <= GRID_GOOD):
        if before <= best_e + 1e-9 or abs(best_f - 1.0) < 1e-6:
            return SnapResult('grid', 1.0, before, before, reason='本來就貼合新拍格')
        return SnapResult('grid', best_f, before, best_e)
    return SnapResult('none', best_f, before, best_e, reason='找不到明顯貼合新拍格的比例')


def fit_bars(anchor: float, old_end: float, new_bar_ms: float) -> SnapResult:
    """A：讓原本這段的長度剛好是整數個新小節。"""
    length = old_end - anchor
    if length <= 0 or new_bar_ms <= 0:
        return SnapResult('none', reason='段落沒有長度')
    bars = max(1, int(round(length / new_bar_ms)))
    factor = bars * new_bar_ms / length
    if abs(factor - 1.0) > MAX_DEVIATION:
        return SnapResult('none', factor, bars=bars,
                          reason='要縮放 %.1f%% 才對得上整數小節，超過微調範圍' % ((factor - 1) * 100))
    return SnapResult('bars', factor, bars=bars)


def scale_from(model: Any, anchor: float, factor: float) -> int:
    """從 anchor 開始把之後的音符（含子音符、踏板、強弱）時間乘上 factor。拍格不動。"""
    if abs(factor - 1.0) < 1e-9:
        return 0

    def _s(value: float) -> int:
        value = float(value)
        if value < anchor:
            return int(round(value))
        return int(round(anchor + factor * (value - anchor)))

    changed = 0
    for note in model.notes_tree:
        if int(note.start) < anchor:
            continue
        start, end = _s(note.start), _s(note.end)
        note.start, note.end = start, max(start + 1, end)
        note.gate = note.end - note.start
        for sub in getattr(note, 'sub_elems', None) or []:
            for tag in ('start_timing_msec', 'end_timing_msec'):
                el = sub.find(tag)
                if el is not None and el.text not in (None, ''):
                    el.text = str(_s(float(el.text)))
        changed += 1
    if getattr(model, 'pedal_spans', None):
        model.pedal_spans = [[float(_s(a)), float(_s(b))] for a, b in model.pedal_spans]
    if getattr(model, 'dynamics', None):
        model.dynamics = {hand: [[float(_s(ms)), lvl, ramp] for ms, lvl, ramp in marks]
                          for hand, marks in model.dynamics.items()}
    if float(getattr(model, 'music_end_ms', 0) or 0) > anchor:
        model.music_end_ms = float(_s(model.music_end_ms))
    model.dirty = True
    return changed


def snap(model: Any, first_measure: int, last_measure: int, anchor: float,
         old_end: float) -> SnapResult:
    """改完 BPM（不調整音符）之後做吸附。B 優先，對不上退回 A。已經套用到 model。"""
    onsets = sorted(float(n.start) for n in model.notes_tree
                    if anchor <= float(n.start) < old_end)
    points = grid_points(model, first_measure, last_measure)
    result = fit_grid(onsets, anchor, points)
    if result.method == 'none':
        start, end = model.get_measure_time_range(first_measure)
        new_bar = float(end - start) if start is not None and end is not None else 0.0
        fallback = fit_bars(anchor, old_end, new_bar)
        fallback.error_before = result.error_before
        if fallback.method == 'none':
            fallback.reason = result.reason + '；' + fallback.reason
            return fallback
        result = fallback
    if result.factor != 1.0:
        scale_from(model, anchor, result.factor)
    if result.method == 'grid':
        result.error_after = _error(
            sorted(float(n.start) for n in model.notes_tree
                   if anchor <= float(n.start) < anchor + result.factor * (old_end - anchor)),
            anchor, 1.0, points)
    return result


def describe(result: SnapResult) -> str:
    if result.method == 'grid':
        if abs(result.factor - 1.0) < 1e-6:
            return '吸附小節線：音符本來就貼合新拍格，沒有縮放'
        return ('吸附小節線：音符 ×%.4f 貼合新拍格（離格點的平均距離 %.0f%% → %.0f%%）'
                % (result.factor, result.error_before * 100, result.error_after * 100))
    if result.method == 'bars':
        return ('吸附小節線：音符 ×%.4f，這段剛好收在 %d 個新小節' % (result.factor, result.bars))
    return '吸附小節線：沒有縮放（%s）' % result.reason
