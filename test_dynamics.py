"""強弱記號：左右手各一條力度曲線。

曲線就是**力度隨時間的走向**：刻度貼著這一手實際用到的 min~max，一開始由音符
現在的力度描出來（＝目前的強弱長什麼樣），然後可以拖著改。

套用時的分母是「該起音當下的原始力度」，所以剛產生的曲線套回去是 no-op；同時
起音的音符一起等比縮放，和弦內部的強弱差保留。
"""

import json
import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel


def make_note(idx, start, velocity, hand=0, pitch=60):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    return n


def model_with(notes):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    return m


class DynamicsCurveTests(unittest.TestCase):
    def test_marks_are_sorted_and_deduped_by_time(self):
        m = model_with([])
        m.dynamics_add(0, 2000, 96)
        m.dynamics_add(0, 0, 48)
        m.dynamics_add(0, 2000, 112)      # 同一時間 → 覆蓋
        self.assertEqual([(int(a), int(b)) for a, b, _c in m.dynamics_marks(0)],
                         [(0, 48), (2000, 112)])

    def test_hold_keeps_the_level_until_the_next_mark(self):
        m = model_with([])
        m.dynamics_add(0, 0, 48, ramp=False)
        m.dynamics_add(0, 2000, 96, ramp=False)
        self.assertEqual(m.dynamics_level_at(0, 0), 48)
        self.assertEqual(m.dynamics_level_at(0, 1999), 48)   # 階梯，不漸變
        self.assertEqual(m.dynamics_level_at(0, 2000), 96)

    def test_ramp_interpolates_to_the_next_mark(self):
        m = model_with([])
        m.dynamics_add(0, 0, 48, ramp=True)
        m.dynamics_add(0, 2000, 96, ramp=False)
        self.assertEqual(m.dynamics_level_at(0, 0), 48)
        self.assertAlmostEqual(m.dynamics_level_at(0, 1000), 72.0)
        self.assertEqual(m.dynamics_level_at(0, 2000), 96)

    def test_level_is_flat_before_the_first_and_after_the_last_mark(self):
        m = model_with([])
        m.dynamics_add(0, 1000, 64, ramp=True)
        m.dynamics_add(0, 2000, 96)
        self.assertEqual(m.dynamics_level_at(0, 0), 64)
        self.assertEqual(m.dynamics_level_at(0, 9999), 96)

    def test_hands_are_independent(self):
        m = model_with([])
        m.dynamics_add(0, 0, 96)
        self.assertEqual(m.dynamics_level_at(0, 500), 96)
        self.assertIsNone(m.dynamics_level_at(1, 500))

    def test_removing_and_clearing(self):
        m = model_with([])
        m.dynamics_add(0, 1000, 64)
        self.assertFalse(m.dynamics_remove_near(0, 5000))   # 太遠，不動
        self.assertTrue(m.dynamics_remove_near(0, 1050))
        self.assertEqual(m.dynamics_marks(0), [])
        m.dynamics_add(0, 0, 64)
        m.dynamics_add(1, 0, 64)
        self.assertEqual(m.dynamics_clear(), 2)

    def test_mark_names_map_to_the_nearest_dynamic(self):
        self.assertEqual(NoteModel.dynamic_mark_name(96), 'f')
        self.assertEqual(NoteModel.dynamic_mark_name(30), 'pp')
        self.assertEqual(NoteModel.dynamic_mark_name(127), 'fff')


