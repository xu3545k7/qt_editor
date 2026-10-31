"""深色模式：整套 Qt 介面的調色盤。

譜面畫布本來就是深色，但選單、工具列、對話框沿用系統主題，兩者放在一起
會有明顯的亮暗落差。這裡只換 QPalette（不用 stylesheet），所以各平台的
原生控制項外觀都還在，只是配色變深。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette

# 文字一律接近純白。深色底上如果只用中灰（220 左右）字，對比不足、
# 小字級的工具列標籤會糊掉。
_TEXT = QColor(250, 250, 252)

_DARK = {
    QPalette.Window:          QColor(37, 37, 40),
    QPalette.WindowText:      _TEXT,
    QPalette.Base:            QColor(24, 24, 27),
    QPalette.AlternateBase:   QColor(37, 37, 40),
    QPalette.ToolTipBase:     QColor(60, 60, 64),
    QPalette.ToolTipText:     _TEXT,
    QPalette.Text:            _TEXT,
    # 按鈕底色要和視窗底**看得出差別**。原本是 (42,42,46)、視窗是 (37,37,40)，
    # 只差 5 階，chip 樣式套下去完全看不出哪裡可以按（實測截圖分不出來）。
    QPalette.Button:          QColor(55, 55, 61),
    QPalette.ButtonText:      _TEXT,
    QPalette.BrightText:      QColor(255, 110, 110),
    QPalette.Link:            QColor(105, 180, 255),
    QPalette.Highlight:       QColor(62, 128, 214),
    QPalette.HighlightedText: QColor(255, 255, 255),
    QPalette.PlaceholderText: QColor(150, 150, 155),
    # 邊框／立體感用的角色。不設的話 `palette(mid)` 會沿用淺色主題的值，
    # 工具列按鈕的框在深色底上會變成刺眼的亮線。
    QPalette.Light:           QColor(86, 86, 94),
    QPalette.Midlight:        QColor(72, 72, 79),      # 按鈕 hover
    QPalette.Mid:             QColor(96, 96, 102),
    QPalette.Dark:            QColor(26, 26, 29),
    QPalette.Shadow:          QColor(14, 14, 16),
}
_DISABLED = {
    QPalette.WindowText:      QColor(150, 150, 156),
    QPalette.Text:            QColor(150, 150, 156),
    QPalette.ButtonText:      QColor(150, 150, 156),
    QPalette.HighlightedText: QColor(190, 190, 195),
}


# 有些控制項不吃 QPalette —— Windows 原生樣式的 QTabBar 分頁、以及
# QComboBox / QLineEdit 的下拉區塊，底色是樣式自己畫的淺色。只換調色盤的話
# 字變白、底還是白，那些地方就整個看不見了。這裡只針對那幾類補最小限度的
# 樣式，其餘控制項仍然走原生外觀。
#: 整套介面的樣式。**深色淺色共用這一份**——以前只有深色有 QSS，淺色是裸的
#: 原生外觀，兩邊長得完全不一樣。顏色全部走 `palette(...)`，所以同一份規則在
#: 兩種調色盤下都成立。
#:
#: 現代化的重點（對照改之前）：
#:   分頁    每個分頁一個 1px 硬框、選取的整塊填滿高亮（Windows 2000 的樣子）
#:           → 無框、選取的用 2px 底線 ＋ 強調色文字
#:   按鈕    3px 圓角 ＋ 永遠看得到的 1px 框
#:           → 6px 圓角、平常無框、滑過才浮出底色、按下去才有強調色
#:   輸入框  2px 內距
#:           → 5px 10px，而且 focus 時框線換成強調色
#:   間距    各處硬塞
#:           → 統一的 6/10/14 階
_QSS = """
/* ── 分頁：無框 ＋ 底線指示 ─────────────────────────────────────── */
QTabWidget::pane { border: none; }
QTabBar::tab {
    background: transparent;
    color: palette(window-text);
    border: none;
    border-bottom: 2px solid transparent;
    padding: 7px 14px;
    margin-right: 2px;
}
QTabBar::tab:hover:!selected { background: palette(alternate-base); }
QTabBar::tab:selected {
    border-bottom: 2px solid palette(highlight);
    color: palette(highlight);
    font-weight: 600;
}

/* ── 工具列按鈕：有底色的 chip ─────────────────────────────────
   **平常就要看得出是按鈕。** 原本 `_tab_button` 的註解寫得很清楚：「不用
   autoRaise：分頁上的按鈕若只在滑鼠移上去才看得到邊框，就看不出哪裡可以按，
   也看不出 checkable 的按鈕現在是不是開著」。那個判斷是對的。
   現代化改的是**怎麼**表現，不是拿掉表現：以前是 1px 硬框 ＋ 3px 圓角，
   現在是填色 chip ＋ 6px 圓角，滑過變亮。                                 */
