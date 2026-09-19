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
