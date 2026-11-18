import unittest

from qt_editor import smart_chart as SC
from qt_editor.models import (
    GNote, NoteModel, OVERLAP_CHORD, OVERLAP_HOLD, OVERLAP_TAIL,
    overlapping_pairs,
)


def make_note(start, end, min_key, max_key, note_type=0, idx=0, hand=0,
              track=None, pitch=None):
    note = GNote(None, idx)
    note.start = start
    note.end = end
    note.gate = end - start
    note.min_key = min_key
    note.max_key = max_key
    note.note_type = note_type
    note.hand = hand
    note.track = track
    note.pitch = pitch
    return note


class OverlapResolutionTests(unittest.TestCase):
    def test_hold_tail_only_shortens_hold_before_later_note(self):
        hold = make_note(100, 500, 4, 6, note_type=2)
        later = make_note(400, 450, 6, 8, note_type=0, idx=1)
        tap = make_note(100, 500, 10, 12, note_type=0, idx=2)
        later_tap = make_note(400, 450, 11, 13, note_type=0, idx=3)
        model = NoteModel()
        model.notes_tree = [hold, later, tap, later_tap]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 1)
        self.assertEqual((hold.end, hold.gate), (360, 260))
        self.assertEqual((tap.end, tap.gate), (500, 400))

    def test_hold_tail_trims_before_next_same_hand_note_any_lane(self):
        # 新規格：同一支手、時間最近的下一個音符，不論 lane 是否重疊都要裁切
        hold = make_note(100, 500, 4, 6, note_type=2, hand=0)
        other = make_note(300, 350, 20, 22, note_type=0, hand=0, idx=1)  # 不同 lane、同手
        model = NoteModel()
        model.notes_tree = [hold, other]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 1)
        self.assertEqual((hold.end, hold.gate), (260, 160))  # 300 - 40

    def test_hold_tail_ignores_other_hand(self):
        hold = make_note(100, 500, 4, 6, note_type=2, hand=0)
        other = make_note(300, 350, 4, 6, note_type=0, hand=1, idx=1)  # 左手 → 不影響
        model = NoteModel()
        model.notes_tree = [hold, other]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 0)
        self.assertEqual(hold.end, 500)

    def test_hold_tail_ignores_same_start_chord(self):
        # 同一時間開始的音符視為和弦（不算「下一個」）→ 不觸發裁切
        hold = make_note(100, 500, 4, 6, note_type=2, hand=0)
        chord = make_note(100, 150, 5, 7, note_type=0, hand=0, idx=1)
        far = make_note(600, 650, 4, 6, note_type=0, hand=0, idx=2)  # 間距夠 → 不裁
        model = NoteModel()
        model.notes_tree = [hold, chord, far]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 0)
        self.assertEqual(hold.end, 500)

    def test_hold_tail_next_note_any_type_triggers(self):
        # 下一個音符不論類型（此處 staccato=3）都會觸發裁切
        hold = make_note(100, 500, 4, 6, note_type=2, hand=0)
        stac = make_note(450, 460, 4, 6, note_type=3, hand=0, idx=1)
        model = NoteModel()
        model.notes_tree = [hold, stac]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 1)
        self.assertEqual(hold.end, 410)  # 450 - 40

    def test_hold_tail_next_note_crosses_tracks(self):
        # 同一支手被拆在不同 track（鋼琴譜常見）→ 仍要當成同一條時間軸
        hold = make_note(100, 500, 4, 6, note_type=2, hand=0, track=0)
        other_track = make_note(300, 350, 20, 22, hand=0, idx=1, track=3)
        model = NoteModel()
        model.notes_tree = [hold, other_track]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 1)
        self.assertEqual((hold.end, hold.gate), (260, 160))  # 300 - 40

    def test_hold_tail_takes_earliest_onset_across_tracks(self):
        # 兩個 track 都有同手音符 → 取「下一個最早的時間」，不是遇到的第一個
        hold = make_note(100, 900, 4, 6, note_type=2, hand=0, track=0)
        late = make_note(700, 750, 8, 10, hand=0, idx=1, track=0)
        early = make_note(400, 450, 20, 22, hand=0, idx=2, track=5)
        model = NoteModel()
        model.notes_tree = [hold, late, early]

        self.assertEqual(model.resolve_hold_tail_overlaps(40), 1)
        self.assertEqual((hold.end, hold.gate), (360, 260))  # 400 - 40

    def test_hold_tail_gap_does_not_lengthen_holds(self):
        # 間距本來就夠 → 一律不動（只縮短、不拉長）
        hold = make_note(100, 200, 4, 6, note_type=2, hand=0, track=0)
        far = make_note(900, 950, 4, 6, hand=0, idx=1, track=1)
        model = NoteModel()
        model.notes_tree = [hold, far]

        self.assertEqual(model.enforce_hold_tail_gap(40), 0)
        self.assertEqual(hold.end, 200)

    def test_enforce_hold_tail_gap_targets_subset_only(self):
        # targets 限定範圍，但時間軸仍看整份譜面
        a = make_note(100, 900, 4, 6, note_type=2, hand=0, track=0)
        b = make_note(100, 900, 8, 10, note_type=2, hand=0, idx=1, track=1)
        blocker = make_note(300, 350, 20, 22, hand=0, idx=2, track=2)
        model = NoteModel()
        model.notes_tree = [a, b, blocker]

        self.assertEqual(model.enforce_hold_tail_gap(40, targets=[a]), 1)
        self.assertEqual(a.end, 260)
        self.assertEqual(b.end, 900)   # 不在 targets 內 → 不動

    def test_next_same_hand_onset_ignores_other_hand_and_chord(self):
        hold = make_note(100, 900, 4, 6, note_type=2, hand=0, track=0)
        chord = make_note(100, 150, 8, 10, hand=0, idx=1, track=1)
        left = make_note(300, 350, 4, 6, hand=1, idx=2, track=1)
        right_later = make_note(600, 650, 4, 6, hand=0, idx=3, track=4)
        model = NoteModel()
        model.notes_tree = [hold, chord, left, right_later]

        self.assertEqual(model.next_same_hand_onset(hold), 600)

    def test_horizontal_overlap_moves_only_left_note(self):
        left = make_note(100, 200, 5, 8, idx=0)
        right = make_note(100, 200, 7, 9, idx=1)
        model = NoteModel()
        model.notes_tree = [left, right]

        self.assertEqual(model.resolve_horizontal_overlaps(), 1)
        self.assertEqual((left.min_key, left.max_key), (3, 6))
        self.assertEqual((right.min_key, right.max_key), (7, 9))

    def test_horizontal_overlap_handles_chain_from_right_to_left(self):
        left = make_note(100, 200, 5, 7, idx=0)
        middle = make_note(100, 200, 6, 8, idx=1)
        right = make_note(100, 200, 7, 9, idx=2)
        model = NoteModel()
        model.notes_tree = [left, middle, right]

        self.assertEqual(model.resolve_horizontal_overlaps(), 2)
        self.assertEqual((left.min_key, left.max_key), (1, 3))
        self.assertEqual((middle.min_key, middle.max_key), (4, 6))
        self.assertEqual((right.min_key, right.max_key), (7, 9))

    def test_horizontal_overlap_does_not_mix_different_start_times(self):
        first = make_note(100, 500, 5, 8, note_type=2, idx=0)
        later = make_note(200, 250, 7, 9, idx=1)
        model = NoteModel()
        model.notes_tree = [first, later]

        self.assertEqual(model.resolve_horizontal_overlaps(), 0)
        self.assertEqual((first.min_key, first.max_key), (5, 8))

    def test_horizontal_tolerance_catches_near_simultaneous_chord(self):
        # MIDI 轉出的和弦常差幾毫秒；容差 0 抓不到，給容差就要能分開
        left = make_note(1000, 1100, 5, 7, idx=0)
        right = make_note(1001, 1101, 6, 8, idx=1)
        model = NoteModel()
        model.notes_tree = [left, right]

        self.assertEqual(model.resolve_horizontal_overlaps(0), 0)      # 舊行為
        self.assertEqual(model.resolve_horizontal_overlaps(30), 1)     # 有容差才動
        self.assertEqual((left.min_key, left.max_key), (3, 5))
        self.assertEqual((right.min_key, right.max_key), (6, 8))

    def test_horizontal_tolerance_chain_of_three(self):
        a = make_note(1000, 1100, 5, 7, idx=0)
        b = make_note(1002, 1100, 6, 8, idx=1)
        c = make_note(1005, 1100, 7, 9, idx=2)
        model = NoteModel()
        model.notes_tree = [a, b, c]

        self.assertEqual(model.resolve_horizontal_overlaps(30), 2)
        self.assertEqual((a.min_key, a.max_key), (1, 3))
        self.assertEqual((b.min_key, b.max_key), (4, 6))
        self.assertEqual((c.min_key, c.max_key), (7, 9))

    def test_horizontal_pushes_right_note_when_left_edge_blocks(self):
        # 左邊那顆已經貼齊 0 號鍵 → 改推右邊那顆往右，而不是一顆都不動
        left = make_note(0, 200, 0, 5, idx=0)
        right = make_note(0, 200, 3, 8, idx=1)
        model = NoteModel()
        model.notes_tree = [left, right]

        report = model.resolve_horizontal_overlaps_report(0)
        self.assertEqual(report['moved'], 1)
        self.assertEqual(report['unresolved'], 0)
        self.assertEqual((left.min_key, left.max_key), (0, 5))
        self.assertEqual((right.min_key, right.max_key), (6, 11))

    def test_horizontal_reports_unresolved_when_no_room_either_side(self):
        wide = make_note(0, 200, 0, 27, idx=0)
        other = make_note(0, 200, 3, 8, idx=1)
        model = NoteModel()
        model.notes_tree = [wide, other]

        report = model.resolve_horizontal_overlaps_report(0)
        self.assertEqual(report['moved'], 0)
        self.assertEqual(report['unresolved'], 1)

    def test_time_overlap_moves_later_note_off_a_sounding_hold(self):
        hold = make_note(0, 1000, 5, 8, note_type=2, idx=0)
        later = make_note(400, 500, 7, 9, idx=1)
        model = NoteModel()
        model.notes_tree = [hold, later]

        # 預設（只看和弦）抓不到 —— 起音時間差太多
        self.assertEqual(model.resolve_horizontal_overlaps(30), 0)
        # 打開時間重疊才會處理，而且動的是後起音的那顆
        self.assertEqual(model.resolve_horizontal_overlaps(30, time_overlap=True), 1)
        self.assertEqual((hold.min_key, hold.max_key), (5, 8))
        self.assertEqual((later.min_key, later.max_key), (9, 11))

    def test_time_overlap_prefers_the_side_the_note_is_already_on(self):
        hold = make_note(0, 1000, 10, 13, note_type=2, idx=0)
        from_left = make_note(400, 500, 8, 11, idx=1)   # 中心偏左 → 往左讓
        model = NoteModel()
        model.notes_tree = [hold, from_left]

        self.assertEqual(model.resolve_horizontal_overlaps(30, time_overlap=True), 1)
        self.assertEqual((from_left.min_key, from_left.max_key), (6, 9))

    def test_time_overlap_ignores_notes_that_already_ended(self):
        first = make_note(0, 300, 5, 8, idx=0)
        second = make_note(400, 500, 5, 8, idx=1)   # 前一顆早就結束了
        model = NoteModel()
        model.notes_tree = [first, second]

        self.assertEqual(model.resolve_horizontal_overlaps(30, time_overlap=True), 0)
        self.assertEqual((second.min_key, second.max_key), (5, 8))

    def test_horizontal_tolerance_still_ignores_far_apart_notes(self):
        # 相隔遠超過容差 → 視覺上不同時，不該被搬動
        first = make_note(1000, 1500, 5, 8, note_type=2, idx=0)
        later = make_note(1100, 1150, 7, 9, idx=1)
        model = NoteModel()
        model.notes_tree = [first, later]

        self.assertEqual(model.resolve_horizontal_overlaps(30), 0)
        self.assertEqual((first.min_key, first.max_key), (5, 8))


