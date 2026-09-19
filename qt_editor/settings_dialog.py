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

from PyQt5.QtWidgets import (QSpinBox, 
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence

from PyQt5.QtWidgets import QCheckBox

from .i18n import t, set_lang


def _has(key: str) -> bool:
    """i18n 有沒有這個 key（`t()` 查不到會原樣回傳 key）。"""
    return t(key) != key
from .theme import apply_theme
from .settings import settings

# 語言選項：(顯示名稱, 代碼)
_LANG_OPTIONS = [
    ('繁體中文', 'zh_tw'),
    ('简体中文', 'zh_cn'),
    ('English',  'en'),
]


class KeyCaptureDialog(QDialog):
    """跳出來等使用者按一個鍵，把按下的那個組合記下來。

    比 `QKeySequenceEdit` 直覺：那個欄位看起來像文字框，很多人會想「用打的」，
    而且它會連續收四個組合鍵。這裡只收**一個**組合，按 Esc 取消。
    """

    def __init__(self, action_label: str, current: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('設定快捷鍵')
        self.setMinimumWidth(320)
        self._seq = None
        box = QVBoxLayout(self)
        title = QLabel('「%s」' % action_label)
        title.setStyleSheet('font-size: 13px; font-weight: bold;')
        box.addWidget(title)
        box.addWidget(QLabel('目前：%s' % (current or '（未設定）')))
        prompt = QLabel('請按下要用的按鍵…')
        prompt.setStyleSheet('font-size: 15px; padding: 14px 0;')
        prompt.setAlignment(Qt.AlignCenter)
        box.addWidget(prompt)
        hint = QLabel('Esc = 取消　Backspace = 清除綁定')
        hint.setStyleSheet('color: gray; font-size: 10px;')
        hint.setAlignment(Qt.AlignCenter)
        box.addWidget(hint)
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        # 單獨按修飾鍵不算——等他按下真正的那個鍵
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                   Qt.Key_AltGr, Qt.Key_unknown):
            return
        if key == Qt.Key_Escape:
            self.reject()
            return
        if key == Qt.Key_Backspace:
            self._seq = ''            # 清除綁定
            self.accept()
            return
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                    | Qt.AltModifier | Qt.MetaModifier)
        self._seq = QKeySequence(int(mods) | key).toString()
        self.accept()

    def sequence(self) -> str | None:
        """按下的組合；取消時回 None，清除時回空字串。"""
        return self._seq


