"""從音符（AI 轉譜的結果）推 BPM、第一拍與小節線的位置。

AI 轉出來的 MIDI 只有「幾秒按下哪個鍵」，沒有拍子。要吸附小節線得先知道三件事：

1. **BPM**：把每個起音當成單位圓上的一點（角度 = 時間 ÷ 拍長），
   拍長對了這些點會擠在一起，向量和的長度（相位一致性）就大。粗掃再細到 0.01。
   節奏遊戲的曲子幾乎都是整數 BPM，整數的分數夠接近就取整數。
   BPM 差 0.1，三分鐘下來就漂將近 100ms，所以細修不能省。
2. **第一拍**：拍長固定後，找讓「落在拍上、其次半拍、再其次 16 分」加權最高的相位。
3. **小節起點**：一小節有 N 拍，N 種可能的起點裡，選低音與重音最多的那一拍。

只假設整首一個速度；中途變速的曲子只會對上主要段落，那種要自己在編輯器裡改。
numpy 有就用（打包的 exe 裡有）；沒有的話 `available()` 是 False，呼叫端改讓使用者手動輸入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

try:
    import numpy as _np
except Exception:                               # noqa: BLE001
    _np = None

#: 起音相差這麼近算同一個和弦（秒）
CHORD_WINDOW = 0.03
#: 自動偵測的 BPM 範圍（節奏遊戲常見範圍；半速／倍速由候選清單提供）
BPM_MIN = 70.0
BPM_MAX = 300.0
#: 整數 BPM 的分數至少要有最佳分數的這個比例才取整數
INTEGER_KEEP = 0.93
#: 低音線以下的音（MIDI 音高）在判斷拍點與小節起點時加重
BASS_PITCH = 55
#: 找拍點相位時，低音在「拍上」那一項的額外權重
BASS_BEAT_WEIGHT = 2.0


def available() -> bool:
    return _np is not None


@dataclass
class BeatEstimate:
    bpm: float
    first_beat: float          #: 秒，>= 0，第一個拍點（不一定是小節起點）
    downbeat: float            #: 秒，>= 0，第一個小節起點
    confidence: float          #: 0~1，BPM 的相位一致性
    candidates: List[Tuple[float, float]] = field(default_factory=list)  #: (bpm, 分數)


def _events(notes: Sequence[Tuple[float, int, int]]):
    """(秒, 音高, 力度) → 合併和弦後的 (時間, 權重, 低音權重) 陣列。"""
    np = _np
    items = sorted(notes)
    times: List[float] = []
    weights: List[float] = []
    bass: List[float] = []
    for t, pitch, vel in items:
        w = max(1, int(vel)) / 127.0
        b = w if int(pitch) < BASS_PITCH else 0.0
        if times and t - times[-1] <= CHORD_WINDOW:
            # 和弦：權重累加但壓縮（10 個音的和弦不該是單音的 10 倍）
            weights[-1] = (weights[-1] ** 2 + w ** 2) ** 0.5
            bass[-1] = max(bass[-1], b)
            continue
        times.append(float(t))
        weights.append(w)
        bass.append(b)
    return np.asarray(times), np.asarray(weights), np.asarray(bass)


def _coherence(times, weights, bpms):
    """每個 BPM 的相位一致性（0~1）。"""
    np = _np
    freqs = np.asarray(bpms, dtype=float)[:, None] / 60.0
    total = weights.sum() or 1.0
    out = np.empty(len(freqs))
    # 分批避免 BPM 多 × 音符多時吃太多記憶體
    for start in range(0, len(freqs), 256):
        phase = 2.0 * np.pi * freqs[start:start + 256] * times[None, :]
        out[start:start + 256] = np.abs((weights[None, :] * np.exp(1j * phase)).sum(axis=1)) / total
    return out


def _grid_score(times, weights, bpms):
    """拍、8 分、16 分三層格子的一致性相加。

    只看一拍的頻率不行：16 分音符密的曲子，起音平均落在拍內四個位置，
    互相抵消，真正的 BPM 分數反而低，會選到 1.25、1.5 倍這種怪值。
    16 分格子（4 倍頻）只有真正的 BPM 與它的整數倍會對齊。
    """
    np = _np
    bpms = np.asarray(bpms, dtype=float)
    return (_coherence(times, weights, bpms)
            + _coherence(times, weights, bpms * 2.0)
            + _coherence(times, weights, bpms * 4.0))


def _prior(bpms):
    """節奏遊戲曲子的 BPM 先驗：以 170 為中心的寬對數常態，只用來分倍數。"""
    np = _np
    return np.exp(-0.5 * (np.log2(np.asarray(bpms, dtype=float) / 170.0) / 0.5) ** 2)


def _fit_bpm(times, weights, lo: float, hi: float) -> Tuple[float, float, List[Tuple[float, float]]]:
    np = _np
    coarse = np.arange(lo, hi + 1e-9, 0.25)
    scores = _grid_score(times, weights, coarse) * (0.6 + 0.4 * _prior(coarse))
    # 候選：局部最大值，依分數排序
    peaks = [i for i in range(1, len(scores) - 1)
             if scores[i] >= scores[i - 1] and scores[i] >= scores[i + 1]]
    peaks.sort(key=lambda i: -scores[i])
    refined: List[Tuple[float, float]] = []
    for i in peaks[:6]:
        fine = np.arange(coarse[i] - 0.3, coarse[i] + 0.3 + 1e-9, 0.01)
        fs = _grid_score(times, weights, fine) * (0.6 + 0.4 * _prior(fine))
        j = int(fs.argmax())
        bpm, score = float(fine[j]), float(fs[j])
        integer = float(round(bpm))
        int_score = float((_grid_score(times, weights, [integer])
                           * (0.6 + 0.4 * _prior([integer])))[0])
        if abs(integer - bpm) <= 0.3 and int_score >= INTEGER_KEEP * score:
            bpm, score = integer, int_score
        if all(abs(bpm - b) > 0.5 for b, _s in refined):
            refined.append((round(bpm, 2), score))
    refined.sort(key=lambda item: -item[1])
    best_bpm, best_score = refined[0] if refined else (120.0, 0.0)
    return best_bpm, best_score, refined


def _beat_phase(times, weights, bass, period: float) -> float:
    """第一拍的相位（秒，0 ≤ φ < period）：拍上最重、半拍次之、16 分再次之。

    切分音多的曲子，旋律的重音常落在反拍，只看起音會差半拍；低音線幾乎都踩在
    拍上，所以「拍上」那一項讓低音加重。
    """
    np = _np
    x = 2.0 * np.pi * (times % period) / period
    on_beat_w = weights + BASS_BEAT_WEIGHT * bass
    candidates = np.linspace(0.0, period, 240, endpoint=False)
    best, best_score = 0.0, -1e18
    for phi in candidates:
        d = x - 2.0 * np.pi * phi / period
        score = float((on_beat_w * np.cos(d)
                       + weights * (0.5 * np.cos(2 * d) + 0.25 * np.cos(4 * d))).sum())
        if score > best_score:
            best, best_score = float(phi), score
    return best


def _downbeat(times, weights, bass, first_beat: float, period: float,
              numerator: int) -> float:
    """在 numerator 個可能的小節起點裡，選拍上低音與重音最多的那個。"""
    np = _np
    beat_index = np.round((times - first_beat) / period).astype(int)
    on_beat = np.abs(times - (first_beat + beat_index * period)) <= min(0.06, period * 0.15)
    best, best_score = 0, -1.0
    for k in range(max(1, numerator)):
        sel = on_beat & (((beat_index - k) % numerator) == 0)
        score = float((bass[sel] * 2.0 + weights[sel]).sum())
        if score > best_score:
            best, best_score = k, score
    return first_beat + best * period


#: 起音對格的寬容度（秒）：AI 轉譜的起音誤差約 10ms，給兩倍多
ONSET_SIGMA = 0.025
#: 一次用多少小節估一段的速度（太短就不可靠：短窗自製支撐度的版本實測會亂跳）
WINDOW_BARS = 16
#: 視窗最少要有這麼多小節才判斷（歌尾巴不足就延用目前速度）
MIN_WINDOW_BARS = 8
#: 視窗裡的速度只在目前速度的 ±這個比例內找（不給它跳到兩倍、一半）
WINDOW_BAND = 0.12
#: 和目前速度差不到這個比例就不算換速，避免一堆 0.1% 的假段
MIN_TEMPO_STEP = 0.012
#: 視窗裡的音符權重少於這個就不判斷（空拍、長音撐著的段落沒有證據）
MIN_WINDOW_WEIGHT = 8.0
#: 一小節的加權平均對格誤差超過這個（毫秒）就當成速度在這裡變了，段落收在這條小節線
SEGMENT_TOLERANCE_MS = 22.0
#: 判斷一小節對不對得上格子，至少要有這麼多音符權重
MIN_BAR_WEIGHT = 1.2
#: 最多切幾段（保險，別讓壞資料生出幾百個速度事件）
MAX_SEGMENTS = 200


@dataclass
class TempoSegment:
    start: float               #: 這一段第一條小節線的時間（秒）
    period: float              #: 一拍幾秒
    bars: int

    @property
    def bpm(self) -> float:
        return 60.0 / self.period


@dataclass
class BeatCurve:
    """整首的拍點時間表（變速用）。"""
    beats: List[float]            #: 每一拍的時間（秒，音檔時間），beats[0] = 第一個小節起點
    numerator: int
    segments: List[TempoSegment]
    confidence: float

    @property
    def bar_starts(self) -> List[float]:
        step = max(1, self.numerator)
        return self.beats[::step]

    @property
    def bpm_first(self) -> float:
        return round(self.segments[0].bpm, 2) if self.segments else 0.0

    @property
    def bpm_min(self) -> float:
        return round(min(s.bpm for s in self.segments), 2) if self.segments else 0.0

    @property
    def bpm_max(self) -> float:
        return round(max(s.bpm for s in self.segments), 2) if self.segments else 0.0


#: sqrt(2*pi)，算隨機起音落在格線附近的期望值用
_CHANCE_CONSTANT = 2.5066282746310002


def _window(times, weights, start: float, span: float):
    np = _np
    lo = int(np.searchsorted(times, start))
    hi = int(np.searchsorted(times, start + span))
    return times[lo:hi], weights[lo:hi]


def _fit_error_ms(times, weights, start: float, period: float, bars: int,
                  numerator: int) -> Tuple[float, float]:
    """這幾小節的加權平均對格誤差（毫秒）與權重。用來找換速的位置。"""
    np = _np
    t, w = _window(times, weights, start, period * numerator * bars)
    total = float(w.sum())
    if total <= 0.0:
        return 0.0, 0.0
    x = (t - start) / period
    to_16th = np.abs(x * 4.0 - np.round(x * 4.0)) / 4.0 * period
    return float((w * to_16th).sum()) / total * 1000.0, total


def _split_point(times, weights, start: float, old_period: float, new_period: float,
                 bars: int, numerator: int) -> int:
    """視窗裡是在第幾條小節線換速的（0 = 視窗一開始就換）。

    視窗有 16 小節，換速點只能落在視窗邊界的話，換速之後最多 16 小節會是錯的
    （合成測試抓到低音離拍點 185ms）。這裡逐條小節線試：前面用舊拍長、後面用新
    拍長，取總誤差最小的那一條。
    """
    best, best_error = 0, None
    for k in range(0, bars):
        before_error, before_weight = _fit_error_ms(times, weights, start, old_period,
                                                    k, numerator) if k else (0.0, 0.0)
        pivot = start + old_period * numerator * k
        after_error, after_weight = _fit_error_ms(times, weights, pivot, new_period,
                                                 bars - k, numerator)
        total_weight = before_weight + after_weight
        if total_weight <= 0.0:
            continue
        error = (before_error * before_weight + after_error * after_weight) / total_weight
        if best_error is None or error < best_error:
            best, best_error = k, error
    return best


def _fit_window_bpm(times, weights, start: float, span: float,
                    bpm_now: float) -> Optional[float]:
    """這個視窗裡的 BPM：用和全曲估計同一套（拍／8 分／16 分三層相位一致性）。

    自己寫的「起音離格線多近」在短窗裡不可靠——格子越密分數越高，實測會挑出
    1.5 倍、2 倍，甚至一路飄到 942 BPM。相位一致性在全曲 20 首裡 16 首完全
    正確，把它限制在目前速度的 ±WINDOW_BAND 內找，就不會跳倍數。
    """
    np = _np
    t, w = _window(times, weights, start, span)
    if float(w.sum()) < MIN_WINDOW_WEIGHT or len(t) < 12:
        return None
    lo, hi = bpm_now * (1.0 - WINDOW_BAND), bpm_now * (1.0 + WINDOW_BAND)
    coarse = np.arange(lo, hi + 1e-9, 0.1)
    scores = _grid_score(t, w, coarse)
    best = float(coarse[int(scores.argmax())])
    fine = np.arange(best - 0.1, best + 0.1 + 1e-9, 0.01)
    fs = _grid_score(t, w, fine)
    best, best_score = float(fine[int(fs.argmax())]), float(fs.max())
    integer = float(round(best))
    if abs(integer - best) <= 0.3:
        int_score = float(_grid_score(t, w, [integer])[0])
        if int_score >= INTEGER_KEEP * best_score:
            return integer
    return best


def estimate(notes: Sequence[Tuple[float, int, int]], bpm: Optional[float] = None,
             numerator: int = 4) -> Optional[BeatEstimate]:
    """notes：(起音秒數, MIDI 音高, 力度)。bpm 給了就只找第一拍與小節起點。

    沒有 numpy 或音符太少時回傳 None。
    """
    if _np is None:
        return None
    times, weights, bass = _events(notes)
    if len(times) < 8:
        return None
    candidates: List[Tuple[float, float]] = []
    if bpm and bpm > 0:
        bpm = float(bpm)
        confidence = float(_coherence(times, weights, [bpm])[0])
    else:
        bpm, confidence, candidates = _fit_bpm(times, weights, BPM_MIN, BPM_MAX)
    period = 60.0 / bpm
    phase = _beat_phase(times, weights, bass, period)
    first_beat = phase
    downbeat = _downbeat(times, weights, bass, first_beat, period, max(1, int(numerator)))
    bar = period * max(1, int(numerator))
    downbeat = downbeat % bar
    return BeatEstimate(bpm=round(bpm, 2), first_beat=first_beat, downbeat=downbeat,
                        confidence=confidence, candidates=candidates)


def estimate_curve(notes: Sequence[Tuple[float, int, int]], bpm: Optional[float] = None,
                   numerator: int = 4,
                   window_bars: int = WINDOW_BARS) -> Optional[BeatCurve]:
    """跟著音樂速度走的拍點表：每 window_bars 小節重估一次速度，相同的併成一段。

    重估用的是和全曲估計同一套方法（`_fit_window_bpm`），只是把範圍限制在目前
    速度的 ±WINDOW_BAND。實測把歌切四段各自估，80 段裡 75 段和參考 MIDI 一致，
    所以「長一點的視窗 + 穩的方法」可信；反過來，逐小節用自製支撐度追蹤會從
    148 飄到 307 BPM，那條路已經放棄。

    定速的曲子只會得到一段，結果和固定 BPM 完全相同。音符太少的視窗不判斷。
    """
    if _np is None:
        return None
    base = estimate(notes, bpm, numerator)
    if base is None:
        return None
    times, weights, _bass = _events(notes)
    numerator = max(1, int(numerator))
    bpm_now = float(base.bpm)
    anchor = float(base.downbeat)
    period = 60.0 / bpm_now
    # downbeat 之前還有音符（前奏、弱起）：往前補整小節
    while anchor - period * numerator > float(times[0]):
        anchor -= period * numerator
    last = float(times[-1])

    segments: List[TempoSegment] = []
    while anchor < last and len(segments) < MAX_SEGMENTS:
        period = 60.0 / bpm_now
        bar_s = period * numerator
        bars = min(window_bars, max(1, int((last - anchor) / bar_s) + 1))
        if bars >= MIN_WINDOW_BARS:
            fitted = _fit_window_bpm(times, weights, anchor, bar_s * bars, bpm_now)
            if fitted and abs(fitted - bpm_now) / bpm_now >= MIN_TEMPO_STEP:
                # 視窗裡的速度和目前不同：先找出是哪一條小節線換的。換速點在視窗
                # 中間時，前面那幾小節先用舊速度收起來，下一輪從換速點重新量。
                split = _split_point(times, weights, anchor, period, 60.0 / fitted,
                                     bars, numerator)
                if split > 0:
                    bars = split
                else:
                    bpm_now = fitted
                    period = 60.0 / bpm_now
                    bar_s = period * numerator
                    bars = min(window_bars, max(1, int((last - anchor) / bar_s) + 1))
        # 這個速度實際能蓋幾小節：逐小節看對格誤差，超標就把段落收在那條小節線。
        # 視窗裡如果只有後段換速（16 小節裡 12 小節是舊速度），整體擬合仍然是舊速度，
        # 不看逐小節的話那 4 小節就會被錯誤地收進來（合成測試差了 5 小節）。
        covered, cursor = 0, anchor
        while covered < bars:
            error, weight = _fit_error_ms(times, weights, cursor, period, 1, numerator)
            if weight >= MIN_BAR_WEIGHT and error > SEGMENT_TOLERANCE_MS and covered >= 1:
                break
            covered += 1
            cursor += bar_s
        bars = max(1, covered)

        if segments and abs(segments[-1].bpm - bpm_now) < 1e-9:
            segments[-1].bars += bars          # 和上一段同速：併起來
        else:
            segments.append(TempoSegment(start=anchor, period=period, bars=bars))
        anchor += bar_s * bars

    if not segments:
        return None
    beats: List[float] = []
    for segment in segments:
        for bar in range(segment.bars):
            bar_start = segment.start + segment.period * numerator * bar
            for j in range(numerator):
                beats.append(bar_start + j * segment.period)
    tail = segments[-1]
    beats.append(tail.start + tail.period * numerator * tail.bars)
    return BeatCurve(beats=beats, numerator=numerator, segments=segments,
                     confidence=base.confidence)
