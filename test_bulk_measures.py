"""批次增刪小節，以及「加了幾個空白小節，小節數卻整個變掉」的回歸測試。

使用者反饋：只在譜面開頭插了幾個空白小節，突然冒出一大堆空白小節。原因是
`add_measure` / `insert_measure` / `delete_measure` 的 beat index 算術都用
`scale * beats_per_bar / entries_per_bar` 推「一個小節占多少 index」，那條式子
只有在「一拍一筆 entry」的譜上才對：

* per-bar 的譜（一小節一筆 entry、index 間距 1）會算成 beats_per_bar，於是每插
  一個小節就在 index 上挖一個 bpb 寬的洞、每刪一個就把後面拉成負數。洞多到蓋過
  中位數之後 `_uses_explicit_beat_units()` 翻面，整份譜換一套小節切法——12/4 的
  譜插 8 個小節，8 小節會變成 2 小節。
* 混合拍號的 explicit 譜在 3 拍的段落硬塞 4 筆 entry，算出 750 這種非整拍 index，
  格線就歪了。

所以這裡的測試除了「小節數要對」，還一律檢查**index 格線本身**：間距的種類不變、
不出現負數、explicit 的譜上每筆都還踩在整拍上。
"""

import unittest

from qt_editor.models import GNote, NoteModel

SCALE = 1000          # explicit beat units：一拍 = index +1000
BEAT_MS = 500         # 120 BPM


