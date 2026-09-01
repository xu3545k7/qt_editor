"""抓住長條的尾端拖曳改長度——放置模式與普通模式都要能用。

音符的 `end` 畫在方塊的**上緣**（時間往下流向判定線），所以「尾巴」是
rect.top()。放置模式下這個判斷要擋在「放一顆新音符」前面，否則點尾巴會變成
在那裡多放一顆。
"""

import unittest

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make(idx, start, end, pitch=60, note_type=2, centre=11):
    n = GNote(None, idx)
    n.start, n.end = start, end
    n.gate = end - start
    n.pitch = pitch
    n.hand = 0
    n.velocity = 90
    n.min_key, n.max_key = centre - 1, centre + 1
    n.note_type = note_type
    return n


class HoldTailDragTests(unittest.TestCase):
    def build(self, notes=None, view_mode='measure', input_mode=False):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 60.0, 4)
        self.model.notes_tree = list(notes or [make(0, 2000, 4000)])
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode(view_mode)
        self.view._note_input_mode = input_mode
        self.paint()
        return self.view

    def paint(self):
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view.render(qp)
        finally:
            qp.end()

    def rect_of(self, note):
        for r, n in self.view._visible:
            if n is note:
                return r
        self.fail('音符沒有被畫出來')

    def tail_point(self, note):
        r = self.rect_of(note)
        return QPoint(int(r.center().x()), int(r.top()))

    def head_point(self, note):
        r = self.rect_of(note)
        return QPoint(int(r.center().x()), int(r.bottom()))

    def press(self, pt):
        self.view.mousePressEvent(QMouseEvent(
            QEvent.MouseButtonPress, pt, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def move(self, pt, held=True):
        self.view.mouseMoveEvent(QMouseEvent(
            QEvent.MouseMove, pt, Qt.NoButton,
            Qt.LeftButton if held else Qt.NoButton, Qt.NoModifier))

    def release(self, pt):
        self.view.mouseReleaseEvent(QMouseEvent(
            QEvent.MouseButtonRelease, pt, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    def drag_tail(self, note, dy):
        pt = self.tail_point(note)
        self.press(pt)
        target = QPoint(pt.x(), pt.y() + dy)
        self.move(target)
        self.release(target)

    # ── 抓取 ────────────────────────────────────────────────────
    def test_the_tail_is_grabbable(self):
        v = self.build()
        note = self.model.notes_tree[0]
        self.assertIs(v._hold_tail_at(self.tail_point(note)), note)

    def test_a_tap_has_no_grabbable_tail(self):
        v = self.build([make(0, 2000, 4000, note_type=0)])
        note = self.model.notes_tree[0]
        self.assertIsNone(v._hold_tail_at(self.tail_point(note)))

    def test_the_middle_of_the_note_is_not_the_tail(self):
        v = self.build()
        note = self.model.notes_tree[0]
        r = self.rect_of(note)
        mid = QPoint(int(r.center().x()), int(r.center().y()))
        self.assertIsNone(v._hold_tail_at(mid))

    def test_a_different_lane_is_not_grabbed(self):
        v = self.build()
        note = self.model.notes_tree[0]
        r = self.rect_of(note)
        far = QPoint(int(r.right()) + 60, int(r.top()))
        self.assertIsNone(v._hold_tail_at(far))

    # ── 兩種模式都要能拉 ────────────────────────────────────────
    def test_it_works_in_normal_mode(self):
        for view_mode in ('measure', 'time', 'pitch'):
            self.build(view_mode=view_mode)
            note = self.model.notes_tree[0]
            self.drag_tail(note, -60)          # 往上 = 拉長
            self.assertGreater(note.gate, 2000, view_mode)

    def test_it_works_in_placement_mode(self):
        for view_mode in ('measure', 'pitch'):
            self.build(view_mode=view_mode, input_mode=True)
            note = self.model.notes_tree[0]
            self.drag_tail(note, -60)
            self.assertGreater(note.gate, 2000, view_mode)

    def test_placement_mode_does_not_add_a_note_when_grabbing_the_tail(self):
        self.build(input_mode=True)
        note = self.model.notes_tree[0]
        self.drag_tail(note, -60)
        self.assertEqual(len(self.model.notes_tree), 1, '抓尾巴不該又放一顆')

    def test_placement_mode_still_places_elsewhere(self):
        v = self.build(input_mode=True)
        note = self.model.notes_tree[0]
        r = self.rect_of(note)
        away = QPoint(int(r.right()) + 80, int(r.top()) - 80)
        self.press(away)
        self.release(away)
        self.assertEqual(len(self.model.notes_tree), 2, '離尾巴遠就該正常放音符')

    # ── 長度 ────────────────────────────────────────────────────
    def test_dragging_down_shortens(self):
        self.build()
        note = self.model.notes_tree[0]
        self.drag_tail(note, 40)
        self.assertLess(note.gate, 2000)

    def test_it_never_collapses_past_the_head(self):
        self.build()
        note = self.model.notes_tree[0]
        self.drag_tail(note, 600)              # 遠遠拖到頭下面
        self.assertGreater(note.end, note.start)
        self.assertGreater(note.gate, 0)

    def test_gate_follows_the_new_length(self):
        self.build()
        note = self.model.notes_tree[0]
        self.drag_tail(note, -60)
        self.assertEqual(note.gate, note.end - note.start)

    def test_the_head_never_moves(self):
        self.build()
        note = self.model.notes_tree[0]
        self.drag_tail(note, -60)
        self.assertEqual(note.start, 2000)

    # ── 復原 ────────────────────────────────────────────────────
    def test_it_is_undoable(self):
        self.build()
        note = self.model.notes_tree[0]
        self.drag_tail(note, -60)
        self.model.undo()
        self.assertEqual(self.model.notes_tree[0].gate, 2000)

    def test_a_click_without_dragging_leaves_no_undo_entry(self):
        self.build()
        note = self.model.notes_tree[0]
        depth = len(self.model.undo_stack)
        pt = self.tail_point(note)
        self.press(pt)
        self.release(pt)
        self.assertEqual(len(self.model.undo_stack), depth)
        self.assertEqual(note.gate, 2000)

    # ── 游標 ────────────────────────────────────────────────────
    def test_the_cursor_hints_the_tail(self):
        v = self.build()
        note = self.model.notes_tree[0]
        self.move(self.tail_point(note), held=False)
        self.assertEqual(v.cursor().shape(), Qt.SizeVerCursor)
        r = self.rect_of(note)
        self.move(QPoint(int(r.right()) + 60, int(r.center().y())), held=False)
        self.assertNotEqual(v.cursor().shape(), Qt.SizeVerCursor)

    def test_the_cursor_returns_to_cross_in_placement_mode(self):
        v = self.build(input_mode=True)
        note = self.model.notes_tree[0]
        self.move(self.tail_point(note), held=False)
        r = self.rect_of(note)
        self.move(QPoint(int(r.right()) + 60, int(r.center().y())), held=False)
        self.assertEqual(v.cursor().shape(), Qt.CrossCursor)


if __name__ == '__main__':
    unittest.main()


class HoldTailSnapTests(HoldTailDragTests):
    """長度吸附在「放置時值」的整數倍上，但存的是毫秒。

    吸的是**長度**不是尾端的絕對位置：MIDI 匯進來的音符頭幾乎都不在格線上，
    吸絕對位置的話拉出來的長度會是個怪數字。
    """

    def off_grid(self, start=2137, dur=2000):
        return [make(0, start, start + dur)]

    def drag_to(self, note, dy):
        pt = self.tail_point(note)
        self.press(pt)
        target = QPoint(pt.x(), pt.y() + dy)
        self.move(target)
        self.release(target)
        return note.gate

    def test_the_length_is_a_whole_number_of_the_placement_value(self):
        for beats, unit_ms in ((1.0, 500.0), (0.5, 250.0), (0.25, 125.0)):
            for dy in (-30, -60, -90, 20):
                self.build(self.off_grid())
                self.view._note_duration_beats = beats
                note = self.model.notes_tree[0]
                gate = self.drag_to(note, dy)
                self.assertAlmostEqual(gate / unit_ms, round(gate / unit_ms),
                                       places=3, msg=(beats, dy, gate))

    def test_the_head_stays_off_grid(self):
        self.build(self.off_grid())
        note = self.model.notes_tree[0]
        self.drag_to(note, -60)
        self.assertEqual(note.start, 2137, '只有長度會變，頭不動')

    def test_a_finer_value_gives_finer_control(self):
        self.build(self.off_grid())
        self.view._note_duration_beats = 1.0
        coarse = self.drag_to(self.model.notes_tree[0], -30)
        self.build(self.off_grid())
        self.view._note_duration_beats = 0.25
        fine = self.drag_to(self.model.notes_tree[0], -30)
        self.assertNotEqual(coarse, fine)

    def test_the_shortest_is_one_step(self):
        self.build(self.off_grid())
        self.view._note_duration_beats = 1.0
        note = self.model.notes_tree[0]
        self.drag_to(note, 600)          # 遠遠拖過頭
        self.assertAlmostEqual(note.gate, 500.0, delta=2)

    def test_the_status_reports_milliseconds(self):
        self.build(self.off_grid())
        note = self.model.notes_tree[0]
        pt = self.tail_point(note)
        self.press(pt)
        self.move(QPoint(pt.x(), pt.y() - 60))
        self.assertIn('ms', self.view._drag_status)
        self.release(QPoint(pt.x(), pt.y() - 60))


class ShortcutSettingTests(unittest.TestCase):
    """偏好設定裡的快捷鍵：預設 1 = 切換檢視、2 = 切換放置模式。"""

    def setUp(self):
        from qt_editor.main_window import MainWindow
        from qt_editor.models import NoteModel
        from qt_editor.settings import settings
        self.settings = settings
        self._saved = {k: settings.get(k) for k, _l, _m
                       in MainWindow._SHORTCUT_ACTIONS}
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 30.0, 4))

    def tearDown(self):
        for k, v in self._saved.items():
            self.settings.set(k, v)
        self.win.close()

    def keys(self):
        return sorted(s.key().toString() for s in self.win._user_shortcuts)

    def fire(self, text):
        from PyQt5.QtGui import QKeySequence
        for s in self.win._user_shortcuts:
            if s.key() == QKeySequence(text):
                s.activated.emit()
                return True
        return False

    def test_the_defaults_include_one_two_and_space(self):
        self.assertEqual(self.keys(), ['1', '2', 'Space'])

    def test_one_cycles_the_view_mode(self):
        before = self.win.view.view_mode
        self.assertTrue(self.fire('1'))
        self.assertNotEqual(self.win.view.view_mode, before)

    def test_two_toggles_placement_mode(self):
        self.assertFalse(self.win.view._note_input_mode)
        self.fire('2')
        self.assertTrue(self.win.view._note_input_mode)
        self.fire('2')
        self.assertFalse(self.win.view._note_input_mode)

    def test_rebinding_takes_effect_without_a_restart(self):
        self.settings.set('shortcut_note_input', 'F8')
        self.win.apply_shortcut_settings()
        self.assertIn('F8', self.keys())
        self.fire('F8')
        self.assertTrue(self.win.view._note_input_mode)

    def test_an_empty_setting_unbinds(self):
        self.settings.set('shortcut_cycle_view', '')
        self.win.apply_shortcut_settings()
        self.assertNotIn('1', self.keys())

    def test_rebinding_does_not_leave_the_old_one_active(self):
        self.settings.set('shortcut_note_input', 'F8')
        self.win.apply_shortcut_settings()
        self.assertFalse(self.fire('2'), '舊的綁定要被拆掉')

    def test_the_dialog_round_trips_the_values(self):
        from qt_editor.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.win)
        self.assertEqual(dlg._key_values['shortcut_cycle_view'], '1')
        dlg._set_key('shortcut_note_input', 'F9')
        dlg._on_accept()
        self.assertEqual(self.settings.get('shortcut_note_input'), 'F9')
        self.assertIn('F9', self.keys())


class MeasuresBpmDialogTests(unittest.TestCase):
    """多小節 BPM：拍號那幾項是互斥的，沒勾「同時修改拍號」就該變灰。

    分子/分母直接餵給 `set_measure_time_signature`，重排方式是它內部的
    `uniform`——沒勾的話這三組動了完全沒有效果。
    """

    def setUp(self):
        from PyQt5.QtCore import QTimer
        from qt_editor.main_window import MainWindow
        from qt_editor.models import NoteModel
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 60.0, 4))
        self.QTimer = QTimer

    def tearDown(self):
        self.win.close()

    def open_and(self, fn):
        from PyQt5.QtWidgets import QDialog
        out = {}

        def poke():
            for d in _app.topLevelWidgets():
                if isinstance(d, QDialog) and d.isVisible():
                    out['r'] = fn(d)
                    d.reject()
        self.QTimer.singleShot(120, poke)
        self.win.change_measures_bpm_dialog()
        return out.get('r')

    @staticmethod
    def _ts_widgets(dlg):
        from PyQt5.QtWidgets import QCheckBox, QRadioButton, QSpinBox
        chk = [c for c in dlg.findChildren(QCheckBox) if '拍號' in c.text()][0]
        spins = dlg.findChildren(QSpinBox)
        radios = [r for r in dlg.findChildren(QRadioButton)
                  if '均分' in r.text() or '保留' in r.text()]
        return chk, spins, radios

    def test_the_time_signature_fields_start_disabled(self):
        def check(dlg):
            chk, spins, radios = self._ts_widgets(dlg)
            self.assertFalse(chk.isChecked())
            # 前兩個 spin 是小節範圍，後兩個是拍號分子/分母
            return [s.isEnabled() for s in spins], [r.isEnabled() for r in radios]
        spins, radios = self.open_and(check)
        self.assertEqual(spins[-2:], [False, False], '拍號分子/分母該是灰的')
        self.assertEqual(radios, [False, False], '重排方式該是灰的')

    def test_the_measure_range_stays_editable(self):
        spins, _ = self.open_and(
            lambda d: ([s.isEnabled() for s in self._ts_widgets(d)[1]], None))
        self.assertEqual(spins[:2], [True, True], '起始/結束小節不該被鎖住')

    def test_ticking_the_box_enables_them(self):
        def check(dlg):
            chk, spins, radios = self._ts_widgets(dlg)
            chk.setChecked(True)
            return [s.isEnabled() for s in spins], [r.isEnabled() for r in radios]
        spins, radios = self.open_and(check)
        self.assertEqual(spins[-2:], [True, True])
        self.assertEqual(radios, [True, True])

    def test_unticking_disables_them_again(self):
        def check(dlg):
            chk, spins, radios = self._ts_widgets(dlg)
            chk.setChecked(True)
            chk.setChecked(False)
            return [s.isEnabled() for s in spins], [r.isEnabled() for r in radios]
        spins, radios = self.open_and(check)
        self.assertEqual(spins[-2:], [False, False])
        self.assertEqual(radios, [False, False])

    def test_the_note_handling_radios_are_always_available(self):
        from PyQt5.QtWidgets import QRadioButton

        def check(dlg):
            return [r.isEnabled() for r in dlg.findChildren(QRadioButton)
                    if '音符' in r.text()]
        self.assertTrue(all(self.open_and(check)), '音符處理和拍號無關，不該變灰')


