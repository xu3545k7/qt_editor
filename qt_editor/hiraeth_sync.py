"""一鍵同步：nos-clone 曲庫 ←→ Hiraeth 套件，兩邊各缺什麼就補什麼，不刪任何東西。

同一首歌的 package_id 每次都一樣、version 遞增，所以匯入＝更新。為了不要每次
都重算一整個曲庫（100 首光是混音就要好幾分鐘），這裡記住每首歌「上次同步時
檔案長什麼樣」（檔名＋大小＋修改時間的雜湊）存在

    <曲庫>_editor/hiraeth_sync.json

沒動過、而且 Hiraeth 那邊也還在的，就跳過。勾「全部重來」可以無視這份紀錄。

反方向（Hiraeth → nos-clone）是把它裝好的檔案挖回來：Hiraeth 匯入後不留原始
ZIP，但 wav、四個難度的 XML 和曲繪都攤在遊戲資料夾裡（見 `hiraeth_tools`），
組回歌曲包的樣子就能用既有的「匯入 Hiraeth 歌曲包」轉成 nos-clone 曲目。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import hiraeth_export as H
from . import hiraeth_tools as T
from .ui_text import tr

STATE_FILE = 'hiraeth_sync.json'
#: 輸出規則改版就 +1：上次同步用舊規則做的包會被重做一次。
#: 2 = 結尾不再留一大段沒事做（見 hiraeth_export.trim_package_tail）
#: 3 = 顫音不再改成長押（Hiraeth importer 改成收 64）
BUILDER_VERSION = 3
Progress = Optional[Callable[[int, int, str], object]]


def state_path(library_root: str) -> str:
    editor_dir = os.path.join(os.path.dirname(os.path.abspath(library_root)),
                              os.path.basename(os.path.abspath(library_root)) + '_editor')
    return os.path.join(editor_dir, STATE_FILE)


def load_state(library_root: str) -> Dict[str, Any]:
    try:
        with open(state_path(library_root), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(library_root: str, state: Dict[str, Any]) -> None:
    path = state_path(library_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def signature(song_dir: str) -> str:
    """樂曲資料夾的指紋：每個檔案的相對路徑、大小、修改時間。"""
    digest = hashlib.sha1()
    for base, dirs, files in os.walk(song_dir):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            digest.update(os.path.relpath(path, song_dir).encode('utf-8', 'ignore'))
            digest.update(b'%d|%d' % (stat.st_size, int(stat.st_mtime)))
    return digest.hexdigest()


def folder_marks(state: Dict[str, Any]) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """曲庫資料夾 → (Hiraeth 的 package_id, 紀錄)。從 Hiraeth 搬回來的曲子靠這個
    記住「它在那邊本來就有」，不然下次同步會被當成新曲送回去，變成兩份。"""
    out: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for package_id, mark in state.items():
        folder = str((mark or {}).get('library_folder') or '')
        if folder:
            out[folder] = (package_id, mark)
    return out


def _mark_for(plan: Any, state: Dict[str, Any],
              by_folder: Dict[str, Tuple[str, Dict[str, Any]]]) -> Tuple[str, Dict[str, Any]]:
    """這個包在 Hiraeth 對應的 package_id 與上次同步的紀錄。"""
    folder = os.path.basename(os.path.normpath(plan.source_dir)) if plan.source_dir else ''
    if folder in by_folder:
        return by_folder[folder]
    return plan.package_id, state.get(plan.package_id) or {}


def plan_sync(library_root: str, installed_keys: Optional[set] = None,
              force: bool = False) -> Tuple[List[Any], List[Any], List[str]]:
    """回傳 (要同步的包, 沒變略過的包, 讀不出來的說明)。"""
    plans, skipped = H.plan_library(library_root)
    state = load_state(library_root)
    by_folder = folder_marks(state)
    todo, unchanged = [], []
    for plan in plans:
        package_id, mark = _mark_for(plan, state, by_folder)
        same = (not force and plan.source_dir
                and mark.get('signature') == signature(plan.source_dir)
                and int(mark.get('builder') or 0) >= BUILDER_VERSION)
        if same and (installed_keys is None or package_id in installed_keys):
            unchanged.append(plan)
        else:
            todo.append(plan)
    return todo, unchanged, skipped


def installed_keys(hiraeth_root: str) -> set:
    try:
        return {str(song.get('key') or '') for song in T.list_songs(hiraeth_root)}
    except T.HiraethError:
        return set()


def missing_in_library(library_root: str, hiraeth_root: str,
                       plans: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Hiraeth 有、nos-clone 沒有的曲目（用 package_id 對）。"""
    if plans is None:
        plans, _skipped = H.plan_library(library_root)
    known = {plan.package_id for plan in plans}
    state = load_state(library_root)
    for package_id, mark in state.items():
        folder = str(mark.get('library_folder') or '')
        if folder and os.path.isdir(os.path.join(library_root, folder)):
            known.add(package_id)
    out = []
    for song in T.list_songs(hiraeth_root):
        package_id = str(song.get('package_id') or song.get('key') or '')
        if package_id and package_id not in known:
            out.append(dict(song, package_id=package_id))
    return out


