"""
settings_dialog.py
==================
偏好設定對話框。
- 語言選擇：繁體中文 / 簡體中文 / English
- 滾輪方向：正向 / 反向
"""

from __future__ import annotations

import os
import sys

from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QVBoxLayout, QWidget,
)

from PyQt5.QtWidgets import QCheckBox

from .i18n import t
from .settings import settings

# 語言選項：(顯示名稱, 代碼)
_LANG_OPTIONS = [
    ('繁體中文', 'zh_tw'),
    ('简体中文', 'zh_cn'),
    ('English',  'en'),
]


class SettingsDialog(QDialog):
    """偏好設定對話框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('settings_title'))
        self.setMinimumWidth(320)

        self._original_lang = settings.get('language', 'zh_tw')

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        # ── 語言 ──────────────────────────────────────────────────────
        self._lang_combo = QComboBox()
        current_lang = self._original_lang
        for display, code in _LANG_OPTIONS:
            self._lang_combo.addItem(display, code)
        # 選中目前語言
        for i, (_, code) in enumerate(_LANG_OPTIONS):
            if code == current_lang:
                self._lang_combo.setCurrentIndex(i)
                break
        form.addRow(QLabel(t('settings_language')), self._lang_combo)

        # ── 滾輪方向 ──────────────────────────────────────────────────
        self._scroll_combo = QComboBox()
        self._scroll_combo.addItem(t('settings_normal'),   False)
        self._scroll_combo.addItem(t('settings_reversed'), True)
        scroll_invert = bool(settings.get('scroll_invert', False))
        self._scroll_combo.setCurrentIndex(1 if scroll_invert else 0)
        form.addRow(QLabel(t('settings_scroll_dir')), self._scroll_combo)

        # ── 語言更改提示 ──────────────────────────────────────────────
        self._note_label = QLabel(t('settings_restart_note'))
        self._note_label.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(self._note_label)

        # ── 匯出音訊自動處理 ──────────────────────────────────────────
        self._audio_auto_chk = QCheckBox('匯出時自動處理音訊（解析檔名偏移並裁切/補零）')
        audio_auto = bool(settings.get('export_auto_process_audio', True))
        self._audio_auto_chk.setChecked(audio_auto)
        form.addRow(QLabel('匯出音訊處理'), self._audio_auto_chk)

        # ── 按鈕 ──────────────────────────────────────────────────────
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _on_accept(self) -> None:
        lang_code = self._lang_combo.currentData()
        scroll_inv = self._scroll_combo.currentData()
        settings.set('language',      lang_code)
        settings.set('scroll_invert', scroll_inv)
        # Save export audio processing setting
        settings.set('export_auto_process_audio', bool(self._audio_auto_chk.isChecked()))
        self.accept()

        if lang_code != self._original_lang:
            # 語言已變更，重啟應用程式
            QApplication.instance().quit()
            os.execv(sys.executable, [sys.executable, '-m', 'qt_editor.app'])
