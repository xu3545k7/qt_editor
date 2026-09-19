"""真的把 `export_song` 從頭跑到尾，特別是**有播放偏移**的那條分支。

為什麼要端到端：這條路徑連續壞過兩次，兩次都是單元測試抓不到的。

1. `from rip import process_audio` —— `rip.py` 根本不存在，`except ImportError`
   把未處理的原檔複製過去，檔名標著 `-1043ms` 內容卻原封不動。
2. 修好之後留了一行 `logging.debug(..., processed, ...)`，而 `processed` 已經
   不再被賦值 —— 使用者拿到
   `cannot access local variable 'processed' where it is not associated with a value`。

第 2 個是執行期的 NameError：語法沒問題、import 沒問題、grep 原始碼也看不出來，
**只有真的走過那一行才會炸**。所以這裡把對話框整個換掉、讓整條路跑完。
"""

import json
import os
import tempfile
import unittest
import wave

from PyQt5.QtWidgets import QApplication, QDialog

import qt_editor.main_window as mw
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])
_window = None


def window():
    """整個檔案共用一個 MainWindow：同一個行程開關多個會 segfault。"""
    global _window
    if _window is None:
        _window = MainWindow()
    return _window


def write_wav(path, duration_ms=4000, rate=8000, level=1200):
    frames = int(round(duration_ms / 1000.0 * rate))
    with wave.open(path, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(int(level).to_bytes(2, 'little', signed=True) * frames)


def chart():
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    notes = []
    for i in range(8):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 400, i * 400 + 300, 300
        n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, i % 2
        n.pitch, n.velocity = 60 + i, 90
        notes.append(n)
    m.notes_tree = notes
    m.music_end_ms = 4000.0
    m.rebuild_display_cache()
    return m


class StubDialog:
    """站在 ExportSongDialog 的位置，直接回傳我們要的答案。"""

    def __init__(self, root, **kwargs):
        self._root = root

    def __call__(self, *a, **k):
        return self

    def exec_(self):
        return QDialog.Accepted

    Accepted = QDialog.Accepted

    def export_root(self):
        return self._root

    def exports_to_game(self):
        return False

    def display_name(self):
        return 'TestSong'

    def author(self):
        return 'Tester'

    def diff_name(self):
        return 'Real'

    def diff_level(self):
        return 10

    def cover_path(self):
        return ''

    def category(self):
        return 'Other'

    def is_append_mode(self):
        return False

    def append_folder(self):
        return ''

    def append_folder_full(self):
        return ''

    def existing_register(self):
        return None

    def no_background_music(self):
        return False

    def video_path(self):
        return ''

    def video_start_sec(self):
        return 0.0


class ExportWithOffset(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.wav = os.path.join(tempfile.mkdtemp(), 'source.wav')
        write_wav(self.wav)
        self.win = window()
        self.win.view.model = chart()
        self.win.audio.audio_path = self.wav
        self._dialog = mw.ExportSongDialog
        self._piano = MainWindow.render_piano_wav
        mw.ExportSongDialog = StubDialog(self.root)
        MainWindow.render_piano_wav = lambda self, path: False
        # 無頭環境開 modal QMessageBox 會直接 segfault（不是斷言失敗，是行程
        # 掛掉、什麼輸出都沒有），所以全部擋掉。question 回 No = 不算鋼琴音軌。
        self._boxes = {name: getattr(mw.QMessageBox, name)
                       for name in ('question', 'information', 'warning', 'critical')}
        mw.QMessageBox.question = staticmethod(
            lambda *a, **k: mw.QMessageBox.No)
        for name in ('information', 'warning', 'critical'):
            setattr(mw.QMessageBox, name, staticmethod(lambda *a, **k: None))

    def tearDown(self):
        mw.ExportSongDialog = self._dialog
        MainWindow.render_piano_wav = self._piano
        for name, fn in self._boxes.items():
            setattr(mw.QMessageBox, name, fn)

    def run_export(self, offset_ms):
        self.win._playback_offset_ms = offset_ms
        self.win.export_song()
        folder = os.path.join(self.root, 'TestSong')
        self.assertTrue(os.path.isdir(folder), '曲目資料夾沒有建出來')
        with open(os.path.join(folder, 'register.json'), encoding='utf-8') as f:
            return folder, json.load(f)

    def audio_of(self, folder, register):
        rel = register['difficulties'][0]['audioResourcePath']
        return os.path.join(folder, rel.split('/', 2)[2] + '.wav')

    def lead_silence_ms(self, path):
        with wave.open(path, 'rb') as f:
            rate = f.getframerate()
            data = f.readframes(f.getnframes())
        for i in range(0, len(data), 2):
            if int.from_bytes(data[i:i + 2], 'little', signed=True):
                return (i // 2) / rate * 1000.0
        return 0.0

    def test_a_delay_actually_reaches_the_audio(self):
        """使用者的情境：偏移 -1043ms（延後）。"""
        folder, reg = self.run_export(-1043)
        audio = self.audio_of(folder, reg)
        self.assertTrue(os.path.isfile(audio), '找不到匯出的音訊 %s' % audio)
        self.assertAlmostEqual(self.lead_silence_ms(audio), 1043, delta=2,
                               msg='偏移沒有套用到音訊上')

    def test_the_exported_audio_differs_from_the_source(self):
        folder, reg = self.run_export(-1043)
        with open(self.wav, 'rb') as a, open(self.audio_of(folder, reg), 'rb') as b:
            self.assertNotEqual(a.read(), b.read(),
                                '輸出和來源一模一樣 = 偏移根本沒套用')

    def test_an_advance_trims_the_front(self):
        """提前 = 砍掉音訊前面 500ms。

        總長度不會變短：砍完之後尾巴補靜音補回譜面長度（見 _pad_wav_tail），
        因為音訊往前提之後，尾端就蓋不到譜面的結尾了。所以要驗的是「前面那
        500ms 不見了」，不是「總長度變短」。
        """
        folder, reg = self.run_export(500)
        with wave.open(self.wav) as f:
            rate = f.getframerate()
            src = f.readframes(f.getnframes())
        with wave.open(self.audio_of(folder, reg)) as f:
            out = f.readframes(f.getnframes())
        cut = int(0.5 * rate) * 2
        self.assertEqual(out[:len(src) - cut], src[cut:],
                         '前面的 500ms 沒有被砍掉')
        self.assertEqual(set(out[len(src) - cut:]), {0},
                         '補回來的尾巴不是靜音')

    def test_an_advance_still_covers_the_chart(self):
        folder, reg = self.run_export(500)
        with wave.open(self.audio_of(folder, reg)) as f:
            length = f.getnframes() / f.getframerate() * 1000.0
        self.assertGreaterEqual(length + 1, 4000, '音訊短於譜面，結尾會沒聲音')

    def test_the_filename_records_the_offset(self):
        folder, reg = self.run_export(-1043)
        self.assertIn('-1043ms', reg['difficulties'][0]['audioResourcePath'])

    def test_no_offset_still_exports(self):
        folder, reg = self.run_export(0)
        self.assertTrue(os.path.isfile(self.audio_of(folder, reg)))

    def test_the_chart_is_written_too(self):
        folder, reg = self.run_export(-1043)
        rel = reg['difficulties'][0]['chartFileName']
        self.assertTrue(
            os.path.isfile(os.path.join(folder, rel.split('/', 2)[2] + '.json')))


class ExportWithExistingSongAudio(ExportWithOffset):
    """曲目資料夾裡已經有一份同名音訊（先前匯出過），這次的音訊改過（例如加了開頭空白）。

    以前直接沿用資料夾裡的舊檔，新音訊永遠匯不出去。
    """

    def old_song_audio(self, same):
        folder = os.path.join(self.root, 'TestSong')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'TestSong.wav')
        if same:
            import shutil
            shutil.copy2(self.wav, path)
        else:
            write_wav(path, duration_ms=1000, level=50)
        return path

    def test_different_old_audio_is_not_reused(self):
        old = self.old_song_audio(same=False)
        with open(old, 'rb') as fh:
            old_bytes = fh.read()
        folder, reg = self.run_export(0)
        audio = self.audio_of(folder, reg)
        with open(self.wav, 'rb') as a, open(audio, 'rb') as b:
            self.assertEqual(a.read(), b.read(), '匯出的還是資料夾裡的舊音訊')
        with open(old, 'rb') as fh:
            self.assertEqual(fh.read(), old_bytes, '其他難度共用的舊音訊被蓋掉了')

    def test_identical_old_audio_is_reused(self):
        self.old_song_audio(same=True)
        folder, reg = self.run_export(0)
        self.assertEqual(reg['difficulties'][0]['audioResourcePath'], 'songs/TestSong/TestSong')


class NoAudioStub(StubDialog):
    def __init__(self, root, mode, append_folder=''):
        super().__init__(root)
        self._mode = mode
        self._append = append_folder
        self._register = None
        if append_folder:
            with open(os.path.join(append_folder, 'register.json'), encoding='utf-8') as f:
                self._register = json.load(f)

    def no_audio_mode(self):
        return self._mode

    def is_append_mode(self):
        return bool(self._append)

    def append_folder(self):
        return os.path.basename(self._append)

    def append_folder_full(self):
        return self._append

    def existing_register(self):
        return self._register


class ExportWithoutAudio(unittest.TestCase):
    """沒載入音源也能匯出：不問、不擋，照對話框選的處理背景音樂。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.win = window()
        self.win.view.model = chart()
        self.win.audio.audio_path = ''
        self.win._playback_offset_ms = 0
        self._dialog = mw.ExportSongDialog
        self._piano = MainWindow.render_piano_wav
        self.rendered = []

        def fake_render(win, path):
            self.rendered.append(path)
            write_wav(path, duration_ms=4000)
            return True
        MainWindow.render_piano_wav = fake_render
        self.asked = []
        self._boxes = {name: getattr(mw.QMessageBox, name)
                       for name in ('question', 'information', 'warning', 'critical')}
        mw.QMessageBox.question = staticmethod(
            lambda *a, **k: self.asked.append(a[1:3]) or mw.QMessageBox.No)
        for name in ('information', 'warning', 'critical'):
            setattr(mw.QMessageBox, name, staticmethod(lambda *a, **k: None))

    def tearDown(self):
        mw.ExportSongDialog = self._dialog
        MainWindow.render_piano_wav = self._piano
        for name, fn in self._boxes.items():
            setattr(mw.QMessageBox, name, fn)

    def export(self, stub):
        mw.ExportSongDialog = stub
        self.win.export_song()
        folder = stub.append_folder_full() or os.path.join(self.root, 'TestSong')
        with open(os.path.join(folder, 'register.json'), encoding='utf-8') as f:
            return folder, json.load(f)

    def test_silent_needs_no_questions(self):
        folder, reg = self.export(NoAudioStub(self.root, 'silent'))
        diff = reg['difficulties'][0]
        self.assertTrue(diff.get('noBackgroundMusic'))
        self.assertTrue(os.path.isfile(os.path.join(folder, 'TestSong.wav')))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.rendered, [])

    def test_midi_renders_the_background_music(self):
        folder, reg = self.export(NoAudioStub(self.root, 'midi'))
        diff = reg['difficulties'][0]
        self.assertEqual(self.rendered, [os.path.join(folder, 'TestSong.wav')])
        self.assertEqual(diff['audioResourcePath'], 'songs/TestSong/TestSong')
        self.assertNotIn('noBackgroundMusic', diff)
        self.assertEqual(self.asked, [])

    def test_reuse_keeps_the_songs_music(self):
        song = os.path.join(self.root, 'TestSong')
        os.makedirs(song)
        with open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as f:
            json.dump({'displayName': 'TestSong', 'author': 'A', 'difficulties': [{
                'difficultyName': 'Easy', 'difficultyLevel': 3,
                'chartFileName': 'songs/TestSong/Easy/TestSong',
                'audioResourcePath': 'songs/TestSong/original',
                'coverResourcePath': ''}]}, f)
        folder, reg = self.export(NoAudioStub(self.root, 'reuse', append_folder=song))
        real = [d for d in reg['difficulties'] if d['difficultyName'] == 'Real'][0]
        self.assertEqual(real['audioResourcePath'], 'songs/TestSong/original')
        self.assertNotIn('noBackgroundMusic', real)
        self.assertFalse(os.path.exists(os.path.join(folder, 'TestSong.wav')))


class NoAudioDialogTests(unittest.TestCase):
    def test_choices_show_only_without_audio(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        root = tempfile.mkdtemp()
        dlg = ExportSongDialog(default_root=root)
        self.assertEqual(dlg.no_audio_mode(), 'silent')
        self.assertTrue(dlg._cb_no_audio.isVisibleTo(dlg))
        self.assertFalse(dlg._chk_no_bgm.isVisibleTo(dlg))
        reuse = dlg._cb_no_audio.model().item(dlg._cb_no_audio.findData('reuse'))
        self.assertFalse(reuse.isEnabled())
        dlg._cb_no_audio.setCurrentIndex(dlg._cb_no_audio.findData('midi'))
        self.assertEqual(dlg.no_audio_mode(), 'midi')
        dlg.close()
        wav = os.path.join(root, 'a.wav')
        write_wav(wav)
        dlg = ExportSongDialog(default_root=root, wav_path=wav)
        self.assertEqual(dlg.no_audio_mode(), '')
        self.assertFalse(dlg._cb_no_audio.isVisibleTo(dlg))
        dlg.close()

    def test_appending_to_a_song_with_music_defaults_to_reuse(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        root = tempfile.mkdtemp()
        song = os.path.join(root, 'S')
        os.makedirs(song)
        with open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as f:
            json.dump({'displayName': 'S', 'difficulties': [
                {'difficultyName': 'Easy', 'audioResourcePath': 'songs/S/S'}]}, f)
        dlg = ExportSongDialog(default_root=root)
        dlg._apply_song_folder(song)
        self.assertEqual(dlg.no_audio_mode(), 'reuse')
        dlg.close()


if __name__ == '__main__':
    unittest.main()
