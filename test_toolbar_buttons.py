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


class BarlineDragToggleTests(unittest.TestCase):
    """小節分頁的「拖曳小節線」開關。

    那個拖曳會改掉前一小節的 BPM，而判定只看游標離小節線 6 像素，所以在小節線
    附近框選很容易誤觸。使用者要一顆開關來武裝／解除它。
    """

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        from qt_editor.settings import settings
        cls.settings = settings
        cls._before = settings.get('barline_drag_enabled', True)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.settings.set('barline_drag_enabled', cls._before)
        cls.win.view.model.dirty = False
        cls.win.close()

    def _acts(self):
        return [tbs.barline_drag_act for tbs in self.win._toolbars
                if getattr(tbs, 'barline_drag_act', None) is not None]

    def test_the_button_exists(self):
        self.assertTrue(self._acts(), '小節分頁沒有「拖曳小節線」這顆')
        self.assertEqual(self._acts()[0].text(), i18n.t('tb_barline_drag'))
        self.assertTrue(self._acts()[0].isCheckable())

    def test_it_defaults_to_on_so_behaviour_does_not_change(self):
        """預設開著：加這顆按鈕不該默默拿掉一個原本就有的功能。"""
        self.assertTrue(all(p.barline_drag_enabled for p in self.win._panes))
        self.assertTrue(self._acts()[0].isChecked())

    def test_turning_it_off_reaches_every_pane(self):
        """每一格各有自己的 ChartView，漏掉一格就會看起來像沒生效。"""
        self.win._on_barline_drag_toggled(False)
        self.assertTrue(all(not p.barline_drag_enabled for p in self.win._panes))
        self.win._on_barline_drag_toggled(True)
        self.assertTrue(all(p.barline_drag_enabled for p in self.win._panes))

    def test_the_choice_is_remembered(self):
        self.win._on_barline_drag_toggled(False)
        self.assertFalse(self.settings.get('barline_drag_enabled', True))
        self.win._on_barline_drag_toggled(True)
        self.assertTrue(self.settings.get('barline_drag_enabled', True))

    def test_every_toolbar_follows(self):
        """分割出獨立視窗時有兩條工具列，勾選狀態要互相跟上。"""
        self.win._on_barline_drag_toggled(False)
        self.assertTrue(all(not a.isChecked() for a in self._acts()))
        self.win._on_barline_drag_toggled(True)
        self.assertTrue(all(a.isChecked() for a in self._acts()))

    def test_the_drag_is_gated_on_the_flag(self):
        """閘門要同時看 time_uniform 和這個旗標。"""
        import inspect

        from qt_editor import chart_view
        source = inspect.getsource(chart_view.ChartView.mousePressEvent)
        self.assertIn('self.time_uniform and self.barline_drag_enabled', source)


class MidiPianoButtonRemovedTests(unittest.TestCase):
    """MIDI 鋼琴那顆從播放分頁移除了。

    右上角音量區塊的 🔊 已經是同一個開關（`_vol_row(action=tbs.hit_act)` 做的
    鏡像），分頁上再放一顆是重複的。但 `tbs.hit_act` 本身要留著——喇叭和
    `_sync_*` 都靠它。
    """

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def test_the_action_still_exists_for_the_speaker_to_mirror(self):
        for tbs in self.win._toolbars:
            self.assertIsNotNone(tbs.hit_act, 'hit_act 被拿掉了，喇叭會壞')

    def test_no_toolbar_button_shows_it_any_more(self):
        from PyQt5.QtWidgets import QToolButton
        labels = []
        for tbs in self.win._toolbars:
            if tbs.root is None:
                continue
            for btn in tbs.root.findChildren(QToolButton):
                act = btn.defaultAction()
                if act is not None:
                    labels.append(act.text())
        self.assertNotIn(i18n.t('tb_hit_sound'), labels,
                         '播放分頁還有「MIDI 鋼琴」那顆按鈕')
