"""「只從 MIDI 還原踏板」：踏板整份換成 MIDI 的 CC64，其他什麼都不動。"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import mido
from PyQt5.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from qt_editor import main_window as mw  # noqa: E402
from qt_editor.models import NoteModel  # noqa: E402

# 120 BPM、480 tick/拍：一拍 500ms。
NOTES = [(0, 60), (480, 64), (960, 67), (1440, 72)]


def write_midi(path, velocity, pedal=()):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for tick, pitch in NOTES:
        events.append((tick, 1, mido.Message('note_on', note=pitch, velocity=velocity)))
        events.append((tick + 400, 0, mido.Message('note_off', note=pitch, velocity=0)))
    for down, up in pedal:
        events.append((down, 2, mido.Message('control_change', control=64, value=127)))
        events.append((up, 0, mido.Message('control_change', control=64, value=0)))
    now = 0
    for tick, _order, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - now))
        now = tick
    mid.save(path)


class RestorePedalOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        chart_mid = os.path.join(self.tmp, 'chart.mid')
        write_midi(chart_mid, velocity=40)
        self.model = NoteModel()
        self.model.load_midi(chart_mid, auto_arrange=False)
        self.model.pedal_spans = [[0.0, 100.0]]
        self.model.json_meta['pedal_origin'] = 'auto'
        self.window = SimpleNamespace(
            view=SimpleNamespace(model=self.model, update=lambda: None,
                                 note_edited=mock.Mock()),
            _refresh_title=lambda: None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_dialog(self, path, answer=QMessageBox.Yes):
        with mock.patch.object(mw.QFileDialog, 'getOpenFileName',
                               return_value=(path, '')), \
             mock.patch.object(mw.QMessageBox, 'question',
                               return_value=answer) as question, \
             mock.patch.object(mw.QMessageBox, 'information') as info:
            mw.MainWindow.restore_pedal_from_midi_dialog(self.window)
        return question, info

    def velocities(self):
        return sorted(n.velocity for n in self.model.notes_tree)

    def test_replaces_pedal_and_keeps_velocity(self):
        src = os.path.join(self.tmp, 'src.mid')
        write_midi(src, velocity=110, pedal=[(0, 900), (960, 1900)])
        before = self.velocities()
        self.run_dialog(src)
        self.assertEqual(self.velocities(), before)
        self.assertEqual(len(self.model.pedal_spans), 2)
        self.assertAlmostEqual(self.model.pedal_spans[1][0], 1000.0, delta=5)
        self.assertNotIn('pedal_origin', self.model.json_meta)
        self.assertTrue(self.model.dirty)

    def test_midi_without_pedal_changes_nothing(self):
        src = os.path.join(self.tmp, 'src.mid')
        write_midi(src, velocity=110)
        question, _info = self.run_dialog(src)
        question.assert_not_called()
        self.assertEqual(self.model.pedal_spans, [[0.0, 100.0]])
        self.assertEqual(self.model.json_meta.get('pedal_origin'), 'auto')

    def test_declining_changes_nothing(self):
        src = os.path.join(self.tmp, 'src.mid')
        write_midi(src, velocity=110, pedal=[(0, 900)])
        self.run_dialog(src, answer=QMessageBox.No)
        self.assertEqual(self.model.pedal_spans, [[0.0, 100.0]])


if __name__ == '__main__':
    unittest.main()
