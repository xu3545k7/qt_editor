"""NosMania 第四格：Hiraeth 套件的操作與雙向同步。

真的那個套件不在測試環境裡，所以這裡做一份「長得一樣」的假套件：
SONG_MANAGER.bat、tools/manager_backend.py（吃 job.json 寫 out.json）、
imported.json 與 contents 底下攤開的 wav／xml／封面。
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import hiraeth_sync as S  # noqa: E402
from qt_editor import hiraeth_tools as T  # noqa: E402
from qt_editor import song_library as L  # noqa: E402
from test_song_library import make_library, write_wav  # noqa: E402

BACKEND = '''# 假的 Hiraeth 管理器：list / import / delete 都寫進 state.json
import json, os, sys, zipfile
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
state = os.path.join(root, 'tools', 'state.json')
data = json.load(open(state, encoding='utf-8'))
job = json.load(open(sys.argv[1], encoding='utf-8'))
out = {'ok': True, 'songs': data['songs'], 'results': [], 'errors': []}
if job['action'] == 'import':
    reg_path = os.path.join(root, 'tools', 'song_library', 'imported.json')
    registry = json.load(open(reg_path, encoding='utf-8'))
    suffix = {'normal': '_00normal.xml', 'hard': '_01hard.xml', 'expert': '_02extreme.xml',
              'real': '_03real.xml'}
    for path in job['paths']:
        with zipfile.ZipFile(path) as z:
            spec = json.loads(z.read('song.json').decode('utf-8'))
            index = 700 + len(registry['packages'])
            base = 'M_C%04d_test' % index
            music = os.path.join(root, 'contents', 'data', 'sound', 'music', base)
            os.makedirs(music, exist_ok=True)
            open(os.path.join(music, base + '.wav'), 'wb').write(z.read('music.wav'))
            for slot, end in suffix.items():
                if slot + '.xml' in z.namelist():
                    open(os.path.join(music, base + end), 'wb').write(z.read(slot + '.xml'))
        registry['packages'][spec['package_id']] = {'index': index, 'basename': base,
                                                    'version': spec['version'], 'files': {}}
        levels = ' / '.join('%s %s' % ({'normal': 'N', 'hard': 'H', 'expert': 'EX',
                                         'real': 'REAL'}[k], v)
                            for k, v in spec['charts'].items())
        data['songs'] = [s for s in data['songs'] if s['key'] != spec['package_id']]
        data['songs'].append({'key': spec['package_id'], 'index': index,
                              'title': spec['title'], 'artist': spec.get('artist', ''),
                              'levels': levels, 'version': spec['version']})
        out['results'].append({'key': spec['package_id']})
    json.dump(registry, open(reg_path, 'w', encoding='utf-8'), ensure_ascii=False)
    out['songs'] = data['songs']
    json.dump(data, open(state, 'w', encoding='utf-8'), ensure_ascii=False)
elif job['action'] == 'delete':
    data['songs'] = [s for s in data['songs'] if s['key'] not in job['keys']]
    out['songs'] = data['songs']
    json.dump(data, open(state, 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(out, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
'''


def fake_hiraeth(root, songs=()):
    """假的 Hiraeth 套件；`songs` 是已經裝在裡面的曲目。"""
    os.makedirs(os.path.join(root, 'tools'), exist_ok=True)
    io.open(os.path.join(root, T.MANAGER_BAT), 'w').write('@echo off')
    io.open(os.path.join(root, T.BACKEND), 'w', encoding='utf-8').write(BACKEND)
    listed, packages = [], {}
    for i, (key, title) in enumerate(songs):
        basename = 'M_C%04d_%s' % (600 + i, key.replace('.', '')[:12])
        music = os.path.join(root, 'contents', 'data', 'sound', 'music', basename)
        os.makedirs(music, exist_ok=True)
        write_wav(os.path.join(music, basename + '.wav'))
        for suffix in ('_00normal.xml', '_03real.xml'):
            shutil.copy2(chart_xml(root), os.path.join(music, basename + suffix))
        cover = os.path.join(root, 'contents', 'data_mods', 'jk%d_l.png' % (600 + i))
        os.makedirs(os.path.dirname(cover), exist_ok=True)
        from PIL import Image
        Image.new('RGB', (100, 100), (20, 30, 40)).save(cover)
        listed.append({'key': key, 'index': 600 + i, 'title': title, 'artist': 'Someone',
                       'levels': 'N 5 / REAL 3', 'version': 1})
        packages[key] = {'index': 600 + i, 'basename': basename, 'version': 1,
                         'files': {os.path.relpath(cover, root).replace(os.sep, '/'): ''}}
    os.makedirs(os.path.join(root, 'tools', 'song_library'), exist_ok=True)
    with io.open(os.path.join(root, T.IMPORTED_JSON), 'w', encoding='utf-8') as fh:
        json.dump({'packages': packages}, fh, ensure_ascii=False)
    with io.open(os.path.join(root, 'tools', 'state.json'), 'w', encoding='utf-8') as fh:
        json.dump({'songs': listed}, fh, ensure_ascii=False)
    return root


_XML_CACHE = {}


def chart_xml(root):
    """一份最小的 music_score XML（用製譜器自己存的，格式一定對）。"""
    if 'path' not in _XML_CACHE:
        from qt_editor.models import GNote, NoteModel
        model = NoteModel.create_new('t', 120.0, 10.0, 4)
        notes = []
        for i in range(4):
            n = GNote(None, i)
            n.start, n.end, n.gate = 2000 + i * 300, 2000 + i * 300 + 150, 150
            n.pitch, n.hand, n.note_type = 60 + i, i % 2, 0
            n.min_key, n.max_key = 3, 5
            notes.append(n)
        model.notes_tree = notes
        model.music_end_ms = 4000.0
        model.rebuild_display_cache()
        path = os.path.join(tempfile.mkdtemp(), 'chart.xml')
        model.save_xml(path)
        _XML_CACHE['path'] = path
    return _XML_CACHE['path']


class ToolkitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = fake_hiraeth(os.path.join(self.tmp, 'Hiraeth_Public_v2'),
                                 [('hiraeth.hanon-120', 'ハノン 120')])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_toolkit_one_level_down(self):
        self.assertTrue(T.is_root(self.root))
        self.assertEqual(T.find_root(self.tmp), self.root)
        self.assertEqual(T.find_root(os.path.join(self.tmp, 'nope')), '')

    def test_lists_installed_songs(self):
        songs = T.list_songs(self.root)
        self.assertEqual([s['title'] for s in songs], ['ハノン 120'])

    def test_imports_a_package(self):
        package = self.make_package(os.path.join(self.tmp, 'new.zip'), 'nostalgia-clone.x-1234')
        with mock.patch.object(T, 'game_running', return_value=False):
            done, errors = T.import_packages(self.root, [package])
        self.assertEqual(errors, [])
        self.assertEqual(len(done), 1)
        self.assertIn('nostalgia-clone.x-1234', [s['key'] for s in T.list_songs(self.root)])

    def test_deleting_needs_the_game_closed(self):
        with mock.patch.object(T, 'game_running', return_value=True):
            with self.assertRaises(T.HiraethError):
                T.delete_songs(self.root, ['hiraeth.hanon-120'])
        with mock.patch.object(T, 'game_running', return_value=False):
            T.delete_songs(self.root, ['hiraeth.hanon-120'])
        self.assertEqual(T.list_songs(self.root), [])

    def make_package(self, path, package_id):
        folder = os.path.join(self.tmp, 'pkg')
        os.makedirs(folder, exist_ok=True)
        write_wav(os.path.join(folder, 'music.wav'))
        shutil.copy2(chart_xml(self.root), os.path.join(folder, 'real.xml'))
        with io.open(os.path.join(folder, 'song.json'), 'w', encoding='utf-8') as fh:
            json.dump({'format': 1, 'package_id': package_id, 'title': 'New Song',
                       'artist': 'A', 'charts': {'real': 3}, 'version': 2}, fh)
        with zipfile.ZipFile(path, 'w') as z:
            for name in sorted(os.listdir(folder)):
                z.write(os.path.join(folder, name), name)
        return path

    def test_extracting_a_song_back_out(self):
        out = os.path.join(self.tmp, 'out')
        song = T.list_songs(self.root)[0]
        T.extract_package(self.root, song['key'], song, out)
        self.assertEqual(sorted(os.listdir(out)),
                         ['cover.png', 'music.wav', 'normal.xml', 'real.xml', 'song.json'])
        with io.open(os.path.join(out, 'song.json'), encoding='utf-8') as fh:
            spec = json.load(fh)
        self.assertEqual(spec['charts'], {'normal': 5, 'real': 3})
        self.assertEqual(spec['title'], 'ハノン 120')


class TwoWaySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.library_root = make_library(self.tmp)
        self.lib = L.SongLibrary(self.library_root)
        self.hiraeth = fake_hiraeth(os.path.join(self.tmp, 'Hiraeth_Public_v2'),
                                    [('hiraeth.hanon-120', 'ハノン 120')])
        self._patch = mock.patch.object(T, 'game_running', return_value=False)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sync(self, **kwargs):
        return S.sync(self.library_root, self.hiraeth, library=self.lib,
                      renderer=lambda model: (b'\0\0\0\0' * 44100, 44100), **kwargs)

    def test_hiraeth_only_songs_come_back(self):
        missing = S.missing_in_library(self.library_root, self.hiraeth)
        self.assertEqual([s['key'] for s in missing], ['hiraeth.hanon-120'])
        summary = self.sync()
        self.assertEqual(len(summary['pulled']), 1)
        folders = {s.folder for s in self.lib.songs()}
        self.assertIn('ハノン 120', folders)

    def test_library_songs_go_to_hiraeth(self):
        summary = self.sync()
        titles = {s['title'] for s in T.list_songs(self.hiraeth)}
        self.assertIn('Alpha Song', titles)
        self.assertIn('Beta Song', titles)
        self.assertGreaterEqual(summary['imported'], 2)

    def test_nothing_is_deleted_on_either_side(self):
        self.sync()
        self.assertIn('hiraeth.hanon-120', [s['key'] for s in T.list_songs(self.hiraeth)])
        self.assertIn('Alpha', {s.folder for s in self.lib.songs()})

    def test_a_second_sync_skips_everything(self):
        self.sync()
        summary = self.sync()
        self.assertEqual(summary['imported'], 0)
        self.assertEqual(summary['pulled'], [])
        self.assertGreater(summary['unchanged'], 0)

    def test_force_redoes_everything(self):
        self.sync()
        summary = self.sync(force=True)
        self.assertGreaterEqual(summary['imported'], 2)
        self.assertEqual(summary['unchanged'], 0)

    def test_an_edited_song_is_sent_again(self):
        self.sync()
        chart = os.path.join(self.library_root, 'Alpha', 'Real', 'Alpha.json')
        with io.open(chart, encoding='utf-8') as fh:
            text = fh.read()
        with io.open(chart, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.utime(chart, (0, 0))
        todo, unchanged, _skipped = S.plan_sync(self.library_root, S.installed_keys(self.hiraeth))
        self.assertEqual([p.label for p in todo], ['Alpha'])
        self.assertTrue(unchanged)


if __name__ == '__main__':
    unittest.main()


class RoundTripTests(unittest.TestCase):
    """XML → JSON → XML：從 Hiraeth 搬回來、再送回去，音符時間與長度不能跑掉。

    JSON 是原始檔、XML 是轉出去的格式（見 pan-xml-compat）。轉出去會做兩件會改到
    時間的事：長押縮成官方長度（預設 80%）、開頭補空白小節。兩件都只在「這份譜
    本來不是從官方 XML 來的」才做，所以搬回來又送回去的譜不會被縮兩次、也不會
    一直往後推。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.library_root = make_library(self.tmp)
        self.lib = L.SongLibrary(self.library_root)
        self.hiraeth = fake_hiraeth(os.path.join(self.tmp, 'Hiraeth_Public_v2'),
                                    [('hiraeth.hanon-120', 'ハノン 120')])
        self._patch = mock.patch.object(T, 'game_running', return_value=False)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def notes_of(self, xml_path):
        import xml.etree.ElementTree as ET
        root = ET.parse(xml_path).getroot()
        return [(int(n.findtext('start_timing_msec')), int(n.findtext('end_timing_msec')))
                for n in root.iter('note')]

    def test_a_pulled_song_survives_going_back(self):
        from qt_editor import hiraeth_export as EX
        original = self.notes_of(chart_xml(self.hiraeth))
        S.sync(self.library_root, self.hiraeth, library=self.lib,
               renderer=lambda model: (b'\0\0\0\0' * 44100, 44100))
        folder = [s.folder for s in self.lib.songs() if s.folder == 'ハノン 120'][0]
        plans, _ = EX.plan_song_folder(os.path.join(self.library_root, folder))
        out = os.path.join(self.tmp, 'back')
        result = EX.build_package(plans[0], out, version=9,
                                  renderer=lambda model: (b'\0\0\0\0' * 44100, 44100))
        self.assertEqual(result.error, '')
        folder_out = os.path.join(self.tmp, 'unzipped')
        with zipfile.ZipFile(result.zip_path) as archive:
            archive.extractall(folder_out)
        again = self.notes_of(os.path.join(folder_out, 'real.xml'))
        self.assertEqual(again, original, '搬回來再送回去，音符時間和長度要一模一樣')

    def test_the_official_flag_is_kept_in_the_json(self):
        import json as _json
        S.sync(self.library_root, self.hiraeth, library=self.lib,
               renderer=lambda model: (b'\0\0\0\0' * 44100, 44100))
        chart = os.path.join(self.library_root, 'ハノン 120', 'Real', 'ハノン 120.json')
        with io.open(chart, encoding='utf-8') as fh:
            data = _json.load(fh)
        self.assertTrue(data.get('hold_lengths_official'),
                        '從官方 XML 轉來的譜要標記起來，再輸出才不會又縮一次長押')


