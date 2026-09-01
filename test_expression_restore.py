"""從原始 MIDI 把力度與延音踏板補回既有譜面。

轉譜時這兩樣會被丟掉（獨立的 midi_to_xml_converter 根本沒讀 CC64），但排譜
成果是人工調過的，不能為了拿回表情資料就整份重匯。所以還原時：

* 力度綁在音符上 → 走和「套用音高」同一套時間＋名次比對
* 踏板是 CC64 時間軸事件，不綁音符 → 整份覆蓋
* 音高、鍵道、左右手一律不動

另外，來源沒有 CC64 時**不能**把現有踏板清掉——手畫的踏板被無聲抹掉比什麼
都不做糟糕得多。
"""

import unittest

from qt_editor.models import GNote, NoteModel


def make_note(idx, start, pitch, velocity, hand=0, min_key=4, max_key=6):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = min_key
    n.max_key = max_key
    n.note_type = 0
    return n


def model_with(notes, spans=()):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    m.pedal_spans = [list(s) for s in spans]
    return m


class VelocityRestoreTests(unittest.TestCase):
    def _pair(self, chart_velocity=100):
        """同一段音樂的兩份資料：譜面（力度已遺失）與來源 MIDI。"""
        source = [make_note(i, i * 500, 60 + i, 30 + i * 12) for i in range(6)]
        chart = model_with([make_note(i, i * 500, 60 + i, chart_velocity)
                            for i in range(6)])
        return chart, source

    def test_velocity_is_restored_onto_the_matching_notes(self):
        chart, source = self._pair()
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['matched_notes'], 6)
        self.assertEqual(stats['velocity_applied'], 6)
        self.assertEqual([n.velocity for n in chart.notes_tree],
                         [30 + i * 12 for i in range(6)])

    def test_notes_that_already_match_are_not_counted_as_changed(self):
        # 力度本來就對的譜面重跑一次還原，應該是無操作
        chart, source = self._pair()
        chart.apply_midi_expression_from_source(source)
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['matched_notes'], 6)
        self.assertEqual(stats['velocity_applied'], 0)

    def test_pitch_lanes_and_hands_are_not_touched(self):
        chart, _ = self._pair()
        # 來源的左右手與鍵道和譜面不同：配對成功，但這兩樣不該被帶進來
        source = [make_note(i, i * 500, 60 + i, 30 + i * 12, hand=1,
                            min_key=20, max_key=22) for i in range(6)]
        before = [(n.pitch, n.hand, n.min_key, n.max_key) for n in chart.notes_tree]
        chart.apply_midi_expression_from_source(source)
        after = [(n.pitch, n.hand, n.min_key, n.max_key) for n in chart.notes_tree]
        self.assertEqual(after, before)
        self.assertEqual([n.velocity for n in chart.notes_tree],
                         [30 + i * 12 for i in range(6)])

    def test_chord_members_each_get_their_own_velocity(self):
        # 同一時間的和弦：每顆音靠自己的音高去拿力度
        chart = model_with([
            make_note(0, 1000, 60, 100, min_key=2, max_key=4),
            make_note(1, 1000, 64, 100, min_key=8, max_key=10),
            make_note(2, 1000, 67, 100, min_key=14, max_key=16),
        ])
        source = [
            make_note(0, 1000, 60, 41), make_note(1, 1000, 64, 82),
            make_note(2, 1000, 67, 123),
        ]
        chart.apply_midi_expression_from_source(source)
        self.assertEqual([n.velocity for n in chart.notes_tree], [41, 82, 123])

    def test_off_velocity_comes_along(self):
        chart, source = self._pair()
        for n in source:
            n.off_velocity = 7
        chart.apply_midi_expression_from_source(source)
        self.assertEqual([n.off_velocity for n in chart.notes_tree], [7] * 6)


class PedalRestoreTests(unittest.TestCase):
    SPANS = [[2.778, 1334.723], [1336.112, 2668.057]]

    def test_pedal_is_replaced_by_the_source_spans(self):
        chart = model_with([make_note(0, 0, 60, 90)])
        stats = chart.apply_midi_expression_from_source([], self.SPANS)
        self.assertEqual(stats['pedal_source'], 2)
        self.assertEqual(stats['pedal_after'], 2)
        self.assertEqual(chart.pedal_spans, self.SPANS)

    def test_existing_pedal_is_overwritten_not_merged(self):
        chart = model_with([make_note(0, 0, 60, 90)], [(9000.0, 9500.0)])
        chart.apply_midi_expression_from_source([], self.SPANS)
        self.assertEqual(chart.pedal_spans, self.SPANS)

    def test_a_source_without_pedal_leaves_the_existing_pedal_alone(self):
        chart = model_with([make_note(0, 0, 60, 90)], [(9000.0, 9500.0)])
        stats = chart.apply_midi_expression_from_source([], [])
        self.assertEqual(stats['pedal_source'], 0)
        self.assertEqual(chart.pedal_spans, [[9000.0, 9500.0]])

    def test_restore_pedal_can_be_switched_off(self):
        chart = model_with([make_note(0, 0, 60, 90)], [(9000.0, 9500.0)])
        chart.apply_midi_expression_from_source([], self.SPANS, restore_pedal=False)
        self.assertEqual(chart.pedal_spans, [[9000.0, 9500.0]])


