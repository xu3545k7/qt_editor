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
        from PyQt5.QtWidgets import QTabWidget
        tabs = QTabWidget()
        layout.addWidget(tabs)

        def page(title):
            w = QWidget()
            f = QFormLayout(w)
            tabs.addTab(w, title)
            return f

        form = page(t('settings_tab_general') if _has('settings_tab_general') else '一般')
        disp = page('顯示')
        edit = page('編輯')
        perf = page('效能')

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
        edit.addRow(QLabel(t('settings_scroll_dir')), self._scroll_combo)

        # ── 轉譜風格 ──────────────────────────────────────────────────
        from .smart_chart import STYLE_EATHER, STYLE_OFFICIAL, normalise_style

        self._style_combo = QComboBox()
        self._style_combo.addItem(t('style_user'), STYLE_EATHER)
        self._style_combo.addItem(t('style_official'), STYLE_OFFICIAL)
        current_style = normalise_style(settings.get('chart_style'))
        self._style_combo.setCurrentIndex(
            1 if current_style == STYLE_OFFICIAL else 0
        )
        self._style_combo.setToolTip(t('pref_chart_style_hint'))
        edit.addRow(QLabel(t('pref_chart_style')), self._style_combo)
        style_hint = QLabel(t('pref_chart_style_hint'))
        style_hint.setWordWrap(True)
        style_hint.setStyleSheet('color: gray; font-size: 10px;')
        edit.addRow(QLabel(''), style_hint)

        # ── 語言更改提示 ──────────────────────────────────────────────
        self._note_label = QLabel(t('settings_restart_note'))
        self._note_label.setStyleSheet('color: gray; font-size: 10px;')
        layout.addWidget(self._note_label)

        # ── 鍵盤（判定線）高度 ────────────────────────────────────────
        self._kb_spin = QSpinBox()
        self._kb_spin.setRange(48, 400)
        self._kb_spin.setSingleStep(8)
        self._kb_spin.setSuffix(' px')
        self._kb_spin.setValue(int(settings.get('keyboard_height_px', 168)))
        self._kb_spin.setToolTip(t('settings_kb_height_hint'))
        disp.addRow(QLabel(t('settings_kb_height')), self._kb_spin)

        # ── 深色模式 ──────────────────────────────────────────────────
        self._dark_chk = QCheckBox(t('settings_dark_hint'))
        self._dark_chk.setChecked(bool(settings.get('dark_mode', False)))
        disp.addRow(QLabel(t('settings_dark_mode')), self._dark_chk)

        # ── 音高模式：踏板欄 / 力度上色 ───────────────────────────────
        self._pedal_chk = QCheckBox('音高模式左側顯示延音踏板欄（可拖曳編輯）')
        self._pedal_chk.setChecked(bool(settings.get('pitch_pedal_lane', True)))
        disp.addRow(QLabel('延音踏板'), self._pedal_chk)

        self._vel_chk = QCheckBox('音高模式依力度調整音符明暗')
        self._vel_chk.setChecked(bool(settings.get('pitch_velocity_shading', True)))
        disp.addRow(QLabel('力度表現'), self._vel_chk)

        # ── 匯出音訊自動處理 ──────────────────────────────────────────
        self._audio_auto_chk = QCheckBox('匯出時自動處理音訊（解析檔名偏移並裁切/補零）')
        audio_auto = bool(settings.get('export_auto_process_audio', True))
        self._audio_auto_chk.setChecked(audio_auto)
        edit.addRow(QLabel('匯出音訊處理'), self._audio_auto_chk)

        # ── 顯示：以前只在「檢視」選單裡的那些 ───────────────────────
        self._toggles = {}

        def toggle(page_form, key, label, text, default=True, tip=''):
            chk = QCheckBox(text)
            chk.setChecked(bool(settings.get(key, default)))
            if tip:
                chk.setToolTip(tip)
            page_form.addRow(QLabel(label), chk)
            self._toggles[key] = chk
            return chk

        toggle(disp, 'pitch_velocity_numbers', '力度數字',
               '在音符上顯示力度數字（音高模式）')
        toggle(disp, 'pitch_dynamics_lane', '強弱曲線',
               '顯示左右兩側的強弱曲線欄（音高模式）')
        toggle(disp, 'pitch_scale_highlight', '調性高亮',
               '把調內的音格與琴鍵標亮（音高模式）')
        toggle(disp, 'ghost_other_hand', '幽靈音符',
               '只編一隻手時，另一手畫成半透明參考')
        toggle(disp, 'show_statusbar', '狀態列', '顯示視窗底部的狀態列',
               default=False)
        toggle(disp, 'show_midi_pitch', 'MIDI 音高編號',
               '音高用 MIDI 編號（21~108）而不是鋼琴鍵序（1~88）',
               default=False)

        # ── 編輯 ──────────────────────────────────────────────────────
        toggle(edit, 'pitch_scale_lock', '鎖調',
               '放音符時只吸調內音（方向鍵的半音微調不受影響）',
               default=False)

        # ── 效能 ──────────────────────────────────────────────────────
        self._undo_spin = QSpinBox()
        self._undo_spin.setRange(8, 512)
        self._undo_spin.setSingleStep(8)
        self._undo_spin.setSuffix(' MB')
        self._undo_spin.setValue(int(settings.get('undo_memory_mb', 64)))
        self._undo_spin.setToolTip(
            '復原歷史最多佔多少記憶體。譜面越大、能留的步數越少（至少 8 步）。'
            + chr(10) +
            '記憶體小的機器調小一點；大譜面編輯時當掉多半是這裡吃太多。')
        perf.addRow(QLabel('復原歷史上限'), self._undo_spin)
        perf_hint = QLabel(
            '一筆歷史大約是「音符數 × 0.5KB」。4700 顆的譜在 64MB 下約可留 28 步，'
            '2000 顆以下則是完整的 50 步。')
        perf_hint.setWordWrap(True)
        perf_hint.setStyleSheet('color: gray; font-size: 10px;')
        perf.addRow(QLabel(''), perf_hint)

        self._latency_spin = QSpinBox()
        self._latency_spin.setRange(-500, 500)
        self._latency_spin.setSingleStep(5)
        self._latency_spin.setSuffix(' ms')
        self._latency_spin.setValue(int(settings.get('audio_latency_ms', 0)))
        self._latency_spin.setToolTip('判定線比聲音早到就調大。')
        perf.addRow(QLabel('音效輸出延遲補償'), self._latency_spin)

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
        settings.set('undo_memory_mb', int(self._undo_spin.value()))
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
