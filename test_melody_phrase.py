# -*- coding: utf-8 -*-
"""樂句：同一句又來了——移了調，或是中間插了裝飾音。

使用者要的是「冠音以外，加上做位移往高或低音但同旋律，以及中間插裝飾音感測」。
兩件事是同一套比對的兩種結果，所以這裡釘住的是那套比對的分寸：

* 什麼叫「同一句」：音程序列一樣、節奏一樣，音高整個差一個固定的量。
* 什麼**不**算：固定音型（同一個音連敲好幾下）、兩段在時間上疊在一起、
  差一個半音的「幾乎一樣」。
* 裝飾音＝**比本體多出來的那幾顆**，不是猜哪顆音太短。
* 整句該平移幾格，以及什麼時候算「位移沒跟上」。
"""

import unittest

from qt_editor import melody_line as ML
from qt_editor import melody_phrase as MP
from qt_editor.models import GNote

RIGHT, LEFT = 0, 1


def make(idx, start, pitch, lane_hi, width=3, hand=RIGHT, dur=120):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = hand
    n.max_key = lane_hi
    n.min_key = max(0, lane_hi - width + 1)
    return n


def phrase(at, pitches, lanes, step=300, hand=RIGHT, start_idx=0):
    """一句：`pitches` 和 `lanes` 一一對應，每顆隔 `step` 毫秒。"""
    return [make(start_idx + k, at + k * step, p, lane, hand=hand)
            for k, (p, lane) in enumerate(zip(pitches, lanes))]


#: 一句有起伏的旋律（五顆、四個音程，裡面有三個不是 0）
TUNE = (60, 64, 62, 67, 65)
LANES = (10, 13, 12, 16, 15)


class WhatCountsAsTheSamePhraseTests(unittest.TestCase):
    def test_a_phrase_moved_up_is_the_same_phrase(self):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + 5 for p in TUNE],
                          [l + 2 for l in LANES], start_idx=10))
        found = MP.echoes(notes)
        self.assertEqual(len(found), 1)
        echo = found[0]
        self.assertEqual(echo.kind, MP.ECHO_SHIFT)
        self.assertEqual(echo.offset, 5)
        self.assertEqual(echo.length, len(TUNE))
        self.assertEqual([n.idx for n in echo.source], [0, 1, 2, 3, 4])
        self.assertEqual([n.idx for n in echo.repeat], [10, 11, 12, 13, 14])

    def test_the_same_pitches_again_are_a_plain_repeat(self):
        notes = phrase(0, TUNE, LANES) + phrase(3000, TUNE, LANES, start_idx=10)
        found = MP.echoes(notes)
        self.assertEqual([e.kind for e in found], [MP.ECHO_SAME])
        self.assertEqual(found[0].offset, 0)

    def test_one_semitone_off_is_another_tune_not_this_one(self):
        """「幾乎一樣」不算：移調是**整句差同一個量**，差一個半音就是別的音。"""
        bent = list(TUNE)
        bent[2] += 1
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + 5 for p in bent], LANES, start_idx=10))
        self.assertEqual(MP.echoes(notes), [])

    def test_a_different_rhythm_is_not_the_same_phrase(self):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + 5 for p in TUNE], LANES,
                          step=700, start_idx=10))
        self.assertEqual(MP.echoes(notes), [])

    def test_a_held_note_repeated_is_not_a_melody(self):
        """固定音型不算旋律：這個曲庫裡伴奏滿地都是同一個音連敲六下。"""
        flat = (60,) * 6
        notes = (phrase(0, flat, (10,) * 6)
                 + phrase(3000, (67,) * 6, (14,) * 6, start_idx=10))
        self.assertEqual(MP.echoes(notes), [])

    def test_too_far_apart_is_not_an_echo_any_more(self):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(MP.ECHO_GAP_MS + 5000, [p + 5 for p in TUNE],
                          LANES, start_idx=10))
        self.assertEqual(MP.echoes(notes), [])

    def test_the_two_halves_never_overlap_in_time(self):
        """週期性的音型會自己吃自己：本體的尾巴不准蓋到回聲的頭。"""
        tune = TUNE * 4
        lanes = LANES * 4
        notes = phrase(0, tune, lanes, step=200)
        for echo in MP.echoes(notes):
            self.assertLess(max(int(n.start) for n in echo.source),
                            min(int(n.start) for n in echo.notes))

    def test_each_hand_has_its_own_phrases(self):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + 5 for p in TUNE], LANES, start_idx=10)
                 + phrase(0, [p - 24 for p in TUNE], (4, 6, 5, 8, 7),
                          hand=LEFT, start_idx=20))
        found = MP.echoes(notes)
        self.assertEqual([e.hand for e in found], [RIGHT])


