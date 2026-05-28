from __future__ import annotations

import os
import tempfile
import uuid
from typing import List, Dict

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush
from PyQt5.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QDoubleSpinBox, QSizePolicy
)

from .models import NoteModel, GNote


class StaffCanvas(QWidget):
    def __init__(self, editor: 'StaffEditor') -> None:
        super().__init__()
        self.editor = editor
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.notes: List[Dict] = []  # {'start_ms','duration_ms','pitch','hand'}

    # Natural note semitone offsets (C D E F G A B)
    _NAT_OFFSETS = [0, 2, 4, 5, 7, 9, 11]
    # Treble clef: middle staff line (B4) diatonic index = 4*7 + 6 = 34
    _TREBLE_CENTER_DIATONIC = 34
    # Bass clef: middle staff line (D3) diatonic index = 3*7 + 1 = 22
    _BASS_CENTER_DIATONIC = 22

    def _get_center_diatonic(self) -> int:
        """Return current clef center diatonic index using editor setting."""
        try:
            return int(self.editor.get_clef_center_diatonic())
        except Exception:
            return self._TREBLE_CENTER_DIATONIC

    def _midi_to_diatonic(self, pitch: int) -> int:
        """Map a MIDI pitch to the nearest natural-note diatonic index.

        Returns an integer diatonic index where each step represents the
        next natural letter (C D E F G A B). This picks the nearest
        natural pitch (ties resolved by lower octave candidate).
        """
        try:
            p = int(pitch)
        except Exception:
            return self._TREBLE_CENTER_DIATONIC
        base_oct = (p // 12) - 1
        best = None
        for o in (base_oct - 1, base_oct, base_oct + 1):
            for li, off in enumerate(self._NAT_OFFSETS):
                cand = (o + 1) * 12 + off
                delta = abs(cand - p)
                if best is None or delta < best[0]:
                    best = (delta, o, li)
        if best is None:
            return self._TREBLE_CENTER_DIATONIC
        _, octv, li = best
        return octv * 7 + li

    def _diatonic_to_y(self, diatonic_idx: int, center_y: float, spacing: float) -> int:
        """Convert diatonic index to widget Y coordinate based on staff center."""
        step_px = float(spacing) / 2.0
        center = self._get_center_diatonic()
        return int(center_y - (diatonic_idx - center) * step_px)

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor('#ffffff'))

        # staff lines
        center_y = h / 2
        spacing = max(8, int(h / 20))
        pen = QPen(QColor('#cccccc'))
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(5):
            y = int(center_y + (i - 2) * spacing)
            painter.drawLine(0, y, w, y)

        # grid: draw beat markers and subdivisions (based on selected duration)
        bpm = self.editor.get_bpm()
        total_ms = max(1, int(self.editor.get_total_duration_ms()))
        if bpm and total_ms:
            beat_ms = 60000.0 / max(1.0, float(bpm))
            sel_ms = float(max(1, self.editor.get_selected_duration_ms()))
            # number of subdivision lines to draw
            n_sub = int(total_ms // sel_ms) + 2
            for k in range(n_sub):
                pos_ms = k * sel_ms
                if pos_ms > total_ms:
                    break
                x = int((pos_ms / float(total_ms)) * w)
                # mark major beat lines (approx) with a stronger color
                if abs(pos_ms - round(pos_ms / beat_ms) * beat_ms) < 1.0:
                    pen = QPen(QColor('#e6e6e6'))
                    pen.setWidth(1)
                else:
                    pen = QPen(QColor('#f6f6f6'))
                    pen.setWidth(1)
                painter.setPen(pen)
                painter.drawLine(x, 0, x, h)

        # note drawing: diatonic mapping -> staff positions, ledger lines, ellipse noteheads
        for n in self.notes:
            try:
                start = float(n['start_ms'])
                pitch = int(n.get('pitch', 60))
            except Exception:
                continue
            x = int((start / float(total_ms)) * w)
            di = self._midi_to_diatonic(pitch)
            y = self._diatonic_to_y(di, center_y, spacing)
            # draw note head (ellipse sized from staff spacing)
            head_w = max(6, int(spacing * 0.95))
            head_h = max(4, int(spacing * 0.62))
            painter.setBrush(QBrush(QColor('#000000')))
            painter.setPen(QPen(QColor('#000000')))
            painter.drawEllipse(x - head_w // 2, y - head_h // 2, head_w, head_h)

            # ledger lines: draw short horizontal lines for diatonic line positions
            center = self._get_center_diatonic()
            bottom_line = center - 4
            top_line = center + 4
            ledger_margin = 6
            # below staff
            if di < bottom_line:
                l = bottom_line - 2
                while l >= di:
                    ly = self._diatonic_to_y(l, center_y, spacing)
                    painter.setPen(QPen(QColor('#000000')))
                    lx1 = x - (head_w // 2) - ledger_margin
                    lx2 = x + (head_w // 2) + ledger_margin
                    painter.drawLine(int(lx1), int(ly), int(lx2), int(ly))
                    l -= 2
            # above staff
            if di > top_line:
                l = top_line + 2
                while l <= di:
                    ly = self._diatonic_to_y(l, center_y, spacing)
                    painter.setPen(QPen(QColor('#000000')))
                    lx1 = x - (head_w // 2) - ledger_margin
                    lx2 = x + (head_w // 2) + ledger_margin
                    painter.drawLine(int(lx1), int(ly), int(lx2), int(ly))
                    l += 2

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        w = self.width()
        h = self.height()
        x = ev.x()
        y = ev.y()
        total_ms = max(1, int(self.editor.get_total_duration_ms()))
        start_ms = int((x / max(1, w)) * total_ms)
        # pitch mapping
        min_pitch = 36
        max_pitch = 84
        ratio = 1.0 - (y / max(1, h))
        pitch = int(min_pitch + ratio * (max_pitch - min_pitch))
        duration_ms = int(self.editor.get_selected_duration_ms())
        hand = int(self.editor.hand_combo.currentIndex())
        self.notes.append({'start_ms': start_ms, 'duration_ms': duration_ms, 'pitch': pitch, 'hand': hand})
        self.update()


class StaffEditor(QDockWidget):
    def __init__(self, main_window) -> None:
        super().__init__('五線譜編輯', main_window)
        self.main = main_window
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(6, 6, 6, 6)
        # controls
        ctrl = QWidget()
        h = QHBoxLayout(ctrl)
        h.setContentsMargins(0, 0, 0, 0)

        h.addWidget(QLabel('BPM:'))
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(1.0, 9999.0)
        try:
            cur_bpm = float(getattr(self.main.view.model, 'bpm', 120.0))
        except Exception:
            cur_bpm = 120.0
        self.bpm_spin.setValue(cur_bpm)
        h.addWidget(self.bpm_spin)

        h.addWidget(QLabel('長度 (秒):'))
        self.len_spin = QDoubleSpinBox()
        self.len_spin.setRange(1.0, 3600.0)
        try:
            cur_len = float(getattr(self.main.view.model, 'music_end_ms', 180000.0)) / 1000.0
        except Exception:
            cur_len = 180.0
        self.len_spin.setValue(cur_len)
        h.addWidget(self.len_spin)

        h.addWidget(QLabel('時值:'))
        self.dur_combo = QComboBox()
        self.dur_combo.addItem('全音符 (4)')
        self.dur_combo.addItem('二分 (2)')
        self.dur_combo.addItem('四分 (1)')
        self.dur_combo.addItem('八分 (1/2)')
        self.dur_combo.addItem('16分 (1/4)')
        self.dur_combo.setCurrentIndex(2)
        h.addWidget(self.dur_combo)

        h.addWidget(QLabel('手:'))
        self.hand_combo = QComboBox()
        self.hand_combo.addItem('右手')
        self.hand_combo.addItem('左手')
        h.addWidget(self.hand_combo)

        h.addWidget(QLabel('譜號:'))
        self.clef_combo = QComboBox()
        self.clef_combo.addItem('高音譜號 (Treble)')
        self.clef_combo.addItem('低音譜號 (Bass)')
        self.clef_combo.setCurrentIndex(0)
        self.clef_combo.currentIndexChanged.connect(self._on_clef_changed)
        h.addWidget(self.clef_combo)

        h.addStretch()

        v.addWidget(ctrl)

        # canvas
        self.canvas = StaffCanvas(self)
        v.addWidget(self.canvas, 1)

        # action buttons
        btn_row = QWidget()
        bh = QHBoxLayout(btn_row)
        bh.setContentsMargins(0, 0, 0, 0)
        self.clear_btn = QPushButton('清除')
        self.clear_btn.clicked.connect(self._on_clear)
        bh.addWidget(self.clear_btn)
        self.finish_btn = QPushButton('完成並匯出')
        self.finish_btn.clicked.connect(self._on_finish)
        bh.addWidget(self.finish_btn)
        self.close_btn = QPushButton('關閉')
        self.close_btn.clicked.connect(self._on_close)
        bh.addWidget(self.close_btn)
        bh.addStretch()
        v.addWidget(btn_row)

        self.setWidget(c)

    # helper getters
    def get_bpm(self) -> float:
        try:
            return float(self.bpm_spin.value())
        except Exception:
            return 120.0

    def get_total_duration_ms(self) -> int:
        try:
            return int(float(self.len_spin.value()) * 1000.0)
        except Exception:
            return 180000

    def get_selected_duration_ms(self) -> int:
        idx = self.dur_combo.currentIndex()
        mapping = {0: 4.0, 1: 2.0, 2: 1.0, 3: 0.5, 4: 0.25}
        beats = mapping.get(idx, 1.0)
        bpm = max(1.0, self.get_bpm())
        ms = 60000.0 / bpm * beats
        return int(ms)

    # clef helpers
    def _on_clef_changed(self, _idx) -> None:
        try:
            self.canvas.update()
        except Exception:
            pass

    def get_clef_center_diatonic(self) -> int:
        """Return diatonic center index for currently selected clef.

        0 -> Treble (B4 center), 1 -> Bass (D3 center)
        """
        try:
            idx = int(self.clef_combo.currentIndex())
        except Exception:
            idx = 0
        return 22 if idx == 1 else 34

    # actions
    def _on_clear(self) -> None:
        self.canvas.notes.clear()
        self.canvas.update()

    def _on_close(self) -> None:
        self.hide()

    def _on_finish(self) -> None:
        notes = list(self.canvas.notes)
        if not notes:
            # nothing to export
            return
        bpm = self.get_bpm()
        duration_sec = max(1.0, float(self.len_spin.value()))
        model = NoteModel.create_new('staff_export', bpm, duration_sec)
        gnotes: List[GNote] = []
        for i, n in enumerate(notes):
            g = GNote(None, i)
            g.start = int(n['start_ms'])
            g.end = int(n['start_ms'] + n['duration_ms'])
            g.gate = max(0, g.end - g.start)
            g.min_key = 0
            g.max_key = 0
            # treat longer than 1 beat as hold
            g.note_type = 2 if (g.end - g.start) > (60000.0 / max(1.0, bpm)) else 0
            g.hand = int(n.get('hand', 0))
            g.pitch = int(n.get('pitch', 60))
            gnotes.append(g)
        model.notes_tree = gnotes
        model.rebuild_display_cache()
        tmp = os.path.join(tempfile.gettempdir(), f'nos_staff_export_{uuid.uuid4().hex}.xml')
        try:
            model.save_xml(tmp)
            # open via main window path loader
            try:
                self.main._load_path(tmp)
            except Exception:
                pass
        except Exception:
            pass
