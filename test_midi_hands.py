"""MIDI 轉檔的左右手判定。

原本的規則是 `hand = 0 if len(mid.tracks) == 2 and track == 0 else 1`，兩個問題：
音軌數不是 2 就**整份變成左手**（format 1 的鋼琴 MIDI 常是 [指揮軌, 右手, 左手]
三軌），而且假設 track 0 一定是右手。改成照各軌的平均音高排序來分。
"""
import unittest

from mido import Message, MetaMessage, MidiFile, MidiTrack

from qt_editor.midi_to_xml_converter import MIDIToXMLConverter


def notes(track, pitches):
    return [{"track": track, "scale_piano": p} for p in pitches]


class SplitHandsTests(unittest.TestCase):
    def test_two_tracks_low_one_is_the_left_hand(self):
        raw = notes(0, [72, 74, 76]) + notes(1, [40, 43, 45])
        left, _ = MIDIToXMLConverter._split_hands(raw)
        self.assertEqual(left, {1})

    def test_track_order_does_not_decide_the_hand(self):
        # 低音在 track 0：舊規則會把它當右手，整份左右相反。
        raw = notes(0, [40, 43, 45]) + notes(1, [72, 74, 76])
        left, _ = MIDIToXMLConverter._split_hands(raw)
        self.assertEqual(left, {0})

    def test_conductor_track_does_not_count(self):
        # [指揮軌, 右手, 左手]：track 0 沒有音符，不該影響分手。
        raw = notes(1, [72, 74, 76]) + notes(2, [40, 43, 45])
        left, _ = MIDIToXMLConverter._split_hands(raw)
        self.assertEqual(left, {2})

    def test_four_tracks_split_in_half_by_pitch(self):
        raw = (notes(0, [75]) + notes(1, [71]) + notes(2, [58]) + notes(3, [36]))
        left, _ = MIDIToXMLConverter._split_hands(raw)
        self.assertEqual(left, {2, 3})

    def test_single_track_falls_back_to_the_median_pitch(self):
        raw = notes(0, [40, 44, 60, 72, 76])
        left, pivot = MIDIToXMLConverter._split_hands(raw)
        self.assertIsNone(left)
        self.assertEqual(pivot, 60)

    def test_no_notes_at_all(self):
        left, pivot = MIDIToXMLConverter._split_hands([])
        self.assertIsNone(left)
        self.assertEqual(pivot, 60)


class ThreeTrackFileTests(unittest.TestCase):
    """三軌檔案原本會整份變成左手。"""

    @staticmethod
    def build():
        mid = MidiFile()
        conductor = MidiTrack()
        conductor.append(MetaMessage("set_tempo", tempo=500000, time=0))
        mid.tracks.append(conductor)
        for pitches in ([72, 76], [40, 43]):
            track = MidiTrack()
            for pitch in pitches:
                track.append(Message("note_on", note=pitch, velocity=80, time=0))
                track.append(Message("note_off", note=pitch, velocity=0, time=480))
            mid.tracks.append(track)
        return mid

    def test_both_hands_are_present(self):
        raw = MIDIToXMLConverter()._extract_raw_notes(self.build())
        hands = {n["scale_piano"]: n["hand"] for n in raw}
        self.assertEqual(hands[72], 0)
        self.assertEqual(hands[76], 0)
        self.assertEqual(hands[40], 1)
        self.assertEqual(hands[43], 1)


if __name__ == "__main__":
    unittest.main()
