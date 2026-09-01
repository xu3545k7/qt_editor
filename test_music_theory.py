"""調性偵測與音階／琶音生成。

編輯器要能「一次放下一組音階或琶音，而且聽起來在這首的調上」，所以先從現有音符
推調性，再照那個調的音階生成音高。
"""

import unittest

from qt_editor.music_theory import (
    Key, PATTERN_KINDS, PITCH_CLASS_NAMES, SCALE_BY_KEY, SCALES, build_arpeggio,
    build_pattern, build_scale, detect_key, pattern_key, pitch_at_scale_index,
    scale_index_of, scale_of, snap_to_key,
)

C_MAJOR = Key(0, 'major')
A_MINOR = Key(9, 'minor')


class KeyBasicsTests(unittest.TestCase):
    def test_major_scale_pitch_classes(self):
        self.assertEqual(C_MAJOR.pitch_classes, (0, 2, 4, 5, 7, 9, 11))

    def test_natural_minor_pitch_classes(self):
        self.assertEqual(A_MINOR.pitch_classes, (0, 2, 4, 5, 7, 9, 11))  # a 小調＝白鍵

    def test_contains(self):
        self.assertTrue(C_MAJOR.contains(60))     # C
        self.assertFalse(C_MAJOR.contains(61))    # C#
        self.assertTrue(C_MAJOR.contains(72))     # 跨八度也算

    def test_name_is_readable(self):
        self.assertEqual(C_MAJOR.name(), 'C 大調')
        self.assertEqual(A_MINOR.name(), 'A 小調')


class DetectKeyTests(unittest.TestCase):
    def test_a_plain_c_major_scale_is_detected_as_c_major(self):
        key = detect_key([60, 62, 64, 65, 67, 69, 71, 72])
        self.assertEqual((key.tonic, key.mode), (0, 'major'))

    def test_emphasising_the_minor_tonic_gives_the_minor_key(self):
        # 一樣是白鍵，但重複強調 A 與 E → a 小調而不是 C 大調
        pitches = [57, 59, 60, 62, 64, 65, 67, 69] + [57] * 6 + [64] * 4
        key = detect_key(pitches)
        self.assertEqual((key.tonic, key.mode), (9, 'minor'))

    def test_transposition_moves_the_detected_tonic(self):
        d_major = [62, 64, 66, 67, 69, 71, 73, 74]
        key = detect_key(d_major)
        self.assertEqual((key.tonic, key.mode), (2, 'major'))

    def test_durations_weight_the_result(self):
        # 顆數一樣，但把 F# 彈得很長 → 應該偏向有 F# 的調（G 大調）
        pitches = [67, 69, 71, 66]
        key = detect_key(pitches, [1, 1, 1, 40])
        self.assertIn(PITCH_CLASS_NAMES[key.tonic], ('G', 'D', 'B', 'F#'))

    def test_no_pitches_returns_none(self):
        self.assertIsNone(detect_key([]))
        self.assertIsNone(detect_key([None, None]))

    def test_confidence_is_within_range(self):
        key = detect_key([60, 62, 64, 65, 67, 69, 71])
        self.assertGreaterEqual(key.confidence, 0.0)
        self.assertLessEqual(key.confidence, 1.0)


class SnapAndIndexTests(unittest.TestCase):
    def test_in_key_pitches_are_left_alone(self):
        for p in (60, 62, 64, 65, 67, 69, 71):
            self.assertEqual(snap_to_key(p, C_MAJOR), p)

    def test_out_of_key_pitches_move_to_the_nearest_scale_tone(self):
        self.assertIn(snap_to_key(61, C_MAJOR), (60, 62))
        self.assertIn(snap_to_key(66, C_MAJOR), (65, 67))

    def test_scale_index_round_trips(self):
        for p in (48, 60, 62, 71, 84):
            idx = scale_index_of(p, C_MAJOR)
            self.assertEqual(pitch_at_scale_index(idx, C_MAJOR), p)

    def test_index_steps_by_seven_per_octave(self):
        self.assertEqual(scale_index_of(72, C_MAJOR) - scale_index_of(60, C_MAJOR), 7)


