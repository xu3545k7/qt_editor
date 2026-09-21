"""nos-clone 曲庫（UserSongs）的讀寫：製譜器的「曲庫管理」用這一層。

寫法照遊戲 `ExternalSongLibrary` 的規則：
- `library.json` 才是權威：`songs[]` 每首 `id` / `folderName` / `category` / `categories`，
  外加頂層 `categories` 清單。沒登記但有 register.json 的資料夾，遊戲會自己補進去
  （分類取 `songlist.json`，沒有就 Other）。
- `songlist.json` 是舊的分類表（分類 → 資料夾清單），這裡一併維持同步。
- 每首歌一個資料夾：`register.json` 的資源路徑寫 `songs/<資料夾>/...`，沒有副檔名。

比遊戲內建和 Hiraeth 多做的：
- 刪除不是真的刪，移到曲庫旁的 `UserSongs_editor/trash/`。
- 每次寫入前把會動到的索引與 register.json 備份到 `UserSongs_editor/history/`，
  可以「復原上一步」。回收區和歷史放在曲庫**外面**：放裡面的話打包遊戲時
  會跟著整個 UserSongs 被複製進去。
- 每次改動寫一個 `.editor_revision.json`，遊戲看到就重整選曲清單。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .ui_text import tr

INDEX_FILE = 'library.json'
SONGLIST_FILE = 'songlist.json'
REVISION_FILE = '.editor_revision.json'
DEFAULT_CATEGORY = 'Other'
ALL_CATEGORY = 'ALL'
HISTORY_KEEP = 30
AUDIO_EXTS = ('.wav', '.ogg', '.mp3')
IMAGE_EXTS = ('.png', '.jpg', '.jpeg')
CHART_EXTS = ('.json', '.xml')


class LibraryError(Exception):
    """給使用者看的錯誤訊息。"""


# ── 小工具 ───────────────────────────────────────────────────────────

def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding='utf-8-sig') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path: str, data: Any) -> None:
    """先寫暫存檔再換掉，寫到一半當掉也不會留下壞掉的索引。"""
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def normalize_category(value: Optional[str]) -> str:
    text = (value or '').strip()
    if not text or text.upper() == ALL_CATEGORY:
        return DEFAULT_CATEGORY
    return text


def find_song_folders(root: str, limit: int = 3000) -> List[str]:
    """root 底下所有樂曲資料夾（含 register.json）。找到就不再往裡面找。"""
    root = os.path.abspath(root)
    found: List[str] = []
    if os.path.isfile(os.path.join(root, 'register.json')):
        return [root]
    for base, dirs, files in os.walk(root):
        if 'register.json' in files:
            found.append(base)
            dirs[:] = []                       # 樂曲資料夾裡面不會再有樂曲
            continue
        dirs[:] = [d for d in sorted(dirs) if not d.endswith('_editor')]
        if len(found) >= limit:
            break
    return sorted(found)


def find_packages(root: str, limit: int = 3000) -> List[str]:
    """root 底下所有 Hiraeth 歌曲包 ZIP。"""
    root = os.path.abspath(root)
    found: List[str] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in sorted(dirs) if not d.endswith('_editor')]
        found += [os.path.join(base, f) for f in sorted(files) if f.lower().endswith('.zip')]
        if len(found) >= limit:
            break
    return found


def _source_categories(root: str) -> Dict[str, List[str]]:
    """來源曲庫 library.json 裡的分類（資料夾名 → 分類）。"""
    data = _read_json(os.path.join(root, INDEX_FILE), {})
    out: Dict[str, List[str]] = {}
    for song in (data.get('songs') or []) if isinstance(data, dict) else []:
        if not isinstance(song, dict):
            continue
        name = str(song.get('folderName') or '')
        cats = song.get('categories') or ([song['category']] if song.get('category') else [])
        if name and cats:
            out[name] = [str(c) for c in cats]
    return out


def safe_folder_name(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', value or '')
    return ' '.join(text.split()).strip(' .') or 'song'


def resolve_resource(song_dir: str, resource: Optional[str], exts: Tuple[str, ...]) -> Optional[str]:
    """`songs/<資料夾>/<子資料夾>/<檔名>`（無副檔名）→ 實際檔案。

    手改壞的 register 只能讓**這一個資源**解析不出來，不能讓整份曲庫清單掛掉。
    以前 `songs/Alpha` 這種只有兩段的路徑會在 `split('/', 2)[2]` 丟
    IndexError，而這個函式是在 `songs()` 的迴圈裡被呼叫的 —— 一首壞掉，曲庫
    管理就整個開不出來，連那首都看不到、修不了。

    反斜線也一併接受：register 是使用者會手改的檔案，而 Windows 上抄路徑抄到
    `songs\Alpha\Real\Alpha` 是很正常的事。譜面那半邊
    （`song_folder.resolve_chart_file`）本來就吃得下，兩邊行為不該不一樣。
    """
    if not resource:
        return None
    rel = str(resource).replace(chr(92), '/')
    parts = [p for p in rel.split('/') if p and p not in ('.', '..')]
    # `songs/<樂曲資料夾>/…` 是遊戲端的寫法，前兩段要剝掉才是相對這個資料夾的
    # 路徑。段數不夠就當作解析不出來 —— 以前這裡是 split('/', 2)[2]，兩段的
    # 路徑會直接丟 IndexError。
    if parts and parts[0] == 'songs':
        parts = parts[2:]
    if not parts:
        return None
    folder = os.path.join(song_dir, *parts[:-1])
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if stem == parts[-1] and ext.lower() in exts:
            return os.path.join(folder, name)
    return None


# ── 資料 ─────────────────────────────────────────────────────────────

@dataclass
class Difficulty:
    index: int
    name: str
    level: Any
    chart: Optional[str]
    audio: Optional[str]
    piano: Optional[str]
    no_background: bool
    raw: Dict[str, Any]


@dataclass
class Song:
    folder: str                        # 資料夾名稱（在曲庫根目錄底下）
    path: str                          # 完整路徑
    id: str
    title: str
    author: str
    categories: List[str]
    slogan: str
    cover: Optional[str]
    difficulties: List[Difficulty]
    registered: bool                   # library.json 裡有沒有這一筆
    problems: List[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.categories[0] if self.categories else DEFAULT_CATEGORY

    def levels_text(self) -> str:
        return '  '.join('%s %s' % (d.name, d.level) for d in self.difficulties)


# ── 曲庫 ─────────────────────────────────────────────────────────────

class SongLibrary:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        if not os.path.isdir(self.root):
            raise LibraryError(tr('找不到曲庫資料夾：%s') % self.root)
        self.editor_dir = os.path.join(os.path.dirname(self.root),
                                       os.path.basename(self.root) + '_editor')

    # ── 讀 ───────────────────────────────────────────────────────────

    def _index(self) -> Dict[str, Any]:
        data = _read_json(os.path.join(self.root, INDEX_FILE), {})
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get('songs'), list):
            data['songs'] = []
        if not isinstance(data.get('categories'), list):
            data['categories'] = []
        return data

    def _songlist(self) -> Dict[str, Any]:
        data = _read_json(os.path.join(self.root, SONGLIST_FILE), {})
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get('categories'), dict):
            data['categories'] = {}
        return data

    def categories(self) -> List[str]:
        index = self._index()
        names = [DEFAULT_CATEGORY]
        sources = list(index['categories'])
        for entry in index['songs']:
            if isinstance(entry, dict):
                sources += list(entry.get('categories') or [entry.get('category')])
        sources += list(self._songlist()['categories'].keys())
        for name in sources:
            name = normalize_category(name)
            if name not in names:
                names.append(name)
        return names

    def songs(self, check: bool = True) -> List[Song]:
        index = self._index()
        entries = {str(e.get('folderName') or ''): e for e in index['songs'] if isinstance(e, dict)}
        category_map: Dict[str, List[str]] = {}
        for category, folders in self._songlist()['categories'].items():
            for folder in folders or []:
                category_map.setdefault(str(folder), []).append(normalize_category(category))
        result = []
        for name in sorted(os.listdir(self.root), key=str.lower):
            path = os.path.join(self.root, name)
            if not os.path.isfile(os.path.join(path, 'register.json')):
                continue
            entry = entries.get(name)
            if entry:
                cats = [normalize_category(c) for c in (entry.get('categories') or [entry.get('category')])]
            else:
                cats = category_map.get(name) or [DEFAULT_CATEGORY]
            result.append(self._load_song(name, entry, cats, check))
        return result

    def song(self, folder: str, check: bool = True) -> Song:
        for song in self.songs(check=False):
            if song.folder == folder:
                return self._load_song(folder, self._entry(folder), song.categories, check)
        raise LibraryError(tr('曲庫裡沒有「%s」') % folder)

    def _entry(self, folder: str) -> Optional[Dict[str, Any]]:
        for entry in self._index()['songs']:
            if isinstance(entry, dict) and entry.get('folderName') == folder:
                return entry
        return None

    def _load_song(self, folder: str, entry: Optional[Dict[str, Any]], cats: List[str],
                   check: bool) -> Song:
        path = os.path.join(self.root, folder)
        register = _read_json(os.path.join(path, 'register.json'), None)
        problems: List[str] = []
        if not isinstance(register, dict):
            register = {}
            problems.append(tr('register.json 讀不出來'))
        diffs = []
        for i, raw in enumerate(register.get('difficulties') or []):
            if not isinstance(raw, dict):
                continue
            d = Difficulty(
                index=i, name=str(raw.get('difficultyName') or ''),
                level=raw.get('difficultyLevel'),
                chart=resolve_resource(path, raw.get('chartFileName'), CHART_EXTS),
                audio=resolve_resource(path, raw.get('audioResourcePath'), AUDIO_EXTS),
                piano=resolve_resource(path, raw.get('pianoAudioResourcePath'), AUDIO_EXTS),
                no_background=bool(raw.get('noBackgroundMusic')), raw=raw)
            diffs.append(d)
            if check:
                if not d.chart:
                    problems.append(tr('%s：找不到譜面檔') % d.name)
                if not d.audio:
                    problems.append(tr('%s：找不到音訊檔') % d.name)
                if raw.get('pianoAudioResourcePath') and not d.piano:
                    problems.append(tr('%s：找不到鋼琴音軌') % d.name)
        cover = next((resolve_resource(path, raw.get('coverResourcePath'), IMAGE_EXTS)
                      for raw in register.get('difficulties') or []
                      if isinstance(raw, dict) and raw.get('coverResourcePath')), None)
        if check and not diffs:
            problems.append(tr('沒有任何難度'))
        return Song(folder=folder, path=path,
                    id=str((entry or {}).get('id') or 'portable:' + folder),
                    title=str(register.get('displayName') or folder),
                    author=str(register.get('author') or ''),
                    categories=cats or [DEFAULT_CATEGORY],
                    slogan=str(register.get('slogan') or ''),
                    cover=cover, difficulties=diffs, registered=entry is not None,
                    problems=problems)

    def deep_check(self, song: Song) -> List[str]:
        """比較花時間的檢查：打開譜面看鍵道、音高。"""
        problems = list(song.problems)
        for d in song.difficulties:
            if not d.chart or not d.chart.lower().endswith('.json'):
                continue
            data = _read_json(d.chart, None)
            notes = data.get('notes') if isinstance(data, dict) else None
            if not isinstance(notes, list) or not notes:
                problems.append(tr('%s：譜面沒有音符') % d.name)
                continue
            lanes = [n.get('endLane') for n in notes if isinstance(n, dict)]
            if any(isinstance(v, int) and v > 27 for v in lanes):
                problems.append(tr('%s：鍵道超過 27（舊的 1 開始寫法）') % d.name)
            pitched = sum(1 for n in notes if isinstance(n, dict) and n.get('pitch') not in (None, ''))
            if pitched < len(notes) * 0.9:
                problems.append(tr('%s：%d/%d 顆沒有音高（無背景音樂時會沒聲音）')
                                % (d.name, len(notes) - pitched, len(notes)))
        return problems

    # ── 寫：共用 ─────────────────────────────────────────────────────

    def _snapshot(self, label: str, folders: List[str]) -> str:
        """寫入前備份索引與相關 register.json，回傳這筆歷史的資料夾。"""
        stamp = time.strftime('%Y%m%d-%H%M%S') + '-%03d' % (int(time.time() * 1000) % 1000)
        target = os.path.join(self.editor_dir, 'history', stamp)
        os.makedirs(target, exist_ok=True)
        for name in (INDEX_FILE, SONGLIST_FILE):
            src = os.path.join(self.root, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(target, name))
        registers = {}
        for folder in folders:
            src = os.path.join(self.root, folder, 'register.json')
            if os.path.isfile(src):
                dst = os.path.join(target, 'registers', safe_folder_name(folder) + '.json')
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                registers[folder] = os.path.basename(dst)
        _write_json(os.path.join(target, 'meta.json'),
                    {'label': label, 'time': time.time(), 'registers': registers})
        self._trim_history()
        return target

    def _trim_history(self) -> None:
        folder = os.path.join(self.editor_dir, 'history')
        entries = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        for name in entries[:-HISTORY_KEEP]:
            shutil.rmtree(os.path.join(folder, name), ignore_errors=True)

    def history(self) -> List[Tuple[str, str]]:
        """[(資料夾, 說明)]，新的在前。"""
        folder = os.path.join(self.editor_dir, 'history')
        out = []
        for name in sorted(os.listdir(folder), reverse=True) if os.path.isdir(folder) else []:
            meta = _read_json(os.path.join(folder, name, 'meta.json'), {})
            out.append((os.path.join(folder, name), str(meta.get('label') or name)))
        return out

    def undo_last(self) -> str:
        """把最近一筆歷史還原回去，回傳它的說明。刪除的歌也會從回收區搬回來。"""
        entries = self.history()
        if not entries:
            raise LibraryError(tr('沒有可以復原的動作'))
        folder, label = entries[0]
        meta = _read_json(os.path.join(folder, 'meta.json'), {})
        restored = meta.get('restore_folder')
        if restored and os.path.isdir(restored.get('trash', '')):
            dest = os.path.join(self.root, restored['folder'])
            if os.path.exists(dest):
                raise LibraryError(tr('曲庫裡已經有「%s」，無法搬回') % restored['folder'])
            shutil.move(restored['trash'], dest)
        removed = meta.get('remove_folder')
        # 批量匯入／更新曲庫一次加好幾首，記的是一份清單
        for name in ([removed] if isinstance(removed, str) else list(removed or [])):
            path = os.path.join(self.root, name)
            if os.path.isdir(path):
                self._move_to_trash(name)
        for name in (INDEX_FILE, SONGLIST_FILE):
            src = os.path.join(folder, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(self.root, name))
        for song_folder, stored in (meta.get('registers') or {}).items():
            src = os.path.join(folder, 'registers', stored)
            dst = os.path.join(self.root, song_folder, 'register.json')
            if os.path.isfile(src) and os.path.isdir(os.path.dirname(dst)):
                shutil.copy2(src, dst)
        shutil.rmtree(folder, ignore_errors=True)
        self.notify_game(restored['folder'] if restored else '')
        return label

    def _update_meta(self, snapshot: str, **values: Any) -> None:
        path = os.path.join(snapshot, 'meta.json')
        meta = _read_json(path, {})
        meta.update(values)
        _write_json(path, meta)

    def notify_game(self, folder: str = '') -> None:
        """留一個「曲庫改過了」的記號，遊戲在選曲畫面看到就重整並停在這首。"""
        path = os.path.join(self.root, REVISION_FILE)
        current = _read_json(path, {})
        revision = int(current.get('revision', 0)) + 1 if isinstance(current, dict) else 1
        entry = self._entry(folder) if folder else None
        # 遊戲內「新增曲目」匯入的歌 id 是亂數，不是 portable:<資料夾>，要用登記的那個
        song_id = str(entry.get('id')) if entry and entry.get('id') else (
            'portable:' + folder if folder else '')
        _write_json(path, {'revision': revision, 'folder': folder, 'id': song_id,
                           'time': time.time()})

    def _write_register(self, folder: str, register: Dict[str, Any]) -> None:
        _write_json(os.path.join(self.root, folder, 'register.json'), register)

    def _read_register(self, folder: str) -> Dict[str, Any]:
        data = _read_json(os.path.join(self.root, folder, 'register.json'), None)
        if not isinstance(data, dict):
            raise LibraryError(tr('「%s」的 register.json 讀不出來') % folder)
        return data

    def _set_index_entry(self, folder: str, categories: Optional[List[str]], remove: bool = False) -> None:
        index = self._index()
        songs = [e for e in index['songs'] if isinstance(e, dict)]
        entry = next((e for e in songs if e.get('folderName') == folder), None)
        if remove:
            songs = [e for e in songs if e is not entry]
        else:
            if entry is None:
                entry = {'id': 'portable:' + folder, 'folderName': folder}
                songs.append(entry)
            cats = [normalize_category(c) for c in (categories or [DEFAULT_CATEGORY])]
            cats = list(dict.fromkeys(cats))
            entry['category'] = cats[0]
            entry['categories'] = cats
            for c in cats:
                if c not in index['categories']:
                    index['categories'].append(c)
        index['songs'] = songs
        _write_json(os.path.join(self.root, INDEX_FILE), index)

        songlist = self._songlist()
        mapping = songlist['categories']
        for name in list(mapping.keys()):
            mapping[name] = [f for f in (mapping[name] or []) if f != folder]
        if not remove:
            for c in cats:
                mapping.setdefault(c, []).append(folder)
        songlist['categories'] = mapping
        _write_json(os.path.join(self.root, SONGLIST_FILE), songlist)

    def _move_to_trash(self, folder: str) -> str:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        dest = os.path.join(self.editor_dir, 'trash', '%s_%s' % (stamp, safe_folder_name(folder)))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(os.path.join(self.root, folder), dest)
        return dest

    # ── 寫：曲目 ─────────────────────────────────────────────────────

    def update_song(self, folder: str, title: str, author: str, categories: List[str],
                    slogan: str = '') -> None:
        title, author = (title or '').strip(), (author or '').strip()
        if not title:
            raise LibraryError(tr('曲名不能空白'))
        register = self._read_register(folder)
        self._snapshot(tr('修改「%s」的曲目資料') % title, [folder])
        register['displayName'] = title
        register['author'] = author
        if slogan.strip():
            register['slogan'] = slogan.strip()
        else:
            register.pop('slogan', None)
        self._write_register(folder, register)
        self._set_index_entry(folder, categories)
        self.notify_game(folder)

    def update_difficulty(self, folder: str, index: int, name: str, level: Any,
                          no_background: Optional[bool] = None) -> None:
        register = self._read_register(folder)
        diffs = register.get('difficulties') or []
        if not 0 <= index < len(diffs):
            raise LibraryError(tr('難度不存在'))
        name = (name or '').strip()
        if not name:
            raise LibraryError(tr('難度名稱不能空白'))
        if any(i != index and str(d.get('difficultyName')) == name for i, d in enumerate(diffs)):
            raise LibraryError(tr('已經有叫「%s」的難度') % name)
        try:
            level_value = float(level)
        except (TypeError, ValueError):
            raise LibraryError(tr('等級要是數字'))
        if not 0 < level_value <= 99:
            raise LibraryError(tr('等級要在 1～99'))
        self._snapshot(tr('修改「%s」的難度 %s') % (register.get('displayName') or folder, name), [folder])
        entry = diffs[index]
        entry['difficultyName'] = name
        entry['difficultyLevel'] = int(level_value) if level_value == int(level_value) else level_value
        if no_background is not None:
            if no_background:
                entry['noBackgroundMusic'] = True
            else:
                entry.pop('noBackgroundMusic', None)
        self._write_register(folder, register)
        self.notify_game(folder)

    def move_difficulty(self, folder: str, index: int, step: int) -> int:
        register = self._read_register(folder)
        diffs = register.get('difficulties') or []
        target = index + step
        if not (0 <= index < len(diffs) and 0 <= target < len(diffs)):
            return index
        self._snapshot(tr('調整「%s」的難度順序') % (register.get('displayName') or folder), [folder])
        diffs[index], diffs[target] = diffs[target], diffs[index]
        self._write_register(folder, register)
        self.notify_game(folder)
        return target

    def delete_difficulty(self, folder: str, index: int) -> None:
        """只從 register.json 拿掉，譜面檔留著（要真的清檔案刪整首或自己清）。"""
        register = self._read_register(folder)
        diffs = register.get('difficulties') or []
        if not 0 <= index < len(diffs):
            raise LibraryError(tr('難度不存在'))
        if len(diffs) == 1:
            raise LibraryError(tr('至少要留一個難度；要整首拿掉請用「刪除曲目」'))
        name = diffs[index].get('difficultyName')
        self._snapshot(tr('刪除「%s」的難度 %s') % (register.get('displayName') or folder, name), [folder])
        del diffs[index]
        self._write_register(folder, register)
        self.notify_game(folder)

    def delete_song(self, folder: str) -> str:
        """整首移到回收區（可以「復原上一步」搬回來），回傳回收區裡的位置。"""
        if not os.path.isdir(os.path.join(self.root, folder)):
            raise LibraryError(tr('曲庫裡沒有「%s」') % folder)
        register = _read_json(os.path.join(self.root, folder, 'register.json'), {})
        title = register.get('displayName') if isinstance(register, dict) else None
        snapshot = self._snapshot(tr('刪除「%s」') % (title or folder), [folder])
        trash = self._move_to_trash(folder)
        self._update_meta(snapshot, restore_folder={'folder': folder, 'trash': trash})
        self._set_index_entry(folder, None, remove=True)
        self.notify_game('')
        return trash

    def add_category(self, name: str) -> None:
        name = (name or '').strip()
        if not name or name.upper() == ALL_CATEGORY or len(name) > 40 or \
                re.search(r'[\\/:*?"<>|]', name):
            raise LibraryError(tr('分類名稱不合格（不能空白、不能叫 ALL、最多 40 字、不能有 \\/:*?"<>|）'))
        index = self._index()
        if name in index['categories'] or name == DEFAULT_CATEGORY:
            raise LibraryError(tr('已經有這個分類'))
        self._snapshot(tr('新增分類「%s」') % name, [])
        index['categories'].append(name)
        _write_json(os.path.join(self.root, INDEX_FILE), index)
        self.notify_game('')

    # ── 匯入 ─────────────────────────────────────────────────────────

    def _unique_folder(self, wanted: str) -> str:
        base = safe_folder_name(wanted)
        name, n = base, 2
        while os.path.exists(os.path.join(self.root, name)):
            name = '%s (%d)' % (base, n)
            n += 1
        return name

    def import_many(self, items: List[Tuple[str, str, str]],
                    progress: Optional[Any] = None) -> Tuple[List[str], List[str]]:
        """一次匯入很多首：`items` 是 [(種類, 路徑, 分類)]，種類是 'folder' 或 'zip'。

        回傳 (成功的資料夾名, 失敗的說明)。整批算一筆歷史，「復原上一步」會把
        這次加進來的全部收回回收區。
        """
        done: List[str] = []
        failed: List[str] = []
        for i, (kind, path, category) in enumerate(items):
            if progress is not None and progress(i, len(items), os.path.basename(path)) is False:
                break
            try:
                if kind == 'zip':
                    done.append(self.import_hiraeth_zip(path, category))
                else:
                    done.append(self.import_folder(path, category))
            except Exception as exc:                    # noqa: BLE001
                failed.append('%s：%s' % (os.path.basename(path), exc))
        if progress is not None:
            progress(len(items), len(items), '')
        if len(done) > 1:
            # 每首各自留了一筆歷史，合併成一筆，「復原上一步」才收得乾淨
            self._collapse_history(len(done), tr('批量匯入 %d 首') % len(done), done)
        return done, failed

    def _collapse_history(self, count: int, label: str, folders: List[str]) -> None:
        entries = self.history()[:count]
        if len(entries) < 2:
            return
        oldest = entries[-1][0]                  # 最舊的那筆才有「動之前」的索引
        self._update_meta(oldest, label=label, remove_folder=list(folders))
        for folder, _label in entries[:-1]:
            shutil.rmtree(folder, ignore_errors=True)

    def merge_from(self, source: str, progress: Optional[Any] = None) -> Tuple[List[str], List[str]]:
        """用另一份曲庫（資料夾）更新這一份：同名的整首換掉，其他的原封不動。

        分類沿用曲庫裡原本的設定（新曲目才讀來源的 library.json）。回傳
        (更新或新增的資料夾, 失敗說明)。
        """
        source = os.path.abspath(source)
        folders = find_song_folders(source)
        if not folders:
            raise LibraryError(tr('這個資料夾裡找不到任何樂曲（沒有 register.json）'))
        source_cats = _source_categories(source)
        existing = {s.folder: s.categories for s in self.songs()}
        snapshot = self._snapshot(tr('更新曲庫（%d 首）') % len(folders),
                                  [os.path.basename(f) for f in folders])
        done: List[str] = []
        failed: List[str] = []
        added: List[str] = []
        for i, path in enumerate(folders):
            name = os.path.basename(path)
            if progress is not None and progress(i, len(folders), name) is False:
                break
            dest = os.path.join(self.root, name)
            try:
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                else:
                    added.append(name)
                shutil.copytree(path, dest)
                cats = existing.get(name) or source_cats.get(name) or [DEFAULT_CATEGORY]
                self._set_index_entry(name, cats)
                done.append(name)
            except OSError as exc:
                failed.append('%s：%s' % (name, exc))
        if progress is not None:
            progress(len(folders), len(folders), '')
        # 復原時只把這次「新增」的收回回收區；原本就有、被換掉的留著（檔案已覆蓋）
        self._update_meta(snapshot, remove_folder=added)
        self.notify_game(done[-1] if done else '')
        return done, failed

    def import_folder(self, source: str, category: str = DEFAULT_CATEGORY) -> str:
        """複製一個已經是 nos-clone 格式的樂曲資料夾（有 register.json）進來。"""
        source = os.path.abspath(source)
        if not os.path.isfile(os.path.join(source, 'register.json')):
            raise LibraryError(tr('這個資料夾沒有 register.json，不是樂曲資料夾'))
        if os.path.dirname(source) == self.root:
            raise LibraryError(tr('這首已經在曲庫裡了'))
        old_name = os.path.basename(source)
        folder = self._unique_folder(old_name)
        snapshot = self._snapshot(tr('匯入「%s」') % folder, [])
        shutil.copytree(source, os.path.join(self.root, folder))
        if folder != old_name:
            # register 裡的路徑是 songs/<資料夾>/...，換了名字要跟著改
            register = self._read_register(folder)
            prefix_old, prefix_new = 'songs/%s/' % old_name, 'songs/%s/' % folder
            for diff in register.get('difficulties') or []:
                for key, value in list(diff.items()):
                    if isinstance(value, str) and value.startswith(prefix_old):
                        diff[key] = prefix_new + value[len(prefix_old):]
            self._write_register(folder, register)
        self._update_meta(snapshot, remove_folder=folder)
        self._set_index_entry(folder, [category])
        self.notify_game(folder)
        return folder

    def import_hiraeth_zip(self, zip_path: str, category: str = DEFAULT_CATEGORY) -> str:
        """Hiraeth 歌曲包 ZIP → nos-clone 樂曲資料夾（XML 譜面轉成 JSON）。"""
        import contextlib
        import io
        import tempfile
        from .models import NoteModel

        slots = (('normal', 'Normal'), ('hard', 'Hard'), ('expert', 'Expert'), ('real', 'Real'))
        try:
            archive = zipfile.ZipFile(zip_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise LibraryError(tr('ZIP 打不開：%s') % exc)
        with archive:
            names = set(archive.namelist())
            if 'song.json' not in names:
                raise LibraryError(tr('ZIP 裡沒有 song.json，不是 Hiraeth 歌曲包'))
            spec = json.loads(archive.read('song.json').decode('utf-8-sig'))
            charts = spec.get('charts') or {}
            if not isinstance(charts, dict) or not any(s in charts for s, _n in slots):
                raise LibraryError(tr('song.json 的 charts 沒有可用的難度'))
            audio = next((n for n in ('music.wav', 'music.ogg') if n in names), None)
            if not audio:
                raise LibraryError(tr('ZIP 裡沒有 music.wav'))
            title = str(spec.get('title') or os.path.splitext(os.path.basename(zip_path))[0])
            folder = self._unique_folder(title)
            stem = folder
            snapshot = self._snapshot(tr('匯入「%s」') % title, [])
            song_dir = os.path.join(self.root, folder)
            os.makedirs(song_dir)
            try:
                with open(os.path.join(song_dir, stem + os.path.splitext(audio)[1]), 'wb') as fh:
                    fh.write(archive.read(audio))
                if 'cover.png' in names:
                    with open(os.path.join(song_dir, stem + '.png'), 'wb') as fh:
                        fh.write(archive.read('cover.png'))
                diffs = []
                for slot, name in slots:
                    if slot not in charts or slot + '.xml' not in names:
                        continue
                    os.makedirs(os.path.join(song_dir, name), exist_ok=True)
                    temp_dir = tempfile.mkdtemp()
                    temp = os.path.join(temp_dir, slot + '.xml')
                    with open(temp, 'wb') as fh:
                        fh.write(archive.read(slot + '.xml'))
                    model = NoteModel()
                    with contextlib.redirect_stdout(io.StringIO()):
                        model.load_xml(temp)
                        model.save_json(os.path.join(song_dir, name, stem + '.json'))
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    level = charts[slot]
                    if slot == 'real':
                        # REAL 顯示等級（1～3.5）→ 遊戲難度等級（輸出 ZIP 時換算的反向）
                        level = int(round(10 + (float(level) - 1) * 2))
                    diff = {'difficultyName': name, 'difficultyLevel': level,
                            'chartFileName': 'songs/%s/%s/%s' % (folder, name, stem),
                            'audioResourcePath': 'songs/%s/%s' % (folder, stem)}
                    if 'cover.png' in names:
                        diff['coverResourcePath'] = 'songs/%s/%s' % (folder, stem)
                    diffs.append(diff)
                register = {'displayName': title, 'author': str(spec.get('artist') or ''),
                            'difficulties': diffs}
                if spec.get('slogan'):
                    register['slogan'] = str(spec['slogan'])
                self._write_register(folder, register)
            except Exception:
                shutil.rmtree(song_dir, ignore_errors=True)
                raise
        self._update_meta(snapshot, remove_folder=folder)
        self._set_index_entry(folder, [category])
        self.notify_game(folder)
        return folder


# ── 曲庫位置 ──────────────────────────────────────────────────────────
#
# 遊戲、曲庫、製譜器、啟動器是四個分開的東西，各放各的，啟動器記住三個位置去連結：
#   D:/Games/Nos-clonev2.0/                ← 遊戲本體（Nos-clonev1.exe、_Data）
#   E:/NosSongs/UserSongs/                 ← 曲庫（旁邊的 UserSongs_editor 是回收區與歷史）
#   D:/Tools/Nos Chart Maker 8.x.exe       ← 製譜器
#   D:/Tools/NosMania.exe                  ← 啟動器
# 偏好設定裡指定的位置優先。沒指定（或那個位置已經不在）才去找啟動器 exe 所在的
# 資料夾和它下一層 —— 四個全丟在同一個資料夾也認得出來。
#
# 遊戲本身只讀 exe 旁邊的 UserSongs，所以進遊戲前在遊戲資料夾放一個目錄連結
# （junction）指到曲庫，見 ensure_game_library_link。

EDITOR_EXE_PATTERN = re.compile(r'chart\s*maker|charteditor', re.IGNORECASE)


def app_dir() -> str:
    """目前這支 exe 所在的資料夾。

    打包成單一 exe 時 `__file__` 在暫存解壓目錄（_MEIxxxx）裡，不能拿來找旁邊的
    遊戲，要用 `sys.executable`。沒打包（開發中）就回傳空字串。
    """
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ''


def _nearby_exes(base: str) -> List[str]:
    """base 本身和下一層資料夾裡的 exe（自己除外）。"""
    import sys
    if not base or not os.path.isdir(base):
        return []
    me = os.path.normcase(os.path.abspath(sys.executable))
    dirs = [base] + sorted(os.path.join(base, d) for d in os.listdir(base)
                           if os.path.isdir(os.path.join(base, d)))
    found = []
    for folder in dirs:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            full = os.path.join(folder, name)
            if name.lower().endswith('.exe') and os.path.isfile(full) and \
                    os.path.normcase(os.path.abspath(full)) != me:
                found.append(full)
    return found


def _newest(paths: List[str]) -> str:
    return max(paths, key=lambda p: os.path.getmtime(p)) if paths else ''


def find_game_exe(base: str) -> str:
    """遊戲 exe：旁邊有同名 `_Data` 資料夾的那個（有好幾份 build 取最新的）。"""
    return _newest([p for p in _nearby_exes(base)
                    if os.path.isdir(p[:-4] + '_Data')])


def find_editor_exe(base: str) -> str:
    """製譜器 exe：檔名有 Chart Maker／ChartEditor（有好幾版取最新的）。"""
    return _newest([p for p in _nearby_exes(base)
                    if EDITOR_EXE_PATTERN.search(os.path.basename(p))])


def bundled_game_exe() -> str:
    return find_game_exe(app_dir())


def bundled_editor_exe() -> str:
    return find_editor_exe(app_dir())


def bundled_root() -> str:
    """跟著 exe 一起放的曲庫：同一層的 UserSongs、下一層的 UserSongs，最後才是遊戲旁邊那份。"""
    base = app_dir()
    if not base:
        return ''
    return find_library_folder(base) or _existing_dir(
        os.path.join(os.path.dirname(bundled_game_exe()), 'UserSongs') if bundled_game_exe() else '')


def _existing_dir(path: str) -> str:
    return path if path and os.path.isdir(path) else ''


def find_library_folder(base: str) -> str:
    """base 裡的 UserSongs，或下一層資料夾裡的 UserSongs（遊戲資料夾裡那份不算）。"""
    if not base or not os.path.isdir(base):
        return ''
    direct = os.path.join(base, 'UserSongs')
    if os.path.isdir(direct):
        return direct
    try:
        children = sorted(os.listdir(base))
    except OSError:
        return ''
    for name in children:
        folder = os.path.join(base, name)
        candidate = os.path.join(folder, 'UserSongs')
        if not os.path.isdir(candidate) or is_junction(candidate):
            continue
        if find_game_exe_in(folder):
            continue
        return candidate
    return ''


def find_game_exe_in(folder: str) -> str:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ''
    for name in names:
        if name.lower().endswith('.exe') and os.path.isdir(os.path.join(folder, name[:-4] + '_Data')):
            return os.path.join(folder, name)
    return ''


def _saved_path(key: str, want_dir: bool) -> str:
    try:
        from .settings import settings
        saved = settings.get(key, '') or ''
    except Exception:                                   # noqa: BLE001
        return ''
    if not saved:
        return ''
    ok = os.path.isdir(saved) if want_dir else os.path.isfile(saved)
    return saved if ok else ''


def default_root() -> str:
    """要管哪一份曲庫：偏好設定指定的優先，否則跟著 exe 放的。"""
    return _saved_path('song_library_root', True) or bundled_root()


def configured_game_exe() -> str:
    """遊戲 exe：偏好設定指定的優先，否則啟動器旁邊（同一層或下一層）找到的。"""
    return _saved_path('game_exe_path', False) or bundled_game_exe()


def configured_editor_exe() -> str:
    """製譜器 exe：偏好設定指定的優先，否則啟動器旁邊找到的。"""
    return _saved_path('editor_exe_path', False) or bundled_editor_exe()


# ── 遊戲 ↔ 曲庫的連結 ─────────────────────────────────────────────────

def is_junction(path: str) -> bool:
    """目錄連結（junction）或符號連結。"""
    import stat
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    tag = getattr(st, 'st_reparse_tag', 0)
    return bool(tag) and tag == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003)


def link_target(path: str) -> str:
    try:
        target = os.readlink(path)
    except (OSError, ValueError):
        return ''
    if target.startswith('\\\\?\\'):
        target = target[4:]
    return os.path.normcase(os.path.abspath(target))


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def make_junction(target: str, link: str) -> None:
    """建目錄連結：不複製檔案、不需要系統管理員。

    Windows 用 junction（`mklink /J`），其他平台用 symlink。名字沿用舊的，
    呼叫的地方不用改。
    """
    from .platform_support import IS_WINDOWS, make_dir_link
    if not IS_WINDOWS:
        if not make_dir_link(link, target) or not os.path.isdir(link):
            raise OSError(tr('建立資料夾連結失敗：%s') % link)
        return
    import subprocess
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', link, target],
                            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0 or not os.path.isdir(link):
        # mbcs 只有 Windows 有，所以這一行只能待在這個分支裡
        raise OSError(tr('建立資料夾連結失敗：%s') % (
            (result.stderr or result.stdout or b'').decode('mbcs', 'replace').strip() or link))


def _only_index_files(folder: str) -> bool:
    """遊戲自己建的空曲庫（只有索引檔、沒有任何曲目）。"""
    try:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isdir(full):
                return False
    except OSError:
        return False
    return True


LAUNCHER_FILE = 'nosmania_launcher.json'
_READS_LIBRARY_CACHE: Dict[Tuple[str, float], bool] = {}


def game_reads_launcher_library(game_dir: str) -> bool:
    """這份遊戲 build 會不會讀 nosmania_launcher.json 裡的 library_root。

    2026-09-17 之後的 build 才會。看編出來的程式裡有沒有 `library_root` 這個字串：
    Mono build 在 `*_Data/Managed/Nostalgia.Runtime.dll`（UTF-16），
    IL2CPP build 在 `*_Data/il2cpp_data/Metadata/global-metadata.dat`（UTF-8）。
    """
    exe = find_game_exe_in(game_dir)
    if not exe:
        return False
    data_dir = exe[:-4] + '_Data'
    probes = ((os.path.join(data_dir, 'Managed', 'Nostalgia.Runtime.dll'), 'library_root'.encode('utf-16-le')),
              (os.path.join(data_dir, 'il2cpp_data', 'Metadata', 'global-metadata.dat'), b'library_root'))
    for path, needle in probes:
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            continue
        if key not in _READS_LIBRARY_CACHE:
            try:
                with open(path, 'rb') as fh:
                    _READS_LIBRARY_CACHE[key] = needle in fh.read()
            except OSError:
                _READS_LIBRARY_CACHE[key] = False
        if _READS_LIBRARY_CACHE[key]:
            return True
    return False


def write_launcher_library_root(game_dir: str, library_root: str) -> None:
    """把曲庫位置併進遊戲資料夾的 nosmania_launcher.json（語言那些鍵保留）。"""
    path = os.path.join(game_dir, LAUNCHER_FILE)
    data: Dict[str, Any] = {}
    try:
        with open(path, encoding='utf-8') as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    root = os.path.abspath(library_root)
    if data.get('library_root') == root:
        return
    data['library_root'] = root
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def ensure_game_library_link(game_dir: str, library_root: str) -> str:
    """讓遊戲用 library_root 這份曲庫。回傳做了什麼：

    - ``'launcher_file'`` 新 build：寫進 nosmania_launcher.json 就好，不需要連結
    - ``'unlinked'``    新 build：寫進設定檔，並拿掉以前建的連結

    - ``'ok'``          已經指對了（或曲庫本來就放在遊戲資料夾裡）
    - ``'linked'``      原本沒有，建了連結
    - ``'relinked'``    原本連到別的曲庫，改連過來（只拿掉連結，不動任何曲目）
    - ``'replaced_empty'`` 遊戲直接開過、自己建了一個空曲庫：改名留著，換成連結
    - ``'real_folder'`` 遊戲資料夾裡有一份真的曲庫（有曲目），不動它
    """
    link = os.path.join(game_dir, 'UserSongs')
    if not library_root or not os.path.isdir(library_root):
        raise OSError(tr('找不到曲庫資料夾：%s') % library_root)
    if _same_dir(link, library_root):
        return 'ok'
    if game_reads_launcher_library(game_dir):
        # 新 build 自己讀設定檔裡的曲庫位置：不需要連結，之前建的也拿掉（只拿連結本身）
        write_launcher_library_root(game_dir, library_root)
        if is_junction(link):
            os.rmdir(link)
            return 'unlinked'
        return 'launcher_file'
    if is_junction(link):
        if link_target(link) == os.path.normcase(os.path.abspath(library_root)) or \
                (os.path.isdir(link) and _same_dir(os.path.realpath(link), os.path.realpath(library_root))):
            return 'ok'
        os.rmdir(link)                  # 只拿掉連結本身
        make_junction(library_root, link)
        return 'relinked'
    if os.path.isdir(link):
        if not _only_index_files(link):
            return 'real_folder'
        backup = link + '.empty-' + time.strftime('%Y%m%d-%H%M%S')
        os.rename(link, backup)
        make_junction(library_root, link)
        return 'replaced_empty'
    if os.path.exists(link):
        raise OSError(tr('遊戲資料夾裡有一個叫 UserSongs 的檔案，無法建立連結'))
    make_junction(library_root, link)
    return 'linked'


def is_library_package(path: str) -> bool:
    """這個 ZIP 是不是一份曲庫（兩層內有 library.json，而且不是遊戲包）。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    if _names_are_game(names):
        return False
    return any(n.count('/') <= 1 and n.rsplit('/', 1)[-1] == INDEX_FILE for n in names)


