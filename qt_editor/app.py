"""
app.py
======
Qt 樂譜編輯器應用程式入口點。

使用方式
--------
python -m qt_editor.app  [檔案路徑]
# 或
python qt_editor/app.py  [檔案路徑]
"""

import sys
import os
import logging
import faulthandler

# 立即啟用啟動時的日誌與 faulthandler，將例外與 stdout/stderr 記錄到工作目錄下的 qt_editor_launch.log
_base_dir = os.getcwd() if getattr(sys, 'frozen', False) else os.path.join(os.path.dirname(__file__), '..')
_log_path = os.path.join(_base_dir, 'qt_editor_launch.log')
try:
    _logfile = open(_log_path, 'a', encoding='utf-8')
    faulthandler.enable(file=_logfile)
    logging.basicConfig(level=logging.DEBUG, filename=_log_path,
                        format='%(asctime)s %(levelname)s: %(message)s')
    # 將未捕捉例外也記錄下來
    def _excepthook(exc_type, exc_value, exc_tb):
        logging.exception('Uncaught exception', exc_info=(exc_type, exc_value, exc_tb))
        # 也保留原本的行為
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook
except Exception:
    # 若無法開檔，繼續但不阻斷啟動
    pass

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

# 支援兩種啟動方式：作為 package (`python -m qt_editor.app`) 或直接執行
# 當作 script 直接執行時，__package__ 會是 None，此時使用相對路徑加入 sys.path
if __package__ is None:
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    # 使用專案包名的絕對匯入，確保在不同工作目錄或 PyInstaller 解包時也能找到模組
    from qt_editor.main_window import MainWindow
    from qt_editor.settings import settings
    from qt_editor.i18n import set_lang
else:
    from .main_window import MainWindow
    from .settings import settings
    from .i18n import set_lang


def _icon_path() -> str:
    """支援 PyInstaller bundle 和一般執行的 icon 路徑。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包後，資源在 sys._MEIPASS
        return os.path.join(sys._MEIPASS, 'qt_editor', 'icon.png')
    else:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')


def pop_lang_arg(argv: list) -> str:
    """從指令列拿掉 `--lang xx`（或 `--lang=xx`）並回傳 xx；沒有或不認得就回傳空字串。"""
    lang = ''
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == '--lang' and i + 1 < len(argv):
            lang = argv[i + 1]
            del argv[i:i + 2]
        elif arg.startswith('--lang='):
            lang = arg.split('=', 1)[1]
            del argv[i]
        else:
            i += 1
    return lang if lang in ('zh_tw', 'zh_cn', 'en') else ''


def pop_library_arg(argv: list) -> str:
    """從指令列拿掉 `--library 路徑`（或 `--library=路徑`）並回傳路徑；沒有就空字串。"""
    root = ''
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == '--library' and i + 1 < len(argv):
            root = argv[i + 1]
            del argv[i:i + 2]
        elif arg.startswith('--library='):
            root = arg.split('=', 1)[1]
            del argv[i]
        else:
            i += 1
    return root


def remember_library(root: str) -> None:
    """啟動器告訴製譜器曲庫在哪（曲庫和製譜器分開放時，製譜器自己找不到）。"""
    if root and os.path.isdir(root) and settings.get('song_library_root', '') != root:
        settings.set('song_library_root', root)


def main() -> None:
    # 載入設定（語言、捲動方向等）
    settings.load()
    remember_library(pop_library_arg(sys.argv))
    # NosMania 啟動器開製譜器時帶 --lang：沿用啟動器選的語言，並記進設定
    lang = pop_lang_arg(sys.argv)
    if lang and settings.get('language', 'zh_tw') != lang:
        settings.set('language', lang)
    set_lang(settings.get('language', 'zh_tw'))

    # 高 DPI 支援
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    try:
        from .settings import settings as _st
        from .theme import apply_theme
    except ImportError:
        from qt_editor.settings import settings as _st
        from qt_editor.theme import apply_theme
    apply_theme(app, bool(_st.get('dark_mode', False)))
    try:
        from .i18n import t as _t
    except Exception:
        from qt_editor.i18n import t as _t
    app.setApplicationName(_t('wnd_title'))

    # 設定視窗 icon
    icon = QIcon(_icon_path())
    app.setWindowIcon(icon)

    window = MainWindow()

    # NosMania 啟動器叫開譜時，已經開著的製譜器直接接手
    try:
        from qt_editor.editor_ipc import EditorServer
    except ImportError:
        from .editor_ipc import EditorServer
    window._ipc_server = EditorServer(window)

    # 若指令列帶有檔案路徑，自動開啟（有較新的自動備份時 _load_path 會自己問）
    path = sys.argv[1] if len(sys.argv) > 1 else ''
    if path and os.path.isfile(path):
        window._recovery_offered = True
        window.show()
        window._load_path(path)
    else:
        # 上次沒正常關閉留下的備份：視窗出來之後再問，不要擋在啟動前面
        window.enter_editor()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
