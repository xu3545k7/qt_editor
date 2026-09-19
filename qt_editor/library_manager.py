"""曲庫管理（遊戲啟動器）：管理 nos-clone 的 UserSongs、啟動遊戲，改了遊戲馬上重整。

資料層在 `song_library`；這裡只有畫面。
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, List, Optional

from PyQt5.QtCore import QFileSystemWatcher, Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
                             QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter,
                             QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import song_library as L
from .settings import settings
from .ui_text import tr

ROLE_FOLDER = Qt.UserRole


# ── 遊戲 exe ─────────────────────────────────────────────────────────

bundled_game_exe = L.bundled_game_exe


def configured_game_exe() -> str:
    """偏好設定指定的遊戲優先；否則啟動器旁邊（同一層或下一層）找到的。"""
    return L.configured_game_exe()


def game_running(exe: str) -> bool:
    name = os.path.basename(exe)
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % name, '/NH'],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    except Exception:                                   # noqa: BLE001
        return False
    return name.lower() in out.lower()


def stop_game(exe: str) -> bool:
    """強制結束遊戲（連子行程一起）。回傳有沒有真的結束掉。"""
    name = os.path.basename(exe)
    if not name:
        return False
    try:
        done = subprocess.run(['taskkill', '/F', '/T', '/IM', name],
                              capture_output=True, text=True, timeout=10,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:                                   # noqa: BLE001
        return False
    return done.returncode == 0


def link_library(target: str, link: str) -> None:
    """在遊戲旁邊建一個目錄連結（junction）指向曲庫：不複製檔案、不需要系統管理員。"""
    L.make_junction(target, link)


# ── 視窗 ─────────────────────────────────────────────────────────────

class LibraryManagerWindow(QDialog):
    def __init__(self, parent=None, open_chart: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.setWindowTitle(tr('NosMania · 曲庫管理'))
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.resize(1180, 720)
        self._open_chart_cb = open_chart
        #: 換了曲庫要通知誰（啟動器：告訴製譜器、把遊戲連過去）
        self.root_changed_cb: Optional[Callable[[str], None]] = None
        self.lib: Optional[L.SongLibrary] = None
        self._songs: List[L.Song] = []
        self._current: Optional[str] = None
        self._own_revision = None

        self._watcher = QFileSystemWatcher(self)
        # 接綁定方法、不要接 lambda：視窗刪掉時 PyQt 會自動斷開綁定方法，lambda
        # 不會 —— 之後資料夾一有變動，監看器就去呼叫已經刪掉的視窗，整個程式崩潰
        # （實際發生過：曲庫管理關掉後，別處動到那份曲庫的資料夾）。
        self._watcher.fileChanged.connect(self._schedule_reload)
        self._watcher.directoryChanged.connect(self._schedule_reload)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(600)
        self._reload_timer.timeout.connect(self._reload_from_disk)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_top())
        layout.addLayout(self._build_filters())
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_song_table())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.status = QLabel('')
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)

        self._fill_roots()
        self._refresh_game_label()

    # ── 版面 ─────────────────────────────────────────────────────────

    def _build_top(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr('曲庫')))
        self.root_combo = QComboBox()
        self.root_combo.setMinimumWidth(360)
        self.root_combo.activated.connect(self._on_root_chosen)
        row.addWidget(self.root_combo, 1)
        for text, slot in ((tr('開啟資料夾'), self._open_root_folder), (tr('重新整理'), self.reload),
                           (tr('復原上一步'), self._undo)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            row.addWidget(btn)
        row.addSpacing(16)
        self.game_label = QLabel('')
        self.game_label.setStyleSheet('color: gray;')
        row.addWidget(self.game_label)
        pick = QPushButton(tr('遊戲位置…'))
        pick.clicked.connect(lambda _c=False: self._pick_game())
        row.addWidget(pick)
        self.launch_btn = QPushButton(tr('▶ 啟動遊戲'))
        self.launch_btn.setStyleSheet('QPushButton { font-weight: bold; padding: 6px 18px; }')
        self.launch_btn.clicked.connect(lambda _c=False: self.launch_game())
        row.addWidget(self.launch_btn)
        return row

    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('搜尋曲名、作者、資料夾…'))
        self.search.textChanged.connect(lambda _t: self._fill_table())
        row.addWidget(self.search, 2)
        self.category_filter = QComboBox()
        self.category_filter.currentIndexChanged.connect(lambda _i: self._fill_table())
        row.addWidget(self.category_filter, 1)
        self.problem_only = QCheckBox(tr('只看有問題的'))
        self.problem_only.toggled.connect(lambda _on: self._fill_table())
        row.addWidget(self.problem_only)
        imp = QToolButton()
        imp.setText(tr('匯入 ▾'))
        imp.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(imp)
        menu.addAction(tr('Hiraeth 歌曲包（ZIP）…'), self._import_zip)
        menu.addAction(tr('樂曲資料夾…'), self._import_folder)
        menu.addAction(tr('批量匯入（掃描整個資料夾）…'), self._batch_import)
        menu.addSeparator()
        menu.addAction(tr('新增分類…'), self._add_category)
        imp.setMenu(menu)
        self._import_menu = menu
        row.addWidget(imp)

        # 整個曲庫層級的工具。它們動的不是「選到的那一首」，所以不和右邊那幾顆
        # 針對單曲的按鈕混在一起 —— 收進自己的下拉，按錯的代價差很多。
        tools = QToolButton()
        tools.setText(tr('曲庫工具 ▾'))
        tools.setPopupMode(QToolButton.InstantPopup)
        tools_menu = QMenu(tools)
        tools_menu.addAction(tr('掃描曲庫，補齊缺少的難度…'), self._fill_difficulties)
        tools_menu.addAction(tr('修剪結尾空白…'), self._trim_tails)
        tools_menu.addSeparator()
        tools_menu.addAction(tr('把整個曲庫備份成 ZIP…'), self._backup_zip)
        tools_menu.addAction(tr('從曲庫備份 ZIP 匯入…'), self._restore_zip)
        tools_menu.addSeparator()
        tools_menu.addAction(tr('匯出乾淨版遊戲（只留新手教學）…'), self._export_clean_game)
        tools.setMenu(tools_menu)
        self._tools_menu = tools_menu
        row.addWidget(tools)

        for text, slot in ((tr('刪除曲目'), self._delete_song), (tr('輸出 Hiraeth ZIP'), self._export_zip),
                           (tr('深入檢查全部'), self._deep_check_all)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            row.addWidget(btn)
        return row

    def _build_song_table(self) -> QWidget:
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr('曲名'), tr('作者'), tr('分類'), tr('難度'), tr('狀態')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_song_selected)
        return self.table

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(6, 0, 0, 0)

        top = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(150, 150)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet('background: #222; color: #888;')
        top.addWidget(self.cover)
        form_box = QGroupBox(tr('曲目資料'))
        form = QFormLayout(form_box)
        self.title_edit = QLineEdit()
        self.author_edit = QLineEdit()
        self.category_edit = QComboBox()
        self.category_edit.setEditable(True)
        self.slogan_edit = QLineEdit()
        self.slogan_edit.setPlaceholderText(tr('選曲畫面的一句介紹（輸出 Hiraeth ZIP 也會用）'))
        form.addRow(tr('曲名'), self.title_edit)
        form.addRow(tr('作者'), self.author_edit)
        form.addRow(tr('分類'), self.category_edit)
        form.addRow(tr('樂曲評論'), self.slogan_edit)
        save = QPushButton(tr('儲存曲目資料'))
        save.clicked.connect(lambda _c=False: self._save_song())
        form.addRow('', save)
        top.addWidget(form_box, 1)
        box.addLayout(top)

        diff_box = QGroupBox(tr('難度（直接改名稱、等級；雙擊譜面欄在製譜器打開）'))
        diff_layout = QVBoxLayout(diff_box)
        self.diff_table = QTableWidget(0, 4)
        self.diff_table.setHorizontalHeaderLabels([tr('難度'), tr('等級'), tr('無背景音樂'), tr('譜面')])
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.diff_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.diff_table.itemDoubleClicked.connect(self._on_diff_double_clicked)
        diff_layout.addWidget(self.diff_table)
        buttons = QHBoxLayout()
        for text, slot in ((tr('在製譜器開啟'), self._open_selected_chart), (tr('上移'), lambda: self._move_diff(-1)),
                           (tr('下移'), lambda: self._move_diff(1)), (tr('刪除難度'), self._delete_diff),
                           (tr('儲存難度變更'), self._save_diffs)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            buttons.addWidget(btn)
        diff_layout.addLayout(buttons)
        box.addWidget(diff_box, 1)

        self.problems = QLabel('')
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet('color: #c0392b;')
        box.addWidget(self.problems)
        check = QPushButton(tr('深入檢查這首（打開譜面看鍵道、音高）'))
        check.clicked.connect(lambda _c=False: self._deep_check_current())
        box.addWidget(check)
        return panel

    # ── 曲庫位置 ─────────────────────────────────────────────────────

    def _fill_roots(self) -> None:
        self.root_combo.blockSignals(True)
        self.root_combo.clear()
        bundled = L.bundled_root()
        if bundled:
            self.root_combo.addItem(tr('遊戲資料夾裡的曲庫　%s') % bundled, bundled)
        saved = settings.get('song_library_root', '') or ''
        if saved and os.path.isdir(saved) and self.root_combo.findData(saved) < 0:
            self.root_combo.addItem(tr('上次用的曲庫　%s') % saved, saved)
        self.root_combo.addItem(tr('選擇曲庫資料夾…'), '')
        self.root_combo.blockSignals(False)
        root = L.default_root()
        if root:
            self.set_root(root)
        else:
            self._say(tr('還沒選曲庫：按上面的下拉「選擇曲庫資料夾…」選 UserSongs'))

    def _on_root_chosen(self, index: int) -> None:
        path = self.root_combo.itemData(index)
        if not path:
            path = QFileDialog.getExistingDirectory(self, tr('選擇曲庫（UserSongs）資料夾'))
            if not path:
                return
        self.set_root(path)

    def set_root(self, path: str) -> None:
        try:
            self.lib = L.SongLibrary(path)
        except L.LibraryError as exc:
            QMessageBox.warning(self, tr('曲庫管理'), str(exc))
            return
        previous = settings.get('song_library_root', '') or ''
        settings.set('song_library_root', self.lib.root)
        changed = os.path.normcase(os.path.abspath(previous)) != os.path.normcase(self.lib.root) \
            if previous else True
        if changed and self.root_changed_cb is not None:
            self.root_changed_cb(self.lib.root)
        if self.root_combo.findData(self.lib.root) < 0:
            self.root_combo.insertItem(0, tr('曲庫　%s') % self.lib.root, self.lib.root)
        self.root_combo.blockSignals(True)
        self.root_combo.setCurrentIndex(self.root_combo.findData(self.lib.root))
        self.root_combo.blockSignals(False)
        self._watch()
        self.reload()

    def _require_lib(self) -> bool:
        """動作需要曲庫但還沒有（或原本那份資料夾不見了）：直接問要用哪一份，不要按了沒反應。"""
        if self.lib:
            return True
        saved = settings.get('song_library_root', '') or ''
        box = QMessageBox(self)
        box.setWindowTitle(tr('曲庫管理'))
        if saved and not os.path.isdir(saved):
            box.setText(tr('原本的曲庫資料夾不見了：\n%s\n\n'
                           '（例如重新 build 遊戲時整個資料夾被換掉）。先選一份曲庫，或從曲庫 ZIP 安裝一份。') % saved)
        else:
            box.setText(tr('還沒有曲庫。先選一份曲庫資料夾，或從曲庫 ZIP 安裝一份。'))
        pick = box.addButton(tr('選擇曲庫資料夾…'), QMessageBox.AcceptRole)
        install = box.addButton(tr('從 ZIP 安裝曲庫…'), QMessageBox.ActionRole)
        box.addButton(tr('取消'), QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is pick:
            path = QFileDialog.getExistingDirectory(self, tr('選擇曲庫（UserSongs）資料夾'), L.app_dir() or '')
            if path:
                if not os.path.isfile(os.path.join(path, L.INDEX_FILE)) and \
                        os.path.isfile(os.path.join(path, 'UserSongs', L.INDEX_FILE)):
                    path = os.path.join(path, 'UserSongs')
                self.set_root(path)
        elif clicked is install:
            self._install_library_zip()
        return self.lib is not None

    def _install_library_zip(self) -> None:
        from PyQt5.QtWidgets import QProgressDialog
        from . import updates
        title = tr('安裝曲庫')
        found = L.find_library_packages()
        source, _ = QFileDialog.getOpenFileName(self, title, found[0] if found else (L.app_dir() or ''),
                                                tr('曲庫包 (*.zip)'))
        if not source:
            return
        dest = QFileDialog.getExistingDirectory(
            self, tr('曲庫要解壓到哪個資料夾（會在裡面放 UserSongs）'), L.app_dir() or os.path.dirname(source))
        if not dest:
            return
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
            root = updates.install_library(source, dest, progress=step)
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, title, tr('安裝失敗：%s') % exc)
            return
        finally:
            progress.close()
        self.set_root(root)

    def _open_root_folder(self) -> None:
        if self.lib:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.lib.root))

    # ── 讀取與顯示 ───────────────────────────────────────────────────

    def _schedule_reload(self, _path: str = '') -> None:
        self._reload_timer.start()

    def _watch(self) -> None:
        """監看曲庫根目錄與兩個索引檔：遊戲或別的製譜器改了就重新讀。"""
        self._stop_watching()
        if not self.lib:
            return
        self._watcher.addPath(self.lib.root)
        for name in (L.INDEX_FILE, L.SONGLIST_FILE):
            full = os.path.join(self.lib.root, name)
            if os.path.exists(full):
                self._watcher.addPath(full)

    def _stop_watching(self) -> None:
        for paths in (self._watcher.files(), self._watcher.directories()):
            if paths:
                self._watcher.removePaths(paths)
        self._reload_timer.stop()

    def closeEvent(self, event) -> None:                 # noqa: N802
        # 關著的視窗不需要跟著曲庫資料夾的變動重新讀取
        self._stop_watching()
        super().closeEvent(event)

    def showEvent(self, event) -> None:                  # noqa: N802
        if self.lib and not self._watcher.directories():
            self._watch()
        super().showEvent(event)

    def _reload_from_disk(self) -> None:
        """別的程式（遊戲、其他製譜器）改了曲庫：重新讀，但不打斷正在打字的欄位。"""
        if self.title_edit.hasFocus() or self.author_edit.hasFocus() or self.slogan_edit.hasFocus():
            self._reload_timer.start()
            return
        self.reload(keep_edits=True)

    def reload(self, keep_edits: bool = False) -> None:
        """重讀曲庫。

        `keep_edits` 是給「別的程式改了曲庫」那條路用的：使用者可能正在右邊改
        曲名，重整不該把他打到一半的字換掉。這個參數以前收了卻沒有人看，於是
        每一次自動重整都會靜靜地還原他的編輯。
        """
        if not self.lib:
            return
        typed = None
        if keep_edits and self._current:
            typed = (self.title_edit.text(), self.author_edit.text(),
                     self.category_edit.currentText(), self.slogan_edit.text())
        self._songs = self.lib.songs()
        current_filter = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem(tr('全部分類'))
        for name in self.lib.categories():
            self.category_filter.addItem(name)
        index = self.category_filter.findText(current_filter)
        self.category_filter.setCurrentIndex(max(0, index))
        self.category_filter.blockSignals(False)
        self._fill_table()
        if typed is not None and self._current:
            self.title_edit.setText(typed[0])
            self.author_edit.setText(typed[1])
            self.category_edit.setCurrentText(typed[2])
            self.slogan_edit.setText(typed[3])
        problems = sum(1 for s in self._songs if s.problems)
        self._say(tr('%d 首歌%s') % (len(self._songs), tr('，%d 首有問題') % problems if problems else ''))

    def _fill_table(self) -> None:
        text = self.search.text().strip().lower()
        category = self.category_filter.currentText()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for song in self._songs:
            if text and text not in ' '.join((song.title, song.author, song.folder)).lower():
                continue
            # 第 0 項是「全部分類」（會跟著語言變，所以看位置不看字）
            if self.category_filter.currentIndex() > 0 and category not in song.categories:
                continue
            if self.problem_only.isChecked() and not song.problems:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (song.title, song.author, ' / '.join(song.categories), song.levels_text(),
                      '⚠ %d' % len(song.problems) if song.problems else '')
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(ROLE_FOLDER, song.folder)
                if song.problems:
                    item.setToolTip('\n'.join(song.problems))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self._select_folder(self._current)

    def _select_folder(self, folder: Optional[str]) -> None:
        if not folder:
            return
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(ROLE_FOLDER) == folder:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def _song(self, folder: Optional[str]) -> Optional[L.Song]:
        return next((s for s in self._songs if s.folder == folder), None)

    def _on_song_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            # 選擇被濾掉了（搜尋、只看有問題的、重新整理）就把「目前這首」清掉。
            # 留著的話，畫面上沒有任何一列反白，右邊卻還顯示著上一首 —— 這時按
            # 「刪除曲目」刪掉的是那一首看不見的歌，而確認框上寫著它的名字，看
            # 起來完全合理。
            self._current = None
            self._clear_detail()
            return
        folder = self.table.item(rows[0].row(), 0).data(ROLE_FOLDER)
        self._current = folder
        self._show_song(self._song(folder))

    def _clear_detail(self) -> None:
        """右邊的欄位清空。沒有選歌的時候那裡不該留著上一首的資料。"""
        for edit in (self.title_edit, self.author_edit, self.slogan_edit):
            edit.clear()
        self.category_edit.setCurrentText('')
        self.diff_table.setRowCount(0)
        self.cover.setPixmap(QPixmap())
        self.cover.setText(tr('沒有曲繪'))
        self.problems.setText('')

    def _show_song(self, song: Optional[L.Song]) -> None:
        if song is None:
            return
        self.title_edit.setText(song.title)
        self.author_edit.setText(song.author)
        self.category_edit.clear()
        self.category_edit.addItems(self.lib.categories())
        self.category_edit.setCurrentText(song.category)
        self.slogan_edit.setText(song.slogan)
        if song.cover:
            pix = QPixmap(song.cover)
            self.cover.setPixmap(pix.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText(tr('沒有曲繪'))
        self.diff_table.setRowCount(0)
        for d in song.difficulties:
            row = self.diff_table.rowCount()
            self.diff_table.insertRow(row)
            name = QTableWidgetItem(d.name)
            level = QTableWidgetItem('' if d.level is None else str(d.level))
            nobgm = QTableWidgetItem()
            nobgm.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            nobgm.setCheckState(Qt.Checked if d.no_background else Qt.Unchecked)
            chart = QTableWidgetItem(os.path.relpath(d.chart, song.path) if d.chart else tr('（找不到）'))
            chart.setFlags(chart.flags() & ~Qt.ItemIsEditable)
            chart.setData(ROLE_FOLDER, d.chart or '')
            for col, item in enumerate((name, level, nobgm, chart)):
                self.diff_table.setItem(row, col, item)
        self.problems.setText('\n'.join('⚠ ' + p for p in song.problems))

    def _say(self, text: str) -> None:
        self.status.setText(text)

    # ── 動作 ─────────────────────────────────────────────────────────

    def _run(self, action, success: str = '') -> bool:
        try:
            action()
        except L.LibraryError as exc:
            QMessageBox.warning(self, tr('曲庫管理'), str(exc))
            return False
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.critical(self, tr('曲庫管理'), tr('發生錯誤：%s') % exc)
            return False
        self.reload()
        if success:
            self._say(success + tr('　（已通知遊戲重整）'))
        return True

    def _save_song(self) -> None:
        song = self._song(self._current)
        if not song or not self.lib:
            return
        category = self.category_edit.currentText().strip() or L.DEFAULT_CATEGORY
        self._run(lambda: self.lib.update_song(song.folder, self.title_edit.text(),
                                               self.author_edit.text(), [category],
                                               self.slogan_edit.text()),
                  tr('已儲存「%s」') % self.title_edit.text())

    def _save_diffs(self) -> None:
        song = self._song(self._current)
        if not song or not self.lib:
            return

        def action():
            for row, d in enumerate(song.difficulties):
                name = self.diff_table.item(row, 0).text()
                level = self.diff_table.item(row, 1).text()
                nobgm = self.diff_table.item(row, 2).checkState() == Qt.Checked
                if (name, str(level), nobgm) != (d.name, str(d.level), d.no_background):
                    self.lib.update_difficulty(song.folder, d.index, name, level, nobgm)
        self._run(action, tr('已儲存「%s」的難度') % song.title)

    def _selected_diff_row(self) -> int:
        rows = self.diff_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _move_diff(self, step: int) -> None:
        song = self._song(self._current)
        row = self._selected_diff_row()
        if song and row >= 0 and self.lib:
            target = row + step
            if self._run(lambda: self.lib.move_difficulty(song.folder, row, step)):
                self.diff_table.selectRow(max(0, min(target, self.diff_table.rowCount() - 1)))

    def _delete_diff(self) -> None:
        song = self._song(self._current)
        row = self._selected_diff_row()
        # row 是從表格來的，而表格不一定和剛從磁碟讀回來的 song.difficulties
        # 同步（選擇被濾掉時 _show_song 不會重建它）。沒有這道檢查就是 IndexError
        # 從按鈕的 slot 穿出去 —— 啟動器沒有裝 excepthook，那會直接讓整支程式收掉。
        if not song or row < 0 or row >= len(song.difficulties) or not self.lib:
            return
        name = song.difficulties[row].name
        if QMessageBox.question(self, tr('刪除難度'), tr('從「%s」拿掉難度 %s？\n（譜面檔會留著，可以按「復原上一步」）')
                                % (song.title, name)) != QMessageBox.Yes:
            return
        self._run(lambda: self.lib.delete_difficulty(song.folder, row), tr('已刪除難度 %s') % name)

    def _delete_song(self) -> None:
        song = self._song(self._current)
        if not song or not self.lib:
            return
        if QMessageBox.question(
                self, tr('刪除曲目'),
                tr('刪除「%s」？\n\n整首會移到回收區（%s），可以按「復原上一步」搬回來。')
                % (song.title, os.path.join(self.lib.editor_dir, 'trash'))) != QMessageBox.Yes:
            return
        self._current = None
        self._run(lambda: self.lib.delete_song(song.folder), tr('已刪除「%s」') % song.title)

    def _undo(self) -> None:
        if not self._require_lib():
            return
        entries = self.lib.history()
        if not entries:
            QMessageBox.information(self, tr('復原'), tr('沒有可以復原的動作。'))
            return
        if QMessageBox.question(self, tr('復原上一步'), tr('復原「%s」？') % entries[0][1]) != QMessageBox.Yes:
            return
        labels = []
        self._run(lambda: labels.append(self.lib.undo_last()))
        if labels:
            self._say(tr('已復原：%s　（已通知遊戲重整）') % labels[0])

    def _add_category(self) -> None:
        name, ok = QInputDialog.getText(self, tr('新增分類'), tr('分類名稱：'))
        if ok and self.lib:
            self._run(lambda: self.lib.add_category(name), tr('已新增分類「%s」') % name)

    def _ask_category(self) -> Optional[str]:
        items = self.lib.categories()
        name, ok = QInputDialog.getItem(self, tr('匯入'), tr('放進哪個分類？'), items, 0, True)
        return (name or L.DEFAULT_CATEGORY) if ok else None

    def _import_zip(self) -> None:
        if not self._require_lib():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, tr('選擇 Hiraeth 歌曲包'), '', 'ZIP (*.zip)')
        if not paths:
            return
        category = self._ask_category()
        if category is None:
            return
        done, failed = [], []
        for path in paths:
            try:
                done.append(self.lib.import_hiraeth_zip(path, category))
            except Exception as exc:                    # noqa: BLE001
                failed.append('%s：%s' % (os.path.basename(path), exc))
        self.reload()
        if done:
            self._current = done[-1]
            self._select_folder(self._current)
        text = tr('匯入 %d 首。') % len(done)
        if failed:
            text += tr('\n\n失敗：\n') + '\n'.join(failed)
        text += tr('\n\n注意：Hiraeth 歌曲包原本沒有按鍵音（力度是 0），聲音全在音樂檔裡。')
        QMessageBox.information(self, tr('匯入 Hiraeth 歌曲包'), text)

    def _import_folder(self) -> None:
        if not self._require_lib():
            return
        path = QFileDialog.getExistingDirectory(self, tr('選擇樂曲資料夾（有 register.json）'))
        if not path:
            return
        category = self._ask_category()
        if category is None:
            return
        folders = []
        if self._run(lambda: folders.append(self.lib.import_folder(path, category))) and folders:
            self._current = folders[0]
            self._select_folder(self._current)
            self._say(tr('已匯入「%s」　（已通知遊戲重整）') % folders[0])

    def _export_zip(self) -> None:
        song = self._song(self._current)
        if not song:
            return
        from .hiraeth_export_dialog import HiraethExportDialog
        from .hiraeth_export_dialog import run_export
        dlg = HiraethExportDialog(self, song.path, self.lib.root, None)
        if dlg.exec_() != HiraethExportDialog.Accepted:
            return
        plans, skipped = dlg.plans()
        run_export(self, plans, skipped, dlg.out_edit.text().strip(), dlg.hold_gap_ms())

    # ── 整個曲庫 ─────────────────────────────────────────────────────

    def _fill_difficulties(self) -> None:
        if not self._require_lib():
            return
        from . import library_tools
        library_tools.fill_difficulties(self, self.lib.root)
        self.reload()

    def _backup_zip(self) -> None:
        if not self._require_lib():
            return
        from . import library_tools
        library_tools.backup_zip(self, self.lib.root)

    def _restore_zip(self) -> None:
        if not self._require_lib():
            return
        category = self._ask_category()
        if category is None:
            return
        from . import library_tools
        done = library_tools.import_backup_zip(self, self.lib, category)
        self.reload()
        if done:
            self._current = done[-1]
            self._select_folder(self._current)

    def _export_clean_game(self) -> None:
        if not self._require_lib():
            return
        from . import library_tools
        library_tools.export_clean_game(self, self.lib, self.game_exe())

    def _trim_tails(self) -> None:
        """曲終拖在最後一顆音符後面好幾秒的曲目，掃出來一次修掉。"""
        if not self._require_lib():
            return
        from .tail_trim_dialog import TailTrimDialog
        dlg = TailTrimDialog(self, self.lib)
        dlg.exec_()
        self.reload()

    def _batch_import(self) -> None:
        """挑一個資料夾，把裡面所有樂曲資料夾與歌曲包一次列出來匯入。"""
        if not self._require_lib():
            return
        from .batch_import_dialog import BatchImportDialog, run_batch_import
        dlg = BatchImportDialog(self, self.lib)
        if dlg.exec_() != BatchImportDialog.Accepted:
            return
        done, failed = run_batch_import(self, self.lib, dlg.items())
        self.reload()
        if done:
            self._current = done[-1]
            self._select_folder(self._current)
        text = tr('匯入 %d 首。') % len(done)
        if failed:
            text += tr('\n\n失敗：\n') + '\n'.join(failed)
        QMessageBox.information(self, tr('批量匯入'), text)
        self._say(tr('批量匯入 %d 首\u3000（已通知遊戲重整）') % len(done))

    def _deep_check_current(self) -> None:
        song = self._song(self._current)
        if not song or not self.lib:
            return
        problems = self.lib.deep_check(song)
        self.problems.setText('\n'.join('⚠ ' + p for p in problems) or tr('✓ 沒有發現問題'))

    def _deep_check_all(self) -> None:
        if not self._require_lib():
            return
        from PyQt5.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for song in self._songs:
                song.problems = self.lib.deep_check(song)
        finally:
            QApplication.restoreOverrideCursor()
        self.problem_only.setChecked(True)
        self._fill_table()
        self._say(tr('深入檢查完成：%d 首有問題') % sum(1 for s in self._songs if s.problems))

    def _on_diff_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 3:
            self._open_chart(item.data(ROLE_FOLDER))

    def _open_selected_chart(self) -> None:
        row = self._selected_diff_row()
        if row >= 0:
            self._open_chart(self.diff_table.item(row, 3).data(ROLE_FOLDER))

    def _open_chart(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, tr('曲庫管理'), tr('找不到這個難度的譜面檔。'))
            return
        if self._open_chart_cb is None:
            QMessageBox.information(self, tr('曲庫管理'), tr('找不到製譜器。'))
            return
        self._open_chart_cb(path)
        self._say(tr('已在製譜器開啟 %s（存檔後遊戲選曲會自動重整）') % os.path.basename(path))

    # ── 遊戲 ─────────────────────────────────────────────────────────

    def game_exe(self) -> str:
        return configured_game_exe()

    def _refresh_game_label(self) -> None:
        exe = self.game_exe()
        self.game_label.setText(tr('遊戲：%s') % (os.path.basename(os.path.dirname(exe)) if exe else tr('（未設定）')))
        self.game_label.setToolTip(exe or tr('按「遊戲位置…」選打包好的遊戲 exe'))

    def _pick_game(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('選擇遊戲執行檔'), os.path.dirname(self.game_exe()),
                                              tr('遊戲 (*.exe)'))
        if path:
            settings.set('game_exe_path', path)
            self._refresh_game_label()

    def launch_game(self) -> None:
        exe = self.game_exe()
        if not exe:
            QMessageBox.information(
                self, tr('啟動遊戲'),
                tr('找不到遊戲。把 NosMania 放在遊戲 build 資料夾那一層（或裡面），'
                '或按「遊戲位置…」選 exe。\n\n'
                '在 Unity 裡測試的話直接按 Play，曲庫管理的改動一樣會即時重整。'))
            return
        if game_running(exe):
            self._say(tr('遊戲已經開著了（改動會自動重整）'))
            return
        game_library = os.path.join(os.path.dirname(exe), 'UserSongs')
        if self.lib is None and L.default_root():
            self.set_root(L.default_root())
        if self.lib is not None:
            # 曲庫和遊戲分開放：遊戲只讀 exe 旁邊的 UserSongs，放一個連結指過去
            try:
                how = L.ensure_game_library_link(os.path.dirname(exe), self.lib.root)
            except Exception as exc:                    # noqa: BLE001
                QMessageBox.warning(self, tr('啟動遊戲'), tr('連結曲庫失敗：%s') % exc)
                return
            if how == 'real_folder' and QMessageBox.question(
                    self, tr('啟動遊戲'),
                    tr('這個遊戲資料夾裡有它自己的一份曲庫：\n%s\n\n和目前管理的不是同一份，'
                       '遊戲會用它自己那份。要切換過去管理它嗎？')
                    % game_library) == QMessageBox.Yes:
                self.set_root(game_library)
        elif not os.path.isdir(game_library):
            QMessageBox.information(
                self, tr('啟動遊戲'),
                tr('還沒指定曲庫：先在首頁的「位置設定…」選曲庫資料夾，或從 ZIP 安裝一份。'))
            return
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except OSError as exc:
            QMessageBox.warning(self, tr('啟動遊戲'), tr('啟動失敗：%s') % exc)
            return
        self._say(tr('已啟動 %s') % os.path.basename(exe))
