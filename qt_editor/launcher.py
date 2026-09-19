"""NosMania 啟動器：進入遊戲、管理曲庫、製作譜面。和製譜器是兩支不同的 exe。

- 進入遊戲 → 曲庫管理的 `launch_game()`（曲庫連結、切換曲庫、已開著就不重開）
- 管理曲庫 → 曲庫管理視窗；「在製譜器開啟」交給製譜器
- 製作譜面 → 製譜器 exe。已經開著就叫它到前面／開那份譜（`editor_ipc`），不另開一個

遊戲、曲庫、製譜器是分開放的三樣東西，「位置設定…」各自指定（`LocationsDialog`）；
沒指定才看啟動器所在的資料夾和下一層（`song_library.configured_*`）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence, QPixmap
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                             QGroupBox, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
                             QShortcut, QToolButton, QVBoxLayout, QWidget)

from . import editor_ipc
from .i18n import get_lang, set_lang
from . import song_library as L
from .settings import settings
from .ui_text import LANGUAGES, tr

APP_NAME = 'NosMania'

_STYLE = """
QWidget#home { background: #15161a; }
QLabel#title { color: #e9e9ee; font-size: 30px; font-weight: 600; letter-spacing: 1px; }
QLabel#subtitle { color: #8b8d98; font-size: 13px; }
QFrame#card { background: #202228; border: 1px solid #2e3038; border-radius: 12px; }
QFrame#card:hover { background: #272a32; border-color: #5b8def; }
QFrame#card[primary="true"] { border-color: #3f6fd8; }
QFrame#card[primary="true"]:hover { background: #24304a; border-color: #7aa5ff; }
QLabel#glyph { color: #7aa5ff; font-size: 36px; }
QLabel#cardTitle { color: #e9e9ee; font-size: 19px; font-weight: 600; }
QLabel#cardText { color: #9a9ca6; font-size: 12px; }
QLabel#key { color: #6d6f7a; font-size: 11px; border: 1px solid #3a3c45; border-radius: 4px;
             padding: 0 5px; }
QLabel#info { color: #8b8d98; font-size: 12px; }
QLabel#status { color: #7fd18b; font-size: 12px; }
QPushButton#link { color: #7aa5ff; background: transparent; border: none; font-size: 12px;
                   padding: 0 4px; }
QPushButton#link:hover { text-decoration: underline; }
QToolButton#update { color: #7aa5ff; background: transparent; border: none; font-size: 12px;
                     padding: 0 6px; }
QToolButton#update:hover { text-decoration: underline; }
QComboBox#lang { color: #e9e9ee; background: #202228; border: 1px solid #3a3c45; border-radius: 6px;
                 padding: 4px 10px; min-width: 110px; font-size: 13px; }
QComboBox#lang:hover { border-color: #5b8def; }
QComboBox#lang QAbstractItemView { color: #e9e9ee; background: #202228;
                                   selection-background-color: #3f6fd8; }
