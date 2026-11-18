# -*- coding: utf-8 -*-
"""「照前面的小節排後面」的設定框。

只有一個真正的輸入：**你排到第幾小節了**。它前面的音符就是參考，它和它後面
的都會被處理。其餘三個勾選是細節，預設值照實測調好（見 `arrange_by_example`
的模組說明），平常不用動。

試算是同步跑的：182 小節 4848 顆的譜實測 0.6 秒，所以改完參數等一下就會更新，
不用按按鈕。改得快的時候靠 `QTimer` 壓一下，不要每敲一個數字就算一次。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                             QGroupBox, QLabel, QSpinBox, QVBoxLayout, QWidget)

from . import arrange_by_example as ABE
from .ui_text import tr


class ArrangeByExampleDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], model,
                 current_measure: int = 1) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('照前面的小節排後面'))
        self.setMinimumWidth(520)
        self._model = model
        self._plan: Optional[ABE.Plan] = None
        self._ready = False

        root = QVBoxLayout(self)
        lead = QLabel(tr(
            '把你**已經排好**的小節當範本：工具會拿它們去比後面每一個小節，'
            '對得上的音符照抄鍵道與左右手，對不上的則照前面的排法重新擺。\n'
            '時間、音高、音符類型都不會動，只動鍵道（和左右手）。'))
        lead.setWordWrap(True)
        root.addWidget(lead)

        box = QGroupBox(tr('範圍'))
        form = QFormLayout(box)
        total = max(1, int(model.count_measures() or 1))
        self._from = QSpinBox()
        # 最少從第 2 小節開始：第 1 小節前面沒有東西可以當參考。
        self._from.setRange(2, max(2, total))
        self._from.setValue(max(2, min(int(current_measure), total)))
        self._from.setSuffix(tr(' 小節'))
        self._from.setToolTip(tr('你排到第幾小節了。**這個小節自己也會被重排**，'
                                 '參考的是它前面那些。'))
        form.addRow(tr('從第幾小節開始排'), self._from)

        self._threshold = QSpinBox()
        self._threshold.setRange(10, 100)
        self._threshold.setValue(int(round(ABE.DEFAULT_THRESHOLD * 100)))
        self._threshold.setSuffix(' %')
        self._threshold.setToolTip(tr(
            '一個小節要有這麼多比例的音符對得上，才值得照抄。\n'
            '實測真正的重複落在 50~80%，不是重複的幾乎都在 30% 以下。'))
        form.addRow(tr('匹配率門檻'), self._threshold)
        root.addWidget(box)

        opts = QGroupBox(tr('細節'))
        obox = QVBoxLayout(opts)
        self._remap = QCheckBox(tr('匹配率太低的也重新排（遷就前面的排法）'))
        self._remap.setChecked(True)
        self._remap.setToolTip(tr(
            '關掉的話，對不上的小節就整個留在原地不動。\n'
            '打開時會從參考段落＋剛照抄好的部分量出「音高 → 鍵道」，用它來擺。'))
        self._hand = QCheckBox(tr('連左右手一起套用'))
        self._hand.setChecked(True)
        self._hand.setToolTip(tr(
            '照抄的音符也把參考那顆的左右手帶過來。\n'
            'AI 轉譜的分手是猜的，前面手改好的分手可以靠這個傳到後面。'))
        self._melody = QCheckBox(tr('冠音帶頭：其他音符照冠音的走法排'))
        self._melody.setChecked(True)
        self._melody.setToolTip(tr(
            '先決定冠音要去哪（上一個冠音 ＋ 這個音程該走幾格），同一組其他的'
            '音符整組跟著走。不開的話每顆各自查「這個音高上次放在哪」，'
            '同一句裡音高往上走、鍵道卻停在原地是常態。\n'
            '實測 20 份譜 10424 顆：落在你原本位置的 ±1 格 64.6% → 68.6%、'
            '守方向 82.5% → 90.5%，重疊沒變多。只動重排的那些，照抄的不碰。'))
        self._shift = QCheckBox(tr('移調的段落把鍵道一起平移'))
        self._shift.setChecked(True)
        self._shift.setToolTip(tr(
            '同一個樂句整段移了調時，照抄的鍵道也跟著往旁邊移。\n'
            '實測：移調的段落不平移只有 19% 落在手排的位置，平移後 23%；'
            '±1 格 63% → 67%。'))
        for cb in (self._remap, self._hand, self._melody, self._shift):
            obox.addWidget(cb)
            cb.toggled.connect(lambda _on: self._schedule())
        root.addWidget(opts)

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

        for spin in (self._from, self._threshold):
            spin.valueChanged.connect(lambda _v: self._schedule())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh)
        self._ready = True
        self._refresh()

    # ── 取值 ──────────────────────────────────────────────────────────

    def from_measure(self) -> int:
        return int(self._from.value())

    def threshold(self) -> float:
        return float(self._threshold.value()) / 100.0

    def options(self) -> dict:
        return {'copy_hand': self._hand.isChecked(),
                'keep_melody': self._melody.isChecked(),
                'shift_transposed': self._shift.isChecked()}

    def plan(self) -> Optional[ABE.Plan]:
        """最後一次試算的結果。按下確定之後呼叫端直接拿它去 `apply`。"""
        return self._plan

    # ── 畫面 ──────────────────────────────────────────────────────────

    def _schedule(self) -> None:
        if self._ready:
            self._timer.start()

    def _refresh(self) -> None:
        if not self._ready:
            return
        self._plan = ABE.plan(self._model, self.from_measure(),
                              threshold=self.threshold(),
                              remap_low=self._remap.isChecked())
        prepared = self._plan
        if not prepared.ok:
            self._summary.setText(prepared.reason or tr('沒有可以處理的小節'))
            self._ok.setEnabled(False)
            return
        copied = prepared.copied
        remapped = prepared.of(ABE.ACTION_REMAP)
        lines = [tr('參考第 1~%d 小節的 %d 顆音符。')
                 % (prepared.from_measure - 1, prepared.reference_notes)]
        lines.append(tr('後面 %d 個有音符的小節：%d 個照抄（平均匹配率 %.0f%%，'
                        '共 %d 顆），%d 個重新排（共 %d 顆）。')
                     % (len(prepared.bars), len(copied),
                        prepared.mean_coverage * 100, prepared.notes_to_copy,
                        len(remapped), prepared.notes_to_remap))
        if prepared.transposed:
            lines.append(tr('其中 %d 個小節是移調的重複。') % len(prepared.transposed))
        if not copied and not prepared.notes_to_remap:
            lines.append(tr('沒有任何小節對得上，也沒有要重排的——換個門檻看看。'))
        lines.append(tr('只動鍵道與左右手，可以復原（Ctrl+Z）。'))
        self._summary.setText('\n'.join(lines))
        self._ok.setEnabled(bool(copied) or bool(prepared.notes_to_remap))
