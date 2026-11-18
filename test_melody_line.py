# -*- coding: utf-8 -*-
"""冠音／低音的旋律線。

使用者要的是「排前面、輔助後面」再多一點輔助，第一個點名的就是**找冠音**。
這裡釘住三件事：

* 冠音／低音怎麼認（和排譜器 `_repair_melody_slope` 同一套，不另立語意）。
* 哪些算破線——方向只在同一條線（600ms）內比，同音不同格看 4 秒。
* **低音只畫不判**：官方譜的低音有 38.6% 是反方向走的，拿方向規則去檢查它
  只會製造假警報。
"""

import unittest

from qt_editor import melody_line as ML
from qt_editor.models import GNote

RIGHT, LEFT = 0, 1


def make(idx, start, pitch, lane_hi, width=3, hand=RIGHT, dur=200):
    """一顆音符。`lane_hi` 是**右緣**——方向規則看的就是右緣。"""
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = hand
    n.max_key = lane_hi
    n.min_key = max(0, lane_hi - width + 1)
    return n


class WhoIsTheTopNoteTests(unittest.TestCase):
    def test_the_highest_pitch_of_a_chord_is_the_top(self):
        notes = [make(0, 0, 60, 10), make(1, 0, 64, 14), make(2, 0, 67, 18)]
        self.assertEqual([n.idx for n in ML.tops(notes)], [2])
        self.assertEqual([n.idx for n in ML.bottoms(notes)], [0])

    def test_same_pitch_breaks_the_tie_on_the_right_edge(self):
        # 同音高同時按下去（兩隻手撞在一起）：冠音取右緣遠的那顆，
        # 低音取左緣近的那顆——和排譜器的 key 一樣。
        notes = [make(0, 0, 60, 10), make(1, 0, 60, 20)]
        self.assertEqual(ML.tops(notes, by_hand=False)[0].idx, 1)
        self.assertEqual(ML.bottoms(notes, by_hand=False)[0].idx, 0)

    def test_each_hand_gets_its_own_line(self):
        notes = [make(0, 0, 40, 6, hand=LEFT), make(1, 0, 44, 9, hand=LEFT),
                 make(2, 0, 70, 20), make(3, 0, 74, 24)]
        series = ML.events(notes)
        self.assertEqual(sorted(series), [RIGHT, LEFT])
        self.assertEqual(series[RIGHT][0].top.idx, 3)
        self.assertEqual(series[LEFT][0].top.idx, 1)

    def test_ignoring_the_hand_leaves_one_line(self):
        notes = [make(0, 0, 40, 6, hand=LEFT), make(1, 0, 74, 24)]
        series = ML.events(notes, by_hand=False)
        self.assertEqual(list(series), [0])
        self.assertEqual(series[0][0].top.idx, 1)

    def test_notes_without_pitch_or_hidden_are_not_in_the_line(self):
        plain = make(0, 0, 60, 10)
        no_pitch = make(1, 0, 90, 20)
        no_pitch.pitch = None
        hidden = make(2, 0, 95, 24)
        hidden.hidden = True
        tops = ML.tops([plain, no_pitch, hidden], by_hand=False)
        self.assertEqual([n.idx for n in tops], [0])


class GroupingTests(unittest.TestCase):
    def test_a_run_of_close_notes_is_not_rolled_into_one_chord(self):
        # 每顆間隔 30ms：和「前一顆」比會滾成一組，和「這組的第一顆」比
        # 才會切開。整段琶音被當成一個和弦的話只會留下一顆冠音。
        notes = [make(i, i * 30, 60 + i, 10 + i) for i in range(5)]
        self.assertEqual(len(ML.tops(notes)), 3)      # 和前一顆比的話只會剩 1 組

    def test_notes_within_the_tolerance_are_one_chord(self):
        notes = [make(0, 0, 60, 10), make(1, 20, 64, 14), make(2, 34, 67, 18)]
        self.assertEqual([n.idx for n in ML.tops(notes)], [2])


