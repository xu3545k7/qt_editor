"""AI 轉譜後的 BPM 偵測與吸附小節線（合成資料，不需要轉譜環境）。"""

import os
import random
import shutil
import tempfile
import unittest

import bisect

import mido

from qt_editor import ai_grid as G
from qt_editor import beat_detect as BD
from qt_editor.models import NoteModel, _bpm_from_midi_tempo


def _write_ai_midi(path, notes, pedals=()):
    """照 piano_transcription_inference 的格式寫：120 BPM、384 tpb、秒 × 768 = tick。"""
    events = []
    for start, end, pitch, vel in notes:
        events.append((start, 1, mido.Message('note_on', note=pitch, velocity=vel)))
        events.append((end, 0, mido.Message('note_on', note=pitch, velocity=0)))
    for down, up in pedals:
        events.append((down, 1, mido.Message('control_change', control=64, value=127)))
        events.append((up, 0, mido.Message('control_change', control=64, value=0)))
    events.sort(key=lambda e: (e[0], e[1]))
    mid = mido.MidiFile(ticks_per_beat=384)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    mid.tracks.append(meta)
    track = mido.MidiTrack()
    last = 0
    for t, _o, msg in events:
        tick = int(t * 768)
        track.append(msg.copy(time=tick - last))
        last = tick
    mid.tracks.append(track)
    mid.save(path)


def _song(bpm=150.0, downbeat=0.7, bars=24, jitter=0.008, seed=1):
    """低音踩每小節第一拍與第三拍、右手 16 分音符，全部加一點時間誤差。"""
    rng = random.Random(seed)
    beat = 60.0 / bpm
    notes = []
    for bar in range(bars):
        t0 = downbeat + bar * 4 * beat
        notes.append((t0 + rng.uniform(-jitter, jitter), 36, 100))
        notes.append((t0 + 2 * beat + rng.uniform(-jitter, jitter), 43, 80))
        for k in range(16):
            if rng.random() < 0.8:
                notes.append((t0 + k * beat / 4 + rng.uniform(-jitter, jitter),
                              72 + (k * 5) % 12, 90 if k % 4 == 0 else 60))
    return sorted(notes)


@unittest.skipUnless(BD.available(), 'numpy 不在')
class BeatDetectTests(unittest.TestCase):
    def test_finds_bpm_and_downbeat(self):
        est = BD.estimate(_song(), numerator=4)
        self.assertAlmostEqual(est.bpm, 150.0, places=2)
        bar = 4 * 60.0 / 150.0
        err = (est.downbeat - 0.7 + bar / 2) % bar - bar / 2
        self.assertLess(abs(err), 0.015)

    def test_given_bpm_only_finds_phase(self):
        est = BD.estimate(_song(bpm=186, downbeat=1.234), bpm=186, numerator=4)
        self.assertEqual(est.bpm, 186)
        self.assertEqual(est.candidates, [])
        bar = 4 * 60.0 / 186
        err = (est.downbeat - 1.234 + bar / 2) % bar - bar / 2
        self.assertLess(abs(err), 0.015)

    def test_too_few_notes(self):
        self.assertIsNone(BD.estimate([(0.0, 60, 100), (0.5, 60, 100)]))


class GridBuildTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ai_grid_test_')
        self.src = os.path.join(self.dir, 'song.ai.mid')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _notes_in_beats(self, path):
        mid = mido.MidiFile(path)
        out, active, tick = [], {}, 0
        for msg in mid.tracks[1]:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note] = (tick, msg.velocity)
            elif msg.type == 'note_on':
                start, vel = active.pop(msg.note)
                out.append((start / 480.0, tick / 480.0, msg.note, vel))
        return mid, sorted(out)

    def test_pad_puts_downbeat_on_bar_line(self):
        st = G.GridSettings(bpm=120, downbeat=0.3)          # 一小節 2 秒
        self.assertEqual(G.pad_ms_for(st), 1700)
        self.assertEqual(G.pad_ms_for(G.GridSettings(bpm=120, downbeat=0.0)), 0)
        self.assertEqual(G.pad_ms_for(G.GridSettings(bpm=120, downbeat=4.0)), 0)
        self.assertEqual(G.pad_ms_for(G.GridSettings(bpm=120, downbeat=2.5)), 1500)

    def test_build_snaps_to_grid_and_writes_tempo(self):
        notes = [(s, s + 0.2, p, v) for s, p, v in _song(bpm=150, downbeat=0.7)]
        _write_ai_midi(self.src, notes, pedals=[(0.69, 2.3), (2.31, 3.9)])
        dst = G.output_path(self.src, 150)
        self.assertTrue(dst.endswith('song.ai.150bpm.mid'))
        stats = G.build(self.src, dst, G.GridSettings(bpm=150, downbeat=0.7, division=4))
        mid, got = self._notes_in_beats(dst)
        tempos = [m.tempo for m in mid.tracks[0] if m.type == 'set_tempo']
        self.assertEqual(tempos, [mido.bpm2tempo(150)])
        # 第一顆（第一小節的低音）落在第二小節的小節線上
        self.assertAlmostEqual(got[0][0], 4.0)
        self.assertEqual(stats.pad_ms, 900)
        for start, end, _p, _v in got:
            self.assertAlmostEqual(start * 4, round(start * 4), places=6)
            self.assertAlmostEqual(end * 4, round(end * 4), places=6)
            self.assertGreaterEqual(end - start, 0.25 - 1e-9)
        self.assertEqual(stats.notes, len(got))
        self.assertEqual(stats.pedals, 2)

    def test_same_cell_same_pitch_is_merged_and_overlap_trimmed(self):
        notes = [
            (0.00, 0.40, 60, 50),
            (0.02, 0.30, 60, 90),     # 和上一顆吸到同一格：留力度大的
            (0.50, 1.40, 62, 70),     # 很長，會跨過下一顆同音的開頭
            (1.00, 1.20, 62, 70),
        ]
        _write_ai_midi(self.src, notes)
        dst = os.path.join(self.dir, 'out.mid')
        stats = G.build(self.src, dst, G.GridSettings(bpm=120, downbeat=0.0, division=4))
        _mid, got = self._notes_in_beats(dst)
        self.assertEqual(stats.merged, 1)
        self.assertEqual(stats.trimmed, 1)
        by_pitch = {}
        for s, e, p, v in got:
            by_pitch.setdefault(p, []).append((s, e, v))
        self.assertEqual(len(by_pitch[60]), 1)
        self.assertEqual(by_pitch[60][0][2], 90)
        (s1, e1, _), (s2, _e2, _) = by_pitch[62]
        self.assertLessEqual(e1, s2)

    def test_no_snap_keeps_timing(self):
        _write_ai_midi(self.src, [(0.333, 0.5, 60, 80), (1.111, 1.3, 64, 80)])
        dst = os.path.join(self.dir, 'out.mid')
        G.build(self.src, dst, G.GridSettings(bpm=100, downbeat=0.0, division=0))
        _mid, got = self._notes_in_beats(dst)
        beat = 0.6
        self.assertAlmostEqual(got[0][0] * beat, 0.333, delta=0.002)
        self.assertAlmostEqual(got[1][0] * beat, 1.111, delta=0.002)

    def test_editor_loads_tidy_bpm_and_bars(self):
        notes = [(s, s + 0.2, p, v) for s, p, v in _song(bpm=161, downbeat=0.05)]
        _write_ai_midi(self.src, notes)
        dst = G.output_path(self.src, 161)
        G.build(self.src, dst, G.GridSettings(bpm=161, downbeat=0.05, division=4))
        model = NoteModel()
        model.load_midi(dst, auto_arrange=False)
        self.assertEqual(model.bpm, 161.0)
        self.assertEqual(model.beats_per_bar, 4)
        bar_ms = 4 * 60000.0 / 161
        first = min(n.start for n in model.notes_tree)
        self.assertAlmostEqual(first, bar_ms, delta=1.0)


def _varying(first=150.0, second=135.0, bars_each=12, downbeat=0.6, jitter=0.006, seed=7):
    """前 bars_each 小節 first BPM、之後 second BPM 的合成曲（低音踩拍、右手 16 分）。"""
    rng = random.Random(seed)
    notes, t0 = [], downbeat
    for index, bpm in ((0, first), (1, second)):
        beat = 60.0 / bpm
        for bar in range(bars_each):
            start = t0 + bar * 4 * beat
            notes.append((start + rng.uniform(-jitter, jitter), 36, 100))
            notes.append((start + 2 * beat + rng.uniform(-jitter, jitter), 43, 80))
            for k in range(16):
                if rng.random() < 0.85:
                    notes.append((start + k * beat / 4 + rng.uniform(-jitter, jitter),
                                  72 + (k * 5) % 12, 90 if k % 4 == 0 else 60))
        t0 += bars_each * 4 * beat
    return sorted(notes)


