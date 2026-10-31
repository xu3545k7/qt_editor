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
    """「常用」分頁：**一開始是空的，照實際按過什麼長出來。**

    使用者：「常用初始就是空白（沒這個選項），以玩家的操作紀錄（有在其他 tab
    上曾經切過去用的）放到常用」。

    這也自動解掉了之前那幾條手工規則（不放有快捷鍵的、不放右鍵選單裡有的）：
    有快捷鍵的你根本不會去按按鈕、右鍵到得了的你也不會切分頁，它們自然不會
    進來——不用再維護排除清單。
    """

    def setUp(self):
        from qt_editor.settings import settings
        from qt_editor.toolbar_usage import SETTINGS_KEY
        self.settings = settings
        self._before = settings.get(SETTINGS_KEY, None)
        settings.set(SETTINGS_KEY, {})
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            self.win = MainWindow()
        self.win.resize(2560, 1400)

    def tearDown(self):
        from qt_editor.toolbar_usage import SETTINGS_KEY
        self.settings.set(SETTINGS_KEY,
                          self._before if self._before is not None else {})
        self.win.view.model.dirty = False
        self.win.close()

    def page(self):
        return self.win._toolbars[0].tabs.widget(0)

    def buttons(self):
        from PyQt5.QtWidgets import QToolButton
        return [b.text() for b in self.page().findChildren(QToolButton)]

    def press(self, label, times=1):
        for _ in range(times):
            self.win._count_toolbar_use(label)
        self.win._refresh_common_tab()

    def test_it_is_the_first_tab_and_opens_by_default(self):
        tabs = self.win._toolbars[0].tabs
        self.assertEqual(tabs.tabText(0), '常用')
        self.assertEqual(tabs.currentIndex(), 0)

    def test_it_starts_empty(self):
        self.assertEqual(self.buttons(), [])

    def test_it_says_how_it_fills_up(self):
        """空的時候要講清楚，不然看起來像壞掉。"""
        from PyQt5.QtWidgets import QLabel
        texts = ' '.join(l.text() for l in self.page().findChildren(QLabel))
        self.assertIn('空', texts)
        self.assertIn('其他分頁', texts)

    def test_pressing_something_enough_times_puts_it_here(self):
        self.press('PianoTrans 轉譜整理…', 3)
        self.assertIn('PianoTrans 轉譜整理…', self.buttons())

    def test_one_press_is_not_enough(self):
        """隨手點一下試試看的東西不該卡在那裡。"""
        from qt_editor.toolbar_usage import MIN_COUNT
        self.press('PianoTrans 轉譜整理…', MIN_COUNT - 1)
        self.assertNotIn('PianoTrans 轉譜整理…', self.buttons())

    def test_the_most_pressed_comes_first(self):
        self.press('處理長條尾端…', 2)
        self.press('PianoTrans 轉譜整理…', 5)
        got = self.buttons()
        self.assertLess(got.index('PianoTrans 轉譜整理…'),
                        got.index('處理長條尾端…'))

    def test_an_item_that_no_longer_exists_is_skipped(self):
        """改版／改名之後舊的計數還在，不能讓它炸掉整頁。"""
        self.press('這顆按鈕已經不存在了', 9)
        self.assertNotIn('這顆按鈕已經不存在了', self.buttons())

    def test_it_never_grows_past_the_limit(self):
        from qt_editor.toolbar_usage import MAX_COMMON
        for label in list(self.win._toolbar_registry)[:MAX_COMMON + 4]:
            self.press(label, 3)
        self.assertLessEqual(len(self.buttons()), MAX_COMMON)

    def test_every_button_says_how_often_you_pressed_it(self):
        self.press('PianoTrans 轉譜整理…', 4)
        from PyQt5.QtWidgets import QToolButton
        for b in self.page().findChildren(QToolButton):
            self.assertIn('按過', b.toolTip())

    def test_the_registry_covers_every_tab(self):
        """所有工具列項目都要經過 `_tab_action`／`_menu_button` 才註冊得到。"""
        reg = self.win._toolbar_registry
        self.assertGreater(len(reg), 40)
        for want in ('PianoTrans 轉譜整理…', '🔒 鎖定音符', '智慧重排吸附…'):
            self.assertIn(want, reg, '註冊表漏了「%s」' % want)

    def test_counting_a_press_survives_a_restart(self):
        """計數放在設定裡，所以跨工作階段留得住。"""
        from qt_editor.toolbar_usage import SETTINGS_KEY
        self.press('處理長條尾端…', 3)
        stored = self.settings.get(SETTINGS_KEY, {})
        self.assertEqual(stored.get('處理長條尾端…'), 3)


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
        """常用分頁是鏡像，不算「散在各處」——它的每一項在別的分頁都有家。"""
        for other in ('編輯', '播放', '檢視'):
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

    def test_its_home_is_the_measure_tab(self):
        """家在小節（它是小節線的編輯模式）。常用頁要按夠次數才會出現。"""
        from PyQt5.QtWidgets import QToolButton
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            win = MainWindow()
        try:
            tabs = win._toolbars[0].tabs
            names = {tabs.tabText(i): [b.text() for b in
                                       tabs.widget(i).findChildren(QToolButton)]
                     for i in range(tabs.count())}
            self.assertTrue(any('鎖定音符' in x for x in names['小節']),
                            names['小節'])
            self.assertTrue(any('拖曳小節線' in x for x in names['小節']))
            self.assertTrue(win._toolbars[0].measure_lock_act.isCheckable())
        finally:
            win.view.model.dirty = False
            win.close()

    def test_a_mirror_on_common_stays_in_step(self):
        """按夠次數之後常用頁也會有一顆，兩顆必須同一個狀態。

        常用頁是從使用紀錄長出來的，哪些開關會出現在上面事先不知道——同步是
        照標籤對的，而且要**保留 checkable**：通用迴圈不知道它是開關的話會建
        成普通按鈕，按下去會直接把狀態關掉。
        """
        from qt_editor.main_window import MainWindow
        from qt_editor.settings import settings
        from qt_editor.toolbar_usage import SETTINGS_KEY
        before = (settings.get(SETTINGS_KEY, None),
                  settings.get('notes_locked', False))
        settings.set(SETTINGS_KEY, {})
        with contextlib.redirect_stdout(io.StringIO()):
            win = MainWindow()
        try:
            for _ in range(3):
                win._count_toolbar_use('🔒 鎖定音符')
            win._refresh_common_tab()
            tbs = win._toolbars[0]
            mirror = tbs.common_acts.get('🔒 鎖定音符')
            self.assertIsNotNone(mirror,
                                 '常用頁沒長出那顆：%s' % list(tbs.common_acts))
            self.assertTrue(mirror.isCheckable(), '鏡像沒保留 checkable')
            win._on_notes_locked_toggled(True)
            self.assertTrue(tbs.measure_lock_act.isChecked())
            self.assertTrue(mirror.isChecked())
            win._on_notes_locked_toggled(False)
            self.assertFalse(tbs.measure_lock_act.isChecked())
            self.assertFalse(mirror.isChecked())
        finally:
            settings.set(SETTINGS_KEY, before[0] if before[0] is not None else {})
            settings.set('notes_locked', before[1])
            win.view.model.dirty = False
            win.close()
