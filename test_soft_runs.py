"""連續的 soft：斜的轉滑奏、垂直的轉顫音（輸出 Hiraeth 歌曲包用）。"""

import unittest

from qt_editor.models import GNote, NoteModel, note_is_slide, note_is_trill
from qt_editor import soft_runs as SR


def soft(idx, start, lane, pitch=60, hand=0, width=3, kind=1):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 40, 40
    n.pitch, n.velocity, n.hand, n.note_type = pitch, 80, hand, kind
    n.max_key = lane
    n.min_key = lane - width + 1
    return n


def model(notes):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class SoftRunTests(unittest.TestCase):
    def test_diagonal_run_becomes_a_slide_chain(self):
        notes = [soft(i, 1000 + i * 20, 10 + i, 60 + i) for i in range(6)]
        m = model(notes)
        r = SR.convert_soft_runs(m)
        self.assertEqual((r.slide_chains, r.slide_notes, r.trills), (1, 6, 0))
        chain = sorted(m.notes_tree, key=lambda n: n.start)
        self.assertTrue(all(note_is_slide(n.note_type) for n in chain))
        self.assertEqual(chain[0].param1, -1)
        self.assertEqual(chain[-1].param2, -1)
        self.assertEqual(chain[1].param1, chain[0].note_index)

    def test_back_and_forth_becomes_a_trill(self):
        # 八度震音：兩個位置來回，左右差 5 格
        notes = [soft(i, 1000 + i * 45, 12 if i % 2 == 0 else 17, 48 if i % 2 == 0 else 60) for i in range(8)]
        m = model(notes)
        r = SR.convert_soft_runs(m)
        self.assertEqual((r.trills, r.trill_notes, r.slide_chains), (1, 8, 0))
        self.assertEqual(len(m.notes_tree), 1)
        self.assertTrue(note_is_trill(m.notes_tree[0].note_type))

    def test_staying_in_place_is_a_trill(self):
        notes = [soft(i, 1000 + i * 60, 15, 60 + (i % 2)) for i in range(5)]
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual(r.trills, 1)

    def test_glissando_starting_flat_is_not_a_trill(self):
        lanes = [15, 15, 15, 16, 17, 18, 19]
        notes = [soft(i, 1000 + i * 15, lane, 60 + i) for i, lane in enumerate(lanes)]
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual((r.trills, r.slide_chains, r.slide_notes), (0, 1, 7))

    def test_width_changes_do_not_fake_a_zigzag(self):
        # 一路往右，寬度 3/2 交替（右緣一直往右）→ 還是滑奏
        notes = [soft(i, 1000 + i * 15, 10 + i, 60 + i, width=3 if i % 2 else 2) for i in range(6)]
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual((r.slide_chains, r.trills), (1, 0))

    def test_octave_doubled_glissando_makes_two_chains(self):
        notes = []
        for i in range(5):
            notes.append(soft(2 * i, 1000 + i * 20, 5 + i, 48 + i))
            notes.append(soft(2 * i + 1, 1000 + i * 20, 15 + i, 60 + i))
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual((r.slide_chains, r.slide_notes), (2, 10))

    def test_slow_or_single_softs_stay(self):
        m = model([soft(0, 1000, 10), soft(1, 3000, 12), soft(2, 3500, 14)])
        r = SR.convert_soft_runs(m)
        self.assertEqual((r.slide_chains, r.trills), (0, 0))
        self.assertTrue(all(int(n.note_type) == 1 for n in m.notes_tree))

    def test_other_note_types_break_the_run(self):
        notes = [soft(0, 1000, 10), soft(1, 1020, 11), soft(2, 1040, 12, kind=0), soft(3, 1060, 13)]
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual(r.slide_notes, 2)          # 只有前兩顆接得起來

    def test_hidden_softs_are_left_alone(self):
        notes = [soft(i, 1000 + i * 20, 10 + i) for i in range(5)]
        for n in notes:
            n.hidden = True
        r = SR.convert_soft_runs(model(notes))
        self.assertEqual((r.slide_chains, r.trills), (0, 0))


if __name__ == '__main__':
    unittest.main()
