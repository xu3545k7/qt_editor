"""beat index 有跳號的譜面（官方檔常見）上的小節編輯。

真實案例：hanei-inspion-piano-arrangement-deemo-ii.xml 的 beat index 以 1000 為
一拍（explicit beat units），但在第 12 筆跳掉兩號（11000 → 13000 → 15000）。
從那之後 entry 的**位置**就比 beat index 少 2 拍，愈後面差愈多。

小節的權威範圍走 `_get_precise_measure_boundaries()`（照 beat 單位算），但編輯
小節的那幾支函式以前用 `measure_idx * entries_per_bar`（照位置算）去找要改哪幾
筆 entry。兩者在跳號之後就對不上——改第 45 小節的 BPM 實際改到第 46 小節的
拍點，其餘全部被平移，畫面上整段時間軸炸開。
"""

import unittest

from qt_editor.models import GNote, NoteModel

SCALE = 1000          # 一拍 = index +1000
BEAT_MS = 500         # 120 BPM
BARS = 24
BEATS_PER_BAR = 4


def make_note(start, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 100
    n.gate = 100
    n.note_type = 0
    n.min_key = 4
    n.max_key = 6
    return n


class BeatIndexGapTests(unittest.TestCase):
    def _model(self, gap_at=12, gap_beats=2):
        """建一份 index 刻度 1000、且在 gap_at 處跳掉 gap_beats 拍的譜。"""
        m = NoteModel.create_new('t', 120.0, 120.0, BEATS_PER_BAR)
        entries = []
        beat = 0
        for i in range(BARS * BEATS_PER_BAR):
            if i == gap_at:
                beat += gap_beats          # 跳號：index 前進，但沒有 entry
            entries.append((beat * SCALE, beat * BEAT_MS))
            beat += 1
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(BARS * BEATS_PER_BAR)]
        m.rebuild_display_cache()
        return m

    def test_the_fixture_really_has_the_gap(self):
        m = self._model()
        self.assertTrue(m._uses_explicit_beat_units())
        self.assertEqual(m._detect_beat_index_scale(), float(SCALE))
        idxs = [i for i, _ in m.get_beat_entries()]
        gaps = [k for k in range(1, len(idxs)) if idxs[k] - idxs[k - 1] != SCALE]
        self.assertEqual(len(gaps), 1)

    def test_entry_slice_matches_the_measure_ms_range(self):
        m = self._model()
        beats = m.get_beat_entries()
        for mi in range(m.count_measures()):
            start_ms, end_ms = m.get_measure_time_range(mi)
            if start_ms is None:
                continue
            expected = [k for k, (_i, ms) in enumerate(beats) if start_ms <= ms < end_ms]
            if not expected:
                continue
            self.assertEqual(m._measure_entry_slice(mi),
                             (expected[0], expected[-1] + 1),
                             'measure %d' % (mi + 1))

    def test_entry_slice_diverges_from_the_naive_formula_after_the_gap(self):
        # 這條在確認 fixture 真的會踩到舊 bug：跳號之後位置公式就是錯的
        m = self._model()
        epb = m.entries_per_bar
        late = m.count_measures() - 2
        self.assertNotEqual(m._measure_entry_slice(late), (late * epb, late * epb + epb))

    def test_set_measure_bpm_late_in_the_chart_only_touches_that_measure(self):
        m = self._model()
        target = m.count_measures() - 3
        before = [round(m.get_measure_bpm(i), 1) for i in range(m.count_measures())]
        start_ms, _ = m.get_measure_time_range(target)
        early_notes = [(n.start, n.end) for n in m.notes_tree if n.start < start_ms]

        m.set_measure_bpm(target, 85.0, uniform=False, adjust_notes=True)

        after = [round(m.get_measure_bpm(i), 1) for i in range(m.count_measures())]
        self.assertAlmostEqual(after[target], 85.0, places=1)
        self.assertEqual(after[:target], before[:target])
        self.assertEqual(after[target + 1:], before[target + 1:])
        self.assertEqual([(n.start, n.end) for n in m.notes_tree if n.start < start_ms],
                         early_notes)

    def test_a_range_of_measures_all_land_on_the_requested_bpm(self):
        m = self._model()
        first, last = 14, 20
        for mi in range(first, last + 1):
            m.set_measure_bpm(mi, 85.0, uniform=False, adjust_notes=True)
        got = [round(m.get_measure_bpm(i), 1) for i in range(first, last + 1)]
        self.assertEqual(got, [85.0] * (last - first + 1))
        lengths = {m.get_measure_time_range(i)[1] - m.get_measure_time_range(i)[0]
                   for i in range(first, last + 1)}
        self.assertEqual(len(lengths), 1, 'measures should all be the same length')

    def test_insert_measure_keeps_the_beat_index_scale(self):
        m = self._model()
        before = m.count_measures()
        self.assertTrue(m.insert_measure(14, 120.0))
        self.assertEqual(m.count_measures(), before + 1)
        self.assertEqual(m._detect_beat_index_scale(), float(SCALE))
        self.assertTrue(m._uses_explicit_beat_units())

    def test_inserted_measure_is_empty_and_pushes_the_rest_back(self):
        m = self._model()
        start_ms, _ = m.get_measure_time_range(14)
        after_before = [n.start for n in m.notes_tree if n.start >= start_ms]
        m.insert_measure(14, 120.0)
        s, e = m.get_measure_time_range(14)
        self.assertEqual(s, start_ms)
        self.assertEqual([n for n in m.notes_tree if s <= n.start < e], [])
        bar_ms = e - s
        self.assertEqual(sorted(n.start for n in m.notes_tree if n.start >= e),
                         sorted(v + bar_ms for v in after_before))

    def test_delete_measure_keeps_the_beat_index_scale(self):
        m = self._model()
        before = m.count_measures()
        m.delete_measure(14)
        self.assertEqual(m.count_measures(), before - 1)
        self.assertEqual(m._detect_beat_index_scale(), float(SCALE))

    def test_insert_then_delete_round_trips_after_the_gap(self):
        m = self._model()
        starts = [n.start for n in m.notes]
        count = m.count_measures()
        m.insert_measure(16, 120.0)
        m.delete_measure(16)
        self.assertEqual(m.count_measures(), count)
        self.assertEqual([n.start for n in m.notes], starts)


class MeasureBpmIsolationTests(unittest.TestCase):
    """改一個小節的 BPM，只有那個小節可以變。

    兩個會讓改動「漏到前後小節」的原因：

    1. 小節邊界所在的拍單位**沒有 entry**（檔案不保證每個拍都有拍點）。邊界的
       ms 只能靠左右內插得到，把小節壓短之後邊界不會落在 start+new_dur，多出來
       的長度就被前後小節分掉。
    2. `time_sig_changes` 存的是絕對 ms，改小節長度時沒跟著移。變拍位置對到
       錯的地方，小節切法從那裡整個歪掉，連很遠的小節都會受影響。
    """

    def _model(self, missing_downbeats=(12, 14), sig_change_at_bar=3):
        """刻意做一份「有小節缺拍點、而且中間變拍號」的譜。"""
        m = NoteModel.create_new('t', 120.0, 120.0, BEATS_PER_BAR)
        entries = [(b * SCALE, b * BEAT_MS)
                   for b in range(BARS * BEATS_PER_BAR)
                   if b not in missing_downbeats]
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        # 第 4 小節改成 2/4，之後回到 4/4（和真實檔案一樣的形狀）
        start = sig_change_at_bar * BEATS_PER_BAR * BEAT_MS
        m.time_sig_changes = [(0, 4, 4), (start, 2, 4), (start + 2 * BEAT_MS, 4, 4)]
        m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(BARS * BEATS_PER_BAR)]
        m.rebuild_display_cache()
        return m

    def test_the_fixture_has_measures_whose_boundary_has_no_entry(self):
        m = self._model()
        scale = m._detect_beat_index_scale()
        units = {round(i / scale, 6) for i, _ms in m.get_beat_entries()}
        missing = [mi for mi in range(m.count_measures())
                   if (b := m._measure_unit_bounds(mi)) and round(b[0], 6) not in units]
        self.assertTrue(missing, 'fixture should have boundary-less measures')

    def test_changing_one_measure_leaves_every_other_measure_alone(self):
        m = self._model()
        n = m.count_measures()
        for target in range(n - 1):
            m = self._model()
            before = [round(m.get_measure_bpm(i), 1) for i in range(n)]
            m.set_measure_bpm(target, 160.0, uniform=False, mode='scale')
            after = [round(m.get_measure_bpm(i), 1) for i in range(n)]
            bled = [i + 1 for i in range(n)
                    if i != target and abs(after[i] - before[i]) > 0.6]
            self.assertEqual(bled, [], 'bar %d bled into %s' % (target + 1, bled))
            self.assertAlmostEqual(after[target], 160.0, delta=1.0)

    def test_time_signature_markers_follow_the_retimed_measure(self):
        m = self._model()
        marks_before = [ms for ms, _n, _d in m.time_sig_changes]
        # 改第 1 小節（在所有標記之前）→ 後面的標記要整段跟著移
        m.set_measure_bpm(0, 240.0, uniform=False, mode='scale')
        marks_after = [ms for ms, _n, _d in m.time_sig_changes]
        self.assertNotEqual(marks_before[1:], marks_after[1:])
        self.assertEqual(marks_before[0], marks_after[0])   # 0 在前面，不動

    def test_boundary_anchors_do_not_move_existing_beats(self):
        m = self._model()
        before = {idx: ms for idx, ms in m.get_beat_entries()}
        m._ensure_measure_boundary_entries(4)
        after = {idx: ms for idx, ms in m.get_beat_entries()}
        self.assertGreater(len(after), len(before), 'should have added an anchor')
        for idx, ms in before.items():
            self.assertEqual(after[idx], ms, 'existing beat %d moved' % idx)


