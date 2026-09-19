"""PAN（本家 Nostalgia）譜面 XML 的規則：寫出時要遵守什麼、存完怎麼驗。

工作流程是 **JSON 為原始檔、XML 是給 PAN 的相容輸出**。JSON 存得下編輯器的
全部東西（Soft／Staccato、踏板、強弱記號、隱藏音符的寄主…），XML 只能放
PAN 認得的欄位。這裡集中放那些規則，寫檔（`NoteModel.save_xml`）、介面上的
功能開關、存檔後的驗證都照這一份。

規則來源：`PAN-001-2024102200_extracted/曲目資料結構與加歌.md` §3（反編譯讀
檔程式整理出來的），以及對 682 份官方譜的統計。檢查項目拿官方譜校正過——
官方全過才算數，所以像「index 不連號」「鍵號超出 1～28」這種官方自己就很
常見、讀檔時會自動修正的，不當成錯誤。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple, Union

# ── 音符類型 ─────────────────────────────────────────────────────────

LONG_BIT = 0x02
SLIDE_BIT = 0x04
SKIN_BIT = 0x08
TRILL_BIT = 0x40
PAN_TYPE_BITS = LONG_BIT | SLIDE_BIT | SKIN_BIT | TRILL_BIT

#: 編輯器自創、PAN 沒有的類型。3 特別危險：它等於 長押位元 | 1，PAN 會當成長押。
EDITOR_ONLY_TYPES: Dict[int, str] = {1: 'Soft', 3: 'Staccato'}


def pan_note_type(note_type: int) -> int:
    """編輯器的 note_type → PAN 寫得出去的值。PAN 沒有的類型換成 Tap。"""
    nt = int(note_type)
    if nt in EDITOR_ONLY_TYPES:
        return 0
    return nt & PAN_TYPE_BITS


def is_pan_note_type(note_type: int) -> bool:
    return pan_note_type(note_type) == int(note_type)


# ── 事件 ─────────────────────────────────────────────────────────────

#: type → (名稱, 說明)。1～8 的作用是推測（見 音效邏輯.md §11）。
EVENT_TYPES: Dict[int, Tuple[str, str]] = {
    0: ('速度（BPM）', '速度變化。檔案裡存 BPM × 100000'),
    1: ('殘響 1', '殘響參數'),
    2: ('殘響 2', '殘響參數'),
    3: ('回音 1', '回音參數'),
    4: ('回音 2', '回音參數'),
    5: ('回音延遲', '回音延遲，依目前 BPM 換算'),
    6: ('EQ 頻率', 'EQ 頻率'),
    7: ('EQ 增益', 'EQ 增益'),
    8: ('EQ Q 值', 'EQ 的 Q 值'),
    9: ('區段標記', '1 = 開始、0 = 結束（官方未使用）'),
}
TEMPO_EVENT = 0
BPM_SCALE = 100000

#: 新譜面開頭的音效事件。官方 682 份每份都在開頭寫了 type 1～8，取的是各自
#: 最常見的值（遊戲開歌時本來也會重設，寫出來是為了和官方檔長得一樣）。
DEFAULT_EFFECT_EVENTS: Dict[int, int] = {1: 120, 2: 11, 3: 24, 4: 20, 5: 24,
                                         6: 124, 7: 0, 8: 80}

# ── 音色 ─────────────────────────────────────────────────────────────

#: 官方 375/682 份用這個共用音色包，而且 track 從 1 開始編號（1 右手、2 左手）
DEFAULT_TRACK_NAME = 'key_apiano1'
DEFAULT_TRACKS: Tuple[Tuple[int, str], ...] = (
    (1, DEFAULT_TRACK_NAME), (2, DEFAULT_TRACK_NAME), (3, DEFAULT_TRACK_NAME))


def default_track_for_hand(hand: int) -> int:
    """官方的慣例：右手 track 1、左手 track 2、自動彈 track 3。"""
    return {0: 1, 1: 2}.get(int(hand), 3)


# ── 格式本身 ─────────────────────────────────────────────────────────

LIMITS = {'note': 20000, 'event': 10000, 'beat': 10000, 'track': 100,
          'velocity_zone': 100, 'sub_note': 100}

SECTIONS = ('header', 'note_data', 'event_data', 'beat_data', 'track_info',
            'velocity_zone_data')

#: 每一種節點底下的欄位與型別，照官方檔的順序
FIELDS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    'header': (('max_scale', 's32'), ('min_scale', 's32'), ('file_version', 's16'),
               ('first_bpm', 's64'), ('music_finish_time_msec', 's32')),
    'note': (('index', 's32'), ('start_timing_msec', 's32'), ('end_timing_msec', 's32'),
             ('gate_time_msec', 's32'), ('scale_piano', 'u8'), ('min_key_index', 's32'),
             ('max_key_index', 's32'), ('note_type', 's32'), ('hand', 's32'),
             ('key_kind', 's32'), ('param1', 's32'), ('param2', 's32'), ('param3', 's32')),
    'sub_note': (('start_timing_msec', 's32'), ('end_timing_msec', 's32'),
                 ('scale_piano', 'u8'), ('velocity', 'u8'), ('track_index', 's32')),
    'event': (('index', 's32'), ('start_timing_msec', 's32'), ('type', 's32'),
              ('value', 's64')),
    'beat': (('index', 's32'), ('start_timing_msec', 's32')),
    'track': (('index', 's32'), ('name', 'str')),
    'velocity_zone': (('index', 's32'), ('start_timing_msec', 's32'),
                      ('end_timing_msec', 's32'), ('velocity_type', 's32')),
}


def typed(parent: ET.Element, tag: str, value, type_attr: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = str(value)
    el.set('__type', type_attr)
    return el


# ── 驗證 ─────────────────────────────────────────────────────────────

@dataclass
class Issue:
    message: str
    count: int = 1


@dataclass
class Validation:
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self, limit: int = 12) -> str:
        lines = [f'・{i.message}' + (f'（{i.count} 處）' if i.count > 1 else '')
                 for i in self.issues[:limit]]
        if len(self.issues) > limit:
            lines.append(f'・…另外還有 {len(self.issues) - limit} 種問題')
        return '\n'.join(lines)


def _int(el: Optional[ET.Element]) -> Optional[int]:
    if el is None or el.text is None:
        return None
    try:
        return int(float(el.text))
    except ValueError:
        return None


def validate(source: Union[str, ET.Element]) -> Validation:
    """照 PAN 讀檔規則檢查一份譜面 XML（路徑或已解析的根節點）。"""
    found: Counter = Counter()
    order: List[str] = []

    def bad(message: str, n: int = 1) -> None:
        if message not in found:
            order.append(message)
        found[message] += n

    if isinstance(source, str):
        try:
            root = ET.parse(source).getroot()
        except ET.ParseError as exc:
            return Validation([Issue(f'XML 解析失敗：{exc}')])
    else:
        root = source

    for child in root:
        if child.tag not in SECTIONS:
            bad(f'多出 PAN 沒有的區段 <{child.tag}>')

    def check_fields(el: ET.Element, kind: str, required: Iterable[str] = ()) -> None:
        spec = dict(FIELDS[kind])
        for tag in required or spec:
            node = el.find(tag)
            if node is None:
                bad(f'<{kind}> 缺少 {tag}（整張譜面載入失敗）')
            elif node.get('__type') != spec[tag]:
                bad(f'<{kind}> 的 {tag} 型別應為 {spec[tag]}')
        for node in el:
            if node.tag not in spec and not (kind == 'note' and node.tag == 'sub_note_data'):
                bad(f'<{kind}> 多出 PAN 沒有的欄位 {node.tag}')
        if el.attrib:
            bad(f'<{kind}> 帶了屬性（官方檔沒有）')

    header = root.find('header')
    if header is None:
        bad('缺少 <header>')
    else:
        check_fields(header, 'header')

    tracks = root.findall('track_info/track')
    if not tracks:
        bad('缺少 <track_info>（子音符找不到音色）')
    if len(tracks) > LIMITS['track']:
        bad(f'音色 track 超過 {LIMITS["track"]}')
    track_ids = set()
    for tr in tracks:
        check_fields(tr, 'track')
        idx = _int(tr.find('index'))
        if idx is not None:
            track_ids.add(idx)
        if not (tr.findtext('name') or '').strip():
            bad('track 沒有名稱')

    notes = root.findall('note_data/note')
    if root.find('note_data') is None:
        bad('缺少 <note_data>')
    if len(notes) > LIMITS['note']:
        bad(f'音符超過 {LIMITS["note"]} 顆')
    last_start = None
    for note in notes:
        check_fields(note, 'note')
        start = _int(note.find('start_timing_msec'))
        end = _int(note.find('end_timing_msec'))
        if start is not None and last_start is not None and start < last_start:
            bad('音符沒有照時間排序（滑鍵會串錯）')
        if start is not None:
            last_start = start
        if start is not None and end is not None and end < start:
            bad('音符結束時間早於開始')
        nt = _int(note.find('note_type'))
        if nt is not None and not is_pan_note_type(nt):
            name = EDITOR_ONLY_TYPES.get(nt, str(nt))
            bad(f'note_type {name} 是 PAN 沒有的類型')
        hand = _int(note.find('hand'))
        if hand is not None and hand not in (0, 1, 2):
            bad('hand 不是 0～2（音符會被丟掉）')
        sp = _int(note.find('scale_piano'))
        if sp is not None and not 1 <= sp <= 88:
            bad('音符音高不在 1～88')
        subs = note.findall('sub_note_data/sub_note')
        if not subs:
            bad('音符沒有子音符（整顆被丟掉）')
            continue
        if len(subs) > LIMITS['sub_note']:
            bad(f'子音符超過 {LIMITS["sub_note"]} 個（會寫壞記憶體）')
        for sub in subs:
            check_fields(sub, 'sub_note')
            tr = _int(sub.find('track_index'))
            if tr is not None and track_ids and tr not in track_ids:
                bad('子音符的 track_index 對不到 <track_info>')
            vel = _int(sub.find('velocity'))
            if vel is not None and not 1 <= vel <= 127:
                bad('子音符力度不在 1～127')
        first = _int(subs[0].find('scale_piano'))
        if first is None or not 1 <= first <= 88:
            bad('第一個子音符的音高不在 1～88（整顆被丟掉）')

    events = root.findall('event_data/event')
    if root.find('event_data') is None:
        bad('缺少 <event_data>')
    if len(events) > LIMITS['event']:
        bad(f'事件超過 {LIMITS["event"]} 個')
    for ev in events:
        check_fields(ev, 'event')
        ty = _int(ev.find('type'))
        value = _int(ev.find('value'))
        if ty is not None and ty not in EVENT_TYPES:
            bad(f'事件類型 {ty} 不存在')
        if ty == TEMPO_EVENT and value is not None and value <= 0:
            bad('速度事件的 BPM 是 0 或負數')

    beats = root.findall('beat_data/beat')
    if root.find('beat_data') is None:
        bad('缺少 <beat_data>')
    if len(beats) > LIMITS['beat']:
        bad(f'拍子超過 {LIMITS["beat"]} 個')
    for i, beat in enumerate(beats):
        check_fields(beat, 'beat')
        if _int(beat.find('index')) != i:
            bad('拍子 index 沒有從 0 連續編號')
            break

    zones = root.findall('velocity_zone_data/velocity_zone')
    if len(zones) > LIMITS['velocity_zone']:
        bad(f'力度區超過 {LIMITS["velocity_zone"]} 個')
    for zone in zones:
        check_fields(zone, 'velocity_zone')

    return Validation([Issue(m, found[m]) for m in order])
