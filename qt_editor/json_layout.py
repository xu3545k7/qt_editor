"""譜面 JSON 的版面：要能用文字編輯器直接改。

`json.dump(..., indent=2)` 對這種資料是最糟的選擇：每個數字自己一行，所以一份
695 顆音符的譜是 **13559 行**，`beat_timings` 的 24 個時間點佔 26 行；鍵的順序又
是 dict 的插入順序，`bpm` 埋在中間、`notes` 排在拍號前面。要手動改一顆音符得先
捲過幾千行找到它，改完還看不出改了什麼。

這裡的版面規則：

* **一顆音符一行**。實測 12 萬顆音符，99% 攤成一行在 307 字元內，所以一個畫面
  看得到幾十顆，也能直接用 grep／排序／整行刪。超過 `MAX_INLINE` 的（帶很多
  sub_note 的顫音）才展開，這種不到 1%。
* **純數字的陣列排成段落**，不是一行一個數字。
* **鍵照固定分組排序**，組和組之間空一行：先曲子設定、再拍號、編輯器旗標、
  拍點、表情、PAN 附帶資料，最長的 `notes` 放最後。沒認識的鍵不會被丟掉，排在
  `notes` 前面。

輸出仍然是標準 JSON（空行在 JSON 裡就是空白），任何解析器都讀得回來。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Sequence, Tuple

#: 縮排幾格
INDENT = 2
#: 純量陣列（時間點、索引）一行排到幾個字元為止
WIDTH = 100
#: 一個物件攤成一行最多幾個字元；超過就展開成一鍵一行
MAX_INLINE = 400

#: 譜面 JSON 的鍵順序。一個 tuple 是一組，組之間空一行。
CHART_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ('bpm', 'first_bpm', 'music_finish_time_msec', 'pitch_encoding'),
    ('time_signature', 'time_signature_numerator', 'time_signature_denominator',
     'time_signature_changes'),
    ('editor_beat_entry_mode', 'editor_precise_beat_grid', 'beat_entry_quarters',
     'pan_lane_index_base', 'pan_header'),
    ('beat_timings', 'beat_indices'),
    ('pedal_data', 'dynamics_data', 'event_data'),
    ('pan_track_info', 'pan_velocity_zones'),
    ('notes',),
)


def _pad(level: int) -> str:
    return ' ' * (INDENT * level)


def _inline(value: Any) -> str:
    """攤成一行的樣子（和標準 json.dumps 一樣，只是逗號後面留一個空格）。"""
    return json.dumps(value, ensure_ascii=False, separators=(', ', ': '))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _render(value: Any, level: int, used: int = 0) -> str:
    """一個值的文字。開頭不含縮排（由呼叫端放），內部換行縮到 `level`。

    `used` 是這一行在這個值之前已經用掉的字元（縮排＋鍵名）。算寬度時要含進去，
    不然 `"beat_indices": [...]` 只量了陣列本身，整行就會超出 `WIDTH`。
    """
    if _is_scalar(value):
        return _inline(value)
    if isinstance(value, dict):
        return _render_dict(value, level, used)
    if isinstance(value, (list, tuple)):
        return _render_list(list(value), level, used)
    return _inline(value)                        # 不認識的型別交給 json 自己處理


def _row(key: Any, value: Any, level: int) -> str:
    """物件裡的一列：`"鍵": 值`。"""
    head = '%s%s: ' % (_pad(level), _inline(str(key)))
    return head + _render(value, level, len(head))


def _render_dict(value: Dict[str, Any], level: int, used: int = 0) -> str:
    if not value:
        return '{}'
    flat = _inline(value)
    if used + len(flat) <= MAX_INLINE:
        return flat
    rows = [_row(key, item, level + 1) for key, item in value.items()]
    return '{\n' + ',\n'.join(rows) + '\n' + _pad(level) + '}'


def _render_list(value: List[Any], level: int, used: int = 0) -> str:
    if not value:
        return '[]'
    flat = _inline(value)
    if used + len(flat) <= WIDTH:
        return flat
    if all(_is_scalar(item) for item in value):
        return '[\n' + _wrap(value, level + 1) + '\n' + _pad(level) + ']'
    rows = ['%s%s' % (_pad(level + 1), _render(item, level + 1, len(_pad(level + 1))))
            for item in value]
    return '[\n' + ',\n'.join(rows) + '\n' + _pad(level) + ']'


def _wrap(items: Sequence[Any], level: int) -> str:
    """純量排成段落：一行塞到 `WIDTH` 為止。"""
    pad = _pad(level)
    lines: List[str] = []
    row = pad
    for i, item in enumerate(items):
        piece = _inline(item) + (',' if i < len(items) - 1 else '')
        if row != pad and len(row) + 1 + len(piece) > WIDTH:
            lines.append(row)
            row = pad
        row += (' ' if row != pad else '') + piece
    lines.append(row)
    return '\n'.join(lines)


def order_keys(data: Dict[str, Any],
               groups: Iterable[Sequence[str]] = CHART_GROUPS) -> List[List[str]]:
    """把 `data` 的鍵照 `groups` 分組。沒認識的鍵排在最後一組前面，不會漏掉。"""
    groups = [list(g) for g in groups]
    known = {key for group in groups for key in group}
    out = [[key for key in group if key in data] for group in groups]
    extra = [key for key in data if key not in known]
    if extra:
        out.insert(max(0, len(out) - 1), extra)
    return [group for group in out if group]


def dumps(data: Dict[str, Any],
          groups: Iterable[Sequence[str]] = CHART_GROUPS) -> str:
    """整份譜面的文字（結尾有換行）。"""
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False, indent=INDENT) + '\n'
    if not data:
        return '{}\n'
    blocks: List[str] = []
    for group in order_keys(data, groups):
        rows = [_row(key, data[key], 1) for key in group]
        blocks.append(',\n'.join(rows))
    # 組和組之間空一行；逗號要留在前一組的最後一行
    body = ',\n\n'.join(blocks)
    return '{\n' + body + '\n}\n'
