# -*- coding: utf-8 -*-
"""把生成好的難度寫回**樂曲資料夾**，而不是平鋪成三個 XML。

遊戲讀的是這個版面：

    UserSongs/<曲名>/
        register.json                 ← 難度清單，遊戲照它找檔案
        Real/  <譜名>.json            ← 遊戲實際讀的譜面
        Normal/<譜名>.json
               source/<譜名>.xml      ← 生成時同時留一份 XML，給制譜器回頭改

`source/` 那一份還兼任「這個難度是自動生成的」的記號：重跑時只有它們會被
覆蓋，使用者自己手寫的 Normal／Hard／Expert 一律不動。

這個模組不碰 Qt，規則才測得起來；UI 只負責問使用者要不要跑。
"""

from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .difficulty import DIFFICULTIES, TARGETS, level_for, worth_generating

#: register.json 往上找幾層就放棄。譜面最深是 <曲名>/<難度>/source/x.xml。
_SEARCH_DEPTH = 4


class SongFolderError(Exception):
    """找不到樂曲資料夾、或這份譜面不適合當生成來源。"""


def find_song_folder(chart_path: str) -> Optional[str]:
    """從譜面路徑往上找出含 `register.json` 的樂曲資料夾。"""
    if not chart_path:
        return None
    folder = os.path.dirname(os.path.abspath(chart_path))
    for _ in range(_SEARCH_DEPTH):
        if os.path.exists(os.path.join(folder, 'register.json')):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return None


def _read_register(song_dir: str) -> Dict[str, Any]:
    path = os.path.join(song_dir, 'register.json')
    with io.open(path, encoding='utf-8') as handle:
        return json.loads(handle.read())


def resolve_chart_file(song_dir: str, entry: Dict[str, Any]) -> Optional[str]:
    """把 register 記錄裡的 `chartFileName` 換成實際存在的檔案路徑。

    `chartFileName` 寫的是遊戲用的相對路徑（`songs/<曲名>/<難度>/<譜名>`，
    沒有副檔名），不能直接當本機路徑用。
    """
    name = (entry.get('chartFileName') or '').replace('\\', '/')
    if not name:
        return None
    parts = name.split('/')
    candidates = [os.path.join(song_dir, parts[-1] + '.json')]
    if len(parts) >= 2:
        candidates.insert(0, os.path.join(song_dir, parts[-2], parts[-1] + '.json'))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    # 找不到就整個資料夾掃一遍——難度資料夾可能被改過名字。
    #
    # 比對不分大小寫：register 寫的名字和磁碟上的檔案不見得同一個大小寫
    # （實測 Abiogenesis 宣告 `Abiogenesis` 但檔案是 `abiogenesis.json`），
    # 用精確字串比對就會判成「找不到來源譜面」，整首被略過。
    wanted = (parts[-1] + '.json').lower()
    for root, _dirs, files in os.walk(song_dir):
        if os.path.basename(root) == 'source':
            continue
        for found in files:
            if found.lower() == wanted and not found.endswith('.analysis.json'):
                return os.path.join(root, found)
    return None


def is_generated(song_dir: str, entry: Dict[str, Any]) -> bool:
    """這個難度是我們自動生成的嗎。

    兩個條件都要成立：

    1. **標籤是我們會生的那三個**（Normal / Hard / Expert）。Real、Master
       這些名字我們從來不生，一定是人寫的。
    2. 旁邊留著 `source/<譜名>.xml`——生成時才會寫出的那一份。

    第 1 點不能省。`source/` 也是使用者自己存 XML 的習慣：實測曲庫裡有 13
    個手寫的 Real 帶著 source/xml，少了這道判斷，整首會被判成「只剩生成的
    難度，找不到可以當來源的譜」而整個補不了。
    """
    label = entry.get('difficultyName') or ''
    if label not in {TARGETS[key].label for key in DIFFICULTIES}:
        return False
    name = (entry.get('chartFileName') or '').replace(chr(92), '/')
    stem = os.path.basename(name)
    if not stem:
        return False
    return os.path.exists(os.path.join(song_dir, label, 'source', stem + '.xml'))


