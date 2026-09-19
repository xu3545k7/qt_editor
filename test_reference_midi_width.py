# -*- coding: utf-8 -*-
"""以參考 MIDI 重建：音符寬度不能因為譜面沒有音高就塌成 1。

`rebuild_from_reference_midi` 是從目前譜面學「音高 → 鍵道位置」的局部分布，
再照參考 MIDI 重擺。學習樣本必須有音高（回歸要用），但**寬度不需要**——它就是
`max_key - min_key + 1`，每顆音符都有。

以前寬度跟著樣本一起被丟掉：沒有音高的譜面樣本全空，`default_width` 退回 1，
於是重建出來的每一顆都是寬度 1。而「以參考 MIDI 重建」正是音高壞掉時才會用的
工具，所以這個洞每次都會踩到。實測鬼火 3474 顆從 `{2:967, 3:2507}` 變成
`{1: 3473}`。
"""

import unittest

from qt_editor.models import DEFAULT_NOTE_LANE_WIDTH, GNote, NoteModel


def chart(width=3, count=40):
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    notes = []
    for i in range(count):
        n = GNote(None, i)
        n.start, n.end, n.gate = i * 250, i * 250 + 120, 120
        n.min_key = 4 + (i % 6)
        n.max_key = n.min_key + width - 1
        n.note_type, n.hand = 0, i % 2
        n.pitch, n.velocity = 55 + (i % 20), 90
        n.track, n.channel = i % 2, 0
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def reference(count=40):
    return [{'start_timing_msec': i * 250,
             'end_timing_msec': i * 250 + 120,
             'pitch': 55 + (i % 20),
             'track': i % 2}
            for i in range(count)]


def widths(model):
    counts = {}
    for note in model.notes_tree:
        w = abs(int(note.max_key) - int(note.min_key)) + 1
        counts[w] = counts.get(w, 0) + 1
    return counts


class KeepLaneTests(unittest.TestCase):
    """同一顆音要留在原本的鍵道上。

    這個工具的說明寫「音高/時間照 MIDI、**左右照原譜**」，但它其實是從原譜學
    一條「音高→鍵道」的區域回歸、再重算每一顆的位置——手擺好的位置會被趨勢
    預測值取代。實測拿原本的 MIDI 回來重建，3473 顆有 100% 是 (時間, 音高)
    完全相同的同一顆音，卻整批被搬走了。
    """

    def lanes_of(self, model):
        out = {}
        for note in model.notes_tree:
            out.setdefault((int(note.start), int(note.pitch)), []).append(
                (int(note.min_key), int(note.max_key)))
        return out

    def test_matching_notes_do_not_move(self):
        m = chart(width=3)
        before = self.lanes_of(m)
        m.rebuild_from_reference_midi(reference())
        after = self.lanes_of(m)
        self.assertEqual(after, before, '同一顆音被搬走了')

    def test_a_hand_placed_lane_survives(self):
        """使用者手擺的位置，即使偏離整體趨勢也要留著。"""
        m = chart(width=3)
        odd = m.notes_tree[7]
        odd.min_key, odd.max_key = 22, 24      # 刻意擺在趨勢之外
        key = (int(odd.start), int(odd.pitch))
        m.rebuild_from_reference_midi(reference())
        got = [n for n in m.notes_tree
               if (int(n.start), int(n.pitch)) == key]
        self.assertEqual([(int(n.min_key), int(n.max_key)) for n in got],
                         [(22, 24)])

    def test_new_notes_still_get_a_lane(self):
        """MIDI 有、原譜沒有的音仍然要靠回歸擺位置。"""
        m = chart(width=3, count=20)
        extra = reference(count=20) + [{
            'start_timing_msec': 9000, 'end_timing_msec': 9100,
            'pitch': 70, 'track': 0}]
        m.rebuild_from_reference_midi(extra)
        fresh = [n for n in m.notes_tree if int(n.start) == 9000]
        self.assertEqual(len(fresh), 1)
        self.assertGreaterEqual(int(fresh[0].min_key), 0)
        self.assertLess(int(fresh[0].max_key), 28)

    def test_duplicates_are_consumed_in_order(self):
        """同一個 (時間, 音高) 出現兩次時，兩個原位各配一顆。"""
        m = chart(width=3, count=4)
        m.notes_tree[1].start = m.notes_tree[0].start
        m.notes_tree[1].pitch = m.notes_tree[0].pitch
        m.notes_tree[0].min_key, m.notes_tree[0].max_key = 2, 4
        m.notes_tree[1].min_key, m.notes_tree[1].max_key = 20, 22
        ref = [{'start_timing_msec': int(n.start),
                'end_timing_msec': int(n.end),
                'pitch': int(n.pitch), 'track': 0} for n in m.notes_tree]
        m.rebuild_from_reference_midi(ref)
        got = sorted((int(n.min_key), int(n.max_key)) for n in m.notes_tree
                     if int(n.start) == ref[0]['start_timing_msec'])
        self.assertEqual(got, [(2, 4), (20, 22)])

    def test_a_chart_without_pitch_keeps_its_lanes(self):
        """沒有音高時用「起音時間＋順位」配，鍵道一樣要留住。

        這個工具正是為了「音高壞掉的譜面」存在的，用 (時間, 音高) 當鑰匙在那種
        譜面上一顆都配不到——實測 La Campanella 的 4118 顆全部被重擺。
        """
        m = chart(width=4)
        before = [(int(n.start), int(n.min_key), int(n.max_key))
                  for n in m.notes_tree]
        for note in m.notes_tree:
            note.pitch = None
        m.rebuild_from_reference_midi(reference())
        after = [(int(n.start), int(n.min_key), int(n.max_key))
                 for n in m.notes_tree]
        self.assertEqual(after, before, '沒有音高就把鍵道全搬走了')
        self.assertEqual(set(widths(m)), {4})

    def test_everything_else_survives_too(self):
        """以前是每一顆都造新的 GNote，力度和 note_index 100% 被清掉、
        note_type 照長度重算，把人標的長押／滑音蓋掉。"""
        m = chart(width=3)
        for i, note in enumerate(m.notes_tree):
            note.velocity = 40 + i
            note.note_index = 100 + i
            note.note_type = 4 if i % 3 == 0 else 0
        before = [(n.velocity, n.note_index, n.note_type) for n in m.notes_tree]
        m.rebuild_from_reference_midi(reference())
        after = [(n.velocity, n.note_index, n.note_type) for n in m.notes_tree]
        self.assertEqual(after, before)


