"""
chart_view.py
=============
QPainter 渲染的樂譜編輯器視圖元件，完整功能版。

功能清單（對應 graphical_chartmaker.py）
----------------------------------------
渲染
  - 背景、鍵位格線、節拍/小節線、時間標籤
  - 音符繪製：顏色依 hand/note_type，pitch 數字
  - 選取框線高亮、拖曳橡皮筋、Alloc 覆蓋框
  - 播放 judge line

滑鼠
  - 左鍵拖曳：框選音符
  - 左鍵單擊 + Ctrl：加選/取消
  - 滾輪：自適應速度捲動
  - Ctrl + 左鍵拖曳：拖曳複製（16分音符 snap）
  - 右鍵：音符屬性對話框

鍵盤
  Up/Down          : 捲動（+Shift 加快 4x）
  Left/Right       : 鍵位平移（+Ctrl 10x，+Shift 5x）
  Ctrl+Up/Down     : 時間移動（32 分音符步）
  +/-              : 縮放
  Ctrl+Z           : Undo
  Ctrl+C           : 複製選取
  Ctrl+V           : 貼上到游標位置
  Delete           : 刪除選取
  H                : note_type → long(2)
  T                : note_type → tap(0)
  K                : note_type → staccato(3)
  L                : hand → 左(1)
  R                : hand → 右(0)
  C                : 就地 duplicate
  P                : 播放視窗
  Shift+P          : 播放選取區
  S                : 停止播放
  Shift+A          : 啟動 Alloc Section 模式
  Enter            : 確認 Alloc Section
  Escape           : 取消 Alloc Section / 取消選取

Alloc Section
  - 選取後 Shift+A 進入
  - 顯示紅框，拖曳改變鍵位範圍
  - 依 pitch 比例自動分配鍵位

座標系
------
X: 鍵位 0..TOTAL_GAME_KEYS（左→右 pixel）
Y: 下方 = window_start（較早），上方 = window_end（較晚）
   pixel_y = H * (1 - (unit_rel / window_size))
"""

from __future__ import annotations

import math
import os
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from PyQt5.QtCore import Qt, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt5.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QKeyEvent,
    QMouseEvent, QPaintEvent, QPixmap, QResizeEvent, QWheelEvent,
)
from PyQt5.QtWidgets import QDialog, QInputDialog, QWidget

from .models import GNote, NoteModel, TOTAL_GAME_KEYS
from .time_mapper import TimeMapper
from .property_dialog import NotePropertyDialog
from .i18n import t

# ---------------------------------------------------------------------------
# 色彩常數
# ---------------------------------------------------------------------------
BG_COLOR         = QColor(28, 28, 32)
GRID_MINOR       = QColor(50, 50, 58)
GRID_MAJOR       = QColor(90, 90, 100)
BARLINE_COLOR    = QColor(220, 200, 60)
BEATLINE_COLOR   = QColor(70, 70, 85)

NOTE_RIGHT       = QColor(255, 179, 179)
NOTE_LEFT        = QColor(166, 216, 255)
NOTE_SOFT        = QColor(255, 215,   0)
NOTE_STAC        = QColor(210, 150, 255)   # staccato 紫色
NOTE_RIGHT_LONG  = QColor(197,  48,  48)
NOTE_LEFT_LONG   = QColor( 28,  95, 153)
NOTE_OUT_R       = QColor(120,  20,  20)
NOTE_OUT_L       = QColor( 10,  60, 110)
NOTE_OUT_S       = QColor(140, 110,   0)

SEL_OUTLINE      = QColor(255, 230,   0)   # 黃色外框
RUBBER_COLOR     = QColor(255, 255,   0)
JUDGELINE_COLOR  = QColor(  0, 200, 255)
ALLOC_COLOR      = QColor(255,  60,  60)

PITCH_TEXT       = QColor(  0,   0,   0)
KEY_LABEL        = QColor(190, 190, 200)
TIME_LABEL       = QColor(160, 160, 170)

TIME_WINDOW_UNITS  = 8.0
SCROLL_STEP_UNITS  = 0.125
MIN_WINDOW_UNITS   = 0.5
MAX_WINDOW_UNITS   = 256.0
PRE_ROLL_UNITS     = 4.0
MIN_NOTE_HEIGHT_PX = 2
# 預覽模式固定高度（毫秒）
PREVIEW_MS = 300
# 固定像素高度（預設）— 預覽不再依時間或 BPM 縮放
PREVIEW_PX = 40




# ---------------------------------------------------------------------------
# ChartView
# ---------------------------------------------------------------------------

