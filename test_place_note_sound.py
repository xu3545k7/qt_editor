"""放置模式放下音符時發聲，可開關。

機制本來就在（`_on_note_placed` render 單音 → `play_oneshot_pcm`），但被
`if not self._is_playing: return` 擋著，**只有播放中才會響**。停著編輯時放
音符完全沒有回饋，聽不出音高對不對。

拿掉那個限制，改由偏好設定 `place_note_sound` 控制（預設開）。開關要完整生效
——關掉之後播放中也不該響，不然「放置時發聲」這個名字就是騙人的。
"""

import unittest

from PyQt5.QtWidgets import QApplication

import qt_editor.main_window as mw
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])
_window = None


def window():
    global _window
    if _window is None:
        _window = MainWindow()
    return _window


def note(pitch=60, start=0, dur=200):
    n = GNote(None, 0)
    n.start, n.end, n.gate = start, start + dur, dur
    n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
    n.pitch, n.velocity = pitch, 90
    return n


class PlaceNoteSoundTests(unittest.TestCase):
    def setUp(self):
        self.win = window()
        self.saved = settings.get('place_note_sound', True)
        self.played = []
        # 攔在 render 這一層：真的合成需要 FluidSynth，測試環境不一定有
        self._synth = self.win._midi_preview_synth

        class FakeSynth:
            sample_rate = 44100

            def render(inner, notes, start, end, **kw):
                self.played.append([(n.pitch, n.velocity) for n in notes])
                return b'\x00\x00'

            def close(inner):
                pass

        self.win._midi_preview_synth = FakeSynth()
        self._oneshot = self.win.audio.play_oneshot_pcm
        self.win.audio.play_oneshot_pcm = lambda pcm, rate: None

    def tearDown(self):
        settings.set('place_note_sound', self.saved)
        self.win._midi_preview_synth = self._synth
        self.win.audio.play_oneshot_pcm = self._oneshot

    def test_it_sounds_when_not_playing(self):
        """這是新行為：以前只有播放中才響。"""
        settings.set('place_note_sound', True)
        self.win._is_playing = False
        self.win._on_note_placed(note(64))
        self.assertEqual(len(self.played), 1, '停著編輯時沒有發聲')

    def test_it_plays_the_pitch_that_was_placed(self):
        settings.set('place_note_sound', True)
        self.win._is_playing = False
        self.win._on_note_placed(note(72))
        self.assertEqual(self.played[0][0][0], 72)

    def test_turning_it_off_silences_it(self):
        settings.set('place_note_sound', False)
        self.win._is_playing = False
        self.win._on_note_placed(note(64))
        self.assertEqual(self.played, [])

    def test_turning_it_off_silences_it_during_playback_too(self):
        """開關要完整生效，不能在播放中偷偷忽略它。"""
        settings.set('place_note_sound', False)
        self.win._is_playing = True
        try:
            self.win._on_note_placed(note(64))
        finally:
            self.win._is_playing = False
        self.assertEqual(self.played, [])

    def test_it_still_sounds_during_playback_when_on(self):
        settings.set('place_note_sound', True)
        self.win._is_playing = True
        try:
            self.win._on_note_placed(note(64))
        finally:
            self.win._is_playing = False
        self.assertEqual(len(self.played), 1)

    def test_a_note_without_pitch_is_skipped(self):
        settings.set('place_note_sound', True)
        self.win._is_playing = False
        n = note(60)
        n.pitch = None
        self.win._on_note_placed(n)
        self.assertEqual(self.played, [])

    def test_none_is_not_a_crash(self):
        settings.set('place_note_sound', True)
        self.win._on_note_placed(None)
        self.assertEqual(self.played, [])

    def test_it_is_on_by_default(self):
        from qt_editor.settings import _DEFAULTS
        self.assertTrue(_DEFAULTS['place_note_sound'])

    def test_the_preference_exists_in_the_dialog(self):
        from qt_editor.settings_dialog import SettingsDialog
        dlg = SettingsDialog(None)
        self.assertIn('place_note_sound', dlg._toggles,
                      '偏好設定裡沒有這個開關')


if __name__ == '__main__':
    unittest.main()
