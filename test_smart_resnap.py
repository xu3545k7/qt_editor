# -*- coding: utf-8 -*-
"""智慧重排吸附：用「幾個小節 ＋ BPM」的序列重建整條小節線。

使用者的實際問題（POTENTIAL 那首）：AI 把整首判成 199.95 BPM，而實際是
150/155/160 的變拍。錯的格線把音符的 start 一起吸歪了。他的原話：

> 音符 ms 大致準確，但拍子可能被判定的固定判錯了 200，導致很多音符的 start
> 被吸附影響。**所以小節範圍不能固定，會繼承錯誤的判斷。**

> 基本上每個音符的位置和結束點大致不變（畢竟是 ai 轉的，差距很小），但頭部的
> 吸附失真應該要好很多。畢竟我用精準 bpm 加上拍號，應該能判斷出來總長度了。

第一版把小節範圍當輸入，於是一小節的長度被「跨度 ÷ 小節數」綁死，他給的
150/155/160 在一份 199.95 的譜上**每一段都被「BPM 太慢」擋掉**——看起來像按了
沒反應。所以輸入改成「幾個小節 ＋ BPM」，小節編號是算出來的結果。
"""

import os
import shutil
import tempfile
import unittest

import mido

from qt_editor import smart_resnap as SR
from qt_editor.models import NoteModel


def _write_midi(path, bpm=200.0, bars=16, numerator=4, pitch=60):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.MetaMessage('time_signature', numerator=numerator,
                                  denominator=4, time=0))
    for _bar in range(bars):
        for _beat in range(numerator):
            track.append(mido.Message('note_on', note=pitch, velocity=90, time=0))
            track.append(mido.Message('note_off', note=pitch, velocity=0, time=240))
            track.append(mido.Message('note_on', note=pitch, velocity=0, time=240))
    mid.tracks.append(track)
    mid.save(path)


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='resnap_test_')
        self.path = os.path.join(self.dir, 'song.mid')
        _write_midi(self.path)
        self.model = NoteModel()
        self.model.load_midi(self.path, auto_arrange=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _span(self):
        starts = [int(n.start) for n in self.model.notes_tree]
        ends = [int(n.end) for n in self.model.notes_tree]
        return (min(starts), max(ends)) if starts else (0, 0)


class SegmentMathTests(unittest.TestCase):
    def test_a_bar_is_numerator_times_four_over_denominator(self):
        """一小節佔幾個四分音符是 分子×4÷分母（見 bar-units-vs-numerator）。"""
        self.assertAlmostEqual(SR.Segment(1, 120.0, 4, 4).total_ms, 2000.0, places=6)
        self.assertAlmostEqual(SR.Segment(1, 120.0, 3, 4).total_ms, 1500.0, places=6)
        self.assertAlmostEqual(SR.Segment(1, 120.0, 6, 8).total_ms, 1500.0, places=6)

    def test_beats_is_bars_times_numerator(self):
        self.assertEqual(SR.Segment(16, 150.0, 4, 4).beats, 64)
        self.assertEqual(SR.Segment(16, 150.0, 3, 4).beats, 48)

    def test_nonsense_segments_are_dropped(self):
        got = SR.normalise([SR.Segment(0, 150.0),
                            SR.Segment(4, 0.0),
                            SR.Segment(4, 99999.0),
                            SR.Segment(4, 150.0)])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].bars, 4)

    def test_the_order_given_is_the_order_used(self):
        """段落是依序鋪下去的，排序會把結構弄錯。"""
        got = SR.normalise([SR.Segment(8, 160.0), SR.Segment(4, 150.0)])
        self.assertEqual([s.bpm for s in got], [160.0, 150.0])


