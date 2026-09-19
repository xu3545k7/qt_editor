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

    beat_data 的單位是四分音符。2/2 的分子是 2，但一小節仍然是 4 個四分音符；
    6/8 的分子是 6，一小節只有 3 個。拿 numerator 當單位數的話，把一小節改成
    2/2 會讓它只佔一半——那一小節被切成兩半、後面每一小節都往前擠一格，
    使用者看到的就是「改一個拍號結果後面全部小節都變了」。
    """

    def model(self):
        m = NoteModel.create_new('t', 120.0, 40.0, 4)
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
