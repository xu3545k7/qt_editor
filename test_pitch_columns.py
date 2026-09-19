"""音高模式的欄位分色，以及「切換模式要立刻重畫」。

**分色**：原本只有「放置模式而且靠近滑鼠」那幾欄才上黑鍵底色，其餘整片全黑，
看不出自己在哪個音區、要數格子。改成整片依黑白鍵分色（預設），另一個選項是
原本的調性分色。

**立刻重畫**：`set_note_input_mode` / `set_note_duration` 都沒有呼叫 `update()`，
所以切換之後要等下一次事件（通常是使用者又點了一下）才看得到，感覺像沒反應。
"""

import unittest

from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])


def view():
    v = ChartView()
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    n = GNote(None, 0)
    n.start, n.end, n.gate = 0, 400, 400
    n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
    n.pitch, n.velocity = 60, 90
    m.notes_tree = [n]
    m.rebuild_display_cache()
    v.model = m
    v.pitch_mode = True
    v.resize(600, 400)
    return v


class ColumnModeTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings.get('pitch_column_mode', 'blackwhite')
        self.view = view()

    def tearDown(self):
        settings.set('pitch_column_mode', self._saved)

    def mode(self, value):
        settings.set('pitch_column_mode', value)
        self.view._pitch_column_cache = None

    def rects(self, value):
        """黑白鍵分色畫了幾塊底色。"""
        self.mode(value)
        drawn = []
        img = QImage(self.view.size(), QImage.Format_RGB32)
        qp = QPainter(img)
        original = qp.drawRect
        qp.drawRect = lambda *a: (drawn.append(a), original(*a))[1]
        try:
            self.view._draw_black_key_columns(qp)
        finally:
            qp.drawRect = original
            qp.end()
        return drawn

    def test_black_white_is_the_default(self):
        from qt_editor.settings import _DEFAULTS
        self.assertEqual(_DEFAULTS['pitch_column_mode'], 'blackwhite')

    def test_an_unknown_value_falls_back_to_black_white(self):
        self.mode('nonsense')
        self.assertEqual(self.view._pitch_column_mode(), 'blackwhite')

    def test_it_shades_every_column(self):
        # 白鍵和黑鍵都要上色（各站背景色一側），所以是全部欄位
        from qt_editor.chart_view import PITCH_GRID_KEYS
        self.assertEqual(len(self.rects('blackwhite')), PITCH_GRID_KEYS)

    def test_the_bands_span_the_full_height(self):
        for _x, y, _w, h in self.rects('blackwhite'):
            self.assertEqual(y, 0)
            self.assertEqual(h, self.view.height())

    def rendered_columns(self):
        """實際畫到畫布上的白鍵欄／黑鍵欄顏色。"""
        from qt_editor.chart_view import PITCH_MIDI_MIN, _is_black_pitch
        self.mode('blackwhite')
        img = QImage(self.view.size(), QImage.Format_RGB32)
        qp = QPainter(img)
        self.view._draw_bg(qp)
        self.view._draw_black_key_columns(qp)
        qp.end()
        white = black = None
        for i in range(self.view._display_key_count()):
            x1, x2 = self.view._key_span(i)
            x = int((x1 + x2) / 2)
            if not (0 <= x < img.width()):
                continue
            colour = img.pixelColor(x, img.height() // 2)
            if _is_black_pitch(PITCH_MIDI_MIN + i):
                black = colour
            else:
                white = colour
        return white, black

    def test_the_two_column_kinds_are_actually_distinguishable(self):
        """第一版把黑鍵設成 (26,26,32)，和背景 (28,28,32) 差 2 級 = 看不見。"""
        white, black = self.rendered_columns()
        self.assertIsNotNone(white)
        self.assertIsNotNone(black)
        self.assertGreaterEqual(white.red() - black.red(), 12,
                                '黑白鍵欄的亮度差太小，看不出交替')

    def test_white_is_lighter_and_black_is_darker_than_the_backdrop(self):
        """各站背景色的一側，整體亮度才不會被拉走。"""
        from qt_editor.chart_view import BG_COLOR
        white, black = self.rendered_columns()
        self.assertGreater(white.red(), BG_COLOR.red())
        self.assertLess(black.red(), BG_COLOR.red())

    def test_it_stays_a_backdrop(self):
        """再怎麼提高對比也不能亮到跟音符搶。"""
        white, _black = self.rendered_columns()
        self.assertLess(white.red(), 80)

    def test_scale_mode_turns_the_highlight_on(self):
        self.mode('scale')
        self.assertTrue(self.view._scale_highlight_on())

    def test_black_white_mode_turns_the_highlight_off(self):
        self.mode('blackwhite')
        self.assertFalse(self.view._scale_highlight_on(),
                         '黑白鍵分色時不該再疊調性底色')

    def test_the_highlight_never_applies_outside_pitch_mode(self):
        self.mode('scale')
        self.view.pitch_mode = False
        self.assertFalse(self.view._scale_highlight_on())

    def test_the_mode_is_read_once_per_frame(self):
        self.mode('blackwhite')
        self.assertEqual(self.view._pitch_column_mode(), 'blackwhite')
        settings.set('pitch_column_mode', 'scale')     # 不清快取
        self.assertEqual(self.view._pitch_column_mode(), 'blackwhite')
        self.view._pitch_column_cache = None
        self.assertEqual(self.view._pitch_column_mode(), 'scale')


class ImmediateRepaintTests(unittest.TestCase):
    """切換之後不重畫的話，要等使用者再點一下才看得到。"""

    def setUp(self):
        self.view = view()
        self.repaints = 0
        real = self.view.update

        def counted(*a, **k):
            self.repaints += 1
            return real(*a, **k)

        self.view.update = counted

    def test_toggling_placement_mode_repaints(self):
        self.view.set_note_input_mode(True)
        self.assertGreater(self.repaints, 0, '開啟放置模式沒有重畫')

    def test_leaving_placement_mode_repaints(self):
        self.view.set_note_input_mode(True)
        self.repaints = 0
        self.view.set_note_input_mode(False)
        self.assertGreater(self.repaints, 0, '關閉放置模式沒有重畫')

    def test_changing_the_note_value_repaints(self):
        self.view.set_note_duration(0.5)
        self.assertGreater(self.repaints, 0, '換時值沒有重畫（格線間隔會變）')


if __name__ == '__main__':
    unittest.main()