class ApplyDynamicsTests(unittest.TestCase):
    def test_a_flat_curve_levels_the_passage_to_that_value(self):
        # 曲線就是力度本身：整段畫平 = 這段都彈成這個力度
        notes = [make_note(0, 0, 40), make_note(1, 1000, 60), make_note(2, 2000, 80)]
        m = model_with(notes)
        m.dynamics_add(0, 0, 96, ramp=False)
        m.apply_dynamics()
        self.assertEqual([n.velocity for n in notes], [96, 96, 96])

    def test_a_chord_keeps_its_internal_spread(self):
        # 同時起音的音符一起等比縮放，內聲部的強弱差不會被壓平
        soft, loud = make_note(0, 0, 50), make_note(1, 0, 75)
        m = model_with([soft, loud])
        m.dynamics_add(0, 0, 100, ramp=False)     # 該起音原始平均 62.5 → ×1.6
        m.apply_dynamics()
        self.assertEqual(soft.velocity, 80)       # 50 × 1.6
        self.assertEqual(loud.velocity, 120)      # 75 × 1.6
        self.assertLess(soft.velocity, loud.velocity)

    def test_a_crescendo_makes_later_notes_louder_than_earlier_ones(self):
        notes = [make_note(i, i * 1000, 70) for i in range(4)]
        m = model_with(notes)
        m.dynamics_add(0, 0, 32, ramp=True)
        m.dynamics_add(0, 3000, 112, ramp=False)
        m.apply_dynamics()
        vels = [n.velocity for n in notes]
        self.assertEqual(vels, sorted(vels))
        self.assertLess(vels[0], vels[-1])

    def test_only_the_hand_with_marks_is_touched(self):
        right = make_note(0, 0, 70, hand=0)
        left = make_note(1, 0, 70, hand=1)
        m = model_with([right, left])
        m.dynamics_add(0, 0, 110)
        m.apply_dynamics()
        self.assertNotEqual(right.velocity, 70)
        self.assertEqual(left.velocity, 70)

    def test_notes_without_velocity_take_the_curve_value_directly(self):
        a = make_note(0, 0, 80)
        b = make_note(1, 1000, None)
        m = model_with([a, b])
        m.dynamics_add(0, 0, 64, ramp=False)
        m.apply_dynamics()
        self.assertEqual(b.velocity, 64)

    def test_apply_is_undoable(self):
        notes = [make_note(0, 0, 40), make_note(1, 1000, 60)]
        m = model_with(notes)
        m.dynamics_add(0, 0, 120)
        m.push_history()
        m.apply_dynamics()
        self.assertNotEqual([n.velocity for n in m.notes_tree], [40, 60])
        m.undo()
        self.assertEqual([n.velocity for n in m.notes_tree], [40, 60])

    def test_marks_themselves_are_undoable(self):
        m = model_with([make_note(0, 0, 60)])
        m.push_history()
        m.dynamics_add(0, 0, 96)
        self.assertTrue(m.dynamics_marks(0))
        m.undo()
        self.assertEqual(m.dynamics_marks(0), [])


class DynamicsPersistenceTests(unittest.TestCase):
    def _roundtrip(self, m):
        path = os.path.join(tempfile.mkdtemp(), 'd.json')
        m.save_json(path)
        with open(path, encoding='utf-8') as f:
            meta = json.load(f)
        back = NoteModel()
        back.load_json(path)
        return meta, back

    def test_marks_survive_save_and_load(self):
        m = model_with([make_note(0, 0, 60)])
        m.dynamics_add(0, 0, 48, ramp=True)
        m.dynamics_add(0, 2000, 96)
        m.dynamics_add(1, 500, 32)
        meta, back = self._roundtrip(m)
        self.assertIn('dynamics_data', meta)
        self.assertEqual(back.dynamics_marks(0),
                         [[0.0, 48.0, True], [2000.0, 96.0, False]])
        self.assertEqual(back.dynamics_marks(1), [[500.0, 32.0, False]])

    def test_no_marks_means_no_field(self):
        m = model_with([make_note(0, 0, 60)])
        meta, back = self._roundtrip(m)
        self.assertNotIn('dynamics_data', meta)
        self.assertEqual(back.dynamics, {})

    def test_broken_data_is_ignored_rather_than_crashing(self):
        self.assertEqual(NoteModel._dynamics_from_meta({'dynamics_data': 'oops'}), {})
        self.assertEqual(
            NoteModel._dynamics_from_meta({'dynamics_data': {'x': [{'ms': 1}]}}), {})


class DynamicsBaselineTests(unittest.TestCase):
    """欄位裡那條虛線 = 這一手目前的平均力度，也就是「倍率 = 1」的位置。

    沒有這條線只看得到曲線的絕對高低，看不出來套用後到底是加強還是減弱。
    """

    def test_baseline_is_the_hand_average(self):
        m = model_with([make_note(0, 0, 40), make_note(1, 1000, 80),
                        make_note(2, 2000, 60, hand=1)])
        self.assertEqual(m.dynamics_baseline(0), 60.0)
        self.assertEqual(m.dynamics_baseline(1), 60.0)

    def test_baseline_ignores_notes_without_velocity(self):
        a = make_note(0, 0, 90)
        b = make_note(1, 1000, None)
        m = model_with([a, b])
        self.assertEqual(m.dynamics_baseline(0), 90.0)

    def test_a_chart_with_no_velocity_can_still_be_given_dynamics(self):
        # 純遊戲譜沒有 velocity 欄位，畫了曲線就直接照曲線值寫進去
        a = make_note(0, 0, None)
        m = model_with([a])
        self.assertEqual(m.dynamics_baseline(0), 0.0)
        m.dynamics_add(0, 0, 96)
        self.assertEqual(m.apply_dynamics(), 1)
        self.assertEqual(a.velocity, 96)

    def test_a_curve_seeded_from_the_notes_applies_as_a_no_op(self):
        # 這是新語意的關鍵：曲線本來就長得跟現況一樣，套回去什麼都不該改
        notes = [make_note(0, 0, 50), make_note(1, 1000, 70), make_note(2, 2000, 61)]
        m = model_with(notes)
        m.dynamics_seed_from_notes(0)
        self.assertEqual(m.apply_dynamics(hands=[0]), 0)
        self.assertEqual([n.velocity for n in notes], [50, 70, 61])


