"""介面上的 JSON／XML 工作流程：預設存 JSON、存 XML 前轉換、XML 模式關掉 PAN 沒有的功能、事件編輯。"""

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])

from qt_editor.models import GNote, NoteModel  # noqa: E402
from qt_editor.pan_format import validate  # noqa: E402


def note(idx, start, note_type=0, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 200, 200
    n.pitch, n.hand, n.note_type = pitch, 0, note_type
    n.min_key, n.max_key = 10, 12
    return n


def json_chart(folder):
    m = NoteModel.create_new('t', 120.0, 20.0, 4)
    m.notes_tree = [note(0, 0), note(1, 500, 1), note(2, 1000, 3), note(3, 1500, 2)]
    m.rebuild_display_cache()
    m.pedal_spans = [[0.0, 800.0]]
    path = os.path.join(folder, 'song.json')
    m.save_json(path)
    loaded = NoteModel()
    loaded.load_json(path)
    return loaded, path


class PanUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False          # 不然關視窗會跳「要不要存檔」
        cls.win.close()

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.model, self.json_path = json_chart(self.folder)
        self.win._load_model_all(self.model)
        self.combo = self.win._toolbars[0].type_combo

    def save(self, path, answer=QMessageBox.Yes):
        with mock.patch('qt_editor.main_window.QMessageBox.question', return_value=answer) as q, \
                mock.patch('qt_editor.main_window.QMessageBox.information') as info, \
                mock.patch('qt_editor.main_window.QMessageBox.warning') as warn, \
                mock.patch.object(self.win, '_block_on_unassigned', return_value=False):
            self.win._do_save(path)
        return q, info, warn

    def type_enabled(self, note_type):
        i = self.win._type_combo_values.index(note_type)
        return self.combo.model().item(i).isEnabled()

    def test_json_mode_has_everything(self):
        self.assertFalse(self.model.pan_xml)
        self.assertTrue(self.type_enabled(1))
        self.assertTrue(self.type_enabled(3))
        self.assertNotIn('相容模式', self.win.windowTitle())

    def test_saving_xml_asks_then_converts(self):
        xml_path = os.path.join(self.folder, 'song.xml')
        q, info, warn = self.save(xml_path)
        self.assertTrue(q.called)
        text = q.call_args[0][2]
        self.assertIn('Soft 音符換成 Tap：1', text)
        self.assertIn('song.json 不會被改動', text)
        self.assertTrue(validate(xml_path).ok)
        self.assertFalse(warn.called)
        self.assertFalse({1, 3} & {n.note_type for n in self.model.notes_tree})
        self.assertTrue(self.model.pan_xml)
        self.assertFalse(self.type_enabled(3))
        self.assertIn('相容模式', self.win.windowTitle())
        with open(self.json_path, encoding='utf-8') as fh:
            self.assertIn('"note_type": 3', fh.read(), 'JSON 原始檔不能被動到')

    def test_declining_writes_nothing(self):
        xml_path = os.path.join(self.folder, 'no.xml')
        self.save(xml_path, answer=QMessageBox.No)
        self.assertFalse(os.path.exists(xml_path))
        self.assertIn(3, [n.note_type for n in self.model.notes_tree])

    def test_the_conversion_can_be_undone(self):
        self.save(os.path.join(self.folder, 'u.xml'))
        self.win.view.undo()
        self.assertIn(3, [n.note_type for n in self.model.notes_tree])

    def test_xml_mode_turns_things_off(self):
        self.save(os.path.join(self.folder, 'x.xml'))
        view = self.win.view
        self.assertFalse(view.type_allowed(1))
        view.set_view_mode('pitch')
        view._lane_flag_cache = None
        self.assertEqual(view._lane_flags(), (False, False))
        view.selected = {self.model.notes_tree[0].idx}
        view.set_type_selected(3)
        self.assertNotEqual(self.model.notes_tree[0].note_type, 3)
        gated = [a for a in self.win._pan_gated_actions]
        self.assertTrue(gated)
        self.assertFalse(any(a.isEnabled() for a in gated))

    def test_selected_soft_falls_back_to_tap(self):
        self.combo.setCurrentIndex(self.win._type_combo_values.index(1))
        self.save(os.path.join(self.folder, 'y.xml'))
        self.assertEqual(self.combo.currentIndex(), 0)
        self.assertEqual(self.win.view._note_input_note_type, 0)

    def test_saving_json_again_unlocks(self):
        self.save(os.path.join(self.folder, 'z.xml'))
        self.save(os.path.join(self.folder, 'z.json'))
        self.assertFalse(self.model.pan_xml)
        self.assertTrue(self.type_enabled(3))

    def test_ctrl_s_on_a_midi_goes_to_save_as(self):
        self.model.current_file = os.path.join(self.folder, 'a.mid')
        with mock.patch.object(self.win, 'save_file_as') as save_as, \
                mock.patch.object(self.win, '_do_save') as do_save:
            self.win.save_file()
        self.assertTrue(save_as.called)
        self.assertFalse(do_save.called)

    def test_save_as_defaults_to_json(self):
        self.model.current_file = None
        self.model._song_name = 'abc'
        with mock.patch('qt_editor.main_window.QFileDialog.getSaveFileName',
                        return_value=('', '')) as dialog:
            self.win.save_file_as()
        default, filters = dialog.call_args[0][2], dialog.call_args[0][3]
        self.assertTrue(default.endswith('.json'))
        self.assertTrue(filters.startswith('JSON'))

    def test_opening_an_old_editor_xml_converts_it(self):
        old = NoteModel()
        old.load_json(self.json_path)
        old.pan_xml = True                        # 當成舊版編輯器存的 XML 開進來
        with mock.patch('qt_editor.main_window.QMessageBox.information') as info:
            self.win._load_model_all(old)
            self.win._convert_loaded_xml(old)
        self.assertTrue(info.called)
        self.assertFalse({1, 3} & {n.note_type for n in old.notes_tree})
        self.assertEqual(old.pedal_spans, [])


class EventDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False          # 不然關視窗會跳「要不要存檔」
        cls.win.close()

    def setUp(self):
        self.model, _path = json_chart(tempfile.mkdtemp())
        self.model.set_measure_bpm(2, 90.0)
        self.win._load_model_all(self.model)

    def dialog(self):
        from qt_editor.event_dialog import EventEditorDialog
        return EventEditorDialog(self.win, self.model, current_ms=0, jump=self.win._jump_to_ms)

    def test_a_chart_without_events_starts_with_defaults(self):
        dlg = self.dialog()
        types = [ty for _ms, ty, _v in dlg.events()]
        self.assertEqual(types.count(0), 3)           # 開頭、第 3 小節變 90、第 4 小節回 120
        self.assertTrue(set(range(1, 9)) <= set(types))

    def test_rebuild_tempo_replaces_only_tempo(self):
        self.model.events = [[0, 0, 5000000], [100, 1, 99]]
        dlg = self.dialog()
        dlg._rebuild_tempo()
        events = dlg.events()
        self.assertIn([100, 1, 99], events)
        self.assertNotIn([0, 0, 5000000], events)
        self.assertEqual(events[0][:2], [0, 0])

    def test_filter_and_delete(self):
        dlg = self.dialog()
        dlg._filter.setCurrentIndex(1)                # 只看速度
        visible = [r for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r)]
        self.assertEqual(len(visible), 3)
        dlg.table.selectRow(visible[1])
        dlg._delete_selected()
        self.assertEqual([ty for _m, ty, _v in dlg.events()].count(0), 2)

    def test_tempo_value_is_shown_as_bpm(self):
        dlg = self.dialog()
        from qt_editor.event_dialog import COL_VALUE
        self.assertEqual(dlg.table.item(0, COL_VALUE).text(), '120 BPM')

    def test_ok_writes_events_and_undo_removes_them(self):
        def fake_exec(dlg):
            dlg._rebuild_tempo()
            return QDialog.Accepted
        from qt_editor.event_dialog import EventEditorDialog
        with mock.patch.object(EventEditorDialog, 'exec_', fake_exec):
            self.win.edit_events_dialog()
        self.assertTrue(self.model.events)
        self.assertTrue(self.model.dirty)
        self.win.view.undo()
        self.assertEqual(self.model.events, [])

    def test_zero_bpm_is_rejected(self):
        dlg = self.dialog()
        from qt_editor.event_dialog import COL_VALUE, RAW
        dlg.table.item(0, COL_VALUE).setData(RAW, 0)
        with mock.patch('qt_editor.event_dialog.QMessageBox.warning') as warn:
            dlg._accept()
        self.assertTrue(warn.called)
        self.assertNotEqual(dlg.result(), QDialog.Accepted)


if __name__ == '__main__':
    unittest.main()
