#!/usr/bin/env python3
"""Simple MIDI→XML converter focusing on 88→28 key mapping and overlap cleanup.

Copied/embedded from workspace root implementation to keep conversion logic
identical to the original project script.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido
import xml.dom.minidom
import xml.etree.ElementTree as ET

from .models import open_midi


def _midi_to_official_scale(pitch: int) -> int:
    """MIDI pitch (A0=21..C8=108) → 官方 scale_piano (A0=1..C8=88)。
    遊戲與 qt_editor 都以 scale_piano=1 對應 A0(MIDI21)，載入時 +20。
    轉換器內部的 scale_piano 存的是 MIDI pitch，寫 XML 前要 -20。"""
    return max(1, min(88, int(pitch) - 20))

try:
    from .smart_chart import (
    SmartChartSettings, SmartChartStats, arrange_midi_notes,
    settings_for_style, STYLE_EATHER,
)
except ImportError:  # pragma: no cover - direct script execution
    from smart_chart import SmartChartSettings, SmartChartStats, arrange_midi_notes


@dataclass
class SubNote:
    start_timing_msec: int
    end_timing_msec: int
    scale_piano: int
    velocity: int
    track: int


@dataclass
class Note:
    index: int
    start_timing_msec: int
    end_timing_msec: int
    gate_time_msec: int
    scale_piano: int
    min_key_index: int
    max_key_index: int
    note_type: int
    hand: int
    key_kind: int
    sub_notes: List[SubNote]

    # smart_chart works with the editor's GNote interface. These aliases keep
    # the standalone converter on the exact same arrangement path.
    @property
    def start(self) -> int:
        return self.start_timing_msec

    @property
    def end(self) -> int:
        return self.end_timing_msec

    @property
    def pitch(self) -> int:
        return self.scale_piano

    @property
    def min_key(self) -> int:
        return self.min_key_index

    @min_key.setter
    def min_key(self, value: int) -> None:
        self.min_key_index = int(value)

    @property
    def max_key(self) -> int:
        return self.max_key_index

    @max_key.setter
    def max_key(self, value: int) -> None:
        self.max_key_index = int(value)

    @property
    def track(self) -> int:
        return self.sub_notes[0].track if self.sub_notes else 0


@dataclass
class VelocityZone:
    index: int
    start_timing_msec: int
    end_timing_msec: int
    velocity_type: int  # 0 = softer, 1 = stronger


class MIDIToXMLConverter:
    TARGET_KEYS = 28
    MIN_MIDI_PITCH = 21
    MAX_MIDI_PITCH = 108
    HOLD_THRESHOLD_MS = 500

    def __init__(self) -> None:
        self.notes: List[Note] = []
        self.tempo_events: List[Tuple[int, int]] = []
        self.ticks_per_beat: int = 480
        self.first_tempo_us: int = 500_000
        self.first_bpm: Optional[int] = None
        self.time_signature_numerator = 4
        self.time_signature_denominator = 4
        self.velocity_zones: List[VelocityZone] = []
        self.tempo_map_ticks: List[Tuple[int, int]] = []
        self.time_signature_events_ticks: List[Tuple[int, int, int]] = []
        self.song_end_tick: int = 0
        self.smart_stats: Optional[SmartChartStats] = None
        # 音高映射範圍：預設用「本 MIDI 實際最低~最高音高」平均分配到 28 鍵，
        # 而非固定滿量程 21~108（None = 尚未計算，退回滿量程）。
        self.pitch_lo: Optional[int] = None
        self.pitch_hi: Optional[int] = None

    def convert_midi_to_xml(self, midi_path: str, output_path: str, resolve_overlaps: bool = True) -> None:
        print(f"轉換 {midi_path} -> {output_path}")
        mid = open_midi(midi_path)

        self.velocity_zones = []
        self.time_signature_events_ticks = []
        self.song_end_tick = 0
        self.tempo_map_ticks = []

        raw_notes = self._extract_raw_notes(mid)
        print(f"取得 {len(raw_notes)} 筆原始音符")

        self._analyze_velocity_zones(raw_notes)

        simple_notes = self._create_simple_notes(raw_notes)
        beat_ms = 60_000.0 / max(1.0, float(self.first_bpm or 120))
        self.smart_stats = arrange_midi_notes(
            simple_notes,
            settings_for_style(
                getattr(self, 'chart_style', None) or STYLE_EATHER,
                beat_ms=beat_ms,
                classify_articulations=True,
            ),
        )
        # The smart arranger already guarantees non-overlap for close-time
        # groups. Keep the legacy fallback only for physically impossible
        # groups, since running it unconditionally can move pitch peaks away
        # from the chart edges.
        if resolve_overlaps and self.smart_stats.unresolved_overlaps:
            self._resolve_overlaps(simple_notes)

        for idx, note in enumerate(simple_notes):
            note.index = idx
            center = (note.min_key_index + note.max_key_index) // 2
            note.key_kind = self._determine_key_kind(center)

        self.notes = simple_notes
        self._write_xml(output_path)
        print(f"完成，輸出 {len(self.notes)} 個音符")

    # ------------------------------------------------------------------
    # MIDI parsing helpers

    def _extract_raw_notes(self, mid: mido.MidiFile) -> List[Dict[str, int]]:
        self.ticks_per_beat = mid.ticks_per_beat
        tempo_events_ticks: List[Tuple[int, int]] = []
        raw_notes_ticks: List[Dict[str, int]] = []
        self.first_tempo_us = 500_000
        self.first_bpm = None
        self.time_signature_events_ticks = [
            (0, self.time_signature_numerator, self.time_signature_denominator)
        ]

        # Output the number of tracks for debugging
        print(f"MIDI 檔案包含 {len(mid.tracks)} 個音軌")

        for track_idx, track in enumerate(mid.tracks):
            current_tick = 0
            active: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

            for msg in track:
                current_tick += msg.time

                if msg.type == "set_tempo":
                    tempo_value = int(msg.tempo)
                    tempo_events_ticks.append((current_tick, tempo_value))
                    if self.first_bpm is None:
                        self.first_tempo_us = tempo_value
                        try:
                            self.first_bpm = int(round(60_000_000 / tempo_value))
                        except ZeroDivisionError:
                            self.first_bpm = None

                if msg.type == "time_signature":
                    try:
                        num = int(msg.numerator)
                        den = int(msg.denominator)
                        self.time_signature_numerator = num
                        self.time_signature_denominator = den
                        if current_tick == 0 and self.time_signature_events_ticks:
                            self.time_signature_events_ticks[0] = (0, num, den)
                        else:
                            self.time_signature_events_ticks.append((current_tick, num, den))
                    except Exception:
                        pass

                if msg.type == "note_on" and msg.velocity > 0:
                    key = (int(msg.channel), int(msg.note))
                    active.setdefault(key, []).append(
                        (current_tick, int(msg.velocity))
                    )
                elif msg.type in {"note_off", "note_on"}:
                    key = (int(msg.channel), int(msg.note))
                    stack = active.get(key)
                    if stack:
                        # 同音配對用**先進先出**：最早那個還沒配對的
                        # note_on，配下一個 note_off。舊版是 stack.pop()
                        # （後進先出），碰到真實鋼琴 MIDI 常見的圓滑奏
                        # ——下一顆已經響了、前一顆的 note_off 才晚一兩個
                        # tick 到——就會把 off 配給剛響的那顆，解析成
                        # 「前一顆 500ms + 新的那顆 1ms」的鬼音。
                        start_tick, velocity = stack.pop(0)
                        raw_notes_ticks.append(
                            {
                                "start_tick": start_tick,
                                "end_tick": current_tick,
                                "scale_piano": int(msg.note),
                                "velocity": velocity,
                                "track": track_idx,
                            }
                        )
                        if current_tick > self.song_end_tick:
                            self.song_end_tick = current_tick
                        if not stack:
                            active.pop(key, None)

            # Preserve hanging notes instead of silently dropping them. A
            # malformed MIDI without note-off is closed at the track end.
            for (_channel, pitch), stack in active.items():
                for start_tick, velocity in stack:
                    end_tick = max(int(start_tick) + 1, int(current_tick))
                    raw_notes_ticks.append(
                        {
                            "start_tick": int(start_tick),
                            "end_tick": end_tick,
                            "scale_piano": int(pitch),
                            "velocity": int(velocity),
                            "track": track_idx,
                        }
                    )
                    self.song_end_tick = max(self.song_end_tick, end_tick)

        raw_notes_ticks.sort(key=lambda n: n["start_tick"])
        tempo_map = self._build_tempo_map(tempo_events_ticks, self.first_tempo_us)
        self.tempo_map_ticks = tempo_map

        self.tempo_events = [
            (int(round(self._ticks_to_ms(tick, tempo_map))), tempo)
            for tick, tempo in tempo_map
        ]

        left_tracks, fallback_pitch = self._split_hands(raw_notes_ticks)

        raw_notes_ms: List[Dict[str, int]] = []
        for note in raw_notes_ticks:
            start_ms = int(round(self._ticks_to_ms(note["start_tick"], tempo_map)))
            end_ms = int(round(self._ticks_to_ms(note["end_tick"], tempo_map)))
            if end_ms <= start_ms:
                end_ms = start_ms + 1

            if left_tracks is not None:
                hand = 1 if int(note["track"]) in left_tracks else 0
            else:
                hand = 1 if int(note["scale_piano"]) < fallback_pitch else 0

            raw_notes_ms.append(
                {
                    "start_timing_msec": start_ms,
                    "end_timing_msec": end_ms,
                    "scale_piano": note["scale_piano"],
                    "velocity": note["velocity"],
                    "track": note["track"],
                    "hand": hand,
                }
            )

        # Debugging: Output track and hand assignment for each note
        for note in raw_notes_ms:
            print(f"音符: start={note['start_timing_msec']}, track={note['track']}, hand={note['hand']}")

        return raw_notes_ms


    @staticmethod
    def _split_hands(raw_notes_ticks: List[Dict[str, int]]):
        """決定哪些音軌算左手。回傳 (左手音軌集合 或 None, 退路用的分界音高)。

        原本的寫法是 `hand = 0 if len(mid.tracks) == 2 and track == 0 else 1`，
        有兩個問題：

        1. **音軌數不是 2 就整份變成左手。** `len(mid.tracks)` 連沒有音符的軌也算，
           而 format 1 的鋼琴 MIDI 很常是 [指揮軌, 右手, 左手] 三軌。這個曲庫裡就有
           5 個檔案中招（RedStormSentiment 3 軌、DF 3 軌、xigite 4 軌、Designant 22 軌）。
        2. **假設 track 0 是右手。** 這個曲庫的 22 個兩軌 MIDI 剛好全部成立（track 0
           的平均音高都比較高），但那是慣例不是保證，而且一旦不成立就整份左右相反。

        改成看**平均音高**：只算有音符的音軌，照平均音高排序，低的那一半給左手。
        這和 `smart_chart._assign_hands` 用的是同一條規則，兩邊才不會各自為政。
        只有一條有音符的音軌時回 None，改用該軌音高中位數當分界（同樣比照排譜器）。
        """
        by_track: Dict[int, List[int]] = {}
        for note in raw_notes_ticks:
            by_track.setdefault(int(note["track"]), []).append(int(note["scale_piano"]))
        by_track = {track: ps for track, ps in by_track.items() if ps}

        if len(by_track) >= 2:
            means = {track: sum(ps) / len(ps) for track, ps in by_track.items()}
            ordered = sorted(by_track, key=lambda track: (means[track], track))
            split = max(1, len(ordered) // 2)
            return set(ordered[:split]), 0

        pitches = sorted(p for ps in by_track.values() for p in ps)
        if not pitches:
            return None, 60
        return None, pitches[len(pitches) // 2]

    def _build_tempo_map(
        self, events_ticks: List[Tuple[int, int]], default_tempo: int
    ) -> List[Tuple[int, int]]:
        merged: List[Tuple[int, int]] = []
        events = sorted(events_ticks, key=lambda x: x[0]) if events_ticks else []

        if not events or events[0][0] != 0:
            merged.append((0, default_tempo))

        for tick, tempo in events:
            if merged and merged[-1][0] == tick:
                merged[-1] = (tick, tempo)
            else:
                merged.append((tick, tempo))

        if not merged:
            merged.append((0, default_tempo))

        return merged

    def _ticks_to_ms(self, tick: int, tempo_map: List[Tuple[int, int]]) -> float:
        if tick <= 0:
            return 0.0

        total_ms = 0.0
        prev_tick, prev_tempo = tempo_map[0]

        for next_tick, next_tempo in tempo_map[1:]:
            if tick <= next_tick:
                delta = tick - prev_tick
                total_ms += self._ticks_delta_to_ms(delta, prev_tempo)
                return total_ms
            delta = next_tick - prev_tick
            total_ms += self._ticks_delta_to_ms(delta, prev_tempo)
            prev_tick, prev_tempo = next_tick, next_tempo

        delta = tick - prev_tick
        total_ms += self._ticks_delta_to_ms(delta, prev_tempo)
        return total_ms

    def _ticks_delta_to_ms(self, delta_ticks: int, tempo_us_per_beat: int) -> float:
        if delta_ticks <= 0:
            return 0.0
        return (delta_ticks * tempo_us_per_beat) / (self.ticks_per_beat * 1000.0)

    # ------------------------------------------------------------------
    # Mapping helpers

    def _create_simple_notes(self, raw_notes: List[Dict[str, int]]) -> List[Note]:
        notes: List[Note] = []
        # 以本 MIDI 實際的最低/最高音高當映射範圍（整體最低~最高平均分配到 28 鍵）。
        pitches = [int(d["scale_piano"]) for d in raw_notes if "scale_piano" in d]
        if pitches:
            self.pitch_lo = min(pitches)
            self.pitch_hi = max(pitches)
        for idx, data in enumerate(raw_notes):
            start = data["start_timing_msec"]
            end = data["end_timing_msec"]
            pitch = data["scale_piano"]
            base_index = self._map_pitch_to_index(pitch)
            min_key_index, max_key_index = self._compute_width_three_range(base_index)
            center_index = (min_key_index + max_key_index) // 2

            # Prioritize hand assignment based on track
            if "hand" in data:
                hand = data["hand"]
            else:
                hand = 1 if center_index < self.TARGET_KEYS // 2 else 0

            key_kind = self._determine_key_kind(center_index)
            note_type = 2 if (end - start) >= self.HOLD_THRESHOLD_MS else 0

            sub_note = SubNote(
                start_timing_msec=start,
                end_timing_msec=end,
                scale_piano=pitch,
                velocity=data["velocity"],
                track=data["track"],
            )

            notes.append(
                Note(
                    index=idx,
                    start_timing_msec=start,
                    end_timing_msec=end,
                    gate_time_msec=end - start,
                    scale_piano=pitch,
                    min_key_index=min_key_index,
                    max_key_index=max_key_index,
                    note_type=note_type,
                    hand=hand,
                    key_kind=key_kind,
                    sub_notes=[sub_note],
                )
            )

        notes.sort(key=lambda n: (n.start_timing_msec, n.scale_piano))
        return notes

    def _analyze_velocity_zones(self, raw_notes: List[Dict[str, int]]) -> None:
        self.velocity_zones = []
        if not raw_notes:
            return

        all_velocities = [note["velocity"] for note in raw_notes if note["velocity"] > 0]
        if not all_velocities:
            return

        global_avg = sum(all_velocities) / len(all_velocities)
        threshold = max(4.0, global_avg * 0.10)

        window_size = 2000  # ms
        step = 1000  # ms
        max_time = max(note["end_timing_msec"] for note in raw_notes)

        current_start: Optional[int] = None
        current_type: Optional[int] = None
        zone_index = 0

        for t in range(0, max_time + step, step):
            window_start = max(0, t - window_size)
            window_end = t + window_size

            window_notes = [
                note
                for note in raw_notes
                if note["start_timing_msec"] <= window_end
                and note["end_timing_msec"] >= window_start
            ]

            if not window_notes:
                if current_start is not None:
                    self.velocity_zones.append(
                        VelocityZone(
                            index=zone_index,
                            start_timing_msec=current_start,
                            end_timing_msec=t,
                            velocity_type=current_type or 0,
                        )
                    )
                    zone_index += 1
                    current_start = None
                    current_type = None
                continue

            avg_velocity = sum(note["velocity"] for note in window_notes) / len(window_notes)

            if avg_velocity > global_avg + threshold:
                zone_type: Optional[int] = 1
            elif avg_velocity < global_avg - threshold:
                zone_type = 0
            else:
                zone_type = None

            if zone_type is None:
                if current_start is not None:
                    self.velocity_zones.append(
                        VelocityZone(
                            index=zone_index,
                            start_timing_msec=current_start,
                            end_timing_msec=t,
                            velocity_type=current_type or 0,
                        )
                    )
                    zone_index += 1
                    current_start = None
                    current_type = None
                continue

            if current_start is None:
                current_start = t
                current_type = zone_type
            elif current_type != zone_type:
                self.velocity_zones.append(
                    VelocityZone(
                        index=zone_index,
                        start_timing_msec=current_start,
                        end_timing_msec=t,
                        velocity_type=current_type,
                    )
                )
                zone_index += 1
                current_start = t
                current_type = zone_type

        if current_start is not None:
            self.velocity_zones.append(
                VelocityZone(
                    index=zone_index,
                    start_timing_msec=current_start,
                    end_timing_msec=max_time,
                    velocity_type=current_type or 0,
                )
            )
        if not self.velocity_zones:
            self.velocity_zones.append(
                VelocityZone(
                    index=0,
                    start_timing_msec=0,
                    end_timing_msec=max_time,
                    velocity_type=1 if global_avg >= 80 else 0,
                )
            )

    def _map_pitch_to_index(self, pitch: int) -> int:
        # 預設用本 MIDI 實際音域（pitch_lo~pitch_hi）平均分配；未計算時退回滿量程 21~108。
        lo = self.pitch_lo if self.pitch_lo is not None else self.MIN_MIDI_PITCH
        hi = self.pitch_hi if self.pitch_hi is not None else self.MAX_MIDI_PITCH
        span = hi - lo
        if span <= 0:
            # 全部同一音高（或無資料）→ 置中
            return self.TARGET_KEYS // 2
        normalized = (pitch - lo) / span
        normalized = max(0.0, min(1.0, normalized))
        raw_index = round(normalized * (self.TARGET_KEYS - 1))
        return int(max(0, min(self.TARGET_KEYS - 1, raw_index)))

    def _compute_width_three_range(self, preferred_center: int) -> Tuple[int, int]:
        center = max(0, min(self.TARGET_KEYS - 1, preferred_center))
        min_key = center - 1
        max_key = center + 1
        if min_key < 0:
            min_key = 0
            max_key = min(self.TARGET_KEYS - 1, min_key + 2)
        if max_key >= self.TARGET_KEYS:
            max_key = self.TARGET_KEYS - 1
            min_key = max(0, max_key - 2)
        if max_key - min_key < 2:
            if min_key == 0:
                max_key = min(self.TARGET_KEYS - 1, min_key + 2)
            else:
                min_key = max(0, max_key - 2)
        return min_key, max_key

    def _determine_key_kind(self, key_index: int) -> int:
        if key_index < 9:
            return 0
        if key_index < 18:
            return 1
        return 2

    def _resolve_overlaps(self, notes: List[Note]) -> None:
        active: List[Note] = []
        for note in notes:
            # Remove notes that are no longer active
            active = [n for n in active if n.end_timing_msec > note.start_timing_msec]
            used_ranges = [(n.min_key_index, n.max_key_index) for n in active]

            # Check if the current note overlaps with any active note
            if self._range_overlaps(note.min_key_index, note.max_key_index, used_ranges):
                # Move the note to avoid overlap, but keep the hand assignment
                note.min_key_index, note.max_key_index = self._find_available_range(
                    (note.min_key_index + note.max_key_index) // 2, used_ranges
                )

            # Add the current note to the active list
            active.append(note)

    def _range_overlaps(self, min_key: int, max_key: int, used_ranges: List[Tuple[int, int]]) -> bool:
        for used_min, used_max in used_ranges:
            if min_key <= used_max and max_key >= used_min:
                return True
        return False

    def _find_available_range(
        self, preferred_center: int, used_ranges: List[Tuple[int, int]]
    ) -> Tuple[int, int]:
        visited_centers: set[int] = set()
        for delta in range(self.TARGET_KEYS):
            candidates: Tuple[int, ...]
            if delta == 0:
                candidates = (preferred_center,)
            else:
                candidates = (preferred_center - delta, preferred_center + delta)
            for center in candidates:
                if center in visited_centers:
                    continue
                visited_centers.add(center)
                if center < 0 or center >= self.TARGET_KEYS:
                    continue
                min_candidate, max_candidate = self._compute_width_three_range(center)
                if not self._range_overlaps(min_candidate, max_candidate, used_ranges):
                    return min_candidate, max_candidate
        fallback_center = max(1, min(self.TARGET_KEYS - 2, preferred_center))
        return self._compute_width_three_range(fallback_center)

    # ------------------------------------------------------------------
    # XML output helpers

    def _write_xml(self, output_path: str) -> None:
        root = ET.Element("music_score")
        header = ET.SubElement(root, "header")

        max_scale = max((note.scale_piano for note in self.notes), default=self.MAX_MIDI_PITCH)
        min_scale = min((note.scale_piano for note in self.notes), default=self.MIN_MIDI_PITCH)
        total_finish = max((note.end_timing_msec for note in self.notes), default=0)

        self._add_xml_element(header, "max_scale", _midi_to_official_scale(max_scale), "s32")
        self._add_xml_element(header, "min_scale", _midi_to_official_scale(min_scale), "s32")
        self._add_xml_element(header, "file_version", 1, "s16")
        self._add_xml_element(header, "first_bpm", self.first_bpm or 0, "s64")
        self._add_xml_element(header, "music_finish_time_msec", total_finish, "s32")
        self._add_xml_element(header, "time_signature_numerator", self.time_signature_numerator, "s32")
        self._add_xml_element(header, "time_signature_denominator", self.time_signature_denominator, "s32")
        # Add a human-friendly time_signature string (e.g. "6/8") for consumers that expect a single field
        try:
            ts_str = f"{self.time_signature_numerator}/{self.time_signature_denominator}"
            self._add_xml_element(header, "time_signature", ts_str, "str")
        except Exception:
            pass

        note_data = ET.SubElement(root, "note_data")
        for note in self.notes:
            note_elem = ET.SubElement(note_data, "note")
            self._add_xml_element(note_elem, "index", note.index, "s32")
            self._add_xml_element(note_elem, "start_timing_msec", note.start_timing_msec, "s32")
            self._add_xml_element(note_elem, "end_timing_msec", note.end_timing_msec, "s32")
            self._add_xml_element(note_elem, "gate_time_msec", note.gate_time_msec, "s32")
            self._add_xml_element(note_elem, "scale_piano", _midi_to_official_scale(note.scale_piano), "u8")
            self._add_xml_element(note_elem, "min_key_index", note.min_key_index, "s32")
            self._add_xml_element(note_elem, "max_key_index", note.max_key_index, "s32")
            self._add_xml_element(note_elem, "note_type", note.note_type, "s32")
            self._add_xml_element(note_elem, "hand", note.hand, "s32")
            self._add_xml_element(note_elem, "key_kind", note.key_kind, "s32")
            self._add_xml_element(note_elem, "param1", getattr(note, "param1", 0), "s32")
            self._add_xml_element(note_elem, "param2", getattr(note, "param2", 0), "s32")
            self._add_xml_element(note_elem, "param3", getattr(note, "param3", 0), "s32")

            measure_index = self._estimate_measure_index(note.start_timing_msec)
            self._add_xml_element(note_elem, "measure_index", measure_index, "s32")

            sub_data = ET.SubElement(note_elem, "sub_note_data")
            for sub_note in note.sub_notes:
                sub_elem = ET.SubElement(sub_data, "sub_note")
                self._add_xml_element(sub_elem, "start_timing_msec", sub_note.start_timing_msec, "s32")
                self._add_xml_element(sub_elem, "end_timing_msec", sub_note.end_timing_msec, "s32")
                self._add_xml_element(sub_elem, "scale_piano", _midi_to_official_scale(sub_note.scale_piano), "u8")
                self._add_xml_element(sub_elem, "velocity", sub_note.velocity, "u8")
                self._add_xml_element(sub_elem, "track_index", sub_note.track, "s32")

        beat_data = ET.SubElement(root, "beat_data")
        for idx, beat_time in enumerate(self._generate_beat_times()):
            beat_elem = ET.SubElement(beat_data, "beat")
            self._add_xml_element(beat_elem, "index", idx, "s32")
            self._add_xml_element(beat_elem, "start_timing_msec", beat_time, "s32")

        # time_signature_changes: every MIDI time_sig event converted to ms
        ts_changes_root = ET.SubElement(root, "time_signature_changes")
        for tick, num, den in sorted(self.time_signature_events_ticks, key=lambda x: x[0]):
            ms = int(round(self._ticks_to_ms(tick, self.tempo_map_ticks)))
            ch_elem = ET.SubElement(ts_changes_root, "ts_change")
            self._add_xml_element(ch_elem, "start_timing_msec", ms, "s32")
            self._add_xml_element(ch_elem, "numerator", num, "s32")
            self._add_xml_element(ch_elem, "denominator", den, "s32")

        track_info = ET.SubElement(root, "track_info")
        for idx in range(1, 4):
            track_elem = ET.SubElement(track_info, "track")
            self._add_xml_element(track_elem, "index", idx, "s32")
            self._add_xml_element(track_elem, "name", "key_apiano1", "str")

        if self.velocity_zones:
            velocity_zone_data = ET.SubElement(root, "velocity_zone_data")
            for zone in self.velocity_zones:
                zone_elem = ET.SubElement(velocity_zone_data, "velocity_zone")
                self._add_xml_element(zone_elem, "index", zone.index, "s32")
                self._add_xml_element(zone_elem, "start_timing_msec", zone.start_timing_msec, "s32")
                self._add_xml_element(zone_elem, "end_timing_msec", zone.end_timing_msec, "s32")
                self._add_xml_element(zone_elem, "velocity_type", zone.velocity_type, "s32")

        xml_str = ET.tostring(root, encoding="unicode")
        dom = xml.dom.minidom.parseString(xml_str)
        formatted = dom.toprettyxml(indent="  ")
        lines = [line for line in formatted.splitlines() if line.strip()]
        Path(output_path).write_text("\n".join(lines), encoding="utf-8")

    def _estimate_measure_index(self, start_ms: int) -> int:
        if not self.first_bpm or self.first_bpm <= 0:
            return 0
        beats_per_bar = max(1, self.time_signature_numerator)
        ms_per_beat = 60_000.0 / float(self.first_bpm)
        ms_per_bar = ms_per_beat * beats_per_bar
        if ms_per_bar <= 0:
            return 0
        return int(start_ms // ms_per_bar)

    def _add_xml_element(self, parent: ET.Element, tag: str, value, type_attr: str) -> ET.Element:
        elem = ET.SubElement(parent, tag)
        elem.set("__type", type_attr)
        elem.text = str(value)
        return elem

    def _generate_beat_times(self) -> List[int]:
        if not self.tempo_map_ticks:
            # fallback: use existing ms-based logic with total duration if tempo map missing
            total_finish = max((note.end_timing_msec for note in self.notes), default=0)
            if total_finish <= 0:
                return [0]
            return list(range(0, total_finish + 1, int(max(1, 60_000 // max(1, self.first_bpm or 120)))))

        events = (
            sorted(self.time_signature_events_ticks, key=lambda x: x[0])
            if self.time_signature_events_ticks
            else [(0, self.time_signature_numerator, self.time_signature_denominator)]
        )

        cleaned_events: List[Tuple[int, int, int]] = []
        for tick, num, den in events:
            if cleaned_events and cleaned_events[-1][0] == tick:
                cleaned_events[-1] = (tick, num, den)
            else:
                cleaned_events.append((tick, num, den))

        if not cleaned_events:
            cleaned_events = [(0, self.time_signature_numerator, self.time_signature_denominator)]

        if cleaned_events[0][0] != 0:
            first_num, first_den = cleaned_events[0][1], cleaned_events[0][2]
            cleaned_events.insert(0, (0, first_num, first_den))

        total_ticks = max(self.song_end_tick, cleaned_events[-1][0], 0)
        if total_ticks <= 0:
            return [0]

        beat_times: List[int] = []
        idx = 0
        tick = cleaned_events[0][0]

        def compute_measure_ticks(numerator: int, denominator: int) -> int:
            numerator = max(1, numerator)
            denominator = max(1, denominator)
            base = self.ticks_per_beat * 4
            beat_ticks = base // denominator
            if beat_ticks == 0:
                beat_ticks = int(round(base / denominator))
            if beat_ticks <= 0:
                beat_ticks = self.ticks_per_beat
            measure_ticks = beat_ticks * numerator
            return max(1, measure_ticks)

        current_num = cleaned_events[0][1]
        current_den = cleaned_events[0][2]
        measure_ticks = compute_measure_ticks(current_num, current_den)

        def next_change_tick(index: int) -> Optional[int]:
            return cleaned_events[index + 1][0] if index + 1 < len(cleaned_events) else None

        pending_change = next_change_tick(idx)

        tick = max(0, tick)
        while tick <= total_ticks:
            ms = int(round(self._ticks_to_ms(int(tick), self.tempo_map_ticks)))
            if not beat_times or ms != beat_times[-1]:
                beat_times.append(ms)

            if pending_change is not None and tick + measure_ticks >= pending_change:
                tick = pending_change
                idx += 1
                current_num = cleaned_events[idx][1]
                current_den = cleaned_events[idx][2]
                measure_ticks = compute_measure_ticks(current_num, current_den)
                pending_change = next_change_tick(idx)
                continue

            tick += measure_ticks

        return beat_times


def main() -> None:
    converter = MIDIToXMLConverter()
    midi_folder = Path("UnityProject/nos_clone/Assets/Resources/songs/MarbleBlue/raw")
    output_folder = Path("UnityProject/nos_clone/Assets/Resources/songs/MarbleBlue/raw")
    output_folder.mkdir(exist_ok=True)

    midi_files = sorted(midi_folder.glob("MarbleBlue.mid"))
    if not midi_files:
        print("未找到 MIDI 檔案")
        return

    test_file = midi_files[0]
    output_file = output_folder / f"{test_file.stem}.xml"
    try:
        converter.convert_midi_to_xml(str(test_file), str(output_file))
    except Exception as exc:
        print(f"轉換失敗: {exc}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
