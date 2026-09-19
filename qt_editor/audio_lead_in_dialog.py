"""開頭空白對話框：加／減音訊開頭的靜音，可選擇音符與小節線要不要一起移。

判定線延遲補償原本放在「播放偏移」對話框裡，那個對話框被這裡取代，所以搬過來。
"""

from __future__ import annotations

import os

from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QLabel, QSpinBox, QVBoxLayout)


class AudioLeadInDialog(QDialog):
    def __init__(self, parent=None, bpm: float = 120.0, audio_path: str = '',
                 current_total_ms: int = 0, leading_silence_ms: int = 0,
                 earliest_note_ms: int = 0, latency_ms: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle('開頭空白')
        self.setMinimumWidth(420)
        self._bpm = bpm if bpm > 0 else 120.0
        self._syncing = False
        self._earliest = int(earliest_note_ms)
        self._silence = int(leading_silence_ms)
        layout = QVBoxLayout(self)

        box = QGroupBox('音訊開頭')
        form = QFormLayout(box)
        if audio_path:
            state = []
            if current_total_ms > 0:
                state.append('目前比原檔多 %d ms 空白' % current_total_ms)
            elif current_total_ms < 0:
                state.append('目前比原檔少 %d ms' % -current_total_ms)
            state.append('開頭靜音約 %d ms' % self._silence)
            info = QLabel('%s\n%s' % (os.path.basename(audio_path), '，'.join(state)))
        else:
            info = QLabel('還沒載入音訊：只能移動音符與小節線。')
        info.setWordWrap(True)
        form.addRow(info)

        self.ms_spin = QSpinBox()
        self.ms_spin.setRange(-600_000, 600_000)
        self.ms_spin.setSingleStep(10)
        self.ms_spin.setSuffix(' ms')
        self.ms_spin.setToolTip('正數 = 在開頭加空白（聲音往後）；負數 = 剪掉開頭（聲音往前）')
        self.beat_spin = QDoubleSpinBox()
        self.beat_spin.setRange(-9999.0, 9999.0)
        self.beat_spin.setDecimals(2)
        self.beat_spin.setSingleStep(0.5)
        self.beat_spin.setSuffix(' 拍')
        self.ms_spin.valueChanged.connect(self._on_ms)
        self.beat_spin.valueChanged.connect(self._on_beat)
        form.addRow('加（＋）／減（－）', self.ms_spin)
        form.addRow('換算成拍數（BPM %.2f）' % self._bpm, self.beat_spin)

        self.move_notes = QCheckBox('音符、小節線一起移（保持和音訊對齊）')
        self.move_notes.setChecked(True)
        self.move_notes.setToolTip(
            '勾選：加 500 ms 空白，所有音符、小節線、踏板也往後 500 ms，兩邊還是對齊——用來留前奏空白。\n'
            '不勾：只移動音訊，譜面不動——用來把音訊對準譜面。')
        self.move_notes.toggled.connect(self._refresh_warning)
        form.addRow(self.move_notes)
        self.warning = QLabel('')
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet('color: #d08000;')
        form.addRow(self.warning)
        layout.addWidget(box)

        lat_box = QGroupBox('判定線延遲補償')
        lat_form = QFormLayout(lat_box)
        self.latency_spin = QSpinBox()
        self.latency_spin.setRange(0, 1000)
        self.latency_spin.setSuffix(' ms')
        self.latency_spin.setValue(int(round(latency_ms)))
        self.latency_spin.setToolTip('音效裝置出聲的延遲。判定線要在聽到聲音的那一刻碰到音符，就把這段扣掉。')
        lat_form.addRow('裝置延遲', self.latency_spin)
        layout.addWidget(lat_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_warning()

    # ── 數值 ─────────────────────────────────────────────────────────

    def _on_ms(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.beat_spin.setValue(value / (60_000.0 / self._bpm))
        self._syncing = False
        self._refresh_warning()

    def _on_beat(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.ms_spin.setValue(int(round(value * 60_000.0 / self._bpm)))
        self._syncing = False
        self._refresh_warning()

    def delta_ms(self) -> int:
        return int(self.ms_spin.value())

    def moves_notes(self) -> bool:
        return bool(self.move_notes.isChecked())

    def latency_ms(self) -> int:
        return int(self.latency_spin.value())

    def _refresh_warning(self, *_args) -> None:
        delta = self.delta_ms()
        notes = []
        if delta < 0:
            if -delta > self._silence:
                notes.append('會剪到有聲音的部分（開頭靜音只有約 %d ms）。' % self._silence)
            if self.moves_notes() and -delta > self._earliest:
                notes.append('最早的音符／小節線只能往前移 %d ms，音訊也只會剪這麼多，才不會錯開。'
                             % self._earliest)
        self.warning.setText('\n'.join(notes))
        self.warning.setVisible(bool(notes))
