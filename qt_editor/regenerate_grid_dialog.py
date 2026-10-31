# -*- coding: utf-8 -*-
"""「鎖定精確位置、重生小節線」：丟掉現在的格線，照一個固定 BPM 重鋪一條。

和「智慧重排吸附」的差別只有一件事，但那件事就是重點：

    智慧重排吸附   格線是對的 → 把音符搬上去（會動音符）
    重生小節線     音符是對的 → 重算格線套上去（**音符一顆都不動**）

所以這裡**沒有吸附選項**。有的話就不是這個功能了。

用在「AI 把整首判成一個固定 BPM、而那個值整個錯掉」的譜：音符的毫秒位置是
對的（它們來自音訊），錯的只有格線。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from . import smart_resnap as SR
from .ui_text import tr

START_KEEP, START_FIRST_NOTE, START_CUSTOM = range(3)


class RegenerateGridDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], model,
                 selection_span=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('重生小節線'))
        self.setMinimumWidth(460)
        self._model = model
        self._span = selection_span            # (起, 迄) 毫秒；沒選東西就是 None
        self._ready = False

        root = QVBoxLayout(self)
        lead = QLabel(tr(
            '丟掉現在的小節線，照下面的速度重鋪一條。\n'
            '**音符一顆都不會動**——這個功能就是為了「音符位置是對的、只有'
            '格線錯了」的譜做的，所以沒有吸附選項。'))
        lead.setWordWrap(True)
        root.addWidget(lead)

        # ── 速度 ──────────────────────────────────────────────────────
        speed = QGroupBox(tr('速度與拍號'))
        form = QFormLayout(speed)
        row = QHBoxLayout()
        self._bpm = QDoubleSpinBox()
        self._bpm.setRange(1.0, 999.0)
        self._bpm.setDecimals(2)
        self._bpm.setValue(float(getattr(model, 'bpm', 0) or 120.0))
        self._bpm.valueChanged.connect(lambda _v: self._refresh())
        row.addWidget(self._bpm, 1)
        detect = QPushButton(tr('🔍 從音符偵測'))
        detect.setToolTip(tr(
            '從譜面上的音符起音量一個速度出來。\n'
            '注意：量的是**現在譜面上**的音符。如果它們已經被錯的格線吸附過，'
            '量到的就是那個錯誤——那種情況要用原始的 .ai.mid（智慧重排吸附裡'
            '的自動偵測走的是那條）。'))
        detect.clicked.connect(lambda _c=False: self._detect())
        row.addWidget(detect)
        form.addRow(tr('BPM'), row)

        sig = QHBoxLayout()
        self._num = QSpinBox()
        self._num.setRange(1, 64)
        self._num.setValue(max(1, int(getattr(model, 'beats_per_bar', 4) or 4)))
        self._den = QSpinBox()
        self._den.setRange(1, 64)
        self._den.setValue(max(1, int(getattr(model, 'time_sig_denominator', 4) or 4)))
        for spin in (self._num, self._den):
            spin.valueChanged.connect(lambda _v: self._refresh())
        sig.addWidget(self._num)
        sig.addWidget(QLabel('/'))
        sig.addWidget(self._den)
        sig.addStretch(1)
        form.addRow(tr('拍號'), sig)

        # ── 從選取範圍定速（編曲軟體的 set tempo from selection）──────
        #
        # Logic 的「Adjust Tempo using…」、Reaper 的「Set project tempo from
        # time selection」、Cubase 的 Time Warp 都是同一招：素材是對的，你知道
        # 它是幾小節，速度讓軟體算。對我們正好就是「鎖定精確位置」那條路。
        pick = QHBoxLayout()
        self._bars = QSpinBox()
        self._bars.setRange(1, 999)
        self._bars.setValue(4)
        self._bars.setSuffix(tr(' 小節'))
        pick.addWidget(self._bars)
        self._from_sel = QPushButton(tr('← 從選取範圍定速'))
        self._from_sel.clicked.connect(lambda _c=False: self._from_selection())
        pick.addWidget(self._from_sel)
        pick.addStretch(1)
        form.addRow(tr('選取的那一段是'), pick)
        self._sel_note = QLabel()
        self._sel_note.setWordWrap(True)
        self._sel_note.setStyleSheet('color: #555; font-size: 11px;')
        form.addRow('', self._sel_note)
        root.addWidget(speed)

        # ── 從哪裡開始鋪 ──────────────────────────────────────────────
        start = QGroupBox(tr('第一條小節線放哪'))
        sbox = QVBoxLayout(start)
        keep_ms = int(round(SR.grid_start_ms(model)))
        first_ms = int(min((int(n.start) for n in model.notes_tree), default=0))
        self._rb_keep = QRadioButton(
            tr('沿用現在的第一個拍點（%d ms）') % keep_ms)
        self._rb_first = QRadioButton(tr('對齊第一顆音符（%d ms）') % first_ms)
        self._rb_custom = QRadioButton(tr('自訂'))
        self._start_group = QButtonGroup(self)
        for i, rb in ((START_KEEP, self._rb_keep),
                      (START_FIRST_NOTE, self._rb_first),
                      (START_CUSTOM, self._rb_custom)):
            self._start_group.addButton(rb, i)
            sbox.addWidget(rb)
            rb.toggled.connect(lambda _on: self._refresh())
        self._rb_keep.setChecked(True)
        self._keep_ms, self._first_ms = keep_ms, first_ms

        crow = QHBoxLayout()
        crow.addSpacing(24)
        self._custom = QSpinBox()
        self._custom.setRange(0, 3600000)
        self._custom.setSuffix(' ms')
        self._custom.setValue(keep_ms)
        self._custom.valueChanged.connect(lambda _v: self._refresh())
        crow.addWidget(self._custom)
        crow.addStretch(1)
        sbox.addLayout(crow)
        root.addWidget(start)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.PlainText)
        self._summary.setStyleSheet('font-size: 11px;')
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ready = True
        self._refresh()

    # ── 取值 ──────────────────────────────────────────────────────────

    def bpm(self) -> float:
        return float(self._bpm.value())

    def signature(self):
        return int(self._num.value()), int(self._den.value())

    def start_ms(self) -> float:
        which = self._start_group.checkedId()
        if which == START_FIRST_NOTE:
            return float(self._first_ms)
        if which == START_CUSTOM:
            return float(self._custom.value())
        return float(self._keep_ms)

    def plan(self):
        num, den = self.signature()
        return SR.regenerate(self._model, self.bpm(), num, den, self.start_ms())

    # ── 畫面 ──────────────────────────────────────────────────────────

    def _from_selection(self) -> None:
        """選取的那一段是 N 個小節 → 算出 BPM，順便把起點對到它的開頭。"""
        from PyQt5.QtWidgets import QMessageBox

        if not self._span:
            QMessageBox.information(self, tr('沒有選取'),
                                    tr('先在譜面上選一段音符。'))
            return
        lo, hi = self._span
        num, den = self.signature()
        bpm = SR.bpm_from_span(float(hi) - float(lo), self._bars.value(), num, den)
        if not bpm:
            QMessageBox.information(
                self, tr('算不出來'),
                tr('這一段 %.0f ms 分成 %d 小節會得到不合理的速度。')
                % (float(hi) - float(lo), self._bars.value()))
            return
        self._bpm.setValue(float(bpm))
        self._custom.setValue(int(round(float(lo))))
        self._rb_custom.setChecked(True)       # 起點對到選取的開頭
        self._refresh()

    def _detect(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        bpm = SR.detect_bpm(self._model, int(self._num.value()))
        if not bpm:
            QMessageBox.information(
                self, tr('偵測不出來'),
                tr('音符太少或速度太不穩，請自己填。'))
            return
        self._bpm.setValue(float(bpm))

    def _refresh(self) -> None:
        if not self._ready:
            return
        self._custom.setEnabled(self._start_group.checkedId() == START_CUSTOM)
        self._from_sel.setEnabled(bool(self._span))
        self._bars.setEnabled(bool(self._span))
        if self._span:
            lo, hi = self._span
            self._sel_note.setText(tr('選取的是 %.0f~%.0f ms（%.0f ms）。'
                                      '填它有幾個小節，速度就算得出來。')
                                   % (lo, hi, float(hi) - float(lo)))
        else:
            self._sel_note.setText(tr('還沒選音符。先在譜面上選一段，'
                                      '就能用「這一段是幾小節」反推速度。'))
        item = self.plan()
        if not item.ok:
            self._summary.setText(tr('鋪不出格線：%s') % (item.reason or '不明'))
            self._ok.setEnabled(False)
            return
        lines = [tr('%d 個小節，從 %.0f ms 鋪到 %.0f ms。')
                 % (item.bars, item.start_ms, item.grid_end_ms)]
        gap = item.needed_ms - item.grid_end_ms
        if gap > 1:
            lines.append(tr('比最後一顆音符短 %.0f ms。') % gap)
        else:
            lines.append(tr('蓋得過最後一顆音符（多 %.0f ms）。') % -gap)
        # 第一顆音符會落在第幾小節第幾拍——照這個判斷 BPM 對不對最直接
        if self._first_ms and item.beats:
            bar_ms = item.segments[0].bar_ms
            offset = (self._first_ms - item.start_ms) / max(1e-6, bar_ms)
            beat = (offset - int(offset)) * max(1, self.signature()[0])
            lines.append(tr('第一顆音符落在第 %d 小節的第 %.2f 拍。')
                         % (int(offset) + 1, beat + 1))
        lines.append(tr('音符一顆都不會動。'))
        self._summary.setText('\n'.join(lines))
        self._ok.setEnabled(True)
