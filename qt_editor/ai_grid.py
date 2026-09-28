"""把 AI 轉譜的 MIDI（固定 120 BPM、時間＝音檔秒數）改寫成指定 BPM、對齊小節線的 MIDI。

編輯器的慣例是「譜面第 0 毫秒＝音檔第 0 毫秒＝第一小節起點」。音樂的第一個
小節起點（downbeat）通常不在 0，所以和「音訊開頭空白」同一套做法：音檔前面
補 `pad_ms` 的靜音，讓 downbeat 剛好落在某條小節線上，音符也一起往後移同樣的量。
補的量永遠在 [0, 一小節) 之間，開頭多出來的那段正好當作進場空間。

吸附（量化）照 quantize_dialog 的精神保留強度：100% 貼齊格點，50% 只收一半。
吸附之後同音高的兩顆可能擠到同一格或互相重疊——同一格留力度大的那顆，重疊的
把前一顆的尾巴截到下一顆開頭，否則匯入時會配出鬼音（見 MIDI 同音配對）。
"""

from __future__ import annotations

import bisect

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import mido

TICKS_PER_BEAT = 480

#: 吸附格選項：(顯示文字, 每拍幾格)；0 = 不吸附，只對齊小節線
GRID_CHOICES: Sequence[Tuple[str, int]] = (
    ('不吸附（只對齊小節線）', 0),
    ('4 分音符', 1),
    ('8 分音符', 2),
    ('8 分三連音', 3),
    ('16 分音符', 4),
    ('16 分三連音', 6),
    ('32 分音符', 8),
)


@dataclass
class GridSettings:
    bpm: float
    downbeat: float            #: 秒（音檔時間），任一個小節起點
    numerator: int = 4         #: 每小節幾拍（分母固定 4）
    division: int = 4          #: 每拍幾格，0 = 不吸附
    strength: float = 1.0      #: 0~1
    snap_length: bool = True
    #: 變速用：每一拍的時間（秒）。給了就以它為格線，`bpm` 只當第一段的速度。
    beats: Optional[Sequence[float]] = None

    @property
    def beat_s(self) -> float:
        return 60.0 / float(self.bpm)

    @property
    def bar_s(self) -> float:
        """第一小節的長度（補靜音的量用它算）。"""
        if self.variable:
            return float(self.beats[min(self.numerator, len(self.beats) - 1)]) - float(self.beats[0])
        return self.beat_s * max(1, int(self.numerator))

    @property
    def variable(self) -> bool:
        return bool(self.beats) and len(self.beats) > max(1, int(self.numerator))


@dataclass
class GridStats:
    pad_ms: int
    notes: int
    merged: int                #: 吸到同一格而合併掉的同音
    trimmed: int               #: 因為和下一顆同音重疊而截短的
    pedals: int
    bars: int
    tempos: int = 1            #: 寫進 MIDI 的速度事件數（1 = 定速）
    bpm_min: float = 0.0
    bpm_max: float = 0.0


def pad_ms_for(settings: GridSettings) -> int:
    """音檔前面要補多少毫秒，downbeat 才會落在小節線上（0 ≤ pad < 一小節）。"""
    bar_ms = settings.bar_s * 1000.0
    downbeat = float(settings.beats[0]) if settings.variable else settings.downbeat
    pad = (-downbeat * 1000.0) % bar_ms
    pad_ms = int(round(pad))
    return 0 if pad_ms >= int(round(bar_ms)) else pad_ms


class _BeatMap:
    """把秒數換成「第幾拍」。變速時照拍點表內插，兩頭用頭尾的拍長外推。"""

    def __init__(self, settings: GridSettings):
        self.fixed = None if settings.variable else settings.beat_s
        self.beats = [float(b) for b in settings.beats] if settings.variable else []

    def position(self, seconds: float) -> float:
        if self.fixed is not None:
            return seconds / self.fixed
        beats = self.beats
        if seconds <= beats[0]:
            first = beats[1] - beats[0]
            return (seconds - beats[0]) / first if first > 0 else 0.0
        if seconds >= beats[-1]:
            last = beats[-1] - beats[-2]
            return (len(beats) - 1) + ((seconds - beats[-1]) / last if last > 0 else 0.0)
        i = bisect.bisect_right(beats, seconds) - 1
        span = beats[i + 1] - beats[i]
        return i + ((seconds - beats[i]) / span if span > 0 else 0.0)

    def seconds(self, position: float) -> float:
        """第幾拍 → 秒（position 的反向）。"""
        if self.fixed is not None:
            return position * self.fixed
        beats = self.beats
        if position <= 0.0:
            return beats[0] + position * (beats[1] - beats[0])
        if position >= len(beats) - 1:
            return beats[-1] + (position - (len(beats) - 1)) * (beats[-1] - beats[-2])
        i = int(position)
        return beats[i] + (position - i) * (beats[i + 1] - beats[i])


