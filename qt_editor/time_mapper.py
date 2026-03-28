"""
time_mapper.py
==============
毫秒 ↔ 拍次單位的雙向映射。

邏輯完全萃取自 graphical_chartmaker.py 的 _time_to_unit / _unit_to_time。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import List, Optional, Tuple


class TimeMapper:
    """把毫秒（ms）與渲染用的拍次單位（beat unit）互相轉換。

    使用方法
    --------
    mapper = TimeMapper()
    mapper.build(beat_entries, bpm, music_end_ms)
    unit = mapper.ms_to_unit(1234.0)
    ms   = mapper.unit_to_ms(4.5)
    """

    def __init__(self) -> None:
        self._bpm: float = 120.0
        self._default_unit_per_ms: float = self._bpm / 60000.0

        # [(ms, unit), ...] 已排序（ms 升序）
        self._time_pts: List[Tuple[float, float]] = []
        self._time_ms_keys: List[float] = []

        # [(unit, ms), ...] 已排序（unit 升序）
        self._unit_pts: List[Tuple[float, float]] = []
        self._unit_keys: List[float] = []

    # ------------------------------------------------------------------
    # 建立映射表
    # ------------------------------------------------------------------

    def build(
        self,
        beat_entries: List[Tuple[int, int]],
        bpm: float,
        music_end_ms: float = 0.0,
        beats_per_bar: int = 4,
    ) -> None:
        """重建整個映射。

        beat_entries
            來自 NoteModel.get_beat_entries()，格式 [(beat_idx, ms), ...]。
        bpm
            當沒有 beat_data 時的 fallback。
        """
        self._bpm = bpm if bpm > 0 else 120.0
        self._default_unit_per_ms = self._bpm / 60000.0

        beats = sorted(beat_entries, key=lambda x: x[1])

        if len(beats) < 2:
            beats = self._generate_fallback_beats(bpm, music_end_ms)

        # 建立 time_pts：(ms, unit)
        time_pts: List[Tuple[float, float]] = [
            (float(ms), float(idx)) for idx, ms in beats
        ]

        # 若 unit 差異 >> 1（小節索引 → 拍次索引），做縮放
        unit_pts = [(float(idx), float(ms)) for idx, ms in beats]
        unit_pts = self._normalize_units(unit_pts, beats_per_bar)

        # 同步更新 time_pts 的 unit 欄位
        time_pts = [(ms, u) for (u, ms) in unit_pts]

        self._time_pts = sorted(time_pts, key=lambda x: x[0])
        self._time_ms_keys = [ms for ms, _ in self._time_pts]

        self._unit_pts = sorted(unit_pts, key=lambda x: x[0])
        self._unit_keys = [u for u, _ in self._unit_pts]

    # ------------------------------------------------------------------
    # fallback：等速拍次產生
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_fallback_beats(
        bpm: float, music_end_ms: float
    ) -> List[Tuple[int, int]]:
        bpm = bpm if bpm > 0 else 120.0
        beat_ms = 60000.0 / bpm
        total_ms = max(music_end_ms, beat_ms * 32)
        count = int(math.ceil(total_ms / beat_ms)) + 8
        return [(i, int(i * beat_ms)) for i in range(count + 1)]

    # ------------------------------------------------------------------
    # 單位正規化：偵測是否為「小節」索引並轉為「拍次」
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_units(
        unit_pts: List[Tuple[float, float]], beats_per_bar: int
    ) -> List[Tuple[float, float]]:
        """若 beat index 以小節為單位（差值 == beats_per_bar），轉為拍次。"""
        if len(unit_pts) < 2:
            return unit_pts

        diffs = [unit_pts[i + 1][0] - unit_pts[i][0]
                 for i in range(min(8, len(unit_pts) - 1))]
        avg_diff = sum(diffs) / len(diffs) if diffs else 1.0

        if abs(avg_diff - beats_per_bar) < 0.5 and beats_per_bar > 1:
            # 小節 → 拍次
            return [(u * beats_per_bar, ms) for u, ms in unit_pts]

        # 如果差值 > 1 但不是整數倍，仍正規化為步長 = 1
        if avg_diff > 1.5:
            factor = avg_diff
            return [(u / factor, ms) for u, ms in unit_pts]

        return unit_pts

    # ------------------------------------------------------------------
    # 對外接口
    # ------------------------------------------------------------------

    def ms_to_unit(self, ms: float) -> float:
        """毫秒 → 拍次單位（線性插值）。"""
        pts = self._time_pts
        if not pts:
            return ms * self._default_unit_per_ms

        if ms <= pts[0][0]:
            slope = ((pts[1][1] - pts[0][1]) / (pts[1][0] - pts[0][0])
                     if len(pts) > 1 and pts[1][0] != pts[0][0]
                     else self._default_unit_per_ms)
            return pts[0][1] + (ms - pts[0][0]) * slope

        if ms >= pts[-1][0]:
            lms, lu = pts[-1]
            slope = ((pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
                     if len(pts) > 1 and pts[-1][0] != pts[-2][0]
                     else self._default_unit_per_ms)
            return lu + (ms - lms) * slope

        idx = bisect_right(self._time_ms_keys, ms) - 1
        idx = max(0, min(idx, len(pts) - 2))
        ms0, u0 = pts[idx]
        ms1, u1 = pts[idx + 1]
        slope = (u1 - u0) / (ms1 - ms0) if ms1 != ms0 else self._default_unit_per_ms
        return u0 + (ms - ms0) * slope

    def unit_to_ms(self, unit: float) -> float:
        """拍次單位 → 毫秒（線性插值）。"""
        pts = self._unit_pts
        if not pts:
            return unit / self._default_unit_per_ms if self._default_unit_per_ms else 0.0

        ms_per_unit = (1.0 / self._default_unit_per_ms) if self._default_unit_per_ms > 0 else 500.0

        if unit <= pts[0][0]:
            slope = ((pts[1][1] - pts[0][1]) / (pts[1][0] - pts[0][0])
                     if len(pts) > 1 and pts[1][0] != pts[0][0]
                     else ms_per_unit)
            return pts[0][1] + (unit - pts[0][0]) * slope

        if unit >= pts[-1][0]:
            lu, lms = pts[-1]
            slope = ((pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
                     if len(pts) > 1 and pts[-1][0] != pts[-2][0]
                     else ms_per_unit)
            return lms + (unit - lu) * slope

        idx = bisect_right(self._unit_keys, unit) - 1
        idx = max(0, min(idx, len(pts) - 2))
        u0, ms0 = pts[idx]
        u1, ms1 = pts[idx + 1]
        slope = (ms1 - ms0) / (u1 - u0) if u1 != u0 else ms_per_unit
        return ms0 + (unit - u0) * slope

    # ------------------------------------------------------------------
    # 視窗邊界計算（便利方法）
    # ------------------------------------------------------------------

    def window_ms_range(
        self, window_start_unit: float, window_size_unit: float
    ) -> Tuple[float, float]:
        """回傳目前視窗對應的 (start_ms, end_ms)。"""
        return (
            self.unit_to_ms(window_start_unit),
            self.unit_to_ms(window_start_unit + window_size_unit),
        )

    def unit_range_of_notes(
        self, notes: list
    ) -> Tuple[float, float]:
        """回傳音符列表的 (min_unit, max_unit)。"""
        if not notes:
            return 0.0, 0.0
        min_u = min(self.ms_to_unit(float(n.start)) for n in notes)
        max_u = max(self.ms_to_unit(float(n.end))   for n in notes)
        return min_u, max_u
