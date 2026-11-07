# -*- coding: utf-8 -*-
"""拿 Hiraeth **自己的** importer 驗我們產出的包，不靠我讀程式比對。

使用者：「一樣難就好，不要有相容問題即可」。所以重點不是難度標示，是
「裝得進去、不會壞」。

做法：真的匯出幾包，然後 import 他們的 `importer.py`，逐一呼叫裡面的
`unpack` / `audio` / `validate_chart` / 以及 song.json 的那串檢查。用他們的
程式當裁判，我漏看的規則也會被抓到。

**不會安裝**：只解到暫存資料夾驗，不碰 music_list.xml 和 imported.json。
"""
from __future__ import annotations

import glob
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, 'D:/Nostalgia/qt_editor')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

#: Hiraeth 安裝在哪。用環境變數 HIRAETH_ROOT 覆寫。
HIRAETH = Path(os.environ.get(
    'HIRAETH_ROOT', r'C:\Users\shawn\Downloads\Hiraeth_v2\Hiraeth_Public_v2'))
IMPORTER = HIRAETH / 'tools' / 'import_tools' / 'importer.py'


def load_importer():
    """載入 Hiraeth 自己的 importer 當裁判。

    三個坑：
    1. 它用相對匯入（`from .artwork import ...`），要當成套件載，不能用
       `spec_from_file_location`。
    2. `artwork.py` / `ifs_texture.py` 需要 `lxml`、`kbinxml` 等——那些只在
       Hiraeth 自己的 runtime 裡有。驗證完全用不到美術那一段，所以**直接把
       那兩個模組換成假的**，不要去裝那些套件。
    3. 假模組要在 `import_tools` 這個套件名底下先放進 sys.modules，相對匯入
       才找得到。
    """
    import types
    tools = str(HIRAETH / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import importlib
    importlib.import_module('import_tools')          # 先把套件本身載起來
    for name, attrs in (('artwork', ('clone_ifs', 'render')),
                        ('ifs_texture', ('bake', 'loaded_small_group'))):
        full = 'import_tools.' + name
        if full in sys.modules:
            continue
        fake = types.ModuleType(full)
        for attr in attrs:
            setattr(fake, attr, lambda *a, **k: None)
        sys.modules[full] = fake
    return importlib.import_module('import_tools.importer')


def check_one(mod, zip_path):
    """對一個 ZIP 跑完 importer 的每一關，回傳問題清單。"""
    problems = []
    work = Path(tempfile.mkdtemp())
    try:
        try:
            mod.unpack(Path(zip_path), work)
        except Exception as exc:                        # noqa: BLE001
            return ['unpack：%s' % exc]
        # 音訊
        seconds = 0.0
        for name in ('music.wav', 'preview.wav'):
            path = work / name
            if not path.exists():
                continue
            try:
                rate, pcm = mod.audio(path)
                if name == 'music.wav':
                    seconds = len(pcm) / 4.0 / rate
            except Exception as exc:                    # noqa: BLE001
                problems.append('%s：%s' % (name, exc))
        # song.json 的那串
        try:
            spec = json.loads((work / 'song.json').read_text(encoding='utf-8'))
        except Exception as exc:                        # noqa: BLE001
            return problems + ['song.json 讀不了：%s' % exc]
        key = str(spec.get('package_id', ''))
        if spec.get('format') != 1 or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,79}', key):
            problems.append('format / package_id 不合格：%r' % key)
        version = spec.get('version')
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            problems.append('version 不是正整數：%r' % version)
        title = spec.get('title'); artist = spec.get('artist', '')
        if (not isinstance(title, str) or not title.strip() or len(title) > 128
                or not isinstance(artist, str) or len(artist) > 128):
            problems.append('title / artist 不合格')
        slogan = spec.get('slogan', '')
        if not isinstance(slogan, str) or len(slogan) > 160:
            problems.append('slogan 不合格')
        levels = spec.get('charts')
        if not isinstance(levels, dict) or not levels or set(levels) - set(mod.DIFF):
            problems.append('charts 對應不合格：%r' % (levels,))
        else:
            stems = {p.stem for p in work.glob('*.xml')}
            if stems != set(levels):
                problems.append('xml 檔和 charts 對不上：%s vs %s' % (stems, set(levels)))
            for difficulty, level in levels.items():
                real_ok = (difficulty == 'real' and not isinstance(level, bool)
                           and isinstance(level, (int, float))
                           and level in mod.REAL_LEVELS)
                other_ok = (difficulty != 'real' and not isinstance(level, bool)
                            and isinstance(level, int) and 1 <= level <= 15)
                if not (real_ok or other_ok):
                    problems.append('%s 等級 %r 不合格' % (difficulty, level))
        # 譜面：用他們的 validate_chart
        for xml in sorted(work.glob('*.xml')):
            try:
                mod.validate_chart(xml, seconds)
            except Exception as exc:                    # noqa: BLE001
                problems.append('%s：%s' % (xml.name, exc))
        # preview
        pv = work / 'preview.wav'
        if pv.exists():
            try:
                rate, pcm = mod.audio(pv)
                if len(pcm) / 4 / rate > 30:
                    problems.append('preview 超過 30 秒')
            except Exception:                           # noqa: BLE001
                pass
        start = spec.get('preview_start', 0)
        if isinstance(start, (int, float)) and seconds and not 0 <= start < seconds:
            problems.append('preview_start %r 超出曲長 %.1f 秒' % (start, seconds))
        return problems
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    from qt_editor import hiraeth_export as H
    mod = load_importer()
    print('用 %s 當裁判' % IMPORTER)
    print('它第 74 行目前是：')
    line = IMPORTER.read_text(encoding='utf-8', errors='replace').splitlines()[73]
    print('   %s' % line.strip()[:110])
    print()

    corpus = os.environ.get('QTE_CORPUS', 'D:/Nostalgia/UserSongs')
    songs = [os.path.dirname(p) for p in
             sorted(glob.glob(os.path.join(corpus, '*', 'register.json')))]
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    keysound = '--keysound' in sys.argv
    out = tempfile.mkdtemp()
    done = ok = 0
    for song_dir in songs:
        if done >= want:
            break
        try:
            plans, _skipped = H.plan_song_folder(song_dir)
        except Exception:                               # noqa: BLE001
            continue
        if not plans:
            continue
        plan = plans[0]
        plan.keysound = keysound
        try:
            result = H.build_package(plan, out)
        except Exception as exc:                        # noqa: BLE001
            print('  %-26s 匯出失敗：%s' % (os.path.basename(song_dir)[:26], exc))
            done += 1
            continue
        if result.error:
            print('  %-26s 匯出回報錯誤：%s' % (os.path.basename(song_dir)[:26], result.error))
            done += 1
            continue
        zip_path = result.zip_path
        if not zip_path or not os.path.exists(zip_path):
            print('  %-26s 找不到產出的 ZIP' % os.path.basename(song_dir)[:26])
            done += 1
            continue
        problems = check_one(mod, zip_path)
        done += 1
        if problems:
            print('  ✗ %-24s %s' % (os.path.basename(song_dir)[:24],
                                     ' ／ '.join(problems)[:150]))
        else:
            ok += 1
            print('  ✓ %-24s 通過 importer 的每一關（%.1f KB）'
                  % (os.path.basename(song_dir)[:24],
                     os.path.getsize(zip_path) / 1024.0))
    print()
    print('%d 包裡 %d 包通過%s' % (done, ok, '（按鍵音模式）' if keysound else ''))
    shutil.rmtree(out, ignore_errors=True)


if __name__ == '__main__':
    main()
