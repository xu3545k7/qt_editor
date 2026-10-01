"""歌曲資訊的紀錄：填過一次就不用再填一次。

匯出的兩個對話框（完整曲目格式、Hiraeth 歌曲包）都要填曲名、作者、曲繪、
難度名稱、等級、分類、樂曲評論。實際的工作流程是「改一點譜 → 匯出 → 進遊戲
聽 → 再改一點 → 再匯出」，所以同一組資訊一天要重打十幾次；欄位又都是空的，
連上次填了什麼都看不到。

這裡把填過的值記起來，下次開對話框自動帶進去。**只填空白欄位**——已經有值的
（例如追加難度模式從 register.json 帶出來的曲名）不動，使用者手打的永遠贏。

三層退路，由細到粗，第一個找到的就用：

1. `charts`：這份譜上次填的（key = 譜面檔）。重複匯出同一份譜就命中這層，
   等於整個對話框都不用碰。
2. `songs`：同一首歌其他難度填的（key = 音源檔）。新開一個難度時曲名、作者、
   曲繪、分類就已經在了，只要填難度名稱和等級。
3. `last`：上次填的，只限「整個曲庫都一樣」的欄位（作者、分類）。全新的一首
   歌也不用再打一次自己的名字。

存在 settings.json 旁邊的 song_info.json。跑測試時只留在記憶體、不落盤，理由
和 `settings._under_test` 一樣：測試會大量呼叫，落盤就會改到使用者的檔案。
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

from .settings import _under_test, config_dir

#: 一首歌共用的欄位（同一首的每個難度都一樣）
SONG_FIELDS = ('title', 'author', 'cover', 'video', 'video_start', 'category', 'slogan')
#: 一份譜自己的欄位（難度名稱、定數、Hiraeth 的難度格與等級）
CHART_FIELDS = ('diff_name', 'diff_level', 'slot', 'slot_level')
#: 跨曲目沿用的欄位。曲繪、影片、曲名是一首歌一個，不能沿用到別首。
LAST_FIELDS = ('author', 'category')

#: 最多記幾份譜／幾首歌。超過就丟掉最久沒用到的（dict 保留插入順序）。
MAX_ENTRIES = 300

_STORE_ENV = 'NOS_SONG_INFO_FILE'

#: 記憶體裡的那一份。None = 還沒從磁碟讀過。
_cache: Optional[dict] = None


def store_file() -> str:
    """紀錄檔的位置。`NOS_SONG_INFO_FILE` 可以改（測試與可攜安裝用）。"""
    return os.environ.get(_STORE_ENV) or os.path.join(config_dir(), 'song_info.json')


def _blank() -> dict:
    return {'version': 1, 'charts': {}, 'songs': {}, 'last': {}}


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = _blank()
    try:
        with open(store_file(), 'r', encoding='utf-8') as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            for key in ('charts', 'songs', 'last'):
                value = saved.get(key)
                if isinstance(value, dict):
                    data[key] = value
    except FileNotFoundError:
        pass
    except Exception:                                    # noqa: BLE001
        pass                                             # 壞檔就當沒有，不要擋住匯出
    _cache = data
    return data


def _save() -> None:
    if _under_test():
        return
    data = _load()
    try:
        path = store_file()
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:                                    # noqa: BLE001
        pass                                             # 記不起來不是錯誤，別打斷匯出


def reset_cache() -> None:
    """忘掉記憶體裡的那一份，下次重新讀檔（測試用）。"""
    global _cache
    _cache = None


def path_key(path: str) -> str:
    """檔案路徑當 key：大小寫與斜線都正規化，不然同一個檔會記成兩筆。"""
    if not path:
        return ''
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:                                    # noqa: BLE001
        return os.path.normcase(str(path))


def _touch(bucket: Dict[str, dict], key: str, values: dict) -> None:
    """寫一筆並把它移到最後（最近用到），必要時丟掉最舊的。"""
    merged = dict(bucket.pop(key, {}) or {})
    merged.update(values)
    bucket[key] = merged
    while len(bucket) > MAX_ENTRIES:
        bucket.pop(next(iter(bucket)))


def recall(chart_path: str = '', audio_path: str = '') -> dict:
    """這份譜／這首歌上次填的值。找不到就回空 dict。

    由粗到細疊上去，所以後鋪的會蓋掉先鋪的。順序刻意讓**共用欄位以「這首歌」
    那筆為準，難度欄位以「這份譜」那筆為準**：

    1. `last` 的作者與分類
    2. 這份譜記的共用欄位 —— 只是備援。沒有音源可當 key 的譜（純鋼琴曲）就靠它
    3. 這首歌記的共用欄位 —— 真正的來源。在 expert 那份改了曲名，回到 hard 也要
       跟著改，不然同一首歌會匯出成兩個資料夾
    4. 這份譜記的難度名稱與等級 —— 這個不能跟著別份譜跑
    """
    data = _load()
    chart = (data['charts'].get(path_key(chart_path)) or {}) if chart_path else {}
    song = (data['songs'].get(path_key(audio_path)) or {}) if audio_path else {}

    def _put(source: dict, fields) -> None:
        out.update({k: v for k, v in source.items()
                    if k in fields and v not in (None, '')})

    out: dict = {}
    _put(data['last'], LAST_FIELDS)
    _put(chart, SONG_FIELDS)
    _put(song, SONG_FIELDS)
    _put(chart, CHART_FIELDS)
    return out


def remember(chart_path: str = '', audio_path: str = '', info: Optional[dict] = None) -> None:
    """記下這次填的值。沒有 key 可記（沒存檔也沒音源）就只更新 `last`。"""
    info = {k: v for k, v in (info or {}).items() if v not in (None, '')}
    if not info:
        return
    data = _load()
    if chart_path:
        _touch(data['charts'], path_key(chart_path),
               {k: v for k, v in info.items() if k in SONG_FIELDS + CHART_FIELDS})
    if audio_path:
        _touch(data['songs'], path_key(audio_path),
               {k: v for k, v in info.items() if k in SONG_FIELDS})
    for field in LAST_FIELDS:
        if field in info:
            data['last'][field] = info[field]
    _save()
