"""音源（SoundFont）的挑選：內建的、使用者自己丟進來的、或指定任何一個檔案。

以前音源是寫死的：打包之後那份 SF2 躺在 exe 裡面（`_MEIPASS`），使用者看不到
也換不掉。這裡把它攤開來：

  * **資料夾**：執行檔旁邊的 `soundfonts/`（macOS 是
    `~/Library/Application Support/NostalgiaChartEditor/soundfonts/`，因為
    `.app` 裡面不能寫）。丟 .sf2 進去，偏好設定的清單就會出現它。
  * **瀏覽**：也可以直接指定電腦上任何一個 .sf2。
  * **內建**：什麼都沒設就是原本那一份，行為和以前一樣。

preset（音色）也可以選。SF2 一個檔案裡常常有好幾個音色，Nice-Steinway 的
鋼琴是 preset 1 而不是 0，所以不讓人選的話換了音源很容易變成別的樂器。

讀 preset 清單是自己走 RIFF 區塊、只讀 phdr 那一段——音源動輒一兩百 MB，
不能為了列個下拉選單就整份讀進記憶體。
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .platform_support import IS_MAC, user_data_dir

#: 內建音源（檔名, preset）。順序＝找不到前一個就退到下一個。
BUILTIN_SOUNDFONTS = (
    ("Nice-Steinway-v3.8.sf2", 1),
    ("UprightPianoKW-20220221.sf2", 0),
)

#: 內建音源會被放在這幾個資料夾裡（打包時 spec 就是照這個放的）
BUILTIN_FOLDERS = ("soundfonts", "UprightPianoKW-SF2-20220221")

SETTING_PATH = 'soundfont_path'
SETTING_PRESET = 'soundfont_preset'

#: 使用者資料夾裡放的說明檔
README_NAME = '把音源放這裡.txt'
README_TEXT = """把 .sf2 音源檔放進這個資料夾，製譜器的
「偏好設定 → 音源」清單裡就會出現它。

選好之後記得也挑 preset（音色）：一個 SF2 裡常常有好幾個音色，
鋼琴不一定是第 0 個。

這裡放的檔案不會被更新覆蓋掉。
"""


@dataclass(frozen=True)
class SoundFontChoice:
    """清單上的一個選項。"""
    path: Path
    label: str
    builtin: bool = False
    preset: int = 0

    @property
    def key(self) -> str:
        """存進設定檔的值。內建是空字串（跟著版本走，不寫死路徑）。"""
        return '' if self.builtin else str(self.path)


def _runtime_root() -> Path:
    """打包後是 _MEIPASS（解開的資料夾），沒打包是專案根目錄。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def builtin_soundfont() -> Optional[Tuple[Path, int]]:
    """跟著程式一起帶的那一份。沒帶就回 None。"""
    root = _runtime_root()
    for name, preset in BUILTIN_SOUNDFONTS:
        for folder in BUILTIN_FOLDERS:
            candidate = root / folder / name
            if candidate.is_file():
                return (candidate, preset)
    return None


def user_dir() -> Path:
    """使用者自己丟音源的資料夾（不保證存在，用 `ensure_user_dir`）。"""
    if IS_MAC and getattr(sys, "frozen", False):
        # .app 裡面不能寫，而且使用者也找不到那個位置
        return Path(user_data_dir()) / 'soundfonts'
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / 'soundfonts'
    return Path(__file__).resolve().parents[1] / 'soundfonts'


def ensure_user_dir() -> Path:
    """建好資料夾並放一張說明。失敗（唯讀磁碟）也不丟例外。"""
    folder = user_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        readme = folder / README_NAME
        if not readme.exists():
            readme.write_text(README_TEXT, encoding='utf-8')
    except OSError:
        pass
    return folder


def _settings():
    from .settings import settings
    return settings


def user_soundfonts() -> List[Path]:
    """使用者資料夾裡的 .sf2（照檔名排序）。"""
    folder = user_dir()
    try:
        return sorted((p for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in ('.sf2', '.sf3')),
                      key=lambda p: p.name.lower())
    except OSError:
        return []


def available_soundfonts() -> List[SoundFontChoice]:
    """清單：內建、使用者資料夾裡的、以及目前設定的那一個（如果在別的地方）。"""
    out: List[SoundFontChoice] = []
    builtin = builtin_soundfont()
    if builtin is not None:
        path, preset = builtin
        out.append(SoundFontChoice(path, '內建：%s' % path.name, True, preset))
    seen = {str(c.path).lower() for c in out}
    for path in user_soundfonts():
        if str(path).lower() not in seen:
            out.append(SoundFontChoice(path, path.name))
            seen.add(str(path).lower())
    chosen = str(_settings().get(SETTING_PATH, '') or '')
    if chosen and chosen.lower() not in seen and Path(chosen).is_file():
        out.append(SoundFontChoice(Path(chosen), '%s（%s）' % (
            Path(chosen).name, Path(chosen).parent)))
    return out


