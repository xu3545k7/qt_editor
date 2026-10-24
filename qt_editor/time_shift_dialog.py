# -*- coding: utf-8 -*-
"""「時間位移」：唯一負責「在時間上搬東西」的對話框。

為什麼要有這一個：原本同一件事散在九個入口，而且用五種說法講
（「不調整音符」「音符絕對時間不動」「保留相對位置」「吸附到新格線」
「音符、小節線一起移」），踏板要不要跟著移還藏在右鍵子選單的勾選裡。
使用者：「這些功能要設計的人性化一點，單一職責並且清楚地在介面上。」

所以這裡只回答三個問題，各佔一欄，而且**動到什麼永遠寫在畫面上**：

  移動多少（毫秒／拍／音符值）
  套用範圍（整首／選取的音符／某段小節）
  要移動什麼（音符／表情／小節線／音訊）

詞彙統一成 **音符 / 表情（踏板·強弱）/ 小節線 / 音訊** 四個，其他對話框也跟著用。

**小節線只能整條一起搬。** 只搬一段會把格線撕開，所以範圍不是「整首」的時候
那一格會變灰並說明原因——`NoteModel.shift_time` 那邊也會直接擋掉。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from .ui_text import tr

#: 位移量的單位：(顯示文字, 代號)
UNITS = (('毫秒', 'ms'), ('拍', 'beat'), ('音符值', 'value'))

#: 「音符值」的選項：(顯示文字, 幾拍)
NOTE_VALUES = (('全音符（4 拍）', 4.0), ('二分音符（2 拍）', 2.0),
               ('四分音符（1 拍）', 1.0), ('八分音符', 0.5),
               ('十六分音符', 0.25), ('三十二分音符', 0.125),
               ('附點四分音符', 1.5), ('附點八分音符', 0.75),
               ('八分三連音', 1.0 / 3.0), ('十六分三連音', 1.0 / 6.0))

SCOPE_SONG, SCOPE_SELECTION, SCOPE_MEASURES = range(3)


class TimeShiftDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], model,
                 selected_count: int = 0, has_audio: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('時間位移'))
        self.setMinimumWidth(460)
        self._model = model
        self._selected = int(selected_count)
        self._has_audio = bool(has_audio)
        # 建構途中 setValue／setCurrentIndex 會當場觸發已經接上的訊號，而
        # `_refresh` 要用的 `_summary`／`_ok` 還不存在。PyQt 的訊號處理裡丟
        # 例外不會變成 Python 的 traceback，是整個行程直接死掉（實測：連
        # log 都沒 flush 出來，檔案 0 bytes）。所以全部建好之前什麼都不做。
        self._ready = False

        root = QVBoxLayout(self)

        # ── 移動多少 ──────────────────────────────────────────────────
        amount = QGroupBox(tr('移動多少'))
        form = QFormLayout(amount)
        row = QHBoxLayout()
        self._amount = QDoubleSpinBox()
        self._amount.setRange(-600000.0, 600000.0)
        self._amount.setDecimals(0)
        self._amount.setValue(0.0)
        self._amount.setToolTip(tr('負的往前（提前）、正的往後（延後）。'))
        self._amount.valueChanged.connect(lambda _v: self._refresh())
        row.addWidget(self._amount, 1)
        self._unit = QComboBox()
        for label, key in UNITS:
            self._unit.addItem(tr(label), key)
        self._unit.currentIndexChanged.connect(lambda _i: self._sync())
        row.addWidget(self._unit)
        self._value = QComboBox()
        for label, beats in NOTE_VALUES:
            self._value.addItem(tr(label), beats)
        self._value.setCurrentIndex(2)
        self._value.currentIndexChanged.connect(lambda _i: self._refresh())
        row.addWidget(self._value)
        form.addRow(row)
        self._beat_note = QLabel()
        self._beat_note.setWordWrap(True)
        self._beat_note.setStyleSheet('color: #555; font-size: 11px;')
        form.addRow(self._beat_note)
        root.addWidget(amount)

        # ── 套用範圍 ──────────────────────────────────────────────────
        scope = QGroupBox(tr('套用範圍'))
        sbox = QVBoxLayout(scope)
        self._rb_song = QRadioButton(tr('整首'))
        self._rb_sel = QRadioButton(tr('選取的 %d 顆音符') % self._selected)
        self._rb_meas = QRadioButton(tr('小節範圍'))
        self._scope = QButtonGroup(self)
        for i, rb in ((SCOPE_SONG, self._rb_song), (SCOPE_SELECTION, self._rb_sel),
                      (SCOPE_MEASURES, self._rb_meas)):
            self._scope.addButton(rb, i)
            sbox.addWidget(rb)
            rb.toggled.connect(lambda _on: self._sync())
        self._rb_sel.setEnabled(self._selected > 0)
        (self._rb_sel if self._selected else self._rb_song).setChecked(True)

        mrow = QHBoxLayout()
        mrow.addSpacing(24)
        mrow.addWidget(QLabel(tr('第')))
        total = max(1, int(model.count_measures()))
        self._first = QSpinBox()
        self._first.setRange(1, total)
        self._last = QSpinBox()
        self._last.setRange(1, total)
        self._last.setValue(total)
        self._first.valueChanged.connect(lambda v: (self._last.setMinimum(v),
                                                    self._refresh()))
        self._last.valueChanged.connect(lambda _v: self._refresh())
        mrow.addWidget(self._first)
        mrow.addWidget(QLabel(tr('到第')))
        mrow.addWidget(self._last)
        mrow.addWidget(QLabel(tr('小節')))
        mrow.addStretch(1)
        sbox.addLayout(mrow)
        root.addWidget(scope)

        # ── 要移動什麼 ────────────────────────────────────────────────
        layers = QGroupBox(tr('要移動什麼'))
        lbox = QVBoxLayout(layers)
        self._notes = QCheckBox(tr('音符'))
        self._notes.setChecked(True)
        self._expression = QCheckBox(tr('表情（踏板、強弱記號）'))
        self._expression.setChecked(True)
        self._expression.setToolTip(tr(
            '被推到開頭之前的踏板會裁掉：一半在外面的切短，整段在外面的消失。'))
        self._grid = QCheckBox(tr('小節線'))
        self._grid.setToolTip(tr(
            '小節線只能整條一起搬——只搬一段會把格線撕開。'))
        self._audio = QCheckBox(tr('音訊（加／減開頭的靜音）'))
        self._audio.setToolTip(tr('沒有載入音檔時不能選。'))
        for chk in (self._notes, self._expression, self._grid, self._audio):
            chk.toggled.connect(lambda _on: self._refresh())
            lbox.addWidget(chk)
        self._layer_note = QLabel()
        self._layer_note.setWordWrap(True)
        self._layer_note.setStyleSheet('color: #555; font-size: 11px;')
        lbox.addWidget(self._layer_note)
        root.addWidget(layers)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.PlainText)
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ready = True
        self._sync()

    # ── 狀態 ──────────────────────────────────────────────────────────

    def scope(self) -> int:
        return int(self._scope.checkedId())

    def unit(self) -> str:
        return str(self._unit.currentData())

    def beats(self) -> float:
        """「拍」或「音符值」模式下，位移量換算成幾拍。"""
        if self.unit() == 'beat':
            return float(self._amount.value())
        if self.unit() == 'value':
            return float(self._amount.value()) * float(self._value.currentData())
        return 0.0

    def delta_ms(self) -> int:
        """毫秒模式下的位移量（其他模式回 0，量要照位置換算）。"""
        return int(self._amount.value()) if self.unit() == 'ms' else 0

    def layers(self) -> dict:
        return {'notes': self._notes.isChecked(),
                'expression': self._expression.isChecked(),
                'grid': self._grid.isChecked() and self._grid.isEnabled(),
                'audio': self._audio.isChecked() and self._audio.isEnabled()}

    def measure_range(self):
        return int(self._first.value()), int(self._last.value())

    def range_ms(self):
        """`(lo, hi)`：範圍是整首或選取時回 `(None, None)`（呼叫端自己處理選取）。"""
        if self.scope() != SCOPE_MEASURES:
            return None, None
        first, last = self.measure_range()
        lo, _e = self._model.get_measure_time_range(first - 1)
        _s, hi = self._model.get_measure_time_range(last - 1)
        return lo, hi

    # ── 畫面 ──────────────────────────────────────────────────────────

    def _sync(self) -> None:
        """單位、範圍、層之間的相依：只在這裡決定什麼能按。"""
        if not self._ready:
            return
        unit = self.unit()
        self._value.setVisible(unit == 'value')
        self._amount.setSuffix(' ms' if unit == 'ms' else '')
        self._beat_note.setVisible(unit != 'ms')
        if unit != 'ms':
            self._beat_note.setText(tr(
                '拍長照每個東西**自己所在位置**的 BPM 換算，變速譜上也精準。'))

        whole = self.scope() == SCOPE_SONG
        # 小節線只能整條一起搬：範圍不是整首就不給選，並且說出原因
        self._grid.setEnabled(whole)
        self._audio.setEnabled(whole and self._has_audio)
        if not whole and self._grid.isChecked():
            self._grid.setChecked(False)
        if not self._audio.isEnabled() and self._audio.isChecked():
            self._audio.setChecked(False)
        notes = []
        if not whole:
            notes.append(tr('小節線只能整條一起搬，所以範圍不是「整首」時不能選。'))
        if not self._has_audio:
            notes.append(tr('沒有載入音檔，所以不能移動音訊。'))
        self._layer_note.setText('\n'.join(notes))
        self._layer_note.setVisible(bool(notes))

        for widget in (self._first, self._last):
            widget.setEnabled(self.scope() == SCOPE_MEASURES)
        self._refresh()

    def _refresh(self) -> None:
        """摘要：這一次按下去會發生什麼。"""
        if not self._ready:
            return
        picked = [name for name, on in
                  ((tr('音符'), self._notes.isChecked()),
                   (tr('表情'), self._expression.isChecked()),
                   (tr('小節線'), self.layers()['grid']),
                   (tr('音訊'), self.layers()['audio'])) if on]
        amount = float(self._amount.value())
        if not picked:
            self._summary.setText(tr('沒有選要移動什麼。'))
            self._ok.setEnabled(False)
            return
        if not amount:
            self._summary.setText(tr('位移量是 0，按確定不會有任何改變。'))
            self._ok.setEnabled(False)
            return
        where = {SCOPE_SONG: tr('整首'),
                 SCOPE_SELECTION: tr('選取的 %d 顆音符') % self._selected,
                 SCOPE_MEASURES: tr('第 %d 到第 %d 小節')
                 % self.measure_range()}[self.scope()]
        if self.unit() == 'ms':
            how = tr('%+d ms') % int(amount)
        else:
            how = tr('%+.4g 拍') % self.beats()
        lines = [tr('%s：%s 往%s移 %s。')
                 % (where, '、'.join(picked),
                    tr('後') if amount > 0 else tr('前'), how.lstrip('+-'))]
        if amount < 0 and self._expression.isChecked():
            lines.append(tr('被推到開頭之前的踏板會裁掉。'))
        if self.layers()['audio']:
            lines.append(tr('音訊會跟著加／減開頭的靜音，才不會和譜面錯開。'))
        self._summary.setText('\n'.join(lines))
        self._ok.setEnabled(True)
