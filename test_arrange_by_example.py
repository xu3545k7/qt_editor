# -*- coding: utf-8 -*-
"""照前面排好的小節，自動排後面相似的部分。

使用者的原話：「可以輸入目前的小節，讓他以這些小節前的音符當作參考，對後面的
小節進行逐一比對，自動排好後面相似的部分，匹配率太低的就去遷就匹配率高的部分
進行重新排。」

兩條路要分開釘住：

* **照抄**——重複的樂句，鍵道（和左右手）原樣搬過去。
* **遷就**——對不上的，照「參考段落＋剛照抄好的部分」量出來的音高→鍵道擺。

還有一條使用者後來補的限制：**不准拿 `hand` 當切分依據**（「因為分手友會錯，
要看資料本身」），所以同一個小節裡一半照抄一半重排是正常結果，而且分手欄位
錯的時候比對結果不該跟著壞。
"""

import unittest

from qt_editor import arrange_by_example as ABE
from qt_editor import melody_line as ML
from qt_editor.models import GNote, NoteModel, TOTAL_GAME_KEYS

BAR = 2000                      # 一小節 2000ms（120 BPM 的 4/4）


def make(idx, start, pitch, centre, width=3, hand=0, note_type=0, dur=200):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = hand
    n.velocity = 90
    half = (width - 1) // 2
    n.min_key, n.max_key = centre - half, centre - half + width - 1
    n.note_type = note_type
    return n


