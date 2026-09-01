import unittest

from qt_editor.models import GNote, NoteModel


def mk(start, end, idx=0):
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.min_key = 4
    n.max_key = 4
    n.pitch = 60
    return n


class ConformToMidiTempoTests(unittest.TestCase):
    def _model(self):
        m = NoteModel()  # root=None → JSON 路徑
        # 舊格線：index 0,1000,2000,3000,4000 對應 time 0,200,400,600,800 (等速)
        m.json_meta = {'beat_timings': [0, 200, 400, 600, 800],
                       'beat_indices': [0, 1000, 2000, 3000, 4000]}
        # 音符落在拍上與拍間
        m.notes_tree = [mk(0, 50), mk(200, 250), mk(300, 350), mk(800, 850)]
        m.music_end_ms = 800.0
        m.rebuild_display_cache()
        return m

    def test_uniform_stretch(self):
        # 新 MIDI：同 index，但 time 是 2 倍(每拍 400ms) → 所有音符時間 ×2
        m = self._model()
        new = [(0, 0), (1000, 400), (2000, 800), (3000, 1200), (4000, 1600)]
        m.conform_to_midi_tempo(new)
        got = [(n.start, n.end) for n in m.notes_tree]
        self.assertEqual(got, [(0, 100), (400, 500), (600, 700), (1600, 1700)])
        # beat_data 換成新節奏
        self.assertEqual([ms for _, ms in m.get_beat_entries()], [0, 400, 800, 1200, 1600])

    def test_variable_tempo_warp(self):
        # 新 MIDI：前半正常(每拍200)、後半變慢(每拍400) → 變速
        m = self._model()
        new = [(0, 0), (1000, 200), (2000, 400), (3000, 800), (4000, 1200)]
        m.conform_to_midi_tempo(new)
        got = [n.start for n in m.notes_tree]
        # 0→0, 200(index1000)→200, 300(index1500,拍間)→ 200+0.5*(800-400)?
        # index1500 介於 new index1000(200) 與 2000(400) → 200+0.5*200=300
        # 800(index4000)→1200
        self.assertEqual(got, [0, 200, 300, 1200])

    def test_note_stays_on_beat(self):
        # 落在拍上的音符，warp 後仍落在對應新拍上
        m = self._model()
        new = [(0, 0), (1000, 333), (2000, 666), (3000, 999), (4000, 1332)]
        m.conform_to_midi_tempo(new)
        starts = [n.start for n in m.notes_tree]
        self.assertEqual(starts[0], 0)     # index0
        self.assertEqual(starts[1], 333)   # index1000 → 新拍1
        self.assertEqual(starts[3], 1332)  # index4000 → 新拍4


if __name__ == '__main__':
    unittest.main()
