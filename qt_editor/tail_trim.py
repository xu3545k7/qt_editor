"""修剪結尾空白：最後一顆音符（和最後有聲音的地方）之後不該再空好幾秒。

遊戲是走到譜面的 `music_finish_time_msec` 才結束（GameManager.autoStopOnChartEnd），
所以曲終設得比實際內容晚，玩家就得對著空畫面等它跑完才進結算。實測這份曲庫
106 首裡中位數是 0，但有幾首多了 3～8 秒（彩云追月 8.5 秒最長）。

這裡量兩件事：
- 譜面：所有難度裡最後一顆音符的結束時間
- 音訊：從檔尾往回找，最後一段有聲音的位置（無背景音樂的曲子跳過這步，
  它的音訊本來就是整段無聲）

兩者取晚的那個，加上一小段收尾（預設 1.5 秒）就是新的曲終；音訊也可以跟著剪短
（原檔先備份到 `<曲庫>_editor/audio_backup/`）。
"""

from __future__ import annotations

import audioop
import json
import os
import shutil
import time
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .ui_text import tr

#: 超過這麼久沒有東西才算「奇怪的空白」
DEFAULT_THRESHOLD_MS = 3000
#: 修剪後留多少收尾（長押尾巴、殘響）
DEFAULT_TAIL_MS = 1500
#: 剪進還有音樂的地方時淡出多久
DEFAULT_FADE_MS = 400
#: 判斷「還有聲音」的音量門檻（16-bit 峰值；約 -35 dBFS）
SILENCE_FLOOR = 600
WINDOW_MS = 50

Progress = Optional[Callable[[int, int, str], object]]


@dataclass
class TailReport:
    folder: str
    title: str
    charts: List[str] = field(default_factory=list)
    audio: str = ''
    finish_ms: int = 0
    last_note_ms: int = 0
    audible_ms: int = 0
    audio_ms: int = 0
    no_background: bool = False

    @property
    def content_ms(self) -> int:
        """最後真的還有東西的時間。"""
        return int(max(self.last_note_ms, self.audible_ms))

    @property
    def blank_ms(self) -> int:
        """曲終前面那段什麼都沒有的時間。"""
        return int(self.finish_ms - self.content_ms)

    @property
    def audio_blank_ms(self) -> int:
        """音訊檔尾巴多出來的那段（曲終之後、或最後的聲音之後）。

        遊戲走到曲終就結束，所以這段玩的時候聽不到；但它會讓檔案變大，也會讓
        Hiraeth 輸出過不了「譜面長度和音訊差 ≤ 2 秒」那條檢查。
        """
        if not self.audio_ms:
            return 0
        return int(self.audio_ms - max(self.content_ms, self.finish_ms))

    @property
    def note_blank_ms(self) -> int:
        """最後一顆音符之後還要等多久才結束（尾奏也算在裡面）。

        這段不是「沒聲音」，是「沒事做」：音樂還在放，但已經沒有音符了。要不要
        剪掉是品味問題（有些曲子的尾奏就是要聽完），所以預設只顯示、不修。
        """
        return int(self.finish_ms - self.last_note_ms)

    @property
    def worst_blank_ms(self) -> int:
        return max(self.blank_ms, self.audio_blank_ms)


def chart_last_note(path: str) -> Dict[str, int]:
    """(曲終, 最後一顆音符的結束時間)。讀 JSON 就好，不用建整個模型。"""
    with open(path, encoding='utf-8-sig') as fh:
        data = json.load(fh)
    last = 0
    for note in data.get('notes') or []:
        for key in ('endTime', 'end_timing_msec', 'end'):
            if key in note:
                last = max(last, int(note.get(key) or 0))
                break
    return {'finish': int(data.get('music_finish_time_msec') or 0), 'last': last}


