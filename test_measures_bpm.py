import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.note_type = 0
    n.min_key = 4
    n.max_key = 4
    return n


class MeasuresBpmTests(unittest.TestCase):
    """多小節重設 BPM：逐小節套用時 beat_data 格式判斷必須保持穩定。

    entries_per_bar 以前每次都從「前 8 個 entry 間距 vs 全域 BPM」重新推斷，
    改完前幾小節後會從 per-bar(1) 翻成 per-beat(4)，之後每個 measure_idx 都抓
    到錯的 entry，整段譜面跑掉。
    """

    def _model(self, bpm=120.0, sec=40.0):
        m = NoteModel.create_new('t', bpm, sec, 4)
        # 每小節第一拍放一顆音符
        m.notes_tree = [make_note(2000 * i, 2000 * i + 100, i) for i in range(20)]
        m.rebuild_display_cache()
        return m

    def _apply(self, m, first, last, bpm):
        for mi in range(first, last + 1):
            m.set_measure_bpm(mi, bpm, uniform=False, adjust_notes=True)

    def test_speed_up_range_from_first_measure(self):
        m = self._model()
        self._apply(m, 0, 7, 480.0)
        self.assertEqual([m.get_measure_bpm(i) for i in range(10)],
                         [480.0] * 8 + [120.0, 120.0])
        self.assertEqual(m.entries_per_bar, 1)

    def test_slow_down_range(self):
        m = self._model()
        self._apply(m, 3, 9, 40.0)
        self.assertEqual([m.get_measure_bpm(i) for i in range(12)],
                         [120.0] * 3 + [40.0] * 7 + [120.0, 120.0])

    def test_notes_stay_on_their_measure_start(self):
        m = self._model()
        self._apply(m, 2, 7, 240.0)
        for i in range(12):
            start_ms, end_ms = m.get_measure_time_range(i)
            self.assertIsNotNone(start_ms, f'measure {i} 沒有範圍')
            inside = [n.start for n in m.notes_tree if start_ms <= n.start < end_ms]
            self.assertEqual(inside, [start_ms], f'measure {i} 音符跑掉了')

    def test_measure_count_unchanged(self):
        m = self._model()
        before = m.count_measures()
        self._apply(m, 0, 7, 480.0)
        self.assertEqual(m.count_measures(), before)

    def test_precise_beat_grid_chart(self):
        m = self._model()
        m.ensure_precise_beat_grid()
        self.assertEqual(m.entries_per_bar, 4)
        self._apply(m, 2, 7, 240.0)
        self.assertEqual([m.get_measure_bpm(i) for i in range(10)],
                         [120.0, 120.0] + [240.0] * 6 + [120.0, 120.0])

    def test_per_beat_chart_detected_and_edited(self):
        """原始遊戲檔格式：一個 entry = 一拍。"""
        m = self._model()
        m._write_beat_entries([(i, i * 500) for i in range(81)])
        m._epb_mode = None
        m.json_meta.pop('editor_beat_entry_mode', None)
        m.json_meta.pop('editor_precise_beat_grid', None)
        self.assertEqual(m.entries_per_bar, 4)
        self._apply(m, 2, 7, 240.0)
        self.assertEqual([m.get_measure_bpm(i) for i in range(10)],
                         [120.0, 120.0] + [240.0] * 6 + [120.0, 120.0])

    def test_mode_survives_reload_of_mixed_bpm_chart(self):
        """存檔後重開：譜面已經有混合 BPM，也不該把 per-bar 誤判成 per-beat。"""
        m = self._model()
        self._apply(m, 0, 7, 480.0)
        entries = list(m.get_beat_entries())
        mode = m.json_meta.get('editor_beat_entry_mode')
        self.assertEqual(mode, 'bar')

        reopened = NoteModel.create_new('t', 120.0, 40.0, 4)
        reopened._write_beat_entries([(i, ms) for i, ms in entries])
        reopened.json_meta['editor_beat_entry_mode'] = mode
        reopened._epb_mode = None
        self.assertEqual(reopened.entries_per_bar, 1)
        self.assertEqual(reopened.count_measures(), m.count_measures())

    def test_with_time_signature_change(self):
        m = self._model()
        for mi in range(2, 6):
            m.set_measure_bpm(mi, 240.0, uniform=False, adjust_notes=True)
            m.set_measure_time_signature(mi, 3, 4, uniform=True, time_uniform=False)
        self.assertEqual([m.get_measure_bpm(i) for i in range(8)],
                         [120.0, 120.0] + [240.0] * 4 + [120.0, 120.0])
        # 3/4 @ 240bpm -> 750ms 一小節
        for mi in range(2, 6):
            s, e = m.get_measure_time_range(mi)
            self.assertEqual(e - s, 750)