class SongPlan:
    """要在哪個樂曲資料夾、從哪一份譜、生出哪幾個難度。"""

    def __init__(self, song_dir: str, register: Dict[str, Any],
                 source_entry: Dict[str, Any], chart_name: str,
                 wanted: Sequence[str], skipped: Sequence[Tuple[str, str]]):
        self.song_dir = song_dir
        self.register = register
        self.source_entry = source_entry
        self.chart_name = chart_name
        self.wanted = list(wanted)
        self.skipped = list(skipped)

    @property
    def source_level(self) -> int:
        return int(self.source_entry.get('difficultyLevel') or 0)

    @property
    def source_label(self) -> str:
        return str(self.source_entry.get('difficultyName') or '')

    @property
    def song_name(self) -> str:
        return os.path.basename(self.song_dir.rstrip('/\\'))

    def paths_for(self, key: str) -> Tuple[str, str]:
        """(要寫出的 .json, 同時留一份的 source/*.xml)"""
        label = TARGETS[key].label
        folder = os.path.join(self.song_dir, label)
        return (os.path.join(folder, self.chart_name + '.json'),
                os.path.join(folder, 'source', self.chart_name + '.xml'))


def plan(chart_path: str, song_dir: Optional[str] = None,
         source_level: Optional[int] = None, regenerate: bool = False) -> SongPlan:
    """看這份譜面能生出哪些難度。生不出來就丟 `SongFolderError` 說明原因。

    `song_dir` 指定要寫進哪個樂曲資料夾；不給就從譜面路徑往上找。剛轉好、
    還沒歸檔的譜面不在任何樂曲資料夾裡，那時候就得由使用者自己指定。

    `source_level` 蓋掉來源等級。譜面不屬於這個資料夾時，register 裡找不到
    它的記錄，等級就無從得知——低難度的等級是照來源推的，所以要問。

    `regenerate` 打開才會重做**之前自己生過**的難度。預設不做：「補齊」的意思
    是補沒有的，已經在那裡的難度不算缺。舊的預設是每次都重生，於是補過一輪之
    後再掃，整個曲庫還是一片「要補」——那個畫面說的不是事實。
    """
    if song_dir is None:
        song_dir = find_song_folder(chart_path)
    if song_dir is None:
        raise SongFolderError(
            '這份譜面不在樂曲資料夾裡（往上四層都沒有 register.json）。\n'
            '請自己指定要寫進哪一個曲目。')
    if not os.path.exists(os.path.join(song_dir, 'register.json')):
        raise SongFolderError('這個資料夾裡沒有 register.json：%s' % song_dir)
    try:
        register = _read_register(song_dir)
    except Exception as exc:                        # noqa: BLE001
        raise SongFolderError('register.json 讀不起來：%s' % exc)

    entries = register.get('difficulties') or []
    if not entries:
        raise SongFolderError('register.json 裡沒有任何難度。')

    # 目前開著的這份譜就是來源。對不上就退回「等級最高的那一份」。
    here = os.path.normcase(os.path.abspath(chart_path))
    stem = os.path.splitext(os.path.basename(chart_path))[0]
    source_entry = None
    for entry in entries:
        resolved = resolve_chart_file(song_dir, entry)
        if resolved and os.path.normcase(os.path.abspath(resolved)) == here:
            source_entry = entry
            break
        if os.path.basename((entry.get('chartFileName') or '')
                            .replace('\\', '/')) == stem:
            source_entry = entry
    if source_entry is None:
        source_entry = max(entries,
                           key=lambda e: int(e.get('difficultyLevel') or 0))

    level = int(source_level if source_level else
                (source_entry.get('difficultyLevel') or 0))
    if level <= 0:
        raise SongFolderError('來源難度沒有等級，推不出低難度的等級——請自己指定。')
    # 等級可能是外面指定的，後面一路都讀 source_entry，這裡就地覆寫。
    source_entry = dict(source_entry)
    source_entry['difficultyLevel'] = level

    by_label = {e.get('difficultyName'): e for e in entries}
    wanted: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for key in DIFFICULTIES:
        label = TARGETS[key].label
        existing = by_label.get(label)
        if label == source_entry.get('difficultyName'):
            skipped.append((key, '這就是來源本身'))
            continue
        if existing is not None:
            made_here = is_generated(song_dir, existing)
            if not made_here:
                skipped.append((key, '已經有手寫的 %s，不覆蓋' % label))
                continue
            if not regenerate:
                skipped.append((key, '已經有 %s（之前生的）' % label))
                continue
        if not worth_generating(level, key):
            skipped.append((key, '來源只有 Lv.%d，生不出更簡單的 %s' % (level, label)))
            continue
        wanted.append(key)

    return SongPlan(song_dir, register, source_entry, stem, wanted, skipped)


