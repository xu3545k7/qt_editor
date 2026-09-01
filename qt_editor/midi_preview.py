"""SoundFont-backed MIDI note preview rendering.

The renderer talks directly to the bundled FluidSynth DLL through ``ctypes``.
It deliberately renders PCM without opening another audio device so the
result can be mixed with the song and share the exact same sample clock.
"""

from __future__ import annotations

import array
import ctypes
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import (
    SOFT_NOTE_TYPE,
    explode_trill,
    game_lane_index_to_midi_pitch,
    note_is_long,
    note_is_staccato,
    note_is_trill,
)


# (檔名, preset 編號)。preset 要跟著檔名走——同一個編號在不同音源是不同東西，
# Nice-Steinway 的 1 是「Bright Steinway」，而舊音源只有 preset 0。遊戲端的取樣
# 庫也是用這一組烤的（`render_piano_samples.py --preset "Bright Steinway"`），
# 兩邊要一致，不然製譜器聽到的和遊戲播的是兩種音色。
DEFAULT_SOUNDFONT = ("Nice-Steinway-v3.8.sf2", 1)
FALLBACK_SOUNDFONT = ("UprightPianoKW-20220221.sf2", 0)
DEFAULT_SOUNDFONT_NAME = DEFAULT_SOUNDFONT[0]
_FLUID_OK = 0
_CC_SUSTAIN = 64          # 延音踏板的 MIDI 控制器編號
METRONOME_CHANNEL = 15    # 小節拍點借用的頻道，踏板不套用在它身上
_FLUID_INTERP_7THORDER = 7

# ── 輸出增益 ────────────────────────────────────────────────────────────
# 內建音源算出來的鋼琴普遍偏小聲：實測 400ms 區塊 RMS 的中位數只有 -25.9
# dBFS，而一般母帶處理過的歌曲在 -12~-14，並在一起就是「鋼琴幾乎聽不到」。
#
# 不能直接把音量乘上去——峰值本來就在 -2.0 dBFS，乘完會削頂。但實測整份
# 渲染只有 0.007% 的取樣超過 -6 dBFS，也就是說「大部分很小聲、少數尖峰很
# 高」（波峰因數 24 dB）。所以做法是：增益之後只把接近天花板的部分用 tanh
# 壓成軟膝，其餘線性通過。
#
# 實測 gain=2.5 / knee=0.6：中位數 -25.9 → -18.3 dBFS（+7.6 dB），峰值
# -0.01 dBFS、削頂 0 個取樣，而且 p10~p90 的動態範圍仍是 15.7 dB —— 和原始
# 完全一樣，力度與強弱曲線的表情沒有被壓掉。
OUTPUT_GAIN = 2.5         # 線性倍率（≈ +8 dB）
OUTPUT_KNEE = 0.6         # 超過滿刻度這個比例才開始壓，以下線性

_gain_table: Optional[Tuple[Tuple[float, float], List[int]]] = None


def _build_gain_table(gain: float, knee: float) -> List[int]:
    """int16 → int16 的查表：先乘增益，接近天花板的部分用 tanh 收成軟膝。

    用查表是因為這裡沒有 numpy，而純 Python 逐取樣算 tanh 對一首三分鐘的曲子
    是上千萬次浮點運算。查表只要建一次 65536 筆（約 0.02 秒），之後每次渲染
    都是一個 `map`，實測 145 秒的音訊只要 0.5 秒。
    """
    knee = max(0.0, min(0.99, float(knee)))
    table: List[int] = [0] * 65536
    for i in range(65536):
        value = i - 65536 if i >= 32768 else i
        y = (value / 32768.0) * float(gain)
        sign = 1.0 if y >= 0 else -1.0
        mag = abs(y)
        if mag > knee:
            mag = knee + (1.0 - knee) * math.tanh((mag - knee) / (1.0 - knee))
        table[i] = max(-32768, min(32767, int(round(sign * mag * 32767))))
    return table


