# -*- coding: utf-8 -*-
"""「以音檔補上踏板」的確認框。

轉譜已經跑完了，這一步只做一件事：**在寫進譜面之前，讓使用者看到對位的結果**。

為什麼非看不可——這套對位一定湊得出一個答案，選錯音檔也一樣。擋不下來的是
演算法，擋得下來的是使用者看到「信心值 31%」。所以這個框把量到的數字攤開，
不是問「要不要？」而已。

試算是即時的（一百多段的運算），所以踩下提前量一改就重算。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                             QLabel, QRadioButton, QSpinBox, QVBoxLayout,
                             QWidget)

from . import audio_pedal as AP
from .ui_text import tr

#: 覆蓋率低於這條就提醒使用者「這個音源聽不到踏板」。有真 CC64 的譜面實測
#: 覆蓋在 41~99%，而一首對位 99.8% 卻只聽出 6% 的曲子是音源沒有延音共鳴。
LOW_COVERAGE = 0.20


# ── 對位的說法（和「以音檔補上一小段」共用）──────────────────────────
#
# 兩個框問的是同一件事，而答案裡那幾個實測數字只該有一份。

def confidence_text(a: 'AP.Alignment') -> str:
    """信心值寫成人看得懂的一句話，低的時候用紅字。"""
    pct = a.confidence * 100
    if a.trustworthy:
        return tr('<b>%.0f%%</b> 的音符對得上（%d / %d 顆）') % (
            pct, a.matched, a.total)
    return tr(
        '<span style="color:#c0392b"><b>只有 %.0f%% 對得上</b></span>　'
        '這八成不是同一首歌的音檔。<br>'
        '（選對的時候實測都在 98%% 以上；也有可能這份譜面被吸附到格線上，'
        '那就自己判斷。）') % pct


def drift_text(a: 'AP.Alignment') -> str:
    """兩條時間軸差多少、有沒有慢慢跑掉。"""
    return tr('音檔比譜面%s %.0f ms%s') % (
        tr('早') if a.offset_ms >= 0 else tr('晚'), abs(a.offset_ms),
        (tr('，整首慢慢跑掉 %.0f ms（已用 %d 個錨點分段修正）')
         % (a.drift_ms, len(a.anchors))) if len(a.anchors) >= 2 else '')


class AudioPedalDialog(QDialog):
    """吃「譜面的起音／既有踏板」和「音檔轉出來的起音／CC64」，吐一份 Report。"""

    def __init__(self, parent: Optional[QWidget], *, chart_onsets: Sequence[float],
                 chart_end_ms: float, existing: Sequence[Sequence[float]],
                 source_onsets: Sequence[float],
                 source_spans: Sequence[Sequence[float]],
                 audio_name: str = '', notes=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('以音檔補上踏板'))
        self.setMinimumWidth(560)
        self._chart_onsets = list(chart_onsets)
        self._chart_end = float(chart_end_ms)
        self._existing = [list(s) for s in existing]
        self._source_onsets = list(source_onsets)
        self._source_spans = [list(s) for s in source_spans]
        self._notes = notes
        self._report: Optional[AP.Report] = None

        root = QVBoxLayout(self)
        lead = QLabel(tr(
            '從 %s 聽出來的延音踏板。\n'
            '只有踏板會被寫進譜面——音符、音高、力度、鍵道一個都不動。')
            % (audio_name or tr('音檔')))
        lead.setWordWrap(True)
        root.addWidget(lead)

        # 對位的結果先算一次：它不受下面任何選項影響，而且是最該先看到的。
        self._alignment = AP.align(self._chart_onsets, self._source_onsets)
        root.addWidget(self._alignment_box())

        box = QGroupBox(tr('怎麼寫進去'))
        form = QFormLayout(box)

        self._replace = QRadioButton(tr('取代現有的踏板'))
        self._fill = QRadioButton(tr('只補空隙（現有的一段都不動）'))
        self._replace.setChecked(True)
        self._fill.setEnabled(bool(self._existing))
        self._fill.setToolTip(tr(
            '給「原本的踏板是真的，但只踩了一小段」的譜面用的。\n'
            '現有的每一段原樣保留，只把它沒蓋到的空隙補上。'))
        if not self._existing:
            self._fill.setToolTip(tr('這份譜面還沒有踏板，沒有空隙可以補。'))
        form.addRow(tr('現有的 %d 段') % len(self._existing), self._replace)
        form.addRow('', self._fill)

        self._lag = QSpinBox()
        self._lag.setRange(0, 400)
        self._lag.setValue(int(AP.PRESS_LAG_MS))
        self._lag.setSingleStep(10)
        self._lag.setSuffix(tr(' ms'))
        self._lag.setToolTip(tr(
            '踩下踏板本身不發出聲音，模型要等到有音符被延長才聽得出來，'
            '所以偵測到的踩下一定偏晚。\n'
            '實測偏晚 +127ms（放開只差 +4ms，所以放開不動）。'
            '掃描 0~260ms 的最佳值是 120ms。'))
        form.addRow(tr('踩下提前'), self._lag)
        root.addWidget(box)

        self._result = QLabel()
        self._result.setWordWrap(True)
        self._result.setTextFormat(Qt.RichText)
        root.addWidget(self._result)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText(tr('寫進譜面'))
        # 對不上的時候預設按鈕給「取消」。警告還是只是警告（吸附過的譜面本來
        # 就會偏），但一路 Enter 下去不該剛好把一份錯的踏板蓋上去。
        if not self._alignment.trustworthy:
            buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        else:
            self._ok.setDefault(True)

        for widget in (self._replace, self._fill):
            widget.toggled.connect(self._refresh)
        self._lag.valueChanged.connect(self._refresh)
        self._refresh()

    # ── 版面 ──────────────────────────────────────────────────────────

    def _alignment_box(self) -> QGroupBox:
        """對位的數字。這一塊不會變，所以一次畫完就好。"""
        a = self._alignment
        box = QGroupBox(tr('對位'))
        form = QFormLayout(box)
        form.addRow(tr('像不像同一首'),
                    QLabel(self._confidence_text(a)))
        form.addRow(tr('時間差'), QLabel(drift_text(a)))
        return box

    @staticmethod
    def _confidence_text(a: 'AP.Alignment') -> str:
        return confidence_text(a)

    # ── 試算 ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._report = AP.build(
            self._chart_onsets, self._chart_end, self._existing,
            self._source_onsets, self._source_spans,
            mode=(AP.FILL if self._fill.isChecked() else AP.REPLACE),
            press_lag_ms=float(self._lag.value()))
        report = self._report

        lines: List[str] = [
            tr('寫進去會是 <b>%d 段</b>踏板，覆蓋全曲 <b>%.0f%%</b>，段長中位 %.0f ms。')
            % (len(report.spans), report.coverage * 100, report.median_ms)]
        if report.kept_existing:
            lines.append(tr('現有的 %d 段原樣保留。') % report.kept_existing)
        if report.dropped_short:
            lines.append(tr('丟掉 %d 段短於 %d ms 的雜訊。')
                         % (report.dropped_short, int(AP.MIN_SPAN_MS)))
        if (report.coverage < LOW_COVERAGE and self._alignment.trustworthy
                and not report.kept_existing):
            # 對位是好的卻幾乎沒聽到踏板＝音源本身沒有延音共鳴。實測
            # PRAGMATISM 的鋼琴分軌：信心 99.8%，但只聽出 6% 的覆蓋。
            # 有真踏板的譜面實測覆蓋在 41~99%。
            lines.append(tr(
                '<span style="color:#c0392b">對位是好的，踏板卻幾乎沒聽到。</span>　'
                '這個音源大概沒什麼延音共鳴（合成音色常這樣），'
                '改用「依和聲生成踏板」可能比較實際。'))
        peak = self._peak_voices(report.spans)
        if peak:
            # 聲音預算不是音樂考量，但踏板壓太久真的會讓鋼琴開始搶音。
            warn = peak > 160
            lines.append(tr('同時發聲尖峰 %s%d%s（遊戲的 voice pool 是 160）')
                         % ('<span style="color:#c0392b"><b>' if warn else '',
                            peak, '</b></span>' if warn else ''))
        self._result.setText('<br>'.join(lines))
        self._ok.setEnabled(bool(report.spans))

    def _peak_voices(self, spans: Sequence[Sequence[float]]) -> int:
        """同時發聲尖峰。算不出來就回 0，不要讓預覽因此開不起來。"""
        if not self._notes or not spans:
            return 0
        try:
            from generate_pedal import peak_voices
            return int(peak_voices(self._notes, spans))
        except Exception:                                   # noqa: BLE001
            return 0

    # ── 給呼叫端 ──────────────────────────────────────────────────────

    def report(self) -> Optional[AP.Report]:
        return self._report

    def mode(self) -> str:
        return AP.FILL if self._fill.isChecked() else AP.REPLACE
