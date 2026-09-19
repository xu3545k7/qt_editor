"""曲庫管理視窗：列歌、改資料、刪除與復原、存譜通知遊戲、啟動遊戲前處理曲庫連結。"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from qt_editor.settings import settings  # noqa: E402
from test_song_library import make_library  # noqa: E402


class LibraryManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        cls._saved = {k: settings._data.get(k) for k in ('song_library_root', 'game_exe_path')}
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved.items():
            settings._data[k] = v if v is not None else ''
        cls.win.view.model.dirty = False
        cls.win.close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self._save = mock.patch.object(settings, 'save')
        self._save.start()
        from qt_editor.library_manager import LibraryManagerWindow
        self.opener = mock.Mock()
        self.mgr = LibraryManagerWindow(None, open_chart=self.opener)
        self.mgr.set_root(self.root)

    def tearDown(self):
        self.mgr.close()
        self._save.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def select(self, folder):
        self.mgr._current = folder
        self.mgr._select_folder(folder)
        self.mgr._show_song(self.mgr._song(folder))

    def revision(self):
        with open(os.path.join(self.root, '.editor_revision.json'), encoding='utf-8') as fh:
            return json.load(fh)

    def test_lists_songs_and_filters(self):
        self.assertEqual(self.mgr.table.rowCount(), 2)
        self.mgr.search.setText('beta')
        self.assertEqual(self.mgr.table.rowCount(), 1)
        self.mgr.search.setText('')
        self.mgr.category_filter.setCurrentText('Deemo')
        self.assertEqual(self.mgr.table.rowCount(), 1)

    def test_save_song_details(self):
        self.select('Alpha')
        self.mgr.title_edit.setText('新曲名')
        self.mgr.category_edit.setCurrentText('Arcaea')
        self.mgr.slogan_edit.setText('評論')
        self.mgr._save_song()
        song = self.mgr.lib.song('Alpha')
        self.assertEqual((song.title, song.category, song.slogan), ('新曲名', 'Arcaea', '評論'))
        self.assertEqual(self.revision()['folder'], 'Alpha')

    def test_edit_difficulties_in_the_table(self):
        self.select('Alpha')
        self.mgr.diff_table.item(1, 0).setText('Expert')
        self.mgr.diff_table.item(1, 1).setText('11')
        self.mgr._save_diffs()
        diffs = self.mgr.lib.song('Alpha').difficulties
        self.assertEqual((diffs[1].name, diffs[1].level), ('Expert', 11))

    def test_delete_and_undo(self):
        self.select('Beta')
        with mock.patch('qt_editor.library_manager.QMessageBox.question', return_value=QMessageBox.Yes):
            self.mgr._delete_song()
            self.assertEqual(self.mgr.table.rowCount(), 1)
            self.mgr._undo()
        self.assertEqual(self.mgr.table.rowCount(), 2)

    def test_open_chart_goes_to_the_editor(self):
        self.select('Alpha')
        self.mgr.diff_table.selectRow(0)
        self.mgr._open_selected_chart()
        self.assertTrue(self.opener.call_args[0][0].endswith('Alpha.json'))

    def test_saving_a_chart_in_the_library_notifies_the_game(self):
        chart = os.path.join(self.root, 'Alpha', 'Real', 'Alpha.json')
        with contextlib.redirect_stdout(io.StringIO()):
            self.win._load_path(chart)
        before = self.revision()['revision'] if os.path.exists(
            os.path.join(self.root, '.editor_revision.json')) else 0
        with mock.patch('qt_editor.main_window.QMessageBox.information'), \
                mock.patch.object(self.win, '_block_on_unassigned', return_value=False):
            self.win._do_save(chart)
        after = self.revision()
        self.assertEqual(after['revision'], before + 1)
        self.assertEqual(after['folder'], 'Alpha')

    def test_launch_links_the_separate_library(self):
        """曲庫和遊戲分開放：進遊戲前自動在遊戲資料夾放連結，不用問。"""
        game_dir = os.path.join(self.tmp, 'Nos-clonev9')
        os.makedirs(os.path.join(game_dir, 'Game_Data'))
        exe = os.path.join(game_dir, 'Game.exe')
        open(exe, 'wb').close()
        settings._data['game_exe_path'] = exe
        with mock.patch('qt_editor.library_manager.game_running', return_value=False),                 mock.patch('qt_editor.library_manager.QMessageBox.question') as ask,                 mock.patch('qt_editor.song_library.make_junction') as link,                 mock.patch('qt_editor.library_manager.subprocess.Popen') as popen:
            self.mgr.launch_game()
        self.assertFalse(ask.called)
        link.assert_called_once_with(self.root, os.path.join(game_dir, 'UserSongs'))
        self.assertEqual(popen.call_args[0][0], [exe])

    def test_launch_leaves_a_real_library_in_the_game_folder(self):
        game_dir = os.path.join(self.tmp, 'Nos-clonev9')
        os.makedirs(os.path.join(game_dir, 'Game_Data'))
        os.makedirs(os.path.join(game_dir, 'UserSongs', 'Own song'))
        exe = os.path.join(game_dir, 'Game.exe')
        open(exe, 'wb').close()
        settings._data['game_exe_path'] = exe
        with mock.patch('qt_editor.library_manager.game_running', return_value=False),                 mock.patch('qt_editor.library_manager.QMessageBox.question',
                           return_value=QMessageBox.No) as ask,                 mock.patch('qt_editor.song_library.make_junction') as link,                 mock.patch('qt_editor.library_manager.subprocess.Popen') as popen:
            self.mgr.launch_game()
        self.assertTrue(ask.called)
        self.assertFalse(link.called)
        self.assertTrue(os.path.isdir(os.path.join(game_dir, 'UserSongs', 'Own song')))
        self.assertTrue(popen.called)

    def test_launch_does_nothing_when_already_running(self):
        exe = os.path.join(self.tmp, 'x.exe')
        open(exe, 'wb').close()
        settings._data['game_exe_path'] = exe
        with mock.patch('qt_editor.library_manager.game_running', return_value=True), \
                mock.patch('qt_editor.library_manager.subprocess.Popen') as popen:
            self.mgr.launch_game()
        self.assertFalse(popen.called)


class BundledLayoutTests(unittest.TestCase):
    """製譜器打包後放在遊戲 build 資料夾裡：遊戲和曲庫直接用旁邊的，不到處找。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.build = os.path.join(self.tmp, 'Nos-clonev1.7')
        os.makedirs(os.path.join(self.build, 'Nos-clonev1_Data'))
        open(os.path.join(self.build, 'Nos-clonev1.exe'), 'wb').close()
        open(os.path.join(self.build, 'UnityCrashHandler64.exe'), 'wb').close()
        self.editor = os.path.join(self.build, 'Nos Chart Maker.exe')
        open(self.editor, 'wb').close()
        os.makedirs(os.path.join(self.build, 'UserSongs'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def frozen(self):
        import sys
        return mock.patch.multiple(sys, create=True, frozen=True, executable=self.editor)

    def test_uses_the_game_and_library_next_to_the_editor(self):
        from qt_editor import song_library as L
        from qt_editor.library_manager import bundled_game_exe
        with self.frozen():
            self.assertEqual(L.app_dir(), self.build)
            self.assertEqual(L.bundled_root(), os.path.join(self.build, 'UserSongs'))
            self.assertEqual(L.default_root(), os.path.join(self.build, 'UserSongs'))
            self.assertEqual(bundled_game_exe(), os.path.join(self.build, 'Nos-clonev1.exe'))

    def test_not_packaged_means_no_guessing(self):
        from qt_editor import song_library as L
        from qt_editor.library_manager import bundled_game_exe
        self.assertEqual(L.app_dir(), '')
        self.assertEqual(L.bundled_root(), '')
        self.assertEqual(bundled_game_exe(), '')


if __name__ == '__main__':
    unittest.main()
