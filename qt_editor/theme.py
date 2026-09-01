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
    QPalette.Button:          QColor(42, 42, 46),
    QPalette.ButtonText:      _TEXT,
    QPalette.BrightText:      QColor(255, 110, 110),
    QPalette.Link:            QColor(105, 180, 255),
    QPalette.Highlight:       QColor(62, 128, 214),
    QPalette.HighlightedText: QColor(255, 255, 255),
    QPalette.PlaceholderText: QColor(150, 150, 155),
    # 邊框／立體感用的角色。不設的話 `palette(mid)` 會沿用淺色主題的值，
    # 工具列按鈕的框在深色底上會變成刺眼的亮線。
    QPalette.Light:           QColor(70, 70, 76),
    QPalette.Midlight:        QColor(56, 56, 61),
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
_DARK_QSS = """
QTabBar::tab {
    background: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    padding: 4px 10px;
}
QTabBar::tab:selected   { background: palette(highlight); color: palette(highlighted-text); }
QTabBar::tab:!selected:hover { background: palette(midlight); }
QTabWidget::pane        { border: 1px solid palette(mid); }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QAbstractSpinBox {
    background: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    padding: 2px 4px;
}
QComboBox QAbstractItemView {
    background: palette(base);
    color: palette(text);
    selection-background-color: palette(highlight);
}
QDialog, QMessageBox { background: palette(window); color: palette(window-text); }
QMessageBox QLabel   { color: palette(window-text); }
/* 工具列按鈕：程式裡有些地方另外設了 border/padding，那些 inline 樣式會
   接管原生繪製、只留下預設的淺色底。這裡補上底色與文字色，inline 樣式
   沒定義的屬性才有得繼承。 */
QToolButton {
    background: palette(button);
    color: palette(button-text);
}
QToolButton:hover    { background: palette(midlight); }
QToolButton:pressed  { background: palette(dark); }
QToolButton:disabled { color: palette(mid); }
QToolBar    { background: palette(window); border: none; }
QStatusBar  { background: palette(window); color: palette(window-text); }
QPushButton {
    background: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 3px;
    padding: 4px 14px;
}
QPushButton:hover    { background: palette(midlight); }
QPushButton:pressed  { background: palette(dark); }
QPushButton:default  { border: 1px solid palette(highlight); }
QPushButton:disabled { color: palette(mid); border-color: palette(dark); }
QCheckBox, QRadioButton, QGroupBox, QLabel { color: palette(window-text); }
QGroupBox::title { color: palette(window-text); }
QMenu       { background: palette(window); color: palette(window-text); }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenuBar    { background: palette(window); color: palette(window-text); }
QMenuBar::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QHeaderView::section, QTableView, QListWidget, QTreeWidget {
    background: palette(base); color: palette(text);
}
QToolTip { background: palette(base); color: palette(text); border: 1px solid palette(mid); }
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
    """套用深色／淺色主題。`dark=False` 會還原成系統預設調色盤與樣式。"""
    if not dark:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet('')
        _repolish(app)
        return
    pal = QPalette()
    for role, color in _DARK.items():
        pal.setColor(role, color)
    for role, color in _DISABLED.items():
        pal.setColor(QPalette.Disabled, role, color)
    app.setPalette(pal)
    app.setStyleSheet(_DARK_QSS)
    _repolish(app)
