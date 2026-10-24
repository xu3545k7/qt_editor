# -*- coding: utf-8 -*-
"""每小節 BPM 的表。

使用者要的：「每個小節線單獨 BPM」——原本只能一次改一個小節，或把一整段設成
同一個值。

最關鍵的性質是**恆等**：把讀出來的每小節 BPM 原樣套回去，什麼都不該變。
第一版不是這樣——它照 BPM 把整條格線重鋪，而 BPM 是由小節長度反推、還吸附過
（`snap_measured_bpm`）的「看法」，不是檔案裡的東西。實測 400 份真譜有三成
對不起來：小節邊界累積偏到 6~84 秒，一筆拍點不是一個四分音符的譜小節數還會
翻倍。那等於「打開表格按個確定」就毀掉譜面。

現在只重鋪被改過的行，其餘的拍點連 index 和筆數都照原樣搬。
"""

import os
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


class MeasureGridRowsTests(unittest.TestCase):
    def chart(self, bars=12):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def test_one_row_per_measure(self):
        m = self.chart()
        self.assertEqual(len(m.measure_grid_rows()), m.count_measures())

    def test_each_row_reports_that_measures_own_bpm(self):
        m = self.chart()
        rows = m.measure_grid_rows()
        for i, (_num, _den, bpm) in enumerate(rows):
            self.assertAlmostEqual(bpm, m.get_measure_bpm(i), places=2)

    def test_rows_carry_the_time_signature(self):
        m = self.chart()
        for num, den, _bpm in m.measure_grid_rows():
            self.assertEqual((num, den), (4, 4))


class IdentityTests(unittest.TestCase):
    """原樣套回 ＝ 什麼都不變。這是整張表能不能用的前提。"""

    def chart(self, bars=12):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def snapshot(self, m):
        return {
            'count': m.count_measures(),
            'bounds': [m.get_measure_time_range(i) for i in range(m.count_measures())],
            'notes': sorted((int(n.start), int(n.end)) for n in m.notes_tree),
            'end': float(m.music_end_ms or 0.0),
            'beats': list(m.get_beat_entries()),
        }

    def test_applying_the_rows_unchanged_changes_nothing(self):
        m = self.chart()
        before = self.snapshot(m)
        info = m.set_measure_grid(m.measure_grid_rows())
        self.assertEqual(info['changed'], 0, '沒改的話一行都不該被重鋪')
        self.assertEqual(self.snapshot(m), before)

    def test_it_reports_zero_changes(self):
        m = self.chart()
        info = m.set_measure_grid(m.measure_grid_rows())
        self.assertEqual(info['changed'], 0)
        self.assertEqual(info['shift_ms'], 0)

    def test_a_tiny_bpm_wobble_counts_as_unchanged(self):
        """`get_measure_bpm` 的值是吸附過的，讀回來差個 0.001 很正常。"""
        m = self.chart()
        before = self.snapshot(m)
        rows = [(n, d, b + 0.005) for n, d, b in m.measure_grid_rows()]
        info = m.set_measure_grid(rows)
        self.assertEqual(info['changed'], 0)
        self.assertEqual(self.snapshot(m), before)


