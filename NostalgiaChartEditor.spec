# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Nostalgia QT chart editor.

Build (from the qt_editor/ directory):
    pyinstaller NostalgiaChartEditor.spec

Produces a single-file windowed exe at: dist/NostalgiaChartEditor-<版本>.exe
（版本取自 qt_editor/version.py，改版只改那一行。）
"""
import ast
import os

from PyInstaller.utils.hooks import collect_submodules


def _read_version():
    """從 qt_editor/version.py 取 __version__，不 import 它。

    用讀檔 + ast 而不是 import：不依賴 sys.path 長什麼樣（spec 是被 exec 的，
    cwd 不一定是專案根目錄），也不會執行到 version.py 以外的任何程式碼。
    路徑以 SPECPATH（PyInstaller 注入的 spec 所在目錄）為基準。
    macOS 的 NostalgiaChartEditor-mac.spec 有同一份邏輯，改動請一起改。
    """
    path = os.path.join(SPECPATH, 'qt_editor', 'version.py')
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == '__version__'
                for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f'{path} 裡找不到 __version__')


VERSION = _read_version()

# graphic/ 內的音符圖、icon 皆需隨包附帶；程式以 __file__/graphic 與 _MEIPASS/qt_editor 定位
datas = [
    ('qt_editor/graphic', 'qt_editor/graphic'),
    ('qt_editor/icon.png', 'qt_editor'),
    ('qt_editor/icon.ico', 'qt_editor'),
    ('qt_editor/Tap.wav', 'qt_editor'),
    ('qt_editor/settings.json', 'qt_editor'),
    # AI 轉譜的工作程序：不在製譜器裡執行，是複製到另外裝的轉譜環境去跑的，
    # 所以要當資料檔帶著（打包進 PYZ 的話就拿不到原始碼了）。
    ('qt_editor/ai_transcribe_worker.py', 'qt_editor'),
    # 轉譜參數的說明文字＝smart_chart.py 裡那些中文註解。打包之後
    # inspect.getsource() 讀不到原始碼，所以原始碼本身也要帶一份，
    # 不然「MIDI 轉譜」的參數表就只剩欄位名沒有解釋（arrange_param_text.py）。
    ('qt_editor/smart_chart.py', 'qt_editor'),
    # 主音源。遊戲端的取樣庫是用同一份的「Bright Steinway」preset 烤的，
    # 換掉這裡就要重跑 render_piano_samples.py，否則兩邊音色會不一樣。
    ('soundfonts/Nice-Steinway-v3.8.sf2', 'soundfonts'),
    ('vendor/fluidsynth/LICENSE', 'fluidsynth'),
]

binaries = [
    ('vendor/fluidsynth/bin/libfluidsynth-3.dll', 'fluidsynth'),
    ('vendor/fluidsynth/bin/SDL3.dll', 'fluidsynth'),
    ('vendor/fluidsynth/bin/sndfile.dll', 'fluidsynth'),
]

# mido 只在 try/except 內動態匯入，需明確收集，避免打包後遺漏
hiddenimports = collect_submodules('mido')
# 「工具 → 延音踏板 → 依和聲生成踏板」是在函式裡才 import 的（避免啟動時就把
# 批次工具拉進來），PyInstaller 的靜態分析看不到，不明講就會漏打包。
hiddenimports += ['generate_pedal', 'batch_restore_expression']
hiddenimports += ['qt_editor.ai_transcribe', 'qt_editor.ai_transcribe_dialog',
                  'qt_editor.ai_grid', 'qt_editor.ai_grid_dialog', 'qt_editor.beat_detect']

a = Analysis(
    ['qt_editor/app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    # 檔名帶版本，好和舊版擺在一起。用連字號而不是空白：exe 常常是從
    # cmd / 捷徑 / 排程叫起來的，路徑有空白就得處處記得加引號。
    name=f'NostalgiaChartEditor-{VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='qt_editor/icon.ico',
)