"""


def icon_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'qt_editor', 'icon.png')   # type: ignore[attr-defined]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')


def count_songs(root: str) -> int:
    """曲庫裡有 register.json 的資料夾數（首頁只顯示數字，不讀整份曲庫）。"""
    try:
        return sum(1 for name in os.listdir(root)
                   if os.path.isfile(os.path.join(root, name, 'register.json')))
    except OSError:
        return 0


def editor_exe() -> str:
    """位置設定指定的製譜器優先，否則啟動器旁邊找到的。"""
    return L.configured_editor_exe()


def editor_command(path: Optional[str] = None) -> List[str]:
    """開製譜器的指令。沒打包（開發中）就用 python -m qt_editor.app。

    帶上曲庫位置：製譜器和曲庫分開放時，它自己找不到曲庫（匯出完整曲目要丟進去）。
    """
    exe = editor_exe()
    root = L.default_root()
    args = ['--lang', get_lang()] + (['--library', root] if root else []) + ([path] if path else [])
    if exe:
        return [exe] + args
    if not getattr(sys, 'frozen', False):
        return [sys.executable, '-m', 'qt_editor.app'] + args
    return []


GAME_LANGUAGE_FILE = 'nosmania_launcher.json'
GAME_LANGUAGE = {'zh_tw': 'TraditionalChinese', 'zh_cn': 'SimplifiedChinese', 'en': 'English'}


def write_game_language(game_dir: str, lang: str, library_root: str = '') -> str:
    """遊戲讀的設定檔（語言＋曲庫位置）。stamp 每次都換，遊戲看到比上次套用的新才換語言。"""
    import json
    import time
    path = os.path.join(game_dir, GAME_LANGUAGE_FILE)
    data = {'language': GAME_LANGUAGE.get(lang, 'TraditionalChinese'), 'code': lang,
            'stamp': int(time.time() * 1000)}
    if library_root:
        data['library_root'] = os.path.abspath(library_root)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        return ''
    return path


def _editor_cwd(command: List[str]) -> str:
    if command and command[0].lower().endswith('.exe') and command[0] != sys.executable:
        return os.path.dirname(command[0])
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dispose(widget: QWidget) -> None:
    """換語言時丟掉舊畫面。先擋掉所有子元件的訊號：刪除途中下拉選單清空、
    表格清空會發 currentIndexChanged 之類的訊號，接到的 lambda 再去碰已刪掉的
    元件就會整個程式崩潰。"""
    from PyQt5.QtCore import QObject
    widget.hide()
    for child in widget.findChildren(QObject):
        child.blockSignals(True)
    widget.blockSignals(True)
    widget.deleteLater()


class GamePicker(QDialog):
    """「進入遊戲」：兩個遊戲各自啟動與強制停止。

    Hiraeth 那邊要有指定好的套件（第四格裡選的）才能開，沒有就整列變灰。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('進入遊戲'))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.rows = {}
        for key, name, hint in (
                ('clone', tr('Nos-clone'), tr('這個資料夾裡的遊戲')),
                ('hiraeth', tr('Hiraeth（PAN 歌曲管理版）'), tr('第四格指定的那份套件'))):
            box = QGroupBox(name)
            row = QHBoxLayout(box)
            label = QLabel(hint)
            label.setStyleSheet('color: gray;')
            row.addWidget(label, 1)
            start = QPushButton(tr('啟動'))
            start.clicked.connect(lambda _c=False, k=key: self.start(k))
            stop = QPushButton(tr('強制停止'))
            stop.clicked.connect(lambda _c=False, k=key: self.stop(k))
            row.addWidget(start)
            row.addWidget(stop)
            layout.addWidget(box)
            self.rows[key] = {'box': box, 'label': label, 'start': start, 'stop': stop}

        row = QHBoxLayout()
        row.addStretch(1)
        self.install_btn = QPushButton(tr('從 ZIP 開啟遊戲…'))
        self.install_btn.setToolTip(tr('選一份打包好的遊戲（ZIP 或資料夾），裝到 NosMania 旁邊，'
                                       '順便認出裡面有沒有製譜器和曲庫。'
                                       'NosMania 旁邊放著的遊戲包會自己找到。'))
        self.install_btn.clicked.connect(lambda _c=False: self.install_from_zip())
        row.addWidget(self.install_btn)
        layout.addLayout(row)

        self.status = QLabel('')
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ── 狀態 ─────────────────────────────────────────────────────────

    @staticmethod
    def clone_exe() -> str:
        from .library_manager import configured_game_exe
        return configured_game_exe()

    @staticmethod
    def hiraeth_root() -> str:
        from . import hiraeth_tools as HT
        return HT.saved_root() or HT.default_root_guess()

    def state(self, key: str):
        """(能不能開, 有沒有在跑, 說明)。"""
        from . import hiraeth_tools as HT
        from .library_manager import game_running
        if key == 'clone':
            exe = self.clone_exe()
            if not exe:
                package = self.found_package()
                if package:
                    return False, False, tr('旁邊有還沒解壓的遊戲包：%s') % os.path.basename(package)
                return False, False, tr('還沒指定遊戲：按首頁的「位置設定…」')
            return True, game_running(exe), os.path.basename(os.path.dirname(exe))
        root = self.hiraeth_root()
        if not HT.can_launch(root):
            return False, False, tr('還沒指定 Hiraeth 套件（在第四格裡選）')
        return True, HT.game_running(), root

    def found_package(self) -> str:
        """NosMania 旁邊那包還沒解壓的遊戲 ZIP（沒有就空字串）。"""
        packages = L.find_game_packages()
        return packages[0] if packages else ''

    def refresh(self) -> None:
        package = self.found_package()
        if hasattr(self, 'install_btn'):
            self.install_btn.setText(tr('從這包裝起來：%s') % os.path.basename(package) if package
                                     else tr('從 ZIP 開啟遊戲…'))
        for key, row in self.rows.items():
            ready, running, hint = self.state(key)
            row['box'].setEnabled(ready)
            row['start'].setEnabled(ready and not running)
            row['stop'].setEnabled(ready and running)
            row['label'].setText((tr('● 執行中　') if running else '') + hint)
            row['label'].setToolTip(hint)

    # ── 動作 ─────────────────────────────────────────────────────────

    def start(self, key: str) -> None:
        from . import hiraeth_tools as HT
        if key == 'clone':
            parent = self.parent()
            if parent is not None and hasattr(parent, 'enter_game'):
                parent.enter_game()
            self.status.setText(tr('已啟動 %s') % tr('Nos-clone'))
        else:
            try:
                HT.launch_game(self.hiraeth_root())
            except HT.HiraethError as exc:
                QMessageBox.warning(self, tr('進入遊戲'), str(exc))
                return
            self.status.setText(tr('已啟動 %s') % tr('Hiraeth（PAN 歌曲管理版）'))
        QTimer.singleShot(2500, self.refresh)

    def install_from_zip(self) -> None:
        """選一份打包好的遊戲（ZIP／資料夾）裝起來，順便認出製譜器與曲庫。"""
        if install_game_interactive(self, self.parent(), self.found_package()):
            self.refresh()

    def stop(self, key: str) -> None:
        from . import hiraeth_tools as HT
        from .library_manager import stop_game as kill_clone
        name = tr('Nos-clone') if key == 'clone' else tr('Hiraeth（PAN 歌曲管理版）')
        if QMessageBox.question(
                self, tr('進入遊戲'),
                tr('強制停止 %s？\n\n沒存的成績會不見（一般情況請在遊戲裡正常離開）。') % name) \
                != QMessageBox.Yes:
            return
        ok = kill_clone(self.clone_exe()) if key == 'clone' else bool(HT.stop_game())
        self.status.setText(tr('已強制停止 %s') % name if ok else tr('停不掉，可能已經關了'))
        self.refresh()