class PreferencesTests(unittest.TestCase):
    """偏好設定：分頁化，並且把原本只在選單裡的設定收進來。"""

    def setUp(self):
        from qt_editor.main_window import MainWindow
        from qt_editor.models import NoteModel
        from qt_editor.settings import settings
        self.settings = settings
        self._saved = {k: settings.get(k) for k in (
            'show_statusbar', 'ghost_other_hand', 'undo_memory_mb',
            'audio_latency_ms', 'pitch_scale_lock')}
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 30.0, 4))

    def tearDown(self):
        for k, v in self._saved.items():
            self.settings.set(k, v)
        self.win.close()

    def dialog(self):
        from qt_editor.settings_dialog import SettingsDialog
        return SettingsDialog(self.win)

    def test_it_is_organised_into_tabs(self):
        from PyQt5.QtWidgets import QTabWidget
        tabs = self.dialog().findChild(QTabWidget)
        self.assertIsNotNone(tabs)
        self.assertGreaterEqual(tabs.count(), 4)

    def test_the_menu_only_settings_are_exposed(self):
        dlg = self.dialog()
        for key in ('pitch_velocity_numbers', 'pitch_dynamics_lane',
                    'pitch_scale_highlight', 'ghost_other_hand',
                    'show_statusbar', 'show_midi_pitch', 'pitch_scale_lock'):
            self.assertIn(key, dlg._toggles, key)

    def test_the_toggles_persist(self):
        dlg = self.dialog()
        dlg._toggles['ghost_other_hand'].setChecked(False)
        dlg._on_accept()
        self.assertFalse(self.settings.get('ghost_other_hand'))

    def test_the_performance_settings_persist(self):
        dlg = self.dialog()
        dlg._undo_spin.setValue(128)
        dlg._latency_spin.setValue(35)
        dlg._on_accept()
        self.assertEqual(self.settings.get('undo_memory_mb'), 128)
        self.assertEqual(self.settings.get('audio_latency_ms'), 35)

    def test_the_menu_checkmarks_follow(self):
        self.settings.set('show_statusbar', True)
        self.settings.set('ghost_other_hand', False)
        self.win._sync_view_settings_ui()
        self.assertTrue(self.win._act_statusbar.isChecked())
        self.assertFalse(self.win._act_ghost.isChecked())

    def test_syncing_does_not_fire_the_menu_handlers(self):
        # blockSignals：同步勾選狀態不該反過來又寫一次設定
        self.settings.set('ghost_other_hand', False)
        self.win._sync_view_settings_ui()
        self.assertFalse(self.settings.get('ghost_other_hand'))


