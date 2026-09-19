"""放置模式：游標在底部鍵盤（或側邊的踏板／強弱欄）上時不能放音符。

鍵盤那塊的 y 換算成時間還是有效的，以前在鍵盤上點一下就會在判定線底下
（畫面上被鍵盤蓋住的地方）放一顆音符，使用者根本看不到自己誤放了。
"""

import unittest

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import NoteModel

_app = QApplication.instance() or QApplication([])


class PlacementAreaTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.follow_to_ms(4000.0)
        self.view.set_note_input_mode(True)

    def ev(self, kind, x, y):
        button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else button
        return QMouseEvent(kind, QPointF(x, y), button, buttons, Qt.NoModifier)

    def click(self, x, y):
        self.view.mousePressEvent(self.ev(QEvent.MouseButtonPress, x, y))
        self.view.mouseReleaseEvent(self.ev(QEvent.MouseButtonRelease, x, y))

    def test_clicking_the_keyboard_places_nothing(self):
        for mode in ('measure', 'time', 'pitch'):
            self.view.set_view_mode(mode)
            top = self.view._keyboard_top_py()
            for y in (top + 1, top + 20, self.view.height() - 2):
                self.click(600, y)
            self.assertEqual(self.model.notes_tree, [], mode)

    def test_clicking_the_chart_still_places(self):
        self.click(600, self.view._keyboard_top_py() - 40)
        self.assertEqual(len(self.model.notes_tree), 1)

    def test_no_ghost_over_the_keyboard(self):
        view = self.view
        view.mouseMoveEvent(self.ev(QEvent.MouseMove, 600, view._keyboard_top_py() - 40))
        self.assertIsNotNone(view._note_input_hover)
        view.mouseMoveEvent(self.ev(QEvent.MouseMove, 600, view._keyboard_top_py() + 10))
        self.assertIsNone(view._note_input_hover, '移到鍵盤上預覽要消失')
        self.assertEqual(view.cursor().shape(), Qt.ArrowCursor)
        view.mouseMoveEvent(self.ev(QEvent.MouseMove, 600, view._keyboard_top_py() - 40))
        self.assertEqual(view.cursor().shape(), Qt.CrossCursor)

    def test_leaving_the_view_clears_the_ghost(self):
        view = self.view
        view.mouseMoveEvent(self.ev(QEvent.MouseMove, 600, 200))
        view.leaveEvent(QEvent(QEvent.Leave))
        self.assertIsNone(view._note_input_hover)

    def test_pattern_drag_does_not_start_on_the_keyboard(self):
        view = self.view
        view.set_note_input_mode(False)
        view.set_view_mode('pitch')
        view.set_pattern_mode(True)
        view.mousePressEvent(self.ev(QEvent.MouseButtonPress, 600, view._keyboard_top_py() + 10))
        self.assertIsNone(view._pattern_drag)


if __name__ == '__main__':
    unittest.main()
