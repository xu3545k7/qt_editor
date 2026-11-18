"""冠音方向的收尾：音高動了、右緣沒跟著動的地方，整組平移回去。

這一道是製譜器「冠音的旋律線」檢查的第一條規則接回排譜流程（見
`smart_chart._repair_melody_direction` 的說明與實測表）。
"""

import unittest

from qt_editor import melody_line as ML
from qt_editor.models import GNote
from qt_editor.smart_chart import (_clusters_uncached, _melody_line_breaks,
                                   _repair_melody_direction,
                                   settings_for_style)


def lane_note(start, pitch, edge, hand=0, index=0, length=100, width=3,
              note_type=0, hidden=False):
    """右緣在 `edge`、寬 `width` 的一顆音符。"""
    n = GNote(None, index)
    n.start, n.end, n.gate = start, start + length, length
    n.pitch, n.hand = pitch, hand
    n.min_key, n.max_key = edge - width + 1, edge
    n.note_type = note_type
    n.hidden = hidden
    return n


def run(notes, **overrides):
    config = settings_for_style('eather', **overrides)
    groups = _clusters_uncached(notes, config.onset_tolerance_ms)
    return _repair_melody_direction(notes, groups, config)


def edges(notes):
    return [n.max_key for n in notes]


class PullingTheTopNoteBackOnSideTests(unittest.TestCase):
    def test_a_step_that_went_the_wrong_way_comes_back(self):
        # 音高往上一個全音，右緣卻往左跑了一格
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 62, 13, index=1)]
        self.assertGreater(run(notes), 0)
        self.assertGreater(notes[1].max_key, notes[0].max_key)

    def test_two_different_pitches_in_the_same_lane_get_separated(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 64, 14, index=1)]
        self.assertGreater(run(notes), 0)
        self.assertGreater(notes[1].max_key, notes[0].max_key)

    def test_going_down_moves_left(self):
        notes = [lane_note(0, 72, 14, index=0), lane_note(250, 67, 14, index=1)]
        self.assertGreater(run(notes), 0)
        self.assertLess(notes[1].max_key, notes[0].max_key)

    def test_a_step_that_is_already_fine_is_left_alone(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 62, 15, index=1)]
        before = edges(notes)
        self.assertEqual(run(notes), 0)
        self.assertEqual(edges(notes), before)

    def test_the_same_pitch_has_no_direction_to_keep(self):
        # 同一個音高前後同格：那是「同音同格」那條規則的事，不是方向
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 60, 14, index=1)]
        self.assertEqual(run(notes), 0)

    def test_a_real_rest_breaks_the_phrase(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(2000, 64, 14, index=1)]
        before = edges(notes)
        self.assertEqual(run(notes), 0)
        self.assertEqual(edges(notes), before)

    def test_a_long_note_does_not_break_the_phrase(self):
        """排譜器的斜率修補比起音，所以長押後面那一步它看不到；這一道比空拍。"""
        notes = [lane_note(0, 60, 14, index=0, length=900),
                 lane_note(1000, 64, 14, index=1)]
        self.assertGreater(run(notes), 0)
        self.assertGreater(notes[1].max_key, notes[0].max_key)

    def test_a_long_note_followed_by_a_real_rest_still_breaks(self):
        notes = [lane_note(0, 60, 14, index=0, length=900),
                 lane_note(2000, 64, 14, index=1)]
        self.assertEqual(run(notes), 0)

    def test_the_whole_chord_moves_together(self):
        notes = [lane_note(0, 60, 14, index=0),
                 lane_note(250, 62, 10, hand=1, index=1),
                 lane_note(250, 67, 14, index=2)]
        self.assertGreater(run(notes), 0)
        # 右手那一組整塊平移，和絃內部幾何不動；左手沒被帶走
        self.assertGreater(notes[2].max_key, notes[0].max_key)
        self.assertEqual(notes[1].max_key, 10)

    def test_no_room_at_the_wall_means_no_move(self):
        config = settings_for_style('eather')
        wall = config.total_lanes - 1
        notes = [lane_note(0, 60, wall, index=0),
                 lane_note(250, 64, wall, index=1)]
        self.assertEqual(run(notes), 0)
        self.assertEqual(notes[1].max_key, wall)

    def test_a_trill_in_the_group_is_never_moved(self):
        notes = [lane_note(0, 60, 14, index=0),
                 lane_note(250, 64, 14, index=1, note_type=0x40)]
        self.assertEqual(run(notes), 0)

    def test_the_switch_turns_it_off(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 64, 14, index=1)]
        before = edges(notes)
        self.assertEqual(run(notes, melody_direction_repair=False), 0)
        self.assertEqual(edges(notes), before)

    def test_a_step_too_far_to_reach_is_left_alone(self):
        # 要走對邊得搬 6 格，超過 melody_direction_reach
        notes = [lane_note(0, 60, 20, index=0), lane_note(250, 64, 15, index=1)]
        self.assertEqual(run(notes, melody_direction_reach=4), 0)
        self.assertGreater(run(notes, melody_direction_reach=8), 0)


