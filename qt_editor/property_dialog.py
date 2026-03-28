"""
property_dialog.py
==================
音符屬性編輯對話框（取代原 tkinter Toplevel）。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QVBoxLayout, QWidget,
)

from .models import GNote
from .i18n import t


class NotePropertyDialog(QDialog):
    """單一音符屬性編輯對話框。

    用法
    ----
    dlg = NotePropertyDialog(parent, note, beat_ms=beat_ms)
    if dlg.exec_() == QDialog.Accepted:
        # note 的欄位已原地更新
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        note: GNote,
        beat_ms: float = 500.0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('prop_title', note.idx))
        self._note    = note
        self._beat_ms = max(1.0, beat_ms)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self._fields: dict[str, QLineEdit] = {}

        def add(label: str, val) -> QLineEdit:
            le = QLineEdit('' if val is None else str(val))
            form.addRow(QLabel(label), le)
            self._fields[label] = le
            return le

        add('start (ms)',    note.start)

        # ── length：ms 與 拍數 雙向即時同步 ─────────────────────
        init_len_ms    = note.end - note.start
        init_len_beats = init_len_ms / self._beat_ms

        ms_le    = add('length (ms)',  init_len_ms)
        beats_le = add('時值（拍）',       f'{init_len_beats:.1f}')

        self._syncing = False

        def _ms_edited(text: str) -> None:
            if self._syncing:
                return
            self._syncing = True
            try:
                beats_le.setText(f'{float(text) / self._beat_ms:.1f}')
            except ValueError:
                pass
            finally:
                self._syncing = False

        def _beats_edited(text: str) -> None:
            if self._syncing:
                return
            self._syncing = True
            try:
                ms_le.setText(str(int(round(float(text) * self._beat_ms))))
            except ValueError:
                pass
            finally:
                self._syncing = False

        ms_le.textEdited.connect(_ms_edited)
        beats_le.textEdited.connect(_beats_edited)
        # ───────────────────────────────────────────────────────────────
        add('min_key',       note.min_key)
        add('width',         note.max_key - note.min_key + 1)
        add('note_type',     note.note_type)  # 0=tap 1=soft 2=long 3=staccato
        add('hand',          note.hand)       # 0=右 1=左
        add('track',         note.track)
        add('pitch',         note.pitch)

        note_type_hint = QLabel('note_type: 0=tap  1=soft  2=long  3=staccato')
        note_type_hint.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(note_type_hint)

        hand_hint = QLabel(t('prop_hand_hint'))
        hand_hint.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(hand_hint)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        self.setMinimumWidth(340)

    def _on_accept(self) -> None:
        n = self._note
        try:
            n.start    = int(self._fields['start (ms)'].text())
            length     = int(self._fields['length (ms)'].text())
            n.end      = n.start + max(0, length)
            n.min_key  = int(self._fields['min_key'].text())
            width      = int(self._fields['width'].text())
            n.max_key  = n.min_key + max(0, width - 1)
            n.note_type = int(self._fields['note_type'].text())
            n.hand     = int(self._fields['hand'].text())

            track_txt  = self._fields['track'].text().strip()
            n.track    = int(track_txt) if track_txt else None

            pitch_txt  = self._fields['pitch'].text().strip()
            n.pitch    = int(pitch_txt) if pitch_txt else None

            n.gate     = max(0, n.end - n.start)
        except ValueError as e:
            QMessageBox.critical(self, t('prop_err_title'), t('prop_err_msg', e))
            return

        self.accept()