def make_note(start, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 100
    n.gate = 100
    n.note_type = 0
    n.min_key = 4
    n.max_key = 6
    return n


def per_bar_model(bpb=4, bars=18):
    """一小節一筆 entry、index 間距 1（`create_new` 建出來的格式）。"""
    m = NoteModel.create_new('t', 120.0, bars * bpb * BEAT_MS / 1000.0, bpb)
    m.notes_tree = [make_note(i * bpb * BEAT_MS, i) for i in range(6)]
    m.rebuild_display_cache()
    return m


def per_beat_model(bars=12, bpb=4):
    """一拍一筆 entry、index 間距 1000（官方檔的格式）。"""
    m = NoteModel.create_new('t', 120.0, 120.0, bpb)
    m._write_beat_entries([(i * SCALE, i * BEAT_MS) for i in range(bars * bpb + 1)],
                          mark_precise=True)
    m._epb_mode = None
    m.notes_tree = [make_note(i * bpb * BEAT_MS, i) for i in range(6)]
    m.rebuild_display_cache()
    return m


def mixed_meter_model(bars_44=4, bars_34=8):
    """前段 4/4、後段 3/4 的 explicit 譜（真實編曲常見）。"""
    m = NoteModel.create_new('t', 120.0, 120.0, 4)
    entries, beat = [], 0
    for bar in range(bars_44 + bars_34):
        for _ in range(4 if bar < bars_44 else 3):
            entries.append((beat * SCALE, beat * BEAT_MS))
            beat += 1
    m._write_beat_entries(entries, mark_precise=True)
    m.time_sig_changes = [(0, 4, 4), (bars_44 * 4 * BEAT_MS, 3, 4)]
    m._epb_mode = None
    m.rebuild_display_cache()
    return m


def index_steps(model):
    """相鄰 entry 的 index 間距有哪幾種（格線有沒有被弄歪的指標）。"""
    idx = [i for i, _ms in model.get_beat_entries()]
    return sorted({idx[k] - idx[k - 1] for k in range(1, len(idx))})


class GridStaysIntactTests(unittest.TestCase):
    """小節數要對，而且 beat index 的格線不能被改掉。"""

    def _check_grid(self, m, where):
        idx = [i for i, _ms in m.get_beat_entries()]
        self.assertTrue(all(v >= 0 for v in idx), '%s：index 跑到負數 %s' % (
            where, [v for v in idx if v < 0][:5]))
        self.assertEqual(len(index_steps(m)), 1,
                         '%s：index 間距出現好幾種 %s' % (where, index_steps(m)))
        if m._uses_explicit_beat_units():
            scale = int(m._detect_beat_index_scale())
            off_grid = [v for v in idx if v % scale]
            self.assertEqual(off_grid, [], '%s：出現非整拍的 index %s' % (
                where, off_grid[:5]))

    def test_inserting_at_the_start_does_not_change_the_measure_count_wholesale(self):
        # 12/4 的 per-bar 譜插 8 個小節：修好前 8 小節會變成 2 小節
        for bpb in (4, 8, 12):
            for count in (1, 3, 8, 16):
                m = per_bar_model(bpb=bpb)
                before = m.measure_count()
                m.insert_measure(0, count=count)
                self.assertEqual(m.measure_count(), before + count,
                                 'bpb=%d 插入 %d 個' % (bpb, count))
                self._check_grid(m, 'per-bar bpb=%d 插入 %d' % (bpb, count))

    def test_deleting_never_pushes_indices_negative(self):
        for bpb in (4, 12):
            m = per_bar_model(bpb=bpb)
            before = m.measure_count()
            m.delete_measure(0, count=3)
            self.assertEqual(m.measure_count(), before - 3)
            self._check_grid(m, 'per-bar bpb=%d 刪除' % bpb)

    def test_appending_keeps_the_index_step(self):
        for build in (per_bar_model, per_beat_model):
            m = build()
            step_before = index_steps(m)
            before = m.measure_count()
            m.add_measure(count=6)
            self.assertEqual(m.measure_count(), before + 6)
            self.assertEqual(index_steps(m), step_before)
            self._check_grid(m, 'add_measure')

    def test_inserting_into_a_three_four_section_stays_on_the_beat(self):
        # 修好前會寫出 19750 / 20500 / 21250 這種「四分之三拍」的 index
        for at in (0, 5, 8):
            m = mixed_meter_model()
            before = m.measure_count()
            m.insert_measure(at)
            self.assertEqual(m.measure_count(), before + 1)
            self._check_grid(m, '混合拍號插在第 %d 小節前' % at)

    def test_the_inserted_measure_matches_the_local_meter(self):
        """插在 3/4 段落的空白小節就該是 3 拍，不是全域的 4 拍。"""
        m = mixed_meter_model()
        m.insert_measure(6)
        start, end = m.get_measure_time_range(6)
        self.assertEqual(end - start, 3 * BEAT_MS)
        s2, e2 = m.get_measure_time_range(7)        # 原本的第 6 小節
        self.assertEqual((s2, e2), (end, end + 3 * BEAT_MS))

    def test_the_grid_survives_a_long_insert_delete_session(self):
        m = per_bar_model(bpb=12)
        before = m.measure_count()
        for _ in range(5):
            m.insert_measure(0, count=4)
            m.delete_measure(0, count=2)
        self.assertEqual(m.measure_count(), before + 5 * 2)
        self._check_grid(m, '反覆增刪')


class BulkEqualsRepeatedSingleTests(unittest.TestCase):
    """一次做 N 個要和「做 N 次一個」結果完全相同。"""

    def _same(self, a, b):
        self.assertEqual(a.get_beat_entries(), b.get_beat_entries())
        self.assertEqual([n.start for n in a.notes], [n.start for n in b.notes])
        self.assertEqual(a.measure_count(), b.measure_count())
        self.assertEqual(a.music_end_ms, b.music_end_ms)

    def test_bulk_insert(self):
        for build, at in ((per_bar_model, 0), (per_beat_model, 3),
                          (mixed_meter_model, 5)):
            bulk, one = build(), build()
            bulk.insert_measure(at, count=4)
            for _ in range(4):
                one.insert_measure(at)
            self._same(bulk, one)

    def test_bulk_delete(self):
        for build, at in ((per_bar_model, 0), (per_beat_model, 3),
                          (mixed_meter_model, 5)):
            bulk, one = build(), build()
            self.assertEqual(bulk.delete_measure(at, count=3),
                             sum(one.delete_measure(at) for _ in range(3)))
            self._same(bulk, one)

    def test_bulk_append(self):
        bulk, one = per_beat_model(), per_beat_model()
        bulk.add_measure(count=5)
        for _ in range(5):
            one.add_measure()
        self._same(bulk, one)

    def test_notes_are_pushed_by_the_whole_block(self):
        m = per_bar_model()
        starts = [n.start for n in m.notes]
        bar_ms = m.get_measure_time_range(0)[1] - m.get_measure_time_range(0)[0]
        m.insert_measure(0, count=4)
        self.assertEqual([n.start for n in m.notes],
                         [s + 4 * bar_ms for s in starts])

    def test_insert_then_delete_the_same_block_round_trips(self):
        for build in (per_bar_model, per_beat_model, mixed_meter_model):
            m = build()
            entries = m.get_beat_entries()
            starts = [n.start for n in m.notes]
            m.insert_measure(2, count=5)
            m.delete_measure(2, count=5)
            self.assertEqual(m.get_beat_entries(), entries)
            self.assertEqual([n.start for n in m.notes], starts)

    def test_count_of_one_is_the_old_behaviour(self):
        m = per_bar_model()
        before = m.measure_count()
        self.assertTrue(m.insert_measure(3))
        self.assertEqual(m.measure_count(), before + 1)

    def test_a_silly_count_is_clamped_to_one(self):
        for count in (0, -5):
            m = per_bar_model()
            before = m.measure_count()
            m.insert_measure(1, count=count)
            self.assertEqual(m.measure_count(), before + 1)

    def test_deleting_more_than_there_is_stops_at_the_end(self):
        m = per_bar_model(bars=6)
        m.delete_measure(0, count=999)
        self.assertGreaterEqual(m.measure_count(), 0)
        self.assertEqual([n for n in m.notes], [])

    def test_deleting_past_the_end_changes_nothing(self):
        m = per_bar_model()
        entries = m.get_beat_entries()
        self.assertEqual(m.delete_measure(m.measure_count() + 3), 0)
        self.assertEqual(m.get_beat_entries(), entries)

    def test_a_big_batch_is_a_single_rewrite(self):
        """插 128 個小節不能是「插 128 次」：那是 O(N×count)，介面會凍住。"""
        m = per_beat_model(bars=200)
        writes = []
        original = type(m)._write_beat_entries
        try:
            type(m)._write_beat_entries = (
                lambda self, beats, mark_precise=False:
                writes.append(len(beats)) or original(self, beats, mark_precise))
            m.insert_measure(0, count=128)
            self.assertEqual(len(writes), 1)
            writes.clear()
            m.delete_measure(0, count=128)
            self.assertEqual(len(writes), 1)
        finally:
            type(m)._write_beat_entries = original


class BulkIsOneUndoStepTests(unittest.TestCase):
    """批次是一步 undo：撤回一次就回到原狀（以前插 16 個要撤 16 次）。"""

    def test_undo_takes_back_the_whole_block(self):
        m = per_bar_model()
        entries = m.get_beat_entries()
        starts = [n.start for n in m.notes]
        m.push_history()
        m.insert_measure(0, count=6)
        self.assertNotEqual(m.get_beat_entries(), entries)
        m.undo()
        self.assertEqual(m.get_beat_entries(), entries)
        self.assertEqual([n.start for n in m.notes], starts)

    def test_undo_restores_deleted_measures_and_notes(self):
        m = per_bar_model()
        entries = m.get_beat_entries()
        starts = [n.start for n in m.notes]
        m.push_history()
        m.delete_measure(0, count=4)
        m.undo()
        self.assertEqual(m.get_beat_entries(), entries)
        self.assertEqual([n.start for n in m.notes], starts)


class CountDialogTests(unittest.TestCase):
    """問「幾個」的對話框：數量／BPM 出得來，刪除的摘要跟著數量走。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, **kw):
        from qt_editor.measure_count_dialog import MeasureCountDialog
        return MeasureCountDialog(None, '標題', '說明', **kw)

    def test_count_defaults_to_one_and_comes_back(self):
        dlg = self._dialog()
        self.assertEqual(dlg.count(), 1)
        self.assertIsNone(dlg.bpm())             # 沒給 bpm 就不顯示那一欄
        dlg._count.setValue(12)
        self.assertEqual(dlg.count(), 12)

    def test_bpm_field_appears_only_when_asked(self):
        dlg = self._dialog(bpm=137.5)
        self.assertAlmostEqual(dlg.bpm(), 137.5, places=2)

    def test_count_is_clamped_to_max(self):
        dlg = self._dialog(count=99, max_count=4)
        self.assertEqual(dlg.count(), 4)

    def test_summary_follows_the_count(self):
        seen = []

        def summary(count):
            seen.append(count)
            return '會刪掉 %d 個' % count

        dlg = self._dialog(summary=summary)
        dlg._count.setValue(5)
        self.assertEqual(dlg._summary.text(), '會刪掉 5 個')
        self.assertIn(5, seen)

    def test_a_broken_summary_does_not_take_the_dialog_down(self):
        dlg = self._dialog(summary=lambda _c: 1 / 0)
        self.assertEqual(dlg._summary.text(), '')


class MainWindowWiringTests(unittest.TestCase):
    """主視窗的三個入口都走批次 API（數量與 BPM 一路傳到 model）。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def _run(self, action, count, model):
        """把對話框換成「直接回傳 count」，再跑一次選單動作。"""
        from unittest import mock

        from PyQt5.QtWidgets import QDialog

        from qt_editor import main_window as MW

        self.win.view.model = model
        stub = mock.Mock()
        stub.exec_.return_value = QDialog.Accepted
        stub.count.return_value = count
        stub.bpm.return_value = 120.0
        with mock.patch.object(MW, 'MeasureCountDialog', return_value=stub):
            action()
        return model

    def test_insert_passes_the_count_through(self):
        m = per_bar_model()
        before = m.measure_count()
        self._run(lambda: self.win.insert_measure_at(0), 7, m)
        self.assertEqual(m.measure_count(), before + 7)

    def test_delete_passes_the_count_through(self):
        m = per_bar_model()
        before = m.measure_count()
        self._run(lambda: self.win.delete_measure_at(1), 5, m)
        self.assertEqual(m.measure_count(), before - 5)

    def test_append_passes_the_count_through(self):
        m = per_bar_model()
        before = m.measure_count()
        self._run(self.win.add_measure_dialog, 9, m)
        self.assertEqual(m.measure_count(), before + 9)

    def test_the_delete_dialog_never_offers_to_empty_the_chart(self):
        """刪光的譜沒有拍點資料，連「新增小節」都會拒絕——至少留一個小節。"""
        from unittest import mock

        from PyQt5.QtWidgets import QDialog

        from qt_editor import main_window as MW

        m = per_bar_model()
        self.win.view.model = m
        total = m.measure_count()
        seen = {}
        stub = mock.Mock()
        stub.exec_.return_value = QDialog.Rejected

        def spy(_parent, _title, _prompt, **kw):
            seen.update(kw)
            return stub

        with mock.patch.object(MW, 'MeasureCountDialog', spy):
            self.win.delete_measure_at(0)
        self.assertEqual(seen['max_count'], total - 1)

    def test_the_status_line_reports_what_really_happened(self):
        # 要求刪 999 個、實際只剩這麼多：回報的是真的刪掉的數量
        m = per_bar_model(bars=6)
        total = m.measure_count()
        self._run(lambda: self.win.delete_measure_at(0), 999, m)
        self.assertEqual(self.win.statusBar().currentMessage().count('999'), 0)
        self.assertIn(str(total - m.measure_count()),
                      self.win.statusBar().currentMessage())

    def test_a_cancelled_dialog_changes_nothing(self):
        from unittest import mock

        from PyQt5.QtWidgets import QDialog

        from qt_editor import main_window as MW

        m = per_bar_model()
        self.win.view.model = m
        entries = m.get_beat_entries()
        stub = mock.Mock()
        stub.exec_.return_value = QDialog.Rejected
        with mock.patch.object(MW, 'MeasureCountDialog', return_value=stub):
            self.win.insert_measure_at(0)
            self.win.delete_measure_at(0)
            self.win.add_measure_dialog()
        self.assertEqual(m.get_beat_entries(), entries)



def tiny_bar_model(numerator=1, denominator=8, beats=64):
    """宣告 1/8 這種拍號的譜：一個小節只有 0.5 個拍單位，比一筆 entry 還短。

    曲庫裡真有這種譜（MIDI 帶進來的拍號），1/4、1/8、3/8 共 19 份。
    """
    m = NoteModel.create_new('t', 120.0, beats * BEAT_MS / 1000.0, numerator)
    m._write_beat_entries([(i * SCALE, i * BEAT_MS) for i in range(beats + 1)],
                          mark_precise=True)
    m._epb_mode = None
    m.beats_per_bar = numerator
    m.time_sig_denominator = denominator
    m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(6)]
    m.rebuild_display_cache()
    return m