class ChangingOneMeasureTests(unittest.TestCase):
    def chart(self, bars=12):
        m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make_note(500 * i, 500 * i + 100, i)
                        for i in range(bars * 4)]
        m.rebuild_display_cache()
        return m

    def change(self, m, idx, factor):
        rows = m.measure_grid_rows()
        rows[idx] = (rows[idx][0], rows[idx][1], rows[idx][2] * factor)
        return m.set_measure_grid(rows)

    def test_earlier_measures_never_move(self):
        m = self.chart()
        before = [m.get_measure_time_range(i) for i in range(5)]
        self.change(m, 5, 1.5)
        self.assertEqual([m.get_measure_time_range(i) for i in range(5)], before)

    def test_the_edited_measure_gets_the_bpm_it_asked_for(self):
        m = self.chart()
        want = m.get_measure_bpm(5) * 1.5
        rows = m.measure_grid_rows()
        rows[5] = (rows[5][0], rows[5][1], want)
        m.set_measure_grid(rows)
        self.assertAlmostEqual(m.get_measure_bpm(5), want, delta=want * 0.01)

    def test_notes_do_not_move_by_default(self):
        m = self.chart()
        before = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        self.change(m, 5, 1.5)
        self.assertEqual(sorted((int(n.start), int(n.end)) for n in m.notes_tree),
                         before, '小節線是底層結構，改它不該動到音符')

    def test_notes_snap_when_asked(self):
        m = self.chart()
        before = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
        rows = m.measure_grid_rows()
        rows[5] = (rows[5][0], rows[5][1], rows[5][2] * 1.5)
        info = m.set_measure_grid(rows, move_notes=True)
        self.assertGreater(info['moved'], 0)
        self.assertNotEqual(sorted((int(n.start), int(n.end)) for n in m.notes_tree),
                            before)

    def test_the_song_length_never_changes(self):
        m = self.chart()
        before = float(m.music_end_ms)
        self.change(m, 5, 1.5)
        self.assertEqual(float(m.music_end_ms), before)

    def test_the_note_count_never_changes(self):
        m = self.chart()
        before = len(m.notes_tree)
        self.change(m, 5, 0.5)
        self.assertEqual(len(m.notes_tree), before)

    def test_the_beat_entries_stay_well_formed(self):
        for factor in (0.5, 0.8, 1.25, 2.0):
            m = self.chart()
            self.change(m, 5, factor)
            beats = m.get_beat_entries()
            idx = [i for i, _ms in beats]
            mss = [ms for _i, ms in beats]
            self.assertEqual(idx, sorted(idx), '×%s index 要遞增' % factor)
            self.assertEqual(mss, sorted(mss), '×%s ms 要遞增' % factor)
            self.assertEqual(len(set(mss)), len(mss), '×%s 有重複的 ms' % factor)

    def test_the_entry_count_is_preserved(self):
        """沒改的小節連拍點筆數都要照原樣——筆數猜錯會讓小節數翻倍。"""
        m = self.chart()
        before = len(m.get_beat_entries())
        self.change(m, 5, 1.5)
        self.assertEqual(len(m.get_beat_entries()), before)

    def test_slowing_a_measure_pushes_the_rest_later(self):
        m = self.chart()
        before = m.get_measure_time_range(8)[0]
        info = self.change(m, 5, 0.5)       # 變慢 → 小節變長
        self.assertGreater(info['shift_ms'], 0)
        self.assertGreater(m.get_measure_time_range(8)[0], before)

    def test_speeding_a_measure_pulls_the_rest_earlier(self):
        m = self.chart()
        before = m.get_measure_time_range(8)[0]
        info = self.change(m, 5, 2.0)
        self.assertLess(info['shift_ms'], 0)
        self.assertLess(m.get_measure_time_range(8)[0], before)


class RealChartTests(unittest.TestCase):
    """真譜上的恆等性質。合成測試過、真譜壞掉，這個專案已經發生太多次。"""

    ROOT = 'D:/Nostalgia/PAN-001-2024102200_extracted'

    def charts(self, limit=12):
        if not os.path.isdir(self.ROOT):
            self.skipTest('沒有官方曲庫')
        out = []
        for dirpath, _d, files in os.walk(self.ROOT):
            for f in sorted(files):
                if f.endswith('.xml') and '00normal' in f:
                    out.append(os.path.join(dirpath, f))
            if len(out) >= limit:
                break
        if not out:
            self.skipTest('找不到官方譜')
        return out[:limit]

    def test_identity_holds_on_official_charts(self):
        checked = 0
        for path in self.charts():
            m = NoteModel()
            try:
                m.load_xml(path)
            except Exception:                              # noqa: BLE001
                continue                                   # 加密格式，讀不開
            rows = m.measure_grid_rows()
            if len(rows) < 8:
                continue
            checked += 1
            before = [m.get_measure_time_range(i) for i in range(len(rows))]
            notes = sorted((int(n.start), int(n.end)) for n in m.notes_tree)
            count = m.count_measures()
            info = m.set_measure_grid(rows)
            self.assertEqual(info['changed'], 0, os.path.basename(path))
            self.assertEqual(m.count_measures(), count, os.path.basename(path))
            self.assertEqual([m.get_measure_time_range(i) for i in range(len(rows))],
                             before, os.path.basename(path))
            self.assertEqual(sorted((int(n.start), int(n.end)) for n in m.notes_tree),
                             notes, os.path.basename(path))
        if not checked:
            self.skipTest('沒有讀得開的官方譜')


if __name__ == '__main__':
    unittest.main()
