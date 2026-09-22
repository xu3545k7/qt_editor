"""跨視窗傳音符：複製走系統剪貼簿，另一個編輯器（另一個行程）才貼得到。

`ChartView.clipboard` 只活在自己的行程裡，所以開兩個編輯器時複製完全傳不過去。
這裡用「兩個各自獨立的 ChartView + 清掉行程內剪貼簿」來模擬另一個視窗。
"""

import json
import unittest

from PyQt5.QtCore import QMimeData
from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel


def chart(count=4, pitch0=60):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    notes = []
    for i in range(count):
        n = GNote(None, i)
        n.start, n.end, n.gate = 1000 + i * 500, 1000 + i * 500 + 250, 250
        n.pitch = pitch0 + i
        n.hand = i % 2
        n.min_key, n.max_key = 5 + i, 7 + i
        n.note_type = 2 if i % 2 else 0
        n.velocity = 80 + i
        n.channel = 0
        n.off_velocity = 0
        n.track = 1
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class CrossWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        QApplication.clipboard().clear()

    def view(self, model=None):
        v = ChartView()
        v.resize(600, 800)
        v.load_model(model or chart())
        return v

    def test_copy_writes_the_notes_to_the_system_clipboard(self):
        v = self.view()
        v.selected = {n.idx for n in v.model.notes_tree[:2]}
        v.copy_to_clipboard()
        text = QApplication.clipboard().text()
        payload = json.loads(text)
        self.assertEqual(payload['format'], ChartView.CLIPBOARD_TAG)
        self.assertEqual(payload['count'], 2)
        self.assertEqual(len(payload['notes']), 2)

    def test_another_window_can_paste_them(self):
        source = self.view()
        source.selected = {n.idx for n in source.model.notes_tree[:3]}
        source.copy_to_clipboard()

        other = self.view(chart(count=1, pitch0=48))     # 另一個視窗、另一份譜
        other.clipboard = []                             # 行程內剪貼簿是空的
        before = len(other.model.notes_tree)
        other.paste_from_clipboard()
        self.assertEqual(len(other.model.notes_tree), before + 3,
                         '另一個視窗要貼得出來')

    def test_what_arrives_is_what_was_copied(self):
        source = self.view()
        picked = source.model.notes_tree[1]
        source.selected = {picked.idx}
        source.copy_to_clipboard()

        other = self.view(chart(count=1, pitch0=48))
        other.clipboard = []
        other.paste_from_clipboard()
        pasted = [n for n in other.model.notes_tree
                  if n.pitch == picked.pitch and n.idx in other.selected]
        self.assertEqual(len(pasted), 1)
        got = pasted[0]
        self.assertEqual(int(got.min_key), int(picked.min_key))
        self.assertEqual(int(got.max_key), int(picked.max_key))
        self.assertEqual(int(got.note_type), int(picked.note_type))
        self.assertEqual(int(got.hand), int(picked.hand))
        self.assertEqual(int(got.velocity), int(picked.velocity))
        self.assertEqual(int(got.end) - int(got.start),
                         int(picked.end) - int(picked.start), '長度要一樣')

    def test_relative_timing_survives(self):
        source = self.view()
        source.selected = {n.idx for n in source.model.notes_tree[:3]}
        source.copy_to_clipboard()
        gaps = [int(n.start) for n in source.model.notes_tree[:3]]
        gaps = [t - gaps[0] for t in gaps]

        other = self.view(chart(count=1, pitch0=48))
        other.clipboard = []
        other.paste_from_clipboard()
        new = sorted((n for n in other.model.notes_tree if n.idx in other.selected),
                     key=lambda n: int(n.start))
        got = [int(n.start) - int(new[0].start) for n in new]
        self.assertEqual(got, gaps, '幾顆音之間的間隔不能跑掉')

    def test_pasting_is_undoable(self):
        source = self.view()
        source.selected = {n.idx for n in source.model.notes_tree[:2]}
        source.copy_to_clipboard()
        other = self.view(chart(count=1, pitch0=48))
        other.clipboard = []
        before = len(other.model.notes_tree)
        other.paste_from_clipboard()
        self.assertTrue(other.model.undo())
        self.assertEqual(len(other.model.notes_tree), before)

    def test_text_from_another_program_is_ignored(self):
        v = self.view()
        QApplication.clipboard().setText('隨便一段別的程式複製的文字')
        v.clipboard = []
        before = len(v.model.notes_tree)
        v.paste_from_clipboard()
        self.assertEqual(len(v.model.notes_tree), before,
                         '別的程式的文字不可以被當成音符貼進來')

    def test_broken_payload_is_ignored(self):
        v = self.view()
        QApplication.clipboard().setText(json.dumps({
            'format': ChartView.CLIPBOARD_TAG,
            'notes': [{'rel_start': 0}],          # 少了大半欄位
        }))
        v.clipboard = []
        before = len(v.model.notes_tree)
        v.paste_from_clipboard()
        self.assertEqual(len(v.model.notes_tree), before)

    def test_the_in_process_clipboard_still_works_without_the_system_one(self):
        """剪貼簿被別的程式蓋掉時，自己複製過的東西還是貼得出來。"""
        v = self.view()
        v.selected = {n.idx for n in v.model.notes_tree[:2]}
        v.copy_to_clipboard()
        QApplication.clipboard().setText('別的東西')
        before = len(v.model.notes_tree)
        v.paste_from_clipboard()
        self.assertEqual(len(v.model.notes_tree), before + 2)

    def test_copying_nothing_leaves_the_clipboard_alone(self):
        v = self.view()
        QApplication.clipboard().setText('保持原樣')
        v.selected = set()
        v.copy_to_clipboard()
        self.assertEqual(QApplication.clipboard().text(), '保持原樣')