class BuildBeatsTests(unittest.TestCase):
    def test_the_beat_count_matches_the_segments(self):
        segs = [SR.Segment(4, 150.0), SR.Segment(4, 160.0)]
        beats = SR.build_beats(segs, 0.0)
        self.assertEqual(len(beats), 4 * 4 + 4 * 4 + 1)

    def test_each_segment_runs_at_its_own_tempo(self):
        beats = SR.build_beats([SR.Segment(1, 150.0), SR.Segment(1, 300.0)], 0.0)
        self.assertAlmostEqual(beats[1] - beats[0], 400.0, places=6)
        self.assertAlmostEqual(beats[5] - beats[4], 200.0, places=6)

    def test_it_starts_where_told(self):
        beats = SR.build_beats([SR.Segment(1, 150.0)], 1725.0)
        self.assertAlmostEqual(beats[0], 1725.0, places=6)

    def test_segment_joins_do_not_drift(self):
        """段落內用累加、段落起點接前一段的游標，誤差不跨段累積。"""
        segs = [SR.Segment(16, 155.0)] * 8
        beats = SR.build_beats(segs, 0.0)
        self.assertAlmostEqual(beats[-1], sum(s.total_ms for s in segs), delta=0.001)


class BarsToCoverTests(_Base):
    """「用精準 BPM 加拍號應該能判斷出總長度」——所以小節數要能算出來。"""

    def test_it_works_out_how_many_bars_are_needed(self):
        _first, end = self._span()
        seg = SR.Segment(1, 200.0, 4, 4)
        bars = SR.bars_to_cover(self.model, seg)
        covered = SR.grid_start_ms(self.model) + bars * seg.bar_ms
        self.assertGreaterEqual(covered, end, '算出來的小節數蓋不到曲末')
        self.assertLess(covered - seg.bar_ms, end, '多算了一個小節')

    def test_it_accounts_for_segments_already_laid(self):
        seg = SR.Segment(4, 200.0, 4, 4)
        whole = SR.bars_to_cover(self.model, seg)
        rest = SR.bars_to_cover(self.model, seg, before=[SR.Segment(4, 200.0)])
        self.assertEqual(rest, whole - 4)

    def test_it_never_returns_a_negative(self):
        got = SR.bars_to_cover(self.model, SR.Segment(1, 200.0),
                               before=[SR.Segment(999, 200.0)])
        self.assertGreaterEqual(got, 0)


class PlanTests(_Base):
    def test_planning_does_not_touch_the_chart(self):
        before = self._span()
        SR.plan(self.model, [SR.Segment(16, 150.0)])
        self.assertEqual(self._span(), before)

    def test_it_reports_how_far_short_the_grid_is(self):
        """段落總和幾乎不可能一次對上曲長，所以要講差多少。"""
        item = SR.plan(self.model, [SR.Segment(2, 150.0)])
        self.assertTrue(item.ok)
        self.assertGreater(item.short_by_ms, 0)
        self.assertIn('早結束', SR.describe(item))

    def test_an_overlong_grid_is_reported_too(self):
        item = SR.plan(self.model, [SR.Segment(400, 150.0)])
        self.assertLess(item.short_by_ms, 0)
        self.assertIn('多鋪', SR.describe(item))

    def test_no_segments_is_refused(self):
        item = SR.plan(self.model, [])
        self.assertFalse(item.ok)
        self.assertIn('沒有', SR.describe(item))

    def test_the_grid_starts_at_the_old_first_beat(self):
        """開頭的導入空白是刻意的，從 0 鋪會把整首的相位移掉。"""
        self.assertAlmostEqual(SR.plan(self.model, [SR.Segment(4, 150.0)]).start_ms,
                               SR.grid_start_ms(self.model), places=6)


