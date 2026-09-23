"""輸出 Hiraeth 歌曲包（ZIP）。

Hiraeth 是 PAN（本家）的歌曲管理版，用它的「導入歌曲包」匯入 ZIP。它的
importer（`tools/import_tools/importer.py`）比 PAN 讀檔程式嚴格很多，這裡的
轉換規則就是 2026-09-15 把整個曲庫 96 包成功匯入的那一套：

- 譜面：music_score XML，header min_scale=1、max_scale=88，長度和音訊差 ≤ 2 秒
- 音符只能 Tap／Long／Slide／Trill（Soft／Staccato → Tap），手只能 0／1。原版 importer
  不收顫音，本機這份 Hiraeth 的 importer 已經改成收 64（見 HIRAETH_NOTE_TYPES）
  （自動彈的音符拿掉），每個 sub_note 的 velocity 必須是 0——**不發按鍵音**，
  聲音全部來自音樂檔；track 全部 key_apiano1
- 音樂：PCM16 雙聲道 16000／22050／44100／48000Hz、≤ 128MiB。因為沒有按鍵音，
  有鋼琴音軌的曲子要把**背景音樂和鋼琴音軌疊在一起**；沒有背景音樂的只用鋼琴
  音軌，連鋼琴音軌都沒有就用內建音源把譜面算出來
- 長押長度照偏好設定縮成官方長度（預設 80%，從官方 XML 來的不縮）
- 譜面先跑一次「處理長條尾端」（最小間距預設 80ms），手才來得及放開
- 封面 PNG 74～4096 像素；曲名、作者要能用 Shift_JIS X 0213 編碼
- 難度只有 normal／hard／expert／real，real 等級 1～3.5（半級）、其他 1～15
- ZIP 根目錄只能有 song.json、music.wav、preview.wav、cover.png 與四種難度 xml

`validate_package()` 照 importer 的檢查逐條實作，輸出前先自己驗過。
"""

from __future__ import annotations

import audioop
import bisect
import collections
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import wave
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import loudness
from .ui_text import tr

SLOTS = ('normal', 'hard', 'expert', 'real')
#: Hiraeth 收的音符類型：Tap、Long、Slide、Trill(0x40)。
#:
#: 原版 importer 只收 0／2／4，所以以前輸出時把顫音改成長押 —— 玩家在套件裡看到的是
#: 一條長條，不是顫音。本機的 importer 已經加上 64（本家遊戲本來就有顫音），這裡
#: 就照原樣輸出。
HIRAETH_NOTE_TYPES = (0, 2, 4, 64)
AUDIO_RATES = (16000, 22050, 44100, 48000)
MAX_WAV_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_BYTES = 300 * 1024 * 1024
PACKAGE_FILES = {'song.json', 'music.wav', 'preview.wav', 'cover.png',
                 'normal.xml', 'hard.xml', 'expert.xml', 'real.xml'}
SKIP_DIFFICULTIES = {'test'}
#: 輸出前「處理長條尾端」的最小間距。使用者指定至少 80ms。
#: 傳 0（或更小）給 hold_gap_ms ＝ 完全不處理長條尾端，譜面照原樣輸出。
DEFAULT_HOLD_GAP_MS = 80
MIN_HOLD_GAP_MS = 80
NO_HOLD_PROCESSING = 0


def hold_gap_setting(settings: Any) -> int:
    """偏好設定裡的長條尾端間距；「處理長條尾端」沒勾就回傳 0（不處理）。"""
    if not bool(settings.get('hiraeth_process_hold_tails', True)):
        return NO_HOLD_PROCESSING
    return max(MIN_HOLD_GAP_MS, int(settings.get('hiraeth_hold_gap_ms', DEFAULT_HOLD_GAP_MS)))
TUTORIAL_PREFIX = '新手教學'
#: 歌曲包結尾最多留多久：最後一顆音符（或最後的聲音）之後留這麼多就收掉。
#:
#: 這裡不收的話，譜面的 `music_finish_time_msec` 會是整個音訊的長度 —— 玩家打完
#: 最後一顆音符還要對著空畫面等音訊跑完（實測 LaVI 等了 43 秒、Testify 40 秒）。
PACKAGE_TAIL_MS = 1500
#: 剪進還有聲音的地方時淡出多久
PACKAGE_FADE_MS = 400

#: 譜面開頭要空幾小節。量過官方 682 份 PAN 譜：拍格一律從 0 開始，第一顆音符
#: 落在第 4 拍（中位數，＝ 4/4 的一整小節），69% 至少空一小節，97% 至少空 1 秒。
#: 太早出第一顆音玩家根本來不及反應，所以輸出 ZIP 時預設補到一小節。
DEFAULT_LEAD_IN_BARS = 1

#: 樂曲評論（song.json 的 slogan，遊戲選曲畫面那一句）Hiraeth 限 160 字
MAX_SLOGAN = 160

#: Shift_JIS X 0213 編不了的簡體字，換成日文／繁體字形（曲庫實際遇到的）
CHAR_FIX = {'东': '東', '乐': '楽', '纪': '紀', '聂': '聶'}

Renderer = Callable[[Any], Tuple[bytes, int]]


# ── 小工具 ───────────────────────────────────────────────────────────

def sjis_safe(text: str) -> str:
    # 換行、tab、連續空白收成一個空格（register.json 裡真的有曲名帶換行的）
    text = ' '.join(unicodedata.normalize('NFC', text or '').split())
    out = []
    for ch in text:
        ch = CHAR_FIX.get(ch, ch)
        try:
            ch.encode('shift_jisx0213')
            out.append(ch)
        except UnicodeEncodeError:
            out.append('?')
    return ''.join(out).strip()


