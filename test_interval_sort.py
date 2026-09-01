import unittest

from qt_editor.models import GNote
from qt_editor.smart_chart import SmartChartSettings, interval_sort_notes


def make_note(idx, start, pitch, min_key=0, width=3):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.min_key = min_key
    n.max_key = min_key + width - 1
    n.pitch = pitch
    n.hand = 0
    return n


def lanes(notes):
    return [(n.pitch, n.min_key, n.max_key) for n in notes]


class IntervalSortTests(unittest.TestCase):
    """右鍵「智慧排序」用的簡化智慧路徑：間距由音程決定，不是音高排名等距。"""

    def test_bigger_interval_gets_a_bigger_lane_gap(self):
        # 舊的 Alloc 是依音高「排名」等距分配，所以這三顆會等距；
        # 依音程排的話 C4→C#4（1 半音）必須明顯比 C#4→C5（11 半音）窄。
        notes = [make_note(0, 0, 60), make_note(1, 0, 61), make_note(2, 0, 72)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        gap_small = notes[1].min_key - notes[0].min_key
        gap_large = notes[2].min_key - notes[1].min_key
        self.assertLess(gap_small, gap_large)

    def test_same_pitch_lands_on_the_same_lane(self):
        notes = [make_note(0, 0, 60), make_note(1, 1000, 60, min_key=20),
                 make_note(2, 2000, 67, min_key=3)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        self.assertEqual(notes[0].min_key, notes[1].min_key)

    def test_simultaneous_notes_never_overlap(self):
        notes = [make_note(i, 0, 60 + i) for i in range(4)]   # 四個相鄰半音
        report = interval_sort_notes(notes, lane_min=0, lane_max=27)
        ordered = sorted(notes, key=lambda n: n.min_key)
        for left, right in zip(ordered, ordered[1:]):
            self.assertLess(left.max_key, right.min_key)
        self.assertEqual(report['unresolved'], 0)

    def test_notes_at_different_times_may_share_lanes(self):
        # 不同時間發聲的音符不該互相推開
        notes = [make_note(0, 0, 60), make_note(1, 5000, 61)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        self.assertLessEqual(abs(notes[1].min_key - notes[0].min_key), 1)

    def test_layout_is_compressed_to_fit_the_given_window(self):
        notes = [make_note(0, 0, 40), make_note(1, 0, 60), make_note(2, 0, 100)]
        interval_sort_notes(notes, lane_min=5, lane_max=15)
        for n in notes:
            self.assertGreaterEqual(n.min_key, 5)
            self.assertLessEqual(n.max_key, 15)

    def test_natural_spacing_is_not_stretched_to_fill_the_window(self):
        # 範圍很寬時不硬拉開——音程本身的間距就是目標值
        notes = [make_note(0, 0, 60), make_note(1, 0, 62)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        self.assertLess(notes[1].min_key, 6)

    def test_widths_are_preserved_unless_narrowing_is_needed(self):
        notes = [make_note(0, 0, 60, width=3), make_note(1, 0, 72, width=3)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        for n in notes:
            self.assertEqual(n.max_key - n.min_key + 1, 3)

    def test_narrowing_stops_at_min_note_width(self):
        config = SmartChartSettings()
        notes = [make_note(i, 0, 60 + i) for i in range(3)]
        interval_sort_notes(notes, config, lane_min=0, lane_max=27)
        for n in notes:
            self.assertGreaterEqual(n.max_key - n.min_key + 1, config.min_note_width - 1)

    def test_stays_inside_the_keyboard(self):
        config = SmartChartSettings()
        notes = [make_note(i, 0, 21 + i * 13) for i in range(6)]
        interval_sort_notes(notes, config, lane_min=0, lane_max=27)
        for n in notes:
            self.assertGreaterEqual(n.min_key, 0)
            self.assertLessEqual(n.max_key, config.total_lanes - 1)

    def test_notes_without_pitch_are_left_alone(self):
        plain = make_note(0, 0, 60, min_key=9)
        plain.pitch = None
        pitched = [make_note(1, 0, 65), make_note(2, 0, 77)]
        before = (plain.min_key, plain.max_key)
        interval_sort_notes([plain] + pitched, lane_min=0, lane_max=27)
        self.assertEqual((plain.min_key, plain.max_key), before)

    def test_single_note_is_a_no_op(self):
        notes = [make_note(0, 0, 60, min_key=7)]
        report = interval_sort_notes(notes, lane_min=0, lane_max=27)
        self.assertEqual(report['moved'], 0)
        self.assertEqual(notes[0].min_key, 7)

    def test_pitch_order_is_preserved_left_to_right(self):
        notes = [make_note(0, 0, 55), make_note(1, 0, 60),
                 make_note(2, 0, 64), make_note(3, 0, 71)]
        interval_sort_notes(notes, lane_min=0, lane_max=27)
        ordered = sorted(notes, key=lambda n: n.pitch)
        self.assertEqual([n.min_key for n in ordered],
                         sorted(n.min_key for n in ordered))


if __name__ == '__main__':
    unittest.main()
