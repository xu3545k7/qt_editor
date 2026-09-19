"""改音高不要重排譜面：音高模式用方向鍵調音時，音符留在原本的鍵道上。

鍵道是排譜的結果，音高只是那顆音彈什麼——修一個音高就把整段的鍵道重排，
排好的譜面等於被打掉。放置模式是在排版，那時候鍵道才跟著音高走。
"""

import unittest

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])


class PitchKeepsLanesTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings._data.get('pitch_edit_moves_lanes')
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        note = GNote(None, 0)
        note.start, note.end, note.gate = 1000, 1200, 200
        note.min_key, note.max_key, note.note_type, note.hand = 4, 6, 0, 0
        note.pitch, note.velocity = 60, 90
        self.model.notes_tree = [note]
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        self.view.selected = {note.idx}
        self.note = note

    def tearDown(self):
        if self._saved is None:
            settings._data.pop('pitch_edit_moves_lanes', None)
        else:
            settings._data['pitch_edit_moves_lanes'] = self._saved

    def lanes(self):
        return (self.note.min_key, self.note.max_key)

    def arrow(self, key=Qt.Key_Right):
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))

    def test_pitch_changes_but_lanes_stay(self):
        before = self.lanes()
        self.arrow()
        self.assertEqual(self.note.pitch, 61)
        self.assertEqual(self.lanes(), before, '調音高不該把音符搬到別的鍵道')

    def test_a_big_jump_keeps_the_lanes_too(self):
        before = self.lanes()
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.ShiftModifier))
        self.assertEqual(self.note.pitch, 70)
        self.assertEqual(self.lanes(), before)

    def test_placement_mode_still_moves_the_lanes(self):
        self.view.set_note_input_mode(True)
        before = self.lanes()
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.ShiftModifier))
        self.assertNotEqual(self.lanes(), before, '排版的時候鍵道要跟著音高走')

    def test_the_setting_turns_relayout_back_on(self):
        settings._data['pitch_edit_moves_lanes'] = True
        before = self.lanes()
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.ShiftModifier))
        self.assertNotEqual(self.lanes(), before)

    def test_the_note_width_is_preserved(self):
        settings._data['pitch_edit_moves_lanes'] = True
        width = self.note.max_key - self.note.min_key
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.ShiftModifier))
        self.assertEqual(self.note.max_key - self.note.min_key, width)

    def test_snapping_to_the_key_keeps_the_lanes(self):
        from qt_editor.music_theory import Key
        self.note.pitch = 61                      # C 大調的離調音
        before = self.lanes()
        moved = self.view.snap_selected_to_key(Key(0, 'major'))
        self.assertEqual(moved, 1)
        self.assertEqual(self.note.pitch, 62, 'snap_to_key 預設往上吸')
        self.assertEqual(self.lanes(), before)


if __name__ == '__main__':
    unittest.main()