def clean_slogan(text: str) -> str:
    """樂曲評論：收成一行、Shift_JIS 編得了、不超過 160 字。"""
    return sjis_safe(text)[:MAX_SLOGAN]


def read_slogan(song_dir: str) -> str:
    """樂曲資料夾 register.json 裡存的評論（沒有就空字串）。"""
    try:
        with open(os.path.join(song_dir, 'register.json'), encoding='utf-8-sig') as fh:
            return str(json.load(fh).get('slogan') or '')
    except (OSError, ValueError):
        return ''


def write_slogan(song_dir: str, slogan: str) -> bool:
    """把評論存回 register.json（遊戲讀 register 時不認得這個欄位，不影響）。"""
    path = os.path.join(song_dir, 'register.json')
    try:
        with open(path, encoding='utf-8-sig') as fh:
            register = json.load(fh)
    except (OSError, ValueError):
        return False
    slogan = (slogan or '').strip()
    if str(register.get('slogan') or '') == slogan:
        return False
    if slogan:
        register['slogan'] = slogan
    else:
        register.pop('slogan', None)
    # 先寫暫存檔再改名：輸出對使用者來說是「只是讀一讀」的動作，不該有任何
    # 機會把 register.json 寫成半截的。
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(register, fh, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    return True


def package_id(key: str, variant: str = '') -> str:
    """穩定的 package_id：同一首歌每次輸出都一樣，Hiraeth 才會當成「更新」。"""
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', key)
                  .encode('ascii', 'ignore').decode().lower()).strip('-')[:36]
    digest = hashlib.sha1((key + '|' + variant).encode('utf-8')).hexdigest()[:8]
    return 'nostalgia-clone.' + (slug + '-' if slug else '') + digest


