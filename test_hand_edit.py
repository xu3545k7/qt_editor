"""音高模式下改左右手。

以前在音高模式改左右手，畫面完全沒反應（那個模式的方塊本色是聲部配色或力度
明暗，紅藍那套看不到），而且自動排譜會照音軌把手改回去。這裡驗證：改手時
音軌一起搬、外框看得出左右、可以還原。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import HAND_FRAME_L, HAND_FRAME_R, NOTE_FRAME
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def make_note(idx, start, pitch, hand=0, track=None, channel=None):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.track = track
    n.channel = channel
    n.velocity = 90
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    return n


class HandEditBase(unittest.TestCase):
    def build(self, notes, fmt='midi'):
        self.win = MainWindow()
        self.model = NoteModel.create_new('t', 120.0, 20.0, 4)
        self.model.file_format = fmt
        self.model.notes_tree = notes
        self.model.rebuild_display_cache()
        self.win._load_model_all(self.model)
        self.view = self.win.view
        self.view.set_view_mode('pitch')
        return self.model

    def select(self, notes):
        self.view.selected = {n.idx for n in notes}


class PitchModeHandEditTests(HandEditBase):
    def setUp(self):
        self.notes = [
            make_note(0, 0, 72, hand=0, track=0, channel=0),
            make_note(1, 200, 74, hand=0, track=0, channel=0),
            make_note(2, 400, 48, hand=1, track=1, channel=0),
            make_note(3, 600, 50, hand=1, track=1, channel=0),
        ]
        self.build(self.notes)

    def test_pitch_mode_is_actually_on(self):
        self.assertTrue(self.view.pitch_mode)

    def test_hand_changes_in_pitch_mode(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertEqual(self.notes[0].hand, 1)

    def test_the_track_moves_with_the_hand(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertEqual(self.notes[0].track, 1, '要落到左手那一軌')

    def test_the_other_notes_are_left_alone(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertEqual([n.hand for n in self.notes[1:]], [0, 1, 1])
        self.assertEqual([n.track for n in self.notes[1:]], [0, 1, 1])

    def test_moving_a_whole_track_really_moves_it(self):
        # 取樣要排掉自己，不然算出來的目標軌就是原本那一軌，等於沒搬
        self.select([self.notes[2], self.notes[3]])
        self.view.set_hand_selected(0)
        self.assertEqual([n.track for n in self.notes[2:]], [0, 0])

    def test_a_freed_track_gets_reused_instead_of_growing(self):
        self.select([self.notes[2], self.notes[3]])
        self.view.set_hand_selected(0)      # 1 號軌空了
        self.select([self.notes[0]])
        self.view.set_hand_selected(1)
        self.assertEqual(self.notes[0].track, 1, '空出來的軌要接手，不要一直往上長')

    def test_status_says_what_happened(self):
        msgs = []
        self.view.status_changed.connect(msgs.append)
        self.select(self.notes[:2])
        self.view.set_hand_selected(1)
        self.assertTrue(any('左手' in m for m in msgs), msgs)

    def test_undo_puts_back_hand_and_track(self):
        self.select(self.notes[:2])
        self.view.set_hand_selected(1)
        self.model.undo()
        self.assertEqual([n.hand for n in self.model.notes_tree], [0, 0, 1, 1])
        self.assertEqual([n.track for n in self.model.notes_tree], [0, 0, 1, 1])

    def test_nothing_selected_is_a_no_op(self):
        self.view.selected = set()
        self.view.set_hand_selected(1)
        self.assertEqual([n.hand for n in self.notes], [0, 0, 1, 1])

    def test_the_note_frame_shows_the_hand_in_pitch_mode(self):
        self.assertEqual(self.view._note_frame_color(self.notes[0]), HAND_FRAME_R)
        self.assertEqual(self.view._note_frame_color(self.notes[2]), HAND_FRAME_L)

    def test_the_frame_follows_an_edit(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertEqual(self.view._note_frame_color(self.notes[0]), HAND_FRAME_L)

    def test_other_modes_keep_the_plain_white_frame(self):
        self.view.set_view_mode('measure')
        self.assertEqual(self.view._note_frame_color(self.notes[0]), NOTE_FRAME)

    def test_the_voice_colour_follows_the_edit(self):
        # 還沒排譜的 MIDI 是照聲部上色的——音軌沒搬的話畫面不會動
        before = self.view._note_colors(self.notes[0])[0].name()
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.view._build_channel_color_map()
        self.assertNotEqual(self.view._note_colors(self.notes[0])[0].name(), before)


class SingleTrackMidiTests(HandEditBase):
    def setUp(self):
        self.notes = [make_note(i, i * 200, 60 + i, hand=0, track=0, channel=0)
                      for i in range(4)]
        self.build(self.notes)

    def test_the_second_hand_gets_its_own_track(self):
        self.select(self.notes[:2])
        self.view.set_hand_selected(1)
        self.assertEqual([n.track for n in self.notes], [1, 1, 0, 0])

    def test_the_arranger_now_agrees_with_the_edit(self):
        from qt_editor.smart_chart import _assign_hands

        # 左手挑低音，排譜的音軌分手才會和使用者指定的一致
        self.select([self.notes[0], self.notes[1]])
        self.view.set_hand_selected(1)
        _assign_hands(self.model.notes_tree, [])
        self.assertEqual([n.hand for n in self.notes], [1, 1, 0, 0])


class NonMidiChartTests(HandEditBase):
    def setUp(self):
        self.notes = [make_note(0, 0, 72, hand=0), make_note(1, 200, 48, hand=1)]
        self.build(self.notes, fmt='xml')

    def test_hand_still_editable(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertEqual(self.notes[0].hand, 1)

    def test_track_is_left_untouched(self):
        self.select(self.notes[:1])
        self.view.set_hand_selected(1)
        self.assertIsNone(self.notes[0].track, 'XML 譜沒有音軌可言，不要亂填')


class TrackResolutionTests(unittest.TestCase):
    def test_no_tracks_at_all_falls_back_to_the_hand_number(self):
        m = NoteModel.create_new('t', 120.0, 5.0, 4)
        m.notes_tree = [make_note(0, 0, 60)]
        self.assertEqual(m.midi_track_for_hand(1), 1)

    def test_a_brand_new_track_is_one_past_the_top(self):
        m = NoteModel.create_new('t', 120.0, 5.0, 4)
        m.notes_tree = [make_note(0, 0, 60, hand=0, track=3)]
        self.assertEqual(m.midi_track_for_hand(1), 4)


if __name__ == '__main__':
    unittest.main()
