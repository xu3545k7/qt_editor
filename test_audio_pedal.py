# -*- coding: utf-8 -*-
"""以音檔補上踏板。

使用者：「可以在踏板不合需求或 midi 沒有踏板的情況下，作為一個工具……
基本上就是轉 midi，但是只把踏板拿到譜面上，其餘都不要動。」

這裡釘住的是三件靠實測決定的事，因為它們都是**改掉也不會當掉、只會悄悄變難聽**
的那種：

1. 對位要自己量出來（音檔和譜面的時間軸不一樣），而且量錯的時候要說得出來。
2. 偵測到的「踩下」一律偏晚 +127ms、「放開」只差 +4ms，所以只能動踩下。
3. 除了 `pedal_spans` 以外什麼都不准動。

量測細節寫在 `qt_editor/audio_pedal.py` 的模組說明裡。
"""

import os
import shutil
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import audio_pedal as AP

_app = QApplication.instance() or QApplication([])


def ramp(count=200, step=250, start=0):
    """一串規律的起音時間。"""
    return [float(start + i * step) for i in range(count)]


def tune(count=400, step=250, start=0, seed=7):
    """不規律的起音時間——像真的音樂。

    完全等距的格子不能拿來測漂移：250ms 和 248.75ms 的兩串會慢慢拍頻，
    於是「每顆音都配到隔壁那一顆」也是一組**全首自洽**的假對位，分不出
    真假。真實的曲子沒有這種對稱性，所以這裡刻意把節奏弄散。
    """
    out = []
    state = seed
    when = float(start)
    for _ in range(count):
        state = (state * 1103515245 + 12345) % 2147483648
        out.append(when)
        when += step * (0.5 + (state % 1000) / 500.0)       # 0.5~2.5 倍
    return out


class TheOffsetIsMeasuredNotAssumedTests(unittest.TestCase):
    """音檔和譜面的時間軸不一樣，差多少只能量。"""

    def test_a_constant_offset_comes_back(self):
        chart = ramp()
        source = [t - 1800.0 for t in chart]       # 譜面開頭補了 1.8 秒的靜音
        a = AP.align(chart, source)
        self.assertAlmostEqual(a.offset_ms, 1800.0, delta=5)
        self.assertGreater(a.confidence, 0.95)

    def test_no_offset_is_zero(self):
        chart = ramp()
        a = AP.align(chart, list(chart))
        self.assertAlmostEqual(a.offset_ms, 0.0, delta=5)

    def test_a_negative_offset_comes_back(self):
        chart = ramp()
        source = [t + 640.0 for t in chart]
        self.assertAlmostEqual(AP.align(chart, source).offset_ms, -640.0, delta=5)

    def test_extra_source_notes_do_not_move_it(self):
        """AI 會多轉出一些音符（起音 F1 97%），對位不該被那幾顆牽走。"""
        chart = ramp()
        source = sorted([t - 300.0 for t in chart] + [t - 300.0 + 90 for t in chart[::7]])
        self.assertAlmostEqual(AP.align(chart, source).offset_ms, 300.0, delta=10)

    def test_missing_source_notes_do_not_move_it(self):
        chart = ramp()
        source = [t - 300.0 for t in chart[::3]]
        self.assertAlmostEqual(AP.align(chart, source).offset_ms, 300.0, delta=10)

    def test_nothing_to_align_is_not_a_crash(self):
        self.assertFalse(AP.align([], ramp()).usable)
        self.assertFalse(AP.align(ramp(), []).usable)


class WrongAudioHasToBeVisibleTests(unittest.TestCase):
    """對位一定湊得出答案，選錯音檔也一樣——擋下來的是使用者看到的數字。"""

    def test_the_same_song_is_confident(self):
        chart = ramp()
        a = AP.align(chart, [t - 500.0 for t in chart])
        self.assertTrue(a.trustworthy)

    def test_an_unrelated_audio_is_not(self):
        """別首歌：音符一樣密，但落點對不上。"""
        chart = ramp(count=300, step=250)
        source = ramp(count=300, step=181, start=37)
        self.assertFalse(AP.align(chart, source).trustworthy)

    def test_confidence_is_tighter_than_the_match_ratio(self):
        """±80ms 下連別首歌都撞得到，所以信心值用更緊的容差。"""
        chart = ramp(count=300, step=250)
        a = AP.align(chart, ramp(count=300, step=181, start=37))
        self.assertLessEqual(a.confidence, a.match_ratio)


