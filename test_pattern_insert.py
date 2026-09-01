"""把生成的音階／琶音真的放進譜面。

放置模式是一顆一顆點；音階、琶音這種照規則走的一串音點起來慢又容易點錯，
所以另外開一條「填參數一次生成」的路。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.music_theory import PATTERN_KINDS, Key

_app = QApplication.instance() or QApplication([])

C_MAJOR = Key(0, 'major')


def make_note(idx, start, pitch, hand=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.velocity = 80
    n.min_key = 4
    n.max_key = 6
    return n


class PatternInsertTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')

    def _inserted(self):
        return sorted(self.model.notes_tree, key=lambda n: (n.start, n.pitch))

    def test_inserts_the_generated_pitches(self):
        made = self.view.insert_pattern('scale', C_MAJOR, 60, 8, 1, 1000.0, 0.5, 0)
        self.assertEqual(made, 8)
        self.assertEqual([n.pitch for n in self._inserted()],
                         [60, 62, 64, 65, 67, 69, 71, 72])

    def test_notes_are_evenly_spaced_by_the_step(self):
        self.view.insert_pattern('scale', C_MAJOR, 60, 4, 1, 1000.0, 0.5, 0)
        starts = [n.start for n in self._inserted()]
        gaps = {starts[i + 1] - starts[i] for i in range(len(starts) - 1)}
        self.assertEqual(len(gaps), 1, '間隔要一致')
        self.assertAlmostEqual(gaps.pop(), 250, delta=2)   # 120bpm 的八分音符

    def test_notes_do_not_touch_each_other(self):
        self.view.insert_pattern('scale', C_MAJOR, 60, 4, 1, 0.0, 0.5, 0)
        ns = self._inserted()
        for cur, nxt in zip(ns, ns[1:]):
            self.assertLess(cur.end, nxt.start, '要留縫，不然黏成一條')

    def test_lanes_follow_the_pitch(self):
        self.view.insert_pattern('scale', C_MAJOR, 60, 8, 1, 0.0, 0.5, 0)
        ns = self._inserted()
        lanes = [n.min_key for n in ns]
        self.assertEqual(lanes, sorted(lanes), '音高上行，鍵道也要往右')

    def test_hand_and_note_type_are_applied(self):
        self.view.insert_pattern('arpeggio', C_MAJOR, 60, 3, 1, 0.0, 0.5,
                                 hand=1, note_type=2)
        for n in self._inserted():
            self.assertEqual(n.hand, 1)
            self.assertEqual(n.note_type, 2)

    def test_the_result_is_selected_so_it_can_be_nudged_right_away(self):
        made = self.view.insert_pattern('scale', C_MAJOR, 60, 5, 1, 0.0, 0.5, 0)
        self.assertEqual(len(self.view.selected), made)

    def test_insertion_is_undoable(self):
        self.view.insert_pattern('scale', C_MAJOR, 60, 8, 1, 0.0, 0.5, 0)
        self.assertEqual(len(self.model.notes_tree), 8)
        self.model.undo()
        self.assertEqual(self.model.notes_tree, [])

    def test_inserting_into_an_existing_chart_keeps_the_old_notes(self):
        self.model.notes_tree = [make_note(0, 9000, 50)]
        self.model.rebuild_display_cache()
        self.view.insert_pattern('scale', C_MAJOR, 60, 4, 1, 0.0, 0.5, 0)
        self.assertEqual(len(self.model.notes_tree), 5)
        self.assertIn(50, [n.pitch for n in self.model.notes_tree])

    def test_zero_count_inserts_nothing_and_leaves_no_history(self):
        depth = len(self.model.undo_stack)
        self.assertEqual(self.view.insert_pattern('scale', C_MAJOR, 60, 0, 1,
                                                  0.0, 0.5, 0), 0)
        self.assertEqual(self.model.notes_tree, [])
        self.assertEqual(len(self.model.undo_stack), depth)


class DetectChartKeyTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.view.load_model(self.model)

    def test_key_comes_from_the_chart_notes(self):
        self.model.notes_tree = [make_note(i, i * 500, p)
                                 for i, p in enumerate([62, 64, 66, 67, 69, 71, 73, 74])]
        self.model.rebuild_display_cache()
        key = self.view.detect_chart_key()
        self.assertEqual((key.tonic, key.mode), (2, 'major'))

    def test_no_pitched_notes_gives_none(self):
        self.assertIsNone(self.view.detect_chart_key())

    def test_long_notes_weigh_more(self):
        # 兩顆音，其中一顆長很多 → 權重要反映在偵測上（不會丟例外、有結果）
        a = make_note(0, 0, 60)
        b = make_note(1, 1000, 66)
        b.end = b.start + 20000
        self.model.notes_tree = [a, b]
        self.model.rebuild_display_cache()
        self.assertIsNotNone(self.view.detect_chart_key())


class PatternDragModeTests(unittest.TestCase):
    """音階輔助是個常駐模式：開啟後在譜面按住往上拖，拖多遠就放幾個音。

    放開之前完全不動譜面，畫面上只有預覽——拖到一半後悔就放開前拖回去。
    """

    def setUp(self):
        from PyQt5.QtCore import QEvent, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent
        self._QEvent, self._QPointF, self._Qt = QEvent, QPointF, Qt
        self._QMouseEvent = QMouseEvent
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        for _ in range(2):
            self.view.zoom(0.5)
        self.view.window_start_unit = self.view.mapper.ms_to_unit(0.0)
        self.view.set_pattern_mode(True)
        self.view.set_pattern_params(kind='scale', direction=1, step_beats=0.5,
                                     key_override=C_MAJOR)
        self.x = self.view._display_key_to_px(self.view._pitch_to_slot(60)) + 4

    def _ev(self, kind, y, button=None):
        Qt = self._Qt
        button = Qt.LeftButton if button is None else button
        released = kind == self._QEvent.MouseButtonRelease
        return self._QMouseEvent(
            kind, self._QPointF(self.x, y), button,
            Qt.NoButton if released else button, Qt.NoModifier)

    def _press(self, ms):
        self.view.mousePressEvent(self._ev(
            self._QEvent.MouseButtonPress, self.view._ms_to_py(float(ms))))

    def _move(self, ms):
        self.view.mouseMoveEvent(self._ev(
            self._QEvent.MouseMove, self.view._ms_to_py(float(ms))))

    def _release(self, ms):
        self.view.mouseReleaseEvent(self._ev(
            self._QEvent.MouseButtonRelease, self.view._ms_to_py(float(ms))))

    def test_dragging_further_adds_more_notes(self):
        self._press(1000)
        self.assertEqual(self.view._pattern_drag['count'], 1)
        self._move(1500)
        self.assertEqual(self.view._pattern_drag['count'], 3)
        self._move(2500)
        self.assertEqual(self.view._pattern_drag['count'], 7)

    def test_nothing_is_written_until_release(self):
        self._press(1000)
        self._move(3000)
        self.assertEqual(self.model.notes_tree, [], '放開之前不該動譜面')
        self._release(3000)
        self.assertTrue(self.model.notes_tree)

    def test_what_you_previewed_is_what_gets_written(self):
        self._press(1000)
        self._move(2500)
        preview = list(self.view._pattern_preview_pitches())
        self._release(2500)
        written = [n.pitch for n in sorted(self.model.notes_tree, key=lambda n: n.start)]
        self.assertEqual(written, preview)

    def test_dragging_back_below_the_start_clamps_to_one_note(self):
        self._press(2000)
        self._move(3000)
        self._move(500)
        self.assertEqual(self.view._pattern_drag['count'], 1)

    def test_the_whole_drag_is_one_undo_step(self):
        self._press(1000)
        self._move(3000)
        self._release(3000)
        self.assertTrue(self.model.notes_tree)
        self.model.undo()
        self.assertEqual(self.model.notes_tree, [])

    def test_turning_the_mode_off_cancels_a_drag_in_progress(self):
        self._press(1000)
        self._move(3000)
        self.view.set_pattern_mode(False)
        self.assertIsNone(self.view._pattern_drag)
        self.assertEqual(self.model.notes_tree, [])

    def test_patterns_stop_at_the_edge_of_the_keyboard(self):
        # 琶音跨幾組八度很快就超過 88 鍵；超出的不該被夾到最高鍵疊成一坨
        self.view.set_pattern_params(kind='arpeggio', step_beats=0.25)
        made = self.view.insert_pattern('arpeggio', C_MAJOR, 55, 30, 1,
                                        0.0, 0.25, 0)
        pitches = [n.pitch for n in self.model.notes_tree]
        self.assertLess(made, 30)
        self.assertTrue(all(21 <= p <= 108 for p in pitches))

    def test_pattern_key_falls_back_to_detection(self):
        self.view.set_pattern_params(use_auto_key=True)
        self.model.notes_tree = [make_note(i, i * 500, p)
                                 for i, p in enumerate([62, 64, 66, 67, 69, 71, 73])]
        self.model.rebuild_display_cache()
        key = self.view.pattern_key()
        self.assertEqual((key.tonic, key.mode), (2, 'major'))

    def test_an_explicit_key_overrides_detection(self):
        self.model.notes_tree = [make_note(i, i * 500, p)
                                 for i, p in enumerate([62, 64, 66, 67, 69, 71, 73])]
        self.model.rebuild_display_cache()
        self.view.set_pattern_params(key_override=Key(5, 'minor'))
        key = self.view.pattern_key()
        self.assertEqual((key.tonic, key.mode), (5, 'minor'))


class ScaleKindInsertTests(unittest.TestCase):
    """指定音階（半音階、全音階、五聲…）也要真的放得進譜面。"""

    def setUp(self):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')

    def pitches(self):
        return [n.pitch for n in
                sorted(self.model.notes_tree, key=lambda n: n.start)]

    def test_a_chromatic_run_goes_in(self):
        made = self.view.insert_pattern('chromatic', C_MAJOR, 60, 12, 1,
                                        0.0, 0.25, 0)
        self.assertEqual(made, 12)
        self.assertEqual(self.pitches(), list(range(60, 72)))

    def test_the_chromatic_run_starts_on_the_clicked_pitch(self):
        self.view.insert_pattern('chromatic', C_MAJOR, 61, 4, 1, 0.0, 0.25, 0)
        self.assertEqual(self.pitches()[0], 61, '不該被譜面主音吸走')

    def test_every_kind_in_the_ui_list_inserts_something(self):
        for _label, kind in PATTERN_KINDS:
            self.model.notes_tree = []
            self.model.rebuild_display_cache()
            made = self.view.insert_pattern(kind, C_MAJOR, 60, 6, 1,
                                            0.0, 0.25, 0)
            self.assertEqual(made, 6, kind)

    def test_the_status_label_names_the_scale(self):
        self.view.set_pattern_params(kind='chromatic')
        self.assertEqual(self.view._pattern_kind_label(), '半音階')

    def test_the_keyboard_clip_still_applies(self):
        made = self.view.insert_pattern('chromatic', C_MAJOR, 100, 40, 1,
                                        0.0, 0.25, 0)
        self.assertLess(made, 40)
        self.assertTrue(all(21 <= n.pitch <= 108 for n in self.model.notes_tree))


if __name__ == '__main__':
    unittest.main()
