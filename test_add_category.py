# -*- coding: utf-8 -*-
"""製譜器自己也要能新增曲庫分類。

使用者：「qt_editor 也要能新增分類」。以前只有「管理曲庫」和遊戲設定裡的曲庫
面板能建，可是想到要分類的時機就是排完譜要匯出的那一刻。

兩條新路：製譜器的 工具 → 曲庫 → 新增分類…，和匯出對話框分類欄旁邊那顆
「新增…」。兩條都走 `SongLibrary.add_category`——三個地方寫出來的 library.json
必須是同一種東西，所以這裡釘住的是「有沒有走同一個入口」和「名稱規則有沒有
生效」，而不是各自重寫一份驗證。
"""

import json
import os
import shutil
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.song_library import (DEFAULT_CATEGORY, INDEX_FILE, LibraryError,
                                    SongLibrary)

_app = QApplication.instance() or QApplication([])


def library(root, categories=('古典',)):
    """一個最小的曲庫：有 library.json 才算數。"""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, INDEX_FILE), 'w', encoding='utf-8') as fh:
        json.dump({'songs': [], 'categories': list(categories)}, fh)
    return root


def index_of(root):
    with open(os.path.join(root, INDEX_FILE), encoding='utf-8') as fh:
        return json.load(fh)


class TheLibraryRuleIsTheOneRuleTests(unittest.TestCase):
    """三條路共用 `SongLibrary.add_category`，所以規則只有一份。"""

    def setUp(self):
        self.root = library(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_new_name_lands_in_the_index(self):
        SongLibrary(self.root).add_category('練習曲')
        self.assertIn('練習曲', index_of(self.root)['categories'])

    def test_a_duplicate_is_refused(self):
        with self.assertRaises(LibraryError):
            SongLibrary(self.root).add_category('古典')

    def test_the_default_category_is_refused(self):
        with self.assertRaises(LibraryError):
            SongLibrary(self.root).add_category(DEFAULT_CATEGORY)

    def test_path_characters_are_refused(self):
        """分類名稱會變成選曲畫面的頁籤，但也可能被當路徑用。"""
        for bad in ('a/b', 'a:b', 'a?b', 'a|b', 'a"b'):
            with self.assertRaises(LibraryError, msg=bad):
                SongLibrary(self.root).add_category(bad)

    def test_an_empty_name_is_refused(self):
        with self.assertRaises(LibraryError):
            SongLibrary(self.root).add_category('   ')

    def test_the_game_is_told_the_library_changed(self):
        """建完分類遊戲要知道——不然選曲畫面要重開才看得到新頁籤。"""
        from qt_editor.song_library import REVISION_FILE
        SongLibrary(self.root).add_category('練習曲')
        path = os.path.join(self.root, REVISION_FILE)
        self.assertTrue(os.path.isfile(path), '應該要留下「曲庫改過了」的記號')
        with open(path, encoding='utf-8') as fh:
            self.assertGreaterEqual(int(json.load(fh).get('revision', 0)), 1)


class TheExportDialogCanMakeOneTests(unittest.TestCase):
    def setUp(self):
        self.root = library(tempfile.mkdtemp())
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for path in (self.root, self.dir):
            shutil.rmtree(path, ignore_errors=True)

    def dialog(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        dlg = ExportSongDialog(None, offset_ms=0, wav_path='',
                               chart_json_path='', default_root=self.root,
                               game_root='')
        dlg._export_root = self.root
        dlg._reload_categories()
        return dlg

    def test_the_button_is_next_to_the_box(self):
        """打字也能建，但那條路不檢查名稱——按鈕是走規則的那條。"""
        from PyQt5.QtWidgets import QPushButton
        dlg = self.dialog()
        row = dlg._cb_category.parentWidget()
        labels = [b.text() for b in row.findChildren(QPushButton)]
        self.assertIn('新增…', labels, '分類欄旁邊要有那顆按鈕')

    def test_the_existing_categories_are_listed(self):
        dlg = self.dialog()
        shown = [dlg._cb_category.itemText(i)
                 for i in range(dlg._cb_category.count())]
        self.assertIn('古典', shown)

    def test_making_one_selects_it(self):
        from unittest import mock
        dlg = self.dialog()
        with mock.patch('PyQt5.QtWidgets.QInputDialog.getText',
                        return_value=('練習曲', True)):
            dlg._new_category()
        self.assertEqual(dlg._cb_category.currentText(), '練習曲')
        self.assertIn('練習曲', index_of(self.root)['categories'])

    def test_cancelling_changes_nothing(self):
        from unittest import mock
        dlg = self.dialog()
        before = index_of(self.root)['categories']
        with mock.patch('PyQt5.QtWidgets.QInputDialog.getText',
                        return_value=('練習曲', False)):
            dlg._new_category()
        self.assertEqual(index_of(self.root)['categories'], before)

    def test_a_bad_name_is_reported_not_written(self):
        from unittest import mock
        dlg = self.dialog()
        before = index_of(self.root)['categories']
        with mock.patch('PyQt5.QtWidgets.QInputDialog.getText',
                        return_value=('a/b', True)), \
                mock.patch('PyQt5.QtWidgets.QMessageBox.warning') as warn:
            dlg._new_category()
        self.assertTrue(warn.called, '名稱不合格要講出來')
        self.assertEqual(index_of(self.root)['categories'], before)

    def test_no_export_root_means_a_message_not_a_crash(self):
        from unittest import mock
        dlg = self.dialog()
        dlg._export_root = ''
        with mock.patch('PyQt5.QtWidgets.QMessageBox.information') as info:
            dlg._new_category()
        self.assertTrue(info.called)


class TheEditorMenuCanMakeOneTests(unittest.TestCase):
    def setUp(self):
        self.root = library(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def window(self):
        import contextlib
        import io as _io
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        self.addCleanup(lambda: (setattr(win.view.model, 'dirty', False),
                                 win.close()))
        return win

    def test_the_tools_menu_offers_it(self):
        win = self.window()
        labels = [label for _group, entries in win._tool_groups()
                  for label, _slot in entries]
        self.assertIn('新增分類…', labels)

    def test_it_writes_through_the_library(self):
        from unittest import mock
        win = self.window()
        with mock.patch.object(win, '_game_library_root', return_value=self.root), \
                mock.patch('PyQt5.QtWidgets.QInputDialog.getText',
                           return_value=('練習曲', True)), \
                mock.patch('PyQt5.QtWidgets.QMessageBox.information'):
            win.add_library_category()
        self.assertIn('練習曲', index_of(self.root)['categories'])

    def test_a_folder_that_is_not_a_library_is_refused(self):
        from unittest import mock
        win = self.window()
        plain = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(plain, ignore_errors=True))
        with mock.patch.object(win, '_game_library_root', return_value=''), \
                mock.patch('qt_editor.main_window.settings.get', return_value=''), \
                mock.patch('PyQt5.QtWidgets.QFileDialog.getExistingDirectory',
                           return_value=plain), \
                mock.patch('PyQt5.QtWidgets.QMessageBox.warning') as warn:
            win.add_library_category()
        self.assertTrue(warn.called, '沒有 library.json 的資料夾不是曲庫')

    def test_cancelling_the_name_changes_nothing(self):
        from unittest import mock
        win = self.window()
        before = index_of(self.root)['categories']
        with mock.patch.object(win, '_game_library_root', return_value=self.root), \
                mock.patch('PyQt5.QtWidgets.QInputDialog.getText',
                           return_value=('', False)):
            win.add_library_category()
        self.assertEqual(index_of(self.root)['categories'], before)


if __name__ == '__main__':
    unittest.main()
