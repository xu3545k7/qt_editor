"""
hold_length_dialog.py
======================
長押長度修整（threshold 分級）對話框。

用途
----
MIDI 轉檔時，note-off 反映的是「聲音/踏板延音」的結束，而不是手指真正離鍵的
時間，於是 >=500ms 的音符全被當成長押（note_type=2）——結果 hold 太多、太長。

本工具依「音符時長（以音符值/拍數為單位，與 BPM 無關）」把音符分成三段，
每段用不同方式處理，讓 MIDI 延音長度轉成接近「真正按下去」的長度：

    時長 <  Tap 門檻          → 轉 Tap（只清長押旗標，長度不變）
    Tap 門檻 <= 時長 < 長押門檻 → 短 Hold：長度按「比例」砍（end = start + gate×ratio）
    時長 >= 長押門檻           → 長 Hold：尾端「前移固定 ms」砍（end -= advance_ms）

沒有「量化強度 / 容差」的模糊處理——規則直接套用。
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt5.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)


# 音符值 → 拍數（以四分音符 = 1 拍計；x/4 拍號）。
# 名稱和工具列「音符時值」下拉同一套：一個全音符切 N 份就叫 N 分音符。
_NOTE_VALUES = [
    ('全音符（4 拍）',          4.0),
    ('二分音符（2 拍）',        2.0),
    ('四分音符（1 拍）',        1.0),
    ('6分音符（四分三連）',     2.0 / 3.0),
    ('八分音符',               0.5),
    ('12分音符（八分三連）',    1.0 / 3.0),
    ('16分音符',              0.25),
    ('24分音符（16分三連）',    1.0 / 6.0),
    ('32分音符',              0.125),
    ('48分音符（32分三連）',    1.0 / 12.0),
]


#: 上次按「確定」時的參數存在設定檔的這個鍵底下，下次開窗直接帶回來。
_SETTINGS_KEY = 'hold_length_fix'


def _select_beats(cb: QComboBox, beats: float) -> None:
    """選最接近 `beats` 的那一項（存檔的值可能是舊版的浮點數）。"""
    best_i, best_d = 0, None
    for i in range(cb.count()):
        d = abs(float(cb.itemData(i)) - float(beats))
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    cb.setCurrentIndex(best_i)


def _note_value_combo(default_beats: float) -> QComboBox:
    from PyQt5.QtCore import QSize
    from .note_icons import VALUE_ICON_H, VALUE_ICON_W, note_value_icon
    cb = QComboBox()
    cb.setIconSize(QSize(int(round(VALUE_ICON_W * 20 / float(VALUE_ICON_H))), 20))
    for label, beats in _NOTE_VALUES:
        cb.addItem(note_value_icon(beats), label, float(beats))
    _select_beats(cb, default_beats)
    return cb


class HoldLengthDialog(QDialog):
    """分級長度修整參數對話框。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 has_selection: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle('長押長度修整（threshold 分級）')
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)

        intro = QLabel(
            'MIDI 延音會讓 hold 太多、太長。依「音符值（拍數）」把音符分三段：'
            '太短的轉 Tap（長度不變）、短 hold 按比例砍、長 hold 尾端前移固定 ms。'
        )
        intro.setWordWrap(True)
        intro.setStyleSheet('color: gray; font-size: 11px;')
        root.addWidget(intro)

        # ── 門檻 ────────────────────────────────────────────────
        th_box = QGroupBox('分級門檻（音符值，吃變速）')
        th_form = QFormLayout(th_box)
        self._cb_tap_th = _note_value_combo(0.5)    # 預設 1/8
        self._cb_hold_th = _note_value_combo(1.0)   # 預設 1/4
        th_form.addRow('Tap 門檻（短於此 → 轉 Tap）', self._cb_tap_th)
        th_form.addRow('長押門檻（長於此 → 長 Hold）', self._cb_hold_th)
        root.addWidget(th_box)

        # ── 短 hold：比例砍 ─────────────────────────────────────
        short_box = QGroupBox('短 Hold（Tap 門檻 ~ 長押門檻）')
        short_form = QFormLayout(short_box)
        self._sp_ratio = QSpinBox()
        self._sp_ratio.setRange(5, 100)
        self._sp_ratio.setValue(60)
        self._sp_ratio.setSuffix(' %')
        short_form.addRow('長度保留比例（end = start + gate×%）', self._sp_ratio)
        root.addWidget(short_box)

        # ── 長 hold：尾端前移 ms ─────────────────────────────────
        long_box = QGroupBox('長 Hold（>= 長押門檻）')
        long_form = QFormLayout(long_box)
        self._sp_advance = QSpinBox()
        self._sp_advance.setRange(0, 5000)
        self._sp_advance.setValue(40)
        self._sp_advance.setSuffix(' ms')
        long_form.addRow('尾端前移（end -= ms）', self._sp_advance)
        root.addWidget(long_box)

        # ── 收尾：同手最小間隔 ───────────────────────────────────
        gap_box = QGroupBox('同手最小間隔（收尾補刀）')
        gap_form = QFormLayout(gap_box)
        self._sp_tail_gap = QSpinBox()
        self._sp_tail_gap.setRange(0, 2000)
        self._sp_tail_gap.setValue(40)
        self._sp_tail_gap.setSuffix(' ms')
        self._sp_tail_gap.setToolTip(
            '固定前移量不管下一顆音在哪，砍完仍可能壓到同手的下一顆。\n'
            '這一刀把長押尾端再往前壓。設 0 = 不套用。'
        )
        gap_form.addRow('長押尾端至少留（0=關閉）', self._sp_tail_gap)

        # 壓到哪裡：只壓真的搶到鍵的，還是一律壓到同手下一顆。
        self._rb_gap_conflict = QRadioButton('只裁真的搶到鍵的（分解和弦保留）')
        self._rb_gap_next = QRadioButton('一律裁到同手下一顆')
        self._rb_gap_conflict.setChecked(True)
        ggrp = QButtonGroup(self)
        ggrp.addButton(self._rb_gap_conflict)
        ggrp.addButton(self._rb_gap_next)
        self._rb_gap_conflict.setToolTip(
            '官方的做法：長押可以蓋過同手的下一顆，只要兩者的鍵道範圍不重疊。\n'
            'real 難度 13175 顆長押有 7.7% 是這樣壓過去的，而那 1019 顆\n'
            '沒有任何一顆的鍵道是重疊的。分解和弦就靠這條保住。'
        )
        self._rb_gap_next.setToolTip(
            '舊行為：不管手按不按得住，一律裁到同手下一顆的起音。\n'
            '實測這樣會砍掉 85.6% 手明明按得住的長押。'
        )
        gap_form.addRow(self._rb_gap_conflict)
        gap_form.addRow(self._rb_gap_next)
        root.addWidget(gap_box)

        # ── 範圍 ────────────────────────────────────────────────
        scope_box = QGroupBox('範圍')
        scope_lay = QHBoxLayout(scope_box)
        self._rb_sel = QRadioButton('選取音符')
        self._rb_all = QRadioButton('整個譜面')
        sgrp = QButtonGroup(self)
        sgrp.addButton(self._rb_sel)
        sgrp.addButton(self._rb_all)
        if has_selection:
            self._rb_sel.setChecked(True)
        else:
            self._rb_all.setChecked(True)
            self._rb_sel.setEnabled(False)
        scope_lay.addWidget(self._rb_sel)
        scope_lay.addWidget(self._rb_all)
        scope_lay.addStretch()
        root.addWidget(scope_box)

        # ── 確認 ────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

        self._restore()

    # ── 記住上次的設定 ───────────────────────────────────────────
    def _restore(self) -> None:
        """帶回上次按「確定」時的參數；沒存過就維持上面的預設值。

        範圍（選取／整個譜面）不記：它取決於這次開窗時有沒有選取音符。
        """
        from .settings import settings
        saved = settings.get(_SETTINGS_KEY) or {}
        if not isinstance(saved, dict):
            return
        try:
            if 'tap_th_beats' in saved:
                _select_beats(self._cb_tap_th, float(saved['tap_th_beats']))
            if 'hold_th_beats' in saved:
                _select_beats(self._cb_hold_th, float(saved['hold_th_beats']))
            if 'short_ratio' in saved:
                self._sp_ratio.setValue(int(round(float(saved['short_ratio']) * 100)))
            if 'long_advance_ms' in saved:
                self._sp_advance.setValue(int(saved['long_advance_ms']))
            if 'tail_gap_ms' in saved:
                self._sp_tail_gap.setValue(int(saved['tail_gap_ms']))
            if 'tail_only_conflicts' in saved:
                if bool(saved['tail_only_conflicts']):
                    self._rb_gap_conflict.setChecked(True)
                else:
                    self._rb_gap_next.setChecked(True)
        except (TypeError, ValueError):
            pass                            # 設定檔被手改壞了，就用預設值

    def accept(self) -> None:
        # 只在「確定」時存；按取消代表這次的調整不算數。
        from .settings import settings
        saved = {k: v for k, v in self.params().items() if k != 'scope'}
        settings.set(_SETTINGS_KEY, saved)
        super().accept()

    # ── 結果 ─────────────────────────────────────────────────────
    def params(self) -> Dict[str, object]:
        return {
            'tap_th_beats':    float(self._cb_tap_th.currentData()),
            'hold_th_beats':   float(self._cb_hold_th.currentData()),
            'short_ratio':     float(self._sp_ratio.value()) / 100.0,
            'long_advance_ms': int(self._sp_advance.value()),
            'tail_gap_ms':     int(self._sp_tail_gap.value()),
            'tail_only_conflicts': bool(self._rb_gap_conflict.isChecked()),
            'scope':           'selected' if self._rb_sel.isChecked() else 'all',
        }
