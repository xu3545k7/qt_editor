# -*- coding: utf-8 -*-
"""從 MIDI 還原音高：偏移偵測與「選錯檔案」的防線。

譜面的 `start_timing_msec` 直接沿用 MIDI 的起音時間，所以兩邊可以用時間對齊。
但實際的檔案有兩種偏移會讓對齊失效：

* **時間偏移**——譜面開頭補過空白（曲庫裡有 +3600 / +6700 / +6850ms 的例子），
  整份往後平移，絕對時間永遠對不上。
* **音高偏移**——手上的 MIDI 是移調版。時間對得上、音高整份差一個固定音程，
  照樣覆蓋下去等於把整首歌移調，而畫面上完全看不出來。

舊版的時間偏移是拿**前 8 組依序相減**猜的，只在兩邊開頭一顆不差時成立。譜面
開頭少兩組音，8 個候選就全部歪掉同一個量，真正的平移量根本不在候選裡——實測
只有 5.8% 的音高是對的，卻回報 changed=1952 看起來像成功了一半。

還有一道更重要的防線：**選錯 MIDI 不能寫下去**。40ms 的容錯窗夠寬，兩首密度
相近的曲子有一半的發音點會巧合對上，光看「對上幾組」擋不住。
"""

import unittest

from qt_editor.models import GNote, NoteModel, PitchRestore


def chart_from(events, shift=0, pitch=None):
    """events: [(start_ms, [pitch, ...]), ...] → 一份譜面。"""
    m = NoteModel.create_new('t', 120.0, 60.0, 4)
    m.ensure_precise_beat_grid()
    notes = []
    for when, pitches in events:
        for lane, p in enumerate(sorted(pitches)):
            n = GNote(None, len(notes))
            n.start = int(when) + shift
            n.end = n.start + 200
            n.gate = 200
            n.min_key, n.max_key = lane * 2, lane * 2 + 1
            n.note_type, n.hand = 0, 0
            n.velocity = 90
            n.pitch = p if pitch is None else pitch
            notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def song(groups=200, seed=1):
    """一段有變化的假曲子：時間不等距、和弦顆數不一。"""
    import random
    rng = random.Random(seed)
    events, when = [], 0
    for i in range(groups):
        when += rng.choice((125, 250, 250, 375, 500))
        size = rng.choice((1, 1, 2, 2, 3))
        base = 48 + rng.randrange(0, 24)
        events.append((when, sorted({base + rng.randrange(0, 13)
                                     for _ in range(size)})))
    return events


def restore(chart, reference_events, **kw):
    """把 load_midi 換成「直接產生參考音符」再呼叫還原。

    真的寫一個 MIDI 檔也可以，但那樣測到的是 MIDI 解析而不是對齊邏輯，而且
    tempo 換算會把「精確的毫秒」變成「接近的毫秒」，看不出偏移偵測本身準不準。
    """
    real = NoteModel.load_midi

    def patched(model, path, auto_arrange=True, **kwargs):
        model.notes_tree = chart_from(reference_events).notes_tree
        model.file_format = 'midi'
        return None

    NoteModel.load_midi = patched
    try:
        return chart.restore_pitches_from_midi('fake.mid', **kw)
    finally:
        NoteModel.load_midi = real


