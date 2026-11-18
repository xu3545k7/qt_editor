"""日文介面的字型。

**為什麼日文需要自己的字型，中文和英文不用。** Qt 不指定字型時用的是系統
的介面字型；在繁體中文的 Windows 上那是微軟正黑體。它有假名、也有漢字，
所以日文「印得出來」—— 問題是漢字是台灣字形：直／骨／海／今／令／每，
日本的寫法都不一樣。整個日文介面會有一種說不上來的違和感，而且使用者多半
講不出哪裡怪，只覺得這個日文「不太對」。

所以日文介面換成隨包附的**源真ゴシック**（GenShinGothic）。它是源ノ角ゴシック
（Source Han Sans / Noto Sans CJK 的日文部分）轉成 TrueType，和遊戲端日文用的
`GenShinGothic-Bold` 同一套字面 —— 遊戲和工具的日文看起來是同一個東西。這裡用
Regular 不用 Bold：桌面介面的內文本來就不該是粗體，遊戲 HUD 才需要。

**字型檔不在就什麼都不做。** 從原始碼跑、或打包時漏了 `fonts/` 的話，介面退回
系統字型 —— 還是看得懂，不該因為少一個字型檔就開不起來。

字級沿用系統原本的，只換家族名：這裡換掉字級等於把整個介面的版面重排一次，
而且各平台的「剛好」不一樣。
"""

from __future__ import annotations

import os
import sys

_FILE = 'GenShinGothic-Regular.ttf'

# 系統原本的字型。第一次套用前先留一份，換回中文／英文時要還原 ——
# 不留的話一旦切過日文就回不去，整個中文介面會一直用日文字型（漢字變日本字形）。
_system_font = None

# 載進 Qt 之後的家族名。'' = 試過但失敗（檔案不在或 Qt 不收），不要每次換語言都重試。
_family = None


def _font_path() -> str:
    """字型檔的位置。PyInstaller 打包後在 `_MEIPASS/qt_editor/fonts`。"""
    if getattr(sys, 'frozen', False):
        base = os.path.join(getattr(sys, '_MEIPASS', ''), 'qt_editor')
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'fonts', _FILE)


def _load_family() -> str:
    """把字型檔載進 Qt，回傳家族名；失敗回空字串。只做一次。"""
    global _family
    if _family is not None:
        return _family
    _family = ''
    try:
        from PyQt5.QtGui import QFontDatabase
        path = _font_path()
        if not os.path.isfile(path):
            return _family
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            return _family
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _family = families[0]
    except Exception:                   # noqa: BLE001
        _family = ''
    return _family


def apply_ui_font(app=None) -> None:
    """照目前語言設定整個程式的字型。日文以外一律還原成系統字型。

    換語言之後要再叫一次。`QApplication.setFont` 會把字型事件送給還在用預設
    字型的元件，所以不必重建畫面。
    """
    global _system_font
    try:
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication
        if app is None:
            app = QApplication.instance()
        if app is None:
            return
        if _system_font is None:
            _system_font = QFont(app.font())

        try:
            from .i18n import get_lang
        except ImportError:
            from qt_editor.i18n import get_lang

        if get_lang() != 'ja':
            app.setFont(_system_font)
            return

        family = _load_family()
        if not family:
            app.setFont(_system_font)
            return

        font = QFont(_system_font)      # 字級、字重都沿用系統的
        font.setFamily(family)
        app.setFont(font)
    except Exception:                   # noqa: BLE001
        # 字型是外觀問題，不值得讓程式開不起來
        pass