#: 來源要有幾成的音符帶音高才生得出低難度。排譜器完全靠音高決定鍵道，
#: 音高不足的譜生出來會是一團亂——寧可明講「這首要先補音高」。
PITCH_MIN_RATIO = 0.9


def plan_song(song_dir: str, regenerate: bool = False) -> SongPlan:
    """整首掃描用：自己挑來源（等級最高、而且不是我們生出來的那一份）。

    和 `plan(chart_path)` 的差別只在誰決定來源：那條路是「使用者開著哪一份
    就用哪一份」，這條是「這首曲子裡最難的那份人寫的譜」。
    """
    if not os.path.exists(os.path.join(song_dir, 'register.json')):
        raise SongFolderError('沒有 register.json')
    # 讀壞了要變成「這一首跳過」，不是讓整趟掃描死掉。scan_library 只接得住
    # SongFolderError；JSONDecodeError 會一路穿出去，連對話框都開不出來 ——
    # 而使用者根本不知道是哪一首害的。plan() 那條路本來就包了，這裡漏了。
    try:
        register = _read_register(song_dir)
    except SongFolderError:
        raise
    except Exception as exc:                            # noqa: BLE001
        raise SongFolderError('register.json 讀不起來：%s' % exc)
    entries = register.get('difficulties') or []
    if not entries:
        raise SongFolderError('register.json 裡沒有任何難度')

    handmade = [e for e in entries if not is_generated(song_dir, e)]
    if not handmade:
        raise SongFolderError('只剩生成的難度，找不到可以當來源的譜')
    source = max(handmade, key=lambda e: int(e.get('difficultyLevel') or 0))
    path = resolve_chart_file(song_dir, source)
    if not path:
        raise SongFolderError('找不到來源譜面（register 指的檔案不在）')
    return plan(path, song_dir=song_dir, regenerate=regenerate)


class LibraryRow:
    """曲庫裡的一首歌，以及它現在的狀況。"""

    def __init__(self, song: str, song_dir: str, source_path: str = '',
                 plan_obj: Optional[SongPlan] = None, reason: str = ''):
        self.song = song
        self.song_dir = song_dir
        self.source_path = source_path
        self.plan = plan_obj
        self.reason = reason

    @property
    def ready(self) -> bool:
        return self.plan is not None and bool(self.plan.wanted)


