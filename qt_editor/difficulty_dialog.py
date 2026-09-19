# -*- coding: utf-8 -*-
"""「生成難度」的選項視窗。

只有三件事要問：生哪幾個難度、寫到哪裡、要不要覆蓋。目標值本身不開放調整
——那是官方 2242 份譜的實測值，不是喜好設定。想微調的話改 `difficulty.TARGETS`。
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QVBoxLayout,
)

from .difficulty import DIFFICULTIES, TARGETS, estimate_level


class DifficultyDialog(QDialog):
    def __init__(self, parent=None, source_path: str = '', chart_name: str = '',
                 real_level: int = 0):
        super().__init__(parent)
        self.setWindowTitle('生成難度')
        self._boxes = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            '從目前這份譜面生出各個難度。\n'
            '音訊事件不會減少——沒被選中的音符會折進鄰近可見音符的 sub_note，\n'
            '和官方譜的做法一樣，所以 keysound 和總音數完全不變。'))

        # 等級是綁在同一首歌的 real 等級上的，不是各自照密度給的。
        level_row = QHBoxLayout()
        self._real_level = QSpinBox()
        self._real_level.setRange(1, 20)
        self._real_level.setValue(int(real_level) if real_level else 13)
        self._real_level.valueChanged.connect(self._refresh_levels)
        level_row.addWidget(QLabel('來源（Real）等級'))
        level_row.addWidget(self._real_level)
        level_row.addWidget(QLabel('各難度的等級由它推算（官方 357 首的對照表）'))
        level_row.addStretch(1)
        layout.addLayout(level_row)

        form = QFormLayout()
        self._labels = {}
        for name in DIFFICULTIES:
            goal = TARGETS[name]
            box = QCheckBox(
                '同手間隔 %dms・同時最多 %d 顆・寬度 %d・長押 %.1f%%'
                % (goal.same_hand_gap_ms, goal.max_simultaneous,
                   goal.note_width, goal.long_ratio * 100))
            box.setChecked(True)
            self._boxes[name] = box
            caption = QLabel('')
            self._labels[name] = caption
            row = QHBoxLayout()
            row.addWidget(caption)
            row.addWidget(box, 1)
            form.addRow(goal.label.upper(), row)
        layout.addLayout(form)
        self._refresh_levels()

        row = QHBoxLayout()
        self._dir = QLineEdit(os.path.dirname(source_path or '') or os.getcwd())
        browse = QPushButton('瀏覽…')
        browse.clicked.connect(self._browse)
        row.addWidget(QLabel('輸出資料夾'))
        row.addWidget(self._dir, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        stem = os.path.splitext(os.path.basename(source_path or ''))[0]
        self._stem = QLineEdit(stem or chart_name or 'chart')
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('檔名前綴'))
        name_row.addWidget(self._stem, 1)
        name_row.addWidget(QLabel('→ 前綴_normal.xml 等'))
        layout.addLayout(name_row)

        layout.addWidget(QLabel(
            '提醒：extreme 的排譜要跑幾十秒，三個一起生大約一分半。'))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_levels(self) -> None:
        real = int(self._real_level.value())
        for name in DIFFICULTIES:
            goal = TARGETS[name]
            level = estimate_level(real, name)
            self._labels[name].setText(
                'Lv. %-2d (%d~%d)' % (level, goal.level_band[0], goal.level_band[1]))

    def levels(self) -> dict:
        real = int(self._real_level.value())
        return {name: estimate_level(real, name) for name in DIFFICULTIES}

    def real_level(self) -> int:
        return int(self._real_level.value())

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, '輸出資料夾', self._dir.text())
        if path:
            self._dir.setText(path)

    def chosen(self) -> List[str]:
        return [n for n in DIFFICULTIES if self._boxes[n].isChecked()]

    def out_dir(self) -> str:
        return self._dir.text().strip()

    def stem(self) -> str:
        return self._stem.text().strip() or 'chart'


class SongFolderDialog(QDialog):
    """生成難度到樂曲資料夾：選曲目、確認來源等級、挑要生哪幾個。

    資料夾要能自己選。自動偵測只在譜面已經歸檔時有用；剛從 MIDI 轉好的譜還
    在暫存目錄裡，那時候得由使用者指定要寫進哪一首。
    """

    def __init__(self, parent=None, chart_path: str = '', song_dir: str = ''):
        super().__init__(parent)
        self.setWindowTitle('生成其他難度到樂曲資料夾')
        self._chart_path = chart_path
        self._plan = None
        self._boxes = {}
        self._reasons = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            '照遊戲讀得懂的版面寫出去：<難度>/<譜名>.json，'
            '並在旁邊留一份 source/<譜名>.xml。\n'
            '等級會併回 register.json，生完直接進遊戲就看得到。'))

        row = QHBoxLayout()
        self._dir = QLineEdit(song_dir)
        # 訊號會把新值當第一個參數傳過來，直接接上去會落在 `first` 上，
        # 於是每次改動都被當成「第一次載入」——等級覆寫因此整個失效。
        self._dir.textChanged.connect(lambda _text: self._reload())
        browse = QPushButton('瀏覽…')
        browse.clicked.connect(self._browse)
        row.addWidget(QLabel('曲目資料夾'))
        row.addWidget(self._dir, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self._status = QLabel('')
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        level_row = QHBoxLayout()
        self._level = QSpinBox()
        self._level.setRange(1, 20)
        self._level.valueChanged.connect(self._on_level_changed)
        level_row.addWidget(QLabel('來源等級'))
        level_row.addWidget(self._level)
        level_row.addWidget(QLabel('低難度的等級照它推算'))
        level_row.addStretch(1)
        layout.addLayout(level_row)

        form = QFormLayout()
        for name in DIFFICULTIES:
            box = QCheckBox('')
            box.setChecked(True)
            self._boxes[name] = box
            form.addRow(TARGETS[name].label.upper(), box)
        layout.addLayout(form)

        layout.addWidget(QLabel(
            '手寫的難度一律不覆蓋；自己生過的會重新生成。\n'
            'EXTREME 的排譜要跑幾十秒，三個一起生大約一分半。'))

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                         | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._loading = False
        # 等級欄要分「使用者指定的」和「還沒填過的」：譜面第一次對不上曲目
        # 時它停在 1，若把 1 當成指定值，之後選好資料夾也會被 Lv.1 卡死，
        # 什麼都生不出來。
        self._level_touched = False
        self._reload()

    # ── 內部 ─────────────────────────────────────────────────
    def _browse(self) -> None:
        start = self._dir.text().strip() or os.path.dirname(self._chart_path or '')
        path = QFileDialog.getExistingDirectory(self, '選擇曲目資料夾', start)
        if path:
            self._dir.setText(path)

    def _on_level_changed(self, _value: int) -> None:
        if self._loading:
            return                          # 是程式自己填的，不是使用者改的
        self._level_touched = True
        self._reload()

    def _reload(self) -> None:
        """資料夾或等級一改就重算，畫面永遠反映真的會發生什麼事。"""
        if self._loading:
            return
        from .song_folder import SongFolderError
        from .song_folder import plan as _plan

        folder = self._dir.text().strip()
        level = int(self._level.value()) if self._level_touched else None
        try:
            self._plan = _plan(self._chart_path, folder or None, level)
        except SongFolderError as exc:
            self._plan = None
            self._status.setText('⚠ %s' % exc)
        if self._plan is None:
            for name in DIFFICULTIES:
                self._boxes[name].setEnabled(False)
                self._boxes[name].setText('')
            self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return

        plan_obj = self._plan
        self._loading = True
        try:
            if not self._level_touched:
                self._level.setValue(max(1, min(20, plan_obj.source_level)))
            if not folder:
                self._dir.setText(plan_obj.song_dir)
        finally:
            self._loading = False

        self._status.setText(
            '曲目「%s」／來源 %s Lv.%d'
            % (plan_obj.song_name, plan_obj.source_label or '（未命名）',
               plan_obj.source_level))
        blocked = dict(plan_obj.skipped)
        for name in DIFFICULTIES:
            box = self._boxes[name]
            if name in blocked:
                box.setChecked(False)
                box.setEnabled(False)
                box.setText('略過：%s' % blocked[name])
            else:
                box.setEnabled(True)
                box.setText('會生成')
                box.setChecked(True)
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(plan_obj.wanted))

    # ── 給呼叫端 ─────────────────────────────────────────────
    def plan(self):
        return self._plan

    def chosen(self) -> List[str]:
        if self._plan is None:
            return []
        return [n for n in self._plan.wanted if self._boxes[n].isChecked()]


class LibraryFillDialog(QDialog):
    """掃整個曲庫，把缺少的難度一次補齊。

    掃描本身要把每一首的來源譜讀進來（音高夠不夠是最常見的卡關原因，而那
    光看 register 看不出來），所以開窗時先跑一次掃描並顯示進度。
    """

    def __init__(self, parent=None, root: str = ''):
        super().__init__(parent)
        self.setWindowTitle('補齊曲庫的難度')
        self.resize(760, 520)
        self._rows = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            '掃過曲庫裡每一首曲子，把缺少的 Normal / Hard / Expert 補上。\n'
            '已經有的難度一律不動——手寫的不覆蓋，之前自己生過的也不重做。'))

        row = QHBoxLayout()
        self._dir = QLineEdit(root)
        browse = QPushButton('瀏覽…')
        browse.clicked.connect(self._browse)
        rescan = QPushButton('重新掃描')
        rescan.clicked.connect(self._scan)
        row.addWidget(QLabel('曲庫資料夾'))
        row.addWidget(self._dir, 1)
        row.addWidget(browse)
        row.addWidget(rescan)
        layout.addLayout(row)

        # 重生成是要自己勾的。預設每次都重做的話，補過一輪之後再掃，畫面上還
        # 是整片「要補」——而那不是事實，只會讓人分不出哪些才是真的缺。
        self._regen = QCheckBox('連之前自動生成的難度也重新生成（來源譜改過才需要）')
        self._regen.toggled.connect(lambda _on: self._scan())
        layout.addWidget(self._regen)

        self._summary = QLabel('')
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                         | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Ok).setText('開始補齊')
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        if root:
            self._scan()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, '選擇曲庫資料夾（UserSongs）', self._dir.text())
        if path:
            self._dir.setText(path)
            self._scan()

    def _scan(self) -> None:
        import os

        from PyQt5.QtWidgets import QProgressDialog

        from .models import NoteModel
        from .song_folder import TARGETS, scan_library

        root = self._dir.text().strip()
        self._list.clear()
        self._rows = []
        if not root or not os.path.isdir(root):
            self._summary.setText('⚠ 找不到這個資料夾。')
            self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return

        progress = QProgressDialog('正在掃描曲庫…', '', 0, 1, self)
        progress.setWindowTitle('補齊曲庫的難度')
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)

        def on_progress(index, total, song):
            progress.setMaximum(max(1, total))
            progress.setValue(index)
            if song:
                progress.setLabelText('正在檢查 %s…' % song)
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()

        def load(path):
            model = NoteModel()
            model.load_json(path)
            return model.notes_tree

        try:
            self._rows = scan_library(root, load, on_progress,
                                      regenerate=self._regen.isChecked())
        finally:
            progress.close()

        ready = [r for r in self._rows if r.ready]
        total_diffs = sum(len(r.plan.wanted) for r in ready)
        self._summary.setText(
            '掃到 %d 首曲子：%d 首要補（共 %d 個難度），%d 首補不了。'
            % (len(self._rows), len(ready), total_diffs,
               len(self._rows) - len(ready)))
        for entry in self._rows:
            if entry.ready:
                text = '✔ %s　→ 補 %s' % (
                    entry.song,
                    '、'.join(TARGETS[k].label for k in entry.plan.wanted))
            else:
                text = '—  %s　%s' % (entry.song, entry.reason)
            item = QListWidgetItem(text)
            if not entry.ready:
                item.setForeground(Qt.gray)
            self._list.addItem(item)
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(bool(ready))

    def ready_rows(self):
        return [r for r in self._rows if r.ready]
