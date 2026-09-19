"""響度量測與增益決策（ITU-R BS.1770-4 / EBU R128）。

量出來的東西只有三個：整曲響度 LUFS-I、真峰值 dBTP、響度範圍 LRA。決策部分照
`交接文档_打包阶段自动响度匹配.md` 的規則：**優先只用固定增益**，峰值超過上限就
把增益壓到剛好不超，需要壓超過預算的曲子標成要複核，不在這裡偷偷做母帶處理。

**為什麼不用 ffmpeg。** 這台機器上沒有，而編輯器是打包成單一執行檔的，多一個外部
相依就多一個「在別人機器上跑不起來」的來源。WAV 自己讀得到，濾波器也只有兩階。

numpy 有就用，沒有就走純 Python 的同一套算式 —— 編輯器目前沒有 numpy 相依，不能
為了量響度而加上去。兩條路徑在測試裡對同一段訊號比對過。
"""

from __future__ import annotations

import math
import wave
from typing import List, Optional, Sequence, Tuple

try:  # numpy 只是加速用，沒有也要能跑
    import numpy as _np
except Exception:  # pragma: no cover - 看機器上有沒有
    _np = None


# ── 常數 ─────────────────────────────────────────────────────────────

#: 這個專案的目標響度。不是廣播標準，是「曲子之間一樣大聲」的起點。
DEFAULT_TARGET_LUFS = -14.0
#: 最終真峰值上限。
DEFAULT_PEAK_LIMIT_DBTP = -1.5
#: 目標的容差：差在這個範圍內就不動它，免得為了小數點再量化一次。
DEFAULT_TOLERANCE_LU = 1.0
#: 需要壓低峰值超過這個量，就不自動處理，交給人複核。
DEFAULT_MAX_PEAK_REDUCTION_DB = 3.0

#: 絕對門限（LKFS）。比這安靜的段落不算進整曲響度。
_ABSOLUTE_GATE = -70.0
#: 相對門限比初步平均低多少。
_RELATIVE_GATE = -10.0
#: 每塊 400ms，重疊 75%。
_BLOCK_MS = 400.0
_BLOCK_OVERLAP = 0.75
#: LRA 用 3 秒的短期響度。
_SHORT_MS = 3000.0

#: 真峰值用幾倍過取樣估。4 倍是 BS.1770 對 ≥44.1k 的最低要求。
_OVERSAMPLE = 4


class LoudnessError(Exception):
    """量不出有意義的響度：空檔、全靜音、解不開。"""


# ── 濾波器係數 ────────────────────────────────────────────────────────

def _k_weighting(rate: int) -> Tuple[Sequence[float], Sequence[float]]:
    """K 加權的兩段濾波器係數，照 BS.1770-4 的方式依取樣率重算。

    標準只給 48kHz 的係數。直接拿去用在 44.1kHz 上，轉折點會跑掉，量出來的值
    會偏移零點幾 LU —— 對「對齊到 ±1 LU」來說不致命，但沒有理由留著這個誤差。
    """
    # 第一段：高頻擱架（模擬人頭的繞射）。
    f0 = 1681.974450955533
    g = 3.999843853973347
    q = 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = math.pow(10.0, g / 20.0)
    vb = math.pow(vh, 0.4996667741545416)
    a0 = 1.0 + k / q + k * k
    b = [
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
    ]
    a = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]

    # 第二段：高通（RLB）。
    f0 = 38.13547087602444
    q = 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    b2 = [1.0, -2.0, 1.0]
    a2 = [
        1.0,
        2.0 * (k * k - 1.0) / (1.0 + k / q + k * k),
        (1.0 - k / q + k * k) / (1.0 + k / q + k * k),
    ]
    # 兩段串起來
    return _convolve(b, b2), _convolve(a, a2)


def _convolve(x: Sequence[float], y: Sequence[float]) -> List[float]:
    out = [0.0] * (len(x) + len(y) - 1)
    for i, xi in enumerate(x):
        for j, yj in enumerate(y):
            out[i + j] += xi * yj
    return out


