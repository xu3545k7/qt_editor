"""格線配置：跟著放置時值，再搭上自己勾的幾層 N 分音符；重疊的只畫粗的那條。"""

import contextlib
import io
import unittest

from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView, PLACE_GRID_COLOR, grid_division_color
from qt_editor.models import NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])

KEYS = ('grid_follow_placement', 'grid_divisions', 'place_grid_mode')


class GridLayerTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.view = ChartView()
        self.view.resize(1000, 700)
        self.view.load_model(NoteModel.create_new('t', 120.0, 30.0, 4))
        self.view.rebuild_mapper()
        self.view.set_note_duration(1.0)          # 放置時值：四分音符

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def layers(self):
        self.view._grid_layer_cache = None
        return self.view.grid_layers()

    def test_default_is_just_the_placement_value(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[])
        self.assertEqual(self.layers(), [(1.0, PLACE_GRID_COLOR)])

    def test_layers_can_be_combined_coarse_first(self):
        settings._data.update(grid_follow_placement=False, grid_divisions=[16, 4, 12])
        beats = [b for b, _c in self.layers()]
        self.assertEqual(beats, [1.0, 4.0 / 12, 0.25])

    def test_a_custom_division_works(self):
        settings._data.update(grid_follow_placement=False, grid_divisions=[20])
        self.assertAlmostEqual(self.layers()[0][0], 0.2)

    def test_the_placement_layer_is_not_drawn_twice(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[4, 16])
        beats = [b for b, _c in self.layers()]
        self.assertEqual(beats, [1.0, 0.25])

    def test_triplets_get_their_own_colour(self):
        self.assertNotEqual(grid_division_color(12).name(), grid_division_color(16).name())
        self.assertNotEqual(grid_division_color(4).name(), grid_division_color(32).name())

    def test_drawing_several_layers(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[4, 16, 12],
                              place_grid_mode='always')
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view.render(qp)
        finally:
            qp.end()

    def painter_calls(self):
        from unittest import mock
        qp = mock.Mock()
        self.view._grid_layer_cache = None
        self.view._place_grid_cache = None
        self.view._draw_place_grid(qp)
        return qp.drawLine.call_args_list

    def test_never_mode_draws_nothing(self):
        settings._data.update(grid_divisions=[4], place_grid_mode='never')
        self.assertEqual(self.painter_calls(), [])

    def test_overlapping_lines_are_drawn_once(self):
        settings._data.update(grid_follow_placement=False, place_grid_mode='always')
        settings._data['grid_divisions'] = [4]
        quarter = len(self.painter_calls())
        settings._data['grid_divisions'] = [4, 8]
        both = len(self.painter_calls())
        settings._data['grid_divisions'] = [8]
        eighth = len(self.painter_calls())
        self.assertGreater(quarter, 0)
        self.assertEqual(both, eighth, '4 分和 8 分重疊的位置只畫一條')


class GridMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        settings._data.update(grid_follow_placement=True, grid_divisions=[],
                              place_grid_mode='placement')

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def menu_texts(self):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu()
        self.win._fill_grid_menu(menu)
        texts = [a.text() for a in menu.actions()]      # 選單還活著的時候就讀完
        menu.deleteLater()
        return texts

    def test_the_menu_lists_the_placement_values(self):
        texts = self.menu_texts()
        for name in ('跟著放置時值', '四分音符', '16分音符', '12分音符（八分三連）', '自訂 N 分音符…'):
            self.assertIn(name, texts)

    def test_ticking_a_division_saves_it_and_shows_the_grid(self):
        self.win._toggle_grid_division(16, True)
        self.win._toggle_grid_division(4, True)
        self.assertEqual(settings.get('grid_divisions'), [4, 16])
        self.assertEqual(settings.get('place_grid_mode'), 'always',
                         '勾了格線卻只在放置模式顯示，會以為沒作用')
        self.win._toggle_grid_division(16, False)
        self.assertEqual(settings.get('grid_divisions'), [4])

    def test_custom_divisions_show_up_in_the_menu(self):
        self.win._toggle_grid_division(20, True)
        texts = self.menu_texts()
        self.assertIn('20分音符', texts)

    def test_the_toolbar_has_the_button(self):
        self.assertTrue(any(getattr(t, 'grid_btn', None) is not None for t in self.win._toolbars))


if __name__ == '__main__':
    unittest.main()


class DragFollowsGridTests(unittest.TestCase):
    """格線多細，手動拉長縮短的單位就多細。"""

    def setUp(self):
        from PyQt5.QtCore import QEvent, QPoint, Qt
        from PyQt5.QtGui import QMouseEvent
        from qt_editor.models import GNote
        self.QEvent, self.QPoint, self.Qt, self.QMouseEvent = QEvent, QPoint, Qt, QMouseEvent
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 60.0, 4)      # 一拍 500ms
        note = GNote(None, 0)
        note.start, note.end, note.gate = 2000, 4000, 2000
        note.pitch, note.hand, note.velocity = 60, 0, 90
        note.min_key, note.max_key, note.note_type = 10, 12, 2
        self.model.notes_tree = [note]
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_note_duration(1.0)                          # 放置時值：四分
        self.note = note

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def step(self):
        self.view._grid_layer_cache = None
        return self.view.drag_step_beats()

    def test_without_a_grid_it_is_the_placement_value(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[])
        self.assertEqual(self.step(), 1.0)

    def test_the_finest_grid_layer_wins(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[16, 12])
        self.assertAlmostEqual(self.step(), 0.25)

    def test_a_coarse_grid_makes_coarse_steps(self):
        settings._data.update(grid_follow_placement=False, grid_divisions=[2])
        self.assertAlmostEqual(self.step(), 2.0)

    def test_dragging_a_tail_snaps_to_the_grid(self):
        settings._data.update(grid_follow_placement=True, grid_divisions=[16])
        self.view._grid_layer_cache = None
        from PyQt5.QtGui import QPainter, QPixmap
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view.render(qp)
        finally:
            qp.end()
        rect = [r for r, n in self.view._visible if n is self.note][0]
        tail = self.QPoint(int(rect.center().x()), int(rect.top()))
        self.view._begin_hold_tail_drag(self.note)
        self.view._drag_hold_tail(self.QPoint(tail.x(), tail.y() - 37))
        # 16 分音符 = 125ms，長度一定是它的整數倍
        self.assertEqual(self.note.gate % 125, 0, self.note.gate)