def find_library_packages(base: str = '', limit: int = 20) -> List[str]:
    """啟動器旁邊（和下一層）放著、還沒解壓的曲庫 ZIP。"""
    base = base or app_dir()
    if not base or not os.path.isdir(base):
        return []
    found: List[str] = []
    for folder in [base] + sorted(os.path.join(base, d) for d in os.listdir(base)
                                  if os.path.isdir(os.path.join(base, d)) and not is_junction(os.path.join(base, d))):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name.lower().endswith('.zip') and os.path.isfile(path) and is_library_package(path):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def is_game_package(path: str) -> bool:
    """這個 ZIP 裡面是不是一份遊戲 build（有 exe 和同名的 `_Data` 資料夾）。

    只讀 ZIP 的目錄表，不解壓，所以 500MB 的包也是一瞬間。
    """
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    return _names_are_game(names)


def _names_are_game(names: List[str]) -> bool:
    folders = {n.split('/')[-2] for n in names if n.endswith('/') and n.count('/') >= 1}
    folders |= {part for n in names for part in n.split('/')[:-1]}
    stems = {os.path.basename(n)[:-4] for n in names if n.lower().endswith('.exe')}
    return any(stem + '_Data' in folders for stem in stems)


def find_game_packages(base: str = '', limit: int = 20) -> List[str]:
    """NosMania 旁邊（和下一層）放著的遊戲包 ZIP。

    使用者常常是「NosMania.exe ＋ 一包遊戲 zip」丟在同一個資料夾，還沒解壓。
    找得到就直接請他裝，不要只說「找不到遊戲」。
    """
    base = base or app_dir()
    if not base or not os.path.isdir(base):
        return []
    found: List[str] = []
    for folder in [base] + sorted(os.path.join(base, d) for d in os.listdir(base)
                                  if os.path.isdir(os.path.join(base, d))):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name.lower().endswith('.zip') and os.path.isfile(path) and is_game_package(path):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found
