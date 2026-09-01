"""判定線的位置只有一條規則：永遠是鍵盤上緣。

「音符碰到鍵盤頂端＝判定的瞬間」這個視覺約定要成立，判定線、鍵盤上緣、
`_judge_ms` 換算出來的位置就必須是同一個 y。以前另外有一套「非跟隨模式時
固定在視窗底部 10%」的比例邏輯，和固定像素高度的鍵盤對不齊，判定線就會浮在
鍵盤上方；那套已經整個移除，這裡把它釘死。
"""

import unittest

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import JUDGELINE_COLOR
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, pitch):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 250
    n.gate = 250
    n.pitch = pitch
    n.hand = 0
    n.velocity = 90
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    return n


class JudgeLineTests(unittest.TestCase):
    MODES = ('pitch', 'measure', 'time')
    TIMES = (0.0, 1.0, 3000.0, 45000.0, 120000.0)

    def setUp(self):
        self.win = MainWindow()
        self.win.resize(1400, 860)
        self.model = NoteModel.create_new('t', 120.0, 300.0, 4)
        self.model.notes_tree = [make_note(i, i * 500, 60 + (i % 12))
                                 for i in range(240)]
        self.model.rebuild_display_cache()
        self.win._load_model_all(self.model)
        self.view = self.win.view
        self.win.show()
        _app.processEvents()

    def tearDown(self):
        self.win.close()

    def line_rows(self):
        """畫面上判定線佔了哪幾個 y。整條橫跨才算，避免抓到零星同色像素。"""
        img = self.view.grab().toImage()
        want = JUDGELINE_COLOR.name()
        step = 11
        need = (img.width() // step) * 0.6
        return [y for y in range(img.height())
                if sum(1 for x in range(0, img.width(), step)
                       if QColor(img.pixel(x, y)).name() == want) > need]

    def test_the_line_sits_on_the_keyboard_edge_in_every_mode(self):
        for mode in self.MODES:
            self.view.set_view_mode(mode)
            for ms in self.TIMES:
                self.view.set_judge_line(ms)
                _app.processEvents()
                rows = self.line_rows()
                top = self.view._judge_py()
                self.assertTrue(rows, '%s @%dms：完全看不到判定線' % (mode, ms))
                self.assertLessEqual(min(rows), top, (mode, ms, rows, top))
                self.assertGreaterEqual(max(rows) + 1, top, (mode, ms, rows, top))

    def test_the_keyboard_top_is_the_judge_line(self):
        self.view.set_view_mode('pitch')
        self.assertEqual(self.view._piano_top_py(), self.view._judge_py())

    def test_it_does_not_move_when_the_time_changes(self):
        self.view.set_view_mode('pitch')
        seen = set()
        for ms in self.TIMES:
            self.view.set_judge_line(ms)
            seen.add(self.view._judge_py())
        self.assertEqual(len(seen), 1, '判定線不該隨播放時間上下跑')

    def test_setting_the_time_scrolls_that_moment_onto_the_line(self):
        # 線不動，所以要動的是視窗
        self.view.set_view_mode('pitch')
        for ms in (3000.0, 45000.0):
            self.view.set_judge_line(ms)
            py = self.view._unit_to_py(
                self.view.mapper.ms_to_unit(ms) - self.view.window_start_unit)
            self.assertAlmostEqual(py, self.view._judge_py(), delta=1.5,
                                   msg='ms=%s 沒有被捲到判定線上' % ms)

    def test_stopping_hides_the_line(self):
        self.view.set_view_mode('pitch')
        self.view.set_judge_line(45000.0)
        _app.processEvents()
        self.assertTrue(self.line_rows())
        self.view.set_judge_line(None)
        _app.processEvents()
        self.assertEqual(self.line_rows(), [])

    def test_it_survives_a_bar_line_landing_on_the_same_y(self):
        # 0ms 剛好是小節線，畫在判定線之後就會整條把它蓋掉
        self.view.set_view_mode('pitch')
        self.view.set_judge_line(0.0)
        _app.processEvents()
        self.assertTrue(self.line_rows(), '小節線把判定線蓋掉了')

    def test_it_is_not_hidden_by_the_keyboard_border(self):
        # 鍵盤上緣有一條紫色外框，和判定線同一個 y
        self.view.set_view_mode('pitch')
        self.view.set_judge_line(45000.0)
        _app.processEvents()
        self.assertTrue(self.line_rows(), '被鍵盤的上框蓋掉了')

    def test_the_old_follow_mode_switch_is_gone(self):
        # 「跟隨 / 不跟隨」不再是一個選項，留著只會讓判定線又飄起來
        self.assertFalse(hasattr(self.view, 'set_follow_mode'))
        self.assertFalse(hasattr(self.view, '_follow_mode'))
        self.assertFalse(hasattr(self.win, '_set_follow_mode_all'))
        self.assertFalse(hasattr(self.win, '_follow_to_ms_all'))


if __name__ == '__main__':
    unittest.main()
