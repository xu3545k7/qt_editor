"""NosMania 首頁的第四格：Hiraeth 套件（PAN 歌曲管理版）。

列出套件裡已經匯入的曲目，直接匯入歌曲包、刪除、開它自己的管理器，
以及在匯入前後強制停止那邊的遊戲（遊戲開著時它的管理器會鎖住不給動）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import hiraeth_tools as H
from .ui_text import tr


class _Job(QThread):
    """背景跑一個 Hiraeth 工作（同步整個曲庫可能要好幾分鐘）。

    `work` 可以收一個 `progress(done, total, 名稱)` 回呼；回傳 False 就是使用者
    按了取消。
    """

    done = pyqtSignal(object, str)
    tick = pyqtSignal(int, int, str)

    def __init__(self, work, parent=None, with_progress: bool = False):
        super().__init__(parent)
        self._work = work
        self._with_progress = with_progress
        self.cancelled = False

    def _progress(self, done: int, total: int, name: str) -> bool:
        self.tick.emit(int(done), int(total), str(name))
        return not self.cancelled

    def run(self) -> None:                               # noqa: D401
        try:
            result = self._work(self._progress) if self._with_progress else self._work()
            self.done.emit(result, '')
        except Exception as exc:                        # noqa: BLE001
            self.done.emit(None, str(exc))


class HiraethPanel(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Hiraeth 套件'))
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.resize(900, 600)
        self._job = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr('套件位置')))
        self.root_edit = QLineEdit(H.saved_root() or H.default_root_guess())
        self.root_edit.setReadOnly(True)
        top.addWidget(self.root_edit, 1)
        for text, slot in ((tr('選擇…'), self._pick_root), (tr('開啟資料夾'), self._open_folder),
                           (tr('開啟 Hiraeth 管理器'), self._open_manager)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            top.addWidget(btn)
        layout.addLayout(top)

        tools = QHBoxLayout()
        self.buttons = {}
        for key, text, slot in (
                ('refresh', tr('重新整理'), self.reload),
                ('import', tr('匯入歌曲包（ZIP）…'), self._import),
                ('import_dir', tr('匯入歌曲資料夾…'), self._import_folder),
                ('delete', tr('從 Hiraeth 刪除選取'), self._delete),
                ('sync', tr('一鍵同步曲庫（雙向）'), self._sync),
                ('stop', tr('強制停止 Hiraeth 遊戲'), self._stop_game)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=slot: f())
            tools.addWidget(btn)
            self.buttons[key] = btn
        from PyQt5.QtWidgets import QMenu, QToolButton
        self.overwrite_btn = QToolButton()
        self.overwrite_btn.setText(tr('單向覆蓋 ▾'))
        self.overwrite_btn.setPopupMode(QToolButton.InstantPopup)
        self.overwrite_btn.setToolTip(tr('以一邊為準，把另一邊換成一樣的（同步是兩邊互補、不刪東西）'))
        menu = QMenu(self.overwrite_btn)
        menu.addAction(tr('nos-clone → Hiraeth（用 nos-clone 覆蓋）…'), self._push_overwrite)
        menu.addAction(tr('Hiraeth → nos-clone（用 Hiraeth 覆蓋）…'), self._pull_overwrite)
        self.overwrite_btn.setMenu(menu)
        self._overwrite_menu = menu
        tools.addWidget(self.overwrite_btn)
        self.buttons['overwrite'] = self.overwrite_btn
        tools.addStretch(1)
        layout.addLayout(tools)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr('曲名'), tr('作者'), tr('難度'), tr('版本')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.status = QLabel('')
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)

        if self.root():
            H.remember_root(self.root())
            self.reload()
        else:
            self._say(tr('還沒指定 Hiraeth 套件：按「選擇…」選解壓好的資料夾（裡面有 SONG_MANAGER.bat）'))
        self._refresh_buttons()

    # ── 基本 ─────────────────────────────────────────────────────────

    def root(self) -> str:
        return self.root_edit.text().strip()

    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _refresh_buttons(self) -> None:
        ok = bool(self.root())
        for key, btn in self.buttons.items():
            btn.setEnabled(ok and (key != 'delete' or bool(self._selected_keys())))

    def _pick_root(self) -> None:
        start = self.root() or os.path.join(os.path.expanduser('~'), 'Downloads')
        picked = QFileDialog.getExistingDirectory(self, tr('選擇 Hiraeth 套件資料夾'), start)
        if not picked:
            return
        root = H.find_root(picked)
        if not root:
            QMessageBox.warning(self, tr('Hiraeth 套件'),
                                tr('這不是 Hiraeth 套件資料夾（找不到 SONG_MANAGER.bat）'))
            return
        self.root_edit.setText(root)
        H.remember_root(root)
        self.reload()

    def _open_folder(self) -> None:
        if self.root():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.root()))

    def _open_manager(self) -> None:
        try:
            H.open_manager(self.root())
        except H.HiraethError as exc:
            QMessageBox.warning(self, tr('Hiraeth 套件'), str(exc))

    # ── 曲目 ─────────────────────────────────────────────────────────

    def reload(self) -> None:
        if not self.root():
            return
        self._run(tr('讀取 Hiraeth 曲庫…'), lambda: H.list_songs(self.root()), self._show_songs)

    def _show_songs(self, songs: List[Dict[str, Any]]) -> None:
        self.table.setRowCount(0)
        for song in songs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            title = QTableWidgetItem(str(song.get('title') or ''))
            title.setData(Qt.UserRole, str(song.get('key') or ''))
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QTableWidgetItem(str(song.get('artist') or '')))
            self.table.setItem(row, 2, QTableWidgetItem(str(song.get('levels') or '')))
            self.table.setItem(row, 3, QTableWidgetItem(str(song.get('version') or '')))
        self._say(tr('Hiraeth 裡有 %d 首') % len(songs))
        self.table.itemSelectionChanged.connect(self._refresh_buttons)
        self._refresh_buttons()

    def _selected_keys(self) -> List[str]:
        rows = {index.row() for index in self.table.selectedIndexes()}
        keys = []
        for row in sorted(rows):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.UserRole):
                keys.append(str(item.data(Qt.UserRole)))
        return keys

    # ── 動作 ─────────────────────────────────────────────────────────

    def _import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr('選擇 Hiraeth 歌曲包'), '', 'ZIP (*.zip)')
        if paths:
            self._import_paths(paths)

    def _import_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr('選擇歌曲資料夾（有 song.json）'))
        if path:
            self._import_paths([path])

    def import_paths(self, paths: List[str]) -> None:
        """給別的地方用：輸出完 ZIP 直接丟進來匯入。"""
        self._import_paths(list(paths))

    def _import_paths(self, paths: List[str]) -> None:
        if not self._ensure_game_stopped():
            return
        root = self.root()

        def work():
            return H.import_packages(root, paths)

        def finished(result):
            done, errors = result
            text = tr('匯入 %d 首。') % len(done)
            if errors:
                text += tr('\n\n失敗：\n') + '\n'.join(errors)
            QMessageBox.information(self, tr('Hiraeth 套件'), text)
            self.reload()

        self._run(tr('匯入 %d 個歌曲包…') % len(paths), work, finished)

    def _delete(self) -> None:
        keys = self._selected_keys()
        if not keys or not self._ensure_game_stopped():
            return
        if QMessageBox.question(self, tr('Hiraeth 套件'),
                                tr('從 Hiraeth 刪掉這 %d 首？\n（它自己會留備份）') % len(keys)) \
                != QMessageBox.Yes:
            return
        root = self.root()
        self._run(tr('刪除 %d 首…') % len(keys), lambda: H.delete_songs(root, keys),
                  lambda _r: self.reload())

    def _sync(self) -> None:
        """把 nos-clone 曲庫整批輸出成歌曲包，再匯入 Hiraeth。沒改過的略過。"""
        from . import hiraeth_sync as S
        from . import song_library as L
        from .settings import settings
        root = L.default_root() or (settings.get('song_library_root', '') or '')
        if not root or not os.path.isdir(root):
            QMessageBox.information(self, tr('Hiraeth 套件'),
                                    tr('找不到 nos-clone 的曲庫，先在曲庫管理選一份。'))
            return
        try:
            todo, unchanged, _skipped = S.plan_sync(root, S.installed_keys(self.root()))
            missing = S.missing_in_library(root, self.root())
            library = L.SongLibrary(root)
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.warning(self, tr('Hiraeth 套件'), str(exc))
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr('一鍵同步曲庫'))
        box.setText(tr('兩邊互補，不會刪任何東西：\n'
                       '・送去 Hiraeth：%d 首（%d 首沒改過會略過）\n'
                       '・搬回 nos-clone：%d 首（Hiraeth 有、這邊沒有的）')
                    % (len(todo), len(unchanged), len(missing)))
        go = box.addButton(tr('開始同步'), QMessageBox.AcceptRole)
        everything = box.addButton(tr('全部重來'), QMessageBox.ActionRole)
        box.addButton(tr('取消'), QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked not in (go, everything):
            return
        force = clicked is everything
        if not todo and not missing and not force:
            self._say(tr('沒有需要同步的曲目'))
            return
        if not self._ensure_game_stopped():
            return
        hiraeth_root = self.root()
        from .hiraeth_export import hold_gap_setting
        gap = hold_gap_setting(settings)
        mix = bool(settings.get('hiraeth_mix_piano', True))
        lead = int(settings.get('hiraeth_lead_in_bars', 1))

        def work(progress):
            return S.sync(root, hiraeth_root, force=force, progress=progress,
                          hold_gap_ms=gap, mix_piano=mix, lead_in_bars=lead,
                          library=library)

        def finished(summary):
            lines = [tr('同步完成：送去 Hiraeth %d 首，搬回 nos-clone %d 首，沒改過略過 %d 首。')
                     % (summary['imported'], len(summary.get('pulled') or []),
                        summary['unchanged'])]
            if summary.get('cancelled'):
                lines.append(tr('（中途取消，已經做完的那幾首有匯入）'))
            if summary['errors']:
                lines += ['', tr('失敗：')] + summary['errors'][:20]
            QMessageBox.information(self, tr('一鍵同步曲庫'), '\n'.join(lines))
            self.reload()

        self._run(tr('同步曲庫…'), work, finished, with_progress=True)

    def _library(self):
        from . import song_library as L
        from .settings import settings
        root = L.default_root() or (settings.get('song_library_root', '') or '')
        if not root or not os.path.isdir(root):
            QMessageBox.information(self, tr('Hiraeth 套件'),
                                    tr('找不到 nos-clone 的曲庫，先在曲庫管理選一份。'))
            return None
        return L.SongLibrary(root)

    def _confirm_overwrite(self, title: str, text: str, extra_label: str):
        """覆蓋前確認。回傳 (要不要做, 要不要連多出來的一起刪)。"""
        from PyQt5.QtWidgets import QCheckBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        check = QCheckBox(extra_label)
        box.setCheckBox(check)
        go = box.addButton(tr('覆蓋'), QMessageBox.AcceptRole)
        box.addButton(tr('取消'), QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is go, check.isChecked()

    def _push_overwrite(self) -> None:
        from . import hiraeth_sync as S
        from .settings import settings
        library = self._library()
        if library is None:
            return
        title = tr('nos-clone → Hiraeth（覆蓋）')
        ok, delete_extra = self._confirm_overwrite(
            title,
            tr('nos-clone 曲庫裡的每一首都會重新輸出送到 Hiraeth，Hiraeth 裡同一首會被換掉'
               '（不管有沒有改過，所以會比同步久）。\n\n'
               'Hiraeth 自己會留更新前的備份。'),
            tr('同時刪掉 Hiraeth 裡 nos-clone 沒有的曲目'))
        if not ok or not self._ensure_game_stopped():
            return
        hiraeth_root = self.root()
        from .hiraeth_export import hold_gap_setting
        options = dict(hold_gap_ms=hold_gap_setting(settings),
                       mix_piano=bool(settings.get('hiraeth_mix_piano', True)),
                       lead_in_bars=int(settings.get('hiraeth_lead_in_bars', 1)))

        def work(progress):
            return S.push_overwrite(library.root, hiraeth_root, library=library,
                                    delete_extra=delete_extra, progress=progress, **options)

        def finished(summary):
            lines = [tr('覆蓋完成：送到 Hiraeth %d 首。') % summary['imported']]
            if summary.get('deleted'):
                lines.append(tr('刪掉 Hiraeth 多出來的 %d 首。') % len(summary['deleted']))
            if summary.get('cancelled'):
                lines.append(tr('（中途取消，已經做完的那幾首有匯入）'))
            if summary['errors']:
                lines += ['', tr('失敗：')] + summary['errors'][:20]
            QMessageBox.information(self, title, '\n'.join(lines))
            self.reload()

        self._run(title, work, finished, with_progress=True)

    def _pull_overwrite(self) -> None:
        from . import hiraeth_sync as S
        library = self._library()
        if library is None:
            return
        title = tr('Hiraeth → nos-clone（覆蓋）')
        ok, delete_extra = self._confirm_overwrite(
            title,
            tr('Hiraeth 裡的每一首都會搬回 nos-clone，nos-clone 裡同一首會被換掉。\n\n'
               '⚠ 這個方向會失真：Hiraeth 存的是簡化過的 XML，Soft／Staccato 會變 Tap、'
               '自動彈的音符會不見、長押是縮過的長度。被換掉的原本那份會搬到回收區，'
               '可以從曲庫管理的「復原上一步」或回收區拿回來。\n\n'
               '分類沿用原本那首的。新手教學一律不動。'),
            tr('同時把 nos-clone 裡 Hiraeth 沒有的曲目搬到回收區'))
        if not ok:
            return
        hiraeth_root = self.root()

        def work(progress):
            return S.pull_overwrite(library, hiraeth_root, delete_extra=delete_extra,
                                    progress=progress)

        def finished(summary):
            lines = [tr('覆蓋完成：換掉 %d 首、新增 %d 首。')
                     % (len(summary['replaced']), len(summary['added']))]
            if summary.get('deleted'):
                lines.append(tr('%d 首 Hiraeth 沒有的搬到回收區。') % len(summary['deleted']))
            if summary['errors']:
                lines += ['', tr('失敗：')] + summary['errors'][:20]
            QMessageBox.information(self, title, '\n'.join(lines))
            self.reload()

        self._run(title, work, finished, with_progress=True)

    def _stop_game(self) -> None:
        if not H.game_running():
            self._say(tr('Hiraeth 的遊戲沒有在跑'))
            return
        if QMessageBox.question(self, tr('Hiraeth 套件'),
                                tr('強制停止 Hiraeth 的遊戲？沒存的成績會不見。')) \
                != QMessageBox.Yes:
            return
        stopped = H.stop_game()
        self._say(tr('已停止：%s') % ('、'.join(stopped) or tr('（沒有正在跑的）')))

    def _ensure_game_stopped(self) -> bool:
        """匯入／刪除前遊戲要關著（Hiraeth 的管理器會鎖住）。"""
        if not H.game_running():
            return True
        reply = QMessageBox.question(
            self, tr('Hiraeth 套件'),
            tr('Hiraeth 的遊戲正開著，管理器會鎖住不給改。要強制停止再繼續嗎？'))
        if reply != QMessageBox.Yes:
            return False
        H.stop_game()
        return True

    # ── 背景工作 ─────────────────────────────────────────────────────

    def _run(self, label: str, work, finished, with_progress: bool = False) -> None:
        progress = QProgressDialog(label, tr('取消') if with_progress else '', 0,
                                   1 if with_progress else 0, self)
        progress.setWindowTitle(tr('Hiraeth 套件'))
        progress.setWindowModality(Qt.WindowModal)
        if not with_progress:
            progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        job = _Job(work, self, with_progress=with_progress)
        self._job = job

        def on_tick(done, total, name):
            progress.setMaximum(max(1, total))
            progress.setValue(done)
            if name:
                progress.setLabelText('(%d/%d) %s' % (done + 1, total, name))
            if progress.wasCanceled():
                job.cancelled = True

        job.tick.connect(on_tick)

        def on_done(result, error):
            progress.close()
            self._job = None
            if error:
                QMessageBox.warning(self, tr('Hiraeth 套件'), error)
                self._say(error)
                return
            finished(result)

        job.done.connect(on_done)
        job.start()