class ChainTests(unittest.TestCase):
    def test_a_long_rest_starts_a_new_chain(self):
        notes = [make(0, 0, 60, 10), make(1, 500, 62, 12),
                 make(2, 2000, 64, 14)]
        found = ML.chains(ML.events(notes)[RIGHT])
        self.assertEqual([len(c) for c in found], [2, 1])

    def test_direction_is_not_judged_across_the_rest(self):
        # 休止符之後的第一顆沒有「上一步」，所以即使方向反了也不算破線。
        notes = [make(0, 0, 60, 20), make(1, 2000, 72, 4)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_DIRECTION,)), [])

    def test_a_long_note_does_not_cut_the_line(self):
        """比的是中間空了多久，不是起音差多遠。

        使用者：「沒有連續的音符也要連，不能前後只是隔一點點就忽略旋律線」。
        兩顆起音差 2100ms（遠超過 600），但中間只空了 100ms。
        """
        notes = [make(0, 0, 60, 10, dur=2000), make(1, 2100, 64, 13)]
        found = ML.chains(ML.events(notes)[RIGHT])
        self.assertEqual([len(c) for c in found], [2])
        self.assertEqual(len(ML.breaks(notes, kinds=(ML.KIND_DIRECTION,))), 0)

    def test_a_long_note_followed_by_a_real_rest_still_breaks(self):
        # 長押放開之後真的空了 1000ms：那是休止，不是同一條線。
        notes = [make(0, 0, 60, 10, dur=2000), make(1, 3000, 64, 13)]
        found = ML.chains(ML.events(notes)[RIGHT])
        self.assertEqual([len(c) for c in found], [1, 1])

    def test_the_end_of_a_chord_is_its_last_release(self):
        """同一組裡有長押就算手還壓著——這份切法同時給低音線和規則用。"""
        notes = [make(0, 0, 72, 16), make(1, 0, 55, 6, dur=2000),
                 make(2, 2100, 74, 18)]
        found = ML.chains(ML.events(notes)[RIGHT])
        self.assertEqual([len(c) for c in found], [2])


class DirectionTests(unittest.TestCase):
    def rising(self, second_edge):
        return [make(0, 0, 60, 10), make(1, 300, 67, second_edge)]

    def test_pitch_up_and_lane_right_is_fine(self):
        self.assertEqual(ML.breaks(self.rising(13)), [])

    def test_pitch_up_but_lane_stays_is_a_break(self):
        found = ML.breaks(self.rising(10))
        self.assertEqual([b.kind for b in found], [ML.KIND_DIRECTION])
        self.assertEqual(found[0].second.idx, 1)
        self.assertEqual(found[0].suggest, 1)     # 往右一格就不算破線

    def test_pitch_up_but_lane_goes_left_is_a_break(self):
        found = ML.breaks(self.rising(7))
        self.assertEqual(found[0].edge_delta, -3)
        self.assertEqual(found[0].suggest, 4)     # −3 → +1

    def test_pitch_down_wants_the_lane_to_go_left(self):
        notes = [make(0, 0, 67, 14), make(1, 300, 60, 14)]
        found = ML.breaks(notes)
        self.assertEqual(found[0].suggest, -1)

    def test_a_big_interval_only_has_to_go_the_right_way(self):
        # 「大音程少走一點可以接受」是使用者自己講的：走一格就夠，
        # 步伐要走多少是排譜器那條目標曲線的事，不是這裡的。
        notes = [make(0, 0, 60, 10), make(1, 300, 84, 11)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_DIRECTION,)), [])

    def test_the_bottom_line_is_never_judged(self):
        # 低音反向、冠音守規矩：一處都不該報。
        notes = [make(0, 0, 60, 10, width=1), make(1, 0, 48, 2, width=1),
                 make(2, 300, 64, 14, width=1), make(3, 300, 40, 6, width=1)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_DIRECTION,)), [])


class RepeatTests(unittest.TestCase):
    def test_the_same_pitch_on_another_lane_is_a_break(self):
        notes = [make(0, 0, 60, 10), make(1, 1000, 60, 14)]
        found = ML.breaks(notes, kinds=(ML.KIND_REPEAT,))
        self.assertEqual([b.kind for b in found], [ML.KIND_REPEAT])
        self.assertEqual(found[0].suggest, -4)    # 搬回原來那一格

    def test_the_same_pitch_on_the_same_lane_is_fine(self):
        notes = [make(0, 0, 60, 10), make(1, 1000, 60, 10)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_REPEAT,)), [])

    def test_far_apart_is_not_compared(self):
        notes = [make(0, 0, 60, 10), make(1, 9000, 60, 14)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_REPEAT,)), [])

    def test_the_same_pitch_is_never_a_direction_break(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 60, 14)]
        found = ML.breaks(notes, kinds=(ML.KIND_DIRECTION,))
        self.assertEqual(found, [])

    def test_repeat_looks_further_than_a_chain(self):
        # 中間隔了 2 秒（線斷了），同音不同格照樣要報——4 秒內的同一個音高
        # 在聽覺上還是同一個位置。
        notes = [make(0, 0, 60, 10), make(1, 2000, 60, 14)]
        found = ML.breaks(notes, kinds=(ML.KIND_REPEAT,))
        self.assertEqual(len(found), 1)


