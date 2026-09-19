"""「輸出 Hiraeth 歌曲包（ZIP）」對話框與背景工作。"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from . import hiraeth_export as H
from .settings import settings
from .ui_text import tr

MODE_SONG, MODE_LIBRARY, MODE_CHART = range(3)

RULES = ('Hiraeth 的匯入規則比 PAN 嚴格：<b>不發按鍵音</b>（聲音全部來自音樂檔，'
         '所以會把背景音樂和鋼琴音軌疊在一起）、'
         'Soft／Staccato 改成 Tap、自動彈的音符拿掉、'
         '難度只有 normal／hard／expert／real（real 等級 1～3.5）。'
         '同一首歌每次輸出的 package_id 都一樣、版本號會遞增，重新匯入就是更新。')


def _path_row(edit: QLineEdit, pick) -> QWidget:
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(edit, 1)
    button = QPushButton(tr('選擇…'))
    button.clicked.connect(lambda _c=False: pick())
    box.addWidget(button)
    return row


class HiraethExportDialog(QDialog):
    def __init__(self, parent, song_dir: Optional[str], library_root: str,
                 model, audio_path: str = ''):
        super().__init__(parent)
        self.setWindowTitle(tr('輸出 Hiraeth 歌曲包（ZIP）'))
        self.resize(620, 0)
        self._model = model
        self._audio_path = audio_path or ''
        layout = QVBoxLayout(self)

        rules = QLabel(tr(RULES))
        rules.setWordWrap(True)
        rules.setStyleSheet('color: #555;')
        layout.addWidget(rules)

        self._group = QButtonGroup(self)
        self.rb_song = QRadioButton(tr('這首歌的樂曲資料夾（register.json 裡的全部難度）'))
        self.rb_library = QRadioButton(tr('整個曲庫（每首一包，跳過新手教學）'))
        self.rb_chart = QRadioButton(tr('只輸出目前開著的譜面'))
        for i, rb in enumerate((self.rb_song, self.rb_library, self.rb_chart)):
            self._group.addButton(rb, i)

        self.song_edit = QLineEdit(song_dir or '')
        self.library_edit = QLineEdit(library_root or '')
        box = QGroupBox(tr('要輸出什麼'))
        form = QVBoxLayout(box)
        form.addWidget(self.rb_song)
        form.addWidget(_path_row(self.song_edit, self._pick_song))
        form.addWidget(self.rb_library)
        form.addWidget(_path_row(self.library_edit, self._pick_library))
        form.addWidget(self.rb_chart)

        self.chart_box = QWidget()
        chart_form = QFormLayout(self.chart_box)
        chart_form.setContentsMargins(24, 0, 0, 0)
        stem = os.path.splitext(os.path.basename(getattr(model, 'current_file', '') or ''))[0]
        self.title_edit = QLineEdit(getattr(model, '_song_name', '') or stem)
        self.artist_edit = QLineEdit('')
        self.slot_combo = QComboBox()
        for slot in H.SLOTS:
            self.slot_combo.addItem(slot)
        self.slot_combo.setCurrentIndex(H.SLOTS.index('expert'))
        from PyQt5.QtWidgets import QDoubleSpinBox
        self.level_spin = QDoubleSpinBox()
        self.level_spin.setRange(1, 15)
        self.level_spin.setDecimals(0)
        self.level_spin.setValue(10)
        self._level_memory = {}
        self._last_level = 10.0
        self.level_spin.valueChanged.connect(self._skip_missing_real_level)
        self.cover_edit = QLineEdit('')
        chart_form.addRow(tr('曲名'), self.title_edit)
        chart_form.addRow(tr('作者'), self.artist_edit)
        chart_form.addRow(tr('難度格'), self.slot_combo)
        chart_form.addRow(tr('等級'), self.level_spin)
        chart_form.addRow(tr('曲繪（PNG／JPG，可空白）'), _path_row(self.cover_edit, self._pick_cover))
        audio_text = (os.path.basename(self._audio_path) if self._audio_path
                      else tr('沒有載入音樂 → 用內建音源把譜面算成鋼琴音軌'))
        chart_form.addRow(tr('音樂'), QLabel(audio_text))
        form.addWidget(self.chart_box)
        layout.addWidget(box)

        options = QFormLayout()
        from PyQt5.QtWidgets import QCheckBox
        # 樂曲評論：song.json 的 slogan，遊戲選曲畫面顯示的那一句
        slogan_row = QWidget()
        slogan_box = QHBoxLayout(slogan_row)
        slogan_box.setContentsMargins(0, 0, 0, 0)
        self.slogan_edit = QLineEdit('')
        self.slogan_edit.setMaxLength(H.MAX_SLOGAN)
        self.slogan_edit.setPlaceholderText(tr('選曲畫面顯示的一句介紹（可空白）'))
        self.slogan_count = QLabel('')
        self.slogan_count.setStyleSheet('color: gray;')
        slogan_box.addWidget(self.slogan_edit, 1)
        slogan_box.addWidget(self.slogan_count)
        self.slogan_edit.textChanged.connect(lambda _t: self._update_slogan_count())
        self.slogan_edit.setToolTip(
            tr('song.json 的 slogan，最多 %d 字。\n'
            '樂曲資料夾模式：會存回那首歌的 register.json，下次自動帶出來。\n'
            '整個曲庫模式：每首用各自 register.json 裡存的評論。') % H.MAX_SLOGAN)
        options.addRow(tr('樂曲評論'), slogan_row)
        self._slogan_loaded_from = None
        self.mix_check = QCheckBox(tr('把鋼琴音軌疊到背景音樂上'))
        self.mix_check.setChecked(bool(settings.get('hiraeth_mix_piano', True)))
        self.mix_check.setToolTip(
            tr('Hiraeth 不發按鍵音，只放音樂檔。\n'
            '樂曲資料夾／曲庫：有 pianoAudioResourcePath 的曲子把那份鋼琴音軌疊上去。\n'
            '目前譜面：用內建音源把譜面算成鋼琴音軌再疊上去。'))
        options.addRow(tr('音樂'), self.mix_check)
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(H.MIN_HOLD_GAP_MS, 1000)
        self.gap_spin.setSuffix(' ms')
        self.gap_spin.setValue(max(H.MIN_HOLD_GAP_MS,
                                   int(settings.get('hiraeth_hold_gap_ms', H.DEFAULT_HOLD_GAP_MS))))
        self.gap_spin.setToolTip(tr('輸出前對每份譜跑「處理長條尾端」：長條尾端到同手下一顆至少留這麼多'))
        self.tail_check = QCheckBox(tr('處理長條尾端'))
        self.tail_check.setChecked(bool(settings.get('hiraeth_process_hold_tails', True)))
        self.tail_check.setToolTip(
            tr('勾選：長條尾端到同手下一顆至少留右邊的間距，同鍵道有別的音就裁短。\n'
               '不勾：長條完全照原樣輸出（同一個鍵上重疊的音也不處理）。'))
        self.tail_check.toggled.connect(self.gap_spin.setEnabled)
        self.gap_spin.setEnabled(self.tail_check.isChecked())
        tail_row = QHBoxLayout()
        tail_row.addWidget(self.tail_check)
        tail_row.addWidget(self.gap_spin)
        tail_row.addStretch(1)
        options.addRow(tr('長條尾端最小間距'), tail_row)
        self.lead_spin = QSpinBox()
        self.lead_spin.setRange(0, 8)
        self.lead_spin.setSuffix(tr(' 小節'))
        self.lead_spin.setValue(int(settings.get('hiraeth_lead_in_bars', H.DEFAULT_LEAD_IN_BARS)))
        self.lead_spin.setToolTip(
            tr('第一顆音符前面至少空幾小節：不夠就在譜面前面補空白小節，'
               '音訊前面補等長的靜音（JSON 原檔不動）。\n'
               '量過官方 682 份譜：第一顆音符中位數就落在第 4 拍，也就是空一整小節。\n'
               '0 = 照譜面原樣輸出。'))
        options.addRow(tr('開頭留白'), self.lead_spin)
        layout.addLayout(options)

        out_row = QFormLayout()
        default_out = settings.get('hiraeth_export_dir', '') or os.path.join(
            os.path.expanduser('~'), 'Desktop', tr('Hiraeth 歌曲包'))
        self.out_edit = QLineEdit(default_out)
        out_row.addRow(tr('輸出到資料夾'), _path_row(self.out_edit, self._pick_out))
        layout.addLayout(out_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr('輸出'))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.song_edit.textChanged.connect(lambda _t: self._load_slogan())
        self.slot_combo.currentIndexChanged.connect(lambda _i: self._sync())
        self._group.buttonToggled.connect(lambda *_a: self._sync())
        if model is None:
            # 從啟動器的曲庫管理叫出來：沒有開著的譜面
            self.rb_chart.setEnabled(False)
            self.rb_chart.setText(tr('只輸出目前開著的譜面（要在製譜器裡用）'))
        (self.rb_song if song_dir or model is None else self.rb_chart).setChecked(True)
        self._sync()

    # ── 介面 ─────────────────────────────────────────────────────────

    def mode(self) -> int:
        return self._group.checkedId()

    def lead_in_bars(self) -> int:
        return int(self.lead_spin.value())

    def _update_slogan_count(self) -> None:
        self.slogan_count.setText('%d／%d' % (len(self.slogan_edit.text()), H.MAX_SLOGAN))

    def _load_slogan(self) -> None:
        """樂曲資料夾換了就帶出那首歌存的評論（使用者已經打過字就不蓋掉）。"""
        song_dir = self.song_edit.text().strip()
        if not os.path.exists(os.path.join(song_dir, 'register.json')):
            return
        if song_dir == self._slogan_loaded_from:
            return
        current = self.slogan_edit.text()
        previous = H.read_slogan(self._slogan_loaded_from) if self._slogan_loaded_from else ''
        if current and current != previous:
            return
        self._slogan_loaded_from = song_dir
        self.slogan_edit.setText(H.read_slogan(song_dir))

    def _sync(self) -> None:
        mode = self.mode()
        # 曲庫模式每首用自己 register.json 裡的評論，這一格用不到
        self.slogan_edit.setEnabled(mode != MODE_LIBRARY)
        if mode == MODE_SONG:
            self._load_slogan()
        self._update_slogan_count()
        self.song_edit.parentWidget().setEnabled(mode == MODE_SONG)
        self.library_edit.parentWidget().setEnabled(mode == MODE_LIBRARY)
        self.chart_box.setEnabled(mode == MODE_CHART)
        real = self.slot_combo.currentText() == 'real'
        if real == getattr(self, '_level_is_real', None):
            return
        # REAL（1～3.5 半級）和其他難度（1～15）各記各的，切換難度格時不會
        # 被新的範圍夾掉：原本填 12 切到 real 再切回來，要還是 12。
        previous = getattr(self, '_level_is_real', None)
        if previous is not None:
            self._level_memory[previous] = self.level_spin.value()
        self._level_is_real = real
        self.level_spin.blockSignals(True)
        self.level_spin.setDecimals(1 if real else 0)
        self.level_spin.setSingleStep(0.5 if real else 1)
        self.level_spin.setRange(1, 3.5 if real else 15)
        self.level_spin.setToolTip(tr('REAL 等級 1～3.5，每格 0.5') if real else '1～15')
        self.level_spin.setValue(self._level_memory.get(real, 3 if real else 10))
        self.level_spin.blockSignals(False)

    def _pick_song(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr('選擇樂曲資料夾'), self.song_edit.text())
        if path:
            self.song_edit.setText(path)

    def _pick_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr('選擇曲庫（UserSongs）'), self.library_edit.text())
        if path:
            self.library_edit.setText(path)

    def _pick_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('選擇曲繪'), '', tr('圖片 (*.png *.jpg *.jpeg)'))
        if path:
            self.cover_edit.setText(path)

    def _pick_out(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr('輸出到哪個資料夾'), self.out_edit.text())
        if path:
            self.out_edit.setText(path)

    # ── 結果 ─────────────────────────────────────────────────────────

    def _skip_missing_real_level(self, value: float) -> None:
        """REAL 沒有 1.5：按上下鍵停到 1.5 就往按的方向跳到 1 或 2。"""
        if getattr(self, '_level_is_real', False) and abs(value - 1.5) < 1e-6:
            target = 2.0 if self._last_level < 1.5 else 1.0
            self.level_spin.blockSignals(True)
            self.level_spin.setValue(target)
            self.level_spin.blockSignals(False)
            value = target
        self._last_level = value

    def plans(self):
        """回傳 (要輸出的包, 略過的說明)。資料不齊時丟 ValueError（訊息給使用者看）。"""
        mode = self.mode()
        if mode == MODE_SONG:
            song_dir = self.song_edit.text().strip()
            if not os.path.exists(os.path.join(song_dir, 'register.json')):
                raise ValueError(tr('這個資料夾沒有 register.json，不是樂曲資料夾。'))
            plans, skipped = H.plan_song_folder(song_dir)
            return self._apply_options(plans), skipped
        if mode == MODE_LIBRARY:
            root = self.library_edit.text().strip()
            if not os.path.isdir(root):
                raise ValueError(tr('找不到曲庫資料夾。'))
            plans, skipped = H.plan_library(root)
            return self._apply_options(plans), skipped
        title = H.sjis_safe(self.title_edit.text())
        if not title:
            raise ValueError(tr('請填曲名。'))
        slot = self.slot_combo.currentText()
        value = self.level_spin.value()
        if slot == 'real':
            half = round(value * 2) / 2.0
            if half not in H.REAL_LEVELS:        # 沒有 REAL 1.5
                half = min(H.REAL_LEVELS, key=lambda level: (abs(level - half), level))
            chart = H.ChartSource(slot, slot, 0, model=self._model,
                                  real_display=int(half) if half == int(half) else half)
        else:
            chart = H.ChartSource(slot, slot, int(round(value)), model=self._model)
        # id 用曲名：檔案搬位置、換難度格都還是同一包，重新匯入就是更新
        plan = H.PackagePlan(H.package_id(title, 'chart'), title, H.sjis_safe(self.artist_edit.text()),
                             [chart], audio_path=self._audio_path or None,
                             no_background=not self._audio_path,
                             cover_path=self.cover_edit.text().strip() or None, label=title,
                             render_piano_layer=True)
        return self._apply_options([plan]), []

    def _apply_options(self, plans):
        slogan = H.clean_slogan(self.slogan_edit.text())
        for plan in plans:
            plan.mix_piano = self.mix_check.isChecked()
            plan.lead_in_bars = self.lead_in_bars()
            if self.mode() != MODE_LIBRARY:
                plan.slogan = slogan
        return plans

    def hold_gap_ms(self) -> int:
        """0 ＝ 不處理長條尾端。"""
        if not self.tail_check.isChecked():
            return H.NO_HOLD_PROCESSING
        return int(self.gap_spin.value())

    def _accept(self) -> None:
        try:
            self.plans()
        except ValueError as exc:
            QMessageBox.warning(self, tr('輸出 Hiraeth 歌曲包'), str(exc))
            return
        if not self.out_edit.text().strip():
            QMessageBox.warning(self, tr('輸出 Hiraeth 歌曲包'), tr('請選輸出資料夾。'))
            return
        settings.set('hiraeth_export_dir', self.out_edit.text().strip())
        settings.set('hiraeth_mix_piano', self.mix_check.isChecked())
        settings.set('hiraeth_process_hold_tails', self.tail_check.isChecked())
        settings.set('hiraeth_hold_gap_ms', int(self.gap_spin.value()))
        settings.set('hiraeth_lead_in_bars', self.lead_in_bars())
        if self.mode() == MODE_SONG:
            # 評論存回 register.json，下次輸出同一首自動帶出來
            H.write_slogan(self.song_edit.text().strip(), self.slogan_edit.text())
        self.accept()


class HiraethExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_all = pyqtSignal(list)

    def __init__(self, plans, out_dir: str, parent=None, hold_gap_ms: int = H.DEFAULT_HOLD_GAP_MS):
        super().__init__(parent)
        self._plans = plans
        self._out = out_dir
        self._gap = int(hold_gap_ms)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        results: List[H.PackageResult] = []
        version = H.default_version()
        for i, plan in enumerate(self._plans):
            if self._cancel:
                break
            self.progress.emit(i, len(self._plans), plan.title)
            results.append(H.build_package(plan, self._out, version=version, hold_gap_ms=self._gap))
        self.progress.emit(len(self._plans), len(self._plans), '')
        self.finished_all.emit(results)


def summary_text(results, skipped, out_dir: str) -> str:
    done = [r for r in results if r.zip_path]
    failed = [r for r in results if r.error]
    lines = [tr('輸出 %d 個 ZIP 到：%s') % (len(done), out_dir)]
    if failed:
        lines += ['', tr('失敗 %d 包：') % len(failed)] + ['・%s：%s' % (r.plan.label, r.error) for r in failed]
    noted = [r for r in done if r.notes]
    if noted:
        lines += ['', tr('有做轉換的：')] + ['・%s：%s' % (r.plan.title, '、'.join(r.notes))
                                  for r in noted[:20]]
        if len(noted) > 20:
            lines.append(tr('・…另外 %d 包') % (len(noted) - 20))
    if skipped:
        lines += ['', tr('略過：')] + ['・' + s for s in skipped[:20]]
    lines += ['', tr('在 Hiraeth 管理器按「导入歌曲包」選這些 ZIP 就能匯入（可以一次多選）。')]
    return '\n'.join(lines)


def open_folder(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def run_export(parent, plans, skipped, out_dir: str, hold_gap_ms: int) -> HiraethExportWorker:
    """背景輸出 Hiraeth ZIP：進度視窗 → 結果（可打開資料夾）。製譜器和啟動器共用。"""
    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtWidgets import QProgressDialog

    progress = QProgressDialog(tr('準備中…'), tr('取消'), 0, len(plans), parent)
    progress.setWindowTitle(tr('輸出 Hiraeth 歌曲包'))
    progress.setWindowModality(_Qt.WindowModal)
    progress.setMinimumDuration(0)
    worker = HiraethExportWorker(plans, out_dir, parent, hold_gap_ms=hold_gap_ms)
    parent._hiraeth_worker = worker

    def on_progress(done: int, total: int, title: str) -> None:
        progress.setValue(done)
        if title:
            progress.setLabelText('(%d/%d) %s' % (done + 1, total, title))

    def on_finished(results) -> None:
        progress.close()
        parent._hiraeth_worker = None
        text = summary_text(results, skipped, out_dir)
        box = QMessageBox(parent)
        box.setWindowTitle(tr('輸出 Hiraeth 歌曲包'))
        box.setIcon(QMessageBox.Warning if any(r.error for r in results)
                    else QMessageBox.Information)
        box.setText(text)
        open_btn = box.addButton(tr('打開資料夾'), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec_()
        if box.clickedButton() is open_btn:
            open_folder(out_dir)

    worker.progress.connect(on_progress)
    worker.finished_all.connect(on_finished)
    progress.canceled.connect(worker.cancel)
    worker.start()
    return worker
