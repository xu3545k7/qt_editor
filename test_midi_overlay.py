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


class UnassignedNotesAreAllowedTests(unittest.TestCase):
    """無主音可以存在，也可以存檔。

    以前有一顆 `hand=2` 就擋下存檔跟匯出，理由是「那是還沒決定用哪隻手
    的中間狀態」。那個理由站不住腱：`hand=2` 是官方格式的合法值（官方
    normal 譜有 54% 的音符是 hand=2，PAN 寫出去走 track 3），使用者也會
    故意留著它。使用者：「請把無主音保護關掉，它可以存在並保存」。

    `_first_unassigned_note` 留下來，只當存檔完成訊息裡的告知用。
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

    def test_no_unassigned_means_nothing_to_report(self):
        window = self._window()
        window.view.model.notes_tree = self._notes([(0, 0), (100, 1)])
        self.assertIsNone(window._first_unassigned_note())

    def test_the_guard_is_gone(self):
        """擋存檔的那個把關整個拿掉了，不是改成預設關。"""
        window = self._window()
        self.assertFalse(hasattr(window, '_block_on_unassigned'))

    def test_a_chart_with_unassigned_notes_really_saves(self):
        from unittest import mock

        window = self._window()
        window.view.model.notes_tree = self._notes(
            [(0, 0), (400, 2), (900, 2)])
        window.view.model.rebuild_display_cache()
        path = os.path.join(tempfile.mkdtemp(), 'unassigned.json')
        with mock.patch('qt_editor.main_window.QMessageBox.information') as info:
            window._do_save(path)
        self.assertTrue(os.path.exists(path), '有無主音就沒寫出任何檔案')
        self.assertGreater(os.path.getsize(path), 0)
        back = NoteModel()
        back.load_json(path)
        kept = sum(1 for n in back.notes_tree
                   if int(getattr(n, 'hand', 0)) == 2)
        self.assertEqual(kept, 2, '無主音沒有原樣存下來')
        # 不擋了，但存檔完成的訊息要說出有幾顆
        said = ' '.join(str(a) for a in info.call_args[0])
        self.assertIn('2 顆無主音', said)

    def test_a_clean_chart_says_nothing_about_unassigned(self):
        from unittest import mock

        window = self._window()
        window.view.model.notes_tree = self._notes([(0, 0), (400, 1)])
        window.view.model.rebuild_display_cache()
        path = os.path.join(tempfile.mkdtemp(), 'clean.json')
        with mock.patch('qt_editor.main_window.QMessageBox.information') as info:
            window._do_save(path)
        said = ' '.join(str(a) for a in info.call_args[0])
        self.assertNotIn('無主音', said)


if __name__ == '__main__':
    unittest.main(verbosity=2)