class KeyCaptureTests(unittest.TestCase):
    """快捷鍵改成「跳提示、按一下就記錄」，而不是文字框。"""

    def capture(self, key, mods=Qt.NoModifier):
        from PyQt5.QtGui import QKeyEvent
        from PyQt5.QtWidgets import QDialog
        from qt_editor.settings_dialog import KeyCaptureDialog
        dlg = KeyCaptureDialog('播放 / 暫停', 'Space')
        dlg.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))
        return dlg.result() == QDialog.Accepted, dlg.sequence()

    def test_a_plain_key_is_recorded(self):
        self.assertEqual(self.capture(Qt.Key_F5), (True, 'F5'))

    def test_modifiers_are_kept(self):
        ok, seq = self.capture(Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertTrue(ok)
        self.assertEqual(seq, 'Ctrl+Shift+K')

    def test_escape_cancels(self):
        self.assertEqual(self.capture(Qt.Key_Escape), (False, None))

    def test_backspace_clears_the_binding(self):
        self.assertEqual(self.capture(Qt.Key_Backspace), (True, ''))

    def test_a_bare_modifier_keeps_waiting(self):
        from PyQt5.QtGui import QKeyEvent
        from PyQt5.QtWidgets import QDialog
        from qt_editor.settings_dialog import KeyCaptureDialog
        dlg = KeyCaptureDialog('x', '')
        dlg.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Control,
                                    Qt.ControlModifier))
        self.assertNotEqual(dlg.result(), QDialog.Accepted)
        self.assertIsNone(dlg.sequence())