class WidthTests(unittest.TestCase):
    def test_a_pitched_chart_keeps_its_width(self):
        m = chart(width=3)
        m.rebuild_from_reference_midi(reference())
        self.assertEqual(set(widths(m)), {3})

    def test_a_chart_without_pitch_keeps_its_width(self):
        """這是壞掉的那個情形——重建工具正是音高壞掉時才會用的。"""
        m = chart(width=3)
        for note in m.notes_tree:
            note.pitch = None
        m.rebuild_from_reference_midi(reference())
        self.assertNotIn(1, widths(m), '沒有音高就把每一顆都變成寬度 1 了')
        self.assertEqual(set(widths(m)), {3})

    def test_a_wider_chart_without_pitch_keeps_its_width(self):
        m = chart(width=5)
        for note in m.notes_tree:
            note.pitch = None
        m.rebuild_from_reference_midi(reference())
        self.assertEqual(set(widths(m)), {5})

    def test_partial_pitch_data_still_learns_from_everything(self):
        """只有幾顆有音高時，寬度仍該取自全部音符。"""
        m = chart(width=4)
        for note in m.notes_tree[3:]:
            note.pitch = None
        m.rebuild_from_reference_midi(reference())
        self.assertEqual(set(widths(m)), {4})

    def test_an_empty_chart_uses_the_corpus_default(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = []
        m.rebuild_from_reference_midi(reference(count=8))
        self.assertEqual(set(widths(m)), {DEFAULT_NOTE_LANE_WIDTH})

    def test_the_default_matches_the_official_mode(self):
        """官方語料的眾數：real 3（97%）、extreme 3（99%）。"""
        self.assertEqual(DEFAULT_NOTE_LANE_WIDTH, 3)

    def test_every_note_is_rebuilt(self):
        m = chart()
        for note in m.notes_tree:
            note.pitch = None
        count = m.rebuild_from_reference_midi(reference(count=25))
        self.assertEqual(count, 25)
        self.assertEqual(len(m.notes_tree), 25)

    def test_lanes_stay_on_the_keyboard(self):
        m = chart(width=5)
        for note in m.notes_tree:
            note.pitch = None
        m.rebuild_from_reference_midi(reference())
        for note in m.notes_tree:
            self.assertGreaterEqual(int(note.min_key), 0)
            self.assertLess(int(note.max_key), 28)


if __name__ == '__main__':
    unittest.main()
