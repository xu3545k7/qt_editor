# -*- coding: utf-8 -*-
"""「時間位移」對話框，與拖曳小節線的即時切換。

使用者：「這些功能要設計的人性化一點，單一職責並且清楚地在介面上。」

原本「在時間上搬東西」散在九個入口，而且用五種說法講同一件事（「不調整音符」
「音符絕對時間不動」「保留相對位置」「吸附到新格線」「音符、小節線一起移」），
踏板要不要跟著移還藏在右鍵子選單的勾選裡。現在收成一個對話框，只回答三個
問題——量／範圍／動到哪幾層——而且動到什麼永遠寫在畫面上。
"""

import os
import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.models import GNote, NoteModel  # noqa: E402
from qt_editor.time_shift_dialog import (SCOPE_MEASURES,  # noqa: E402
                                         SCOPE_SELECTION, SCOPE_SONG,
                                         TimeShiftDialog)


def make(idx, start, dur=200):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch, n.hand, n.note_type = 60, 0, 0
    n.velocity = 90
    n.min_key, n.max_key = 4, 4
    return n


def chart(bars=12):
    m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
    m.ensure_precise_beat_grid()
    m.notes_tree = [make(i, 1000 + 500 * i) for i in range(8)]
    m.pedal_spans = [[1000.0, 2000.0], [3000.0, 4000.0]]
    m.dynamics = {0: [[1200.0, 90.0, 0.0]]}
    m.rebuild_display_cache()
    return m