class ApplyTests(_Base):
    def _segments(self):
        return [SR.Segment(8, 150.0), SR.Segment(8, 160.0)]

    def test_the_grid_is_replaced(self):
        # 比時間而不是比數量：合成譜剛好也是 65 個拍點，數量相同不代表沒換
        before = [ms for _i, ms in (self.model.get_beat_entries() or [])]
        item = SR.apply(self.model, SR.plan(self.model, self._segments()))
        after = [ms for _i, ms in (self.model.get_beat_entries() or [])]
        self.assertEqual(len(after), len(item.beats), '拍點數對不上計畫')
        self.assertNotEqual(after, before, '格線沒被換掉')
        # 第二段是 160 BPM，和原本的 200 不一樣
        self.assertNotAlmostEqual(after[-1] - after[-2], before[-1] - before[-2],
                                  delta=1.0)

    def test_no_notes_are_lost(self):
        before = len(self.model.notes_tree)
        SR.apply(self.model, SR.plan(self.model, self._segments()))
        self.assertEqual(len(self.model.notes_tree), before)

    def test_the_total_length_does_not_change(self):
        """音符只被搬到最近的格點、不做縮放，所以最後一顆音符不該跑遠。"""
        _first, end_before = self._span()
        item = SR.apply(self.model, SR.plan(self.model, self._segments()))
        _f, end_after = self._span()
        self.assertLessEqual(abs(end_after - end_before), item.max_shift_ms + 1)

    def test_lengths_survive(self):
        before = sorted(int(n.end) - int(n.start) for n in self.model.notes_tree)
        SR.apply(self.model, SR.plan(self.model, self._segments()))
        after = sorted(int(n.end) - int(n.start) for n in self.model.notes_tree)
        self.assertEqual(after, before, '只該搬起點，長度要跟著平移')

    def test_notes_land_on_the_new_grid(self):
        item = SR.apply(self.model, SR.plan(self.model, self._segments()))
        points = SR.grid_points(item.beats, 4)
        off = sum(1 for note in self.model.notes_tree
                  if not any(abs(float(note.start) - p) < 1.5 for p in points))
        self.assertEqual(off, 0, '有音符沒落在新格線上')

    def test_snapping_can_be_turned_off(self):
        before = sorted(int(n.start) for n in self.model.notes_tree)
        item = SR.apply(self.model, SR.plan(self.model, self._segments()), snap=False)
        after = sorted(int(n.start) for n in self.model.notes_tree)
        self.assertEqual(after, before, '不吸附時音符不該動')
        self.assertEqual(item.moved, 0)

    def test_a_refused_plan_changes_nothing(self):
        before = (len(self.model.get_beat_entries() or []), self._span())
        SR.apply(self.model, SR.plan(self.model, []))
        now = (len(self.model.get_beat_entries() or []), self._span())
        self.assertEqual(now, before)

    def test_the_max_shift_is_within_half_a_grid_cell(self):
        """最近格點吸附的上限就是半格，超過表示吸錯了。"""
        item = SR.apply(self.model, SR.plan(self.model, self._segments()))
        cell = min(b - a for a, b in zip(item.beats, item.beats[1:])) / 4.0
        self.assertLessEqual(item.max_shift_ms, cell / 2 + 1)


class DialogTests(_Base):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from qt_editor.smart_resnap_dialog import SmartResnapDialog
        return SmartResnapDialog(None, self.model)

    def test_there_is_no_measure_number_column(self):
        """小節編號是錯判的產物，拿它當輸入就繼承那個錯誤。"""
        dlg = self._dialog()
        headers = [dlg._table.horizontalHeaderItem(i).text()
                   for i in range(dlg._table.columnCount())]
        self.assertEqual(headers, ['小節數', '分子', '分母', 'BPM'])

    def test_it_starts_with_one_segment(self):
        self.assertEqual(len(self._dialog().segments()), 1)

    def test_copying_the_last_segment_repeats_it(self):
        dlg = self._dialog()
        dlg._cell(0, 0).setValue(12)
        dlg._cell(0, 3).setValue(155.0)
        dlg._add_row(copy_last=True)
        segs = dlg.segments()
        self.assertEqual((segs[1].bars, segs[1].bpm), (12, 155.0))

    def test_fill_to_the_end_works_out_the_bar_count(self):
        """「用精準 BPM 加拍號應該能判斷出總長度」就是這顆按鈕。"""
        dlg = self._dialog()
        dlg._cell(0, 0).setValue(1)
        dlg._cell(0, 3).setValue(200.0)
        dlg._fill_last()
        item = SR.plan(self.model, dlg.segments())
        self.assertGreaterEqual(item.grid_end_ms, item.needed_ms,
                                '補完還是蓋不到曲末')

    def test_the_preview_updates(self):
        dlg = self._dialog()
        dlg._cell(0, 0).setValue(8)
        dlg._cell(0, 3).setValue(150.0)
        self.assertIn('8 小節', dlg._preview.text())

    def test_the_division_only_matters_when_snapping(self):
        dlg = self._dialog()
        self.assertTrue(dlg._division.isEnabled())
        dlg._snap.setChecked(False)
        self.assertFalse(dlg._division.isEnabled())
        self.assertFalse(dlg.snap_enabled())

    def test_dropping_every_row_disables_ok(self):
        dlg = self._dialog()
        dlg._drop_row()
        self.assertEqual(dlg.segments(), [])
        self.assertFalse(dlg._ok.isEnabled())