#: 把 IIR 轉成 FIR 時取多長的脈衝響應。
#:
#: K 加權裡衰減最慢的是 38Hz 的高通，時間常數約 4ms；44.1kHz 下 16384 點是 0.37 秒，
#: 也就是 90 個時間常數之後才截斷，誤差遠在量測精度之下。
_IMPULSE_LENGTH = 16384

_fir_cache: dict = {}


def _filter(samples, b: Sequence[float], a: Sequence[float]):
    """套用 K 加權。

    **numpy 走 FFT 卷積，不是遞迴。** 遞迴濾波每個樣本都相依前一個，在 numpy 裡
    只能逐點跑，一首五分鐘的曲子要一分多鐘；這台機器上也沒有 scipy 的 lfilter。
    改成先用純 Python 把脈衝響應算出來（只有 16384 點，一次就好、還能快取），再用
    FFT 卷積 —— 同一個線性系統，換一種算法。
    """
    if _np is not None and not isinstance(samples, list):
        key = (tuple(b), tuple(a))
        fir = _fir_cache.get(key)
        if fir is None:
            impulse = [0.0] * _IMPULSE_LENGTH
            impulse[0] = 1.0
            fir = _np.asarray(_filter(impulse, b, a), dtype=_np.float64)
            _fir_cache[key] = fir
        return _overlap_add(samples, fir)

    order = max(len(b), len(a)) - 1
    z = [0.0] * order
    out = [0.0] * len(samples)
    b = list(b) + [0.0] * (order + 1 - len(b))
    a = list(a) + [0.0] * (order + 1 - len(a))
    for n, x in enumerate(samples):
        y = b[0] * x + z[0]
        for i in range(order - 1):
            z[i] = b[i + 1] * x + z[i + 1] - a[i + 1] * y
        z[order - 1] = b[order] * x - a[order] * y
        out[n] = y
    if _np is not None:
        return _np.asarray(out)
    return out


def _overlap_add(samples, fir):
    """用 FFT 分段卷積，只保留和輸入等長的前段（因果系統的輸出）。"""
    taps = len(fir)
    block = 1
    while block < taps * 8:
        block *= 2
    step = block - taps + 1
    spectrum = _np.fft.rfft(fir, block)
    out = _np.zeros(len(samples) + taps, dtype=_np.float64)
    for start in range(0, len(samples), step):
        chunk = samples[start:start + step]
        piece = _np.fft.irfft(_np.fft.rfft(chunk, block) * spectrum, block)
        out[start:start + len(piece)] += piece[:len(out) - start]
    return out[:len(samples)]


# ── 讀檔 ─────────────────────────────────────────────────────────────

def read_wav_channels(path: str) -> Tuple[List[List[float]], int]:
    """讀成 −1..1 的浮點，每個聲道一條。只認 PCM WAV。"""
    with wave.open(path, 'rb') as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.getnframes()
        raw = w.readframes(frames)
    if frames <= 0 or not raw:
        raise LoudnessError('音檔是空的')
    if width not in (1, 2, 3, 4):
        raise LoudnessError('不支援 %d bit 的 WAV' % (width * 8))

    if _np is not None:
        data = _decode_numpy(raw, width, channels)
    else:
        data = _decode_python(raw, width, channels)
    return data, rate


