"""
oplog.py
========
操作紀錄：把「使用者在編輯器裡做了什麼」寫成一份可以事後分析的 JSONL。

**為什麼要這個**：自動轉譜／修譜的演算法（smart_chart、和弦收窄、分手、
hold 尾巴、量化…）好不好，最誠實的指標不是我怎麼想，而是**使用者跑完工具
之後又手動改了什麼**。改動的方向就是演算法該往哪邊調。所以這裡記的不只是
「按了什麼」，還有每個動作**前後的譜面統計輪廓**，以及具體改了哪幾顆。

一行一個事件（JSONL），方便 grep 也方便 pandas 讀。

事件種類
--------
``session``  開檔／換檔：檔名、音符數、BPM、統計輪廓
``action``   一次編輯：動作名稱、耗時、增刪改的計數、抽樣的前後值、輪廓變化
``undo``     復原（**緊接在某個 action 之後的 undo＝那個動作做錯了**）
``mark``     其他值得記的事（存檔、匯出、播放、工具參數…）

怎麼掛上去的
------------
`NoteModel.push_history()` 是所有編輯的共同前置（69 個呼叫點），所以只要在
那裡結算上一個動作、開始下一個，就自動涵蓋全部，不用去改每一個工具。動作
名稱直接取呼叫端的函式名（`resolve_hold_tails_dialog`、`_finish_hold_tail_drag`
之類），已經夠好認了。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# 一次動作最多記幾顆具體的音符（其餘只進計數）。太多會讓檔案爆掉，
# 太少又看不出改動的樣態；30 顆足以看出「使用者往哪個方向修」。
MAX_SAMPLES = 30

# 單檔上限，超過就換下一個檔（-2、-3…），避免長期使用把磁碟塞爆。
MAX_BYTES = 20 * 1024 * 1024

# 同時發聲的判定窗（毫秒）。和譜面工具用的一致。
CHORD_WINDOW_MS = 10

_FIELDS = ('start', 'end', 'min_key', 'max_key', 'pitch', 'hand', 'note_type')


def _log_dir() -> str:
    base = (os.getcwd() if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'logs')


def _fingerprint(model) -> Dict[int, Tuple]:
    """id(note) -> 欄位 tuple。

    用物件身分當鍵：手動編輯是就地改欄位，物件會留著，所以配得起來；
    整份重建 notes 的工具會全部對不上，那就會顯示成「刪光再加回來」——
    那本身也是有意義的資訊（這個工具是重寫而不是微調）。
    """
    out: Dict[int, Tuple] = {}
    for n in getattr(model, 'notes_tree', ()) or ():
        try:
            out[id(n)] = (int(n.start), int(n.end), int(n.min_key), int(n.max_key),
                          n.pitch, int(n.hand), int(n.note_type))
        except Exception:                       # noqa: BLE001
            continue
    return out


def profile(model) -> Dict[str, Any]:
    """譜面的統計輪廓——調演算法時真正要看的那幾個數字。

    欄位挑的是官方語料分析時用過的那一組：鍵寬分佈、同時發聲的和弦大小、
    同手重疊率、左右手比例、音符種類。這樣「工具做了什麼」和「使用者改回
    什麼」可以放在同一組座標上比較。
    """
    notes = list(getattr(model, 'notes_tree', ()) or ())
    n = len(notes)
    out: Dict[str, Any] = {'notes': n}
    if not n:
        return out

    starts = [int(x.start) for x in notes]
    ends = [int(x.end) for x in notes]
    span_ms = max(ends) - min(starts)
    out['span_s'] = round(span_ms / 1000.0, 2)
    out['density_per_s'] = round(n / (span_ms / 1000.0), 3) if span_ms > 0 else 0.0

    widths: Dict[int, int] = {}
    types: Dict[int, int] = {}
    left = 0
    for x in notes:
        w = int(x.max_key) - int(x.min_key) + 1
        widths[w] = widths.get(w, 0) + 1
        types[int(x.note_type)] = types.get(int(x.note_type), 0) + 1
        if int(x.hand) == 1:
            left += 1
    out['width_hist'] = {str(k): v for k, v in sorted(widths.items())}
    out['mean_width'] = round(sum(k * v for k, v in widths.items()) / n, 3)
    out['type_hist'] = {str(k): v for k, v in sorted(types.items())}
    out['left_hand_ratio'] = round(left / n, 4)

    # 同時發聲的組：大小分佈，以及同手的鍵道重疊率
    order = sorted(range(n), key=lambda i: starts[i])
    groups: List[List[int]] = []
    cur = [order[0]]
    for i in order[1:]:
        if starts[i] - starts[cur[0]] <= CHORD_WINDOW_MS:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)

    sizes: Dict[int, int] = {}
    pairs = overlaps = 0
    for g in groups:
        sizes[len(g)] = sizes.get(len(g), 0) + 1
        for a in range(len(g)):
            for b in range(a + 1, len(g)):
                na, nb = notes[g[a]], notes[g[b]]
                if int(na.hand) != int(nb.hand):
                    continue
                pairs += 1
                if int(na.min_key) <= int(nb.max_key) and int(nb.min_key) <= int(na.max_key):
                    overlaps += 1
    out['chord_size_hist'] = {str(k): v for k, v in sorted(sizes.items())}
    out['same_hand_pairs'] = pairs
    out['overlap_ratio'] = round(overlaps / pairs, 4) if pairs else 0.0
    return out


def _diff(before: Dict[int, Tuple], after: Dict[int, Tuple]) -> Dict[str, Any]:
    """兩份 fingerprint 的差異：計數 + 抽樣。"""
    counts = {'added': 0, 'removed': 0, 'time': 0, 'length': 0,
              'lane': 0, 'hand': 0, 'type': 0, 'pitch': 0}
    samples: List[Dict[str, Any]] = []

    for key, new in after.items():
        old = before.get(key)
        if old is None:
            counts['added'] += 1
            if len(samples) < MAX_SAMPLES:
                samples.append({'op': 'add', 'after': list(new)})
            continue
        if old == new:
            continue
        changed = []
        if old[0] != new[0]:
            changed.append('time')
        if (old[1] - old[0]) != (new[1] - new[0]):
            changed.append('length')
        if old[2] != new[2] or old[3] != new[3]:
            changed.append('lane')
        if old[5] != new[5]:
            changed.append('hand')
        if old[6] != new[6]:
            changed.append('type')
        if old[4] != new[4]:
            changed.append('pitch')
        for c in changed:
            counts[c] += 1
        if changed and len(samples) < MAX_SAMPLES:
            samples.append({'op': '+'.join(changed),
                            'before': list(old), 'after': list(new)})

    for key, old in before.items():
        if key not in after:
            counts['removed'] += 1
            if len(samples) < MAX_SAMPLES:
                samples.append({'op': 'del', 'before': list(old)})

    counts = {k: v for k, v in counts.items() if v}
    return {'counts': counts, 'samples': samples}


def _under_test() -> bool:
    """跑測試時不要寫進正式的紀錄。

    測試套件會大量呼叫 `push_history()`，寫出來的東西和使用者真正的編輯混在
    一起——實測第一天的 781 筆裡有 387 筆是測試留下的，動作名稱還是
    `test_undo_restores_the_exact_file` 這種。分析的時候要先濾掉，不如一開始
    就別寫。設 `NOS_OPLOG=1` 可以強制打開。
    """
    if os.environ.get('NOS_OPLOG') == '1':
        return False
    if 'unittest' in sys.modules or 'pytest' in sys.modules:
        return True
    name = os.path.basename(sys.argv[0] or '').lower()
    return name.startswith('test') or name.startswith('pytest')


class OpLog:
    """單例。`oplog.enabled = False` 就整個停掉，開銷是一個布林判斷。"""

    def __init__(self) -> None:
        self.enabled = not _under_test()
        self._path: Optional[str] = None
        self._pending: Optional[Dict[str, Any]] = None
        self._session_id = ''
        self._next_params: Optional[Dict[str, Any]] = None

    def configure(self, enabled: bool) -> None:
        """套用偏好設定的開關，但**測試環境永遠關著**。

        不能讓呼叫端直接寫 `oplog.enabled = settings.get(...)`——MainWindow 在
        測試裡也會被建出來，那樣就把 `_under_test` 的保護整個蓋掉了（實測一次
        完整的測試跑會多寫 127 筆進正式紀錄）。
        """
        self.enabled = bool(enabled) and not _under_test()

    # ── 檔案 ────────────────────────────────────────────────────
    def path(self) -> str:
        if self._path is None:
            d = _log_dir()
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:                   # noqa: BLE001
                self.enabled = False
                return ''
            stem = time.strftime('oplog-%Y%m%d')
            candidate = os.path.join(d, stem + '.jsonl')
            n = 2
            while os.path.exists(candidate) and os.path.getsize(candidate) >= MAX_BYTES:
                candidate = os.path.join(d, '%s-%d.jsonl' % (stem, n))
                n += 1
            self._path = candidate
        return self._path

    def _write(self, record: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = self.path()
        if not path:
            return
        record.setdefault('t', round(time.time(), 3))
        record.setdefault('sid', self._session_id)
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:                       # noqa: BLE001
            # 紀錄壞掉絕對不能影響編輯——關掉就好
            self.enabled = False

    # ── 事件 ────────────────────────────────────────────────────
    def session(self, chart_path: str, model) -> None:
        """開檔／換檔。也把上一份的待結算動作收尾。"""
        if not self.enabled:
            return
        self.flush(None)
        self._session_id = '%s-%04x' % (time.strftime('%H%M%S'),
                                        (id(model) >> 4) & 0xFFFF)
        self._write({
            'kind': 'session',
            'chart': os.path.basename(chart_path or ''),
            'bpm': float(getattr(model, 'bpm', 0.0) or 0.0),
            'profile': profile(model),
        })

    def mark(self, what: str, **fields: Any) -> None:
        """存檔、匯出、播放…之類的單點事件。"""
        if not self.enabled:
            return
        rec = {'kind': 'mark', 'what': what}
        rec.update(fields)
        self._write(rec)

    def params(self, **fields: Any) -> None:
        """標註「接下來這個動作」的參數（工具對話框按下確定時呼叫）。"""
        if self.enabled:
            self._next_params = fields

    def on_edit(self, model, action: str) -> None:
        """`push_history()` 的鉤子：結算上一個動作，開始記下一個。"""
        if not self.enabled:
            return
        self.flush(model)
        self._pending = {
            'action': action,
            'started': time.time(),
            'before': _fingerprint(model),
            'profile_before': profile(model),
            'params': self._next_params,
        }
        self._next_params = None

    def flush(self, model) -> None:
        """把待結算的動作寫出去。model 為 None 表示拿不到後續狀態。"""
        pending, self._pending = self._pending, None
        if not self.enabled or pending is None:
            return
        if model is None:
            return
        after = _fingerprint(model)
        result = _diff(pending['before'], after)
        if not result['counts']:
            return                              # 什麼都沒改，不留噪音
        rec: Dict[str, Any] = {
            'kind': 'action',
            'action': pending['action'],
            'dt_ms': round((time.time() - pending['started']) * 1000.0, 1),
            'counts': result['counts'],
            'samples': result['samples'],
        }
        if pending['params']:
            rec['params'] = pending['params']
        before_p, after_p = pending['profile_before'], profile(model)
        rec['profile'] = after_p
        rec['profile_delta'] = {
            k: round(after_p[k] - before_p[k], 4)
            for k in ('notes', 'mean_width', 'left_hand_ratio',
                      'overlap_ratio', 'density_per_s')
            if isinstance(before_p.get(k), (int, float))
            and isinstance(after_p.get(k), (int, float))
            and before_p[k] != after_p[k]
        }
        self._write(rec)

    def undone(self, model, redo: bool = False) -> None:
        """復原／重做。緊接在 action 之後的 undo 是最強的負面訊號。"""
        if not self.enabled:
            return
        self.flush(model)
        self._write({'kind': 'redo' if redo else 'undo',
                     'profile': profile(model)})


oplog = OpLog()
