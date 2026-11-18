# -*- coding: utf-8 -*-
"""「以音檔補上一小段」的對話框：採哪一段、現有的怎麼辦、怎麼擺。

轉譜已經跑完了，這一步要讓使用者在寫進譜面之前看到三件事：

1. **這份轉譜聽得準不準**（`audio_section.reproduced`）。不是信心值——信心值
   只回答「是不是同一首歌」。這裡報的是「它在你已經寫好的地方重現了幾成」，
   實測那個數字幾乎就是空白段落會採到的比例（見 `audio_section` 的對照表）。
2. **採進來會多幾顆、跳過幾顆**。採 12 顆和跳過 300 顆是兩回事。
3. **它會比你自己寫的密**，而且密多少照難度而定。那不是錯音，是難度減量。

三件都是「按下去之前才來得及改主意」的資訊，所以全部攤在畫面上，不是只問
「要不要？」。試算是即時的（幾千顆的過濾），選項一改就重算。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from . import audio_pedal as AP
from . import audio_section as AS
from .audio_pedal_dialog import confidence_text, drift_text
from .ui_text import tr

#: 重現率低於這條就用紅字。實測五首對得上的在 90~100%，拿到翻奏版音檔的那首
#: 是 34%、拿混音轉的是 14%——中間空了五十個百分點，門檻放哪裡都分得開。
LOW_REPRODUCED = 0.60

#: 鍵道要「照現有排法」至少得學到幾顆。少於這個就只剩那條迴歸直線，和照音高
#: 平鋪沒什麼差別（見 `arrange_by_example.PitchLine`）。
MIN_LANE_SAMPLES = 40

SCOPE_SELECTION, SCOPE_MEASURES = range(2)


class AudioSectionDialog(QDialog):
    """吃「譜面 ＋ 轉譜結果 ＋ 對位」，吐一個 `audio_section.Pick` 和幾個選項。"""

    def __init__(self, parent: Optional[QWidget], *, model: Any,
                 source: Sequence[AS.Note], alignment: AP.Alignment,
                 selection: Optional[Tuple[float, float]] = None,
                 audio_name: str = '', lane_samples: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('以音檔補上一小段'))
        self.setMinimumWidth(620)
        self._model = model
        self._source = list(source)
        self._alignment = alignment
        self._selection = selection
        self._lane_samples = int(lane_samples)
        self._pick: Optional[AS.Pick] = None
        # 建構途中 setValue 會當場觸發訊號，而 `_refresh` 要用的東西還沒建好。
        # PyQt 的訊號處理裡丟例外是整個行程直接死掉，不是 traceback。
        self._ready = False

        self._chart = [(float(n.start), int(n.pitch)) for n in model.notes_tree
                       if n.pitch is not None and not getattr(n, 'hidden', False)]
        self._hit, self._total = AS.reproduced(
            self._chart, AS.mapped(self._source, alignment))

        root = QVBoxLayout(self)
        lead = QLabel(tr(
            '從 %s 聽出來的 %d 顆音，只有你選的那一段會放進譜面。\n'
            '範圍外的音符、鍵道、踏板一個都不動。')
            % (audio_name or tr('音檔'), len(self._source)))
        lead.setWordWrap(True)
        root.addWidget(lead)
        root.addWidget(self._quality_box())
        root.addWidget(self._scope_box())
        root.addWidget(self._existing_box())
        root.addWidget(self._placing_box())

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.RichText)
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText(tr('放進譜面'))
        # 聽不準的時候預設按鈕給「取消」：一路 Enter 下去不該剛好把一段雜音
        # 倒進譜面裡。
        if self._reproduced_ratio < LOW_REPRODUCED or not alignment.trustworthy:
            buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        else:
            self._ok.setDefault(True)

        self._ready = True
        self._sync()

    # ── 版面 ──────────────────────────────────────────────────────────

    @property
    def _reproduced_ratio(self) -> float:
        return self._hit / float(self._total) if self._total else 0.0

    def _quality_box(self) -> QGroupBox:
        """這份轉譜值不值得用。不會隨選項變，一次畫完。"""
        box = QGroupBox(tr('這份轉譜聽得準不準'))
        form = QFormLayout(box)
        form.addRow(tr('像不像同一首'), QLabel(confidence_text(self._alignment)))

        ratio = self._reproduced_ratio
        text = tr('<b>%.0f%%</b>（%d / %d 顆）') % (ratio * 100, self._hit, self._total)
        if ratio < LOW_REPRODUCED:
            text = (tr('<span style="color:#c0392b"><b>只有 %.0f%%</b></span>'
                       '（%d / %d 顆）') % (ratio * 100, self._hit, self._total))
        label = QLabel(text)
        label.setTextFormat(Qt.RichText)
        form.addRow(tr('重現你寫好的'), label)

        note = QLabel(tr(
            '實測這個數字就是空白段落會採到的比例（五首：100%→97、99%→98、'
            '98%→97、90%→89、34%→33）。\n'
            '低的話八成是拿到別的版本的音檔，或者這份是從混音轉的——'
            '先用鋼琴分軌重轉一次會差很多。'))
        note.setWordWrap(True)
        note.setStyleSheet('color: #555; font-size: 11px;')
        form.addRow('', note)
        form.addRow(tr('時間差'), QLabel(drift_text(self._alignment)))
        return box

    def _scope_box(self) -> QGroupBox:
        box = QGroupBox(tr('採哪一段'))
        inner = QVBoxLayout(box)
        self._scope = QButtonGroup(self)
        self._rb_sel = QRadioButton(tr('選取音符涵蓋的範圍'))
        self._rb_meas = QRadioButton(tr('小節範圍'))
        for index, button in ((SCOPE_SELECTION, self._rb_sel),
                              (SCOPE_MEASURES, self._rb_meas)):
            self._scope.addButton(button, index)
            inner.addWidget(button)
            button.toggled.connect(lambda _on: self._sync())
        self._rb_sel.setEnabled(self._selection is not None)
        if self._selection is None:
            self._rb_sel.setToolTip(tr('沒有選取任何音符。'))
        (self._rb_sel if self._selection is not None else self._rb_meas).setChecked(True)

        row = QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QLabel(tr('第')))
        total = max(1, int(self._model.count_measures()))
        self._first = QSpinBox()
        self._first.setRange(1, total)
        self._last = QSpinBox()
        self._last.setRange(1, total)
        self._last.setValue(min(total, 8))
        if self._selection is not None:
            start = self._model.get_measure_at_ms(self._selection[0]) + 1
            end = self._model.get_measure_at_ms(self._selection[1]) + 1
            self._first.setValue(max(1, min(total, start)))
            self._last.setValue(max(1, min(total, end)))
        self._first.valueChanged.connect(lambda v: (self._last.setMinimum(v),
                                                    self._refresh()))
        self._last.valueChanged.connect(lambda _v: self._refresh())
        row.addWidget(self._first)
        row.addWidget(QLabel(tr('到第')))
        row.addWidget(self._last)
        row.addWidget(QLabel(tr('小節')))
        row.addStretch(1)
        inner.addLayout(row)

        self._where = QLabel()
        self._where.setWordWrap(True)
        self._where.setStyleSheet('color: #555; font-size: 11px;')
        inner.addWidget(self._where)
        return box

    def _existing_box(self) -> QGroupBox:
        self._existing_group = QGroupBox(tr('這一段現有的音符'))
        inner = QVBoxLayout(self._existing_group)
        self._keep = QRadioButton(tr('留著，只補音檔多出來的'))
        self._keep.setToolTip(tr('同一刻、同音高的不放第二次（容差 %d ms）。')
                              % int(AS.MATCH_MS))
        self._wipe = QRadioButton(tr('整段換成音檔聽出來的'))
        self._keep.setChecked(True)
        for button in (self._keep, self._wipe):
            inner.addWidget(button)
            button.toggled.connect(lambda _on: self._refresh())
        return self._existing_group

    def _placing_box(self) -> QGroupBox:
        box = QGroupBox(tr('怎麼擺'))
        inner = QVBoxLayout(box)
        self._by_example = QCheckBox(tr('鍵道照這份譜面現有的排法'))
        self._by_example.setChecked(self._lane_samples >= MIN_LANE_SAMPLES)
        self._by_example.setEnabled(self._lane_samples >= MIN_LANE_SAMPLES)
        self._by_example.setToolTip(tr(
            '從你已經排好的音符學「這個音高放在哪一格」，再照冠音的走向排這一段。\n'
            '實測和你自己在那一段寫的鍵道比：完全相同 9.8% → 30.2%、'
            '±1 格 37.8% → 68.8%。\n'
            '關掉就照音高平鋪（和匯入 MIDI 的預設一樣）。'))
        if self._lane_samples < MIN_LANE_SAMPLES:
            self._by_example.setToolTip(tr(
                '這份譜面排好的音符只有 %d 顆，學不出音高和鍵道的對應。')
                % self._lane_samples)
        inner.addWidget(self._by_example)

        self._split = QCheckBox(tr('自動分左右手'))
        self._split.setChecked(True)
        self._split.setToolTip(tr(
            'AI 轉出來的是單軌，沒有左右手。關掉的話採進來的全部標成無主音'
            '（畫面上是綠色），之後可以自己指定。'))
        inner.addWidget(self._split)
        return box

    # ── 狀態 ──────────────────────────────────────────────────────────

    def scope(self) -> int:
        return int(self._scope.checkedId())

    def measure_range(self) -> Tuple[int, int]:
        return int(self._first.value()), int(self._last.value())

    def range_ms(self) -> Tuple[float, float]:
        """採的範圍（譜面毫秒）。小節模式下是整小節的邊界。"""
        if self.scope() == SCOPE_SELECTION and self._selection is not None:
            return float(self._selection[0]), float(self._selection[1])
        first, last = self.measure_range()
        lo, _end = self._model.get_measure_time_range(first - 1)
        _start, hi = self._model.get_measure_time_range(last - 1)
        return float(lo or 0), float(hi if hi is not None else (lo or 0))

    def replace(self) -> bool:
        return bool(self._wipe.isChecked())

    def by_example(self) -> bool:
        return bool(self._by_example.isChecked() and self._by_example.isEnabled())

    def split_hands(self) -> bool:
        return bool(self._split.isChecked())

    def picked(self) -> AS.Pick:
        """最後一次試算的結果（按下「放進譜面」之後拿這一份去用）。"""
        if self._pick is None:
            self._refresh()
        return self._pick or AS.Pick()

    # ── 試算 ──────────────────────────────────────────────────────────

    def _sync(self) -> None:
        """哪幾格能按。小節的兩個數字只在小節模式下有用。"""
        if not self._ready:
            return
        measures = self.scope() == SCOPE_MEASURES
        for widget in (self._first, self._last):
            widget.setEnabled(measures)
        self._refresh()

    def _refresh(self) -> None:
        if not self._ready:
            return
        lo, hi = self.range_ms()
        existing = [(t, p) for t, p in self._chart if lo <= t < hi]
        self._existing_group.setTitle(
            tr('這一段現有的 %d 顆音符') % len(existing))
        self._wipe.setText(tr('整段換成音檔聽出來的（刪掉現有的 %d 顆）')
                           % len(existing))
        self._keep.setEnabled(bool(existing))
        if not existing and self._keep.isChecked():
            self._keep.setChecked(True)      # 空的時候兩種一樣，維持預設
        self._pick = AS.pick(self._source, self._alignment, lo, hi,
                             avoid=() if self.replace() else existing)

        first, last = (self._model.get_measure_at_ms(lo) + 1,
                       self._model.get_measure_at_ms(max(lo, hi - 1)) + 1)
        self._where.setText(tr('第 %d~%d 小節　%.1f ~ %.1f 秒　音檔這一段有 %d 顆')
                            % (first, last, lo / 1000.0, hi / 1000.0,
                               self._pick.inside))

        got = self._pick
        skipped = []
        if got.duplicate:
            skipped.append(tr('%d 顆你已經寫了') % got.duplicate)
        if got.short:
            skipped.append(tr('%d 顆太短（< %d ms）') % (got.short, int(AS.MIN_MS)))
        lines = [tr('<b>會放進 %d 顆</b>%s。') % (
            len(got), tr('，跳過 ') + tr('、').join(skipped) if skipped else '')]
        if got.clipped:
            lines.append(tr('%d 顆的尾巴伸出範圍外，切在邊界上。') % got.clipped)
        lines.append(tr(
            '採進來的是完整的鋼琴演奏，會比你自己寫的密——實測同一批段落，'
            'Expert 難度有 64% 是你也會寫的音，Hard 38%、Normal 31%。'
            '多的那些不是錯音，是你刻意省掉的內聲部。'))
        if not len(got):
            lines = [tr('<b>這一段採不到任何音符。</b>'
                        '音檔的這一段可能沒有鋼琴聲，或者整首已經結束了。')]
        self._summary.setText('<br>'.join(lines))
        self._ok.setEnabled(bool(len(got)))