class TimeOffsetTests(unittest.TestCase):
    """平移量要量得出來，而且不能只靠開頭那幾組。"""

    def setUp(self):
        self.events = song()

    def broken(self, **kw):
        """音高全壞掉的譜面。"""
        return chart_from(self.events, pitch=60, **kw)

    def correct(self, chart):
        want = [p for _when, ps in self.events for p in sorted(ps)]
        got = [n.pitch for n in chart.notes_tree]
        return sum(1 for a, b in zip(got, want) if a == b) / float(len(want))

    def test_no_offset(self):
        c = self.broken()
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, 0)
        self.assertEqual(self.correct(c), 1.0)

    def test_a_shifted_chart_is_detected(self):
        c = self.broken(shift=3600)
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, 3600)
        self.assertEqual(self.correct(c), 1.0)

    def test_a_negative_shift_is_detected(self):
        c = self.broken(shift=-1043)
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, -1043)
        self.assertEqual(self.correct(c), 1.0)

    def test_a_shift_survives_a_missing_opening(self):
        """舊版在這裡崩掉：前 8 組的差全部歪同一個量。"""
        c = chart_from(self.events[3:], shift=3600, pitch=60)
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, 3600, '開頭少了 3 組就量不出平移量')
        want = [p for _when, ps in self.events[3:] for p in sorted(ps)]
        got = [n.pitch for n in c.notes_tree]
        self.assertEqual(got, want)

    def test_a_shift_survives_extra_notes_at_the_front(self):
        extra = [(self.events[0][0] - 700, [70])]
        c = chart_from(extra + self.events, shift=6850, pitch=60)
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, 6850)

    def test_a_shift_survives_holes_in_the_middle(self):
        thinned = [e for i, e in enumerate(self.events) if i % 7]
        c = chart_from(thinned, shift=3600, pitch=60)
        r = restore(c, self.events)
        self.assertEqual(r.offset_ms, 3600)

    def test_a_tiny_chart_does_not_crash(self):
        events = [(0, [60]), (500, [62])]
        c = chart_from(events, shift=250, pitch=1)
        r = restore(c, events)
        self.assertIsInstance(r, PitchRestore)


class SemitoneOffsetTests(unittest.TestCase):
    """MIDI 是移調版的話要講出來——覆蓋下去整首會走音，畫面上看不出來。"""

    def setUp(self):
        self.events = song(seed=3)

    def transposed(self, semitones):
        """譜面音高正確，但比 MIDI 低 `semitones`。"""
        shifted = [(w, [p - semitones for p in ps]) for w, ps in self.events]
        return chart_from(shifted)

    def test_a_transposed_midi_is_reported(self):
        r = restore(self.transposed(20), self.events)
        self.assertEqual(r.semitones, 20)

    def test_a_downward_transposition_is_reported(self):
        r = restore(self.transposed(-12), self.events)
        self.assertEqual(r.semitones, -12)

    def test_a_matching_midi_reports_no_transposition(self):
        r = restore(chart_from(self.events), self.events)
        self.assertEqual(r.semitones, 0)

    def test_broken_pitches_are_not_mistaken_for_a_transposition(self):
        """音高壞掉時差值是散的，不該報成移調。"""
        r = restore(chart_from(self.events, pitch=60), self.events)
        self.assertEqual(r.semitones, 0)

    def test_it_still_restores_a_transposed_chart(self):
        """偵測到移調不代表拒絕——只是要講，套用與否是呼叫端決定。"""
        c = self.transposed(20)
        r = restore(c, self.events)
        self.assertTrue(r.applied)
        want = [p for _w, ps in self.events for p in sorted(ps)]
        self.assertEqual([n.pitch for n in c.notes_tree], want)