def selected_soundfont() -> Tuple[Optional[Path], int]:
    """現在該用哪一份音源、哪一個 preset。

    設定裡指定的檔案不見了（換電腦、外接硬碟沒插）就退回內建，不要讓試聽
    整個壞掉——那會是一個「昨天還好好的」的無聲。
    """
    prefs = _settings()
    chosen = str(prefs.get(SETTING_PATH, '') or '')
    preset = int(prefs.get(SETTING_PRESET, -1))
    if chosen:
        path = Path(chosen)
        if path.is_file():
            return (path, preset if preset >= 0 else default_preset_for(path))
    builtin = builtin_soundfont()
    if builtin is None:
        return (None, max(0, preset))
    path, builtin_preset = builtin
    return (path, preset if preset >= 0 else builtin_preset)


def default_preset_for(path: Path) -> int:
    """沒指定 preset 時用哪一個：bank 0 裡編號最小的，通常就是主音色。"""
    for name, preset in BUILTIN_SOUNDFONTS:
        if Path(path).name == name:
            return preset
    presets = list_presets(path)
    bank0 = [p for p in presets if p[2] == 0]
    if bank0:
        return min(p[1] for p in bank0)
    return min((p[1] for p in presets), default=0)


# ── SF2 裡有哪些音色 ──────────────────────────────────────────────────

def _chunks(fh, start: int, end: int):
    """走訪 RIFF 區塊，只讀表頭（8 bytes），不把內容讀進來。"""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        chunk_id = header[:4].decode('ascii', 'replace')
        size = struct.unpack('<I', header[4:8])[0]
        yield chunk_id, pos + 8, size
        pos += 8 + size + (size & 1)


def list_presets(path) -> List[Tuple[str, int, int]]:
    """(名稱, preset 編號, bank)。讀不出來就回空清單，不丟例外。

    只 seek 到 phdr 那一段讀，不整份載入——音源常常一兩百 MB。
    """
    try:
        path = Path(path)
        size = path.stat().st_size
        with path.open('rb') as fh:
            if fh.read(4) != b'RIFF':
                return []
            fh.seek(8)
            if fh.read(4) != b'sfbk':
                return []
            pdta = None
            for chunk_id, off, chunk_size in _chunks(fh, 12, size):
                if chunk_id == 'LIST':
                    fh.seek(off)
                    if fh.read(4) == b'pdta':
                        pdta = (off + 4, off + chunk_size)
                        break
            if pdta is None:
                return []
            for chunk_id, off, chunk_size in _chunks(fh, *pdta):
                if chunk_id != 'phdr':
                    continue
                fh.seek(off)
                data = fh.read(chunk_size)
                out = []
                # 每筆 38 bytes，最後一筆是 EOP 終結記錄
                for i in range(max(0, len(data) // 38 - 1)):
                    record = data[i * 38:(i + 1) * 38]
                    name = record[:20].split(b'\0')[0].decode('latin1', 'replace').strip()
                    number, bank, _bag = struct.unpack('<HHH', record[20:26])
                    out.append((name, number, bank))
                return out
    except (OSError, struct.error, ValueError):
        return []
    return []


def preset_label(path, number: int) -> str:
    """給 UI 顯示的 preset 名稱。"""
    for name, num, bank in list_presets(path):
        if num == int(number):
            return '%d：%s%s' % (num, name, '' if bank == 0 else '（bank %d）' % bank)
    return str(number)


# ── 把內建音源攤出來 ──────────────────────────────────────────────────

def export_builtin(dest_dir=None) -> Optional[Path]:
    """把內建音源複製到使用者資料夾，讓人拿去改或換。

    回傳複製後的路徑；沒有內建音源或複製失敗回 None。已經有同名檔案就直接
    回那一份（不覆蓋使用者可能改過的東西）。
    """
    import shutil

    builtin = builtin_soundfont()
    if builtin is None:
        return None
    source = builtin[0]
    folder = Path(dest_dir) if dest_dir else ensure_user_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        if target.exists():
            return target
        if os.path.abspath(source) == os.path.abspath(target):
            return target
        shutil.copy2(source, target)
        return target
    except OSError:
        return None


def set_selection(path, preset: int = -1) -> None:
    """寫進偏好設定。`path` 空的＝用內建，`preset` 負的＝跟著音源預設。"""
    prefs = _settings()
    text = '' if not path else str(path)
    builtin = builtin_soundfont()
    if text and builtin is not None and os.path.abspath(text) == os.path.abspath(builtin[0]):
        text = ''                       # 內建就存空字串，換版本也不會指到舊路徑
    prefs.set(SETTING_PATH, text)
    prefs.set(SETTING_PRESET, int(preset))
