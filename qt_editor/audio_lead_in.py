"""音訊開頭空白：編輯譜面時直接給 WAV 加上／剪掉開頭的靜音。

取代舊的「播放偏移」——那只是播放時錯開，匯出時才去處理音訊，編輯時看到的
和遊戲裡的不一定一樣。這裡直接產生一份新的 WAV（原檔不動），編輯器改用它。

新檔名是 `<原名>_lead+500.wav`：同一份音訊再調一次會換算成相對原檔的總量
（`_lead+500` 再 −200 → `_lead+300`，回到 0 就用回原檔）。**檔名裡不可以有
`+500ms` 這種寫法**——匯出時 `wav_process.parse_offset_from_filename` 會把它
當成要套用的偏移，等於處理兩次。
"""

from __future__ import annotations

import audioop
import os
import re
import wave
from dataclasses import dataclass
from typing import Optional, Tuple

_LEAD_RE = re.compile(r'_lead([+-]\d+)$')

#: 開頭靜音判斷：振幅低於滿刻度的這個比例算安靜（約 −46 dBFS）
SILENCE_RATIO = 0.005
_CHUNK_MS = 10


@dataclass
class LeadInResult:
    path: str               #: 新的音訊檔（回到 0 時是原檔）
    total_ms: int           #: 相對原檔的總開頭空白調整（正 = 加靜音）
    applied_ms: int         #: 這一次實際套用的量
    cut_sound_ms: int = 0   #: 剪掉的部分裡，有聲音的毫秒數（0 = 只剪到靜音）


def split_lead_name(path: str) -> Tuple[str, int]:
    """`song_lead+500.wav` → (`song.wav` 的完整路徑, 500)；沒有標記就是 (原路徑, 0)。"""
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    match = _LEAD_RE.search(stem)
    if not match:
        return path, 0
    return os.path.join(folder, stem[:match.start()] + ext), int(match.group(1))


def lead_name(original: str, total_ms: int) -> str:
    if total_ms == 0:
        return original
    folder, name = os.path.split(original)
    stem, ext = os.path.splitext(name)
    return os.path.join(folder, '%s_lead%+d%s' % (stem, int(total_ms), ext or '.wav'))


def wav_length_ms(path: str) -> float:
    with wave.open(path, 'rb') as wf:
        rate = wf.getframerate() or 1
        return wf.getnframes() * 1000.0 / rate


def leading_silence_ms(path: str, limit_ms: int = 600_000) -> int:
    """開頭有多少毫秒是安靜的（以 10ms 為單位）。"""
    with wave.open(path, 'rb') as wf:
        width, channels, rate = wf.getsampwidth(), wf.getnchannels(), wf.getframerate()
        if width not in (1, 2, 3, 4) or rate <= 0:
            return 0
        threshold = int((1 << (8 * width - 1)) * SILENCE_RATIO)
        chunk = max(1, rate * _CHUNK_MS // 1000)
        silent = 0
        while silent < limit_ms:
            data = wf.readframes(chunk)
            if not data:
                break
            if width == 1:
                data = audioop.bias(data, 1, -128)
            if audioop.max(data, width) > threshold:
                break
            silent += _CHUNK_MS
        return silent


def apply_lead_in(current_path: str, delta_ms: int) -> LeadInResult:
    """在 `current_path` 目前的狀態上再加（正）／減（負）`delta_ms` 的開頭空白。

    永遠從**原檔**重新產生，不在已處理過的檔案上疊加處理，所以來回調不會累積誤差。
    剪掉的量超過原檔開頭能剪的（整個原檔長度）會夾住。
    """
    from .wav_process import process_wav

    original, current_total = split_lead_name(current_path)
    if not os.path.isfile(original):
        # 原檔不在（例如只拿到處理過的那份）：就以目前這份當原檔
        original, current_total = current_path, 0
    delta = int(delta_ms)
    total = current_total + delta
    length = wav_length_ms(original)
    if total < 0:
        total = max(total, -int(length))
    applied = total - current_total

    cut_sound = 0
    if total < 0:
        cut_sound = max(0, -total - leading_silence_ms(original, limit_ms=-total + _CHUNK_MS))

    target = lead_name(original, total)
    if total != 0 and target != current_path:
        tmp = target + '.part'
        process_wav(original, tmp, offset_ms=total)
        os.replace(tmp, target)
    elif total != 0 and not os.path.isfile(target):
        process_wav(original, target, offset_ms=total)
    return LeadInResult(target, total, applied, cut_sound)


def describe(total_ms: int) -> Optional[str]:
    if total_ms > 0:
        return '開頭加了 %d ms 空白' % total_ms
    if total_ms < 0:
        return '開頭剪掉 %d ms' % -total_ms
    return None
