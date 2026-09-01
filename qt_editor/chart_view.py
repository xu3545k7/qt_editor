"""
chart_view.py
=============
QPainter 渲染的樂譜編輯器視圖元件，完整功能版。

功能清單（對應 graphical_chartmaker.py）
----------------------------------------
渲染
  - 背景、鍵位格線、節拍/小節線、時間標籤
  - 音符繪製：顏色依 hand/note_type，pitch 數字
  - 選取框線高亮、拖曳橡皮筋、Alloc 覆蓋框
  - 播放 judge line

滑鼠
  - 左鍵拖曳：框選音符
  - 左鍵單擊 + Ctrl：加選/取消
  - 滾輪：自適應速度捲動
  - Ctrl + 左鍵拖曳：拖曳複製（16分音符 snap）
  - 右鍵：音符屬性對話框

鍵盤
  Up/Down          : 捲動（+Shift 加快 4x）
  Left/Right       : 鍵位平移（+Ctrl 10x，+Shift 5x）
  Ctrl+Up/Down     : 時間移動（32 分音符步）
  +/-              : 縮放
  Ctrl+Z           : Undo
  Ctrl+C           : 複製選取
  Ctrl+V           : 貼上到游標位置
  Delete           : 刪除選取
  H                : note_type → long(2)
  T                : note_type → tap(0)
  K                : note_type → staccato(3)
  L                : hand → 左(1)
  R                : hand → 右(0)
  C                : 就地 duplicate
  P                : 播放視窗
  Shift+P          : 播放選取區
  S                : 停止播放
  Shift+A          : 啟動 Alloc Section 模式
  Enter            : 確認 Alloc Section
  Escape           : 取消 Alloc Section / 取消選取

Alloc Section
  - 選取後 Shift+A 進入
  - 顯示紅框，拖曳改變鍵位範圍
  - 依 pitch 比例自動分配鍵位

座標系
------
X: 鍵位 0..TOTAL_GAME_KEYS（左→右 pixel）
Y: 下方 = window_start（較早），上方 = window_end（較晚）
   pixel_y = H * (1 - (unit_rel / window_size))
"""

from __future__ import annotations

import math
import os
from bisect import bisect_left, bisect_right
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from statistics import median

from PyQt5.QtCore import Qt, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt5.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QBrush, QIcon,
    QKeyEvent, QLinearGradient,
    QMouseEvent, QPaintEvent, QPixmap, QPolygonF, QResizeEvent, QWheelEvent,
)
from PyQt5.QtWidgets import QDialog, QInputDialog, QWidget

from .models import (
    GNote, NoteModel, TOTAL_GAME_KEYS, LONG_BIT, lane_range_to_external, lane_to_external,
    build_slide_index_map, slide_next_note, midi_to_official_piano_index,
    note_is_long, note_is_slide, note_is_trill,
    hold_fix_candidate, classify_hold_length,
    trill_sub_cells, trill_fallback_cells,
    make_trill_from_notes, explode_trill,
    move_trill_cell, shift_trill_cells, refit_trill_cells,
)
from .time_mapper import TimeMapper
from .property_dialog import NotePropertyDialog
from .i18n import t

# ---------------------------------------------------------------------------
# 色彩常數
# ---------------------------------------------------------------------------
BG_COLOR         = QColor(28, 28, 32)
GRID_MINOR       = QColor(50, 50, 58)
GRID_MAJOR       = QColor(90, 90, 100)
BARLINE_COLOR    = QColor(220, 200, 60)
BEATLINE_COLOR   = QColor(70, 70, 85)

# 一般模式的分區金線：28 格分成 4 區（1~7 / 8~14 / 15~21 / 22~28），
# 邊界畫在格線層（音符之下），所以只是背景參考線、不會蓋住音符。
ZONE_LINE_COLOR  = QColor(198, 160, 60, 170)
ZONE_LANES       = 7

NOTE_RIGHT       = QColor(255, 179, 179)
NOTE_LEFT        = QColor(166, 216, 255)
# 無主音（hand=2）。官方資料本來就有這個值（normal 譜有 54% 的音符是 hand=2），
# 而「匯入 MIDI 疊上去」也用它。以前 `if hand == 0 else 左手` 會把它畫成左手，
# 疊上去之後根本分不出哪些是新來的，所以給它自己的顏色。
NOTE_NONE        = QColor(200, 235, 180)
NOTE_NONE_LONG   = QColor( 90, 140,  70)
NOTE_OUT_N       = QColor( 50,  95,  35)
NOTE_SOFT        = QColor(255, 215,   0)
NOTE_STAC        = QColor(210, 150, 255)   # staccato 紫色
NOTE_SLIDE_R     = QColor(255, 120, 120)   # slide 右手
NOTE_SLIDE_L     = QColor(120, 200, 255)   # slide 左手
SLIDE_BAND_R     = QColor(255, 110, 110, 130)  # slide 梯形帶（右手）
SLIDE_BAND_L     = QColor(110, 200, 255, 130)  # slide 梯形帶（左手）
SLIDE_EDGE_R     = QColor(255, 150, 150)
SLIDE_EDGE_L     = QColor(150, 220, 255)
NOTE_TRILL_R     = QColor(120, 230, 170)   # trill 右手（顫音）— 編輯模式格子色
NOTE_TRILL_L     = QColor(120, 210, 230)   # trill 左手
NOTE_OUT_TRILL   = QColor( 30, 120,  90)
TRILL_ZIGZAG     = QColor( 40, 180, 130)
# 預覽模式 trill：中間淡色無邊方形 mesh + 開頭實心 tap / 尾端空心 tap
TRILL_PALE_R     = QColor(255, 140, 140, 110)   # 淡色 mesh（右手）
TRILL_PALE_L     = QColor(150, 185, 255, 110)   # 淡色 mesh（左手）
TRILL_TAP_R      = QColor(230,  70,  70)        # 實心/空心 tap 色（右手）
TRILL_TAP_L      = QColor( 70, 120, 230)        # （左手）
NOTE_RIGHT_LONG  = QColor(197,  48,  48)
NOTE_LEFT_LONG   = QColor( 28,  95, 153)
NOTE_OUT_R       = QColor(120,  20,  20)
NOTE_OUT_L       = QColor( 10,  60, 110)
NOTE_OUT_S       = QColor(140, 110,   0)
MIDI_CHANNEL_COLORS = [
    QColor(239, 83, 80),
    QColor(255, 167, 38),
    QColor(255, 238, 88),
    QColor(102, 187, 106),
    QColor(38, 166, 154),
    QColor(41, 182, 246),
    QColor(92, 107, 192),
    QColor(126, 87, 194),
    QColor(171, 71, 188),
    QColor(236, 64, 122),
    QColor(141, 110, 99),
    QColor(120, 144, 156),
    QColor(255, 112, 67),
    QColor(156, 204, 101),
    QColor(77, 182, 172),
    QColor(79, 195, 247),
]


def _note_gradient(base: QColor, rect) -> QLinearGradient:
    """音符方塊的縱向漸層：頭部一段高光，其餘維持本色、底部略暗。

    高光的厚度是**固定像素**而不是方塊高度的比例——依比例的話，短音符上是
    俐落的一道高光，長押拉長到幾百 px 就會變成一整片不均勻的色塊，反而顯髒。
    固定像素能讓長短音符看起來是同一種材質。
    保留原本的 alpha，隱藏音符的半透明才不會被漸層洗掉。
    """
    top = float(rect.top())
    height = max(1.0, float(rect.height()))
    grad = QLinearGradient(0.0, top, 0.0, top + height)
    alpha = base.alpha()

    def at(color):
        c = QColor(color); c.setAlpha(alpha); return c

    # 高光只佔頭部這麼厚；方塊比它還矮時就整塊漸層
    zone = min(1.0, NOTE_HILIGHT_PX / height)
    grad.setColorAt(0.0, at(base.lighter(NOTE_GRADIENT_TOP)))
    if zone < 1.0:
        grad.setColorAt(zone, at(base))
    grad.setColorAt(1.0, at(base.darker(NOTE_GRADIENT_BOTTOM)))
    return grad


def _perceptual_distance(a: QColor, b: QColor) -> float:
    """兩色的視覺差距。

    只比色相是不夠的——亮黃 (255,238,88) 和橙 (255,167,38) 色相差得不多，
    但真正難分辨的是「亮度也接近」的組合。這裡用加權 RGB 距離（近似人眼
    對綠最敏感、藍最遲鈍），色相和明暗一起算進去。
    """
    dr = (a.red() - b.red()) / 255.0
    dg = (a.green() - b.green()) / 255.0
    db = (a.blue() - b.blue()) / 255.0
    return (2.0 * dr * dr + 4.0 * dg * dg + 3.0 * db * db) ** 0.5


def _distinct_first_order(colors):
    """依視覺差距做 greedy farthest-point 排序：前面幾個差最大，越後面才越接近。
    回傳調色盤索引的排列（對比優先）。"""
    n = len(colors)
    if n == 0:
        return []
    dist = [[_perceptual_distance(colors[i], colors[j]) for j in range(n)]
            for i in range(n)]
    order = [0]                      # 從第一色（紅）開始
    remaining = set(range(1, n))
    while remaining:
        # 選離「已選集合」最近距離最大的（farthest-point）
        best = max(remaining, key=lambda r: min(dist[r][o] for o in order))
        order.append(best)
        remaining.discard(best)
    return order


# 對比優先的調色盤索引順序（用到幾個 channel 就從前面取幾個 → 最大對比）
MIDI_COLOR_ORDER = _distinct_first_order(MIDI_CHANNEL_COLORS)

SEL_OUTLINE      = QColor(255, 230,   0)   # 黃色外框
RUBBER_COLOR     = QColor(255, 255,   0)
JUDGELINE_COLOR  = QColor(  0, 200, 255)
ALLOC_COLOR      = QColor(255,  60,  60)

PITCH_TEXT       = QColor(  0,   0,   0)
KEY_LABEL        = QColor(190, 190, 200)
TIME_LABEL       = QColor(160, 160, 170)

TIME_WINDOW_UNITS  = 8.0
SCROLL_STEP_UNITS  = 0.125
MIN_WINDOW_UNITS   = 0.5
MAX_WINDOW_UNITS   = 256.0
PRE_ROLL_UNITS     = 4.0
MIN_NOTE_HEIGHT_PX = 2
PITCH_GRID_KEYS    = 88
PITCH_MIDI_MIN     = 21
PITCH_MIDI_MAX     = 108

# 音高模式：鋼琴鍵盤式排列（白鍵寬、黑鍵窄）
_BLACK_PITCH_CLASSES = frozenset({1, 3, 6, 8, 10})   # C#,D#,F#,G#,A#
_WHITE_KEY_W = 1.0
_BLACK_KEY_W = 0.44   # 黑鍵寬（白鍵寬=1）。必須 <0.5，白鍵欄位才會比黑鍵寬


def _is_black_pitch(midi_pitch: int) -> bool:
    return (int(midi_pitch) % 12) in _BLACK_PITCH_CLASSES


def _parse_beats_text(text: str) -> float:
    """把使用者輸入的拍數字串解析成 float，支援 '3/4'、'1 3/4'、'0.75'。"""
    s = (text or '').strip()
    if not s:
        raise ValueError('empty')
    if ' ' in s and '/' in s:            # 帶分數：'1 3/4'
        whole, frac = s.split(None, 1)
        num, den = frac.split('/')
        return float(whole) + float(num) / float(den)
    if '/' in s:
        num, den = s.split('/')
        return float(num) / float(den)
    return float(s)


def _beats_to_mixed_fraction(value: float, max_den: int = 64) -> str:
    """把拍數轉回帶分數字串，給輸入框回填用。"""
    from fractions import Fraction

    frac = Fraction(value).limit_denominator(max_den)
    whole = frac.numerator // frac.denominator
    rem = frac.numerator % frac.denominator
    if whole != 0 and rem != 0:
        return f'{whole} {rem}/{frac.denominator}'
    if rem == 0:
        return str(whole)
    return f'{rem}/{frac.denominator}'


def _build_pitch_key_spans():
    """回傳 (note, draw, total)，單位 = 白鍵寬。

    `note[i]` 是音符欄位（互不重疊），`draw[i]` 是畫鍵盤用的鍵形（黑鍵會
    壓在白鍵上面）。

    真實鋼琴的幾何：白鍵每個等寬並排，黑鍵**騎在兩個白鍵的交界上**、比較窄。
    舊版是給黑鍵自己一格水平空間（白 1.0、黑 0.6 依序排開），那不是鋼琴的
    長相——白鍵會被黑鍵擠得寬窄不一。改成真實排法之後，音符欄位和底下的
    鍵盤共用這張表，所以看起來像鋼琴而且仍然對得齊。
    """
    draw = [None] * PITCH_GRID_KEYS      # 畫鍵盤用：白鍵整格、黑鍵騎在交界上
    white = 0
    for i in range(PITCH_GRID_KEYS):
        if not _is_black_pitch(PITCH_MIDI_MIN + i):
            draw[i] = (float(white), float(white + 1))
            white += 1
    total = float(white)
    half = _BLACK_KEY_W / 2.0
    for i in range(PITCH_GRID_KEYS):
        if draw[i] is not None:
            continue
        centre = total                    # 黑鍵以「下一個白鍵的左緣」為中心
        for j in range(i + 1, PITCH_GRID_KEYS):
            if draw[j] is not None:
                centre = draw[j][0]
                break
        draw[i] = (centre - half, centre + half)

    # 音符欄位：黑鍵沿用黑鍵的範圍，白鍵只取「上面沒有黑鍵」的那一段。
    # 這樣白鍵音符和黑鍵音符不會左右重疊，而且白鍵欄位一定比黑鍵寬
    # （最窄的白鍵剩 1 - _BLACK_KEY_W，只要黑鍵窄於半格就成立）。
    note = list(draw)
    for i in range(PITCH_GRID_KEYS):
        pitch = PITCH_MIDI_MIN + i
        if _is_black_pitch(pitch):
            continue
        lo, hi = draw[i]
        if i > 0 and _is_black_pitch(pitch - 1):
            lo = draw[i - 1][1]
        if i + 1 < PITCH_GRID_KEYS and _is_black_pitch(pitch + 1):
            hi = draw[i + 1][0]
        note[i] = (lo, hi)
    return note, draw, total


_PITCH_KEY_SPANS, _PITCH_KEY_DRAW, _PITCH_KEY_TOTAL = _build_pitch_key_spans()

# 音高模式底部的鋼琴鍵盤
# 音符方塊：白色圓角外框
NOTE_FRAME           = QColor(255, 255, 255, 235)
# 音高模式的外框改用左右手色：方塊本色被聲部配色/力度明暗佔走了，紅藍看不出來
HAND_FRAME_R         = QColor(255, 120, 110, 245)   # 右手
HAND_FRAME_L         = QColor(120, 195, 255, 245)   # 左手
# 調性高亮：調內的音格鋪一層淡底，主音再亮一點（黑底上只能加亮不能壓暗）
SCALE_IN_KEY_BG      = QColor(120, 150, 200, 26)
SCALE_TONIC_BG       = QColor(200, 170,  90, 40)
# 調外音的琴鍵色：只比正常鍵「灰一階」，不是壓成深灰。這裡的資訊在音符欄
# 已經用底色講過一次了，鍵盤還是要先看得出是鍵盤——壓太重會變成一塊板子。
PIANO_OFF_WHITE_TOP  = QColor(214, 214, 220)
PIANO_OFF_WHITE_MID  = QColor(196, 196, 203)
PIANO_OFF_WHITE_BOT  = QColor(166, 166, 175)
PIANO_OFF_BLACK_TOP  = QColor( 44,  44,  52)
PIANO_OFF_BLACK_MID  = QColor( 24,  24,  30)
# 相鄰的調外白鍵不能糊成一片，邊框要壓得住
PIANO_OFF_EDGE       = QColor( 96,  96, 106)
GHOST_NOTE_ALPHA     = 58      # 幽靈音符（被手別篩掉的那一手）
HIDDEN_NOTE_ALPHA    = 70      # 遊戲譜面隱藏的音符：半透明
HIDDEN_EXTRACT_ASK_OVER = 3    # 拆出目標超過這個數量就跳複選清單
HIDDEN_LINK_COLOR    = QColor(255, 255, 255, 150)   # 隱藏音 -> 寄主的連線
NOTE_GRADIENT_TOP    = 122     # 頭部高光亮多少 %
NOTE_GRADIENT_BOTTOM = 108     # 底部暗多少 %（放輕，長押才不會拖成髒色塊）
NOTE_HILIGHT_PX      = 14.0    # 高光的固定厚度（px）
NOTE_CORNER_RADIUS   = 4.0
PEDAL_LANE_PX        = 18.0    # 音高模式左側的踏板欄寬度
DYN_LANE_PX          = 26.0    # 強弱欄寬度（左右各一條）
DYN_LANE_BG          = QColor(18, 20, 26)
DYN_LANE_EDGE        = QColor(70, 78, 96)
DYN_LINE_RIGHT       = QColor(255, 150, 150)   # 右手，和音符的紅同系
DYN_LINE_LEFT        = QColor(150, 200, 255)   # 左手，和音符的藍同系
DYN_FILL_ALPHA       = 60
DYN_MARK_TEXT        = QColor(236, 240, 248)
DYN_BASELINE_COLOR   = QColor(150, 158, 176, 170)   # 參考線＝這一手目前的平均力度
DYN_SCALE_TEXT       = QColor(150, 158, 176)        # 欄位頂端的刻度數字
PATTERN_PREVIEW_FILL = QColor(255, 235, 160, 120)   # 音階輔助的拖曳預覽
PATTERN_PREVIEW_EDGE = QColor(255, 245, 200, 220)
LANE_HEADER_PX       = 74.0                        # 欄位標籤高度（文字直排）
LANE_HEADER_TEXT     = QColor(20, 20, 24)
LANE_HEADER_EDGE     = QColor(0, 0, 0, 120)
PEDAL_LANE_BG        = QColor(24, 20, 14)
PEDAL_LANE_EDGE      = QColor(90, 78, 52)
PEDAL_SPAN_COLOR     = QColor(232, 168, 52)      # 踩下的區間
PEDAL_SPAN_ACTIVE    = QColor(255, 214, 120)     # 判定線正踩著
PEDAL_DRAG_COLOR     = QColor(255, 214, 120, 140)
PEDAL_EDGE_LINE      = QColor(40, 30, 10)        # 六角形描邊，尖端才看得清楚
PEDAL_HEX_CAP_PX     = 5.0                       # 上下尖角的斜切高度
PEDAL_EDGE_GRAB_PX   = 6.0                       # 抓邊界的容許距離
HOLD_TAIL_GRAB_PX    = 7.0                       # 抓長條尾端的容許距離
VELOCITY_MIN_SHADE   = 0.42    # 力度 1 時的亮度倍率（0 會變全黑看不見）
# 力度數字（只在音高模式）。音高數字是**黑字直接畫在音符上**，所以力度改成
# 「深色藥丸底 + 暖橘字」——底色、字色、位置三樣都不同，不會和音高看混。
VELOCITY_TEXT        = QColor(255, 198,  92)
VELOCITY_TEXT_BG     = QColor(  0,   0,   0, 215)   # 描邊色，讓橘字在亮/暗音符上都讀得到
VELOCITY_PILL_H      = 11.0
VELOCITY_TEXT_MIN_H  = 26.0    # 音符至少這麼高才畫得下音高＋力度兩行
PITCH_GRID_FOCUS_KEYS = 6      # 放置模式下滑鼠左右各顯示幾個欄位的格線
# 鍵盤高度。之前「拉伸到判定線」時大約是這個量體，你要的是那個長度——
# 只是不能跟著縮放變動，所以改成固定值。真實鋼琴白鍵長寬比約 6:1，
# 52 個白鍵鋪滿 1300px 時單鍵約 25px 寬，對應長度就在這個範圍。
PIANO_STRIP_DEFAULT = 168      # 鍵盤高度預設值（可在偏好設定調整）
TAIL_PAD_WINDOWS = 1.0         # 曲末額外留幾個視窗高的空白，讓譜捲得完


def _velocity_shading_on() -> bool:
    try:
        from .settings import settings as _st
        return bool(_st.get('pitch_velocity_shading', True))
    except Exception:                       # noqa: BLE001
        return True


def _velocity_numbers_on() -> bool:
    try:
        from .settings import settings as _st
        return bool(_st.get('pitch_velocity_numbers', True))
    except Exception:                       # noqa: BLE001
        return True


def _setting_on(key: str, default: bool = True) -> bool:
    try:
        from .settings import settings as _st
        return bool(_st.get(key, default))
    except Exception:                       # noqa: BLE001
        return default


def keyboard_height() -> int:
    """鍵盤（＝判定線位置）的高度，所有格子共用同一個值。"""
    try:
        from .settings import settings as _st
        return max(48, int(_st.get('keyboard_height_px', PIANO_STRIP_DEFAULT)))
    except Exception:                       # noqa: BLE001
        return PIANO_STRIP_DEFAULT
PIANO_FLASH_MS   = 90                      # tap 沒長度，給它這麼久的亮起時間
PIANO_KEY_WHITE  = QColor(228, 228, 233)
PIANO_KEY_BLACK  = QColor( 28,  28,  34)
LANE_KEY_BASE    = QColor(232, 232, 236)   # 一般模式 28 格鍵盤的白鍵
LANE_KEY_LIT     = QColor(150, 235, 165)   # 被判定時的淡綠
LANE_KEY_GAP_PX  = 3.0     # 鍵與鍵之間的黑縫
LANE_KEY_RADIUS  = 5.0     # 鍵的圓角
LANE_BLACK_W     = 0.4    # 黑鍵區塊佔鍵寬的比例
LANE_BLACK_H     = 0.42    # 佔鍵高的比例
# 預覽模式固定高度（毫秒）
PREVIEW_MS = 300
# 固定像素高度（預設）— 預覽不再依時間或 BPM 縮放
PREVIEW_PX = 40




# ---------------------------------------------------------------------------
# ChartView
# ---------------------------------------------------------------------------

# 分割模式：每個格子的代表色（0 = 左/上 = 紅，1 = 右/下 = 藍）
PANE_COLORS = (QColor(200, 60, 55), QColor(50, 115, 205))


class SharedEditState:
    """分割模式下，兩個 ChartView 共用的編輯狀態。

    兩個格子看的是同一份 NoteModel（undo 因此天生共用），選取與剪貼簿
    則存在這個物件裡，讓左邊選取的音符在右邊也是選取狀態。
    """

    def __init__(self, selected=None, clipboard=None) -> None:
        self.selected:  Set[int]   = set(selected or ())
        self.clipboard: List[dict] = list(clipboard or ())


