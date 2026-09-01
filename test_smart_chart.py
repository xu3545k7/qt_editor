import unittest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import mido
from qt_editor.models import GNote, NoteModel
from qt_editor.midi_to_xml_converter import MIDIToXMLConverter
from qt_editor.smart_chart import SmartChartSettings, arrange_midi_notes


def note(start, pitch, hand=0, track=0, duration=100, index=0):
    result = GNote(None, index)
    result.start = start
    result.end = start + duration
    result.gate = duration
    result.pitch = pitch
    result.hand = hand
    result.track = track
    result.min_key = 0
    result.max_key = 2
    return result


def lane_note(start, pitch, centre, hand=0, track=0, duration=100, index=0):
    """帶真實鍵道位置的音符。分手要看鍵道跨度，`note()` 那種 0~2 量不出東西。"""
    result = note(start, pitch, hand=hand, track=track, duration=duration, index=index)
    result.min_key = centre - 1
    result.max_key = centre + 1
    return result


def stats_width_one(notes):
    return sum(1 for n in notes if n.max_key - n.min_key + 1 <= 1)


class SmartChartTests(unittest.TestCase):
    def test_keeps_every_note_and_preserves_midi_data(self):
        notes = [
            note(0, 48, track=1, duration=800, index=0),
            note(0, 72, track=2, duration=120, index=1),
            note(300, 76, track=2, duration=90, index=2),
        ]
        before = [(n.start, n.end, n.pitch, n.note_type) for n in notes]
        stats = arrange_midi_notes(notes)
        self.assertEqual(stats.notes, 3)
        self.assertEqual(
            [(n.start, n.end, n.pitch, n.note_type) for n in notes],
            before,
        )

    def test_close_notes_follow_pitch_from_left_to_right(self):
        notes = [
            note(1000, 72, track=1, index=0),
            note(1002, 48, track=1, index=1),
            note(1005, 60, track=1, index=2),
        ]
        stats = arrange_midi_notes(notes)
        ordered = sorted(notes, key=lambda n: n.pitch)
        centers = [(n.min_key + n.max_key) / 2 for n in ordered]
        self.assertEqual(centers, sorted(centers))
        self.assertEqual(stats.pitch_order_violations, 0)
        self.assertEqual(stats.unresolved_overlaps, 0)

    def test_two_hands_do_not_overlap_in_close_group(self):
        notes = [
            note(1000, 43, track=1, index=0),
            note(1003, 52, track=1, index=1),
            note(1001, 67, track=2, index=2),
            note(1004, 76, track=2, index=3),
        ]
        arrange_midi_notes(notes)
        left = [n for n in notes if n.hand == 1]
        right = [n for n in notes if n.hand == 0]
        self.assertLess(max(n.max_key for n in left), min(n.min_key for n in right))

    def test_simultaneous_octave_centers_are_at_most_five_lanes_apart(self):
        """**同時發聲**的同手八度，中心距上限 5 格。

        官方 real 量到 ≥8 半音一律 5 格（8~24 半音全部都是 5，n=60600），
        而且是**平的**——不隨鍵盤位置或上下文改變。前後相接的八度是另一
        條規則（見下一個測試），不適用這個上限。
        """
        notes = [
            note(0, 60, track=2, index=0),
            note(0, 72, track=2, index=1),
            note(500, 36, track=1, index=2),
        ]
        arrange_midi_notes(notes)
        low_center = (notes[0].min_key + notes[0].max_key) / 2.0
        high_center = (notes[1].min_key + notes[1].max_key) / 2.0
        self.assertLessEqual(high_center - low_center, 5.0)

    def test_consecutive_octave_is_not_capped_at_five(self):
        """**前後相接**的八度不套那個上限。

        官方 real 的前後同手八度中心距中位數 5.0、平均 5.28，6 格是常見值。
        這裡只釘「方向正確而且不會誇張到跨越大半個鍵盤」。
        """
        notes = [
            note(0, 60, track=2, index=0),
            note(100, 72, track=2, index=1),
            note(500, 36, track=1, index=2),
        ]
        arrange_midi_notes(notes)
        low_center = (notes[0].min_key + notes[0].max_key) / 2.0
        high_center = (notes[1].min_key + notes[1].max_key) / 2.0
        self.assertGreater(high_center, low_center)
        self.assertLessEqual(high_center - low_center, 9.0)

    def test_nearby_lower_pitch_reserves_space_on_the_left(self):
        near = [
            note(0, 60, track=2, index=0),
            note(0, 72, track=2, index=1),
            note(500, 36, track=1, index=2),
            note(5000, 100, track=2, index=3),
        ]
        far = [
            note(0, 60, track=2, index=0),
            note(0, 72, track=2, index=1),
            note(5000, 36, track=1, index=2),
            note(5000, 100, track=2, index=3),
        ]
        arrange_midi_notes(near)
        arrange_midi_notes(far)
        # 上下文影響的是整組**擺在哪裡**，不是和絃**內部撐多開** ——
        # 同時發聲的同手和絃內部距離是平的上限（見
        # test_simultaneous_octave_centers_are_at_most_five_lanes_apart）。
        for chart in (near, far):
            low, high = chart[0], chart[1]
            span = ((high.min_key + high.max_key)
                    - (low.min_key + low.max_key)) / 2.0
            self.assertLessEqual(span, 5.0)
        near_center = sum((n.min_key + n.max_key) / 2 for n in near[:2]) / 2
        far_center = sum((n.min_key + n.max_key) / 2 for n in far[:2]) / 2
        self.assertNotEqual(near_center, far_center)

    def test_nearby_pitch_extremes_compress_current_group(self):
        with_extremes = [
            note(0, 60, track=2, index=0),
            note(0, 84, track=2, index=1),
            note(450, 36, track=1, index=2),
            note(500, 108, track=2, index=3),
        ]
        isolated = [
            note(0, 60, track=2, index=0),
            note(0, 84, track=2, index=1),
            note(5000, 36, track=1, index=2),
            note(5100, 108, track=2, index=3),
        ]
        arrange_midi_notes(with_extremes)
        arrange_midi_notes(isolated)
        compressed_span = abs(
            (with_extremes[1].min_key + with_extremes[1].max_key)
            - (with_extremes[0].min_key + with_extremes[0].max_key)
        ) / 2
        isolated_span = abs(
            (isolated[1].min_key + isolated[1].max_key)
            - (isolated[0].min_key + isolated[0].max_key)
        ) / 2
        # 兩邊都被同一條「平的上限」夾住，所以內部跨度相同才是對的；
        # 上下文的影響體現在整組的位置，不在和絃內部。
        self.assertEqual(compressed_span, isolated_span)
        self.assertLessEqual(compressed_span, 5.0)

    def test_isolated_chart_high_pitch_is_anchored_to_right_edge(self):
        notes = [
            note(0, 97, track=2, index=0),
            note(5000, 40, track=1, index=1),
            note(5000, 100, track=2, index=2),
        ]
        stats = arrange_midi_notes(notes)
        self.assertEqual(notes[0].max_key, 27)
        self.assertGreater(stats.global_edge_anchored_groups, 0)

    def test_global_high_note_is_not_recentered_by_local_window(self):
        notes = [
            note(0, 93, track=2, index=0),
            note(5000, 24, track=1, index=1),
            note(5000, 106, track=2, index=2),
        ]
        arrange_midi_notes(notes)
        center = (notes[0].min_key + notes[0].max_key) / 2.0
        self.assertGreater(center, 18.0)

    def test_local_high_peak_is_anchored_right_with_later_chart_high(self):
        notes = [
            note(0, 81, track=2, index=0),
            note(300, 93, track=2, index=1),
            note(600, 88, track=2, index=2),
            note(5000, 24, track=1, index=3),
            note(5000, 106, track=2, index=4),
        ]
        arrange_midi_notes(notes)
        self.assertEqual(notes[1].max_key, 27)

    def test_nearby_groups_never_reverse_pitch_order(self):
        notes = [
            note(0, 66, track=2, index=0),
            note(117, 64, track=2, index=1),
            note(233, 63, track=2, index=2),
            note(349, 64, track=2, index=3),
            note(466, 66, track=2, index=4),
            note(3000, 24, track=1, index=5),
            note(3000, 106, track=2, index=6),
        ]
        stats = arrange_midi_notes(notes)
        self.assertEqual(stats.pitch_order_violations, 0)
        ordered = sorted(notes[:5], key=lambda item: item.start)
        for first, second in zip(ordered, ordered[1:]):
            first_center = (first.min_key + first.max_key) / 2.0
            second_center = (second.min_key + second.max_key) / 2.0
            self.assertGreaterEqual(
                (first.pitch - second.pitch)
                * (first_center - second_center),
                0,
            )

    def test_macro_chord_contour_survives_local_packing(self):
        notes = [
            note(0, 56, track=1, index=0),
            note(0, 63, track=2, index=1),
            note(0, 68, track=2, index=2),
            note(116, 51, track=1, index=3),
            note(116, 68, track=2, index=4),
            note(232, 54, track=1, index=5),
            note(232, 66, track=2, index=6),
            note(348, 54, track=1, index=7),
            note(348, 70, track=2, index=8),
            note(464, 66, track=1, index=9),
            note(464, 71, track=2, index=10),
            note(3000, 24, track=1, index=11),
            note(3000, 106, track=2, index=12),
        ]
        stats = arrange_midi_notes(notes)
        right = sorted(
            [item for item in notes[:11] if item.hand == 0],
            key=lambda item: item.start,
        )
        events = {}
        for item in right:
            events.setdefault(item.start, []).append(item)
        contour = []
        for when, event_notes in sorted(events.items()):
            pitches = sorted(item.pitch for item in event_notes)
            centers = sorted(
                (item.min_key + item.max_key) / 2.0
                for item in event_notes
            )
            mid = len(pitches) // 2
            pitch_center = (
                pitches[mid]
                if len(pitches) % 2
                else (pitches[mid - 1] + pitches[mid]) / 2.0
            )
            lane_center = (
                centers[mid]
                if len(centers) % 2
                else (centers[mid - 1] + centers[mid]) / 2.0
            )
            contour.append((when, pitch_center, lane_center))
        for first, second in zip(contour, contour[1:]):
            if abs(second[1] - first[1]) >= 3:
                self.assertGreaterEqual(
                    (second[1] - first[1]) * (second[2] - first[2]),
                    0,
                )
        self.assertEqual(stats.macro_trend_violations, 0)

    def test_two_track_midi_keeps_the_hands_the_file_declares(self):
        """來源 MIDI 剛好兩軌時，那兩軌就是左右手，排譜器不准改。

        實測這個曲庫 18 個來源 MIDI 有 16 個是兩軌，人工譜的分手就是照音軌
        走的；排譜器再去動它只會製造 0~2% 的分歧。所以就算一隻手同時four音
        （官方會拆成 2/2），只要音軌講明了就照著走。
        """
        notes = [note(0, 60 + i * 2, track=2, index=i) for i in range(4)]
        notes.append(note(500, 36, track=1, index=4))
        arrange_midi_notes(notes, SmartChartSettings(minimum_pitch_span=18))
        self.assertEqual({int(n.hand) for n in notes[:4]}, {0})
        self.assertEqual(int(notes[4].hand), 1)

    def test_four_simultaneous_notes_are_split_between_hands(self):
        """同時 4 音不會全塞給一隻手：官方 51% 排 2/2、44% 排 3/1，4/0 只有 2%。

        `max_hand_chord_notes` 的上限是 max(3, ceil(N/2))，所以四音一定拆開。
        拆開之後每隻手只有兩顆、跨度又短，照「度數短又有空間就不收窄」的
        規則就維持寬度 3 —— 這和舊版「四音同手一律收窄成 2」是同一件事的
        兩端，差別在分手先發生。
        """
        # 音軌不是「剛好兩軌」時（這裡刻意關掉信任），分手是猜的，才輪到
        # 官方的平均分配規則接手。
        notes = [note(0, 60 + i * 2, track=2, index=i) for i in range(4)]
        notes.append(note(500, 36, track=1, index=4))
        settings = SmartChartSettings(
            minimum_pitch_span=18, trust_two_track_hands=False
        )
        arrange_midi_notes(notes, settings)
        per_hand = {}
        for item in notes[:4]:
            per_hand[item.hand] = per_hand.get(item.hand, 0) + 1
        self.assertLessEqual(max(per_hand.values()), 3)
        self.assertEqual(sum(per_hand.values()), 4)

    def test_initial_widths_narrow_a_four_note_hand(self):
        """真的把四顆放在同一隻手時，寬度規則仍然收窄成 2。"""
        from qt_editor.smart_chart import _initial_hand_widths

        settings = SmartChartSettings()
        hand_notes = [note(0, 60 + i * 2, index=i) for i in range(4)]
        self.assertEqual(
            _initial_hand_widths(hand_notes, settings, False),
            [settings.dense_width] * 4,
        )

    def test_small_interval_cue_never_widens_past_the_difficulty_width(self):
        """寬度是難度常數：官方 real 97% 是 3，沒有一顆比 3 寬。

        小度數修復在「收窄不被允許」時會改用加寬，而加寬原本沒有上限——四輪
        疊起來實測長到 7。這裡兩顆前後相鄰、音高差 2 但中心相同的音符，正是
        會走到那條路的最小案例：兩顆都不在密和絃裡，所以收窄被擋，只剩加寬。
        """
        from qt_editor.smart_chart import (
            _clusters, _prime_pitch_cache, _repair_small_interval_width_cues,
        )

        settings = SmartChartSettings()
        high = note(0, 62, index=0)
        high.min_key, high.max_key = 12, 14
        low = note(120, 60, index=1)
        low.min_key, low.max_key = 12, 14      # 中心和 high 相同 → 度數看不出來
        notes = [high, low]
        _prime_pitch_cache(notes)
        try:
            _repair_small_interval_width_cues(
                notes, _clusters(notes, settings.onset_tolerance_ms), settings
            )
        finally:
            _prime_pitch_cache(notes, False)
        self.assertEqual(
            [n.max_key - n.min_key + 1 for n in notes],
            [settings.normal_width, settings.normal_width],
        )

    def test_top_edge_repair_skips_pairs_that_are_already_ordered(self):
        """違規清單是每輪開頭算的，中途音符被改動後可能已經順了。

        那時 `deficit` 會是 0 或負數，而修復寫的是 `old_max - deficit` ——
        減掉負數就是加寬。實測 8 首曲子少了這道守門還會剩 4 顆過寬的音符。
        """
        from qt_editor.smart_chart import (
            _clusters, _prime_pitch_cache, _repair_top_edge_order,
        )

        settings = SmartChartSettings()
        low = note(0, 60, index=0)
        low.min_key, low.max_key = 10, 12
        high = note(120, 64, index=1)
        high.min_key, high.max_key = 20, 22    # 已經在右邊，這一對不該被動
        notes = [low, high]
        _prime_pitch_cache(notes)
        try:
            _repair_top_edge_order(
                notes, _clusters(notes, settings.onset_tolerance_ms), settings
            )
        finally:
            _prime_pitch_cache(notes, False)
        self.assertEqual([(n.min_key, n.max_key) for n in notes],
                         [(10, 12), (20, 22)])

    def test_width_clamp_keeps_the_right_edge(self):
        """收尾夾寬度時收左緣：右緣是排序與視覺的權威，動它會推翻前面的結論。"""
        from qt_editor.smart_chart import _clamp_note_widths

        settings = SmartChartSettings()
        wide = note(0, 60, index=0)
        wide.min_key, wide.max_key = 10, 16
        narrow = note(0, 64, index=1)
        narrow.min_key, narrow.max_key = 20, 21
        self.assertEqual(_clamp_note_widths([wide, narrow], settings), 1)
        self.assertEqual((wide.min_key, wide.max_key), (14, 16))
        self.assertEqual((narrow.min_key, narrow.max_key), (20, 21))

    def test_close_notes_in_sub_octave_chord_are_narrowed(self):
        notes = [
            note(0, 63, track=2, index=0),
            note(0, 65, track=2, index=1),
            note(0, 70, track=2, index=2),
            note(1000, 36, track=1, index=3),
        ]
        arrange_midi_notes(notes)
        widths = {
            item.pitch: item.max_key - item.min_key + 1
            for item in notes[:3]
        }
        # 跨度 70-63 = 7 半音，落在收窄帶（7~11）→ 整組一起收成寬度 2。
        # 只收「相鄰小度數」那兩顆的話，整組還是佔 3+2+2=7 格上下，和跨了
        # 一個八度的和絃分不出來。
        self.assertEqual(widths[63], 2)
        self.assertEqual(widths[65], 2)
        self.assertEqual(widths[70], 2)

    def test_short_span_chord_with_room_is_not_narrowed(self):
        """度數短又有空間就不收窄 —— 那種用位置排得開。"""
        notes = [
            note(0, 60, track=2, index=0),
            note(0, 62, track=2, index=1),
            note(0, 64, track=2, index=2),
            note(1000, 36, track=1, index=3),
        ]
        arrange_midi_notes(notes)
        self.assertEqual(
            [n.max_key - n.min_key + 1 for n in notes[:3]],
            [3, 3, 3],
        )

    def test_close_pair_in_wide_chord_keeps_normal_width(self):
        notes = [
            note(0, 48, track=2, index=0),
            note(0, 63, track=2, index=1),
            note(0, 65, track=2, index=2),
            note(1000, 36, track=1, index=3),
        ]
        arrange_midi_notes(notes)
        self.assertEqual(
            [item.max_key - item.min_key + 1 for item in notes[:3]],
            [3, 3, 3],
        )

    def test_same_hand_chord_tops_have_strict_right_edge_order(self):
        notes = [
            note(0, 63, track=2, index=0),
            note(0, 65, track=2, index=1),
            note(0, 70, track=2, index=2),
            note(117, 72, track=2, index=3),
            note(234, 68, track=2, index=4),
            note(3000, 36, track=1, index=5),
            note(3000, 96, track=2, index=6),
        ]
        stats = arrange_midi_notes(notes)
        tops = [notes[2], notes[3], notes[4]]
        for first, second in zip(tops, tops[1:]):
            if first.pitch < second.pitch:
                self.assertLess(first.max_key, second.max_key)
            else:
                self.assertGreater(first.max_key, second.max_key)
        self.assertEqual(stats.top_edge_violations, 0)

    def test_midi_articulations_use_dynamic_hand_rhythm_distribution(self):
        notes = [
            note(0, 60, track=2, duration=1600, index=0),
            note(1800, 64, track=2, duration=50, index=1),
            note(2000, 67, track=2, duration=80, index=2),
            note(2400, 72, track=2, duration=100, index=3),
            note(3000, 36, track=1, duration=100, index=4),
        ]
        notes[2].velocity = 30
        # soft / staccato 預設不自動寫（見
        # test_expression_marks_are_not_written_by_default），這裡是在驗
        # 判斷邏輯本身，所以把旗標打開。
        settings = SmartChartSettings(
            beat_ms=600,
            classify_articulations=True,
            classify_staccato=True,
            classify_soft=True,
        )
        arrange_midi_notes(notes, settings)
        self.assertEqual(notes[0].note_type, 2)     # 長 → hold
        self.assertEqual(notes[2].note_type, 1)     # 力度 30 → soft
        # notes[1] 短，但 200ms 後就有下一顆同手音 —— 那是快速樂句，不是
        # 斷奏，所以維持 tap（`staccato_gap_ratio`：斷奏還要求「下一顆同手
        # 音很遠」）。notes[3] 後面沒有同手音了，算樂句結尾 → staccato。
        self.assertEqual(notes[1].note_type, 0)
        self.assertEqual(notes[3].note_type, 3)

    def test_hold_judgment_subtracts_next_same_hand_spacing(self):
        notes = [
            note(0, 60, duration=2000, index=0),
            note(100, 64, duration=50, index=1),
            note(300, 67, duration=50, index=2),
            note(500, 69, duration=50, index=3),
        ]
        arrange_midi_notes(
            notes,
            SmartChartSettings(classify_articulations=True),
        )
        self.assertNotEqual(notes[0].note_type, 2)

    def test_fast_monotone_scale_becomes_slide_chain(self):
        notes = [
            note(index * 80, 60 + index, track=2, duration=30, index=index)
            for index in range(5)
        ]
        notes.append(note(1000, 36, track=1, index=5))
        stats = arrange_midi_notes(
            notes,
            SmartChartSettings(
                classify_articulations=True, classify_slide=True
            ),
        )
        self.assertEqual([item.note_type for item in notes[:5]], [4] * 5)
        self.assertEqual(notes[0].param1, -1)
        self.assertEqual(notes[4].param2, -1)
        self.assertEqual(stats.slide_notes, 5)

    def test_fast_alternation_is_detected_without_deleting_notes(self):
        notes = [
            note(index * 70, 60 + index % 2 * 2, track=2, duration=30, index=index)
            for index in range(8)
        ]
        notes.append(note(1000, 36, track=1, index=8))
        stats = arrange_midi_notes(
            notes,
            SmartChartSettings(classify_articulations=True),
        )
        self.assertEqual(len(notes), 9)
        self.assertEqual(stats.trill_patterns, 1)

    def test_later_same_hand_note_avoids_active_hold_corridor(self):
        notes = [
            note(0, 72, track=2, duration=1000, index=0),
            note(300, 72, track=2, duration=100, index=1),
            note(1200, 36, track=1, duration=100, index=2),
        ]
        notes[0].note_type = 2
        stats = arrange_midi_notes(notes)
        hold, later = notes[:2]
        self.assertTrue(
            later.max_key < hold.min_key or later.min_key > hold.max_key
        )
        self.assertEqual(stats.hold_corridor_conflicts, 0)

    def test_short_explicit_hold_also_blocks_later_start(self):
        notes = [
            note(0, 72, track=2, duration=300, index=0),
            note(150, 72, track=2, duration=80, index=1),
            note(800, 36, track=1, index=2),
        ]
        notes[0].note_type = 2
        stats = arrange_midi_notes(notes)
        self.assertEqual(stats.hold_corridor_conflicts, 0)
        self.assertTrue(
            notes[1].max_key < notes[0].min_key
            or notes[1].min_key > notes[0].max_key
        )

    def test_pedal_sustain_is_not_automatically_a_hold(self):
        notes = [
            note(0, 72, track=2, duration=8400, index=0),
            note(100, 76, track=2, duration=80, index=1),
            note(600, 36, track=1, index=2),
        ]
        arrange_midi_notes(
            notes,
            SmartChartSettings(classify_articulations=True),
        )
        self.assertNotEqual(notes[0].note_type, 2)
        self.assertGreater(notes[1].min_key, notes[0].max_key)

    def test_long_non_hold_note_orders_every_start_during_its_duration(self):
        notes = [
            note(0, 72, duration=8400, index=0),
            note(250, 84, duration=80, index=1),
            note(500, 60, duration=80, index=2),
        ]
        stats = arrange_midi_notes(notes)
        sustained, higher, lower = notes
        self.assertNotEqual(sustained.note_type, 2)
        self.assertGreater(higher.min_key, sustained.max_key)
        self.assertLess(lower.max_key, sustained.min_key)
        self.assertEqual(stats.hold_corridor_conflicts, 0)

    def test_long_note_orders_later_start_across_hands(self):
        notes = [
            note(0, 60, hand=1, duration=8400, index=0),
            note(300, 72, hand=0, duration=80, index=1),
        ]
        stats = arrange_midi_notes(notes)
        self.assertGreater(notes[1].min_key, notes[0].max_key)
        self.assertEqual(stats.hold_corridor_conflicts, 0)

    def test_small_nearby_pitch_step_gets_distinct_lane_centers(self):
        notes = [
            note(0, 63, track=2, index=0),
            note(117, 64, track=2, index=1),
            note(1000, 36, track=1, index=2),
            note(1000, 96, track=2, index=3),
        ]
        stats = arrange_midi_notes(notes)
        low_center = (notes[0].min_key + notes[0].max_key) / 2.0
        high_center = (notes[1].min_key + notes[1].max_key) / 2.0
        self.assertLess(low_center, high_center)
        self.assertEqual(stats.small_interval_unresolved, 0)

    def test_repeated_five_event_motif_reuses_layout(self):
        pitches = [60, 64, 67, 64, 60]
        notes = []
        for repeat, base in enumerate((0, 2000)):
            for offset, pitch in enumerate(pitches):
                notes.append(
                    note(
                        base + offset * 120,
                        pitch,
                        track=2,
                        index=len(notes),
                    )
                )
        notes.append(note(4000, 36, track=1, index=len(notes)))
        stats = arrange_midi_notes(notes)
        self.assertGreater(stats.motif_reuses, 0)

    def test_extreme_chord_overlaps_instead_of_shrinking_to_width_one(self):
        """20 顆同時音塞不進 28 格時的行為。

        舊版是壓成寬度 1 硬塞（20×1=20 格）。但人工譜與官方譜都沒有寬度 1
        的音符，`min_note_width=2` 之後 20×2=40 格本來就放不下 —— 官方在
        這種密度是**讓鍵道重疊**（k=5 有 72.6% 重疊、k=6 100%），不是縮成
        一格，更不是排到鍵盤外面去。所以這裡釘的是：一顆都不能少、全部留在
        0~27 之內、沒有任何寬度 1、音高順序不反轉。
        """
        notes = [note(0, 40 + i, track=1, index=i) for i in range(20)]
        arrange_midi_notes(notes)
        self.assertEqual(len(notes), 20)
        self.assertEqual(stats_width_one(notes), 0)
        self.assertTrue(all(0 <= n.min_key and n.max_key <= 27 for n in notes))
        ordered = sorted(notes, key=lambda n: n.pitch)
        edges = [n.max_key for n in ordered]
        self.assertEqual(edges, sorted(edges))

    def test_expression_marks_are_not_written_by_default(self):
        """soft / staccato / 滑音都不自動寫 —— 那是演奏詮釋，交給人判斷。

        hold 仍然自動寫（那是從 MIDI 長度直接看得出來的事實），trill 本來
        就只統計不寫入。
        """
        notes = [
            note(index * 80, 60 + index, track=2, duration=30, index=index)
            for index in range(6)
        ]
        notes.append(note(0, 36, track=1, duration=40, index=6))
        notes[-1].velocity = 20
        notes.append(note(2000, 48, track=1, duration=1800, index=7))
        stats = arrange_midi_notes(
            notes, SmartChartSettings(classify_articulations=True)
        )
        written = {int(item.note_type) for item in notes}
        self.assertNotIn(1, written)   # soft
        self.assertNotIn(3, written)   # staccato
        self.assertNotIn(4, written)   # slide
        self.assertEqual(stats.slide_notes, 0)

    def test_two_chart_styles_differ_where_they_should(self):
        """兩套風格是實測出來的兩種做法，不是鬆緊度的差別。

        官方靠「讓鍵道重疊」擠空間、只有單手同時 4 音才收窄、會自動標滑音；
        Eather 靠「收窄」擠空間（整組跨度 7~11 半音就收）、表情記號自己標。
        和絃內部幾何、分手上限、度數階梯表兩者共用 —— 那些是官方語料量出來
        的物理事實，手寫譜也遵守。
        """
        from qt_editor.smart_chart import (
            STYLE_EATHER, STYLE_OFFICIAL, settings_for_style,
        )

        def widths_for(style):
            notes = [
                note(0, 63, track=2, index=0),
                note(0, 67, track=2, index=1),
                note(0, 70, track=2, index=2),      # 跨度 7 半音
                note(1000, 36, track=1, index=3),
            ]
            arrange_midi_notes(notes, settings_for_style(style))
            return [n.max_key - n.min_key + 1 for n in notes[:3]]

        self.assertEqual(widths_for(STYLE_EATHER), [2, 2, 2])
        self.assertEqual(widths_for(STYLE_OFFICIAL), [3, 3, 3])

        def slides_for(style):
            notes = [
                note(index * 80, 60 + index, track=2, duration=30, index=index)
                for index in range(5)
            ]
            notes.append(note(1000, 36, track=1, index=5))
            stats = arrange_midi_notes(
                notes,
                settings_for_style(style, classify_articulations=True),
            )
            return stats.slide_notes

        self.assertEqual(slides_for(STYLE_EATHER), 0)
        self.assertEqual(slides_for(STYLE_OFFICIAL), 5)

    def test_unknown_style_falls_back_to_eather_style(self):
        from qt_editor.smart_chart import (
            normalise_style, settings_for_style, STYLE_EATHER,
        )

        self.assertEqual(
            settings_for_style('nonsense').narrow_span_min,
            settings_for_style(STYLE_EATHER).narrow_span_min,
        )
        # 舊設定檔存的是 'user'，要讀得懂，不能叫使用者重設。
        self.assertEqual(normalise_style('user'), STYLE_EATHER)
        self.assertEqual(normalise_style(None), STYLE_EATHER)
        self.assertEqual(
            settings_for_style('user').narrow_span_min,
            settings_for_style(STYLE_EATHER).narrow_span_min,
        )

    def test_model_entry_point_marks_chart_dirty(self):
        model = NoteModel()
        model.notes_tree = [note(0, 48, track=1), note(0, 72, track=2, index=1)]
        stats = model.smart_arrange_midi()
        self.assertEqual(stats.notes, 2)
        self.assertTrue(model.dirty)

    def test_midi_to_xml_automatically_uses_smart_arrangement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            midi_path = Path(temp_dir) / "edge.mid"
            xml_path = Path(temp_dir) / "edge.xml"
            midi = mido.MidiFile(ticks_per_beat=480)
            low_track = mido.MidiTrack()
            high_track = mido.MidiTrack()
            midi.tracks.extend([low_track, high_track])
            low_track.append(mido.Message("note_on", note=40, velocity=80, time=4800))
            low_track.append(mido.Message("note_off", note=40, velocity=0, time=120))
            high_track.append(mido.Message("note_on", note=97, velocity=90, time=0))
            high_track.append(mido.Message("note_off", note=97, velocity=0, time=120))
            high_track.append(mido.Message("note_on", note=100, velocity=90, time=4680))
            high_track.append(mido.Message("note_off", note=100, velocity=0, time=120))
            midi.save(midi_path)

            converter = MIDIToXMLConverter()
            converter.convert_midi_to_xml(str(midi_path), str(xml_path))
            root = ET.parse(xml_path).getroot()
            converted = {}
            for element in root.findall("./note_data/note"):
                pitch = int(element.findtext("scale_piano", "0"))
                converted[pitch] = (
                    int(element.findtext("min_key_index", "0")),
                    int(element.findtext("max_key_index", "0")),
                )
            # XML stores the official 1..88 piano scale (MIDI pitch - 20).
            self.assertEqual(converted[77][1], 27)
            self.assertIsNotNone(converter.smart_stats)
            self.assertEqual(converter.smart_stats.notes, 3)

    def test_opening_midi_automatically_uses_smart_arrangement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            midi_path = Path(temp_dir) / "auto.mid"
            midi = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            midi.tracks.append(track)
            track.append(mido.Message("note_on", note=60, velocity=80, time=0))
            track.append(mido.Message("note_off", note=60, velocity=0, time=120))
            midi.save(midi_path)
            model = NoteModel()
            model.load_midi(str(midi_path))
            self.assertIsNotNone(model.last_smart_chart_stats)
            self.assertEqual(model.last_smart_chart_stats.notes, 1)

    def test_midi_to_xml_preserves_overlapping_and_hanging_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            midi_path = Path(temp_dir) / "stacked.mid"
            xml_path = Path(temp_dir) / "stacked.xml"
            midi = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            midi.tracks.append(track)
            track.append(mido.Message("note_on", note=60, velocity=80, time=0))
            track.append(mido.Message("note_on", note=60, velocity=70, time=10))
            track.append(mido.Message("note_off", note=60, velocity=0, time=100))
            track.append(mido.Message("note_on", note=72, velocity=90, time=10))
            # No note-off for 60's first note or the final 72.
            midi.save(midi_path)
            converter = MIDIToXMLConverter()
            converter.convert_midi_to_xml(str(midi_path), str(xml_path))
            xml_notes = ET.parse(xml_path).getroot().findall("./note_data/note")
            self.assertEqual(len(xml_notes), 3)


