# -*- coding: utf-8 -*-
"""自動儲存：定時把還沒存檔的改動寫成**備份**，崩潰之後重開可以救回來。

刻意不直接覆蓋原檔：
  * 使用者關閉時選「不儲存」，應該真的什麼都沒寫進去；
  * 跑完一個工具（排譜、生成）結果不滿意時，原檔要還是原來那份。

備份放在固定的資料夾（不是譜面旁邊）：曲目資料夾會被遊戲和批次工具掃描，
多出一份 `.json` 可能被當成另一份譜；而且同一個檔名（`Melodiniq.json`）在
Normal / Hard / Expert 三個資料夾都有，檔名要帶原路徑的雜湊才分得開。

這個模組不碰 Qt，規則才測得起來；計時、問使用者要不要還原是 UI 的事。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
from typing import Any, List, NamedTuple, Optional

#: 預設每幾分鐘存一次備份
DEFAULT_INTERVAL_MIN = 3

#: 判斷「原檔在備份之後又被存過」時的時間容差（秒）
_MTIME_SLACK_S = 2.0


def autosave_dir() -> str:
    """備份資料夾。

    打包後的 exe 不能用「目前工作目錄」：從捷徑、檔案總管、拖曳開檔啟動時
    工作目錄都不一樣，崩潰後重開會找不到上次的備份。所以一律放在使用者的
    LocalAppData 底下。
    """
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'NostalgiaChartEditor', 'autosave')


class BackupInfo(NamedTuple):
    data_path: str        #: 備份的譜面檔
    meta_path: str        #: 旁邊記錄來源與時間的 .meta.json
    source: Optional[str]  #: 原本那份檔案；新譜（還沒存過）是 None
    saved_at: float       #: 備份寫出的時間（time.time()）
    notes: int            #: 備份當下的音符數


def _safe_stem(path: Optional[str]) -> str:
    stem = os.path.splitext(os.path.basename(path or ''))[0] or 'untitled'
    return re.sub(r'[\\/:*?"<>|\s]+', '_', stem)[:60]


def backup_paths(source: Optional[str], session: str,
                 folder: Optional[str] = None) -> tuple:
    """(備份譜面路徑, meta 路徑)。

    有來源檔：用來源的完整路徑算雜湊，同一份檔案永遠對到同一個備份。
    新譜：用這次工作階段的代號，兩個視窗各開一份新譜也不會互蓋。
    """
    folder = folder or autosave_dir()
    if source:
        key = os.path.normcase(os.path.abspath(source))
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]
        # 保持原本的格式：XML 的檔頭與其他區段 JSON 存不下，用 JSON 救回來
        # 再存回 XML 會少東西。MIDI 本身存不下鍵道，一律用 JSON。
        ext = '.xml' if source.lower().endswith('.xml') else '.json'
    else:
        digest = 'new-' + re.sub(r'[^0-9A-Za-z]+', '', session)[:12]
        ext = '.json'
    name = '%s__%s%s' % (_safe_stem(source), digest, ext)
    data = os.path.join(folder, name)
    return data, data + '.meta.json'


def write_backup(model: Any, session: str, folder: Optional[str] = None) -> str:
    """把 `model` 目前的狀態寫成備份，回傳備份路徑。

    存檔函式有副作用：會把 `current_file` 換成寫出的路徑、清掉 `dirty`。
    備份不是「存檔」，這三個欄位原樣還原——否則下次按 Ctrl+S 會存到備份
    資料夾去，標題也不會再提醒還沒存檔。

    先寫暫存檔再 `os.replace`：寫到一半當掉的話，留下的是上一份完整備份，
    不是半份壞掉的檔案。
    """
    source = getattr(model, 'current_file', None) or None
    data_path, meta_path = backup_paths(source, session, folder)
    os.makedirs(os.path.dirname(data_path), exist_ok=True)

    saved = (model.current_file, model.dirty, getattr(model, 'file_format', None),
             getattr(model, 'pan_xml', False))
    temp = data_path + '.tmp'
    try:
        if data_path.endswith('.xml'):
            model.save_xml(temp)
        else:
            model.save_json(temp)
    finally:
        model.current_file, model.dirty = saved[0], saved[1]
        if saved[2] is not None:
            model.file_format = saved[2]
        model.pan_xml = saved[3]
    os.replace(temp, data_path)

    meta = {
        'source': source,
        'saved_at': time.time(),
        'notes': len(getattr(model, 'notes_tree', ()) or ()),
        'pid': os.getpid(),
    }
    with io.open(meta_path, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(meta, ensure_ascii=False, indent=2))
    return data_path


def discard_backup(source: Optional[str], session: str,
                   folder: Optional[str] = None) -> None:
    """正式存檔成功、或使用者選了「不儲存」之後，這份備份就沒用了。"""
    data_path, meta_path = backup_paths(source, session, folder)
    for path in (data_path, meta_path, data_path + '.tmp'):
        try:
            os.remove(path)
        except OSError:
            pass


def _read_meta(meta_path: str) -> Optional[BackupInfo]:
    data_path = meta_path[:-len('.meta.json')]
    if not os.path.exists(data_path):
        return None
    try:
        with io.open(meta_path, encoding='utf-8') as handle:
            meta = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    return BackupInfo(data_path, meta_path, meta.get('source') or None,
                      float(meta.get('saved_at') or 0.0),
                      int(meta.get('notes') or 0))


def list_backups(folder: Optional[str] = None) -> List[BackupInfo]:
    """資料夾裡還留著的備份，新的在前。

    正常結束的工作階段會把自己的備份清掉，所以留下來的幾乎都是崩潰或強制
    關閉留下的。來源檔在備份之後又被存過（比備份新）的，代表改動已經進了
    原檔，不列出來。
    """
    folder = folder or autosave_dir()
    if not os.path.isdir(folder):
        return []
    out: List[BackupInfo] = []
    for name in os.listdir(folder):
        if not name.endswith('.meta.json'):
            continue
        info = _read_meta(os.path.join(folder, name))
        if info is None:
            continue
        if info.source and os.path.exists(info.source):
            try:
                # 留兩秒容差：檔案系統的修改時間和 time.time() 精度不同，只差
                # 幾毫秒時會誤判成「原檔比備份新」。真的在備份之後存檔的話，
                # 存檔當下就會把備份刪掉，不靠這個比較。
                if os.path.getmtime(info.source) > info.saved_at + _MTIME_SLACK_S:
                    continue
            except OSError:
                pass
        out.append(info)
    out.sort(key=lambda info: -info.saved_at)
    return out


def find_backup(source: str, folder: Optional[str] = None) -> Optional[BackupInfo]:
    """開某個檔案時，有沒有比它還新的備份可以還原。"""
    if not source:
        return None
    wanted = os.path.normcase(os.path.abspath(source))
    for info in list_backups(folder):
        if info.source and os.path.normcase(os.path.abspath(info.source)) == wanted:
            return info
    return None


def load_backup(info: BackupInfo, model: Any) -> None:
    """把備份讀進 `model`，但讓它看起來是「原檔加上還沒存的改動」。

    存檔目標指回原檔（MIDI 除外：MIDI 存不下鍵道，照舊要另存新檔），並標成
    未儲存——還原只是把改動拿回來，要不要寫進原檔還是使用者決定。
    """
    if info.data_path.endswith('.xml'):
        model.load_xml(info.data_path)
    else:
        model.load_json(info.data_path)
    source = info.source
    if source and not source.lower().endswith(('.mid', '.midi')):
        model.current_file = source
    else:
        model.current_file = None
    model.dirty = True


def describe(info: BackupInfo) -> str:
    """給使用者看的一行：時間、來源、音符數。"""
    when = time.strftime('%m/%d %H:%M', time.localtime(info.saved_at))
    name = os.path.basename(info.source) if info.source else '（未存檔的新譜）'
    return '%s　%s　%d 顆音符' % (when, name, info.notes)


if __name__ == '__main__':            # pragma: no cover - 手動檢查用
    for backup in list_backups():
        print(describe(backup), '->', backup.data_path, file=sys.stdout)