class DynamicsRangeTests(unittest.TestCase):
    """欄位刻度貼著這一手實際用到的力度範圍，不是固定的 1~127。

    只用到 70~96 的譜如果照滿刻度畫，整條曲線會擠成一條直線，看不出起伏。
    """

    def test_range_is_the_hand_min_and_max(self):
        m = model_with([make_note(0, 0, 70), make_note(1, 1000, 96),
                        make_note(2, 2000, 84), make_note(3, 0, 20, hand=1)])
        self.assertEqual(m.dynamics_range(0), (70.0, 96.0))

    def test_a_degenerate_range_is_widened_so_the_scale_still_works(self):
        m = model_with([make_note(0, 0, 64), make_note(1, 1000, 64)])
        lo, hi = m.dynamics_range(0)
        self.assertGreater(hi - lo, 0, '刻度不能是零寬（會除以零）')

    def test_no_velocity_data_falls_back_to_the_full_midi_range(self):
        m = model_with([make_note(0, 0, None)])
        self.assertEqual(m.dynamics_range(0), (1.0, 127.0))


class DynamicsContourTests(unittest.TestCase):
    """由音符現在的力度描出「目前的強弱」，然後才拿去編輯。"""

    def test_one_mark_per_onset_by_default(self):
        notes = [make_note(0, 0, 70), make_note(1, 500, 84), make_note(2, 1000, 96)]
        m = model_with(notes)
        contour = m.dynamics_contour_from_notes(0)
        self.assertEqual([(int(ms), lv) for ms, lv, _r in contour],
                         [(0, 70.0), (500, 84.0), (1000, 96.0)])

    def test_simultaneous_notes_collapse_to_their_mean(self):
        m = model_with([make_note(0, 0, 60), make_note(1, 0, 80)])
        contour = m.dynamics_contour_from_notes(0)
        self.assertEqual(len(contour), 1)
        self.assertEqual(contour[0][1], 70.0)

    def test_resolution_buckets_and_averages(self):
        notes = [make_note(i, i * 250, 40 + i * 10) for i in range(4)]
        m = model_with(notes)
        coarse = m.dynamics_contour_from_notes(0, resolution_ms=1000.0)
        self.assertEqual(len(coarse), 1)
        self.assertEqual(coarse[0][1], 55.0)          # (40+50+60+70)/4

    def test_contour_marks_ramp_so_it_draws_as_a_curve(self):
        notes = [make_note(0, 0, 70), make_note(1, 500, 90)]
        m = model_with(notes)
        contour = m.dynamics_contour_from_notes(0)
        self.assertTrue(contour[0][2], '中間的點要漸變，才是連續的輪廓')
        self.assertFalse(contour[-1][2], '最後一個之後沒得漸變')

    def test_seeding_installs_editable_marks(self):
        notes = [make_note(0, 0, 70), make_note(1, 500, 90)]
        m = model_with(notes)
        self.assertEqual(m.dynamics_seed_from_notes(0), 2)
        self.assertEqual(len(m.dynamics_marks(0)), 2)
        # 產生之後就能像手畫的一樣改
        marks = m.dynamics_marks(0)
        marks[0][1] = 120.0
        m.dynamics_set(0, marks)
        m.apply_dynamics(hands=[0])
        self.assertEqual(notes[0].velocity, 120)
        self.assertEqual(notes[1].velocity, 90)

    def test_seeding_a_hand_with_no_velocity_produces_nothing(self):
        m = model_with([make_note(0, 0, None)])
        self.assertEqual(m.dynamics_seed_from_notes(0), 0)


