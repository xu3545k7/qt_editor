# -*- coding: utf-8 -*-
"""核心（譜面模型／排譜／難度）不准綁在 PyQt5 或 Windows 上。

為什麼要守這條：iPad 版要把這些模組原封不動嵌進去跑（UI 另外寫原生的），
所以它們必須在「沒有 PyQt5、沒有 winsound、沒有 C 擴充」的環境下活得下去。
哪天有人圖方便在 `models.py` 裡 `from PyQt5 import QtGui` 拿個 QColor，
桌機版完全看不出問題，iPad 版卻整個起不來——這個測試就是為了當場擋下來。

靜態（讀原始碼）和動態（真的 import）兩邊都查：
・靜態抓得到藏在函式裡的延遲 import
・動態抓得到透過別的模組間接拉進來的相依
"""

import ast
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

#: 要能嵌進 iPad 的模組。新增核心模組時加進來。
CORE_MODULES = (
    'models',               # 譜面模型：音符、小節、拍點、存讀檔
    'smart_chart',          # 自動排譜
    'difficulty',           # 難度計算
    'hand_split',           # 單軌分手
    'music_theory',         # 調性、音程
    'json_layout',          # 手改得動的 JSON 排版
    'smart_resnap',         # 智慧重排吸附
    'beat_grid_check',      # 拍線體檢（量化前的判斷）
    'midi_to_xml_converter',
    'song_library',         # 曲庫索引
    'i18n',                 # 介面字串（原生 UI 也要照這份翻譯）
)

#: 核心不准碰的東西。PyQt5 是介面、其餘是 Windows 專屬。
FORBIDDEN = ('PyQt5', 'winsound', 'simpleaudio', 'msvcrt', 'winreg')

#: 核心可以用的第三方套件——**必須是純 Python**，iOS 上沒有編譯環境。
#: numpy（loudness）和 PIL（hiraeth_export）是 C 擴充，所以那兩個模組
#: 不在核心裡，iPad 版也不提供它們的功能。
ALLOWED_THIRD_PARTY = {'mido'}

HERE = pathlib.Path(__file__).resolve().parent


def source_of(name):
    # utf-8-sig 不是 utf-8：有些檔案開頭帶著 BOM（編輯器或 PowerShell 寫出來的
    # 常常會），Python 自己 import 時會吃掉它，但 `ast.parse` 看到就會報
    # 「invalid non-printable character U+FEFF」——那是讀檔的方式不對，不是
    # 那份原始碼真的有問題。
    return (HERE / 'qt_editor' / (name + '.py')).read_text(encoding='utf-8-sig')


def imported_tops(name):
    """這個模組 import 了哪些頂層套件（含藏在函式裡的）。"""
    tops = set()
    for node in ast.walk(ast.parse(source_of(name))):
        if isinstance(node, ast.Import):
            tops.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split('.')[0])
    return tops


class _Blocker:
    """把一組套件變成「這台機器上沒有」，模擬 iOS 的環境。"""

    def __init__(self, blocked):
        self.blocked = set(blocked)

    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in self.blocked:
            raise ImportError('blocked for the portability check: %s' % name)
        return None


class StaticTests(unittest.TestCase):
    def test_the_core_never_imports_the_gui(self):
        for name in CORE_MODULES:
            for top in imported_tops(name):
                self.assertNotIn(
                    top, FORBIDDEN,
                    '%s.py import 了 %s——iPad 版就是倒在這裡' % (name, top))

    def test_the_core_only_uses_pure_python_packages(self):
        std = set(sys.stdlib_module_names)
        ours = set(CORE_MODULES) | {'qt_editor'}
        for name in CORE_MODULES:
            for top in imported_tops(name):
                if top in std or top in ours or top.startswith('_'):
                    continue
                self.assertIn(
                    top, ALLOWED_THIRD_PARTY,
                    '%s.py 用了 %s；核心只能用純 Python 的套件，'
                    'iOS 上沒有編譯環境' % (name, top))

    def test_mido_really_is_pure_python(self):
        """唯一允許的第三方套件，確認它沒有夾帶 C 擴充。"""
        import mido
        root = pathlib.Path(mido.__file__).parent
        bad = [p.name for p in root.rglob('*')
               if p.suffix in ('.pyd', '.so', '.dll', '.dylib')]
        self.assertEqual(bad, [], 'mido 裡有編譯過的檔案：%s' % bad)


class RuntimeTests(unittest.TestCase):
    """真的在「沒有 Qt」的環境裡跑一遍：import、建譜、存檔、讀回來。"""

    def setUp(self):
        self.blocker = _Blocker(FORBIDDEN + ('numpy', 'PIL'))
        sys.meta_path.insert(0, self.blocker)
        # 已經載入的要清掉，不然 import 直接命中快取、什麼都沒驗到
        self.saved = {k: v for k, v in sys.modules.items()
                      if k.startswith('qt_editor.')}
        for k in self.saved:
            del sys.modules[k]

    def tearDown(self):
        sys.meta_path.remove(self.blocker)
        for k in [k for k in sys.modules if k.startswith('qt_editor.')]:
            del sys.modules[k]
        sys.modules.update(self.saved)

    def test_every_core_module_imports_without_qt(self):
        for name in CORE_MODULES:
            with self.subTest(module=name):
                importlib.import_module('qt_editor.' + name)

    def test_a_chart_round_trips_without_qt(self):
        """載入／存檔是 iPad 版的第一件事，先確認它不需要介面層。"""
        models = importlib.import_module('qt_editor.models')
        m = models.NoteModel.create_new('portable', 150.0, 30.0, 4)
        for i in range(32):
            n = models.GNote(None, i)
            n.start, n.gate = 400 * i, 300
            n.end = n.start + n.gate
            n.pitch, n.hand, n.note_type = 48 + i, i % 2, 0
            n.min_key, n.max_key = (i % 20) + 2, (i % 20) + 4
            n.track = 1
            m.notes_tree.append(n)
        m.rebuild_display_cache()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'portable.json')
            m.save_json(path)
            back = models.NoteModel.create_new('x', 120.0, 10.0, 4)
            back.load_json(path)
            back.rebuild_display_cache()

        self.assertEqual(len(back.notes_tree), 32)
        self.assertEqual([int(n.pitch) for n in back.notes_tree],
                         [48 + i for i in range(32)])
        self.assertAlmostEqual(float(back.bpm), 150.0, places=3)

    def test_the_arranger_runs_without_qt(self):
        """自動排譜是最值錢的那塊，它也不准綁在介面上。"""
        importlib.import_module('qt_editor.smart_chart')
        importlib.import_module('qt_editor.difficulty')


if __name__ == '__main__':
    unittest.main()
