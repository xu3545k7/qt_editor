"""把 AI 轉譜的 MIDI（固定 120 BPM、時間＝音檔秒數）改寫成指定 BPM、對齊小節線的 MIDI。

編輯器的慣例是「譜面第 0 毫秒＝音檔第 0 毫秒＝第一小節起點」。音樂的第一個
小節起點（downbeat）通常不在 0，所以和「音訊開頭空白」同一套做法：音檔前面
補 `pad_ms` 的靜音，讓 downbeat 剛好落在某條小節線上，音符也一起往後移同樣的量。
補的量本身永遠在 [0, 一小節) 之間，開頭多出來的那段正好當作進場空間。

`trim_lead_in` 打開時 `pad_ms` 可以是**負的**：音檔開頭本來就沉默好幾小節的話，
補完之後再往回扣整數個小節，只留 `lead_in_bars` 小節的導入（扣整小節不會破壞
小節線對齊）。負的量交給 `audio_lead_in.apply_lead_in`，它支援刪減。

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

#: 滑音（一串 tap）判定：音高每一步最多幾個半音
RUN_MAX_STEP = 3
#: 至少幾顆才算一串
RUN_MIN_NOTES = 4
#: 相鄰起音要比一格的這個比例更密。0.75 是實測挑的：0.5 會把漂流那條 10 顆的
#: 下行滑音切成兩半（只認得 6 顆），1.0 則開始誤抓 280 BPM 譜裡 4 顆的快速音階
#: ——那種間隔就在一格附近，本來就該吸附。
RUN_MAX_GAP = 0.75

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
    #: 自動刪掉開頭多餘的空白。對齊小節線只需要補 [0, 一小節) 的靜音，但音檔
    #: 本身開頭可能先有好幾小節的沉默，補完之後譜面就從一大段空白開始。打開
    #: 之後會再往回扣**整數個**小節，只留 `lead_in_bars` 小節的導入——扣整小節
    #: 不會破壞小節線對齊，這是這個做法成立的關鍵。
    trim_lead_in: bool = False
    #: 保留幾小節的導入（`trim_lead_in` 開著才有意義）
    lead_in_bars: int = 1
    #: 滑音／音階跑動不吸附（整串只對齊第一顆）
    keep_runs: bool = True
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
    #: 吸附後落在 0 之前、被收到 0 的事件數（導入空白＝0 時的踏板最常見）
    lead_clamped: int = 0
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


def lead_shift_ms(settings: GridSettings,
                  first_note_s: Optional[float] = None) -> int:
    """音檔開頭要補（正）或刪（負）多少毫秒。

    先照 `pad_ms_for` 補到小節線上，再視 `trim_lead_in` 往回扣整數個小節。
    `first_note_s` 給了就不會把第一顆音符扣到負的時間（弱起的音在 downbeat
    之前），少給只是保守一點。
    """
    pad_ms = pad_ms_for(settings)
    if not settings.trim_lead_in:
        return pad_ms
    bar_ms = settings.bar_s * 1000.0
    if bar_ms <= 0:
        return pad_ms
    # 基準是**第一顆音符**，不是 downbeat。使用者說的「前面空白」是音樂開始前
    # 的那一段，而這兩者常常差很多：實測漂流的 downbeat 在 0.327 秒（不到一
    # 小節），但第一顆音在 8.27 秒——空白是音樂晚進來造成的，拿 downbeat 當
    # 基準會算出「不用刪」。
    anchor_ms = 1000.0 * (float(first_note_s) if first_note_s is not None
                          else (float(settings.beats[0]) if settings.variable
                                else float(settings.downbeat)))
    keep = max(0, int(settings.lead_in_bars))
    if keep == 0:
        # **0 就是真的 0。**只扣整小節的話第一顆音符會停在「不到一小節」的
        # 餘數上（實測 POTENTIAL：1725ms 的空白扣掉 1 小節還剩 525ms），控制項
        # 寫 0 卻留了半個小節。使用者要的是「導入空白是 0」，所以整段扣掉。
        #
        # 代價是格線的相位改由第一顆音符決定（它變成 downbeat）。keep >= 1 時
        # 仍然只扣整小節，原本的相位不動。
        return int(round(-anchor_ms))
    # 取 floor：保留「至少」這麼多整小節的導入。第一顆音通常不在小節線上，
    # 用 round 會把它推到目標線之前。
    bars_before = int((anchor_ms + pad_ms) // bar_ms)
    drop = max(0, bars_before - keep)
    return int(round(pad_ms - drop * bar_ms))


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


def _run_starts(notes, beat_map: '_BeatMap', settings: GridSettings,
                shift: float) -> Dict[int, float]:
    """找出「一串 tap」的音符，回傳 {在 notes 裡的序號: 要用的拍位置}。

    滑音（華音）和音階跑動是一串單音，顆與顆之間的時間差本身就是它的樣子。
    照格線吸附會把整串壓到同幾個格子上——實測漂流那條 10 顆、橫跨 185ms 的
    下行滑音，16 分吸附後只剩 3 個格子，變成三個五連音和弦。所以整串只把
    第一顆對到格線上，其餘保留原本的相對時間，起點仍然對拍。

    判定要音高**單調小步**：擠在一起的和弦也一樣密，但它音高亂跳、跨好幾個
    八度（實測有 5 顆在 20ms 內、音高 85/97/30/42/82），那種本來就該一起吸到
    同一格。
    """
    out: Dict[int, float] = {}
    if not settings.keep_runs or settings.division <= 0 or len(notes) < RUN_MIN_NOTES:
        return out
    gap = RUN_MAX_GAP / float(settings.division)       # 拍
    beats = [beat_map.position(n[0]) + shift for n in notes]

    def flush(run: List[int]) -> None:
        if len(run) < RUN_MIN_NOTES:
            return
        head = beats[run[0]]
        anchor = _snap(head, settings)
        for idx in run:
            out[idx] = anchor + (beats[idx] - head)

    run = [0]
    for i in range(1, len(notes)):
        step = notes[i][2] - notes[run[-1]][2]
        dt = beats[i] - beats[run[-1]]
        ok = 0.0 < dt < gap and 0 < abs(step) <= RUN_MAX_STEP
        if ok and len(run) >= 2:
            prev = notes[run[-1]][2] - notes[run[-2]][2]
            ok = step * prev > 0                       # 整串同方向
        if ok:
            run.append(i)
        else:
            flush(run)
            run = [i]
    flush(run)
    return out


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
    first_note_s = min((n[0] for n in notes), default=None)
    pad_ms = lead_shift_ms(settings, first_note_s)
    pad = pad_ms / 1000.0
    beat_map = _BeatMap(settings)
    shift = _shift_beats(settings, beat_map, pad)
    step = 1.0 / settings.division if settings.division > 0 else 0.0
    min_len_ticks = max(1, int(round(step * TICKS_PER_BEAT))) if step else 1

    placed: Dict[Tuple[int, int], Tuple[int, int]] = {}   # (音高, 開始 tick) → (結束 tick, 力度)
    merged = 0
    runs = _run_starts(notes, beat_map, settings, shift)
    for idx, (start, end, pitch, vel) in enumerate(notes):
        in_run = idx in runs
        b0 = runs[idx] if in_run else _snap(beat_map.position(start) + shift, settings)
        b1 = beat_map.position(end) + shift
        if settings.snap_length and not in_run:
            b1 = _snap(b1, settings)
        elif in_run:
            # 一串 tap：長度跟著起音一起平移，不吸附也不撐到一格
            b1 += b0 - (beat_map.position(start) + shift)
        t0 = int(round(b0 * TICKS_PER_BEAT))
        t1 = int(round(b1 * TICKS_PER_BEAT))
        if settings.snap_length and settings.strength >= 0.999 and not in_run:
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

    # 吸附之後可能出現**負的** tick。導入空白＝0 的時候基準是「第一顆音符」，
    # 而踏板常常在它之前就按下去了（實測 8 份 AI 轉譜有 5 份如此，最早的在
    # 0.009 秒）。MIDI 的 delta time 不能是負的，mido 會直接丟
    # 'message time must be non-negative in MIDI file'——使用者踩到的就是這個，
    # 那 5 份一份都轉不出來。
    #
    # 負的一律收到 0：踏板在音樂開始之前就已經按住了，收到 0 正好是那個意思。
    clamped = 0
    if final and final[0][0] < 0:
        fixed: List[Tuple[int, int, int, int]] = []
        for t0, t1, pitch, vel in final:
            if t0 < 0:
                clamped += 1
                t0 = 0
            fixed.append((t0, max(t1, t0 + 1), pitch, vel))
        final = sorted(fixed)
    if any(d < 0 for d, _u in pedal_ticks):
        kept: List[Tuple[int, int]] = []
        for d, u in pedal_ticks:
            if d < 0:
                clamped += 1
                d = 0
            u = max(0, u)
            if u <= d:
                continue                      # 整段都在開頭之前，沒有長度了
            if kept and d < kept[-1][1]:
                # 收到 0 之後可能和下一段疊在一起：上一段在這裡放開
                prev_d, _prev_u = kept[-1]
                if d <= prev_d:
                    continue
                kept[-1] = (prev_d, d)
            kept.append((d, u))
        pedal_ticks = kept

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
                     lead_clamped=clamped,
                     bpm_min=round(min(bpms), 2), bpm_max=round(max(bpms), 2))


def output_path(ai_midi_path: str, bpm: float, variable: bool = False) -> str:
    """`歌名.ai.mid` → `歌名.ai.186bpm.mid`（變速的加 `-var`，免得蓋掉定速那份）。"""
    base = ai_midi_path[:-len('.mid')] if ai_midi_path.lower().endswith('.mid') else ai_midi_path
    text = ('%.2f' % bpm).rstrip('0').rstrip('.')
    return '%s.%sbpm%s.mid' % (base, text, '-var' if variable else '')
