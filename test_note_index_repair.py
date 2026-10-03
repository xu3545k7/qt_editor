# -*- coding: utf-8 -*-
"""音符編號壞掉時，存 JSON 要順手修好。

XML 與 Hiraeth ZIP 本來就在 `build_pan_xml` 裡修（齊全又唯一就沿用，否則重編），
只有 JSON 這條原樣寫出去。而**遊戲讀的是 JSON**：`ExternalSongLibrary` 讀
param1/param2、`JudgmentManager` 用 `slideNotesByIndex.TryGetValue(param1)` 串鏈、
`NoteController` 比 `candidate.index != param2` 找下一節——編號壞掉滑奏就斷。
"""

import json
import os
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])

SLIDE = 4


def note(start, pitch=60, note_type=0, idx=0, index=None, lane=4,
         param1=-1, param2=-1):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 100
    n.gate = 100
    n.pitch = pitch
    n.note_type = note_type
    n.hand = 0
    n.min_key = lane
    n.max_key = lane + 2
    n.note_index = index
    n.param1 = param1
    n.param2 = param2
    return n


def model_with(notes):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class LeaveGoodNumbersAloneTests(unittest.TestCase):
    def test_complete_and_unique_numbers_are_kept(self):
        """官譜的編號不從 0 開始也不連號，那是原檔的資訊。"""
        notes = [note(i * 200, index=901 + i * 2, idx=i) for i in range(4)]
        m = model_with(notes)
        self.assertEqual(m.repair_note_indices(), 0)
        self.assertEqual([n.note_index for n in notes], [901, 903, 905, 907])

    def test_a_chart_without_any_numbers_is_left_alone(self):
        notes = [note(i * 200, idx=i) for i in range(4)]
        m = model_with(notes)
        self.assertEqual(m.repair_note_indices(), 0)
        self.assertEqual([n.note_index for n in notes], [None] * 4)


class RepairTests(unittest.TestCase):
    def test_half_numbered_charts_get_renumbered(self):
        """有些有、有些沒有：param1/param2 會指到不存在的編號。"""
        notes = [note(0, index=5, idx=0), note(200, idx=1),
                 note(400, index=7, idx=2)]
        m = model_with(notes)
        self.assertEqual(m.repair_note_indices(), 3)
        self.assertEqual([n.note_index for n in notes], [0, 1, 2])

    def test_duplicate_numbers_get_renumbered(self):
        notes = [note(0, index=3, idx=0), note(200, index=3, idx=1),
                 note(400, index=9, idx=2)]
        m = model_with(notes)
        self.assertGreater(m.repair_note_indices(), 0)
        self.assertEqual(sorted(n.note_index for n in notes), [0, 1, 2])

    def test_the_chain_survives_the_renumbering(self):
        """重編之前要先用**原**編號把鏈結解析出來，不然會全部變成鏈尾。"""
        # 原編號 100/101/102 串成一條，但 102 和別人重複 -> 觸發重編
        notes = [note(i * 120, pitch=60 + i, note_type=SLIDE, idx=i,
                      index=100 + i, lane=4 + i,
                      param1=(99 + i if i else -1),
                      param2=(101 + i if i < 2 else -1))
                 for i in range(3)]
        notes.append(note(500, pitch=70, idx=3, index=102))   # 和第三顆撞號
        m = model_with(notes)
        self.assertGreater(m.repair_note_indices(), 0)
        self.assertEqual([n.note_index for n in notes], [0, 1, 2, 3])

        have = {n.note_index for n in notes}
        for n in notes:
            for value in (n.param1, n.param2):
                if value >= 0:
                    self.assertIn(value, have, '鏈結指到不存在的編號')
        chain = [(n.note_index, n.param1, n.param2) for n in notes[:3]]
        self.assertEqual(chain, [(0, -1, 1), (1, 0, 2), (2, 1, -1)],
                         '三顆要還是串成一條，頭尾是 -1')


class SaveJsonTests(unittest.TestCase):
    def _save(self, m):
        path = os.path.join(tempfile.mkdtemp(), 'x.json')
        m.save_json(path)
        with open(path, encoding='utf-8') as fh:
            return json.load(fh), path

    def test_saving_repairs_a_broken_chart(self):
        notes = [note(0, index=5, idx=0), note(200, idx=1), note(400, index=5, idx=2)]
        raw, _ = self._save(model_with(notes))
        ids = [n['index'] for n in raw['notes']]
        self.assertEqual(sorted(ids), [0, 1, 2])

    def test_saving_keeps_official_numbering(self):
        notes = [note(i * 200, index=901 + i * 2, idx=i) for i in range(4)]
        raw, _ = self._save(model_with(notes))
        self.assertEqual([n['index'] for n in raw['notes']], [901, 903, 905, 907])

    def test_saving_twice_is_stable(self):
        notes = [note(0, index=5, idx=0), note(200, idx=1), note(400, index=9, idx=2)]
        m = model_with(notes)
        first, path = self._save(m)
        back = NoteModel()
        back.load_json(path)
        back.rebuild_display_cache()
        second, _ = self._save(back)
        self.assertEqual([n['index'] for n in first['notes']],
                         [n['index'] for n in second['notes']])


if __name__ == '__main__':
    unittest.main()
