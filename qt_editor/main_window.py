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
Tools    : Alloc Section、Resolve Overlaps、調整 BPM/Beats/Offset
View     : 縮放、捲動反向
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer
import logging
from PyQt5.QtGui import QKeySequence, QCloseEvent
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QSizePolicy, QSlider,
    QStatusBar, QToolBar, QVBoxLayout, QWidget,
    QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QRadioButton,
)

from .models import NoteModel
from .chart_view import ChartView
from .audio_player import AudioPlayer
from .i18n import t
from .settings import settings
from .settings_dialog import SettingsDialog
from .new_chart_dialog import NewChartDialog
from .playback_offset_dialog import PlaybackOffsetDialog
from .export_song_dialog import ExportSongDialog, SONGS_ROOT

# 可選：MIDI 轉換器
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


class MainWindow(QMainWindow):
    """頂層主視窗（完整功能版）。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(t('wnd_title'))
        self.resize(1280, 860)

        # 套用儲存的滾輪方向設定

        # ── 中央編輯區 ───────────────────────────────────────────────
        self.view = ChartView(self)
        self.setCentralWidget(self.view)

        # ── 音訊播放器 ────────────────────────────────────────────────
        self.audio = AudioPlayer(self)
        self._dual_audio_enabled: bool = False
        self._play_start_ms: float = 0.0
        self._play_end_ms:   float = 0.0
        self._is_playing:    bool  = False
        self._playback_offset_ms: int = 0       # 播放偏移（正=提前，負=延後）
        self._playback_offset_advance: bool = True  # 上次選的方向

        # ── judge line 更新計時器 ─────────────────────────────────────
        self._judge_timer = QTimer(self)
        self._judge_timer.setInterval(16)
        self._judge_timer.timeout.connect(self._on_judge_tick)

        # ── 打擊聲 ────────────────────────────────────────────────────
        self._hit_sound_persistent: bool = False   # 使用者勾選的持久開關
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

        # ── 信號連接 ──────────────────────────────────────────────────
        self.view.selection_changed.connect(self._on_selection_changed)
        self.view.status_changed.connect(self._on_status_changed)
        self.view.note_edited.connect(self._on_note_edited)
        self.view.play_requested.connect(self._play_range)
        self.view.play_full_requested.connect(self.play_full)
        self.view.play_from_window_requested.connect(self.play_from_window)
        self.view.stop_requested.connect(self.stop_audio)
        self.view.pause_requested.connect(self.pause_audio)
        self.view.resume_requested.connect(self.resume_audio)
        self.view.note_input_changed.connect(self._on_note_input_mode_changed)
        self.view.set_measure_bpm_requested.connect(self.set_measure_bpm_at)
        self.view.set_measure_time_sig_requested.connect(self.set_measure_time_sig_at)

        self.audio.playback_stopped.connect(self._on_playback_stopped)
        # Ensure audio mode label exists
        try:
            self._lbl_audio_mode = QLabel('')
            self.statusBar().addPermanentWidget(self._lbl_audio_mode)
        except Exception:
            self._lbl_audio_mode = None
        # ── 放置音符模式狀態 ───────────────────────────────────
        self._note_dur_items = [
            ('全音符',   4.0),
            ('二分音符',   2.0),
            ('四分音符',   1.0),
            ('八分音符',   0.5),
            ('16分音符',  0.25),
            ('32分音符',  0.125),
            ('64分音符',  0.0625),
        ]
        # ── 建立 UI ───────────────────────────────────────────────────
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()

        # ── 定時刷新 title ────────────────────────────────────────────
        self._title_timer = QTimer(self)
        self._title_timer.timeout.connect(self._refresh_title)
        self._title_timer.start(500)
        # Give initial keyboard focus to the chart view so shortcuts (eg. Tab)
        # are immediately active without requiring a mouse click.
        try:
            self.view.setFocus()
        except Exception:
            pass

    # ==================================================================
    # 選單建立
    # ==================================================================

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
        file_m.addSeparator()
        self._add_action(file_m, t('action_save'), self.save_file, QKeySequence.Save)
        self._add_action(file_m, t('action_save_as'), self.save_file_as, 'Ctrl+Shift+S')
        self._add_action(file_m, t('action_save_json'), self.save_as_json, 'Ctrl+Shift+J')
        self._add_action(file_m, t('action_export_song'), self.export_song)
        file_m.addSeparator()
        self._add_action(file_m, t('action_quit'), self.close, QKeySequence.Quit)

        # ── 編輯 ──────────────────────────────────────────────────────
        edit_m = mb.addMenu(t('menu_edit'))
        self._add_action(edit_m, t('action_undo'), self.view.undo, QKeySequence.Undo)
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_select_all'), self.view.select_all, QKeySequence.SelectAll)
        self._add_action(edit_m, t('action_deselect'), self.view.deselect_all)
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_delete'), self.view.delete_selected, QKeySequence.Delete)
        self._add_action(edit_m, t('action_duplicate'), self.view.duplicate_selected)
        edit_m.addSeparator()
        self._add_action(edit_m, t('action_copy'), self.view.copy_to_clipboard, QKeySequence.Copy)
        self._add_action(edit_m, t('action_paste'), self.view.paste_from_clipboard, QKeySequence.Paste)
        edit_m.addSeparator()

        # 寬度
        self._add_action(edit_m, t('action_width2'), lambda: self.view.set_width_selected(2))
        self._add_action(edit_m, t('action_width3'), lambda: self.view.set_width_selected(3))
        edit_m.addSeparator()

        # ── 音符類型（直接展開，不再藏子選單）
        self._add_action(edit_m, t('action_type_tap'), lambda: self.view.set_type_selected(0))
        self._add_action(edit_m, t('action_type_soft'), lambda: self.view.set_type_selected(1))
        self._add_action(edit_m, t('action_type_long'), lambda: self.view.set_type_selected(2))
        self._add_action(edit_m, t('action_type_staccato'), lambda: self.view.set_type_selected(3))
        edit_m.addSeparator()

        # ── 左右手（直接展開）
        self._add_action(edit_m, t('action_right_hand'), lambda: self.view.set_hand_selected(0))
        self._add_action(edit_m, t('action_left_hand'), lambda: self.view.set_hand_selected(1))
        edit_m.addSeparator()

        self._add_action(edit_m, t('action_shift_pitch'), self.shift_pitch_dialog)

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
        self._act_hit.setChecked(False)
        self._act_hit.triggered.connect(lambda checked: setattr(self, '_hit_sound_persistent', checked))
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

        # 工具
        tools_m = mb.addMenu(t('menu_tools'))
        self._add_action(tools_m, t('action_auto_sort'),        self.view.start_alloc_section)
        self._add_action(tools_m, t('action_resort_all'),       self.view.resort_all_notes)
        self._add_action(tools_m, t('action_resolve_overlaps'), self.resolve_overlaps_dialog)
        tools_m.addSeparator()
        self._add_action(tools_m, t('action_adjust_bpm'),    self.adjust_bpm_dialog)
        self._add_action(tools_m, t('action_adjust_beats'),  self.adjust_beats_dialog)
        self._add_action(tools_m, t('action_adjust_offset'), self.adjust_offset_dialog)
        tools_m.addSeparator()
        self._add_action(tools_m, t('action_add_measure'),    self.add_measure_dialog)
        self._add_action(tools_m, t('action_delete_measure'), self.delete_measure_dialog)
        self._add_action(tools_m, t('action_set_measure_bpm'), self.set_measure_bpm_dialog)
        # 修改小節拍號（numerator/denominator）
        try:
            self._add_action(tools_m, t('action_set_measure_time_signature'), self.set_measure_time_sig_dialog)
        except Exception:
            # fallback label if i18n key not present
            self._add_action(tools_m, '修改小節拍號…', self.set_measure_time_sig_dialog)
        # 移除相同 startTime 且相同 pitch 的短音符（保留最長者）
        try:
            self._add_action(tools_m, '移除重複音符（同 start/pitch）', self.remove_duplicate_start_pitch_dialog)
        except Exception:
            self._add_action(tools_m, '移除重複音符…', self.remove_duplicate_start_pitch_dialog)

        # 檢視
        view_m = mb.addMenu(t('menu_view'))
        self._add_action(view_m, t('action_zoom_in'),  lambda: self.view.zoom(0.5), '=')
        self._add_action(view_m, t('action_zoom_out'), lambda: self.view.zoom(2.0), '-')
        view_m.addSeparator()
        self._act_inv = QAction(t('action_scroll_invert'), self, checkable=True)
        self._act_inv.setChecked(bool(settings.get('scroll_invert', False)))
        self._act_inv.triggered.connect(self._toggle_scroll_invert)
        view_m.addAction(self._act_inv)

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

    def _add_action(self, menu, label: str, slot, shortcut=None) -> QAction:
        act = QAction(label, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    # ==================================================================
    # 工具列
    # ==================================================================

    def _build_toolbar(self) -> None:
        tb: QToolBar = self.addToolBar(t('tb_main'))
        tb.setMovable(False)

        # ── 新增譜面按鈕 ──────────────────────────────────────────────
        self._tb_new_act = QAction(t('tb_new_chart'), self)
        self._tb_new_act.setToolTip(t('action_new_chart'))
        self._tb_new_act.triggered.connect(self.new_chart_dialog)
        tb.addAction(self._tb_new_act)

        tb.addAction(t('tb_open'), self.open_file)
        tb.addAction(t('tb_save'), self.save_file)
        tb.addSeparator()
        tb.addAction(t('tb_undo'), self.view.undo)
        tb.addSeparator()

        # ── 放置音符模式區塊 ──────────────────────────────────────────
        self._tb_note_input_act = QAction(t('tb_note_input'), self, checkable=True)
        self._tb_note_input_act.setToolTip(t('tb_note_input_tip'))
        self._tb_note_input_act.toggled.connect(self._on_note_input_toggle)
        tb.addAction(self._tb_note_input_act)

        # 音符時值下拉選單
        self._dur_combo = QComboBox()
        self._dur_combo.setFixedWidth(88)
        for name, _ in self._note_dur_items:
            self._dur_combo.addItem(name)
        self._dur_combo.setCurrentIndex(2)   # 預設四分音符
        self._dur_combo.currentIndexChanged.connect(self._on_dur_combo_changed)
        self._dur_combo.setToolTip('音符時值（放置音符模式下生效）')
        tb.addWidget(self._dur_combo)

        # 手按鈕（右手 / 左手）
        self._hand_combo = QComboBox()
        self._hand_combo.setFixedWidth(56)
        self._hand_combo.addItem(t('tb_note_hand_r'))
        self._hand_combo.addItem(t('tb_note_hand_l'))
        self._hand_combo.currentIndexChanged.connect(
            lambda i: self.view.set_note_input_hand(i)
        )
        self._hand_combo.setToolTip('放置音符的預設手')
        tb.addWidget(self._hand_combo)

        # 放置寬度下拉（1..6）
        self._width_combo = QComboBox()
        self._width_combo.setFixedWidth(72)
        for w in range(1, 7):
            self._width_combo.addItem(f'寬度 {w}')
        self._width_combo.setCurrentIndex(2)  # 預設 3
        self._width_combo.currentIndexChanged.connect(self._on_width_combo_changed)
        self._width_combo.setToolTip('放置音符的預設寬度（格數）')
        tb.addWidget(self._width_combo)

        # 放置音符類型下拉
        self._type_combo = QComboBox()
        self._type_combo.setFixedWidth(120)
        self._type_combo.addItem('Tap  (T)')
        self._type_combo.addItem('Soft')
        self._type_combo.addItem('Long  (H)')
        self._type_combo.addItem('Staccato  (K)')
        self._type_combo.setCurrentIndex(0)
        self._type_combo.currentIndexChanged.connect(self._on_type_combo_changed)
        self._type_combo.setToolTip('放置音符的預設類型')
        tb.addWidget(self._type_combo)
        # 同步初始值到 view
        self._on_dur_combo_changed(self._dur_combo.currentIndex())
        self._on_width_combo_changed(self._width_combo.currentIndex())
        self._on_type_combo_changed(self._type_combo.currentIndex())

        tb.addSeparator()

        # ── 小節操作按鈕 ──────────────────────────────────────────────
        tb.addAction(t('tb_add_measure'),    self.add_measure_dialog)
        tb.addAction(t('tb_delete_measure'), self.delete_measure_dialog)
        tb.addAction(t('tb_set_measure_bpm'), self.set_measure_bpm_dialog)
        # 多小節 BPM 設定按鈕
        act_multi_bpm = QAction('多小節 BPM', self)
        act_multi_bpm.setToolTip('設定從某小節到某小節的 BPM')
        act_multi_bpm.triggered.connect(self.change_measures_bpm_dialog)
        tb.addAction(act_multi_bpm)
        tb.addSeparator()

        tb.addAction(t('tb_auto_sort'), self.view.start_alloc_section)
        tb.addSeparator()
        tb.addAction(t('tb_zoom_out'), lambda: self.view.zoom(2.0))
        tb.addAction(t('tb_zoom_in'),  lambda: self.view.zoom(0.5))
        tb.addSeparator()
        tb.addAction(t('tb_play_full'), self.play_full)
        tb.addAction(t('tb_play'),  self.play_window)
        tb.addAction(t('tb_stop'),  self.stop_audio)
        self._tb_pause_act = QAction(t('tb_pause'), self)
        self._tb_pause_act.triggered.connect(self._toggle_pause_resume)
        tb.addAction(self._tb_pause_act)
        tb.addSeparator()
        self._tb_hit_act = QAction(t('tb_hit_sound'), self, checkable=True)
        self._tb_hit_act.setToolTip(t('tb_hit_sound_tip'))
        self._tb_hit_act.toggled.connect(self._on_hit_toggle)
        tb.addAction(self._tb_hit_act)
        tb.addSeparator()
        # ── 播放偏移按鈕 ──────────────────────────────────────────────
        self._tb_offset_act = QAction(t('tb_offset'), self)
        self._tb_offset_act.setToolTip(t('tb_offset_tip'))
        self._tb_offset_act.triggered.connect(self._show_offset_dialog)
        tb.addAction(self._tb_offset_act)
        self._lbl_offset = QLabel(t('status_offset_none'))
        self._lbl_offset.setStyleSheet('font-size: 11px; margin: 0 4px;')
        tb.addWidget(self._lbl_offset)
        tb.addSeparator()
        # Append visible shortcut hint to toolbar label
        self._act_preview = QAction(t('tb_preview') + ' (Tab)', self, checkable=True)
        # Show shortcut hint on toolbar tooltip
        try:
            tip_text = t('tb_preview_tip') + ' (Tab)'
        except Exception:
            tip_text = t('tb_preview_tip')
        self._act_preview.setToolTip(tip_text)
        self._act_preview.toggled.connect(self.view.toggle_preview_mode)
        tb.addAction(self._act_preview)
        self._act_time_uniform = QAction(t('tb_time_uniform'), self, checkable=True)
        self._act_time_uniform.setToolTip(t('tb_time_uniform_tip'))
        self._act_time_uniform.toggled.connect(self._on_time_uniform_toggle)
        tb.addAction(self._act_time_uniform)
        tb.addSeparator()
        # ── 音量區塊：彈性間距把它推到工具列右端 ───────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addWidget(self._make_vol_block())

    def _make_vol_block(self) -> QWidget:
        """將音樂音量、打擊音量兩条滑桿縱向疊放，標籤靠左對齊。"""
        block = QWidget()
        vbox = QVBoxLayout(block)
        vbox.setContentsMargins(4, 2, 8, 2)
        vbox.setSpacing(2)
        vbox.addWidget(self._make_vol_row(t('tb_music_vol'), 100, self._on_music_vol_changed))
        # second audio source volume (visible even if not loaded)
        try:
            lbl2 = t('tb_music2_vol')
        except Exception:
            lbl2 = '音源2 音量'
        vbox.addWidget(self._make_vol_row(lbl2, 100, self._on_music2_vol_changed))
        vbox.addWidget(self._make_vol_row(t('tb_hit_vol'),   100, self._on_hit_vol_changed))
        return block

    def _make_vol_row(self, label_text: str, default_val: int, callback) -> QWidget:
        """單行：靠左標籤 + 滑桿。"""
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        lbl = QLabel(label_text)
        lbl.setStyleSheet('font-size: 11px;')
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lbl.setFixedWidth(62)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(default_val)
        slider.setFixedWidth(90)
        slider.setToolTip(f'{label_text}: {default_val}%')
        def _on_change(val: int) -> None:
            slider.setToolTip(f'{label_text}: {val}%')
            callback(val)
        slider.valueChanged.connect(_on_change)
        hbox.addWidget(lbl)
        hbox.addWidget(slider)
        return row

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
        self._lbl_status.setText(msg)

    def _on_note_edited(self) -> None:
        self._refresh_title()
        # Keep hit-time cache consistent after any model edit (including undo).
        self._rebuild_hit_times()

    def _refresh_title(self) -> None:
        m = self.view.model
        fname = os.path.basename(m.current_file) if m.current_file else t('wnd_no_file')
        dirty = ' *' if m.dirty else ''
        self.setWindowTitle(f"{t('wnd_title')}  —  {fname}{dirty}")

    def _toggle_scroll_invert(self, checked: bool) -> None:
        self.view.scroll_invert = checked
        self.view._emit_status()

    # ── 放置音符模式 ──────────────────────────────────────────────────

    def _on_note_input_toggle(self, checked: bool) -> None:
        """工具列「放置模式」按鈕切換。"""
        self.view.set_note_input_mode(checked)

    def _on_note_input_mode_changed(self, enabled: bool) -> None:
        """ChartView 主動改變 note_input_mode 時（如按 Esc）同步工具列按鈕狀態。"""
        self._tb_note_input_act.setChecked(enabled)

    def _on_dur_combo_changed(self, idx: int) -> None:
        """音符時值下拉選單改變。"""
        if 0 <= idx < len(self._note_dur_items):
            _, beats = self._note_dur_items[idx]
            self.view.set_note_duration(beats)

    def _on_width_combo_changed(self, idx: int) -> None:
        """放置寬度下拉選單改變。"""
        if idx >= 0:
            self.view.set_note_input_width(idx + 1)

    def _on_type_combo_changed(self, idx: int) -> None:
        """放置音符類型下拉選單改變。"""
        if idx >= 0:
            self.view.set_note_input_note_type(idx)

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
            self.view.load_model(model)
            self._rebuild_hit_times()
            self._refresh_title()
            # 自動進入放置音符模式
            self._tb_note_input_act.setChecked(True)
            self.view.set_note_input_mode(True)
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    # ==================================================================
    # 小節操作
    # ==================================================================

    def add_measure_dialog(self) -> None:
        """新增小節：詢問新小節的 BPM，然後在末尾追加。"""
        m = self.view.model
        if m.root is None:
            QMessageBox.warning(self, t('dlg_warn'),
                                '目前沒有開啟的譜面，或譜面不包含 beat_data。')
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

    def delete_measure_dialog(self) -> None:
        """刪除小節：根據目前視窗中央所在的小節，彈出確認後刪除。"""
        m = self.view.model
        if m.root is None:
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        beats = m.get_beat_entries()
        if not beats:
            QMessageBox.warning(self, t('dlg_warn'),
                                t('dlg_delete_measure_no_data'))
            return

        # 用視窗中央的 unit 決定要刪的小節（與 barline 顯示一致）
        center_unit = self.view.window_start_unit + self.view.window_size_unit / 2.0
        center_ms   = self.view.mapper.unit_to_ms(center_unit)
        measure_idx = m.get_measure_at_ms(center_ms)
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
            m.delete_measure(measure_idx)
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.selected.clear()
            self.view.update()
            self.view.selection_changed.emit(0)
            self._rebuild_hit_times()
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
        hint.setStyleSheet('color: #666; font-size: 11px')
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

    def change_measures_bpm_dialog(self) -> None:
        """設定多個小節的 BPM（指定起始小節與結束小節，以 1-based 輸入）。"""
        m = self.view.model
        if not m.get_beat_entries():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_delete_measure_no_data'))
            return
        total_measures = len(m.get_beat_entries())
        from PyQt5.QtWidgets import (
            QDialog, QFormLayout, QSpinBox, QDoubleSpinBox, QDialogButtonBox,
            QVBoxLayout, QHBoxLayout, QRadioButton, QLabel, QCheckBox
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
        form.addRow('拍號 分子 (numerator):', num_spin)
        form.addRow('拍號 分母 (denominator):', den_spin)

        vbox.addLayout(form)

        apply_ts_chk = QCheckBox('同時修改拍號 (應用於上述小節範圍)')
        vbox.addWidget(apply_ts_chk)

        vbox.addWidget(QLabel('小節內音符重排方式（拍號變更時）：'))
        hb = QHBoxLayout()
        rb_uniform = QRadioButton('均分小節內拍子（等距）')
        rb_preserve = QRadioButton('保留相對位置（只縮放）')
        rb_uniform.setChecked(True)
        hb.addWidget(rb_uniform)
        hb.addWidget(rb_preserve)
        vbox.addLayout(hb)

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

        s_idx = start - 1
        e_idx = end - 1
        try:
            m.push_history()
            for mi in range(s_idx, e_idx + 1):
                try:
                    m.set_measure_bpm(mi, bpm, uniform=bool(self.view.time_uniform))
                except Exception:
                    continue
                if apply_ts:
                    try:
                        m.set_measure_time_signature(mi, new_num, new_den,
                                                     uniform=uniform_choice,
                                                     time_uniform=bool(self.view.time_uniform))
                    except Exception:
                        continue
            self.view.rebuild_mapper()
            self.view._update_unit_bounds()
            self.view.update()
            self._rebuild_hit_times()
            self.view.note_edited.emit()
            QMessageBox.information(self, '完成', f'已將第 {start} 到第 {end} 小節的 BPM 設為 {bpm:.2f}' + (f'，並修改拍號為 {new_num}/{new_den}' if apply_ts else ''))
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

    # ==================================================================
    # 檔案操作
    # ==================================================================

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_open_title'), '', t('dlg_file_filter'),
        )
        if path:
            self._load_path(path)

    def _open_midi_hand(self, hand: int) -> None:
        """開啟 MIDI，將其中**所有**音符視為指定手（0=右 1=左），合併進目前譜面。"""
        if not _HAS_MIDI_CONV:
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
            self.view.selected.clear()
            self.view.update()
            self.view.selection_changed.emit(0)
            self._rebuild_hit_times()
            msg_key = 'dlg_midi_right_done' if hand == 0 else 'dlg_midi_left_done'
            QMessageBox.information(self, t('dlg_save_ok_title'), t(msg_key, len(new_notes)))
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def _load_path(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        try:
            model = NoteModel()
            if ext == '.json':
                model.load_json(path)
            elif ext in ('.mid', '.midi'):
                if not _HAS_MIDI_CONV:
                    QMessageBox.warning(self, t('dlg_warn'), t('dlg_midi_no_conv'))
                    return
                xml_out = os.path.splitext(path)[0] + '_converted.xml'
                MIDIToXMLConverter().convert_midi_to_xml(path, xml_out, resolve_overlaps=False)
                model.load_xml(xml_out)
                model.current_file = xml_out
            else:
                model.load_xml(path)
            self.view.load_model(model)
            # 載入檔案時關閉放置音符模式
            self._tb_note_input_act.setChecked(False)
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
            self._refresh_title()
        except Exception as e:
            QMessageBox.critical(self, t('dlg_load_fail_title'), t('dlg_load_fail_msg', e))

    def save_file(self) -> None:
        if not self.view.model.current_file:
            self.save_file_as()
            return
        self._do_save(self.view.model.current_file)

    def save_file_as(self) -> None:
        m = self.view.model
        default = m.current_file or ''
        if not default and hasattr(m, '_song_name') and m._song_name:
            default = m._song_name + '.xml'
        path, _ = QFileDialog.getSaveFileName(
            self, t('dlg_save_as_title'),
            default,
            'XML (*.xml);;JSON (*.json)',
        )
        if path:
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

    def _do_save(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        try:
            m = self.view.model
            if ext == '.json':
                m.save_json(path)
                actual = path
            else:
                if m.root is None:
                    actual = os.path.splitext(path)[0] + '.json'
                    m.save_json(actual)
                else:
                    m.save_xml(path)
                    actual = path
            self._refresh_title()
            QMessageBox.information(self, t('dlg_save_ok_title'), t('dlg_save_ok_msg', actual))
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
        self.view.set_follow_mode(True)
        self.view.follow_to_ms(0.0)
        self.view.update()
        self._play_range(0.0, self._audio_total_ms(), follow=True)

    def play_from_window(self) -> None:
        """從目前視窗底部（最低可見時間）播到曲末，不自動停止。"""
        ws_ms, _ = self.view._window_ms()
        self._play_range(ws_ms, self._audio_total_ms(), follow=True)

    def play_window(self) -> None:
        self.play_from_window()

    def play_window_hit(self) -> None:
        self.play_from_window()

    def play_selection(self) -> None:
        self.view._emit_play_selection()

    def play_selection_hit(self) -> None:
        self.view._emit_play_selection()

    def _play_range(self, start_ms: float, end_ms: float, follow: bool = False) -> None:
        # 載入 WAV 檔案檢查
        if not self.audio.is_loaded():
            reply = QMessageBox.question(
                self, t('dlg_no_audio_title'), t('dlg_no_audio_msg'),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self.load_wav()
            return
        # 播放前重建唯一 startTime 清單（含蓋 up/down 移動後的變更）
        self._rebuild_hit_times()
        self._play_start_ms = start_ms
        self._play_end_ms   = end_ms
        self._hit_last_ms   = start_ms
        # 指標跳到第一個 >= start_ms 的位置
        import bisect
        self._hit_ptr = bisect.bisect_left(self._hit_times, int(start_ms))
        self.view.set_follow_mode(follow)
        self._tb_pause_act.setText(t('tb_pause'))
        # 播放前先把視圖跳到起始位置，確保畫面在音訊啟動前已呈現正確位置
        if follow:
            self.view.follow_to_ms(start_ms)
            self.view._judge_ms = start_ms
            self.view.update()
        else:
            self.view.set_judge_line(start_ms)
        # Apply playback offset: audio backend plays at audio-time, but user
        # offset is signed (positive=advance, negative=delay). To simulate
        # the offset without modifying the file, start the audio at
        # start_ms + offset and map audio current_ms back to chart time
        # when updating UI and hit timings.
        adj_start = start_ms + float(self._playback_offset_ms)
        adj_end = end_ms + float(self._playback_offset_ms)
        self.audio.play(adj_start, adj_end)
        self._is_playing = True
        self._judge_timer.start()

    def _toggle_pause_resume(self) -> None:
        if self.audio.is_paused():
            self.resume_audio()
        else:
            self.pause_audio()

    def pause_audio(self) -> None:
        if self._is_playing:
            self.audio.pause()
            self._judge_timer.stop()
            self._tb_pause_act.setText(t('tb_resume'))

    def resume_audio(self) -> None:
        # audio.resume() 內部呼叫 play()→stop()，會觸發 playback_stopped 信號
        # 導致 _is_playing=False，需要在 resume 之後重新設為 True
        self.audio.resume()
        self._is_playing = True
        self._judge_timer.start()
        self._tb_pause_act.setText(t('tb_pause'))

    def stop_audio(self) -> None:
        self.audio.stop()
        self._is_playing = False
        self._judge_timer.stop()
        self.view.set_follow_mode(False)
        self.view.set_judge_line(None)
        self._tb_pause_act.setText(t('tb_pause'))

    def restart_audio(self) -> None:
        self._play_range(self._play_start_ms, self._play_end_ms)

    def _on_judge_tick(self) -> None:
        if not self._is_playing:
            self._judge_timer.stop()
            return
        try:
            pos = self.audio.current_ms()
            if pos is None:
                self._judge_timer.stop()
                self._is_playing = False
                self.view.set_judge_line(None)
                return
            # Map audio time back to chart time by subtracting playback offset.
            # audio.current_ms() reports audio-file timeline; UI/hit times are
            # chart timeline. Use chart_pos for comparisons and view updates.
            chart_pos = pos - float(self._playback_offset_ms)
            # 打擊聲：用指標掃描預計算的 hit_times，每個唯一 startTime 只響一次
            if self._hit_sound_persistent and (self._hit_wav_tmp_path or self._hit_sound_bytes):
                while (self._hit_ptr < len(self._hit_times)
                       and self._hit_times[self._hit_ptr] < chart_pos):
                    self._play_hit_sound()
                    self._hit_ptr += 1
            self._hit_last_ms = chart_pos
            # 跟隨模式：播放整首時直接滾動視窗（以譜面時間為基準）
            if getattr(self.view, '_follow_mode', False):
                self.view.follow_to_ms(chart_pos)
                self.view._judge_ms = chart_pos
                self.view.update()
            else:
                self.view.set_judge_line(chart_pos)
        except Exception:
            self._judge_timer.stop()
            self._is_playing = False

    def _on_music_vol_changed(self, val: int) -> None:
        self.audio.set_volume(val / 100.0)

    def _on_music2_vol_changed(self, val: int) -> None:
        # set secondary audio volume
        try:
            if hasattr(self.audio, 'set_volume2'):
                self.audio.set_volume2(val / 100.0)
        except Exception:
            pass

    def _on_hit_vol_changed(self, val: int) -> None:
        self._hit_vol = val / 100.0
        self._rebuild_hit_wav()

    def _on_hit_toggle(self, checked: bool) -> None:
        self._hit_sound_persistent = checked
        self._act_hit.setChecked(checked)
        # 播放中途才開啟時：將 ptr 推進到當前位置，跳過已過去的音符，
        # 避免下一個 tick 瞬間重播所有歷史音符互相覆蓋而聽不到聲音。
        if checked and self._is_playing:
            pos = self.audio.current_ms()
            if pos is not None:
                import bisect
                # map audio time back to chart time
                chart_pos = pos - float(self._playback_offset_ms)
                self._hit_ptr = bisect.bisect_left(self._hit_times, chart_pos)

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

    def _on_time_uniform_toggle(self, checked: bool) -> None:
        self.view.toggle_time_uniform(checked)
        # 動態更新按鈕文字
        from qt_editor.i18n import t
        self._act_time_uniform.setText(
            t('tb_time_uniform_on') if checked else t('tb_time_uniform_off')
        )

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
        try:
            self.view.set_follow_mode(False)
            self.view.set_judge_line(None)
            self._tb_pause_act.setText(t('tb_pause'))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 打擊聲
    # ------------------------------------------------------------------

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
        # 使用 winsound.PlaySound + SND_ASYNC 直接在主執行緒非同步播放。
        # 音量已套用在 _hit_wav_tmp_path 的暫存 WAV 中。
        path = self._hit_wav_tmp_path
        if not path:
            path = os.path.join(os.path.dirname(__file__), 'short-shimmering-hi-hat.wav')
        try:
            import winsound
            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            pass

    # ==================================================================
    # 播放偏移
    # ==================================================================

    def _show_offset_dialog(self) -> None:
        """開啟播放延遲/提前設定對話框。"""
        bpm = self.view.model.bpm if self.view.model.bpm > 0 else 120.0
        dlg = PlaybackOffsetDialog(
            self,
            bpm=bpm,
            current_ms=abs(self._playback_offset_ms),
            is_advance=self._playback_offset_advance,
        )
        if dlg.exec_() == PlaybackOffsetDialog.Accepted:
            self._playback_offset_ms = dlg.offset_ms()
            self._playback_offset_advance = dlg.is_advance()
            # 更新工具列上的偏移標籤
            if self._playback_offset_ms == 0:
                self._lbl_offset.setText(t('status_offset_none'))
            else:
                self._lbl_offset.setText(t('status_offset', self._playback_offset_ms))

    # ==================================================================
    # 匯出完整曲目
    # ==================================================================

    def export_song(self) -> None:
        """匯出完整曲目格式（register.json + 音源 + 譜面 + 曲繪）。"""
        logging.debug('export_song: start')
        import json
        import shutil
        from pathlib import Path

        m = self.view.model
        logging.debug('model current_file=%s dirty=%s', m.current_file, getattr(m, 'dirty', None))
        if not m.notes_tree:
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_no_chart'))
            return

        wav_path = getattr(self.audio, 'audio_path', '') or ''
        logging.debug('audio_path=%s', wav_path)
        if not wav_path or not os.path.isfile(wav_path):
            reply = QMessageBox.question(
                self, t('dlg_no_audio_title'), t('dlg_export_no_audio'),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self.load_wav()
                wav_path = getattr(self.audio, 'audio_path', '') or ''
            if not wav_path or not os.path.isfile(wav_path):
                return

        dlg = ExportSongDialog(
            self,
            offset_ms=self._playback_offset_ms,
            wav_path=wav_path,
            chart_json_path=m.current_file or '',
        )
        if dlg.exec_() != ExportSongDialog.Accepted:
            logging.debug('export_song: dialog cancelled')
            return

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

            # 決定曲目資料夾
            if is_append and append_folder_full:
                song_folder = append_folder_full
                folder_name = append_folder
            elif is_append and append_folder:
                song_folder = os.path.join(SONGS_ROOT, append_folder)
                folder_name = append_folder
            else:
                folder_name = display_name
                song_folder = os.path.join(SONGS_ROOT, folder_name)

            logging.debug('creating song_folder=%s', song_folder)
            os.makedirs(song_folder, exist_ok=True)

            # ── 難度子資料夾 ──────────────────────────────────────
            diff_folder = os.path.join(song_folder, diff_name)
            logging.debug('creating diff_folder=%s', diff_folder)
            os.makedirs(diff_folder, exist_ok=True)

            # ── 儲存譜面 JSON ─────────────────────────────────────
            chart_basename = display_name   # 用曲名當 JSON 檔名
            chart_path = os.path.join(diff_folder, chart_basename + '.json')
            logging.debug('saving chart json to %s', chart_path)
            m.save_json(chart_path)
            logging.debug('chart saved')

            # ── 處理音源 ──────────────────────────────────────────
            src_wav = Path(wav_path)
            offset = self._playback_offset_ms
            # rip.py 的 process_audio: 正值=前面加靜音，負值=裁剪前面
            # 我們的偏移語意：正=提前（音訊要從更前面開始→砍前面 = 負 ms）
            #                 負=延後（音訊前面要加靜音   = 正 ms）
            rip_ms = -offset   # 轉換語意

            audio_in_song_root = os.path.join(song_folder, display_name + '.wav')
            logging.debug('processing audio with rip_ms=%s', rip_ms)
            if rip_ms == 0:
                # 無偏移：若曲目資料夾已有同名 wav → 直接沿用，不複製
                if os.path.isfile(audio_in_song_root):
                    audio_dest = audio_in_song_root
                else:
                    audio_dest = audio_in_song_root
                    shutil.copy2(str(src_wav), audio_dest)
                audio_res_suffix = display_name          # 資源路徑用曲名 (放在曲目根)
            else:
                # 有偏移：處理音源後放入難度子資料夾，檔名標註偏移量
                sign = '+' if offset > 0 else ''
                offset_tag = f'{sign}{offset}ms'
                wav_name = f'{display_name}_{offset_tag}.wav'
                audio_dest = os.path.join(diff_folder, wav_name)
                try:
                    import sys as _sys2
                    _rip_dir = os.path.join(os.path.dirname(__file__), '..')
                    if _rip_dir not in _sys2.path:
                        _sys2.path.insert(0, _rip_dir)
                    from rip import process_audio
                    processed = process_audio(src_wav, rip_ms)
                except ImportError:
                    processed = src_wav
                if os.path.normpath(str(processed)) != os.path.normpath(audio_dest):
                    shutil.copy2(str(processed), audio_dest)
                # 清理 process_audio 產生的中間檔案
                if str(processed) != str(src_wav) and \
                   os.path.normpath(str(processed)) != os.path.normpath(audio_dest):
                    try:
                        os.remove(str(processed))
                    except Exception:
                        pass
                audio_res_suffix = f'{diff_name}/{Path(wav_name).stem}'  # 資源路徑含難度子資料夾
                logging.debug('audio processed to %s (audio_dest=%s)', processed, audio_dest)

            # ── 處理曲繪 ──────────────────────────────────────────
            cover_dest = ''
            if cover_path and os.path.isfile(cover_path):
                ext = os.path.splitext(cover_path)[1]
                cover_dest_name = display_name + ext
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
            chart_res  = f'{_res_rel}/{diff_name}/{chart_basename}'
            audio_res  = f'{_res_rel}/{audio_res_suffix}'
            cover_res  = f'{_res_rel}/{display_name}' if cover_dest else ''

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

    def resolve_overlaps_dialog(self) -> None:
        gap, ok = QInputDialog.getInt(
            self, t('dlg_resolve_title'), t('dlg_resolve_label'), 40, 1, 1000,
        )
        if not ok:
            return
        m = self.view.model
        m.push_history()
        notes_sorted = sorted(
            (n for n in m.notes_tree if n.note_type == 2),
            key=lambda n: (n.min_key, n.start),
        )
        changed = False
        by_key: dict = {}
        for n in m.notes_tree:
            for k in range(n.min_key, n.max_key + 1):
                by_key.setdefault(k, []).append(n)
        for k, lst in by_key.items():
            lst_s = sorted(lst, key=lambda x: x.start)
            for i in range(len(lst_s) - 1):
                a, b = lst_s[i], lst_s[i + 1]
                if a.end + gap > b.start:
                    a.end  = max(a.start + 1, b.start - gap)
                    a.gate = a.end - a.start
                    changed = True
        if changed:
            m.rebuild_display_cache()
            self.view.update()
            self.view.note_edited.emit()
        else:
            QMessageBox.information(self, t('dlg_no_overlaps_title'), t('dlg_no_overlaps_msg'))

    def adjust_bpm_dialog(self) -> None:
        cur = self.view.model.bpm
        bpm, ok = QInputDialog.getDouble(
            self, t('dlg_bpm_title'), t('dlg_bpm_label', cur),
            cur, 10.0, 999.0, 2,
        )
        if ok:
            self.view.model.bpm = bpm
            self.view.rebuild_mapper()

    def adjust_beats_dialog(self) -> None:
        cur = self.view.model.beats_per_bar
        beats, ok = QInputDialog.getInt(
            self, t('dlg_beats_title'), t('dlg_beats_label', cur),
            cur, 1, 32,
        )
        if ok:
            self.view.model.beats_per_bar = beats
            self.view.rebuild_mapper()

    def adjust_offset_dialog(self) -> None:
        delta, ok = QInputDialog.getInt(
            self, t('dlg_offset_title'), t('dlg_offset_label'),
            0, -100000, 100000,
        )
        if ok and delta != 0:
            self.view.shift_selected_time(delta) if self.view.selected else None
            if not self.view.selected:
                # 無選取時對全部音符操作
                m = self.view.model
                m.push_history()
                for n in m.notes_tree:
                    n.start = max(0, n.start + delta)
                    n.end   = max(n.start + 1, n.end + delta)
                    n.gate  = n.end - n.start
                m.rebuild_display_cache()
                self.view.update()
                self.view.note_edited.emit()

    # ==================================================================
    # 關閉
    # ==================================================================


    def open_preferences_dialog(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec_() == SettingsDialog.Accepted:
            # 套用滾輪方向設定
            scroll_inv = bool(settings.get('scroll_invert', False))
            self.view.scroll_invert = scroll_inv
            self._act_inv.setChecked(scroll_inv)
            self.view._emit_status()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._judge_timer.stop()
        self._title_timer.stop()
        if self.view.model.dirty:
            reply = QMessageBox.question(
                self, t('dlg_unsaved_title'), t('dlg_unsaved_msg'),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply == QMessageBox.Save:
                self.save_file()
                event.accept()
            elif reply == QMessageBox.Discard:
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
