"""預覽（TTB）不該畫出隱藏音符。

隱藏音符不佔按鍵、玩家看不到，預覽的用途就是「這在遊戲裡長怎樣」。主編輯畫面
早就在非音高模式濾掉了（`chart_view._draw_notes`），但預覽這條路徑漏掉，所以
預覽會多出一堆遊戲裡不存在的音符。

濾在入口（建構子與 `set_notes`），不是在 `paintEvent`：`_draw_slide_bands`、
`_update_size` 也都讀 `self.notes`，一個一個加判斷遲早會漏掉一處。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.models import GNote
from qt_editor.preview_window import PreviewCanvas

_app = QApplication.instance() or QApplication([])


def note(idx, start, hidden=False, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 200, 200
    n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
    n.pitch, n.velocity = pitch, 90
    n.hidden = hidden
    return n


class PreviewSkipsHidden(unittest.TestCase):
    def notes(self):
        return [note(0, 0), note(1, 300, hidden=True),
                note(2, 600), note(3, 900, hidden=True)]

    def test_the_constructor_filters_them(self):
        canvas = PreviewCanvas(self.notes())
        self.assertEqual([n.idx for n in canvas.notes], [0, 2])

    def test_set_notes_filters_them_too(self):
        canvas = PreviewCanvas([])
        canvas.set_notes(self.notes())
        self.assertEqual([n.idx for n in canvas.notes], [0, 2])

    def test_a_chart_without_hidden_notes_is_untouched(self):
        plain = [note(0, 0), note(1, 300), note(2, 600)]
        canvas = PreviewCanvas(plain)
        self.assertEqual(len(canvas.notes), 3)

    def test_notes_without_the_attribute_survive(self):
        bare = GNote(None, 9)
        bare.start, bare.end, bare.gate = 0, 200, 200
        bare.min_key, bare.max_key, bare.note_type, bare.hand = 4, 6, 0, 0
        del bare.hidden
        canvas = PreviewCanvas([bare])
        self.assertEqual(len(canvas.notes), 1, 'getattr 的預設值要是 False')

    def test_every_hidden_note_is_gone(self):
        canvas = PreviewCanvas(self.notes())
        self.assertFalse(any(getattr(n, 'hidden', False) for n in canvas.notes))

    def test_all_hidden_leaves_an_empty_canvas(self):
        canvas = PreviewCanvas([note(0, 0, hidden=True), note(1, 300, hidden=True)])
        self.assertEqual(canvas.notes, [])


if __name__ == '__main__':
    unittest.main()
