"""遊戲預覽：一個小視窗，照遊戲裡的樣子把音符斜斜地降到判定線。

和 `preview_window` 的靜態俯視預覽不同，這裡要回答的是「這段進遊戲會長怎樣」：
音符從遠處往下掉、鍵道隨距離收窄（斜降的透視），碰到鍵盤上緣就是判定的瞬間。
特效刻意簡化——只有判定線上的一下閃光和被按住的鍵會亮，沒有粒子、沒有評價
文字、沒有分數。要看的是**排列與時機**，不是畫面。

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
                         QPolygonF)
from PyQt5.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QSpinBox,
                             QVBoxLayout, QWidget)

from .models import (GNote, TOTAL_GAME_KEYS, note_is_long, note_is_slide,
                     note_is_trill, SOFT_NOTE_TYPE, STACCATO_NOTE_TYPE)

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

BG_TOP = QColor(8, 10, 22)
BG_BOTTOM = QColor(22, 26, 48)
LANE_LINE = QColor(255, 255, 255, 28)
LANE_LINE_OCTAVE = QColor(255, 255, 255, 60)
JUDGE_LINE = QColor(120, 225, 255)
KEY_FACE = QColor(30, 34, 52)
KEY_EDGE = QColor(255, 255, 255, 40)
KEY_LIT = QColor(120, 225, 255, 120)

#: 左右手的顏色（hand: 0 = 右手, 1 = 左手；和編輯器一致）
RIGHT_FILL, RIGHT_EDGE = QColor(255, 96, 96), QColor(255, 186, 186)
LEFT_FILL, LEFT_EDGE = QColor(96, 160, 255), QColor(186, 214, 255)
SOFT_FILL, SOFT_EDGE = QColor(170, 170, 185), QColor(225, 225, 235)
HOLD_ALPHA = 130


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


class GamePreviewCanvas(QWidget):
    """斜降的遊戲畫面。時間由外面餵進來，自己不跑計時器。"""

    def __init__(self, notes: Sequence[GNote] = (), parent=None) -> None:
        super().__init__(parent)
        self.notes = _playable(notes)
        self.lead_ms = float(DEFAULT_LEAD_MS)
        self.now_ms = 0.0
        self.show_flash = True
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(False)

    # ── 對外 ──────────────────────────────────────────────────────────
    def set_notes(self, notes: Sequence[GNote]) -> None:
        self.notes = _playable(notes)
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

    def _lane_x(self, lane: float, pos: float) -> float:
        """第 lane 條鍵道邊界（0 ~ TOTAL_GAME_KEYS）在深度 pos 的 x。"""
        centre = self.width() * 0.5
        frac = lane / float(TOTAL_GAME_KEYS) - 0.5
        return centre + frac * 2.0 * self._half_width(pos)

    def _pos_of(self, ms: float) -> float:
        return (float(ms) - self.now_ms) / self.lead_ms

    def _quad(self, lo: int, hi: int, near_pos: float, far_pos: float) -> QPolygonF:
        """鍵道 [lo, hi] 從 near_pos 到 far_pos 的四邊形（長押的身體）。"""
        return QPolygonF([
            QPointF(self._lane_x(lo, near_pos), self._y(near_pos)),
            QPointF(self._lane_x(hi + 1, near_pos), self._y(near_pos)),
            QPointF(self._lane_x(hi + 1, far_pos), self._y(far_pos)),
            QPointF(self._lane_x(lo, far_pos), self._y(far_pos)),
        ])

    # ── 繪製 ──────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:            # noqa: N802
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        self._draw_background(qp)
        self._draw_lanes(qp)
        # 遠的先畫，近的壓在上面（近大遠小，疊起來才對）
        visible = sorted(self._visible_notes(), key=lambda item: -item[1])
        for note, pos in visible:
            if note_is_long(note.note_type) or note_is_trill(note.note_type):
                self._draw_hold(qp, note)
            if note_is_slide(note.note_type):
                self._draw_slide(qp, note, pos)
        for note, pos in visible:
            self._draw_head(qp, note, pos)
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
        near = max(0.0, self._pos_of(note.start))
        far = min(1.0, self._pos_of(note.end))
        if far <= 0.0:                              # 整條都過了
            return
        fill, edge = _colors(note)
        body = QColor(fill)
        body.setAlpha(HOLD_ALPHA)
        qp.setBrush(QBrush(body))
        qp.setPen(QPen(edge, 1))
        qp.drawPolygon(self._quad(int(note.min_key), int(note.max_key), near, max(near, far)))

    def _draw_slide(self, qp: QPainter, note: GNote, pos: float) -> None:
        """滑鍵：在判定面上畫一條指向下一個位置的淡帶（簡化版，不追串接）。"""
        fill, _edge = _colors(note)
        band = QColor(fill)
        band.setAlpha(70)
        qp.setBrush(QBrush(band))
        qp.setPen(Qt.NoPen)
        near = max(0.0, pos)
        qp.drawPolygon(self._quad(int(note.min_key), int(note.max_key),
                                  near, min(1.0, near + 0.06)))

    def _draw_head(self, qp: QPainter, note: GNote, pos: float) -> None:
        fill, edge = _colors(note)
        nt = int(note.note_type)
        if pos < 0.0:
            # 剛剛打到：畫一下閃光就結束（簡化的判定特效）
            if self.show_flash and -pos * self.lead_ms <= FLASH_MS:
                self._draw_flash(qp, note, -pos * self.lead_ms / FLASH_MS)
            return
        # 音符頭的厚度照深度縮：遠的薄、近的厚
        thick = 0.055 * (1.0 - 0.55 * pos)
        head = self._quad(int(note.min_key), int(note.max_key),
                          max(0.0, pos - thick * 0.5), pos + thick * 0.5)
        qp.setBrush(QBrush(fill))
        if nt == STACCATO_NOTE_TYPE:
            qp.setPen(QPen(edge, 2, Qt.DotLine))    # 斷奏：虛線邊
        elif nt == SOFT_NOTE_TYPE:
            qp.setBrush(Qt.NoBrush)                 # 弱音：只有外框
            qp.setPen(QPen(edge, 2))
        else:
            qp.setPen(QPen(edge, 1))
        qp.drawPolygon(head)

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
