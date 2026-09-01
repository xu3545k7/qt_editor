"""繪圖路徑上的快取與視窗裁切。

播放時每 16ms 重繪一次，所以任何「每幀掃全譜」的東西都會直接變成頓挫。這裡
釘住三件事：該快取的有快取、快取在編輯後會失效、裁切不會把該畫的音符弄丟。
"""

import unittest

from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, pitch, hand=0, end=None, velocity=80):
    n = GNote(None, idx)
    n.start = start
    n.end = end if end is not None else start + 200
    n.gate = n.end - n.start
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    n.track = hand
    n.channel = 0
    return n


class PaintCase(unittest.TestCase):
    def build(self, notes):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 600.0, 4)
        self.model.notes_tree = list(notes)
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        return self.view

    def paint(self):
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view.render(qp)
        finally:
            qp.end()


class WindowSliceTests(PaintCase):
    def setUp(self):
        # 每 100ms 一顆，共 20 秒
        self.notes = [make_note(i, i * 100, 60 + (i % 12)) for i in range(200)]
        self.build(self.notes)

    def test_it_returns_the_notes_in_range(self):
        got = self.view._notes_in_window(500, 900)
        self.assertEqual([n.start for n in got], [500, 600, 700, 800, 900])

    def test_the_bounds_are_inclusive(self):
        self.assertEqual(len(self.view._notes_in_window(500, 500)), 1)

    def test_an_empty_range_gives_nothing(self):
        self.assertEqual(self.view._notes_in_window(550, 560), [])

    def test_a_range_past_the_end_gives_nothing(self):
        self.assertEqual(self.view._notes_in_window(10 ** 9, 10 ** 9 + 1), [])

    def test_it_matches_a_plain_linear_filter(self):
        for lo, hi in ((0, 0), (0, 19900), (-5000, 300), (1234, 5678),
                       (19800, 99999)):
            want = [n for n in self.model.notes if lo <= n.start <= hi]
            self.assertEqual(self.view._notes_in_window(lo, hi), want, (lo, hi))

    def test_an_empty_chart_is_safe(self):
        view = self.build([])
        self.assertEqual(view._notes_in_window(0, 1000), [])

    def test_the_index_is_rebuilt_when_the_notes_change(self):
        self.view._notes_in_window(0, 100)
        self.model.notes_tree = [make_note(0, 12345, 60)]
        self.model.rebuild_display_cache()
        # 顆數變了、時間也變了：快取綁的是 list 身分，rebuild 之後一定會重算
        self.assertEqual([n.start for n in self.view._notes_in_window(0, 99999)],
                         [12345])

    def test_the_index_is_rebuilt_even_when_the_count_is_unchanged(self):
        # 顆數一樣但時間全變了——用長度比對的快取會在這裡漏掉
        self.view._notes_in_window(0, 100)
        self.model.notes_tree = [make_note(i, 50000 + i * 100, 60)
                                 for i in range(200)]
        self.model.rebuild_display_cache()
        self.assertEqual(self.view._notes_in_window(0, 100), [])
        self.assertEqual(len(self.view._notes_in_window(50000, 69900)), 200)


class MaxSpanTests(PaintCase):
    def test_it_reports_the_longest_note(self):
        view = self.build([make_note(0, 0, 60, end=200),
                           make_note(1, 500, 62, end=4500)])
        self.assertEqual(view._max_note_span_ms(), 4000.0)

    def test_an_empty_chart_is_zero(self):
        self.assertEqual(self.build([])._max_note_span_ms(), 0.0)

    def test_it_follows_an_edit(self):
        view = self.build([make_note(0, 0, 60, end=200)])
        self.assertEqual(view._max_note_span_ms(), 200.0)
        self.model.notes_tree.append(make_note(1, 100, 62, end=9000))
        self.model.rebuild_display_cache()
        self.assertEqual(view._max_note_span_ms(), 8900.0)


class LongHoldVisibilityTests(PaintCase):
    """裁切最容易出錯的地方：頭在畫面外、身體垂進來的長押。"""

    def visible_starts(self):
        self.paint()
        return sorted(n.start for _r, n in self.view._visible)

    def test_a_long_hold_starting_before_the_window_still_draws(self):
        view = self.build([make_note(0, 0, 60, end=60000),
                           make_note(1, 40000, 62)])
        view.set_view_mode('pitch')
        view.follow_to_ms(40000.0)
        self.assertIn(0, self.visible_starts(), '長押的頭在畫面上方外面也要畫')

    def test_notes_far_outside_the_window_are_skipped(self):
        notes = [make_note(i, i * 100, 60) for i in range(200)]
        view = self.build(notes)
        view.follow_to_ms(0.0)
        self.assertLess(len(self.visible_starts()), len(notes),
                        '畫面外的不該進 _visible')

    def test_the_windowing_never_loses_a_note(self):
        # 拿「不裁切」當對照組，逐位置比對可見集合
        notes = [make_note(i, i * 100, 60 + (i % 24),
                           end=i * 100 + (5000 if i % 37 == 0 else 200))
                 for i in range(300)]
        view = self.build(notes)
        real = ChartView._notes_in_window

        def full(_self, _lo, _hi):
            return _self.model.notes

        for ms in (0, 1000, 5000, 12000, 25000, 29900):
            view.follow_to_ms(float(ms))
            ChartView._notes_in_window = full
            self.paint()
            want = {id(n) for _r, n in view._visible}
            ChartView._notes_in_window = real
            self.paint()
            got = {id(n) for _r, n in view._visible}
            self.assertEqual(got, want, 'ms=%d' % ms)


