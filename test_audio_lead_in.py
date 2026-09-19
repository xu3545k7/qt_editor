"""開頭空白：加／減音訊開頭靜音，可選擇音符與小節線一起移（取代播放偏移）。"""

import os
import shutil
import tempfile
import unittest
import wave
from unittest import mock

from PyQt5.QtWidgets import QApplication, QDialog

from qt_editor import audio_lead_in as A
from qt_editor.wav_process import parse_offset_from_filename
from test_export_end_to_end import chart, window, write_wav

_app = QApplication.instance() or QApplication([])


def write_with_silence(path, silence_ms=300, sound_ms=2000, rate=8000):
    with wave.open(path, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b'\x00\x00' * int(rate * silence_ms / 1000))
        f.writeframes((3000).to_bytes(2, 'little', signed=True) * int(rate * sound_ms / 1000))


class LeadInFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wav = os.path.join(self.tmp, 'song.wav')
        write_with_silence(self.wav)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_leading_silence(self):
        self.assertAlmostEqual(A.leading_silence_ms(self.wav), 300, delta=10)

    def test_pad_then_adjust_from_the_original(self):
        first = A.apply_lead_in(self.wav, 500)
        self.assertEqual(os.path.basename(first.path), 'song_lead+500.wav')
        self.assertAlmostEqual(A.wav_length_ms(first.path), 2800, delta=2)
        self.assertAlmostEqual(A.leading_silence_ms(first.path), 800, delta=10)
        second = A.apply_lead_in(first.path, -200)
        self.assertEqual(os.path.basename(second.path), 'song_lead+300.wav')
        self.assertEqual((second.total_ms, second.applied_ms), (300, -200))
        self.assertAlmostEqual(A.wav_length_ms(second.path), 2600, delta=2)
        back = A.apply_lead_in(second.path, -300)
        self.assertEqual(back.path, self.wav)            # 回到 0 就用原檔
        self.assertTrue(os.path.isfile(self.wav))        # 原檔從來沒被改

    def test_trim_reports_cutting_into_sound(self):
        quiet = A.apply_lead_in(self.wav, -200)
        self.assertEqual(quiet.cut_sound_ms, 0)
        self.assertAlmostEqual(A.wav_length_ms(quiet.path), 2100, delta=2)
        loud = A.apply_lead_in(self.wav, -500)
        self.assertAlmostEqual(loud.cut_sound_ms, 200, delta=10)

    def test_cannot_trim_more_than_the_audio(self):
        result = A.apply_lead_in(self.wav, -99999)
        self.assertEqual(result.applied_ms, -2300)

    def test_name_is_not_read_as_an_export_offset(self):
        # 匯出會把檔名裡的 +500ms 當成要再套用一次的偏移
        path = A.apply_lead_in(self.wav, 500).path
        self.assertIsNone(parse_offset_from_filename(path))
        self.assertEqual(A.split_lead_name(path), (self.wav, 500))


class StubDialog:
    def __init__(self, delta, move, latency=0):
        self.delta, self.move, self.latency = delta, move, latency

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self

    def exec_(self):
        return QDialog.Accepted

    def delta_ms(self):
        return self.delta

    def moves_notes(self):
        return self.move

    def latency_ms(self):
        return self.latency


class LeadInWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wav = os.path.join(self.tmp, 'song.wav')
        write_wav(self.wav, duration_ms=4000)
        self.win = window()
        self.win.view.model = chart()
        self.win.view.load_model(self.win.view.model)
        self.assertTrue(self.win.audio.load_wav(self.wav))
        self._save = mock.patch('qt_editor.main_window.settings.set')
        self._save.start()

    def tearDown(self):
        self._save.stop()
        self.win.audio.audio_path = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_dialog(self, delta, move):
        stub = StubDialog(delta, move)
        with mock.patch('qt_editor.audio_lead_in_dialog.AudioLeadInDialog', stub), \
                mock.patch('qt_editor.main_window.QMessageBox.warning') as warn:
            self.win._show_lead_in_dialog()
        self.assertFalse(warn.called, warn.call_args)
        return stub

    def starts(self):
        return [n.start for n in self.win.view.model.notes_tree]

    def test_pad_moves_notes_with_the_audio(self):
        before = self.starts()
        self.run_dialog(500, True)
        self.assertEqual(self.starts(), [s + 500 for s in before])
        self.assertTrue(self.win.audio.audio_path.endswith('song_lead+500.wav'))
        self.assertAlmostEqual(A.wav_length_ms(self.win.audio.audio_path), 4500, delta=2)

    def test_audio_only_leaves_the_chart_alone(self):
        before = self.starts()
        self.run_dialog(500, False)
        self.assertEqual(self.starts(), before)
        self.assertTrue(self.win.audio.audio_path.endswith('song_lead+500.wav'))

    def test_trim_is_limited_by_the_earliest_note(self):
        self.run_dialog(300, True)                       # 最早的音符在 300
        self.run_dialog(-800, True)                      # 只能往前 300
        self.assertEqual(self.starts()[0], 0)
        self.assertEqual(self.win.audio.audio_path, self.wav)   # 音訊也只剪 300 → 回到原檔

    def test_undo_restores_the_notes(self):
        before = self.starts()
        self.run_dialog(500, True)
        self.win.view.model.undo()
        self.assertEqual([n.start for n in self.win.view.model.notes_tree], before)

    def test_export_uses_the_new_audio_when_the_song_folder_has_an_old_one(self):
        from qt_editor.main_window import MainWindow
        old = os.path.join(self.tmp, 'old.wav')
        write_wav(old, duration_ms=1000)
        self.assertTrue(MainWindow._same_audio_file(self.wav, self.wav))
        self.assertFalse(MainWindow._same_audio_file(self.wav, old))


class LeadInDialogTests(unittest.TestCase):
    def test_warnings_and_beat_conversion(self):
        from qt_editor.audio_lead_in_dialog import AudioLeadInDialog
        dlg = AudioLeadInDialog(None, bpm=120.0, audio_path='song.wav', current_total_ms=500,
                                leading_silence_ms=300, earliest_note_ms=200, latency_ms=40)
        dlg.beat_spin.setValue(1.0)
        self.assertEqual(dlg.delta_ms(), 500)
        self.assertTrue(dlg.moves_notes())
        self.assertTrue(dlg.warning.isHidden())
        dlg.ms_spin.setValue(-400)
        self.assertIn('有聲音', dlg.warning.text())
        self.assertIn('200', dlg.warning.text())
        dlg.move_notes.setChecked(False)
        self.assertNotIn('音符', dlg.warning.text())
        self.assertEqual(dlg.latency_ms(), 40)
        dlg.deleteLater()


if __name__ == '__main__':
    unittest.main()