def audio_tail(path: str, floor: int = SILENCE_FLOOR) -> Dict[str, int]:
    """(音訊長度, 最後有聲音的時間)，毫秒。"""
    with wave.open(path, 'rb') as w:
        rate, width, frames = w.getframerate(), w.getsampwidth(), w.getnframes()
        total = int(frames / rate * 1000.0)
        step = max(1, int(rate * WINDOW_MS / 1000.0))
        audible = 0
        pos = frames
        while pos > 0:
            start = max(0, pos - step)
            w.setpos(start)
            chunk = w.readframes(pos - start)
            if chunk and audioop.max(chunk, width) > floor:
                audible = int(pos / rate * 1000.0)
                break
            pos = start
    return {'total': total, 'audible': audible}


def scan_song(song: Any) -> TailReport:
    """一首歌的結尾狀況。音訊讀不了就只看譜面。"""
    report = TailReport(folder=song.folder, title=song.title)
    for diff in song.difficulties:
        chart = diff.chart or ''
        if chart.lower().endswith('.json') and os.path.isfile(chart):
            try:
                marks = chart_last_note(chart)
            except (OSError, ValueError):
                continue
            report.charts.append(chart)
            report.finish_ms = max(report.finish_ms, marks['finish'])
            report.last_note_ms = max(report.last_note_ms, marks['last'])
        if diff.no_background:
            report.no_background = True
        if not report.audio and diff.audio:
            report.audio = diff.audio
    if report.audio and report.audio.lower().endswith('.wav') and os.path.isfile(report.audio) \
            and not report.no_background:
        try:
            tail = audio_tail(report.audio)
        except (OSError, wave.Error, audioop.error):
            tail = {'total': 0, 'audible': 0}
        report.audio_ms = tail['total']
        report.audible_ms = tail['audible']
    return report


def scan_library(lib: Any, progress: Progress = None,
                 threshold_ms: int = DEFAULT_THRESHOLD_MS,
                 include_outro: bool = False) -> List[TailReport]:
    """整個曲庫裡結尾空白超過門檻的曲目，空白長的排前面。

    `include_outro` 連「最後一顆音符之後」（尾奏）也算成空白。
    """
    songs = lib.songs()
    found = []
    for i, song in enumerate(songs):
        if progress is not None and progress(i, len(songs), song.title) is False:
            break
        report = scan_song(song)
        worst = max(report.worst_blank_ms, report.note_blank_ms) if include_outro \
            else report.worst_blank_ms
        if report.charts and worst >= int(threshold_ms):
            found.append(report)
    if progress is not None:
        progress(len(songs), len(songs), '')
    key = ((lambda r: -max(r.worst_blank_ms, r.note_blank_ms)) if include_outro
           else (lambda r: -r.worst_blank_ms))
    found.sort(key=key)
    return found


BACKUP_ROOT = 'tail_trim_backup'


def backup_dir(lib: Any) -> str:
    """這次修剪的原檔放哪。

    曲庫的「復原上一步」只還原 register.json 與索引，**不會**還原譜面檔和音訊，
    所以動它們之前要自己留一份。
    """
    return os.path.join(lib.editor_dir, BACKUP_ROOT, time.strftime('%Y%m%d-%H%M%S'))


def _keep_copy(path: str, folder: str) -> None:
    """動檔案之前先複製一份原檔到備份資料夾。"""
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, os.path.basename(path))
    if not os.path.exists(target):
        shutil.copy2(path, target)


