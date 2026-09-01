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

from PyQt5.QtCore import Qt, QRect, QRectF, QSize, QPointF
from PyQt5.QtGui import QColor, QPainter, QPixmap, QPen, QFont, QBrush, QPolygonF, QLinearGradient
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .models import (
    GNote, TOTAL_GAME_KEYS,
    build_slide_index_map, slide_next_note,
    note_is_slide, note_is_long, note_is_trill, note_has_duration,
    trill_sub_cells, trill_fallback_cells,
)

# slide 梯形在預覽的「固定視覺厚度」（px，不隨 MIDI 時長）
SLIDE_PREVIEW_THK = 14.0

# slide（滑）梯形帶顏色：依 hand 區分左右手
SLIDE_FILL_RIGHT = QColor(255, 110, 110, 150)
SLIDE_FILL_LEFT  = QColor(110, 200, 255, 150)
SLIDE_EDGE_RIGHT = QColor(255, 150, 150)
SLIDE_EDGE_LEFT  = QColor(150, 220, 255)

# trill（顫音）：中間淡色無邊方形 mesh + 開頭實心 tap / 尾端空心 tap
TRILL_PALE_RIGHT = QColor(255, 140, 140, 110)
TRILL_PALE_LEFT  = QColor(150, 185, 255, 110)
TRILL_TAP_RIGHT  = QColor(230,  70,  70)
TRILL_TAP_LEFT   = QColor( 70, 120, 230)

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
            n.end if note_has_duration(n.note_type) else n.start
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

    def _note_x_range(self, n: GNote) -> tuple[float, float]:
        """梯形帶左右緣 px：與 note 一致取 90% 寬並置中，不占滿格寬。"""
        cell_w = CANVAS_W / TOTAL_GAME_KEYS
        x1 = n.min_key * cell_w
        x2 = (n.max_key + 1) * cell_w
        margin = (x2 - x1) * 0.05
        return x1 + margin, x2 - margin

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

        # 第一遍：hold 主體 / trill 顫音條（較低圖層）
        for n in self.notes:
            if note_is_trill(n.note_type):
                self._draw_trill_body(qp, n)
            elif note_is_long(n.note_type):
                self._draw_hold_body(qp, n)

        # 第一.5遍：slide 梯形帶（連接鏈上最近的兩顆）
        self._draw_slide_bands(qp)

        # 第二遍：所有 note head（較高圖層）
        for n in self.notes:
            self._draw_note_head(qp, n)

    # ------------------------------------------------------------------
    # Slide 梯形帶
    # ------------------------------------------------------------------

    def _draw_slide_bands(self, qp: QPainter) -> None:
        """每顆 slide 畫成一個梯形：跨自身 start→end 厚度，
        由自己的鍵道滑向下一顆（param2）的鍵道；只連明確鏈結。"""
        index_map = build_slide_index_map(self.notes)
        if not index_map:
            return
        for n in self.notes:
            if not note_is_slide(n.note_type):
                continue
            nxt = slide_next_note(n, self.notes, index_map)
            if nxt is None:
                continue

            # 預覽固定視覺厚度：尾巴 = 頭往後 SLIDE_PREVIEW_THK（不隨 MIDI 時長）
            xa1, xa2 = self._note_x_range(n)
            xb1, xb2 = self._note_x_range(nxt)
            y_near = self.ms_y(n.start) + SLIDE_PREVIEW_THK
            y_far  = self.ms_y(nxt.start)    # 下一顆頭
            poly = QPolygonF([
                QPointF(xa1, y_near), QPointF(xa2, y_near),
                QPointF(xb2, y_far), QPointF(xb1, y_far),
            ])
            if n.hand == 1:
                fill, edge = SLIDE_FILL_LEFT, SLIDE_EDGE_LEFT
            else:
                fill, edge = SLIDE_FILL_RIGHT, SLIDE_EDGE_RIGHT
            qp.setBrush(QBrush(fill))
            qp.setPen(QPen(edge, 1.5))
            qp.drawPolygon(poly)

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
    # Trill 顫音條
    # ------------------------------------------------------------------

    def _draw_trill_body(self, qp: QPainter, n: GNote) -> None:
        """顫音：中央直向方形 mesh（類似 hold）+ 左右六邊形節點 + 頭尾 tap 圖。"""
        cell_w = CANVAS_W / TOTAL_GAME_KEYS
        x1z = float(n.min_key * cell_w)
        x2z = float((n.max_key + 1) * cell_w)
        zone_w = x2z - x1z
        if zone_w <= 0:
            return
        cells = trill_sub_cells(n) or trill_fallback_cells(int(n.start), int(n.end))
        cells = sorted(cells, key=lambda c: c[2])
        if not cells:
            return

        cx = (x1z + x2z) / 2.0
        tapc = TRILL_TAP_LEFT if n.hand == 1 else TRILL_TAP_RIGHT
        y_start = float(self.ms_y(n.start))
        y_end   = float(self.ms_y(n.end))
        ytop, ybot = (min(y_start, y_end), max(y_start, y_end))

        # 預覽固定「向外滿」：依序左右交替、整個區寬滿版，不看內部音符排布
        hx1, hx2 = x1z, x2z
        inset = min(zone_w * 0.15, 16.0)
        qp.setPen(Qt.NoPen)
        for i, (relx, relw, st, en, pit, _idx) in enumerate(cells):
            outer_x = x1z if (i % 2 == 0) else x2z
            yt = float(self.ms_y(st))
            yn = float(self.ms_y(cells[i + 1][2] if i + 1 < len(cells) else en))
            a, b = (min(yt, yn), max(yt, yn))
            pad = max(2.0, (b - a) * 0.15)
            a -= pad
            b += pad
            ymid = (a + b) / 2.0
            grad = QLinearGradient(outer_x, 0.0, cx, 0.0)
            c0 = QColor(tapc); c0.setAlpha(235)
            c1 = QColor(tapc); c1.setAlpha(110)
            c2 = QColor(tapc); c2.setAlpha(14)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(0.40, c0)
            grad.setColorAt(0.78, c1)
            grad.setColorAt(1.0, c2)
            qp.setBrush(QBrush(grad))
            qp.drawPolygon(QPolygonF([
                QPointF(hx1, ymid), QPointF(hx1 + inset, a), QPointF(hx2 - inset, a),
                QPointF(hx2, ymid), QPointF(hx2 - inset, b), QPointF(hx1 + inset, b),
            ]))

        # 頭尾 tap 素材（開頭實心、尾端空心=淡化）
        img = _pix('left_note.png' if n.hand == 1 else 'right_note.png')
        if not img.isNull() and img.width() > 0:
            th = 16.0
            qp.setOpacity(1.0)
            qp.drawPixmap(QRect(int(x1z), int(y_start - th / 2.0), int(zone_w), int(th)), img)
            qp.setOpacity(0.35)
            qp.drawPixmap(QRect(int(x1z), int(y_end - th / 2.0), int(zone_w), int(th)), img)
            qp.setOpacity(1.0)

    # ------------------------------------------------------------------
    # Note Head（tap / soft / stac，以及 hold 的 tap head）
    # ------------------------------------------------------------------

    def _draw_note_head(self, qp: QPainter, n: GNote) -> None:
        """依音符類型選圖，以 0.9 寬、原始比例繪製於 starttime 位置。"""
        nt   = n.note_type
        hand = n.hand

        if note_is_trill(nt):
            return  # trill 的頭尾 tap 由 _draw_trill_body 負責

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
