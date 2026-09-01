import json
import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, min_key=4, max_key=6, note_type=0, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.min_key = min_key
    n.max_key = max_key
    n.note_type = note_type
    return n


class AlignUndoSaveTests(unittest.TestCase):
    def _model(self):
        m = NoteModel()
        m.notes_tree = [
            make_note(100, 500, note_type=2, idx=0),
            make_note(300, 400, idx=1),
        ]
        m.rebuild_display_cache()
        return m

    def test_undo_restores_after_align(self):
        m = self._model()
        original = [(int(n.start), int(n.end)) for n in m.notes_tree]
        self.assertFalse(m.dirty)

        # 模擬 align_selected_edge 的核心：先存歷史再改動
        m.push_history()
        changed = m.align_notes_edge(m.notes_tree, 'start', 200)
        self.assertEqual(changed, 2)
        self.assertTrue(m.dirty)
        self.assertEqual([(int(n.start), int(n.end)) for n in m.notes_tree],
                         [(200, 500), (200, 400)])

        # 回復上一動
        self.assertTrue(m.undo())
        self.assertEqual([(int(n.start), int(n.end)) for n in m.notes_tree], original)

    def test_undo_restores_end_align(self):
        m = self._model()
        original = [(int(n.start), int(n.end)) for n in m.notes_tree]
        m.push_history()
        m.align_notes_edge(m.notes_tree, 'end', 600)
        self.assertEqual([(int(n.start), int(n.end)) for n in m.notes_tree],
                         [(100, 600), (300, 600)])
        self.assertTrue(m.undo())
        self.assertEqual([(int(n.start), int(n.end)) for n in m.notes_tree], original)

    def test_save_json_reflects_align(self):
        m = self._model()
        m.push_history()
        m.align_notes_edge(m.notes_tree, 'start', 200)

        fd, path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        try:
            m.save_json(path)
            self.assertFalse(m.dirty)  # 存檔後 dirty 應清除
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            starts = sorted(int(n['startTime']) for n in data['notes'])
            self.assertEqual(starts, [200, 200])
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main()
