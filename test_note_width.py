"""音符佔鍵道寬度的百分比（偏好設定 `note_width_pct`）。

收窄套在 `_note_display_x_range` 這一條上，而不是各個繪製函式裡：音符本體、
trill 網格、幽靈音符、長押頭尾、選取外框全都從這裡算出來，改一處就整批一致。
`_visible` 也用同一份矩形，所以**點選判定跟著縮**——所見即所點。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])


def note(start=0, lo=4, hi=6, pitch=60):
    n = GNote(None, 0)
    n.start, n.end, n.gate = start, start + 400, 400
    n.min_key, n.max_key = lo, hi
    n.pitch, n.hand, n.velocity, n.note_type = pitch, 0, 90, 0
    return n


class NoteWidthTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings.get('note_width_pct', 100)
        self.view = ChartView()
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [note()]
        m.rebuild_display_cache()
        self.view.model = m
        self.view.resize(1200, 800)

    def tearDown(self):
        settings.set('note_width_pct', self._saved)

    def span(self, pct, n=None):
        settings.set('note_width_pct', pct)
        self.view._note_width_frac = None       # 每幀清一次，測試裡手動清
        return self.view._note_display_x_range(n or self.view.model.notes_tree[0])

    def test_a_hundred_percent_fills_the_whole_lane(self):
        x1, _ = self.view._key_span(4)
        _, x2 = self.view._key_span(6)
        self.assertEqual(self.span(100), (x1, x2))

    def test_the_note_gets_narrower(self):
        full = self.span(100)
        half = self.span(50)
        self.assertAlmostEqual(half[1] - half[0], (full[1] - full[0]) * 0.5, places=6)

    def test_it_stays_centred_on_the_lane(self):
        full = self.span(100)
        for pct in (90, 70, 50, 40):
            narrow = self.span(pct)
            self.assertAlmostEqual((narrow[0] + narrow[1]) / 2.0,
                                   (full[0] + full[1]) / 2.0, places=6,
                                   msg='%d%% 沒有置中' % pct)

    def test_narrowing_never_leaves_the_lane(self):
        full = self.span(100)
        narrow = self.span(60)
        self.assertGreaterEqual(narrow[0], full[0])
        self.assertLessEqual(narrow[1], full[1])

    def test_out_of_range_values_are_clamped(self):
        full = self.span(100)
        # 設定對話框限制在 40~100，但設定檔可以被手改成任何值
        self.assertEqual(self.span(500), full, '超過 100% 不該讓音符溢出鍵道')
        tiny = self.span(1)
        self.assertAlmostEqual(tiny[1] - tiny[0], (full[1] - full[0]) * 0.4, places=6,
                               msg='下限應該夾在 40%')

    def test_a_wider_note_scales_proportionally(self):
        one = self.view._key_span(4)
        wide = self.span(50, note(lo=0, hi=9))
        narrow = self.span(50, note(lo=4, hi=4))
        lane = one[1] - one[0]
        self.assertAlmostEqual(wide[1] - wide[0], lane * 10 * 0.5, places=6)
        self.assertAlmostEqual(narrow[1] - narrow[0], lane * 0.5, places=6)

    def test_hit_testing_uses_the_same_geometry(self):
        """點選判定和繪製共用 _note_rect，所以縮的時候一起縮。"""
        settings.set('note_width_pct', 50)
        self.view._note_width_frac = None
        rect = self.view._note_rect(self.view.model.notes_tree[0])
        self.assertIsNotNone(rect)
        x1, x2 = self.span(50)
        self.assertAlmostEqual(rect.left(), x1, places=6)
        self.assertAlmostEqual(rect.width(), x2 - x1, places=6)

    def test_the_value_is_read_once_per_frame(self):
        self.span(100)
        settings.set('note_width_pct', 50)      # 不清快取
        self.assertEqual(self.view._note_width_fraction(), 1.0,
                         '同一幀內不該重讀設定')
        self.view._note_width_frac = None
        self.assertEqual(self.view._note_width_fraction(), 0.5)


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings.get('note_width_pct', 100)

    def tearDown(self):
        settings.set('note_width_pct', self._saved)

    def test_the_spinbox_shows_the_current_value(self):
        from qt_editor.settings_dialog import SettingsDialog
        settings.set('note_width_pct', 75)
        dlg = SettingsDialog(None)
        self.assertEqual(dlg._note_width_spin.value(), 75)

    def test_the_range_matches_what_the_view_accepts(self):
        from qt_editor.settings_dialog import SettingsDialog
        dlg = SettingsDialog(None)
        self.assertEqual((dlg._note_width_spin.minimum(),
                          dlg._note_width_spin.maximum()), (40, 100))


if __name__ == '__main__':
    unittest.main()
