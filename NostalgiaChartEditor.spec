# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Nostalgia QT chart editor.

Build (from the qt_editor/ directory):
    pyinstaller NostalgiaChartEditor.spec

Produces a single-file windowed exe at: dist/NostalgiaChartEditor.exe
"""
from PyInstaller.utils.hooks import collect_submodules

# graphic/ 內的音符圖、icon 皆需隨包附帶；程式以 __file__/graphic 與 _MEIPASS/qt_editor 定位
datas = [
    ('qt_editor/graphic', 'qt_editor/graphic'),
    ('qt_editor/icon.png', 'qt_editor'),
    ('qt_editor/icon.ico', 'qt_editor'),
    ('qt_editor/Tap.wav', 'qt_editor'),
    ('qt_editor/settings.json', 'qt_editor'),
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
    name='NostalgiaChartEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='qt_editor/icon.ico',
)