class BuildingItTests(unittest.TestCase):
    """建構途中訊號會提早觸發，而且 PyQt 裡那是整個行程死掉、不是 traceback。

    第一版把訊號接在建 `_summary`／`_ok` 之前，`setCurrentIndex(2)` 當場叫到
    `_refresh()`——實測是行程直接消失，連 log 都沒 flush（檔案 0 bytes）。
    所以有一個就緒旗標。
    """

    def test_it_builds_at_all(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=3)
        self.assertTrue(dlg._ready)

    def test_it_builds_without_a_selection(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        self.assertEqual(dlg.scope(), SCOPE_SONG)

    def test_it_builds_on_a_chart_with_no_measures(self):
        m = NoteModel.create_new('t', 120.0, 1.0, 4)
        TimeShiftDialog(None, m, selected_count=0)


class ScopeTests(unittest.TestCase):
    def test_a_selection_is_preselected_when_there_is_one(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=3)
        self.assertEqual(dlg.scope(), SCOPE_SELECTION)
        self.assertIn('3', dlg._rb_sel.text())

    def test_without_a_selection_that_option_is_unavailable(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        self.assertFalse(dlg._rb_sel.isEnabled())

    def test_the_measure_range_covers_the_whole_chart_by_default(self):
        m = chart()
        dlg = TimeShiftDialog(None, m, selected_count=0)
        dlg._rb_meas.setChecked(True)
        self.assertEqual(dlg.measure_range(), (1, m.count_measures()))

    def test_the_measure_range_turns_into_milliseconds(self):
        m = chart()
        dlg = TimeShiftDialog(None, m, selected_count=0)
        dlg._rb_meas.setChecked(True)
        dlg._first.setValue(3)
        dlg._last.setValue(5)
        lo, hi = dlg.range_ms()
        self.assertEqual(lo, m.get_measure_time_range(2)[0])
        self.assertEqual(hi, m.get_measure_time_range(4)[1])

    def test_the_last_measure_cannot_come_before_the_first(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        dlg._rb_meas.setChecked(True)
        dlg._first.setValue(6)
        self.assertGreaterEqual(dlg._last.value(), 6)


class LayerTests(unittest.TestCase):
    """哪幾層能選，以及為什麼不能選——理由要寫在畫面上，不是默默變灰。"""

    def test_the_grid_can_only_move_as_a_whole(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=3)
        self.assertFalse(dlg._grid.isEnabled(), '範圍是選取時不該能動小節線')
        dlg._rb_song.setChecked(True)
        self.assertTrue(dlg._grid.isEnabled())

    def test_it_says_why_the_grid_is_unavailable(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=3)
        self.assertIn('小節線', dlg._layer_note.text())

    def test_switching_away_from_the_whole_song_unticks_the_grid(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=3)
        dlg._rb_song.setChecked(True)
        dlg._grid.setChecked(True)
        dlg._rb_meas.setChecked(True)
        self.assertFalse(dlg._grid.isChecked())
        self.assertFalse(dlg.layers()['grid'])

    def test_audio_needs_an_audio_file(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0, has_audio=False)
        self.assertFalse(dlg._audio.isEnabled())
        self.assertIn('音檔', dlg._layer_note.text())
        with_audio = TimeShiftDialog(None, chart(), selected_count=0, has_audio=True)
        self.assertTrue(with_audio._audio.isEnabled())

    def test_notes_and_expression_are_on_by_default(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        self.assertTrue(dlg.layers()['notes'])
        self.assertTrue(dlg.layers()['expression'])
        self.assertFalse(dlg.layers()['grid'])


class AmountTests(unittest.TestCase):
    def test_milliseconds_come_straight_through(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        dlg._amount.setValue(-300)
        self.assertEqual(dlg.unit(), 'ms')
        self.assertEqual(dlg.delta_ms(), -300)

    def test_beats_are_reported_as_beats(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        dlg._unit.setCurrentIndex(1)
        dlg._amount.setValue(-2)
        self.assertEqual(dlg.unit(), 'beat')
        self.assertEqual(dlg.beats(), -2.0)
        self.assertEqual(dlg.delta_ms(), 0, '拍要照位置換算，不能給固定毫秒')

    def test_a_note_value_multiplies_out(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        dlg._unit.setCurrentIndex(2)
        dlg._value.setCurrentIndex(3)            # 八分音符 = 0.5 拍
        dlg._amount.setValue(-1)
        self.assertAlmostEqual(dlg.beats(), -0.5)

    def test_the_note_value_picker_only_shows_for_note_values(self):
        dlg = TimeShiftDialog(None, chart(), selected_count=0)
        self.assertTrue(dlg._value.isHidden(), '毫秒模式不該出現音符值下拉')
        dlg._unit.setCurrentIndex(2)
        self.assertFalse(dlg._value.isHidden())


class SummaryTests(unittest.TestCase):
    """「這一次按下去會發生什麼」要寫在畫面上。"""

    def dialog(self, selected=0, audio=False):
        dlg = TimeShiftDialog(None, chart(), selected_count=selected,
                              has_audio=audio)
        dlg._rb_song.setChecked(True)
        dlg._amount.setValue(-300)
        return dlg

    def test_it_lists_every_layer_that_will_move(self):
        dlg = self.dialog()
        dlg._grid.setChecked(True)
        text = dlg._summary.text()
        for word in ('音符', '表情', '小節線'):
            self.assertIn(word, text)

    def test_it_names_the_scope(self):
        self.assertIn('整首', self.dialog()._summary.text())

    def test_it_warns_that_the_pedal_gets_clipped(self):
        self.assertIn('裁掉', self.dialog()._summary.text())

    def test_it_does_not_warn_when_moving_later(self):
        dlg = self.dialog()
        dlg._amount.setValue(+300)
        self.assertNotIn('裁掉', dlg._summary.text())

    def test_zero_cannot_be_applied(self):
        dlg = self.dialog()
        dlg._amount.setValue(0)
        self.assertFalse(dlg._ok.isEnabled())
        self.assertIn('0', dlg._summary.text())

    def test_picking_no_layer_cannot_be_applied(self):
        dlg = self.dialog()
        dlg._notes.setChecked(False)
        dlg._expression.setChecked(False)
        self.assertFalse(dlg._ok.isEnabled())
        self.assertIn('沒有選', dlg._summary.text())


class ShiftTimePrimitiveTests(unittest.TestCase):
    """模型層的 `shift_time`：三層各自獨立。"""

    def state(self, m):
        return ([n.start for n in m.notes_tree][:2],
                [[int(a), int(b)] for a, b in m.pedal_spans],
                [ms for _i, ms in m.get_beat_entries()][:2])

    def test_notes_and_expression_without_the_grid(self):
        m = chart()
        grid = self.state(m)[2]
        m.shift_time(-400, notes=True, expression=True, grid=False)
        notes, pedals, after = self.state(m)
        self.assertEqual(notes, [600, 1100])
        self.assertEqual(pedals, [[600, 1600], [2600, 3600]])
        self.assertEqual(after, grid, '小節線不該動')

    def test_the_grid_without_the_notes(self):
        m = chart()
        before = self.state(m)
        m.shift_time(600, notes=False, expression=False, grid=True)
        notes, pedals, grid = self.state(m)
        self.assertEqual(notes, before[0])
        self.assertEqual(pedals, before[1])
        self.assertEqual(grid, [g + 600 for g in before[2]])

    def test_all_three_go_through_shift_all_time(self):
        m = chart()
        info = m.shift_time(600, notes=True, expression=True, grid=True)
        self.assertEqual(info['applied_ms'], 600)
        self.assertGreater(info['notes'], 0)
        self.assertGreater(info['expression'], 0, '表情數要是真的數字')

    def test_a_range_limits_notes_and_expression(self):
        m = chart()
        info = m.shift_time(-300, notes=True, expression=True,
                            lo_ms=2900, hi_ms=4300)
        self.assertEqual(info['notes'], 3)
        self.assertEqual(self.state(m)[1], [[1000, 2000], [2700, 3700]])

    def test_the_grid_refuses_a_range(self):
        with self.assertRaises(ValueError):
            chart().shift_time(-300, grid=True, lo_ms=1000)


class BarlineDragModeTests(unittest.TestCase):
    """拖曳小節線：拖的途中按 Alt 切換「只動小節線／連音符一起拖」。

    使用者挑的就是「拖的時候可以切換」。預設不動音符——和其他改格線的操作
    （每小節 BPM、改拍號的均分）一致；原本是一定會把後面的音符全部拖走。
    """

    def view(self):
        from qt_editor.chart_view import ChartView
        v = ChartView()
        v.resize(1200, 720)
        v.load_model(chart())
        v.rebuild_mapper()
        return v

    def test_the_default_is_grid_only(self):
        self.assertFalse(self.view()._barline_drag_moves_notes)

    def test_the_hint_says_which_mode_it_is_in(self):
        import inspect
        from qt_editor import chart_view
        src = inspect.getsource(chart_view.ChartView.paintEvent)
        self.assertIn('Alt 切換', src, '拖曳提示沒寫出切換方式')

    def test_grid_only_leaves_the_notes_where_they_are(self):
        v = self.view()
        m = v.model
        before = [(n.start, n.end) for n in m.notes_tree]
        m.set_measure_bpm(2, 150.0, uniform=True, adjust_notes=False)
        self.assertEqual([(n.start, n.end) for n in m.notes_tree], before)

    def test_dragging_the_notes_along_still_works(self):
        v = self.view()
        m = v.model
        before = [(n.start, n.end) for n in m.notes_tree]
        m.set_measure_bpm(2, 150.0, uniform=True, adjust_notes=True)
        self.assertNotEqual([(n.start, n.end) for n in m.notes_tree], before)


if __name__ == '__main__':
    unittest.main()
