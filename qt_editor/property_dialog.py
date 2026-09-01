"""
Simple note property editor dialog.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .i18n import t
from .models import GNote, TOTAL_GAME_KEYS, lane_from_external, lane_to_external


class NotePropertyDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget],
        note: GNote,
        beat_ms: float = 500.0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('prop_title', note.idx))
        self._note = note
        self._edited = note.clone(note.idx)
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

        add('start (ms)', self._edited.start)

        init_len_ms = self._edited.end - self._edited.start
        init_len_beats = init_len_ms / self._beat_ms

        ms_le = add('length (ms)', init_len_ms)
        beats_le = add('length (beats)', f'{init_len_beats:.1f}')

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

        add('min_key', lane_to_external(self._edited.min_key))
        add('width', self._edited.max_key - self._edited.min_key + 1)
        add('note_type', self._edited.note_type)
        add('hand', self._edited.hand)
        add('param1 (slide prev)', self._edited.param1)
        add('param2 (slide next)', self._edited.param2)
        add('track', self._edited.track)
        add('pitch', self._edited.pitch)
        add('velocity', self._edited.velocity)
        add('channel', self._edited.channel)
        add('off_velocity', self._edited.off_velocity)

        note_type_hint = QLabel('note_type(bitmask): 0=tap 2=long 4=slide 64=trill  (+8=black skin)')
        note_type_hint.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(note_type_hint)

        hand_hint = QLabel(t('prop_hand_hint'))
        hand_hint.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(hand_hint)

        midi_hint = QLabel('MIDI: track/channel are 0-based, velocity range is 0-127.')
        midi_hint.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(midi_hint)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        self.setMinimumWidth(360)

    def _on_accept(self) -> None:
        n = self._edited
        try:
            n.start = int(self._fields['start (ms)'].text())
            length = int(self._fields['length (ms)'].text())
            n.end = n.start + max(0, length)

            min_key_external = int(self._fields['min_key'].text())
            n.min_key = lane_from_external(min_key_external)
            width = int(self._fields['width'].text())
            n.max_key = n.min_key + max(0, width - 1)
            n.min_key = max(0, min(TOTAL_GAME_KEYS - 1, n.min_key))
            n.max_key = max(n.min_key, min(TOTAL_GAME_KEYS - 1, n.max_key))

            n.note_type = int(self._fields['note_type'].text())
            n.hand = int(self._fields['hand'].text())

            p1_txt = self._fields['param1 (slide prev)'].text().strip()
            n.param1 = int(p1_txt) if p1_txt else 0
            p2_txt = self._fields['param2 (slide next)'].text().strip()
            n.param2 = int(p2_txt) if p2_txt else 0

            track_txt = self._fields['track'].text().strip()
            n.track = int(track_txt) if track_txt else None

            pitch_txt = self._fields['pitch'].text().strip()
            n.pitch = int(pitch_txt) if pitch_txt else None

            velocity_txt = self._fields['velocity'].text().strip()
            n.velocity = int(velocity_txt) if velocity_txt else None

            channel_txt = self._fields['channel'].text().strip()
            n.channel = int(channel_txt) if channel_txt else None

            off_velocity_txt = self._fields['off_velocity'].text().strip()
            n.off_velocity = int(off_velocity_txt) if off_velocity_txt else None

            n.gate = max(0, n.end - n.start)
        except ValueError as e:
            QMessageBox.critical(self, t('prop_err_title'), t('prop_err_msg', e))
            return

        self.accept()

    def apply_to(self, note: GNote) -> None:
        edited = self._edited
        note.start = edited.start
        note.end = edited.end
        note.gate = edited.gate
        note.min_key = edited.min_key
        note.max_key = edited.max_key
        note.note_type = edited.note_type
        note.hand = edited.hand
        note.param1 = edited.param1
        note.param2 = edited.param2
        note.param3 = edited.param3
        note.track = edited.track
        note.pitch = edited.pitch
        note.velocity = edited.velocity
        note.channel = edited.channel
        note.off_velocity = edited.off_velocity
