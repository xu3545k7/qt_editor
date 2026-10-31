# -*- coding: utf-8 -*-
"""一個小節裡該有幾條拍線。

使用者報的：「6/8 拍號中只有三個拍」。

病根是兩個數字被混成一個：
  **幾個四分音符**（＝小節的長度）6/8 是 3 個
  **幾拍**（＝幾條拍線）        6/8 是 6 條

`beat_data` 一筆代表的音值不固定（曲庫實測：四分、八分、附點二分都有），所以
「一小節幾筆」＝ 幾個四分音符 ÷ 一筆幾個四分音符，而**那個筆數必須等於小節佔
的單位數**——`_detect_beat_index_scale` 是拿 index 間距的中位數當一個單位，
兩者不等的話寫出來的格線自己讀回來就對不上。
"""

import unittest

from qt_editor.models import NoteModel


def quarter_grid(bpm=120.0, sec=30.0):
    """一筆拍點＝一個四分音符的譜（製譜器鋪出來的精確格線）。"""
    m = NoteModel.create_new('t', bpm, sec, 4)
    m.ensure_precise_beat_grid()
    return m


def beats_in_bar(m, idx):
    s, e = m.get_measure_time_range(idx)
    if s is None or e is None:
        return 0
    return sum(1 for _i, b in m.get_beat_entries() if s <= b < e)


class SetSignatureBeatCountTests(unittest.TestCase):
    """在編輯器裡設一個拍號，小節裡就該有「分子」條拍線。"""

    #: (分子, 分母, 一小節幾個四分音符)
    CASES = ((4, 4, 4.0), (3, 4, 3.0), (2, 4, 2.0), (5, 4, 5.0), (6, 4, 6.0),
             (6, 8, 3.0), (7, 8, 3.5), (9, 8, 4.5), (12, 8, 6.0), (3, 8, 1.5),
             (24, 16, 6.0))

    def test_beat_line_count_is_the_numerator(self):
        for num, den, _quarters in self.CASES:
            with self.subTest(sig='%d/%d' % (num, den)):
                m = quarter_grid()
                m.set_measure_time_signature(4, num, den, uniform=True)
                self.assertEqual(beats_in_bar(m, 4), num)

    def test_bar_length_still_follows_the_denominator(self):
        """拍線變密**不可以**把小節拉長。6/8 仍然是 3 個四分音符。"""
        for num, den, quarters in self.CASES:
            with self.subTest(sig='%d/%d' % (num, den)):
                m = quarter_grid()
                m.set_measure_time_signature(4, num, den, uniform=True)
                s, e = m.get_measure_time_range(4)
                self.assertAlmostEqual(e - s, quarters * 500.0, delta=3)

    def test_the_next_bar_starts_where_this_one_ends(self):
        """多寫幾筆拍點不能把接縫推開——index 要一筆一筆內插，不是步距乘 k。

        6/8 的步距是 500、24/16 是 250，取整之後乘 count 次會和下一小節的起點
        差上幾個 index，接縫那一小節就被拉長或壓短。
        """
        for num, den, _q in self.CASES:
            with self.subTest(sig='%d/%d' % (num, den)):
                m = quarter_grid()
                m.set_measure_time_signature(4, num, den, uniform=True)
                _s, e = m.get_measure_time_range(4)
                nxt_s, nxt_e = m.get_measure_time_range(5)
                self.assertEqual(nxt_s, e)
                self.assertAlmostEqual(nxt_e - nxt_s, 2000, delta=3)

    def test_bars_before_and_after_keep_their_length(self):
        for num, den, _q in self.CASES:
            with self.subTest(sig='%d/%d' % (num, den)):
                m = quarter_grid()
                m.set_measure_time_signature(4, num, den, uniform=True)
                for other in (0, 1, 2, 3, 5, 6):
                    s, e = m.get_measure_time_range(other)
                    self.assertAlmostEqual(e - s, 2000, delta=3,
                                           msg='第%d小節' % (other + 1))

    def test_cut_time_only_relabels(self):
        """4/4 → 2/2 長度沒變，所以除了標籤什麼都不動（既有決定）。

        小節裡仍然是 4 條四分音符拍線而不是 2 條二分音符——那比拍值**細**，
        每一拍都還有線，不是少了。見 `CutTimeScopeTests`。
        """
        m = quarter_grid()
        before = [m.get_measure_time_range(i) for i in range(8)]
        m.set_measure_time_signature(4, 2, 2, uniform=True)
        self.assertEqual([m.get_measure_time_range(i) for i in range(8)], before)
        self.assertEqual(beats_in_bar(m, 4), 4)


