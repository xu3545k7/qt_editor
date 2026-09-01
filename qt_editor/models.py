"""
models.py
=========
資料模型層：GNote（單一音符）與 NoteModel（整份譜面）。

設計原則
--------
- 純 Python stdlib（xml.etree.ElementTree）不依賴 lxml
- GNote 同時支援 XML element 與 JSON dict 來源
- NoteModel 負責持有所有音符，並提供 undo/redo 歷史推入
"""

from __future__ import annotations

import bisect
import copy
import json
import logging
import math
import xml.etree.ElementTree as ET
import xml.dom.minidom
from bisect import bisect_left, bisect_right
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import mido
except Exception:  # pragma: no cover - optional dependency at runtime
    mido = None  # type: ignore[assignment]


def _tolerate_broken_key_signatures() -> None:
    """讓 mido 不要因為壞掉的調號 meta 事件就拒絕整個檔案。

    有些轉檔器寫出的 FF 59 調號超出 -7..7（實測到過 18 個升記號），mido 會丟
    KeySignatureError，整個 MIDI 就讀不進來。調號對音符、時間、力度、踏板都
    沒有任何影響，我們也從來不讀它，為了它放棄整份演奏資料不合理。
    """
    if mido is None:
        return
    try:
        from mido.midifiles.meta import MetaSpec_key_signature
    except Exception:  # pragma: no cover - mido internals moved
        return
    if getattr(MetaSpec_key_signature, '_nostalgia_tolerant', False):
        return

    original = MetaSpec_key_signature.decode

    def decode(self, message, data):  # type: ignore[no-untyped-def]
        try:
            original(self, message, data)
        except Exception:
            message.key = 'C'

    MetaSpec_key_signature.decode = decode
    MetaSpec_key_signature._nostalgia_tolerant = True


_tolerate_broken_key_signatures()


def open_midi(path: str, **kwargs: Any):
    """開啟 MIDI，容忍壞掉的 meta 事件與超出範圍的資料位元組。

    所有讀取 MIDI 的路徑都要走這裡，不要直接呼叫 ``mido.MidiFile``，不然同一份
    檔案在某個功能能讀、換一個功能就讀不到。
    """
    if mido is None:
        raise RuntimeError('mido is not available.')
    _tolerate_broken_key_signatures()
    kwargs.setdefault('clip', True)
    return mido.MidiFile(path, **kwargs)

TOTAL_GAME_KEYS: int = 28
INTERNAL_LANE_BASE: int = 0
EXTERNAL_LANE_BASE: int = 1
LEGACY_LANE_BASE: int = 0
EDITOR_BEAT_UNIT_SCALE: int = 1000
OFFICIAL_PIANO_INDEX_MIN: int = 1
OFFICIAL_PIANO_INDEX_MAX: int = 88
MIDI_PIANO_MIN: int = 21
MIDI_PIANO_MAX: int = 108

# note_type 整數 → 遊戲 JSON 所用的 type 字串
# 4 = slide（滑）：以 param1/param2 串成鏈，param1=前一顆 index、param2=下一顆 index、-1 表端點
_NOTE_TYPE_STR: Dict[int, str] = {0: 'tap', 1: 'soft', 2: 'hold', 3: 'staccato', 4: 'slide', 64: 'trill'}


def lane_to_external(lane: int) -> int:
    return int(lane) + EXTERNAL_LANE_BASE


def lane_from_external(lane: int) -> int:
    return int(lane) - EXTERNAL_LANE_BASE


def lane_range_to_external(min_key: int, max_key: int) -> Tuple[int, int]:
    return lane_to_external(min_key), lane_to_external(max_key)


def lane_to_serialized(lane: int, lane_index_base: int) -> int:
    return int(lane) + int(lane_index_base)


def lane_range_to_serialized(min_key: int, max_key: int, lane_index_base: int) -> Tuple[int, int]:
    return (
        lane_to_serialized(min_key, lane_index_base),
        lane_to_serialized(max_key, lane_index_base),
    )


def _decode_json_pitch(raw: Any, encoding: Optional[str]) -> Optional[int]:
    """把 JSON 的音高值解成內部使用的 MIDI 音高（21..108）。

    **JSON 的 `pitch` 欄位一向就是 MIDI**（`to_json_dict` 寫的是 `self.pitch`，
    而且從不寫 `scale_piano`），所以預設不做任何轉換。只有檔案明確宣告
    `pitch_encoding == 'scale_piano'` 時才 +20。

    不要對沒有宣告的舊檔做值域猜測——1~88 之內的 MIDI 音高（例如中央 C=60）
    和 scale_piano 從單一數值分不出來，猜錯就會把音高 +20 再夾在 108，資料
    直接毀掉且救不回來。XML 走的是另一條路（`scale_piano` 欄位），不受影響。
    """
    if raw in (None, ''):
        return None
    value = int(raw)
    if encoding == 'scale_piano':
        return official_piano_index_to_midi(value)
    return value


def official_piano_index_to_midi(pitch: int) -> int:
    """Convert Nostalgia XML scale_piano (A0=1 .. C8=88) to MIDI pitch."""
    return max(MIDI_PIANO_MIN, min(MIDI_PIANO_MAX, int(pitch) + (MIDI_PIANO_MIN - 1)))


def midi_to_official_piano_index(pitch: int) -> int:
    """Convert internal MIDI pitch (A0=21 .. C8=108) back to Nostalgia XML scale_piano."""
    p = int(pitch)
    if p < MIDI_PIANO_MIN:
        # Backward compatibility for legacy editor state that already stored 1..88 internally.
        return max(OFFICIAL_PIANO_INDEX_MIN, min(OFFICIAL_PIANO_INDEX_MAX, p))
    return max(
        OFFICIAL_PIANO_INDEX_MIN,
        min(OFFICIAL_PIANO_INDEX_MAX, p - (MIDI_PIANO_MIN - 1)),
    )


def midi_pitch_to_game_lane_index(pitch: int) -> int:
    """Map MIDI pitch 21..108 to the 28-lane gameplay center index."""
    normalized = (int(pitch) - MIDI_PIANO_MIN) / max(1, (MIDI_PIANO_MAX - MIDI_PIANO_MIN))
    normalized = max(0.0, min(1.0, normalized))
    raw_index = round(normalized * (TOTAL_GAME_KEYS - 1))
    return int(max(0, min(TOTAL_GAME_KEYS - 1, raw_index)))


def game_lane_index_to_midi_pitch(key_index: int) -> int:
    """Approximate a 28-lane center index back to MIDI 21..108."""
    idx = max(0, min(TOTAL_GAME_KEYS - 1, int(key_index)))
    normalized = idx / max(1, (TOTAL_GAME_KEYS - 1))
    pitch = MIDI_PIANO_MIN + round(normalized * (MIDI_PIANO_MAX - MIDI_PIANO_MIN))
    return int(max(MIDI_PIANO_MIN, min(MIDI_PIANO_MAX, pitch)))


def bpm_to_xml_value(bpm: float) -> int:
    """Store XML BPM in the official scaled integer format."""
    return int(round(float(bpm) * 100000.0))


def lane_center_to_width3_range(preferred_center: int) -> Tuple[int, int]:
    """Expand a center lane to the width-3 range used by MIDI restore output."""
    center = max(0, min(TOTAL_GAME_KEYS - 1, int(preferred_center)))
    min_key = center - 1
    max_key = center + 1
    if min_key < 0:
        min_key = 0
        max_key = min(TOTAL_GAME_KEYS - 1, min_key + 2)
    if max_key >= TOTAL_GAME_KEYS:
        max_key = TOTAL_GAME_KEYS - 1
        min_key = max(0, max_key - 2)
    if max_key - min_key < 2:
        if min_key == 0:
            max_key = min(TOTAL_GAME_KEYS - 1, min_key + 2)
        else:
            min_key = max(0, max_key - 2)
    return min_key, max_key


SLIDE_NOTE_TYPE: int = 4


def _k_nearest_samples_by_time(
    samples: List[tuple], times: List[int], t: int, k: int
) -> List[tuple]:
    """從 samples（已依時間排序，times 為其時間 key）取時間最接近 t 的 k 個。"""
    if not samples:
        return []
    i = bisect_left(times, t)
    res: List[tuple] = []
    lo, hi = i - 1, i
    while len(res) < k and (lo >= 0 or hi < len(samples)):
        if lo < 0:
            res.append(samples[hi]); hi += 1
        elif hi >= len(samples):
            res.append(samples[lo]); lo -= 1
        elif (t - times[lo]) <= (times[hi] - t):
            res.append(samples[lo]); lo -= 1
        else:
            res.append(samples[hi]); hi += 1
    return res


def _fit_local_lane(
    local: List[tuple], pitch: int, default_width: int, total_keys: int
) -> Tuple[float, int]:
    """由時間局部樣本推該 pitch 應落的 lane 中心與寬度。

    local: [(pitch, lane_center, width), ...]（時間局部）。
    以局部樣本做 lane_center ~ pitch 的線性回歸；pitch 變異過小時退回平均 lane。
    無樣本時退回「A0..C8 線性鋪滿全 lane」。回傳 (lane_center, width)。
    """
    if not local:
        # A0(21)..C8(108) 線性鋪滿
        lc = (float(pitch) - 21.0) / max(1.0, (108.0 - 21.0)) * (total_keys - 1)
        return max(0.0, min(total_keys - 1, lc)), max(1, int(default_width))

    ps = [float(p) for p, _, _ in local]
    ls = [float(lc) for _, lc, _ in local]
    ws = sorted(int(w) for _, _, w in local)
    width = ws[len(ws) // 2] if ws else int(default_width)
    width = max(1, int(width))

    n = len(local)
    mean_p = sum(ps) / n
    mean_l = sum(ls) / n
    var_p = sum((p - mean_p) ** 2 for p in ps)
    if var_p < 1e-6:
        return max(0.0, min(total_keys - 1, mean_l)), width
    cov = sum((ps[i] - mean_p) * (ls[i] - mean_l) for i in range(n))
    slope = cov / var_p
    lc = mean_l + slope * (float(pitch) - mean_p)
    return max(0.0, min(total_keys - 1, lc)), width


def _assign_track_hands(
    midi_notes: List[Dict[str, Any]], swap_hands: bool = False
) -> Optional[Dict[int, int]]:
    """直接讀 MIDI 本身的左右手結構：以「有音符的音軌」對應左右手。

    MIDI 的左右手就是編碼在不同 track（鋼琴譜常見：第一個有音符的軌=右手/高音部、
    第二個=左手/低音部）。此處忽略沒音符的 tempo/conductor 軌，依 **track 順序** 對應：
    第一個音軌→右手(0)、第二個→左手(1)。若剛好只有 2 軌但順序相反，可用 swap_hands 翻轉。
    3 軌以上才退回用平均音高分（高→右）。單一音軌回傳 None，由呼叫端用音高分手。
    回傳 {track: hand}。
    """
    pitches_by_track: Dict[int, List[int]] = {}
    for m in midi_notes:
        tr = int(m.get('track', 0))
        p = int(m.get('scale_piano', m.get('pitch', 60)))
        pitches_by_track.setdefault(tr, []).append(p)

    tracks = sorted(pitches_by_track)  # 依 track index 排序
    if len(tracks) < 2:
        return None

    if len(tracks) == 2:
        # 直接照 MIDI 的音軌順序：第一軌=右手、第二軌=左手
        right, left = (tracks[1], tracks[0]) if swap_hands else (tracks[0], tracks[1])
        return {right: 0, left: 1}

    # 3 軌以上：track 順序意義不明，改用平均音高分（高→右）
    means = {tr: sum(v) / len(v) for tr, v in pitches_by_track.items()}
    all_p = sorted(p for v in pitches_by_track.values() for p in v)
    mid_p = all_p[len(all_p) // 2]
    mapping = {tr: (0 if means[tr] >= mid_p else 1) for tr in tracks}
    if swap_hands:
        mapping = {tr: (1 - h) for tr, h in mapping.items()}
    return mapping


# note_type 是位元旗標（bitmask），非連續列舉：
#   0x02 = long（長押）、0x04 = glissando（滑鍵）、0x40 = trill（顫音）
#   0x08 = 外觀皮膚旗標（white/black note skin，純視覺、與判定無關）
# 例：10=long|skin、12=slide|skin、72=trill|skin
LONG_BIT:  int = 0x02
SLIDE_BIT: int = 0x04
SKIN_BIT:  int = 0x08
TRILL_BIT: int = 0x40
TRILL_NOTE_TYPE: int = 0x40

# 編輯器專用的純量型別（非位元遮罩，官方資料不使用）：
#   soft=1、staccato=3。注意 staccato(3)=0b11 的位元含 LONG_BIT(0x02)，
#   但它其實是短音符，不能被 note_is_long 誤判成長押。
SOFT_NOTE_TYPE:     int = 1
STACCATO_NOTE_TYPE: int = 3


def note_is_staccato(nt: int) -> bool:
    """編輯器專用 staccato（純量值 3，非位元遮罩）。"""
    return int(nt) == STACCATO_NOTE_TYPE


def note_is_long(nt: int) -> bool:
    # staccato(3) 的位元雖含 LONG_BIT，但它是編輯器短音符，不算長押。
    if int(nt) == STACCATO_NOTE_TYPE:
        return False
    return bool(int(nt) & LONG_BIT)


def note_is_slide(nt: int) -> bool:
    return bool(int(nt) & SLIDE_BIT)


def note_is_trill(nt: int) -> bool:
    return bool(int(nt) & TRILL_BIT)


def note_has_duration(nt: int) -> bool:
    """是否為需要繪製「持續長度」的類型（long / trill）。"""
    return note_is_long(nt) or note_is_trill(nt)


def hold_fix_candidate(nt: int) -> bool:
    """是否為「長度修整」工具的處理對象。

    只處理純長押（long）音符——MIDI 轉檔會把 >=500ms 的音符變成 note_type=2，
    這些正是「hold 太多太長」的來源。trill / slide / staccato / soft / tap 不動。
    保留 skin 位元（0x08）：例如 10=long|skin 仍是修整對象。
    """
    n = int(nt)
    if note_is_trill(n) or note_is_slide(n):
        return False
    return note_is_long(n)


def classify_hold_length(dur_beats: float, tap_th_beats: float,
                         hold_th_beats: float) -> str:
    """依音符時長（拍數）分級，回傳 'tap' / 'mid' / 'long'。

    - dur < tap_th                → 'tap' （太短，砍成 Tap、掉長度）
    - tap_th <= dur < hold_th      → 'mid' （中段，依設定轉 Tap 或保留短 Hold）
    - dur >= hold_th               → 'long'（長段，保留 Hold、尾端提前 release）

    以拍數（音符值）為單位，故與 BPM / 變速無關。
    """
    d = float(dur_beats)
    if d < float(tap_th_beats):
        return 'tap'
    if d < float(hold_th_beats):
        return 'mid'
    return 'long'


def _child_int(elem: ET.Element, tag: str) -> Optional[int]:
    c = elem.find(tag)
    if c is not None and c.text is not None:
        try:
            return int(float(c.text))
        except (ValueError, TypeError):
            pass
    v = elem.get(tag)
    if v is not None:
        try:
            return int(float(v))
        except (ValueError, TypeError):
            pass
    return None


_SUB_U8_TAGS = {'scale_piano', 'velocity', 'off_velocity'}


def sub_elem_to_dict(se: ET.Element) -> Dict[str, Any]:
    """把一個 <sub_note> 元素的所有子欄位轉成 dict（供 JSON 序列化）。"""
    d: Dict[str, Any] = {}
    for child in list(se):
        txt = child.text
        if txt is None:
            continue
        try:
            d[child.tag] = int(float(txt))
        except (ValueError, TypeError):
            d[child.tag] = txt
    return d


def dict_to_sub_elem(d: Dict[str, Any]) -> ET.Element:
    """把 JSON 的 sub_note dict 還原成 <sub_note> 元素（含 __type）。"""
    se = ET.Element('sub_note')
    for tag, val in d.items():
        e = ET.SubElement(se, tag)
        e.text = str(val)
        e.set('__type', 'u8' if tag in _SUB_U8_TAGS else 's32')
    return se


def trill_sub_cells(note: 'GNote', cell_frac: float = 0.5) -> List[Tuple[float, float, int, int]]:
    """把 trill 的每個 sub_note 轉成繪製資訊 (rel_x, rel_w, start_ms, end_ms)。

    - rel_x / rel_w：相對於音符寬度的 0..1 比例
    - x 依該 sub_note 的 scale_piano 在此 trill 音高範圍內線性分布
      → 2 音高 = 左右來回；多音高 = 音階移動的階梯
    無 sub_note 時回傳 []（呼叫端可退回固定交替）。
    """
    subs = []
    for i, se in enumerate(getattr(note, 'sub_elems', []) or []):
        st = _child_int(se, 'start_timing_msec')
        if st is None:
            continue
        en = _child_int(se, 'end_timing_msec')
        pit = _child_int(se, 'scale_piano')
        srcp = _child_int(se, 'src_pitch')
        if srcp is not None and srcp >= 0:
            disp = int(srcp)
        elif pit is not None:
            disp = official_piano_index_to_midi(int(pit))
        else:
            disp = None
        smn = _child_int(se, 'src_min_key')
        smx = _child_int(se, 'src_max_key')
        subs.append({'i': i, 'st': int(st), 'en': int(en) if en is not None else int(st),
                     'pit': int(pit) if pit is not None else 0, 'disp': disp,
                     'smn': smn, 'smx': smx})
    if not subs:
        return []

    zmin = int(note.min_key)
    zw = float(max(1, int(note.max_key) - zmin + 1))
    cells: List[Tuple[float, float, int, int, Optional[int], int]] = []

    # 每個 sub_note 有鍵道（src_min/max_key）→ 依鍵道在區寬內定位（可逐格編輯）
    if all(s['smn'] is not None and s['smx'] is not None for s in subs):
        for s in subs:
            relx = (int(s['smn']) - zmin) / zw
            relw = (int(s['smx']) - int(s['smn']) + 1) / zw
            relx = max(0.0, min(1.0, relx))
            relw = max(0.03, min(1.0 - relx, relw))
            cells.append((relx, relw, s['st'], s['en'], s['disp'], s['i']))
        return cells

    # 官方 trill（無鍵道）→ 依 scale_piano 在音域內線性分布（顯示用）。
    # 用「整數鍵道」計算，與 ensure_trill_cell_lanes 完全一致 → 之後實體化不改變外觀。
    zmax = int(note.max_key)
    zw_i = int(zw)
    pits = [s['pit'] for s in subs]
    pmin, pmax = min(pits), max(pits)
    span = (pmax - pmin) or 1
    width_lanes = max(1, int(round(max(0.05, min(1.0, float(cell_frac))) * zw_i)))
    for s in subs:
        frac = (s['pit'] - pmin) / span
        relx0 = frac * (1.0 - width_lanes / zw)
        min_lane = zmin + int(round(relx0 * zw))
        min_lane = max(zmin, min(zmax - width_lanes + 1, min_lane))
        relx = (min_lane - zmin) / zw
        relw = width_lanes / zw
        cells.append((relx, relw, s['st'], s['en'], s['disp'], s['i']))
    return cells


def trill_fallback_cells(
    start_ms: int, end_ms: int, step_ms: int = 70, cell_frac: float = 0.5,
) -> List[Tuple[float, float, int, int, Optional[int]]]:
    """無 sub_note 資料時的替代：沿時間固定左右各半格交替。"""
    relw = max(0.1, min(1.0, float(cell_frac)))
    cells: List[Tuple[float, float, int, int, Optional[int], int]] = []
    t = int(start_ms)
    end_ms = max(int(start_ms) + 1, int(end_ms))
    left = True
    while t < end_ms:
        relx = 0.0 if left else (1.0 - relw)
        cells.append((relx, relw, t, min(t + step_ms, end_ms), None, -1))
        left = not left
        t += step_ms
    if not cells:
        cells.append((0.0, relw, int(start_ms), end_ms, None, -1))
    return cells


def _set_child_int(se: ET.Element, tag: str, val: int, ty: str = 's32') -> None:
    c = se.find(tag)
    if c is None:
        c = ET.SubElement(se, tag)
    c.text = str(int(val))
    c.set('__type', ty)


def ensure_trill_cell_lanes(trill: 'GNote', cell_frac: float = 0.5) -> None:
    """確保每個 sub_note 都有鍵道（src_min_key/src_max_key），供逐格編輯。

    官方 trill（只有 scale_piano）→ 依現有「音高線性分布」的外觀實體化：
    位置與寬度都沿用目前 render 的樣子，讓實體化時外觀不變，
    之後移動單一格才不會連帶改到其它格的寬度。
    """
    subs = list(getattr(trill, 'sub_elems', []) or [])
    if not subs:
        return
    if all(_child_int(se, 'src_min_key') is not None and _child_int(se, 'src_max_key') is not None
           for se in subs):
        return
    zmin = int(trill.min_key)
    zmax = int(trill.max_key)
    zw = max(1, zmax - zmin + 1)
    width_lanes = max(1, int(round(max(0.05, min(1.0, float(cell_frac))) * zw)))
    pits = [(_child_int(se, 'scale_piano') or 0) for se in subs]
    pmin, pmax = min(pits), max(pits)
    span = (pmax - pmin) or 1
    for se, p in zip(subs, pits):
        if _child_int(se, 'src_min_key') is not None and _child_int(se, 'src_max_key') is not None:
            continue
        # 與 trill_sub_cells 顯示公式完全一致，實體化後外觀不變
        frac = (p - pmin) / span
        relx0 = frac * (1.0 - width_lanes / zw)
        min_lane = zmin + int(round(relx0 * zw))
        min_lane = max(zmin, min(zmax - width_lanes + 1, min_lane))
        _set_child_int(se, 'src_min_key', min_lane)
        _set_child_int(se, 'src_max_key', min_lane + width_lanes - 1)


def _trill_fit_zone_to_cells(trill: 'GNote') -> None:
    """把 trill 的 min_key/max_key 緊貼所有 cell 鍵道的實際範圍。

    可放大也可縮小：例如所有 cell 被移到只剩 1~2 格，trill 也會縮成 1~2，
    不會停留在原本較寬的範圍。
    """
    subs = list(getattr(trill, 'sub_elems', []) or [])
    mins = [_child_int(se, 'src_min_key') for se in subs if _child_int(se, 'src_min_key') is not None]
    maxs = [_child_int(se, 'src_max_key') for se in subs if _child_int(se, 'src_max_key') is not None]
    if mins and maxs:
        trill.min_key = max(0, min(mins))
        trill.max_key = min(TOTAL_GAME_KEYS - 1, max(maxs))


def move_trill_cell(trill: 'GNote', sub_index: int, delta: int) -> bool:
    """把第 sub_index 個 sub_note（mesh 格）左右移動 delta 格；
    超出目前 trill 範圍時擴張 min_key/max_key（修正）。回傳是否有變更。"""
    subs = list(getattr(trill, 'sub_elems', []) or [])
    if not (0 <= sub_index < len(subs)):
        return False
    ensure_trill_cell_lanes(trill)
    se = subs[sub_index]
    smn = _child_int(se, 'src_min_key')
    smx = _child_int(se, 'src_max_key')
    if smn is None or smx is None:
        return False
    width = smx - smn
    new_min = smn + int(delta)
    new_max = new_min + width
    if new_min < 0 or new_max > TOTAL_GAME_KEYS - 1:
        return False
    _set_child_int(se, 'src_min_key', new_min)
    _set_child_int(se, 'src_max_key', new_max)
    _trill_fit_zone_to_cells(trill)
    return True


def shift_trill_cells(trill: 'GNote', delta: int) -> None:
    """整顆 trill 平移時，所有 cell 鍵道同步平移 delta（保持相對位置）。"""
    ensure_trill_cell_lanes(trill)
    for se in list(getattr(trill, 'sub_elems', []) or []):
        smn = _child_int(se, 'src_min_key')
        smx = _child_int(se, 'src_max_key')
        if smn is None or smx is None:
            continue
        _set_child_int(se, 'src_min_key', max(0, min(TOTAL_GAME_KEYS - 1, smn + delta)))
        _set_child_int(se, 'src_max_key', max(0, min(TOTAL_GAME_KEYS - 1, smx + delta)))


def refit_trill_cells(trill: 'GNote') -> None:
    """trill 寬度改變後，把落在區外的 cell 依比例重排回 [min_key, max_key] 內。"""
    subs = list(getattr(trill, 'sub_elems', []) or [])
    if not subs:
        return
    ensure_trill_cell_lanes(trill)
    zmin, zmax = int(trill.min_key), int(trill.max_key)
    zw = max(1, zmax - zmin + 1)
    lanes = [(_child_int(se, 'src_min_key'), _child_int(se, 'src_max_key')) for se in subs]
    valid = [(a, b) for a, b in lanes if a is not None and b is not None]
    if not valid:
        return
    cur_min = min(a for a, _ in valid)
    cur_max = max(b for _, b in valid)
    cur_span = max(1, cur_max - cur_min)
    for se in subs:
        a = _child_int(se, 'src_min_key')
        b = _child_int(se, 'src_max_key')
        if a is None or b is None:
            continue
        w = b - a
        if 0 <= (a - zmin) and (b - zmin) <= zw - 1:
            continue  # 已在範圍內
        # 依相對位置重排到新區寬
        frac = (a - cur_min) / cur_span
        na = zmin + int(round(frac * max(0, zw - 1 - w)))
        na = max(zmin, min(zmax - w, na))
        _set_child_int(se, 'src_min_key', na)
        _set_child_int(se, 'src_max_key', na + w)


def _trill_sub_from_note(n: 'GNote', hand: int) -> ET.Element:
    """把一顆來源音符轉成 trill 的 sub_note 元素。
    除官方欄位外，另存 src_* 擴充欄位供無損還原（官方解析會忽略未知欄位）。"""
    se = ET.Element('sub_note')

    def ac(tag: str, val: Any, ty: str) -> None:
        e = ET.SubElement(se, tag)
        e.text = str(val)
        e.set('__type', ty)

    if n.pitch is not None:
        official = midi_to_official_piano_index(n.pitch)
    else:
        center = (int(n.min_key) + int(n.max_key)) // 2
        official = midi_to_official_piano_index(game_lane_index_to_midi_pitch(center))

    ac('start_timing_msec', int(n.start), 's32')
    ac('end_timing_msec',   int(n.end),   's32')
    ac('scale_piano',       int(official), 'u8')
    ac('velocity',          int(n.velocity) if n.velocity is not None else 100, 'u8')
    ac('track_index',       int(n.track) if n.track is not None else (2 if hand else 1), 's32')
    # ── 還原用擴充欄位（editor 專用，內部 0-based 鍵道）
    ac('src_min_key',   int(n.min_key), 's32')
    ac('src_max_key',   int(n.max_key), 's32')
    ac('src_note_type', int(n.note_type), 's32')
    ac('src_hand',      int(n.hand), 's32')
    ac('src_pitch',     int(n.pitch) if n.pitch is not None else -1, 's32')
    ac('src_velocity',  int(n.velocity) if n.velocity is not None else -1, 's32')
    ac('src_track',     int(n.track) if n.track is not None else -1, 's32')
    return se


def make_trill_from_notes(src_notes: List['GNote'], hand: Optional[int] = None) -> Optional['GNote']:
    """把一組音符打包成單一 trill（note_type=64），每顆音符成為一個 sub_note。"""
    grp = sorted(src_notes, key=lambda n: (int(n.start), int(n.min_key)))
    if not grp:
        return None
    if hand is None:
        hand = int(grp[0].hand)
    t = GNote(None, 0)
    t.note_type = TRILL_NOTE_TYPE
    t.hand = int(hand)
    t.start = min(int(n.start) for n in grp)
    t.end   = max(int(n.end)   for n in grp)
    t.gate  = max(0, t.end - t.start)
    t.min_key = min(int(n.min_key) for n in grp)
    t.max_key = max(int(n.max_key) for n in grp)
    t.pitch = grp[0].pitch
    t.velocity = grp[0].velocity
    t.track = grp[0].track
    t.param1 = t.param2 = t.param3 = 0
    t.note_index = None
    t.sub_elems = [_trill_sub_from_note(n, int(hand)) for n in grp]
    return t


def explode_trill(trill: 'GNote') -> List['GNote']:
    """把 trill 解開還原成一組音符。

    優先用 sub_note 內的 src_* 擴充欄位無損還原；官方 trill 無此欄位時，
    依 scale_piano 在此 trill 音域（min_key..max_key）內的相對位置重建鍵道。
    """
    subs = list(getattr(trill, 'sub_elems', []) or [])
    if not subs:
        return []
    pit_all = [p for p in (_child_int(se, 'scale_piano') for se in subs) if p is not None]
    pmin = min(pit_all) if pit_all else 0
    pmax = max(pit_all) if pit_all else 0
    span = (pmax - pmin) or 1
    zmin, zmax = int(trill.min_key), int(trill.max_key)
    zw = max(0, zmax - zmin)

    out: List['GNote'] = []
    for se in subs:
        st = _child_int(se, 'start_timing_msec')
        if st is None:
            continue
        en = _child_int(se, 'end_timing_msec')
        sp = _child_int(se, 'scale_piano')
        srcp = _child_int(se, 'src_pitch')
        smn = _child_int(se, 'src_min_key')
        smx = _child_int(se, 'src_max_key')
        st_type = _child_int(se, 'src_note_type')
        sh = _child_int(se, 'src_hand')
        # 優先用還原欄位（可表示 None＝-1）；否則退回官方 sub_note 欄位
        src_vel = _child_int(se, 'src_velocity')
        src_trk = _child_int(se, 'src_track')
        vel = src_vel if src_vel is not None else _child_int(se, 'velocity')
        trk = src_trk if src_trk is not None else _child_int(se, 'track_index')

        g = GNote(None, 0)
        g.start = int(st)
        g.end = int(en) if en is not None else int(st) + 1
        g.gate = max(0, g.end - g.start)

        if srcp is not None:
            g.pitch = None if srcp < 0 else int(srcp)
        elif sp is not None:
            g.pitch = official_piano_index_to_midi(sp)
        else:
            g.pitch = None

        if smn is not None and smx is not None:
            g.min_key, g.max_key = int(smn), int(smx)
        else:
            frac = ((sp if sp is not None else pmin) - pmin) / span
            center = zmin + int(round(frac * zw))
            width = min(2, zw) if zw > 0 else 2
            g.min_key = max(0, min(TOTAL_GAME_KEYS - 1, center))
            g.max_key = max(g.min_key, min(TOTAL_GAME_KEYS - 1, g.min_key + width))

        g.note_type = int(st_type) if st_type is not None else 0
        g.hand = int(sh) if sh is not None else int(trill.hand)
        g.velocity = None if (vel is None or int(vel) < 0) else int(vel)
        g.track = None if (trk is None or int(trk) < 0) else int(trk)
        g.note_index = None
        out.append(g)
    return out


def note_type_to_str(nt: int) -> str:
    """回傳 JSON 'type' 字串（優先看基底位元；tap 為預設）。"""
    if note_is_trill(nt):
        return 'trill'
    if note_is_slide(nt):
        return 'slide'
    if note_is_long(nt):
        return 'hold'
    return _NOTE_TYPE_STR.get(int(nt) & ~SKIN_BIT, _NOTE_TYPE_STR.get(int(nt), 'tap'))


def build_slide_index_map(notes: List['GNote']) -> Dict[int, 'GNote']:
    """建立 {原始 index → slide 音符} 對照表，供 param1/param2 鏈結查詢。"""
    result: Dict[int, 'GNote'] = {}
    for n in notes:
        if not note_is_slide(int(getattr(n, 'note_type', 0))):
            continue
        ni = getattr(n, 'note_index', None)
        if ni is not None:
            result[int(ni)] = n
    return result


def slide_next_note(
    n: 'GNote',
    notes: List['GNote'],
    index_map: Optional[Dict[int, 'GNote']] = None,
) -> Optional['GNote']:
    """回傳 slide 鏈上「下一顆」音符。

    param2 = 下一顆的原始 index，-1 代表鏈尾。**已經串過鏈的音符一律以
    param 為準**，不做任何推測 —— 否則串聯只框到某一段時，鏈尾會被「同手
    最近的下一顆」接到下一個區段去，看起來像自己連過去了。

    只有「完全沒有鏈結資訊」（note_index 未指派且 param1/param2 都是 0）的
    滑鍵才退回時間最近的同手滑鍵，那是給編輯器內剛放下、還沒串鏈的音符用的。
    """
    if not note_is_slide(int(getattr(n, 'note_type', 0))):
        return None
    p2 = getattr(n, 'param2', 0)
    if p2 is not None and int(p2) == -1:
        return None  # 鏈尾，無下一顆
    if index_map is None:
        index_map = build_slide_index_map(notes)
    if p2 is not None and int(p2) != 0:
        tgt = index_map.get(int(p2))
        if tgt is not None and tgt is not n and note_is_slide(int(getattr(tgt, 'note_type', 0))):
            return tgt
        return None  # param2 指定了目標卻找不到 → 視為斷鏈，不亂接

    # 到這裡代表 param2 未設定（0/None）。已經串過鏈的音符不推測。
    chained = (
        getattr(n, 'note_index', None) is not None
        or int(getattr(n, 'param1', 0) or 0) != 0
    )
    if chained:
        return None

    # 完全沒鏈結資訊：退回同手、時間較晚且最接近的 slide
    best: Optional['GNote'] = None
    for m in notes:
        if m is n or not note_is_slide(int(getattr(m, 'note_type', 0))):
            continue
        if int(m.hand) != int(n.hand):
            continue
        if int(m.start) <= int(n.start):
            continue
        if best is None or int(m.start) < int(best.start):
            best = m
    return best


def key_kind_from_lane_index(key_index: int) -> int:
    """Match the legacy MIDI converter's 3-zone key kind split."""
    idx = int(key_index)
    if idx < 9:
        return 0
    if idx < 18:
        return 1
    return 2


# ---------------------------------------------------------------------------
# GNote
# ---------------------------------------------------------------------------

HAND_FINGERS = 5
"""一隻手同時按得住幾個鍵。"""

HAND_REACH_SEMITONES = 14
"""一隻手撐得開幾個半音（大九度）。實測 12/14/16 對結果幾乎沒差。"""

HAND_REACH_LANES = 7
"""沒有音高時改用鍵道量同一件事。

實測同時起音的相鄰兩音，鍵道差 / 半音差的中位數是 0.462（11 萬組樣本），
所以 14 個半音約等於 6.5 個鍵道，取 7。鍵道總共只有 0~27 條，比鍵盤壓縮過。"""


class GNote:
    """記憶體中的單一音符。

    可由 XML element 或純 dict 建立。
    屬性修改後須呼叫 apply_back() 才會寫回底層 element。
    """

    def __init__(self, elem: Optional[ET.Element], idx: int):
        self.elem: Optional[ET.Element] = elem
        self.idx: int = idx

        # --- 主要欄位（預設值）
        self.start: int = 0
        self.end: int = 0
        self.gate: int = 0
        self.min_key: int = 0
        self.max_key: int = 0
        self.note_type: int = 0   # 0=tap 1=soft 2=long 3=staccato
        self.hand: int = 0        # 0=右 1=左
        self.track: Optional[int] = None
        self.pitch: Optional[int] = None
        self.velocity: Optional[int] = None
        self.channel: Optional[int] = None
        self.off_velocity: Optional[int] = None
        self.sub_elems: List[ET.Element] = []
        # 「遊戲譜面隱藏」：不佔一個按鍵，但仍然發聲。存檔時會被掛到音高
        # 最接近的可見音符底下，變成它的 sub_note——這正是官方低難度把和絃
        # 塞進同一顆 note 的做法。
        self.hidden: bool = False

        # --- slide（note_type=4）串鏈欄位
        # note_index：此音符在 XML 中的原始 <index>（param1/param2 用它互相參照）
        # param1：鏈上「前一顆」的 index；param2：「下一顆」的 index；-1 表端點
        self.note_index: Optional[int] = None
        self.param1: int = 0
        self.param2: int = 0
        self.param3: int = 0

        if elem is not None:
            self._load_from_elem(elem)

    # ------------------------------------------------------------------
    # 讀取輔助
    # ------------------------------------------------------------------

    @staticmethod
    def _elem_int(elem: ET.Element, tag: str, default: Any = 0) -> Any:
        """從 child text 或 attribute 讀取整數值。"""
        child = elem.find(tag)
        if child is not None and child.text is not None:
            try:
                return int(float(child.text))
            except (ValueError, TypeError):
                pass
        val = elem.get(tag)
        if val is not None:
            try:
                return int(float(val))
            except (ValueError, TypeError):
                pass
        return default

    def _load_from_elem(self, elem: ET.Element) -> None:
        g = self._elem_int
        self.start    = g(elem, 'start_timing_msec')
        self.end      = g(elem, 'end_timing_msec')
        self.gate     = g(elem, 'gate_time_msec')
        self.min_key  = g(elem, 'min_key_index')
        self.max_key  = g(elem, 'max_key_index')
        self.note_type = g(elem, 'note_type')
        # 保留原始 hand 值（官方資料存在 hand=2，不可強制壓成 0/1）
        self.hand     = g(elem, 'hand', 0)

        # track（選擇性）
        has_track = (elem.find('track') is not None) or (elem.get('track') is not None)
        self.track = g(elem, 'track', None) if has_track else None

        # pitch（選擇性）
        has_pitch = (elem.find('scale_piano') is not None) or (elem.get('scale_piano') is not None)
        self.pitch = (
            official_piano_index_to_midi(g(elem, 'scale_piano', None))
            if has_pitch else None
        )
        has_velocity = (elem.find('velocity') is not None) or (elem.get('velocity') is not None)
        self.velocity = g(elem, 'velocity', None) if has_velocity else None
        has_channel = (elem.find('channel') is not None) or (elem.get('channel') is not None)
        self.channel = g(elem, 'channel', None) if has_channel else None
        has_off_velocity = (elem.find('off_velocity') is not None) or (elem.get('off_velocity') is not None)
        self.off_velocity = g(elem, 'off_velocity', None) if has_off_velocity else None

        # slide 串鏈欄位（無則採預設）
        self.note_index = g(elem, 'index', None)
        self.param1 = g(elem, 'param1', 0)
        self.param2 = g(elem, 'param2', 0)
        self.param3 = g(elem, 'param3', 0)

        # sub notes
        sroot = elem.find('sub_note_data')
        if sroot is not None:
            self.sub_elems = list(sroot)

    # ------------------------------------------------------------------
    # 從 JSON dict 建立
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: Dict[str, Any], idx: int,
                  pitch_encoding: Optional[str] = None) -> 'GNote':
        note = cls(None, idx)

        # 時間：支援多種欄位名稱
        note.start = int(d.get('start_timing_msec',
                    d.get('start',
                    d.get('startTime', 0))))
        note.end   = int(d.get('end_timing_msec',
                    d.get('end',
                    d.get('endTime', note.start))))
        note.gate  = int(d.get('gate_time_msec',
                    d.get('gate', max(0, note.end - note.start))))

        # 鍵位：支援 min/max_key_index、min/max_key、startLane/endLane
        note.min_key = int(d.get('min_key_index',
                      d.get('min_key',
                      d.get('startLane', 0))))
        note.max_key = int(d.get('max_key_index',
                      d.get('max_key',
                      d.get('endLane', note.min_key))))

        note.hidden    = bool(d.get('hidden', False))
        note.note_type = int(d.get('note_type', 0))
        note.hand      = int(d.get('hand', 0))
        note.track     = int(d['track']) if d.get('track') not in (None, '') else None
        # 先看 pitch（MIDI，遊戲和本編輯器的主要欄位），沒有才退回 scale_piano
        # （1~88，需要 +20）。順序不能反 —— 現在兩個欄位都會寫出去，優先讀
        # scale_piano 的話會把 1~88 當成 MIDI 值用，音高整個垮掉。
        if d.get('pitch') not in (None, ''):
            note.pitch = _decode_json_pitch(d['pitch'], pitch_encoding or 'midi')
        else:
            note.pitch = _decode_json_pitch(d.get('scale_piano'), 'scale_piano')
        note.velocity  = int(d['velocity']) if d.get('velocity') not in (None, '') else None
        note.channel   = int(d['channel']) if d.get('channel') not in (None, '') else None
        note.off_velocity = int(d['off_velocity']) if d.get('off_velocity') not in (None, '') else None
        note.note_index = int(d['index']) if d.get('index') not in (None, '') else None
        note.param1    = int(d.get('param1', 0) or 0)
        note.param2    = int(d.get('param2', 0) or 0)
        note.param3    = int(d.get('param3', 0) or 0)
        sub_list = d.get('subNotes')
        if isinstance(sub_list, list) and sub_list:
            note.sub_elems = [dict_to_sub_elem(s) for s in sub_list if isinstance(s, dict)]
        return note

    # ------------------------------------------------------------------
    # 寫回 XML element
    # ------------------------------------------------------------------

    def __deepcopy__(self, memo):
        """深拷貝時**不複製** XML 元素，只共用參考。

        `push_history` 每次都 deepcopy 整個 notes_tree，而每顆音符都抓著自己的
        `<note>` 元素（trill/隱藏音還帶一串 `sub_note`），照抄的話等於把整棵 XML
        樹複製一次——實測 1738 顆的官方譜要 298ms / 15MB，**每編輯一次**。

        共用是安全的：音符的欄位才是權威，`apply_back` 在存檔時會用欄位重寫
        元素內容，所以快照裡那份元素就算是舊的也會被蓋掉。`sub_elems` 用淺
        拷貝的 list，避免有人就地改動時影響到快照。
        """
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        for key, value in self.__dict__.items():
            if key == 'elem':
                new.__dict__[key] = value
            elif key == 'sub_elems':
                new.__dict__[key] = list(value) if value else []
            else:
                new.__dict__[key] = copy.deepcopy(value, memo)
        return new

    def apply_back(self, lane_index_base: int = EXTERNAL_LANE_BASE) -> None:
        """將記憶體欄位同步回 self.elem（XML模式）。JSON模式不需要此操作。"""
        if self.elem is None:
            return

        def set_text(tag: str, val: Any) -> None:
            child = self.elem.find(tag)
            if child is not None:
                child.text = str(val)

        def set_attr(tag: str, val: Any) -> None:
            if self.elem.get(tag) is not None:
                self.elem.set(tag, str(val))

        def ensure_text(tag: str, val: Any, type_attr: str) -> None:
            """和 set_text 的差別：欄位不存在時**新建**它。

            官方 <note> 沒有 velocity/channel/off_velocity 這幾個欄位，用
            set_text 寫的話會靜靜地掉光——MIDI 匯入的力度存成 XML 再讀回來
            就全沒了。遊戲端是按名字取欄位（root.Element / TryReadInt），多
            出來的子元素不會有影響。
            """
            child = self.elem.find(tag)
            if child is None:
                child = ET.SubElement(self.elem, tag)
                child.set('__type', type_attr)
            child.text = str(val)

        xml_min_key, xml_max_key = lane_range_to_serialized(
            self.min_key,
            self.max_key,
            lane_index_base,
        )

        pairs = [
            ('start_timing_msec', self.start),
            ('end_timing_msec',   self.end),
            ('gate_time_msec',    self.gate),
            ('min_key_index',     xml_min_key),
            ('max_key_index',     xml_max_key),
            ('note_type',         self.note_type),
            ('hand',              self.hand),
        ]
        for tag, val in pairs:
            set_text(tag, val)
            set_attr(tag, val)

        # slide 串鏈欄位（僅在原 XML 具備該欄位時寫回）
        for tag, val in (('param1', self.param1), ('param2', self.param2), ('param3', self.param3)):
            set_text(tag, val)
            set_attr(tag, val)

        if self.track is not None:
            set_text('track', self.track)
            set_attr('track', self.track)

        if self.pitch is not None:
            official_pitch = midi_to_official_piano_index(self.pitch)
            set_text('scale_piano', official_pitch)
            set_attr('scale_piano', official_pitch)
        # velocity 用官方 sub_note 就在用的欄位名與型別（u8），語意一致。
        if self.velocity is not None:
            ensure_text('velocity', self.velocity, 'u8')
            set_attr('velocity', self.velocity)
        if self.channel is not None:
            ensure_text('channel', self.channel, 'u8')
            set_attr('channel', self.channel)
        if self.off_velocity is not None:
            ensure_text('off_velocity', self.off_velocity, 'u8')
            set_attr('off_velocity', self.off_velocity)

    # ------------------------------------------------------------------
    # 序列化為 JSON dict（存檔用）
    # ------------------------------------------------------------------

        # sub_note_data 要跟著記憶體走。隱藏音符存檔前會把自己的 sub 併回寄主
        # （`_merge_hidden_into_hosts`），但那只改了 `sub_elems`；從 XML 載入的
        # 音符 `elem` 已經存在，存檔時是直接把舊的 elem 掛回去，於是併進來的
        # sub 一個字都沒寫出去——隱藏音符就這樣整顆消失。
        subs = list(getattr(self, 'sub_elems', []) or [])
        sroot = self.elem.find('sub_note_data')
        if subs:
            if sroot is None:
                sroot = ET.SubElement(self.elem, 'sub_note_data')
            for child in list(sroot):
                sroot.remove(child)
            for se in subs:
                sroot.append(se)
        elif sroot is not None:
            # 最後一個 sub 也被刪掉了（例如使用者刪掉隱藏音符）
            self.elem.remove(sroot)

    def to_json_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            'startTime':  self.start,
            'endTime':    self.end,
            'gateTime':   self.gate if self.gate else (self.end - self.start),
            'startLane':  self.min_key,
            'endLane':    self.max_key,
            'hidden':     True if self.hidden else None,
            'pitch':      self.pitch,
            # 同時附上遊戲/XML 用的鋼琴鍵號（1~88）。遊戲只讀 pitch（它自己
            # 就是拿 scale_piano+20 產生 pitch 的，見 ExternalSongLibrary.cs），
            # 這個欄位純粹讓人打開 JSON 時看得懂是哪個琴鍵。
            'scale_piano': (midi_to_official_piano_index(self.pitch)
                            if self.pitch is not None else None),
            'type':       note_type_to_str(self.note_type),
            'note_type':  self.note_type,
            'hand':       self.hand,
            'track':      self.track,
            'velocity':   self.velocity,
            'channel':    self.channel,
            'off_velocity': self.off_velocity,
            'index':      self.note_index,
            'param1':     self.param1,
            'param2':     self.param2,
            'param3':     self.param3,
        }
        if self.pitch is None:
            del d['pitch']
            d.pop('scale_piano', None)
        if self.note_index is None:
            del d['index']
        for key in ('track', 'velocity', 'channel', 'off_velocity', 'hidden'):
            if d[key] is None:
                del d[key]
        # trill 需要保留 sub_note_data（mesh 格 + src_* 還原欄位）才能完整往返
        if note_is_trill(self.note_type) and self.sub_elems:
            d['subNotes'] = [sub_elem_to_dict(se) for se in self.sub_elems]
        return d

    # ------------------------------------------------------------------
    # 快速複製
    # ------------------------------------------------------------------

    def clone(self, new_idx: int) -> 'GNote':
        clone = copy.deepcopy(self)
        clone.idx = new_idx
        return clone

    def __repr__(self) -> str:
        return (f'GNote(idx={self.idx}, start={self.start}, end={self.end}, '
                f'keys={self.min_key}-{self.max_key}, hand={self.hand})')