def default_version() -> int:
    """版本號一律遞增（Hiraeth 更新同一包要求 version 變大）：用分鐘時間戳。"""
    return int(time.time() // 60)


#: REAL 顯示等級。官方 music_list 的 level_real 實際出現 9～15：
#: 9、10、11 都是 REAL 1（**沒有 1.5**），12 → 2、13 → 2.5、14 → 3、15 → 3.5。
REAL_LEVELS = (1, 2, 2.5, 3, 3.5)
_REAL_BY_LEVEL = {12: 2, 13: 2.5, 14: 3}


def real_level(level: int) -> float:
    """遊戲的難度等級（整數）→ REAL 顯示等級。

    ≤11 → 1、12 → 2、13 → 2.5、14 → 3、15 以上 → 3.5。整數回傳 int，
    song.json 才寫成 `2` 而不是 `2.0`。
    """
    value = int(round(float(level or 0)))
    if value <= 11:
        return 1
    if value >= 15:
        return 3.5
    return _REAL_BY_LEVEL[value]


def resolve_resource(song_dir: str, resource: Optional[str], exts: Tuple[str, ...]) -> Optional[str]:
    """register.json 裡的 `songs/<曲名>/<子資料夾>/<檔名>`（無副檔名）→ 實際檔案。"""
    if not resource:
        return None
    rel = resource.split('/', 2)[2] if resource.startswith('songs/') else resource
    parts = rel.split('/')
    folder = os.path.join(song_dir, *parts[:-1])
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if stem == parts[-1] and ext.lower() in exts:
            return os.path.join(folder, name)
    return None


# ── 音訊 ─────────────────────────────────────────────────────────────

def read_wav(path: str) -> Tuple[bytes, int]:
    """任何 PCM WAV → 16-bit 雙聲道、Hiraeth 收的取樣率。"""
    with wave.open(path, 'rb') as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        pcm = w.readframes(w.getnframes())
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels == 1:
        pcm = audioop.tostereo(pcm, 2, 1, 1)
    elif channels != 2:
        raise ValueError(tr('不支援 %d 聲道的音訊') % channels)
    if rate not in AUDIO_RATES:
        pcm, _ = audioop.ratecv(pcm, 2, 2, rate, 44100, None)
        rate = 44100
    return pcm, rate


def write_wav(path: str, pcm: bytes, rate: int) -> Tuple[int, float]:
    if len(pcm) > MAX_WAV_BYTES - 1024:
        pcm, _ = audioop.ratecv(pcm, 2, 2, rate, 22050, None)
        rate = 22050
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return rate, len(pcm) / 4 / rate


def mix_pcm(base: bytes, base_rate: int, layer: bytes, layer_rate: int) -> Tuple[bytes, int, float]:
    """把鋼琴音軌疊到背景音樂上。回傳 (pcm, 取樣率, 為了不破音降了幾 dB)。

    兩邊都從 0 秒對齊（鋼琴音軌本來就是照譜面時間算的）。短的那邊補靜音。
    先用一半音量加起來量出真正的峰值，會破音才整體降下來，不會無條件變小聲。
    """
    import math
    if layer_rate != base_rate:
        layer, _ = audioop.ratecv(layer, 2, 2, layer_rate, base_rate, None)
    length = max(len(base), len(layer))
    length -= length % 4
    base = base[:length] + b'\0' * (length - len(base))
    layer = layer[:length] + b'\0' * (length - len(layer))
    half = audioop.add(audioop.mul(base, 2, 0.5), audioop.mul(layer, 2, 0.5), 2)
    peak = audioop.max(half, 2) * 2
    del half
    if peak <= 32767:
        return audioop.add(base, layer, 2), base_rate, 0.0
    gain = 32767.0 / peak
    mixed = audioop.add(audioop.mul(base, 2, gain), audioop.mul(layer, 2, gain), 2)
    return mixed, base_rate, -20.0 * math.log10(gain)


def render_piano(model: Any) -> Tuple[bytes, int]:
    """用內建音源把整份譜面算成鋼琴音軌（沒有背景音樂的曲子用）。"""
    from .midi_preview import MidiPreviewSynth, build_chart_midi_notes, pedal_spans_in_range
    synth = MidiPreviewSynth(44100)
    try:
        if not synth.is_ready:
            raise ValueError(tr('內建鋼琴音源沒有準備好'))
        notes = build_chart_midi_notes(model, note_length_ms=None, expressive=True, real_pedal=True)
        if not notes:
            raise ValueError(tr('譜面沒有音高，算不出鋼琴音軌'))
        end_ms = max(float(model.music_end_ms or 0), max(n.end_ms for n in notes)) + 1500.0
        pcm = synth.render(notes, 0.0, end_ms, pedal_spans=pedal_spans_in_range(model, 0.0, end_ms))
        return bytes(pcm), 44100
    finally:
        synth.close()


# ── 譜面 ─────────────────────────────────────────────────────────────

def load_chart(path: str) -> Any:
    from .models import NoteModel
    model = NoteModel()
    with contextlib.redirect_stdout(io.StringIO()):
        if path.lower().endswith('.json'):
            model.load_json(path)
        else:
            model.load_xml(path)
    return model


def copy_model(model: Any) -> Any:
    """轉換會改動音符，拿一份副本做（走 JSON 往返，不動原本的檔案狀態）。"""
    handle, temp = tempfile.mkstemp(suffix='.json')
    os.close(handle)
    saved = (model.current_file, model.dirty, model.file_format, getattr(model, 'pan_xml', False))
    try:
        model.save_json(temp)
        return load_chart(temp)
    finally:
        (model.current_file, model.dirty, model.file_format, model.pan_xml) = saved
        try:
            os.remove(temp)
        except OSError:
            pass


def hiraeth_chart_xml(model: Any, duration_ms: int, log: Dict[str, int],
                      hold_gap_ms: int = DEFAULT_HOLD_GAP_MS) -> bytes:
    """PAN 相容 XML 再收緊到 Hiraeth 的規則。`model` 會被改，請傳副本。"""
    before = len(model.notes_tree)
    model.notes_tree = [n for n in model.notes_tree
                        if getattr(n, 'hidden', False) or int(n.hand) in (0, 1)]
    log['auto_notes_dropped'] = before - len(model.notes_tree)
    model.rebuild_display_cache()
    root = model.build_pan_xml()
    header = root.find('header')
    header.find('min_scale').text = '1'
    header.find('max_scale').text = '88'
    header.find('music_finish_time_msec').text = str(int(duration_ms))
    limit = int(duration_ms) + 2000
    tracks = root.findall('track_info/track')
    for track in tracks:
        track.find('name').text = 'key_apiano1'
    first_track = tracks[0].findtext('index')
    trills = 0
    for note in root.iter('note'):
        type_el = note.find('note_type')
        nt = int(type_el.text)
        if nt & 0x40:
            nt = 0x40                      # 顫音照原樣（連同它的 sub_note）
            trills += 1
        nt &= ~0x08
        if nt not in HIRAETH_NOTE_TYPES:
            nt = 0
        type_el.text = str(nt)
        if nt != 4:
            note.find('param1').text = '0'
            note.find('param2').text = '0'
        for sub in note.iter('sub_note'):
            sub.find('velocity').text = '0'
            sub.find('track_index').text = first_track
            start = max(0, min(int(sub.findtext('start_timing_msec')), limit))
            end = max(start, min(int(sub.findtext('end_timing_msec')), limit))
            sub.find('start_timing_msec').text = str(start)
            sub.find('end_timing_msec').text = str(end)
    log['trills_kept'] = trills
    trimmed, to_tap = (trim_holds_against_lane_conflicts(root, hold_gap_ms)
                       if int(hold_gap_ms) > 0 else (0, 0))
    log['holds_trimmed_for_lanes'] = trimmed
    log['holds_turned_into_taps'] = to_tap
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='utf-8')


#: 長條裁完剩不到這麼長就改成點擊（再短的長條玩起來跟點一下一樣，只是判定更難）
MIN_HOLD_MS = 60