class TinyBarTests(unittest.TestCase):
    """使用者回報：加了幾個小節，其他小節在原本位置上下左右浮動。

    原因是 `_measure_index_shape` 的 index 跨度量的是「到下一筆 entry 的距離」。
    小節比一筆 entry 還短時（1/8 = 0.5 個拍單位，而 entry 是一拍一筆），那個距離
    是小節的四倍，於是「插 4 個小節」實際插進 16 個，整份譜的小節被重新切一次。
    實測 felzione（1/8）188 小節變成 204、位移出現 90 種、120 個小節長度被改掉。
    """

    def positions(self, model):
        return [model.get_measure_time_range(i) for i in range(model.count_measures())]

    def test_the_template_is_not_wider_than_the_measure(self):
        m = tiny_bar_model()
        offsets, span = m._measure_index_shape(0)
        bounds = m._measure_unit_bounds(0)
        self.assertIsNotNone(bounds)
        want = int(round((bounds[1] - bounds[0]) * SCALE))
        self.assertEqual(span, want, '跨度要照小節自己的寬度，不是到下一筆 entry')
        self.assertTrue(all(o < span for o in offsets))

    def test_inserting_adds_exactly_that_many_measures(self):
        for numerator, denominator in ((1, 8), (1, 4), (2, 4)):
            with self.subTest(sig='%d/%d' % (numerator, denominator)):
                m = tiny_bar_model(numerator, denominator)
                before = self.positions(m)
                m.insert_measure(0, None, 4)
                m.rebuild_display_cache()
                after = self.positions(m)
                self.assertEqual(len(after), len(before) + 4)

    def test_the_rest_of_the_chart_only_slides(self):
        m = tiny_bar_model()
        before = self.positions(m)
        m.insert_measure(0, None, 4)
        m.rebuild_display_cache()
        after = self.positions(m)
        shifts, lengths = set(), []
        for k, (start, end) in enumerate(before):
            j = k + 4
            if j >= len(after) or start is None or after[j][0] is None:
                continue
            shifts.add(after[j][0] - start)
            lengths.append((after[j][1] - after[j][0]) - (end - start))
        self.assertEqual(len(shifts), 1, '每個小節要往後平移同一個量：%s' % sorted(shifts))
        self.assertEqual(set(lengths), {0}, '原有小節的長度不能被改掉')



