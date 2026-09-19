"""整個曲庫層級的工具：補齊難度、打包備份、從備份還原。

這些功能本來散在製譜器的「工具」選單裡，但它們動的都不是「目前這份譜」，而是
**整個曲庫** —— 而曲庫管理才是那個看得到整個曲庫的視窗。選單的入口留著（有人已
經記得路徑了），實作搬到這裡，兩邊呼叫同一份。
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from typing import List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QDialog, QFileDialog, QMessageBox,
                             QProgressDialog)

from .ui_text import tr

# 打包時跳過的東西。
#
# `*_editor` 是曲庫管理自己的暫存（回收區、還原點），一份備份裡放這些只會讓檔案
# 變大又容易誤導 —— 還原的人以為連「復原上一步」的歷史都回來了，其實那些是對不上
# 的。`.git` 之類同理。
SKIP_DIRS = {'.git', '__pycache__', 'trash', 'history'}
SKIP_SUFFIX = ('.tmp', '.bak', '.log')

#: 新手教學的分類名。乾淨版只留這一類的曲目。
TUTORIAL_CATEGORY = '新手教學'

#: 乾淨版一定不帶的東西。
#:
#: `*_BurstDebugInformation_DoNotShip` 是 Unity 自己說不要出貨的除錯資料；
#: `UserSongs_editor` 是曲庫管理的回收區和還原點（別人的機器上還原不了，只會
#: 讓壓縮檔變大）；當掉的傾印檔和日誌同理。
CLEAN_SKIP_DIRS = {'UserSongs_editor', 'trash', 'history', '__pycache__', '.git',
                   # 製譜器的操作紀錄（每一次編輯的前後差異，純個人資料）
                   'logs'}
CLEAN_SKIP_SUFFIX = ('.log', '.tmp', '.bak', '.dmp', '.pdb', '.jsonl')
#: 遊戲資料夾根目錄裡屬於「這台電腦」的檔案：製譜器的偏好設定（裡面是本機路徑）、
#: 啟動器寫給遊戲的語言檔、曲庫變動記號。別人解壓後應該從預設值開始。
CLEAN_SKIP_ROOT_FILES = {'settings.json', 'nosmania_launcher.json', '.editor_revision.json'}


# ── 補齊難度 ─────────────────────────────────────────────────────────

def fill_difficulties(parent, root: str = '') -> None:
    """掃整個曲庫，把缺少的 Normal / Hard / Expert 一次補齊。"""
    from .difficulty_dialog import LibraryFillDialog

    dlg = LibraryFillDialog(parent, root=root)
    if dlg.exec_() != QDialog.Accepted:
        return
    rows = dlg.ready_rows()
    if not rows:
        return
    run_fill(parent, rows)


def run_fill(parent, rows) -> None:
    """背景跑整庫補齊，主執行緒顯示進度。"""
    from .difficulty import TARGETS, generate
    from .models import NoteModel
    from .song_folder import commit, write_difficulty

    total = sum(len(r.plan.wanted) for r in rows)

    class _Worker(QThread):
        step = pyqtSignal(str, int)
        done = pyqtSignal(list, str)

        def run(self):
            made, error, index = [], '', 0
            try:
                for entry in rows:
                    records = []
                    for key in entry.plan.wanted:
                        index += 1
                        self.step.emit('%s — %s' % (entry.song, TARGETS[key].label), index)
                        copy = NoteModel()
                        copy.load_json(entry.source_path)
                        result = generate(copy, key)
                        records.append(write_difficulty(entry.plan, key, copy, result))
                    if records:
                        commit(entry.plan, records)
                        made.append((entry.song, len(records)))
            except Exception as exc:                        # noqa: BLE001
                logging.exception('library fill failed')
                error = str(exc)
            self.done.emit(made, error)

    state = _pump(parent, _Worker(), total, tr('補齊曲庫的難度'), tr('正在補齊難度…'),
                  lambda text, index: tr('正在生成 %s…（%d / %d）') % (text, index, total))
    if state['error']:
        QMessageBox.critical(parent, tr('補齊曲庫的難度'), tr('中途失敗了：%s') % state['error'])
        return
    made = state['made']
    QMessageBox.information(
        parent, tr('補齊曲庫的難度'),
        tr('補好 %d 首，共 %d 個難度。\n\n%s')
        % (len(made), sum(n for _s, n in made),
           '\n'.join(tr('%s（%d 個）') % row for row in made[:30])))


# ── 備份 ─────────────────────────────────────────────────────────────

def backup_zip(parent, root: str) -> None:
    """把整個曲庫打包成一個 ZIP。"""
    if not root or not os.path.isdir(root):
        QMessageBox.warning(parent, tr('備份曲庫'), tr('還沒選曲庫。'))
        return

    default = os.path.join(os.path.dirname(root), '%s_backup.zip' % os.path.basename(root))
    path, _ = QFileDialog.getSaveFileName(parent, tr('把曲庫存成 ZIP'), default, 'ZIP (*.zip)')
    if not path:
        return

    files = _collect(root, skip=os.path.abspath(path))
    if not files:
        QMessageBox.warning(parent, tr('備份曲庫'), tr('這個曲庫裡沒有東西可以打包。'))
        return

    class _Worker(QThread):
        step = pyqtSignal(str, int)
        done = pyqtSignal(list, str)

        def run(self):
            error = ''
            try:
                # 先寫暫存檔再改名：中途失敗（滿了、拔隨身碟）不會留下一個看起來
                # 正常、其實只有一半的備份 —— 那種檔案比沒有備份更危險。
                temp = path + '.part'
                with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for index, (full, rel) in enumerate(files, 1):
                        self.step.emit(rel, index)
                        zf.write(full, os.path.join(os.path.basename(root), rel))
                os.replace(temp, path)
            except Exception as exc:                        # noqa: BLE001
                logging.exception('library backup failed')
                error = str(exc)
                try:
                    if os.path.exists(path + '.part'):
                        os.remove(path + '.part')
                except OSError:
                    pass
            self.done.emit([], error)

    state = _pump(parent, _Worker(), len(files), tr('備份曲庫'), tr('正在打包…'),
                  lambda text, index: tr('正在打包 %s…（%d / %d）') % (text, index, len(files)))
    if state['error']:
        QMessageBox.critical(parent, tr('備份曲庫'), tr('打包失敗：%s') % state['error'])
        return
    size = os.path.getsize(path) / (1024 * 1024)
    QMessageBox.information(parent, tr('備份曲庫'),
                            tr('已存成：\n%s\n\n%d 個檔案，%.1f MB') % (path, len(files), size))


def export_clean_game(parent, lib, game_exe: str) -> None:
    """把整個遊戲資料夾打包成一份**沒有曲目**的 ZIP，只留新手教學。

    給別人的版本不該帶著自己的曲庫：那裡面是一整櫃別人的版權音樂，而且動輒幾
    個 GB。教學曲留著，因為沒有它們，第一次打開遊戲的人連怎麼玩都看不到。

    曲目資料在**兩個地方**，兩邊都要處理：

    * `UserSongs/`：玩家自己的曲庫（這台機器上 116 首、7.3 GB）。
    * `<遊戲>_Data/StreamingAssets/`：跟著建置出貨的那幾首。

    `library.json` 和 `songlist.json` 不是照抄的，是**重寫過的**：留著已經被拿掉
    的曲目，遊戲的選單會列出一堆點下去什麼都沒有的項目。
    """
    game_dir = os.path.dirname(os.path.abspath(game_exe)) if game_exe else ''
    if not game_dir or not os.path.isdir(game_dir):
        QMessageBox.warning(parent, tr('匯出乾淨版'),
                            tr('找不到遊戲資料夾。先在上面設定「遊戲位置…」。'))
        return

    keep = _tutorial_folders(lib)
    if not keep:
        if QMessageBox.question(
                parent, tr('匯出乾淨版'),
                tr('這個曲庫裡找不到「%s」分類的曲目，匯出的版本會一首歌都沒有。要繼續嗎？')
                % TUTORIAL_CATEGORY) != QMessageBox.Yes:
            return

    default = os.path.join(os.path.dirname(game_dir),
                           '%s_clean.zip' % os.path.basename(game_dir))
    path, _ = QFileDialog.getSaveFileName(parent, tr('匯出乾淨版遊戲'), default, 'ZIP (*.zip)')
    if not path:
        return

    files, dropped = _collect_clean(game_dir, keep, skip=os.path.abspath(path))
    if not files:
        QMessageBox.warning(parent, tr('匯出乾淨版'), tr('這個遊戲資料夾裡沒有東西可以打包。'))
        return

    library_root = os.path.join(game_dir, 'UserSongs')
    rewritten = _pruned_index(library_root, keep)
    top = os.path.basename(game_dir)

    class _Worker(QThread):
        step = pyqtSignal(str, int)
        done = pyqtSignal(list, str)

        def run(self):
            error = ''
            try:
                temp = path + '.part'
                with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for index, (full, rel) in enumerate(files, 1):
                        self.step.emit(rel, index)
                        zf.write(full, os.path.join(top, rel))
                    for rel, text in rewritten.items():
                        zf.writestr(os.path.join(top, rel), text)
                os.replace(temp, path)
            except Exception as exc:                        # noqa: BLE001
                logging.exception('clean export failed')
                error = str(exc)
                try:
                    if os.path.exists(path + '.part'):
                        os.remove(path + '.part')
                except OSError:
                    pass
            self.done.emit([], error)

    state = _pump(parent, _Worker(), len(files), tr('匯出乾淨版'), tr('正在打包…'),
                  lambda text, index: tr('正在打包 %s…（%d / %d）') % (text, index, len(files)))
    if state['error']:
        QMessageBox.critical(parent, tr('匯出乾淨版'), tr('打包失敗：%s') % state['error'])
        return
    size = os.path.getsize(path) / (1024 * 1024)
    QMessageBox.information(
        parent, tr('匯出乾淨版'),
        tr('已存成：%s') % path + NL + NL
        + tr('留下 %d 首教學曲，拿掉 %d 首曲目，共 %d 個檔案、%.0f MB。')
        % (len(keep), dropped, len(files), size))


def _tutorial_folders(lib) -> set:
    """哪些資料夾是教學曲。

    分類為準，資料夾名稱當備援：分類是使用者可以改的，而教學曲的資料夾名稱一律
    以「新手教學」開頭。兩個訊號都認，漏掉一首教學曲的代價比多帶一首大。
    """
    keep = set()
    try:
        for song in lib.songs(check=False):
            if any(TUTORIAL_CATEGORY in (c or '') for c in song.categories):
                keep.add(song.folder)
    except Exception:                                       # noqa: BLE001
        logging.exception('tutorial scan failed')
    try:
        for name in os.listdir(lib.root):
            if name.startswith(TUTORIAL_CATEGORY) and os.path.isdir(os.path.join(lib.root, name)):
                keep.add(name)
    except OSError:
        pass
    return keep


def _collect_clean(game_dir: str, keep: set, skip: str = ''):
    """遊戲資料夾裡要帶走的檔案，以及拿掉了幾首曲目。"""
    songs_root = os.path.normcase(os.path.join(game_dir, 'UserSongs'))
    found, dropped = [], 0
    for base, dirs, names in os.walk(game_dir):
        dirs[:] = sorted(d for d in dirs
                         if d not in CLEAN_SKIP_DIRS
                         and not d.endswith('_BurstDebugInformation_DoNotShip'))
        here = os.path.normcase(base)

        # 曲庫底下：只留教學曲。
        if here == songs_root:
            trimmed = []
            for name in dirs:
                if name in keep:
                    trimmed.append(name)
                else:
                    dropped += 1
            dirs[:] = trimmed
        # 建置內附的曲目：整個不帶。
        elif os.path.basename(base) == 'StreamingAssets':
            dropped += len(dirs)
            dirs[:] = []

        for name in sorted(names):
            if name.lower().endswith(CLEAN_SKIP_SUFFIX):
                continue
            if here == os.path.normcase(game_dir) and name.lower() in CLEAN_SKIP_ROOT_FILES:
                continue
            if here == songs_root and name == '.editor_revision.json':
                continue
            full = os.path.join(base, name)
            if skip and os.path.abspath(full) == skip:
                continue
            # 索引是重寫的，不照抄。
            if here == songs_root and name in ('library.json', 'songlist.json'):
                continue
            found.append((full, os.path.relpath(full, game_dir)))
    return found, dropped


def _pruned_index(library_root: str, keep: set) -> dict:
    """重寫過的 library.json / songlist.json，只剩留下來的曲目。"""
    out = {}

    index = _read_json(os.path.join(library_root, 'library.json'))
    if isinstance(index, dict):
        songs = [s for s in (index.get('songs') or [])
                 if isinstance(s, dict) and str(s.get('folderName') or '') in keep]
        index['songs'] = songs
        index['categories'] = sorted({str(c) for s in songs
                                      for c in (s.get('categories') or [s.get('category')]) if c})
        out[os.path.join('UserSongs', 'library.json')] = json.dumps(
            index, ensure_ascii=False, indent=2)

    songlist = _read_json(os.path.join(library_root, 'songlist.json'))
    if isinstance(songlist, dict):
        cats = {}
        for category, folders in (songlist.get('categories') or {}).items():
            left = [f for f in (folders or []) if str(f) in keep]
            if left:
                cats[category] = left
        songlist['categories'] = cats
        out[os.path.join('UserSongs', 'songlist.json')] = json.dumps(
            songlist, ensure_ascii=False, indent=2)
    return out


def _read_json(path: str):
    try:
        with io.open(path, encoding='utf-8-sig') as handle:
            return json.load(handle)
    except Exception:                                       # noqa: BLE001
        return None


def _collect(root: str, skip: str = '') -> List[tuple]:
    """曲庫裡所有要打包的檔案，回傳 (完整路徑, 相對路徑)。"""
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith('_editor'))
        for name in sorted(names):
            if name.lower().endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(base, name)
            if skip and os.path.abspath(full) == skip:
                continue                                    # 別把自己包進去
            found.append((full, os.path.relpath(full, root)))
    return found


def import_backup_zip(parent, lib, category: Optional[str] = None) -> List[str]:
    """從備份 ZIP 把曲目匯進目前的曲庫。

    不直接把壓縮檔倒進曲庫：裡面的 `library.json` 是**那一份**曲庫的索引，覆蓋
    過來會把這邊既有的曲目全部從索引上抹掉。改成把每一個樂曲資料夾各自走一次
    正規的匯入流程，索引由匯入自己維護。
    """
    path, _ = QFileDialog.getOpenFileName(parent, tr('選擇曲庫備份 ZIP'), '', 'ZIP (*.zip)')
    if not path:
        return []

    temp = tempfile.mkdtemp(prefix='nosmania_restore_')
    done, failed = [], []
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(temp)
        songs = _song_folders(temp)
        if not songs:
            QMessageBox.warning(parent, tr('匯入曲庫備份'),
                                tr('這個 ZIP 裡找不到樂曲資料夾（要有 register.json）。'))
            return []

        progress = QProgressDialog(tr('正在匯入…'), '', 0, len(songs), parent)
        progress.setWindowTitle(tr('匯入曲庫備份'))
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModal)
        for index, folder in enumerate(songs, 1):
            progress.setLabelText(tr('正在匯入 %s…（%d / %d）')
                                  % (os.path.basename(folder), index, len(songs)))
            progress.setValue(index - 1)
            QApplication.processEvents()
            try:
                done.append(lib.import_folder(folder, category)
                            if category else lib.import_folder(folder))
            except Exception as exc:                        # noqa: BLE001
                failed.append('%s：%s' % (os.path.basename(folder), exc))
        progress.close()
    except Exception as exc:                                # noqa: BLE001
        logging.exception('library restore failed')
        QMessageBox.critical(parent, tr('匯入曲庫備份'), tr('讀不了這個 ZIP：%s') % exc)
        return []
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    text = tr('匯入 %d 首。') % len(done)
    if failed:
        text += tr('\n\n失敗：\n') + '\n'.join(failed[:20])
    QMessageBox.information(parent, tr('匯入曲庫備份'), text)
    return done


def _song_folders(root: str) -> List[str]:
    """ZIP 解出來之後，哪些資料夾是樂曲資料夾。"""
    found = []
    for base, dirs, names in os.walk(root):
        if 'register.json' in names:
            found.append(base)
            dirs[:] = []                                    # 樂曲資料夾裡面不會再有樂曲
        else:
            dirs[:] = sorted(dirs)
    return sorted(found)


# ── 共用的進度條 ─────────────────────────────────────────────────────

def _pump(parent, worker, total: int, title: str, label: str, describe) -> dict:
    """跑一個背景工作，主執行緒顯示進度並保持回應。"""
    progress = QProgressDialog(label, '', 0, max(1, total), parent)
    progress.setWindowTitle(title)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    # 一定要 modal。底下那個 processEvents() 的迴圈會讓整個視窗保持可點，
    # 使用者能在補難度跑到一半的時候再按一次同一個選單 —— 兩條執行緒於是對
    # 同一批 register.json 做「讀-改-寫」，先寫完的那一份就被蓋掉了。
    progress.setWindowModality(Qt.WindowModal)
    progress.setValue(0)

    state = {'made': [], 'error': ''}
    worker.step.connect(lambda text, index: (progress.setLabelText(describe(text, index)),
                                             progress.setValue(index - 1)))
    worker.done.connect(lambda made, error: state.update(made=made, error=error))
    worker.start()
    while not worker.isFinished():
        QApplication.processEvents()
        worker.wait(50)
    QApplication.processEvents()
    progress.close()
    return state