class PedalTimelineTests(unittest.TestCase):
    """踏板的毫秒長在來源 MIDI 的時間軸上，必須換算到譜面的時間軸。

    syuten 的來源有 35 個變速事件（BPM 30~93），譜面卻是固定 86 BPM，兩條
    軸最多差 362ms。不換算的話踏板就踩在錯的地方——實測踏下點離譜面音符
    中位 31ms、最壞 573ms，換算後變成中位 3ms、最壞 4ms。
    """

    def _drifted(self):
        # 譜面等距，來源被變速拉扯過，但音符順序一樣
        chart = model_with([make_note(i, i * 1000, 60 + i, None) for i in range(5)])
        source = [make_note(i, t, 60 + i, 40 + i)
                  for i, t in enumerate([0, 1500, 2000, 4000, 4200])]
        return chart, source

    def test_pedal_is_converted_onto_the_chart_timeline(self):
        chart, source = self._drifted()
        # 來源時間軸上：踏板剛好蓋住第 2~4 顆音（1500~4000）
        stats = chart.apply_midi_expression_from_source(source, [[1500.0, 4000.0]])
        self.assertEqual(stats['alignment'], 'order')
        # 譜面時間軸上那三顆音是 1000~3000
        self.assertEqual(chart.pedal_spans, [[1000.0, 3000.0]])
        self.assertGreater(stats['pedal_shift_ms'], 0)

    def test_interpolation_between_anchors(self):
        chart, source = self._drifted()
        # 1750 落在來源錨點 1500(→1000) 與 2000(→2000) 的正中間
        chart.apply_midi_expression_from_source(source, [[1750.0, 2000.0]])
        self.assertEqual(chart.pedal_spans, [[1500.0, 2000.0]])

    def test_times_outside_the_anchors_keep_the_nearest_shift(self):
        chart, source = self._drifted()
        # 早於第一個錨點（來源 0 → 譜面 0，位移 0）
        chart.apply_midi_expression_from_source(source, [[-500.0, 0.0]])
        self.assertEqual(chart.pedal_spans, [[-500.0, 0.0]])

    def test_matching_timelines_leave_the_pedal_untouched(self):
        chart = model_with([make_note(i, i * 1000, 60 + i, None) for i in range(5)])
        source = [make_note(i, i * 1000, 60 + i, 40 + i) for i in range(5)]
        stats = chart.apply_midi_expression_from_source(source, [[1500.0, 3500.0]])
        self.assertEqual(stats['alignment'], 'time')
        self.assertEqual(stats['pedal_shift_ms'], 0)
        self.assertEqual(chart.pedal_spans, [[1500.0, 3500.0]])

    def test_pedal_passes_through_when_there_is_no_usable_alignment(self):
        # 對位被否決時沒有換算依據，原樣寫進去比亂猜好
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        source = [make_note(i, i * 137, 30 + i * 3, 42) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source, [[100.0, 900.0]])
        self.assertEqual(stats['alignment'], 'none')
        self.assertEqual(stats['pedal_shift_ms'], 0)
        self.assertEqual(chart.pedal_spans, [[100.0, 900.0]])


