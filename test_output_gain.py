"""內建音源的輸出增益與軟膝限幅。

算出來的鋼琴普遍偏小聲——實測 400ms 區塊 RMS 中位數 -25.9 dBFS，而母帶處理過
的歌曲在 -12~-14，疊在一起幾乎聽不到。但峰值本來就在 -2.0 dBFS，直接乘會削頂，
所以是「增益 + 只壓接近天花板的部分」。

這裡要釘住的三件事：真的變大聲、一個取樣都不能削頂、動態範圍不能被壓掉
（力度和強弱曲線的表情全靠它）。
"""

import array
import math
import unittest

from qt_editor.midi_preview import (
    OUTPUT_GAIN, OUTPUT_KNEE, apply_output_gain,
)


def pcm(values):
    return array.array('h', values).tobytes()


def samples(data):
    a = array.array('h')
    a.frombytes(data)
    return list(a)


def rms_db(values):
    if not values:
        return -math.inf
    r = math.sqrt(sum(float(v) * v for v in values) / len(values))
    return 20 * math.log10(r / 32767) if r > 0 else -math.inf


class BasicsTests(unittest.TestCase):
    def test_silence_stays_silence(self):
        self.assertEqual(samples(apply_output_gain(pcm([0] * 64))), [0] * 64)

    def test_empty_input(self):
        self.assertEqual(apply_output_gain(b''), b'')

    def test_gain_of_one_is_a_no_op(self):
        data = pcm([100, -200, 300])
        self.assertEqual(apply_output_gain(data, gain=1.0), data)

    def test_an_odd_trailing_byte_does_not_crash(self):
        out = apply_output_gain(pcm([1000, -1000]) + b'\x01')
        self.assertEqual(len(out), 4, '半個取樣要丟掉，不能亂補')

    def test_the_length_is_preserved(self):
        data = pcm(list(range(-500, 500)))
        self.assertEqual(len(apply_output_gain(data)), len(data))


class NoClippingTests(unittest.TestCase):
    def test_full_scale_input_never_wraps(self):
        data = pcm([32767, -32768, 32000, -32000, 20000, -20000])
        for v in samples(apply_output_gain(data)):
            self.assertLessEqual(v, 32767)
            self.assertGreaterEqual(v, -32768)

    def test_a_full_scale_ramp_stays_in_range_and_monotonic(self):
        raw = list(range(-32768, 32768, 7))
        out = samples(apply_output_gain(pcm(raw)))
        self.assertTrue(all(-32768 <= v <= 32767 for v in out))
        # 單調遞增：只要有一處反轉就代表溢位或查表算錯，聽起來是爆音
        self.assertEqual(out, sorted(out), '轉移函數必須單調')

    def test_the_sign_is_preserved(self):
        for v in (-30000, -1000, -1, 1, 1000, 30000):
            got = samples(apply_output_gain(pcm([v])))[0]
            self.assertEqual(got == 0 or (got > 0) == (v > 0), True, v)

    def test_it_is_symmetric(self):
        for v in (500, 5000, 25000, 32000):
            up = samples(apply_output_gain(pcm([v])))[0]
            down = samples(apply_output_gain(pcm([-v])))[0]
            self.assertEqual(up, -down, v)


class LevelTests(unittest.TestCase):
    def test_quiet_signals_get_the_full_gain(self):
        # 膝點以下是線性的，小訊號就該原封不動乘上去
        for v in (100, 500, 2000):
            got = samples(apply_output_gain(pcm([v])))[0]
            self.assertAlmostEqual(got / float(v), OUTPUT_GAIN, delta=0.02, msg=v)

    def test_a_quiet_passage_gets_louder_by_the_full_gain(self):
        raw = [int(3000 * math.sin(i * 0.05)) for i in range(4000)]
        before = rms_db(raw)
        after = rms_db(samples(apply_output_gain(pcm(raw))))
        self.assertAlmostEqual(after - before, 20 * math.log10(OUTPUT_GAIN),
                               delta=0.3)

    def test_loud_peaks_are_held_below_the_ceiling(self):
        raw = [int(30000 * math.sin(i * 0.05)) for i in range(4000)]
        out = samples(apply_output_gain(pcm(raw)))
        self.assertLess(max(abs(v) for v in out), 32767)

    def test_the_knee_only_touches_the_top(self):
        # 膝點換算回輸入端：低於這個值的完全線性
        linear_max = int(32768 * OUTPUT_KNEE / OUTPUT_GAIN) - 2
        got = samples(apply_output_gain(pcm([linear_max])))[0]
        self.assertAlmostEqual(got / float(linear_max), OUTPUT_GAIN, delta=0.02)

    def test_the_default_gain_is_a_real_boost(self):
        self.assertGreater(OUTPUT_GAIN, 1.5)
        self.assertLess(OUTPUT_GAIN, 4.0, '再大就會把大部分訊號推進軟膝')


class DynamicsTests(unittest.TestCase):
    """力度與強弱曲線的表情不能被增益壓掉。"""

    def block(self, amp, n=2000):
        return [int(amp * math.sin(i * 0.07)) for i in range(n)]

    def test_the_gap_between_soft_and_loud_survives(self):
        soft, loud = self.block(1500), self.block(12000)
        before = rms_db(loud) - rms_db(soft)
        after = (rms_db(samples(apply_output_gain(pcm(loud))))
                 - rms_db(samples(apply_output_gain(pcm(soft)))))
        self.assertAlmostEqual(after, before, delta=1.0,
                               msg='強弱差距被壓掉了')

    def test_louder_input_still_means_louder_output(self):
        levels = [rms_db(samples(apply_output_gain(pcm(self.block(a)))))
                  for a in (500, 2000, 8000, 20000, 31000)]
        self.assertEqual(levels, sorted(levels))


class TableTests(unittest.TestCase):
    def test_a_different_setting_rebuilds_the_table(self):
        a = samples(apply_output_gain(pcm([4000]), gain=2.0, knee=0.6))[0]
        b = samples(apply_output_gain(pcm([4000]), gain=3.0, knee=0.6))[0]
        self.assertNotEqual(a, b)
        # 換回來要拿到原本的值，不能被上一次的表汙染
        again = samples(apply_output_gain(pcm([4000]), gain=2.0, knee=0.6))[0]
        self.assertEqual(again, a)

    def test_repeated_calls_are_stable(self):
        data = pcm([1234, -5678, 30000])
        first = apply_output_gain(data)
        for _ in range(3):
            self.assertEqual(apply_output_gain(data), first)


class RenderIntegrationTests(unittest.TestCase):
    """整條路（渲染 → 增益）真的有變大聲。"""

    def test_the_rendered_piano_is_louder_than_the_raw_synth(self):
        from qt_editor.midi_preview import MidiPreviewNote, MidiPreviewSynth

        synth = MidiPreviewSynth(44100)
        if not synth.is_ready:
            self.skipTest('FluidSynth 不可用')
        try:
            notes = [MidiPreviewNote(start_ms=i * 200.0, end_ms=i * 200.0 + 180.0,
                                     pitch=60 + i, velocity=70, channel=0)
                     for i in range(8)]
            out = synth.render(notes, 0.0, 2000.0)
        finally:
            synth.close()
        self.assertTrue(out)
        got = samples(out)
        self.assertTrue(any(got), '算出來是靜音')
        self.assertLessEqual(max(abs(v) for v in got), 32767)


if __name__ == '__main__':
    unittest.main()