# ---------------------------------------------------------------------------
# NoteModel
# ---------------------------------------------------------------------------


# General MIDI 音色名稱（program 0~127）。多軌 MIDI 的樂器標示要嘛是
# track_name meta、要嘛是 program_change 的編號，後者要靠這張表變成人看得懂的字。
_GM_PROGRAM_NAMES: Tuple[str, ...] = (
    'Acoustic Grand Piano', 'Bright Acoustic Piano', 'Electric Grand Piano',
    'Honky-tonk Piano', 'Electric Piano 1', 'Electric Piano 2', 'Harpsichord',
    'Clavi', 'Celesta', 'Glockenspiel', 'Music Box', 'Vibraphone', 'Marimba',
    'Xylophone', 'Tubular Bells', 'Dulcimer', 'Drawbar Organ',
    'Percussive Organ', 'Rock Organ', 'Church Organ', 'Reed Organ',
    'Accordion', 'Harmonica', 'Tango Accordion', 'Acoustic Guitar (nylon)',
    'Acoustic Guitar (steel)', 'Electric Guitar (jazz)',
    'Electric Guitar (clean)', 'Electric Guitar (muted)', 'Overdriven Guitar',
    'Distortion Guitar', 'Guitar harmonics', 'Acoustic Bass',
    'Electric Bass (finger)', 'Electric Bass (pick)', 'Fretless Bass',
    'Slap Bass 1', 'Slap Bass 2', 'Synth Bass 1', 'Synth Bass 2', 'Violin',
    'Viola', 'Cello', 'Contrabass', 'Tremolo Strings', 'Pizzicato Strings',
    'Orchestral Harp', 'Timpani', 'String Ensemble 1', 'String Ensemble 2',
    'SynthStrings 1', 'SynthStrings 2', 'Choir Aahs', 'Voice Oohs',
    'Synth Voice', 'Orchestra Hit', 'Trumpet', 'Trombone', 'Tuba',
    'Muted Trumpet', 'French Horn', 'Brass Section', 'SynthBrass 1',
    'SynthBrass 2', 'Soprano Sax', 'Alto Sax', 'Tenor Sax', 'Baritone Sax',
    'Oboe', 'English Horn', 'Bassoon', 'Clarinet', 'Piccolo', 'Flute',
    'Recorder', 'Pan Flute', 'Blown Bottle', 'Shakuhachi', 'Whistle',
    'Ocarina', 'Lead 1 (square)', 'Lead 2 (sawtooth)', 'Lead 3 (calliope)',
    'Lead 4 (chiff)', 'Lead 5 (charang)', 'Lead 6 (voice)', 'Lead 7 (fifths)',
    'Lead 8 (bass + lead)', 'Pad 1 (new age)', 'Pad 2 (warm)',
    'Pad 3 (polysynth)', 'Pad 4 (choir)', 'Pad 5 (bowed)', 'Pad 6 (metallic)',
    'Pad 7 (halo)', 'Pad 8 (sweep)', 'FX 1 (rain)', 'FX 2 (soundtrack)',
    'FX 3 (crystal)', 'FX 4 (atmosphere)', 'FX 5 (brightness)',
    'FX 6 (goblins)', 'FX 7 (echoes)', 'FX 8 (sci-fi)', 'Sitar', 'Banjo',
    'Shamisen', 'Koto', 'Kalimba', 'Bag pipe', 'Fiddle', 'Shanai',
    'Tinkle Bell', 'Agogo', 'Steel Drums', 'Woodblock', 'Taiko Drum',
    'Melodic Tom', 'Synth Drum', 'Reverse Cymbal', 'Guitar Fret Noise',
    'Breath Noise', 'Seashore', 'Bird Tweet', 'Telephone Ring', 'Helicopter',
    'Applause', 'Gunshot',
)


def gm_program_name(program: Optional[int]) -> str:
    """GM program 編號 → 音色名稱。超出範圍就回空字串。"""
    if program is None:
        return ''
    try:
        index = int(program)
    except (TypeError, ValueError):
        return ''
    if 0 <= index < len(_GM_PROGRAM_NAMES):
        return _GM_PROGRAM_NAMES[index]
    return ''


#: 一筆 undo 快照每顆音符大約佔多少位元組（實測 400~500）
_UNDO_BYTES_PER_NOTE = 500
#: 再大的譜面也至少留這麼多步可以復原
_UNDO_MIN_DEPTH = 8


