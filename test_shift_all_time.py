"""樂曲總資訊的「整體位移」：小節線要跟著搬。

以前只搬 `notes_tree`，小節線留在原地——音符和小節的相對位置就跑掉了，本來在
第 3 拍的音會變成落在別的地方。現在 beat_data / beat_timings、拍號標記、踏板、
強弱記號、music_end 一起平移。

往後移會在最前面留下一段**不屬於任何小節**的空白：小節是從第一個拍點開始切的，
拍點搬到 delta 之後，[0, delta) 就不屬於任何小節。遊戲顯示的 BPM 取自第一小節
（兩個拍點的間距），平移不改變間距，所以顯示值不受影響。
"""

import unittest

from qt_editor.models import GNote, NoteModel


def make(idx, start, pitch=60, dur=200):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = 0
    n.velocity = 90
    n.min_key, n.max_key = 4, 6
    n.note_type = 0
    return n


class ShiftAllTimeTests(unittest.TestCase):
    def chart(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make(i, i * 500) for i in range(8)]
        m.rebuild_display_cache()
        return m

    # ── 基本 ────────────────────────────────────────────────────
    def test_zero_is_a_no_op(self):
        m = self.chart()
        before = [n.start for n in m.notes_tree]
        self.assertEqual(m.shift_all_time(0), 0)
        self.assertEqual([n.start for n in m.notes_tree], before)

    def test_notes_move(self):
        m = self.chart()
        m.shift_all_time(2000)
        self.assertEqual([n.start for n in m.notes_tree],
                         [2000 + i * 500 for i in range(8)])

    def test_note_lengths_are_preserved(self):
        m = self.chart()
        lens = [n.end - n.start for n in m.notes_tree]
        m.shift_all_time(1234)
        self.assertEqual([n.end - n.start for n in m.notes_tree], lens)

    # ── 小節線 ──────────────────────────────────────────────────
    def test_the_bar_lines_move_too(self):
        m = self.chart()
        before = [ms for _i, ms in m.get_beat_entries()[:5]]
        m.shift_all_time(2000)
        after = [ms for _i, ms in m.get_beat_entries()[:5]]
        self.assertEqual(after, [b + 2000 for b in before])

    def test_notes_keep_their_position_within_the_bar(self):
        """這是重點：只搬音符不搬小節線的話，這個會壞掉。"""
        m = self.chart()
        bar0 = m.get_measure_time_range(0)[0]
        before = [n.start - bar0 for n in m.notes_tree]
        m.shift_all_time(2000)
        bar0 = m.get_measure_time_range(0)[0]
        after = [n.start - bar0 for n in m.notes_tree]
        self.assertEqual(after, before)

    def test_the_measure_count_does_not_change(self):
        m = self.chart()
        before = m.count_measures()
        m.shift_all_time(2000)
        self.assertEqual(m.count_measures(), before)

    # ── 前面的空白 ──────────────────────────────────────────────
    def test_a_non_measure_gap_appears_at_the_front(self):
        m = self.chart()
        m.shift_all_time(2000)
        first_bar_start = m.get_measure_time_range(0)[0]
        self.assertEqual(first_bar_start, 2000,
                         '第一小節應該從 delta 開始，前面是不屬於小節的空白')

    def test_the_first_measure_bpm_is_unchanged(self):
        """遊戲顯示的就是第一小節的 BPM，平移不該動到它。"""
        m = self.chart()
        before = m.get_measure_bpm(0)
        m.shift_all_time(2000)
        self.assertAlmostEqual(m.get_measure_bpm(0), before, places=2)

    # ── 其他資料 ────────────────────────────────────────────────
    def test_music_end_moves(self):
        m = self.chart()
        before = m.music_end_ms
        m.shift_all_time(2000)
        self.assertAlmostEqual(m.music_end_ms, before + 2000)

    def test_pedal_spans_move(self):
        m = self.chart()
        m.pedal_spans = [[1000.0, 2000.0]]
        m.shift_all_time(500)
        self.assertEqual(m.pedal_spans, [[1500.0, 2500.0]])

    def test_dynamics_marks_move(self):
        m = self.chart()
        m.dynamics_set(0, [[1000.0, 80.0, False]])
        m.shift_all_time(500)
        self.assertAlmostEqual(m.dynamics_marks(0)[0][0], 1500.0)

    def test_time_signature_markers_move(self):
        m = self.chart()
        m.time_sig_changes = [(0, 4, 4), (4000, 3, 4)]
        m.shift_all_time(1000)
        self.assertEqual([ms for ms, _n, _d in m.time_sig_changes], [1000, 5000])

    # ── 負值 ────────────────────────────────────────────────────
    def test_a_negative_shift_moves_things_earlier(self):
        m = self.chart()
        m.shift_all_time(3000)
        m.shift_all_time(-1000)
        self.assertEqual(m.notes_tree[0].start, 2000)

    def test_an_oversized_negative_shift_does_not_collapse_the_grid(self):
        """夾的是整個位移量，不是逐一夾在 0。

        逐一夾的話開頭幾筆會被壓成**同一個時間**（拍點變 0,0,0），小節長度歸零、
        BPM 變成天文數字，整份譜就毀了。之前的測試只斷言「>= 0」，資料已經爛掉
        還是會過。
        """
        m = self.chart()
        before = [ms for _i, ms in m.get_beat_entries()[:6]]
        gaps = [before[i + 1] - before[i] for i in range(len(before) - 1)]
        bpm = m.get_measure_bpm(0)
        m.shift_all_time(-999999)
        after = [ms for _i, ms in m.get_beat_entries()[:6]]
        self.assertEqual([after[i + 1] - after[i] for i in range(len(after) - 1)],
                         gaps, '拍點間距必須原封不動')
        self.assertAlmostEqual(m.get_measure_bpm(0), bpm, places=2)
        self.assertGreaterEqual(min(after), 0)

    def test_a_negative_shift_with_no_room_is_a_no_op(self):
        m = self.chart()          # 從 0 開始，前面沒有空間
        before = [n.start for n in m.notes_tree]
        beats = [ms for _i, ms in m.get_beat_entries()[:5]]
        m.shift_all_time(-5000)
        self.assertEqual([n.start for n in m.notes_tree], before)
        self.assertEqual([ms for _i, ms in m.get_beat_entries()[:5]], beats)

    def test_a_negative_shift_uses_all_the_room_available(self):
        m = self.chart()
        m.shift_all_time(4000)
        m.shift_all_time(-999999)
        self.assertEqual(m.get_beat_entries()[0][1], 0, '應該剛好移到 0')

    def test_it_reports_how_much_was_actually_applied(self):
        m = self.chart()
        m.shift_all_time(1000)
        m.shift_all_time(-999999)
        self.assertEqual(m.last_shift_ms, -1000)

    def test_earliest_time_covers_every_kind_of_data(self):
        m = self.chart()
        m.shift_all_time(9000)
        m.pedal_spans = [[500.0, 800.0]]        # 比拍點還早
        self.assertEqual(m.earliest_time_ms(), 500)

    def test_it_marks_the_chart_dirty(self):
        m = self.chart()
        m.dirty = False
        m.shift_all_time(100)
        self.assertTrue(m.dirty)

    def test_it_is_undoable(self):
        m = self.chart()
        before = [n.start for n in m.notes_tree]
        beats = [ms for _i, ms in m.get_beat_entries()[:5]]
        m.push_history()
        m.shift_all_time(2000)
        m.undo()
        self.assertEqual([n.start for n in m.notes_tree], before)
        self.assertEqual([ms for _i, ms in m.get_beat_entries()[:5]], beats)


if __name__ == '__main__':
    unittest.main()
