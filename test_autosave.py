# -*- coding: utf-8 -*-
"""自動儲存：寫成備份、不動原檔、崩潰後救得回來、存檔或放棄後清乾淨。"""
import io
import json
import os
import shutil
import tempfile
import time
import unittest

from qt_editor import autosave as A
from qt_editor.models import GNote, NoteModel


def note(idx, start, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 120, 120
    n.min_key, n.max_key = 5, 7
    n.pitch, n.hand, n.note_type = pitch, 0, 0
    return n


def chart(count=12):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = [note(i, i * 250) for i in range(count)]
    m.rebuild_display_cache()
    return m


class BackupRuleTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.folder = os.path.join(self.root, 'autosave')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def saved_json(self, name='chart.json', count=12):
        m = chart(count)
        path = os.path.join(self.root, name)
        m.save_json(path)
        return m, path

    def test_a_backup_is_not_a_save(self):
        """存備份不能改掉「目前檔案」、也不能把未儲存標記清掉。"""
        m, path = self.saved_json()
        m.notes_tree[0].start = 999
        m.dirty = True
        A.write_backup(m, 'sess', self.folder)
        self.assertEqual(m.current_file, path, 'Ctrl+S 會存到備份資料夾去')
        self.assertTrue(m.dirty, '標題就不會再提醒還沒存檔')
        with io.open(path, encoding='utf-8') as fh:
            self.assertNotIn('999', json.dumps(json.load(fh)['notes'][0]),
                             '原檔不能被動到')

    def test_no_temp_file_is_left_behind(self):
        m, _path = self.saved_json()
        A.write_backup(m, 'sess', self.folder)
        self.assertFalse([n for n in os.listdir(self.folder) if n.endswith('.tmp')])

    def test_the_backup_keeps_the_source_format(self):
        xml_model = chart()
        xml_path = os.path.join(self.root, 'a.xml')
        xml_model.save_xml(xml_path)
        self.assertTrue(A.write_backup(xml_model, 's', self.folder).endswith('.xml'))
        json_model, _ = self.saved_json('b.json')
        self.assertTrue(A.write_backup(json_model, 's', self.folder).endswith('.json'))
        fresh = chart()                                    # 還沒存過的新譜
        self.assertTrue(A.write_backup(fresh, 's', self.folder).endswith('.json'))

    def test_same_file_name_in_different_folders_do_not_collide(self):
        """Normal / Hard / Expert 底下都叫 Melodiniq.json。"""
        a = A.backup_paths(os.path.join(self.root, 'Normal', 'x.json'), 's', self.folder)
        b = A.backup_paths(os.path.join(self.root, 'Hard', 'x.json'), 's', self.folder)
        self.assertNotEqual(a[0], b[0])

    def test_two_new_charts_in_different_sessions_do_not_collide(self):
        self.assertNotEqual(A.backup_paths(None, 'aaaa', self.folder)[0],
                            A.backup_paths(None, 'bbbb', self.folder)[0])

    def test_recovering_brings_back_the_unsaved_edit(self):
        m, path = self.saved_json()
        m.notes_tree[3].start = 4321
        m.dirty = True
        A.write_backup(m, 'sess', self.folder)
        info = A.find_backup(path, self.folder)
        self.assertIsNotNone(info)
        restored = NoteModel()
        A.load_backup(info, restored)
        self.assertIn(4321, [n.start for n in restored.notes_tree])
        self.assertEqual(restored.current_file, path, '還原後存檔要存回原檔')
        self.assertTrue(restored.dirty, '還原只是拿回改動，要不要存由使用者決定')

    def test_a_midi_source_does_not_save_back_as_midi(self):
        """MIDI 存不下鍵道，還原之後要另存新檔。"""
        m = chart()
        m.current_file = os.path.join(self.root, 'song.mid')
        open(m.current_file, 'wb').close()
        os.utime(m.current_file, (time.time() - 100, time.time() - 100))
        A.write_backup(m, 'sess', self.folder)
        info = A.find_backup(m.current_file, self.folder)
        restored = NoteModel()
        A.load_backup(info, restored)
        self.assertIsNone(restored.current_file)

    def test_a_backup_older_than_the_file_is_ignored(self):
        """備份之後原檔又被存過，改動已經在原檔裡了，不要再問。"""
        m, path = self.saved_json()
        A.write_backup(m, 'sess', self.folder)
        later = time.time() + 5
        os.utime(path, (later, later))
        self.assertIsNone(A.find_backup(path, self.folder))
        self.assertEqual(A.list_backups(self.folder), [])

    def test_discarding_removes_everything(self):
        m, path = self.saved_json()
        A.write_backup(m, 'sess', self.folder)
        A.discard_backup(path, 'sess', self.folder)
        self.assertEqual(os.listdir(self.folder), [])

    def test_the_newest_backup_is_listed_first(self):
        first, _ = self.saved_json('one.json')
        second, _ = self.saved_json('two.json')
        A.write_backup(first, 's', self.folder)
        time.sleep(0.02)
        A.write_backup(second, 's', self.folder)
        names = [os.path.basename(b.source) for b in A.list_backups(self.folder)]
        self.assertEqual(names, ['two.json', 'one.json'])

    def test_a_broken_meta_file_is_skipped_not_fatal(self):
        m, path = self.saved_json()
        _data, meta = A.backup_paths(path, 's', self.folder)
        A.write_backup(m, 's', self.folder)
        with io.open(meta, 'w', encoding='utf-8') as fh:
            fh.write('{not json')
        self.assertEqual(A.list_backups(self.folder), [])

    def test_xml_backup_round_trips_hidden_notes(self):
        """XML 備份要和正式存檔一樣無損（隱藏音符併回寄主再拆開）。"""
        m = chart(6)
        # 隱藏音要掛在同一時刻的寄主上（和弦），沒有寄主的話存檔會取消隱藏
        m.notes_tree[2].start, m.notes_tree[2].end = 250, 370
        m.notes_tree[2].pitch = 67
        m.notes_tree[2].hidden = True
        xml_path = os.path.join(self.root, 'h.xml')
        m.save_xml(xml_path)
        m.dirty = True
        m.notes_tree[0].start = 7
        for _ in range(3):                   # 連續備份好幾次也不能越存越多
            A.write_backup(m, 's', self.folder)
        restored = NoteModel()
        A.load_backup(A.find_backup(xml_path, self.folder), restored)
        self.assertEqual(len(restored.notes_tree), 6)
        self.assertEqual(sum(1 for n in restored.notes_tree if n.hidden), 1)


class RepeatedXmlSaveTests(unittest.TestCase):
    """連續存 XML 不能改變記憶體裡的譜，也不能讓隱藏音越存越多。

    存檔時會把隱藏音符的子音併回寄主。以前併完就留在記憶體裡，下一次存檔
    又把它們當成寄主自己的再併一次：編輯器裡隱藏的音符每存一次多一份，
    6 顆存 3 次重新開檔變 9 顆。手動存檔很少連存沒被發現，自動儲存會一直踩到。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.m = chart(6)
        hidden = self.m.notes_tree[2]
        hidden.start, hidden.end, hidden.pitch = 250, 370, 67
        hidden.hidden = True

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_saving_several_times_writes_the_same_file(self):
        outputs = []
        for i in range(3):
            path = os.path.join(self.root, '%d.xml' % i)
            self.m.save_xml(path)
            with io.open(path, encoding='utf-8') as fh:
                outputs.append(fh.read())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_the_reloaded_chart_keeps_its_note_count(self):
        for i in range(3):
            path = os.path.join(self.root, '%d.xml' % i)
            self.m.save_xml(path)
        reloaded = NoteModel()
        reloaded.load_xml(path)
        self.assertEqual(len(reloaded.notes_tree), 6)
        self.assertEqual(sum(1 for n in reloaded.notes_tree if n.hidden), 1)

    def test_saving_leaves_the_hosts_as_they_were(self):
        before = [list(n.sub_elems or []) for n in self.m.notes_tree]
        self.m.save_xml(os.path.join(self.root, 'x.xml'))
        after = [list(n.sub_elems or []) for n in self.m.notes_tree]
        self.assertEqual([len(s) for s in before], [len(s) for s in after])


class WindowWiringTests(unittest.TestCase):
    """主視窗：計時到就備份、播放中不存、存檔／不儲存會清掉備份。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.folder = os.path.join(self.root, 'autosave')
        self._saved_dir = A.autosave_dir
        A.autosave_dir = lambda: self.folder
        self.model = chart()
        self.path = os.path.join(self.root, 'c.json')
        self.model.save_json(self.path)
        os.utime(self.path, (time.time() - 60, time.time() - 60))
        self.win.view.model = self.model
        self.win._is_playing = False
        self.win._autosave_pending = False

    def tearDown(self):
        A.autosave_dir = self._saved_dir
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_dirty_chart_gets_backed_up(self):
        self.model.dirty = True
        self.win._on_autosave_tick()
        self.assertIsNotNone(A.find_backup(self.path))

    def test_a_clean_chart_is_left_alone(self):
        self.model.dirty = False
        self.win._on_autosave_tick()
        self.assertEqual(A.list_backups(), [])

    def test_nothing_is_written_while_playing_but_it_catches_up(self):
        self.model.dirty = True
        self.win._is_playing = True
        self.win._on_autosave_tick()
        self.assertEqual(A.list_backups(), [])
        self.assertTrue(self.win._autosave_pending)
        self.win._is_playing = False
        self.win._on_autosave_tick()
        self.assertIsNotNone(A.find_backup(self.path))

    def test_saving_clears_the_backup(self):
        from unittest import mock
        self.model.dirty = True
        self.win._on_autosave_tick()
        with mock.patch('qt_editor.main_window.QMessageBox.information'), \
                mock.patch.object(self.win, '_block_on_unassigned', return_value=False):
            self.win._do_save(self.path)
        self.assertEqual(os.listdir(self.folder), [])

    def test_the_setting_turns_it_off(self):
        from qt_editor.settings import settings
        saved = settings._data.get('autosave_enabled')
        try:
            settings._data['autosave_enabled'] = False
            self.model.dirty = True
            self.win._on_autosave_tick()
            self.assertEqual(A.list_backups(), [])
            self.win._configure_autosave()
            self.assertFalse(self.win._autosave_timer.isActive())
        finally:
            if saved is None:
                settings._data.pop('autosave_enabled', None)
            else:
                settings._data['autosave_enabled'] = saved
            self.win._configure_autosave()


if __name__ == '__main__':
    unittest.main()
