"""用 sounddevice（PortAudio）做出一個和 simpleaudio 同樣介面的播放後端。

為什麼是這個形狀：播放器（`audio_player.py`）本來只認得兩個後端——
simpleaudio 和 winsound。winsound 在 macOS 不存在，simpleaudio 又沒有
Apple Silicon 的輪子、原始碼在新版 Python 上編不起來，所以 Mac 會變成
完全沒有聲音。

與其把播放器改寫一遍（那是整個播放、暫停、混音、雙音源的邏輯），不如提供
一個介面一模一樣的替身：`WaveObject(data, ...).play()` 回傳一個有 `stop()`
的物件。播放器那邊一行都不用改。

sounddevice 的輪子是 universal2（Intel 與 Apple Silicon 都有），PortAudio
也允許同時開多條輸出串流，雙音源（歌曲＋鋼琴）照樣同時播。
"""

from __future__ import annotations

from typing import Optional

try:
    import sounddevice as _sd
except Exception:                               # noqa: BLE001
    _sd = None


#: bytes_per_sample -> sounddevice 的 dtype
_DTYPES = {1: 'int8', 2: 'int16', 4: 'int32'}


def available() -> bool:
    """這台機器能不能用這個後端（有裝套件、而且真的找得到輸出裝置）。"""
    if _sd is None:
        return False
    try:
        return bool(_sd.query_devices(kind='output'))
    except Exception:                           # noqa: BLE001
        # 查不到裝置不代表不能播（有些平台要開了才知道），讓它試
        return True


class PlayObject:
    """對應 simpleaudio 的 PlayObject：可以 stop()、可以問還在不在播。"""

    def __init__(self, data: bytes, channels: int, sampwidth: int, rate: int):
        self._data = memoryview(bytes(data))
        self._frame_bytes = max(1, int(channels) * int(sampwidth))
        self._pos = 0
        self._done = False
        dtype = _DTYPES.get(int(sampwidth))
        if dtype is None:
            raise ValueError('unsupported sample width: %r' % (sampwidth,))
        self._stream = _sd.RawOutputStream(
            samplerate=int(rate),
            channels=int(channels),
            dtype=dtype,
            callback=self._callback,
            finished_callback=self._finished,
        )
        self._stream.start()

    # PortAudio 執行緒：這裡面不要做會配置記憶體或拿鎖的事
    def _callback(self, outdata, frames, _time, _status) -> None:
        want = frames * self._frame_bytes
        chunk = self._data[self._pos:self._pos + want]
        got = len(chunk)
        outdata[:got] = chunk
        self._pos += got
        if got < want:
            outdata[got:] = b'\x00' * (want - got)
            raise _sd.CallbackStop

    def _finished(self) -> None:
        self._done = True

    def is_playing(self) -> bool:
        if self._done:
            return False
        try:
            return bool(self._stream.active)
        except Exception:                       # noqa: BLE001
            return False

    def stop(self) -> None:
        self._done = True
        try:
            self._stream.abort(ignore_errors=True)
        except Exception:                       # noqa: BLE001
            pass
        try:
            self._stream.close(ignore_errors=True)
        except Exception:                       # noqa: BLE001
            pass

    def wait_done(self) -> None:
        import time
        while self.is_playing():
            time.sleep(0.01)


class WaveObject:
    """對應 simpleaudio 的 WaveObject（參數名稱也一樣，才能直接替換）。"""

    def __init__(self, audio_data: bytes, num_channels: int = 2,
                 bytes_per_sample: int = 2, sample_rate: int = 44100):
        self.audio_data = audio_data
        self.num_channels = int(num_channels)
        self.bytes_per_sample = int(bytes_per_sample)
        self.sample_rate = int(sample_rate)

    def play(self) -> PlayObject:
        return PlayObject(self.audio_data, self.num_channels,
                          self.bytes_per_sample, self.sample_rate)


def stop_all() -> None:
    """對應 simpleaudio.stop_all()。"""
    if _sd is None:
        return
    try:
        _sd.stop()
    except Exception:                           # noqa: BLE001
        pass


def backend_name() -> Optional[str]:
    return 'sounddevice' if available() else None
