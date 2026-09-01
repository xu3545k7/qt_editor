"""踏板欄與強弱欄的每一種編輯都要能 Ctrl+Z。

這些欄位是直接在畫布上拖曳的，很容易一手滑就改掉東西；沒有 undo 的話只能手動
調回去。新增/刪除踏板區間以前**完全沒有壓歷史**，是這一輪補上的。
"""

import unittest

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, velocity, hand=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = 60 + idx
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    return n


class LaneUndoTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.view.resize(1200, 720)
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = [make_note(0, 0, 70), make_note(1, 1000, 90),
                        make_note(2, 500, 60, hand=1)]
        m.rebuild_display_cache()
        self.model = m
        self.view.load_model(m)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        for _ in range(2):
            self.view.zoom(0.5)
        self.view.window_start_unit = self.view.mapper.ms_to_unit(0.0)

    # ── 小工具 ────────────────────────────────────────────────────
    def _press(self, x, y):
        self.view.mousePressEvent(QMouseEvent(
            QEvent.MouseButtonPress, QPointF(x, y),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def _move(self, x, y):
        self.view.mouseMoveEvent(QMouseEvent(
            QEvent.MouseMove, QPointF(x, y),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def _release(self, x, y):
        self.view.mouseReleaseEvent(QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(x, y),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    def _pedal_x(self):
        return self.view._pedal_lane_px() / 2.0

    def _dyn_rect(self, hand):
        return self.view._dyn_lane_rect(hand)

    # ── 踏板 ──────────────────────────────────────────────────────
    def test_creating_a_pedal_span_by_dragging_is_undoable(self):
        y1, y2 = self.view._ms_to_py(500.0), self.view._ms_to_py(2500.0)
        x = self._pedal_x()
        self._press(x, y1)
        self._move(x, y2)
        self._release(x, y2)
        self.assertEqual(len(self.model.pedal_spans), 1)

        self.model.undo()
        self.assertEqual(self.model.pedal_spans, [])

    def test_deleting_a_pedal_span_by_clicking_is_undoable(self):
        self.model.pedal_spans = [[500.0, 2500.0]]
        y = self.view._ms_to_py(1500.0)
        x = self._pedal_x()
        self._press(x, y)
        self._release(x, y)
        self.assertEqual(self.model.pedal_spans, [])

        self.model.undo()
        self.assertEqual([[int(a), int(b)] for a, b in self.model.pedal_spans],
                         [[500, 2500]])

    def test_dragging_a_pedal_edge_is_undoable(self):
        self.model.pedal_spans = [[1000.0, 3000.0]]
        x = self._pedal_x()
        y_end = self.view._ms_to_py(3000.0)
        self._press(x, y_end)
        self._move(x, self.view._ms_to_py(4000.0))
        self._release(x, self.view._ms_to_py(4000.0))
        self.assertGreater(self.model.pedal_spans[0][1], 3500.0)

        self.model.undo()
        self.assertEqual([[int(a), int(b)] for a, b in self.model.pedal_spans],
                         [[1000, 3000]])

    def test_clicking_empty_pedal_space_leaves_no_history_entry(self):
        # 點在沒有區間的空白處：什麼都沒改，就不該塞一筆 undo
        depth = len(self.model.undo_stack)
        y = self.view._ms_to_py(1500.0)
        x = self._pedal_x()
        self._press(x, y)
        self._release(x, y)
        self.assertEqual(len(self.model.undo_stack), depth)

    # ── 強弱曲線 ──────────────────────────────────────────────────
    def test_placing_a_dynamic_mark_by_dragging_is_undoable(self):
        rect = self._dyn_rect(0)
        x = rect.left() + rect.width() - 3
        y = self.view._ms_to_py(1000.0)
        self._press(x, y)
        self._release(x, y)
        self.assertEqual(len(self.model.dynamics_marks(0)), 1)

        self.model.undo()
        self.assertEqual(self.model.dynamics_marks(0), [])

    def test_a_whole_drag_collapses_into_one_undo_step(self):
        rect = self._dyn_rect(0)
        x = rect.left() + rect.width() - 3
        self._press(x, self.view._ms_to_py(500.0))
        for ms in (800.0, 1100.0, 1400.0):
            self._move(x, self.view._ms_to_py(ms))
        self._release(x, self.view._ms_to_py(1400.0))
        self.assertEqual(len(self.model.dynamics_marks(0)), 1,
                         '整段拖曳只留一個記號')

        self.model.undo()
        self.assertEqual(self.model.dynamics_marks(0), [],
                         '一次 undo 就該把整段拖曳還原')

    def test_seeding_from_notes_is_undoable(self):
        self.view._ctx_seed_dynamics(0, 0.0)
        self.assertEqual(len(self.model.dynamics_marks(0)), 2)

        self.model.undo()
        self.assertEqual(self.model.dynamics_marks(0), [])

    def test_applying_dynamics_is_undoable(self):
        before = [n.velocity for n in self.model.notes_tree]
        self.model.dynamics_add(0, 0, 120, ramp=False)
        self.view._ctx_apply_dynamics()
        self.assertNotEqual([n.velocity for n in self.model.notes_tree], before)

        self.model.undo()
        self.assertEqual([n.velocity for n in self.model.notes_tree], before)

    def test_adding_a_mark_from_the_menu_is_undoable(self):
        self.view._ctx_add_dynamic(1, 1500.0, 96, True)
        self.assertEqual(len(self.model.dynamics_marks(1)), 1)

        self.model.undo()
        self.assertEqual(self.model.dynamics_marks(1), [])

    def test_clearing_marks_is_undoable(self):
        self.model.dynamics_add(0, 0, 64)
        self.model.dynamics_add(1, 0, 96)
        self.view._ctx_clear_dynamics(None)
        self.assertEqual(self.model.dynamics_clear(), 0)

        self.model.undo()
        self.assertTrue(self.model.dynamics_marks(0))
        self.assertTrue(self.model.dynamics_marks(1))

    def test_removing_a_single_mark_is_undoable(self):
        self.model.dynamics_add(0, 1000, 64)
        self.view._ctx_remove_dynamic(0, 1000.0)
        self.assertEqual(self.model.dynamics_marks(0), [])

        self.model.undo()
        self.assertEqual(len(self.model.dynamics_marks(0)), 1)

    def test_removing_where_there_is_no_mark_leaves_no_history_entry(self):
        depth = len(self.model.undo_stack)
        self.view._ctx_remove_dynamic(0, 9999.0)
        self.assertEqual(len(self.model.undo_stack), depth)


if __name__ == '__main__':
    unittest.main()