class ShortcutListTests(unittest.TestCase):
    """快捷鍵分頁要列出所有動作、顯示目前綁定、標出重複。"""

    def setUp(self):
        from qt_editor.main_window import MainWindow
        from qt_editor.models import NoteModel
        from qt_editor.settings import settings
        self.settings = settings
        self._saved = {k: settings.get(k) for k, _l, _m
                       in MainWindow._SHORTCUT_ACTIONS}
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 30.0, 4))

    def tearDown(self):
        for k, v in self._saved.items():
            self.settings.set(k, v)
        self.win.close()

    def dialog(self):
        from qt_editor.settings_dialog import SettingsDialog
        return SettingsDialog(self.win)

    def test_playback_actions_are_listed(self):
        keys = self.dialog()._key_values
        for k in ('shortcut_play_pause', 'shortcut_play_full',
                  'shortcut_play_window', 'shortcut_stop'):
            self.assertIn(k, keys, k)

    def test_the_current_binding_is_shown(self):
        dlg = self.dialog()
        self.assertEqual(dlg._key_labels['shortcut_cycle_view'].text(), '1')

    def test_an_unset_action_says_so(self):
        dlg = self.dialog()
        self.assertIn('未設定', dlg._key_labels['shortcut_stop'].text())

    def test_a_clash_is_flagged(self):
        dlg = self.dialog()
        dlg._set_key('shortcut_stop', '1')
        self.assertTrue(dlg._key_labels['shortcut_stop'].toolTip())
        self.assertTrue(dlg._key_labels['shortcut_cycle_view'].toolTip(),
                        '兩邊都要標')

    def test_clearing_removes_the_clash(self):
        dlg = self.dialog()
        dlg._set_key('shortcut_stop', '1')
        dlg._set_key('shortcut_stop', '')
        self.assertFalse(dlg._key_labels['shortcut_cycle_view'].toolTip())

    def test_play_pause_binds_to_something_callable(self):
        for key, _label, method in self.win._SHORTCUT_ACTIONS:
            self.assertTrue(callable(getattr(self.win, method, None)),
                            '%s -> %s' % (key, method))

    def test_space_plays_then_pauses(self):
        from PyQt5.QtGui import QKeySequence
        fired = []
        self.win.play_window = lambda: fired.append('play')
        self.win._toggle_pause_resume = lambda: fired.append('pause')
        self.win._is_playing = False
        self.win._shortcut_play_pause()
        self.win._is_playing = True
        self.win._shortcut_play_pause()
        self.assertEqual(fired, ['play', 'pause'])