class DriftIsFollowedTests(unittest.TestCase):
    """譜面是固定 BPM、錄音不是，整首的位移會慢慢跑掉。"""

    def test_a_stretched_timeline_is_remapped(self):
        """實測接得住約 290ms 的總漂移；真實譜面量到的是 12~157ms。"""
        chart = tune(count=400, step=250)                   # 約 0 ~ 145 秒
        source = [t * 0.998 for t in chart]                 # 尾端差 291ms
        a = AP.align(chart, source)
        self.assertGreaterEqual(len(a.anchors), 2)
        self.assertGreater(a.match_ratio, 0.99)
        # 整份只平移一個常數的話，尾巴會差近 300ms——遠超過 ±80ms 的容差，
        # 所以這一定是靠分段的錨點接住的。
        self.assertGreater(a.drift_ms, 200)
        late = AP.remap([[source[-1], source[-1] + 100]], a)[0]
        self.assertAlmostEqual(late[0], chart[-1], delta=15)

    def test_mild_drift_does_not_cry_wolf(self):
        """對得準就不要亂叫。錨點版的預測要真的拿來算信心值。"""
        chart = tune(count=400, step=250)
        a = AP.align(chart, [t * 0.999 for t in chart])     # 尾端差 145ms
        self.assertGreater(a.drift_ms, 100, '這首確實有漂移')
        self.assertTrue(a.trustworthy, '但每顆音都對得上，不該報警')

    def test_drift_beyond_what_it_can_follow_fails_loudly(self):
        """接不住的時候要讓使用者看得到，而不是悄悄把踏板放錯地方。"""
        chart = tune(count=400, step=250)
        a = AP.align(chart, [t * 0.99 for t in chart])      # 尾端差 1.4 秒
        self.assertFalse(a.trustworthy)

    def test_a_flat_timeline_keeps_the_constant(self):
        chart = ramp(count=200)
        a = AP.align(chart, [t - 1200.0 for t in chart])
        moved = AP.remap([[1000.0, 2000.0]], a)[0]
        self.assertAlmostEqual(moved[0], 2200.0, delta=25)

    def test_too_few_anchors_falls_back_to_the_offset(self):
        a = AP.Alignment(offset_ms=750.0, anchors=[])
        self.assertEqual(AP.remap([[10.0, 20.0]], a), [[760.0, 770.0]])


class OnlyThePressIsLateTests(unittest.TestCase):
    """放開的誤差是 +4ms，踩下是 +127ms——物理上就只能動踩下。"""

    def test_the_press_moves_back_and_the_release_does_not(self):
        out = AP.unlag_presses([[1000.0, 3000.0]], lag_ms=120)
        self.assertEqual(out, [[880.0, 3000.0]])

    def test_it_never_crosses_the_previous_release(self):
        """推過頭會吃掉前一段。換踏是『放開的同一瞬間再踩』，不是重疊。"""
        out = AP.unlag_presses([[0.0, 1000.0], [1050.0, 2000.0]], lag_ms=120)
        self.assertEqual(out[1][0], 1001.0)
        self.assertGreater(out[1][0], out[0][1])

    def test_a_short_span_keeps_at_least_a_millisecond(self):
        out = AP.unlag_presses([[1000.0, 1002.0]], lag_ms=5000)
        self.assertLess(out[0][0], out[0][1])

    def test_zero_lag_changes_nothing(self):
        spans = [[0.0, 900.0], [1000.0, 1800.0]]
        self.assertEqual(AP.unlag_presses(spans, lag_ms=0), spans)

    def test_the_calibrated_default_is_the_measured_one(self):
        """120ms 是掃描 0~260 的最佳值，不是隨手填的。改它要重跑量測。"""
        self.assertEqual(AP.PRESS_LAG_MS, 120)


class TidyingDoesNotEatPedalChangesTests(unittest.TestCase):
    def test_true_overlaps_merge(self):
        spans, _ = AP.tidy_spans([[0.0, 1000.0], [900.0, 1500.0]])
        self.assertEqual(spans, [[0.0, 1500.0]])

    def test_a_zero_gap_stays_two_spans(self):
        """間隔 0 代表『這裡要換踏』，併掉等於把整首的和聲糊在一起。"""
        spans, _ = AP.tidy_spans([[0.0, 1000.0], [1000.0, 2000.0]])
        self.assertEqual(len(spans), 2)

    def test_noise_is_dropped_and_counted(self):
        spans, dropped = AP.tidy_spans([[0.0, 900.0], [1000.0, 1010.0]])
        self.assertEqual(len(spans), 1)
        self.assertEqual(dropped, 1)

    def test_everything_outside_the_chart_is_clipped(self):
        """音檔常比譜面長，換算過去的尾巴會跑到譜面之外。"""
        spans = AP.clamp([[-500.0, 400.0], [1000.0, 9000.0], [9500.0, 9800.0]], 5000.0)
        self.assertEqual(spans, [[0.0, 400.0], [1000.0, 5000.0]])


