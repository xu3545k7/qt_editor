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


@unittest.skipUnless(BD.available(), 'numpy 不在')
class VariableTempoDefaultTests(unittest.TestCase):
    """偵測到換速時「跟著音樂的速度變化」預設勾起來；定速曲維持不勾。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _dialog(self, onsets, bpm):
        from qt_editor.ai_grid_dialog import AiGridDialog
        return AiGridDialog(None, onsets, 'song.wav', bpm_hint=bpm)

    def test_tempo_change_is_checked_by_default(self):
        dlg = self._dialog(_varying(), 150)
        self.assertTrue(dlg.variable())
        self.assertTrue(dlg.settings().variable)

    def test_constant_song_stays_unchecked(self):
        dlg = self._dialog(_song(bpm=150, downbeat=0.7, bars=24), 150)
        self.assertFalse(dlg.variable())

    def test_user_choice_survives_redetection(self):
        dlg = self._dialog(_varying(), 150)
        dlg._variable.setChecked(False)
        dlg._bpm.setValue(151.0)            # 改 BPM 會重跑偵測
        dlg._bpm.setValue(150.0)
        self.assertFalse(dlg.variable())
