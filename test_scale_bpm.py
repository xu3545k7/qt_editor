import unittest

from qt_editor.models import GNote, NoteModel


def make_note(start, end, idx=0, nt=0):
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.note_type = nt
    n.min_key = 4
    n.max_key = 4
    return n


class ScaleAllTimeTests(unittest.TestCase):
    def _model(self):
        m = NoteModel()
        # 音符落在 beat 上：500ms(beat1)、1000ms(beat2)
        m.notes_tree = [make_note(500, 700, 0), make_note(1000, 1000, 1)]
        m.json_meta = {'beat_timings': [0, 500, 1000, 1500]}
        m.music_end_ms = 2000.0
        m.time_sig_changes = [(0, 4, 4), (1000, 3, 4)]
        m.rebuild_display_cache()
        return m

    def test_scale_half_keeps_note_on_beat(self):
        m = self._model()
        # bpm 120 -> 240：factor = 120/240 = 0.5
        changed = m.scale_all_time(0.5)
        self.assertEqual(changed, 2)
        # 音符時間減半
        self.assertEqual((m.notes_tree[0].start, m.notes_tree[0].end), (250, 350))
        self.assertEqual(m.notes_tree[0].gate, 100)
        self.assertEqual(m.notes_tree[1].start, 500)
        # beat_timings 也減半 → 音符 500→250 仍落在 beat(原500→250) 上
        self.assertEqual(m.json_meta['beat_timings'], [0, 250, 500, 750])
        # music_end / 拍號時間一起縮放
        self.assertEqual(m.music_end_ms, 1000.0)
        self.assertEqual(m.time_sig_changes, [(0, 4, 4), (500, 3, 4)])

    def test_scale_up_lengthens(self):
        m = self._model()
        # bpm 120 -> 60：factor = 2.0
        m.scale_all_time(2.0)
        self.assertEqual(m.notes_tree[0].start, 1000)
        self.assertEqual(m.json_meta['beat_timings'], [0, 1000, 2000, 3000])

    def test_note_stays_aligned_to_its_beat(self):
        # 任意 factor 下，落在 beat 上的音符縮放後仍與該 beat 對齊
        for f in (0.37, 1.6, 0.5, 2.0):
            m = self._model()
            note_start = m.notes_tree[1].start  # 1000, 落在 beat index 2
            m.scale_all_time(f)
            self.assertEqual(m.notes_tree[1].start, round(note_start * f))
            self.assertEqual(m.json_meta['beat_timings'][2], round(1000 * f))
            # 兩者相等 → 仍對齊
            self.assertEqual(m.notes_tree[1].start, m.json_meta['beat_timings'][2])

    def test_noop_factors(self):
        m = self._model()
        self.assertEqual(m.scale_all_time(1.0), 0)
        self.assertEqual(m.scale_all_time(0.0), 0)
        self.assertEqual(m.notes_tree[0].start, 500)


if __name__ == '__main__':
    unittest.main()