class GamePickerTests(unittest.TestCase):
    """「進入遊戲」：兩個遊戲各自啟動／強制停止；沒指定 Hiraeth 套件就是灰的。"""

    def setUp(self):
        from qt_editor.launcher import GamePicker
        from qt_editor.settings import settings
        self.settings = settings
        self.tmp = tempfile.mkdtemp()
        self.game_dir = os.path.join(self.tmp, 'Game')
        os.makedirs(os.path.join(self.game_dir, 'Game_Data'))
        self.exe = os.path.join(self.game_dir, 'Game.exe')
        io.open(self.exe, 'w').write('game')
        self._saved = {k: settings._data.get(k) for k in ('game_exe_path', 'hiraeth_root')}
        settings._data.update(game_exe_path=self.exe, hiraeth_root='')
        self._patches = [mock.patch.object(settings, 'save'),
                         mock.patch('qt_editor.library_manager.game_running', return_value=False),
                         mock.patch.object(T, 'game_running', return_value=False),
                         mock.patch.object(T, 'default_root_guess', return_value='')]
        for p in self._patches:
            p.start()
        self.picker = GamePicker(None)

    def tearDown(self):
        self.picker.close()
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            self.settings._data[k] = v if v is not None else ''
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hiraeth_is_greyed_out_without_a_toolkit(self):
        self.assertTrue(self.picker.rows['clone']['box'].isEnabled())
        self.assertFalse(self.picker.rows['hiraeth']['box'].isEnabled())

    def test_a_toolkit_enables_the_second_game(self):
        root = fake_hiraeth(os.path.join(self.tmp, 'Hiraeth_Public_v2'))
        os.makedirs(os.path.join(root, 'tools', 'runtime', 'python'), exist_ok=True)
        io.open(os.path.join(root, T.RUNTIME_PYTHON), 'w').write('')
        io.open(os.path.join(root, T.LAUNCH_SCRIPT), 'w').write('')
        self.settings._data['hiraeth_root'] = root
        self.picker.refresh()
        self.assertTrue(self.picker.rows['hiraeth']['box'].isEnabled())
        self.assertTrue(self.picker.rows['hiraeth']['start'].isEnabled())
        with mock.patch.object(T, 'launch_game') as launch:
            self.picker.start('hiraeth')
        launch.assert_called_once_with(root)

    def test_running_games_swap_start_for_stop(self):
        with mock.patch('qt_editor.library_manager.game_running', return_value=True):
            self.picker.refresh()
            self.assertFalse(self.picker.rows['clone']['start'].isEnabled())
            self.assertTrue(self.picker.rows['clone']['stop'].isEnabled())

    def test_stopping_asks_first(self):
        from PyQt5.QtWidgets import QMessageBox
        with mock.patch('qt_editor.launcher.QMessageBox.question',
                        return_value=QMessageBox.No) as ask, \
                mock.patch('qt_editor.library_manager.stop_game') as kill:
            self.picker.stop('clone')
        self.assertTrue(ask.called)
        self.assertFalse(kill.called)
        with mock.patch('qt_editor.launcher.QMessageBox.question',
                        return_value=QMessageBox.Yes), \
                mock.patch('qt_editor.library_manager.stop_game', return_value=True) as kill:
            self.picker.stop('clone')
        kill.assert_called_once_with(self.exe)


