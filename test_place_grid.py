"""放置格線：依「放置時值」畫的水平參考線。

間隔就是放置模式選的音符長度，而且用**同一個吸附函式**算位置，所以線正好落在
音符會被放到的地方——放之前就看得到會落在哪一條。

三段開關（偏好設定「放置格線」）：`placement`（預設，只有放置模式開著時畫）、
`always`、`never`。
"""

import unittest

from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])


class PlaceGridTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings.get('place_grid_mode', 'placement')
        self.view = ChartView()
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        n = GNote(None, 0)
        n.start, n.end, n.gate = 0, 400, 400
        n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
        n.pitch, n.velocity = 60, 90
        m.notes_tree = [n]
        m.rebuild_display_cache()
        self.view.model = m
        self.view.resize(600, 800)
        self.drawn = []
        self.view._draw_place_grid = self._spy(self.view._draw_place_grid)

    def _spy(self, real):
        view = self.view
        drawn = self.drawn

        def wrapper(qp):
            lines = []
            original = qp.drawLine

            def capture(*args):
                lines.append(args)
                return original(*args)

            qp.drawLine = capture
            try:
                real(qp)
            finally:
                qp.drawLine = original
            drawn.append(lines)
        return wrapper

    def tearDown(self):
        settings.set('place_grid_mode', self._saved)

    def lines(self, mode, input_mode, beats=1.0):
        settings.set('place_grid_mode', mode)
        self.view._place_grid_cache = None
        self.view.set_note_input_mode(input_mode)
        self.view.set_note_duration(beats)
        self.drawn.clear()
        img = QImage(self.view.size(), QImage.Format_RGB32)
        qp = QPainter(img)
        self.view._draw_place_grid(qp)
        qp.end()
        return self.drawn[-1] if self.drawn else []

    # ── 三段開關 ────────────────────────────────────────────────
    def test_placement_mode_draws_only_while_placing(self):
        self.assertEqual(self.lines('placement', False), [],
                         '沒開放置模式就不該畫')
        self.assertTrue(self.lines('placement', True), '開了放置模式卻沒畫')

    def test_always_draws_even_when_not_placing(self):
        self.assertTrue(self.lines('always', False))

    def test_never_draws_nothing(self):
        self.assertEqual(self.lines('never', True), [])
        self.assertEqual(self.lines('never', False), [])

    def test_an_unknown_mode_falls_back_to_placement(self):
        settings.set('place_grid_mode', 'nonsense')
        self.view._place_grid_cache = None
        self.assertEqual(self.view._place_grid_mode(), 'placement')

    # ── 間隔要跟著放置時值 ──────────────────────────────────────
    def test_a_shorter_note_value_gives_more_lines(self):
        quarter = len(self.lines('always', True, beats=1.0))
        eighth = len(self.lines('always', True, beats=0.5))
        self.assertGreater(eighth, quarter,
                           '八分音符的格線該比四分音符密')

    def test_halving_the_value_roughly_doubles_the_lines(self):
        quarter = len(self.lines('always', True, beats=1.0))
        eighth = len(self.lines('always', True, beats=0.5))
        self.assertAlmostEqual(eighth / max(1, quarter), 2.0, delta=0.4)

    def test_the_lines_are_horizontal_and_span_the_width(self):
        for x1, y1, x2, y2 in self.lines('always', True):
            self.assertEqual(y1, y2, '不是水平線')
            self.assertEqual(x1, 0)
            self.assertEqual(x2, self.view.width())

    def test_they_sit_where_a_note_would_snap(self):
        """線的位置＝吸附位置，這是整個功能的重點。"""
        settings.set('place_grid_mode', 'always')
        self.view._place_grid_cache = None
        self.view.set_note_duration(1.0)
        ys = sorted(y1 for _x1, y1, _x2, _y2 in self.lines('always', True))
        for y in ys[1:-1]:
            unit = self.view._py_to_unit_abs(y)
            snapped = self.view._snap_unit_to_duration(unit, 1.0)
            self.assertAlmostEqual(unit, snapped, delta=0.02,
                                   msg='y=%d 不在吸附點上' % y)

    def test_a_very_dense_value_draws_nothing(self):
        # 一格不到 4px 時整片會糊成底色，不如不畫
        self.assertEqual(self.lines('always', True, beats=1.0 / 64), [])

    def test_the_setting_is_read_once_per_frame(self):
        self.lines('always', True)
        settings.set('place_grid_mode', 'never')     # 不清快取
        self.assertEqual(self.view._place_grid_mode(), 'always')
        self.view._place_grid_cache = None
        self.assertEqual(self.view._place_grid_mode(), 'never')


class BothPanesTests(unittest.TestCase):
    """分割檢視：一格開放置，另一格也要看得到格線。

    放置模式本身是單格的（工具列固定操作某一格），但格線是給眼睛看的參考，
    另一格顯示同一段音樂時也該看得到落點。
    """

    def setUp(self):
        from qt_editor.main_window import MainWindow
        self._saved = settings.get('place_grid_mode', 'placement')
        settings.set('place_grid_mode', 'placement')
        global _win
        try:
            self.win = _win
        except NameError:
            self.win = None
        if self.win is None:
            self.win = MainWindow()
            _win = self.win

    def tearDown(self):
        settings.set('place_grid_mode', self._saved)
        for v in self.win._panes:
            v.set_note_input_mode(False)
        self.win._sync_place_grid_peers()

    def test_the_other_pane_learns_about_it(self):
        self.win._panes[0].set_note_input_mode(True)
        self.win._sync_place_grid_peers()
        self.assertTrue(self.win._panes[1]._peer_placing,
                        '另一格不知道有人在放置，格線不會畫')

    def test_the_placing_pane_does_not_mark_itself(self):
        self.win._panes[0].set_note_input_mode(True)
        self.win._sync_place_grid_peers()
        self.assertFalse(self.win._panes[0]._peer_placing)

    def test_the_other_pane_does_not_become_placeable(self):
        """只傳格線，不打開放置模式——不然點下去會誤放音符。"""
        self.win._panes[0].set_note_input_mode(True)
        self.win._sync_place_grid_peers()
        self.assertFalse(self.win._panes[1]._note_input_mode)

    def test_it_clears_when_placement_ends(self):
        self.win._panes[0].set_note_input_mode(True)
        self.win._sync_place_grid_peers()
        self.win._panes[0].set_note_input_mode(False)
        self.win._sync_place_grid_peers()
        self.assertFalse(self.win._panes[1]._peer_placing)

    def test_the_peer_flag_makes_the_grid_appear(self):
        pane = self.win._panes[1]
        pane.resize(600, 400)
        pane._place_grid_cache = None
        pane.set_note_input_mode(False)

        def count():
            drawn = []
            img = QImage(pane.size(), QImage.Format_RGB32)
            qp = QPainter(img)
            original = qp.drawLine
            qp.drawLine = lambda *a: (drawn.append(a), original(*a))[1]
            try:
                pane._draw_place_grid(qp)
            finally:
                qp.drawLine = original
                qp.end()
            return len(drawn)

        pane.set_peer_placement(False)
        self.assertEqual(count(), 0)
        pane.set_peer_placement(True)
        self.assertGreater(count(), 0, '收到 peer 旗標之後仍然沒畫格線')


if __name__ == '__main__':
    unittest.main()