class RepairTests(unittest.TestCase):
    """把破掉的方向拉回來。只動後面那一組，而且整組一起動。"""

    def test_a_broken_step_gets_pushed_the_right_way(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 10)]
        self.assertEqual(ML.repair_direction(notes), 1)
        self.assertEqual(notes[1].max_key, 11)
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_DIRECTION,)), [])

    def test_a_chart_that_already_obeys_is_left_alone(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 14)]
        self.assertEqual(ML.repair_direction(notes), 0)
        self.assertEqual(notes[1].max_key, 14)

    def test_the_whole_chord_moves_as_one(self):
        # 和絃內部的幾何一格都不能變——這是排譜器那邊的規矩，這裡照搬。
        notes = [make(0, 0, 60, 10),
                 make(1, 300, 64, 7, width=2), make(2, 300, 67, 10, width=2)]
        self.assertEqual(ML.repair_direction(notes), 2)
        self.assertEqual((notes[1].min_key, notes[1].max_key), (7, 8))
        self.assertEqual((notes[2].min_key, notes[2].max_key), (10, 11))

    def test_it_will_not_move_notes_that_are_not_free(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 10)]
        self.assertEqual(ML.repair_direction(notes, movable=set()), 0)
        self.assertEqual(notes[1].max_key, 10)

    def test_a_trill_is_never_moved(self):
        # 顫音的鍵道還連著它每一格子音符，整組平移會把它拆散。
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 10)]
        notes[1].note_type = 0x40
        self.assertEqual(ML.repair_direction(notes), 0)
        self.assertEqual(notes[1].max_key, 10)

    def test_it_stays_on_the_keyboard(self):
        notes = [make(0, 0, 60, 27, width=1), make(1, 300, 67, 27, width=1)]
        self.assertEqual(ML.repair_direction(notes), 0)

    def test_it_steps_over_whatever_is_in_the_way(self):
        notes = [make(0, 0, 60, 10, width=1),
                 make(1, 300, 67, 10, width=1),
                 make(2, 300, 50, 11, width=1, hand=LEFT)]
        self.assertEqual(ML.repair_direction(notes), 1)
        self.assertEqual(notes[1].max_key, 12)      # 11 被左手佔著
        self.assertEqual(notes[2].max_key, 11)      # 擋路的那顆不動

    def test_the_repeat_budget_protects_an_aligned_pitch(self):
        def chart():
            return [make(0, 0, 60, 10), make(1, 300, 67, 10),
                    make(2, 1000, 67, 10)]
        strict = chart()
        self.assertEqual(ML.repair_direction(strict, repeat_budget=0), 0)
        loose = chart()
        self.assertEqual(ML.repair_direction(loose, repeat_budget=1), 1)
        self.assertEqual(loose[1].max_key, 11)