class ChartView(QWidget):
    """完整功能的樂譜編輯視窗。"""

    # ── 對外訊號 ──────────────────────────────────────────────────────
    focus_gained      = pyqtSignal()             # 取得鍵盤焦點（分割模式切換作用格）
    selection_changed = pyqtSignal(int)          # 已選取數
    status_changed    = pyqtSignal(str)          # 狀態列文字
    note_edited       = pyqtSignal()             # 任何可 undo 的修改
    play_requested    = pyqtSignal(float, float) # start_ms, end_ms
    play_full_requested = pyqtSignal()            # 播放整首
    # 播放 MIDI：start_ms, end_ms, 指定音符 idx 清單（None = 範圍內全部）
    play_midi_requested = pyqtSignal(float, float, object)
    overlay_swap_requested = pyqtSignal()         # 疊層分割：對調上下層
    new_chart_requested = pyqtSignal()            # 還沒有譜面就想放置音符
    arrange_required  = pyqtSignal()              # 未排譜時想切換檢視 → 請主視窗詢問
    note_placed       = pyqtSignal(object)        # 剛放下一顆音符（播放中要即時發聲）
    play_from_window_requested = pyqtSignal()     # 從視窗底部播到末尾
    stop_requested    = pyqtSignal()
    pause_requested   = pyqtSignal()
    resume_requested  = pyqtSignal()
    note_input_changed = pyqtSignal(bool)        # 放置模式開關
    set_measure_bpm_requested = pyqtSignal(int)  # 右鍵小節空白，傳小節編號
    set_measure_time_sig_requested = pyqtSignal(int)
    insert_measure_requested = pyqtSignal(int)   # 在此小節前插入一個空白小節
    delete_measure_requested = pyqtSignal(int)   # 刪除此小節

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        # ── 分割模式 ─────────────────────────────────────────────────
        # `_shared` 必須在任何 selected/clipboard 存取之前建立。
        self._shared: Optional[SharedEditState] = None
        self._peer:   Optional['ChartView']     = None
        self.pane_role:   int  = 0      # 0 = 左/上（紅）, 1 = 右/下（藍）
        self.pane_active: bool = True   # 是否為工具列目前作用的格子
        self.split_active: bool = False # 分割模式是否開啟（決定要不要畫色框）
        # 疊層分割：'' = 一般 / 'base' = 底層實色 / 'top' = 疊在上面的半透明層
        self.overlay_role: str = ''
        self.overlay_opacity: float = 0.55
        self._shift_tap: bool = False   # 單獨輕點 Shift 才算（見 keyReleaseEvent）
        # 時間視窗連動：一格捲動/縮放時，另一格跟到同一段時間
        self.time_sync: bool = True
        self._syncing: bool = False
        self._last_window_sig: Optional[Tuple[float, float, float]] = None

        self.model:  NoteModel  = NoteModel()
        self.mapper: TimeMapper = TimeMapper()

        # ── 視窗狀態 ─────────────────────────────────────────────────
        self.window_start_unit: float = 0.0
        self.window_size_unit:  float = TIME_WINDOW_UNITS
        self.scroll_invert:     bool  = False
        self._min_unit: float = 0.0
        self._max_unit: float = TIME_WINDOW_UNITS

        # ── 選取 ──────────────────────────────────────────────────────
        # 實際容器；分割時改由 `_shared` 提供（見 selected / clipboard property）
        self._selected:  Set[int]   = set()
        self._clipboard: List[dict] = []

        # ── 框選拖曳 ──────────────────────────────────────────────────
        self._rubber_start: Optional[QPoint] = None
        self._rubber_end:   Optional[QPoint] = None
        # rubber stored in absolute units so selection follows scrolling
        self._rubber_start_u: Optional[float] = None
        self._rubber_end_u: Optional[float] = None
        self._is_rubbing:   bool = False

        # ── Ctrl+drag 複製 ────────────────────────────────────────────
        self._is_drag_copy:      bool  = False
        self._drag_start_abs_ms: float = 0.0
        self._drag_cur_delta_ms: float = 0.0
        self._drag_snap_ms:      float = 0.0

        # ── 自適應滾輪 ───────────────────────────────────────────────
        self._wheel_events:    deque = deque()
        self._wheel_hist_sec:  float = 0.6
        self._wheel_max_items: int   = 16
        self._wheel_scale:     float = 3.0
        self._wheel_min_mult:  float = 1.0
        self._wheel_max_mult:  float = 8.0

        # ── 游標位置（貼上用）────────────────────────────────────────
        self._last_mouse_unit: Optional[float] = None

        # ── Alloc Section ─────────────────────────────────────────────
        self.alloc_active:      bool         = False
        self.alloc_locked:      List[int]    = []
        self.alloc_orig:        Dict[int, Tuple] = {}
        self.alloc_target_min:  int          = 0
        self.alloc_target_max:  int          = 0
        self.alloc_time_min_u:  float        = 0.0
        self.alloc_time_max_u:  float        = 0.0
        self._alloc_drag_edge:  Optional[Tuple[str, str]] = None

        # ── Judge line ────────────────────────────────────────────────
        self._judge_ms: Optional[float] = None

        # ── 可見音符快取（供 hit-test）────────────────────────────────
        self._visible: List[Tuple[QRectF, GNote]] = []

        # trill mesh 逐格編輯：hit 區域 + 目前選中的子音符集合 {(trill_note, sub_index)}
        self._trill_cell_hits: List[Tuple[QRectF, GNote, int]] = []
        self._sel_cells: Set[Tuple[GNote, int]] = set()

        # ── 字型 ──────────────────────────────────────────────────────
        self._font_key   = QFont('Consolas', 7)
        self._font_time  = QFont('Consolas', 7)
        self._font_pitch = QFont('Consolas', 7, QFont.Bold)
        self._font_vel   = QFont('Consolas', 7)

        # 狀態列輔助文字
        self._drag_status: str = ''

        # 小節線拖曳（time_uniform 模式下）
        self._barline_dragging: bool = False
        self._barline_drag_measure: Optional[int] = None  # 目標要改變 BPM 的小節 index (0-based)
        self._barline_drag_start_ms: Optional[int] = None
        self._barline_drag_orig_end_ms: Optional[int] = None
        self._barline_drag_py: Optional[int] = None

        # ── 預覽模式 ──────────────────────────────────────────
        self.preview_mode: bool = False
        self._pix_cache: dict   = {}      # 圖片快取：檔名 → QPixmap

        # ── 時間均分模式 ──────────────────────────────────────
        self.time_uniform: bool = False
        self.pitch_mode:   bool = False
        self._grid_focus_slot = None      # 音高模式：滑鼠所在的欄位（決定格線在哪裡顯示）
        self.show_midi_pitch: bool = False   # False = 顯示 scale_piano(1~88)，True = MIDI(21~108)
        self._time_uniform_span_ms: float = 0.0

        # ── 放置音符模式 ───────────────────────────────────────
        self._note_input_mode:     bool            = False
        # 音階輔助模式：像放置模式一樣是個常駐模式，按住往上拖決定音數
        self._pattern_mode:        bool            = False
        self._pattern_kind:        str             = 'scale'
        self._pattern_direction:   int             = 1
        self._pattern_step_beats:  float           = 0.5
        self._pattern_key_override = None    # None = 自動偵測
        self._pattern_drag:        Optional[dict]  = None
        self._note_duration_beats: float           = 1.0    # 四分音符
        self._note_input_hand:     int             = 0      # 0=右手
        # 放置模式預設：寬度與音符類型
        self._note_input_width:    int             = 3      # 預設 3 格寬
        self._note_input_note_type: int            = 0      # 0 = Tap
        self._note_input_hover:    object          = None   # QPoint or None
        # 只編一隻手：'all' | 0（右手）| 1（左手）。被濾掉的那手畫成幽靈音符，
        # 看得到、選不到，當參考用。
        self.hand_filter:          object          = 'all'
        self._in_key_cache:        Optional[tuple] = None   # (Key, 音級 set)
        self._dyn_scale_cache:     dict            = {}     # hand -> (lo, hi)
        self._vel_shade_on:        bool            = True   # 每幀開頭讀一次
        self._lane_flag_cache:     Optional[tuple] = None   # 同上
        # 二分搜尋用的 start 陣列與最長時值，都綁 model.notes 這個 list 的身分
        self._note_start_cache:    Optional[tuple] = None
        self._max_span_cache:      Optional[tuple] = None
        # 這些快取存的都是「掃全譜算出來的東西」，不快取就是每幀 O(N)。
        # 任何可 undo 的修改都會發 note_edited，掛在那裡一次失效全部。
        self.note_edited.connect(self._invalidate_chart_caches)
        # 放置後直接往上拖曳可以拉長時值；tap 被拉長時自動轉成 hold
        self._input_drag_note:      Optional[GNote] = None
        self._pedal_drag: Optional[Tuple[float, float]] = None   # 踏板欄拖曳中的 (起, 迄) ms
        self._pedal_edge_drag: Optional[Tuple[int, str]] = None  # 正在拉的踏板邊界
        self._hold_tail_note:  Optional[GNote] = None   # 正在拉長度的長條
        self._hold_tail_moved: bool = False
        self._hold_tail_hover: bool = False
        self._pedal_edge_moved: bool = False                     # 這次拉邊有沒有真的動到
        self._dyn_drag: Optional[int] = None                     # 強弱欄拖曳中的 hand
        self._dyn_last_mark: Optional[float] = None              # 拖曳中上一顆記號的 ms
        self._vel_wheel_last_ms: float = 0.0    # 滾輪調力度：上一格的時間（合併 undo 用）
        self._input_drag_start_unit: float          = 0.0
        self._input_drag_base_end:   int            = 0
        self._input_drag_extended:   bool           = False

    def focusNextPrevChild(self, next: bool) -> bool:  # type: ignore[override]
        """Prevent default focus traversal on Tab so keyPressEvent receives Tab.
        Returning False stops Qt from moving focus to the next widget.
        """
        return False

    # ==================================================================
    # 分割模式（兩個格子看同一份譜）
    # ==================================================================

    @property
    def selected(self) -> Set[int]:
        sh = self._shared
        return sh.selected if sh is not None else self._selected

    @selected.setter
    def selected(self, value) -> None:
        tgt = self.selected
        if tgt is value:
            return
        new = set(value)
        tgt.clear()
        tgt.update(new)

    @property
    def clipboard(self) -> List[dict]:
        sh = self._shared
        return sh.clipboard if sh is not None else self._clipboard

    @clipboard.setter
    def clipboard(self, value) -> None:
        tgt = self.clipboard
        if tgt is value:
            return
        new = list(value)
        tgt[:] = new

    def link_pane(self, peer: 'ChartView') -> None:
        """把另一個格子接上來：共用選取／剪貼簿，並互相連動重繪。"""
        if peer is self:
            return
        shared = self._shared or peer._shared
        if shared is None:
            shared = SharedEditState(self._selected, self._clipboard)
        self._shared = shared
        peer._shared = shared
        self._peer = peer
        peer._peer = self

    def update(self, *args) -> None:  # type: ignore[override]
        """重繪自己時順便重繪對側格子（同一份譜，畫面必須一致）。

        視窗若有動過，順便把時間範圍推給對側格子。
        直接呼叫 QWidget.update 以避免兩邊互相遞迴。
        """
        self._push_window_to_peer()
        super().update(*args)
        peer = self._peer
        if peer is not None and peer.isVisible():
            QWidget.update(peer)

    def focusInEvent(self, ev) -> None:  # type: ignore[override]
        super().focusInEvent(ev)
        self.focus_gained.emit()

    # ── 時間視窗連動 ──────────────────────────────────────────────────

    def visible_ms_range(self) -> Tuple[float, float]:
        """目前可視的譜面時間範圍（start_ms, end_ms）。"""
        ws_ms = float(self.mapper.unit_to_ms(self.window_start_unit))
        if self.time_uniform:
            return ws_ms, ws_ms + max(1.0, float(self._time_uniform_span_ms or 1.0))
        return ws_ms, float(self.mapper.unit_to_ms(
            self.window_start_unit + self.window_size_unit))

    def _window_signature(self) -> Tuple[float, float, float]:
        return (float(self.window_start_unit),
                float(self.window_size_unit),
                float(self._time_uniform_span_ms))

    def _push_window_to_peer(self) -> None:
        """視窗有變動就把時間範圍鏡射到對側格子（小節模式↔音高模式都適用）。"""
        peer = self._peer
        if (peer is None or self._syncing or not self.time_sync
                or not peer.isVisible() or not self.isVisible()):
            return
        sig = self._window_signature()
        if sig == self._last_window_sig:
            return
        self._last_window_sig = sig
        self._syncing = True
        try:
            peer.apply_window_ms(*self.visible_ms_range())
        finally:
            self._syncing = False

    def apply_window_ms(self, start_ms: float, end_ms: float) -> None:
        """把可視時間範圍設成 [start_ms, end_ms]（由對側格子鏡射過來）。

        兩種模式的縱向映射不同（小節模式按 unit、時間/音高模式按 ms），
        這裡統一用 ms 當共同語言，讓兩格看的是同一段時間。
        """
        prev = self._syncing
        self._syncing = True
        try:
            span_ms = max(1.0, float(end_ms) - float(start_ms))
            self.window_start_unit = self.mapper.ms_to_unit(float(start_ms))
            if self.time_uniform:
                self._time_uniform_span_ms = span_ms
                self._sync_time_uniform_window_units()
            else:
                end_u = self.mapper.ms_to_unit(float(start_ms) + span_ms)
                self.window_size_unit = max(
                    MIN_WINDOW_UNITS,
                    min(MAX_WINDOW_UNITS, float(end_u - self.window_start_unit)),
                )
            self._clamp_window_start()
            # clamp 之後才記錄，否則下次會被誤判成「使用者動過」而回推
            self._last_window_sig = self._window_signature()
            QWidget.update(self)
            self._emit_status()
        finally:
            self._syncing = prev

    def sync_window_from(self, src: 'ChartView') -> None:
        """立刻把自己的可視範圍對齊到 src（分割剛開啟時用）。"""
        self.apply_window_ms(*src.visible_ms_range())

    # ── 疊層分割 ──────────────────────────────────────────────────────

    def set_overlay_role(self, role: str) -> None:
        """設定疊層角色：'' 一般 / 'base' 底層實色 / 'top' 半透明疊層。

        疊層時上面那格不畫自己的背景，並且整張畫面用 `overlay_opacity`
        疊上去，這樣底下那格才透得出來。
        """
        role = role if role in {'base', 'top'} else ''
        if role == self.overlay_role:
            return
        self.overlay_role = role
        top = (role == 'top')
        self.setAttribute(Qt.WA_TranslucentBackground, top)
        self.setAttribute(Qt.WA_NoSystemBackground, top)
        self.setAutoFillBackground(not top)
        QWidget.update(self)

    def set_input_enabled(self, enabled: bool) -> None:
        """疊層時只有作用中的格子吃滑鼠事件，另一格讓事件穿透過去。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not enabled)

    def scroll_to_ms(self, ms: float) -> None:
        """把視窗捲到指定譜面時間（分割時用來對齊另一格的位置）。"""
        self.window_start_unit = self.mapper.ms_to_unit(float(ms))
        if self.time_uniform:
            self._sync_time_uniform_window_units()
        self._clamp_window_start()
        self.update()
        self._emit_status()

    def window_start_ms(self) -> float:
        return float(self.mapper.unit_to_ms(self.window_start_unit))

    # ==================================================================
    # 公開 API
    # ==================================================================

    def load_model(self, model: NoteModel) -> None:
        self.model = model
        self.mapper = TimeMapper()
        self.mapper.build(
            model.get_beat_entries(),
            model.bpm,
            model.music_end_ms,
            model.beats_per_bar,
        )
        self.selected.clear()
        self.clipboard.clear()
        self.alloc_active = False
        self._judge_ms = None
        self._last_window_sig = None   # 換譜後重新開始比對視窗變動
        self._invalidate_chart_caches()
        self._note_input_mode = False
        self._note_input_hover = None
        self._input_drag_note = None
        self.setCursor(Qt.ArrowCursor)
        self._update_unit_bounds()
        # 載入新譜面時捲回開頭，避免沿用上一張譜的捲動位置而看到空白
        # （音符其實已載入，只是視窗停在時間軸上沒有音符的區段）。
        self.scroll_to_top()
        self.update()
        self._emit_status()

    def scroll_to_top(self) -> None:
        """把捲動視窗移到譜面開頭（第一顆音符所在處）。"""
        self.window_start_unit = self._min_unit
        self._clamp_window_start()
        self.update()
        self._emit_status()

    def toggle_time_uniform(self, enabled: bool) -> None:
        """切換時間均分模式。"""
        self.set_view_mode('time' if enabled else 'measure')

    @property
    def view_mode(self) -> str:
        if self.pitch_mode:
            return 'pitch'
        if self.time_uniform:
            return 'time'
        return 'measure'

    def set_view_mode(self, mode: str) -> None:
        mode = str(mode or 'measure').lower()
        if mode not in {'measure', 'time', 'pitch'}:
            mode = 'measure'
        # 還沒轉譜的 MIDI 只有音高檢視有意義（其他檢視的鍵道位置是假的）。
        # 鎖在這裡而不是只鎖在工具列，因為切換的入口不只一個：快捷鍵、
        # 時間均分開關、分割格子同步都會走到這個函式。
        if mode != 'pitch' and getattr(self.model, 'midi_unarranged', False):
            mode = 'pitch'
            # 不要默默擋掉——發訊號讓主視窗跳出「要不要轉譜」
            self.arrange_required.emit()

        prev_time_uniform = self.time_uniform
        self.pitch_mode = (mode == 'pitch')
        self.time_uniform = (mode in {'time', 'pitch'})
        if self.pitch_mode and self.alloc_active:
            self.cancel_alloc_section()
        if self.time_uniform and not prev_time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            we_ms = self.mapper.unit_to_ms(self.window_start_unit + self.window_size_unit)
            self._time_uniform_span_ms = max(1.0, float(we_ms - ws_ms))
            self._sync_time_uniform_window_units()
        elif self.time_uniform:
            self._sync_time_uniform_window_units()
        self.update()
        self._emit_status()

    def cycle_view_mode(self) -> str:
        next_mode = {
            'measure': 'time',
            'time': 'pitch',
            'pitch': 'measure',
        }[self.view_mode]
        self.set_view_mode(next_mode)
        return next_mode

    def _sync_time_uniform_window_units(self) -> None:
        """依固定 ms span 回填當前對應的 unit 視窗寬，供邊界/狀態使用。"""
        if not self.time_uniform:
            return
        ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
        target_we_ms = ws_ms + max(1.0, self._time_uniform_span_ms)
        we_u = self.mapper.ms_to_unit(target_we_ms)
        new_units = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS, float(we_u - self.window_start_unit)))
        self.window_size_unit = new_units

    def toggle_preview_mode(self, enabled: bool) -> None:
        """啟用/停用圖片預覽模式（覆蓋編輯畫面）。"""
        self.preview_mode = enabled
        if enabled:
            self.selected.clear()
            self.alloc_active = False
            self.selection_changed.emit(0)
        self.update()

    def rebuild_mapper(self) -> None:
        """BPM / beat_data 改變後重建 TimeMapper。"""
        self.mapper.build(
            self.model.get_beat_entries(),
            self.model.bpm,
            self.model.music_end_ms,
            self.model.beats_per_bar,
        )
        self._update_unit_bounds()
        self.update()
        self._emit_status()

    # ── 播放相關 ──────────────────────────────────────────────────────

    def set_judge_line(self, ms: Optional[float]) -> None:
        """更新判定時刻，並把視窗捲到讓它落在鍵盤上緣。

        判定線的位置只有一條規則：**永遠是鍵盤上緣**（見 `_judge_py`）。所以
        「線畫在哪」不是一個選項，要動的是視窗——把 `ms` 那一刻捲到那條線上。
        以前這裡另外有一套「非跟隨模式時固定在視窗底部 10%」，那是比例位置，
        和固定像素高度的鍵盤對不齊，於是判定線會浮在鍵盤上方；已整套移除。
        """
        self._judge_ms = ms
        if ms is not None:
            self.follow_to_ms(float(ms))
        self.update()

    def _judge_py(self) -> int:
        """判定線的 y —— 就是鍵盤上緣，沒有其他情況。

        「音符碰到鍵盤頂端＝判定的瞬間」這個視覺約定要成立，判定線、鍵盤上緣、
        `_judge_ms` 換算出來的位置就必須是同一個 y。這裡是唯一的定義來源。
        """
        return self._keyboard_top_py()

    def _judge_fraction(self) -> float:
        """判定線離視窗底部的比例——由 `_judge_py` 反推，不要自己另外算。"""
        h = max(1, self.height())
        return min(0.9, max(0.02, (h - self._judge_py()) / float(h)))

    def follow_to_ms(self, ms: float) -> None:
        """把 `ms` 那一刻捲到判定線（＝鍵盤上緣）。"""
        frac = self._judge_fraction()
        if self.time_uniform:
            # 時間均分：以 ms 視窗跟隨，保持播放視覺等速
            span_ms = max(1.0, float(self._time_uniform_span_ms or 1.0))
            desired_ws_ms = float(ms) - span_ms * frac
            new_ws = self.mapper.ms_to_unit(desired_ws_ms)
            # 和非均分分支一致：follow 只擋住「往前捲進負時間」，不擋往後。
            # 之前這裡呼叫 _clamp_window_start()——那是給手動捲動用的嚴格夾限，
            # 會把視窗釘死在起點，判定時刻整首卡在 1400ms 不動，和小節模式那格
            # 差到將近一分鐘。
            lo = min(-PRE_ROLL_UNITS, self._min_unit, 0.0)
            self.window_start_unit = max(lo, new_ws)
            self._sync_time_uniform_window_units()
        else:
            cur_unit = self.mapper.ms_to_unit(ms)
            # 同上：比例由鍵盤高度換算，不再寫死 10%
            target_rel = self.window_size_unit * frac
            new_ws = cur_unit - target_rel
            # follow 模式只限制往前（不進入負時間），不限制往後
            lo = min(-PRE_ROLL_UNITS, self._min_unit, 0.0)
            self.window_start_unit = max(lo, new_ws)
        self.update()

    # ── 選取 ──────────────────────────────────────────────────────────

    def select_all(self) -> None:
        # 手別篩選開著時只選得到正在編的那一手——另一手是參考影子，全選把它
        # 一起選進來就等於篩選沒作用。
        self.selected = {n.idx for n in self.model.notes if not self._is_ghost(n)}
        self.update()
        self.selection_changed.emit(len(self.selected))

    def deselect_all(self) -> None:
        self.selected.clear()
        self._sel_cells.clear()
        self.update()
        self.selection_changed.emit(0)

    # ── 編輯操作 ──────────────────────────────────────────────────────

    def delete_selected(self) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        self.model.notes_tree = [n for n in self.model.notes_tree
                                  if n.idx not in self.selected]
        self.model.rebuild_display_cache()
        self.selected.clear()
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(0)

    def shift_selected_keys(self, delta: int, push: bool = True) -> None:
        if not self.selected or self.alloc_active:
            return
        if push:
            self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            width = n.max_key - n.min_key
            new_min = n.min_key + delta
            new_max = n.max_key + delta
            if new_min < 0:
                new_min = 0; new_max = width
            if new_max >= TOTAL_GAME_KEYS:
                new_max = TOTAL_GAME_KEYS - 1
                new_min = max(0, new_max - width)
            actual = new_min - n.min_key
            n.min_key, n.max_key = new_min, new_max
            # trill：整顆平移時，內部 mesh cell 鍵道同步平移
            if note_is_trill(n.note_type) and actual:
                shift_trill_cells(n, actual)
        # 鍵位移動不改變時間順序，不需要 rebuild_display_cache，直接重繪
        self.update()
        self.note_edited.emit()

    def shift_selected_time(self, delta_ms: int, push: bool = True) -> None:
        if not self.selected or self.alloc_active:
            return
        if push:
            self.model.push_history()
        # 用物件參考保存選取集，避免 rebuild 後 idx 重編导致選取失效
        sel_notes = {n for n in self.model.notes_tree if n.idx in self.selected}
        for n in sel_notes:
            n.start = max(0, n.start + delta_ms)
            n.end   = max(n.start + 1, n.end + delta_ms)
            n.gate  = n.end - n.start
        self.model.rebuild_display_cache()
        self.selected = {n.idx for n in sel_notes}
        self.update()
        self.note_edited.emit()

    def shift_selected_by_beats(self, beats: float, push: bool = True) -> int:
        """把選取音符整顆沿時間軸平移 `beats` 拍（正=延後、負=提前），長度不變。

        每顆音符用**它自己所在小節的 BPM** 換算拍長（`_ms_per_beat_at`），所以
        變速譜上「往後推一個八分音符」推的是該處真正的八分音符，不是整首用同
        一個平均值硬算。和弦（start 相同）換算出來的量也相同，不會被拆開。

        回傳實際被移動的音符數。
        """
        if not self.selected or self.alloc_active or not beats:
            return 0
        sel_notes = [n for n in self.model.notes_tree if n.idx in self.selected]
        if not sel_notes:
            return 0

        # 先算好位移量再套用：邊算邊改的話，第二顆之後查到的 BPM 會是移動後的
        # 位置，同一批選取會被算成不同的拍長。
        deltas = {
            id(n): int(round(self._ms_per_beat_at(float(n.start)) * float(beats)))
            for n in sel_notes
        }
        if not any(deltas.values()):
            return 0

        was_dirty = self.model.dirty
        if push:
            self.model.push_history()

        moved = 0
        for n in sel_notes:
            delta = deltas[id(n)]
            if not delta:
                continue
            length = max(1, int(n.end) - int(n.start))
            n.start = max(0, int(n.start) + delta)
            n.end = n.start + length
            n.gate = length
            moved += 1

        if moved:
            self.model.rebuild_display_cache()
            self.selected = {n.idx for n in sel_notes}
            self.update()
            self.note_edited.emit()
        elif push and self.model.undo_stack:
            self.model.undo_stack.pop()
            self.model.dirty = was_dirty
        return moved

    def shift_selected_by_32nd(self, direction: int, push: bool = True) -> None:
        if not self.selected:
            return
        bpm  = self.model.bpm if self.model.bpm > 0 else 120.0
        step = int(round(60000.0 / bpm / 8.0 * direction))

        anchor = min(
            (n for n in self.model.notes_tree if n.idx in self.selected),
            key=lambda n: n.start,
        )
        unit_before = self.mapper.ms_to_unit(float(anchor.start))

        self.shift_selected_time(step, push=push)

        unit_after = self.mapper.ms_to_unit(float(anchor.start))

        self.window_start_unit += (unit_after - unit_before)
        self._clamp_window_start()
        self.update()

    def selection_time_bounds(self) -> Optional[Dict[str, int]]:
        """回傳目前選取音符的 start/end 最早最晚值，供對齊對話框自動填入。"""
        sel_notes = [n for n in self.model.notes_tree if n.idx in self.selected]
        if not sel_notes:
            return None
        return {
            'min_start': min(int(n.start) for n in sel_notes),
            'max_start': max(int(n.start) for n in sel_notes),
            'min_end':   min(int(n.end)   for n in sel_notes),
            'max_end':   max(int(n.end)   for n in sel_notes),
        }

    def align_selected_edge(self, target: str, value: int, push: bool = True) -> int:
        """把所有選取音符的 start 或 end 對齊到同一個絕對值（縮放，固定另一端）。

        target=='start'：設定 start，保持 end 不動（改長條/音符的頭）。
        target=='end'  ：設定 end，保持 start 不動（改尾端）。
        回傳實際被修改的音符數。
        """
        if not self.selected or self.alloc_active:
            return 0
        if target not in ('start', 'end'):
            return 0
        value = max(0, int(value))
        # 以物件參考保存選取集，避免 rebuild 後 idx 重編導致選取失效
        sel_notes = [n for n in self.model.notes_tree if n.idx in self.selected]
        if not sel_notes:
            return 0

        was_dirty = self.model.dirty
        if push:
            self.model.push_history()
        changed = self.model.align_notes_edge(sel_notes, target, value)

        if changed:
            self.model.rebuild_display_cache()
            self.selected = {n.idx for n in sel_notes}
            self.update()
            self.note_edited.emit()
        elif push and self.model.undo_stack:
            # 沒有任何變更 → 撤掉剛剛壓入的歷史紀錄並還原 dirty
            self.model.undo_stack.pop()
            self.model.dirty = was_dirty
        return changed

    # ── 長押長度修整（threshold 分級）──────────────────────────────
    def _ms_per_beat_at(self, ms: float) -> float:
        """回傳指定 ms 位置一拍（四分音符）的毫秒長度，吃小節 BPM / 變速。"""
        bpm = 0.0
        try:
            midx = self.model.get_measure_at_ms(float(ms))
            bpm = float(self.model.get_measure_bpm(midx))
        except Exception:
            bpm = 0.0
        if bpm <= 0:
            bpm = float(self.model.bpm) if self.model.bpm > 0 else 120.0
        return 60000.0 / bpm

    def _note_dur_beats(self, n: GNote) -> float:
        """音符時長換算成拍數（依該位置 BPM，與變速無關）。"""
        mpb = self._ms_per_beat_at(float(n.start))
        if mpb <= 0:
            return 0.0
        return (float(n.end) - float(n.start)) / mpb

    def apply_hold_length_fix(self, params: dict) -> dict:
        """依分級門檻修整長押長度。回傳統計 {'tapped', 'trimmed', 'unchanged', 'total'}。

        三段（門檻為音符值/拍數，吃變速）：
          - 時長 < tap 門檻      → 轉 Tap：只清 LONG_BIT（保留 skin），長度不變。
          - tap 門檻 ~ 長押門檻   → 短 Hold：長度按比例砍 end = start + gate×ratio。
          - 時長 >= 長押門檻      → 長 Hold：尾端前移固定 ms，end -= advance_ms。

        params 見 HoldLengthDialog.params()。scope='selected' 只處理選取音符，
        否則處理整個譜面。只動純長押（見 hold_fix_candidate）。

        分級砍完之後還會再套一次「同手最小間隔」（`tail_gap_ms`，預設 0 = 不套）：
        長 hold 的固定前移量不管下一顆音在哪，砍完仍可能壓到同手的下一顆；
        補這一刀之後尾端保證 <= 同手（跨 track）下一顆最早起音 − gap。
        """
        stats = {'tapped': 0, 'trimmed': 0, 'unchanged': 0, 'total': 0, 'gapped': 0}
        if self.alloc_active:
            return stats

        tap_th = float(params.get('tap_th_beats', 0.5))
        hold_th = max(float(params.get('hold_th_beats', 1.0)), tap_th)
        short_ratio = max(0.05, min(1.0, float(params.get('short_ratio', 0.6))))
        advance_ms = max(0, int(params.get('long_advance_ms', 40)))
        tail_gap_ms = max(0, int(params.get('tail_gap_ms', 0)))
        tail_only_conflicts = bool(params.get('tail_only_conflicts', True))
        scope = params.get('scope', 'all')

        if scope == 'selected':
            pool = [n for n in self.model.notes_tree if n.idx in self.selected]
        else:
            pool = list(self.model.notes_tree)
        targets = [n for n in pool if hold_fix_candidate(int(n.note_type))]
        if not targets:
            return stats

        was_dirty = self.model.dirty
        self.model.push_history()

        for n in targets:
            stats['total'] += 1
            start = int(n.start)
            end = int(n.end)
            action = classify_hold_length(self._note_dur_beats(n), tap_th, hold_th)

            if action == 'tap':
                # 轉 Tap：只清 LONG_BIT（保留 skin 等其它位元），長度不變。
                n.note_type = int(n.note_type) & ~LONG_BIT
                stats['tapped'] += 1
                continue

            if action == 'mid':      # 短 Hold：比例砍
                new_end = start + int(round((end - start) * short_ratio))
            else:                     # 長 Hold：尾端前移固定 ms
                new_end = end - advance_ms
            new_end = max(start + 1, new_end)

            if new_end != end:
                n.end = new_end
                n.gate = new_end - start
                stats['trimmed'] += 1
            else:
                stats['unchanged'] += 1

        # 補刀：同手（跨 track）最小間隔。時間軸取自整份譜面，但只裁 targets。
        if tail_gap_ms > 0:
            stats['gapped'] = self.model.enforce_hold_tail_gap(
                tail_gap_ms, targets, only_conflicts=tail_only_conflicts)

        changed = stats['tapped'] + stats['trimmed'] + stats['gapped']
        if changed:
            sel_notes = [n for n in self.model.notes_tree if n.idx in self.selected]
            self.model.rebuild_display_cache()
            self.selected = {n.idx for n in sel_notes}
            self.update()
            self.note_edited.emit()
        elif self.model.undo_stack:
            self.model.undo_stack.pop()
            self.model.dirty = was_dirty
        return stats

    def set_type_selected(self, t: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.note_type = t
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def set_hand_selected(self, hand: int) -> None:
        """把選取的音符改成左手／右手。所有檢視模式都能用，音高模式也一樣。

        MIDI 模式下順便把音軌搬過去：那個模式的顏色是照聲部(channel, track)
        上的，而且自動排譜會照音軌重新分手。只改 `hand` 的話畫面完全沒反應、
        排完譜又被改回去，看起來就像「音高模式不能改左右手」。
        """
        if not self.selected or self.alloc_active:
            return
        hand = 1 if int(hand) == 1 else 0
        picked = [n for n in self.model.notes_tree if n.idx in self.selected]
        if not picked:
            return
        self.model.push_history()
        track = channel = None
        if self.model.is_midi_mode():
            track = self.model.midi_track_for_hand(hand, picked)
            channel = self.model._default_midi_channel_for_track(track)
        for n in picked:
            n.hand = hand
            if track is not None:
                n.track = track
                n.channel = channel
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()
        self.status_changed.emit(
            '%d 顆音符改成%s' % (len(picked), '左手' if hand else '右手'))

    def chain_slides_selected(self) -> None:
        """把選取的音符全設成 slide（type4）並串成鏈。

        依「手（hand）」分組，每組再按 start 時間排序後前後相連：
        param1=前一顆 index、param2=下一顆 index，端點填 -1。
        會為缺少原始 index 的音符指派唯一 index，確保存檔後鏈結不斷。
        """
        if not self.selected or self.alloc_active:
            return
        sel = [n for n in self.model.notes_tree if n.idx in self.selected]
        if len(sel) < 2:
            return
        self.model.push_history()

        # 蒐集現有 note_index，供指派唯一值
        used = {
            int(n.note_index)
            for n in self.model.notes_tree
            if getattr(n, 'note_index', None) is not None
        }
        # 從 1 開始：param2 == 0 在格式上代表「未設定」，index 0 會讓鏈結
        # 被誤判成未串鏈而觸發推測連線。
        next_idx = (max(used) + 1) if used else 1
        next_idx = max(1, next_idx)

        for n in sel:
            n.note_type = 4
            if n.note_index is None:
                while next_idx in used:
                    next_idx += 1
                n.note_index = next_idx
                used.add(next_idx)
                next_idx += 1

        # 依手分組並按時間串鏈
        groups: Dict[int, List[GNote]] = {}
        for n in sel:
            groups.setdefault(int(n.hand), []).append(n)
        for notes in groups.values():
            notes.sort(key=lambda g: (int(g.start), int(g.min_key)))
            for i, n in enumerate(notes):
                n.param1 = notes[i - 1].note_index if i > 0 else -1
                n.param2 = notes[i + 1].note_index if i < len(notes) - 1 else -1
                n.param3 = 0

        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def unchain_slides_selected(self) -> None:
        """把選取的滑鍵還原成一般 tap（note_type=0），並清除鏈結參數。"""
        if not self.selected or self.alloc_active:
            return
        slides = [n for n in self.model.notes_tree
                  if n.idx in self.selected and note_is_slide(n.note_type)]
        if not slides:
            return
        self.model.push_history()
        for n in slides:
            n.note_type = 0
            n.param1 = n.param2 = n.param3 = 0
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def pack_trill_selected(self) -> None:
        """把選取的音符打包成 trill（依手分組，每組一顆 trill）。
        每顆來源音符成為 trill 的一個 sub_note（含還原資訊）。"""
        if not self.selected or self.alloc_active:
            return
        sel = [n for n in self.model.notes_tree if n.idx in self.selected]
        if len(sel) < 2:
            return
        self.model.push_history()

        kept = [n for n in self.model.notes_tree if n.idx not in self.selected]
        groups: Dict[int, List[GNote]] = {}
        for n in sel:
            groups.setdefault(int(n.hand), []).append(n)
        trills = [make_trill_from_notes(g, hand) for hand, g in groups.items()]
        trills = [t for t in trills if t is not None]

        self.model.notes_tree = kept + trills
        self.model.rebuild_display_cache()
        self.selected = {t.idx for t in trills}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def unpack_trill_selected(self) -> None:
        """把選取的 trill 解開還原成一組音符。"""
        if not self.selected or self.alloc_active:
            return
        trills = [n for n in self.model.notes_tree
                  if n.idx in self.selected and note_is_trill(n.note_type)]
        if not trills:
            return
        self.model.push_history()

        trill_ids = {id(t) for t in trills}
        kept = [n for n in self.model.notes_tree if id(n) not in trill_ids]
        restored: List[GNote] = []
        for t in trills:
            restored.extend(explode_trill(t))

        self.model.notes_tree = kept + restored
        self.model.rebuild_display_cache()
        self.selected = {n.idx for n in restored}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def set_width_selected(self, target_width: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            new_max = min(n.min_key + target_width - 1, TOTAL_GAME_KEYS - 1)
            n.max_key = new_max
            # trill：寬度改變後，把 mesh cell 依比例重排回新範圍內
            if note_is_trill(n.note_type):
                refit_trill_cells(n)
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def shift_selected_pitch(self, delta: int, push: bool = True, sync_keys: bool = False) -> None:
        if not self.selected or self.alloc_active:
            return
        if push:
            self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected and n.pitch is not None:
                hi = PITCH_MIDI_MAX if sync_keys else 127
                lo = PITCH_MIDI_MIN if sync_keys else 0
                n.pitch = max(lo, min(hi, n.pitch + delta))
                if sync_keys:
                    self._sync_note_keys_to_pitch(n)
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def set_channel_selected(self, channel: int) -> None:
        if not self.selected or self.alloc_active:
            return
        channel = max(0, min(15, int(channel)))
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.channel = channel
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def delete_selected_tracks(self) -> None:
        if not self.selected or self.alloc_active or not self.model.is_midi_mode():
            return
        tracks = {
            int(n.track) for n in self.model.notes_tree
            if n.idx in self.selected and n.track is not None
        }
        if not tracks:
            return
        self.model.push_history()
        self.model.delete_midi_tracks(tracks)
        self.selected.clear()
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(0)

    def delete_selected_channel(self, channel: int) -> None:
        if not self.selected or self.alloc_active or not self.model.is_midi_mode():
            return
        channel = max(0, min(15, int(channel)))
        self.model.push_history()
        self.model.notes_tree = [
            n for n in self.model.notes_tree
            if not (n.idx in self.selected and n.channel is not None and int(n.channel) == channel)
        ]
        self.model.rebuild_display_cache()
        self.selected.clear()
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(0)

    def _channel_icon(self, channel: int) -> QIcon:
        pix = QPixmap(14, 14)
        pix.fill(Qt.transparent)
        qp = QPainter(pix)
        qp.setPen(Qt.black)
        qp.setBrush(QBrush(self._channel_base_color(int(channel))))
        qp.drawRect(1, 1, 12, 12)
        qp.end()
        return QIcon(pix)

    def set_length_beats_selected(self, beats: float) -> None:
        """將所有已選音符的時長設定為指定拍數（依目前 BPM 轉算 ms）。"""
        if not self.selected or self.alloc_active:
            return
        beat_ms = 60000.0 / max(1.0, self.model.bpm)
        new_len = max(1, int(round(beats * beat_ms)))
        self.model.push_history()
        for n in self.model.notes_tree:
            if n.idx in self.selected:
                n.end  = n.start + new_len
                n.gate = new_len
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def duplicate_selected(self) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        new_notes = []
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            clone = n.clone(len(self.model.notes_tree) + len(new_notes))
            clone.min_key = min(TOTAL_GAME_KEYS - 1, clone.min_key + 1)
            clone.max_key = min(TOTAL_GAME_KEYS - 1, clone.max_key + 1)
            new_notes.append(clone)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()

    def duplicate_with_offset(self, offset_ms: int) -> None:
        if not self.selected or self.alloc_active:
            return
        self.model.push_history()
        prev = len(self.model.notes_tree)
        new_notes: List[GNote] = []
        for n in self.model.notes_tree:
            if n.idx not in self.selected:
                continue
            clone = n.clone(prev + len(new_notes))
            dur = max(1, clone.end - clone.start)
            clone.start = max(0, clone.start + offset_ms)
            clone.end   = clone.start + dur
            clone.gate  = dur
            new_notes.append(clone)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        # rebuild_display_cache 會重新編 idx，用物件參考取新 idx 才正確
        self.selected = {n.idx for n in new_notes}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def copy_to_clipboard(self) -> None:
        if not self.selected:
            return
        nodes = sorted(
            [n for n in self.model.notes_tree if n.idx in self.selected],
            key=lambda n: n.start,
        )
        if not nodes:
            return
        base = nodes[0].start
        self.clipboard = [{
            'rel_start': n.start - base,
            'rel_end':   n.end   - base,
            'min_key':   n.min_key,
            'max_key':   n.max_key,
            'note_type': n.note_type,
            'hand':      n.hand,
            'pitch':     n.pitch,
            'track':     n.track,
            'velocity':  n.velocity,
            'channel':   n.channel,
            'off_velocity': n.off_velocity,
            'gate':      n.end - n.start,
        } for n in nodes]

    def paste_from_clipboard(self) -> None:
        if not self.clipboard or self.alloc_active:
            return
        if self._last_mouse_unit is not None:
            base_ms = self.mapper.unit_to_ms(self._last_mouse_unit)
        else:
            base_ms = self.mapper.unit_to_ms(self.window_start_unit)
        base_ms = max(0.0, base_ms)
        # Snap paste position to measure start (小節) when beat entries exist
        beats = self.model.get_beat_entries()
        if beats:
            try:
                epb = self.model.entries_per_bar
                measure_idx = self.model.get_measure_at_ms(base_ms)
                entry_idx = measure_idx * max(1, epb)
                if 0 <= entry_idx < len(beats):
                    base_ms = float(beats[entry_idx][1])
            except Exception:
                pass
        self.model.push_history()
        prev = len(self.model.notes_tree)
        new_notes: List[GNote] = []
        for d in self.clipboard:
            n = GNote(None, prev + len(new_notes))
            n.start     = max(0, int(base_ms + d['rel_start']))
            n.end       = max(n.start + 1, int(base_ms + d['rel_end']))
            n.gate      = n.end - n.start
            n.min_key   = d['min_key']
            n.max_key   = d['max_key']
            n.note_type = d['note_type']
            n.hand      = d['hand']
            n.pitch     = d['pitch']
            n.track     = d['track']
            n.velocity  = d.get('velocity')
            n.channel   = d.get('channel')
            n.off_velocity = d.get('off_velocity')
            new_notes.append(n)
        self.model.notes_tree.extend(new_notes)
        self.model.rebuild_display_cache()
        # rebuild_display_cache 會重新編 idx，用物件參考取新 idx 才正確
        self.selected = {n.idx for n in new_notes}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(self.selected))

    def undo(self) -> None:
        if self.model.undo():
            self.selected.clear()
            # Undo may change beat timings / time signatures; keep viewport mapping in sync.
            self.rebuild_mapper()
            self._update_unit_bounds()
            self.update()
            self.note_edited.emit()
            self.selection_changed.emit(0)
            self._emit_status()

    # ── 視窗捲動/縮放 ─────────────────────────────────────────────────

    def scroll_by(self, delta_units: float) -> None:
        self.window_start_unit += delta_units
        self._clamp_window_start()
        self.update()
        self._emit_status()

    def zoom(self, factor: float) -> None:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            old_span = max(1.0, float(self._time_uniform_span_ms or 1.0))
            old_center_ms = ws_ms + old_span * 0.5
            new_span = max(50.0, min(600000.0, old_span * factor))
            self._time_uniform_span_ms = new_span

            if self._judge_ms is not None:
                desired_ws_ms = float(self._judge_ms) - new_span * 0.10
            else:
                desired_ws_ms = old_center_ms - new_span * 0.5
            self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
            self._sync_time_uniform_window_units()
            self._clamp_window_start()
            self.update()
            self._emit_status()
            return

        old_center = self.window_start_unit + self.window_size_unit * 0.5
        new_size = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS,
                       self.window_size_unit * factor))
        self.window_size_unit = new_size
        # 播放中：以 judge line 重新對齊（底部 10%），維持 follow 效果
        if self._judge_ms is not None:
            cur_unit = self.mapper.ms_to_unit(self._judge_ms)
            target_rel = new_size * 0.10
            self.window_start_unit = cur_unit - target_rel
        else:
            # 非播放：以原視窗中心做縮放基準
            self.window_start_unit = old_center - new_size * 0.5
        self._clamp_window_start()
        self.update()
        self._emit_status()

    # ── Alloc Section ─────────────────────────────────────────────────

    def smart_sort_selected(self) -> int:
        """簡化版智慧排序：依**音程**把選取音符排好鍵道，直接套用、不需拖曳。

        走的是智慧寫譜那條路的簡化版（`interval_sort_notes`）：只做音高 → 鍵道
        位置這件事，不重新分左右手、不改音符類型、不動時間。

        和「基本排序」（Alloc Section）差在間距怎麼決定：基本排序是依**音高排名
        等距**分配，小二度和大七度會被排成一樣寬；這裡是間隔多大就排多開。

        回傳被移動的音符數。
        """
        from .smart_chart import SmartChartSettings, interval_sort_notes

        if self.alloc_active:
            return 0
        if self.pitch_mode:
            self._drag_status = '音高模式下不需要排序（本來就照音高排）'
            self._emit_status()
            return 0
        sel_notes = [n for n in self.model.notes_tree if n.idx in self.selected]
        pitched = [n for n in sel_notes if n.pitch is not None]
        if len(pitched) < 2:
            self._drag_status = '智慧排序：至少要選兩顆有音高的音符'
            self._emit_status()
            return 0

        was_dirty = self.model.dirty
        self.model.push_history()
        beat_ms = 60000.0 / max(1.0, float(self.model.bpm or 120.0))
        report = interval_sort_notes(
            pitched,
            SmartChartSettings(beat_ms=beat_ms),
            lane_min=min(int(n.min_key) for n in pitched),
            lane_max=max(int(n.max_key) for n in pitched),
        )
        moved = int(report.get('moved', 0)) + int(report.get('narrowed', 0))
        if moved:
            self.model.rebuild_display_cache()
            self.selected = {n.idx for n in sel_notes}
            self.update()
            self.note_edited.emit()
            self._drag_status = '智慧排序：移動 %d、收窄 %d%s' % (
                report.get('moved', 0), report.get('narrowed', 0),
                '、%d 處放不下' % report['unresolved'] if report.get('unresolved') else '')
        else:
            if self.model.undo_stack:
                self.model.undo_stack.pop()
            self.model.dirty = was_dirty
            self._drag_status = '智慧排序：已經照音程排好了，沒有需要調整的'
        self._emit_status()
        return moved

    def start_alloc_section(self) -> None:
        if self.pitch_mode:
            self._drag_status = '音高模式下不支援 Alloc Section'
            self._emit_status()
            return
        if self.alloc_active:
            return
        # `self.selected` 存的是 display cache (`self.model.notes`) 的 idx。
        # 不能直接當成 notes_tree 的索引使用，必須把選取的 display idx 映射
        # 回 notes_tree 的實際位置（index）。否則會修改到錯誤的音符，造成
        # 譜面寬度/位置異常。
        locked_notes = [n for n in self.model.notes if n.idx in self.selected]
        locked_indices = [self.model.notes_tree.index(n) for n in locked_notes if n in self.model.notes_tree]
        locked_indices = sorted(locked_indices)
        if not locked_indices:
            return
        self.alloc_active = True
        self.alloc_locked = locked_indices
        self.alloc_orig = {}
        for i in locked_indices:
            n = self.model.notes_tree[i]
            self.alloc_orig[i] = (n.min_key, n.max_key, n.pitch)
        self.alloc_target_min = min(self.model.notes_tree[i].min_key for i in locked_indices)
        self.alloc_target_max = max(self.model.notes_tree[i].max_key for i in locked_indices)
        # Preserve overall chart key range so alloc won't change total width
        try:
            # Preserve left bound as existing content, but allow the right bound
            # to expand up to the full key range so alloc can reach the very
            # rightmost key. This addresses reports that alloc could not drag
            # to the extreme right.
            self._preserve_min_key = min(n.min_key for n in self.model.notes_tree)
            self._preserve_max_key = TOTAL_GAME_KEYS - 1
        except Exception:
            self._preserve_min_key = 0
            self._preserve_max_key = TOTAL_GAME_KEYS - 1
        starts = [self.mapper.ms_to_unit(float(self.model.notes_tree[i].start)) for i in locked_indices]
        ends   = [self.mapper.ms_to_unit(float(self.model.notes_tree[i].end))   for i in locked_indices]
        self.alloc_time_min_u = min(starts)
        self.alloc_time_max_u = max(ends)
        self._alloc_drag_edge = None
        self._apply_alloc_dist()
        self.update()
        self._drag_status = 'Alloc Section：拖曳紅框邊界。Enter 確認 / Esc 取消'
        self._emit_status()

    def resort_all_notes(self) -> None:
        """全譜重整：將所有音符依音高由左到右重新醒套鍵位，
        保留原始寬度與單個音符寬度，關鍵範圍跟原譜面相同。"""
        if not self.model.notes_tree:
            return
        self.model.push_history()
        notes = self.model.notes_tree

        # 範圍：維持原譜面的整體 min_key / max_key
        preserve_mn = min(n.min_key for n in notes)
        preserve_mx = max(n.max_key for n in notes)
        span = preserve_mx - preserve_mn

        # 對每個音符記錄原始寬度
        orig_w = {id(n): n.max_key - n.min_key for n in notes}

        # 依音高分組
        groups: dict = {}
        for n in notes:
            groups.setdefault(n.pitch, []).append(n)

        pitches_sorted = sorted(p for p in groups if p is not None)
        n_p = len(pitches_sorted)
        rank_frac = {p: (i / max(n_p - 1, 1)) for i, p in enumerate(pitches_sorted)}
        if n_p == 1:
            rank_frac[pitches_sorted[0]] = 0.5

        for p, group_notes in groups.items():
            frac = rank_frac.get(p, 0.5) if p is not None else 0.5
            kpos = preserve_mn + int(round(frac * span))
            kpos = max(preserve_mn, min(preserve_mx, kpos))
            for n in group_notes:
                w = orig_w[id(n)]
                new_min = kpos
                new_max = kpos + w
                if new_max > TOTAL_GAME_KEYS - 1:
                    new_max = TOTAL_GAME_KEYS - 1
                    new_min = max(0, new_max - w)
                if new_min < 0:
                    new_min = 0
                    new_max = min(TOTAL_GAME_KEYS - 1, w)
                # clamp 到 preserve 範圍
                if new_min < preserve_mn:
                    new_min = preserve_mn
                    new_max = new_min + w
                if new_max > preserve_mx:
                    new_max = preserve_mx
                    new_min = new_max - w
                n.min_key = int(new_min)
                n.max_key = int(new_max)

        self.model.rebuild_display_cache()
        self._update_unit_bounds()   # 更新捲動上下界
        self.selected.clear()
        self.update()
        self.selection_changed.emit(0)
        self._emit_status()

    # ── 放置音符模式 ──────────────────────────────────────────────────

    def set_note_input_mode(self, enabled: bool) -> None:
        """開啟或關閉放置音符模式（點擊即可在拍子位置新增音符）。"""
        self._note_input_mode = enabled
        if enabled:
            self.alloc_active = False
            self._alloc_drag_edge = None
            from PyQt5.QtCore import Qt as _Qt
            self.setCursor(_Qt.CrossCursor)
        else:
            from PyQt5.QtCore import Qt as _Qt
            self.setCursor(_Qt.ArrowCursor)
            self._note_input_hover = None
            self._input_drag_note = None
        self.note_input_changed.emit(enabled)
        self._emit_status()

    def set_note_duration(self, beats: float) -> None:
        """設定放置音符模式的音符時值（單位：拍次）。"""
        self._note_duration_beats = max(1.0 / 64, float(beats))

    def set_note_input_hand(self, hand: int) -> None:
        """設定放置音符預設手（0=右 1=左）。"""
        self._note_input_hand = hand

    def set_note_input_width(self, width: int) -> None:
        """設定放置音符預設寬度（格數）。"""
        try:
            w = int(width)
        except Exception:
            return
        self._note_input_width = max(1, min(int(TOTAL_GAME_KEYS), w))

    def set_note_input_note_type(self, note_type: int) -> None:
        """設定放置音符預設類型（0=tap,1=soft,2=long,3=staccato,4=slide,64=trill）。"""
        try:
            t = int(note_type)
        except Exception:
            return
        # 只接受已知類型（含官方 bitmask 的 trill=64）
        self._note_input_note_type = t if t in (0, 1, 2, 3, 4, 64) else 0

    # ------------------------------------------------------------------

    def _display_key_count(self) -> int:
        return PITCH_GRID_KEYS if self.pitch_mode else TOTAL_GAME_KEYS

    def _pedal_lane_px(self) -> float:
        """音高模式左側踏板欄的寬度；其他模式與疊層上層皆為 0。

        這是**保留出來**的空間，不是疊上去的——所有音高像素換算都減掉它，
        鍵盤與音符欄位才不會被踏板欄切掉最低的幾顆鍵。
        """
        if not self.pitch_mode or self.overlay_role == 'top':
            return 0.0
        # 每個像素換算都會呼叫到這裡（密集譜上每幀好幾百次），設定值一幀讀
        # 一次就好，不要每次都進 settings。
        return PEDAL_LANE_PX if self._lane_flags()[0] else 0.0

    def _key_area_px(self) -> float:
        """扣掉左右欄位之後，真正給琴鍵/音符用的寬度。"""
        return max(1.0, self.width() - self._left_gutter_px() - self._right_gutter_px())

    def _dyn_lane_px(self) -> float:
        """強弱欄的寬度（左右各一條）；非音高模式或關掉時是 0。"""
        if not self.pitch_mode or self.overlay_role == 'top':
            return 0.0
        return DYN_LANE_PX if self._lane_flags()[1] else 0.0

    def _lane_flags(self):
        """(踏板欄開著嗎, 強弱欄開著嗎)。同一幀內只讀一次設定。

        欄寬會被 `_key_area_px` 之類的像素換算反覆問到，直接讀 settings 的話
        每幀上千次 dict 查詢＋函式內 import，在密集譜上量得出來。快取靠
        `paintEvent` 開頭清掉，所以選單一改馬上就看得到。
        """
        flags = self._lane_flag_cache
        if flags is None:
            flags = self._lane_flag_cache = (
                _setting_on('pitch_pedal_lane', True),
                _setting_on('pitch_dynamics_lane', True),
            )
        return flags

    def _left_gutter_px(self) -> float:
        """畫面左側保留給欄位的總寬度：踏板欄 + 左手強弱欄。"""
        return self._pedal_lane_px() + self._dyn_lane_px()

    def _right_gutter_px(self) -> float:
        """畫面右側保留給右手強弱欄的寬度。"""
        return self._dyn_lane_px()

    def _key_span(self, key: int) -> tuple:
        """第 key 個欄位在畫面上的 (左, 右) 像素。

        音高模式下黑鍵和白鍵會重疊，沒辦法再用「累積左緣」表示，所以取用
        整段而不是兩次呼叫 `_display_key_to_px(i)`/`(i+1)`。
        """
        if self.pitch_mode:
            i = max(0, min(PITCH_GRID_KEYS - 1, int(key)))
            lo, hi = _PITCH_KEY_SPANS[i]
            gut = self._left_gutter_px()
            scale = self._key_area_px() / max(_PITCH_KEY_TOTAL, 1e-9)
            return gut + lo * scale, gut + hi * scale
        w = self.width() / max(self._display_key_count(), 1)
        return int(key) * w, (int(key) + 1) * w

    def _key_draw_span(self, key: int) -> tuple:
        """畫鍵盤用的鍵形範圍（黑鍵會壓在白鍵上面，和音符欄位不同）。"""
        i = max(0, min(PITCH_GRID_KEYS - 1, int(key)))
        lo, hi = _PITCH_KEY_DRAW[i]
        gut = self._left_gutter_px()
        scale = self._key_area_px() / max(_PITCH_KEY_TOTAL, 1e-9)
        return gut + lo * scale, gut + hi * scale

    def _display_key_to_px(self, key: float) -> float:
        if self.pitch_mode:
            i = int(math.floor(key))
            i = max(0, min(PITCH_GRID_KEYS - 1, i))
            lo, hi = _PITCH_KEY_SPANS[i]
            rel = lo + (key - i) * (hi - lo)
            gut = self._left_gutter_px()
            return gut + rel / max(_PITCH_KEY_TOTAL, 1e-9) * self._key_area_px()
        return key * self.width() / max(self._display_key_count(), 1)

    def _px_to_display_key(self, px: float) -> float:
        if self.pitch_mode:
            gut = self._left_gutter_px()
            rel = (px - gut) / max(self._key_area_px(), 1) * _PITCH_KEY_TOTAL
            # 黑鍵騎在白鍵交界上、畫在上層，所以先比對黑鍵才符合視覺
            for black in (True, False):
                for i in range(PITCH_GRID_KEYS):
                    if _is_black_pitch(PITCH_MIDI_MIN + i) != black:
                        continue
                    lo, hi = _PITCH_KEY_SPANS[i]
                    if lo <= rel < hi:
                        seg = hi - lo
                        return i + ((rel - lo) / seg if seg > 0 else 0.0)
            return float(PITCH_GRID_KEYS - 1) if rel > 0 else 0.0
        return px * self._display_key_count() / max(self.width(), 1)

    def _pitch_to_slot(self, pitch: int) -> int:
        return max(0, min(PITCH_GRID_KEYS - 1, int(round(pitch)) - PITCH_MIDI_MIN))

    def _slot_to_pitch(self, slot: float) -> int:
        return max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, int(slot) + PITCH_MIDI_MIN))

    # ── 調性高亮 / 鎖調 ──────────────────────────────────────────────
    def _active_key(self):
        """畫面高亮與鎖調用的調：使用者指定的優先，否則偵測；都沒有回 None。

        和 `pattern_key()` 的差別只在最後一步：那個要保證生得出音階所以會退回
        C 大調，這裡沒調性就該什麼都不標，硬標一個 C 大調是騙人的。

        偵測要掃全譜音高，一定要快取——這條路在每次重繪都會走到，不快取就是
        每幀 O(N)。任何編輯都會發 `note_edited`，接上去讓它自己失效。
        """
        if self._pattern_key_override is not None:
            return self._pattern_key_override
        cached = self._in_key_cache
        if cached is None:
            key = self.detect_chart_key()
            classes = frozenset(key.pitch_classes) if key is not None else None
            cached = self._in_key_cache = (key, classes)
        return cached[0]

    def _highlight_pitch_classes(self) -> Optional[frozenset]:
        """要標亮的音級集合；沒有調性資訊就回 None（＝不標）。"""
        if self._pattern_key_override is not None:
            return frozenset(self._pattern_key_override.pitch_classes)
        self._active_key()
        cached = self._in_key_cache
        return cached[1] if cached else None

    def _invalidate_key_cache(self) -> None:
        self._in_key_cache = None

    def _notes_in_window(self, lo_ms: float, hi_ms: float):
        """start 落在 [lo_ms, hi_ms] 的音符。`model.notes` 照 start 排序才成立。

        繪圖每幀都要挑「看得到的音符」，直接 for 全譜再 if 過濾是 O(全譜)；
        音符一多，那個成本就直接變成播放時的頓挫。
        """
        notes = self.model.notes
        if not notes:
            return []
        starts = self._note_start_index()
        lo = bisect_left(starts, float(lo_ms))
        hi = bisect_right(starts, float(hi_ms))
        return notes[lo:hi]

    def _note_start_index(self):
        """`model.notes` 的 start 陣列，供二分搜尋用。

        快取比對的是**那個 list 本身是不是同一個物件**，不是長度或內容雜湊。
        `rebuild_display_cache` 每次都指派一個新的 list，而任何會改到 start 的
        操作都得經過它（不然畫面根本不會更新），所以這個比對是精確的。
        用長度比對會漏掉「顆數沒變但時間改了」——那會讓音符整片消失，比配色
        不對嚴重得多。
        """
        notes = self.model.notes
        cached = self._note_start_cache
        if cached is None or cached[0] is not notes:
            cached = self._note_start_cache = (
                notes, [float(n.start) for n in notes])
        return cached[1]

    def _max_note_span_ms(self) -> float:
        """全譜最長的音符時長，當作「往回找多遠還可能有音在響」的界限。

        和 `_note_start_index` 綁同一個 list 身分，長度變不變都能抓到。
        """
        notes = self.model.notes
        cached = self._max_span_cache
        if cached is None or cached[0] is not notes:
            cached = self._max_span_cache = (notes, max(
                (float(n.end) - float(n.start) for n in notes), default=0.0))
        return cached[1]

    def _invalidate_chart_caches(self) -> None:
        """譜面內容變了 → 所有「掃全譜算出來」的快取一起丟掉。

        調性（音高分布）、強弱刻度（力度範圍）、聲部配色（channel/track 集合）
        都屬於這一類。
        """
        self._in_key_cache = None
        self._dyn_scale_cache = {}
        self._chan_color_key = None
        self._note_start_cache = None
        self._max_span_cache = None

    def _scale_highlight_on(self) -> bool:
        return self.pitch_mode and _setting_on('pitch_scale_highlight', True)

    def _scale_lock_on(self) -> bool:
        return self.pitch_mode and _setting_on('pitch_scale_lock', False)

    def _lock_pitch(self, pitch: int) -> int:
        """鎖調開著時把音高吸到調內；沒開或沒有調性就原樣回傳。

        只作用在滑鼠放置／音階起點。方向鍵的 ±1 半音刻意不吸——那是「我就是
        要這顆升記號」的手動微調，鎖調不該把唯一的逃生口也關掉。
        """
        if not self._scale_lock_on():
            return int(pitch)
        key = self._active_key()
        if key is None:
            return int(pitch)
        from .music_theory import snap_to_key
        return max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, snap_to_key(int(pitch), key)))

    # ── 量化 ─────────────────────────────────────────────────────────
    #: 量化格點：(顯示名, 拍數)。以四分音符 = 1 拍計。
    QUANTIZE_GRIDS = [
        ('二分音符',   2.0),
        ('四分音符',   1.0),
        ('八分音符',   0.5),
        ('八分三連',   1.0 / 3.0),
        ('16 分音符',  0.25),
        ('16 分三連',  1.0 / 6.0),
        ('32 分音符',  0.125),
    ]

    def quantize_notes(self, grid_beats: float, strength: float = 1.0,
                       whole_chart: bool = False, also_length: bool = False,
                       min_gate_beats: float = 0.0) -> int:
        """把音符的起點對齊到拍點格線，回傳改了幾顆。

        `strength` 0~1 是「往格線靠多少」：1.0 完全對齊，0.5 只走一半。MIDI 是
        人彈的，一次拉到 1.0 會把所有搖擺和呼吸都壓平，所以留這個旋鈕。

        格線寬度用 `_beat_in_units_at` 取當地的一拍，變速、變拍號的段落也對得
        上——直接用毫秒算的話，一首有轉速的曲子後半段會整個歪掉。

        `also_length` 連結束點一起量化（長押才有意義）；量化後長度至少留
        `min_gate_beats` 拍，免得原本很短的音被壓成 0。
        """
        if grid_beats <= 0:
            return 0
        strength = max(0.0, min(1.0, float(strength)))
        if strength <= 0.0:
            return 0
        targets = [n for n in self.model.notes_tree
                   if whole_chart or not self.selected or n.idx in self.selected]
        if not targets:
            return 0

        def pull(ms: float) -> float:
            unit = self.mapper.ms_to_unit(float(ms))
            snapped = self._snap_unit_to_duration(unit, grid_beats)
            return float(self.mapper.unit_to_ms(unit + (snapped - unit) * strength))

        plan = []
        for n in targets:
            start = pull(n.start)
            end = pull(n.end) if also_length else float(n.end) + (start - float(n.start))
            if also_length:
                unit = self.mapper.ms_to_unit(start)
                floor_ms = self.mapper.unit_to_ms(
                    unit + max(0.0, float(min_gate_beats)) * self._beat_in_units_at(unit))
                end = max(end, floor_ms, start + 1.0)
            new_start, new_end = int(round(start)), int(round(end))
            new_start = max(0, new_start)
            new_end = max(new_start + 1, new_end)
            if (new_start, new_end) != (int(n.start), int(n.end)):
                plan.append((n, new_start, new_end))
        if not plan:
            return 0

        self.model.push_history()
        for n, start, end in plan:
            n.start, n.end = start, end
            n.gate = end - start
        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self.update()
        self.note_edited.emit()
        return len(plan)

    def snap_selected_to_key(self, key=None) -> int:
        """把選取的音符吸到調內，回傳改了幾顆。沒選取就整譜。

        鎖調只管「之後放的音」，這個管「已經在譜上的音」——MIDI 轉過來的譜常有
        幾顆離調的錯音，一顆一顆改太慢。

        `key` 給 None 就用目前的調。呼叫端最好明確指定：自動偵測是照全譜音高
        分布算的，吸過一次之後分布就變了，關係大小調那種模稜兩可的譜會被判成
        另一個調，再按一次又把音移到別的地方去。主視窗那邊會在信心不足時先問
        過使用者，把當下的調釘住再傳進來。
        """
        if key is None:
            key = self._active_key()
        if key is None:
            self.status_changed.emit('沒有調性資訊，無法吸附')
            return 0
        from .music_theory import snap_to_key
        targets = [n for n in self.model.notes_tree
                   if n.pitch is not None
                   and (not self.selected or n.idx in self.selected)]
        moved = [(n, snap_to_key(int(n.pitch), key)) for n in targets]
        moved = [(n, p) for n, p in moved if p != int(n.pitch)]
        if not moved:
            self.status_changed.emit('%s：沒有離調的音符' % key.name())
            return 0
        self.model.push_history()
        for n, pitch in moved:
            n.pitch = max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, int(pitch)))
            if self.pitch_mode:
                self._sync_note_keys_to_pitch(n)
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()
        self.status_changed.emit('%s：%d 顆吸到調內' % (key.name(), len(moved)))
        return len(moved)

    def _display_pitch(self, n: GNote) -> int:
        raw_pitch = getattr(n, 'pitch', None)
        if raw_pitch is None:
            center = (float(n.min_key) + float(n.max_key) + 1.0) * 0.5
            frac = center / max(float(TOTAL_GAME_KEYS), 1.0)
            return max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, int(round(PITCH_MIDI_MIN + frac * (PITCH_GRID_KEYS - 1)))))
        return max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, int(round(raw_pitch))))

    def _pitch_to_lane_center(self, pitch: int) -> int:
        clamped = max(PITCH_MIDI_MIN, min(PITCH_MIDI_MAX, int(round(pitch))))
        frac = (clamped - PITCH_MIDI_MIN) / max(PITCH_GRID_KEYS - 1, 1)
        return int(round(frac * max(TOTAL_GAME_KEYS - 1, 0)))

    def _center_to_lane_range(self, center: int, width: int) -> Tuple[int, int]:
        width = max(1, min(TOTAL_GAME_KEYS, int(width)))
        span = width - 1
        min_key = int(center) - (span // 2)
        max_key = min_key + span
        if min_key < 0:
            min_key = 0
            max_key = span
        if max_key >= TOTAL_GAME_KEYS:
            max_key = TOTAL_GAME_KEYS - 1
            min_key = max(0, max_key - span)
        return min_key, max_key

    def _sync_note_keys_to_pitch(self, n: GNote, width: Optional[int] = None) -> None:
        if getattr(n, 'pitch', None) is None:
            return
        note_width = width if width is not None else max(1, int(n.max_key - n.min_key + 1))
        center = self._pitch_to_lane_center(int(n.pitch))
        n.min_key, n.max_key = self._center_to_lane_range(center, note_width)

    def _note_display_x_range(self, n: GNote) -> Tuple[float, float]:
        if self.pitch_mode:
            pitch = self._display_pitch(n)
            slot = self._pitch_to_slot(pitch)
            return self._key_span(slot)
        x1, _ = self._key_span(int(n.min_key))
        _, x2 = self._key_span(int(n.max_key))
        return x1, x2

    def _beat_in_units(self) -> float:
        """1 拍 = 幾個 unit。
        per-beat 格式（原始遊戲檔）：1 unit = 1 拍 → 回傳 1.0
        per-bar  格式（新增譜面）  ：1 unit = beats_per_bar 拍 → 回傳 1/bpb"""
        if self.model.root is None:
            return 1.0 / max(1, self.model.beats_per_bar)
        epb = self.model.entries_per_bar
        if epb <= 1:
            # per-bar：1 unit = beats_per_bar 拍
            return 1.0 / max(1, self.model.beats_per_bar)
        return 1.0  # per-beat：1 unit = 1 拍

    def _beat_in_units_at(self, unit: float) -> float:
        """回傳指定 unit 位置下 1 拍對應的 unit 長度。
        在小節均分模式下，會依該小節拍號動態變化（例如 15/4 比 4/4 更密）。"""
        # 時間均分已正常，沿用現行行為
        if self.time_uniform:
            return self._beat_in_units()
        try:
            ms = float(self.mapper.unit_to_ms(float(unit)))
            bpb = max(1, int(self.model.get_beats_per_bar_at_ms(ms)))
            return 1.0 / float(bpb)
        except Exception:
            return self._beat_in_units()

    def _snap_unit_to_duration(self, unit: float, duration_beats: float) -> float:
        """將 unit 吸附到最近的 duration_beats 倍數（拍次格線）。"""
        if duration_beats <= 0:
            return unit
        snap = duration_beats * self._beat_in_units_at(unit)
        return round(unit / snap) * snap

    def _infer_pitch_from_key(self, key_f: float) -> int:
        """依點擊鍵位，以全譜 alloc 映射推算最近音高。
        若譜面無任何音高資料，則直接線性映射到 1~88（鋼琴標準鍵數）。"""
        notes = self.model.notes_tree if (self.model and self.model.notes_tree) else []
        pitches = sorted(set(n.pitch for n in notes if n.pitch is not None))
        if not pitches:
            # 無參考音高：線性映射 key 0~TOTAL_GAME_KEYS 到 pitch 1~88
            frac = max(0.0, min(1.0, key_f / max(TOTAL_GAME_KEYS, 1)))
            return max(PITCH_MIDI_MIN, int(round(PITCH_MIDI_MIN + frac * (PITCH_GRID_KEYS - 1))))
        n_p = len(pitches)
        try:
            preserve_mn = min(n.min_key for n in notes)
            preserve_mx = max(n.max_key for n in notes)
        except Exception:
            preserve_mn = 0
            preserve_mx = TOTAL_GAME_KEYS - 1
        span = max(preserve_mx - preserve_mn, 1)
        frac = max(0.0, min(1.0, (key_f - preserve_mn) / span))
        idx  = int(round(frac * (n_p - 1)))
        return pitches[max(0, min(n_p - 1, idx))]

    # ── 輔助組合音符（音階 / 琶音）────────────────────────────────────

    # ── 音階輔助模式 ──────────────────────────────────────────────────

    PATTERN_MIN_COUNT = 1
    PATTERN_MAX_COUNT = 64

    def set_pattern_mode(self, enabled: bool) -> None:
        """開關音階輔助模式。和放置模式互斥——兩個都在等左鍵按下去。"""
        self._pattern_mode = bool(enabled)
        if enabled:
            self.set_note_input_mode(False)
            self.note_input_changed.emit(False)
            self.alloc_active = False
            self.setCursor(Qt.CrossCursor)
        else:
            self._pattern_drag = None
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_pattern_params(self, kind: Optional[str] = None,
                           direction: Optional[int] = None,
                           step_beats: Optional[float] = None,
                           key_override=None, use_auto_key: bool = False) -> None:
        """由工具列設定音型參數。`use_auto_key=True` 表示調性回到自動偵測。"""
        if kind is not None:
            self._pattern_kind = str(kind)
        if direction is not None:
            self._pattern_direction = int(direction)
        if step_beats is not None:
            self._pattern_step_beats = max(1.0 / 16, float(step_beats))
        if use_auto_key:
            self._pattern_key_override = None
        elif key_override is not None:
            self._pattern_key_override = key_override
        self.update()

    def pattern_key(self):
        """目前要用的調性：使用者指定的優先，否則從譜面（含 MIDI）偵測。

        生成音階一定要有個調可用，所以譜面完全沒音高時退回 C 大調。
        """
        key = self._active_key()
        if key is not None:
            return key
        from .music_theory import Key
        return Key(0, 'major')

    def _pattern_count_for(self, py: float) -> int:
        """按住的點到目前 y 之間放得下幾個音。

        用「時間」算而不是純像素：預覽音符就落在它們真正會去的位置，往上拖多遠
        就多幾顆，所見即所得。
        """
        drag = self._pattern_drag
        if drag is None:
            return self.PATTERN_MIN_COUNT
        start_unit = float(drag['start_unit'])
        step_units = self._pattern_step_beats * self._beat_in_units_at(start_unit)
        if step_units <= 0:
            return self.PATTERN_MIN_COUNT
        cur_unit = self._py_to_unit_abs(float(py))
        steps = int(round((cur_unit - start_unit) / step_units))
        return max(self.PATTERN_MIN_COUNT,
                   min(self.PATTERN_MAX_COUNT, steps + 1))

    @staticmethod
    def _clip_pattern_to_keyboard(pitches: List[int]) -> List[int]:
        """走到鋼琴範圍外就停手。

        琶音跨個幾組八度很快就超過 88 鍵的頂端；不切掉的話那些音會全部被夾到
        最高鍵，畫面上疊成一坨、聽起來也不對。音階跑到頂就該結束。
        """
        out: List[int] = []
        for pitch in pitches:
            if not (PITCH_MIDI_MIN <= int(pitch) <= PITCH_MIDI_MAX):
                break
            out.append(int(pitch))
        return out

    def _pattern_preview_pitches(self) -> List[int]:
        from .music_theory import build_pattern

        drag = self._pattern_drag
        if drag is None:
            return []
        return self._clip_pattern_to_keyboard(build_pattern(
            self._pattern_kind, drag['key'], int(drag['start_pitch']),
            int(drag['count']), self._pattern_direction))

    def _begin_pattern_drag(self, pos) -> None:
        if not self.model.has_chart():
            self.new_chart_requested.emit()
            return
        raw_unit = self._py_to_unit_abs(pos.y())
        snapped = self._snap_unit_to_duration(raw_unit, self._pattern_step_beats)
        display_key = self._px_to_display_key(pos.x())
        pitch = (self._lock_pitch(self._slot_to_pitch(display_key)) if self.pitch_mode
                 else self._infer_pitch_from_key(display_key))
        self._pattern_drag = {
            'start_unit': float(snapped),
            'start_ms': float(self.mapper.unit_to_ms(snapped)),
            'start_pitch': int(pitch),
            'key': self.pattern_key(),
            'count': self.PATTERN_MIN_COUNT,
        }
        self._update_pattern_drag(pos)

    def _update_pattern_drag(self, pos) -> None:
        drag = self._pattern_drag
        if drag is None:
            return
        drag['count'] = self._pattern_count_for(pos.y())
        pitches = self._pattern_preview_pitches()
        # 顯示實際用到的調：選了半音階這種指定音階時，主音是下筆的那個音，
        # 和上面偵測到的調性不一樣，要照實寫。
        from .music_theory import pattern_key
        used = pattern_key(self._pattern_kind, drag['key'],
                           int(drag['start_pitch']))
        self._drag_status = '%s ・ %s ・ %d 音' % (
            used.name(), self._pattern_kind_label(), len(pitches))
        self._emit_status()
        self.update()

    def _pattern_kind_label(self) -> str:
        from .music_theory import PATTERN_KIND_NAMES
        return PATTERN_KIND_NAMES.get(self._pattern_kind, self._pattern_kind)

    def _finish_pattern_drag(self) -> None:
        drag = self._pattern_drag
        self._pattern_drag = None
        if drag is None:
            return
        made = self.insert_pattern(
            self._pattern_kind, drag['key'], drag['start_pitch'],
            int(drag['count']), self._pattern_direction,
            drag['start_ms'], self._pattern_step_beats,
            self._note_input_hand, note_type=self._note_input_note_type)
        self._drag_status = ('插入 %d 顆（%s）' % (made, drag['key'].name())
                             if made else '')
        self._emit_status()
        self.update()

    def _draw_pattern_preview(self, qp: QPainter) -> None:
        """拖曳中的預覽：音符實際會落在哪裡就畫在哪裡。"""
        drag = self._pattern_drag
        if drag is None:
            return
        pitches = self._pattern_preview_pitches()
        if not pitches:
            return
        start_unit = float(drag['start_unit'])
        step_units = self._pattern_step_beats * self._beat_in_units_at(start_unit)
        qp.setRenderHint(QPainter.Antialiasing, True)
        for i, pitch in enumerate(pitches):
            unit = start_unit + step_units * i
            y_top = self._unit_to_py(unit + step_units * 0.9 - self.window_start_unit)
            y_bot = self._unit_to_py(unit - self.window_start_unit)
            if self.pitch_mode:
                x1, x2 = self._key_span(self._pitch_to_slot(pitch))
            else:
                center = self._pitch_to_lane_center(pitch)
                lo, hi = self._center_to_lane_range(center, self._note_input_width)
                x1, _ = self._key_span(lo)
                _, x2 = self._key_span(hi)
            rect = QRectF(x1, y_top, max(2.0, x2 - x1), max(2.0, y_bot - y_top))
            qp.setBrush(PATTERN_PREVIEW_FILL)
            qp.setPen(QPen(PATTERN_PREVIEW_EDGE, 1))
            qp.drawRoundedRect(rect, 2.0, 2.0)
        qp.setRenderHint(QPainter.Antialiasing, False)

    def detect_chart_key(self):
        """從譜面現有音符推出調性；沒有音高資料就回 None。

        用音長當權重——長音對調性的貢獻本來就比經過音大。
        """
        from .music_theory import detect_key

        pitched = [n for n in self.model.notes_tree if n.pitch is not None]
        if not pitched:
            return None
        return detect_key([int(n.pitch) for n in pitched],
                          [max(1, int(n.end) - int(n.start)) for n in pitched])

    def insert_pattern(self, kind: str, key, start_pitch: int, count: int,
                       direction: int, start_ms: float, step_beats: float,
                       hand: int, note_type: int = 0,
                       gate_ratio: float = 0.9) -> int:
        """一次放下一組音階／琶音，音高照 `key` 的音階走。回傳放了幾顆。

        每顆的時間間隔是 `step_beats` 拍（吃該處的變速），長度是間隔的
        `gate_ratio`，留一點縫才不會黏成一條。
        """
        from .music_theory import build_pattern

        if not self.model.has_chart():
            self.new_chart_requested.emit()
            return 0
        pitches = self._clip_pattern_to_keyboard(
            build_pattern(kind, key, int(start_pitch), int(count), int(direction)))
        if not pitches:
            return 0

        self.model.push_history()
        start_unit = self.mapper.ms_to_unit(float(start_ms))
        made: List[GNote] = []
        for i, pitch in enumerate(pitches):
            unit = start_unit + step_beats * self._beat_in_units_at(start_unit) * i
            next_unit = unit + step_beats * self._beat_in_units_at(unit) * gate_ratio
            on_ms = self.mapper.unit_to_ms(unit)
            off_ms = self.mapper.unit_to_ms(next_unit)
            n = GNote(None, len(self.model.notes_tree) + len(made))
            n.start = max(0, int(round(on_ms)))
            n.end = max(n.start + 1, int(round(off_ms)))
            n.gate = n.end - n.start
            n.pitch = max(0, min(127, int(pitch)))
            n.note_type = int(note_type)
            n.hand = int(hand)
            center = self._pitch_to_lane_center(n.pitch)
            n.min_key, n.max_key = self._center_to_lane_range(
                center, self._note_input_width)
            # 每顆各自查自己那個時間點的鄰居，整串跨過漸強漸弱時力度才會跟著走
            n.velocity = self.model.velocity_near(n.start, n.hand)
            if self.model.is_midi_mode():
                n.track = self.model._default_midi_track_for_hand(n.hand)
                n.channel = self.model._default_midi_channel_for_track(n.track)
                n.off_velocity = self.model._default_midi_off_velocity_for_track(n.track)
            made.append(n)

        self.model.notes_tree.extend(made)
        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self.selected = {n.idx for n in made}
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(len(made))
        return len(made)

    def _place_note_at(self, pos: 'QPoint') -> None:
        """在游標位置（拍子 snap）新增一個音符。"""
        # 還沒有譜面就先請使用者建立一份：沒有拍點與長度的話，音符會落在
        # 一個沒有時間軸的地方，看起來像沒反應。
        if not self.model.has_chart():
            self._drag_status = t('status_need_chart')
            self._emit_status()
            self.new_chart_requested.emit()
            return
        raw_unit     = self._py_to_unit_abs(pos.y())
        snapped_unit = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        snapped_ms   = self.mapper.unit_to_ms(snapped_unit)
        dur_units    = self._note_duration_beats * self._beat_in_units_at(snapped_unit)
        end_ms       = self.mapper.unit_to_ms(snapped_unit + dur_units)
        dur_ms       = max(10.0, end_ms - snapped_ms)

        display_key = self._px_to_display_key(pos.x())
        if self.pitch_mode:
            pitch = self._lock_pitch(self._slot_to_pitch(display_key))
            center = self._pitch_to_lane_center(pitch)
        else:
            center = max(0, min(TOTAL_GAME_KEYS - 1, int(display_key)))
            pitch = self._infer_pitch_from_key(display_key)
        min_key, max_key = self._center_to_lane_range(center, self._note_input_width)

        self.model.push_history()
        n = GNote(None, len(self.model.notes_tree))
        n.start     = max(0, int(round(snapped_ms)))
        n.end       = max(n.start + 1, int(round(snapped_ms + dur_ms)))
        n.gate      = n.end - n.start
        n.min_key   = min_key
        n.max_key   = max_key
        n.pitch     = pitch
        n.note_type = self._note_input_note_type
        n.hand      = self._note_input_hand
        # 力度抄附近同手音符的，抄不到才用預設——新音才不會和旁邊格格不入
        n.velocity  = self.model.velocity_near(n.start, n.hand)
        if self.model.is_midi_mode():
            n.track = self.model._default_midi_track_for_hand(n.hand)
            n.channel = self.model._default_midi_channel_for_track(n.track)
            n.off_velocity = self.model._default_midi_off_velocity_for_track(n.track)
        if self.pitch_mode:
            self._sync_note_keys_to_pitch(n, self._note_input_width)

        self.model.notes_tree.append(n)
        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self.selected = {n.idx}
        # 記下來讓 mouseMoveEvent 可以直接拖長它
        self._input_drag_note       = n
        self._input_drag_start_unit = snapped_unit
        self._input_drag_base_end   = int(n.end)
        self._input_drag_extended   = False
        self.update()
        self.note_edited.emit()
        self.note_placed.emit(n)
        self.selection_changed.emit(1)

    # ── 放置後拖曳拉長時值 ────────────────────────────────────────────

    def _drag_extend_note(self, pos: 'QPoint') -> None:
        """放置模式按住不放往上拖：把剛放下的音符拉長到游標處（吸附拍格）。

        tap 一旦被拉長就強制變成 hold —— tap 沒有長度的概念，拖出長度的
        意圖就是要一個長押。
        """
        note = self._input_drag_note
        if note is None:
            return
        raw_unit = self._py_to_unit_abs(pos.y())
        snapped_end_unit = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        # 最短就是原本放下去的那個時值，往下拖不會比它更短
        min_end_ms = self._input_drag_base_end
        end_ms = int(round(self.mapper.unit_to_ms(snapped_end_unit)))
        end_ms = max(min_end_ms, end_ms)
        if end_ms == int(note.end):
            return

        note.end = max(int(note.start) + 1, end_ms)
        note.gate = max(1, int(note.end) - int(note.start))
        extended = int(note.end) > self._input_drag_base_end
        if extended and not self._input_drag_extended:
            self._input_drag_extended = True
            if int(note.note_type) == 0:      # tap → hold
                note.note_type = 2
        elif not extended and self._input_drag_extended:
            # 拖回原長度就還原成原本選的類型
            self._input_drag_extended = False
            if int(note.note_type) == 2 and int(self._note_input_note_type) == 0:
                note.note_type = 0

        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self.selected = {note.idx}
        self.update()
        self._emit_status()

    def _finish_input_drag(self) -> None:
        if self._input_drag_note is None:
            return
        changed = self._input_drag_extended
        self._input_drag_note = None
        self._input_drag_extended = False
        if changed:
            self.model.rebuild_display_cache()
            self.update()
            self.note_edited.emit()

    def _draw_note_input_cursor(self, qp: 'QPainter') -> None:
        """在游標位置畫 snap 指示線；預覽模式下使用圖示 ghost。"""
        if self._note_input_hover is None:
            return
        if self._input_drag_note is not None:
            # 正在拖長剛放下的音符，實體音符本身就看得到，不用再疊 ghost
            return
        pos = self._note_input_hover
        raw_unit     = self._py_to_unit_abs(pos.y())
        snapped_unit = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        snapped_rel  = snapped_unit - self.window_start_unit
        display_key  = self._px_to_display_key(pos.x())
        if self.pitch_mode:
            pitch = self._lock_pitch(self._slot_to_pitch(display_key))
            center = self._pitch_to_lane_center(pitch)
        else:
            center = max(0, min(TOTAL_GAME_KEYS - 1, int(display_key)))
            pitch = self._infer_pitch_from_key(display_key)
        min_key, max_key = self._center_to_lane_range(center, self._note_input_width)

        snap_y  = int(self._unit_to_py(snapped_rel))
        if self.pitch_mode:
            slot = self._pitch_to_slot(pitch)
            _a, _b = self._key_span(slot)
            key_x, key_x2 = int(_a), int(_b)
        else:
            key_x = int(self._key_span(int(min_key))[0])
            key_x2 = int(self._key_span(int(max_key))[1])
        w = self.width()

        # 水平 snap 線（紅色虛線）
        from PyQt5.QtGui import QPen, QColor
        from PyQt5.QtCore import Qt
        qp.setPen(QPen(QColor(255, 80, 80, 200), 1, Qt.DashLine))
        qp.drawLine(0, snap_y, w, snap_y)

        # 鍵位方格預覽（編輯模式）/ 圖示 ghost（預覽模式）
        dur_unit = self._note_duration_beats * self._beat_in_units_at(snapped_unit)
        end_unit = snapped_unit + dur_unit - self.window_start_unit
        note_top = int(self._unit_to_py(end_unit))
        note_bot = snap_y
        if note_bot > note_top:
            if self.preview_mode:
                ghost = GNote(None, -1)
                ghost.start = max(0, int(round(self.mapper.unit_to_ms(snapped_unit))))
                ghost.end = max(ghost.start + 1, int(round(self.mapper.unit_to_ms(snapped_unit + dur_unit))))
                ghost.gate = max(1, ghost.end - ghost.start)
                ghost.min_key = min_key
                ghost.max_key = max_key
                ghost.note_type = int(self._note_input_note_type)
                ghost.hand = int(self._note_input_hand)
                ghost.pitch = pitch

                qp.save()
                qp.setOpacity(0.72)
                if ghost.note_type == 2:
                    self._preview_hold_body(qp, ghost)
                self._preview_note_head(qp, ghost)
                if ghost.note_type == 3:
                    # 和實際繪製走同一條路徑，游標預覽才不會跟落下的音符長得不一樣
                    self._preview_stac_v(qp, ghost)
                qp.restore()

                qp.setPen(QPen(QColor(255, 220, 80, 220), 1))
                qp.setBrush(Qt.NoBrush)
                for pr in self._preview_part_rects(ghost):
                    qp.drawRect(pr)
            else:
                # 預覽方塊和實際音符長一樣：白色圓角外框 + 該聲部的顏色。
                # 顏色改成問 _note_colors，這樣「放下去會是什麼樣子」就跟
                # 放下去之後真的長什麼樣一致（含 channel/track 的聲部配色）。
                ghost = GNote(None, -1)
                ghost.start = max(0, int(round(self.mapper.unit_to_ms(snapped_unit))))
                ghost.end = max(ghost.start + 1,
                                int(round(self.mapper.unit_to_ms(snapped_unit + dur_unit))))
                ghost.min_key, ghost.max_key = int(min_key), int(max_key)
                ghost.note_type = self._note_input_note_type
                ghost.hand = self._note_input_hand
                ghost.pitch = pitch
                if self.model.is_midi_mode():
                    # 和 _place_note 用同一套推導，預覽的顏色才等於放下去的顏色
                    ghost.track = self.model._default_midi_track_for_hand(ghost.hand)
                    ghost.channel = self.model._default_midi_channel_for_track(ghost.track)
                fill = QColor(self._note_colors(ghost)[0])
                fill.setAlpha(120)
                rect = QRectF(key_x, note_top,
                              max(1.0, float(key_x2 - key_x)),
                              max(1.0, float(note_bot - note_top)))
                radius = min(NOTE_CORNER_RADIUS, rect.width() / 2.0, rect.height() / 2.0)
                qp.setRenderHint(QPainter.Antialiasing, True)
                qp.setBrush(QBrush(_note_gradient(fill, rect)))
                qp.setPen(QPen(NOTE_FRAME, 2))
                qp.drawRoundedRect(rect, radius, radius)
                qp.setRenderHint(QPainter.Antialiasing, False)

        # 提示文字
        snapped_ms  = self.mapper.unit_to_ms(snapped_unit)
        pitch_str   = str(pitch) if pitch is not None else '-'
        min_lane, max_lane = lane_range_to_external(min_key, max_key)
        qp.setPen(QColor(255, 200, 60))
        from PyQt5.QtGui import QFont
        qp.setFont(QFont('Consolas', 8))
        qp.drawText(4, self.height() - 22,
                    f'✏ snap={int(snapped_ms)}ms  key={min_lane}~{max_lane}  '
                    f'pitch={pitch_str}  dur={self._note_duration_beats:.4g}beat  '
                    f'hand={"右" if self._note_input_hand == 0 else "左"}')

    def confirm_alloc_section(self) -> None:
        if not self.alloc_active:
            return
        final = {}
        for i in self.alloc_locked:
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                final[i] = (n.min_key, n.max_key, n.pitch)
        self._restore_alloc_orig()
        self.model.push_history()
        for i, (mn, mx, pt) in final.items():
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                n.min_key, n.max_key, n.pitch = mn, mx, pt
        self.alloc_active = False
        self.alloc_locked.clear()
        self.alloc_orig.clear()
        self._alloc_drag_edge = None
        # clear preserve fields
        if hasattr(self, '_preserve_min_key'):
            delattr = False
            try:
                del self._preserve_min_key
                del self._preserve_max_key
            except Exception:
                pass
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()
        self._drag_status = ''
        self._emit_status()

    def cancel_alloc_section(self) -> None:
        if not self.alloc_active:
            return
        self._restore_alloc_orig()
        self.alloc_active = False
        self.alloc_locked.clear()
        self.alloc_orig.clear()
        self._alloc_drag_edge = None
        # clear preserve fields
        try:
            del self._preserve_min_key
            del self._preserve_max_key
        except Exception:
            pass
        self.model.rebuild_display_cache()
        self.update()
        self._drag_status = ''
        self._emit_status()

    def _restore_alloc_orig(self) -> None:
        for i, (mn, mx, pt) in self.alloc_orig.items():
            if 0 <= i < len(self.model.notes_tree):
                n = self.model.notes_tree[i]
                n.min_key, n.max_key, n.pitch = mn, mx, pt

    def _apply_alloc_dist(self) -> None:
        if not self.alloc_locked:
            return
        mn = max(0, min(TOTAL_GAME_KEYS - 1, int(round(self.alloc_target_min))))
        mx = max(mn, min(TOTAL_GAME_KEYS - 1, int(round(self.alloc_target_max))))
        self.alloc_target_min, self.alloc_target_max = mn, mx
        span = mx - mn

        groups: Dict[Optional[int], List[int]] = {}
        for i in self.alloc_locked:
            if 0 <= i < len(self.model.notes_tree):
                p = self.model.notes_tree[i].pitch
                groups.setdefault(p, []).append(i)

        # 以音高排名均分：不依音高數值差，而是依排序位置等距分配
        pitches_sorted = sorted(p for p in groups if p is not None)
        n_p = len(pitches_sorted)
        rank_frac: Dict[int, float] = {
            p: (i / max(n_p - 1, 1)) for i, p in enumerate(pitches_sorted)
        }

        for p, indices in groups.items():
            frac = rank_frac[p] if (p is not None and p in rank_frac) else 0.5
            kpos = mn + int(round(frac * span))
            kpos = max(mn, min(mx, kpos))
            for i in indices:
                if 0 <= i < len(self.model.notes_tree):
                    note = self.model.notes_tree[i]
                    # 使用原始寬度，且不 clip max_key，完全保留音符寬度
                    orig_mn, orig_mx, _ = self.alloc_orig.get(
                        i, (note.min_key, note.max_key, note.pitch))
                    w = orig_mx - orig_mn
                    new_min = kpos
                    new_max = kpos + w
                    # 超出右邊界 → 先 clip 到 TOTAL_GAME_KEYS 範圍，維持寬度
                    if new_max > TOTAL_GAME_KEYS - 1:
                        new_max = TOTAL_GAME_KEYS - 1
                        new_min = new_max - w
                    # 超出左邊界
                    if new_min < 0:
                        new_min = 0
                        new_max = w
                    # 若有 preserve 範圍（進入 Alloc 時記錄），則進一步 clip 到 preserve 範圍內
                    pmn = getattr(self, '_preserve_min_key', None)
                    pmx = getattr(self, '_preserve_max_key', None)
                    if pmn is not None and pmx is not None:
                        if new_min < pmn:
                            new_min = pmn
                            new_max = new_min + w
                        if new_max > pmx:
                            new_max = pmx
                            new_min = new_max - w
                    note.min_key = new_min
                    note.max_key = new_max
        # After redistribution, ensure overall chart width remains the same
        try:
            cur_min = min(n.min_key for n in self.model.notes_tree)
            cur_max = max(n.max_key for n in self.model.notes_tree)
            pmn = getattr(self, '_preserve_min_key', None)
            pmx = getattr(self, '_preserve_max_key', None)
            if pmn is not None and pmx is not None:
                # If current range exceeds preserved range, shift all notes to fit
                if cur_min < pmn or cur_max > pmx:
                    # desired shift to map current range -> preserved range
                    desired_shift = (pmn - cur_min) if (cur_min < pmn) else (pmx - cur_max)
                    # Prefer shifting only the selected (locked) notes so alloc can
                    # move them to the chart edge even when other notes occupy
                    # space. Fall back to uniform shift if no locked notes.
                    try:
                        if self.alloc_locked:
                            # compute allowed shift for locked notes only
                            sel_notes = [self.model.notes_tree[i] for i in self.alloc_locked
                                         if 0 <= i < len(self.model.notes_tree)]
                            if sel_notes:
                                min_sel = min(n.min_key for n in sel_notes)
                                max_sel = max(n.max_key for n in sel_notes)
                                min_allowed = -min_sel
                                max_allowed = (TOTAL_GAME_KEYS - 1) - max_sel
                                shift = max(min(desired_shift, max_allowed), min_allowed)
                                for i in self.alloc_locked:
                                    if 0 <= i < len(self.model.notes_tree):
                                        n = self.model.notes_tree[i]
                                        n.min_key = int(n.min_key + shift)
                                        n.max_key = int(n.max_key + shift)
                            else:
                                raise Exception("no selected notes")
                        else:
                            raise Exception("no locked notes")
                    except Exception:
                        # fallback: uniform shift across all notes (old behavior)
                        try:
                            min_allowed = -min(n.min_key for n in self.model.notes_tree)
                            max_allowed = (TOTAL_GAME_KEYS - 1) - max(n.max_key for n in self.model.notes_tree)
                        except Exception:
                            min_allowed = -TOTAL_GAME_KEYS
                            max_allowed = TOTAL_GAME_KEYS
                        shift = max(min(desired_shift, max_allowed), min_allowed)
                        for n in self.model.notes_tree:
                            n.min_key = int(n.min_key + shift)
                            n.max_key = int(n.max_key + shift)
        except Exception:
            pass
        self.model.rebuild_display_cache()

    def _update_alloc_edge(self, axis: str, side: str, raw: float) -> None:
        if axis == 'x':
            v = max(0, min(TOTAL_GAME_KEYS - 1, int(round(raw))))
            if side == 'min':
                self.alloc_target_min = min(v, self.alloc_target_max)
            else:
                self.alloc_target_max = max(v, self.alloc_target_min)
            self._apply_alloc_dist()
        else:
            if side == 'min':
                self.alloc_time_min_u = min(raw, self.alloc_time_max_u - 0.05)
            else:
                self.alloc_time_max_u = max(raw, self.alloc_time_min_u + 0.05)
        self.update()

    # ==================================================================
    # 座標轉換
    # ==================================================================

    def _unit_to_py(self, unit_rel: float) -> float:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            ms_range = max(float(self._time_uniform_span_ms or 1.0), 1e-9)
            cur_ms = self.mapper.unit_to_ms(self.window_start_unit + unit_rel)
            return self.height() * (1.0 - (cur_ms - ws_ms) / ms_range)
        return self.height() * (1.0 - unit_rel / max(self.window_size_unit, 1e-9))

    def _py_to_unit_abs(self, py: float) -> float:
        if self.time_uniform:
            ws_ms = self.mapper.unit_to_ms(self.window_start_unit)
            ms_range = max(float(self._time_uniform_span_ms or 1.0), 1e-9)
            frac = 1.0 - py / max(self.height(), 1)
            target_ms = ws_ms + frac * ms_range
            return self.mapper.ms_to_unit(target_ms)
        rel = (1.0 - py / max(self.height(), 1)) * self.window_size_unit
        return self.window_start_unit + rel

    def _key_to_px(self, key: float) -> float:
        return key * self.width() / TOTAL_GAME_KEYS

    def _px_to_key(self, px: float) -> float:
        return px * TOTAL_GAME_KEYS / max(self.width(), 1)

    def _note_rect(self, n: GNote) -> Optional[QRectF]:
        start_u = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        end_u   = self.mapper.ms_to_unit(float(n.end))   - self.window_start_unit
        win = self.window_size_unit
        if end_u < 0 or start_u > win:
            return None
        x1, x2 = self._note_display_x_range(n)
        y_top    = self._unit_to_py(end_u)
        y_bottom = self._unit_to_py(start_u)
        w = max(1.0, x2 - x1)
        h = max(float(MIN_NOTE_HEIGHT_PX), y_bottom - y_top)
        return QRectF(x1, y_top, w, h)

    # ==================================================================
    # 視窗邊界
    # ==================================================================

    def _update_unit_bounds(self) -> None:
        # 以 music_end_ms 與音符範圍取最大值，確保新增小節後能捲到末端
        end_ms = getattr(self.model, 'music_end_ms', 0.0) or 0.0
        end_unit = self.mapper.ms_to_unit(end_ms)
        notes = self.model.notes
        if notes:
            mn, mx = self.mapper.unit_range_of_notes(notes)
            self._min_unit = mn
            self._max_unit = max(mx, end_unit)
        else:
            self._min_unit = 0.0
            self._max_unit = max(end_unit, self.window_size_unit)
        # 尾端補空白。至少要有「判定線到視窗底部」那一段，最後一顆音符才走得
        # 到判定線（沒有的話它只能停在畫面最底下）；再加上 TAIL_PAD_WINDOWS 個
        # 視窗高，讓最後的音符過線之後能繼續往上跑出畫面，收尾不會卡住。
        self._max_unit += self.window_size_unit * (
            self._judge_fraction() + TAIL_PAD_WINDOWS
        )
        self._clamp_window_start()

    def _clamp_window_start(self) -> None:
        try:
            lo = min(-PRE_ROLL_UNITS, self._min_unit, 0.0)
            hi = max(lo, self._max_unit - self.window_size_unit)
            self.window_start_unit = max(lo, min(self.window_start_unit, hi))
        except Exception:
            pass

    def _window_ms(self) -> Tuple[float, float]:
        return (self.mapper.unit_to_ms(self.window_start_unit),
                self.mapper.unit_to_ms(self.window_start_unit + self.window_size_unit))

    # ==================================================================
    # 工具
    # ==================================================================

    def _sixteenth_ms(self) -> float:
        bpm = self.model.bpm if self.model.bpm > 0 else 120.0
        return 60000.0 / bpm / 4.0

    def _quantize(self, delta_ms: float, snap_ms: float) -> float:
        if snap_ms <= 0:
            return delta_ms
        return round(delta_ms / snap_ms) * snap_ms

    def _wheel_multiplier(self) -> float:
        import time as _t
        now = _t.time()
        while self._wheel_events and (
            now - self._wheel_events[0][0] > self._wheel_hist_sec
            or len(self._wheel_events) > self._wheel_max_items
        ):
            self._wheel_events.popleft()
        if not self._wheel_events:
            return self._wheel_min_mult
        times = [t for t, _ in self._wheel_events]
        steps = [s for _, s in self._wheel_events]
        span  = max(1e-3, times[-1] - times[0])
        rate  = sum(steps) / span
        mult  = 1.0 + rate / self._wheel_scale
        return max(self._wheel_min_mult, min(self._wheel_max_mult, mult))

    def _scroll_step_units(self) -> float:
        """自適應滾動步長：視窗大小的 1.5%，縮放愈大（視窗愈小）步長愈細緻。
        範圍 clamp 至 [0.01, 0.5] units，鍵盤 Shift 另行乘 4。"""
        return max(0.01, min(0.5, self.window_size_unit * 0.015))

    def _emit_status(self) -> None:
        ws_ms, we_ms = self._window_ms()
        ws_u = self.window_start_unit
        we_u = ws_u + self.window_size_unit
        mode_name = {
            'measure': '小節均分',
            'time': '時間均分',
            'pitch': '音高',
        }.get(self.view_mode, self.view_mode)
        extra = f'  [模式:{mode_name}]'
        if self._drag_status:
            extra += f' [{self._drag_status}]'
        msg = (t('status_window',
                 int(ws_ms), int(we_ms),
                 ws_u, we_u,
                 self.window_size_unit,
                 len(self.selected),
                 self.model.bpm) + extra)
        self.status_changed.emit(msg)

    # ==================================================================
    # 繪製
    # ==================================================================

    def paintEvent(self, _: QPaintEvent) -> None:
        self._visible.clear()
        self._trill_cell_hits.clear()
        self._lane_flag_cache = None      # 每幀讀一次設定就好
        self._ensure_channel_colors()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, False)
        overlay_op = 0.0
        if self.overlay_role == 'top':
            # 疊層：不畫背景（讓底層透出來），整層用統一透明度疊上去。
            # 格線再壓更淡，否則音高鍵盤欄會把底層的音符切得很碎。
            overlay_op = max(0.05, min(1.0, float(self.overlay_opacity)))
            qp.setOpacity(overlay_op * 0.3)
            self._draw_grid(qp)
            qp.setOpacity(overlay_op)
        else:
            self._draw_bg(qp)
            self._draw_grid(qp)
        if self.preview_mode:
            self._draw_notes_preview(qp)
            self._draw_preview_overlay(qp)
            # Draw selection outlines on top of the preview overlay so they remain visible
            qp.setPen(QPen(SEL_OUTLINE, 2))
            qp.setBrush(Qt.NoBrush)
            for rect, n in self._visible:
                if n.idx in self.selected:
                    for pr in self._preview_part_rects(n):
                        qp.drawRect(pr)
            # Draw rubber-band selection rectangle (mouse drag) in preview mode
            self._draw_rubber(qp)
            if self._note_input_mode:
                self._draw_note_input_cursor(qp)
        else:
            self._draw_notes(qp)
            self._draw_alloc_overlay(qp)
            self._draw_rubber(qp)
            if self._is_drag_copy:
                self._draw_drag_info(qp)
            if self._note_input_mode:
                self._draw_note_input_cursor(qp)
            self._draw_pattern_preview(qp)
        # 疊層模式的上層不畫鍵盤，否則會把底下那層整條蓋掉
        if self.overlay_role != 'top':
            if self.pitch_mode:
                self._draw_pedal_lane(qp)
                self._draw_dynamics_lanes(qp)
                self._draw_piano_keyboard(qp)
                # 標籤畫在鍵盤旁邊，所以要在鍵盤之後——鍵盤會鋪滿整條的底色
                self._draw_lane_headers(qp)
            else:
                self._draw_lane_keyboard(qp)

        # Draw beat/bar lines and BPM labels last so they appear above notes.
        if overlay_op:
            qp.setOpacity(overlay_op * 0.3)   # 拍線會和底層重複，畫淡一點
        self._draw_beat_lines(qp)
        if overlay_op:
            qp.setOpacity(overlay_op)

        # 判定線畫在最上層。它和鍵盤上緣是同一個 y，畫早了會被鍵盤那條紫色
        # 上框蓋掉；而剛好停在整數小節時，那條小節線也會落在同一個 y 把它蓋掉
        # （播放到 0ms 就是這樣整條不見的）。
        self._draw_judge_line(qp)

        # 若正在拖曳小節線，疊加顯示移動中的小節線與 BPM 提示
        if getattr(self, '_barline_dragging', False) and self._barline_drag_py is not None:
            qp.setPen(QPen(QColor(255, 120, 60), 2, Qt.SolidLine))
            qp.drawLine(0, int(self._barline_drag_py), self.width(), int(self._barline_drag_py))
            try:
                # 嘗試計算暫時 BPM 並顯示在狀態列
                if self._barline_drag_start_ms is not None:
                    cur_ms = self.mapper.unit_to_ms(self._py_to_unit_abs(self._barline_drag_py))
                    new_dur = max(1, int(round(cur_ms - float(self._barline_drag_start_ms))))
                    num = self.model.get_beats_per_bar_at_ms(int(self._barline_drag_start_ms))
                    den = self.model.time_sig_denominator
                    new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                    qp.setPen(TIME_LABEL)
                    qp.drawText(6, int(self._barline_drag_py) - 6, f'{new_bpm:.2f} BPM')
            except Exception:
                pass

        # 分割模式：框出對側格子的可視時間範圍 + 本格代表色外框
        self._draw_peer_range(qp)
        qp.setOpacity(1.0)      # 框線不跟著疊層變淡，否則看不出作用中的是哪層
        self._draw_voice_legend(qp)
        self._draw_pane_frame(qp)

    # ------------------------------------------------------------------
    # 聲部圖例
    # ------------------------------------------------------------------
    def _voice_legend_entries(self):
        """(顏色, 樂器名稱) 清單。沒有樂器標示就回空 list。

        只在「還沒排譜的 MIDI」有意義——那時候畫面是照聲部（channel/track）
        上色的，圖例才對得上。排過譜之後一律以左右手上色，紅藍兩色自明。
        """
        model = self.model
        if self.preview_mode or not model.is_midi_mode():
            return []
        if not getattr(model, 'midi_unarranged', False):
            return []
        names = getattr(model, 'midi_voice_names', None)
        if not names:
            return []
        vmap = getattr(self, '_voice_color_map', None)
        if not vmap:
            return []
        # 分不出聲部時，上色會退回「照左右手」或「照音高中位數切一刀」
        # （見 _build_channel_color_map）。那時候顏色和樂器沒有對應關係
        # ——單軌 MIDI 會被切成兩色，圖例卻只能標出兩個一樣的樂器名。
        # 只有真的照 (channel, track) 上色時，圖例才有意義。
        if getattr(self, '_voice_split', None) is not None:
            return []
        if getattr(self, '_voice_by_hand', False):
            return []
        # 畫面上實際用到的聲部，照顏色對應的順序排。
        seen = {}
        for note in model.notes_tree:
            key = self._voice_key(note)
            if key in seen:
                continue
            track = getattr(note, 'track', None)
            channel = note.channel if note.channel is not None else 0
            label = names.get((int(track), int(channel))) if track is not None else ''
            if not label:
                continue
            colour = vmap.get(key)
            if colour is None:
                continue
            seen[key] = (QColor(colour), label)
        if len(seen) < 2:
            return []          # 只有一個聲部，圖例沒有意義
        return [seen[k] for k in sorted(seen)]

    def _draw_voice_legend(self, qp: QPainter) -> None:
        """右上角的小框：哪個顏色是哪個樂器。

        多軌 MIDI（例如 Designant 有 20 軌：Bass / Drum / Pad / Lead…）光看
        顏色分不出誰是誰，所以把音軌自己的樂器標示畫出來。名稱取自 MIDI 的
        track_name / instrument_name meta，沒有名稱才退回 program_change 的
        GM 音色名。
        """
        self._voice_legend_rect = None
        entries = self._voice_legend_entries()
        if not entries:
            return

        metrics = qp.fontMetrics()
        swatch = max(8, metrics.height() - 4)
        pad = 6
        gap = 6
        line_h = max(swatch, metrics.height()) + 3
        width = max(metrics.horizontalAdvance(name) for _c, name in entries)
        box_w = pad * 2 + swatch + gap + width
        box_h = pad * 2 + line_h * len(entries) - 3
        # 高度超過一半畫面就分欄，二十軌的檔案不能從頭蓋到尾。
        columns = 1
        while box_h > self.height() * 0.6 and columns < 4:
            columns += 1
            rows = (len(entries) + columns - 1) // columns
            box_h = pad * 2 + line_h * rows - 3
            box_w = pad * 2 + (swatch + gap + width) * columns + gap * (columns - 1)
        rows = (len(entries) + columns - 1) // columns

        x = self.width() - box_w - 8
        y = 8
        # 記下來給測試用：畫在哪、多大。
        self._voice_legend_rect = (x, y, box_w, box_h)
        qp.setOpacity(1.0)
        qp.setPen(QPen(QColor(90, 90, 100), 1))
        qp.setBrush(QColor(24, 24, 28, 225))
        qp.drawRoundedRect(x, y, box_w, box_h, 4, 4)

        for index, (colour, name) in enumerate(entries):
            column, row = divmod(index, rows)
            cx = x + pad + column * (swatch + gap + width + gap)
            cy = y + pad + row * line_h
            qp.setPen(Qt.NoPen)
            qp.setBrush(colour)
            qp.drawRect(cx, cy + (line_h - 3 - swatch) // 2, swatch, swatch)
            qp.setPen(QPen(QColor(226, 226, 232)))
            qp.drawText(cx + swatch + gap, cy + metrics.ascent(), name)

    def _ms_to_py(self, ms: float) -> float:
        return self._unit_to_py(self.mapper.ms_to_unit(float(ms)) - self.window_start_unit)

    def _draw_peer_range(self, qp: QPainter) -> None:
        """音高模式格子裡，框出小節/時間模式格子目前顯示的時間範圍。

        兩格的縱向映射不同（小節模式把小節等分、音高模式照實際時間），
        所以就算兩邊看的是同一段時間，框線也能看出彼此的對應關係；
        視窗被 clamp 而對不齊時更會直接看出差異。
        """
        peer = self._peer
        if not self.split_active or peer is None or not peer.isVisible():
            return
        # 疊層時兩格本來就重疊在同一塊區域，範圍框沒有意義
        if self.overlay_role:
            return
        # 只有音高格畫對側的範圍；兩格同模式時沒有參考價值
        if not self.pitch_mode or peer.pitch_mode:
            return
        try:
            s_ms, e_ms = peer.visible_ms_range()
            y_bot = self._ms_to_py(s_ms)     # 時間往上增加 → start 在下
            y_top = self._ms_to_py(e_ms)
        except Exception:
            return
        if y_bot < y_top:
            y_top, y_bot = y_bot, y_top
        h_px = self.height()
        y_top_c = max(0.0, min(float(h_px), y_top))
        y_bot_c = max(0.0, min(float(h_px), y_bot))
        if y_bot_c - y_top_c < 1.0:
            return

        col = QColor(PANE_COLORS[peer.pane_role % len(PANE_COLORS)])
        # 範圍外變暗，讓框內的區段一眼看出來
        shade = QColor(0, 0, 0, 90)
        qp.setPen(Qt.NoPen)
        qp.setBrush(shade)
        if y_top_c > 0:
            qp.drawRect(0, 0, self.width(), int(y_top_c))
        if y_bot_c < h_px:
            qp.drawRect(0, int(y_bot_c), self.width(), int(h_px - y_bot_c))
        qp.setBrush(Qt.NoBrush)

        pen = QPen(col, 2, Qt.DashLine)
        qp.setPen(pen)
        qp.drawRect(1, int(y_top_c) + 1,
                    max(1, self.width() - 3),
                    max(1, int(y_bot_c - y_top_c) - 2))

    def _draw_pane_frame(self, qp: QPainter) -> None:
        if not self.split_active:
            return
        col = QColor(PANE_COLORS[self.pane_role % len(PANE_COLORS)])
        if self.pane_active:
            w = 3
        else:
            w = 1
            col.setAlpha(110)
        qp.setPen(QPen(col, w))
        qp.setBrush(Qt.NoBrush)
        off = w // 2
        qp.drawRect(off, off, max(1, self.width() - w), max(1, self.height() - w))

    def _draw_bg(self, qp: QPainter) -> None:
        qp.fillRect(self.rect(), BG_COLOR)

    def _draw_scale_highlight(self, qp: QPainter) -> None:
        """把調內的音格鋪一層很淡的底，主音再深一點。

        底色是黑的，所以是「把調內的標亮」而不是「把調外的畫暗」——在黑底上
        再壓黑看不出差別。主音另外一個顏色，才知道這個調從哪裡開始數。
        """
        if not self._scale_highlight_on():
            return
        classes = self._highlight_pitch_classes()
        if not classes:
            return
        key = self._active_key()
        tonic = int(key.tonic) % 12 if key is not None else None
        h = self.height()
        bottom = self._piano_top_py()       # 不要蓋到底下的鍵盤
        if bottom > 0:
            h = min(h, int(bottom))
        qp.setPen(Qt.NoPen)
        for i in range(PITCH_GRID_KEYS):
            pitch = PITCH_MIDI_MIN + i
            if pitch % 12 not in classes:
                continue
            x1, x2 = self._key_span(i)
            qp.setBrush(SCALE_TONIC_BG if pitch % 12 == tonic else SCALE_IN_KEY_BG)
            qp.drawRect(int(x1), 0, max(1, int(x2 - x1)), h)
        qp.setBrush(Qt.NoBrush)

    def _draw_grid(self, qp: QPainter) -> None:
        h = self.height()
        qp.setFont(self._font_key)
        display_keys = self._display_key_count()
        # 音高模式：黑鍵欄位加深色底，讓排列像鋼琴鍵盤
        if self.pitch_mode:
            self._draw_scale_highlight(qp)
            # 音高模式：底色維持全黑，只有在放置模式、而且靠近滑鼠的欄位才
            # 畫出格線，其餘留白讓音符自己說話。
            if not (self._note_input_mode and self._grid_focus_slot is not None):
                return
            focus = int(self._grid_focus_slot)
            reach = PITCH_GRID_FOCUS_KEYS
            for i in range(max(0, focus - reach), min(display_keys, focus + reach + 1)):
                x1, x2 = self._key_span(i)
                fade = 1.0 - abs(i - focus) / float(reach + 1)
                if fade <= 0.0:
                    continue
                if _is_black_pitch(PITCH_MIDI_MIN + i):
                    qp.setPen(Qt.NoPen)
                    shade = QColor(30, 30, 36)
                    shade.setAlphaF(0.9 * fade)
                    qp.setBrush(shade)
                    qp.drawRect(int(x1), 0, max(1, int(x2 - x1)), h)
                    qp.setBrush(Qt.NoBrush)
                line = QColor(GRID_MAJOR if (PITCH_MIDI_MIN + i) % 12 == 0 else GRID_MINOR)
                line.setAlphaF(min(1.0, line.alphaF() * fade))
                qp.setPen(QPen(line, 1))
                qp.drawLine(int(x1), 0, int(x1), h)
                qp.drawLine(int(x2), 0, int(x2), h)
                if (PITCH_MIDI_MIN + i) % 12 == 0:
                    qp.setPen(KEY_LABEL)
                    qp.drawText(int(x1) + 2, 11, self._pitch_label(PITCH_MIDI_MIN + i))
            return
        for i in range(display_keys + 1):
            x = int(self._display_key_to_px(i))
            major = (i % 4 == 0)
            qp.setPen(QPen(GRID_MAJOR if major else GRID_MINOR, 1))
            qp.drawLine(x, 0, x, h)
            if i < display_keys:
                qp.setPen(KEY_LABEL)
                qp.drawText(x + 2, 11, str(lane_to_external(i)))

        # 分區金線：每 7 格一區（1~7 / 8~14 / 15~21 / 22~28）。畫在格線層 =
        # 音符下方，只當背景參考，不會遮住音符。
        qp.setPen(QPen(ZONE_LINE_COLOR, 2))
        for i in range(ZONE_LANES, display_keys, ZONE_LANES):
            x = int(self._display_key_to_px(i))
            qp.drawLine(x, 0, x, h)

    def _draw_beat_lines(self, qp: QPainter) -> None:
        w = self.width()
        start_b = math.floor(self.window_start_unit)
        end_b   = math.ceil(self.window_start_unit + self.window_size_unit) + 1
        qp.setFont(self._font_time)

        # If time_uniform is enabled, draw based on actual ms beat timings
        if self.time_uniform:
            ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
            beats = self.model.get_beat_entries()
            if beats:
                prev_measure = None
                # draw beat lines for beats falling within visible ms range
                for idx, bms in beats:
                    if bms < ws_ms - 1 or bms > we_ms + 1:
                        continue
                    unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                    py = int(self._unit_to_py(unit))
                    if not (-2 <= py <= self.height() + 2):
                        continue
                    # determine if this beat is a bar start
                    measure_idx = self.model.get_measure_at_ms(bms)
                    bar_no = measure_idx + 1
                    is_bar = (prev_measure is None) or (measure_idx != prev_measure)
                    prev_measure = measure_idx
                    if is_bar:
                        # time signature at current bar start
                        ts_num = self.model.get_beats_per_bar_at_ms(float(bms))
                        ts_den = self.model.time_sig_denominator
                        for ch_ms, _ch_num, ch_den in self.model.time_sig_changes:
                            if ch_ms <= int(bms):
                                ts_den = ch_den
                            else:
                                break
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
                        tot_s = bms / 1000.0
                        m = int(tot_s // 60)
                        s = tot_s - m * 60
                        try:
                            bar_bpm = self.model.get_measure_bpm(bar_no - 1)
                            bpm_str = f'  {bar_bpm:.1f}BPM'
                        except Exception:
                            bpm_str = ''
                        qp.setPen(TIME_LABEL)
                        qp.drawText(2, py - 2, f'{m}:{s:05.2f}  {ts_num}/{ts_den}{bpm_str}')
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawText(w - 30, py - 2, str(bar_no))
                    else:
                        qp.setPen(QPen(BEATLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
        else:
            # In measure-uniform mode, also derive lines from beat timings + measure changes
            # to avoid missing labels under variable time signatures.
            ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
            beats = self.model.get_beat_entries()
            if beats:
                prev_measure = None
                for _idx, bms in beats:
                    if bms < ws_ms - 1 or bms > we_ms + 1:
                        continue
                    unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                    py = int(self._unit_to_py(unit))
                    if not (-2 <= py <= self.height() + 2):
                        continue

                    measure_idx = self.model.get_measure_at_ms(bms)
                    bar_no = measure_idx + 1
                    is_bar = (prev_measure is None) or (measure_idx != prev_measure)
                    prev_measure = measure_idx

                    if is_bar:
                        ts_num = self.model.get_beats_per_bar_at_ms(float(bms))
                        ts_den = self.model.time_sig_denominator
                        for ch_ms, _ch_num, ch_den in self.model.time_sig_changes:
                            if ch_ms <= int(bms):
                                ts_den = ch_den
                            else:
                                break
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)
                        tot_s = bms / 1000.0
                        m = int(tot_s // 60)
                        s = tot_s - m * 60
                        try:
                            bar_bpm = self.model.get_measure_bpm(bar_no - 1)
                            bpm_str = f'  {bar_bpm:.1f}BPM'
                        except Exception:
                            bpm_str = ''
                        qp.setPen(TIME_LABEL)
                        qp.drawText(2, py - 2, f'{m}:{s:05.2f}  {ts_num}/{ts_den}{bpm_str}')
                        qp.setPen(QPen(BARLINE_COLOR, 1))
                        qp.drawText(w - 30, py - 2, str(bar_no))
                    else:
                        qp.setPen(QPen(BEATLINE_COLOR, 1))
                        qp.drawLine(0, py, w, py)

    def _build_channel_color_map(self) -> dict:
        """把實際用到的 channel 依出現順序(rank)對應到『對比優先』的顏色。
        用到越少 → 拿到色相差越大的色；用到很多(>調色盤數)才回頭重用(妥協)。"""
        # 用「聲部」= (channel, track) 當 key，不是只用 channel。
        # 絕大多數鋼琴 MIDI 是「多個 track、全部掛在 channel 0」——實測
        # felzione 和 v-aesir 都是 2 個 track / 1 個 channel，只看 channel
        # 的話整份譜會是同一個顏色，這正是看起來「全紅」的原因。
        # 分聲部的三層退路。format 0 的 MIDI 只有一個 track、常常也只有一個
        # channel，(channel, track) 完全分不出東西，整份就會是同一個顏色——
        # 「兩個音軌但 MIDI 全紅」講的就是這種檔案。轉成 XML 之後看得出藍紅，
        # 是因為排譜時的分手本來就會退到「照音高中位數切一刀」，這裡比照辦理。
        notes = self.model.notes_tree
        self._voice_by_hand = False
        self._voice_split = None
        voices = sorted({self._voice_key(nn) for nn in notes})
        if len(voices) < 2:
            if len({int(nn.hand) for nn in notes}) > 1:
                self._voice_by_hand = True
            else:
                pitches = [int(nn.pitch) for nn in notes if nn.pitch is not None]
                if len(set(pitches)) > 1:
                    self._voice_split = float(median(pitches))
            voices = sorted({self._voice_key(nn) for nn in notes})
        pal = MIDI_CHANNEL_COLORS
        order = MIDI_COLOR_ORDER or list(range(len(pal)))
        vmap = {}
        for i, v in enumerate(voices):
            vmap[v] = pal[order[i % len(order)]]
        self._voice_color_map = vmap
        # channel → 顏色（工具列圖示等只知道 channel 的地方用）
        cmap = {}
        for (ch, _tr), col in vmap.items():
            cmap.setdefault(ch, col)
        self._chan_color_map = cmap
        # 一起記下這份對照表是照哪些 channel 算的。舊版只快取結果、從來不失效，
        # 換一份 MIDI（或改了音符的 channel）之後還在用上一首的配色，於是
        # 「對比優先」就失效了——這正是多頻道顏色看起來不對的原因。
        self._chan_color_key = tuple(voices)
        return cmap

    def _voice_key(self, n: GNote) -> tuple:
        """音符的『聲部』：同一個 channel 底下不同 track 也要分得出來。

        `_voice_by_hand` 是 (channel, track) 完全分不出來時的退路。
        """
        split = getattr(self, '_voice_split', None)
        if split is not None:
            # 音高中位數以上算一個聲部、以下算另一個（和排譜的分手同一套規則）
            pitch = n.pitch if n.pitch is not None else 0
            return (0 if float(pitch) >= split else 1, 0)
        if getattr(self, '_voice_by_hand', False):
            return (int(n.hand), 0)
        return (int(n.channel) if n.channel is not None else 0,
                int(n.track) if n.track is not None else 0)

    def _voice_base_color(self, n: GNote) -> QColor:
        vmap = getattr(self, '_voice_color_map', None)
        if not vmap:
            self._build_channel_color_map()
            vmap = self._voice_color_map
        c = vmap.get(self._voice_key(n))
        return QColor(c) if c is not None else self._channel_base_color(
            int(n.channel) if n.channel is not None else 0)

    def _ensure_channel_colors(self) -> None:
        """每次重繪開頭檢查一次配色是否還對應目前的 channel 集合。

        放在這裡而不是 `_channel_base_color` 裡——後者是每顆音符都會呼叫的，
        在那裡掃全曲會變成每次重繪 O(N²)。

        連「比對簽章」本身也不能每幀做：算簽章要對全譜跑一次 `_voice_key`，
        1128 顆的譜就是每幀多兩千次呼叫。改成只有快取被清掉時才重算——會動到
        channel/track 的操作都會發 `note_edited`，那裡會把 `_chan_color_key`
        設回 None。
        """
        if (getattr(self, '_chan_color_map', None)
                and getattr(self, '_chan_color_key', None) is not None):
            return
        self._build_channel_color_map()

    def _channel_base_color(self, channel: int) -> QColor:
        cmap = getattr(self, '_chan_color_map', None)
        if not cmap:
            cmap = self._build_channel_color_map()
        c = cmap.get(int(channel))
        if c is None:
            c = MIDI_CHANNEL_COLORS[int(channel) % len(MIDI_CHANNEL_COLORS)]
        return QColor(c)

    def _note_colors(self, n: GNote) -> Tuple[QColor, QColor]:
        # 預覽模式一律以左右手為準：那個模式畫的是遊戲實際外觀，而原版
        # 圖檔本來就分左右手幀。音軌（track）和 hand 排譜後會分岔——實測
        # felzione 有 2.5% 的音符 track != hand——兩套上色同時存在就會出現
        # 「顏色和左右手對不上」。存成 XML 之後 is_midi_mode() 變 False、
        # 兩邊自動一致，這也是為什麼只有「還沒儲存時」看得到。
        # 聲部（channel/track）上色只用在「還沒排譜」的 MIDI——那時候還沒有
        # 左右手可言，音軌是唯一能分辨聲部的依據。一旦排過譜，左右手就是
        # 譜面的事實，一律以 hand 為準：track 和 hand 排譜後會分岔（實測
        # felzione 有 2.5% 的音符 track != hand），兩套上色並存就會出現同一顆
        # 音符在兩個格子裡顏色相反。
        if (self.model.is_midi_mode() and n.channel is not None
                and getattr(self.model, 'midi_unarranged', False)
                and not self.preview_mode):
            base = self._voice_base_color(n)
            # 類型只做很小的明暗變化。原本長押是 darker(135)，在單一聲部的
            # 曲子裡看起來就像「另一種紅」，會被誤認成不同音軌。
            if n.note_type == 2:
                fill = base.darker(155)
            elif n.note_type == 1:
                fill = base.lighter(135)
            elif n.note_type == 3:
                fill = base.lighter(118)
            else:
                fill = base
            outline = fill.darker(170)
            return fill, outline
        nt = n.note_type
        # 無主音（hand=2）自成一色，不併進左手。
        unassigned = int(getattr(n, 'hand', 0)) == 2
        # 位元判斷（相容官方 bitmask：10=long|skin、12=slide|skin、72=trill|skin）
        if note_is_trill(nt):
            if unassigned:
                return NOTE_NONE, NOTE_OUT_TRILL
            return (NOTE_TRILL_R, NOTE_OUT_TRILL) if n.hand == 0 else (NOTE_TRILL_L, NOTE_OUT_TRILL)
        if note_is_slide(nt):
            if unassigned:
                return NOTE_NONE, NOTE_OUT_N
            return (NOTE_SLIDE_R, NOTE_OUT_R) if n.hand == 0 else (NOTE_SLIDE_L, NOTE_OUT_L)
        if note_is_long(nt):
            if unassigned:
                return NOTE_NONE_LONG, NOTE_OUT_N
            return (NOTE_RIGHT_LONG, NOTE_OUT_R) if n.hand == 0 else (NOTE_LEFT_LONG, NOTE_OUT_L)
        if nt == 1:
            return NOTE_SOFT, NOTE_OUT_S
        if nt == 3:
            return NOTE_STAC, NOTE_OUT_S
        if unassigned:
            return NOTE_NONE, NOTE_OUT_N
        return (NOTE_RIGHT, NOTE_OUT_R) if n.hand == 0 else (NOTE_LEFT, NOTE_OUT_L)

    def _slide_head_py(self, n: GNote) -> float:
        """slide 音符 start 對應的 y 像素（含時間視窗換算）。"""
        unit_rel = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        return self._unit_to_py(unit_rel)

    def _slide_band_x_range(self, n: GNote) -> Tuple[float, float]:
        """梯形帶左右緣：比 note(0.9) 窄、置中，讓 band 像穿過音符中間的細帶。"""
        x1, x2 = self._note_display_x_range(n)
        margin = (x2 - x1) * 0.22   # band 寬 ≈ 0.56 格寬，明顯窄於 note
        return x1 + margin, x2 - margin

    def _draw_slide_bands(self, qp: QPainter) -> None:
        """每顆 slide 畫成一個梯形：跨自身 start→end 的厚度，
        由自己的鍵道滑向「下一顆（param2）」的鍵道；只連明確鏈結，不用最近退回。"""
        notes = self.model.notes
        index_map = build_slide_index_map(notes)
        if not index_map:
            return
        win = self.window_size_unit
        ws_ms, we_ms = self._window_ms()
        # 和 Pass 1 同一套視窗裁切：下面本來就會用自己的 start/end 再擋一次，
        # 這裡只是不要為了那個判斷把全譜跑過一遍。
        for n in self._notes_in_window(ws_ms - self._max_note_span_ms(), we_ms):
            if not note_is_slide(int(n.note_type)) or self._is_ghost(n):
                continue
            # 視窗裁切（以自身 start/end）
            ua = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
            ub = self.mapper.ms_to_unit(float(n.end)) - self.window_start_unit
            if (ua < 0 and ub < 0) or (ua > win and ub > win):
                continue

            # 下一顆：param2 優先，否則同手最近的下一顆（讓相鄰滑鍵連成一條）
            nxt = slide_next_note(n, notes, index_map)
            if nxt is None:
                continue

            # 從本顆尾巴 → 下一顆頭；尾巴位置預覽/編輯分開算
            xa1, xa2 = self._slide_band_x_range(n)
            xb1, xb2 = self._slide_band_x_range(nxt)
            if self.preview_mode:
                # 預覽：band 近緣用音符 start 位置。不可用固定像素偏移，否則縮小時
                # 偏移量會超過音符間距、使近緣越過遠緣 → band 過厚且材質反轉。
                y_near = self._ms_to_py(n.start)
            else:
                # 編輯/MIDI：用音符實際時長 start→end
                y_near = self._ms_to_py(n.end)
            y_far = self._ms_to_py(nxt.start)      # 下一顆頭
            # 保險夾住：near 不可越過 far（避免任何情況下的反轉）
            py_start = self._ms_to_py(n.start)
            if py_start >= y_far:
                y_near = max(y_near, y_far)
            else:
                y_near = min(y_near, y_far)
            poly = QPolygonF([
                QPointF(xa1, y_near), QPointF(xa2, y_near),
                QPointF(xb2, y_far), QPointF(xb1, y_far),
            ])
            if n.hand == 1:
                fill, edge = SLIDE_BAND_L, SLIDE_EDGE_L
            else:
                fill, edge = SLIDE_BAND_R, SLIDE_EDGE_R
            qp.setBrush(QBrush(fill))
            qp.setPen(QPen(edge, 0.8))
            qp.drawPolygon(poly)

    def _ms_to_py(self, ms: float) -> float:
        """任意毫秒 → y 像素（含時間視窗換算）。"""
        return self._unit_to_py(self.mapper.ms_to_unit(float(ms)) - self.window_start_unit)

    def _py_to_ms(self, py: float) -> float:
        """y 像素 → 毫秒（`_ms_to_py` 的反向）。"""
        return float(self.mapper.unit_to_ms(self._py_to_unit_abs(float(py))))

    def _draw_trill_mesh(self, qp: QPainter, n: GNote, x1: float, w: float,
                         selected: bool = False, use_pixmap: bool = False) -> None:
        """依 sub_note 資料把 trill 畫成 mesh：
        每個 hit 一個半格方塊（x 依音高、y 依時間），頭尾各畫一個 tap。"""
        if w <= 0:
            return
        cells = trill_sub_cells(n) or trill_fallback_cells(int(n.start), int(n.end))

        mesh_fill = NOTE_TRILL_L if n.hand == 1 else NOTE_TRILL_R
        label_cells: List[Tuple[QRectF, int]] = []
        sel_cell_rect: Optional[QRectF] = None
        for relx, relw, st, en, pit, sidx in cells:
            y0 = self._ms_to_py(st)
            y1 = self._ms_to_py(en)
            top = min(y0, y1)
            h = max(3.0, abs(y1 - y0))
            bx = x1 + relx * w
            bw = max(2.0, relw * w)
            cell = QRectF(bx + 1.0, top + 0.5, bw - 2.0, max(1.0, h - 1.0))
            is_sel_cell = (n, sidx) in self._sel_cells
            qp.setBrush(QBrush(mesh_fill.lighter(135) if is_sel_cell else mesh_fill))
            qp.setPen(QPen(SEL_OUTLINE, 2) if is_sel_cell else QPen(NOTE_OUT_TRILL, 1))
            qp.drawRect(cell)
            if is_sel_cell:
                sel_cell_rect = cell
            if pit is not None:
                label_cells.append((cell, int(pit)))
            # 記錄 hit 區域供點選（僅編輯模式且非 fallback cell）
            if sidx >= 0:
                self._trill_cell_hits.append((cell, n, sidx))

        # 音高標籤（疊在方塊上）
        if label_cells:
            qp.setPen(PITCH_TEXT)
            qp.setFont(self._font_pitch)
            for cell, pit in label_cells:
                if cell.height() >= 10 and cell.width() >= 12:
                    qp.drawText(cell, Qt.AlignCenter, str(pit))

        # 前後兩個 tap（音符 start / end）
        tap_h = 10.0
        heads = [
            QRectF(x1, self._ms_to_py(n.start) - tap_h / 2.0, w, tap_h),
            QRectF(x1, self._ms_to_py(n.end)   - tap_h / 2.0, w, tap_h),
        ]
        if use_pixmap:
            img = self._get_pix('left_note.png' if n.hand == 1 else 'right_note.png')
            if not img.isNull() and img.width() > 0:
                for hr in heads:
                    qp.drawPixmap(hr.toRect(), img)
        else:
            tap_fill, tap_out = ((NOTE_LEFT, NOTE_OUT_L) if n.hand == 1
                                 else (NOTE_RIGHT, NOTE_OUT_R))
            qp.setBrush(QBrush(tap_fill))
            qp.setPen(QPen(tap_out, 1))
            for hr in heads:
                qp.drawRect(hr)

        if selected:
            r = self._note_rect(n)
            if r is not None:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(SEL_OUTLINE, 2))
                qp.drawRect(r)

    # ── 幽靈音符 ─────────────────────────────────────────────────────
    def _ghosts_on(self) -> bool:
        return _setting_on('ghost_other_hand', True)

    def _is_ghost(self, n: 'GNote') -> bool:
        """這顆音符是不是「另一隻手」的參考影子。

        手別篩選開著的時候，被濾掉的那一手不是消失、而是變成半透明的影子：
        編右手時左手還看得到，才知道兩手會不會撞在一起。
        """
        if self.hand_filter == 'all' or self.preview_mode:
            return False
        return int(getattr(n, 'hand', 0)) != int(self.hand_filter)

    def _draw_ghost_note(self, qp: QPainter, n: 'GNote', rect) -> None:
        fill, _outline = self._note_colors(n)
        fill = QColor(fill)
        fill.setAlpha(GHOST_NOTE_ALPHA)
        edge = QColor(self._note_frame_color(n))
        edge.setAlpha(GHOST_NOTE_ALPHA + 40)
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1, Qt.DotLine))
        qp.setRenderHint(QPainter.Antialiasing, True)
        radius = min(NOTE_CORNER_RADIUS, rect.width() / 2.0, rect.height() / 2.0)
        qp.drawRoundedRect(QRectF(rect), radius, radius)
        qp.setRenderHint(QPainter.Antialiasing, False)

    def set_hand_filter(self, hand) -> None:
        """只編某一隻手：'all' / 0（右手）/ 1（左手）。

        關掉幽靈音符時就真的不畫另一手；開著的話畫成影子。兩種都不可選取。
        """
        if hand not in ('all', 0, 1):
            hand = 'all'
        if self.hand_filter == hand:
            return
        self.hand_filter = hand
        if hand != 'all':
            # 選取裡如果還留著另一手的音符，之後的操作會動到看不見的東西
            self.selected = {
                n.idx for n in self.model.notes_tree
                if n.idx in self.selected and int(getattr(n, 'hand', 0)) == int(hand)
            }
            self.selection_changed.emit(len(self.selected))
        self.update()
        self._emit_status()

    def _note_frame_color(self, n: 'GNote') -> QColor:
        """音符外框顏色。音高模式改用左右手的顏色。

        音高模式的方塊本色是聲部配色（還沒排譜的 MIDI）或力度明暗，紅藍那套
        看不出來，改了左右手畫面完全沒反應。外框不佔空間又整顆看得到，拿來
        當左右手的指示最省事。
        """
        if not self.pitch_mode:
            return NOTE_FRAME
        return HAND_FRAME_L if int(n.hand) == 1 else HAND_FRAME_R

    def _velocity_shaded(self, base: QColor, n: 'GNote') -> QColor:
        """音高模式下，把音符本色依力度調暗。

        只在音高模式做。小節／時間模式看的是「玩家要按哪裡」，把力度混進
        顏色只會干擾左右手的紅藍判讀；音高模式看的是音樂本身，力度才是
        資訊。沒有 velocity 的音符（純遊戲譜）一律當作全力度，不變色。
        """
        if not self.pitch_mode or not self._vel_shade_on:
            return base
        raw = getattr(n, 'velocity', None)
        if raw is None:
            return base
        vel = max(1, min(127, int(raw)))
        k = VELOCITY_MIN_SHADE + (1.0 - VELOCITY_MIN_SHADE) * (vel / 127.0)
        out = QColor(base)
        out.setRgb(int(base.red() * k), int(base.green() * k), int(base.blue() * k))
        out.setAlpha(base.alpha())
        return out

    def _draw_notes(self, qp: QPainter) -> None:
        qp.setFont(self._font_pitch)
        self._ensure_channel_colors()     # 聲部集合變了才重算配色
        # 設定值一幀讀一次就好。放在每顆音符的迴圈裡讀是每幀上千次 dict 查詢
        # 加上 import 開銷，在密集譜上量得出來。
        self._vel_shade_on = _velocity_shading_on()
        ghosts_on = self._ghosts_on()

        # Pass 0：slide 梯形帶（畫在音符方塊下層）
        self._draw_slide_bands(qp)

        # Pass 1：畫視窗內的音符方塊。
        # 以前是整份跑一遍、靠 `_note_rect` 回 None 濾掉畫面外的，等於每幀
        # O(全譜)——這是音符一多播放就開始頓的主因。改成先二分切出可能看得到
        # 的那一段：一顆音符要看得到必須 end >= 視窗起點，而 end 最多是
        # start + 全譜最長時值，所以往前多留一個最長時值就不會漏掉那些「頭在
        # 畫面上方外面、身體還垂進來」的長押。
        ws_ms, we_ms = self._window_ms()
        for n in self._notes_in_window(ws_ms - self._max_note_span_ms(), we_ms):
            rect = self._note_rect(n)
            if rect is None:
                continue
            if getattr(n, 'hidden', False) and not self.pitch_mode:
                # 其他模式完全不顯示隱藏音——也不進 _visible。進了的話 Pass 2
                # 會照 _visible 畫音高數字，方塊雖然沒畫、數字還是會疊在寄主
                # 上面糊成一團；順帶也讓這些音在非音高模式下不可被點選。
                continue
            if self._is_ghost(n):
                # 幽靈音符：只畫個影子當參考，不進 _visible ＝ 點不到、框不到、
                # Pass 2 也不會在它上面寫音高/力度數字。關掉的話連影子都不畫。
                if ghosts_on:
                    self._draw_ghost_note(qp, n, rect)
                continue
            self._visible.append((rect, n))
            fill, outline = self._note_colors(n)
            selected = n.idx in self.selected
            if getattr(n, 'hidden', False):
                # 音高模式：留在原本的音高位置，但畫成半透明表示不佔按鍵，
                # 並拉一條白色虛線連到寄主，才看得出來它掛在誰身上
                fill = QColor(fill); fill.setAlpha(HIDDEN_NOTE_ALPHA)
                host = self._hidden_host(n)
                hr = self._note_rect(host) if host is not None else None
                if hr is not None:
                    pen = QPen(HIDDEN_LINK_COLOR, 1, Qt.DashLine)
                    qp.setPen(pen); qp.setBrush(Qt.NoBrush)
                    qp.setRenderHint(QPainter.Antialiasing, True)
                    qp.drawLine(rect.center(), hr.center())
                    qp.setRenderHint(QPainter.Antialiasing, False)
            if note_is_trill(n.note_type):
                x1, x2 = self._note_display_x_range(n)
                self._draw_trill_mesh(qp, n, x1, x2 - x1, selected, use_pixmap=False)
                continue
            qp.setBrush(QBrush(_note_gradient(self._velocity_shaded(fill, n), rect)))
            qp.setPen(QPen(SEL_OUTLINE, 2) if selected
                      else QPen(self._note_frame_color(n), 2))
            qp.setRenderHint(QPainter.Antialiasing, True)
            radius = min(NOTE_CORNER_RADIUS, rect.width() / 2.0, rect.height() / 2.0)
            qp.drawRoundedRect(QRectF(rect), radius, radius)
            qp.setRenderHint(QPainter.Antialiasing, False)

        # Pass 2：畫所有音高文字（疊在最上層，不被其他音符遮擋，允許超出音符範圍）
        # 音高模式下夠高的音符再多畫一行力度。
        show_vel = self.pitch_mode and _velocity_numbers_on()
        for rect, n in self._visible:
            if n.pitch is None:
                continue
            cx = rect.center().x()
            cy = rect.center().y()
            vel = self._velocity_label(n) if show_vel else None
            # 有力度要畫時音高往上讓半行，兩個數字才不會疊在一起
            if vel is not None and rect.height() >= VELOCITY_TEXT_MIN_H:
                pitch_cy = cy - VELOCITY_PILL_H / 2.0
            else:
                pitch_cy = cy
                vel = None
            qp.setFont(self._font_pitch)
            qp.setPen(PITCH_TEXT)
            # 以音符中心為基準，給一個固定大小的繪製區域，不受音符尺寸限制
            text_rect = QRectF(cx - 16, pitch_cy - 8, 32, 16)
            qp.drawText(text_rect.toRect(), Qt.AlignCenter, self._pitch_label(n.pitch))
            if vel is not None:
                self._draw_velocity_text(qp, cx, cy + VELOCITY_PILL_H / 2.0, vel)

    @staticmethod
    def _velocity_label(n: GNote) -> Optional[str]:
        raw = getattr(n, 'velocity', None)
        if raw is None:
            return None
        try:
            return str(max(0, min(127, int(raw))))
        except (TypeError, ValueError):
            return None

    def _draw_velocity_text(self, qp: QPainter, cx: float, cy: float, text: str) -> None:
        """力度數字：暖橘字 + 深色描邊。

        音高數字是**黑色粗體**、力度是**橘色細體**，而且畫在音符下半部——顏色、
        字重、位置三樣都不同，不會看混。

        用描邊而不是實心底色：音高模式的音符只有一個琴鍵寬，塞一塊不透明的底
        會比音符本身還寬，看起來像一根橫槓卡在音符中間，而不是一個標籤。
        """
        qp.setFont(self._font_vel)
        rect = QRectF(cx - 18.0, cy - VELOCITY_PILL_H / 2.0,
                      36.0, VELOCITY_PILL_H).toRect()
        qp.setBrush(Qt.NoBrush)
        qp.setPen(VELOCITY_TEXT_BG)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            qp.drawText(rect.translated(dx, dy), Qt.AlignCenter, text)
        qp.setPen(VELOCITY_TEXT)
        qp.drawText(rect, Qt.AlignCenter, text)

    def _draw_alloc_overlay(self, qp: QPainter) -> None:
        if not self.alloc_active:
            return
        t_min_rel = self.alloc_time_min_u - self.window_start_unit
        t_max_rel = self.alloc_time_max_u - self.window_start_unit
        y_top    = self._unit_to_py(t_max_rel)
        y_bottom = self._unit_to_py(t_min_rel)
        # Clamp drawing to widget bounds to avoid visual overflow
        w = float(self.width())
        x_left_raw = self._key_to_px(self.alloc_target_min)
        x_right_raw = self._key_to_px(self.alloc_target_max + 1)
        x_left = max(0.0, min(w, x_left_raw))
        x_right = max(0.0, min(w, x_right_raw))
        rect_w = max(0.0, x_right - x_left)
        qp.setPen(QPen(ALLOC_COLOR, 2, Qt.DashLine))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(QRectF(x_left, y_top, rect_w, y_bottom - y_top))

    def _pitch_label(self, pitch) -> str:
        """音高數字要顯示成哪一套編號。

        內部一律用 MIDI 音高（A0=21..C8=108），但遊戲和 XML 的 `scale_piano`
        是 1..88，兩者差 20。畫面上預設顯示 scale_piano，因為那才是製譜時
        對得上遊戲的編號；`show_midi_pitch` 打開就切回 MIDI 編號。
        """
        if pitch is None:
            return ''
        value = int(pitch)
        if not self.show_midi_pitch:
            value = midi_to_official_piano_index(value)
        return str(value)

    def _keyboard_top_py(self) -> int:
        """鍵盤上緣 y —— 音高模式的鋼琴和一般模式的 28 格鍵盤共用。

        播放中釘在判定線；**停止播放時固定在畫布最下方**，這樣不管哪個檢視
        模式、哪一個分割格，鍵盤都在同一個高度，不會各自飄。
        """
        return int(max(0, self.height() - keyboard_height()))

    def _lit_lane(self, note) -> int:
        """音符經過時該亮哪一格：奇數寬亮中心，偶數寬亮中心偏右。"""
        lo, hi = int(note.min_key), int(note.max_key)
        return lo + (hi - lo + 1) // 2

    def _draw_lane_keyboard(self, qp: QPainter) -> None:
        """一般模式底部的 28 格鍵盤，照街機面板的樣子畫。

        28 = 4 組 × 7 鍵，每組的黑鍵分布是 黑黑白黑黑黑白 —— 也就是鋼琴的
        C D E F G A B，E 和 B 右上角沒有黑鍵所以是純白。黑色那塊是從鍵的
        **右上角往內縮**一小段，不是整條到邊。鍵與鍵之間留黑色縫隙、四角
        圓角，和實機面板一致。
        """
        h = self.height()
        top = self._keyboard_top_py()
        strip = keyboard_height()
        if strip <= 0:
            return
        judged = {self._lit_lane(n) for n in self._active_notes()}
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.fillRect(0, top, self.width(), strip, QColor(10, 10, 13))
        for lane in range(TOTAL_GAME_KEYS):
            x1, x2 = self._key_span(lane)
            # 鍵與鍵之間的黑縫
            kx = x1 + LANE_KEY_GAP_PX / 2.0
            kw = max(1.0, (x2 - x1) - LANE_KEY_GAP_PX)
            body = QRectF(kx, float(top) + LANE_KEY_GAP_PX / 2.0,
                          kw, float(strip) - LANE_KEY_GAP_PX)
            grad = QLinearGradient(0, body.top(), 0, body.bottom())
            if lane in judged:
                grad.setColorAt(0.0, LANE_KEY_LIT.lighter(112))
                grad.setColorAt(1.0, LANE_KEY_LIT.darker(115))
            else:
                grad.setColorAt(0.0, QColor(252, 252, 254))
                grad.setColorAt(0.80, LANE_KEY_BASE)
                grad.setColorAt(1.0, QColor(206, 206, 213))
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(grad))
            qp.drawRoundedRect(body, LANE_KEY_RADIUS, LANE_KEY_RADIUS)
            # 右上角的黑鍵區塊：E(2) 和 B(6) 沒有。
            # 它是**填滿到鍵的右緣與上緣**的一塊，不是浮在中間的長條，所以
            # 用鍵本身的圓角形狀當裁切區、再填一個直角矩形進去——這樣右上
            # 那個角會自然跟著鍵的圓角，左下兩角則保持直角。
            if lane % 7 not in (2, 6):
                bw = body.width() * LANE_BLACK_W
                bh = body.height() * LANE_BLACK_H
                clip = QPainterPath()
                clip.addRoundedRect(body, LANE_KEY_RADIUS, LANE_KEY_RADIUS)
                qp.save()
                qp.setClipPath(clip)
                black = QRectF(body.right() - bw, body.top(), bw, bh)
                bg = QLinearGradient(0, black.top(), 0, black.bottom())
                bg.setColorAt(0.0, QColor(74, 68, 66))
                bg.setColorAt(1.0, QColor(34, 30, 29))
                qp.setBrush(QBrush(bg))
                qp.setPen(Qt.NoPen)
                qp.drawRect(black)
                qp.restore()
        qp.setPen(QPen(QColor(150, 90, 200), 2))
        qp.drawLine(0, top, self.width(), top)
        qp.setBrush(Qt.NoBrush)
        qp.setRenderHint(QPainter.Antialiasing, False)

    @staticmethod
    def _sound_until(note) -> float:
        """這顆音符的鍵該亮到什麼時候。

        **只有長押才看 `end`。** 從 MIDI 轉來的 tap，`end` 仍然保留原始 MIDI
        的完整音長（可能好幾秒），拿它當亮燈長度的話 tap 的鍵會一直亮著，
        而且改 note_type 也沒用——因為改的是型別、`end` 沒變。
        """
        start = float(note.start)
        if note_is_long(note.note_type) or note_is_trill(note.note_type):
            return max(float(note.end), start + PIANO_FLASH_MS)
        return start + PIANO_FLASH_MS

    def _active_notes(self) -> list:
        """判定線當下正在發聲的音符。"""
        if self._judge_ms is None:
            return []
        now = float(self._judge_ms)
        out = []
        for note in self.model.notes_tree:
            start = float(note.start)
            if start <= now <= self._sound_until(note):
                out.append(note)
        return out

    def _piano_top_py(self) -> int:
        """音高模式鋼琴鍵盤的上緣 y。

        和判定線是同一個 y（見 `_judge_py`）——否則「音符碰到鍵盤頂端就是
        判定的瞬間」這個視覺約定就不成立。
        """
        return self._judge_py()

    def _active_pitches(self) -> dict:
        """碰到判定線的音高 → 該音符的聲部顏色。

        亮的顏色跟著聲部（channel/track）走，而不是固定一種黃色，這樣一眼
        就看得出剛剛過線的是哪一軌。
        """
        if self._judge_ms is None:
            return {}
        now = float(self._judge_ms)
        live: dict = {}
        # 只有 start <= now 的音符可能還在響，而且再早也不會早過「全譜最長的
        # 那顆」。用這個界限二分切一段出來，不必每幀掃全譜。
        for note in self._notes_in_window(now - self._max_note_span_ms(), now):
            if note.pitch is None:
                continue
            if float(note.start) <= now <= self._sound_until(note):
                live[int(note.pitch)] = self._note_colors(note)[0]
        return live

    def _draw_piano_keyboard(self, qp: QPainter) -> None:
        """音高模式底部的鋼琴鍵盤，音符落到判定線時對應的鍵會亮起。

        每個音高各佔一個欄位（白鍵寬、黑鍵窄），和上方的音符欄位共用同一套
        `_display_key_to_px`，所以鍵和音符一定對得齊。
        """
        h = self.height()
        top = self._piano_top_py()
        strip = keyboard_height()          # 高度恆定，不跟著判定線位置縮放
        if strip <= 0:
            return
        live = self._active_pitches()
        # 調外音的暗色直接做進每個鍵自己的漸層，不可以畫完再蓋一層灰。
        # 白鍵的繪製範圍本來就墊在黑鍵底下，事後蓋的話會連旁邊的黑鍵一起
        # 塗掉，鍵的形狀、邊框、黑白層次全部消失，看起來就是一塊灰色板子。
        classes = self._highlight_pitch_classes() if self._scale_highlight_on() else None

        def off_key(p: int) -> bool:
            return bool(classes) and p % 12 not in classes and live.get(p) is None

        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.fillRect(0, top, self.width(), strip, QColor(10, 10, 13))
        black_h = int(strip * 0.62)
        # 白鍵先鋪滿，黑鍵再騎上去——順序反了黑鍵會被白鍵的邊框切掉
        for i in range(PITCH_GRID_KEYS):
            pitch = PITCH_MIDI_MIN + i
            if _is_black_pitch(pitch):
                continue
            x1, x2 = self._key_draw_span(i)
            grad = QLinearGradient(0, top, 0, top + strip)
            lit = live.get(pitch)
            if lit is not None:
                grad.setColorAt(0.0, lit.lighter(125))
                grad.setColorAt(1.0, lit.darker(112))
            elif off_key(pitch):
                grad.setColorAt(0.0, PIANO_OFF_WHITE_TOP)
                grad.setColorAt(0.82, PIANO_OFF_WHITE_MID)
                grad.setColorAt(1.0, PIANO_OFF_WHITE_BOT)
            else:
                grad.setColorAt(0.0, QColor(250, 250, 252))
                grad.setColorAt(0.82, PIANO_KEY_WHITE)
                grad.setColorAt(1.0, QColor(196, 196, 204))
            qp.setPen(QPen(PIANO_OFF_EDGE if off_key(pitch) else QColor(60, 60, 68), 1))
            qp.setBrush(QBrush(grad))
            qp.drawRoundedRect(QRectF(x1, top, x2 - x1, strip), 2.0, 2.0)
            if pitch % 12 == 0 and (x2 - x1) >= 9:
                qp.setPen(QColor(110, 110, 120))
                qp.drawText(int(x1) + 2, top + strip - 4, 'C%d' % (pitch // 12 - 1))
        for i in range(PITCH_GRID_KEYS):
            pitch = PITCH_MIDI_MIN + i
            if not _is_black_pitch(pitch):
                continue
            x1, x2 = self._key_draw_span(i)
            grad = QLinearGradient(0, top, 0, top + black_h)
            lit = live.get(pitch)
            if lit is not None:
                grad.setColorAt(0.0, lit.lighter(108))
                grad.setColorAt(1.0, lit.darker(130))
            elif off_key(pitch):
                grad.setColorAt(0.0, PIANO_OFF_BLACK_TOP)
                grad.setColorAt(0.55, PIANO_OFF_BLACK_MID)
                grad.setColorAt(1.0, QColor(8, 8, 11))
            else:
                grad.setColorAt(0.0, QColor(58, 58, 68))
                grad.setColorAt(0.55, PIANO_KEY_BLACK)
                grad.setColorAt(1.0, QColor(12, 12, 16))
            qp.setPen(QPen(QColor(0, 0, 0), 1))
            qp.setBrush(QBrush(grad))
            qp.drawRoundedRect(QRectF(x1, top - 1, x2 - x1, black_h + 1), 2.0, 2.0)
        qp.setPen(QPen(QColor(150, 90, 200), 2))
        qp.drawLine(0, top, self.width(), top)
        qp.setBrush(Qt.NoBrush)
        qp.setRenderHint(QPainter.Antialiasing, False)

    # ------------------------------------------------------------------
    # 延音踏板欄
    # ------------------------------------------------------------------
    def _draw_pedal_lane(self, qp: QPainter) -> None:
        """音高模式左側的踏板欄：踩下的區間畫成一條琥珀色的長條。"""
        gut = self._pedal_lane_px()
        if gut <= 0:
            return
        h = self.height()
        bottom = self._piano_top_py()
        qp.setRenderHint(QPainter.Antialiasing, False)
        qp.fillRect(0, 0, int(gut), h, PEDAL_LANE_BG)
        qp.setPen(QPen(PEDAL_LANE_EDGE, 1))
        qp.drawLine(int(gut), 0, int(gut), h)

        spans = getattr(self.model, 'pedal_spans', None) or []
        judge = self._judge_ms
        qp.setPen(Qt.NoPen)
        qp.setRenderHint(QPainter.Antialiasing, True)
        for start_ms, end_ms in spans:
            y1 = self._ms_to_py(float(start_ms))
            y2 = self._ms_to_py(float(end_ms))
            if y1 > y2:
                y1, y2 = y2, y1
            if y2 < -8 or y1 > bottom + 8:
                continue
            y1 = max(-4.0, y1)
            y2 = min(float(bottom), y2)
            if y2 - y1 < 1.0:
                y2 = y1 + 1.0
            active = judge is not None and float(start_ms) <= float(judge) <= float(end_ms)
            qp.setBrush(PEDAL_SPAN_ACTIVE if active else PEDAL_SPAN_COLOR)
            qp.setPen(QPen(PEDAL_EDGE_LINE, 1))
            qp.drawPolygon(self._pedal_hex(y1, y2, gut))
            qp.setPen(Qt.NoPen)

        # 拖曳中的新區間 / 正在拉的邊
        drag = getattr(self, '_pedal_drag', None)
        if drag is not None:
            y1 = self._ms_to_py(min(drag))
            y2 = self._ms_to_py(max(drag))
            qp.setBrush(PEDAL_DRAG_COLOR)
            qp.drawPolygon(self._pedal_hex(min(y1, y2), max(y1, y2) + 1.0, gut))

        edge = getattr(self, '_pedal_edge_drag', None)
        if edge is not None:
            span = self.model.pedal_spans[edge[0]] if edge[0] < len(self.model.pedal_spans) else None
            if span is not None:
                ey = self._ms_to_py(float(span[0] if edge[1] == 'start' else span[1]))
                qp.setPen(QPen(PEDAL_SPAN_ACTIVE, 2))
                qp.drawLine(int(1), int(ey), int(gut - 1), int(ey))
                qp.setPen(Qt.NoPen)

    @staticmethod
    def _pedal_hex(y1: float, y2: float, gut: float) -> QPolygonF:
        """踏板區間的六角形。

        上下兩端收成尖角，**邊界落在哪一刻一眼就看得到**——圓角矩形的兩端是
        一段圓弧，踩下/放開的確切時間點被圓角糊掉了，對不準拍子。尖角的頂點
        就是那個時刻。
        """
        x1, x2 = 3.0, gut - 4.0
        mid = (x1 + x2) / 2.0
        # 端點的斜切高度；區間太短時按比例縮，才不會兩個尖角互相穿過去
        cap = min(PEDAL_HEX_CAP_PX, max(1.0, (y2 - y1) / 2.0))
        return QPolygonF([
            QPointF(mid, y1),            # 踩下的那一刻
            QPointF(x2, y1 + cap),
            QPointF(x2, y2 - cap),
            QPointF(mid, y2),            # 放開的那一刻
            QPointF(x1, y2 - cap),
            QPointF(x1, y1 + cap),
        ])

    # ── 長條尾端拖曳 ────────────────────────────────────────────────
    def _hold_tail_at(self, pos) -> Optional[GNote]:
        """游標是不是停在某條長押的尾端？是的話回傳那顆音符。

        音符的 end 畫在方塊的**上緣**（時間往下流向判定線），所以「尾巴」就是
        rect.top()。只認長押：tap 沒有長度可拉。用 `_visible` 而不是整份譜面，
        所以畫面外的、被手別篩選成幽靈的都不會被抓到。
        """
        best, best_dist = None, HOLD_TAIL_GRAB_PX
        for rect, note in self._visible:
            if not note_is_long(int(note.note_type)):
                continue
            if not (rect.left() - 2.0 <= pos.x() <= rect.right() + 2.0):
                continue
            dist = abs(float(rect.top()) - float(pos.y()))
            if dist <= best_dist:
                best, best_dist = note, dist
        return best

    def _begin_hold_tail_drag(self, note: GNote) -> None:
        self._hold_tail_note = note
        self._hold_tail_moved = False
        self.model.push_history()
        self.selected = {note.idx}
        self.selection_changed.emit(len(self.selected))
        self.update()

    def _hold_tail_steps(self, note: GNote, py: float) -> int:
        """游標在這顆長押的頭後面第幾個「放置時值」格。至少 1。

        吸附的是**長度**，不是尾端的絕對位置：長度永遠是工具列選的那個時值的
        整數倍。吸絕對位置的話，頭本來就不在格線上的音符（MIDI 匯進來的幾乎都
        是）拉出來的長度會是個怪數字。
        """
        start_unit = self.mapper.ms_to_unit(float(note.start))
        step_units = max(1e-6, self._note_duration_beats
                         * self._beat_in_units_at(start_unit))
        raw_unit = self._py_to_unit_abs(float(py))
        return max(1, int(round((raw_unit - start_unit) / step_units)))

    def _drag_hold_tail(self, pos) -> None:
        """把長押的尾端拉到游標處。

        長度吸附在「放置時值」的整數倍上（工具列那個下拉），但實際存的是毫秒
        ——變速段落裡同樣是「四分音符」，毫秒數本來就不一樣。
        """
        note = self._hold_tail_note
        if note is None:
            return
        steps = self._hold_tail_steps(note, pos.y())
        start_unit = self.mapper.ms_to_unit(float(note.start))
        step_units = max(1e-6, self._note_duration_beats
                         * self._beat_in_units_at(start_unit))
        end_ms = int(round(self.mapper.unit_to_ms(start_unit + step_units * steps)))
        end_ms = max(int(note.start) + 1, end_ms)
        if end_ms == int(note.end):
            return
        note.end = end_ms
        note.gate = max(1, end_ms - int(note.start))
        self._hold_tail_moved = True
        self.model.rebuild_display_cache()
        self._update_unit_bounds()
        self._drag_status = '長條長度 %d ms（%g × %s）' % (
            note.gate, steps, self._note_value_label(self._note_duration_beats))
        self.update()
        self._emit_status()

    @staticmethod
    def _note_value_label(beats: float) -> str:
        """拍數 → 音符值名稱，狀態列用。"""
        table = {4.0: '全音符', 2.0: '二分音符', 1.0: '四分音符', 0.5: '八分音符',
                 1.0 / 3.0: '八分三連', 0.25: '16分音符', 1.0 / 6.0: '16分三連',
                 0.125: '32分音符', 0.0625: '64分音符'}
        for value, name in table.items():
            if abs(float(beats) - value) < 1e-6:
                return name
        return '%.4g 拍' % float(beats)

    def _finish_hold_tail_drag(self) -> None:
        note = self._hold_tail_note
        self._hold_tail_note = None
        if note is None:
            return
        if not self._hold_tail_moved:
            # 只是點了一下尾巴，沒拉動：不要在 undo 堆裡留一筆空的
            if self.model.undo_stack:
                self.model.undo_stack.pop()
        else:
            self.model.rebuild_display_cache()
            self.note_edited.emit()
        self._hold_tail_moved = False
        self._drag_status = ''
        self.update()
        self._emit_status()

    def _pedal_edge_at(self, py: float) -> Optional[Tuple[int, str]]:
        """游標在哪一段踏板的哪一端附近？回傳 (區間索引, 'start'|'end')。"""
        best = None
        best_dist = PEDAL_EDGE_GRAB_PX
        for i, span in enumerate(self.model.pedal_spans or []):
            for which, ms in (('start', span[0]), ('end', span[1])):
                dist = abs(self._ms_to_py(float(ms)) - float(py))
                if dist <= best_dist:
                    best, best_dist = (i, which), dist
        return best

    def _drag_pedal_edge(self, py: float) -> None:
        """把正在拉的那一端移到游標所在時間。

        兩端不會互穿：頭最多推到尾前面 `MIN` 毫秒，反之亦然。拉的過程**不**做
        正規化（合併重疊區間），否則索引會在手上變動、抓著的那一段會突然換成
        別段；放開時才整理一次。
        """
        edge = self._pedal_edge_drag
        if edge is None:
            return
        index, which = edge
        spans = self.model.pedal_spans or []
        if not (0 <= index < len(spans)):
            self._pedal_edge_drag = None
            return
        span = spans[index]
        ms = max(0.0, self._py_to_ms(py))
        gap = 20.0
        if which == 'start':
            span[0] = min(ms, float(span[1]) - gap)
        else:
            span[1] = max(ms, float(span[0]) + gap)
        self.model.dirty = True
        self._pedal_edge_moved = True
        self.status_changed.emit(
            '踏板 %d ~ %d ms' % (int(span[0]), int(span[1])))
        self.update()

    def _pedal_lane_hit(self, px: float) -> bool:
        gut = self._pedal_lane_px()
        return gut > 0 and float(px) < gut

    # ------------------------------------------------------------------
    # 強弱欄（左手在左邊緣，右手在右邊緣）
    # ------------------------------------------------------------------
    def _dyn_lane_rect(self, hand: int) -> Optional[QRectF]:
        """某一手的強弱欄矩形；欄位關閉時回 None。

        左手放畫面左邊、右手放右邊——和演奏時兩手在鍵盤上的位置一致，不用想
        就知道哪條是哪隻手。左邊那條排在踏板欄的右側，不互相擠。
        """
        lane = self._dyn_lane_px()
        if lane <= 0:
            return None
        h = float(self._piano_top_py())
        if int(hand) == 1:
            return QRectF(self._pedal_lane_px(), 0.0, lane, h)
        return QRectF(self.width() - lane, 0.0, lane, h)

    def _dyn_scale(self, hand: int) -> Tuple[float, float]:
        """強弱欄的刻度＝這一手實際用到的力度範圍（見 dynamics_range）。

        一定要快取：畫曲線時每一個點都會問一次刻度，而 `dynamics_range` 是掃
        全譜的。1128 顆的譜實測每幀會掃八萬多次，光這一項就吃掉快四成的繪圖
        時間。任何編輯都會發 `note_edited`，快取跟著失效。
        """
        hand = int(hand)
        cache = self._dyn_scale_cache
        got = cache.get(hand)
        if got is None:
            got = cache[hand] = self.model.dynamics_range(hand)
        return got

    def _dyn_level_to_px(self, rect: QRectF, level: float, hand: int) -> float:
        """強弱值 → 欄內的 x。左手欄由右往左長，右手欄由左往右長，
        兩條就像從畫面中央往外撐開，對稱好讀。

        刻度不是固定的 1~127，而是這一手真正用到的範圍——實際只用 70~96 的譜
        用滿刻度畫會擠成一條直線，看不出任何強弱起伏。"""
        lo, hi = self._dyn_scale(hand)
        t = max(0.0, min(1.0, (float(level) - lo) / max(1e-6, hi - lo)))
        inset = 2.0
        span = max(1.0, rect.width() - inset * 2)
        if rect.left() < self.width() / 2.0:        # 左手欄
            return rect.right() - inset - t * span
        return rect.left() + inset + t * span

    def _dyn_px_to_level(self, rect: QRectF, px: float, hand: int) -> float:
        lo, hi = self._dyn_scale(hand)
        inset = 2.0
        span = max(1.0, rect.width() - inset * 2)
        if rect.left() < self.width() / 2.0:
            t = (rect.right() - inset - float(px)) / span
        else:
            t = (float(px) - rect.left() - inset) / span
        t = max(0.0, min(1.0, t))
        return max(1.0, min(127.0, lo + t * (hi - lo)))

    def _dyn_lane_hit(self, px: float) -> Optional[int]:
        """點在哪一條強弱欄上？回傳 hand，沒中回 None。"""
        for hand in (1, 0):
            rect = self._dyn_lane_rect(hand)
            if rect is not None and rect.left() <= float(px) < rect.right():
                return hand
        return None

    def _place_dynamic_mark(self, hand: int, pos, replace: bool = False) -> None:
        """在強弱欄按下/拖曳 → 放一個記號。

        y 決定時間、x 決定強弱值。`replace=True`（拖曳中）會先把剛才那一顆
        拿掉再放新的，所以整段拖曳只會留下一個記號，不會拖出一串。
        """
        rect = self._dyn_lane_rect(hand)
        if rect is None:
            return
        ms = max(0.0, self._py_to_ms(pos.y()))
        level = self._dyn_px_to_level(rect, pos.x(), hand)
        if replace and self._dyn_last_mark is not None:
            # 拖曳中：先拿掉上一格留下的那顆，整段拖曳只會留一個記號
            self.model.dynamics_remove_near(hand, self._dyn_last_mark, tolerance_ms=1.0)
        # 前面已經有記號的話預設畫成漸變，直接拖出 cresc./dim.；
        # 不要漸變的（樂譜上突然的 f）在右鍵選單裡切成「維持」。
        marks = self.model.dynamics_marks(hand)
        ramp = any(m[0] < ms for m in marks)
        self.model.dynamics_add(hand, ms, level, ramp=ramp)
        self._dyn_last_mark = ms
        self.status_changed.emit(
            '%s 強弱記號 %s（%d）@ %d ms'
            % ('左手' if int(hand) == 1 else '右手',
               self.model.dynamic_mark_name(level), int(round(level)), int(ms)))
        self.update()

    def _draw_dynamics_lanes(self, qp: QPainter) -> None:
        for hand in (0, 1):
            self._draw_dynamics_lane(qp, hand)

    # ------------------------------------------------------------------
    # 欄位表頭：告訴使用者哪一條是什麼
    # ------------------------------------------------------------------
    #: (取得矩形的方法, 標籤, 底色, tooltip)
    def _lane_headers(self):
        """每條欄位的標籤方塊。

        放在**鍵盤旁邊**（欄位下方、和鍵盤同一條水平帶）而不是欄位頂端：頂端
        那排色塊會壓在譜面最上方，一眼看過去很雜；鍵盤那一帶本來就是空的，
        標籤擺過去既不擋譜、也和它標示的欄位對得起來。
        """
        top = float(self._piano_top_py())
        # 鍵盤高度可以在偏好設定調小；標籤加上底下兩行刻度不能凸出畫布外
        avail = max(18.0, float(self.height()) - top - 24.0)
        height = min(LANE_HEADER_PX, avail)
        out = []
        gut = self._pedal_lane_px()
        if gut > 0:
            out.append((QRectF(0.0, top, gut, height), '踏板',
                        PEDAL_SPAN_COLOR,
                        '延音踏板\n\n空白處拖曳 = 新增一段；點一下既有區間 = 刪除。\n'
                        '六角形的上下尖端就是踩下／放開的時刻，可以直接拉。'))
        for hand, label, colour in ((1, '左力道曲線', DYN_LINE_LEFT),
                                    (0, '右力道曲線', DYN_LINE_RIGHT)):
            rect = self._dyn_lane_rect(hand)
            if rect is None:
                continue
            lo, hi = self._dyn_scale(hand)
            out.append((QRectF(rect.left(), top, rect.width(), height),
                        label, colour,
                        '%s力度曲線（刻度 %d~%d）\n\n'
                        '拖曳 = 放記號：上下決定時間、左右決定強弱。\n'
                        '右鍵 → 強弱記號 可以選 pp~fff、產生曲線、套用。'
                        % ('左手' if hand == 1 else '右手',
                           int(round(lo)), int(round(hi)))))
        return out

    def _draw_lane_headers(self, qp: QPainter) -> None:
        """在每條欄位頂端釘一個色塊標籤。

        欄位只有 18~26px 寬，光看顏色分不出誰是踏板誰是力度；標籤釘在最上面
        不跟著捲動，隨時看得到。
        """
        if not self.pitch_mode or self.overlay_role == 'top':
            return
        qp.setRenderHint(QPainter.Antialiasing, False)
        qp.setFont(self._font_vel)
        for rect, label, colour, _tip in self._lane_headers():
            chip = QColor(colour)
            chip.setAlpha(230)
            qp.fillRect(rect, chip)
            qp.setPen(QPen(LANE_HEADER_EDGE, 1))
            qp.drawLine(int(rect.left()), int(rect.top()),
                        int(rect.right()), int(rect.top()))
            # 欄位只有 18~26px 寬，橫排放不下「左力道曲線」這種完整詞；轉 90 度
            # 沿著欄位直排就放得下，也不用把欄位加寬去吃掉琴鍵的空間。
            qp.setPen(LANE_HEADER_TEXT)
            qp.save()
            qp.translate(rect.center())
            qp.rotate(-90.0)
            span = QRectF(-rect.height() / 2.0, -rect.width() / 2.0,
                          rect.height(), rect.width())
            qp.drawText(span.toRect(), Qt.AlignCenter, label)
            qp.restore()

        # 力度欄的刻度數字接在色塊下面：強的在上、弱的在下，和曲線往外＝強的
        # 方向一致，不用回頭想哪一端是大的。
        qp.setPen(DYN_SCALE_TEXT)
        for hand in (0, 1):
            lane = self._dyn_lane_rect(hand)
            if lane is None:
                continue
            lo, hi = self._dyn_scale(hand)
            headers = {h[1]: h[0] for h in self._lane_headers()}
            any_rect = next(iter(headers.values()), None)
            base = ((any_rect.bottom() + 1.0) if any_rect is not None
                    else float(self._piano_top_py()) + LANE_HEADER_PX + 1.0)
            for i, value in enumerate((hi, lo)):
                row = QRectF(lane.left(), base + i * 11.0, lane.width(), 11.0)
                qp.drawText(row.toRect(), Qt.AlignCenter, str(int(round(value))))

    def _lane_tooltip_at(self, px: float, py: float) -> str:
        """游標所在欄位的說明；不在欄位上回空字串。"""
        if not self.pitch_mode:
            return ''
        for rect, _label, _colour, tip in self._lane_headers():
            if rect.left() <= float(px) < rect.right():
                return tip
        return ''

    def _dynamics_note_envelope(self, hand: int):
        """可見範圍內、這一手每個起音的力度上下界 [(ms, 最小, 最大), ...]。

        同一時刻常常不只一個力度——旋律音重、內聲部輕。取平均會把這個差距抹掉，
        所以保留 min/max，畫成兩條線（相同時兩條會重合，看起來就是一條）。
        """
        ws_ms, we_ms = self._window_ms()
        buckets: Dict[int, List[float]] = {}
        # notes 是照 start 排好的，用二分找出視窗那一段就好。整份掃過去是每幀
        # O(全譜)，長曲子在播放時就是這樣一點一點卡住的。
        for note in self._notes_in_window(ws_ms - 1, we_ms + 1):
            if int(note.hand) != int(hand) or note.velocity is None:
                continue
            buckets.setdefault(int(note.start), []).append(float(note.velocity))
        return [(ms, min(v), max(v)) for ms, v in sorted(buckets.items())]

    def _draw_dynamics_note_contour(self, qp: QPainter, rect: QRectF,
                                    hand: int, colour: QColor) -> None:
        """把這一手音符的實際力度畫出來（唯讀的「目前狀態」）。

        同一時刻有不同力度時畫成**兩條線**（上界＝最重、下界＝最輕）並在中間
        淡淡填色；力度一致時兩條重合，看起來就是一條。只取可見範圍內的音符，
        密集譜也不會為了畫欄位去掃整份譜面。
        """
        env = self._dynamics_note_envelope(hand)
        if not env:
            return
        faded = QColor(colour)
        faded.setAlpha(150)
        qp.setRenderHint(QPainter.Antialiasing, True)

        if len(env) < 2:
            ms, lo, hi = env[0]
            y = self._ms_to_py(ms)
            qp.setPen(Qt.NoPen)
            qp.setBrush(faded)
            for level in ({lo, hi}):
                qp.drawEllipse(QPointF(self._dyn_level_to_px(rect, level, hand), y),
                               2.0, 2.0)
            qp.setRenderHint(QPainter.Antialiasing, False)
            return

        lows = [QPointF(self._dyn_level_to_px(rect, lo, hand), self._ms_to_py(ms))
                for ms, lo, _hi in env]
        highs = [QPointF(self._dyn_level_to_px(rect, hi, hand), self._ms_to_py(ms))
                 for ms, _lo, hi in env]
        spread = any(abs(hi - lo) > 0.5 for _ms, lo, hi in env)

        if spread:
            band = QColor(colour)
            band.setAlpha(45)
            qp.setPen(Qt.NoPen)
            qp.setBrush(band)
            qp.drawPolygon(QPolygonF(highs + list(reversed(lows))))

        qp.setBrush(Qt.NoBrush)
        # 實線（只是淡）：虛線留給那條平均參考線，兩者才分得出誰是資料誰是刻度
        qp.setPen(QPen(faded, 1))
        qp.drawPolyline(QPolygonF(highs))
        if spread:
            qp.drawPolyline(QPolygonF(lows))
        qp.setPen(Qt.NoPen)
        qp.setBrush(faded)
        for p in highs:
            qp.drawEllipse(p, 1.5, 1.5)
        if spread:
            for p in lows:
                qp.drawEllipse(p, 1.5, 1.5)
        qp.setRenderHint(QPainter.Antialiasing, False)

    def _draw_dynamics_lane(self, qp: QPainter, hand: int) -> None:
        rect = self._dyn_lane_rect(hand)
        if rect is None:
            return
        colour = DYN_LINE_LEFT if int(hand) == 1 else DYN_LINE_RIGHT

        qp.setRenderHint(QPainter.Antialiasing, False)
        qp.fillRect(rect, DYN_LANE_BG)
        qp.setPen(QPen(DYN_LANE_EDGE, 1))
        edge_x = int(rect.left()) if int(hand) == 0 else int(rect.right())
        qp.drawLine(edge_x, 0, edge_x, int(rect.height()))

        # 參考線：這一手目前的平均力度，也就是「倍率 = 1」的位置。曲線壓在這
        # 條線上代表套用後不變，偏外側是加強、偏內側是減弱——沒有這條線就只
        # 看得到絕對高低，看不出來到底動了多少。
        baseline = self.model.dynamics_baseline(hand)
        if baseline > 0:
            bx = self._dyn_level_to_px(rect, baseline, hand)
            qp.setPen(QPen(DYN_BASELINE_COLOR, 1, Qt.DashLine))
            qp.drawLine(int(bx), 0, int(bx), int(rect.height()))

        marks = self.model.dynamics_marks(hand)
        if not marks:
            # 還沒畫過曲線 → 直接把**音符現在的力度**描出來，一眼看到目前的
            # 強弱長什麼樣。這條是唯讀的推導線（虛線），要編輯的話用右鍵
            # 「從音符力度產生曲線」把它變成真的記號。
            self._draw_dynamics_note_contour(qp, rect, hand, colour)
            return

        # 曲線取樣：每 3px 一點，直接照 dynamics_level_at 的階梯/漸變規則走，
        # 畫出來的形狀就是套用時真正用的那條線。
        top, bottom = 0.0, rect.height()
        pts: List[QPointF] = []
        y = top
        while y <= bottom:
            level = self.model.dynamics_level_at(hand, self._py_to_ms(y))
            if level is not None:
                pts.append(QPointF(self._dyn_level_to_px(rect, level, hand), y))
            y += 3.0
        if len(pts) < 2:
            return

        qp.setRenderHint(QPainter.Antialiasing, True)
        base_x = rect.right() if int(hand) == 1 else rect.left()
        fill = QPolygonF([QPointF(base_x, pts[0].y())] + pts
                         + [QPointF(base_x, pts[-1].y())])
        shade = QColor(colour)
        shade.setAlpha(DYN_FILL_ALPHA)
        qp.setPen(Qt.NoPen)
        qp.setBrush(shade)
        qp.drawPolygon(fill)
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(colour, 2))
        qp.drawPolyline(QPolygonF(pts))

        # 記號本身：小方點 + pp/mf/f 之類的名字
        qp.setFont(self._font_vel)
        for ms, level, ramp in marks:
            my = self._ms_to_py(float(ms))
            if my < -12 or my > bottom + 12:
                continue
            mx = self._dyn_level_to_px(rect, level, hand)
            qp.setPen(Qt.NoPen)
            qp.setBrush(colour)
            qp.drawEllipse(QPointF(mx, my), 3.0, 3.0)
            qp.setBrush(Qt.NoBrush)
            qp.setPen(DYN_MARK_TEXT)
            label = self.model.dynamic_mark_name(level) + ('〳' if ramp else '')
            tr = QRectF(rect.left(), my - 12.0, rect.width(), 11.0)
            qp.drawText(tr.toRect(), Qt.AlignCenter, label)
        qp.setRenderHint(QPainter.Antialiasing, False)

    def _draw_judge_line(self, qp: QPainter) -> None:
        """判定線一律畫在鍵盤上緣。`set_judge_line` 保證視窗已經捲到位。"""
        if self._judge_ms is None:
            return
        py = self._judge_py()
        qp.setPen(QPen(JUDGELINE_COLOR, 2))
        qp.drawLine(0, py, self.width(), py)

    def _draw_rubber(self, qp: QPainter) -> None:
        if not self._is_rubbing:
            return
        # If we have stored absolute unit positions, compute current pixel y
        if self._rubber_start_u is not None and self._rubber_end_u is not None:
            try:
                rel_s = float(self._rubber_start_u) - float(self.window_start_unit)
                rel_e = float(self._rubber_end_u)   - float(self.window_start_unit)
                y1 = int(self._unit_to_py(rel_s))
                y2 = int(self._unit_to_py(rel_e))
            except Exception:
                return
            # preserve original x positions if available
            x1 = int(self._rubber_start.x()) if self._rubber_start is not None else 0
            x2 = int(self._rubber_end.x())   if self._rubber_end is not None else self.width()
            r = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()
        else:
            if not self._rubber_start or not self._rubber_end:
                return
            r = QRect(self._rubber_start, self._rubber_end).normalized()
        qp.setPen(QPen(RUBBER_COLOR, 1, Qt.DashLine))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(r)

    def _draw_drag_info(self, qp: QPainter) -> None:
        delta = int(round(self._drag_cur_delta_ms))
        snap  = int(round(self._drag_snap_ms)) if self._drag_snap_ms > 0 else 0
        pre   = '+' if delta >= 0 else ''
        qp.setPen(QColor(255, 200, 0))
        qp.setFont(QFont('Consolas', 9))
        qp.drawText(8, self.height() - 8,
                    f'Ctrl+拖曳複製  Δ{pre}{delta}ms  snap={snap}ms')

    # ==================================================================
    # 預覽模式繪製
    # ==================================================================

    def _get_pix(self, name: str) -> QPixmap:
        """懶載入 graphic/ 資料夾的圖片，並快取。
        素材為 nos-clone 的 Glass 音符（右=紅、左=藍）。"""
        if name not in self._pix_cache:
            path = os.path.join(os.path.dirname(__file__), 'graphic', name)
            self._pix_cache[name] = QPixmap(path)
        return self._pix_cache[name]

    # 原版 note 幀：kind → (資料夾前綴, 檔名前綴)；幀號 = note 寬度(1~10 鍵)
    _ORIG_NOTE_SPEC = {
        'white':       ('white',       'w'),
        'white_piano': ('white_piano', 'w_piano'),
        'glissando':   ('glissando',   'g'),
        'trill':       ('trill',       'tr'),
    }

    def _get_note_frame(self, kind: str, hand: int, width: int) -> QPixmap:
        """載入原版 note 幀（graphic/orig/…），依 note 寬度(1~10)與左右手挑對應幀。"""
        folder, prefix = self._ORIG_NOTE_SPEC.get(kind, ('white', 'w'))
        lr = 'l' if int(hand) == 1 else 'r'
        idx = max(1, min(10, int(width)))
        key = f'orig/{folder}_{lr}/{prefix}_{lr}_{idx:02d}'
        if key not in self._pix_cache:
            path = os.path.join(
                os.path.dirname(__file__), 'graphic', 'orig',
                f'{folder}_{lr}', f'{prefix}_{lr}_{idx:02d}.png')
            self._pix_cache[key] = QPixmap(path)
        return self._pix_cache[key]

    def _preview_note_xw(self, n: GNote, scale: float):
        """回傳 (x, draw_w)：以 scale 縮放後置中於原始格寬內。"""
        x1, x2 = self._note_display_x_range(n)
        full_w = x2 - x1
        draw_w = full_w * scale
        x = x1 + (full_w - draw_w) * 0.5
        return x, draw_w

    def _preview_hold_body(self, qp: QPainter, n: GNote) -> None:
        """Hold 主體：淺紅(右)/淺藍(左) 半透明 mesh 條，垂直拉伸至 endtime。"""
        rect = self._preview_hold_body_rect(n)
        if rect is None:
            return
        if n.hand == 1:
            fill = QColor(150, 200, 255, 150)   # 淺藍（左手）
            edge = QColor(120, 200, 255, 220)
        else:
            fill = QColor(255, 170, 170, 150)   # 淺紅（右手）
            edge = QColor(255, 140, 140, 220)
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1.5))
        qp.drawRoundedRect(rect, 4.0, 4.0)

    def _preview_trill_body(self, qp: QPainter, n: GNote) -> None:
        """預覽：中央直向方形 mesh（類似 hold）+ 左右六邊形節點 + 頭尾 tap 圖。"""
        x1z, x2z = self._note_display_x_range(n)
        zone_w = float(x2z - x1z)
        if zone_w <= 0:
            return
        cells = trill_sub_cells(n) or trill_fallback_cells(int(n.start), int(n.end))
        cells = sorted(cells, key=lambda c: c[2])
        if not cells:
            return

        cx = (x1z + x2z) / 2.0
        tapc = TRILL_TAP_L if n.hand == 1 else TRILL_TAP_R
        y_start = self._ms_to_py(n.start)
        y_end   = self._ms_to_py(n.end)
        ytop, ybot = (min(y_start, y_end), max(y_start, y_end))

        # 預覽固定「向外滿」：依序左右交替、整個區寬滿版，不看內部音符排布
        hx1, hx2 = x1z, x2z
        inset = min(zone_w * 0.15, 16.0)
        qp.setPen(Qt.NoPen)
        for i, (relx, relw, st, en, pit, sidx) in enumerate(cells):
            outer_x = x1z if (i % 2 == 0) else x2z
            yt = self._ms_to_py(st)
            yn = self._ms_to_py(cells[i + 1][2] if i + 1 < len(cells) else en)
            a, b = (min(yt, yn), max(yt, yn))
            pad = max(2.0, (b - a) * 0.15)
            a -= pad
            b += pad
            ymid = (a + b) / 2.0
            # 漸層：外尖端實心 → 到中線急遽淡出，過中線僅剩極淡的尾巴
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

        # 頭尾 tap：用原版 trill 幀（依 trill 寬度）；開頭實心、尾端空心=淡化
        trill_w = abs(int(n.max_key) - int(n.min_key)) + 1
        img = self._get_note_frame('trill', n.hand, trill_w)
        if not img.isNull() and img.width() > 0:
            th = 16.0
            qp.setOpacity(1.0)
            qp.drawPixmap(QRectF(x1z, y_start - th / 2.0, zone_w, th).toRect(), img)
            qp.setOpacity(0.35)
            qp.drawPixmap(QRectF(x1z, y_end - th / 2.0, zone_w, th).toRect(), img)
            qp.setOpacity(1.0)

    def _preview_head_rect(self, n: GNote) -> Optional[QRectF]:
        start_u = self.mapper.ms_to_unit(float(n.start)) - self.window_start_unit
        if start_u > self.window_size_unit + 1.0 or start_u < -1.0:
            return None
        x, draw_w = self._preview_note_xw(n, 0.9)
        draw_h = max(float(MIN_NOTE_HEIGHT_PX), float(PREVIEW_PX))
        start_py = self._unit_to_py(start_u)
        # 以 startTime 為中線：圖示垂直置中於 start 位置
        cy = start_py - draw_h / 2.0
        return QRectF(float(x), float(cy), float(draw_w), float(draw_h))

    def _preview_hold_body_rect(self, n: GNote) -> Optional[QRectF]:
        rect = self._note_rect(n)
        if rect is None or rect.height() < 1:
            return None
        # mesh 比 note 頭(0.9)窄一些，畫在音符後面（低圖層）
        x, draw_w = self._preview_note_xw(n, 0.55)
        return QRectF(float(x), float(rect.top()), float(draw_w), float(rect.height()))

    def _preview_stac_rect(self, n: GNote) -> Optional[QRectF]:
        """staccato 指示箭頭的位置——繪製與框選共用這一份幾何。"""
        rect = self._preview_head_rect(n)
        if rect is None:
            return None
        side = float(rect.width())
        return QRectF(
            float(rect.left()), float(rect.top()) - side * 0.95, side, side
        )

    def _preview_stac_v(self, qp: QPainter, n: GNote) -> None:
        """staccato 標記：在音符正上方畫遊戲本體的指示箭頭。

        素材直接取自 Nostalgia-clone 的 Note.prefab —— 該 prefab 的
        `rightStaccatoIndicatorSprite` / `leftStaccatoIndicatorSprite` 綁的是
        `RightStacArrow-v2.png` / `LeftStacArrow-v2.png`（右=紅、左=藍），
        不是舊的純色 `RightStac.png` / `LeftStac.png`（那兩張在遊戲裡已經
        沒有任何 prefab 參照）。舊版這裡是手繪 chevron，形狀對但少了分層
        描邊和拖尾，跟遊戲畫面對不起來。
        """
        rect = self._preview_stac_rect(n)
        if rect is None:
            return
        img = self._get_pix(
            'LeftStacArrow-v2.png' if int(n.hand) == 1 else 'RightStacArrow-v2.png'
        )
        if not img.isNull():
            # 素材是正方形，箭頭尖端朝上、拖尾朝下（朝向音符），等比例貼上
            qp.drawPixmap(rect.toRect(), img)

    def _preview_part_rects(self, n: GNote) -> List[QRectF]:
        parts: List[QRectF] = []
        head = self._preview_head_rect(n)
        if head is not None:
            parts.append(head)
        if note_is_long(n.note_type) or note_is_trill(n.note_type):
            body = self._preview_hold_body_rect(n)
            if body is not None:
                parts.append(body)
                # 額外加入 hold 尾端提示，方便辨識「頭尾都選到」
                tail_h = max(6.0, min(18.0, head.height() * 0.35 if head is not None else 10.0))
                parts.append(QRectF(body.left(), body.top(), body.width(), tail_h))
        if n.note_type == 3:
            stac = self._preview_stac_rect(n)
            if stac is not None:
                parts.append(stac)
        return parts

    def _preview_hit_rect(self, n: GNote) -> Optional[QRectF]:
        parts = self._preview_part_rects(n)
        if not parts:
            return None
        r = parts[0]
        for p in parts[1:]:
            r = r.united(p)
        return r

    def _preview_note_head(self, qp: QPainter, n: GNote) -> None:
        """Note head：0.9 寬、原圖比例高度 clamp 至 rect。
        全部底部對齊於 starttime（rect.bottom）。
        stac 這裡畫 tap 圖（left/right_note），指示箭頭在 Pass 3 另行疊上。
        """
        nt, hand = n.note_type, n.hand
        if note_is_trill(nt):
            return  # trill 的頭尾 tap 由 _preview_trill_body 負責
        # 原版 note 幀依 note 寬度(鍵數)挑對應圖
        width = abs(int(n.max_key) - int(n.min_key)) + 1
        if nt == 3:
            # staccato：底圖用 white 幀（和 tap 一致），Pass 3 再疊 V 型標記
            img = self._get_note_frame('white', hand, width)
        elif nt == 1:                                 # soft → 弱音幀
            img = self._get_note_frame('white_piano', hand, width)
        elif note_is_slide(nt):                       # slide → glissando 幀
            img = self._get_note_frame('glissando', hand, width)
        else:                                         # tap(0) / hold head(2) → white 幀
            img = self._get_note_frame('white', hand, width)
        if img.isNull() or img.width() == 0:
            return
        rect = self._preview_head_rect(n)
        if rect is None:
            return
        qp.drawPixmap(rect.toRect(), img)

    def _draw_notes_preview(self, qp: QPainter) -> None:
        """預覽模式的音符繪製：依 note_type 使用 graphic/ 圖片。"""
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Pass 0：slide 梯形帶（最低圖層）
        self._draw_slide_bands(qp)
        # Pass 1：hold 主體 / trill 顫音條（低圖層）
        for n in self.model.notes:
            if note_is_trill(n.note_type):
                self._preview_trill_body(qp, n)
            elif note_is_long(n.note_type):
                self._preview_hold_body(qp, n)
        # Pass 2：所有 note head（tap / soft / hold head）；stac 的層也在這裡畫 tap 底層
        for n in self.model.notes:
            self._preview_note_head(qp, n)
        # Pass 3：staccato 標記 → V 型（chevron），畫在音符正上方
        for n in self.model.notes:
            if n.note_type == 3:
                self._preview_stac_v(qp, n)

        # Pass 4：建立 hit-test 區域 + 選取外框（圍繞圖示）
        qp.setPen(QPen(SEL_OUTLINE, 2))
        qp.setBrush(Qt.NoBrush)
        for n in self.model.notes:
            hit_rect = self._preview_hit_rect(n)
            if hit_rect is None:
                continue
            # 建立 hit-test 區域（選取外框改於 paintEvent 的 overlay 之後繪製）
            self._visible.append((hit_rect, n))
        qp.setRenderHint(QPainter.SmoothPixmapTransform, False)

    def _draw_preview_overlay(self, qp: QPainter) -> None:
        """半透明遙罩，視覺提示目前為不可編輯的預覽模式。"""
        qp.fillRect(self.rect(), QColor(18, 18, 30, 100))

    # ==================================================================
    # 滑鼠
    # ==================================================================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        self._shift_tap = False   # Shift+點擊/拖曳不算「輕點 Shift」
        pos = event.pos()

        # 踏板欄要擋在放置模式前面：不擋的話點左邊那條會變成在最低音區放音符。
        if self._pedal_lane_hit(pos.x()):
            if event.button() == Qt.LeftButton:
                edge = self._pedal_edge_at(pos.y())
                if edge is not None:
                    # 抓住某一段的頭或尾 → 拉這一端，不是開新的一段
                    self._pedal_edge_drag = edge
                    self._pedal_edge_moved = False
                    self.model.push_history()
                else:
                    ms = self.mapper.unit_to_ms(self._py_to_unit_abs(pos.y()))
                    self._pedal_drag = (float(ms), float(ms))
                self.update()
            return

        # 強弱欄同理：左鍵放/拖記號，右鍵在選單裡處理
        dyn_hand = self._dyn_lane_hit(pos.x())
        if dyn_hand is not None:
            if event.button() == Qt.LeftButton:
                self._dyn_drag = int(dyn_hand)
                self._dyn_last_mark = None
                self.model.push_history()
                self._place_dynamic_mark(int(dyn_hand), pos)
            return

        # ── 長條尾端：抓住就拉長度 ─────────────────────────────────
        # 放在放置模式與音階輔助**前面**：兩個模式下游標停在尾巴上時，使用者
        # 的意圖是改長度，不是在那裡再放一顆音符。
        if (event.button() == Qt.LeftButton and not self.alloc_active
                and not self.preview_mode):
            tail = self._hold_tail_at(pos)
            if tail is not None:
                self._begin_hold_tail_drag(tail)
                return

        # ── 音階輔助模式：按住往上拖決定音數 ───────────────────────
        if self._pattern_mode and not self.alloc_active:
            if event.button() == Qt.LeftButton:
                self._begin_pattern_drag(pos)
                return

        # ── 放置音符模式 ───────────────────────────────────────────
        if self._note_input_mode and not self.alloc_active:
            if event.button() == Qt.LeftButton:
                self._place_note_at(pos)
                return

        # 預覽模式：允許選取（點擊/框選），其餘互動不開放
        if self.preview_mode:
            if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
                ctrl_hit = self._hit_test(pos)
                if ctrl_hit is not None:
                    if ctrl_hit.idx in self.selected:
                        self.selected.discard(ctrl_hit.idx)
                    else:
                        self.selected.add(ctrl_hit.idx)
                    self.selection_changed.emit(len(self.selected))
                    self.update()
                    return
            if event.button() == Qt.LeftButton:
                self._rubber_start = pos
                self._rubber_end = pos
                self._is_rubbing = True
            return

        # 預覽模式下，除放置模式外不接受其他編輯互動
        if self.preview_mode:
            return

        # ── Alloc 模式：邊界拖曳 ────────────────────────────────────
        if self.alloc_active:
            if event.button() == Qt.LeftButton:
                xk  = self._px_to_key(pos.x())
                yu  = self._py_to_unit_abs(pos.y())
                xthr, ythr = 0.6, 0.15
                if   abs(xk - self.alloc_target_min)         <= xthr:
                    self._alloc_drag_edge = ('x', 'min')
                elif abs(xk - (self.alloc_target_max + 1))   <= xthr:
                    self._alloc_drag_edge = ('x', 'max')
                elif abs(yu - self.alloc_time_min_u)          <= ythr:
                    self._alloc_drag_edge = ('y', 'min')
                elif abs(yu - self.alloc_time_max_u)          <= ythr:
                    self._alloc_drag_edge = ('y', 'max')
                else:
                    self._alloc_drag_edge = None
            return

        # ── 左鍵 + Ctrl：切換單一音符選取，或空地框選（加法模式）───────────
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            ctrl_hit = self._hit_test(pos)
            if ctrl_hit is not None:
                # 點到音符 → 切換選取（已選取則取消，未選取則加入）
                if ctrl_hit.idx in self.selected:
                    self.selected.discard(ctrl_hit.idx)
                else:
                    self.selected.add(ctrl_hit.idx)
                self.selection_changed.emit(len(self.selected))
                self.update()
                return
            # Ctrl + 空地 → 由下方 rubber band 處理（加法模式）
            # Ctrl + 空地 → 由下方 rubber band 處理（加法模式）

        # ── 左鍵：框選 ────────────────────────────────────────────────
        if event.button() == Qt.LeftButton:
            # Shift + 點音符：從先前 anchor 到本次點選建立時間範圍選取
            if event.modifiers() & Qt.ShiftModifier:
                hit = self._hit_test(pos)
                if hit is not None:
                    anchor_ms = None
                    if getattr(self, '_last_select_anchor_ms', None) is not None:
                        anchor_ms = float(self._last_select_anchor_ms)
                    elif self.selected:
                        picks = [n for n in self.model.notes if n.idx in self.selected]
                        if picks:
                            anchor_ms = min(float(n.start) for n in picks)
                    if anchor_ms is None:
                        anchor_ms = float(hit.start)
                    start_range = min(anchor_ms, float(hit.start))
                    end_range   = max(anchor_ms, float(hit.end))
                    new_sel = {n.idx for n in self.model.notes if float(n.start) >= start_range and float(n.start) <= end_range}
                    self.selected = new_sel
                    self._last_select_anchor_ms = anchor_ms
                    self.selection_changed.emit(len(self.selected))
                    self.update()
                    return
            # 若 time_uniform 模式且靠近小節線，啟動小節線拖曳
            if self.time_uniform:
                ws_ms, we_ms = self.mapper.window_ms_range(self.window_start_unit, self.window_size_unit)
                beats = self.model.get_beat_entries()
                if beats:
                    thr = 6  # pixel threshold
                    closest = None
                    for idx, bms in beats:
                        # 找 bar start
                        measure_idx = self.model.get_measure_at_ms(bms)
                        # bar start 的 y
                        unit = self.mapper.ms_to_unit(float(bms)) - self.window_start_unit
                        py = int(self._unit_to_py(unit))
                        if abs(py - pos.y()) <= thr:
                            # 找到最靠近的 barline，記得不是第一個（需要前一小節可調整）
                            closest = (measure_idx, py, bms)
                            break
                    if closest is not None:
                        measure_idx, py, bms = closest
                        if measure_idx > 0:
                            prev_idx = measure_idx - 1
                            start_ms, end_ms = self.model.get_measure_time_range(prev_idx)
                            if start_ms is not None and end_ms is not None:
                                # 開始拖曳：目標為前一小節（改變前一小節 BPM）
                                self._barline_dragging = True
                                self._barline_drag_measure = prev_idx
                                self._barline_drag_start_ms = start_ms
                                self._barline_drag_orig_end_ms = end_ms
                                self._barline_drag_py = py
                                self._drag_status = '拖曳小節線'
                                self._emit_status()
                                return
            # 預設行為：框選
            self._rubber_start = pos
            self._rubber_end   = pos
            # store absolute unit positions so rubber follows scrolling
            try:
                u = self._py_to_unit_abs(pos.y())
            except Exception:
                u = self.window_start_unit
            self._rubber_start_u = u
            self._rubber_end_u = u
            self._is_rubbing   = True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.pos()
        self._last_mouse_unit = self._py_to_unit_abs(pos.y())

        if getattr(self, '_pedal_edge_drag', None) is not None:
            self._drag_pedal_edge(pos.y())
            return

        if self._hold_tail_note is not None and (event.buttons() & Qt.LeftButton):
            self._drag_hold_tail(pos)
            return

        # 游標停在長條尾端就換成上下箭頭，讓人知道那裡可以拉
        if not self.alloc_active and not self.preview_mode:
            over = self._hold_tail_at(pos) is not None
            if over != self._hold_tail_hover:
                self._hold_tail_hover = over
                if over:
                    self.setCursor(Qt.SizeVerCursor)
                else:
                    self.setCursor(Qt.CrossCursor if self._note_input_mode
                                   else Qt.ArrowCursor)

        if self._pattern_drag is not None:
            self._update_pattern_drag(pos)
            return

        # 停在欄位上就顯示那條欄位在幹嘛（顏色和一個字說不完整套操作方式）
        tip = self._lane_tooltip_at(pos.x(), pos.y())
        if tip != self.toolTip():
            self.setToolTip(tip)

        if self._pedal_drag is not None:
            ms = self.mapper.unit_to_ms(self._py_to_unit_abs(pos.y()))
            self._pedal_drag = (self._pedal_drag[0], float(ms))
            self.update()
            return

        if getattr(self, '_dyn_drag', None) is not None:
            # 上下決定時間、左右決定強弱，一次拖曳就能把記號放到位
            self._place_dynamic_mark(int(self._dyn_drag), pos, replace=True)
            return

        # 放置音符模式：記錄游標並更新 snap 指示線
        if self._note_input_mode:
            self._note_input_hover = QPoint(pos)
            # 音高模式的格線只在滑鼠附近顯示，所以要跟著游標走
            if self.pitch_mode:
                self._grid_focus_slot = int(self._px_to_display_key(pos.x()))
            if self._input_drag_note is not None and (event.buttons() & Qt.LeftButton):
                self._drag_extend_note(pos)
                return
            self.update()

        if self.preview_mode:
            if self._is_rubbing and (event.buttons() & Qt.LeftButton):
                self._rubber_end = pos
                try:
                    self._rubber_end_u = self._py_to_unit_abs(pos.y())
                except Exception:
                    pass
                self.update()
            return

        if self.alloc_active and self._alloc_drag_edge:
            axis, side = self._alloc_drag_edge
            raw = (self._px_to_key(pos.x()) if axis == 'x'
                   else self._py_to_unit_abs(pos.y()))
            self._update_alloc_edge(axis, side, raw)
            return

        if self._is_drag_copy and (event.buttons() & Qt.LeftButton):
            yu  = self._py_to_unit_abs(pos.y())
            cur = self.mapper.unit_to_ms(yu)
            raw = cur - self._drag_start_abs_ms
            self._drag_cur_delta_ms = self._quantize(raw, self._drag_snap_ms)
            self.update()
            return

        # 小節線拖曳 - 更新顯示位置與暫時 BPM
        if getattr(self, '_barline_dragging', False) and (event.buttons() & Qt.LeftButton):
            self._barline_drag_py = pos.y()
            # 計算暫時 BPM 並更新狀態欄
            try:
                start_ms = int(self._barline_drag_start_ms or 0)
                cur_ms = int(round(self.mapper.unit_to_ms(self._py_to_unit_abs(pos.y()))))
                new_dur = max(1, cur_ms - start_ms)
                num = self.model.get_beats_per_bar_at_ms(start_ms)
                den = self.model.time_sig_denominator
                new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                self._drag_status = f'目標 BPM: {new_bpm:.2f}'
            except Exception:
                self._drag_status = ''
            self._emit_status()
            self.update()
            return

        if self._is_rubbing and (event.buttons() & Qt.LeftButton):
            self._rubber_end = pos
            try:
                self._rubber_end_u = self._py_to_unit_abs(pos.y())
            except Exception:
                pass
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if getattr(self, '_dyn_drag', None) is not None:
            self._dyn_drag = None
            self._dyn_last_mark = None
            self.note_edited.emit()
            self.update()
            return

        if self._hold_tail_note is not None:
            self._finish_hold_tail_drag()
            return

        if getattr(self, '_pedal_edge_drag', None) is not None:
            self._pedal_edge_drag = None
            # 拉完才正規化：拉的過程中合併區間會讓索引跑掉，手上那一段就飛了
            self.model.pedal_spans = self.model._normalise_pedal_spans(
                self.model.pedal_spans)
            if not self._pedal_edge_moved and self.model.undo_stack:
                self.model.undo_stack.pop()   # 只是點了一下邊界，沒拉動
            else:
                self.note_edited.emit()
            self._pedal_edge_moved = False
            self.update()
            return

        if self._pedal_drag is not None:
            start_ms, end_ms = self._pedal_drag
            self._pedal_drag = None
            # 先存檔再改：新增/刪除踏板以前完全沒有壓歷史，Ctrl+Z 救不回來。
            # 真的有動到才留下歷史紀錄，否則點空白處也會塞一筆進 undo 堆。
            was_dirty = self.model.dirty
            self.model.push_history()
            if abs(end_ms - start_ms) < 20.0:
                # 幾乎沒拖動 = 點一下：踩在既有區間上就刪掉它
                touched = self.model.pedal_remove_at(start_ms)
            else:
                touched = self.model.pedal_add_span(start_ms, end_ms)
            if not touched and self.model.undo_stack:
                self.model.undo_stack.pop()
                self.model.dirty = was_dirty
            else:
                self.note_edited.emit()
            self.update()
            return
        if self._pattern_drag is not None:
            if event.button() == Qt.LeftButton:
                self._finish_pattern_drag()
            return

        if event.button() == Qt.LeftButton and self._input_drag_note is not None:
            self._finish_input_drag()
            return
        if self.preview_mode:
            if event.button() == Qt.LeftButton and self._is_rubbing:
                self._is_rubbing = False
                start = self._rubber_start
                end = event.pos()
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                if start and abs(end.x() - start.x()) <= 3 and abs(end.y() - start.y()) <= 3:
                    self._single_click(end, ctrl)
                else:
                    self._rubber_select(QRect(start, end).normalized(), ctrl)
                self._rubber_start = None
                self._rubber_end = None
                self._rubber_start_u = None
                self._rubber_end_u = None
                self.update()
                self.selection_changed.emit(len(self.selected))
            return
        if event.button() == Qt.LeftButton and self.alloc_active:
            self._alloc_drag_edge = None
            return

        if event.button() == Qt.LeftButton and self._is_drag_copy:
            delta = int(round(self._drag_cur_delta_ms))
            self._is_drag_copy      = False
            self._drag_cur_delta_ms = 0.0
            self._drag_snap_ms      = 0.0
            self._drag_status       = ''
            if abs(delta) >= 1:
                self.duplicate_with_offset(delta)
            self.update()
            return

        # 小節線拖曳釋放：計算最終 BPM 並套用
        if event.button() == Qt.LeftButton and getattr(self, '_barline_dragging', False):
            try:
                self._barline_dragging = False
                target_idx = int(self._barline_drag_measure) if self._barline_drag_measure is not None else None
                if target_idx is None:
                    self._barline_drag_py = None
                    self._barline_drag_measure = None
                    self._drag_status = ''
                    self._emit_status()
                    return
                start_ms = int(self._barline_drag_start_ms or 0)
                cur_ms = int(round(self.mapper.unit_to_ms(self._py_to_unit_abs(event.pos().y()))))
                new_dur = max(1, cur_ms - start_ms)
                num = self.model.get_beats_per_bar_at_ms(start_ms)
                den = self.model.time_sig_denominator
                new_bpm = num * 4.0 * 60000.0 / (den * float(new_dur))
                # 推入歷史並套用
                self.model.push_history()
                self.model.set_measure_bpm(target_idx, float(new_bpm), uniform=True)
                self.rebuild_mapper()
                self._barline_drag_py = None
                self._barline_drag_measure = None
                self._barline_drag_start_ms = None
                self._barline_drag_orig_end_ms = None
                self._drag_status = ''
                self._emit_status()
                self.note_edited.emit()
                self.update()
            except Exception:
                # 清理狀態
                self._barline_dragging = False
                self._barline_drag_py = None
                self._barline_drag_measure = None
                self._drag_status = ''
                self._emit_status()
            return
            return

        if event.button() == Qt.LeftButton and self._is_rubbing:
            self._is_rubbing = False
            start = self._rubber_start
            end   = event.pos()
            ctrl  = bool(event.modifiers() & Qt.ControlModifier)
            if start and abs(end.x()-start.x()) <= 3 and abs(end.y()-start.y()) <= 3:
                self._single_click(end, ctrl)
            else:
                self._rubber_select(QRect(start, end).normalized(), ctrl)
            self._rubber_start = None
            self._rubber_end   = None
            self._rubber_start_u = None
            self._rubber_end_u = None
            self.update()
            self.selection_changed.emit(len(self.selected))

    def _hit_test(self, pos: QPoint) -> Optional[GNote]:
        pt = QPointF(pos)
        hit, min_area = None, float('inf')
        for rect, n in self._visible:
            if rect.contains(pt):
                area = rect.width() * rect.height()
                if area < min_area:
                    min_area, hit = area, n
        return hit

    def _single_click(self, pos: QPoint, ctrl: bool) -> None:
        # 先判斷是否點到 trill mesh 格 → 選取單一 cell
        cell = self._hit_trill_cell(pos)
        if cell is not None:
            if ctrl:
                if cell in self._sel_cells:
                    self._sel_cells.discard(cell)
                else:
                    self._sel_cells.add(cell)
            else:
                self._sel_cells = {cell}
                self.selected.clear()
            # 同時選取所屬 trill（音符），供右鍵操作
            self.selected = {t.idx for t, _i in self._sel_cells}
            return
        if not ctrl:
            self._sel_cells.clear()

        hit = self._hit_test(pos)
        if hit is None:
            if not ctrl:
                self.selected.clear()
                self._last_select_anchor_ms = None
        elif ctrl:
            if hit.idx in self.selected:
                self.selected.discard(hit.idx)
            else:
                self.selected.add(hit.idx)
            # update anchor to earliest selected
            picks = [n for n in self.model.notes if n.idx in self.selected]
            if picks:
                self._last_select_anchor_ms = min(float(n.start) for n in picks)
        else:
            self.selected = {hit.idx} if hit.idx not in self.selected else set()
            if hit is not None:
                self._last_select_anchor_ms = float(hit.start)

    def _rubber_select(self, rect: QRect, ctrl: bool) -> None:
        r = QRectF(rect)
        # 若框到 trill mesh 格 → 範圍選取 cell（不選音符），之後用左右鍵移動
        hit_cells = [(n, sidx) for cell_rect, n, sidx in self._trill_cell_hits
                     if r.intersects(cell_rect)]
        if hit_cells:
            if not ctrl:
                self._sel_cells.clear()
                self.selected.clear()
            for key in hit_cells:
                self._sel_cells.add(key)
            # 同時把所屬 trill 選為音符，讓右鍵「解開」、刪除等操作可用
            for t, _i in self._sel_cells:
                self.selected.add(t.idx)
            return
        if not ctrl:
            self._sel_cells.clear()
            self.selected.clear()
        for note_rect, n in self._visible:
            if r.intersects(note_rect):
                if ctrl and n.idx in self.selected:
                    self.selected.discard(n.idx)   # Ctrl + 已選取 → 取消
                else:
                    self.selected.add(n.idx)       # 否則追加
        # update anchor to earliest selected start
        picks = [nn for nn in self.model.notes if nn.idx in self.selected]
        if picks:
            self._last_select_anchor_ms = min(float(nn.start) for nn in picks)

    # 滾輪調力度：一格轉多少
    _VEL_WHEEL_UNDO_GAP_MS   = 700   # 間隔超過這麼久才算新的一筆 undo
    VELOCITY_WHEEL_STEP      = 4
    VELOCITY_WHEEL_FINE_STEP = 1

    def wheelEvent(self, event: QWheelEvent) -> None:
        import time as _t
        degrees = event.angleDelta().y() / 8.0
        steps   = degrees / 15.0

        # ── 游標停在「已選取的音符」上 → 滾輪改力度 ──────────────
        # 條件掛在「游標壓著選取的音符」而不是「有東西被選取」：後者會讓
        # 選了音符之後整個畫面都捲不動，而選起來再捲動去別處看是很常見的
        # 操作。壓在音符上時本來也沒有要捲動的意思。
        if steps and not (event.modifiers() & Qt.ControlModifier):
            hit = self._hit_test(event.pos())
            if hit is not None and hit.idx in self.selected:
                fine = bool(event.modifiers() & Qt.ShiftModifier)
                step = self.VELOCITY_WHEEL_FINE_STEP if fine else self.VELOCITY_WHEEL_STEP
                self._wheel_velocity(int(round(steps)) * step)
                event.accept()
                return
        # If Ctrl is held, use zoom instead of scroll
        if bool(event.modifiers() & Qt.ControlModifier):
            # map steps to a multiplicative zoom factor (steps>0 -> zoom in)
            try:
                factor = float(pow(0.9, steps)) if steps else 1.0
            except Exception:
                factor = 1.0
            if factor == 1.0:
                return

            # Preserve the unit (time) under the mouse position when zooming
            pos = event.pos()
            try:
                unit_under_mouse = float(self._py_to_unit_abs(pos.y()))
            except Exception:
                unit_under_mouse = self.window_start_unit + self.window_size_unit * 0.5

            if self.time_uniform:
                # time_uniform: operate on ms span (_time_uniform_span_ms)
                old_span_ms = max(1.0, float(self._time_uniform_span_ms or 1.0))
                new_span_ms = max(50.0, min(600000.0, old_span_ms * factor))

                # relative fraction of unit within old window
                old_size = float(self.window_size_unit)
                if old_size <= 0:
                    rel_frac = 0.5
                else:
                    rel_frac = (unit_under_mouse - self.window_start_unit) / old_size

                # compute new window_start in ms such that unit_under_mouse stays at same pixel
                unit_ms = float(self.mapper.unit_to_ms(unit_under_mouse))
                desired_ws_ms = unit_ms - rel_frac * new_span_ms
                # apply
                self._time_uniform_span_ms = new_span_ms
                self.window_start_unit = self.mapper.ms_to_unit(desired_ws_ms)
                self._sync_time_uniform_window_units()
                self._clamp_window_start()
                self.update()
                self._emit_status()
                return

            # non-time_uniform: adjust window_size_unit and window_start_unit to preserve unit position
            old_size = float(self.window_size_unit)
            new_size = max(MIN_WINDOW_UNITS, min(MAX_WINDOW_UNITS, old_size * factor))
            if old_size <= 0:
                rel_frac = 0.5
            else:
                rel_frac = (unit_under_mouse - self.window_start_unit) / old_size

            new_ws = unit_under_mouse - rel_frac * new_size
            self.window_size_unit = new_size
            self.window_start_unit = new_ws
            self._clamp_window_start()
            self.update()
            self._emit_status()
            return

        now = _t.time()
        self._wheel_events.append((now, abs(steps) if steps else 1.0))
        mult  = self._wheel_multiplier()
        delta = -steps * self._scroll_step_units() * mult
        if self.scroll_invert:
            delta = -delta
        self.scroll_by(delta)

    # ==================================================================
    # 鍵盤
    # ==================================================================

    def _hit_trill_cell(self, pos: QPoint) -> Optional[Tuple[GNote, int]]:
        """點擊位置是否落在某個 trill mesh 格上，回傳 (trill_note, sub_index)。"""
        for rect, note, sidx in reversed(self._trill_cell_hits):
            if rect.contains(float(pos.x()), float(pos.y())):
                return (note, sidx)
        return None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key   = event.key()
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        # 疊層分割：單獨輕點 Shift = 對調哪一層透明。
        # 只有「按下 Shift 後沒按別的鍵就放開」才算，Shift+P / Shift+A
        # 之類的組合鍵不受影響（見 keyReleaseEvent）。
        if key == Qt.Key_Shift:
            if not event.isAutoRepeat():
                self._shift_tap = bool(self.overlay_role)
        else:
            self._shift_tap = False

        # ── trill mesh 逐格編輯：選中的 cell 用 左/右 移動（可多選；只支援左右）──
        if self._sel_cells and not ctrl and key in (Qt.Key_Left, Qt.Key_Right):
            delta = -1 if key == Qt.Key_Left else 1
            # 只留下仍存在且仍為 trill 的 cell
            valid = [(t, i) for (t, i) in self._sel_cells
                     if t in self.model.notes_tree and note_is_trill(t.note_type)]
            if valid:
                if not event.isAutoRepeat():
                    self.model.push_history()
                changed = False
                # 同一顆 trill 的多格一起移動時，依方向排序避免互相擠壓
                for t, i in sorted(valid, key=lambda ti: (id(ti[0]), ti[1])):
                    if move_trill_cell(t, i, delta):
                        changed = True
                if changed:
                    self.update()
                    self.note_edited.emit()
            else:
                self._sel_cells.clear()
            return

        # Toggle preview mode with Tab (always available)
        if key == Qt.Key_Tab:
            self.toggle_preview_mode(not self.preview_mode)
            return

        # ── Alloc 模式 ────────────────────────────────────────────────
        if self.alloc_active:
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.confirm_alloc_section()
            elif key == Qt.Key_Escape:
                self.cancel_alloc_section()
            elif key == Qt.Key_Left:
                self._update_alloc_edge('x', 'min', self.alloc_target_min - 1)
            elif key == Qt.Key_Right:
                self._update_alloc_edge('x', 'max', self.alloc_target_max + 1)
            return

        # ── 預覽模式：只允許選取 + 上下左右 + 播放停止 ──────────────────────
        if self.preview_mode:
            if key == Qt.Key_Up:
                if self.selected:
                    self.shift_selected_by_32nd(-1, push=not event.isAutoRepeat())
                else:
                    self.scroll_by(self._scroll_step_units() * (4 if shift else 1))
            elif key == Qt.Key_Down:
                if self.selected:
                    self.shift_selected_by_32nd(1, push=not event.isAutoRepeat())
                else:
                    self.scroll_by(-self._scroll_step_units() * (4 if shift else 1))
            elif key == Qt.Key_Left and not ctrl:
                if self.pitch_mode:
                    self.shift_selected_pitch(-(10 if shift else 1), push=not event.isAutoRepeat(), sync_keys=True)
                else:
                    self.shift_selected_keys(-(10 if shift else 1), push=not event.isAutoRepeat())
            elif key == Qt.Key_Right and not ctrl:
                if self.pitch_mode:
                    self.shift_selected_pitch(10 if shift else 1, push=not event.isAutoRepeat(), sync_keys=True)
                else:
                    self.shift_selected_keys(10 if shift else 1, push=not event.isAutoRepeat())
            elif ctrl and key == Qt.Key_A:
                self.select_all()
            elif key == Qt.Key_Escape:
                self.deselect_all()
            elif ctrl and key == Qt.Key_P:
                self.play_full_requested.emit()
            elif key == Qt.Key_P and not shift:
                ws_ms, we_ms = self._window_ms()
                self.play_requested.emit(ws_ms, we_ms)
            elif key == Qt.Key_S:
                self.stop_requested.emit()
            return

        # 若正在拖曳小節線，按 Esc 可取消拖曳（不套用變更）
        if key == Qt.Key_Escape and getattr(self, '_barline_dragging', False):
            self._barline_dragging = False
            self._barline_drag_measure = None
            self._barline_drag_start_ms = None
            self._barline_drag_orig_end_ms = None
            self._barline_drag_py = None
            self._drag_status = ''
            self._emit_status()
            self.update()
            return

        # ── Up/Down：有選取→32分音符時間移動，無選取→捲動 ───────────
        if key == Qt.Key_Up:
            if self.selected:
                self.shift_selected_by_32nd(-1, push=not event.isAutoRepeat())
            else:
                self.scroll_by(self._scroll_step_units() * (4 if shift else 1))
            return
        if key == Qt.Key_Down:
            if self.selected:
                self.shift_selected_by_32nd(1, push=not event.isAutoRepeat())
            else:
                self.scroll_by(-self._scroll_step_units() * (4 if shift else 1))
            return

        # ── 鍵位平移 ─────────────────────────────────────────────────
        if key == Qt.Key_Left and not ctrl:
            if self.pitch_mode:
                self.shift_selected_pitch(-(10 if shift else 1), push=not event.isAutoRepeat(), sync_keys=True)
            else:
                self.shift_selected_keys(-(10 if shift else 1), push=not event.isAutoRepeat())
            return
        if key == Qt.Key_Right and not ctrl:
            if self.pitch_mode:
                self.shift_selected_pitch(10 if shift else 1, push=not event.isAutoRepeat(), sync_keys=True)
            else:
                self.shift_selected_keys(10 if shift else 1, push=not event.isAutoRepeat())
            return

        # ── Undo ──────────────────────────────────────────────────────
        if ctrl and key == Qt.Key_Z:
            self.undo()
            return

        # ── Copy / Paste ──────────────────────────────────────────────
        if ctrl and key == Qt.Key_C:
            self.copy_to_clipboard()
            return
        if ctrl and key == Qt.Key_V:
            self.paste_from_clipboard()
            return

        # ── 全選 ──────────────────────────────────────────────────────
        if ctrl and key == Qt.Key_A:
            self.select_all()
            return
        if key == Qt.Key_Escape:
            self.deselect_all()
            return

        # ── 刪除 ──────────────────────────────────────────────────────
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            return

        # ── 縮放 ──────────────────────────────────────────────────────
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom(0.5)
            return
        if key == Qt.Key_Minus:
            self.zoom(2.0)
            return

        # ── 音符類型 ──────────────────────────────────────────────────
        if key == Qt.Key_H:
            self.set_type_selected(2)
            return
        if key == Qt.Key_T:
            self.set_type_selected(0)
            return
        if key == Qt.Key_K:
            self.set_type_selected(3)
            return

        # ── 左右手 ────────────────────────────────────────────────────
        if key == Qt.Key_L:
            self.set_hand_selected(1)   # 1 = 左
            return
        if key == Qt.Key_R:
            self.set_hand_selected(0)   # 0 = 右
            return

        # ── 就地複製（非 Ctrl+C）───────────────────────────────────
        if key == Qt.Key_C and not ctrl:
            self.duplicate_selected()
            return

        # ── 播放 ──────────────────────────────────────────────────────
        if key == Qt.Key_P and ctrl:
            self.play_full_requested.emit()
            return
        if key == Qt.Key_P and not shift and not ctrl:
            self.play_from_window_requested.emit()
            return
        if key == Qt.Key_P and shift:
            self._emit_play_selection()
            return
        if key == Qt.Key_S:
            self.stop_requested.emit()
            return

        # ── Alloc Section 啟動 ────────────────────────────────────────
        if key == Qt.Key_A and shift:
            self.start_alloc_section()
            return

        super().keyPressEvent(event)

    # ── 播放 MIDI ─────────────────────────────────────────────────────

    def _selected_notes(self) -> List[GNote]:
        return [n for n in self.model.notes if n.idx in self.selected]

    def selection_time_range(self) -> Optional[Tuple[float, float]]:
        """選取音符涵蓋的時間範圍；沒選取時回傳 None。"""
        sel = self._selected_notes()
        if not sel:
            return None
        return (float(min(n.start for n in sel)), float(max(n.end for n in sel)))

    def play_midi_selected_range(self) -> None:
        """播放「選取範圍」內的所有音符（含沒被選到、但落在範圍內的）。"""
        rng = self.selection_time_range()
        if rng is None:
            self.play_midi_window()
            return
        # 範圍以起點為準過濾，尾端加一點寬容避免最後一顆被排除
        self.play_midi_requested.emit(rng[0], rng[1] + 1.0, None)

    def play_midi_selected_notes(self) -> None:
        """只播放被選取的那些音符。"""
        sel = self._selected_notes()
        if not sel:
            self.play_midi_window()
            return
        self.play_midi_requested.emit(
            float(min(n.start for n in sel)),
            float(max(n.end for n in sel)),
            [int(n.idx) for n in sel],
        )

    def play_midi_window(self) -> None:
        """播放目前可視時間範圍內的音符。"""
        ws_ms, we_ms = self.visible_ms_range()
        self.play_midi_requested.emit(float(ws_ms), float(we_ms), None)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if (event.key() == Qt.Key_Shift
                and not event.isAutoRepeat()
                and self._shift_tap
                and self.overlay_role):
            self._shift_tap = False
            self.overlay_swap_requested.emit()
            event.accept()
            return
        self._shift_tap = False
        super().keyReleaseEvent(event)

    def _emit_play_selection(self) -> None:
        if not self.selected:
            ws_ms, we_ms = self._window_ms()
            self.play_requested.emit(ws_ms, we_ms)
            return
        # map selected display idx -> actual note objects from display cache
        sel_notes = [n for n in self.model.notes if n.idx in self.selected]
        if not sel_notes:
            ws_ms, we_ms = self._window_ms()
            self.play_requested.emit(ws_ms, we_ms)
            return
        self.play_requested.emit(
            float(min(n.start for n in sel_notes)),
            float(max(n.end   for n in sel_notes)),
        )

    # ==================================================================
    # resize
    # ==================================================================

    def resizeEvent(self, _: QResizeEvent) -> None:
        # 判定線的位置是 `鍵盤高度 ÷ 畫布高度` 換算出來的，視窗一改大小分母就
        # 變了，但 window_start_unit 還是照舊高度算的，判定線就會和鍵盤上緣
        # 分家。播放中每幀都會重跑 follow_to_ms 所以看不出來，暫停時放大就歪掉。
        # 這裡重新對齊一次；尾端留白也依視窗高度算，一併更新。
        # 順序不能反：_update_unit_bounds 結尾會 _clamp_window_start()，
        # 先 follow 再更新邊界的話剛對齊好的位置又會被拉回去。
        self._update_unit_bounds()
        if self._judge_ms is not None:
            self.follow_to_ms(float(self._judge_ms))
        self.update()

    # ── 右鍵選單：各組子選單 ────────────────────────────────────────

    def _ctx_note_property_dialog(self, hit: GNote) -> None:
        """編輯單顆音符屬性。`hit` 來自 display cache，要換回 notes_tree 的物件。"""
        if hit in self.model.notes_tree:
            auth = hit
        else:
            auth = next((n for n in self.model.notes_tree if n.idx == hit.idx), hit)
        dlg = NotePropertyDialog(self, auth,
                                 beat_ms=60000.0 / max(1.0, self.model.bpm))
        if dlg.exec_() == QDialog.Accepted:
            self.model.push_history()
            dlg.apply_to(auth)
            auth.apply_back()
            self.model.rebuild_display_cache()
            self.update()
            self.note_edited.emit()

    def _ctx_note_bpm_dialog(self, hit: GNote) -> None:
        from PyQt5.QtWidgets import QMessageBox

        if hit in self.model.notes_tree:
            auth = hit
        else:
            auth = next((n for n in self.model.notes_tree if n.idx == hit.idx), hit)
        if not self.model.get_beat_entries():
            QMessageBox.warning(self, '缺少拍點資料',
                                '目前譜面沒有可編輯的 beat_data / beat_timings。')
            return
        current_bpm = self.model.get_measure_bpm(
            self.model.get_measure_at_ms(auth.start))
        bpm, ok = QInputDialog.getDouble(
            self, '從此音符開始變拍',
            f'從 {int(auth.start)} ms 開始的新 BPM：',
            float(current_bpm), 1.0, 9999.0, 2,
        )
        if not ok:
            return
        try:
            self.model.push_history()
            self.model.set_note_bpm(int(auth.start), float(bpm))
            self.rebuild_mapper()
            self._update_unit_bounds()
            self.update()
            self.note_edited.emit()
        except Exception as exc:
            QMessageBox.critical(self, '變拍失敗', str(exc))

    def _hidden_host(self, note: 'GNote'):
        """隱藏音符要掛在哪顆可見音符上（音高最近）。

        重繪每一幀都會用到，所以照音符集合的簽章快取；集合一變就重算。
        """
        sig = (len(self.model.notes_tree),
               sum(1 for n in self.model.notes_tree if getattr(n, 'hidden', False)))
        if getattr(self, '_host_sig', None) != sig:
            self._host_map = {id(h): host
                              for h, host in self.model.resolve_hidden_hosts()}
            self._host_sig = sig
        return self._host_map.get(id(note))

    def _ctx_toggle_hidden(self) -> None:
        """把選取的音符設成／取消「遊戲譜面隱藏」。

        隱藏的音符不佔一個按鍵，存檔時會併進音高最接近的可見音符的
        sub_note_data —— 也就是官方低難度把和絃塞進同一顆 note 的做法。
        """
        notes = [n for n in self.model.notes if n.idx in self.selected]
        if not notes:
            return
        self.model.push_history()
        target = not all(bool(getattr(n, 'hidden', False)) for n in notes)
        for note in notes:
            note.hidden = target
        self.update()
        self.note_edited.emit()
        self.status_changed.emit(
            ('已隱藏 %d 顆音符（存檔時併入最近的音符）' if target
             else '已取消隱藏 %d 顆音符') % len(notes))

    def _hidden_children(self, host: 'GNote') -> list:
        """掛在這顆音符底下的隱藏音符。"""
        return [h for h, hh in self.model.resolve_hidden_hosts()
                if hh is not None and id(hh) == id(host)]

    def _ctx_extract_hidden(self, host: 'GNote') -> None:
        """把掛在寄主底下的隱藏音符拆出來變成獨立音符。

        超過三顆時跳一個複選清單讓使用者挑——一次全拆通常不是想要的，
        官方低難度的寄主動輒掛五六個音。
        """
        children = self._hidden_children(host)
        if not children:
            return
        picked = children
        if len(children) > HIDDEN_EXTRACT_ASK_OVER:
            from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                         QListWidgetItem, QDialogButtonBox, QLabel)
            dlg = QDialog(self)
            dlg.setWindowTitle('拆出隱藏音符')
            box = QVBoxLayout(dlg)
            box.addWidget(QLabel('這顆音符底下有 %d 個隱藏音，選擇要拆出的：'
                                 % len(children)))
            lst = QListWidget(); lst.setSelectionMode(QListWidget.NoSelection)
            for h in sorted(children, key=lambda x: (x.pitch or 0)):
                it = QListWidgetItem('音高 %s' % self._pitch_label(h.pitch))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                it.setData(Qt.UserRole, h.idx)
                lst.addItem(it)
            box.addWidget(lst)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
            box.addWidget(bb)
            if dlg.exec_() != QDialog.Accepted:
                return
            keep = {lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                    if lst.item(i).checkState() == Qt.Checked}
            picked = [h for h in children if h.idx in keep]
        if not picked:
            return
        self.model.push_history()
        for h in picked:
            h.hidden = False
        self.model.rebuild_display_cache()
        self.update()
        self.note_edited.emit()
        self.status_changed.emit('已拆出 %d 顆隱藏音符' % len(picked))

    def _ctx_build_note_menu(self, menu, has_sel: bool) -> None:
        """音符屬性：類型 / 左右手 / 寬度 / 遊戲譜面隱藏。"""
        sel = [n for n in self.model.notes if n.idx in self.selected]
        all_hidden = bool(sel) and all(bool(getattr(n, 'hidden', False)) for n in sel)
        a = menu.addAction('取消遊戲譜面隱藏' if all_hidden else '遊戲譜面隱藏 NOTE')
        a.setEnabled(has_sel)
        a.triggered.connect(lambda checked=False: self._ctx_toggle_hidden())
        hosts = [n for n in sel if not getattr(n, 'hidden', False)
                 and self._hidden_children(n)]
        if hosts:
            total = sum(len(self._hidden_children(n)) for n in hosts)
            a = menu.addAction('拆出隱藏音符（%d 個）…' % total)
            a.triggered.connect(
                lambda checked=False, _hs=list(hosts):
                    [self._ctx_extract_hidden(h) for h in _hs])
        menu.addSeparator()

        type_m = menu.addMenu('類型')
        for label, ntype in [('Tap  (T)', 0), ('Soft', 1),
                             ('Long  (H)', 2), ('Staccato  (K)', 3),
                             ('Slide  (滑)', 4), ('Trill  (顫音)', 64)]:
            a = type_m.addAction(label)
            a.setEnabled(has_sel)
            a.triggered.connect(
                lambda checked=False, _t=ntype: self.set_type_selected(_t))

        hand_m = menu.addMenu('左右手')
        for label, hand in [('右手  (R)', 0), ('左手  (L)', 1)]:
            a = hand_m.addAction(label)
            a.setEnabled(has_sel)
            a.triggered.connect(
                lambda checked=False, _h=hand: self.set_hand_selected(_h))

        width_m = menu.addMenu('寬度')
        for w in range(1, 7):
            a = width_m.addAction(f'寬度 {w}')
            a.setEnabled(has_sel)
            a.triggered.connect(
                lambda checked=False, _w=w: self.set_width_selected(_w))

    # 平移用的音符值：(顯示名, 拍數)。以四分音符 = 1 拍計。
    _CTX_SHIFT_VALUES = [
        ('全音符  (4 拍)', 4.0),
        ('二分音符  (2 拍)', 2.0),
        ('四分音符  (1 拍)', 1.0),
        ('八分音符', 0.5),
        ('八分三連', 1.0 / 3.0),
        ('16 分音符', 0.25),
        ('16 分三連', 1.0 / 6.0),
        ('32 分音符', 0.125),
        ('64 分音符', 0.0625),
    ]
    # 整拍平移（拍數）
    _CTX_SHIFT_BEATS = [1, 2, 3, 4, 8, 16]
    # 固定時間平移（ms）——和拍子無關，用來修 MIDI 匯入的整體偏移
    _CTX_SHIFT_MS = [1, 5, 10, 25, 50, 100, 250, 500]

    def _ctx_shift_custom_dialog(self, sign: int) -> None:
        """自訂平移量：可輸入拍數（支援分數）。"""
        from PyQt5.QtWidgets import QMessageBox

        text, ok = QInputDialog.getText(
            self, '平移自訂拍數',
            '要平移幾拍？（可用分數，例如 3/4、1 1/2；四分音符 = 1 拍）',
            text='0.25',
        )
        if not ok:
            return
        try:
            beats = _parse_beats_text(text)
        except ValueError:
            QMessageBox.warning(self, '輸入錯誤',
                                '無法解析拍數，請輸入數字或分數，例如 3/4 或 1 1/2。')
            return
        if beats <= 0:
            QMessageBox.warning(self, '輸入錯誤', '拍數必須大於 0。')
            return
        self.shift_selected_by_beats(beats * sign)

    def _ctx_shift_custom_ms_dialog(self, sign: int) -> None:
        """自訂固定時間平移（ms），不吃 BPM。"""
        ms, ok = QInputDialog.getInt(
            self, '平移固定時間',
            '要平移幾毫秒？（不吃 BPM，整組固定位移）',
            50, 1, 9_999_999, 1)
        if ok:
            self.shift_selected_time(int(ms) * sign)

    def _ctx_build_shift_menu(self, menu, has_sel: bool) -> None:
        """整組平移：往後（延後）/ 往前（提前），各有音符值 / 整拍 / 固定時間三種。

        音符值與整拍換算用的是每顆音符**自己所在位置的 BPM**，所以變速譜上也是
        精準的音符值，不是整首一個平均拍長；固定時間那組則完全不看 BPM。
        """
        menu.setEnabled(has_sel)
        for title, sign in (('往後（延後）', +1), ('往前（提前）', -1)):
            sub = menu.addMenu(title)
            prefix = '+' if sign > 0 else '−'

            for label, beats in self._CTX_SHIFT_VALUES:
                a = sub.addAction(f'{prefix} {label}')
                a.triggered.connect(
                    lambda checked=False, _b=beats * sign:
                    self.shift_selected_by_beats(_b))

            beat_m = sub.addMenu('整拍')
            for beats in self._CTX_SHIFT_BEATS:
                a = beat_m.addAction(f'{prefix} {beats} 拍')
                a.triggered.connect(
                    lambda checked=False, _b=float(beats) * sign:
                    self.shift_selected_by_beats(_b))
            beat_m.addSeparator()
            a = beat_m.addAction('自訂拍數…')
            a.triggered.connect(
                lambda checked=False, _s=sign: self._ctx_shift_custom_dialog(_s))

            ms_m = sub.addMenu('固定時間')
            for ms in self._CTX_SHIFT_MS:
                a = ms_m.addAction(f'{prefix} {ms} ms')
                a.triggered.connect(
                    lambda checked=False, _ms=ms * sign:
                    self.shift_selected_time(_ms))
            ms_m.addSeparator()
            a = ms_m.addAction('自訂毫秒…')
            a.triggered.connect(
                lambda checked=False, _s=sign: self._ctx_shift_custom_ms_dialog(_s))

            sub.addSeparator()
            a = sub.addAction('自訂拍數…')
            a.triggered.connect(
                lambda checked=False, _s=sign: self._ctx_shift_custom_dialog(_s))

    def _ctx_set_beats_dialog(self) -> None:
        """把選取音符的時長設成指定拍數（分數 / 小數雙向同步）。"""
        from PyQt5.QtWidgets import (
            QDialog as _QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
            QDialogButtonBox, QMessageBox,
        )

        beat_ms = 60000.0 / max(1.0, self.model.bpm)
        first_n = next((n for n in self.model.notes if n.idx in self.selected), None)
        default_beats = (round((first_n.end - first_n.start) / beat_ms, 3)
                         if first_n else 1.0)

        dlg = _QDialog(self)
        dlg.setWindowTitle('設定時長（拍）')
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            f'將 {len(self.selected)} 個音符設為指定拍數'
            f'（上方輸入分數，下方小數；1拍 = {beat_ms:.1f} ms）：'))

        frac_box = QHBoxLayout()
        frac_edit = QLineEdit()
        frac_edit.setPlaceholderText('例如 3/4 或 1 3/4')
        frac_box.addWidget(QLabel('分數：'))
        frac_box.addWidget(frac_edit)
        layout.addLayout(frac_box)

        dec_box = QHBoxLayout()
        dec_edit = QLineEdit()
        dec_edit.setPlaceholderText('例如 0.75')
        dec_box.addWidget(QLabel('小數：'))
        dec_box.addWidget(dec_edit)
        layout.addLayout(dec_box)

        ms_label = QLabel('')
        layout.addWidget(ms_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        frac_edit.setText(_beats_to_mixed_fraction(default_beats))
        dec_edit.setText(str(round(default_beats, 6)))
        ms_label.setText(f'{round(default_beats * beat_ms, 1)} ms')

        updating = {'flag': False}

        def on_frac_changed(text: str) -> None:
            if updating['flag']:
                return
            updating['flag'] = True
            try:
                val = _parse_beats_text(text)
                dec_edit.setText(str(round(val, 6)))
                ms_label.setText(f'{round(val * beat_ms, 1)} ms')
            except Exception:
                dec_edit.setText('')
                ms_label.setText('')
            finally:
                updating['flag'] = False

        def on_dec_changed(text: str) -> None:
            if updating['flag']:
                return
            updating['flag'] = True
            try:
                v = float(text)
                frac_edit.setText(_beats_to_mixed_fraction(v))
                ms_label.setText(f'{round(v * beat_ms, 1)} ms')
            except Exception:
                frac_edit.setText('')
                ms_label.setText('')
            finally:
                updating['flag'] = False

        frac_edit.textChanged.connect(on_frac_changed)
        dec_edit.textChanged.connect(on_dec_changed)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec_() != _QDialog.Accepted:
            return

        final_text = dec_edit.text().strip()
        try:
            beats_val = (float(final_text) if final_text
                         else _parse_beats_text(frac_edit.text()))
        except Exception:
            QMessageBox.warning(self, '輸入錯誤',
                                '無法解析拍數輸入，請輸入數字或分數，例如 3/4 或 1 3/4。')
            return
        if beats_val <= 0:
            QMessageBox.warning(self, '輸入錯誤', '拍數必須大於 0。')
            return
        self.set_length_beats_selected(beats_val)

    def _ctx_set_edge_custom(self, target: str) -> None:
        fn = next((n for n in self.model.notes if n.idx in self.selected), None)
        if target == 'start':
            default = int(fn.start) if fn else 0
            title, prompt = '設定起始時間', '起始對齊到 (ms)（終點不動）：'
        else:
            default = int(fn.end) if fn else 0
            title, prompt = '設定終止時間', '終止對齊到 (ms)（起點不動）：'
        val, ok = QInputDialog.getInt(
            self, title, f'將 {len(self.selected)} 個音符的{prompt}',
            default, 0, 9_999_999)
        if ok:
            self.align_selected_edge(target, val)

    def _ctx_delete_section(self) -> None:
        """刪除段落：拿掉選取的音符，後面的音符整段往前遞補這段空白。

        時間範圍取「選取音符的最早起點 ~ 最晚終點」。範圍**之後**的音符整批
        往前移這段長度；落在範圍內但沒被選到的音符會留在原地（不動它們，
        免得刪掉使用者沒打算刪的東西），但會在狀態列提醒有幾顆。
        """
        sel = [n for n in self.model.notes_tree if n.idx in self.selected]
        if not sel:
            return
        sel_ids_pre = {id(n) for n in sel}
        span_start = min(int(n.start) for n in sel)
        last_sel_start = max(int(n.start) for n in sel)
        # 終點取「選取之後第一顆音符的起點」——這樣後面那顆會剛好遞補到被刪掉
        # 的位置，間隔完全保留。取「最後一顆選取音符的結束」的話會少掉音符
        # 尾巴到下一顆之間那段空白，後面整批就對不回原本的節奏格。
        later = [int(n.start) for n in self.model.notes_tree
                 if id(n) not in sel_ids_pre and int(n.start) > last_sel_start]
        span_end = min(later) if later else max(int(n.end) for n in sel)
        # 再對齊到整數拍當保險：位移量不是拍的整數倍的話，後面每一顆都會
        # 偏離拍點，對節奏譜面等於整段毀掉。
        u0 = self.mapper.ms_to_unit(float(span_start))
        u1 = self.mapper.ms_to_unit(float(span_end))
        beats = max(1, int(round(u1 - u0)))
        span_end = int(round(self.mapper.unit_to_ms(u0 + beats)))
        delta = max(0, span_end - span_start)
        sel_ids = {id(n) for n in sel}
        inside = [n for n in self.model.notes_tree
                  if id(n) not in sel_ids and span_start <= int(n.start) < span_end]
        self.model.push_history()
        self.model.notes_tree = [n for n in self.model.notes_tree
                                 if id(n) not in sel_ids]
        moved = 0
        for note in self.model.notes_tree:
            if int(note.start) >= span_end:
                note.start = int(note.start) - delta
                note.end = int(note.end) - delta
                moved += 1
        end_ms = float(getattr(self.model, 'music_end_ms', 0.0) or 0.0)
        if end_ms > span_end:
            self.model.music_end_ms = max(0.0, end_ms - delta)
        self.model.rebuild_display_cache()
        self.selected.clear()
        self._update_unit_bounds()
        self.update()
        self.note_edited.emit()
        self.selection_changed.emit(0)
        msg = '已刪除段落 %d~%dms（%d 顆），後方 %d 顆往前移 %dms' % (
            span_start, span_end, len(sel), moved, delta)
        if inside:
            msg += '；範圍內另有 %d 顆未選取，維持原位' % len(inside)
        self.status_changed.emit(msg)

    def _ctx_build_time_menu(self, menu, has_sel: bool) -> None:
        """時間與時值：平移 / 設定時長 / 對齊頭尾 / 刪除段落。"""
        menu.setEnabled(has_sel)
        a = menu.addAction('刪除段落（後方往前遞補）')
        a.setEnabled(has_sel)
        a.triggered.connect(lambda checked=False: self._ctx_delete_section())
        menu.addSeparator()
        self._ctx_build_shift_menu(menu.addMenu('整組平移'), has_sel)
        menu.addSeparator()

        a = menu.addAction('設定時長（拍）…')
        a.setEnabled(has_sel)
        a.triggered.connect(self._ctx_set_beats_dialog)

        bounds = self.selection_time_bounds() if has_sel else None
        for target, title, key_min, key_max in (
            ('start', '設定起始時間', 'min_start', 'max_start'),
            ('end',   '設定終止時間', 'min_end',   'max_end'),
        ):
            sub = menu.addMenu(title)
            sub.setEnabled(has_sel)
            a = sub.addAction('自訂…')
            a.triggered.connect(
                lambda checked=False, _t=target: self._ctx_set_edge_custom(_t))
            if bounds:
                sub.addSeparator()
                a = sub.addAction(f'一律對齊到最早  ({bounds[key_min]} ms)')
                a.triggered.connect(
                    lambda checked=False, _t=target, _v=bounds[key_min]:
                    self.align_selected_edge(_t, _v))
                a = sub.addAction(f'一律對齊到最晚  ({bounds[key_max]} ms)')
                a.triggered.connect(
                    lambda checked=False, _t=target, _v=bounds[key_max]:
                    self.align_selected_edge(_t, _v))

    def _ctx_build_structure_menu(self, menu, has_sel: bool, multi_sel: bool) -> None:
        """結構：滑鍵 / 顫音 / Alloc Section。"""
        has_slide_sel = any(
            n.idx in self.selected and note_is_slide(n.note_type)
            for n in self.model.notes_tree
        )
        has_trill_sel = any(
            n.idx in self.selected and note_is_trill(n.note_type)
            for n in self.model.notes_tree
        )
        for label, enabled, slot in (
            ('串聯為滑鍵 (Slide)',  multi_sel,      self.chain_slides_selected),
            ('解開滑鍵（還原成 Tap）', has_slide_sel,  self.unchain_slides_selected),
            ('打包成 Trill (顫音)',  multi_sel,      self.pack_trill_selected),
            ('解開 Trill（還原）',   has_trill_sel,  self.unpack_trill_selected),
        ):
            a = menu.addAction(label)
            a.setEnabled(enabled)
            a.triggered.connect(lambda checked=False, _s=slot: _s())
        menu.addSeparator()

        # 排序：預設走簡化版智慧路徑（直接排好，不用拖）。要自己框範圍再用基本排序。
        multi_pitched = sum(
            1 for n in self.model.notes_tree
            if n.idx in self.selected and n.pitch is not None
        ) >= 2
        a = menu.addAction('智慧排序（依音程排好）')
        a.setEnabled(multi_pitched and not self.alloc_active and not self.pitch_mode)
        a.setToolTip('依音程距離直接排好鍵道位置，不需要拖曳。')
        a.triggered.connect(self.smart_sort_selected)

        a = menu.addAction('基本排序（拖曳範圍）  (Shift+A)')
        a.setEnabled(has_sel and not self.alloc_active)
        a.setToolTip('原本的 Alloc Section：拉紅框決定範圍，音符依音高排名等距分配。')
        a.triggered.connect(self.start_alloc_section)

    def _ctx_build_midi_menu(self, menu, has_sel: bool) -> None:
        """MIDI：channel / 依顏色刪除 / 刪整軌。"""
        ch_m = menu.addMenu('Set Channel')
        for ch in range(16):
            a = ch_m.addAction(f'Channel {ch}')
            a.setEnabled(has_sel)
            a.triggered.connect(
                lambda checked=False, _ch=ch: self.set_channel_selected(_ch))
        ch_m.addSeparator()
        a = ch_m.addAction('自訂 Channel…')
        a.setEnabled(has_sel)
        a.triggered.connect(self._ctx_set_channel_dialog)

        selected_channels = sorted({
            int(n.channel) for n in self.model.notes_tree
            if n.idx in self.selected and n.channel is not None
        })
        if selected_channels:
            del_color_m = menu.addMenu('Delete By Color')
            for ch in selected_channels:
                a = del_color_m.addAction(self._channel_icon(ch), f'Channel {ch}')
                a.triggered.connect(
                    lambda checked=False, _ch=ch: self.delete_selected_channel(_ch))

        # ── 延音踏板 ─────────────────────────────────────────
        ped_m = menu.addMenu('延音踏板')
        span_count = len(getattr(self.model, 'pedal_spans', None) or [])
        act = ped_m.addAction('由選取音符建立踏板' if has_sel else '由選取音符建立踏板')
        act.setEnabled(has_sel)
        act.triggered.connect(self._ctx_pedal_from_selection)
        act = ped_m.addAction('由整首音符長度推算踏板…')
        act.triggered.connect(self._ctx_pedal_from_note_lengths)
        ped_m.addSeparator()
        act = ped_m.addAction(f'清除全部踏板（{span_count} 段）')
        act.setEnabled(span_count > 0)
        act.triggered.connect(self._ctx_pedal_clear)

        a = menu.addAction('Delete Selected Track(s)')
        a.setEnabled(has_sel)
        a.triggered.connect(self.delete_selected_tracks)

    # ------------------------------------------------------------------
    # 力度
    # ------------------------------------------------------------------
    def _wheel_velocity(self, delta: int) -> None:
        """滾輪調整力度。整組選取一起動，維持彼此的強弱關係。

        連續滾動只推一次 undo——每一格都存一筆的話，要退回原本的力度得按
        幾十次 Ctrl+Z。距上次滾動超過 `_VEL_WHEEL_UNDO_GAP_MS` 才算新的一筆。
        """
        if not delta:
            return
        notes = self._selected_notes()
        if not notes:
            return
        import time as _t
        now = _t.time() * 1000.0
        if now - getattr(self, '_vel_wheel_last_ms', 0.0) > self._VEL_WHEEL_UNDO_GAP_MS:
            self.model.push_history()
        self._vel_wheel_last_ms = now

        for n in notes:
            base = 100 if n.velocity is None else int(n.velocity)
            n.velocity = max(1, min(127, base + int(delta)))
        self.model.dirty = True
        self.note_edited.emit()
        if len(notes) == 1:
            self.status_changed.emit(f'力度 {notes[0].velocity}')
        else:
            lo = min(int(n.velocity) for n in notes)
            hi = max(int(n.velocity) for n in notes)
            self.status_changed.emit(f'{len(notes)} 顆音符力度 {lo}~{hi}')
        self.update()

    def set_velocity_selected(self, value: int) -> None:
        notes = self._selected_notes()
        if not notes:
            return
        self.model.push_history()
        value = max(1, min(127, int(value)))
        for n in notes:
            n.velocity = value
        self.model.dirty = True
        self.note_edited.emit()
        self.status_changed.emit(f'{len(notes)} 顆音符力度設為 {value}')
        self.update()

    def nudge_velocity_selected(self, delta: int) -> None:
        notes = self._selected_notes()
        if not notes:
            return
        self.model.push_history()
        for n in notes:
            base = 100 if n.velocity is None else int(n.velocity)
            n.velocity = max(1, min(127, base + int(delta)))
        self.model.dirty = True
        self.note_edited.emit()
        sign = '+' if delta > 0 else ''
        self.status_changed.emit(f'{len(notes)} 顆音符力度 {sign}{int(delta)}')
        self.update()

    def _ctx_set_velocity_dialog(self) -> None:
        notes = self._selected_notes()
        if not notes:
            return
        current = next((int(n.velocity) for n in notes if n.velocity is not None), 100)
        value, ok = QInputDialog.getInt(
            self, '設定力度', '力度 (1-127)', current, 1, 127, 1)
        if ok:
            self.set_velocity_selected(value)

    def _ctx_scale_velocity_dialog(self) -> None:
        """按比例縮放，保留原本的強弱起伏——整段調亮／調暗時用這個，
        設固定值會把演奏的表情整平掉。"""
        notes = self._selected_notes()
        if not notes:
            return
        percent, ok = QInputDialog.getInt(
            self, '縮放力度', '百分比 (%)', 100, 10, 300, 5)
        if not ok:
            return
        self.model.push_history()
        factor = percent / 100.0
        for n in notes:
            base = 100 if n.velocity is None else int(n.velocity)
            n.velocity = max(1, min(127, int(round(base * factor))))
        self.model.dirty = True
        self.note_edited.emit()
        self.status_changed.emit(f'{len(notes)} 顆音符力度 ×{factor:.2f}')
        self.update()

    def _ctx_insert_pattern(self, pos) -> None:
        """右鍵處插入一組音階／琶音。起始時間與音高都取右鍵落點。"""
        from PyQt5.QtWidgets import QMessageBox

        from .pattern_dialog import PatternDialog

        if not self.model.has_chart():
            self.new_chart_requested.emit()
            return
        raw_unit = self._py_to_unit_abs(pos.y())
        snapped = self._snap_unit_to_duration(raw_unit, self._note_duration_beats)
        start_ms = max(0.0, self.mapper.unit_to_ms(snapped))

        display_key = self._px_to_display_key(pos.x())
        if self.pitch_mode:
            start_pitch = self._lock_pitch(self._slot_to_pitch(display_key))
        else:
            start_pitch = self._infer_pitch_from_key(display_key)

        dlg = PatternDialog(
            self, detected=self.detect_chart_key(),
            start_pitch=int(start_pitch), start_ms=int(round(start_ms)),
            hand=self._note_input_hand, show_midi_pitch=self.show_midi_pitch)
        if dlg.exec_() != QDialog.Accepted:
            return
        p = dlg.params()
        made = self.insert_pattern(
            p['kind'], p['key'], p['start_pitch'], p['count'], p['direction'],
            p['start_ms'], p['step_beats'], p['hand'],
            note_type=self._note_input_note_type)
        if made:
            self.status_changed.emit(
                '插入 %d 顆（%s）' % (made, p['key'].name()))
        else:
            QMessageBox.information(self, '插入音階 / 琶音', '沒有產生任何音符。')

    def _ctx_build_dynamics_menu(self, menu, click_ms: float) -> None:
        """強弱記號：像樂譜的 p / f / cresc.，左右手各一條曲線。"""
        dyn_m = menu.addMenu('強弱記號')
        dyn_m.setToolTipsVisible(True)
        for hand, name in ((0, '右手'), (1, '左手')):
            sub = dyn_m.addMenu('%s（此處 %d ms）' % (name, int(click_ms)))
            for label, level in self.model.DYNAMIC_MARKS:
                for ramp, suffix in ((False, ''), (True, ' ▸ 漸變到下一個')):
                    a = sub.addAction('%s  %d%s' % (label, level, suffix))
                    a.triggered.connect(
                        lambda checked=False, _h=hand, _ms=click_ms,
                               _lv=level, _r=ramp:
                        self._ctx_add_dynamic(_h, _ms, _lv, _r))
                if label in ('mp', 'fff'):
                    sub.addSeparator()
            sub.addSeparator()
            a = sub.addAction('刪除此處的記號')
            a.triggered.connect(
                lambda checked=False, _h=hand, _ms=click_ms:
                self._ctx_remove_dynamic(_h, _ms))
            a = sub.addAction('清除這一手全部記號')
            a.triggered.connect(
                lambda checked=False, _h=hand: self._ctx_clear_dynamics(_h))
        dyn_m.addSeparator()
        seed_m = dyn_m.addMenu('從音符力度產生曲線')
        seed_m.setToolTipsVisible(True)
        beat_ms = 60000.0 / max(1.0, float(self.model.bpm or 120.0))
        for label, res in (('每顆音符（最忠實）', 0.0),
                           ('每拍', beat_ms),
                           ('每小節', beat_ms * max(1, int(self.model.beats_per_bar)))):
            for hand, name in ((0, '右手'), (1, '左手'), (None, '兩手')):
                a = seed_m.addAction('%s ・ %s' % (label, name))
                a.setToolTip('把目前的音符力度描成可編輯的記號。')
                a.triggered.connect(
                    lambda checked=False, _h=hand, _r=res:
                    self._ctx_seed_dynamics(_h, _r))
            seed_m.addSeparator()

        a = dyn_m.addAction('套用強弱到音符力度')
        a.setToolTip('曲線當倍率乘進 velocity，保留每顆音符原本的相對起伏。')
        a.triggered.connect(self._ctx_apply_dynamics)
        a = dyn_m.addAction('清除全部強弱記號')
        a.triggered.connect(lambda: self._ctx_clear_dynamics(None))


    def _ctx_seed_dynamics(self, hand: Optional[int], resolution_ms: float) -> None:
        """用目前的音符力度產生可編輯的強弱記號。"""
        hands = (0, 1) if hand is None else (int(hand),)
        self.model.push_history()
        total = sum(self.model.dynamics_seed_from_notes(h, resolution_ms) for h in hands)
        if total:
            self.note_edited.emit()
            self.status_changed.emit('由音符力度產生了 %d 個強弱記號' % total)
            self.update()
        else:
            if self.model.undo_stack:
                self.model.undo_stack.pop()
            self.status_changed.emit('這一手沒有帶力度的音符')

    def _ctx_add_dynamic(self, hand: int, ms: float, level: int, ramp: bool) -> None:
        self.model.push_history()
        self.model.dynamics_add(hand, ms, level, ramp=ramp)
        self.note_edited.emit()
        self.status_changed.emit(
            '%s 加上 %s%s @ %d ms'
            % ('左手' if int(hand) == 1 else '右手',
               self.model.dynamic_mark_name(level),
               '（漸變）' if ramp else '', int(ms)))
        self.update()

    def _ctx_remove_dynamic(self, hand: int, ms: float) -> None:
        self.model.push_history()
        if self.model.dynamics_remove_near(hand, ms):
            self.note_edited.emit()
            self.update()
        elif self.model.undo_stack:
            self.model.undo_stack.pop()
            self.status_changed.emit('這附近沒有強弱記號')

    def _ctx_clear_dynamics(self, hand: Optional[int]) -> None:
        self.model.push_history()
        removed = self.model.dynamics_clear(hand)
        if removed:
            self.note_edited.emit()
            self.status_changed.emit('清除了 %d 個強弱記號' % removed)
            self.update()
        elif self.model.undo_stack:
            self.model.undo_stack.pop()

    def _ctx_apply_dynamics(self) -> None:
        """把強弱曲線當倍率乘進音符 velocity。"""
        from PyQt5.QtWidgets import QMessageBox

        if not any(self.model.dynamics_marks(h) for h in (0, 1)):
            QMessageBox.information(self, '套用強弱', '還沒有任何強弱記號。')
            return
        self.model.push_history()
        changed = self.model.apply_dynamics()
        if changed:
            self.model.rebuild_display_cache()
            self.note_edited.emit()
            self.status_changed.emit('強弱已套用到 %d 顆音符' % changed)
            self.update()
        else:
            if self.model.undo_stack:
                self.model.undo_stack.pop()
            QMessageBox.information(self, '套用強弱', '音符力度已經符合曲線，沒有需要改的。')

    def _ctx_build_velocity_menu(self, menu, has_sel: bool) -> None:
        """設定選取音符的力度。

        以前掛在 MIDI 子選單底下，只有 MIDI 模式看得到；力度是音樂資訊、
        不是 MIDI 專屬的欄位，所以拉到頂層，有選取就能用。
        """
        vel_m = menu
        vel_m.setEnabled(has_sel)
        vel_m.setToolTipsVisible(True)
        for label, value in (('ppp  16', 16), ('pp  32', 32), ('p  48', 48),
                             ('mp  64', 64), ('mf  80', 80), ('f  96', 96),
                             ('ff  112', 112), ('fff  127', 127)):
            act = vel_m.addAction(label)
            act.triggered.connect(
                lambda checked=False, _v=value: self.set_velocity_selected(_v))
        vel_m.addSeparator()
        for label, delta in (('加重 +10', 10), ('減輕 −10', -10)):
            act = vel_m.addAction(label)
            act.triggered.connect(
                lambda checked=False, _d=delta: self.nudge_velocity_selected(_d))
        vel_m.addSeparator()
        act = vel_m.addAction('自訂力度…')
        act.triggered.connect(self._ctx_set_velocity_dialog)
        act = vel_m.addAction('依比例縮放…')
        act.triggered.connect(self._ctx_scale_velocity_dialog)
        vel_m.addSeparator()
        act = vel_m.addAction('力度分布…')
        act.setEnabled(True)      # 沒選取就看整份譜面
        act.setToolTip('列出各力度值各有幾顆音符，確認資料本身是不是就只有一兩種力度。')
        act.triggered.connect(self._ctx_velocity_report_dialog)


    def _ctx_velocity_report_dialog(self) -> None:
        """列出力度分布，用來確認「畫面上到處都是同一個數字」是資料本來就這樣，
        還是哪裡把力度壓平了。

        很多來源 MIDI 其實只有兩三種力度（打譜軟體輸出、量化過的檔案常見），
        看起來就會像整首都同一個值。
        """
        from collections import Counter
        from PyQt5.QtWidgets import QMessageBox

        sel = self._selected_notes()
        scope = sel if sel else list(self.model.notes_tree)
        title = '選取的 %d 顆音符' % len(sel) if sel else '整份譜面 %d 顆音符' % len(scope)
        if not scope:
            QMessageBox.information(self, '力度分布', '沒有音符。')
            return

        counts = Counter(int(n.velocity) for n in scope if n.velocity is not None)
        missing = sum(1 for n in scope if n.velocity is None)
        lines = [title, '']
        if counts:
            lines.append('相異力度值：%d 種（%d ~ %d）'
                         % (len(counts), min(counts), max(counts)))
            lines.append('')
            for vel, cnt in counts.most_common(12):
                bar = '█' * max(1, round(cnt / max(counts.values()) * 24))
                lines.append('%3d  %-24s %d 顆 (%.0f%%)'
                             % (vel, bar, cnt, cnt / len(scope) * 100))
            if len(counts) > 12:
                lines.append('…還有 %d 種' % (len(counts) - 12))
        if missing:
            lines.append('')
            lines.append('沒有力度資料：%d 顆（遊戲原生譜面通常沒有，'
                         '力度是 MIDI 匯入才有的）' % missing)
        QMessageBox.information(self, '力度分布', '\n'.join(lines))

    # ------------------------------------------------------------------
    # 踏板
    # ------------------------------------------------------------------
    def _ctx_pedal_from_selection(self) -> None:
        notes = self._selected_notes()
        if not notes:
            return
        start = min(float(n.start) for n in notes)
        end = max(float(n.end) for n in notes)
        if self.model.pedal_add_span(start, end):
            self.note_edited.emit()
            self.status_changed.emit(f'加入踏板 {int(start)}~{int(end)} ms')
            self.update()

    def _ctx_pedal_from_note_lengths(self) -> None:
        gap, ok = QInputDialog.getInt(
            self, '由音符長度推算踏板',
            '相隔多少 ms 以內視為同一段踏板', 60, 0, 2000, 10)
        if not ok:
            return
        self.model.push_history()
        count = self.model.pedal_from_note_lengths(float(gap))
        self.note_edited.emit()
        self.status_changed.emit(f'推算出 {count} 段踏板')
        self.update()

    def _ctx_pedal_clear(self) -> None:
        count = self.model.pedal_clear()
        if count:
            self.note_edited.emit()
            self.status_changed.emit(f'清除 {count} 段踏板')
            self.update()

    def _ctx_set_channel_dialog(self) -> None:
        if not self.selected:
            return
        existing = next(
            (int(n.channel) for n in self.model.notes_tree
             if n.idx in self.selected and n.channel is not None),
            0,
        )
        value, ok = QInputDialog.getInt(
            self, 'Set MIDI Channel', 'Channel (0-15)', existing, 0, 15, 1)
        if ok:
            self.set_channel_selected(value)

    def _ctx_hit_factor_dialog(self) -> None:
        from PyQt5.QtWidgets import (
            QDialog as _QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox,
            QDialogButtonBox, QMessageBox, QLabel,
        )

        dlg = _QDialog(self)
        dlg.setWindowTitle('設定打擊因')
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()

        jm = getattr(self.model, 'json_meta', {}) or {}
        spins = {}
        for key, label, default in (
            ('hit_factor_right', '右手打擊因：', 1.0),
            ('hit_factor_left',  '左手打擊因：', 1.0),
            ('hit_factor_beat',  '小節拍打擊因：', 1.0),
        ):
            sb = QDoubleSpinBox()
            sb.setRange(0.0, 10.0)
            sb.setDecimals(2)
            sb.setValue(float(jm.get(key, default)))
            form.addRow(label, sb)
            spins[key] = sb

        vbox.addLayout(form)
        hint = QLabel('說明：此設定只儲存在當前模型（save 時會寫入 JSON meta）。')
        hint.setStyleSheet('color:#666; font-size:11px')
        vbox.addWidget(hint)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        vbox.addWidget(bbox)

        if dlg.exec_() != _QDialog.Accepted:
            return
        for key, sb in spins.items():
            jm[key] = float(sb.value())
        self.model.json_meta = jm
        self.model.dirty = True
        QMessageBox.information(self, '已儲存', '打擊因已更新並儲存在模型。')

    def _ctx_build_playback_menu(self, menu, has_sel: bool) -> None:
        """播放：視窗 / 選取 / MIDI 合成 / 停止 / 打擊因。"""
        a = menu.addAction('▶ 播放視窗  (P)')
        a.triggered.connect(self.play_from_window_requested.emit)
        a = menu.addAction('▶ 播放選取  (Shift+P)')
        a.triggered.connect(self._emit_play_selection)

        midi_play_m = menu.addMenu('🎹 播放 MIDI')
        sel_rng = self.selection_time_range()
        a = midi_play_m.addAction(
            '▶ 選取範圍內的音符'
            + (f'  ({int(sel_rng[0])}–{int(sel_rng[1])} ms)' if sel_rng else ''))
        a.setEnabled(sel_rng is not None)
        a.triggered.connect(self.play_midi_selected_range)
        a = midi_play_m.addAction(f'▶ 僅選取的 {len(self.selected)} 顆音符')
        a.setEnabled(has_sel)
        a.triggered.connect(self.play_midi_selected_notes)
        a = midi_play_m.addAction('▶ 目前視窗範圍')
        a.triggered.connect(self.play_midi_window)

        menu.addSeparator()
        a = menu.addAction('■ 停止  (S)')
        a.triggered.connect(self.stop_requested.emit)
        menu.addSeparator()
        a = menu.addAction('設定打擊因…')
        a.triggered.connect(self._ctx_hit_factor_dialog)

    def _ctx_build_measure_menu(self, menu, click_ms: float) -> None:
        """小節：新增 / 刪除 / 改 BPM / 改個別拍號，全部針對「點到的那一小節」。

        全曲共通的 BPM 與總拍號不在這裡——那是整首的事，放在「樂曲總資訊」。
        """
        m_idx = self.model.get_measure_at_ms(click_ms)
        cur_bpm = self.model.get_measure_bpm(m_idx)

        a = menu.addAction(f'在第 {m_idx + 1} 小節前插入空白小節…')
        a.triggered.connect(
            lambda _checked=False, _idx=m_idx:
            self.insert_measure_requested.emit(_idx))
        a = menu.addAction(f'刪除第 {m_idx + 1} 小節…')
        a.triggered.connect(
            lambda _checked=False, _idx=m_idx:
            self.delete_measure_requested.emit(_idx))
        menu.addSeparator()

        a = menu.addAction(f'修改第 {m_idx + 1} 小節 BPM（目前 {cur_bpm:.1f}）…')
        a.triggered.connect(
            lambda _checked=False, _idx=m_idx:
            self.set_measure_bpm_requested.emit(_idx))
        try:
            cur_num = self.model.get_beats_per_bar_at_ms(click_ms)
            cur_den = self.model.time_sig_denominator
            for ms, num, den in self.model.time_sig_changes:
                if ms <= click_ms:
                    cur_den = den
                else:
                    break
        except Exception:
            cur_num = self.model.beats_per_bar
            cur_den = self.model.time_sig_denominator
        a = menu.addAction(
            f'修改第 {m_idx + 1} 小節 拍號（目前 {cur_num}/{cur_den}）…')
        a.triggered.connect(
            lambda _checked=False, _idx=m_idx:
            self.set_measure_time_sig_requested.emit(_idx))

    # ==================================================================
    # 右鍵選單（contextMenuEvent）
    # ==================================================================

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        """右鍵選單。同性質的動作收進同一個子選單，頂層只留最常用的幾個。

        分組：音符屬性 / 時間與時值 / 結構 / MIDI / 播放 / 小節，
        頂層直接放「就地複製」「刪除選取」和命中音符時的屬性編輯。
        """
        from PyQt5.QtWidgets import QMenu

        # ── 預覽模式：只顯示播放控制 ──────────────────────────
        if self.preview_mode:
            menu = QMenu(self)
            act_pw = menu.addAction('▶ 播放視窗  (P)')
            act_pw.triggered.connect(self.play_from_window_requested.emit)
            act_pf = menu.addAction('▶ 播放全曲  (Ctrl+P)')
            act_pf.triggered.connect(self.play_full_requested.emit)
            act_stop = menu.addAction('■ 停止  (S)')
            act_stop.triggered.connect(self.stop_requested.emit)
            menu.exec_(event.globalPos())
            return

        pos = event.pos()
        hit = self._hit_test(pos)
        has_sel = bool(self.selected)
        multi_sel = len(self.selected) >= 2

        menu = QMenu(self)

        # ── 命中音符：單顆專屬動作放最上面 ──────────────────────────
        if hit is not None:
            a = menu.addAction('編輯屬性…')
            a.triggered.connect(
                lambda checked=False, _h=hit: self._ctx_note_property_dialog(_h))
            a = menu.addAction('從此音符開始變拍…')
            a.triggered.connect(
                lambda checked=False, _h=hit: self._ctx_note_bpm_dialog(_h))
            menu.addSeparator()

        # ── 分組子選單 ──────────────────────────────────────────────
        note_m = menu.addMenu('音符屬性')
        note_m.setEnabled(has_sel)
        self._ctx_build_note_menu(note_m, has_sel)

        self._ctx_build_velocity_menu(menu.addMenu('設定力度'), has_sel)
        self._ctx_build_time_menu(menu.addMenu('時間與時值'), has_sel)
        # 強弱記號只在音高模式——曲線欄也只有那個模式畫得出來
        if self.pitch_mode:
            self._ctx_build_dynamics_menu(
                menu, max(0.0, self._py_to_ms(pos.y())))
        self._ctx_build_structure_menu(menu.addMenu('結構 / 連接'), has_sel, multi_sel)

        # 輔助組合音符：音階、琶音一次放一組（不必進放置模式一顆一顆點）
        a = menu.addAction('插入音階 / 琶音…')
        a.setToolTip('照譜面偵測到的調性一次放下一組音，起點取右鍵的位置。')
        a.triggered.connect(
            lambda checked=False, _p=QPoint(pos): self._ctx_insert_pattern(_p))

        if self.model.is_midi_mode():
            midi_m = menu.addMenu('MIDI')
            self._ctx_build_midi_menu(midi_m, has_sel)

        menu.addSeparator()

        # ── 頂層直接動作 ────────────────────────────────────────────
        a = menu.addAction('就地複製  (C)')
        a.setEnabled(has_sel)
        a.triggered.connect(self.duplicate_selected)
        a = menu.addAction('刪除選取  (Del)')
        a.setEnabled(has_sel)
        a.triggered.connect(self.delete_selected)

        menu.addSeparator()
        self._ctx_build_playback_menu(menu.addMenu('播放'), has_sel)

        # ── 小節：以點擊位置所在的小節為對象 ───────────────────────
        # 以前只有點在空白處才出現。現在這裡放的是新增/刪除小節這種真的會用到
        # 的操作，點在音符上時一樣需要，所以只要有拍點資料就顯示。
        if self.model.get_beat_entries():
            click_ms = self.mapper.unit_to_ms(self._py_to_unit_abs(pos.y()))
            self._ctx_build_measure_menu(menu.addMenu('小節'), click_ms)

        menu.exec_(event.globalPos())
