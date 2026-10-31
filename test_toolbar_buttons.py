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

    def test_the_drag_is_gated_on_the_flag_alone(self):
        """只看這個旗標。**不再綁「小節均分模式」**。

        使用者：「拖曳小節線不再是時間均分專用」。那個模式管的是小節內拍點
        怎麼重排，和「能不能拖小節線」是兩件事。
        """
        import inspect

        from qt_editor import chart_view
        source = inspect.getsource(chart_view.ChartView.mousePressEvent)
        self.assertIn('if self.barline_drag_enabled:', source)
        self.assertNotIn('self.time_uniform and self.barline_drag_enabled', source)

    def test_it_defaults_to_off(self):
        """使用者：「默認都是關閉」。判定只看離小節線 6 像素，開著很容易誤觸。"""
        from qt_editor.settings import settings
        before = settings.get('barline_drag_enabled', None)
        try:
            settings.set('barline_drag_enabled', None)
            self.assertFalse(bool(settings.get('barline_drag_enabled', False)))
        finally:
            if before is not None:
                settings.set('barline_drag_enabled', before)


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
    """「常用」分頁：常做 **而且沒有快捷鍵** 的事。

    內容照操作紀錄排（37 份 logs/oplog-*.jsonl、159 個工作階段、15459 次操作），
    再濾掉已經有鍵的——使用者：「不要把快捷鍵相關的放到常用」。

        set_width_selected      809   有鍵（E 在 2↔3 切換，正好涵蓋）
        delete_selected         584   有鍵（Delete）
        shift_selected_time     548   有鍵（↑／↓）
        set_hand_selected       387   **沒有**（Q 改的是放置的手）
        set_type_selected       309   **一半沒有**（T/H/K 有，Soft/Slide/Trill 沒有）
        shift_selected_pitch    162   有鍵
        duplicate_selected       36   **沒有**
        resolve_hold_tails       19   **沒有**
        _ctx_note_property       13   **沒有**
        chain_slides_selected    12   **沒有**

    存檔／復原／重做／播放／暫停／停止也都有鍵，所以都不放。
    寬度：這一頁 1057px ＋音量 244px，實機螢幕 2560px。
    """

    #: 這些動作有快捷鍵，所以**不該**出現在常用頁
    HAS_SHORTCUT = ('存檔', '復原', '重做', '播放', '暫停', '停止', '刪除',
                    '寬 2', '寬 3')

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()
        cls.win.resize(2560, 1400)
        # **只載一次**。每個測試都 `_load_model_all` 的話，整個測試套件跑完
        # 之後行程結束不了（2242 個測試全過，然後卡在結束）。
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
        self.assertEqual(self.win._toolbars[0].tabs.tabText(0), '常用')

    def test_it_is_what_opens_by_default(self):
        self.assertEqual(self.win._toolbars[0].tabs.currentIndex(), 0)

    def test_nothing_here_already_has_a_shortcut(self):
        """有鍵的放按鈕只是佔空間——手會先按鍵，眼睛不會往上看。"""
        labels = ' '.join(self.buttons())
        for banned in self.HAS_SHORTCUT:
            self.assertNotIn(banned, labels,
                             '「%s」有快捷鍵，不該放在常用頁' % banned)

    def test_the_frequent_keyless_actions_are_here(self):
        labels = ' '.join(self.buttons())
        for want in ('右手', '左手', '複製', '滑奏串接', '長條尾端', '音符屬性'):
            self.assertIn(want, labels, '「%s」沒有按鈕' % want)
        self.assertIsNotNone(self.win._toolbars[0].common_type_combo)

    def test_hand_buttons_apply_to_the_selection(self):
        self.buttons()['● 左手'].click()
        self.assertTrue(all(n.hand == 1 for n in self.model.notes_tree))
        self.buttons()['● 右手'].click()
        self.assertTrue(all(n.hand == 0 for n in self.model.notes_tree))

    def test_the_type_picker_applies_and_resets(self):
        """它是一個**動作**不是狀態，所以選完要跳回提示文字。"""
        combo = self.win._toolbars[0].common_type_combo
        combo.setCurrentIndex(3)                 # Long
        self.win._on_common_type_changed(3)
        self.assertTrue(all(n.note_type == 2 for n in self.model.notes_tree))
        self.assertEqual(combo.currentIndex(), 0)
        self.assertIn('改類型', combo.currentText())

    def test_the_type_picker_shows_which_ones_already_have_keys(self):
        """T/H/K 有鍵、其餘沒有——寫出來才知道哪些可以用鍵代替。"""
        combo = self.win._toolbars[0].common_type_combo
        labels = [combo.itemText(i) for i in range(combo.count())]
        self.assertTrue(any('(T)' in x for x in labels))
        self.assertTrue(any(x == 'Soft' for x in labels), '沒鍵的就不加括號')

    def test_duplicate_works(self):
        before = len(self.model.notes_tree)
        self.buttons()['⧉ 複製'].click()
        self.assertGreater(len(self.model.notes_tree), before)

    def test_the_note_property_button_needs_a_selection(self):
        """沒選東西要講一句，不要默默沒反應。"""
        from unittest import mock
        self.win.view.deselect_all()
        with mock.patch('qt_editor.main_window.QMessageBox.information') as box:
            self.win._common_note_property()
        self.assertTrue(box.called)

    def test_the_page_fits_the_screen(self):
        from PyQt5.QtWidgets import QComboBox, QToolButton
        total = 0
        for w in self.page().findChildren(QToolButton) +                 self.page().findChildren(QComboBox):
            if not w.isHidden():
                total += w.sizeHint().width()
        self.assertLess(total + 244, 2560,
                        '常用頁 %d px ＋音量 244 放不進 2560' % total)