class InsertedLengthTests(unittest.TestCase):
    """插進去的空白小節要和隔壁一樣長。

    長度本來是照 BPM 推的，但 index 跨度是照當地格線量的。拍點是八分音符、而
    BPM 說的是四分音符的譜（曲庫裡不少）兩者差一倍：在 felzione 第 94 小節插
    4 個，會塞進 5580ms 的空白，而那裡的小節只有 698ms——插進去的空白小節是
    隔壁的兩倍長。
    """

    def half_speed_model(self, bars=16, bpb=4):
        """拍點間距只有 BPM 所說的一半（entry 是八分音符，BPM 是四分音符）。"""
        m = NoteModel.create_new('t', 120.0, 120.0, bpb)
        half = BEAT_MS // 2
        m._write_beat_entries([(i * SCALE, i * half) for i in range(bars * bpb + 1)],
                              mark_precise=True)
        m._epb_mode = None
        m.notes_tree = [make_note(i * bpb * half, i) for i in range(6)]
        m.rebuild_display_cache()
        return m

    def test_blank_measures_match_the_neighbour(self):
        m = self.half_speed_model()
        at = 4
        start, end = m.get_measure_time_range(at)
        local = end - start
        m.insert_measure(at, None, 3)
        m.rebuild_display_cache()
        for k in range(3):
            a, b = m.get_measure_time_range(at + k)
            self.assertEqual(b - a, local,
                             '第 %d 個插入的小節長度 %d，隔壁是 %d' % (k, b - a, local))

    def test_a_given_bpm_still_wins(self):
        """指定新 BPM 時要照那個 BPM 算——那是使用者明講的。"""
        m = self.half_speed_model()
        at = 4
        local = (lambda r: r[1] - r[0])(m.get_measure_time_range(at))
        m.insert_measure(at, 60.0, 1)
        m.rebuild_display_cache()
        a, b = m.get_measure_time_range(at)
        self.assertNotEqual(b - a, local)
        self.assertGreater(b - a, local, '60 BPM 的小節要比原本長')


