"""
align_time_dialog.py
====================
批次對齊起始/終止時間對話框。

把所有選取音符的 start 或 end 對齊到同一個絕對值（縮放，固定另一端）。
可從選取範圍自動填入最早/最晚的 start 或 end。
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt5.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from .i18n import t


class AlignTimeDialog(QDialog):
    """讓使用者選擇對齊目標(start/end)、輸入或自動填入絕對 ms 值。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 stats: Optional[Dict[str, int]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('dlg_align_title'))
        self.setMinimumWidth(360)

        self._stats = stats or {
            'min_start': 0, 'max_start': 0, 'min_end': 0, 'max_end': 0,
        }

        layout = QVBoxLayout(self)

        # ── 對齊目標：start / end ────────────────────────────────
        layout.addWidget(QLabel(t('dlg_align_target')))
        target_row = QHBoxLayout()
        self._rb_start = QRadioButton(t('dlg_align_target_start'))
        self._rb_end = QRadioButton(t('dlg_align_target_end'))
        self._rb_start.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._rb_start)
        grp.addButton(self._rb_end)
        target_row.addWidget(self._rb_start)
        target_row.addWidget(self._rb_end)
        target_row.addStretch()
        layout.addLayout(target_row)

        # ── 目標值 (ms) ──────────────────────────────────────────
        val_row = QHBoxLayout()
        val_row.addWidget(QLabel(t('dlg_align_value')))
        self._spin = QSpinBox()
        self._spin.setRange(0, 9_999_999)
        self._spin.setSuffix(' ms')
        self._spin.setValue(int(self._stats['min_start']))
        val_row.addWidget(self._spin)
        layout.addLayout(val_row)

        # ── 自動填入按鈕 ─────────────────────────────────────────
        layout.addWidget(QLabel(t('dlg_align_fill')))
        fill_grid = QGridLayout()
        self._add_fill_btn(fill_grid, 0, 0, t('dlg_align_fill_min_start'), 'min_start')
        self._add_fill_btn(fill_grid, 0, 1, t('dlg_align_fill_max_start'), 'max_start')
        self._add_fill_btn(fill_grid, 1, 0, t('dlg_align_fill_min_end'),   'min_end')
        self._add_fill_btn(fill_grid, 1, 1, t('dlg_align_fill_max_end'),   'max_end')
        layout.addLayout(fill_grid)

        hint = QLabel(t('dlg_align_hint'))
        hint.setStyleSheet('color: gray; font-size: 10px;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 確認 ─────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _add_fill_btn(self, grid: QGridLayout, row: int, col: int,
                      label: str, key: str) -> None:
        btn = QPushButton(f'{label}  ({int(self._stats[key])} ms)')
        btn.clicked.connect(lambda: self._spin.setValue(int(self._stats[key])))
        grid.addWidget(btn, row, col)

    # ── 結果 ─────────────────────────────────────────────────────
    def target(self) -> str:
        return 'start' if self._rb_start.isChecked() else 'end'

    def value_ms(self) -> int:
        return int(self._spin.value())
