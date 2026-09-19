"""修剪結尾空白：曲終拖太久、音訊尾巴一大段靜音，兩種都要抓到並修掉。"""

import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import wave

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import song_library as L  # noqa: E402
from qt_editor import tail_trim as T  # noqa: E402

RATE = 8000


def tone_wav(path, sound_ms, silence_ms):
    """前面有聲音、後面一段靜音的 WAV。"""
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        loud = struct.pack('<h', 9000) * int(RATE * sound_ms / 1000)
        quiet = b'\0\0' * int(RATE * silence_ms / 1000)
        w.writeframes(loud + quiet)


def chart_json(path, last_note_ms, finish_ms):
    data = {'first_bpm': 120.0, 'music_finish_time_msec': int(finish_ms), 'bpm': 120.0,
            'notes': [{'startTime': 0, 'endTime': 100, 'gateTime': 100, 'startLane': 0,
                       'endLane': 2, 'pitch': 60, 'note_type': 0, 'hand': 0},
                      {'startTime': int(last_note_ms) - 200, 'endTime': int(last_note_ms),
                       'gateTime': 200, 'startLane': 0, 'endLane': 2, 'pitch': 62,
                       'note_type': 0, 'hand': 0}]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False)


def make_song(root, folder, last_note_ms, finish_ms, sound_ms, silence_ms, no_bgm=False):
    song = os.path.join(root, folder)
    chart_json(os.path.join(song, 'Real', folder + '.json'), last_note_ms, finish_ms)
    tone_wav(os.path.join(song, folder + '.wav'), sound_ms, silence_ms)
    diff = {'difficultyName': 'Real', 'difficultyLevel': 12,
            'chartFileName': 'songs/%s/Real/%s' % (folder, folder),
            'audioResourcePath': 'songs/%s/%s' % (folder, folder)}
    if no_bgm:
        diff['noBackgroundMusic'] = True
    with io.open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as fh:
        json.dump({'displayName': folder, 'author': 'A', 'difficulties': [diff]}, fh,
                  ensure_ascii=False)
    return song


class TailTrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'UserSongs')
        os.makedirs(self.root)
        with io.open(os.path.join(self.root, 'library.json'), 'w', encoding='utf-8') as fh:
            json.dump({'songs': [], 'categories': []}, fh)
        # 曲終比最後一顆音符晚 8 秒
        make_song(self.root, 'LateFinish', last_note_ms=10000, finish_ms=18000,
                  sound_ms=10000, silence_ms=9000)
        # 曲終剛好，但音訊尾巴多了 20 秒靜音
        make_song(self.root, 'LongAudio', last_note_ms=10000, finish_ms=11000,
                  sound_ms=10000, silence_ms=20000)
        # 收得剛剛好的，不該被抓
        make_song(self.root, 'Tidy', last_note_ms=10000, finish_ms=11000,
                  sound_ms=10200, silence_ms=800)
        self.lib = L.SongLibrary(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self, folder):
        song = [s for s in self.lib.songs() if s.folder == folder][0]
        return T.scan_song(song)

    def audio_ms(self, folder):
        with wave.open(os.path.join(self.root, folder, folder + '.wav')) as w:
            return w.getnframes() / w.getframerate() * 1000.0

    def finish_ms(self, folder):
        with io.open(os.path.join(self.root, folder, 'Real', folder + '.json'),
                     encoding='utf-8') as fh:
            return json.load(fh)['music_finish_time_msec']

    # ── 掃描 ─────────────────────────────────────────────────────────

    def test_measures_the_blank_before_the_end(self):
        report = self.report('LateFinish')
        self.assertEqual(report.last_note_ms, 10000)
        self.assertAlmostEqual(report.audible_ms, 10000, delta=60)
        self.assertAlmostEqual(report.blank_ms, 8000, delta=60)

    def test_measures_the_blank_in_the_audio(self):
        report = self.report('LongAudio')
        self.assertAlmostEqual(report.audio_ms, 30000, delta=60)
        self.assertAlmostEqual(report.audio_blank_ms, 19000, delta=60)

    def test_a_tidy_song_is_not_flagged(self):
        report = self.report('Tidy')
        self.assertLess(report.worst_blank_ms, 3000)
        found = [r.folder for r in T.scan_library(self.lib)]
        self.assertNotIn('Tidy', found)
        self.assertEqual(sorted(found), ['LateFinish', 'LongAudio'])

    def test_no_background_songs_skip_the_audio_check(self):
        make_song(self.root, 'Silent', last_note_ms=8000, finish_ms=9000,
                  sound_ms=0, silence_ms=30000, no_bgm=True)
        report = self.report('Silent')
        self.assertEqual(report.audio_ms, 0, '無背景音樂的曲子不量音訊')
        self.assertEqual(report.audio_blank_ms, 0)

    # ── 修剪 ─────────────────────────────────────────────────────────

    def test_trimming_pulls_the_end_in_and_cuts_the_audio(self):
        done = T.trim_all(self.lib, T.scan_library(self.lib), tail_ms=1500)
        self.assertEqual(len(done), 2)
        self.assertAlmostEqual(self.finish_ms('LateFinish'), 11500, delta=60)
        self.assertAlmostEqual(self.audio_ms('LateFinish'), 11500, delta=100)
        self.assertAlmostEqual(self.audio_ms('LongAudio'), 11500, delta=100)

    def test_the_end_is_never_pushed_later(self):
        report = self.report('LongAudio')      # 曲終 11 秒、內容 10 秒
        T.trim_song(self.lib, report, tail_ms=5000)
        self.assertEqual(self.finish_ms('LongAudio'), 11000, '曲終只會往前收')

    def test_the_audio_still_covers_the_end(self):
        T.trim_all(self.lib, T.scan_library(self.lib), tail_ms=1500)
        for folder in ('LateFinish', 'LongAudio'):
            self.assertGreaterEqual(self.audio_ms(folder) + 60, self.finish_ms(folder),
                                    '音訊不能比曲終短，不然最後幾秒沒聲音')

    def test_the_originals_are_backed_up(self):
        T.trim_all(self.lib, T.scan_library(self.lib), tail_ms=1500)
        backups = {}
        root = os.path.join(self.tmp, 'UserSongs_editor', T.BACKUP_ROOT)
        for base, _dirs, files in os.walk(root):
            if files:
                backups[os.path.basename(base)] = sorted(files)
        self.assertEqual(backups, {'LateFinish': ['LateFinish.json', 'LateFinish.wav'],
                                   'LongAudio': ['LongAudio.json', 'LongAudio.wav']})
        # 備份的譜面還是原本的曲終（備份放在一個時間戳資料夾底下）
        stamp = os.path.join(root, os.listdir(root)[0])
        with io.open(os.path.join(stamp, 'LateFinish', 'LateFinish.json'), encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['music_finish_time_msec'], 18000)

    def test_keeping_the_audio_is_optional(self):
        before = self.audio_ms('LongAudio')
        T.trim_all(self.lib, T.scan_library(self.lib), tail_ms=1500, trim_audio_file=False)
        self.assertAlmostEqual(self.audio_ms('LongAudio'), before, delta=1)

    def test_the_dialog_lists_and_trims(self):
        from qt_editor.tail_trim_dialog import TailTrimDialog
        dlg = TailTrimDialog(None, self.lib)
        try:
            self.assertEqual(dlg.table.rowCount(), 2)
            self.assertEqual(len(dlg.checked()), 2)
            from PyQt5.QtCore import Qt
            dlg.table.item(1, 0).setCheckState(Qt.Unchecked)
            self.assertEqual(len(dlg.checked()), 1)
        finally:
            dlg.close()