class MenuEntryTests(unittest.TestCase):
    def test_the_measure_menu_offers_it(self):
        import contextlib
        import io as _io

        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        try:
            # `_measure_entries` 多帶一個「分組」欄位（工具列用它收成下拉）
            labels = [label for _g, label, _slot, _tip in win._measure_entries()]
            self.assertIn('智慧重排吸附…', labels)
        finally:
            win.view.model.dirty = False
            win.close()
        self.assertIsInstance(app, QApplication)


if __name__ == '__main__':
    unittest.main()


class DetectTests(unittest.TestCase):
    """從起音量出速度結構。

    使用者：「這首有非常多變的 bpm」。我一開始用「整首定速」的指標去評，得出
    「150/160 和雜訊同級」——那是錯的，因為定速指標會被佔多數的段落主導。
    逐視窗量就看得見：POTENTIAL 實測是 200 → 150 → 200。
    """

    def _onsets(self, plan, per_beat=4):
        """plan: [(bpm, 幾拍)]，每拍放 `per_beat` 顆音（預設 16 分），回傳毫秒。

        預設密一點是必要的：偵測視窗 6 秒要 20 顆音符才判斷，一拍一顆的話
        150 BPM 的視窗只有 15 顆會被跳過（真實資料每個視窗約 120 顆）。
        """
        out, t = [], 0.0
        for bpm, beats in plan:
            step = 60000.0 / bpm / per_beat
            for _i in range(beats * per_beat):
                out.append(t)
                t += step
        return out

    def test_a_steady_tempo_comes_out_as_one_segment(self):
        segs = SR.detect_segments(self._onsets([(200.0, 400)]))
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0].bpm, 200.0, delta=1.0)

    def test_a_gradual_tempo_change_is_found(self):
        """頻寬內（±12%）的變速追得到。"""
        segs = SR.detect_segments(self._onsets([(200.0, 300), (182.0, 300),
                                                (200.0, 300)]))
        self.assertGreaterEqual(len(segs), 2, '沒偵測到變速：%s'
                                % [s.bpm for s in segs])
        self.assertTrue(any(abs(s.bpm - 182.0) < 6 for s in segs),
                        '漏掉慢的那一段：%s' % [s.bpm for s in segs])

    def test_an_abrupt_big_jump_is_a_known_limitation(self):
        """>12% 的突變追不到——這是 `beat_detect.WINDOW_BAND` 的刻意設計。

        它限制每個視窗只能在目前速度的 ±12% 內找，為的是不讓速度跳到兩倍／
        一半（`beat_detect` 的註解：自己寫的「離格線多近」在短窗裡會挑出 1.5
        倍、2 倍，甚至飄到 942 BPM）。代價就是 200→150 這種突變追不上。

        釘住它是為了讓這個限制「看得見」：遇到真的有突變的譜要手動補段落。
        """
        from qt_editor import beat_detect as BD
        self.assertLess(BD.WINDOW_BAND, 0.25, '頻寬變寬了，這條限制要重新評估')
        segs = SR.detect_segments(self._onsets([(200.0, 200), (150.0, 150),
                                                (200.0, 200)]))
        self.assertTrue(all(abs(s.bpm - 150.0) > 10 for s in segs),
                        '追到了？那 WINDOW_BAND 應該放寬：%s' % [s.bpm for s in segs])

    def test_too_few_notes_gives_nothing(self):
        self.assertEqual(SR.detect_segments([0.0, 100.0, 200.0]), [])

    def test_the_band_blocks_the_half_tempo_trap(self):
        """不限頻帶的話 200 的曲子有一半視窗會選 100，看起來像速度亂跳。"""
        self.assertGreaterEqual(SR.DETECT_LO, 120.0)
        self.assertLessEqual(SR.DETECT_HI, 260.0)
        segs = SR.detect_segments(self._onsets([(200.0, 400)]))
        for seg in segs:
            self.assertGreaterEqual(seg.bpm, SR.DETECT_LO)
            self.assertLessEqual(seg.bpm, SR.DETECT_HI)

    def test_grid_offset_is_half_for_random_notes(self):
        """0.5 ＝ 隨機，這個正規化讓不同 BPM 可以比。"""
        import random
        rng = random.Random(7)
        noise = [rng.uniform(0, 60000) for _i in range(4000)]
        self.assertAlmostEqual(SR.grid_offset(noise, 200.0), 0.5, delta=0.03)

    def test_grid_offset_is_zero_on_the_grid(self):
        cell = 60000.0 / 200.0 / 4
        self.assertAlmostEqual(SR.grid_offset([i * cell for i in range(200)],
                                              200.0), 0.0, delta=1e-6)


