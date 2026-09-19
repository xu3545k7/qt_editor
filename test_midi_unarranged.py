# -*- coding: utf-8 -*-
"""選了「不轉換」的 MIDI，不該被半推半就地排掉譜。

未排譜的 MIDI 只有音高檢視有意義，所以任何往其他檢視走的動作都會撞到
「要不要現在轉譜？」這個問句。問題是排譜會**重寫每一顆音符的鍵道**，而
觸發它的動作都很輕：切換檢視的快捷鍵、開分割檢視、時間均分開關。

這裡釘住三件事：
  1. 排譜可以復原（含 `midi_unarranged` 本身，不然 undo 完音符回到未排譜的
     暫時鍵道、程式卻還以為排過了）。
  2. 那個問句預設停在「否」。
  3. 開分割檢視不會自己去踩到它——只是想開兩格對照左右手而已。
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


def midi_model(unarranged=True):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    m.file_format = 'midi'
    m.midi_unarranged = unarranged
    notes = []
    for i in range(8):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 300, i * 300 + 200, 200
        n.min_key, n.max_key, n.note_type = i % 6, i % 6 + 2, 0
        n.hand = i % 2
        n.track, n.channel = i % 2, 0
        n.pitch, n.velocity = 55 + i * 2, 90
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class UndoTests(unittest.TestCase):
    """排譜要能整步復原。"""

    def test_the_snapshot_records_the_unarranged_flag(self):
        m = midi_model()
        m.push_history()
        self.assertIn('midi_unarranged', m.undo_stack[-1],
                      '快照沒有記下「還沒排譜」，undo 之後模式會對不上')

    def test_undo_puts_the_model_back_into_midi_mode(self):
        m = midi_model()
        m.push_history()
        m.midi_unarranged = False           # 排譜做的事
        for n in m.notes_tree:
            n.min_key, n.max_key = 20, 22
        self.assertTrue(m.undo())
        self.assertTrue(m.midi_unarranged,
                        'undo 之後音符回到未排譜狀態，旗標卻還說排過了')

    def test_undo_restores_the_lanes_too(self):
        m = midi_model()
        m.push_history()
        for n in m.notes_tree:
            n.min_key, n.max_key = 20, 22
        m.undo()
        self.assertNotEqual([n.min_key for n in m.notes_tree], [20] * 8)

    def test_an_arranged_chart_stays_arranged_through_undo(self):
        m = midi_model(unarranged=False)
        m.push_history()
        m.notes_tree[0].min_key = 9
        m.undo()
        self.assertFalse(m.midi_unarranged)


class ArrangeIsUndoableTests(unittest.TestCase):
    """`_arrange_now` 以前完全沒有 push_history——排下去就回不來了。"""

    def setUp(self):
        self.win = window()
        self.model = midi_model()
        self.win.view.model = self.model
        self._trim = NoteModel.trim_pedal_sustained_holds
        self._arrange = NoteModel.smart_arrange_midi

        def fake_arrange(model, style=None):
            for n in model.notes_tree:
                n.min_key, n.max_key = 20, 22
            model.midi_unarranged = False
            return {}

        NoteModel.trim_pedal_sustained_holds = lambda model, *a, **k: 0
        NoteModel.smart_arrange_midi = fake_arrange

    def tearDown(self):
        NoteModel.trim_pedal_sustained_holds = self._trim
        NoteModel.smart_arrange_midi = self._arrange

    def test_it_arranges(self):
        self.assertTrue(self.win._arrange_now())
        self.assertFalse(self.model.midi_unarranged)

    def test_it_leaves_exactly_one_undo_step(self):
        depth = len(self.model.undo_stack)
        self.win._arrange_now()
        self.assertEqual(len(self.model.undo_stack), depth + 1,
                         '排譜不是一個乾淨的復原步驟')

    def test_undoing_it_gives_back_the_unarranged_midi(self):
        self.win._arrange_now()
        self.assertTrue(self.model.undo(), '排譜之後沒有東西可以 undo')
        self.assertTrue(self.model.midi_unarranged)
        self.assertNotEqual([n.min_key for n in self.model.notes_tree], [20] * 8)


class PromptTests(unittest.TestCase):
    """預設按鈕：Enter 一下不該就把整份譜排掉。"""

    def setUp(self):
        self.win = window()
        self.win.view.model = midi_model()
        self.calls = []
        self._q = mw.QMessageBox.question

        def question(parent, title, text, buttons=0, default=0):
            self.calls.append((buttons, default))
            return QMessageBox.No

        mw.QMessageBox.question = staticmethod(question)

    def tearDown(self):
        mw.QMessageBox.question = self._q

    def test_the_default_button_is_no(self):
        self.win._require_arranged_for_view()
        self.assertTrue(self.calls, '沒有問就直接排譜了')
        _buttons, default = self.calls[0]
        self.assertEqual(default, QMessageBox.No,
                         '預設停在「是」，切換檢視的快捷鍵按下去 Enter 就排掉了')

    def test_saying_no_leaves_the_chart_alone(self):
        self.assertFalse(self.win._require_arranged_for_view())
        self.assertTrue(self.win.view.model.midi_unarranged)

    def test_an_arranged_chart_is_never_asked(self):
        self.win.view.model = midi_model(unarranged=False)
        self.assertTrue(self.win._require_arranged_for_view())
        self.assertEqual(self.calls, [])

    def test_time_uniform_still_checks_before_toggling(self):
        """這個開關有兩份同名定義，沒有守門的那份把有守門的蓋掉了。"""
        names = mw.MainWindow._on_time_uniform_toggle.__code__.co_names
        self.assertIn('_require_arranged_for_view', names,
                      '時間均分開關沒有經過確認就切走了')


class SplitViewTests(unittest.TestCase):
    """開分割檢視不該自己去觸發轉譜。

    `_enable_split` 給新格子指定 'measure'（讓兩格形態不同），但未排譜的
    MIDI 只能待在音高模式——那行指定會被擋回去並發出 arrange_required，
    使用者只是想開兩格對照左右手，卻收到「要不要轉譜」。
    """

    def setUp(self):
        self.win = window()
        # 一定要 show()：`_enable_split` 是照 isVisible() 決定要開哪一格的，
        # 視窗沒顯示的話兩格都算「沒開」，走到的分支和實際操作不一樣。
        self.win.show()
        self.model = midi_model()
        for pane in self.win._panes:
            pane.load_model(self.model)
            pane.set_view_mode('pitch')
        self.win._disable_split()
        self.assertEqual(len(self.win._visible_panes()), 1,
                         '前置條件：先收成單格')
        self.asked = []
        self._q = mw.QMessageBox.question

        def question(parent, title, text, *a, **k):
            self.asked.append(text)
            return QMessageBox.No

        mw.QMessageBox.question = staticmethod(question)
        self.signals = []
        for pane in self.win._panes:
            pane.arrange_required.connect(self._note_signal)

    def _note_signal(self):
        self.signals.append(1)

    def tearDown(self):
        mw.QMessageBox.question = self._q
        for pane in self.win._panes:
            try:
                pane.arrange_required.disconnect(self._note_signal)
            except TypeError:
                pass
        self.win._disable_split()
        self.win.hide()

    def test_opening_the_split_does_not_ask_to_arrange(self):
        self.win._enable_split()
        self.assertEqual(self.asked, [],
                         '只是開個分割檢視就跳出轉譜對話框：%s' % self.asked)

    def test_it_does_not_even_emit_the_request(self):
        self.win._enable_split()
        self.assertEqual(self.signals, [])

    def test_both_panes_stay_in_pitch_view(self):
        self.win._enable_split()
        panes = self.win._visible_panes()
        self.assertEqual(len(panes), 2, '分割沒開起來')
        for i, pane in enumerate(panes):
            self.assertTrue(pane.pitch_mode, '第 %d 格離開了音高模式' % i)

    def test_the_chart_is_still_unarranged_afterwards(self):
        self.win._enable_split()
        self.assertTrue(self.model.midi_unarranged)

    def test_an_arranged_chart_still_gets_two_different_views(self):
        """一般譜面的行為不能被改掉：分割就是要看兩種形態。"""
        arranged = midi_model(unarranged=False)
        for pane in self.win._panes:
            pane.load_model(arranged)
        self.win._disable_split()
        self.win._panes[self.win._active_pane].set_view_mode('pitch')
        self.win._enable_split()
        modes = [p.pitch_mode for p in self.win._visible_panes()]
        self.assertEqual(len(modes), 2)
        self.assertNotEqual(modes[0], modes[1], '兩格形態一樣，分割就沒意義了')


if __name__ == '__main__':
    unittest.main()
