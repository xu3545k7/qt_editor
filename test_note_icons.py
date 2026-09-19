"""放置模式的音符圖示：每種類型都畫得出來、左右手顏色不同、工具列跟著手換色。"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.note_icons import note_type_pixmap  # noqa: E402

TYPES = (0, 1, 2, 3, 4, 64)


def opaque_pixels(pix):
    img = pix.toImage()
    return sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
               if (img.pixel(x, y) >> 24) & 0xFF > 40)


class IconTests(unittest.TestCase):
    def test_every_type_draws_something(self):
        for nt in TYPES:
            for hand in (0, 1):
                self.assertGreater(opaque_pixels(note_type_pixmap(nt, hand)), 50,
                                   f'type {nt} hand {hand} 素材沒載到')

    def test_types_look_different(self):
        images = [note_type_pixmap(nt, 0).toImage() for nt in TYPES]
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                self.assertNotEqual(images[i], images[j], (TYPES[i], TYPES[j]))

    def test_hands_look_different(self):
        for nt in TYPES:
            self.assertNotEqual(note_type_pixmap(nt, 0).toImage(),
                                note_type_pixmap(nt, 1).toImage(), nt)


class NoteValueIconTests(unittest.TestCase):
    def test_shapes(self):
        from qt_editor.note_icons import _value_shape
        self.assertEqual(_value_shape(4.0), (True, False, 0, 0))          # 全音符
        self.assertEqual(_value_shape(2.0), (True, True, 0, 0))           # 二分
        self.assertEqual(_value_shape(1.0), (False, True, 0, 0))          # 四分
        self.assertEqual(_value_shape(0.5), (False, True, 1, 0))          # 八分
        self.assertEqual(_value_shape(0.0625), (False, True, 4, 0))       # 64 分
        self.assertEqual(_value_shape(2.0 / 3.0), (False, True, 0, 3))     # 四分三連
        self.assertEqual(_value_shape(1.0 / 6.0), (False, True, 2, 3))     # 16 分三連
        self.assertEqual(_value_shape(4.0 / 20), (False, True, 2, 5))      # 20 分 = 五連 16 分
        self.assertEqual(_value_shape(4.0 / 7), (False, True, 0, 7))       # 7 分 = 七連四分
        self.assertEqual(_value_shape(4.0 / 96), (False, True, 4, 3))      # 96 分 = 64 分三連
        self.assertEqual(_value_shape(4.0 / 128), (False, True, 4, 0))     # 超過 64 分就畫 4 條符尾

    def test_every_value_is_distinct(self):
        from qt_editor.note_icons import note_value_pixmap
        values = [4.0, 2.0, 1.0, 2 / 3, 0.5, 1 / 3, 0.25, 1 / 6, 0.125, 1 / 12, 0.0625]
        images = [note_value_pixmap(v).toImage() for v in values]
        for v in values:
            self.assertGreater(opaque_pixels(note_value_pixmap(v)), 20, v)
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                if values[i] in (2 / 3, 1 / 3, 1 / 6, 1 / 12) or values[j] in (2 / 3, 1 / 3, 1 / 6, 1 / 12):
                    continue          # 三連音的 3 是文字，offscreen 沒字型畫不出來
                self.assertNotEqual(images[i], images[j], (values[i], values[j]))


class ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()

    def icon_image(self, combo, i):
        return combo.itemIcon(i).pixmap(combo.iconSize()).toImage()

    def test_type_icons_follow_the_hand(self):
        combo = self.win._toolbars[0].type_combo
        self.win._on_hand_combo_changed(0)
        right = self.icon_image(combo, 2)
        self.win._on_hand_combo_changed(1)
        left = self.icon_image(combo, 2)
        self.assertFalse(combo.itemIcon(2).isNull())
        self.assertNotEqual(right, left)
        for tbs in self.win._toolbars:                 # 兩條工具列都要換
            self.assertEqual(self.icon_image(tbs.type_combo, 2), left)
        self.win._on_hand_combo_changed(0)

    def test_duration_menus_have_icons(self):
        tbs = self.win._toolbars[0]
        for combo in (tbs.dur_combo, tbs.pattern_step_combo):
            for i in range(combo.count()):
                if combo.itemText(i) == '自訂…':
                    continue                           # 輸入用的那一項沒有固定音符
                self.assertFalse(combo.itemIcon(i).isNull(), combo.itemText(i))

    def test_hand_combo_has_icons(self):
        combo = self.win._toolbars[0].hand_combo
        self.assertFalse(combo.itemIcon(0).isNull())
        self.assertFalse(combo.itemIcon(1).isNull())


if __name__ == '__main__':
    unittest.main()
