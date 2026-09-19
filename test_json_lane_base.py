"""JSON 的鍵道從 0 開始（遊戲的格式），存檔再開不能移動。

以前讀 JSON 沿用 XML 的猜法：最小鍵道 >= 1 就當成 1 開始、整份減 1。沒用到最
左邊那一格的譜（曲庫 Exhale 三個難度、10 份新手教學）一開檔就左移一格，每存一次
再開又移一格。發現的經過：顫音的鍵道 10-14 存兩次變成 8-12。
"""

import json
import os
import shutil
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel


def chart(lanes, note_type=0):
    m = NoteModel.create_new('t', 120.0, 10.0, 4)
    notes = []
    for i, (lo, hi) in enumerate(lanes):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 300, i * 300 + 100, 100
        n.pitch, n.hand, n.note_type = 60, 0, note_type
        n.min_key, n.max_key = lo, hi
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class JsonLaneBaseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def roundtrip(self, m, times=3):
        for k in range(times):
            path = os.path.join(self.dir, '%d.json' % k)
            m.save_json(path)
            m = NoteModel()
            m.load_json(path)
        return [(n.min_key, n.max_key) for n in m.notes_tree]

    def test_lanes_stay_put_when_lane_0_is_unused(self):
        self.assertEqual(self.roundtrip(chart([(3, 5), (10, 12), (18, 20)])),
                         [(3, 5), (10, 12), (18, 20)])

    def test_a_trill_keeps_its_lanes(self):
        self.assertEqual(self.roundtrip(chart([(10, 14)], note_type=64)), [(10, 14)])

    def test_game_json_is_read_as_is(self):
        path = os.path.join(self.dir, 'game.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'bpm': 120, 'notes': [
                {'startTime': 0, 'endTime': 100, 'startLane': 2, 'endLane': 4, 'note_type': 0, 'hand': 0},
                {'startTime': 500, 'endTime': 600, 'startLane': 20, 'endLane': 22, 'note_type': 0, 'hand': 1},
            ]}, fh)
        m = NoteModel()
        m.load_json(path)
        self.assertEqual([(n.min_key, n.max_key) for n in m.notes_tree], [(2, 4), (20, 22)])

    def test_old_one_based_json_is_still_converted(self):
        # 出現第 28 格只可能是 1 開始的舊檔（曲庫的 birth_of_devil Real）
        path = os.path.join(self.dir, 'old.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'bpm': 120, 'notes': [
                {'startTime': 0, 'endTime': 100, 'startLane': 1, 'endLane': 3, 'note_type': 0, 'hand': 0},
                {'startTime': 500, 'endTime': 600, 'startLane': 26, 'endLane': 28, 'note_type': 0, 'hand': 1},
            ]}, fh)
        m = NoteModel()
        m.load_json(path)
        self.assertEqual([(n.min_key, n.max_key) for n in m.notes_tree], [(0, 2), (25, 27)])

    def test_an_explicit_base_wins(self):
        path = os.path.join(self.dir, 'explicit.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'bpm': 120, 'lane_index_base': 1, 'notes': [
                {'startTime': 0, 'endTime': 100, 'startLane': 5, 'endLane': 7, 'note_type': 0, 'hand': 0},
            ]}, fh)
        m = NoteModel()
        m.load_json(path)
        self.assertEqual([(n.min_key, n.max_key) for n in m.notes_tree], [(4, 6)])


if __name__ == '__main__':
    unittest.main()
