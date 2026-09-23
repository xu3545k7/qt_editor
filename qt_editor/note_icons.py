"""放置模式用的音符小圖示：每種類型各一張，用的是和預覽模式同一套遊戲素材。

下拉選單只寫「Tap / Soft / Long…」的話，要放下去之前看不出長什麼樣，而編輯
模式的游標預覽又一律是有顏色的方塊（各類型看起來都一樣）。這裡把預覽模式的
畫法縮成一張 icon，工具列和游標旁邊都拿它來顯示「現在要放的是哪一種」。

圖示方向和譜面一致：音符往下落，所以頭在下面，長押的身體、顫音條往上長。

遊戲素材的載入（`graphic_pixmap`、`note_frame`）和「把圖撐到實心尖端貼齊格線」
（`art_fill_rect`）也放在這裡：譜面的預覽模式、遊戲預覽、這些 icon 都要貼同一
套圖，各自寫一份遲早會有一邊的音符看起來比別人窄。
"""

import os
from typing import Dict, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QIcon, QImage, QLinearGradient,
                         QPainter, QPen, QPixmap, QPolygonF)

#: 圖示的邏輯尺寸（px）。實際畫 2 倍再標 devicePixelRatio，高 DPI 才不糊。
ICON_W, ICON_H = 42, 30
_SCALE = 2

_GRAPHIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'graphic')
_ORIG = {'white': ('white', 'w'), 'white_piano': ('white_piano', 'w_piano'),
         'glissando': ('glissando', 'g'), 'trill': ('trill', 'tr')}

_TAP = {0: QColor(230, 70, 70), 1: QColor(70, 120, 230)}
_HOLD_FILL = {0: QColor(255, 170, 170, 170), 1: QColor(150, 200, 255, 170)}
_HOLD_EDGE = {0: QColor(255, 140, 140, 230), 1: QColor(120, 200, 255, 230)}

_pix_cache: Dict[str, QPixmap] = {}
_icon_cache: Dict[Tuple[int, int], QPixmap] = {}


def graphic_pixmap(rel: str) -> QPixmap:
    """載入 `graphic/` 底下的素材（相對路徑），同一張只讀一次。"""
    if rel not in _pix_cache:
        _pix_cache[rel] = QPixmap(os.path.join(_GRAPHIC, rel))
    return _pix_cache[rel]


def note_frame(kind: str, hand: int, width: int) -> QPixmap:
    """原版音符幀：`kind`（white / white_piano / glissando / trill）、左右手、
    寬度（1~10 鍵，超出就夾住）→ `graphic/orig/…` 的那一張。"""
    folder, prefix = _ORIG[kind]
    lr = 'l' if int(hand) == 1 else 'r'
    return graphic_pixmap(os.path.join('orig', f'{folder}_{lr}',
                                       f'{prefix}_{lr}_{max(1, min(10, int(width))):02d}.png'))


# 原版 note 幀左右尖端外面那圈柔邊要多淡才算「看不見」。w_r_03 最左邊 7 px
# 的最高 alpha 只有 43，在深色底上肉眼等於沒有東西；到 x=7 才跳到 157。
ART_SOLID_ALPHA = 128
_ART_SPAN_CACHE: Dict[int, Tuple[float, float]] = {}


def _art_solid_span(img: QPixmap) -> Tuple[float, float]:
    """圖裡「真的看得見」的那一段佔全圖寬度的比例，回傳 (左, 右)，值域 0~1。

    原版 note 幀是尖頭六邊形，兩端各留了一圈幾乎全透明的柔邊當抗鋸齒。把整張
    圖貼滿鍵道的話，實心的尖端會停在離鍵道邊界約 5% 的地方，兩顆相鄰的音符
    中間就永遠合不起來——設成 100% 寬也還是差一點。所以量出實心範圍，繪製時
    把圖往外撐到讓**實心尖端**落在鍵道邊界上，溢出去的只有那圈看不見的柔邊。

    整張圖掃 alpha 是 O(w×h)，密集譜上每幀做會很痛，但我們只要左右兩個邊界，
    從兩側往內找到第一根「有實心像素」的直行就可以停，通常十幾行就結束。
    結果依 `cacheKey()` 快取（同一張 QPixmap 換算一次就好）。
    """
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        return 0.0, 1.0
    ck = img.cacheKey()
    span = _ART_SPAN_CACHE.get(ck)
    if span is not None:
        return span
    qi = img.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = qi.width(), qi.height()
    bpl = qi.bytesPerLine()
    ptr = qi.constBits()
    ptr.setsize(bpl * h)
    buf = bytes(ptr)

    def solid(x: int) -> bool:
        # ARGB32 在小端機器上的位元組序是 B,G,R,A → alpha 在每個像素的第 4 個
        base = 4 * x + 3
        return any(buf[y * bpl + base] >= ART_SOLID_ALPHA for y in range(h))

    lo = 0
    while lo < w and not solid(lo):
        lo += 1
    if lo >= w:                       # 整張都是柔邊/全透明 → 當成滿版
        span = (0.0, 1.0)
    else:
        hi = w - 1
        while hi > lo and not solid(hi):
            hi -= 1
        span = (lo / w, (hi + 1) / w)
    _ART_SPAN_CACHE[ck] = span
    return span