class MeasureTabIsConsolidatedTests(unittest.TestCase):
    """小節相關的全部集中在「小節」分頁。

    使用者：「我需要先在小節相關的功能確切整合，不要散在各處」。原本散在
    五個地方——小節分頁、編輯分頁（拖曳小節線）、檢視分頁（小節均分模式）、
    右鍵選單、工具→樂曲總資訊。

    右鍵的「小節」子選單留在原地：那是對**點到的那一小節**做事，有位置資訊，
    和分頁上「對譜尾／視窗中央那一小節」不是同一回事。
    """

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()
        cls.win.resize(2560, 1400)

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def labels(self, title):
        """分頁上看得到的文字：按鈕本身 ＋ 下拉裡的每一項。"""
        from PyQt5.QtWidgets import QToolButton
        tabs = self.win._toolbars[0].tabs
        for i in range(tabs.count()):
            if tabs.tabText(i) != title:
                continue
            out = []
            for b in tabs.widget(i).findChildren(QToolButton):
                out.append(b.text())
                if b.menu() is not None:
                    out += [a.text() for a in b.menu().actions()]
            return out
        self.fail('沒有「%s」分頁' % title)

    def visible_controls(self, title):
        from PyQt5.QtWidgets import QToolButton
        tabs = self.win._toolbars[0].tabs
        for i in range(tabs.count()):
            if tabs.tabText(i) == title:
                return [b for b in tabs.widget(i).findChildren(QToolButton)
                        if not b.isHidden()]
        self.fail('沒有「%s」分頁' % title)

    def test_the_tab_is_not_a_wall_of_buttons(self):
        """使用者：「小節的 tab 整理一下，太多了」。原本攤平是 12 顆。

        照操作紀錄，小節類的操作**每一個都很少用**（最多的
        `set_measure_bpm_at` 14 次、`delete_measure_at` 12 次、
        `smart_resnap_dialog` 1 次），所以收進下拉不會有「常用卻要多點一下」
        的問題。留在外面的只有兩顆**模式開關**——它們是狀態，收進下拉就看不出
        現在開著還是關著。
        """
        controls = self.visible_controls('小節')
        self.assertLessEqual(len(controls), 6, '還是太多顆：%s'
                             % [b.text() for b in controls])
        toggles = [b for b in controls if b.defaultAction() is not None
                   and b.defaultAction().isCheckable()]
        self.assertTrue(toggles, '模式開關要留在外面看得見')

    def test_the_menu_bar_and_the_tab_share_one_source(self):
        """兩邊各寫一份一定會走鐘（原本的註解就寫著「兩邊不會少一項」）。"""
        flat = {label for _g, label, _s, _t in self.win._measure_entries()}
        grouped = {label for _name, items in self.win._measure_groups()
                   for label, _slot in items}
        self.assertEqual(flat, grouped)

    def test_the_barline_drag_moved_here(self):
        self.assertTrue(any('拖曳小節線' in x for x in self.labels('小節')))
        self.assertFalse(any('拖曳小節線' in x for x in self.labels('編輯')),
                         '編輯分頁還留著一顆')

    def test_the_measure_view_mode_moved_here(self):
        """它不只是檢視——改 BPM／拍號時小節內拍點怎麼重排是看它決定的。"""
        self.assertTrue(any('小節均分' in x for x in self.labels('小節')))
        self.assertFalse(any('小節均分' in x for x in self.labels('檢視')),
                         '檢視分頁還留著一顆')

    def test_the_regenerate_entry_is_here(self):
        self.assertTrue(any('重生小節線' in x for x in self.labels('小節')))

    def test_nothing_measure_ish_is_left_on_other_tabs(self):
        for other in ('常用', '編輯', '播放', '檢視'):
            for label in self.labels(other):
                self.assertNotIn('小節', label,
                                 '「%s」留在「%s」分頁' % (label, other))

    def test_the_tab_still_fits_the_screen(self):
        from PyQt5.QtWidgets import QComboBox, QToolButton
        tabs = self.win._toolbars[0].tabs
        page = [tabs.widget(i) for i in range(tabs.count())
                if tabs.tabText(i) == '小節'][0]
        total = sum(w.sizeHint().width()
                    for w in page.findChildren(QToolButton) + page.findChildren(QComboBox)
                    if not w.isHidden())
        self.assertLess(total + 244, 2560,
                        '小節分頁 %d px ＋音量 244 放不進 2560' % total)


