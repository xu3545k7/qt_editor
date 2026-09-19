"""曲庫管理的資料層：讀寫要照遊戲 ExternalSongLibrary 的規則，刪除可以復原。"""

import json
import os
import shutil
import tempfile
import unittest
import wave
import zipfile

from qt_editor.models import GNote, NoteModel
from qt_editor.song_library import LibraryError, SongLibrary


def write_wav(path, seconds=2):
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b'\0\0\0\0' * int(22050 * seconds))


def chart(path, count=4):
    m = NoteModel.create_new('t', 120.0, 5.0, 4)
    notes = []
    for i in range(count):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 300, i * 300 + 100, 100
        n.pitch, n.hand, n.note_type = 60 + i, 0, 0
        n.min_key, n.max_key = 3, 5
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    m.save_json(path)


def make_library(root):
    lib = os.path.join(root, 'UserSongs')
    os.makedirs(lib)
    for folder, title, category in (('Alpha', 'Alpha Song', 'Deemo'), ('Beta', 'Beta Song', 'POPS')):
        song = os.path.join(lib, folder)
        os.makedirs(os.path.join(song, 'Real'))
        chart(os.path.join(song, 'Real', folder + '.json'))
        write_wav(os.path.join(song, folder + '.wav'))
        with open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as fh:
            json.dump({'displayName': title, 'author': 'Someone', 'difficulties': [
                {'difficultyName': 'Real', 'difficultyLevel': 12,
                 'chartFileName': 'songs/%s/Real/%s' % (folder, folder),
                 'audioResourcePath': 'songs/%s/%s' % (folder, folder)},
                {'difficultyName': 'Hard', 'difficultyLevel': 7,
                 'chartFileName': 'songs/%s/Real/%s' % (folder, folder),
                 'audioResourcePath': 'songs/%s/%s' % (folder, folder)}]}, fh)
    with open(os.path.join(lib, 'library.json'), 'w', encoding='utf-8') as fh:
        json.dump({'songs': [{'id': 'portable:Alpha', 'folderName': 'Alpha', 'category': 'Deemo',
                              'categories': ['Deemo']}], 'categories': ['Deemo']}, fh)
    with open(os.path.join(lib, 'songlist.json'), 'w', encoding='utf-8') as fh:
        json.dump({'categories': {'Deemo': ['Alpha'], 'POPS': ['Beta']}}, fh)
    return lib


class SongLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self.lib = SongLibrary(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, name):
        with open(os.path.join(self.root, name), encoding='utf-8') as fh:
            return json.load(fh)

    def test_lists_songs_like_the_game(self):
        songs = {s.folder: s for s in self.lib.songs()}
        self.assertEqual(set(songs), {'Alpha', 'Beta'})
        self.assertEqual(songs['Alpha'].categories, ['Deemo'])
        self.assertTrue(songs['Alpha'].registered)
        # 沒登記在 library.json 的資料夾，分類照 songlist.json（遊戲也是這樣補）
        self.assertEqual(songs['Beta'].categories, ['POPS'])
        self.assertFalse(songs['Beta'].registered)
        self.assertEqual([d.name for d in songs['Alpha'].difficulties], ['Real', 'Hard'])
        self.assertEqual(songs['Alpha'].problems, [])

    def test_missing_files_are_reported(self):
        os.remove(os.path.join(self.root, 'Alpha', 'Alpha.wav'))
        song = self.lib.song('Alpha')
        self.assertTrue(any('音訊' in p for p in song.problems))

    def test_update_song_writes_register_and_both_indexes(self):
        self.lib.update_song('Beta', 'Beta 新名字', '作者', ['Arcaea'], slogan='一句話')
        with open(os.path.join(self.root, 'Beta', 'register.json'), encoding='utf-8') as fh:
            register = json.load(fh)
        self.assertEqual((register['displayName'], register['author'], register['slogan']),
                         ('Beta 新名字', '作者', '一句話'))
        index = self.read('library.json')
        entry = next(e for e in index['songs'] if e['folderName'] == 'Beta')
        self.assertEqual((entry['id'], entry['category'], entry['categories']),
                         ('portable:Beta', 'Arcaea', ['Arcaea']))
        self.assertIn('Arcaea', index['categories'])
        songlist = self.read('songlist.json')['categories']
        self.assertIn('Beta', songlist['Arcaea'])
        self.assertNotIn('Beta', songlist['POPS'])

    def test_all_is_not_a_real_category(self):
        self.lib.update_song('Alpha', 'Alpha Song', 'Someone', ['ALL'])
        self.assertEqual(self.lib.song('Alpha').categories, ['Other'])

    def test_difficulty_edits(self):
        self.lib.update_difficulty('Alpha', 1, 'Expert', 10.5)
        diffs = self.lib.song('Alpha').difficulties
        self.assertEqual((diffs[1].name, diffs[1].level), ('Expert', 10.5))
        with self.assertRaises(LibraryError):
            self.lib.update_difficulty('Alpha', 1, 'Real', 10)       # 重名
        self.assertEqual(self.lib.move_difficulty('Alpha', 1, -1), 0)
        self.assertEqual([d.name for d in self.lib.song('Alpha').difficulties], ['Expert', 'Real'])
        self.lib.delete_difficulty('Alpha', 0)
        self.assertEqual([d.name for d in self.lib.song('Alpha').difficulties], ['Real'])
        with self.assertRaises(LibraryError):
            self.lib.delete_difficulty('Alpha', 0)                    # 至少留一個

    def test_delete_goes_to_trash_and_undo_brings_it_back(self):
        trash = self.lib.delete_song('Alpha')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'Alpha')))
        self.assertTrue(os.path.isdir(trash))
        inside = os.path.normcase(os.path.abspath(trash)).startswith(
            os.path.normcase(os.path.abspath(self.root)) + os.sep)
        self.assertFalse(inside, '回收區不能在曲庫裡面（打包遊戲時會被一起複製）')
        self.assertFalse(any(e['folderName'] == 'Alpha' for e in self.read('library.json')['songs']))
        self.lib.undo_last()
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'Alpha', 'register.json')))
        self.assertTrue(any(e['folderName'] == 'Alpha' for e in self.read('library.json')['songs']))

    def test_undo_restores_register(self):
        self.lib.update_song('Alpha', '改掉', 'x', ['Deemo'])
        self.lib.undo_last()
        self.assertEqual(self.lib.song('Alpha').title, 'Alpha Song')
        with self.assertRaises(LibraryError):
            self.lib.undo_last()

    def test_every_change_bumps_the_revision_for_the_game(self):
        self.lib.update_song('Alpha', 'A', 'x', ['Deemo'])
        first = self.read('.editor_revision.json')
        self.lib.update_difficulty('Alpha', 0, 'Real', 13)
        second = self.read('.editor_revision.json')
        self.assertEqual(second['revision'], first['revision'] + 1)
        self.assertEqual(second['id'], 'portable:Alpha')

    def test_import_folder_renames_paths_when_the_name_is_taken(self):
        source = os.path.join(self.tmp, 'outside', 'Alpha')
        shutil.copytree(os.path.join(self.root, 'Alpha'), source)
        folder = self.lib.import_folder(source, 'Classical')
        self.assertEqual(folder, 'Alpha (2)')
        song = self.lib.song(folder)
        self.assertEqual(song.problems, [], '資源路徑要跟著新資料夾名稱')
        self.assertEqual(song.categories, ['Classical'])
        self.lib.undo_last()
        self.assertFalse(os.path.exists(os.path.join(self.root, 'Alpha (2)')))

    def test_import_hiraeth_zip(self):
        from qt_editor import hiraeth_export as H
        plans, _ = H.plan_song_folder(os.path.join(self.root, 'Alpha'))
        result = H.build_package(plans[0], os.path.join(self.tmp, 'zips'),
                                 renderer=lambda _m: (b'\0' * 4 * 44100, 44100))
        self.assertEqual(result.error, '')
        folder = self.lib.import_hiraeth_zip(result.zip_path, 'Other')
        song = self.lib.song(folder)
        self.assertEqual(song.problems, [])
        self.assertEqual({d.name: d.level for d in song.difficulties}, {'Hard': 7, 'Real': 12})

    def test_add_category(self):
        self.lib.add_category('新分類')
        self.assertIn('新分類', self.lib.categories())
        for bad in ('', 'ALL', 'a/b'):
            with self.assertRaises(LibraryError):
                self.lib.add_category(bad)


if __name__ == '__main__':
    unittest.main()
