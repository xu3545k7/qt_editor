# -*- coding: utf-8 -*-
"""疊和聲：以選取為最低音／冠音，加八度或和弦。

使用者：「音高偏移按鈕右鍵也要放，並且加上一些功能：以此為冠音＋八度＋和弦、
以此為最低音＋八度＋和弦。要複製同樣音符編排但是高或低八度／和弦。
如果是 2 音就隔開 1 格，3 音緊密排，4 音全部改 2 寬並且緊密排。」

兩側的語意相反，這是最容易寫反的地方：

    以此為最低音  往**高音**側加，原本的音留在原位
    以此為冠音    往**低音**側加，原本那顆是最高的，**右緣**不動

右緣是排序權威，所以冠音那側釘右緣不是左緣。
"""

import unittest

from PyQt5.QtWidgets import QApplication, QMenu

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])

#: 一段 C 大調，讓調性偵測有東西可以推
CONTEXT = (60, 62, 64, 65, 67, 69, 71, 72)


def note(idx, start, pitch, lo=10, hi=12):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 200, 200
    n.pitch, n.hand, n.note_type = pitch, 0, 0
    n.min_key, n.max_key = lo, hi
    return n


class ChordPackTests(unittest.TestCase):
    """排版規則本身（純模型，不碰介面）。"""

    def group(self, count, lo=10, hi=12):
        return [note(i, 1000, 60 + 4 * i, lo, hi) for i in range(count)]

    def spans(self, notes):
        return [(int(n.min_key), int(n.max_key)) for n in notes]

    def test_two_notes_leave_one_lane_between_them(self):
        ns = self.group(2)
        NoteModel.pack_chord_lanes(ns, 'low')
        self.assertEqual(self.spans(ns), [(10, 12), (14, 16)])

    def test_three_notes_sit_flush(self):
        ns = self.group(3)
        NoteModel.pack_chord_lanes(ns, 'low')
        self.assertEqual(self.spans(ns), [(10, 12), (13, 15), (16, 18)])

    def test_four_notes_go_to_width_two_and_flush(self):
        ns = self.group(4)
        NoteModel.pack_chord_lanes(ns, 'low')
        self.assertEqual(self.spans(ns),
                         [(10, 11), (12, 13), (14, 15), (16, 17)])

    def test_five_notes_follow_the_dense_rule_too(self):
        ns = self.group(5)
        NoteModel.pack_chord_lanes(ns, 'low')
        self.assertTrue(all(hi - lo == 1 for lo, hi in self.spans(ns)))

    def test_the_low_anchor_keeps_the_bottom_note_put(self):
        ns = self.group(3)
        NoteModel.pack_chord_lanes(ns, 'low')
        self.assertEqual(int(ns[0].min_key), 10)

    def test_the_high_anchor_keeps_the_top_right_edge_put(self):
        """右緣是排序權威，冠音那側要釘右緣。"""
        ns = self.group(3)
        NoteModel.pack_chord_lanes(ns, 'high')
        self.assertEqual(int(ns[-1].max_key), 12)

    def test_a_lone_note_is_left_alone(self):
        ns = self.group(1)
        self.assertEqual(NoteModel.pack_chord_lanes(ns, 'low'), 0)
        self.assertEqual(self.spans(ns), [(10, 12)])

    def test_it_never_runs_off_the_keyboard(self):
        ns = self.group(4, lo=25, hi=27)
        NoteModel.pack_chord_lanes(ns, 'low')
        for lo, hi in self.spans(ns):
            self.assertGreaterEqual(lo, 0)
            self.assertLess(hi, 28)

    def test_a_chord_too_wide_for_the_keyboard_is_refused(self):
        ns = self.group(4)
        before = self.spans(ns)
        NoteModel.pack_chord_lanes(ns, 'low', total_lanes=4)
        self.assertEqual(self.spans(ns), before)


