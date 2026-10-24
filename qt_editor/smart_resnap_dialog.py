# -*- coding: utf-8 -*-
"""「智慧重排吸附」的對話框：一張表輸入「幾個小節 ＋ 拍號 ＋ BPM」的序列。

**表格裡沒有小節編號。** 這個工具的用途正是「原本的小節編號是錯判的產物」，
拿它當輸入就會繼承那個錯誤（見 `smart_resnap` 的模組說明）。使用者給的是
「第一段幾小節、多快」，編號是鋪完之後算出來的。

預覽是重點：段落總和幾乎不可能一次就對上曲長，所以要即時告訴他差幾毫秒、
換算成幾個小節，他才有辦法調。
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                             QHeaderView, QLabel, QPushButton, QSpinBox,
                             QTableWidget, QVBoxLayout, QWidget)

from . import smart_resnap as SR
from .ui_text import tr

#: 吸附格：(顯示文字, 一拍切幾格)
DIVISIONS = (('4 分音符', 1), ('8 分音符', 2), ('8 分三連音', 3),
             ('16 分音符', 4), ('16 分三連音', 6), ('32 分音符', 8))


class SmartResnapDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], model) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('智慧重排吸附'))
        self.setMinimumWidth(620)
        self._model = model

        root = QVBoxLayout(self)
        root.addWidget(QLabel(tr(
            '照「幾個小節 ＋ BPM」的順序重建整條小節線，再把音符吸附上去。\n'
            '用在「AI 把整首判成一個固定 BPM、而實際是變拍」的譜：原本的小節'
            '編號是錯判的結果，所以這裡不填小節編號，只填每一段有幾個小節。\n'
            '音符只會被搬到最近的新格點，不做任何縮放，所以樂曲總長度不變。')))

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(
            [tr('小節數'), tr('分子'), tr('分母'), tr('BPM')])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self._table)

        row = QHBoxLayout()
        add = QPushButton(tr('＋ 加一段'))
        add.clicked.connect(lambda _checked=False: self._add_row())
        dup = QPushButton(tr('⧉ 複製最後一段'))
        dup.clicked.connect(lambda _checked=False: self._add_row(copy_last=True))
        auto = QPushButton(tr('🔍 自動偵測'))
        auto.setToolTip(tr(
            '從原始的 AI MIDI 量出速度結構，直接填進表格。\n'
            '找的是同名的 .ai.mid——那份沒被吸附過，才量得到真正的速度；'
            '已經存成譜面的那份量出來只會確認原本的判斷。'))
        auto.clicked.connect(lambda _checked=False: self._autodetect())
        row.addWidget(auto)
        fill = QPushButton(tr('↳ 最後一段補到曲末'))
        fill.setToolTip(tr(
            '照最後一段的 BPM 與拍號算出還要幾個小節才蓋到最後一顆音符，'
            '直接填進去。'))
        fill.clicked.connect(lambda _checked=False: self._fill_last())
        row.addWidget(fill)
        drop = QPushButton(tr('－ 刪掉選到的'))
        drop.clicked.connect(lambda _checked=False: self._drop_row())
        for btn in (add, dup):
            row.addWidget(btn)
        row.addWidget(drop)
        row.addStretch(1)
        root.addLayout(row)

        opts = QHBoxLayout()
        self._snap = QCheckBox(tr('把音符吸附到新格線'))
        self._snap.setChecked(True)
        self._snap.setToolTip(tr(
            '只搬音符的起點、長度跟著平移，不做縮放。\n'
            '不勾就只換小節線，音符原地不動。'))
        self._snap.toggled.connect(lambda _on: self._refresh())
        opts.addWidget(self._snap)
        opts.addWidget(QLabel(tr('吸附到')))
        self._division = QComboBox()
        for label, div in DIVISIONS:
            self._division.addItem(tr(label), div)
        self._division.setCurrentIndex(3)          # 16 分
        opts.addWidget(self._division)
        opts.addStretch(1)
        root.addLayout(opts)

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setTextFormat(Qt.PlainText)
        self._preview.setStyleSheet('font-size: 11px;')
        root.addWidget(self._preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)

        self._add_row()
        self._refresh()

    # ── 表格 ──────────────────────────────────────────────────────────

    def _add_row(self, copy_last: bool = False) -> None:
        bars, num, den, bpm = 16, 4, 4, float(getattr(self._model, 'bpm', 0) or 120.0)
        if copy_last and self._table.rowCount():
            r = self._table.rowCount() - 1
            bars = int(self._cell(r, 0).value())
            num = int(self._cell(r, 1).value())
            den = int(self._cell(r, 2).value())
            bpm = float(self._cell(r, 3).value())
        r = self._table.rowCount()
        self._table.insertRow(r)
        bars_spin = QSpinBox()
        bars_spin.setRange(1, 999)
        bars_spin.setValue(bars)
        num_spin = QSpinBox()
        num_spin.setRange(1, 64)
        num_spin.setValue(num)
        den_spin = QSpinBox()
        den_spin.setRange(1, 64)
        den_spin.setValue(den)
        bpm_spin = QDoubleSpinBox()
        bpm_spin.setRange(1.0, 9999.0)
        bpm_spin.setDecimals(2)
        bpm_spin.setValue(bpm)
        for spin, col in ((bars_spin, 0), (num_spin, 1), (den_spin, 2), (bpm_spin, 3)):
            spin.valueChanged.connect(lambda _v: self._refresh())
            self._table.setCellWidget(r, col, spin)
        self._refresh()

    def raw_midi_path(self) -> Optional[str]:
        """同名的 `.ai.mid`：沒被吸附過的那份。

        譜面檔叫 `<曲>.ai.199.95bpm.json`，原始 MIDI 是 `<曲>.ai.mid`，所以砍掉
        `.ai.` 之後的全部再接回去。
        """
        import os
        current = getattr(self._model, 'current_file', '') or ''
        if not current:
            return None
        head, _sep, _tail = current.partition('.ai.')
        candidate = head + '.ai.mid'
        return candidate if os.path.isfile(candidate) else None

    def _autodetect(self) -> None:
        """量出速度結構並填進表格。"""
        from PyQt5.QtWidgets import QMessageBox

        path = self.raw_midi_path()
        if not path:
            QMessageBox.information(
                self, tr('找不到原始 MIDI'),
                tr('自動偵測要用同名的 .ai.mid（沒被吸附過的那份）。\n'
                   '已經存成譜面的音符被原本的格線吸過，量出來只會確認那個判斷。'))
            return
        from . import ai_grid as G
        try:
            onsets = [t * 1000.0 for t, _p, _v in G.onsets_for_detection(path)]
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, tr('讀不到'), str(exc))
            return
        segs = SR.detect_segments(onsets)
        if not segs:
            QMessageBox.information(self, tr('偵測不出來'),
                                    tr('音符太少或速度太不穩，請自己填。'))
            return
        self._set_rows(segs)

    def _set_rows(self, segs) -> None:
        """用一組段落重建整張表。"""
        while self._table.rowCount():
            self._table.removeRow(0)
        for seg in segs:
            self._add_row()
            r = self._table.rowCount() - 1
            self._cell(r, 0).setValue(int(seg.bars))
            self._cell(r, 1).setValue(int(seg.numerator))
            self._cell(r, 2).setValue(int(seg.denominator))
            self._cell(r, 3).setValue(float(seg.bpm))
        self._refresh()

    def _fill_last(self) -> None:
        """把最後一段的小節數改成「剛好蓋到曲末」。

        使用者：「用精準 bpm 加上拍號應該能判斷出來總長度了」。手算很煩而且
        容易差一兩個小節，交給 `bars_to_cover`。
        """
        r = self._table.rowCount() - 1
        if r < 0:
            return
        segs = self.segments()
        if not segs:
            return
        need = SR.bars_to_cover(self._model, segs[-1], before=segs[:-1])
        self._cell(r, 0).setValue(max(1, need))

    def _cell(self, row: int, col: int):
        return self._table.cellWidget(row, col)

    def _drop_row(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows and self._table.rowCount():
            rows = [self._table.rowCount() - 1]
        for r in rows:
            self._table.removeRow(r)
        self._refresh()

    # ── 結果 ──────────────────────────────────────────────────────────

    def segments(self) -> List[SR.Segment]:
        out: List[SR.Segment] = []
        for r in range(self._table.rowCount()):
            cells = [self._cell(r, c) for c in range(4)]
            if any(c is None for c in cells):
                continue
            out.append(SR.Segment(bars=int(cells[0].value()),
                                  numerator=int(cells[1].value()),
                                  denominator=int(cells[2].value()),
                                  bpm=float(cells[3].value())))
        return out

    def snap_enabled(self) -> bool:
        return bool(self._snap.isChecked())

    def division(self) -> int:
        return int(self._division.currentData() or 4)

    def _refresh(self) -> None:
        self._division.setEnabled(self.snap_enabled())
        segs = self.segments()
        if not segs:
            self._preview.setText(tr('還沒有段落。'))
            self._ok.setEnabled(False)
            return
        item = SR.plan(self._model, segs)
        self._preview.setText(SR.describe(item))
        self._ok.setEnabled(item.ok)
