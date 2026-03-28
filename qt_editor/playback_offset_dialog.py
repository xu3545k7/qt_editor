"""
playback_offset_dialog.py
=========================
播放延遲/提前設定對話框。
提前 = 音訊起始位置往前（正 offset → start_ms 減少）
延後 = 音訊起始位置往後（正 offset → start_ms 增加）
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from .i18n import t


class PlaybackOffsetDialog(QDialog):
    """讓使用者以 ms 或拍數設定播放提前/延後偏移。"""

    def __init__(self, parent=None, bpm: float = 120.0,
                 current_ms: int = 0, is_advance: bool = True):
        super().__init__(parent)
        self.setWindowTitle(t('dlg_pb_offset_title'))
        self.setMinimumWidth(340)

        self._bpm = bpm if bpm > 0 else 120.0
        self._is_advance = is_advance  # True = 提前, False = 延後
        self._syncing = False

        layout = QVBoxLayout(self)

        # ── 方向切換按鈕 ──────────────────────────────────────────
        dir_row = QHBoxLayout()
        self._btn_dir = QPushButton()
        self._btn_dir.setFixedWidth(100)
        self._btn_dir.clicked.connect(self._toggle_direction)
        dir_row.addWidget(QLabel(t('dlg_pb_offset_dir')))
        dir_row.addWidget(self._btn_dir)
        dir_row.addStretch()
        layout.addLayout(dir_row)

        # ── 時間 (ms) ────────────────────────────────────────────
        ms_row = QHBoxLayout()
        ms_row.addWidget(QLabel(t('dlg_pb_offset_ms')))
        self._spin_ms = QSpinBox()
        self._spin_ms.setRange(0, 999_999)
        self._spin_ms.setSuffix(' ms')
        self._spin_ms.setValue(abs(current_ms))
        self._spin_ms.valueChanged.connect(self._on_ms_changed)
        ms_row.addWidget(self._spin_ms)
        layout.addLayout(ms_row)

        # ── 拍數 ──────────────────────────────────────────────────
        beat_row = QHBoxLayout()
        beat_row.addWidget(QLabel(t('dlg_pb_offset_beat')))
        self._spin_beat = QDoubleSpinBox()
        self._spin_beat.setRange(0.0, 9999.0)
        self._spin_beat.setDecimals(1)
        self._spin_beat.setSingleStep(0.5)
        self._spin_beat.setSuffix(t('dlg_pb_offset_beat_unit'))
        ms_per_beat = 60_000.0 / self._bpm
        self._spin_beat.setValue(abs(current_ms) / ms_per_beat)
        self._spin_beat.valueChanged.connect(self._on_beat_changed)
        beat_row.addWidget(self._spin_beat)
        layout.addLayout(beat_row)

        # ── 確認 ──────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

        self._update_dir_btn()

    # ── helpers ───────────────────────────────────────────────────
    def _update_dir_btn(self):
        self._btn_dir.setText(
            t('dlg_pb_offset_advance') if self._is_advance
            else t('dlg_pb_offset_delay')
        )

    def _toggle_direction(self):
        self._is_advance = not self._is_advance
        self._update_dir_btn()

    def _on_ms_changed(self, val: int):
        if self._syncing:
            return
        self._syncing = True
        ms_per_beat = 60_000.0 / self._bpm
        self._spin_beat.setValue(val / ms_per_beat)
        self._syncing = False

    def _on_beat_changed(self, val: float):
        if self._syncing:
            return
        self._syncing = True
        ms_per_beat = 60_000.0 / self._bpm
        self._spin_ms.setValue(int(round(val * ms_per_beat)))
        self._syncing = False

    # ── 結果 ──────────────────────────────────────────────────────
    def offset_ms(self) -> int:
        """回傳帶正負號的偏移量。 正 = 提前，負 = 延後。"""
        v = self._spin_ms.value()
        return v if self._is_advance else -v

    def is_advance(self) -> bool:
        return self._is_advance
