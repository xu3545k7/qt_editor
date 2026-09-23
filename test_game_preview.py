"""遊戲預覽（斜降的小視窗）：幾何、跟著播放跑、真的畫得出來。

預覽要回答的是「這段進遊戲長怎樣」，所以這裡驗的是遊戲畫面的那幾條規則：
判定線那一刻的音符落在判定線上、越遠越窄越擠、隱藏音符不出現，以及暫停時
捲動譜面預覽也會跟著看向那一段。
"""

import unittest

from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from qt_editor import game_preview as GP
from qt_editor.models import GNote, TOTAL_GAME_KEYS


def note(start, end=None, *, hand=0, nt=0, lo=10, hi=12, hidden=False):
    n = GNote(None, 0)
    n.start = start
    n.end = end if end is not None else start + 100
    n.gate = n.end - n.start
    n.note_type = nt
    n.hand = hand
    n.min_key, n.max_key = lo, hi
    n.pitch = 60
    if hidden:
        n.hidden = True
    return n


class _CanvasCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def canvas(self, notes=(), size=(480, 360)):
        c = GP.GamePreviewCanvas(notes)
        c.resize(*size)
        return c


class GeometryTests(_CanvasCase):
    def test_the_note_being_judged_sits_on_the_judge_line(self):
        c = self.canvas()
        c.set_time(5000)
        self.assertAlmostEqual(c._y(c._pos_of(5000)), c._judge_y(), places=6)

    def test_further_away_means_higher_up_the_screen(self):
        c = self.canvas()
        c.set_time(0)
        ys = [c._y(c._pos_of(ms)) for ms in (0, 200, 600, 1200)]
        self.assertEqual(ys, sorted(ys, reverse=True))

    def test_further_away_means_narrower(self):
        c = self.canvas()
        near = c._lane_x(TOTAL_GAME_KEYS, 0.0) - c._lane_x(0, 0.0)
        far = c._lane_x(TOTAL_GAME_KEYS, 1.0) - c._lane_x(0, 1.0)
        self.assertLess(far, near)
        self.assertAlmostEqual(far / near, GP.FAR_WIDTH_RATIO, places=6)

    def test_the_far_end_is_compressed(self):
        """真的透視是 1/z：等距的兩段時間，遠的那段在畫面上要短一些。"""
        c = self.canvas()
        c.set_time(0)
        near_gap = c._y(c._pos_of(0)) - c._y(c._pos_of(300))
        far_gap = c._y(c._pos_of(900)) - c._y(c._pos_of(1200))
        self.assertGreater(near_gap, far_gap)

    def test_lanes_stay_centred_and_ordered(self):
        c = self.canvas()
        for pos in (0.0, 0.5, 1.0):
            xs = [c._lane_x(lane, pos) for lane in range(TOTAL_GAME_KEYS + 1)]
            self.assertEqual(xs, sorted(xs))
            middle = (xs[0] + xs[-1]) / 2.0
            self.assertAlmostEqual(middle, c.width() / 2.0, places=6)

    def test_the_view_distance_changes_how_far_ahead_you_see(self):
        c = self.canvas()
        c.set_time(0)
        c.set_lead_ms(1000)
        self.assertAlmostEqual(c._pos_of(1000), 1.0, places=6)
        c.set_lead_ms(2000)
        self.assertAlmostEqual(c._pos_of(1000), 0.5, places=6)

    def test_the_view_distance_is_clamped(self):
        c = self.canvas()
        c.set_lead_ms(10)
        self.assertEqual(c.lead_ms, GP.MIN_LEAD_MS)
        c.set_lead_ms(999999)
        self.assertEqual(c.lead_ms, GP.MAX_LEAD_MS)


