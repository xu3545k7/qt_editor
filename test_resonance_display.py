"""長音的深淺、力度數字、標示位置。

使用者要的：我們猜「真的按著」的那一段畫深、殘響畫淺；力度用數字（不要深淺）；
音高和力度都標在音符前緣。
"""

import unittest
from unittest import mock

from PyQt5.QtCore import QRectF
from PyQt5.QtWidgets import QApplication

from qt_editor.models import GNote, NoteModel


def note(idx, start, end, pitch, note_type=2, hand=0):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, end, end - start
    n.pitch, n.hand, n.note_type = pitch, hand, note_type
    n.min_key, n.max_key = 5, 7
    n.velocity = 90
    return n


class ReleaseGuessTests(unittest.TestCase):
    """猜「其實在哪一刻放開」——裁切與畫面共用這一份判斷。"""

    def model(self, notes):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = notes
        m.rebuild_display_cache()
        return m

    def test_the_same_pitch_again_means_it_was_released(self):
        hold = note(0, 0, 4000, 60)
        m = self.model([hold, note(1, 1000, 1100, 60, 0)])
        self.assertEqual(m.pedal_release_guesses()[id(hold)], 1000)

    def test_a_note_the_hand_cannot_reach_means_it_was_released(self):
        hold = note(0, 0, 4000, 60)
        m = self.model([hold, note(1, 800, 900, 90, 0)])
        self.assertEqual(m.pedal_release_guesses()[id(hold)], 800)

    def test_a_reachable_note_is_not_evidence(self):
        hold = note(0, 0, 4000, 60)
        m = self.model([hold, note(1, 800, 900, 67, 0)])
        self.assertEqual(m.pedal_release_guesses(), {})

    def test_taps_are_never_guessed(self):
        tap = note(0, 0, 4000, 60, note_type=0)
        m = self.model([tap, note(1, 800, 900, 90, 0)])
        self.assertEqual(m.pedal_release_guesses(), {})

    def test_guessing_does_not_modify_anything(self):
        hold = note(0, 0, 4000, 60)
        m = self.model([hold, note(1, 1000, 1100, 60, 0)])
        before = (hold.start, hold.end, hold.gate)
        m.pedal_release_guesses()
        self.assertEqual((hold.start, hold.end, hold.gate), before,
                         '只是猜，不可以動到音符')

    def test_trimming_uses_the_same_guess(self):
        hold = note(0, 0, 4000, 60)
        m = self.model([hold, note(1, 1000, 1100, 60, 0)])
        cut = m.pedal_release_guesses()[id(hold)]
        m.trim_pedal_sustained_holds(gap_ms=100)
        self.assertEqual(int(hold.end), cut - 100,
                         '畫成淺色的那一段，正好是按下裁切會被裁掉的那一段')


class DrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, notes):
        from qt_editor.chart_view import ChartView
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = notes
        m.rebuild_display_cache()
        v = ChartView()
        v.load_model(m)
        v.pitch_mode = True
        self._v = v
        return v

    def test_the_resonance_slice_is_drawn_light(self):
        hold = note(0, 0, 4000, 60)
        v = self.view([hold, note(1, 1000, 1100, 60, 0)])
        rect = QRectF(0, 0, 20, 400)        # 上＝晚，下＝早
        qp = mock.MagicMock()
        v._draw_resonance(qp, hold, rect, 3.0)
        self.assertTrue(qp.drawRect.called, '殘響那一段要蓋一層淺色')
        drawn = qp.drawRect.call_args[0][0]
        # 放開在 1000/4000 → 前 1/4 是按著的（下方），上面 3/4 是殘響
        self.assertAlmostEqual(drawn.top(), 0.0, places=3)
        self.assertAlmostEqual(drawn.height(), 300.0, places=3)

    def test_a_note_with_no_guess_is_left_alone(self):
        hold = note(0, 0, 4000, 60)
        v = self.view([hold])
        qp = mock.MagicMock()
        v._draw_resonance(qp, hold, QRectF(0, 0, 20, 400), 3.0)
        self.assertFalse(qp.drawRect.called)

    def test_nothing_is_drawn_outside_pitch_mode(self):
        hold = note(0, 0, 4000, 60)
        v = self.view([hold, note(1, 1000, 1100, 60, 0)])
        v.pitch_mode = False
        qp = mock.MagicMock()
        v._draw_resonance(qp, hold, QRectF(0, 0, 20, 400), 3.0)
        self.assertFalse(qp.drawRect.called)

    def test_the_guess_is_cached_between_frames(self):
        hold = note(0, 0, 4000, 60)
        v = self.view([hold, note(1, 1000, 1100, 60, 0)])
        with mock.patch.object(NoteModel, 'pedal_release_guesses',
                               autospec=True, return_value={}) as guess:
            v._release_guesses()
            v._release_guesses()
            v._release_guesses()
        self.assertEqual(guess.call_count, 1, '每幀重算就會卡')

    def test_editing_a_hold_invalidates_the_cache(self):
        hold = note(0, 0, 4000, 60)
        v = self.view([hold, note(1, 1000, 1100, 60, 0)])
        v._release_guesses()
        hold.end = 2000
        with mock.patch.object(NoteModel, 'pedal_release_guesses',
                               autospec=True, return_value={}) as guess:
            v._release_guesses()
        self.assertEqual(guess.call_count, 1, '長音改了就要重算')


class VelocityDisplayTests(unittest.TestCase):
    def test_shading_is_off_by_default(self):
        from qt_editor import chart_view as CV
        from qt_editor.settings import _DEFAULTS
        self.assertFalse(_DEFAULTS['pitch_velocity_shading'],
                         '力度用數字標就好，不要再用深淺')
        with mock.patch.dict(_DEFAULTS, {}, clear=False):
            self.assertFalse(CV._velocity_shading_on())

    def test_numbers_are_on_by_default(self):
        from qt_editor import chart_view as CV
        self.assertTrue(CV._velocity_numbers_on())

    def test_shading_leaves_the_colour_alone_when_off(self):
        from PyQt5.QtGui import QColor

        from qt_editor.chart_view import ChartView
        app = QApplication.instance() or QApplication([])
        v = ChartView()
        v.pitch_mode = True
        v._vel_shade_on = False
        base = QColor(200, 60, 60)
        n = note(0, 0, 100, 60)
        n.velocity = 10
        self.assertEqual(v._velocity_shaded(base, n), base)
        self.assertTrue(app is not None)


class PreviewIsUntouchedTests(unittest.TestCase):
    """制譜那邊的預覽（小節／時間模式）維持原樣：長條畫滿、標示置中。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, pitch_mode):
        from qt_editor.chart_view import ChartView
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        hold = note(0, 0, 4000, 60)
        m.notes_tree = [hold, note(1, 1000, 1100, 60, 0)]
        m.rebuild_display_cache()
        v = ChartView()
        v.load_model(m)
        v.pitch_mode = pitch_mode
        self._v = v
        return v, hold

    def test_the_hold_is_drawn_full_in_preview(self):
        v, hold = self.view(pitch_mode=False)
        qp = mock.MagicMock()
        v._draw_resonance(qp, hold, QRectF(0, 0, 20, 400), 3.0)
        self.assertFalse(qp.drawRect.called, '預覽的長條要畫滿，不分深淺')

    def test_pitch_mode_does_split_it(self):
        v, hold = self.view(pitch_mode=True)
        qp = mock.MagicMock()
        v._draw_resonance(qp, hold, QRectF(0, 0, 20, 400), 3.0)
        self.assertTrue(qp.drawRect.called)


if __name__ == '__main__':
    unittest.main()
