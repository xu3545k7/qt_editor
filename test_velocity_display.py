"""音高模式在音符上顯示力度數字。

只在音高模式畫：小節／時間模式看的是「玩家要按哪裡」，多一組數字只會干擾；
音高模式看的是音樂本身，力度才是資訊。

注意：測試環境（offscreen 且無字型）畫不出任何文字，所以這裡驗證的是
「有沒有要求畫、畫的是什麼值」，不是像素。
"""

import unittest

from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView, VELOCITY_TEXT_MIN_H
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, pitch, velocity):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 380
    n.gate = 380
    n.pitch = pitch
    n.hand = idx % 2
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    return n


class VelocityDisplayTests(unittest.TestCase):
    def setUp(self):
        settings.set('pitch_velocity_numbers', True)
        self.addCleanup(settings.set, 'pitch_velocity_numbers', True)

    def _render(self, mode='pitch', zoom_steps=2, velocities=(20, 64, 127, None)):
        view = ChartView()
        view.resize(1200, 700)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        model.notes_tree = [
            make_note(i, 300 + i * 420, 48 + i * 4, v)
            for i, v in enumerate(velocities)
        ]
        model.rebuild_display_cache()
        view.load_model(model)
        view.rebuild_mapper()
        view.set_view_mode(mode)
        for _ in range(zoom_steps):
            view.zoom(0.5)
        view.window_start_unit = view.mapper.ms_to_unit(200.0)

        drawn = []
        original = view._draw_velocity_text

        def spy(qp, cx, cy, text):
            drawn.append(text)
            return original(qp, cx, cy, text)

        view._draw_velocity_text = spy
        view.render(QImage(1200, 700, QImage.Format_ARGB32))
        heights = [view._note_rect(n).height() for n in model.notes
                   if view._note_rect(n) is not None]
        return drawn, (max(heights) if heights else 0.0)

    def test_pitch_mode_shows_the_velocity_of_every_note_that_has_one(self):
        drawn, height = self._render()
        self.assertGreaterEqual(height, VELOCITY_TEXT_MIN_H)
        self.assertEqual(drawn, ['20', '64', '127'])   # velocity=None 的不畫

    def test_other_view_modes_never_show_velocity(self):
        for mode in ('measure', 'time'):
            drawn, height = self._render(mode=mode)
            self.assertGreaterEqual(height, VELOCITY_TEXT_MIN_H, mode)
            self.assertEqual(drawn, [], mode)

    def test_short_notes_are_skipped_so_dense_views_stay_readable(self):
        drawn, height = self._render(zoom_steps=0)
        self.assertLess(height, VELOCITY_TEXT_MIN_H)
        self.assertEqual(drawn, [])

    def test_the_toggle_turns_it_off(self):
        settings.set('pitch_velocity_numbers', False)
        drawn, _h = self._render()
        self.assertEqual(drawn, [])

    def test_out_of_range_velocity_is_clamped_to_0_127(self):
        drawn, _h = self._render(velocities=(-5, 999))
        self.assertEqual(drawn, ['0', '127'])

    def test_velocity_label_ignores_junk_values(self):
        n = make_note(0, 0, 60, 'oops')
        self.assertIsNone(ChartView._velocity_label(n))
        n.velocity = None
        self.assertIsNone(ChartView._velocity_label(n))


if __name__ == '__main__':
    unittest.main()
