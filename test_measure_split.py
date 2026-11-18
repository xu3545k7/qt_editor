# -*- coding: utf-8 -*-
"""在一顆音符前面分割小節。

使用者：「從某音符開始改拍點的功能現在還是怪怪的……我是希望它可以生成一個類似
小節線的線在 note 前面，也可以對它做和小節線一樣的事情，顯示上也類似，要標註
從這邊開始 bpm 是多少。右鍵選取小節也不能一起算。總之有點類似分割小節的功能。」

**舊版做的是另一件事。** `set_note_bpm` 在音符處插一筆 beat entry、再縮放小節
的尾段。那一筆是普通的拍線、不是小節邊界，所以 `get_measure_at_ms` 仍然回報
原來那一小節（右鍵選小節算不到它），而小節線只畫在「剛好是拍點」的位置上，
線和標籤會跑到邊界後的第一個拍點上。

現在是真的把小節切開：分割點進 `json_meta['measure_splits']`，小節邊界的計算
直接讀它，於是編號、BPM、右鍵選單、拖曳全部自動跟上。存檔規則是使用者定的：
**JSON 存分割，PAN XML 還是算整個小節。**
"""

import os
import shutil
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.models import GNote, NoteModel          # noqa: E402


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
    m.rebuild_display_cache()
    return m


def split_at(m, measure_idx=2, offset=700):
    lo, _hi = m.get_measure_time_range(measure_idx)
    where = int(lo) + offset
    assert m.add_measure_split(where), '這個位置應該切得下去'
    return where


class ItBecomesARealMeasureBoundaryTests(unittest.TestCase):
    """「類似小節線」的意思是資料上就是一個邊界，不是畫一條裝飾線。"""

    def test_the_measure_is_cut_in_two(self):
        m = chart()
        before = m.count_measures()
        lo, hi = m.get_measure_time_range(2)
        where = split_at(m)
        self.assertEqual(m.count_measures(), before + 1)
        self.assertEqual(m.get_measure_time_range(2), (lo, where))
        self.assertEqual(m.get_measure_time_range(3), (where, hi))

    def test_the_outer_grid_does_not_move(self):
        """這是「分割」和「插入小節」的差別：後面每一小節都留在原地。"""
        m = chart()
        after = [m.get_measure_time_range(i) for i in range(3, 10)]
        split_at(m)
        self.assertEqual([m.get_measure_time_range(i) for i in range(4, 11)], after)

    def test_the_right_click_menu_lands_on_the_right_segment(self):
        """使用者：「右鍵選取小節也不能一起算」。"""
        m = chart()
        where = split_at(m)
        self.assertEqual(m.get_measure_at_ms(where - 10), 2)
        self.assertEqual(m.get_measure_at_ms(where), 3)
        self.assertEqual(m.get_measure_at_ms(where + 10), 3)

    def test_a_split_on_a_bar_line_is_refused(self):
        """那裡本來就有小節線，不用切。"""
        m = chart()
        lo, _hi = m.get_measure_time_range(2)
        self.assertFalse(m.add_measure_split(lo))
        self.assertEqual(m.measure_splits, [])

    def test_splitting_twice_at_the_same_place_does_nothing(self):
        m = chart()
        where = split_at(m)
        self.assertFalse(m.add_measure_split(where))
        self.assertEqual(m.measure_splits, [where])

    def test_two_splits_in_one_measure(self):
        m = chart()
        lo, hi = m.get_measure_time_range(2)
        a, b = int(lo) + 500, int(lo) + 1200
        self.assertTrue(m.add_measure_split(a))
        self.assertTrue(m.add_measure_split(b))
        self.assertEqual([m.get_measure_time_range(i) for i in (2, 3, 4)],
                         [(lo, a), (a, b), (b, hi)])
        self.assertEqual(m.get_measure_time_range(5), (hi, hi + 2000))

    def test_removing_it_merges_the_measure_back(self):
        m = chart()
        lo, hi = m.get_measure_time_range(2)
        before = m.count_measures()
        where = split_at(m)
        self.assertTrue(m.remove_measure_split(where))
        self.assertEqual(m.count_measures(), before)
        self.assertEqual(m.get_measure_time_range(2), (lo, hi))

    def test_it_knows_which_segments_are_split(self):
        """畫面靠這個決定要不要畫成虛線。"""
        m = chart()
        split_at(m)
        self.assertTrue(m.measure_is_split_segment(2))
        self.assertTrue(m.measure_is_split_segment(3))
        self.assertFalse(m.measure_is_split_segment(1))
        self.assertFalse(m.measure_is_split_segment(4))