class FractionalBarTests(unittest.TestCase):
    """小節是 1.5 個拍單位（3/8）時，插入數量要對。

    以前是把「一個小節」的範本複製 count 次，而 1.5 個單位怎麼取都湊不出整數：
    58 小節的譜插 4 個會變成 63 或 60 個。現在改成整塊算：4 個 1.5 單位的小節
    = 6 個單位 = 6 筆 entry，剛好都踩在拍上。
    """

    def model(self, numerator=3, denominator=8, beats=96):
        m = NoteModel.create_new('t', 120.0, beats * BEAT_MS / 1000.0, numerator)
        m._write_beat_entries([(i * SCALE, i * BEAT_MS) for i in range(beats + 1)],
                              mark_precise=True)
        m._epb_mode = None
        m.beats_per_bar = numerator
        m.time_sig_denominator = denominator
        m.notes_tree = [make_note(i * BEAT_MS, i) for i in range(6)]
        m.rebuild_display_cache()
        return m

    def test_inserting_adds_exactly_that_many(self):
        for count in (1, 2, 4, 5):
            with self.subTest(count=count):
                m = self.model()
                before = m.count_measures()
                m.insert_measure(2, None, count)
                m.rebuild_display_cache()
                self.assertEqual(m.count_measures(), before + count)

    def test_the_grid_stays_on_the_beat(self):
        m = self.model()
        m.insert_measure(2, None, 4)
        m.rebuild_display_cache()
        step = m._beat_index_step()
        offgrid = [idx for idx, _ms in m.get_beat_entries() if idx % step]
        self.assertEqual(offgrid, [], 'entry 的 index 要踩在拍上')



