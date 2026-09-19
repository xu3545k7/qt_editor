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

    @property
    def beat_s(self) -> float:
        return 60.0 / float(self.bpm)

    @property
    def bar_s(self) -> float:
        return self.beat_s * max(1, int(self.numerator))


@dataclass
class GridStats:
    pad_ms: int
    notes: int
    merged: int                #: 吸到同一格而合併掉的同音
    trimmed: int               #: 因為和下一顆同音重疊而截短的
    pedals: int
    bars: int


def pad_ms_for(settings: GridSettings) -> int:
    """音檔前面要補多少毫秒，downbeat 才會落在小節線上（0 ≤ pad < 一小節）。"""
    bar_ms = settings.bar_s * 1000.0
    pad = (-settings.downbeat * 1000.0) % bar_ms
    pad_ms = int(round(pad))
    return 0 if pad_ms >= int(round(bar_ms)) else pad_ms


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


def build(src_path: str, dst_path: str, settings: GridSettings) -> GridStats:
    """讀 AI MIDI、對齊並吸附，寫出新的 MIDI。回傳統計。"""
    notes, pedals = read_ai_midi(src_path)
    pad_ms = pad_ms_for(settings)
    pad = pad_ms / 1000.0
    beat_s = settings.beat_s
    step = 1.0 / settings.division if settings.division > 0 else 0.0
    min_len_ticks = max(1, int(round(step * TICKS_PER_BEAT))) if step else 1

    placed: Dict[Tuple[int, int], Tuple[int, int]] = {}   # (音高, 開始 tick) → (結束 tick, 力度)
    merged = 0
    for start, end, pitch, vel in notes:
        b0 = _snap((start + pad) / beat_s, settings)
        b1 = (end + pad) / beat_s
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
        d = int(round(_snap((down + pad) / beat_s, settings) * TICKS_PER_BEAT))
        u = int(round(_snap((up + pad) / beat_s, settings) * TICKS_PER_BEAT))
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

    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(float(settings.bpm)), time=0))
    meta.append(mido.MetaMessage('time_signature', numerator=max(1, int(settings.numerator)),
                                 denominator=4, time=0))
    meta.append(mido.MetaMessage('end_of_track', time=0))
    midi.tracks.append(meta)
    track = mido.MidiTrack()
    last = 0
    for tick, _order, msg in events:
        track.append(msg.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage('end_of_track', time=0))
    midi.tracks.append(track)
    midi.save(dst_path)

    bar_ticks = TICKS_PER_BEAT * max(1, int(settings.numerator))
    return GridStats(pad_ms=pad_ms, notes=len(final), merged=merged, trimmed=trimmed,
                     pedals=len(pedal_ticks), bars=(last + bar_ticks - 1) // bar_ticks)


def output_path(ai_midi_path: str, bpm: float) -> str:
    """`歌名.ai.mid` → `歌名.ai.186bpm.mid`。"""
    base = ai_midi_path[:-len('.mid')] if ai_midi_path.lower().endswith('.mid') else ai_midi_path
    text = ('%.2f' % bpm).rstrip('0').rstrip('.')
    return '%s.%sbpm.mid' % (base, text)