def art_fill_rect(rect: QRectF, img: QPixmap) -> QRectF:
    """把 `rect` 換成「貼上去之後圖的實心部分剛好填滿 rect」的繪製矩形。

    只撐水平方向：使用者要的是左右尖端互相碰到，上下是固定的音符高度，一起
    撐的話反而會把長押頭撐出格子。
    """
    lo, hi = _art_solid_span(img)
    solid = hi - lo
    if solid <= 0.0 or solid >= 0.999:
        return rect
    width = rect.width() / solid
    return QRectF(rect.left() - lo * width, rect.top(), width, rect.height())


def _head(qp: QPainter, img: QPixmap, cx: float, bottom: float, w: float) -> QRectF:
    """把音符幀等比例貼成寬 w、底邊在 bottom 的一顆頭。"""
    if img.isNull() or img.width() <= 0:
        return QRectF(cx - w / 2, bottom - w * 0.4, w, w * 0.4)
    h = w * img.height() / float(img.width())
    rect = QRectF(cx - w / 2, bottom - h, w, h)
    qp.drawPixmap(rect.toRect(), img)
    return rect


def note_type_pixmap(note_type: int, hand: int = 0) -> QPixmap:
    """`note_type`：0 tap、1 soft、2 long、3 staccato、4 slide、64 trill。"""
    hand = 1 if int(hand) == 1 else 0
    key = (int(note_type), hand)
    if key in _icon_cache:
        return _icon_cache[key]
    W, H = ICON_W * _SCALE, ICON_H * _SCALE
    pix = QPixmap(W, H)
    pix.fill(Qt.transparent)
    qp = QPainter(pix)
    qp.setRenderHint(QPainter.Antialiasing, True)
    qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
    pad = 1.5 * _SCALE
    cx = W / 2.0
    head_w = W - 2 * pad
    nt = int(note_type)

    if nt == 2:                                          # long：身體往上長
        img = note_frame('white', hand, 3)
        ratio = img.height() / float(img.width()) if not img.isNull() and img.width() else 0.43
        body_w = head_w * 0.46
        body_bottom = H - pad - head_w * ratio / 2       # 身體收在頭的中線，不從下面露出來
        qp.setBrush(QBrush(_HOLD_FILL[hand]))
        qp.setPen(QPen(_HOLD_EDGE[hand], 1.5 * _SCALE))
        qp.drawRoundedRect(QRectF(cx - body_w / 2, pad, body_w, body_bottom - pad),
                           3 * _SCALE, 3 * _SCALE)
        _head(qp, img, cx, H - pad, head_w)
    elif nt == 3:                                        # staccato：頭上一個箭頭
        img = note_frame('white', hand, 3)
        ratio = img.height() / float(img.width()) if not img.isNull() and img.width() else 0.43
        arrow = graphic_pixmap(
            'LeftStacArrow-v2.png' if hand == 1 else 'RightStacArrow-v2.png')
        # 素材四周留白很多，放大一點並讓拖尾壓到音符頭上（箭頭先畫、頭蓋在上面）
        bottom = H - pad - head_w * ratio / 2
        side = min(bottom, head_w * 0.9)
        if not arrow.isNull() and side > 0:
            qp.drawPixmap(QRectF(cx - side / 2, bottom - side, side, side).toRect(), arrow)
        _head(qp, note_frame('white', hand, 3), cx, H - pad, head_w)
    elif nt == 4:                                        # slide：兩顆滑鍵連一條帶子
        w = head_w * 0.56
        low_cx, high_cx = pad + w / 2, W - pad - w / 2
        low_bottom, high_bottom = H - pad, H * 0.42
        img = note_frame('glissando', hand, 2)
        ratio = img.height() / float(img.width()) if not img.isNull() and img.width() else 0.45
        band = QColor(_TAP[hand])
        band.setAlpha(80)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(band))
        low_mid = low_bottom - w * ratio / 2
        high_mid = high_bottom - w * ratio / 2
        qp.drawPolygon(QPolygonF([
            QPointF(low_cx - w / 2, low_mid), QPointF(low_cx + w / 2, low_mid),
            QPointF(high_cx + w / 2, high_mid), QPointF(high_cx - w / 2, high_mid)]))
        _head(qp, img, high_cx, high_bottom, w)
        _head(qp, img, low_cx, low_bottom, w)
    elif nt & 64:                                        # trill：左右交替的顫音條
        img = note_frame('trill', hand, 3)
        ratio = img.height() / float(img.width()) if not img.isNull() and img.width() else 0.4
        cap_w = head_w * 0.7                             # 頭尾小一點，中間的顫音條才看得到
        head_h = cap_w * ratio
        top, bottom = pad + head_h / 2, H - pad - head_h / 2
        steps = 3
        step = (bottom - top) / steps
        inset = head_w * 0.15
        x1, x2 = cx - head_w / 2, cx + head_w / 2
        qp.setPen(Qt.NoPen)
        for i in range(steps):
            b = bottom - i * step + step * 0.12
            a = bottom - (i + 1) * step - step * 0.12
            mid = (a + b) / 2
            outer = x1 if i % 2 == 0 else x2
            grad = QLinearGradient(outer, 0.0, cx, 0.0)
            solid = QColor(_TAP[hand]); solid.setAlpha(235)
            faint = QColor(_TAP[hand]); faint.setAlpha(30)
            grad.setColorAt(0.0, solid)
            grad.setColorAt(0.45, solid)
            grad.setColorAt(1.0, faint)
            qp.setBrush(QBrush(grad))
            qp.drawPolygon(QPolygonF([
                QPointF(x1, mid), QPointF(x1 + inset, a), QPointF(x2 - inset, a),
                QPointF(x2, mid), QPointF(x2 - inset, b), QPointF(x1 + inset, b)]))
        qp.setOpacity(0.35)
        _head(qp, img, cx, top + head_h / 2, cap_w)
        qp.setOpacity(1.0)
        _head(qp, img, cx, H - pad, cap_w)
    else:                                                # tap / soft：一顆頭，置中
        kind = 'white_piano' if nt == 1 else 'white'
        img = note_frame(kind, hand, 3)
        ratio = img.height() / float(img.width()) if not img.isNull() and img.width() else 0.43
        _head(qp, img, cx, H / 2 + head_w * ratio / 2, head_w)

    qp.end()
    pix.setDevicePixelRatio(_SCALE)
    _icon_cache[key] = pix
    return pix


