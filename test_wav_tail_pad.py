"""匯出時把音訊的尾巴補到蓋過譜面。

「樂曲總資訊」的整體位移只搬譜面資料，音檔長度不會跟著長——前面塞進去的空白
等於把最後那幾秒擠出音檔之外。遊戲讀譜面的 `music_finish_time_msec` 收歌，
所以歌照跑，只是後面沒有聲音。
"""

import os
import tempfile
import unittest
import wave

from qt_editor.main_window import MainWindow


def write_wav(path, duration_ms, rate=8000, channels=1, width=2, level=1000):
    frames = int(round(duration_ms / 1000.0 * rate))
    one = int(level).to_bytes(width, 'little', signed=True) * channels
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(one * frames)


def read_wav(path):
    with wave.open(path, 'rb') as wf:
        return wf.getparams(), wf.readframes(wf.getnframes())


class PadWavTailTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'a.wav')

    def test_a_short_track_is_extended_to_the_chart_end(self):
        write_wav(self.path, 1000)
        added = MainWindow._pad_wav_tail(self.path, 3000)
        self.assertAlmostEqual(added, 2000, delta=1)
        params, _ = read_wav(self.path)
        self.assertAlmostEqual(params.nframes / params.framerate, 3.0, places=3)

    def test_a_long_track_is_left_alone(self):
        write_wav(self.path, 5000)
        before = read_wav(self.path)
        self.assertEqual(MainWindow._pad_wav_tail(self.path, 3000), 0.0)
        self.assertEqual(read_wav(self.path), before,
                         '比譜面長的音檔不該被動到——尾奏不能裁掉')

    def test_the_original_audio_survives_verbatim(self):
        write_wav(self.path, 1000, level=1234)
        _, before = read_wav(self.path)
        MainWindow._pad_wav_tail(self.path, 4000)
        _, after = read_wav(self.path)
        self.assertEqual(after[:len(before)], before, '原本的聲音被改掉了')

    def test_what_is_added_is_silence(self):
        write_wav(self.path, 1000, level=1234)
        _, before = read_wav(self.path)
        MainWindow._pad_wav_tail(self.path, 4000)
        _, after = read_wav(self.path)
        self.assertEqual(set(after[len(before):]), {0}, '補上去的不是靜音')

    def test_stereo_and_odd_rates_keep_their_format(self):
        write_wav(self.path, 500, rate=44100, channels=2)
        MainWindow._pad_wav_tail(self.path, 2000)
        params, _ = read_wav(self.path)
        self.assertEqual((params.nchannels, params.framerate, params.sampwidth),
                         (2, 44100, 2))
        self.assertAlmostEqual(params.nframes / params.framerate, 2.0, places=3)

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(
            MainWindow._pad_wav_tail(os.path.join(self.dir, 'nope.wav'), 1000), 0.0)

    def test_an_exact_match_adds_nothing(self):
        write_wav(self.path, 2000)
        self.assertEqual(MainWindow._pad_wav_tail(self.path, 2000), 0.0)


class ShiftThenExportTests(unittest.TestCase):
    """位移多少，音訊就要長多少——這是回歸這個 bug 的那條測試。"""

    def test_a_forward_shift_makes_the_audio_too_short(self):
        from qt_editor.models import GNote, NoteModel
        d = tempfile.mkdtemp()
        wav = os.path.join(d, 'song.wav')
        write_wav(wav, 10000)

        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        n = GNote(None, 0)
        n.start, n.end, n.gate = 9000, 9500, 500
        n.pitch, n.hand, n.velocity = 60, 0, 90
        n.min_key, n.max_key, n.note_type = 4, 6, 0
        m.notes_tree = [n]
        m.music_end_ms = 10000.0
        m.rebuild_display_cache()
        m.shift_all_time(2000)

        end = max(float(m.music_end_ms),
                  max(float(n.end) for n in m.notes_tree))
        self.assertAlmostEqual(end, 12000, delta=1, msg='位移應該把曲終一起往後搬')

        with wave.open(wav) as f:
            self.assertLess(f.getnframes() / f.getframerate() * 1000, end,
                            '前提：位移後音檔比譜面短')
        MainWindow._pad_wav_tail(wav, end)
        with wave.open(wav) as f:
            self.assertGreaterEqual(f.getnframes() / f.getframerate() * 1000 + 1, end)


if __name__ == '__main__':
    unittest.main()
