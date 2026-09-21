"""平台分支：在 Windows 上模擬 macOS，確認那些分支真的走得到。

這個專案原本一個平台判斷都沒有，所以這裡釘住的是「macOS 上不會默默壞掉」：
設定檔不會寫進 .app、找得到 dylib、曲庫連結改用 symlink、做不到的功能會
明講原因。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from qt_editor import platform_support as P


class MacPatch:
    """把模組層的平台常數換成 macOS 的樣子。"""

    def __init__(self, platform='darwin'):
        self.platform = platform

    def __enter__(self):
        self._saved = (P.IS_WINDOWS, P.IS_MAC, P.IS_LINUX)
        P.IS_WINDOWS = self.platform.startswith('win')
        P.IS_MAC = self.platform == 'darwin'
        P.IS_LINUX = not P.IS_WINDOWS and not P.IS_MAC
        return self

    def __exit__(self, *_exc):
        P.IS_WINDOWS, P.IS_MAC, P.IS_LINUX = self._saved
        return False


class FeatureGateTests(unittest.TestCase):
    def test_windows_only_features_are_off_on_mac(self):
        with MacPatch():
            for name in ('hiraeth', 'ai_transcribe', 'game_launch'):
                self.assertFalse(P.feature_available(name), name)
                self.assertTrue(P.feature_reason(name), '關掉了要講原因：%s' % name)

    def test_everything_is_on_for_windows(self):
        with MacPatch('win32'):
            for name in P.WINDOWS_ONLY_FEATURES:
                self.assertTrue(P.feature_available(name), name)
                self.assertEqual(P.feature_reason(name), '')

    def test_unknown_features_are_allowed(self):
        with MacPatch():
            self.assertTrue(P.feature_available('編輯譜面'))


class PathTests(unittest.TestCase):
    def test_mac_user_data_goes_to_application_support(self):
        with MacPatch():
            path = P.user_data_dir('X')
            self.assertIn('Library/Application Support', path.replace(os.sep, '/'))
            self.assertTrue(path.endswith('X'))

    def test_windows_user_data_uses_localappdata(self):
        with MacPatch('win32'), mock.patch.dict(os.environ, {'LOCALAPPDATA': r'C:\L'}):
            self.assertEqual(P.user_data_dir('X'), os.path.join(r'C:\L', 'X'))

    def test_app_suffix_and_recognition(self):
        with MacPatch():
            self.assertEqual(P.executable_suffix(), '.app')
            self.assertTrue(P.looks_like_app('/Applications/Game.app'))
        with MacPatch('win32'):
            self.assertEqual(P.executable_suffix(), '.exe')
            self.assertTrue(P.looks_like_app(r'C:\g\Game.exe'))
            self.assertFalse(P.looks_like_app('/Applications/Game.app'))

    def test_mac_apps_are_launched_with_open(self):
        with MacPatch():
            self.assertEqual(P.launch_command('/A/Game.app'), ['open', '-a', '/A/Game.app'])
            self.assertEqual(P.launch_command('/A/tool'), ['/A/tool'])
        with MacPatch('win32'):
            self.assertEqual(P.launch_command(r'C:\g\Game.exe'), [r'C:\g\Game.exe'])


class SettingsLocationTests(unittest.TestCase):
    """打包後的設定檔位置：macOS 不能寫在執行檔旁邊（那是 .app 裡面）。"""

    def test_frozen_mac_settings_live_outside_the_bundle(self):
        from qt_editor import settings as S
        exe = '/Applications/Nos Chart Maker.app/Contents/MacOS/Nos Chart Maker'
        with MacPatch(), mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', exe), \
                mock.patch.object(P, 'user_data_dir', return_value='/tmp/nos-test'), \
                mock.patch('os.makedirs'):
            base = S._base_dir()
        self.assertNotIn('.app/Contents', base)
        self.assertEqual(base, '/tmp/nos-test')

    def test_frozen_windows_still_sits_next_to_the_exe(self):
        from qt_editor import settings as S
        exe = r'D:\Nostalgia\Nos Chart Maker 10.0.exe'
        with MacPatch('win32'), mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', exe):
            self.assertEqual(S._base_dir(), os.path.dirname(exe))


class FluidSynthLookupTests(unittest.TestCase):
    def names(self, key):
        from qt_editor import midi_preview as M
        with mock.patch.object(M.sys, 'platform',
                               {'mac': 'darwin', 'win': 'win32'}.get(key, 'linux')):
            return [c.name for c in M.fluidsynth_library_candidates()]

    def test_mac_looks_for_dylibs(self):
        names = self.names('mac')
        self.assertIn('libfluidsynth.3.dylib', names)
        self.assertNotIn('libfluidsynth-3.dll', names)

    def test_windows_still_looks_for_the_dll(self):
        self.assertIn('libfluidsynth-3.dll', self.names('win'))

    def test_mac_searches_homebrew(self):
        from qt_editor import midi_preview as M
        with mock.patch.object(M.sys, 'platform', 'darwin'):
            folders = {str(c.parent).replace(os.sep, '/')
                       for c in M.fluidsynth_library_candidates()}
        self.assertIn('/opt/homebrew/lib', folders, 'Apple Silicon 的 Homebrew')
        self.assertIn('/usr/local/lib', folders, 'Intel 的 Homebrew')

    def test_bundled_copies_come_first(self):
        from qt_editor import midi_preview as M
        with mock.patch.object(M.sys, 'platform', 'darwin'):
            first = M.fluidsynth_library_candidates()[0]
        self.assertNotIn('homebrew', str(first), '自己帶的要優先於系統裝的')


class DirectoryLinkTests(unittest.TestCase):
    def test_symlink_is_used_off_windows(self):
        with MacPatch():
            with mock.patch('os.symlink') as link:
                self.assertTrue(P.make_dir_link('/l', '/t'))
            link.assert_called_once()

    def test_a_failed_symlink_reports_false(self):
        with MacPatch():
            with mock.patch('os.symlink', side_effect=OSError):
                self.assertFalse(P.make_dir_link('/l', '/t'))

    def test_junction_is_used_on_windows(self):
        with MacPatch('win32'):
            with mock.patch('subprocess.run') as run:
                self.assertTrue(P.make_dir_link('l', 't'))
            self.assertIn('mklink', run.call_args[0][0])

    def test_song_library_links_without_cmd_on_mac(self):
        """make_junction 在 macOS 上不能去叫 cmd，也不能碰 mbcs 編碼。"""
        from qt_editor import song_library as SL
        with MacPatch(), tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, 'target')
            link = os.path.join(root, 'link')
            os.makedirs(target)
            with mock.patch('subprocess.run', side_effect=AssertionError('不該叫 cmd')), \
                    mock.patch.object(P, 'make_dir_link', return_value=True), \
                    mock.patch('os.path.isdir', return_value=True):
                SL.make_junction(target, link)

    def test_a_failure_is_reported_as_oserror(self):
        from qt_editor import song_library as SL
        with MacPatch():
            with mock.patch.object(P, 'make_dir_link', return_value=False):
                with self.assertRaises(OSError):
                    SL.make_junction('/t', '/l')


class AudioBackendTests(unittest.TestCase):
    """sounddevice 的替身：介面要和 simpleaudio 一樣。"""

    def test_it_mimics_simpleaudio(self):
        from qt_editor import audio_backend_sd as B
        self.assertTrue(hasattr(B, 'WaveObject'))
        obj = B.WaveObject(b'\0\0', num_channels=2, bytes_per_sample=2,
                           sample_rate=44100)
        for attr in ('audio_data', 'num_channels', 'bytes_per_sample', 'sample_rate'):
            self.assertTrue(hasattr(obj, attr), attr)
        self.assertTrue(hasattr(obj, 'play'))

    def test_play_objects_have_stop_and_is_playing(self):
        from qt_editor import audio_backend_sd as B
        for name in ('stop', 'is_playing', 'wait_done'):
            self.assertTrue(callable(getattr(B.PlayObject, name)), name)

    def test_missing_sounddevice_is_not_a_crash(self):
        from qt_editor import audio_backend_sd as B
        with mock.patch.object(B, '_sd', None):
            self.assertFalse(B.available())
            self.assertIsNone(B.backend_name())
            B.stop_all()                    # 不能丟例外

    def test_the_player_falls_back_to_it(self):
        """simpleaudio 不在的時候，播放器要接上這個後端。"""
        import importlib
        from qt_editor import audio_backend_sd as B
        real_import = __import__

        def no_simpleaudio(name, *args, **kwargs):
            if name == 'simpleaudio':
                raise ImportError('no simpleaudio')
            return real_import(name, *args, **kwargs)

        with mock.patch.object(B, 'available', return_value=True), \
                mock.patch('builtins.__import__', side_effect=no_simpleaudio):
            module = importlib.reload(importlib.import_module('qt_editor.audio_player'))
        try:
            self.assertTrue(module._HAS_SA, '沒有 simpleaudio 就該用 sounddevice')
            self.assertIs(module.sa, B)
        finally:
            importlib.reload(module)


class MacBuildFilesTests(unittest.TestCase):
    """Mac 版的建置檔案要在，而且不能帶 Windows 的東西。"""

    def read(self, name):
        import io
        here = os.path.dirname(os.path.abspath(__file__))
        return io.open(os.path.join(here, name), encoding='utf-8').read()

    def test_every_build_file_is_actually_in_git(self):
        """.gitignore 有 `*.spec`，Mac 的 spec 就這樣被擋掉、推不上 GitHub，
        使用者在 Mac 上拿到的是一份少了 spec 的原始碼。"""
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        needed = ['NostalgiaChartEditor-mac.spec', 'build_mac.sh',
                  'requirements-mac.txt', 'README-mac.md']
        try:
            out = subprocess.run(['git', 'check-ignore'] + needed, cwd=here,
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest('沒有 git')
        ignored = [l for l in out.stdout.splitlines() if l.strip()]
        self.assertEqual(ignored, [], '這些建置檔案被 .gitignore 擋住了：%s' % ignored)

    def test_the_mac_spec_makes_an_app_bundle(self):
        spec = self.read('NostalgiaChartEditor-mac.spec')
        self.assertIn('BUNDLE(', spec)
        self.assertIn('COLLECT(', spec)
        self.assertIn('.app', spec)
        self.assertNotIn('.dll', spec.replace('DLL', ''), 'Mac 版不該帶 Windows DLL')
        self.assertNotIn("icon.ico", spec)

    def test_the_mac_requirements_avoid_windows_only_packages(self):
        req = self.read('requirements-mac.txt').lower()
        self.assertIn('sounddevice', req)
        self.assertIn('pyqt5', req)
        for bad in ('winsound', 'pywin32'):
            self.assertNotIn(bad, req.split('#')[0])

    def test_the_build_script_is_a_posix_script(self):
        script = self.read('build_mac.sh')
        self.assertTrue(script.startswith('#!/bin/bash'))
        self.assertIn('iconutil', script, '要生 .icns')
        self.assertIn('fluid-synth', script, '要處理 FluidSynth')
        self.assertNotIn('\r\n', script, 'CRLF 會讓 bash 說找不到直譯器')

    def test_the_build_script_is_pure_ascii(self):
        """中文在機器之間複製時被改掉編碼，會讓 shell 把後面當成變數展開
        （實測回報：line 19 unbound variable）。說明放 README-mac.md。"""
        script = self.read('build_mac.sh')
        bad = [(i, line) for i, line in enumerate(script.splitlines(), 1)
               if any(ord(ch) > 126 for ch in line)]
        self.assertEqual(bad, [], '這幾行有非 ASCII 字元：%s' % bad[:3])

    def test_the_build_script_does_not_use_set_u(self):
        """`set -u` 擋不了什麼，卻會在 venv 的 activate 與訊息字串上炸掉。"""
        for line in self.read('build_mac.sh').splitlines():
            stripped = line.strip()
            if stripped.startswith('set -') and not stripped.startswith('set -x'):
                flags = stripped.split()[1].lstrip('-')
                self.assertNotIn('u', flags, stripped)

    def test_the_build_script_announces_itself_before_any_check(self):
        """沒有輸出＝沒有執行到。第一個會印東西的指令要排在所有檢查前面，
        使用者回報「跑了但什麼都沒有」時才分辨得出來是哪一種。"""
        lines = [l.strip() for l in self.read('build_mac.sh').splitlines()]
        code = [l for l in lines if l and not l.startswith('#')]
        first_echo = next(i for i, l in enumerate(code) if l.startswith('echo'))
        first_check = next((i for i, l in enumerate(code)
                            if l.startswith('if [') or l.startswith('[ ')), len(code))
        self.assertLess(first_echo, first_check)


if __name__ == '__main__':
    unittest.main()