def note_type_icon(note_type: int, hand: int = 0) -> QIcon:
    return QIcon(note_type_pixmap(note_type, hand))


def hand_icon(hand: int) -> QIcon:
    """左右手選單用：一顆紅／藍的 tap。"""
    return note_type_icon(0, hand)


# ── 時值圖示（全音符、二分音符…） ─────────────────────────────────────
#
# 不用 Unicode 的音樂符號（U+1D15D 𝅝 那一段）：Windows 預設字型多半沒有，會
# 變成方框，就算有也上下對不齊；三連音更沒有現成的字。直接用畫的：符頭、
# 符桿、符尾數量照時值來，三連音在左上角標一個 3。

#: 時值圖示的邏輯尺寸（px）
VALUE_ICON_W, VALUE_ICON_H = 18, 24
_VALUE_SCALE = 4
_value_cache: Dict[Tuple[float, str], QPixmap] = {}

#: 幾拍 → (空心符頭, 有符桿, 符尾數)。一拍 = 四分音符。
_PLAIN_VALUES = {
    4.0: (True, False, 0),       # 全音符
    2.0: (True, True, 0),        # 二分音符
    1.0: (False, True, 0),       # 四分音符
    0.5: (False, True, 1),       # 八分音符
    0.25: (False, True, 2),      # 16 分音符
    0.125: (False, True, 3),     # 32 分音符
    0.0625: (False, True, 4),    # 64 分音符
}


