"""響度量測：拿 BS.1770 的測試訊號驗證，並比對 numpy 與純 Python 兩條路徑。"""

import math
import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_editor import loudness  # noqa: E402


def sine(rate: int, seconds: float, freq: float, dbfs: float, channels: int = 2):
    amp = 10.0 ** (dbfs / 20.0)
    total = int(rate * seconds)
    data = []
    for n in range(total):
        v = amp * math.sin(2.0 * math.pi * freq * n / rate)
        data.extend([v] * channels)
    return data


def write_wav(path: str, samples, rate: int, channels: int):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b''.join(
            struct.pack('<h', max(-32768, min(32767, int(round(v * 32767.0)))))
            for v in samples))


class LoudnessTests(unittest.TestCase):
    # EBU Tech 3341 的驗收訊號：1 kHz 正弦、雙聲道等幅，量出來的 LUFS 等於 dBFS。
    # K 加權在 1 kHz 的增益（約 +0.69 dB）剛好抵銷定義裡的 −0.691，所以不是 −20.7。

    def test_stereo_1khz_minus20dbfs(self):
        rate = 48000
        data = sine(rate, 3.0, 1000.0, -20.0)
        channels = [data[0::2], data[1::2]]
        m = loudness.measure(channels, rate)
        self.assertAlmostEqual(m.lufs_i, -20.0, delta=0.15)

    def test_stereo_1khz_minus26dbfs(self):
        rate = 48000
        data = sine(rate, 3.0, 1000.0, -26.0)
        channels = [data[0::2], data[1::2]]
        m = loudness.measure(channels, rate)
        self.assertAlmostEqual(m.lufs_i, -26.0, delta=0.15)

    def test_mono_is_three_db_quieter_than_the_same_level_stereo(self):
        rate = 48000
        data = sine(rate, 3.0, 1000.0, -20.0, channels=1)
        m = loudness.measure([data], rate)
        self.assertAlmostEqual(m.lufs_i, -23.0, delta=0.15)

    def test_44100_matches_48000(self):
        # 係數依取樣率重算，所以同一個訊號在兩個取樣率下要量出同一個值。
        a = loudness.measure([sine(48000, 3.0, 1000.0, -20.0, 1)], 48000)
        b = loudness.measure([sine(44100, 3.0, 1000.0, -20.0, 1)], 44100)
        self.assertAlmostEqual(a.lufs_i, b.lufs_i, delta=0.1)

    def test_numpy_and_pure_python_agree(self):
        rate = 44100
        data = sine(rate, 2.0, 440.0, -18.0, 1)
        with_numpy = loudness.measure([data], rate)
        saved = loudness._np
        loudness._np = None
        loudness._fir_cache.clear()
        try:
            without = loudness.measure([data], rate)
        finally:
            loudness._np = saved
            loudness._fir_cache.clear()
        self.assertAlmostEqual(with_numpy.lufs_i, without.lufs_i, delta=0.05)
        self.assertAlmostEqual(with_numpy.true_peak_dbtp, without.true_peak_dbtp, delta=0.05)

    def test_true_peak_sees_between_samples(self):
        # 取樣點都在 ±0.5，但波峰落在取樣點之間：取樣峰值會低估。
        rate = 48000
        data = sine(rate, 1.0, 12000.0, -6.0, 1)
        m = loudness.measure([data], rate)
        self.assertGreater(m.true_peak_dbtp, -6.5)

    def test_silence_is_rejected(self):
        with self.assertRaises(loudness.LoudnessError):
            loudness.measure([[0.0] * 48000], 48000)

    def test_reads_a_wav_file(self):
        rate = 48000
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'a.wav')
            write_wav(path, sine(rate, 2.0, 1000.0, -20.0), rate, 2)
            m = loudness.measure_file(path)
        self.assertAlmostEqual(m.lufs_i, -20.0, delta=0.2)
        self.assertEqual(m.channels, 2)
        self.assertEqual(m.rate, rate)


class GainPlanTests(unittest.TestCase):
    def measurement(self, lufs, peak, lra=8.0):
        return loudness.Measurement(lufs, peak, lra, 44100, 2, 44100 * 60)

    def test_already_on_target_is_left_alone(self):
        plan = loudness.plan_gain(self.measurement(-14.3, -6.0))
        self.assertEqual(plan.status, 'ok')
        self.assertEqual(plan.gain_db, 0.0)

    def test_quiet_song_gets_plain_gain(self):
        plan = loudness.plan_gain(self.measurement(-20.0, -10.0))
        self.assertEqual(plan.status, 'gain')
        self.assertAlmostEqual(plan.gain_db, 6.0, places=3)
        self.assertAlmostEqual(plan.resulting_lufs, -14.0, places=3)

    def test_loud_song_is_turned_down(self):
        plan = loudness.plan_gain(self.measurement(-8.0, 0.4))
        self.assertEqual(plan.status, 'gain')
        self.assertAlmostEqual(plan.gain_db, -6.0, places=3)
        self.assertLessEqual(plan.resulting_peak_dbtp, -1.5 + 1e-6)

    def test_gain_is_capped_at_the_peak_limit(self):
        # −16 LUFS 但峰值已經到 −2：只能再推 0.5 dB，離目標還差 1.5 LU。
        plan = loudness.plan_gain(self.measurement(-16.0, -2.0))
        self.assertEqual(plan.status, 'limited')
        self.assertAlmostEqual(plan.gain_db, 0.5, places=3)
        self.assertAlmostEqual(plan.resulting_peak_dbtp, -1.5, places=3)

    def test_within_tolerance_after_safe_gain_counts_as_done(self):
        plan = loudness.plan_gain(self.measurement(-15.5, -2.0))
        self.assertEqual(plan.status, 'gain')
        self.assertAlmostEqual(plan.resulting_peak_dbtp, -1.5, places=3)

    def test_way_too_quiet_goes_to_review(self):
        plan = loudness.plan_gain(self.measurement(-25.0, -5.0))
        self.assertEqual(plan.status, 'needs_review')
        self.assertAlmostEqual(plan.gain_db, 3.5, places=3)
        self.assertGreater(plan.peak_reduction_db, 3.0)

    def test_gain_is_applied_to_pcm(self):
        pcm = struct.pack('<4h', 1000, -1000, 2000, -2000)
        louder = loudness.apply_gain_pcm16(pcm, 6.02)
        values = struct.unpack('<4h', louder)
        self.assertAlmostEqual(values[0] / 1000.0, 2.0, places=1)
        self.assertAlmostEqual(values[2] / 2000.0, 2.0, places=1)


if __name__ == '__main__':
    unittest.main()