class LaneHeaderTests(unittest.TestCase):
    """欄位只有 18~26px 寬，光看顏色分不出誰是踏板誰是力度。

    表頭釘在最上面不跟著捲動；停在欄位上還有一段 tooltip 說明怎麼操作。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.chart_view import ChartView
        self.app = QApplication.instance() or QApplication([])
        self.view = ChartView()
        self.view.resize(1200, 720)
        m = model_with([make_note(0, 0, 70), make_note(1, 500, 96),
                        make_note(2, 0, 60, hand=1)])
        self.view.load_model(m)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')

    def test_every_lane_gets_a_labelled_header(self):
        headers = self.view._lane_headers()
        self.assertEqual([h[1] for h in headers],
                         ['踏板', '左力道曲線', '右力道曲線'])

    def test_headers_sit_beside_the_keyboard_not_above_the_chart(self):
        # 放在譜面最上方會壓著譜、看起來很雜；鍵盤那一帶本來就是空的
        top = float(self.view._piano_top_py())
        for rect, _label, _colour, _tip in self.view._lane_headers():
            self.assertEqual(rect.top(), top, '標籤要貼著鍵盤上緣')
            self.assertGreater(rect.width(), 0)

    def test_headers_line_up_with_the_lane_they_label(self):
        by_label = {h[1]: h[0] for h in self.view._lane_headers()}
        for hand, label in ((1, '左力道曲線'), (0, '右力道曲線')):
            lane = self.view._dyn_lane_rect(hand)
            self.assertEqual(by_label[label].left(), lane.left())
            self.assertEqual(by_label[label].width(), lane.width())

    def test_header_colour_matches_what_the_lane_draws(self):
        from qt_editor.chart_view import (DYN_LINE_LEFT, DYN_LINE_RIGHT,
                                          PEDAL_SPAN_COLOR)
        colours = {h[1]: h[2] for h in self.view._lane_headers()}
        self.assertEqual(colours['踏板'], PEDAL_SPAN_COLOR)
        self.assertEqual(colours['左力道曲線'], DYN_LINE_LEFT)
        self.assertEqual(colours['右力道曲線'], DYN_LINE_RIGHT)

    def test_tooltip_identifies_the_lane_under_the_cursor(self):
        self.assertIn('延音踏板', self.view._lane_tooltip_at(5, 100))
        self.assertIn('左手力度曲線', self.view._lane_tooltip_at(25, 100))
        self.assertIn('右手力度曲線', self.view._lane_tooltip_at(1190, 100))
        self.assertEqual(self.view._lane_tooltip_at(600, 100), '')

    def test_tooltip_shows_the_current_scale(self):
        tip = self.view._lane_tooltip_at(1190, 100)
        self.assertIn('70', tip)      # 右手範圍 70~96
        self.assertIn('96', tip)

    def test_no_headers_outside_pitch_mode(self):
        self.view.set_view_mode('measure')
        self.assertEqual(self.view._lane_headers(), [])
        self.assertEqual(self.view._lane_tooltip_at(5, 100), '')


class DynamicsEnvelopeTests(unittest.TestCase):
    """同一時刻常常不只一個力度——旋律音重、內聲部輕。

    取平均會把這個差距抹掉，所以保留 min/max 畫成兩條線；力度一致時兩條重合，
    看起來就是一條。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.chart_view import ChartView
        self.app = QApplication.instance() or QApplication([])
        self.view = ChartView()
        self.view.resize(1200, 760)

    def _load(self, notes):
        m = model_with(notes)
        self.view.load_model(m)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        return m

    def test_simultaneous_notes_give_a_low_and_a_high(self):
        self._load([make_note(0, 0, 110), make_note(1, 0, 55)])
        self.assertEqual(self.view._dynamics_note_envelope(0), [(0, 55.0, 110.0)])

    def test_a_single_velocity_collapses_to_one_value(self):
        self._load([make_note(0, 0, 80), make_note(1, 0, 80)])
        self.assertEqual(self.view._dynamics_note_envelope(0), [(0, 80.0, 80.0)])

    def test_onsets_come_out_in_time_order(self):
        self._load([make_note(0, 2000, 60), make_note(1, 0, 90),
                    make_note(2, 1000, 70)])
        got = [ms for ms, _lo, _hi in self.view._dynamics_note_envelope(0)]
        self.assertEqual(got, sorted(got))

    def test_the_other_hand_is_not_mixed_in(self):
        self._load([make_note(0, 0, 110), make_note(1, 0, 20, hand=1)])
        self.assertEqual(self.view._dynamics_note_envelope(0), [(0, 110.0, 110.0)])
        self.assertEqual(self.view._dynamics_note_envelope(1), [(0, 20.0, 20.0)])

    def test_notes_without_velocity_are_skipped(self):
        self._load([make_note(0, 0, None), make_note(1, 0, 64)])
        self.assertEqual(self.view._dynamics_note_envelope(0), [(0, 64.0, 64.0)])

    def test_an_empty_hand_gives_nothing(self):
        self._load([make_note(0, 0, 64, hand=1)])
        self.assertEqual(self.view._dynamics_note_envelope(0), [])


if __name__ == '__main__':
    unittest.main()