class HarmonyTests(unittest.TestCase):
    def view(self, pitches, starts=None):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        ctx = [note(100 + i, 5000 + 300 * i, p)
               for i, p in enumerate(CONTEXT * 3)]
        picked = [note(i, (starts or [1000] * len(pitches))[i], p)
                  for i, p in enumerate(pitches)]
        m.notes_tree = ctx + picked
        m.rebuild_display_cache()
        view = ChartView()
        view.model = m
        view.selected = {n.idx for n in picked}
        return view, m

    def pitches_at(self, model, when=1000):
        return sorted(int(n.pitch) for n in model.notes_tree
                      if int(n.start) == when)

    # ── 以此為最低音：往上加 ──────────────────────────────────
    def test_low_octave_adds_the_note_above(self):
        view, model = self.view([60])
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 1)
        self.assertEqual(self.pitches_at(model), [60, 72])

    def test_low_triad_is_diatonic(self):
        """C 大調從 C 疊上去是 C-E-G，不用另外指定和弦品質。"""
        view, model = self.view([60])
        self.assertEqual(view.add_harmony_to_selection('low', 'triad'), 2)
        self.assertEqual(self.pitches_at(model), [60, 64, 67])

    def test_low_seventh_adds_three(self):
        view, model = self.view([60])
        self.assertEqual(view.add_harmony_to_selection('low', 'seventh'), 3)
        self.assertEqual(self.pitches_at(model), [60, 64, 67, 71])

    # ── 以此為冠音：往下加 ────────────────────────────────────
    def test_high_octave_adds_the_note_below(self):
        view, model = self.view([72])
        self.assertEqual(view.add_harmony_to_selection('high', 'octave'), 1)
        self.assertEqual(self.pitches_at(model), [60, 72])

    def test_high_triad_puts_the_selection_on_top(self):
        view, model = self.view([72])
        view.add_harmony_to_selection('high', 'triad')
        got = self.pitches_at(model)
        self.assertEqual(max(got), 72, '選取的那顆要是最高的')
        self.assertEqual(got, [65, 69, 72])

    # ── 「複製同樣音符編排」 ──────────────────────────────────
    def test_an_octave_copies_the_whole_arrangement(self):
        """兩顆的選取 ＋八度 要得到四顆，不是只複製其中一顆。"""
        view, model = self.view([60, 64])
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 2)
        self.assertEqual(self.pitches_at(model), [60, 64, 72, 76])

    def test_the_clones_keep_timing_and_hand(self):
        view, model = self.view([60])
        view.add_harmony_to_selection('low', 'octave')
        made = [n for n in model.notes_tree if int(n.start) == 1000]
        self.assertEqual({int(n.start) for n in made}, {1000})
        self.assertEqual({int(n.end) for n in made}, {1200})
        self.assertEqual({int(n.hand) for n in made}, {0})

    # ── 排版接上去 ────────────────────────────────────────────
    def test_the_result_is_packed_by_the_rules(self):
        view, model = self.view([60])
        view.add_harmony_to_selection('low', 'seventh')
        made = sorted((n for n in model.notes_tree if int(n.start) == 1000),
                      key=lambda n: int(n.pitch))
        self.assertEqual([(int(n.min_key), int(n.max_key)) for n in made],
                         [(10, 11), (12, 13), (14, 15), (16, 17)])

    # ── 範圍：八度跨刻、和弦只動一刻 ──────────────────────────
    def test_an_octave_copies_every_selected_instant(self):
        """使用者：「加八度那些的還是只加了第一個音」。

        八度是「複製同樣音符編排」，所以選一整段八度樂句下去，每一刻都要
        長出夥伴，不是只有第一顆。
        """
        view, model = self.view([60, 64], starts=[1000, 2000])
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 2)
        self.assertEqual(self.pitches_at(model, 1000), [60, 72])
        self.assertEqual(self.pitches_at(model, 2000), [64, 76])

    def test_an_octave_handles_a_long_run(self):
        starts = [1000 + 200 * i for i in range(6)]
        view, model = self.view([60, 62, 64, 65, 67, 69], starts=starts)
        self.assertEqual(view.add_harmony_to_selection('high', 'octave'), 6)
        for when, pitch in zip(starts, (60, 62, 64, 65, 67, 69)):
            self.assertEqual(self.pitches_at(model, when), [pitch - 12, pitch])

    def test_each_instant_is_packed_on_its_own(self):
        """跨刻一起排會把不同時間的音符擠成一疊；每一刻要各自排。"""
        view, model = self.view([60, 64], starts=[1000, 2000])
        view.add_harmony_to_selection('low', 'octave')
        for when in (1000, 2000):
            made = sorted((n for n in model.notes_tree if int(n.start) == when),
                          key=lambda n: int(n.pitch))
            self.assertEqual([(int(n.min_key), int(n.max_key)) for n in made],
                             [(10, 12), (14, 16)])

    def test_chords_still_only_touch_the_earliest_instant(self):
        """和弦套到每一刻會把一整段旋律變成一整段和弦——那條限制留著。"""
        view, model = self.view([60, 64], starts=[1000, 2000])
        self.assertEqual(view.add_harmony_to_selection('low', 'triad'), 2)
        self.assertEqual(self.pitches_at(model, 1000), [60, 64, 67])
        self.assertEqual(self.pitches_at(model, 2000), [64])

    def test_an_octave_skips_only_the_instants_that_leave_the_piano(self):
        """一刻超出音域不該連累其他刻。"""
        view, model = self.view([60, 107], starts=[1000, 2000])
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 1)
        self.assertEqual(self.pitches_at(model, 1000), [60, 72])
        self.assertEqual(self.pitches_at(model, 2000), [107])

    def test_the_whole_run_is_one_undo(self):
        view, model = self.view([60, 64], starts=[1000, 2000])
        view.add_harmony_to_selection('low', 'octave')
        model.undo()
        model.rebuild_display_cache()
        self.assertEqual(self.pitches_at(model, 1000), [60])
        self.assertEqual(self.pitches_at(model, 2000), [64])

    # ── 邊界 ──────────────────────────────────────────────────
    def test_nothing_selected_does_nothing(self):
        view, _model = self.view([60])
        view.selected = set()
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 0)

    def test_it_refuses_to_leave_the_piano(self):
        view, model = self.view([107])
        self.assertEqual(view.add_harmony_to_selection('low', 'octave'), 0)
        self.assertEqual(self.pitches_at(model), [107])

    def test_a_bad_argument_does_nothing(self):
        view, _model = self.view([60])
        self.assertEqual(view.add_harmony_to_selection('sideways', 'octave'), 0)
        self.assertEqual(view.add_harmony_to_selection('low', 'nonsense'), 0)

    def test_it_is_undoable(self):
        view, model = self.view([60])
        view.add_harmony_to_selection('low', 'triad')
        model.undo()
        model.rebuild_display_cache()
        self.assertEqual(self.pitches_at(model), [60])