def pull_into_library(library, hiraeth_root: str, songs: List[Dict[str, Any]],
                      category: str = 'Other',
                      progress: Progress = None) -> Tuple[List[str], List[str]]:
    """把 Hiraeth 的曲目挖回 nos-clone 曲庫。回傳 (資料夾名, 失敗說明)。"""
    import zipfile
    done: List[str] = []
    errors: List[str] = []
    state = load_state(library.root)
    work = tempfile.mkdtemp(prefix='nosmania_pull_')
    try:
        for i, song in enumerate(songs):
            title = str(song.get('title') or song['package_id'])
            if progress is not None and progress(i, len(songs), title) is False:
                break
            folder = os.path.join(work, 'pkg%d' % i)
            try:
                T.extract_package(hiraeth_root, song['package_id'], song, folder)
                zip_path = folder + '.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name in sorted(os.listdir(folder)):
                        archive.write(os.path.join(folder, name), name)
                name = library.import_hiraeth_zip(zip_path, category)
                done.append(name)
                # 從 Hiraeth 搬回來的本來就和那邊一樣，不用再送回去
                state[song['package_id']] = dict(state.get(song['package_id']) or {},
                                                 library_folder=name, title=title,
                                                 key=str(song.get('key') or ''),
                                                 builder=BUILDER_VERSION)
            except Exception as exc:                    # noqa: BLE001
                errors.append('%s：%s' % (title, exc))
        if done:
            for package_id, mark in list(state.items()):
                folder = str(mark.get('library_folder') or '')
                if folder and os.path.isdir(os.path.join(library.root, folder)):
                    mark['signature'] = signature(os.path.join(library.root, folder))
            save_state(library.root, state)
        if progress is not None:
            progress(len(songs), len(songs), '')
        return done, errors
    finally:
        shutil.rmtree(work, ignore_errors=True)


def sync(library_root: str, hiraeth_root: str, force: bool = False,
         progress: Progress = None, renderer=H.render_piano,
         hold_gap_ms: int = H.DEFAULT_HOLD_GAP_MS, mix_piano: bool = True,
         lead_in_bars: int = H.DEFAULT_LEAD_IN_BARS,
         library=None, pull: bool = True, category: str = 'Other') -> Dict[str, Any]:
    """兩邊互補：nos-clone 缺的從 Hiraeth 挖回來，Hiraeth 缺的（或改過的）輸出過去。

    不刪任何東西。回傳給使用者看的摘要。
    """
    keys = installed_keys(hiraeth_root)
    todo, unchanged, skipped = plan_sync(library_root, keys, force)
    summary: Dict[str, Any] = {'exported': 0, 'imported': 0, 'unchanged': len(unchanged),
                               'pulled': [], 'skipped': skipped, 'errors': [],
                               'cancelled': False}

    # ── Hiraeth → nos-clone（只補缺的）────────────────────────────
    if pull and library is not None:
        try:
            missing = missing_in_library(library_root, hiraeth_root)
        except T.HiraethError as exc:
            missing = []
            summary['errors'].append(str(exc))
        if missing:
            pulled, errors = pull_into_library(library, hiraeth_root, missing,
                                               category=category, progress=progress)
            summary['pulled'] = pulled
            summary['errors'] += errors

    if not todo:
        return summary
    work = tempfile.mkdtemp(prefix='nosmania_sync_')
    state = load_state(library_root)
    try:
        version = H.default_version()
        zips: List[Tuple[Any, str]] = []
        total = len(todo) + 1
        by_folder = folder_marks(state)
        for i, plan in enumerate(todo):
            if progress is not None and progress(i, total, plan.title) is False:
                summary['cancelled'] = True
                break
            # 本來就是從 Hiraeth 搬回來的：沿用它那邊的 package_id，改動才是「更新」
            mapped, _mark = _mark_for(plan, state, by_folder)
            plan.package_id = mapped
            plan.mix_piano = mix_piano
            plan.lead_in_bars = lead_in_bars
            result = H.build_package(plan, work, version=version, renderer=renderer,
                                     hold_gap_ms=hold_gap_ms)
            if result.error:
                summary['errors'].append('%s：%s' % (plan.title, result.error))
                continue
            zips.append((plan, result.zip_path))
        summary['exported'] = len(zips)
        if zips:
            if progress is not None:
                progress(len(todo), total, tr('匯入 Hiraeth…'))
            done, errors = T.import_packages(hiraeth_root, [path for _plan, path in zips])
            summary['imported'] = len(done)
            summary['errors'] += errors
            if done:
                for plan, _path in zips:
                    folder = (os.path.basename(os.path.normpath(plan.source_dir))
                              if plan.source_dir else '')
                    mark = dict(state.get(plan.package_id) or {})
                    mark.update(signature=signature(plan.source_dir) if plan.source_dir else '',
                                title=plan.title, key=plan.package_id, version=version,
                                builder=BUILDER_VERSION)
                    if folder:
                        mark['library_folder'] = folder
                    state[plan.package_id] = mark
                save_state(library_root, state)
        if progress is not None:
            progress(total, total, '')
        return summary
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── 單向覆蓋 ─────────────────────────────────────────────────────────
#
# 同步是「兩邊互補、不刪東西」。覆蓋是「以一邊為準」：
# - nos-clone → Hiraeth：每一首都重新輸出送過去（不管有沒有改過），同名的就被換掉。
# - Hiraeth → nos-clone：每一首都從 Hiraeth 挖回來換掉 nos-clone 那份。
#   ⚠ 這個方向會失真：XML 沒有 JSON 那麼多資訊（Soft／Staccato 變 Tap、自動彈的
#   音符不見、長押被縮成官方長度），被換掉的原檔會搬到回收區。
# 「連另一邊多出來的也刪掉」是另外勾的，而且新手教學一律不刪（Hiraeth 那邊本來
# 就沒有教學曲，不排除的話等於把教學全部丟掉）。