class NonQuarterGridUnitsTests(unittest.TestCase):
    """一筆拍點不是四分音符的譜：單位數要跟著換算。

    `Suspenseful_Third_Day`（6/8、150BPM）一筆是八分音符，一個 6/8 小節是
    6 筆＝1200ms。不換算的話算成 3 個單位＝3 筆，**每個小節被切成一半**
    （實測 215 個小節而不是 107 個），裡面只剩 3 條拍線——使用者報的症狀。
    """

    def eighth_grid(self):
        """全 6/8、一筆是八分音符的譜。手搭 beat_data，不經過模型的鋪設。"""
        m = NoteModel.create_new('t', 150.0, 30.0, 4)
        m.beats_per_bar = 6
        m.time_sig_denominator = 8
        m.time_sig_changes = [(0, 6, 8)]
        # 150BPM → 四分音符 400ms、八分 200ms；一小節 6 筆 = 1200ms
        m._write_beat_entries([(i * 1000, i * 200) for i in range(121)],
                              mark_precise=True)
        m.rebuild_display_cache()
        return m

    def test_one_entry_is_an_eighth_note(self):
        m = self.eighth_grid()
        self.assertAlmostEqual(m._entry_quarter_span(), 0.5)

    def test_a_six_eight_bar_is_six_entries(self):
        m = self.eighth_grid()
        self.assertAlmostEqual(m.bar_units_at_ms(0), 6.0)

    def test_bars_are_not_cut_in_half(self):
        m = self.eighth_grid()
        self.assertEqual(m.get_measure_time_range(0), (0, 1200))
        self.assertEqual(beats_in_bar(m, 0), 6)

    def test_the_bar_count_matches_the_music(self):
        m = self.eighth_grid()
        # 120 筆八分音符 = 20 個 6/8 小節
        self.assertEqual(m.count_measures(), 20)


