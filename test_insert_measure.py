import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.note_type = 0
    n.min_key = 4
    n.max_key = 6
    return n


class InsertMeasureTests(unittest.TestCase):
    """右鍵「在此新增小節」用的 insert_measure：delete_measure 的反向操作。"""

    def _model(self, bpm=120.0, sec=40.0):
        # 120 BPM / 4 拍 → 一小節 2000ms
        m = NoteModel.create_new('t', bpm, sec, 4)
        m.notes_tree = [make_note(2000 * i, 2000 * i + 100, i) for i in range(10)]
        m.rebuild_display_cache()
        return m

    def test_bar_length_assumption(self):
        m = self._model()
        self.assertEqual(m.get_measure_time_range(0), (0, 2000))
        self.assertEqual(m.get_measure_at_ms(2500), 1)

    def test_insert_pushes_later_notes_by_one_bar(self):
        m = self._model()
        before = m.measure_count()
        self.assertTrue(m.insert_measure(3))

        starts = [n.start for n in m.notes]
        # 前三小節不動，第 3 小節起整段往後推 2000ms
        self.assertEqual(starts[:3], [0, 2000, 4000])
        self.assertEqual(starts[3:], [8000, 10000, 12000, 14000, 16000, 18000, 20000])
        self.assertEqual(m.measure_count(), before + 1)

    def test_inserted_measure_is_empty_and_boundaries_line_up(self):
        m = self._model()
        m.insert_measure(3)
        start_ms, end_ms = m.get_measure_time_range(3)
        self.assertEqual((start_ms, end_ms), (6000, 8000))
        self.assertEqual([n for n in m.notes if start_ms <= n.start < end_ms], [])
        # 第 4 小節就是原本的第 3 小節
        self.assertEqual(m.get_measure_time_range(4), (8000, 10000))

    def test_note_lengths_survive_the_insert(self):
        m = self._model()
        m.notes_tree[5].end = m.notes_tree[5].start + 1500
        m.notes_tree[5].gate = 1500
        m.rebuild_display_cache()
        m.insert_measure(2)
        moved = m.notes[5]
        self.assertEqual(moved.end - moved.start, 1500)
        self.assertEqual(moved.gate, 1500)

    def test_insert_then_delete_round_trips(self):
        m = self._model()
        starts = [n.start for n in m.notes]
        count = m.measure_count()
        m.insert_measure(4)
        m.delete_measure(4)
        self.assertEqual([n.start for n in m.notes], starts)
        self.assertEqual(m.measure_count(), count)

    def test_insert_at_or_past_the_end_appends(self):
        m = self._model()
        count = m.measure_count()
        starts = [n.start for n in m.notes]
        self.assertTrue(m.insert_measure(count + 5))
        self.assertEqual([n.start for n in m.notes], starts)   # 沒有音符被推動
        self.assertEqual(m.measure_count(), count + 1)

    def test_insert_at_zero_pushes_everything(self):
        m = self._model()
        m.insert_measure(0)
        self.assertEqual([n.start for n in m.notes][:3], [2000, 4000, 6000])
        self.assertEqual(m.get_measure_time_range(0), (0, 2000))

    def test_insert_uses_the_given_bpm_for_the_new_bar_length(self):
        m = self._model()
        m.insert_measure(2, new_bpm=240.0)      # 一小節 1000ms
        self.assertEqual(m.get_measure_time_range(2), (4000, 5000))
        self.assertEqual([n.start for n in m.notes][:4], [0, 2000, 5000, 7000])

    def test_delete_measure_returns_deleted_note_count(self):
        m = self._model()
        self.assertEqual(m.delete_measure(3), 1)


if __name__ == '__main__':
    unittest.main()
