# -*- coding: utf-8 -*-
"""兩顆新按鈕：工具→音符整理的「PianoTrans 轉譜整理」、檢視分頁的「斜降」。"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import i18n
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def note(start, pitch, gate=1500, note_type=0, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + gate
    n.gate = gate
    n.pitch = pitch
    n.note_type = note_type
    n.hand = 0
    n.track = 1
    n.min_key = n.max_key = (pitch - 21) // 3
    return n


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def test_the_cleanup_sits_under_note_tidying(self):
        groups = dict((name, [label for label, _fn in items])
                      for name, items in self.win._tool_groups())
        self.assertIn('音符整理', groups)
        self.assertTrue(any('PianoTrans' in label for label in groups['音符整理']),
                        groups['音符整理'])

    def test_the_falling_view_button_is_on_the_view_tab(self):
        for tbs in self.win._toolbars:
            self.assertIsNotNone(getattr(tbs, 'game_preview_act', None))
            self.assertTrue(tbs.game_preview_act.isCheckable(), '斜降是開關，不是一次性動作')

    def test_closing_the_window_pops_the_button_back_out(self):
        """視窗被右上角的叉關掉時，按鈕要彈回來，不然按一次就再也打不開。"""
        self.win.toggle_game_preview(True)
        for tbs in self.win._toolbars:
            self.assertTrue(tbs.game_preview_act.isChecked())
        self.win._on_game_preview_closed()
        for tbs in self.win._toolbars:
            self.assertFalse(tbs.game_preview_act.isChecked())
        self.assertIsNone(getattr(self.win, '_game_preview', None))

    def test_toggling_off_closes_the_window(self):
        self.win.toggle_game_preview(True)
        self.assertIsNotNone(getattr(self.win, '_game_preview', None))
        self.win.toggle_game_preview(False)
        self.assertIsNone(getattr(self.win, '_game_preview', None))

    def test_syncing_the_button_does_not_fire_the_toggle_again(self):
        """同步顯示用的 setChecked 不能再觸發一次開關，否則會自己打架。"""
        self.win.toggle_game_preview(False)
        self.win._sync_game_preview_buttons(True)
        self.assertIsNone(getattr(self.win, '_game_preview', None),
                          '只是同步勾選，不該真的開視窗')
        for tbs in self.win._toolbars:
            tbs.game_preview_act.setChecked(False)

    def test_the_cleanup_does_the_same_thing_as_the_automatic_one(self):
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        run = [note(i * 200, 40 + i * 4, idx=i) for i in range(16)]
        model.notes_tree = run
        model.rebuild_display_cache()
        self.win.view.model = model
        self.win._load_model_all(model)
        summary = self.win._fix_ai_transcription()
        self.assertTrue(summary.startswith('AI 轉譜修整'), summary)
        self.assertEqual(len({int(n.hand) for n in run}), 2, '要分成兩隻手')
        self.assertEqual({int(n.track) for n in run}, {1, 2}, '要拆成兩條音軌')
        model.dirty = False


class TranslationTests(unittest.TestCase):
    """三種語言都要有，不然切過去會看到鍵名。"""

    def test_every_language_has_the_new_keys(self):
        for lang, table in i18n._STRINGS.items():
            for key in ('tb_game_preview', 'tb_game_preview_tip'):
                self.assertIn(key, table, '%s 少了 %s' % (lang, key))


if __name__ == '__main__':
    unittest.main()