class PreciseGridResolutionTests(unittest.TestCase):
    """鋪精確格線：解析度取全曲最細的拍值，而且要釘住。"""

    def legacy(self, sigs, bpm=144.0, bars=24):
        """一小節一筆的舊格線（`_legacy_*` 那條路）。"""
        m = NoteModel.create_new('t', bpm, 60.0, 4)
        num, den = sigs[0][1], sigs[0][2]
        m.beats_per_bar = num
        m.time_sig_denominator = den
        m.time_sig_changes = list(sigs)
        bar_ms = num * 4.0 / den * 60000.0 / bpm
        m._write_beat_entries(
            [(i, int(round(i * bar_ms))) for i in range(bars + 1)])
        m._epb_mode = None
        m.rebuild_display_cache()
        return m

    def test_six_eight_gets_six_lines_per_bar(self):
        m = self.legacy([(0, 6, 8)])
        bars_before = [m.get_measure_time_range(i) for i in range(6)]
        m.ensure_precise_beat_grid()
        m.rebuild_display_cache()
        self.assertEqual([m.get_measure_time_range(i) for i in range(6)],
                         bars_before, '升級格線不該動到小節的位置')
        for i in range(6):
            self.assertEqual(beats_in_bar(m, i), 6, '第%d小節' % (i + 1))

    def test_the_span_is_pinned_so_it_is_not_re_measured(self):
        """不釘住的話鋪完會重新量一次，量到的和鋪的時候用的不一樣就切錯。

        實測 Scherzo（全 6/8、一筆原本是附點二分）：不釘住時每隔幾十小節會
        多出一條拍線。
        """
        m = self.legacy([(0, 6, 8)])
        m.ensure_precise_beat_grid()
        self.assertAlmostEqual(
            float(m.json_meta[m.BEAT_ENTRY_QUARTERS_KEY]), 0.5)
        self.assertAlmostEqual(m._entry_quarter_span(), 0.5)

    def test_mixed_denominators_use_the_finest_beat(self):
        """4/4 和 9/8 混在一起：格線走八分音符，9/8 才排得出 9 條。

        於是 4/4 小節會有 8 條（每一拍都還有線，只是多了細分）。
        """
        m = self.legacy([(0, 4, 4), (8000, 9, 8), (12000, 4, 4)], bpm=120.0)
        m.ensure_precise_beat_grid()
        m.rebuild_display_cache()
        self.assertAlmostEqual(m._entry_quarter_span(), 0.5)
        for i in range(m.count_measures() - 1):
            s, _e = m.get_measure_time_range(i)
            num = m.get_beats_per_bar_at_ms(s)
            got = beats_in_bar(m, i)
            self.assertEqual(got % num, 0,
                             '第%d小節 %d 拍卻有 %d 條拍線' % (i + 1, num, got))
            self.assertGreaterEqual(got, num)

    def test_an_absurd_denominator_does_not_explode_the_grid(self):
        """24/16 多半是 MIDI 的產物。照最細拍值鋪會讓 4/4 變 16 筆，太密了。"""
        m = self.legacy([(0, 24, 16)], bpm=120.0)
        m.ensure_precise_beat_grid()
        for i in range(4):
            self.assertLessEqual(beats_in_bar(m, i),
                                 m._MAX_GRID_ENTRIES_PER_BAR)


class InsertKeepsTheSignatureTests(unittest.TestCase):
    """插進來的空白小節要和「被它們擠到後面」的那一小節同拍號。

    剛好落在插入點上的標記會被往後推，那一段空白就落回**前一個**標記的管轄
    範圍。felzione 第 1 小節是 MIDI 留下的 232ms 前導、宣告 1/8，在第 2 小節
    前插 4 個 6/8 小節時那 5584ms 全部被切成 1/8：小節數 94 → 118。
    """

    def chart(self):
        """第 1 小節是短前導（1/8），之後全是 6/8——felzione 的形狀。"""
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.beats_per_bar = 1
        m.time_sig_denominator = 8
        # 一筆＝八分音符（250ms @120BPM）：前導 1 筆，之後每小節 6 筆
        m.time_sig_changes = [(0, 1, 8), (250, 6, 8)]
        m._write_beat_entries([(i * 1000, i * 250) for i in range(121)],
                              mark_precise=True)
        m.rebuild_display_cache()
        return m

    def test_the_pickup_bar_does_not_swallow_the_inserted_ones(self):
        m = self.chart()
        before = [m.get_measure_time_range(i) for i in range(m.count_measures())]
        self.assertEqual(before[0], (0, 250), '第 1 小節是 1/8 的前導')
        self.assertEqual(before[1], (250, 1750), '第 2 小節是 6/8')
        m.insert_measure(1, count=4)
        m.rebuild_display_cache()
        self.assertEqual(m.count_measures(), len(before) + 4)
        for i in range(1, 6):
            s, e = m.get_measure_time_range(i)
            self.assertEqual(e - s, 1500, '插進來的第%d小節應該是 6/8' % (i + 1))

    def test_the_rest_of_the_chart_shifts_by_one_amount(self):
        m = self.chart()
        before = [m.get_measure_time_range(i) for i in range(m.count_measures())]
        m.insert_measure(1, count=4)
        m.rebuild_display_cache()
        shifts = {m.get_measure_time_range(i + 4)[0] - before[i][0]
                  for i in range(1, len(before))}
        self.assertEqual(shifts, {4 * 1500})


