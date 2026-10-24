"""AI 轉譜完、載入之前的「BPM 與小節線」設定。

自動偵測的 BPM 在 20 首實測裡 16 首完全正確、其餘 4 首剛好差兩倍（候選清單裡
有正確值），所以提供候選與 ×2／÷2。改 BPM 或拍號時自動重找小節起點；
自己改過起點之後就不再自動覆蓋，要的話按「重新偵測」。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from . import ai_grid as G
from . import beat_detect as BD
from .ui_text import tr


class AiGridDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], onsets: List[Tuple[float, int, int]],
                 audio_name: str, bpm_hint: float = 0.0, numerator: int = 4,
                 division: int = 4):
        super().__init__(parent)
        self.setWindowTitle(tr('AI 轉譜：BPM 與小節線'))
        self.setMinimumWidth(460)
        self._onsets = onsets
        self._downbeat_edited = False
        self._updating = False

        root = QVBoxLayout(self)
        head = QLabel(tr('%s\n\n轉出來的音符沒有拍子。設定 BPM 後，音檔前面會補一小段靜音，'
                         '讓第一小節剛好落在小節線上；再把音符吸附到格點。') % audio_name)
        head.setWordWrap(True)
        root.addWidget(head)

        self._enable = QCheckBox(tr('設定 BPM 並吸附小節線'))
        self._enable.setChecked(True)
        self._enable.toggled.connect(self._refresh_enabled)
        root.addWidget(self._enable)

        self._body = QGroupBox()
        form = QFormLayout(self._body)

        bpm_row = QHBoxLayout()
        self._bpm = QDoubleSpinBox()
        self._bpm.setRange(20.0, 999.0)
        self._bpm.setDecimals(2)
        self._bpm.setSingleStep(1.0)
        self._bpm.setValue(120.0)
        self._bpm.valueChanged.connect(self._on_bpm_changed)
        bpm_row.addWidget(self._bpm, 1)
        for text, factor in (('÷2', 0.5), ('×2', 2.0)):
            btn = QPushButton(text)
            btn.setFixedWidth(40)
            btn.clicked.connect(lambda _c=False, f=factor: self._bpm.setValue(self._bpm.value() * f))
            bpm_row.addWidget(btn)
        self._detect_bpm_btn = QPushButton(tr('自動偵測'))
        self._detect_bpm_btn.clicked.connect(self._detect_bpm)
        bpm_row.addWidget(self._detect_bpm_btn)
        form.addRow(tr('BPM'), bpm_row)

        self._candidates = QComboBox()
        self._candidates.activated.connect(self._pick_candidate)
        form.addRow(tr('偵測候選'), self._candidates)

        self._numerator = QSpinBox()
        self._numerator.setRange(1, 16)
        self._numerator.setValue(max(1, int(numerator)))
        self._numerator.setSuffix(' / 4')
        self._numerator.valueChanged.connect(self._on_bpm_changed)
        form.addRow(tr('拍號'), self._numerator)

        down_row = QHBoxLayout()
        self._downbeat = QSpinBox()
        self._downbeat.setRange(0, 60000)
        self._downbeat.setSuffix(' ms')
        self._downbeat.valueChanged.connect(self._on_downbeat_edited)
        down_row.addWidget(self._downbeat, 1)
        redetect = QPushButton(tr('重新偵測'))
        redetect.clicked.connect(self._detect_downbeat)
        down_row.addWidget(redetect)
        form.addRow(tr('第一小節起點（音檔時間）'), down_row)

        self._grid = QComboBox()
        for label, div in G.GRID_CHOICES:
            self._grid.addItem(tr(label), div)
        self._grid.setCurrentIndex(max(0, self._grid.findData(division)))
        self._grid.currentIndexChanged.connect(self._refresh_enabled)
        self._grid.setToolTip(tr(
            '滑音和快速音階不吸附：音高單調小步、又比格子密的一串，只把第一顆對到'
            '格線上，其餘保留原本的相對時間。整串照格線吸附會被壓成幾個和弦。'))
        form.addRow(tr('吸附到'), self._grid)

        self._strength = QSpinBox()
        self._strength.setRange(0, 100)
        self._strength.setValue(100)
        self._strength.setSuffix(' %')
        form.addRow(tr('吸附強度'), self._strength)

        self._snap_length = QCheckBox(tr('長度（放開的時間）也吸附'))
        self._snap_length.setChecked(True)
        form.addRow('', self._snap_length)

        from .settings import settings as _prefs
        self._trim_lead = QCheckBox(tr('自動刪掉開頭多餘的空白'))
        self._trim_lead.setChecked(bool(_prefs.get('ai_trim_lead_in', False)))
        self._trim_lead.setToolTip(tr(
            '對齊小節線只需要補不到一小節的靜音，但音檔開頭常常本來就沉默好幾小節，補完之後譜面就從一大段空白開始。\n打開之後會再往回扣整數個小節（扣整小節不會破壞小節線對齊），只留下面指定的導入小節數。\n不會把第一顆音符扣到負的時間；真的剪到聲音時會在狀態列提醒。'))
        self._trim_lead.toggled.connect(self._refresh_enabled)
        form.addRow('', self._trim_lead)

        self._lead_bars = QSpinBox()
        self._lead_bars.setRange(0, 16)
        self._lead_bars.setValue(int(_prefs.get('ai_lead_in_bars', 1) or 0))
        self._lead_bars.setSuffix(tr(' 小節'))
        self._lead_bars.setToolTip(tr(
            '要留幾小節的導入空白。\n'
            '**0 就是真的 0**：第一顆音符會落在 0 ms，格線的相位改由它決定。\n'
            '1 以上只扣整數個小節，所以原本的拍子相位不會變，但留下來的空白'
            '可能比指定的多（最多多不到一小節）。'))
        form.addRow(tr('保留導入'), self._lead_bars)

        self._variable = QCheckBox(tr('跟著音樂的速度變化（分段偵測）'))
        self._variable.setToolTip(tr(
            '每 16 小節重新量一次速度，相同的併成一段，只在真的換速的地方寫速度變化。\n'
            '定速的曲子偵測結果就是一段，和不勾一樣。勾了之後第一小節起點由偵測決定。'))
        self._variable.toggled.connect(self._refresh_enabled)
        form.addRow('', self._variable)
        self._tempo_info = QLabel()
        self._tempo_info.setWordWrap(True)
        form.addRow('', self._tempo_info)

        self._info = QLabel()
        self._info.setWordWrap(True)
        form.addRow(self._info)
        root.addWidget(self._body)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if not BD.available():
            self._detect_bpm_btn.setEnabled(False)
            redetect.setEnabled(False)
            self._candidates.addItem(tr('（這個版本無法自動偵測，請手動輸入）'))
            self._candidates.setEnabled(False)
        self._curve = None
        if bpm_hint and bpm_hint > 0:
            self._bpm.setValue(float(bpm_hint))
            self._detect_bpm(fill=False)       # 只填候選，不蓋掉給定的 BPM
        else:
            self._detect_bpm()
        self._detect_curve()
        self._refresh_enabled()

    # ── 偵測 ──
    def _detect_bpm(self, fill: bool = True) -> None:
        est = BD.estimate(self._onsets, numerator=self._numerator.value())
        self._candidates.clear()
        if est is None:
            self._candidates.addItem(tr('（音符太少，無法偵測）'))
            return
        for bpm, score in est.candidates[:6]:
            self._candidates.addItem('%.2f' % bpm, bpm)
        if fill:
            self._downbeat_edited = False
            self._bpm.setValue(est.bpm)         # 會觸發 _on_bpm_changed 重找起點
            self._on_bpm_changed()

    def _pick_candidate(self, index: int) -> None:
        bpm = self._candidates.itemData(index)
        if bpm:
            self._bpm.setValue(float(bpm))

    def _detect_downbeat(self) -> None:
        est = BD.estimate(self._onsets, bpm=self._bpm.value(), numerator=self._numerator.value())
        if est is None:
            return
        self._updating = True
        self._downbeat.setValue(int(round(est.downbeat * 1000)))
        self._updating = False
        self._downbeat_edited = False
        self._refresh_info()

    def _detect_curve(self) -> None:
        """偵測速度變化（0.3 秒以內，開窗時就算好，勾選才會用到）。"""
        if not BD.available():
            self._variable.setEnabled(False)
            self._tempo_info.setText(tr('（這個版本無法偵測速度變化）'))
            return
        self._curve = BD.estimate_curve(self._onsets, bpm=self._bpm.value(),
                                        numerator=self._numerator.value())
        segments = self._curve.segments if self._curve else []
        if len(segments) <= 1:
            self._variable.setEnabled(False)
            if self._variable.isChecked():
                self._variable.setChecked(False)
            self._tempo_info.setText(tr('偵測結果：整首同一個速度。'))
            return
        self._variable.setEnabled(True)
        bar = 1
        parts = []
        for segment in segments:
            parts.append('第 %d 小節 %.2f BPM' % (bar, segment.bpm))
            bar += segment.bars
        self._tempo_info.setText(tr('偵測到 %d 段速度：%s') % (len(segments), '，'.join(parts)))

    def _on_bpm_changed(self, *_args) -> None:
        if not self._downbeat_edited:
            self._detect_downbeat()
        else:
            self._refresh_info()
        self._detect_curve()

    def _on_downbeat_edited(self, *_args) -> None:
        if not self._updating:
            self._downbeat_edited = True
        self._refresh_info()

    # ── 顯示 ──
    def _remember(self) -> None:
        """把開頭空白的選擇存進偏好設定。"""
        try:
            from .settings import settings as _prefs
            _prefs.set('ai_trim_lead_in', bool(self._trim_lead.isChecked()))
            _prefs.set('ai_lead_in_bars', int(self._lead_bars.value()))
        except Exception:                               # noqa: BLE001
            pass

    def _refresh_enabled(self, *_args) -> None:
        on = self._enable.isChecked()
        self._body.setEnabled(on)
        snapping = self._grid.currentData() not in (0, None)
        self._strength.setEnabled(on and snapping)
        self._snap_length.setEnabled(on and snapping)
        # 變速時整條拍點表來自偵測，手動改起點沒有意義（每段拍長都不同）
        self._downbeat.setEnabled(on and not self.variable())
        self._lead_bars.setEnabled(on and self._trim_lead.isChecked())
        self._refresh_info()

    def _refresh_info(self) -> None:
        if not self._enable.isChecked():
            self._info.setText(tr('不設定：照原本 120 BPM 匯入，時間就是音檔秒數。'))
            return
        settings = self.settings()
        # 要把第一顆音符傳進去：刪減的基準是它，不給的話預覽會嚴重低報
        # （實測漂流：downbeat 基準算出「補 1273ms」，實際會刪 5127ms）。
        first = min((t for t, _p, _v in self._onsets), default=None)
        pad = G.lead_shift_ms(settings, first)
        bar_ms = settings.bar_s * 1000.0
        downbeat = (settings.beats[0] if settings.variable else settings.downbeat) * 1000.0
        bar_no = int(round((downbeat + pad) / bar_ms)) + 1
        if pad < 0:
            text = tr('一小節 %.0f ms。音檔開頭刪掉 %d ms（音符一起往前移），'
                      '第一顆音符會落在第 %.1f 小節。') % (
                bar_ms, -pad,
                (min((t for t, _p, _v in self._onsets), default=0.0) * 1000.0
                 + pad) / bar_ms + 1)
        else:
            text = tr('一小節 %.0f ms。音檔開頭會補 %d ms 靜音（音符一起往後移），'
                      '第一小節起點落在第 %d 小節的小節線上。') % (
                bar_ms, pad, bar_no)
        if settings.variable:
            text += tr('（第一小節的長度；後面每段各自的速度見上面）')
        self._info.setText(text)

    # ── 結果 ──
    def enabled(self) -> bool:
        return self._enable.isChecked()

    def variable(self) -> bool:
        return bool(self._variable.isChecked() and self._curve
                    and len(self._curve.segments) > 1)

    def settings(self) -> G.GridSettings:
        variable = self.variable()
        curve = self._curve if variable else None
        # 記住開頭空白的選擇：使用者講過兩次「導入空白要 0」，不該每次重新勾
        self._remember()
        return G.GridSettings(
            bpm=curve.bpm_first if curve else float(self._bpm.value()),
            downbeat=curve.beats[0] if curve else self._downbeat.value() / 1000.0,
            numerator=int(self._numerator.value()),
            division=int(self._grid.currentData() or 0),
            strength=self._strength.value() / 100.0,
            snap_length=self._snap_length.isChecked(),
            trim_lead_in=self._trim_lead.isChecked(),
            lead_in_bars=int(self._lead_bars.value()),
            beats=curve.beats if curve else None,
        )