if __name__ == '__main__':
    unittest.main()


class OutroTests(unittest.TestCase):
    """「連尾奏一起剪」：最後一顆音符之後音樂還在放的那種（例如 LaVI）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'UserSongs')
        os.makedirs(self.root)
        with io.open(os.path.join(self.root, 'library.json'), 'w', encoding='utf-8') as fh:
            json.dump({'songs': [], 'categories': []}, fh)
        # 音符到 10 秒，音樂放到 14 秒，曲終 15 秒：沒有靜音，但有 5 秒沒事做
        make_song(self.root, 'Outro', last_note_ms=10000, finish_ms=15000,
                  sound_ms=14000, silence_ms=1000)
        self.lib = L.SongLibrary(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self):
        song = [s for s in self.lib.songs() if s.folder == 'Outro'][0]
        return T.scan_song(song)

    def audio_ms(self):
        with wave.open(os.path.join(self.root, 'Outro', 'Outro.wav')) as w:
            return w.getnframes() / w.getframerate() * 1000.0

    def finish_ms(self):
        with io.open(os.path.join(self.root, 'Outro', 'Real', 'Outro.json'),
                     encoding='utf-8') as fh:
            return json.load(fh)['music_finish_time_msec']

    def test_it_is_not_flagged_as_blank_by_default(self):
        report = self.report()
        self.assertLess(report.worst_blank_ms, 3000, '尾奏不是空白')
        self.assertAlmostEqual(report.note_blank_ms, 5000, delta=60)
        self.assertEqual(T.scan_library(self.lib), [])

    def test_including_the_outro_finds_it(self):
        found = T.scan_library(self.lib, include_outro=True)
        self.assertEqual([r.folder for r in found], ['Outro'])

    def test_cutting_the_outro_ends_after_the_last_note(self):
        T.trim_all(self.lib, T.scan_library(self.lib, include_outro=True),
                   tail_ms=1500, cut_outro=True)
        self.assertAlmostEqual(self.finish_ms(), 11500, delta=60)
        self.assertAlmostEqual(self.audio_ms(), 11500, delta=100)

    def test_the_music_fades_out_instead_of_cutting_off(self):
        T.trim_all(self.lib, T.scan_library(self.lib, include_outro=True),
                   tail_ms=1500, cut_outro=True)
        with wave.open(os.path.join(self.root, 'Outro', 'Outro.wav')) as w:
            rate = w.getframerate()
            w.setpos(max(0, w.getnframes() - int(rate * 0.05)))
            last = w.readframes(int(rate * 0.05))
        import audioop
        self.assertLess(audioop.max(last, 2), 4000, '結尾要淡出，不然會啪一聲')
