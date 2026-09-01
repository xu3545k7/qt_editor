import array
import unittest
from types import SimpleNamespace

from qt_editor.audio_player import AudioPlayer
from qt_editor.midi_preview import (
    MidiPreviewNote,
    MidiPreviewSynth,
    build_preview_notes,
    default_fluidsynth_dll_path,
    default_soundfont_path,
)
from qt_editor.models import game_lane_index_to_midi_pitch


class _Model:
    def __init__(self, notes, beats=()):
        self.notes_tree = list(notes)
        self._beats = list(beats)

    def get_beat_entries(self):
        return list(self._beats)


def _note(
    start,
    pitch=None,
    *,
    end=None,
    velocity=90,
    channel=0,
    hand=0,
    note_type=0,
    gate=0,
):
    return SimpleNamespace(
        start=start,
        end=start + 500 if end is None else end,
        gate=gate,
        pitch=pitch,
        velocity=velocity,
        channel=channel,
        hand=hand,
        note_type=note_type,
        min_key=10,
        max_key=12,
    )


class MidiPreviewEventTests(unittest.TestCase):
    def test_chord_keeps_every_pitch_at_same_time(self):
        model = _Model([_note(100, 60), _note(100, 64), _note(100, 67)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertEqual([60, 64, 67], [note.pitch for note in notes])
        self.assertEqual([100.0, 100.0, 100.0], [note.start_ms for note in notes])

    def test_missing_pitch_uses_lane_center(self):
        model = _Model([_note(100, None)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertEqual(game_lane_index_to_midi_pitch(11), notes[0].pitch)

    def test_repeated_pitch_is_trimmed_before_next_onset(self):
        model = _Model([_note(100, 60), _note(200, 60)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertLess(notes[0].end_ms, notes[1].start_ms)

    def test_hold_sustains_to_chart_end(self):
        model = _Model([_note(100, 60, end=1800, note_type=2)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertEqual(1800.0, notes[0].end_ms)

    def test_hold_uses_gate_when_end_is_incomplete(self):
        model = _Model([_note(100, 60, end=100, gate=900, note_type=10)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertEqual(1000.0, notes[0].end_ms)

    def test_tap_stays_short_even_if_chart_end_is_long(self):
        model = _Model([_note(100, 60, end=1800, note_type=0)])
        notes = build_preview_notes(model, enable_beat=False)
        self.assertEqual(320.0, notes[0].end_ms)

    def test_hand_and_beat_filters_are_preserved(self):
        model = _Model(
            [_note(100, 60, hand=0), _note(200, 48, hand=1)],
            beats=[(0, 0)],
        )
        notes = build_preview_notes(
            model,
            enable_right=False,
            enable_left=True,
            enable_beat=True,
        )
        self.assertEqual([84, 48], [note.pitch for note in notes])


class AudioMixTests(unittest.TestCase):
    def test_preview_add_does_not_attenuate_silent_song(self):
        song = array.array('h', [1000, -1000, 500, -500]).tobytes()
        silence = array.array('h', [0, 0, 0, 0]).tobytes()
        self.assertEqual(song, AudioPlayer._mix_pcm_add(song, silence, 2))

    def test_preview_add_clamps_instead_of_wrapping(self):
        song = array.array('h', [30000, -30000]).tobytes()
        piano = array.array('h', [10000, -10000]).tobytes()
        mixed = array.array('h', AudioPlayer._mix_pcm_add(song, piano, 2))
        self.assertEqual([32767, -32768], list(mixed))


class FluidSynthRenderTests(unittest.TestCase):
    @unittest.skipUnless(
        default_fluidsynth_dll_path().is_file()
        and default_soundfont_path().is_file(),
        "bundled FluidSynth/SoundFont not available",
    )
    def test_bundled_piano_renders_audible_stereo_pcm(self):
        with MidiPreviewSynth(44100) as synth:
            pcm = synth.render(
                [
                    MidiPreviewNote(0, 180, 60, 90),
                    MidiPreviewNote(0, 180, 64, 90),
                    MidiPreviewNote(0, 180, 67, 90),
                ],
                0,
                400,
            )
        samples = memoryview(pcm).cast('h')
        self.assertEqual(44100 * 4 * 400 // 1000, len(pcm))
        self.assertGreater(max(abs(value) for value in samples), 100)


if __name__ == '__main__':
    unittest.main()