class MeasureTimeSignatureIsolationTests(unittest.TestCase):
    """改一個小節的拍號，只有那個小節可以變。

    以前改第 6 小節的拍號，看到的是**第 5 小節**變成 3/4、第 6 小節還是 4/4；
    改本來就是 2/4 的那一小節更慘，後面 50 個小節全被設成 2/4。三個原因：

    1. 重建拍點時把所有 beat index 重編成 0,1,2,…，官方檔的 ×1000 刻度被打掉，
       整份譜換成另一套小節切法。
    2. 「這一小節之前的拍號」用 `tms <= start_ms` 找，把這一小節自己的標記也算
       進去，於是恢復標記把後面全設成這一小節原本的拍號。
    3. 標記是絕對 ms、小節邊界是內插出來的，兩者差 1ms 就會讓恢復點落到前一個
       或後一個小節。
    """

    def _model(self):
        m = NoteModel.create_new('t', 120.0, 120.0, BEATS_PER_BAR)
        entries = [(b * SCALE, b * BEAT_MS)
                   for b in range(BARS * BEATS_PER_BAR) if b not in (12, 14)]
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        start = 3 * BEATS_PER_BAR * BEAT_MS
        m.time_sig_changes = [(0, 4, 4), (start, 2, 4), (start + 2 * BEAT_MS, 4, 4)]
        m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(BARS * BEATS_PER_BAR)]
        m.rebuild_display_cache()
        return m

    @staticmethod
    def _sig(m, mi):
        start_ms, _e = m.get_measure_time_range(mi)
        if start_ms is None:
            return None
        num = m.get_beats_per_bar_at_ms(start_ms)
        den = m.time_sig_denominator
        for ms, _n, d in m.time_sig_changes:
            if ms <= start_ms:
                den = d
            else:
                break
        return '%s/%s' % (num, den)

    def test_changing_one_measure_signature_leaves_the_others_alone(self):
        n = self._model().count_measures()
        for target in range(n - 1):
            m = self._model()
            before = [self._sig(m, i) for i in range(n)]
            m.set_measure_time_signature(target, 3, 4, uniform=True, time_uniform=False)
            after = [self._sig(m, i) for i in range(n)]
            self.assertEqual(after[target], '3/4', 'bar %d' % (target + 1))
            # 只比對「改動前後都存在」的小節：小節拍數變了之後，譜尾那個不完整
            # 的小節可能剛好放不下（或多露出一個），那不是被改壞。
            common = min(m.count_measures(), n)
            bled = [i + 1 for i in range(common)
                    if i != target and before[i] != after[i]]
            self.assertEqual(bled, [], 'bar %d bled into %s' % (target + 1, bled))

    def test_editing_a_measure_that_already_has_its_own_signature(self):
        # 第 4 小節本來就是 2/4；改成 3/4 之後，後面不能被設成 2/4
        m = self._model()
        n = m.count_measures()
        self.assertEqual(self._sig(m, 3), '2/4')
        m.set_measure_time_signature(3, 3, 4, uniform=True, time_uniform=False)
        self.assertEqual(self._sig(m, 3), '3/4')
        self.assertEqual([self._sig(m, i) for i in range(4, min(n, 10))], ['4/4'] * 6)

    def test_beat_index_scale_survives_a_signature_change(self):
        m = self._model()
        m.set_measure_time_signature(6, 3, 4, uniform=True, time_uniform=False)
        self.assertEqual(m._detect_beat_index_scale(), float(SCALE))
        self.assertTrue(m._uses_explicit_beat_units())


