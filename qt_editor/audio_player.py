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


_AUDIOOP_MUL = None
_AUDIOOP_TRIED = False


def _audioop_add():
    """`audioop.add`，取不到就回 None。"""
    mul = _audioop_mul()
    if mul is None:
        return None
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            import audioop
        return audioop.add
    except Exception:                           # noqa: BLE001
        return None


def _audioop_mul():
    """`audioop.mul`，取不到就回 None（3.13 之後 audioop 已被移除）。"""
    global _AUDIOOP_MUL, _AUDIOOP_TRIED
    if not _AUDIOOP_TRIED:
        _AUDIOOP_TRIED = True
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                import audioop
            _AUDIOOP_MUL = audioop.mul
        except Exception:                       # noqa: BLE001
            _AUDIOOP_MUL = None
    return _AUDIOOP_MUL


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

        # 音效裝置的輸出延遲：把 buffer 交給後端之後，實際發聲還要再等一小段。
        # current_ms() 回報的是「現在聽得到的位置」，所以要把這段扣掉，否則
        # 判定線會固定跑在聲音前面。單位 ms，由使用者校正（見播放偏移對話框）。
        self._output_latency_ms: float = 0.0

        # simpleaudio play object
        self._sa_obj = None
        self._sa_objs = []  # support multiple play objects when dual-source

        # 現成 PCM 播放（播放 MIDI：不需要載入 WAV）
        self._pcm_buf:       Optional[bytes] = None
        self._pcm_raw:       Optional[bytes] = None   # 未套音量的原始 PCM
        self._pcm_raw_origin_ms: float = 0.0          # `_pcm_raw` 對應的起始時間
        self._pcm_volume:    float = 1.0
        self._pcm_rate:      int = 0
        self._pcm_channels:  int = 0
        self._pcm_sampwidth: int = 0
        self._pcm_origin_ms: float = 0.0

        # 音量 (0.0 ~ 1.0)
        self._volume: float = 1.0
        # optional second audio volume
        self._volume2: float = 1.0
        # Optional pre-rendered MIDI preview. It is mixed into the primary
        # stream so song and piano share one sample clock.
        self._preview_pcm: Optional[bytes] = None
        self._preview_start_ms: float = 0.0
        self._preview_rate: int = 0
        self._preview_channels: int = 0
        self._preview_sampwidth: int = 0
        self._preview_volume: float = 1.0

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

    def set_preview_overlay(
        self,
        pcm: bytes,
        start_ms: float,
        sample_rate: int,
        volume: float = 1.0,
    ) -> bool:
        """Install stereo PCM16 generated by the MIDI preview synthesizer."""
        if (
            not pcm
            or self.audio_sampwidth != 2
            or self.audio_channels not in (1, 2)
            or int(sample_rate) != int(self.audio_rate)
        ):
            self.clear_preview_overlay()
            return False

        if self.audio_channels == 1:
            pcm = self._stereo16_to_mono16(pcm)

        self._preview_pcm = bytes(pcm)
        self._preview_start_ms = float(start_ms)
        self._preview_rate = int(sample_rate)
        self._preview_channels = int(self.audio_channels)
        self._preview_sampwidth = 2
        self._preview_volume = max(0.0, min(1.0, float(volume)))
        return True

    def clear_preview_overlay(self, restart: bool = False) -> None:
        self._preview_pcm = None
        self._preview_start_ms = 0.0
        self._preview_rate = 0
        self._preview_channels = 0
        self._preview_sampwidth = 0
        if restart:
            self.refresh_output()

    def set_preview_volume(self, volume: float) -> None:
        self._preview_volume = max(0.0, min(1.0, float(volume)))
        if self._playing:
            if not hasattr(self, '_vol_timer'):
                self._vol_timer = QTimer(self)
                self._vol_timer.setSingleShot(True)
                self._vol_timer.timeout.connect(self._apply_volume_restart)
            self._vol_timer.start(120)

    def refresh_output(self) -> None:
        """Restart only the backend at its current position."""
        self._apply_volume_restart()

    def _apply_volume_restart(self) -> None:
        """debounce 到期後，若正在播放則從當前位置以新音量重啟。

        兩個以前會「靜音但看起來還在播」的洞：

        1. **PCM 模式（播放 MIDI 鋼琴）沒有被處理**。那個模式的聲音在 `_pcm_buf`
           裡，而這裡卻去 `_prepare_segments()` 切歌曲 WAV；沒載入歌曲時切出來是
           空的，於是後端被停掉之後就再也沒有被啟動——沒有聲音，但 `_playing`
           還是 True、判定線繼續跑。
        2. **重啟失敗時沒有收尾**。切不出東西就什麼都不做，留下一個沒有聲音的
           殭屍狀態。現在改成乾脆停下來（會發 `playback_stopped`），至少讓人
           知道發生了什麼事。
        """
        if not self._playing or self._paused:
            return
        cur = self.current_ms()
        if cur is None or cur >= self._play_end_ms:
            return

        # ── PCM 模式：用新音量重算原始 PCM，從目前位置接下去 ──────────
        if self._pcm_buf is not None:
            raw = self._pcm_raw
            if not raw:
                return                      # 沒有原始資料就別動它，繼續播
            self._stop_backend()
            try:
                data = self._apply_volume(raw, float(self._volume),
                                          int(self._pcm_sampwidth))
            except Exception:               # noqa: BLE001
                data = raw
            bps = max(1, self._pcm_channels * self._pcm_sampwidth)
            # 位移一定要從**原始 PCM 自己的起點**算。用 `_pcm_origin_ms` 的話，
            # 第一次改音量之後它就被更新成當下位置，第二次再改就會從錯誤的地方
            # 切下去（聽起來像是跳回去一段）。
            offset_ms = max(0.0, cur - self._pcm_raw_origin_ms)
            start_byte = int(offset_ms / 1000.0 * self._pcm_rate) * bps
            start_byte = max(0, min(len(data), start_byte))
            tail = data[start_byte:]
            self._pcm_volume = float(self._volume)
            if not tail or not self._start_pcm_backend(tail):
                self._fail_stop()
                return
            self._play_start_ms = cur
            self._pcm_origin_ms = cur
            self._pcm_buf = tail
            self._wall_start = time.perf_counter()
            return

        # ── 一般（歌曲 WAV）模式 ──────────────────────────────────────
        self._stop_backend()
        segs, primary_scaled = self._prepare_segments(cur, self._play_end_ms)
        if not segs:
            self._fail_stop()
            return
        self._play_start_ms = cur
        self._start_backend(segs, primary_volume_applied=primary_scaled)
        self._wall_start    = time.perf_counter()   # 同 play()：起跑後才蓋章

    def _fail_stop(self) -> None:
        """重啟失敗時把狀態收乾淨，不要留下「沒聲音卻還在播」的殭屍。"""
        self._playing = False
        self._paused = False
        self._timer.stop()
        self._stop_backend()
        self.playback_stopped.emit()

    @staticmethod
    def _apply_volume(pcm: bytes, volume: float, sampwidth: int) -> bytes:
        """對 PCM raw bytes 套用音量縮放（只支援 8bit / 16bit）。

        用 `audioop.mul`（C 實作）而不是逐取樣的 Python 迴圈。舊版對一首四分鐘
        的立體聲歌曲要跑 **3.2 秒**，而且按播放、拉音量滑桿、換播放位置都會重跑
        一次（`_apply_volume_restart` 會整段重新準備）——那就是「按下播放會卡住
        好幾秒」的來源。audioop 版本 0.058 秒，輸出逐位元組相同。

        16-bit 直接 mul；8-bit 是無號的，要先減 128 轉成有號再縮放、加回去。
        audioop 不在的話（3.13 之後被移除）退回原本的 Python 迴圈。
        """
        if volume >= 0.999:
            return pcm
        if sampwidth == 2:
            mul = _audioop_mul()
            if mul is not None:
                try:
                    return mul(pcm, 2, float(volume))
                except Exception:               # noqa: BLE001
                    pass
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
        self._pcm_buf = None      # 改播歌曲音訊，離開 MIDI-only 模式
        segs, primary_scaled = self._prepare_segments(start_ms, end_ms)
        if not segs:
            return

        self._play_start_ms  = start_ms
        self._play_end_ms    = end_ms
        self._last_start_ms  = start_ms
        self._last_end_ms    = end_ms
        self._playing        = True
        self._paused         = False
        self._paused_at_ms   = 0.0

        # _wall_start 一定要在 backend 真的開始之後才蓋章。_start_backend 裡還有
        # 一次 per-sample 的音量處理（音量 <100% 時是純 Python 迴圈，整首歌可能
        # 跑好幾百毫秒甚至更久），先蓋章的話這段時間會被算成「已經播過了」，
        # 判定線一開播就先跳掉一大段，之後整首都對不上音樂。
        self._start_backend(segs, primary_volume_applied=primary_scaled)
        self._wall_start     = time.perf_counter()
        self._timer.start()

    def play_oneshot_pcm(self, pcm: bytes, rate: int,
                         channels: int = 2, sampwidth: int = 2) -> bool:
        """射後不理地疊播一小段 PCM，不影響正在進行的播放。

        給「播放中新放置的音符」用——主播放是一次 render 好的整段音訊，
        中途加的音符不在裡面，所以另外開一路把那一顆放出來。
        """
        if not pcm or sa is None or rate <= 0:
            return False
        try:
            sa.play_buffer(pcm, channels, sampwidth, rate)
            return True
        except Exception:                       # noqa: BLE001
            return False

    def play_pcm(
        self,
        pcm: bytes,
        rate: int,
        channels: int,
        sampwidth: int,
        start_ms: float,
        end_ms: float,
        volume: float = 1.0,
    ) -> bool:
        """直接播放一段現成的 PCM（不需要載入 WAV）。

        給「播放 MIDI」用：把譜面算成鋼琴音之後直接送去播，位置回報仍然
        用譜面時間，所以判定線、跟隨捲動都照常運作。
        """
        if not pcm or rate <= 0 or channels <= 0 or sampwidth <= 0:
            return False
        self.stop()
        try:
            data = self._apply_volume(pcm, float(volume), int(sampwidth))
        except Exception:
            data = pcm

        self._pcm_buf        = data
        # 原始（未套音量）的那份要留著：改音量時要從它重算，拿已經縮過的
        # `_pcm_buf` 再乘一次會越乘越小。
        self._pcm_raw        = pcm
        self._pcm_raw_origin_ms = float(start_ms)
        self._pcm_volume     = float(volume)
        self._pcm_rate       = int(rate)
        self._pcm_channels   = int(channels)
        self._pcm_sampwidth  = int(sampwidth)
        self._play_start_ms  = float(start_ms)
        self._play_end_ms    = float(end_ms)
        self._last_start_ms  = float(start_ms)
        self._last_end_ms    = float(end_ms)
        self._pcm_origin_ms  = float(start_ms)

        if not self._start_pcm_backend(data):
            self._pcm_buf = None
            return False

        self._wall_start   = time.perf_counter()
        self._playing      = True
        self._paused       = False
        self._paused_at_ms = 0.0
        self._timer.start()
        return True

    def _start_pcm_backend(self, data: bytes) -> bool:
        if _HAS_SA:
            try:
                wave_obj = sa.WaveObject(
                    data,
                    num_channels=self._pcm_channels,
                    bytes_per_sample=self._pcm_sampwidth,
                    sample_rate=self._pcm_rate,
                )
                self._sa_objs = [wave_obj.play()]
                return True
            except Exception:
                pass
        if _HAS_WS:
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
                tmp_path = tmp.name
                with wave.open(tmp, 'wb') as wf:
                    wf.setnchannels(self._pcm_channels)
                    wf.setsampwidth(self._pcm_sampwidth)
                    wf.setframerate(self._pcm_rate)
                    wf.writeframes(data)
                tmp.close()
                winsound.PlaySound(  # type: ignore
                    tmp_path, winsound.SND_FILENAME | winsound.SND_ASYNC
                )
                cleanup_timer = QTimer(self)
                cleanup_timer.setSingleShot(True)
                cleanup_timer.timeout.connect(lambda p=tmp_path: _try_delete(p))
                cleanup_timer.start(
                    int(max(0.0, self._play_end_ms - self._play_start_ms) + 2000)
                )
                return True
            except Exception:
                pass
        return False

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
        if self._pcm_buf is not None:
            self._resume_pcm()
            return
        self.play(self._paused_at_ms, self._play_end_ms)

    def _resume_pcm(self) -> None:
        """MIDI-only 播放的續播：直接從暫停處切 PCM 繼續。"""
        buf = self._pcm_buf
        if not buf:
            return
        resume_ms = float(self._paused_at_ms)
        frame_sz = max(1, self._pcm_channels * self._pcm_sampwidth)
        offset = int((resume_ms - self._pcm_origin_ms) * self._pcm_rate / 1000.0) * frame_sz
        offset = max(0, min(len(buf), offset - offset % frame_sz))
        tail = buf[offset:]
        if not tail:
            self.stop()
            return
        self._stop_backend()
        if not self._start_pcm_backend(tail):
            return
        self._play_start_ms = resume_ms
        self._wall_start    = time.perf_counter()
        self._playing       = True
        self._paused        = False
        self._timer.start()

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

    def set_output_latency_ms(self, latency_ms: float) -> None:
        """設定音效裝置輸出延遲補償（ms）。"""
        self._output_latency_ms = max(0.0, float(latency_ms))

    def output_latency_ms(self) -> float:
        return self._output_latency_ms

    def current_ms(self) -> Optional[float]:
        """目前**聽得到**的播放位置（ms）。

        用 perf_counter 而不是 time.time()：Windows 上 time.time() 的解析度只有
        ~15.6ms，拿來推判定線會一格一格跳、而且和音訊起點對不準。
        再扣掉裝置輸出延遲，讓回報的位置是耳朵聽到的位置而不是送出的位置。
        """
        if self._paused:
            return float(self._paused_at_ms)
        if not self._playing:
            return None
        elapsed = (time.perf_counter() - self._wall_start) * 1000.0
        return self._play_start_ms + elapsed - self._output_latency_ms

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

    def _prepare_segments(self, start_ms: float, end_ms: float):
        primary = self._slice(start_ms, end_ms)
        if not primary:
            return [], False

        primary_scaled = False
        preview = self._slice_preview(start_ms, end_ms)
        if preview is not None:
            if len(preview) < len(primary):
                preview += b'\x00' * (len(primary) - len(preview))
            elif len(preview) > len(primary):
                preview = preview[:len(primary)]
            song = self._apply_volume(primary, self._volume, self.audio_sampwidth)
            piano = self._apply_volume(
                preview, self._preview_volume, self._preview_sampwidth
            )
            primary = self._mix_pcm_add(song, piano, self.audio_sampwidth)
            primary_scaled = True

        segments = [primary]
        if self.audio2_bytes is not None:
            secondary = self._slice2(start_ms, end_ms)
            if secondary:
                segments.append(secondary)
        return segments, primary_scaled

    def _start_backend(self, pcm: bytes, primary_volume_applied: bool = False) -> None:
        # pcm may be a list of segments (for dual-source) or single bytes
        segs = pcm if isinstance(pcm, (list, tuple)) else [pcm]
        # apply volume per segment (primary uses main audio params)
        out_segs = []
        for i, s in enumerate(segs):
            if s is None:
                continue
            if i == 0:
                sw = self.audio_sampwidth
                vol = 1.0 if primary_volume_applied else self._volume
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
        if end_ms <= start_ms:
            return None
        bps = self.audio_channels * self.audio_sampwidth
        lead_ms = max(0.0, min(end_ms, 0.0) - start_ms)
        lead_frames = int(lead_ms / 1000.0 * self.audio_rate)
        lead = b'\x00' * (lead_frames * bps)
        s_ms = max(0.0, start_ms)
        audio_end_ms = max(0.0, end_ms)
        sb = int(s_ms / 1000.0 * self.audio_rate) * bps
        eb = int(audio_end_ms / 1000.0 * self.audio_rate) * bps
        eb = min(len(self.audio_bytes), eb)
        body = self.audio_bytes[sb:eb] if eb > sb else b''
        out = lead + body
        return out or None

    def _slice2(self, start_ms: float, end_ms: float) -> Optional[bytes]:
        """Slice secondary audio buffer using its own audio params."""
        if self.audio2_bytes is None or self.audio2_rate <= 0:
            return None
        if end_ms <= start_ms:
            return None
        bps = self.audio2_channels * self.audio2_sampwidth
        lead_ms = max(0.0, min(end_ms, 0.0) - start_ms)
        lead_frames = int(lead_ms / 1000.0 * self.audio2_rate)
        lead = b'\x00' * (lead_frames * bps)
        s_ms = max(0.0, start_ms)
        audio_end_ms = max(0.0, end_ms)
        sb = int(s_ms / 1000.0 * self.audio2_rate) * bps
        eb = int(audio_end_ms / 1000.0 * self.audio2_rate) * bps
        eb = min(len(self.audio2_bytes), eb)
        body = self.audio2_bytes[sb:eb] if eb > sb else b''
        out = lead + body
        return out or None

    def _slice_preview(self, start_ms: float, end_ms: float) -> Optional[bytes]:
        if (
            self._preview_pcm is None
            or self._preview_rate <= 0
            or self._preview_channels != self.audio_channels
            or self._preview_sampwidth != self.audio_sampwidth
            or end_ms <= start_ms
        ):
            return None

        bytes_per_frame = self._preview_channels * self._preview_sampwidth
        frame_count = max(
            0, int((float(end_ms) - float(start_ms)) * self._preview_rate / 1000.0)
        )
        if frame_count <= 0:
            return None

        source_frame = int(
            (float(start_ms) - self._preview_start_ms)
            * self._preview_rate
            / 1000.0
        )
        destination_frame = 0
        if source_frame < 0:
            destination_frame = min(frame_count, -source_frame)
            source_frame = 0

        source_frames_total = len(self._preview_pcm) // bytes_per_frame
        copy_frames = min(
            frame_count - destination_frame,
            max(0, source_frames_total - source_frame),
        )
        out = bytearray(frame_count * bytes_per_frame)
        if copy_frames > 0:
            source_byte = source_frame * bytes_per_frame
            destination_byte = destination_frame * bytes_per_frame
            byte_count = copy_frames * bytes_per_frame
            out[destination_byte:destination_byte + byte_count] = (
                self._preview_pcm[source_byte:source_byte + byte_count]
            )
        return bytes(out)

    @staticmethod
    def _stereo16_to_mono16(pcm: bytes) -> bytes:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            import audioop
        return audioop.tomono(pcm, 2, 0.5, 0.5)

    @staticmethod
    def _mix_pcm_add(pcm1: bytes, pcm2: bytes, sampwidth: int) -> bytes:
        """Add PCM without reducing the song when the preview is silent."""
        if sampwidth != 2:
            return pcm1
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            import audioop
        return audioop.add(pcm1, pcm2, 2)

    def _mix_pcm(self, pcm1: bytes, pcm2: bytes, sampwidth: int) -> bytes:
        """Mix two PCM byte streams with same sampwidth (8/16-bit).

        雙音源播放走這條。和 `_apply_volume` 同一個問題：逐取樣的 Python 迴圈，
        一首四分鐘的歌要好幾秒。16-bit 改用 audioop（各乘 0.5 再相加，和原本的
        `a1/2 + a2/2` 一樣），拿不到 audioop 才退回迴圈。
        """
        import array as _arr
        if sampwidth == 2:
            mul = _audioop_mul()
            add = _audioop_add()
            if mul is not None and add is not None:
                try:
                    n = min(len(pcm1), len(pcm2))
                    n -= n % 2
                    return add(mul(pcm1[:n], 2, 0.5), mul(pcm2[:n], 2, 0.5), 2)
                except Exception:               # noqa: BLE001
                    pass
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
