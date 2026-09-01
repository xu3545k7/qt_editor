"""beat_data 的間距：一小節幾筆，以及漏拍的偵測與修補。

`anima-xi-fullarr-phyxinon.json` 同時踩到兩件事：

1. 它的 entry 是**半小節一筆**（1304ms = 2 拍 @92BPM）。舊程式只認得
   「一小節一筆」或「一拍一筆」兩種，硬選最接近的那個 → 選到「一拍一筆」，
   於是每個小節的 BPM 變成一半（92 → 46）、小節數也少一半。
2. 它有 16 段間距是別人的兩倍（漏了拍點），索引卻是連號的，所以沒有任何
   地方標示那裡少了一筆——那幾小節的 BPM 直接減半，時間均分模式下一個畫面
   涵蓋的音樂長度也跟著跳來跳去。
"""

import unittest

from qt_editor.models import NoteModel


def chart_with_beats(times, bpm=92.0, bpb=4):
    m = NoteModel.create_new('t', bpm, 300.0, bpb)
    m._epb_mode = None
    m._epb_count = None
    m.json_meta = {}
    m._write_beat_data_pairs([(i, int(t)) for i, t in enumerate(times)])
    m._epb_mode = None
    m._epb_count = None
    return m


def even(step, n, start=0):
    return [start + int(round(step * i)) for i in range(n)]


class EntriesPerBarTests(unittest.TestCase):
    def test_one_entry_per_bar(self):
        m = chart_with_beats(even(2608.7, 40))
        self.assertEqual(m._detect_entries_per_bar(), 1)

    def test_one_entry_per_beat(self):
        m = chart_with_beats(even(652.2, 80))
        self.assertEqual(m._detect_entries_per_bar(), 4)

    def test_half_a_bar_per_entry(self):
        # 這是 anima 那份的間距：舊程式沒有這個選項
        m = chart_with_beats(even(1304.3, 60))
        self.assertEqual(m._detect_entries_per_bar(), 2)

    def test_it_never_exceeds_the_beats_per_bar(self):
        m = chart_with_beats(even(60.0, 40))
        self.assertLessEqual(m._detect_entries_per_bar(), 4)
        self.assertGreaterEqual(m._detect_entries_per_bar(), 1)

    def test_a_stored_value_that_is_wildly_off_is_overridden(self):
        # anima 那份存的是 'beat'（=4），實際是 2；差兩倍就該相信量到的
        m = chart_with_beats(even(1304.3, 60))
        m.json_meta['editor_beat_entry_mode'] = 'beat'
        m._epb_count = None
        self.assertEqual(m.entries_per_bar, 2)

    def test_a_stored_value_that_agrees_is_kept(self):
        m = chart_with_beats(even(2608.7, 40))
        m.json_meta['editor_beat_entry_mode'] = 'bar'
        m._epb_count = None
        self.assertEqual(m.entries_per_bar, 1)

    def test_the_result_is_pinned_after_the_first_call(self):
        m = chart_with_beats(even(1304.3, 60))
        first = m.entries_per_bar
        m.bpm = 200.0          # 之後改 BPM 也不該讓判斷漂掉
        self.assertEqual(m.entries_per_bar, first)

    def test_a_new_chart_is_one_entry_per_bar(self):
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.assertEqual(m.entries_per_bar, 1)


class MissingBeatEntryTests(unittest.TestCase):
    def gapped(self):
        """40 筆，其中第 5 和第 20 格是兩倍寬（漏了一筆）。"""
        times, t = [], 0
        for i in range(40):
            times.append(int(round(t)))
            t += 1304.3 * (2 if i in (5, 20) else 1)
        return chart_with_beats(times)

    def test_it_finds_the_gaps(self):
        self.assertEqual(len(self.gapped().find_missing_beat_entries()), 2)

    def test_a_uniform_chart_has_nothing_to_repair(self):
        m = chart_with_beats(even(1304.3, 40))
        self.assertEqual(m.find_missing_beat_entries(), [])
        self.assertEqual(m.repair_missing_beat_entries(), 0)

    def test_repair_adds_exactly_the_missing_entries(self):
        m = self.gapped()
        before = len(m.get_beat_entries())
        self.assertEqual(m.repair_missing_beat_entries(), 2)
        self.assertEqual(len(m.get_beat_entries()), before + 2)

    def test_repair_makes_the_spacing_uniform(self):
        m = self.gapped()
        m.repair_missing_beat_entries()
        beats = m.get_beat_entries()
        gaps = [beats[i + 1][1] - beats[i][1] for i in range(len(beats) - 1)]
        self.assertLessEqual(max(gaps) - min(gaps), 2, gaps)

    def test_repair_renumbers_the_indices(self):
        m = self.gapped()
        m.repair_missing_beat_entries()
        idx = [i for i, _ms in m.get_beat_entries()]
        self.assertEqual(idx, sorted(idx))
        self.assertEqual(len(set(idx)), len(idx))

    def test_running_it_twice_changes_nothing_the_second_time(self):
        m = self.gapped()
        m.repair_missing_beat_entries()
        self.assertEqual(m.repair_missing_beat_entries(), 0)

    def test_a_real_tempo_change_is_not_treated_as_a_gap(self):
        # 一段真的變慢（每格 1461ms，不是整數倍）不該被當成漏拍
        times, t = [], 0
        for i in range(40):
            times.append(int(round(t)))
            t += 1461.0 if 10 <= i < 20 else 1304.3
        m = chart_with_beats(times)
        self.assertEqual(m.find_missing_beat_entries(), [])

    def test_repair_marks_the_chart_dirty(self):
        m = self.gapped()
        m.dirty = False
        m.repair_missing_beat_entries()
        self.assertTrue(m.dirty)


if __name__ == '__main__':
    unittest.main()