def _decode_numpy(raw: bytes, width: int, channels: int):
    if width == 1:
        arr = (_np.frombuffer(raw, dtype=_np.uint8).astype(_np.float32) - 128.0) / 128.0
    elif width == 2:
        arr = _np.frombuffer(raw, dtype='<i2').astype(_np.float32) / 32768.0
    elif width == 4:
        arr = _np.frombuffer(raw, dtype='<i4').astype(_np.float64) / 2147483648.0
    else:  # 24 bit：湊成 32 bit 再轉
        b = _np.frombuffer(raw, dtype=_np.uint8).reshape(-1, 3).astype(_np.int32)
        arr = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8).astype(_np.int32)
        arr = (arr >> 8).astype(_np.float64) / 8388608.0
    usable = (len(arr) // channels) * channels
    arr = arr[:usable].reshape(-1, channels)
    return [arr[:, c].astype(_np.float64) for c in range(channels)]


def _decode_python(raw: bytes, width: int, channels: int) -> List[List[float]]:
    import array
    if width == 2:
        a = array.array('h')
        a.frombytes(raw[:len(raw) - len(raw) % 2])
        scale = 1.0 / 32768.0
        flat = [v * scale for v in a]
    elif width == 4:
        a = array.array('i')
        a.frombytes(raw[:len(raw) - len(raw) % 4])
        scale = 1.0 / 2147483648.0
        flat = [v * scale for v in a]
    elif width == 1:
        flat = [(b - 128) / 128.0 for b in raw]
    else:
        flat = []
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            flat.append(v / 8388608.0)
    usable = (len(flat) // channels) * channels
    return [flat[c:usable:channels] for c in range(channels)]


# ── 量測 ─────────────────────────────────────────────────────────────

class Measurement:
    """一個音檔量出來的東西。"""

    def __init__(self, lufs_i: float, true_peak_dbtp: float, lra: float,
                 rate: int, channels: int, frames: int):
        self.lufs_i = lufs_i
        self.true_peak_dbtp = true_peak_dbtp
        self.lra = lra
        self.rate = rate
        self.channels = channels
        self.frames = frames

    def as_dict(self) -> dict:
        return {
            'lufs_i': round(self.lufs_i, 2),
            'true_peak_dbtp': round(self.true_peak_dbtp, 2),
            'lra': round(self.lra, 2),
            'sample_rate': self.rate,
            'channels': self.channels,
            'frames': self.frames,
        }

    def __repr__(self) -> str:  # pragma: no cover - 只給人看
        return ('Measurement(lufs_i=%.2f, true_peak=%.2f, lra=%.2f, %dHz, %dch, %d frames)'
                % (self.lufs_i, self.true_peak_dbtp, self.lra, self.rate, self.channels,
                   self.frames))


#: 各聲道的加權。BS.1770：左右 1.0，中 1.0，環繞 1.41。這裡只會遇到單聲道和立體聲。
_CHANNEL_WEIGHTS = (1.0, 1.0, 1.0, 1.41, 1.41)


def measure_pcm16(pcm: bytes, rate: int, channels: int = 2) -> Measurement:
    """量一段已經在記憶體裡的 16-bit PCM（打包時混好的那一份）。"""
    if not pcm:
        raise LoudnessError('音檔是空的')
    if _np is not None:
        data = _decode_numpy(pcm, 2, channels)
    else:
        data = _decode_python(pcm, 2, channels)
    return measure(data, rate)


def measure_file(path: str) -> Measurement:
    channels, rate = read_wav_channels(path)
    return measure(channels, rate)


def measure(channels: Sequence[Sequence[float]], rate: int) -> Measurement:
    if not channels or not len(channels[0]):
        raise LoudnessError('音檔是空的')
    frames = len(channels[0])
    b, a = _k_weighting(rate)

    block = int(round(rate * _BLOCK_MS / 1000.0))
    step = max(1, int(round(block * (1.0 - _BLOCK_OVERLAP))))
    if frames < block:
        raise LoudnessError('音檔太短，量不出整曲響度（不足 %d ms）' % int(_BLOCK_MS))

    # 每一塊的 mean square，各聲道加權相加。
    sums = None
    for index, samples in enumerate(channels):
        weight = _CHANNEL_WEIGHTS[min(index, len(_CHANNEL_WEIGHTS) - 1)]
        filtered = _filter(samples, b, a)
        power = _block_power(filtered, block, step)
        if sums is None:
            sums = [p * weight for p in power]
        else:
            for i, p in enumerate(power):
                sums[i] += p * weight
    assert sums is not None

    lufs = _gated_loudness(sums)
    lra = _loudness_range(channels, rate, b, a)
    peak = _true_peak_dbtp(channels)
    return Measurement(lufs, peak, lra, rate, len(channels), frames)


def _block_power(samples, block: int, step: int) -> List[float]:
    """每一塊的均方值。"""
    if _np is not None and not isinstance(samples, list):
        square = _np.square(samples)
        cumulative = _np.concatenate(([0.0], _np.cumsum(square)))
        starts = _np.arange(0, len(samples) - block + 1, step)
        totals = cumulative[starts + block] - cumulative[starts]
        return (totals / block).tolist()
    out = []
    total = 0.0
    # 滑動視窗：先算第一塊，之後每次加尾減頭。直接每塊重算是 O(n²/step)。
    for i in range(block):
        total += samples[i] * samples[i]
    out.append(total / block)
    start = 0
    while start + step + block <= len(samples):
        for i in range(start + block, start + block + step):
            total += samples[i] * samples[i]
        for i in range(start, start + step):
            total -= samples[i] * samples[i]
        start += step
        out.append(max(0.0, total) / block)
    return out


def _loudness_from_power(power: float) -> float:
    if power <= 0.0:
        return float('-inf')
    return -0.691 + 10.0 * math.log10(power)


def _gated_loudness(block_power: Sequence[float]) -> float:
    """兩段門限：先丟掉 −70 LKFS 以下，再丟掉比初步平均低 10 LU 的。"""
    first = [p for p in block_power if _loudness_from_power(p) > _ABSOLUTE_GATE]
    if not first:
        raise LoudnessError('整首都在絕對門限以下（等於靜音）')
    preliminary = _loudness_from_power(sum(first) / len(first))
    threshold = preliminary + _RELATIVE_GATE
    second = [p for p in first if _loudness_from_power(p) > threshold]
    if not second:
        raise LoudnessError('相對門限之上沒有內容')
    return _loudness_from_power(sum(second) / len(second))


def _loudness_range(channels: Sequence[Sequence[float]], rate: int,
                    b: Sequence[float], a: Sequence[float]) -> float:
    """LRA：短期響度（3 秒）在絕對與相對門限之後的 10～95 百分位差。"""
    block = int(round(rate * _SHORT_MS / 1000.0))
    step = max(1, int(round(block * (1.0 - _BLOCK_OVERLAP))))
    if len(channels[0]) < block:
        return 0.0
    sums = None
    for index, samples in enumerate(channels):
        weight = _CHANNEL_WEIGHTS[min(index, len(_CHANNEL_WEIGHTS) - 1)]
        filtered = _filter(samples, b, a)
        power = _block_power(filtered, block, step)
        if sums is None:
            sums = [p * weight for p in power]
        else:
            for i, p in enumerate(power):
                sums[i] += p * weight
    assert sums is not None
    values = [_loudness_from_power(p) for p in sums]
    kept = [v for v in values if v > _ABSOLUTE_GATE]
    if not kept:
        return 0.0
    powers = [p for p, v in zip(sums, values) if v > _ABSOLUTE_GATE]
    threshold = _loudness_from_power(sum(powers) / len(powers)) - 20.0
    kept = sorted(v for v in kept if v > threshold)
    if len(kept) < 2:
        return 0.0
    return _percentile(kept, 95.0) - _percentile(kept, 10.0)


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * pct / 100.0
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    frac = position - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _true_peak_dbtp(channels: Sequence[Sequence[float]]) -> float:
    """真峰值：四倍過取樣之後的最大絕對值。

    取樣點之間的波形可能比每一個取樣點都高，只看取樣峰值會低估，轉檔或重取樣之後
    才爆出來。過取樣用線性內插估，比標準的多相 FIR 保守一點點，寧可低估增益。
    """
    peak = 0.0
    for samples in channels:
        if _np is not None and not isinstance(samples, list):
            direct = float(_np.max(_np.abs(samples))) if len(samples) else 0.0
            if len(samples) > 1:
                between = 0.0
                for k in range(1, _OVERSAMPLE):
                    t = k / _OVERSAMPLE
                    mid = samples[:-1] * (1.0 - t) + samples[1:] * t
                    between = max(between, float(_np.max(_np.abs(mid))))
                direct = max(direct, between)
            peak = max(peak, direct)
        else:
            previous = 0.0
            for value in samples:
                peak = max(peak, abs(value))
                for k in range(1, _OVERSAMPLE):
                    t = k / _OVERSAMPLE
                    peak = max(peak, abs(previous * (1.0 - t) + value * t))
                previous = value
    if peak <= 0.0:
        return float('-inf')
    return 20.0 * math.log10(peak)


# ── 決策 ─────────────────────────────────────────────────────────────

class GainPlan:
    """一個檔案要怎麼處理。

    `status` 有四種：
    * ``ok``：本來就在容差內、峰值也沒超，不用動。
    * ``gain``：只用固定增益就到位。
    * ``limited``：為了不超過峰值上限，增益被壓小了，還在可接受範圍。
    * ``needs_review``：要壓的峰值超過預算，交給人決定（換目標或重新母帶）。
    """

    def __init__(self, status: str, gain_db: float, measured: Measurement,
                 target_lufs: float, peak_limit_dbtp: float,
                 peak_reduction_db: float, reason: str = ''):
        self.status = status
        self.gain_db = gain_db
        self.measured = measured
        self.target_lufs = target_lufs
        self.peak_limit_dbtp = peak_limit_dbtp
        self.peak_reduction_db = peak_reduction_db
        self.reason = reason

    @property
    def resulting_lufs(self) -> float:
        return self.measured.lufs_i + self.gain_db

    @property
    def resulting_peak_dbtp(self) -> float:
        return self.measured.true_peak_dbtp + self.gain_db

    def as_dict(self) -> dict:
        return {
            'status': self.status,
            'gain_db': round(self.gain_db, 2),
            'target_lufs': self.target_lufs,
            'true_peak_limit_dbtp': self.peak_limit_dbtp,
            'measured': self.measured.as_dict(),
            'resulting_lufs': round(self.resulting_lufs, 2),
            'resulting_true_peak_dbtp': round(self.resulting_peak_dbtp, 2),
            'peak_reduction_needed_db': round(self.peak_reduction_db, 2),
            'reason': self.reason,
        }

    def __repr__(self) -> str:  # pragma: no cover - 只給人看
        return 'GainPlan(%s, %+.2f dB, %.1f → %.1f LUFS)' % (
            self.status, self.gain_db, self.measured.lufs_i, self.resulting_lufs)


def plan_gain(measured: Measurement, target_lufs: float = DEFAULT_TARGET_LUFS,
              peak_limit_dbtp: float = DEFAULT_PEAK_LIMIT_DBTP,
              tolerance_lu: float = DEFAULT_TOLERANCE_LU,
              max_peak_reduction_db: float = DEFAULT_MAX_PEAK_REDUCTION_DB) -> GainPlan:
    """只用固定增益能不能到目標；不能的話差多少。

    這裡**不做限幅**。要壓峰值才能到目標的曲子，增益壓到剛好不破，剩下的差距誠實
    地留著，並標出來 —— 偷偷壓縮動態換一個漂亮的數字，是這份文件特別交代不要做的事。
    """
    wanted = target_lufs - measured.lufs_i
    headroom = peak_limit_dbtp - measured.true_peak_dbtp
    reduction = max(0.0, wanted - headroom)

    within_tolerance = abs(wanted) <= tolerance_lu
    if within_tolerance and measured.true_peak_dbtp <= peak_limit_dbtp:
        return GainPlan('ok', 0.0, measured, target_lufs, peak_limit_dbtp, reduction,
                        '已經在容差內')

    if reduction <= 0.0:
        return GainPlan('gain', wanted, measured, target_lufs, peak_limit_dbtp, 0.0, '')

    # 只用安全增益（不破峰）就落在容差內的話，就用它，不必為了小數點去動峰值。
    safe = headroom
    if abs(target_lufs - (measured.lufs_i + safe)) <= tolerance_lu:
        return GainPlan('gain', safe, measured, target_lufs, peak_limit_dbtp, 0.0,
                        '壓到剛好不破峰，仍在容差內')

    status = 'limited' if reduction <= max_peak_reduction_db else 'needs_review'
    reason = ('還差 %.1f LU 才到目標，需要壓低峰值 %.1f dB' % (
        target_lufs - (measured.lufs_i + safe), reduction))
    return GainPlan(status, safe, measured, target_lufs, peak_limit_dbtp, reduction, reason)


def apply_gain_pcm16(pcm: bytes, gain_db: float) -> bytes:
    """把固定增益套進 16-bit PCM，超過的夾住（照理不會，增益已經留了餘裕）。"""
    if abs(gain_db) < 1e-6:
        return pcm
    import audioop
    factor = math.pow(10.0, gain_db / 20.0)
    return audioop.mul(pcm, 2, factor)