class SlopeTests(unittest.TestCase):
    """第三種破線：走對邊了，但走的格數差太多。

    門檻 3 格的來源：使用者 221 份譜、187057 步，差距 95% 在 3 格內。
    """

    def step(self, interval, lanes):
        return [make(0, 0, 60, 10), make(1, 300, 60 + interval, 10 + lanes)]

    def test_a_tiny_interval_that_flies_is_a_break(self):
        # 2 個半音該走 1.02 格，飛了 8 格。
        found = ML.breaks(self.step(2, 8), kinds=(ML.KIND_SLOPE,))
        self.assertEqual([b.kind for b in found], [ML.KIND_SLOPE])
        self.assertAlmostEqual(found[0].excess, 8 - 1.02, places=2)
        self.assertEqual(found[0].suggest, -7)      # 往回縮 7 格

    def test_a_leap_that_barely_moves_is_a_break(self):
        # 一個八度該走 5.05 格，只挪了一格。
        found = ML.breaks(self.step(12, 1), kinds=(ML.KIND_SLOPE,))
        self.assertAlmostEqual(found[0].excess, 1 - 5.05, places=2)
        self.assertEqual(found[0].suggest, 4)       # 再往右 4 格

    def test_going_down_is_measured_the_same_way(self):
        found = ML.breaks(self.step(-2, -8), kinds=(ML.KIND_SLOPE,))
        self.assertEqual(found[0].suggest, 7)       # 往回（右）縮 7 格

    def test_a_normal_step_is_not_a_break(self):
        self.assertEqual(ML.breaks(self.step(7, 3), kinds=(ML.KIND_SLOPE,)), [])

    def test_the_wrong_side_belongs_to_the_direction_check(self):
        """走錯邊／沒動只報一次，而且報的是方向，不是步伐。"""
        for lanes in (0, -6):
            found = ML.breaks(self.step(2, lanes))
            self.assertEqual([b.kind for b in found], [ML.KIND_DIRECTION],
                             lanes)

    def test_the_learnt_curve_wins(self):
        # 這份譜 2 個半音本來就走 8 格的話，走 8 格不算差。
        self.assertEqual(
            ML.breaks(self.step(2, 8), kinds=(ML.KIND_SLOPE,),
                      curve={2: 8.0}), [])

    def test_a_negative_learnt_step_is_clamped_to_zero(self):
        # 整段反著排的譜會學到負的中位數，那時「該走幾格」沒有意義。
        notes = self.step(2, 2)
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_SLOPE,),
                                   curve={2: -5.0}), [])

    def test_the_rest_breaks_the_line_here_too(self):
        notes = [make(0, 0, 60, 10), make(1, 2000, 62, 25)]
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_SLOPE,)), [])

    def test_the_tolerance_can_be_loosened(self):
        notes = self.step(2, 4)                     # 差 2.98 格
        self.assertEqual(ML.breaks(notes, kinds=(ML.KIND_SLOPE,)), [])
        self.assertEqual(len(ML.breaks(notes, kinds=(ML.KIND_SLOPE,),
                                       slope_tolerance=1.0)), 1)

    def test_every_kind_is_checked_by_default(self):
        self.assertEqual(set(ML.ALL_KINDS),
                         {ML.KIND_DIRECTION, ML.KIND_REPEAT, ML.KIND_SLOPE})
        found = ML.breaks(self.step(2, 8))
        self.assertEqual([b.kind for b in found], [ML.KIND_SLOPE])


class StepCurveTests(unittest.TestCase):
    """「差幾個半音 → 右緣走幾格」：先學使用者的，再退回量好的表。"""

    def test_it_learns_the_median_step(self):
        # 同樣是 2 個半音，三次分別走了 1、3、1 格 → 取中位數 1
        notes = []
        for i, (lo, hi) in enumerate(((10, 11), (10, 13), (10, 11))):
            at = i * 1000
            notes += [make(i * 2, at, 60, lo), make(i * 2 + 1, at + 300, 62, hi)]
        self.assertEqual(ML.learn_steps(notes)[2], 1.0)

    def test_one_sample_is_not_enough(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 62, 14)]
        self.assertEqual(ML.learn_steps(notes), {})

    def test_the_fallback_is_the_measured_table(self):
        self.assertEqual(ML.step_for(None, 1), ML.DEFAULT_STEPS[0])
        self.assertEqual(ML.step_for({}, 2), ML.DEFAULT_STEPS[1])

    def test_what_is_learnt_wins(self):
        self.assertEqual(ML.step_for({3: 9.0}, 3), 9.0)

    def test_beyond_the_table_it_extrapolates(self):
        # 截斷的話十四度以上全部走一樣遠，那不是樂譜。
        self.assertGreater(ML.step_for(None, 20), ML.DEFAULT_STEPS[-1])


