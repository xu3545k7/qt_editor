"""批量匯入：挑一個資料夾，把裡面所有樂曲資料夾與 Hiraeth 歌曲包一次列出來匯入。

一列一首：勾選、名稱、來源種類、分類、狀態（曲庫已經有同名的會標出來）。分類可以
整批設定，也可以逐曲改。
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from . import song_library as L
from .ui_text import tr

KIND_FOLDER, KIND_ZIP = 'folder', 'zip'


def scan(folder: str) -> List[Tuple[str, str]]:
    """[(種類, 路徑)]：資料夾裡所有樂曲資料夾與 Hiraeth 歌曲包。"""
    items = [(KIND_FOLDER, path) for path in L.find_song_folders(folder)]
    items += [(KIND_ZIP, path) for path in L.find_packages(folder)]
    return items


class BatchImportDialog(QDialog):
    def __init__(self, parent, library: L.SongLibrary, folder: str = ''):
        super().__init__(parent)
        self.setWindowTitle(tr('批量匯入'))
        self.resize(820, 560)
        self.lib = library
        self._existing = {s.folder for s in library.songs()}

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr('來源資料夾')))
        self.folder_edit = QLineEdit(folder)
        self.folder_edit.setReadOnly(True)
        top.addWidget(self.folder_edit, 1)
        pick = QPushButton(tr('選擇…'))
        pick.clicked.connect(lambda _c=False: self._pick())
        top.addWidget(pick)
        layout.addLayout(top)

        tools = QHBoxLayout()
        for text, slot in ((tr('全選'), lambda: self._check_all(True)),
                           (tr('全不選'), lambda: self._check_all(False)),
                           (tr('只選新的'), self._check_new)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            tools.addWidget(btn)
        tools.addSpacing(16)
        tools.addWidget(QLabel(tr('全部設為')))
        self.bulk_category = QComboBox()
        self.bulk_category.setEditable(True)
        self.bulk_category.addItems(library.categories())
        tools.addWidget(self.bulk_category, 1)
        apply_btn = QPushButton(tr('套用到勾選的'))
        apply_btn.clicked.connect(lambda _c=False: self._apply_category())
        tools.addWidget(apply_btn)
        layout.addLayout(tools)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr('曲目'), tr('來源'), tr('分類'), tr('狀態')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.status = QLabel('')
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText(tr('匯入'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if folder:
            self.set_folder(folder)
        else:
            self._refresh_status()

    # ── 來源 ─────────────────────────────────────────────────────────

    def _pick(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr('選擇要掃描的資料夾'),
                                                  self.folder_edit.text())
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder: str) -> None:
        self.folder_edit.setText(folder)
        self.table.setRowCount(0)
        for kind, path in scan(folder):
            self._add_row(kind, path)
        self._refresh_status()

    def _add_row(self, kind: str, path: str) -> None:
        name = (os.path.basename(path) if kind == KIND_FOLDER
                else os.path.splitext(os.path.basename(path))[0])
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, (kind, path))
        item.setToolTip(path)
        self.table.setItem(row, 0, item)
        self.table.setItem(row, 1, QTableWidgetItem(
            tr('樂曲資料夾') if kind == KIND_FOLDER else tr('Hiraeth ZIP')))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.lib.categories())
        combo.setCurrentText(self.bulk_category.currentText() or L.DEFAULT_CATEGORY)
        self.table.setCellWidget(row, 2, combo)
        self.table.setItem(row, 3, QTableWidgetItem(
            tr('曲庫已有同名，會另存一份') if name in self._existing else ''))

    # ── 勾選 ─────────────────────────────────────────────────────────

    def _check_all(self, on: bool) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._refresh_status()

    def _check_new(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            known = bool(self.table.item(row, 3).text())
            item.setCheckState(Qt.Unchecked if known else Qt.Checked)
        self._refresh_status()

    def _apply_category(self) -> None:
        text = self.bulk_category.currentText().strip()
        if not text:
            return
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                self.table.cellWidget(row, 2).setCurrentText(text)

    def _refresh_status(self) -> None:
        total = self.table.rowCount()
        picked = len(self.items())
        self.status.setText(tr('掃到 %d 首，勾選 %d 首') % (total, picked))
        self._ok.setEnabled(picked > 0)

    def items(self) -> List[Tuple[str, str, str]]:
        """[(種類, 路徑, 分類)]，只有勾選的。"""
        out = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.checkState() != Qt.Checked:
                continue
            kind, path = item.data(Qt.UserRole)
            category = self.table.cellWidget(row, 2).currentText().strip() or L.DEFAULT_CATEGORY
            out.append((kind, path, category))
        return out

    def accept(self) -> None:                            # noqa: D401
        if not self.items():
            return
        super().accept()


def run_batch_import(parent, library: L.SongLibrary,
                     items: List[Tuple[str, str, str]]) -> Tuple[List[str], List[str]]:
    """跑批量匯入，附進度視窗（可取消）。回傳 (成功的資料夾, 失敗說明)。"""
    progress = QProgressDialog(tr('準備中…'), tr('取消'), 0, len(items), parent)
    progress.setWindowTitle(tr('批量匯入'))
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)

    def step(done: int, total: int, name: str):
        progress.setValue(done)
        if name:
            progress.setLabelText('(%d/%d) %s' % (done + 1, total, name))
        return not progress.wasCanceled()

    try:
        return library.import_many(items, progress=step)
    finally:
        progress.close()