@unittest.skipUnless(BD.available(), 'numpy 不在')
class TempoCurveTests(unittest.TestCase):
    def test_constant_song_is_one_segment(self):
        curve = BD.estimate_curve(_song(bpm=150, downbeat=0.7, bars=24), numerator=4)
        self.assertEqual(len(curve.segments), 1)
        self.assertAlmostEqual(curve.segments[0].bpm, 150.0, places=1)

    def test_finds_the_two_tempos(self):
        # 給 BPM（使用者通常知道；不給的話全曲估計可能挑到 2 倍速，那是另一件事）
        curve = BD.estimate_curve(_varying(), bpm=150, numerator=4)
        bpms = [round(s.bpm) for s in curve.segments]
        self.assertIn(150, bpms)
        self.assertIn(135, bpms)
        # 拍點表本身要跟著變慢：後半的拍長明顯比前半長
        first = curve.beats[4] - curve.beats[3]
        last = curve.beats[-2] - curve.beats[-3]
        self.assertGreater(last, first * 1.05)

    def test_beats_follow_the_music(self):
        notes = _varying()
        curve = BD.estimate_curve(notes, bpm=150, numerator=4)
        # 每個低音（都踩在拍上）都該離某一拍很近
        beats = curve.beats
        for t, pitch, _v in notes:
            if pitch != 36:
                continue
            i = min(max(bisect.bisect_left(beats, t) - 1, 0), len(beats) - 2)
            gap = min(abs(t - beats[i]), abs(t - beats[i + 1]))
            self.assertLess(gap, 0.03, '拍點離 %.3f 秒的低音太遠' % t)


