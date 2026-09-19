"""批量匯入與獨立更新：掃資料夾一次匯入多首；製譜器／遊戲／曲庫各自換新版。"""

import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import song_library as L  # noqa: E402
from qt_editor import updates  # noqa: E402
from test_song_library import chart, make_library, write_wav  # noqa: E402


def make_song(path: str, folder: str, title: str = '') -> str:
    """一個最小的樂曲資料夾：register.json ＋ 一份譜 ＋ 一個音檔。"""
    os.makedirs(os.path.join(path, 'Real'), exist_ok=True)
    chart(os.path.join(path, 'Real', folder + '.json'))
    write_wav(os.path.join(path, folder + '.wav'))
    with io.open(os.path.join(path, 'register.json'), 'w', encoding='utf-8') as fh:
        json.dump({'displayName': title or (folder + ' Song'), 'author': 'Someone',
                   'difficulties': [{'difficultyName': 'Real', 'difficultyLevel': 12,
                                     'chartFileName': 'songs/%s/Real/%s' % (folder, folder),
                                     'audioResourcePath': 'songs/%s/%s' % (folder, folder)}]},
                  fh, ensure_ascii=False)
    return path


def zip_folder(folder: str, path: str, prefix: str = '') -> str:
    with zipfile.ZipFile(path, 'w') as archive:
        for base, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(base, name)
                archive.write(full, os.path.join(prefix, os.path.relpath(full, folder)))
    return path


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_song_folders_at_any_depth(self):
        make_song(os.path.join(self.tmp, 'pack', 'Alpha'), 'Alpha')
        make_song(os.path.join(self.tmp, 'Beta'), 'Beta')
        found = sorted(os.path.basename(p) for p in L.find_song_folders(self.tmp))
        self.assertEqual(found, ['Alpha', 'Beta'])

    def test_a_song_folder_itself_scans_as_one(self):
        song = os.path.join(self.tmp, 'Alpha')
        make_song(song, 'Alpha')
        self.assertEqual(L.find_song_folders(song), [os.path.abspath(song)])

    def test_finds_packages(self):
        os.makedirs(os.path.join(self.tmp, 'zips'))
        for name in ('b.zip', 'a.zip'):
            with zipfile.ZipFile(os.path.join(self.tmp, 'zips', name), 'w') as z:
                z.writestr('song.json', '{}')
        self.assertEqual([os.path.basename(p) for p in L.find_packages(self.tmp)],
                         ['a.zip', 'b.zip'])

    def test_the_editor_workspace_is_skipped(self):
        root = make_library(self.tmp)
        trash = os.path.join(self.tmp, 'UserSongs_editor', 'trash', '20260101-000000_Gone')
        make_song(trash, 'Gone')
        found = [os.path.basename(p) for p in L.find_song_folders(self.tmp)]
        self.assertNotIn('Gone', found)
        self.assertIn('Alpha', found)
        self.assertTrue(os.path.isdir(root))


class BatchImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self.lib = L.SongLibrary(self.root)
        self.source = os.path.join(self.tmp, 'incoming')
        for name in ('Gamma', 'Delta'):
            make_song(os.path.join(self.source, name), name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self, category='Arcaea'):
        return [('folder', os.path.join(self.source, name), category)
                for name in ('Gamma', 'Delta')]

    def test_imports_everything_and_sets_categories(self):
        done, failed = self.lib.import_many(self.items())
        self.assertEqual(sorted(done), ['Delta', 'Gamma'])
        self.assertEqual(failed, [])
        songs = {s.folder: s for s in self.lib.songs()}
        self.assertEqual(songs['Gamma'].category, 'Arcaea')
        self.assertIn('Arcaea', self.lib.categories())

    def test_progress_can_cancel(self):
        seen = []

        def step(done, total, name):
            seen.append(name)
            return done < 1
        done, _failed = self.lib.import_many(self.items(), progress=step)
        self.assertEqual(len(done), 1)
        self.assertGreaterEqual(len(seen), 2)

    def test_one_undo_takes_the_whole_batch_back(self):
        self.lib.import_many(self.items())
        self.lib.undo_last()
        folders = {s.folder for s in self.lib.songs()}
        self.assertNotIn('Gamma', folders)
        self.assertNotIn('Delta', folders)
        self.assertIn('Alpha', folders)

    def test_failures_are_reported_and_the_rest_still_import(self):
        items = self.items() + [('folder', os.path.join(self.tmp, 'nope'), 'Other')]
        done, failed = self.lib.import_many(items)
        self.assertEqual(len(done), 2)
        self.assertEqual(len(failed), 1)

    def test_the_dialog_lists_folders_and_zips(self):
        from qt_editor.batch_import_dialog import BatchImportDialog
        with zipfile.ZipFile(os.path.join(self.source, 'pack.zip'), 'w') as z:
            z.writestr('song.json', '{}')
        dlg = BatchImportDialog(None, self.lib, self.source)
        try:
            self.assertEqual(dlg.table.rowCount(), 3)
            from PyQt5.QtCore import Qt
            kinds = {dlg.table.item(r, 0).data(Qt.UserRole)[0] for r in range(3)}
            self.assertEqual(kinds, {'folder', 'zip'})
            dlg.bulk_category.setCurrentText('Deemo')
            dlg._apply_category()
            self.assertTrue(all(c == 'Deemo' for _k, _p, c in dlg.items()))
            dlg._check_all(False)
            self.assertEqual(dlg.items(), [])
            self.assertFalse(dlg._ok.isEnabled())
        finally:
            dlg.close()

    def test_only_new_skips_songs_already_in_the_library(self):
        from PyQt5.QtCore import Qt
        from qt_editor.batch_import_dialog import BatchImportDialog
        make_song(os.path.join(self.source, 'Alpha'), 'Alpha')      # 曲庫裡已經有 Alpha
        dlg = BatchImportDialog(None, self.lib, self.source)
        try:
            dlg._check_new()
            picked = {os.path.basename(p) for _k, p, _c in dlg.items()}
            self.assertEqual(picked, {'Gamma', 'Delta'})
            row = [r for r in range(dlg.table.rowCount())
                   if dlg.table.item(r, 0).text() == 'Alpha'][0]
            self.assertEqual(dlg.table.item(row, 0).checkState(), Qt.Unchecked)
            self.assertTrue(dlg.table.item(row, 3).text())
        finally:
            dlg.close()


class LibraryUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self.lib = L.SongLibrary(self.root)
        self.new = os.path.join(self.tmp, 'newlib')
        make_song(os.path.join(self.new, 'Alpha'), 'Alpha', title='新版 Alpha')
        make_song(os.path.join(self.new, 'Gamma'), 'Gamma')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def titles(self):
        return {s.folder: s.title for s in self.lib.songs()}

    def test_same_name_is_replaced_and_the_rest_kept(self):
        done, failed = self.lib.merge_from(self.new)
        self.assertEqual(sorted(done), ['Alpha', 'Gamma'])
        self.assertEqual(failed, [])
        titles = self.titles()
        self.assertEqual(titles['Alpha'], '新版 Alpha')
        self.assertIn('Beta', titles, '沒在新版裡的曲目要留著')

    def test_categories_of_existing_songs_survive(self):
        self.lib.update_song('Alpha', 'Alpha Song', 'Someone', ['Deemo'])
        self.lib.merge_from(self.new)
        self.assertEqual(self.lib.song('Alpha').categories, ['Deemo'])

    def test_undo_removes_only_the_songs_that_were_added(self):
        self.lib.merge_from(self.new)
        self.lib.undo_last()
        folders = {s.folder for s in self.lib.songs()}
        self.assertNotIn('Gamma', folders)
        self.assertIn('Alpha', folders)

    def test_a_zip_works_too(self):
        path = zip_folder(self.new, os.path.join(self.tmp, 'lib.zip'), prefix='UserSongs')
        done, _failed = updates.update_library(path, self.lib)
        self.assertEqual(sorted(done), ['Alpha', 'Gamma'])
        self.assertEqual(self.titles()['Alpha'], '新版 Alpha')

    def test_an_empty_folder_is_refused(self):
        empty = os.path.join(self.tmp, 'empty')
        os.makedirs(empty)
        with self.assertRaises(L.LibraryError):
            self.lib.merge_from(empty)


class EditorUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, 'Nos-clonev2.0')
        os.makedirs(self.game)
        self.current = os.path.join(self.game, 'Nos Chart Maker 7.7.exe')
        io.open(self.current, 'w').write('old')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_exe(self, name='Nos Chart Maker 7.8.exe'):
        folder = os.path.join(self.tmp, 'download')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        io.open(path, 'w').write('new')
        return path

    def test_the_new_exe_lands_next_to_the_old_one(self):
        dest = updates.update_editor(self.new_exe(), self.current)
        self.assertEqual(dest, os.path.join(self.game, 'Nos Chart Maker 7.8.exe'))
        self.assertEqual(io.open(dest).read(), 'new')

    def test_the_old_exe_is_kept_under_another_name(self):
        updates.update_editor(self.new_exe(), self.current)
        self.assertFalse(os.path.exists(self.current))
        old = [n for n in os.listdir(self.game) if '.old-' in n]
        self.assertEqual(len(old), 1)
        self.assertEqual(io.open(os.path.join(self.game, old[0])).read(), 'old')

    def test_a_zip_is_unpacked(self):
        folder = os.path.join(self.tmp, 'pack')
        os.makedirs(folder)
        io.open(os.path.join(folder, 'Nos Chart Maker 8.0.exe'), 'w').write('zipped')
        path = zip_folder(folder, os.path.join(self.tmp, 'editor.zip'), prefix='editor')
        dest = updates.update_editor(path, self.current)
        self.assertEqual(os.path.basename(dest), 'Nos Chart Maker 8.0.exe')
        self.assertEqual(io.open(dest).read(), 'zipped')

    def test_a_zip_without_an_editor_is_refused(self):
        folder = os.path.join(self.tmp, 'junk')
        os.makedirs(folder)
        io.open(os.path.join(folder, 'readme.txt'), 'w').write('x')
        path = zip_folder(folder, os.path.join(self.tmp, 'junk.zip'))
        with self.assertRaises(updates.UpdateError):
            updates.update_editor(path, self.current)


class GameUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, 'Nos-clonev2.0')
        os.makedirs(os.path.join(self.game, 'Nos-clonev1_Data'))
        io.open(os.path.join(self.game, 'Nos-clonev1.exe'), 'w').write('old exe')
        io.open(os.path.join(self.game, 'Nos-clonev1_Data', 'data'), 'w').write('old data')
        self.root = make_library(self.game)                     # <game>/UserSongs
        io.open(os.path.join(self.game, 'Nos Chart Maker 7.7.exe'), 'w').write('editor')
        io.open(os.path.join(self.game, 'nosmania_launcher.json'), 'w').write('{}')

        self.build = os.path.join(self.tmp, 'build', 'Nos-clonev2.1')
        os.makedirs(os.path.join(self.build, 'Nos-clonev1_Data'))
        io.open(os.path.join(self.build, 'Nos-clonev1.exe'), 'w').write('new exe')
        io.open(os.path.join(self.build, 'Nos-clonev1_Data', 'data'), 'w').write('new data')
        io.open(os.path.join(self.build, 'UnityPlayer.dll'), 'w').write('dll')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_program_files_are_replaced(self):
        copied, kept = updates.update_game(self.build, self.game)
        self.assertEqual(copied, 3)
        self.assertEqual(io.open(os.path.join(self.game, 'Nos-clonev1.exe')).read(), 'new exe')
        self.assertEqual(io.open(os.path.join(self.game, 'Nos-clonev1_Data', 'data')).read(),
                         'new data')
        self.assertTrue(os.path.isfile(os.path.join(self.game, 'UnityPlayer.dll')))
        self.assertIn('UserSongs', kept)

    def test_the_library_and_the_editor_are_left_alone(self):
        updates.update_game(self.build, self.game)
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'library.json')))
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'Alpha')))
        self.assertEqual(io.open(os.path.join(self.game, 'Nos Chart Maker 7.7.exe')).read(),
                         'editor')
        self.assertTrue(os.path.isfile(os.path.join(self.game, 'nosmania_launcher.json')))

    def test_a_build_inside_a_zip_is_found(self):
        path = zip_folder(self.build, os.path.join(self.tmp, 'build.zip'), prefix='Nos-clonev2.1')
        copied, _kept = updates.update_game(path, self.game)
        self.assertEqual(copied, 3)
        self.assertEqual(io.open(os.path.join(self.game, 'Nos-clonev1.exe')).read(), 'new exe')

    def test_something_that_is_not_a_build_is_refused(self):
        junk = os.path.join(self.tmp, 'junk')
        os.makedirs(junk)
        io.open(os.path.join(junk, 'readme.txt'), 'w').write('x')
        with self.assertRaises(updates.UpdateError):
            updates.update_game(junk, self.game)