if __name__ == "__main__":
    unittest.main()


class HandReachTests(unittest.TestCase):
    """「盡量平均」不能凌駕於手搆不搆得到。

    Miku 的消失那份：左手 41、53（八度）配右手 68、72、75、80（四音和絃），
    硬切 3/3 會把 68 判給左手，於是左手要同時按 41~68 —— 一隻手做不到。官方
    real 難度 30495 個同時發聲組裡，單手鍵道跨度 99.7% 在 9 條以內。
    """

    def group(self):
        # 鍵道位置照這首歌的音高範圍換算（約 0.35 條/半音）
        return [
            lane_note(1000, 41, 2, track=1, index=0),
            lane_note(1000, 53, 6, track=1, index=1),
            lane_note(1000, 68, 11, track=0, index=2),
            lane_note(1000, 72, 13, track=0, index=3),
            lane_note(1000, 75, 14, track=0, index=4),
            lane_note(1000, 80, 15, track=0, index=5),
        ]

    def test_unreachable_even_split_yields_to_the_hand(self):
        notes = self.group()
        arrange_midi_notes(notes, SmartChartSettings(beat_ms=500.0))
        by_pitch = {n.pitch: n.hand for n in notes}
        self.assertEqual(by_pitch[41], 1)
        self.assertEqual(by_pitch[53], 1)
        self.assertEqual(by_pitch[68], 0, "68 屬於右手那個四音和絃，不該被切給左手")
        self.assertEqual(sum(1 for n in notes if n.hand == 1), 2)

    def test_even_split_still_wins_when_both_hands_can_reach(self):
        # 四音和絃整組都在一隻手搆得到的範圍內，但官方仍然幾乎不會 4/0
        # （N=4 只有 2% 是 4/0），所以照舊切 2/2。
        notes = [
            lane_note(1000, 60, 8, track=0, index=0),
            lane_note(1000, 64, 9, track=0, index=1),
            lane_note(1000, 67, 10, track=0, index=2),
            lane_note(1000, 72, 12, track=0, index=3),
        ]
        arrange_midi_notes(notes, SmartChartSettings(beat_ms=500.0))
        self.assertEqual(sum(1 for n in notes if n.hand == 1), 2)

    def test_reachable_track_split_is_left_alone(self):
        # 音軌本來就切成 2/2 而且兩手都搆得到 —— 不要動它。
        notes = [
            lane_note(1000, 48, 3, track=1, index=0),
            lane_note(1000, 55, 5, track=1, index=1),
            lane_note(1000, 72, 12, track=0, index=2),
            lane_note(1000, 76, 14, track=0, index=3),
        ]
        arrange_midi_notes(notes, SmartChartSettings(beat_ms=500.0))
        self.assertEqual([n.hand for n in sorted(notes, key=lambda x: x.pitch)],
                         [1, 1, 0, 0])

    def test_track_split_survives_when_the_even_split_is_merely_possible(self):
        """t=49771 那一組：3/3 其實搆得到，但音軌說是 2/4，就該照音軌。

        官方 N=6 的分配是 3/3 55%、4/2 22%、2/4 17%——單手拿 4 顆佔 39%，
        舊的 cap（單手最多 3 顆）等於把那 39% 全禁掉，於是右手和絃的最低音
        60 被切給左手。上限改成官方的 95 百分位（N=5~7 是 4）之後就不會了。
        """
        notes = [
            lane_note(1000, 41, 8, hand=1, track=1, index=0),
            lane_note(1000, 53, 11, hand=1, track=1, index=1),
            lane_note(1000, 60, 14, track=0, index=2),
            lane_note(1000, 65, 16, track=0, index=3),
            lane_note(1000, 68, 19, track=0, index=4),
            lane_note(1000, 72, 21, track=0, index=5),
        ]
        arrange_midi_notes(notes, SmartChartSettings(beat_ms=500.0))
        by_pitch = {n.pitch: n.hand for n in notes}
        self.assertEqual(by_pitch[60], 0, "60 是右手和絃的最低音，不該被切給左手")
        self.assertEqual(sum(1 for n in notes if n.hand == 1), 2)

    def test_five_note_group_may_put_four_on_one_hand(self):
        # 官方 N=5 有 12% 是 4/1，舊上限一律禁止。
        from qt_editor.smart_chart import _hand_split_cap
        settings = SmartChartSettings(beat_ms=500.0)
        self.assertEqual(_hand_split_cap(4, settings), 3)
        self.assertEqual(_hand_split_cap(5, settings), 4)
        self.assertEqual(_hand_split_cap(6, settings), 4)
        self.assertEqual(_hand_split_cap(7, settings), 4)
        self.assertEqual(_hand_split_cap(8, settings), 5)

    def test_leap_rule_is_skipped_when_the_tracks_decide_the_hands(self):
        """有音軌就別再猜。大跳修正只該在分手是猜出來的時候跑。

        實測它只把同手大跳率壓下 0.4~1.2 個百分點，代價是把上百顆音符判到和音軌
        相反的手（初音未來的消失：8 顆 → 119 顆，也就是使用者看到的「左手變右手」）。

        直接驗「有沒有被呼叫」。試過用合成音符去重現那個翻手，但翻不翻取決於前面
        好幾組累積下來的 last-hand 狀態，合成的例子兩種設定都不會翻——那種測試是
        空包彈。
        """
        import qt_editor.smart_chart as module

        calls = []
        original = module._reduce_hand_leaps
        module._reduce_hand_leaps = lambda groups, settings: calls.append(1) or 0
        try:
            two_tracks = [
                lane_note(0, 48, 4, hand=1, track=1, index=0),
                lane_note(0, 72, 18, track=0, index=1),
            ]
            arrange_midi_notes(two_tracks, SmartChartSettings(beat_ms=500.0))
            self.assertEqual(calls, [], "音軌已經說了左右手，不該再跑大跳修正")

            one_track = [
                lane_note(0, 48, 4, track=0, index=0),
                lane_note(0, 72, 18, track=0, index=1),
            ]
            arrange_midi_notes(one_track, SmartChartSettings(beat_ms=500.0))
            self.assertEqual(len(calls), 1, "分手是猜的時候仍然要跑")
        finally:
            module._reduce_hand_leaps = original

    def test_single_track_still_gets_the_leap_rule(self):
        from qt_editor.smart_chart import _hands_came_from_tracks
        one = [lane_note(0, 60, 12, track=0, index=0),
               lane_note(0, 48, 4, track=0, index=1)]
        two = [lane_note(0, 60, 12, track=0, index=0),
               lane_note(0, 48, 4, track=1, index=1)]
        none = [lane_note(0, 60, 12, index=0)]
        for n in none:
            n.track = None
        self.assertFalse(_hands_came_from_tracks(one))
        self.assertTrue(_hands_came_from_tracks(two))
        self.assertFalse(_hands_came_from_tracks(none))