class TempoEventTests(unittest.TestCase):
    """速度事件：定速只寫一個、換速只寫一個，而且不會來回震盪。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ai_grid_var_')
        self.src = os.path.join(self.dir, 'song.ai.mid')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _tempos(self, path):
        mid = mido.MidiFile(path)
        out, tick = [], 0
        for msg in mid.tracks[0]:
            tick += msg.time
            if msg.type == 'set_tempo':
                out.append((tick // (480 * 4), round(mido.tempo2bpm(msg.tempo), 2)))
        return out

    def test_constant_beats_write_one_tempo(self):
        beat = 60.0 / 150.0
        beats = [0.5 + k * beat for k in range(4 * 40 + 1)]
        notes = [(b, b + 0.2, 60 + (k % 5), 90) for k, b in enumerate(beats[:-1])]
        _write_ai_midi(self.src, notes)
        dst = os.path.join(self.dir, 'out.mid')
        stats = G.build(self.src, dst, G.GridSettings(bpm=150, downbeat=0.5, numerator=4,
                                                      division=4, beats=beats))
        self.assertEqual(stats.tempos, 1)
        self.assertEqual(self._tempos(dst), [(0, 150.0)])

    def test_one_change_writes_two_tempos_without_wobble(self):
        # 20 小節 196 BPM 之後 10 小節 176 BPM——實測舊版會在這裡每兩小節跳一次
        beats, t = [], 0.4
        for bpm, bars in ((196.0, 20), (176.0, 10)):
            beat = 60.0 / bpm
            for _ in range(bars * 4):
                beats.append(t)
                t += beat
        beats.append(t)
        notes = [(b, b + 0.15, 60 + (k % 7), 90) for k, b in enumerate(beats[:-1])]
        _write_ai_midi(self.src, notes)
        dst = os.path.join(self.dir, 'out.mid')
        stats = G.build(self.src, dst, G.GridSettings(bpm=196, downbeat=0.4, numerator=4,
                                                      division=4, beats=beats))
        tempos = self._tempos(dst)
        self.assertEqual(stats.tempos, 2, tempos)
        self.assertEqual([bpm for _bar, bpm in tempos], [196.0, 176.0])

    def test_bar_lines_land_on_the_tracked_beats(self):
        beats, t = [], 0.3
        for bpm, bars in ((170.0, 12), (150.0, 12)):
            beat = 60.0 / bpm
            for _ in range(bars * 4):
                beats.append(t)
                t += beat
        beats.append(t)
        notes = [(b, b + 0.12, 60 + (k % 9), 88) for k, b in enumerate(beats[:-1])]
        _write_ai_midi(self.src, notes)
        dst = os.path.join(self.dir, 'out.mid')
        settings = G.GridSettings(bpm=170, downbeat=0.3, numerator=4, division=4, beats=beats)
        stats = G.build(self.src, dst, settings)
        model = NoteModel()
        model.load_midi(dst, auto_arrange=False)
        pad = stats.pad_ms / 1000.0
        shift_bars = int(round((beats[0] + pad) / (beats[4] - beats[0])))
        worst = 0.0
        for k in range(0, len(beats) - 1, 4):
            start, _end = model.get_measure_time_range(k // 4 + shift_bars)
            if start is None:
                continue
            worst = max(worst, abs(start - (beats[k] + pad) * 1000.0))
        self.assertLess(worst, 3.0, '小節線和追蹤到的拍點差 %.1fms' % worst)


class MidiTempoTests(unittest.TestCase):
    def test_tidy_bpm(self):
        for bpm in (161, 186, 99.11, 88.8, 174.5, 227, 120):
            self.assertEqual(_bpm_from_midi_tempo(mido.bpm2tempo(bpm)), bpm)
        # 本來就不整齊的速度保持原值
        self.assertAlmostEqual(_bpm_from_midi_tempo(372123), 60e6 / 372123)


if __name__ == '__main__':
    unittest.main()


class GlissandoTests(unittest.TestCase):
    """滑音（華音）不能被吸附。

    使用者：「他應該是一串 tap，不能被改時間」。實測漂流那條 10 顆、橫跨 185ms
    的下行滑音，16 分吸附後只剩 3 個格子——變成三個五連音和弦。但擠在一起的
    和弦（實測 5 顆在 20ms 內、音高 85/97/30/42/82）本來就該吸到同一格，所以
    判定必須看音高是不是單調小步，不能只看時間密。
    """

    GRID = 0.25            # 150 BPM 的 16 分 = 0.25 拍 = 100ms

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ai_gliss_test_')
        self.src = os.path.join(self.dir, 'song.ai.mid')
        self.dst = os.path.join(self.dir, 'out.mid')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, notes, **kw):
        _write_ai_midi(self.src, notes)
        opts = dict(bpm=150, downbeat=0.0, division=4)
        opts.update(kw)
        G.build(self.src, self.dst, G.GridSettings(**opts))
        mid = mido.MidiFile(self.dst)
        out, tick = [], 0
        for msg in mid.tracks[1]:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                out.append((tick / 480.0, msg.note))
        return sorted(out)

    @staticmethod
    def _gliss(t0=2.010, step=0.020, top=101, down=2, count=10, length=0.5):
        return [(t0 + i * step, t0 + i * step + length, top - i * down, 80)
                for i in range(count)]

    def test_a_glissando_keeps_every_onset(self):
        got = self._run(self._gliss())
        self.assertEqual(len(got), 10)
        self.assertEqual(len({t for t, _p in got}), 10, '每一顆都還是自己的時間')

    def test_only_the_first_note_is_pulled_onto_the_grid(self):
        got = self._run(self._gliss())
        first = got[0][0]
        self.assertAlmostEqual(first, 5.0, places=6, msg='起點要對拍')
        # 其餘保留原本的相對時間：20ms = 0.05 拍，9 段共 0.45 拍
        self.assertAlmostEqual(got[-1][0] - first, 0.45, delta=0.02)
        for (a, _pa), (b, _pb) in zip(got, got[1:]):
            self.assertAlmostEqual(b - a, 0.05, delta=0.01)

    def test_a_tight_chord_still_snaps_together(self):
        """音高亂跳、跨好幾個八度的密集群是和弦，該一起吸到同一格。"""
        notes = [(3.010 + i * 0.005, 3.5, p, 80)
                 for i, p in enumerate((85, 97, 30, 42, 82))]
        got = self._run(notes)
        self.assertEqual(len({t for t, _p in got}), 1)
        self.assertAlmostEqual(got[0][0], 7.5, places=6)

    def test_a_zigzag_is_not_a_run(self):
        """上下來回跳的不算滑音——那是顫音或裝飾音，照舊吸附。"""
        notes = [(2.010 + i * 0.020, 2.5, p, 80)
                 for i, p in enumerate((60, 62, 60, 62, 60, 62))]
        got = self._run(notes)
        self.assertLessEqual(len({t for t, _p in got}), 2)

    def test_three_notes_are_too_few(self):
        got = self._run(self._gliss(count=3))
        self.assertEqual(len({t for t, _p in got}), 1, '短的裝飾音不算一串')

    def test_notes_already_a_grid_apart_are_untouched(self):
        """一般的 16 分音符串（間隔剛好一格）不在例外範圍內。"""
        notes = [(2.010 + i * 0.100, 2.5, 60 + i, 80) for i in range(8)]
        got = self._run(notes)
        for t, _p in got:
            self.assertAlmostEqual(t / self.GRID, round(t / self.GRID), places=6)

    def test_turning_the_exemption_off_brings_the_old_behaviour_back(self):
        got = self._run(self._gliss(), keep_runs=False)
        self.assertLessEqual(len({t for t, _p in got}), 3,
                             '沒有例外時 10 顆會塌成 3 個格子')

    def test_a_run_keeps_its_length_instead_of_being_stretched_to_a_cell(self):
        """一串 tap 的長度跟著起音平移，不吸附也不撐到一整格。"""
        _write_ai_midi(self.src, self._gliss(length=0.030))
        G.build(self.src, self.dst, G.GridSettings(bpm=150, downbeat=0.0, division=4))
        mid = mido.MidiFile(self.dst)
        spans, active, tick = [], {}, 0
        for msg in mid.tracks[1]:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note] = tick
            elif msg.type == 'note_on':
                spans.append((tick - active.pop(msg.note)) / 480.0)
        self.assertEqual(len(spans), 10)
        for span in spans:
            self.assertLess(span, self.GRID, '30ms 的 tap 不該被撐成一格')
            self.assertAlmostEqual(span, 0.075, delta=0.01)


class LeadInTrimTests(unittest.TestCase):
    """自動刪掉開頭多餘的空白。

    使用者：「轉譜沒有前面空白自動刪減再貼合小節線的選項」。對齊小節線只需要補
    不到一小節的靜音，但音檔開頭常常本來就沉默好幾小節，補完之後譜面就從一大段
    空白開始（實測漂流：downbeat 0.327 秒、一小節 1.6 秒，補 1273ms 之後第一顆音
    落在第 3 小節）。

    成立的關鍵是**扣整數個小節不會破壞小節線對齊**，所以先補到線上、再往回扣。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ai_lead_test_')
        self.src = os.path.join(self.dir, 'song.ai.mid')
        self.dst = os.path.join(self.dir, 'out.mid')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _settings(self, **kw):
        # 150 BPM 4/4 → 一小節 1.6 秒；downbeat 5.1 秒 ＝ 第 3 小節多一點
        opts = dict(bpm=150.0, downbeat=5.1, numerator=4, division=4)
        opts.update(kw)
        return G.GridSettings(**opts)

    def test_off_by_default_so_nothing_changes(self):
        st = self._settings()
        self.assertFalse(st.trim_lead_in)
        self.assertEqual(G.lead_shift_ms(st), G.pad_ms_for(st))

    def test_the_pad_alone_leaves_the_music_four_bars_in(self):
        """純補靜音會把音樂推到第 4 條小節線上——開頭 6.4 秒什麼都沒有。"""
        st = self._settings()
        pad = G.pad_ms_for(st)
        self.assertEqual(pad, 1300)                     # (-5100) % 1600
        self.assertAlmostEqual((5100 + pad) / 1600.0, 4.0, places=6)

    def test_trimming_keeps_only_the_asked_for_bars(self):
        for bars in (0, 1, 2):
            st = self._settings(trim_lead_in=True, lead_in_bars=bars)
            shift = G.lead_shift_ms(st)
            landed = (5100 + shift) / 1600.0
            self.assertAlmostEqual(landed, bars, places=6,
                                   msg='保留 %d 小節時落在第 %.2f 小節' % (bars, landed))

    def test_trimming_stays_on_the_bar_line(self):
        """扣掉的量一定是整數個小節，否則對齊就白做了。"""
        st = self._settings(trim_lead_in=True, lead_in_bars=1)
        dropped = G.pad_ms_for(st) - G.lead_shift_ms(st)
        self.assertAlmostEqual(dropped / 1600.0, round(dropped / 1600.0), places=6)

    def test_it_never_pushes_the_first_note_before_zero(self):
        """弱起的音在 downbeat 之前，不能被扣到負的時間。"""
        st = self._settings(trim_lead_in=True, lead_in_bars=0)
        # 第一顆音在 0.2 秒（遠早於 downbeat）→ 幾乎不能扣
        shift = G.lead_shift_ms(st, first_note_s=0.2)
        self.assertGreaterEqual(200 + shift, 0, '第一顆音被扣到負的時間')

    def test_build_shifts_the_notes_earlier(self):
        notes = [(5.1 + i * 0.4, 5.1 + i * 0.4 + 0.2, 60 + i, 80) for i in range(8)]
        _write_ai_midi(self.src, notes)
        kept = G.build(self.src, self.dst,
                       self._settings(trim_lead_in=True, lead_in_bars=1))
        padded = G.build(self.src, self.dst, self._settings())
        self.assertLess(kept.pad_ms, padded.pad_ms, '刪減後的位移要比純補小')
        self.assertEqual(kept.notes, padded.notes, '刪減不該弄掉音符')

    def test_the_dialog_offers_it(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from qt_editor.ai_grid_dialog import AiGridDialog
        onsets = [(0.4 * i, 60 + i % 12, 80) for i in range(60)]
        dlg = AiGridDialog(None, onsets, 'song.wav', bpm_hint=150.0)
        self.assertTrue(hasattr(dlg, '_trim_lead'), '對話框沒有這個選項')
        dlg._trim_lead.setChecked(True)
        dlg._lead_bars.setValue(2)
        got = dlg.settings()
        self.assertTrue(got.trim_lead_in)
        self.assertEqual(got.lead_in_bars, 2)
        self.assertIsInstance(app, QApplication)


class LeadInAnchorTests(unittest.TestCase):
    """刪減的基準必須是第一顆音符，不是 downbeat。

    這兩者常常差很多：實測漂流的 downbeat 在 0.327 秒（不到一小節），但第一顆
    音在 8.27 秒——開頭那 6 小節的空白是音樂晚進來造成的。第一版拿 downbeat 當
    基準，結果對這首算出「不用刪」，等於功能沒生效。
    """

    def test_an_early_downbeat_with_a_late_first_note_still_trims(self):
        st = G.GridSettings(bpm=150.0, downbeat=0.327, numerator=4, division=4,
                            trim_lead_in=True, lead_in_bars=1)
        self.assertGreater(G.lead_shift_ms(st, first_note_s=8.27) * -1, 4000,
                           '第一顆音在 8.27 秒卻幾乎沒刪，基準抓錯了')
        # 不給第一顆音時退回 downbeat：保守，但不能比給了還激進
        self.assertGreaterEqual(G.lead_shift_ms(st),
                                G.lead_shift_ms(st, first_note_s=8.27))

    def test_what_is_dropped_is_always_whole_bars(self):
        """只有整小節才不會破壞小節線對齊。"""
        for first in (2.0, 5.5, 8.27, 13.9):
            st = G.GridSettings(bpm=150.0, downbeat=0.327, numerator=4, division=4,
                                trim_lead_in=True, lead_in_bars=1)
            dropped = G.pad_ms_for(st) - G.lead_shift_ms(st, first)
            self.assertAlmostEqual(dropped / 1600.0, round(dropped / 1600.0),
                                   places=6, msg='第一顆音 %.2f 秒時扣了 %d ms' % (first, dropped))

    def test_at_least_the_asked_for_bars_survive(self):
        """用 floor 而不是 round：第一顆音通常不在小節線上，round 會把它推過頭。"""
        for bars in (0, 1, 2, 3):
            st = G.GridSettings(bpm=150.0, downbeat=0.327, numerator=4, division=4,
                                trim_lead_in=True, lead_in_bars=bars)
            landed = (8270 + G.lead_shift_ms(st, 8.27)) / 1600.0
            self.assertGreaterEqual(landed, bars,
                                    '要求留 %d 小節卻只剩 %.2f' % (bars, landed))
            self.assertLess(landed, bars + 1,
                            '要求留 %d 小節卻留了 %.2f' % (bars, landed))

    def test_the_dialog_preview_counts_the_first_note(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from qt_editor.ai_grid_dialog import AiGridDialog
        # 第一顆音在 8.27 秒之後才開始密集敲
        onsets = [(8.27 + 0.4 * i, 60 + i % 12, 80) for i in range(60)]
        dlg = AiGridDialog(None, onsets, 'song.wav', bpm_hint=150.0)
        dlg._enable.setChecked(True)
        dlg._trim_lead.setChecked(True)
        dlg._lead_bars.setValue(1)
        dlg._refresh_info()
        self.assertIn('刪掉', dlg._info.text(),
                      '預覽沒講會刪：%r' % dlg._info.text())
        self.assertIsInstance(app, QApplication)


class LeadInZeroTests(unittest.TestCase):
    """「保留導入 = 0」要是真的 0。

    使用者：「我是希望導入空白是 0」。第一版只扣整數個小節，所以第一顆音符會停在
    不到一小節的餘數上（實測 POTENTIAL：1725ms 的空白扣掉 1 小節還剩 525ms）——
    控制項寫 0 卻留了半個小節。

    代價是 0 的時候格線相位改由第一顆音符決定；>=1 時只扣整小節、相位不變。
    """

    def _settings(self, bars, **kw):
        return G.GridSettings(bpm=199.95, downbeat=1.712, numerator=4, division=4,
                              trim_lead_in=True, lead_in_bars=bars, **kw)

    def test_zero_really_means_zero(self):
        shift = G.lead_shift_ms(self._settings(0), first_note_s=1.712)
        self.assertEqual(round(1712 + shift), 0, '保留 0 小節卻還有空白')

    def test_zero_works_whatever_the_first_note_is(self):
        for first in (0.2, 1.712, 8.27, 31.4):
            shift = G.lead_shift_ms(self._settings(0), first_note_s=first)
            self.assertEqual(round(first * 1000 + shift), 0)

    def test_one_bar_or_more_still_only_drops_whole_bars(self):
        """>=1 時相位不能變，所以只扣整小節（留下來的可能比指定的多）。"""
        bar = 60000.0 / 199.95 * 4
        for bars in (1, 2, 3):
            st = self._settings(bars)
            dropped = G.pad_ms_for(st) - G.lead_shift_ms(st, first_note_s=8.27)
            # 位移會被 int(round()) 取整到毫秒，所以容許 1.5ms 的誤差——不是
            # 容許「不到整小節」，而是容許整小節的毫秒取整。
            off = abs(dropped - round(dropped / bar) * bar)
            self.assertLess(off, 1.5,
                            '保留 %d 小節時扣了 %.1f ms，離整小節差 %.2f ms'
                            % (bars, dropped, off))

    def test_at_least_the_asked_for_bars_survive_when_not_zero(self):
        bar = 60000.0 / 199.95 * 4
        for bars in (1, 2):
            shift = G.lead_shift_ms(self._settings(bars), first_note_s=8.27)
            self.assertGreaterEqual((8270 + shift) / bar, bars - 1e-6)

    def test_the_choice_is_remembered(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from qt_editor.ai_grid_dialog import AiGridDialog
        from qt_editor.settings import settings
        before = (settings.get('ai_trim_lead_in', False),
                  settings.get('ai_lead_in_bars', 1))
        try:
            onsets = [(1.712 + 0.3 * i, 60 + i % 12, 80) for i in range(60)]
            dlg = AiGridDialog(None, onsets, 'song.wav', bpm_hint=199.95)
            dlg._trim_lead.setChecked(True)
            dlg._lead_bars.setValue(0)
            got = dlg.settings()
            self.assertTrue(got.trim_lead_in)
            self.assertEqual(got.lead_in_bars, 0)
            again = AiGridDialog(None, onsets, 'song.wav', bpm_hint=199.95)
            self.assertTrue(again._trim_lead.isChecked(), '沒記住勾選')
            self.assertEqual(again._lead_bars.value(), 0, '沒記住小節數')
        finally:
            settings.set('ai_trim_lead_in', before[0])
            settings.set('ai_lead_in_bars', before[1])
        self.assertIsInstance(app, QApplication)


class NegativeTickTests(unittest.TestCase):
    """導入空白＝0 時，落在第一顆音符之前的事件不能把存檔弄壞。

    使用者實測踩到：`ValueError: message time must be non-negative in MIDI file`。
    基準是**第一顆音符**，而踏板常常在它之前就按下去了——實測 8 份 AI 轉譜有
    5 份如此（最早的在 0.009 秒），那 5 份一份都轉不出來。

    負的 tick 一律收到 0：踏板在音樂開始之前就已經按住了，收到 0 正是那個意思。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ai_neg_test_')
        self.src = os.path.join(self.dir, 'song.ai.mid')
        self.dst = os.path.join(self.dir, 'out.mid')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, pedals):
        notes = [(s, s + 0.2, p, v) for s, p, v in _song(bpm=150, downbeat=2.5)]
        _write_ai_midi(self.src, notes, pedals=pedals)
        return min(n[0] for n in notes)

    def settings(self, bars=0):
        return G.GridSettings(bpm=150.0, downbeat=2.5, numerator=4, division=4,
                              trim_lead_in=True, lead_in_bars=bars)

    def test_a_pedal_before_the_first_note_does_not_break_the_save(self):
        first = self.write([(0.2, 3.0), (3.1, 5.0)])
        self.assertLess(0.2, first, '這個測試需要踏板在第一顆音符之前')
        stats = G.build(self.src, self.dst, self.settings())
        self.assertTrue(os.path.exists(self.dst))
        self.assertGreater(stats.notes, 0)

    def test_it_says_how_many_it_pulled_back(self):
        self.write([(0.2, 3.0), (3.1, 5.0)])
        self.assertEqual(G.build(self.src, self.dst, self.settings()).lead_clamped, 1)

    def test_nothing_is_clamped_when_there_is_nothing_early(self):
        first = self.write([(2.6, 4.0)])
        self.assertLess(first, 2.6)
        self.assertEqual(G.build(self.src, self.dst, self.settings()).lead_clamped, 0)

    def test_every_tick_in_the_file_is_non_negative(self):
        self.write([(0.05, 3.0), (3.1, 5.0)])
        G.build(self.src, self.dst, self.settings())
        for track in mido.MidiFile(self.dst).tracks:
            for msg in track:
                self.assertGreaterEqual(msg.time, 0, msg)

    def test_a_pedal_entirely_before_the_start_is_dropped_not_zero_length(self):
        """整段都在開頭之前的踏板收到 0 之後沒有長度了，直接丟掉。"""
        self.write([(0.05, 0.1), (3.1, 5.0)])
        stats = G.build(self.src, self.dst, self.settings())
        self.assertEqual(stats.pedals, 1, '零長度的踏板段落該被丟掉')

    def test_the_pedal_order_survives(self):
        """收到 0 之後不能和下一段疊起來。"""
        self.write([(0.05, 3.0), (3.1, 5.0), (5.2, 7.0)])
        G.build(self.src, self.dst, self.settings())
        spans, down = [], None
        for track in mido.MidiFile(self.dst).tracks:
            tick = 0
            for msg in track:
                tick += msg.time
                if msg.type == 'control_change' and msg.control == 64:
                    if msg.value >= 64:
                        down = tick
                    elif down is not None:
                        spans.append((down, tick))
                        down = None
        self.assertTrue(spans)
        for a, b in zip(spans, spans[1:]):
            self.assertLessEqual(a[1], b[0], '踏板段落疊在一起了：%s %s' % (a, b))

    def test_it_also_covers_a_lead_in_of_one_bar(self):
        """>=1 的時候也可能有東西落在 0 之前（踏板比保留的小節還早）。"""
        self.write([(0.05, 3.0), (3.1, 5.0)])
        stats = G.build(self.src, self.dst, self.settings(bars=1))
        self.assertTrue(os.path.exists(self.dst))
        self.assertGreaterEqual(stats.lead_clamped, 0)
