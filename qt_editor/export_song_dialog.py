"""
export_song_dialog.py
=====================
「儲存為完整曲目格式」對話框。
- 新曲目模式：使用者填入曲名、作者、難度名稱、定數、選曲繪，
  匯出到 songs/<曲名>/ 下。
- 追加難度模式：選擇資料夾（母資料夾會掃描子曲目讓使用者挑；
  若直接選到含 register.json 的子資料夾則直接帶入），自動讀取
  register.json 並預填曲名/作者/曲繪等，只需填新的難度名稱與定數。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QInputDialog,
)

from .i18n import t

_base_dir = None
if getattr(sys, 'frozen', False):
    # When packaged by PyInstaller, __file__ points into the temp extraction
    # directory. Prefer the current working directory (the folder the EXE
    # was started from) as the project root so exported files land in the
    # real workspace rather than the temp dir.
    _base_dir = os.getcwd()
else:
    _base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))

SONGS_ROOT = os.path.normpath(
    os.path.join(_base_dir, 'UnityProject', 'nos_clone', 'Assets', 'Resources', 'songs')
)


def _scan_songs_in_folder(folder: str) -> list[tuple[str, str]]:
    """掃描 folder 底下所有含 register.json 的子資料夾。
    回傳 [(display_text, full_path), ...]。"""
    results: list[tuple[str, str]] = []
    if not os.path.isdir(folder):
        return results
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, 'register.json')):
            results.append((name, full))
    return results


def _is_song_folder(folder: str) -> bool:
    """判斷 folder 本身是否為有效曲目資料夾（含 register.json）。"""
    return os.path.isfile(os.path.join(folder, 'register.json'))


def _resolve_cover_from_register(data: dict, song_folder: str) -> str:
    """從 register.json 的 coverResourcePath 解析出實際曲繪檔案路徑。"""
    diffs = data.get('difficulties', [])
    if not diffs:
        return ''
    cover_res = diffs[0].get('coverResourcePath', '')
    if not cover_res:
        return ''
    # coverResourcePath 格式: "songs/<folder>/<name>"（無副檔名）
    # Resources 根目錄 = SONGS_ROOT/../..
    resources_root = os.path.normpath(os.path.join(SONGS_ROOT, os.pardir, os.pardir))
    for ext in ('.png', '.jpg', '.jpeg'):
        cand = os.path.normpath(os.path.join(resources_root, cover_res + ext))
        if os.path.isfile(cand):
            return cand
    # fallback: 直接在曲目資料夾裡找同名圖片
    display = data.get('displayName', '')
    if display:
        for ext in ('.png', '.jpg', '.jpeg'):
            cand = os.path.join(song_folder, display + ext)
            if os.path.isfile(cand):
                return cand
    return ''


class ExportSongDialog(QDialog):
    """完整曲目匯出對話框。"""

    def __init__(self, parent=None, *,
                 offset_ms: int = 0,
                 wav_path: str = '',
                 chart_json_path: str = ''):
        super().__init__(parent)
        self.setWindowTitle(t('dlg_export_title'))
        self.setMinimumWidth(520)
        self._offset_ms = offset_ms
        self._wav_path = wav_path
        self._chart_json_path = chart_json_path

        self._existing_register: Optional[dict] = None
        self._selected_song_folder: str = ''   # 追加模式選中的曲目資料夾完整路徑

        main_layout = QVBoxLayout(self)

        # ── 追加模式選項 ──────────────────────────────────────────
        mode_row = QHBoxLayout()
        self._chk_append = QCheckBox(t('dlg_export_append'))
        self._chk_append.toggled.connect(self._on_append_toggled)
        mode_row.addWidget(self._chk_append)

        self._btn_browse_folder = QPushButton(t('dlg_export_browse_folder'))
        self._btn_browse_folder.setEnabled(False)
        self._btn_browse_folder.clicked.connect(self._browse_song_folder)
        mode_row.addWidget(self._btn_browse_folder)

        mode_row.addStretch()
        main_layout.addLayout(mode_row)

        # 顯示已選曲目
        self._lbl_selected_song = QLabel('')
        self._lbl_selected_song.setStyleSheet('color: #2266aa; font-weight: bold;')
        main_layout.addWidget(self._lbl_selected_song)

        # ── 表單欄位 ──────────────────────────────────────────────
        form = QFormLayout()

        self._le_display = QLineEdit()
        form.addRow(t('dlg_export_display_name'), self._le_display)

        self._le_author = QLineEdit()
        form.addRow(t('dlg_export_author'), self._le_author)

        self._le_diff_name = QLineEdit()
        form.addRow(t('dlg_export_diff_name'), self._le_diff_name)

        self._spin_level = QSpinBox()
        self._spin_level.setRange(1, 99)
        self._spin_level.setValue(10)
        form.addRow(t('dlg_export_diff_level'), self._spin_level)

        # 曲繪
        cover_row = QHBoxLayout()
        self._le_cover = QLineEdit()
        self._le_cover.setReadOnly(True)
        cover_row.addWidget(self._le_cover)
        self._btn_cover = QPushButton(t('dlg_export_browse'))
        self._btn_cover.clicked.connect(self._browse_cover)
        cover_row.addWidget(self._btn_cover)
        form.addRow(t('dlg_export_cover'), cover_row)

        # 音源提示
        offset_text = ''
        if offset_ms > 0:
            offset_text = t('dlg_export_audio_hint_adv', offset_ms)
        elif offset_ms < 0:
            offset_text = t('dlg_export_audio_hint_delay', abs(offset_ms))
        else:
            offset_text = t('dlg_export_audio_hint_none')
        self._lbl_audio = QLabel(offset_text)
        form.addRow(t('dlg_export_audio'), self._lbl_audio)

        main_layout.addLayout(form)

        # ── 按鈕 ──────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self._validate_and_accept)
        bbox.rejected.connect(self.reject)
        main_layout.addWidget(bbox)

    # ── 事件處理 ──────────────────────────────────────────────────
    def _on_append_toggled(self, checked: bool):
        self._btn_browse_folder.setEnabled(checked)
        if not checked:
            self._existing_register = None
            self._selected_song_folder = ''
            self._lbl_selected_song.setText('')
            self._le_display.setReadOnly(False)
            self._le_display.clear()
            self._le_author.setReadOnly(False)
            self._le_author.clear()
            self._le_cover.clear()
            self._btn_cover.setEnabled(True)

    def _browse_song_folder(self):
        """讓使用者選擇資料夾，自動判斷是母資料夾或子資料夾。"""
        # 預設開啟 SONGS_ROOT（若存在）
        start_dir = SONGS_ROOT if os.path.isdir(SONGS_ROOT) else ''
        folder = QFileDialog.getExistingDirectory(
            self, t('dlg_export_pick_folder'), start_dir)
        if not folder:
            return

        if _is_song_folder(folder):
            # 直接選到含 register.json 的子資料夾 → 直接帶入
            self._apply_song_folder(folder)
        else:
            # 當作母資料夾掃描
            songs = _scan_songs_in_folder(folder)
            if not songs:
                QMessageBox.information(
                    self, t('dlg_export_select_song'), t('dlg_export_no_songs'))
                return
            # 彈出選擇清單
            names = [s[0] for s in songs]
            chosen, ok = QInputDialog.getItem(
                self,
                t('dlg_export_select_song'),
                t('dlg_export_found_songs', len(songs)),
                names, 0, False,
            )
            if not ok or not chosen:
                return
            # 找到對應的完整路徑
            for name, full_path in songs:
                if name == chosen:
                    self._apply_song_folder(full_path)
                    break

    def _apply_song_folder(self, song_folder: str):
        """讀取 song_folder 內的 register.json 並帶入所有欄位。"""
        reg_path = os.path.join(song_folder, 'register.json')
        if not os.path.isfile(reg_path):
            return
        try:
            with open(reg_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        self._existing_register = data
        self._selected_song_folder = song_folder
        folder_name = os.path.basename(song_folder)
        self._lbl_selected_song.setText(f'📁 {folder_name}')

        # 帶入曲名、作者
        self._le_display.setText(data.get('displayName', folder_name))
        self._le_display.setReadOnly(True)
        self._le_author.setText(data.get('author', ''))
        self._le_author.setReadOnly(True)

        # 帶入曲繪（從 register 自動解析）
        cover = _resolve_cover_from_register(data, song_folder)
        if cover:
            self._le_cover.setText(cover + '  ' + t('dlg_export_cover_auto'))
            self._btn_cover.setEnabled(False)   # 自動帶入時不需要手動選
        else:
            self._le_cover.clear()
            self._btn_cover.setEnabled(True)    # 找不到才讓使用者自己選

    def _browse_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_export_cover_pick'), '',
            'Images (*.png *.jpg *.jpeg);;All Files (*)',
        )
        if path:
            self._le_cover.setText(path)

    def _validate_and_accept(self):
        if not self._le_display.text().strip():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_err_no_name'))
            return
        if not self._le_diff_name.text().strip():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_err_no_diff'))
            return
        self.accept()

    # ── 結果 getter ───────────────────────────────────────────────
    def display_name(self) -> str:
        return self._le_display.text().strip()

    def author(self) -> str:
        return self._le_author.text().strip()

    def diff_name(self) -> str:
        return self._le_diff_name.text().strip()

    def diff_level(self) -> int:
        return self._spin_level.value()

    def cover_path(self) -> str:
        """回傳曲繪路徑（去掉「自動帶入」提示文字）。"""
        raw = self._le_cover.text().strip()
        # 移除尾端的 i18n 提示（若有）
        auto_hint = t('dlg_export_cover_auto')
        if raw.endswith(auto_hint):
            raw = raw[:-len(auto_hint)].strip()
        return raw

    def is_append_mode(self) -> bool:
        return self._chk_append.isChecked()

    def append_folder(self) -> str:
        """回傳追加模式下選定的曲目資料夾名稱（僅 basename）。"""
        if self._selected_song_folder:
            return os.path.basename(self._selected_song_folder)
        return ''

    def append_folder_full(self) -> str:
        """回傳追加模式下選定的曲目資料夾完整路徑。"""
        return self._selected_song_folder

    def existing_register(self) -> Optional[dict]:
        return self._existing_register
