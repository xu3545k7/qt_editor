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


class CommonTabTests(unittest.TestCase):
    """「常用」分頁：內容是照**操作紀錄**排的，不是照直覺。

    統計 37 份 logs/oplog-*.jsonl、159 個工作階段、15459 次操作：

        shift_selected_keys    11839  76.6%   方向鍵，不需要按鈕
        set_width_selected       809   5.2%
        delete_selected          584   3.8%
        shift_selected_time      548   3.5%
        set_hand_selected        387   2.5%
        set_type_selected        309   2.0%
        _begin_hold_tail_drag    280   1.8%   拖曳
        shift_selected_pitch     162   1.0%

    前 8 名＝98.4%，而這些**在工具列上一顆按鈕都沒有**；原本最大的「編輯」
    分頁整頁在服務放置音符，而 `_place_note_at` 整份紀錄只出現 9 次。

    寬度：這一頁約 1914px ＋ 右上角音量 244px ＝ 2158px，而實機螢幕可用寬度
    2560px（2560x1440、96 DPI、縮放 1.0）。放得下。
    """

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()
        cls.win.resize(2560, 1400)
        # **只載一次**。每個測試都 `_load_model_all` 的話，整個測試套件跑完
        # 之後行程結束不了（2242 個測試全過，然後卡在結束）——那條路會留下
        # 自動存檔計時器之類的東西。同檔的其他類別也是只載一次。
        cls.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        cls.win._load_model_all(cls.model)

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def setUp(self):
        m = self.model
        m.notes_tree = [note(1000 + 500 * i, 60, idx=i) for i in range(4)]
        for n in m.notes_tree:
            n.min_key, n.max_key, n.hand = 4, 6, 0
        m.rebuild_display_cache()
        self.win.view.rebuild_mapper()
        self.win.view.select_all()

    def tearDown(self):
        self.model.dirty = False

    def page(self):
        return self.win._toolbars[0].tabs.widget(0)

    def buttons(self):
        from PyQt5.QtWidgets import QToolButton
        return {b.text(): b for b in self.page().findChildren(QToolButton)}

    def test_it_is_the_first_tab(self):
        tabs = self.win._toolbars[0].tabs
        self.assertEqual(tabs.tabText(0), '常用')

    def test_it_is_what_opens_by_default(self):
        self.assertEqual(self.win._toolbars[0].tabs.currentIndex(), 0)

    def test_the_top_actions_all_have_a_button(self):
        """紀錄裡前幾名的操作都要一鍵可達。"""
        labels = ' '.join(self.buttons())
        for want in ('寬 2', '寬 3', '右手', '左手', '刪除', '複製'):
            self.assertIn(want, labels, '「%s」沒有按鈕' % want)
        self.assertIsNotNone(self.win._toolbars[0].common_type_combo,
                             '改類型沒有控制項')

    def test_width_buttons_apply_to_the_selection(self):
        self.buttons()['寬 2'].click()
        self.assertTrue(all(n.max_key - n.min_key + 1 == 2
                            for n in self.model.notes_tree))
        self.buttons()['寬 3'].click()
        self.assertTrue(all(n.max_key - n.min_key + 1 == 3
                            for n in self.model.notes_tree))

    def test_hand_buttons_apply_to_the_selection(self):
        self.buttons()['● 左手'].click()
        self.assertTrue(all(n.hand == 1 for n in self.model.notes_tree))
        self.buttons()['● 右手'].click()
        self.assertTrue(all(n.hand == 0 for n in self.model.notes_tree))

    def test_the_type_picker_applies_and_resets(self):
        """它是一個**動作**不是狀態，所以選完要跳回提示文字。

        停在上次選的那一項會讓人以為選取的音符現在就是那個類型。
        """
        combo = self.win._toolbars[0].common_type_combo
        combo.setCurrentIndex(3)                 # Long
        self.win._on_common_type_changed(3)
        self.assertTrue(all(n.note_type == 2 for n in self.model.notes_tree))
        self.assertEqual(combo.currentIndex(), 0)
        self.assertIn('改類型', combo.currentText())

    def test_the_type_picker_offers_every_type(self):
        combo = self.win._toolbars[0].common_type_combo
        labels = [combo.itemText(i) for i in range(combo.count())]
        for want in ('Tap', 'Soft', 'Long', 'Staccato', 'Slide', 'Trill'):
            self.assertIn(want, labels)

    def test_delete_and_duplicate_work(self):
        before = len(self.model.notes_tree)
        self.buttons()['⧉ 複製'].click()
        self.assertGreater(len(self.model.notes_tree), before)
        self.win.view.select_all()
        self.buttons()['🗑 刪除'].click()
        self.assertEqual(len(self.model.notes_tree), 0)

    def test_both_pause_buttons_change_together(self):
        """「播放」分頁和「常用」分頁各有一顆，漏掉一顆就會看到兩種字。"""
        self.win._set_pause_text('tb_resume')
        for tbs in self.win._toolbars:
            self.assertEqual(tbs.pause_act.text(),
                             tbs.common_pause_act.text())
        self.win._set_pause_text('tb_pause')

    def test_the_page_fits_the_screen(self):
        """2560px 的螢幕要放得下這一頁加上右上角的音量區塊。"""
        from PyQt5.QtWidgets import QComboBox, QLabel, QToolButton
        total = 0
        for w in self.page().findChildren(object):
            if isinstance(w, (QToolButton, QComboBox, QLabel)) and not w.isHidden():
                total += w.sizeHint().width()
        self.assertLess(total + 244, 2560,
                        '常用頁 %d px ＋音量 244 放不進 2560' % total)