if __name__ == '__main__':
    unittest.main()


class FindingOverlapsTests(unittest.TestCase):
    """只找不動的那一支（`overlapping_pairs`），畫面標記和報告都走它。

    判準是「編輯器畫出來的兩個方塊有沒有相交」，所以**非長押**也算——
    `chart_view._note_rect` 連 tap 都是從 start 畫到 end。
    """

    def test_two_notes_on_the_same_lane_at_the_same_time(self):
        a = make_note(0, 200, 4, 6)
        b = make_note(10, 200, 6, 8, idx=1)
        got = overlapping_pairs([a, b])
        self.assertEqual([(k, x.idx, y.idx) for k, x, y in got],
                         [(OVERLAP_CHORD, 0, 1)])

    def test_apart_in_lanes_is_not_an_overlap(self):
        a = make_note(0, 200, 4, 6)
        b = make_note(0, 200, 7, 9, idx=1)
        self.assertEqual(overlapping_pairs([a, b]), [])

    def test_touching_lanes_is_an_overlap(self):
        # max_key 和 min_key 同一格 ＝ 共用那個按鍵
        a = make_note(0, 200, 4, 6)
        b = make_note(0, 200, 6, 9, idx=1)
        self.assertEqual(len(overlapping_pairs([a, b])), 1)

    def test_a_note_inside_a_hold_corridor(self):
        hold = make_note(0, 1000, 4, 6, note_type=2)
        later = make_note(400, 500, 5, 7, idx=1)
        got = overlapping_pairs([hold, later])
        self.assertEqual([k for k, _a, _b in got], [OVERLAP_HOLD])

    def test_a_trill_also_has_a_corridor(self):
        trill = make_note(0, 1000, 4, 6, note_type=0x40)
        later = make_note(400, 500, 5, 7, idx=1)
        self.assertEqual([k for k, _a, _b in overlapping_pairs([trill, later])],
                         [OVERLAP_HOLD])

    def test_only_the_sounding_length_runs_over(self):
        # 兩顆都是 tap，前一顆的音長還沒走完：方塊疊在一起，但只差在時值。
        a = make_note(0, 300, 4, 6)
        b = make_note(200, 400, 5, 7, idx=1)
        self.assertEqual([k for k, _a, _b in overlapping_pairs([a, b])],
                         [OVERLAP_TAIL])

    def test_end_to_start_is_not_an_overlap(self):
        a = make_note(0, 200, 4, 6)
        b = make_note(200, 400, 5, 7, idx=1)
        self.assertEqual(overlapping_pairs([a, b]), [])

    def test_a_kind_can_be_left_out(self):
        a = make_note(0, 300, 4, 6)
        b = make_note(200, 400, 5, 7, idx=1)
        self.assertEqual(
            overlapping_pairs([a, b], kinds=(OVERLAP_CHORD, OVERLAP_HOLD)), [])

    def test_the_pairs_come_back_in_time_order(self):
        notes = [make_note(0, 300, 4, 6, idx=0),
                 make_note(200, 500, 5, 7, idx=1),
                 make_note(400, 600, 5, 7, idx=2)]
        got = overlapping_pairs(notes)
        self.assertEqual([y.idx for _k, _x, y in got], [1, 2])

    def test_one_long_note_can_hit_several(self):
        hold = make_note(0, 1000, 4, 6, note_type=2)
        got = overlapping_pairs([hold,
                                 make_note(200, 300, 5, 7, idx=1),
                                 make_note(600, 700, 5, 7, idx=2)])
        self.assertEqual(len(got), 2)

    def test_it_agrees_with_the_tool_that_moves_them(self):
        """偵測和「處理左右重疊」看到的是同一批，不是兩套語意。"""
        notes = [make_note(0, 200, 4, 6, idx=0),
                 make_note(10, 200, 6, 8, idx=1),
                 make_note(600, 1600, 10, 12, note_type=2, idx=2),
                 make_note(900, 1000, 11, 13, idx=3)]
        model = NoteModel()
        model.notes_tree = list(notes)
        found = overlapping_pairs(notes, onset_ms=30)
        report = model.resolve_horizontal_overlaps_report(30, time_overlap=True)
        self.assertEqual(len(found), report['conflicts'])