#: 裝飾音的測試要用長一點的句子：錨點是「連續 3 個乾淨的音程」，五顆的句子被
#: 中間一顆裝飾音切開之後兩邊都湊不滿 3 個，整句就認不出來了（見模組說明）。
#: 這一串自己**不含**移調的重複（音程序列裡沒有任何三個連在一起的重覆），
#: 不然找到的第一處回聲會是句子自己的後半對前半。
LONG = (60, 64, 62, 67, 65, 71, 69, 74)
LONG_LANES = (10, 13, 12, 16, 15, 19, 18, 22)


class OrnamentsTests(unittest.TestCase):
    """「中間插裝飾音」＝回聲比本體多出來的那幾顆。"""

    def plain_then_decorated(self, extra=1):
        notes = (phrase(0, LONG, LONG_LANES)
                 + phrase(4000, LONG, LONG_LANES, start_idx=10))
        for k in range(extra):
            # 插在第 2 顆和第 3 顆的中間（本體那裡沒有東西），後面還留著
            # 五個乾淨的音程給錨點用
            notes.append(make(50 + k, 4000 + 300 + 100 + k * 60, 63, 12))
        return notes

    def test_the_extra_notes_are_the_ornament(self):
        found = MP.echoes(self.plain_then_decorated())
        self.assertEqual(len(found), 1)
        echo = found[0]
        self.assertEqual([n.idx for n in echo.ornaments], [50])
        self.assertEqual(echo.length, len(LONG))     # 結構音一顆都沒少

    def test_two_in_a_row_are_still_ornaments(self):
        found = MP.echoes(self.plain_then_decorated(2))
        self.assertEqual(len(found), 1)
        self.assertEqual([n.idx for n in found[0].ornaments], [50, 51])

    def test_too_many_in_a_row_breaks_the_phrase_instead(self):
        """塞到接不回去就不是裝飾了，那是另一句。"""
        notes = self.plain_then_decorated(MP.MAX_INSERT + 2)
        for echo in MP.echoes(notes):
            self.assertLessEqual(len(echo.ornaments), MP.MAX_INSERT)

    def test_ornaments_lists_them_all_in_time_order(self):
        found = MP.echoes(self.plain_then_decorated(2))
        got = MP.ornaments(found)
        self.assertEqual([n.idx for n in got], [50, 51])

    def test_a_missing_note_is_not_an_ornament(self):
        """少了一顆是 `missing`，和多出來的分開記——要改的地方不一樣。"""
        notes = phrase(0, LONG, LONG_LANES)
        notes.append(make(5, 400, 63, 12))          # 本體多一顆
        notes += phrase(4000, LONG, LONG_LANES, start_idx=10)
        found = MP.echoes(notes)
        self.assertEqual(len(found), 1)
        self.assertEqual([n.idx for n in found[0].missing], [5])
        self.assertEqual(found[0].ornaments, [])


class HowFarTheWholePhraseShouldMoveTests(unittest.TestCase):
    """整句移了調，鍵道就該跟著移——移多少，差多少才算沒跟上。"""

    def echo_with(self, offset, lane_shift):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + offset for p in TUNE],
                          [l + lane_shift for l in LANES], start_idx=10))
        found = MP.echoes(notes)
        self.assertEqual(len(found), 1)
        return found[0]

    def test_it_measures_what_the_phrase_actually_did(self):
        echo = self.echo_with(12, 4)
        self.assertEqual(echo.shift, 4.0)

    def test_the_step_table_is_used_as_is_for_a_whole_phrase(self):
        """步伐表直接拿來量一整句，不打折（`PHRASE_STEP_FACTOR` 的註解有實測）。"""
        echo = self.echo_with(12, 5)
        self.assertAlmostEqual(
            echo.want,
            ML.step_for(None, 12) * MP.PHRASE_STEP_FACTOR, places=6)
        self.assertLess(abs(echo.off_by), 1.0)
        self.assertFalse(echo.bad)

    def test_staying_put_while_the_tune_jumps_an_octave_is_bad(self):
        echo = self.echo_with(12, 0)
        self.assertTrue(echo.bad)
        self.assertGreater(echo.suggest, 0)          # 該往右移

    def test_staying_put_for_a_semitone_is_fine(self):
        """移一兩個半音時該平移不到一格，整句留在原地是對的。"""
        echo = self.echo_with(1, 0)
        self.assertFalse(echo.bad)

    def test_going_the_wrong_way_is_bad(self):
        echo = self.echo_with(12, -4)
        self.assertTrue(echo.bad)

    def test_moving_much_too_far_is_bad_too(self):
        echo = self.echo_with(12, 4 + int(MP.SHIFT_TOLERANCE) + 2)
        self.assertTrue(echo.bad)
        self.assertLess(echo.suggest, 0)             # 該收回來

    def test_a_plain_repeat_is_never_judged_here(self):
        """原樣重複該不該回到同一格是 `melody_line.KIND_REPEAT` 的事。"""
        notes = phrase(0, TUNE, LANES) + phrase(
            3000, TUNE, [l + 6 for l in LANES], start_idx=10)
        echo = MP.echoes(notes)[0]
        self.assertEqual(echo.kind, MP.ECHO_SAME)
        self.assertFalse(echo.bad)

    def test_the_learned_curve_wins_over_the_table(self):
        notes = (phrase(0, TUNE, LANES)
                 + phrase(3000, [p + 12 for p in TUNE],
                          [l + 4 for l in LANES], start_idx=10))
        echo = MP.echoes(notes, curve={12: 10.0})[0]
        self.assertAlmostEqual(echo.want, 10.0 * MP.PHRASE_STEP_FACTOR,
                               places=6)
        self.assertTrue(echo.bad)                    # 照這份譜的習慣是差太多