class LeadByTopTests(unittest.TestCase):
    """冠音帶頭：先決定冠音去哪，整組跟著走。"""

    def test_the_top_lands_on_the_previous_plus_the_step(self):
        # 2 個半音 → 表上 1.02 格 → 一格。前一顆右緣 10，所以目標是 11。
        notes = [make(0, 0, 60, 10), make(1, 300, 62, 17)]
        self.assertEqual(ML.lead_by_top(notes), 1)
        self.assertEqual(notes[1].max_key, 11)

    def test_the_whole_group_follows(self):
        notes = [make(0, 0, 60, 10),
                 make(1, 300, 55, 13, width=2), make(2, 300, 62, 17, width=2)]
        self.assertEqual(ML.lead_by_top(notes), 2)
        self.assertEqual(notes[2].max_key, 11)      # 冠音到目標
        self.assertEqual(notes[1].max_key, 7)       # 低的那顆跟著移同樣的量

    def test_the_same_pitch_goes_back_to_the_same_lane(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 60, 16)]
        ML.lead_by_top(notes)
        self.assertEqual(notes[1].max_key, 10)

    def test_a_rest_starts_a_new_line(self):
        # 隔了 2 秒：沒有「上一顆」可以比，位置就別動。
        notes = [make(0, 0, 60, 10), make(1, 2000, 62, 17)]
        self.assertEqual(ML.lead_by_top(notes), 0)
        self.assertEqual(notes[1].max_key, 17)

    def test_a_long_note_is_not_a_rest(self):
        """和 `chains` 同一條線：比中間空了多久，不是起音差多遠。

        比起音的話，長押後面那一組會因為「沒有上一顆」被整組跳過不重排。
        """
        notes = [make(0, 0, 60, 10, dur=2000), make(1, 2100, 62, 17)]
        self.assertEqual(ML.lead_by_top(notes), 1)
        self.assertEqual(notes[1].max_key, 11)      # 照樣接著前一顆排

    def test_the_anchor_keeps_it_from_drifting(self):
        """相對鏈會累積誤差，所以目標要夾在「這個音高本來該在哪」附近。"""
        notes = [make(0, 0, 60, 10, width=1), make(1, 300, 62, 17, width=1)]
        # 這個音高的絕對位置在中心 20（寬 1 → 右緣 20），容許偏 1 格
        ML.lead_by_top(notes, anchor=lambda n: 20.0, drift_slack=1)
        self.assertEqual(notes[1].max_key, 19)

    def test_it_will_not_move_what_it_may_not_move(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 62, 17)]
        self.assertEqual(ML.lead_by_top(notes, movable=set()), 0)
        notes[1].note_type = 0x40
        self.assertEqual(ML.lead_by_top(notes), 0)

    def test_an_immovable_group_is_still_the_previous_top(self):
        """動不了的那一組就在譜上，下一步要和它比，不是跳過去和更早的比。"""
        notes = [make(0, 0, 60, 10), make(1, 300, 64, 20), make(2, 600, 66, 25)]
        ML.lead_by_top(notes, movable={id(notes[2])})
        # 和第 2 顆（右緣 20）比：2 個半音 → 一格 → 21
        self.assertEqual(notes[2].max_key, 21)

    def test_it_steps_aside_when_the_target_is_taken(self):
        notes = [make(0, 0, 60, 10, width=1),
                 make(1, 300, 62, 17, width=1),
                 make(2, 300, 50, 11, width=1, hand=LEFT)]
        ML.lead_by_top(notes)
        self.assertEqual(notes[1].max_key, 12)      # 11 被左手佔著
        self.assertEqual(notes[2].max_key, 11)

    def test_it_stays_on_the_keyboard(self):
        notes = [make(0, 0, 60, 27, width=1), make(1, 300, 72, 27, width=1)]
        ML.lead_by_top(notes)
        self.assertLessEqual(notes[1].max_key, 27)


class ReportTests(unittest.TestCase):
    def test_breaks_come_back_in_time_order(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 10),
                 make(2, 600, 60, 20)]
        found = ML.breaks(notes)
        self.assertEqual([b.start for b in found], [300, 600, 600])

    def test_summary_counts_each_kind(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 67, 10),
                 make(2, 600, 60, 20)]
        got = ML.summary(ML.breaks(notes))
        self.assertEqual(got[ML.KIND_DIRECTION], 2)
        self.assertEqual(got[ML.KIND_REPEAT], 1)

    def test_rate_ignores_steps_without_a_pitch_move(self):
        # 三步，其中一步是同音高：分母只算 2 步。
        notes = [make(0, 0, 60, 10), make(1, 300, 60, 10),
                 make(2, 600, 64, 14), make(3, 900, 60, 10)]
        good, total = ML.rate(notes)
        self.assertEqual((good, total), (2, 2))

    def test_rate_counts_the_broken_steps_as_bad(self):
        notes = [make(0, 0, 60, 10), make(1, 300, 64, 10)]
        self.assertEqual(ML.rate(notes), (0, 1))

    def test_an_empty_chart_has_no_steps(self):
        self.assertEqual(ML.rate([]), (0, 0))
        self.assertEqual(ML.breaks([]), [])


if __name__ == '__main__':
    unittest.main()
