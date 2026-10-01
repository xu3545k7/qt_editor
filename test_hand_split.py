# -*- coding: utf-8 -*-
"""重新判斷左右手。

使用者回報 AI 轉出來的譜「很多都是誤判左右手」。成因是單軌來源走的是
`smart_chart._assign_hands` 的「全曲音高中位數切一刀」那條路，旋律只要跨過那條
線就換手——實測那份譜 58 條連續跑動裡有 14 條（24%）中途換手。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import hand_split
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def note(start, pitch, gate=100, note_type=0, hand=0, idx=0, lane=4):
    n = GNote(None, idx)
    n.start = start
    n.end = start + gate
    n.gate = gate
    n.pitch = pitch
    n.note_type = note_type
    n.hand = hand
    n.min_key = n.max_key = lane
    return n


def hands(notes):
    return ''.join('L' if int(n.hand) else 'R' for n in notes)


class RunTests(unittest.TestCase):
    """一條連續的旋律線要整條同一隻手。"""

    def test_a_scale_crossing_the_middle_stays_on_one_hand(self):
        """下行音階跨過音高中位數，不該在中間換手。"""
        run = [note(i * 60, 84 - i * 2, idx=i) for i in range(16)]
        # 先擺成「切一刀」的樣子：低的一半給左手
        for i, n in enumerate(run):
            n.hand = 1 if n.pitch < 69 else 0
        self.assertIn('RL', hands(run), '前提：原本確實在中間換了手')
        hand_split.assign(run)
        self.assertEqual(len(set(hands(run))), 1,
                         '整條跑動要同一隻手，實際是 %s' % hands(run))

    def test_a_v_shaped_run_does_not_switch_at_the_bottom(self):
        """先下後上的跑動：最低點不該插進另一隻手。"""
        down = [note(i * 60, 88 - i * 3, idx=i) for i in range(10)]
        up = [note((10 + i) * 60, 61 + i * 3, idx=10 + i) for i in range(10)]
        run = down + up
        for n in run:
            n.hand = 1 if n.pitch < 69 else 0
        hand_split.assign(run)
        self.assertEqual(len(set(hands(run))), 1, hands(run))

    def test_a_real_two_hand_texture_keeps_two_hands(self):
        """低音伴奏＋高音旋律：該是兩隻手，不能全塞給一隻。"""
        notes = []
        for i in range(12):
            notes.append(note(i * 200, 40 + (i % 3), idx=2 * i))        # 低音
            notes.append(note(i * 200, 76 + (i % 4), idx=2 * i + 1))    # 旋律
        hand_split.assign(notes)
        self.assertEqual(len(set(hands(notes))), 2, '應該兩隻手都用到')
        low = {int(n.hand) for n in notes if n.pitch < 60}
        high = {int(n.hand) for n in notes if n.pitch > 70}
        self.assertEqual(len(low), 1, '低音聲部要固定同一手')
        self.assertEqual(len(high), 1, '旋律聲部要固定同一手')
        self.assertNotEqual(low, high, '兩個聲部不該是同一手')


class ReachTests(unittest.TestCase):
    """手張不開的分法要被排除——而且是用半音量，不是鍵道。"""

    def test_one_hand_never_spans_more_than_an_octave(self):
        notes = [note(0, 40), note(0, 44), note(0, 79), note(0, 84)]
        hand_split.assign(notes)
        for hand in (0, 1):
            ps = [n.pitch for n in notes if int(n.hand) == hand]
            if len(ps) >= 2:
                self.assertLessEqual(max(ps) - min(ps), hand_split.MAX_SPAN,
                                     '單手跨了 %d 個半音' % (max(ps) - min(ps)))

    def test_a_held_long_note_does_not_block_the_whole_hand(self):
        """長押佔的是跨距，不是名額。

        一開始把每顆音都算成「佔著手指到 endTime」，結果一隻手瞬間五指全滿、
        後面的音全被推給另一手——實測讓「單手跨超過八度」從 29 個時刻暴增到
        519 個。
        """
        held = note(0, 36, gate=4000, note_type=2)      # 踩著的低音長押
        melody = [note(200 + i * 60, 72 + i, idx=i + 1) for i in range(12)]
        notes = [held] + melody
        hand_split.assign(notes)
        self.assertEqual(len({int(n.hand) for n in melody}), 1,
                         '旋律不該被長押逼得換手：%s' % hands(melody))
        self.assertNotEqual(int(held.hand), int(melody[0].hand),
                            '長押和旋律差 36 個半音，不可能同一隻手')

    def test_five_fingers_is_the_limit_for_one_strike(self):
        notes = [note(0, 60 + i * 2) for i in range(8)]
        hand_split.assign(notes)
        for hand in (0, 1):
            self.assertLessEqual(sum(1 for n in notes if int(n.hand) == hand),
                                 hand_split.MAX_FINGERS)


class GroupingTests(unittest.TestCase):
    def test_notes_a_few_milliseconds_apart_count_as_one_chord(self):
        notes = [note(0, 60), note(3, 64), note(7, 67)]
        groups = hand_split.group_by_onset(notes)
        self.assertEqual(len(groups), 1)

    def test_notes_far_apart_are_separate_groups(self):
        notes = [note(0, 60), note(200, 64)]
        self.assertEqual(len(hand_split.group_by_onset(notes)), 2)

    def test_it_reports_how_many_notes_changed(self):
        run = [note(i * 60, 84 - i * 2, idx=i) for i in range(16)]
        for n in run:
            n.hand = 1 if n.pitch < 69 else 0
        before = hands(run)
        changed = hand_split.assign(run)
        after = hands(run)
        self.assertEqual(changed, sum(1 for a, b in zip(before, after) if a != b))

    def test_an_empty_or_tiny_chart_is_left_alone(self):
        self.assertEqual(hand_split.assign([]), 0)
        self.assertEqual(hand_split.assign([note(0, 60)]), 0)

    def test_notes_without_pitch_are_ignored(self):
        n = note(0, 60)
        n.pitch = None
        self.assertEqual(hand_split.assign([n]), 0)


class ViewIntegrationTests(unittest.TestCase):
    """接到編輯器上——模組對了但沒接上去的話這裡會抓到。"""

    def _view(self):
        from qt_editor.chart_view import ChartView
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        run = [note(i * 120, 84 - i * 2, idx=i) for i in range(16)]
        for n in run:
            n.hand = 1 if n.pitch < 69 else 0
        model.notes_tree = run
        model.rebuild_display_cache()
        view = ChartView()
        view.model = model
        return view, run

    def test_the_tool_changes_hands_and_can_be_undone(self):
        view, run = self._view()
        before = hands(run)
        stats = view.apply_hand_split(split_tracks=False)
        self.assertGreater(stats['changed'], 0)
        self.assertEqual(stats['total'], 16)
        view.model.undo()
        self.assertEqual(hands(view.model.notes_tree), before, 'Ctrl+Z 要回得來')

    def test_splitting_tracks_follows_the_new_hands(self):
        view, _run = self._view()
        view.apply_hand_split(split_tracks=True)
        for n in view.model.notes_tree:
            self.assertEqual(int(n.track), 1 if int(n.hand) == 1 else 2)


if __name__ == '__main__':
    unittest.main()


class AiTranscriptionFixTests(unittest.TestCase):
    """AI 轉譜載進來就自動補好——不動手寫譜的任何預設值。"""

    def _window(self):
        from qt_editor.main_window import MainWindow
        win = MainWindow()
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        run = []
        # 下行音階，每顆都拖過下一顆（＝踏板長度）。間隔取 120 ms：真實跑動就是
        # 這個密度，接線獎勵的時間窗（LINE_MS=150）才成立——那個窗拉寬過，實測
        # 反而讓還原正確率下降（中位 97.76% -> 97.58%），所以是測試要貼近真實。
        for i in range(16):
            n = note(i * 120, 84 - i * 2, gate=900, note_type=2, idx=i)
            n.hand = 1 if n.pitch < 69 else 0    # 音高切一刀的樣子
            run.append(n)
        model.notes_tree = run
        model.rebuild_display_cache()
        win.view.model = model
        win._load_model_all(model)
        return win, run

    def test_it_fixes_both_hands_and_lengths(self):
        win, run = self._window()
        text = win._fix_ai_transcription()
        self.assertTrue(text.startswith('AI 轉譜修整'), text)
        self.assertEqual(len({int(n.hand) for n in run}), 1, '整條跑動要同一隻手')
        for a, b in zip(run, run[1:]):
            self.assertLessEqual(a.end, b.start, '長度不該再蓋過同手下一顆')

    def test_one_undo_puts_everything_back(self):
        win, run = self._window()
        before = [(int(n.hand), int(n.end), int(n.note_type)) for n in run]
        win._fix_ai_transcription()
        win.view.model.undo()
        after = [(int(n.hand), int(n.end), int(n.note_type))
                 for n in win.view.model.notes_tree]
        self.assertEqual(after, before, '一次 Ctrl+Z 就該全部回去')

    def test_a_clean_chart_reports_nothing(self):
        from qt_editor.main_window import MainWindow
        win = MainWindow()
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        notes = [note(i * 500, 60 + i, gate=100, note_type=0, idx=i) for i in range(4)]
        for n in notes:
            n.hand = 0
        model.notes_tree = notes
        model.rebuild_display_cache()
        win.view.model = model
        win._load_model_all(model)
        self.assertEqual(win._fix_ai_transcription(), '')

    def test_no_hold_rides_over_more_than_five_notes(self):
        """使用者要的性質：一串至少每五個要裁一次。"""
        from qt_editor.main_window import AI_MAX_RING_NOTES, MainWindow
        win = MainWindow()
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        # 一顆很長的低音長押，上面壓著同一隻手的一長串
        hold = note(0, 40, gate=6000, note_type=2, idx=0)
        run = [note(200 + i * 150, 62 + (i % 3), gate=80, note_type=0, idx=i + 1,
                    lane=10) for i in range(20)]
        for n in [hold] + run:
            n.hand = 1
        model.notes_tree = [hold] + run
        model.rebuild_display_cache()
        win.view.model = model
        win._load_model_all(model)
        win._fix_ai_transcription()

        for h in (n for n in model.notes_tree if int(n.note_type) & 2):
            same = [n for n in model.notes_tree if int(n.hand) == int(h.hand)]
            under = sum(1 for n in same if int(h.start) < int(n.start) < int(h.end))
            self.assertLessEqual(under, AI_MAX_RING_NOTES,
                                 '長押底下串了 %d 顆同手音' % under)