QToolButton {
    background: palette(button);
    color: palette(button-text);
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
}
QToolButton:hover   { background: palette(midlight);
                      border-color: palette(mid); }
QToolButton:pressed { background: palette(dark); }
QToolButton:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
    font-weight: 600;
}
QToolButton:disabled { color: palette(mid); background: transparent; }
QToolButton::menu-indicator { image: none; }

/* ── 輸入類：圓角、呼吸空間、focus 時框線變強調色 ───────────────── */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QAbstractSpinBox {
    background: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: palette(highlight);
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QAbstractSpinBox:focus { border-color: palette(highlight); }
QComboBox:disabled, QLineEdit:disabled, QAbstractSpinBox:disabled {
    color: palette(mid);
}
/* drop-down 不要整個改寫：把 border 拿掉會連原生的箭頭一起沒了，下拉看起來
   就是一個普通的框，看不出可以點開（實測截圖分不出來）。只放寬一點。 */
QComboBox::drop-down { width: 20px; }
QComboBox QAbstractItemView {
    background: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    selection-background-color: palette(highlight);
    outline: none;
}

/* ── 一般按鈕 ─────────────────────────────────────────────────── */
QPushButton {
    background: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover    { background: palette(midlight); }
QPushButton:pressed  { background: palette(dark); }
QPushButton:default  { border-color: palette(highlight); font-weight: 600; }
QPushButton:disabled { color: palette(mid); border-color: palette(dark); }

/* ── 分組框：標題當小標，框線淡一點 ─────────────────────────────── */
QGroupBox {
    border: 1px solid palette(midlight);
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: palette(window-text);
}

/* ── 選單：圓角、項目有內距 ───────────────────────────────────── */
QMenu {
    background: palette(window);
    color: palette(window-text);
    border: 1px solid palette(mid);
    padding: 4px;
}
QMenu::item { padding: 5px 20px; border-radius: 4px; }
QMenu::item:selected { background: palette(highlight);
                       color: palette(highlighted-text); }
QMenu::separator { height: 1px; background: palette(midlight); margin: 4px 8px; }
QMenuBar { background: palette(window); color: palette(window-text); }
QMenuBar::item { padding: 5px 10px; border-radius: 4px; background: transparent; }
QMenuBar::item:selected { background: palette(highlight);
                          color: palette(highlighted-text); }

/* ── 其餘 ─────────────────────────────────────────────────────── */
QToolBar   { background: palette(window); border: none; spacing: 4px; }
QStatusBar { background: palette(window); color: palette(window-text); }
QDialog, QMessageBox { background: palette(window); color: palette(window-text); }
QCheckBox, QRadioButton, QLabel { color: palette(window-text); }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
QHeaderView::section {
    background: palette(window); color: palette(window-text);
    border: none; border-bottom: 1px solid palette(mid); padding: 5px 8px;
}
QTableView, QListWidget, QTreeWidget {
    background: palette(base); color: palette(text);
    border: 1px solid palette(mid); border-radius: 6px;
    selection-background-color: palette(highlight);
}
QToolTip {
    background: palette(base); color: palette(text);
    border: 1px solid palette(mid); padding: 4px 6px;
}
QScrollBar:vertical   { background: transparent; width: 11px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle {
    background: palette(mid); border-radius: 5px; min-height: 28px; min-width: 28px;
}
QScrollBar::handle:hover { background: palette(light); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


def _repolish(app) -> None:
    """讓已經開著的視窗立刻套用新主題。

    切換主題時如果有對話框正開著，它不一定會自動重繪 —— 對每個 top-level
    視窗重新 polish 一次，避免出現「主視窗變深色、對話框還是淺色」。
    """
    for widget in app.topLevelWidgets():
        widget.setPalette(app.palette())
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()


def apply_theme(app, dark: bool) -> None:
    """套用深色／淺色主題。

    **兩種模式套同一份 QSS**，只有調色盤不同。以前淺色是裸的原生外觀、深色才
    有樣式，兩邊看起來像兩個程式。
    """
    if not dark:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(_QSS)
        _repolish(app)
        return
    pal = QPalette()
    for role, color in _DARK.items():
        pal.setColor(role, color)
    for role, color in _DISABLED.items():
        pal.setColor(QPalette.Disabled, role, color)
    app.setPalette(pal)
    app.setStyleSheet(_QSS)
    _repolish(app)
