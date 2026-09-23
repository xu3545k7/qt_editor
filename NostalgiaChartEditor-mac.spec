# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Nostalgia chart editor — macOS (.app bundle).

Build (from the qt_editor/ directory, on a Mac):
    ./build_mac.sh
or by hand:
    pyinstaller NostalgiaChartEditor-mac.spec --noconfirm

Produces: dist_mac/Nos Chart Maker <版本>.app（版本取自 qt_editor/version.py）

和 Windows 版的差別（NostalgiaChartEditor.spec）：
  * onedir + BUNDLE，不是單檔。macOS 的單檔執行檔不是 .app，Finder 點不開，
    而且每次啟動都要解壓到暫存資料夾、和 Gatekeeper 相處不良。
  * 圖示要 .icns（build_mac.sh 會從 icon.png 生一份）。
  * 不帶 Windows 的 FluidSynth DLL。macOS 用 Homebrew 裝的 dylib，
    build_mac.sh 會把它複製進 bundle；找不到就在執行期去 /opt/homebrew/lib
    與 /usr/local/lib 找（midi_preview.fluidsynth_library_candidates）。
  * 音源 SF2 有 187MB 且不在 git repo 裡；有就打包進去，沒有就不打包，
    程式會退回舊音源或告訴使用者找不到音源。
"""
import ast
import os

from PyInstaller.utils.hooks import collect_submodules


def _read_version():
    """從 qt_editor/version.py 取 __version__，不 import 它。

    用讀檔 + ast 而不是 import：不依賴 sys.path 長什麼樣（spec 是被 exec 的，
    cwd 不一定是專案根目錄），也不會執行到 version.py 以外的任何程式碼。
    路徑以 SPECPATH（PyInstaller 注入的 spec 所在目錄）為基準。
    Windows 的 NostalgiaChartEditor.spec 有同一份邏輯，改動請一起改。
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

datas = [
    ('qt_editor/graphic', 'qt_editor/graphic'),
    ('qt_editor/icon.png', 'qt_editor'),
    ('qt_editor/Tap.wav', 'qt_editor'),
    ('qt_editor/settings.json', 'qt_editor'),
    ('qt_editor/ai_transcribe_worker.py', 'qt_editor'),
    # 轉譜參數表的說明文字要讀這份原始碼（arrange_param_text.py）
    ('qt_editor/smart_chart.py', 'qt_editor'),
]

# 音源：有才帶。沒有的話音源預覽會退回 UprightPianoKW，再沒有就出提示。
for sf2 in ('soundfonts/Nice-Steinway-v3.8.sf2',
            'UprightPianoKW-SF2-20220221/UprightPianoKW-20220221.sf2'):
    if os.path.isfile(sf2):
        datas.append((sf2, os.path.dirname(sf2)))

if os.path.isfile('vendor/fluidsynth/LICENSE'):
    datas.append(('vendor/fluidsynth/LICENSE', 'fluidsynth'))

# FluidSynth：build_mac.sh 會把 Homebrew 的 dylib 們複製到 vendor/fluidsynth/mac/
binaries = []
mac_lib_dir = 'vendor/fluidsynth/mac'
if os.path.isdir(mac_lib_dir):
    for name in sorted(os.listdir(mac_lib_dir)):
        if name.endswith('.dylib'):
            binaries.append((os.path.join(mac_lib_dir, name), 'fluidsynth'))

hiddenimports = collect_submodules('mido')
hiddenimports += ['generate_pedal', 'batch_restore_expression']
hiddenimports += ['qt_editor.ai_transcribe', 'qt_editor.ai_transcribe_dialog',
                  'qt_editor.ai_grid', 'qt_editor.ai_grid_dialog',
                  'qt_editor.beat_detect', 'qt_editor.audio_backend_sd',
                  'qt_editor.game_preview']

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
    [],
    exclude_binaries=True,
    name='Nos Chart Maker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,          # 跟著建置這台機器（arm64 或 x86_64）
    codesign_identity=None,    # build_mac.sh 事後做 ad-hoc 簽章
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Nos Chart Maker',
)

app = BUNDLE(
    coll,
    # 檔名帶版本，好和舊版擺在一起；bundle 內的執行檔名維持乾淨的
    # 'Nos Chart Maker'（COLLECT/EXE 的 name），換版不會動到路徑。
    name=f'Nos Chart Maker {VERSION}.app',
    icon='qt_editor/icon.icns' if os.path.isfile('qt_editor/icon.icns') else None,
    bundle_identifier='com.nostalgia.chartmaker',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleDisplayName': 'Nos Chart Maker',
        'CFBundleName': 'Nos Chart Maker',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': '11.0',
        # 譜面檔（.json / .xml）可以直接拖到 App 上開啟
        'CFBundleDocumentTypes': [{
            'CFBundleTypeName': 'Nostalgia chart',
            'CFBundleTypeRole': 'Editor',
            'LSItemContentTypes': ['public.json', 'public.xml'],
            'LSHandlerRank': 'Alternate',
        }],
    },
)