def _choose_install_source(parent: QWidget, title: str, package: str, filter_text: str,
                            found_text: str) -> str:
    """ZIP 來源：旁邊找到的那包先問要不要用，否則讓使用者選。"""
    if package:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(found_text % package)
        use = box.addButton(tr('裝這一包'), QMessageBox.AcceptRole)
        other = box.addButton(tr('選別的…'), QMessageBox.ActionRole)
        box.addButton(tr('取消'), QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is use:
            return package
        if clicked is not other:
            return ''
    path, _ = QFileDialog.getOpenFileName(parent, title, L.app_dir() or '', filter_text)
    return path


def install_game_interactive(parent: QWidget, launcher, package: str = '') -> bool:
    """選一份打包好的遊戲（ZIP／資料夾）裝起來，順便認出製譜器與曲庫。"""
    from . import updates
    title = tr('安裝遊戲')
    source = _choose_install_source(parent, title, package, tr('遊戲包 (*.zip)'),
                                    tr('NosMania 旁邊找到一包遊戲：\n%s\n\n要裝這一包嗎？'))
    if not source:
        source = QFileDialog.getExistingDirectory(parent, title)
    if not source:
        return False
    base = L.app_dir() or os.path.dirname(os.path.abspath(source))
    name = os.path.splitext(os.path.basename(source))[0] or 'game'
    if os.path.isfile(source) and os.path.isdir(base):
        dest = os.path.join(base, name)
        if QMessageBox.question(parent, title, tr('裝到這裡？\n%s') % dest) != QMessageBox.Yes:
            dest = QFileDialog.getExistingDirectory(parent, tr('裝到哪個資料夾'), base)
            if not dest:
                return False
            dest = os.path.join(dest, name)
    else:
        dest = QFileDialog.getExistingDirectory(parent, tr('裝到哪個資料夾'), base)
        if not dest:
            return False
        dest = os.path.join(dest, name) if os.path.isfile(source) else dest
    try:
        if launcher is not None and hasattr(launcher, '_with_progress'):
            found = launcher._with_progress(
                title, lambda step: updates.install_game(source, dest, progress=step))
        else:
            found = updates.install_game(source, dest)
    except Exception as exc:                            # noqa: BLE001
        QMessageBox.warning(parent, title, tr('安裝失敗：%s') % exc)
        return False
    lines = [tr('裝好了：%s') % found['folder'], '']
    settings.set('game_exe_path', found['game'])
    if launcher is not None and hasattr(launcher, 'write_game_language'):
        launcher.write_game_language()
    lines.append(tr('・遊戲：%s') % os.path.basename(found['game']))
    if found['editor']:
        settings.set('editor_exe_path', found['editor'])
        lines.append(tr('・製譜器：%s') % os.path.basename(found['editor']))
    if found['library'] and not L.is_junction(found['library']):
        lines.append(tr('・這包裡附了曲庫：%d 首') % count_songs(found['library']))
        if launcher is not None and hasattr(launcher, 'set_library_root'):
            launcher.set_library_root(found['library'])
        else:
            settings.set('song_library_root', found['library'])
    elif L.default_root():
        lines.append(tr('・曲庫：進遊戲時會連到 %s') % L.default_root())
    else:
        lines.append(tr('・曲庫：還沒有，到「位置設定…」選一份或從 ZIP 安裝'))
    QMessageBox.information(parent, title, '\n'.join(lines))
    if launcher is not None and hasattr(launcher, 'refresh_info'):
        launcher.refresh_info()
    return True


def install_library_interactive(parent: QWidget, launcher, package: str = '') -> bool:
    """選一份曲庫 ZIP 解壓到使用者選的資料夾，記成現在的曲庫。"""
    from . import updates
    title = tr('安裝曲庫')
    source = _choose_install_source(parent, title, package, tr('曲庫包 (*.zip)'),
                                    tr('NosMania 旁邊找到一包曲庫：\n%s\n\n要裝這一包嗎？'))
    if not source:
        return False
    base = L.app_dir() or os.path.dirname(os.path.abspath(source))
    dest = QFileDialog.getExistingDirectory(
        parent, tr('曲庫要解壓到哪個資料夾（會在裡面放 UserSongs）'), base)
    if not dest:
        return False
    try:
        if launcher is not None and hasattr(launcher, '_with_progress'):
            root = launcher._with_progress(
                title, lambda step: updates.install_library(source, dest, progress=step))
        else:
            root = updates.install_library(source, dest)
    except Exception as exc:                            # noqa: BLE001
        QMessageBox.warning(parent, title, tr('安裝失敗：%s') % exc)
        return False
    if launcher is not None and hasattr(launcher, 'set_library_root'):
        launcher.set_library_root(root)
    else:
        settings.set('song_library_root', root)
    QMessageBox.information(parent, title, tr('裝好了：%s\n曲目 %d 首') % (root, count_songs(root)))
    return True


class LocationsDialog(QDialog):
    """遊戲、曲庫、製譜器各放各的：在這裡指定位置，或從 ZIP 安裝。"""

    def __init__(self, launcher=None):
        super().__init__(launcher)
        self.launcher = launcher
        self.setWindowTitle(tr('位置設定'))
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        note = QLabel(tr('三樣東西分開放，NosMania 記住位置去連結。'
                         '沒指定的話，會找 NosMania 所在的資料夾和它下一層。'))
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        layout.addWidget(note)
        self.rows = {}
        for key, name in (('game', tr('遊戲本體')), ('library', tr('曲庫')), ('editor', tr('製譜器'))):
            box = QGroupBox(name)
            col = QVBoxLayout(box)
            path = QLabel('')
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path.setWordWrap(True)
            col.addWidget(path)
            row = QHBoxLayout()
            row.addStretch(1)
            pick = QPushButton(tr('選擇…'))
            pick.clicked.connect(lambda _c=False, k=key: self.pick(k))
            row.addWidget(pick)
            install = None
            if key in ('game', 'library'):
                install = QPushButton(tr('從 ZIP 安裝…'))
                install.clicked.connect(lambda _c=False, k=key: self.install(k))
                row.addWidget(install)
            show = QPushButton(tr('打開資料夾'))
            show.clicked.connect(lambda _c=False, k=key: self.show_folder(k))
            row.addWidget(show)
            col.addLayout(row)
            layout.addWidget(box)
            self.rows[key] = {'path': path, 'install': install, 'show': show}
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    @staticmethod
    def current(key: str) -> str:
        if key == 'game':
            return L.configured_game_exe()
        if key == 'library':
            return L.default_root()
        return editor_exe()

    def refresh(self) -> None:
        packages = {'game': L.find_game_packages(), 'library': L.find_library_packages()}
        for key, row in self.rows.items():
            value = self.current(key)
            if key == 'library' and value:
                text = '%s　（%s）' % (value, tr('%d 首') % count_songs(value))
            else:
                text = value or tr('（未設定）')
            row['path'].setText(text)
            row['show'].setEnabled(bool(value))
            found = packages.get(key) or []
            if row['install'] is not None:
                row['install'].setText(tr('從旁邊的 ZIP 安裝：%s') % os.path.basename(found[0]) if found
                                       else tr('從 ZIP 安裝…'))

    def pick(self, key: str) -> None:
        value = self.current(key)
        start = os.path.dirname(value) if value else (L.app_dir() or '')
        if key == 'library':
            path = QFileDialog.getExistingDirectory(self, tr('選擇曲庫（UserSongs）資料夾'), value or start)
            if not path:
                return
            if not os.path.isfile(os.path.join(path, L.INDEX_FILE)) and \
                    os.path.isfile(os.path.join(path, 'UserSongs', L.INDEX_FILE)):
                path = os.path.join(path, 'UserSongs')
            if not os.path.isfile(os.path.join(path, L.INDEX_FILE)) and QMessageBox.question(
                    self, tr('位置設定'),
                    tr('這個資料夾裡沒有 library.json，看起來不是曲庫。\n還是要用它當曲庫嗎？')) != QMessageBox.Yes:
                return
            if self.launcher is not None:
                self.launcher.set_library_root(path)
            else:
                settings.set('song_library_root', path)
        else:
            label = tr('選擇遊戲執行檔') if key == 'game' else tr('選擇製譜器執行檔')
            path, _ = QFileDialog.getOpenFileName(self, label, start, tr('執行檔 (*.exe)'))
            if not path:
                return
            if key == 'game' and not os.path.isdir(path[:-4] + '_Data'):
                QMessageBox.warning(self, tr('位置設定'),
                                    tr('這不是遊戲 exe（旁邊要有同名的 _Data 資料夾）。'))
                return
            settings.set('game_exe_path' if key == 'game' else 'editor_exe_path', path)
            if self.launcher is not None:
                if key == 'game':
                    self.launcher.write_game_language()
                self.launcher.refresh_info()
        self.refresh()

    def install(self, key: str) -> None:
        if key == 'game':
            found = L.find_game_packages()
            install_game_interactive(self, self.launcher, found[0] if found else '')
        else:
            found = L.find_library_packages()
            install_library_interactive(self, self.launcher, found[0] if found else '')
        self.refresh()

    def show_folder(self, key: str) -> None:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        value = self.current(key)
        folder = value if key == 'library' else os.path.dirname(value)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class _Card(QFrame):
    clicked = pyqtSignal()

    def __init__(self, glyph: str, title: str, text: str, key: str, primary: bool = False):
        super().__init__()
        self.setObjectName('card')
        self.setProperty('primary', primary)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(210, 170)
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 20, 22, 18)
        box.setSpacing(6)
        head = QHBoxLayout()
        icon = QLabel(glyph)
        icon.setObjectName('glyph')
        head.addWidget(icon)
        head.addStretch(1)
        hint = QLabel(key)
        hint.setObjectName('key')
        head.addWidget(hint, 0, Qt.AlignTop)
        box.addLayout(head)
        box.addStretch(1)
        name = QLabel(title)
        name.setObjectName('cardTitle')
        box.addWidget(name)
        body = QLabel(text)
        body.setObjectName('cardText')
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        body.setMinimumHeight(body.fontMetrics().lineSpacing() * 2 + 4)
        box.addWidget(body)

    def mouseReleaseEvent(self, event) -> None:          # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__(None)
        self._manager = None
        self._hiraeth = None
        self._picker = None
        self._locations = None
        self._body: Optional[QWidget] = None
        self.setObjectName('home')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_STYLE)
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 450)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        for key, slot in (('1', self.pick_game), ('2', self.manage_library),
                          ('3', lambda: self.open_editor()), ('4', self.open_hiraeth)):
            QShortcut(QKeySequence(key), self, activated=slot)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(3000)
        self._status_timer.timeout.connect(self.refresh_info)
        self._build()

    def _build(self) -> None:
        """整個畫面照目前語言建一次；換語言就丟掉重建。"""
        if self._body is not None:
            self.layout().removeWidget(self._body)
            _dispose(self._body)
        body = QWidget()
        self._body = body
        self.layout().addWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(36, 30, 36, 22)
        root.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel()
        pix = QPixmap(icon_path())
        if not pix.isNull():
            logo.setPixmap(pix.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName('title')
        titles.addWidget(title)
        subtitle = QLabel(tr('遊戲 · 曲庫 · 製譜器'))
        subtitle.setObjectName('subtitle')
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        lang_box = QVBoxLayout()
        lang_box.setSpacing(3)
        # 看不懂目前語言的人也要找得到這個選單，所以中文介面後面加上 Language
        lang_label = QLabel('Language' if get_lang() == 'en' else tr('語言') + ' · Language')
        lang_label.setObjectName('subtitle')
        lang_box.addWidget(lang_label, 0, Qt.AlignRight)
        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName('lang')
        self.lang_combo.setCursor(Qt.PointingHandCursor)
        for code, name in LANGUAGES:
            self.lang_combo.addItem(name, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(get_lang())))
        self.lang_combo.activated.connect(
            lambda i: self.set_language(self.lang_combo.itemData(i)))
        lang_box.addWidget(self.lang_combo)
        header.addLayout(lang_box)
        root.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.game_card = _Card('▶', tr('進入遊戲'),
                               tr('選 Nos-clone 或 Hiraeth 開始玩，也可以在那裡強制停止。'),
                               '1', primary=True)
        self.library_card = _Card('♫', tr('管理曲庫'), tr('曲目資料、難度、分類、匯入匯出、刪除與復原。'), '2')
        self.editor_card = _Card('✎', tr('製作譜面'), tr('打開製譜器，編輯或新建譜面。'), '3')
        self.hiraeth_card = _Card('⇄', tr('Hiraeth 套件'),
                                  tr('把歌曲包匯入 PAN 歌曲管理版、看它的曲庫、管它的遊戲。'), '4')
        self.game_card.clicked.connect(self.pick_game)
        self.library_card.clicked.connect(self.manage_library)
        self.editor_card.clicked.connect(lambda: self.open_editor())
        self.hiraeth_card.clicked.connect(self.open_hiraeth)
        for card in (self.game_card, self.library_card, self.editor_card, self.hiraeth_card):
            cards.addWidget(card, 1)
        root.addLayout(cards, 1)

        info = QHBoxLayout()
        info.setSpacing(6)
        self.info = QLabel('')
        self.info.setObjectName('info')
        info.addWidget(self.info, 1)
        self.update_btn = QToolButton()
        self.update_btn.setObjectName('update')
        self.update_btn.setText(tr('更新 ▾'))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.update_btn)
        menu.addAction(tr('更新製譜器（選新的 exe／ZIP）…'), self.update_editor)
        menu.addAction(tr('更新遊戲（選新的 build 資料夾／ZIP）…'), self.update_game)
        menu.addAction(tr('更新曲庫（選曲庫資料夾／ZIP）…'), self.update_library)
        self.update_btn.setMenu(menu)
        self._update_menu = menu
        info.addWidget(self.update_btn)
        self.locations_link = self._link(tr('位置設定…'), self.open_locations)
        self.locations_link.setToolTip(tr('遊戲、曲庫、製譜器分開放：在這裡指定各自的位置，或從 ZIP 安裝'))
        info.addWidget(self.locations_link)
        root.addLayout(info)

        bottom = QHBoxLayout()
        self.status = QLabel('')
        self.status.setObjectName('status')
        bottom.addWidget(self.status, 1)
        self.stop_link = self._link(tr('強制停止遊戲'), self.stop_game)
        self.stop_link.setVisible(False)
        bottom.addWidget(self.stop_link)
        root.addLayout(bottom)
        if self.isVisible():
            self.refresh_info()

    # ── 語言 ─────────────────────────────────────────────────────────

    def set_language(self, lang: str) -> None:
        """換語言：啟動器、曲庫管理當場換；開著的製譜器叫它換；遊戲寫設定檔讓它讀。"""
        if lang not in dict(LANGUAGES):
            return
        settings.set('language', lang)
        changed = get_lang() != lang
        set_lang(lang)
        self.write_game_language()
        editor_ipc.send({'lang': lang})
        if not changed:
            return
        manager = self._manager
        if manager is not None:
            visible = manager.isVisible()
            folder = manager._current
            root = manager.lib.root if manager.lib else ''
            geometry = manager.saveGeometry()
            manager.close()
            _dispose(manager)
            self._manager = None
            if visible:
                manager = self.library_manager()
                manager.restoreGeometry(geometry)
                if root:
                    manager.set_root(root)
                if folder:
                    manager._current = folder
                    manager._select_folder(folder)
                manager.show()
        QTimer.singleShot(0, self._build)

    def write_game_language(self) -> None:
        """遊戲資料夾裡寫 nosmania_launcher.json（語言＋曲庫位置），順便把曲庫連好。

        遊戲、曲庫一知道位置就先連，不等到按「進入遊戲」：有人第一次會直接點遊戲 exe，
        沒連結的話遊戲自己建一個空曲庫（舊版還會塞曲子進去）。
        """
        from .library_manager import configured_game_exe
        exe = configured_game_exe()
        if not exe:
            return
        root = L.default_root()
        write_game_language(os.path.dirname(exe), get_lang(), root)
        if root:
            try:
                L.ensure_game_library_link(os.path.dirname(exe), root)
            except OSError:
                pass            # 進遊戲時會再試一次，失敗會跳訊息

    def _link(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName('link')
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _c=False: slot())
        return btn

    # ── 顯示 ─────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:                  # noqa: N802
        self.refresh_info()
        self._status_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:                  # noqa: N802
        self._status_timer.stop()
        super().hideEvent(event)

    def library_manager(self):
        from .library_manager import LibraryManagerWindow
        if self._manager is None:
            # 不掛在首頁底下：首頁的樣式表不會套到曲庫管理，工作列上也是獨立視窗
            self._manager = LibraryManagerWindow(None, open_chart=self.open_editor)
            self._manager.root_changed_cb = self._library_changed_in_manager
        return self._manager

    def refresh_info(self) -> None:
        from .library_manager import configured_game_exe, game_running
        exe = configured_game_exe()
        root = L.default_root()
        editor = editor_exe()
        saved = settings.get('song_library_root', '') or ''
        if root:
            library_text = tr('%d 首') % count_songs(root)
        elif saved:
            library_text = tr('資料夾不見了，按「位置設定…」重選')
        else:
            library_text = tr('未選擇')
        parts = [tr('遊戲：%s') % (os.path.basename(os.path.dirname(exe)) if exe else tr('未設定')),
                 tr('曲庫：%s') % library_text,
                 tr('製譜器：%s') % (os.path.splitext(os.path.basename(editor))[0] if editor
                               else (tr('開發版') if editor_command() else tr('未設定')))]
        self.info.setText('　·　'.join(parts))
        self.info.setToolTip('\n'.join(p for p in (exe, root, editor) if p))
        from . import hiraeth_tools as HT
        names = []
        if exe and game_running(exe):
            names.append(tr('Nos-clone'))
        if HT.game_running():
            names.append(tr('Hiraeth（PAN 歌曲管理版）'))
        self.status.setText(tr('● 執行中：%s') % '、'.join(names) if names else '')
        self.stop_link.setVisible(bool(names))

    # ── 三個入口 ─────────────────────────────────────────────────────

    def enter_game(self) -> None:
        manager = self.library_manager()
        # 每次從啟動器進遊戲都帶上啟動器的語言（在遊戲裡改的語言只在直接開遊戲時保留）
        self.write_game_language()
        manager.launch_game()
        self.status.setText(manager.status.text())
        QTimer.singleShot(2500, self.refresh_info)

    def pick_game(self) -> None:
        """第一格：兩個遊戲（Nos-clone／Hiraeth）各自啟動與強制停止。"""
        picker = getattr(self, '_picker', None)
        if picker is None:
            picker = GamePicker(self)
            self._picker = picker
        picker.refresh()
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def stop_game(self) -> None:
        """首頁那個「強制停止遊戲」：交給遊戲選單，那裡兩個遊戲都管得到。"""
        self.pick_game()

    def open_hiraeth(self) -> None:
        """第四格：Hiraeth（PAN 歌曲管理版）套件。"""
        from .hiraeth_panel import HiraethPanel
        panel = getattr(self, '_hiraeth', None)
        if panel is None:
            panel = HiraethPanel(None)
            self._hiraeth = panel
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def manage_library(self) -> None:
        manager = self.library_manager()
        manager.show()
        manager.raise_()
        manager.activateWindow()

    def open_editor(self, path: Optional[str] = None) -> bool:
        """開製譜器（有 path 就開那份譜）。開著的就交給它，不另開一個。"""
        message = {'open': path} if path else {'show': True}
        message['lang'] = get_lang()
        if L.default_root():
            message['library'] = L.default_root()
        if editor_ipc.send(message):
            self.status.setText(tr('已交給開著的製譜器'))
            return True
        command = editor_command(path)
        if not command:
            QMessageBox.information(
                self, APP_NAME, tr('找不到製譜器。按首頁的「位置設定…」選製譜器 exe'
                '（或把 Nos Chart Maker 放在 NosMania 同一層）。'))
            return False
        try:
            subprocess.Popen(command, cwd=_editor_cwd(command))
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, tr('開啟製譜器失敗：%s') % exc)
            return False
        self.status.setText(tr('正在開啟製譜器…'))
        return True

    # ── 更新 ─────────────────────────────────────────────────────────

    def _pick_update_source(self, title: str) -> str:
        """更新用的來源：一個 ZIP／exe，或一個資料夾。"""
        from PyQt5.QtWidgets import QMessageBox as _Box
        box = _Box(self)
        box.setWindowTitle(title)
        box.setText(tr('新版在哪裡？'))
        as_file = box.addButton(tr('選檔案（ZIP／exe）'), _Box.AcceptRole)
        as_dir = box.addButton(tr('選資料夾'), _Box.ActionRole)
        box.addButton(tr('取消'), _Box.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is as_file:
            path, _ = QFileDialog.getOpenFileName(self, title, '', tr('更新檔 (*.zip *.exe)'))
            return path
        if clicked is as_dir:
            return QFileDialog.getExistingDirectory(self, title)
        return ''

    def update_editor(self) -> None:
        from . import updates
        title = tr('更新製譜器')
        source = self._pick_update_source(title)
        if not source:
            return
        current = editor_exe()
        target = os.path.dirname(current) if current else L.app_dir()
        if not target:
            target = QFileDialog.getExistingDirectory(self, tr('製譜器要放在哪個資料夾'))
            if not target:
                return
        try:
            dest = updates.update_editor(source, current, target)
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, title, tr('更新失敗：%s') % exc)
            return
        settings.set('editor_exe_path', dest)
        self.refresh_info()
        QMessageBox.information(self, title, tr('已更新製譜器：\n%s\n\n舊版改名保留在同一個資料夾。')
                                % dest)

    def update_game(self) -> None:
        from .library_manager import game_running
        from . import updates
        title = tr('更新遊戲')
        exe = self.library_manager().game_exe()
        game_dir = os.path.dirname(exe) if exe else ''
        if not game_dir:
            QMessageBox.information(self, title, tr('先在「位置設定…」指定現在的遊戲。'))
            return
        if exe and game_running(exe):
            QMessageBox.information(self, title, tr('遊戲正開著，請先關掉再更新。'))
            return
        source = self._pick_update_source(title)
        if not source:
            return
        try:
            copied, kept = self._with_progress(
                title, lambda step: updates.update_game(source, game_dir, progress=step))
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, title, tr('更新失敗：%s') % exc)
            return
        self.refresh_info()
        QMessageBox.information(self, title, tr('已更新遊戲：換了 %d 個檔案。\n保留：%s')
                                % (copied, '、'.join(kept) or tr('（無）')))

    def update_library(self) -> None:
        from . import updates
        title = tr('更新曲庫')
        manager = self.library_manager()
        if manager.lib is None:
            QMessageBox.information(self, title, tr('先在曲庫管理選一份曲庫。'))
            return
        source = self._pick_update_source(title)
        if not source:
            return
        try:
            done, failed = self._with_progress(
                title, lambda step: updates.update_library(source, manager.lib, progress=step))
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, title, tr('更新失敗：%s') % exc)
            return
        manager.reload()
        self.refresh_info()
        text = tr('更新了 %d 首（同名的整首換掉，其他保留）。') % len(done)
        if failed:
            text += tr('\n\n失敗：\n') + '\n'.join(failed)
        QMessageBox.information(self, title, text)

    def _with_progress(self, title: str, work):
        """跑一件會花時間的更新，附進度視窗（可取消）。"""
        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog(tr('準備中…'), tr('取消'), 0, 1, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        def step(done: int, total: int, name: str):
            progress.setMaximum(max(1, total))
            progress.setValue(done)
            if name:
                progress.setLabelText('(%d/%d) %s' % (done + 1, total, name))
            return not progress.wasCanceled()

        try:
            return work(step)
        finally:
            progress.close()

    def open_locations(self) -> None:
        dialog = getattr(self, '_locations', None)
        if dialog is None:
            dialog = LocationsDialog(self)
            self._locations = dialog
        dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _library_changed_in_manager(self, root: str) -> None:
        """曲庫管理裡換了曲庫（它自己已經記起來了）：製譜器、遊戲連結、首頁跟著換。"""
        editor_ipc.send({'library': root})
        self.write_game_language()
        self.refresh_info()

    def set_library_root(self, root: str) -> None:
        """換曲庫：記起來、曲庫管理跟著換、開著的製譜器也告訴它、遊戲的設定檔寫進去。"""
        settings.set('song_library_root', root)
        if self._manager is not None:
            self._manager.set_root(root)
        editor_ipc.send({'library': root})
        self.write_game_language()
        self.refresh_info()

    def closeEvent(self, event) -> None:                 # noqa: N802
        if self._manager is not None:
            self._manager.close()
        for window in (getattr(self, '_hiraeth', None), getattr(self, '_picker', None),
                       getattr(self, '_locations', None)):
            if window is not None:
                window.close()
        super().closeEvent(event)


def main() -> None:
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import QApplication
    settings.load()
    set_lang(settings.get('language', 'zh_tw'))
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    from .theme import apply_theme
    apply_theme(app, bool(settings.get('dark_mode', False)))
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(icon_path()))
    window = LauncherWindow()
    window.show()
    sys.exit(app.exec_())