class SettingsDialog(QDialog):
    """偏好設定對話框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('settings_title'))
        self.setMinimumWidth(320)

        self._original_lang = settings.get('language', 'zh_tw')

        layout = QVBoxLayout(self)
        from PyQt5.QtWidgets import QGroupBox, QTabWidget
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self._tab_bodies = []

        def tab(title):
            """一個分頁：由上往下疊分組框，最後補一段彈性空白。"""
            w = QWidget()
            body = QVBoxLayout(w)
            tabs.addTab(w, title)
            self._tab_bodies.append(body)
            return body

        def section(body, title):
            box = QGroupBox(title)
            f = QFormLayout(box)
            body.addWidget(box)
            return f

        def hint(form, text):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet('color: gray; font-size: 10px;')
            form.addRow(QLabel(''), label)
            return label

        self._toggles = {}

        def toggle(form, key, label, text, default=True, tip=''):
            chk = QCheckBox(text)
            chk.setChecked(bool(settings.get(key, default)))
            if tip:
                chk.setToolTip(tip)
            form.addRow(QLabel(label), chk)
            self._toggles[key] = chk
            return chk

        general = tab(t('settings_tab_general') if _has('settings_tab_general') else '一般')
        screen = tab('畫面')
        editing = tab('編輯與播放')

        # ════════════════════════ 一般 ════════════════════════
        ui = section(general, '介面')

        self._lang_combo = QComboBox()
        current_lang = self._original_lang
        for display, code in _LANG_OPTIONS:
            self._lang_combo.addItem(display, code)
        for i, (_, code) in enumerate(_LANG_OPTIONS):
            if code == current_lang:
                self._lang_combo.setCurrentIndex(i)
                break
        ui.addRow(QLabel(t('settings_language')), self._lang_combo)

        self._dark_chk = QCheckBox(t('settings_dark_hint'))
        self._dark_chk.setChecked(bool(settings.get('dark_mode', False)))
        ui.addRow(QLabel(t('settings_dark_mode')), self._dark_chk)

        toggle(ui, 'show_statusbar', '狀態列', '顯示視窗底部的狀態列',
               default=False)

        self._scroll_combo = QComboBox()
        self._scroll_combo.addItem(t('settings_normal'),   False)
        self._scroll_combo.addItem(t('settings_reversed'), True)
        scroll_invert = bool(settings.get('scroll_invert', False))
        self._scroll_combo.setCurrentIndex(1 if scroll_invert else 0)
        ui.addRow(QLabel(t('settings_scroll_dir')), self._scroll_combo)

        files = section(general, '檔案')

        from .autosave import DEFAULT_INTERVAL_MIN, autosave_dir
        autosave_row = QHBoxLayout()
        self._autosave_chk = QCheckBox('每隔')
        self._autosave_chk.setChecked(bool(settings.get('autosave_enabled', True)))
        self._autosave_spin = QSpinBox()
        self._autosave_spin.setRange(1, 60)
        self._autosave_spin.setSuffix(' 分鐘')
        self._autosave_spin.setValue(int(settings.get('autosave_interval_min',
                                                      DEFAULT_INTERVAL_MIN)))
        self._autosave_spin.setEnabled(self._autosave_chk.isChecked())
        self._autosave_chk.toggled.connect(self._autosave_spin.setEnabled)
        autosave_row.addWidget(self._autosave_chk)
        autosave_row.addWidget(self._autosave_spin)
        autosave_row.addWidget(QLabel('備份一次還沒存的改動'))
        autosave_row.addStretch(1)
        autosave_tip = (
            '寫到備份資料夾，不會覆蓋你的原檔：' + chr(10) + autosave_dir() + chr(10) +
            '當掉或沒存檔就關掉之後，重開編輯器或再開同一個檔案時會問要不要還原。'
            + chr(10) + '正式存檔、或關閉時選「不儲存」，備份就會自動刪掉。'
            + chr(10) + '播放中不會存（存 XML 要幾百毫秒，判定線會頓），停下來時補存。')
        self._autosave_chk.setToolTip(autosave_tip)
        self._autosave_spin.setToolTip(autosave_tip)
        files.addRow(QLabel('自動儲存'), autosave_row)

        self._audio_auto_chk = QCheckBox('匯出時自動處理音訊（解析檔名偏移並裁切/補零）')
        self._audio_auto_chk.setChecked(
            bool(settings.get('export_auto_process_audio', True)))
        files.addRow(QLabel('匯出音訊處理'), self._audio_auto_chk)

        self._hold_pct_spin = QSpinBox()
        self._hold_pct_spin.setRange(10, 100)
        self._hold_pct_spin.setSuffix(' %')
        self._hold_pct_spin.setValue(int(settings.get('official_hold_length_pct', 80)))
        self._hold_pct_spin.setToolTip(
            '存成 XML（PAN 相容）或輸出 Hiraeth ZIP 時，長押長度乘上這個比例。' + chr(10) +
            'JSON 原始檔不受影響；本來就是從官方 XML 讀進來的譜不會再縮。')
        files.addRow(QLabel('轉官方格式的長押長度'), self._hold_pct_spin)

        advanced = section(general, '進階')

        toggle(advanced, 'oplog_enabled', '操作紀錄',
               '把每次編輯的前後差異寫進 qt_editor/logs/*.jsonl',
               tip='用來看「跑完工具之後又手動改了什麼」，'
                   '那個方向就是演算法該調的方向。' + chr(10) +
                   '純本機檔案，不會傳出去；每次編輯多花約 3 毫秒。')

        self._undo_spin = QSpinBox()
        self._undo_spin.setRange(8, 512)
        self._undo_spin.setSingleStep(8)
        self._undo_spin.setSuffix(' MB')
        self._undo_spin.setValue(int(settings.get('undo_memory_mb', 64)))
        self._undo_spin.setToolTip(
            '復原歷史最多佔多少記憶體。譜面越大、能留的步數越少（至少 8 步）。'
            + chr(10) +
            '記憶體小的機器調小一點。')
        advanced.addRow(QLabel('復原歷史上限'), self._undo_spin)
        hint(advanced,
             '沒改到的音符在各步之間共用，一般編輯每步只多幾 KB。上限是照最壞情況'
             '（排譜、生成那種整份重寫，每顆約 0.34KB）算的：4700 顆的譜在 64MB 下'
             '可留 42 步，3700 顆以下是完整的 50 步。')

        # ════════════════════════ 畫面 ════════════════════════
        views = section(screen, '所有檢視')

        self._kb_spin = QSpinBox()
        self._kb_spin.setRange(48, 400)
        self._kb_spin.setSingleStep(8)
        self._kb_spin.setSuffix(' px')
        self._kb_spin.setValue(int(settings.get('keyboard_height_px', 168)))
        self._kb_spin.setToolTip(t('settings_kb_height_hint'))
        views.addRow(QLabel(t('settings_kb_height')), self._kb_spin)

        self._note_width_spin = QSpinBox()
        self._note_width_spin.setRange(40, 100)
        self._note_width_spin.setSingleStep(5)
        self._note_width_spin.setSuffix(' %')
        self._note_width_spin.setValue(int(settings.get('note_width_pct', 100)))
        self._note_width_spin.setToolTip(
            '音符要佔滿鍵道寬度的百分之幾。100 = 貼滿整個鍵道，'
            '相鄰音符的尖端剛好互相碰到；'
            '調小會在左右留出間隙，密集的譜比較看得出一顆一顆。' + chr(10) +
            '點選判定跟著縮——所見即所點。')
        views.addRow(QLabel('音符寬度'), self._note_width_spin)

        self._hold_width_spin = QSpinBox()
        self._hold_width_spin.setRange(20, 100)
        self._hold_width_spin.setSingleStep(5)
        self._hold_width_spin.setSuffix(' %')
        self._hold_width_spin.setValue(int(settings.get('hold_width_pct', 55)))
        self._hold_width_spin.setToolTip(
            '長押主體要佔音符寬度的百分之幾（預覽模式）。'
            '100 = 和音符頭一樣寬。' + chr(10) +
            '這條是壓在音符頭後面的裝飾長條，調太寬時密集的長押段落'
            '會糊成一整片，看不出一顆一顆。')
        views.addRow(QLabel('長押寬度（預覽）'), self._hold_width_spin)

        toggle(views, 'ghost_other_hand', '幽靈音符',
               '只編一隻手時，另一手畫成半透明參考')

        pitch = section(screen, '音高模式')

        self._pedal_chk = QCheckBox('左側顯示延音踏板欄（可拖曳編輯）')
        self._pedal_chk.setChecked(bool(settings.get('pitch_pedal_lane', True)))
        pitch.addRow(QLabel('延音踏板'), self._pedal_chk)

        self._vel_chk = QCheckBox('依力度調整音符明暗')
        self._vel_chk.setChecked(bool(settings.get('pitch_velocity_shading', True)))
        pitch.addRow(QLabel('力度明暗'), self._vel_chk)

        toggle(pitch, 'pitch_velocity_numbers', '力度數字',
               '在音符上顯示力度數字')
        toggle(pitch, 'pitch_dynamics_lane', '強弱曲線',
               '顯示左右兩側的強弱曲線欄')

        self._pitch_column_combo = QComboBox()
        for label, value in (('黑白鍵分色', 'blackwhite'), ('調性分色', 'scale')):
            self._pitch_column_combo.addItem(label, value)
        _pc = str(settings.get('pitch_column_mode', 'blackwhite'))
        _pc_index = self._pitch_column_combo.findData(_pc)
        self._pitch_column_combo.setCurrentIndex(_pc_index if _pc_index >= 0 else 0)
        self._pitch_column_combo.setToolTip(
            '欄位底色要照什麼分。' + chr(10) +
            '黑白鍵分色：照鋼琴的黑白鍵排列，鍵盤本身就是刻度（預設）。'
            + chr(10) +
            '調性分色：把調內的音格與琴鍵標亮，主音再深一點。')
        pitch.addRow(QLabel('欄位分色'), self._pitch_column_combo)

        toggle(pitch, 'show_midi_pitch', '音高編號',
               '用 MIDI 編號（21~108）而不是鋼琴鍵序（1~88）',
               default=False)

        # ════════════════════════ 編輯與播放 ════════════════════════
        arrange = section(editing, '排譜')

        from .smart_chart import STYLE_EATHER, STYLE_OFFICIAL, normalise_style

        self._style_combo = QComboBox()
        self._style_combo.addItem(t('style_user'), STYLE_EATHER)
        self._style_combo.addItem(t('style_official'), STYLE_OFFICIAL)
        current_style = normalise_style(settings.get('chart_style'))
        self._style_combo.setCurrentIndex(
            1 if current_style == STYLE_OFFICIAL else 0
        )
        self._style_combo.setToolTip(t('pref_chart_style_hint'))
        arrange.addRow(QLabel(t('pref_chart_style')), self._style_combo)
        hint(arrange, t('pref_chart_style_hint'))

        place = section(editing, '放置音符')

        self._place_grid_combo = QComboBox()
        for label, value in (('放置模式才顯示', 'placement'),
                             ('永遠顯示', 'always'),
                             ('永遠關閉', 'never')):
            self._place_grid_combo.addItem(label, value)
        current = str(settings.get('place_grid_mode', 'placement'))
        index = self._place_grid_combo.findData(current)
        self._place_grid_combo.setCurrentIndex(index if index >= 0 else 0)
        self._place_grid_combo.setToolTip(
            '依「放置時值」畫的水平參考線，間隔就是你在放置模式選的音符長度。'
            + chr(10) +
            '線的位置正好是音符會被吸附到的地方，放之前就看得到會落在哪一條。')
        place.addRow(QLabel('放置格線'), self._place_grid_combo)

        toggle(place, 'place_note_sound', '放置時發聲',
               '放下音符時用內建鋼琴音彈出那一顆',
               tip='聽得出剛放的音高對不對。播放中放下的音符本來就只有靠這個'
                   '才聽得到——主播放是整段預先算好的，中途新增的音不在裡面。')

        toggle(place, 'pitch_edit_moves_lanes', '改音高時重排鍵道',
               '音符的鍵道跟著音高搬',
               default=False,
               tip='關著（預設）：調音高就只改音高，音符留在原本的鍵道上，'
                   + chr(10) +
                   '排好的譜面不會被重排。打開：音高一改，鍵道就跟著搬到對應位置。'
                   + chr(10) +
                   '放置模式下鍵道一律跟著音高走，不受這個設定影響。')

        toggle(place, 'pitch_scale_lock', '鎖調',
               '只吸調內音（方向鍵的半音微調不受影響）',
               default=False)

        playback = section(editing, '播放')

        toggle(playback, 'resume_from_view', '暫停後繼續',
               '從畫面正在看的位置開始播（捲動過才算）',
               tip='暫停之後捲去看譜面別的地方，按繼續時從判定線（鍵盤上緣）'
                   '對到的那一刻開始播。' + chr(10) +
                   '沒捲動過就照原本的暫停點續播。關掉則一律回到暫停點。')

        self._latency_spin = QSpinBox()
        self._latency_spin.setRange(-500, 500)
        self._latency_spin.setSingleStep(5)
        self._latency_spin.setSuffix(' ms')
        self._latency_spin.setValue(int(settings.get('audio_latency_ms', 0)))
        self._latency_spin.setToolTip('判定線比聲音早到就調大。')
        playback.addRow(QLabel('音效輸出延遲補償'), self._latency_spin)

        for body in self._tab_bodies:
            body.addStretch(1)

        def page(title):
            """快捷鍵分頁沿用單一表單。"""
            w = QWidget()
            f = QFormLayout(w)
            tabs.addTab(w, title)
            return f

        # ── 快捷鍵 ────────────────────────────────────────────────────
        self._keys = {}
        keys_form = page('快捷鍵')
        actions = getattr(parent, '_SHORTCUT_ACTIONS', None) or (
            ('shortcut_cycle_view', '切換檢視模式', ''),
            ('shortcut_note_input', '切換放置模式', ''),
        )
        self._key_values = {}
        self._key_labels = {}
        for key, label, _method in actions:
            self._key_values[key] = str(settings.get(key, '') or '')
            row = QWidget()
            row_box = QHBoxLayout(row)
            row_box.setContentsMargins(0, 0, 0, 0)
            shown = QLabel()
            shown.setStyleSheet(
                'padding: 2px 8px; border: 1px solid palette(mid);'
                ' background: palette(base);')
            shown.setMinimumWidth(120)
            btn = QPushButton('變更…')
            clr = QPushButton('清除')
            row_box.addWidget(shown, 1)
            row_box.addWidget(btn)
            row_box.addWidget(clr)
            keys_form.addRow(QLabel(label), row)
            self._key_labels[key] = shown
            btn.clicked.connect(
                lambda _c=False, k=key, lb=label: self._capture_key(k, lb))
            clr.clicked.connect(lambda _c=False, k=key: self._set_key(k, ''))
            self._refresh_key_label(key)
        keys_hint = QLabel('按「變更…」之後直接按你要的鍵，按一下就記錄起來。'
                           '重複綁定同一個鍵會標紅色；和選單既有的快捷鍵'
                           '（Ctrl+S、Tab 之類）重複時，選單的優先。')
        keys_hint.setWordWrap(True)
        keys_hint.setStyleSheet('color: gray; font-size: 10px;')
        keys_form.addRow(QLabel(''), keys_hint)

        # ── 按鈕 ──────────────────────────────────────────────────────
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _refresh_key_label(self, key: str) -> None:
        """更新某一列顯示的按鍵，並把重複綁定標紅。"""
        value = self._key_values.get(key, '')
        label = self._key_labels.get(key)
        if label is None:
            return
        label.setText(value or '（未設定）')
        clash = bool(value) and sum(
            1 for k, v in self._key_values.items() if v == value) > 1
        label.setStyleSheet(
            'padding: 2px 8px; border: 1px solid %s; background: palette(base);%s'
            % ('#c0392b' if clash else 'palette(mid)',
               ' color: #c0392b;' if clash else ''))
        label.setToolTip('這個按鍵和其他動作重複了' if clash else '')

    def _set_key(self, key: str, value: str) -> None:
        self._key_values[key] = value
        for other in self._key_values:
            self._refresh_key_label(other)

    def _capture_key(self, key: str, label: str) -> None:
        dlg = KeyCaptureDialog(label, self._key_values.get(key, ''), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        seq = dlg.sequence()
        if seq is not None:
            self._set_key(key, seq)

    def _on_accept(self) -> None:
        lang_code = self._lang_combo.currentData()
        scroll_inv = self._scroll_combo.currentData()
        settings.set('language',      lang_code)
        settings.set('scroll_invert', scroll_inv)
        settings.set('chart_style', self._style_combo.currentData())
        # Save export audio processing setting
        settings.set('export_auto_process_audio', bool(self._audio_auto_chk.isChecked()))
        settings.set('keyboard_height_px', int(self._kb_spin.value()))
        settings.set('pitch_pedal_lane', bool(self._pedal_chk.isChecked()))
        settings.set('pitch_velocity_shading', bool(self._vel_chk.isChecked()))
        for key, value in self._key_values.items():
            settings.set(key, value)
        for key, chk in self._toggles.items():
            settings.set(key, bool(chk.isChecked()))
        settings.set('pitch_column_mode',
                     self._pitch_column_combo.currentData())
        settings.set('place_grid_mode',
                     self._place_grid_combo.currentData())
        settings.set('note_width_pct', int(self._note_width_spin.value()))
        settings.set('hold_width_pct', int(self._hold_width_spin.value()))
        settings.set('undo_memory_mb', int(self._undo_spin.value()))
        settings.set('autosave_enabled', bool(self._autosave_chk.isChecked()))
        settings.set('autosave_interval_min', int(self._autosave_spin.value()))
        settings.set('official_hold_length_pct', int(self._hold_pct_spin.value()))
        settings.set('audio_latency_ms', int(self._latency_spin.value()))
        # 深色模式即時套用，不需重開
        dark = bool(self._dark_chk.isChecked())
        settings.set('dark_mode', dark)
        apply_theme(QApplication.instance(), dark)
        # 快捷鍵即時重新綁定，不用重開
        win = self.parent()
        while win is not None and not hasattr(win, 'apply_shortcut_settings'):
            win = win.parent()
        if win is not None:
            win.apply_shortcut_settings()
        self.accept()

        if lang_code != self._original_lang:
            # 就地套用新語言，不重啟。舊版是 os.execv 重啟，但打包成 exe 之後
            # `sys.executable -m qt_editor.app` 是不存在的命令，等於關掉就回不來。
            set_lang(lang_code)
            win = self.parent()
            while win is not None and not hasattr(win, 'retranslate_ui'):
                win = win.parent()
            if win is not None:
                win.retranslate_ui()
