"""
new_chart_dialog.py
===================
新增譜面對話框：輸入曲名、起始 BPM、拍號分子與曲子長度。
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QLineEdit, QMessageBox, QSpinBox,
)


class NewChartDialog(QDialog):
    """讓使用者輸入新譜面基本參數的對話框。"""

    def __init__(
        self,
        parent=None,
        default_bpm: float = 120.0,
        default_duration: int = 180,
        default_beats: int = 4,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle('新增譜面')
        self.setMinimumWidth(340)

        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # ── 曲名 ──────────────────────────────────────────────────────
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText('例：My Song')
        layout.addRow('曲名（檔案名稱）：', self._name_edit)

        # ── 起始 BPM ──────────────────────────────────────────────────
        self._bpm_spin = QDoubleSpinBox()
        self._bpm_spin.setRange(10.0, 999.0)
        self._bpm_spin.setDecimals(2)
        self._bpm_spin.setValue(default_bpm)
        self._bpm_spin.setSingleStep(1.0)
        layout.addRow('起始 BPM：', self._bpm_spin)

        # ── 每小節拍數 ────────────────────────────────────────────────
        self._beats_spin = QSpinBox()
        self._beats_spin.setRange(1, 32)
        self._beats_spin.setValue(default_beats)
        layout.addRow('每小節拍數（拍號分子）：', self._beats_spin)

        # ── 曲子長度 ──────────────────────────────────────────────────
        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(5, 7200)
        self._duration_spin.setValue(default_duration)
        self._duration_spin.setSuffix(' 秒')
        layout.addRow('曲子長度：', self._duration_spin)

        # ── 提示 ──────────────────────────────────────────────────────
        hint = QLabel('（長度僅決定初始拍子數，之後可以新增/刪除小節）')
        hint.setStyleSheet('color: gray; font-size: 11px;')
        layout.addRow(hint)

        # ── 按鈕 ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText('確定')
        btns.button(QDialogButtonBox.Cancel).setText('取消')
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, '錯誤', '請輸入曲名。')
            self._name_edit.setFocus()
            return
        self.accept()

    # ------------------------------------------------------------------
    # 屬性
    # ------------------------------------------------------------------

    @property
    def song_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def bpm(self) -> float:
        return self._bpm_spin.value()

    @property
    def beats_per_bar(self) -> int:
        return self._beats_spin.value()

    @property
    def duration_sec(self) -> int:
        return self._duration_spin.value()