class InsufficientDegreeNarrowingTests(unittest.TestCase):
    """度數不夠但同手按鍵很多時要收窄。

    參考的是**這個曲庫的手寫譜**，不是官方——兩者用的是相反的手段：

    * 官方靠**重疊**擠空間（同手和絃 28% 的組別鍵道重疊），寬度 2 只佔 3.0%。
    * 這個曲庫的手寫譜幾乎不重疊（8 首實測 0.0~4.6%），改靠**收窄**：
      寬度 2 佔 14.6%。

    手寫譜同手 3 顆時的收窄率，各跨度是 12% / 28% / 33% / 13%
    （0~4、5~6、7~11、12+ 半音）—— 峰值在中間那段，不在最擠的地方。
    """

    def hand(self, pitches, start=0):
        notes = [note(start, p, track=2, index=i) for i, p in enumerate(pitches)]
        notes.append(note(start + 2000, 36, track=1, index=len(pitches)))
        return notes

    def test_the_band_is_what_triggers_narrowing(self):
        from qt_editor.smart_chart import _hand_span_wants_narrowing
        settings = SmartChartSettings()
        wants = lambda ps: _hand_span_wants_narrowing(
            [note(0, p, index=i) for i, p in enumerate(ps)], settings)
        self.assertFalse(wants([60, 62, 64]), '太擠：收窄也擠不出位置')
        self.assertTrue(wants([60, 63, 67]), '跨度 7：正是度數不夠那段')
        self.assertTrue(wants([60, 65, 71]), '跨度 11')
        self.assertFalse(wants([60, 67, 74]), '跨度 14：本來就放得下')

    def test_two_note_hands_use_the_same_band(self):
        """兩顆和三顆以上走同一條帶子——度數短就不收窄，靠位置表現。"""
        from qt_editor.smart_chart import _hand_span_wants_narrowing
        settings = SmartChartSettings()
        pair = lambda a, b: [note(0, a, index=0), note(0, b, index=1)]
        self.assertTrue(_hand_span_wants_narrowing(pair(60, 67), settings))
        self.assertFalse(_hand_span_wants_narrowing(pair(60, 62), settings),
                         '跨度 2：太短，不收窄')
        self.assertFalse(_hand_span_wants_narrowing(pair(60, 74), settings),
                         '跨度 14：本來就放得下')

    def test_a_single_note_never_triggers_it(self):
        from qt_editor.smart_chart import _hand_span_wants_narrowing
        self.assertFalse(_hand_span_wants_narrowing(
            [note(0, 60, index=0)], SmartChartSettings()))

    def test_the_width_rule_narrows_an_in_band_chord(self):
        """寬度規則本身：落在區間內、相鄰音程又近的那幾顆要變窄。

        注意這是**規則**的斷言，不是整條排譜流程的。實測手寫譜 k=3 在這段
        跨度的收窄率是 33%，不是 100%——後面十幾道修復通道會依當下的空間
        決定要不要用，所以不能拿單一和絃去斷言最終寬度。
        """
        from qt_editor.smart_chart import _initial_hand_widths
        settings = SmartChartSettings()
        chord = [note(0, p, index=i) for i, p in enumerate((60, 63, 67))]
        self.assertEqual(
            _initial_hand_widths(chord, settings, True), [2, 2, 2])
        # 不允許收窄時就維持難度寬度
        self.assertEqual(
            _initial_hand_widths(chord, settings, False), [3, 3, 3])

    def test_a_wide_three_note_chord_keeps_full_width(self):
        notes = self.hand([60, 67, 74])
        arrange_midi_notes(notes)
        widths = [n.max_key - n.min_key + 1 for n in notes[:3]]
        self.assertEqual(widths, [3, 3, 3], '度數夠就不該收窄')

    def test_narrowing_survives_the_restore_pass(self):
        """_restore_unneeded_narrowing 會把收窄還原回寬度 3。

        落在「度數不夠」那段跨度的收窄是刻意的，必須擋住還原，否則
        k=3 的收窄率永遠回不到手寫譜的 33%。
        """
        from qt_editor.smart_chart import _restore_unneeded_narrowing
        settings = SmartChartSettings()
        notes = [note(0, p, track=2, index=i) for i, p in enumerate((60, 63, 67))]
        for n, (lo, hi) in zip(notes, ((10, 11), (13, 14), (16, 17))):
            n.min_key, n.max_key = lo, hi
        restored = _restore_unneeded_narrowing(notes, [notes], settings)
        self.assertEqual(restored, 0)
        self.assertEqual([n.max_key - n.min_key + 1 for n in notes], [2, 2, 2])

    def test_narrowing_outside_the_band_is_still_restored(self):
        # 三顆擠在 4 個半音內：收窄不是刻意的，該還原
        from qt_editor.smart_chart import _restore_unneeded_narrowing
        settings = SmartChartSettings()
        notes = [note(0, p, track=2, index=i) for i, p in enumerate((60, 62, 64))]
        for n, (lo, hi) in zip(notes, ((10, 11), (13, 14), (16, 17))):
            n.min_key, n.max_key = lo, hi
        restored = _restore_unneeded_narrowing(notes, [notes], settings)
        self.assertGreater(restored, 0)

    def test_chords_do_not_overlap(self):
        """手寫譜幾乎不重疊（0.0~4.6%），收窄是它擠空間的方式。"""
        notes = self.hand([60, 63, 67])
        arrange_midi_notes(notes)
        chord = sorted(notes[:3], key=lambda n: n.min_key)
        for a, b in zip(chord, chord[1:]):
            if a.hand == b.hand:
                self.assertGreater(int(b.min_key), int(a.max_key),
                                   '同手音符不該疊在一起')

    def test_notes_stay_on_the_keyboard(self):
        notes = self.hand([60, 62, 64, 66, 68])
        settings = SmartChartSettings()
        arrange_midi_notes(notes, settings)
        for n in notes:
            self.assertGreaterEqual(int(n.min_key), 0)
            self.assertLess(int(n.max_key), settings.total_lanes)

    def test_the_band_is_configurable(self):
        from qt_editor.smart_chart import _hand_span_wants_narrowing
        wide = SmartChartSettings(narrow_span_min=0, narrow_span_max=99)
        self.assertTrue(_hand_span_wants_narrowing(
            [note(0, p, index=i) for i, p in enumerate((60, 62, 64))], wide))