class WhatTheCallerSeesTests(unittest.TestCase):
    def setUp(self):
        self.notes = (phrase(0, TUNE, LANES)
                      + phrase(3000, [p + 7 for p in TUNE],
                               [l + 3 for l in LANES], start_idx=10))
        # 裝飾音插在最後兩顆中間：前面三個音程還是乾淨的，錨點抓得到
        self.notes.append(make(50, 4050, 73, 18))
        self.found = MP.echoes(self.notes)

    def test_the_echo_knows_its_own_span(self):
        echo = self.found[0]
        self.assertEqual(echo.source_start, 0)
        self.assertEqual(echo.start, 3000)
        self.assertEqual(echo.end, 3000 + 4 * 300)
        # `notes` 要含裝飾音（選取時整句一起選）
        self.assertIn(50, [n.idx for n in echo.notes])

    def test_the_label_says_how_far_it_moved(self):
        self.assertEqual(self.found[0].label, '同句 +7 半音')

    def test_summary_counts_each_kind(self):
        got = MP.summary(self.found)
        self.assertEqual(got[MP.ECHO_SHIFT], 1)
        self.assertEqual(got[MP.ECHO_SAME], 0)
        self.assertEqual(got['ornament'], 1)
        self.assertEqual(got['ornament_notes'], 1)

    def test_kinds_can_be_narrowed(self):
        self.assertEqual(MP.echoes(self.notes, kinds=(MP.ECHO_SAME,)), [])

    def test_hidden_notes_and_notes_without_pitch_stay_out(self):
        """和 `melody_line.usable` 同一條規則：隱藏音符不佔按鍵。"""
        notes = list(self.notes)
        ghost = make(60, 3100, 90, 20)
        ghost.hidden = True
        notes.append(ghost)
        for echo in MP.echoes(notes):
            self.assertNotIn(60, [n.idx for n in echo.notes])

    def test_it_survives_a_chart_with_almost_no_notes(self):
        self.assertEqual(MP.echoes([]), [])
        self.assertEqual(MP.echoes([make(0, 0, 60, 10)]), [])