class NoteModel:
    """整份譜面的資料狀態。

    負責
    ----
    - 持有 notes_tree（記憶體唯一資料來源）
    - 提供 undo stack
    - 持有 BPM / 時間基準 / beat_data 等後設資料
    - 不含任何 UI 邏輯
    """

    def __init__(self) -> None:
        # --- XML 狀態
        self.root: Optional[ET.Element] = None
        self.tree: Optional[ET.ElementTree] = None

        # --- 通用後設資料
        self.file_format: str = 'xml'       # 'xml' | 'json' | 'midi'
        # MIDI 匯入時選了「不轉換」——還沒排譜，只有音高檢視有意義
        self.midi_unarranged: bool = False
        # (track, channel) → 樂器名稱。只有從 MIDI 載入才有，XML/JSON 沒有
        # 這個資訊。畫面上的聲部圖例用它標「哪個顏色是哪個樂器」。
        self.midi_voice_names: Dict[Tuple[int, int], str] = {}
        self.current_file: Optional[str] = None
        self.json_meta: Dict[str, Any] = {}
        self.xml_lane_index_base: int = EXTERNAL_LANE_BASE
        self.midi_data: Optional[Dict[str, Any]] = None

        # --- 音樂參數
        self.bpm: float = 120.0
        self.beats_per_bar: int = 4
        self.time_sig_denominator: int = 4
        self.beat_offset_ms: float = 0.0
        self.music_end_ms: float = 0.0
        # [(ms, numerator, denominator), ...] sorted by ms — empty = single time sig
        self.time_sig_changes: List[Tuple[int, int, int]] = []

        # --- 延音踏板（CC64）
        # [[start_ms, end_ms], ...]，依 start 排序、互不重疊。
        # 存「區間」而不是原始的 on/off 事件：編輯器要讓人拖出一段踏板，
        # 區間才是可直接操作的單位；匯出時再展開回 CC64 的一對訊息。
        self.pedal_spans: List[List[float]] = []
        # 強弱記號：{hand: [[ms, level, ramp], ...]}。ramp=True 表示從這個
        # 記號漸變到下一個（cresc./dim.），False 是維持到下一個（p、f）。
        self.dynamics: Dict[int, List[List[float]]] = {}

        # --- 音符資料
        self.notes_tree: List[GNote] = []  # 唯一資料來源
        self.notes: List[GNote] = []       # 排序後的顯示快取

        # --- Undo 歷史
        self.undo_stack: List[List[GNote]] = []
        self.undo_limit: int = 50

        # --- 狀態旗標
        self.dirty: bool = False

        # --- 播放/渲染熱路徑快取（beat_data 派生資料）
        # 以「內容簽章」失效，避免每幀重建 beat 清單與小節邊界造成播放卡頓。
        self._cache_be: Optional[List[tuple]] = None
        self._cache_be_sig: Any = None
        self._cache_pb: Optional[List[Tuple[float, int, float, int]]] = None
        self._cache_pb_sig: Any = None
        self._cache_scale: Optional[float] = None
        self._cache_scale_sig: Any = None
        # beat_data 是 per-bar 還是 per-beat：只在載入時判斷一次後釘住，
        # 不隨編輯重新偵測（見 _beat_entry_mode）。
        self._epb_mode: Optional[str] = None
        self._epb_count: Optional[int] = None

    def _beat_sig(self) -> Any:
        """beat_data 的廉價內容簽章：任何增刪/位移都會改變它，供快取失效判斷。

        只讀取子節點數與首尾 ms（皆為 O(1)），不重建整個清單。
        """
        if self.root is not None:
            br = self.root.find('beat_data')
            if br is None:
                return ('xml', id(self.root), -1)
            n = len(br)
            first = mid = last = None
            if n:
                try:
                    first = br[0].findtext('start_timing_msec')
                    mid = br[n // 2].findtext('start_timing_msec')
                    last = br[n - 1].findtext('start_timing_msec')
                except Exception:
                    first = mid = last = None
            return ('xml', id(self.root), n, first, mid, last)
        jm = self.json_meta or {}
        bt = jm.get('beat_timings')
        if isinstance(bt, (list, tuple)) and bt:
            return ('json', len(bt), bt[0], bt[len(bt) // 2], bt[-1])
        return ('none',)

    # ------------------------------------------------------------------
    # Undo 歷史
    # ------------------------------------------------------------------

    def push_history(self) -> None:
        # Snapshot a fuller model state so undo can revert beat timings and metadata too
        snap: Dict[str, Any] = {
            'notes_tree': copy.deepcopy(self.notes_tree),
            'pedal_spans': copy.deepcopy(self.pedal_spans),
            'dynamics': copy.deepcopy(self.dynamics),
            'time_sig_changes': copy.deepcopy(self.time_sig_changes),
            'json_meta': copy.deepcopy(self.json_meta),
            'music_end_ms': float(self.music_end_ms),
            'bpm': float(self.bpm),
            'beats_per_bar': int(self.beats_per_bar),
            'time_sig_denominator': int(self.time_sig_denominator),
            'xml_lane_index_base': int(self.xml_lane_index_base),
            'root_xml': self._root_snapshot(),
        }
        self.undo_stack.append(snap)
        while len(self.undo_stack) > self._undo_depth_budget():
            self.undo_stack.pop(0)
        self.dirty = True

    def _undo_depth_budget(self) -> int:
        """這份譜面能留幾筆歷史。

        固定 50 筆在大譜面上會吃掉幾百 MB——實測 4716 顆的譜一筆約 2MB，
        50 筆就是 94MB，再加上 Qt 與音訊緩衝，記憶體小的機器直接被 OOM 殺掉
        （使用者回報「比較差的電腦會閃退」）。改成用**記憶體預算**回推深度：
        小譜面照樣 50 筆，大譜面自動變淺但至少留 `_UNDO_MIN_DEPTH` 筆。
        """
        try:
            from .settings import settings as _st
            budget_mb = float(_st.get('undo_memory_mb', 64))
        except Exception:                       # noqa: BLE001
            budget_mb = 64.0
        per_snapshot = max(1.0, len(self.notes_tree) * _UNDO_BYTES_PER_NOTE)
        depth = int(budget_mb * 1024 * 1024 / per_snapshot)
        return max(_UNDO_MIN_DEPTH, min(int(self.undo_limit), depth))

    def _root_snapshot(self) -> Optional[str]:
        """序列化 XML 樹，但**不含 note_data**。

        音符的權威是 `notes_tree`（`undo` 的註解講得很清楚），存檔時
        `save_xml` 本來就會把 note_data 整個清空重建。把那一段留在快照裡是
        純浪費——它佔了整棵樹的絕大部分，實測 `tostring(root)` 548ms 裡
        note_data 自己就佔 469ms。
        """
        if self.root is None:
            return None
        nd = self.root.find('note_data')
        if nd is None:
            return ET.tostring(self.root, encoding='unicode')
        # 清空 note_data 再序列化，但**元素本身留在原位**。整個拆掉的話還原出來
        # 的樹沒有這個節點，`save_xml` 會在最後面重新長一個，存出來的檔案就只是
        # 把 note_data 從檔頭搬到檔尾——內容一樣但整份檔案全變了。
        children = list(nd)
        for child in children:
            nd.remove(child)
        try:
            return ET.tostring(self.root, encoding='unicode')
        finally:
            for child in children:
                nd.append(child)

    def undo(self) -> bool:
        """退回上一個快照；回傳是否成功。"""
        if not self.undo_stack:
            return False
        snap = self.undo_stack.pop()
        # restore notes
        self.notes_tree = copy.deepcopy(snap.get('notes_tree', []))
        self.pedal_spans = copy.deepcopy(snap.get('pedal_spans', []))
        self.dynamics = copy.deepcopy(snap.get('dynamics', {}))
        # restore metadata
        self.time_sig_changes = copy.deepcopy(snap.get('time_sig_changes', []))
        self.json_meta = copy.deepcopy(snap.get('json_meta', {}))
        self.music_end_ms = float(snap.get('music_end_ms', 0.0))
        self.bpm = float(snap.get('bpm', self.bpm))
        self.beats_per_bar = int(snap.get('beats_per_bar', self.beats_per_bar))
        self.time_sig_denominator = int(snap.get('time_sig_denominator', self.time_sig_denominator))
        self.xml_lane_index_base = int(snap.get('xml_lane_index_base', self.xml_lane_index_base))
        root_xml = snap.get('root_xml')
        if root_xml is not None:
            try:
                self.root = ET.fromstring(root_xml)
                self.tree = ET.ElementTree(self.root)
            except Exception:
                self.root = None
                self.tree = None
        else:
            self.root = None
            self.tree = None

        # notes_tree 的深拷貝才是權威狀態。
        #
        # 舊版在還原 XML 之後，會改用 root 的 note_data 重新解析 notes_tree。
        # 但 XML 只反映「載入時的那份檔案」，不包含記憶體裡新增/刪除的音符
        # （那些要到存檔才寫回 XML）。結果是：放置一顆音符後按 undo，整份譜
        # 會跳回檔案原狀、把這次工作階段新增的音符全部吃掉，而且之後再按
        # undo 都沒有反應。改成優先用快照裡的深拷貝。
        snap_notes = snap.get('notes_tree')
        note_data_xml = snap.get('note_data_xml')
        if snap_notes is not None:
            self.notes_tree = copy.deepcopy(snap_notes)
        elif self.root is not None and (self.root.find('note_data') is not None or note_data_xml is not None):
            # prefer actual root's note_data; fall back to serialized note_data if needed
            nd = self.root.find('note_data')
            if nd is None and note_data_xml:
                try:
                    nd = ET.fromstring(note_data_xml)
                except Exception:
                    nd = None
            if nd is not None:
                notes_elems = nd.findall('note')
                self.notes_tree = [GNote(elem, i) for i, elem in enumerate(notes_elems)]
                lane_base = int(snap.get('xml_lane_index_base', self._guess_lane_index_base(self.notes_tree)))
                self.xml_lane_index_base = lane_base
                self._normalize_notes_to_internal(self.notes_tree, lane_base)
                # try to preserve original keys/pitch by matching starts/ends from snapshot
                if snap_notes:
                    # build list of unmatched snapshot notes
                    unmatched = [on for on in snap_notes]
                    for n in self.notes_tree:
                        best_idx = None
                        best_score = None
                        n_start = int(getattr(n, 'start', 0))
                        n_end = int(getattr(n, 'end', 0))
                        for i, on in enumerate(unmatched):
                            try:
                                o_start = int(getattr(on, 'start', 0))
                                o_end = int(getattr(on, 'end', 0))
                            except Exception:
                                continue
                            score = abs(o_start - n_start) + abs(o_end - n_end)
                            if best_score is None or score < best_score:
                                best_score = score
                                best_idx = i
                        # accept match if within small tolerance (e.g., 8 ms total difference)
                        if best_idx is not None and best_score is not None and best_score <= 8:
                            on = unmatched.pop(best_idx)
                            try:
                                n.min_key = int(getattr(on, 'min_key', n.min_key))
                                n.max_key = int(getattr(on, 'max_key', n.max_key))
                                n.pitch = getattr(on, 'pitch', n.pitch)
                            except Exception:
                                pass
            else:
                self.notes_tree = []
        else:
            # JSON-only or no XML root: restore deepcopy of notes_tree
            self.notes_tree = copy.deepcopy(snap.get('notes_tree', []))
        self.pedal_spans = copy.deepcopy(snap.get('pedal_spans', []))
        self.dynamics = copy.deepcopy(snap.get('dynamics', {}))

        self.rebuild_display_cache()
        self.dirty = True
        return True

    # ------------------------------------------------------------------
    # 音符顯示快取
    # ------------------------------------------------------------------

    def rebuild_display_cache(self) -> None:
        """將 notes_tree 依 start 排序後放入 notes（顯示用）。"""
        self.notes = sorted(self.notes_tree, key=lambda n: (n.start, n.min_key))
        # 重新對齊 idx
        for i, n in enumerate(self.notes):
            n.idx = i

    def trim_pedal_sustained_holds(
        self, gap_ms: int = 100, hand_reach_semitones: int = 12
    ) -> int:
        """裁切被踏板延長的長音。

        兩種情況都代表「前面那個長音其實是踏板踩住的殘響，不是真的按著」：

        1. 長音的**時值中間**又出現完全相同音高的 note —— 同一個鍵不可能在
           還按著的時候再被按一次。
        2. 長音還在響的時候，**同一隻手**出現了距離超過一個八度的音符 ——
           一隻手構不到，所以那個長音一定已經放開了。

        兩種都把尾端裁到「下一顆的 start − gap_ms」（預設 100ms），不是原長度
        硬塞進去。只有 note_type == 2（長條）會被裁；非長條不需要裁 —— 除了
        start 完全相同的那顆以外，其他時間開始的音符可以直接疊在後面。

        回傳被裁切的音符數。
        """
        gap = max(0, int(gap_ms))
        reach = max(1, int(hand_reach_semitones))

        pitched = [n for n in self.notes_tree
                   if getattr(n, 'pitch', None) is not None]
        by_pitch: Dict[int, List['GNote']] = {}
        by_hand: Dict[int, List['GNote']] = {}
        for note in pitched:
            by_pitch.setdefault(int(note.pitch), []).append(note)
            by_hand.setdefault(int(note.hand), []).append(note)
        for items in by_pitch.values():
            items.sort(key=lambda n: int(n.start))
        for items in by_hand.values():
            items.sort(key=lambda n: int(n.start))

        changed = 0
        for hold in pitched:
            if int(hold.note_type) != 2:
                continue
            start = int(hold.start)
            end = int(hold.end)
            pitch = int(hold.pitch)
            cut: Optional[int] = None

            # (1) 中間出現同音高
            same = by_pitch.get(pitch, [])
            starts = [int(n.start) for n in same]
            idx = bisect_right(starts, start)
            if idx < len(same):
                nxt = int(same[idx].start)
                if nxt < end:
                    cut = nxt

            # (2) 中間出現同手、超過一個八度的音符（手構不到）
            mates = by_hand.get(int(hold.hand), [])
            starts = [int(n.start) for n in mates]
            idx = bisect_right(starts, start)
            while idx < len(mates):
                other = mates[idx]
                other_start = int(other.start)
                if other_start >= end:
                    break
                if abs(int(other.pitch) - pitch) > reach:
                    if cut is None or other_start < cut:
                        cut = other_start
                    break
                idx += 1

            if cut is None:
                continue
            new_end = max(start + 1, cut - gap)
            if new_end >= end:
                continue
            hold.end = new_end
            hold.gate = max(1, new_end - start)
            changed += 1

        if changed:
            self.rebuild_display_cache()
            self.dirty = True
        return changed

    def hand_onset_timelines(self) -> Dict[int, List[int]]:
        """每支手的「起音時間軸」：{hand: 排序後且去重的 start 清單}。

        **只看 hand，完全不看 track / channel**——同一支手的音符即使被拆在不同
        MIDI track（鋼琴譜常見：踏板軌、分部軌），在演奏上仍然是同一隻手，所以
        必須併成同一條時間軸來找「下一顆最早的音」。
        """
        starts_by_hand: Dict[int, Set[int]] = {}
        for note in self.notes_tree:
            starts_by_hand.setdefault(int(note.hand), set()).add(int(note.start))
        return {hand: sorted(values) for hand, values in starts_by_hand.items()}

    def next_same_hand_onset(
        self,
        note: 'GNote',
        timelines: Optional[Dict[int, List[int]]] = None,
    ) -> Optional[int]:
        """該音符之後、**同一支手**（跨所有 track）最早的起音時間；沒有則 None。

        同一時間開始的音符算和弦，不算「下一顆」。
        """
        if timelines is None:
            timelines = self.hand_onset_timelines()
        starts = timelines.get(int(note.hand))
        if not starts:
            return None
        index = bisect_right(starts, int(note.start))
        return starts[index] if index < len(starts) else None

    def hand_note_timelines(self) -> Dict[int, List['GNote']]:
        """每隻手的音符照起音排序（跨所有 track）。"""
        by_hand: Dict[int, List['GNote']] = {}
        for note in self.notes_tree:
            by_hand.setdefault(int(note.hand), []).append(note)
        for notes in by_hand.values():
            notes.sort(key=lambda n: (int(n.start), int(n.end)))
        return by_hand

    @staticmethod
    def _same_key(a: 'GNote', b: 'GNote') -> bool:
        """兩顆音會不會搶到同一個鍵——**比鍵道範圍，不是比音高**。

        比音高太鬆：88 鍵壓成 28 條鍵道之後，不同音高常常落在重疊的鍵道上，
        那在遊戲裡就是同一根手指的位置。官方的做法量得很乾淨：real 難度 13175 顆
        長押裡有 1019 顆（7.7%）的尾巴確實蓋過同手的下一顆音，但那 1019 顆
        **沒有任何一顆**的鍵道範圍是重疊的（0.0%）。也就是說官方允許長押壓過去，
        條件正是「不搶同一個鍵」。
        """
        return not (int(a.max_key) < int(b.min_key) or int(b.max_key) < int(a.min_key))

    def release_deadline(
        self,
        hold: 'GNote',
        notes: List['GNote'],
        starts: List[int],
        fingers: int = HAND_FINGERS,
        reach: int = HAND_REACH_SEMITONES,
        lane_reach: float = HAND_REACH_LANES,
        max_ring_notes: int = 0,
    ) -> Optional[int]:
        """這顆長押**非放開不可**的時間點；整段都按得住就回 None。

        「同一支手後面有音就得放開」是錯的——那正是分解和弦的樣子：手指按著前面
        的音，其它指頭繼續彈下去。實測全曲庫 32286 顆長押裡有 20896 顆和同手的下
        一顆重疊，其中 **85.6% 一隻手完全按得住**，只有 13.4% 是「下一顆就是同一
        個鍵」（那才真的得先放開），1.0% 超過五指或超過手的跨度。舊規則把那 85.6%
        全砍了，合計砍掉約 1735 秒的長度，最狠的一顆砍掉 12 秒。

        所以只有三種情況算衝突：

        1. **下一顆用到同一個鍵** —— 沒放開就按不下去。
        2. **同時要按的鍵超過五個** —— 一隻手只有五根手指。
        3. **同時按著的鍵跨度超過 `reach` 個半音** —— 手撐不開。

        `reach` 取 14（大九度）。這個值幾乎不影響結果：12 / 14 / 16 三種在全曲庫
        只差 100 顆左右（3001 / 2962 / 2902），會被裁的比例都在 14% 上下。

        `max_ring_notes > 0` 再加第四種：長押期間同手進來的音超過這個數就放開。
        一組分解和弦就那麼幾顆，響過一長串之後和聲早就換過了。官方有重疊的長押
        裡 92% 只讓 3 顆以內的音進來（1 顆 50.7%、2 顆 28.8%、3 顆 12.7%）。
        """
        span_limit = max(1, int(reach))
        index = bisect_right(starts, int(hold.start))   # 同時起音的算和弦，不算下一顆
        end = int(hold.end)
        rung = 0                                         # 長押期間已經進來幾顆

        while index < len(notes):
            nxt = notes[index]
            if int(nxt.start) >= end:
                return None                              # 已經超過尾端，沒有衝突
            if self._same_key(hold, nxt):
                return int(nxt.start)
            if max_ring_notes > 0 and rung >= int(max_ring_notes):
                return int(nxt.start)
            rung += 1

            # 這一刻這隻手同時按著的鍵。往回找到「起音早於它、而且還沒放開」的音符；
            # 長押不會無限長，掃到 12 秒之前就可以停。
            moment = int(nxt.start)
            keys = []
            back = index
            while back > 0:
                back -= 1
                other = notes[back]
                if moment - int(other.start) > 12000:
                    break
                if int(other.end) > moment:
                    keys.append(other)
            keys.append(nxt)

            distinct = len({int(n.pitch) if n.pitch is not None else -int(n.min_key) - 1
                            for n in keys})
            if distinct > max(1, int(fingers)):
                return moment

            # 跨度：有音高就用半音，沒有就用鍵道（見 HAND_REACH_LANES）。
            pitches = [int(n.pitch) for n in keys if n.pitch is not None]
            if len(pitches) == len(keys):
                if max(pitches) - min(pitches) > span_limit:
                    return moment
            else:
                centres = [(int(n.min_key) + int(n.max_key)) / 2.0 for n in keys]
                if max(centres) - min(centres) > lane_reach:
                    return moment
            index += 1

        return None

    def enforce_hold_tail_gap(
        self,
        gap_ms: int,
        targets: Optional[Iterable['GNote']] = None,
        fingers: int = HAND_FINGERS,
        reach: int = HAND_REACH_SEMITONES,
        lane_reach: float = HAND_REACH_LANES,
        only_conflicts: bool = True,
        sequential_gap: bool = False,
        max_ring_notes: int = 0,
    ) -> int:
        """把長條尾端壓到「非放開不可的那一刻 − gap_ms」以內。

        `only_conflicts=False` 回到舊規則：一律裁到同手下一顆的起音。那條規則會
        把分解和弦一起砍掉（實測 85.6% 的裁切是手明明按得住的），但「所有長條都
        不准蓋過同手的下一顆」有時候就是想要的效果，所以留成選項。

        判斷哪一刻非放開不可交給 `release_deadline()`：同手後面有音**不等於**要
        放開，分解和弦就是按著彈的。只有同一個鍵、超過五指、或超過手的跨度才算。

        - 同一支手的音符**不論在哪個 track** 都算。
        - 和長條同時開始的音符視為和弦，不算「下一顆」。
        - 只縮短長條本身（note_type==2）；其他音符長度不動。
        - 只會縮短、不會拉長；間距本來就夠的不動。

        `sequential_gap=True` 再多一條：**同手下一顆和這顆長押時間上沒有重疊**
        時，就是前後關係，即使沒有物理衝突也要把 `gap_ms` 的間隔留出來。有重疊
        的一律當分解和弦不動。只靠物理衝突的話，尾巴貼著下一顆（只差幾毫秒）
        也不會被處理——那正是「啥都不會裁」的原因。

        `targets` 給定時只處理那些音符（時間軸仍取自整份譜面），回傳被裁切的數量。
        """
        gap = max(0, int(gap_ms))
        hands = self.hand_note_timelines()
        starts_by_hand = {hand: [int(n.start) for n in notes]
                          for hand, notes in hands.items()}
        pool = self.notes_tree if targets is None else targets
        changed = 0

        for hold in pool:
            if int(hold.note_type) != 2:
                continue
            notes = hands.get(int(hold.hand))
            if not notes:
                continue
            if only_conflicts:
                deadline = self.release_deadline(
                    hold, notes, starts_by_hand[int(hold.hand)], fingers, reach,
                    lane_reach, max_ring_notes)
                if sequential_gap:
                    # 同手下一顆和這顆長押「時間上沒有重疊」＝ 前後關係，
                    # 那就該留出最小間隔；有重疊的才是分解和弦，不動它。
                    nxt = self.next_same_hand_onset(hold)
                    if nxt is not None and int(hold.end) <= nxt:
                        deadline = nxt if deadline is None else min(deadline, nxt)
            else:
                deadline = self.next_same_hand_onset(hold)
            if deadline is None:
                continue
            if int(hold.end) + gap <= deadline:
                continue  # 間距已足夠，不需裁切
            new_end = max(int(hold.start) + 1, deadline - gap)
            if new_end >= int(hold.end):
                continue
            hold.end = new_end
            hold.gate = new_end - int(hold.start)
            changed += 1

        return changed

    def resolve_hold_tail_overlaps(self, gap_ms: int, only_conflicts: bool = True,
                                   sequential_gap: bool = False,
                                   max_ring_notes: int = 0) -> int:
        """對整份譜面套用長條尾端最小間隔（見 `enforce_hold_tail_gap`）。"""
        return self.enforce_hold_tail_gap(
            gap_ms, only_conflicts=only_conflicts, sequential_gap=sequential_gap,
            max_ring_notes=max_ring_notes)

    @staticmethod
    def _lanes_overlap(a: 'GNote', b: 'GNote') -> bool:
        return not (int(a.max_key) < int(b.min_key) or int(a.min_key) > int(b.max_key))

    @staticmethod
    def _place_note(note: 'GNote', new_min: int) -> bool:
        """把音符整條移到 new_min（保持寬度）。超出鍵盤範圍就不動，回傳是否移動。"""
        width = max(0, int(note.max_key) - int(note.min_key))
        new_min = int(new_min)
        if new_min < 0 or new_min + width > TOTAL_GAME_KEYS - 1:
            return False
        if new_min == int(note.min_key):
            return False
        note.min_key = new_min
        note.max_key = new_min + width
        return True

    def resolve_horizontal_overlaps_report(
        self, tolerance_ms: int = 0, time_overlap: bool = False,
    ) -> Dict[str, int]:
        """整理左右重疊，回傳 {'moved', 'conflicts', 'unresolved'}。

        兩趟處理**同時起音的和弦**（start 相差在 tolerance_ms 內）：

        1. 由右往左：左邊那顆往左讓開，右邊那顆不動（維持音高由左到右的排列）。
        2. 由左往右：第一趟被鍵盤左邊界卡住、擠不下去的，改推右邊那顆往右。
           少了這一趟，「最左邊那顆已經貼齊 0 號鍵」的譜會一顆都不動，卻回報
           「沒有需要整理的重疊」——看起來就像工具壞了。

        `time_overlap=True` 再加一趟：**同時發聲**（時間區間重疊）但起音時間不同
        的音符也算重疊，例如長條還按著的時候，同樣鍵位又出現一顆音。這是編輯器
        裡真正看得到的重疊；只比對 start 的話這種完全抓不到。這一趟移動的是
        **後起音的那顆**（先響的已經在畫面上了，動它會讓玩家看到的東西跳掉），
        並優先往它原本所在的那一側讓開。

        每顆音符都保持自己的寬度。兩邊都塞不下的算 unresolved，不會硬擠。
        """
        tol = max(0, int(tolerance_ms))
        ordered_by_time = sorted(self.notes_tree, key=lambda n: int(n.start))

        moved = 0
        conflicts = 0
        unresolved = 0

        # ── 和弦（同時起音）──────────────────────────────────────────
        groups: List[List[GNote]] = []
        for note in ordered_by_time:
            if groups and (int(note.start) - int(groups[-1][0].start)) <= tol:
                groups[-1].append(note)
            else:
                groups.append([note])

        for notes in groups:
            ordered = sorted(
                notes,
                key=lambda note: (int(note.min_key), int(note.max_key), int(note.idx)),
            )
            # 第一趟：由右往左，左邊那顆往左讓開
            for index in range(len(ordered) - 2, -1, -1):
                left, right = ordered[index], ordered[index + 1]
                if int(left.max_key) < int(right.min_key):
                    continue
                conflicts += 1
                width = max(0, int(left.max_key) - int(left.min_key))
                if self._place_note(left, int(right.min_key) - 1 - width):
                    moved += 1

            # 第二趟：由左往右，剩下的改推右邊那顆往右
            for index in range(1, len(ordered)):
                left, right = ordered[index - 1], ordered[index]
                if int(left.max_key) < int(right.min_key):
                    continue
                if self._place_note(right, int(left.max_key) + 1):
                    moved += 1
                else:
                    unresolved += 1

        # ── 同時發聲但起音不同（長條 vs 之後的音符）────────────────────
        if time_overlap:
            active: List[GNote] = []
            for later in sorted(self.notes_tree,
                                key=lambda n: (int(n.start), int(n.min_key))):
                start = int(later.start)
                active = [n for n in active if int(n.end) > start + tol]
                for earlier in active:
                    if int(earlier.start) + tol >= start:
                        continue          # 同和弦，前面那兩趟已經處理過
                    if not self._lanes_overlap(earlier, later):
                        continue
                    conflicts += 1
                    width = max(0, int(later.max_key) - int(later.min_key))
                    # 先往自己原本所在的那一側讓，讓不動再試另一側
                    if int(later.min_key) >= int(earlier.min_key):
                        sides = (int(earlier.max_key) + 1,
                                 int(earlier.min_key) - 1 - width)
                    else:
                        sides = (int(earlier.min_key) - 1 - width,
                                 int(earlier.max_key) + 1)
                    if any(self._place_note(later, s) for s in sides):
                        moved += 1
                    else:
                        unresolved += 1
                active.append(later)

        return {'moved': moved, 'conflicts': conflicts, 'unresolved': unresolved}

    def resolve_horizontal_overlaps(
        self, tolerance_ms: int = 0, time_overlap: bool = False,
    ) -> int:
        """整理左右重疊，回傳被移動的音符數（細節見 `..._report`）。"""
        return self.resolve_horizontal_overlaps_report(
            tolerance_ms, time_overlap)['moved']

    @staticmethod
    def align_notes_edge(notes: List[GNote], target: str, value: int) -> int:
        """把一組音符的 start 或 end 對齊到同一個絕對值（縮放，固定另一端）。

        target=='start'：設定 start，保持 end 不動（clamp 到 [0, end-1]）。
        target=='end'  ：設定 end，保持 start 不動（至少 start+1）。
        回傳實際被修改的音符數；gate 會同步更新。
        """
        if target not in ('start', 'end'):
            return 0
        value = max(0, int(value))
        changed = 0
        for n in notes:
            if target == 'start':
                new_start = max(0, min(value, int(n.end) - 1))
                if new_start == int(n.start):
                    continue
                n.start = new_start
            else:  # 'end'
                new_end = max(int(n.start) + 1, value)
                if new_end == int(n.end):
                    continue
                n.end = new_end
            n.gate = int(n.end) - int(n.start)
            changed += 1
        return changed

    def scale_all_time(self, factor: float) -> int:
        """把整個時間軸等比縮放 factor 倍，保留音符對小節的相對位置。

        會縮放：音符 start/end/gate、trill/slide 子音符的 start/end_timing_msec、
        beat_data（XML）與 json beat_timings、music_end_ms、拍號變化時間。
        供「調整 BPM（等比縮放）」使用：factor = 舊bpm / 新bpm。
        回傳受影響音符數。呼叫端負責 push_history / rebuild_display_cache / rebuild_mapper。
        """
        if factor <= 0.0 or abs(factor - 1.0) < 1e-12:
            return 0

        def _s(v) -> int:
            return int(round(float(v) * factor))

        changed = 0
        for n in self.notes_tree:
            n.start = max(0, _s(n.start))
            n.end = max(n.start + 1, _s(n.end))
            n.gate = n.end - n.start
            # trill / slide 子音符的絕對 ms 時間也要一起縮放
            for se in getattr(n, 'sub_elems', None) or []:
                for tag in ('start_timing_msec', 'end_timing_msec'):
                    cur = _child_int(se, tag)
                    if cur is not None:
                        _set_child_int(se, tag, max(0, _s(cur)))
            changed += 1

        # XML beat_data
        if self.root is not None:
            br = self.root.find('beat_data')
            if br is not None:
                for b in br.findall('beat'):
                    cur = _child_int(b, 'start_timing_msec')
                    if cur is not None:
                        _set_child_int(b, 'start_timing_msec', max(0, _s(cur)))

        # JSON beat_timings
        jm = self.json_meta or {}
        bt = jm.get('beat_timings')
        if isinstance(bt, (list, tuple)) and bt:
            jm['beat_timings'] = [max(0, _s(x)) for x in bt]

        # 音樂結束時間
        self.music_end_ms = float(self.music_end_ms) * factor

        # 拍號變化 (ms, numerator, denominator)
        if self.time_sig_changes:
            self.time_sig_changes = [
                (max(0, _s(ms)), num, den) for (ms, num, den) in self.time_sig_changes
            ]

        self.dirty = True
        return changed

    def rebuild_from_reference_midi(
        self,
        midi_notes: List[Dict[str, Any]],
        window_ms: int = 2000,
        long_threshold_ms: int = 180,
        swap_hands: bool = False,
    ) -> int:
        """用參考 MIDI 的音符重建整張譜：pitch/時間照 MIDI，左右 lane 依原譜的
        「時間局部 pitch→lane 分布」推得（隨時間變化）。回傳新音符數。

        midi_notes: [{'start_timing_msec', 'end_timing_msec', 'scale_piano'|'pitch',
                      'hand'(選填)}, ...]
        呼叫端負責 push_history / rebuild_display_cache / rebuild_mapper。
        """
        # 1) 在清除前，從目前音符學樣本 (time, pitch, lane_center, width)
        samples: List[tuple] = []
        for nn in self.notes_tree:
            p = nn.pitch
            if p is None:
                continue
            lc = (int(nn.min_key) + int(nn.max_key)) / 2.0
            w = abs(int(nn.max_key) - int(nn.min_key)) + 1
            samples.append((int(nn.start), int(p), lc, int(w)))
        samples.sort(key=lambda s: s[0])
        stimes = [s[0] for s in samples]
        default_width = (
            sorted(s[3] for s in samples)[len(samples) // 2] if samples else 1
        )

        # 2) 左右手：從 MIDI 的音軌結構穩健判斷（忽略無音符的 tempo 軌），
        #    無法分軌時退回音高分手（中央 C 以下→左手）。
        track_hands = _assign_track_hands(midi_notes, swap_hands=swap_hands)

        # 3) 逐 MIDI 音符重建
        ordered = sorted(
            midi_notes, key=lambda x: int(x.get('start_timing_msec', 0))
        )
        new_notes: List[GNote] = []
        for idx, m in enumerate(ordered):
            start = int(m.get('start_timing_msec', 0))
            end = max(start + 1, int(m.get('end_timing_msec', start + 1)))
            pitch = int(m.get('scale_piano', m.get('pitch', 60)))
            if track_hands is not None:
                hand = track_hands.get(int(m.get('track', 0)), 0)
            else:
                # 單軌 MIDI：用音高分手（中央 C = MIDI 60 以下算左手）
                hand = 0 if pitch >= 60 else 1

            # 時間局部樣本（±window_ms）；太少則擴大到時間最近的 12 個
            local: List[tuple]
            if samples:
                lo = bisect_left(stimes, start - window_ms)
                hi = bisect_right(stimes, start + window_ms)
                window = samples[lo:hi]
                if len(window) < 4:
                    window = _k_nearest_samples_by_time(samples, stimes, start, 12)
                local = [(s[1], s[2], s[3]) for s in window]
            else:
                local = []

            lane_c, width = _fit_local_lane(
                local, pitch, default_width, TOTAL_GAME_KEYS
            )
            half = (width - 1) / 2.0
            mn = int(round(lane_c - half))
            mx = mn + int(width) - 1
            mn = max(0, min(TOTAL_GAME_KEYS - 1, mn))
            mx = max(mn, min(TOTAL_GAME_KEYS - 1, mx))

            g = GNote(None, idx)
            g.start = start
            g.end = end
            g.gate = end - start
            g.min_key = mn
            g.max_key = mx
            g.pitch = pitch
            g.hand = hand
            g.note_type = 2 if (end - start) >= long_threshold_ms else 0
            new_notes.append(g)

        self.notes_tree = new_notes
        self.dirty = True
        return len(new_notes)

    def set_beat_grid_ms(self, beat_ms_list: List[float]) -> int:
        """以一組 beat 起始 ms 覆蓋 beat_data（XML）與 json beat_timings。
        供「採用參考 MIDI 的節拍線」使用。回傳 beat 數。"""
        beats = sorted(int(round(float(x))) for x in beat_ms_list if x is not None)
        # 去重
        dedup: List[int] = []
        for b in beats:
            if not dedup or b != dedup[-1]:
                dedup.append(b)
        if not dedup:
            return 0

        # JSON 端
        jm = self.json_meta if isinstance(self.json_meta, dict) else {}
        jm['beat_timings'] = list(dedup)
        jm['beat_indices'] = list(range(len(dedup)))
        self.json_meta = jm

        # XML 端 beat_data：<beat><index/><start_timing_msec/></beat>
        if self.root is not None:
            old = self.root.find('beat_data')
            if old is not None:
                self.root.remove(old)
            beat_root = ET.SubElement(self.root, 'beat_data')
            for i, ms in enumerate(dedup):
                be = ET.SubElement(beat_root, 'beat')
                _set_child_int(be, 'index', i)
                _set_child_int(be, 'start_timing_msec', ms)

        self.dirty = True
        return len(dedup)

    def conform_to_midi_tempo(
        self, midi_index_ms_pairs: List[Tuple[int, int]]
    ) -> int:
        """把整張譜的節拍對齊到參考 MIDI：以 beat『index(音樂座標)』為橋樑，
        逐段線性時間扭曲(time-warp)每個音符，並用 MIDI 的 beat 取代 beat_data。

        原理：
          音符時間 t → (用目前 beat_data 內插) 音樂 index p → (用 MIDI 內插) 新時間。
        因此每個小節/拍內的音符依比例縮放到 MIDI 對應的節奏（支援變速）。

        midi_index_ms_pairs: [(index, ms), ...]，index 必須與本譜 beat_index 同單位
        （八分音符位置 ×1000）。回傳受影響音符數。
        呼叫端負責 push_history / rebuild_display_cache / rebuild_mapper。
        """
        old = sorted(self.get_beat_entries(), key=lambda x: x[0])  # (index, ms)
        new = sorted((int(i), int(m)) for i, m in midi_index_ms_pairs)
        if len(old) < 2 or len(new) < 2:
            return 0
        old_idx = [int(i) for i, _ in old]
        old_ms = [int(m) for _, m in old]
        new_idx = [i for i, _ in new]
        new_ms = [m for _, m in new]

        def _interp(xs: List[int], ys: List[int], x: float) -> float:
            j = bisect_right(xs, x) - 1
            if j < 0:
                j = 0
            if j >= len(xs) - 1:
                j = len(xs) - 2
            x0, x1 = xs[j], xs[j + 1]
            y0, y1 = ys[j], ys[j + 1]
            if x1 == x0:
                return float(y0)
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)

        def warp(t: float) -> float:
            p = _interp(old_ms, old_idx, t)     # 時間 → 音樂 index
            return _interp(new_idx, new_ms, p)  # 音樂 index → 新時間

        for note in self.notes_tree:
            s = max(0, int(round(warp(int(note.start)))))
            e = int(round(warp(int(note.end))))
            note.start = s
            note.end = max(s + 1, e)
            note.gate = note.end - s
            for se in getattr(note, 'sub_elems', None) or []:
                for tag in ('start_timing_msec', 'end_timing_msec'):
                    c = _child_int(se, tag)
                    if c is not None:
                        _set_child_int(se, tag, max(0, int(round(warp(c)))))

        if self.music_end_ms:
            self.music_end_ms = float(warp(self.music_end_ms))

        # 拍號變化的時間也要一起 warp（否則 1 分半後拍號位置全錯）
        if self.time_sig_changes:
            self.time_sig_changes = [
                (max(0, int(round(warp(ms)))), num, den)
                for (ms, num, den) in self.time_sig_changes
            ]
        jm0 = self.json_meta if isinstance(self.json_meta, dict) else {}
        tsc = jm0.get('time_signature_changes')
        if isinstance(tsc, list):
            for ev in tsc:
                if isinstance(ev, dict) and 'time_ms' in ev:
                    try:
                        ev['time_ms'] = max(0, int(round(warp(int(ev['time_ms'])))))
                    except (TypeError, ValueError):
                        pass

        # 重新對時 beat_data：保留原本每個 entry 的 index 與數量（維持 bar/格式結構），
        # 只把每個 entry 的時間改成 MIDI 對應音樂 index 的新時間。
        # 不可用 set_beat_grid_ms 換成 MIDI 八分音符——那會改變 entry 數量與 index 縮放，
        # 破壞 entries_per_bar/bar 判定，導致 bpm 算錯。
        retimed = []  # (index, new_ms)
        prev = -1
        for idx, _old in old:
            t = int(round(_interp(new_idx, new_ms, idx)))
            if t <= prev:
                t = prev + 1   # 保單調遞增
            retimed.append((int(idx), t))
            prev = t
        self._write_beat_data_pairs(retimed)
        self.dirty = True
        return len(self.notes_tree)

    def find_missing_beat_entries(self, tolerance: float = 0.35
                                  ) -> List[Tuple[int, int]]:
        """找出 beat_data 裡「間距是別人整數倍」的缺口，回傳要補的 (前一格序, ms)。

        `anima-xi-fullarr-phyxinon.json` 就是這種：標準間距 1304ms，但有 16 段
        是 2609（剛好兩倍）。索引卻是連號的 0..185，所以沒有任何地方標示那裡
        少了一筆——編輯器只看到「這一格的時間是別人的兩倍」，於是那幾小節的
        BPM 直接減半、時間均分模式下那一段的畫面也跟著不對。

        判斷方式是拿間距和**全曲中位數**比，四捨五入取倍數 k；k >= 2 而且
        `gap / k` 和中位數差在 `tolerance` 以內才補（真的變速時 gap/k 會落在
        附近，整段漏拍才會剛好是整數倍）。
        """
        beats = self.get_beat_entries()
        if len(beats) < 3:
            return []
        gaps = sorted(int(beats[i + 1][1]) - int(beats[i][1])
                      for i in range(len(beats) - 1)
                      if int(beats[i + 1][1]) > int(beats[i][1]))
        if not gaps:
            return []
        unit = float(gaps[len(gaps) // 2])
        if unit <= 0:
            return []
        out: List[Tuple[int, int]] = []
        for i in range(len(beats) - 1):
            a_ms, b_ms = int(beats[i][1]), int(beats[i + 1][1])
            gap = b_ms - a_ms
            if gap <= 0:
                continue
            k = int(round(gap / unit))
            if k < 2:
                continue
            step = gap / float(k)
            if abs(step - unit) > tolerance * unit:
                continue          # 真的是一段長間隔，不是漏拍
            for j in range(1, k):
                out.append((i, int(round(a_ms + step * j))))
        return out

    def repair_missing_beat_entries(self, tolerance: float = 0.35) -> int:
        """把 `find_missing_beat_entries` 找到的拍點補進去，回傳補了幾筆。

        補完之後索引重新連號（保留原本的 index 縮放），BPM 與小節數才會回到
        正確的值。
        """
        missing = self.find_missing_beat_entries(tolerance)
        if not missing:
            return 0
        beats = self.get_beat_entries()
        scale = self._detect_beat_index_scale() or 1.0
        times = sorted({int(ms) for _idx, ms in beats}
                       | {int(ms) for _i, ms in missing})
        pairs = [(int(round(n * scale)), ms) for n, ms in enumerate(times)]
        self._write_beat_data_pairs(pairs)
        self._epb_mode = None
        self._epb_count = None
        jm = self.json_meta if isinstance(self.json_meta, dict) else {}
        jm.pop('editor_entries_per_bar', None)
        jm.pop('editor_beat_entry_mode', None)
        self.json_meta = jm
        self.dirty = True
        logging.info('repaired %d missing beat entries', len(missing))
        return len(missing)

    def _write_beat_data_pairs(self, pairs: List[Tuple[int, int]]) -> None:
        """以 (index, ms) 寫入 beat_data，**保留原本的 index**（不重新編號），
        以維持 per-beat/per-bar 格式與 index 縮放（entries_per_bar 判定所需）。"""
        pairs = sorted(pairs, key=lambda x: int(x[0]))
        if not pairs:
            return
        idxs = [int(i) for i, _ in pairs]
        mss = [int(m) for _, m in pairs]
        # JSON 端
        jm = self.json_meta if isinstance(self.json_meta, dict) else {}
        jm['beat_timings'] = list(mss)
        jm['beat_indices'] = list(idxs)
        self.json_meta = jm
        # XML 端 beat_data
        if self.root is not None:
            old = self.root.find('beat_data')
            if old is not None:
                self.root.remove(old)
            beat_root = ET.SubElement(self.root, 'beat_data')
            for i, ms in pairs:
                be = ET.SubElement(beat_root, 'beat')
                _set_child_int(be, 'index', int(i))
                _set_child_int(be, 'start_timing_msec', int(ms))

    @staticmethod
    def _guess_lane_index_base(notes: List[GNote]) -> int:
        """Infer lane base from the observed serialized lane range only."""
        if not notes:
            return LEGACY_LANE_BASE
        min_lane = min(min(int(n.min_key), int(n.max_key)) for n in notes)
        max_lane = max(max(int(n.min_key), int(n.max_key)) for n in notes)
        if min_lane >= EXTERNAL_LANE_BASE and max_lane <= TOTAL_GAME_KEYS:
            return EXTERNAL_LANE_BASE
        return LEGACY_LANE_BASE

    @staticmethod
    def _normalize_notes_to_internal(notes: List[GNote], lane_index_base: int) -> None:
        delta = int(lane_index_base) - INTERNAL_LANE_BASE
        for n in notes:
            mn = int(n.min_key) - delta
            mx = int(n.max_key) - delta
            if mx < mn:
                mn, mx = mx, mn
            mn = max(0, min(TOTAL_GAME_KEYS - 1, mn))
            mx = max(mn, min(TOTAL_GAME_KEYS - 1, mx))
            n.min_key = mn
            n.max_key = mx

    def _get_xml_lane_index_base(self) -> int:
        return int(self.xml_lane_index_base)

    def _get_json_lane_index_base(self) -> int:
        raw = self.json_meta.get('lane_index_base')
        if raw not in (None, ''):
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        return self._guess_lane_index_base(self.notes_tree)

    # ------------------------------------------------------------------
    # 載入 XML
    # ------------------------------------------------------------------

    def load_xml(self, path: str) -> None:
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()
        self.file_format = 'xml'
        self.json_meta = {}
        self.midi_data = None
        self.current_file = path
        self._epb_mode = None   # 換譜面 → 重新判斷 beat_data 格式
        self._epb_count = None

        self._parse_xml_header()

        nd = self.root.find('note_data')
        if nd is None:
            raise ValueError('找不到 <note_data> 節點')

        self.notes_tree = [GNote(ne, i) for i, ne in enumerate(nd.findall('note'))]
        self._read_pedal_data_from_xml()
        self._read_dynamics_data_from_xml()
        self._split_sub_notes_into_hidden()
        self._load_velocity_from_subs()
        lane_base = self._guess_lane_index_base(self.notes_tree)
        self.xml_lane_index_base = lane_base
        self._normalize_notes_to_internal(self.notes_tree, lane_base)
        self.rebuild_display_cache()
        self.undo_stack.clear()
        self.dirty = False

    def _parse_xml_header(self) -> None:
        """從 XML header / beat_data 讀取 BPM 等基本參數。"""
        assert self.root is not None
        # BPM
        for path in ('header/first_bpm', 'header/bpm', 'first_bpm', 'bpm'):
            el = self.root.find(path)
            if el is not None and el.text:
                try:
                    raw = float(el.text)
                    # 原始遊戲格式以 BPM×100000 儲存（如 18000000 = 180 BPM）
                    self.bpm = raw / 100000.0 if raw > 10000 else raw
                    break
                except ValueError:
                    pass

        # beats_per_bar
        for path in ('header/time_signature_numerator', 'time_signature_numerator'):
            el = self.root.find(path)
            if el is not None and el.text:
                try:
                    self.beats_per_bar = int(el.text)
                    break
                except ValueError:
                    pass

        # time_sig_denominator
        for path in ('header/time_signature_denominator', 'time_signature_denominator'):
            el = self.root.find(path)
            if el is not None and el.text:
                try:
                    self.time_sig_denominator = int(el.text)
                    break
                except ValueError:
                    pass

        # beat_offset / music_end
        for path in ('header/beat_offset_ms', 'beat_offset_ms'):
            el = self.root.find(path)
            if el is not None and el.text:
                try:
                    self.beat_offset_ms = float(el.text)
                    break
                except ValueError:
                    pass

        for path in ('header/music_finish_time_msec', 'music_finish_time_msec'):
            el = self.root.find(path)
            if el is not None and el.text:
                try:
                    self.music_end_ms = float(el.text)
                    break
                except ValueError:
                    pass

        # time_signature_changes
        self.time_sig_changes = []
        ts_root = self.root.find('time_signature_changes')
        if ts_root is not None:
            for ch in ts_root.findall('ts_change'):
                ms_el  = ch.find('start_timing_msec')
                num_el = ch.find('numerator')
                den_el = ch.find('denominator')
                if ms_el is None or num_el is None or den_el is None:
                    continue
                try:
                    self.time_sig_changes.append(
                        (int(float(ms_el.text)), int(float(num_el.text)), int(float(den_el.text)))
                    )
                except (ValueError, TypeError):
                    pass
        self.time_sig_changes.sort(key=lambda x: x[0])

    # ------------------------------------------------------------------
    # 載入 JSON
    # ------------------------------------------------------------------

    def load_json(self, path: str) -> None:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        self.file_format = 'json'
        self.root = None
        self.tree = None
        self.midi_data = None
        self.current_file = path
        self.json_meta = {k: v for k, v in data.items() if k != 'notes'} if isinstance(data, dict) else {}
        self._epb_mode = None   # 換譜面 → 重新判斷 beat_data 格式
        self._epb_count = None

        self.dynamics = self._dynamics_from_meta(self.json_meta)

        self.pedal_spans = []
        raw_pedal = self.json_meta.get('pedal_data')
        if isinstance(raw_pedal, list):
            spans = []
            for item in raw_pedal:
                try:
                    if isinstance(item, dict):
                        spans.append([float(item['start_ms']), float(item['end_ms'])])
                    else:
                        spans.append([float(item[0]), float(item[1])])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
            self.pedal_spans = self._normalise_pedal_spans(spans)

        # BPM
        for key in ('first_bpm', 'bpm'):
            v = self.json_meta.get(key)
            if v not in (None, ''):
                try:
                    self.bpm = float(v)
                    break
                except (ValueError, TypeError):
                    pass

        # time_signature
        if 'time_signature_numerator' in self.json_meta:
            try:
                self.beats_per_bar = int(self.json_meta['time_signature_numerator'])
            except (ValueError, TypeError):
                pass
        if 'time_signature_denominator' in self.json_meta:
            try:
                self.time_sig_denominator = int(self.json_meta['time_signature_denominator'])
            except (ValueError, TypeError):
                pass

        # offset / end
        for key in ('music_offset_msec', 'music_offset_ms', 'offset_ms'):
            v = self.json_meta.get(key)
            if v not in (None, ''):
                try:
                    self.beat_offset_ms = float(v)
                    break
                except (ValueError, TypeError):
                    pass

        for key in ('music_finish_time_msec', 'music_finish_time_ms'):
            v = self.json_meta.get(key)
            if v not in (None, ''):
                try:
                    self.music_end_ms = float(v)
                    break
                except (ValueError, TypeError):
                    pass

        # time_signature_changes
        self.time_sig_changes = []
        raw_ts = self.json_meta.get('time_signature_changes', [])
        for item in raw_ts:
            try:
                self.time_sig_changes.append(
                    (int(item['time_ms']), int(item['numerator']), int(item['denominator']))
                )
            except (KeyError, TypeError, ValueError):
                pass
        self.time_sig_changes.sort(key=lambda x: x[0])

        notes_list = data.get('notes', []) if isinstance(data, dict) else []
        # 照檔案自己宣告的編號解讀音高；舊檔沒宣告就交給值域判斷
        _enc = self.json_meta.get('pitch_encoding')
        self.notes_tree = [GNote.from_dict(d, i, _enc)
                           for i, d in enumerate(notes_list)]
        # 把 JSON 記下來的「寄主是誰」接回去。沒有這一步的話，官方那種
        # 「sub_note 自帶時間、和寄主差超過 120ms」的隱藏音符在重新存檔時
        # 會找不到寄主而被取消隱藏。
        for note, item in zip(self.notes_tree, notes_list):
            if not isinstance(item, dict):
                continue
            target = item.get('hostIndex')
            if target is None:
                continue
            try:
                target = int(target)
            except (TypeError, ValueError):
                continue
            if 0 <= target < len(self.notes_tree) and target != note.idx:
                note._sub_host = self.notes_tree[target]
        lane_base = self._get_json_lane_index_base()
        self.xml_lane_index_base = lane_base
        self._normalize_notes_to_internal(self.notes_tree, lane_base)
        self.rebuild_display_cache()
        self.undo_stack.clear()
        self.dirty = False

    @staticmethod
    def _build_tempo_map_ticks(
        events_ticks: List[Tuple[int, int]],
        default_tempo: int,
    ) -> List[Tuple[int, int]]:
        merged: List[Tuple[int, int]] = []
        events = sorted(events_ticks, key=lambda x: x[0]) if events_ticks else []
        if not events or events[0][0] != 0:
            merged.append((0, int(default_tempo)))
        for tick, tempo in events:
            tick_i = int(tick)
            tempo_i = int(tempo)
            if merged and merged[-1][0] == tick_i:
                merged[-1] = (tick_i, tempo_i)
            else:
                merged.append((tick_i, tempo_i))
        if not merged:
            merged.append((0, int(default_tempo)))
        return merged

    # ------------------------------------------------------------------
    # 延音踏板 (CC64)
    # ------------------------------------------------------------------
    PEDAL_DOWN_THRESHOLD = 64      # MIDI 慣例：>=64 算踩下

    def _pedal_spans_from_cc(self, cc_events, tempo_map_ticks, ticks_per_beat) -> List[List[float]]:
        """把 CC64 的 on/off 事件摺成不重疊的踩踏區間。

        多軌檔可能每軌都寫一份踏板，事件因此會交錯。這裡不分軌，照時間排序
        後用「第一次踩下 → 第一次放開」配對，重複的踩下當作沒發生（真實鋼琴
        也是這樣：踏板已經在下面，再踩一次不會有第二段殘響）。
        """
        if not cc_events:
            return []
        spans: List[List[float]] = []
        open_start: Optional[float] = None
        for tick, value in sorted(cc_events, key=lambda e: e[0]):
            ms = self._ticks_to_ms_from_tempo_map(int(tick), tempo_map_ticks, int(ticks_per_beat))
            if int(value) >= self.PEDAL_DOWN_THRESHOLD:
                if open_start is None:
                    open_start = float(ms)
            elif open_start is not None:
                if ms - open_start >= 1.0:
                    spans.append([float(open_start), float(ms)])
                open_start = None
        if open_start is not None:
            # 檔案結束時踏板還踩著：收在最後一顆音符的尾巴
            tail = max((float(n.end) for n in self.notes_tree), default=open_start)
            if tail - open_start >= 1.0:
                spans.append([float(open_start), float(tail)])
        return self._normalise_pedal_spans(spans)

    @staticmethod
    def _normalise_pedal_spans(spans) -> List[List[float]]:
        """排序 + 合併**真正重疊**的區間，並保住每一次換踏。

        以前的條件是「間隔 ≤1ms 就合併」，那會把換踏整個吃掉。許多 MIDI 把換踏
        記成瞬間動作——同一個 tick 放開又踩下，間隔正好是 0——實測 felzione 的 97
        次踩放因此被併成 23 段，其中一段長達 39 秒。那已經不是延音，是整首的和聲
        糊在一起，遊戲端還得為此同時撐住上百個發聲。

        間隔 0 代表的是「這裡要換踏」，不是「一直踩著」，所以只在區間真的重疊時
        才合併。多小的抬起都不必特別撐開——遊戲端問的是「這段時間內有沒有發生抬
        起」，不是每幀取樣瞬間狀態，再短也看得到。
        """
        clean = []
        for span in spans:
            lo, hi = float(span[0]), float(span[1])
            if hi - lo >= 1.0:
                clean.append([lo, hi])
        clean.sort(key=lambda sp: sp[0])

        merged: List[List[float]] = []
        for lo, hi in clean:
            # 只有真的重疊（後一段在前一段結束前就開始）才是同一次踩踏被拆成兩筆。
            if merged and lo < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return merged

    def pedal_add_span(self, start_ms: float, end_ms: float) -> bool:
        lo, hi = sorted((float(start_ms), float(end_ms)))
        if hi - lo < 1.0:
            return False
        self.pedal_spans = self._normalise_pedal_spans(self.pedal_spans + [[lo, hi]])
        self.dirty = True
        return True

    def pedal_span_at(self, ms: float) -> Optional[List[float]]:
        for span in self.pedal_spans:
            if span[0] <= float(ms) <= span[1]:
                return span
        return None

    def pedal_remove_at(self, ms: float) -> bool:
        span = self.pedal_span_at(ms)
        if span is None:
            return False
        self.pedal_spans = [sp for sp in self.pedal_spans if sp is not span]
        self.dirty = True
        return True

    def pedal_clear(self) -> int:
        count = len(self.pedal_spans)
        if count:
            self.pedal_spans = []
            self.dirty = True
        return count

    # ------------------------------------------------------------------
    # 強弱記號（像樂譜的 p / f / cresc. / dim.）
    # ------------------------------------------------------------------

    #: 記號名稱 → 力度值。和樂譜上的 pp/p/mf/f 對應，也是右鍵選單的預設集。
    DYNAMIC_MARKS: List[Tuple[str, int]] = [
        ('ppp', 16), ('pp', 32), ('p', 48), ('mp', 64),
        ('mf', 80), ('f', 96), ('ff', 112), ('fff', 127),
    ]

    @staticmethod
    def dynamic_mark_name(level: float) -> str:
        """力度值 → 最接近的記號名稱，畫在曲線旁邊用。"""
        return min(NoteModel.DYNAMIC_MARKS,
                   key=lambda item: abs(item[1] - float(level)))[0]

    @staticmethod
    def _normalise_dynamics(marks) -> List[List[float]]:
        """排序 + 同一時間只留最後一個，讓記號串永遠是正規形式。"""
        by_ms: Dict[int, List[float]] = {}
        for mark in marks or ():
            try:
                ms = int(round(float(mark[0])))
                level = max(1.0, min(127.0, float(mark[1])))
                ramp = bool(mark[2]) if len(mark) > 2 else False
            except (TypeError, ValueError, IndexError):
                continue
            by_ms[max(0, ms)] = [float(max(0, ms)), level, ramp]
        return [by_ms[k] for k in sorted(by_ms)]

    @classmethod
    def _dynamics_from_meta(cls, meta) -> Dict[int, List[List[float]]]:
        """從 json_meta['dynamics_data'] 讀回強弱記號。格式壞掉就當作沒有。"""
        raw = (meta or {}).get('dynamics_data')
        if not isinstance(raw, dict):
            return {}
        out: Dict[int, List[List[float]]] = {}
        for key, marks in raw.items():
            try:
                hand = int(key)
            except (TypeError, ValueError):
                continue
            parsed = []
            for m in marks or ():
                try:
                    if isinstance(m, dict):
                        parsed.append([float(m['ms']), float(m['level']),
                                       bool(m.get('ramp', False))])
                    else:
                        parsed.append([float(m[0]), float(m[1]),
                                       bool(m[2]) if len(m) > 2 else False])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
            if parsed:
                out[hand] = cls._normalise_dynamics(parsed)
        return out

    def dynamics_marks(self, hand: int) -> List[List[float]]:
        """某一手的記號串 [[ms, level, ramp], ...]（已排序）。"""
        return self.dynamics.get(int(hand), [])

    def dynamics_set(self, hand: int, marks) -> None:
        self.dynamics[int(hand)] = self._normalise_dynamics(marks)
        self.dirty = True

    def dynamics_add(self, hand: int, ms: float, level: float,
                     ramp: bool = False) -> None:
        """新增/覆蓋一個記號。`ramp=True` 表示從這裡漸變到下一個記號。"""
        marks = list(self.dynamics_marks(hand))
        marks.append([float(ms), float(level), bool(ramp)])
        self.dynamics_set(hand, marks)

    def dynamics_remove_near(self, hand: int, ms: float,
                             tolerance_ms: float = 120.0) -> bool:
        marks = self.dynamics_marks(hand)
        if not marks:
            return False
        nearest = min(marks, key=lambda m: abs(m[0] - float(ms)))
        if abs(nearest[0] - float(ms)) > float(tolerance_ms):
            return False
        self.dynamics_set(hand, [m for m in marks if m is not nearest])
        return True

    def dynamics_clear(self, hand: Optional[int] = None) -> int:
        hands = [int(hand)] if hand is not None else list(self.dynamics.keys())
        removed = 0
        for h in hands:
            removed += len(self.dynamics.get(h, []))
            self.dynamics[h] = []
        if removed:
            self.dirty = True
        return removed

    def dynamics_level_at(self, hand: int, ms: float) -> Optional[float]:
        """某一手在 `ms` 的強弱值；沒有任何記號時回傳 None。

        兩個記號之間怎麼走由**前一個記號**的 `ramp` 決定：
          ramp=False → 維持前一個的值到下一個記號（樂譜上的 p、f 那種）
          ramp=True  → 線性漸變到下一個記號的值（cresc. / dim. 的漸層）
        第一個記號之前一律用第一個記號的值，最後一個之後用最後一個的值。
        """
        marks = self.dynamics_marks(hand)
        if not marks:
            return None
        ms = float(ms)
        if ms <= marks[0][0]:
            return float(marks[0][1])
        for cur, nxt in zip(marks, marks[1:]):
            if ms >= nxt[0]:
                continue
            if not cur[2]:
                return float(cur[1])
            span = max(1e-6, nxt[0] - cur[0])
            t = (ms - cur[0]) / span
            return float(cur[1]) + (float(nxt[1]) - float(cur[1])) * t
        return float(marks[-1][1])

    def dynamics_range(self, hand: int,
                       notes: Optional[Iterable['GNote']] = None) -> Tuple[float, float]:
        """某一手實際用到的力度範圍 (最小, 最大)，當作強弱欄的刻度。

        用固定的 1~127 當刻度的話，實際只用到 70~96 的譜整條曲線會擠成一條，
        看不出任何起伏。改成貼著這一手真正的範圍，畫面才有解析度。

        全部一樣大聲（min==max）或沒有力度資料時回傳一個有寬度的區間，否則
        刻度會退化成除以零。
        """
        pool = self.notes_tree if notes is None else notes
        vals = [float(n.velocity) for n in pool
                if int(n.hand) == int(hand) and n.velocity is not None]
        if not vals:
            return (1.0, 127.0)
        lo, hi = min(vals), max(vals)
        if hi - lo < 8.0:                 # 太窄就往兩邊撐開，拖曳才有空間
            mid = (lo + hi) / 2.0
            lo, hi = mid - 4.0, mid + 4.0
        return (max(1.0, lo), min(127.0, max(hi, lo + 8.0)))

    def dynamics_contour_from_notes(
        self, hand: int, resolution_ms: float = 0.0,
        notes: Optional[Iterable['GNote']] = None,
    ) -> List[List[float]]:
        """把這一手音符**現在的力度**變成一串記號，也就是「目前的強弱長什麼樣」。

        `resolution_ms <= 0` → 每個起音一個記號（最忠實）。給正數就按那個長度
        分桶取平均，記號數會少很多、線條也順——密集譜每顆一個記號會多到看不懂。

        全部標成 ramp（漸變），畫出來才是一條連續的力度輪廓而不是階梯。
        """
        pool = self.notes_tree if notes is None else notes
        picked = [n for n in pool
                  if int(n.hand) == int(hand) and n.velocity is not None]
        if not picked:
            return []
        buckets: Dict[int, List[float]] = {}
        for note in picked:
            start = float(note.start)
            key = (int(start // resolution_ms) if resolution_ms > 0
                   else int(round(start)))
            buckets.setdefault(key, []).append(float(note.velocity))
        marks: List[List[float]] = []
        for key in sorted(buckets):
            vals = buckets[key]
            ms = float(key) * resolution_ms if resolution_ms > 0 else float(key)
            marks.append([ms, sum(vals) / len(vals), True])
        if marks:
            marks[-1][2] = False          # 最後一個之後沒得漸變
        return marks

    def dynamics_seed_from_notes(self, hand: int, resolution_ms: float = 0.0) -> int:
        """用目前的音符力度產生可編輯的強弱記號，回傳記號數。"""
        marks = self.dynamics_contour_from_notes(hand, resolution_ms)
        self.dynamics_set(hand, marks)
        return len(marks)

    def dynamics_baseline(self, hand: int, notes: Optional[Iterable['GNote']] = None) -> float:
        """某一手音符的平均力度，當作「曲線是倍率」的基準。

        曲線寫 f（96）時，這一手的平均會被推到 96，而每顆音符相對平均的高低
        比例保持不變——原本的演奏起伏才不會被壓平成一條直線。
        """
        pool = self.notes_tree if notes is None else notes
        vals = [float(n.velocity) for n in pool
                if int(n.hand) == int(hand) and n.velocity is not None]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    def apply_dynamics(self, hands: Optional[Iterable[int]] = None,
                       notes: Optional[Iterable['GNote']] = None) -> int:
        """把強弱曲線套進音符 velocity，回傳被改動的音符數。

        倍率的分母是**這個起音當下的原始力度**（同時起音的音符取平均），不是
        整首的平均：

            新力度 = 原力度 × 曲線值 / 該起音的原始力度

        這樣「用音符力度產生曲線之後直接套用」會是一個 no-op —— 曲線本來就
        長得跟現況一樣，什麼都不該改。用整首平均當分母的話，只是把曲線產生
        出來再套回去就會把整首重新縮放一遍，完全不合直覺。

        和弦裡的每顆音一起等比縮放，所以內聲部之間的強弱差保留；把曲線某一
        段往外拉，就是把那一段整體推上去。沒有 velocity 的音符直接吃曲線值。
        """
        pool = list(self.notes_tree if notes is None else notes)
        target_hands = ([int(h) for h in hands] if hands is not None
                        else sorted(self.dynamics.keys()))
        changed = 0
        for hand in target_hands:
            if not self.dynamics_marks(hand):
                continue
            mine = [n for n in pool if int(n.hand) == hand]
            # 每個起音的原始力度（同時起音取平均）＝ 曲線被拉之前的參考值
            reference: Dict[int, List[float]] = {}
            for note in mine:
                if note.velocity is not None:
                    reference.setdefault(int(note.start), []).append(float(note.velocity))
            ref_at = {k: sum(v) / len(v) for k, v in reference.items()}
            for note in mine:
                level = self.dynamics_level_at(hand, float(note.start))
                if level is None:
                    continue
                if note.velocity is None:
                    new = int(round(level))
                else:
                    base_ref = ref_at.get(int(note.start), 0.0)
                    if base_ref <= 0:
                        continue
                    new = int(round(float(note.velocity) * (level / base_ref)))
                new = max(1, min(127, new))
                if note.velocity != new:
                    note.velocity = new
                    changed += 1
        if changed:
            self.dirty = True
        return changed

    def pedal_release_after(self, ms: float) -> Optional[float]:
        """`ms` 當下若踩著踏板，回傳放開的時刻；沒踩著回傳 None。"""
        span = self.pedal_span_at(ms)
        return float(span[1]) if span is not None else None

    def pedal_sustained_end(self, note: 'GNote') -> float:
        """音符在踏板作用下真正的收音時間。

        判斷點取音符**自己的 note-off**，不是 note-on——踏板是在放開琴鍵的
        那一刻決定要不要接手延音的。
        """
        end = float(note.end)
        release = self.pedal_release_after(end)
        return max(end, release) if release is not None else end

    def pedal_from_note_lengths(self, min_gap_ms: float = 60.0) -> int:
        """從現有音符的長度反推踏板：連續重疊的音符群 = 一段踩踏。

        沒有原始 CC64 的譜面（例如從 XML 讀進來的）想加踏板時的起手式。
        """
        events = sorted(((float(n.start), float(n.end)) for n in self.notes_tree),
                        key=lambda e: e[0])
        spans: List[List[float]] = []
        for start, end in events:
            if spans and start <= spans[-1][1] + float(min_gap_ms):
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
        spans = [sp for sp in spans if sp[1] - sp[0] >= 120.0]
        self.pedal_spans = self._normalise_pedal_spans(spans)
        self.dirty = True
        return len(self.pedal_spans)


    @staticmethod
    def _ticks_to_ms_from_tempo_map(
        tick: int,
        tempo_map: List[Tuple[int, int]],
        ticks_per_beat: int,
    ) -> float:
        if tick <= 0:
            return 0.0
        if not tempo_map:
            tempo_map = [(0, 500_000)]
        total_us = 0.0
        remaining_tick = int(tick)
        current_tick = 0
        current_tempo = int(tempo_map[0][1])
        for idx in range(1, len(tempo_map)):
            next_tick, next_tempo = tempo_map[idx]
            next_tick = int(next_tick)
            if remaining_tick <= next_tick:
                break
            delta_tick = max(0, next_tick - current_tick)
            total_us += delta_tick * current_tempo / max(1, ticks_per_beat)
            current_tick = next_tick
            current_tempo = int(next_tempo)
        delta_tick = max(0, remaining_tick - current_tick)
        total_us += delta_tick * current_tempo / max(1, ticks_per_beat)
        return total_us / 1000.0

    @staticmethod
    def _ms_to_ticks_from_tempo_map(
        ms: float,
        tempo_map: List[Tuple[int, int]],
        ticks_per_beat: int,
    ) -> int:
        if ms <= 0:
            return 0
        if not tempo_map:
            tempo_map = [(0, 500_000)]
        remaining_us = float(ms) * 1000.0
        current_tick = int(tempo_map[0][0])
        current_tempo = int(tempo_map[0][1])
        for idx in range(1, len(tempo_map)):
            next_tick, next_tempo = tempo_map[idx]
            next_tick = int(next_tick)
            segment_tick = max(0, next_tick - current_tick)
            segment_us = segment_tick * current_tempo / max(1, ticks_per_beat)
            if remaining_us <= segment_us:
                return int(round(current_tick + remaining_us * max(1, ticks_per_beat) / current_tempo))
            remaining_us -= segment_us
            current_tick = next_tick
            current_tempo = int(next_tempo)
        return int(round(current_tick + remaining_us * max(1, ticks_per_beat) / current_tempo))

    @staticmethod
    def _pick_common_int(values: List[int], default: int) -> int:
        counts: Dict[int, int] = {}
        for value in values:
            counts[int(value)] = counts.get(int(value), 0) + 1
        if not counts:
            return int(default)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    @staticmethod
    def _clamp_midi_byte(value: Optional[int], default: int = 0, minimum: int = 0) -> int:
        if value is None:
            return int(default)
        return max(int(minimum), min(127, int(value)))

    def is_midi_mode(self) -> bool:
        return self.file_format == 'midi'

    def _default_midi_track_for_hand(self, hand: int) -> int:
        values = [int(n.track) for n in self.notes_tree if n.track is not None and int(n.hand) == int(hand)]
        if values:
            return self._pick_common_int(values, 0)
        values = [int(n.track) for n in self.notes_tree if n.track is not None]
        if values:
            return self._pick_common_int(values, 0)
        return 0

    def midi_track_for_hand(self, hand: int, moving: Sequence['GNote'] = ()) -> int:
        """把一批音符改成某一手時，它們該落在哪個 MIDI 音軌。

        MIDI 模式下「哪一手」在資料上其實是音軌說了算：smart_chart 的
        `_assign_hands` 有兩個以上音軌時，一律照音軌重新分手。只改 `hand`
        不動 `track`，自動排譜就會把使用者的指定整批改回去。

        取樣時要把「這次要搬的音符」排掉，否則整軌換手時算出來的會是它們
        自己原本的音軌，等於沒搬。真的沒有可用的軌（例如單軌 MIDI，兩手擠
        在同一軌）就開一條新的——不然兩手在音軌上分不開。
        """
        hand = 1 if int(hand) == 1 else 0
        skip = {id(n) for n in moving}
        rest = [n for n in self.notes_tree
                if id(n) not in skip and n.track is not None]
        same = [int(n.track) for n in rest if int(n.hand) == hand]
        if same:
            return self._pick_common_int(same, 0)
        used = {int(n.track) for n in self.notes_tree if n.track is not None}
        if not used:
            return hand
        # 搬完之後沒人用的舊軌可以直接接手，不必一直往上長
        free = sorted(used - {int(n.track) for n in rest})
        if free:
            return free[0]
        return max(used) + 1

    def _default_midi_channel_for_track(self, track: Optional[int] = None) -> int:
        values = [
            int(n.channel) for n in self.notes_tree
            if n.channel is not None and (track is None or int(n.track or 0) == int(track))
        ]
        return max(0, min(15, self._pick_common_int(values, 0)))

    def _default_midi_velocity_for_track(self, track: Optional[int] = None) -> int:
        values = [
            int(n.velocity) for n in self.notes_tree
            if n.velocity is not None and (track is None or int(n.track or 0) == int(track))
        ]
        if not values:
            return 100
        return self._clamp_midi_byte(int(round(sum(values) / len(values))), 100, minimum=1)

    #: 譜面完全沒有力度資料時，新音符用的預設值
    DEFAULT_NEW_NOTE_VELOCITY = 96

    def velocity_near(self, start_ms: float, hand: Optional[int] = None,
                      window_ms: float = 1200.0) -> int:
        """新放的音符該用多大力度：抄附近既有音符的，抄不到才給預設。

        找法由近而遠：

        1. `start_ms` 前後 `window_ms` 內**同一隻手**的音符——取時間最近的那個
           起音（同時起音就取平均）。新音要和旁邊聽起來一致，抄鄰居最準。
        2. 放寬到同一時間窗內的任何一隻手。
        3. 這一手全曲的平均。
        4. 全曲平均。
        5. `DEFAULT_NEW_NOTE_VELOCITY`。

        MIDI 匯入的譜每顆都有力度，所以第 1 步幾乎都會中；純遊戲譜沒有力度資料
        就會一路掉到預設值。
        """
        start_ms = float(start_ms)
        pool = [n for n in self.notes_tree if n.velocity is not None]
        if not pool:
            return int(self.DEFAULT_NEW_NOTE_VELOCITY)

        def nearest_onset(notes):
            if not notes:
                return None
            best = min(notes, key=lambda n: abs(float(n.start) - start_ms))
            onset = int(best.start)
            same = [float(n.velocity) for n in notes if int(n.start) == onset]
            return int(round(sum(same) / len(same)))

        in_window = [n for n in pool
                     if abs(float(n.start) - start_ms) <= float(window_ms)]
        if hand is not None:
            same_hand = [n for n in in_window if int(n.hand) == int(hand)]
            got = nearest_onset(same_hand)
            if got is not None:
                return max(1, min(127, got))
        got = nearest_onset(in_window)
        if got is not None:
            return max(1, min(127, got))

        if hand is not None:
            hand_pool = [float(n.velocity) for n in pool if int(n.hand) == int(hand)]
            if hand_pool:
                return max(1, min(127, int(round(sum(hand_pool) / len(hand_pool)))))
        return max(1, min(127, int(round(
            sum(float(n.velocity) for n in pool) / len(pool)))))

    def _default_midi_off_velocity_for_track(self, track: Optional[int] = None) -> int:
        values = [
            int(n.off_velocity) for n in self.notes_tree
            if n.off_velocity is not None and (track is None or int(n.track or 0) == int(track))
        ]
        if not values:
            return 0
        return self._clamp_midi_byte(int(round(sum(values) / len(values))), 0)

    def _build_midi_precise_beat_entries(
        self,
        song_end_tick: int,
        ticks_per_beat: int,
        tempo_map_ticks: List[Tuple[int, int]],
        time_signature_events_ticks: List[Tuple[int, int, int]],
    ) -> List[Tuple[int, int]]:
        if ticks_per_beat <= 0:
            return []

        ts_events = sorted(time_signature_events_ticks, key=lambda item: item[0]) if time_signature_events_ticks else []
        if not ts_events or int(ts_events[0][0]) != 0:
            ts_events.insert(0, (0, int(self.beats_per_bar), int(self.time_sig_denominator)))

        segments: List[Tuple[int, int, int, int]] = []
        for idx, (tick, num, den) in enumerate(ts_events):
            start_tick = int(tick)
            end_tick = int(ts_events[idx + 1][0]) if idx + 1 < len(ts_events) else int(song_end_tick)
            if end_tick < start_tick:
                end_tick = start_tick
            segments.append((start_tick, end_tick, int(num), max(1, int(den))))

        entries_with_unit: List[Tuple[float, int]] = []
        current_unit = 0.0
        last_tick = None

        for start_tick, end_tick, _num, den in segments:
            if last_tick is None or int(last_tick) != int(start_tick):
                ms = int(round(self._ticks_to_ms_from_tempo_map(start_tick, tempo_map_ticks, ticks_per_beat)))
                entries_with_unit.append((current_unit, ms))
            beat_step = float(ticks_per_beat) * 4.0 / float(max(1, den))
            if beat_step <= 0.0:
                beat_step = float(ticks_per_beat)

            tick = float(start_tick)
            unit = float(current_unit)
            while tick + beat_step < float(end_tick) - 1e-6:
                tick += beat_step
                unit += 1.0
                ms = int(round(self._ticks_to_ms_from_tempo_map(int(round(tick)), tempo_map_ticks, ticks_per_beat)))
                entries_with_unit.append((unit, ms))

            current_unit += (float(end_tick) - float(start_tick)) / beat_step
            last_tick = end_tick

        final_tick = max(0, int(song_end_tick))
        final_ms = int(round(self._ticks_to_ms_from_tempo_map(final_tick, tempo_map_ticks, ticks_per_beat)))
        if not entries_with_unit:
            entries_with_unit.append((0.0, 0))
        if entries_with_unit[-1][1] != final_ms:
            entries_with_unit.append((current_unit, final_ms))

        beat_entries: List[Tuple[int, int]] = []
        seen: Set[Tuple[int, int]] = set()
        for unit, ms in entries_with_unit:
            idx = int(round(unit * EDITOR_BEAT_UNIT_SCALE))
            pair = (idx, int(ms))
            if pair in seen:
                continue
            seen.add(pair)
            beat_entries.append(pair)
        beat_entries.sort(key=lambda item: item[1])
        return beat_entries

    def load_midi(self, path: str, auto_arrange: bool = True) -> None:
        """載入 MIDI。

        `auto_arrange=False` 時只匯入音符、不做自動排譜，維持 MIDI 編輯模式。
        """
        if mido is None:
            raise RuntimeError('mido is not available.')

        mid = open_midi(path)
        self.file_format = 'midi'
        self.root = None
        self.tree = None
        self.json_meta = {}
        self.current_file = path
        self._epb_mode = None   # 換譜面 → 重新判斷 beat_data 格式
        self._epb_count = None

        default_tempo = 500_000
        tempo_events_ticks: List[Tuple[int, int]] = []
        time_signature_events_ticks: List[Tuple[int, int, int]] = []
        base_events_by_track: List[List[Dict[str, Any]]] = [[] for _ in range(len(mid.tracks))]
        raw_notes_ticks: List[Dict[str, int]] = []
        track_titles: Dict[int, str] = {}
        programs: Dict[Tuple[int, int], int] = {}
        pedal_cc_ticks: List[Tuple[int, int]] = []      # (tick, value 0-127)
        first_bpm: Optional[float] = None
        song_end_tick = 0
        is_two_tracks = len(mid.tracks) == 2

        for track_idx, track in enumerate(mid.tracks):
            current_tick = 0
            active: Dict[Tuple[int, int], List[Tuple[int, int, int]]] = {}
            for order, msg in enumerate(track):
                current_tick += int(msg.time)
                song_end_tick = max(song_end_tick, current_tick)

                if msg.type == 'set_tempo':
                    tempo_value = int(msg.tempo)
                    tempo_events_ticks.append((current_tick, tempo_value))
                    if first_bpm is None:
                        default_tempo = tempo_value
                        try:
                            first_bpm = 60_000_000.0 / float(tempo_value)
                        except ZeroDivisionError:
                            first_bpm = 120.0

                if msg.type == 'time_signature':
                    try:
                        time_signature_events_ticks.append(
                            (current_tick, int(msg.numerator), int(msg.denominator))
                        )
                    except Exception:
                        pass

                is_note_on = msg.type == 'note_on' and int(msg.velocity) > 0
                is_note_off = msg.type == 'note_off' or (msg.type == 'note_on' and int(msg.velocity) == 0)

                if is_note_on:
                    key = (int(msg.channel), int(msg.note))
                    active.setdefault(key, []).append((current_tick, int(msg.velocity), order))
                    continue

                if is_note_off:
                    key = (int(msg.channel), int(msg.note))
                    stack = active.get(key)
                    if stack:
                        # 同音配對用**先進先出**：最早那個還沒配對的
                        # note_on，配下一個 note_off。舊版是 stack.pop()
                        # （後進先出），碰到真實鋼琴 MIDI 常見的圓滑奏
                        # ——下一顆已經響了、前一顆的 note_off 才晚一兩個
                        # tick 到——就會把 off 配給剛響的那顆，解析成
                        # 「前一顆 500ms + 新的那顆 1ms」的鬼音。
                        start_tick, velocity, _ = stack.pop(0)
                        raw_notes_ticks.append({
                            'track': int(track_idx),
                            'channel': int(msg.channel),
                            'pitch': int(msg.note),
                            'velocity': int(velocity),
                            'off_velocity': int(getattr(msg, 'velocity', 0)),
                            'start_tick': int(start_tick),
                            'end_tick': int(current_tick),
                            'hand': 0 if is_two_tracks and track_idx == 0 else 1,
                        })
                        if not stack:
                            active.pop(key, None)
                    continue

                # 延音踏板抽成獨立的資料，不要留在 base_events 裡，否則匯出時
                # 會和 self.pedal_spans 展開的訊息重複踩下去。
                if msg.type == 'control_change' and int(msg.control) == 64:
                    pedal_cc_ticks.append((current_tick, int(msg.value)))
                    continue

                # 樂器標示：track_name / instrument_name 這兩個 meta，以及
                # program_change 的 GM 音色編號。多軌 MIDI 常常兩種都有，
                # 名稱優先（作者自己寫的比 GM 編號準）。
                if msg.type in ('track_name', 'instrument_name'):
                    # meta 的字串常常補了 NUL 或其他控制字元（實測 'Piano'），
                    # 直接拿去畫會出現方塊字。
                    raw = str(getattr(msg, 'name', '') or '')
                    # mido 一律用 latin-1 解 meta 字串，但實際上幾乎都是
                    # UTF-8（日文曲名／樂器名很常見）。latin-1 解出來的
                    # 'Ã©Â¢Â' 這種東西再 encode 回去用 UTF-8 解才是原文。
                    # 解不動就維持原樣——那是真的 latin-1 或別的編碼。
                    try:
                        raw = raw.encode('latin-1').decode('utf-8')
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    name = ''.join(ch for ch in raw if ch.isprintable()).strip()
                    if name:
                        track_titles.setdefault(int(track_idx), name)
                elif msg.type == 'program_change':
                    programs.setdefault(
                        (int(track_idx), int(msg.channel)), int(msg.program))

                if msg.type != 'end_of_track':
                    base_events_by_track[track_idx].append({
                        'tick': int(current_tick),
                        'order': int(order),
                        'msg': msg.copy(time=0),
                    })

            for (channel, pitch), stack in active.items():
                for start_tick, velocity, _order in stack:
                    raw_notes_ticks.append({
                        'track': int(track_idx),
                        'channel': int(channel),
                        'pitch': int(pitch),
                        'velocity': int(velocity),
                        'off_velocity': 0,
                        'start_tick': int(start_tick),
                        'end_tick': max(int(start_tick) + 1, int(current_tick)),
                        'hand': 0 if is_two_tracks and track_idx == 0 else 1,
                    })

        tempo_map_ticks = self._build_tempo_map_ticks(tempo_events_ticks, default_tempo)
        self.time_sig_changes = []
        if time_signature_events_ticks:
            for tick, num, den in sorted(time_signature_events_ticks, key=lambda item: item[0]):
                ms = int(round(self._ticks_to_ms_from_tempo_map(int(tick), tempo_map_ticks, mid.ticks_per_beat)))
                self.time_sig_changes.append((ms, int(num), int(den)))
            self.beats_per_bar = int(self.time_sig_changes[0][1])
            self.time_sig_denominator = int(self.time_sig_changes[0][2])
        else:
            self.beats_per_bar = 4
            self.time_sig_denominator = 4

        self.bpm = float(first_bpm or round(60_000_000.0 / float(default_tempo), 6))
        self.music_end_ms = float(self._ticks_to_ms_from_tempo_map(song_end_tick, tempo_map_ticks, mid.ticks_per_beat))
        beat_entries = self._build_midi_precise_beat_entries(
            song_end_tick,
            mid.ticks_per_beat,
            tempo_map_ticks,
            time_signature_events_ticks,
        )
        if beat_entries:
            self._write_beat_entries(beat_entries, mark_precise=True)

        raw_notes_ticks.sort(key=lambda note: (int(note['start_tick']), int(note['track']), int(note['pitch'])))
        # (track, channel) → 給人看的樂器名稱。畫面上的聲部圖例用它。
        voices: Dict[Tuple[int, int], str] = {}
        for item in raw_notes_ticks:
            key = (int(item['track']), int(item['channel']))
            if key in voices:
                continue
            title = track_titles.get(int(item['track']), '')
            program = programs.get(key)
            if program is None:
                program = programs.get((int(item['track']), 0))
            # program 0（Acoustic Grand Piano）是幾乎所有 MIDI 的預設值，
            # 沒有資訊量——實測 Designant 二十軌全是 0，接上去只會讓
            # 「Bass」變成「Bass（Acoustic Grand Piano）」。只有作者真的
            # 換過音色才值得補在後面。
            gm = gm_program_name(program) if program else ''
            if title and gm and title.lower() != gm.lower():
                voices[key] = '%s（%s）' % (title, gm)
            else:
                voices[key] = title or gm or ''
        self.midi_voice_names = voices

        raw_notes_ticks = self._drop_degenerate_duplicate_notes(
            raw_notes_ticks, tempo_map_ticks, mid.ticks_per_beat
        )
        notes: List[GNote] = []
        for idx, item in enumerate(raw_notes_ticks):
            start_ms = int(round(self._ticks_to_ms_from_tempo_map(int(item['start_tick']), tempo_map_ticks, mid.ticks_per_beat)))
            end_ms = int(round(self._ticks_to_ms_from_tempo_map(int(item['end_tick']), tempo_map_ticks, mid.ticks_per_beat)))
            if end_ms <= start_ms:
                end_ms = start_ms + 1
            pitch = int(item['pitch'])
            center = midi_pitch_to_game_lane_index(pitch)
            min_key, max_key = lane_center_to_width3_range(center)

            note = GNote(None, idx)
            note.start = start_ms
            note.end = end_ms
            note.gate = max(1, end_ms - start_ms)
            note.min_key = min_key
            note.max_key = max_key
            note.note_type = 2 if note.gate >= 500 else 0
            note.hand = int(item['hand'])
            note.track = int(item['track'])
            note.pitch = pitch
            note.velocity = self._clamp_midi_byte(int(item['velocity']), 100, minimum=1)
            note.channel = max(0, min(15, int(item['channel'])))
            note.off_velocity = self._clamp_midi_byte(int(item['off_velocity']), 0)
            notes.append(note)

        self.notes_tree = notes
        self.rebuild_display_cache()
        self.undo_stack.clear()
        self.dirty = False
        self.midi_data = {
            'ticks_per_beat': int(mid.ticks_per_beat),
            'type': int(mid.type),
            'track_count': int(len(mid.tracks)),
            'base_events_by_track': base_events_by_track,
            'tempo_map_ticks': tempo_map_ticks,
        }
        self.pedal_spans = self._pedal_spans_from_cc(pedal_cc_ticks, tempo_map_ticks, mid.ticks_per_beat)
        self.pitches_folded = self.fold_pitches_into_piano_range()
        if auto_arrange:
            # 踏板殘響造成的長音先裁掉，再排版 —— 否則那些過長的長條會佔住
            # 走廊，把後面的排版空間吃光。
            self.trim_pedal_sustained_holds()
            self.last_smart_chart_stats = self.smart_arrange_midi()
            self.midi_unarranged = False
        else:
            # 不轉譜：維持 MIDI 編輯模式，音符保留原始音高與時間，鍵道只是
            # 依音高做一個直觀的暫時位置，使用者之後可以再手動或自動排版。
            self.last_smart_chart_stats = None
            self._layout_midi_by_pitch()
            # 還沒排譜 —— 只有音高檢視有意義，其他檢視方式要先轉譜
            self.midi_unarranged = True

    def restore_pitches_from_midi(self, midi_path: str,
                                  tolerance_ms: int = 40) -> Tuple[int, int]:
        """拿原始 MIDI 把譜面裡壞掉的音高比對回來。

        可行的前提是**排譜不改時間**——`start_timing_msec` 直接沿用 MIDI 的
        起音時間，所以兩邊可以用時間對齊。鍵道位置和音符類型完全不動，只覆蓋
        `pitch`。

        對齊方式分兩層：先把兩邊各自按起音時間分組、依序配對（單調推進，容
        許 `tolerance_ms` 的誤差）；同一組之內再各自排序後依名次配對——排譜
        器本來就是照音高決定鍵道順序的，所以「鍵道由低到高」等同「音高由低
        到高」，名次配得起來。

        回傳 (改掉的音符數, 沒配到的音符數)。
        """
        # 用同一條載入路徑取得參考音符——自己重解 tempo 很容易出錯（多軌檔案
        # 的 tempo 事件在 track 0、音符在別軌，逐軌各算各的時間會全錯，實測
        # 會把 832 顆本來正確的音高改壞）。走 load_midi 就和當初轉譜時用的是
        # 同一套時間換算，起音時間必然對得起來。
        reference = NoteModel()
        reference.load_midi(midi_path, auto_arrange=False)
        ref = [(int(n.start), int(n.pitch)) for n in reference.notes_tree
               if n.pitch is not None]
        if not ref:
            return (0, len(self.notes_tree))

        ref_groups: Dict[int, List[int]] = {}
        for when, pitch in ref:
            ref_groups.setdefault(when, []).append(pitch)
        ref_times = sorted(ref_groups)

        chart_groups: Dict[int, List[Any]] = {}
        for note in self.notes_tree:
            chart_groups.setdefault(int(note.start), []).append(note)
        chart_times = sorted(chart_groups)

        # 譜面被整份平移過的話，絕對時間永遠對不上——實測曲庫裡好幾份就是
        # 開頭補了空白（chronomia +3600ms、testify_mv +6700ms、エンドマーク
        # +6850ms），音符卻一顆不缺。先把平移量量出來、扣掉，再走原本那條
        # 容錯配對，這樣「平移」和「平移＋少數幾組被刪掉」兩種都吃得下。
        #
        # 量法：拿前幾組的時間差當候選（外加 0 = 沒平移），各自數數看能對上
        # 幾組，取最高的。比直接用第一組的差穩——譜面開頭多一顆或少一顆音，
        # 第一組的差就整個歪掉。
        def _hits(offset: int) -> int:
            hit = 0
            for value in chart_times:
                target = value - offset
                index = bisect_left(ref_times, target)
                for probe in (index - 1, index):
                    if 0 <= probe < len(ref_times) and abs(ref_times[probe] - target) <= tolerance_ms:
                        hit += 1
                        break
            return hit

        offset = 0
        if chart_times and ref_times:
            candidates = {0}
            for index in range(min(8, len(chart_times), len(ref_times))):
                candidates.add(chart_times[index] - ref_times[index])
            offset, best_hits = 0, _hits(0)
            for candidate in candidates:
                if candidate == 0:
                    continue
                hits = _hits(candidate)
                if hits > best_hits:
                    offset, best_hits = candidate, hits
            if offset:
                logging.info('pitch restore: chart is shifted %+d ms from the MIDI',
                             offset)

            # 平移修不了「被拉伸」的譜（recollect-lines_ele 是 0~167598 對
            # MIDI 的 0~165413，中間每一組都差一點點，愈後面差愈多）。那種
            # 情形只要**發音組數完全相同**，就改用序位配對：第 n 組配第 n 組。
            # 兩條路取能對上比較多的那條。
            if (len(chart_times) == len(ref_times)
                    and best_hits < len(chart_times) * 0.9):
                changed = unmatched = 0
                for index, when in enumerate(chart_times):
                    group = chart_groups[when]
                    pitches = sorted(ref_groups[ref_times[index]])
                    group.sort(key=lambda n: (int(n.min_key) + int(n.max_key),
                                              int(n.start)))
                    if len(group) != len(pitches):
                        unmatched += abs(len(group) - len(pitches))
                    for note, pitch in zip(group, pitches):
                        if note.pitch != pitch:
                            note.pitch = int(pitch)
                            changed += 1
                logging.info('pitch restore (by ordinal): %d changed, %d unmatched',
                             changed, unmatched)
                return (changed, unmatched)

        changed = unmatched = 0
        cursor = 0
        for when in chart_times:
            aligned = when - offset
            # 單調推進到最接近的參考時間
            while (cursor + 1 < len(ref_times)
                   and abs(ref_times[cursor + 1] - aligned) <= abs(ref_times[cursor] - aligned)):
                cursor += 1
            group = chart_groups[when]
            if cursor >= len(ref_times) or abs(ref_times[cursor] - aligned) > tolerance_ms:
                unmatched += len(group)
                continue
            pitches = sorted(ref_groups[ref_times[cursor]])
            # 鍵道由低到高 == 音高由低到高，依名次配
            group.sort(key=lambda n: (int(n.min_key) + int(n.max_key), int(n.start)))
            if len(group) != len(pitches):
                unmatched += abs(len(group) - len(pitches))
            for note, pitch in zip(group, pitches):
                if note.pitch != pitch:
                    note.pitch = int(pitch)
                    changed += 1
        logging.info('pitch restore: %d changed, %d unmatched', changed, unmatched)
        return (changed, unmatched)

    def _merge_hidden_into_hosts(self) -> int:
        """把隱藏音符的 sub 元素併回寄主的 `sub_note_data`（存檔前呼叫）。

        從 XML 載入的隱藏音符持有原本那個 sub 元素、也記得它原本的排列位置
        （`_sub_order`），所以照順序併回去就是逐位元組還原。使用者自己標記
        隱藏的音符沒有原始元素，這裡替它現做一個。
        """
        hosts: Dict[int, List[Tuple[int, Any]]] = {}
        orphans = 0
        for note, host in self.resolve_hidden_hosts():
            if host is None:
                # 沒有寄主可掛就不能隱藏——隱藏音符不會單獨寫成 <note>，
                # 放著不管等於整顆音消失。取消隱藏至少把音留住。
                note.hidden = False
                orphans += 1
                continue
            order = (getattr(note, '_sub_order', None) or [10_000])[0]
            subs = list(getattr(note, 'sub_elems', []) or [])
            se = subs[0] if subs else self._make_sub_elem(note)
            hosts.setdefault(id(host), []).append((order, se))
        if orphans:
            logging.warning(
                '%d hidden notes had no host and were kept visible', orphans)
        if not hosts:
            return 0
        by_id = {id(n): n for n in self.notes_tree}
        for host_id, items in hosts.items():
            host = by_id.get(host_id)
            if host is None:
                continue
            own = list(zip(getattr(host, '_sub_order', None) or
                           range(len(getattr(host, 'sub_elems', []) or [])),
                           getattr(host, 'sub_elems', []) or []))
            if not own:
                # 寄主自己沒有 sub（編輯器新增的音符）就現做一個。少了這一個，
                # sub_note_data 裡就只剩隱藏音符那一顆，載入時
                # `_split_sub_notes_into_hidden` 看到 len(subs) < 2 直接跳過，
                # 於是寄主的音變成隱藏音的音、隱藏音符本身沒了。
                own = [(-1, self._make_sub_elem(host))]
            host.sub_elems = [se for _o, se in sorted(own + items, key=lambda x: x[0])]
        return sum(len(v) for v in hosts.values())

    @staticmethod
    def _make_sub_elem(note: Any) -> ET.Element:
        """替沒有原始 sub 元素的隱藏音符現做一個（使用者手動標記的情況）。"""
        se = ET.Element('sub_note')
        def add(tag, val, ty):
            el = ET.SubElement(se, tag); el.text = str(int(val)); el.set('__type', ty)
        add('start_timing_msec', note.start, 's32')
        add('end_timing_msec', note.end, 's32')
        if note.pitch is not None:
            add('scale_piano', midi_to_official_piano_index(int(note.pitch)), 'u8')
        add('velocity', note.velocity if note.velocity is not None else 100, 'u8')
        add('track_index', note.track if note.track is not None else 0, 's32')
        return se

    def _load_velocity_from_subs(self) -> int:
        """把官方 `<sub_note>` 裡的 velocity 讀進 `GNote.velocity`。

        官方格式的 `<note>` **沒有** velocity 欄位——力度是記在 sub_note 上的
        （`start_timing_msec / end_timing_msec / scale_piano / velocity /
        track_index`）。以前載入時只讀 `scale_piano`，velocity 原封不動留在
        sub 元素裡；檔案存回去是無損的，但編輯器「看不到」那些力度：音符上的
        力度數字空白、強弱曲線沒有資料、放新音抄不到鄰居、鋼琴音軌只能用預設值。

        這裡只是把已經在檔案裡的值搬進記憶體，不改動任何檔案內容。
        """
        filled = 0
        for note in self.notes_tree:
            if note.velocity is not None:
                continue
            for se in getattr(note, 'sub_elems', None) or ():
                raw = _child_int(se, 'src_velocity')
                if raw is None:
                    raw = _child_int(se, 'velocity')
                if raw is not None and int(raw) >= 0:
                    note.velocity = max(1, min(127, int(raw)))
                    filled += 1
                    break
        if filled:
            logging.info('velocity taken from %d sub_note entries', filled)
        return filled

    def _write_velocity_into_subs(self) -> None:
        """把 `GNote.velocity` 寫回它自己的 `<sub_note>`（官方存力度的地方）。

        沒有 sub 的音符（編輯器新增的）就維持寫在 `<note>` 上的擴充欄位。
        """
        for note in self.notes_tree:
            if note.velocity is None:
                continue
            for se in getattr(note, 'sub_elems', None) or ():
                child = se.find('velocity')
                if child is None:
                    child = ET.SubElement(se, 'velocity')
                    child.set('__type', 'u8')
                child.text = str(max(1, min(127, int(note.velocity))))
                break

    def _split_sub_notes_into_hidden(self) -> int:
        """把「一鍵多音」的 sub_note 拆成獨立的隱藏音符。

        官方每顆 note 都有 sub_note_data，而且 73% 的單 sub 就是 note 自己的
        音高（重複記載）；真正代表「這個按鍵同時發出別的音」的，是清單裡
        **音高不等於代表音**的那些 sub。

        拆出來的隱藏音符直接**持有原本那個 sub 元素**（連同 velocity、
        track_index 等欄位），存檔時原封不動寫回去，所以往返無損。
        """
        made = 0
        for note in list(self.notes_tree):
            subs = list(getattr(note, 'sub_elems', []) or [])
            if len(subs) < 2:
                continue
            own, extra = [], []
            for order, se in enumerate(subs):
                child = se.find('scale_piano')
                sp = None
                if child is not None and child.text not in (None, ''):
                    try:
                        sp = official_piano_index_to_midi(int(float(child.text)))
                    except (TypeError, ValueError):
                        sp = None
                # 代表音留在寄主身上，其餘各自變成一顆隱藏音符
                if not own and (sp is None or sp == note.pitch):
                    own.append((order, se))
                else:
                    extra.append((order, se))
            if not extra:
                continue
            if not own:                      # 沒有任何 sub 等於代表音
                own.append(extra.pop(0))
            note.sub_elems = [se for _o, se in own]
            note._sub_order = [o for o, _se in own]
            for order, se in extra:
                child = se.find('scale_piano')
                ghost = GNote(None, len(self.notes_tree))
                # 時間用 sub 自己的，不是寄主的。官方的 sub_note 各自帶
                # start/end_timing_msec，而且常常和寄主不同——直接抄寄主的話，
                # 存檔往返一次每顆隱藏音符的頭尾都會被改掉（實測 12 首官方譜
                # 有 9 首對不起來）。
                sub_start = _child_int(se, 'start_timing_msec')
                sub_end = _child_int(se, 'end_timing_msec')
                ghost.start = int(sub_start) if sub_start is not None else int(note.start)
                ghost.end = int(sub_end) if sub_end is not None else int(note.end)
                if ghost.end <= ghost.start:
                    ghost.end = ghost.start + max(1, int(note.gate))
                ghost.gate = ghost.end - ghost.start
                ghost.min_key, ghost.max_key = int(note.min_key), int(note.max_key)
                ghost.note_type = int(note.note_type)
                ghost.hand = int(note.hand)
                ghost.track, ghost.channel = note.track, note.channel
                ghost.hidden = True
                ghost.sub_elems = [se]
                ghost._sub_order = [order]
                ghost._sub_host = note
                if child is not None and child.text not in (None, ''):
                    try:
                        ghost.pitch = official_piano_index_to_midi(int(float(child.text)))
                    except (TypeError, ValueError):
                        ghost.pitch = note.pitch
                self.notes_tree.append(ghost)
                made += 1
        if made:
            logging.info('split %d sub-notes into hidden notes', made)
        return made

    @staticmethod
    def _mean_pitch(note: Any) -> Optional[float]:
        """音符代表的音高。自帶多個 sub_note（一鍵多音）時取平均。"""
        subs = list(getattr(note, 'sub_elems', []) or [])
        values = []
        for se in subs:
            child = se.find('scale_piano')
            if child is not None and child.text not in (None, ''):
                try:
                    values.append(official_piano_index_to_midi(int(float(child.text))))
                except (TypeError, ValueError):
                    pass
        if values:
            return sum(values) / len(values)
        return float(note.pitch) if note.pitch is not None else None

    def resolve_hidden_hosts(
        self, window_ms: int = 120
    ) -> List[Tuple[Any, Optional[Any]]]:
        """替每個隱藏音符找出要掛載的可見音符。

        規則：同一時刻（容許 `window_ms`）的可見音符中，音高最接近的那顆。
        隱藏音符自帶多音時用平均音高比對。找不到寄主就回 None——那種情況不能
        隱藏，否則音就消失了。
        """
        visible = [n for n in self.notes_tree if not getattr(n, 'hidden', False)]
        visible.sort(key=lambda n: int(n.start))
        starts = [int(n.start) for n in visible]
        alive = {id(n) for n in visible}
        out: List[Tuple[Any, Optional[Any]]] = []
        for note in self.notes_tree:
            if not getattr(note, 'hidden', False):
                continue
            # 從 XML 拆出來的隱藏音符記得自己原本掛在誰身上，直接用它——官方的
            # sub_note 有自己的 start/end，和寄主差超過 window_ms 是常態，用時間
            # 就近猜會猜不到，於是存檔時被當成孤兒。
            known = getattr(note, '_sub_host', None)
            if known is not None and id(known) in alive:
                out.append((note, known))
                continue
            mine = self._mean_pitch(note)
            when = int(note.start)
            lo = bisect_left(starts, when - window_ms)
            hi = bisect_right(starts, when + window_ms)
            pool = visible[lo:hi]
            if not pool or mine is None:
                out.append((note, pool[0] if pool else None))
                continue
            host = min(pool, key=lambda v: (
                abs((self._mean_pitch(v) if self._mean_pitch(v) is not None else 1e9) - mine),
                abs(int(v.start) - when),
            ))
            out.append((note, host))
        return out

    def fold_pitches_into_piano_range(self) -> int:
        """把鋼琴 88 鍵範圍外的音高整八度折回範圍內。

        MIDI 可以到 0~127，但遊戲的 scale_piano 只有 1~88（MIDI 21~108）。
        載入時不夾、存檔時才夾的話，109/110/115 會**無聲地全部塌成同一個 88**，
        記憶體裡看起來正常、存出去的譜卻是錯的。

        折疊而不是夾：整個八度平移保留音級和相對高低，比全部擠在邊界合理。
        回傳被折疊的音符數。
        """
        folded = 0
        for note in self.notes_tree:
            if note.pitch is None:
                continue
            pitch = int(note.pitch)
            original = pitch
            while pitch > MIDI_PIANO_MAX:
                pitch -= 12
            while pitch < MIDI_PIANO_MIN:
                pitch += 12
            # 極端值（例如打擊軌的 0）折完仍可能出界，最後才夾
            pitch = max(MIDI_PIANO_MIN, min(MIDI_PIANO_MAX, pitch))
            if pitch != original:
                note.pitch = pitch
                folded += 1
        if folded:
            logging.warning(
                '%d notes were outside the 88-key range and were folded by octaves',
                folded,
            )
        return folded

    def _layout_midi_by_pitch(self) -> None:
        """不排譜時的暫時鍵道位置：單純照音高線性攤在鍵盤上。

        這不是譜面排版，只是讓 MIDI 編輯模式在小節檢視下也看得到東西；
        使用者按下自動排譜時會整個重算。
        """
        pitches = [int(n.pitch) for n in self.notes_tree if n.pitch is not None]
        if not pitches:
            return
        lo, hi = min(pitches), max(pitches)
        span = max(1, hi - lo)
        usable = max(1, TOTAL_GAME_KEYS - 3)
        for n in self.notes_tree:
            if n.pitch is None:
                continue
            centre = int(round((int(n.pitch) - lo) / span * usable))
            n.min_key = max(0, min(TOTAL_GAME_KEYS - 3, centre))
            n.max_key = n.min_key + 2

    def smart_arrange_midi(self, style: Optional[str] = None):
        """Keep all notes while arranging lanes from MIDI pitch and timing.

        `style` 是轉譜風格（`smart_chart.STYLE_EATHER` / `STYLE_OFFICIAL`）。
        不指定時讀偏好設定裡的 `chart_style`。
        """
        from .smart_chart import (
            arrange_midi_notes, normalise_style, settings_for_style,
            STYLE_EATHER,
        )

        if style is None:
            try:
                from .settings import settings as _prefs
                style = _prefs.get('chart_style')
            except Exception:            # noqa: BLE001
                style = STYLE_EATHER
        style = normalise_style(style)
        beat_ms = 60_000.0 / max(1.0, float(self.bpm or 120.0))
        stats = arrange_midi_notes(
            self.notes_tree,
            settings_for_style(
                style, beat_ms=beat_ms, classify_articulations=True
            ),
        )
        self.chart_style = style
        self.midi_unarranged = False        # 排過了，其他檢視方式解鎖
        self.last_smart_chart_stats = stats
        self.rebuild_display_cache()
        self.dirty = True
        return stats

    # 同一 tick、同一音軌、同一音高被敲了兩次，而且其中一顆只有幾毫秒——
    # 那是 MIDI 產生器留下的殘渣（實測 bad-apple 有 161 組，形狀都是
    # 「412ms 的正常音 + 1ms 的分身」），不是音樂內容。留著的話排譜器必須
    # 把它排到另一條鍵道上，玩家就得為同一個聲音按兩下。官方語料裡這種
    # 東西幾乎不存在（13.7 萬組和絃只有 1 組同音重複）。
    #
    # 只清「明顯是殘渣」的：最短的那顆 <= duplicate_stub_ms，而且同組最長
    # 的至少是它的 5 倍、也至少有 50ms。兩顆都是正常長度的重複（可能是刻意
    # 疊軌）一律保留。
    DUPLICATE_STUB_MS = 20

    def _drop_degenerate_duplicate_notes(
        self,
        raw_notes: List[Dict[str, int]],
        tempo_map_ticks,
        ticks_per_beat: int,
    ) -> List[Dict[str, int]]:
        if not raw_notes:
            return raw_notes

        def length_ms(item: Dict[str, int]) -> float:
            start = self._ticks_to_ms_from_tempo_map(
                int(item['start_tick']), tempo_map_ticks, ticks_per_beat
            )
            end = self._ticks_to_ms_from_tempo_map(
                int(item['end_tick']), tempo_map_ticks, ticks_per_beat
            )
            return float(end) - float(start)

        buckets: Dict[Tuple[int, int, int], List[Dict[str, int]]] = {}
        for item in raw_notes:
            key = (
                int(item['track']),
                int(item['start_tick']),
                int(item['pitch']),
            )
            buckets.setdefault(key, []).append(item)

        dropped: set = set()
        for group in buckets.values():
            if len(group) < 2:
                continue
            lengths = [length_ms(item) for item in group]
            shortest = min(lengths)
            longest = max(lengths)
            if shortest > self.DUPLICATE_STUB_MS:
                continue
            if longest < max(5.0 * shortest, 50.0):
                continue
            keeper = max(range(len(group)), key=lambda i: lengths[i])
            for index, item in enumerate(group):
                if index != keeper and lengths[index] <= self.DUPLICATE_STUB_MS:
                    dropped.add(id(item))
        if not dropped:
            return raw_notes
        self.dropped_duplicate_notes = len(dropped)
        logging.info(
            '%d duplicate same-pitch stub note(s) dropped on import',
            len(dropped),
        )
        return [item for item in raw_notes if id(item) not in dropped]

    def save_midi(self, path: Optional[str] = None) -> None:
        if mido is None:
            raise RuntimeError('mido is not available.')
        if path is None:
            path = self.current_file
        if path is None:
            raise ValueError('No save path specified.')

        midi_info = self.midi_data or {}
        ticks_per_beat = int(midi_info.get('ticks_per_beat', 480))
        tempo_map_ticks = list(midi_info.get('tempo_map_ticks') or [])
        if not tempo_map_ticks:
            bpm = max(1.0, float(self.bpm or 120.0))
            tempo_map_ticks = [(0, int(round(60_000_000.0 / bpm)))]

        base_events_by_track: List[List[Dict[str, Any]]] = copy.deepcopy(
            midi_info.get('base_events_by_track', [])
        )
        track_count = max(1, len(base_events_by_track))
        explicit_tracks = [int(n.track) for n in self.notes_tree if n.track is not None]
        if explicit_tracks:
            track_count = max(track_count, max(explicit_tracks) + 1)
        while len(base_events_by_track) < track_count:
            base_events_by_track.append([])

        if self.midi_data is None:
            tempo_value = int(tempo_map_ticks[0][1])
            base_events_by_track[0].append({
                'tick': 0,
                'order': -2,
                'msg': mido.MetaMessage('set_tempo', tempo=tempo_value, time=0),
            })
            base_events_by_track[0].append({
                'tick': 0,
                'order': -1,
                'msg': mido.MetaMessage(
                    'time_signature',
                    numerator=int(self.beats_per_bar),
                    denominator=int(self.time_sig_denominator),
                    time=0,
                ),
            })

        events_by_track: List[List[Tuple[int, int, int, Any]]] = [[] for _ in range(track_count)]
        for track_idx, items in enumerate(base_events_by_track):
            for item in items:
                events_by_track[track_idx].append((
                    int(item.get('tick', 0)),
                    0,
                    int(item.get('order', 0)),
                    item['msg'].copy(time=0),
                ))

        # 踏板一律寫在 track 0：多軌檔每軌各寫一份的話，同一段殘響會被踩放
        # 好幾次，聽起來像斷奏。CC64 是全域的，一份就夠。
        for p_order, (p_start, p_end) in enumerate(self.pedal_spans):
            down_tick = self._ms_to_ticks_from_tempo_map(float(p_start), tempo_map_ticks, ticks_per_beat)
            up_tick = max(down_tick + 1,
                          self._ms_to_ticks_from_tempo_map(float(p_end), tempo_map_ticks, ticks_per_beat))
            events_by_track[0].append((
                int(down_tick), 0, -2_000_000 + p_order * 2,
                mido.Message('control_change', control=64, value=127, channel=0, time=0),
            ))
            events_by_track[0].append((
                int(up_tick), 0, -2_000_000 + p_order * 2 + 1,
                mido.Message('control_change', control=64, value=0, channel=0, time=0),
            ))

        for order, note in enumerate(self.notes_tree):
            track = int(note.track) if note.track is not None else self._default_midi_track_for_hand(note.hand)
            if track < 0:
                track = 0
            while track >= len(events_by_track):
                events_by_track.append([])
            channel = max(0, min(15, int(note.channel) if note.channel is not None else self._default_midi_channel_for_track(track)))
            velocity = self._clamp_midi_byte(
                note.velocity if note.velocity is not None else self._default_midi_velocity_for_track(track),
                100,
                minimum=1,
            )
            off_velocity = self._clamp_midi_byte(
                note.off_velocity if note.off_velocity is not None else self._default_midi_off_velocity_for_track(track),
                0,
            )
            center_lane = (int(note.min_key) + int(note.max_key)) // 2
            pitch = int(note.pitch) if note.pitch is not None else game_lane_index_to_midi_pitch(center_lane)
            pitch = max(0, min(127, pitch))
            start_tick = self._ms_to_ticks_from_tempo_map(float(note.start), tempo_map_ticks, ticks_per_beat)
            end_tick = self._ms_to_ticks_from_tempo_map(float(note.end), tempo_map_ticks, ticks_per_beat)
            end_tick = max(start_tick + 1, end_tick)

            events_by_track[track].append((
                int(start_tick),
                2,
                int(order * 2),
                mido.Message('note_on', note=pitch, velocity=velocity, channel=channel, time=0),
            ))
            events_by_track[track].append((
                int(end_tick),
                1,
                int(order * 2 + 1),
                mido.Message('note_off', note=pitch, velocity=off_velocity, channel=channel, time=0),
            ))

        out_type = int(midi_info.get('type', 1 if len(events_by_track) > 1 else 0))
        if len(events_by_track) > 1 and out_type == 0:
            out_type = 1
        out_mid = mido.MidiFile(type=out_type, ticks_per_beat=ticks_per_beat)
        for track_events in events_by_track:
            midi_track = mido.MidiTrack()
            last_tick = 0
            for tick, _sort_rank, order, msg in sorted(track_events, key=lambda item: (item[0], item[1], item[2])):
                _ = order
                msg_copy = msg.copy(time=max(0, int(tick) - int(last_tick)))
                midi_track.append(msg_copy)
                last_tick = int(tick)
            midi_track.append(mido.MetaMessage('end_of_track', time=0))
            out_mid.tracks.append(midi_track)

        out_mid.save(path)
        self.file_format = 'midi'
        self.current_file = path
        self.dirty = False

    def delete_midi_tracks(self, tracks_to_delete: Set[int]) -> int:
        if not tracks_to_delete:
            return 0
        normalized = {int(track) for track in tracks_to_delete if int(track) >= 0}
        if not normalized:
            return 0

        before = len(self.notes_tree)
        self.notes_tree = [
            n for n in self.notes_tree
            if n.track is None or int(n.track) not in normalized
        ]
        for n in self.notes_tree:
            if n.track is not None:
                shift = sum(1 for deleted in normalized if deleted < int(n.track))
                n.track = int(n.track) - shift

        if self.midi_data is not None:
            base_events = list(self.midi_data.get('base_events_by_track', []))
            kept_events = [
                events for idx, events in enumerate(base_events)
                if idx not in normalized
            ]
            if not kept_events:
                kept_events = [[]]
            self.midi_data['base_events_by_track'] = kept_events
            self.midi_data['track_count'] = len(kept_events)
            midi_type = int(self.midi_data.get('type', 1 if len(kept_events) > 1 else 0))
            if len(kept_events) <= 1:
                midi_type = 0
            elif midi_type == 0:
                midi_type = 1
            self.midi_data['type'] = midi_type

        self.rebuild_display_cache()
        self.dirty = True
        return before - len(self.notes_tree)

    # ------------------------------------------------------------------
    # 存檔
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 輔助：從記憶體欄位建立 XML note 元素
    # ------------------------------------------------------------------

    @staticmethod
    def _build_note_element(
        n: 'GNote',
        idx: int,
        lane_index_base: int = EXTERNAL_LANE_BASE,
        key_kind: int = 0,
        measure_index: int = 0,
    ) -> ET.Element:
        """從 GNote 記憶體欄位建立標準格式的 XML <note> 元素。"""
        def add_el(parent: ET.Element, tag: str, val, type_attr: str) -> ET.Element:
            el = ET.SubElement(parent, tag)
            el.text = str(val)
            el.set('__type', type_attr)
            return el

        xml_min_key, xml_max_key = lane_range_to_serialized(
            n.min_key,
            n.max_key,
            lane_index_base,
        )

        note_el = ET.Element('note')
        note_el.set('start_timing_msec', str(n.start))
        note_el.set('end_timing_msec',   str(n.end))
        note_el.set('gate_time_msec',    str(n.gate))
        note_el.set('index',             str(idx))
        note_el.set('min_key_index',     str(xml_min_key))
        note_el.set('max_key_index',     str(xml_max_key))
        note_el.set('note_type',         str(n.note_type))
        note_el.set('hand',              str(n.hand))
        if n.pitch is not None:
            note_el.set('scale_piano', str(midi_to_official_piano_index(n.pitch)))
        if n.velocity is not None:
            note_el.set('velocity', str(int(n.velocity)))

        add_el(note_el, 'index',              idx,        's32')
        add_el(note_el, 'start_timing_msec',  n.start,    's32')
        add_el(note_el, 'end_timing_msec',    n.end,      's32')
        add_el(note_el, 'gate_time_msec',     n.gate,     's32')
        if n.pitch is not None:
            add_el(note_el, 'scale_piano',    midi_to_official_piano_index(n.pitch),    'u8')
        if n.velocity is not None:
            add_el(note_el, 'velocity',       int(n.velocity),  'u8')
        add_el(note_el, 'min_key_index',      xml_min_key,  's32')
        add_el(note_el, 'max_key_index',      xml_max_key,  's32')
        add_el(note_el, 'note_type',          n.note_type,'s32')
        add_el(note_el, 'hand',               n.hand,     's32')
        add_el(note_el, 'key_kind',           key_kind,   's32')
        add_el(note_el, 'param1',             getattr(n, 'param1', 0),  's32')
        add_el(note_el, 'param2',             getattr(n, 'param2', 0),  's32')
        add_el(note_el, 'param3',             getattr(n, 'param3', 0),  's32')
        add_el(note_el, 'measure_index',      measure_index, 's32')
        if n.sub_elems:
            sub_root = ET.SubElement(note_el, 'sub_note_data')
            for sub in n.sub_elems:
                sub_root.append(copy.deepcopy(sub))
        return note_el

    @staticmethod
    def _add_typed_xml(parent: ET.Element, tag: str, text: Any, type_attr: str) -> ET.Element:
        el = ET.SubElement(parent, tag)
        el.text = str(text)
        el.set('__type', type_attr)
        return el

    @staticmethod
    def _upsert_typed_xml(
        parent: ET.Element,
        tag: str,
        text: Any,
        type_attr: str,
    ) -> ET.Element:
        el = parent.find(tag)
        if el is None:
            el = ET.SubElement(parent, tag)
        el.text = str(text)
        el.set('__type', type_attr)
        return el

    def _ensure_event_data_for_export(self) -> None:
        assert self.root is not None
        if self.root.find('event_data') is not None:
            return

        event_root = ET.Element('event_data')
        for idx in range(9):
            ev = ET.SubElement(event_root, 'event')
            self._add_typed_xml(ev, 'index', idx, 's32')
            self._add_typed_xml(ev, 'start_timing_msec', 0, 's32')
            self._add_typed_xml(ev, 'type', idx, 's32')
            self._add_typed_xml(ev, 'value', 0, 's64')

        children = list(self.root)
        note_data = self.root.find('note_data')
        beat_data = self.root.find('beat_data')
        if note_data is not None and note_data in children:
            self.root.insert(children.index(note_data) + 1, event_root)
        elif beat_data is not None and beat_data in children:
            self.root.insert(children.index(beat_data), event_root)
        else:
            self.root.append(event_root)

    def _ensure_xml_tree_for_export(self) -> None:
        if self.root is not None and self.tree is not None:
            return

        root = ET.Element('music_score')
        hdr = ET.SubElement(root, 'header')
        self._add_typed_xml(hdr, 'max_scale', 108, 's32')
        self._add_typed_xml(hdr, 'min_scale', 21, 's32')
        self._add_typed_xml(hdr, 'file_version', 1, 's16')
        self._add_typed_xml(hdr, 'first_bpm', bpm_to_xml_value(self.bpm), 's64')
        self._add_typed_xml(hdr, 'music_finish_time_msec', int(round(self.music_end_ms)), 's32')
        self._add_typed_xml(hdr, 'time_signature_numerator', int(self.beats_per_bar), 's32')
        self._add_typed_xml(hdr, 'time_signature_denominator', int(self.time_sig_denominator), 's32')
        self._add_typed_xml(hdr, 'time_signature', f'{self.beats_per_bar}/{self.time_sig_denominator}', 'str')
        if self.beat_offset_ms:
            self._add_typed_xml(hdr, 'beat_offset_ms', int(round(self.beat_offset_ms)), 's32')

        beat_root = ET.SubElement(root, 'beat_data')
        beat_entries = self.get_beat_entries()
        if beat_entries:
            for bidx, bms in beat_entries:
                beat_el = ET.SubElement(beat_root, 'beat')
                self._add_typed_xml(beat_el, 'index', int(bidx), 's32')
                self._add_typed_xml(beat_el, 'start_timing_msec', int(bms), 's32')
        else:
            bar_ms = self._bar_ms()
            total_bars = int(math.ceil(max(0.0, self.music_end_ms) / max(1.0, bar_ms))) + 2
            for i in range(total_bars + 1):
                beat_el = ET.SubElement(beat_root, 'beat')
                self._add_typed_xml(beat_el, 'index', i, 's32')
                self._add_typed_xml(beat_el, 'start_timing_msec', int(round(i * bar_ms)), 's32')

        if self.time_sig_changes:
            ts_root = ET.SubElement(root, 'time_signature_changes')
            for tms, tnum, tden in self.time_sig_changes:
                ch = ET.SubElement(ts_root, 'ts_change')
                self._add_typed_xml(ch, 'start_timing_msec', int(tms), 's32')
                self._add_typed_xml(ch, 'numerator', int(tnum), 's32')
                self._add_typed_xml(ch, 'denominator', int(tden), 's32')

        ET.SubElement(root, 'note_data')
        self.root = root
        self.tree = ET.ElementTree(root)

    def _sync_xml_metadata_for_export(self) -> None:
        self._ensure_xml_tree_for_export()
        assert self.root is not None

        hdr = self.root.find('header')
        if hdr is None:
            hdr = ET.SubElement(self.root, 'header')

        self._upsert_typed_xml(hdr, 'max_scale', 108, 's32')
        self._upsert_typed_xml(hdr, 'min_scale', 21, 's32')
        self._upsert_typed_xml(hdr, 'file_version', 1, 's16')
        self._upsert_typed_xml(hdr, 'first_bpm', bpm_to_xml_value(self.bpm), 's64')
        self._upsert_typed_xml(hdr, 'music_finish_time_msec', int(round(self.music_end_ms)), 's32')
        self._upsert_typed_xml(hdr, 'time_signature_numerator', int(self.beats_per_bar), 's32')
        self._upsert_typed_xml(hdr, 'time_signature_denominator', int(self.time_sig_denominator), 's32')
        self._upsert_typed_xml(
            hdr,
            'time_signature',
            f'{int(self.beats_per_bar)}/{int(self.time_sig_denominator)}',
            'str',
        )
        if abs(float(self.beat_offset_ms)) > 1e-6:
            self._upsert_typed_xml(hdr, 'beat_offset_ms', int(round(self.beat_offset_ms)), 's32')
        else:
            beat_offset_el = hdr.find('beat_offset_ms')
            if beat_offset_el is not None:
                hdr.remove(beat_offset_el)

        ts_root = self.root.find('time_signature_changes')
        if self.time_sig_changes:
            if ts_root is None:
                ts_root = ET.SubElement(self.root, 'time_signature_changes')
            else:
                for child in list(ts_root):
                    ts_root.remove(child)
            for tms, tnum, tden in self.time_sig_changes:
                ch = ET.SubElement(ts_root, 'ts_change')
                self._add_typed_xml(ch, 'start_timing_msec', int(tms), 's32')
                self._add_typed_xml(ch, 'numerator', int(tnum), 's32')
                self._add_typed_xml(ch, 'denominator', int(tden), 's32')
        elif ts_root is not None:
            self.root.remove(ts_root)

    @staticmethod
    def _lane_ranges_overlap(
        min_key: int,
        max_key: int,
        used_ranges: List[Tuple[int, int]],
    ) -> bool:
        for used_min, used_max in used_ranges:
            if min_key <= used_max and max_key >= used_min:
                return True
        return False

    @staticmethod
    def _find_available_lane_range(
        preferred_center: int,
        used_ranges: List[Tuple[int, int]],
    ) -> Tuple[int, int]:
        visited_centers: set[int] = set()
        for delta in range(TOTAL_GAME_KEYS):
            if delta == 0:
                candidates = (preferred_center,)
            else:
                candidates = (preferred_center - delta, preferred_center + delta)
            for center in candidates:
                if center in visited_centers:
                    continue
                visited_centers.add(center)
                if center < 0 or center >= TOTAL_GAME_KEYS:
                    continue
                min_candidate, max_candidate = lane_center_to_width3_range(center)
                if not NoteModel._lane_ranges_overlap(min_candidate, max_candidate, used_ranges):
                    return min_candidate, max_candidate
        fallback_center = max(1, min(TOTAL_GAME_KEYS - 2, preferred_center))
        return lane_center_to_width3_range(fallback_center)

    def _build_midi_restored_notes(
        self,
        notes: List[GNote],
    ) -> List[Tuple[GNote, int]]:
        restored: List[GNote] = [src.clone(src.idx) for src in notes]
        groups: Dict[int, List[GNote]] = {}
        for note in restored:
            groups.setdefault(int(note.start), []).append(note)

        for same_time_notes in groups.values():
            pitches: List[int] = []
            for note in same_time_notes:
                if note.pitch is None:
                    continue
                pitches.append(
                    official_piano_index_to_midi(note.pitch)
                    if note.pitch < MIDI_PIANO_MIN else int(note.pitch)
                )

            if not pitches:
                continue

            pitches.sort(reverse=True)
            target_notes = sorted(
                same_time_notes,
                key=lambda note: (
                    (int(note.min_key) + int(note.max_key)) / 2.0,
                    int(note.max_key),
                    -int(note.idx),
                ),
                reverse=True,
            )
            for note, pitch in zip(target_notes, pitches):
                note.pitch = pitch

        return [
            (
                note,
                key_kind_from_lane_index((int(note.min_key) + int(note.max_key)) // 2),
            )
            for note in restored
        ]

    def apply_midi_pitches_from_source_notes(
        self,
        source_notes: List[GNote],
        time_tolerance_ms: int = 2,
        apply_hand: bool = True,
    ) -> Dict[str, int]:
        """把來源 MIDI 的音高（預設連同左右手）依時間比對後套回目前譜面。

        `apply_hand=False` 用在「這份譜只是缺音高」的情境：左右手是排譜時的人工
        決定，音高補回來不代表要把手部分配也一起換掉。
        """
        tolerance = max(0, int(time_tolerance_ms))

        current_groups: Dict[int, List[GNote]] = {}
        for note in self.notes_tree:
            current_groups.setdefault(int(note.start), []).append(note)

        raw_source_groups: Dict[int, List[Tuple[int, int, int, float]]] = {}
        for note in source_notes:
            if note.pitch is None:
                continue
            pitch = (
                official_piano_index_to_midi(note.pitch)
                if int(note.pitch) < MIDI_PIANO_MIN else int(note.pitch)
            )
            hand = 1 if int(getattr(note, 'hand', 0)) else 0
            source_center = (int(note.min_key) + int(note.max_key)) / 2.0
            raw_source_groups.setdefault(int(note.start), []).append(
                (int(pitch), hand, int(note.start), source_center)
            )

        # JSON overlap cleanup may split one MIDI chord across adjacent integer
        # milliseconds (for example 91800 and 91801). Merge only these tiny
        # neighbours before matching, so one source chord can restore every
        # member without shifting all later groups by one position.
        def cluster_groups(groups):
            clustered = []
            for start_ms in sorted(groups):
                entries = list(groups[start_ms])
                if clustered and int(start_ms) - clustered[-1][1] <= tolerance:
                    clustered[-1][1] = int(start_ms)
                    clustered[-1][2].extend(entries)
                else:
                    clustered.append([int(start_ms), int(start_ms), entries])
            return clustered

        target_clusters = cluster_groups(current_groups)
        source_clusters = cluster_groups(raw_source_groups)

        matched_groups = 0
        exact_groups = 0
        matched_notes = 0
        partial_groups = 0

        target_idx = 0
        source_idx = 0
        while target_idx < len(target_clusters) and source_idx < len(source_clusters):
            target_start, _target_end, target_entries = target_clusters[target_idx]
            source_start, _source_end, source_entries = source_clusters[source_idx]
            delta = int(source_start) - int(target_start)

            if abs(delta) > tolerance:
                # Never fall back to the next sequential group. A missing group
                # must not corrupt all exact matches that follow it.
                if delta < 0:
                    source_idx += 1
                else:
                    target_idx += 1
                continue

            # Some overlap-cleaned charts redistribute a chord across two
            # neighbouring beat groups (e.g. target counts 1+3 versus MIDI
            # counts 2+2). Grow only this local block until its note totals
            # balance; this restores every note without shifting later music.
            block_targets = list(target_entries)
            block_sources = list(source_entries)
            target_clusters_used = 1
            source_clusters_used = 1
            block_first_ms = min(int(target_start), int(source_start))
            rebalance_window_ms = max(250, tolerance * 8)
            while len(block_targets) != len(block_sources):
                if len(block_targets) < len(block_sources):
                    next_idx = target_idx + target_clusters_used
                    if next_idx >= len(target_clusters):
                        break
                    next_start = int(target_clusters[next_idx][0])
                    if next_start - block_first_ms > rebalance_window_ms:
                        break
                    block_targets.extend(target_clusters[next_idx][2])
                    target_clusters_used += 1
                else:
                    next_idx = source_idx + source_clusters_used
                    if next_idx >= len(source_clusters):
                        break
                    next_start = int(source_clusters[next_idx][0])
                    if next_start - block_first_ms > rebalance_window_ms:
                        break
                    block_sources.extend(source_clusters[next_idx][2])
                    source_clusters_used += 1

            target_notes = sorted(
                block_targets,
                key=lambda note: (
                    int(note.start),
                    -((int(note.min_key) + int(note.max_key)) / 2.0),
                    -int(note.max_key),
                    int(note.idx),
                ),
            )
            balanced_block = len(block_targets) == len(block_sources)
            rebalanced = target_clusters_used > 1 or source_clusters_used > 1
            assignments = []
            if balanced_block and rebalanced:
                # Within a repaired local block, choose the closest source by
                # both time and the MIDI-derived 28-key lane. This keeps exact
                # chord members together while assigning any displaced member
                # to the neighbouring target that actually occupies its lane.
                remaining_sources = list(block_sources)
                for note in target_notes:
                    target_center = (int(note.min_key) + int(note.max_key)) / 2.0
                    best_idx = min(
                        range(len(remaining_sources)),
                        key=lambda idx: (
                            abs(int(remaining_sources[idx][2]) - int(note.start)) * 0.1
                            + abs(float(remaining_sources[idx][3]) - target_center),
                            abs(int(remaining_sources[idx][2]) - int(note.start)),
                            abs(float(remaining_sources[idx][3]) - target_center),
                            -int(remaining_sources[idx][0]),
                        ),
                    )
                    assignments.append((note, remaining_sources.pop(best_idx)))
            else:
                ranked_targets = sorted(
                    block_targets,
                    key=lambda note: (
                        (int(note.min_key) + int(note.max_key)) / 2.0,
                        int(note.max_key),
                        -int(note.idx),
                    ),
                    reverse=True,
                )
                ranked_sources = sorted(block_sources, key=lambda item: item[0], reverse=True)
                assignments = list(zip(ranked_targets, ranked_sources))

            target_starts = {int(target_clusters[target_idx + i][0]) for i in range(target_clusters_used)}
            source_starts = {int(source_clusters[source_idx + i][0]) for i in range(source_clusters_used)}
            exact_groups += len(target_starts & source_starts)
            applied = 0
            for note, source_entry in assignments:
                pitch, hand = source_entry[0], source_entry[1]
                note.pitch = int(pitch)
                if apply_hand:
                    note.hand = int(hand)
                applied += 1
            if applied:
                matched_groups += min(target_clusters_used, source_clusters_used)
                matched_notes += applied
                if not balanced_block:
                    partial_groups += 1

            target_idx += target_clusters_used
            source_idx += source_clusters_used

        return {
            'matched_notes': int(matched_notes),
            'matched_groups': int(matched_groups),
            'exact_groups': int(exact_groups),
            'partial_groups': int(partial_groups),
            'target_groups': int(len(target_clusters)),
            'source_groups': int(len(source_clusters)),
        }

    @staticmethod
    def _as_midi_pitch(pitch: Any) -> Optional[int]:
        """把音高正規化成 MIDI 編號，容忍 1..88 的官方鋼琴索引。"""
        if pitch is None:
            return None
        value = int(pitch)
        return official_piano_index_to_midi(value) if value < MIDI_PIANO_MIN else value

    def apply_midi_expression_from_source(
        self,
        source_notes: List[GNote],
        source_pedal_spans: Optional[Sequence[Sequence[float]]] = None,
        time_tolerance_ms: int = 10,
        restore_velocity: bool = True,
        restore_pedal: bool = True,
        exact_pitch_only: bool = False,
    ) -> Dict[str, int]:
        """拿原始 MIDI 把**表情資料**（力度＋延音踏板）補回目前譜面。

        音高、鍵道、左右手、音符類型全部不動——排譜成果是人工調過的，這裡
        只補當初轉譜時被丟掉的東西。

        力度用**起音時間＋音高**配對，不用 `apply_midi_pitches_from_source_notes`
        那套鍵道名次比對。力度本來就屬於「某個音高」，而鍵道是排譜的產物：
        來源 MIDI 一旦排過譜，鍵道和每個時間點的音符數都會變，名次比對的
        游標就會錯開，後面整片配不到（syuten 實測只剩 280/1667）。改用音高
        當識別碼之後，數量對不上、鍵道被手動改過都不影響。

        踏板則完全不需要配對：CC64 是時間軸事件，不綁任何音符，整份覆蓋。
        來源沒有 CC64 時**不動**現有踏板——寧可什麼都不做，也不要把手畫的
        踏板無聲清掉。回傳的 `pedal_source` 是 0 就代表這件事。

        `exact_pitch_only=True` 關掉「取最接近音高」那一輪。手動刪過音符的譜面
        要用這個：被刪掉的音在來源裡還在，剩下的音會被那一輪配到隔壁的力度，
        而那是憑空捏造的。寧可讓配不到的音維持原樣，也不要寫入猜出來的值。
        """
        tolerance = max(0, int(time_tolerance_ms))
        stats: Dict[str, int] = {
            'total_notes': len(self.notes_tree),
            'matched_notes': 0,
            'matched_exact': 0,
            'matched_nearest': 0,
            'pitch_offset': 0,
            'alignment': 'none',
            'velocity_applied': 0,
            'pedal_before': len(self.pedal_spans),
            'pedal_source': 0,
            'pedal_after': len(self.pedal_spans),
            'pedal_shift_ms': 0,
        }

        # 群組對位是力度和踏板共用的：踏板的時間戳也長在來源那條時間軸上。
        source_groups: Dict[int, List[GNote]] = {}
        for note in source_notes:
            if self._as_midi_pitch(getattr(note, 'pitch', None)) is None:
                continue
            source_groups.setdefault(int(note.start), []).append(note)
        source_times = sorted(source_groups)

        chart_groups: Dict[int, List[GNote]] = {}
        for note in self.notes_tree:
            chart_groups.setdefault(int(note.start), []).append(note)
        chart_times = sorted(chart_groups)

        pairs, offset, alignment = self._choose_pairing(
            chart_groups, source_groups, chart_times, source_times, tolerance)
        stats['pitch_offset'] = offset
        stats['alignment'] = alignment

        if restore_velocity:
            for when, nearest in pairs:
                # 只有帶力度的來源音符能用來抄，但對位本身用的是全部音符。
                pool = [n for n in source_groups[nearest]
                        if getattr(n, 'velocity', None) is not None]

                # 第一輪：音高完全相同的直接配掉。和弦裡每顆音各拿自己的力度。
                leftovers: List[GNote] = []
                for note in chart_groups[when]:
                    pitch = self._as_midi_pitch(getattr(note, 'pitch', None))
                    hit = None
                    if pitch is not None:
                        wanted = pitch + offset
                        for candidate in pool:
                            if self._as_midi_pitch(candidate.pitch) == wanted:
                                hit = candidate
                                break
                    if hit is None:
                        leftovers.append(note)
                        continue
                    pool.remove(hit)
                    stats['matched_exact'] += 1
                    self._copy_expression(note, hit, stats)

                # 第二輪：音高被改過（或譜面沒有音高）的，在同一個起音點裡
                # 取還沒被用掉的最接近音高。力度配錯一點無傷，配不到才可惜。
                if exact_pitch_only:
                    continue
                for note in leftovers:
                    if not pool:
                        break
                    pitch = self._as_midi_pitch(getattr(note, 'pitch', None))
                    if pitch is None:
                        hit = pool[0]
                    else:
                        wanted = pitch + offset
                        hit = min(pool, key=lambda c: abs(self._as_midi_pitch(c.pitch) - wanted))
                    pool.remove(hit)
                    stats['matched_nearest'] += 1
                    self._copy_expression(note, hit, stats)

            stats['matched_notes'] = stats['matched_exact'] + stats['matched_nearest']

        spans = [list(span) for span in (source_pedal_spans or ())]
        stats['pedal_source'] = len(spans)
        if restore_pedal and spans:
            # 踏板的毫秒是用來源 MIDI 的節奏圖算出來的。譜面若是用固定 BPM
            # 建的（syuten 就是：來源有 35 個變速事件，譜面是固定 86 BPM），
            # 兩條時間軸最多差 362ms，直接寫進去踏板就會踩在錯的地方——實測
            # 踏下點離譜面音符中位 31ms、最壞 573ms，離來源音符卻只有 3ms。
            # 對位已經給了成對的時間點，拿它做分段線性內插換算過去。
            remapped, shift = self._remap_times_to_chart(spans, pairs)
            stats['pedal_shift_ms'] = shift
            self.pedal_spans = self._normalise_pedal_spans(remapped)
        stats['pedal_after'] = len(self.pedal_spans)

        if stats['velocity_applied'] or stats['pedal_after'] != stats['pedal_before']:
            self.dirty = True

        logging.info(
            'expression restore: velocity %d applied, matched %d/%d (%d exact, %d nearest), '
            'alignment=%s offset=%+d, pedal %d -> %d (source %d, shifted up to %d ms)',
            stats['velocity_applied'], stats['matched_notes'], stats['total_notes'],
            stats['matched_exact'], stats['matched_nearest'],
            stats['alignment'], stats['pitch_offset'],
            stats['pedal_before'], stats['pedal_after'], stats['pedal_source'],
            stats['pedal_shift_ms'],
        )
        return stats

    @staticmethod
    def _build_time_map(pairs: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """把成對的群組時間整理成遞增的 (來源時間 → 譜面時間) 錨點。

        兩邊都必須嚴格遞增，內插才有意義。依時間對位時可能有好幾個譜面群組
        指到同一個來源群組，這種重複與倒退一律丟掉。
        """
        anchors: List[Tuple[int, int]] = []
        for source_ms, chart_ms in sorted((int(s), int(c)) for c, s in pairs):
            if anchors and (source_ms <= anchors[-1][0] or chart_ms <= anchors[-1][1]):
                continue
            anchors.append((source_ms, chart_ms))
        return anchors

    def _remap_times_to_chart(
        self,
        spans: Sequence[Sequence[float]],
        pairs: Sequence[Tuple[int, int]],
    ) -> Tuple[List[List[float]], int]:
        """把來源時間軸上的區間換算到譜面時間軸，回傳 (區間, 最大位移 ms)。

        錨點之間線性內插，頭尾之外沿用最近那個錨點的位移量。沒有足夠錨點
        （例如對位整個被否決）時原封不動回傳。
        """
        out = [[float(span[0]), float(span[1])] for span in spans]
        anchors = self._build_time_map(pairs)
        if len(anchors) < 2:
            return (out, 0)

        sources = [a[0] for a in anchors]

        def convert(value: float) -> float:
            if value <= sources[0]:
                return value + (anchors[0][1] - anchors[0][0])
            if value >= sources[-1]:
                return value + (anchors[-1][1] - anchors[-1][0])
            i = bisect.bisect_right(sources, value) - 1
            s0, c0 = anchors[i]
            s1, c1 = anchors[i + 1]
            if s1 == s0:
                return float(c0)
            return c0 + (c1 - c0) * (value - s0) / (s1 - s0)

        shift = 0.0
        remapped: List[List[float]] = []
        for start, end in out:
            new_start, new_end = convert(start), convert(end)
            shift = max(shift, abs(new_start - start), abs(new_end - end))
            remapped.append([new_start, new_end])
        return (remapped, int(round(shift)))

    @staticmethod
    def _nearest_time(times: List[int], when: int, tolerance: int) -> Optional[int]:
        """回傳 times 裡離 when 最近、且在容許誤差內的時間。"""
        if not times:
            return None
        index = bisect.bisect_left(times, when)
        best = None
        for candidate in times[max(0, index - 1):index + 2]:
            if best is None or abs(candidate - when) < abs(best - when):
                best = candidate
        if best is None or abs(best - when) > tolerance:
            return None
        return best

    # 音高吻合率低於這個比例就當成「選錯 MIDI」，整個放棄對位。
    MIN_ALIGNMENT_HIT_RATIO = 0.25

    def _choose_pairing(
        self,
        chart_groups: Dict[int, List[GNote]],
        source_groups: Dict[int, List[GNote]],
        chart_times: List[int],
        source_times: List[int],
        tolerance: int,
    ) -> Tuple[List[Tuple[int, int]], int, str]:
        """決定譜面的起音群組要對到來源的哪一個群組。

        預設當然是比對絕對時間，但那有個大前提：兩邊的時間軸要一致。實測
        syuten 就不是——它的來源 MIDI 有 35 個變速事件（BPM 30~93），而譜面
        是用固定 86 BPM 建的，所以絕對毫秒必然對不起來（863 個群組只有 165
        個吻合）。可是兩邊的**音符順序**完全一樣，依「第幾個群組」對位反而
        是 863/863 全中。

        所以兩種對位都試，用「音高完全相同的音符數」評分，取高的那個。用
        exact 命中數當分數是因為對位一旦錯位，這個數字會立刻垮掉，不會像
        「最接近音高」那樣不管怎麼配都給得出答案。依序對位另外試幾個起始
        位移，容忍來源頭尾多出幾個群組。
        """
        candidates: List[Tuple[str, List[Tuple[int, int]]]] = []

        by_time = [(when, self._nearest_time(source_times, when, tolerance))
                   for when in chart_times]
        candidates.append(
            ('time', [(a, b) for a, b in by_time if b is not None]))

        for shift in (0, -2, -1, 1, 2):
            if shift >= 0:
                pairs = list(zip(chart_times, source_times[shift:]))
            else:
                pairs = list(zip(chart_times[-shift:], source_times))
            if pairs:
                candidates.append(('order' if shift == 0 else 'order%+d' % shift, pairs))

        best: Optional[Tuple[int, List[Tuple[int, int]], int, str]] = None
        for kind, pairs in candidates:
            if not pairs:
                continue
            offset = self._estimate_pitch_offset(chart_groups, source_groups, pairs)
            score = self._score_pairing(chart_groups, source_groups, pairs, offset)
            if best is None or score > best[0]:
                best = (score, pairs, offset, kind)

        if best is None:
            return ([], 0, 'none')

        # 依序對位一定湊得出答案，所以「取分數最高的」還不夠——選錯 MIDI 時
        # 最高分也只是矮子裡拔將軍。這裡再設一道地板：音高完全吻合的比例太
        # 低就整個放棄，寧可回報 0 顆讓使用者看到選錯了，也不要抄一整份錯的
        # 力度進去。實測對得上的譜面都在 98% 以上，門檻拉在 25% 很安全。
        scorable = sum(
            1 for notes in chart_groups.values() for note in notes
            if self._as_midi_pitch(getattr(note, 'pitch', None)) is not None
        )
        if scorable and best[0] < scorable * self.MIN_ALIGNMENT_HIT_RATIO:
            return ([], 0, 'none')
        return (best[1], best[2], best[3])

    def _score_pairing(
        self,
        chart_groups: Dict[int, List[GNote]],
        source_groups: Dict[int, List[GNote]],
        pairs: Sequence[Tuple[int, int]],
        offset: int,
    ) -> int:
        """這組對位底下，有多少譜面音符能找到音高完全相同的來源音符。"""
        hits = 0
        for when, other in pairs:
            pool: Dict[int, int] = {}
            for note in source_groups[other]:
                pitch = self._as_midi_pitch(note.pitch)
                pool[pitch] = pool.get(pitch, 0) + 1
            for note in chart_groups[when]:
                pitch = self._as_midi_pitch(getattr(note, 'pitch', None))
                if pitch is None:
                    continue
                wanted = pitch + offset
                if pool.get(wanted, 0) > 0:
                    pool[wanted] -= 1
                    hits += 1
        return hits

    def _estimate_pitch_offset(
        self,
        chart_groups: Dict[int, List[GNote]],
        source_groups: Dict[int, List[GNote]],
        pairs: Sequence[Tuple[int, int]],
    ) -> int:
        """量出譜面與來源之間的固定音高位移（半音）。

        譜面的 `scale_piano` 有好幾套八度基準，不同轉檔工具寫出來的差一個
        固定量——實測 bad-apple 的譜面比它自己的來源 MIDI 高 20 個半音，
        syuten 則是 0。不先扣掉這個位移，音高就完全對不起來，只能退回
        「取最接近」，一旦來源有多餘的音就會抄錯力度。

        做法是把每個共同起音點的兩邊音高各自排序後依名次相減，取眾數。
        主導率太低代表根本不是同一首曲子的 MIDI，這時回傳 0 不做位移。
        """
        deltas: Dict[int, int] = {}
        for when, other in pairs:
            chart_pitches = sorted(
                p for p in (self._as_midi_pitch(getattr(n, 'pitch', None))
                            for n in chart_groups[when]) if p is not None
            )
            source_pitches = sorted(
                self._as_midi_pitch(n.pitch) for n in source_groups[other]
            )
            for chart_pitch, source_pitch in zip(chart_pitches, source_pitches):
                delta = source_pitch - chart_pitch
                deltas[delta] = deltas.get(delta, 0) + 1

        if not deltas:
            return 0
        total = sum(deltas.values())
        # 平手時偏好 0：沒有明確證據就不要動音高。
        best = max(deltas.items(), key=lambda kv: (kv[1], kv[0] == 0))
        return best[0] if best[1] * 2 >= total else 0

    @staticmethod
    def _copy_expression(target: GNote, source: GNote, stats: Dict[str, int]) -> None:
        """把一顆來源音符的力度抄到譜面音符上。"""
        velocity = int(source.velocity)
        if target.velocity != velocity:
            stats['velocity_applied'] += 1
        target.velocity = velocity
        # off_velocity 一起帶過去，否則存檔時 sub_note 會留著舊譜的放鍵
        # 力度，跟新的按鍵力度對不起來。
        if getattr(source, 'off_velocity', None) is not None:
            target.off_velocity = int(source.off_velocity)

    @staticmethod
    def _pedal_spans_for_file(spans) -> List[Tuple[int, int]]:
        """存檔用的整數毫秒踏板區間：**起點無條件進位、終點無條件捨去**。

        直接四捨五入會出事：鋼琴家快速換踏時兩段之間只差 1.4ms，四捨五入之後
        間隔變成 1ms 以內，讀檔時 `_normalise_pedal_spans` 就把它們併成一段——
        踏板明明抬起來過（那一抬正是為了把延音清掉），合併之後整段糊在一起。
        實測 hanei 那份 76 段會被併成 31 段。

        往內縮的話間隔只會變大不會變小，真正的抬起一定活得下來；每段最多短
        1ms，聽不出來。值本來就是整數時進位/捨去都是原值，反覆存讀不會漂移。
        """
        import math

        out: List[Tuple[int, int]] = []
        for span in spans or ():
            start = int(math.ceil(float(span[0])))
            end = int(math.floor(float(span[1])))
            if end > start:
                out.append((start, end))
        return out

    def _write_dynamics_data_for_export(self) -> None:
        """把強弱記號寫成 <dynamics_data> 區段（和 pedal_data 一樣是額外的兄弟節點）。

        沒有記號時整段移除；遊戲端用名字取節點，不認得的節點碰都不會碰到。
        """
        if self.root is None:
            return
        old = self.root.find('dynamics_data')
        if old is not None:
            self.root.remove(old)
        live = {h: marks for h, marks in self.dynamics.items() if marks}
        if not live:
            return
        section = ET.SubElement(self.root, 'dynamics_data')
        index = 0
        for hand in sorted(live):
            for ms, level, ramp in live[hand]:
                item = ET.SubElement(section, 'dynamic')
                for tag, val in (('index', index),
                                 ('hand', int(hand)),
                                 ('start_timing_msec', int(round(ms))),
                                 ('level', int(round(level))),
                                 ('ramp', 1 if ramp else 0)):
                    el = ET.SubElement(item, tag)
                    el.text = str(val)
                    el.set('__type', 's32')
                index += 1

    def _read_dynamics_data_from_xml(self) -> None:
        self.dynamics = {}
        if self.root is None:
            return
        section = self.root.find('dynamics_data')
        if section is None:
            return
        buckets: Dict[int, List[List[float]]] = {}
        for item in section:
            try:
                hand = int(float(item.findtext('hand')))
                ms = float(item.findtext('start_timing_msec'))
                level = float(item.findtext('level'))
                ramp = bool(int(float(item.findtext('ramp') or 0)))
            except (TypeError, ValueError):
                continue
            buckets.setdefault(hand, []).append([ms, level, ramp])
        self.dynamics = {h: self._normalise_dynamics(v) for h, v in buckets.items()}

    def _write_pedal_data_for_export(self) -> None:
        """把 pedal_spans 寫成 <pedal_data> 區段（root 底下，note_data 的兄弟）。

        官方格式沒有踏板，但根節點本來就分成 header / note_data / event_data /
        beat_data / track_info / velocity_zone_data 好幾段，多一段是這個格式
        自己的形狀；遊戲端一律用名字取節點（`root.Element("note_data")`），
        不認得的兄弟節點碰都不會碰到。

        欄位排法直接照抄官方的 velocity_zone —— index / start / end。

        沒有踏板時整段移除，既有譜面存檔不會平白多出一個節點。
        """
        if self.root is None:
            return
        old = self.root.find('pedal_data')
        if old is not None:
            self.root.remove(old)
        if not self.pedal_spans:
            return
        section = ET.SubElement(self.root, 'pedal_data')
        for i, (start_ms, end_ms) in enumerate(self._pedal_spans_for_file(self.pedal_spans)):
            item = ET.SubElement(section, 'pedal')
            for tag, val in (('index', i),
                             ('start_timing_msec', start_ms),
                             ('end_timing_msec', end_ms)):
                el = ET.SubElement(item, tag)
                el.text = str(val)
                el.set('__type', 's32')

    def _read_pedal_data_from_xml(self) -> None:
        self.pedal_spans = []
        if self.root is None:
            return
        section = self.root.find('pedal_data')
        if section is None:
            return
        spans = []
        for item in section:
            try:
                spans.append([float(item.findtext('start_timing_msec')),
                              float(item.findtext('end_timing_msec'))])
            except (TypeError, ValueError):
                continue
        self.pedal_spans = self._normalise_pedal_spans(spans)

    def save_xml(self, path: Optional[str] = None, use_midi_restore: bool = False) -> None:
        if use_midi_restore:
            self.save_xml_with_midi_restore(path)
            return
        if path is None:
            path = self.current_file
        if path is None:
            raise ValueError('未指定存檔路徑')
        self._ensure_xml_tree_for_export()
        self._sync_xml_metadata_for_export()
        assert self.root is not None and self.tree is not None

        # ── 永遠從 notes_tree 重建 note_data ────────────────────────
        # （可正確處理刪除、新增、MIDI 匯入後的儲存）
        nd = self.root.find('note_data')
        if nd is None:
            nd = ET.SubElement(self.root, 'note_data')
        else:
            for child in list(nd):
                nd.remove(child)

        # slide（note_type=4）鏈結修復：param1/param2 參照的是音符原始 <index>，
        # 但下方會把 index 依序重新編號，因此先建立「舊 index → 新序號」對照表，
        # 將 slide 音符的 param1/param2 重寫成新序號，避免存檔後鏈結錯位。
        old_to_new: Dict[int, int] = {}
        for i, n in enumerate(self.notes_tree):
            if getattr(n, 'note_index', None) is not None:
                old_to_new[int(n.note_index)] = i
        for n in self.notes_tree:
            if int(getattr(n, 'note_type', 0)) != 4:
                continue
            for attr in ('param1', 'param2'):
                v = getattr(n, attr, -1)
                if v is not None and int(v) >= 0 and int(v) in old_to_new:
                    setattr(n, attr, old_to_new[int(v)])

        # Preserve the current note_data order for regular NOS XML saves.
        # Re-sorting here can reshuffle nearby notes after reload and make
        # left/right hand labels appear to jump to a different note.
        # 隱藏音符不獨立寫出——它們的音要併回寄主的 sub_note_data。
        self._write_velocity_into_subs()
        merged = self._merge_hidden_into_hosts()
        for i, n in enumerate(self.notes_tree):
            if getattr(n, 'hidden', False):
                continue
            if n.elem is not None:
                # 更新既有 XML 元素後重新掛入
                n.apply_back(EXTERNAL_LANE_BASE)
                n.elem.set('index', str(i))
                idx_child = n.elem.find('index')
                if idx_child is not None:
                    idx_child.text = str(i)
                nd.append(n.elem)
            else:
                nd.append(self._build_note_element(n, i, EXTERNAL_LANE_BASE))
            # 新序號成為此音符的新原始 index，供下一次存檔對照
            n.note_index = i

        self._ensure_event_data_for_export()
        self._write_pedal_data_for_export()
        self._write_dynamics_data_for_export()
        raw = ET.tostring(self.root, encoding='unicode')
        pretty_bytes = xml.dom.minidom.parseString(raw).toprettyxml(indent='  ', encoding='utf-8')
        lines = [l for l in pretty_bytes.decode('utf-8').splitlines() if l.strip()]
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

        self.file_format = 'xml'
        self.current_file = path
        self.dirty = False

    def save_xml_with_midi_restore(self, path: Optional[str] = None) -> None:
        if path is None:
            path = self.current_file
        if path is None:
            raise ValueError('No save path specified.')
        self._ensure_xml_tree_for_export()
        self._sync_xml_metadata_for_export()
        assert self.root is not None and self.tree is not None

        nd = self.root.find('note_data')
        if nd is None:
            nd = ET.SubElement(self.root, 'note_data')
        else:
            for child in list(nd):
                nd.remove(child)

        ordered_notes = list(self.notes_tree)
        for i, (n, key_kind) in enumerate(self._build_midi_restored_notes(ordered_notes)):
            nd.append(self._build_note_element(n, i, EXTERNAL_LANE_BASE, key_kind=key_kind))

        self._ensure_event_data_for_export()
        self._write_pedal_data_for_export()
        self._write_dynamics_data_for_export()
        raw = ET.tostring(self.root, encoding='unicode')
        pretty_bytes = xml.dom.minidom.parseString(raw).toprettyxml(indent='  ', encoding='utf-8')
        lines = [l for l in pretty_bytes.decode('utf-8').splitlines() if l.strip()]
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

        self.file_format = 'xml'
        self.current_file = path
        self.dirty = False

    def save_json(self, path: Optional[str] = None) -> None:
        if path is None:
            path = self.current_file
        if path is None:
            raise ValueError('未指定存檔路徑')

        meta = dict(self.json_meta)
        meta['bpm']                    = self.bpm
        meta['first_bpm']              = self.bpm
        meta.pop('lane_index_base', None)
        meta['music_finish_time_msec'] = int(self.music_end_ms)
        # 明確記下 notes[].pitch 用的是哪一套編號。舊檔沒有這個欄位，載入時
        # 只能靠值域猜——而猜錯的代價很大：把已經是 MIDI 的值再 +20 然後夾在
        # 108，會讓所有 >=88 的音符全部塌成同一個 108（實測「彩云追月」有
        # 16.2% 的音符變成 108，散在 9 個鍵道上）。
        meta['pitch_encoding'] = 'midi'      # 21..108；另一種是 'scale_piano'(1..88)
        ordered = sorted(self.notes_tree, key=lambda n: (n.start, n.min_key))
        meta['notes'] = [n.to_json_dict() for n in ordered]
        # 隱藏音符的「寄主是誰」也要寫進 JSON。以前只有 XML 拆 sub_note 時會
        # 在記憶體裡記 `_sub_host`，JSON 沒有這個資訊——於是 XML→JSON→XML
        # 走一圈之後，那些和寄主時間差超過 120ms 的隱藏音符（官方的 sub_note
        # 自帶時間，差很多是常態）就找不到寄主，被當成孤兒取消隱藏。實測
        # chopin_nocturn2 的 normal 譜 321 顆隱藏音符會掉成 240 顆。
        position = {id(n): i for i, n in enumerate(ordered)}
        for note, host in self.resolve_hidden_hosts():
            index = position.get(id(note))
            if index is None or host is None:
                continue
            target = position.get(id(host))
            if target is not None:
                meta['notes'][index]['hostIndex'] = int(target)

        # time_signature string (e.g. "4/4") + numerator / denominator
        _fnum = self.beats_per_bar
        _fden = self.time_sig_denominator
        if self.time_sig_changes:
            _, _fnum, _fden = self.time_sig_changes[0]
        meta['time_signature']             = f'{_fnum}/{_fden}'
        meta['time_signature_numerator']   = _fnum
        meta['time_signature_denominator'] = _fden

        # beat_timings：從 XML beat_data 讀取，去重後排序
        entries = self.get_beat_entries()
        if entries:
            meta['beat_timings'] = [int(ms) for (_, ms) in entries]
            meta['beat_indices'] = [int(round(idx)) for (idx, _) in entries]
        else:
            meta.pop('beat_timings', None)
            meta.pop('beat_indices', None)

        # time_signature_changes
        if 'time_signature_changes' not in meta and self.time_sig_changes:
            meta['time_signature_changes'] = [
                {'time_ms': ms, 'numerator': num, 'denominator': den}
                for ms, num, den in self.time_sig_changes
            ]

        # 延音踏板。沒有踏板時整個欄位不寫出去——現有譜面存檔後不會多出
        # 任何鍵，diff 乾淨，舊版讀取器也完全不受影響。
        if self.pedal_spans:
            meta['pedal_data'] = [
                {'start_ms': lo, 'end_ms': hi}
                for lo, hi in self._pedal_spans_for_file(self.pedal_spans)
            ]
        else:
            meta.pop('pedal_data', None)

        # 強弱記號。同樣沒有就不寫欄位。
        live_dynamics = {h: marks for h, marks in self.dynamics.items() if marks}
        if live_dynamics:
            meta['dynamics_data'] = {
                str(int(hand)): [
                    {'ms': int(round(m[0])), 'level': int(round(m[1])),
                     'ramp': bool(m[2])}
                    for m in marks
                ]
                for hand, marks in sorted(live_dynamics.items())
            }
        else:
            meta.pop('dynamics_data', None)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        self.current_file = path
        self.dirty = False

    # ------------------------------------------------------------------
    # beat_data 存取（供 TimeMapper 使用）
    # ------------------------------------------------------------------

    def _detect_beat_index_scale(self, beats: Optional[List[tuple]] = None) -> float:
        # 熱路徑：_uses_explicit_beat_units 每幀每拍都會呼叫；只依賴 beat 索引，
        # 以簽章快取避免每次 O(N log N) 排序。傳入明確 beats 時直接計算（不快取）。
        if beats is None:
            sig = self._beat_sig()
            if self._cache_scale is not None and self._cache_scale_sig == sig:
                return self._cache_scale
            val = self._compute_beat_index_scale(self.get_beat_entries())
            self._cache_scale = val
            self._cache_scale_sig = sig
            return val
        return self._compute_beat_index_scale(beats)

    def _compute_beat_index_scale(self, beats: List[tuple]) -> float:
        if len(beats) < 2:
            return 1.0
        diffs = sorted(
            abs(float(beats[i + 1][0]) - float(beats[i][0]))
            for i in range(len(beats) - 1)
            if float(beats[i + 1][0]) > float(beats[i][0])
        )
        if not diffs:
            return 1.0
        median = diffs[len(diffs) // 2]
        return median if median >= 8.0 else 1.0

    def _uses_explicit_beat_units(self) -> bool:
        jm = getattr(self, 'json_meta', {}) or {}
        if bool(jm.get('editor_precise_beat_grid', False)):
            return True
        return self._detect_beat_index_scale() >= 8.0

    def _get_beat_unit_entries(self) -> List[Tuple[float, int]]:
        beats = self.get_beat_entries()
        if not beats:
            return []
        scale = self._detect_beat_index_scale(beats)
        entries = [(float(idx) / scale, int(ms)) for idx, ms in beats]
        entries.sort(key=lambda x: x[1])
        return entries

    def _unit_to_ms_from_entries(
        self,
        unit: float,
        unit_entries: Optional[List[Tuple[float, int]]] = None,
    ) -> Optional[float]:
        pts = unit_entries if unit_entries is not None else self._get_beat_unit_entries()
        if not pts:
            return None
        if unit <= pts[0][0]:
            if len(pts) == 1 or pts[1][0] == pts[0][0]:
                return float(pts[0][1])
            slope = (pts[1][1] - pts[0][1]) / (pts[1][0] - pts[0][0])
            return pts[0][1] + (unit - pts[0][0]) * slope
        if unit >= pts[-1][0]:
            if len(pts) == 1 or pts[-1][0] == pts[-2][0]:
                return float(pts[-1][1])
            slope = (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
            return pts[-1][1] + (unit - pts[-1][0]) * slope
        unit_keys = [u for u, _ in pts]
        idx = bisect_right(unit_keys, unit) - 1
        idx = max(0, min(idx, len(pts) - 2))
        u0, ms0 = pts[idx]
        u1, ms1 = pts[idx + 1]
        if u1 == u0:
            return float(ms0)
        return ms0 + (unit - u0) * (ms1 - ms0) / (u1 - u0)

    def _ms_to_unit_from_entries(
        self,
        ms: float,
        unit_entries: Optional[List[Tuple[float, int]]] = None,
    ) -> Optional[float]:
        pts = unit_entries if unit_entries is not None else self._get_beat_unit_entries()
        if not pts:
            return None
        time_pts = [(float(v_ms), unit) for unit, v_ms in pts]
        time_pts.sort(key=lambda x: x[0])
        ms_keys = [v_ms for v_ms, _ in time_pts]
        if ms <= time_pts[0][0]:
            if len(time_pts) == 1 or time_pts[1][0] == time_pts[0][0]:
                return float(time_pts[0][1])
            slope = (time_pts[1][1] - time_pts[0][1]) / (time_pts[1][0] - time_pts[0][0])
            return time_pts[0][1] + (ms - time_pts[0][0]) * slope
        if ms >= time_pts[-1][0]:
            if len(time_pts) == 1 or time_pts[-1][0] == time_pts[-2][0]:
                return float(time_pts[-1][1])
            slope = (time_pts[-1][1] - time_pts[-2][1]) / (time_pts[-1][0] - time_pts[-2][0])
            return time_pts[-1][1] + (ms - time_pts[-1][0]) * slope
        idx = bisect_right(ms_keys, ms) - 1
        idx = max(0, min(idx, len(time_pts) - 2))
        ms0, u0 = time_pts[idx]
        ms1, u1 = time_pts[idx + 1]
        if ms1 == ms0:
            return float(u0)
        return u0 + (ms - ms0) * (u1 - u0) / (ms1 - ms0)

    def _get_precise_measure_boundaries(self) -> List[Tuple[float, int, float, int]]:
        # 小節邊界是播放渲染每幀、每條 beat 線都會查的熱點；以簽章快取。
        # 邊界同時取決於 beat_data 與拍號，故簽章含 time_sig 狀態。
        sig = (self._beat_sig(), tuple(self.time_sig_changes),
               int(self.beats_per_bar), int(self.time_sig_denominator))
        if self._cache_pb is not None and self._cache_pb_sig == sig:
            return self._cache_pb
        result = self._compute_precise_measure_boundaries()
        self._cache_pb = result
        self._cache_pb_sig = sig
        return result

    def _compute_precise_measure_boundaries(self) -> List[Tuple[float, int, float, int]]:
        entries = self._get_beat_unit_entries()
        if len(entries) < 2:
            return []
        bounds: List[Tuple[float, int, float, int]] = []
        cur_unit = float(entries[0][0])
        cur_ms = int(entries[0][1])
        last_unit = float(entries[-1][0])
        guard = 0
        while cur_unit < last_unit - 1e-6 and guard < 100000:
            guard += 1
            beats_in_bar = max(1, self.get_beats_per_bar_at_ms(cur_ms))
            end_unit = cur_unit + float(beats_in_bar)
            end_ms_f = self._unit_to_ms_from_entries(end_unit, entries)
            if end_ms_f is None:
                break
            end_ms = int(round(end_ms_f))
            bounds.append((cur_unit, cur_ms, end_unit, end_ms))
            cur_unit = end_unit
            cur_ms = end_ms
        return bounds

    def _legacy_entries_per_bar(self) -> int:
        # 與 entries_per_bar 同一個（已釘住的）判斷，差別只在不看 explicit beat units。
        bpb = max(1, self.beats_per_bar)
        if bpb <= 1:
            return 1
        if len(self.get_beat_entries()) < 2:
            return 1
        return self._resolved_entries_per_bar()

    def _legacy_get_measure_time_range(self, measure_idx: int) -> Tuple[Optional[int], Optional[int]]:
        beats = self.get_beat_entries()
        if not beats:
            return (None, None)
        epb = self._legacy_entries_per_bar()
        entry_start = measure_idx * epb
        entry_end = entry_start + epb
        if entry_start >= len(beats):
            return (None, None)
        start_ms = beats[entry_start][1]
        if entry_end < len(beats):
            end_ms = beats[entry_end][1]
        else:
            end_ms = start_ms + int(round(self._bar_ms()))
        return (int(start_ms), int(end_ms))

    def _legacy_count_measures(self) -> int:
        epb = max(1, self._legacy_entries_per_bar())
        return len(self.get_beat_entries()) // epb

    def _write_beat_entries(self, beats: List[Tuple[int, int]], mark_precise: bool = False) -> None:
        beats_sorted = sorted(
            [(int(round(idx)), int(round(ms))) for idx, ms in beats],
            key=lambda x: x[1],
        )
        if self.root is not None:
            beat_root = self.root.find('beat_data')
            if beat_root is None:
                beat_root = ET.SubElement(self.root, 'beat_data')
            for child in list(beat_root):
                beat_root.remove(child)
            for idx, ms in beats_sorted:
                beat_el = ET.SubElement(beat_root, 'beat')
                idx_el = ET.SubElement(beat_el, 'index')
                idx_el.set('__type', 's32')
                idx_el.text = str(idx)
                ms_el = ET.SubElement(beat_el, 'start_timing_msec')
                ms_el.set('__type', 's32')
                ms_el.text = str(ms)
        jm = getattr(self, 'json_meta', {}) or {}
        jm['beat_timings'] = [ms for _, ms in beats_sorted]
        jm['beat_indices'] = [idx for idx, _ in beats_sorted]
        if mark_precise:
            jm['editor_precise_beat_grid'] = True
        self.json_meta = jm

    def ensure_precise_beat_grid(self) -> None:
        if self._uses_explicit_beat_units():
            return
        if not self.get_beat_entries():
            return
        total_measures = self._legacy_count_measures()
        if total_measures <= 0:
            return
        new_beats: List[Tuple[int, int]] = []
        cursor_unit = 0.0
        last_end_ms = 0
        for measure_idx in range(total_measures):
            start_ms, end_ms = self._legacy_get_measure_time_range(measure_idx)
            if start_ms is None or end_ms is None:
                continue
            beats_in_bar = max(1, self.get_beats_per_bar_at_ms(start_ms))
            measure_dur = end_ms - start_ms
            for beat_idx in range(beats_in_bar):
                unit = cursor_unit + float(beat_idx)
                beat_ms = start_ms + (measure_dur * beat_idx / float(beats_in_bar))
                new_beats.append(
                    (int(round(unit * EDITOR_BEAT_UNIT_SCALE)), int(round(beat_ms)))
                )
            cursor_unit += float(beats_in_bar)
            last_end_ms = int(end_ms)
        new_beats.append((int(round(cursor_unit * EDITOR_BEAT_UNIT_SCALE)), int(round(last_end_ms))))
        self._write_beat_entries(new_beats, mark_precise=True)

    def set_note_bpm(self, note_start_ms: int, new_bpm: float) -> None:
        self.ensure_precise_beat_grid()
        raw_beats = self.get_beat_entries()
        unit_entries = self._get_beat_unit_entries()
        bounds = self._get_precise_measure_boundaries()
        if len(raw_beats) < 2 or len(unit_entries) < 2 or not bounds:
            return

        measure_idx = self.get_measure_at_ms(note_start_ms)
        if not (0 <= measure_idx < len(bounds)):
            return
        _start_unit, start_ms, end_unit, end_ms = bounds[measure_idx]
        anchor_ms = int(max(start_ms, min(note_start_ms, end_ms)))
        anchor_unit_f = self._ms_to_unit_from_entries(float(anchor_ms), unit_entries)
        if anchor_unit_f is None:
            return
        anchor_unit = float(anchor_unit_f)
        if anchor_unit >= end_unit - 1e-6:
            return

        den = max(1, self.time_sig_denominator)
        for change_ms, _num, ch_den in self.time_sig_changes:
            if change_ms <= start_ms:
                den = max(1, ch_den)
            else:
                break

        remaining_units = max(0.0, end_unit - anchor_unit)
        old_tail_dur = max(1, end_ms - anchor_ms)
        unit_ms = 4.0 * 60000.0 / (den * max(1.0, float(new_bpm)))
        new_tail_dur = remaining_units * unit_ms
        ratio = new_tail_dur / float(old_tail_dur)
        delta = int(round(new_tail_dur - old_tail_dur))

        def _transform_time(value_ms: int) -> int:
            if value_ms < anchor_ms:
                return int(value_ms)
            if value_ms < end_ms:
                return int(round(anchor_ms + (value_ms - anchor_ms) * ratio))
            return int(value_ms + delta)

        for n in self.notes_tree:
            n.start = _transform_time(int(n.start))
            n.end = _transform_time(int(n.end))
            if n.end <= n.start:
                n.end = n.start + 1
            n.gate = max(1, n.end - n.start)

        scale = self._detect_beat_index_scale(raw_beats)
        raw_anchor = int(round(anchor_unit * scale))
        existing_raw = {int(round(idx)) for idx, _ in raw_beats}
        new_beats: List[Tuple[int, int]] = []
        inserted_anchor = raw_anchor in existing_raw
        epsilon = 1e-6

        for (raw_idx, raw_ms), (unit, _ms) in zip(raw_beats, unit_entries):
            if not inserted_anchor and unit > anchor_unit + epsilon:
                new_beats.append((raw_anchor, anchor_ms))
                inserted_anchor = True
            new_beats.append((int(round(raw_idx)), _transform_time(int(raw_ms))))

        if not inserted_anchor:
            new_beats.append((raw_anchor, anchor_ms))

        new_beats.sort(key=lambda x: x[0])
        deduped: List[Tuple[int, int]] = []
        for raw_idx, raw_ms in new_beats:
            if deduped and deduped[-1][0] == raw_idx:
                deduped[-1] = (raw_idx, min(deduped[-1][1], raw_ms))
            else:
                deduped.append((raw_idx, raw_ms))
        self._write_beat_entries(deduped, mark_precise=True)

        self.music_end_ms = max(0.0, self.music_end_ms + delta)
        if self.root is not None:
            fin_el = self.root.find('header/music_finish_time_msec')
            if fin_el is not None:
                fin_el.text = str(int(self.music_end_ms))
        else:
            jm = getattr(self, 'json_meta', {}) or {}
            jm['music_finish_time_msec'] = int(self.music_end_ms)
            self.json_meta = jm

        self.rebuild_display_cache()
        self.dirty = True

    def get_beats_per_bar_at_ms(self, ms: float) -> int:
        """查詢 ms 時刻的每小節拍數（numerator），無變拍號資料時回傳 beats_per_bar。"""
        if not self.time_sig_changes:
            return self.beats_per_bar
        # 預設應該是全域拍號；若 ms 尚未到第一個變拍號，不應提前套用。
        result = self.beats_per_bar
        for change_ms, num, _den in self.time_sig_changes:
            if change_ms <= ms:
                result = num
            else:
                break
        return result

    @property
    def entries_per_bar(self) -> int:
        """beat_data 每小節有幾個 entry。
        - per-bar 格式（create_new 建立）：entry 間距 ≈ 一小節 ms → 回傳 1
        - per-beat 格式（原始遊戲檔）：entry 間距 ≈ 一拍 ms → 回傳 beats_per_bar
        """
        bpb = max(1, self.beats_per_bar)
        if self._uses_explicit_beat_units():
            return bpb
        if bpb <= 1:
            return 1
        return self._resolved_entries_per_bar()

    def _resolved_entries_per_bar(self) -> int:
        """一小節有幾個 beat entry。

        以前只有兩種答案：1（一小節一筆）或 `beats_per_bar`（一拍一筆）。實際
        遇到的檔案不只這兩種——`anima-xi-fullarr-phyxinon.json` 是**半小節一筆**
        （間距 1304ms = 2 拍 @92BPM）。硬套最接近的那一個會選到「一拍一筆」，
        於是每個小節的 BPM 都變成一半（92 → 46），小節數也少一半。
        """
        bpb = max(1, self.beats_per_bar)
        # `_epb_mode` 是既有的釘住旗標，四處都有 `self._epb_mode = None` 當作
        # 「重新判斷」的訊號。數字快取必須跟著同一個訊號失效，否則那些呼叫端
        # 以為自己清掉了、實際還在用舊值。
        if self._epb_mode is None:
            self._epb_count = None
        cached = self._epb_count
        if cached:
            return cached
        measured = self._detect_entries_per_bar()
        jm = getattr(self, 'json_meta', None)
        stored = None
        if isinstance(jm, dict):
            raw = jm.get('editor_entries_per_bar')
            if isinstance(raw, int) and 1 <= raw <= bpb:
                stored = raw
            elif jm.get('editor_beat_entry_mode') == 'bar':
                stored = 1
            elif jm.get('editor_beat_entry_mode') == 'beat':
                stored = bpb
        # 釘住的值優先（見 `_beat_entry_mode` 的說明：每次重新偵測會讓改過 BPM
        # 的譜面漂掉），但差到兩倍以上就是當初判錯的，這時相信量到的。
        if stored is not None and measured and max(stored, measured) < 2 * min(stored, measured):
            resolved = stored
        else:
            resolved = measured or stored or 1
        resolved = max(1, min(bpb, int(resolved)))
        self._epb_count = resolved
        self._epb_mode = (
            'beat' if resolved == bpb else 'bar' if resolved == 1 else 'mixed')
        if isinstance(jm, dict):
            jm['editor_entries_per_bar'] = resolved
            jm['editor_beat_entry_mode'] = (
                'beat' if resolved == bpb else 'bar' if resolved == 1 else 'mixed')
        return resolved

    def _detect_entries_per_bar(self) -> int:
        """從 entry 間距的中位數推「一小節幾筆」= round(bar_ms / 間距)。

        用中位數而不是開頭幾筆，這樣譜面開頭有一段變速也不會把結論帶偏。
        """
        beats = self.get_beat_entries()
        if len(beats) < 2:
            return 1
        diffs = sorted(beats[i + 1][1] - beats[i][1]
                       for i in range(len(beats) - 1)
                       if beats[i + 1][1] > beats[i][1])
        if not diffs:
            return 1
        unit_ms = float(diffs[len(diffs) // 2])
        if unit_ms <= 0:
            return 1
        bpm = max(1.0, self.bpm)
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        bar_ms = num * 4.0 * 60000.0 / (den * bpm)
        return max(1, min(num, int(round(bar_ms / unit_ms))))

    def _beat_entry_mode(self) -> str:
        """beat_data 是 'bar'（一小節一筆）還是 'beat'（一拍一筆）。

        這件事只能靠「entry 間距 vs 全域 BPM」推得，但一旦使用者改了某些小節
        的 BPM，那些間距就不再對應全域 BPM；如果每次都重新偵測，per-bar 譜會
        在改完前幾小節後被誤判成 per-beat，接下來每個 measure_idx 都會抓到錯的
        entry（多小節改 BPM 改到一半整份譜面就跑掉）。所以只判斷一次就釘住，
        並寫進 json_meta 讓存檔後重開沿用同一個判斷。
        """
        cached = self._epb_mode
        if cached in ('bar', 'beat'):
            return cached
        jm = getattr(self, 'json_meta', None)
        stored = jm.get('editor_beat_entry_mode') if isinstance(jm, dict) else None
        if stored in ('bar', 'beat'):
            self._epb_mode = stored
            return stored
        mode = self._detect_beat_entry_mode()
        self._epb_mode = mode
        if isinstance(jm, dict):
            jm['editor_beat_entry_mode'] = mode
        return mode

    def _detect_beat_entry_mode(self) -> str:
        """從 entry 間距推斷 beat_data 格式：比較間距中位數與理論 beat_ms / bar_ms。

        取全部相鄰間距的中位數（不是只取開頭幾筆），這樣就算譜面開頭已經有一段
        變速，多數小節仍會把中位數拉回正確的那一邊。
        """
        beats = self.get_beat_entries()
        if len(beats) < 2:
            return 'bar'
        diffs = sorted(beats[i + 1][1] - beats[i][1]
                       for i in range(len(beats) - 1)
                       if beats[i + 1][1] > beats[i][1])
        if not diffs:
            return 'bar'
        unit_ms = diffs[len(diffs) // 2]   # 中位數
        bpm = max(1.0, self.bpm)
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        beat_ms = 60000.0 / bpm                         # 一拍
        bar_ms  = num * 4.0 * 60000.0 / (den * bpm)     # 一小節
        return 'beat' if abs(unit_ms - beat_ms) < abs(unit_ms - bar_ms) else 'bar'

    # ------------------------------------------------------------------
    # 新增譜面（從頭建立）
    # ------------------------------------------------------------------

    @classmethod
    def create_new(
        cls,
        song_name: str,
        bpm: float,
        duration_sec: float,
        beats_per_bar: int = 4,
    ) -> 'NoteModel':
        """從頭建立一份空白譜面，回傳已初始化的 NoteModel。"""
        model = cls()
        model.file_format   = 'xml'
        model.current_file  = None
        model.xml_lane_index_base = EXTERNAL_LANE_BASE
        model.bpm           = max(1.0, float(bpm))
        model.beats_per_bar = max(1, int(beats_per_bar))
        model.beat_offset_ms = 0.0
        model.music_end_ms  = duration_sec * 1000.0
        # beat_data 一個 entry = 一小節（見下方建立迴圈），直接釘住免得日後誤判
        model._epb_mode = 'bar'
        model._epb_count = 1
        model.json_meta = {'editor_beat_entry_mode': 'bar'}

        # beat_data: 每個 entry 代表一個小節（bar）
        num     = max(1, model.beats_per_bar)
        den     = max(1, model.time_sig_denominator)
        bar_ms  = num * 4.0 * 60000.0 / (den * model.bpm)
        total_bars = int(math.ceil(model.music_end_ms / bar_ms)) + 2

        def _add(parent: ET.Element, tag: str, text, type_attr: str) -> ET.Element:
            el = ET.SubElement(parent, tag)
            el.text = str(text)
            el.set('__type', type_attr)
            return el

        root = ET.Element('music_score')

        hdr = ET.SubElement(root, 'header')
        _add(hdr, 'max_scale',                    108,                    's32')
        _add(hdr, 'min_scale',                    21,                     's32')
        _add(hdr, 'file_version',                 1,                      's16')
        _add(hdr, 'first_bpm',                    bpm_to_xml_value(model.bpm), 's64')
        _add(hdr, 'music_finish_time_msec',        int(model.music_end_ms),'s32')
        _add(hdr, 'time_signature_numerator',      model.beats_per_bar,    's32')
        _add(hdr, 'time_signature_denominator',    den,                    's32')
        _add(hdr, 'time_signature',
             f'{model.beats_per_bar}/{den}',   'str')

        bd = ET.SubElement(root, 'beat_data')
        for i in range(total_bars + 1):
            beat_el = ET.SubElement(bd, 'beat')
            _add(beat_el, 'index',             i,                            's32')
            _add(beat_el, 'start_timing_msec', int(round(i * bar_ms)),       's32')

        ET.SubElement(root, 'note_data')

        model.root  = root
        model.tree  = ET.ElementTree(root)
        model.notes_tree = []
        model.notes      = []
        model.undo_stack.clear()
        model.dirty = False
        # 記住曲名供建議存檔名稱用
        model._song_name: str = song_name
        return model

    # ------------------------------------------------------------------
    # 小節操作
    # ------------------------------------------------------------------

    def _bar_ms(self, bpm: Optional[float] = None) -> float:
        """以目前拍號計算一個小節的毫秒數（標準 quarter-note BPM）。"""
        b   = float(bpm) if bpm is not None else self.bpm
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        return num * 4.0 * 60000.0 / (den * max(1.0, b))

    def get_measure_time_range(self, measure_idx: int) -> Tuple[Optional[int], Optional[int]]:
        """回傳第 measure_idx（0-indexed）小節的 (start_ms, end_ms)。
        超出範圍時回傳 (None, None)。"""
        if self._uses_explicit_beat_units():
            bounds = self._get_precise_measure_boundaries()
            if 0 <= measure_idx < len(bounds):
                _su, start_ms, _eu, end_ms = bounds[measure_idx]
                return (int(start_ms), int(end_ms))
            return (None, None)
        beats = self.get_beat_entries()
        if not beats:
            return (None, None)
        epb = self.entries_per_bar
        entry_start = measure_idx * epb
        entry_end   = entry_start + epb
        if entry_start >= len(beats):
            return (None, None)
        start_ms = beats[entry_start][1]
        if entry_end < len(beats):
            end_ms = beats[entry_end][1]
        else:
            end_ms = start_ms + int(round(self._bar_ms()))
        return (int(start_ms), int(end_ms))

    def get_measure_at_ms(self, ms: float) -> int:
        """回傳 ms 時刻對應的小節編號（0-indexed）。"""
        if self._uses_explicit_beat_units():
            bounds = self._get_precise_measure_boundaries()
            if not bounds:
                return 0
            # bounds 依 start_ms 遞增且相接（end==下一個 start）→ bisect 找所屬小節。
            i = bisect_right(bounds, float(ms), key=lambda b: b[1]) - 1
            return max(0, min(i, len(bounds) - 1))
        beats = self.get_beat_entries()
        if not beats:
            bar_ms = self._bar_ms()
            return max(0, int(ms / max(1.0, bar_ms)))
        epb = self.entries_per_bar
        # 找最後一個 beat_ms <= ms 的位置 i；該拍屬於第 i // epb 小節。
        i = bisect_right(beats, float(ms), key=lambda b: b[1]) - 1
        return max(0, i // max(1, epb))

    def _measure_entry_slice(self, measure_idx: int) -> Tuple[int, int]:
        """第 measure_idx 小節涵蓋哪幾筆 beat entry，回傳位置區間 [s, e)。

        **不要用 `measure_idx * entries_per_bar`。** 那個算式假設「每小節剛好占
        epb 筆、而且 beat index 從 0 開始連號」。真實檔案不保證：例如
        hanei-inspion 那份，beat index 在第 12 筆跳掉兩號（11000 → 13000 →
        15000），從那之後 entry 的**位置**就比 index 少 2，愈後面差愈多。到第
        45 小節時整整差了一個小節——改第 45 小節的 BPM 實際上改到第 46 小節的
        拍點，其餘全部被平移，畫面上就是整段時間軸炸開。

        小節的權威範圍一律以 `get_measure_time_range()` 的 ms 為準（它在
        explicit beat units 的譜上走 `_get_precise_measure_boundaries()`，是照
        beat 單位而不是照位置算的），再用 ms 反查是哪幾筆 entry。
        """
        beats = self.get_beat_entries()
        if not beats:
            return (0, 0)

        # explicit beat units：小節邊界本來就是用「拍單位」定義的，就用拍單位
        # 挑 entry。用 ms 挑會挑錯——小節邊界的 ms 是靠 entry 之間插值換算的，
        # 缺拍點的地方插值會偏，於是某些 entry 被算進隔壁小節。
        bounds = self._measure_unit_bounds(measure_idx)
        if bounds is not None:
            start_unit, end_unit = bounds
            scale = max(1e-9, self._detect_beat_index_scale())
            units = [float(idx) / scale for idx, _ms in beats]
            s = bisect_left(units, start_unit - 1e-6)
            e = bisect_left(units, end_unit - 1e-6)
            return (s, max(s, e))

        start_ms, end_ms = self.get_measure_time_range(measure_idx)
        if start_ms is None or end_ms is None:
            # 沒有權威範圍（例如超出末端）才退回位置估算
            epb = max(1, self.entries_per_bar)
            s = min(len(beats), max(0, measure_idx * epb))
            return (s, min(len(beats), s + epb))
        ms_list = [ms for _idx, ms in beats]
        s = bisect_left(ms_list, int(start_ms))
        e = bisect_left(ms_list, int(end_ms))
        return (s, max(s, e))

    def _measure_unit_bounds(self, measure_idx: int) -> Optional[Tuple[float, float]]:
        """第 measure_idx 小節的 (起始拍單位, 結束拍單位)；非 explicit 格式回 None。"""
        if not self._uses_explicit_beat_units():
            return None
        bounds = self._get_precise_measure_boundaries()
        if not (0 <= measure_idx < len(bounds)):
            return None
        start_unit, _s_ms, end_unit, _e_ms = bounds[measure_idx]
        if end_unit <= start_unit:
            return None
        return (float(start_unit), float(end_unit))

    def _retime_time_sig_changes(
        self, start_ms: int, end_ms: int, ratio: float, delta: int,
    ) -> None:
        """某一小節被改長度／被增刪之後，拍號標記的絕對 ms 也要跟著移。

        `time_sig_changes` 存的是**絕對 ms**。改一個小節的長度時，音符和拍點
        都搬了，這些標記卻留在原地——於是「從哪裡開始是 2/4」對到了錯的位置，
        小節切法從那裡整個歪掉。症狀是改了第 1 小節，遠處第 44 小節的 BPM 跟著
        變（90 → 88.5）。

        `scale_all_time()` 和「依 MIDI 重定節拍」早就有做這件事，只有逐小節編輯
        這條路漏掉了。json_meta 裡的鏡像 `time_signature_changes` 一併更新。
        """
        def moved(ms: int) -> int:
            ms = int(ms)
            if ms < start_ms:
                return ms
            if ms < end_ms:
                return start_ms + int(round((ms - start_ms) * ratio))
            return max(0, ms + delta)

        if self.time_sig_changes:
            self.time_sig_changes = [
                (max(0, moved(ms)), num, den)
                for (ms, num, den) in self.time_sig_changes
            ]
        jm = self.json_meta if isinstance(self.json_meta, dict) else {}
        tsc = jm.get('time_signature_changes')
        if isinstance(tsc, list):
            for ev in tsc:
                if isinstance(ev, dict) and 'time_ms' in ev:
                    try:
                        ev['time_ms'] = max(0, moved(int(ev['time_ms'])))
                    except (TypeError, ValueError):
                        pass

    def _resnap_time_sig_changes(self, tolerance_ms: int = 8) -> None:
        """把拍號標記對齊到真正的小節起點。

        標記存的是絕對 ms，而小節邊界是從拍點內插算出來的，重算之後兩者常常
        差個 1~2ms。差 1ms 就足以讓「下一小節才恢復原拍號」變成「這一小節就
        恢復」，或反過來把該保留的標記當成在本小節內而刪掉——看起來就是改了
        一個小節的拍號，前一個小節跟著變、後一個小節也跟著變。
        """
        if not self.time_sig_changes:
            return
        starts: List[int] = []
        for i in range(self.count_measures()):
            s, _e = self.get_measure_time_range(i)
            if s is not None:
                starts.append(int(s))
        if not starts:
            return
        snapped: Dict[int, Tuple[int, int]] = {}
        for ms, num, den in self.time_sig_changes:
            ms = int(ms)
            j = bisect_left(starts, ms)
            best = None
            for c in (j - 1, j):
                if 0 <= c < len(starts):
                    if best is None or abs(starts[c] - ms) < abs(starts[best] - ms):
                        best = c
            if best is not None and abs(starts[best] - ms) <= tolerance_ms:
                ms = starts[best]
            snapped[ms] = (int(num), int(den))
        self.time_sig_changes = sorted(
            [(ms, nd[0], nd[1]) for ms, nd in snapped.items()], key=lambda x: x[0])

    def _ensure_measure_boundary_entries(self, measure_idx: int) -> bool:
        """確保這一小節的頭、尾兩個拍單位上真的有 beat entry，缺的就補一筆。

        小節邊界是用「拍單位」定義的，但檔案不保證每個拍單位都有 entry——
        hanei 那份就少了好幾個小節的第一拍（unit 12、14 都沒有 entry）。

        少了邊界那一筆，該邊界的 ms 只能靠左右兩筆內插得到。於是「把某小節
        改成 160 BPM」時，程式把小節內的拍點壓短、後面整段平移，邊界卻是內插
        出來的、不會剛好落在 start+new_dur —— 多出來或少掉的長度就被前後兩個
        小節分掉，症狀正是「改一個小節，前後兩個一起變」。

        補上邊界錨點（ms 用目前的內插值，等於不改變任何現有時間），邊界才釘
        得住。回傳是否有補。
        """
        bounds = self._measure_unit_bounds(measure_idx)
        if bounds is None:
            return False
        scale = self._detect_beat_index_scale()
        if scale <= 0:
            return False
        entries = self._get_beat_unit_entries()      # [(unit, ms), ...]
        if not entries:
            return False
        have = {round(u, 6) for u, _ms in entries}
        beats = list(self.get_beat_entries())
        added = False
        for unit in bounds:
            key = round(float(unit), 6)
            if key in have:
                continue
            ms = self._unit_to_ms_from_entries(float(unit), entries)
            if ms is None:
                continue
            beats.append((int(round(float(unit) * scale)), int(round(ms))))
            have.add(key)
            added = True
        if added:
            beats.sort(key=lambda x: x[1])
            self._write_beat_entries(beats)
        return added

    def _measure_entry_fractions(
        self, measure_idx: int, entry_s: int, entry_e: int,
    ) -> List[float]:
        """這一小節每一筆 entry 在小節內的位置比例（0 = 小節頭，1 = 小節尾）。

        **要用 beat index（拍單位）算，不能用「第幾筆 / 總筆數」。** beat index
        有跳號的譜，某些小節的拍點是缺的：例如某小節橫跨第 12~16 拍，但只有
        第 13、15 拍有 entry。按筆數均分會把它們排到 0 和 1/2，實際上應該是
        1/4 和 3/4。小節邊界是靠 entry 之間插值換算 ms 的，entry 位置一偏，
        前後小節的長度就跟著被改掉——症狀就是「改一個小節的 BPM，前後兩個
        小節也跟著變」。
        """
        count = max(0, entry_e - entry_s)
        if count <= 0:
            return []
        bounds = self._measure_unit_bounds(measure_idx)
        if bounds is not None:
            start_unit, end_unit = bounds
            span = end_unit - start_unit
            scale = max(1e-9, self._detect_beat_index_scale())
            beats = self.get_beat_entries()
            fracs = []
            for i in range(entry_s, entry_e):
                unit = float(beats[i][0]) / scale
                fracs.append(max(0.0, min(1.0, (unit - start_unit) / span)))
            return fracs
        # 舊格式：每小節固定 epb 筆、依序等距，按筆數均分就是對的
        return [k / float(count) for k in range(count)]

    def count_measures(self) -> int:
        """回傳目前總小節數。"""
        if self._uses_explicit_beat_units():
            return len(self._get_precise_measure_boundaries())
        epb = max(1, self.entries_per_bar)
        return len(self.get_beat_entries()) // epb

    def _set_music_end_ms(self, value: float) -> None:
        """更新曲末時間；XML 與 JSON 兩種格式都要寫到。"""
        self.music_end_ms = float(value)
        if self.root is not None:
            fin_el = self.root.find('header/music_finish_time_msec')
            if fin_el is not None:
                fin_el.text = str(int(self.music_end_ms))
        jm = getattr(self, 'json_meta', {}) or {}
        if 'music_finish_time_msec' in jm:
            jm['music_finish_time_msec'] = int(self.music_end_ms)
            self.json_meta = jm

    def add_measure(self, new_bpm: Optional[float] = None) -> None:
        """在譜面末尾新增一個小節。

        XML 與 JSON 兩種格式都支援：讀寫都走 `get_beat_entries()` /
        `_write_beat_entries()`，後者會同時更新 <beat_data> 與
        `json_meta['beat_timings'|'beat_indices']`。
        """
        bpm_use  = float(new_bpm) if new_bpm and new_bpm > 0 else self.bpm
        bar_ms   = self._bar_ms(bpm_use)
        epb      = max(1, self.entries_per_bar)
        beat_ms_each = bar_ms / epb

        existing = list(self.get_beat_entries())   # [(idx, ms), ...]
        # index 要照這份譜自己的刻度往前加（官方檔一拍 +1000，不是 +1）。
        scale = self._detect_beat_index_scale()
        beats_in_bar = max(1, self.beats_per_bar)
        idx_step = max(1, int(round(scale * beats_in_bar / epb)))
        if existing:
            last_idx, last_ms = existing[-1]
        else:
            last_idx, last_ms = -idx_step, 0

        final_ms = last_ms
        for k in range(epb):
            final_ms = last_ms + int(round(beat_ms_each * (k + 1)))
            existing.append((int(last_idx) + idx_step * (k + 1), final_ms))

        self._write_beat_entries(existing)
        self._set_music_end_ms(max(self.music_end_ms, float(final_ms)))
        self.dirty = True

    def measure_count(self) -> int:
        """譜面目前有幾個小節（`count_measures()` 的別名，兩邊語意必須一致）。"""
        return self.count_measures()

    def insert_measure(self, measure_idx: int,
                       new_bpm: Optional[float] = None) -> bool:
        """在第 measure_idx 小節**之前**插入一個空白小節（`delete_measure` 的反向）。

        插入點之後的音符與拍點整段往後推一個小節的長度，所以已經排好的譜不會
        錯位。`measure_idx` 超出目前小節數時等同 `add_measure`（接在譜尾）。

        回傳是否真的插入了。
        """
        beats = self.get_beat_entries()
        epb = max(1, self.entries_per_bar)
        measure_idx = max(0, int(measure_idx))
        if not beats or measure_idx >= self.count_measures():
            self.add_measure(new_bpm)
            return True

        start_ms, _end_ms = self.get_measure_time_range(measure_idx)
        if start_ms is None:
            self.add_measure(new_bpm)
            return True

        bpm_use = float(new_bpm) if new_bpm and new_bpm > 0 else self.bpm
        dur_ms = int(round(self._bar_ms(bpm_use)))
        if dur_ms <= 0:
            return False
        insert_ms = int(start_ms)
        beats_in_bar = max(1, self.get_beats_per_bar_at_ms(insert_ms))
        beat_ms_each = dur_ms / epb

        # 1. 插入點之後的音符整段往後推（長度不變）
        for n in self.notes_tree:
            if int(n.start) >= insert_ms:
                length = max(1, int(n.end) - int(n.start))
                n.start = int(n.start) + dur_ms
                n.end = n.start + length
                n.gate = length

        # 2. 拍點：在這一小節的第一筆 entry 前插入 epb 筆，之後的往後推。
        #    index 要照原本的刻度往後推一個小節的「拍單位」，不能重編號。
        # 以 ms 反查插入點，不能用 measure_idx*epb（見 _measure_entry_slice）
        ins_at = self._measure_entry_slice(measure_idx)[0]
        scale = self._detect_beat_index_scale()
        idx_shift = int(round(beats_in_bar * scale))
        base_idx = int(beats[ins_at][0])
        idx_step = max(1, int(round(scale * beats_in_bar / epb)))
        entries: List[Tuple[int, int]] = []
        for i, (bidx, bms) in enumerate(beats):
            if i == ins_at:
                for k in range(epb):
                    entries.append((base_idx + idx_step * k,
                                    insert_ms + int(round(beat_ms_each * k))))
            if i >= ins_at:
                entries.append((int(bidx) + idx_shift, int(bms) + dur_ms))
            else:
                entries.append((int(bidx), int(bms)))
        self._write_beat_entries(entries)
        # 插入點之後的拍號標記也要往後推（絕對 ms）
        self._retime_time_sig_changes(insert_ms, insert_ms, 1.0, dur_ms)

        # 3. 更新 music_finish_time_msec
        self._set_music_end_ms(float(self.music_end_ms) + dur_ms)

        self.rebuild_display_cache()
        self.dirty = True
        return True

    def delete_measure(self, measure_idx: int) -> int:
        """刪除第 measure_idx 小節（0-indexed）以及其中所有音符，
        並將後續音符/拍子時間往前平移填補間距。
        回傳刪除的音符數。"""
        start_ms, end_ms = self.get_measure_time_range(measure_idx)
        if start_ms is None or end_ms is None:
            return 0
        dur_ms = end_ms - start_ms

        # 1. 刪除音符
        before  = len(self.notes_tree)
        self.notes_tree = [
            n for n in self.notes_tree
            if not (start_ms <= n.start < end_ms)
        ]
        deleted = before - len(self.notes_tree)

        # 2. 後續音符時間往前平移
        for n in self.notes_tree:
            if n.start >= end_ms:
                n.start = max(0, n.start - dur_ms)
                n.end   = max(n.start + 1, n.end - dur_ms)
                n.gate  = n.end - n.start

        # 3. 拍點：刪除這一小節涵蓋的 entries
        all_beats = self.get_beat_entries()   # [(idx, ms), ...]
        # 以 ms 反查，不能假設每小節剛好 epb 筆（見 _measure_entry_slice）
        del_start, del_end = self._measure_entry_slice(measure_idx)

        # 重建 beat 清單：跳過 [del_start, del_end)，後續 ms 與 index 都往前
        # 補上這一小節的量。index 照原刻度平移，不能重編號——重編號會把
        # beat index 的刻度打掉，explicit beat units 的譜會整個換一套小節切法。
        beats_in_bar = max(1, self.get_beats_per_bar_at_ms(start_ms))
        idx_shift = int(round(beats_in_bar * self._detect_beat_index_scale()))
        entries: List[Tuple[int, int]] = []
        for i, (bidx, bms) in enumerate(all_beats):
            if i < del_start:
                entries.append((int(bidx), int(bms)))
            elif i < del_end:
                pass   # 刪除這些 entry
            else:
                entries.append((int(bidx) - idx_shift, max(0, int(bms) - dur_ms)))
        self._write_beat_entries(entries)
        # 被刪掉那一段裡的拍號標記收到小節頭，後面的整段往前補
        self._retime_time_sig_changes(int(start_ms), int(end_ms), 0.0, -dur_ms)

        # 4. 更新 music_finish_time_msec
        self._set_music_end_ms(max(0.0, self.music_end_ms - dur_ms))

        self.rebuild_display_cache()
        self.dirty = True
        return deleted

    def _signature_by_measure(self) -> List[Tuple[int, int]]:
        """目前每個小節的 (numerator, denominator)，以小節序號為索引。"""
        out: List[Tuple[int, int]] = []
        for i in range(self.count_measures()):
            start_ms, _e = self.get_measure_time_range(i)
            if start_ms is None:
                break
            num = max(1, self.get_beats_per_bar_at_ms(start_ms))
            den = max(1, int(self.time_sig_denominator))
            for tms, _tnum, tden in self.time_sig_changes:
                if tms <= start_ms:
                    den = max(1, int(tden))
                else:
                    break
            out.append((int(num), int(den)))
        return out

    def _rebuild_time_sig_changes(self, sig_by_measure: List[Tuple[int, int]]) -> None:
        """用「每個小節的拍號」重建標記清單。

        小節起點直接由拍點換算：第 i 小節的起始拍單位 = 前面所有小節的
        numerator 總和，這和 `_compute_precise_measure_boundaries()` 的累加方式
        一模一樣，所以標記一定落在真正的小節邊界上。只有拍號和前一小節不同的
        地方才寫標記。

        explicit beat units（一個 index = 一拍）用「拍單位累加」求小節起點，因為
        邊界本來就是這樣算的。舊格式的 per-bar 譜一個 entry = 一小節，小節起點
        和 numerator 無關，直接查 `get_measure_time_range()` 就好（也不會循環
        依賴——那條路不看拍號）。
        """
        if not sig_by_measure:
            return
        starts: List[int] = []
        if self._uses_explicit_beat_units():
            entries = self._get_beat_unit_entries()
            if not entries:
                return
            unit = float(entries[0][0])
            for num, _den in sig_by_measure:
                ms = self._unit_to_ms_from_entries(unit, entries)
                if ms is None:
                    break
                starts.append(max(0, int(round(ms))))
                unit += float(max(1, num))
        else:
            for i in range(len(sig_by_measure)):
                s, _e = self.get_measure_time_range(i)
                if s is None:
                    break
                starts.append(int(s))
        if not starts:
            return

        marks: List[Tuple[int, int, int]] = []
        prev: Optional[Tuple[int, int]] = None
        for start_ms, (num, den) in zip(starts, sig_by_measure):
            if prev != (num, den):
                marks.append((start_ms, int(num), int(den)))
                prev = (num, den)
        if marks:
            self.time_sig_changes = marks
            self.beats_per_bar = int(marks[0][1])
            self.time_sig_denominator = int(marks[0][2])

    def _sync_time_sig_changes_out(self) -> None:
        """把 self.time_sig_changes 寫回 XML <time_signature_changes> 或 json_meta。"""
        if self.root is not None:
            ts_root = self.root.find('time_signature_changes')
            if ts_root is None:
                ts_root = ET.SubElement(self.root, 'time_signature_changes')
            for child in list(ts_root):
                ts_root.remove(child)
            for tms, tnum, tden in self.time_sig_changes:
                ch = ET.SubElement(ts_root, 'ts_change')
                ms_el = ET.SubElement(ch, 'start_timing_msec')
                ms_el.set('__type', 's32')
                ms_el.text = str(int(tms))
                num_el = ET.SubElement(ch, 'numerator')
                num_el.set('__type', 's32')
                num_el.text = str(int(tnum))
                den_el = ET.SubElement(ch, 'denominator')
                den_el.set('__type', 's32')
                den_el.text = str(int(tden))
        else:
            jm = getattr(self, 'json_meta', None) or {}
            jm['time_signature_changes'] = [
                {'time_ms': int(tms), 'numerator': int(tnum), 'denominator': int(tden)}
                for tms, tnum, tden in self.time_sig_changes
            ]
            self.json_meta = jm

    def set_measure_time_signature(
        self,
        measure_idx: int,
        numerator: int,
        denominator: int,
        uniform: bool = True,
        time_uniform: bool = True,
    ) -> None:
        """設定第 measure_idx 小節的拍號（numerator / denominator）。
        會在內存與檔案結構中新增或更新 time_signature_changes。
        """
        if numerator < 1 or denominator < 1:
            return
        # 先把標記對齊到真正的小節起點，否則下面用 ms 區間判斷「哪些標記屬於
        # 這一小節」會因為 1ms 的內插誤差而誤刪隔壁小節的恢復標記。
        self._resnap_time_sig_changes()
        self._ensure_measure_boundary_entries(measure_idx)
        # 改動前先記下「每個小節各是什麼拍號」。最後會用這份清單重建標記——
        # 靠 ms 加加減減去搬標記永遠會差那 1ms，前後小節的拍號就互相跑掉。
        sig_by_measure = self._signature_by_measure()
        start_ms, end_ms = self.get_measure_time_range(measure_idx)
        if start_ms is None or end_ms is None:
            return

        old_dur = end_ms - start_ms
        # read current BPM for this measure (before we change the signature)
        try:
            bpm_here = float(self.get_measure_bpm(measure_idx))
        except Exception:
            bpm_here = max(1.0, float(self.bpm))

        # compute new duration for the measure:
        # keep BPM and let bar duration change with numerator/denominator.
        # (measure-uniform mode now also applies real-time scaling)
        num = int(numerator)
        den = int(denominator)
        new_dur = int(round(num * 4.0 * 60000.0 / (den * max(1.0, bpm_here))))
        delta = new_dur - old_dur
        ratio = new_dur / max(1, old_dur)

        # Build time_signature_changes so this change affects ONLY this measure:
        # [start_ms, end_ms) uses new signature, and next measure restores previous signature.
        old_changes = sorted(list(self.time_sig_changes), key=lambda x: x[0])

        # signature active before the edited measure
        prev_num = int(self.beats_per_bar)
        prev_den = int(self.time_sig_denominator)
        for tms, tnum, tden in old_changes:
            # 嚴格小於：這一小節自己的標記不算「之前的拍號」。用 <= 的話，
            # 編輯一個本來就有自己標記的小節（例如原本是 2/4 的那一小節），
            # 恢復標記會把後面全部設成 2/4。
            if tms < start_ms:
                prev_num = int(tnum)
                prev_den = int(tden)
            else:
                break

        new_end_ms = int(end_ms + delta)

        # shift later changes by delta because timeline after this measure moves
        shifted_changes: List[Tuple[int, int, int]] = []
        for tms, tnum, tden in old_changes:
            if tms < start_ms:
                shifted_changes.append((int(tms), int(tnum), int(tden)))
            elif tms >= end_ms:
                shifted_changes.append((int(tms + delta), int(tnum), int(tden)))
            # changes inside [start_ms, end_ms) are replaced by the single-measure edit

        # insert edited-measure signature at measure start
        shifted_changes.append((int(start_ms), int(num), int(den)))

        # ensure restoration at next-measure start (single-measure scope)
        if not any(int(tms) == int(new_end_ms) for tms, _n, _d in shifted_changes):
            shifted_changes.append((int(new_end_ms), int(prev_num), int(prev_den)))

        # normalize (last writer wins at same timestamp)
        _tmp: Dict[int, Tuple[int, int]] = {}
        for tms, tnum, tden in shifted_changes:
            _tmp[int(tms)] = (int(tnum), int(tden))
        self.time_sig_changes = sorted(
            [(tms, nd[0], nd[1]) for tms, nd in _tmp.items()],
            key=lambda x: x[0],
        )

        self._sync_time_sig_changes_out()

        # 如果小節長度沒變，只更新拍號資料即可
        if delta == 0:
            self.rebuild_display_cache()
            self.dirty = True
            return

        # 1. 調整小節內音符與平移後續音符
        # Keep original timings so we can distinguish in-measure notes from subsequent notes
        # and avoid shifting the same note twice.
        orig_pos: Dict[int, Tuple[int, int]] = {id(n): (int(n.start), int(n.end)) for n in self.notes_tree}

        # If bar duration shrinks in time-uniform mode, remove notes that start in the cut tail.
        # Example: 4/4 -> 3/4 removes notes whose start lies in the removed last beat region.
        if time_uniform and new_dur < old_dur:
            cut_start = int(start_ms + new_dur)
            kept: List[GNote] = []
            for n in self.notes_tree:
                o_s, _o_e = orig_pos[id(n)]
                if start_ms <= o_s < end_ms and o_s >= cut_start:
                    continue
                kept.append(n)
            self.notes_tree = kept
            orig_pos = {id(n): (int(n.start), int(n.end)) for n in self.notes_tree}

        notes_in_measure: List[GNote] = [n for n in self.notes_tree if start_ms <= orig_pos[id(n)][0] < end_ms]
        notes_in_measure.sort(key=lambda x: (x.start, x.min_key))
        in_measure_ids = {id(n) for n in notes_in_measure}

        if notes_in_measure:
            if uniform:
                # 均分模式：保留音符原始開始時間與長度，不做縮放。
                # 只有小節長度/後續平移會依 time_uniform 決定是否變動。
                pass
            else:
                # 保留相對位置：以比例縮放每個音符的 offset
                for n in notes_in_measure:
                    o_s, o_e = orig_pos[id(n)]
                    rel_s = o_s - start_ms
                    rel_e = o_e - start_ms
                    ns = start_ms + int(round(rel_s * ratio))
                    ne = start_ms + int(round(rel_e * ratio))
                    ns = max(start_ms, min(start_ms + new_dur - 1, ns))
                    ne = max(ns + 1, min(start_ms + new_dur, ne))
                    n.start = ns
                    n.end = ne
                    n.gate = max(1, n.end - n.start)

        # In shrink case, clip surviving in-measure notes that cross the new bar end.
        if time_uniform and new_dur < old_dur:
            new_end = int(start_ms + new_dur)
            for n in notes_in_measure:
                if n.end > new_end:
                    n.end = max(n.start + 1, new_end)
                    n.gate = max(1, n.end - n.start)

        # shift only notes that were originally after this measure (constant delta)
        for n in self.notes_tree:
            o_s, o_e = orig_pos[id(n)]
            if id(n) in in_measure_ids:
                continue
            if o_s >= end_ms:
                n.start = max(0, o_s + delta)
                n.end = max(n.start + 1, o_e + delta)
                n.gate = max(1, n.end - n.start)

        # 2. 更新拍點（XML beat_data 與 JSON beat_timings 走同一條路）
        all_beats = list(self.get_beat_entries())
        if all_beats:
            entry_s, entry_e = self._measure_entry_slice(measure_idx)
            entry_s = max(0, entry_s)
            entry_e = min(len(all_beats), entry_e)

            # 這一小節的拍數從 prev_num 變成 num，所以「拍單位」的長度也變了；
            # 後面每一筆 entry 的 index 要整段平移這個差額。
            #
            # 以前這裡是把**所有** entry 的 index 重編成 0,1,2,…。官方檔的
            # index 刻度是每拍 +1000，重編之後刻度變成 1，
            # `_uses_explicit_beat_units()` 立刻翻成 False，整份譜改用另一套
            # 小節切法 —— 於是「改第 6 小節的拍號」會看到第 5 小節變成 3/4、
            # 自己還是 4/4，前後全部錯位。
            scale = self._detect_beat_index_scale()
            explicit = self._uses_explicit_beat_units()
            per_beat = self.entries_per_bar > 1
            bounds = self._measure_unit_bounds(measure_idx)
            old_span_units = (bounds[1] - bounds[0]) if bounds else float(prev_num)

            if explicit:
                base_idx = int(round(bounds[0] * scale)) if bounds else int(all_beats[entry_s][0])
                new_span_units = float(num)
                idx_shift = int(round((new_span_units - old_span_units) * scale))
                new_count = max(1, num if per_beat else 1)
                idx_step = int(round(scale * new_span_units / new_count))
            else:
                # 舊格式：index 本來就是 0..n-1 的流水號，維持原本的重編行為
                base_idx = entry_s
                idx_shift = 0
                new_count = max(1, entry_e - entry_s)
                idx_step = 1

            entries: List[Tuple[int, int]] = [
                (int(all_beats[i][0]), int(all_beats[i][1])) for i in range(entry_s)
            ]
            for k in range(new_count):
                bms = start_ms + int(round(new_dur * (k / float(new_count))))
                entries.append((base_idx + idx_step * k, int(bms)))
            for i in range(entry_e, len(all_beats)):
                entries.append((int(all_beats[i][0]) + idx_shift,
                                int(all_beats[i][1]) + delta))

            if not explicit:      # 舊格式：重新編號成連續流水號
                entries = [(i, ms) for i, (_idx, ms) in enumerate(entries)]
            self._write_beat_entries(entries)

        # 拍點重算完，用「每小節拍號」清單重建標記：位置直接取自重算後的
        # 拍點，和 _compute_precise_measure_boundaries 的累加方式完全一致，
        # 不會有 1ms 誤差。
        if 0 <= measure_idx < len(sig_by_measure):
            sig_by_measure[measure_idx] = (int(num), int(den))
        self._rebuild_time_sig_changes(sig_by_measure)
        self._sync_time_sig_changes_out()

        # 3. 更新 music_finish_time_msec
        self.music_end_ms = max(0.0, self.music_end_ms + delta)
        if self.root is not None:
            fin_el = self.root.find('header/music_finish_time_msec')
            if fin_el is not None:
                fin_el.text = str(int(self.music_end_ms))
        else:
            jm = getattr(self, 'json_meta', None) or {}
            jm['music_finish_time_msec'] = int(self.music_end_ms)
            self.json_meta = jm

        self.rebuild_display_cache()
        self.dirty = True
        # 完成：此方法不應回傳任何 deleted 變數（可能來自其他函式）

    def get_measure_bpm(self, measure_idx: int) -> float:
        """回傳第 measure_idx 小節的 BPM（依前後小節的時間間距估算）。
        BPM = num * 4 * 60000 / (den * bar_ms)"""
        if self._uses_explicit_beat_units():
            bounds = self._get_precise_measure_boundaries()
            if 0 <= measure_idx < len(bounds):
                _su, start_ms, _eu, end_ms = bounds[measure_idx]
                num_p = max(1, self.get_beats_per_bar_at_ms(start_ms))
                den_p = max(1, self.time_sig_denominator)
                for change_ms, _ch_num, ch_den in self.time_sig_changes:
                    if change_ms <= start_ms:
                        den_p = max(1, ch_den)
                    else:
                        break
                bar_ms = max(1, end_ms - start_ms)
                return round(num_p * 4.0 * 60000.0 / (den_p * float(bar_ms)), 2)
        beats = self.get_beat_entries()
        # Determine numerator/denominator for this measure using time_sig_changes
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        # Find matching time signature change at or before the measure start
        beats = self.get_beat_entries()
        if beats:
            # measure start ms
            epb = self.entries_per_bar
            e_s = measure_idx * epb
            if 0 <= e_s < len(beats):
                start_ms = beats[e_s][1]
                for change_ms, ch_num, ch_den in self.time_sig_changes:
                    if change_ms <= start_ms:
                        num = max(1, ch_num)
                        den = max(1, ch_den)
                    else:
                        break
        epb   = self.entries_per_bar
        e_s   = measure_idx * epb
        e_e   = e_s + epb
        if e_e < len(beats):
            bar_ms = beats[e_e][1] - beats[e_s][1]
            if bar_ms > 0:
                return round(num * 4.0 * 60000.0 / (den * bar_ms), 2)
        # 若是最後一小節，用前一小節間距估算
        if e_s > 0 and e_s < len(beats):
            prev_s = max(0, e_s - epb)
            bar_ms = beats[e_s][1] - beats[prev_s][1]
            if bar_ms > 0:
                return round(num * 4.0 * 60000.0 / (den * bar_ms), 2)
        return float(self.bpm)

    def set_measure_bpm(self, measure_idx: int, new_bpm: float, uniform: bool = False, mode: str = 'scale', adjust_notes: bool = True) -> None:
        """修改第 measure_idx 小節的 BPM。
        mode='scale'（預設）：等比例縮放小節內音符時間，並平移後續音符與拍子（原有行為）。
        mode='trim'：直接以 new_bpm 計算小節長度，保留小節內音符的起始時間；若音符超出新小節長度，將其 end 裁剪到小節尾（若裁剪後 end<=start 則刪除）。
        uniform 參數只影響 beat_timings 在 JSON 模式下如何重排（保留原邏輯）。"""
        # Allow JSON-only charts (no XML root) by operating on in-memory
        # beat timings stored in `json_meta['beat_timings']` when present.
        # 先把小節頭尾的拍點補齊，否則邊界只是內插出來的、釘不住，改完長度會
        # 被前後小節分掉（見 _ensure_measure_boundary_entries）。
        self._ensure_measure_boundary_entries(measure_idx)
        start_ms, end_ms = self.get_measure_time_range(measure_idx)
        if start_ms is None or end_ms is None:
            return
        old_dur = end_ms - start_ms
        # 使用該小節的拍號（若存在 time_sig_changes）計算新的小節長度
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        for change_ms, ch_num, ch_den in self.time_sig_changes:
            if change_ms <= start_ms:
                num = max(1, ch_num)
                den = max(1, ch_den)
            else:
                break
        new_dur = int(round(num * 4.0 * 60000.0 / (den * float(new_bpm))))
        delta   = new_dur - old_dur
        if delta == 0 and mode == 'scale':
            return
        ratio = new_dur / max(1, old_dur)

        # Notes handling differs by mode
        if adjust_notes and mode == 'scale':
            # 1. 縮放小節內音符，平移後續音符
            for n in self.notes_tree:
                if start_ms <= n.start < end_ms:
                    rel_s   = n.start - start_ms
                    rel_e   = n.end   - start_ms
                    n.start = start_ms + int(round(rel_s * ratio))
                    n.end   = start_ms + int(round(rel_e * ratio))
                    n.gate  = max(1, n.end - n.start)
                elif n.start >= end_ms:
                    n.start += delta
                    n.end   += delta
        elif adjust_notes:
            # mode == 'trim' : 保留 start，不縮放；若 end 超出新小節長度則裁剪；若 start 已在新小節之外則刪除；後續音符平移
            new_notes: List[GNote] = []
            new_end_ms = start_ms + new_dur
            for n in self.notes_tree:
                if start_ms <= n.start < end_ms:
                    # note originally inside the edited measure
                    if n.start >= new_end_ms:
                        # starts beyond new end -> drop
                        continue
                    # clip end if necessary
                    if n.end > new_end_ms:
                        n.end = max(n.start + 1, int(new_end_ms - 1))
                    n.gate = max(1, n.end - n.start)
                    new_notes.append(n)
                elif n.start >= end_ms:
                    n.start += delta
                    n.end   += delta
                    new_notes.append(n)
                else:
                    new_notes.append(n)
            # replace notes_tree
            self.notes_tree = new_notes

        # 2. 更新 beat_data（支援 per-bar 和 per-beat 格式）
        beat_root = None if self.root is None else self.root.find('beat_data')
        if beat_root is not None:
            # XML-backed beat_data (existing behavior)
            all_beats = self.get_beat_entries()
            # 以 ms 反查這一小節真正涵蓋哪幾筆 entry。用 measure_idx*epb 的話，
            # beat index 有跳號的譜會改到隔壁小節的拍點（見 _measure_entry_slice）。
            entry_s, entry_e = self._measure_entry_slice(measure_idx)
            fracs = self._measure_entry_fractions(measure_idx, entry_s, entry_e)
            new_beats: List[Tuple[int, int]] = []
            for i, (bidx, bms) in enumerate(all_beats):
                if i < entry_s:
                    new_beats.append((bidx, bms))
                elif i < entry_e:
                            # 插值重算本小節內各 entry 的時間（beat starts）
                            if mode == 'scale':
                                frac = fracs[i - entry_s]
                                new_beats.append((bidx, start_ms + int(round(new_dur * frac))))
                            else:
                                # trim 模式：保留原始 entry 時間，若超出新小節長度則跳過（刪除）
                                orig_bms = bms
                                if orig_bms < start_ms + new_dur:
                                    new_beats.append((bidx, int(orig_bms)))
                                else:
                                    # 跳過（等同刪除該 beat entry）
                                    pass
                else:
                    new_beats.append((bidx, bms + delta))

            # 清空並重建
            for child in list(beat_root):
                beat_root.remove(child)
            for bidx, bms in new_beats:
                beat_el = ET.SubElement(beat_root, 'beat')
                idx_el  = ET.SubElement(beat_el, 'index')
                idx_el.set('__type', 's32')
                idx_el.text = str(bidx)
                ms_el = ET.SubElement(beat_el, 'start_timing_msec')
                ms_el.set('__type', 's32')
                ms_el.text = str(bms)
        else:
            # JSON-only charts: update json_meta['beat_timings'] if present
            jm = getattr(self, 'json_meta', None)
            if jm and isinstance(jm.get('beat_timings'), (list, tuple)) and jm.get('beat_timings'):
                try:
                    vals = sorted(int(float(x)) for x in jm.get('beat_timings'))
                except Exception:
                    vals = []
                if vals:
                    # 同 XML 路徑（見 _measure_entry_slice / _measure_entry_fractions）
                    entry_s, entry_e = self._measure_entry_slice(measure_idx)
                    fracs = self._measure_entry_fractions(measure_idx, entry_s, entry_e)
                    new_vals: List[int] = []
                    for i, bms in enumerate(vals):
                        if i < entry_s:
                            new_vals.append(int(bms))
                        elif i < entry_e:
                            if mode == 'scale':
                                if uniform:
                                    # 均分小節：依拍單位分配（beat starts）
                                    frac = fracs[i - entry_s]
                                    new_bms = int(start_ms + int(round(new_dur * frac)))
                                else:
                                    # 保留原始相對位置（scale by ratio）
                                    rel = int(bms) - int(start_ms)
                                    new_bms = int(int(start_ms) + round(rel * ratio))
                                new_vals.append(new_bms)
                            else:
                                # trim 模式：保留原 beat timing 若在新小節內，否則跳過
                                if int(bms) < start_ms + new_dur:
                                    new_vals.append(int(bms))
                                else:
                                    pass
                        else:
                            new_vals.append(int(bms + delta))
                    jm['beat_timings'] = new_vals
                    self.json_meta = jm

        # 拍號標記是絕對 ms，這一小節被改長度後也要跟著移，否則遠處的小節
        # 切法會歪掉（見 _retime_time_sig_changes）
        self._retime_time_sig_changes(int(start_ms), int(end_ms), ratio, delta)

        # 3. 更新 music_finish_time_msec（XML 或 JSON）
        self.music_end_ms = max(0.0, self.music_end_ms + delta)
        if self.root is not None:
            fin_el = self.root.find('header/music_finish_time_msec')
            if fin_el is not None:
                fin_el.text = str(int(self.music_end_ms))
        else:
            jm = getattr(self, 'json_meta', None) or {}
            jm['music_finish_time_msec'] = int(self.music_end_ms)
            self.json_meta = jm

        self.rebuild_display_cache()
        self.dirty = True

    def has_chart(self) -> bool:
        """是否已經有一份可編輯的譜面。

        剛啟動時 `MainWindow` 會塞一個空的 `NoteModel` 佔位，那時候還沒有
        任何譜面：沒開過檔、沒有音符、長度是 0、也沒有拍點。放置模式在這
        個狀態下要先請使用者建立譜面，否則音符會落在一個沒有時間軸的地方。
        """
        if self.current_file:
            return True
        if self.notes_tree:
            return True
        if float(self.music_end_ms or 0.0) > 0.0:
            return True
        return bool(self.get_beat_entries())

    def get_beat_entries(self) -> List[tuple]:
        """回傳 [(beat_index, start_ms), ...] 排序後的清單（快取；讀取端請勿就地修改）。"""
        sig = self._beat_sig()
        if self._cache_be is not None and self._cache_be_sig == sig:
            return self._cache_be
        result = self._compute_beat_entries()
        self._cache_be = result
        self._cache_be_sig = sig
        return result

    def _compute_beat_entries(self) -> List[tuple]:
        """回傳 [(beat_index, start_ms), ...] 排序後的清單。"""
        # Support both XML-backed beat_data (self.root) and JSON-backed
        # `json_meta['beat_timings']`. Prefer XML beat_data when present.
        if self.root is None:
            # Try JSON metadata beat_timings (list of ms)
            jm = getattr(self, 'json_meta', None)
            if jm and isinstance(jm.get('beat_timings'), (list, tuple)) and jm.get('beat_timings'):
                try:
                    vals = sorted(int(float(x)) for x in jm.get('beat_timings'))
                except Exception:
                    return []
                raw_idxs = jm.get('beat_indices')
                if isinstance(raw_idxs, (list, tuple)) and len(raw_idxs) == len(vals):
                    try:
                        pairs = [(int(round(float(idx))), int(v)) for idx, v in zip(raw_idxs, vals)]
                        pairs.sort(key=lambda x: x[1])
                        return pairs
                    except Exception:
                        pass
                return [(i, int(v)) for i, v in enumerate(vals)]
            return []

        beat_root = self.root.find('beat_data')
        if beat_root is None:
            return []
        beats: List[tuple] = []
        for b in beat_root.findall('beat'):
            idx_el = b.find('index')
            ms_el  = b.find('start_timing_msec')
            if idx_el is None:
                idx_el = b.find('idx')
            if idx_el is None or ms_el is None:
                continue
            try:
                beats.append((int(float(idx_el.text)), int(float(ms_el.text))))
            except (ValueError, TypeError):
                pass
        beats.sort(key=lambda x: x[1])
        return beats