def apply_output_gain(pcm: bytes, gain: float = OUTPUT_GAIN,
                      knee: float = OUTPUT_KNEE) -> bytes:
    """把渲染出來的 16-bit PCM 套上輸出增益與軟膝限幅。"""
    if not pcm or gain == 1.0:
        return pcm
    global _gain_table
    key = (float(gain), float(knee))
    if _gain_table is None or _gain_table[0] != key:
        _gain_table = (key, _build_gain_table(gain, knee))
    table = _gain_table[1]
    samples = array.array('h')
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    return array.array('h', map(table.__getitem__, samples)).tobytes()


class MidiPreviewError(RuntimeError):
    """Raised when the bundled synthesizer cannot render a preview."""


@dataclass(frozen=True)
class MidiPreviewNote:
    start_ms: float
    end_ms: float
    pitch: int
    velocity: int = 100
    channel: int = 0


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def default_soundfont() -> Tuple[Path, int]:
    """(音源路徑, preset)。找不到主音源就退回舊的那份，preset 跟著換。"""
    root = _runtime_root()
    for name, preset in (DEFAULT_SOUNDFONT, FALLBACK_SOUNDFONT):
        for folder in ("soundfonts", "UprightPianoKW-SF2-20220221"):
            candidate = root / folder / name
            if candidate.is_file():
                return (candidate, preset)
    return (root / "soundfonts" / DEFAULT_SOUNDFONT[0], DEFAULT_SOUNDFONT[1])


def default_soundfont_path() -> Path:
    return default_soundfont()[0]


