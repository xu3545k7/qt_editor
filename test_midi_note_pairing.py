"""同音、時間很近的 note_on / note_off 配對。

真實鋼琴 MIDI 常見的圓滑奏：下一顆已經響了、前一顆的 note_off 才晚一兩個
tick 到。配對如果用後進先出（`stack.pop()`），那個 note_off 會被配給**剛
響的那顆**，解析成「前一顆吃掉整段 + 新的那顆只有 1ms」的鬼音。正確做法是
先進先出（`stack.pop(0)`）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import mido
from qt_editor.models import NoteModel

TPB = 480          # 480 tick = 1 拍 = 500ms @ 120bpm


def parse(events):
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    last = 0
    for tick, kind, pitch, velocity in sorted(events, key=lambda e: e[0]):
        track.append(
            mido.Message(kind, note=pitch, velocity=velocity, time=tick - last)
        )
        last = tick
    path = os.path.join(tempfile.mkdtemp(), 'pairing.mid')
    mid.save(path)
    model = NoteModel()
    model.load_midi(path, auto_arrange=False)
    return sorted(
        ((int(n.start), int(n.end)) for n in model.notes_tree),
        key=lambda item: (item[0], item[1]),
    )


class NotePairingTests(unittest.TestCase):
    def test_legato_overlap_does_not_create_a_ghost_note(self):
        """下一顆先響、前一顆晚 1 tick 放開 —— 兩顆都要有正常長度。"""
        notes = parse([
            (0, 'note_on', 60, 100),
            (240, 'note_on', 60, 90),
            (241, 'note_off', 60, 0),
            (480, 'note_off', 60, 0),
        ])
        self.assertEqual(len(notes), 2)
        for start, end in notes:
            self.assertGreater(end - start, 100, '出現了鬼音: %s' % (notes,))
        self.assertEqual(notes, [(0, 251), (250, 500)])

    def test_restruck_same_pitch_pairs_in_order(self):
        """同音重疊：先響的先收，不會被巢狀包起來。"""
        notes = parse([
            (0, 'note_on', 60, 100),
            (48, 'note_on', 60, 90),
            (480, 'note_off', 60, 0),
            (528, 'note_off', 60, 0),
        ])
        self.assertEqual(notes, [(0, 500), (50, 550)])

    def test_three_stacked_same_pitch_notes_keep_equal_lengths(self):
        notes = parse([
            (0, 'note_on', 60, 100),
            (24, 'note_on', 60, 90),
            (48, 'note_on', 60, 80),
            (480, 'note_off', 60, 0),
            (504, 'note_off', 60, 0),
            (528, 'note_off', 60, 0),
        ])
        self.assertEqual([end - start for start, end in notes], [500, 500, 500])

    def test_back_to_back_same_pitch_is_unchanged(self):
        """off 和 on 落在同一個 tick —— 本來就對，不可以被改壞。"""
        notes = parse([
            (0, 'note_on', 60, 100),
            (240, 'note_off', 60, 0),
            (240, 'note_on', 60, 90),
            (480, 'note_off', 60, 0),
        ])
        self.assertEqual(notes, [(0, 250), (250, 500)])

    def test_duplicate_stub_note_is_dropped(self):
        """同一 tick、同一音高被敲兩次，其中一顆只有幾毫秒 → 那是殘渣。

        實測 bad-apple 有 161 組這種形狀（412ms 的正常音 + 1ms 的分身）。
        留著的話排譜器得把它排到另一條鍵道上，玩家要為同一個聲音按兩下。
        """
        notes = parse([
            (0, 'note_on', 60, 100),
            (0, 'note_on', 60, 90),
            (1, 'note_off', 60, 0),
            (400, 'note_off', 60, 0),
        ])
        self.assertEqual(len(notes), 1)
        self.assertGreater(notes[0][1] - notes[0][0], 100)

    def test_two_full_length_duplicates_are_kept(self):
        """兩顆都是正常長度的重複（可能是刻意疊軌）不動它。"""
        notes = parse([
            (0, 'note_on', 60, 100),
            (0, 'note_on', 60, 90),
            (400, 'note_off', 60, 0),
            (400, 'note_off', 60, 0),
        ])
        self.assertEqual(len(notes), 2)

    def test_note_on_velocity_zero_counts_as_note_off(self):
        notes = parse([
            (0, 'note_on', 60, 100),
            (240, 'note_on', 60, 0),
        ])
        self.assertEqual(notes, [(0, 250)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
