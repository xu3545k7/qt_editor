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


class ShiftExpressionTests(unittest.TestCase):
    """平移踏板與強弱記號（不動音符、不動小節線）。

    使用者：「但是踏板也要往前甚至裁掉阿」——選取平移只搬音符的話，踏法就和
    音符錯開了。被推到 0 之前的部分要裁掉：一半在外面的切短，整段都在外面的
    直接消失。
    """

    def chart(self):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make(i, 1000 + 500 * i) for i in range(8)]
        m.pedal_spans = [[200.0, 1500.0], [2000.0, 3000.0], [5000.0, 6000.0]]
        m.dynamics = {0: [[300.0, 90.0, 0.0], [2500.0, 60.0, 0.0]]}
        m.rebuild_display_cache()
        return m

    def spans(self, m):
        return [[int(a), int(b)] for a, b in m.pedal_spans]

    def marks(self, m):
        return [int(x[0]) for x in m.dynamics[0]]

    def test_everything_moves_later(self):
        m = self.chart()
        m.shift_expression(700)
        self.assertEqual(self.spans(m), [[900, 2200], [2700, 3700], [5700, 6700]])
        self.assertEqual(self.marks(m), [1000, 3200])

    def test_a_span_hanging_off_the_front_is_clipped(self):
        m = self.chart()
        info = m.shift_expression(-500)
        self.assertEqual(self.spans(m)[0], [0, 1000], '該切短，不是整段丟掉')
        self.assertEqual(info['pedals_dropped'], 0)

    def test_a_span_entirely_before_the_start_is_dropped(self):
        m = self.chart()
        info = m.shift_expression(-1800)
        self.assertEqual(self.spans(m), [[200, 1200], [3200, 4200]])
        self.assertEqual(info['pedals_dropped'], 1)

    def test_it_never_moves_the_notes(self):
        m = self.chart()
        before = [(n.start, n.end) for n in m.notes_tree]
        m.shift_expression(-500)
        self.assertEqual([(n.start, n.end) for n in m.notes_tree], before)

    def test_it_never_moves_the_bar_lines(self):
        m = self.chart()
        before = list(m.get_beat_entries())
        m.shift_expression(-500)
        self.assertEqual(list(m.get_beat_entries()), before)

    def test_a_range_only_moves_what_fits_inside_it(self):
        m = self.chart()
        info = m.shift_expression(-400, 1800, 4000)
        self.assertEqual(self.spans(m), [[200, 1500], [1600, 2600], [5000, 6000]],
                         '只有完整落在範圍內的那一段該動')
        self.assertEqual(info['pedals'], 1)

    def test_a_callable_delta_is_measured_at_each_position(self):
        """變速譜上照每個東西自己的位置換算拍長（和音符同一條規則）。"""
        m = self.chart()
        m.shift_expression(lambda ms: -ms / 10.0)
        self.assertEqual(self.spans(m)[0][0], 180)       # 200 - 20
        self.assertEqual(self.spans(m)[1][0], 1800)      # 2000 - 200

    def test_marks_squashed_onto_the_same_moment_are_deduped(self):
        """全部夾到 0 的時候不能疊成一堆。"""
        m = self.chart()
        info = m.shift_expression(-99999)
        self.assertEqual(self.marks(m), [0])
        self.assertEqual(info['dynamics_dropped'], 1)

    def test_a_chart_without_expression_is_fine(self):
        m = self.chart()
        m.pedal_spans = []
        m.dynamics = {}
        self.assertEqual(m.shift_expression(-500),
                         {'pedals': 0, 'pedals_dropped': 0,
                          'dynamics': 0, 'dynamics_dropped': 0})