class LauncherUpdateMenuTests(unittest.TestCase):
    """首頁的「更新 ▾」接到對的地方，而且會擋掉遊戲開著的情況。"""

    def setUp(self):
        from qt_editor.launcher import LauncherWindow
        from qt_editor.settings import settings
        self.settings = settings
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self.game_dir = os.path.join(self.tmp, 'Game')
        os.makedirs(os.path.join(self.game_dir, 'Game_Data'))
        self.exe = os.path.join(self.game_dir, 'Game.exe')
        io.open(self.exe, 'w').write('game')
        self._saved = {k: settings._data.get(k)
                       for k in ('song_library_root', 'game_exe_path', 'editor_exe_path')}
        settings._data.update(song_library_root=self.root, game_exe_path=self.exe,
                              editor_exe_path='')
        self._patches = [mock.patch.object(settings, 'save'),
                         mock.patch('qt_editor.library_manager.game_running', return_value=False)]
        for p in self._patches:
            p.start()
        self.home = LauncherWindow()

    def tearDown(self):
        self.home.close()
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            self.settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_menu_has_three_updates(self):
        texts = [a.text() for a in self._menu().actions()]
        self.assertEqual(len(texts), 3)
        self.assertTrue(any('製譜器' in t for t in texts))
        self.assertTrue(any('遊戲' in t for t in texts))
        self.assertTrue(any('曲庫' in t for t in texts))

    def _menu(self):
        return self.home._update_menu

    def test_updating_the_game_is_blocked_while_it_runs(self):
        with mock.patch('qt_editor.library_manager.game_running', return_value=True), \
                mock.patch('qt_editor.launcher.QMessageBox.information') as box, \
                mock.patch.object(self.home, '_pick_update_source') as pick:
            self.home.update_game()
        self.assertTrue(box.called)
        self.assertFalse(pick.called, '連檔案都不用選')

    def test_updating_the_editor_remembers_the_new_exe(self):
        new = os.path.join(self.tmp, 'Nos Chart Maker 9.9.exe')
        io.open(new, 'w').write('new')
        old = os.path.join(self.game_dir, 'Nos Chart Maker 7.7.exe')
        io.open(old, 'w').write('old')
        self.settings._data['editor_exe_path'] = old
        with mock.patch.object(self.home, '_pick_update_source', return_value=new), \
                mock.patch('qt_editor.launcher.QMessageBox.information'):
            self.home.update_editor()
        dest = os.path.join(self.game_dir, 'Nos Chart Maker 9.9.exe')
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(self.settings.get('editor_exe_path'), dest)

    def test_updating_the_library_merges(self):
        source = os.path.join(self.tmp, 'newlib')
        make_song(os.path.join(source, 'Alpha'), 'Alpha', title='新版 Alpha')
        manager = self.home.library_manager()
        manager.set_root(self.root)
        with mock.patch.object(self.home, '_pick_update_source', return_value=source), \
                mock.patch('qt_editor.launcher.QMessageBox.information'):
            self.home.update_library()
        self.assertEqual(manager.lib.song('Alpha').title, '新版 Alpha')


class ZipNameTests(unittest.TestCase):
    def test_zip_names_stay_ascii(self):
        from qt_editor.hiraeth_export import ascii_file_name
        self.assertEqual(ascii_file_name('Conflict (VILA Remix)'), 'Conflict_VILA_Remix')
        self.assertEqual(ascii_file_name('L10: Largo'), 'L10_Largo')
        self.assertEqual(ascii_file_name('遠航星 - Voyaging Star'), 'Voyaging_Star')
        self.assertEqual(ascii_file_name('彩云追月'), '')

    def test_a_cjk_only_title_falls_back_to_the_package_id(self):
        import tempfile as tf
        from qt_editor import hiraeth_export as H
        from test_hiraeth_export import fake_renderer, make_song as make_hiraeth_song
        root = tf.mkdtemp()
        try:
            song = make_hiraeth_song(root)
            plans, _ = H.plan_song_folder(song)
            result = H.build_package(plans[0], os.path.join(root, 'out'),
                                     version=7, renderer=fake_renderer)
            self.assertEqual(result.error, '')
            name = os.path.basename(result.zip_path)
            self.assertTrue(name.isascii(), name)
            self.assertTrue(name.startswith('song_'), name)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()


