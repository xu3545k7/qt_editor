"""延音踏板改用真正的 CC64 播放。

以前踏板是靠「把音符尾巴延長到放開踏板為止」模擬的。那樣有兩個問題：

1. 同音重複時，`_trim_overlaps` 會為了不讓舊的 note-off 切掉新音而把前一顆
   **裁短**——踏板明明踩著，那顆音反而比沒踏板還短。
2. 合成器收不到 CC64，音是走 release 而不是留在 sustain，共鳴聽感不同；
   半踏、快速換踏更是完全表現不出來。

現在改成送真正的 CC64，音符長度完全不動。
"""

import array
import unittest

from qt_editor import midi_preview as mp
from qt_editor.models import GNote, NoteModel


def make_note(idx, start, pitch, velocity=90, dur=300):
    n = GNote(None, idx)
    n.start = start
    n.end = start + dur
    n.gate = dur
    n.pitch = pitch
    n.hand = 0
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    return n


def model_with(notes, spans=()):
    m = NoteModel.create_new('t', 120.0, 10.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    m.pedal_spans = [list(s) for s in spans]
    return m


class PedalSpanRangeTests(unittest.TestCase):
    def test_spans_are_clipped_to_the_render_range(self):
        m = model_with([make_note(0, 200, 60)], [(150.0, 2000.0)])
        self.assertEqual(mp.pedal_spans_in_range(m, 0.0, 3000.0), [(150.0, 2000.0)])
        # 從中間開始播：本來就踩著的踏板，從第一個取樣點起就是踩下狀態
        self.assertEqual(mp.pedal_spans_in_range(m, 500.0, 3000.0), [(500.0, 2000.0)])
        self.assertEqual(mp.pedal_spans_in_range(m, 0.0, 1000.0), [(150.0, 1000.0)])

    def test_offset_shifts_spans_with_the_audio_timeline(self):
        m = model_with([make_note(0, 200, 60)], [(150.0, 2000.0)])
        self.assertEqual(mp.pedal_spans_in_range(m, 0.0, 3000.0, 250.0),
                         [(400.0, 2250.0)])

    def test_spans_outside_the_range_are_dropped(self):
        m = model_with([make_note(0, 200, 60)], [(5000.0, 6000.0)])
        self.assertEqual(mp.pedal_spans_in_range(m, 0.0, 3000.0), [])

    def test_no_pedal_data_is_not_an_error(self):
        m = model_with([make_note(0, 200, 60)])
        self.assertEqual(mp.pedal_spans_in_range(m, 0.0, 3000.0), [])


class RealPedalNoteLengthTests(unittest.TestCase):
    def test_real_pedal_leaves_note_lengths_untouched(self):
        m = model_with([make_note(0, 200, 60), make_note(1, 1000, 64)],
                       [(150.0, 2000.0)])
        notes = mp.build_chart_midi_notes(m, note_length_ms=None, real_pedal=True)
        self.assertEqual([(n.start_ms, n.end_ms) for n in notes],
                         [(200.0, 500.0), (1000.0, 1300.0)])

    def test_the_old_extend_mode_still_works_when_asked(self):
        m = model_with([make_note(0, 200, 60)], [(150.0, 2000.0)])
        notes = mp.build_chart_midi_notes(m, note_length_ms=None, real_pedal=False)
        self.assertEqual(notes[0].end_ms, 2000.0)

    def test_extend_mode_truncates_a_repeated_pitch_but_cc64_does_not(self):
        # 這就是模擬法的破綻：踏板踩著、同一個音再彈一次，前一顆被裁短
        notes = [make_note(0, 200, 60), make_note(1, 600, 60)]
        extended = mp.build_chart_midi_notes(
            model_with(notes, [(150.0, 2000.0)]), note_length_ms=None, real_pedal=False)
        self.assertLess(extended[0].end_ms, 2000.0)

        notes2 = [make_note(0, 200, 60), make_note(1, 600, 60)]
        real = mp.build_chart_midi_notes(
            model_with(notes2, [(150.0, 2000.0)]), note_length_ms=None, real_pedal=True)
        self.assertEqual(real[0].end_ms, 500.0)   # 原長，交給 CC64 去延音


def _tail_rms(pcm: bytes, rate: int = 44100, seconds: float = 1.0) -> int:
    frames = int(rate * seconds) * 2 * 2
    samples = array.array('h', pcm[-frames:])
    if not samples:
        return 0
    return int((sum(x * x for x in samples) / len(samples)) ** 0.5)


class RenderedPedalAudioTests(unittest.TestCase):
    """真的把 PCM 算出來聽——CC64 有沒有作用，尾巴的能量說了算。"""

    @classmethod
    def setUpClass(cls):
        cls.synth = mp.MidiPreviewSynth(44100)
        if not cls.synth.is_ready:
            raise unittest.SkipTest('FluidSynth 不可用')

    @classmethod
    def tearDownClass(cls):
        synth = getattr(cls, 'synth', None)
        if synth is not None:
            synth.close()

    def _notes(self):
        m = model_with([make_note(0, 200, 60)])
        return mp.build_chart_midi_notes(m, note_length_ms=None, real_pedal=True)

    def test_pedal_makes_the_sound_ring_past_the_note_off(self):
        notes = self._notes()
        dry = self.synth.render(notes, 0.0, 2500.0)
        wet = self.synth.render(notes, 0.0, 2500.0, pedal_spans=[(150.0, 2500.0)])
        self.assertEqual(_tail_rms(dry), 0, '沒踏板時尾段應該已經沒聲音')
        self.assertGreater(_tail_rms(wet), 0, '踩著踏板時尾段應該還在響')

    def test_pedal_state_does_not_leak_into_the_next_render(self):
        notes = self._notes()
        self.synth.render(notes, 0.0, 2500.0, pedal_spans=[(150.0, 2500.0)])
        after = self.synth.render(notes, 0.0, 2500.0)
        self.assertEqual(_tail_rms(after), 0, '上一次的踏板狀態殘留到下一段了')

    def test_releasing_the_pedal_early_cuts_the_ring(self):
        notes = self._notes()
        short = self.synth.render(notes, 0.0, 2500.0, pedal_spans=[(150.0, 1000.0)])
        long_ = self.synth.render(notes, 0.0, 2500.0, pedal_spans=[(150.0, 2500.0)])
        self.assertLess(_tail_rms(short), _tail_rms(long_))

class PedalEdgeEditingTests(unittest.TestCase):
    """踏板區間畫成六角形，兩端可以直接拉。

    圓角矩形的兩端是一段圓弧，踩下/放開的確切時刻被圓角糊掉了，對不準拍子；
    六角形的尖端就是那個時刻，看得到也抓得到。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from qt_editor.chart_view import ChartView
        self.view = ChartView()
        self.view.resize(1200, 720)
        m = model_with([make_note(0, 0, 60)])
        m.pedal_spans = [[1000.0, 3000.0]]
        self.model = m
        self.view.load_model(m)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        for _ in range(2):
            self.view.zoom(0.5)
        self.view.window_start_unit = self.view.mapper.ms_to_unit(0.0)

    def test_hexagon_has_six_points_with_the_tips_on_the_exact_edges(self):
        poly = self.view._pedal_hex(100.0, 300.0, 18.0)
        self.assertEqual(len(poly), 6)
        ys = sorted(p.y() for p in poly)
        self.assertEqual(ys[0], 100.0)     # 上尖端 = 踩下的那一刻
        self.assertEqual(ys[-1], 300.0)    # 下尖端 = 放開的那一刻

    def test_short_spans_do_not_let_the_tips_cross(self):
        poly = self.view._pedal_hex(100.0, 104.0, 18.0)
        ys = [p.y() for p in poly]
        self.assertGreaterEqual(min(ys), 100.0)
        self.assertLessEqual(max(ys), 104.0)

    def test_edges_are_grabbable_but_the_middle_is_not(self):
        y_start = self.view._ms_to_py(1000.0)
        y_end = self.view._ms_to_py(3000.0)
        self.assertEqual(self.view._pedal_edge_at(y_start + 2), (0, 'start'))
        self.assertEqual(self.view._pedal_edge_at(y_end - 2), (0, 'end'))
        self.assertIsNone(self.view._pedal_edge_at((y_start + y_end) / 2))

    def test_dragging_an_edge_moves_only_that_edge(self):
        self.view._pedal_edge_drag = (0, 'end')
        self.view._drag_pedal_edge(self.view._ms_to_py(3800.0))
        span = self.model.pedal_spans[0]
        self.assertEqual(int(span[0]), 1000, '沒被拉的那一端不該動')
        self.assertAlmostEqual(span[1], 3800.0, delta=2)   # 像素換算會差 1~2ms

        self.view._pedal_edge_drag = (0, 'start')
        self.view._drag_pedal_edge(self.view._ms_to_py(400.0))
        span = self.model.pedal_spans[0]
        self.assertAlmostEqual(span[0], 400.0, delta=2)
        self.assertAlmostEqual(span[1], 3800.0, delta=2)

    def test_the_two_edges_cannot_cross(self):
        self.view._pedal_edge_drag = (0, 'end')
        self.view._drag_pedal_edge(self.view._ms_to_py(-9999.0))
        span = self.model.pedal_spans[0]
        self.assertGreater(span[1], span[0])

if __name__ == '__main__':
    unittest.main()