class BuilderVersionTests(TwoWaySyncTests):
    """輸出規則改版之後，上次同步過的也要重做一次（不然舊包的毛病留在那邊）。"""

    def test_an_old_builder_mark_is_resent(self):
        self.sync()
        state = S.load_state(self.library_root)
        for mark in state.values():
            mark['builder'] = 1                      # 假裝是舊規則做的
        S.save_state(self.library_root, state)
        todo, unchanged, _skipped = S.plan_sync(self.library_root, S.installed_keys(self.hiraeth))
        self.assertEqual(unchanged, [])
        self.assertTrue(todo)

    def test_a_current_builder_mark_is_skipped(self):
        self.sync()
        todo, unchanged, _skipped = S.plan_sync(self.library_root, S.installed_keys(self.hiraeth))
        self.assertEqual(todo, [])
        self.assertTrue(unchanged)


class OverwriteTests(unittest.TestCase):
    """單向覆蓋：以一邊為準。多出來的要另外勾才刪；新手教學一律不動。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.library_root = make_library(self.tmp)
        self.lib = L.SongLibrary(self.library_root)
        self.hiraeth = fake_hiraeth(os.path.join(self.tmp, 'Hiraeth_Public_v2'),
                                    [('hiraeth.hanon-120', 'ハノン 120')])
        self._patch = mock.patch.object(T, 'game_running', return_value=False)
        self._patch.start()
        self.render = lambda model: (b'\0\0\0\0' * 44100, 44100)

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def keys(self):
        return {s['key'] for s in T.list_songs(self.hiraeth)}

    def test_push_sends_everything_even_unchanged(self):
        S.sync(self.library_root, self.hiraeth, library=self.lib, renderer=self.render)
        summary = S.push_overwrite(self.library_root, self.hiraeth, library=self.lib,
                                   renderer=self.render)
        self.assertGreaterEqual(summary['imported'], 2, '覆蓋不看有沒有改過，全部重送')
        self.assertIn('hiraeth.hanon-120', self.keys(), '沒勾刪除就不刪')

    def test_push_can_delete_what_nos_clone_does_not_have(self):
        summary = S.push_overwrite(self.library_root, self.hiraeth, library=self.lib,
                                   delete_extra=True, renderer=self.render)
        self.assertEqual(summary['deleted'], ['hiraeth.hanon-120'])
        self.assertNotIn('hiraeth.hanon-120', self.keys())

    def test_pull_replaces_and_keeps_categories(self):
        S.sync(self.library_root, self.hiraeth, library=self.lib, renderer=self.render)
        alpha_key = [k for k in self.keys() if k.startswith('nostalgia-clone.alpha')][0]
        self.assertTrue(alpha_key)
        summary = S.pull_overwrite(self.lib, self.hiraeth)
        self.assertTrue(summary['replaced'])
        self.assertEqual(summary['errors'], [])
        songs = {s.title: s for s in self.lib.songs()}
        self.assertIn('Deemo', songs['Alpha Song'].categories, '分類沿用原本那首的')
        trash = os.path.join(self.tmp, 'UserSongs_editor', 'trash')
        self.assertTrue(os.listdir(trash), '被換掉的原檔要在回收區')

    def test_pull_delete_extra_never_touches_tutorials(self):
        tutorial = os.path.join(self.library_root, '新手教學初階00')
        os.makedirs(os.path.join(tutorial, 'Real'))
        with io.open(os.path.join(tutorial, 'register.json'), 'w', encoding='utf-8') as fh:
            json.dump({'displayName': '初階 0', 'difficulties': []}, fh, ensure_ascii=False)
        summary = S.pull_overwrite(self.lib, self.hiraeth, delete_extra=True)
        folders = {s.folder for s in self.lib.songs()}
        self.assertIn('新手教學初階00', folders)
        self.assertIn('Alpha', summary['deleted'])
        self.assertNotIn('新手教學初階00', summary['deleted'])

    def test_the_panel_has_both_directions(self):
        from qt_editor.hiraeth_panel import HiraethPanel
        from qt_editor.settings import settings
        with mock.patch.object(settings, 'save'), \
                mock.patch('qt_editor.hiraeth_panel.HiraethPanel.reload'):
            panel = HiraethPanel(None)
            try:
                texts = [a.text() for a in panel._overwrite_menu.actions()]
                self.assertEqual(len(texts), 2)
                self.assertTrue(texts[0].startswith('nos-clone → Hiraeth'))
                self.assertTrue(texts[1].startswith('Hiraeth → nos-clone'))
            finally:
                panel.close()


class OverwriteSafetyTests(OverwriteTests):
    def test_a_failed_pull_keeps_the_original(self):
        S.sync(self.library_root, self.hiraeth, library=self.lib, renderer=self.render)
        with mock.patch.object(T, 'extract_package', side_effect=T.HiraethError('broken')):
            summary = S.pull_overwrite(self.lib, self.hiraeth, delete_extra=True)
        self.assertTrue(summary['errors'])
        folders = {s.folder for s in self.lib.songs()}
        self.assertIn('Alpha', folders, '挖檔失敗時原本那首不能先被丟掉')
        self.assertIn('Beta', folders)
        self.assertEqual(summary['deleted'], [], '有失敗就不刪多出來的')