if __name__ == '__main__':
    unittest.main()


class BarUnitsDenominatorTests(unittest.TestCase):
    """一小節佔幾個「拍單位」要看分母，不是只看分子。

    單位是**一筆 beat_data**。這裡的譜一筆是一個四分音符，所以 2/2 的分子是 2
    但一小節仍然是 4 筆；6/8 的分子是 6，一小節只有 3 筆。拿 numerator 當單位數
    的話，把一小節改成 2/2 會讓它只佔一半——那一小節被切成兩半、後面每一小節
    都往前擠一格，使用者看到的就是「改一個拍號結果後面全部小節都變了」。

    「一筆不是四分音符」的譜見 `NonQuarterGridUnitsTests`。
    """

    def model(self):
        m = NoteModel.create_new('t', 120.0, 40.0, 4)
        # 一定要先鋪精確格線。`create_new` 給的是**一小節一筆**的格線，那種譜
        # 一小節就是 1 個單位（而且小節切法走 legacy 那條路，根本不問這個
        # 函式）。不鋪的話這裡量到的是「一筆 = 一個小節」，答案全部變 1.0。
        m.ensure_precise_beat_grid()
        return m

    def test_common_time_is_four_units(self):
        m = self.model()
        self.assertAlmostEqual(m.bar_units_at_ms(0), 4.0)

    def test_cut_time_is_also_four_units(self):
        m = self.model()
        m.time_sig_changes = [(0, 2, 2)]
        m.time_sig_denominator = 2
        self.assertAlmostEqual(m.bar_units_at_ms(0), 4.0)

    def test_three_four_is_three_units(self):
        m = self.model()
        m.time_sig_changes = [(0, 3, 4)]
        self.assertAlmostEqual(m.bar_units_at_ms(0), 3.0)

    def test_six_eight_is_three_units(self):
        m = self.model()
        m.time_sig_changes = [(0, 6, 8)]
        m.time_sig_denominator = 8
        self.assertAlmostEqual(m.bar_units_at_ms(0), 3.0)

    def test_seven_eight_is_three_and_a_half(self):
        m = self.model()
        m.time_sig_changes = [(0, 7, 8)]
        m.time_sig_denominator = 8
        self.assertAlmostEqual(m.bar_units_at_ms(0), 3.5)

    def test_the_denominator_follows_the_change_at_that_time(self):
        m = self.model()
        m.time_sig_changes = [(0, 4, 4), (5000, 6, 8)]
        self.assertAlmostEqual(m.bar_units_at_ms(0), 4.0)
        self.assertAlmostEqual(m.bar_units_at_ms(6000), 3.0)

    def test_no_changes_falls_back_to_the_global_signature(self):
        m = self.model()
        m.time_sig_changes = []
        m.beats_per_bar = 3
        m.time_sig_denominator = 4
        self.assertAlmostEqual(m.bar_units_at_ms(1234), 3.0)