class MatchingRobustnessTests(unittest.TestCase):
    """力度用音高配對，不是鍵道名次——syuten 就是敗在後者。"""

    def test_shuffled_lanes_do_not_break_matching(self):
        # 鍵道被人工調過，順序不再等於音高順序。名次比對會全部配錯，
        # 音高比對不受影響。
        chart = model_with([
            make_note(0, 1000, 60, 100, min_key=20, max_key=22),
            make_note(1, 1000, 64, 100, min_key=2, max_key=4),
            make_note(2, 1000, 67, 100, min_key=11, max_key=13),
        ])
        source = [make_note(0, 1000, 60, 41), make_note(1, 1000, 64, 82),
                  make_note(2, 1000, 67, 123)]
        chart.apply_midi_expression_from_source(source)
        self.assertEqual([n.velocity for n in chart.notes_tree], [41, 82, 123])

    def test_extra_source_notes_are_ignored(self):
        # 來源比譜面多（譜面是精簡過的難度）：多出來的不該把後面的配對推歪
        chart = model_with([make_note(i, i * 500, 60 + i, 100) for i in range(4)])
        source = [make_note(i, i * 500, 60 + i, 30 + i * 10) for i in range(4)]
        source += [make_note(90 + i, i * 500, 80 + i, 7) for i in range(4)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['matched_exact'], 4)
        self.assertEqual([n.velocity for n in chart.notes_tree], [30, 40, 50, 60])

    def test_missing_source_notes_leave_those_notes_alone(self):
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(4)])
        source = [make_note(0, 0, 60, 42), make_note(2, 1000, 62, 44)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['matched_notes'], 2)
        self.assertEqual([n.velocity for n in chart.notes_tree], [42, None, 44, None])

    def test_a_constant_pitch_offset_is_detected_and_removed(self):
        # 不同轉檔工具的 scale_piano 八度基準差一個固定量（實測 bad-apple
        # 差 20 個半音）。位移沒扣掉的話音高全對不上。
        chart = model_with([make_note(i, i * 500, 80 + i, 100) for i in range(6)])
        source = [make_note(i, i * 500, 60 + i, 30 + i * 10) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['pitch_offset'], -20)
        self.assertEqual(stats['matched_exact'], 6)
        self.assertEqual([n.velocity for n in chart.notes_tree],
                         [30 + i * 10 for i in range(6)])

    def test_onsets_further_apart_than_the_tolerance_leave_time_alignment(self):
        # 容許誤差管的是「依時間對位」。超過了就不能再用時間配，改由依序
        # 對位接手（音高仍然要吻合才算數）。
        chart = model_with([make_note(0, 1000, 60, None)])
        source = [make_note(0, 1400, 60, 42)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertNotEqual(stats['alignment'], 'time')
        self.assertEqual(chart.notes_tree[0].velocity, 42)

    def test_small_onset_jitter_still_matches(self):
        chart = model_with([make_note(0, 1000, 60, None)])
        source = [make_note(0, 1003, 60, 42)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['matched_exact'], 1)
        self.assertEqual(chart.notes_tree[0].velocity, 42)


class AlignmentTests(unittest.TestCase):
    """譜面和來源 MIDI 的時間軸不一定一致，這時要改用「第幾個群組」對位。

    syuten 就是這樣：來源 MIDI 有 35 個變速事件（BPM 30~93），譜面卻是用
    固定 86 BPM 建的，絕對毫秒 863 個群組只對得上 165 個；但音符順序完全
    一樣，依序對位是 863/863。
    """

    def test_time_alignment_is_used_when_the_timelines_agree(self):
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        source = [make_note(i, i * 500, 60 + i, 30 + i * 10) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['alignment'], 'time')
        self.assertEqual(stats['matched_exact'], 6)

    def test_ordinal_alignment_rescues_a_different_tempo_map(self):
        # 來源時間被變速拉扯得亂七八糟，但音符順序不變
        drifted = [0, 900, 1100, 2400, 2500, 4000]
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        source = [make_note(i, drifted[i], 60 + i, 30 + i * 10) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['alignment'], 'order')
        self.assertEqual(stats['matched_exact'], 6)
        self.assertEqual([n.velocity for n in chart.notes_tree],
                         [30 + i * 10 for i in range(6)])

    def test_a_leading_extra_source_group_is_tolerated(self):
        drifted = [0, 900, 1100, 2400, 2500, 4000]
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        # 來源開頭多一個群組：依序對位要能往後挪一格
        source = [make_note(99, -400, 50, 9)]
        source += [make_note(i, drifted[i], 60 + i, 30 + i * 10) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['alignment'], 'order+1')
        self.assertEqual([n.velocity for n in chart.notes_tree],
                         [30 + i * 10 for i in range(6)])

    def test_an_unrelated_source_is_rejected_outright(self):
        # 選錯 MIDI 時不該硬湊。依序對位一定生得出一個配法，所以要靠音高
        # 吻合率的地板擋下來——回報 0 顆，讓使用者看得出來選錯了。
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        source = [make_note(i, i * 137, 30 + i * 3, 42) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source)
        self.assertEqual(stats['alignment'], 'none')
        self.assertEqual(stats['matched_notes'], 0)
        self.assertEqual([n.velocity for n in chart.notes_tree], [None] * 6)

    def test_pedal_still_comes_through_when_notes_cannot_be_aligned(self):
        # 踏板不綁音符，音符對不上也不該連踏板一起放棄
        chart = model_with([make_note(i, i * 500, 60 + i, None) for i in range(6)])
        source = [make_note(i, i * 137, 30 + i * 3, 42) for i in range(6)]
        stats = chart.apply_midi_expression_from_source(source, [[0.0, 900.0]])
        self.assertEqual(stats['matched_notes'], 0)
        self.assertEqual(chart.pedal_spans, [[0.0, 900.0]])


class ExistingPitchPathTests(unittest.TestCase):
    """既有的「套用音高與左右手」不能被表情還原影響。"""

    def test_applying_pitch_leaves_velocity_alone(self):
        chart = model_with([make_note(i, i * 500, 60 + i, 100) for i in range(4)])
        source = [make_note(i, i * 500, 90 - i, 33, hand=1) for i in range(4)]
        result = chart.apply_midi_pitches_from_source_notes(source)
        self.assertEqual(result['matched_notes'], 4)
        self.assertEqual([n.velocity for n in chart.notes_tree], [100] * 4)
        # 音高與左右手照舊要被套用
        self.assertEqual([n.pitch for n in chart.notes_tree], [90 - i for i in range(4)])
        self.assertEqual([n.hand for n in chart.notes_tree], [1] * 4)


if __name__ == '__main__':
    unittest.main()
