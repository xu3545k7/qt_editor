"""NosMania 啟動器：三個入口接到對的地方；製譜器已開著就交給它；找遊戲和製譜器只看附近。"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.settings import settings  # noqa: E402
from test_song_library import make_library  # noqa: E402


class LauncherTests(unittest.TestCase):
    def setUp(self):
        from qt_editor.launcher import LauncherWindow
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self._saved = {k: settings._data.get(k)
                       for k in ('song_library_root', 'game_exe_path', 'editor_exe_path')}
        settings._data.update(song_library_root=self.root, game_exe_path='', editor_exe_path='')
        self._patches = [mock.patch.object(settings, 'save'),
                         mock.patch('qt_editor.library_manager.game_running', return_value=False),
                         mock.patch('qt_editor.launcher.editor_ipc.send', return_value=False)]
        self.send = self._patches[-1].start()
        for p in self._patches[:-1]:
            p.start()
        self.home = LauncherWindow()
        self.home.show()

    def tearDown(self):
        self.home.close()
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_info_line(self):
        self.assertIn('曲庫：2 首', self.home.info.text())

    def test_manage_library_opens_the_manager(self):
        self.home.manage_library()
        self.assertTrue(self.home._manager.isVisible())
        self.assertEqual(self.home._manager.lib.root, self.root)

    def test_enter_game_uses_the_launch_flow(self):
        with mock.patch('qt_editor.library_manager.LibraryManagerWindow.launch_game') as launch:
            self.home.enter_game()
        self.assertTrue(launch.called)

    def test_make_charts_starts_the_editor_exe(self):
        exe = os.path.join(self.tmp, 'Nos Chart Maker 9.9.exe')
        open(exe, 'wb').close()
        settings._data['editor_exe_path'] = exe
        with mock.patch('qt_editor.launcher.subprocess.Popen') as popen:
            self.assertTrue(self.home.open_editor())
        self.assertEqual(popen.call_args[0][0], [exe, '--lang', 'zh_tw', '--library', self.root])
        self.assertEqual(popen.call_args[1]['cwd'], self.tmp)

    def test_running_editor_gets_the_chart_instead(self):
        self.send.return_value = True
        with mock.patch('qt_editor.launcher.subprocess.Popen') as popen:
            self.home.open_editor('x.json')
        self.send.assert_called_with({'open': 'x.json', 'lang': 'zh_tw', 'library': self.root})
        self.assertFalse(popen.called)

    def test_library_manager_opens_charts_through_the_launcher(self):
        chart = os.path.join(self.root, 'Alpha', 'Real', 'Alpha.json')
        manager = self.home.library_manager()
        with mock.patch.object(self.home, 'open_editor') as opener:
            manager._open_chart_cb = opener
            manager._open_chart(chart)
        opener.assert_called_once_with(chart)

    def test_hiraeth_dialog_without_an_open_chart(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        dlg = HiraethExportDialog(None, os.path.join(self.root, 'Alpha'), self.root, None)
        self.assertFalse(dlg.rb_chart.isEnabled())
        self.assertTrue(dlg.rb_song.isChecked())
        dlg.close()


class EditorIpcTests(unittest.TestCase):
    def test_server_opens_and_shows(self):
        from qt_editor.editor_ipc import EditorServer
        window = mock.Mock()
        server = EditorServer.__new__(EditorServer)
        server._window = window
        server.handle(b'{"open": "D:/a.json"}')
        window.open_chart_from_library.assert_called_once_with('D:/a.json')
        server.handle(b'{"show": true}')
        self.assertTrue(window.enter_editor.called)
        self.assertIsNone(server.handle(b'not json'))

    def test_round_trip_over_local_socket(self):
        from PyQt5.QtCore import QEventLoop, QObject, QTimer
        from qt_editor import editor_ipc

        class Window(QObject):
            opened = []

            def open_chart_from_library(self, path):
                self.opened.append(path)

            def enter_editor(self):
                pass

        window = Window()
        name = 'NosMania.Test.%d' % os.getpid()
        server = editor_ipc.EditorServer(window, name=name)
        self.assertTrue(server.listening)
        try:
            # 真的跨行程送：同一個執行緒裡 server 沒辦法在 send() 等連線時接受
            import subprocess
            code = ('import sys; from qt_editor import editor_ipc as e; e.SERVER_NAME = sys.argv[1]; '
                    'sys.exit(0 if e.send({"open": "曲.json"}, 5000) else 3)')
            proc = subprocess.Popen([sys.executable, '-c', code, name],
                                    cwd=os.path.dirname(os.path.abspath(__file__)))
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if Window.opened else None)
            poll.start(20)
            QTimer.singleShot(15000, loop.quit)
            loop.exec_()
            poll.stop()
            self.assertEqual(proc.wait(10), 0)
            self.assertEqual(Window.opened, ['曲.json'])
        finally:
            server.close()


class LayoutTests(unittest.TestCase):
    """nos-mania2.0/NosMania.exe ＋ Nos-clonev2.0/（遊戲、製譜器、UserSongs）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.top = os.path.join(self.tmp, 'nos-mania2.0')
        self.game_dir = os.path.join(self.top, 'Nos-clonev2.0')
        os.makedirs(os.path.join(self.game_dir, 'Nos-clonev1_Data'))
        os.makedirs(os.path.join(self.game_dir, 'UserSongs'))
        for name in ('Nos-clonev1.exe', 'UnityCrashHandler64.exe', 'Nos Chart Maker 7.2.exe'):
            open(os.path.join(self.game_dir, name), 'wb').close()
        self.launcher = os.path.join(self.top, 'NosMania.exe')
        open(self.launcher, 'wb').close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def frozen(self, exe):
        return mock.patch.multiple(sys, create=True, frozen=True, executable=exe)

    def test_launcher_finds_game_editor_and_library_one_level_down(self):
        from qt_editor import song_library as L
        with self.frozen(self.launcher):
            self.assertEqual(L.bundled_game_exe(), os.path.join(self.game_dir, 'Nos-clonev1.exe'))
            self.assertEqual(L.bundled_editor_exe(),
                             os.path.join(self.game_dir, 'Nos Chart Maker 7.2.exe'))
            self.assertEqual(L.bundled_root(), os.path.join(self.game_dir, 'UserSongs'))

    def test_newest_editor_wins(self):
        from qt_editor import song_library as L
        newer = os.path.join(self.top, 'Nos Chart Maker 7.3.exe')
        open(newer, 'wb').close()
        old = os.path.join(self.game_dir, 'Nos Chart Maker 7.2.exe')
        os.utime(old, (1, 1))
        with self.frozen(self.launcher):
            self.assertEqual(L.bundled_editor_exe(), newer)

    def test_not_packaged_means_no_guessing(self):
        from qt_editor import song_library as L
        self.assertEqual(L.bundled_game_exe(), '')
        self.assertEqual(L.bundled_editor_exe(), '')
        self.assertEqual(L.bundled_root(), '')


class EditorIsJustAnEditorTests(unittest.TestCase):
    def test_no_home_or_library_menu(self):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            win = MainWindow()
        try:
            texts = [a.text() for m in win.menuBar().actions() if m.menu()
                     for a in m.menu().actions()]
            self.assertFalse([t for t in texts if '曲庫管理' in t or '首頁' in t])
            self.assertFalse(hasattr(win, 'show_home'))
        finally:
            win.view.model.dirty = False
            win.close()


if __name__ == '__main__':
    unittest.main()
