"""新增／插入／刪除小節時問「幾個」的小對話框。

以前這三個動作各自用 `QInputDialog` 問一個數字，一次只能動一個小節：要在
開頭補 16 個空白小節就得按 16 次，而且每按一次都是獨立的一步 undo，撤回也
要按 16 次。這裡把「數量」（需要時再加上 BPM）收成一個對話框，實際操作交給
`NoteModel` 的 `count` 參數一次做完 —— 一步 undo、快取只重建一次。

刪除用的 `summary` 回呼會隨數量即時更新（會刪到哪一段、裡面有幾顆音符），
所以按下確定之前就看得到後果，不必再多一個確認框。
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QLabel, QSpinBox, QVBoxLayout)

from .i18n import t


class MeasureCountDialog(QDialog):
    """回傳要動幾個小節（`count`），必要時也回傳新小節的 BPM（`bpm`）。"""

    def __init__(self, parent, title: str, prompt: str, *,
                 count: int = 1, max_count: int = 999,
                 bpm: Optional[float] = None,
                 summary: Optional[Callable[[int], str]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._summary_fn = summary

        layout = QVBoxLayout(self)
        if prompt:
            head = QLabel(prompt)
            head.setWordWrap(True)
            layout.addWidget(head)

        form = QFormLayout()
        self._count = QSpinBox()
        self._count.setRange(1, max(1, int(max_count)))
        self._count.setValue(max(1, min(int(max_count), int(count))))
        form.addRow(t('dlg_measure_count_label'), self._count)

        self._bpm: Optional[QDoubleSpinBox] = None
        if bpm is not None:
            self._bpm = QDoubleSpinBox()
            self._bpm.setDecimals(2)
            self._bpm.setRange(10.0, 999.0)
            self._bpm.setValue(float(bpm))
            form.addRow(t('dlg_add_measure_bpm_label'), self._bpm)
        layout.addLayout(form)

        self._summary = QLabel('')
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._count.valueChanged.connect(self._refresh_summary)
        self._refresh_summary()
        # 數量是主角：一開就選好，直接打字就能改
        self._count.selectAll()
        self._count.setFocus()

    def _refresh_summary(self) -> None:
        if self._summary_fn is None:
            self._summary.setVisible(False)
            return
        try:
            text = self._summary_fn(self.count())
        except Exception:                       # noqa: BLE001
            text = ''
        self._summary.setText(text)
        self._summary.setVisible(bool(text))

    def count(self) -> int:
        return int(self._count.value())

    def bpm(self) -> Optional[float]:
        return float(self._bpm.value()) if self._bpm is not None else None
