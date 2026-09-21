"""「MIDI 轉譜」對話框：模式、鍵道範圍、和絃重疊、自訂參數表。

參數表是照 `SmartChartSettings` 的欄位自動長出來的（整份，可搜尋），所以排譜器
加了新參數這裡就會自己跟著多一列，不必兩邊維護。只有和選項打架的那幾個欄位
（`arrange_options.LOCKED_FIELDS`）不開放。
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QRadioButton, QScrollArea, QSpinBox,
                             QVBoxLayout, QWidget)

from .arrange_options import (LOCKED_FIELDS, MODE_CUSTOM, MODE_EATHER, MODE_FLAT,
                              MODE_LABELS, MODE_OFFICIAL, MODES, TOTAL_GAME_KEYS,
                              ArrangeOptions, coerce_value)
from .arrange_param_text import grouped_fields, label_for, tooltip_for
from .settings import settings
from .smart_chart import (STYLE_EATHER, STYLE_OFFICIAL, SmartChartSettings,
                          settings_for_style)
from .ui_text import tr

OVERLAP_BY_MODE, OVERLAP_ON, OVERLAP_OFF = range(3)

#: 不是智能排譜的模式，選了之後譜面看起來會「零零碎碎」——要讓人看得出來
UNARRANGED_MODES = (MODE_FLAT,)

MODE_HINTS = {
    MODE_FLAT: '音高直接線性對應鍵道，不跑任何排譜通道。最快，但只是攤開，不是譜。',
    MODE_EATHER: '這個曲庫的風格：靠收窄擠空間、幾乎不重疊、表情記號留給人自己標。',
    MODE_OFFICIAL: '官方語料的風格：靠鍵道重疊擠空間、只有單手同時 4 音才收窄、自動標滑音。',
    MODE_CUSTOM: '以某一種風格為底，再改下面的參數表。只有改動過的欄位會被記住。',
}


def _spin(value: float, is_int: bool) -> QWidget:
    box = QSpinBox() if is_int else QDoubleSpinBox()
    box.setRange(-99999, 99999)
    if not is_int:
        box.setDecimals(3)
        box.setSingleStep(0.1)
    box.setValue(int(value) if is_int else float(value))
    return box


class _ParamTable(QWidget):
    """SmartChartSettings 的完整參數表：一欄一個欄位，上面有搜尋框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('搜尋參數名稱…'))
        self.search.textChanged.connect(self._filter)
        box.addWidget(self.search)

        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        self._form = QFormLayout(inner)
        self._form.setLabelAlignment(Qt.AlignRight)
        area.setWidget(inner)
        box.addWidget(area, 1)

        self._widgets: Dict[str, QWidget] = {}
        self._labels: Dict[str, QLabel] = {}
        self._headings: list = []
        self._base: Dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        defaults = SmartChartSettings()
        for title, names in grouped_fields():
            heading = None
            for name in names:
                if name in LOCKED_FIELDS:
                    continue
                if heading is None:
                    # 分組標題：整列一條，搜尋時跟著整組一起隱藏
                    heading = QLabel('── %s ──' % tr(title))
                    heading.setStyleSheet('color: #777; margin-top: 6px;')
                    self._form.addRow(heading)
                    self._headings.append((heading, names))
                current = getattr(defaults, name)
                if isinstance(current, bool):
                    widget: QWidget = QCheckBox()
                    widget.setChecked(bool(current))
                elif isinstance(current, int):
                    widget = _spin(current, True)
                elif isinstance(current, float):
                    widget = _spin(current, False)
                else:
                    # tuple（步伐表）與 Optional[float]（None = 不限制）都用文字
                    widget = QLineEdit()
                    widget.setPlaceholderText(tr('空白 = 不限制') if current is None
                                              else tr('用逗號分隔'))
                label = QLabel(label_for(name))
                tip = tooltip_for(name)
                label.setToolTip(tip)
                widget.setToolTip(tip)
                self._form.addRow(label, widget)
                self._widgets[name] = widget
                self._labels[name] = label

    def _filter(self, text: str) -> None:
        """中文名、欄位名、分組名都可以搜。"""
        needle = str(text or '').strip().lower()
        shown = set()
        for name, widget in self._widgets.items():
            show = (needle in name.lower()
                    or needle in label_for(name).lower())
            widget.setVisible(show)
            self._labels[name].setVisible(show)
            if show:
                shown.add(name)
        for heading, names in self._headings:
            title = heading.text().strip('─ ').lower()
            if needle and needle in title:
                # 搜到組名就把整組叫回來
                for name in names:
                    if name in self._widgets:
                        self._widgets[name].setVisible(True)
                        self._labels[name].setVisible(True)
                        shown.add(name)
            heading.setVisible(any(n in shown for n in names))

    # ── 值的進出 ──────────────────────────────────────────────────────

    def set_base(self, style: str, overrides: Optional[Dict[str, Any]] = None) -> None:
        """把表格填成「某風格的預設值 + 使用者的覆寫」。"""
        base = settings_for_style(style)
        self._base = {f.name: getattr(base, f.name) for f in fields(SmartChartSettings)}
        for name, value in self._base.items():
            if name in self._widgets:
                self._set_widget(name, value)
        for name, value in (overrides or {}).items():
            if name in self._widgets:
                self._set_widget(name, value)

    def _set_widget(self, name: str, value: Any) -> None:
        widget = self._widgets[name]
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(int(value) if isinstance(widget, QSpinBox) else float(value))
        else:
            if value is None:
                widget.setText('')
            elif isinstance(value, (tuple, list)):
                widget.setText(', '.join(_number_text(v) for v in value))
            else:
                widget.setText(_number_text(value))

    def _widget_value(self, name: str) -> Any:
        widget = self._widgets[name]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        text = widget.text().strip()
        if not text:
            return None
        return coerce_value(name, text)

    def overrides(self) -> Dict[str, Any]:
        """只回傳和底層風格不一樣的欄位。"""
        out: Dict[str, Any] = {}
        for name in self._widgets:
            try:
                value = self._widget_value(name)
            except (TypeError, ValueError):
                continue
            base = self._base.get(name)
            if isinstance(base, tuple) and isinstance(value, (list, tuple)):
                same = tuple(float(v) for v in value) == tuple(float(v) for v in base)
            elif isinstance(base, float) and isinstance(value, (int, float)):
                same = abs(float(value) - float(base)) < 1e-9
            else:
                same = value == base
            if not same:
                out[name] = value
        return out