class ItDoesNotTouchTheGridTests(unittest.TestCase):
    """純分割不往 beat_data 裡塞東西——不然 PAN 匯出會留下一條孤兒拍線。"""

    def test_no_beat_entry_is_added(self):
        m = chart()
        before = list(m.get_beat_entries())
        split_at(m)
        self.assertEqual(list(m.get_beat_entries()), before)

    def test_no_note_moves(self):
        m = chart()
        before = [(n.start, n.end) for n in m.notes_tree]
        split_at(m)
        self.assertEqual([(n.start, n.end) for n in m.notes_tree], before)

    def test_the_time_signature_is_untouched(self):
        """走的不是拍號切割——`time_sig_changes` 是給真的變拍用的。"""
        m = chart()
        before = list(m.time_sig_changes)
        split_at(m)
        self.assertEqual(list(m.time_sig_changes), before)


class TheBpmOnTheLineTests(unittest.TestCase):
    """使用者：「要標註從這邊開始 bpm 是多少」。那個數字來自 get_measure_bpm。"""

    def test_a_plain_split_does_not_change_any_tempo(self):
        m = chart()
        split_at(m)
        for i in (1, 2, 3, 4):
            self.assertAlmostEqual(m.get_measure_bpm(i), 120.0, delta=0.01)

    def test_a_segment_reports_its_own_tempo(self):
        m = chart()
        split_at(m)
        m.set_measure_bpm(2, 150.0, uniform=False, adjust_notes=True)
        self.assertAlmostEqual(m.get_measure_bpm(2), 150.0, delta=0.05)
        self.assertAlmostEqual(m.get_measure_bpm(3), 120.0, delta=0.05)

    def test_changing_a_segment_only_shifts_what_follows(self):
        """和一般的「改小節 BPM」同一個行為：這一段縮放，後面整段平移。"""
        m = chart()
        split_at(m)
        lo2, hi2 = m.get_measure_time_range(3)
        m.set_measure_bpm(2, 150.0, uniform=False, adjust_notes=True)
        new_lo2, new_hi2 = m.get_measure_time_range(3)
        self.assertEqual(new_hi2 - new_lo2, hi2 - lo2, '第二段的長度不該變')
        self.assertLess(new_lo2, lo2, '前面變快了，它應該往前移')

    def test_a_whole_measure_still_uses_the_time_signature(self):
        """完整小節的算式要和以前逐位相同——量出來的浮點值會帶進誤差。"""
        m = chart()
        self.assertAlmostEqual(m.measure_quarter_notes(2), 4.0, delta=1e-9)
        split_at(m)
        self.assertAlmostEqual(m.measure_quarter_notes(4), 4.0, delta=1e-9)

    def test_a_segment_counts_only_its_own_share(self):
        m = chart()
        split_at(m, offset=700)                 # 2000ms 的小節切成 700 / 1300
        self.assertAlmostEqual(m.measure_quarter_notes(2), 4.0 * 0.35, delta=0.02)
        self.assertAlmostEqual(m.measure_quarter_notes(3), 4.0 * 0.65, delta=0.02)


class TheSplitMovesWithTheMusicTests(unittest.TestCase):
    """分割點是絕對毫秒，和拍號標記一樣，改長度時要跟著搬。"""

    def test_it_follows_a_tempo_change_earlier_in_the_song(self):
        m = chart()
        where = split_at(m, measure_idx=5)
        m.set_measure_bpm(1, 60.0, uniform=False, adjust_notes=True)
        moved = m.measure_splits[0]
        self.assertNotEqual(moved, where, '前面變慢了，分割點應該往後移')
        self.assertTrue(m.measure_is_split_segment(m.get_measure_at_ms(moved)))

    def test_it_follows_a_global_shift(self):
        m = chart()
        where = split_at(m)
        m.shift_all_time(500)
        self.assertEqual(m.measure_splits, [where + 500])

    def test_it_follows_a_global_scale(self):
        m = chart()
        where = split_at(m)
        m.scale_all_time(2.0)
        self.assertEqual(m.measure_splits, [where * 2])

    def test_undo_brings_it_back(self):
        m = chart()
        m.push_history()
        where = split_at(m)
        self.assertEqual(m.measure_splits, [where])
        m.undo()
        self.assertEqual(m.measure_splits, [])
        self.assertFalse(m.measure_is_split_segment(2))