class LayingTheEchoOutLikeItsSourceTests(unittest.TestCase):
    """照本體排：使用者說「套得不像我排的沒問題，我排的不一樣反而算是失誤，
    除非沒空間」。所以這裡釘住的是「照抄」和那個唯一的例外。
    """

    def chart(self, echo_lanes, offset=12):
        return (phrase(0, TUNE, LANES)
                + phrase(3000, [p + offset for p in TUNE], echo_lanes,
                         start_idx=10))

    def one(self, notes):
        found = MP.echoes(notes)
        self.assertEqual(len(found), 1)
        return found

    def test_it_copies_the_shape_of_the_source(self):
        notes = self.chart([14] * 5)                 # 整句排成一條平的
        found = self.one(notes)
        self.assertTrue(found[0].bad)                # 位移也沒跟上
        base = int(round(found[0].want))             # 判破了就用「該移的量」
        got = MP.apply_shape(notes, found)
        self.assertEqual([int(n.max_key) for n in found[0].repeat],
                         [l + base for l in LANES])  # 本體的形狀 ＋ 整句位移
        self.assertEqual(got.exact, 5)
        self.assertEqual(got.phrases, 1)
        self.assertEqual(got.moved, 5)

    def test_a_phrase_that_already_matches_is_left_alone(self):
        notes = self.chart([l + 4 for l in LANES])
        got = MP.apply_shape(notes, self.one(notes))
        self.assertEqual(got.moved, 0)
        self.assertEqual(got.exact, 5)
        self.assertEqual(got.phrases, 0)             # 沒動就不算動過一句

    def test_it_keeps_his_own_displacement_when_it_is_within_tolerance(self):
        """位移在容許內就是他的決定，這個動作只修形狀，不把整句搬走。"""
        lanes = [l + 4 for l in LANES]
        lanes[2] += 3                                # 只有一顆排歪
        notes = self.chart(lanes)
        found = self.one(notes)
        self.assertFalse(found[0].bad)               # 位移在容許內
        MP.apply_shape(notes, found)
        self.assertEqual([int(n.max_key) for n in found[0].repeat],
                         [l + 4 for l in LANES])     # 用他的 4 格，不是「該移」的量

    def test_the_whole_stack_follows_its_top(self):
        """和絃內部一格都不動——和排譜器、`lead_by_top` 同一條線。"""
        notes = self.chart([14] * 5)
        inner = make(98, 3300, 55, 11)               # 同一刻、同一手、較低的那顆
        notes.append(inner)
        found = self.one(notes)
        base = int(round(found[0].want))
        MP.apply_shape(notes, found)
        moved = LANES[1] + base - 14                 # 這一組走了幾格
        self.assertEqual(int(found[0].repeat[1].max_key), LANES[1] + base)
        self.assertEqual((int(inner.min_key), int(inner.max_key)),
                         (9 + moved, 11 + moved))

    def test_an_ornament_follows_its_nearest_neighbour(self):
        """裝飾音沒有本體可以抄，跟著時間上最近的那一顆配對音符走一樣的量。"""
        notes = (phrase(0, LONG, LONG_LANES)
                 + phrase(4000, [p + 12 for p in LONG], [14] * len(LONG),
                          start_idx=10))
        notes.append(make(50, 4400, 75, 14))         # 第 2、3 顆中間
        found = self.one(notes)
        self.assertEqual([n.idx for n in found[0].ornaments], [50])
        base = int(round(found[0].want))
        got = MP.apply_shape(notes, found)
        self.assertEqual(got.ornaments, 1)
        # 時間上最近的配對音符是 4300 那一顆：它走了幾格，裝飾音就走幾格
        self.assertEqual(int(notes[-1].max_key),
                         14 + (LONG_LANES[1] + base - 14))

    # ── 唯一的例外：沒空間 ────────────────────────────────────────

    def test_there_is_no_room_past_the_edge(self):
        notes = (phrase(0, TUNE, (20, 23, 22, 27, 26))
                 + phrase(3000, [p + 12 for p in TUNE], (20,) * 5,
                          start_idx=10))
        found = self.one(notes)
        got = MP.apply_shape(notes, found)
        self.assertEqual(got.blocked, 2)             # 27+4 和 26+4 都出界
        self.assertEqual([int(n.max_key) for n in found[0].repeat][3:],
                         [20, 20])                   # 原樣留著

    def test_it_squeezes_past_someone_else(self):
        notes = self.chart([14] * 5)
        blocker = make(99, 3300, 50, 17, width=1, hand=LEFT)
        notes.append(blocker)                        # 目標格被另一手佔住
        found = self.one(notes)
        base = int(round(found[0].want))
        got = MP.apply_shape(notes, found)
        self.assertEqual(got.nudged, 1)
        self.assertNotEqual(int(found[0].repeat[1].max_key), LANES[1] + base)
        self.assertLessEqual(                        # 讓開，但沒讓超過上限
            abs(int(found[0].repeat[1].max_key) - (LANES[1] + base)),
            MP.MAX_ADJUST)
        self.assertEqual(int(blocker.max_key), 17)   # 佔位的那顆不動

    def test_letting_it_squeeze_can_be_turned_off(self):
        notes = self.chart([14] * 5)
        notes.append(make(99, 3300, 50, 17, width=1, hand=LEFT))
        got = MP.apply_shape(notes, self.one(notes), max_adjust=0)
        self.assertEqual((got.nudged, got.blocked), (0, 1))

    def test_a_trill_never_moves(self):
        """顫音的鍵道還連著它每一格子音符，要搬得整顆一起搬。"""
        notes = self.chart([14] * 5)
        found = self.one(notes)
        found[0].repeat[1].note_type = 0x40
        got = MP.apply_shape(notes, found)
        self.assertEqual(got.blocked, 1)
        self.assertEqual(int(found[0].repeat[1].max_key), 14)

    def test_nothing_to_do(self):
        got = MP.apply_shape(self.chart([14] * 5), [])
        self.assertEqual((got.moved, got.phrases, got.groups), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