class BrokenChordTests(unittest.TestCase):
    """分解和弦不該被裁。

    「同一支手後面有音就得放開」是錯的：手指按著前面的音、其它指頭繼續彈下去，
    那正是分解和弦。實測全曲庫 20896 顆和同手下一顆重疊的長押裡，85.6% 一隻手
    完全按得住。
    """

    def test_broken_chord_is_not_trimmed(self):
        # C 按著，接著彈 E、G —— 一隻手的事，不用放開。
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        e = make_note(200, 260, 6, 7, hand=0, idx=1, pitch=64)
        g = make_note(400, 460, 8, 9, hand=0, idx=2, pitch=67)
        model = NoteModel()
        model.notes_tree = [hold, e, g]

        self.assertEqual(model.enforce_hold_tail_gap(40), 0)
        self.assertEqual(hold.end, 2000)

    def test_same_pitch_still_forces_release(self):
        # 同一個鍵：沒放開就按不下去。
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        again = make_note(600, 660, 4, 5, hand=0, idx=1, pitch=60)
        model = NoteModel()
        model.notes_tree = [hold, again]

        self.assertEqual(model.enforce_hold_tail_gap(40), 1)
        self.assertEqual((hold.end, hold.gate), (560, 560))

    def test_sixth_finger_forces_release(self):
        # 五根手指按滿了，第六個鍵就得放掉一個。
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        rest = [make_note(100 * (i + 1), 2000, 6 + i, 7 + i, hand=0, idx=i + 1,
                          pitch=62 + i) for i in range(5)]
        model = NoteModel()
        model.notes_tree = [hold] + rest

        self.assertEqual(model.enforce_hold_tail_gap(40), 1)
        self.assertEqual(hold.end, 460)      # 第六顆在 500

    def test_stretch_beyond_the_hand_forces_release(self):
        # 相差兩個八度，撐不開。
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=48)
        far = make_note(300, 360, 14, 15, hand=0, idx=1, pitch=72)
        model = NoteModel()
        model.notes_tree = [hold, far]

        self.assertEqual(model.enforce_hold_tail_gap(40), 1)
        self.assertEqual(hold.end, 260)

    def test_exactly_at_the_reach_limit_is_still_playable(self):
        # 大九度（14 個半音）還按得住，第 15 個就不行。
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        ninth = make_note(300, 360, 8, 9, hand=0, idx=1, pitch=74)
        model = NoteModel()
        model.notes_tree = [hold, ninth]
        self.assertEqual(model.enforce_hold_tail_gap(40), 0)

        hold2 = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        tenth = make_note(300, 360, 8, 9, hand=0, idx=1, pitch=75)
        model2 = NoteModel()
        model2.notes_tree = [hold2, tenth]
        self.assertEqual(model2.enforce_hold_tail_gap(40), 1)

    def test_without_pitch_the_rule_falls_back_to_lanes(self):
        # 沒有音高的譜面改用鍵道量：7 條以內按得住，再遠就不行。
        near_hold = make_note(0, 2000, 4, 5, note_type=2, hand=0)
        near = make_note(300, 360, 9, 10, hand=0, idx=1)
        near_model = NoteModel()
        near_model.notes_tree = [near_hold, near]
        self.assertEqual(near_model.enforce_hold_tail_gap(40), 0)

        far_hold = make_note(0, 2000, 4, 5, note_type=2, hand=0)
        far = make_note(300, 360, 20, 21, hand=0, idx=1)
        far_model = NoteModel()
        far_model.notes_tree = [far_hold, far]
        self.assertEqual(far_model.enforce_hold_tail_gap(40), 1)
        self.assertEqual(far_hold.end, 260)

    def test_deadline_is_the_first_real_conflict_not_the_first_note(self):
        # 前面幾顆都按得住，卡在真正衝突的那一顆。
        hold = make_note(0, 3000, 4, 5, note_type=2, hand=0, pitch=60)
        ok1 = make_note(200, 260, 6, 7, hand=0, idx=1, pitch=64)
        ok2 = make_note(400, 460, 8, 9, hand=0, idx=2, pitch=67)
        clash = make_note(900, 960, 4, 5, hand=0, idx=3, pitch=60)
        model = NoteModel()
        model.notes_tree = [hold, ok1, ok2, clash]

        self.assertEqual(model.enforce_hold_tail_gap(40), 1)
        self.assertEqual(hold.end, 860)

