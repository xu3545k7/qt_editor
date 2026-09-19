"""改寬度：右緣不動、動左緣。

右緣（`max_key`）是排序與視覺的權威——音高高的音右緣要更右。改寬度時動右緣，
排好的高低關係就被打亂了；使用者手動改寬度時也一直是左緣在動（見操作紀錄）。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView, TOTAL_GAME_KEYS
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def note(idx, start, min_key, max_key, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 200, 200
    n.min_key, n.max_key = min_key, max_key
    n.pitch, n.velocity, n.hand, n.note_type = pitch, 90, 0, 0
    return n


class WidthEditTests(unittest.TestCase):
    def setUp(self):
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.notes = [note(0, 0, 10, 12), note(1, 500, 20, 22, pitch=72)]
        self.model.notes_tree = self.notes
        self.model.rebuild_display_cache()
        self.view = ChartView()
        self.view.load_model(self.model)

    def lanes(self):
        return [(n.min_key, n.max_key) for n in self.model.notes_tree]

    def test_narrowing_keeps_the_right_edge(self):
        self.view.selected = {n.idx for n in self.model.notes_tree}
        self.view.set_width_selected(2)
        self.assertEqual(self.lanes(), [(11, 12), (21, 22)])

    def test_widening_grows_to_the_left(self):
        self.view.selected = {self.notes[0].idx}
        self.view.set_width_selected(2)
        self.view.set_width_selected(3)
        self.assertEqual(self.lanes()[0], (10, 12))

    def test_left_wall_pushes_right_instead(self):
        self.model.notes_tree = [note(0, 0, 0, 1)]
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.selected = {0}
        self.view.set_width_selected(3)
        self.assertEqual(self.lanes(), [(0, 2)])

    def test_right_wall_stays_inside(self):
        self.model.notes_tree = [note(0, 0, TOTAL_GAME_KEYS - 2, TOTAL_GAME_KEYS - 1)]
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.selected = {0}
        self.view.set_width_selected(3)
        self.assertEqual(self.lanes(), [(TOTAL_GAME_KEYS - 3, TOTAL_GAME_KEYS - 1)])

    def test_undo_restores_widths(self):
        self.view.selected = {self.notes[0].idx}
        self.view.set_width_selected(2)
        self.model.undo()
        self.assertEqual([(n.min_key, n.max_key) for n in self.model.notes_tree][0], (10, 12))


if __name__ == '__main__':
    unittest.main()
