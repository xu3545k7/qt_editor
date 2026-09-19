"""曲庫工具：備份要收哪些檔案、還原要認得哪些資料夾、補難度的入口接得上。"""

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import library_tools as T  # noqa: E402
from test_song_library import make_library  # noqa: E402


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.root = make_library(self.temp)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _write(self, rel, text='x'):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_collects_songs_and_index(self):
        rels = {rel for _full, rel in T._collect(self.root)}
        self.assertIn('library.json', rels)
        self.assertTrue(any(rel.endswith('register.json') for rel in rels))

    def test_skips_editor_scratch_and_temp_files(self):
        self._write(os.path.join('trash', 'old', 'register.json'))
        self._write(os.path.join('history', '20240101', 'library.json'))
        self._write('notes.tmp')
        self._write('run.log')
        rels = {rel for _full, rel in T._collect(self.root)}
        self.assertFalse([r for r in rels if r.startswith(('trash', 'history'))])
        self.assertNotIn('notes.tmp', rels)
        self.assertNotIn('run.log', rels)

    def test_never_packs_the_archive_into_itself(self):
        inside = self._write('backup.zip')
        rels = {rel for _full, rel in T._collect(self.root, skip=os.path.abspath(inside))}
        self.assertNotIn('backup.zip', rels)


class SongFolderTests(unittest.TestCase):
    def test_finds_song_folders_at_any_depth(self):
        temp = tempfile.mkdtemp()
        try:
            for rel in (os.path.join('UserSongs', 'A'), os.path.join('UserSongs', 'B')):
                folder = os.path.join(temp, rel)
                os.makedirs(folder)
                with open(os.path.join(folder, 'register.json'), 'w', encoding='utf-8') as fh:
                    fh.write('{}')
            # 樂曲資料夾裡面的子資料夾不該被當成另一首
            nested = os.path.join(temp, 'UserSongs', 'A', 'Real')
            os.makedirs(nested)
            with open(os.path.join(nested, 'register.json'), 'w', encoding='utf-8') as fh:
                fh.write('{}')
            found = [os.path.basename(p) for p in T._song_folders(temp)]
            self.assertEqual(found, ['A', 'B'])
        finally:
            shutil.rmtree(temp, ignore_errors=True)


class RoundTripTests(unittest.TestCase):
    """備份打出來的 ZIP，還原那一頭要認得。"""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.root = make_library(self.temp)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_backup_zip_can_be_read_back(self):
        out = os.path.join(tempfile.mkdtemp(), 'lib.zip')
        files = T._collect(self.root)
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            for full, rel in files:
                zf.write(full, os.path.join(os.path.basename(self.root), rel))

        temp = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(out) as zf:
                zf.extractall(temp)
            songs = T._song_folders(temp)
            self.assertTrue(songs, '備份裡應該找得到樂曲資料夾')
            for folder in songs:
                self.assertTrue(os.path.isfile(os.path.join(folder, 'register.json')))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            shutil.rmtree(os.path.dirname(out), ignore_errors=True)


class EntryPointTests(unittest.TestCase):
    def test_fill_difficulties_opens_the_scan_dialog(self):
        with mock.patch('qt_editor.difficulty_dialog.LibraryFillDialog') as dlg:
            dlg.return_value.exec_.return_value = 0        # 使用者按取消
            T.fill_difficulties(None, 'C:/nowhere')
        dlg.assert_called_once()