class CacheInvalidationTests(PaintCase):
    def setUp(self):
        self.notes = [make_note(0, 0, 60, hand=0, velocity=40),
                      make_note(1, 500, 64, hand=0, velocity=100),
                      make_note(2, 1000, 48, hand=1, velocity=70)]
        self.build(self.notes)

    def test_the_dynamics_scale_is_cached(self):
        calls = []
        real = self.model.dynamics_range
        self.model.dynamics_range = lambda h: (calls.append(h), real(h))[1]
        for _ in range(50):
            self.view._dyn_scale(0)
            self.view._dyn_scale(1)
        self.assertEqual(len(calls), 2, '每個點都重掃全譜會吃掉四成繪圖時間')

    def test_the_dynamics_scale_follows_a_velocity_edit(self):
        before = self.view._dyn_scale(0)
        self.notes[1].velocity = 127
        self.view.note_edited.emit()
        self.assertNotEqual(self.view._dyn_scale(0), before)

    def test_editing_clears_every_chart_cache(self):
        self.view._dyn_scale(0)
        self.view._highlight_pitch_classes()
        self.view._notes_in_window(0, 100)
        self.view._max_note_span_ms()
        self.view.note_edited.emit()
        self.assertEqual(self.view._dyn_scale_cache, {})
        self.assertIsNone(self.view._in_key_cache)
        self.assertIsNone(self.view._note_start_cache)
        self.assertIsNone(self.view._max_span_cache)

    def test_loading_a_chart_clears_them_too(self):
        self.view._dyn_scale(0)
        self.view._max_note_span_ms()
        self.build([make_note(0, 0, 72)])
        self.assertEqual(self.view._dyn_scale_cache, {})
        self.assertIsNone(self.view._max_span_cache)

    def test_the_voice_colour_map_is_not_rebuilt_every_frame(self):
        calls = []
        real = self.view._build_channel_color_map
        self.view._build_channel_color_map = lambda: (calls.append(1), real())[1]
        for _ in range(20):
            self.view._ensure_channel_colors()
        self.assertLessEqual(len(calls), 1)

    def test_the_voice_colour_map_follows_a_channel_edit(self):
        self.view._ensure_channel_colors()
        calls = []
        real = self.view._build_channel_color_map
        self.view._build_channel_color_map = lambda: (calls.append(1), real())[1]
        self.view.note_edited.emit()
        self.view._ensure_channel_colors()
        self.assertEqual(len(calls), 1, '改了聲部就要重配色')

    def test_the_lane_flags_are_read_once_per_frame(self):
        self.view._lane_flag_cache = None
        first = self.view._lane_flags()
        self.assertIs(self.view._lane_flags(), first)
        self.paint()      # paintEvent 開頭會清掉，設定改了下一幀就看得到

    def test_velocity_shading_is_read_once_per_frame(self):
        from qt_editor.settings import settings
        was = settings.get('pitch_velocity_shading', True)
        try:
            settings.set('pitch_velocity_shading', False)
            self.paint()
            self.assertFalse(self.view._vel_shade_on)
            settings.set('pitch_velocity_shading', True)
            self.paint()
            self.assertTrue(self.view._vel_shade_on)
        finally:
            settings.set('pitch_velocity_shading', was)


class ActivePitchTests(PaintCase):
    def test_it_finds_the_sounding_notes(self):
        # 長押才會一直亮著；tap 只閃 PIANO_FLASH_MS 那一下
        long_note = make_note(0, 0, 60, end=1000)
        long_note.note_type = 2
        view = self.build([long_note, make_note(1, 2000, 64, end=2200)])
        view._judge_ms = 500.0
        self.assertIn(60, view._active_pitches())
        self.assertNotIn(64, view._active_pitches())

    def test_a_very_long_hold_is_still_found_much_later(self):
        # 二分是往回找「一個最長時值」，長押不能因此漏掉
        long_note = make_note(0, 0, 60, end=100000)
        long_note.note_type = 2
        view = self.build([long_note, make_note(1, 90000, 64, end=90200)])
        view._judge_ms = 89000.0
        self.assertIn(60, view._active_pitches())

    def test_nothing_sounds_before_the_chart_starts(self):
        view = self.build([make_note(0, 5000, 60)])
        view._judge_ms = 0.0
        self.assertEqual(view._active_pitches(), {})

    def test_no_judge_line_means_nothing_lit(self):
        lit = make_note(0, 0, 60, end=1000)
        lit.note_type = 2
        view = self.build([lit])
        view._judge_ms = None
        self.assertEqual(view._active_pitches(), {})


if __name__ == '__main__':
    unittest.main()