def trim_holds_against_lane_conflicts(root: ET.Element, gap_ms: int) -> Tuple[int, int]:
    """在最後的 XML 上，把長條的走廊和尾巴清乾淨：遊戲實際讀到的就是這一份。

    長條響的期間、以及尾巴之後 `gap_ms` 內，**同一個鍵道**上有別的音起音（不論哪
    隻手）→ 尾巴裁到那顆起音前 `gap_ms`。只看鍵道有沒有重疊，所以按著長條、在別的
    鍵繼續彈的分解和弦不受影響。

    前面 `resolve_hold_tail_overlaps` 只處理 note_type 剛好是 2 的同手長條；帶其他
    旗標的長條（例如 10）在那之後才被收成 2，另一隻手也完全不看。全曲庫 101 包匯出
    實測，這樣漏掉的有：另一隻手落在走廊裡 26 顆、尾巴距同鍵道下一顆不到 80ms 201 處。

    回傳 (裁短的長條數, 裁到太短改成點擊的數量)。
    """
    gap = max(0, int(gap_ms))
    rows = []
    for note in root.iter('note'):
        rows.append([note, int(note.findtext('start_timing_msec')), int(note.findtext('end_timing_msec')),
                     int(note.findtext('min_key_index')), int(note.findtext('max_key_index')),
                     int(note.findtext('note_type')), int(note.findtext('hand') or 0)])
    rows.sort(key=lambda r: r[1])
    starts = [r[1] for r in rows]
    trimmed = to_tap = 0
    def deadline_for(start: int, end: int, lo: int, hi: int, hand: int) -> Optional[int]:
        first = bisect.bisect_right(starts, start)
        last = bisect.bisect_left(starts, end + gap)
        deadline = None
        for other in rows[first:last]:
            if other[1] <= start:
                continue
            same_lane = not (other[4] < lo or other[3] > hi)
            # 同手的下一顆在長條放開之後才起音 ＝ 前後關係，也要留 gap 讓手放開
            # （和 resolve_hold_tail_overlaps 的 sequential_gap 同一條規則）
            same_hand_after = other[6] == hand and other[1] >= end
            if not (same_lane or same_hand_after):
                continue
            deadline = other[1] if deadline is None else min(deadline, other[1])
        return deadline

    for row in rows:
        note, start, end, lo, hi, kind, hand = row
        if kind != 2 or end - start <= 1:
            continue
        # 裁一次之後，原本「還按著時就起音」的同手音可能變成「放開後馬上要彈」，
        # 所以用新的尾巴再找一次，直到不再變短（實測 Oceanus 70735 那條就是這樣）。
        new_end = end
        for _ in range(4):
            deadline = deadline_for(start, new_end, lo, hi, hand)
            if deadline is None or max(start + 1, deadline - gap) >= new_end:
                break
            new_end = max(start + 1, deadline - gap)
        if new_end >= end:
            continue
        trimmed += 1
        if new_end - start < MIN_HOLD_MS:
            note.find('note_type').text = '0'
            row[5] = 0
            to_tap += 1
        note.find('end_timing_msec').text = str(new_end)
        note.find('gate_time_msec').text = str(new_end - start)
        row[2] = new_end
    return trimmed, to_tap


# ── 規劃 ─────────────────────────────────────────────────────────────

@dataclass
class ChartSource:
    slot: str
    name: str                 # 原本的難度名
    level: int                # 原本的等級
    path: Optional[str] = None
    model: Any = None         # 直接給 model（目前譜面模式）
    real_display: Optional[float] = None   # 直接指定 REAL 顯示等級（目前譜面模式）


@dataclass
class PackagePlan:
    package_id: str
    title: str
    artist: str
    charts: List[ChartSource]
    audio_path: Optional[str] = None
    piano_path: Optional[str] = None
    no_background: bool = False
    cover_path: Optional[str] = None
    mix_piano: bool = True            # 有背景音樂時疊上鋼琴音軌
    render_piano_layer: bool = False  # 沒有鋼琴音軌檔時用內建音源算一份來疊（目前譜面模式）
    lead_in_bars: int = DEFAULT_LEAD_IN_BARS   # 譜面開頭要空幾小節（0 = 照原樣）
    source_dir: str = ''      # 來源樂曲資料夾（一鍵同步用它判斷有沒有改過）
    label: str = ''           # 給人看的來源說明
    slogan: str = ''          # 樂曲評論（song.json 的 slogan）


def assign_slots(diffs: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[Dict[str, Any], Optional[str]]]]:
    """同一份音訊的難度 → 四格。放不下的回傳 (難度, 原因)；原因 None 表示要另外成一包。"""
    slots: Dict[str, Dict[str, Any]] = {}
    leftovers = []
    for df in diffs:
        name = str(df.get('difficultyName', '')).strip().lower()
        if name in SLOTS and name not in slots:
            slots[name] = df
        else:
            leftovers.append(df)
    rest: List[Tuple[Dict[str, Any], Optional[str]]] = []
    for df in sorted(leftovers, key=lambda d: -int(d.get('difficultyLevel') or 0)):
        if str(df.get('difficultyName', '')).strip().lower() in SKIP_DIFFICULTIES:
            rest.append((df, tr('測試用難度')))
        elif 'real' not in slots:
            slots['real'] = df          # 最難的（Master 之類）補 real
        elif 'expert' not in slots:
            slots['expert'] = df
        else:
            rest.append((df, None))
    return slots, rest


def plan_song_folder(song_dir: str) -> Tuple[List[PackagePlan], List[str]]:
    """一個樂曲資料夾（有 register.json）→ 要輸出的包。同一份音訊的難度合成一包。"""
    folder = os.path.basename(os.path.normpath(song_dir))
    with open(os.path.join(song_dir, 'register.json'), encoding='utf-8-sig') as fh:
        register = json.load(fh)
    title = sjis_safe(register.get('displayName') or folder)
    artist = sjis_safe(register.get('author') or '')
    slogan = clean_slogan(register.get('slogan') or '')
    diffs = register.get('difficulties') or []
    cover = next((resolve_resource(song_dir, df.get('coverResourcePath'), ('.png', '.jpg', '.jpeg'))
                  for df in diffs if df.get('coverResourcePath')), None)
    piano = next((resolve_resource(song_dir, df.get('pianoAudioResourcePath'), ('.wav',))
                  for df in diffs if df.get('pianoAudioResourcePath')), None)
    groups: Dict[Any, List[Dict[str, Any]]] = collections.OrderedDict()
    for df in diffs:
        groups.setdefault(df.get('audioResourcePath'), []).append(df)

    def source(slot: str, df: Dict[str, Any]) -> ChartSource:
        path = resolve_resource(song_dir, df.get('chartFileName'), ('.json', '.xml'))
        return ChartSource(slot, str(df.get('difficultyName', '')), int(df.get('difficultyLevel') or 1), path)

    plans: List[PackagePlan] = []
    skipped: List[str] = []
    for n, (audio_res, group) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        slots, rest = assign_slots(group)
        variant = '' if n == 0 else ' / '.join(str(df['difficultyName']) for df in group)
        common = dict(artist=artist, slogan=slogan, source_dir=os.path.abspath(song_dir),
                      audio_path=resolve_resource(song_dir, audio_res, ('.wav',)),
                      piano_path=piano, cover_path=cover,
                      no_background=any(df.get('noBackgroundMusic') for df in group))
        plans.append(PackagePlan(package_id(folder, variant),
                                 sjis_safe('%s [%s]' % (title, variant)) if variant else title,
                                 charts=[source(s, df) for s, df in slots.items()],
                                 label=folder + (' [%s]' % variant if variant else ''), **common))
        for df, why in rest:
            name = str(df['difficultyName'])
            if why:
                skipped.append(tr('%s / %s：%s，略過') % (folder, name, why))
                continue
            plans.append(PackagePlan(package_id(folder, name), sjis_safe('%s [%s]' % (title, name)),
                                     charts=[source('real', df)], label='%s [%s]' % (folder, name),
                                     **common))
    return plans, skipped