class InstallGameTests(unittest.TestCase):
    """從 ZIP 開啟遊戲：裝起來之後要自己認出遊戲、製譜器、曲庫。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.build = os.path.join(self.tmp, 'src', 'Nos-clonev2.0')
        os.makedirs(os.path.join(self.build, 'Nos-clonev1_Data'))
        io.open(os.path.join(self.build, 'Nos-clonev1.exe'), 'w').write('game')
        io.open(os.path.join(self.build, 'Nos-clonev1_Data', 'data'), 'w').write('data')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, name='game.zip'):
        path = zip_folder(self.build, os.path.join(self.tmp, name), prefix='Nos-clonev2.0')
        dest = os.path.join(self.tmp, 'installed')
        return updates.install_game(path, dest)

    def test_a_game_only_zip(self):
        found = self.install()
        self.assertTrue(found['game'].endswith('Nos-clonev1.exe'))
        self.assertTrue(os.path.isfile(found['game']))
        self.assertEqual(found['editor'], '')
        self.assertEqual(found['library'], '')

    def test_an_editor_inside_is_detected(self):
        io.open(os.path.join(self.build, 'Nos Chart Maker 8.1.exe'), 'w').write('editor')
        found = self.install('with_editor.zip')
        self.assertTrue(found['editor'].endswith('Nos Chart Maker 8.1.exe'))

    def test_a_library_inside_is_detected(self):
        make_library(self.build)                       # <build>/UserSongs
        found = self.install('with_songs.zip')
        self.assertTrue(found['library'].endswith('UserSongs'))
        self.assertTrue(os.path.isfile(os.path.join(found['library'], 'library.json')))

    def test_a_folder_source_works_too(self):
        found = updates.install_game(self.build, os.path.join(self.tmp, 'copied'))
        self.assertTrue(os.path.isfile(found['game']))

    def test_something_that_is_not_a_game_is_refused(self):
        junk = os.path.join(self.tmp, 'junk')
        os.makedirs(junk)
        io.open(os.path.join(junk, 'readme.txt'), 'w').write('x')
        path = zip_folder(junk, os.path.join(self.tmp, 'junk.zip'))
        with self.assertRaises(updates.UpdateError):
            updates.install_game(path, os.path.join(self.tmp, 'nope'))


class FindGamePackageTests(unittest.TestCase):
    """NosMania 旁邊放著還沒解壓的遊戲 ZIP，要自己找到（不要只說「找不到遊戲」）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.build = os.path.join(self.tmp, 'src', 'Nos-clonev2.0')
        os.makedirs(os.path.join(self.build, 'Nos-clonev1_Data'))
        io.open(os.path.join(self.build, 'Nos-clonev1.exe'), 'w').write('game')
        io.open(os.path.join(self.build, 'Nos-clonev1_Data', 'data'), 'w').write('data')
        self.home = os.path.join(self.tmp, 'nos-mania')
        os.makedirs(self.home)
        self.game_zip = zip_folder(self.build, os.path.join(self.home, 'Nos-clonev2.0_clean.zip'),
                                   prefix='Nos-clonev2.0')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_game_zip_is_recognised(self):
        self.assertTrue(L.is_game_package(self.game_zip))

    def test_a_library_zip_is_not_mistaken_for_a_game(self):
        library = make_library(os.path.join(self.tmp, 'lib'))
        songs_zip = zip_folder(library, os.path.join(self.home, 'songs.zip'), prefix='UserSongs')
        self.assertFalse(L.is_game_package(songs_zip))
        self.assertEqual(L.find_game_packages(self.home), [self.game_zip])

    def test_packages_one_level_down_are_found_too(self):
        deeper = os.path.join(self.home, 'downloads')
        os.makedirs(deeper)
        moved = shutil.move(self.game_zip, os.path.join(deeper, 'game.zip'))
        self.assertEqual(L.find_game_packages(self.home), [moved])

    def test_the_picker_says_a_package_is_waiting(self):
        from qt_editor.launcher import GamePicker
        from qt_editor.settings import settings
        saved = settings._data.get('game_exe_path')
        try:
            settings._data['game_exe_path'] = ''
            with mock.patch.object(settings, 'save'), \
                    mock.patch('qt_editor.song_library.app_dir', return_value=self.home), \
                    mock.patch('qt_editor.library_manager.game_running', return_value=False):
                picker = GamePicker(None)
                try:
                    self.assertEqual(picker.found_package(), self.game_zip)
                    self.assertIn('Nos-clonev2.0_clean.zip',
                                  picker.rows['clone']['label'].text())
                    self.assertIn('Nos-clonev2.0_clean.zip', picker.install_btn.text())
                finally:
                    picker.close()
        finally:
            settings._data['game_exe_path'] = saved if saved is not None else ''
