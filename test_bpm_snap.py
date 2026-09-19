"""改 BPM（不調整音符）後的「吸附小節線」：B 貼合新拍格優先，對不上退回 A 整數小節。"""

import random
import unittest

from qt_editor import bpm_snap as B
from qt_editor.models import GNote, NoteModel


def _chart(onsets, chart_bpm=120.0, length_s=60.0):
    m = NoteModel.create_new('t', chart_bpm, length_s, 4)
    notes = []
    for k, t in enumerate(onsets):
        n = GNote(None, k)
        n.start, n.end, n.gate = int(round(t)), int(round(t)) + 100, 100
        n.pitch, n.velocity, n.hand, n.note_type = 60, 90, 0, 0
        n.min_key, n.max_key = 5, 7
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def _retempo(m, first, last, bpm):
    anchor, _ = m.get_measure_time_range(first)
    _, old_end = m.get_measure_time_range(last)
    for mi in range(first, last + 1):
        m.set_measure_bpm(mi, bpm, adjust_notes=False)
    return float(anchor), float(old_end)


class BpmSnapTests(unittest.TestCase):
    def test_grid_fit_when_bpm_was_wrong(self):
        beat = 60000.0 / 89.6
        m = _chart([k * beat for k in range(96)])
        last = m.count_measures() - 1
        anchor, old_end = _retempo(m, 0, last, 90.0)
        r = B.snap(m, 0, last, anchor, old_end)
        self.assertEqual(r.method, 'grid')
        self.assertAlmostEqual(r.factor, 89.6 / 90.0, delta=0.002)
        self.assertLess(r.error_after, r.error_before)
        self.assertAlmostEqual(m.notes_tree[60].start, 60 * 60000 / 90.0, delta=30)
        self.assertIn('貼合新拍格', B.describe(r))

    def test_already_on_grid_does_nothing(self):
        m = _chart([k * 500.0 for k in range(68)])
        anchor, old_end = _retempo(m, 0, 16, 90.0)
        before = [n.start for n in m.notes_tree]
        r = B.snap(m, 0, 16, anchor, old_end)
        self.assertEqual((r.method, r.factor), ('grid', 1.0))
        self.assertEqual(before, [n.start for n in m.notes_tree])

    def test_falls_back_to_whole_bars(self):
        rng = random.Random(7)
        # 自由速度：音符不在任何格子上，這段 17 個 120 小節 = 34 秒 = 12.75 個 90 小節
        onsets = sorted(rng.uniform(0, 33900) for _ in range(120))
        m = _chart(onsets)
        anchor, old_end = _retempo(m, 0, 16, 90.0)
        r = B.snap(m, 0, 16, anchor, old_end)
        self.assertEqual(r.method, 'bars')
        self.assertEqual(r.bars, 13)
        self.assertAlmostEqual(r.factor, 13 * (4 * 60000 / 90.0) / 34000.0, places=3)
        self.assertIn('13 個新小節', B.describe(r))

    def test_too_far_is_left_alone(self):
        self.assertEqual(B.fit_bars(0.0, 5000.0, 2000.0).method, 'none')   # 2.5 小節 → ±20%
        self.assertEqual(B.fit_bars(0.0, 26000.0, 2000.0).method, 'bars')  # 13 小節剛好

    def test_scale_leaves_notes_before_anchor_and_beats(self):
        m = _chart([0, 1000, 5000, 9000])
        bars = [m.get_measure_time_range(i) for i in range(6)]
        m.pedal_spans = [[500.0, 900.0], [6000.0, 8000.0]]
        B.scale_from(m, 4000.0, 1.05)
        self.assertEqual([n.start for n in m.notes_tree], [0, 1000, 5050, 9250])
        self.assertEqual(m.pedal_spans, [[500.0, 900.0], [6100.0, 8200.0]])
        self.assertEqual(bars, [m.get_measure_time_range(i) for i in range(6)])


if __name__ == '__main__':
    unittest.main()
