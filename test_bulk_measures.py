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


if __name__ == '__main__':
    unittest.main()
