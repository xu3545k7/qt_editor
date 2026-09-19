"""重做（redo）、多小節 BPM 的「到同 BPM 的最後一小節」、新增的快捷鍵。"""

import contextlib
import io
import unittest

from qt_editor.models import GNote, NoteModel


def make(idx, start, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 80, 80
    n.pitch = pitch
    n.hand = 0
    n.min_key, n.max_key = 5, 7
    n.note_type = 0
    return n


def chart(count=8):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.notes_tree = [make(i, i * 500) for i in range(count)]
    m.rebuild_display_cache()
    return m


def starts(m):
    return [int(n.start) for n in m.notes_tree]


class RedoTests(unittest.TestCase):
    def edit(self, m, value):
        m.push_history()
        m.notes_tree[0].start = value
        m.rebuild_display_cache()

    def test_redo_brings_back_what_undo_took(self):
        m = chart()
        self.edit(m, 111)
        self.edit(m, 222)
        after = starts(m)
        m.undo()
        m.undo()
        self.assertEqual(m.notes_tree[0].start, 0)
        self.assertTrue(m.redo())
        self.assertEqual(m.notes_tree[0].start, 111)
        self.assertTrue(m.redo())
        self.assertEqual(starts(m), after)
        self.assertFalse(m.redo(), '沒得重做了')

    def test_undo_and_redo_can_go_back_and_forth(self):
        m = chart()
        self.edit(m, 111)
        for _ in range(5):
            m.undo()
            self.assertEqual(m.notes_tree[0].start, 0)
            m.redo()
            self.assertEqual(m.notes_tree[0].start, 111)
        self.assertEqual(len(m.undo_stack), 1, '來回按不能讓歷史越長越多')
        self.assertEqual(len(m.redo_stack), 0)

    def test_a_new_edit_drops_the_redo(self):
        m = chart()
        self.edit(m, 111)
        m.undo()
        self.edit(m, 333)
        self.assertFalse(m.redo())
        self.assertEqual(m.notes_tree[0].start, 333)

    def test_a_discarded_no_op_edit_keeps_the_redo(self):
        """點一下音符沒拖動：記了一筆又撤回，重做不能因此消失。"""
        m = chart()
        self.edit(m, 111)
        m.undo()
        m.push_history()
        self.assertTrue(m.discard_last_history())
        self.assertTrue(m.redo())
        self.assertEqual(m.notes_tree[0].start, 111)

    def test_redo_restores_the_tempo_map_too(self):
        m = chart()
        before = m.get_measure_bpm(1)
        m.push_history()
        m.set_measure_bpm(1, 90.0)
        changed = m.get_measure_bpm(1)
        m.undo()
        self.assertAlmostEqual(m.get_measure_bpm(1), before, places=1)
        m.redo()
        self.assertAlmostEqual(m.get_measure_bpm(1), changed, places=1)

    def test_loading_another_chart_clears_both(self):
        m = chart()
        self.edit(m, 111)
        m.undo()
        m.clear_history()
        self.assertFalse(m.undo())
        self.assertFalse(m.redo())


class SameBpmRunTests(unittest.TestCase):
    def model(self, bpms):
        m = chart()
        m.count_measures = lambda: len(bpms)
        m.get_measure_bpm = lambda i: bpms[i]
        return m

    def test_jitter_within_half_a_bpm_is_the_same_tempo(self):
        m = self.model([120.3, 119.8, 120.4, 119.9, 120.2, 140.0, 140.1])
        last, mean = m.same_bpm_run(0)
        self.assertEqual(last, 4)
        self.assertAlmostEqual(mean, 120.12, places=2)

    def test_not_measured_against_the_first_measure(self):
        # 第一小節偏高 0.5、下一小節偏低 0.4：差 0.9，但都在 120±0.5 裡
        m = self.model([120.5, 119.6, 120.0, 119.7, 125.0])
        self.assertEqual(m.same_bpm_run(0)[0], 3)

    def test_a_slow_drift_does_not_run_forever(self):
        m = self.model([120.0, 120.3, 120.6, 120.9, 121.2, 121.5])
        self.assertEqual(m.same_bpm_run(0)[0], 3)

    def test_a_one_bpm_step_away_from_a_steady_run_splits_it(self):
        m = self.model([120.0, 120.0, 120.0, 121.1])
        self.assertEqual(m.same_bpm_run(0)[0], 2)

    def test_starts_from_the_given_measure(self):
        m = self.model([100.0, 100.0, 150.0, 150.2, 149.9, 90.0])
        self.assertEqual(m.same_bpm_run(2)[0], 4)

    def test_runs_to_the_end_when_nothing_changes(self):
        m = self.model([88.0] * 6)
        self.assertEqual(m.same_bpm_run(1)[0], 5)

    def test_exactly_half_a_bpm_still_counts(self):
        m = self.model([120.0, 120.5, 125.0])
        self.assertEqual(m.same_bpm_run(0)[0], 1)

    def test_a_real_tempo_map(self):
        m = chart()
        for mi, bpm in ((3, 150.0), (4, 150.0), (5, 150.0)):
            m.set_measure_bpm(mi, bpm)
        self.assertEqual(m.same_bpm_run(0)[0], 2)
        self.assertEqual(m.same_bpm_run(3)[0], 5)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()
        cls.win.show()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False        # 不然關視窗會跳「要不要存檔」
        cls.win.close()

    def setUp(self):
        self.model = chart(40)
        self.model.dirty = False
        self.win._load_model_all(self.model)
        self.win._is_playing = False

    def test_redo_key_bindings(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QKeyEvent
        from PyQt5.QtCore import QEvent
        view = self.win.view
        self.model.push_history()
        self.model.notes_tree[0].start = 4444
        view.undo()
        self.assertEqual(self.model.notes_tree[0].start, 0)
        for mods, key in ((Qt.ControlModifier | Qt.ShiftModifier, Qt.Key_Z),
                          (Qt.ControlModifier, Qt.Key_Y)):
            view.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))
            self.assertEqual(self.model.notes_tree[0].start, 4444)
            view.undo()

    def test_the_edit_menu_has_redo_with_both_keys(self):
        from PyQt5.QtGui import QKeySequence
        acts = [a for a in self.win.findChildren(type(self.win.menuBar().actions()[0]))
                if a.text() in ('重做', 'Redo')]
        self.assertTrue(acts)
        keys = {s.toString() for s in acts[0].shortcuts()}
        self.assertEqual(keys, {QKeySequence('Ctrl+Y').toString(),
                                QKeySequence('Ctrl+Shift+Z').toString()})

    def test_measure_jumps(self):
        view = self.win.view
        self.win._jump_to_ms(0.0)
        self.win._jump_next_measure()
        self.win._jump_next_measure()
        self.assertEqual(self.model.get_measure_at_ms(view.judge_line_view_ms() + 1), 2)
        self.win._jump_prev_measure()
        self.assertEqual(self.model.get_measure_at_ms(view.judge_line_view_ms() + 1), 1)

    def test_prev_measure_from_the_middle_goes_to_its_start_first(self):
        view = self.win.view
        start, end = self.model.get_measure_time_range(3)
        self.win._jump_to_ms((start + end) / 2)
        self.win._jump_prev_measure()
        self.assertAlmostEqual(view.judge_line_view_ms(), start, delta=2)

    def test_song_end_is_the_last_note(self):
        self.win._jump_song_end()
        last = max(n.start for n in self.model.notes_tree)
        self.assertAlmostEqual(self.win.view.judge_line_view_ms(), last, delta=2)
        self.win._jump_song_start()
        self.assertAlmostEqual(self.win.view.judge_line_view_ms(), 0.0, delta=2)

    def test_duration_steps_by_length(self):
        items = self.win._note_dur_items
        self.win._on_dur_combo_changed(2)
        base = items[2][1]
        self.win._shortcut_dur_shorter()
        shorter = items[self.win._ni_dur_idx][1]
        self.assertLess(shorter, base)
        self.assertFalse(any(shorter < v < base for _n, v in items),
                         '只挪一級，不能跳過中間的時值')
        self.win._shortcut_dur_longer()
        self.assertEqual(self.win._ni_dur_idx, 2)
        for tbs in self.win._toolbars:
            if getattr(tbs, 'dur_combo', None) is not None:
                self.assertEqual(tbs.dur_combo.currentIndex(), 2, '工具列要跟著變')

    def test_hand_toggle(self):
        self.win._on_hand_combo_changed(0)
        self.win._shortcut_toggle_hand()
        self.assertEqual(self.win._ni_hand_idx, 1)
        self.win._shortcut_toggle_hand()
        self.assertEqual(self.win._ni_hand_idx, 0)

    def test_tap_hold_toggle(self):
        view = self.win.view
        notes = self.model.notes_tree[:3]
        notes[0].note_type = 2
        notes[1].note_type = 0
        notes[2].note_type = 3
        view.selected = {n.idx for n in notes}
        self.win._shortcut_toggle_tap_hold()
        self.assertEqual([n.note_type for n in notes], [2, 2, 2], '混合的全部變長條')
        self.win._shortcut_toggle_tap_hold()
        self.assertEqual([n.note_type for n in notes], [0, 0, 0], '全是長條就變點擊')
        ids = [n.idx for n in notes]
        view.undo()
        by_id = {n.idx: n for n in view.model.notes_tree}
        self.assertEqual([by_id[i].note_type for i in ids], [2, 2, 2], '可以復原')
        view.selected = set()
        before = [n.note_type for n in view.model.notes_tree]
        self.win._shortcut_toggle_tap_hold()
        self.assertEqual([n.note_type for n in view.model.notes_tree], before, '沒選取不動')

    def test_width_toggle_keeps_right_edge(self):
        view = self.win.view
        a, b = self.model.notes_tree[:2]
        a.min_key, a.max_key = 10, 11
        b.min_key, b.max_key = 20, 22
        view.selected = {a.idx, b.idx}
        self.win._shortcut_toggle_width()
        self.assertEqual((a.min_key, a.max_key), (9, 11))
        self.assertEqual((b.min_key, b.max_key), (20, 22))
        self.win._shortcut_toggle_width()
        self.assertEqual((a.min_key, a.max_key), (10, 11), '全是寬 3 就回到寬 2，右緣不動')
        self.assertEqual((b.min_key, b.max_key), (21, 22))

    def test_edit_toggles_are_customizable_shortcuts(self):
        from qt_editor import settings as S
        keys = {k: m for k, _l, m in self.win._SHORTCUT_ACTIONS}
        self.assertEqual(keys['shortcut_toggle_tap_hold'], '_shortcut_toggle_tap_hold')
        self.assertEqual(keys['shortcut_toggle_width'], '_shortcut_toggle_width')
        defaults = [S._DEFAULTS[k] for k, _l, _m in self.win._SHORTCUT_ACTIONS if S._DEFAULTS.get(k)]
        self.assertEqual(len(defaults), len(set(defaults)), '預設快捷鍵不能撞')
        fixed = {'H', 'T', 'K', 'L', 'R', 'C', 'P', 'S'}
        self.assertFalse(fixed & {S._DEFAULTS['shortcut_toggle_tap_hold'], S._DEFAULTS['shortcut_toggle_width']})

    def test_measures_bpm_dialog_end_buttons(self):
        from unittest import mock
        from PyQt5.QtWidgets import QDialog, QLabel, QPushButton, QSpinBox
        for mi in range(4, self.model.count_measures()):
            self.model.set_measure_bpm(mi, 150.3 if mi % 2 else 149.8)
        total = self.model.count_measures()
        seen = {}

        def fake_exec(dlg):
            start_spin, end_spin = dlg.findChildren(QSpinBox)[:2]
            buttons = {b.text(): b for b in dlg.findChildren(QPushButton)}
            buttons['到最後一小節'].click()
            seen['last'] = end_spin.value()
            start_spin.setValue(5)
            buttons['到同 BPM 的最後一小節'].click()
            seen['same_from_5'] = end_spin.value()
            start_spin.setValue(1)
            buttons['到同 BPM 的最後一小節'].click()
            seen['same_from_1'] = end_spin.value()
            seen['label'] = [lb.text() for lb in dlg.findChildren(QLabel) if '平均 BPM' in lb.text()]
            return QDialog.Rejected

        with mock.patch.object(QDialog, 'exec_', fake_exec):
            self.win.change_measures_bpm_dialog()
        self.assertEqual(seen['last'], total)
        self.assertEqual(seen['same_from_5'], total)
        self.assertEqual(seen['same_from_1'], 4)
        self.assertTrue(seen['label'])

    def test_every_shortcut_points_at_a_real_method_and_has_a_default(self):
        from qt_editor.settings import _DEFAULTS
        from PyQt5.QtGui import QKeySequence
        bound = []
        for key, _label, method in self.win._SHORTCUT_ACTIONS:
            self.assertTrue(callable(getattr(self.win, method, None)), method)
            self.assertIn(key, _DEFAULTS)
            if _DEFAULTS[key]:
                self.assertFalse(QKeySequence(_DEFAULTS[key]).isEmpty(), key)
                bound.append(_DEFAULTS[key])
        self.assertEqual(len(bound), len(set(bound)), '預設快捷鍵不能重複')


if __name__ == '__main__':
    unittest.main()