class NotesLockedTests(unittest.TestCase):
    """「鎖定音符」＝只調整小節線時間的狀態。預設關，放在常用分頁。

    開著的時候拖曳小節線：
    ・不會動到音符
    ・自動吸附到最近的**音符終點**（尾拍）
    ・盡量讓算出來的 BPM 落在整數上——差不到 0.1 才取整。AI 轉譜本身的誤差
      遠大於 0.1，所以這等於免費（使用者原話：「除非差 0.1bpm 以上，依 AI
      轉譜可接受誤差來說應該不會」）。
    """

    @classmethod
    def setUpClass(cls):
        from qt_editor.chart_view import ChartView
        cls.view = ChartView()
        cls.view.resize(1200, 720)
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [note(1000 + 500 * i, 60, gate=200, idx=i)
                        for i in range(10)]        # 終點 1200, 1700, 2200…
        m.rebuild_display_cache()
        cls.view.load_model(m)
        cls.view.rebuild_mapper()

    @classmethod
    def tearDownClass(cls):
        cls.view.model.dirty = False
        cls.view.close()

    def setUp(self):
        self.view.notes_locked = False
        self.view._snap_targets_cache = None

    def test_it_is_off_by_default(self):
        from qt_editor.chart_view import ChartView
        self.assertFalse(ChartView().notes_locked)

    def test_there_is_nothing_to_snap_to_when_unlocked(self):
        """沒鎖住的時候格線是基準，不該被音符拉著走。"""
        self.assertEqual(self.view._barline_snap_targets(), [])
        self.assertEqual(self.view._barline_snap(1690.0), (1690.0, None))

    def test_locked_snaps_to_note_ends(self):
        self.view.notes_locked = True
        self.assertIn(1700, self.view._barline_snap_targets())
        got, hit = self.view._barline_snap(1690.0)
        self.assertEqual((got, hit), (1700.0, 1700))

    def test_it_snaps_to_ends_not_starts(self):
        """尾拍＝音符終點。起點 1500 不該是吸附目標。"""
        self.view.notes_locked = True
        self.assertNotIn(1500, self.view._barline_snap_targets())

    def test_it_does_not_drag_you_across_the_room(self):
        self.view.notes_locked = True
        far = 1700 + self.view.BARLINE_SNAP_MS + 10
        got, hit = self.view._barline_snap(float(far))
        self.assertIsNone(hit)
        self.assertEqual(got, float(far))

    def test_a_bpm_close_to_an_integer_becomes_one(self):
        bpm, rounded = self.view._barline_bpm(0, 1950)   # 123.0769
        self.assertTrue(rounded)
        self.assertEqual(bpm, 123.0)

    def test_a_bpm_far_from_an_integer_keeps_its_decimals(self):
        bpm, rounded = self.view._barline_bpm(0, 1800)   # 133.333
        self.assertFalse(rounded)
        self.assertAlmostEqual(bpm, 133.333, places=2)

    def test_the_tolerance_is_a_tenth_of_a_bpm(self):
        self.assertEqual(self.view.BPM_ROUND_TOLERANCE, 0.1)

    def test_an_exact_bpm_stays_exact(self):
        bpm, _rounded = self.view._barline_bpm(0, 2000)
        self.assertEqual(bpm, 120.0)

    def test_the_toggle_is_on_the_common_tab(self):
        """使用者指定放在常用。它沒有快捷鍵，符合那一頁的規則。"""
        from PyQt5.QtWidgets import QToolButton
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            win = MainWindow()
        try:
            page = win._toolbars[0].tabs.widget(0)
            labels = [b.text() for b in page.findChildren(QToolButton)]
            self.assertTrue(any('鎖定音符' in x for x in labels), labels)
            act = win._toolbars[0].notes_locked_act
            self.assertTrue(act.isCheckable())
            self.assertFalse(act.isChecked(), '預設該是關的')
        finally:
            win.view.model.dirty = False
            win.close()