def folder_for_package(library_root: str, package_id: str,
                       plans: Optional[List[Any]] = None) -> str:
    """nos-clone 曲庫裡對應這個 Hiraeth 曲目的資料夾名（沒有就空字串）。"""
    state = load_state(library_root)
    mark = state.get(package_id) or {}
    folder = str(mark.get('library_folder') or '')
    if folder and os.path.isdir(os.path.join(library_root, folder)):
        return folder
    if plans is None:
        plans, _skipped = H.plan_library(library_root)
    for plan in plans:
        if plan.package_id == package_id and plan.source_dir:
            return os.path.basename(os.path.normpath(plan.source_dir))
    return ''


def push_overwrite(library_root: str, hiraeth_root: str, library=None,
                   delete_extra: bool = False, progress: Progress = None,
                   **build_options: Any) -> Dict[str, Any]:
    """nos-clone → Hiraeth：全部重新送過去；勾了 delete_extra 就刪掉 Hiraeth 多出來的。"""
    summary = sync(library_root, hiraeth_root, force=True, progress=progress,
                   library=library, pull=False, **build_options)
    summary['deleted'] = []
    if delete_extra:
        plans, _skipped = H.plan_library(library_root)
        state = load_state(library_root)
        by_folder = folder_marks(state)
        ours = {_mark_for(plan, state, by_folder)[0] for plan in plans}
        extra = [key for key in installed_keys(hiraeth_root) if key and key not in ours]
        if extra:
            T.delete_songs(hiraeth_root, extra)
            summary['deleted'] = extra
    return summary


def pull_overwrite(library, hiraeth_root: str, delete_extra: bool = False,
                   progress: Progress = None, category: str = 'Other') -> Dict[str, Any]:
    """Hiraeth → nos-clone：每首都從 Hiraeth 挖回來換掉；勾了 delete_extra 就把
    nos-clone 多出來的（新手教學除外）搬到回收區。分類沿用原本那首的。"""
    summary: Dict[str, Any] = {'replaced': [], 'added': [], 'deleted': [], 'errors': []}
    songs = T.list_songs(hiraeth_root)
    plans, _skipped = H.plan_library(library.root)
    existing = {s.folder: s.categories for s in library.songs()}
    kept_folders = set()
    for i, song in enumerate(songs):
        package_id = str(song.get('package_id') or song.get('key') or '')
        title = str(song.get('title') or package_id)
        if progress is not None and progress(i, len(songs), title) is False:
            break
        old = folder_for_package(library.root, package_id, plans)
        cats = existing.get(old) if old else None
        try:
            # 先把新的匯進來，成功了才把舊的移走：挖檔失敗時原本那首還在原位
            done, errors = pull_into_library(library, hiraeth_root,
                                             [dict(song, package_id=package_id)],
                                             category=(cats or [category])[0])
        except Exception as exc:                        # noqa: BLE001
            summary['errors'].append('%s：%s' % (title, exc))
            continue
        summary['errors'] += errors
        if done and old and old not in done and os.path.isdir(os.path.join(library.root, old)):
            library._move_to_trash(old)
            library._set_index_entry(old, None, remove=True)
        elif old:
            kept_folders.add(old)             # 沒換成功：舊的留著，也不算「多出來的」
        for name in done:
            kept_folders.add(name)
            if cats:
                library._set_index_entry(name, list(cats))
            (summary['replaced'] if old else summary['added']).append(name)
    if progress is not None:
        progress(len(songs), len(songs), '')
    if delete_extra and not summary['errors']:
        for song in library.songs():
            if song.folder in kept_folders or song.folder.startswith(H.TUTORIAL_PREFIX) \
                    or H.TUTORIAL_PREFIX in (song.categories or []):
                continue
            library._move_to_trash(song.folder)
            library._set_index_entry(song.folder, None, remove=True)
            summary['deleted'].append(song.folder)
    library.notify_game('')
    return summary