class FastRunTests(_Base):
    """比格子還密的音串不能被壓在一起。

    使用者：「有很多高速音被你量化貼在一起了... 可能超過16分就不行了?」。實測
    POTENTIAL 有 54% 的相鄰起音間隔比一個 16 分格（75ms）還密、最密的只有
    29ms（200BPM 的 32 分是 37.5ms）。每顆各自吸的話不同的起音時刻從 1170 掉到
    1090。改成「和前一組的間隔小於一格時整串跟著平移」，保留內部相對時間。
    """

    def _dense(self, bpm=200.0, bars=8):
        """每拍 4 顆 16 分，再在每小節第一拍塞一串 32 分。"""
        from qt_editor.models import GNote
        notes = []
        beat = 60000.0 / bpm
        idx = 0
        for bar in range(bars):
            base = bar * beat * 4
            for b in range(4):
                for k in range(4):                     # 16 分
                    t = base + b * beat + k * beat / 4
                    n = GNote(None, idx); idx += 1
                    n.start = int(round(t)); n.end = n.start + 60
                    n.gate = 60; n.pitch = 60 + k; n.hand = 0; n.track = 1
                    n.min_key, n.max_key = 10, 12
                    notes.append(n)
            for k in range(8):                         # 32 分的快速串
                t = base + beat * 3 + k * beat / 8
                n = GNote(None, idx); idx += 1
                n.start = int(round(t)); n.end = n.start + 30
                n.gate = 30; n.pitch = 72 + k; n.hand = 0; n.track = 1
                n.min_key, n.max_key = 14, 16
                notes.append(n)
        self.model.notes_tree = notes
        return notes

    def test_distinct_onsets_survive(self):
        self._dense()
        before = len(set(int(n.start) for n in self.model.notes_tree))
        SR.apply(self.model, SR.plan(self.model, [SR.Segment(8, 200.0)]))
        after = len(set(int(n.start) for n in self.model.notes_tree))
        self.assertEqual(after, before, '高速音被壓在一起了（%d → %d）' % (before, after))

    def test_the_run_keeps_its_internal_timing(self):
        self._dense()
        def gaps():
            t = sorted(set(int(n.start) for n in self.model.notes_tree))
            return sorted(b - a for a, b in zip(t, t[1:]))[:8]
        before = gaps()
        SR.apply(self.model, SR.plan(self.model, [SR.Segment(8, 200.0)]))
        self.assertEqual(gaps(), before, '最密的那幾個間隔被改掉了')

    def test_the_shift_stays_within_half_a_cell(self):
        self._dense()
        item = SR.apply(self.model, SR.plan(self.model, [SR.Segment(8, 200.0)]))
        half = (60000.0 / 200.0 / 4) / 2
        self.assertLessEqual(item.max_shift_ms, half + 1)

    def test_notes_past_the_grid_are_left_alone(self):
        """格線沒蓋到的地方不吸——硬吸會被拉回最後一個格點。

        實測格線比曲末短 1 秒時，尾巴的音符位移到 272ms（半格只有 37ms）。
        """
        self._dense(bars=8)
        tail = max(int(n.start) for n in self.model.notes_tree)
        item = SR.apply(self.model, SR.plan(self.model, [SR.Segment(2, 200.0)]))
        self.assertLess(item.max_shift_ms, 60,
                        '超出格線的音符被硬拉了 %.0f ms' % item.max_shift_ms)
        self.assertEqual(max(int(n.start) for n in self.model.notes_tree), tail,
                         '格線外的音符不該被動到')

    def test_a_chord_still_moves_as_one(self):
        """同一刻的音符是和弦，要套同一個位移。"""
        from qt_editor.models import GNote
        notes = []
        for k, pitch in enumerate((60, 64, 67)):
            n = GNote(None, k)
            n.start = 137; n.end = 300; n.gate = 163
            n.pitch = pitch; n.hand = 0; n.track = 1
            n.min_key, n.max_key = 10, 12
            notes.append(n)
        self.model.notes_tree = notes
        SR.apply(self.model, SR.plan(self.model, [SR.Segment(4, 200.0)]))
        self.assertEqual(len(set(int(n.start) for n in notes)), 1, '和弦被拆開了')