class DenominatorLookupTests(unittest.TestCase):
    """分母的查找：以前在五個地方各抄一份。"""

    def test_it_follows_the_change_at_that_time(self):
        m = quarter_grid()
        m.time_sig_changes = [(0, 4, 4), (5000, 6, 8), (9000, 2, 2)]
        self.assertEqual(m._denominator_at_ms(0), 4)
        self.assertEqual(m._denominator_at_ms(4999), 4)
        self.assertEqual(m._denominator_at_ms(5000), 8)
        self.assertEqual(m._denominator_at_ms(8999), 8)
        self.assertEqual(m._denominator_at_ms(9000), 2)

    def test_no_changes_falls_back_to_the_global_one(self):
        m = quarter_grid()
        m.time_sig_changes = []
        m.time_sig_denominator = 8
        self.assertEqual(m._denominator_at_ms(1234), 8)


class FillBeatIndexGapsTests(unittest.TestCase):
    """補回 `beat_data` 裡跳號的那幾筆。

    曲庫實測有 128 份檔案少了拍點：Suspenseful_Third_Day 的 index 從 143000
    跳到 145000（144000 不見了），那一小節就只有 2 條拍線而不是 3 條。V 少 10
    筆、felzione 少 7 筆。那不是哪個功能算錯，是檔案本身有洞。
    """

    def holed(self, drop=(3, 7)):
        """一筆＝一個四分音符的格線，挖掉幾筆。"""
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        rows = [(i * 1000, i * 500) for i in range(41)]
        m._write_beat_entries([r for i, r in enumerate(rows) if i not in drop],
                              mark_precise=True)
        m.rebuild_display_cache()
        return m

    def test_it_puts_the_missing_entries_back(self):
        m = self.holed()
        self.assertEqual(len(m.get_beat_entries()), 39)
        self.assertEqual(m.fill_beat_index_gaps(), 2)
        beats = m.get_beat_entries()
        self.assertEqual(len(beats), 41)
        self.assertEqual([i for i, _ms in beats], [i * 1000 for i in range(41)])
        self.assertEqual([ms for _i, ms in beats], [i * 500 for i in range(41)])

    def test_the_hole_was_costing_a_beat_line(self):
        m = self.holed(drop=(3,))
        self.assertEqual(beats_in_bar(m, 0), 3, '第 1 小節少一條拍線')
        m.fill_beat_index_gaps()
        m.rebuild_display_cache()
        self.assertEqual(beats_in_bar(m, 0), 4)

    def test_nothing_else_moves(self):
        m = self.holed()
        bars = [m.get_measure_time_range(i) for i in range(m.count_measures())]
        end = m.music_end_ms
        m.fill_beat_index_gaps()
        m.rebuild_display_cache()
        self.assertEqual([m.get_measure_time_range(i)
                          for i in range(m.count_measures())], bars)
        self.assertEqual(m.music_end_ms, end)

    def test_a_clean_grid_is_left_alone(self):
        m = quarter_grid()
        before = m.get_beat_entries()
        self.assertEqual(m.fill_beat_index_gaps(), 0)
        self.assertEqual(m.get_beat_entries(), before)

    def test_it_is_idempotent(self):
        m = self.holed()
        m.fill_beat_index_gaps()
        self.assertEqual(m.fill_beat_index_gaps(), 0)

    def test_a_jump_that_is_not_a_whole_number_of_steps_is_not_a_hole(self):
        """變速和譜尾的收尾標記本來就不落在格線上，不要亂補。

        每份譜的最後一筆拍點是收尾標記，落在曲末而不在格線上。
        """
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m._write_beat_entries([(0, 0), (1000, 500), (2000, 1000),
                               (2600, 1300),              # 不是刻度的整數倍
                               (3600, 1800)], mark_precise=True)
        m.rebuild_display_cache()
        self.assertEqual(m.fill_beat_index_gaps(), 0)

    def test_a_serial_numbered_grid_has_no_such_thing_as_a_hole(self):
        """index 只是 0,1,2… 的流水號時沒有「跳號」的概念，直接跳過。"""
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m._write_beat_entries([(i, i * 2000) for i in range(12)])
        m.rebuild_display_cache()
        self.assertEqual(m.fill_beat_index_gaps(), 0)


if __name__ == '__main__':
    unittest.main()