class WhatIsVisibleTests(_CanvasCase):
    def test_hidden_notes_never_show_up(self):
        # 隱藏音符只發聲、玩家看不到（和 preview_window 同一個理由）
        c = self.canvas([note(1000), note(1000, hidden=True)])
        self.assertEqual(len(c.notes), 1)

    def test_notes_beyond_the_view_are_skipped(self):
        c = self.canvas([note(500), note(50_000)])
        c.set_time(0)
        self.assertEqual([n.start for n, _pos in c._visible_notes()], [500])

    def test_a_hold_stays_while_its_tail_is_still_running(self):
        c = self.canvas([note(0, 3000, nt=2)])
        c.set_time(1500)                        # 頭過了判定線，尾巴還在
        self.assertEqual(len(c._visible_notes()), 1)

    def test_a_finished_note_goes_away(self):
        c = self.canvas([note(0, 100)])
        c.set_time(4000)
        self.assertEqual(c._visible_notes(), [])

    def test_the_keyboard_lights_up_only_while_held(self):
        c = self.canvas([note(1000, 2000, nt=2, lo=4, hi=6)])
        c.set_time(500)
        self.assertEqual(c._held_lanes(), set())
        c.set_time(1500)
        self.assertEqual(c._held_lanes(), {4, 5, 6})
        c.set_time(2500)
        self.assertEqual(c._held_lanes(), set())

    def test_hands_get_different_colours(self):
        self.assertNotEqual(GP._colors(note(0, hand=0)), GP._colors(note(0, hand=1)))

    def test_soft_notes_are_not_hand_coloured(self):
        soft = GP._colors(note(0, nt=1, hand=0))
        self.assertEqual(soft, GP._colors(note(0, nt=1, hand=1)))
        self.assertNotEqual(soft, GP._colors(note(0, hand=0)))


class PaintingTests(_CanvasCase):
    def _render(self, notes, now):
        c = self.canvas(notes)
        c.set_time(now)
        image = QImage(c.width(), c.height(), QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            c.render(painter)
        finally:
            painter.end()
        return image

    def test_every_note_type_paints_without_blowing_up(self):
        notes = [note(200, nt=0), note(400, nt=1), note(600, nt=3),
                 note(800, 1600, nt=2), note(900, 1000, nt=4),
                 note(1000, 1800, nt=0x40 | 0x02)]
        for now in (0, 500, 1200, 3000):
            image = self._render(notes, now)
            self.assertFalse(image.isNull())

    def test_something_is_actually_drawn(self):
        blank = self._render([], 0)
        with_notes = self._render([note(300, lo=0, hi=27)], 0)
        self.assertNotEqual(blank.constBits().asstring(blank.byteCount()),
                            with_notes.constBits().asstring(with_notes.byteCount()))

    def test_an_empty_chart_still_paints(self):
        self.assertFalse(self._render([], 0).isNull())


class WindowWiringTests(_CanvasCase):
    def test_the_window_passes_time_and_notes_to_the_canvas(self):
        win = GP.GamePreviewWindow([note(100)])
        try:
            win.set_time(1234)
            self.assertEqual(win.canvas.now_ms, 1234)
            win.set_notes([note(1), note(2)])
            self.assertEqual(len(win.canvas.notes), 2)
            win.set_time(None)                  # 沒有時刻就維持原狀，不是跳到 0
            self.assertEqual(win.canvas.now_ms, 1234)
        finally:
            win.close()

    def test_the_view_distance_box_drives_the_canvas(self):
        win = GP.GamePreviewWindow()
        try:
            win._lead.setValue(700)
            self.assertEqual(win.canvas.lead_ms, 700)
            win._flash.setChecked(False)
            self.assertFalse(win.canvas.show_flash)
        finally:
            win.close()


class MainWindowTests(unittest.TestCase):
    """主視窗：開啟、跟著判定線跑、關掉之後不再餵資料。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        preview = getattr(cls.win, '_game_preview', None)
        if preview is not None:
            preview.close()
        cls.win.view.model.dirty = False
        cls.win.close()

    def test_opening_it_twice_reuses_the_same_window(self):
        self.win.open_game_preview()
        first = self.win._game_preview
        self.assertIsNotNone(first)
        self.win.open_game_preview()
        self.assertIs(self.win._game_preview, first)

    def test_the_judge_line_drives_the_preview(self):
        self.win.open_game_preview()
        self.win._set_judge_line_all(4321.0)
        self.assertEqual(self.win._game_preview.canvas.now_ms, 4321.0)

    def test_stopping_falls_back_to_where_the_judge_line_rests(self):
        # 暫停後捲動譜面，預覽也要看向那一段
        self.win.open_game_preview()
        self.win._set_judge_line_all(None)
        self.assertAlmostEqual(self.win._game_preview.canvas.now_ms,
                               self.win.view.judge_line_view_ms(), places=3)

    def test_feeding_a_closed_preview_is_harmless(self):
        self.win.open_game_preview()
        self.win._game_preview.close()
        self.win._set_judge_line_all(10.0)      # 不能丟例外

    def test_nothing_happens_when_it_was_never_opened(self):
        self.win._game_preview = None
        self.win._set_judge_line_all(20.0)
        self.assertIsNone(self.win._game_preview)


if __name__ == '__main__':
    unittest.main()