class WrongMidiTests(unittest.TestCase):
    """對不起來就一顆都不要改。"""

    def setUp(self):
        self.events = song(seed=5)
        self.other = song(seed=99)

    def test_a_different_song_is_refused(self):
        c = chart_from(self.events, pitch=60)
        r = restore(c, self.other)
        self.assertFalse(r.applied, '拿別首的 MIDI 也照寫')

    def test_nothing_is_written_when_refused(self):
        c = chart_from(self.events, pitch=60)
        restore(c, self.other)
        self.assertEqual({n.pitch for n in c.notes_tree}, {60},
                         '明明拒絕了卻還是動到音高')

    def test_the_chord_sizes_give_it_away(self):
        """光看「對上幾組」擋不住——顆數一致的比例才是決定性的。"""
        c = chart_from(self.events, pitch=60)
        r = restore(c, self.other)
        self.assertLess(r.chord_agreement, 0.5)

    def test_the_right_midi_agrees_on_every_chord(self):
        c = chart_from(self.events, pitch=60)
        r = restore(c, self.events)
        self.assertEqual(r.chord_agreement, 1.0)

    def test_a_hand_edited_chart_is_still_accepted(self):
        """使用者自己動過一些和弦不該被當成選錯檔案。"""
        edited = [(w, ps + [ps[0] + 5]) if i % 5 == 0 else (w, ps)
                  for i, (w, ps) in enumerate(self.events)]
        c = chart_from(edited, pitch=60)
        r = restore(c, self.events)
        self.assertTrue(r.applied, '改過 20%% 的和弦就被拒絕了')

    def test_an_empty_midi_changes_nothing(self):
        c = chart_from(self.events, pitch=60)
        r = restore(c, [])
        self.assertFalse(r.applied)
        self.assertEqual(r.changed, 0)


class DryRunTests(unittest.TestCase):
    """試算：先看會改幾顆，再決定要不要動。"""

    def setUp(self):
        self.events = song(seed=11)

    def test_it_reports_how_many_would_change(self):
        c = chart_from(self.events, pitch=60)
        total = len(c.notes_tree)
        r = restore(c, self.events, apply=False)
        self.assertGreater(r.changed, total * 0.9,
                           '試算報 0 顆，對話框就沒東西可講了')

    def test_it_does_not_touch_the_chart(self):
        c = chart_from(self.events, pitch=60)
        restore(c, self.events, apply=False)
        self.assertEqual({n.pitch for n in c.notes_tree}, {60})

    def test_it_says_it_did_not_apply(self):
        c = chart_from(self.events, pitch=60)
        self.assertFalse(restore(c, self.events, apply=False).applied)

    def test_a_dry_run_still_says_it_would_apply(self):
        """`applied` 在試算時永遠是 False——拿它判斷「對不對得起來」會把
        100% 對上的譜面也擋掉。實測 La Campanella 明明 match 100%、
        agreement 99.7%，對話框還是說「這份 MIDI 和譜面對不起來」。"""
        c = chart_from(self.events, pitch=60)
        r = restore(c, self.events, apply=False)
        self.assertTrue(r.acceptable, '試算沒有說它會套用')
        self.assertFalse(r.applied)

    def test_a_wrong_midi_is_not_acceptable_either(self):
        c = chart_from(self.events, pitch=60)
        r = restore(c, song(seed=77), apply=False)
        self.assertFalse(r.acceptable)

    def test_the_real_run_agrees_with_the_dry_run(self):
        c = chart_from(self.events, pitch=60)
        dry = restore(c, self.events, apply=False)
        real = restore(c, self.events)
        self.assertEqual(dry.acceptable, real.acceptable)
        self.assertTrue(real.applied)

    def test_it_still_measures_the_offsets(self):
        c = chart_from(self.events, shift=3600, pitch=60)
        r = restore(c, self.events, apply=False)
        self.assertEqual(r.offset_ms, 3600)

    def test_the_real_run_changes_the_same_number(self):
        c = chart_from(self.events, pitch=60)
        dry = restore(c, self.events, apply=False)
        real = restore(c, self.events)
        self.assertEqual(dry.changed, real.changed)


class MenuTests(unittest.TestCase):
    """匯出時的警告叫使用者用「從 MIDI 還原音高」——那個入口要真的存在。"""

    def test_the_tool_is_in_the_menu(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.main_window import MainWindow
        _app = QApplication.instance() or QApplication([])
        global _win
        try:
            win = _win
        except NameError:
            win = _win = MainWindow()
        labels = [label
                  for _group, items in win._tool_groups()
                  for label, _fn in items]
        self.assertTrue(any('還原音高' in x for x in labels),
                        '工具選單裡找不到這個功能：%s' % labels)


if __name__ == '__main__':
    unittest.main()