def read_ai_midi(path: str):
    """讀 AI 轉出的 MIDI → (音符 [(開始秒, 結束秒, 音高, 力度)], 踏板 [(踩下秒, 放開秒)])。"""
    from .models import open_midi   # 讀 MIDI 一律走這裡（容忍壞掉的 meta 事件）

    notes: List[Tuple[float, float, int, int]] = []
    pedals: List[Tuple[float, float]] = []
    active: Dict[int, List[Tuple[float, int]]] = {}
    pedal_down: Optional[float] = None
    t = 0.0
    for msg in open_midi(path):
        t += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            active.setdefault(msg.note, []).append((t, msg.velocity))
        elif msg.type in ('note_off', 'note_on'):
            stack = active.get(msg.note)
            if stack:
                start, vel = stack.pop(0)       # 同音先進先出
                notes.append((start, t, msg.note, vel))
        elif msg.type == 'control_change' and msg.control == 64:
            if msg.value >= 64 and pedal_down is None:
                pedal_down = t
            elif msg.value < 64 and pedal_down is not None:
                pedals.append((pedal_down, t))
                pedal_down = None
    for pitch, stack in active.items():         # 沒收尾的音：給一個短長度
        for start, vel in stack:
            notes.append((start, start + 0.1, pitch, vel))
    if pedal_down is not None:
        pedals.append((pedal_down, t))
    notes.sort()
    return notes, pedals


def onsets_for_detection(path: str) -> List[Tuple[float, int, int]]:
    notes, _pedals = read_ai_midi(path)
    return [(s, p, v) for s, _e, p, v in notes]


def _snap(beat: float, settings: GridSettings) -> float:
    if settings.division <= 0:
        return beat
    grid = round(beat * settings.division) / float(settings.division)
    return beat + settings.strength * (grid - beat)


def _shift_beats(settings: GridSettings, beat_map: '_BeatMap', pad: float) -> float:
    """拍點表的第 0 拍，在輸出裡是第幾拍。

    輸出的時間軸＝音檔時間＋pad，而小節線是整數個 numerator 拍。補的靜音讓第一個
    小節起點剛好落在小節線上，所以位移是整數個小節（變速時每一段拍長不同，只能
    這樣算，不能拿 pad 去除一個拍長）。
    """
    if not settings.variable:
        return pad / settings.beat_s
    numerator = max(1, int(settings.numerator))
    bar_first = settings.bar_s
    bars = round((float(settings.beats[0]) + pad) / bar_first) if bar_first > 0 else 0
    return float(bars * numerator)


def _tempo_events(beat_map: '_BeatMap', shift: float, total_bars: int, numerator: int,
                  pad: float, budget_ms: float = 8.0):
    """小節線的目標時間 → 速度事件 [(tick, 微秒/拍)]。

    每一小節都算一次「要多快才會落在目標時間」，但只有在沿用目前速度會偏掉超過
    budget_ms 時才真的寫一個事件。定速的曲子因此只有一個事件；變速的只在真的
    換速的地方插入，而且每次都是從**目前累積到的時間**算起，誤差不會一路加上去。
    """
    events = []
    # tick 0 的目標時間幾乎是 0（pad 取整到毫秒會差半毫秒以內），從它起算而不是從 0：
    # 拿那半毫秒去算第一個速度，會讓整首慢慢偏掉，偏到剛好撞上 budget 時速度就會
    # 每兩小節來回跳（實測 196 BPM 被寫成 195.993，第 190 小節起在 177／175 之間震盪）。
    cursor = beat_map.seconds(-shift) + pad
    per_beat = None
    for bar in range(total_bars):
        position = bar * numerator
        target_now = beat_map.seconds(position - shift) + pad
        target_next = beat_map.seconds(position - shift + numerator) + pad
        if per_beat is not None:
            predicted = cursor + per_beat * numerator
            if abs(predicted - target_next) * 1000.0 <= budget_ms:
                cursor = predicted
                continue
        per_beat = (target_next - target_now) / numerator      # 這一小節真正的長度
        drift = cursor - target_now
        if abs(drift) * 1000.0 > budget_ms:                    # 偏太多才把偏差補回來
            per_beat -= drift / numerator
        per_beat = max(1e-4, per_beat)
        events.append((position * TICKS_PER_BEAT,
                       max(1, int(round(per_beat * 1e6)))))
        cursor += per_beat * numerator
    if not events:
        events.append((0, 500000))
    return events


