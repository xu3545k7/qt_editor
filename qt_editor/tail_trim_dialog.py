"""曲庫工具「修剪結尾空白」的視窗：先掃描列出來，勾好再修。"""

from __future__ import annotations

from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QProgressDialog, QPushButton,
                             QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import tail_trim as T
from .hiraeth_export import TUTORIAL_PREFIX
from .ui_text import tr


def _progress(parent, title: str):
    box = QProgressDialog(tr('準備中…'), tr('取消'), 0, 1, parent)
    box.setWindowTitle(title)
    box.setWindowModality(Qt.WindowModal)
    box.setMinimumDuration(0)

    def step(done: int, total: int, name: str):
        box.setMaximum(max(1, total))
        box.setValue(done)
        if name:
            box.setLabelText('(%d/%d) %s' % (done + 1, total, name))
        return not box.wasCanceled()
    return box, step


class TailTrimDialog(QDialog):
    def __init__(self, parent, lib):
        super().__init__(parent)
        self.setWindowTitle(tr('修剪結尾空白'))
        self.resize(760, 520)
        self.lib = lib
        self.reports: List[T.TailReport] = []

        layout = QVBoxLayout(self)
        hint = QLabel(tr('兩種結尾空白都會抓：譜面的「曲終」拖在最後一顆音符後面（玩完要對著'
                         '空畫面等），還有音訊檔尾巴那一大段靜音（檔案變大，Hiraeth 輸出也會'
                         '卡在「譜面長度和音訊差 2 秒」）。曲終只會往前收，音訊會留到曲終之後'
                         '一小段，原檔先備份。'))
        hint.setWordWrap(True)
        hint.setStyleSheet('color: gray;')
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr('空白超過')))
        self.threshold = QSpinBox()
        self.threshold.setRange(500, 60000)
        self.threshold.setSingleStep(500)
        self.threshold.setSuffix(' ms')
        self.threshold.setValue(T.DEFAULT_THRESHOLD_MS)
        row.addWidget(self.threshold)
        row.addWidget(QLabel(tr('才算；修剪後留')))
        self.tail = QSpinBox()
        self.tail.setRange(0, 10000)
        self.tail.setSingleStep(250)
        self.tail.setSuffix(' ms')
        self.tail.setValue(T.DEFAULT_TAIL_MS)
        row.addWidget(self.tail)
        row.addWidget(QLabel(tr('收尾')))
        self.cut_outro = QCheckBox(tr('連尾奏一起剪'))
        self.cut_outro.setToolTip(tr('最後一顆音符之後就沒事做了，但音樂常常還在放。'
                                     '勾了會從那裡收尾（音樂淡出），沒勾只剪掉沒聲音的部分。'))
        self.cut_outro.toggled.connect(lambda _on: self.scan())
        row.addWidget(self.cut_outro)
        self.trim_audio = QCheckBox(tr('同時剪短音訊檔'))
        self.trim_audio.setChecked(True)
        self.trim_audio.setToolTip(tr('原檔（譜面與音訊）會先備份到 '
                                      'UserSongs_editor/tail_trim_backup。'
                                      '無背景音樂的曲子不會動音訊。'))
        row.addWidget(self.trim_audio)
        rescan = QPushButton(tr('重新掃描'))
        rescan.clicked.connect(lambda _c=False: self.scan())
        row.addWidget(rescan)
        row.addStretch(1)
        layout.addLayout(row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([tr('曲目'), tr('曲終'), tr('最後的內容'),
                                              tr('空白'), tr('音訊長度'), tr('音訊空白'),
                                              tr('音符後')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.status = QLabel('')
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText(tr('修剪勾選的'))
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.scan()

    # ── 掃描 ─────────────────────────────────────────────────────────

    def scan(self) -> None:
        box, step = _progress(self, tr('修剪結尾空白'))
        box.show()
        try:
            self.reports = T.scan_library(self.lib, progress=step,
                                          threshold_ms=self.threshold.value(),
                                          include_outro=self.cut_outro.isChecked())
        finally:
            box.close()
        self.table.setRowCount(0)
        for report in self.reports:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(report.title or report.folder)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # 新手教學結尾那幾秒是字幕在跑，預設不要動它
            tutorial = report.folder.startswith(TUTORIAL_PREFIX)
            item.setCheckState(Qt.Unchecked if tutorial else Qt.Checked)
            item.setData(Qt.UserRole, report.folder)
            item.setToolTip(tr('新手教學：結尾在跑字幕，預設不修') if tutorial else report.folder)
            self.table.setItem(row, 0, item)
            for column, value in ((1, report.finish_ms), (2, report.content_ms),
                                  (3, report.blank_ms), (4, report.audio_ms),
                                  (5, report.audio_blank_ms), (6, report.note_blank_ms)):
                cell = QTableWidgetItem('%.1f s' % (value / 1000.0) if value else '—')
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, cell)
        tutorials = sum(1 for r in self.reports if r.folder.startswith(TUTORIAL_PREFIX))
        text = tr('%d 首結尾空白超過 %.1f 秒') % (len(self.reports), self.threshold.value() / 1000.0)
        if tutorials:
            text += tr('（其中 %d 首是新手教學，預設沒勾）') % tutorials
        self.status.setText(text)
        self._ok.setEnabled(bool(self.reports))

    def checked(self) -> List[T.TailReport]:
        folders = {self.table.item(row, 0).data(Qt.UserRole)
                   for row in range(self.table.rowCount())
                   if self.table.item(row, 0).checkState() == Qt.Checked}
        return [r for r in self.reports if r.folder in folders]

    # ── 修剪 ─────────────────────────────────────────────────────────

    def apply(self) -> None:
        picked = self.checked()
        if not picked:
            return
        box, step = _progress(self, tr('修剪結尾空白'))
        box.show()
        try:
            done = T.trim_all(self.lib, picked, tail_ms=self.tail.value(),
                              trim_audio_file=self.trim_audio.isChecked(), progress=step,
                              cut_outro=self.cut_outro.isChecked())
        finally:
            box.close()
        trimmed_audio = sum(1 for d in done if d['audio_ms'])
        QMessageBox.information(
            self, tr('修剪結尾空白'),
            tr('修好 %d 首（其中 %d 首連音訊一起剪短）。\n'
               '原本的譜面與音訊備份在 UserSongs_editor/tail_trim_backup。')
            % (len(done), trimmed_audio))
        self.scan()
