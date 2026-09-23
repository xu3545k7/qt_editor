"""遊戲預覽：一個小視窗，照遊戲裡的樣子把音符斜斜地降到判定線。

和 `preview_window` 的靜態俯視預覽不同，這裡要回答的是「這段進遊戲會長怎樣」：
音符從遠處往下掉、鍵道隨距離收窄（斜降的透視），碰到鍵盤上緣就是判定的瞬間。
特效刻意簡化——只有判定線上的一下閃光和被按住的鍵會亮，沒有粒子、沒有評價
文字、沒有分數。要看的是**排列與時機**，不是畫面。

材質
----
音符本體和譜面的預覽模式**共用同一套素材**：原版的 note 幀（`note_icons` 的
`note_frame`，依類型／左右手／寬度挑圖）、`art_fill_rect` 把柔邊撐掉讓實心尖端
貼齊鍵道，長押／滑鍵／顫音也用編輯器那組顏色。這裡本來是自己畫有顏色的梯形，
但那等於同一顆音符在兩個視窗長得不一樣，看預覽時得在腦袋裡換算一次。

差別只有透視：寬度和厚度都乘上 `_scale(pos)`，所以遠處是整顆等比例縮小，而不是
被壓扁；長押、滑鍵帶、顫音條這些跨時間的形狀畫成梯形（兩端在不同深度上）。

時間從製譜器推進來（`set_time`）：播放中由 `_on_judge_tick` 每 16ms 餵一次，
暫停時餵 `judge_line_view_ms()`，所以捲動譜面也會跟著更新。

座標
----
`pos` 是「還有多久到判定線」normalise 成 0~1（0 = 正在判定，1 = 視野最遠）。
`_depth(pos)` 是透視本體：`(1+k)·pos / (1+k·pos)`，也就是 1/z 的形狀——0 對 0、
1 對 1，而且斜率越遠越小，所以等長的時間在遠處占的畫面越短（音符往上擠）。
y 和鍵道寬度都吃同一條曲線，遠處整片縮窄，就是斜降的視覺來源。

（別用 `pos ** k` 這種次方近似：k>1 會把音符擠在判定線附近、遠處反而散開，
剛好和透視相反。）
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                         QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QSpinBox,
                             QVBoxLayout, QWidget)

from .models import (GNote, TOTAL_GAME_KEYS, build_slide_index_map,
                     note_is_long, note_is_slide, note_is_trill,
                     slide_next_note, trill_fallback_cells, trill_sub_cells,
                     SOFT_NOTE_TYPE, STACCATO_NOTE_TYPE)
from .note_icons import art_fill_rect, graphic_pixmap, note_frame

#: 視野：判定線往前看幾毫秒（遊戲的下落速度，可在視窗上調）
DEFAULT_LEAD_MS = 1400
MIN_LEAD_MS, MAX_LEAD_MS = 300, 4000

#: 判定線在畫面上的高度比例，以及鍵盤的高度比例
JUDGE_Y_RATIO = 0.80
KEYBOARD_RATIO = 0.13
#: 最遠處的鍵道寬度剩多少（越小＝透視越強）
FAR_WIDTH_RATIO = 0.34
#: 透視強度（1/z 的 k）：越大＝遠處擠得越兇
PERSPECTIVE_K = 2.2
#: 判定閃光持續多久
FLASH_MS = 110
#: 判定線上的音符頭有多厚（佔畫面高度），遠處再乘 `_scale`。譜面預覽那邊是固定
#: 40px，這裡跟著視窗大小走——預覽視窗可以被拉到很小。
HEAD_H_RATIO = 0.085
#: 滑鍵帶比音符窄多少（和編輯器的 `_slide_band_x_range` 同一個比例）
SLIDE_BAND_FRAC = 0.56
#: 顫音頭尾那張 trill 幀比一般音符頭薄多少（譜面預覽是 16px 對 40px）
TRILL_TAP_H_FRAC = 16.0 / 40.0

BG_TOP = QColor(8, 10, 22)
BG_BOTTOM = QColor(22, 26, 48)
LANE_LINE = QColor(255, 255, 255, 28)
LANE_LINE_OCTAVE = QColor(255, 255, 255, 60)
JUDGE_LINE = QColor(120, 225, 255)
KEY_FACE = QColor(30, 34, 52)
KEY_EDGE = QColor(255, 255, 255, 40)
KEY_LIT = QColor(120, 225, 255, 120)

#: 左右手的顏色（hand: 0 = 右手, 1 = 左手；和編輯器一致）。素材讀不到時的退路。
RIGHT_FILL, RIGHT_EDGE = QColor(255, 96, 96), QColor(255, 186, 186)
LEFT_FILL, LEFT_EDGE = QColor(96, 160, 255), QColor(186, 214, 255)
SOFT_FILL, SOFT_EDGE = QColor(170, 170, 185), QColor(225, 225, 235)

#: 長押身體、滑鍵帶、顫音條：和譜面預覽模式同一組顏色（key = hand）
HOLD_FILL = {0: QColor(255, 170, 170, 150), 1: QColor(150, 200, 255, 150)}
HOLD_EDGE = {0: QColor(255, 140, 140, 220), 1: QColor(120, 200, 255, 220)}
SLIDE_BAND = {0: QColor(255, 110, 110, 130), 1: QColor(110, 200, 255, 130)}
SLIDE_EDGE = {0: QColor(255, 150, 150), 1: QColor(150, 220, 255)}
TRILL_TAP = {0: QColor(230, 70, 70), 1: QColor(70, 120, 230)}


def _playable(notes: Sequence[GNote]) -> List[GNote]:
    """隱藏音符只發聲、玩家看不到，預覽也不該畫（和 `preview_window` 同一個理由）。"""
    return [n for n in notes if not getattr(n, 'hidden', False)]


def _colors(note: GNote) -> Tuple[QColor, QColor]:
    nt = int(note.note_type)
    if nt == SOFT_NOTE_TYPE:
        return SOFT_FILL, SOFT_EDGE
    if int(note.hand) == 1:
        return LEFT_FILL, LEFT_EDGE
    return RIGHT_FILL, RIGHT_EDGE


def _hand(note: GNote) -> int:
    return 1 if int(note.hand) == 1 else 0


def _art(note: GNote) -> QPixmap:
    """這顆音符該貼哪一張原版幀——和譜面預覽模式同一套判斷。

    顫音的頭尾 tap 不走這裡（用 trill 幀，由 `_draw_trill` 一起畫）。
    """
    nt = int(note.note_type)
    if nt == SOFT_NOTE_TYPE:
        kind = 'white_piano'
    elif note_is_slide(nt):
        kind = 'glissando'
    else:                       # tap / staccato / 長押頭都是 white 幀
        kind = 'white'
    width = abs(int(note.max_key) - int(note.min_key)) + 1
    return note_frame(kind, _hand(note), width)


def _pref_fraction(key: str, default: float, low: float) -> float:
    """讀偏好設定的百分比（和編輯器共用同一個 key，兩邊寬窄才會一致）。"""
    try:
        from .settings import settings
        pct = float(settings.get(key, default) or default)
    except Exception:           # noqa: BLE001 — 設定壞了不能讓預覽畫不出來
        pct = float(default)
    return max(low, min(1.0, pct / 100.0))


class GamePreviewCanvas(QWidget):
    """斜降的遊戲畫面。時間由外面餵進來，自己不跑計時器。"""

    def __init__(self, notes: Sequence[GNote] = (), parent=None) -> None:
        super().__init__(parent)
        self.notes = _playable(notes)
        self._slide_map = build_slide_index_map(self.notes)
        self.lead_ms = float(DEFAULT_LEAD_MS)
        self.now_ms = 0.0
        self.show_flash = True
        # 音符頭／長押身體的寬度比例，每幀從偏好設定讀一次（見 `paintEvent`）
        self._head_frac = 1.0
        self._hold_frac = 0.55
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(False)

    # ── 對外 ──────────────────────────────────────────────────────────
    def set_notes(self, notes: Sequence[GNote]) -> None:
        self.notes = _playable(notes)
        # 滑鍵的鏈結表跟著換譜重建就好，不必每幀掃一次全譜
        self._slide_map = build_slide_index_map(self.notes)
        self.update()

    def set_time(self, ms: Optional[float]) -> None:
        if ms is None:
            return
        self.now_ms = float(ms)
        self.update()

    def set_lead_ms(self, ms: int) -> None:
        self.lead_ms = float(max(MIN_LEAD_MS, min(MAX_LEAD_MS, int(ms))))
        self.update()

    # ── 幾何 ──────────────────────────────────────────────────────────
    def _judge_y(self) -> float:
        return self.height() * JUDGE_Y_RATIO

    @staticmethod
    def _depth(pos: float) -> float:
        """1/z 的透視曲線：0→0、1→1，斜率越遠越小（遠處在畫面上被壓扁）。"""
        k = PERSPECTIVE_K
        return (1.0 + k) * pos / (1.0 + k * pos)

    def _y(self, pos: float) -> float:
        """pos（0 = 判定線, 1 = 最遠）→ 畫面 y。"""
        return self._judge_y() * (1.0 - self._depth(pos))

    def _half_width(self, pos: float) -> float:
        near = self.width() * 0.5
        return near * (1.0 - (1.0 - FAR_WIDTH_RATIO) * self._depth(pos))

    def _scale(self, pos: float) -> float:
        """深度 pos 的縮放（判定線 = 1）。寬度和音符厚度吃同一個值，遠處才是
        整顆等比例縮小而不是被壓扁。"""
        return 1.0 - (1.0 - FAR_WIDTH_RATIO) * self._depth(pos)

    def _lane_x(self, lane: float, pos: float) -> float:
        """第 lane 條鍵道邊界（0 ~ TOTAL_GAME_KEYS）在深度 pos 的 x。"""
        centre = self.width() * 0.5
        frac = lane / float(TOTAL_GAME_KEYS) - 0.5
        return centre + frac * 2.0 * self._half_width(pos)

    def _span_x(self, lo: int, hi: int, pos: float,
                frac: float = 1.0) -> Tuple[float, float]:
        """鍵道 [lo, hi] 在深度 pos 的左右緣，依 frac 以中線收窄。"""
        x1 = self._lane_x(lo, pos)
        x2 = self._lane_x(hi + 1, pos)
        if frac >= 0.999:
            return x1, x2
        mid = (x1 + x2) * 0.5
        half = (x2 - x1) * 0.5 * frac
        return mid - half, mid + half

    def _pos_of(self, ms: float) -> float:
        return (float(ms) - self.now_ms) / self.lead_ms

    def _quad(self, lo: int, hi: int, near_pos: float, far_pos: float,
              frac: float = 1.0) -> QPolygonF:
        """鍵道 [lo, hi] 從 near_pos 到 far_pos 的四邊形（長押的身體）。"""
        nx1, nx2 = self._span_x(lo, hi, near_pos, frac)
        fx1, fx2 = self._span_x(lo, hi, far_pos, frac)
        return QPolygonF([
            QPointF(nx1, self._y(near_pos)),
            QPointF(nx2, self._y(near_pos)),
            QPointF(fx2, self._y(far_pos)),
            QPointF(fx1, self._y(far_pos)),
        ])

    def _head_rect(self, note: GNote, pos: float) -> QRectF:
        """音符頭的矩形：寬度是鍵道寬（收窄比例照偏好設定），厚度照深度縮，
        中線落在 startTime 上——和譜面預覽的 `_preview_head_rect` 同一個規則。"""
        x1, x2 = self._span_x(int(note.min_key), int(note.max_key),
                              pos, self._head_frac)
        height = self.height() * HEAD_H_RATIO * self._scale(pos)
        return QRectF(x1, self._y(pos) - height * 0.5, x2 - x1, height)

    # ── 繪製 ──────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:            # noqa: N802
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.SmoothPixmapTransform)
        self._head_frac = _pref_fraction('note_width_pct', 100, 0.4)
        self._hold_frac = _pref_fraction('hold_width_pct', 55, 0.2)
        self._draw_background(qp)
        self._draw_lanes(qp)
        # 遠的先畫，近的壓在上面（近大遠小，疊起來才對）
        visible = sorted(self._visible_notes(), key=lambda item: -item[1])
        # 圖層順序和譜面預覽一致：滑鍵帶 → 長押／顫音身體 → 音符頭 → 斷奏箭頭
        self._draw_slide_bands(qp)
        for note, pos in visible:
            if note_is_trill(note.note_type):
                self._draw_trill(qp, note)
            elif note_is_long(note.note_type):
                self._draw_hold(qp, note)
        for note, pos in visible:
            self._draw_head(qp, note, pos)
        for note, pos in visible:
            if int(note.note_type) == STACCATO_NOTE_TYPE:
                self._draw_stac_arrow(qp, note, pos)
        self._draw_keyboard(qp)
        self._draw_judge_line(qp)

    def _visible_notes(self) -> List[Tuple[GNote, float]]:
        out = []
        for n in self.notes:
            pos = self._pos_of(n.start)
            if pos > 1.0:
                continue
            tail = self._pos_of(n.end) if note_is_long(n.note_type) else pos
            # 頭已經過判定線，但尾巴還在畫面上的長押要留著
            if max(pos, tail) < -FLASH_MS / self.lead_ms:
                continue
            out.append((n, pos))
        return out

    def _draw_background(self, qp: QPainter) -> None:
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, BG_TOP)
        grad.setColorAt(1.0, BG_BOTTOM)
        qp.fillRect(self.rect(), QBrush(grad))

    def _draw_lanes(self, qp: QPainter) -> None:
        for lane in range(TOTAL_GAME_KEYS + 1):
            qp.setPen(QPen(LANE_LINE_OCTAVE if lane % 7 == 0 else LANE_LINE, 1))
            qp.drawLine(QPointF(self._lane_x(lane, 0.0), self._judge_y()),
                        QPointF(self._lane_x(lane, 1.0), self._y(1.0)))

    def _draw_hold(self, qp: QPainter, note: GNote) -> None:
        """長押身體：半透明的淺紅／淺藍條，比音符頭窄（比例照偏好設定）。"""
        near = max(0.0, self._pos_of(note.start))
        far = min(1.0, self._pos_of(note.end))
        if far <= 0.0:                              # 整條都過了
            return
        hand = _hand(note)
        qp.setBrush(QBrush(HOLD_FILL[hand]))
        qp.setPen(QPen(HOLD_EDGE[hand], 1.5))
        qp.drawPolygon(self._quad(int(note.min_key), int(note.max_key),
                                  near, max(near, far),
                                  self._head_frac * self._hold_frac))

    def _draw_slide_bands(self, qp: QPainter) -> None:
        """滑鍵帶：從自己的鍵道連到鏈上的下一顆（`param2`；只連明確鏈結）。

        近緣取自己的 startTime、遠緣取下一顆的 startTime，和譜面預覽模式一樣
        ——滑鍵在遊戲裡是一路滑過去，帶子要填滿兩顆之間而不是自己的時長。
        """
        if not self._slide_map:
            return
        frac = self._head_frac * SLIDE_BAND_FRAC
        for n in self.notes:
            if not note_is_slide(int(n.note_type)):
                continue
            nxt = slide_next_note(n, self.notes, self._slide_map)
            if nxt is None:
                continue
            near, far = self._pos_of(n.start), self._pos_of(nxt.start)
            if far <= near or far <= 0.0 or near >= 1.0:
                continue                            # 整條在畫面外
            near, far = max(0.0, near), min(1.0, far)
            hand = _hand(n)
            ax1, ax2 = self._span_x(int(n.min_key), int(n.max_key), near, frac)
            bx1, bx2 = self._span_x(int(nxt.min_key), int(nxt.max_key), far, frac)
            qp.setBrush(QBrush(SLIDE_BAND[hand]))
            qp.setPen(QPen(SLIDE_EDGE[hand], 0.8))
            qp.drawPolygon(QPolygonF([
                QPointF(ax1, self._y(near)), QPointF(ax2, self._y(near)),
                QPointF(bx2, self._y(far)), QPointF(bx1, self._y(far)),
            ]))

    def _draw_trill(self, qp: QPainter, note: GNote) -> None:
        """顫音：左右交替的漸層條 + 頭尾各一張 trill 幀（尾端淡化）。

        和譜面預覽一樣「向外滿」——每一格都撐滿整個區寬，只有漸層的方向在左右
        交替，看的是「在這兩個音之間來回」而不是每一下的確切鍵道。
        """
        cells = (trill_sub_cells(note)
                 or trill_fallback_cells(int(note.start), int(note.end)))
        if not cells:
            return
        cells = sorted(cells, key=lambda c: c[2])
        lo, hi = int(note.min_key), int(note.max_key)
        # 區寬和音符頭一樣吃「音符寬度」設定（譜面預覽的 `_note_display_x_range`）
        frac = self._head_frac
        tap = TRILL_TAP[_hand(note)]
        qp.setPen(Qt.NoPen)
        for i, cell in enumerate(cells):
            # 每一格畫到「下一格的開頭」，中間不留縫（最後一格用自己的 end）
            nxt_ms = cells[i + 1][2] if i + 1 < len(cells) else cell[3]
            near, far = self._pos_of(cell[2]), self._pos_of(nxt_ms)
            pad = (far - near) * 0.15                # 上下各撐一點，格子才連得起來
            near, far = near - pad, far + pad
            if far <= 0.0 or near >= 1.0:
                continue
            near, far = max(0.0, near), min(1.0, far)
            mid = (near + far) * 0.5
            nx1, nx2 = self._span_x(lo, hi, near, frac)
            fx1, fx2 = self._span_x(lo, hi, far, frac)
            mx1, mx2 = self._span_x(lo, hi, mid, frac)
            if mx2 - mx1 <= 0.5:                     # 遠到只剩一條線，漸層無意義
                continue
            # 漸層：外尖端實心 → 到中線急遽淡出，過中線僅剩極淡的尾巴
            outer = mx1 if i % 2 == 0 else mx2
            grad = QLinearGradient(outer, 0.0, (mx1 + mx2) * 0.5, 0.0)
            solid = QColor(tap); solid.setAlpha(235)
            faint = QColor(tap); faint.setAlpha(110)
            tail = QColor(tap); tail.setAlpha(14)
            grad.setColorAt(0.0, solid)
            grad.setColorAt(0.40, solid)
            grad.setColorAt(0.78, faint)
            grad.setColorAt(1.0, tail)
            qp.setBrush(QBrush(grad))
            inset_n = min((nx2 - nx1) * 0.15, 16.0 * self._scale(near))
            inset_f = min((fx2 - fx1) * 0.15, 16.0 * self._scale(far))
            y_near, y_mid, y_far = self._y(near), self._y(mid), self._y(far)
            qp.drawPolygon(QPolygonF([
                QPointF(mx1, y_mid), QPointF(fx1 + inset_f, y_far),
                QPointF(fx2 - inset_f, y_far), QPointF(mx2, y_mid),
                QPointF(nx2 - inset_n, y_near), QPointF(nx1 + inset_n, y_near),
            ]))

        # 頭尾 tap：開頭實心、尾端淡化，厚度比一般音符頭薄（和譜面預覽同一個處理）
        img = note_frame('trill', _hand(note), abs(hi - lo) + 1)
        if img.isNull() or img.width() <= 0:
            return
        for ms, opacity in ((note.end, 0.35), (note.start, 1.0)):
            pos = self._pos_of(ms)
            if pos < 0.0 or pos > 1.0:
                continue
            rect = self._head_rect(note, pos)
            thin = rect.height() * TRILL_TAP_H_FRAC
            rect = QRectF(rect.left(), rect.center().y() - thin * 0.5,
                          rect.width(), thin)
            qp.setOpacity(opacity)
            qp.drawPixmap(art_fill_rect(rect, img).toRect(), img)
        qp.setOpacity(1.0)

    def _draw_head(self, qp: QPainter, note: GNote, pos: float) -> None:
        """音符頭：貼原版 note 幀，和譜面的預覽模式同一張圖。"""
        if pos < 0.0:
            # 剛剛打到：畫一下閃光就結束（簡化的判定特效）
            if self.show_flash and -pos * self.lead_ms <= FLASH_MS:
                self._draw_flash(qp, note, -pos * self.lead_ms / FLASH_MS)
            return
        if note_is_trill(int(note.note_type)):
            return                                  # 頭尾 tap 由 `_draw_trill` 畫
        rect = self._head_rect(note, pos)
        img = _art(note)
        if img.isNull() or img.width() <= 0:
            self._draw_flat_head(qp, note, rect)
            return
        # 撐掉圖裡尖端外面那圈透明柔邊，實心尖端才會落在鍵道邊界上
        qp.drawPixmap(art_fill_rect(rect, img).toRect(), img)

    def _draw_flat_head(self, qp: QPainter, note: GNote, rect: QRectF) -> None:
        """素材讀不到時的退路：照手別上色的方塊。打包漏圖也還看得到譜。"""
        fill, edge = _colors(note)
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1))
        qp.drawRect(rect)

    def _draw_stac_arrow(self, qp: QPainter, note: GNote, pos: float) -> None:
        """斷奏：音符正上方的指示箭頭，用遊戲本體那張素材。"""
        if pos < 0.0:
            return
        img = graphic_pixmap('LeftStacArrow-v2.png' if int(note.hand) == 1
                             else 'RightStacArrow-v2.png')
        if img.isNull():
            return
        rect = self._head_rect(note, pos)
        side = rect.width()
        if side <= 0.0:
            return
        # 素材是正方形，箭頭尖端朝上、拖尾壓在音符頭上
        qp.drawPixmap(QRectF(rect.left(), rect.top() - side * 0.95,
                             side, side).toRect(), img)

    def _draw_flash(self, qp: QPainter, note: GNote, fade: float) -> None:
        colour = QColor(JUDGE_LINE)
        colour.setAlpha(int(200 * max(0.0, 1.0 - fade)))
        qp.setPen(QPen(colour, 3))
        qp.setBrush(Qt.NoBrush)
        y = self._judge_y()
        x1 = self._lane_x(int(note.min_key), 0.0)
        x2 = self._lane_x(int(note.max_key) + 1, 0.0)
        grow = 6.0 * fade
        qp.drawRoundedRect(QRectF(x1 - grow, y - 7.0 - grow,
                                  (x2 - x1) + 2 * grow, 14.0 + 2 * grow), 5, 5)

    def _held_lanes(self) -> set:
        """現在被按住的鍵道（長押正在進行中）——鍵盤要亮起來。"""
        lit = set()
        for n in self.notes:
            if not note_is_long(n.note_type):
                continue
            if int(n.start) <= self.now_ms <= int(n.end):
                lit.update(range(int(n.min_key), int(n.max_key) + 1))
        return lit

    def _draw_keyboard(self, qp: QPainter) -> None:
        top = self._judge_y()
        height = self.height() * KEYBOARD_RATIO
        lit = self._held_lanes()
        for lane in range(TOTAL_GAME_KEYS):
            x1 = self._lane_x(lane, 0.0)
            x2 = self._lane_x(lane + 1, 0.0)
            qp.setBrush(QBrush(KEY_LIT if lane in lit else KEY_FACE))
            qp.setPen(QPen(KEY_EDGE, 1))
            qp.drawRect(int(x1), int(top), max(1, int(x2 - x1)), int(height))

    def _draw_judge_line(self, qp: QPainter) -> None:
        qp.setPen(QPen(JUDGE_LINE, 2))
        y = self._judge_y()
        qp.drawLine(QPointF(self._lane_x(0, 0.0), y),
                    QPointF(self._lane_x(TOTAL_GAME_KEYS, 0.0), y))


class GamePreviewWindow(QDialog):
    """裝著 `GamePreviewCanvas` 的小視窗，可以一邊編輯一邊開著。"""

    def __init__(self, notes: Sequence[GNote] = (), parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle('遊戲預覽（斜降）')
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = GamePreviewCanvas(notes, self)
        layout.addWidget(self.canvas, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel('視野'))
        self._lead = QSpinBox()
        self._lead.setRange(MIN_LEAD_MS, MAX_LEAD_MS)
        self._lead.setSingleStep(100)
        self._lead.setSuffix(' ms')
        self._lead.setValue(int(self.canvas.lead_ms))
        self._lead.setToolTip('判定線往前看多久＝下落速度。數字小＝音符掉得快。')
        self._lead.valueChanged.connect(self.canvas.set_lead_ms)
        bar.addWidget(self._lead)
        self._flash = QCheckBox('判定閃光')
        self._flash.setChecked(True)
        self._flash.toggled.connect(self._on_flash)
        bar.addWidget(self._flash)
        bar.addStretch(1)
        bar.addWidget(QLabel('播放製譜器就會跟著動'))
        layout.addLayout(bar)

    def _on_flash(self, on: bool) -> None:
        self.canvas.show_flash = bool(on)
        self.canvas.update()

    # ── 對外（主視窗餵資料）──────────────────────────────────────────
    def set_notes(self, notes: Sequence[GNote]) -> None:
        self.canvas.set_notes(notes)

    def set_time(self, ms: Optional[float]) -> None:
        self.canvas.set_time(ms)