class HoldTailSequentialGapTests(unittest.TestCase):
    """長條尾端：靠**時間有沒有重疊**分辨前後與分解和弦。

    同手的下一顆和這條長押時間上沒有重疊 = 前後關係，即使手按得住也該把間隔
    留出來；有重疊的就是分解和弦，不動。

    原本只裁「物理上按不出來」的（同一個鍵、超過五指、超過手的跨度），那條
    規則在整個曲庫只動到 14.2% 的長押，尾巴貼著下一顆只差幾毫秒也不管 ——
    那正是「啥都不會裁」的原因。
    """

    def chart(self, spec):
        """spec: [(start, pitch, note_type, dur), ...] 同一隻手。

        每顆各佔自己的鍵道：`_same_key` 比的是**鍵道範圍**不是音高，全部塞在
        同一組鍵道的話每一對都會被判成搶同一個鍵，什麼規則都試不出來。
        """
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = []
        base = min(p for _s, p, _t, _d in spec)
        for i, (start, pitch, nt, dur) in enumerate(spec):
            n = note(start, pitch, hand=0, track=0, duration=dur, index=i)
            n.note_type = nt
            lo = min(24, (pitch - base) * 2)
            n.min_key, n.max_key = lo, lo + 2
            m.notes_tree.append(n)
        m.rebuild_display_cache()
        return m

    def test_a_tail_touching_the_next_note_gets_the_gap(self):
        # 長押在 495 結束、下一顆 500 開始：沒重疊 = 前後，間隔卻只有 5ms
        m = self.chart([(0, 60, 2, 495), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 1)
        self.assertEqual(m.notes_tree[0].end, 460)

    def test_an_overlapping_tail_is_a_broken_chord(self):
        # 長押還響著下一顆就進來 = 分解和弦，不動
        m = self.chart([(0, 60, 2, 2000), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)
        self.assertEqual(m.notes_tree[0].end, 2000)

    def test_a_stepwise_overlap_is_also_left_alone(self):
        # 音程不再是判斷依據：二度但時間重疊，一樣當分解和弦
        m = self.chart([(0, 60, 2, 2000), (500, 62, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)

    def test_a_gap_that_is_already_enough_is_untouched(self):
        m = self.chart([(0, 60, 2, 400), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)
        self.assertEqual(m.notes_tree[0].end, 400)

    def test_exactly_the_gap_is_enough(self):
        m = self.chart([(0, 60, 2, 460), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)

    def test_it_is_off_by_default(self):
        m = self.chart([(0, 60, 2, 495), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40), 0)

    def test_physical_conflicts_still_win_when_overlapping(self):
        # 同一個鍵：即使時間重疊也不是分解和弦，一定要先放開
        m = self.chart([(0, 60, 2, 2000), (300, 60, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 1)

    def test_the_last_hold_has_nothing_after_it(self):
        m = self.chart([(0, 60, 2, 495)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)

    def test_only_holds_are_touched(self):
        m = self.chart([(0, 60, 0, 495), (500, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)

    def test_a_hold_is_never_lengthened(self):
        m = self.chart([(0, 60, 2, 100), (900, 64, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 0)
        self.assertEqual(m.notes_tree[0].end, 100)

    def test_a_hold_never_collapses_to_zero(self):
        m = self.chart([(0, 60, 2, 10), (20, 64, 0, 100)])
        m.resolve_hold_tail_overlaps(400, sequential_gap=True)
        self.assertGreater(m.notes_tree[0].end, m.notes_tree[0].start)

    def test_simultaneous_notes_are_a_chord_not_the_next_note(self):
        m = self.chart([(0, 60, 2, 495), (0, 64, 0, 100), (500, 67, 0, 100)])
        self.assertEqual(m.resolve_hold_tail_overlaps(40, sequential_gap=True), 1)
        self.assertEqual(m.notes_tree[0].end, 460)

    def test_the_ring_limit_still_works(self):
        spec = [(0, 60, 2, 4000)] + [
            (200 + i * 200, 64 + (i % 3) * 3, 0, 100) for i in range(6)]
        m = self.chart(spec)
        self.assertEqual(m.resolve_hold_tail_overlaps(40), 0)
        m2 = self.chart(spec)
        self.assertEqual(m2.resolve_hold_tail_overlaps(40, max_ring_notes=3), 1)