def _value_shape(beats: float) -> Tuple[bool, bool, int, int]:
    """回傳 (空心, 有符桿, 符尾數, 連音數字)。連音數字 0 = 一般音符。

    任意「N 分音符」（一個全音符分成 N 份）照樂譜的連音寫法畫：N 塞進不超過
    它的最大 2 的冪次那種音符裡，左上角標 N 的奇數部分。12 分 = 八分音符標 3、
    20 分 = 16 分音符標 5、7 分 = 四分音符標 7。
    """
    for base, (hollow, stem, flags) in _PLAIN_VALUES.items():
        if abs(beats - base) < 1e-6:
            return hollow, stem, flags, 0
    division = 4.0 / beats if beats > 0 else 0.0
    n = int(round(division))
    if n >= 1 and abs(division - n) < 1e-4:
        power = 1
        while power * 2 <= n:
            power *= 2
        odd = n
        while odd % 2 == 0:
            odd //= 2
        base = 4.0 / min(power, 64)
        hollow, stem, flags = _PLAIN_VALUES.get(base, (False, True, 4))
        return hollow, stem, flags, (odd if odd > 1 else 0)
    return False, True, 0, 0


def note_value_pixmap(beats: float, color: QColor = None) -> QPixmap:
    """畫一個時值的音符符號（`beats`：幾個四分音符長）。"""
    color = QColor(color) if color is not None else QColor(35, 35, 40)
    key = (round(float(beats), 6), color.name(QColor.HexArgb))
    if key in _value_cache:
        return _value_cache[key]
    from PyQt5.QtGui import QFont, QPainterPath, QTransform
    s = _VALUE_SCALE
    pix = QPixmap(VALUE_ICON_W * s, VALUE_ICON_H * s)
    pix.fill(Qt.transparent)
    qp = QPainter(pix)
    qp.setRenderHint(QPainter.Antialiasing, True)
    qp.scale(s, s)
    hollow, stem, flags, tuplet = _value_shape(float(beats))

    if not stem:                                           # 全音符：大一點、置中
        cx, cy, rx, ry = 9.0, 16.0, 5.0, 3.4
    else:
        cx, cy, rx, ry = 6.2, 19.6, 3.9, 2.8
    head = QPainterPath()
    head.addEllipse(QPointF(0, 0), rx, ry)
    tilt = QTransform().translate(cx, cy).rotate(0 if not stem else -22)
    outer = tilt.map(head)
    qp.setPen(Qt.NoPen)
    qp.setBrush(color)
    if hollow:
        hole = QPainterPath()
        if stem:                                           # 二分音符：斜斜的細長洞
            hole.addEllipse(QPointF(0, 0), rx * 0.68, ry * 0.36)
            hole = QTransform().translate(cx, cy).rotate(-32).map(hole)
        else:                                              # 全音符：洞往另一邊斜
            hole.addEllipse(QPointF(0, 0), rx * 0.42, ry * 0.8)
            hole = QTransform().translate(cx, cy).rotate(-35).map(hole)
        qp.drawPath(outer.subtracted(hole))
    else:
        qp.drawPath(outer)

    if stem:
        sx = cx + rx * 0.92 - 0.35
        top = 2.2
        qp.drawRect(QRectF(sx - 0.55, top, 1.1, cy - 0.8 - top))
        spacing = 3.3 if flags <= 3 else 2.9
        for i in range(flags):
            y = top + i * spacing
            flag = QPainterPath(QPointF(sx, y))
            flag.cubicTo(sx + 1.2, y + 3.2, sx + 6.2, y + 4.2, sx + 4.6, y + 8.8)
            flag.cubicTo(sx + 5.0, y + 5.8, sx + 2.2, y + 4.6, sx, y + 3.0)
            flag.closeSubpath()
            qp.drawPath(flag)

    if tuplet:
        font = QFont()
        font.setBold(True)
        label = str(tuplet)
        font.setPixelSize(8 if len(label) == 1 else 6)
        text = QPainterPath()
        text.addText(0.0, 7.5, font, label)                # 路徑而不是 drawText，縮放後才不糊
        qp.drawPath(text)

    qp.end()
    pix.setDevicePixelRatio(s)
    _value_cache[key] = pix
    return pix


def note_value_icon(beats: float, color: QColor = None) -> QIcon:
    return QIcon(note_value_pixmap(beats, color))