def fade_out(pcm: bytes, width: int, channels: int, rate: int, fade_ms: int) -> bytes:
    """最後一段淡出。剪到還有聲音的地方時不淡出會「啪」一聲。"""
    import audioop as _audioop
    frame = width * channels
    length = min(len(pcm), int(rate * fade_ms / 1000.0) * frame)
    length -= length % frame
    if length <= 0:
        return pcm
    head, tail = pcm[:len(pcm) - length], pcm[len(pcm) - length:]
    steps = 16
    chunk = (length // steps // frame) * frame or frame
    faded = b''
    for i in range(0, length, chunk):
        piece = tail[i:i + chunk]
        gain = max(0.0, 1.0 - (i + len(piece) / 2.0) / length)
        faded += _audioop.mul(piece, width, gain)
    return head + faded


def trim_audio(path: str, keep_ms: int, backup: str = '', fade_ms: int = 0) -> int:
    """把音訊剪到 keep_ms（原檔先備份）。回傳剪掉幾毫秒。"""
    with wave.open(path, 'rb') as w:
        rate, width, channels, frames = w.getframerate(), w.getsampwidth(), w.getnchannels(), w.getnframes()
        keep = min(frames, max(1, int(rate * keep_ms / 1000.0)))
        if keep >= frames:
            return 0
        w.setpos(0)
        pcm = w.readframes(keep)
    if fade_ms:
        pcm = fade_out(pcm, width, channels, rate, fade_ms)
    _keep_copy(path, backup)
    temp = path + '.tmp'
    with wave.open(temp, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(temp, path)
    return int((frames - keep) / rate * 1000.0)


def set_chart_finish(path: str, finish_ms: int) -> bool:
    """改譜面的曲終（只動這個欄位，其他原封不動）。"""
    with open(path, encoding='utf-8-sig') as fh:
        data = json.load(fh)
    if int(data.get('music_finish_time_msec') or 0) <= int(finish_ms):
        return False
    data['music_finish_time_msec'] = int(finish_ms)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(temp, path)
    return True


def trim_song(lib: Any, report: TailReport, tail_ms: int = DEFAULT_TAIL_MS,
              trim_audio_file: bool = True, backup: str = '',
              cut_outro: bool = False, fade_ms: int = DEFAULT_FADE_MS) -> Dict[str, Any]:
    """把一首歌的結尾收乾淨（曲終往前收、音訊尾巴剪掉）。回傳做了什麼。

    `cut_outro` 連最後一顆音符之後的尾奏也剪掉（音樂會在那裡淡出）。
    """
    base = report.last_note_ms if cut_outro else report.content_ms
    target = int(base) + int(tail_ms)
    # 曲終只會往前收，不會被拉長：本來就收得剛好的譜面不要動到
    finish = min(report.finish_ms, target) if report.finish_ms else target
    if report.audio_ms:
        finish = min(finish, report.audio_ms)
    # 音訊至少留到曲終，不然遊戲最後幾秒沒有聲音
    keep = max(finish, target)
    changed = {'folder': report.folder, 'finish_ms': finish, 'charts': 0, 'audio_ms': 0}
    keep_dir = os.path.join(backup, report.folder) if backup else ''
    for chart in report.charts:
        try:
            _keep_copy(chart, keep_dir)
            if set_chart_finish(chart, finish):
                changed['charts'] += 1
        except (OSError, ValueError):
            continue
    if trim_audio_file and report.audio and report.audio.lower().endswith('.wav') \
            and os.path.isfile(report.audio) and report.audio_ms > keep + 500:
        try:
            # 剪到還有音樂的地方就淡出，剪掉的本來就是靜音就不用
            fade = fade_ms if keep < report.audible_ms else 0
            changed['audio_ms'] = trim_audio(report.audio, keep, keep_dir, fade)
        except (OSError, wave.Error):
            changed['audio_ms'] = 0
    return changed


def trim_all(lib: Any, reports: List[TailReport], tail_ms: int = DEFAULT_TAIL_MS,
             trim_audio_file: bool = True, progress: Progress = None,
             cut_outro: bool = False, fade_ms: int = DEFAULT_FADE_MS) -> List[Dict[str, Any]]:
    """整批修剪。動到的曲目會通知遊戲重整。"""
    if not reports:
        return []
    lib._snapshot(tr('修剪結尾空白（%d 首）') % len(reports), [r.folder for r in reports])
    backup = backup_dir(lib)
    done = []
    for i, report in enumerate(reports):
        if progress is not None and progress(i, len(reports), report.title) is False:
            break
        done.append(trim_song(lib, report, tail_ms, trim_audio_file, backup,
                              cut_outro=cut_outro, fade_ms=fade_ms))
    if progress is not None:
        progress(len(reports), len(reports), '')
    if done:
        lib.notify_game(done[-1]['folder'])
    return done