class EndMarkerTests(unittest.TestCase):
    """真實譜面的最後一筆拍點是收尾標記：落在曲末、不在格線上。

    拿最後一個小節當範本就會被它汙染——Cocytus 量出來是
    `([0,1000,2000,2646], 3646)`、Hemisphere 是 `([0,10], 1010)`。實測 343 份譜
    有 95 份「加 3 個小節」的結果是錯的（只加到 2 個、或一次加了 6 個），而且最後
    一個原有小節的長度會被改掉。
    """

    def model(self, bars=12, bpb=4, marker_offset=646):
        """一拍一筆，最後補一筆落在格線外的收尾標記。"""
        m = NoteModel.create_new('t', 120.0, bars * bpb * BEAT_MS / 1000.0, bpb)
        entries = [(i * SCALE, i * BEAT_MS) for i in range(bars * bpb + 1)]
        last_idx, last_ms = entries[-1]
        entries.append((last_idx + marker_offset,
                        last_ms + marker_offset * BEAT_MS // SCALE))
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        m.notes_tree = [make_note(i * bpb * BEAT_MS, i) for i in range(6)]
        m.rebuild_display_cache()
        return m

    def test_the_marker_does_not_become_the_template(self):
        m = self.model()
        total = m.count_measures()
        tail = m.get_measure_at_ms(max(0, int(m.get_beat_entries()[-1][1]) - 1))
        template = m._clean_template_measure(tail)
        step = m._beat_index_step()
        offsets, span = m._measure_index_shape(template)
        self.assertEqual(span % step, 0, '範本的跨度要踩在刻度上')
        self.assertTrue(all(off % step == 0 for off in offsets))

    def test_appending_adds_exactly_that_many(self):
        for count in (1, 3, 5):
            with self.subTest(count=count):
                m = self.model()
                before = m.count_measures()
                m.add_measure(None, count)
                m.rebuild_display_cache()
                self.assertEqual(m.count_measures(), before + count)

    def test_the_last_existing_measure_keeps_its_length(self):
        m = self.model()
        total = m.count_measures()
        was = m.get_measure_time_range(total - 2)
        m.add_measure(None, 3)
        m.rebuild_display_cache()
        self.assertEqual(m.get_measure_time_range(total - 2), was)

    def test_appended_measures_are_full_length(self):
        m = self.model()
        total = m.count_measures()
        typical = (lambda r: r[1] - r[0])(m.get_measure_time_range(total - 2))
        m.add_measure(None, 3)
        m.rebuild_display_cache()
        for k in range(3):
            a, b = m.get_measure_time_range(total + k - 1)
            if b is None or a is None:
                continue
            self.assertAlmostEqual(b - a, typical, delta=2,
                                   msg='第 %d 個接上去的小節長度 %s，隔壁是 %s'
                                       % (k, b - a, typical))



class DeleteOnBeatsTests(unittest.TestCase):
    """刪除的範圍要以**拍點**為界。

    index 的平移量本來就照拍點算，時間卻照小節邊界的內插值算——邊界沒落在拍點上
    時兩者差一點，刪完就有小節位移少 1ms、長度被改掉。1/8 那種小節比一筆拍點還短
    的譜差更多（felzione 刪 3 個小節，188 變 186、119 個小節的長度都變了）。
    """

    def uneven_model(self, bars=16, bpb=4):
        """拍點間距不均勻（947/948 交錯，真實譜面就是這樣）。"""
        m = NoteModel.create_new('t', 190.0, bars * bpb * 0.25, bpb)
        entries, ms = [], 0
        for i in range(bars * bpb + 1):
            entries.append((i * SCALE, ms))
            ms += 237 if i % 2 else 236        # 不均勻
        m._write_beat_entries(entries, mark_precise=True)
        m._epb_mode = None
        m.notes_tree = [make_note(entries[i * bpb][1], i) for i in range(6)]
        m.rebuild_display_cache()
        return m

    def test_the_grid_is_exactly_the_old_one_minus_a_chunk(self):
        m = self.uneven_model()
        before = list(m.get_beat_entries())
        at, count = 4, 3
        cut_from = m._measure_entry_slice(at)[0]
        cut_to = m._measure_entry_slice(at + count - 1)[1]
        m.delete_measure(at, count)
        m.rebuild_display_cache()
        after = list(m.get_beat_entries())
        self.assertEqual(len(after), len(before) - (cut_to - cut_from))
        # 剩下的拍點：前半原樣，後半整段平移（index 與 ms 都是）
        for i in range(cut_from):
            self.assertEqual(after[i], before[i])
        idx_shift = before[cut_to][0] - before[cut_from][0]
        ms_shift = before[cut_to][1] - before[cut_from][1]
        for i in range(cut_from, len(after)):
            src = before[i + (cut_to - cut_from)]
            self.assertEqual(after[i], (src[0] - idx_shift, src[1] - ms_shift))

    def test_measures_after_the_cut_keep_their_length(self):
        m = self.uneven_model()
        at, count = 4, 3
        before = [m.get_measure_time_range(i) for i in range(m.count_measures())]
        m.delete_measure(at, count)
        m.rebuild_display_cache()
        after = [m.get_measure_time_range(i) for i in range(m.count_measures())]
        shifts = set()
        for k in range(at + count, len(before)):
            j = k - count
            if j >= len(after) or before[k][0] is None or after[j][0] is None:
                continue
            shifts.add(before[k][0] - after[j][0])
            self.assertEqual(after[j][1] - after[j][0], before[k][1] - before[k][0])
        self.assertEqual(len(shifts), 1, '刪除點之後要整段平移同一個量：%s' % sorted(shifts))


if __name__ == '__main__':
    unittest.main()
