"""匯出 MIDI 檔，以及匯出完整曲目時順便算一份鋼琴音軌。

鋼琴音軌走的是播放那條路（力度、trill/staccato 表情、CC64 延音踏板都在），所以
匯出的東西和編輯器裡聽到的一致。
"""

import os
import tempfile
import unittest
import wave

from PyQt5.QtWidgets import QApplication

from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, pitch, velocity=90, hand=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 300
    n.gate = 300
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    return n


class MidiFileExportTests(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow()
        self.model = NoteModel.create_new('t', 120.0, 20.0, 4)
        self.model.notes_tree = [
            make_note(0, 0, 60, 40), make_note(1, 500, 64, 90),
            make_note(2, 1000, 67, 120, hand=1),
        ]
        self.model.rebuild_display_cache()
        self.win._load_model_all(self.model)
        self.path = os.path.join(tempfile.mkdtemp(), 'out.mid')

    def test_saving_produces_a_readable_midi(self):
        self.model.save_midi(self.path)
        self.assertTrue(os.path.isfile(self.path))
        back = NoteModel()
        back.load_midi(self.path)
        self.assertEqual(len(back.notes_tree), 3)

    def test_pitches_and_velocities_survive_the_round_trip(self):
        self.model.save_midi(self.path)
        back = NoteModel()
        back.load_midi(self.path)
        got = sorted((n.pitch, n.velocity) for n in back.notes_tree)
        self.assertEqual(got, [(60, 40), (64, 90), (67, 120)])

    def test_hands_survive_as_separate_tracks(self):
        self.model.save_midi(self.path)
        back = NoteModel()
        back.load_midi(self.path)
        self.assertEqual({n.hand for n in back.notes_tree}, {0, 1})


class PianoRenderTests(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow()
        self.model = NoteModel.create_new('t', 120.0, 6.0, 4)
        self.model.notes_tree = [make_note(i, i * 500, 60 + i * 2) for i in range(4)]
        self.model.music_end_ms = 4000
        self.model.rebuild_display_cache()
        self.win._load_model_all(self.model)
        self.path = os.path.join(tempfile.mkdtemp(), 'piano.wav')

    def test_render_writes_a_stereo_wav(self):
        if not self.win.render_piano_wav(self.path):
            self.skipTest('FluidSynth 不可用')
        self.assertTrue(os.path.isfile(self.path))
        with wave.open(self.path, 'rb') as f:
            self.assertEqual(f.getnchannels(), 2)
            self.assertEqual(f.getsampwidth(), 2)
            self.assertGreater(f.getnframes(), 0)

    def test_the_render_covers_the_whole_chart(self):
        if not self.win.render_piano_wav(self.path):
            self.skipTest('FluidSynth 不可用')
        with wave.open(self.path, 'rb') as f:
            seconds = f.getnframes() / float(f.getframerate())
        last_end = max(n.end for n in self.model.notes_tree) / 1000.0
        self.assertGreater(seconds, last_end, '要蓋過最後一顆音，還要留尾巴')

    def test_an_empty_chart_renders_nothing(self):
        self.model.notes_tree = []
        self.model.rebuild_display_cache()
        self.assertFalse(self.win.render_piano_wav(self.path))

    def test_the_audio_is_not_silence(self):
        if not self.win.render_piano_wav(self.path):
            self.skipTest('FluidSynth 不可用')
        with open(self.path, 'rb') as f:
            data = f.read()
        self.assertTrue(any(b for b in data[44:]), '算出來的應該是聲音不是靜音')


if __name__ == '__main__':
    unittest.main()
