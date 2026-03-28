import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QFileDialog, QMessageBox, QMenu, QToolBar, QPushButton,
    QVBoxLayout, QWidget, QInputDialog, QLabel, QListWidget, QListWidgetItem, QHBoxLayout
)
from PyQt5.QtCore import Qt
from collections import deque
import time
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import copy
from bisect import bisect_right
from typing import List, Optional, Set
from qt_editor.note_model import NoteFile, GNote
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush
from PyQt5.QtCore import QRect

TOTAL_GAME_KEYS = 28
TIME_WINDOW_UNITS = 8.0
SCROLL_STEP_UNITS = 0.125
MIN_NOTE_HEIGHT_UNITS = 0.05

class NoteCanvas(QWidget):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.setMinimumSize(800, 500)
        self.setMouseTracking(True)
        self.dragging = False
        self.drag_start = None
        self.drag_rect = None
        self.last_pos = None
        self.zoom = 1.0
        self.offset_x = 0
        self.offset_y = 0

    def paintEvent(self, event):
        # optional profiling of paint duration
        profiling = getattr(self.app, 'profiling_enabled', False)
        t0 = time.time() if profiling else None

        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        self.draw_background(qp)
        self.draw_notes(qp)
        self.draw_selection(qp)

        if profiling:
            t1 = time.time()
            dt_ms = (t1 - t0) * 1000.0
            # update app-level profiling counters
            try:
                self.app._prof_frame_count += 1
                self.app.total_draw_ms += dt_ms
                self.app.draw_samples += 1
                now = t1
                # update fps every 1 second
                if now - self.app._prof_last_time >= 1.0:
                    elapsed = now - self.app._prof_last_time
                    self.app.fps = self.app._prof_frame_count / elapsed if elapsed > 0 else 0.0
                    # reset counters for next interval
                    self.app._prof_last_time = now
                    self.app._prof_frame_count = 0
                    # reset accumulated draw stats to avoid unbounded growth
                    self.app.total_draw_ms = 0.0
                    self.app.draw_samples = 0
            except Exception:
                pass

    def draw_background(self, qp):
        qp.fillRect(self.rect(), QColor(30, 30, 30))
        w = self.width()
        h = self.height()
        # 畫鍵盤格線與音高標題
        font = qp.font()
        font.setPointSize(8)
        qp.setFont(font)
        for i in range(TOTAL_GAME_KEYS+1):
            x = int(i * w / TOTAL_GAME_KEYS)
            qp.setPen(QColor(60, 60, 60) if i % 4 else QColor(120, 120, 120))
            qp.drawLine(x, 0, x, h)
            # 音高標題
            if i < TOTAL_GAME_KEYS:
                qp.setPen(QColor(180,180,180))
                qp.drawText(x+2, 12, f'{i}')
        # 畫時間格線與標籤（使用 unit -> ms 映射顯示友善時間）
        subdivisions = 4  # subdivisions per unit (e.g., 4 -> 16th notes if unit is quarter-note)
        tick_count = int(self.app.window_size_unit * subdivisions) + 1
        for t in range(0, tick_count):
            y = int(h - t * h / (self.app.window_size_unit * subdivisions))
            unit_val = self.app.window_start_unit + (t / float(subdivisions))
            # convert to ms via app mapping
            try:
                ms_val = float(self.app._unit_to_time(unit_val))
            except Exception:
                ms_val = 0.0
            major = (t % subdivisions == 0)
            qp.setPen(QColor(80, 80, 80) if not major else QColor(180, 180, 180))
            qp.drawLine(0, y, w, y)
            # 標出時間（每 major tick 顯示 mm:ss.ss）
            if major:
                qp.setPen(QColor(220,220,220))
                total_s = ms_val / 1000.0
                m = int(total_s // 60)
                s = total_s - m * 60
                time_str = f'{m:d}:{s:05.2f}'
                qp.drawText(2, y-2, time_str)
        # 畫小節線
        bar_len = self.app.beats_per_bar
        bar_start = int(self.app.window_start_unit // bar_len) * bar_len
        bar_end = int((self.app.window_start_unit + self.app.window_size_unit) // bar_len + 2) * bar_len
        for bar in range(bar_start, bar_end, bar_len):
            y = int(h - (bar - self.app.window_start_unit) * h / self.app.window_size_unit)
            if 0 <= y <= h:
                qp.setPen(QPen(QColor(255, 200, 0), 2))
                qp.drawLine(0, y, w, y)
                qp.setPen(QColor(255, 200, 0))
                qp.drawText(w-40, y-2, f'Bar {bar//bar_len+1}')

    def draw_notes(self, qp):
        if not self.app.notes:
            return
        w = self.width()
        h = self.height()
        for idx, n in enumerate(self.app.notes):
            # convert ms -> unit (beat units) for display
            start_u = self.app._time_to_unit(float(n.start))
            end_u = self.app._time_to_unit(float(n.end))
            # only draw notes intersecting current window
            if end_u < self.app.window_start_unit or start_u > self.app.window_start_unit + self.app.window_size_unit:
                continue
            x1 = int(n.min_key * w / TOTAL_GAME_KEYS)
            x2 = int((n.max_key+1) * w / TOTAL_GAME_KEYS)
            # y 軸根據 window_start_unit 做偏移 (unit -> relative unit)
            y1 = int(h - (start_u - self.app.window_start_unit) * h / self.app.window_size_unit)
            y2 = int(h - (end_u - self.app.window_start_unit) * h / self.app.window_size_unit)
            # determine color by note properties
            # note_type: 0=tap,1=soft,2=long,3=staccato (treat staccato as tap)
            nt = getattr(n, 'note_type', 0)
            hand = getattr(n, 'hand', 0)
            # soft overrides hand colors
            if nt == 1:
                fill = QColor(255, 210, 0)  # yellow for soft
                pen_col = QColor(140, 110, 0)
            else:
                if nt == 2:
                    # long (hold) deep colors
                    if hand == 1:
                        fill = QColor(197, 48, 48)  # deep red
                        pen_col = QColor(120, 20, 20)
                    else:
                        fill = QColor(28, 95, 153)  # deep blue
                        pen_col = QColor(10, 40, 80)
                else:
                    # tap / staccato light colors by hand
                    if hand == 1:
                        fill = QColor(255, 179, 179)  # light red
                        pen_col = QColor(120, 20, 20)
                    else:
                        fill = QColor(166, 216, 255)  # light blue
                        pen_col = QColor(10, 60, 110)

            # selection highlight: draw a thicker white outline if selected
            if idx in self.app.selected:
                qp.setPen(QPen(QColor(255, 255, 255), 2))
            else:
                qp.setPen(QPen(pen_col, 1))
            qp.setBrush(QBrush(fill))
            qp.drawRect(x1, y2, x2-x1, y1-y2)

            # draw pitch number centered if space allows
            try:
                rect_w = x2 - x1
                rect_h = y1 - y2
                if rect_w > 12 and rect_h > 10:
                    font = qp.font()
                    font.setPointSize(8)
                    font.setBold(True)
                    qp.setFont(font)
                    qp.setPen(QPen(QColor(0, 0, 0) if nt != 1 else QColor(30,30,30), 1))
                    qp.drawText(QRect(x1, y2, rect_w, rect_h), Qt.AlignCenter, str(getattr(n, 'pitch', '')))
            except Exception:
                pass

    def draw_selection(self, qp):
        if self.drag_rect:
            qp.setPen(QPen(QColor(255,255,0), 2, Qt.DashLine))
            qp.setBrush(Qt.NoBrush)
            r = self.drag_rect.normalized()
            qp.drawRect(r)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_start = event.pos()
            self.last_pos = event.pos()
            self.drag_rect = None
        elif event.button() == Qt.RightButton:
            idx = self.note_at(event.pos())
            if idx is not None:
                if idx in self.app.selected:
                    self.app.selected.remove(idx)
                else:
                    self.app.selected.add(idx)
                self.app.update_status()
                self.update()

    def mouseMoveEvent(self, event):
        if self.dragging and self.drag_start:
            self.drag_rect = QRect(self.drag_start, event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging and self.drag_start:
            r = QRect(self.drag_start, event.pos()).normalized()
            self.app.selected = set()
            for idx, n in enumerate(self.app.notes):
                if self.note_rect(n).intersects(r):
                    self.app.selected.add(idx)
            self.app.update_status()
            self.dragging = False
            self.drag_start = None
            self.drag_rect = None
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        modifiers = event.modifiers()
        # If Ctrl pressed -> zoom around mouse Y position
        if modifiers & Qt.ControlModifier:
            # zoom in for positive delta, out for negative
            try:
                h = self.height()
                # anchor unit under cursor
                mouse_y = event.pos().y()
                rel = max(0.0, min(h, mouse_y))
                anchor_unit = self.app.window_start_unit + ((h - rel) / h) * self.app.window_size_unit
                if delta > 0:
                    self.app.zoom_window(0.9, anchor_unit=anchor_unit)
                else:
                    self.app.zoom_window(1.1, anchor_unit=anchor_unit)
            except Exception:
                pass
            self.app.update_status()
            self.update()
            return

        # No Ctrl: scroll time window up/down
        step = self.app.scroll_step_unit
        if delta > 0:
            self.app.window_start_unit = max(0.0, self.app.window_start_unit - step)
        elif delta < 0:
            max_start = self.max_window_start()
            self.app.window_start_unit = min(max_start, self.app.window_start_unit + step)
        self.app.update_status()
        self.update()

    def max_window_start(self):
        # 取得所有音符最大 end，避免超出範圍
        if not self.app.notes:
            return 0.0
        # compute in unit space
        max_end_unit = max(self.app._time_to_unit(float(n.end)) for n in self.app.notes)
        return max(0.0, max_end_unit - self.app.window_size_unit)

    def note_at(self, pos):
        for idx, n in enumerate(self.app.notes):
            if self.note_rect(n).contains(pos):
                return idx
        return None

    def note_rect(self, n):
        w = self.width()
        h = self.height()
        x1 = int(n.min_key * w / TOTAL_GAME_KEYS)
        x2 = int((n.max_key+1) * w / TOTAL_GAME_KEYS)
        start_u = self.app._time_to_unit(float(n.start))
        end_u = self.app._time_to_unit(float(n.end))
        y1 = int(h - (start_u - self.app.window_start_unit) * h / self.app.window_size_unit)
        y2 = int(h - (end_u - self.app.window_start_unit) * h / self.app.window_size_unit)
        return QRect(x1, y2, x2-x1, y1-y2)

class DrawGraphicalQtApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Nostaligia Editor (Qt)')
        self.resize(1000, 700)
        self.current_file = None
        self.note_file: Optional[NoteFile] = None
        self.notes: List[GNote] = []
        self.selected: Set[int] = set()
        self.clipboard = []
        self.undo_stack = []
        self.redo_stack = []
        self.undo_limit = 50
        self.dirty = False
        self.bpm = 120.0
        self.beats_per_bar = 4
        self.beat_offset_ms = 0.0
        self.window_start_unit = 0.0
        self.window_size_unit = TIME_WINDOW_UNITS
        self.scroll_step_unit = SCROLL_STEP_UNITS
        self._playing = False
        self._paused = False
        # profiling / rendering stats
        self.profiling_enabled = False
        self._prof_last_time = time.time()
        self._prof_frame_count = 0
        self.fps = 0.0
        self.total_draw_ms = 0.0
        self.draw_samples = 0
        self.status_label = QLabel()
        self.list_widget = QListWidget()
        self.init_ui()
        self.update_status()

    def init_ui(self):
        # File actions
        open_act = QAction('Open', self)
        open_act.triggered.connect(self.open_file)
        save_act = QAction('Save', self)
        save_act.triggered.connect(self.save_as)
        quick_save_act = QAction('Quick Save', self)
        quick_save_act.triggered.connect(self.quick_save)
        open_wav_act = QAction('Open WAV', self)
        # Edit actions
        copy_act = QAction('Copy', self)
        copy_act.triggered.connect(self.copy_selection)
        paste_act = QAction('Paste', self)
        paste_act.triggered.connect(self.paste_selection)
        delete_act = QAction('Delete', self)
        delete_act.triggered.connect(self.delete_selected)
        duplicate_act = QAction('Duplicate', self)
        duplicate_act.triggered.connect(self.duplicate_selected)
        shift_pitch_act = QAction('Shift Pitch', self)
        shift_pitch_act.triggered.connect(self.shift_selected_pitch_prompt)
        undo_act = QAction('Undo', self)
        undo_act.triggered.connect(self.undo_last_action)
        redo_act = QAction('Redo', self)
        redo_act.triggered.connect(self.redo_last_action)
        # Playback actions
        play_window_act = QAction('Play Window', self)
        play_sel_act = QAction('Play Selection', self)
        pause_act = QAction('Pause', self)
        # Change Width actions
        width2_act = QAction('Width 2', self)
        width2_act.triggered.connect(self.set_width_selected_two)
        width3_act = QAction('Width 3', self)
        width3_act.triggered.connect(self.set_width_selected_three)
        # Change Type actions
        tap_act = QAction('Make Tap', self)
        tap_act.triggered.connect(lambda: self.set_type_selected('tap'))
        long_act = QAction('Make Long', self)
        long_act.triggered.connect(lambda: self.set_type_selected('long'))
        soft_act = QAction('Make Soft', self)
        soft_act.triggered.connect(lambda: self.set_type_selected('soft'))
        stac_act = QAction('Make Staccato', self)
        stac_act.triggered.connect(lambda: self.set_type_selected('staccato'))
        # BPM/Bar/Offset actions
        bpm_act = QAction('Set BPM', self)
        bpm_act.triggered.connect(self.set_bpm)
        bar_act = QAction('Set Bar', self)
        bar_act.triggered.connect(self.set_bar)
        offset_act = QAction('Set Offset', self)
        offset_act.triggered.connect(self.set_offset)

        menubar = self.menuBar()
        file_menu = menubar.addMenu('File')
        file_menu.addAction(open_act)
        file_menu.addAction(save_act)
        file_menu.addAction(quick_save_act)
        file_menu.addAction(open_wav_act)
        edit_menu = menubar.addMenu('Edit')
        edit_menu.addAction(copy_act)
        edit_menu.addAction(paste_act)
        edit_menu.addAction(delete_act)
        edit_menu.addAction(duplicate_act)
        edit_menu.addAction(shift_pitch_act)
        edit_menu.addAction(undo_act)
        edit_menu.addAction(redo_act)
        tools_menu = menubar.addMenu('Tools')
        tools_menu.addAction(bpm_act)
        tools_menu.addAction(bar_act)
        tools_menu.addAction(offset_act)
        profile_act = QAction('Toggle Profiling', self)
        profile_act.triggered.connect(self.toggle_profiling)
        tools_menu.addAction(profile_act)
        playback_menu = menubar.addMenu('Playback')
        playback_menu.addAction(play_window_act)
        playback_menu.addAction(play_sel_act)
        playback_menu.addAction(pause_act)
        change_width_menu = menubar.addMenu('Change Width')
        change_width_menu.addAction(width2_act)
        change_width_menu.addAction(width3_act)
        change_type_menu = menubar.addMenu('Change Type')
        change_type_menu.addAction(tap_act)
        change_type_menu.addAction(long_act)
        change_type_menu.addAction(soft_act)
        change_type_menu.addAction(stac_act)

        # 主視窗內容 (Qt-native canvas + list)
        central = QWidget()
        vlayout = QVBoxLayout()
        hlayout = QHBoxLayout()

        # Qt-native drawing canvas (fast for many rectangles)
        self.canvas = NoteCanvas(self, self)
        hlayout.addWidget(self.canvas, 3)
        hlayout.addWidget(self.list_widget, 1)

        vlayout.addLayout(hlayout)
        vlayout.addWidget(self.status_label)
        central.setLayout(vlayout)
        self.setCentralWidget(central)

    def update_status(self):
        self.status_label.setText(
            f'File: {self.current_file}\nNotes: {len(self.notes)}\nSelected: {len(self.selected)}\nBPM: {self.bpm}\nBar: {self.beats_per_bar}\nOffset: {self.beat_offset_ms}'
        )
        self.list_widget.clear()
        for i, n in enumerate(self.notes):
            item = QListWidgetItem(f'Idx:{i} Start:{n.start} End:{n.end} Key:{n.min_key}-{n.max_key} Type:{n.note_type} Hand:{n.hand} Pitch:{n.pitch}')
            if i in self.selected:
                item.setSelected(True)
            self.list_widget.addItem(item)
        if hasattr(self, 'canvas'):
            self.canvas.update()

        # append profiling stats if enabled
        if getattr(self, 'profiling_enabled', False):
            avg_draw = (self.total_draw_ms / self.draw_samples) if self.draw_samples > 0 else 0.0
            # show on status label (append)
            txt = self.status_label.text()
            txt += f"\nFPS: {self.fps:.1f}  Avg draw: {avg_draw:.2f} ms"
            self.status_label.setText(txt)

    def toggle_profiling(self):
        self.profiling_enabled = not self.profiling_enabled
        if self.profiling_enabled:
            self._prof_last_time = time.time()
            self._prof_frame_count = 0
            self.fps = 0.0
            self.total_draw_ms = 0.0
            self.draw_samples = 0
            QMessageBox.information(self, 'Profiling', 'Rendering profiling enabled.')
        else:
            QMessageBox.information(self, 'Profiling', 'Rendering profiling disabled.')

    def on_selection_changed(self):
        self.selected = set([i.row() for i in self.list_widget.selectedIndexes()])
        self.update_status()

    def open_file(self):
        fname, _ = QFileDialog.getOpenFileName(self, 'Open File', '', 'XML Files (*.xml)')
        if fname:
            self.current_file = fname
            self.note_file = NoteFile(fname)
            ok = self.note_file.load()
            if ok:
                self.notes = self.note_file.notes
                self.selected = set()
                self.undo_stack.clear()
                self.redo_stack.clear()
                # 從 note_file 讀取 bpm, beats_per_bar, beat_offset_ms 與 beat list
                if hasattr(self.note_file, 'bpm'):
                    self.bpm = self.note_file.bpm
                if hasattr(self.note_file, 'beats_per_bar'):
                    self.beats_per_bar = self.note_file.beats_per_bar
                if hasattr(self.note_file, 'beat_offset_ms'):
                    self.beat_offset_ms = self.note_file.beat_offset_ms
                if hasattr(self.note_file, 'beats'):
                    self._beats = list(self.note_file.beats)
                else:
                    self._beats = []
                # build time<->unit mapping
                self._build_time_mapping()
                QMessageBox.information(self, 'Open', f'File opened: {fname}')
                self.update_status()
            else:
                QMessageBox.warning(self, 'Open', 'Failed to load notes.')

    def save_as(self):
        fname, _ = QFileDialog.getSaveFileName(self, 'Save As', '', 'XML Files (*.xml)')
        if fname and self.note_file:
            self.current_file = fname
            self.note_file.save(fname)
            QMessageBox.information(self, 'Save', f'Saved to: {fname}')
            self.update_status()

    def quick_save(self):
        if not self.current_file or not self.note_file:
            self.save_as()
            return
        self.note_file.save(self.current_file)
        QMessageBox.information(self, 'Quick Save', f'Saved to: {self.current_file}')
        self.update_status()

    def copy_selection(self):
        self.clipboard = [self.notes[idx].to_dict() for idx in self.selected if 0 <= idx < len(self.notes)]
        QMessageBox.information(self, 'Copy', f'Copied {len(self.clipboard)} notes.')
        if getattr(self, 'profiling_enabled', False):
            self._prof_last_time = time.time()
            self._prof_frame_count = 0
            self.fps = 0.0
            self.total_draw_ms = 0.0
            self.draw_samples = 0

    def paste_selection(self):
        if not self.clipboard or not self.note_file:
            QMessageBox.warning(self, 'Paste', 'Clipboard is empty or no file loaded.')
            return
        self.push_undo()
        base_idx = len(self.notes)
        for n in self.clipboard:
            elem = self.note_file.root.find('note_data').makeelement('note')
            for k, v in n.items():
                if k != 'sub_elems':
                    elem.set(k, str(v))
            new_note = GNote(elem, base_idx)
            self.notes.append(new_note)
            base_idx += 1
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Paste', f'Pasted {len(self.clipboard)} notes.')
        self.update_status()
        if getattr(self, 'profiling_enabled', False):
            # reset profiling counters to avoid stale numbers after big edits
            self._prof_last_time = time.time()
            self._prof_frame_count = 0
            self.fps = 0.0
            self.total_draw_ms = 0.0
            self.draw_samples = 0

    def delete_selected(self):
        if not self.selected:
            QMessageBox.warning(self, 'Delete', 'No notes selected.')
            return
        self.push_undo()
        self.notes = [n for i, n in enumerate(self.notes) if i not in self.selected]
        self.note_file.notes = self.notes
        self.selected.clear()
        QMessageBox.information(self, 'Delete', 'Selected notes deleted.')
        self.update_status()

    def duplicate_selected(self):
        if not self.selected:
            QMessageBox.warning(self, 'Duplicate', 'No notes selected.')
            return
        self.push_undo()
        base_idx = len(self.notes)
        for idx in self.selected:
            n = self.notes[idx].to_dict()
            elem = self.note_file.root.find('note_data').makeelement('note')
            for k, v in n.items():
                if k != 'sub_elems':
                    elem.set(k, str(v))
            new_note = GNote(elem, base_idx)
            self.notes.append(new_note)
            base_idx += 1
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Duplicate', f'Duplicated {len(self.selected)} notes.')
        self.update_status()
        if getattr(self, 'profiling_enabled', False):
            self._prof_last_time = time.time()
            self._prof_frame_count = 0
            self.fps = 0.0
            self.total_draw_ms = 0.0
            self.draw_samples = 0

    def shift_selected_pitch_prompt(self):
        if not self.selected:
            QMessageBox.warning(self, 'Warning', 'No notes selected')
            return
        val, ok = QInputDialog.getInt(self, 'Shift Pitch', 'Shift selected pitches by (use negative for down):', 0)
        if ok:
            self.shift_selected_pitch(val)

    def shift_selected_pitch(self, delta):
        self.push_undo()
        for idx in self.selected:
            n = self.notes[idx]
            n.pitch = max(0, min(127, n.pitch + delta))
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Shift Pitch', f'Pitch shifted by {delta}')
        self.update_status()
        if getattr(self, 'profiling_enabled', False):
            self._prof_last_time = time.time()
            self._prof_frame_count = 0
            self.fps = 0.0
            self.total_draw_ms = 0.0
            self.draw_samples = 0

    def shift_selected_time_by_32nd(self, direction: int):
        """Shift selected notes in time by one 32nd note (direction: -1 or +1)."""
        if not self.selected:
            QMessageBox.warning(self, 'Warning', 'No notes selected')
            return
        try:
            bpm = float(self.bpm) if self.bpm and self.bpm > 0 else 120.0
        except Exception:
            bpm = 120.0
        unit_ms = 60000.0 / bpm / 8.0
        delta = int(round(direction * unit_ms))
        self.push_undo()
        for idx in self.selected:
            n = self.notes[idx]
            try:
                n.start = float(n.start) + delta
                n.end = float(n.end) + delta
            except Exception:
                pass
        self.note_file.notes = self.notes
        self.update_status()

    def shift_selected_keys(self, delta_keys: int):
        """Shift selected notes' key indices by delta_keys."""
        if not self.selected:
            QMessageBox.warning(self, 'Warning', 'No notes selected')
            return
        self.push_undo()
        for idx in self.selected:
            n = self.notes[idx]
            try:
                n.min_key = max(0, min(TOTAL_GAME_KEYS - 1, int(n.min_key) + delta_keys))
                n.max_key = max(n.min_key, min(TOTAL_GAME_KEYS - 1, int(n.max_key) + delta_keys))
            except Exception:
                pass
        self.note_file.notes = self.notes
        self.update_status()

    def keyPressEvent(self, event):
        # Handle arrow keys for scrolling or moving notes (Ctrl modifies behavior)
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        if key == Qt.Key_Left:
            # Left: move selected notes left by one key (min/max -1)
            if ctrl:
                # Ctrl+Left: time shift (32nd) backward
                self.shift_selected_time_by_32nd(-1)
            else:
                try:
                    self.shift_selected_keys(-1)
                except Exception:
                    pass
                self.update_status()
                if hasattr(self, 'canvas'):
                    self.canvas.update()
        elif key == Qt.Key_Right:
            # Right: move selected notes right by one key (min/max +1)
            if ctrl:
                # Ctrl+Right: time shift (32nd) forward
                self.shift_selected_time_by_32nd(1)
            else:
                try:
                    self.shift_selected_keys(1)
                except Exception:
                    pass
                self.update_status()
                if hasattr(self, 'canvas'):
                    self.canvas.update()
        elif key == Qt.Key_Down:
            if ctrl:
                self.shift_selected_keys(1)
            else:
                max_start = self.canvas.max_window_start() if hasattr(self, 'canvas') else 0.0
                self.window_start_unit = min(max_start, self.window_start_unit + self.window_size_unit * 0.25)
                self.update_status()
                if hasattr(self, 'canvas'):
                    self.canvas.update()
        elif key == Qt.Key_Up:
            if ctrl:
                self.shift_selected_keys(-1)
            else:
                self.window_start_unit = max(0.0, self.window_start_unit - self.window_size_unit * 0.25)
                self.update_status()
                if hasattr(self, 'canvas'):
                    self.canvas.update()

    def undo_last_action(self):
        if not self.undo_stack:
            QMessageBox.information(self, 'Undo', 'Nothing to undo.')
            return
        # push current state to redo (deep copy) and restore previous snapshot
        self.redo_stack.append(copy.deepcopy(self.notes))
        self.notes = self.undo_stack.pop()
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Undo', 'Undo last action.')
        self.update_status()

    def redo_last_action(self):
        if not self.redo_stack:
            QMessageBox.information(self, 'Redo', 'Nothing to redo.')
            return
        # push current state to undo (deep copy) and restore redo snapshot
        self.undo_stack.append(copy.deepcopy(self.notes))
        self.notes = self.redo_stack.pop()
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Redo', 'Redo last action.')
        self.update_status()

    def set_width_selected_two(self):
        self.push_undo()
        for idx in self.selected:
            n = self.notes[idx]
            n.max_key = n.min_key + 1
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Change Width', 'Width set to 2.')
        self.update_status()

    def set_width_selected_three(self):
        self.push_undo()
        for idx in self.selected:
            n = self.notes[idx]
            n.max_key = n.min_key + 2
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Change Width', 'Width set to 3.')
        self.update_status()

    def set_type_selected(self, t):
        self.push_undo()
        type_map = {'tap': 0, 'long': 2, 'soft': 1, 'staccato': 3}
        for idx in self.selected:
            n = self.notes[idx]
            n.note_type = type_map.get(t, 0)
        self.note_file.notes = self.notes
        QMessageBox.information(self, 'Change Type', f'Note type set to {t}.')
        self.update_status()

    def set_bpm(self):
        val, ok = QInputDialog.getDouble(self, 'Set BPM', 'BPM:', self.bpm)
        if ok:
            self.bpm = val
            self.update_status()

    def set_bar(self):
        val, ok = QInputDialog.getInt(self, 'Set Bar', 'Beats per Bar:', self.beats_per_bar)
        if ok:
            self.beats_per_bar = val
            self.update_status()

    def set_offset(self):
        val, ok = QInputDialog.getDouble(self, 'Set Offset', 'Offset (ms):', self.beat_offset_ms)
        if ok:
            self.beat_offset_ms = val
            self.update_status()

    def push_undo(self):
        # store a deep copy so undo/redo fully restore object state
        self.undo_stack.append(copy.deepcopy(self.notes))
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    # ---- time mapping (ms <-> unit) -------------------------------------------------
    def _build_time_mapping(self):
        # build from self._beats (list of (idx, ms)). If absent, fallback to uniform beats from BPM.
        beats = getattr(self, '_beats', []) or []
        if not beats:
            beat_ms = 60000.0 / (self.bpm if self.bpm and self.bpm > 0 else 120.0)
            total_end = max((n.end for n in self.notes), default=0.0)
            count = int(total_end / beat_ms) + 8
            beats = [(i, float(i * beat_ms)) for i in range(count + 1)]
        beats.sort(key=lambda x: x[0])
        # time_points: list of (ms, unit)
        self._time_points = [(float(ms), float(idx)) for idx, ms in beats]
        self._time_points_ms = [ms for ms, _ in self._time_points]
        # unit_points: list of (unit, ms)
        self._unit_points = [(float(idx), float(ms)) for idx, ms in beats]
        self._unit_points_units = [u for u, _ in self._unit_points]
        self._default_unit_per_ms = (self.bpm / 60000.0) if self.bpm and self.bpm > 0 else (120.0 / 60000.0)

    def _time_to_unit(self, msec: float) -> float:
        if not hasattr(self, '_time_points') or not self._time_points:
            # fallback linear mapping
            return msec * self._default_unit_per_ms
        pts = self._time_points
        if msec <= pts[0][0]:
            return pts[0][1]
        if msec >= pts[-1][0]:
            # extrapolate using last slope
            ms0, u0 = pts[-2]
            ms1, u1 = pts[-1]
            slope = (u1 - u0) / (ms1 - ms0) if ms1 != ms0 else self._default_unit_per_ms
            return u1 + (msec - ms1) * slope
        idx = bisect_right(self._time_points_ms, msec) - 1
        if idx < 0:
            return self._time_points[0][1]
        ms0, u0 = pts[idx]
        ms1, u1 = pts[idx + 1]
        slope = (u1 - u0) / (ms1 - ms0) if ms1 != ms0 else self._default_unit_per_ms
        return u0 + (msec - ms0) * slope

    def _unit_to_time(self, unit_val: float) -> float:
        if not hasattr(self, '_unit_points') or not self._unit_points:
            return unit_val / self._default_unit_per_ms if self._default_unit_per_ms > 0 else unit_val * 500.0
        pts = self._unit_points
        if unit_val <= pts[0][0]:
            return pts[0][1]
        if unit_val >= pts[-1][0]:
            u0, ms0 = pts[-2]
            u1, ms1 = pts[-1]
            slope = (ms1 - ms0) / (u1 - u0) if u1 != u0 else (1.0 / self._default_unit_per_ms if self._default_unit_per_ms > 0 else 500.0)
            return ms1 + (unit_val - u1) * slope
        idx = bisect_right(self._unit_points_units, unit_val) - 1
        if idx < 0:
            return self._unit_points[0][1]
        u0, ms0 = pts[idx]
        u1, ms1 = pts[idx + 1]
        slope = (ms1 - ms0) / (u1 - u0) if u1 != u0 else (1.0 / self._default_unit_per_ms if self._default_unit_per_ms > 0 else 500.0)
        return ms0 + (unit_val - u0) * slope

    # ---- Zoom helpers -----------------------------------------------------------
    def zoom_window(self, scale: float, anchor_unit: Optional[float] = None):
        # scale <1 -> zoom in (smaller window_size_unit), scale >1 -> zoom out
        try:
            scale_val = float(scale)
        except Exception:
            return
        if scale_val <= 0:
            return
        current = self.window_size_unit if self.window_size_unit > 0 else TIME_WINDOW_UNITS
        new_size = current * scale_val
        new_size = max(0.25, min(512.0, new_size))
        if abs(new_size - self.window_size_unit) <= 1e-6:
            return
        if anchor_unit is None:
            anchor_unit = self.window_start_unit + (self.window_size_unit * 0.5)
        # keep anchor in same screen position
        anchor_rel = (anchor_unit - self.window_start_unit) / self.window_size_unit if self.window_size_unit > 0 else 0.5
        self.window_size_unit = new_size
        self.window_start_unit = anchor_unit - anchor_rel * self.window_size_unit
        # clamp
        if self.window_start_unit < 0.0:
            self.window_start_unit = 0.0
        max_start = 0.0
        if self.notes:
            max_end_unit = max(self._time_to_unit(float(n.end)) for n in self.notes)
            max_start = max(0.0, max_end_unit - self.window_size_unit)
        self.window_start_unit = min(self.window_start_unit, max_start)
        self.update_status()
        if hasattr(self, 'canvas'):
            self.canvas.update()

    def zoom_in(self):
        self.zoom_window(0.5)

    def zoom_out(self):
        self.zoom_window(2.0)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = DrawGraphicalQtApp()
    win.show()
    sys.exit(app.exec_())