class MergingIntoWhatIsThereTests(unittest.TestCase):
    def test_replace_takes_over(self):
        out, kept = AP.merge_into([[0.0, 1000.0]], [[2000.0, 3000.0]], AP.REPLACE)
        self.assertEqual(out, [[2000.0, 3000.0]])
        self.assertEqual(kept, 0)

    def test_fill_keeps_every_existing_span(self):
        """真人踩過的地方一格都不動，只補空隙。"""
        existing = [[0.0, 1000.0], [5000.0, 6000.0]]
        out, kept = AP.merge_into(existing, [[0.0, 7000.0]], AP.FILL)
        self.assertEqual(kept, 2)
        for span in existing:
            self.assertIn(span, out)

    def test_fill_with_nothing_there_is_just_replace(self):
        out, kept = AP.merge_into([], [[0.0, 1000.0]], AP.FILL)
        self.assertEqual(out, [[0.0, 1000.0]])
        self.assertEqual(kept, 0)


class EndToEndTests(unittest.TestCase):
    def test_a_shifted_source_lands_on_the_chart(self):
        chart = ramp(count=200, step=250)                   # 0 ~ 50 秒
        source_onsets = [t - 2000.0 for t in chart]
        # 音檔時間軸上的踏板，而且踩下刻意晚 120ms（模型就是這樣）
        source_spans = [[1000.0 + i * 2000 + 120, 2800.0 + i * 2000] for i in range(20)]
        report = AP.build(chart, chart[-1] + 500, [], source_onsets, source_spans)

        self.assertTrue(report.alignment.trustworthy)
        self.assertEqual(report.alignment.anchors[0][0] - report.alignment.anchors[0][1],
                         2000)
        # 換算 +2000、踩下再推回 120 → 回到原本的整數秒
        self.assertAlmostEqual(report.spans[0][0], 3000.0, delta=20)
        self.assertGreater(report.coverage, 0)

    def test_the_source_pedal_count_is_reported(self):
        chart = ramp(count=50)
        report = AP.build(chart, 20000.0, [], list(chart),
                          [[0.0, 900.0], [1000.0, 1005.0]])
        self.assertEqual(report.source_spans, 2)
        self.assertEqual(report.dropped_short, 1)


