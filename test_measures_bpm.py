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