def chart(notes, bars=8):
    """一份有 `bars` 個小節、每小節 BAR 毫秒的譜。"""
    m = NoteModel.create_new('t', 120.0, float(bars * BAR) / 1000.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    return m


def phrase(base_idx, bar, pitches_and_lanes, hand=0, step=250, shift=0):
    """一個小節的樂句：[(音高, 鍵道中心), ...]，從小節開頭每 `step` 毫秒一顆。"""
    out = []
    for i, (pitch, lane) in enumerate(pitches_and_lanes):
        out.append(make(base_idx + i, bar * BAR + i * step,
                        pitch + shift, lane, hand=hand))
    return out


def centre_of(note):
    return (int(note.min_key) + int(note.max_key)) / 2.0


class TheReferenceIsEverythingBeforeTheGivenMeasureTests(unittest.TestCase):
    def test_the_given_measure_is_itself_rearranged(self):
        """「輸入目前的小節」= 我排到這裡，所以這一節本身還沒排好。"""
        notes = phrase(0, 0, [(60, 4), (62, 8), (64, 12)])
        notes += phrase(10, 1, [(60, 20), (62, 20), (64, 20)])
        m = chart(notes)
        p = ABE.plan(m, 2)
        self.assertEqual(p.reference_measures, [0])
        self.assertEqual([b.index for b in p.bars], [1])

    def test_measure_one_has_nothing_to_learn_from(self):
        m = chart(phrase(0, 0, [(60, 4)]))
        self.assertFalse(ABE.plan(m, 1).ok)
        self.assertIn('第 2 小節', ABE.plan(m, 1).reason)

    def test_a_chart_with_no_pitch_says_so_instead_of_doing_nothing(self):
        """沒有音高時比對和重排都動不了，要講出來而不是靜靜地不動。"""
        notes = phrase(0, 0, [(60, 4), (62, 8)]) + phrase(10, 1, [(60, 20)])
        for n in notes:
            n.pitch = None
        p = ABE.plan(chart(notes), 2)
        self.assertFalse(p.ok)
        self.assertIn('音高', p.reason)

    def test_past_the_end_is_refused(self):
        m = chart(phrase(0, 0, [(60, 4)]) + phrase(10, 1, [(60, 8)]), bars=8)
        p = ABE.plan(m, 99)
        self.assertFalse(p.ok)


class ARepeatedPhraseIsCopiedTests(unittest.TestCase):
    def repeated(self, shift=0, hand=0):
        """第 1 小節排好的樂句，第 2 小節原樣重複但鍵道是錯的。"""
        tune = [(60, 3), (64, 9), (67, 15), (72, 21)]
        ref = phrase(0, 0, tune)
        again = phrase(10, 1, [(p, 1) for p, _lane in tune], hand=hand,
                       shift=shift)
        return chart(ref + again), ref, again

    def test_the_lanes_come_across(self):
        m, ref, again = self.repeated()
        p = ABE.plan(m, 2)
        self.assertEqual([b.action for b in p.bars], [ABE.ACTION_COPY])
        self.assertEqual(p.bars[0].source, 0)
        ABE.apply(m, p)
        self.assertEqual([centre_of(n) for n in again],
                         [centre_of(n) for n in ref])

    def test_the_match_rate_is_reported(self):
        m, _ref, _again = self.repeated()
        p = ABE.plan(m, 2)
        self.assertAlmostEqual(p.bars[0].coverage, 1.0, places=6)

    def test_the_hand_comes_across_too(self):
        m, ref, again = self.repeated(hand=1)
        for n in ref:
            n.hand = 0
        ABE.apply(m, ABE.plan(m, 2), copy_hand=True)
        self.assertEqual([int(n.hand) for n in again], [0, 0, 0, 0])

    def test_the_hand_can_be_left_alone(self):
        m, ref, again = self.repeated(hand=1)
        ABE.apply(m, ABE.plan(m, 2), copy_hand=False)
        self.assertEqual([int(n.hand) for n in again], [1, 1, 1, 1])

    def test_nothing_else_is_touched(self):
        """只動鍵道與左右手：時間、音高、類型都不准動。"""
        m, _ref, again = self.repeated()
        before = [(int(n.start), int(n.end), int(n.pitch), int(n.note_type))
                  for n in again]
        ABE.apply(m, ABE.plan(m, 2))
        self.assertEqual(
            [(int(n.start), int(n.end), int(n.pitch), int(n.note_type))
             for n in again], before)

    def test_a_transposed_repeat_is_found(self):
        m, _ref, _again = self.repeated(shift=7)
        p = ABE.plan(m, 2)
        self.assertEqual(p.bars[0].action, ABE.ACTION_COPY)
        self.assertEqual(p.bars[0].transpose, 7)

    def test_a_transposed_repeat_moves_its_lanes_sideways(self):
        """移了調就不能原樣照抄鍵道——音高高了五度，鍵道還留在原處。

        實測 7 份手排譜：移調的段落不平移只有 19% 落在手排的位置，平移後 23%。
        """
        m, ref, again = self.repeated(shift=7)
        ABE.apply(m, ABE.plan(m, 2), shift_transposed=True)
        moved = [centre_of(n) for n in again]
        self.assertTrue(all(b > a for a, b in zip([centre_of(n) for n in ref],
                                                  moved)),
                        '往上移調的重複，鍵道要往右移')

    def test_the_sideways_move_can_be_switched_off(self):
        m, ref, again = self.repeated(shift=7)
        ABE.apply(m, ABE.plan(m, 2), shift_transposed=False)
        self.assertEqual([centre_of(n) for n in again],
                         [centre_of(n) for n in ref])


class HalfABarCanMatchTests(unittest.TestCase):
    """「自動排好後面**相似的部分**」——相似的是部分，不是整節。

    這也是不看 `hand` 的理由：使用者說「分手友會錯，要看資料本身」。一個小節
    裡旋律在重複、伴奏在變是常態，決定的單位必須是一顆一顆音符。
    """

    def mixed(self):
        tune = [(72, 20), (74, 23), (76, 26)]      # 重複的那一半
        bass = [(36, 2), (38, 5)]                  # 會變的那一半
        ref = phrase(0, 0, tune) + phrase(20, 0, bass, step=600)
        again = (phrase(30, 1, [(p, 1) for p, _l in tune])
                 + phrase(50, 1, [(48, 1), (55, 1)], step=600))
        return chart(ref + again), ref, again

    def test_the_repeating_half_is_copied_and_the_rest_is_not(self):
        m, ref, again = self.mixed()
        p = ABE.plan(m, 2)
        self.assertEqual(p.bars[0].action, ABE.ACTION_COPY)
        copied = {id(n) for _s, n in p.bars[0].good}
        self.assertEqual(copied, {id(n) for n in again[:3]},
                         '只有重複的那三顆算「相似的部分」')
        self.assertEqual({id(n) for n in p.bars[0].leftover},
                         {id(n) for n in again[3:]})

    def test_the_copied_half_lands_on_the_reference_lanes(self):
        m, ref, again = self.mixed()
        ABE.apply(m, ABE.plan(m, 2))
        self.assertEqual([centre_of(n) for n in again[:3]],
                         [centre_of(n) for n in ref[:3]])

    def test_a_wrong_hand_label_does_not_change_the_result(self):
        """分手欄位亂填也要得到同樣的鍵道——比對完全不看它。"""
        want = []
        for flip in (False, True):
            m, _ref, again = self.mixed()
            if flip:
                for i, n in enumerate(m.notes_tree):
                    n.hand = i % 2          # 亂填
            ABE.apply(m, ABE.plan(m, 2), copy_hand=False)
            want.append([centre_of(n) for n in again])
        self.assertEqual(want[0], want[1])


class WhatCannotBeMatchedConformsTests(unittest.TestCase):
    """「匹配率太低的就去遷就匹配率高的部分進行重新排」。"""

    def built(self, lane_for=None):
        """參考段落把音高 60/64/68 放在鍵道 4/12/20（每半音 2 格）。"""
        ref = (phrase(0, 0, [(60, 4), (64, 12), (68, 20)])
               + phrase(10, 1, [(60, 4), (64, 12), (68, 20)]))
        # 第 3 小節是完全不同的樂句，而且鍵道全擠在左邊
        odd = phrase(20, 2, [(62, 1), (66, 1), (70, 1)], step=700)
        return chart(ref + odd), odd

    def test_it_is_marked_for_remapping(self):
        m, _odd = self.built()
        p = ABE.plan(m, 3)
        self.assertEqual(p.bars[-1].action, ABE.ACTION_REMAP)
        self.assertLess(p.bars[-1].coverage, p.threshold)

    def test_the_pitches_land_where_the_reference_would_put_them(self):
        """62 在 60 和 64 之間，鍵道也該在 4 和 12 之間。"""
        m, odd = self.built()
        ABE.apply(m, ABE.plan(m, 3))
        got = [centre_of(n) for n in odd]
        self.assertTrue(4 < got[0] < 12, got)
        self.assertTrue(12 < got[1] < 20, got)
        self.assertTrue(got[2] > 20, got)
        self.assertEqual(got, sorted(got), '音高往上，鍵道也要往右')

    def test_it_can_be_switched_off(self):
        m, odd = self.built()
        before = [centre_of(n) for n in odd]
        p = ABE.plan(m, 3, remap_low=False)
        self.assertEqual(p.bars[-1].action, ABE.ACTION_KEEP)
        ABE.apply(m, p)
        self.assertEqual([centre_of(n) for n in odd], before)

    def test_the_model_learns_from_the_copied_bars_too(self):
        """順序：先照抄 → 再學 → 最後重排。學的樣本要含剛照抄好的那些。

        參考只有一顆音高 60；第 2 小節是它的重複（會被照抄到鍵道 4），第 3 小節
        是另一個音高。如果學習樣本沒含第 2 小節，這條線就只有一個樣本、學不出
        斜率，第 3 小節一顆都不會動。
        """
        ref = phrase(0, 0, [(60, 4)])
        same = phrase(10, 1, [(60, 25)])
        other = phrase(20, 2, [(72, 1)])
        m = chart(ref + same + other)
        p = ABE.plan(m, 2)
        self.assertEqual(p.bars[0].action, ABE.ACTION_COPY)
        ABE.apply(m, p)
        self.assertEqual(centre_of(same[0]), 4.0)
        self.assertNotEqual(centre_of(other[0]), centre_of(other[0]) * 0 + 1,
                            '第 3 小節應該被移走，不該留在鍵道 1')


class CollisionsAreOpenedUpTests(unittest.TestCase):
    def test_two_notes_on_the_same_lane_at_the_same_time_are_separated(self):
        a = make(0, 0, 60, 10)
        b = make(1, 0, 62, 10)
        ABE.spread_group([a, b])
        self.assertGreater(int(b.min_key), int(a.max_key))

    def test_a_chord_that_does_not_clash_is_left_alone(self):
        a = make(0, 0, 60, 4)
        b = make(1, 0, 62, 12)
        ABE.spread_group([a, b])
        self.assertEqual((int(a.min_key), int(b.min_key)), (3, 11))

    def test_the_stack_is_pulled_back_inside_the_keyboard(self):
        notes = [make(i, 0, 60 + i, TOTAL_GAME_KEYS - 2) for i in range(3)]
        ABE.spread_group(notes)
        self.assertLessEqual(max(int(n.max_key) for n in notes),
                             TOTAL_GAME_KEYS - 1)
        self.assertGreaterEqual(min(int(n.min_key) for n in notes), 0)

    def test_nothing_is_ever_pushed_off_the_keyboard(self):
        """整疊比鍵盤還寬時寧可重疊，也不要留下鍵道 28 以上的壞資料。"""
        notes = [make(i, 0, 60 + i, 20, width=3) for i in range(12)]
        ABE.spread_group(notes)
        self.assertLessEqual(max(int(n.max_key) for n in notes),
                             TOTAL_GAME_KEYS - 1)
        self.assertGreaterEqual(min(int(n.min_key) for n in notes), 0)

    def test_hand_is_not_consulted(self):
        """同一刻同一個鍵道上有兩顆就是打不出來，和它們屬於哪隻手無關。"""
        a = make(0, 0, 60, 10, hand=0)
        b = make(1, 0, 62, 10, hand=1)
        ABE.spread_group([a, b])
        self.assertGreater(int(b.min_key), int(a.max_key))

    def test_remapping_does_not_pile_onto_the_copied_notes(self):
        """重排的音符要避開同一刻剛照抄好的那些（它們不准動）。"""
        fixed = make(0, 0, 60, 10)
        moving = make(1, 0, 62, 2)
        model = ABE.LaneModel(line=ABE.PitchLine(exact={62: 10.0}, samples=5))
        ABE.remap_notes([moving], model, fixed=[fixed])
        self.assertEqual(centre_of(fixed), 10.0, '照抄好的不准被推走')
        self.assertGreater(int(moving.min_key), int(fixed.max_key))


class WhatIsNeverTouchedTests(unittest.TestCase):
    def test_a_trill_is_left_out(self):
        """顫音的鍵道還連著每一格子音符，要改得整顆一起搬。"""
        ref = phrase(0, 0, [(60, 4), (64, 12)])
        trill = make(10, BAR, 60, 1, note_type=64)
        tail = make(11, BAR + 250, 64, 1)
        m = chart(ref + [trill, tail])
        p = ABE.plan(m, 2)
        touched = {id(n) for b in p.bars for n in b.leftover}
        touched |= {id(n) for b in p.bars for _s, n in b.good}
        self.assertNotIn(id(trill), touched)
        self.assertIn(id(tail), touched)

    def test_a_hidden_note_is_left_out(self):
        ref = phrase(0, 0, [(60, 4), (64, 12)])
        buried = make(10, BAR, 60, 1)
        buried.hidden = True
        m = chart(ref + [buried])
        p = ABE.plan(m, 2)
        for b in p.bars:
            self.assertNotIn(id(buried), {id(n) for n in b.leftover})

    def test_the_reference_half_is_never_moved(self):
        tune = [(60, 3), (64, 9), (67, 15)]
        ref = phrase(0, 0, tune)
        again = phrase(10, 1, [(p, 1) for p, _l in tune])
        m = chart(ref + again)
        before = [centre_of(n) for n in ref]
        ABE.apply(m, ABE.plan(m, 2))
        self.assertEqual([centre_of(n) for n in ref], before)


class LaneModelTests(unittest.TestCase):
    def test_a_pitch_seen_before_goes_back_where_it_was(self):
        notes = [make(0, 0, 60, 7), make(1, 500, 60, 7), make(2, 1000, 64, 15)]
        fit = ABE.learn_lane_model(notes)
        self.assertEqual(fit.centre(notes[0]), 7.0)

    def test_the_median_wins_over_an_outlier(self):
        notes = [make(i, i * 100, 60, lane) for i, lane in enumerate((7, 7, 20))]
        self.assertEqual(ABE.learn_lane_model(notes).centre(notes[0]), 7.0)

    def test_an_unseen_pitch_follows_the_learnt_line(self):
        notes = [make(0, 0, 60, 4), make(1, 500, 72, 16)]
        fit = ABE.learn_lane_model(notes)
        probe = make(2, 1000, 66, 0)
        self.assertAlmostEqual(fit.centre(probe), 10.0, places=6)

    def test_the_slope_is_clamped_to_something_musical(self):
        """只有兩顆相鄰音高的樣本會迴歸出荒謬的斜率。"""
        notes = [make(0, 0, 60, 0), make(1, 500, 61, 27)]
        fit = ABE.learn_lane_model(notes)
        self.assertLessEqual(fit.line.slope, ABE.MAX_SLOPE)
        self.assertGreaterEqual(fit.line.slope, ABE.MIN_SLOPE)

    def test_the_hand_label_is_never_consulted(self):
        """分手可能是錯的，而實測分手版一毛錢也沒買到（見 LaneModel 的說明）。"""
        notes = [make(0, 0, 60, 4, hand=0), make(1, 500, 72, 16, hand=0)]
        fit = ABE.learn_lane_model(notes)
        for hand in (0, 1, 2):
            probe = make(2, 1000, 66, 0, hand=hand)
            self.assertAlmostEqual(fit.centre(probe), 10.0, places=6)



class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, notes=None, current=3):
        from qt_editor.arrange_by_example_dialog import ArrangeByExampleDialog
        notes = notes or (phrase(0, 0, [(60, 4), (64, 12)])
                          + phrase(10, 1, [(60, 1), (64, 1)])
                          + phrase(20, 2, [(60, 1), (64, 1)]))
        self.model = chart(notes)
        return ArrangeByExampleDialog(None, self.model, current)

    def test_it_starts_at_the_measure_you_are_on(self):
        self.assertEqual(self._dialog(current=3).from_measure(), 3)

    def test_measure_one_is_not_offerable(self):
        """第 1 小節前面沒有東西可以當參考，所以連選都不該選得到。"""
        self.assertEqual(self._dialog()._from.minimum(), 2)

    def test_the_summary_counts_what_will_happen(self):
        dlg = self._dialog(current=2)
        text = dlg._summary.text()
        self.assertIn('參考第 1~1 小節', text)
        self.assertIn('照抄', text)

    def test_it_hands_back_a_usable_plan(self):
        dlg = self._dialog(current=2)
        prepared = dlg.plan()
        self.assertTrue(prepared.ok)
        self.assertEqual(prepared.from_measure, 2)

    def test_a_chart_with_no_pitch_disables_the_ok_button(self):
        notes = phrase(0, 0, [(60, 4)]) + phrase(10, 1, [(60, 1)])
        for n in notes:
            n.pitch = None
        dlg = self._dialog(notes, current=2)
        self.assertFalse(dlg._ok.isEnabled())
        self.assertIn('音高', dlg._summary.text())

    def test_the_options_are_handed_over_by_name(self):
        """`apply()` 的關鍵字參數名要對得上，不然選項靜靜地沒生效。"""
        import inspect
        dlg = self._dialog()
        accepted = inspect.signature(ABE.apply).parameters
        for key in dlg.options():
            self.assertIn(key, accepted)


