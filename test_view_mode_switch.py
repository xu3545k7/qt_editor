# -*- coding: utf-8 -*-
"""檢視分頁上的檢視模式下拉。

使用者：「檢視也要可以切小節均分和時間均分以及音高模式」，接著
「依次顯示一種，用下拉選單選就好」。

小節分頁上原本就有一顆循環鈕（因為這個模式也決定改 BPM／拍號時小節內的
拍點怎麼重排），但它要按兩下才到得了第三個模式。現在檢視分頁有一顆顯示
目前模式的下拉、檢視選單也有一組，三邊共用同一個狀態。
"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication, QToolButton

from qt_editor import i18n
from qt_editor import main_window as mw
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])

MODES = ('measure', 'time', 'pitch')


def chart():
    """一份排過的小譜面：三個模式都走得到。"""
    model = NoteModel.create_new('t', 120.0, 20.0, 4)
    notes = []
    for i in range(8):
        n = GNote(None, i)
        n.start, n.gate = 500 * i, 400
        n.end = n.start + n.gate
        n.pitch, n.hand, n.note_type = 60 + i, 0, 0
        n.min_key, n.max_key = 10 + i, 12 + i
        n.track = 1
        notes.append(n)
    model.notes_tree = notes
    model.rebuild_display_cache()
    return model


def midi_model():
    """選了「不轉換」的 MIDI：只有音高檢視有意義。"""
    model = chart()
    model.midi_unarranged = True
    return model


class SwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def setUp(self):
        self.win.view.model = chart()
        self.win.view.set_view_mode('measure')
        self.win._refresh_view_mode_action()

    def tbs(self):
        return self.win._toolbars[0]

    def checked(self, acts):
        return {m for m, a in acts.items() if a.isChecked()}

    # ── 在不在、長什麼樣 ──────────────────────────────────────────
    def test_every_toolbar_has_all_three(self):
        for tbs in self.win._toolbars:
            self.assertEqual(set(tbs.view_mode_acts), set(MODES))
            for act in tbs.view_mode_acts.values():
                self.assertTrue(act.isCheckable(), '要看得出現在是哪一個')

    def view_page(self):
        tbs = self.tbs()
        for i in range(tbs.tabs.count()):
            if tbs.tabs.tabText(i) == i18n.t('tb_tab_view'):
                return tbs.tabs.widget(i)
        self.fail('找不到檢視分頁')

    def test_the_picker_sits_on_the_view_tab(self):
        """不是只有建出來——要真的在檢視那一頁上。"""
        btn = self.tbs().view_mode_btn
        self.assertIn(btn, self.view_page().findChildren(QToolButton))

    def test_it_is_one_dropdown_not_three_buttons(self):
        """使用者：「依次顯示一種，用下拉選單選就好」。"""
        tbs = self.tbs()
        menu = tbs.view_mode_btn.menu()
        self.assertIsNotNone(menu, '沒有下拉選單')
        self.assertEqual(list(menu.actions()),
                         [tbs.view_mode_acts[m] for m in MODES])
        on_page = {b.defaultAction() for b in self.view_page().findChildren(QToolButton)}
        for mode, act in tbs.view_mode_acts.items():
            self.assertNotIn(act, on_page, '%s 又被攤成一顆按鈕了' % mode)

    def test_the_button_shows_the_mode_it_is_in(self):
        for mode, label_key, _tip in MainWindow.VIEW_MODES:
            self.tbs().view_mode_acts[mode].trigger()
            self.assertIn(i18n.t(label_key), self.tbs().view_mode_btn.text())

    def test_each_one_explains_itself(self):
        for act in self.tbs().view_mode_acts.values():
            self.assertTrue(act.toolTip(), act.text())
        self.assertTrue(self.tbs().view_mode_btn.toolTip())

    def test_the_view_menu_offers_the_same_three(self):
        self.assertEqual(set(self.win._view_mode_menu_acts), set(MODES))

    # ── 切換 ──────────────────────────────────────────────────────
    def test_picking_one_switches_the_view(self):
        for mode in ('time', 'pitch', 'measure'):
            self.tbs().view_mode_acts[mode].trigger()
            self.assertEqual(self.win.view.view_mode, mode)

    def test_only_one_is_ever_lit(self):
        for mode in MODES:
            self.tbs().view_mode_acts[mode].trigger()
            self.assertEqual(self.checked(self.tbs().view_mode_acts), {mode})

    def test_clicking_the_lit_one_leaves_it_lit(self):
        """QAction 本來是開關，點第二下會把自己關掉——那會變成沒有打勾。"""
        acts = self.tbs().view_mode_acts
        acts['time'].trigger()
        acts['time'].trigger()
        self.assertEqual(self.win.view.view_mode, 'time')
        self.assertEqual(self.checked(acts), {'time'})

    def test_the_menu_switches_too(self):
        self.win._view_mode_menu_acts['pitch'].trigger()
        self.assertEqual(self.win.view.view_mode, 'pitch')

    # ── 三邊共用同一個狀態 ────────────────────────────────────────
    def test_the_cycle_button_follows(self):
        self.tbs().view_mode_acts['pitch'].trigger()
        self.assertIn('音高', self.tbs().view_mode_act.text())

    def test_the_cycle_button_moves_the_three(self):
        self.win._cycle_view_mode()                 # measure → time
        self.assertEqual(self.checked(self.tbs().view_mode_acts), {'time'})
        self.assertEqual(self.checked(self.win._view_mode_menu_acts), {'time'})

    def test_the_menu_and_the_buttons_agree(self):
        self.win._view_mode_menu_acts['time'].trigger()
        self.assertEqual(self.checked(self.tbs().view_mode_acts), {'time'})
        self.tbs().view_mode_acts['measure'].trigger()
        self.assertEqual(self.checked(self.win._view_mode_menu_acts), {'measure'})

    def test_changing_the_view_elsewhere_still_lights_the_right_one(self):
        """快捷鍵／分割同步都直接走 ChartView，按鈕得靠 refresh 跟上。"""
        self.win.view.set_view_mode('pitch')
        self.win._refresh_view_mode_action()
        self.assertEqual(self.checked(self.tbs().view_mode_acts), {'pitch'})


class UnarrangedTests(unittest.TestCase):
    """未排譜的 MIDI 只能待在音高模式，離開前要先問。"""

    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def setUp(self):
        self.win.view.model = midi_model()
        self.win.view.set_view_mode('pitch')
        self.win._refresh_view_mode_action()
        self.calls = []
        self._ask = MainWindow.ask_arrange_options

        def ask(win, intro='', allow_skip=False, fresh_import=True):
            self.calls.append(intro)
            return None, None                       # 使用者按取消
        MainWindow.ask_arrange_options = ask

    def tearDown(self):
        MainWindow.ask_arrange_options = self._ask

    def acts(self):
        return self.win._toolbars[0].view_mode_acts

    def test_leaving_pitch_asks_first(self):
        self.acts()['measure'].trigger()
        self.assertTrue(self.calls, '沒問就把整份譜的鍵道重寫掉了')

    def test_cancelling_snaps_back_to_pitch(self):
        self.acts()['time'].trigger()
        self.assertEqual(self.win.view.view_mode, 'pitch')
        self.assertEqual({m for m, a in self.acts().items() if a.isChecked()},
                         {'pitch'})

    def test_staying_in_pitch_never_asks(self):
        self.acts()['pitch'].trigger()
        self.assertEqual(self.calls, [])

    def test_the_gate_is_the_same_one_as_the_cycle_button(self):
        names = mw.MainWindow._pick_view_mode.__code__.co_names
        self.assertIn('_require_arranged_for_view', names)


class TranslationTests(unittest.TestCase):
    def test_every_language_has_the_new_labels(self):
        keys = ('tb_view_mode_label', 'tb_view_mode_tip', 'menu_view_mode',
                *(k for _m, k, _tip in MainWindow.VIEW_MODES),
                *(k for _m, _l, k in MainWindow.VIEW_MODES))
        for lang, table in i18n._STRINGS.items():
            for key in keys:
                self.assertIn(key, table, '%s 少了 %s' % (lang, key))


if __name__ == '__main__':
    unittest.main()