def scan_library(root: str, load_chart, on_progress=None,
                 regenerate: bool = False) -> List[LibraryRow]:
    """掃過整個曲庫，回報每一首要生哪些難度、或為什麼生不了。

    `load_chart(path)` 由呼叫端提供（這個模組不該認識 `NoteModel`），用來
    檢查來源有沒有音高——那是最常見的卡關原因，而且光看 register 看不出來。
    """
    rows: List[LibraryRow] = []
    names = sorted(n for n in os.listdir(root)
                   if os.path.isdir(os.path.join(root, n)))
    for index, song in enumerate(names):
        song_dir = os.path.join(root, song)
        if on_progress is not None:
            on_progress(index, len(names), song)
        if not os.path.exists(os.path.join(song_dir, 'register.json')):
            continue                        # 不是曲目資料夾，安靜跳過
        try:
            plan_obj = plan_song(song_dir, regenerate=regenerate)
        except SongFolderError as exc:
            rows.append(LibraryRow(song, song_dir, reason=str(exc)))
            continue
        source_path = resolve_chart_file(song_dir, plan_obj.source_entry) or ''
        if not plan_obj.wanted:
            why = '、'.join('%s：%s' % (TARGETS[k].label, w)
                            for k, w in plan_obj.skipped) or '沒有要生的難度'
            rows.append(LibraryRow(song, song_dir, source_path, None, why))
            continue
        try:
            notes = load_chart(source_path)
        except Exception as exc:            # noqa: BLE001
            rows.append(LibraryRow(song, song_dir, source_path, None,
                                   '來源讀不起來：%s' % exc))
            continue
        visible = [n for n in notes if not getattr(n, 'hidden', False)]
        pitched = sum(1 for n in visible if getattr(n, 'pitch', None) is not None)
        if not visible:
            rows.append(LibraryRow(song, song_dir, source_path, None,
                                   '來源沒有音符'))
        elif pitched < len(visible) * PITCH_MIN_RATIO:
            rows.append(LibraryRow(
                song, song_dir, source_path, None,
                '來源沒有音高（%d/%d）——請先用「從 MIDI 還原音高與表情」補'
                % (pitched, len(visible))))
        else:
            rows.append(LibraryRow(song, song_dir, source_path, plan_obj))
    if on_progress is not None:
        on_progress(len(names), len(names), '')
    return rows


def _write_json_atomic(path: str, data: Any) -> None:
    """先寫暫存檔再改名。

    直接用 'w' 開檔會**先清空**再寫：寫到一半斷掉（磁碟滿了、當掉、拔電），
    留下的是一個空的或半截的 register.json，那首歌在遊戲裡會變成沒有任何難度。
    而補難度是一次跑幾十首的批次，斷在中間的機率不是零。

    曲庫那邊（song_library._write_json）本來就是這樣寫的，這裡沒跟上。
    """
    temp = path + '.tmp'
    with io.open(temp, 'w', encoding='utf-8', newline='') as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(temp, path)


def write_difficulty(plan_obj: SongPlan, key: str, model: Any,
                     result: Any) -> Dict[str, Any]:
    """寫出一個難度的 json + source xml，回傳它的 register 記錄。"""
    json_path, xml_path = plan_obj.paths_for(key)
    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
    model.save_json(json_path)
    model.save_xml(xml_path)

    # 等級由譜面自己的指標和來源等級各出一半——只照來源推的話，同一個 real
    # 等級的兩首歌會拿到一樣的等級，但生出來的譜可以差很多。
    level = level_for(key, result.notes_per_sec, result.peak_notes,
                      plan_obj.source_level)
    record = dict(plan_obj.source_entry)
    record['difficultyName'] = TARGETS[key].label
    record['difficultyLevel'] = level
    record['chartFileName'] = 'songs/%s/%s/%s' % (
        plan_obj.song_name, TARGETS[key].label, plan_obj.chart_name)
    return record


def commit(plan_obj: SongPlan, records: Sequence[Dict[str, Any]]) -> str:
    """把新記錄併回 register.json，由易到難排在既有難度前面。"""
    if not records:
        return ''
    labels = {r.get('difficultyName') for r in records}
    kept = [e for e in (plan_obj.register.get('difficulties') or [])
            if e.get('difficultyName') not in labels]
    order = {TARGETS[k].label: i for i, k in enumerate(DIFFICULTIES)}
    fresh = sorted(records, key=lambda r: order.get(r.get('difficultyName'), 99))
    plan_obj.register['difficulties'] = fresh + kept

    path = os.path.join(plan_obj.song_dir, 'register.json')
    _write_json_atomic(path, plan_obj.register)
    return path