def _number_text(value: Any) -> str:
    if isinstance(value, float):
        return ('%.6f' % value).rstrip('0').rstrip('.')
    return str(value)


class ArrangeDialog(QDialog):
    """回傳一份 `ArrangeOptions`（`options()`）。"""

    #: 按了「先不轉譜」時，exec_() 之後這個是 True（對話框本身還是 Accepted）
    skipped = False

    def __init__(self, parent=None, title: str = 'MIDI 轉譜',
                 intro: str = '', options: Optional[ArrangeOptions] = None,
                 allow_skip: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr(title))
        self.resize(640, 0)
        opts = (options or ArrangeOptions.from_dict(settings.get('arrange_options'))).normalised()
        layout = QVBoxLayout(self)

        if intro:
            label = QLabel(tr(intro))
            label.setWordWrap(True)
            layout.addWidget(label)

        # ── 模式 ──────────────────────────────────────────────────────
        mode_box = QGroupBox(tr('轉譜模式'))
        mode_layout = QVBoxLayout(mode_box)
        self._modes: Dict[str, QRadioButton] = {}
        for mode in MODES:
            rb = QRadioButton(tr(MODE_LABELS[mode]))
            rb.setToolTip(tr(MODE_HINTS[mode]))
            mode_layout.addWidget(rb)
            self._modes[mode] = rb
        # 先選好再接訊號：setChecked 會立刻叫 _mode_changed，那時候下面的
        # 提示文字、重疊選單、自訂區塊都還沒建出來。
        self._modes[opts.mode].setChecked(True)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('color: #555;')
        mode_layout.addWidget(self.hint)
        layout.addWidget(mode_box)

        # ── 鍵道範圍 ──────────────────────────────────────────────────
        lane_box = QGroupBox(tr('鍵道範圍'))
        lane_layout = QVBoxLayout(lane_box)
        self.limit_lanes = QCheckBox(tr('限制排譜可以用的鍵道'))
        self.limit_lanes.setChecked(opts.lanes_limited)
        lane_layout.addWidget(self.limit_lanes)
        row = QHBoxLayout()
        self.lane_lo = QSpinBox()
        self.lane_hi = QSpinBox()
        for spin in (self.lane_lo, self.lane_hi):
            spin.setRange(1, TOTAL_GAME_KEYS)
        self.lane_lo.setValue(opts.lane_lo + 1)
        self.lane_hi.setValue(opts.lane_hi + 1)
        row.addWidget(QLabel(tr('第')))
        row.addWidget(self.lane_lo)
        row.addWidget(QLabel(tr('格 到 第')))
        row.addWidget(self.lane_hi)
        row.addWidget(QLabel(tr('格（共 %d 格）') % TOTAL_GAME_KEYS))
        row.addStretch(1)
        lane_layout.addLayout(row)
        self.limit_lanes.toggled.connect(self._lane_toggle)
        self._lane_toggle(self.limit_lanes.isChecked())
        layout.addWidget(lane_box)

        # ── 重疊 ──────────────────────────────────────────────────────
        overlap_box = QGroupBox(tr('和絃的鍵道重疊'))
        overlap_layout = QVBoxLayout(overlap_box)
        self.overlap = QComboBox()
        self.overlap.addItem(tr('照模式決定（官方風格允許、其他不允許）'), OVERLAP_BY_MODE)
        self.overlap.addItem(tr('允許重疊：塞不下就疊上去，不收窄'), OVERLAP_ON)
        self.overlap.addItem(tr('不允許重疊：同一刻的音一定排開'), OVERLAP_OFF)
        if opts.allow_chord_overlap is True:
            self.overlap.setCurrentIndex(OVERLAP_ON)
        elif opts.allow_chord_overlap is False:
            self.overlap.setCurrentIndex(OVERLAP_OFF)
        overlap_layout.addWidget(self.overlap)
        layout.addWidget(overlap_box)

        # ── 自訂 ──────────────────────────────────────────────────────
        self.custom_box = QGroupBox(tr('自訂參數'))
        custom_layout = QVBoxLayout(self.custom_box)
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel(tr('以哪一種風格為底：')))
        self.base_style = QComboBox()
        self.base_style.addItem(tr('Eather'), STYLE_EATHER)
        self.base_style.addItem(tr('官方'), STYLE_OFFICIAL)
        self.base_style.setCurrentIndex(1 if opts.base_style == STYLE_OFFICIAL else 0)
        base_row.addWidget(self.base_style)
        base_row.addStretch(1)
        base_row.addWidget(QLabel(tr('預設集：')))
        self.presets = QComboBox()
        self.presets.setMinimumWidth(140)
        base_row.addWidget(self.presets)
        load_btn = QPushButton(tr('載入'))
        load_btn.clicked.connect(self._load_preset)
        save_btn = QPushButton(tr('另存…'))
        save_btn.clicked.connect(self._save_preset)
        del_btn = QPushButton(tr('刪除'))
        del_btn.clicked.connect(self._delete_preset)
        for b in (load_btn, save_btn, del_btn):
            base_row.addWidget(b)
        custom_layout.addLayout(base_row)
        self.table = _ParamTable()
        self.table.set_base(opts.base_style, opts.overrides)
        self.base_style.currentIndexChanged.connect(self._base_style_changed)
        custom_layout.addWidget(self.table, 1)
        layout.addWidget(self.custom_box, 1)
        self._refresh_presets()

        for rb in self._modes.values():
            rb.toggled.connect(self._mode_changed)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr('開始轉譜'))
        reset = buttons.addButton(tr('回到預設'), QDialogButtonBox.ResetRole)
        reset.setToolTip(tr('模式回到 Eather 智能轉譜、整條鍵道都能用、重疊照模式決定。'))
        reset.clicked.connect(self._reset_to_defaults)
        if allow_skip:
            skip = buttons.addButton(tr('先不轉譜'), QDialogButtonBox.DestructiveRole)
            skip.setToolTip(tr('只匯入音符，停留在 MIDI 編輯模式，之後隨時可以再轉。'))
            skip.clicked.connect(self._skip)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._mode_changed()

    # ── 互動 ──────────────────────────────────────────────────────────

    def _lane_toggle(self, on: bool) -> None:
        self.lane_lo.setEnabled(bool(on))
        self.lane_hi.setEnabled(bool(on))

    def _reset_to_defaults(self) -> None:
        """回到出廠狀態。上次亂按留下來的設定不該一路跟著使用者。"""
        self._modes[MODE_EATHER].setChecked(True)
        self.limit_lanes.setChecked(False)
        self.lane_lo.setValue(1)
        self.lane_hi.setValue(TOTAL_GAME_KEYS)
        self.overlap.setCurrentIndex(OVERLAP_BY_MODE)
        self.base_style.setCurrentIndex(0)
        self.table.set_base(STYLE_EATHER, {})

    def _mode_changed(self, *_args) -> None:
        mode = self.mode()
        self.hint.setText(tr(MODE_HINTS[mode]))
        # 不是智能排譜的模式要顯眼：對話框記得上次的選擇，使用者常常直接按
        # 「開始轉譜」，然後只看到譜面變得零零碎碎，不知道是模式的關係。
        if mode in UNARRANGED_MODES:
            self.hint.setStyleSheet('color: #b00; font-weight: bold;')
            self.hint.setText(tr('注意：%s 不會排譜，出來的譜面會零零碎碎。'
                                 % MODE_LABELS[mode]))
        else:
            self.hint.setStyleSheet('color: #555;')
        custom = mode == MODE_CUSTOM
        self.custom_box.setVisible(custom)
        # 直接平攤不跑排譜通道，重疊與否沒有意義
        self.overlap.setEnabled(mode != MODE_FLAT)
        self.adjustSize()

    def _base_style_changed(self, *_args) -> None:
        self.table.set_base(self.base_style.currentData(), {})

    # ── 預設集 ────────────────────────────────────────────────────────

    def _stored_presets(self) -> Dict[str, Any]:
        data = settings.get('arrange_presets')
        return dict(data) if isinstance(data, dict) else {}

    def _refresh_presets(self, select: str = '') -> None:
        self.presets.clear()
        for name in sorted(self._stored_presets()):
            self.presets.addItem(name)
        if select:
            index = self.presets.findText(select)
            if index >= 0:
                self.presets.setCurrentIndex(index)

    def _load_preset(self) -> None:
        name = self.presets.currentText()
        data = self._stored_presets().get(name)
        if not data:
            return
        opts = ArrangeOptions.from_dict(data)
        self._modes[opts.mode].setChecked(True)
        self.limit_lanes.setChecked(opts.lanes_limited)
        self.lane_lo.setValue(opts.lane_lo + 1)
        self.lane_hi.setValue(opts.lane_hi + 1)
        self.overlap.setCurrentIndex(
            OVERLAP_BY_MODE if opts.allow_chord_overlap is None
            else (OVERLAP_ON if opts.allow_chord_overlap else OVERLAP_OFF))
        self.base_style.setCurrentIndex(1 if opts.base_style == STYLE_OFFICIAL else 0)
        self.table.set_base(opts.base_style, opts.overrides)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr('另存預設集'), tr('名稱：'),
                                        text=self.presets.currentText())
        name = str(name or '').strip()
        if not ok or not name:
            return
        data = self._stored_presets()
        data[name] = self.options().to_dict()
        settings.set('arrange_presets', data)
        self._refresh_presets(name)

    def _delete_preset(self) -> None:
        name = self.presets.currentText()
        if not name:
            return
        if QMessageBox.question(self, tr('刪除預設集'),
                                tr('要刪掉「%s」嗎？') % name) != QMessageBox.Yes:
            return
        data = self._stored_presets()
        data.pop(name, None)
        settings.set('arrange_presets', data)
        self._refresh_presets()

    # ── 結果 ──────────────────────────────────────────────────────────

    def mode(self) -> str:
        for mode, rb in self._modes.items():
            if rb.isChecked():
                return mode
        return MODE_EATHER

    def options(self) -> ArrangeOptions:
        overlap_choice = self.overlap.currentData()
        allow = (None if overlap_choice == OVERLAP_BY_MODE
                 else overlap_choice == OVERLAP_ON)
        limited = self.limit_lanes.isChecked()
        return ArrangeOptions(
            mode=self.mode(),
            lane_lo=(self.lane_lo.value() - 1) if limited else 0,
            lane_hi=(self.lane_hi.value() - 1) if limited else TOTAL_GAME_KEYS - 1,
            allow_chord_overlap=allow,
            base_style=self.base_style.currentData(),
            overrides=self.table.overrides() if self.mode() == MODE_CUSTOM else {},
        ).normalised()

    def _skip(self) -> None:
        self.skipped = True
        super().accept()

    def accept(self) -> None:                     # noqa: D102
        self.skipped = False
        settings.set('arrange_options', self.options().to_dict())
        super().accept()