class CutTimeScopeTests(unittest.TestCase):
    """4/4 改成 2/2：兩者一小節等長，所以除了標籤以外什麼都不該動。"""

    def chart(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        return m

    def test_the_measure_count_does_not_change(self):
        m = self.chart()
        before = m.count_measures()
        m.set_measure_time_signature(3, 2, 2)
        self.assertEqual(m.count_measures(), before,
                         '2/2 和 4/4 一小節等長，小節數不該變')

    def test_every_measure_keeps_its_timing(self):
        m = self.chart()
        before = [m.get_measure_time_range(i) for i in range(10)]
        m.set_measure_time_signature(3, 2, 2)
        after = [m.get_measure_time_range(i) for i in range(10)]
        self.assertEqual(after, before, '沒有一個小節的時間該被動到')

    def test_only_the_edited_measure_gets_the_new_signature(self):
        m = self.chart()
        m.set_measure_time_signature(3, 2, 2)
        sig = {}
        for i in range(8):
            start, _e = m.get_measure_time_range(i)
            num = m.get_beats_per_bar_at_ms(start or 0)
            den = m.time_sig_denominator
            for cms, _cn, cd in m.time_sig_changes:
                if cms <= (start or 0):
                    den = cd
                else:
                    break
            sig[i] = (num, den)
        self.assertEqual(sig[3], (2, 2))
        for i in (0, 1, 2, 4, 5, 6, 7):
            self.assertEqual(sig[i], (4, 4), '第 %d 小節不該被改' % i)

    def test_two_four_really_does_halve_the_measure(self):
        """會真的改變小節長度的拍號要算對。

        以前這裡拿到 0.25（應為 0.5）：`set_measure_time_signature` 在重寫拍點
        **之前**就把新的 `time_sig_changes` 寫進去了，於是後面問到的
        `_measure_entry_slice` / `_measure_unit_bounds` 已經是新拍號——「舊跨度」
        變成等於新跨度、切片也只涵蓋新長度，尾巴那段被當成小節內容重寫，
        比例等於被套了兩次。改成先把舊值記下來再動。
        """
        m = self.chart()
        before = m.get_measure_time_range(3)
        m.set_measure_time_signature(3, 2, 4)
        after = m.get_measure_time_range(3)
        self.assertAlmostEqual((after[1] - after[0]) / (before[1] - before[0]),
                               0.5, delta=0.02)


class TimeSignatureDurationTests(unittest.TestCase):
    """改單一小節的拍號：長度要照 分子×4÷分母 算，而且只影響那一小節。"""

    def chart(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        return m

    def expected_ratio(self, num, den):
        return (num * 4.0 / den) / 4.0          # 相對於 4/4

    def test_every_signature_gets_the_right_duration(self):
        for num, den in ((4, 4), (2, 2), (3, 4), (6, 8), (2, 4), (7, 8)):
            m = self.chart()
            before = m.get_measure_time_range(3)
            m.set_measure_time_signature(3, num, den)
            after = m.get_measure_time_range(3)
            got = (after[1] - after[0]) / (before[1] - before[0])
            self.assertAlmostEqual(got, self.expected_ratio(num, den), delta=0.02,
                                   msg='%d/%d' % (num, den))

    def test_the_beat_entries_stay_sorted_and_unique(self):
        """比例套兩次的症狀是寫出重複 ms、index 不遞增的拍點清單。"""
        for num, den in ((2, 2), (3, 4), (6, 8), (2, 4), (7, 8)):
            m = self.chart()
            m.set_measure_time_signature(3, num, den)
            beats = m.get_beat_entries()
            idx = [i for i, _ms in beats]
            mss = [ms for _i, ms in beats]
            self.assertEqual(idx, sorted(idx), '%d/%d index 要遞增' % (num, den))
            self.assertEqual(len(set(mss)), len(mss),
                             '%d/%d 不該有重複的 ms' % (num, den))
            self.assertEqual(mss, sorted(mss), '%d/%d ms 要遞增' % (num, den))

    def test_earlier_measures_never_move(self):
        for num, den in ((2, 2), (3, 4), (6, 8), (2, 4), (7, 8)):
            m = self.chart()
            before = [m.get_measure_time_range(i) for i in range(3)]
            m.set_measure_time_signature(3, num, den)
            after = [m.get_measure_time_range(i) for i in range(3)]
            self.assertEqual(after, before, '%d/%d 動到前面的小節了' % (num, den))

    def test_later_measures_keep_their_length(self):
        for num, den in ((2, 2), (3, 4), (6, 8), (2, 4), (7, 8)):
            m = self.chart()
            before = [m.get_measure_time_range(i) for i in range(4, 8)]
            lens_before = [b[1] - b[0] for b in before]
            m.set_measure_time_signature(3, num, den)
            after = [m.get_measure_time_range(i) for i in range(4, 8)]
            lens_after = [a[1] - a[0] for a in after]
            self.assertEqual(lens_after, lens_before,
                             '%d/%d 後面小節的長度不該變' % (num, den))


class BpmSnapTests(unittest.TestCase):
    """整數毫秒造成的 BPM 抖動：180 BPM 不該顯示成 179.9 / 180.1。

    使用者回報「BPM 算出來在 179.9 和 180.1 反覆跳」。180 BPM 的 4/4 小節是
    1333.33 ms，存成整數只能 1333 或 1334，回推就是 180.05 / 179.91。
    """

    def test_a_180bpm_bar_reads_exactly_180(self):
        from qt_editor.models import snap_measured_bpm
        # 1333 和 1334 都要讀成 180（兩條小節線各自最多差 0.5 ms）
        self.assertEqual(snap_measured_bpm(1333, 4), 180.0)
        self.assertEqual(snap_measured_bpm(1334, 4), 180.0)
        # 2/4（兩拍）和單拍的量測窗也一樣
        self.assertEqual(snap_measured_bpm(666, 2), 180.0)
        self.assertEqual(snap_measured_bpm(667, 2), 180.0)
        self.assertEqual(snap_measured_bpm(333, 1), 180.0)
        self.assertEqual(snap_measured_bpm(334, 1), 180.0)

    def test_a_real_non_integer_tempo_is_reported_as_measured(self):
        """吸附不能吃掉真的速度：1400 ms 就是 171.43，不是 171.5 也不是 171。"""
        from qt_editor.models import snap_measured_bpm
        self.assertEqual(snap_measured_bpm(1400, 4), 171.43)
        self.assertEqual(snap_measured_bpm(1524, 4), 157.48)

    def test_no_snap_when_it_would_need_more_than_a_millisecond(self):
        from qt_editor.models import snap_measured_bpm, BPM_SNAP_TOLERANCE_MS
        ideal = 4 * 60000.0 / 200.0                      # 200 BPM = 1200 ms
        off = int(ideal + BPM_SNAP_TOLERANCE_MS + 1)     # 差 2 ms
        self.assertNotEqual(snap_measured_bpm(off, 4), 200.0)

    def test_every_measure_of_a_180bpm_chart_reads_the_same(self):
        m = NoteModel.create_new('t', 180.0, 40.0, 4)
        m.rebuild_display_cache()
        got = {m.get_measure_bpm(i) for i in range(m.count_measures())}
        self.assertEqual(got, {180.0}, '同一個速度的譜不該量出多個 BPM：%s' % sorted(got))

    def test_a_constant_tempo_chart_writes_one_tempo_event(self):
        """抖動會讓 tempo_events_from_measures 每一小節都補一個速度事件。"""
        m = NoteModel.create_new('t', 180.0, 40.0, 4)
        m.rebuild_display_cache()
        self.assertEqual(len(m.tempo_events_from_measures()), 1)



class ShrinkingMeasuresPileUpTests(unittest.TestCase):
    """縮短小節時音符不可以疊在一起。

    使用者報的：「選中多個小節修改時，設置『小節內音符絕對時間不變』的基準點
    是所在小節的開始，當小節時值變少時音符就會錯位疊在一起。」

    機制：均分模式本來是「小節內的音符留在原地、小節之後的音符平移 delta」。
    兩條規則接起來不是單調映射——起點落在被切掉那一段的音符留在原地，後面的
    音符滑過來疊在它上面。4 小節 4/4、每拍一顆共 16 顆，前 3 個改成 3/4 之後
    只剩 13 個不同的起音時刻。
    """

    def chart(self, bars=8):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        # 每拍一顆：縮短 4/4 → 3/4 時第 4 拍那顆就落在被切掉的那一段
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def shrink(self, m, upto=3, uniform=True, time_uniform=False):
        for mi in range(upto):
            m.set_measure_time_signature(mi, 3, 4, uniform=uniform,
                                         time_uniform=time_uniform)

    def test_no_two_notes_end_up_at_the_same_time(self):
        for uniform in (True, False):
            m = self.chart()
            before = len({int(n.start) for n in m.notes_tree})
            self.shrink(m, uniform=uniform)
            after = len({int(n.start) for n in m.notes_tree})
            self.assertEqual(after, before,
                             'uniform=%s：不同起音時刻從 %d 掉到 %d'
                             % (uniform, before, after))

    def test_the_note_count_never_changes(self):
        for uniform in (True, False):
            for tu in (True, False):
                m = self.chart()
                before = len(m.notes_tree)
                self.shrink(m, uniform=uniform, time_uniform=tu)
                self.assertEqual(len(m.notes_tree), before,
                                 'uniform=%s time_uniform=%s 刪到音符了'
                                 % (uniform, tu))

    def test_the_view_mode_does_not_decide_whether_notes_survive(self):
        """`time_uniform` 的值來自檢視模式，不該改變結果。

        它本來會在小節縮短時把尾巴那段的音符刪掉——等於「現在用哪種顯示方式」
        決定了音符會不會被刪。
        """
        for uniform in (True, False):
            a = self.chart()
            b = self.chart()
            self.shrink(a, uniform=uniform, time_uniform=True)
            self.shrink(b, uniform=uniform, time_uniform=False)
            self.assertEqual(sorted((n.start, n.end) for n in a.notes_tree),
                             sorted((n.start, n.end) for n in b.notes_tree),
                             'uniform=%s：檢視模式改變了結果' % uniform)

    def test_absolute_mode_does_not_move_a_single_note(self):
        m = self.chart()
        before = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        self.shrink(m, uniform=True)
        after = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        self.assertEqual(after, before, '均分模式不該動到任何音符')

    def test_absolute_mode_keeps_the_song_length(self):
        """音符一顆都沒動，音檔也沒變短，曲末就不該跟著 delta 走。"""
        m = self.chart()
        before = float(m.music_end_ms)
        self.shrink(m, uniform=True)
        self.assertEqual(float(m.music_end_ms), before)

    def test_scaling_mode_keeps_notes_inside_their_measure(self):
        m = self.chart()
        self.shrink(m, uniform=False)
        for mi in range(3):
            s, e = m.get_measure_time_range(mi)
            inside = [int(n.start) for n in m.notes_tree if s <= n.start < e]
            self.assertEqual(len(inside), 4, '第 %d 小節該還有 4 顆' % mi)
            self.assertEqual(len(set(inside)), 4, '第 %d 小節有音符疊在一起' % mi)

    def test_the_beat_entries_stay_well_formed(self):
        for uniform in (True, False):
            m = self.chart()
            self.shrink(m, uniform=uniform)
            beats = m.get_beat_entries()
            idx = [i for i, _ms in beats]
            mss = [ms for _i, ms in beats]
            self.assertEqual(idx, sorted(idx), 'uniform=%s index 要遞增' % uniform)
            self.assertEqual(mss, sorted(mss), 'uniform=%s ms 要遞增' % uniform)
            self.assertEqual(len(set(mss)), len(mss),
                             'uniform=%s 有重複的 ms' % uniform)


class GridCoverageTests(unittest.TestCase):
    """均分模式縮短小節會讓格線短一截（音符沒動、小節線往前擠）。

    補回去是呼叫端的事：`_append_measures` 會把 `music_end_ms` 推到新的格線
    盡頭，在一個範圍上逐小節補就變成棘輪——實測連改 5 個小節，8 小節的譜長成
    13 小節、曲末從 16000 爬到 23500。
    """

    def chart(self, bars=8):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def test_grid_end_ms_reports_the_last_bar_line(self):
        m = self.chart()
        total = m.count_measures()
        self.assertEqual(m.grid_end_ms(), m.get_measure_time_range(total - 1)[1])

    def test_shrinking_moves_the_grid_end_earlier(self):
        m = self.chart()
        before = m.grid_end_ms()
        for mi in range(5):
            m.set_measure_time_signature(mi, 3, 4, uniform=True)
        self.assertLess(m.grid_end_ms(), before, '格線該變短了')

    def test_extending_puts_it_back(self):
        m = self.chart()
        before = m.grid_end_ms()
        for mi in range(5):
            m.set_measure_time_signature(mi, 3, 4, uniform=True)
        added = m.extend_grid_to_cover(float(before))
        self.assertGreater(added, 0)
        self.assertGreaterEqual(m.grid_end_ms(), before)

    def test_extending_does_not_touch_the_notes(self):
        m = self.chart()
        before_grid = m.grid_end_ms()
        before_notes = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        for mi in range(5):
            m.set_measure_time_signature(mi, 3, 4, uniform=True)
        m.extend_grid_to_cover(float(before_grid))
        self.assertEqual(sorted((int(n.start), int(n.end)) for n in m.notes_tree),
                         before_notes)

    def test_batching_covers_the_gap_without_overshooting(self):
        """整批補一次：剛好蓋過目標，不多補。"""
        m = self.chart()
        target = m.grid_end_ms()
        before_count = m.count_measures()
        for mi in range(5):
            m.set_measure_time_signature(mi, 3, 4, uniform=True)
        gap = target - m.grid_end_ms()
        added = m.extend_grid_to_cover(float(target))
        self.assertGreaterEqual(m.grid_end_ms(), target, '沒蓋到目標')
        # 一個小節 2000ms，缺口 2500ms → 兩個小節剛好；不該補第三個
        self.assertEqual(added, 2, '缺口 %dms 補了 %d 個小節' % (gap, added))
        self.assertEqual(m.count_measures(), before_count + 2)

    def test_calling_it_per_measure_is_what_ratchets(self):
        """所以呼叫端要整批做一次。

        這個測試不是在要求逐小節也要正確——它擋的是「把 extend 搬回
        `set_measure_time_signature` 裡面」。`_append_measures` 一次只能接整個
        小節，所以每改一小節就補一次會一路超補上去。
        """
        batched = self.chart()
        target = batched.grid_end_ms()
        for mi in range(5):
            batched.set_measure_time_signature(mi, 3, 4, uniform=True)
        batched.extend_grid_to_cover(float(target))

        per_measure = self.chart()
        for mi in range(5):
            t = per_measure.grid_end_ms()
            per_measure.set_measure_time_signature(mi, 3, 4, uniform=True)
            per_measure.extend_grid_to_cover(float(t))
        self.assertGreater(per_measure.count_measures(),
                           batched.count_measures(),
                           '逐小節補居然沒有超補？那就可以搬回去了')

    def test_extending_does_not_change_the_song_length(self):
        """補小節線不是把歌變長。"""
        m = self.chart()
        target = m.grid_end_ms()
        before = float(m.music_end_ms)
        for mi in range(5):
            m.set_measure_time_signature(mi, 3, 4, uniform=True)
        m.extend_grid_to_cover(float(target))
        self.assertEqual(float(m.music_end_ms), before)

    def test_it_stops_instead_of_spinning(self):
        """接不上去的時候要停，不要空轉。"""
        m = self.chart()
        end = m.grid_end_ms()
        self.assertEqual(m.extend_grid_to_cover(float(end)), 0,
                         '已經蓋到了就不該補')


class DialogSequenceTests(unittest.TestCase):
    """對話框「多小節 BPM／拍號」實際跑的那一串呼叫。

    `change_measures_bpm_dialog` 對範圍內每個小節依序呼叫 `set_measure_bpm`
    ＋ `set_measure_time_signature`，整批跑完才補一次格線。這裡複製那個順序，
    因為真正會壞的是**連續套用**，不是單一次呼叫。
    """

    def chart(self, bars=20):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def run_dialog(self, m, first, last, bpm, num, den, uniform,
                   adjust_notes=True, time_uniform=False):
        grid_end_before = m.grid_end_ms()
        for mi in range(first, last + 1):
            m.set_measure_bpm(mi, bpm, uniform=time_uniform,
                              adjust_notes=adjust_notes)
            m.set_measure_time_signature(mi, num, den, uniform=uniform,
                                         time_uniform=time_uniform)
        added = 0
        if uniform and grid_end_before is not None and (m.grid_end_ms() or 0) < grid_end_before:
            added = m.extend_grid_to_cover(float(grid_end_before))
        return added

    def test_the_absolute_mode_never_piles_notes_up(self):
        for time_uniform in (True, False):
            m = self.chart()
            before = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
            self.run_dialog(m, 5, 12, 150.0, 3, 4, uniform=True,
                            adjust_notes=False, time_uniform=time_uniform)
            after = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
            self.assertEqual(len(after), len(before),
                             'time_uniform=%s 掉了音符' % time_uniform)
            self.assertEqual(len({s for s, _e in after}), len({s for s, _e in before}),
                             'time_uniform=%s 有音符疊在一起' % time_uniform)
            self.assertEqual(after, before,
                             'time_uniform=%s 不調整音符時不該動到任何音符' % time_uniform)

    def test_the_scaling_mode_never_piles_notes_up(self):
        m = self.chart()
        before = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        self.run_dialog(m, 5, 12, 150.0, 3, 4, uniform=False)
        after = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        self.assertEqual(len(after), len(before), '掉了音符')
        self.assertEqual(len({s for s, _e in after}), len({s for s, _e in before}),
                         '有音符疊在一起')

    def test_the_grid_is_topped_back_up_once(self):
        m = self.chart()
        before = m.grid_end_ms()
        added = self.run_dialog(m, 5, 12, 150.0, 3, 4, uniform=True,
                                adjust_notes=False)
        self.assertGreater(added, 0, '格線縮短了卻沒補')
        self.assertGreaterEqual(m.grid_end_ms(), before)

    def test_the_beat_entries_stay_well_formed_through_the_whole_batch(self):
        for uniform in (True, False):
            m = self.chart()
            self.run_dialog(m, 5, 12, 150.0, 3, 4, uniform=uniform,
                            adjust_notes=not uniform)
            beats = m.get_beat_entries()
            idx = [i for i, _ms in beats]
            mss = [ms for _i, ms in beats]
            self.assertEqual(idx, sorted(idx), 'uniform=%s index 要遞增' % uniform)
            self.assertEqual(mss, sorted(mss), 'uniform=%s ms 要遞增' % uniform)
            self.assertEqual(len(set(mss)), len(mss),
                             'uniform=%s 有重複的 ms' % uniform)