def default_fluidsynth_dll_path() -> Path:
    root = _runtime_root()
    candidates = [
        root / "fluidsynth" / "libfluidsynth-3.dll",
        root / "vendor" / "fluidsynth" / "bin" / "libfluidsynth-3.dll",
        (
            root
            / "vendor"
            / "fluidsynth"
            / "fluidsynth-v2.5.7-win10-x64-cpp11"
            / "bin"
            / "libfluidsynth-3.dll"
        ),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _note_default_velocity(model, chart_note) -> int:
    """力度優先用音符自己的 <velocity>；XML 沒寫就退回同 track 的平均值。

    Nostalgia 的 XML 可能完全沒有 velocity 欄位（純遊戲譜），也可能是從
    MIDI 匯入而帶著每顆音符的力度。兩種都要能播。
    """
    raw = getattr(chart_note, "velocity", None)
    if raw is not None:
        return int(raw)
    getter = getattr(model, "_default_midi_velocity_for_track", None)
    if callable(getter):
        try:
            return int(getter(getattr(chart_note, "track", None)))
        except Exception:
            pass
    return 100


def _note_pitch(chart_note) -> int:
    """音高優先用 <scale_piano>（已轉成 MIDI），否則由鍵位推回音高。"""
    raw_pitch = getattr(chart_note, "pitch", None)
    if raw_pitch is None:
        low = int(getattr(chart_note, "min_key", 0))
        high = int(getattr(chart_note, "max_key", low))
        raw_pitch = game_lane_index_to_midi_pitch((low + high) // 2)
    return max(0, min(127, int(raw_pitch)))


def _expand_source(chart_note) -> List:
    """Trill 展開成各個子音符，其餘原樣回傳。

    XML 的 trill 把每一格存在 `sub_note` 裡（含各自的 scale_piano 與
    velocity），展開後才聽得到真正的顫音，而不是一顆長音。
    """
    if note_is_trill(int(getattr(chart_note, "note_type", 0))):
        try:
            cells = explode_trill(chart_note)
        except Exception:
            cells = []
        if cells:
            return cells
    return [chart_note]


def build_chart_midi_notes(
    model,
    *,
    source_notes: Optional[Iterable] = None,
    range_ms: Optional[Tuple[float, float]] = None,
    enable_right: bool = True,
    enable_left: bool = True,
    enable_beat: bool = False,
    note_length_ms: Optional[int] = None,
    min_length_ms: float = 30.0,
    expressive: bool = False,
    real_pedal: bool = False,
) -> List[MidiPreviewNote]:
    """把譜面音符轉成可播放的 MIDI 事件。

    `note_length_ms=None` 表示保留音符本身的長度（start→end），用在「播放
    MIDI」這種要聽原本時值的場合；給定數字則會把每顆音符裁短，供密集譜面
    的打擊預覽使用。

    `range_ms` 以「音符起點落在範圍內」為準過濾，所以範圍外開始、延伸進來
    的長音不會突然發聲。

    `expressive=True` 時，會把 XML 裡的表情資訊也帶進播放：trill 展開成
    子音符、soft 減力度、staccato 縮短發聲、gate_time_msec 當實際按鍵長度。

    `real_pedal=True` 表示踏板改由合成器的 CC64 表現（見
    `MidiPreviewSynth.render` 的 `pedal_spans`），這裡就**不再**把音符尾巴
    延長到放開踏板的時刻——兩個一起做會變成雙重延音。
    """
    raw_src = getattr(model, "notes_tree", ()) if source_notes is None else source_notes
    if expressive:
        src = [cell for n in raw_src for cell in _expand_source(n)]
    else:
        src = raw_src
    length_ms = None if note_length_ms is None else max(30, int(note_length_ms))
    lo, hi = (None, None) if range_ms is None else (float(range_ms[0]), float(range_ms[1]))
    notes: List[MidiPreviewNote] = []

    for chart_note in src:
        hand = int(getattr(chart_note, "hand", 0))
        if hand == 0 and not enable_right:
            continue
        if hand == 1 and not enable_left:
            continue

        start = float(getattr(chart_note, "start", 0))
        if lo is not None and not (lo <= start < hi):
            continue

        chart_end = float(getattr(chart_note, "end", start))
        gate_ms = max(0.0, float(getattr(chart_note, "gate", 0) or 0))
        note_type = int(getattr(chart_note, "note_type", 0))
        if length_ms is None or note_is_long(note_type):
            # Hold notes sustain to their chart tail even when ordinary
            # preview notes use a short fixed duration. ``gate`` covers MIDI
            # and XML inputs whose end field has not been normalized.
            chart_end = max(chart_end, start + gate_ms)
            end = chart_end if chart_end > start else start + min_length_ms
        else:
            end = min(chart_end, start + length_ms) if chart_end > start else start + length_ms
        end = max(start + float(min_length_ms), end)

        raw_channel = getattr(chart_note, "channel", None)
        channel = 0 if raw_channel is None else int(raw_channel)
        velocity = _note_default_velocity(model, chart_note)

        if expressive:
            # XML 沒有 velocity 欄位時，note_type 就是唯一的力度線索：
            # soft 彈得輕，staccato 是斷奏（縮短發聲、稍微加重）。
            if note_type == SOFT_NOTE_TYPE:
                velocity = int(round(velocity * 0.62))
            elif note_is_staccato(note_type):
                velocity = int(round(velocity * 1.08))
                staccato_end = start + max(
                    float(min_length_ms),
                    (gate_ms if gate_ms > 0 else (end - start)) * 0.35,
                )
                end = min(end, staccato_end)
            elif gate_ms > 0 and not note_is_long(note_type):
                # gate_time_msec 才是實際按下去的長度，比 end_timing 更貼近演奏
                end = min(end, max(start + float(min_length_ms), start + gate_ms))

        # 延音踏板：放開琴鍵時踏板還踩著的話，殘響延續到放開踏板為止。
        # 判斷點是音符自己的收音時刻，不是起音——這才是踏板接手的那一刻。
        # 用 gate/staccato 修正過的 `end` 去查，不是 chart_note.end——斷奏
        # 明明提早放鍵，卻按原始音長去接踏板的話就白縮短了。
        release = None if real_pedal else getattr(model, "pedal_release_after", None)
        if callable(release) and getattr(model, "pedal_spans", None):
            try:
                up = release(end)
                if up is not None:
                    end = max(end, float(up))
            except Exception:
                pass

        notes.append(
            MidiPreviewNote(
                start_ms=start,
                end_ms=end,
                pitch=_note_pitch(chart_note),
                velocity=max(1, min(127, velocity)),
                channel=max(0, min(15, channel)),
            )
        )

    if enable_beat:
        for _beat_index, beat_ms in getattr(model, "get_beat_entries")():
            start = float(beat_ms)
            if lo is not None and not (lo <= start < hi):
                continue
            notes.append(
                MidiPreviewNote(
                    start_ms=start,
                    end_ms=start + 55.0,
                    pitch=84,
                    velocity=42,
                    channel=METRONOME_CHANNEL,
                )
            )

    notes.sort(key=lambda n: (n.start_ms, n.pitch, n.channel, n.end_ms))
    return _trim_overlaps(notes)


def _trim_overlaps(notes: List[MidiPreviewNote]) -> List[MidiPreviewNote]:
    """Prevent overlapping instances of one channel/pitch from sharing a
    note-off. Chords remain untouched because their pitches differ."""
    next_start: dict[Tuple[int, int], float] = {}
    trimmed_reversed: List[MidiPreviewNote] = []
    for note in reversed(notes):
        key = (note.channel, note.pitch)
        following = next_start.get(key)
        end = note.end_ms
        if following is not None and end >= following:
            end = max(note.start_ms + 1.0, following - 1.0)
        trimmed_reversed.append(
            MidiPreviewNote(
                note.start_ms,
                end,
                note.pitch,
                note.velocity,
                note.channel,
            )
        )
        next_start[key] = note.start_ms

    return list(reversed(trimmed_reversed))


def pedal_spans_in_range(
    model,
    start_ms: float,
    end_ms: float,
    offset_ms: float = 0.0,
) -> List[Tuple[float, float]]:
    """取出 [start_ms, end_ms) 內的踏板區間，裁到範圍內並套用時間位移。

    `offset_ms` 給「疊在歌曲音訊上的預覽」用——那條路的音訊時間軸和譜面差一個
    播放偏移，踏板也必須跟著移，否則踏板會踩在錯的地方。

    從範圍中間開始播時，一段本來就已經踩著的踏板會被裁成「從 start_ms 起就是
    踩下的狀態」，聽起來才和從頭播一致。
    """
    spans = getattr(model, "pedal_spans", None) or []
    lo, hi = float(start_ms), float(end_ms)
    out: List[Tuple[float, float]] = []
    for span in spans:
        try:
            s, e = float(span[0]) + offset_ms, float(span[1]) + offset_ms
        except (TypeError, ValueError, IndexError):
            continue
        s, e = max(s, lo), min(e, hi)
        if e - s >= 1.0:
            out.append((s, e))
    return out


def build_preview_notes(
    model,
    *,
    enable_right: bool = True,
    enable_left: bool = True,
    enable_beat: bool = True,
    note_length_ms: int = 220,
    real_pedal: bool = False,
) -> List[MidiPreviewNote]:
    """Convert the current chart to pitched preview events.

    Preview notes are intentionally short so dense rhythm-game charts remain
    readable. Repeated notes of the same pitch/channel are trimmed before the
    next onset to prevent an older note-off from cutting off the new note.
    """
    return build_chart_midi_notes(
        model,
        enable_right=enable_right,
        enable_left=enable_left,
        enable_beat=enable_beat,
        note_length_ms=note_length_ms,
        real_pedal=real_pedal,
    )


class MidiPreviewSynth:
    """Reusable FluidSynth instance that renders interleaved stereo PCM16."""

    def __init__(
        self,
        sample_rate: int,
        *,
        soundfont_path: Optional[os.PathLike[str] | str] = None,
        dll_path: Optional[os.PathLike[str] | str] = None,
        bank: int = 0,
        preset: Optional[int] = None,
    ) -> None:
        self.sample_rate = max(8_000, int(sample_rate))
        # 一份音源可以有好幾種調音掛在不同的 preset 上（Nice-Steinway 的 0~3 是
        # Steinway Grand / Bright / Mellow / Dark，用的是同一批錄音）。選錯的話
        # 製譜器聽到的和烤進遊戲的會是兩種音色。
        self.bank = max(0, int(bank))
        if soundfont_path is None:
            found, default_preset = default_soundfont()
            self.soundfont_path = found.resolve()
        else:
            # 明講了音源卻沒講 preset，就用 0：其他編號在別份音源是什麼東西
            # 沒人知道，猜不如不猜。
            self.soundfont_path = Path(soundfont_path).resolve()
            default_preset = 0
        self.preset = max(0, int(default_preset if preset is None else preset))
        self.dll_path = Path(dll_path or default_fluidsynth_dll_path()).resolve()
        self._dll_dir_handle = None
        self._lib = None
        self._settings = None
        self._synth = None
        self._sfid = -1

        if not self.soundfont_path.is_file():
            raise MidiPreviewError(f"SoundFont not found: {self.soundfont_path}")
        if not self.dll_path.is_file():
            raise MidiPreviewError(f"FluidSynth DLL not found: {self.dll_path}")

        try:
            if hasattr(os, "add_dll_directory"):
                self._dll_dir_handle = os.add_dll_directory(str(self.dll_path.parent))
            self._lib = ctypes.CDLL(str(self.dll_path))
            self._bind_api()
            self._create_synth()
        except Exception as exc:
            self.close()
            if isinstance(exc, MidiPreviewError):
                raise
            raise MidiPreviewError(f"Unable to initialize FluidSynth: {exc}") from exc

    @property
    def is_ready(self) -> bool:
        return bool(self._synth and self._sfid >= 0)

    def _bind_api(self) -> None:
        lib = self._lib
        if lib is None:
            raise MidiPreviewError("FluidSynth library is not loaded")

        void_p = ctypes.c_void_p
        lib.new_fluid_settings.argtypes = []
        lib.new_fluid_settings.restype = void_p
        lib.delete_fluid_settings.argtypes = [void_p]
        lib.delete_fluid_settings.restype = None
        lib.fluid_settings_setnum.argtypes = [void_p, ctypes.c_char_p, ctypes.c_double]
        lib.fluid_settings_setnum.restype = ctypes.c_int
        lib.fluid_settings_setint.argtypes = [void_p, ctypes.c_char_p, ctypes.c_int]
        lib.fluid_settings_setint.restype = ctypes.c_int

        lib.new_fluid_synth.argtypes = [void_p]
        lib.new_fluid_synth.restype = void_p
        lib.delete_fluid_synth.argtypes = [void_p]
        lib.delete_fluid_synth.restype = None
        lib.fluid_synth_sfload.argtypes = [void_p, ctypes.c_char_p, ctypes.c_int]
        lib.fluid_synth_sfload.restype = ctypes.c_int
        lib.fluid_synth_program_select.argtypes = [
            void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.fluid_synth_program_select.restype = ctypes.c_int
        lib.fluid_synth_noteon.argtypes = [void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_noteon.restype = ctypes.c_int
        lib.fluid_synth_noteoff.argtypes = [void_p, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_noteoff.restype = ctypes.c_int
        lib.fluid_synth_cc.argtypes = [void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_cc.restype = ctypes.c_int
        lib.fluid_synth_system_reset.argtypes = [void_p]
        lib.fluid_synth_system_reset.restype = ctypes.c_int
        lib.fluid_synth_write_s16.argtypes = [
            void_p,
            ctypes.c_int,
            void_p,
            ctypes.c_int,
            ctypes.c_int,
            void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.fluid_synth_write_s16.restype = ctypes.c_int

        if hasattr(lib, "fluid_synth_set_interp_method"):
            lib.fluid_synth_set_interp_method.argtypes = [void_p, ctypes.c_int, ctypes.c_int]
            lib.fluid_synth_set_interp_method.restype = ctypes.c_int

    def _create_synth(self) -> None:
        lib = self._lib
        if lib is None:
            raise MidiPreviewError("FluidSynth library is not loaded")

        self._settings = lib.new_fluid_settings()
        if not self._settings:
            raise MidiPreviewError("Unable to create FluidSynth settings")

        lib.fluid_settings_setnum(
            self._settings, b"synth.sample-rate", float(self.sample_rate)
        )
        lib.fluid_settings_setnum(self._settings, b"synth.gain", 0.45)
        lib.fluid_settings_setint(self._settings, b"synth.polyphony", 256)
        lib.fluid_settings_setint(self._settings, b"synth.reverb.active", 0)
        lib.fluid_settings_setint(self._settings, b"synth.chorus.active", 0)

        self._synth = lib.new_fluid_synth(self._settings)
        if not self._synth:
            raise MidiPreviewError("Unable to create FluidSynth synthesizer")

        self._sfid = lib.fluid_synth_sfload(
            self._synth, os.fsencode(self.soundfont_path), 0
        )
        if self._sfid < 0:
            raise MidiPreviewError(f"Unable to load SoundFont: {self.soundfont_path}")

        if hasattr(lib, "fluid_synth_set_interp_method"):
            lib.fluid_synth_set_interp_method(
                self._synth, -1, _FLUID_INTERP_7THORDER
            )
        self._reset_channels()

    def _reset_channels(self) -> None:
        lib = self._lib
        if lib is None or not self._synth:
            return
        lib.fluid_synth_system_reset(self._synth)
        for channel in range(16):
            # Force every channel, including MIDI channel 10, to the piano
            # preset rather than FluidSynth's default percussion bank.
            lib.fluid_synth_program_select(
                self._synth, channel, self._sfid, self.bank, self.preset
            )
            # 踏板歸零：上一次 render 若在踏板踩著時結束，狀態會留在
            # 合成器裡，下一段開頭整段都被延音。
            lib.fluid_synth_cc(self._synth, channel, _CC_SUSTAIN, 0)

    # 補償增益在 `apply_output_gain`，不在 synth.gain：FluidSynth 自己的輸出級
    # 是硬切的，把 synth.gain 拉到 0.85 就開始削掉尖峰（實測 552 個取樣）。
    # 先讓它乾淨地算完，再在 PCM 上做「增益 + 軟膝」，一個取樣都不會削到。

    def render(
        self,
        notes: Sequence[MidiPreviewNote] | Iterable[MidiPreviewNote],
        start_ms: float,
        end_ms: float,
        pedal_spans: Sequence[Tuple[float, float]] = (),
    ) -> bytes:
        """把音符渲染成 PCM。

        `pedal_spans` 是 [(踩下 ms, 放開 ms), ...]，會送出真正的 CC64 給合成器，
        而不是把音符尾巴拉長。差別在同音重複與共鳴：真踏板下再彈同一個音，前一
        個音的殘響會繼續、新音疊上去；拉長音符的話反而會被去重疊那一關裁掉。
        """
        if not self.is_ready or self._lib is None:
            raise MidiPreviewError("FluidSynth is not ready")
        if end_ms <= start_ms:
            return b""

        self._reset_channels()
        total_frames = max(
            0, int(round((float(end_ms) - float(start_ms)) * self.sample_rate / 1000.0))
        )

        def to_frame(ms: float) -> int:
            return max(0, min(total_frames, int(round(
                (float(ms) - float(start_ms)) * self.sample_rate / 1000.0))))

        # kind: 0=note_off, 1=控制器(CC), 2=note_on。同一個取樣點上依這個順序送：
        # 先放掉該放的音，再套用踏板狀態，最後才觸發新音。踏板踩下要早於同時刻
        # 的 note_on（那些音才會被延音接住），踏板放開要晚於同時刻的 note_off。
        events: List[Tuple[int, int, int, int, int]] = []
        pedal_channels = sorted({
            max(0, min(15, int(n.channel))) for n in notes
            if max(0, min(15, int(n.channel))) != METRONOME_CHANNEL
        })
        for span_start, span_end in pedal_spans or ():
            if float(span_end) <= start_ms or float(span_start) >= end_ms:
                continue
            down = to_frame(max(float(span_start), float(start_ms)))
            up = to_frame(min(float(span_end), float(end_ms)))
            if up <= down:
                continue
            for ch in pedal_channels:
                events.append((down, 1, ch, _CC_SUSTAIN, 127))
                events.append((up, 1, ch, _CC_SUSTAIN, 0))

        for note in notes:
            if note.end_ms <= start_ms or note.start_ms >= end_ms:
                continue
            on_ms = max(float(start_ms), float(note.start_ms))
            off_ms = min(float(end_ms), max(on_ms + 1.0, float(note.end_ms)))
            on_frame = max(
                0, int(round((on_ms - float(start_ms)) * self.sample_rate / 1000.0))
            )
            off_frame = min(
                total_frames,
                max(
                    on_frame + 1,
                    int(
                        round(
                            (off_ms - float(start_ms))
                            * self.sample_rate
                            / 1000.0
                        )
                    ),
                ),
            )
            channel = max(0, min(15, int(note.channel)))
            pitch = max(0, min(127, int(note.pitch)))
            velocity = max(1, min(127, int(note.velocity)))
            events.append((on_frame, 2, channel, pitch, velocity))
            if off_frame < total_frames:
                events.append((off_frame, 0, channel, pitch, 0))

        # Note-offs precede CC, which precede note-ons at the same sample.
        events.sort(key=lambda item: (item[0], item[1]))
        output = bytearray()
        current_frame = 0
        event_index = 0

        def render_frames(frame_count: int) -> None:
            remaining = int(frame_count)
            while remaining > 0:
                count = min(4096, remaining)
                buffer = (ctypes.c_int16 * (count * 2))()
                result = self._lib.fluid_synth_write_s16(
                    self._synth,
                    count,
                    buffer,
                    0,
                    2,
                    buffer,
                    1,
                    2,
                )
                if result != _FLUID_OK:
                    raise MidiPreviewError("FluidSynth failed while rendering PCM")
                output.extend(ctypes.string_at(buffer, count * 4))
                remaining -= count

        while event_index < len(events):
            event_frame = min(total_frames, events[event_index][0])
            if event_frame > current_frame:
                render_frames(event_frame - current_frame)
                current_frame = event_frame

            while (
                event_index < len(events)
                and events[event_index][0] == event_frame
            ):
                _frame, kind, channel, arg_a, arg_b = events[event_index]
                if kind == 2:
                    self._lib.fluid_synth_noteon(
                        self._synth, channel, arg_a, arg_b
                    )
                elif kind == 1:
                    self._lib.fluid_synth_cc(self._synth, channel, arg_a, arg_b)
                else:
                    self._lib.fluid_synth_noteoff(self._synth, channel, arg_a)
                event_index += 1

        if current_frame < total_frames:
            render_frames(total_frames - current_frame)

        return apply_output_gain(bytes(output))

    def close(self) -> None:
        lib = self._lib
        if lib is not None and self._synth:
            try:
                lib.delete_fluid_synth(self._synth)
            except Exception:
                pass
        self._synth = None
        self._sfid = -1

        if lib is not None and self._settings:
            try:
                lib.delete_fluid_settings(self._settings)
            except Exception:
                pass
        self._settings = None
        self._lib = None

        if self._dll_dir_handle is not None:
            try:
                self._dll_dir_handle.close()
            except Exception:
                pass
        self._dll_dir_handle = None

    def __enter__(self) -> "MidiPreviewSynth":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