class BuildScaleTests(unittest.TestCase):
    def test_ascending_c_major(self):
        self.assertEqual(build_scale(C_MAJOR, 60, 8), [60, 62, 64, 65, 67, 69, 71, 72])

    def test_descending(self):
        self.assertEqual(build_scale(C_MAJOR, 72, 8, -1), [72, 71, 69, 67, 65, 64, 62, 60])

    def test_a_major_uses_the_right_accidentals(self):
        a_major = Key(9, 'major')
        got = build_scale(a_major, 69, 8)
        self.assertEqual([PITCH_CLASS_NAMES[p % 12] for p in got],
                         ['A', 'B', 'C#', 'D', 'E', 'F#', 'G#', 'A'])

    def test_thirds_step_two_scale_degrees(self):
        self.assertEqual(build_scale(C_MAJOR, 60, 4, step=2), [60, 64, 67, 71])

    def test_an_out_of_key_start_is_snapped_first(self):
        got = build_scale(C_MAJOR, 61, 3)
        self.assertTrue(all(C_MAJOR.contains(p) for p in got))

    def test_zero_count_is_empty(self):
        self.assertEqual(build_scale(C_MAJOR, 60, 0), [])


class BuildArpeggioTests(unittest.TestCase):
    def test_major_triad_from_the_tonic(self):
        self.assertEqual(build_arpeggio(C_MAJOR, 60, 3), [60, 64, 67])

    def test_it_keeps_stacking_into_the_next_octave(self):
        self.assertEqual(build_arpeggio(C_MAJOR, 60, 6), [60, 64, 67, 72, 76, 79])

    def test_the_chord_quality_follows_the_key(self):
        # 大調的 II 級是小三和弦，不用另外指定品質
        self.assertEqual(build_arpeggio(C_MAJOR, 62, 3), [62, 65, 69])   # D F A
        # 小調主和弦是小三和弦
        self.assertEqual(build_arpeggio(A_MINOR, 57, 3), [57, 60, 64])   # A C E

    def test_seventh_adds_the_fourth_tone(self):
        self.assertEqual(build_arpeggio(C_MAJOR, 60, 4, seventh=True), [60, 64, 67, 71])

    def test_descending_arpeggio(self):
        got = build_arpeggio(C_MAJOR, 72, 3, -1)
        self.assertEqual(got, [72, 69, 65])
        self.assertEqual(got, sorted(got, reverse=True))


class BuildPatternTests(unittest.TestCase):
    def test_up_then_down_plays_the_peak_once(self):
        got = build_pattern('scale', C_MAJOR, 60, 5, direction=0)
        self.assertEqual(got, [60, 62, 64, 65, 67, 65, 64, 62, 60])
        self.assertEqual(got.count(67), 1, '頂點只彈一次')
        self.assertEqual(got[0], got[-1], '回到起點')

    def test_every_kind_produces_in_key_pitches(self):
        for kind in ('scale', 'thirds', 'arpeggio', 'arpeggio7'):
            got = build_pattern(kind, A_MINOR, 57, 8)
            self.assertTrue(all(A_MINOR.contains(p) for p in got), kind)

    def test_unknown_kind_falls_back_to_a_scale(self):
        self.assertEqual(build_pattern('???', C_MAJOR, 60, 3),
                         build_scale(C_MAJOR, 60, 3))


class ScaleRegistryTests(unittest.TestCase):
    def test_every_scale_starts_on_the_tonic_and_stays_in_one_octave(self):
        for s in SCALES:
            self.assertEqual(s.steps[0], 0, s.key)
            self.assertEqual(list(s.steps), sorted(set(s.steps)), s.key)
            self.assertLess(max(s.steps), 12, s.key)

    def test_ids_are_unique(self):
        self.assertEqual(len(SCALE_BY_KEY), len(SCALES))

    def test_an_unknown_id_falls_back_to_major(self):
        self.assertEqual(scale_of('nope').key, 'major')
        self.assertEqual(Key(0, 'nope').pitch_classes, C_MAJOR.pitch_classes)

    def test_the_ui_list_covers_every_scale_plus_the_shape_kinds(self):
        kinds = {value for _label, value in PATTERN_KINDS}
        self.assertTrue({'scale', 'thirds', 'arpeggio', 'arpeggio7'} <= kinds)
        for s in SCALES:
            if s.key in ('major', 'minor'):
                continue        # 這兩個從「調性」那邊選，音型不重複列
            self.assertIn(s.key, kinds, s.key)

    def test_labels_are_unique(self):
        labels = [label for label, _v in PATTERN_KINDS]
        self.assertEqual(len(set(labels)), len(labels))


