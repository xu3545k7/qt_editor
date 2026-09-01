"""新放的音符要有力度，而且要和旁邊聽起來一致。

以前只有 MIDI 模式的音符會拿到 velocity（而且是整軌平均），非 MIDI 模式一律
None。有了力度顯示與強弱曲線之後，新音沒有力度就會在畫面上開天窗，聽起來也和
旁邊格格不入。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.music_theory import Key

_app = QApplication.instance() or QApplication([])

C_MAJOR = Key(0, 'major')


def make_note(idx, start, velocity, hand=0, pitch=60):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    return n


def model_with(notes):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    return m


class VelocityNearTests(unittest.TestCase):
    def test_copies_the_nearest_same_hand_onset(self):
        m = model_with([make_note(0, 1000, 40), make_note(1, 5000, 110)])
        self.assertEqual(m.velocity_near(1100, 0), 40)
        self.assertEqual(m.velocity_near(4900, 0), 110)

    def test_simultaneous_notes_average(self):
        m = model_with([make_note(0, 1000, 60), make_note(1, 1000, 80)])
        self.assertEqual(m.velocity_near(1000, 0), 70)

    def test_the_same_hand_wins_over_a_closer_other_hand(self):
        m = model_with([make_note(0, 1000, 30, hand=1),
                        make_note(1, 1400, 100, hand=0)])
        self.assertEqual(m.velocity_near(1050, 0), 100, '同手優先')
        self.assertEqual(m.velocity_near(1050, 1), 30)

    def test_falls_back_to_the_other_hand_inside_the_window(self):
        m = model_with([make_note(0, 1000, 33, hand=1)])
        self.assertEqual(m.velocity_near(1000, 0), 33)

    def test_falls_back_to_the_hand_average_when_nothing_is_near(self):
        m = model_with([make_note(0, 0, 40), make_note(1, 500, 80)])
        self.assertEqual(m.velocity_near(50_000, 0), 60)

    def test_falls_back_to_the_default_when_there_is_no_velocity_at_all(self):
        m = model_with([make_note(0, 0, None), make_note(1, 500, None)])
        self.assertEqual(m.velocity_near(1000, 0), NoteModel.DEFAULT_NEW_NOTE_VELOCITY)

    def test_an_empty_chart_uses_the_default(self):
        m = model_with([])
        self.assertEqual(m.velocity_near(0, 0), NoteModel.DEFAULT_NEW_NOTE_VELOCITY)

    def test_result_is_always_a_valid_midi_velocity(self):
        m = model_with([make_note(0, 0, 1), make_note(1, 500, 127)])
        for ms in (0, 250, 500, 9999):
            self.assertGreaterEqual(m.velocity_near(ms, 0), 1)
            self.assertLessEqual(m.velocity_near(ms, 0), 127)


class PlacedNoteVelocityTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = model_with([])
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')

    def test_pattern_notes_get_a_velocity_even_without_midi(self):
        self.assertFalse(self.model.is_midi_mode())
        self.view.insert_pattern('scale', C_MAJOR, 60, 4, 1, 0.0, 0.5, 0)
        for n in self.model.notes_tree:
            self.assertIsNotNone(n.velocity, '新音一定要有力度')

    def test_pattern_notes_copy_the_local_velocity(self):
        self.model.notes_tree = [make_note(0, 10_000, 45)]
        self.model.rebuild_display_cache()
        self.view.insert_pattern('scale', C_MAJOR, 60, 3, 1, 10_000.0, 0.5, 0)
        placed = [n for n in self.model.notes_tree if n.pitch in (60, 62, 64)]
        self.assertTrue(placed)
        for n in placed:
            self.assertEqual(n.velocity, 45)

    def test_each_pattern_note_looks_up_its_own_neighbourhood(self):
        # 一段輕、一段重；音階跨過去時力度要跟著走，不是整串同一個值
        anchors = [make_note(0, 0, 30), make_note(1, 12_000, 120)]
        self.model.notes_tree = list(anchors)
        self.model.rebuild_display_cache()
        existing = {id(n) for n in anchors}

        self.view.insert_pattern('scale', C_MAJOR, 60, 13, 1, 0.0, 2.0, 0)

        placed = sorted([n for n in self.model.notes_tree if id(n) not in existing],
                        key=lambda n: n.start)
        self.assertEqual(placed[0].velocity, 30, '開頭抄輕的那顆')
        self.assertEqual(placed[-1].velocity, 120, '結尾抄重的那顆')

    def test_a_chart_with_no_velocity_gets_the_default(self):
        self.view.insert_pattern('scale', C_MAJOR, 60, 3, 1, 0.0, 0.5, 0)
        for n in self.model.notes_tree:
            self.assertEqual(n.velocity, NoteModel.DEFAULT_NEW_NOTE_VELOCITY)


if __name__ == '__main__':
    unittest.main()