class WhereItIsStoredTests(unittest.TestCase):
    """使用者定案：「json 存分割，pan 還是算存整個小節」。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_json_keeps_it(self):
        m = chart()
        where = split_at(m)
        count = m.count_measures()
        path = os.path.join(self.dir, 'c.json')
        m.save_json(path)
        back = NoteModel()
        back.load_json(path)
        self.assertEqual(back.measure_splits, [where])
        self.assertEqual(back.count_measures(), count)
        self.assertTrue(back.measure_is_split_segment(2))

    def test_pan_xml_writes_the_whole_measure(self):
        """PAN 沒有「分割」這回事，而且匯出不能留下痕跡。"""
        plain = chart()
        plain_path = os.path.join(self.dir, 'plain.xml')
        plain.save_xml(plain_path)

        m = chart()
        split_at(m)
        split_path = os.path.join(self.dir, 'split.xml')
        m.save_xml(split_path)

        with open(plain_path, encoding='utf-8') as fh:
            a = fh.read()
        with open(split_path, encoding='utf-8') as fh:
            b = fh.read()
        self.assertEqual(a, b, '分割不該在 PAN XML 裡留下任何痕跡')

        back = NoteModel()
        back.load_xml(split_path)
        self.assertEqual(back.measure_splits, [])
        self.assertEqual(back.count_measures(), plain.count_measures())

    def test_merging_gives_the_original_measures_back(self):
        m = chart()
        plain = chart()
        split_at(m)
        self.assertEqual(
            [(int(s), int(e)) for _su, s, _eu, e in m.measure_boundaries_merged()],
            [(int(s), int(e)) for _su, s, _eu, e
             in plain._get_precise_measure_boundaries()])


class TheEditorDrawsAndEditsItTests(unittest.TestCase):
    def view(self, with_split=True):
        from qt_editor.chart_view import ChartView
        v = ChartView()
        v.resize(1200, 720)
        m = chart()
        v.load_model(m)
        v.rebuild_mapper()
        self.where = split_at(m) if with_split else None
        v.rebuild_mapper()
        self.addCleanup(lambda: (setattr(m, 'dirty', False), v.close()))
        return v

    def test_the_bar_line_is_drawn_at_the_split(self):
        """小節線從邊界畫，不是從拍點裡挑——分割點本來就不是拍點。"""
        v = self.view()
        bars = dict(v._visible_bar_lines(0, 10 ** 9))
        self.assertIn(self.where, bars, '分割點上沒有小節線')

    def test_bar_lines_still_appear_without_a_split(self):
        v = self.view(with_split=False)
        bars = v._visible_bar_lines(0, 10 ** 9)
        self.assertTrue(bars)
        self.assertEqual(bars[0][1], 1, '第一條是第 1 小節')

    def test_every_measure_boundary_gets_a_line(self):
        """以前邊界上沒有拍點就畫不出線（hanei 少了好幾個小節的第一拍）。"""
        v = self.view()
        bars = {ms for ms, _no in v._visible_bar_lines(0, 10 ** 9)}
        bounds = v.model._get_precise_measure_boundaries()
        for _su, start_ms, _eu, _em in bounds:
            self.assertIn(int(start_ms), bars)

    def test_the_context_menu_offers_the_split(self):
        import inspect
        from qt_editor import chart_view
        src = inspect.getsource(chart_view.ChartView.contextMenuEvent)
        self.assertIn('在這顆音符前面分割小節…', src)
        self.assertNotIn('從此音符開始變拍', src, '舊的說法要換掉')

    def test_the_measure_menu_offers_removing_it(self):
        import inspect
        from qt_editor import chart_view
        src = inspect.getsource(chart_view.ChartView._ctx_build_measure_menu)
        self.assertIn('移除這條分割線', src)

    def test_the_drag_readout_uses_the_segment_share(self):
        """拖片段的小節線時顯示的 BPM 要是它真正的速度。"""
        v = self.view()
        lo, hi = v.model.get_measure_time_range(2)
        bpm, _r = v._barline_bpm(lo, hi, 2)
        self.assertAlmostEqual(bpm, v.model.get_measure_bpm(2), delta=0.6)

    def test_the_drag_readout_is_unchanged_for_whole_measures(self):
        v = self.view(with_split=False)
        lo, hi = v.model.get_measure_time_range(2)
        bpm, _r = v._barline_bpm(lo, hi, 2)
        self.assertAlmostEqual(bpm, 120.0, delta=0.01)

    def press_at(self, v, ms):
        from PyQt5.QtCore import QPoint, Qt
        from PyQt5.QtGui import QMouseEvent
        v.follow_to_ms(float(ms))
        unit = v.mapper.ms_to_unit(float(ms)) - v.window_start_unit
        py = int(v._unit_to_py(unit))
        v.mousePressEvent(QMouseEvent(
            QMouseEvent.MouseButtonPress, QPoint(300, py),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def test_the_split_line_can_be_grabbed(self):
        """使用者：「也可以對它做和小節線一樣的事情」。"""
        v = self.view()
        v.barline_drag_enabled = True
        self.press_at(v, self.where)
        self.assertTrue(v._barline_dragging, '抓不到分割線')
        self.assertEqual(v._barline_drag_measure, 2, '要改的是分割線前面那一段')
        self.assertEqual((v._barline_drag_start_ms, v._barline_drag_orig_end_ms),
                         v.model.get_measure_time_range(2))

    def test_a_beat_line_inside_a_measure_is_not_grabbable(self):
        """以前抓到小節中間的拍線也會開始拖，然後去改前一小節的 BPM。"""
        v = self.view(with_split=False)
        v.barline_drag_enabled = True
        lo, hi = v.model.get_measure_time_range(2)
        beats = [int(ms) for _i, ms in v.model.get_beat_entries()
                 if lo < int(ms) < hi]
        self.assertTrue(beats, '這一小節裡沒有拍線，前提不成立')
        self.press_at(v, beats[0])
        self.assertFalse(v._barline_dragging)


if __name__ == '__main__':
    unittest.main()