class SelectionShiftCarriesTheExpressionTests(unittest.TestCase):
    """選取平移要把踏板與強弱記號一起帶走。

    使用者：「但是踏板也要往前甚至裁掉阿」。選取平移原本只搬 `n.start/end`，
    踏板留在原地——踏法就和音符錯開了。

    範圍規則：全選＝搬全部；部分選取＝只搬完整落在選取時間範圍內的。全選要
    特別處理，因為踏板常常在第一顆音符之前就踩下去（實測 8 份 AI 轉譜有 5 份
    如此）——照「完整落在範圍內」判斷會把那一段留下。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.chart_view import ChartView
        from qt_editor.settings import settings
        self.app = QApplication.instance() or QApplication([])
        self.settings = settings
        self.before = settings.get('shift_moves_pedal', True)
        settings.set('shift_moves_pedal', True)
        self.view = ChartView()
        self.view.resize(1200, 720)

    def tearDown(self):
        self.settings.set('shift_moves_pedal', self.before)

    def chart(self):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [make(i, 1000 + 500 * i) for i in range(8)]
        m.pedal_spans = [[200.0, 1500.0], [2000.0, 3000.0], [5000.0, 6000.0]]
        m.dynamics = {0: [[300.0, 90.0, 0.0]]}
        m.rebuild_display_cache()
        self.view.load_model(m)
        self.view.rebuild_mapper()
        return m

    def spans(self, m):
        return [[int(a), int(b)] for a, b in m.pedal_spans]

    def test_select_all_moves_every_pedal_span(self):
        m = self.chart()
        self.view.select_all()
        self.view.shift_selected_time(-500)
        self.assertEqual(self.spans(m), [[0, 1000], [1500, 2500], [4500, 5500]])

    def test_the_notes_still_move(self):
        m = self.chart()
        self.view.select_all()
        self.view.shift_selected_time(-500)
        self.assertEqual([n.start for n in m.notes_tree][:3], [500, 1000, 1500])

    def test_a_part_selection_only_moves_what_is_inside_it(self):
        m = self.chart()
        self.view.selected = {n.idx for n in m.notes_tree[2:5]}
        self.view.shift_selected_time(-300)
        self.assertEqual(self.spans(m),
                         [[200, 1500], [1700, 2700], [5000, 6000]])

    def test_the_bounds_are_measured_before_the_notes_move(self):
        """搬完才量的話，剛好在邊緣的踏板會被判成在外面。

        實測那個 bug：部分選取往前移 300ms，該動的那一段一動也沒動——因為
        量到的是移動後的範圍（1700~2900），而踏板是 2000~3000。
        """
        m = self.chart()
        self.view.selected = {n.idx for n in m.notes_tree[2:5]}
        self.view.shift_selected_time(-300)
        self.assertNotEqual(self.spans(m)[1], [2000, 3000], '邊緣那一段沒被搬')

    def test_turning_it_off_leaves_the_pedal_alone(self):
        m = self.chart()
        self.settings.set('shift_moves_pedal', False)
        self.view.select_all()
        self.view.shift_selected_time(-500)
        self.assertEqual(self.spans(m), [[200, 1500], [2000, 3000], [5000, 6000]])
        self.assertEqual([n.start for n in m.notes_tree][:3], [500, 1000, 1500],
                         '關掉只是不搬踏板，音符還是要搬')

    def test_shifting_by_beats_carries_it_too(self):
        m = self.chart()
        self.view.select_all()
        self.view.shift_selected_by_beats(-1.0)        # 120 BPM → 500ms
        self.assertEqual(self.spans(m), [[0, 1000], [1500, 2500], [4500, 5500]])

    def test_a_span_pushed_off_the_front_is_dropped(self):
        m = self.chart()
        self.view.select_all()
        self.view.shift_selected_time(-2500)
        self.assertEqual(self.spans(m), [[0, 500], [2500, 3500]])

    def test_undo_puts_the_pedal_back(self):
        m = self.chart()
        self.view.select_all()
        self.view.shift_selected_time(-2500)
        m.undo()
        self.assertEqual(self.spans(m), [[200, 1500], [2000, 3000], [5000, 6000]])
        self.assertEqual([n.start for n in m.notes_tree][:3], [1000, 1500, 2000])

    def test_the_bar_lines_never_move(self):
        m = self.chart()
        before = list(m.get_beat_entries())
        self.view.select_all()
        self.view.shift_selected_time(-500)
        self.assertEqual(list(m.get_beat_entries()), before)
