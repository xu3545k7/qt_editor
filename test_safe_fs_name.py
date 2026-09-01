import os
import tempfile
import unittest

from qt_editor.main_window import safe_fs_name


class SafeFsNameTests(unittest.TestCase):
    def test_slash_in_song_title(self):
        # 造成 WinError 3 的實際案例：曲名含 '/' 被當成路徑分隔符
        self.assertEqual(safe_fs_name('Pure White / 純白'), 'Pure White _ 純白')

    def test_all_windows_illegal_chars_replaced(self):
        self.assertEqual(safe_fs_name('A:B*C?D"E<F>G|H'), 'A_B_C_D_E_F_G_H')
        self.assertEqual(safe_fs_name('back\\slash'), 'back_slash')

    def test_clean_names_unchanged(self):
        # 既有曲目資料夾行為不可改變
        for name in ('Melodiniq', 'Break_Through_the_Dome', '純白', 'Normal Song'):
            self.assertEqual(safe_fs_name(name), name)

    def test_trailing_dot_and_space_stripped(self):
        # Windows 不允許結尾的句點/空白
        self.assertEqual(safe_fs_name('trailing dots...'), 'trailing dots')
        self.assertEqual(safe_fs_name('trailing space   '), 'trailing space')

    def test_empty_falls_back(self):
        self.assertEqual(safe_fs_name(''), 'untitled')
        self.assertEqual(safe_fs_name('   '), 'untitled')
        self.assertEqual(safe_fs_name('...'), 'untitled')
        self.assertEqual(safe_fs_name('', fallback='Normal'), 'Normal')

    def test_sanitized_name_is_actually_creatable(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, safe_fs_name('Pure White / 純白'))
        os.makedirs(os.path.join(folder, 'Real'), exist_ok=True)
        self.assertTrue(os.path.isdir(os.path.join(folder, 'Real')))


if __name__ == '__main__':
    unittest.main()