def plan_library(root: str) -> Tuple[List[PackagePlan], List[str]]:
    plans: List[PackagePlan] = []
    skipped: List[str] = []
    for name in sorted(os.listdir(root)):
        song_dir = os.path.join(root, name)
        if not os.path.isdir(song_dir) or name.startswith(TUTORIAL_PREFIX):
            continue
        if not os.path.exists(os.path.join(song_dir, 'register.json')):
            skipped.append(tr('%s：沒有 register.json，略過') % name)
            continue
        try:
            got, why = plan_song_folder(song_dir)
        except Exception as exc:                          # noqa: BLE001
            skipped.append('%s：%s' % (name, exc))
            continue
        plans += got
        skipped += why
    return plans, skipped


# ── 輸出 ─────────────────────────────────────────────────────────────

@dataclass
class PackageResult:
    plan: PackagePlan
    zip_path: str = ''
    notes: List[str] = field(default_factory=list)
    error: str = ''


def package_file_name(plan: 'PackagePlan') -> str:
    """ZIP 檔名：曲名的英數字部分 ＋ 這一包固定的 8 碼（package_id 的雜湊尾巴）。

    只用曲名會撞名：中日文被拿掉後「エンドマークに希望と涙を添えて [Master piano]」
    和「系ぎて [Master piano]」都剩 master_piano，好幾個資料夾曲名都叫 felzione。
    一次匯出整個曲庫時後一包直接蓋掉前一包（實測 101 包少掉 4 包，沒有任何警告）。
    雜湊尾巴每一包都不同、同一包每次都一樣，所以重新匯出還是蓋掉自己那一份。
    """
    # package_id 是 nostalgia-clone.<slug>-<8 碼>，slug 可能是空的（nostalgia-clone.<8 碼>）
    tail = plan.package_id.rsplit('.', 1)[-1]
    digest = ascii_file_name(tail.rsplit('-', 1)[-1]) or 'package'
    title = ascii_file_name(plan.title)
    return '%s_%s' % (title, digest) if title else 'song_' + digest


def ascii_file_name(text: str) -> str:
    """ZIP 的檔名只留英數和 - _ .。

    中日文檔名在別人的系統、Hiraeth 管理器和命令列上常常變成亂碼或整個開不起來，
    所以寧可換掉。曲名整個不是英數（例如「彩云追月」）時換成 package_id。
    """
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', text or '')
    cleaned = re.sub(r'_{2,}', '_', cleaned).strip('_ .-')
    return cleaned[:80]


def last_audible_ms(pcm: bytes, rate: int, floor: int = 600, window_ms: int = 50) -> int:
    """PCM（16-bit 雙聲道）最後還有聲音的位置，毫秒。"""
    frame = 4
    step = max(frame, int(rate * window_ms / 1000.0) * frame)
    pos = len(pcm) - (len(pcm) % frame)
    while pos > 0:
        start = max(0, pos - step)
        if audioop.max(pcm[start:pos], 2) > floor:
            return int(pos / frame / rate * 1000.0)
        pos = start
    return 0


