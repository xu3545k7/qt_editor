"""
quantize_dialog.py
==================
量化（把音符起點對齊到拍點格線）的參數對話框。

放置音符時本來就會吸格，但 MIDI 匯進來的譜是人彈的，起點全是 4993ms 這種數字，
既有的音符沒有任何工具能對齊。這裡補上那一半。

刻意保留「量化強度」而不是只有全有全無：演奏的搖擺與呼吸也是資訊，一次拉到
100% 會把整首壓成機械節奏。先用 50% 收一收、聽過再決定要不要拉滿，是比較安全
的做法。

最上面那塊「拍線檢查」是後來加的。量化的格線是「把相鄰兩條拍線之間切成等分」，
所以拍線本身歪掉時，量化照樣會成功、也照樣會回報「已對齊」，但音符落在的位置
不是使用者以為的位置（使用者回饋：「有的稍微偏離了節拍線有的時間靠得比較近，
但是製譜器告訴我所有的 note 是量化好的」）。檢查結果放在格點選擇**上面**，因為
它會改變那個選擇——格線本身斷掉時，選多細的格點都沒有意義。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QLabel, QRadioButton, QSlider, QVBoxLayout, QWidget,
)
from PyQt5.QtCore import Qt


class QuantizeDialog(QDialog):
    """量化參數：格點 / 強度 / 範圍 / 是否連長度。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 grids=(), sel_count: int = 0,
                 default_grid_beats: float = 0.25,
                 grid_report=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('量化（對齊到格點）')
        self.setMinimumWidth(400)
        root = QVBoxLayout(self)

        # ── 拍線檢查（有話說才出現）────────────────────────────
        self.grid_report = grid_report
        self.report_box: Optional[QGroupBox] = None
        self._add_grid_report(root, grid_report)

        # ── 格點 ────────────────────────────────────────────────
        grid_box = QGroupBox('格點')
        grid_form = QFormLayout(grid_box)
        self._cb_grid = QComboBox()
        for label, beats in grids:
            self._cb_grid.addItem(label, float(beats))
        best_i, best_d = 0, None
        for i in range(self._cb_grid.count()):
            d = abs(float(self._cb_grid.itemData(i)) - float(default_grid_beats))
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        self._cb_grid.setCurrentIndex(best_i)
        grid_form.addRow('對齊到', self._cb_grid)

        self._sl_strength = QSlider(Qt.Horizontal)
        self._sl_strength.setRange(0, 100)
        self._sl_strength.setValue(100)
        self._lbl_strength = QLabel('100%')
        grid_form.addRow('量化強度', self._sl_strength)
        grid_form.addRow('', self._lbl_strength)
        self._sl_strength.valueChanged.connect(
            lambda v: self._lbl_strength.setText('%d%%　%s' % (
                v, '（完全對齊格線）' if v >= 100 else
                   '（保留 %d%% 的原始起伏）' % (100 - v))))
        root.addWidget(grid_box)

        # ── 範圍 ────────────────────────────────────────────────
        scope_box = QGroupBox('範圍')
        scope_form = QFormLayout(scope_box)
        self._rb_sel = QRadioButton('只有選取的 %d 顆' % int(sel_count))
        self._rb_all = QRadioButton('整份譜面')
        self._rb_sel.setEnabled(int(sel_count) > 0)
        if int(sel_count) > 0:
            self._rb_sel.setChecked(True)
        else:
            self._rb_all.setChecked(True)
        scope_form.addRow(self._rb_sel)
        scope_form.addRow(self._rb_all)
        root.addWidget(scope_box)

        # ── 長度 ────────────────────────────────────────────────
        len_box = QGroupBox('長度')
        len_form = QFormLayout(len_box)
        self._ck_length = QCheckBox('結束點也對齊（長押才有意義）')
        self._ck_length.setToolTip(
            '不勾：整顆平移，長度原封不動。\n'
            '勾了：頭尾都對齊到格線，長押會被拉成整數個格子。')
        len_form.addRow(self._ck_length)
        self._cb_min = QComboBox()
        for label, beats in grids:
            self._cb_min.addItem(label, float(beats))
        self._cb_min.setCurrentIndex(self._cb_grid.count() - 1)
        self._cb_min.setEnabled(False)
        len_form.addRow('最短保留', self._cb_min)
        self._ck_length.toggled.connect(self._cb_min.setEnabled)
        root.addWidget(len_box)

        hint = QLabel('格線是把相鄰兩條拍線之間切成等分，所以變速、變拍號的'
                      '段落也對得上——但拍線本身的間距不對時，格線也會跟著歪。')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: gray; font-size: 11px;')
        root.addWidget(hint)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

    def _add_grid_report(self, root: QVBoxLayout, rep) -> None:
        """把拍線體檢的結果放到最上面。沒話說就不佔位置。"""
        if rep is None or not getattr(rep, 'enough', False):
            return
        warns = list(rep.warnings())
        notes = list(rep.notes())
        if not warns and not notes:
            return

        box = QGroupBox('拍線檢查')
        v = QVBoxLayout(box)
        head = QLabel(rep.summary())
        head.setWordWrap(True)
        v.addWidget(head)
        for text in warns:
            lbl = QLabel('⚠　' + text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet('color: #b35300;')
            v.addWidget(lbl)
        for text in notes:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet('color: gray;')
            v.addWidget(lbl)
        if warns:
            tail = QLabel(
                '量化還是會做，而且做完會回報「已對齊」——因為那些音符確實會'
                '落在上面這條格線上。但格線在斷點那裡是歪的，所以對齊後的'
                '間隔不會平均。先修拍線再量化，結果才會是你想的那樣。')
            tail.setWordWrap(True)
            tail.setStyleSheet('color: #b35300; font-size: 11px;')
            v.addWidget(tail)
        self.report_box = box
        root.addWidget(box)
        # 這塊是整段文字，400px 寬會疊成很高的一塊；放寬一點比較好讀
        self.setMinimumWidth(500)

    def params(self) -> dict:
        return {
            'grid_beats':     float(self._cb_grid.currentData()),
            'strength':       self._sl_strength.value() / 100.0,
            'whole_chart':    bool(self._rb_all.isChecked()),
            'also_length':    bool(self._ck_length.isChecked()),
            'min_gate_beats': (float(self._cb_min.currentData())
                               if self._ck_length.isChecked() else 0.0),
        }
