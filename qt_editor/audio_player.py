"""
audio_player.py
===============
WAV 播放引擎，使用 QThread 計時以避免阻塞 UI。

公開 API
--------
AudioPlayer(parent)
    .load_wav(path) -> bool
    .play(start_ms, end_ms)
    .pause()
    .resume()
    .stop(hold_ms=None)
    .restart()

Signals
    position_changed(float)  -- 目前播放位置 (ms)，每 ~16ms 發送一次
    playback_stopped()       -- 自然播完或手動停止
"""

from __future__ import annotations

import os
import tempfile
import time
import wave
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

# 後端支援偵測
try:
    import simpleaudio as sa
    _HAS_SA = True
except Exception:
    sa = None  # type: ignore
    _HAS_SA = False

try:
    import winsound
    _HAS_WS = True
except Exception:
    winsound = None  # type: ignore
    _HAS_WS = False


class AudioPlayer(QObject):
    """WAV 播放器（simpleaudio 優先，fallback winsound）。"""

    position_changed = pyqtSignal(float)   # ms
    playback_stopped  = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        # WAV 資料
        self.audio_bytes:   Optional[bytes] = None
        self.audio_channels: int = 0
        self.audio_sampwidth: int = 0
        self.audio_rate:    int = 0
        self.audio_frames:  int = 0
        self.audio_path:    Optional[str] = None
        # optional second audio (for dual-source playback)
        self.audio2_bytes:   Optional[bytes] = None
        self.audio2_channels: int = 0
        self.audio2_sampwidth: int = 0
        self.audio2_rate:    int = 0
        self.audio2_frames:  int = 0
        self.audio2_path:    Optional[str] = None

        # 播放狀態
        self._playing:   bool = False
        self._paused:    bool = False
        self._play_start_ms: float = 0.0
        self._play_end_ms:   float = 0.0
        self._wall_start:    float = 0.0
        self._paused_at_ms:  float = 0.0
        self._last_start_ms: float = 0.0
        self._last_end_ms:   float = 0.0

        # simpleaudio play object
        self._sa_obj = None
        self._sa_objs = []  # support multiple play objects when dual-source

        # 音量 (0.0 ~ 1.0)
        self._volume: float = 1.0
        # optional second audio volume
        self._volume2: float = 1.0

        # QTimer 用於定期發送位置訊號
        self._timer = QTimer(self)
        self._timer.setInterval(16)   # ~60 FPS
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------
    # WAV 載入
    # ------------------------------------------------------------------

    def load_wav(self, path: str) -> bool:
        try:
            with wave.open(path, 'rb') as wf:
                self.audio_channels  = wf.getnchannels()
                self.audio_sampwidth = wf.getsampwidth()
                self.audio_rate      = wf.getframerate()
                self.audio_frames    = wf.getnframes()
                self.audio_bytes     = wf.readframes(self.audio_frames)
            self.audio_path = path
            return True
        except Exception:
            return False

    def load_wavs(self, paths: list) -> bool:
        """Load primary and optional secondary WAV files for dual playback.
        `paths` can be a list of 1 or 2 file paths. Returns True if primary loaded.
        """
        ok = False
        if not paths:
            return False
        p0 = paths[0]
        try:
            with wave.open(p0, 'rb') as wf:
                self.audio_channels  = wf.getnchannels()
                self.audio_sampwidth = wf.getsampwidth()
                self.audio_rate      = wf.getframerate()
                self.audio_frames    = wf.getnframes()
                self.audio_bytes     = wf.readframes(self.audio_frames)
            self.audio_path = p0
            ok = True
        except Exception:
            self.audio_bytes = None
            self.audio_path = None
            ok = False

        # secondary
        if len(paths) > 1 and paths[1]:
            p1 = paths[1]
            try:
                with wave.open(p1, 'rb') as wf:
                    self.audio2_channels  = wf.getnchannels()
                    self.audio2_sampwidth = wf.getsampwidth()
                    self.audio2_rate      = wf.getframerate()
                    self.audio2_frames    = wf.getnframes()
                    self.audio2_bytes     = wf.readframes(self.audio2_frames)
                self.audio2_path = p1
            except Exception:
                self.audio2_bytes = None
                self.audio2_path = None
        else:
            self.audio2_bytes = None
            self.audio2_path = None

        return ok

    def is_loaded(self) -> bool:
        return self.audio_bytes is not None

    # ------------------------------------------------------------------
    # 音量
    # ------------------------------------------------------------------

    def set_volume(self, volume: float) -> None:
        """設定播放音量，0.0（靜音）到 1.0（原始音量）。
        使用 debounce：停止拖動 300ms 後才重啟後端，避免高頻呼叫 native audio crash。"""
        self._volume = max(0.0, min(1.0, volume))
        # debounce timer：每次更新都重置，停止拖動後才真正重啟
        if not hasattr(self, '_vol_timer'):
            self._vol_timer = QTimer(self)
            self._vol_timer.setSingleShot(True)
            self._vol_timer.timeout.connect(self._apply_volume_restart)
        self._vol_timer.start(300)

    def set_volume2(self, volume: float) -> None:
        """設定第二音源音量（若載入第二音源）。"""
        self._volume2 = max(0.0, min(1.0, volume))
        if not hasattr(self, '_vol_timer'):
            self._vol_timer = QTimer(self)
            self._vol_timer.setSingleShot(True)
            self._vol_timer.timeout.connect(self._apply_volume_restart)
        self._vol_timer.start(300)

    def _apply_volume_restart(self) -> None:
        """debounce 到期後，若正在播放則從當前位置以新音量重啟。"""
        if not self._playing:
            return
        cur = self.current_ms()
        if cur is None or cur >= self._play_end_ms:
            return
        self._stop_backend()
        seg = self._slice(cur, self._play_end_ms)
        if seg:
            self._play_start_ms = cur
            self._wall_start    = time.time()
            # start backend for primary (and secondary when present)
            segs = [seg]
            if self.audio2_bytes is not None:
                seg2 = self._slice2(cur, self._play_end_ms)
                if seg2:
                    segs.append(seg2)
            self._start_backend(segs)

    @staticmethod
    def _apply_volume(pcm: bytes, volume: float, sampwidth: int) -> bytes:
        """對 PCM raw bytes 套用音量縮放（只支援 8bit / 16bit）。"""
        if volume >= 0.999:
            return pcm
        import array as _arr
        if sampwidth == 2:
            a = _arr.array('h', pcm)
            for i in range(len(a)):
                a[i] = max(-32768, min(32767, int(a[i] * volume)))
            return bytes(a)
        if sampwidth == 1:  # unsigned 8-bit
            a = _arr.array('B', pcm)
            for i in range(len(a)):
                a[i] = max(0, min(255, int((a[i] - 128) * volume + 128)))
            return bytes(a)
        return pcm

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------

    def play(self, start_ms: float, end_ms: float) -> None:
        if not self.is_loaded():
            return
        self.stop()
        seg = self._slice(start_ms, end_ms)
        if not seg:
            return

        self._play_start_ms  = start_ms
        self._play_end_ms    = end_ms
        self._last_start_ms  = start_ms
        self._last_end_ms    = end_ms
        self._wall_start     = time.time()
        self._playing        = True
        self._paused         = False
        self._paused_at_ms   = 0.0

        # prepare backend segments (support optional secondary audio)
        segs = [seg]
        if self.audio2_bytes is not None:
            seg2 = self._slice2(start_ms, end_ms)
            if seg2:
                segs.append(seg2)
        self._start_backend(segs)
        self._timer.start()

    def pause(self) -> None:
        if not self._playing:
            return
        cur = self.current_ms()
        self._stop_backend()
        self._paused        = True
        self._playing       = False
        self._paused_at_ms  = cur if cur is not None else self._play_start_ms
        self._timer.stop()
        self.position_changed.emit(self._paused_at_ms)

    def resume(self) -> None:
        if not self._paused:
            return
        self.play(self._paused_at_ms, self._play_end_ms)

    def stop(self, hold_ms: Optional[float] = None) -> None:
        self._stop_backend()
        self._playing = False
        self._paused  = False
        self._timer.stop()
        if hold_ms is not None:
            self.position_changed.emit(float(hold_ms))
        self.playback_stopped.emit()

    def restart(self) -> None:
        if self._last_end_ms > self._last_start_ms:
            self.play(self._last_start_ms, self._last_end_ms)

    # ------------------------------------------------------------------
    # 查詢
    # ------------------------------------------------------------------

    def current_ms(self) -> Optional[float]:
        if self._paused:
            return float(self._paused_at_ms)
        if not self._playing:
            return None
        return self._play_start_ms + (time.time() - self._wall_start) * 1000.0

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # 內部 - 計時 tick
    # ------------------------------------------------------------------

    def _on_tick(self) -> None:
        if not self._playing:
            return
        cur = self.current_ms()
        if cur is None:
            return
        if cur >= self._play_end_ms:
            final = self._play_end_ms
            self._stop_backend()
            self._playing = False
            self._timer.stop()
            self.position_changed.emit(final)
            self.playback_stopped.emit()
            return
        self.position_changed.emit(cur)

    # ------------------------------------------------------------------
    # 內部 - 後端
    # ------------------------------------------------------------------

    def _start_backend(self, pcm: bytes) -> None:
        # pcm may be a list of segments (for dual-source) or single bytes
        segs = pcm if isinstance(pcm, (list, tuple)) else [pcm]
        # apply volume per segment (primary uses main audio params)
        out_segs = []
        for i, s in enumerate(segs):
            if s is None:
                continue
            if i == 0:
                sw = self.audio_sampwidth
                vol = self._volume
            else:
                sw = self.audio2_sampwidth or self.audio_sampwidth
                vol = getattr(self, '_volume2', self._volume)
            out_segs.append(self._apply_volume(s, vol, sw))

        if _HAS_SA:
            try:
                # create and play multiple WaveObjects
                self._sa_objs = []
                for i, s in enumerate(out_segs):
                    if i == 0:
                        nch = self.audio_channels
                        rate = self.audio_rate
                        ss = self.audio_sampwidth
                    else:
                        nch = self.audio2_channels or self.audio_channels
                        rate = self.audio2_rate or self.audio_rate
                        ss = self.audio2_sampwidth or self.audio_sampwidth
                    wave_obj = sa.WaveObject(
                        s,
                        num_channels=nch,
                        bytes_per_sample=ss,
                        sample_rate=rate,
                    )
                    self._sa_objs.append(wave_obj.play())
                return
            except Exception:
                # fall through to mixing fallback
                pass

        # fallback: if only one segment, use winsound as before; if multiple,
        # try to mix into single PCM with primary audio params
        if _HAS_WS:
            if len(out_segs) == 1:
                self._winsound_play(out_segs[0])
            else:
                try:
                    mixed = self._mix_pcm(out_segs[0], out_segs[1], self.audio_sampwidth)
                    self._winsound_play(mixed)
                except Exception:
                    # last resort: play primary only
                    self._winsound_play(out_segs[0])

    def _stop_backend(self) -> None:
        if _HAS_SA:
            try:
                if hasattr(self, '_sa_objs') and self._sa_objs:
                    for o in list(self._sa_objs):
                        try:
                            o.stop()
                        except Exception:
                            pass
                    self._sa_objs = []
                if self._sa_obj is not None:
                    try:
                        self._sa_obj.stop()
                    except Exception:
                        pass
                    self._sa_obj = None
            except Exception:
                pass
        if _HAS_WS:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)  # type: ignore
            except Exception:
                pass

    def _winsound_play(self, pcm: bytes) -> None:
        if not _HAS_WS:
            return
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            tmp_path = tmp.name
            with wave.open(tmp, 'wb') as wf:
                wf.setnchannels(self.audio_channels)
                wf.setsampwidth(self.audio_sampwidth)
                wf.setframerate(self.audio_rate)
                wf.writeframes(pcm)
            tmp.close()
            winsound.PlaySound(tmp_path, winsound.SND_FILENAME | winsound.SND_ASYNC)  # type: ignore
            # 簡易清理：過了播放長度後刪除（2 秒寬裕）
            delay = int((self._play_end_ms - self._play_start_ms) + 2000)
            cleanup_timer = QTimer(self)
            cleanup_timer.setSingleShot(True)
            cleanup_timer.timeout.connect(lambda p=tmp_path: _try_delete(p))
            cleanup_timer.start(delay)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 切片
    # ------------------------------------------------------------------

    def _slice(self, start_ms: float, end_ms: float) -> Optional[bytes]:
        if self.audio_bytes is None or self.audio_rate <= 0:
            return None
        s_ms = max(0.0, start_ms)
        e_ms = max(s_ms, end_ms)
        bps  = self.audio_channels * self.audio_sampwidth
        sb   = int(s_ms / 1000.0 * self.audio_rate) * bps
        eb   = int(e_ms / 1000.0 * self.audio_rate) * bps
        eb   = min(len(self.audio_bytes), eb)
        return self.audio_bytes[sb:eb] if eb > sb else None

    def _slice2(self, start_ms: float, end_ms: float) -> Optional[bytes]:
        """Slice secondary audio buffer using its own audio params."""
        if self.audio2_bytes is None or self.audio2_rate <= 0:
            return None
        s_ms = max(0.0, start_ms)
        e_ms = max(s_ms, end_ms)
        bps = self.audio2_channels * self.audio2_sampwidth
        sb = int(s_ms / 1000.0 * self.audio2_rate) * bps
        eb = int(e_ms / 1000.0 * self.audio2_rate) * bps
        eb = min(len(self.audio2_bytes), eb)
        return self.audio2_bytes[sb:eb] if eb > sb else None

    def _mix_pcm(self, pcm1: bytes, pcm2: bytes, sampwidth: int) -> bytes:
        """Mix two PCM byte streams with same sampwidth (8/16-bit)."""
        import array as _arr
        if sampwidth == 2:
            a1 = _arr.array('h', pcm1)
            a2 = _arr.array('h', pcm2)
            # length align
            n = min(len(a1), len(a2))
            res = _arr.array('h', [0]) * n
            for i in range(n):
                v = int(a1[i] / 2 + a2[i] / 2)
                if v > 32767:
                    v = 32767
                if v < -32768:
                    v = -32768
                res[i] = v
            return bytes(res)
        if sampwidth == 1:
            a1 = _arr.array('B', pcm1)
            a2 = _arr.array('B', pcm2)
            n = min(len(a1), len(a2))
            res = _arr.array('B', [0]) * n
            for i in range(n):
                v = int(((a1[i] - 128) + (a2[i] - 128)) / 2 + 128)
                if v < 0:
                    v = 0
                if v > 255:
                    v = 255
                res[i] = v
            return bytes(res)
        # default: return first
        return pcm1


def _try_delete(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass
