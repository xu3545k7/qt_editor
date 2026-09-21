"""換音源：內建的、丟進資料夾的、或指定任何一個檔案。

以前音源打包在 exe 裡，看不到也換不掉。這裡釘住的是：選得到、選錯了不會
無聲、檔案不見了會自己退回內建。
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qt_editor import soundfonts as SF
from qt_editor.settings import settings


def fake_sf2(path, presets=(('Grand', 0, 0), ('Bright', 1, 0), ('Organ', 2, 8))):
    """做一個最小但合法的 SF2：RIFF/sfbk + LIST pdta + phdr。"""
    records = b''
    for name, number, bank in list(presets) + [('EOP', 0, 0)]:
        records += name.encode('latin1')[:20].ljust(20, b'\0')
        records += struct.pack('<HHH', number, bank, 0)
        records += struct.pack('<III', 0, 0, 0)
    phdr = b'phdr' + struct.pack('<I', len(records)) + records
    pdta = b'LIST' + struct.pack('<I', 4 + len(phdr)) + b'pdta' + phdr
    body = b'sfbk' + pdta
    data = b'RIFF' + struct.pack('<I', len(body)) + body
    Path(path).write_bytes(data)
    return Path(path)


class PresetReadingTests(unittest.TestCase):
    def test_reads_names_numbers_and_banks(self):
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'x.sf2'))
            self.assertEqual(SF.list_presets(path),
                             [('Grand', 0, 0), ('Bright', 1, 0), ('Organ', 2, 8)])

    def test_a_broken_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as root:
            bad = os.path.join(root, 'bad.sf2')
            Path(bad).write_bytes(b'not a soundfont at all')
            self.assertEqual(SF.list_presets(bad), [])
            self.assertEqual(SF.list_presets(os.path.join(root, 'missing.sf2')), [])

    def test_it_does_not_read_the_whole_file(self):
        """音源動輒一兩百 MB，列個下拉選單不能整份載入。"""
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'x.sf2'))
            real_read = Path.read_bytes
            with mock.patch.object(Path, 'read_bytes',
                                   side_effect=AssertionError('不可以整份讀')):
                SF.list_presets(path)
            self.assertTrue(callable(real_read))

    def test_default_preset_prefers_bank_zero(self):
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'x.sf2'),
                            presets=(('Organ', 5, 8), ('Piano', 2, 0)))
            self.assertEqual(SF.default_preset_for(path), 2)

    def test_the_builtin_soundfont_keeps_its_known_preset(self):
        """Nice-Steinway 的鋼琴是 preset 1（Bright Steinway），不是 0。
        遊戲端的取樣也是用這個 preset 烤的。"""
        self.assertEqual(SF.default_preset_for(Path('Nice-Steinway-v3.8.sf2')), 1)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self._saved = (settings.get(SF.SETTING_PATH), settings.get(SF.SETTING_PRESET))

    def tearDown(self):
        settings.set(SF.SETTING_PATH, self._saved[0])
        settings.set(SF.SETTING_PRESET, self._saved[1])

    def test_nothing_chosen_means_the_builtin_one(self):
        settings.set(SF.SETTING_PATH, '')
        settings.set(SF.SETTING_PRESET, -1)
        builtin = SF.builtin_soundfont()
        path, preset = SF.selected_soundfont()
        if builtin is None:
            self.skipTest('這台機器沒有內建音源')
        self.assertEqual(path, builtin[0])
        self.assertEqual(preset, builtin[1])

    def test_a_chosen_file_is_used(self):
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'mine.sf2'))
            SF.set_selection(path, 2)
            chosen, preset = SF.selected_soundfont()
            self.assertEqual(chosen, path)
            self.assertEqual(preset, 2)

    def test_a_missing_file_falls_back_instead_of_going_silent(self):
        settings.set(SF.SETTING_PATH, os.path.join('Z:', 'gone', 'nope.sf2'))
        settings.set(SF.SETTING_PRESET, 3)
        path, _preset = SF.selected_soundfont()
        builtin = SF.builtin_soundfont()
        if builtin is None:
            self.skipTest('這台機器沒有內建音源')
        self.assertEqual(path, builtin[0], '外接硬碟沒插就整個沒聲音是不行的')

    def test_auto_preset_follows_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'mine.sf2'),
                            presets=(('Organ', 7, 0),))
            SF.set_selection(path, -1)
            self.assertEqual(SF.selected_soundfont()[1], 7)

    def test_choosing_the_builtin_path_is_stored_as_builtin(self):
        """存絕對路徑的話，換一版程式（路徑不同）就會指到不存在的檔案。"""
        builtin = SF.builtin_soundfont()
        if builtin is None:
            self.skipTest('這台機器沒有內建音源')
        SF.set_selection(str(builtin[0]), -1)
        self.assertEqual(settings.get(SF.SETTING_PATH), '')

    def test_midi_preview_uses_the_selection(self):
        from qt_editor import midi_preview as MP
        with tempfile.TemporaryDirectory() as root:
            path = fake_sf2(os.path.join(root, 'mine.sf2'))
            SF.set_selection(path, 1)
            self.assertEqual(MP.default_soundfont(), (Path(path), 1))

    def test_broken_settings_do_not_break_the_preview(self):
        from qt_editor import midi_preview as MP
        with mock.patch.object(SF, 'selected_soundfont', side_effect=RuntimeError):
            path, _preset = MP.default_soundfont()
        self.assertTrue(str(path).endswith('.sf2'))


class UserFolderTests(unittest.TestCase):
    def test_the_folder_sits_next_to_the_exe_on_windows(self):
        exe = r'D:\Nostalgia\Nos Chart Maker 10.0.exe'
        with mock.patch.object(SF, 'IS_MAC', False), \
                mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', exe):
            self.assertEqual(SF.user_dir(),
                             Path(os.path.dirname(exe)) / 'soundfonts')

    def test_the_folder_is_outside_the_app_bundle_on_mac(self):
        exe = '/Applications/Nos Chart Maker.app/Contents/MacOS/Nos Chart Maker'
        with mock.patch.object(SF, 'IS_MAC', True), \
                mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', exe), \
                mock.patch.object(SF, 'user_data_dir', return_value='/Users/x/Support'):
            folder = SF.user_dir()
        self.assertNotIn('.app/Contents', str(folder))
        self.assertEqual(folder, Path('/Users/x/Support') / 'soundfonts')

    def test_files_dropped_in_the_folder_show_up(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sf2(os.path.join(root, 'b.sf2'))
            fake_sf2(os.path.join(root, 'a.sf2'))
            Path(os.path.join(root, 'notes.txt')).write_text('x')
            with mock.patch.object(SF, 'user_dir', return_value=Path(root)):
                names = [p.name for p in SF.user_soundfonts()]
        self.assertEqual(names, ['a.sf2', 'b.sf2'], '照檔名排序，且只收 .sf2')

    def test_the_list_starts_with_the_builtin_one(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sf2(os.path.join(root, 'mine.sf2'))
            with mock.patch.object(SF, 'user_dir', return_value=Path(root)):
                choices = SF.available_soundfonts()
        if SF.builtin_soundfont() is not None:
            self.assertTrue(choices[0].builtin)
            self.assertEqual(choices[0].key, '', '內建存空字串')
        self.assertIn('mine.sf2', [c.path.name for c in choices])

    def test_ensure_user_dir_leaves_a_note(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'soundfonts'
            with mock.patch.object(SF, 'user_dir', return_value=folder):
                SF.ensure_user_dir()
            self.assertTrue(folder.is_dir())
            self.assertTrue((folder / SF.README_NAME).is_file(),
                            '資料夾裡要有一張說明，不然沒人知道那是幹嘛的')

    def test_a_read_only_location_is_not_a_crash(self):
        with mock.patch.object(Path, 'mkdir', side_effect=OSError):
            SF.ensure_user_dir()

    def test_exporting_the_builtin_one(self):
        with tempfile.TemporaryDirectory() as root:
            src = fake_sf2(os.path.join(root, 'built-in.sf2'))
            dest = Path(root) / 'out'
            with mock.patch.object(SF, 'builtin_soundfont', return_value=(src, 1)):
                target = SF.export_builtin(dest)
            self.assertEqual(target, dest / 'built-in.sf2')
            self.assertTrue(target.is_file())

    def test_exporting_does_not_overwrite_what_is_already_there(self):
        with tempfile.TemporaryDirectory() as root:
            src = fake_sf2(os.path.join(root, 'built-in.sf2'))
            dest = Path(root) / 'out'
            dest.mkdir()
            (dest / 'built-in.sf2').write_bytes(b'mine, edited')
            with mock.patch.object(SF, 'builtin_soundfont', return_value=(src, 1)):
                SF.export_builtin(dest)
            self.assertEqual((dest / 'built-in.sf2').read_bytes(), b'mine, edited')


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._saved = (settings.get(SF.SETTING_PATH), settings.get(SF.SETTING_PRESET))

    def tearDown(self):
        settings.set(SF.SETTING_PATH, self._saved[0])
        settings.set(SF.SETTING_PRESET, self._saved[1])

    def dialog(self):
        from qt_editor.settings_dialog import SettingsDialog
        self._dlg = SettingsDialog(None)
        return self._dlg

    def test_the_rows_are_there(self):
        dlg = self.dialog()
        self.assertGreater(dlg._sf_combo.count(), 0)
        self.assertEqual(dlg._sf_preset.itemData(0), -1, '第一個是「跟著音源」')

    def test_presets_are_listed_for_the_current_file(self):
        dlg = self.dialog()
        if SF.builtin_soundfont() is None:
            self.skipTest('這台機器沒有內建音源')
        self.assertGreater(dlg._sf_preset.count(), 1, '應該列得出音色')

    def test_accept_saves_the_choice(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(fake_sf2(os.path.join(root, 'mine.sf2')))
            dlg = self.dialog()
            dlg._sf_combo.addItem('mine.sf2', path)
            dlg._sf_combo.setCurrentIndex(dlg._sf_combo.count() - 1)
            index = dlg._sf_preset.findData(1)
            if index >= 0:
                dlg._sf_preset.setCurrentIndex(index)
            dlg._on_accept()
            self.assertEqual(settings.get(SF.SETTING_PATH), path)

    def test_switching_files_reloads_the_presets(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(fake_sf2(os.path.join(root, 'mine.sf2'),
                                presets=(('Only One', 4, 0),)))
            dlg = self.dialog()
            dlg._sf_combo.addItem('mine.sf2', path)
            dlg._sf_combo.setCurrentIndex(dlg._sf_combo.count() - 1)
            labels = [dlg._sf_preset.itemText(i) for i in range(dlg._sf_preset.count())]
        self.assertTrue(any('Only One' in x for x in labels), labels)


if __name__ == '__main__':
    unittest.main()
