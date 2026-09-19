"""匯出時的播放偏移必須真的套用到音訊上。

**踩過的坑**：這條路徑原本寫的是

    try:
        from rip import process_audio
        processed = process_audio(src_wav, rip_ms)
    except ImportError:
        processed = src_wav

而 `rip.py` **根本不存在**（不在原始碼、也不在打包 spec 裡），所以 ImportError
每次都成立，然後把**未處理的原檔**複製過去。輸出的檔名是
`歌名_-1043ms.wav`，內容卻原封不動 —— 要進遊戲才會發現對不上。

正負號：`_playback_offset_ms` 正 = 提前、負 = 延後；轉成 `rip_ms = -offset`
之後就和 `process_wav` 一致（正 = 前面補靜音 = 延後）。
"""

import os
import tempfile
import unittest
import wave

from qt_editor.wav_process import process_wav


def write_wav(path, duration_ms, rate=8000, level=1000):
    frames = int(round(duration_ms / 1000.0 * rate))
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(int(level).to_bytes(2, 'little', signed=True) * frames)


def read(path):
    with wave.open(path, 'rb') as wf:
        return wf.getframerate(), wf.readframes(wf.getnframes())


def lead_silence_ms(path):
    rate, data = read(path)
    for i in range(0, len(data), 2):
        if int.from_bytes(data[i:i + 2], 'little', signed=True) != 0:
            return (i // 2) / rate * 1000.0
    return len(data) // 2 / rate * 1000.0


class OffsetIsApplied(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, 'song.wav')
        self.out = os.path.join(self.dir, 'out.wav')
        write_wav(self.src, 3000)

    def test_a_delay_pads_silence_at_the_front(self):
        # 使用者的情境：偏移 -1043ms（延後）→ rip_ms = +1043
        process_wav(self.src, self.out, offset_ms=1043)
        self.assertAlmostEqual(lead_silence_ms(self.out), 1043, delta=1)

    def test_an_advance_trims_the_front(self):
        process_wav(self.src, self.out, offset_ms=-500)
        rate, data = read(self.out)
        self.assertAlmostEqual(len(data) / 2 / rate * 1000.0, 2500, delta=2)
        self.assertEqual(lead_silence_ms(self.out), 0.0)

    def test_the_output_is_not_the_input(self):
        """核心回歸：原本的程式碼會靜靜地輸出未處理的原檔。"""
        process_wav(self.src, self.out, offset_ms=1043)
        self.assertNotEqual(read(self.src)[1], read(self.out)[1],
                            '輸出和輸入一模一樣 = 偏移根本沒套用')

    def test_the_sound_itself_survives(self):
        process_wav(self.src, self.out, offset_ms=800)
        src_rate, src_data = read(self.src)
        _, out_data = read(self.out)
        pad = int(0.8 * src_rate) * 2
        self.assertEqual(out_data[pad:], src_data, '原本的聲音被改到了')

    def test_zero_offset_is_a_faithful_copy(self):
        process_wav(self.src, self.out, offset_ms=0)
        self.assertEqual(read(self.src)[1], read(self.out)[1])

    def test_trim_end_is_honoured(self):
        process_wav(self.src, self.out, offset_ms=0, trim_end_ms=1000)
        rate, data = read(self.out)
        self.assertAlmostEqual(len(data) / 2 / rate * 1000.0, 2000, delta=2)


class TheExportUsesIt(unittest.TestCase):
    def test_no_reference_to_the_missing_rip_module(self):
        import qt_editor.main_window as mw
        # 只看真正的 import 陳述，不要抓到註解裡提到它的那一行
        bad = [n for n, line in enumerate(
                   open(mw.__file__, encoding='utf-8'), 1)
               if line.lstrip().startswith('from rip import')]
        self.assertEqual(bad, [],
                         'rip.py 不存在，import 它等於永遠走到「不處理」那條'
                         '（第 %s 行）' % bad)

    def test_the_offset_branch_calls_process_wav(self):
        import qt_editor.main_window as mw
        source = open(mw.__file__, encoding='utf-8').read()
        head = source[source.index('有偏移：處理音源後放入難度子資料夾'):]
        self.assertIn('process_wav', head[:1200])


if __name__ == '__main__':
    unittest.main()