def trim_package_tail(pcm: bytes, rate: int, last_note_ms: int,
                      tail_ms: int = PACKAGE_TAIL_MS,
                      fade_ms: int = PACKAGE_FADE_MS) -> Tuple[bytes, int]:
    """把歌曲包的音訊收到內容結束之後一點點。回傳 (pcm, 剪掉幾毫秒)。

    留到「最後一顆音符」和「最後還有聲音的地方」比較晚的那個 —— 尾奏留著，
    但後面那段什麼都沒有的就不要了。剪進還有音樂的地方時淡出，免得「啪」一聲。
    """
    frame = 4
    total_ms = int(len(pcm) / frame / rate * 1000.0)
    audible = last_audible_ms(pcm, rate)
    keep_ms = max(int(last_note_ms), audible) + int(tail_ms)
    if keep_ms >= total_ms:
        return pcm, 0
    keep = (int(rate * keep_ms / 1000.0)) * frame
    trimmed = pcm[:keep]
    if keep_ms < audible and fade_ms:
        length = min(len(trimmed), int(rate * fade_ms / 1000.0) * frame)
        length -= length % frame
        if length > 0:
            head, tail = trimmed[:len(trimmed) - length], trimmed[len(trimmed) - length:]
            chunk = (length // 16 // frame) * frame or frame
            faded = b''
            for i in range(0, length, chunk):
                piece = tail[i:i + chunk]
                faded += audioop.mul(piece, 2, max(0.0, 1.0 - (i + len(piece) / 2.0) / length))
            trimmed = head + faded
    return trimmed, total_ms - keep_ms


def first_note_ms(model: Any) -> Optional[int]:
    starts = [int(n.start) for n in model.notes_tree]
    return min(starts) if starts else None


def apply_lead_in(models: List[Any], bars: int) -> int:
    """在譜面最前面補空白小節，讓第一顆音符前面至少空 `bars` 小節。

    補的是**小節**不是毫秒：拍格、小節線跟著整段往後推，第一個拍點仍然在 0，
    和官方譜一樣。回傳整份譜往後移了幾毫秒——音訊前面要補一樣長的靜音，不然
    就對不上了。同一包的難度共用一份音訊，所以全部用同一個位移量。
    """
    bars = int(bars or 0)
    if bars <= 0 or not models:
        return 0
    need = 0
    for model in models:
        first = first_note_ms(model)
        if first is None:
            continue
        index = model.get_measure_at_ms(float(first))
        need = max(need, bars - int(index if index is not None else 0))
    if need <= 0:
        return 0
    delta = 0
    for model in models:
        before = first_note_ms(model)
        for _ in range(need):
            model.insert_measure(0)
        after = first_note_ms(model)
        if before is not None and after is not None:
            delta = max(delta, int(after) - int(before))
    return delta


def build_package(plan: PackagePlan, out_dir: str, version: Optional[int] = None,
                  renderer: Renderer = render_piano,
                  hold_gap_ms: int = DEFAULT_HOLD_GAP_MS,
                  normalize_loudness: bool = True,
                  target_lufs: float = loudness.DEFAULT_TARGET_LUFS) -> PackageResult:
    """輸出一個 ZIP（先在暫存資料夾組好、驗過，再壓縮）。"""
    result = PackageResult(plan)
    work = tempfile.mkdtemp(prefix='hiraeth_')
    try:
        charts: List[Tuple[ChartSource, Any]] = []
        for chart in plan.charts:
            if chart.model is not None:
                charts.append((chart, copy_model(chart.model)))
            elif chart.path:
                charts.append((chart, load_chart(chart.path)))
            else:
                result.notes.append(tr('%s 找不到譜面檔，略過') % chart.name)
        if not charts:
            raise ValueError(tr('沒有可用的譜面'))

        # Hiraeth 沒有 soft：連續的 soft 依畫面走向轉成滑奏（斜的）或顫音（垂直的），
        # 其餘才變點擊。改的是副本，曲庫裡的譜不動。
        from .soft_runs import convert_soft_runs
        slides = trills = 0
        for _chart, model in charts:
            converted = convert_soft_runs(model)
            slides += converted.slide_chains
            trills += converted.trills
        if slides or trills:
            result.notes.append(tr('連續的 soft 轉成滑奏 %d 條、顫音 %d 個') % (slides, trills))

        fixed = 0
        if int(hold_gap_ms) > 0:
            for _chart, model in charts:
                fixed += int(model.resolve_hold_tail_overlaps(
                    int(hold_gap_ms), only_conflicts=True, sequential_gap=True) or 0)
        else:
            result.notes.append(tr('沒有處理長條尾端（照原樣輸出）'))
        if fixed:
            result.notes.append(tr('處理長條尾端（間距 %d ms）%d 條') % (int(hold_gap_ms), fixed))

        # 開頭留白：先補空白小節，音訊再補等長的靜音（現算的鋼琴音軌本來就含了）
        lead_ms = apply_lead_in([m for _c, m in charts], plan.lead_in_bars)
        if lead_ms:
            result.notes.append(tr('開頭補了空白小節（%.2f 秒），音訊前面補等長靜音')
                                % (lead_ms / 1000.0))

        def lead_pad(data: bytes, rate_hz: int) -> bytes:
            """檔案來的音訊要跟著往後移；現算的鋼琴音軌是照移好的譜算的，不用補。"""
            if not lead_ms:
                return data
            return b'\0' * (int(round(lead_ms / 1000.0 * rate_hz)) * 4) + data

        if plan.no_background or not plan.audio_path:
            if plan.piano_path:
                pcm, rate = read_wav(plan.piano_path)
                pcm = lead_pad(pcm, rate)
                result.notes.append(tr('沒有背景音樂：用鋼琴音軌 %s') % os.path.basename(plan.piano_path))
            else:
                hardest = max(charts, key=lambda item: len(item[1].notes_tree))[1]
                pcm, rate = renderer(hardest)
                result.notes.append(tr('沒有背景音樂：用內建音源把譜面算成鋼琴音軌'))
        else:
            pcm, rate = read_wav(plan.audio_path)
            pcm = lead_pad(pcm, rate)
            layer = None
            if plan.mix_piano and plan.piano_path:
                layer = read_wav(plan.piano_path)
                layer = (lead_pad(layer[0], layer[1]), layer[1])
                result.notes.append(tr('疊上鋼琴音軌 %s') % os.path.basename(plan.piano_path))
            elif plan.mix_piano and plan.render_piano_layer:
                hardest = max(charts, key=lambda item: len(item[1].notes_tree))[1]
                layer = renderer(hardest)
                result.notes.append(tr('疊上內建音源算的鋼琴音軌'))
            if layer is not None:
                pcm, rate, lowered = mix_pcm(pcm, rate, layer[0], layer[1])
                del layer
                if lowered > 0.05:
                    result.notes.append(tr('混音後整體降 %.1f dB 避免破音') % lowered)

        # ── 響度 ─────────────────────────────────────────────────────────
        #
        # 歌曲包裡只有一份 music.wav，所以這裡是**整首混好之後**才對齊目標，而不是
        # 分軌各自對齊（分軌各自對齊的是 nos-clone 那邊，它兩軌分開播）。
        #
        # 只用固定增益，不做限幅：推不到目標的曲子就停在不破峰的地方，並在報告裡
        # 說差多少。偷偷壓縮動態去換一個漂亮的數字，是交接文件特別交代不要做的事。
        if normalize_loudness:
            try:
                before = loudness.measure_pcm16(pcm, rate)
                gain = loudness.plan_gain(before, target_lufs)
                if abs(gain.gain_db) >= 0.05:
                    pcm = loudness.apply_gain_pcm16(pcm, gain.gain_db)
                result.notes.append(tr('響度 %.1f LUFS → %.1f（%+.1f dB，峰值 %.1f dBTP）')
                                    % (before.lufs_i, gain.resulting_lufs, gain.gain_db,
                                       gain.resulting_peak_dbtp))
                if gain.status == 'needs_review':
                    result.notes.append(tr('這首離目標還差 %.1f LU：要再大聲就得壓低峰值 %.1f dB，'
                                           '已經超出自動處理的範圍')
                                        % (target_lufs - gain.resulting_lufs, gain.peak_reduction_db))
            except loudness.LoudnessError as error:
                result.notes.append(tr('沒有調整響度：%s') % error)

        last_end = max((int(n.end) for _c, m in charts for n in m.notes_tree), default=0)
        audio_ms = len(pcm) / 4 / rate * 1000.0
        if last_end > audio_ms + 1500:
            pad = int((last_end + 1000 - audio_ms) / 1000.0 * rate) * 4
            pcm = pcm + b'\0' * pad
            result.notes.append(tr('音訊比譜面短，尾端補 %.1f 秒靜音') % (pad / 4 / rate))
        # 結尾收乾淨：譜面的曲終就是音訊長度，不收的話玩完要對著空畫面等
        pcm, cut = trim_package_tail(pcm, rate, last_end)
        if cut > 250:
            result.notes.append(tr('結尾多出 %.1f 秒沒事做，剪掉') % (cut / 1000.0))
        rate, seconds = write_wav(os.path.join(work, 'music.wav'), pcm, rate)
        del pcm
        duration_ms = int(round(seconds * 1000))

        levels: Dict[str, int] = {}
        trills = dropped = 0
        for chart, model in charts:
            log: Dict[str, int] = {}
            data = hiraeth_chart_xml(model, duration_ms, log, hold_gap_ms)
            with open(os.path.join(work, chart.slot + '.xml'), 'wb') as fh:
                fh.write(data)
            trills += log['trills_kept']
            dropped += log['auto_notes_dropped']
            if chart.slot != 'real':
                levels[chart.slot] = max(1, min(15, int(chart.level)))
            elif chart.real_display is not None:
                levels[chart.slot] = chart.real_display
            else:
                levels[chart.slot] = real_level(chart.level)
        if trills:
            result.notes.append(tr('%d 顆顫音照原樣保留') % trills)
        if dropped:
            result.notes.append(tr('拿掉 %d 顆自動彈的音符') % dropped)

        from PIL import Image
        if plan.cover_path and os.path.exists(plan.cover_path):
            image = Image.open(plan.cover_path).convert('RGB')
        else:
            image = Image.new('RGB', (512, 512), (48, 40, 60))
            result.notes.append(tr('沒有曲繪，用純色底'))
        w, h = image.size
        if min(w, h) < 74:
            scale = 74.0 / min(w, h)
            image = image.resize((int(round(w * scale)), int(round(h * scale))))
        elif max(w, h) > 4096:
            scale = 4096.0 / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)))
        image.save(os.path.join(work, 'cover.png'))

        spec = {'format': 1, 'package_id': plan.package_id, 'title': plan.title[:128],
                'artist': plan.artist[:128], 'charts': levels,
                'version': int(version if version is not None else default_version()),
                'slogan': clean_slogan(plan.slogan)}
        with open(os.path.join(work, 'song.json'), 'w', encoding='utf-8') as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)

        problems = validate_package(work)
        if problems:
            raise ValueError(tr('沒通過 Hiraeth 的檢查：') + '；'.join(problems))

        os.makedirs(out_dir, exist_ok=True)
        zip_path = os.path.join(out_dir, package_file_name(plan) + '.zip')
        temp_zip = zip_path + '.tmp'
        with zipfile.ZipFile(temp_zip, 'w') as archive:
            for name in sorted(os.listdir(work)):
                kind = zipfile.ZIP_DEFLATED if name.endswith(('.xml', '.json')) else zipfile.ZIP_STORED
                archive.write(os.path.join(work, name), name, compress_type=kind)
        os.replace(temp_zip, zip_path)
        result.zip_path = zip_path
    except Exception as exc:                              # noqa: BLE001
        result.error = str(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


# ── 驗證（照 Hiraeth importer 逐條實作）───────────────────────────────

def validate_package(folder: str) -> List[str]:
    problems: List[str] = []
    names = set(os.listdir(folder))
    extra = {n for n in names if n.lower() not in PACKAGE_FILES}
    if extra:
        problems.append(tr('多出不接受的檔案：') + ', '.join(sorted(extra)))
    for required in ('song.json', 'music.wav', 'cover.png'):
        if required not in names:
            problems.append(tr('缺少 ') + required)
    if problems:
        return problems
    if sum(os.path.getsize(os.path.join(folder, n)) for n in names) > MAX_PACKAGE_BYTES:
        problems.append(tr('整包超過 300MB'))
    with open(os.path.join(folder, 'song.json'), encoding='utf-8-sig') as fh:
        spec = json.load(fh)
    if spec.get('format') != 1 or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,79}', spec.get('package_id', '')):
        problems.append(tr('format 或 package_id 不合格'))
    version = spec.get('version', 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        problems.append(tr('version 要是正整數'))
    slogan = spec.get('slogan', spec.get('description', ''))
    if not isinstance(slogan, str) or len(slogan) > MAX_SLOGAN:
        problems.append(tr('slogan（樂曲評論）不合格，最多 %d 字') % MAX_SLOGAN)
    else:
        try:
            slogan.encode('shift_jisx0213')
        except UnicodeEncodeError:
            problems.append(tr('slogan 有 Shift_JIS 編不了的字'))
    for key in ('title', 'artist'):
        value = spec.get(key)
        if not isinstance(value, str) or len(value) > 128 or (key == 'title' and not value.strip()):
            problems.append(key + tr(' 不合格'))
            continue
        try:
            value.encode('shift_jisx0213')
        except UnicodeEncodeError:
            problems.append(key + tr(' 有 Shift_JIS 編不了的字'))
    levels = spec.get('charts')
    if not isinstance(levels, dict) or not levels or set(levels) - set(SLOTS):
        return problems + [tr('charts 不合格')]
    if {os.path.splitext(n)[0] for n in names if n.endswith('.xml')} != set(levels):
        problems.append(tr('xml 檔和 charts 對不上'))
    for slot, level in levels.items():
        if slot == 'real':
            ok = (not isinstance(level, bool) and isinstance(level, (int, float))
                  and level in REAL_LEVELS)
        else:
            ok = not isinstance(level, bool) and isinstance(level, int) and 1 <= level <= 15
        if not ok:
            problems.append(tr('%s 等級 %r 超出範圍') % (slot, level))
    wav_path = os.path.join(folder, 'music.wav')
    if os.path.getsize(wav_path) > MAX_WAV_BYTES:
        return problems + [tr('music.wav 超過 128MiB')]
    with wave.open(wav_path, 'rb') as w:
        if (w.getcomptype() != 'NONE' or w.getnchannels() != 2 or w.getsampwidth() != 2
                or w.getframerate() not in AUDIO_RATES):
            return problems + [tr('music.wav 要是 PCM16 雙聲道 16000/22050/44100/48000Hz')]
        seconds = w.getnframes() / float(w.getframerate())
    for slot in levels:
        path = os.path.join(folder, slot + '.xml')
        if os.path.exists(path):
            problems += ['%s.xml：%s' % (slot, p) for p in validate_chart(path, seconds)]
    return problems


def validate_chart(path: str, seconds: float) -> List[str]:
    if os.path.getsize(path) > 16 * 1024 * 1024:
        return [tr('譜面超過 16MB')]
    root = ET.parse(path).getroot()
    if root.tag != 'music_score':
        return [tr('根節點不是 music_score')]
    problems: List[str] = []
    duration = int(root.findtext('header/music_finish_time_msec', '-1'))
    if duration <= 0 or abs(duration - seconds * 1000) > 2000:
        problems.append(tr('譜面長度和音訊差超過 2 秒'))
    if (root.findtext('header/file_version') != '1' or root.findtext('header/min_scale') != '1'
            or root.findtext('header/max_scale') != '88'):
        problems.append(tr('header 不合格（file_version=1、min_scale=1、max_scale=88）'))
    notes = root.findall('note_data/note')
    if not 1 <= len(notes) <= 50000:
        problems.append(tr('音符數不合格'))
    ids = []
    bad_note = None
    for note in notes:
        try:
            values = {tag: int(note.findtext(tag)) for tag in (
                'index', 'start_timing_msec', 'end_timing_msec', 'gate_time_msec', 'scale_piano',
                'min_key_index', 'max_key_index', 'note_type', 'hand', 'key_kind',
                'param1', 'param2', 'param3')}
        except (TypeError, ValueError):
            bad_note = tr('音符欄位缺漏或不是整數')
            break
        ids.append(values['index'])
        if not 0 <= values['start_timing_msec'] <= values['end_timing_msec'] <= duration + 2000 \
                or not 1 <= values['scale_piano'] <= 88:
            bad_note = tr('音符時間或音高不合格')
        elif values['note_type'] not in HIRAETH_NOTE_TYPES or values['hand'] not in (0, 1) \
                or values['key_kind'] != 0:
            bad_note = tr('音符類型／手／key_kind 不合格（只收 Tap、Long、Slide、Trill，手 0／1）')
        elif not 0 <= values['min_key_index'] <= values['max_key_index'] <= 28:
            bad_note = tr('鍵範圍不合格')
        if bad_note:
            break
    if bad_note:
        problems.append(bad_note)
    elif sorted(ids) != list(range(len(notes))):
        problems.append(tr('音符 index 沒有從 0 連號'))
    for sub in root.iter('sub_note'):
        if sub.findtext('velocity') != '0':
            problems.append(tr('sub_note 力度不是 0'))
            break
        if not 1 <= int(sub.findtext('scale_piano', '0')) <= 88 or \
                not 0 <= int(sub.findtext('start_timing_msec', '-1')) <= int(sub.findtext('end_timing_msec', '-1')) <= duration + 2000:
            problems.append(tr('sub_note 時間或音高不合格'))
            break
    tracks = root.findall('track_info/track')
    ids_ok = {int(t.findtext('index')) for t in tracks if t.findtext('name') == 'key_apiano1'}
    if len(ids_ok) != len(tracks) or any(int(s.findtext('track_index', '-1')) not in ids_ok
                                         for s in root.iter('sub_note')):
        problems.append(tr('track 不是 key_apiano1 或對不上'))
    if int(root.findtext('header/first_bpm', '0')) <= 0 or not root.findall('beat_data/beat'):
        problems.append(tr('缺 BPM 或拍子資料'))
    return problems
