"""這一批是「已經出過事」的那些洞，每一條都對應一個真的會發生的情境。

它們散在四個模組裡，共同點是：壞掉的時候沒有錯誤訊息，只有一個說不通的結果
（設定跑掉、清單全空、只剩一顆音符、功能永遠說「請先存檔」）。
"""

import io
import json
import os
import shutil
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import difficulty as D  # noqa: E402
from qt_editor import song_folder as SF  # noqa: E402
from qt_editor.song_library import resolve_resource  # noqa: E402
from qt_editor.settings import _DEFAULTS  # noqa: E402
from test_difficulty import chart  # noqa: E402


class ResourcePathTests(unittest.TestCase):
    """一首壞掉的歌不能讓整個曲庫清單打不開。"""

    def test_a_short_resource_path_is_unresolved_not_a_crash(self):
        # 以前 split('/', 2)[2] 會丟 IndexError，而這個函式是在 songs() 的迴圈
        # 裡呼叫的 —— 一首壞掉，曲庫管理整個開不出來。
        self.assertIsNone(resolve_resource(tempfile.gettempdir(), 'songs/Alpha', ('.json',)))
        self.assertIsNone(resolve_resource(tempfile.gettempdir(), 'songs', ('.json',)))
        self.assertIsNone(resolve_resource(tempfile.gettempdir(), '', ('.json',)))

    def test_backslashes_resolve_like_forward_slashes(self):
        root = tempfile.mkdtemp()
        try:
            folder = os.path.join(root, 'Real')
            os.makedirs(folder)
            target = os.path.join(folder, 'Song.json')
            with io.open(target, 'w', encoding='utf-8') as fh:
                fh.write('{}')
            forward = resolve_resource(root, 'songs/Alpha/Real/Song', ('.json',))
            backward = resolve_resource(root, 'songs\\Alpha\\Real\\Song', ('.json',))
            self.assertEqual(forward, target)
            self.assertEqual(backward, target, 'register 是使用者會手改的檔案')
        finally:
            shutil.rmtree(root, ignore_errors=True)


class SettingsKeyTests(unittest.TestCase):
    """load() 只留下列在 _DEFAULTS 裡的鍵，所以在用的鍵一定要在裡面。"""

    def test_every_setting_the_app_writes_is_registered(self):
        for key in ('dark_mode', 'keyboard_height_px', 'show_statusbar'):
            self.assertIn(key, _DEFAULTS, '%s 沒登記的話，重開就會被丟掉' % key)


class ScanRobustnessTests(unittest.TestCase):
    """壞掉的 register.json 要變成「這一首跳過」，不是整趟掃描死掉。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.song_dir = os.path.join(self.root, 'Broken')
        os.makedirs(self.song_dir)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text):
        with io.open(os.path.join(self.song_dir, 'register.json'), 'w', encoding='utf-8') as fh:
            fh.write(text)

    def test_a_truncated_register_is_a_song_folder_error(self):
        self._write('{"difficulties": [')          # 遊戲被中途砍掉就會留下這種檔案
        with self.assertRaises(SF.SongFolderError):
            SF.plan_song(self.song_dir)

    def test_a_scan_skips_the_broken_song_and_keeps_going(self):
        self._write('{"difficulties": [')
        good = os.path.join(self.root, 'Fine')
        os.makedirs(os.path.join(good, 'Real'))
        with io.open(os.path.join(good, 'Real', 'Fine.json'), 'w', encoding='utf-8') as fh:
            fh.write('{"notes": []}')
        with io.open(os.path.join(good, 'register.json'), 'w', encoding='utf-8') as fh:
            json.dump({'difficulties': [{'difficultyName': 'Real', 'difficultyLevel': 14,
                                         'chartFileName': 'songs/Fine/Real/Fine'}]}, fh)

        rows = SF.scan_library(self.root, lambda path: [])
        names = {r.song for r in rows}
        self.assertIn('Broken', names, '壞掉的那首要出現在清單上，附理由')
        self.assertIn('Fine', names, '壞掉的那首不該拖垮其他人')


class AtomicWriteTests(unittest.TestCase):
    """register.json 一律先寫暫存檔再改名。"""

    def test_commit_leaves_no_temp_file_and_writes_valid_json(self):
        root = tempfile.mkdtemp()
        try:
            song_dir = os.path.join(root, 'Song')
            os.makedirs(os.path.join(song_dir, 'Real'))
            entry = {'difficultyName': 'Real', 'difficultyLevel': 14,
                     'chartFileName': 'songs/Song/Real/Song'}
            with io.open(os.path.join(song_dir, 'register.json'), 'w', encoding='utf-8') as fh:
                json.dump({'difficulties': [entry]}, fh)

            plan_obj = SF.SongPlan(song_dir, {'difficulties': [entry]}, entry, 'Song', [], [])
            record = dict(entry)
            record['difficultyName'] = 'Hard'
            SF.commit(plan_obj, [record])

            path = os.path.join(song_dir, 'register.json')
            self.assertFalse(os.path.exists(path + '.tmp'), '暫存檔要被改名掉，不是留著')
            with io.open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            self.assertEqual([d['difficultyName'] for d in data['difficulties']], ['Hard', 'Real'])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ShortChartTests(unittest.TestCase):
    """短曲子不能被砍到只剩一顆音符。"""

    def _visible(self, notes):
        return sum(1 for n in notes if not getattr(n, 'hidden', False))

    def test_a_two_second_chart_keeps_a_sensible_share(self):
        # 20 顆、每 100ms 一顆、總長 1.9 秒。以前 normal / hard 都只留 1 顆：
        # 量不出同手間隔時傳回 0，被讀成「無限快」，二分就一路走到 4000ms 上限。
        for key in ('normal', 'hard'):
            model = chart([(i * 100, [(60 + i % 7, 0, 80)]) for i in range(20)])
            D.generate(model, key)
            visible = self._visible(model.notes_tree)
            self.assertGreaterEqual(visible, 4, '%s 砍過頭了：只剩 %d 顆' % (key, visible))

    def test_the_ladder_does_not_invert_on_a_short_chart(self):
        counts = {}
        for key in ('normal', 'hard', 'extreme'):
            model = chart([(i * 100, [(60 + i % 7, 0, 80)]) for i in range(20)])
            D.generate(model, key)
            counts[key] = self._visible(model.notes_tree)
        self.assertLessEqual(counts['normal'], counts['hard'])
        self.assertLessEqual(counts['hard'], counts['extreme'])


if __name__ == '__main__':
    unittest.main()
