"""NosMania 語言：啟動器選的語言帶到曲庫管理、製譜器、遊戲；匯出完整曲目直接放進遊戲曲庫。"""

import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import i18n  # noqa: E402
from qt_editor.settings import settings  # noqa: E402
from test_song_library import make_library  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class TranslationTableTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang('zh_tw')

    def test_every_tr_literal_has_a_translation(self):
        from qt_editor.ui_text import _TABLE
        missing = []
        for name in ('library_manager', 'launcher', 'hiraeth_export_dialog', 'song_library',
                     'hiraeth_export', 'library_tools', 'updates', 'batch_import_dialog',
                     'hiraeth_tools', 'hiraeth_panel', 'hiraeth_sync',
                     'tail_trim', 'tail_trim_dialog'):
            path = os.path.join(HERE, 'qt_editor', name + '.py')
            with io.open(path, encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'tr' and \
                        node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value not in _TABLE:
                        missing.append((name, node.lineno, node.args[0].value))
        self.assertEqual(missing, [])

    def test_placeholders_match(self):
        import re
        from qt_editor.ui_text import _TABLE
        spec = re.compile(r'%(?:\d\$)?[sdr]|%\.\d?f')
        for src, (cn, en) in _TABLE.items():
            n = len(spec.findall(src))
            self.assertEqual(len(spec.findall(cn)), n, src)
            self.assertEqual(len(spec.findall(en)), n, src)

    def test_reordered_arguments(self):
        from qt_editor.ui_text import tr
        i18n.set_lang('en')
        self.assertEqual(tr('修改「%s」的難度 %s') % ('Song', 'Real'), 'Edit difficulty Real of "Song"')
        i18n.set_lang('zh_cn')
        self.assertEqual(tr('曲庫'), '曲库')
        i18n.set_lang('zh_tw')
        self.assertEqual(tr('曲庫'), '曲庫')


class JapaneseTableTests(unittest.TestCase):
    """日文是第四種語言，兩張表（i18n 的 key 表、ui_text 的繁體原文表）都要滿。

    日文和韓文在遊戲端走的是「繁體字串當鍵查表」，這裡的 `_JA` 是同一個做法。
    漏翻時**退英文不退繁體**：日文畫面上冒出一句繁體中文像是程式壞了。
    """

    def tearDown(self):
        i18n.set_lang('zh_tw')

    def test_i18n_has_every_key(self):
        zh = i18n._STRINGS['zh_tw']
        ja = i18n._STRINGS['ja']
        self.assertEqual([k for k in zh if k not in ja], [])
        self.assertEqual([k for k, v in zh.items() if v and not ja.get(k)], [])

    def test_ui_text_has_every_key(self):
        from qt_editor.ui_text import _JA, _TABLE
        self.assertEqual([k for k in _TABLE if k not in _JA], [])
        self.assertEqual([k for k, v in _JA.items() if not v], [])

    def test_placeholders_match(self):
        import re
        from qt_editor.ui_text import _JA
        # %s %d %r %.1f %+.1f 都要照數量留著，不然 tr(...) % args 會當場炸
        spec = re.compile(r'%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[a-zA-Z]')
        for src_text, ja in _JA.items():
            self.assertEqual(len(spec.findall(ja)), len(spec.findall(src_text)), src_text)
        brace = re.compile(r'\{[^{}]*\}')
        zh = i18n._STRINGS['zh_tw']
        for key, ja in i18n._STRINGS['ja'].items():
            want = sorted(x.split(':')[0].rstrip('}') + '}' for x in brace.findall(zh[key]))
            got = sorted(x.split(':')[0].rstrip('}') + '}' for x in brace.findall(ja))
            self.assertEqual(got, want, key)

    def test_japanese_is_returned(self):
        from qt_editor.ui_text import tr
        i18n.set_lang('ja')
        self.assertEqual(tr('曲庫'), 'ライブラリ')
        self.assertEqual(i18n.t('menu_file'), 'ファイル(&F)')
        self.assertEqual(i18n.t('dlg_bpm_label', 120.0), '現在の BPM = 120.00\n新しい BPM：')

    def test_missing_falls_back_to_english_not_chinese(self):
        ja = i18n._STRINGS['ja']
        saved = ja.pop('menu_file')
        try:
            i18n.set_lang('ja')
            self.assertEqual(i18n.t('menu_file'), 'File(&F)')
        finally:
            ja['menu_file'] = saved

    def test_every_language_reaches_the_game(self):
        """選單上列得出來的語言，啟動器都要能寫進 nosmania_launcher.json。"""
        from qt_editor.launcher import GAME_LANGUAGE
        from qt_editor.ui_text import LANGUAGES
        self.assertEqual([c for c, _ in LANGUAGES if c not in GAME_LANGUAGE], [])
        self.assertEqual(GAME_LANGUAGE['ja'], 'Japanese')

    def test_language_lists_are_not_copied(self):
        """三個地方以前各抄一份語言清單，加語言時只改一處就會漏。"""
        from qt_editor.app import pop_lang_arg
        from qt_editor.settings_dialog import _LANG_OPTIONS
        from qt_editor.ui_text import LANGUAGES
        self.assertEqual(pop_lang_arg(['x', '--lang', 'ja']), 'ja')
        self.assertEqual(pop_lang_arg(['x', '--lang', 'ko']), '')
        self.assertEqual([c for _, c in _LANG_OPTIONS], [c for c, _ in LANGUAGES])


class JapaneseFontTests(unittest.TestCase):
    """日文介面要換字型：系統字型的漢字是本地字形，日文看起來會怪。"""

    def tearDown(self):
        i18n.set_lang('zh_tw')
        from qt_editor.ui_font import apply_ui_font
        apply_ui_font()

    def test_font_file_ships_with_the_package(self):
        from qt_editor.ui_font import _font_path
        self.assertTrue(os.path.isfile(_font_path()), _font_path())

    def test_switches_and_switches_back(self):
        # 家族名不寫死：Qt 回的是 'Gen Shin Gothic' 還是 'Gen Shin Gothic Regular'
        # 要看平台和這個 process 裡已經註冊過什麼，兩種都對。
        from qt_editor.ui_font import _load_family, apply_ui_font
        app = QApplication.instance()
        i18n.set_lang('zh_tw')
        apply_ui_font(app)
        before = app.font().family()
        size = app.font().pointSize()
        family = _load_family()
        self.assertTrue(family.startswith('Gen Shin Gothic'), family)
        i18n.set_lang('ja')
        apply_ui_font(app)
        self.assertEqual(app.font().family(), family)
        self.assertEqual(app.font().pointSize(), size)   # 只換家族，不動字級
        i18n.set_lang('zh_tw')
        apply_ui_font(app)
        self.assertEqual(app.font().family(), before)

    def test_specs_bundle_the_font(self):
        for name in ('NostalgiaChartEditor.spec', 'NosMania.spec',
                     'NostalgiaChartEditor-mac.spec'):
            with io.open(os.path.join(HERE, name), encoding='utf-8') as fh:
                self.assertIn("('qt_editor/fonts', 'qt_editor/fonts')", fh.read(), name)


class LauncherLanguageTests(unittest.TestCase):
    def setUp(self):
        from qt_editor.launcher import LauncherWindow
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)
        self.game_dir = os.path.join(self.tmp, 'Game')
        os.makedirs(os.path.join(self.game_dir, 'Game_Data'))
        self.exe = os.path.join(self.game_dir, 'Game.exe')
        open(self.exe, 'wb').close()
        self._saved = {k: settings._data.get(k)
                       for k in ('song_library_root', 'game_exe_path', 'editor_exe_path', 'language')}
        settings._data.update(song_library_root=self.root, game_exe_path=self.exe, editor_exe_path='')
        self._patches = [mock.patch.object(settings, 'save'),
                         mock.patch('qt_editor.library_manager.game_running', return_value=False)]
        for p in self._patches:
            p.start()
        self.send = mock.patch('qt_editor.launcher.editor_ipc.send', return_value=False)
        self.send_mock = self.send.start()
        self.home = LauncherWindow()
        self.home.show()

    def tearDown(self):
        self.home.close()
        self.send.stop()
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            settings._data[k] = v if v is not None else ''
        i18n.set_lang('zh_tw')
        shutil.rmtree(self.tmp, ignore_errors=True)

    def flush(self):
        for _ in range(3):
            QApplication.processEvents()

    def test_switching_language_rebuilds_launcher_and_manager(self):
        self.home.manage_library()
        self.home._manager._current = 'Alpha'
        self.home.set_language('en')
        self.flush()
        self.assertEqual(i18n.get_lang(), 'en')
        self.assertEqual(settings.get('language'), 'en')
        self.assertEqual(self.home.lang_combo.currentData(), 'en')
        self.assertIn('Library: 2 songs', self.home.info.text())
        manager = self.home._manager
        self.assertTrue(manager.isVisible())
        self.assertEqual(manager.windowTitle(), 'NosMania · Song Library')
        self.send_mock.assert_called_with({'lang': 'en'})

    def test_game_gets_a_language_file(self):
        self.home.set_language('zh_cn')
        with open(os.path.join(self.game_dir, 'nosmania_launcher.json'), encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual((data['language'], data['code']), ('SimplifiedChinese', 'zh_cn'))
        first = data['stamp']
        with mock.patch('qt_editor.library_manager.LibraryManagerWindow.launch_game'), \
                mock.patch('time.time', return_value=first / 1000.0 + 5):
            self.home.enter_game()
        with open(os.path.join(self.game_dir, 'nosmania_launcher.json'), encoding='utf-8') as fh:
            self.assertGreater(json.load(fh)['stamp'], first)

    def test_editor_gets_the_language(self):
        from qt_editor import launcher
        i18n.set_lang('en')
        editor = os.path.join(self.tmp, 'Nos Chart Maker 9.exe')
        open(editor, 'wb').close()
        settings._data['editor_exe_path'] = editor
        root = launcher.L.default_root()
        library = ['--library', root] if root else []
        self.assertEqual(launcher.editor_command('a.json'), [editor, '--lang', 'en'] + library + ['a.json'])
        self.send_mock.return_value = True
        self.home.open_editor('a.json')
        expected = {'open': 'a.json', 'lang': 'en'}
        if root:
            expected['library'] = root
        self.send_mock.assert_called_with(expected)


class EditorLanguageArgTests(unittest.TestCase):
    def test_pop_lang_arg(self):
        from qt_editor.app import pop_lang_arg
        argv = ['app', '--lang', 'en', 'x.json']
        self.assertEqual(pop_lang_arg(argv), 'en')
        self.assertEqual(argv, ['app', 'x.json'])
        argv = ['app', '--lang=zh_cn']
        self.assertEqual(pop_lang_arg(argv), 'zh_cn')
        self.assertEqual(argv, ['app'])
        self.assertEqual(pop_lang_arg(['app', '--lang', 'fr']), '')

    def test_ipc_language_message(self):
        from qt_editor.editor_ipc import EditorServer
        window = mock.Mock()
        server = EditorServer.__new__(EditorServer)
        server._window = window
        server.handle(b'{"lang": "en", "open": "a.json"}')
        window.apply_language.assert_called_once_with('en')
        window.open_chart_from_library.assert_called_once_with('a.json')

    def test_apply_language_retranslates(self):
        from qt_editor.main_window import MainWindow
        saved = settings._data.get('language')
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(settings, 'save'):
            win = MainWindow()
            try:
                win.apply_language('en')
                menus = [a.text() for a in win.menuBar().actions()]
                self.assertIn('File(&F)', menus)
                self.assertEqual(i18n.get_lang(), 'en')
            finally:
                win.apply_language('zh_tw')
                win.view.model.dirty = False
                win.close()
                settings._data['language'] = saved if saved is not None else 'zh_tw'


class ExportToGameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = make_library(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dialog_uses_the_game_library_without_asking(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        dlg = ExportSongDialog(default_root=os.path.join(self.tmp, 'elsewhere'), game_root=self.root)
        self.assertTrue(dlg.exports_to_game())
        self.assertEqual(dlg.export_root(), self.root)
        self.assertFalse(dlg._btn_browse_root.isVisibleTo(dlg))
        dlg.close()

    def test_dialog_without_a_game_library_keeps_choosing(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        dlg = ExportSongDialog(default_root=self.tmp)
        self.assertFalse(dlg.exports_to_game())
        self.assertTrue(dlg._btn_browse_root.isVisibleTo(dlg))
        dlg.close()

    def test_game_library_root_needs_an_index(self):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            win = MainWindow()
        try:
            with mock.patch('qt_editor.song_library.default_root', return_value=self.root):
                self.assertEqual(win._game_library_root(), self.root)
            with mock.patch('qt_editor.song_library.default_root', return_value=self.tmp):
                self.assertEqual(win._game_library_root(), '')
        finally:
            win.view.model.dirty = False
            win.close()


if __name__ == '__main__':
    unittest.main()
