"""遊戲、曲庫、製譜器、啟動器分開放：啟動器記住三個位置去連結。"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import song_library as L  # noqa: E402
from qt_editor import updates  # noqa: E402
from qt_editor.settings import settings  # noqa: E402
from test_song_library import make_library  # noqa: E402

KEYS = ('song_library_root', 'game_exe_path', 'editor_exe_path')


def _game(folder):
    os.makedirs(os.path.join(folder, 'Nos-clonev1_Data'))
    exe = os.path.join(folder, 'Nos-clonev1.exe')
    open(exe, 'wb').close()
    return exe


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: settings._data.get(k) for k in KEYS}
        settings._data.update({k: '' for k in KEYS})
        self._save = mock.patch.object(settings, 'save')
        self._save.start()

    def tearDown(self):
        self._save.stop()
        for k, v in self._saved.items():
            settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def frozen(self, folder):
        return mock.patch.multiple(sys, create=True, frozen=True,
                                   executable=os.path.join(folder, 'NosMania.exe'))

    def test_saved_locations_win_over_nearby(self):
        near = os.path.join(self.tmp, 'launcher')
        near_game = _game(os.path.join(near, 'Nos-clonev2.0'))
        os.makedirs(os.path.join(near, 'UserSongs'))
        far_game = _game(os.path.join(self.tmp, 'Games', 'Nos'))
        far_lib = make_library(os.path.join(self.tmp, 'Songs'))
        far_editor = os.path.join(self.tmp, 'Tools', 'Nos Chart Maker 9.exe')
        os.makedirs(os.path.dirname(far_editor))
        open(far_editor, 'wb').close()
        with self.frozen(near):
            self.assertEqual(L.configured_game_exe(), near_game)
            self.assertEqual(L.default_root(), os.path.join(near, 'UserSongs'))
            settings._data.update(game_exe_path=far_game, song_library_root=far_lib,
                                  editor_exe_path=far_editor)
            self.assertEqual(L.configured_game_exe(), far_game)
            self.assertEqual(L.default_root(), far_lib)
            self.assertEqual(L.configured_editor_exe(), far_editor)
            # 指定的位置不見了就退回附近找到的
            shutil.rmtree(os.path.dirname(far_game))
            self.assertEqual(L.configured_game_exe(), near_game)

    def test_all_four_in_one_folder(self):
        base = os.path.join(self.tmp, 'NosMania 2.0')
        game = _game(os.path.join(base, 'Nos-clonev2.0'))
        lib = make_library(base)
        editor = os.path.join(base, 'Nos Chart Maker 8.5.exe')
        open(editor, 'wb').close()
        with self.frozen(base):
            self.assertEqual(L.configured_game_exe(), game)
            self.assertEqual(L.default_root(), lib)
            self.assertEqual(L.configured_editor_exe(), editor)

    def test_library_one_level_down_but_not_inside_the_game(self):
        base = os.path.join(self.tmp, 'base')
        game_dir = os.path.join(base, 'Nos-clonev2.0')
        _game(game_dir)
        os.makedirs(os.path.join(game_dir, 'UserSongs'))
        self.assertEqual(L.find_library_folder(base), '')
        lib = make_library(os.path.join(base, 'Songs'))
        self.assertEqual(L.find_library_folder(base), lib)

    def test_launch_writes_library_root_for_the_game(self):
        from qt_editor import launcher
        game_dir = os.path.join(self.tmp, 'game')
        os.makedirs(game_dir)
        path = launcher.write_game_language(game_dir, 'en', os.path.join(self.tmp, 'Songs', 'UserSongs'))
        with io.open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual(data['code'], 'en')
        self.assertEqual(data['library_root'], os.path.join(self.tmp, 'Songs', 'UserSongs'))

    def test_editor_remembers_library_from_the_launcher(self):
        from qt_editor import app
        lib = make_library(self.tmp)
        argv = ['editor', '--lang', 'en', '--library', lib, 'chart.json']
        self.assertEqual(app.pop_library_arg(argv), lib)
        self.assertEqual(argv, ['editor', '--lang', 'en', 'chart.json'])
        app.remember_library(lib)
        self.assertEqual(settings.get('song_library_root'), lib)
        app.remember_library(os.path.join(self.tmp, 'missing'))
        self.assertEqual(settings.get('song_library_root'), lib)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, 'game')
        os.makedirs(self.game)
        self.a = make_library(os.path.join(self.tmp, 'A'))
        self.b = make_library(os.path.join(self.tmp, 'B'))
        self.link = os.path.join(self.game, 'UserSongs')

    def tearDown(self):
        if L.is_junction(self.link):
            os.rmdir(self.link)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_link_relink_and_keep_songs(self):
        self.assertEqual(L.ensure_game_library_link(self.game, self.a), 'linked')
        self.assertTrue(L.is_junction(self.link))
        self.assertEqual(sorted(os.listdir(self.link)), sorted(os.listdir(self.a)))
        self.assertEqual(L.ensure_game_library_link(self.game, self.a), 'ok')
        before = sorted(os.listdir(self.a))
        self.assertEqual(L.ensure_game_library_link(self.game, self.b), 'relinked')
        self.assertEqual(sorted(os.listdir(self.a)), before)       # 換連結不動原本的曲庫
        self.assertEqual(sorted(os.listdir(self.link)), sorted(os.listdir(self.b)))

    def test_empty_library_made_by_the_game_is_set_aside(self):
        os.makedirs(self.link)
        open(os.path.join(self.link, 'library.json'), 'w').close()
        self.assertEqual(L.ensure_game_library_link(self.game, self.a), 'replaced_empty')
        self.assertTrue(L.is_junction(self.link))
        self.assertTrue(any(n.startswith('UserSongs.empty-') for n in os.listdir(self.game)))

    def test_real_library_in_game_folder_is_untouched(self):
        os.makedirs(os.path.join(self.link, 'Song'))
        self.assertEqual(L.ensure_game_library_link(self.game, self.a), 'real_folder')
        self.assertFalse(L.is_junction(self.link))

    def _new_build(self):
        exe_dir = os.path.join(self.game, 'Nos-clonev1_Data', 'Managed')
        os.makedirs(exe_dir)
        open(os.path.join(self.game, 'Nos-clonev1.exe'), 'wb').close()
        with open(os.path.join(exe_dir, 'Nostalgia.Runtime.dll'), 'wb') as fh:
            fh.write(b'MZ..' + 'library_root'.encode('utf-16-le') + b'..')

    def test_new_build_reads_the_launcher_file_instead_of_a_link(self):
        _game(self.game)
        self.assertFalse(L.game_reads_launcher_library(self.game))
        self.assertEqual(L.ensure_game_library_link(self.game, self.a), 'linked')
        shutil.rmtree(os.path.join(self.game, 'Nos-clonev1_Data'))
        os.remove(os.path.join(self.game, 'Nos-clonev1.exe'))
        self._new_build()
        with open(os.path.join(self.game, 'nosmania_launcher.json'), 'w', encoding='utf-8') as fh:
            json.dump({'code': 'en', 'stamp': 5}, fh)
        self.assertTrue(L.game_reads_launcher_library(self.game))
        self.assertEqual(L.ensure_game_library_link(self.game, self.b), 'unlinked')
        self.assertFalse(os.path.exists(self.link))
        self.assertTrue(os.path.isdir(self.a))                      # 拿掉連結不動曲庫
        with open(os.path.join(self.game, 'nosmania_launcher.json'), encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual(data, {'code': 'en', 'stamp': 5, 'library_root': os.path.abspath(self.b)})
        self.assertEqual(L.ensure_game_library_link(self.game, self.b), 'launcher_file')
        self.assertFalse(os.path.exists(self.link))

    def test_library_folder_itself_in_game_folder(self):
        lib = make_library(self.game)
        self.assertEqual(L.ensure_game_library_link(self.game, lib), 'ok')


class InstallLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = make_library(os.path.join(self.tmp, 'src'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, name, top):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, 'w') as zf:
            for base, _dirs, files in os.walk(self.lib):
                for f in files:
                    full = os.path.join(base, f)
                    rel = os.path.relpath(full, self.lib).replace(os.sep, '/')
                    zf.write(full, (top + '/' if top else '') + rel)
        return path

    def test_zip_with_usersongs_folder(self):
        package = self._zip('songs.zip', 'UserSongs')
        self.assertTrue(L.is_library_package(package))
        self.assertFalse(L.is_game_package(package))
        dest = os.path.join(self.tmp, 'Songs')
        root = updates.install_library(package, dest)
        self.assertEqual(root, os.path.join(dest, 'UserSongs'))
        self.assertTrue(os.path.isfile(os.path.join(root, 'library.json')))
        self.assertEqual(sorted(os.listdir(root)), sorted(os.listdir(self.lib)))

    def test_flat_zip_goes_into_usersongs(self):
        package = self._zip('flat.zip', '')
        root = updates.install_library(package, os.path.join(self.tmp, 'Songs'))
        self.assertEqual(os.path.basename(root), 'UserSongs')
        self.assertTrue(os.path.isfile(os.path.join(root, 'library.json')))

    def test_does_not_overwrite_an_existing_library(self):
        package = self._zip('songs.zip', 'UserSongs')
        dest = os.path.join(self.tmp, 'Songs')
        updates.install_library(package, dest)
        with self.assertRaises(updates.UpdateError):
            updates.install_library(package, dest)

    def test_folder_is_used_in_place(self):
        self.assertEqual(updates.install_library(self.lib, self.tmp), self.lib)
        self.assertEqual(updates.install_library(os.path.dirname(self.lib), self.tmp), self.lib)

    def test_stray_exe_in_a_song_folder_is_still_a_library(self):
        package = self._zip('songs.zip', 'UserSongs')
        with zipfile.ZipFile(package, 'a') as zf:
            zf.writestr('UserSongs/Alpha/setup.exe', b'x')
        self.assertTrue(L.is_library_package(package))
        self.assertFalse(L.is_game_package(package))

    def test_found_next_to_the_launcher(self):
        package = self._zip('Nos-clone 2.0 曲庫.zip', 'UserSongs')
        self.assertEqual(L.find_library_packages(self.tmp), [package])


class LocationsDialogTests(unittest.TestCase):
    def setUp(self):
        from qt_editor.launcher import LauncherWindow
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.lib = make_library(os.path.join(self.tmp, 'Songs'))
        self.exe = _game(os.path.join(self.tmp, 'Game'))
        settings._data.update(song_library_root=self.lib, game_exe_path=self.exe, editor_exe_path='')
        self._patches = [mock.patch.object(settings, 'save'),
                         mock.patch('qt_editor.library_manager.game_running', return_value=False),
                         mock.patch('qt_editor.launcher.editor_ipc.send', return_value=False)]
        self.send = [p.start() for p in self._patches][-1]
        self.home = LauncherWindow()

    def tearDown(self):
        self.home.close()
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dialog_shows_the_three_locations(self):
        self.home.open_locations()
        dialog = self.home._locations
        self.assertIn(self.exe, dialog.rows['game']['path'].text())
        self.assertIn(self.lib, dialog.rows['library']['path'].text())
        self.assertIn('2 首', dialog.rows['library']['path'].text())
        dialog.close()

    def test_changing_library_tells_editor_and_game(self):
        other = make_library(os.path.join(self.tmp, 'Other'))
        self.addCleanup(lambda: L.is_junction(os.path.join(os.path.dirname(self.exe), 'UserSongs')) and
                        os.rmdir(os.path.join(os.path.dirname(self.exe), 'UserSongs')))
        self.home.set_library_root(other)
        self.assertEqual(settings.get('song_library_root'), other)
        self.send.assert_called_with({'library': other})
        with io.open(os.path.join(os.path.dirname(self.exe), 'nosmania_launcher.json'), encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['library_root'], other)

    def test_library_is_linked_as_soon_as_both_are_known(self):
        link = os.path.join(os.path.dirname(self.exe), 'UserSongs')
        try:
            self.home.set_library_root(self.lib)
            self.assertTrue(L.is_junction(link))
            self.assertEqual(sorted(os.listdir(link)), sorted(os.listdir(self.lib)))
        finally:
            if L.is_junction(link):
                os.rmdir(link)

    def test_home_page_has_one_locations_link(self):
        self.assertEqual(self.home.locations_link.text(), '位置設定…')
        self.assertFalse(hasattr(self.home, 'pick_editor'))


class MissingLibraryTests(unittest.TestCase):
    """重新 build 遊戲把資料夾整個換掉，原本放在裡面的曲庫不見了：匯入不可以按了沒反應。"""

    def setUp(self):
        from qt_editor.library_manager import LibraryManagerWindow
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.gone = os.path.join(self.tmp, 'Nos-clone2.0', 'UserSongs')
        settings._data.update(song_library_root=self.gone, game_exe_path='', editor_exe_path='')
        self._save = mock.patch.object(settings, 'save')
        self._save.start()
        self.mgr = LibraryManagerWindow()
        self.changed = []
        self.mgr.root_changed_cb = self.changed.append

    def tearDown(self):
        self.mgr.close()
        self._save.stop()
        for k, v in self._saved.items():
            settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _answer(self, label):
        from PyQt5.QtWidgets import QMessageBox
        seen = []

        def exec_(box):
            seen.append(box.text())
            box._pick = next(b for b in box.buttons() if b.text() == label)
            return 0
        return seen, mock.patch.multiple(QMessageBox, exec_=exec_,
                                         clickedButton=lambda box: box._pick)

    def test_import_asks_for_a_library_and_continues(self):
        from PyQt5.QtWidgets import QFileDialog
        lib = make_library(os.path.join(self.tmp, 'Songs'))
        seen, patch = self._answer('選擇曲庫資料夾…')
        with patch, mock.patch.object(QFileDialog, 'getExistingDirectory', return_value=os.path.dirname(lib)),                 mock.patch.object(QFileDialog, 'getOpenFileNames', return_value=([], '')) as pick_zip:
            self.mgr._import_zip()
        self.assertIn(self.gone, seen[0])
        self.assertEqual(self.mgr.lib.root, lib)
        self.assertEqual(self.changed, [lib])
        self.assertTrue(pick_zip.called)          # 選好曲庫之後接著做原本的匯入

    def test_cancel_leaves_everything_alone(self):
        seen, patch = self._answer('取消')
        with patch:
            self.mgr._batch_import()
        self.assertEqual(len(seen), 1)
        self.assertIsNone(self.mgr.lib)

    def test_home_page_says_the_folder_is_gone(self):
        from qt_editor.launcher import LauncherWindow
        with mock.patch('qt_editor.library_manager.game_running', return_value=False):
            home = LauncherWindow()
            home.refresh_info()
            self.assertIn('資料夾不見了', home.info.text())
            home.close()


if __name__ == '__main__':
    unittest.main()