class PitchMenuTests(unittest.TestCase):
    """使用者要求把音高偏移也放進右鍵（本來只在「編輯」選單）。"""

    def setUp(self):
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        model.notes_tree = [note(0, 1000, 60), note(1, 1000, 64)]
        model.rebuild_display_cache()
        self.view = ChartView()
        self.view.model = model
        self.view.selected = {0, 1}
        # menu 要留在 self 上：被回收之後 Qt 會把子選單刪掉，再讀 actions()
        # 會噴「wrapped C/C++ object has been deleted」。
        self._menu = QMenu()
        self.view._ctx_build_note_menu(self._menu, True)
        self.sub = next(a.menu() for a in self._menu.actions()
                        if a.text() == '音高')

    def test_the_pitch_submenu_exists(self):
        self.assertIsNotNone(self.sub)

    def test_the_shift_entries_are_there(self):
        texts = [a.text() for a in self.sub.actions()]
        for want in ('＋1 半音', '－1 半音', '＋八度', '－八度', '自訂偏移…'):
            self.assertIn(want, texts)

    def test_both_anchors_offer_all_three_kinds(self):
        for side in ('以此為最低音', '以此為冠音'):
            sub = next(a.menu() for a in self.sub.actions()
                       if a.text() == side)
            labels = [b.text() for b in sub.actions()]
            self.assertEqual(len(labels), 3, side)
            self.assertTrue(any('八度' in x for x in labels), side)
            self.assertTrue(any('三和弦' in x for x in labels), side)
            self.assertTrue(any('七和弦' in x for x in labels), side)

    def test_it_says_how_many_notes_it_will_use(self):
        texts = [a.text() for a in self.sub.actions()]
        self.assertTrue(any('2 顆' in x for x in texts), texts)

    def test_the_labels_separate_the_two_scopes(self):
        """八度動整個選取、和弦只動最早那一刻——標籤要講清楚，不然按下去才發現。"""
        model = NoteModel.create_new('t', 120.0, 20.0, 4)
        model.notes_tree = [note(0, 1000, 60), note(1, 1000, 64),
                            note(2, 2000, 67)]
        model.rebuild_display_cache()
        view = ChartView()
        view.model = model
        view.selected = {0, 1, 2}
        menu = QMenu()
        view._ctx_build_note_menu(menu, True)
        sub = next(a.menu() for a in menu.actions() if a.text() == '音高')
        side = next(a.menu() for a in sub.actions()
                    if a.text() == '以此為最低音')
        labels = {('octave' if '八度' in b.text() else 'chord'): b.text()
                  for b in side.actions()}
        self.assertIn('3 顆', labels['octave'], labels)
        self.assertIn('2 顆', labels['chord'], labels)
        menu.clear()


if __name__ == '__main__':
    unittest.main()
