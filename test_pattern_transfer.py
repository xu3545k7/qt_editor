"""排列分布：複製一段的鍵道配置，套到另一段「有一點點不同」的段落。"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import pattern_transfer as PT
from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])

PHRASE = [  # (相對 ms, 音高, 手, min_key)
    (0, 60, 0, 10), (250, 64, 0, 12), (500, 67, 0, 14), (750, 72, 0, 18),
    (1000, 48, 1, 2), (1250, 52, 1, 4), (1500, 55, 1, 6), (1750, 60, 0, 9),
]


def _note(idx, start, pitch, hand=0, key=20, note_type=0):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 120, 120
    n.pitch, n.velocity, n.hand = pitch, 90, hand
    n.min_key, n.max_key, n.note_type = key, key + 2, note_type
    return n


def _model(notes):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def _source(base=1000):
    return [_note(i, base + t, p, h, k) for i, (t, p, h, k) in enumerate(PHRASE)]


class PatternTransferTests(unittest.TestCase):
    def test_identical_phrase_copies_lanes_and_hands(self):
        src = _source()
        tgt = [_note(100 + i, 20000 + t, p) for i, (t, p, _h, _k) in enumerate(PHRASE)]
        dist = PT.capture(src)
        result = PT.match(dist, tgt)
        self.assertGreater(result.similarity, 0.95)
        m = _model(src + tgt)
        PT.apply(m, result)
        for n, (_t, _p, h, k) in zip(tgt, PHRASE):
            self.assertEqual((n.min_key, n.max_key, n.hand), (k, k + 2, h))
        self.assertEqual(result.unmatched, [])

    def test_slightly_different_phrase_still_matches(self):
        # 少一顆、多一顆、時間抖一點、整段移調 +2
        tgt = []
        for i, (t, p, _h, _k) in enumerate(PHRASE):
            if i == 3:
                continue
            tgt.append(_note(100 + i, 20000 + t + (15 if i % 2 else -10), p + 2))
        extra = _note(199, 20000 + 1125, 90)
        tgt.append(extra)
        result = PT.match(PT.capture(_source()), tgt)
        self.assertEqual(result.transpose, 2)
        self.assertEqual(len(result.pairs), 7)
        self.assertIn(extra, result.unmatched)
        self.assertGreater(result.similarity, 0.6)
        PT.apply(_model(_source() + tgt), result)
        self.assertEqual(tgt[4].min_key, PHRASE[5][3])   # 第 6 顆（跳過了第 4 顆）
        self.assertEqual(extra.min_key, 20)              # 沒對上的不動

    def test_slower_phrase_uses_scale(self):
        tgt = [_note(100 + i, 20000 + int(t * 1.15), p) for i, (t, p, _h, _k) in enumerate(PHRASE)]
        result = PT.match(PT.capture(_source()), tgt)
        self.assertAlmostEqual(result.scale, 1.15, places=2)
        self.assertEqual(len(result.pairs), len(PHRASE))

    def test_unrelated_phrase_is_low_similarity(self):
        tgt = [_note(100 + i, 20000 + i * 400, 30 + i * 7) for i in range(8)]
        result = PT.match(PT.capture(_source()), tgt)
        self.assertLess(result.similarity, 0.3)

    def test_trills_are_ignored(self):
        src = _source() + [_note(50, 1100, 70, note_type=0x40)]
        self.assertEqual(len(PT.capture(src)), len(PHRASE))


class PatternMenuTests(unittest.TestCase):
    def setUp(self):
        ChartView._pattern_clipboard = None
        self.src = _source()
        self.tgt = [_note(100 + i, 20000 + t, p) for i, (t, p, _h, _k) in enumerate(PHRASE)]
        self.tgt.append(_note(199, 20000 + 1125, 95))
        self.model = _model(self.src + self.tgt)
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.view.load_model(self.model)
        self.view.rebuild_mapper()

    def tearDown(self):
        ChartView._pattern_clipboard = None

    def test_copy_apply_undo(self):
        self.view.selected = {n.idx for n in self.src}
        self.view.copy_distribution_selected()
        self.assertIsNotNone(ChartView._pattern_clipboard)
        self.view.selected = {n.idx for n in self.tgt}
        preview = self.view.distribution_preview()
        self.assertGreater(preview.similarity, 0.8)
        self.view.apply_distribution_selected()
        self.assertEqual(self.tgt[0].min_key, PHRASE[0][3])
        self.assertEqual(self.tgt[4].hand, 1)
        # 沒對上的那顆被選起來
        self.assertEqual(self.view.selected, {self.tgt[-1].idx})
        first_idx = self.tgt[0].idx
        self.model.undo()
        restored = {n.idx: n for n in self.model.notes_tree}
        self.assertEqual(restored[first_idx].min_key, 20)

    def test_menu_entries(self):
        from PyQt5.QtWidgets import QMenu
        self.view.selected = {n.idx for n in self.src}
        self.view.copy_distribution_selected()
        self.view.selected = {n.idx for n in self.tgt}
        menu = QMenu()
        self.view._ctx_build_structure_menu(menu, True, True)
        labels = [a.text() for a in menu.actions()]
        self.assertIn('複製排列分布', labels)
        self.assertTrue(any(t.startswith('套用排列分布（相似度') for t in labels))


if __name__ == '__main__':
    unittest.main()
