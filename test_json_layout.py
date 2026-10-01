# -*- coding: utf-8 -*-
"""譜面 JSON 的版面：要能用文字編輯器直接改。

使用者回報「存出來的 json 完全沒整理，很難改」。`json.dump(indent=2)` 把每個
數字排成一行，一份 695 顆音符的譜是 13559 行，鍵的順序還是隨機的。
"""

import json
import os
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import json_layout
from qt_editor.models import NoteModel

_app = QApplication.instance() or QApplication([])


def note(start, **extra):
    d = {'startTime': start, 'endTime': start + 100, 'gateTime': 100,
         'startLane': 3, 'endLane': 5, 'pitch': 60, 'type': 'tap',
         'note_type': 0, 'hand': 0}
    d.update(extra)
    return d


class LayoutTests(unittest.TestCase):
    def lines(self, data):
        return json_layout.dumps(data).splitlines()

    def test_one_note_per_line(self):
        """最重要的一條：一顆音符一行，才看得到幾十顆、也才能整行刪。"""
        text = json_layout.dumps({'notes': [note(0), note(100), note(200)]})
        rows = [x for x in text.splitlines() if x.lstrip().startswith('{"')]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertTrue(row.strip().endswith(('},', '}')), row)
            self.assertIn('"startTime"', row)

    def test_a_note_too_long_for_one_line_is_expanded(self):
        """帶一堆 sub_note 的顫音攤成一行會有好幾千字元，那種才展開。"""
        fat = note(0, subNotes=[{'start_timing_msec': i, 'end_timing_msec': i + 10,
                                 'scale_piano': 40, 'velocity': 80, 'track_index': 1}
                                for i in range(40)])
        text = json_layout.dumps({'notes': [fat]})
        self.assertTrue(max(len(x) for x in text.splitlines()) < 2000,
                        '超長的音符要展開，不是硬塞成一行')
        self.assertEqual(json.loads(text)['notes'][0], fat)

    def test_scalar_arrays_are_paragraphs_not_one_number_per_line(self):
        data = {'beat_timings': [i * 333 for i in range(60)]}
        rows = self.lines(data)
        self.assertLess(len(rows), 12, '60 個時間點不該佔 60 行')
        for row in rows:
            self.assertLessEqual(len(row), json_layout.WIDTH, row)

    def test_a_long_key_still_keeps_the_line_within_the_width(self):
        """寬度要算進鍵名：只量陣列本身的話 beat_indices 那行會超出去。"""
        data = {'beat_indices': list(range(24))}
        for row in self.lines(data):
            self.assertLessEqual(len(row), json_layout.WIDTH, row)

    def test_short_arrays_stay_on_one_line(self):
        text = json_layout.dumps({'beat_timings': [0, 500, 1000]})
        self.assertIn('"beat_timings": [0, 500, 1000]', text)

    # ── 鍵的順序 ──────────────────────────────────────────────────
    def test_the_song_settings_come_first_and_the_notes_last(self):
        data = {'notes': [note(0)], 'time_signature': '4/4', 'bpm': 120.0,
                'beat_timings': [0], 'pitch_encoding': 'midi'}
        keys = [x.strip().split('"')[1] for x in self.lines(data)
                if x.startswith('  "')]
        self.assertEqual(keys[0], 'bpm')
        self.assertEqual(keys[-1], 'notes')
        self.assertLess(keys.index('time_signature'), keys.index('beat_timings'))

    def test_groups_are_separated_by_a_blank_line(self):
        data = {'bpm': 120.0, 'time_signature': '4/4', 'notes': [note(0)]}
        self.assertIn('', self.lines(data), '組和組之間要空一行')
        self.assertEqual(json.loads(json_layout.dumps(data)), data,
                         '空行是 JSON 的空白，解析器讀得回來')

    def test_an_unknown_key_is_kept_and_lands_before_the_notes(self):
        data = {'notes': [note(0)], '我自己加的': 1, 'bpm': 120.0}
        keys = [x.strip().split('"')[1] for x in self.lines(data) if x.startswith('  "')]
        self.assertEqual(keys, ['bpm', '我自己加的', 'notes'])

    # ── 內容不能變 ────────────────────────────────────────────────
    def test_everything_survives_the_round_trip(self):
        data = {
            'bpm': 120.5,
            'title': '引號 " 反斜線 \\ 換行 \n 都要跳脫',
            'empty_list': [], 'empty_dict': {},
            'nested': {'a': [1, 2, {'b': None, 'c': True}]},
            'notes': [note(0), note(100, hidden=True, velocity=None)],
        }
        self.assertEqual(json.loads(json_layout.dumps(data)), data)

    def test_an_empty_chart_is_still_valid_json(self):
        self.assertEqual(json.loads(json_layout.dumps({})), {})

    def test_it_ends_with_a_newline(self):
        self.assertTrue(json_layout.dumps({'bpm': 1}).endswith('}\n'))


class SaveJsonUsesTheLayoutTests(unittest.TestCase):
    """真的接到存檔上——版面模組對了但沒接上去的話這裡會抓到。"""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'chart.json')
        self.model = NoteModel.create_new('t', 120.0, 30.0, 4)
        from qt_editor.models import GNote
        for i in range(40):
            n = GNote(None, i)
            n.start, n.end, n.gate = i * 250, i * 250 + 100, 100
            n.min_key, n.max_key, n.pitch, n.note_type = 3, 5, 60, 0
            self.model.notes_tree.append(n)
        self.model.rebuild_display_cache()

    def test_a_forty_note_chart_is_not_hundreds_of_lines(self):
        self.model.save_json(self.path)
        with open(self.path, encoding='utf-8') as fh:
            rows = fh.read().splitlines()
        self.assertLess(len(rows), 80, '40 顆音符不該存成幾百行')
        notes = [x for x in rows if x.lstrip().startswith('{"startTime"')]
        self.assertEqual(len(notes), 40, '一顆音符一行')

    def test_saving_and_loading_keeps_every_note(self):
        self.model.save_json(self.path)
        back = NoteModel()
        back.load_json(self.path)
        back.rebuild_display_cache()
        self.assertEqual([(n.start, n.end, n.min_key, n.pitch) for n in back.notes_tree],
                         [(n.start, n.end, n.min_key, n.pitch)
                          for n in self.model.notes_tree])

    def test_the_file_starts_with_the_song_settings(self):
        self.model.save_json(self.path)
        with open(self.path, encoding='utf-8') as fh:
            second = fh.read().splitlines()[1]
        self.assertIn('"bpm"', second, '打開檔案第一眼要看到 BPM')


if __name__ == '__main__':
    unittest.main()
