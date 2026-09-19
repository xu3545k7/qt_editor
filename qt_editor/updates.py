"""NosMania 的「更新」：用新版的 exe／zip／資料夾換掉已經裝好的那一份。

三個部分各自獨立更新，互不影響：

- **製譜器**：選一支新的 exe（或裡面有 exe 的 zip），放到舊的那一支旁邊，
  舊版改名成 `*.old-<日期>.exe`（執行中的 exe 不能直接覆蓋，改名可以）。
- **遊戲**：選新的 build（zip 或資料夾），程式檔整份覆蓋過去，
  但 `UserSongs`、`UserSongs_editor`、製譜器 exe 和啟動器寫的設定檔都保留。
- **曲庫**：選一包新的曲庫（zip 或資料夾），同名的曲目整首換掉、其他的留著
  （見 `song_library.SongLibrary.merge_from`）。

第一次安裝：遊戲本體（`install_game`）和曲庫（`install_library`）是兩包分開的 ZIP，
各自解壓到使用者選的地方，啟動器記住位置再連起來。

沒有連網：一律是使用者自己挑本機的檔案。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
from typing import Callable, List, Optional, Tuple

from .ui_text import tr

#: 更新遊戲時不能動的東西（曲庫和它的回收區、製譜器、啟動器寫的設定）
KEEP_IN_GAME_DIR = ('usersongs', 'usersongs_editor', 'nosmania_launcher.json',
                    'settings.json', '.editor_revision.json', 'qt_editor_launch.log')

Progress = Optional[Callable[[int, int, str], object]]


class UpdateError(Exception):
    pass


def _stamp() -> str:
    return time.strftime('%Y%m%d-%H%M%S')


def unpack(source: str) -> Tuple[str, Optional[str]]:
    """把來源攤成一個資料夾。回傳 (資料夾, 要刪掉的暫存目錄或 None)。"""
    source = os.path.abspath(source)
    if os.path.isdir(source):
        return source, None
    if os.path.isfile(source) and source.lower().endswith('.exe'):
        return source, None                  # 直接給一支 exe 也可以
    if not zipfile.is_zipfile(source):
        raise UpdateError(tr('這不是資料夾也不是 ZIP：%s') % os.path.basename(source))
    temp = tempfile.mkdtemp(prefix='nosmania_update_')
    with zipfile.ZipFile(source) as archive:
        archive.extractall(temp)
    return _single_child(temp), temp


def _single_child(folder: str) -> str:
    """ZIP 裡常常只包一層資料夾，直接進去那一層。"""
    entries = [e for e in os.listdir(folder) if not e.startswith('__MACOSX')]
    if len(entries) == 1 and os.path.isdir(os.path.join(folder, entries[0])):
        return os.path.join(folder, entries[0])
    return folder


def find_editor_exe(folder: str) -> str:
    """更新用的製譜器 exe：檔名有 Chart Maker／ChartEditor 的那一支。"""
    from .song_library import EDITOR_EXE_PATTERN
    if os.path.isfile(folder) and folder.lower().endswith('.exe'):
        return folder
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if name.lower().endswith('.exe') and EDITOR_EXE_PATTERN.search(name):
                return os.path.join(base, name)
    return ''


def find_build_root(folder: str) -> str:
    """遊戲 build 的根目錄：有 `<名字>.exe` 和同名 `_Data` 資料夾的那一層。"""
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if name.lower().endswith('.exe') and \
                    os.path.isdir(os.path.join(base, name[:-4] + '_Data')):
                return base
    return ''


# ── 製譜器 ───────────────────────────────────────────────────────────

def update_editor(source: str, current_exe: str, target_dir: str = '') -> str:
    """換上新的製譜器 exe，回傳新 exe 的路徑。舊的改名保留。"""
    folder, temp = unpack(source)
    try:
        new_exe = find_editor_exe(folder)
        if not new_exe:
            raise UpdateError(tr('裡面找不到製譜器的 exe（檔名要有 Chart Maker）'))
        target_dir = target_dir or (os.path.dirname(current_exe) if current_exe else '')
        if not target_dir or not os.path.isdir(target_dir):
            raise UpdateError(tr('不知道要裝到哪裡：先用「製譜器位置…」指定現在那一支'))
        dest = os.path.join(target_dir, os.path.basename(new_exe))
        if current_exe and os.path.isfile(current_exe) and \
                os.path.normcase(current_exe) != os.path.normcase(dest):
            _retire(current_exe)
        elif os.path.isfile(dest):
            _retire(dest)
        shutil.copy2(new_exe, dest)
        return dest
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


def _retire(path: str) -> str:
    """把舊的 exe 改名收起來（執行中的 exe 可以改名，不能覆蓋）。"""
    stem, ext = os.path.splitext(path)
    old = '%s.old-%s%s' % (stem, _stamp(), ext)
    try:
        os.replace(path, old)
    except OSError:
        return ''
    return old


# ── 遊戲 ─────────────────────────────────────────────────────────────

def update_game(source: str, game_dir: str, progress: Progress = None) -> Tuple[int, List[str]]:
    """用新的 build 覆蓋遊戲程式檔，曲庫與製譜器留著。回傳 (複製的檔案數, 保留的東西)。"""
    if not game_dir or not os.path.isdir(game_dir):
        raise UpdateError(tr('找不到遊戲資料夾'))
    folder, temp = unpack(source)
    try:
        root = find_build_root(folder)
        if not root:
            raise UpdateError(tr('裡面找不到遊戲 build（要有 exe 和同名的 _Data 資料夾）'))
        files = []
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in sorted(dirs) if d.lower() not in KEEP_IN_GAME_DIR]
            for name in sorted(names):
                if os.path.relpath(base, root) == '.' and name.lower() in KEEP_IN_GAME_DIR:
                    continue
                files.append(os.path.join(base, name))
        kept = [name for name in sorted(os.listdir(game_dir))
                if name.lower() in KEEP_IN_GAME_DIR]
        copied = 0
        for i, path in enumerate(files):
            if progress is not None and progress(i, len(files), os.path.basename(path)) is False:
                break
            rel = os.path.relpath(path, root)
            dest = os.path.join(game_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isfile(dest) and dest.lower().endswith('.exe'):
                # 遊戲沒關就換不掉 exe：改名再放新的
                try:
                    shutil.copy2(path, dest)
                except PermissionError:
                    _retire(dest)
                    shutil.copy2(path, dest)
            else:
                shutil.copy2(path, dest)
            copied += 1
        if progress is not None:
            progress(len(files), len(files), '')
        return copied, kept
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


# ── 曲庫 ─────────────────────────────────────────────────────────────

def update_library(source: str, library, progress: Progress = None) -> Tuple[List[str], List[str]]:
    """用新的一包曲庫更新：同名整首換掉、其他留著。回傳 (更新的曲目, 失敗說明)。"""
    folder, temp = unpack(source)
    try:
        def step(done: int, total: int, name: str):
            return progress(done, total, name) if progress is not None else None
        return library.merge_from(folder, progress=step)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


# ── 從 ZIP 裝一份遊戲 ────────────────────────────────────────────────

def inspect_build(folder: str) -> Dict[str, str]:
    """一個資料夾裡有什麼：遊戲 exe、製譜器 exe、曲庫。

    使用者拿到的 ZIP 有好幾種：只有遊戲、遊戲＋製譜器、遊戲＋製譜器＋曲庫。
    裝完自己認出來，省得再一個一個去「位置…」指。
    """
    game = find_build_root(folder)
    exe = ''
    if game:
        for name in sorted(os.listdir(game)):
            if name.lower().endswith('.exe') and os.path.isdir(os.path.join(game, name[:-4] + '_Data')):
                exe = os.path.join(game, name)
                break
    editor = find_editor_exe(game or folder) or find_editor_exe(folder)
    library = ''
    for base in (game, folder):
        if not base:
            continue
        candidate = os.path.join(base, 'UserSongs')
        if os.path.isfile(os.path.join(candidate, 'library.json')):
            library = candidate
            break
    if not library:
        from .song_library import INDEX_FILE
        for base, dirs, files in os.walk(folder):
            dirs[:] = [d for d in sorted(dirs) if not d.endswith('_editor')]
            if INDEX_FILE in files and os.path.basename(base).lower() == 'usersongs':
                library = base
                break
    return {'folder': game or folder, 'game': exe, 'editor': editor, 'library': library}


def install_game(source: str, dest_dir: str, progress: Progress = None) -> Dict[str, str]:
    """把一份遊戲（ZIP 或資料夾）裝到 dest_dir，回傳裡面偵測到的東西。"""
    source = os.path.abspath(source)
    dest_dir = os.path.abspath(dest_dir)
    if os.path.isdir(source):
        if os.path.normcase(source) != os.path.normcase(dest_dir):
            _copy_tree(source, dest_dir, progress)
    else:
        if not zipfile.is_zipfile(source):
            raise UpdateError(tr('這不是資料夾也不是 ZIP：%s') % os.path.basename(source))
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(source) as archive:
            names = [n for n in archive.namelist() if not n.endswith('/')]
            for i, name in enumerate(names):
                if progress is not None and progress(i, len(names), os.path.basename(name)) is False:
                    break
                archive.extract(name, dest_dir)
            if progress is not None:
                progress(len(names), len(names), '')
    found = inspect_build(_single_child(dest_dir) if os.path.isdir(dest_dir) else dest_dir)
    if not found['game']:
        raise UpdateError(tr('裡面找不到遊戲 build（要有 exe 和同名的 _Data 資料夾）'))
    return found


def library_prefix(names: List[str]) -> Optional[str]:
    """ZIP 裡曲庫的根：`library.json` 所在的那層（'' 表示在 ZIP 最上層）。沒有就 None。"""
    from .song_library import INDEX_FILE
    best = None
    for name in names:
        if name.rsplit('/', 1)[-1] != INDEX_FILE or name.count('/') > 1:
            continue
        prefix = name[:-len(INDEX_FILE)]
        if best is None or len(prefix) < len(best):
            best = prefix
    return best


def install_library(source: str, dest_parent: str, progress: Progress = None) -> str:
    """把一份曲庫（ZIP 或資料夾）裝好，回傳曲庫資料夾。

    - 資料夾：本身（或裡面的 UserSongs）有 library.json 就直接用，不複製。
    - ZIP：解壓到 dest_parent。ZIP 裡包了一層（UserSongs/…）就照那一層；
      library.json 直接在最上層就解到 dest_parent/UserSongs。
      目的地已經有曲目的話不蓋過去 —— 要合併請用「更新曲庫」。
    """
    from .song_library import INDEX_FILE
    source = os.path.abspath(source)
    if os.path.isdir(source):
        for folder in (source, os.path.join(source, 'UserSongs')):
            if os.path.isfile(os.path.join(folder, INDEX_FILE)):
                return folder
        raise UpdateError(tr('這個資料夾不是曲庫（裡面沒有 library.json）'))
    if not zipfile.is_zipfile(source):
        raise UpdateError(tr('這不是資料夾也不是 ZIP：%s') % os.path.basename(source))
    with zipfile.ZipFile(source) as archive:
        names = [n for n in archive.namelist() if not n.endswith('/')]
        prefix = library_prefix(names)
        if prefix is None:
            raise UpdateError(tr('ZIP 裡找不到曲庫（要有 library.json）'))
        if prefix:
            root = os.path.join(dest_parent, prefix.rstrip('/'))
            extract_to = dest_parent
        else:
            root = os.path.join(dest_parent, 'UserSongs')
            extract_to = root
        if os.path.isdir(root) and any(os.path.isdir(os.path.join(root, n)) for n in os.listdir(root)):
            raise UpdateError(tr('這裡已經有一份曲庫：\n%s\n\n要把新的曲目併進去，請用「更新曲庫」。') % root)
        os.makedirs(extract_to, exist_ok=True)
        for i, name in enumerate(names):
            if progress is not None and progress(i, len(names), os.path.basename(name)) is False:
                break
            archive.extract(name, extract_to)
        if progress is not None:
            progress(len(names), len(names), '')
    if not os.path.isfile(os.path.join(root, INDEX_FILE)):
        raise UpdateError(tr('解壓沒有完成（被取消了？）：%s') % root)
    return root


def _copy_tree(source: str, dest: str, progress: Progress = None) -> None:
    files = []
    for base, dirs, names in os.walk(source):
        dirs[:] = sorted(dirs)
        files += [os.path.join(base, n) for n in sorted(names)]
    for i, path in enumerate(files):
        if progress is not None and progress(i, len(files), os.path.basename(path)) is False:
            break
        target = os.path.join(dest, os.path.relpath(path, source))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
    if progress is not None:
        progress(len(files), len(files), '')
