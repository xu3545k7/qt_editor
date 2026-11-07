"""Hiraeth（PAN 歌曲管理版）套件：從 NosMania 直接操作它的曲庫。

Hiraeth 的管理器本體是 `tools/manager_backend.py`，它吃一個 JSON 工作檔、
寫一個 JSON 結果檔，動作有 `list` / `import` / `delete`，而且要用套件自己帶的
`tools/runtime/python/python.exe` 跑（那份 runtime 才有它要的環境）。

    python.exe tools/manager_backend.py <job.json> <out.json>

遊戲跑的時候 backend 會拿不到 `launcher.lock`，回傳「Please close the game」，
所以匯入前要先確認遊戲關了（`game_running()` / `stop_game()`）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .ui_text import tr

MANAGER_BAT = 'SONG_MANAGER.bat'
BACKEND = os.path.join('tools', 'manager_backend.py')
RUNTIME_PYTHON = os.path.join('tools', 'runtime', 'python', 'python.exe')
#: Hiraeth 的遊戲是 spice 起的（contents/spice64.exe）
GAME_PROCESSES = ('spice64.exe', 'spice.exe')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


#: 真按鍵音要靠改過的 importer：原版硬擋 `velocity != 0`，會把我們保留了
#: 力度的包退件（訊息是英文的
#: `This package format requires background-only charts`）。
IMPORTER = os.path.join('tools', 'import_tools', 'importer.py')
KEYSOUND_BACKUP = os.path.join('tools', 'backup_before_keysound', 'importer.py')
#: 改過之後會出現的字串，用來判斷這份 importer 收不收按鍵音。
KEYSOUND_PATCH = '0<=velocity<=127'
#: 原版那一行的特徵。**用字串比對而不是行號**：Hiraeth 改版時行號一定會動
#: （V2 在第 74 行），但這是格式規則、文字不太會變。抓不到就不要亂改。
KEYSOUND_STOCK_MARK = "n.findtext('velocity','-1'))!=0"
#: 換上去的兩行。縮排沿用原本那一行的。
KEYSOUND_NEW_LINES = (
    "velocity=int(n.findtext('velocity','-1'))",
    "if not 0<=velocity<=127:raise ValueError('Sub-note velocity must be 0..127')",
)


class HiraethError(Exception):
    pass


def importer_accepts_keysound(root: str) -> Optional[bool]:
    """這份安裝的 importer 收不收帶按鍵音的包？讀不到檔案回 None。

    **Hiraeth 一更新就會把我們的修改蓋回去**，然後勾了「保留按鍵音」匯出的
    包會被退件，看起來像我們的包壞了。所以匯出前先問一下。

    備份在 `tools/backup_before_keysound/importer.py`（當初為顫音改它時也是
    同一個作法，見 `backup_before_trill`）。
    """
    path = os.path.join(root or '', IMPORTER)
    try:
        with io.open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
    except OSError:
        return None
    return KEYSOUND_PATCH in text


def apply_keysound_patch(root: str) -> str:
    """把按鍵音修補套到這份 Hiraeth 的 importer 上，回傳做了什麼。

    **兩個版本都適用**：靠 `KEYSOUND_STOCK_MARK` 這個字串定位，不是行號
    （V2 在第 74 行，Hiraeth 改版一定會移動）。找不到那一行就丟
    `HiraethError`，不會亂猜著改——寧可叫人自己看，也不要把別人的安裝改壞。

    改之前先備份到 `tools/backup_before_keysound/importer.py`（已經有備份就
    不覆蓋，那是**原版**，留著才能還原）。已經修補過的直接回報、不重做。
    """
    path = os.path.join(root or '', IMPORTER)
    try:
        with io.open(path, encoding='utf-8', errors='replace', newline='') as handle:
            text = handle.read()
    except OSError as exc:
        raise HiraethError(tr('讀不到 importer：%s') % exc)
    if KEYSOUND_PATCH in text:
        return tr('這份 importer 已經可以收按鍵音了，沒有改動。')

    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if KEYSOUND_STOCK_MARK in line]
    if len(hits) != 1:
        raise HiraethError(tr(
            '在 importer 裡找到 %d 處擋力度的程式碼（預期剛好 1 處），'
            '不敢亂改。請自己比對 %s。') % (len(hits), KEYSOUND_BACKUP))

    index = hits[0]
    original = lines[index]
    indent = original[:len(original) - len(original.lstrip())]
    ending = '\r\n' if original.endswith('\r\n') else '\n'
    lines[index:index + 1] = [indent + body + ending
                              for body in KEYSOUND_NEW_LINES]

    backup = os.path.join(root, KEYSOUND_BACKUP)
    kept = False
    if not os.path.exists(backup):
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        with io.open(backup, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        kept = True
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(''.join(lines))
    return tr('已套用（改的是第 %d 行）%s') % (
        index + 1, tr('，原版備份到 %s') % KEYSOUND_BACKUP if kept
        else tr('，原本就有備份，沒有覆蓋'))


def is_root(path: str) -> bool:
    """這個資料夾是不是 Hiraeth 套件的根目錄。"""
    return bool(path) and os.path.isfile(os.path.join(path, MANAGER_BAT)) \
        and os.path.isfile(os.path.join(path, BACKEND))


def find_root(start: str) -> str:
    """從使用者選的位置找出套件根目錄：它自己、或它底下一層（解壓後常多包一層）。"""
    start = os.path.abspath(start or '')
    if not start:
        return ''
    if os.path.isfile(start):
        start = os.path.dirname(start)
    if is_root(start):
        return start
    try:
        names = sorted(os.listdir(start))
    except OSError:
        return ''
    for name in names:
        child = os.path.join(start, name)
        if os.path.isdir(child) and is_root(child):
            return child
    return ''


def saved_root() -> str:
    """偏好設定記住的套件位置（確認過還在）。"""
    from .settings import settings
    root = settings.get('hiraeth_root', '') or ''
    return root if is_root(root) else ''


def remember_root(root: str) -> None:
    from .settings import settings
    settings.set('hiraeth_root', root)


def game_running() -> bool:
    for name in GAME_PROCESSES:
        try:
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % name, '/NH'],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=NO_WINDOW).stdout
        except Exception:                               # noqa: BLE001
            continue
        if name.lower() in out.lower():
            return True
    return False


def stop_game() -> List[str]:
    """強制停止 Hiraeth 的遊戲行程，回傳真的停掉的那幾個。"""
    stopped = []
    for name in GAME_PROCESSES:
        try:
            done = subprocess.run(['taskkill', '/F', '/T', '/IM', name],
                                  capture_output=True, text=True, timeout=10,
                                  creationflags=NO_WINDOW)
        except Exception:                               # noqa: BLE001
            continue
        if done.returncode == 0:
            stopped.append(name)
    return stopped


def open_manager(root: str) -> None:
    """開 Hiraeth 自己的管理器視窗（SONG_MANAGER.bat）。"""
    if not is_root(root):
        raise HiraethError(tr('這不是 Hiraeth 套件資料夾（找不到 SONG_MANAGER.bat）'))
    subprocess.Popen(['cmd', '/c', 'start', '', os.path.join(root, MANAGER_BAT)],
                     cwd=root, creationflags=NO_WINDOW)


def run_job(root: str, job: Dict[str, Any], timeout: int = 1800) -> Dict[str, Any]:
    """跑一個管理器工作（list / import / delete），回傳它的 JSON 結果。"""
    if not is_root(root):
        raise HiraethError(tr('這不是 Hiraeth 套件資料夾（找不到 SONG_MANAGER.bat）'))
    python = os.path.join(root, RUNTIME_PYTHON)
    if not os.path.isfile(python):
        python = 'python'
    work = tempfile.mkdtemp(prefix='nosmania_hiraeth_')
    job_path = os.path.join(work, 'job-%s.json' % uuid.uuid4().hex[:8])
    out_path = job_path + '.out'
    try:
        with open(job_path, 'w', encoding='utf-8') as fh:
            json.dump(job, fh, ensure_ascii=False)
        done = subprocess.run([python, os.path.join(root, BACKEND), job_path, out_path],
                              cwd=root, capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW)
        if not os.path.isfile(out_path):
            raise HiraethError((done.stderr or done.stdout or '').strip()[-400:]
                               or tr('Hiraeth 管理器沒有回應'))
        with open(out_path, encoding='utf-8-sig') as fh:
            result = json.load(fh)
    except subprocess.TimeoutExpired:
        raise HiraethError(tr('Hiraeth 管理器太久沒回應'))
    except (OSError, ValueError) as exc:
        raise HiraethError(str(exc))
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    if not result.get('ok') and result.get('error'):
        raise HiraethError(str(result['error']))
    return result


def list_songs(root: str) -> List[Dict[str, Any]]:
    """套件裡已經匯入的曲目。"""
    return list(run_job(root, {'action': 'list'}).get('songs') or [])


def import_packages(root: str, paths: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """匯入歌曲包（ZIP 或歌曲資料夾），回傳 (成功的, 失敗說明)。"""
    if not paths:
        return [], []
    if game_running():
        raise HiraethError(tr('Hiraeth 的遊戲正開著，匯入前要先停止'))
    result = run_job(root, {'action': 'import', 'paths': [os.path.abspath(p) for p in paths]})
    errors = ['%s：%s' % (os.path.basename(str(e.get('path') or '')), e.get('error'))
              for e in (result.get('errors') or [])]
    return list(result.get('results') or []), errors


def delete_songs(root: str, keys: List[str]) -> List[str]:
    """從套件裡刪掉這些曲目（key 是 list 回來的那個）。"""
    if not keys:
        return []
    if game_running():
        raise HiraethError(tr('Hiraeth 的遊戲正開著，刪除前要先停止'))
    run_job(root, {'action': 'delete', 'keys': list(keys)})
    return list(keys)


def song_label(song: Dict[str, Any]) -> str:
    return '%s / %s' % (song.get('title') or '', song.get('artist') or '')


def default_root_guess(start: Optional[str] = None) -> str:
    """還沒設定過時猜一下：下載資料夾裡解壓好的那一份。"""
    bases = [start] if start else []
    bases.append(os.path.join(os.path.expanduser('~'), 'Downloads'))
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        root = find_root(base)
        if root:
            return root
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            if 'hiraeth' not in name.lower():
                continue
            root = find_root(os.path.join(base, name))
            if root:
                return root
    return ''


# ── 反向：把 Hiraeth 裡的曲子挖出來 ──────────────────────────────────
#
# 匯入之後 Hiraeth 不留原始 ZIP，檔案是攤在遊戲資料夾裡的：
#   contents/data/sound/music/<basename>/<basename>.wav          音樂
#   contents/data/sound/music/<basename>/<basename>_00normal.xml 譜面（四個難度）
#   contents/data_mods/.../jk<index>_l.png                       曲繪
# `tools/song_library/imported.json` 記著每包的 basename、index 與檔案清單。

IMPORTED_JSON = os.path.join('tools', 'song_library', 'imported.json')
SLOT_SUFFIX = (('normal', '_00normal.xml'), ('hard', '_01hard.xml'),
               ('expert', '_02extreme.xml'), ('real', '_03real.xml'))
LEVEL_PREFIX = (('normal', 'N'), ('hard', 'H'), ('expert', 'EX'), ('real', 'REAL'))


def imported_packages(root: str) -> Dict[str, Dict[str, Any]]:
    """package_id → {index, basename, version, files}。"""
    try:
        with open(os.path.join(root, IMPORTED_JSON), encoding='utf-8-sig') as fh:
            data = json.load(fh)
        packages = data.get('packages') if isinstance(data, dict) else None
        return packages if isinstance(packages, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_levels(text: str) -> Dict[str, float]:
    """`N 7 / H 9 / EX 12 / REAL 3.5` → {'normal': 7, ...}。"""
    out: Dict[str, float] = {}
    for part in str(text or '').split('/'):
        chunk = part.strip().split()
        if len(chunk) != 2:
            continue
        tag, value = chunk[0].upper(), chunk[1]
        for slot, prefix in LEVEL_PREFIX:
            if tag == prefix:
                try:
                    number = float(value)
                except ValueError:
                    continue
                out[slot] = int(number) if number == int(number) else number
    return out


def extract_package(root: str, package_id: str, song: Dict[str, Any], out_dir: str) -> str:
    """把 Hiraeth 裡的一首挖成 Hiraeth 歌曲包的樣子（song.json + wav + xml + 曲繪）。

    回傳那個資料夾。給 `SongLibrary.import_hiraeth_zip` 用（先壓成 ZIP）。
    """
    import shutil as _shutil
    package = imported_packages(root).get(package_id) or {}
    basename = str(package.get('basename') or '')
    if not basename:
        raise HiraethError(tr('Hiraeth 裡找不到這首的檔案：%s') % package_id)
    music_dir = os.path.join(root, 'contents', 'data', 'sound', 'music', basename)
    wav = os.path.join(music_dir, basename + '.wav')
    if not os.path.isfile(wav):
        raise HiraethError(tr('Hiraeth 裡找不到這首的音樂檔：%s') % package_id)
    os.makedirs(out_dir, exist_ok=True)
    _shutil.copy2(wav, os.path.join(out_dir, 'music.wav'))
    levels = parse_levels(song.get('levels'))
    charts: Dict[str, Any] = {}
    for slot, suffix in SLOT_SUFFIX:
        path = os.path.join(music_dir, basename + suffix)
        if not os.path.isfile(path):
            continue
        _shutil.copy2(path, os.path.join(out_dir, slot + '.xml'))
        charts[slot] = levels.get(slot, 1)
    if not charts:
        raise HiraethError(tr('Hiraeth 裡找不到這首的譜面：%s') % package_id)
    cover = _package_cover(root, package)
    if cover:
        _shutil.copy2(cover, os.path.join(out_dir, 'cover.png'))
    spec = {'format': 1, 'package_id': package_id, 'title': str(song.get('title') or package_id),
            'artist': str(song.get('artist') or ''), 'charts': charts,
            'version': int(package.get('version') or 1)}
    with open(os.path.join(out_dir, 'song.json'), 'w', encoding='utf-8') as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
    return out_dir


def _package_cover(root: str, package: Dict[str, Any]) -> str:
    """大張的曲繪（jk<index>_l.png）；找不到就退而求其次拿任何一張 png。"""
    index = str(package.get('index') or '')
    names = list((package.get('files') or {}).keys())
    wanted = [n for n in names if n.lower().endswith('.png')]
    best = [n for n in wanted if ('jk%s_l' % index) in n] or \
           [n for n in wanted if '_l.png' in n.lower()] or wanted
    for name in best:
        path = os.path.join(root, name.replace('/', os.sep))
        if os.path.isfile(path):
            return path
    return ''


LAUNCH_SCRIPT = os.path.join('tools', 'launch_local.py')


def can_launch(root: str) -> bool:
    """這份套件能不能直接開遊戲（要有它自己的 runtime 和 launch_local.py）。"""
    return is_root(root) and os.path.isfile(os.path.join(root, LAUNCH_SCRIPT)) \
        and os.path.isfile(os.path.join(root, RUNTIME_PYTHON))


def launch_game(root: str) -> None:
    """開 Hiraeth 的遊戲。

    它的管理器（song_manager.ps1）就是這樣做的：用套件自己帶的 python 跑
    `tools/launch_local.py`——那支會先起本機伺服器再叫 spice64，中間還會抓著
    `launcher.lock`，所以遊戲開著時匯入會被擋下來。
    """
    if not can_launch(root):
        raise HiraethError(tr('這份 Hiraeth 套件不完整，開不了遊戲'))
    subprocess.Popen([os.path.join(root, RUNTIME_PYTHON), os.path.join(root, LAUNCH_SCRIPT)],
                     cwd=os.path.join(root, 'tools'), creationflags=NO_WINDOW)