class JsonBackedMeasureTests(unittest.TestCase):
    """JSON 譜面（遊戲匯出的 .json）也要能做小節操作。

    這種譜 `root is None`，拍點存在 `json_meta['beat_timings' / 'beat_indices']`。
    add/insert/delete_measure 以前一開頭就 `if self.root is None: return`，UI 層
    也一起擋掉，於是明明有 213 筆拍點、54 小節，按刪除小節卻回報「找不到小節
    資料」。set_measure_bpm 早就支援 JSON 了，只有這三支沒跟上。
    """

    def _model(self):
        m = NoteModel.create_new('t', 120.0, 120.0, BEATS_PER_BAR)
        entries = [(b * SCALE, b * BEAT_MS) for b in range(BARS * BEATS_PER_BAR)]
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(BARS * BEATS_PER_BAR)]
        m.rebuild_display_cache()
        # 改成 JSON 譜：拿掉 XML root，只留 json_meta 的拍點
        m.root = None
        return m

    def test_the_fixture_is_json_backed_but_still_has_measures(self):
        m = self._model()
        self.assertIsNone(m.root)
        self.assertTrue(m.get_beat_entries())
        self.assertEqual(m.count_measures(), BARS)

    def test_delete_measure_works_without_xml(self):
        m = self._model()
        before = m.count_measures()
        start_ms, end_ms = m.get_measure_time_range(9)
        inside = sum(1 for n in m.notes_tree if start_ms <= n.start < end_ms)

        deleted = m.delete_measure(9)

        self.assertEqual(deleted, inside)
        self.assertEqual(m.count_measures(), before - 1)

    def test_insert_measure_works_without_xml(self):
        m = self._model()
        before = m.count_measures()
        start_ms, _ = m.get_measure_time_range(9)

        self.assertTrue(m.insert_measure(9, 120.0))

        self.assertEqual(m.count_measures(), before + 1)
        s, e = m.get_measure_time_range(9)
        self.assertEqual(s, start_ms)
        self.assertEqual([n for n in m.notes_tree if s <= n.start < e], [])

    def test_add_measure_works_without_xml(self):
        m = self._model()
        before = m.count_measures()
        m.add_measure(120.0)
        self.assertEqual(m.count_measures(), before + 1)

    def test_add_measure_keeps_the_beat_index_scale(self):
        # index 要照譜面自己的刻度前進（一拍 +1000），不是 +1
        m = self._model()
        m.add_measure(120.0)
        self.assertEqual(m._detect_beat_index_scale(), float(SCALE))
        self.assertTrue(m._uses_explicit_beat_units())

    def test_edits_land_in_json_meta(self):
        m = self._model()
        before = len(m.json_meta['beat_timings'])
        m.insert_measure(9, 120.0)
        self.assertEqual(len(m.json_meta['beat_timings']), before + BEATS_PER_BAR)
        self.assertEqual(len(m.json_meta['beat_indices']),
                         len(m.json_meta['beat_timings']))

    def test_insert_then_delete_round_trips(self):
        m = self._model()
        starts = [n.start for n in m.notes]
        count = m.count_measures()
        m.insert_measure(9, 120.0)
        m.delete_measure(9)
        self.assertEqual(m.count_measures(), count)
        self.assertEqual([n.start for n in m.notes], starts)


if __name__ == '__main__':
    unittest.main()