class RegenerateTests(unittest.TestCase):
    """「鎖定精確位置、重生小節線」：丟掉格線重鋪，音符一顆都不動。

    和這個模組原本的用法剛好相反：

        apply(..., snap=True)  格線是對的 → 把音符搬上去
        regenerate(...)        音符是對的 → 重算格線套上去

    所以**沒有吸附選項**。有的話就不是這個功能了。
    """

    @staticmethod
    def _note(idx, start):
        from qt_editor.models import GNote
        n = GNote(None, idx)
        n.start, n.end, n.gate = start, start + 200, 200
        n.pitch, n.hand, n.note_type = 60, 0, 0
        n.min_key, n.max_key = 4, 4
        return n

    def chart(self, bpm=120.0, step=250, count=60):
        m = NoteModel.create_new('t', bpm, 40.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [self._note(i, 1000 + step * i) for i in range(count)]
        m.rebuild_display_cache()
        return m

    def notes(self, m):
        return sorted((int(n.start), int(n.end)) for n in m.notes_tree)

    def test_it_never_moves_a_note(self):
        m = self.chart()
        before = self.notes(m)
        SR.apply(m, SR.regenerate(m, 150.0), snap=False)
        self.assertEqual(self.notes(m), before)

    def test_the_new_grid_really_has_the_asked_for_bpm(self):
        m = self.chart()
        SR.apply(m, SR.regenerate(m, 150.0), snap=False)
        self.assertAlmostEqual(m.get_measure_bpm(0), 150.0, delta=0.05)

    def test_it_covers_the_last_note(self):
        m = self.chart()
        item = SR.regenerate(m, 150.0)
        self.assertGreaterEqual(item.grid_end_ms, item.needed_ms)

    def test_it_keeps_the_original_start_by_default(self):
        """開頭那段導入空白是刻意的，不要從 0 重鋪。"""
        m = self.chart()
        item = SR.regenerate(m, 150.0)
        self.assertAlmostEqual(item.start_ms, SR.grid_start_ms(m), delta=0.5)

    def test_a_custom_start_is_honoured(self):
        m = self.chart()
        item = SR.regenerate(m, 150.0, start_ms=1712.0)
        self.assertAlmostEqual(item.start_ms, 1712.0, delta=0.5)
        self.assertAlmostEqual(item.beats[0], 1712.0, delta=0.5)

    def test_the_time_signature_changes_the_bar_length(self):
        for num, den, want in ((4, 4, 1.0), (3, 4, 0.75), (6, 8, 0.75)):
            m = self.chart()
            item = SR.regenerate(m, 120.0, num, den)
            self.assertAlmostEqual(item.segments[0].bar_ms / 2000.0, want,
                                   delta=0.01, msg='%d/%d' % (num, den))

    def test_an_absurdly_slow_bpm_is_refused(self):
        """BPM 填 0.0001 會鋪出一拍 10 分鐘、橫跨 27 天的格線。

        `normalise` 擋不到這個——它只管「一拍短到不合理」。而且這種 BPM 只鋪
        **一個**小節，所以 MAX_REGEN_BARS 也攔不住。兩頭都要有界。
        """
        item = SR.regenerate(self.chart(), 0.0001)
        self.assertFalse(item.ok)
        self.assertIn('BPM', item.reason)

    def test_an_absurdly_fast_bpm_is_refused(self):
        item = SR.regenerate(self.chart(), 9999.0)
        self.assertFalse(item.ok)
        self.assertIn('BPM', item.reason)

    def test_ordinary_bpms_are_all_fine(self):
        for bpm in (60, 90, 120, 150, 180, 200, 240, 300):
            self.assertTrue(SR.regenerate(self.chart(), float(bpm)).ok, bpm)

    def test_detect_bpm_reads_the_chart_itself(self):
        """音符每 250ms 一顆 ＝ 240 BPM 的四分音符。"""
        self.assertAlmostEqual(SR.detect_bpm(self.chart(step=250)) or 0,
                               240.0, delta=1.0)

    def test_detect_bpm_gives_up_on_too_few_notes(self):
        self.assertIsNone(SR.detect_bpm(self.chart(count=5)))


class TempoFromSelectionTests(unittest.TestCase):
    """「這一段是 N 個小節」→ BPM。編曲軟體的 set tempo from selection。

    Logic 的「Adjust Tempo using…」、Reaper 的「Set project tempo from time
    selection」、Cubase 的 Time Warp 都是同一招：素材是對的，你知道它是幾小節，
    速度讓軟體算。對我們正好是「鎖定精確位置」那條路——音符不動，格線遷就它。
    """

    def test_four_bars_of_four_four(self):
        self.assertAlmostEqual(SR.bpm_from_span(8000, 4, 4, 4), 120.0, places=2)

    def test_twice_as_fast(self):
        self.assertAlmostEqual(SR.bpm_from_span(4000, 4, 4, 4), 240.0, places=2)

    def test_three_four_has_shorter_bars(self):
        """3/4 的小節是 4/4 的四分之三，同樣的速度下塞得進更多小節。"""
        self.assertAlmostEqual(SR.bpm_from_span(6000, 4, 3, 4), 120.0, places=2)

    def test_the_bpm_always_means_the_quarter_note(self):
        """6/8 的一小節 ＝ 3 個四分音符，所以 2000ms 一小節是 90 不是 180。

        和 `Segment.beat_ms`（`4×60000÷(分母×BPM)`）同一套；整個程式裡的 BPM
        都是四分音符的速度。
        """
        self.assertAlmostEqual(SR.bpm_from_span(4000, 2, 6, 8), 90.0, places=2)

    def test_half_bars_work(self):
        self.assertAlmostEqual(SR.bpm_from_span(4000, 2.0, 4, 4), 120.0, places=2)

    def test_nonsense_input_gives_nothing(self):
        self.assertIsNone(SR.bpm_from_span(0, 4))
        self.assertIsNone(SR.bpm_from_span(8000, 0))
        self.assertIsNone(SR.bpm_from_span(-100, 4))

    def test_a_result_outside_the_sane_range_is_refused(self):
        """一段 100ms 說成 100 小節會算出幾萬 BPM。"""
        self.assertIsNone(SR.bpm_from_span(100, 100))
        self.assertIsNone(SR.bpm_from_span(600000, 1))

    def test_it_round_trips_with_regenerate(self):
        """算出來的 BPM 鋪回去，那一段就真的是 N 個小節。"""
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        from qt_editor.models import GNote
        notes = []
        for i in range(40):
            n = GNote(None, i)
            n.start, n.end, n.gate = 1000 + 500 * i, 1000 + 500 * i + 200, 200
            n.pitch, n.hand, n.note_type = 60, 0, 0
            n.min_key, n.max_key = 4, 4
            notes.append(n)
        m.notes_tree = notes
        m.rebuild_display_cache()
        bpm = SR.bpm_from_span(8000, 4, 4, 4)
        item = SR.regenerate(m, bpm, 4, 4, start_ms=1000.0)
        SR.apply(m, item, snap=False)
        start, _e = m.get_measure_time_range(0)
        _s, end = m.get_measure_time_range(3)
        self.assertAlmostEqual(end - start, 8000, delta=5)
