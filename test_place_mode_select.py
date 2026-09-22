"""放置模式也要選得到東西。

以前左鍵一律放音符，所以選取之後才能用的功能（框選、整批位移開始／結束、
改寬度、裁殘響…）全都得先切回編輯模式。現在：點空白處＝放，點既有音符＝選，
Ctrl／Shift＝照編輯模式那一套。
"""

import unittest

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel


def chart(count=6):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    notes = []
    for i in range(count):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 500, i * 500 + 200, 200
        n.pitch = 60 + i
        n.hand = 0
        n.min_key, n.max_key = 5 + (i % 3), 7 + (i % 3)
        n.note_type = 0
        n.velocity = 90
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class PlaceModeSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self):
        v = ChartView()
        v.resize(600, 800)
        v.load_model(chart())
        v.set_note_input_mode(True)
        # _hit_test 看的是繪製時建立的可見清單，所以要先畫一次
        v.grab()
        self._v = v
        return v

    def press(self, view, pos, modifiers=Qt.NoModifier):
        event = QMouseEvent(QEvent.MouseButtonPress, pos, Qt.LeftButton,
                            Qt.LeftButton, modifiers)
        view.mousePressEvent(event)

    def click(self, view, pos, modifiers=Qt.NoModifier):
        """完整的一次點擊。選取是在放開時才成立（框選也走同一條路）。"""
        self.press(view, pos, modifiers)
        view.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, pos,
                                           Qt.LeftButton, Qt.NoButton, modifiers))

    def point_on_note(self, view, note):
        rect = view._note_rect(note)
        self.assertIsNotNone(rect, '這顆音符要在畫面上')
        point = QPoint(int(rect.center().x()), int(rect.center().y()))
        # 畫面下緣是鋼琴鍵盤，壓在那裡的音符點不到（放置也一樣被擋）
        if view._placement_blocked_at(point):
            self.skipTest('這顆音符被鍵盤欄擋住')
        return point

    def visible_note(self, view, wanted=1):
        """第 n 顆點得到的音符（跳過壓在鍵盤欄底下的）。"""
        found = 0
        for note in view.model.notes_tree:
            rect = view._note_rect(note)
            if rect is None:
                continue
            point = QPoint(int(rect.center().x()), int(rect.center().y()))
            if view._placement_blocked_at(point) or view._hit_test(point) is not note:
                continue
            found += 1
            if found >= wanted:
                return note, point
        self.skipTest('畫面上找不到點得到的音符')

    def empty_point(self, view):
        """畫面上沒有任何音符的位置。"""
        for x in range(20, view.width() - 20, 7):
            for y in range(40, view.height() - 40, 11):
                p = QPoint(x, y)
                if view._hit_test(p) is None and not view._placement_blocked_at(p):
                    return p
        self.skipTest('找不到空白處')

    def test_clicking_an_existing_note_selects_it(self):
        v = self.view()
        target, point = self.visible_note(v)
        before = len(v.model.notes_tree)
        self.click(v, point)
        self.assertEqual(len(v.model.notes_tree), before, '不可以多出一顆')
        self.assertIn(target.idx, v.selected)

    def test_clicking_empty_space_still_places(self):
        v = self.view()
        before = len(v.model.notes_tree)
        self.click(v, self.empty_point(v))
        self.assertEqual(len(v.model.notes_tree), before + 1, '空白處還是要放得出來')

    def test_ctrl_click_toggles_selection(self):
        v = self.view()
        target, point = self.visible_note(v)
        self.press(v, point, Qt.ControlModifier)
        self.assertIn(target.idx, v.selected)
        self.press(v, point, Qt.ControlModifier)
        self.assertNotIn(target.idx, v.selected)

    def test_ctrl_drag_on_empty_space_starts_a_marquee(self):
        v = self.view()
        before = len(v.model.notes_tree)
        self.press(v, self.empty_point(v), Qt.ControlModifier)
        self.assertTrue(v._is_rubbing, 'Ctrl 拖空地要能框選')
        self.assertEqual(len(v.model.notes_tree), before, '框選不可以放音符')

    def test_no_placement_preview_over_an_existing_note(self):
        v = self.view()
        _target, point = self.visible_note(v)
        move = QMouseEvent(QEvent.MouseMove, point, Qt.NoButton,
                           Qt.NoButton, Qt.NoModifier)
        v.mouseMoveEvent(move)
        self.assertIsNone(v._note_input_hover,
                          '游標在音符上時按下去是選取，不該畫放置預覽')

    def test_the_preview_comes_back_over_empty_space(self):
        v = self.view()
        point = self.empty_point(v)
        move = QMouseEvent(QEvent.MouseMove, point, Qt.NoButton,
                           Qt.NoButton, Qt.NoModifier)
        v.mouseMoveEvent(move)
        self.assertIsNotNone(v._note_input_hover)

    def test_selection_tools_work_in_place_mode(self):
        """選好之後，那些「只對選取動作」的功能要能直接用。"""
        v = self.view()
        target, point = self.visible_note(v, 2)
        self.click(v, point)
        self.assertIn(target.idx, v.selected)
        v.toggle_tap_hold_selected()
        self.assertEqual(int(target.note_type), 2, '點擊 → 長條')
        width_before = int(target.max_key) - int(target.min_key) + 1
        v.toggle_width_selected()
        width_after = int(target.max_key) - int(target.min_key) + 1
        self.assertNotEqual(width_after, width_before, '寬度要切換')
        self.assertIn(width_after, (2, 3))

    def test_edge_nudge_works_in_place_mode(self):
        """剛加的「整批位移開始／結束」也要能在放置模式下用。"""
        v = self.view()
        target, point = self.visible_note(v, 3)
        self.click(v, point)
        start_before = int(target.start)
        moved = v.nudge_selected_edge_time('start', 50)
        self.assertTrue(moved)
        self.assertEqual(int(target.start), start_before + 50)


if __name__ == '__main__':
    unittest.main()