class MenuEntryTests(unittest.TestCase):
    def test_the_tools_menu_offers_it(self):
        import contextlib
        import io as _io

        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        try:
            labels = [label for _group, entries in win._tool_groups()
                      for label, _slot in entries]
            self.assertIn('照前面的小節排後面…', labels)
            self.assertTrue(callable(win.arrange_by_example_dialog))
        finally:
            win.view.model.dirty = False
            win.close()
        self.assertIsInstance(app, QApplication)


if __name__ == '__main__':
    unittest.main()


class KeepTheMelodyLineTests(unittest.TestCase):
    """重排完再照冠音走一次。

    使用者：「冠音和之前比，其他的部分在守住旋律的前提下根據那些冠音的變化
    重排」。原本的重排是照「這個音高上次放在哪」擺的，那張表沒有「上一顆在
    哪裡」的概念，所以同一句裡音高往上走、鍵道卻停在原地是常態（實測 20 份譜
    的後半段守方向只有 82.6%，使用者自己的譜是 97.8%）。
    """

    def chart(self):
        # 參考段落：60 和 67 都被放在第 10 格，但隔了 1 秒——不同一條線，
        # 所以參考段落自己沒有破線。
        ref = [make(0, 0, 60, 10), make(1, 1000, 67, 10),
               make(2, 2000, 60, 10), make(3, 3000, 67, 10)]
        # 後面：同一條線上 60 → 67，照那張表排會兩顆都落在第 10 格。
        tail = [make(10, 4 * BAR, 60, 2), make(11, 4 * BAR + 300, 67, 2)]
        return chart(ref + tail)

    def test_without_it_the_line_breaks(self):
        m = self.chart()
        ABE.apply(m, ABE.plan(m, 5), keep_melody=False)
        tail = [n for n in m.notes_tree if int(n.start) >= 4 * BAR]
        self.assertEqual([n.max_key for n in tail], [11, 11])   # 兩顆同一格
        self.assertEqual(len(ML.breaks(m.notes_tree, kinds=(ML.KIND_DIRECTION,))), 1)

    def test_with_it_the_top_moves_with_the_interval(self):
        m = self.chart()
        stats = ABE.apply(m, ABE.plan(m, 5), keep_melody=True)
        tail = [n for n in m.notes_tree if int(n.start) >= 4 * BAR]
        self.assertEqual(tail[0].max_key, 11)
        self.assertEqual(tail[1].max_key, 12)
        self.assertEqual(stats.melody_fixed, 1)
        self.assertEqual(ML.breaks(m.notes_tree, kinds=(ML.KIND_DIRECTION,)), [])

    def test_the_whole_group_follows_the_top(self):
        """其他的部分是**跟著冠音整組走**，不是各自重排。"""
        ref = [make(0, 0, 60, 10), make(1, 1000, 67, 10),
               make(2, 2000, 60, 10), make(3, 3000, 67, 10)]
        tail = [make(10, 4 * BAR, 60, 2),
                make(11, 4 * BAR + 300, 55, 2), make(12, 4 * BAR + 300, 67, 6)]
        def gap(keep):
            m = chart(ref + [make(n.idx, n.start, n.pitch,
                                  (int(n.min_key) + int(n.max_key)) // 2)
                             for n in tail])
            ABE.apply(m, ABE.plan(m, 5), keep_melody=keep)
            pair = [n for n in m.notes_tree if int(n.start) >= 4 * BAR + 300]
            return (int(pair[1].max_key) - int(pair[0].max_key),
                    int(pair[1].max_key))

        loose, loose_top = gap(False)
        led, led_top = gap(True)
        self.assertEqual(led, loose, '和絃內部的間距一格都不能變')
        self.assertNotEqual(led_top, loose_top, '冠音本身該被帶走')

    def test_the_same_pitch_goes_back_to_the_same_lane(self):
        """音高沒變就回到同一格——同音同格是順便守住的，不是另外補的。"""
        ref = [make(0, 0, 60, 10), make(1, 400, 60, 10),
               make(2, 2000, 60, 10), make(3, 2400, 60, 10)]
        tail = [make(10, 4 * BAR, 60, 4), make(11, 4 * BAR + 300, 60, 9)]
        m = chart(ref + tail)
        ABE.apply(m, ABE.plan(m, 5), keep_melody=True)
        got = [n.max_key for n in m.notes_tree if int(n.start) >= 4 * BAR]
        self.assertEqual(got[0], got[1])

    def test_it_never_moves_the_notes_it_copied(self):
        # 照抄過去的鍵道來自使用者自己排好的前半段，收尾不該改掉它。
        ref = phrase(0, 0, [(60, 10), (62, 10), (64, 10)])
        tail = phrase(10, 4, [(60, 20), (62, 20), (64, 20)])
        m = chart(ref + tail)
        prepared = ABE.plan(m, 5)
        self.assertTrue(prepared.copied, '這個樣本該被判成照抄')
        ABE.apply(m, prepared, keep_melody=True)
        got = [n.max_key for n in m.notes_tree if int(n.start) >= 4 * BAR]
        self.assertEqual(got, [11, 11, 11])