class ChromaticTests(unittest.TestCase):
    def test_a_chromatic_run_is_every_semitone(self):
        self.assertEqual(build_pattern('chromatic', C_MAJOR, 60, 13),
                         list(range(60, 73)))

    def test_it_starts_exactly_where_you_put_it(self):
        # 對稱音階不該被譜面主音吸走，點哪裡就從哪裡開始
        for start in range(60, 72):
            self.assertEqual(build_pattern('chromatic', C_MAJOR, start, 3)[0],
                             start, start)

    def test_descending(self):
        self.assertEqual(build_pattern('chromatic', A_MINOR, 72, 4, -1),
                         [72, 71, 70, 69])

    def test_up_then_down(self):
        self.assertEqual(build_pattern('chromatic', C_MAJOR, 60, 3, 0),
                         [60, 61, 62, 61, 60])

    def test_it_ignores_the_key_entirely(self):
        for key in (C_MAJOR, A_MINOR, Key(6, 'major')):
            self.assertEqual(build_pattern('chromatic', key, 61, 5),
                             [61, 62, 63, 64, 65], key.name())


class OtherScaleTests(unittest.TestCase):
    def test_whole_tone_steps_by_two_semitones(self):
        got = build_pattern('whole_tone', C_MAJOR, 66, 6)
        self.assertEqual(got, [66, 68, 70, 72, 74, 76])

    def test_blues_is_rooted_on_the_key_tonic(self):
        # a 小調的藍調：A C D D# E G
        got = build_pattern('blues', A_MINOR, 69, 6)
        self.assertEqual(got, [69, 72, 74, 75, 76, 79])

    def test_minor_pentatonic_skips_the_second_and_sixth(self):
        got = build_pattern('minor_pentatonic', C_MAJOR, 60, 6)
        self.assertEqual(got, [60, 63, 65, 67, 70, 72])

    def test_harmonic_minor_has_the_raised_seventh(self):
        got = build_pattern('harmonic_minor', A_MINOR, 69, 8)
        self.assertEqual(got, [69, 71, 72, 74, 76, 77, 80, 81])

    def test_the_church_modes_are_rotations_of_the_major_scale(self):
        white = {0, 2, 4, 5, 7, 9, 11}
        cases = {'dorian': 2, 'phrygian': 4, 'lydian': 5,
                 'mixolydian': 7, 'locrian': 11}
        for mode, tonic in cases.items():
            self.assertEqual(set(Key(tonic, mode).pitch_classes), white, mode)

    def test_octatonic_alternates_half_and_whole(self):
        got = build_pattern('octatonic_hw', C_MAJOR, 60, 9)
        self.assertEqual([b - a for a, b in zip(got, got[1:])],
                         [1, 2, 1, 2, 1, 2, 1, 2])

    def test_every_scale_kind_produces_the_right_number_of_notes(self):
        for _label, kind in PATTERN_KINDS:
            got = build_pattern(kind, C_MAJOR, 60, 7)
            self.assertEqual(len(got), 7, kind)
            self.assertEqual(got, sorted(got), kind)

    def test_every_scale_kind_stays_inside_its_own_scale(self):
        for s in SCALES:
            got = build_pattern(s.key, C_MAJOR, 60, 9)
            used = pattern_key(s.key, C_MAJOR, 60)
            self.assertTrue(all(used.contains(p) for p in got), s.key)


class PatternKeyTests(unittest.TestCase):
    def test_shape_kinds_keep_the_chart_key(self):
        for kind in ('scale', 'thirds', 'arpeggio', 'arpeggio7'):
            self.assertEqual(pattern_key(kind, A_MINOR, 60), A_MINOR)

    def test_a_keyed_scale_takes_the_chart_tonic(self):
        got = pattern_key('blues', A_MINOR, 60)
        self.assertEqual((got.tonic, got.mode), (9, 'blues'))

    def test_a_symmetric_scale_takes_the_start_pitch(self):
        got = pattern_key('chromatic', A_MINOR, 61)
        self.assertEqual((got.tonic, got.mode), (1, 'chromatic'))

    def test_an_unknown_kind_keeps_the_key(self):
        self.assertEqual(pattern_key('???', C_MAJOR, 60), C_MAJOR)


if __name__ == '__main__':
    unittest.main()