def build(src_path: str, dst_path: str, settings: GridSettings) -> GridStats:
    """讀 AI MIDI、對齊並吸附，寫出新的 MIDI。回傳統計。"""
    notes, pedals = read_ai_midi(src_path)
    pad_ms = pad_ms_for(settings)
    pad = pad_ms / 1000.0
    beat_map = _BeatMap(settings)
    shift = _shift_beats(settings, beat_map, pad)
    step = 1.0 / settings.division if settings.division > 0 else 0.0
    min_len_ticks = max(1, int(round(step * TICKS_PER_BEAT))) if step else 1

    placed: Dict[Tuple[int, int], Tuple[int, int]] = {}   # (音高, 開始 tick) → (結束 tick, 力度)
    merged = 0
    for start, end, pitch, vel in notes:
        b0 = _snap(beat_map.position(start) + shift, settings)
        b1 = beat_map.position(end) + shift
        if settings.snap_length:
            b1 = _snap(b1, settings)
        t0 = int(round(b0 * TICKS_PER_BEAT))
        t1 = int(round(b1 * TICKS_PER_BEAT))
        if settings.snap_length and settings.strength >= 0.999:
            t1 = max(t1, t0 + min_len_ticks)
        t1 = max(t1, t0 + 1)
        key = (pitch, t0)
        if key in placed:
            merged += 1
            old_end, old_vel = placed[key]
            if vel > old_vel or (vel == old_vel and t1 > old_end):
                placed[key] = (t1, vel)
            continue
        placed[key] = (t1, vel)

    # 同音重疊：前一顆的尾巴截到下一顆開頭
    by_pitch: Dict[int, List[List[int]]] = {}
    for (pitch, t0), (t1, vel) in placed.items():
        by_pitch.setdefault(pitch, []).append([t0, t1, vel])
    trimmed = 0
    final: List[Tuple[int, int, int, int]] = []
    for pitch, items in by_pitch.items():
        items.sort()
        for cur, nxt in zip(items, items[1:]):
            if cur[1] > nxt[0]:
                cur[1] = nxt[0]
                trimmed += 1
        for t0, t1, vel in items:
            final.append((t0, t1, pitch, vel))
    final.sort()

    pedal_ticks: List[Tuple[int, int]] = []
    for down, up in pedals:
        d = int(round(_snap(beat_map.position(down) + shift, settings) * TICKS_PER_BEAT))
        u = int(round(_snap(beat_map.position(up) + shift, settings) * TICKS_PER_BEAT))
        if u > d:
            if pedal_ticks and d < pedal_ticks[-1][1]:
                # 吸附後和上一段接在一起或重疊：上一段在這裡放開
                prev_d, _prev_u = pedal_ticks[-1]
                if d <= prev_d:
                    continue
                pedal_ticks[-1] = (prev_d, d)
            pedal_ticks.append((d, u))

    # 同一個 tick：先放開（音、踏板）再按下，避免零長度與配錯
    events: List[Tuple[int, int, mido.Message]] = []
    for t0, t1, pitch, vel in final:
        events.append((t0, 3, mido.Message('note_on', note=pitch, velocity=int(vel))))
        events.append((t1, 0, mido.Message('note_on', note=pitch, velocity=0)))
    for d, u in pedal_ticks:
        events.append((d, 2, mido.Message('control_change', control=64, value=127)))
        events.append((u, 1, mido.Message('control_change', control=64, value=0)))
    events.sort(key=lambda item: (item[0], item[1]))

    numerator = max(1, int(settings.numerator))
    bar_ticks = TICKS_PER_BEAT * numerator
    end_tick = events[-1][0] if events else 0
    total_bars = max(1, (end_tick + bar_ticks - 1) // bar_ticks)
    tempos = _tempo_events(beat_map, shift, total_bars, numerator, pad)

    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage('time_signature', numerator=numerator,
                                 denominator=4, time=0))
    previous_tick = 0
    for tick, microseconds in tempos:
        meta.append(mido.MetaMessage('set_tempo', tempo=microseconds,
                                     time=int(tick - previous_tick)))
        previous_tick = int(tick)
    meta.append(mido.MetaMessage('end_of_track', time=0))
    midi.tracks.append(meta)
    track = mido.MidiTrack()
    previous = 0
    for tick, _order, msg in events:
        track.append(msg.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage('end_of_track', time=0))
    midi.tracks.append(track)
    midi.save(dst_path)

    bpms = [60.0 / (microseconds / 1e6) for _tick, microseconds in tempos]
    return GridStats(pad_ms=pad_ms, notes=len(final), merged=merged, trimmed=trimmed,
                     pedals=len(pedal_ticks), bars=total_bars, tempos=len(tempos),
                     bpm_min=round(min(bpms), 2), bpm_max=round(max(bpms), 2))


def output_path(ai_midi_path: str, bpm: float, variable: bool = False) -> str:
    """`歌名.ai.mid` → `歌名.ai.186bpm.mid`（變速的加 `-var`，免得蓋掉定速那份）。"""
    base = ai_midi_path[:-len('.mid')] if ai_midi_path.lower().endswith('.mid') else ai_midi_path
    text = ('%.2f' % bpm).rstrip('0').rstrip('.')
    return '%s.%sbpm%s.mid' % (base, text, '-var' if variable else '')