class TailGapModeTests(unittest.TestCase):
    """兩種裁法都要能用。

    預設只裁「真的搶到同一個鍵」的（官方就是這樣：real 難度 13175 顆長押有 7.7%
    的尾巴蓋過同手的下一顆，而那 1019 顆沒有任何一顆的鍵道是重疊的）。想要舊的
    「一律裁到同手下一顆」時把 only_conflicts 關掉。
    """

    @staticmethod
    def broken_chord():
        hold = make_note(0, 2000, 4, 5, note_type=2, hand=0, pitch=60)
        nxt = make_note(300, 360, 7, 8, hand=0, idx=1, pitch=67)
        model = NoteModel()
        model.notes_tree = [hold, nxt]
        return model, hold

    def test_default_keeps_the_broken_chord(self):
        model, hold = self.broken_chord()
        self.assertEqual(model.resolve_hold_tail_overlaps(40), 0)
        self.assertEqual(hold.end, 2000)

    def test_old_behaviour_is_still_available(self):
        model, hold = self.broken_chord()
        self.assertEqual(model.resolve_hold_tail_overlaps(40, only_conflicts=False), 1)
        self.assertEqual(hold.end, 260)

    def test_contested_key_is_cut_in_both_modes(self):
        for only in (True, False):
            hold = make_note(0, 2000, 4, 6, note_type=2, hand=0, pitch=60)
            # 鍵道 5~7 和長押的 4~6 重疊 —— 同一根手指的位置
            clash = make_note(600, 660, 5, 7, hand=0, idx=1, pitch=62)
            model = NoteModel()
            model.notes_tree = [hold, clash]
            self.assertEqual(model.enforce_hold_tail_gap(40, only_conflicts=only), 1)
            self.assertEqual(hold.end, 560)



