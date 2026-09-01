"""
pattern_dialog.py
=================
輔助組合音符：一次放下一組音階或琶音，音高照譜面目前的調性走。

放置模式是一顆一顆點，音階、琶音這種「一串照規則走的音」點起來又慢又容易點錯
音；這裡改成填參數一次生成。調性預設是從現有音符偵測出來的，不用自己算升降記號。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from .music_theory import (
    PATTERN_KINDS, PITCH_CLASS_NAMES, Key, build_pattern, pattern_key,
)
from .models import midi_to_official_piano_index


_KINDS = list(PATTERN_KINDS)

_DIRECTIONS = [
    ('上行',        1),
    ('下行',       -1),
    ('上行後下行',  0),
]

#: (顯示名, 拍數)
_STEPS = [
    ('四分音符', 1.0),
    ('八分音符', 0.5),
    ('八分三連', 1.0 / 3.0),
    ('16 分音符', 0.25),
    ('16 分三連', 1.0 / 6.0),
    ('32 分音符', 0.125),
    ('二分音符', 2.0),
]


class PatternDialog(QDialog):
    """音階／琶音的參數對話框，附即時預覽。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 detected: Optional[Key] = None,
                 start_pitch: int = 60,
                 start_ms: int = 0,
                 hand: int = 0,
                 show_midi_pitch: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle('插入音階 / 琶音')
        self.setMinimumWidth(430)
        self._show_midi = bool(show_midi_pitch)
        root = QVBoxLayout(self)

        # ── 調性 ────────────────────────────────────────────────
        key_box = QGroupBox('調性')
        key_form = QFormLayout(key_box)
        self._cb_tonic = QComboBox()
        for i, name in enumerate(PITCH_CLASS_NAMES):
            self._cb_tonic.addItem(name, i)
        self._cb_mode = QComboBox()
        self._cb_mode.addItem('大調', 'major')
        self._cb_mode.addItem('小調', 'minor')
        if detected is not None:
            self._cb_tonic.setCurrentIndex(detected.tonic % 12)
            self._cb_mode.setCurrentIndex(0 if detected.mode == 'major' else 1)
        row = QHBoxLayout()
        row.addWidget(self._cb_tonic)
        row.addWidget(self._cb_mode)
        row.addStretch()
        holder = QWidget()
        holder.setLayout(row)
        key_form.addRow('調', holder)

        if detected is None:
            hint = '譜面還沒有音高資料，請自己選調。'
        elif detected.confidence >= 0.35:
            hint = '由譜面偵測：%s（相當明確）' % detected.name()
        else:
            hint = ('由譜面偵測：%s（不太確定，可能和關係大小調混淆，'
                    '必要時自己改）' % detected.name())
        lbl = QLabel(hint)
        lbl.setWordWrap(True)
        lbl.setStyleSheet('color: gray; font-size: 11px;')
        key_form.addRow(lbl)
        root.addWidget(key_box)

        # ── 圖形 ────────────────────────────────────────────────
        shape_box = QGroupBox('音型')
        shape_form = QFormLayout(shape_box)
        self._cb_kind = QComboBox()
        for label, value in _KINDS:
            self._cb_kind.addItem(label, value)
        self._cb_dir = QComboBox()
        for label, value in _DIRECTIONS:
            self._cb_dir.addItem(label, value)
        self._sp_count = QSpinBox()
        self._sp_count.setRange(2, 64)
        self._sp_count.setValue(8)
        self._sp_count.setSuffix(' 音')
        shape_form.addRow('類型', self._cb_kind)
        shape_form.addRow('方向', self._cb_dir)
        shape_form.addRow('音數', self._sp_count)
        root.addWidget(shape_box)

        # ── 起點與節奏 ──────────────────────────────────────────
        start_box = QGroupBox('起點與節奏')
        start_form = QFormLayout(start_box)
        self._sp_pitch = QSpinBox()
        self._sp_pitch.setRange(21, 108)
        self._sp_pitch.setValue(max(21, min(108, int(start_pitch))))
        self._sp_start = QSpinBox()
        self._sp_start.setRange(0, 9_999_999)
        self._sp_start.setValue(max(0, int(start_ms)))
        self._sp_start.setSuffix(' ms')
        self._cb_step = QComboBox()
        for label, beats in _STEPS:
            self._cb_step.addItem(label, beats)
        self._cb_step.setCurrentIndex(1)          # 預設八分音符
        self._cb_hand = QComboBox()
        self._cb_hand.addItem('右手', 0)
        self._cb_hand.addItem('左手', 1)
        self._cb_hand.setCurrentIndex(1 if int(hand) == 1 else 0)
        start_form.addRow('起始音高 (MIDI)', self._sp_pitch)
        start_form.addRow('起始時間', self._sp_start)
        start_form.addRow('每音間隔', self._cb_step)
        start_form.addRow('放到哪隻手', self._cb_hand)
        root.addWidget(start_box)

        # ── 預覽 ────────────────────────────────────────────────
        self._preview = QLabel('')
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            'font-family: Consolas; font-size: 11px; color: palette(text);'
            ' background: palette(base); padding: 6px; border: 1px solid palette(mid);')
        root.addWidget(self._preview)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

        for widget in (self._cb_tonic, self._cb_mode, self._cb_kind, self._cb_dir):
            widget.currentIndexChanged.connect(self._refresh_preview)
        self._sp_count.valueChanged.connect(self._refresh_preview)
        self._sp_pitch.valueChanged.connect(self._refresh_preview)
        self._refresh_preview()

    # ── 結果 ─────────────────────────────────────────────────────
    def key(self) -> Key:
        return Key(int(self._cb_tonic.currentData()),
                   str(self._cb_mode.currentData()))

    def params(self) -> dict:
        return {
            'kind':       str(self._cb_kind.currentData()),
            'key':        self.key(),
            'start_pitch': int(self._sp_pitch.value()),
            'count':      int(self._sp_count.value()),
            'direction':  int(self._cb_dir.currentData()),
            'start_ms':   float(self._sp_start.value()),
            'step_beats': float(self._cb_step.currentData()),
            'hand':       int(self._cb_hand.currentData()),
        }

    # ── 預覽 ─────────────────────────────────────────────────────
    def _label(self, pitch: int) -> str:
        name = PITCH_CLASS_NAMES[pitch % 12]
        if self._show_midi:
            return '%s%d' % (name, pitch)
        return '%s(%d)' % (name, midi_to_official_piano_index(pitch))

    def _refresh_preview(self) -> None:
        try:
            pitches = build_pattern(
                str(self._cb_kind.currentData()), self.key(),
                int(self._sp_pitch.value()), int(self._sp_count.value()),
                int(self._cb_dir.currentData()))
        except Exception:                       # noqa: BLE001
            self._preview.setText('（無法產生）')
            return
        if not pitches:
            self._preview.setText('（沒有音符）')
            return
        shown = pitches[:24]
        text = '  '.join(self._label(p) for p in shown)
        if len(pitches) > len(shown):
            text += '  …（共 %d 音）' % len(pitches)
        else:
            text += '\n共 %d 音' % len(pitches)
        # 選了指定音階時，上面的「調」只提供主音、大小調不算數；把實際用的
        # 音階寫出來，才不會以為自己選的調沒生效。
        used = pattern_key(str(self._cb_kind.currentData()), self.key(),
                           int(self._sp_pitch.value()))
        text += '　　實際音階：%s' % used.name()
        self._preview.setText(text)