class ChartView(QWidget):
    """完整功能的樂譜編輯視窗。"""

    # ── 對外訊號 ──────────────────────────────────────────────────────
    selection_changed = pyqtSignal(int)          # 已選取數
    status_changed    = pyqtSignal(str)          # 狀態列文字
    note_edited       = pyqtSignal()             # 任何可 undo 的修改
    play_requested    = pyqtSignal(float, float) # start_ms, end_ms
    play_full_requested = pyqtSignal()            # 播放整首
    play_from_window_requested = pyqtSignal()     # 從視窗底部播到末尾
    stop_requested    = pyqtSignal()
    pause_requested   = pyqtSignal()
    resume_requested  = pyqtSignal()
    note_input_changed = pyqtSignal(bool)        # 放置模式開關
    set_measure_bpm_requested = pyqtSignal(int)  # 右鍵小節空白，傳小節編號
    set_measure_time_sig_requested = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.model:  NoteModel  = NoteModel()
        self.mapper: TimeMapper = TimeMapper()

        # ── 視窗狀態 ─────────────────────────────────────────────────
        self.window_start_unit: float = 0.0
        self.window_size_unit:  float = TIME_WINDOW_UNITS
        self.scroll_invert:     bool  = False
        self._min_unit: float = 0.0
        self._max_unit: float = TIME_WINDOW_UNITS

        # ── 選取 ──────────────────────────────────────────────────────
        self.selected:   Set[int]   = set()
        self.clipboard:  List[dict] = []

        # ── 框選拖曳 ──────────────────────────────────────────────────
        self._rubber_start: Optional[QPoint] = None
        self._rubber_end:   Optional[QPoint] = None
        # rubber stored in absolute units so selection follows scrolling
        self._rubber_start_u: Optional[float] = None
        self._rubber_end_u: Optional[float] = None
        self._is_rubbing:   bool = False

        # ── Ctrl+drag 複製 ────────────────────────────────────────────
        self._is_drag_copy:      bool  = False
        self._drag_start_abs_ms: float = 0.0
        self._drag_cur_delta_ms: float = 0.0
        self._drag_snap_ms:      float = 0.0

        # ── 自適應滾輪 ───────────────────────────────────────────────
        self._wheel_events:    deque = deque()
        self._wheel_hist_sec:  float = 0.6
        self._wheel_max_items: int   = 16
        self._wheel_scale:     float = 3.0
        self._wheel_min_mult:  float = 1.0
        self._wheel_max_mult:  float = 8.0

        # ── 游標位置（貼上用）────────────────────────────────────────
        self._last_mouse_unit: Optional[float] = None

        # ── Alloc Section ─────────────────────────────────────────────
        self.alloc_active:      bool         = False
        self.alloc_locked:      List[int]    = []
        self.alloc_orig:        Dict[int, Tuple] = {}
        self.alloc_target_min:  int          = 0
        self.alloc_target_max:  int          = 0
        self.alloc_time_min_u:  float        = 0.0
        self.alloc_time_max_u:  float        = 0.0
        self._alloc_drag_edge:  Optional[Tuple[str, str]] = None

        # ── Judge line ────────────────────────────────────────────────
        self._judge_ms: Optional[float] = None

        # ── 可見音符快取（供 hit-test）────────────────────────────────
        self._visible: List[Tuple[QRectF, GNote]] = []

        # ── 字型 ──────────────────────────────────────────────────────
        self._font_key   = QFont('Consolas', 7)
        self._font_time  = QFont('Consolas', 7)
        self._font_pitch = QFont('Consolas', 7, QFont.Bold)

        # 狀態列輔助文字
        self._drag_status: str = ''

        # 小節線拖曳（time_uniform 模式下）
        self._barline_dragging: bool = False
        self._barline_drag_measure: Optional[int] = None  # 目標要改變 BPM 的小節 index (0-based)
        self._barline_drag_start_ms: Optional[int] = None
        self._barline_drag_orig_end_ms: Optional[int] = None
        self._barline_drag_py: Optional[int] = None

        # ── 預覽模式 ──────────────────────────────────────────
        self.preview_mode: bool = False
        self._pix_cache: dict   = {}      # 圖片快取：檔名 → QPixmap

        # ── 時間均分模式 ──────────────────────────────────────
        self.time_uniform: bool = False
        self._time_uniform_span_ms: float = 0.0

        # ── 放置音符模式 ───────────────────────────────────────
        self._note_input_mode:     bool            = False
        self._note_duration_beats: float           = 1.0    # 四分音符
        self._note_input_hand:     int             = 0      # 0=右手
        # 放置模式預設：寬度與音符類型
        self._note_input_width:    int             = 3      # 預設 3 格寬
        self._note_input_note_type: int            = 0      # 0 = Tap
        self._note_input_hover:    object          = None   # QPoint or None

    def focusNextPrevChild(self, next: bool) -> bool:  # type: ignore[override]
        """Prevent default focus traversal on Tab so keyPressEvent receives Tab.
        Returning False stops Qt from moving focus to the next widget.
        """
        return False

    # ==================================================================
    # 公開 API
    # ==================================================================

    def load_model(self, model: NoteModel) -> None:
        self.model = model
        self.mapper = TimeMapper()
        self.mapper.build(
            model.get_beat_entries(),
            model.bpm,
            model.music_end_ms,
            model.beats_per_bar,
        )
        self.selected.clear()
        self.clipboard.clear()
        self.alloc_active = False
        self._judge_ms = None
        self._follow_mode = False
        self._note_input_mode = False
        self._note_input_hover = None
        self.setCursor(Qt.ArrowCursor)
        self._update_unit_bounds()
        self.update()
        self._emit_status()

    def toggle_time_uniform(self, enabled: bool) -> None:
        """切換時間均分模式。"""
        self.time_uniform = enabled
        if enabled:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            we_ms = self.mapper.unit_to_ms(self.window_start_unit + self.window_size_unit)
            self._time_uniform_span_ms = max(1.0, float(we_ms - ws_ms))
            self._sync_time_uniform_window_units()
        self.update()

    def _sync_time_uniform_window_units(self) -> None:
        """依固定 ms span 回填當前對應的 unit 視窗寬，供邊界/狀態使用。"""
        if not self.time_uniform:
            return
        ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
        target_we_ms = ws_ms + max(1.0, self._time_uniform_span_ms)
        we_u = self.mapper.ms_to_unit(target_we_ms)
        new_units = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS, float(we_u - self.window_start_unit)))
        self.window_size_unit = new_units

    def toggle_preview_mode(self, enabled: bool) -> None:
        """啟用/停用圖片預覽模式（覆蓋編輯畫面）。"""
        self.preview_mode = enabled
        if enabled:
            self.selected.clear()
            self.alloc_active = False
            self.selection_changed.emit(0)
        self.update()

    def rebuild_mapper(self) -> None:
        """BPM / beat_data 改變後重建 TimeMapper。"""
        self.mapper.build(
            self.model.get_beat_entries(),
            self.model.bpm,
            self.model.music_end_ms,
            self.model.beats_per_bar,
        )
        self._update_unit_bounds()
        self.update()
        self._emit_status()

    # ── 播放相關 ──────────────────────────────────────────────────────

    def set_judge_line(self, ms: Optional[float]) -> None:
        """由外部（MainWindow）更新 judge line 位置（ms）。不做自動滚動；滚動由呼叫方負責。"""
        self._judge_ms = ms
        if ms is None:
            # 停止播放時才用冞副小滚動（非 follow 模式）
            pass
        else:
            if not self._follow_mode:
                # 非跟隨模式：判定線固定在底部 10%（視覺上方 90%）
                if self.time_uniform:
                    # 時間均分：以 ms 視窗平移，避免因 unit 非線性造成視覺速度忽快忽慢
                    ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
                    span_ms = max(1.0, float(self._time_uniform_span_ms or 1.0))
                    desired_ws_ms = float(ms) - span_ms * 0.10
                    if desired_ws_ms > ws_ms:
                        self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
                        self._sync_time_uniform_window_units()
                        self._clamp_window_start()
                else:
                    cur_unit = self.mapper.ms_to_unit(ms)
                    target_y = self.window_size_unit * 0.10
                    desired_ws = cur_unit - target_y
                    if desired_ws > self.window_start_unit:
                        self.window_start_unit = desired_ws
                        self._clamp_window_start()
        self.update()

    def follow_to_ms(self, ms: float) -> None:
        """播放整首時將判定線固定在視窗底部 10%（頂部 90%），視窗隨播放滾動。"""
        if self.time_uniform:
            # 時間均分：以 ms 視窗跟隨，保持播放視覺等速
            span_ms = max(1.0, float(self._time_uniform_span_ms or 1.0))
            desired_ws_ms = float(ms) - span_ms * 0.10
            self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
            self._sync_time_uniform_window_units()
            self._clamp_window_start()
        else:
            cur_unit = self.mapper.ms_to_unit(ms)
            # 判定線固定在屏幕底部 10%：py = 0.90*height → unit_rel = 0.10*window_size
            target_rel = self.window_size_unit * 0.10
            new_ws = cur_unit - target_rel
            # follow 模式只限制往前（不進入負時間），不限制往後
            lo = min(-PRE_ROLL_UNITS, self._min_unit, 0.0)
            self.window_start_unit = max(lo, new_ws)
        self.update()

    def set_follow_mode(self, enabled: bool) -> None:
        """開啟/關閉自動跟隨模式：播放整首時傳入 True。"""
        self._follow_mode = enabled

    # ── 選取 ──────────────────────────────────────────────────────────

    def select_all(self) -> None:
        self.selected = set(range(len(self.model.notes)))
        self.update()
        self.selection_changed.emit(len(self.selected))

    def deselect_all(self) -> None:
        self.selected.clear()
        self.update()
        self.selection_changed.emit(0)

    # ── 編輯操作 ──────────────────────────────────────────────────────

    def delete_selected(self) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        self.model.notes_tree = [n for n in self.model.notes_tree
                                  if n.idx not in self.selected]
        self.model.rebuild_display_cache()
        self.selected.clear()
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(0)

    def shift_selected_keys(self, delta: int, push: bool = True) -> None:
        if not self.selected or self.alloc_active:
            return
        if push:
            self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            width = n.max_key - n.min_key
            new_min = n.min_key + delta
            new_max = n.max_key + delta
            if new_min < 0:
                new_min = 0; new_max = width
            if new_max >= TOTAL_GAME_KEYS:
                new_max = TOTAL_GAME_KEYS - 1
                new_min = max(0, new_max - width)
            n.min_key, n.max_key = new_min, new_max
        # 鍵位移動不改變時間順序，不需要 rebuild_display_cache，直接重繪
        self.update()
        self.note_edited.emit()

    def shift_selected_time(self, delta_ms: int, push: bool = True) -> None:
        if not self.selected or self.alloc_active:
            return
        if push:
            self.model.push_history()
        # 用物件參考保存選取集，避免 rebuild 後 idx 重編导致選取失效
        sel_notes = {n for n in self.model.notes_tree if n.idx in self.selected}
        for n in sel_notes:
            n.start = max(0, n.start + delta_ms)
            n.end   = max(n.start + 1, n.end + delta_ms)
            n.gate  = n.end - n.start
        self.model.rebuild_display_cache()
        self.selected = {n.idx for n in sel_notes}
        self.update()
        self.note_edited.emit()

    def shift_selected_by_32nd(self, direction: int, push: bool = True) -> None:
        if not self.selected:
            return
        bpm  = self.model.bpm if self.model.bpm > 0 else 120.0
        step = int(round(60000.0 / bpm / 8.0 * direction))

        anchor = min(
            (n for n in self.model.notes_tree if n.idx in self.selected),
            key=lambda n: n.start,
        )
        unit_before = self.mapper.ms_to_unit(float(anchor.start))

        self.shift_selected_time(step, push=push)

        unit_after = self.mapper.ms_to_unit(float(anchor.start))

        self.window_start_unit += (unit_after - unit_before)
        self._clamp_window_start()
        self.update()

    def set_type_selected(self, t: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.note_type = t
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def set_hand_selected(self, hand: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.hand = hand
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def set_width_selected(self, target_width: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            new_max = min(n.min_key + target_width - 1, TOTAL_GAME_KEYS - 1)
            n.max_key = new_max
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def shift_selected_pitch(self, delta: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected and n.pitch is not None:
                n.pitch = max(0, min(127, n.pitch + delta))
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def set_length_beats_selected(self, beats: float) -> None:
        """將所有已選音符的時長設定為指定拍數（依目前 BPM 轉算 ms）。"""
        if not self.selected or self.alloc_active:
            return
        beat_ms = 60000.0 / max(1.0, self.model.bpm)
        new_len = max(1, int(round(beats * beat_ms)))
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.end  = n.start + new_len
                n.gate = new_len
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def duplicate_selected(self) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        new_notes = []
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            clone = n.clone(len(self.model.notes_tree) + len(new_notes))
            clone.min_key = min(TOTAL_GAME_KEYS - 1, clone.min_key + 1)
            clone.max_key = min(TOTAL_GAME_KEYS - 1, clone.max_key + 1)
            new_notes.append(clone)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def duplicate_with_offset(self, offset_ms: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        prev = len(self.model.notes_tree)
        new_notes: List[GNote] = []
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            clone = n.clone(prev + len(new_notes))
            dur = max(1, clone.end - clone.start)
            clone.start = max(0, clone.start + offset_ms)
            clone.end   = clone.start + dur
            clone.gate  = dur
            new_notes.append(clone)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        # rebuild_display_cache 會重新編 idx，用物件參考取新 idx 才正確
        self.selected = {n.idx for n in new_notes}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def copy_to_clipboard(self) -> None:
        if not self.selected:
            return
        nodes = sorted(
            [n for n in self.model.notes_tree if n.idx in self.selected],
            key=lambda n: n.start,
        )
        if not nodes:
            return
        base = nodes[0].start
        self.clipboard = [{
            'rel_start': n.start - base,
            'rel_end':   n.end   - base,
            'min_key':   n.min_key,
            'max_key':   n.max_key,
            'note_type': n.note_type,
            'hand':      n.hand,
            'pitch':     n.pitch,
            'track':     n.track,
            'gate':      n.end - n.start,
        } for n in nodes]

    def paste_from_clipboard(self) -> None:
        if not self.clipboard or self.alloc_active:
            return
        if self._last_mouse_unit is not None:
            base_ms = self.mapper.unit_to_ms(self._last_mouse_unit)
        else:
            base_ms = self.mapper.unit_to_ms(self.window_start_unit)
        base_ms = max(0.0, base_ms)
        # Snap paste position to measure start (小節) when beat entries exist
        beats = self.model.get_beat_entries()
        if beats:
            try:
                epb = self.model.entries_per_bar
                measure_idx = self.model.get_measure_at_ms(base_ms)
                entry_idx = measure_idx * max(1, epb)
                if 0 <= entry_idx < len(beats):
                    base_ms = float(beats[entry_idx][1])
            except Exception:
                pass
        self.model.push_history()
        prev = len(self.model.notes_tree)
        new_notes: List[GNote] = []
        for d in self.clipboard:
            n = GNote(None, prev + len(new_notes))
            n.start     = max(0, int(base_ms + d['rel_start']))
            n.end       = max(n.start + 1, int(base_ms + d['rel_end']))
            n.gate      = n.end - n.start
            n.min_key   = d['min_key']
            n.max_key   = d['max_key']
            n.note_type = d['note_type']
            n.hand      = d['hand']
            n.pitch     = d['pitch']
            n.track     = d['track']
            new_notes.append(n)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        # rebuild_display_cache 會重新編 idx，用物件參考取新 idx 才正確
        self.selected = {n.idx for n in new_notes}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def undo(self) -> None:
        if self.model.undo():
            self.selected.clear()
            # Undo may change beat timings / time signatures; keep viewport mapping in sync.
            self.rebuild_mapper()
            self._update_unit_bounds()
            self.update()
            self.note_edited.emit()
            self.selection_changed.emit(0)
            self._emit_status()

    # ── 視窗捲動/縮放 ─────────────────────────────────────────────────

    def scroll_by(self, delta_units: float) -> None:
        self.window_start_unit += delta_units
        self._clamp_window_start()
        self.update()
        self._emit_status()

    def zoom(self, factor: float) -> None:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            old_span = max(1.0, float(self._time_uniform_span_ms or 1.0))
            old_center_ms = ws_ms + old_span * 0.5
            new_span = max(50.0, min(600000.0, old_span * factor))
            self._time_uniform_span_ms = new_span

            if self._judge_ms is not None:
                desired_ws_ms = float(self._judge_ms) - new_span * 0.10
            else:
                desired_ws_ms = old_center_ms - new_span * 0.5
            self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
            self._sync_time_uniform_window_units()
            self._clamp_window_start()
            self.update()
            self._emit_status()
            return

        old_center = self.window_start_unit + self.window_size_unit * 0.5
        new_size = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS,
                       self.window_size_unit * factor))
        self.window_size_unit = new_size
        # 播放中：以 judge line 重新對齊（底部 10%），維持 follow 效果
        if self._judge_ms is not None:
            cur_unit = self.mapper.ms_to_unit(self._judge_ms)
            target_rel = new_size * 0.10
            self.window_start_unit = cur_unit - target_rel
        else:
            # 非播放：以原視窗中心做縮放基準
            self.window_start_unit = old_center - new_size * 0.5
        self._clamp_window_start()
        self.update()
        self._emit_status()

    # ── Alloc Section ─────────────────────────────────────────────────

    def start_alloc_section(self) -> None:
        if self.alloc_active:
            return
        # `self.selected` 存的是 display cache (`self.model.notes`) 的 idx。
        # 不能直接當成 notes_tree 的索引使用，必須把選取的 display idx 映射
        # 回 notes_tree 的實際位置（index）。否則會修改到錯誤的音符，造成
        # 譜面寬度/位置異常。
        locked_notes = [n for n in self.model.notes if n.idx in self.selected]
        locked_indices = [self.model.notes_tree.index(n) for n in locked_notes if n in self.model.notes_tree]
        locked_indices = sorted(locked_indices)
        if not locked_indices:
            return
        self.alloc_active = True
        self.alloc_locked = locked_indices
        self.alloc_orig = {}
        for i in locked_indices:
            n = self.model.notes_tree[i]
            self.alloc_orig[i] = (n.min_key, n.max_key, n.pitch)
        self.alloc_target_min = min(self.model.notes_tree[i].min_key for i in locked_indices)
        self.alloc_target_max = max(self.model.notes_tree[i].max_key for i in locked_indices)
        # Preserve overall chart key range so alloc won't change total width
        try:
            # Preserve left bound as existing content, but allow the right bound
            # to expand up to the full key range so alloc can reach the very
            # rightmost key. This addresses reports that alloc could not drag
            # to the extreme right.
            self._preserve_min_key = min(n.min_key for n in self.model.notes_tree)
            self._preserve_max_key = TOTAL_GAME_KEYS - 1
        except Exception:
            self._preserve_min_key = 0
            self._preserve_max_key = TOTAL_GAME_KEYS - 1
        starts = [self.mapper.ms_to_unit(float(self.model.notes_tree[i].start)) for i in locked_indices]
        ends   = [self.mapper.ms_to_unit(float(self.model.notes_tree[i].end))   for i in locked_indices]
        self.alloc_time_min_u = min(starts)
        self.alloc_time_max_u = max(ends)
        self._alloc_drag_edge = None
        self._apply_alloc_dist()
        self.update()
        self._drag_status = 'Alloc Section：拖曳紅框邊界。Enter 確認 / Esc 取消'
        self._emit_status()

    def resort_all_notes(self) -> None:
        """全譜重整：將所有音符依音高由左到右重新醒套鍵位，
        保留原始寬度與單個音符寬度，關鍵範圍跟原譜面相同。"""
        if not self.model.notes_tree:
            return
        self.model.push_history()
        notes = self.model.notes_tree

        # 範圍：維持原譜面的整體 min_key / max_key
        preserve_mn = min(n.min_key for n in notes)
        preserve_mx = max(n.max_key for n in notes)
        span = preserve_mx - preserve_mn

        # 對每個音符記錄原始寬度
        orig_w = {id(n): n.max_key - n.min_key for n in notes}

        # 依音高分組
        groups: dict = {}
        for n in notes:
            groups.setdefault(n.pitch, []).append(n)

        pitches_sorted = sorted(p for p in groups if p is not None)
        n_p = len(pitches_sorted)
        rank_frac = {p: (i / max(n_p - 1, 1)) for i, p in enumerate(pitches_sorted)}
        if n_p == 1:
            rank_frac[pitches_sorted[0]] = 0.5

        for p, group_notes in groups.items():
            frac = rank_frac.get(p, 0.5) if p is not None else 0.5
            kpos = preserve_mn + int(round(frac * span))
            kpos = max(preserve_mn, min(preserve_mx, kpos))
            for n in group_notes:
                w = orig_w[id(n)]
                new_min = kpos
                new_max = kpos + w
                if new_max > TOTAL_GAME_KEYS - 1:
                    new_max = TOTAL_GAME_KEYS - 1
                    new_min = max(0, new_max - w)
                if new_min < 0:
                    new_min = 0
                    new_max = min(TOTAL_GAME_KEYS - 1, w)
                # clamp 到 preserve 範圍
                if new_min < preserve_mn:
                    new_min = preserve_mn
                    new_max = new_min + w
                if new_max > preserve_mx:
                    new_max = preserve_mx
                    new_min = new_max - w
                n.min_key = int(new_min)
                n.max_key = int(new_max)

        self.model.rebuild_display_cache()
        self._update_unit_bounds()   # 更新捲動上下界
        self.selected.clear()
        self.update()
        self.selection_changed.emit(0)
        self._emit_status()

    # ── 放置音符模式 ──────────────────────────────────────────────────

    def set_note_input_mode(self, enabled: bool) -> None:
        """開啟或關閉放置音符模式（點擊即可在拍子位置新增音符）。"""
        self._note_input_mode = enabled
        if enabled:
            self.alloc_active = False
            self._alloc_drag_edge = None
            from PyQt5.QtCore import Qt as _Qt
            self.setCursor(_Qt.CrossCursor)
        else:
            from PyQt5.QtCore import Qt as _Qt
            self.setCursor(_Qt.ArrowCursor)
            self._note_input_hover = None
        self.note_input_changed.emit(enabled)
        self._emit_status()

    def set_note_duration(self, beats: float) -> None:
        """設定放置音符模式的音符時值（單位：拍次）。"""
        self._note_duration_beats = max(1.0 / 64, float(beats))

    def set_note_input_hand(self, hand: int) -> None:
        """設定放置音符預設手（0=右 1=左）。"""
        self._note_input_hand = hand

    def set_note_input_width(self, width: int) -> None:
        """設定放置音符預設寬度（格數）。"""
        try:
            w = int(width)
        except Exception:
            return
        self._note_input_width = max(1, min(int(TOTAL_GAME_KEYS), w))

    def set_note_input_note_type(self, note_type: int) -> None:
        """設定放置音符預設類型（0=tap,1=soft,2=long,3=staccato）。"""
        try:
            t = int(note_type)
        except Exception:
            return
        self._note_input_note_type = max(0, min(3, t))

    # ------------------------------------------------------------------

    def _beat_in_units(self) -> float:
        """1 拍 = 幾個 unit。
        per-beat 格式（原始遊戲檔）：1 unit = 1 拍 → 回傳 1.0
        per-bar  格式（新增譜面）  ：1 unit = beats_per_bar 拍 → 回傳 1/bpb"""
        if self.model.root is None:
            return 1.0 / max(1, self.model.beats_per_bar)
        epb = self.model.entries_per_bar
        if epb <= 1:
            # per-bar：1 unit = beats_per_bar 拍
            return 1.0 / max(1, self.model.beats_per_bar)
        return 1.0  # per-beat：1 unit = 1 拍

    def _beat_in_units_at(self, unit: float) -> float:
        """回傳指定 unit 位置下 1 拍對應的 unit 長度。
        在小節均分模式下，會依該小節拍號動態變化（例如 15/4 比 4/4 更密）。"""
        # 時間均分已正常，沿用現行行為
        if self.time_uniform:
            return self._beat_in_units()
        try:
            ms = float(self.mapper.unit_to_ms(float(unit)))
            bpb = max(1, int(self.model.get_beats_per_bar_at_ms(ms)))
            return 1.0 / float(bpb)
        except Exception:
            return self._beat_in_units()

    def _snap_unit_to_duration(self, unit: float, duration_beats: float) -> float:
        """將 unit 吸附到最近的 duration_beats 倍數（拍次格線）。"""
        if duration_beats <= 0:
            return unit
        snap = duration_beats * self._beat_in_units_at(unit)
        return round(unit / snap) * snap

    def _infer_pitch_from_key(self, key_f: float) -> int:
        """依點擊鍵位，以全譜 alloc 映射推算最近音高。
        若譜面無任何音高資料，則直接線性映射到 1~88（鋼琴標準鍵數）。"""
        notes = self.model.notes_tree if (self.model and self.model.notes_tree) else []
        pitches = sorted(set(n.pitch for n in notes if n.pitch is not None))
        if not pitches:
            # 無參考音高：線性映射 key 0~TOTAL_GAME_KEYS 到 pitch 1~88
            frac = max(0.0, min(1.0, key_f / max(TOTAL_GAME_KEYS, 1)))
            return max(1, int(round(1 + frac * 87)))
        n_p = len(pitches)
        try:
            preserve_mn = min(n.min_key for n in notes)
            preserve_mx = max(n.max_key for n in notes)
        except Exception:
            preserve_mn = 0
            preserve_mx = TOTAL_GAME_KEYS - 1
        span = max(preserve_mx - preserve_mn, 1)
        frac = max(0.0, min(1.0, (key_f - preserve_mn) / span))
        idx  = int(round(frac * (n_p - 1)))
        return pitches[max(0, min(n_p - 1, idx))]

    def _place_note_at(self, pos: 'QPoint') -> None:
        """在游標位置（拍子 snap）新增一個音符。"""
        raw_unit     = self._py_to_unit_abs(pos.y())
        snapped_unit = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        snapped_ms   = self.mapper.unit_to_ms(snapped_unit)
        dur_units    = self._note_duration_beats * self._beat_in_units_at(snapped_unit)
        end_ms       = self.mapper.unit_to_ms(snapped_unit + dur_units)
        dur_ms       = max(10.0, end_ms - snapped_ms)

        key_f   = self._px_to_key(pos.x())
        center  = max(0, min(TOTAL_GAME_KEYS - 1, int(key_f)))
        half = self._note_input_width // 2
        min_key = max(0, center - half)
        max_key = min(TOTAL_GAME_KEYS - 1, min_key + self._note_input_width - 1)
        pitch   = self._infer_pitch_from_key(key_f)

        self.model.push_history()
        n = GNote(None, len(self.model.notes_tree))
        n.start     = max(0, int(round(snapped_ms)))
        n.end       = max(n.start + 1, int(round(snapped_ms + dur_ms)))
        n.gate      = n.end - n.start
        n.min_key   = min_key
        n.max_key   = max_key
        n.pitch     = pitch
        n.note_type = self._note_input_note_type
        n.hand      = self._note_input_hand

        self.model.notes_tree.append(n)
        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self.selected = {n.idx}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(1)

    def _draw_note_input_cursor(self, qp: 'QPainter') -> None:
        """在游標位置畫 snap 指示線；預覽模式下使用圖示 ghost。"""
        if self._note_input_hover is None:
            return
        pos = self._note_input_hover
        raw_unit     = self._py_to_unit_abs(pos.y())
        snapped_unit = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        snapped_rel  = snapped_unit - self.window_start_unit
        key_f        = self._px_to_key(pos.x())
        center       = max(0, min(TOTAL_GAME_KEYS - 1, int(key_f)))
        min_key = max(0, center - (self._note_input_width // 2))
        max_key      = min(TOTAL_GAME_KEYS - 1, min_key + self._note_input_width - 1)
        pitch        = self._infer_pitch_from_key(key_f)

        snap_y  = int(self._unit_to_py(snapped_rel))
        key_x   = int(self._key_to_px(min_key))
        key_x2  = int(self._key_to_px(max_key + 1))
        w = self.width()

        # 水平 snap 線（紅色虛線）
        from PyQt5.QtGui import QPen, QColor
        from PyQt5.QtCore import Qt
        qp.setPen(QPen(QColor(255, 80, 80, 200), 1, Qt.DashLine))
        qp.drawLine(0, snap_y, w, snap_y)

        # 鍵位方格預覽（編輯模式）/ 圖示 ghost（預覽模式）
        dur_unit = self._note_duration_beats * self._beat_in_units_at(snapped_unit)
        end_unit = snapped_unit + dur_unit - self.window_start_unit
        note_top = int(self._unit_to_py(end_unit))
        note_bot = snap_y
        if note_bot > note_top:
            if self.preview_mode:
                ghost = GNote(None, -1)
                ghost.start = max(0, int(round(self.mapper.unit_to_ms(snapped_unit))))
                ghost.end = max(ghost.start + 1, int(round(self.mapper.unit_to_ms(snapped_unit + dur_unit))))
                ghost.gate = max(1, ghost.end - ghost.start)
                ghost.min_key = min_key
                ghost.max_key = max_key
                ghost.note_type = int(self._note_input_note_type)
                ghost.hand = int(self._note_input_hand)
                ghost.pitch = pitch

                qp.save()
                qp.setOpacity(0.72)
                if ghost.note_type == 2:
                    self._preview_hold_body(qp, ghost)
                self._preview_note_head(qp, ghost)
                if ghost.note_type == 3:
                    stac_img = self._get_pix('LeftStac.png' if ghost.hand == 1 else 'RightStac.png')
                    stac_rect = self._preview_stac_rect(ghost)
                    if stac_rect is not None and not stac_img.isNull():
                        qp.drawPixmap(stac_rect.toRect(), stac_img)
                qp.restore()

                qp.setPen(QPen(QColor(255, 220, 80, 220), 1))
                qp.setBrush(Qt.NoBrush)
                for pr in self._preview_part_rects(ghost):
                    qp.drawRect(pr)
            else:
                from PyQt5.QtCore import QRect as _QR
                color = QColor(255, 100, 100, 80) if self._note_input_hand == 0 else QColor(100, 160, 255, 80)
                qp.fillRect(_QR(key_x, note_top,
                                max(1, key_x2 - key_x),
                                max(1, note_bot - note_top)), color)
                qp.setPen(QPen(QColor(255, 80, 80, 200), 1))
                qp.drawRect(_QR(key_x, note_top,
                                max(1, key_x2 - key_x),
                                max(1, note_bot - note_top)))

        # 提示文字
        snapped_ms  = self.mapper.unit_to_ms(snapped_unit)
        pitch_str   = str(pitch) if pitch is not None else '-'
        qp.setPen(QColor(255, 200, 60))
        from PyQt5.QtGui import QFont
        qp.setFont(QFont('Consolas', 8))
        qp.drawText(4, self.height() - 22,
                    f'✏ snap={int(snapped_ms)}ms  key={min_key}~{max_key}  '
                    f'pitch={pitch_str}  dur={self._note_duration_beats:.4g}beat  '
                    f'hand={"右" if self._note_input_hand == 0 else "左"}')

    def confirm_alloc_section(self) -> None:
        if not self.alloc_active:
            return
        final = {}
        for i in self.alloc_locked:
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                final[i] = (n.min_key, n.max_key, n.pitch)
        self._restore_alloc_orig()
        self.model.push_history()
        for i, (mn, mx, pt) in final.items():
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                n.min_key, n.max_key, n.pitch = mn, mx, pt
        self.alloc_active = False
        self.alloc_locked.clear()
        self.alloc_orig.clear()
        self._alloc_drag_edge = None
        # clear preserve fields
        if hasattr(self, '_preserve_min_key'):
            delattr = False
            try:
                del self._preserve_min_key
                del self._preserve_max_key
            except Exception:
                pass
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()
        self._drag_status = ''
        self._emit_status()

    def cancel_alloc_section(self) -> None:
        if not self.alloc_active:
            return
        self._restore_alloc_orig()
        self.alloc_active = False
        self.alloc_locked.clear()
        self.alloc_orig.clear()
        self._alloc_drag_edge = None
        # clear preserve fields
        try:
            del self._preserve_min_key
            del self._preserve_max_key
        except Exception:
            pass
        self.model.rebuild_display_cache()
        self.update()
        self._drag_status = ''
        self._emit_status()

    def _restore_alloc_orig(self) -> None:
        for i, (mn, mx, pt) in self.alloc_orig.items():
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                n.min_key, n.max_key, n.pitch = mn, mx, pt

    def _apply_alloc_dist(self) -> None:
        if not self.alloc_locked:
            return
        mn = max(0, min(TOTAL_GAME_KEYS - 1, int(round(self.alloc_target_min))))
        mx = max(mn, min(TOTAL_GAME_KEYS - 1, int(round(self.alloc_target_max))))
        self.alloc_target_min, self.alloc_target_max = mn, mx
        span = mx - mn

        groups: Dict[Optional[int], List[int]] = {}
        for i in self.alloc_locked:
            if 0 <= i < len(self.model.notes_tree):
                p = self.model.notes_tree[i].pitch
                groups.setdefault(p, []).append(i)

        # 以音高排名均分：不依音高數值差，而是依排序位置等距分配
        pitches_sorted = sorted(p for p in groups if p is not None)
        n_p = len(pitches_sorted)
        rank_frac: Dict[int, float] = {
            p: (i / max(n_p - 1, 1)) for i, p in enumerate(pitches_sorted)
        }

        for p, indices in groups.items():
            frac = rank_frac[p] if (p is not None and p in rank_frac) else 0.5
            kpos = mn + int(round(frac * span))
            kpos = max(mn, min(mx, kpos))
            for i in indices:
                if 0 <= i < len(self.model.notes_tree):
                    note = self.model.notes_tree[i]
                    # 使用原始寬度，且不 clip max_key，完全保留音符寬度
                    orig_mn, orig_mx, _ = self.alloc_orig.get(
                        i, (note.min_key, note.max_key, note.pitch))
                    w = orig_mx - orig_mn
                    new_min = kpos
                    new_max = kpos + w
                    # 超出右邊界 → 先 clip 到 TOTAL_GAME_KEYS 範圍，維持寬度
                    if new_max > TOTAL_GAME_KEYS - 1:
                        new_max = TOTAL_GAME_KEYS - 1
                        new_min = new_max - w
                    # 超出左邊界
                    if new_min < 0:
                        new_min = 0
                        new_max = w
                    # 若有 preserve 範圍（進入 Alloc 時記錄），則進一步 clip 到 preserve 範圍內
                    pmn = getattr(self, '_preserve_min_key', None)
                    pmx = getattr(self, '_preserve_max_key', None)
                    if pmn is not None and pmx is not None:
                        if new_min < pmn:
                            new_min = pmn
                            new_max = new_min + w
                        if new_max > pmx:
                            new_max = pmx
                            new_min = new_max - w
                    note.min_key = new_min
                    note.max_key = new_max
        # After redistribution, ensure overall chart width remains the same
        try:
            cur_min = min(n.min_key for n in self.model.notes_tree)
            cur_max = max(n.max_key for n in self.model.notes_tree)
            pmn = getattr(self, '_preserve_min_key', None)
            pmx = getattr(self, '_preserve_max_key', None)
            if pmn is not None and pmx is not None:
                # If current range exceeds preserved range, shift all notes to fit
                if cur_min < pmn or cur_max > pmx:
                    # desired shift to map current range -> preserved range
                    desired_shift = (pmn - cur_min) if (cur_min < pmn) else (pmx - cur_max)
                    # Prefer shifting only the selected (locked) notes so alloc can
                    # move them to the chart edge even when other notes occupy
                    # space. Fall back to uniform shift if no locked notes.
                    try:
                        if self.alloc_locked:
                            # compute allowed shift for locked notes only
                            sel_notes = [self.model.notes_tree[i] for i in self.alloc_locked
                                         if 0 <= i < len(self.model.notes_tree)]
                            if sel_notes:
                                min_sel = min(n.min_key for n in sel_notes)
                                max_sel = max(n.max_key for n in sel_notes)
                                min_allowed = -min_sel
                                max_allowed = (TOTAL_GAME_KEYS - 1) - max_sel
                                shift = max(min(desired_shift, max_allowed), min_allowed)
                                for i in self.alloc_locked:
                                    if 0 <= i < len(self.model.notes_tree):
                                        n = self.model.notes_tree[i]
                                        n.min_key = int(n.min_key + shift)
                                        n.max_key = int(n.max_key + shift)
                            else:
                                raise Exception("no selected notes")
                        else:
                            raise Exception("no locked notes")
                    except Exception:
                        # fallback: uniform shift across all notes (old behavior)
                        try:
                            min_allowed = -min(n.min_key for n in self.model.notes_tree)
                            max_allowed = (TOTAL_GAME_KEYS - 1) - max(n.max_key for n in self.model.notes_tree)
                        except Exception:
                            min_allowed = -TOTAL_GAME_KEYS
                            max_allowed = TOTAL_GAME_KEYS
                        shift = max(min(desired_shift, max_allowed), min_allowed)
                        for n in self.model.notes_tree:
                            n.min_key = int(n.min_key + shift)
                            n.max_key = int(n.max_key + shift)
        except Exception:
            pass
        self.model.rebuild_display_cache()

    def _update_alloc_edge(self, axis: str, side: str, raw: float) -> None:
        if axis == 'x':
            v = max(0, min(TOTAL_GAME_KEYS - 1, int(round(raw))))
            if side == 'min':
                self.alloc_target_min = min(v, self.alloc_target_max)
            else:
                self.alloc_target_max = max(v, self.alloc_target_min)
            self._apply_alloc_dist()
        else:
            if side == 'min':
                self.alloc_time_min_u = min(raw, self.alloc_time_max_u - 0.05)
            else:
                self.alloc_time_max_u = max(raw, self.alloc_time_min_u + 0.05)
        self.update()

    # ==================================================================
    # 座標轉換
    # ==================================================================

    def _unit_to_py(self, unit_rel: float) -> float:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            ms_range = max(float(self._time_uniform_span_ms or 1.0), 1e-9)
            cur_ms = self.mapper.unit_to_ms(self.window_start_unit + unit_rel)
            return self.height() * (1.0 - (cur_ms - ws_ms) / ms_range)
        return self.height() * (1.0 - unit_rel / max(self.window_size_unit, 1e-9))

    def _py_to_unit_abs(self, py: float) -> float:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            ms_range = max(float(self._time_uniform_span_ms or 1.0), 1e-9)
            frac = 1.0 - py / max(self.height(), 1)
            target_ms = ws_ms + frac * ms_range
            return self.mapper.ms_to_unit(target_ms)
        rel = (1.0 - py / max(self.height(), 1)) * self.window_size_unit
        return self.window_start_unit + rel

    def _key_to_px(self, key: float) -> float:
        return key * self.width() / TOTAL_GAME_KEYS

    def _px_to_key(self, px: float) -> float:
        return px * TOTAL_GAME_KEYS / max(self.width(), 1)

    def _note_rect(self, n: GNote) -> Optional[QRectF]:
        start_u = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        end_u   = self.mapper.ms_to_unit(float(n.end))   - self.window_start_unit
        win = self.window_size_unit
        if end_u < 0 or start_u > win:
            return None
        x1 = self._key_to_px(n.min_key)
        x2 = self._key_to_px(n.max_key + 1)
        y_top    = self._unit_to_py(end_u)
        y_bottom = self._unit_to_py(start_u)
        w = max(1.0, x2 - x1)
        h = max(float(MIN_NOTE_HEIGHT_PX), y_bottom - y_top)
        return QRectF(x1, y_top, w, h)

    # ==================================================================
    # 視窗邊界
    # ==================================================================

    def _update_unit_bounds(self) -> None:
        # 以 music_end_ms 與音符範圍取最大值，確保新增小節後能捲到末端
        end_ms = getattr(self.model, 'music_end_ms', 0.0) or 0.0
        end_unit = self.mapper.ms_to_unit(end_ms)
        notes = self.model.notes
        if notes:
            mn, mx = self.mapper.unit_range_of_notes(notes)
            self._min_unit = mn
            self._max_unit = max(mx, end_unit)
        else:
            self._min_unit = 0.0
            self._max_unit = max(end_unit, self.window_size_unit)
        self._clamp_window_start()

    def _clamp_window_start(self) -> None:
        try:
            lo = min(-PRE_ROLL_UNITS, self._min_unit, 0.0)
            hi = max(lo, self._max_unit - self.window_size_unit)
            self.window_start_unit = max(lo, min(self.window_start_unit, hi))
        except Exception:
            pass

    def _window_ms(self) -> Tuple[float, float]:
        return (self.mapper.unit_to_ms(self.window_start_unit),
                self.mapper.unit_to_ms(self.window_start_unit + self.window_size_unit))

    # ==================================================================
    # 工具
    # ==================================================================

    def _sixteenth_ms(self) -> float:
        bpm = self.model.bpm if self.model.bpm > 0 else 120.0
        return 60000.0 / bpm / 4.0

    def _quantize(self, delta_ms: float, snap_ms: float) -> float:
        if snap_ms <= 0:
            return delta_ms
        return round(delta_ms / snap_ms) * snap_ms

    def _wheel_multiplier(self) -> float:
        import time as _t
        now = _t.time()
        while self._wheel_events and (
            now - self._wheel_events[0][0] > self._wheel_hist_sec
            or len(self._wheel_events) > self._wheel_max_items
        ):
            self._wheel_events.popleft()
        if not self._wheel_events:
            return self._wheel_min_mult
        times = [t for t, _ in self._wheel_events]
        steps = [s for _, s in self._wheel_events]
        span  = max(1e-3, times[-1] - times[0])
        rate  = sum(steps) / span
        mult  = 1.0 + rate / self._wheel_scale
        return max(self._wheel_min_mult, min(self._wheel_max_mult, mult))

    def _scroll_step_units(self) -> float:
        """自適應滾動步長：視窗大小的 1.5%，縮放愈大（視窗愈小）步長愈細緻。
        範圍 clamp 至 [0.01, 0.5] units，鍵盤 Shift 另行乘 4。"""
        return max(0.01, min(0.5, self.window_size_unit * 0.015))

    def _emit_status(self) -> None:
        ws_ms, we_ms = self._window_ms()
        ws_u = self.window_start_unit
        we_u = ws_u + self.window_size_unit
        extra = f'  [{self._drag_status}]' if self._drag_status else ''
        msg = (t('status_window',
                 int(ws_ms), int(we_ms),
                 ws_u, we_u,
                 self.window_size_unit,
                 len(self.selected),
                 self.model.bpm) + extra)
        self.status_changed.emit(msg)

    # ==================================================================
    # 繪製
    # ==================================================================

    def paintEvent(self, _: QPaintEvent) -> None:
        self._visible.clear()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, False)
        self._draw_bg(qp)
        self._draw_grid(qp)
        if self.preview_mode:
            self._draw_notes_preview(qp)
            self._draw_preview_overlay(qp)
            # Draw selection outlines on top of the preview overlay so they remain visible
            qp.setPen(QPen(SEL_OUTLINE, 2))
            qp.setBrush(Qt.NoBrush)
            for rect, n in self._visible:
                if n.idx in self.selected:
                    for pr in self._preview_part_rects(n):
                        qp.drawRect(pr)
            # Draw rubber-band selection rectangle (mouse drag) in preview mode
            self._draw_rubber(qp)
            if self._note_input_mode:
                self._draw_note_input_cursor(qp)
        else:
            self._draw_notes(qp)
            self._draw_alloc_overlay(qp)
            self._draw_rubber(qp)
            if self._is_drag_copy:
                self._draw_drag_info(qp)
            if self._note_input_mode:
                self._draw_note_input_cursor(qp)
        self._draw_judge_line(qp)

        # Draw beat/bar lines and BPM labels last so they appear above notes.
        self._draw_beat_lines(qp)

        # 若正在拖曳小節線，疊加顯示移動中的小節線與 BPM 提示
        if getattr(self, '_barline_dragging', False) and self._barline_drag_py is not None:
            qp.setPen(QPen(QColor(255, 120, 60), 2, Qt.SolidLine))
            qp.drawLine(0, int(self._barline_drag_py), self.width(), int(self._barline_drag_py))
            try:
                # 嘗試計算暫時 BPM 並顯示在狀態列
                if self._barline_drag_start_ms is not None:
                    cur_ms = self.mapper.unit_to_ms(self._py_to_unit_abs(self._barline_drag_py))
                    new_dur = max(1, int(round(cur_ms - float(self._barline_drag_start_ms))))
                    num = self.model.get_beats_per_bar_at_ms(int(self._barline_drag_start_ms))
                    den = self.model.time_sig_denominator
                    new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                    qp.setPen(TIME_LABEL)
                    qp.drawText(6, int(self._barline_drag_py) - 6, f'{new_bpm:.2f} BPM')
            except Exception:
                pass

    def _draw_bg(self, qp: QPainter) -> None:
        qp.fillRect(self.rect(), BG_COLOR)

    def _draw_grid(self, qp: QPainter) -> None:
        h = self.height()
        qp.setFont(self._font_key)
        for i in range(TOTAL_GAME_KEYS + 1):
            x = int(self._key_to_px(i))
            qp.setPen(QPen(GRID_MAJOR if i % 4 == 0 else GRID_MINOR, 1))
            qp.drawLine(x, 0, x, h)
            if i < TOTAL_GAME_KEYS:
                qp.setPen(KEY_LABEL)
                qp.drawText(x + 2, 11, str(i))

    def _draw_beat_lines(self, qp: QPainter) -> None:
        w = self.width()
        start_b = math.floor(self.window_start_unit)
        end_b   = math.ceil(self.window_start_unit + self.window_size_unit) + 1
        qp.setFont(self._font_time)

        # If time_uniform is enabled, draw based on actual ms beat timings
        if self.time_uniform:
            ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
            beats = self.model.get_beat_entries()
            if beats:
                prev_measure = None
                # draw beat lines for beats falling within visible ms range
                for idx, bms in beats:
                    if bms < ws_ms - 1 or bms > we_ms + 1:
                        continue
                    unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                    py = int(self._unit_to_py(unit))
                    if not (-2 <= py <= self.height() + 2):
                        continue
                    # determine if this beat is a bar start
                    measure_idx = self.model.get_measure_at_ms(bms)
                    bar_no = measure_idx + 1
                    is_bar = (prev_measure is None) or (measure_idx != prev_measure)
                    prev_measure = measure_idx
                    if is_bar:
                        # time signature at current bar start
                        ts_num = self.model.get_beats_per_bar_at_ms(float(bms))
                        ts_den = self.model.time_sig_denominator
                        for ch_ms, _ch_num, ch_den in self.model.time_sig_changes:
                            if ch_ms <= int(bms):
                                ts_den = ch_den
                            else:
                                break
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
                        tot_s = bms / 1000.0
                        m = int(tot_s // 60)
                        s = tot_s - m * 60
                        try:
                            bar_bpm = self.model.get_measure_bpm(bar_no - 1)
                            bpm_str = f'  {bar_bpm:.1f}BPM'
                        except Exception:
                            bpm_str = ''
                        qp.setPen(TIME_LABEL)
                        qp.drawText(2, py - 2, f'{m}:{s:05.2f}  {ts_num}/{ts_den}{bpm_str}')
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawText(w - 30, py - 2, str(bar_no))
                    else:
                        qp.setPen(QPen(BEATLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
        else:
            # In measure-uniform mode, also derive lines from beat timings + measure changes
            # to avoid missing labels under variable time signatures.
            ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
            beats = self.model.get_beat_entries()
            if beats:
                prev_measure = None
                for _idx, bms in beats:
                    if bms < ws_ms - 1 or bms > we_ms + 1:
                        continue
                    unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                    py = int(self._unit_to_py(unit))
                    if not (-2 <= py <= self.height() + 2):
                        continue

                    measure_idx = self.model.get_measure_at_ms(bms)
                    bar_no = measure_idx + 1
                    is_bar = (prev_measure is None) or (measure_idx != prev_measure)
                    prev_measure = measure_idx

                    if is_bar:
                        ts_num = self.model.get_beats_per_bar_at_ms(float(bms))
                        ts_den = self.model.time_sig_denominator
                        for ch_ms, _ch_num, ch_den in self.model.time_sig_changes:
                            if ch_ms <= int(bms):
                                ts_den = ch_den
                            else:
                                break
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
                        tot_s = bms / 1000.0
                        m = int(tot_s // 60)
                        s = tot_s - m * 60
                        try:
                            bar_bpm = self.model.get_measure_bpm(bar_no - 1)
                            bpm_str = f'  {bar_bpm:.1f}BPM'
                        except Exception:
                            bpm_str = ''
                        qp.setPen(TIME_LABEL)
                        qp.drawText(2, py - 2, f'{m}:{s:05.2f}  {ts_num}/{ts_den}{bpm_str}')
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawText(w - 30, py - 2, str(bar_no))
                    else:
                        qp.setPen(QPen(BEATLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)

    def _note_colors(self, n: GNote) -> Tuple[QColor, QColor]:
        nt = n.note_type
        if nt == 1:
            return NOTE_SOFT, NOTE_OUT_S
        if nt == 2:
            return (NOTE_RIGHT_LONG, NOTE_OUT_R) if n.hand == 0 else (NOTE_LEFT_LONG, NOTE_OUT_L)
        if nt == 3:
            return NOTE_STAC, NOTE_OUT_S
        return (NOTE_RIGHT, NOTE_OUT_R) if n.hand == 0 else (NOTE_LEFT, NOTE_OUT_L)

    def _draw_notes(self, qp: QPainter) -> None:
        qp.setFont(self._font_pitch)

        # Pass 1：畫所有音符方塊
        for n in self.model.notes:
            rect = self._note_rect(n)
            if rect is None:
                continue
            self._visible.append((rect, n))
            fill, outline = self._note_colors(n)
            selected = n.idx in self.selected
            qp.setBrush(QBrush(fill))
            qp.setPen(QPen(SEL_OUTLINE, 2) if selected else QPen(outline, 1))
            qp.drawRect(rect)

        # Pass 2：畫所有音高文字（疊在最上層，不被其他音符遮擋，允許超出音符範圍）
        qp.setPen(PITCH_TEXT)
        for rect, n in self._visible:
            if n.pitch is None:
                continue
            # 以音符中心為基準，給一個固定大小的繪製區域，不受音符尺寸限制
            cx = rect.center().x()
            cy = rect.center().y()
            text_rect = QRectF(cx - 16, cy - 8, 32, 16)
            qp.drawText(text_rect.toRect(), Qt.AlignCenter, str(n.pitch))

    def _draw_alloc_overlay(self, qp: QPainter) -> None:
        if not self.alloc_active:
            return
        t_min_rel = self.alloc_time_min_u - self.window_start_unit
        t_max_rel = self.alloc_time_max_u - self.window_start_unit
        y_top    = self._unit_to_py(t_max_rel)
        y_bottom = self._unit_to_py(t_min_rel)
        # Clamp drawing to widget bounds to avoid visual overflow
        w = float(self.width())
        x_left_raw = self._key_to_px(self.alloc_target_min)
        x_right_raw = self._key_to_px(self.alloc_target_max + 1)
        x_left = max(0.0, min(w, x_left_raw))
        x_right = max(0.0, min(w, x_right_raw))
        rect_w = max(0.0, x_right - x_left)
        qp.setPen(QPen(ALLOC_COLOR, 2, Qt.DashLine))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(QRectF(x_left, y_top, rect_w, y_bottom - y_top))

    def _draw_judge_line(self, qp: QPainter) -> None:
        if self._judge_ms is None:
            return
        unit = self.mapper.ms_to_unit(self._judge_ms)
        rel  = unit - self.window_start_unit
        if not (-0.5 < rel < self.window_size_unit + 0.5):
            return
        py = int(self._unit_to_py(rel))
        qp.setPen(QPen(JUDGELINE_COLOR, 2))
        qp.drawLine(0, py, self.width(), py)

    def _draw_rubber(self, qp: QPainter) -> None:
        if not self._is_rubbing:
            return
        # If we have stored absolute unit positions, compute current pixel y
        if self._rubber_start_u is not None and self._rubber_end_u is not None:
            try:
                rel_s = float(self._rubber_start_u) - float(self.window_start_unit)
                rel_e = float(self._rubber_end_u)   - float(self.window_start_unit)
                y1 = int(self._unit_to_py(rel_s))
                y2 = int(self._unit_to_py(rel_e))
            except Exception:
                return
            # preserve original x positions if available
            x1 = int(self._rubber_start.x()) if self._rubber_start is not None else 0
            x2 = int(self._rubber_end.x())   if self._rubber_end is not None else self.width()
            r = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()
        else:
            if not self._rubber_start or not self._rubber_end:
                return
            r = QRect(self._rubber_start, self._rubber_end).normalized()
        qp.setPen(QPen(RUBBER_COLOR, 1, Qt.DashLine))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(r)

    def _draw_drag_info(self, qp: QPainter) -> None:
        delta = int(round(self._drag_cur_delta_ms))
        snap  = int(round(self._drag_snap_ms)) if self._drag_snap_ms > 0 else 0
        pre   = '+' if delta >= 0 else ''
        qp.setPen(QColor(255, 200, 0))
        qp.setFont(QFont('Consolas', 9))
        qp.drawText(8, self.height() - 8,
                    f'Ctrl+拖曳複製  Δ{pre}{delta}ms  snap={snap}ms')

    # ==================================================================
    # 預覽模式繪製
    # ==================================================================

    def _get_pix(self, name: str) -> QPixmap:
        """懶載入 graphic/ 資料夾的圖片，並快取。"""
        if name not in self._pix_cache:
            path = os.path.join(os.path.dirname(__file__), 'graphic', name)
            self._pix_cache[name] = QPixmap(path)
        return self._pix_cache[name]

    def _preview_note_xw(self, n: GNote, scale: float):
        """回傳 (x, draw_w)：以 scale 縮放後置中於原始格寬內。"""
        x1 = self._key_to_px(n.min_key)
        x2 = self._key_to_px(n.max_key + 1)
        full_w = x2 - x1
        draw_w = full_w * scale
        x = x1 + (full_w - draw_w) * 0.5
        return x, draw_w

    def _preview_hold_body(self, qp: QPainter, n: GNote) -> None:
        """Hold 主體：Lefthold/Righthold 垂直拉伸至 endtime，寬度 0.8。"""
        rect = self._preview_hold_body_rect(n)
        if rect is None:
            return
        img_name = 'Lefthold.png' if n.hand == 1 else 'Righthold.png'
        img = self._get_pix(img_name)
        if img.isNull():
            return
        qp.drawPixmap(rect.toRect(), img)

    def _preview_head_rect(self, n: GNote) -> Optional[QRectF]:
        start_u = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        if start_u > self.window_size_unit + 1.0 or start_u < -1.0:
            return None
        x, draw_w = self._preview_note_xw(n, 0.9)
        draw_h = max(float(MIN_NOTE_HEIGHT_PX), float(PREVIEW_PX))
        start_py = self._unit_to_py(start_u)
        cy = start_py - draw_h
        return QRectF(float(x), float(cy), float(draw_w), float(draw_h))

    def _preview_hold_body_rect(self, n: GNote) -> Optional[QRectF]:
        rect = self._note_rect(n)
        if rect is None or rect.height() < 1:
            return None
        x, draw_w = self._preview_note_xw(n, 0.8)
        return QRectF(float(x), float(rect.top()), float(draw_w), float(rect.height()))

    def _preview_stac_rect(self, n: GNote) -> Optional[QRectF]:
        start_u = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        if start_u > self.window_size_unit + 1.0 or start_u < -1.0:
            return None
        x, draw_w = self._preview_note_xw(n, 0.9)
        tap_h = max(float(MIN_NOTE_HEIGHT_PX), float(PREVIEW_PX))
        try:
            pix_per_unit = float(self.height()) / max(1.0, float(self.window_size_unit))
            units_per_ms = float(self.mapper.ms_to_unit(1.0) - self.mapper.ms_to_unit(0.0))
            pix_per_ms = pix_per_unit * units_per_ms
            stac_draw_h = min(120.0, pix_per_ms * float(PREVIEW_MS) * 3.0)
            if stac_draw_h < float(MIN_NOTE_HEIGHT_PX):
                stac_draw_h = float(MIN_NOTE_HEIGHT_PX)
        except Exception:
            stac_draw_h = tap_h * 3
        start_py = self._unit_to_py(start_u)
        stac_y = start_py - tap_h - stac_draw_h
        return QRectF(float(x), float(stac_y), float(draw_w), float(stac_draw_h))

    def _preview_part_rects(self, n: GNote) -> List[QRectF]:
        parts: List[QRectF] = []
        head = self._preview_head_rect(n)
        if head is not None:
            parts.append(head)
        if n.note_type == 2:
            body = self._preview_hold_body_rect(n)
            if body is not None:
                parts.append(body)
                # 額外加入 hold 尾端提示，方便辨識「頭尾都選到」
                tail_h = max(6.0, min(18.0, head.height() * 0.35 if head is not None else 10.0))
                parts.append(QRectF(body.left(), body.top(), body.width(), tail_h))
        if n.note_type == 3:
            stac = self._preview_stac_rect(n)
            if stac is not None:
                parts.append(stac)
        return parts

    def _preview_hit_rect(self, n: GNote) -> Optional[QRectF]:
        parts = self._preview_part_rects(n)
        if not parts:
            return None
        r = parts[0]
        for p in parts[1:]:
            r = r.united(p)
        return r

    def _preview_note_head(self, qp: QPainter, n: GNote) -> None:
        """Note head：0.9 寬、原圖比例高度 clamp 至 rect。
        全部底部對齊於 starttime（rect.bottom）。
        stac 這訞畫 tap 圖（left/right_note），LeftStac/RightStac 在 Pass 3 另行覆蓋。
        """
        nt, hand = n.note_type, n.hand
        if nt == 1:
            img_name = 'soft_note.png'
        else:                                         # tap(0) / hold head(2) / stac base(3)
            img_name = 'left_note.png' if hand == 1 else 'right_note.png'
        img = self._get_pix(img_name)
        if img.isNull() or img.width() == 0:
            return
        rect = self._preview_head_rect(n)
        if rect is None:
            return
        qp.drawPixmap(rect.toRect(), img)

    def _draw_notes_preview(self, qp: QPainter) -> None:
        """預覽模式的音符繪製：依 note_type 使用 graphic/ 圖片。"""
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Pass 1：hold 主體（低圖層）
        for n in self.model.notes:
            if n.note_type == 2:
                self._preview_hold_body(qp, n)
        # Pass 2：所有 note head（tap / soft / hold head）；stac 的層也在這裡畫 tap 底層
        for n in self.model.notes:
            self._preview_note_head(qp, n)
        # Pass 3：stac 的 LeftStac/RightStac 圖（疊在 tap 之上）
        for n in self.model.notes:
            if n.note_type == 3:
                stac_img_name = 'LeftStac.png' if n.hand == 1 else 'RightStac.png'
                tap_img_name  = 'left_note.png' if n.hand == 1 else 'right_note.png'
                stac_img = self._get_pix(stac_img_name)
                tap_img  = self._get_pix(tap_img_name)
                if stac_img.isNull() or stac_img.width() == 0:
                    continue
                rect = self._preview_stac_rect(n)
                if rect is None:
                    continue
                qp.drawPixmap(rect.toRect(), stac_img)

        # Pass 4：建立 hit-test 區域 + 選取外框（圍繞圖示）
        qp.setPen(QPen(SEL_OUTLINE, 2))
        qp.setBrush(Qt.NoBrush)
        for n in self.model.notes:
            hit_rect = self._preview_hit_rect(n)
            if hit_rect is None:
                continue
            # 建立 hit-test 區域（選取外框改於 paintEvent 的 overlay 之後繪製）
            self._visible.append((hit_rect, n))
        qp.setRenderHint(QPainter.SmoothPixmapTransform, False)

    def _draw_preview_overlay(self, qp: QPainter) -> None:
        """半透明遙罩，視覺提示目前為不可編輯的預覽模式。"""
        qp.fillRect(self.rect(), QColor(18, 18, 30, 100))

    # ==================================================================
    # 滑鼠
    # ==================================================================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        pos = event.pos()

        # ── 放置音符模式 ───────────────────────────────────────────
        if self._note_input_mode and not self.alloc_active:
            if event.button() == Qt.LeftButton:
                self._place_note_at(pos)
                return

        # 預覽模式：允許選取（點擊/框選），其餘互動不開放
        if self.preview_mode:
            if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
                ctrl_hit = self._hit_test(pos)
                if ctrl_hit is not None:
                    if ctrl_hit.idx in self.selected:
                        self.selected.discard(ctrl_hit.idx)
                    else:
                        self.selected.add(ctrl_hit.idx)
                    self.selection_changed.emit(len(self.selected))
                    self.update()
                    return
            if event.button() == Qt.LeftButton:
                self._rubber_start = pos
                self._rubber_end = pos
                self._is_rubbing = True
            return

        # 預覽模式下，除放置模式外不接受其他編輯互動
        if self.preview_mode:
            return

        # ── Alloc 模式：邊界拖曳 ────────────────────────────────────
        if self.alloc_active:
            if event.button() == Qt.LeftButton:
                xk  = self._px_to_key(pos.x())
                yu  = self._py_to_unit_abs(pos.y())
                xthr, ythr = 0.6, 0.15
                if   abs(xk - self.alloc_target_min)         <= xthr:
                    self._alloc_drag_edge = ('x', 'min')
                elif abs(xk - (self.alloc_target_max + 1))   <= xthr:
                    self._alloc_drag_edge = ('x', 'max')
                elif abs(yu - self.alloc_time_min_u)          <= ythr:
                    self._alloc_drag_edge = ('y', 'min')
                elif abs(yu - self.alloc_time_max_u)          <= ythr:
                    self._alloc_drag_edge = ('y', 'max')
                else:
                    self._alloc_drag_edge = None
            return

        # ── 左鍵 + Ctrl：切換單一音符選取，或空地框選（加法模式）───────────
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            ctrl_hit = self._hit_test(pos)
            if ctrl_hit is not None:
                # 點到音符 → 切換選取（已選取則取消，未選取則加入）
                if ctrl_hit.idx in self.selected:
                    self.selected.discard(ctrl_hit.idx)
                else:
                    self.selected.add(ctrl_hit.idx)
                self.selection_changed.emit(len(self.selected))
                self.update()
                return
            # Ctrl + 空地 → 由下方 rubber band 處理（加法模式）
            # Ctrl + 空地 → 由下方 rubber band 處理（加法模式）

        # ── 左鍵：框選 ────────────────────────────────────────────────
        if event.button() == Qt.LeftButton:
            # Shift + 點音符：從先前 anchor 到本次點選建立時間範圍選取
            if event.modifiers() & Qt.ShiftModifier:
                hit = self._hit_test(pos)
                if hit is not None:
                    anchor_ms = None
                    if getattr(self, '_last_select_anchor_ms', None) is not None:
                        anchor_ms = float(self._last_select_anchor_ms)
                    elif self.selected:
                        picks = [n for n in self.model.notes if n.idx in self.selected]
                        if picks:
                            anchor_ms = min(float(n.start) for n in picks)
                    if anchor_ms is None:
                        anchor_ms = float(hit.start)
                    start_range = min(anchor_ms, float(hit.start))
                    end_range   = max(anchor_ms, float(hit.end))
                    new_sel = {n.idx for n in self.model.notes if float(n.start) >= start_range and float(n.start) <= end_range}
                    self.selected = new_sel
                    self._last_select_anchor_ms = anchor_ms
                    self.selection_changed.emit(len(self.selected))
                    self.update()
                    return
            # 若 time_uniform 模式且靠近小節線，啟動小節線拖曳
            if self.time_uniform:
                ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
                beats = self.model.get_beat_entries()
                if beats:
                    thr = 6  # pixel threshold
                    closest = None
                    for idx, bms in beats:
                        # 找 bar start
                        measure_idx = self.model.get_measure_at_ms(bms)
                        # bar start 的 y
                        unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                        py = int(self._unit_to_py(unit))
                        if abs(py - pos.y()) <= thr:
                            # 找到最靠近的 barline，記得不是第一個（需要前一小節可調整）
                            closest = (measure_idx, py, bms)
                            break
                    if closest is not None:
                        measure_idx, py, bms = closest
                        if measure_idx > 0:
                            prev_idx = measure_idx - 1
                            start_ms, end_ms = self.model.get_measure_time_range(prev_idx)
                            if start_ms is not None and end_ms is not None:
                                # 開始拖曳：目標為前一小節（改變前一小節 BPM）
                                self._barline_dragging = True
                                self._barline_drag_measure = prev_idx
                                self._barline_drag_start_ms = start_ms
                                self._barline_drag_orig_end_ms = end_ms
                                self._barline_drag_py = py
                                self._drag_status = '拖曳小節線'
                                self._emit_status()
                                return
            # 預設行為：框選
            self._rubber_start = pos
            self._rubber_end   = pos
            # store absolute unit positions so rubber follows scrolling
            try:
                u = self._py_to_unit_abs(pos.y())
            except Exception:
                u = self.window_start_unit
            self._rubber_start_u = u
            self._rubber_end_u = u
            self._is_rubbing   = True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.pos()
        self._last_mouse_unit = self._py_to_unit_abs(pos.y())

        # 放置音符模式：記錄游標並更新 snap 指示線
        if self._note_input_mode:
            self._note_input_hover = QPoint(pos)
            self.update()

        if self.preview_mode:
            if self._is_rubbing and (event.buttons() & Qt.LeftButton):
                self._rubber_end = pos
                try:
                    self._rubber_end_u = self._py_to_unit_abs(pos.y())
                except Exception:
                    pass
                self.update()
            return

        if self.alloc_active and self._alloc_drag_edge:
            axis, side = self._alloc_drag_edge
            raw = (self._px_to_key(pos.x()) if axis == 'x'
                   else self._py_to_unit_abs(pos.y()))
            self._update_alloc_edge(axis, side, raw)
            return

        if self._is_drag_copy and (event.buttons() & Qt.LeftButton):
            yu  = self._py_to_unit_abs(pos.y())
            cur = self.mapper.unit_to_ms(yu)
            raw = cur - self._drag_start_abs_ms
            self._drag_cur_delta_ms = self._quantize(raw, self._drag_snap_ms)
            self.update()
            return

        # 小節線拖曳 - 更新顯示位置與暫時 BPM
        if getattr(self, '_barline_dragging', False) and (event.buttons() & Qt.LeftButton):
            self._barline_drag_py = pos.y()
            # 計算暫時 BPM 並更新狀態欄
            try:
                start_ms = int(self._barline_drag_start_ms or 0)
                cur_ms = int(round(self.mapper.unit_to_ms(self._py_to_unit_abs(pos.y()))))
                new_dur = max(1, cur_ms - start_ms)
                num = self.model.get_beats_per_bar_at_ms(start_ms)
                den = self.model.time_sig_denominator
                new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                self._drag_status = f'目標 BPM: {new_bpm:.2f}'
            except Exception:
                self._drag_status = ''
            self._emit_status()
            self.update()
            return

        if self._is_rubbing and (event.buttons() & Qt.LeftButton):
            self._rubber_end = pos
            try:
                self._rubber_end_u = self._py_to_unit_abs(pos.y())
            except Exception:
                pass
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.preview_mode:
            if event.button() == Qt.LeftButton and self._is_rubbing:
                self._is_rubbing = False
                start = self._rubber_start
                end = event.pos()
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                if start and abs(end.x() - start.x()) <= 3 and abs(end.y() - start.y()) <= 3:
                    self._single_click(end, ctrl)
                else:
                    self._rubber_select(QRect(start, end).normalized(), ctrl)
                self._rubber_start = None
                self._rubber_end = None
                self._rubber_start_u = None
                self._rubber_end_u = None
                self.update()
                self.selection_changed.emit(len(self.selected))
            return
        if event.button() == Qt.LeftButton and self.alloc_active:
            self._alloc_drag_edge = None
            return

        if event.button() == Qt.LeftButton and self._is_drag_copy:
            delta = int(round(self._drag_cur_delta_ms))
            self._is_drag_copy      = False
            self._drag_cur_delta_ms = 0.0
            self._drag_snap_ms      = 0.0
            self._drag_status       = ''
            if abs(delta) >= 1:
                self.duplicate_with_offset(delta)
            self.update()
            return

        # 小節線拖曳釋放：計算最終 BPM 並套用
        if event.button() == Qt.LeftButton and getattr(self, '_barline_dragging', False):
            try:
                self._barline_dragging = False
                target_idx = int(self._barline_drag_measure) if self._barline_drag_measure is not None else None
                if target_idx is None:
                    self._barline_drag_py = None
                    self._barline_drag_measure = None
                    self._drag_status = ''
                    self._emit_status()
                    return
                start_ms = int(self._barline_drag_start_ms or 0)
                cur_ms = int(round(self.mapper.unit_to_ms(self._py_to_unit_abs(event.pos().y()))))
                new_dur = max(1, cur_ms - start_ms)
                num = self.model.get_beats_per_bar_at_ms(start_ms)
                den = self.model.time_sig_denominator
                new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                # 推入歷史並套用
                self.model.push_history()
                self.model.set_measure_bpm(target_idx, float(new_bpm), uniform=True)
                self.rebuild_mapper()
                self._barline_drag_py = None
                self._barline_drag_measure = None
                self._barline_drag_start_ms = None
                self._barline_drag_orig_end_ms = None
                self._drag_status = ''
                self._emit_status()
                self.note_edited.emit()
                self.update()
            except Exception:
                # 清理狀態
                self._barline_dragging = False
                self._barline_drag_py = None
                self._barline_drag_measure = None
                self._drag_status = ''
                self._emit_status()
            return
            return

        if event.button() == Qt.LeftButton and self._is_rubbing:
            self._is_rubbing = False
            start = self._rubber_start
            end   = event.pos()
            ctrl  = bool(event.modifiers() & Qt.ControlModifier)
            if start and abs(end.x()-start.x()) <= 3 and abs(end.y()-start.y()) <= 3:
                self._single_click(end, ctrl)
            else:
                self._rubber_select(QRect(start, end).normalized(), ctrl)
            self._rubber_start = None
            self._rubber_end   = None
            self._rubber_start_u = None
            self._rubber_end_u = None
            self.update()
            self.selection_changed.emit(len(self.selected))

    def _hit_test(self, pos: QPoint) -> Optional[GNote]:
        pt = QPointF(pos)
        hit, min_area = None, float('inf')
        for rect, n in self._visible:
            if rect.contains(pt):
                area = rect.width() * rect.height()
                if area < min_area:
                    min_area, hit = area, n
        return hit

    def _single_click(self, pos: QPoint, ctrl: bool) -> None:
        hit = self._hit_test(pos)
        if hit is None:
            if not ctrl:
                self.selected.clear()
                self._last_select_anchor_ms = None
        elif ctrl:
            if hit.idx in self.selected:
                self.selected.discard(hit.idx)
            else:
                self.selected.add(hit.idx)
            # update anchor to earliest selected
            picks = [n for n in self.model.notes if n.idx in self.selected]
            if picks:
                self._last_select_anchor_ms = min(float(n.start) for n in picks)
        else:
            self.selected = {hit.idx} if hit.idx not in self.selected else set()
            if hit is not None:
                self._last_select_anchor_ms = float(hit.start)

    def _rubber_select(self, rect: QRect, ctrl: bool) -> None:
        if not ctrl:
            self.selected.clear()
        r = QRectF(rect)
        for note_rect, n in self._visible:
            if r.intersects(note_rect):
                if ctrl and n.idx in self.selected:
                    self.selected.discard(n.idx)   # Ctrl + 已選取 → 取消
                else:
                    self.selected.add(n.idx)       # 否則追加
        # update anchor to earliest selected start
        picks = [nn for nn in self.model.notes if nn.idx in self.selected]
        if picks:
            self._last_select_anchor_ms = min(float(nn.start) for nn in picks)

    def wheelEvent(self, event: QWheelEvent) -> None:
        import time as _t
        degrees = event.angleDelta().y() / 8.0
        steps   = degrees / 15.0
        # If Ctrl is held, use zoom instead of scroll
        if bool(event.modifiers() & Qt.ControlModifier):
            # map steps to a multiplicative zoom factor (steps>0 -> zoom in)
            try:
                factor = float(pow(0.9, steps)) if steps else 1.0
            except Exception:
                factor = 1.0
            if factor == 1.0:
                return

            # Preserve the unit (time) under the mouse position when zooming
            pos = event.pos()
            try:
                unit_under_mouse = float(self._py_to_unit_abs(pos.y()))
            except Exception:
                unit_under_mouse = self.window_start_unit + self.window_size_unit * 0.5

            if self.time_uniform:
                # time_uniform: operate on ms span (_time_uniform_span_ms)
                old_span_ms = max(1.0, float(self._time_uniform_span_ms or 1.0))
                new_span_ms = max(50.0, min(600000.0, old_span_ms * factor))

                # relative fraction of unit within old window
                old_size = float(self.window_size_unit)
                if old_size <= 0:
                    rel_frac = 0.5
                else:
                    rel_frac = (unit_under_mouse - self.window_start_unit) / old_size

                # compute new window_start in ms such that unit_under_mouse stays at same pixel
                unit_ms = float(self.mapper.unit_to_ms(unit_under_mouse))
                desired_ws_ms = unit_ms - rel_frac * new_span_ms
                # apply
                self._time_uniform_span_ms = new_span_ms
                self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
                self._sync_time_uniform_window_units()
                self._clamp_window_start()
                self.update()
                self._emit_status()
                return

            # non-time_uniform: adjust window_size_unit and window_start_unit to preserve unit position
            old_size = float(self.window_size_unit)
            new_size = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS, old_size * factor))
            if old_size <= 0:
                rel_frac = 0.5
            else:
                rel_frac = (unit_under_mouse - self.window_start_unit) / old_size

            new_ws = unit_under_mouse - rel_frac * new_size
            self.window_size_unit = new_size
            self.window_start_unit = new_ws
            self._clamp_window_start()
            self.update()
            self._emit_status()
            return

        now = _t.time()
        self._wheel_events.append((now, abs(steps) if steps else 1.0))
        mult  = self._wheel_multiplier()
        delta = -steps * self._scroll_step_units() * mult
        if self.scroll_invert:
            delta = -delta
        self.scroll_by(delta)

    # ==================================================================
    # 鍵盤
    # ==================================================================

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key   = event.key()
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        # Toggle preview mode with Tab (always available)
        if key == Qt.Key_Tab:
            self.toggle_preview_mode(not self.preview_mode)
            return

        # ── Alloc 模式 ────────────────────────────────────────────────
        if self.alloc_active:
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.confirm_alloc_section()
            elif key == Qt.Key_Escape:
                self.cancel_alloc_section()
            elif key == Qt.Key_Left:
                self._update_alloc_edge('x', 'min', self.alloc_target_min - 1)
            elif key == Qt.Key_Right:
                self._update_alloc_edge('x', 'max', self.alloc_target_max + 1)
            return

        # ── 預覽模式：只允許選取 + 上下左右 + 播放停止 ──────────────────────
        if self.preview_mode:
            if key == Qt.Key_Up:
                if self.selected:
                    self.shift_selected_by_32nd(-1, push=not event.isAutoRepeat())
                else:
                    self.scroll_by(self._scroll_step_units() * (4 if shift else 1))
            elif key == Qt.Key_Down:
                if self.selected:
                    self.shift_selected_by_32nd(1, push=not event.isAutoRepeat())
                else:
                    self.scroll_by(-self._scroll_step_units() * (4 if shift else 1))
            elif key == Qt.Key_Left and not ctrl:
                self.shift_selected_keys(-(10 if shift else 1), push=not event.isAutoRepeat())
            elif key == Qt.Key_Right and not ctrl:
                self.shift_selected_keys(10 if shift else 1, push=not event.isAutoRepeat())
            elif ctrl and key == Qt.Key_A:
                self.select_all()
            elif key == Qt.Key_Escape:
                self.deselect_all()
            elif ctrl and key == Qt.Key_P:
                self.play_full_requested.emit()
            elif key == Qt.Key_P and not shift:
                ws_ms, we_ms = self._window_ms()
                self.play_requested.emit(ws_ms, we_ms)
            elif key == Qt.Key_S:
                self.stop_requested.emit()
            return

        # 若正在拖曳小節線，按 Esc 可取消拖曳（不套用變更）
        if key == Qt.Key_Escape and getattr(self, '_barline_dragging', False):
            self._barline_dragging = False
            self._barline_drag_measure = None
            self._barline_drag_start_ms = None
            self._barline_drag_orig_end_ms = None
            self._barline_drag_py = None
            self._drag_status = ''
            self._emit_status()
            self.update()
            return

        # ── Up/Down：有選取→32分音符時間移動，無選取→捲動 ───────────
        if key == Qt.Key_Up:
            if self.selected:
                self.shift_selected_by_32nd(-1, push=not event.isAutoRepeat())
            else:
                self.scroll_by(self._scroll_step_units() * (4 if shift else 1))
            return
        if key == Qt.Key_Down:
            if self.selected:
                self.shift_selected_by_32nd(1, push=not event.isAutoRepeat())
            else:
                self.scroll_by(-self._scroll_step_units() * (4 if shift else 1))
            return

        # ── 鍵位平移 ─────────────────────────────────────────────────
        if key == Qt.Key_Left and not ctrl:
            self.shift_selected_keys(-(10 if shift else 1), push=not event.isAutoRepeat())
            return
        if key == Qt.Key_Right and not ctrl:
            self.shift_selected_keys(10 if shift else 1, push=not event.isAutoRepeat())
            return

        # ── Undo ──────────────────────────────────────────────────────
        if ctrl and key == Qt.Key_Z:
            self.undo()
            return

        # ── Copy / Paste ──────────────────────────────────────────────
        if ctrl and key == Qt.Key_C:
            self.copy_to_clipboard()
            return
        if ctrl and key == Qt.Key_V:
            self.paste_from_clipboard()
            return

        # ── 全選 ──────────────────────────────────────────────────────
        if ctrl and key == Qt.Key_A:
            self.select_all()
            return
        if key == Qt.Key_Escape:
            self.deselect_all()
            return

        # ── 刪除 ──────────────────────────────────────────────────────
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            return

        # ── 縮放 ──────────────────────────────────────────────────────
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom(0.5)
            return
        if key == Qt.Key_Minus:
            self.zoom(2.0)
            return

        # ── 音符類型 ──────────────────────────────────────────────────
        if key == Qt.Key_H:
            self.set_type_selected(2)
            return
        if key == Qt.Key_T:
            self.set_type_selected(0)
            return
        if key == Qt.Key_K:
            self.set_type_selected(3)
            return

        # ── 左右手 ────────────────────────────────────────────────────
        if key == Qt.Key_L:
            self.set_hand_selected(1)   # 1 = 左
            return
        if key == Qt.Key_R:
            self.set_hand_selected(0)   # 0 = 右
            return

        # ── 就地複製（非 Ctrl+C）───────────────────────────────────
        if key == Qt.Key_C and not ctrl:
            self.duplicate_selected()
            return

        # ── 播放 ──────────────────────────────────────────────────────
        if key == Qt.Key_P and ctrl:
            self.play_full_requested.emit()
            return
        if key == Qt.Key_P and not shift and not ctrl:
            self.play_from_window_requested.emit()
            return
        if key == Qt.Key_P and shift:
            self._emit_play_selection()
            return
        if key == Qt.Key_S:
            self.stop_requested.emit()
            return

        # ── Alloc Section 啟動 ────────────────────────────────────────
        if key == Qt.Key_A and shift:
            self.start_alloc_section()
            return

        super().keyPressEvent(event)

    def _emit_play_selection(self) -> None:
        if not self.selected:
            ws_ms, we_ms = self._window_ms()
            self.play_requested.emit(ws_ms, we_ms)
            return
        # map selected display idx -> actual note objects from display cache
        sel_notes = [n for n in self.model.notes if n.idx in self.selected]
        if not sel_notes:
            ws_ms, we_ms = self._window_ms()
            self.play_requested.emit(ws_ms, we_ms)
            return
        self.play_requested.emit(
            float(min(n.start for n in sel_notes)),
            float(max(n.end   for n in sel_notes)),
        )

    # ==================================================================
    # resize
    # ==================================================================

    def resizeEvent(self, _: QResizeEvent) -> None:
        self.update()

    # ==================================================================
    # 右鍵選單（contextMenuEvent）
    # ==================================================================

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        from PyQt5.QtWidgets import QMenu, QAction as _QA

        # ── 預覽模式：只顯示播放控制 ──────────────────────────
        if self.preview_mode:
            menu = QMenu(self)
            act_pw = menu.addAction('▶ 播放視窗  (P)')
            act_pw.triggered.connect(self.play_from_window_requested.emit)
            act_pf = menu.addAction('▶ 播放全曲  (Ctrl+P)')
            act_pf.triggered.connect(self.play_full_requested.emit)
            act_stop = menu.addAction('■ 停止  (S)')
            act_stop.triggered.connect(self.stop_requested.emit)
            menu.exec_(event.globalPos())
            return

        pos = event.pos()
        hit = self._hit_test(pos)

        menu = QMenu(self)

        # ── 命中音符：屬性編輯 ──────────────────────────────────────
        if hit is not None:
            act_prop = _QA('編輯屬性…', self)
            def _open_prop():
                # `hit` 已經是被命中的 `GNote` 物件（來自 display cache）。
                # 不要直接用 `hit.idx` 當成 notes_tree 的索引，應該以物件為主，
                # 若需要從 notes_tree 取回對應物件，則根據物件或其 idx 找對應項目。
                if hit in self.model.notes_tree:
                    auth = hit
                else:
                    auth = next((n for n in self.model.notes_tree if n.idx == hit.idx), hit)
                dlg = NotePropertyDialog(self, auth,
                        beat_ms=60000.0 / max(1.0, self.model.bpm))
                if dlg.exec_() == QDialog.Accepted:
                    self.model.push_history()
                    auth.apply_back()
                    self.model.rebuild_display_cache()
                    self.update()
                    self.note_edited.emit()
            act_prop.triggered.connect(_open_prop)
            menu.addAction(act_prop)
            menu.addSeparator()

        # ── 音符類型（有選取才啟用）───────────────────────────────────
        has_sel = bool(self.selected)
        type_m = menu.addMenu('音符類型')
        for label, t in [('Tap  (T)', 0), ('Soft', 1),
                         ('Long  (H)', 2), ('Staccato  (K)', 3)]:
            a = type_m.addAction(label)
            a.setEnabled(has_sel)
            a.triggered.connect(lambda checked=False, _t=t: self.set_type_selected(_t))

        hand_m = menu.addMenu('左右手')
        for label, h in [('右手  (R)', 0), ('左手  (L)', 1)]:
            a = hand_m.addAction(label)
            a.setEnabled(has_sel)
            a.triggered.connect(lambda checked=False, _h=h: self.set_hand_selected(_h))

        width_m = menu.addMenu('設定寬度')
        for label, w in [('寬度 1', 1), ('寬度 2', 2), ('寬度 3', 3),
                         ('寬度 4', 4), ('寬度 5', 5), ('寬度 6', 6)]:
            a = width_m.addAction(label)
            a.setEnabled(has_sel)
            a.triggered.connect(lambda checked=False, _w=w: self.set_width_selected(_w))

        menu.addSeparator()

        # ── 打擊因設定（右手 / 左手 / 小節拍）────────────────────────────
        hit_m = menu.addMenu('打擊因')
        act_set_hit = hit_m.addAction('設定打擊因…')
        def _set_hit_dialog():
            from PyQt5.QtWidgets import (
                QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox,
                QDialogButtonBox, QMessageBox, QLabel
            )

            dlg = QDialog(self)
            dlg.setWindowTitle('設定打擊因')
            vbox = QVBoxLayout(dlg)
            form = QFormLayout()

            # 讀取 model 目前值（若有）或預設 1.0
            jm = getattr(self.model, 'json_meta', {}) or {}
            r_val = float(jm.get('hit_factor_right', 1.0))
            l_val = float(jm.get('hit_factor_left', 1.0))
            b_val = float(jm.get('hit_factor_beat', 1.0))

            sb_right = QDoubleSpinBox()
            sb_right.setRange(0.0, 10.0)
            sb_right.setDecimals(2)
            sb_right.setValue(r_val)
            form.addRow('右手打擊因：', sb_right)

            sb_left = QDoubleSpinBox()
            sb_left.setRange(0.0, 10.0)
            sb_left.setDecimals(2)
            sb_left.setValue(l_val)
            form.addRow('左手打擊因：', sb_left)

            sb_beat = QDoubleSpinBox()
            sb_beat.setRange(0.0, 10.0)
            sb_beat.setDecimals(2)
            sb_beat.setValue(b_val)
            form.addRow('小節拍打擊因：', sb_beat)

            vbox.addLayout(form)
            hint = QLabel('說明：此設定只儲存在當前模型（save 時會寫入 JSON meta）。')
            hint.setStyleSheet('color:#666; font-size:11px')
            vbox.addWidget(hint)

            bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bbox.accepted.connect(dlg.accept)
            bbox.rejected.connect(dlg.reject)
            vbox.addWidget(bbox)

            if dlg.exec_() != QDialog.Accepted:
                return

            jm['hit_factor_right'] = float(sb_right.value())
            jm['hit_factor_left'] = float(sb_left.value())
            jm['hit_factor_beat'] = float(sb_beat.value())
            self.model.json_meta = jm
            self.model.dirty = True
            QMessageBox.information(self, '已儲存', '打擊因已更新並儲存在模型。')

        act_set_hit.triggered.connect(_set_hit_dialog)
        # ── 編輯動作 ────────────────────────────────────────────────
        act_dup  = menu.addAction('就地複製  (C)')
        act_dup.setEnabled(has_sel)
        act_dup.triggered.connect(self.duplicate_selected)

        # ── 設定時長（拍）────────────────────────────────────────
        act_set_beats = menu.addAction('設定時長（拍）…')
        act_set_beats.setEnabled(has_sel)
        def _set_beats_dialog():
            from PyQt5.QtWidgets import (
                QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                QDialogButtonBox, QMessageBox
            )
            from fractions import Fraction

            beat_ms = 60000.0 / max(1.0, self.model.bpm)
            first_n = next((n for n in self.model.notes if n.idx in self.selected), None)
            default_beats = round((first_n.end - first_n.start) / beat_ms, 3) if first_n else 1.0

            def parse_fraction_to_float(s: str) -> float:
                s = s.strip()
                if not s:
                    raise ValueError('empty')
                # mixed number: '1 3/4'
                if ' ' in s and '/' in s:
                    whole, frac = s.split(None, 1)
                    num, den = frac.split('/')
                    return float(whole) + float(num) / float(den)
                if '/' in s:
                    num, den = s.split('/')
                    return float(num) / float(den)
                return float(s)

            def float_to_mixed_fraction(value: float, max_den: int = 64) -> str:
                frac = Fraction(value).limit_denominator(max_den)
                whole = frac.numerator // frac.denominator
                rem = frac.numerator % frac.denominator
                if whole != 0 and rem != 0:
                    return f"{whole} {rem}/{frac.denominator}"
                if rem == 0:
                    return str(whole)
                return f"{rem}/{frac.denominator}"

            dlg = QDialog(self)
            dlg.setWindowTitle('設定時長（拍）')
            layout = QVBoxLayout(dlg)

            label = QLabel(f'將 {len(self.selected)} 個音符設為指定拍數（上方輸入分數，下方小數；1拍 = {beat_ms:.1f} ms）：')
            layout.addWidget(label)

            frac_box = QHBoxLayout()
            frac_label = QLabel('分數：')
            frac_edit = QLineEdit()
            frac_edit.setPlaceholderText('例如 3/4 或 1 3/4')
            frac_box.addWidget(frac_label)
            frac_box.addWidget(frac_edit)
            layout.addLayout(frac_box)

            dec_box = QHBoxLayout()
            dec_label = QLabel('小數：')
            dec_edit = QLineEdit()
            dec_edit.setPlaceholderText('例如 0.75')
            dec_box.addWidget(dec_label)
            dec_box.addWidget(dec_edit)
            layout.addLayout(dec_box)

            ms_label = QLabel('')
            layout.addWidget(ms_label)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            layout.addWidget(buttons)

            # initialize
            frac_edit.setText(float_to_mixed_fraction(default_beats))
            dec_edit.setText(str(round(default_beats, 6)))
            ms_label.setText(f'{round(default_beats * beat_ms, 1)} ms')

            updating = {'flag': False}

            def on_frac_changed(text: str) -> None:
                if updating['flag']:
                    return
                updating['flag'] = True
                try:
                    val = parse_fraction_to_float(text)
                    dec_edit.setText(str(round(val, 6)))
                    ms_label.setText(f'{round(val * beat_ms, 1)} ms')
                except Exception:
                    # leave decimal blank on parse error
                    dec_edit.setText('')
                    ms_label.setText('')
                finally:
                    updating['flag'] = False

            def on_dec_changed(text: str) -> None:
                if updating['flag']:
                    return
                updating['flag'] = True
                try:
                    v = float(text)
                    frac_edit.setText(float_to_mixed_fraction(v))
                    ms_label.setText(f'{round(v * beat_ms, 1)} ms')
                except Exception:
                    frac_edit.setText('')
                    ms_label.setText('')
                finally:
                    updating['flag'] = False

            frac_edit.textChanged.connect(on_frac_changed)
            dec_edit.textChanged.connect(on_dec_changed)

            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)

            if dlg.exec_() != QDialog.Accepted:
                return

            # final parse prefer decimal if available
            final_text = dec_edit.text().strip()
            try:
                if final_text:
                    beats_val = float(final_text)
                else:
                    beats_val = parse_fraction_to_float(frac_edit.text())
            except Exception:
                QMessageBox.warning(self, '輸入錯誤', '無法解析拍數輸入，請輸入數字或分數，例如 3/4 或 1 3/4。')
                return
            if beats_val <= 0:
                QMessageBox.warning(self, '輸入錯誤', '拍數必須大於 0。')
                return
            self.set_length_beats_selected(beats_val)
        act_set_beats.triggered.connect(_set_beats_dialog)

        act_del = menu.addAction('刪除選取  (Del)')
        act_del.setEnabled(has_sel)
        act_del.triggered.connect(self.delete_selected)

        menu.addSeparator()

        # ── 播放控制 ─────────────────────────────────────────────────
        act_pw = menu.addAction('▶ 播放視窗  (P)')
        act_pw.triggered.connect(self.play_from_window_requested.emit)

        act_ps = menu.addAction('▶ 播放選取  (Shift+P)')
        act_ps.triggered.connect(self._emit_play_selection)

        act_stop = menu.addAction('■ 停止  (S)')
        act_stop.triggered.connect(self.stop_requested.emit)

        menu.addSeparator()

        # ── 小節 BPM（空白處才顯示） ────────────────────────────────
        # 支援 XML beat_data 與 JSON 的 beat_timings
        if hit is None and self.model.get_beat_entries():
            unit_abs   = self._py_to_unit_abs(pos.y())
            _click_ms  = self.mapper.unit_to_ms(unit_abs)
            _m_idx     = self.model.get_measure_at_ms(_click_ms)
            _cur_bpm   = self.model.get_measure_bpm(_m_idx)
            act_mbpm   = menu.addAction(
                f'修改第 {_m_idx + 1} 小節 BPM（目前 {_cur_bpm:.1f}）…'
            )
            act_mbpm.triggered.connect(
                lambda _checked=False, _idx=_m_idx: self.set_measure_bpm_requested.emit(_idx)
            )
            # time signature current value
            # numerator via model.get_beats_per_bar_at_ms, denominator via time_sig_changes
            try:
                cur_num = self.model.get_beats_per_bar_at_ms(_click_ms)
                cur_den = self.model.time_sig_denominator
                for ms, num, den in self.model.time_sig_changes:
                    if ms <= _click_ms:
                        cur_den = den
                    else:
                        break
            except Exception:
                cur_num, cur_den = self.model.beats_per_bar, self.model.time_sig_denominator
            act_tsig = menu.addAction(f'修改第 {_m_idx + 1} 小節 拍號（目前 {cur_num}/{cur_den}）…')
            act_tsig.triggered.connect(lambda _checked=False, _idx=_m_idx: self.set_measure_time_sig_requested.emit(_idx))
            menu.addSeparator()

        # ── Alloc Section ──────────────────────────────────────────
        act_alloc = menu.addAction('Alloc Section  (Shift+A)')
        act_alloc.setEnabled(has_sel and not self.alloc_active)
        act_alloc.triggered.connect(self.start_alloc_section)

        menu.exec_(event.globalPos())

