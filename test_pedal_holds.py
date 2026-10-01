# -*- coding: utf-8 -*-
"""踏板造成的長度 → Tap。

使用者看著一串上行音階說「這很明顯都是單音配踏板而已吧」——AI 音訊轉譜的
note-off 是踏板放開的時間，不是手指離鍵的時間，於是整串音階每顆都變成長押。

分級修整抓不到這種：它看的是「音符現在的長度」，而那個長度正是踏板的長度，
所以每顆都被判成「長 Hold」，只把尾端前移 40ms 就結束了。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])

LONG = 2


def note(start, end, pitch=60, hand=0, note_type=LONG, idx=0, lane=None):
    """鍵道預設照音高算。

    `_same_key`（「下一顆用到同一個鍵」）比的是**鍵道範圍**不是音高，所以
    fixture 若讓每顆都用同一格，每一對都會判成搶同一個鍵，任何規則都試不出來
    （記憶 hold-length-fix 記過這個坑）。
    """
    n = GNote(None, idx)
    n.start = start
    n.end = end
    n.gate = end - start
    n.pitch = pitch
    n.note_type = note_type
    n.hand = hand
    n.track = 1
    n.min_key = (pitch - 21) // 3 if lane is None else lane
    n.max_key = n.min_key
    return n


def model_with(notes):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


class PedalToTapTests(unittest.TestCase):
    def test_a_run_of_overlapping_long_notes_becomes_taps(self):
        """一串上行音階，每顆都拖過下一顆——整串都是踏板。"""
        run = [note(i * 200, i * 200 + 900, pitch=60 + i, idx=i) for i in range(6)]
        m = model_with(run)                              # 120 BPM -> 一拍 500 ms
        stats = m.pedal_lengths_to_keypress(gap_ms=40)
        self.assertEqual(stats['trimmed'], 5, '最後一顆後面沒有音，不算')
        self.assertEqual(stats['retyped'], 5, '縮完只剩 160 ms，不到一拍 -> Tap')
        self.assertEqual([int(n.note_type) & LONG for n in run[:5]], [0] * 5)
        self.assertEqual([n.end for n in run[:5]], [160, 360, 560, 760, 960])

    def test_a_note_with_room_to_spare_stays_a_hold(self):
        """縮完還有一拍以上，手指按得住，就還是長押。"""
        notes = [note(0, 5000, pitch=60, idx=0), note(2000, 2100, pitch=62, idx=1)]
        m = model_with(notes)
        stats = m.pedal_lengths_to_keypress(gap_ms=40)
        self.assertEqual(stats['trimmed'], 1)
        self.assertEqual(stats['retyped'], 0)
        self.assertEqual(notes[0].end, 1960)
        self.assertEqual(int(notes[0].note_type) & LONG, LONG)

    def test_a_tap_with_a_pedal_length_is_shortened_too(self):
        """問題不只在長押：AI 轉譜連 Tap 的長度都是踏板長度。"""
        notes = [note(0, 900, note_type=0, idx=0), note(200, 300, note_type=0, idx=1)]
        m = model_with(notes)
        stats = m.pedal_lengths_to_keypress(gap_ms=40)
        self.assertEqual(stats['trimmed'], 1)
        self.assertEqual(notes[0].end, 160)

    def test_a_note_released_before_the_next_one_stays_a_hold(self):
        """尾巴在同手下一顆之前就結束——那是手指真的按著。"""
        notes = [note(0, 400, idx=0), note(500, 900, idx=1)]
        m = model_with(notes)
        self.assertEqual(m.pedal_lengths_to_keypress()['trimmed'], 0)
        self.assertEqual(int(notes[0].note_type) & LONG, LONG)

    def test_the_other_hand_does_not_count(self):
        """另一隻手在彈，不妨礙這隻手按著。"""
        notes = [note(0, 2000, pitch=40, hand=1, idx=0),
                 note(200, 300, pitch=72, hand=0, note_type=0, idx=1),
                 note(400, 500, pitch=74, hand=0, note_type=0, idx=2)]
        m = model_with(notes)
        self.assertEqual(m.pedal_lengths_to_keypress()['trimmed'], 0)
        self.assertEqual(int(notes[0].note_type) & LONG, LONG)

    def test_a_chord_partner_is_not_the_next_note(self):
        """同時開始的是和弦，不是「下一顆」。"""
        notes = [note(0, 800, pitch=60, idx=0), note(0, 800, pitch=64, idx=1)]
        m = model_with(notes)
        self.assertEqual(m.pedal_lengths_to_keypress()['trimmed'], 0)

    def test_other_note_type_bits_survive(self):
        """只清長押位元，skin 之類的其他位元要留著（見記憶 note-type-bitmask）。"""
        notes = [note(0, 900, note_type=LONG | 8, idx=0), note(200, 300, idx=1)]
        m = model_with(notes)
        m.pedal_lengths_to_keypress()
        self.assertEqual(int(notes[0].note_type), 8, '只清長押位元，skin 要留著')

    def test_it_only_touches_the_notes_it_is_given(self):
        a = note(0, 900, pitch=60, idx=0)
        b = note(200, 1100, pitch=62, idx=1)
        c = note(400, 500, pitch=64, idx=2)
        m = model_with([a, b, c])
        self.assertEqual(m.pedal_lengths_to_keypress([b])['trimmed'], 1)
        self.assertEqual(a.end, 900, '沒被指定的不該動')


class OrderTests(unittest.TestCase):
    """順序：要在任何裁切之前做，不然證據被自己抹掉。"""

    def test_trimming_first_destroys_the_evidence(self):
        run = [note(i * 200, i * 200 + 900, pitch=60 + i, idx=i) for i in range(6)]
        m = model_with(run)
        m.enforce_hold_tail_gap(40, list(run), only_conflicts=False)
        self.assertEqual(m.pedal_lengths_to_keypress()['retyped'], 0,
                         '裁過之後就沒有東西蓋過下一顆了，這條規則抓不到')

    def test_the_view_runs_it_before_the_tiers(self):
        from qt_editor.chart_view import ChartView
        run = [note(i * 200, i * 200 + 900, pitch=60 + i, idx=i) for i in range(6)]
        view = ChartView()
        view.model = model_with(run)
        stats = view.apply_hold_length_fix({
            'pedal_to_tap': True, 'tap_th_beats': 0.5, 'hold_th_beats': 1.0,
            'short_ratio': 0.6, 'long_advance_ms': 40, 'tail_gap_ms': 0,
            'scope': 'all',
        })
        self.assertEqual(stats['pedal'], 5)
        self.assertEqual(stats['shortened'], 5)
        self.assertEqual(stats['total'], 1, '轉成 Tap 的就不進分級了')

    def test_the_option_can_be_turned_off(self):
        from qt_editor.chart_view import ChartView
        run = [note(i * 200, i * 200 + 900, pitch=60 + i, idx=i) for i in range(6)]
        view = ChartView()
        view.model = model_with(run)
        stats = view.apply_hold_length_fix({
            'pedal_to_tap': False, 'tap_th_beats': 0.5, 'hold_th_beats': 1.0,
            'short_ratio': 0.6, 'long_advance_ms': 40, 'tail_gap_ms': 0,
            'scope': 'all',
        })
        self.assertEqual(stats['pedal'], 0)
        self.assertEqual(stats['total'], 6)


if __name__ == '__main__':
    unittest.main()


class RingLimitTests(unittest.TestCase):
    """一串超過 N 顆就不是分解和弦了——手按得住也該放開。

    使用者：「至少超過 5 顆音一串可以裁吧」。實測曲庫 11734 顆長押只有 45 顆
    （0.4%）底下串超過 5 顆，其中 27 顆現行規則本來就會裁；這一刀多抓剩下的
    18 顆，例如 Evans 有一顆 2.2 秒的長押底下串了 9 顆同手音。
    """

    def _chart(self, under):
        """一顆長押，底下串 `under` 顆同手的音（都按得住：同手、音域很窄）。"""
        hold = note(0, 4000, pitch=60, idx=0)
        run = [note(200 + i * 200, 200 + i * 200 + 80, pitch=62 + (i % 3),
                    note_type=0, idx=i + 1, lane=8) for i in range(under)]
        return model_with([hold] + run), hold

    def test_a_short_broken_chord_is_left_alone(self):
        m, hold = self._chart(3)
        m.resolve_hold_tail_overlaps(40, only_conflicts=True, max_ring_notes=5)
        self.assertEqual(hold.end, 4000, '三顆還是分解和弦')

    def test_a_long_string_releases_the_hold(self):
        m, hold = self._chart(9)
        m.resolve_hold_tail_overlaps(40, only_conflicts=True, max_ring_notes=5)
        self.assertLess(hold.end, 4000, '串了九顆就不是分解和弦了')
        self.assertEqual(hold.end, 200 + 5 * 200 - 40, '在第六顆進來時放開')

    def test_zero_means_no_limit(self):
        m, hold = self._chart(9)
        m.resolve_hold_tail_overlaps(40, only_conflicts=True, max_ring_notes=0)
        self.assertEqual(hold.end, 4000)

    def test_the_tiered_tool_passes_the_limit_through(self):
        from qt_editor.chart_view import ChartView
        m, hold = self._chart(9)
        view = ChartView()
        view.model = m
        view.apply_hold_length_fix({
            'pedal_to_tap': False, 'tap_th_beats': 0.5, 'hold_th_beats': 1.0,
            'short_ratio': 1.0, 'long_advance_ms': 0, 'tail_gap_ms': 40,
            'tail_only_conflicts': True, 'tail_ring_notes': 5, 'scope': 'all',
        })
        self.assertLess(hold.end, 4000)


class TrimOnMidiImportTests(unittest.TestCase):
    """匯入 MIDI 時就把踏板長度折算回按鍵長度。

    使用者：「為啥我看 midi 很正常，轉成遊戲譜就一堆奇怪的長條?」
    `trim_pedal_sustained_holds` 本來只認長押、而且要有「同鍵再按」或「同手構
    不到」的證據，所以 Tap 的長度從來沒被碰過——但 AI／MIDI 的 note-off 是踏板
    放開的時間，Tap 的長度一樣是踏板長度。
    """

    def _sustained(self, notes):
        """補一顆另一隻手的音：這條規則要求譜面分得出兩隻手（見閘門 1）。"""
        mate = note(0, 50, pitch=40, hand=1, note_type=0, idx=99)
        return model_with(list(notes) + [mate])

    def test_a_tap_with_a_pedal_length_is_trimmed_too(self):
        # 差 32 個半音：手搆不到，所以那 900ms 一定是踏板
        notes = [note(0, 900, pitch=40, note_type=0, idx=0),
                 note(200, 300, pitch=72, note_type=0, idx=1)]
        m = self._sustained(notes)
        self.assertEqual(m.trim_pedal_sustained_holds(), 1, 'Tap 也要裁')
        self.assertEqual(notes[0].end, 160)

    def test_a_wide_run_is_trimmed_but_a_chord_is_not(self):
        """使用者：「如果只是和弦就不用」——按得住的就留著。"""
        wide = [note(i * 200, i * 200 + 1500, pitch=50 + i * 4, note_type=0, idx=i)
                for i in range(8)]                     # 跨 28 個半音，按不住
        m = self._sustained(wide)
        self.assertGreater(m.trim_pedal_sustained_holds(), 0)
        self.assertLess(wide[0].end - wide[0].start, 1500, '大跳的跑動要裁')

        # 四顆、跨 12 個半音（C 大三和弦加八度），五指和一個八度以內，按得住。
        # 音高要分得開：鍵道是 3 個半音一格，半音相鄰會落在同一格 = 搶同一個鍵。
        chord = [note(i * 200, i * 200 + 1500, pitch=p, note_type=0, idx=i)
                 for i, p in enumerate((60, 64, 67, 72))]
        m2 = self._sustained(chord)
        self.assertEqual(m2.trim_pedal_sustained_holds(), 0, '分解和弦不該被裁')
        self.assertEqual(chord[0].end - chord[0].start, 1500)

    def test_it_leaves_honest_lengths_alone(self):
        notes = [note(0, 150, pitch=40, note_type=0, idx=0),
                 note(200, 350, pitch=72, note_type=0, idx=1)]
        m = self._sustained(notes)
        self.assertEqual(m.trim_pedal_sustained_holds(), 0)
        self.assertEqual(notes[0].end, 150)

    def test_the_other_hand_does_not_shorten_anything(self):
        notes = [note(0, 2000, pitch=40, hand=1, note_type=2, idx=0),
                 note(200, 280, pitch=72, hand=0, note_type=0, idx=1),
                 note(400, 480, pitch=74, hand=0, note_type=0, idx=2)]
        m = model_with(notes)
        self.assertEqual(m.trim_pedal_sustained_holds(), 0)
        self.assertEqual(notes[0].end, 2000)

    def test_the_gap_never_eats_more_than_a_quarter(self):
        run = [note(i * 53, i * 53 + 900, pitch=50 + i * 4, note_type=0, idx=i)
               for i in range(8)]
        m = self._sustained(run)
        m.trim_pedal_sustained_holds()
        kept = run[0].end - run[0].start
        self.assertGreaterEqual(kept, 53 * 3 // 4, '留下的長度不該被間隔吃光')

    def test_a_selection_limits_what_gets_trimmed(self):
        # 音程差 32 個半音：一隻手搆不到，兩顆都該裁
        a = note(0, 2000, pitch=40, note_type=0, idx=0)
        b = note(200, 2200, pitch=72, note_type=0, idx=1)
        c = note(400, 480, pitch=74, note_type=0, idx=2)
        m = self._sustained([a, b, c])
        self.assertEqual(m.trim_pedal_sustained_holds(only=[b]), 1)
        self.assertEqual(a.end, 2000, '沒選到的不該動')

    def test_a_single_hand_chart_is_never_touched(self):
        """兩手擠在同一軌的譜：手指數和跨度會算成一隻手背全部，整份被切碎。"""
        run = [note(i * 200, i * 200 + 1500, pitch=50 + i * 4, note_type=0, idx=i)
               for i in range(8)]
        m = model_with(run)                           # 全部 hand=0
        self.assertEqual(m.trim_pedal_sustained_holds(), 0)
        self.assertEqual(run[0].end, 1500)

    def test_a_hand_played_chart_keeps_its_broken_chords(self):
        """只有少數音蓋過同手下一顆 = 分解和弦，不是延音來源。"""
        notes = []
        for i in range(30):                           # 28 顆規矩的
            notes.append(note(i * 200, i * 200 + 150, pitch=60 + (i % 5),
                              note_type=0, idx=i))
        notes.append(note(0, 2000, pitch=40, hand=1, note_type=2, idx=90))
        notes.append(note(400, 3000, pitch=43, hand=1, note_type=2, idx=91))
        notes.append(note(800, 900, pitch=45, hand=1, note_type=0, idx=92))
        m = model_with(notes)
        self.assertEqual(m.trim_pedal_sustained_holds(), 0,
                         '分解和弦不該被裁（界線是 release_deadline，不是下一顆）')
        self.assertEqual(notes[-2].end, 3000)

    def test_the_fix_survives_a_json_round_trip(self):
        """使用者：「轉成 JSON 要維持修好樣子」。"""
        import json
        import os
        import tempfile
        run = [note(i * 200, i * 200 + 1500, pitch=50 + i * 4, note_type=0, idx=i)
               for i in range(8)]
        m = self._sustained(run)
        self.assertGreater(m.trim_pedal_sustained_holds(), 0)
        fixed = [(int(n.start), int(n.end), int(n.note_type))
                 for n in sorted(m.notes_tree, key=lambda x: (int(x.start), int(x.pitch)))]

        path = os.path.join(tempfile.mkdtemp(), 'x.json')
        m.save_json(path)
        with open(path, encoding='utf-8') as fh:          # 檔案裡就是修好的長度
            raw = json.load(fh)
        self.assertEqual(sorted((n['startTime'], n['endTime']) for n in raw['notes']),
                         sorted((s, e) for s, e, _t in fixed))

        back = NoteModel()
        back.load_json(path)
        back.rebuild_display_cache()
        self.assertEqual(
            [(int(n.start), int(n.end), int(n.note_type))
             for n in sorted(back.notes_tree, key=lambda x: (int(x.start), int(x.pitch)))],
            fixed, '讀回來要和存出去的一樣')

    def test_reloading_does_not_trim_again(self):
        """讀回來不該再裁一次——修好的譜再開一次不能越開越短。"""
        import os
        import tempfile
        run = [note(i * 200, i * 200 + 1500, pitch=50 + i * 4, note_type=0, idx=i)
               for i in range(8)]
        m = self._sustained(run)
        m.trim_pedal_sustained_holds()
        path = os.path.join(tempfile.mkdtemp(), 'x.json')
        m.save_json(path)
        back = NoteModel()
        back.load_json(path)
        back.rebuild_display_cache()
        before = [(int(n.start), int(n.end)) for n in back.notes_tree]
        back.trim_pedal_sustained_holds()
        self.assertEqual([(int(n.start), int(n.end)) for n in back.notes_tree], before)

    def test_trimming_happens_before_the_arranger_sees_the_lengths(self):
        """排譜器要看到誠實的長度，不然它會為了閃「長條走廊」把鍵道映射扭曲掉。

        實測那份 AI 轉譜第 110 小節的八度交替（89↔101）：排譜後才裁的話
        `_repair_hold_corridors` 反覆把 89 往上推，八度從 4 條塌成 1 條；
        先裁再排譜就維持住。全譜「方向相反或八度壓扁」從 2.4% 降到 0.1%。

        關鍵是單軌來源那時候還沒分手，所以要先做一次暫時的分手。
        """
        # 16 顆、音高 40~100：分完手之後每一隻手還是跨 28 個半音，按不住
        run = [note(i * 200, i * 200 + 1500, pitch=40 + i * 4, note_type=0, idx=i)
               for i in range(16)]
        m = model_with(run)                           # 全部 hand=0
        self.assertEqual(len({int(n.hand) for n in m.notes_tree}), 1, '前提：單手')
        self.assertEqual(m.trim_pedal_sustained_holds(), 0, '沒分手之前裁不了')
        self.assertGreater(m._provisional_hands_for_trim(), 0)
        self.assertEqual(len({int(n.hand) for n in m.notes_tree}), 2,
                         '暫時分手之後裁切才有手可以判')
        self.assertGreater(m.trim_pedal_sustained_holds(), 0)

    def test_the_provisional_split_leaves_a_two_handed_chart_alone(self):
        notes = [note(0, 100, pitch=40, hand=1, note_type=0, idx=0),
                 note(100, 200, pitch=72, hand=0, note_type=0, idx=1)]
        m = model_with(notes)
        self.assertEqual(m._provisional_hands_for_trim(), 0, '已經分好手就別動')
        self.assertEqual([int(n.hand) for n in notes], [1, 0])
