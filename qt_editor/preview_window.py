"""
preview_window.py
=================
靜態譜面預覽視窗：使用 graphic/ 資料夾內的圖片繪製各類音符。

音符繪製規則
------------
tap  (note_type=0) : 依 hand 使用 left_note / right_note。
                     寬度 = 0.9 * 格寬；高度按圖片原始比例。
hold (note_type=2) : 主體以 left_note / right_note 垂直拉伸至 endtime，
                     寬度 = 0.8 * 格寬；
                     starttime 處另畫一個 tap（0.9 寬，原始比例），置於較高圖層。
soft (note_type=1) : 同 tap，但不分左右手，使用 soft_note 圖片。
stac (note_type=3) : 同 tap，依 hand 使用 LeftStac / RightStac。
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QPainter, QPixmap, QPen, QFont
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .models import GNote, TOTAL_GAME_KEYS

# ── 常數 ──────────────────────────────────────────────────────────────────────
GRAPHIC_DIR = os.path.join(os.path.dirname(__file__), 'graphic')
CANVAS_W    = 868           # 預覽畫布寬度 (px)，需可被 TOTAL_GAME_KEYS 整除為整數
PX_PER_MS   = 0.25          # 時間軸縮放：每毫秒佔幾像素（Ctrl+滾輪可調）
MARGIN_TOP  = 40            # 頂部留白 (px)
MARGIN_BOT  = 100           # 底部留白 (px)

# ── 圖片快取（module-level，避免重複載入）────────────────────────────────────
_PIXMAP_CACHE: dict[str, QPixmap] = {}


def _pix(name: str) -> QPixmap:
    """依檔名讀取 graphic/ 資料夾中的圖片，並做 module-level 快取。"""
    if name not in _PIXMAP_CACHE:
        path = os.path.join(GRAPHIC_DIR, name)
        _PIXMAP_CACHE[name] = QPixmap(path)
    return _PIXMAP_CACHE[name]


# ── 畫布 Widget ───────────────────────────────────────────────────────────────
class PreviewCanvas(QWidget):
    """實際繪製譜面的 QWidget，嵌入 QScrollArea 使用。"""

    def __init__(self, notes: List[GNote], px_per_ms: float = PX_PER_MS) -> None:
        super().__init__()
        self.notes     = notes
        self.px_per_ms = px_per_ms
        self._update_size()

    # ------------------------------------------------------------------
    # 公開介面
    # ------------------------------------------------------------------

    def set_notes(self, notes: List[GNote]) -> None:
        self.notes = notes
        self._update_size()
        self.update()

    def set_px_per_ms(self, v: float) -> None:
        self.px_per_ms = max(0.05, min(2.0, v))
        self._update_size()
        self.update()

    # ------------------------------------------------------------------
    # 尺寸
    # ------------------------------------------------------------------

    def _max_ms(self) -> int:
        if not self.notes:
            return 10_000
        return max(
            n.end if n.note_type == 2 else n.start
            for n in self.notes
        )

    def _update_size(self) -> None:
        h = MARGIN_TOP + int(self._max_ms() * self.px_per_ms) + MARGIN_BOT
        self.setFixedSize(CANVAS_W, h)

    # ------------------------------------------------------------------
    # 座標轉換
    # ------------------------------------------------------------------

    def ms_y(self, ms: int) -> int:
        """ms → y pixel（時間由上往下增加）。"""
        return MARGIN_TOP + int(ms * self.px_per_ms)

    def _note_xw(self, n: GNote, scale: float) -> tuple[int, int]:
        """回傳 (x_start, draw_width) 以 px 為單位。"""
        cell_w = CANVAS_W / TOTAL_GAME_KEYS
        span   = n.max_key - n.min_key + 1
        full_w = span * cell_w
        draw_w = full_w * scale
        x      = n.min_key * cell_w + (full_w - draw_w) * 0.5
        return int(x), int(draw_w)

    # ------------------------------------------------------------------
    # paintEvent
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        qp = QPainter(self)
        qp.setRenderHint(QPainter.SmoothPixmapTransform)
        qp.setRenderHint(QPainter.Antialiasing)

        # 背景
        qp.fillRect(self.rect(), QColor(15, 15, 25))

        # 格線
        self._draw_grid(qp)

        # 第一遍：hold 主體（較低圖層）
        for n in self.notes:
            if n.note_type == 2:
                self._draw_hold_body(qp, n)

        # 第二遍：所有 note head（較高圖層）
        for n in self.notes:
            self._draw_note_head(qp, n)

    # ------------------------------------------------------------------
    # 格線
    # ------------------------------------------------------------------

    def _draw_grid(self, qp: QPainter) -> None:
        h = self.height()
        cell_w = CANVAS_W / TOTAL_GAME_KEYS

        # 垂直鍵道線
        for i in range(TOTAL_GAME_KEYS + 1):
            x = int(i * cell_w)
            color = QColor(75, 75, 100) if i % 4 == 0 else QColor(35, 35, 50)
            qp.setPen(color)
            qp.drawLine(x, 0, x, h)

        # 水平時間線（每秒一條）
        fnt = QFont()
        fnt.setPointSize(7)
        qp.setFont(fnt)
        for ms in range(0, self._max_ms() + 2000, 1000):
            y = self.ms_y(ms)
            if 0 <= y <= h:
                qp.setPen(QColor(50, 50, 75))
                qp.drawLine(0, y, CANVAS_W, y)
                if ms % 5000 == 0:
                    qp.setPen(QColor(140, 140, 180))
                    sec = ms // 1000
                    qp.drawText(2, y - 2, f'{sec//60}:{sec%60:02d}')

    # ------------------------------------------------------------------
    # Hold 主體
    # ------------------------------------------------------------------

    def _draw_hold_body(self, qp: QPainter, n: GNote) -> None:
        """將 left_note / right_note 圖片垂直拉伸至 endtime，寬度 0.8。"""
        x, draw_w = self._note_xw(n, 0.8)
        y_start   = self.ms_y(n.start)
        y_end     = self.ms_y(n.end)
        h_body    = y_end - y_start
        if h_body <= 0:
            return

        img_name = 'left_note.png' if n.hand == 1 else 'right_note.png'
        img = _pix(img_name)
        if img.isNull():
            return

        qp.drawPixmap(QRect(x, y_start, draw_w, h_body), img)

    # ------------------------------------------------------------------
    # Note Head（tap / soft / stac，以及 hold 的 tap head）
    # ------------------------------------------------------------------

    def _draw_note_head(self, qp: QPainter, n: GNote) -> None:
        """依音符類型選圖，以 0.9 寬、原始比例繪製於 starttime 位置。"""
        nt   = n.note_type
        hand = n.hand

        # 選圖 ──────────────────────────────────────────────────────────
        if nt == 1:                              # soft
            img_name = 'soft_note.png'
        elif nt == 3:                            # staccato
            img_name = 'LeftStac.png' if hand == 1 else 'RightStac.png'
        else:                                    # tap (0) 或 hold head (2)
            img_name = 'left_note.png' if hand == 1 else 'right_note.png'

        img = _pix(img_name)
        if img.isNull() or img.width() == 0:
            return

        # 尺寸（0.9 寬，依原圖比例決定高度）──────────────────────────────
        x, draw_w = self._note_xw(n, 0.9)
        aspect    = img.height() / img.width()
        draw_h    = int(draw_w * aspect)

        # y：圖片垂直置中於 starttime ──────────────────────────────────
        y = self.ms_y(n.start) - draw_h // 2

        qp.drawPixmap(QRect(x, y, draw_w, draw_h), img)


# ── 預覽視窗 ──────────────────────────────────────────────────────────────────
class PreviewWindow(QDialog):
    """可捲動的靜態譜面預覽視窗。"""

    def __init__(self, notes: List[GNote], parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle('譜面預覽')
        self.resize(920, 720)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        # ── 畫布 ──────────────────────────────────────────────────────
        self._canvas = PreviewCanvas(notes)

        # ── 捲動區域 ───────────────────────────────────────────────────
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(False)
        self._scroll.setWidget(self._canvas)
        self._scroll.setAlignment(Qt.AlignHCenter)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        # ── 工具列（縮放）──────────────────────────────────────────────
        self._lbl_zoom = QLabel(self._zoom_text())
        btn_zi = QPushButton('放大 ＋')
        btn_zo = QPushButton('縮小 −')
        btn_zi.setFixedWidth(70)
        btn_zo.setFixedWidth(70)
        btn_zi.clicked.connect(self._zoom_in)
        btn_zo.clicked.connect(self._zoom_out)

        ctrl_lay = QHBoxLayout()
        ctrl_lay.addWidget(QLabel('縮放：'))
        ctrl_lay.addWidget(btn_zo)
        ctrl_lay.addWidget(self._lbl_zoom)
        ctrl_lay.addWidget(btn_zi)
        ctrl_lay.addStretch()
        hint = QLabel('（Ctrl + 滾輪縮放）')
        hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        ctrl_lay.addWidget(hint)

        # ── 主版面 ──────────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(ctrl_lay)
        lay.addWidget(self._scroll, 1)

    # ------------------------------------------------------------------
    # 縮放
    # ------------------------------------------------------------------

    def _zoom_text(self) -> str:
        return f'{self._canvas.px_per_ms * 1000:.0f} px/s'

    def _zoom_in(self) -> None:
        self._canvas.set_px_per_ms(self._canvas.px_per_ms * 1.4)
        self._lbl_zoom.setText(self._zoom_text())

    def _zoom_out(self) -> None:
        self._canvas.set_px_per_ms(self._canvas.px_per_ms / 1.4)
        self._lbl_zoom.setText(self._zoom_text())

    # ------------------------------------------------------------------
    # 滾輪縮放（Ctrl+滾輪）
    # ------------------------------------------------------------------

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self._zoom_in()
            else:
                self._zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 更新音符資料（外部呼叫）
    # ------------------------------------------------------------------

    def refresh(self, notes: List[GNote]) -> None:
        """用最新的 notes_tree 重新繪製。"""
        self._canvas.set_notes(notes)
