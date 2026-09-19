"""匯出完整曲目：背景影片與分類。

兩個欄位存在**不同的地方**，這是最容易搞錯的一點：

* 影片 → `register.json` 的難度項（`videoPath` / `videostartTime`）。路徑格式和
  曲繪一樣是 `songs/<資料夾>/<名稱>`，**不含副檔名**——遊戲端
  `ExternalSongLibrary.ResolveAsset` 會自己去試 .mp4 / .webm / .mov。
* 分類 → 匯出根目錄的 `library.json`（索引檔），每首歌一筆 `folderName` +
  `category` + `categories`，外加一份全域 `categories` 清單給選歌畫面列頁籤。
"""

import contextlib
import json
import os
import tempfile
import unittest
import wave

from PyQt5.QtWidgets import QApplication, QMessageBox

from qt_editor.main_window import MainWindow
from qt_editor.models import NoteModel

_app = QApplication.instance() or QApplication([])

_shared_window = None


def _window():
    """整個測試檔共用一個 MainWindow——同一個行程裡開關多個會 segfault。"""
    global _shared_window
    if _shared_window is None:
        _shared_window = MainWindow()
    return _shared_window


@contextlib.contextmanager
def _patched_warning(calls, answer):
    import qt_editor.main_window as mw
    original = mw.QMessageBox.warning

    def fake(parent, title, text, *a, **kw):
        calls.append(text)
        return answer
    mw.QMessageBox.warning = staticmethod(fake)
    try:
        yield
    finally:
        mw.QMessageBox.warning = original


class CategoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 30.0, 4))
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        self.win.close()

    def library(self):
        with open(os.path.join(self.root, 'library.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_it_creates_the_index_when_missing(self):
        self.win._write_song_category(self.root, 'mysong', 'Deemo')
        data = self.library()
        self.assertEqual(len(data['songs']), 1)
        self.assertEqual(data['songs'][0]['folderName'], 'mysong')
        self.assertEqual(data['songs'][0]['category'], 'Deemo')
        self.assertIn('Deemo', data['categories'])

    def test_the_song_lists_its_category(self):
        self.win._write_song_category(self.root, 'mysong', 'POPS')
        self.assertEqual(self.library()['songs'][0]['categories'], ['POPS'])

    def test_a_second_song_is_added_not_replaced(self):
        self.win._write_song_category(self.root, 'a', 'Deemo')
        self.win._write_song_category(self.root, 'b', 'POPS')
        data = self.library()
        self.assertEqual({s['folderName'] for s in data['songs']}, {'a', 'b'})
        self.assertEqual(set(data['categories']), {'Deemo', 'POPS'})

    def test_re_exporting_updates_in_place(self):
        self.win._write_song_category(self.root, 'a', 'Deemo')
        self.win._write_song_category(self.root, 'a', 'POPS')
        data = self.library()
        self.assertEqual(len(data['songs']), 1, '同一首歌不該被加兩次')
        self.assertEqual(data['songs'][0]['category'], 'POPS')

    def test_other_songs_are_left_alone(self):
        path = os.path.join(self.root, 'library.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'songs': [{'id': 'x', 'folderName': 'other',
                                  'category': 'Keep', 'categories': ['Keep']}],
                       'categories': ['Keep']}, f)
        self.win._write_song_category(self.root, 'mine', 'New')
        data = self.library()
        other = [s for s in data['songs'] if s['folderName'] == 'other'][0]
        self.assertEqual(other['category'], 'Keep')
        self.assertEqual(set(data['categories']), {'Keep', 'New'})

    def test_an_empty_category_falls_back_to_other(self):
        self.win._write_song_category(self.root, 'a', '   ')
        self.assertEqual(self.library()['songs'][0]['category'], 'Other')

    def test_a_corrupt_index_is_replaced_not_crashed(self):
        path = os.path.join(self.root, 'library.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{ not json')
        self.win._write_song_category(self.root, 'a', 'Deemo')
        self.assertEqual(self.library()['songs'][0]['category'], 'Deemo')

    def test_all_is_never_added_as_a_real_category(self):
        # 遊戲端把 ALL 當成「全部」的虛擬頁籤，不該進清單
        self.win._write_song_category(self.root, 'a', 'ALL')
        self.assertNotIn('ALL', self.library()['categories'])


class ExportDialogFieldTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        with open(os.path.join(self.root, 'library.json'), 'w', encoding='utf-8') as f:
            json.dump({'songs': [{'folderName': 'x', 'category': 'Deemo',
                                  'categories': ['Deemo', 'Chunithm']}],
                       'categories': ['POPS']}, f)

    def dialog(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        return ExportSongDialog(default_root=self.root)

    def test_existing_categories_are_offered(self):
        d = self.dialog()
        names = {d._cb_category.itemText(i) for i in range(d._cb_category.count())}
        self.assertLessEqual({'POPS', 'Deemo', 'Chunithm', 'Other'}, names)

    def test_the_category_is_editable(self):
        d = self.dialog()
        self.assertTrue(d._cb_category.isEditable(), '要能打新的分類')
        d._cb_category.setCurrentText('BrandNew')
        self.assertEqual(d.category(), 'BrandNew')

    def test_an_empty_category_falls_back(self):
        d = self.dialog()
        d._cb_category.setCurrentText('   ')
        self.assertEqual(d.category(), 'Other')

    def names(self, d):
        return {d._cb_category.itemText(i) for i in range(d._cb_category.count())}

    def test_songlist_json_is_also_read(self):
        """同一個目錄還有一份 songlist.json，分類放在 categories 字典的鍵。"""
        with open(os.path.join(self.root, 'songlist.json'), 'w', encoding='utf-8') as f:
            json.dump({'categories': {'Arcaea': ['a'], 'Classical': ['b']}}, f)
        self.assertLessEqual({'Arcaea', 'Classical'}, self.names(self.dialog()))

    def test_songlist_alone_is_enough(self):
        os.remove(os.path.join(self.root, 'library.json'))
        with open(os.path.join(self.root, 'songlist.json'), 'w', encoding='utf-8') as f:
            json.dump({'categories': {'東方Project': ['a']}}, f)
        self.assertIn('東方Project', self.names(self.dialog()))

    def test_changing_the_export_root_reloads_the_categories(self):
        """換匯出根目錄要重讀——不然下拉停在開視窗當下那份，常常是空的。"""
        other = tempfile.mkdtemp()
        with open(os.path.join(other, 'library.json'), 'w', encoding='utf-8') as f:
            json.dump({'categories': ['sound voltex']}, f)
        d = self.dialog()
        self.assertNotIn('sound voltex', self.names(d))
        d._export_root = other
        d._reload_categories()
        self.assertIn('sound voltex', self.names(d))

    def test_a_root_without_an_index_still_offers_the_default(self):
        d = self.dialog()
        d._export_root = tempfile.mkdtemp()
        d._reload_categories()
        self.assertEqual(self.names(d), {'Other'})

    def test_a_corrupt_songlist_does_not_lose_library_categories(self):
        with open(os.path.join(self.root, 'songlist.json'), 'w', encoding='utf-8') as f:
            f.write('{ not json')
        self.assertLessEqual({'POPS', 'Deemo'}, self.names(self.dialog()))

    def test_the_auto_detected_root_exists(self):
        """預設根目錄指到不存在的路徑時，分類永遠讀不到——這就是原本的 bug。"""
        from qt_editor.export_song_dialog import SONGS_ROOT
        self.assertTrue(os.path.isdir(SONGS_ROOT),
                        '自動偵測的匯出根目錄不存在: %s' % SONGS_ROOT)

    def test_the_root_is_found_from_anywhere_in_the_workspace(self):
        """打包後 _base_dir 是啟動時的工作目錄，寫死一層會落空，所以往上找。"""
        from qt_editor.export_song_dialog import _find_songs_root
        import qt_editor
        here = os.path.dirname(os.path.dirname(os.path.abspath(qt_editor.__file__)))
        for start in (here, os.path.join(here, 'dist'), os.path.dirname(here)):
            self.assertTrue(_find_songs_root(start),
                            '從 %s 找不到曲目根目錄' % start)

    def test_an_unrelated_directory_finds_nothing(self):
        from qt_editor.export_song_dialog import _find_songs_root
        self.assertEqual(_find_songs_root(tempfile.mkdtemp()), '')

    def test_video_is_optional_and_empty_by_default(self):
        d = self.dialog()
        self.assertEqual(d.video_path(), '')
        self.assertEqual(d.video_start_sec(), 0.0)

    def test_the_video_offset_accepts_negatives(self):
        d = self.dialog()
        d._spin_video_start.setValue(-2.5)
        self.assertAlmostEqual(d.video_start_sec(), -2.5)

    def test_clearing_the_video_empties_it(self):
        d = self.dialog()
        d._le_video.setText(r'C:\some\clip.mp4')
        self.assertTrue(d.video_path())
        d._btn_video_clear.click()
        self.assertEqual(d.video_path(), '')


if __name__ == '__main__':
    unittest.main()


class NoBackgroundMusicTests(unittest.TestCase):
    """純鋼琴曲：遊戲裡只該響玩家打出來的 keysound。

    遊戲端 `ExternalSongLibrary` 驗證時 `audioResourcePath` 是**必填**
    （`RequireAsset`），留空會整首被判定不合法，所以做法是輸出一段和譜面等長的
    **無聲**音訊，再把鋼琴音軌欄位拿掉——遊戲的 `ShouldPlayGameplayPianoLayer()`
    在合成鋼琴模式下本來就不播錄好的鋼琴層，但玩家可能沒開那個模式，留著會變成
    keysound 底下又有一台鋼琴在彈。
    """

    def setUp(self):
        self.win = MainWindow()
        self.win._load_model_all(NoteModel.create_new('t', 120.0, 30.0, 4))
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        self.win.close()

    def wav(self, ms=206800, rate=8000):
        import wave
        path = os.path.join(self.dir, 'silent.wav')
        self.win._write_silent_wav(path, ms, rate)
        return path, wave.open(path, 'rb')

    def test_the_track_is_the_right_length(self):
        path, f = self.wav()
        try:
            self.assertAlmostEqual(f.getnframes() / f.getframerate(), 206.8, places=1)
        finally:
            f.close()

    def test_it_is_actually_silent(self):
        path, f = self.wav(3000)
        try:
            self.assertFalse(any(f.readframes(f.getnframes())))
        finally:
            f.close()

    def test_it_is_a_readable_mono_wav(self):
        path, f = self.wav(3000)
        try:
            self.assertEqual(f.getnchannels(), 1)
            self.assertEqual(f.getsampwidth(), 2)
        finally:
            f.close()

    def test_it_stays_small(self):
        """8kHz 單聲道：207 秒約 3.3MB。44.1kHz 立體聲會是 36MB。"""
        path, f = self.wav()
        f.close()
        self.assertLess(os.path.getsize(path), 5_000_000)

    def test_a_zero_length_chart_still_writes_a_valid_file(self):
        path, f = self.wav(0)
        try:
            self.assertGreaterEqual(f.getnframes(), 1)
        finally:
            f.close()

    def test_the_dialog_defaults_to_off(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        dlg = ExportSongDialog(default_root=self.dir)
        self.assertFalse(dlg.no_background_music(),
                         '一般曲目不該預設關掉背景音樂')

    def test_the_dialog_reports_the_choice(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        # 有載入音源時才有這個勾選框（沒音源時改成「背景音樂要：」下拉）
        wav = os.path.join(self.dir, 'bgm.wav')
        open(wav, 'wb').close()
        dlg = ExportSongDialog(default_root=self.dir, wav_path=wav)
        dlg._chk_no_bgm.setChecked(True)
        self.assertTrue(dlg.no_background_music())


class NoPitchGuardTests(unittest.TestCase):
    """無背景音樂 + 沒有音高 = 遊戲裡整首靜音，匯出前要擋下來。"""

    def model(self, pitched, unpitched):
        from qt_editor.models import GNote, NoteModel
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        notes = []
        for i in range(pitched + unpitched):
            n = GNote(None, i)
            n.start, n.end, n.gate = i * 100, i * 100 + 80, 80
            n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
            n.pitch = 60 if i < pitched else None
            notes.append(n)
        m.notes_tree = notes
        return m

    def test_a_fully_pitched_chart_passes_silently(self):
        w = _window()
        self.assertTrue(w._warn_if_no_pitch_data(self.model(100, 0)))

    def test_an_empty_chart_passes(self):
        w = _window()
        self.assertTrue(w._warn_if_no_pitch_data(self.model(0, 0)))

    def test_a_chart_with_no_pitch_at_all_is_flagged(self):
        w = _window()
        calls = []
        with _patched_warning(calls, answer=QMessageBox.Cancel):
            self.assertFalse(w._warn_if_no_pitch_data(self.model(0, 100)))
        self.assertEqual(len(calls), 1, '應該要跳警告')
        self.assertIn('100', calls[0], '訊息要說有幾顆沒音高')

    def test_the_user_can_override(self):
        w = _window()
        with _patched_warning([], answer=QMessageBox.Yes):
            self.assertTrue(w._warn_if_no_pitch_data(self.model(0, 100)))

    def test_a_few_missing_is_tolerated(self):
        # 1% 以內不打擾——還原音高本來就會有零星幾顆配不到
        w = _window()
        self.assertTrue(w._warn_if_no_pitch_data(self.model(1000, 5)))


# ---------------------------------------------------------------------------
# 已出貨的 etude：無背景音樂的曲目在資料層必須自洽
# ---------------------------------------------------------------------------
ETUDE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'Nostalgia-clone', 'UserSongs', 'no45-scherzo-alla-napolitana')


@unittest.skipUnless(os.path.isdir(ETUDE), '找不到 etude 曲目資料夾')
class TestKeysoundOnlySong(unittest.TestCase):
    """no45-scherzo-alla-napolitana 改成只播玩家 keysound 之後的不變條件。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ETUDE, 'register.json'), encoding='utf-8') as f:
            cls.reg = json.load(f)
        cls.diffs = cls.reg['difficulties']

    def _chart(self, diff):
        from qt_editor.models import NoteModel
        rel = diff['chartFileName'].split('/', 2)[2] + '.json'
        m = NoteModel()
        m.load_json(os.path.join(ETUDE, rel.replace('/', os.sep)))
        return m

    def test_every_difficulty_declares_no_background_music(self):
        for d in self.diffs:
            self.assertTrue(d.get('noBackgroundMusic'),
                            '%s 沒有宣告 noBackgroundMusic，遊戲錄音模式下會整首沒聲音'
                            % d['difficultyName'])

    def test_no_piano_stem_is_left_behind(self):
        # 留著錄好的鋼琴層等於在 keysound 底下又擺一台鋼琴
        for d in self.diffs:
            self.assertNotIn('pianoAudioResourcePath', d, d['difficultyName'])

    def test_no_speed_manipulation_remains(self):
        # 沒有背景音樂就沒有東西可以變速
        for d in self.diffs:
            self.assertNotIn('audioManage', d, d['difficultyName'])

    def test_the_backing_track_is_silent(self):
        paths = {d['audioResourcePath'] for d in self.diffs}
        self.assertEqual(len(paths), 1, '兩個難度應共用同一段無聲音訊')
        wav = os.path.join(ETUDE, paths.pop().split('/')[-1] + '.wav')
        with wave.open(wav) as f:
            frames = f.readframes(f.getnframes())
        self.assertEqual(max(frames), 0, '背景音軌不是全靜音')

    def test_the_backing_track_outlasts_the_longest_chart(self):
        longest = max(max(n.end for n in self._chart(d).notes_tree)
                      for d in self.diffs)
        wav = os.path.join(
            ETUDE, self.diffs[0]['audioResourcePath'].split('/')[-1] + '.wav')
        with wave.open(wav) as f:
            dur_ms = f.getnframes() / f.getframerate() * 1000.0
        self.assertGreaterEqual(
            dur_ms, longest,
            '無聲音軌 %.1f s 比最長的譜面 %.1f s 短，歌會提早結束'
            % (dur_ms / 1000.0, longest / 1000.0))

    def test_the_original_difficulty_is_185_bpm(self):
        d = next(x for x in self.diffs if '185' in x['difficultyName'])
        self.assertAlmostEqual(self._chart(d).bpm, 185.0, places=3)
