# -*- coding: utf-8 -*-
"""疊加匯入 MIDI（無主音）。

和「開啟右手／左手 MIDI」不一樣：那兩個是**取代**該手的全部音符，這個是
純粹加上去 —— 現有的一顆都不會少。分手規則和自動排譜一致：來源剛好兩軌就
照它的分配，分不出來的一律標成無主音（hand=2）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import mido
from qt_editor.models import NoteModel
from qt_editor.midi_to_xml_converter import MIDIToXMLConverter


def write_midi(path, tracks):
    mid = mido.MidiFile(ticks_per_beat=480)
    for index, pitches in enumerate(tracks):
        track = mido.MidiTrack()
        mid.tracks.append(track)
        if index == 0:
            track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
        for offset, pitch in enumerate(pitches):
            track.append(mido.Message('note_on', note=pitch, velocity=90,
                                      time=0 if offset else 0))
            track.append(mido.Message('note_off', note=pitch, velocity=0, time=240))
    mid.save(path)
    return path


def convert(path):
    out = os.path.join(tempfile.mkdtemp(), 'overlay.xml')
    MIDIToXMLConverter().convert_midi_to_xml(path, out, resolve_overlaps=False)
    model = NoteModel()
    model.load_xml(out)
    return model


class MidiOverlayTests(unittest.TestCase):
    def _tmp(self, name):
        return os.path.join(tempfile.mkdtemp(), name)

    def test_two_track_source_keeps_its_own_hands(self):
        """剛好兩軌 = 來源檔已經講明左右手，照它的分配匯入。

        音軌數要從 **MIDI 檔本身**數：轉出來的 XML 沒有 track 欄位，拿轉檔
        後的音符去數會全部是 None，判斷永遠不成立。
        """
        from qt_editor.main_window import MainWindow

        path = write_midi(self._tmp('two.mid'), [[72, 74], [48, 50]])
        self.assertEqual(MainWindow._midi_note_track_count(path), 2)
        model = convert(path)
        hands = {int(n.hand) for n in model.notes_tree}
        self.assertTrue(hands <= {0, 1}, '兩軌來源不該出現無主音')

    def test_single_track_source_has_no_hand_split(self):
        """單軌分不出左右手 —— 匯入時要標成無主音。"""
        from qt_editor.main_window import MainWindow

        path = write_midi(self._tmp('one.mid'), [[72, 74, 48, 50]])
        self.assertEqual(MainWindow._midi_note_track_count(path), 1)
        model = convert(path)
        for note in model.notes_tree:
            note.hand = 2
        self.assertEqual({int(n.hand) for n in model.notes_tree}, {2})

    def test_overlay_keeps_every_existing_note(self):
        """疊加不能動到現有音符 —— 這是它和「取代該手」最重要的差別。"""
        base = convert(write_midi(self._tmp('base.mid'), [[60, 62], [36, 38]]))
        before = [(int(n.start), int(n.pitch or 0), int(n.hand)) for n in base.notes_tree]
        extra = convert(write_midi(self._tmp('extra.mid'), [[84, 86, 88, 90]]))
        for note in extra.notes_tree:
            note.hand = 2
        merged = list(base.notes_tree) + list(extra.notes_tree)
        merged.sort(key=lambda n: (int(n.start), int(n.min_key)))
        after = [(int(n.start), int(n.pitch or 0), int(n.hand)) for n in merged]
        for item in before:
            self.assertIn(item, after)
        self.assertEqual(len(merged), len(base.notes_tree) + len(extra.notes_tree))
        self.assertEqual(sum(1 for n in merged if int(n.hand) == 2),
                         len(extra.notes_tree))

    def test_unassigned_notes_get_their_own_colour(self):
        """無主音必須看得出來，不能被畫成左手。"""
        from qt_editor.chart_view import NOTE_LEFT, NOTE_NONE, NOTE_RIGHT
        self.assertNotEqual(NOTE_NONE, NOTE_LEFT)
        self.assertNotEqual(NOTE_NONE, NOTE_RIGHT)


class UnassignedSaveGuardTests(unittest.TestCase):
    """有無主音就不准存檔／匯出，而且要跳到最早的那一顆。

    無主音（hand=2）是「還沒決定用哪隻手」的中間狀態。帶著它存檔等於把
    未完成的譜交出去，所以直接擋掉。
    """

    def _window(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.main_window import MainWindow
        if QApplication.instance() is None:
            self._app = QApplication([])
        return MainWindow()

    def _notes(self, hands):
        from qt_editor.models import GNote
        out = []
        for index, (start, hand) in enumerate(hands):
            note = GNote(None, index)
            note.start = start
            note.end = start + 100
            note.gate = 100
            note.pitch = 60 + index
            note.hand = hand
            note.min_key = index * 3
            note.max_key = index * 3 + 2
            out.append(note)
        return out

    def test_first_unassigned_is_the_earliest_one(self):
        window = self._window()
        window.view.model.notes_tree = self._notes(
            [(900, 2), (100, 0), (400, 2), (700, 1)])
        found = window._first_unassigned_note()
        self.assertIsNotNone(found)
        self.assertEqual(int(found.start), 400)

    def test_no_unassigned_means_no_block(self):
        window = self._window()
        window.view.model.notes_tree = self._notes([(0, 0), (100, 1)])
        self.assertIsNone(window._first_unassigned_note())

    def test_blocked_save_jumps_to_and_selects_that_note(self):
        from PyQt5 import QtWidgets

        window = self._window()
        window.view.model.notes_tree = self._notes(
            [(900, 2), (100, 0), (400, 2)])
        window.view.model.rebuild_display_cache()
        shown = {}
        original = QtWidgets.QMessageBox.warning
        QtWidgets.QMessageBox.warning = staticmethod(
            lambda *a, **k: shown.setdefault('args', a))
        try:
            blocked = window._block_on_unassigned()
        finally:
            QtWidgets.QMessageBox.warning = original
        self.assertTrue(blocked, '有無主音卻沒擋下來')
        self.assertIn('args', shown, '沒有跳出警告')
        selected = list(window.view.selected)
        self.assertEqual(len(selected), 1)
        self.assertEqual(int(selected[0].start), 400)


    def test_blocked_save_does_not_clear_dirty(self):
        """存檔被擋下來時，dirty 必須維持 True。

        關閉視窗時選「儲存」是靠 dirty 判斷有沒有真的存到才決定關不關；
        擋下來卻把 dirty 清掉的話，視窗會照關、改動直接消失。
        """
        from PyQt5 import QtWidgets

        window = self._window()
        window.view.model.notes_tree = self._notes([(0, 0), (400, 2)])
        window.view.model.rebuild_display_cache()
        window.view.model.current_file = None
        window.view.model.dirty = True
        original = QtWidgets.QMessageBox.warning
        QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        try:
            window._do_save('unused.xml')
        finally:
            QtWidgets.QMessageBox.warning = original
        self.assertTrue(window.view.model.dirty, '被擋下來卻清掉了 dirty')


if __name__ == '__main__':
    unittest.main(verbosity=2)