class CleanExportTests(unittest.TestCase):
    """乾淨版：只留新手教學，索引要跟著重寫。"""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.game = os.path.join(self.temp, 'Game')
        self.root = os.path.join(self.game, 'UserSongs')
        os.makedirs(self.root)
        os.makedirs(os.path.join(self.game, 'Game_Data', 'StreamingAssets', 'BuiltInSong'))
        os.makedirs(os.path.join(self.game, 'Game_BurstDebugInformation_DoNotShip'))
        os.makedirs(os.path.join(self.temp, 'UserSongs_editor', 'trash'))
        with open(os.path.join(self.game, 'Game.exe'), 'wb') as fh:
            fh.write(b'MZ')
        with open(os.path.join(self.game, 'Game_Data', 'StreamingAssets',
                               'BuiltInSong', 'a.wav'), 'wb') as fh:
            fh.write(b'\0')
        with open(os.path.join(self.game, 'Game_BurstDebugInformation_DoNotShip', 'x.txt'),
                  'w', encoding='utf-8') as fh:
            fh.write('x')

        for folder, category in (('新手教學初階00', '新手教學'), ('Alpha', 'POPS')):
            song = os.path.join(self.root, folder)
            os.makedirs(song)
            with open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as fh:
                json.dump({'displayName': folder, 'difficulties': []}, fh)
        with open(os.path.join(self.root, 'library.json'), 'w', encoding='utf-8') as fh:
            json.dump({'songs': [
                {'folderName': '新手教學初階00', 'category': '新手教學', 'categories': ['新手教學']},
                {'folderName': 'Alpha', 'category': 'POPS', 'categories': ['POPS']}],
                'categories': ['新手教學', 'POPS']}, fh)
        with open(os.path.join(self.root, 'songlist.json'), 'w', encoding='utf-8') as fh:
            json.dump({'categories': {'新手教學': ['新手教學初階00'], 'POPS': ['Alpha']}}, fh)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_only_tutorials_and_no_shipping_junk(self):
        files, dropped = T._collect_clean(self.game, {'新手教學初階00'})
        rels = {rel.replace(os.sep, '/') for _full, rel in files}
        self.assertIn('Game.exe', rels)
        self.assertIn('UserSongs/新手教學初階00/register.json', rels)
        self.assertFalse([r for r in rels if r.startswith('UserSongs/Alpha')], '別人的曲庫不能跟著走')
        self.assertFalse([r for r in rels if 'StreamingAssets' in r], '內附曲目也是曲目')
        self.assertFalse([r for r in rels if 'DoNotShip' in r], 'Unity 自己說不要出貨')
        self.assertFalse([r for r in rels if r.endswith(('library.json', 'songlist.json'))],
                         '索引是重寫的，不照抄')
        self.assertEqual(dropped, 2, 'Alpha 和內附那首')

    def test_personal_files_are_left_behind(self):
        # 這台電腦的東西：製譜器設定（本機路徑）、操作紀錄、啟動器語言檔、曲庫變動記號
        os.makedirs(os.path.join(self.game, 'logs'))
        for rel, text in (('settings.json', '{"song_library_root": "D:/me"}'),
                          ('logs/oplog-20260916.jsonl', '{}'),
                          ('nosmania_launcher.json', '{}'),
                          ('UserSongs/.editor_revision.json', '{}')):
            with open(os.path.join(self.game, rel), 'w', encoding='utf-8') as fh:
                fh.write(text)
        files, _dropped = T._collect_clean(self.game, {'新手教學初階00'})
        rels = {rel.replace(os.sep, '/') for _full, rel in files}
        for rel in ('settings.json', 'logs/oplog-20260916.jsonl', 'nosmania_launcher.json',
                    'UserSongs/.editor_revision.json'):
            self.assertNotIn(rel, rels)
        self.assertIn('Game.exe', rels)

    def test_the_index_is_rewritten_not_copied(self):
        out = T._pruned_index(self.root, {'新手教學初階00'})
        index = json.loads(out[os.path.join('UserSongs', 'library.json')])
        self.assertEqual([s['folderName'] for s in index['songs']], ['新手教學初階00'])
        self.assertEqual(index['categories'], ['新手教學'])
        songlist = json.loads(out[os.path.join('UserSongs', 'songlist.json')])
        self.assertEqual(songlist['categories'], {'新手教學': ['新手教學初階00']})


if __name__ == '__main__':
    unittest.main()
