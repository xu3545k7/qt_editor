import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, min_key=0, max_key=0, note_type=0, idx=0):
    note = GNote(None, idx)
    note.start = start
    note.end = end
    note.gate = end - start
    note.min_key = min_key
    note.max_key = max_key
    note.note_type = note_type
    return note


class AlignNotesEdgeTests(unittest.TestCase):
    def test_align_start_keeps_end_fixed(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        self.assertEqual(NoteModel.align_notes_edge([a, b], 'start', 200), 2)
        # end 不變，start 對齊到 200，gate 同步
        self.assertEqual((a.start, a.end, a.gate), (200, 500, 300))
        self.assertEqual((b.start, b.end, b.gate), (200, 400, 200))

    def test_align_end_keeps_start_fixed(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        self.assertEqual(NoteModel.align_notes_edge([a, b], 'end', 600), 2)
        self.assertEqual((a.start, a.end, a.gate), (100, 600, 500))
        self.assertEqual((b.start, b.end, b.gate), (300, 600, 300))

    def test_align_start_clamped_below_end(self):
        # 目標值 >= end：start 不能越過 end，最多 end-1
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', 500), 1)
        self.assertEqual((n.start, n.end, n.gate), (199, 200, 1))

    def test_align_end_clamped_above_start(self):
        # 目標值 <= start：end 至少 start+1
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'end', 50), 1)
        self.assertEqual((n.start, n.end, n.gate), (100, 101, 1))

    def test_align_start_negative_clamped_to_zero(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', -50), 1)
        self.assertEqual((n.start, n.end, n.gate), (0, 200, 200))

    def test_no_change_returns_zero(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', 100), 0)
        self.assertEqual((n.start, n.end), (100, 200))

    def test_invalid_target_is_noop(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'middle', 150), 0)
        self.assertEqual((n.start, n.end), (100, 200))


if __name__ == '__main__':
    unittest.main()
