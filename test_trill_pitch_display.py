"""顫音的音高顯示。

回報：顫音上的數字和旁邊音符對不上。原因是一般音符走 `_pitch_label`（預設顯示
遊戲的 scale_piano 1～88），顫音格子卻直接印 MIDI 音高，差 20。另外音高模式下
79／81 交替的顫音整條擠在寄主那一格，看不出在彈哪兩個音。
"""

import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def sub(start, scale_piano):
    se = ET.Element('sub_note')
    for tag, val, ty in (('start_timing_msec', start, 's32'), ('end_timing_msec', start + 80, 's32'),
                         ('scale_piano', scale_piano, 'u8'), ('velocity', 90, 'u8'),
                         ('track_index', 1, 's32')):
        el = ET.SubElement(se, tag)
        el.text, el.attrib['__type'] = str(val), ty
    return se


class TrillPitchDisplayTests(unittest.TestCase):
    def setUp(self):
        self.view = ChartView()
        self.view.resize(1300, 720)
        self.model = NoteModel.create_new('t', 120.0, 10.0, 4)
        trill = GNote(None, 0)
        trill.start, trill.end, trill.gate = 1000, 1800, 800
        trill.pitch, trill.hand, trill.note_type = 81, 0, 64
        trill.min_key, trill.max_key = 20, 23
        # 官方譜的寫法：scale_piano 59／61 = MIDI 79／81 交替
        trill.sub_elems = [sub(1000 + i * 100, 59 if i % 2 == 0 else 61) for i in range(8)]
        self.trill = trill
        self.model.notes_tree = [trill]
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        for _ in range(3):
            self.view.zoom(0.5)
        self.view.follow_to_ms(1400)

    def labels(self):
        painter = mock.MagicMock()
        x1, x2 = self.view._note_display_x_range(self.trill)
        with mock.patch.object(self.view, '_font_pitch', create=True):
            self.view._draw_trill_mesh(painter, self.trill, x1, x2 - x1)
        texts = [c.args[2] for c in painter.drawText.call_args_list if len(c.args) >= 3]
        rects = [c.args[0] for c in painter.drawRect.call_args_list]
        return texts, rects

    def test_labels_use_the_same_numbering_as_other_notes(self):
        self.view.show_midi_pitch = False
        texts, _ = self.labels()
        self.assertTrue(texts)
        self.assertEqual(set(texts), {'59', '61'})

    def test_labels_follow_the_midi_numbering_switch(self):
        self.view.show_midi_pitch = True
        texts, _ = self.labels()
        self.assertEqual(set(texts), {'79', '81'})

    def test_pitch_mode_spans_both_notes(self):
        self.view.set_view_mode('pitch')
        x1, x2 = self.view._note_display_x_range(self.trill)
        left79, _ = self.view._key_span(self.view._pitch_to_slot(79))
        _, right81 = self.view._key_span(self.view._pitch_to_slot(81))
        self.assertLessEqual(x1, left79 + 3)
        self.assertGreaterEqual(x2, right81 - 3)

    def test_pitch_mode_puts_each_stroke_on_its_own_pitch(self):
        self.view.set_view_mode('pitch')
        _texts, rects = self.labels()
        cell_lefts = sorted({round(r.left()) for r in rects if r.height() < 300 and r.width() < 40})
        self.assertGreaterEqual(len(cell_lefts), 2, '79 和 81 要在不同的兩格')

    def test_measure_mode_is_unchanged(self):
        x1, x2 = self.view._note_display_x_range(self.trill)
        left, _ = self.view._key_span(20)
        _, right = self.view._key_span(23)
        self.assertLessEqual(abs(x1 - left), 40)
        self.assertLessEqual(abs(x2 - right), 40)


if __name__ == '__main__':
    unittest.main()
