"""譜面事件編輯：速度（BPM）、音效參數（殘響／回音／EQ）、區段標記。

事件是 PAN 譜面 `<event_data>` 的內容，JSON 也存（`event_data`）。速度事件
在檔案裡是 BPM × 100000，這裡直接用 BPM 顯示與輸入。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton,
                             QSpinBox, QStyledItemDelegate, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from .pan_format import BPM_SCALE, DEFAULT_EFFECT_EVENTS, EVENT_TYPES, TEMPO_EVENT

COL_MS, COL_MEASURE, COL_TYPE, COL_VALUE, COL_NOTE = range(5)
RAW = Qt.UserRole

FILTERS = (('全部', None), ('只看速度', {TEMPO_EVENT}),
           ('只看音效（1～8）', set(range(1, 9))), ('只看區段標記', {9}))


def type_label(ty: int) -> str:
    name = EVENT_TYPES.get(int(ty), ('未知', ''))[0]
    return f'{int(ty)}  {name}'


def value_text(ty: int, raw: int) -> str:
    if int(ty) == TEMPO_EVENT:
        return f'{raw / BPM_SCALE:.3f}'.rstrip('0').rstrip('.') + ' BPM'
    return str(int(raw))


class _TypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for ty in sorted(EVENT_TYPES):
            combo.addItem(type_label(ty), ty)
        return combo

    def setEditorData(self, editor, index):
        pos = editor.findData(int(index.data(RAW) or 0))
        editor.setCurrentIndex(max(0, pos))

    def setModelData(self, editor, model, index):
        ty = int(editor.currentData())
        model.setData(index, ty, RAW)
        model.setData(index, type_label(ty), Qt.DisplayRole)


class _ValueDelegate(QStyledItemDelegate):
    """速度用 BPM（小數）輸入，其他類型用整數。"""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table

    def _type(self, index) -> int:
        item = self._table.item(index.row(), COL_TYPE)
        return int(item.data(RAW) or 0) if item is not None else 0

    def createEditor(self, parent, option, index):
        if self._type(index) == TEMPO_EVENT:
            spin = QDoubleSpinBox(parent)
            spin.setDecimals(5)
            spin.setRange(1.0, 2000.0)
            spin.setSuffix(' BPM')
            return spin
        spin = QSpinBox(parent)
        spin.setRange(-1_000_000, 1_000_000)
        return spin

    def setEditorData(self, editor, index):
        raw = int(index.data(RAW) or 0)
        if isinstance(editor, QDoubleSpinBox):
            editor.setValue(raw / BPM_SCALE)
        else:
            editor.setValue(raw)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QDoubleSpinBox):
            raw = int(round(editor.value() * BPM_SCALE))
        else:
            raw = int(editor.value())
        model.setData(index, raw, RAW)


class EventEditorDialog(QDialog):
    """編輯譜面事件。按「確定」後用 `events()` 取結果（已照時間排序）。"""

    def __init__(self, parent, model, current_ms: float = 0.0,
                 jump: Optional[Callable[[float], None]] = None):
        super().__init__(parent)
        self.setWindowTitle('事件（速度／音效）')
        self.resize(640, 520)
        self._model = model
        self._current_ms = max(0, int(current_ms))
        self._jump = jump
        self._filling = False

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self._filter = QComboBox()
        for label, _types in FILTERS:
            self._filter.addItem(label)
        self._filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        top.addWidget(QLabel('顯示：'))
        top.addWidget(self._filter)
        top.addStretch(1)
        self._count = QLabel('')
        top.addWidget(self._count)
        layout.addLayout(top)

        self._hint = QLabel('')
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet('color: gray;')
        layout.addWidget(self._hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['時間 (ms)', '小節', '類型', '值', '說明'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.table.setItemDelegateForColumn(COL_TYPE, _TypeDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_VALUE, _ValueDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        row1 = QHBoxLayout()
        for text, slot, tip in (
                ('新增（目前位置）', self._add_here, '在判定線所在的時間新增一個速度事件'),
                ('刪除選取', self._delete_selected, ''),
                ('跳到這個事件', self._jump_selected, '把譜面捲到選取事件的時間（也可以雙擊時間欄）'),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            row1.addWidget(btn)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        for text, slot, tip in (
                ('從小節 BPM 重建速度事件', self._rebuild_tempo,
                 '把所有速度事件換成照目前每小節 BPM 算出來的（開頭一個，BPM 有變的小節各一個）'),
                ('補上音效預設值', self._add_effect_defaults,
                 '開頭沒有的音效類型（1～8）補上官方最常見的開頭值'),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            row2.addWidget(btn)
        row2.addStretch(1)
        layout.addLayout(row2)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        events = model.sorted_events()
        if events:
            self._hint.setText('雙擊儲存格直接改。速度的值用 BPM 輸入，存檔時換成 BPM × 100000。')
        else:
            events = model.default_events()
            self._hint.setText('這份譜還沒有事件。下面是照小節 BPM 產生的速度事件加上音效預設值，'
                               '按「確定」才會寫進譜面。')
        self._fill(events)

    # ── 表格 ─────────────────────────────────────────────────────────

    def _fill(self, events: List[List[int]]) -> None:
        self._filling = True
        try:
            self.table.setRowCount(0)
            for ms, ty, value in sorted(events, key=lambda e: (e[0], e[1])):
                self._append_row(ms, ty, value)
        finally:
            self._filling = False
        self._apply_filter()

    def _append_row(self, ms: int, ty: int, value: int) -> int:
        was = self._filling
        self._filling = True
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            ms_item = QTableWidgetItem()
            ms_item.setData(Qt.EditRole, int(ms))
            self.table.setItem(row, COL_MS, ms_item)
            measure = QTableWidgetItem()
            measure.setFlags(measure.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, COL_MEASURE, measure)
            type_item = QTableWidgetItem(type_label(ty))
            type_item.setData(RAW, int(ty))
            self.table.setItem(row, COL_TYPE, type_item)
            value_item = QTableWidgetItem()
            value_item.setData(RAW, int(value))
            self.table.setItem(row, COL_VALUE, value_item)
            note = QTableWidgetItem()
            note.setFlags(note.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, COL_NOTE, note)
            self._refresh_row(row)
            return row
        finally:
            self._filling = was

    def _row_values(self, row: int) -> List[int]:
        return [int(self.table.item(row, COL_MS).data(Qt.EditRole) or 0),
                int(self.table.item(row, COL_TYPE).data(RAW) or 0),
                int(self.table.item(row, COL_VALUE).data(RAW) or 0)]

    def _refresh_row(self, row: int) -> None:
        ms, ty, value = self._row_values(row)
        was = self._filling
        self._filling = True
        try:
            measure = self._model.get_measure_at_ms(float(ms)) + 1
            self.table.item(row, COL_MEASURE).setText(str(measure))
            self.table.item(row, COL_VALUE).setText(value_text(ty, value))
            self.table.item(row, COL_NOTE).setText(EVENT_TYPES.get(ty, ('', '事件類型不存在'))[1])
        finally:
            self._filling = was

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        row = item.row()
        if item.column() == COL_TYPE:
            # 換類型時值的意義跟著變：換成速度就給目前 BPM，換走就歸零
            ty = int(item.data(RAW) or 0)
            value_item = self.table.item(row, COL_VALUE)
            raw = int(value_item.data(RAW) or 0)
            self._filling = True
            try:
                if ty == TEMPO_EVENT and raw < BPM_SCALE:
                    value_item.setData(RAW, int(round(float(self._model.bpm) * BPM_SCALE)))
                elif ty != TEMPO_EVENT and raw >= BPM_SCALE:
                    value_item.setData(RAW, int(DEFAULT_EFFECT_EVENTS.get(ty, 0)))
            finally:
                self._filling = False
        if item.column() in (COL_MS, COL_TYPE, COL_VALUE):
            self._refresh_row(row)
            self._apply_filter()

    def _apply_filter(self) -> None:
        wanted = FILTERS[self._filter.currentIndex()][1]
        shown = 0
        for row in range(self.table.rowCount()):
            ty = int(self.table.item(row, COL_TYPE).data(RAW) or 0)
            hide = wanted is not None and ty not in wanted
            self.table.setRowHidden(row, hide)
            shown += 0 if hide else 1
        self._count.setText(f'{shown} / {self.table.rowCount()} 個事件')

    # ── 按鈕 ─────────────────────────────────────────────────────────

    def _selected_rows(self) -> List[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _add_here(self) -> None:
        ms = self._current_ms
        bpm = float(self._model.get_measure_bpm(self._model.get_measure_at_ms(float(ms))))
        row = self._append_row(ms, TEMPO_EVENT, int(round(bpm * BPM_SCALE)))
        self._filter.setCurrentIndex(0)
        self._apply_filter()
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, COL_MS))

    def _delete_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            self.table.removeRow(row)
        self._apply_filter()

    def _jump_selected(self) -> None:
        rows = self._selected_rows()
        if rows and self._jump is not None:
            self._jump(float(self._row_values(rows[0])[0]))

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        if item.column() == COL_MEASURE and self._jump is not None:
            self._jump(float(self._row_values(item.row())[0]))

    def _rebuild_tempo(self) -> None:
        kept = [e for e in self.events() if e[1] != TEMPO_EVENT]
        self._fill(self._model.tempo_events_from_measures() + kept)

    def _add_effect_defaults(self) -> None:
        events = self.events()
        present = {ty for _ms, ty, _v in events}
        extra = [[0, ty, value] for ty, value in sorted(DEFAULT_EFFECT_EVENTS.items())
                 if ty not in present]
        self._fill(events + extra)

    # ── 結果 ─────────────────────────────────────────────────────────

    def events(self) -> List[List[int]]:
        rows = [self._row_values(row) for row in range(self.table.rowCount())]
        return sorted(rows, key=lambda e: (e[0], e[1]))

    def _accept(self) -> None:
        bad = [e for e in self.events() if e[1] == TEMPO_EVENT and e[2] <= 0]
        if bad:
            QMessageBox.warning(self, '事件', '速度事件的 BPM 要大於 0（第一個在 %d ms）。' % bad[0][0])
            return
        unknown = [e for e in self.events() if e[1] not in EVENT_TYPES]
        if unknown:
            QMessageBox.warning(self, '事件', '有 PAN 沒有的事件類型 %d。' % unknown[0][1])
            return
        self.accept()
