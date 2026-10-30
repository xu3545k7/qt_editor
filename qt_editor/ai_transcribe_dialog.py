"""「從音檔 AI 轉譜」的介面：安裝確認、進度視窗、環境管理。

實際的安裝與轉譜在 ai_transcribe（不碰 Qt），這裡只把它放進背景執行緒、
把紀錄與進度接到畫面上。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QVBoxLayout)

from . import ai_transcribe as AT
from . import piano_separate as SEP
from . import platform_support as PS
from .settings import settings
from .ui_text import tr

_STAGE_TEXT = {
    'python': '下載 Python' if PS.IS_WINDOWS else '建立 Python 虛擬環境',
    'pip': '下載 pip' if PS.IS_WINDOWS else '升級 pip',
    'torch': '下載 torch',
    'packages': '下載轉譜套件',
    'model': '下載模型權重',
    'selftest': '自我檢查',
    'load_audio': '讀取音檔',
    'load_model': '載入模型',
    'transcribe': '轉譜中',
}
#: 這幾個階段的進度單位是位元組，顯示成 MB。
_BYTE_STAGES = {'python', 'pip', 'torch', 'packages', 'model'}

SOLO_PIANO_HINT = ('模型只用獨奏鋼琴訓練過：整首混音（有鼓、合成器、人聲）會轉出大量雜音。'
                   '有鋼琴分軌（*_piano.wav）的曲子請用分軌。')

#: 裝了分離模型之後，混音也有救（實測見 piano_separate 的說明）
SEPARATE_HINT = ('先把鋼琴從混音裡分離出來再轉。實測 6 首：直接轉混音 F1 29%，'
                 '先分離是 60%（原本就乾淨的鋼琴軌是 93%）。'
                 '分離出來的軌會存成 <音檔>._piano.wav 放在旁邊。')

#: 每種 torch 版本大概多快（安裝前的確認對話框要講清楚要等多久）。
_VARIANT_SPEED = {
    'cuda': '偵測到 NVIDIA 顯卡，裝 GPU 版，一首歌幾秒鐘',
    'mps': '會用 Apple 晶片的 GPU（MPS）轉譜，一首歌約半分鐘（M5 Pro 實測約音檔長度的 14%）',
    'cpu': '沒有可用的 GPU，裝 CPU 版，一首歌約半分鐘到數分鐘（看 CPU）',
}


class _Worker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, job: Callable[..., Any], cancel: threading.Event, parent=None):
        super().__init__(parent)
        self._job = job
        self._cancel = cancel

    def run(self) -> None:
        try:
            result = self._job(self._cancel, self.log.emit, self.progress.emit)
        except AT.Cancelled:
            self.cancelled.emit()
        except Exception as exc:                # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.finished_ok.emit(result)


class TaskDialog(QDialog):
    """跑一個長工作：上面階段＋進度條，下面紀錄。取消會砍掉子程序。

    關閉前一定等執行緒結束——執行緒還在發訊號時把視窗刪掉會整個崩潰。
    """

    def __init__(self, parent, title: str, hint: str,
                 job: Callable[[threading.Event, Callable, Callable], Any]):
        super().__init__(parent)
        self.setWindowTitle(tr(title))
        self.resize(640, 420)
        self.result_value: Any = None
        self.error: str = ''
        self._cancel = threading.Event()
        self._done = False

        layout = QVBoxLayout(self)
        if hint:
            hint_label = QLabel(tr(hint))
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        self._stage = QLabel(tr('準備中…'))
        layout.addWidget(self._stage)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(3000)
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.Monospace)
        self._log.setFont(mono)
        layout.addWidget(self._log, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._button = QPushButton(tr('取消'))
        self._button.clicked.connect(self._on_button)
        row.addWidget(self._button)
        layout.addLayout(row)

        self._worker = _Worker(job, self._cancel, self)
        self._worker.log.connect(self._append)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)

    def exec_(self) -> int:
        self._worker.start()
        return super().exec_()

    # ── 訊號 ──
    def _append(self, line: str) -> None:
        self._log.appendPlainText(line)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        name = tr(_STAGE_TEXT.get(stage, stage))
        if total <= 0:
            self._bar.setRange(0, 0)
            self._stage.setText(name + '…')
            return
        if stage in _BYTE_STAGES:
            text = '%s　%.0f / %.0f MB' % (name, done / 1048576.0, total / 1048576.0)
            # QProgressBar 是 int32，torch 的位元組數會爆，用千分比
            self._bar.setRange(0, 1000)
            self._bar.setValue(int(1000 * min(done, total) / total))
        else:
            text = '%s　%d / %d' % (name, done, total)
            self._bar.setRange(0, total)
            self._bar.setValue(min(done, total))
        self._stage.setText(text)

    def _finish(self, stage_text: str) -> None:
        self._done = True
        self._worker.wait()
        self._stage.setText(stage_text)
        self._button.setText(tr('關閉'))
        self._button.setEnabled(True)

    def _on_ok(self, result: Any) -> None:
        self.result_value = result
        self._finish(tr('完成'))
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self._append('')
        self._append('錯誤：' + message)
        self._finish(tr('失敗'))
        self._bar.setRange(0, 1)
        self._bar.setValue(0)

    def _on_cancelled(self) -> None:
        self._finish(tr('已取消'))
        self.reject()

    def _on_button(self) -> None:
        if self._done:
            self.reject()
            return
        self._cancel.set()
        self._button.setEnabled(False)
        self._stage.setText(tr('取消中…'))

    def reject(self) -> None:
        if not self._done:           # Esc / 關閉鈕：當成取消，等執行緒收尾再關
            self._on_button()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._done:
            self._on_button()
            event.ignore()
            return
        super().closeEvent(event)


# ── 對外 ────────────────────────────────────────────────────────────────

def ensure_installed(parent) -> bool:
    """沒裝就問要不要裝、裝完回傳 True；已經裝好直接 True。"""
    if AT.is_installed():
        return True
    variant = AT.default_variant()
    # Windows 版自己帶 embeddable Python；其他平台要拿系統的 Python 建 venv
    host = '' if PS.IS_WINDOWS else AT.find_host_python()
    if not PS.IS_WINDOWS and not host:
        QMessageBox.warning(
            parent, tr('安裝 AI 轉譜環境'),
            tr('AI 轉譜要另外建一個 Python 環境，但這台機器上找不到 Python %d.%d 以上。\n\n'
               '裝好之後再試一次，例如：\n　brew install python@3.12')
            % AT.MIN_HOST_PYTHON)
        return False
    size_mb = AT.estimated_download_mb(variant)
    text = (
        'AI 轉譜要用到 PyTorch 和 ByteDance 的鋼琴轉譜模型，太大放不進製譜器，'
        '需要另外下載安裝一次（之後就不用了）：\n\n'
        '　下載量：約 %.1f GB，裝好後佔用約 %.1f GB（%s）\n'
        '　安裝位置：%s\n'
        '%s\n'
        '中途取消的話，下次會從還沒完成的步驟接著裝。\n\n'
        '%s\n\n現在安裝嗎？'
    ) % (size_mb / 1000.0, AT.estimated_disk_mb(variant) / 1000.0,
         _VARIANT_SPEED.get(variant, variant), AT.env_root(),
         ('　使用的 Python：%s\n' % host) if host else '',
         SOLO_PIANO_HINT)
    reply = QMessageBox.question(parent, tr('安裝 AI 轉譜環境'), tr(text),
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if reply != QMessageBox.Yes:
        return False
    return run_install(parent, variant)


def run_install(parent, variant: Optional[str] = None) -> bool:
    dlg = TaskDialog(parent, '安裝 AI 轉譜環境', '',
                     lambda cancel, log, progress: AT.install(cancel, log, progress,
                                                               variant=variant))
    dlg.exec_()
    if dlg.result_value is None:
        return False
    info = dlg.result_value
    QMessageBox.information(
        parent, tr('安裝完成'),
        tr('AI 轉譜環境已經裝好。\n\ntorch %s，轉譜會使用 %s%s。') % (
            info.get('torch', '?'), AT.device_label(info.get('device', '')),
            ('（%s）' % info['device_name']) if info.get('device_name') else ''))
    return True


class SeparateDialog(QDialog):
    """要不要先分離鋼琴、用哪個分離器。

    兩個分離器都列出來，連實測數字一起——選哪個是有代價的決定（速度 vs 品質），
    不該只給一個不說理由的勾選框。沒裝的那個標「要下載」，選了會先裝。
    """

    def __init__(self, parent, available: Dict[str, bool]):
        super().__init__(parent)
        self.setWindowTitle(tr('AI 轉譜：先分離鋼琴'))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        intro = QLabel(tr(SEPARATE_HINT))
        intro.setWordWrap(True)
        intro.setStyleSheet('color: #555;')
        layout.addWidget(intro)

        box = QGroupBox(tr('用哪個分離器'))
        inner = QVBoxLayout(box)
        self._buttons = {}
        for key, info in SEP.MODELS.items():
            ready = available.get(key, False)
            text = '%s　—　%s' % (info['label'], tr(info['note']))
            if not ready:
                text += tr('（要先下載）')
            btn = QRadioButton(text)
            btn.setChecked(key == SEP.DEFAULT_MODEL)
            self._buttons[key] = btn
            inner.addWidget(btn)
        layout.addWidget(box)

        self._skip = QCheckBox(tr('不要分離，直接轉原始音檔'))
        self._skip.setToolTip(tr('手上已經是鋼琴錄音的話就不用分離。'))
        layout.addWidget(self._skip)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self) -> str:
        """選了哪個；不分離就回空字串。"""
        if self._skip.isChecked():
            return ''
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return ''


def ensure_components(parent) -> None:
    """環境已經裝好、但後來多出來的必要元件還沒補上的話，問一次要不要補。

    不把 `ENV_VERSION` 加一號：那會讓整個環境被當成沒裝、跳出重裝 5.7GB 的流程，
    而這裡缺的通常只有幾十 MB。

    按「否」就記起來，不要每次轉譜都問一次——轉得出來比轉不出來重要，沒補的話
    會退回舊模型（`AT.transcribe` 裡有那條退路）。
    """
    if not AT.is_installed():
        return                              # 環境本身沒裝，走原本的安裝流程
    missing = AT.missing_components()
    if not missing:
        return
    key = 'ai_skip_components_' + ','.join(sorted(missing))
    if settings.get(key, False):
        return
    labels = {k: label for k, label, _r, _s in AT.COMPONENTS}
    sizes = {k: size for k, _l, _r, size in AT.COMPONENTS}
    nl = chr(10)
    text = tr('轉譜環境少了這些：%s現在補裝嗎？（約 %d MB）') % (
        nl + nl + nl.join('・%s' % labels.get(k, k) for k in missing) + nl + nl,
        sum(sizes.get(k, 0) for k in missing))
    answer = QMessageBox.question(parent, tr('補裝轉譜元件'), text,
                                  QMessageBox.Yes | QMessageBox.No,
                                  QMessageBox.Yes)
    if answer != QMessageBox.Yes:
        settings.set(key, True)
        return
    task = TaskDialog(parent, tr('補裝轉譜元件'), tr('下載中…'),
                      lambda cancel, log, progress:
                      AT.install_components(missing, cancel, log, progress))
    task.exec_()
    if task.error:
        QMessageBox.critical(parent, tr('安裝失敗'), task.error)
    elif task.result_value:
        QMessageBox.warning(
            parent, tr('補裝轉譜元件'),
            tr('這些還是不能用：%s') % '、'.join(
                labels.get(k, k) for k in task.result_value))


def ask_separate(parent, audio_path: str) -> str:
    """要不要先分離鋼琴、用哪個。回傳分離器的名字；不分離回空字串。

    檔名看起來已經是鋼琴分軌就不問——那本來就乾淨，再分一次只會多掉東西。
    """
    if os.path.basename(audio_path).lower().endswith('_piano.wav'):
        return ''
    available = AT.separator_models()
    dlg = SeparateDialog(parent, available)
    if dlg.exec_() != QDialog.Accepted:
        return ''
    choice = dlg.choice()
    if not choice or available.get(choice):
        return choice

    # 選到還沒裝的那個，先裝
    if choice == 'demucs':
        title, job = tr('安裝 demucs'), AT.install_demucs
    else:
        title, job = tr('安裝鋼琴分離模型'), AT.install_separator
    task = TaskDialog(parent, title, tr('下載中…'),
                      lambda cancel, log, progress: job(cancel, log, progress))
    task.exec_()
    if task.error:
        QMessageBox.critical(parent, tr('安裝失敗'), task.error)
    return choice if AT.separator_models().get(choice) else ''


def run_transcribe(parent, audio_path: str, output_path: str,
                   want_wav: bool = True,
                   separate: Optional[str] = None) -> Optional[Dict]:
    """跑轉譜；成功回傳 worker 的結果（notes / pedals / device / elapsed），否則 None。

    `want_wav=False`：非 WAV 的輸入不順便解一份同名 WAV（只要 MIDI 檔的時候用）。
    `separate=None` 會先問使用者要不要分離鋼琴、用哪個分離器；給空字串就不分離。
    """
    ensure_components(parent)
    if separate is None:
        separate = ask_separate(parent, audio_path)
    hint = '%s\n\n%s' % (os.path.basename(audio_path),
                         (tr('先用 %s 分離鋼琴。') % SEP.MODELS[separate]['label'])
                         if separate else SOLO_PIANO_HINT)
    dlg = TaskDialog(parent, 'AI 轉譜', hint,
                     lambda cancel, log, progress: AT.transcribe(
                         audio_path, output_path, cancel, log, progress,
                         want_wav=want_wav, separate=bool(separate),
                         separate_model=separate or '',
                         model=str(settings.get('ai_transcribe_model',
                                                AT.DEFAULT_MODEL))))
    dlg.exec_()
    if dlg.result_value is None and dlg.error:
        # 失敗時視窗留著讓使用者看紀錄，關掉後再給一個簡短的錯誤框
        QMessageBox.critical(parent, tr('AI 轉譜失敗'), dlg.error)
    return dlg.result_value


def manage_environment(parent) -> None:
    """查看／重裝／移除轉譜環境。"""
    info = AT.installed_info()
    root = AT.env_root()
    if info is None:
        exists = os.path.isdir(root)
        text = 'AI 轉譜環境還沒安裝。'
        if exists:
            text += '\n\n（%s 裡有上次沒裝完的檔案，約 %d MB）' % (root, AT.env_size_mb())
    else:
        text = ('AI 轉譜環境已安裝。\n\n'
                '　位置：%s\n　大小：約 %d MB\n　torch：%s%s\n　轉譜裝置：%s%s') % (
            root, AT.env_size_mb(), info.get('torch', '?'),
            ('（CUDA %s）' % info['cuda']) if info.get('cuda') else '',
            AT.device_label(info.get('device', '')),
            ('（%s）' % info['device_name']) if info.get('device_name') else '')
    box = QMessageBox(parent)
    box.setWindowTitle(tr('AI 轉譜環境'))
    box.setText(tr(text))
    install_btn = box.addButton(tr('安裝') if info is None else tr('重新安裝'),
                                QMessageBox.AcceptRole)
    remove_btn = None
    if os.path.isdir(root):
        remove_btn = box.addButton(tr('移除'), QMessageBox.DestructiveRole)
    box.addButton(tr('關閉'), QMessageBox.RejectRole)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is install_btn:
        if info is None:
            ensure_installed(parent)
        else:
            run_install(parent, info.get('variant'))
    elif remove_btn is not None and clicked is remove_btn:
        reply = QMessageBox.question(
            parent, tr('移除 AI 轉譜環境'),
            tr('確定要刪除 %s 嗎？之後要用 AI 轉譜得重新下載。') % root,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            AT.uninstall()
            QMessageBox.information(parent, tr('AI 轉譜環境'), tr('已移除。'))