class DenseChordNarrowingTests(unittest.TestCase):
    """k≥4 一律收窄（使用者風格），不是只有剛好 4 音（官方風格）。

    使用者回報「排譜器自己生的，第 10 小節那幾個左手和弦都在亂飄」。追到
    `_initial_hand_widths` 的 `len(hand_notes) == dense_hand_threshold`——
    **剛好等於 4** 才收窄，5 顆就掉出去拿寬度 3。5×3＝15 格，貼著鍵盤邊緣
    放不下，整組只好往旁邊滑：實測同一顆低音在 1.5 秒內從鍵道 20 掉到 12。

    官方 real 的收窄率 k=4 是 88.4%、**k=5 掉回 24.2%**（官方改用重疊擠空間），
    使用者的 94 份譜則是 k≥5、跨度 12+ 收窄 **99%**。所以這是風格差異，用
    `dense_hand_threshold_is_floor` 分開，官方那條原樣不動。

    全語料 A/B（8 首、33155 顆）：同音跳≥3格 −25.3%、**≥6格 −48.5%**、
    貼邊 −14.9%。
    """

    def notes(self, pitches):
        out = []
        for i, pitch in enumerate(pitches):
            n = GNote(None, i)
            n.start, n.end, n.gate = 0, 100, 100
            n.pitch, n.hand, n.note_type = pitch, 0, 0
            n.min_key, n.max_key = 0, 2
            out.append(n)
        return out

    def widths(self, style, pitches):
        cfg = SC.settings_for_style(style)
        ns = self.notes(pitches)
        return SC._initial_hand_widths(
            ns, cfg, SC._hand_span_wants_narrowing(ns, cfg))

    def test_five_notes_narrow_for_the_user(self):
        self.assertEqual(self.widths('eather', [74, 76, 80, 83, 86]),
                         [2, 2, 2, 2, 2])

    def test_five_notes_stay_wide_for_the_official_style(self):
        """官方 k=5 的收窄率只有 24.2%，那條不能跟著改。"""
        self.assertEqual(self.widths('official', [74, 76, 80, 83, 86]),
                         [3, 3, 3, 3, 3])

    def test_four_notes_narrow_in_both_styles(self):
        for style in ('eather', 'official'):
            self.assertEqual(self.widths(style, [74, 76, 80, 83]),
                             [2, 2, 2, 2], style)

    def test_six_notes_also_narrow_for_the_user(self):
        self.assertEqual(self.widths('eather', [60, 64, 67, 71, 74, 78]),
                         [2] * 6)

    def test_a_wide_span_no_longer_escapes_the_rule(self):
        """跨度 ≥12 半音會短路掉後面的收窄，密集和弦必須在那之前就處理掉。"""
        self.assertEqual(self.widths('eather', [50, 57, 64, 71, 78]),
                         [2, 2, 2, 2, 2])

    def test_the_flag_is_what_switches_it(self):
        cfg = SC.settings_for_style('eather', dense_hand_threshold_is_floor=False)
        ns = self.notes([74, 76, 80, 83, 86])
        self.assertEqual(
            SC._initial_hand_widths(ns, cfg,
                                    SC._hand_span_wants_narrowing(ns, cfg)),
            [3, 3, 3, 3, 3])