class SpendingSamePitchForDirectionTests(unittest.TestCase):
    """同音額度：最多拆掉幾處「同音同格」來換一處方向。"""

    def chart(self):
        # 60 → 64 同格（方向破），而 64 在 500ms 後又出現一次、就在同一格
        return [lane_note(0, 60, 14, index=0),
                lane_note(250, 64, 14, index=1),
                lane_note(500, 64, 14, index=2)]

    def test_without_budget_the_alignment_wins(self):
        notes = self.chart()
        self.assertEqual(run(notes, melody_direction_repeat_budget=0), 0)
        self.assertEqual(notes[1].max_key, notes[2].max_key)

    def test_with_budget_one_the_direction_wins(self):
        notes = self.chart()
        self.assertGreater(run(notes, melody_direction_repeat_budget=1), 0)
        self.assertGreater(notes[1].max_key, notes[0].max_key)

    def test_the_default_spends_one(self):
        self.assertEqual(
            settings_for_style('eather').melody_direction_repeat_budget, 1)


class HiddenNotesAreShadowsTests(unittest.TestCase):
    """隱藏子音符不算冠音、不跟著搬、也不擋路。"""

    def test_a_hidden_note_is_never_the_top(self):
        # 隱藏的那顆音高最高，但它不佔按鍵，不該被當成冠音
        notes = [lane_note(0, 60, 14, index=0),
                 lane_note(250, 62, 15, index=1),
                 lane_note(250, 90, 15, index=2, hidden=True)]
        self.assertEqual(run(notes), 0)

    def test_a_hidden_note_stays_where_it_is(self):
        notes = [lane_note(0, 60, 14, index=0),
                 lane_note(250, 64, 14, index=1),
                 lane_note(250, 64, 14, index=2, hidden=True)]
        self.assertGreater(run(notes), 0)
        self.assertGreater(notes[1].max_key, notes[0].max_key)
        self.assertEqual(notes[2].max_key, 14)

    def test_a_note_without_a_pitch_is_not_on_the_line(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 64, 14, index=1)]
        notes[1].pitch = None
        self.assertEqual(run(notes), 0)


class CountingTheSameThingAsTheEditorTests(unittest.TestCase):
    """報告的三個數字要和 `melody_line.breaks` 完全一樣。"""

    def counts(self, notes):
        config = settings_for_style('eather')
        groups = _clusters_uncached(notes, config.onset_tolerance_ms)
        mine = _melody_line_breaks(notes, groups, config)
        theirs = ML.summary(ML.breaks(notes))
        return mine, (theirs['direction'], theirs['repeat'], theirs['slope'])

    def test_a_plain_direction_break(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 64, 14, index=1)]
        mine, theirs = self.counts(notes)
        self.assertEqual(mine, theirs)
        self.assertEqual(mine[0], 1)

    def test_a_repeated_pitch_in_another_lane(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 67, 16, index=1),
                 lane_note(500, 60, 12, index=2)]
        mine, theirs = self.counts(notes)
        self.assertEqual(mine, theirs)
        self.assertGreaterEqual(mine[1], 1)

    def test_a_step_that_is_far_too_wide(self):
        # 一個全音該走 1 格，這裡走了 8 格
        notes = [lane_note(0, 60, 6, index=0), lane_note(250, 62, 14, index=1)]
        mine, theirs = self.counts(notes)
        self.assertEqual(mine, theirs)
        self.assertEqual(mine[2], 1)

    def test_hidden_notes_do_not_add_phantom_breaks(self):
        notes = [lane_note(0, 60, 14, index=0), lane_note(250, 62, 15, index=1),
                 lane_note(120, 90, 3, index=2, hidden=True),
                 lane_note(380, 91, 4, index=3, hidden=True)]
        mine, theirs = self.counts(notes)
        self.assertEqual(mine, theirs)
        self.assertEqual(mine, (0, 0, 0))

    def test_an_empty_chart(self):
        config = settings_for_style('eather')
        self.assertEqual(_melody_line_breaks([], [], config), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
