"""放置模式的「自訂…」時值：輸入 N，一個全音符分成 N 份。"""

import contextlib
import io
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.settings import settings  # noqa: E402


class CustomDurationTests(unittest.TestCase):
    def setUp(self):
        self._saved = settings._data.get('custom_note_divisions')
        settings._data['custom_note_divisions'] = []
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            self.win = MainWindow()
        self.combo = self.win._toolbars[0].dur_combo

    def tearDown(self):
        settings._data['custom_note_divisions'] = self._saved or []
        self.win.view.model.dirty = False
        self.win.close()

    def pick_custom(self, n, ok=True):
        with mock.patch('qt_editor.main_window.QInputDialog.getInt', return_value=(n, ok)), \
                mock.patch.object(settings, 'save'):
            self.combo.setCurrentIndex(self.combo.count() - 1)

    def names(self):
        return [self.combo.itemText(i) for i in range(self.combo.count())]

    def test_the_last_item_is_custom(self):
        self.assertEqual(self.names()[-1], '自訂…')

    def test_a_new_division_is_added_in_length_order_and_selected(self):
        self.pick_custom(20)
        names = self.names()
        self.assertIn('20分音符', names)
        self.assertLess(names.index('16分音符'), names.index('20分音符'))
        self.assertLess(names.index('20分音符'), names.index('24分音符'))
        self.assertEqual(self.combo.currentText(), '20分音符')
        self.assertAlmostEqual(self.win.view._note_duration_beats, 0.2)
        self.assertFalse(self.combo.itemIcon(names.index('20分音符')).isNull())

    def test_an_existing_value_is_just_selected(self):
        before = self.combo.count()
        self.pick_custom(16)
        self.assertEqual(self.combo.count(), before)
        self.assertEqual(self.combo.currentText(), '16分音符')
        self.assertEqual(settings._data['custom_note_divisions'], [])

    def test_cancel_goes_back(self):
        self.combo.setCurrentIndex(self.names().index('八分音符'))
        self.pick_custom(20, ok=False)
        self.assertEqual(self.combo.currentText(), '八分音符')
        self.assertNotIn('20分音符', self.names())

    def test_it_is_remembered_and_the_default_stays_quarter(self):
        self.pick_custom(3)
        self.assertEqual(settings._data['custom_note_divisions'], [3])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            again = MainWindow()
        try:
            combo = again._toolbars[0].dur_combo
            names = [combo.itemText(i) for i in range(combo.count())]
            self.assertIn('3分音符', names)
            self.assertEqual(combo.currentText(), '四分音符')
        finally:
            again.close()

    def test_both_toolbars_and_shortcuts_see_it(self):
        self.pick_custom(20)
        self.win._on_dur_combo_changed(self.names().index('16分音符'))
        self.win._shortcut_dur_shorter()
        self.assertEqual(self.combo.currentText(), '20分音符')

    def test_status_label_names_it(self):
        from qt_editor.chart_view import ChartView
        self.assertEqual(ChartView._note_value_label(0.2), '20分音符')
        self.assertEqual(ChartView._note_value_label(4.0 / 7), '7分音符')


if __name__ == '__main__':
    unittest.main()
