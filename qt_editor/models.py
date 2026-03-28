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

import copy
import json
import math
import xml.etree.ElementTree as ET
import xml.dom.minidom
from typing import Any, Dict, List, Optional, Tuple

TOTAL_GAME_KEYS: int = 28

# note_type 整數 → 遊戲 JSON 所用的 type 字串
_NOTE_TYPE_STR: Dict[int, str] = {0: 'tap', 1: 'soft', 2: 'hold', 3: 'staccato'}


# ---------------------------------------------------------------------------
# GNote
# ---------------------------------------------------------------------------

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
        self.sub_elems: List[ET.Element] = []

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
        self.hand     = 1 if g(elem, 'hand', 0) else 0

        # track（選擇性）
        has_track = (elem.find('track') is not None) or (elem.get('track') is not None)
        self.track = g(elem, 'track', None) if has_track else None

        # pitch（選擇性）
        has_pitch = (elem.find('scale_piano') is not None) or (elem.get('scale_piano') is not None)
        self.pitch = g(elem, 'scale_piano', None) if has_pitch else None

        # sub notes
        sroot = elem.find('sub_note_data')
        if sroot is not None:
            self.sub_elems = list(sroot)

    # ------------------------------------------------------------------
    # 從 JSON dict 建立
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: Dict[str, Any], idx: int) -> 'GNote':
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

        note.note_type = int(d.get('note_type', 0))
        note.hand      = int(d.get('hand', 0))
        note.track     = int(d['track']) if d.get('track') not in (None, '') else None
        raw_pitch      = d.get('scale_piano', d.get('pitch', None))
        note.pitch     = int(raw_pitch) if raw_pitch not in (None, '') else None
        return note

    # ------------------------------------------------------------------
    # 寫回 XML element
    # ------------------------------------------------------------------

    def apply_back(self) -> None:
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

        pairs = [
            ('start_timing_msec', self.start),
            ('end_timing_msec',   self.end),
            ('gate_time_msec',    self.gate),
            ('min_key_index',     self.min_key),
            ('max_key_index',     self.max_key),
            ('note_type',         self.note_type),
            ('hand',              self.hand),
        ]
        for tag, val in pairs:
            set_text(tag, val)
            set_attr(tag, val)

        if self.track is not None:
            set_text('track', self.track)
            set_attr('track', self.track)

        if self.pitch is not None:
            set_text('scale_piano', self.pitch)
            set_attr('scale_piano', self.pitch)

    # ------------------------------------------------------------------
    # 序列化為 JSON dict（存檔用）
    # ------------------------------------------------------------------

    def to_json_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            'startTime':  self.start,
            'endTime':    self.end,
            'gateTime':   self.gate if self.gate else (self.end - self.start),
            'startLane':  self.min_key,
            'endLane':    self.max_key,
            'pitch':      self.pitch,
            'type':       _NOTE_TYPE_STR.get(self.note_type, 'tap'),
            'note_type':  self.note_type,
            'hand':       self.hand,
        }
        if self.pitch is None:
            del d['pitch']
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
        self.file_format: str = 'xml'       # 'xml' | 'json'
        self.current_file: Optional[str] = None
        self.json_meta: Dict[str, Any] = {}

        # --- 音樂參數
        self.bpm: float = 120.0
        self.beats_per_bar: int = 4
        self.time_sig_denominator: int = 4
        self.beat_offset_ms: float = 0.0
        self.music_end_ms: float = 0.0
        # [(ms, numerator, denominator), ...] sorted by ms — empty = single time sig
        self.time_sig_changes: List[Tuple[int, int, int]] = []

        # --- 音符資料
        self.notes_tree: List[GNote] = []  # 唯一資料來源
        self.notes: List[GNote] = []       # 排序後的顯示快取

        # --- Undo 歷史
        self.undo_stack: List[List[GNote]] = []
        self.undo_limit: int = 50

        # --- 狀態旗標
        self.dirty: bool = False

    # ------------------------------------------------------------------
    # Undo 歷史
    # ------------------------------------------------------------------

    def push_history(self) -> None:
        # Snapshot a fuller model state so undo can revert beat timings and metadata too
        snap: Dict[str, Any] = {
            'notes_tree': copy.deepcopy(self.notes_tree),
            'time_sig_changes': copy.deepcopy(self.time_sig_changes),
            'json_meta': copy.deepcopy(self.json_meta),
            'music_end_ms': float(self.music_end_ms),
            'bpm': float(self.bpm),
            'beats_per_bar': int(self.beats_per_bar),
            'time_sig_denominator': int(self.time_sig_denominator),
            'root_xml': ET.tostring(self.root, encoding='unicode') if self.root is not None else None,
            'note_data_xml': None,
        }
        if self.root is not None:
            nd = self.root.find('note_data')
            if nd is not None:
                snap['note_data_xml'] = ET.tostring(nd, encoding='unicode')
        self.undo_stack.append(snap)
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.dirty = True

    def undo(self) -> bool:
        """退回上一個快照；回傳是否成功。"""
        if not self.undo_stack:
            return False
        snap = self.undo_stack.pop()
        # restore notes
        self.notes_tree = copy.deepcopy(snap.get('notes_tree', []))
        # restore metadata
        self.time_sig_changes = copy.deepcopy(snap.get('time_sig_changes', []))
        self.json_meta = copy.deepcopy(snap.get('json_meta', {}))
        self.music_end_ms = float(snap.get('music_end_ms', 0.0))
        self.bpm = float(snap.get('bpm', self.bpm))
        self.beats_per_bar = int(snap.get('beats_per_bar', self.beats_per_bar))
        self.time_sig_denominator = int(snap.get('time_sig_denominator', self.time_sig_denominator))
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

        # If we have restored an XML root, rebuild notes_tree from its note_data
        note_data_xml = snap.get('note_data_xml')
        if self.root is not None and (self.root.find('note_data') is not None or note_data_xml is not None):
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
                # try to preserve original keys/pitch by matching starts/ends from snapshot
                snap_notes = snap.get('notes_tree', [])
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
                # fallback to stored deep-copied notes_tree
                self.notes_tree = copy.deepcopy(snap.get('notes_tree', []))
        else:
            # JSON-only or no XML root: restore deepcopy of notes_tree
            self.notes_tree = copy.deepcopy(snap.get('notes_tree', []))

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

    # ------------------------------------------------------------------
    # 載入 XML
    # ------------------------------------------------------------------

    def load_xml(self, path: str) -> None:
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()
        self.file_format = 'xml'
        self.json_meta = {}
        self.current_file = path

        self._parse_xml_header()

        nd = self.root.find('note_data')
        if nd is None:
            raise ValueError('找不到 <note_data> 節點')

        self.notes_tree = [GNote(ne, i) for i, ne in enumerate(nd.findall('note'))]
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
        self.current_file = path
        self.json_meta = {k: v for k, v in data.items() if k != 'notes'} if isinstance(data, dict) else {}

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
        self.notes_tree = [GNote.from_dict(d, i) for i, d in enumerate(notes_list)]
        self.rebuild_display_cache()
        self.undo_stack.clear()
        self.dirty = False

    # ------------------------------------------------------------------
    # 存檔
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 輔助：從記憶體欄位建立 XML note 元素
    # ------------------------------------------------------------------

    @staticmethod
    def _build_note_element(n: 'GNote', idx: int) -> ET.Element:
        """從 GNote 記憶體欄位建立標準格式的 XML <note> 元素。"""
        def add_el(parent: ET.Element, tag: str, val, type_attr: str) -> ET.Element:
            el = ET.SubElement(parent, tag)
            el.text = str(val)
            el.set('__type', type_attr)
            return el

        note_el = ET.Element('note')
        note_el.set('start_timing_msec', str(n.start))
        note_el.set('end_timing_msec',   str(n.end))
        note_el.set('gate_time_msec',    str(n.gate))
        note_el.set('index',             str(idx))
        note_el.set('min_key_index',     str(n.min_key))
        note_el.set('max_key_index',     str(n.max_key))
        note_el.set('note_type',         str(n.note_type))
        note_el.set('hand',              str(n.hand))
        if n.pitch is not None:
            note_el.set('scale_piano', str(n.pitch))

        add_el(note_el, 'index',              idx,        's32')
        add_el(note_el, 'start_timing_msec',  n.start,    's32')
        add_el(note_el, 'end_timing_msec',    n.end,      's32')
        add_el(note_el, 'gate_time_msec',     n.gate,     's32')
        if n.pitch is not None:
            add_el(note_el, 'scale_piano',    n.pitch,    'u8')
        add_el(note_el, 'min_key_index',      n.min_key,  's32')
        add_el(note_el, 'max_key_index',      n.max_key,  's32')
        add_el(note_el, 'note_type',          n.note_type,'s32')
        add_el(note_el, 'hand',               n.hand,     's32')
        add_el(note_el, 'key_kind',           0,          's32')
        add_el(note_el, 'param1',             0,          's32')
        add_el(note_el, 'param2',             0,          's32')
        add_el(note_el, 'param3',             0,          's32')
        add_el(note_el, 'measure_index',      0,          's32')
        return note_el

    def save_xml(self, path: Optional[str] = None) -> None:
        if path is None:
            path = self.current_file
        if path is None:
            raise ValueError('未指定存檔路徑')
        assert self.root is not None and self.tree is not None

        # ── 永遠從 notes_tree 重建 note_data ────────────────────────
        # （可正確處理刪除、新增、MIDI 匯入後的儲存）
        nd = self.root.find('note_data')
        if nd is None:
            nd = ET.SubElement(self.root, 'note_data')
        else:
            for child in list(nd):
                nd.remove(child)

        sorted_notes = sorted(self.notes_tree, key=lambda _n: (_n.start, _n.min_key))
        for i, n in enumerate(sorted_notes):
            if n.elem is not None:
                # 更新既有 XML 元素後重新掛入
                n.apply_back()
                n.elem.set('index', str(i))
                idx_child = n.elem.find('index')
                if idx_child is not None:
                    idx_child.text = str(i)
                nd.append(n.elem)
            else:
                nd.append(self._build_note_element(n, i))

        raw = ET.tostring(self.root, encoding='unicode')
        pretty_bytes = xml.dom.minidom.parseString(raw).toprettyxml(indent='  ', encoding='utf-8')
        lines = [l for l in pretty_bytes.decode('utf-8').splitlines() if l.strip()]
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

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
        meta['music_finish_time_msec'] = int(self.music_end_ms)
        meta['notes'] = [n.to_json_dict() for n in
                         sorted(self.notes_tree, key=lambda n: (n.start, n.min_key))]

        # time_signature string (e.g. "4/4") + numerator / denominator
        _fnum = self.beats_per_bar
        _fden = self.time_sig_denominator
        if self.time_sig_changes:
            _, _fnum, _fden = self.time_sig_changes[0]
        meta['time_signature']             = f'{_fnum}/{_fden}'
        meta['time_signature_numerator']   = _fnum
        meta['time_signature_denominator'] = _fden

        # beat_timings：從 XML beat_data 讀取，去重後排序
        if 'beat_timings' not in meta:
            entries = self.get_beat_entries()
            if entries:
                meta['beat_timings'] = sorted(set(ms for (_, ms) in entries))

        # time_signature_changes
        if 'time_signature_changes' not in meta and self.time_sig_changes:
            meta['time_signature_changes'] = [
                {'time_ms': ms, 'numerator': num, 'denominator': den}
                for ms, num, den in self.time_sig_changes
            ]

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        self.current_file = path
        self.dirty = False

    # ------------------------------------------------------------------
    # beat_data 存取（供 TimeMapper 使用）
    # ------------------------------------------------------------------

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
        """偵測 beat_data 每小節有幾個 entry。
        - per-bar 格式（create_new 建立）：entry 間距 ≈ 一小節 ms → 回傳 1
        - per-beat 格式（原始遊戲檔）：entry 間距 ≈ 一拍 ms → 回傳 beats_per_bar
        判斷方式：比較 unit_ms 與理論 beat_ms / bar_ms，選較接近的那個。"""
        bpb = max(1, self.beats_per_bar)
        if bpb <= 1:
            return 1
        beats = self.get_beat_entries()
        if len(beats) < 2:
            return 1
        # 取多個相鄰間距的中位數，避免首尾異常值干擾
        diffs = [beats[i+1][1] - beats[i][1]
                 for i in range(min(8, len(beats)-1))
                 if beats[i+1][1] > beats[i][1]]
        if not diffs:
            return 1
        diffs.sort()
        unit_ms = diffs[len(diffs) // 2]   # 中位數
        bpm = max(1.0, self.bpm)
        num = max(1, self.beats_per_bar)
        den = max(1, self.time_sig_denominator)
        beat_ms = 60000.0 / bpm                          # 一拍
        bar_ms  = num * 4.0 * 60000.0 / (den * bpm)     # 一小節
        # 選更接近的
        dist_beat = abs(unit_ms - beat_ms)
        dist_bar  = abs(unit_ms - bar_ms)
        return bpb if dist_beat < dist_bar else 1

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
        model.bpm           = max(1.0, float(bpm))
        model.beats_per_bar = max(1, int(beats_per_bar))
        model.beat_offset_ms = 0.0
        model.music_end_ms  = duration_sec * 1000.0

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
        _add(hdr, 'first_bpm',                    int(model.bpm),         's64')
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
        beats = self.get_beat_entries()
        if not beats:
            bar_ms = self._bar_ms()
            return max(0, int(ms / max(1.0, bar_ms)))
        epb = self.entries_per_bar
        for i, (_bidx, beat_ms) in enumerate(beats):
            if beat_ms > ms:
                return max(0, (i - 1) // epb)
        return max(0, (len(beats) - 1) // epb)

    def count_measures(self) -> int:
        """回傳目前總小節數。"""
        epb = max(1, self.entries_per_bar)
        return len(self.get_beat_entries()) // epb

    def add_measure(self, new_bpm: Optional[float] = None) -> None:
        """在譜面末尾新增一個小節（per-bar 格式加1個entry；per-beat 格式加 entries_per_bar 個 entry）。"""
        if self.root is None:
            return
        bpm_use  = float(new_bpm) if new_bpm and new_bpm > 0 else self.bpm
        bar_ms   = self._bar_ms(bpm_use)
        epb      = self.entries_per_bar  # 1 or beats_per_bar
        beat_ms_each = bar_ms / max(1, epb)   # per-entry ms spacing

        beat_root = self.root.find('beat_data')
        if beat_root is None:
            beat_root = ET.SubElement(self.root, 'beat_data')

        existing = self.get_beat_entries()   # [(idx, ms), ...]
        if existing:
            last_idx, last_ms = existing[-1]
        else:
            last_idx, last_ms = -1, 0

        final_ms = last_ms
        for k in range(epb):
            new_idx = last_idx + 1 + k
            new_ms  = last_ms + int(round(beat_ms_each * (k + 1)))
            beat_el = ET.SubElement(beat_root, 'beat')
            idx_el  = ET.SubElement(beat_el, 'index')
            idx_el.set('__type', 's32')
            idx_el.text = str(new_idx)
            ms_el = ET.SubElement(beat_el, 'start_timing_msec')
            ms_el.set('__type', 's32')
            ms_el.text = str(new_ms)
            final_ms = new_ms

        # 更新 music_finish_time_msec
        self.music_end_ms = max(self.music_end_ms, float(final_ms))
        fin_el = self.root.find('header/music_finish_time_msec')
        if fin_el is not None:
            fin_el.text = str(int(self.music_end_ms))
        self.dirty = True

    def delete_measure(self, measure_idx: int) -> int:
        """刪除第 measure_idx 小節（0-indexed）以及其中所有音符，
        並將後續音符/拍子時間往前平移填補間距。
        回傳刪除的音符數。"""
        if self.root is None:
            return 0
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

        # 3. 更新 beat_data：刪除 measure_idx 對應的 entries（epb 個）
        beat_root = self.root.find('beat_data')
        if beat_root is not None:
            all_beats = self.get_beat_entries()   # [(idx, ms), ...]
            epb = self.entries_per_bar
            del_start = measure_idx * epb
            del_end   = del_start + epb

            # 重建 beat 清單：跳過 [del_start, del_end)，後續 ms 往前平移、index 重排
            new_beats: List[Tuple[int, int]] = []
            for i, (_bidx, bms) in enumerate(all_beats):
                if i < del_start:
                    new_beats.append((i, bms))
                elif i < del_end:
                    pass   # 刪除這些 entry
                else:
                    new_beats.append((len(new_beats), max(0, bms - dur_ms)))

            # 清空舊内容
            for child in list(beat_root):
                beat_root.remove(child)

            for bidx, bms in new_beats:
                beat_el = ET.SubElement(beat_root, 'beat')
                idx_el  = ET.SubElement(beat_el, 'index')
                idx_el.set('__type', 's32')
                idx_el.text = str(bidx)
                ms_el = ET.SubElement(beat_el, 'start_timing_msec')
                ms_el.set('__type', 's32')
                ms_el.text = str(int(bms))

        # 4. 更新 music_finish_time_msec
        new_end = max(0.0, self.music_end_ms - dur_ms)
        self.music_end_ms = new_end
        fin_el = self.root.find('header/music_finish_time_msec')
        if fin_el is not None:
            fin_el.text = str(int(new_end))

        self.rebuild_display_cache()
        self.dirty = True

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
            if tms <= start_ms:
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

        # sync full time_signature_changes into XML / JSON metadata
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

        # 2. 更新 beat_data 或 json beat_timings
        beat_root = None if self.root is None else self.root.find('beat_data')
        if beat_root is not None:
            all_beats = self.get_beat_entries()
            entry_s = measure_idx * self.entries_per_bar
            entry_e = entry_s + self.entries_per_bar
            entry_s = max(0, entry_s)
            entry_e = min(len(all_beats), entry_e)
            old_entry_count = max(1, entry_e - entry_s)
            new_entry_count = old_entry_count

            new_beats: List[Tuple[int, int]] = []
            for i in range(0, entry_s):
                new_beats.append((i, all_beats[i][1]))

            for k in range(new_entry_count):
                frac = (k / float(new_entry_count)) if new_entry_count > 0 else 0.0
                bms = start_ms + int(round(new_dur * frac))
                new_beats.append((len(new_beats), bms))

            for i in range(entry_e, len(all_beats)):
                bms = all_beats[i][1] + delta
                new_beats.append((len(new_beats), int(bms)))

            for child in list(beat_root):
                beat_root.remove(child)
            for bidx, bms in new_beats:
                beat_el = ET.SubElement(beat_root, 'beat')
                idx_el = ET.SubElement(beat_el, 'index')
                idx_el.set('__type', 's32')
                idx_el.text = str(bidx)
                ms_el = ET.SubElement(beat_el, 'start_timing_msec')
                ms_el.set('__type', 's32')
                ms_el.text = str(int(bms))
        else:
            jm = getattr(self, 'json_meta', None)
            if jm and isinstance(jm.get('beat_timings'), (list, tuple)) and jm.get('beat_timings'):
                try:
                    vals = sorted(int(float(x)) for x in jm.get('beat_timings'))
                except Exception:
                    vals = []
                if vals:
                    epb = self.entries_per_bar
                    entry_s = measure_idx * epb
                    entry_e = entry_s + epb
                    entry_s = max(0, entry_s)
                    entry_e = min(len(vals), entry_e)
                    old_entry_count = max(1, entry_e - entry_s)
                    new_entry_count = old_entry_count

                    new_vals: List[int] = []
                    for i in range(0, entry_s):
                        new_vals.append(int(vals[i]))
                    for k in range(new_entry_count):
                        frac = (k / float(new_entry_count)) if new_entry_count > 0 else 0.0
                        new_bms = int(start_ms + int(round(new_dur * frac)))
                        new_vals.append(new_bms)
                    for i in range(entry_e, len(vals)):
                        new_vals.append(int(vals[i] + delta))

                    jm['beat_timings'] = new_vals
                    self.json_meta = jm

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

    def set_measure_bpm(self, measure_idx: int, new_bpm: float, uniform: bool = False, mode: str = 'scale') -> None:
        """修改第 measure_idx 小節的 BPM。
        mode='scale'（預設）：等比例縮放小節內音符時間，並平移後續音符與拍子（原有行為）。
        mode='trim'：直接以 new_bpm 計算小節長度，保留小節內音符的起始時間；若音符超出新小節長度，將其 end 裁剪到小節尾（若裁剪後 end<=start 則刪除）。
        uniform 參數只影響 beat_timings 在 JSON 模式下如何重排（保留原邏輯）。"""
        # Allow JSON-only charts (no XML root) by operating on in-memory
        # beat timings stored in `json_meta['beat_timings']` when present.
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
        if mode == 'scale':
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
        else:
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
            epb = self.entries_per_bar
            entry_s = measure_idx * epb
            entry_e = entry_s + epb
            new_beats: List[Tuple[int, int]] = []
            for i, (bidx, bms) in enumerate(all_beats):
                if i < entry_s:
                    new_beats.append((bidx, bms))
                elif i < entry_e:
                            # 插值重算本小節內各 entry 的時間（beat starts）
                            if mode == 'scale':
                                frac = ((i - entry_s) / float(epb)) if epb > 0 else 0.0
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
                    epb = self.entries_per_bar
                    entry_s = measure_idx * epb
                    entry_e = entry_s + epb
                    new_vals: List[int] = []
                    for i, bms in enumerate(vals):
                        if i < entry_s:
                            new_vals.append(int(bms))
                        elif i < entry_e:
                            if mode == 'scale':
                                if uniform:
                                    # 均分小節：依 index 等距分配（beat starts）
                                    frac = ((i - entry_s) / float(epb)) if epb > 0 else 0.0
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

    def get_beat_entries(self) -> List[tuple]:
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
