"""AI 轉譜後的 BPM 偵測與吸附小節線（合成資料，不需要轉譜環境）。"""

import os
import random
import shutil
import tempfile
import unittest

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


class MidiTempoTests(unittest.TestCase):
    def test_tidy_bpm(self):
        for bpm in (161, 186, 99.11, 88.8, 174.5, 227, 120):
            self.assertEqual(_bpm_from_midi_tempo(mido.bpm2tempo(bpm)), bpm)
        # 本來就不整齊的速度保持原值
        self.assertAlmostEqual(_bpm_from_midi_tempo(372123), 60e6 / 372123)


if __name__ == '__main__':
    unittest.main()
