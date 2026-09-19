"""整份譜面取消隱藏音符。

右鍵已經有針對選取範圍的版本，但隱藏音符在多數檢視模式下**畫不出來**（只有
音高模式看得到），要一顆一顆選根本選不到，所以要有整份處理的入口。

取消之後它們會變成真的要打的音符，可打音符數會增加——所以先問過再做，而且
要能 undo。
"""

import unittest

from PyQt5.QtWidgets import QApplication, QMessageBox

import qt_editor.main_window as mw
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])
_window = None


def window():
    """共用一個 MainWindow：同一個行程開關多個會 segfault。"""
    global _window
    if _window is None:
        _window = MainWindow()
    return _window


def chart(hidden_count=3, visible_count=5):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    notes = []
    for i in range(visible_count + hidden_count):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 300, i * 300 + 200, 200
        n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
        n.pitch, n.velocity = 60 + i, 90
        n.hidden = i >= visible_count
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class UnhideAllTests(unittest.TestCase):
    def setUp(self):
        self.win = window()
        self.answers = []
        self.asked = []
        self._q = mw.QMessageBox.question
        self._i = mw.QMessageBox.information

        def question(parent, title, text, *a, **k):
            self.asked.append(text)
            return self.answers.pop(0) if self.answers else QMessageBox.No

        def information(parent, title, text, *a, **k):
            self.asked.append(text)

        mw.QMessageBox.question = staticmethod(question)
        mw.QMessageBox.information = staticmethod(information)

    def tearDown(self):
        mw.QMessageBox.question = self._q
        mw.QMessageBox.information = self._i

    def hidden_of(self, model):
        return [n for n in model.notes_tree if getattr(n, 'hidden', False)]

    def test_it_unhides_every_hidden_note(self):
        m = chart(hidden_count=3, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.Yes]
        self.win.unhide_all_dialog()
        self.assertEqual(self.hidden_of(m), [])

    def test_no_note_is_added_or_removed(self):
        m = chart(hidden_count=3, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.Yes]
        self.win.unhide_all_dialog()
        self.assertEqual(len(m.notes_tree), 8, '取消隱藏不該增刪音符')

    def test_saying_no_changes_nothing(self):
        m = chart(hidden_count=3, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.No]
        self.win.unhide_all_dialog()
        self.assertEqual(len(self.hidden_of(m)), 3)

    def test_it_can_be_undone(self):
        m = chart(hidden_count=3, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.Yes]
        self.win.unhide_all_dialog()
        self.assertTrue(m.undo(), 'undo 失敗')
        self.assertEqual(len(self.hidden_of(m)), 3, 'undo 之後隱藏狀態沒回來')

    def test_a_chart_with_none_says_so_and_does_not_ask(self):
        m = chart(hidden_count=0, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.Yes]      # 不該被用到
        self.win.unhide_all_dialog()
        self.assertEqual(len(self.answers), 1, '沒有隱藏音符時不該問')
        self.assertIn('沒有隱藏音符', self.asked[0])

    def test_the_prompt_states_how_many_and_the_new_total(self):
        m = chart(hidden_count=3, visible_count=5)
        self.win.view.model = m
        self.answers = [QMessageBox.Yes]
        self.win.unhide_all_dialog()
        prompt = self.asked[0]
        self.assertIn('3', prompt, '沒有講有幾顆')
        self.assertIn('5', prompt, '沒有講原本可打幾顆')
        self.assertIn('8', prompt, '沒有講變成幾顆')

    def test_it_is_offered_in_the_tools_menu(self):
        labels = [label
                  for _group, items in self.win._tool_groups()
                  for label, _fn in items]
        self.assertTrue(any('隱藏音符' in x for x in labels),
                        '工具選單裡找不到這個功能：%s' % labels)


if __name__ == '__main__':
    unittest.main()
