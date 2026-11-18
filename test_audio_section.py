# -*- coding: utf-8 -*-
"""只採一小段：從音檔聽出來的音符，只把選定的那一段搬進現有譜面。

使用者：「加入一個 只採某個小部分的音檔生成的 MIDI 部分」。

這裡釘住的是三類**改掉也不會當掉、只會悄悄變糟**的事：

1. 「只採那一段」是字面意思——範圍外一顆都不准動。
2. 幾個靠實測定下來的門檻（最短 20 ms、同音同刻的容差、尾巴切在邊界），
   還有它們互相之間的順序（太短的不可以去認領現有音符的額度）。
3. 擺進譜面那一串的性質：沿用譜面的鍵寬、沒分手就標無主音、一次 Ctrl+Z 回去。

量測細節寫在 `qt_editor/audio_section.py` 的模組說明裡。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import audio_pedal as AP
from qt_editor import audio_section as AS

_app = QApplication.instance() or QApplication([])


def tune(count=160, step=250.0, start=0.0, seed=7):
    """不規律的起音時間——像真的音樂。

    完全等距的格子不能拿來測對位：相鄰的兩顆差距一樣，直方圖會有好幾個一樣高
    的峰（「每顆音都配到隔壁那一顆」也是一組全首自洽的假對位）。真實的曲子沒有
    這種對稱性，所以刻意把節奏弄散。
    """
    out = []
    now = float(start)
    state = seed
    for _ in range(count):
        out.append(now)
        state = (state * 1103515245 + 12345) % 2147483648
        now += step * (0.6 + 0.8 * (state % 1000) / 1000.0)
    return out


def flat(offset_ms=0.0):
    """整份平移的對位（沒有漂移錨點）。"""
    return AP.Alignment(offset_ms=offset_ms)


def src(items):
    """`(起, 迄, 音高)` 或 `(起, 迄, 音高, 力度)` → `audio_section.Note`。"""
    out = []
    for item in items:
        start, end, pitch = item[0], item[1], item[2]
        velocity = item[3] if len(item) > 3 else 80
        out.append(AS.Note(float(start), float(end), int(pitch), int(velocity)))
    return out


class OnlyThatStretchTests(unittest.TestCase):
    """「只採一小段」的字面意思：範圍之外的一顆都不要。"""

    def test_an_onset_outside_the_range_is_left_alone(self):
        got = AS.pick(src([(100, 300, 60), (5000, 5200, 61), (9000, 9200, 62)]),
                      flat(), 4000, 6000)
        self.assertEqual([n.pitch for n in got.notes], [61])
        self.assertEqual(got.outside, 2)

    def test_the_boundaries_are_half_open(self):
        """起點算在裡面、終點不算——相鄰的兩段才不會重複採到同一顆。"""
        got = AS.pick(src([(4000, 4100, 60), (6000, 6100, 61)]),
                      flat(), 4000, 6000)
        self.assertEqual([n.pitch for n in got.notes], [60])

    def test_times_land_on_the_chart_timeline(self):
        """音檔時間要換算成譜面時間，不是原封不動搬過來。"""
        got = AS.pick(src([(4600, 4800, 60)]), flat(400.0), 4000, 6000)
        self.assertEqual(len(got.notes), 1)
        self.assertAlmostEqual(got.notes[0].start, 5000.0)
        self.assertAlmostEqual(got.notes[0].end, 5200.0)

    def test_drift_anchors_are_honoured(self):
        """錨點之間線性內插——整首慢慢跑掉的時候單一個位移接不住。"""
        alignment = AP.Alignment(offset_ms=0.0, anchors=[(0, 0), (10000, 9000)])
        got = AS.pick(src([(4500, 4600, 60)]), alignment, 0, 10000)
        self.assertEqual(len(got.notes), 1)
        self.assertAlmostEqual(got.notes[0].start, 5000.0)


class TheModelsCrumbsTests(unittest.TestCase):
    """太短的音是模型的碎音。下限 20 ms 是掃出來的（見 `MIN_MS`）。"""

    def test_crumbs_are_dropped(self):
        got = AS.pick(src([(1000, 1005, 60), (2000, 2100, 61)]), flat(), 0, 5000)
        self.assertEqual([n.pitch for n in got.notes], [61])
        self.assertEqual(got.short, 1)

    def test_the_floor_is_twenty_milliseconds(self):
        """0~20 ms 是免費的、30 開始丟真音符，所以門檻釘在這裡。"""
        self.assertEqual(AS.MIN_MS, 20)
        kept = AS.pick(src([(1000, 1020, 60)]), flat(), 0, 5000)
        self.assertEqual(len(kept.notes), 1)
        dropped = AS.pick(src([(1000, 1019, 60)]), flat(), 0, 5000)
        self.assertEqual(len(dropped.notes), 0)

    def test_a_crumb_does_not_spend_an_existing_note(self):
        """碎音被丟掉，它本來會認領的那一顆要留給後面真的會進來的候選。

        不然一顆雜訊就能讓真正重複的那一顆溜進去，變成同一刻同音高兩顆。
        """
        got = AS.pick(src([(1000, 1002, 60), (1010, 1300, 60)]),
                      flat(), 0, 5000, avoid=[(1005.0, 60)])
        self.assertEqual(len(got.notes), 0)
        self.assertEqual((got.short, got.duplicate), (1, 1))


class NotWritingItTwiceTests(unittest.TestCase):
    """「留著現有的、只補沒有的」：同一刻同音高不放第二次。"""

    def test_the_same_note_is_skipped(self):
        got = AS.pick(src([(1000, 1300, 60)]), flat(), 0, 5000,
                      avoid=[(1030.0, 60)])
        self.assertEqual(len(got.notes), 0)
        self.assertEqual(got.duplicate, 1)

    def test_a_different_pitch_at_the_same_instant_is_kept(self):
        """同一刻的和絃本來就有好幾顆，只有同音高才叫重複。"""
        got = AS.pick(src([(1000, 1300, 67)]), flat(), 0, 5000,
                      avoid=[(1000.0, 60)])
        self.assertEqual(len(got.notes), 1)

    def test_the_same_pitch_far_away_is_kept(self):
        got = AS.pick(src([(3000, 3300, 60)]), flat(), 0, 5000,
                      avoid=[(1000.0, 60)])
        self.assertEqual(len(got.notes), 1)
        self.assertEqual(got.duplicate, 0)

    def test_one_existing_note_absorbs_only_one(self):
        """譜面上那一顆只能擋掉一顆——重複的音檔音符還有第二顆就該進來。"""
        got = AS.pick(src([(1000, 1100, 60), (1040, 1200, 60)]),
                      flat(), 0, 5000, avoid=[(1000.0, 60)])
        self.assertEqual(len(got.notes), 1)
        self.assertEqual(got.duplicate, 1)

    def test_an_empty_avoid_list_takes_everything(self):
        got = AS.pick(src([(1000, 1300, 60), (1000, 1300, 64)]), flat(), 0, 5000)
        self.assertEqual(len(got.notes), 2)
        self.assertEqual(got.duplicate, 0)


class TailsStayInsideTests(unittest.TestCase):
    """AI 的音長是**踏板長度**，一顆就可能蓋過範圍外他自己寫的音符。"""

    def test_a_tail_past_the_edge_is_cut(self):
        got = AS.pick(src([(5900, 9000, 60)]), flat(), 4000, 6000)
        self.assertEqual(got.clipped, 1)
        self.assertAlmostEqual(got.notes[0].end, 6000.0)

    def test_a_cut_note_still_has_a_length(self):
        """切在邊界上也不能變成零長度（遊戲畫不出來）。"""
        got = AS.pick(src([(5999, 9000, 60)]), flat(), 4000, 6000)
        self.assertGreater(got.notes[0].end, got.notes[0].start)

    def test_a_note_that_rounds_onto_the_edge_is_not_taken(self):
        """譜面的時間是**整數**毫秒，所以範圍要用整數判斷。

        起音 5999.6 進了譜面就是 6000——那已經是範圍外，而那一刻站著他自己
        寫的音符。實測沒擋的話，這顆 1 ms 的碎片會把對方的鍵道推走
        （5 份譜有 2 份中招），正是這個功能最不該發生的事。
        """
        got = AS.pick(src([(5999.6, 9000, 60)]), flat(), 4000, 6000)
        self.assertEqual(len(got.notes), 0)
        self.assertEqual(got.outside, 1)

    def test_the_times_are_whole_milliseconds(self):
        got = AS.pick(src([(4999.6, 5200.4, 60)]), flat(), 4000, 6000)
        self.assertEqual((got.notes[0].start, got.notes[0].end), (5000.0, 5200.0))

    def test_clipping_can_be_turned_off(self):
        got = AS.pick(src([(5900, 9000, 60)]), flat(), 4000, 6000, clip=False)
        self.assertEqual(got.clipped, 0)
        self.assertAlmostEqual(got.notes[0].end, 9000.0)


class ItGetsOutOfTheWayTests(unittest.TestCase):
    """`settle`：採進來的要自己閃開他已經寫好的音符，不准推對方。"""

    @staticmethod
    def note(start, lane, width=3, pitch=60):
        from qt_editor.models import GNote

        made = GNote(None, 0)
        made.start = start
        made.end = start + 100
        made.pitch = pitch
        made.min_key, made.max_key = lane, lane + width - 1
        return made

    def lanes(self, notes):
        return [(n.min_key, n.max_key) for n in notes]

    def test_a_wall_never_moves(self):
        wall = self.note(1000, 5)
        mine = self.note(1000, 5, pitch=72)
        AS.settle([mine], [wall])
        self.assertEqual(self.lanes([wall]), [(5, 7)])

    def test_the_new_one_steps_aside(self):
        mine = self.note(1000, 5, pitch=72)
        moved = AS.settle([mine], [self.note(1000, 5)])
        self.assertEqual(moved, 1)
        self.assertNotEqual(self.lanes([mine]), [(5, 7)])

    def test_it_takes_the_nearest_free_lane(self):
        mine = self.note(1000, 5, pitch=72)
        AS.settle([mine], [self.note(1000, 5)])
        self.assertIn(self.lanes([mine])[0], [(2, 4), (8, 10)])

    def test_a_clear_lane_is_left_alone(self):
        mine = self.note(1000, 12, pitch=72)
        self.assertEqual(AS.settle([mine], [self.note(1000, 5)]), 0)
        self.assertEqual(self.lanes([mine]), [(12, 14)])

    def test_another_instant_is_not_a_wall(self):
        mine = self.note(1000, 5, pitch=72)
        self.assertEqual(AS.settle([mine], [self.note(2000, 5)]), 0)

    def test_the_new_ones_also_avoid_each_other(self):
        first = self.note(1000, 5, pitch=60)
        second = self.note(1010, 5, pitch=72)
        AS.settle([first, second], [])
        self.assertNotEqual(self.lanes([first]), self.lanes([second]))

    def test_no_room_beats_going_out_of_bounds(self):
        """擠不下的時候原地重疊——鍵道 28 以上是壞資料，重疊只是不好打。"""
        from qt_editor.models import TOTAL_GAME_KEYS

        walls = [self.note(1000, lane, width=1) for lane in range(TOTAL_GAME_KEYS)]
        mine = self.note(1000, 5, pitch=72)
        AS.settle([mine], walls)
        self.assertEqual(self.lanes([mine]), [(5, 7)])


class IsThisTranscriptionWorthUsingTests(unittest.TestCase):
    """`reproduced`：這份轉譜在他**已經寫好**的地方重現了幾成。

    實測那個數字幾乎就是空白段落會採到的比例（100%→97、99%→98、90%→89、
    34%→33），所以它是這個功能唯一的品質閘門，不能壞。
    """

    def test_a_perfect_transcription_reproduces_everything(self):
        chart = [(float(i) * 500, 60 + i % 5) for i in range(20)]
        source = src([(t, t + 200, p) for t, p in chart])
        self.assertEqual(AS.reproduced(chart, source), (20, 20))

    def test_the_wrong_pitch_counts_for_nothing(self):
        chart = [(1000.0, 60)]
        self.assertEqual(AS.reproduced(chart, src([(1000, 1200, 61)])), (0, 1))

    def test_a_trill_cannot_claim_the_same_note_twice(self):
        """同一顆轉譜音符只能認領一顆譜面音符，不然顫音會把分數灌水。"""
        chart = [(1000.0, 60), (1030.0, 60), (1060.0, 60)]
        got = AS.reproduced(chart, src([(1000, 1100, 60)]))
        self.assertEqual(got, (1, 3))

    def test_being_late_within_tolerance_still_counts(self):
        """吸附到格線上的譜面本身就會偏半個十六分音符。"""
        chart = [(1000.0, 60)]
        self.assertEqual(AS.reproduced(chart, src([(1070, 1200, 60)])), (1, 1))
        self.assertEqual(AS.reproduced(chart, src([(1200, 1400, 60)])), (0, 1))


class TheChartsOwnKeyWidthTests(unittest.TestCase):
    """沿用譜面的鍵寬是鍵道上最大的單一改善（完全相同 20.8% → 30.2%）。"""

    def test_it_takes_the_commonest_width(self):
        self.assertEqual(AS.dominant_width(self.notes([4, 4, 4, 3])), 4)

    def test_an_empty_chart_falls_back(self):
        self.assertEqual(AS.dominant_width([]), 3)
        self.assertEqual(AS.dominant_width([], fallback=2), 2)

    def test_junk_does_not_crash_it(self):
        self.assertEqual(AS.dominant_width([object(), object()]), 3)

    @staticmethod
    def notes(widths):
        from qt_editor.models import GNote

        out = []
        for width in widths:
            note = GNote(None, 0)
            note.min_key, note.max_key = 5, 5 + width - 1
            out.append(note)
        return out


class CountingWhatHappenedTests(unittest.TestCase):

    def test_inside_is_everything_the_audio_had_there(self):
        got = AS.pick(src([(1000, 1300, 60), (1100, 1105, 61), (1200, 1500, 62),
                           (9000, 9300, 63)]),
                      flat(), 0, 5000, avoid=[(1200.0, 62)])
        self.assertEqual((len(got), got.short, got.duplicate, got.outside),
                         (1, 1, 1, 1))
        self.assertEqual(got.inside, 3)


# ── 擺進譜面 ──────────────────────────────────────────────────────────

class PuttingItIntoTheChartTests(unittest.TestCase):
    """整條路跑一遍。最重要的那一條是「範圍外一顆都沒動」。"""

    BAR_MS = 2000.0             # 120 BPM、4/4
    RANGE = (0.0, 16000.0)      # 對話框沒有選取時的預設：第 1~8 小節

    def chart(self):
        from qt_editor.models import GNote, NoteModel

        model = NoteModel()
        notes = []
        for index, when in enumerate(tune(160)):
            note = GNote(None, index)
            note.start = int(when)
            note.end = note.start + 200
            note.gate = 200
            note.pitch = 48 + (index % 25)
            note.velocity = 70 + (index % 10)   # 90 留給採進來的當記號
            note.hand = index % 2
            note.min_key = 3 + (index % 8)
            note.max_key = note.min_key + 3      # 整份都是寬 4，像他的 Hard 譜
            note.note_type = 0
            notes.append(note)
        model.notes_tree = notes
        # 小節線：對話框要靠它把「第幾小節」換成毫秒
        model.json_meta = {'beat_timings': [i * 500 for i in range(100)]}
        model.rebuild_display_cache()
        return model

    @staticmethod
    def snapshot(notes):
        return [(n.start, n.end, n.pitch, n.velocity, n.hand,
                 n.min_key, n.max_key, n.note_type) for n in notes]

    def run_tool(self, accept=True, replace=False, split=True, by_example=True,
                 extra=()):
        import contextlib
        import io as _io
        from unittest import mock
        from PyQt5.QtWidgets import QDialog
        from qt_editor.main_window import MainWindow
        from qt_editor.audio_section_dialog import AudioSectionDialog

        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        self.addCleanup(lambda: (setattr(win.view.model, 'dirty', False), win.close()))
        win.view.model = self.chart()
        model = win.view.model
        win.view.selected = set()

        # 音檔比譜面早 400 ms，而且多聽到一些他沒寫的音（真實的情形：
        # 轉譜是完整的演奏，譜面是它的減量版）
        # 採進來的音符力度一律 90，譜面自己是 70~79——這樣事後分得出哪些是新的
        source = src([(n.start - 400, n.end - 400, n.pitch, 90)
                      for n in model.notes_tree]
                     + [(a, b, p, 90) for a, b, p in extra])

        def choose(dialog):
            if isinstance(dialog, AudioSectionDialog):
                dialog._wipe.setChecked(replace)
                dialog._split.setChecked(split)
                if not by_example:
                    dialog._by_example.setChecked(False)
            return QDialog.Accepted if accept else QDialog.Rejected

        before = self.snapshot(model.notes_tree)
        with mock.patch.object(win, '_pick_transcribe_audio', return_value='a_piano.wav'), \
                mock.patch.object(win, '_ai_midi_ready', return_value=True), \
                mock.patch('qt_editor.audio_section.read_notes', return_value=source), \
                mock.patch('PyQt5.QtWidgets.QDialog.exec_', new=choose), \
                mock.patch('PyQt5.QtWidgets.QMessageBox.information'):
            win.audio_section_dialog()
        return win, model, before

    # ── 範圍 ──────────────────────────────────────────────────────────

    def test_notes_arrive(self):
        _win, model, before = self.run_tool(
            extra=[(1600, 1800, 90), (3600, 3800, 91)])
        self.assertGreater(len(model.notes_tree), len(before))

    def test_nothing_outside_the_range_is_touched(self):
        """這是整個功能的招牌。它壞掉的話「只採一小段」就是假的。"""
        _win, model, before = self.run_tool(
            replace=True, extra=[(1600, 1800, 90), (20000, 20200, 91)])
        lo, hi = self.RANGE
        kept = self.snapshot([n for n in model.notes_tree if not (lo <= n.start < hi)])
        want = [row for row in before if not (lo <= row[0] < hi)]
        self.assertEqual(kept, want)

    def test_keeping_the_existing_notes_keeps_all_of_them(self):
        _win, model, before = self.run_tool(extra=[(1600, 1800, 90)])
        lo, hi = self.RANGE
        self.assertEqual(
            len([row for row in before if lo <= row[0] < hi]),
            len([n for n in model.notes_tree
                 if lo <= n.start < hi and n.velocity != 90]))

    def test_replacing_clears_the_range_first(self):
        _win, model, before = self.run_tool(replace=True)
        lo, hi = self.RANGE
        # 原本那些音符（力度 70~109、來源是 90）在範圍內應該都不見了；
        # 範圍外的數量不變。
        self.assertEqual(len([row for row in before if not (lo <= row[0] < hi)]),
                         len([n for n in model.notes_tree
                              if not (lo <= n.start < hi)]))
        self.assertTrue(all(n.velocity == 90 for n in model.notes_tree
                            if lo <= n.start < hi))

    def test_keeping_them_keeps_their_lanes_too(self):
        """「留著現有的」包含鍵道。採進來的要自己閃開，不是把他的推走。"""
        _win, model, before = self.run_tool(extra=[(1600, 1800, 90)])
        lo, hi = self.RANGE
        theirs = sorted((n.start, n.min_key, n.max_key) for n in model.notes_tree
                        if lo <= n.start < hi and n.velocity != 90)
        want = sorted((row[0], row[5], row[6]) for row in before
                      if lo <= row[0] < hi)
        self.assertEqual(theirs, want)

    def test_cancelling_changes_nothing(self):
        _win, model, before = self.run_tool(accept=False)
        self.assertEqual(self.snapshot(model.notes_tree), before)

    def test_one_undo_puts_it_back(self):
        win, model, before = self.run_tool(extra=[(1600, 1800, 90)])
        win.view.undo()
        self.assertEqual(self.snapshot(model.notes_tree), before)

    # ── 擺的方式 ──────────────────────────────────────────────────────

    def test_it_inherits_the_charts_key_width(self):
        _win, model, _before = self.run_tool(extra=[(1600, 1800, 90)])
        new = [n for n in model.notes_tree if n.velocity == 90]
        self.assertTrue(new)
        self.assertEqual({n.max_key - n.min_key + 1 for n in new}, {4})

    def test_everything_stays_on_the_keyboard(self):
        from qt_editor.models import TOTAL_GAME_KEYS

        _win, model, _before = self.run_tool(
            extra=[(1600, 1800, 21), (1700, 1900, 108)])
        for note in model.notes_tree:
            self.assertGreaterEqual(note.min_key, 0)
            self.assertLess(note.max_key, TOTAL_GAME_KEYS)

    def test_without_the_split_they_are_ownerless(self):
        """分不出手就標 hand=2（官方資料本來就有這個值），不要假裝是右手。"""
        _win, model, _before = self.run_tool(split=False,
                                             extra=[(1600, 1800, 90)])
        new = [n for n in model.notes_tree if n.velocity == 90]
        self.assertTrue(new)
        self.assertEqual({n.hand for n in new}, {2})

    def test_with_the_split_they_have_a_hand(self):
        _win, model, _before = self.run_tool(extra=[(1600, 1800, 90)])
        new = [n for n in model.notes_tree if n.velocity == 90]
        self.assertTrue(set(n.hand for n in new) <= {0, 1})

    def test_long_notes_become_holds(self):
        """長度門檻和整首匯入 MIDI 同一條規則（`LONG_MS`）。

        不比對 `gate`：擺進去之後還會裁一次踏板殘響，長度會變短（型別不會
        跟著變，和整首匯入的行為一致）。
        """
        _win, model, _before = self.run_tool(extra=[(1600, 2800, 90)])
        new = [n for n in model.notes_tree if n.velocity == 90]
        self.assertTrue(any(int(n.note_type) & 2 for n in new))

    def test_the_new_notes_are_selected(self):
        """採進來的都選起來——不要的直接 Delete。"""
        win, model, _before = self.run_tool(extra=[(1600, 1800, 90)])
        new = {n.idx for n in model.notes_tree if n.velocity == 90}
        self.assertTrue(new)
        self.assertEqual(win.view.selected, new)

    def test_indices_stay_in_order(self):
        _win, model, _before = self.run_tool(extra=[(1600, 1800, 90)])
        self.assertEqual([n.idx for n in model.notes_tree],
                         list(range(len(model.notes_tree))))

    # ── 對話框 ────────────────────────────────────────────────────────

    def test_the_dialog_reports_the_same_number_as_the_module(self):
        """閘門的數字是使用者唯一的判斷依據，兩邊必須一致。"""
        from qt_editor.audio_section_dialog import AudioSectionDialog

        model = self.chart()
        source = src([(n.start - 400, n.end - 400, n.pitch, 90)
                      for n in model.notes_tree])
        alignment = AS.align([float(n.start) for n in model.notes_tree], source)
        dialog = AudioSectionDialog(None, model=model, source=source,
                                    alignment=alignment)
        self.addCleanup(dialog.deleteLater)
        chart = [(float(n.start), int(n.pitch)) for n in model.notes_tree]
        self.assertEqual((dialog._hit, dialog._total),
                         AS.reproduced(chart, AS.mapped(source, alignment)))
        self.assertGreater(dialog._hit, 0.9 * dialog._total)

    def test_the_measure_range_becomes_milliseconds(self):
        from qt_editor.audio_section_dialog import AudioSectionDialog

        model = self.chart()
        source = src([(1000, 1200, 60)])
        dialog = AudioSectionDialog(None, model=model, source=source, alignment=flat())
        self.addCleanup(dialog.deleteLater)
        dialog._rb_meas.setChecked(True)
        dialog._first.setValue(3)
        dialog._last.setValue(4)
        self.assertEqual(dialog.range_ms(), (2 * self.BAR_MS, 4 * self.BAR_MS))

    def test_a_selection_wins_when_there_is_one(self):
        from qt_editor.audio_section_dialog import AudioSectionDialog

        model = self.chart()
        dialog = AudioSectionDialog(None, model=model, source=src([(1000, 1200, 60)]),
                                    alignment=flat(), selection=(5000.0, 7000.0))
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.range_ms(), (5000.0, 7000.0))

    def test_without_a_selection_that_option_is_off(self):
        from qt_editor.audio_section_dialog import AudioSectionDialog

        dialog = AudioSectionDialog(None, model=self.chart(),
                                    source=src([(1000, 1200, 60)]), alignment=flat())
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._rb_sel.isEnabled())
        self.assertTrue(dialog._rb_meas.isChecked())

    def test_an_empty_stretch_cannot_be_confirmed(self):
        """採不到東西的時候「放進譜面」要按不下去，而不是放進 0 顆。"""
        from qt_editor.audio_section_dialog import AudioSectionDialog

        dialog = AudioSectionDialog(None, model=self.chart(),
                                    source=src([(90000, 90200, 60)]), alignment=flat())
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._ok.isEnabled())


if __name__ == '__main__':
    unittest.main()
