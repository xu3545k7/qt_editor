# -*- coding: utf-8 -*-
"""「每小節 BPM」的表：一個小節一行，各自填自己的 BPM。

使用者要的：「每個小節線單獨 BPM」——原本只能一個小節一個小節改，或者把
「第 X 到第 Y 小節」整段設成同一個值。

這張表**只動被改過的那幾行**（`NoteModel.set_measure_grid` 的核心性質）。
理由寫在那個方法裡：BPM 不是檔案裡的東西，而是由小節長度反推、還吸附過的
「看法」，所以「讀出全部 BPM 再照它重鋪」不是恆等操作——實測 400 份真譜有
三成會跑掉。只動改過的行，打開按確定就真的什麼都不會變。

**音符預設一顆都不動。** 小節線是譜面底層的結構（使用者：「小節線編輯功能
需要處在譜面編輯更底層的位置」），改它不該改到音符的時間。

拍號那一欄是**唯讀**的：改拍號要變動一小節裡的拍點筆數，那是
`set_measure_time_signature` 的工作（走 小節 → 修改小節拍號）。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                             QHeaderView, QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .ui_text import tr

COL_MEASURE, COL_BPM, COL_SIG, COL_LEN = range(4)


class MeasureBpmDialog(QDialog):
    """一列一個小節：小節號、BPM、拍號、長度。"""

    def __init__(self, parent: Optional[QWidget], model) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('每小節 BPM'))
        self.setMinimumSize(560, 520)
        self._model = model
        self._orig: List[Tuple[int, int, float]] = list(model.measure_grid_rows())

        root = QVBoxLayout(self)
        root.addWidget(QLabel(tr(
            '一個小節一行，各自填自己的 BPM。只有被改過的行會重鋪，其餘小節的'
            '拍點原封不動（只跟著前面的變化平移）。\n'
            '音符預設一顆都不動——改的是小節線，不是譜。')))

        self._table = QTableWidget(len(self._orig), 4, self)
        self._table.setHorizontalHeaderLabels(
            [tr('小節'), tr('BPM'), tr('拍號'), tr('長度')])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(COL_MEASURE, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(COL_BPM, QHeaderView.Stretch)
        head.setSectionResizeMode(COL_SIG, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(COL_LEN, QHeaderView.ResizeToContents)
        root.addWidget(self._table)

        self._spins: List[QDoubleSpinBox] = []
        for r, (num, den, bpm) in enumerate(self._orig):
            self._fill_row(r, num, den, bpm)

        row = QHBoxLayout()
        down = QPushButton(tr('↓ 套用到以下全部'))
        down.setToolTip(tr('把選到的那一行的 BPM 填到它下面每一行。\n'
                           '「從這裡開始變速」用這個最快。'))
        down.clicked.connect(lambda _c=False: self._fill_down())
        sel = QPushButton(tr('套用到選取的行'))
        sel.setToolTip(tr('把選到的第一行的 BPM 填到其餘選取的行。'))
        sel.clicked.connect(lambda _c=False: self._fill_selected())
        undo_row = QPushButton(tr('↺ 還原選取的行'))
        undo_row.clicked.connect(lambda _c=False: self._reset_selected())
        undo_all = QPushButton(tr('↺ 全部還原'))
        undo_all.clicked.connect(lambda _c=False: self._reset_all())
        for btn in (down, sel, undo_row, undo_all):
            row.addWidget(btn)
        row.addStretch(1)
        root.addLayout(row)

        self._snap = QCheckBox(tr('把音符吸附到新格線'))
        self._snap.setToolTip(tr(
            '預設不勾：只改小節線，音符一顆都不動。\n'
            '勾了才會把音符搬到最近的新格點（只搬起點、長度跟著平移，不縮放）。'))
        self._snap.toggled.connect(lambda _on: self._refresh())
        root.addWidget(self._snap)

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setTextFormat(Qt.PlainText)
        self._preview.setStyleSheet('font-size: 11px;')
        root.addWidget(self._preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    # ── 表格 ──────────────────────────────────────────────────────────

    def _fill_row(self, r: int, num: int, den: int, bpm: float) -> None:
        label = QTableWidgetItem('%d' % (r + 1))
        label.setFlags(Qt.ItemIsEnabled)
        self._table.setItem(r, COL_MEASURE, label)

        spin = QDoubleSpinBox()
        spin.setRange(1.0, 9999.0)
        spin.setDecimals(2)
        spin.setValue(float(bpm))
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda _v: self._refresh())
        self._table.setCellWidget(r, COL_BPM, spin)
        self._spins.append(spin)

        sig = QTableWidgetItem('%d/%d' % (num, den))
        sig.setFlags(Qt.ItemIsEnabled)
        sig.setToolTip(tr('拍號要走 小節 → 修改小節拍號（它要變動一小節裡的'
                          '拍點筆數，是另一套邏輯）。'))
        self._table.setItem(r, COL_SIG, sig)

        length = QTableWidgetItem('')
        length.setFlags(Qt.ItemIsEnabled)
        self._table.setItem(r, COL_LEN, length)

    def _selected_rows(self) -> List[int]:
        return sorted({i.row() for i in self._table.selectedIndexes()})

    def _fill_down(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        value = self._spins[rows[0]].value()
        for r in range(rows[0] + 1, len(self._spins)):
            self._spins[r].setValue(value)

    def _fill_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) < 2:
            return
        value = self._spins[rows[0]].value()
        for r in rows[1:]:
            self._spins[r].setValue(value)

    def _reset_selected(self) -> None:
        for r in self._selected_rows():
            if r < len(self._orig):
                self._spins[r].setValue(float(self._orig[r][2]))

    def _reset_all(self) -> None:
        for r, (_n, _d, bpm) in enumerate(self._orig):
            self._spins[r].setValue(float(bpm))

    # ── 結果 ──────────────────────────────────────────────────────────

    def rows(self) -> List[Tuple[int, int, float]]:
        out: List[Tuple[int, int, float]] = []
        for r, (num, den, _bpm) in enumerate(self._orig):
            out.append((int(num), int(den), float(self._spins[r].value())))
        return out

    def snap_enabled(self) -> bool:
        return bool(self._snap.isChecked())

    def changed_rows(self) -> List[int]:
        eps = float(getattr(self._model, 'MEASURE_BPM_EPS', 0.02))
        return [r for r, (_n, _d, bpm) in enumerate(self._orig)
                if abs(self._spins[r].value() - float(bpm)) > eps]

    def _refresh(self) -> None:
        """更新長度欄與摘要。長度是「這一行會變成幾毫秒」，比 BPM 誠實。"""
        shift = 0.0
        changed = self.changed_rows()
        changed_set = set(changed)
        for r, (num, den, _bpm) in enumerate(self._orig):
            bpm = float(self._spins[r].value())
            units = self._model._bar_units_for(int(num), int(den))
            new_ms = units * 60000.0 / max(1e-6, bpm)
            item = self._table.item(r, COL_LEN)
            if item is not None:
                item.setText('%d ms' % int(round(new_ms)))
            if r in changed_set:
                old = self._model.get_measure_time_range(r)
                if old and old[0] is not None and old[1] is not None:
                    shift += new_ms - float(old[1] - old[0])
        lines = ['共 %d 個小節，改了 %d 行。' % (len(self._orig), len(changed))]
        if changed:
            lines.append('第 %s 小節之後的小節線會往%s移 %d ms。'
                         % (changed[0] + 1, '後' if shift > 0 else '前',
                            int(round(abs(shift)))))
        else:
            lines.append('沒有任何改動——按確定不會動到譜面。')
        if self.snap_enabled():
            lines.append('音符會被吸附到新格線（只搬起點）。')
        else:
            lines.append('音符一顆都不會動。')
        self._preview.setText('\n'.join(lines))
