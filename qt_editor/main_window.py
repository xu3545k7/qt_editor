"""
main_window.py
==============
完整功能的 QMainWindow，整合 ChartView、AudioPlayer 及所有選單/工具列。

功能對應 graphical_chartmaker.py
---------------------------------
File     : 開啟（XML/JSON/MIDI）、儲存、另存新檔、離開
Edit     : 復原、全選/取消選取、刪除
         : 複製（Ctrl+C）、貼上（Ctrl+V）
         : 就地重複（C+menu）
         : 設定 Width 2 / Width 3
         : 設定類型（Tap/Soft/Long/Staccato）
         : 設定手（左手/右手）
         : Shift Pitch…（對話框）
Audio    : 載入 WAV、播放視窗、播放選取、暫停、繼續、停止、重新播放
Tools    : Alloc Section、處理長條尾端、處理左右重疊、調整 BPM/Beats/Offset
View     : 縮放、捲動反向
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
import logging
from PyQt5.QtGui import QKeySequence, QCloseEvent
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMenuBar, QMessageBox,
    QScrollArea, QSizePolicy, QSlider, QSplitter, QStatusBar, QTabWidget, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
    QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QRadioButton,
)

from .models import NoteModel, open_midi
from .chart_view import ChartView, PANE_COLORS
from .audio_player import AudioPlayer
from .midi_preview import (
    MidiPreviewNote,
    MidiPreviewSynth,
    build_chart_midi_notes,
    build_preview_notes,
    pedal_spans_in_range,
)
from . import autosave
from .i18n import t
from .oplog import oplog, profile as oplog_profile
from .settings import settings
from .settings_dialog import SettingsDialog
from .new_chart_dialog import NewChartDialog
from .playback_offset_dialog import PlaybackOffsetDialog
from .align_time_dialog import AlignTimeDialog
from .hold_length_dialog import HoldLengthDialog
from .export_song_dialog import ExportSongDialog, SONGS_ROOT

# 可選：MIDI 轉換器
try:
    from .midi_to_xml_converter import MIDIToXMLConverter
    _HAS_MIDI_CONV = True
except Exception:
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from midi_to_xml_converter import MIDIToXMLConverter
        _HAS_MIDI_CONV = True
    except Exception:
        MIDIToXMLConverter = None  # type: ignore
        _HAS_MIDI_CONV = False

# 可選：simpleaudio（打擊聲用）
try:
    import simpleaudio as sa
    _HAS_SA = True
except Exception:
    sa = None  # type: ignore
    _HAS_SA = False


# Windows 不允許出現在檔名/資料夾名的字元（曲名如「Pure White / 純白」含 '/'
# 會被當成路徑分隔符 → WinError 3 找不到路徑）。
_FS_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_fs_name(name: str, fallback: str = 'untitled') -> str:
    """把曲名/難度名轉成合法的檔名或資料夾名。

    非法字元一律換成底線；並去掉結尾的空白與句點（Windows 不允許）。
    不含非法字元的名稱會原樣返回，確保既有曲目資料夾行為不變。
    顯示用的原始名稱仍寫進 register.json 的 displayName，不受影響。
    """
    s = _FS_ILLEGAL_RE.sub('_', str(name))
    s = s.rstrip('. ').strip()
    return s or fallback


class OverlayStack(QWidget):
    """疊層分割：兩個 ChartView 疊在同一塊區域，後加入的畫在上面。

    不用 layout，直接把每個子元件撐滿整塊區域；Qt 會依 z 序把它們畫進同
    一張 backing store，上層設了 WA_TranslucentBackground 就不會抹掉下層。
    """

    def add_pane(self, pane: QWidget) -> None:
        pane.setParent(self)
        pane.setGeometry(self.rect())
        pane.raise_()
        pane.show()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        for child in self.children():
            if isinstance(child, QWidget):
                child.setGeometry(self.rect())


class PaneMenuBar(QMenuBar):
    """獨立視窗的選單列：拉開選單前先把作用格切到這個視窗的格子。

    選單本身跟主視窗共用同一組 QMenu／QAction（同一份譜、同一組動作），所以
    差別只在「動作作用在哪一格」——那由作用格決定，這裡先幫忙切好。
    """

    def __init__(self, on_activate, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on_activate = on_activate

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._on_activate()
        except Exception:                       # noqa: BLE001
            logging.debug('detached menubar activate failed', exc_info=True)
        super().mousePressEvent(event)


class DetachedPaneWindow(QWidget):
    """分割模式「獨立視窗」用的外框：選單列 + 工具列 + 一個 ChartView。

    版面刻意和主視窗一致（上面一條白色選單列、下面一條分頁工具列），工具列
    是這個視窗自己的一條，所以拆出去的格子可以完全獨立操作，不用切回主視窗。
    """

    closed = pyqtSignal()

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        self.resize(760, 860)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._layout = lay
        self._menubar: Optional[QWidget] = None
        self._toolbar: Optional[QWidget] = None

    def set_pane(self, pane: QWidget) -> None:
        self._layout.addWidget(pane, 1)
        pane.show()

    def set_menubar(self, bar: QWidget) -> None:
        """選單列固定在最上面。"""
        self.clear_menubar()
        self._menubar = bar
        bar.setParent(self)
        self._layout.insertWidget(0, bar, 0)
        bar.show()

    def clear_menubar(self) -> None:
        bar, self._menubar = self._menubar, None
        self._drop(bar)

    def set_toolbar(self, bar: QWidget) -> None:
        """工具列排在選單列下面（格子已經放進來時也要保持在它們下方）。"""
        self.clear_toolbar()
        self._toolbar = bar
        bar.setParent(self)
        self._layout.insertWidget(1 if self._menubar is not None else 0, bar, 0)
        bar.show()

    def clear_toolbar(self) -> None:
        bar, self._toolbar = self._toolbar, None
        self._drop(bar)

    def _drop(self, bar: Optional[QWidget]) -> None:
        if bar is None:
            return
        self._layout.removeWidget(bar)
        bar.setParent(None)
        bar.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.closed.emit()
        super().closeEvent(event)


class ToolbarSet:
    """一條分頁式工具列所擁有的 widget。

    主視窗一條；切到「獨立視窗」分割模式時，被拆出去的那個視窗再有自己的
    一條。兩條各自記住「自己負責的那一格」的狀態（放置模式 / 預覽 / 檢視
    模式），互不干擾；全域狀態（播放、音源、音量、偏移）則兩條互相同步。

    `pane` = 這條工具列固定操作哪一格；None 表示跟著目前的作用格走（沒有
    拆出獨立視窗時只有一條工具列，維持原本「工具列作用在作用格」的行為）。
    """

    def __init__(self, pane: Optional[int] = None) -> None:
        self.pane = pane
        self.root: Optional[QWidget] = None
        self.tabs = None
        # 編輯分頁
        self.note_input_act: Optional[QAction] = None
        self.dur_combo = None
        self.hand_combo = None
        self.width_combo = None
        self.type_combo = None
        self.note_input_group: List[QWidget] = []
        self.grid_btn = None
        # 音階輔助
        self.pattern_act: Optional[QAction] = None
        self.pattern_group: List[QWidget] = []
        self.pattern_kind_combo = None
        self.pattern_dir_combo = None
        self.pattern_step_combo = None
        self.pattern_key_combo = None
        self.pattern_key_label = None
        self.hand_filter_combo = None
        # 分割區塊
        self.split_act: Optional[QAction] = None
        self.split_dir_act: Optional[QAction] = None
        self.pane_acts: List[QAction] = []
        self.split_hint = None
        # 播放分頁
        self.pause_act: Optional[QAction] = None
        self.hit_act: Optional[QAction] = None
        self.offset_label = None
        # 檢視分頁
        self.preview_act: Optional[QAction] = None
        self.view_mode_act: Optional[QAction] = None
        # 音量列：key -> {'label','slider','action','text'}
        self.vol: Dict[str, Dict[str, Any]] = {}
        # QToolButton 不持有 menu 的所有權，得自己留著參考
        self.tool_menus: List[QMenu] = []


# 下拉框除了文字還要容納箭頭與內距；標籤只需要一點左右留白。
# 兩者都是邏輯像素，Qt 的 High-DPI 縮放會自動跟著螢幕倍率放大。
COMBO_CHROME_PX = 34
LABEL_PAD_PX    = 6


def _difficulty_report(rows) -> str:
    """把生成結果排成一張可以和官方數字對照的表。

    每一行都印出「實際值 / 官方目標」——生成器的門檻是二分搜尋出來的，沒有
    這張表就沒辦法判斷它到底有沒有做到。
    """
    from .difficulty import TARGETS

    lines = ['音訊事件總數不變（沒被選中的折進 sub_note）。', '']
    for result, path in rows:
        goal = TARGETS[result.difficulty]
        lines.append('%s  →  %s' % (result.difficulty.upper(), os.path.basename(path)))
        lines.append('    可見 %d / %d 顆（%.0f%%）  每秒 %.1f 顆'
                     % (result.visible, result.total,
                        result.visible_ratio * 100, result.notes_per_sec))
        lines.append('    同手間隔 %.0fms（官方 %dms）  同時最多 %d 顆（官方 %d）'
                     % (result.gap_hands_ms, goal.same_hand_gap_ms,
                        result.max_simultaneous, goal.max_simultaneous))
        lines.append('    長押 %.1f%%（官方 %.1f%%）  hand=2 %.0f%%（官方 %.0f%%）'
                     % (result.long_ratio * 100, goal.long_ratio * 100,
                        result.hand2_ratio * 100, goal.hand2_ratio * 100))
        if result.orphans:
            lines.append('    ⚠ %d 顆找不到寄主，被迫留成可見音符' % result.orphans)
        lines.append('')
    return '\n'.join(lines).rstrip()


def _model_path(model) -> str:
    """這份譜面在磁碟上的位置。沒存過就是空字串。

    以前寫的是 `getattr(model, 'path', '')`，而 NoteModel 從來沒有 `path` 這個
    欄位（叫 `current_file`），備援的 `self._current_path` 整個專案也不存在 ——
    所以那四個地方拿到的永遠是空字串。看得見的後果是「生成其他難度到樂曲資料
    夾…」永遠回一句「請先把這份譜面存檔」，即使譜面就是從磁碟開起來的：那個功
    能從頭到尾沒有辦法用。
    """
    return getattr(model, 'current_file', '') or ''


def _still_playing(obj) -> bool:
    """播放物件還在響嗎（simpleaudio 與 sounddevice 的替身都有這個方法）。"""
    try:
        return bool(obj.is_playing())
    except Exception:                           # noqa: BLE001
        return False


class MainWindow(QMainWindow):
    """頂層主視窗（完整功能版）。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(t('wnd_title'))
        self.resize(1280, 860)

        # 套用儲存的滾輪方向設定

        # ── 中央編輯區（分割模式：最多兩個格子看同一份譜）─────────────
        # pane 0 = 左/上（紅）, pane 1 = 右/下（藍）。pane 1 預設隱藏，
        # 按下工具列「分割」才顯示，並預設進入音高模式。
        self._panes: List[ChartView] = []
        self._active_pane: int = 0
        # 版面：'h' 左右平分寬度 / 'v' 上下平分高度 / 'window' 獨立視窗
        #      / 'overlay' 疊層分割（底層實色 + 上層半透明）
        self._split_layout: str = 'h'
        self._detached_win: Optional[DetachedPaneWindow] = None
        # 工具列：主視窗一條，獨立視窗模式時那個視窗再一條
        self._toolbars: List[ToolbarSet] = []
        self._tb_main: Optional[ToolbarSet] = None
        self._tb_detached: Optional[ToolbarSet] = None
        # 正在跑某條工具列的動作時先不要回填狀態（會把剛按下的按鈕又刷回去）
        self._tb_action_busy: bool = False
        # 放置音符參數是兩條工具列共用的，記在這裡讓新建的工具列直接沿用
        self._ni_dur_idx:   int = 2      # 四分音符
        self._ni_hand_idx:  int = 0      # 右手
        self._ni_width_idx: int = 2      # 寬度 3
        self._ni_type_idx:  int = 0      # Tap
        # 音量也是共用的（工具列可能有兩條）
        self._vol_values:  Dict[str, int]  = {'music': 100, 'music2': 100, 'hit': 100}
        self._vol_enabled: Dict[str, bool] = {'music': True, 'music2': True}
        self._overlay_stack: Optional[OverlayStack] = None
        self._overlay_top: int = 1      # 疊層時哪一格在上面（半透明的那層）
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        for role in (0, 1):
            v = ChartView(self)
            v.pane_role = role
            v.pane_active = (role == 0)
            self._panes.append(v)
            self._splitter.addWidget(v)
        self._panes[0].link_pane(self._panes[1])
        self._panes[1].set_view_mode('pitch')
        self._panes[1].hide()
        self.setCentralWidget(self._splitter)

        # ── 音訊播放器 ────────────────────────────────────────────────
        self.audio = AudioPlayer(self)
        # 判定線 / 打擊聲要對上耳朵聽到的位置，得扣掉裝置輸出延遲（見播放偏移對話框）
        self.audio.set_output_latency_ms(float(settings.get('audio_latency_ms', 0) or 0))
        self._midi_preview_synth = None
        self._midi_preview_active: bool = False
        self._midi_preview_error_shown: bool = False
        self._dual_audio_enabled: bool = False
        self._play_start_ms: float = 0.0
        self._play_end_ms:   float = 0.0
        self._is_playing:    bool  = False
        self._playback_offset_ms: int = 0       # 播放偏移（正=提前，負=延後）
        self._playback_offset_advance: bool = True  # 上次選的方向

        # ── judge line 更新計時器 ─────────────────────────────────────
        self._silent_play = None      # 只播 MIDI 時的牆上時鐘 (t0, start_ms, end_ms)
        self._judge_timer = QTimer(self)
        self._judge_timer.setInterval(16)
        self._judge_timer.timeout.connect(self._on_judge_tick)

        # ── 打擊聲 ────────────────────────────────────────────────────
        # 音源（MIDI 打擊聲）預設打開。這裡就給 True，選單與各條工具列建立時
        # 都照這個值靜默同步——否則每次重建工具列（換語言、開獨立視窗）都會
        # 因為 setChecked(True) 觸發 toggled 而把使用者關掉的音源又打開。
        self._hit_sound_persistent: bool = True    # 使用者勾選的持久開關
        self._hit_sound_bytes:   Optional[bytes] = None
        self._hit_sound_channels:  int = 0
        self._hit_sound_sampwidth: int = 0
        self._hit_sound_rate:      int = 0
        self._hit_last_ms: float = -1.0   # 上一個 tick 的 judge 位置
        self._hit_times:  List[int] = []  # 預計算的唯一 startTime 清單（排序）
        self._hit_ptr:    int = 0          # 目前掃描到的位置
        self._hit_vol:    float = 1.0      # 打擊聲音量
        self._hit_wav_tmp_path: Optional[str] = None  # 套用音量後的暫存 WAV 路徑
        # per-type enable flags (右手 / 左手 / 小節拍)
        self._hit_enable_right: bool = True
        self._hit_enable_left:  bool = True
        self._hit_enable_beat:  bool = True
        self._load_hit_sound()

        # ── 信號連接（兩個格子都要接）──────────────────────────────────
        for _v in self._panes:
            self._connect_view(_v)

        self.audio.playback_stopped.connect(self._on_playback_stopped)
        # Ensure audio mode label exists
        try:
            self._lbl_audio_mode = QLabel('')
            self.statusBar().addPermanentWidget(self._lbl_audio_mode)
        except Exception:
            self._lbl_audio_mode = None
        # ── 放置音符模式狀態 ───────────────────────────────────
        # 三連音照長度插在一起，捲下拉時長短是連續的。名稱用「N 分音符」
        # （一個全音符切 N 份），和直拍那幾格同一套叫法；三連音的講法放在
        # 選項提示裡——寫進選項文字的話，工具列的下拉會照最長那項被撐寬。
        self._note_dur_items = [
            ('全音符',   4.0),
            ('二分音符',   2.0),
            ('四分音符',   1.0),
            ('6分音符',   2.0 / 3.0),
            ('八分音符',   0.5),
            ('12分音符',  1.0 / 3.0),
            ('16分音符',  0.25),
            ('24分音符',  1.0 / 6.0),
            ('32分音符',  0.125),
            ('48分音符',  1.0 / 12.0),
            ('64分音符',  0.0625),
        ]
        self._note_dur_tips = {
            '6分音符': '四分三連音（三顆佔兩拍）',
            '12分音符': '八分三連音（三顆佔一拍）',
            '24分音符': '16分三連音（六顆佔一拍）',
            '48分音符': '32分三連音（十二顆佔一拍）',
        }
        for division in settings.get('custom_note_divisions', []) or []:
            self._insert_custom_duration(division)
        # 自訂的比四分音符長（例如 3 分）會插在前面，預設要照長度找回四分音符
        self._ni_dur_idx = next(i for i, (_nm, beats) in enumerate(self._note_dur_items)
                                if abs(beats - 1.0) < 1e-9)

        # ── 音階輔助狀態 ───────────────────────────────────────
        from .music_theory import PATTERN_KINDS as _PATTERN_KINDS
        self._pattern_kinds = list(_PATTERN_KINDS)
        self._pattern_dirs = [('上行', 1), ('下行', -1), ('上行後下行', 0)]
        self._pattern_steps = [
            ('八分音符', 0.5), ('16 分音符', 0.25), ('八分三連', 1.0 / 3.0),
            ('16 分三連', 1.0 / 6.0), ('四分音符', 1.0), ('32 分音符', 0.125),
        ]
        # 調性：第一項是「自動」＝從譜面（含 MIDI 匯入的音高）偵測
        self._pattern_keys = [('自動偵測', None)]
        from .music_theory import PITCH_CLASS_NAMES as _PCN
        for tonic, name in enumerate(_PCN):
            self._pattern_keys.append(('%s 大調' % name, (tonic, 'major')))
        for tonic, name in enumerate(_PCN):
            self._pattern_keys.append(('%s 小調' % name, (tonic, 'minor')))
        self._pat_kind_idx = 0
        self._pat_dir_idx = 0
        self._pat_step_idx = 0
        self._pat_key_idx = 0
        # 手別篩選：只編一隻手，另一手變成幽靈音符
        self._hand_filters = [('雙手', 'all'), ('只編右手', 0), ('只編左手', 1)]
        self._hand_filter_idx = 0

        # ── 建立 UI ───────────────────────────────────────────────────
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self.statusBar().setVisible(bool(settings.get('show_statusbar', False)))
        self._user_shortcuts = []
        self.apply_shortcut_settings()

        # ── 定時刷新 title ────────────────────────────────────────────
        self._title_timer = QTimer(self)
        self._title_timer.timeout.connect(self._refresh_title)
        self._title_timer.start(500)

        # ── 操作紀錄 ──────────────────────────────────────────────────
        # 一個動作是在「下一次編輯」時才結算的，所以閒下來要自己收尾一次，
        # 否則最後一個動作會一直懸在那裡、關掉視窗就沒了。
        oplog.configure(settings.get('oplog_enabled', True))
        self._oplog_timer = QTimer(self)
        self._oplog_timer.timeout.connect(self._flush_oplog)
        self._oplog_timer.start(3000)

        # ── 自動儲存（備份） ──────────────────────────────────────────
        # 定時把還沒存的改動寫成備份，崩潰後重開可以救回來；不覆蓋原檔。
        import uuid
        self._autosave_session = uuid.uuid4().hex[:8]
        self._autosave_pending = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._on_autosave_tick)
        self._configure_autosave()
        # Give initial keyboard focus to the chart view so shortcuts (eg. Tab)
        # are immediately active without requiring a mouse click.
        try:
            self.view.setFocus()
        except Exception:
            pass

    # ==================================================================
    # 分割模式
    # ==================================================================

    @property
    def view(self) -> ChartView:
        """目前作用中的格子。未分割時就是唯一的那一格。"""
        return self._panes[self._active_pane]

    def _connect_view(self, v: ChartView) -> None:
        v.focus_gained.connect(lambda _v=v: self._on_pane_focus(_v))
        v.selection_changed.connect(self._on_selection_changed)
        v.status_changed.connect(self._on_status_changed)
        v.note_edited.connect(self._on_note_edited)
        v.play_requested.connect(self._play_range)
        v.play_full_requested.connect(self.play_full)
        v.play_midi_requested.connect(self._on_play_midi_requested)
        v.overlay_swap_requested.connect(self.swap_overlay_layers)
        v.new_chart_requested.connect(self._on_new_chart_requested)
        v.arrange_required.connect(self._on_arrange_required)
        v.note_placed.connect(self._on_note_placed)
        v.play_from_window_requested.connect(self.play_from_window)
        v.stop_requested.connect(self.stop_audio)
        v.pause_requested.connect(self.pause_audio)
        v.resume_requested.connect(self.resume_audio)
        v.note_input_changed.connect(self._on_note_input_mode_changed)
        v.set_measure_bpm_requested.connect(self.set_measure_bpm_at)
        v.set_measure_time_sig_requested.connect(self.set_measure_time_sig_at)
        v.insert_measure_requested.connect(self.insert_measure_at)
        v.delete_measure_requested.connect(self.delete_measure_at)

    def _visible_panes(self) -> List[ChartView]:
        return [v for v in self._panes if v.isVisible()]

    @property
    def _split_on(self) -> bool:
        return len(self._visible_panes()) > 1

    def _load_model_all(self, model: NoteModel) -> None:
        """把同一份 model 載入所有格子（分割時兩邊看同一份譜）。"""
        for v in self._panes:
            v.load_model(model)
        # 兩格各自 scroll_to_top 過，載完再對齊一次時間範圍
        if self._split_on:
            anchor = self.view
            for v in self._visible_panes():
                if v is not anchor:
                    v.sync_window_from(anchor)
        self._refresh_pattern_key_label()
        self._apply_format_mode()

    # ── 格式模式：JSON（完整）／XML（PAN 相容）──────────────────────────

    #: XML 模式要關掉的工具（PAN 沒有踏板）。用函式名比對，選單和工具列兩邊都抓得到。
    _PAN_DISABLED_SLOTS = frozenset({'generate_pedal_dialog', 'clear_pedal'})

    def _register_pan_gated(self, action, slot=None) -> None:
        name = slot if isinstance(slot, str) else getattr(slot, '__name__', '')
        if slot is not None and name not in self._PAN_DISABLED_SLOTS:
            return
        if not hasattr(self, '_pan_gated_actions'):
            self._pan_gated_actions = []
        self._pan_gated_actions.append(action)
        action.setEnabled(not getattr(self.view.model, 'pan_xml', False)
                          if hasattr(self, 'view') else True)

    def _apply_format_mode(self) -> None:
        """依目前譜面是 JSON 還是 PAN XML，把 PAN 沒有的功能開或關。

        XML 模式下：放置類型選單的 Soft／Staccato 變灰（看得到、選不了），
        編輯選單對應的類型、踏板工具、強弱曲線欄都關掉。
        """
        if not hasattr(self, 'view'):
            return
        from PyQt5 import sip
        from .pan_format import is_pan_note_type
        pan = bool(getattr(self.view.model, 'pan_xml', False))
        values = getattr(self, '_type_combo_values', None) or []
        tip = 'XML（PAN）格式沒有這個類型——另存成 JSON 才能用'
        for tbs in getattr(self, '_toolbars', ()):
            combo = getattr(tbs, 'type_combo', None)
            if combo is None:
                continue
            items = combo.model()
            for i, nt in enumerate(values[:combo.count()]):
                item = items.item(i)
                if item is None:
                    continue
                allowed = not pan or is_pan_note_type(nt)
                item.setEnabled(allowed)
                item.setToolTip('' if allowed else tip)
            current = combo.currentIndex()
            if 0 <= current < len(values) and pan and not is_pan_note_type(values[current]):
                combo.setCurrentIndex(0)          # 會走 _on_type_combo_changed 同步到格子
        alive = []
        for act in getattr(self, '_pan_gated_actions', ()):
            if sip.isdeleted(act):
                continue
            act.setEnabled(not pan)
            alive.append(act)
        self._pan_gated_actions = alive
        dyn = getattr(self, '_act_dyn_lane', None)
        if dyn is not None:
            dyn.setEnabled(not pan)
        for pane in getattr(self, '_panes', ()):
            pane._lane_flag_cache = None
            pane.update()
        self._refresh_title()

    def _set_judge_line_all(self, ms) -> None:
        """更新所有格子的判定時刻。

        判定線永遠畫在鍵盤上緣，`set_judge_line` 會順便把視窗捲到位，所以
        「跟隨 / 不跟隨」不再是一個選項——以前那組 `_set_follow_mode_all` /
        `_follow_to_ms_all` 已經整套移除。
        """
        for v in self._visible_panes():
            v.set_judge_line(ms)

    # ── 分割開關 / 方向 / 作用格 ──────────────────────────────────────

    def _on_split_toggled(self, checked: bool) -> None:
        if checked:
            self._enable_split()
        else:
            self._disable_split()

    def _enable_split(self) -> None:
        """開啟分割：把空的格子填進來，新格子預設音高模式。

        「兩個都沒開就照順序（左/上 先、右/下 後）；只缺一個就開那個。」
        """
        hidden = [i for i, v in enumerate(self._panes) if not v.isVisible()]
        if not hidden:
            self._refresh_split_ui()
            return
        if len(hidden) == len(self._panes):
            to_open = hidden                     # 兩個都沒開 → 照順序開
        else:
            to_open = hidden[:1]                 # 缺一個 → 就開那個

        src = self._visible_panes()
        anchor = src[0] if src else self._panes[0]
        model = anchor.model

        for i in to_open:
            v = self._panes[i]
            if v.model is not model:
                # 只有還沒接上同一份譜時才 load（load 會清掉共用的選取）
                v.load_model(model)
            else:
                v.rebuild_mapper()
            # 新開的預設音高模式；但若原本那格已經是音高模式，
            # 就退回小節均分，讓兩格保持不同形態（否則分割沒意義）。
            #
            # 未排譜的 MIDI 例外：它只有音高檢視有意義，硬指定 'measure'
            # 會被 set_view_mode 擋回 pitch 並發出 arrange_required——結果
            # 只是想開個分割對照左右手，卻跳出「要不要轉譜」。兩格都留在
            # 音高模式就好（時間軸範圍還是可以各看各的）。
            if getattr(model, 'midi_unarranged', False):
                v.set_view_mode('pitch')
            else:
                v.set_view_mode('measure' if anchor.pitch_mode else 'pitch')
            v.show()
            self._apply_note_input_settings(v)
        # 新格子的時間範圍直接對齊原本那格（之後靠 time_sync 持續連動）
        for i in to_open:
            if self._panes[i] is not anchor:
                self._panes[i].sync_window_from(anchor)

        self._apply_split_layout()
        # 作用格切到新開的那一格
        self.set_active_pane(to_open[-1])
        self._refresh_split_ui()

    def _disable_split(self, keep: Optional[int] = None) -> None:
        """關閉分割：預設留下目前作用中的格子。"""
        if keep is None:
            keep = self._active_pane
        if not self._panes[keep].isVisible():
            keep = next((i for i, v in enumerate(self._panes) if v.isVisible()), 0)
        for i, v in enumerate(self._panes):
            if i != keep:
                v.hide()
        self._apply_split_layout()      # 收掉獨立視窗、把格子放回主視窗
        self.set_active_pane(keep)
        self._refresh_split_ui()

    def _cycle_split_layout(self) -> None:
        """左右平分寬度 → 上下平分高度 → 獨立視窗 → 疊層分割 → 循環。"""
        self._split_layout = {
            'h': 'v',
            'v': 'window',
            'window': 'overlay',
            'overlay': 'h',
        }.get(self._split_layout, 'h')
        self._apply_split_layout()
        self._refresh_split_ui()

    # ── 版面套用（splitter ⇄ 獨立視窗）────────────────────────────────

    def _dock_pane(self, i: int) -> None:
        """把格子放回主視窗的 splitter（保持 pane 0 在前）。"""
        v = self._panes[i]
        if self._splitter.indexOf(v) >= 0:
            return
        self._splitter.insertWidget(min(i, self._splitter.count()), v)

    def _close_detached(self) -> None:
        win = self._detached_win
        if win is None:
            return
        self._detached_win = None
        try:
            win.closed.disconnect()
        except Exception:
            pass
        # 這個視窗的工具列跟著它一起消失，先從清單解除登記，之後的 _refresh_*
        # 才不會去碰已經被 deleteLater 的 widget
        self._detach_toolbar_cleanup()
        # 和 _clear_overlay 一樣：先把格子接回 splitter，否則刪掉視窗時
        # 會把還掛在它底下的格子一起刪掉
        for i in range(len(self._panes)):
            if win.isAncestorOf(self._panes[i]):
                self._dock_pane(i)
        try:
            win.close()
            win.deleteLater()
        except Exception:
            pass

    def _clear_overlay(self) -> None:
        """拆掉疊層容器，把格子恢復成一般（實色、可互動）狀態。"""
        for p in self._panes:
            p.set_overlay_role('')
            p.set_input_enabled(True)
        stack = self._overlay_stack
        if stack is None:
            return
        self._overlay_stack = None
        # 一定要先把格子接回 splitter，否則刪掉容器時會連格子一起刪掉
        for i in range(len(self._panes)):
            self._dock_pane(i)
        stack.setParent(None)
        try:
            stack.deleteLater()
        except Exception:
            pass

    def _apply_overlay_layout(self) -> None:
        """疊層分割：一格當底層實色，另一格疊在上面半透明。

        預設 pane 1（音高/MIDI）在上面；輕點 Shift 可以對調。
        """
        self._close_detached()
        stack = self._overlay_stack
        if stack is None:
            stack = OverlayStack(self)
            self._overlay_stack = stack
            self._splitter.insertWidget(0, stack)
        top = 1 if self._overlay_top not in (0, 1) else self._overlay_top
        base = 1 - top
        # 先加底層再加上層，z 序才對
        stack.add_pane(self._panes[base])
        stack.add_pane(self._panes[top])
        self._panes[base].set_overlay_role('base')
        self._panes[top].set_overlay_role('top')
        self._refresh_overlay_input()
        stack.show()

    def swap_overlay_layers(self) -> None:
        """疊層分割：對調哪一層是半透明的（輕點 Shift 觸發）。"""
        if self._split_layout != 'overlay' or self._overlay_stack is None:
            return
        self._overlay_top = 1 - self._overlay_top
        self._apply_overlay_layout()
        self._refresh_split_ui()
        for p in self._panes:
            QWidget.update(p)
        base = self._panes[1 - self._overlay_top]
        top = self._panes[self._overlay_top]
        self.statusBar().showMessage(
            t('overlay_swap_status', self._mode_name(base), self._mode_name(top)), 4000
        )

    @staticmethod
    def _mode_name(pane: ChartView) -> str:
        return {
            'measure': t('tb_time_uniform_measure'),
            'time': t('tb_time_uniform_time'),
            'pitch': t('tb_time_uniform_pitch'),
        }.get(pane.view_mode, pane.view_mode)

    def _refresh_overlay_input(self) -> None:
        """疊層時只有作用中的那層吃滑鼠事件，另一層讓事件穿透。"""
        if self._split_layout != 'overlay' or self._overlay_stack is None:
            for p in self._panes:
                p.set_input_enabled(True)
            return
        for i, p in enumerate(self._panes):
            p.set_input_enabled(i == self._active_pane)

    def _apply_split_layout(self) -> None:
        """依目前版面把兩個格子擺好。

        獨立視窗 / 疊層都只在分割開啟時成立；分割關掉時一定把格子收回
        splitter，否則主視窗會變成空的或留著半透明的格子。
        """
        vis = [i for i, v in enumerate(self._panes) if v.isVisible()]
        split_on = len(vis) > 1
        want_window = (self._split_layout == 'window' and split_on)
        want_overlay = (self._split_layout == 'overlay' and split_on)

        if want_overlay:
            self._apply_overlay_layout()
            return

        self._clear_overlay()
        if not want_window:
            self._close_detached()
            for i in range(len(self._panes)):
                self._dock_pane(i)
            self._splitter.setOrientation(
                Qt.Vertical if self._split_layout == 'v' else Qt.Horizontal
            )
            self._equalize_split()
            return

        # 獨立視窗：pane 0 留在主視窗，pane 1 拆出去
        self._dock_pane(0)
        pane = self._panes[1]
        if self._detached_win is None:
            win = DetachedPaneWindow(self._detached_title(pane), self)
            win.closed.connect(self._on_detached_closed)
            self._detached_win = win
            win.set_pane(pane)
            # 拆出去的格子要能獨立操作，所以這個視窗有自己的一條工具列
            self._attach_detached_toolbar()
            win.show()
        else:
            self._detached_win.setWindowTitle(self._detached_title(pane))
            if self._tb_detached is None:
                self._attach_detached_toolbar()
            self._detached_win.show()
        pane.show()
        # 讓主視窗的選單快捷鍵在獨立視窗裡也有效
        self._make_menu_shortcuts_application_wide()

    def _detached_title(self, pane: ChartView) -> str:
        return t('wnd_detached_title', self._mode_name(pane))

    def _on_detached_closed(self) -> None:
        """關掉獨立視窗 = 結束分割，留下主視窗那一格。"""
        self._detached_win = None
        self._detach_toolbar_cleanup()
        self._split_layout = 'h'
        for tbs in self._toolbars:
            if tbs.split_act is None:
                continue
            tbs.split_act.blockSignals(True)
            tbs.split_act.setChecked(False)
            tbs.split_act.blockSignals(False)
        self._disable_split(keep=0)

    def _make_menu_shortcuts_application_wide(self) -> None:
        """把選單快捷鍵改成整個應用程式範圍。

        獨立視窗沒有自己的選單列，不這樣做的話 Ctrl+Z / Ctrl+C 之類在
        那邊會沒反應。動作本身都是走 `self.view`（作用中的格子），所以
        改成 ApplicationShortcut 後行為仍然正確。
        """
        def walk(menu):
            for act in menu.actions():
                sub = act.menu()
                if sub is not None:
                    walk(sub)
                elif not act.shortcut().isEmpty():
                    act.setShortcutContext(Qt.ApplicationShortcut)
        try:
            walk(self.menuBar())
        except Exception:
            pass

    def _equalize_split(self) -> None:
        """平分寬度（左右）或高度（上下）。"""
        if self._split_layout == 'window':
            return
        n = self._splitter.count() and len([
            i for i in range(self._splitter.count())
            if self._splitter.widget(i) is not None and self._splitter.widget(i).isVisible()
        ])
        if n <= 1:
            return
        total = (self._splitter.width() if self._split_layout == 'h'
                 else self._splitter.height())
        total = max(n, int(total))
        self._splitter.setSizes([total // n] * n)

    def _on_pane_focus(self, v: ChartView) -> None:
        try:
            role = self._panes.index(v)
        except ValueError:
            return
        if role != self._active_pane and v.isVisible():
            self.set_active_pane(role, give_focus=False)

    def set_active_pane(self, role: int, give_focus: bool = True) -> None:
        """切換工具列作用中的格子（紅=左/上，藍=右/下）。"""
        if not (0 <= role < len(self._panes)):
            return
        v = self._panes[role]
        if not v.isVisible():
            self._refresh_split_ui()
            return
        self._active_pane = role
        for i, p in enumerate(self._panes):
            p.pane_active = (i == role)
            p.split_active = self._split_on
            QWidget.update(p)
        self._refresh_overlay_input()
        self._sync_toolbar_to_active_pane()
        self._refresh_split_ui()
        if give_focus:
            try:
                v.setFocus()
            except Exception:
                pass

    def _sync_toolbar_to_active_pane(self) -> None:
        """（保留舊名）把每條工具列刷成它負責那一格的狀態。"""
        self._sync_toolbars()

    def _sync_toolbars(self) -> None:
        """把「這條工具列負責的那一格」的狀態回填到它的按鈕。

        獨立視窗模式下兩條工具列各看各的格子，所以不能只看作用格。
        """
        if self._tb_action_busy:
            return          # 動作跑到一半，等它跑完再一次刷（見 _bind）
        for tbs in self._toolbars:
            v = self._pane_of(tbs)
            for act, val in (
                (tbs.preview_act, v.preview_mode),
                (tbs.note_input_act, v._note_input_mode),
            ):
                if act is None:
                    continue
                act.blockSignals(True)
                act.setChecked(bool(val))
                act.blockSignals(False)
        self._refresh_view_mode_action()
        # blockSignals 會擋掉 toggled，收合狀態要自己補刷
        self._refresh_toolbar_groups()

    def _refresh_toolbar_groups(self) -> None:
        """模式沒開就把它的參數欄整組收起來，開了才展開。

        變灰的話那些下拉框還是占著位置、還是要用眼睛掃過去；使用者要的是沒開
        的時候畫面乾淨，所以改成真的隱藏。
        """
        for tbs in self._toolbars:
            for head, group in ((tbs.note_input_act, tbs.note_input_group),
                                (tbs.pattern_act, tbs.pattern_group)):
                if head is None:
                    continue
                show = head.isChecked()
                for widget in group:
                    widget.setVisible(show)
        self._refresh_pattern_key_label()

    def _refresh_pattern_key_label(self) -> None:
        """把偵測到的調性顯示在工具列上（MIDI 匯入的譜就是從 MIDI 音高推的）。"""
        for tbs in self._toolbars:
            label = tbs.pattern_key_label
            if label is None:
                continue
            key = self._pane_of(tbs).detect_chart_key()
            if key is None:
                label.setText('調性：－')
                label.setToolTip('譜面還沒有音高資料，用右邊的下拉自己選。')
            else:
                sure = '' if key.confidence >= 0.35 else '?'
                label.setText('調性：%s%s' % (key.name(), sure))
                label.setToolTip(
                    '由譜面音高偵測（信心 %.2f）。%s\n下拉選「自動」以外的值可以蓋掉它。'
                    % (key.confidence,
                       '相當明確。' if key.confidence >= 0.35
                       else '不太確定，可能和關係大小調混淆。'))

    # ── 音階輔助 ──────────────────────────────────────────────────────

    def _on_pattern_toggle(self, checked: bool) -> None:
        """音階輔助開關。和放置模式互斥，兩個都在等左鍵。"""
        view = self.view
        view.set_pattern_mode(bool(checked))
        if checked:
            for tbs in self._toolbars:
                if tbs.note_input_act is not None and tbs.note_input_act.isChecked():
                    tbs.note_input_act.blockSignals(True)
                    tbs.note_input_act.setChecked(False)
                    tbs.note_input_act.blockSignals(False)
        self._refresh_toolbar_groups()

    def _on_pattern_kind_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._pattern_kinds):
            self._pat_kind_idx = idx
            for v in self._panes:
                v.set_pattern_params(kind=self._pattern_kinds[idx][1])

    def _on_pattern_dir_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._pattern_dirs):
            self._pat_dir_idx = idx
            for v in self._panes:
                v.set_pattern_params(direction=self._pattern_dirs[idx][1])

    def _on_pattern_step_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._pattern_steps):
            self._pat_step_idx = idx
            for v in self._panes:
                v.set_pattern_params(step_beats=self._pattern_steps[idx][1])

    def _on_pattern_key_changed(self, idx: int) -> None:
        if not (0 <= idx < len(self._pattern_keys)):
            return
        self._pat_key_idx = idx
        spec = self._pattern_keys[idx][1]
        from .music_theory import Key
        for v in self._panes:
            if spec is None:
                v.set_pattern_params(use_auto_key=True)
            else:
                v.set_pattern_params(key_override=Key(spec[0], spec[1]))
        self._refresh_pattern_key_label()

    def _on_hand_filter_changed(self, idx: int) -> None:
        if not (0 <= idx < len(self._hand_filters)):
            return
        self._hand_filter_idx = idx
        hand = self._hand_filters[idx][1]
        for v in self._panes:
            v.set_hand_filter(hand)
        # 兩條工具列要同步，不然主視窗和獨立視窗會顯示不同的篩選狀態
        for tbs in self._toolbars:
            combo = tbs.hand_filter_combo
            if combo is not None and combo.currentIndex() != idx:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)

    def _refresh_split_ui(self) -> None:
        """更新分割相關按鈕的勾選狀態與文字（左右/上下用語會跟著方向變）。"""
        split_on = self._split_on
        for p in self._panes:
            p.split_active = split_on
            QWidget.update(p)

        layout = self._split_layout
        dir_key = t({
            'h': 'tb_split_dir_h',
            'v': 'tb_split_dir_v',
            'window': 'tb_split_dir_w',
            'overlay': 'tb_split_dir_o',
        }.get(layout, 'tb_split_dir_h'))

        keys = {
            'h': ('tb_pane_left', 'tb_pane_right'),
            'v': ('tb_pane_top', 'tb_pane_bottom'),
            'window': ('tb_pane_main', 'tb_pane_detached'),
            'overlay': ('tb_pane_base', 'tb_pane_overlay'),
        }.get(layout, ('tb_pane_left', 'tb_pane_right'))
        if layout == 'overlay' and self._overlay_top == 0:
            keys = (keys[1], keys[0])      # 對調後 pane 0 才是疊層

        if self._detached_win is not None:
            self._detached_win.setWindowTitle(self._detached_title(self._panes[1]))

        for tbs in self._toolbars:
            act = tbs.split_act
            if act is not None:
                act.blockSignals(True)
                act.setChecked(split_on)
                act.blockSignals(False)

            act = tbs.split_dir_act
            if act is not None:
                self._set_labeled_text(act, dir_key, t('tb_split_dir_tip'))
                act.setEnabled(split_on)

            # 疊層模式才提示 Shift 可以切換塗層
            if tbs.split_hint is not None:
                tbs.split_hint.setVisible(split_on and layout == 'overlay')

            # 有自己格子的工具列（獨立視窗模式）：這排按鈕不再是「選一格」，
            # 而是標示「這條工具列作用在哪一格」，所以固定勾自己的、不能點。
            own = tbs.pane
            for role, a in enumerate(tbs.pane_acts):
                a.blockSignals(True)
                a.setText(t(keys[role]))
                a.setChecked(role == (own if own is not None else self._active_pane))
                a.setEnabled(self._panes[role].isVisible() and own is None)
                a.setToolTip(t('tb_pane_tip' if own is None else 'tb_pane_tip_window'))
                a.blockSignals(False)

    # ==================================================================
    # 選單建立
    # ==================================================================

    def _refresh_window_title(self) -> None:
        """視窗標題：有開檔就顯示檔名，否則顯示程式名。"""
        base = t('wnd_title')
        model = getattr(self.view, 'model', None) if getattr(self, '_panes', None) else None
        path = getattr(model, 'current_file', None)
        self.setWindowTitle('%s — %s' % (os.path.basename(path), base) if path else base)

    def apply_language(self, lang: str) -> None:
        """NosMania 啟動器換了語言：記進設定，和目前不同就就地換掉介面文字。"""
        from .i18n import get_lang, set_lang
        if lang not in ('zh_tw', 'zh_cn', 'en'):
            return
        if settings.get('language', 'zh_tw') != lang:
            settings.set('language', lang)
        if get_lang() != lang:
            set_lang(lang)
            self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """換語言後就地重建介面文字，不重啟程式。

        選單和工具列的字串都是在 `_build_menu` / `_build_toolbar` 裡透過 t()
        取的，所以清掉重跑一次就會套用新語言。舊做法是 `os.execv` 重啟——那在
        打包後的 exe 根本不成立（`sys.executable` 是 exe，沒有 `-m` 可用），
        從別的工作目錄跑也會失敗。
        """
        tabs = [tbs.tabs.currentIndex() if tbs.tabs is not None else 0
                for tbs in self._toolbars]
        # 兩條工具列（主視窗 + 獨立視窗）都要重建，字串才會一起換掉
        self.menuBar().clear()
        for bar in self.findChildren(QToolBar):
            self.removeToolBar(bar)
            bar.deleteLater()
        if self._detached_win is not None:
            self._detached_win.clear_toolbar()
            # 選單列共用主視窗的 QMenu，主視窗那邊 clear() 後這些就是舊語言的
            self._detached_win.clear_menubar()
        self._toolbars = []
        self._tb_main = None
        self._tb_detached = None
        self._build_menu()
        self._build_toolbar()
        if self._detached_win is not None:
            self._attach_detached_toolbar()
            self._make_menu_shortcuts_application_wide()
        # 還原切換語言前的介面狀態（放置模式等都記在格子上，不用另外存）
        try:
            for tbs, idx in zip(self._toolbars, tabs):
                if tbs.tabs is not None:
                    tbs.tabs.setCurrentIndex(idx)
            self._refresh_split_ui()
            self._sync_toolbars()
            self._refresh_vol_rows()
        except Exception:                       # noqa: BLE001
            logging.debug('retranslate: state restore skipped', exc_info=True)
        self._refresh_window_title()
        self.statusBar().showMessage(t('settings_language'), 2000)
        for pane in self._panes:
            pane.update()

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # ── 檔案 ──────────────────────────────────────────────────────
        file_m = mb.addMenu(t('menu_file'))
        self._act_new = self._add_action(file_m, t('action_new_chart'), self.new_chart_dialog,
                                         QKeySequence.New)
        self._add_action(file_m, t('action_open'), self.open_file, QKeySequence.Open)
        # 匯入 MIDI 音軌（子選單，不常用）
        midi_sub = file_m.addMenu(t('action_import_midi_sub'))
        self._add_action(midi_sub, t('action_open_midi_right'), lambda: self._open_midi_hand(0))
        self._add_action(midi_sub, t('action_open_midi_left'),  lambda: self._open_midi_hand(1))
        self._add_action(midi_sub, t('action_open_midi_overlay'), self._open_midi_overlay)
        # 音檔 → MIDI（ByteDance 鋼琴轉譜模型），轉完走一般的 MIDI 匯入
        ai_sub = file_m.addMenu('AI 轉譜（音檔 → 譜面）')
        self._add_action(ai_sub, '從音檔轉譜…', self.transcribe_audio_ai,
                         feature='ai_transcribe')
        self._add_action(ai_sub, '轉譜環境（安裝／移除）…', self._manage_ai_transcribe,
                         feature='ai_transcribe')
        file_m.addSeparator()
        self._add_action(file_m, t('action_save'), self.save_file, QKeySequence.Save)
        self._add_action(file_m, t('action_save_as'), self.save_file_as, 'Ctrl+Shift+S')
        self._add_action(file_m, t('action_save_json'), self.save_as_json, 'Ctrl+Shift+J')
        self._add_action(file_m, t('action_save_xml'), self.save_as_xml, 'Ctrl+Shift+X')
        self._add_action(file_m, t('action_save_xml_midi_restore'), self.save_as_xml_midi_restore)
        self._add_action(file_m, '匯出 MIDI…', self.export_midi_file)
        self._add_action(file_m, t('action_export_song'), self.export_song)
        self._add_action(file_m, '輸出 Hiraeth 歌曲包（ZIP）…', self.export_hiraeth_packages)
        file_m.addSeparator()
        self._add_action(file_m, t('action_quit'), self.close, QKeySequence.Quit)

        # ── 編輯 ──────────────────────────────────────────────────────
        edit_m = mb.addMenu(t('menu_edit'))
        self._add_action(edit_m, t('action_undo'), lambda: self.view.undo(), QKeySequence.Undo)
        redo_act = self._add_action(edit_m, t('action_redo'), lambda: self.view.redo())
        # 兩種習慣都收：Ctrl+Y（Windows 慣例）與 Ctrl+Shift+Z（Photoshop／DAW 慣例）
        redo_act.setShortcuts([QKeySequence('Ctrl+Y'), QKeySequence('Ctrl+Shift+Z')])
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_select_all'), lambda: self.view.select_all(), QKeySequence.SelectAll)
        self._add_action(edit_m, t('action_deselect'), lambda: self.view.deselect_all())
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_delete'), lambda: self.view.delete_selected(), QKeySequence.Delete)
        self._add_action(edit_m, t('action_duplicate'), lambda: self.view.duplicate_selected())
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_copy'), lambda: self.view.copy_to_clipboard(), QKeySequence.Copy)
        self._add_action(edit_m, t('action_paste'), lambda: self.view.paste_from_clipboard(), QKeySequence.Paste)
        edit_m.addSeparator()

        # 寬度
        self._add_action(edit_m, t('action_width2'), lambda: self.view.set_width_selected(2))
        self._add_action(edit_m, t('action_width3'), lambda: self.view.set_width_selected(3))
        self._add_action(edit_m, '鍵寬 2 / 3 切換', self._shortcut_toggle_width)
        edit_m.addSeparator()

        # ── 音符類型（直接展開，不再藏子選單）
        self._add_action(edit_m, t('action_type_tap'), lambda: self.view.set_type_selected(0))
        self._register_pan_gated(self._add_action(
            edit_m, t('action_type_soft'), lambda: self.view.set_type_selected(1)))
        self._add_action(edit_m, t('action_type_long'), lambda: self.view.set_type_selected(2))
        self._register_pan_gated(self._add_action(
            edit_m, t('action_type_staccato'), lambda: self.view.set_type_selected(3)))
        self._add_action(edit_m, '點擊 / 長條切換', self._shortcut_toggle_tap_hold)
        edit_m.addSeparator()

        # ── 左右手（直接展開）
        self._add_action(edit_m, t('action_right_hand'), lambda: self.view.set_hand_selected(0))
        self._add_action(edit_m, t('action_left_hand'), lambda: self.view.set_hand_selected(1))
        edit_m.addSeparator()

        self._add_action(edit_m, t('action_shift_pitch'), self.shift_pitch_dialog)
        self._add_action(edit_m, t('action_align_time'), self.align_selected_time_dialog)

        # 音訊
        # 注意：P / Shift+P / S 快捷鍵由 ChartView.keyPressEvent 處理，此處不重複設定
        audio_m = mb.addMenu(t('menu_audio'))
        self._add_action(audio_m, t('action_load_wav'), self.load_wav)
        # 雙音源載入開關
        self._act_dual = QAction('雙音源載入', self, checkable=True)
        self._act_dual.setChecked(False)
        self._act_dual.triggered.connect(self._on_toggle_dual_audio)
        audio_m.addAction(self._act_dual)
        audio_m.addSeparator()
        # 含打擊聲勾選開關
        self._act_hit = QAction(t('action_hit_sound'), self, checkable=True)
        self._act_hit.setChecked(bool(self._hit_sound_persistent))
        self._act_hit.triggered.connect(self._on_hit_toggle)
        audio_m.addAction(self._act_hit)
        audio_m.addSeparator()
        self._add_action(audio_m, t('action_play_full'),   self.play_full)
        self._add_action(audio_m, t('action_play_window'), self.play_window)
        self._add_action(audio_m, t('action_play_sel'), self.play_selection)
        audio_m.addSeparator()
        self._add_action(audio_m, t('action_pause'), self.pause_audio)
        self._add_action(audio_m, t('action_resume'), self.resume_audio)
        self._add_action(audio_m, t('action_stop'), self.stop_audio)
        self._add_action(audio_m, t('action_restart'), self.restart_audio)

        # 小節（與工具列「小節」分頁同一份內容）
        measure_m = mb.addMenu(t('menu_measure'))
        measure_m.setToolTipsVisible(True)
        for label, slot, tip in self._measure_entries():
            act = self._add_action(measure_m, label, slot)
            if tip:
                act.setToolTip(tip)
        measure_m.addSeparator()
        hint = measure_m.addAction('（想針對某一小節：在該處按右鍵 → 小節）')
        hint.setEnabled(False)

        # 工具（分類；與工具列「工具」分頁同一份內容）
        tools_m = mb.addMenu(t('menu_tools'))
        self._add_action(tools_m, t('action_smart_midi_chart'), self.smart_midi_chart_dialog)
        self._add_action(tools_m, 'MIDI 轉譜…（重新排整份譜面）', self.rearrange_dialog)
        tools_m.addSeparator()
        for title, entries in self._tool_groups():
            sub = tools_m.addMenu(title)
            for label, slot in entries:
                self._add_action(sub, label, slot)

        # 檢視
        view_m = mb.addMenu(t('menu_view'))
        self._add_action(view_m, t('action_zoom_in'),  lambda: self.view.zoom(0.5), '=')
        self._add_action(view_m, t('action_zoom_out'), lambda: self.view.zoom(2.0), '-')
        view_m.addSeparator()
        self._act_inv = QAction(t('action_scroll_invert'), self, checkable=True)
        self._act_inv.setChecked(bool(settings.get('scroll_invert', False)))
        self._act_inv.triggered.connect(self._toggle_scroll_invert)
        view_m.addAction(self._act_inv)

        # 底部狀態列預設關閉——它在畫布下方留一條淺色橫條，和獨立視窗（沒有
        # 狀態列）擺在一起時兩邊高度就不一樣。需要那些數值時再打開。
        self._act_statusbar = QAction(t('action_show_statusbar'), self, checkable=True)
        self._act_statusbar.setChecked(bool(settings.get('show_statusbar', False)))
        self._act_statusbar.toggled.connect(self._toggle_statusbar)
        view_m.addAction(self._act_statusbar)

        # 音高編號：遊戲的 scale_piano(1~88) 或 MIDI(21~108)，兩者固定差 20
        view_m.addSeparator()
        from PyQt5.QtWidgets import QActionGroup
        pitch_m = view_m.addMenu(t('menu_pitch_numbering'))
        self._pitch_num_group = QActionGroup(self)
        self._pitch_num_group.setExclusive(True)
        use_midi = bool(settings.get('show_midi_pitch', False))
        for label, is_midi in ((t('action_pitch_piano'), False),
                               (t('action_pitch_midi'), True)):
            act = QAction(label, self, checkable=True)
            act.setChecked(use_midi == is_midi)
            act.triggered.connect(
                lambda _c, m=is_midi: self._set_pitch_numbering(m)
            )
            self._pitch_num_group.addAction(act)
            pitch_m.addAction(act)
        self._set_pitch_numbering(use_midi, save=False)

        # 力度呈現（只在音高模式作用）
        vel_m = view_m.addMenu('力度顯示（音高模式）')
        vel_m.setToolTipsVisible(True)
        self._act_vel_shade = QAction('用亮度表示力度', self, checkable=True)
        self._act_vel_shade.setChecked(bool(settings.get('pitch_velocity_shading', True)))
        self._act_vel_shade.setToolTip('力度愈輕，音符畫得愈暗。')
        self._act_vel_shade.toggled.connect(
            lambda on: self._set_velocity_display('pitch_velocity_shading', on))
        vel_m.addAction(self._act_vel_shade)

        self._act_vel_num = QAction('在音符上顯示力度數字', self, checkable=True)
        self._act_vel_num.setChecked(bool(settings.get('pitch_velocity_numbers', True)))
        self._act_vel_num.setToolTip(
            '音符夠高時，下半部多畫一格深色底的橘色數字 = 力度（0~127）。\n'
            '音高數字是黑字直接畫在音符上，兩者底色、字色、位置都不同。')
        self._act_vel_num.toggled.connect(
            lambda on: self._set_velocity_display('pitch_velocity_numbers', on))
        vel_m.addAction(self._act_vel_num)

        self._act_dyn_lane = QAction('顯示強弱曲線欄（左右邊緣）', self, checkable=True)
        self._act_dyn_lane.setChecked(bool(settings.get('pitch_dynamics_lane', True)))
        self._act_dyn_lane.setToolTip(
            '左邊緣 = 左手，右邊緣 = 右手。在欄位上左鍵拖曳就能放記號：\n'
            '上下決定時間、左右決定強弱。右鍵選單可以選 pp~fff 與漸變。')
        self._act_dyn_lane.toggled.connect(
            lambda on: self._set_velocity_display('pitch_dynamics_lane', on))
        vel_m.addAction(self._act_dyn_lane)

        # 調性輔助（音高模式）
        view_m.addSeparator()
        scale_m = view_m.addMenu('調性輔助（音高模式）')
        scale_m.setToolTipsVisible(True)
        # 這一項現在是「音高欄位分色」下拉的其中一個選項（偏好設定 → 顯示）。
        # 選單留著當快捷開關：勾起來 = 調性分色，取消 = 回到黑白鍵分色。
        self._act_scale_hl = QAction('調性高亮', self, checkable=True)
        self._act_scale_hl.setChecked(
            str(settings.get('pitch_column_mode', 'blackwhite')) == 'scale')
        self._act_scale_hl.setToolTip(
            '調內的音格鋪一層淡底、主音再亮一點，調外的琴鍵壓暗。\n'
            '取消勾選會回到黑白鍵分色。調性取自「編輯」分頁的調性下拉。')
        self._act_scale_hl.toggled.connect(
            lambda on: self._set_pitch_column_mode('scale' if on else 'blackwhite'))
        scale_m.addAction(self._act_scale_hl)

        self._act_scale_lock = QAction('鎖調：只放得下調內音', self, checkable=True)
        self._act_scale_lock.setChecked(bool(settings.get('pitch_scale_lock', False)))
        self._act_scale_lock.setToolTip(
            '放音符與音階起點會自動吸到最近的調內音。\n'
            '方向鍵的 ±1 半音不受影響——要放升降記號時用它。')
        self._act_scale_lock.toggled.connect(
            lambda on: self._set_velocity_display('pitch_scale_lock', on))
        scale_m.addAction(self._act_scale_lock)

        self._act_ghost = QAction('幽靈音符（另一手畫成影子）', self, checkable=True)
        self._act_ghost.setChecked(bool(settings.get('ghost_other_hand', True)))
        self._act_ghost.setToolTip(
            '「編輯」分頁選了只編一隻手時，另一手畫成半透明的參考影子。\n'
            '關掉的話另一手就完全不畫。兩種情況都選不到它。')
        self._act_ghost.toggled.connect(
            lambda on: self._set_velocity_display('ghost_other_hand', on))
        view_m.addAction(self._act_ghost)

        # 設定
        settings_m = mb.addMenu(t('menu_settings'))
        self._add_action(settings_m, t('action_preferences'), self.open_preferences_dialog)
        # 打擊聲類型開關（右手 / 左手 / 小節拍）
        settings_m.addSeparator()
        self._act_hit_right = QAction('打擊聲：右手', self, checkable=True)
        self._act_hit_right.setChecked(True)
        self._act_hit_right.toggled.connect(lambda ch: self._on_hit_enable_toggle('right', ch))
        settings_m.addAction(self._act_hit_right)

        self._act_hit_left = QAction('打擊聲：左手', self, checkable=True)
        self._act_hit_left.setChecked(True)
        self._act_hit_left.toggled.connect(lambda ch: self._on_hit_enable_toggle('left', ch))
        settings_m.addAction(self._act_hit_left)

        self._act_hit_beat = QAction('打擊聲：小節拍', self, checkable=True)
        self._act_hit_beat.setChecked(True)
        self._act_hit_beat.toggled.connect(lambda ch: self._on_hit_enable_toggle('beat', ch))
        settings_m.addAction(self._act_hit_beat)

    def _measure_entries(self):
        """小節操作：(標籤, slot, tooltip)。選單列與工具列「小節」分頁共用。

        這幾個都作用在「譜尾」或「目前視窗中央那一小節」——因為選單/工具列
        沒有點擊位置可以參考。要針對特定小節，走右鍵選單的「小節」。
        """
        return [
            (t('tb_add_measure'), self.add_measure_dialog,
             '在譜面末尾接上一個空白小節'),
            (t('tb_delete_measure'), self.delete_measure_dialog,
             '刪除目前視窗中央所在的小節（含其中的音符）'),
            (t('tb_set_measure_bpm'), self.set_measure_bpm_dialog,
             '修改目前視窗中央所在小節的 BPM'),
            (t('tb_measures_bpm'), self.change_measures_bpm_dialog,
             t('tb_measures_bpm_tip')),
            (self._tool_label('action_set_measure_time_signature', '修改小節拍號…'),
             self.set_measure_time_sig_dialog,
             '修改目前視窗中央所在小節的拍號。整首的總拍號請用 工具 → 樂曲總資訊'),
            ('事件（速度／音效）…', self.edit_events_dialog,
             '編輯譜面事件：速度變化（BPM）、殘響／回音／EQ 參數、區段標記。\n'
             'PAN 靠速度事件知道速度；JSON 與 XML 都會存。'),
        ]

    def edit_events_dialog(self) -> None:
        """開事件編輯。按確定才寫進譜面（可以復原）。"""
        from .event_dialog import EventEditorDialog
        m = self.view.model
        if not m.has_chart():
            QMessageBox.warning(self, t('dlg_warn'), t('status_need_chart'))
            return
        dlg = EventEditorDialog(self, m, current_ms=self.view.judge_line_view_ms(),
                                jump=self._jump_to_ms)
        if dlg.exec_() != EventEditorDialog.Accepted:
            return
        events = dlg.events()
        if events == m.sorted_events():
            return
        m.push_history()
        m.events = events
        m.dirty = True
        self.view.note_edited.emit()
        self._refresh_title()
        self.statusBar().showMessage('已更新 %d 個事件' % len(events), 3000)

    def _tool_groups(self):
        """工具的分類表。選單與工具列「工具」分頁共用同一份，不會兩邊走味。

        「範圍內自動排序」「全譜重整排序」已移除——前者右鍵就有，後者的工作
        現在由智慧寫譜接手。五線譜編輯也一併拿掉。
        """
        return [
            ('節奏與音高', [
                ('量化（對齊到格點）…', self.quantize_dialog),
                ('把音符吸到調內', self.snap_notes_to_key),
                ('修補缺漏的拍點（BPM 不對時）…', self.repair_beat_entries),
            ]),
            ('音符整理', [
                (t('action_resolve_hold_tails'), self.resolve_hold_tails_dialog),
                (t('action_resolve_horizontal_overlaps'), self.resolve_horizontal_overlaps),
                (self._tool_label('action_hold_length_fix', '長押長度修整…'),
                 self.hold_length_fix_dialog),
                ('移除重複音符（同 start/pitch）…', self.remove_duplicate_start_pitch_dialog),
                ('取消整份譜面的隱藏音符…', self.unhide_all_dialog),
            ]),
            ('難度', [
                # 預設走這條：直接寫進樂曲資料夾，生完進遊戲就看得到。
                ('生成其他難度到樂曲資料夾…', self.generate_into_song_folder),
                # 舊的那條留著：來源不在樂曲資料夾裡（例如剛轉好還沒歸檔的
                # 譜）時還是要有辦法生。
                ('生成難度成獨立檔案…', self.generate_difficulties_dialog),
                ('掃描曲庫，補齊缺少的難度…', self.fill_library_difficulties),
            ]),
            ('樂曲總資訊', [
                ('調整樂曲總資訊（BPM / 總拍號 / 整體位移）…', self.song_info_dialog),
            ]),
            ('以 MIDI 修復', [
                ('從 MIDI 還原音高與表情（力度／踏板）…',
                 self.restore_from_midi_dialog),
                (t('action_conform_beats_midi'), self.conform_beats_to_midi_dialog),
                (t('action_align_reference_midi'), self.align_reference_midi_dialog),
            ]),
            ('延音踏板', [
                ('依和聲生成踏板…', self.generate_pedal_dialog),
                ('清除踏板', self.clear_pedal),
            ]),
            ('從別份譜面搬表情', [
                ('從另一份譜面搬音高／力度／踏板…', self.transfer_from_chart_dialog),
            ]),
        ]

    @staticmethod
    def _tool_label(key: str, fallback: str) -> str:
        """翻譯字串，查不到就用 fallback。

        `t()` 查不到 key 時是**回傳 key 本身**、不會丟例外，所以 try/except 擋
        不住——選單上就會出現 `action_set_measure_time_signature` 這種字串。
        """
        label = t(key)
        return fallback if label == key else label

    def _add_action(self, menu, label: str, slot, shortcut=None, keys=None,
                    feature: str = '') -> QAction:
        """`feature` 給的話，在做不到那件事的平台上這個項目會變灰並寫出原因
        （例如 AI 轉譜的執行環境只有 Windows 版），而不是按下去才壞掉。"""
        act = QAction(label, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        if feature:
            from .platform_support import feature_available, feature_reason
            if not feature_available(feature):
                act.setEnabled(False)
                act.setToolTip(feature_reason(feature))
                act.setText('%s（%s）' % (label, feature_reason(feature)))
                return act
        self._register_pan_gated(act, slot or '')
        if shortcut is None:
            # 自己沒綁快捷鍵的選單項目（綁了的 Qt 會自己畫在右邊），看看偏好設定
            # 裡有沒有人綁到同一個動作
            self._label_shortcut(act, label, act.toolTip(), keys or slot, 'menu')
        return act

    # ==================================================================
    # 工具列
    # ==================================================================

    @staticmethod
    def _fit_combo(combo, floor: int) -> None:
        """下拉框寬度依實際文字量測，硬寫的數值只當下限。

        原本是 setFixedWidth，但那個寬度是照繁中的字串estimate 的——換成
        英文（"Second audio volume" 要 213px 卻只給 62px）或使用者調大系統
        字級就會被裁掉。改成量測後取 max，語言和 DPI 都能自動適應。
        """
        fm = combo.fontMetrics()
        need = max((fm.horizontalAdvance(combo.itemText(i))
                    for i in range(combo.count())), default=0)
        combo.setMinimumWidth(max(floor, need + COMBO_CHROME_PX))
        combo.setSizeAdjustPolicy(combo.AdjustToContents)

    @staticmethod
    def _fit_label(label, floor: int) -> None:
        """標籤寬度同理：量文字，硬寫值當下限。"""
        need = label.fontMetrics().horizontalAdvance(label.text())
        label.setMinimumWidth(max(floor, need + LABEL_PAD_PX))

    # ── 工具列與作用格的連動 ──────────────────────────────────────────

    def _pane_of(self, tbs: ToolbarSet) -> ChartView:
        """這條工具列負責哪一格。"""
        i = tbs.pane if tbs.pane is not None else self._active_pane
        if not (0 <= i < len(self._panes)):
            i = self._active_pane
        return self._panes[i]

    def _bind(self, tbs: ToolbarSet, slot, forward: bool = True):
        """包一層：按下這條工具列的東西時，先把作用格切到它負責的那一格。

        獨立視窗模式下兩條工具列各有各的格子，這樣按主視窗的按鈕就作用在主
        視窗的格子、按獨立視窗的按鈕就作用在獨立視窗的格子，不用先去點格子
        本身。動作本身仍然是走 `self.view`，所以其他版面（左右/上下/疊層）
        只有一條工具列時行為完全不變。

        `forward=False` 用在 `triggered` 這種「參數沒有意義」的訊號上：包一層
        之後 PyQt 看到的是 *args，會把 checked 旗標一起丟過來，而那些 slot 都
        是不收參數的（原本直接接 bound method 時 PyQt 會自己省略）。
        """
        def run(*args):
            prev = self._tb_action_busy
            self._tb_action_busy = True
            try:
                if tbs.pane is not None and self._panes[tbs.pane].isVisible():
                    if self._active_pane != tbs.pane:
                        self.set_active_pane(tbs.pane, give_focus=False)
                return slot(*args) if forward else slot()
            finally:
                self._tb_action_busy = prev
                if not prev:
                    self._sync_toolbars()
        return run

    # ── 工具列建立 ────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        """主視窗的分頁式工具列：一次只看得到一組功能。

        以前所有按鈕擠在同一條，寬度遠超過視窗，後半段會被塞進 » 溢位選單裡
        找不到。改成 編輯 / 播放 / 小節 / 檢視 四個分頁，音量區塊釘在右上角
        （所有分頁都看得到，因為隨時可能要調音量）。
        """
        tb: QToolBar = self.addToolBar(t('tb_main'))
        tb.setMovable(False)

        # 檔案操作（新增/開啟/儲存）與復原不放工具列，選單裡有，
        # 而且都有標準快捷鍵（Ctrl+N/O/S/Z）。
        self._tb_main = self._build_toolbar_set(
            pane=0 if self._detached_win is not None else None
        )
        tb.addWidget(self._tb_main.root)

    def _build_toolbar_set(self, pane: Optional[int] = None) -> ToolbarSet:
        """建立一整條工具列（主視窗與獨立視窗各一條，狀態互不干擾）。"""
        tbs = ToolbarSet(pane)
        root = QWidget()
        tbs.root = root
        # 先掛進清單，建立過程中的 _refresh_* 就能一併刷到這條（欄位都有 None 保護）
        self._toolbars.append(tbs)

        tabs = QTabWidget(root)
        tabs.setDocumentMode(True)
        tabs.setUsesScrollButtons(False)
        tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        tbs.tabs = tabs

        tabs.addTab(self._build_tab_edit(tbs),    t('tb_tab_edit'))
        tabs.addTab(self._build_tab_play(tbs),    t('tb_tab_play'))
        tabs.addTab(self._build_tab_measure(tbs), t('tb_tab_measure'))
        tabs.addTab(self._build_tab_tools(tbs),   t('tb_tab_tools'))
        tabs.addTab(self._build_tab_view(tbs),    t('tb_tab_view'))

        # 分頁與音量包成同一個 widget 再交給工具列。分兩次 addWidget 的話，
        # 工具列會認為自己塞不下而把音量整塊收進 » 溢位選單裡。
        # 音量常駐在分頁右側：它不屬於任何一個分頁，調音量時也不該被迫換頁。
        # 也不用 setCornerWidget——那會把三條滑桿硬塞進分頁標籤那一列的高度裡。
        hbox = QHBoxLayout(root)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(tabs, 1)
        hbox.addWidget(self._make_vol_block(tbs), 0, Qt.AlignVCenter)

        self._refresh_split_ui()
        self._refresh_vol_rows()
        self._sync_toolbars()
        return tbs

    def _drop_toolbar_set(self, tbs: Optional[ToolbarSet]) -> None:
        """把一條工具列從清單移除（widget 由它的容器負責刪掉）。"""
        if tbs is None:
            return
        try:
            self._toolbars.remove(tbs)
        except ValueError:
            pass

    # ── 獨立視窗的工具列 ──────────────────────────────────────────────

    def _attach_detached_toolbar(self) -> None:
        """幫獨立視窗裝上它自己的選單列與工具列（固定作用在 pane 1）。"""
        win = self._detached_win
        if win is None:
            return
        self._attach_detached_menubar()
        self._drop_toolbar_set(self._tb_detached)
        self._tb_detached = self._build_toolbar_set(pane=1)
        win.set_toolbar(self._tb_detached.root)
        if self._tb_main is not None:
            self._tb_main.pane = 0      # 主視窗那條改成固定作用在 pane 0
        self._sync_toolbars()

    def _attach_detached_menubar(self) -> None:
        """獨立視窗頂端也放一條選單列，版面才和主視窗一致。

        直接沿用主視窗那幾個 QMenu：同一份譜、同一組動作，複製一份只會多出
        一組重複的快捷鍵。QMenu 本來就可以掛在多個選單列上。
        """
        win = self._detached_win
        if win is None:
            return
        mb = PaneMenuBar(lambda: self.set_active_pane(1, give_focus=False), win)
        mb.setNativeMenuBar(False)      # 這是普通 widget，不能被系統選單列接管
        for act in self.menuBar().actions():
            menu = act.menu()
            if menu is not None:
                mb.addMenu(menu)
        win.set_menubar(mb)

    def _detach_toolbar_cleanup(self) -> None:
        """收掉獨立視窗時，連它的工具列一起解除登記。"""
        self._drop_toolbar_set(self._tb_detached)
        self._tb_detached = None
        if self._tb_main is not None:
            self._tb_main.pane = None   # 回到「工具列作用在作用格」

    # ── 分頁組裝小工具 ────────────────────────────────────────────────

    @staticmethod
    def _tab_page(widgets) -> QWidget:
        """把一串 widget 排成一列，做成分頁內容。None = 分隔線。

        外面再包一層橫向捲動區：不這樣做的話，最寬的那一頁（編輯頁約 880px）
        會變成整個視窗的最小寬度——獨立視窗那條工具列一裝上去，那個視窗就再
        也縮不小了。捲動軸只在真的塞不下時才出現，出現時才把該頁加高，平常
        的工具列高度和以前一樣。
        """
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(6, 2, 6, 2)
        hbox.setSpacing(4)
        for w in widgets:
            if w is None:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setFrameShadow(QFrame.Sunken)
                hbox.addWidget(sep)
            else:
                hbox.addWidget(w)
        hbox.addStretch(1)

        area = QScrollArea()
        area.setWidget(row)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 寬度忽略內容（才縮得下去）、高度固定成剛好一列
        area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        base_h = row.sizeHint().height()
        area.setFixedHeight(base_h)

        bar = area.horizontalScrollBar()

        def _fit_height(lo: int, hi: int) -> None:
            # 捲動軸是覆蓋在 viewport 下方的，出現時要補它的高度才不會蓋到按鈕
            area.setFixedHeight(base_h + (bar.sizeHint().height() if hi > lo else 0))
        bar.rangeChanged.connect(_fit_height)
        return area

    def _tab_button(self, action: QAction) -> QToolButton:
        """把 QAction 包成分頁上的按鈕（保留 checkable / tooltip / 快捷鍵）。"""
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setDefaultAction(action)
        # 不用 autoRaise：分頁上的按鈕若只在滑鼠移上去才看得到邊框，就看不出
        # 哪裡可以按，也看不出 checkable 的按鈕現在是不是開著。
        btn.setStyleSheet(
            'QToolButton { border: 1px solid palette(mid); border-radius: 3px;'
            ' padding: 3px 8px; }'
            'QToolButton:checked { background: palette(highlight);'
            ' color: palette(highlighted-text); font-weight: bold; }'
            'QToolButton:disabled { color: gray; }'
        )
        return btn

    def _tab_action(self, tbs: ToolbarSet, label: str, slot, tip: str = '',
                    checkable: bool = False, keys=None) -> QAction:
        """建立分頁上的動作。動作掛在這條工具列底下，工具列被刪就一起走。

        `keys`：這顆按鈕對應哪個快捷鍵動作（函式名）。不給的話拿 slot 的函式名；
        slot 是 lambda 時要自己給。
        """
        act = QAction(label, tbs.root, checkable=checkable)
        if tip:
            act.setToolTip(tip)
        self._label_shortcut(act, label, tip, keys or slot, 'button')
        # checkable 走 toggled（要 checked 旗標）；其餘走 triggered（不收參數）
        bound = self._bind(tbs, slot, forward=checkable)
        if checkable:
            act.toggled.connect(bound)
        else:
            act.triggered.connect(bound)
        return act

    # ── 分頁：編輯 ────────────────────────────────────────────────────

    def _build_tab_edit(self, tbs: ToolbarSet) -> QWidget:
        # 分割搬到「檢視」分頁了——那是版面的事。這個位置改成常駐顯示偵測到的
        # 調性：音階輔助要用它，放音符時想知道現在是什麼調也看得到。
        tbs.pattern_key_label = QLabel('調性：－', tbs.root)
        tbs.pattern_key_label.setStyleSheet('font-size: 11px; margin: 0 6px;')

        # 放置音符模式（開關 + 參數）——開關是「這條工具列那一格」的狀態
        tbs.note_input_act = self._tab_action(
            tbs, t('tb_note_input'), self._on_note_input_toggle,
            tip=t('tb_note_input_tip'), checkable=True,
        )

        def make_combo(items, floor: int, idx: int, tip: str, slot) -> QComboBox:
            """下拉框的初始值一律靜默設定：第二條工具列建立時不能反過來改到
            使用者已經選好的參數（setCurrentIndex 會觸發 currentIndexChanged）。"""
            combo = QComboBox(tbs.root)
            for label in items:
                combo.addItem(label)
            self._fit_combo(combo, floor)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            combo.currentIndexChanged.connect(self._bind(tbs, slot))
            combo.setToolTip(tip)
            return combo

        tbs.dur_combo = make_combo(
            [name for name, _ in self._note_dur_items], 88, self._ni_dur_idx,
            '音符時值（放置音符模式下生效）。最後一項「自訂…」可以輸入任意 N 分音符',
            self._on_dur_combo_changed)
        tbs.hand_combo = make_combo(
            [t('tb_note_hand_r'), t('tb_note_hand_l')], 56, self._ni_hand_idx,
            '放置音符的預設手', self._on_hand_combo_changed)
        tbs.width_combo = make_combo(
            [f'寬度 {w}' for w in range(1, 7)], 72, self._ni_width_idx,
            '放置音符的預設寬度（格數）', self._on_width_combo_changed)
        tbs.type_combo = make_combo(
            ('Tap  (T)', 'Soft', 'Long  (H)', 'Staccato  (K)',
             'Slide  (滑)', 'Trill  (顫音)'), 120, self._ni_type_idx,
            '放置音符的預設類型', self._on_type_combo_changed)
        # 下拉索引 → note_type（trill 不是連號，需明確對照）
        self._type_combo_values = [0, 1, 2, 3, 4, 64]
        # 選單上直接看得到每種音符長什麼樣（遊戲素材，顏色跟著放置的手）
        from PyQt5.QtCore import QSize
        from .note_icons import (VALUE_ICON_H, VALUE_ICON_W, hand_icon,
                                 note_value_icon)
        # 工具列上縮小一點（原圖 42x30），不然整條工具列會被撐高
        icon_w, icon_h = 28, 20
        for combo in (tbs.type_combo, tbs.hand_combo):
            combo.setIconSize(QSize(icon_w, icon_h))
        # 時值選單：每一項前面畫出音符符號（三連音左上角標 3）
        value_h = icon_h
        value_w = int(round(VALUE_ICON_W * value_h / float(VALUE_ICON_H)))
        tbs.dur_combo.setIconSize(QSize(value_w, value_h))
        self._fill_dur_combo(tbs.dur_combo)
        for i in range(tbs.hand_combo.count()):
            tbs.hand_combo.setItemIcon(i, hand_icon(i))
        self._refresh_type_icons(tbs)
        # 量寬度時要把圖示算進去，不然文字會被擠掉
        for combo, floor in ((tbs.type_combo, 120), (tbs.hand_combo, 56)):
            self._fit_combo(combo, floor + icon_w + 6)

        # 下拉框本身的文字是選項，快捷鍵寫進提示
        self._label_shortcut(tbs.dur_combo, '', tbs.dur_combo.toolTip(),
                             ('_shortcut_dur_shorter', '_shortcut_dur_longer'), 'tooltip')
        self._label_shortcut(tbs.hand_combo, '', tbs.hand_combo.toolTip(),
                             '_shortcut_toggle_hand', 'tooltip')

        # 格線配置：不屬於放置參數（沒開放置模式也能看格線），所以不放進變灰的那組
        tbs.grid_btn = QToolButton(tbs.root)
        tbs.grid_btn.setText('格線 ▾')
        tbs.grid_btn.setPopupMode(QToolButton.InstantPopup)
        tbs.grid_btn.setToolTip('格線配置：跟著放置時值，或自己搭幾層（例如 4 分＋16 分），'
                                '也可以加自訂 N 分音符')
        grid_menu = QMenu(tbs.grid_btn)
        grid_menu.aboutToShow.connect(lambda m=grid_menu: self._fill_grid_menu(m))
        tbs.grid_btn.setMenu(grid_menu)

        # 放置模式沒開時參數變灰（不隱藏，免得每次切換整條工具列跳位）
        tbs.note_input_group = [
            tbs.dur_combo, tbs.hand_combo, tbs.width_combo, tbs.type_combo,
        ]

        # 同步初始值到 view
        self._on_dur_combo_changed(self._ni_dur_idx)
        self._on_hand_combo_changed(self._ni_hand_idx)
        self._on_width_combo_changed(self._ni_width_idx)
        self._on_type_combo_changed(self._ni_type_idx)

        # ── 音階輔助（開關 + 參數）────────────────────────────────
        # 和放置模式同一種形狀：沒開就整組收起來，開了才展開。
        tbs.pattern_act = self._tab_action(
            tbs, '♪ 音階輔助', self._on_pattern_toggle,
            tip='開啟後在譜面按住往上拖：拖多遠就放幾個音，放開才寫進譜面。\n'
                '音高照偵測到的調性走。',
            checkable=True)

        tbs.pattern_kind_combo = make_combo(
            [label for label, _v in self._pattern_kinds], 152,
            self._pat_kind_idx,
            '音型：前四項照譜面的調性走，後面是指定音階（半音階、全音階、'
            '五聲、藍調、各調式…）。\n'
            '半音階/全音階/減音階是對稱音階，起點就是你下筆的那個音。',
            self._on_pattern_kind_changed)
        tbs.pattern_dir_combo = make_combo(
            [label for label, _v in self._pattern_dirs], 92,
            self._pat_dir_idx, '方向', self._on_pattern_dir_changed)
        tbs.pattern_step_combo = make_combo(
            [label for label, _v in self._pattern_steps], 92,
            self._pat_step_idx, '每音間隔', self._on_pattern_step_changed)
        tbs.pattern_step_combo.setIconSize(tbs.dur_combo.iconSize())
        for i, (_label, beats) in enumerate(self._pattern_steps):
            tbs.pattern_step_combo.setItemIcon(i, note_value_icon(beats))
        self._fit_combo(tbs.pattern_step_combo, 92 + tbs.dur_combo.iconSize().width() + 6)
        tbs.pattern_key_combo = make_combo(
            [label for label, _v in self._pattern_keys], 96,
            self._pat_key_idx, '調性（自動＝從譜面音高偵測）',
            self._on_pattern_key_changed)
        tbs.pattern_group = [
            tbs.pattern_kind_combo, tbs.pattern_dir_combo,
            tbs.pattern_step_combo, tbs.pattern_key_combo,
        ]
        self._on_pattern_kind_changed(self._pat_kind_idx)
        self._on_pattern_dir_changed(self._pat_dir_idx)
        self._on_pattern_step_changed(self._pat_step_idx)
        self._on_pattern_key_changed(self._pat_key_idx)

        auto_sort = self._tab_action(
            tbs, t('tb_auto_sort'), lambda: self.view.start_alloc_section(),
            keys='start_alloc_section')

        # ── 手別篩選：只編一隻手，另一手變成參考影子 ──────────────
        tbs.hand_filter_combo = make_combo(
            [label for label, _v in self._hand_filters], 96, self._hand_filter_idx,
            '只編這一隻手。被濾掉的那一手畫成半透明的幽靈音符：看得到、'
            '點不到、全選也不會選到。\n'
            '（幽靈音符可在「檢視」分頁關掉。）',
            self._on_hand_filter_changed)

        return self._tab_page([
            tbs.pattern_key_label,
            None,
            self._tab_button(tbs.note_input_act),
            tbs.dur_combo, tbs.hand_combo, tbs.width_combo, tbs.type_combo,
            tbs.grid_btn,
            None,
            self._tab_button(tbs.pattern_act),
            tbs.pattern_kind_combo, tbs.pattern_dir_combo,
            tbs.pattern_step_combo, tbs.pattern_key_combo,
            None,
            tbs.hand_filter_combo,
            self._tab_button(auto_sort),
        ])

    # ── 分頁：播放 ────────────────────────────────────────────────────

    def _build_tab_play(self, tbs: ToolbarSet) -> QWidget:
        act_play_full = self._tab_action(tbs, t('tb_play_full'), self.play_full)
        act_play_win  = self._tab_action(tbs, t('tb_play'), self.play_window)
        act_stop      = self._tab_action(tbs, t('tb_stop'), self.stop_audio)
        tbs.pause_act = self._tab_action(tbs, t('tb_pause'), self._toggle_pause_resume)

        # MIDI 音源開關：按鈕在這裡，它控制的音量條在右上角音量區塊那一列。
        # 目前狀態直接靜默套用；重建工具列不該把使用者關掉的音源又打開。
        tbs.hit_act = self._tab_action(
            tbs, t('tb_hit_sound'), self._on_hit_toggle,
            tip=t('tb_hit_sound_tip'), checkable=True)
        tbs.hit_act.blockSignals(True)
        tbs.hit_act.setChecked(bool(self._hit_sound_persistent))
        tbs.hit_act.blockSignals(False)

        # 「開頭空白」取代舊的播放偏移：直接加／減音訊開頭的靜音（見 audio_lead_in）
        offset_act = self._tab_action(
            tbs, t('tb_offset'), self._show_lead_in_dialog, tip=t('tb_offset_tip'))

        return self._tab_page([
            self._tab_button(act_play_full),
            self._tab_button(act_play_win),
            self._tab_button(tbs.pause_act),
            self._tab_button(act_stop),
            None,
            self._tab_button(tbs.hit_act),
            None,
            self._tab_button(offset_act),
        ])

    # ── 分頁：小節 ────────────────────────────────────────────────────

    def _build_tab_measure(self, tbs: ToolbarSet) -> QWidget:
        # 和選單列的「小節」共用 _measure_entries()，兩邊不會少一項。
        # 前兩項（新增/刪除）和後三項（BPM/多小節/拍號）之間放分隔線。
        entries = self._measure_entries()
        widgets = []
        for i, (label, slot, tip) in enumerate(entries):
            if i in (2, 5):
                widgets.append(None)
            widgets.append(self._tab_button(
                self._tab_action(tbs, label, slot, tip=tip)))
        return self._tab_page(widgets)

    # ── 分頁：工具 ────────────────────────────────────────────────────

    def _menu_button(self, tbs: ToolbarSet, label: str, entries) -> QToolButton:
        """一顆按鈕點開一組動作（工具分類用）。"""
        btn = QToolButton()
        btn.setText(label + ' ▾')
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setStyleSheet(
            'QToolButton { border: 1px solid palette(mid); border-radius: 3px;'
            ' padding: 3px 8px; }'
            'QToolButton::menu-indicator { image: none; }'
        )
        menu = QMenu(btn)
        for item_label, slot in entries:
            act = menu.addAction(item_label)
            act.triggered.connect(self._bind(tbs, slot, forward=False))
            self._register_pan_gated(act, slot)
            self._label_shortcut(act, item_label, '', slot, 'menu')
        btn.setMenu(menu)
        # QToolButton 不持有 menu 的所有權，menu 被 GC 掉按鈕就變成點不開
        tbs.tool_menus.append(menu)
        return btn

    def _build_tab_tools(self, tbs: ToolbarSet) -> QWidget:
        # 智慧寫譜是最常用的一顆，單獨放外面，其餘照分類收進下拉
        smart = self._tab_action(tbs, t('action_smart_midi_chart'),
                                 self.smart_midi_chart_dialog)
        widgets = [self._tab_button(smart), None]
        for title, entries in self._tool_groups():
            widgets.append(self._menu_button(tbs, title, entries))
        return self._tab_page(widgets)

    # ── 分頁：檢視 ────────────────────────────────────────────────────

    def _build_tab_view(self, tbs: ToolbarSet) -> QWidget:
        split_block = self._build_split_block(tbs)
        act_zoom_out = self._tab_action(tbs, t('tb_zoom_out'), lambda: self.view.zoom(2.0),
                                        keys='zoom_out')
        act_zoom_in  = self._tab_action(tbs, t('tb_zoom_in'),  lambda: self.view.zoom(0.5),
                                        keys='zoom_in')

        tbs.preview_act = self._tab_action(
            tbs, t('tb_preview'),
            lambda checked: self.view.toggle_preview_mode(checked),
            tip=t('tb_preview_tip'), checkable=True, keys='toggle_preview')

        tbs.view_mode_act = self._tab_action(
            tbs, t('tb_time_uniform_measure'), self._cycle_view_mode,
            tip=t('tb_time_uniform_tip'))
        self._refresh_view_mode_action()

        return self._tab_page([
            split_block,
            None,
            self._tab_button(act_zoom_out),
            self._tab_button(act_zoom_in),
            None,
            self._tab_button(tbs.preview_act),
            self._tab_button(tbs.view_mode_act),
        ])

    def _build_split_block(self, tbs: ToolbarSet) -> QWidget:
        """分割模式：開關 + 版面循環 + 紅藍作用格選擇，下面一行放提示文字。

        用自己的 QToolButton（而不是 tb.addAction）才能把按鈕排成一列、
        底下再掛一行提示，例如疊層模式的「⇧ SHIFT 切換塗層」。
        """
        # 關掉分割時留下的是「作用中的格子」，所以這兩顆也要先切作用格：
        # 按主視窗的分割鈕就留下主視窗那格，按獨立視窗的就留下它自己那格。
        tbs.split_act = self._tab_action(
            tbs, t('tb_split'), self._on_split_toggled,
            tip=t('tb_split_tip'), checkable=True)
        tbs.split_dir_act = self._tab_action(
            tbs, t('tb_split_dir_h'), self._cycle_split_layout,
            tip=t('tb_split_dir_tip'))

        # 紅 = 左/上，藍 = 右/下；勾選的那一格才是工具列的作用對象
        tbs.pane_acts = []
        for role, key in ((0, 'tb_pane_left'), (1, 'tb_pane_right')):
            act = QAction(t(key), tbs.root, checkable=True)
            act.setToolTip(t('tb_pane_tip'))
            act.triggered.connect(lambda _c=False, r=role: self.set_active_pane(r))
            tbs.pane_acts.append(act)

        block = QWidget()
        vbox = QVBoxLayout(block)
        vbox.setContentsMargins(2, 1, 2, 1)
        vbox.setSpacing(0)

        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(2)
        for act in (tbs.split_act, tbs.split_dir_act, *tbs.pane_acts):
            btn = QToolButton(row)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.setDefaultAction(act)
            if act in tbs.pane_acts:
                r, g, b, _a = PANE_COLORS[tbs.pane_acts.index(act)].getRgb()
                btn.setStyleSheet(
                    'QToolButton { border: 1px solid rgba(%d,%d,%d,140);'
                    ' border-radius: 3px; padding: 2px 6px; }'
                    'QToolButton:checked { background: rgb(%d,%d,%d); color: white;'
                    ' font-weight: bold; }'
                    'QToolButton:disabled { color: gray; border-color: rgba(%d,%d,%d,60); }'
                    # 獨立視窗模式下這排是「不能點的指示燈」，勾起來的那顆
                    # 仍要看得出顏色，不能被 :disabled 的灰蓋掉
                    'QToolButton:checked:disabled { background: rgb(%d,%d,%d);'
                    ' color: white; font-weight: bold; border-color: rgb(%d,%d,%d); }'
                    % (r, g, b, r, g, b, r, g, b, r, g, b, r, g, b)
                )
            hbox.addWidget(btn)
        hbox.addStretch(1)
        vbox.addWidget(row)

        tbs.split_hint = QLabel(t('tb_split_shift_hint'))
        tbs.split_hint.setStyleSheet(
            'font-size: 10px; color: palette(mid); margin-left: 4px;'
        )
        tbs.split_hint.setVisible(False)
        vbox.addWidget(tbs.split_hint)

        self._refresh_split_ui()
        return block

    def _make_vol_block(self, tbs: ToolbarSet) -> QWidget:
        """三條音量：每列都是 標籤 + 滑桿 + 喇叭開關，欄寬一致所以滑桿對齊。"""
        block = QWidget()
        vbox = QVBoxLayout(block)
        vbox.setContentsMargins(4, 2, 8, 2)
        vbox.setSpacing(2)
        try:
            lbl2 = t('tb_music2_vol')
        except Exception:
            lbl2 = '音源2 音量'
        vbox.addWidget(self._make_vol_row(
            tbs, t('tb_music_vol'), self._on_music_vol_changed, 'music'))
        vbox.addWidget(self._make_vol_row(
            tbs, lbl2, self._on_music2_vol_changed, 'music2'))
        # 鋼琴那一列的開關就是 MIDI 音源開關（本來就是它在控制這條音量）
        vbox.addWidget(self._make_vol_row(
            tbs, t('tb_hit_vol'), self._on_hit_vol_changed, 'hit',
            action=tbs.hit_act))
        self._refresh_vol_rows()
        return block

    def _make_vol_row(
        self,
        tbs: ToolbarSet,
        label_text: str,
        callback,
        key: str,
        action: Optional[QAction] = None,
    ) -> QWidget:
        """單行：靠左標籤 + 滑桿 + 喇叭開關（預設開啟）。

        音量是全域的，所以起始值取目前的實際值（不是固定 100）——獨立視窗
        那條工具列建起來時才不會把音量拉回滿格。
        """
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)

        cur = int(self._vol_values.get(key, 100))

        lbl = QLabel(label_text)
        lbl.setStyleSheet('font-size: 11px;')
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._fit_label(lbl, 62)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.blockSignals(True)
        slider.setValue(cur)
        slider.blockSignals(False)
        slider.setMinimumWidth(90)
        slider.setToolTip(f'{label_text}: {cur}%')

        def _on_change(val: int) -> None:
            self._vol_values[key] = int(val)
            self._mirror_vol_sliders(key, int(val))
            callback(val)
        slider.valueChanged.connect(_on_change)

        mirror = None
        if action is not None:
            # 共用的開關（MIDI 鋼琴）另外給喇叭一個自己的 QAction，兩邊互相跟隨。
            # 直接 setDefaultAction 共用的話，兩顆按鈕只能顯示同一段文字：不是
            # 分頁上的「MIDI 鋼琴」變成 🔊，就是這顆 26px 的喇叭被擠成「MIDI」。
            mirror = action
            action = QAction(tbs.root, checkable=True)
            action.blockSignals(True)
            action.setChecked(mirror.isChecked())
            action.blockSignals(False)
            action.toggled.connect(
                lambda on, src=mirror: src.isChecked() != on and src.setChecked(on))
        else:
            action = QAction(tbs.root, checkable=True)
            action.blockSignals(True)
            action.setChecked(bool(self._vol_enabled.get(key, True)))
            action.blockSignals(False)
            action.toggled.connect(
                lambda on, k=key: self._on_vol_enable_toggled(k, on)
            )
        button = QToolButton()
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setDefaultAction(action)
        button.setFixedWidth(26)
        button.setStyleSheet(
            'QToolButton { font-size: 12px; border: 1px solid palette(mid);'
            ' border-radius: 3px; padding: 0px; }'
            'QToolButton:checked { border-color: palette(highlight); }'
        )

        hbox.addWidget(lbl)
        hbox.addWidget(slider)
        hbox.addWidget(button)
        tbs.vol[key] = {
            'label': lbl, 'slider': slider, 'action': action, 'text': label_text,
            'mirror': mirror,
        }
        return row

    def _mirror_vol_sliders(self, key: str, val: int) -> None:
        """一條工具列拉音量，另一條的同名滑桿也跟著走。"""
        for tbs in self._toolbars:
            ent = tbs.vol.get(key)
            if ent is None:
                continue
            slider = ent['slider']
            if slider.value() == val:
                continue
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
            slider.setToolTip('%s: %d%%' % (ent['text'], val))

    def _on_vol_enable_toggled(self, key: str, on: bool) -> None:
        """喇叭開關：關閉時該音源靜音，滑桿一併變灰。"""
        on = bool(on)
        self._vol_enabled[key] = on
        # 另一條工具列上的同一顆開關也要跟著切
        for tbs in self._toolbars:
            ent = tbs.vol.get(key)
            if ent is None or ent['action'].isChecked() == on:
                continue
            ent['action'].blockSignals(True)
            ent['action'].setChecked(on)
            ent['action'].blockSignals(False)

        val = int(self._vol_values.get(key, 100))
        if key == 'music':
            self.audio.set_volume((val / 100.0) if on else 0.0)
        elif key == 'music2':
            try:
                if hasattr(self.audio, 'set_volume2'):
                    self.audio.set_volume2((val / 100.0) if on else 0.0)
            except Exception:
                pass
        self._refresh_vol_rows()

    def _refresh_vol_rows(self) -> None:
        """喇叭圖示與滑桿啟用狀態，統一依各自的開關更新。"""
        # 沒載入音樂檔時，那兩條音量整列灰掉——它們控制的東西不存在。
        # 鋼琴（MIDI 音源）那條不受影響，沒有 WAV 也照樣能播。
        has_audio = bool(self.audio.is_loaded())
        has_second = bool(getattr(self.audio, 'audio2_path', None))
        available = {'music': has_audio, 'music2': has_audio and has_second, 'hit': True}
        for tbs in self._toolbars:
            for key in ('music', 'music2', 'hit'):
                ent = tbs.vol.get(key)
                if ent is None:
                    continue
                action = ent['action']
                mirror = ent.get('mirror')
                if mirror is not None and action.isChecked() != mirror.isChecked():
                    action.blockSignals(True)
                    action.setChecked(mirror.isChecked())
                    action.blockSignals(False)
                usable = available[key]
                on = bool(action.isChecked()) and usable
                action.setText('🔊' if on else '🔇')
                action.setEnabled(usable)
                action.setToolTip(
                    (t('tb_vol_on') if on else t('tb_vol_off')) if usable
                    else t('tb_vol_no_audio')
                )
                ent['slider'].setEnabled(on)
                ent['label'].setEnabled(on)

    # ==================================================================
    # 狀態列
    # ==================================================================

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        self.setStatusBar(sb)

        self._lbl_status = QLabel(t('status_open_file'))
        self._lbl_sel    = QLabel(t('status_sel', 0))
        self._lbl_audio  = QLabel(t('status_audio_none'))

        # 說明文字改為 tooltip，避免 QLabel 強制拉寬視窗最小寬度
        sb.setToolTip(t('status_hint'))

        # 防止 permanent widget 撐開視窗最小寬度
        for lbl in (self._lbl_status, self._lbl_sel, self._lbl_audio):
            lbl.setMinimumWidth(0)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        sb.addWidget(self._lbl_status, 1)
        sb.addPermanentWidget(self._lbl_audio)
        sb.addPermanentWidget(self._lbl_sel)

    # ==================================================================
    # 信號處理
    # ==================================================================

    def _on_selection_changed(self, count: int) -> None:
        self._lbl_sel.setText(t('status_sel', count))

    def _on_status_changed(self, msg: str) -> None:
        # 分割時只顯示作用格的狀態，避免另一格的訊息蓋掉
        src = self.sender()
        if isinstance(src, ChartView) and src is not self.view:
            return
        self._lbl_status.setText(msg)

    def _on_note_edited(self) -> None:
        self._refresh_title()
        # 音高變了調性就可能變（掃一次音級分布，成本可忽略）
        self._refresh_pattern_key_label()
        # Keep hit-time cache consistent after any model edit (including undo).
        self._rebuild_hit_times()
        # 另一格看的是同一份譜：BPM / beat_data 可能變了，重建它的 mapper
        src = self.sender()
        for v in self._visible_panes():
            if v is not src:
                v.rebuild_mapper()

    def _refresh_title(self) -> None:
        m = self.view.model
        fname = os.path.basename(m.current_file) if m.current_file else t('wnd_no_file')
        dirty = ' *' if m.dirty else ''
        mode = '　[XML・PAN 相容模式]' if getattr(m, 'pan_xml', False) else ''
        self.setWindowTitle(f"{t('wnd_title')}  —  {fname}{dirty}{mode}")

    def _set_pitch_numbering(self, use_midi: bool, save: bool = True) -> None:
        """切換音高數字要顯示遊戲編號還是 MIDI 編號（只影響標籤，不動內部值）。"""
        for pane in self._panes:
            pane.show_midi_pitch = bool(use_midi)
            pane.update()
        if save:
            settings.set('show_midi_pitch', bool(use_midi))

    def _set_velocity_display(self, key: str, enabled: bool) -> None:
        """力度呈現開關（亮度 / 數字）。只影響畫面，不動譜面資料。"""
        settings.set(key, bool(enabled))
        for pane in self._panes:
            pane.update()

    def _toggle_statusbar(self, checked: bool) -> None:
        settings.set('show_statusbar', bool(checked))
        self.statusBar().setVisible(bool(checked))

    def _toggle_scroll_invert(self, checked: bool) -> None:
        for v in self._panes:
            v.scroll_invert = checked
        self.view._emit_status()

    # ── 放置音符模式 ──────────────────────────────────────────────────

    def _on_new_chart_requested(self) -> None:
        """放置模式下還沒有譜面就點擊 → 直接開「新增譜面」。"""
        if getattr(self, '_new_chart_dialog_open', False):
            return
        self._new_chart_dialog_open = True
        try:
            self.statusBar().showMessage(t('status_need_chart'), 4000)
            self.new_chart_dialog()
        finally:
            self._new_chart_dialog_open = False

    def _on_note_input_toggle(self, checked: bool) -> None:
        """工具列「放置模式」按鈕切換（只作用在目前的格子）。"""
        self.view.set_note_input_mode(checked)
        self._refresh_toolbar_groups()

    def _on_note_input_mode_changed(self, enabled: bool) -> None:
        """ChartView 主動改變 note_input_mode 時（如按 Esc）同步工具列按鈕狀態。"""
        src = self.sender()
        if isinstance(src, ChartView) and src not in self._panes:
            return
        self._sync_place_grid_peers()
        self._sync_toolbars()

    def _sync_place_grid_peers(self) -> None:
        """放置格線要在兩格都畫出來。

        放置模式本身是單格的（工具列固定操作某一格），但格線是給眼睛看的
        參考線——另一格顯示同一段音樂時也該看得到落點。這裡只把「有人在放置」
        傳過去，不打開另一格的放置模式，免得點下去誤放音符。
        """
        any_placing = any(getattr(v, '_note_input_mode', False)
                          for v in self._panes)
        for v in self._panes:
            v.set_peer_placement(
                any_placing and not getattr(v, '_note_input_mode', False))

    def _set_note_input_mode(self, on: bool) -> None:
        """程式內部（載入譜面等）切換放置模式，不經過工具列按鈕的 toggled。"""
        for v in self._panes:
            v.set_note_input_mode(bool(on))
        self._sync_place_grid_peers()
        self._sync_toolbars()

    def _apply_note_input_settings(self, v: ChartView) -> None:
        """把工具列的放置參數（時值/手/寬度/類型）套到指定格子。"""
        idx = self._ni_dur_idx
        if 0 <= idx < len(self._note_dur_items):
            v.set_note_duration(self._note_dur_items[idx][1])
        v.set_note_input_hand(max(0, self._ni_hand_idx))
        if self._ni_width_idx >= 0:
            v.set_note_input_width(self._ni_width_idx + 1)
        values = getattr(self, '_type_combo_values', None)
        ti = self._ni_type_idx
        if values and 0 <= ti < len(values):
            v.set_note_input_note_type(values[ti])
        elif ti >= 0:
            v.set_note_input_note_type(ti)

    def _mirror_combos(self, name: str, idx: int) -> None:
        """放置參數是兩條工具列共用的，一邊改了另一邊要跟著顯示同一個值。"""
        for tbs in self._toolbars:
            combo = getattr(tbs, name, None)
            if combo is None or combo.currentIndex() == idx:
                continue
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    #: 自訂 N 分音符最多記幾個（最近用的留著）
    _MAX_CUSTOM_DIVISIONS = 8
    _CUSTOM_DUR_LABEL = '自訂…'

    def _insert_custom_duration(self, division) -> Optional[int]:
        """把「N 分音符」加進時值清單（照長短排好），回傳它的位置。已經有就回傳那一項。"""
        try:
            n = int(division)
        except (TypeError, ValueError):
            return None
        if not 1 <= n <= 256:
            return None
        beats = 4.0 / n
        for i, (_name, value) in enumerate(self._note_dur_items):
            if abs(value - beats) < 1e-9:
                return i
        name = '%d分音符' % n
        pos = next((i for i, (_nm, value) in enumerate(self._note_dur_items) if value < beats),
                   len(self._note_dur_items))
        self._note_dur_items.insert(pos, (name, beats))
        power = 1
        while power * 2 <= n:
            power *= 2
        base = {1: '全音符', 2: '二分音符', 4: '四分音符', 8: '八分音符'}.get(power, '%d分音符' % power)
        odd = n
        while odd % 2 == 0:
            odd //= 2
        if odd == 1:
            tip = '自訂：一個全音符分成 %d 份' % n
        else:
            space = 1
            while space * 2 <= odd:
                space *= 2
            tip = '自訂：一個全音符分成 %d 份（%s的 %d 連音：%d 顆佔 %d 顆的長度）' % (
                n, base, odd, odd, space)
        self._note_dur_tips[name] = tip
        return pos

    def _fill_dur_combo(self, combo) -> None:
        """照 `_note_dur_items` 重建時值下拉：名稱、音符圖示、說明，最後接「自訂…」。"""
        from .note_icons import note_value_icon
        combo.blockSignals(True)
        try:
            combo.clear()
            for name, beats in self._note_dur_items:
                combo.addItem(note_value_icon(beats), name)
                tip = self._note_dur_tips.get(name)
                if tip:
                    combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
            combo.addItem(self._CUSTOM_DUR_LABEL)
            combo.setItemData(combo.count() - 1,
                              '輸入一個數字 N：一個全音符分成 N 份（例如 20 = 五連 16 分音符）',
                              Qt.ToolTipRole)
            combo.setCurrentIndex(max(0, min(self._ni_dur_idx, len(self._note_dur_items) - 1)))
        finally:
            combo.blockSignals(False)
        self._fit_combo(combo, 88 + combo.iconSize().width() + 6)

    def _ask_custom_duration(self) -> None:
        """選了「自訂…」：問 N，加進清單並選它；取消就回到原本那一項。"""
        n, ok = QInputDialog.getInt(
            self, '自訂音符時值',
            '一個全音符分成幾份？\n\n'
            '例：5 = 五連四分音符、20 = 五連 16 分音符、96 = 64 分三連音',
            16, 1, 256, 1)
        if not ok:
            for tbs in self._toolbars:
                combo = getattr(tbs, 'dur_combo', None)
                if combo is not None:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(self._ni_dur_idx)
                    combo.blockSignals(False)
            return
        self._select_custom_duration(n)

    def _select_custom_duration(self, n: int) -> None:
        current_beats = self._note_dur_items[self._ni_dur_idx][1]
        pos = self._insert_custom_duration(n)
        if pos is None:
            return
        beats = self._note_dur_items[pos][1]
        # 插入一項之後，原本選的那一項位置可能往後移了
        self._ni_dur_idx = next(i for i, (_nm, v) in enumerate(self._note_dur_items)
                                if abs(v - current_beats) < 1e-9)
        standard = {4.0 / d for d in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64)}
        if not any(abs(beats - s) < 1e-9 for s in standard):
            saved = [int(x) for x in settings.get('custom_note_divisions', []) or []
                     if int(x) != int(n)]
            saved = (saved + [int(n)])[-self._MAX_CUSTOM_DIVISIONS:]
            settings.set('custom_note_divisions', saved)
        for tbs in self._toolbars:
            combo = getattr(tbs, 'dur_combo', None)
            if combo is not None:
                self._fill_dur_combo(combo)
        self._on_dur_combo_changed(pos)
        self.statusBar().showMessage('放置時值：%s' % self._note_dur_items[pos][0], 2000)

    # ── 格線配置 ─────────────────────────────────────────────────────

    #: 格線選單列出的 N 分音符（和放置時值同一套），自訂的另外接在後面
    _GRID_STANDARD = (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64)
    _GRID_NAMES = {1: '全音符', 2: '二分音符', 4: '四分音符', 6: '6分音符（四分三連）',
                   8: '八分音符', 12: '12分音符（八分三連）', 16: '16分音符',
                   24: '24分音符（16分三連）', 32: '32分音符', 48: '48分音符', 64: '64分音符'}
    _GRID_MODES = (('placement', '只在放置模式時顯示'), ('always', '一直顯示'),
                   ('never', '不顯示'))

    def _grid_divisions(self) -> List[int]:
        out = []
        for n in settings.get('grid_divisions', []) or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 256 and n not in out:
                out.append(n)
        return out

    def _fill_grid_menu(self, menu: QMenu) -> None:
        """每次打開才重建：勾選狀態、自訂的 N 分音符都是當下的設定。"""
        from .note_icons import note_value_icon
        menu.clear()
        follow = menu.addAction('跟著放置時值')
        follow.setCheckable(True)
        follow.setChecked(bool(settings.get('grid_follow_placement', True)))
        follow.setToolTip('放置模式選幾分音符，就多畫那一層（原本的放置格線）')
        follow.toggled.connect(lambda on: self._set_grid_follow(on))
        menu.addSeparator()

        chosen = self._grid_divisions()
        extra = sorted({int(n) for n in (settings.get('custom_note_divisions', []) or [])
                        if str(n).isdigit()} | set(chosen) - set(self._GRID_STANDARD))
        for n in list(self._GRID_STANDARD) + extra:
            act = menu.addAction(note_value_icon(4.0 / n),
                                 self._GRID_NAMES.get(n, '%d分音符' % n))
            act.setCheckable(True)
            act.setChecked(n in chosen)
            act.toggled.connect(lambda on, value=n: self._toggle_grid_division(value, on))
        menu.addSeparator()
        menu.addAction('自訂 N 分音符…', self._ask_grid_division)
        clear = menu.addAction('清除自己勾的格線', self._clear_grid_divisions)
        clear.setEnabled(bool(chosen))
        menu.addSeparator()

        from PyQt5.QtWidgets import QActionGroup
        current = str(settings.get('place_grid_mode', 'placement'))
        group = QActionGroup(menu)
        for value, label in self._GRID_MODES:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == value)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, v=value: self._set_grid_mode(v))

    def _refresh_grid(self) -> None:
        for view in self._panes:
            view.update()

    def _set_grid_follow(self, on: bool) -> None:
        settings.set('grid_follow_placement', bool(on))
        self._refresh_grid()

    def _set_grid_mode(self, mode: str) -> None:
        settings.set('place_grid_mode', mode)
        self._refresh_grid()

    def _toggle_grid_division(self, n: int, on: bool) -> None:
        chosen = [d for d in self._grid_divisions() if d != int(n)]
        if on:
            chosen.append(int(n))
        chosen.sort()
        settings.set('grid_divisions', chosen)
        # 自己勾了格線卻因為「只在放置模式時顯示」而看不到，會以為沒作用
        if on and str(settings.get('place_grid_mode', 'placement')) == 'placement' \
                and not any(getattr(v, '_note_input_mode', False) for v in self._panes):
            settings.set('place_grid_mode', 'always')
            self.statusBar().showMessage('格線改成一直顯示（不用開放置模式也看得到）', 3000)
        self._refresh_grid()

    def _ask_grid_division(self) -> None:
        n, ok = QInputDialog.getInt(
            self, '自訂格線',
            '一個全音符分成幾份？\n\n例：5 = 五連四分音符、20 = 五連 16 分音符、96 = 64 分三連音',
            16, 1, 256, 1)
        if ok:
            self._toggle_grid_division(int(n), True)

    def _clear_grid_divisions(self) -> None:
        settings.set('grid_divisions', [])
        self._refresh_grid()

    def _on_dur_combo_changed(self, idx: int) -> None:
        """音符時值下拉選單改變。"""
        if idx == len(self._note_dur_items):
            self._ask_custom_duration()
            return
        if 0 <= idx < len(self._note_dur_items):
            self._ni_dur_idx = idx
            self._mirror_combos('dur_combo', idx)
            _, beats = self._note_dur_items[idx]
            for v in self._panes:
                v.set_note_duration(beats)

    def _on_hand_combo_changed(self, idx: int) -> None:
        """放置左右手下拉選單改變。"""
        if idx >= 0:
            self._ni_hand_idx = idx
            self._mirror_combos('hand_combo', idx)
            for v in self._panes:
                v.set_note_input_hand(idx)
            for tbs in getattr(self, '_toolbars', ()):
                self._refresh_type_icons(tbs)

    def _refresh_type_icons(self, tbs) -> None:
        """類型選單的圖示換成目前這隻手的顏色（右紅、左藍）。"""
        combo = getattr(tbs, 'type_combo', None)
        values = getattr(self, '_type_combo_values', None)
        if combo is None or not values:
            return
        from .note_icons import note_type_icon
        for i, nt in enumerate(values[:combo.count()]):
            combo.setItemIcon(i, note_type_icon(nt, self._ni_hand_idx))

    def _on_width_combo_changed(self, idx: int) -> None:
        """放置寬度下拉選單改變。"""
        if idx >= 0:
            self._ni_width_idx = idx
            self._mirror_combos('width_combo', idx)
            for v in self._panes:
                v.set_note_input_width(idx + 1)

    def _on_type_combo_changed(self, idx: int) -> None:
        """放置音符類型下拉選單改變。"""
        values = getattr(self, '_type_combo_values', None)
        if idx >= 0:
            self._ni_type_idx = idx
            self._mirror_combos('type_combo', idx)
        for v in self._panes:
            if values and 0 <= idx < len(values):
                v.set_note_input_note_type(values[idx])
            elif idx >= 0:
                v.set_note_input_note_type(idx)

    # ==================================================================
    # 新增譜面
    # ==================================================================

    def new_chart_dialog(self) -> None:
        """開啟「新增譜面」對話框，建立空白譜面並載入。
        若目前有未儲存的變更，先詢問是否儲存。"""
        m = self.view.model
        # 若目前有內容（有音符或已開啟檔案），詢問是否繼續
        if m.current_file or m.notes_tree:
            if m.dirty:
                reply = QMessageBox.question(
                    self, t('dlg_unsaved_title'),
                    '目前譜面有未儲存的變更。\n要繼續建立新譜面並捨棄變更嗎？',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return

        dlg = NewChartDialog(
            self,
            default_bpm=120.0,
            default_duration=180,
            default_beats=4,
        )
        if dlg.exec_() != NewChartDialog.Accepted:
            return

        try:
            model = NoteModel.create_new(
                dlg.song_name,
                dlg.bpm,
                dlg.duration_sec,
                dlg.beats_per_bar,
            )
            self._load_model_all(model)
            self._rebuild_hit_times()
            self._refresh_title()
            # 自動進入放置音符模式
            self._set_note_input_mode(True)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    # ==================================================================
    # 小節操作
    # ==================================================================

    def add_measure_dialog(self) -> None:
        """新增小節：詢問新小節的 BPM，然後在末尾追加。"""
        m = self.view.model
        # 只看有沒有拍點資料。以前還檢查 `m.root is None`，等於把 JSON 譜面
        # （遊戲匯出的 .json，拍點存在 json_meta['beat_timings']）整個擋掉——
        # 明明有 213 筆拍點、54 小節，卻回報「找不到小節資料」。
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        cur_bpm = m.bpm
        bpm, ok = QInputDialog.getDouble(
            self,
            t('dlg_add_measure_title'),
            t('dlg_add_measure_label', cur_bpm),
            cur_bpm, 10.0, 999.0, 2,
        )
        if not ok:
            return
        try:
            m.add_measure(bpm)
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def insert_measure_at(self, measure_idx: int) -> None:
        """在指定小節**之前**插入一個空白小節，後面整段往後推（右鍵小節選單）。

        和工具列的「新增小節」不同：那個是接在譜尾，這個是插在中間。
        """
        m = self.view.model
        if not m.get_beat_entries():        # XML / JSON 都可以，只要有拍點
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return
        try:
            cur_bpm = float(m.get_measure_bpm(measure_idx))
        except Exception:
            cur_bpm = float(m.bpm)
        if cur_bpm <= 0:
            cur_bpm = float(m.bpm)

        bpm, ok = QInputDialog.getDouble(
            self, '插入空白小節',
            f'插在第 {measure_idx + 1} 小節之前。新小節的 BPM（決定小節長度）：',
            cur_bpm, 10.0, 999.0, 2,
        )
        if not ok:
            return
        was_dirty = m.dirty
        m.push_history()
        try:
            if not m.insert_measure(measure_idx, bpm):
                m.discard_last_history()
                m.dirty = was_dirty
                QMessageBox.warning(self, t('dlg_warn'), '無法插入小節。')
                return
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
            self.view.note_edited.emit()
            self._rebuild_hit_times()
            self.statusBar().showMessage(
                '已在第 %d 小節前插入空白小節' % (measure_idx + 1), 5000)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def delete_measure_dialog(self) -> None:
        """刪除小節：根據目前視窗中央所在的小節，彈出確認後刪除。"""
        m = self.view.model
        if not m.get_beat_entries():        # XML / JSON 都可以，只要有拍點
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return
        # 用視窗中央的 unit 決定要刪的小節（與 barline 顯示一致）
        center_unit = self.view.window_start_unit + self.view.window_size_unit / 2.0
        center_ms   = self.view.mapper.unit_to_ms(center_unit)
        self.delete_measure_at(m.get_measure_at_ms(center_ms))

    def delete_measure_at(self, measure_idx: int) -> None:
        """刪除指定小節（含其中音符），後面整段往前補上。"""
        m = self.view.model
        if not m.get_beat_entries():        # XML / JSON 都可以，只要有拍點
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        start_ms, end_ms = m.get_measure_time_range(measure_idx)
        if start_ms is None:
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        # 計算小節內的音符數
        n_notes = sum(
            1 for n in m.notes_tree
            if start_ms <= n.start < end_ms
        )
        display_bar = measure_idx + 1   # 1-indexed

        if n_notes > 0:
            msg = t('dlg_delete_measure_msg',
                    display_bar, start_ms, end_ms, n_notes)
        else:
            msg = t('dlg_delete_measure_empty',
                    display_bar, start_ms, end_ms)

        reply = QMessageBox.question(
            self,
            t('dlg_delete_measure_title'),
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            m.push_history()
            deleted = m.delete_measure(measure_idx)
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.selected.clear()
            self.view.update()
            self.view.note_edited.emit()
            self.view.selection_changed.emit(0)
            self._rebuild_hit_times()
            self.statusBar().showMessage(
                '已刪除第 %d 小節（含 %d 顆音符）' % (display_bar, deleted or 0), 5000)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def set_measure_bpm_dialog(self) -> None:
        """修改小節 BPM：根據目前視窗中央所在的小節。"""
        m = self.view.model
        # 支援 JSON-only（只有 json_meta['beat_timings']）情況
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return
        # 與 barline 顯示一致：以 ms 換算小節編號
        center_unit = self.view.window_start_unit + self.view.window_size_unit / 2.0
        center_ms   = self.view.mapper.unit_to_ms(center_unit)
        self.set_measure_bpm_at(m.get_measure_at_ms(center_ms))

    def set_measure_bpm_at(self, measure_idx: int) -> None:
        """以小節編號直接彈出修改 BPM 對話框。"""
        m = self.view.model
        # 支援 JSON-only（只有 beat_timings）情況：以 get_beat_entries() 判斷
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        start_ms, end_ms = m.get_measure_time_range(measure_idx)
        if start_ms is None:
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        current_bpm = m.get_measure_bpm(measure_idx)
        display_bar = measure_idx + 1
        label = t('dlg_set_measure_bpm_label', display_bar, current_bpm)

        # 自訂對話框：輸入 BPM 並選擇模式（縮放 / 裁減/拉長）
        dlg = QDialog(self)
        dlg.setWindowTitle(t('dlg_set_measure_bpm_title'))
        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        bpm_spin = QDoubleSpinBox()
        bpm_spin.setRange(1.0, 9999.0)
        bpm_spin.setDecimals(2)
        bpm_spin.setValue(float(current_bpm))
        form.addRow(label, bpm_spin)

        # 模式選擇
        layout.addLayout(form)
        layout.addWidget(QLabel('小節內音符處理方式：'))
        from PyQt5.QtWidgets import QRadioButton
        rb_scale = QRadioButton('縮放（依比例改）')
        rb_trim  = QRadioButton('裁減/拉長（保留 start，超出裁剪）')
        rb_scale.setChecked(True)
        layout.addWidget(rb_scale)
        layout.addWidget(rb_trim)

        # 保留原有 view.time_uniform 行為（影響 beat timings 重排）
        hint = QLabel('（"小節均分模式" 會影響小節內 beat timings 重排）')
        hint.setStyleSheet('color: palette(mid); font-size: 11px')
        layout.addWidget(hint)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        layout.addWidget(bbox)

        if dlg.exec_() != QDialog.Accepted:
            return

        new_bpm = float(bpm_spin.value())
        mode = 'scale' if rb_scale.isChecked() else 'trim'

        try:
            m.push_history()
            m.set_measure_bpm(measure_idx, new_bpm, uniform=bool(self.view.time_uniform), mode=mode)
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
            self._rebuild_hit_times()
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    # 「同 BPM」的容許誤差：實際量出來的每小節 BPM 會抖大約 0.5
    _SAME_BPM_TOLERANCE = 0.5

    def change_measures_bpm_dialog(self) -> None:
        """設定多個小節的 BPM（指定起始小節與結束小節，以 1-based 輸入）。"""
        m = self.view.model
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_delete_measure_no_data'))
            return
        total_measures = max(1, m.count_measures())
        from PyQt5.QtWidgets import (
            QDialog, QFormLayout, QSpinBox, QDoubleSpinBox, QDialogButtonBox,
            QVBoxLayout, QHBoxLayout, QRadioButton, QLabel, QCheckBox, QPushButton
        )

        dlg = QDialog(self)
        dlg.setWindowTitle('多小節 BPM / 拍號')
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()

        start_spin = QSpinBox()
        start_spin.setRange(1, total_measures)
        start_spin.setValue(1)
        end_spin = QSpinBox()
        end_spin.setRange(1, total_measures)
        end_spin.setValue(min(1 + 4, total_measures))
        form.addRow('起始小節 (1-based):', start_spin)
        form.addRow('結束小節 (1-based):', end_spin)
        end_row = QHBoxLayout()
        to_last_btn = QPushButton('到最後一小節')
        to_last_btn.setToolTip(f'結束小節設為第 {total_measures} 小節')
        same_bpm_btn = QPushButton('到同 BPM 的最後一小節')
        same_bpm_btn.setToolTip(
            f'從起始小節往後找，整段 BPM 都落在同一個 ±{self._SAME_BPM_TOLERANCE:g} 範圍內就算同一段'
            '（實際 BPM 會小幅抖動）')
        end_row.addWidget(to_last_btn)
        end_row.addWidget(same_bpm_btn)
        end_row.addStretch(1)
        form.addRow('', end_row)
        run_label = QLabel('')
        run_label.setStyleSheet('color: gray;')
        form.addRow('', run_label)

        def jump_to_last() -> None:
            end_spin.setValue(total_measures)
            run_label.setText('')

        def jump_to_same_bpm() -> None:
            first = int(start_spin.value())
            last_idx, mean = m.same_bpm_run(first - 1, self._SAME_BPM_TOLERANCE)
            end_spin.setValue(last_idx + 1)
            run_label.setText(f'第 {first}–{last_idx + 1} 小節，平均 BPM {mean:.2f}')

        to_last_btn.clicked.connect(lambda _checked=False: jump_to_last())
        same_bpm_btn.clicked.connect(lambda _checked=False: jump_to_same_bpm())

        bpm_spin = QDoubleSpinBox()
        cur_bpm = m.bpm if getattr(m, 'bpm', 0) else 120.0
        bpm_spin.setRange(1.0, 9999.0)
        bpm_spin.setDecimals(2)
        bpm_spin.setValue(float(cur_bpm))
        form.addRow('BPM:', bpm_spin)

        num_spin = QSpinBox()
        num_spin.setRange(1, 64)
        num_spin.setValue(int(m.get_beats_per_bar_at_ms(0) or getattr(m, 'beats_per_bar', 4)))
        den_spin = QSpinBox()
        den_spin.setRange(1, 64)
        den_spin.setValue(int(getattr(m, 'time_sig_denominator', 4)))
        num_label = QLabel('拍號 分子 (numerator):')
        den_label = QLabel('拍號 分母 (denominator):')
        form.addRow(num_label, num_spin)
        form.addRow(den_label, den_spin)

        vbox.addLayout(form)

        apply_ts_chk = QCheckBox('同時修改拍號 (應用於上述小節範圍)')
        vbox.addWidget(apply_ts_chk)

        from PyQt5.QtWidgets import QButtonGroup

        vbox.addWidget(QLabel('音符處理：'))
        note_hb = QHBoxLayout()
        rb_adjust_notes = QRadioButton('調整音符')
        rb_keep_notes = QRadioButton('不調整音符')
        rb_adjust_notes.setChecked(True)
        note_hb.addWidget(rb_adjust_notes)
        note_hb.addWidget(rb_keep_notes)
        vbox.addLayout(note_hb)
        # 一定要各自分組。四顆單選鈕的 parent 都是這個對話框，Qt 的 autoExclusive
        # 是照 parent 分的，不分組的話點「保留相對位置」會把「調整音符」取消掉。
        note_group = QButtonGroup(dlg)
        note_group.addButton(rb_adjust_notes)
        note_group.addButton(rb_keep_notes)
        # 不調整音符時：從段落開頭做很小的縮放，讓音符對上新的小節線（見 bpm_snap）
        snap_chk = QCheckBox('吸附小節線')
        snap_chk.setToolTip(
            '音符保持原本的速度，只做很小的縮放（最多 ±8%）去對上新的小節線：\n'
            '・先找讓音符最貼合新拍格的比例（適合 BPM 原本設錯的情況）\n'
            '・找不到的話，至少讓這段結尾剛好落在整數小節上\n'
            '不會像「調整音符」那樣整段變慢／變快。')
        snap_row = QHBoxLayout()
        snap_row.addSpacing(24)
        snap_row.addWidget(snap_chk)
        snap_row.addStretch(1)
        vbox.addLayout(snap_row)

        rearrange_label = QLabel('小節內音符重排方式（拍號變更時）：')
        vbox.addWidget(rearrange_label)
        hb = QHBoxLayout()
        rb_uniform = QRadioButton('均分小節內拍子（等距）')
        rb_preserve = QRadioButton('保留相對位置（只縮放）')
        rb_uniform.setChecked(True)
        hb.addWidget(rb_uniform)
        hb.addWidget(rb_preserve)
        vbox.addLayout(hb)
        rearrange_group = QButtonGroup(dlg)
        rearrange_group.addButton(rb_uniform)
        rearrange_group.addButton(rb_preserve)

        # 拍號那三組只有勾了「同時修改拍號」才會被用到（分子/分母直接餵給
        # set_measure_time_signature，重排方式是它內部的 `uniform`）。沒勾的話
        # 動它們完全沒有效果，所以直接變灰，不要讓人以為調了有用。
        ts_hint = QLabel('（拍號欄位與重排方式要勾「同時修改拍號」才會生效）')
        ts_hint.setStyleSheet('color: gray; font-size: 10px;')
        ts_hint.setWordWrap(True)
        vbox.addWidget(ts_hint)

        def sync_ts_enabled(_=None) -> None:
            on = apply_ts_chk.isChecked()
            for wdg in (num_label, num_spin, den_label, den_spin):
                wdg.setEnabled(on)
            # 重排方式只在「調整音符」時才有意義，不調整就直接藏起來，不要留
            # 一排灰灰的東西讓人猜。要不要**能按**還是看有沒有勾同時修改拍號。
            show = rb_adjust_notes.isChecked()
            for wdg in (rearrange_label, rb_uniform, rb_preserve):
                wdg.setVisible(show)
                wdg.setEnabled(on)
            ts_hint.setVisible(not on)
            snap_chk.setVisible(not show)
        apply_ts_chk.toggled.connect(sync_ts_enabled)
        rb_adjust_notes.toggled.connect(sync_ts_enabled)
        sync_ts_enabled()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vbox.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        # sync end_spin min with start_spin
        def on_start_changed(val: int) -> None:
            end_spin.setMinimum(val)
        start_spin.valueChanged.connect(on_start_changed)

        if dlg.exec_() != QDialog.Accepted:
            return

        start = int(start_spin.value())
        end = int(end_spin.value())
        bpm = float(bpm_spin.value())
        apply_ts = bool(apply_ts_chk.isChecked())
        new_num = int(num_spin.value())
        new_den = int(den_spin.value())
        uniform_choice = bool(rb_uniform.isChecked())
        adjust_notes = bool(rb_adjust_notes.isChecked())
        snap_bars = (not adjust_notes) and bool(snap_chk.isChecked())

        s_idx = start - 1
        e_idx = end - 1
        # 吸附要用「改之前」這段的起點與長度
        anchor_ms, _unused = m.get_measure_time_range(s_idx)
        _unused, old_end_ms = m.get_measure_time_range(e_idx)
        snap_text = ''
        try:
            m.push_history()
            for mi in range(s_idx, e_idx + 1):
                try:
                    m.set_measure_bpm(mi, bpm, uniform=bool(self.view.time_uniform), adjust_notes=adjust_notes)
                except Exception:
                    continue
                if apply_ts:
                    try:
                        m.set_measure_time_signature(mi, new_num, new_den,
                                                     uniform=uniform_choice,
                                                     time_uniform=bool(self.view.time_uniform))
                    except Exception:
                        continue
            if snap_bars and anchor_ms is not None and old_end_ms is not None:
                from . import bpm_snap
                result = bpm_snap.snap(m, s_idx, e_idx, float(anchor_ms), float(old_end_ms))
                m.rebuild_display_cache()
                snap_text = '\n\n' + bpm_snap.describe(result)
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
            self._rebuild_hit_times()
            self.view.note_edited.emit()
            QMessageBox.information(self, '完成', f'已將第 {start} 到第 {end} 小節的 BPM 設為 {bpm:.2f}' + (f'，並修改拍號為 {new_num}/{new_den}' if apply_ts else '') + snap_text)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def set_measure_time_sig_dialog(self) -> None:
        """以視窗中央小節為目標，彈出修改拍號對話框。"""
        m = self.view.model
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_delete_measure_no_data'))
            return
        center_unit = self.view.window_start_unit + self.view.window_size_unit / 2.0
        center_ms = self.view.mapper.unit_to_ms(center_unit)
        self.set_measure_time_sig_at(m.get_measure_at_ms(center_ms))

    def set_measure_time_sig_at(self, measure_idx: int) -> None:
        """直接修改指定小節的拍號（彈出對話框）。"""
        m = self.view.model
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_delete_measure_no_data'))
            return

        start_ms, end_ms = m.get_measure_time_range(measure_idx)
        if start_ms is None:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_delete_measure_no_data'))
            return

        # current numerator/denominator
        cur_num = m.get_beats_per_bar_at_ms(start_ms)
        cur_den = m.time_sig_denominator
        for ms, num, den in m.time_sig_changes:
            if ms <= start_ms:
                cur_den = den
            else:
                break

        # 使用對話框：輸入分子/分母，並詢問如何重排小節內音符（均分 / 保留相對位置）
        from PyQt5.QtWidgets import (
            QDialog, QSpinBox, QFormLayout, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QRadioButton, QLabel
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(t('dlg_set_measure_time_sig_title') if 'dlg_set_measure_time_sig_title' in globals() else '設定小節拍號')
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()
        num_spin = QSpinBox()
        num_spin.setRange(1, 64)
        num_spin.setValue(int(cur_num))
        den_spin = QSpinBox()
        den_spin.setRange(1, 64)
        den_spin.setValue(int(cur_den))
        form.addRow('分子 (numerator):', num_spin)
        form.addRow('分母 (denominator):', den_spin)
        vbox.addLayout(form)

        vbox.addWidget(QLabel('小節內音符重排方式：'))
        hb = QHBoxLayout()
        rb_uniform = QRadioButton('均分小節內拍子（等距）')
        rb_preserve = QRadioButton('保留相對位置（只縮放）')
        # 默認為小節均分
        rb_uniform.setChecked(True)
        hb.addWidget(rb_uniform)
        hb.addWidget(rb_preserve)
        vbox.addLayout(hb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vbox.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec_() != QDialog.Accepted:
            return

        new_num = int(num_spin.value())
        new_den = int(den_spin.value())
        uniform_choice = bool(rb_uniform.isChecked())
        try:
            m.push_history()
            m.set_measure_time_signature(
                measure_idx,
                new_num,
                new_den,
                uniform=uniform_choice,
                time_uniform=bool(self.view.time_uniform),
            )
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
            self._rebuild_hit_times()
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def remove_duplicate_start_pitch_dialog(self) -> None:
        """移除具有相同 start time 與相同 pitch 的重複音符，優先保留長度最長者。"""
        m = self.view.model
        if not m.notes_tree:
            QMessageBox.information(self, '資訊', '目前沒有音符可以處理。')
            return

        # 分組：以 (start, pitch) 為 key，只處理有 pitch 的音符
        from collections import defaultdict
        groups = defaultdict(list)
        for n in m.notes_tree:
            if getattr(n, 'pitch', None) is None:
                continue
            key = (int(getattr(n, 'start', 0)), int(getattr(n, 'pitch', 0)))
            groups[key].append(n)

        to_remove = set()
        for key, lst in groups.items():
            if len(lst) <= 1:
                continue
            # 保留 gate/長度最大的那一個（若相同則保留第一個）
            keeper = max(lst, key=lambda x: (int(getattr(x, 'end', 0)) - int(getattr(x, 'start', 0))))
            for n in lst:
                if n is keeper:
                    continue
                to_remove.add(n)

        if not to_remove:
            QMessageBox.information(self, '完成', '未發現相同 start 與 pitch 的重複音符。')
            return

        # 確認提示
        reply = QMessageBox.question(self, '確認', f'將移除 {len(to_remove)} 個重複音符（保留最長者），是否繼續？',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            m.push_history()
            before = len(m.notes_tree)
            m.notes_tree = [n for n in m.notes_tree if n not in to_remove]
            after = len(m.notes_tree)
            m.rebuild_display_cache()
            self.view.update()
            self._rebuild_hit_times()
            self.view.note_edited.emit()
            QMessageBox.information(self, '完成', f'已移除 {before - after} 個重複音符。')
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(e))

    def unhide_all_dialog(self) -> None:
        """把整份譜面的隱藏音符全部取消隱藏，變回正常音符。

        右鍵已經有針對選取範圍的版本，但隱藏音符**在多數檢視模式下畫不出來**
        （只有音高模式看得到），要一顆一顆選根本選不到。整份處理才實用。

        取消之後它們會變成真的、要打的音符，譜面的音符數會增加——所以先把
        數量講清楚再問。
        """
        m = self.view.model
        hidden = [n for n in m.notes_tree if getattr(n, 'hidden', False)]
        if not hidden:
            QMessageBox.information(self, '取消隱藏', '這份譜面沒有隱藏音符。')
            return

        visible = len(m.notes_tree) - len(hidden)
        reply = QMessageBox.question(
            self, '取消隱藏',
            '這份譜面有 %d 顆隱藏音符。\n\n'
            '取消隱藏之後它們會變成正常音符，玩家要打——'
            '可打的音符數會從 %d 變成 %d。\n\n要繼續嗎？'
            % (len(hidden), visible, len(m.notes_tree)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            m.push_history()
            for note in hidden:
                note.hidden = False
            m.rebuild_display_cache()
            self.view.update()
            self._rebuild_hit_times()
            self.view.note_edited.emit()
            QMessageBox.information(
                self, '完成', '已取消 %d 顆音符的隱藏。' % len(hidden))
        except Exception as exc:                # noqa: BLE001
            logging.exception('unhide_all_dialog failed')
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(exc))

    # ==================================================================
    # 檔案操作
    # ==================================================================

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_open_title'), '', t('dlg_file_filter'),
        )
        if path:
            self._load_path(path)

    def _load_midi_with_progress(self, model: NoteModel, path: str) -> bool:
        """載入 MIDI 並顯示進度，避免長時間排譜時視窗變成「沒有回應」。

        排譜是一段不可細分的 CPU 工作，所以用不定量（busy）進度條 + 背景
        執行緒：主執行緒持續抽事件，視窗就不會被系統標成無回應。這裡刻意
        不用 QProgressDialog.exec_() 的巢狀模態迴圈 —— 那個迴圈的結束時機
        依賴 reset()，在某些平台上會關不掉。改成自己 pump 事件比較可控。
        """
        from PyQt5.QtCore import QThread, pyqtSignal as _sig
        from PyQt5.QtWidgets import QProgressDialog, QMessageBox

        # 匯入 MIDI 不再硬性轉譜——先問要怎麼轉（模式／鍵道範圍／和絃重疊／
        # 自訂參數）。按「先不轉譜」就停在 MIDI 編輯模式，使用者可以先自己
        # 整理，之後再轉。
        arrange, options = self.ask_arrange_options(
            intro=t('dlg_midi_arrange_ask', os.path.basename(path)),
            allow_skip=True,
        )
        if arrange is None:                       # 取消
            return False
        auto_arrange = bool(arrange)
        if auto_arrange:
            settings.set('chart_style', options.style())
            model.chart_style = options.style()

        class _Worker(QThread):
            done = _sig(bool, str)

            def run(self) -> None:
                try:
                    model.load_midi(path, auto_arrange=auto_arrange,
                                    arrange_options=options)
                    self.done.emit(True, '')
                except Exception as exc:            # noqa: BLE001
                    self.done.emit(False, str(exc))

        outcome: Dict[str, Any] = {'finished': False, 'ok': False, 'err': ''}

        def _finished(ok: bool, err: str) -> None:
            outcome['finished'] = True
            outcome['ok'] = ok
            outcome['err'] = err

        worker = _Worker(self)
        worker.done.connect(_finished)

        dlg = QProgressDialog(
            t('dlg_midi_converting', os.path.basename(path)),
            t('dlg_cancel'), 0, 0, self,
        )
        dlg.setWindowTitle(t('dlg_midi_converting_title'))
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.show()

        cancelled = False
        started = time.monotonic()
        base_label = dlg.labelText()
        worker.start()
        while worker.isRunning():
            QApplication.processEvents()
            if not cancelled:
                dlg.setLabelText(
                    '%s  |  %s' % (base_label,
                              t('dlg_elapsed', int(time.monotonic() - started)))
                )
            if not cancelled and dlg.wasCanceled():
                # 排譜無法中斷，只能等它跑完再丟棄結果
                cancelled = True
                dlg.setLabelText(t('dlg_midi_cancelling'))
                dlg.setCancelButton(None)
            worker.wait(30)
        QApplication.processEvents()
        worker.wait()
        dlg.close()

        if cancelled:
            return False
        if not outcome['ok']:
            QMessageBox.critical(self, t('dlg_load_fail_title'),
                                 t('dlg_load_fail_msg', outcome['err']))
            return False
        return True

    def _open_midi_hand(self, hand: int) -> None:
        """開啟 MIDI，將其中**所有**音符視為指定手（0=右 1=左），合併進目前譜面。"""
        # 對於正在執行的應用，嘗試延遲匯入 converter（避免需重啟）
        try:
            from .midi_to_xml_converter import MIDIToXMLConverter
        except Exception:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_midi_no_conv'))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t('action_open_midi_right') if hand == 0 else t('action_open_midi_left'),
            '', 'MIDI (*.mid *.midi)',
        )
        if not path:
            return
        try:
            import tempfile
            xml_out = os.path.join(tempfile.gettempdir(), '_nos_midi_hand_tmp.xml')
            MIDIToXMLConverter().convert_midi_to_xml(path, xml_out, resolve_overlaps=False)
            tmp_model = NoteModel()
            tmp_model.load_xml(xml_out)
            # 所有音符都強制指定手（不管 MIDI 原本的手部判斷）
            new_notes = tmp_model.notes_tree
            for n in new_notes:
                n.hand = hand
            if not new_notes:
                QMessageBox.information(self, t('dlg_warn'), t('dlg_midi_no_notes'))
                return
            # 保留現有另一手的音符，將此手換成 MIDI 來的
            self.view.model.push_history()
            kept = [n for n in self.view.model.notes_tree if n.hand != hand]
            self.view.model.notes_tree = kept + new_notes
            self.view.model.rebuild_display_cache()
            self.view._update_unit_bounds()   # 更新捲動上下界
            self.view.scroll_to_top()          # 捲回開頭，避免看到空白
            self.view.selected.clear()
            self.view.update()
            self.view.selection_changed.emit(0)
            self._rebuild_hit_times()
            msg_key = 'dlg_midi_right_done' if hand == 0 else 'dlg_midi_left_done'
            QMessageBox.information(self, t('dlg_save_ok_title'), t(msg_key, len(new_notes)))
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    @staticmethod
    def _midi_note_track_count(path: str) -> int:
        """這個 MIDI 有幾條軌真的帶音符（空軌、純指揮軌不算）。"""
        try:
            import mido
        except Exception:
            return 0
        try:
            mid = mido.MidiFile(path)
        except Exception:
            return 0
        count = 0
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and int(getattr(msg, 'velocity', 0)) > 0:
                    count += 1
                    break
        return count

    def _open_midi_overlay(self) -> None:
        """把 MIDI **疊**進目前譜面，不動現有的左右手。

        和上面那兩個「右手／左手」不一樣：那兩個是**取代**該手的全部音符，
        這個是純粹加上去 —— 現有的一顆都不會少。

        新來的音符怎麼分手：
        - 來源 MIDI 剛好兩軌時，那兩軌就是左右手（和自動排譜同一條規則），
          直接照它的分配匯入；
        - 分不出來的（單軌、或三軌以上要用猜的）一律標成**無主音**
          （`hand=2`，官方資料本來就有這個值），畫面上是獨立的綠色，
          之後可以自己指定左右手。

        鍵道不動：疊上來的音符沿用轉檔器給的位置，要排譜的話再按自動排譜。
        """
        try:
            from .midi_to_xml_converter import MIDIToXMLConverter
        except Exception:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_midi_no_conv'))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t('action_open_midi_overlay'), '', 'MIDI (*.mid *.midi)',
        )
        if not path:
            return
        try:
            import tempfile
            xml_out = os.path.join(tempfile.gettempdir(), '_nos_midi_overlay_tmp.xml')
            MIDIToXMLConverter().convert_midi_to_xml(path, xml_out, resolve_overlaps=False)
            tmp_model = NoteModel()
            tmp_model.load_xml(xml_out)
            new_notes = tmp_model.notes_tree
            if not new_notes:
                QMessageBox.information(self, t('dlg_warn'), t('dlg_midi_no_notes'))
                return

            # 來源 MIDI 有幾軌真的帶音符——剛好兩軌才算「已經分配」。
            # 要從 MIDI 檔本身數：轉出來的 XML 沒有 track 欄位（那個格式本來
            # 就沒有），拿轉檔後的音符去數會全部是 None。
            assigned = self._midi_note_track_count(path) == 2
            if not assigned:
                for n in new_notes:
                    n.hand = 2

            model = self.view.model
            model.push_history()
            model.notes_tree = list(model.notes_tree) + new_notes
            model.notes_tree.sort(key=lambda n: (int(n.start), int(n.min_key)))
            for index, note in enumerate(model.notes_tree):
                note.idx = index
            model.rebuild_display_cache()
            model.dirty = True
            self.view._update_unit_bounds()
            self.view.selected.clear()
            self.view.update()
            self.view.selection_changed.emit(0)
            self._rebuild_hit_times()
            QMessageBox.information(
                self, t('dlg_save_ok_title'),
                t('dlg_midi_overlay_done', len(new_notes),
                  t('dlg_midi_overlay_kept') if assigned
                  else t('dlg_midi_overlay_none')))
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def transcribe_audio_ai(self) -> None:
        """音檔 → AI 鋼琴轉譜成 MIDI → 設 BPM／吸附小節線 → 走一般的開 MIDI 流程。

        轉出來的 MIDI 時間軸是音檔本身的時間（固定 120 BPM、沒有拍子資訊）。
        設了 BPM 的話改寫成 `歌名.ai.<BPM>bpm.mid`，音檔開頭補靜音（`_lead+N.wav`）
        讓第一小節落在小節線上；mp3 等格式轉譜時已順便解碼成同名 WAV。
        載入後把那份 WAV 掛成背景音樂，一開就對得上。
        """
        from . import ai_transcribe as AT
        from .ai_transcribe_dialog import ensure_installed, run_transcribe

        if not ensure_installed(self):
            return
        start_dir = os.path.dirname(getattr(self.audio, 'audio_path', '') or
                                    self.view.model.current_file or '')
        path, _ = QFileDialog.getOpenFileName(
            self, '選擇要轉譜的音檔', start_dir,
            '音檔 (*.wav *.flac *.ogg *.mp3);;All files (*)')
        if not path:
            return
        if os.path.isfile(path) and os.path.getsize(path) == 0:
            QMessageBox.warning(
                self, 'AI 轉譜',
                '這個音檔是空的（0 位元組），多半是下載沒有成功，請重新下載：\n\n%s' % path)
            return

        # 模型只認獨奏鋼琴：挑了整首混音、旁邊卻有鋼琴分軌時建議改用分軌
        stem, ext = os.path.splitext(path)
        piano_stem = stem + '_piano' + ext
        if not stem.lower().endswith('_piano') and os.path.isfile(piano_stem):
            reply = QMessageBox.question(
                self, 'AI 轉譜',
                '同一個資料夾裡有鋼琴分軌：\n\n　%s\n\n模型只認獨奏鋼琴，用分軌轉出來會乾淨很多。'
                '要改用分軌嗎？' % os.path.basename(piano_stem),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                path = piano_stem

        out = AT.default_output_path(path)
        reuse = False
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(path):
            box = QMessageBox(self)
            box.setWindowTitle('AI 轉譜')
            box.setText('這個音檔已經轉過了：\n\n　%s\n\n要直接用上次的結果，還是重新轉譜？'
                        % os.path.basename(out))
            use_btn = box.addButton('用上次的', QMessageBox.AcceptRole)
            redo_btn = box.addButton('重新轉譜', QMessageBox.AcceptRole)
            box.addButton(t('dlg_cancel'), QMessageBox.RejectRole)
            box.setDefaultButton(use_btn)
            box.exec_()
            if box.clickedButton() not in (use_btn, redo_btn):
                return
            reuse = box.clickedButton() is use_btn

        summary = ''
        if not reuse:
            try:
                result = run_transcribe(self, path, out)
            except OSError as exc:              # 音檔旁邊寫不進去之類
                QMessageBox.critical(self, 'AI 轉譜失敗', str(exc))
                return
            if not result:
                return
            if not result.get('notes'):
                QMessageBox.information(self, 'AI 轉譜', '沒有轉出任何音符（音檔裡可能沒有鋼琴聲）。')
                return
            summary = '轉出 %d 顆音、%d 段踏板（%s，%.0f 秒）' % (
                result['notes'], result.get('pedals', 0),
                'GPU' if result.get('device') == 'cuda' else 'CPU', result.get('elapsed', 0))

        # BPM 與小節線：改寫成指定 BPM 的 MIDI，音檔開頭補靜音讓第一小節對上小節線
        from . import ai_grid as G
        from .ai_grid_dialog import AiGridDialog

        grid_dlg = AiGridDialog(self, G.onsets_for_detection(out), os.path.basename(path),
                                division=int(settings.get('ai_grid_division', 4) or 0))
        if grid_dlg.exec_() != QDialog.Accepted:
            return
        wav = AT.wav_path_for(path)
        audio_for_editor = wav if os.path.isfile(wav) else ''
        load_path = out
        if grid_dlg.enabled():
            grid = grid_dlg.settings()
            settings.set('ai_grid_division', grid.division)
            load_path = G.output_path(out, grid.bpm)
            stats = G.build(out, load_path, grid)
            if audio_for_editor and stats.pad_ms:
                from .audio_lead_in import apply_lead_in
                audio_for_editor = apply_lead_in(audio_for_editor, stats.pad_ms).path
            summary = '　'.join(filter(None, [
                summary, '%.2f BPM，開頭補 %d ms' % (grid.bpm, stats.pad_ms),
                ('吸附後合併 %d 顆同格同音' % stats.merged) if stats.merged else '']))

        self._load_path(load_path)
        if os.path.normcase(self.view.model.current_file or '') != os.path.normcase(load_path):
            return                              # 使用者在排譜詢問那裡取消了
        if audio_for_editor and self.audio.load_wav(audio_for_editor):
            self._refresh_vol_rows()
            self._lbl_audio.setText(t('status_audio_loaded', os.path.basename(audio_for_editor)))
        if summary:
            if self.statusBar().isVisible():
                self.statusBar().showMessage(summary, 15000)
            else:
                QMessageBox.information(self, 'AI 轉譜', summary)

    def _manage_ai_transcribe(self) -> None:
        from .ai_transcribe_dialog import manage_environment
        manage_environment(self)

    def _load_path(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        # 這個檔案有比它還新的自動備份：多半是上次改到一半當掉了。
        try:
            backup = autosave.find_backup(path)
        except Exception:                       # noqa: BLE001
            backup = None
        if backup is not None:
            reply = QMessageBox.question(
                self, '找到自動備份',
                '這個檔案有一份比較新的自動備份，可能是上次沒存檔就關掉或當掉了：'
                '\n\n　%s\n\n要還原那份備份嗎？（選「否」就開原本的檔案）'
                % autosave.describe(backup),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                self._open_backup(backup)
                return
        try:
            model = NoteModel()
            if ext == '.json':
                model.load_json(path)
            elif ext in ('.mid', '.midi'):
                # MIDI 匯入含排譜，是唯一會跑到數十秒的路徑 → 走進度視窗
                if not self._load_midi_with_progress(model, path):
                    return
            else:
                model.load_xml(path)
            self._install_loaded_model(model, path)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def _install_loaded_model(self, model: NoteModel, path: str) -> None:
        """把讀好的 model 裝進所有格子並把相關狀態收拾好（開檔與還原備份共用）。"""
        try:
            self._load_model_all(model)
            oplog.session(path, model)
            # 選了「不轉換」的 MIDI：直接進音高模式，其他檢視要先轉譜
            if getattr(model, 'midi_unarranged', False):
                for pane in self._panes:
                    pane.set_view_mode('pitch')
                self._refresh_view_mode_action()
            # 載入檔案時關閉放置音符模式
            self._set_note_input_mode(False)
            # restore hit enable flags from model json_meta if present
            try:
                jm = getattr(model, 'json_meta', {}) or {}
                self._hit_enable_right = bool(jm.get('hit_enable_right', self._hit_enable_right))
                self._hit_enable_left  = bool(jm.get('hit_enable_left', self._hit_enable_left))
                self._hit_enable_beat  = bool(jm.get('hit_enable_beat', self._hit_enable_beat))
                # update menu checked state if actions exist
                try:
                    self._act_hit_right.setChecked(self._hit_enable_right)
                    self._act_hit_left.setChecked(self._hit_enable_left)
                    self._act_hit_beat.setChecked(self._hit_enable_beat)
                except Exception:
                    pass
            except Exception:
                pass
            self._rebuild_hit_times()
            self._convert_loaded_xml(model)
            self._refresh_title()
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def _convert_loaded_xml(self, model: NoteModel) -> None:
        """開的是 XML（PAN 模式），裡面卻有 PAN 沒有的東西（舊版編輯器存的）。

        直接轉換並告訴使用者——留著的話畫面和 PAN 實際讀到的不一樣，例如
        Staccato 在 PAN 裡是長押。轉換可以復原，也還沒寫回檔案。
        """
        if not getattr(model, 'pan_xml', False):
            return
        preview = model.pan_conversion_preview()
        if not preview:
            return
        model.push_history()
        model.convert_to_pan()
        for pane in self._panes:
            pane.update()
        self._apply_format_mode()
        QMessageBox.information(
            self, 'XML 相容模式',
            '這份 XML 裡有 PAN 沒有的東西（多半是舊版編輯器存的），已經先轉換：\n\n%s'
            '\n\n還沒寫回檔案，不要的話可以按「復原」。想保留完整內容請另存成 JSON。'
            % '\n'.join('・%s：%d' % item for item in preview.items()))

    def save_file(self) -> None:
        current = self.view.model.current_file
        # MIDI 存不下鍵道：開 MIDI 進來的譜按 Ctrl+S 走另存新檔（預設 JSON）
        if not current or current.lower().endswith(('.mid', '.midi')):
            self.save_file_as()
            return
        self._do_save(current)

    def save_file_as(self) -> None:
        """另存新檔。預設存 JSON——JSON 是原始檔，XML 是給 PAN 的相容輸出。"""
        m = self.view.model
        current = m.current_file or ''
        if current.lower().endswith(('.json', '.xml')):
            default = current
        elif current:
            default = os.path.splitext(current)[0] + '.json'
        else:
            default = (m._song_name + '.json') if getattr(m, '_song_name', '') else ''
        path, _ = QFileDialog.getSaveFileName(
            self, t('dlg_save_as_title'),
            default,
            'JSON（原始檔，完整） (*.json);;XML（PAN 相容） (*.xml);;MIDI (*.mid *.midi)',
        )
        if path:
            if not os.path.splitext(path)[1]:
                selected_filter = _.lower()
                if 'midi' in selected_filter:
                    path += '.mid'
                elif 'xml' in selected_filter:
                    path += '.xml'
                else:
                    path += '.json'
            self._do_save(path)

    def save_as_json(self) -> None:
        m = self.view.model
        default = (
            os.path.splitext(m.current_file)[0] + '.json'
            if m.current_file else ''
        )
        path, _ = QFileDialog.getSaveFileName(
            self, t('dlg_save_json_title'), default, 'JSON (*.json)',
        )
        if path:
            if not path.lower().endswith('.json'):
                path += '.json'
            self._do_save(path)

    def save_as_xml(self) -> None:
        m = self.view.model
        default = (
            os.path.splitext(m.current_file)[0] + '.xml'
            if m.current_file else ''
        )
        if not default and hasattr(m, '_song_name') and m._song_name:
            default = m._song_name + '.xml'
        path, _ = QFileDialog.getSaveFileName(
            self, t('dlg_save_xml_title'), default, 'XML (*.xml)',
        )
        if path:
            if not path.lower().endswith('.xml'):
                path += '.xml'
            self._do_save(path)

    def save_as_xml_midi_restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            t('dlg_save_xml_midi_restore_title'),
            '',
            'MIDI (*.mid *.midi)',
        )
        if not path:
            return
        try:
            source_model = NoteModel()
            if not self._load_midi_with_progress(source_model, path):
                return
            current_model = self.view.model
            current_model.push_history()
            result = current_model.apply_midi_pitches_from_source_notes(source_model.notes_tree)
            current_model.rebuild_display_cache()
            self.view.update()
            self.view.note_edited.emit()
            self._refresh_title()
            QMessageBox.information(
                self,
                t('dlg_save_ok_title'),
                t(
                    'dlg_apply_midi_restore_done',
                    os.path.basename(path),
                    result.get('matched_notes', 0),
                    result.get('matched_groups', 0),
                    result.get('partial_groups', 0),
                ),
            )
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def restore_from_midi_dialog(self) -> None:
        """從同一份 MIDI 一次把音高和表情（力度／踏板）都還原回來。

        以前是兩個指令、各選一次檔案。但它們的來源永遠是同一份 MIDI，前提
        也一樣——音高對不上就別談力度了——分開只是讓人多選一次檔案，還可能
        不小心選到兩份不同的。

        沒有音高的譜面在「無背景音樂」模式下整首都不會發出聲音（keysound 是
        照音高發的）；力度和 CC64 延音踏板則是轉譜時被丟掉的兩樣東西。

        兩種偏移都會回報：譜面開頭補過空白造成的**時間平移**，以及 MIDI 是
        移調版造成的**音高偏移**。後者要特別講出來——照樣覆蓋下去等於把整首
        移調，畫面上看不出來，只有玩的時候才會發現整首走音。
        """
        path, _ = QFileDialog.getOpenFileName(
            self, '從 MIDI 還原音高與表情', '', 'MIDI (*.mid *.midi)')
        if not path:
            return
        model = self.view.model
        try:
            # 先試算：對不上就什麼都不要動，也不要留一個空的 undo 步驟。
            preview = model.restore_pitches_from_midi(path, apply=False)
        except Exception as e:                      # noqa: BLE001
            logging.exception('restore pitches failed')
            QMessageBox.critical(self, t('dlg_load_fail_title'),
                                 t('dlg_load_fail_msg', e))
            return

        # 用 acceptable 不是 applied：試算永遠沒有寫下去，applied 一定是 False。
        if not preview.acceptable:
            QMessageBox.warning(
                self, '還原音高與表情',
                '這份 MIDI 和譜面對不起來，沒有做任何改動。\n\n'
                '對得上的發音點只有 %.0f%%，其中和弦顆數也一致的只有 %.0f%%'
                '（同一首曲子應該接近 100%%）。\n\n'
                '確認一下是不是選錯檔案了。'
                % (preview.match_ratio * 100, preview.chord_agreement * 100))
            return

        detail = []
        if preview.by_ordinal:
            detail.append(
                '這份 MIDI 和譜面的**時間軸不同**（譜面是固定 BPM、MIDI 有速度圖），\n'
                '所以改用「發音順序」配對：第 n 個發音點配第 n 個。\n'
                '兩邊的發音點數差 %.1f%%。'
                % abs(100.0 - preview.match_ratio * 100))
        if preview.offset_ms:
            detail.append('譜面比 MIDI 晚了 %+d ms（開頭補過空白），已自動扣掉。'
                          % preview.offset_ms)
        if preview.semitones:
            direction = '高' if preview.semitones > 0 else '低'
            gap = abs(preview.semitones)
            if preview.base_mismatch:
                # 差超過一個八度不是移調，是音高基準對不上——而譜面那一側
                # 才是壞的（這個工具本來就是來修它的）。照樣勸退的話，需要
                # 修的譜面反而永遠修不成。
                detail.append(
                    'MIDI 比譜面%s %d 個半音——超過一個八度，那不是移調，是'
                    '音高基準對不上。\n'
                    '    譜面現有音高的中位是 %d、MIDI 是 %d；聽起來合理的'
                    '那一側才是對的，\n'
                    '    通常就是 MIDI（譜面的音高壞掉正是你要修的東西）。'
                    % (direction, gap, preview.chart_median,
                       preview.midi_median))
            else:
                detail.append(
                    '⚠ 這份 MIDI 比譜面%s %d 個半音——是移調版。\n'
                    '    照樣還原會把整首歌移調，畫面上看不出來，只有實際聽'
                    '才會發現走音。' % (direction, gap))
        detail.append('%d 顆音符的音高會被覆蓋，%d 顆配不到參考音符。'
                      % (preview.changed, preview.unmatched))
        detail.append('接著會用同一份 MIDI 把力度與延音踏板一起補回來。')
        answer = QMessageBox.question(
            self, '還原音高與表情', '\n'.join(detail) + '\n\n要套用嗎？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
            if preview.semitones and not preview.base_mismatch
            else QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return

        # 兩步共用一個還原點：使用者眼中這是一個動作，undo 要一次全退。
        model.push_history()
        try:
            result = model.restore_pitches_from_midi(path)
            source_model = NoteModel()
            # 刻意不排譜：力度靠音高配對，鍵道用不到，而排譜正是 MIDI 匯入
            # 裡唯一會跑到數十秒的部分。
            source_model.load_midi(path, auto_arrange=False)
            stats = model.apply_midi_expression_from_source(
                source_model.notes_tree,
                source_model.pedal_spans,
            )
        except Exception as e:                      # noqa: BLE001
            logging.exception('restore from midi failed')
            QMessageBox.critical(self, t('dlg_load_fail_title'),
                                 t('dlg_load_fail_msg', e))
            return

        model.rebuild_display_cache()
        for pane in self._panes:
            pane.update()
        self.view.note_edited.emit()
        self._refresh_title()
        pedal_line = (
            t('dlg_apply_midi_expression_pedal', stats.get('pedal_after', 0))
            if stats.get('pedal_source', 0)
            else t('dlg_apply_midi_expression_no_pedal')
        )
        QMessageBox.information(
            self, t('dlg_save_ok_title'),
            '從 %s：\n'
            '· 音高還原 %d 顆，%d 顆配不到。\n'
            '· 力度還原 %d 顆（配對到 %d / %d 顆）。\n'
            '· %s'
            % (os.path.basename(path), result.changed, result.unmatched,
               stats.get('velocity_applied', 0), stats.get('matched_notes', 0),
               stats.get('total_notes', 0), pedal_line))

    def generate_difficulties_dialog(self) -> None:
        """從目前這份譜面生出 normal / hard / extreme 三份檔案。

        目前這份當成來源（相當於官方的 real）。生成不會刪音符——沒被選中的
        折進鄰近可見音符的 `sub_note_data`，和官方譜同一套機制，所以三個難度
        的音訊事件集和來源完全相同。細節見 `difficulty.py`。

        寫成獨立檔案而不是就地改：一次要產三份，而且使用者要拿它們互相比對。
        """
        from .difficulty import DIFFICULTIES, generate
        from .difficulty_dialog import DifficultyDialog

        model = self.view.model
        if not model.notes_tree:
            QMessageBox.information(self, '生成難度', '這份譜面沒有音符。')
            return
        if not any(n.pitch is not None for n in model.notes_tree):
            QMessageBox.information(
                self, '生成難度',
                '這份譜面沒有音高，排譜器沒有東西可以依據。\n\n'
                '請先用「工具 → 以 MIDI 修復 → 從 MIDI 還原音高與表情」補回來。')
            return

        source = _model_path(model)
        dlg = DifficultyDialog(self, source_path=source,
                               chart_name=str(getattr(model, 'title', '') or ''))
        if dlg.exec_() != QDialog.Accepted:
            return
        wanted = dlg.chosen()
        if not wanted:
            return
        out_dir, stem = dlg.out_dir(), dlg.stem()
        if not os.path.isdir(out_dir):
            QMessageBox.warning(self, '生成難度', '輸出資料夾不存在：%s' % out_dir)
            return

        targets = [(name, os.path.join(out_dir, '%s_%s.xml' % (stem, name)))
                   for name in wanted]
        clashes = [p for _n, p in targets if os.path.exists(p)]
        if clashes:
            reply = QMessageBox.question(
                self, '生成難度',
                '這些檔案已經存在，要覆蓋嗎？\n\n%s'
                % '\n'.join(os.path.basename(p) for p in clashes),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        self._run_difficulty_generation(model, targets)

    def generate_into_song_folder(self) -> None:
        """從目前開著的這份譜，直接生出其他難度寫進**它所在的樂曲資料夾**。

        和「生成成獨立檔案」的差別是寫出去的版面：這條路照遊戲讀得懂的結構
        寫（`<難度>/<譜名>.json` 加一份 `source/<譜名>.xml`），而且會把等級
        併回 `register.json`，所以生完直接進遊戲就看得到。

        使用者手寫的同名難度一律不覆蓋——判準是它旁邊有沒有 `source/` 那份
        XML，那是自動生成時才會留下的記號。
        """
        model = self.view.model
        if not model.notes_tree:
            QMessageBox.information(self, '生成難度', '這份譜面沒有音符。')
            return
        if not any(n.pitch is not None for n in model.notes_tree):
            QMessageBox.information(
                self, '生成難度',
                '這份譜面沒有音高，排譜器沒有東西可以依據。\n\n'
                '請先用「工具 → 以 MIDI 修復 → 從 MIDI 還原音高與表情」補回來。')
            return

        chart_path = _model_path(model)
        if not chart_path or not os.path.exists(chart_path):
            QMessageBox.information(
                self, '生成難度',
                '請先把這份譜面存檔——要有檔案路徑才知道它屬於哪一首曲子。')
            return

        from .difficulty_dialog import SongFolderDialog
        from .song_folder import find_song_folder

        # 自動偵測只是**預設值**：剛從 MIDI 轉好的譜還在暫存目錄裡，不屬於
        # 任何曲目，那時候要由使用者自己指定寫到哪一首。
        detected = find_song_folder(chart_path) or ''
        dlg = SongFolderDialog(self, chart_path=chart_path, song_dir=detected)
        if dlg.exec_() != QDialog.Accepted:
            return
        plan_obj = dlg.plan()
        picked = dlg.chosen()
        if plan_obj is None or not picked:
            return
        plan_obj.wanted = picked

        targets = [(key, plan_obj.paths_for(key)[0]) for key in plan_obj.wanted]
        self._run_difficulty_generation(model, targets, plan_obj)

    def fill_library_difficulties(self) -> None:
        """掃整個曲庫，把每一首缺少的難度一次補齊。

        一首一首開檔案生太慢了——曲庫有八十幾首。這條路只問一次「曲庫在
        哪」，其餘照每首自己的 register 決定來源與要補的難度。
        """
        from .difficulty_dialog import LibraryFillDialog

        root = ''
        here = _model_path(self.view.model)
        if here:
            # 預設帶入目前這份譜所在的曲庫（<曲庫>/<曲名>/…）
            from .song_folder import find_song_folder
            song_dir = find_song_folder(here)
            if song_dir:
                root = os.path.dirname(song_dir)

        dlg = LibraryFillDialog(self, root=root)
        if dlg.exec_() != QDialog.Accepted:
            return
        rows = dlg.ready_rows()
        if not rows:
            return
        self._run_library_fill(rows)

    def _run_library_fill(self, rows) -> None:
        """背景跑整庫補齊。實作在 library_tools，曲庫管理用的是同一份。"""
        from .library_tools import run_fill
        run_fill(self, rows)

    def _run_difficulty_generation(self, model, targets, plan_obj=None) -> None:
        """在背景執行緒跑生成，主執行緒只顯示進度。

        extreme 的排譜在鬼火那種密度要跑 45 秒左右，三個加起來一分半——
        不開執行緒的話整個視窗會沒有回應，看起來像當掉。
        """
        from PyQt5.QtCore import QThread, pyqtSignal as _sig
        from PyQt5.QtWidgets import QProgressDialog
        from .difficulty import generate
        from .song_folder import commit, write_difficulty

        # 每個難度都從**來源檔**重新讀一份：生成是就地改的，共用同一個 model
        # 的話第二個難度會拿到第一個的結果（音符已經被隱藏過一輪了）。
        source_path = _model_path(model)
        if not source_path or not os.path.exists(source_path):
            # 還沒存過檔的譜面沒有來源檔可以重讀，先落地到暫存檔。
            import tempfile
            # 用 JSON 落地：XML 是 PAN 相容格式，踏板、強弱、Soft／Staccato 都存不下
            handle, source_path = tempfile.mkstemp(suffix='.json')
            os.close(handle)
            saved = (model.current_file, model.dirty, model.file_format, model.pan_xml)
            try:
                model.save_json(source_path)
            finally:
                (model.current_file, model.dirty,
                 model.file_format, model.pan_xml) = saved

        class _Worker(QThread):
            step = _sig(str)
            done = _sig(list, str)

            def run(self):
                rows, records, error = [], [], ''
                try:
                    for name, path in targets:
                        self.step.emit(name)
                        copy = NoteModel()
                        if source_path.lower().endswith('.json'):
                            copy.load_json(source_path)
                        else:
                            copy.load_xml(source_path)
                        result = generate(copy, name)
                        if plan_obj is None:
                            copy.save_xml(path)
                        else:
                            # 樂曲資料夾版面：json 給遊戲、source/xml 留著給
                            # 制譜器回頭改，順便當「這是生成的」的記號。
                            records.append(
                                write_difficulty(plan_obj, name, copy, result))
                        rows.append((result, path))
                    if plan_obj is not None and records:
                        commit(plan_obj, records)
                except Exception as e:                  # noqa: BLE001
                    logging.exception('difficulty generation failed')
                    error = str(e)
                self.done.emit(rows, error)

        progress = QProgressDialog('正在生成難度…', '', 0, len(targets), self)
        # modal：底下是一個 processEvents() 的迴圈，不擋住輸入的話，使用者可以
        # 在生成跑到一半時再按一次同一個選單，兩條執行緒會對同一批檔案各寫各的。
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle('生成難度')
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        state = {'rows': [], 'error': ''}
        worker = _Worker()

        def on_step(name):
            progress.setLabelText('正在生成 %s…（排譜要跑幾十秒）' % name.upper())

        def on_done(rows, error):
            state['rows'], state['error'] = rows, error

        worker.step.connect(on_step)
        worker.done.connect(on_done)
        worker.start()
        while not worker.isFinished():
            QApplication.processEvents()
            worker.wait(50)
        worker.wait()
        progress.setValue(len(targets))
        progress.close()

        if state['error']:
            QMessageBox.critical(self, '生成難度', state['error'])
            return
        QMessageBox.information(self, '生成難度', _difficulty_report(state['rows']))

    def generate_pedal_dialog(self) -> None:
        """替沒有踏板的譜面依和聲生成一份。

        用的是和批次工具 `generate_pedal.py` **同一個函式**，不是另外寫一份 ——
        兩邊走味的話，編輯器裡聽到的和批次跑出來的就會不一樣。

        判準：拍子決定「什麼時候可以換」，和聲決定「這一拍要不要換」，再加一條
        「長音橫跨的點不換」。全部量自 83 份有真人 CC64 的譜面。
        """
        model = self.view.model
        if not model.notes_tree:
            QMessageBox.information(self, '生成踏板', '這份譜面沒有音符。')
            return
        if not any(n.pitch is not None for n in model.notes_tree):
            QMessageBox.information(
                self, '生成踏板',
                '這份譜面沒有音高，無法判斷和聲。\n\n'
                '請先用「工具 → 以 MIDI 修復 → 從 MIDI 還原音高與表情」把音高補回來。')
            return

        existing = len(model.pedal_spans)
        if existing:
            reply = QMessageBox.question(
                self, '生成踏板',
                f'這份譜面已經有 {existing} 段踏板。\n\n'
                '生成會整份取代它。如果那是從來源 MIDI 的 CC64 還原來的，'
                '取代等於把真人的踩法換成猜的。要繼續嗎？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        try:
            from generate_pedal import change_marks, generate_pedal_spans, peak_voices
        except ImportError:
            # 打包成 exe 之後 generate_pedal.py 不在 sys.path 上。
            import os
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            from generate_pedal import change_marks, generate_pedal_spans, peak_voices

        try:
            # 候選點是小節線；有些譜面的 beat_timings 是四分音符，直接拿來用會換得四倍快。
            spans = generate_pedal_spans(model.notes_tree, change_marks(model))
            if not spans:
                QMessageBox.information(self, '生成踏板', '算不出任何踏板段落。')
                return

            model.push_history()
            model.pedal_spans = [[float(a), float(b)] for a, b in spans]
            # 生成的踏板要標記出來：它是猜的，遊戲端的 hardcore 模式不該拿它當標準答案。
            model.json_meta['pedal_origin'] = 'auto'
            model.dirty = True
            self.view.update()
            self.view.note_edited.emit()
            self._refresh_title()

            end = max(int(n.end) for n in model.notes_tree)
            cover = sum(b - a for a, b in spans) / max(1, end)
            lengths = sorted(b - a for a, b in spans)
            QMessageBox.information(
                self, '生成踏板',
                f'生成 {len(spans)} 段踏板。\n\n'
                f'覆蓋 {100 * cover:.0f}%　段長中位 {lengths[len(lengths) // 2]:.0f}ms\n'
                f'同時發聲尖峰 {peak_voices(model.notes_tree, spans)}（遊戲的 voice pool 是 160）\n\n'
                '這是依和聲猜出來的，不是真人的踩法。')
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def transfer_from_chart_dialog(self) -> None:
        """從同一首歌的另一份譜面，照**序位**把音高／力度／踏板搬過來。

        給「時間軸被拉伸過的變體」用。Recollect Lines 的 `_ele` 版和本體是同一份
        譜面，但逐點時間差從 0 到 2559ms 都有、比值在 1.013~1.246 之間跑——那是
        伸縮不是平移，所以「以 MIDI 重建修復」那條靠時間配對的路救不了它（最佳
        吻合只有 5%）。序位配對完全不受影響：第 i 組對第 i 組。

        用的是批次工具同一個 `transfer_from_sibling()`，不是另外寫一份。
        """
        model = self.view.model
        if not model.notes_tree:
            QMessageBox.information(self, '搬表情', '這份譜面沒有音符。')
            return

        path, _ = QFileDialog.getOpenFileName(
            self, '選擇來源譜面（同一首歌的另一份）', '',
            'Nostalgia 譜面 (*.json *.xml)')
        if not path:
            return

        try:
            import os
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            from batch_restore_expression import onset_groups, transfer_from_sibling
        except ImportError as exc:
            QMessageBox.critical(self, '搬表情', '載入批次工具失敗：%s' % exc)
            return

        try:
            source = NoteModel()
            if path.lower().endswith('.json'):
                source.load_json(path)
            else:
                source.load_xml(path)
            if not source.notes_tree:
                QMessageBox.information(self, '搬表情', '來源譜面沒有音符。')
                return

            src_shape = [len(g) for g in onset_groups(source.notes_tree)]
            dst_shape = [len(g) for g in onset_groups(model.notes_tree)]
            if src_shape != dst_shape:
                # 前提很嚴是刻意的：結構不同就不是同一份譜面，硬搬會把音高配到
                # 錯的音符上，而那種錯誤在畫面上看不出來。
                QMessageBox.warning(
                    self, '搬表情',
                    '結構對不上，不能搬。'
                    + chr(10) + chr(10)
                    + '來源 %d 個起音組 / %d 顆音符' % (len(src_shape), len(source.notes_tree))
                    + chr(10)
                    + '目前 %d 個起音組 / %d 顆音符' % (len(dst_shape), len(model.notes_tree))
                    + chr(10) + chr(10)
                    + '序位配對要求「每一組的音符數都相同」。'
                      '兩份只是同一首歌的不同難度時本來就對不上——'
                      '那種情況請用「以 MIDI 修復」。')
                return

            if not any(n.pitch is not None for n in source.notes_tree):
                QMessageBox.information(self, '搬表情', '來源譜面本身沒有音高，搬不出東西。')
                return

            model.push_history()
            stats = transfer_from_sibling(model, source)
            model.dirty = True
            self.view.update()
            self.view.note_edited.emit()
            self._refresh_title()

            lines = [
                '從 %s 搬過來：' % os.path.basename(path),
                '',
                '音高 %d / %d 顆' % (stats['pitch'], len(model.notes_tree)),
                '力度 %d 顆' % stats['velocity'],
                '踏板 %d → %d 段' % (stats['pedal_before'], stats['pedal_after']),
            ]
            if stats['lane_mismatch']:
                lines.append('')
                lines.append('其中 %d / %d 組的鍵道排列和來源不同，'
                             '那幾組的音高分配可能有偏差。'
                             % (stats['lane_mismatch'], stats['groups']))
            QMessageBox.information(self, '搬表情', chr(10).join(lines))
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def clear_pedal(self) -> None:
        """把踏板整份清掉。生成錯了要能一鍵回到沒有踏板的狀態。"""
        model = self.view.model
        if not model.pedal_spans:
            QMessageBox.information(self, '清除踏板', '這份譜面本來就沒有踏板。')
            return
        count = len(model.pedal_spans)
        reply = QMessageBox.question(
            self, '清除踏板', f'要清掉 {count} 段踏板嗎？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        model.push_history()
        model.pedal_spans = []
        model.json_meta.pop('pedal_origin', None)
        model.dirty = True
        self.view.update()
        self.view.note_edited.emit()
        self._refresh_title()

    def _first_unassigned_note(self):
        """譜面上最早的一顆無主音（`hand=2`），沒有就回 None。"""
        model = getattr(self.view, 'model', None)
        if model is None:
            return None
        unassigned = [n for n in model.notes_tree
                      if int(getattr(n, 'hand', 0)) == 2]
        if not unassigned:
            return None
        return min(unassigned, key=lambda n: (int(n.start), int(n.min_key)))

    def _block_on_unassigned(self) -> bool:
        """有無主音就擋下存檔／匯出，並把畫面跳到最早的那一顆。

        無主音（`hand=2`）是「還沒決定用哪隻手」的中間狀態——疊加匯入 MIDI
        會產生它。帶著這種音符存檔等於把未完成的譜交出去，所以直接擋掉，
        並且**跳到最早的那一顆**、選起來，讓人知道要處理哪裡。

        回傳 True 表示已經擋下來了，呼叫端要直接 return。
        """
        note = self._first_unassigned_note()
        if note is None:
            return False
        total = sum(1 for n in self.view.model.notes_tree
                    if int(getattr(n, 'hand', 0)) == 2)
        try:
            self.view.scroll_to_ms(float(note.start))
            self.view.selected.clear()
            self.view.selected.add(note)
            self.view.update()
            self.view.selection_changed.emit(len(self.view.selected))
        except Exception:            # noqa: BLE001
            pass
        QMessageBox.warning(
            self, t('dlg_unassigned_title'),
            t('dlg_unassigned_msg', total, int(note.start)))
        return True

    def _confirm_pan_conversion(self, path: str) -> bool:
        """存成 XML 之前：列出 PAN 沒有、會被換掉或拿掉的東西，同意才轉換。

        轉換會真的改動畫面上的譜（可以復原），這樣存完看到的就是檔案裡的樣子。
        """
        m = self.view.model
        preview = m.pan_conversion_preview()
        switching = not getattr(m, 'pan_xml', False)
        if not preview and not (switching and m.time_sig_changes):
            return True
        lines = ['存成 XML（PAN 相容格式）時，PAN 沒有的東西會被換掉或拿掉：', '']
        lines += ['・%s：%d' % (label, count) for label, count in preview.items()]
        if switching and m.time_sig_changes:
            lines.append('・拍號變化（PAN 沒有拍號；拍子照樣寫入，重新開 XML 時小節以 4/4 顯示）')
        source = m.current_file or ''
        if switching and source and os.path.abspath(source) != os.path.abspath(path):
            lines += ['', '原本的 %s 不會被改動。之後在 XML 上做的修改不會回到它。'
                      % os.path.basename(source)]
        lines += ['', '要轉換並存檔嗎？（轉換可以用「復原」還原）']
        reply = QMessageBox.question(self, '轉成 PAN 相容 XML', '\n'.join(lines),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return False
        if preview:
            m.push_history()
            m.convert_to_pan()
            for pane in self._panes:
                pane.update()
            self.view.note_edited.emit()
        return True

    def _do_save(self, path: str, use_midi_restore: bool = False) -> None:
        if self._block_on_unassigned():
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in ('.json', '.mid', '.midi') and not use_midi_restore:
            if not self._confirm_pan_conversion(path):
                return
        validation = None
        try:
            m = self.view.model
            previous_source = m.current_file
            if ext == '.json':
                m.save_json(path)
                actual = path
            elif ext in ('.mid', '.midi'):
                m.save_midi(path)
                actual = path
            else:
                m.save_xml(path, use_midi_restore=use_midi_restore)
                actual = path
                from .pan_format import validate
                validation = validate(path)
            self._notify_game_if_in_library(actual)
            oplog.flush(m)
            oplog.mark('save', path=os.path.basename(actual),
                       profile=oplog_profile(m))
            # 正式存進去了，備份就沒用了。另存新檔時舊路徑（或新譜的工作階段）
            # 那一份也一起清掉，不然下次開舊檔會被問要不要還原一份過時的備份。
            self._discard_autosave(previous_source, actual)
            self._autosave_pending = False
            self._apply_format_mode()
            if validation is not None and not validation.ok:
                QMessageBox.warning(
                    self, 'XML 已存檔，但沒通過 PAN 檢查',
                    '檔案已經寫出：%s\n\n照 PAN 的讀檔規則檢查，還有這些問題：\n\n%s'
                    % (actual, validation.summary()))
            else:
                msg = t('dlg_save_ok_msg', actual)
                if validation is not None:
                    msg += '\n\n已照 PAN 讀檔規則檢查，沒有問題。'
                QMessageBox.information(self, t('dlg_save_ok_title'), msg)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_save_fail_title'), t('dlg_save_fail_msg', e))

    # ==================================================================
    # 音訊操作
    # ==================================================================

    def load_wav(self) -> None:
        if getattr(self, '_dual_audio_enabled', False):
            # sequential dialogs: require primary, optional secondary
            p0, _ = QFileDialog.getOpenFileName(self, t('dlg_load_wav_title'), '', t('dlg_wav_filter'))
            if not p0:
                return
            ask = QMessageBox.question(self, t('dlg_load_wav_title'), '是否要選擇第二個音源？',
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            p1 = None
            if ask == QMessageBox.Yes:
                p1, _ = QFileDialog.getOpenFileName(self, '選擇第二個音源', '', t('dlg_wav_filter'))
            sel = [p0]
            if p1:
                sel.append(p1)
            ok = self.audio.load_wavs(sel)
            if ok:
                if len(sel) == 1:
                    name = os.path.basename(sel[0])
                else:
                    name = f"{os.path.basename(sel[0])} + {os.path.basename(sel[1])}"
                self._refresh_vol_rows()   # 有音樂了，音量列解除灰色
                self._lbl_audio.setText(t('status_audio_loaded', name))
            else:
                QMessageBox.warning(self, t('dlg_warn'), t('dlg_wav_fail_msg', sel[0] if sel else ''))
            return

        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_load_wav_title'), '', t('dlg_wav_filter'),
        )
        if not path:
            return
        if self.audio.load_wav(path):
            self._refresh_vol_rows()   # 有音樂了，音量列解除灰色
            self._lbl_audio.setText(t('status_audio_loaded', os.path.basename(path)))
        else:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_wav_fail_msg', path))

    def _on_toggle_dual_audio(self, checked: bool) -> None:
        self._dual_audio_enabled = bool(checked)
        # update small status label for immediate feedback
        try:
            if getattr(self, '_lbl_audio_mode', None) is not None:
                if self._dual_audio_enabled:
                    self._lbl_audio_mode.setText('雙音源：開')
                else:
                    self._lbl_audio_mode.setText('雙音源：關')
        except Exception:
            pass

    def _audio_total_ms(self) -> float:
        """取得音訊檔總長度（ms）；未載入時回傳曲譜末尾時間。"""
        if self.audio.is_loaded() and self.audio.audio_rate > 0 and self.audio.audio_frames > 0:
            return self.audio.audio_frames / self.audio.audio_rate * 1000.0
        return max(self.view.model.music_end_ms, 600_000.0)

    def play_full(self) -> None:
        """從頭播放整首（0 ms 到音訊末尾）；先把視圖滾到起點再播。"""
        # 先把視圖滾到 0ms 位置，讓使用者看到起點
        self._set_judge_line_all(0.0)
        self.view.update()
        self._play_range(0.0, self._audio_total_ms())

    def play_from_window(self) -> None:
        """從目前視窗底部（最低可見時間）播到曲末，不自動停止。"""
        ws_ms, _ = self.view._window_ms()
        self._play_range(ws_ms, self._audio_total_ms())

    def play_window(self) -> None:
        self.play_from_window()

    def play_window_hit(self) -> None:
        self.play_from_window()

    def play_selection(self) -> None:
        self.view._emit_play_selection()

    def play_selection_hit(self) -> None:
        self.view._emit_play_selection()

    def _play_range(self, start_ms: float, end_ms: float) -> None:
        """播放一段範圍。畫面一律跟著捲——判定線就是鍵盤上緣，那條線只有在
        視窗跟著走的時候才代表當下時刻。"""
        # 沒有 WAV 也可以播——鋼琴音是另外合成的，不靠音訊檔。
        if not self.audio.is_loaded():
            # 沒有音源是正常的（純鋼琴曲、還沒配樂），不再每次跳窗問要不要載入：
            # 直接用鋼琴合成播。打擊音靠 PCM overlay 疊在音樂上，沒有底層音訊
            # 就疊不上去、會退回 Tap.wav，所以走合成那條路（自己 render 自己播）。
            self.play_midi_range(start_ms, end_ms)
            return
        # 播放前重建唯一 startTime 清單（含蓋 up/down 移動後的變更）
        self._rebuild_hit_times()
        self._play_start_ms = start_ms
        self._play_end_ms   = end_ms
        self._hit_last_ms   = start_ms
        # 指標跳到第一個 >= start_ms 的位置
        import bisect
        self._hit_ptr = bisect.bisect_left(self._hit_times, int(start_ms))
        self._set_pause_text('tb_pause')
        # 播放前先把視圖跳到起始位置，確保畫面在音訊啟動前已呈現正確位置
        self._set_judge_line_all(start_ms)
        # Apply playback offset: audio backend plays at audio-time, but user
        # offset is signed (positive=advance, negative=delay). To simulate
        # the offset without modifying the file, start the audio at
        # start_ms + offset and map audio current_ms back to chart time
        # when updating UI and hit timings.
        adj_start = start_ms + float(self._playback_offset_ms)
        adj_end = end_ms + float(self._playback_offset_ms)
        self._midi_preview_active = self._prepare_midi_preview(start_ms, end_ms)
        if self.audio.is_loaded():
            self._silent_play = None
            self.audio.play(adj_start, adj_end)
        else:
            # 只播 MIDI：沒有音訊後端可以問時間，改用牆上時鐘推進判定線
            from time import perf_counter
            self._silent_play = (perf_counter(), adj_start, adj_end)
        self._is_playing = True
        self._judge_timer.start()

    # ── 播放 MIDI（不需要載入 WAV）────────────────────────────────────

    def play_midi_range(
        self,
        start_ms: float,
        end_ms: float,
        note_ids: Optional[List[int]] = None,
    ) -> None:
        """把範圍內的音符算成鋼琴音直接播出來。

        `note_ids` 有給就只播那些音符（display idx），否則播起點落在
        [start_ms, end_ms) 的所有音符。時值與力度都照音符本身的資料，
        XML 沒有 velocity 時退回同 track 的平均值。
        """
        model = self.view.model
        start_ms = float(start_ms)
        end_ms = float(end_ms)
        if end_ms <= start_ms:
            self.statusBar().showMessage(t('midi_play_empty_range'), 4000)
            return

        source = None
        if note_ids:
            wanted = set(int(i) for i in note_ids)
            source = [n for n in model.notes if n.idx in wanted]
            if not source:
                source = [n for n in model.notes_tree if n.idx in wanted]

        try:
            notes = build_chart_midi_notes(
                model,
                source_notes=source,
                range_ms=None if source is not None else (start_ms, end_ms),
                enable_right=self._hit_enable_right,
                enable_left=self._hit_enable_left,
                enable_beat=False,
                note_length_ms=None,          # 保留音符原本的時值
                expressive=True,              # 力度 / trill / staccato 等表情資訊
                real_pedal=True,              # 踏板走 CC64，不是把音符拉長
            )
        except Exception as exc:
            logging.exception('build MIDI notes failed')
            QMessageBox.critical(self, t('midi_play_fail_title'), str(exc))
            return

        if not notes:
            self.statusBar().showMessage(t('midi_play_no_notes'), 4000)
            return

        # 只選了音符時，播放區間就依那些音符的頭尾
        play_start = min(start_ms, min(n.start_ms for n in notes))
        play_end   = max(end_ms,   max(n.end_ms   for n in notes))
        play_end  += 600.0        # 留一點尾巴讓最後一顆音自然收掉

        rate = self.audio.audio_rate if self.audio.audio_rate > 0 else 44100

        try:
            if (self._midi_preview_synth is None
                    or self._midi_preview_synth.sample_rate != rate):
                if self._midi_preview_synth is not None:
                    self._midi_preview_synth.close()
                self._midi_preview_synth = MidiPreviewSynth(rate)

            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                pcm = self._midi_preview_synth.render(
                    notes, play_start, play_end,
                    pedal_spans=pedal_spans_in_range(model, play_start, play_end),
                )
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            logging.exception('MIDI range rendering failed')
            QMessageBox.critical(self, t('midi_play_fail_title'), str(exc))
            return

        self.stop_audio()
        ok = self.audio.play_pcm(
            pcm, rate, 2, 2, play_start, play_end, volume=self._hit_vol,
        )
        if not ok:
            QMessageBox.critical(self, t('midi_play_fail_title'),
                                 t('midi_play_no_backend'))
            return

        self._play_start_ms = play_start
        self._play_end_ms   = play_end
        self._is_playing    = True
        # 借用這個旗標壓掉 Tap.wav 打擊聲，避免和鋼琴音疊在一起
        self._midi_preview_active = True
        self._set_judge_line_all(play_start)
        self._set_pause_text('tb_pause')
        self._judge_timer.start()
        self.statusBar().showMessage(
            t('midi_play_status', len(notes), int(play_start), int(play_end)), 5000
        )

    def _on_play_midi_requested(self, start_ms: float, end_ms: float, note_ids) -> None:
        ids = list(note_ids) if note_ids else None
        self.play_midi_range(start_ms, end_ms, ids)

    def _toggle_pause_resume(self) -> None:
        if self.audio.is_paused():
            self.resume_audio()
        else:
            self.pause_audio()

    #: 暫停後畫面被捲動超過這麼多毫秒，就算「去看了別的地方」。只是用來吃掉
    #: 浮點誤差——真的捲動一格滾輪都是幾十毫秒起跳。
    _RESUME_FROM_VIEW_TOLERANCE_MS = 1.0

    def pause_audio(self) -> None:
        if self._is_playing:
            self.audio.pause()
            self._judge_timer.stop()
            self._set_pause_text('tb_resume')
            self._autosave_if_pending()
            # 記下暫停當下每一格停在哪一刻，繼續時才分得出使用者有沒有捲走。
            self._paused_view_ms = {
                id(pane): pane.judge_line_view_ms()
                for pane in self._visible_panes()
            }

    def _viewed_elsewhere_ms(self) -> Optional[float]:
        """暫停後使用者若捲去看了別的地方，回傳判定線現在對到的那一刻。

        沒動過就回傳 None，照原本的暫停點續播。分割畫面時哪一格動了就以那一格
        為準（作用中的那一格優先）。
        """
        before = getattr(self, '_paused_view_ms', None) or {}
        panes = [self.view] + [p for p in self._visible_panes() if p is not self.view]
        for pane in panes:
            if id(pane) not in before:
                continue
            now = pane.judge_line_view_ms()
            if abs(now - before[id(pane)]) > self._RESUME_FROM_VIEW_TOLERANCE_MS:
                return max(0.0, now)
        return None

    def resume_audio(self) -> None:
        viewed = (self._viewed_elsewhere_ms()
                  if settings.get('resume_from_view', True) else None)
        self._paused_view_ms = None
        if viewed is not None:
            self._resume_from(viewed)
            return
        # audio.resume() 內部呼叫 play()→stop()，會觸發 playback_stopped 信號
        # 導致 _is_playing=False，需要在 resume 之後重新設為 True
        self.audio.resume()
        self._is_playing = True
        self._judge_timer.start()
        self._set_pause_text('tb_pause')

    def _resume_from(self, start_ms: float) -> None:
        """從畫面上正在看的那一刻繼續播，而不是回到暫停點。

        直接重新起播而不是改暫停點：打擊音指標、鋼琴預覽的疊加音都是照起播
        範圍準備的，捲到範圍外（例如捲回開頭之前）時沿用舊的會缺聲音。

        原本是播一段範圍（例如選取的音符）時，看的地方還在範圍內就播到範圍結
        束；捲到範圍之後，就一路播到曲末。
        """
        end_ms = float(self._play_end_ms)
        if start_ms >= end_ms - 1.0:
            end_ms = self._audio_total_ms()
        if self.audio.is_pcm_playback():
            # 只播 MIDI 的那種：重新合成這一段鋼琴音
            self.play_midi_range(start_ms, end_ms)
        else:
            self._play_range(start_ms, end_ms)

    def stop_audio(self) -> None:
        self.audio.stop()
        self._is_playing = False
        self._judge_timer.stop()
        self._set_judge_line_all(None)
        self._set_pause_text('tb_pause')

    def restart_audio(self) -> None:
        self._play_range(self._play_start_ms, self._play_end_ms)

    def _on_judge_tick(self) -> None:
        if not self._is_playing:
            self._judge_timer.stop()
            return
        try:
            silent = getattr(self, '_silent_play', None)
            if silent is not None:
                from time import perf_counter
                t0, s0, s1 = silent
                pos = s0 + (perf_counter() - t0) * 1000.0
                if pos >= s1:
                    pos = None
            else:
                pos = self.audio.current_ms()
            if pos is None:
                self._judge_timer.stop()
                self._is_playing = False
                self._set_judge_line_all(None)
                return
            # Map audio time back to chart time by subtracting playback offset.
            # audio.current_ms() reports audio-file timeline; UI/hit times are
            # chart timeline. Use chart_pos for comparisons and view updates.
            chart_pos = pos - float(self._playback_offset_ms)
            # 打擊聲：用指標掃描預計算的 hit_times，每個唯一 startTime 只響一次
            if (
                self._hit_sound_persistent
                and not self._midi_preview_active
                and (self._hit_wav_tmp_path or self._hit_sound_bytes)
            ):
                while (self._hit_ptr < len(self._hit_times)
                       and self._hit_times[self._hit_ptr] < chart_pos):
                    self._play_hit_sound()
                    self._hit_ptr += 1
            self._hit_last_ms = chart_pos
            # 判定線固定在鍵盤上緣，所以視窗一律跟著捲
            self._set_judge_line_all(chart_pos)
        except Exception:
            self._judge_timer.stop()
            self._is_playing = False

    def _on_music_vol_changed(self, val: int) -> None:
        if not self._vol_enabled.get('music', True):
            return
        self.audio.set_volume(val / 100.0)

    def _on_music2_vol_changed(self, val: int) -> None:
        if not self._vol_enabled.get('music2', True):
            return
        # set secondary audio volume
        try:
            if hasattr(self.audio, 'set_volume2'):
                self.audio.set_volume2(val / 100.0)
        except Exception:
            pass

    def _on_hit_vol_changed(self, val: int) -> None:
        self._hit_vol = val / 100.0
        self.audio.set_preview_volume(self._hit_vol)
        self._rebuild_hit_wav()

    def _on_hit_toggle(self, checked: bool) -> None:
        self._hit_sound_persistent = bool(checked)
        acts = [getattr(self, '_act_hit', None)]
        acts += [tbs.hit_act for tbs in self._toolbars]
        for action in acts:
            if action is not None and action.isChecked() != bool(checked):
                previous = action.blockSignals(True)
                action.setChecked(bool(checked))
                action.blockSignals(previous)
        # 開關都對齊之後再畫喇叭，兩條工具列的喇叭才會一起換
        self._refresh_vol_rows()

        if not checked:
            self._midi_preview_active = False
            self.audio.clear_preview_overlay(restart=self._is_playing)
            return

        if self._is_playing:
            pos = self.audio.current_ms()
            if pos is not None:
                import bisect
                chart_pos = pos - float(self._playback_offset_ms)
                self._hit_ptr = bisect.bisect_left(self._hit_times, chart_pos)
                self._midi_preview_active = self._prepare_midi_preview(
                    chart_pos, self._play_end_ms
                )
                if self._midi_preview_active:
                    self.audio.refresh_output()
        # 播放中途才開啟時：將 ptr 推進到當前位置，跳過已過去的音符，
        # 避免下一個 tick 瞬間重播所有歷史音符互相覆蓋而聽不到聲音。
    def _on_hit_enable_toggle(self, kind: str, checked: bool) -> None:
        """Handler for right/left/beat enable toggles from settings menu."""
        if kind == 'right':
            self._hit_enable_right = bool(checked)
        elif kind == 'left':
            self._hit_enable_left = bool(checked)
        elif kind == 'beat':
            self._hit_enable_beat = bool(checked)
        # persist into model json_meta if a model is loaded
        try:
            m = self.view.model
            jm = getattr(m, 'json_meta', {}) or {}
            jm['hit_enable_right'] = bool(self._hit_enable_right)
            jm['hit_enable_left'] = bool(self._hit_enable_left)
            jm['hit_enable_beat'] = bool(self._hit_enable_beat)
            m.json_meta = jm
        except Exception:
            pass
        # rebuild hit times so change takes effect immediately
        try:
            self._rebuild_hit_times()
        except Exception:
            pass
        if self._is_playing and self._hit_sound_persistent:
            self._on_hit_toggle(True)

    def _on_time_uniform_toggle(self, checked: bool) -> None:
        # 時間均分也是「非音高」的檢視，同樣要先確認譜面排過
        if checked and not self._require_arranged_for_view():
            self._refresh_view_mode_action()
            return
        self.view.toggle_time_uniform(checked)
        # 動態更新按鈕文字
        self._refresh_view_mode_action()

    def _refresh_view_mode_action(self) -> None:
        """檢視模式按鈕的文字：每條工具列各自顯示「自己那一格」的模式。"""
        for tbs in self._toolbars:
            act = tbs.view_mode_act
            if act is None:
                continue
            mode = getattr(self._pane_of(tbs), 'view_mode', 'measure')
            self._set_labeled_text(act, t({
                'measure': 'tb_time_uniform_measure',
                'time': 'tb_time_uniform_time',
                'pitch': 'tb_time_uniform_pitch',
            }.get(mode, 'tb_time_uniform_measure')), t('tb_time_uniform_tip'))

    def _set_pause_text(self, key: str) -> None:
        """暫停／繼續按鈕的文字（兩條工具列都要跟著換）。"""
        for tbs in self._toolbars:
            if tbs.pause_act is not None:
                self._set_labeled_text(tbs.pause_act, t(key))

    def _offset_label_text(self) -> str:
        return (t('status_offset_none') if self._playback_offset_ms == 0
                else t('status_offset', self._playback_offset_ms))

    def _refresh_offset_label(self) -> None:
        text = self._offset_label_text()
        for tbs in self._toolbars:
            if tbs.offset_label is not None:
                tbs.offset_label.setText(text)

    def _on_note_placed(self, note) -> None:
        """放下音符時用內建鋼琴音彈出那一顆，讓你當場聽得出音高對不對。

        播放中一定要有：主播放是「一次把整段 render 成 PCM」，播到一半新增的
        音符不在那段音訊裡，沒有這個就完全沒有回饋。停著編輯時也響，是偏好
        設定「放置時發聲」控制的（預設開）。

        走 `play_oneshot_pcm`，不會打斷正在播的音訊。
        """
        if note is None or note.pitch is None:
            return
        if not settings.get('place_note_sound', True):
            return
        try:
            rate = self.audio.audio_rate if self.audio.audio_rate > 0 else 44100
            if (self._midi_preview_synth is None
                    or self._midi_preview_synth.sample_rate != rate):
                if self._midi_preview_synth is not None:
                    self._midi_preview_synth.close()
                self._midi_preview_synth = MidiPreviewSynth(rate)
            span = max(140.0, float(note.end) - float(note.start))
            pcm = self._midi_preview_synth.render(
                [MidiPreviewNote(0.0, span, int(note.pitch),
                                 int(note.velocity or 96), 0)],
                0.0, span,
            )
            self.audio.play_oneshot_pcm(pcm, rate)
        except Exception:                      # noqa: BLE001
            logging.debug('live note preview failed', exc_info=True)

    def _on_arrange_required(self) -> None:
        """使用者想切到別的檢視、但譜面還沒排過——問他要不要現在轉。

        擋下來的地方在 ChartView（切換入口不只一個），但那裡不該跳對話框，
        所以改成發訊號到這裡統一詢問；答應就當場排譜並切過去。
        """
        if getattr(self, '_arrange_prompt_open', False):
            return                      # 一次切換可能觸發多個格子，只問一次
        self._arrange_prompt_open = True
        try:
            if self._require_arranged_for_view():
                self._cycle_view_mode()
        finally:
            self._arrange_prompt_open = False

    def _require_arranged_for_view(self) -> bool:
        """切到音高以外的檢視前，先確認譜面已經排過。

        選了「不轉換」的 MIDI 只有音高檢視有意義——音符還沒有譜面上的鍵道
        位置，用小節/時間檢視看到的是暫時排開的假位置。所以這裡攔下來問，
        願意轉就當場排譜，不轉就留在音高模式。
        """
        # getattr(self, 'model') 永遠是 None——MainWindow 根本沒有這個屬性，
        # 所以這個守門以前是**永遠放行**：對話框從來沒跳出來過，切換檢視
        # 只是默默地彈回音高模式，看起來像沒反應。
        model = self.view.model
        if not getattr(model, 'midi_unarranged', False):
            return True
        # 這個框是切換檢視時自動跳的（快捷鍵一按就會碰到），而排譜會重寫整份
        # 譜面，所以維持「要按確定才會動」：對話框的取消＝留在音高模式。
        arrange, options = self.ask_arrange_options(intro=t('dlg_midi_need_arrange'))
        if not arrange:
            return False
        return self._arrange_now(options)

    def ask_arrange_options(self, intro: str = '', allow_skip: bool = False):
        """跳出「MIDI 轉譜」對話框。回傳 (要不要轉, 選項)。

        `allow_skip=True` 時多一顆「先不轉譜」，按了就回 (False, None)——匯入
        MIDI 用得到，切換檢視時跳的那個框則直接取消就好。
        """
        from .arrange_dialog import ArrangeDialog

        dlg = ArrangeDialog(self, intro=intro, allow_skip=allow_skip)
        if dlg.exec_() != ArrangeDialog.Accepted:
            return None, None
        if getattr(dlg, 'skipped', False):
            return False, None
        return True, dlg.options()

    def _arrange_now(self, options=None) -> bool:
        """對目前這份未排譜的 MIDI 就地執行自動排譜（含進度視窗）。

        `options` 給的話就照那份選項轉；沒給就先問（模式／鍵道範圍／重疊／自訂）。
        """
        from PyQt5.QtCore import QThread, pyqtSignal as _sig
        from PyQt5.QtWidgets import QProgressDialog, QMessageBox

        if options is None:
            arrange, options = self.ask_arrange_options(
                intro='這份 MIDI 還沒轉成譜面。要怎麼轉？')
            if not arrange:
                return False
        model = self.view.model
        # 自動排譜會重寫每一顆音符的鍵道，還會先裁掉踏板殘響造成的長音——
        # 這是整份譜面級別的改動，沒有 push_history 就**完全救不回來**。
        # 觸發它的又是切換檢視時跳出來的那個問句（快捷鍵一按就會碰到），
        # 誤觸的代價太高。
        model.push_history()

        class _Worker(QThread):
            done = _sig(bool, str)

            def run(self) -> None:
                try:
                    model.trim_pedal_sustained_holds()
                    model.arrange_with_options(options)
                    self.done.emit(True, '')
                except Exception as exc:            # noqa: BLE001
                    self.done.emit(False, str(exc))

        state = {'finished': False, 'ok': False, 'err': ''}
        worker = _Worker(self)
        worker.done.connect(
            lambda ok, err: state.update(finished=True, ok=ok, err=err)
        )
        dlg = QProgressDialog(
            t('dlg_midi_converting', os.path.basename(model.current_file or '')),
            t('dlg_cancel'), 0, 0, self,
        )
        dlg.setWindowTitle(t('dlg_midi_converting_title'))
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.show()
        worker.start()
        while not state['finished']:
            QApplication.processEvents()
            if dlg.wasCanceled():
                break
            worker.wait(30)
        dlg.close()
        worker.wait()
        if not state['ok']:
            if state['err']:
                QMessageBox.warning(self, t('dlg_warn'), state['err'])
            return False
        for pane in self._panes:
            pane.rebuild_mapper()
            pane.update()
        return True

    def _cycle_view_mode(self, checked: bool = False) -> None:
        # 音高 → 下一個模式時才需要確認；留在音高永遠是允許的
        if self.view.view_mode == 'pitch' and not self._require_arranged_for_view():
            self._refresh_view_mode_action()
            return
        self.view.cycle_view_mode()
        self._refresh_view_mode_action()

    def _rebuild_hit_times(self) -> None:
        """從目前 model 建立排序的唯一 startTime 清單（輕量）。"""
        # Build candidate times from note starts and beat timings, but only
        # include times that should produce a hit sound according to per-type
        # enable flags (right/left/beat).
        model = self.view.model
        note_starts = {}
        for n in model.notes_tree:
            note_starts.setdefault(int(n.start), []).append(n)

        beat_ms_set = set(ms for (_i, ms) in model.get_beat_entries())

        times = set()
        # notes: include time if any note at that time matches enabled hand
        for ms, notes in note_starts.items():
            play = False
            for n in notes:
                if n.hand == 0 and self._hit_enable_right:
                    play = True
                    break
                if n.hand == 1 and self._hit_enable_left:
                    play = True
                    break
            if play:
                times.add(int(ms))

        # beats (bar/beat timings): include if beat enabled
        if self._hit_enable_beat:
            for ms in beat_ms_set:
                times.add(int(ms))

        self._hit_times = sorted(times)
        self._hit_ptr = 0

    def _on_playback_stopped(self) -> None:
        self._is_playing = False
        self._judge_timer.stop()
        self._autosave_if_pending()
        try:
            self._set_judge_line_all(None)
            self._set_pause_text('tb_pause')
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 打擊聲
    # ------------------------------------------------------------------

    def _prepare_midi_preview(self, start_ms: float, end_ms: float) -> bool:
        """Render the selected chart range and install it as a PCM overlay."""
        self.audio.clear_preview_overlay()
        if not self._hit_sound_persistent or end_ms <= start_ms:
            return False
        if (
            self.audio.audio_rate <= 0
            or self.audio.audio_sampwidth != 2
            or self.audio.audio_channels not in (1, 2)
        ):
            return False

        try:
            if (
                self._midi_preview_synth is None
                or self._midi_preview_synth.sample_rate != self.audio.audio_rate
            ):
                if self._midi_preview_synth is not None:
                    self._midi_preview_synth.close()
                self._midi_preview_synth = MidiPreviewSynth(self.audio.audio_rate)

            chart_notes = build_preview_notes(
                self.view.model,
                enable_right=self._hit_enable_right,
                enable_left=self._hit_enable_left,
                enable_beat=self._hit_enable_beat,
                real_pedal=True,              # 踏板走 CC64，不是把音符拉長
            )
            offset = float(self._playback_offset_ms)
            audio_notes = [
                MidiPreviewNote(
                    note.start_ms + offset,
                    note.end_ms + offset,
                    note.pitch,
                    note.velocity,
                    note.channel,
                )
                for note in chart_notes
            ]
            audio_start = float(start_ms) + offset
            audio_end = float(end_ms) + offset

            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                pcm = self._midi_preview_synth.render(
                    audio_notes, audio_start, audio_end,
                    # 踏板也要跟著播放偏移移動，否則會踩在錯的位置
                    pedal_spans=pedal_spans_in_range(
                        self.view.model, audio_start, audio_end, offset),
                )
            finally:
                QApplication.restoreOverrideCursor()

            installed = self.audio.set_preview_overlay(
                pcm,
                audio_start,
                self.audio.audio_rate,
                self._hit_vol,
            )
            if installed:
                self._midi_preview_error_shown = False
            return installed
        except Exception as exc:
            logging.exception("MIDI preview rendering failed")
            self.audio.clear_preview_overlay()
            if not self._midi_preview_error_shown:
                self.statusBar().showMessage(
                    f"MIDI piano preview unavailable; using Tap.wav: {exc}",
                    8000,
                )
                self._midi_preview_error_shown = True
            return False

    def _load_hit_sound(self) -> None:
        import wave
        wav_path = os.path.join(os.path.dirname(__file__), 'Tap.wav')
        if not os.path.exists(wav_path):
            return
        try:
            with wave.open(wav_path, 'rb') as wf:
                self._hit_sound_channels  = wf.getnchannels()
                self._hit_sound_sampwidth = wf.getsampwidth()
                self._hit_sound_rate      = wf.getframerate()
                self._hit_sound_bytes     = wf.readframes(wf.getnframes())
        except Exception:
            return
        self._rebuild_hit_wav()

    def _rebuild_hit_wav(self) -> None:
        """以目前 _hit_vol 將 _hit_sound_bytes 套用音量後寫入暫存 WAV，供 winsound 播放。"""
        if not self._hit_sound_bytes:
            return
        import wave, tempfile, array as _arr
        pcm = self._hit_sound_bytes
        vol = self._hit_vol
        if vol < 0.999 and self._hit_sound_sampwidth == 2:
            a = _arr.array('h', pcm)
            for i in range(len(a)):
                a[i] = max(-32768, min(32767, int(a[i] * vol)))
            pcm = bytes(a)
        elif vol < 0.999 and self._hit_sound_sampwidth == 1:
            a = _arr.array('B', pcm)
            for i in range(len(a)):
                a[i] = max(0, min(255, int((a[i] - 128) * vol + 128)))
            pcm = bytes(a)
        # 清除舊暫存檔
        if self._hit_wav_tmp_path:
            try:
                os.remove(self._hit_wav_tmp_path)
            except Exception:
                pass
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            with wave.open(tmp, 'wb') as wf:
                wf.setnchannels(self._hit_sound_channels)
                wf.setsampwidth(self._hit_sound_sampwidth)
                wf.setframerate(self._hit_sound_rate)
                wf.writeframes(pcm)
            tmp.close()
            self._hit_wav_tmp_path = tmp.name
        except Exception:
            self._hit_wav_tmp_path = None

    def _play_hit_sound(self) -> None:
        # 音量已套用在 _hit_wav_tmp_path 的暫存 WAV 中。
        # Windows 走 winsound（最輕、不佔音訊裝置）；其他平台沒有 winsound，
        # 改用播放器的後端（simpleaudio 或 sounddevice），不然放置音效會無聲。
        path = self._hit_wav_tmp_path
        if not path:
            path = os.path.join(os.path.dirname(__file__), 'short-shimmering-hi-hat.wav')
        try:
            import winsound
            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return
        except Exception:
            pass
        self._play_hit_sound_pcm(path)

    def _play_hit_sound_pcm(self, path: str) -> None:
        """沒有 winsound 時的放置音效：直接丟給 simpleaudio／sounddevice。

        每次播一顆短音就開一條串流，播完自己結束；留著參照是為了不讓物件
        在播完前被回收（PortAudio 的串流被 GC 掉就沒聲音了）。
        """
        import wave as _wave

        from . import audio_player as _ap

        if not _ap._HAS_SA or _ap.sa is None:
            return
        try:
            with _wave.open(path, 'rb') as wf:
                params = wf.getparams()
                pcm = wf.readframes(params.nframes)
            obj = _ap.sa.WaveObject(pcm, num_channels=params.nchannels,
                                    bytes_per_sample=params.sampwidth,
                                    sample_rate=params.framerate)
            playing = getattr(self, '_hit_sound_objs', [])
            playing = [o for o in playing if _still_playing(o)]
            playing.append(obj.play())
            self._hit_sound_objs = playing[-8:]
        except Exception:                       # noqa: BLE001
            pass

    # ==================================================================
    # 播放偏移
    # ==================================================================

    def _show_lead_in_dialog(self) -> None:
        """開頭空白：給音訊加／減開頭靜音，可選擇音符與小節線一起移。取代播放偏移。"""
        from .audio_lead_in import apply_lead_in, describe, leading_silence_ms, split_lead_name
        from .audio_lead_in_dialog import AudioLeadInDialog

        m = self.view.model
        path = getattr(self.audio, 'audio_path', '') or ''
        loaded = bool(path) and os.path.isfile(path) and self.audio.is_loaded()
        total = split_lead_name(path)[1] if loaded else 0
        silence = 0
        if loaded:
            try:
                silence = leading_silence_ms(path)
            except Exception:                            # noqa: BLE001
                silence = 0
        dlg = AudioLeadInDialog(
            self, bpm=m.bpm if m.bpm > 0 else 120.0, audio_path=path if loaded else '',
            current_total_ms=total, leading_silence_ms=silence,
            earliest_note_ms=int(m.earliest_time_ms() or 0),
            latency_ms=self.audio.output_latency_ms())
        if dlg.exec_() != QDialog.Accepted:
            return
        self.audio.set_output_latency_ms(dlg.latency_ms())
        settings.set('audio_latency_ms', int(dlg.latency_ms()))
        delta = dlg.delta_ms()
        move = dlg.moves_notes()
        if delta == 0 or (not loaded and not move):
            return
        self.stop_audio()

        messages = []
        audio_delta = delta
        moved_notes = False
        if move:
            m.push_history()
            m.shift_all_time(delta)
            audio_delta = int(getattr(m, 'last_shift_ms', delta))
            moved_notes = audio_delta != 0
            if audio_delta != delta:
                messages.append('音符最多只能往前移 %d ms' % -audio_delta)
            if moved_notes:
                messages.append('音符與小節線 %+d ms' % audio_delta)

        if loaded and audio_delta:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                result = apply_lead_in(path, audio_delta)
                second = getattr(self.audio, 'audio2_path', None)
                second_path = apply_lead_in(second, result.applied_ms).path if second else None
            except Exception as exc:                     # noqa: BLE001
                QApplication.restoreOverrideCursor()
                if moved_notes:
                    m.undo()                             # 音訊沒改成功，音符也退回去，不要錯開
                QMessageBox.warning(self, '開頭空白', '處理音訊失敗：%s' % exc)
                return
            QApplication.restoreOverrideCursor()
            if moved_notes and result.applied_ms != audio_delta:
                # 音訊能剪的比音符移的少（音訊太短）：音符補回差額，兩邊才會對齊
                m.shift_all_time(result.applied_ms - audio_delta)
            ok = (self.audio.load_wavs([result.path, second_path]) if second_path
                  else self.audio.load_wav(result.path))
            if not ok:
                QMessageBox.warning(self, t('dlg_warn'), t('dlg_wav_fail_msg', result.path))
                return
            self._refresh_vol_rows()
            self._lbl_audio.setText(t('status_audio_loaded', os.path.basename(result.path)))
            messages.append('音訊 %+d ms（%s）' % (
                result.applied_ms, describe(result.total_ms) or '回到原檔'))
            if result.cut_sound_ms:
                messages.append('剪到 %d ms 有聲音的部分' % result.cut_sound_ms)

        if moved_notes:
            m.rebuild_display_cache()
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.note_edited.emit()
            self._rebuild_hit_times()
        self.view.update()
        if messages:
            self.statusBar().showMessage('開頭空白：' + '、'.join(messages), 8000)

    def _show_offset_dialog(self) -> None:
        """開啟播放延遲/提前設定對話框。"""
        bpm = self.view.model.bpm if self.view.model.bpm > 0 else 120.0
        dlg = PlaybackOffsetDialog(
            self,
            bpm=bpm,
            current_ms=abs(self._playback_offset_ms),
            is_advance=self._playback_offset_advance,
            latency_ms=self.audio.output_latency_ms(),
        )
        if dlg.exec_() == PlaybackOffsetDialog.Accepted:
            self._playback_offset_ms = dlg.offset_ms()
            self._playback_offset_advance = dlg.is_advance()
            self.audio.set_output_latency_ms(dlg.latency_ms())
            settings.set('audio_latency_ms', int(dlg.latency_ms()))
            # 更新工具列上的偏移標籤
            self._refresh_offset_label()

    # ------------------------------------------------------------------
    # MIDI 匯出
    # ------------------------------------------------------------------

    def export_midi_file(self) -> None:
        """把目前譜面存成 .mid。"""
        if self._block_on_unassigned():
            return
        m = self.view.model
        if not m.notes_tree:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_no_chart'))
            return
        base = os.path.splitext(os.path.basename(m.current_file or ''))[0] or 'chart'
        start_dir = os.path.dirname(m.current_file or '') or ''
        path, _ = QFileDialog.getSaveFileName(
            self, '匯出 MIDI', os.path.join(start_dir, base + '.mid'),
            'MIDI (*.mid);;All Files (*)')
        if not path:
            return
        try:
            m.save_midi(path)
        except Exception as exc:
            logging.exception('export midi failed')
            QMessageBox.critical(self, t('dlg_save_fail_title'), str(exc))
            return
        self.statusBar().showMessage('已匯出 MIDI：%s' % path, 6000)

    def render_piano_wav(self, path: str) -> bool:
        """把整份譜面用內建音源算成鋼琴音軌 WAV。回傳是否成功。

        走的是播放那條路（含力度、trill/staccato 表情、CC64 延音踏板），所以
        匯出的音軌和你在編輯器裡聽到的一致。
        """
        import wave

        m = self.view.model
        rate = self.audio.audio_rate if self.audio.audio_rate > 0 else 44100
        try:
            if (self._midi_preview_synth is None
                    or self._midi_preview_synth.sample_rate != rate):
                if self._midi_preview_synth is not None:
                    self._midi_preview_synth.close()
                self._midi_preview_synth = MidiPreviewSynth(rate)
            if not self._midi_preview_synth.is_ready:
                return False
            notes = build_chart_midi_notes(
                m, note_length_ms=None, expressive=True, real_pedal=True)
            if not notes:
                return False
            end_ms = max(float(m.music_end_ms or 0.0),
                         max(n.end_ms for n in notes)) + 1500.0
            pcm = self._midi_preview_synth.render(
                notes, 0.0, end_ms,
                pedal_spans=pedal_spans_in_range(m, 0.0, end_ms))
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm)
            return True
        except Exception:
            logging.exception('render piano wav failed')
            return False

    # ==================================================================
    # 匯出完整曲目
    # ==================================================================

    @staticmethod
    def _write_silent_wav(path: str, duration_ms: float,
                          rate: int = 8000) -> None:
        """寫一段無聲的 WAV。

        給「無背景音樂」用：遊戲的 `audioResourcePath` 是必填的，留空會驗證
        失敗，所以放一段和譜面等長的無聲音訊，聲音就只剩玩家打出來的 keysound。

        單聲道 8kHz 就夠——這個檔案唯一的用途是給遊戲一個正確長度的時間軸，
        沒有任何內容。207 秒只要 3.3MB，用 44.1kHz 立體聲會是 36MB。
        """
        import wave

        frames = max(1, int(round(max(0.0, float(duration_ms)) / 1000.0 * rate)))
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(rate))
            wf.writeframes(bytes(2 * frames))   # 2 bytes/frame，全 0 = 無聲

    def _warn_if_no_pitch_data(self, m) -> bool:
        """「無背景音樂」的譜面必須有音高，否則遊戲裡會**完全沒有聲音**。

        遊戲的 keysound 是 `PianoVisualLayout.ResolveMidiPitch` 決定要彈哪個
        音的；沒有 pitch / scale_piano 就回 -1，那顆音不發聲。背景又是無聲的，
        結果就是整首靜音——實測 no45 那首就是這樣，譜面只有鍵道沒有音高。

        回傳 False 表示使用者選擇取消匯出。
        """
        notes = list(getattr(m, 'notes_tree', ()) or ())
        if not notes:
            return True
        have = sum(1 for n in notes if n.pitch is not None)
        if have >= len(notes) * 0.99:
            return True
        answer = QMessageBox.warning(
            self, '無背景音樂',
            '這份譜面有 %d / %d 顆音符沒有音高資料。\n\n'
            '「無背景音樂」是靠玩家打出的 keysound 發聲的，沒有音高的音符在'
            '遊戲裡不會發出任何聲音——背景又是無聲的，整首歌會完全沒有聲音。\n\n'
            '建議先用「從 MIDI 還原音高」把音高補回來再匯出。\n'
            '仍要繼續嗎？' % (len(notes) - have, len(notes)),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        return answer == QMessageBox.Yes

    @staticmethod
    def _pad_wav_tail(path: str, min_duration_ms: float) -> float:
        """把 WAV 的尾巴補靜音到至少 `min_duration_ms`，回傳補了幾毫秒。

        「樂曲總資訊」的整體位移把整首往後推之後，音訊**不會**跟著變長：
        前面塞進去的空白等於把最後那幾秒擠出音檔之外。遊戲是讀譜面的
        `music_finish_time_msec` 收歌的，所以歌照跑，只是後面沒有聲音。
        前面補了多少，後面就要補回來。

        只加不減——比譜面長的音檔原封不動，不會去裁掉尾奏。
        """
        import wave

        if not os.path.isfile(path):
            return 0.0
        with wave.open(path, 'rb') as wf:
            params = wf.getparams()
            frames = wf.readframes(params.nframes)
        want = int(round(max(0.0, float(min_duration_ms)) / 1000.0 * params.framerate))
        if params.nframes >= want:
            return 0.0
        extra = want - params.nframes
        with wave.open(path, 'wb') as wf:
            wf.setparams(params)
            wf.writeframes(frames)
            wf.writeframes(bytes(extra * params.nchannels * params.sampwidth))
        return extra / float(params.framerate) * 1000.0

    def _write_song_category(self, root: str, folder_name: str,
                             category: str) -> None:
        """把這首歌的分類寫進 `<匯出根目錄>/library.json`。

        遊戲端 `ExternalSongLibrary` 的分類不在 register.json 裡，而是在索引檔
        library.json：每首歌一筆 `folderName` + `category` + `categories`，另外
        還有一份全域 `categories` 清單供選歌畫面列頁籤。這裡兩邊都補上，沒有
        檔案就建一份，其他歌的資料原封不動。
        """
        import json

        category = (category or 'Other').strip() or 'Other'
        path = os.path.join(root, 'library.json')
        data = {}
        try:
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f) or {}
        except Exception:                       # noqa: BLE001
            logging.exception('library.json 讀取失敗，改寫成新的')
            data = {}
        songs = data.get('songs')
        if not isinstance(songs, list):
            songs = []
        entry = None
        for song in songs:
            if isinstance(song, dict) and str(song.get('folderName') or '') == folder_name:
                entry = song
                break
        if entry is None:
            entry = {'id': 'portable:%s' % folder_name, 'folderName': folder_name}
            songs.append(entry)
        entry['category'] = category
        existing = [c for c in (entry.get('categories') or []) if c]
        if category not in existing:
            existing.insert(0, category)
        entry['categories'] = existing
        data['songs'] = songs
        names = [c for c in (data.get('categories') or []) if c]
        if category not in names and category.upper() != 'ALL':
            names.append(category)
        data['categories'] = names
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logging.debug('library.json updated: %s -> %s', folder_name, category)
        except Exception:                       # noqa: BLE001
            logging.exception('library.json 寫入失敗')

    def enter_editor(self) -> None:
        """顯示並帶到最前面（啟動器叫開譜時也走這裡）。第一次顯示時才問要不要還原自動備份。"""
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if not getattr(self, '_recovery_offered', False):
            self._recovery_offered = True
            QTimer.singleShot(300, self.offer_crash_recovery)

    def open_chart_from_library(self, path: str) -> None:
        """曲庫管理雙擊難度：打開那份譜（目前的譜沒存會先問）。"""
        m = self.view.model
        if m.dirty:
            reply = QMessageBox.question(
                self, t('dlg_unsaved_title'), t('dlg_unsaved_msg'),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Save:
                self.save_file()
                if self.view.model.dirty:
                    return
        self._load_path(path)
        self.enter_editor()
        self.activateWindow()

    def _notify_game_if_in_library(self, path: str) -> None:
        """存到曲庫裡的譜：通知遊戲重整（下次開這首就是新的譜）。"""
        try:
            from .song_folder import find_song_folder
            from .song_library import INDEX_FILE, SongLibrary
            song_dir = find_song_folder(path)
            if not song_dir:
                return
            root = os.path.dirname(song_dir)
            if not os.path.isfile(os.path.join(root, INDEX_FILE)):
                return
            SongLibrary(root).notify_game(os.path.basename(song_dir))
        except Exception:                               # noqa: BLE001
            logging.exception('notify game failed')

    def export_hiraeth_packages(self) -> None:
        """輸出 Hiraeth（PAN 歌曲管理版）用的歌曲包 ZIP。"""
        from .hiraeth_export_dialog import MODE_CHART, HiraethExportDialog
        from .song_folder import find_song_folder

        m = self.view.model
        song_dir = find_song_folder(m.current_file or '')
        library = settings.get('export_songs_root') or SONGS_ROOT
        if song_dir:
            library = os.path.dirname(song_dir)
        dlg = HiraethExportDialog(self, song_dir, library, m,
                                  audio_path=getattr(self.audio, 'audio_path', '') or '')
        if dlg.exec_() != HiraethExportDialog.Accepted:
            return
        if dlg.mode() == MODE_CHART:
            if not m.notes_tree:
                QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_no_chart'))
                return
        elif m.dirty and song_dir:
            reply = QMessageBox.question(
                self, '輸出 Hiraeth 歌曲包',
                '目前譜面有還沒存檔的修改。樂曲資料夾／曲庫模式讀的是磁碟上的檔案，'
                '要先存檔嗎？',
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                self.save_file()
        plans, skipped = dlg.plans()
        out_dir = dlg.out_edit.text().strip()
        if not plans:
            QMessageBox.information(self, '輸出 Hiraeth 歌曲包',
                                    '沒有可以輸出的譜面。\n\n' + '\n'.join(skipped[:20]))
            return

        self._run_hiraeth_export(plans, skipped, out_dir, dlg.hold_gap_ms())

    def _run_hiraeth_export(self, plans, skipped, out_dir: str, hold_gap_ms: int) -> None:
        from .hiraeth_export_dialog import run_export
        run_export(self, plans, skipped, out_dir, hold_gap_ms)

    @staticmethod
    def _existing_audio_resource(dlg) -> str:
        """追加難度時，這首歌原本的背景音樂（不是無聲的那種）；沒有就空字串。"""
        if not dlg.is_append_mode():
            return ''
        for diff in (dlg.existing_register() or {}).get('difficulties', []) or []:
            if diff.get('audioResourcePath') and not diff.get('noBackgroundMusic'):
                return str(diff['audioResourcePath'])
        return ''

    @staticmethod
    def _same_audio_file(a: str, b: str) -> bool:
        import filecmp
        try:
            if os.path.samefile(a, b):
                return True
            return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
        except OSError:
            return False

    def _game_library_root(self) -> str:
        """遊戲的曲庫：製譜器放在遊戲資料夾裡就是旁邊的 UserSongs；否則上次管理的曲庫。

        要有 library.json 才算（不然只是某個資料夾，讓使用者自己選位置）。
        """
        from .song_library import INDEX_FILE, default_root
        root = default_root()
        return root if root and os.path.isfile(os.path.join(root, INDEX_FILE)) else ''

    def export_song(self) -> None:
        """匯出完整曲目格式（register.json + 音源 + 譜面 + 曲繪）。"""
        if self._block_on_unassigned():
            return
        logging.debug('export_song: start')
        import json
        import shutil
        from pathlib import Path

        m = self.view.model
        logging.debug('model current_file=%s dirty=%s', m.current_file, getattr(m, 'dirty', None))
        if not m.notes_tree:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_no_chart'))
            return

        # 沒有音源是正常情況（純鋼琴曲、先排譜之後才配樂），不擋也不問；
        # 對話框上小字標註「以無背景音樂匯出」。
        wav_path = getattr(self.audio, 'audio_path', '') or ''
        if wav_path and not os.path.isfile(wav_path):
            wav_path = ''
        logging.debug('audio_path=%s', wav_path)

        dlg = ExportSongDialog(
            self,
            offset_ms=self._playback_offset_ms,
            wav_path=wav_path,
            chart_json_path=m.current_file or '',
            default_root=settings.get('export_songs_root') or SONGS_ROOT,
            game_root=self._game_library_root(),
        )
        if dlg.exec_() != ExportSongDialog.Accepted:
            logging.debug('export_song: dialog cancelled')
            return

        # 沒有音源時照對話框選的：無背景音樂／用 MIDI 轉成音源／沿用這首原本的音樂
        no_audio_mode = '' if wav_path else (getattr(dlg, 'no_audio_mode', lambda: 'silent')() or 'silent')
        reused_audio_res = self._existing_audio_resource(dlg) if no_audio_mode == 'reuse' else ''
        midi_audio = no_audio_mode == 'midi'
        no_bgm = dlg.no_background_music() or no_audio_mode == 'silent' or \
            (no_audio_mode == 'reuse' and not reused_audio_res)

        # 鋼琴音軌：用內建音源把譜面算成 WAV，寫進 register 的
        # pianoAudioResourcePath。遊戲有這個欄位就能放「只有鋼琴」的版本。
        # 無背景音樂時不輸出（見下面），就不用問。
        want_piano = (not no_bgm) and (not midi_audio) and QMessageBox.question(
            self, '鋼琴音軌',
            '要不要一併輸出 MIDI 鋼琴音軌？\n\n'
            '用內建音源把整份譜面算成 WAV（含力度、表情與延音踏板），'
            '和你在編輯器裡聽到的一致。\n曲子長的話會算一陣子。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

        # 使用者選定的匯出根目錄（空 → 回退到自動偵測的 SONGS_ROOT）；並記住供下次使用
        export_root = dlg.export_root() or SONGS_ROOT
        if export_root and os.path.isdir(export_root) and not dlg.exports_to_game():
            settings.set('export_songs_root', export_root)

        logging.debug('export_song: dialog accepted')

        try:
            display_name = dlg.display_name()
            logging.debug('export params: display_name=%s, diff_name=%s, diff_level=%s, append=%s',
                          display_name, dlg.diff_name(), dlg.diff_level(), dlg.is_append_mode())
            author       = dlg.author()
            diff_name    = dlg.diff_name()
            diff_level   = dlg.diff_level()
            cover_path   = dlg.cover_path()
            is_append    = dlg.is_append_mode()
            append_folder = dlg.append_folder()
            append_folder_full = dlg.append_folder_full()

            # 檔案系統用的安全名稱（曲名可能含 '/'、':' 等 Windows 非法字元）。
            # register.json 的 displayName / difficultyName 仍用原始名稱。
            safe_name = safe_fs_name(display_name)
            safe_diff = safe_fs_name(diff_name, fallback='Normal')

            # 決定曲目資料夾
            if is_append and append_folder_full:
                song_folder = append_folder_full
                folder_name = append_folder
            elif is_append and append_folder:
                song_folder = os.path.join(export_root, append_folder)
                folder_name = append_folder
            else:
                folder_name = safe_name
                song_folder = os.path.join(export_root, folder_name)

            logging.debug('creating song_folder=%s', song_folder)
            os.makedirs(song_folder, exist_ok=True)

            # ── 難度子資料夾 ──────────────────────────────────────
            diff_folder = os.path.join(song_folder, safe_diff)
            logging.debug('creating diff_folder=%s', diff_folder)
            os.makedirs(diff_folder, exist_ok=True)

            # ── 儲存譜面 JSON ─────────────────────────────────────
            chart_basename = safe_name   # 用曲名當 JSON 檔名（已合法化）
            # save_json / save_xml 都會覆寫 model.file_format，之後再問
            # is_midi_mode() 就永遠是 False，所以先取快照。
            was_midi = m.is_midi_mode()
            src_chart_path = getattr(m, 'current_file', None) or ''
            chart_path = os.path.join(diff_folder, chart_basename + '.json')
            logging.debug('saving chart json to %s', chart_path)
            m.save_json(chart_path)
            logging.debug('chart saved')

            # ── 譜面原始格式備份（XML / MIDI）──────────────────────
            # 遊戲只讀 JSON，但 XML 保留了 scale_piano、param 鏈結等 JSON 會
            # 丟掉的欄位，MIDI 則是重新排譜的來源。一起放進曲目資料夾當備份，
            # 之後要重製或改難度才不用回頭找原檔。
            backup_dir = os.path.join(diff_folder, 'source')
            os.makedirs(backup_dir, exist_ok=True)
            backed_up: List[str] = []
            try:
                xml_backup = os.path.join(backup_dir, chart_basename + '.xml')
                m.save_xml(xml_backup)
                backed_up.append(os.path.basename(xml_backup))
            except Exception:
                logging.exception('export_song: xml backup failed')
            if was_midi:
                try:
                    midi_backup = os.path.join(backup_dir, chart_basename + '.mid')
                    m.save_midi(midi_backup)
                    backed_up.append(os.path.basename(midi_backup))
                except Exception:
                    logging.exception('export_song: midi backup failed')
            else:
                # 非 MIDI 來源：如果原始檔本身就是 mid，直接複製一份
                src_chart = src_chart_path
                if src_chart.lower().endswith(('.mid', '.midi')) and os.path.isfile(src_chart):
                    try:
                        dest = os.path.join(backup_dir, chart_basename + os.path.splitext(src_chart)[1])
                        shutil.copy2(src_chart, dest)
                        backed_up.append(os.path.basename(dest))
                    except Exception:
                        logging.exception('export_song: midi copy failed')
            logging.debug('source backup: %s', backed_up)
            # 備份的存檔會把「目前檔案」換成備份那一份（XML 還會切成 PAN 相容模式）。
            # 匯出之後接著編的是剛匯出的 JSON，把狀態擺回那裡，Ctrl+S 才不會存進
            # source/ 備份資料夾。
            m.current_file = chart_path
            m.file_format = 'json'
            m.pan_xml = False
            m.dirty = False
            self._apply_format_mode()

            # ── 處理音源 ──────────────────────────────────────────
            src_wav = Path(wav_path)

            # ── 無背景音樂：輸出等長的無聲音訊 ────────────────────
            # 遊戲端 audioResourcePath 是必填的（ExternalSongLibrary.RequireAsset），
            # 留空會驗證失敗，所以放一段無聲的。加上不輸出鋼琴音軌，遊戲裡就
            # 只剩玩家打出來的 keysound。
            if (no_bgm or midi_audio) and not self._warn_if_no_pitch_data(m):
                return
            if no_bgm:
                end_ms = max(
                    float(getattr(m, 'music_end_ms', 0.0) or 0.0),
                    max((float(n.end) for n in m.notes_tree), default=0.0) + 2000.0,
                )
                audio_dest = os.path.join(song_folder, safe_name + '.wav')
                self._write_silent_wav(audio_dest, end_ms)
                audio_res_suffix = safe_name
                logging.debug('silent backing track written: %s (%.1f s)',
                              audio_dest, end_ms / 1000.0)
            offset = self._playback_offset_ms
            # rip.py 的 process_audio: 正值=前面加靜音，負值=裁剪前面
            # 我們的偏移語意：正=提前（音訊要從更前面開始→砍前面 = 負 ms）
            #                 負=延後（音訊前面要加靜音   = 正 ms）
            rip_ms = -offset   # 轉換語意

            audio_in_song_root = os.path.join(song_folder, safe_name + '.wav')
            logging.debug('processing audio with rip_ms=%s', rip_ms)
            if midi_audio:
                # 純粹用 MIDI 轉音源：內建鋼琴把譜面算成背景音樂
                audio_dest = os.path.join(song_folder, safe_name + '.wav')
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    rendered = self.render_piano_wav(audio_dest)
                finally:
                    QApplication.restoreOverrideCursor()
                if not rendered:
                    QMessageBox.warning(self, t('dlg_export_title'), t('dlg_export_midi_audio_failed'))
                    return
                audio_res_suffix = safe_name
            elif no_bgm:
                pass                      # 上面已經寫好無聲音訊
            elif reused_audio_res:
                audio_res_suffix = ''     # 沿用原本的音樂，不動檔案
            elif rip_ms == 0:
                # 無偏移：若曲目資料夾已有同名 wav → 直接沿用，不複製
                # Check user settings: auto-process audio on export (parse filename offset)
                auto_proc = bool(settings.get('export_auto_process_audio', True))
                processed = None
                if auto_proc:
                    try:
                        from .wav_process import parse_offset_from_filename, process_wav
                        detected = parse_offset_from_filename(str(src_wav))
                        if detected is not None and detected != 0:
                            sign = '+' if detected > 0 else ''
                            offset_tag = f'{sign}{detected}ms'
                            wav_name = f'{safe_name}_{offset_tag}.wav'
                            audio_dest = os.path.join(diff_folder, wav_name)
                            try:
                                process_wav(str(src_wav), audio_dest, offset_ms=detected,
                                            trim_end_ms=int(settings.get('export_trim_end_ms', 0)))
                                processed = audio_dest
                            except Exception:
                                processed = None
                            if processed:
                                audio_res_suffix = f'{safe_diff}/{Path(wav_name).stem}'
                                logging.debug('audio processed to %s (audio_dest=%s)', processed, audio_dest)
                    except Exception:
                        processed = None

                if processed is None:
                    if not os.path.isfile(audio_in_song_root):
                        audio_dest = audio_in_song_root
                        shutil.copy2(str(src_wav), audio_dest)
                        audio_res_suffix = safe_name      # 資源路徑用曲名 (放在曲目根)
                    elif self._same_audio_file(str(src_wav), audio_in_song_root):
                        audio_dest = audio_in_song_root
                        audio_res_suffix = safe_name
                    else:
                        # 曲目資料夾已經有一份**不一樣**的同名音訊（例如這次加了開頭
                        # 空白）。以前直接沿用舊的，新音訊永遠匯不出去。蓋掉會讓其他
                        # 難度錯開，所以放進這個難度自己的子資料夾。
                        wav_name = os.path.basename(str(src_wav))
                        audio_dest = os.path.join(diff_folder, wav_name)
                        shutil.copy2(str(src_wav), audio_dest)
                        audio_res_suffix = f'{safe_diff}/{Path(wav_name).stem}'
            else:
                # 有偏移：處理音源後放入難度子資料夾，檔名標註偏移量
                sign = '+' if offset > 0 else ''
                offset_tag = f'{sign}{offset}ms'
                wav_name = f'{safe_name}_{offset_tag}.wav'
                audio_dest = os.path.join(diff_folder, wav_name)
                # 以前這裡是 `from rip import process_audio`，但 **rip.py 根本
                # 不存在**（不在原始碼、也不在打包 spec 裡），所以 ImportError
                # 每次都成立、然後 `processed = src_wav` 把未處理的原檔複製過去
                # ——檔名標著 `-1043ms`，內容卻原封不動。這條路從來沒有真的處理
                # 過音訊。改用套件內的 wav_process，正負號語意完全一樣
                # （正 = 前面補靜音 = 延後）。
                try:
                    from .wav_process import process_wav
                    process_wav(str(src_wav), audio_dest, offset_ms=int(rip_ms),
                                trim_end_ms=int(settings.get('export_trim_end_ms', 0)))
                except Exception:
                    logging.exception('export_song: audio offset failed')
                    # 靜靜複製原檔，使用者會拿到一份「檔名說有偏移、實際沒有」
                    # 的音訊，而且要到進遊戲才會發現。寧可講出來。
                    shutil.copy2(str(src_wav), audio_dest)
                    QMessageBox.warning(
                        self, '音訊偏移',
                        '音訊偏移 %+d ms 套用失敗，匯出的是**未處理**的原始音訊。'
                        % offset)
                audio_res_suffix = f'{safe_diff}/{Path(wav_name).stem}'  # 資源路徑含難度子資料夾
                logging.debug('audio offset %+d ms applied -> %s', rip_ms, audio_dest)

            # ── 鋼琴音軌（選用）──────────────────────────────────
            # 「無背景音樂」時完全不輸出：留著的話，玩家如果沒開合成鋼琴模式，
            # 遊戲會把錄好的鋼琴層疊在 keysound 底下，等於又有一台鋼琴在彈。
            if no_bgm:
                want_piano = False
            if want_piano:
                piano_name = f'{safe_name}_piano.wav'
                piano_dest = os.path.join(song_folder, piano_name)
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    ok_piano = self.render_piano_wav(piano_dest)
                finally:
                    QApplication.restoreOverrideCursor()
                if ok_piano:
                    piano_res_suffix = Path(piano_name).stem
                    logging.debug('piano track written to %s', piano_dest)
                else:
                    piano_res_suffix = ''
                    QMessageBox.warning(
                        self, '鋼琴音軌',
                        '鋼琴音軌算不出來（音源不可用或譜面沒有音符），'
                        '其餘內容照常匯出。')
            else:
                piano_res_suffix = ''

            # ── 讓音訊蓋過譜面的結尾 ──────────────────────────────
            # 見 _pad_wav_tail：整體位移只搬譜面，音檔長度不會跟著長。
            chart_end_ms = max(
                float(getattr(m, 'music_end_ms', 0.0) or 0.0),
                max((float(n.end) for n in m.notes_tree), default=0.0),
            )
            for _suffix in (audio_res_suffix, piano_res_suffix):
                if not _suffix:
                    continue
                _wav = os.path.join(song_folder,
                                    _suffix.replace('/', os.sep) + '.wav')
                try:
                    _added = self._pad_wav_tail(_wav, chart_end_ms)
                    if _added:
                        logging.debug('padded tail of %s by %.0f ms', _wav, _added)
                except Exception:
                    logging.exception('export_song: tail padding failed: %s', _wav)

            # ── 處理曲繪 ──────────────────────────────────────────
            cover_dest = ''
            if cover_path and os.path.isfile(cover_path):
                ext = os.path.splitext(cover_path)[1]
                cover_dest_name = safe_name + ext
                cover_dest = os.path.join(song_folder, cover_dest_name)
                if os.path.normpath(cover_path) != os.path.normpath(cover_dest):
                    shutil.copy2(cover_path, cover_dest)
                logging.debug('cover copied to %s', cover_dest)

            # ── 生成 / 更新 register.json ─────────────────────────
            # 建構 chartFileName / audioResourcePath / coverResourcePath
            # 格式：songs/<folder>/<diff>/<basename>  (無副檔名)
            # 嘗試從實際路徑推導出 Resources 下的相對路徑
            _res_marker = os.sep + 'Resources' + os.sep
            if _res_marker in song_folder:
                _res_rel = song_folder.split(_res_marker, 1)[1].replace(os.sep, '/')
            else:
                _res_rel = f'songs/{folder_name}'
            chart_res  = f'{_res_rel}/{safe_diff}/{chart_basename}'
            audio_res  = reused_audio_res or f'{_res_rel}/{audio_res_suffix}'
            cover_res  = f'{_res_rel}/{safe_name}' if cover_dest else ''

            # 追加模式且曲繪已存在時，沿用原有 coverResourcePath（不複製檔案）
            if is_append and not cover_dest:
                existing_reg = dlg.existing_register()
                if existing_reg:
                    diffs = existing_reg.get('difficulties', [])
                    if diffs:
                        cover_res = diffs[0].get('coverResourcePath', cover_res)

            new_diff = {
                'difficultyName':    diff_name,
                'difficultyLevel':   diff_level,
                'chartFileName':     chart_res,
                'audioResourcePath': audio_res,
                'coverResourcePath': cover_res,
            }
            if no_bgm:
                # 遊戲端讀到這個旗標就會強制開合成鋼琴（見 SettingsManager
                # .SongForcesSynthesisedPiano）：不然玩家若停在「遊戲錄音」
                # 音源模式，這首歌會整首沒有聲音。
                new_diff['noBackgroundMusic'] = True

            # ── 背景影片（選填）───────────────────────────────
            # 遊戲端 ExternalSongLibrary 會把 videoPath 當成 songs/<資料夾>/<名稱>
            # 去找 .mp4 / .webm / .mov，和曲繪同一套規則，所以這裡也不寫副檔名。
            video_src = dlg.video_path()
            if video_src and os.path.isfile(video_src):
                video_ext = os.path.splitext(video_src)[1].lower() or '.mp4'
                video_dest = os.path.join(song_folder, safe_name + video_ext)
                if os.path.abspath(video_src) != os.path.abspath(video_dest):
                    shutil.copy2(video_src, video_dest)
                new_diff['videoPath'] = f'{_res_rel}/{safe_name}'
                new_diff['videostartTime'] = float(dlg.video_start_sec())
                logging.debug('video copied to %s', video_dest)
            elif is_append:
                # 追加難度時沒指定影片 → 沿用同一首歌既有的那一份
                existing_reg = dlg.existing_register()
                for d in (existing_reg or {}).get('difficulties', []):
                    if d.get('videoPath'):
                        new_diff['videoPath'] = d['videoPath']
                        new_diff['videostartTime'] = d.get('videostartTime', 0.0)
                        break
            if piano_res_suffix:
                new_diff['pianoAudioResourcePath'] = f'{_res_rel}/{piano_res_suffix}'
            elif no_bgm:
                pass          # 無背景音樂：連沿用舊鋼琴音軌都不要
            elif is_append:
                # 追加難度時沒重算鋼琴音軌 → 沿用原本那一份，不要把欄位弄丟
                existing_reg = dlg.existing_register()
                for d in (existing_reg or {}).get('difficulties', []):
                    if d.get('pianoAudioResourcePath'):
                        new_diff['pianoAudioResourcePath'] = d['pianoAudioResourcePath']
                        break

            reg_path = os.path.join(song_folder, 'register.json')
            if is_append and os.path.isfile(reg_path):
                with open(reg_path, 'r', encoding='utf-8') as f:
                    reg = json.load(f)
                # 若已有同名難度，替換之；否則直接追加
                diffs = reg.get('difficulties', [])
                replaced = False
                for i, d in enumerate(diffs):
                    if d.get('difficultyName') == diff_name:
                        diffs[i] = new_diff
                        replaced = True
                        break
                if not replaced:
                    diffs.append(new_diff)
                reg['difficulties'] = diffs
            else:
                reg = {
                    'displayName': display_name,
                    'author':      author,
                    'difficulties': [new_diff],
                }

            logging.debug('writing register.json to %s', reg_path)
            with open(reg_path, 'w', encoding='utf-8') as f:
                json.dump(reg, f, ensure_ascii=False, indent=2)
            logging.debug('register.json written')

            # ── 分類（寫在匯出根目錄的 library.json，不是 register.json）──
            self._write_song_category(
                os.path.dirname(song_folder), folder_name, dlg.category())

            if dlg.exports_to_game():
                # 通知遊戲重整選曲並停在這首
                self._notify_game_if_in_library(chart_path)
                QMessageBox.information(
                    self, t('dlg_export_ok_title'), t('dlg_export_ok_game', song_folder))
            else:
                QMessageBox.information(
                    self, t('dlg_export_ok_title'), t('dlg_export_ok_msg', song_folder))

        except Exception as e:
            logging.exception('export failed')
            QMessageBox.critical(
                self, t('dlg_export_fail_title'), t('dlg_export_fail_msg', e))

    # ==================================================================
    # 工具對話框
    # ==================================================================

    def shift_pitch_dialog(self) -> None:
        delta, ok = QInputDialog.getInt(
            self, t('dlg_shift_pitch_title'), t('dlg_shift_pitch_label'),
            0, -128, 128,
        )
        if ok:
            self.view.shift_selected_pitch(delta)

    def resolve_hold_tails_dialog(self) -> None:
        """長條尾端：哪些要裁、哪些是分解和弦要留著。

        「手按得住就不裁」太鬆——分解和弦按著是對的，但音階跑動按著會把二度
        撞在一起，那種才要裁。所以把判斷條件攤成選項，見 `release_deadline`。
        """
        from PyQt5.QtWidgets import QCheckBox, QSpinBox

        dlg = QDialog(self)
        dlg.setWindowTitle(t('dlg_resolve_hold_tails_title'))
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()

        sp_gap = QSpinBox()
        sp_gap.setRange(1, 1000)
        sp_gap.setValue(40)
        sp_gap.setSuffix(' ms')
        form.addRow(t('dlg_resolve_label'), sp_gap)

        cb_step = QCheckBox('同手下一顆沒有重疊到就留出間隔')
        cb_step.setChecked(True)
        cb_step.setToolTip(
            '同手的下一顆和這條長押**時間上沒有重疊** = 前後關係，' + '\n' +
            '即使手按得住也該把間隔留出來。' + '\n' +
            '有重疊的就是分解和弦，一律不動。' + '\n' + '\n' +
            '不勾的話只裁真的按不出來的（同一個鍵、超過五指、超過手的跨度），' + '\n' +
            '那條規則在整個曲庫只會動到 14% 的長押，尾巴貼著下一顆也不管。')
        vbox.addLayout(form)
        vbox.addWidget(cb_step)

        row = QHBoxLayout()
        cb_ring = QCheckBox('長押期間最多讓幾顆同手音進來')
        cb_ring.setChecked(False)
        sp_ring = QSpinBox()
        sp_ring.setRange(1, 16)
        sp_ring.setValue(3)
        sp_ring.setSuffix(' 顆')
        sp_ring.setEnabled(False)
        cb_ring.toggled.connect(sp_ring.setEnabled)
        cb_ring.setToolTip(
            '一組分解和弦就那麼幾顆，響過一長串之後和聲早就換過了。\n'
            '官方有重疊的長押裡 92% 只讓 3 顆以內的音進來。')
        row.addWidget(cb_ring)
        row.addWidget(sp_ring)
        row.addStretch()
        vbox.addLayout(row)

        cb_all = QCheckBox('一律裁到同手下一顆（不管按不按得住）')
        cb_all.setChecked(False)
        cb_all.setToolTip('最狠的做法：分解和弦也會被砍掉。')
        vbox.addWidget(cb_all)

        def sync(on):
            for w in (cb_step, cb_ring, sp_ring):
                w.setEnabled(not on)
            if not on:
                sp_ring.setEnabled(cb_ring.isChecked())
        cb_all.toggled.connect(sync)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        vbox.addWidget(bb)
        if dlg.exec_() != QDialog.Accepted:
            return

        gap = int(sp_gap.value())
        m = self.view.model
        was_dirty = m.dirty
        # 這幾個旋鈕就是要調的東西——記下來才知道哪組設定的結果被使用者改回去
        oplog.params(gap_ms=gap, only_conflicts=not cb_all.isChecked(),
                     sequential_gap=cb_step.isChecked(),
                     max_ring_notes=int(sp_ring.value()) if cb_ring.isChecked() else 0)
        m.push_history()
        changed = m.resolve_hold_tail_overlaps(
            gap,
            only_conflicts=not cb_all.isChecked(),
            sequential_gap=cb_step.isChecked(),
            max_ring_notes=int(sp_ring.value()) if cb_ring.isChecked() else 0)
        if changed:
            m.rebuild_display_cache()
            self.view.update()
            self.view.note_edited.emit()
        else:
            m.discard_last_history()
            m.dirty = was_dirty
            QMessageBox.information(self, t('dlg_no_overlaps_title'), t('dlg_no_overlaps_msg'))

    def resolve_horizontal_overlaps(self) -> None:
        from PyQt5.QtWidgets import QCheckBox, QSpinBox

        dlg = QDialog(self)
        dlg.setWindowTitle(t('action_resolve_horizontal_overlaps'))
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()

        sp_tol = QSpinBox()
        sp_tol.setRange(0, 2000)
        sp_tol.setValue(30)          # MIDI 轉出的和弦常差幾毫秒，0 會完全抓不到
        sp_tol.setSuffix(' ms')
        form.addRow('視為「同時起音」的容差', sp_tol)

        cb_time = QCheckBox('同時發聲也算重疊（長條壓到後面的音符）')
        cb_time.setChecked(True)
        cb_time.setToolTip(
            '不勾：只整理起音時間相近的和弦。\n'
            '勾：長條還按著時、同樣鍵位又出現的音符也會被讓開——\n'
            '這才是編輯器裡真正看得到的那種重疊。'
        )
        vbox.addLayout(form)
        vbox.addWidget(cb_time)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        vbox.addWidget(bbox)
        if dlg.exec_() != QDialog.Accepted:
            return

        m = self.view.model
        was_dirty = m.dirty
        m.push_history()
        report = m.resolve_horizontal_overlaps_report(
            sp_tol.value(), cb_time.isChecked())
        if report['moved']:
            m.rebuild_display_cache()
            self.view.update()
            self.view.note_edited.emit()
            msg = '已移開 %d 顆音符（共偵測到 %d 處重疊）' % (
                report['moved'], report['conflicts'])
            if report['unresolved']:
                msg += '；有 %d 處兩側都塞不下，未處理' % report['unresolved']
            self.statusBar().showMessage(msg, 8000)
        else:
            m.discard_last_history()
            m.dirty = was_dirty
            if report['unresolved']:
                QMessageBox.warning(
                    self, t('dlg_no_overlaps_title'),
                    '偵測到 %d 處重疊，但兩側都沒有空間可以讓開，一顆都沒動。'
                    % report['unresolved'])
            else:
                QMessageBox.information(
                    self, t('dlg_no_overlaps_title'), t('dlg_no_overlaps_msg'))

    def hold_length_fix_dialog(self) -> None:
        if self.view.alloc_active:
            return
        has_sel = bool(self.view.selected)
        dlg = HoldLengthDialog(self, has_selection=has_sel)
        if dlg.exec_() != QDialog.Accepted:
            return
        params = dlg.params()
        stats = self.view.apply_hold_length_fix(params)
        if not (stats['tapped'] or stats['trimmed'] or stats.get('gapped')):
            QMessageBox.information(
                self, '長押長度修整',
                '沒有符合條件的長押可修整（範圍內找到 %d 顆長押）。' % stats['total'])
            return
        self.statusBar().showMessage(
            '長押長度修整：轉 Tap %d、砍長度 %d、補間隔 %d、不變 %d（共 %d 顆長押）'
            % (stats['tapped'], stats['trimmed'], stats.get('gapped', 0),
               stats['unchanged'], stats['total']),
            6000,
        )

    def repair_beat_entries(self) -> None:
        """補回 beat_data 裡漏掉的拍點。

        漏拍的症狀是「BPM 怪怪的」：索引是連號的，但某些格的時間是別人的兩倍，
        編輯器只能把那幾小節算成一半的 BPM，時間均分模式下那一段的畫面也跟著
        不對。補回去之後小節數與 BPM 才會回到正確的值。
        """
        m = self.view.model
        missing = m.find_missing_beat_entries()
        if not missing:
            QMessageBox.information(
                self, '修補缺漏的拍點', '拍點間距是均勻的，沒有需要補的地方。')
            return
        before_bars = m.count_measures()
        answer = QMessageBox.question(
            self, '修補缺漏的拍點',
            '找到 %d 個缺漏的拍點。\n\n'
            '補回去之後小節數會從 %d 變成 %d，各小節的 BPM 也會跟著修正。\n'
            '要繼續嗎？' % (len(missing), before_bars,
                            before_bars + len(missing) // 2),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        m.push_history()
        added = m.repair_missing_beat_entries()
        if not added:
            m.discard_last_history()
            return
        self.view.rebuild_mapper()
        self.view.update()
        self.view.note_edited.emit()
        self.statusBar().showMessage(
            '補了 %d 個拍點，小節數 %d → %d' % (added, before_bars, m.count_measures()),
            8000)

    def quantize_dialog(self) -> None:
        """量化：把音符起點對齊到拍點格線。"""
        from .quantize_dialog import QuantizeDialog

        view = self.view
        if view.alloc_active or not view.model.notes_tree:
            QMessageBox.information(self, '量化', '譜面沒有音符。')
            return
        dlg = QuantizeDialog(
            self, grids=view.QUANTIZE_GRIDS, sel_count=len(view.selected),
            default_grid_beats=0.25)
        if dlg.exec_() != QDialog.Accepted:
            return
        params = dlg.params()
        oplog.params(**{k: v for k, v in params.items()
                       if isinstance(v, (int, float, str, bool))})
        moved = view.quantize_notes(**params)
        if not moved:
            QMessageBox.information(self, '量化', '沒有音符需要移動（本來就在格線上）。')
            return
        self.statusBar().showMessage(
            '量化：%d 顆對齊到格線（強度 %d%%）'
            % (moved, int(round(params['strength'] * 100))), 6000)

    def snap_notes_to_key(self) -> None:
        """把離調的音符吸到最近的調內音。有選取就只處理選取。"""
        view = self.view
        if view.alloc_active:
            return
        key = view._active_key()
        if key is None:
            QMessageBox.information(
                self, '吸到調內', '譜面沒有音高資料，無法判斷調性。')
            return
        # 偵測不確定時先問過。吸完之後全譜的音高分布就變了，關係大小調那種
        # 模稜兩可的譜會被判成另一個調，使用者再按一次就把音移到別的地方去。
        if getattr(key, 'confidence', 1.0) < 0.35:
            answer = QMessageBox.question(
                self, '吸到調內',
                '偵測到的調性是「%s」，但不太確定（可能和關係大小調混淆）。\n'
                '要用這個調嗎？\n\n'
                '取消的話可以到「編輯」分頁的調性下拉自己指定，再執行一次。'
                % key.name(),
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
        oplog.params(key=key.name(), confidence=round(float(
            getattr(key, 'confidence', 1.0)), 3),
            selection=len(view.selected))
        moved = view.snap_selected_to_key(key)
        if not moved:
            QMessageBox.information(
                self, '吸到調內',
                '沒有音符被移動——%s 裡沒有離調的音。' % key.name())

    def align_selected_time_dialog(self) -> None:
        if not self.view.selected or self.view.alloc_active:
            QMessageBox.information(
                self, t('dlg_no_overlaps_title'), t('dlg_align_no_sel_msg'))
            return
        stats = self.view.selection_time_bounds()
        if not stats:
            QMessageBox.information(
                self, t('dlg_no_overlaps_title'), t('dlg_align_no_sel_msg'))
            return
        dlg = AlignTimeDialog(self, stats=stats)
        if dlg.exec_() != QDialog.Accepted:
            return
        changed = self.view.align_selected_edge(dlg.target(), dlg.value_ms())
        if not changed:
            QMessageBox.information(
                self, t('dlg_no_overlaps_title'), t('dlg_align_no_change_msg'))

    # ------------------------------------------------------------------
    # 樂曲總資訊
    # ------------------------------------------------------------------

    def song_info_dialog(self) -> None:
        """整首共通的資料放在同一個對話框：BPM、總拍號、整體時間偏移。

        個別小節的 BPM / 拍號不在這裡——那是逐小節的事，走右鍵「小節」選單。
        三個欄位各自獨立，只有真的被改動的那幾項會套用。
        """
        from PyQt5.QtWidgets import QSpinBox

        m = self.view.model
        cur_bpm = float(m.bpm)
        cur_num = int(m.beats_per_bar)
        cur_den = int(m.time_sig_denominator)

        dlg = QDialog(self)
        dlg.setWindowTitle('樂曲總資訊')
        dlg.setMinimumWidth(400)
        vbox = QVBoxLayout(dlg)

        bpm_box = QGroupBox('全曲 BPM')
        bpm_form = QFormLayout(bpm_box)
        sp_bpm = QDoubleSpinBox()
        sp_bpm.setRange(10.0, 999.0)
        sp_bpm.setDecimals(2)
        sp_bpm.setValue(cur_bpm)
        sp_bpm.setToolTip(
            '改這裡會等比縮放整首（音符與拍點一起 × 舊/新）。\n'
            '音符對小節/拍的相對位置完全保留，只有絕對時間改變。'
        )
        bpm_form.addRow('BPM', sp_bpm)
        vbox.addWidget(bpm_box)

        sig_box = QGroupBox('總拍號')
        sig_form = QFormLayout(sig_box)
        sp_num = QSpinBox()
        sp_num.setRange(1, 32)
        sp_num.setValue(cur_num)
        sp_den = QSpinBox()
        sp_den.setRange(1, 64)
        sp_den.setValue(cur_den)
        sig_form.addRow('分子（每小節幾拍）', sp_num)
        sig_form.addRow('分母（幾分音符為一拍）', sp_den)
        sig_box.setToolTip('整首的預設拍號。個別小節要變拍號請用右鍵「小節」選單。')
        vbox.addWidget(sig_box)

        off_box = QGroupBox('整體時間偏移')
        off_form = QFormLayout(off_box)
        sp_off = QSpinBox()
        sp_off.setRange(-100000, 100000)
        sp_off.setValue(0)
        sp_off.setSuffix(' ms')
        sp_off.setToolTip(
            '把整個時間軸往後（正）或往前（負）移，**小節線一起搬**。0 = 不動。\n'
            '往後移會在最前面留下一段不屬於任何小節的空白（遊戲顯示的 BPM 取自第一小節，平移不影響它）。\n'
            '往前移碰到 0 會夾住。\n'
            '這是改譜面資料；只想調「播放時聽起來對不對齊」請用播放頁的偏移。'
        )
        off_form.addRow('整體位移（含小節線）', sp_off)
        vbox.addWidget(off_box)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        vbox.addWidget(bbox)

        if dlg.exec_() != QDialog.Accepted:
            return

        new_bpm = float(sp_bpm.value())
        new_num = int(sp_num.value())
        new_den = int(sp_den.value())
        offset = int(sp_off.value())
        changed = []

        if abs(new_bpm - cur_bpm) > 1e-9 and new_bpm > 0:
            m.push_history()
            # 等比縮放整首（音符 start/end + beat_data 一起 × 舊/新）
            m.scale_all_time((cur_bpm / new_bpm) if cur_bpm > 0 else 1.0)
            m.bpm = new_bpm
            m.rebuild_display_cache()
            changed.append('BPM %.2f → %.2f' % (cur_bpm, new_bpm))

        if (new_num, new_den) != (cur_num, cur_den):
            m.beats_per_bar = new_num
            m.time_sig_denominator = new_den
            m.dirty = True
            changed.append('總拍號 %d/%d → %d/%d' % (cur_num, cur_den, new_num, new_den))

        if offset:
            m.push_history()
            # 小節線要跟著搬，否則音符和小節的相對位置會跑掉。往後移會在最
            # 前面留下一段不屬於任何小節的空白，那是刻意的。
            m.shift_all_time(offset)
            applied = int(getattr(m, 'last_shift_ms', offset))
            m.rebuild_display_cache()
            if applied != offset:
                # 往前移最多只能移到最早的東西碰到 0，不然開頭幾筆會被壓成
                # 同一個時間，小節長度歸零、BPM 變成天文數字。
                changed.append('整體位移 %+d ms（含小節線；要求 %+d，'
                               '前面只有 %d ms 可移）'
                               % (applied, offset, -applied if applied else 0))
            else:
                changed.append('整體位移 %+d ms（含小節線）' % offset)

        if not changed:
            return
        self.view.rebuild_mapper()
        self.view._update_unit_bounds()
        self.view.update()
        self.view.note_edited.emit()
        self._rebuild_hit_times()
        self.statusBar().showMessage('樂曲總資訊已更新：' + '、'.join(changed), 6000)

    def conform_beats_to_midi_dialog(self) -> None:
        """依參考 MIDI 重定節拍：以 beat index 為共同座標，逐段線性 warp 每個音符
        （縮放小節/拍內音符），並用 MIDI 的節奏取代 beat_data。支援變速。"""
        m = self.view.model
        if not m.notes_tree:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_ref_midi_no_base'))
            return
        if len(m.get_beat_entries()) < 2:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_conform_no_beatdata'))
            return
        if MIDIToXMLConverter is None:
            QMessageBox.critical(self, t('dlg_ref_midi_fail_title'), 'MIDI 模組不可用')
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_conform_pick'), '',
            'Reference (*.xml *.mid *.midi);;XML (*.xml);;MIDI (*.mid *.midi);;All Files (*)')
        if not path:
            return
        new_first_bpm = None
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.xml':
                # 直接讀轉出的 XML 的 beat_data（591 meter-aware entries，最準）
                import xml.etree.ElementTree as _ET
                root = _ET.parse(path).getroot()
                bd = root.find('beat_data')
                if bd is None:
                    raise ValueError('XML 沒有 beat_data')
                midi_pairs = []
                for b in bd.findall('beat'):
                    ie = b.find('index'); me = b.find('start_timing_msec')
                    if ie is not None and me is not None and ie.text and me.text:
                        midi_pairs.append((int(float(ie.text)), int(float(me.text))))
                hdr = root.find('header')
                if hdr is not None:
                    fb = hdr.find('first_bpm')
                    if fb is not None and fb.text:
                        v = float(fb.text)
                        new_first_bpm = v / 100000.0 if v > 10000 else v  # XML 是 ×100000
            else:
                # MIDI：naive 八分音符（meter-unaware，變拍/變速段較不準；建議先轉成 XML 再用）
                import mido
                import io as _io
                import contextlib as _ct
                conv = MIDIToXMLConverter()
                with _ct.redirect_stdout(_io.StringIO()):
                    conv._extract_raw_notes(open_midi(path))
                tpb = int(conv.ticks_per_beat) or 480
                half = max(1, tpb // 2)
                n8 = int(conv.song_end_tick) // half + 2
                midi_pairs = [
                    (k * 1000, int(round(conv._ticks_to_ms(k * half, conv.tempo_map_ticks))))
                    for k in range(n8)
                ]
                if conv.first_bpm:
                    new_first_bpm = float(conv.first_bpm)
        except Exception as e:
            QMessageBox.critical(
                self, t('dlg_ref_midi_fail_title'), t('dlg_ref_midi_fail_msg', e))
            return
        if len(midi_pairs) < 2:
            QMessageBox.information(self, t('dlg_warn'), t('dlg_ref_midi_no_notes'))
            return

        m.push_history()
        n = m.conform_to_midi_tempo(midi_pairs)
        if new_first_bpm:
            m.bpm = float(new_first_bpm)
        m.rebuild_display_cache()
        self.view.rebuild_mapper()
        self.view.scroll_to_top()
        m.dirty = True
        self.view.update()
        self.view.note_edited.emit()
        QMessageBox.information(
            self, t('dlg_conform_ok_title'), t('dlg_conform_ok_msg', n))

    def align_reference_midi_dialog(self) -> None:
        """以參考 MIDI 重建：pitch/時間照 MIDI、左右 lane 依原譜時間局部分布、
        節拍線採用 MIDI tempo。"""
        m = self.view.model
        if not m.notes_tree:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_ref_midi_no_base'))
            return
        if MIDIToXMLConverter is None:
            QMessageBox.critical(self, t('dlg_ref_midi_fail_title'), 'MIDI 模組不可用')
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_ref_midi_pick'), '', 'MIDI (*.mid *.midi);;All Files (*)')
        if not path:
            return
        try:
            import mido
            conv = MIDIToXMLConverter()
            mid = open_midi(path)
            raw = conv._extract_raw_notes(mid)
        except Exception as e:
            QMessageBox.critical(
                self, t('dlg_ref_midi_fail_title'), t('dlg_ref_midi_fail_msg', e))
            return
        if not raw:
            QMessageBox.information(self, t('dlg_warn'), t('dlg_ref_midi_no_notes'))
            return

        m.push_history()
        n = m.rebuild_from_reference_midi(raw)

        # 節拍線：採用 MIDI 的 tempo 重生 beat_data
        try:
            tpb = int(conv.ticks_per_beat) or 480
            end_tick = int(conv.song_end_tick)
            num_beats = (end_tick // tpb) + 2 if end_tick > 0 else 0
            if num_beats > 1 and conv.tempo_map_ticks:
                beats = [
                    conv._ticks_to_ms(i * tpb, conv.tempo_map_ticks)
                    for i in range(num_beats)
                ]
                m.set_beat_grid_ms(beats)
            if conv.first_bpm:
                m.bpm = float(conv.first_bpm)
        except Exception:
            pass

        m.rebuild_display_cache()
        self.view.rebuild_mapper()
        self.view.scroll_to_top()   # 時間軸整個換掉 → 捲回開頭，避免看到空白
        m.dirty = True
        self.view.update()
        self.view.note_edited.emit()
        QMessageBox.information(
            self, t('dlg_ref_midi_ok_title'), t('dlg_ref_midi_ok_msg', n))

    def rearrange_dialog(self) -> None:
        """對目前這份譜重跑一次轉譜，選項自己挑（模式／鍵道範圍／重疊／自訂）。

        已經排過的譜也能再排——音高還在就排得出來，所以調參數看效果不必重新
        匯入 MIDI。會重寫每一顆音符的鍵道，`_arrange_now` 會先 push_history。
        """
        model = self.view.model
        if not any(n.pitch is not None for n in model.notes_tree):
            QMessageBox.information(
                self, t('dlg_warn'),
                '這份譜沒有音高資料（不是從 MIDI 來的），沒辦法重新轉譜。')
            return
        arrange, options = self.ask_arrange_options(
            intro='重新排整份譜面。原本的鍵道會被全部重寫（可以復原）。')
        if not arrange:
            return
        if self._arrange_now(options):
            self.statusBar().showMessage('已重新轉譜', 3000)

    def smart_midi_chart_dialog(self) -> None:
        """Open a MIDI and create a non-destructive, pitch-aware first chart."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            t('dlg_smart_midi_pick'),
            '',
            'MIDI (*.mid *.midi);;All Files (*)',
        )
        if not path:
            return
        try:
            model = NoteModel()
            if not self._load_midi_with_progress(model, path):
                return
            if not model.notes_tree:
                QMessageBox.information(self, t('dlg_warn'), t('dlg_midi_no_notes'))
                return
            # 使用者在轉譜對話框選了「直接平攤」或「先不轉譜」時就沒有統計。
            # 以前這裡會再偷偷跑一次智能排譜，等於把他的選擇推翻掉。
            stats = getattr(model, 'last_smart_chart_stats', None)
            model.json_meta = dict(model.json_meta or {})
            model.json_meta['smart_midi_source'] = os.path.basename(path)
            # The source MIDI must never be overwritten by Ctrl+S. The result
            # is a new chart and should be saved as JSON or XML.
            model.file_format = 'json'
            model.current_file = None
            model.dirty = True
            self._load_model_all(model)
            self._set_note_input_mode(False)
            self._rebuild_hit_times()
            self._refresh_title()
            if stats is None:
                QMessageBox.information(
                    self, t('dlg_smart_midi_ok_title'),
                    '已匯入 %d 顆音符（沒有跑智能排譜）。' % len(model.notes_tree))
                return
            QMessageBox.information(
                self,
                t('dlg_smart_midi_ok_title'),
                t(
                    'dlg_smart_midi_ok_msg',
                    stats.notes,
                    stats.groups,
                    stats.width_two_notes,
                    stats.hand_changes,
                    stats.dp_shifted_groups,
                    stats.context_compressed_groups,
                    stats.global_edge_anchored_groups,
                    stats.unresolved_overlaps,
                    stats.articulation_changes,
                    stats.slide_notes,
                    stats.trill_patterns,
                    stats.motif_reuses,
                    stats.motion_limited_groups,
                    stats.hold_corridor_conflicts,
                    stats.small_interval_restores,
                    stats.small_interval_unresolved,
                ),
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                t('dlg_ref_midi_fail_title'),
                t('dlg_ref_midi_fail_msg', e),
            )

    # ==================================================================
    # 關閉
    # ==================================================================


    #: 可自訂的快捷鍵：設定鍵 -> (顯示名, 要呼叫什麼)
    _SHORTCUT_ACTIONS = (
        ('shortcut_cycle_view', '切換檢視模式', '_cycle_view_mode'),
        ('shortcut_note_input', '切換放置模式', '_toggle_note_input_mode'),
        ('shortcut_play_pause', '播放 / 暫停', '_shortcut_play_pause'),
        ('shortcut_play_full',  '從頭播放全曲', 'play_full'),
        ('shortcut_play_window', '播放目前視窗', 'play_window'),
        ('shortcut_stop',       '停止播放', 'stop_audio'),
        ('shortcut_next_measure', '往後一小節', '_jump_next_measure'),
        ('shortcut_prev_measure', '往前一小節', '_jump_prev_measure'),
        ('shortcut_song_start', '跳到曲首', '_jump_song_start'),
        ('shortcut_song_end',   '跳到最後一顆音符', '_jump_song_end'),
        ('shortcut_dur_shorter', '放置時值：短一級', '_shortcut_dur_shorter'),
        ('shortcut_dur_longer', '放置時值：長一級', '_shortcut_dur_longer'),
        ('shortcut_toggle_hand', '放置手：左右切換', '_shortcut_toggle_hand'),
        ('shortcut_toggle_tap_hold', '選取音符：點擊 / 長條切換', '_shortcut_toggle_tap_hold'),
        ('shortcut_toggle_width', '選取音符：鍵寬 2 / 3 切換', '_shortcut_toggle_width'),
        ('shortcut_measures_bpm', '多小節 BPM / 拍號…', 'change_measures_bpm_dialog'),
        ('shortcut_events', '事件（速度／音效）…', 'edit_events_dialog'),
    )

    # ── 快捷鍵：跳位置 ────────────────────────────────────────────────

    #: 往前一小節時，離小節開頭超過這麼多就先回到本小節開頭（和播放器的
    #: 「上一首」一樣），不然停在小節中間按一下會跳過半個小節。
    _MEASURE_JUMP_SLACK_MS = 30.0

    def _jump_to_ms(self, ms: float) -> None:
        """把判定線移到某一刻。正在播就從那裡接著播，沒在播（含暫停）只捲畫面。

        暫停時只捲畫面就好：「從查看的位置繼續」會在按繼續時接手。
        """
        ms = max(0.0, float(ms))
        if getattr(self, '_is_playing', False) and not self.audio.is_paused():
            self._resume_from(ms)
            return
        for pane in self._visible_panes() or [self.view]:
            pane.follow_to_ms(ms)
            pane.update()
        self.view._emit_status()

    def _jump_measure(self, step: int) -> None:
        m = self.view.model
        total = m.count_measures()
        if total <= 0:
            return
        now = self.view.judge_line_view_ms()
        cur = m.get_measure_at_ms(now)
        start, _end = m.get_measure_time_range(cur)
        if step < 0 and start is not None and now - start > self._MEASURE_JUMP_SLACK_MS:
            target = cur
        else:
            target = cur + step
        target = max(0, min(total - 1, target))
        start, _end = m.get_measure_time_range(target)
        if start is not None:
            self._jump_to_ms(start)
            self.statusBar().showMessage(f'第 {target + 1} / {total} 小節', 1500)

    def _jump_next_measure(self) -> None:
        self._jump_measure(1)

    def _jump_prev_measure(self) -> None:
        self._jump_measure(-1)

    def _jump_song_start(self) -> None:
        self._jump_to_ms(0.0)

    def _jump_song_end(self) -> None:
        notes = self.view.model.notes_tree
        if notes:
            self._jump_to_ms(max(float(n.start) for n in notes))

    # ── 快捷鍵：放置參數 ──────────────────────────────────────────────

    def _shortcut_step_duration(self, longer: bool) -> None:
        """在時值選單裡往長或往短挪一格（照實際長度排，不照選單順序）。"""
        items = self._note_dur_items
        order = sorted(range(len(items)), key=lambda i: items[i][1])
        pos = order.index(self._ni_dur_idx) if self._ni_dur_idx in order else 0
        pos = max(0, min(len(order) - 1, pos + (1 if longer else -1)))
        idx = order[pos]
        self._on_dur_combo_changed(idx)
        self.statusBar().showMessage(f'放置時值：{items[idx][0]}', 1500)

    def _shortcut_dur_shorter(self) -> None:
        self._shortcut_step_duration(longer=False)

    def _shortcut_dur_longer(self) -> None:
        self._shortcut_step_duration(longer=True)

    def _shortcut_toggle_hand(self) -> None:
        idx = 0 if self._ni_hand_idx == 1 else 1
        self._on_hand_combo_changed(idx)
        label = t('tb_note_hand_r') if idx == 0 else t('tb_note_hand_l')
        self.statusBar().showMessage(f'放置手：{label}', 1500)

    def _selection_pane(self):
        """編輯快捷鍵要作用在哪個譜面：有焦點的那個，否則有選取的那個，否則主譜面。"""
        panes = [p for p in (self._visible_panes() or []) if p is not None] or [self.view]
        for pane in panes:
            if pane.hasFocus():
                return pane
        for pane in panes:
            if pane.selected:
                return pane
        return self.view

    def _shortcut_toggle_tap_hold(self) -> None:
        pane = self._selection_pane()
        if not pane.selected:
            self.statusBar().showMessage('先選取音符再切換點擊／長條', 1500)
            return
        target = pane.toggle_tap_hold_selected()
        if target is not None:
            self.statusBar().showMessage(
                '%d 顆改成%s' % (len(pane.selected), '長條' if target == 2 else '點擊'), 1500)

    def _shortcut_toggle_width(self) -> None:
        pane = self._selection_pane()
        if not pane.selected:
            self.statusBar().showMessage('先選取音符再切換鍵寬', 1500)
            return
        target = pane.toggle_width_selected()
        if target is not None:
            self.statusBar().showMessage('%d 顆改成寬 %d' % (len(pane.selected), target), 1500)

    #: 寫死在譜面鍵盤處理（ChartView.keyPressEvent）裡的鍵。只有譜面有焦點時
    #: 才有效，但一樣是那顆按鈕的快捷鍵，要標出來。
    _FIXED_SHORTCUTS = {
        'play_full': ('Ctrl+P',),
        'play_window': ('P',),
        'play_selection': ('Shift+P',),
        'stop_audio': ('S',),
        'zoom_in': ('+',),
        'zoom_out': ('-',),
        'toggle_preview': ('Tab',),
        'start_alloc_section': ('Shift+A',),
    }

    #: 按鈕接的函式和快捷鍵接的函式不是同一個，但做的是同一件事
    _SHORTCUT_ALIASES = {
        '_on_note_input_toggle': '_toggle_note_input_mode',
        '_toggle_pause_resume': '_shortcut_play_pause',
        'pause_audio': '_shortcut_play_pause',
        'resume_audio': '_shortcut_play_pause',
    }

    def _shortcut_names(self, keys) -> Tuple[str, ...]:
        """`keys` 可以是函式名稱、名稱清單，或直接給 slot（取它的函式名）。"""
        if keys is None:
            return ()
        if isinstance(keys, str):
            names = [keys]
        elif isinstance(keys, (list, tuple)):
            names = list(keys)
        else:
            name = getattr(keys, '__name__', '')
            names = [name] if name and name != '<lambda>' else []
        out = []
        for name in names:
            for n in (name, self._SHORTCUT_ALIASES.get(name)):
                if n and n not in out:
                    out.append(n)
        return tuple(out)

    def _shortcut_text(self, names, fixed: bool = True) -> str:
        """這些動作目前綁了哪些鍵，排成「Space / P」這樣。沒綁就是空字串。"""
        user = {method: key for key, _label, method in self._SHORTCUT_ACTIONS}
        seqs = []
        for name in names:
            bound = str(settings.get(user[name], '') or '') if name in user else ''
            if bound:
                seqs.append(bound)
            if fixed:
                seqs.extend(self._FIXED_SHORTCUTS.get(name, ()))
        shown = []
        for seq in seqs:
            text = QKeySequence(seq).toString(QKeySequence.NativeText) or seq
            if text not in shown:
                shown.append(text)
        return ' / '.join(shown)

    def _label_shortcut(self, target, text: str, tip: str, keys, style: str) -> None:
        """登記一個要標快捷鍵的按鈕／選單項目／下拉框，並馬上畫一次。

        style：'button' 文字後面加（鍵）；'menu' 用 Tab 分隔，Qt 會把它靠右
        對齊在快捷鍵那一欄；'tooltip' 只寫進提示（下拉框的文字是選項，不能動）。
        偏好設定改了快捷鍵之後 `_refresh_shortcut_labels` 會全部重畫。
        """
        # 只登記「可能有快捷鍵」的動作。沒有的不要碰它的文字：像 MIDI 鋼琴開關
        # 和音量旁邊那顆喇叭共用同一個 QAction，文字由 `_refresh_vol_rows` 換成
        # 🔊／🔇，這裡重畫一次就會把喇叭蓋成「MIDI 鋼琴」。
        user = {method for _key, _label, method in self._SHORTCUT_ACTIONS}
        names = tuple(n for n in self._shortcut_names(keys)
                      if n in user or n in self._FIXED_SHORTCUTS)
        if not names:
            return
        target.setProperty('sc_text', text)
        target.setProperty('sc_tip', tip or '')
        target.setProperty('sc_names', list(names))
        target.setProperty('sc_style', style)
        if not hasattr(self, '_shortcut_labeled'):
            self._shortcut_labeled = []
        if target not in self._shortcut_labeled:
            self._shortcut_labeled.append(target)
        self._render_shortcut_label(target)

    def _set_labeled_text(self, target, text: str, tip: Optional[str] = None) -> None:
        """換掉按鈕的文字（例如暫停↔繼續），快捷鍵標示保留。"""
        if target.property('sc_names'):
            target.setProperty('sc_text', text)
            if tip is not None:
                target.setProperty('sc_tip', tip)
            self._render_shortcut_label(target)
            return
        target.setText(text)
        if tip is not None:
            target.setToolTip(tip)

    def _render_shortcut_label(self, target) -> None:
        text = target.property('sc_text') or ''
        tip = target.property('sc_tip') or ''
        style = target.property('sc_style')
        names = target.property('sc_names') or []
        # 選單的文字裡已經寫了固定鍵（例如「▶ 播放整首  Ctrl+P」），那邊只補可自訂的
        keys = self._shortcut_text(names, fixed=(style != 'menu'))
        if style == 'button':
            target.setText(f'{text} ({keys})' if keys else text)
        elif style == 'menu':
            target.setText(f'{text}\t{keys}' if keys else text)
        if style in ('button', 'tooltip'):
            line = f'快捷鍵：{keys}' if keys else ''
            target.setToolTip('\n'.join(x for x in (tip, line) if x) or text)

    def _refresh_shortcut_labels(self) -> None:
        from PyQt5 import sip
        alive = []
        for target in getattr(self, '_shortcut_labeled', ()):
            if sip.isdeleted(target):
                continue                      # 獨立視窗那條工具列被收掉了
            self._render_shortcut_label(target)
            alive.append(target)
        self._shortcut_labeled = alive

    def _shortcut_play_pause(self) -> None:
        """一個鍵管播放與暫停：沒在播就從目前視窗開始，正在播就暫停/繼續。"""
        if getattr(self, '_is_playing', False):
            self._toggle_pause_resume()
        else:
            self.play_window()

    def apply_shortcut_settings(self) -> None:
        """依偏好設定重新綁定快捷鍵。設定改完會即時呼叫，不必重開。"""
        from PyQt5.QtWidgets import QShortcut

        for sc in getattr(self, '_user_shortcuts', ()):
            sc.setEnabled(False)
            sc.setParent(None)
        self._user_shortcuts = []
        for key, _label, method in self._SHORTCUT_ACTIONS:
            seq = QKeySequence(str(settings.get(key, '') or ''))
            if seq.isEmpty():
                continue
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(getattr(self, method))
            self._user_shortcuts.append(sc)
        self._refresh_shortcut_labels()

    def _toggle_note_input_mode(self) -> None:
        """快捷鍵用：切換放置模式，並讓工具列按鈕跟著更新。"""
        on = not bool(getattr(self.view, '_note_input_mode', False))
        self.view.set_note_input_mode(on)
        self._sync_toolbars()
        self._refresh_toolbar_groups()

    def open_preferences_dialog(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec_() == SettingsDialog.Accepted:
            # 套用滾輪方向設定
            scroll_inv = bool(settings.get('scroll_invert', False))
            for v in self._panes:
                v.scroll_invert = scroll_inv
            self._act_inv.setChecked(scroll_inv)
            # 關掉操作紀錄的話，手上那個沒結算的動作就別留著了
            self._flush_oplog()
            oplog.configure(settings.get('oplog_enabled', True))
            self._configure_autosave()
            self._sync_view_settings_ui()
            self.view._emit_status()

    def _sync_view_settings_ui(self) -> None:
        """偏好設定改完之後，把選單的勾選狀態與畫面一起對齊。

        同一個設定在偏好設定和「檢視」選單都改得到，不同步的話選單上的勾勾會
        和實際行為對不起來。
        """
        pairs = (
            ('_act_vel_num', 'pitch_velocity_numbers', True),
            ('_act_dyn_lane', 'pitch_dynamics_lane', True),
            ('_act_scale_lock', 'pitch_scale_lock', False),
            ('_act_ghost', 'ghost_other_hand', True),
            ('_act_vel_shade', 'pitch_velocity_shading', True),
            ('_act_statusbar', 'show_statusbar', False),
        )
        for attr, key, default in pairs:
            act = getattr(self, attr, None)
            if act is None:
                continue
            want = bool(settings.get(key, default))
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
        act = getattr(self, '_act_scale_hl', None)
        if act is not None:
            want = str(settings.get('pitch_column_mode', 'blackwhite')) == 'scale'
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
        self.statusBar().setVisible(bool(settings.get('show_statusbar', False)))
        self._set_pitch_numbering(bool(settings.get('show_midi_pitch', False)),
                                  save=False)
        try:
            self.audio.set_output_latency_ms(int(settings.get('audio_latency_ms', 0)))
        except Exception:
            pass
        for v in self._panes:
            v.update()

    def _set_pitch_column_mode(self, mode: str) -> None:
        """切換音高模式的欄位分色，馬上重畫。

        選單和偏好設定的下拉是同一個設定的兩個入口，所以兩邊都走這裡。
        """
        settings.set('pitch_column_mode', mode)
        for pane in self._panes:
            pane.update()

    # ── 自動儲存 ──────────────────────────────────────────────────────

    def _configure_autosave(self) -> None:
        """依偏好設定開關計時器。設定改完會即時呼叫。"""
        minutes = int(settings.get('autosave_interval_min',
                                   autosave.DEFAULT_INTERVAL_MIN) or 0)
        if settings.get('autosave_enabled', True) and minutes > 0:
            self._autosave_timer.start(max(1, minutes) * 60_000)
        else:
            self._autosave_timer.stop()

    def _on_autosave_tick(self) -> None:
        """時間到：有沒存的改動就寫一份備份。

        三種時候先不存、下一輪再說：
          * **播放中**——存 XML 要幾百毫秒，判定線會頓一下；停下來時補存。
          * **有對話框開著**——工具可能正在背景執行緒改這份譜，存到一半的
            狀態沒意義。
          * **滑鼠按著**——正在拖曳音符，拖完再存。
        """
        if not settings.get('autosave_enabled', True):
            return
        model = self.view.model
        if not getattr(model, 'dirty', False) or not model.notes_tree:
            return
        if (self._is_playing
                or QApplication.activeModalWidget() is not None
                or QApplication.mouseButtons() != Qt.NoButton):
            self._autosave_pending = True
            return
        self._autosave_pending = False
        try:
            autosave.write_backup(model, self._autosave_session)
        except Exception:                       # noqa: BLE001
            logging.exception('autosave failed')
            return
        self.statusBar().showMessage(
            '已自動備份（%s）' % time.strftime('%H:%M'), 4000)

    def _autosave_if_pending(self) -> None:
        """剛才因為播放而跳過的那一次，停下來就補上。"""
        if self._autosave_pending:
            QTimer.singleShot(0, self._on_autosave_tick)

    def _discard_autosave(self, *sources: Optional[str]) -> None:
        for source in sources or (self.view.model.current_file,):
            try:
                autosave.discard_backup(source, self._autosave_session)
            except Exception:                   # noqa: BLE001
                logging.exception('discard autosave failed')

    def offer_crash_recovery(self) -> None:
        """啟動時發現上次沒正常結束留下的備份，問要不要還原。"""
        try:
            backups = autosave.list_backups()
        except Exception:                       # noqa: BLE001
            logging.exception('list autosave failed')
            return
        if not backups:
            return
        lines = '\n'.join('　' + autosave.describe(b) for b in backups[:6])
        more = '\n　…另外還有 %d 份' % (len(backups) - 6) if len(backups) > 6 else ''
        box = QMessageBox(self)
        box.setWindowTitle('找到自動備份')
        box.setText('上次編輯器沒有正常關閉，留下了還沒存檔的備份：\n\n%s%s'
                    % (lines, more))
        restore = box.addButton('還原…', QMessageBox.AcceptRole)
        delete = box.addButton('刪除這些備份', QMessageBox.DestructiveRole)
        box.addButton('稍後再說', QMessageBox.RejectRole)
        box.setDefaultButton(restore)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is delete:
            for info in backups:
                for path in (info.data_path, info.meta_path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return
        if clicked is not restore:
            return
        chosen = backups[0]
        if len(backups) > 1:
            labels = [autosave.describe(b) for b in backups]
            label, ok = QInputDialog.getItem(
                self, '找到自動備份', '要還原哪一份？', labels, 0, False)
            if not ok:
                return
            chosen = backups[labels.index(label)]
        self._open_backup(chosen)

    def _open_backup(self, info) -> None:
        try:
            model = NoteModel()
            autosave.load_backup(info, model)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, t('dlg_load_fail_title'),
                                 t('dlg_load_fail_msg', e))
            return
        self._install_loaded_model(model, info.source or info.data_path)
        self.statusBar().showMessage('已從自動備份還原，記得存檔', 6000)

    def _flush_oplog(self) -> None:
        """把還沒結算的那個動作寫出去（見 oplog.flush）。"""
        if not oplog.enabled:
            return
        try:
            oplog.flush(self.view.model)
        except Exception:                       # noqa: BLE001
            logging.exception('oplog flush failed')

    def closeEvent(self, event: QCloseEvent) -> None:
        self._judge_timer.stop()
        self._title_timer.stop()
        self._oplog_timer.stop()
        self._flush_oplog()
        if self.view.model.dirty:
            reply = QMessageBox.question(
                self, t('dlg_unsaved_title'), t('dlg_unsaved_msg'),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply == QMessageBox.Save:
                self.save_file()
                # 存不成就不能關。以前是無條件 accept()，於是存檔被擋下來
                # （現在有無主音會擋）或使用者在「另存新檔」按取消時，視窗
                # 照關、改動直接消失。用 dirty 判斷有沒有真的存到。
                if self.view.model.dirty:
                    self._title_timer.start(500)
                    if self._is_playing:
                        self._judge_timer.start()
                    event.ignore()
                    return
                event.accept()
            elif reply == QMessageBox.Discard:
                # 使用者明確選了不儲存：備份也要一起丟掉，下次開檔不再問
                self._discard_autosave()
                event.accept()
            else:
                # 使用者取消：恢復計時器
                self._title_timer.start(500)
                if self._is_playing:
                    self._judge_timer.start()
                event.ignore()
        else:
            self.audio.stop()
            self._is_playing = False
            event.accept()
        if event.isAccepted():
            self._autosave_timer.stop()
            # 獨立視窗是頂層視窗，不關掉的話應用程式不會結束
            self._close_detached()
            if self._midi_preview_synth is not None:
                self._midi_preview_synth.close()
                self._midi_preview_synth = None
