import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, min_key=0, max_key=0, note_type=0, idx=0):
    note = GNote(None, idx)
    note.start = start
    note.end = end
    note.gate = end - start
    note.min_key = min_key
    note.max_key = max_key
    note.note_type = note_type
    return note


class AlignNotesEdgeTests(unittest.TestCase):
    def test_align_start_keeps_end_fixed(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        self.assertEqual(NoteModel.align_notes_edge([a, b], 'start', 200), 2)
        # end 不變，start 對齊到 200，gate 同步
        self.assertEqual((a.start, a.end, a.gate), (200, 500, 300))
        self.assertEqual((b.start, b.end, b.gate), (200, 400, 200))

    def test_align_end_keeps_start_fixed(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        self.assertEqual(NoteModel.align_notes_edge([a, b], 'end', 600), 2)
        self.assertEqual((a.start, a.end, a.gate), (100, 600, 500))
        self.assertEqual((b.start, b.end, b.gate), (300, 600, 300))

    def test_align_start_clamped_below_end(self):
        # 目標值 >= end：start 不能越過 end，最多 end-1
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', 500), 1)
        self.assertEqual((n.start, n.end, n.gate), (199, 200, 1))

    def test_align_end_clamped_above_start(self):
        # 目標值 <= start：end 至少 start+1
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'end', 50), 1)
        self.assertEqual((n.start, n.end, n.gate), (100, 101, 1))

    def test_align_start_negative_clamped_to_zero(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', -50), 1)
        self.assertEqual((n.start, n.end, n.gate), (0, 200, 200))

    def test_no_change_returns_zero(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'start', 100), 0)
        self.assertEqual((n.start, n.end), (100, 200))

    def test_invalid_target_is_noop(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(NoteModel.align_notes_edge([n], 'middle', 150), 0)
        self.assertEqual((n.start, n.end), (100, 200))


class NudgeNotesEdgeTests(unittest.TestCase):
    """固定提前／延後：每顆各自位移單一邊，另一端不動。"""

    def test_nudge_start_keeps_end_and_relative_spread(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        deltas = {id(a): -50, id(b): -50}
        self.assertEqual(NoteModel.nudge_notes_edge([a, b], 'start', deltas), 2)
        # end 不動，兩顆的 start 各自提前 50ms，參差保留（差值仍是 200）
        self.assertEqual((a.start, a.end, a.gate), (50, 500, 450))
        self.assertEqual((b.start, b.end, b.gate), (250, 400, 150))

    def test_nudge_end_keeps_start(self):
        a = make_note(100, 500, note_type=2, idx=0)
        b = make_note(300, 400, idx=1)
        deltas = {id(a): 125, id(b): 125}
        self.assertEqual(NoteModel.nudge_notes_edge([a, b], 'end', deltas), 2)
        self.assertEqual((a.start, a.end, a.gate), (100, 625, 525))
        self.assertEqual((b.start, b.end, b.gate), (300, 525, 225))

    def test_per_note_deltas(self):
        # 變速譜的情形：同一批選取，每顆換算出來的 ms 不同
        a = make_note(0, 400, idx=0)
        b = make_note(1000, 1400, idx=1)
        self.assertEqual(
            NoteModel.nudge_notes_edge([a, b], 'end', {id(a): 50, id(b): 25}), 2)
        self.assertEqual((a.start, a.end, a.gate), (0, 450, 450))
        self.assertEqual((b.start, b.end, b.gate), (1000, 1425, 425))

    def test_nudge_start_clamped_below_end(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(
            NoteModel.nudge_notes_edge([n], 'start', {id(n): 500}), 1)
        self.assertEqual((n.start, n.end, n.gate), (199, 200, 1))

    def test_nudge_end_clamped_above_start(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(
            NoteModel.nudge_notes_edge([n], 'end', {id(n): -500}), 1)
        self.assertEqual((n.start, n.end, n.gate), (100, 101, 1))

    def test_nudge_start_negative_clamped_to_zero(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(
            NoteModel.nudge_notes_edge([n], 'start', {id(n): -400}), 1)
        self.assertEqual((n.start, n.end, n.gate), (0, 200, 200))

    def test_already_at_limit_reports_no_change(self):
        # 已經頂在下限的音符再位移一次 → 不算變更（呼叫端靠這個提示使用者）
        n = make_note(100, 101, idx=0)
        self.assertEqual(
            NoteModel.nudge_notes_edge([n], 'end', {id(n): -10}), 0)
        self.assertEqual((n.start, n.end), (100, 101))

    def test_zero_delta_and_missing_entry_are_noop(self):
        a = make_note(100, 200, idx=0)
        b = make_note(300, 400, idx=1)
        # a 給 0、b 根本沒在 deltas 裡
        self.assertEqual(
            NoteModel.nudge_notes_edge([a, b], 'start', {id(a): 0}), 0)
        self.assertEqual((a.start, a.end), (100, 200))
        self.assertEqual((b.start, b.end), (300, 400))

    def test_invalid_target_is_noop(self):
        n = make_note(100, 200, idx=0)
        self.assertEqual(
            NoteModel.nudge_notes_edge([n], 'middle', {id(n): 10}), 0)
        self.assertEqual((n.start, n.end), (100, 200))


class AlignEndsWithinStartGroupsTests(unittest.TestCase):
    """同 start 的音符各自一組，尾端對齊到該組的最晚／最早。

    使用者最常做的尾端修正就是這個：一個和弦同時按下去就該同時放開。操作紀錄
    裡長押尾端被一顆一顆拖了 282 次、775 顆，88% 往短的方向。

    和 `align_notes_edge` 的差別是「各組自己一組」——那個是把整個選取對到同一個
    絕對毫秒，框住連續好幾個和弦時會把它們全拉到第一個和弦那一刻。
    """

    def chords(self):
        """兩個和弦：第一個尾巴參差、第二個本來就齊，外加一顆單音。"""
        return [
            make_note(1000, 1500, note_type=2, idx=0),
            make_note(1000, 1800, note_type=2, idx=1),
            make_note(1000, 1300, note_type=2, idx=2),
            make_note(2000, 2400, note_type=2, idx=3),
            make_note(2000, 2400, note_type=2, idx=4),
            make_note(3000, 3900, note_type=2, idx=5),
        ]

    def test_latest_pulls_the_group_to_its_longest(self):
        ns = self.chords()
        self.assertEqual(
            NoteModel.align_ends_within_start_groups(ns, 'latest'), 2)
        self.assertEqual([n.end for n in ns[:3]], [1800, 1800, 1800])

    def test_earliest_pulls_the_group_to_its_shortest(self):
        ns = self.chords()
        self.assertEqual(
            NoteModel.align_ends_within_start_groups(ns, 'earliest'), 2)
        self.assertEqual([n.end for n in ns[:3]], [1300, 1300, 1300])

    def test_each_group_gets_its_own_value(self):
        """這是和「一律對齊到同一個絕對值」的關鍵差別。"""
        ns = self.chords()
        NoteModel.align_ends_within_start_groups(ns, 'latest')
        self.assertEqual([n.end for n in ns[3:5]], [2400, 2400],
                         '第二個和弦不該被第一個的 1800 影響')

    def test_a_lone_note_has_nothing_to_align_to(self):
        ns = self.chords()
        NoteModel.align_ends_within_start_groups(ns, 'latest')
        self.assertEqual(ns[5].end, 3900)

    def test_an_already_even_group_is_left_alone(self):
        ns = [make_note(1000, 1500, note_type=2, idx=0),
              make_note(1000, 1500, note_type=2, idx=1)]
        self.assertEqual(
            NoteModel.align_ends_within_start_groups(ns, 'latest'), 0)

    def test_gate_follows(self):
        ns = self.chords()
        NoteModel.align_ends_within_start_groups(ns, 'latest')
        for n in ns:
            self.assertEqual(n.gate, n.end - n.start)

    def test_it_is_idempotent(self):
        ns = self.chords()
        NoteModel.align_ends_within_start_groups(ns, 'earliest')
        self.assertEqual(
            NoteModel.align_ends_within_start_groups(ns, 'earliest'), 0)

    def test_the_end_still_clears_the_start(self):
        """組內最早的 end 不可以等於 start——尾端至少要在起點之後 1ms。"""
        ns = [make_note(1000, 1000, note_type=2, idx=0),
              make_note(1000, 1800, note_type=2, idx=1)]
        NoteModel.align_ends_within_start_groups(ns, 'earliest')
        self.assertEqual([n.end for n in ns], [1001, 1001])

    def test_a_bad_pick_does_nothing(self):
        ns = self.chords()
        self.assertEqual(
            NoteModel.align_ends_within_start_groups(ns, '亂填'), 0)
        self.assertEqual([n.end for n in ns[:3]], [1500, 1800, 1300])


if __name__ == '__main__':
    unittest.main()