class MeasuresBpmRadioTests(unittest.TestCase):
    """多小節 BPM 的兩組單選鈕。

    四顆單選鈕的 parent 都是同一個對話框，Qt 的 autoExclusive 是照 parent 分的
    ——不各自 `QButtonGroup` 的話，點「保留相對位置」會把「調整音符」取消掉。
    另外「重排方式」只在調整音符時才有意義，不調整就整排隱藏。
    """

    def setUp(self):
        from PyQt5.QtCore import QTimer
        from qt_editor.main_window import MainWindow
        from qt_editor.models import NoteModel
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 60.0, 4))
        self.win.show()
        self.QTimer = QTimer

    def tearDown(self):
        self.win.close()

    def run_in_dialog(self, fn):
        from PyQt5.QtWidgets import QDialog
        out = {}

        def poke():
            for d in _app.topLevelWidgets():
                if isinstance(d, QDialog) and d.isVisible():
                    out['r'] = fn(d, self._radios(d), self._apply_chk(d))
                    d.reject()
        self.QTimer.singleShot(150, poke)
        self.win.change_measures_bpm_dialog()
        return out.get('r')

    @staticmethod
    def _radios(dlg):
        from PyQt5.QtWidgets import QRadioButton
        out = {}
        for r in dlg.findChildren(QRadioButton):
            text = r.text()
            key = ('adjust' if text == '調整音符' else
                   'keep' if '不調整' in text else
                   'uniform' if '均分' in text else 'preserve')
            out[key] = r
        return out

    @staticmethod
    def _apply_chk(dlg):
        from PyQt5.QtWidgets import QCheckBox
        return [c for c in dlg.findChildren(QCheckBox) if '拍號' in c.text()][0]

    def test_choosing_a_rearrange_option_keeps_adjust_notes_selected(self):
        def check(_d, rb, chk):
            chk.setChecked(True)          # 先讓重排方式可以按
            rb['preserve'].click()
            return rb['adjust'].isChecked(), rb['preserve'].isChecked()
        adjust, preserve = self.run_in_dialog(check)
        self.assertTrue(adjust, '「調整音符」不該被取消')
        self.assertTrue(preserve)

    def test_the_two_groups_are_independent(self):
        def check(_d, rb, chk):
            chk.setChecked(True)
            rb['preserve'].click()
            rb['keep'].click()
            return rb['keep'].isChecked(), rb['preserve'].isChecked()
        keep, preserve = self.run_in_dialog(check)
        self.assertTrue(keep)
        self.assertTrue(preserve, '換音符處理不該重設重排方式')

    def test_rearrange_options_hide_when_not_adjusting(self):
        def check(_d, rb, _chk):
            rb['keep'].click()
            return rb['uniform'].isVisible(), rb['preserve'].isVisible()
        self.assertEqual(self.run_in_dialog(check), (False, False))

    def test_they_come_back_when_adjusting_again(self):
        def check(_d, rb, _chk):
            rb['keep'].click()
            rb['adjust'].click()
            return rb['uniform'].isVisible(), rb['preserve'].isVisible()
        self.assertEqual(self.run_in_dialog(check), (True, True))

    def test_they_are_visible_by_default(self):
        def check(_d, rb, _chk):
            return rb['uniform'].isVisible(), rb['preserve'].isVisible()
        self.assertEqual(self.run_in_dialog(check), (True, True))

    def test_the_note_handling_radios_never_hide(self):
        def check(_d, rb, _chk):
            rb['keep'].click()
            return rb['adjust'].isVisible(), rb['keep'].isVisible()
        self.assertEqual(self.run_in_dialog(check), (True, True))