class ReadingTheTranscriptionTests(unittest.TestCase):
    """轉出來的 MIDI 要讀得出 CC64。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_midi(self, events):
        import mido
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        last = 0
        for when_ms, msg in events:
            ticks = int(round(mido.second2tick(when_ms / 1000.0, 480, 500000)))
            track.append(msg.copy(time=ticks - last))
            last = ticks
        path = os.path.join(self.dir, 'x.mid')
        mid.save(path)
        return path

    def test_cc64_becomes_spans_in_milliseconds(self):
        import mido
        path = self.write_midi([
            (0, mido.Message('note_on', note=60, velocity=80)),
            (100, mido.Message('control_change', control=64, value=127)),
            (500, mido.Message('note_off', note=60, velocity=0)),
            (1600, mido.Message('control_change', control=64, value=0)),
        ])
        onsets, spans = AP.read_midi(path)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 100.0, delta=3)
        self.assertAlmostEqual(spans[0][1], 1600.0, delta=3)
        self.assertAlmostEqual(onsets[0], 0.0, delta=3)

    def test_a_midi_without_pedal_gives_no_spans(self):
        import mido
        path = self.write_midi([
            (0, mido.Message('note_on', note=60, velocity=80)),
            (400, mido.Message('note_off', note=60, velocity=0)),
        ])
        _onsets, spans = AP.read_midi(path)
        self.assertEqual(spans, [])


class TheDialogShowsWhatItMeasuredTests(unittest.TestCase):
    def dialog(self, existing=(), confident=True):
        from qt_editor.audio_pedal_dialog import AudioPedalDialog
        chart = ramp(count=200, step=250)
        source = ([t - 400.0 for t in chart] if confident
                  else ramp(count=200, step=181, start=37))
        dlg = AudioPedalDialog(
            None, chart_onsets=chart, chart_end_ms=chart[-1] + 500,
            existing=list(existing), source_onsets=source,
            source_spans=[[600.0 + i * 2000, 2200.0 + i * 2000] for i in range(20)],
            audio_name='song_piano.wav')
        self.addCleanup(dlg.close)
        return dlg

    def test_it_produces_a_report(self):
        dlg = self.dialog()
        self.assertIsNotNone(dlg.report())
        self.assertTrue(dlg.report().spans)

    def test_fill_is_off_when_there_is_no_pedal_to_keep(self):
        self.assertFalse(self.dialog()._fill.isEnabled())

    def test_fill_is_available_when_there_is(self):
        self.assertTrue(self.dialog(existing=[[0.0, 1000.0]])._fill.isEnabled())

    def test_choosing_fill_keeps_the_existing_spans(self):
        dlg = self.dialog(existing=[[0.0, 1000.0]])
        dlg._fill.setChecked(True)
        self.assertEqual(dlg.mode(), AP.FILL)
        self.assertEqual(dlg.report().kept_existing, 1)

    def test_changing_the_lag_moves_the_presses(self):
        dlg = self.dialog()
        dlg._lag.setValue(0)
        late = dlg.report().spans[0][0]
        dlg._lag.setValue(300)
        self.assertAlmostEqual(dlg.report().spans[0][0], late - 300, delta=5)

    def test_a_wrong_audio_is_called_out(self):
        """低信心要用紅字講出來，不是只給一個數字。"""
        dlg = self.dialog(confident=False)
        self.assertFalse(dlg._alignment.trustworthy)
        self.assertIn('color:#c0392b',
                      dlg._confidence_text(dlg._alignment))

    def test_a_good_match_is_not_called_out(self):
        dlg = self.dialog()
        self.assertNotIn('color:#c0392b', dlg._confidence_text(dlg._alignment))

    def test_enter_does_not_commit_a_bad_match(self):
        """警告還是警告，但一路 Enter 不該剛好蓋上一份錯的踏板。"""
        from PyQt5.QtWidgets import QDialogButtonBox
        dlg = self.dialog(confident=False)
        cancel = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel)
        self.assertTrue(cancel.isDefault())
        self.assertTrue(dlg._ok.isEnabled(), '還是要按得下去，只是不預設')

    def test_a_good_match_commits_on_enter(self):
        dlg = self.dialog()
        self.assertTrue(dlg._ok.isDefault())

    def test_a_dry_source_is_called_out(self):
        """對位好卻幾乎沒聽到踏板＝音源沒有延音共鳴，不是對錯了。"""
        from qt_editor.audio_pedal_dialog import AudioPedalDialog
        chart = ramp(count=200, step=250)
        dlg = AudioPedalDialog(
            None, chart_onsets=chart, chart_end_ms=chart[-1] + 500, existing=[],
            source_onsets=[t - 400.0 for t in chart],
            source_spans=[[600.0, 1200.0], [20000.0, 20600.0]],
            audio_name='dry.wav')
        self.addCleanup(dlg.close)
        self.assertTrue(dlg._alignment.trustworthy)
        self.assertLess(dlg.report().coverage, 0.2)
        self.assertIn('沒什麼延音共鳴', dlg._result.text())

    def test_a_normal_source_is_not(self):
        self.assertNotIn('沒什麼延音共鳴', self.dialog()._result.text())


class NothingButThePedalMovesTests(unittest.TestCase):
    """使用者：「只把踏板拿到譜面上，其餘都不要動。」

    這是整個功能的合約。走的是真正的入口 `pedal_from_audio_dialog`，只把
    「選音檔」和「跑轉譜」兩步換掉——那兩步是子程序和檔案框，剩下的全是真的。
    """

    def chart(self):
        from qt_editor.models import GNote, NoteModel
        model = NoteModel.create_new('t', 120.0, 60.0, 4)
        notes = []
        for i in range(60):
            n = GNote(None, i)
            n.start = float(i * 400)
            n.end = n.start + 260
            n.gate = 260
            n.pitch = 48 + (i % 25)
            n.velocity = 40 + (i % 60)
            n.hand = i % 2
            n.min_key = 3 + (i % 10)
            n.max_key = n.min_key + 1
            n.note_type = 0
            notes.append(n)
        model.notes_tree = notes
        model.rebuild_display_cache()
        return model

    @staticmethod
    def snapshot(model):
        return [(n.start, n.end, n.gate, n.pitch, n.velocity, n.hand,
                 n.min_key, n.max_key, n.note_type) for n in model.notes_tree]

    def run_tool(self, existing=(), accept=True):
        import contextlib
        import io as _io
        from unittest import mock
        from PyQt5.QtWidgets import QDialog
        from qt_editor.main_window import MainWindow

        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        self.addCleanup(lambda: (setattr(win.view.model, 'dirty', False), win.close()))
        win.view.model = self.chart()
        win.view.model.pedal_spans = [list(s) for s in existing]
        model = win.view.model

        onsets = [float(n.start) for n in model.notes_tree]
        spans = [[600.0 + i * 2000, 2200.0 + i * 2000] for i in range(10)]
        with mock.patch.object(win, '_pick_transcribe_audio', return_value='a_piano.wav'), \
                mock.patch.object(win, '_run_pedal_transcribe', return_value=True), \
                mock.patch('os.path.isfile', return_value=False), \
                mock.patch('qt_editor.audio_pedal.read_midi',
                           return_value=([t - 400.0 for t in onsets], spans)), \
                mock.patch('PyQt5.QtWidgets.QDialog.exec_',
                           return_value=QDialog.Accepted if accept else QDialog.Rejected), \
                mock.patch('PyQt5.QtWidgets.QMessageBox.information'):
            before = self.snapshot(model)
            win.pedal_from_audio_dialog()
        return win, model, before

    def test_the_pedal_arrives(self):
        _win, model, _before = self.run_tool()
        self.assertTrue(model.pedal_spans)

    def test_not_one_note_is_touched(self):
        _win, model, before = self.run_tool()
        self.assertEqual(self.snapshot(model), before)

    def test_it_is_marked_as_heard_not_authored(self):
        _win, model, _before = self.run_tool()
        self.assertEqual(model.json_meta.get('pedal_origin'), 'audio')

    def test_cancelling_writes_nothing(self):
        _win, model, _before = self.run_tool(accept=False)
        self.assertEqual(model.pedal_spans, [])
        self.assertNotIn('pedal_origin', model.json_meta)

    def test_undo_puts_the_old_pedal_back(self):
        """生成錯了要退得回去，而且是一步退完。"""
        existing = [[0.0, 900.0]]
        win, model, _before = self.run_tool(existing=existing)
        self.assertNotEqual(model.pedal_spans, existing)
        win.view.undo()
        self.assertEqual(win.view.model.pedal_spans, existing)

    def test_it_survives_a_save_and_reload(self):
        from qt_editor.models import NoteModel
        _win, model, _before = self.run_tool()
        written = list(model.pedal_spans)
        path = os.path.join(tempfile.mkdtemp(), 'c.json')
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(path), ignore_errors=True))
        model.save_json(path)
        back = NoteModel()
        back.load_json(path)
        self.assertEqual(len(back.pedal_spans), len(written))
        self.assertEqual(back.json_meta.get('pedal_origin'), 'audio')


class TheEditorOffersItTests(unittest.TestCase):
    def window(self):
        import contextlib
        import io as _io
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        self.addCleanup(lambda: (setattr(win.view.model, 'dirty', False), win.close()))
        return win

    def test_the_pedal_menu_offers_it(self):
        win = self.window()
        groups = dict((name, [label for label, _slot in entries])
                      for name, entries in win._tool_groups())
        self.assertIn('以音檔補上踏板…', groups['延音踏板'])

    def test_it_comes_before_the_harmony_guess(self):
        """實測 71%/87% vs 37%/49%，比較準的排前面。"""
        win = self.window()
        labels = dict(win._tool_groups())['延音踏板']
        labels = [label for label, _slot in labels]
        self.assertLess(labels.index('以音檔補上踏板…'), labels.index('依和聲生成踏板…'))

    def test_pan_xml_turns_it_off(self):
        """PAN 格式存不下踏板，這個工具也要跟著關。"""
        from qt_editor.main_window import MainWindow
        self.assertIn('pedal_from_audio_dialog', MainWindow._PAN_DISABLED_SLOTS)

    def test_an_empty_chart_is_refused(self):
        from unittest import mock
        win = self.window()
        win.view.model.notes_tree = []
        with mock.patch('PyQt5.QtWidgets.QMessageBox.information') as info, \
                mock.patch.object(win, '_pick_transcribe_audio') as pick:
            win.pedal_from_audio_dialog()
        self.assertTrue(info.called)
        self.assertFalse(pick.called, '沒音符就不該先問人家要哪個音檔')


if __name__ == '__main__':
    unittest.main()