class MidiClipboardTests(unittest.TestCase):
    """MIDI 也要能貼：自己複製時附一份 MIDI，別人放的 MIDI 我們也收。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        QApplication.clipboard().clear()

    def view(self, model=None):
        v = ChartView()
        v.resize(600, 800)
        v.load_model(model or chart())
        return v

    def midi_bytes_for(self, notes):
        """做一份 MIDI，模擬別的程式放進剪貼簿的內容。"""
        view = self.view()
        return view._notes_to_midi_bytes(notes)

    def test_copy_also_puts_midi_on_the_clipboard(self):
        v = self.view()
        v.selected = {n.idx for n in v.model.notes_tree[:3]}
        v.copy_to_clipboard()
        mime = QApplication.clipboard().mimeData()
        self.assertTrue(any(mime.hasFormat(name) for name in ChartView.MIDI_MIME_TYPES),
                        '剪貼簿上要有一份 MIDI')
        data = bytes(mime.data('audio/midi'))
        self.assertEqual(data[:4], b'MThd', '要是真的 MIDI 檔')

    def test_the_midi_we_write_round_trips(self):
        v = self.view()
        picked = v.model.notes_tree[:3]
        v.selected = {n.idx for n in picked}
        v.copy_to_clipboard()
        data = bytes(QApplication.clipboard().mimeData().data('audio/midi'))
        back = v._notes_from_midi_bytes(data)
        self.assertEqual([d['pitch'] for d in back], [int(n.pitch) for n in picked])
        gaps = [int(n.start) - int(picked[0].start) for n in picked]
        self.assertEqual([d['rel_start'] for d in back], gaps)

    def test_midi_from_another_program_can_be_pasted(self):
        source = [{'rel_start': 0, 'rel_end': 400, 'pitch': 64, 'velocity': 100},
                  {'rel_start': 500, 'rel_end': 900, 'pitch': 67, 'velocity': 90}]
        data = self.midi_bytes_for(source)
        mime = QMimeData()
        mime.setData('audio/midi', data)
        QApplication.clipboard().setMimeData(mime)

        v = self.view()
        v.clipboard = []
        before = len(v.model.notes_tree)
        v.paste_from_clipboard()
        self.assertEqual(len(v.model.notes_tree), before + 2)
        pasted = sorted((n for n in v.model.notes_tree if n.idx in v.selected),
                        key=lambda n: int(n.start))
        self.assertEqual([int(n.pitch) for n in pasted], [64, 67])
        self.assertEqual(int(pasted[1].start) - int(pasted[0].start), 500)

    def test_a_long_midi_note_arrives_as_a_hold(self):
        data = self.midi_bytes_for(
            [{'rel_start': 0, 'rel_end': 2000, 'pitch': 60, 'velocity': 100}])
        mime = QMimeData()
        mime.setData('audio/midi', data)
        QApplication.clipboard().setMimeData(mime)
        v = self.view()
        v.clipboard = []
        v.paste_from_clipboard()
        pasted = [n for n in v.model.notes_tree if n.idx in v.selected]
        self.assertEqual(int(pasted[0].note_type), 2)

    def test_a_midi_file_copied_in_explorer_can_be_pasted(self):
        import os
        import tempfile

        from PyQt5.QtCore import QUrl

        data = self.midi_bytes_for(
            [{'rel_start': 0, 'rel_end': 300, 'pitch': 72, 'velocity': 100}])
        handle, path = tempfile.mkstemp(suffix='.mid')
        with os.fdopen(handle, 'wb') as fh:
            fh.write(data)
        try:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(path)])
            QApplication.clipboard().setMimeData(mime)
            v = self.view()
            v.clipboard = []
            before = len(v.model.notes_tree)
            v.paste_from_clipboard()
            self.assertEqual(len(v.model.notes_tree), before + 1)
        finally:
            os.unlink(path)

    def test_our_own_json_wins_over_the_midi_copy(self):
        """同一次複製兩種格式都在，要用 JSON（帶鍵道與左右手，資訊比較全）。"""
        v = self.view()
        picked = v.model.notes_tree[1]
        v.selected = {picked.idx}
        v.copy_to_clipboard()
        other = self.view(chart(count=1, pitch0=48))
        other.clipboard = []
        other.paste_from_clipboard()
        pasted = [n for n in other.model.notes_tree if n.idx in other.selected][0]
        self.assertEqual(int(pasted.min_key), int(picked.min_key), '鍵道要保住')
        self.assertEqual(int(pasted.max_key), int(picked.max_key))

    def test_rubbish_bytes_are_not_treated_as_midi(self):
        v = self.view()
        self.assertIsNone(v._notes_from_midi_bytes(b'not a midi file at all'))
        self.assertIsNone(v._notes_from_midi_bytes(b''))


if __name__ == '__main__':
    unittest.main()
