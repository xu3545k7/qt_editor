"""旋律斜率修補：冠音／低音前後步伐往「幾個半音 → 幾格」的實測曲線拉。"""

import unittest

from qt_editor.models import GNote
from qt_editor.smart_chart import (_repair_melody_slope, _clusters, settings_for_style,
                                   SmartChartSettings)


def lane_note(start, pitch, centre, hand=0, index=0):
    n = GNote(None, index)
    n.start, n.end, n.gate = start, start + 100, 100
    n.pitch, n.hand = pitch, hand
    n.min_key, n.max_key = centre - 1, centre + 1
    return n


def centre(n):
    return (n.min_key + n.max_key) / 2.0


def run(notes, **overrides):
    config = settings_for_style('eather', **overrides)
    groups = _clusters(notes, config.onset_tolerance_ms)
    return _repair_melody_slope(notes, groups, config)


class MelodySlopeTests(unittest.TestCase):
    def test_semitone_steps_that_are_too_wide_come_back(self):
        # 右手 60 61 60 61：每一步都被擺成 4 格（目標約 1 格）
        notes = [lane_note(i * 250, 60 + (i % 2), 14 + 4 * (i % 2), index=i) for i in range(4)]
        moves = run(notes)
        self.assertGreater(moves, 0)
        steps = [abs(centre(b) - centre(a)) for a, b in zip(notes, notes[1:])]
        self.assertTrue(all(1 <= s <= 2 for s in steps), steps)
        # 方向不准反、不准同格
        for a, b in zip(notes, notes[1:]):
            self.assertEqual((b.max_key - a.max_key) > 0, b.pitch > a.pitch)

    def test_repeated_pitch_stays_in_the_same_lane(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 72, 15, index=1),
                 lane_note(500, 60, 14, index=2)]
        run(notes)
        self.assertEqual((notes[0].min_key, notes[0].max_key), (notes[2].min_key, notes[2].max_key))

    def test_already_on_target_does_not_move(self):
        # 全音 1 格、三度 1~2 格：成本已經低於門檻
        notes = [lane_note(0, 60, 12, index=0), lane_note(250, 62, 13, index=1),
                 lane_note(500, 65, 14, index=2)]
        before = [(n.min_key, n.max_key) for n in notes]
        self.assertEqual(run(notes), 0)
        self.assertEqual([(n.min_key, n.max_key) for n in notes], before)

    def test_far_apart_notes_are_ignored(self):
        notes = [lane_note(0, 60, 10, index=0), lane_note(5000, 61, 20, index=1)]
        self.assertEqual(run(notes), 0)

    def test_far_repeat_is_not_pulled_away(self):
        # 60 在 0ms 和 2000ms 各出現一次（超出 600ms 的鄰居範圍）。中間 250ms 的 61
        # 被擺得太遠，斜率想把 60(2000) 往右拉；遠同音的成本要讓它留在原本那一格。
        notes = [lane_note(0, 60, 14, index=0), lane_note(1750, 67, 22, index=1),
                 lane_note(2000, 60, 14, index=2)]
        run(notes, melody_slope_far_repeat_weight=0.0)
        moved_without = notes[2].max_key
        notes = [lane_note(0, 60, 14, index=0), lane_note(1750, 67, 22, index=1),
                 lane_note(2000, 60, 14, index=2)]
        run(notes, melody_slope_far_repeat_weight=5.0)
        self.assertEqual(notes[2].max_key, notes[0].max_key)
        self.assertNotEqual(moved_without, notes[0].max_key)

    def test_semitone_top_step_becomes_one_lane(self):
        from qt_editor.smart_chart import _repair_small_top_steps
        # 60 → 61（半音）被擺開 4 格；61 → 72 本來就該走遠
        notes = [lane_note(0, 60, 10, index=0), lane_note(250, 61, 14, index=1),
                 lane_note(500, 72, 19, index=2)]
        config = settings_for_style('eather')
        groups = _clusters(notes, config.onset_tolerance_ms)
        self.assertEqual(_repair_small_top_steps(notes, groups, config), 1)
        self.assertEqual(centre(notes[1]) - centre(notes[0]), 1)
        self.assertGreater(notes[2].max_key, notes[1].max_key)

    def test_semitone_fix_keeps_direction(self):
        from qt_editor.smart_chart import _repair_small_top_steps
        notes = [lane_note(0, 62, 14, index=0), lane_note(250, 60, 10, index=1)]
        config = settings_for_style('eather')
        _repair_small_top_steps(notes, _clusters(notes, config.onset_tolerance_ms), config)
        self.assertLess(notes[1].max_key, notes[0].max_key)
        self.assertEqual(centre(notes[0]) - centre(notes[1]), 1)

    def test_repeat_snap_does_not_drag_the_rest_of_the_chord(self):
        from qt_editor.smart_chart import _snap_repeated_pitch_lanes
        # 64 一直待在右緣 16；60 第一次在右緣 10。1000ms 的和弦 [60, 64] 若為了讓
        # 60 對齊而整塊左移 2 格，64 就被拖離它自己的位置——不應該發生。
        def chart():
            return [lane_note(0, 60, 9, index=0), lane_note(400, 64, 15, index=1),
                    lane_note(1000, 60, 11, index=2), lane_note(1000, 64, 15, index=3),
                    lane_note(1600, 64, 15, index=4)]
        loose = chart()
        config = settings_for_style('eather', snap_block_drift_slack=None)
        loose_moves = _snap_repeated_pitch_lanes(loose, _clusters(loose, config.onset_tolerance_ms), config)
        guarded = chart()
        config = settings_for_style('eather', snap_block_drift_slack=1.0)
        guarded_moves = _snap_repeated_pitch_lanes(
            guarded, _clusters(guarded, config.onset_tolerance_ms), config)
        # 有上限：60 自己對齊過去，64 留在原位，一步到位
        self.assertEqual(guarded[2].max_key, guarded[0].max_key)
        self.assertEqual(guarded[3].max_key, 16)
        self.assertEqual(guarded_moves, 1)
        # 沒上限：整塊拖過去、64 又拖回來，來回好幾次，最後 60 還是沒對齊
        self.assertGreater(loose_moves, guarded_moves)
        self.assertNotEqual(loose[2].max_key, loose[0].max_key)

    def test_style_tables(self):
        self.assertAlmostEqual(settings_for_style('eather').melody_step_top[0], 0.84)
        self.assertAlmostEqual(settings_for_style('official').melody_step_top[0], 1.10)
        self.assertEqual(SmartChartSettings().melody_slope_passes, 2)
        self.assertTrue(SmartChartSettings().melody_slope_after_snap)
        self.assertEqual(settings_for_style('eather').melody_slope_far_repeat_weight, 0.5)
        self.assertEqual(settings_for_style('official').melody_slope_far_repeat_weight, 0.0)
        self.assertTrue(settings_for_style('eather').small_top_step_fix)
        self.assertFalse(settings_for_style('official').small_top_step_fix)
        self.assertEqual(settings_for_style('eather').snap_block_drift_slack, 1.0)
        self.assertEqual(settings_for_style('official').snap_block_drift_slack, 2.0)


if __name__ == '__main__':
    unittest.main()
