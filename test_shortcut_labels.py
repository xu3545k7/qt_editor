"""按鈕、選單、下拉框上標出目前的快捷鍵；偏好設定改鍵之後馬上跟著變。"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.settings import settings  # noqa: E402

KEYS = ('shortcut_note_input', 'shortcut_play_pause', 'shortcut_measures_bpm',
        'shortcut_cycle_view')


class ShortcutLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        cls.saved = {k: settings._data.get(k) for k in KEYS}
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            settings._data[k] = v
        cls.win.apply_shortcut_settings()
        cls.win.close()

    def setUp(self):
        for k, v in self.saved.items():
            settings._data[k] = v
        settings._data['shortcut_note_input'] = '2'
        settings._data['shortcut_play_pause'] = 'Space'
        settings._data['shortcut_cycle_view'] = '1'
        settings._data['shortcut_measures_bpm'] = ''
        self.win.apply_shortcut_settings()
        self.tbs = self.win._toolbars[0]

    def rebind(self, key, value):
        settings._data[key] = value
        self.win.apply_shortcut_settings()

    def test_buttons_show_their_keys(self):
        self.assertTrue(self.tbs.note_input_act.text().endswith('(2)'))
        self.assertIn('Space', self.tbs.pause_act.text())
        self.assertIn('Tab', self.tbs.preview_act.text())
        self.assertIn('(1)', self.tbs.view_mode_act.text())
        self.assertIn('快捷鍵：2', self.tbs.note_input_act.toolTip())

    def test_fixed_view_keys_are_shown_too(self):
        texts = [b.text() for b in self.tbs.root.findChildren(type(self.tbs.pause_act))]
        self.assertTrue(any('Ctrl+P' in x for x in texts), texts)
        self.assertTrue(any(x.endswith('(S)') for x in texts), texts)
        self.assertTrue(any('Shift+A' in x for x in texts), texts)

    def test_rebinding_updates_the_label(self):
        self.rebind('shortcut_note_input', 'N')
        self.assertTrue(self.tbs.note_input_act.text().endswith('(N)'))
        self.assertNotIn('(2)', self.tbs.note_input_act.text())

    def test_clearing_removes_the_label(self):
        base = self.tbs.note_input_act.property('sc_text')
        self.rebind('shortcut_note_input', '')
        self.assertEqual(self.tbs.note_input_act.text(), base)

    def test_changing_the_button_text_keeps_the_key(self):
        from qt_editor.i18n import t
        self.win._set_pause_text('tb_resume')
        self.assertEqual(self.tbs.pause_act.text(), f"{t('tb_resume')} (Space)")
        self.win._set_pause_text('tb_pause')
        self.win._refresh_view_mode_action()
        self.assertIn('(1)', self.tbs.view_mode_act.text())

    def test_combos_get_it_in_the_tooltip(self):
        self.assertIn('[', self.tbs.dur_combo.toolTip())
        self.assertIn(']', self.tbs.dur_combo.toolTip())
        self.assertIn('Q', self.tbs.hand_combo.toolTip())
        self.assertTrue(self.tbs.dur_combo.toolTip().startswith('音符時值'),
                        '原本的說明要留著')

    def test_buttons_without_shortcuts_are_left_alone(self):
        """MIDI 鋼琴開關沒有快捷鍵；它的喇叭要維持 🔊／🔇，不能被蓋成文字。"""
        from qt_editor.i18n import t
        speaker = self.tbs.vol['hit']['action']
        self.assertEqual(self.tbs.hit_act.text(), t('tb_hit_sound'))
        self.assertIn(speaker.text(), ('🔊', '🔇'))
        self.assertIsNone(self.tbs.hit_act.property('sc_names'))

    def test_the_piano_speaker_follows_the_midi_toggle(self):
        speaker = self.tbs.vol['hit']['action']
        original = self.tbs.hit_act.isChecked()
        try:
            self.tbs.hit_act.setChecked(False)
            self.assertFalse(speaker.isChecked())
            self.assertEqual(speaker.text(), '🔇')
            speaker.setChecked(True)                  # 反過來按喇叭
            self.assertTrue(self.tbs.hit_act.isChecked())
            self.assertTrue(self.win._hit_sound_persistent)
            self.assertEqual(speaker.text(), '🔊')
        finally:
            self.tbs.hit_act.setChecked(original)

    def test_menus_show_user_keys_in_the_shortcut_column(self):
        from qt_editor.i18n import t
        self.rebind('shortcut_measures_bpm', 'Ctrl+B')
        menu_texts = []
        for act in self.win.menuBar().actions():
            if act.menu() is not None:
                menu_texts += [a.text() for a in act.menu().actions()]
        self.assertIn(f"{t('tb_measures_bpm')}\tCtrl+B", menu_texts)
        # 選單文字裡本來就寫了固定鍵的，不要再重複補一次
        self.assertFalse(any(x.count('Ctrl+P') > 1 for x in menu_texts))


if __name__ == '__main__':
    unittest.main()
