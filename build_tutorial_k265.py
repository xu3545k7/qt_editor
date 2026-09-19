"""新手教學：初階課、進階課，各自有單課和照順序串起來的全課程。

每一課是 UserSongs 裡的一首歌（分類「新手教學」），只有 keysound、沒有伴奏：

* 從 MIDI 取指定小節，換成該課的慢速 BPM 重新排時間，拍號沿用來源（2/4、4/4、3/4）。
* 每一課分成「示範段」和「遊玩段」，段與段之間留空讓玩家讀說明。
  有反覆的就用反覆，沒有的就把同一段再放一次。
* **不刪音**：看不見的音藏進寄主（`hidden` + `_sub_host`），打到寄主時照自己的
  時間發聲，所以聽起來永遠是完整的曲子。
* **初階**（K.265 小星星變奏曲）：點擊、左右手、節奏、和弦、長押、滑奏（自製的白鍵刮奏，
  不取曲子）、顫音、跳躍、交叉手、慢板踏板、演奏會。
* **進階**：音階（K.545，右手左手各一次）、左手快速音群、琶音（K.265）、斷奏（普羅高菲夫
  第七號奏鳴曲，比賽現場演奏的 MIDI，照秒數取段）、同音連打、
  快速跳躍、快速和弦跳躍（李斯特《鐘》）、強弱控制（拉威爾《Scarbo》，保留原曲的極端力度，
  自動用演奏會模式判強弱）、高速八度（聖桑
  《骷髏之舞》雙鋼琴版，右手、左手、兩手各一次）。主練的手全部音都要打。
* `新手教學初階00`／`新手教學進階00` 是全課程：單課照順序接成一份譜面，每一課保留
  自己的速度和拍號。
* 段落資訊寫在每課資料夾的 `tutorial.json`，遊戲端 `TutorialSession` 讀它：示範段
  自動彈、不計分，右下角顯示說明；給人看的譜面在 `Normal/source/*.xml`。

用法（在 qt_editor/ 底下）：

    python build_tutorial_k265.py C:/path/k265.mid ../Nostalgia-clone/UserSongs \
        "C:/path/K545 Piano Sonata.mid" "C:/path/Dance-Macabre-2.mid" "C:/path/la-campanella (2).mid" \
        "C:/path/Piano E-Competition MIDI_2002_Vsevolod Dvorkine_Prokofiev - Sonata No. 7, Op. 83.mid" \
        "C:/path/scarbo-maurice-ravel-gaspard-de-la-nuit.mid" \
        "C:/path/etude-op25-no11-in-a-minor-winter-wind-f-chopin.mid"
"""
from __future__ import annotations

import bisect
import copy
import json
import shutil
import math
import os
import sys
import wave
from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace
from typing import Callable, Dict, List, Sequence, Tuple

import mido

from qt_editor import difficulty as D
from qt_editor.models import (
    GNote, NoteModel, _trill_sub_from_note, lane_center_to_width3_range,
    midi_pitch_to_game_lane_index,
)

import generate_pedal
import tutorial_cover

CATEGORY = '新手教學'
AUTHOR = 'W. A. Mozart'
TPB = 480
BAR_TICKS = 960            # 2/4
SNAP_TICKS = 20            # 來源有 59/119 這種差一 tick 的起音，吸到 20 tick 格
# 開頭和段與段之間的空白用**秒數**訂，換算成整小節。
# 每一段前面：說明框 → 敲三下 → 開始（使用者定的順序）。空白 = 說明的閱讀時間 + 三拍，
# 湊成整小節。閱讀時間和遊戲端 TutorialOverlay 用同一個公式。
READ_BASE_MS = 500
READ_PER_CHAR_MS = 65
READ_MIN_MS = 1200
READ_MAX_MS = 3000
COUNT_IN_BEATS = 3
#: 一拍超過這個長度（慢板）就改用半拍敲，免得三下又拖太久。
COUNT_IN_MAX_BEAT_MS = 800


def read_ms(text: str) -> int:
    return int(max(READ_MIN_MS, min(READ_MAX_MS, READ_BASE_MS + len(text or '') * READ_PER_CHAR_MS)))
TAIL_BARS = 2
LONG_MIN_TICKS = 400       # 允許長押的課程裡，接近四分音符以上才算長押（MIDI 的音比記譜略短）
HOLD_TAIL_ADVANCE_MS = 40  # 和「長押長度修整」的尾端前移同一個預設值
#: 來源裡按多短算斷奏（換算成每拍 TPB 的 tick；132 BPM 下約 150ms）。
STACCATO_SOURCE_TICKS = 160
#: 斷奏音符寫進譜面的長度：放開不能晚於這個時間太多。
STACCATO_MS = 120
VELOCITY_MIDDLE = 80       # 每一課的力度中位數對齊到這裡
VELOCITY_CONTRAST = 0.6    # 和中位數的差距保留幾成（強弱還在，但不會從 30 跳到 127）
VELOCITY_RANGE = (45, 110)


RH, LH = 0, 1


# ----------------------------------------------------------------------
# MIDI
# ----------------------------------------------------------------------
class Raw:
    __slots__ = ('start', 'end', 'pitch', 'hand', 'velocity', 'bar', 'track', 'pos')

    def __init__(self, start, end, pitch, hand, velocity, bar_ticks=BAR_TICKS, track=0):
        self.start, self.end, self.pitch = start, end, pitch
        self.hand, self.velocity = hand, velocity
        self.bar = start // bar_ticks + 1        # 1-based
        self.track = track                       # 來源 MIDI 的第幾軌
        self.pos = start % bar_ticks             # 小節內的位置（格線判斷用）

    @property
    def dur(self) -> int:
        return self.end - self.start


def _snap(tick: int) -> int:
    return int(round(tick / SNAP_TICKS)) * SNAP_TICKS


def glissando_notes(source: dict) -> List[Raw]:
    """自製的滑奏練習：右手沿白鍵從 C4 一路滑到 C6，下一小節再滑回來。

    滑奏課要教的就是「按住、一口氣滑過去」這個動作，曲子裡的音階句子會夾著別的音，
    反而看不出來。自己寫一條乾淨的來回最直接（使用者的要求）。每一節都有音高，滑過去
    聽起來就是一串刮奏。
    """
    bar_ticks = source['bar_ticks']
    step = source['step_ticks']
    whites = [p for p in range(source['low'], source['high'] + 1) if p % 12 in (0, 2, 4, 5, 7, 9, 11)]
    out: List[Raw] = []
    for bar in range(1, source['bars'] + 1):
        run = whites if bar % 2 else list(reversed(whites))
        base = (bar - 1) * bar_ticks
        for index, pitch in enumerate(run):
            start = base + index * step
            raw = Raw(start, start + step, pitch, RH, 80, bar_ticks)
            raw.pos = start - base
            out.append(raw)
    return out


def read_performance(path: str, source: dict) -> List[Raw]:
    """現場演奏錄下來的 MIDI（Piano e-Competition）：一軌、沒有小節、時間是真人的節奏。

    時間照秒數換成 tick（假設原速每分鐘 `src_bpm` 拍），段落用秒數指定。分手靠同一刻
    的音：兩顆以上同時按的，在音程最大的空隙分開（兩顆就是低的左手）；單音照音高分
    （C4 以上右手）。
    """
    mid = mido.MidiFile(path)
    per_second = source['src_bpm'] / 60.0 * TPB
    now = 0.0
    active: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
    notes = []
    for msg in mid:
        now += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            active[msg.note].append((now, msg.velocity))
        elif msg.type in ('note_off', 'note_on') and active[msg.note]:
            start, vel = active[msg.note].pop(0)
            notes.append((start, now, msg.note, vel))
    notes.sort()
    groups: List[List[tuple]] = []
    for note in notes:
        if groups and note[0] - groups[-1][0][0] < 0.03:
            groups[-1].append(note)
        else:
            groups.append([note])
    out: List[Raw] = []
    bar_ticks = source['bar_ticks']
    for group in groups:
        # 三顆以上：在音程最大的那個空隙把兩手分開（B1 B2 | A3 D4）。兩顆：低的左手。
        pitches = sorted(n[2] for n in group)
        split_at = pitches[1] if len(pitches) >= 2 else pitches[0]
        if len(pitches) >= 3:
            # 剛好一個八度的空隙多半是同一隻手撐開的八度（左手 B1+B2），打個折扣。
            gaps = [((b - a) - (3 if b - a == 12 else 0), b) for a, b in zip(pitches, pitches[1:])]
            split_at = max(gaps)[1]
        # 真人演奏兩手常差 10–20ms 才下去；同一刻的音對齊到最早那顆，譜面上才是同時的一下。
        together = group[0][0]
        for start, end, pitch, vel in group:
            end = max(end, together + 0.02)
            start = together
            if len(group) >= 2:
                hand = LH if pitch < split_at else RH
            else:
                hand = RH if pitch >= 60 else LH
            while pitch > 108:
                pitch -= 12
            while pitch < 21:
                pitch += 12
            s0 = int(round(start * per_second))
            e0 = max(s0 + SNAP_TICKS, int(round(end * per_second)))
            raw = Raw(s0, e0, pitch, hand, vel, bar_ticks)
            raw.pos = s0 % TPB
            out.append(raw)
    out.sort(key=lambda n: (n.start, n.hand, n.pitch))
    return out


def midi_bar_starts(path: str) -> List[int]:
    """照 MIDI 裡的拍號事件切小節，回傳每一小節開頭的 tick（已換算成每拍 TPB）。

    拍號在曲子中間變來變去的檔案（《鐘》有 2/8、4/8，後段還有 49/4、193/4 這種把
    華彩樂段塞成一小節的寫法）不能用固定長度去數，否則小節號會一路錯下去。
    """
    mid = mido.MidiFile(path)
    scale = TPB / float(mid.ticks_per_beat)
    changes = []
    for track in mid.tracks:
        now = 0
        for msg in track:
            now += msg.time
            if msg.type == 'time_signature':
                changes.append((now, msg.numerator, msg.denominator))
    changes.sort()
    if not changes or changes[0][0] > 0:
        changes.insert(0, (0, 4, 4))
    end = max(sum(msg.time for msg in track) for track in mid.tracks)
    starts, tick = [], 0
    while tick < end:
        current = [c for c in changes if c[0] <= tick][-1]
        starts.append(tick)
        tick += current[1] * mid.ticks_per_beat * 4 // current[2]
        following = [c[0] for c in changes if c[0] > starts[-1]]
        if following and tick > following[0]:
            tick = following[0]            # 拍號在小節中間換：新拍號從那裡算起
    starts.append(end)
    # 和音符一樣吸到 SNAP_TICKS 格：這份檔案中段的拍號事件落在 110910 這種奇數 tick，
    # 小節起點會比吸附後的音符晚 5 tick，小節第一拍的音就被算進前一小節。
    return [_snap(int(round(t * scale))) for t in starts]


def read_midi(path: str, bar_ticks: int = BAR_TICKS, split=None,
              origin: Tuple[int, int] = (0, 1), bar_starts: List[int] = None) -> List[Raw]:
    """讀 MIDI，tick 一律換算成每拍 `TPB`。

    兩軌的檔案第一條有音符的軌是右手；其他檔案要給
    `split(pitch, bar, 長度 tick, 軌號) -> hand`，回傳 None 的音不要（打擊樂之類）。

    `origin = (tick, 小節號)`：這個 tick 是那一小節的開頭，之後每 `bar_ticks` 一小節。
    `bar_starts`（`midi_bar_starts` 的結果）優先：照拍號事件逐小節算。
    """
    mid = mido.MidiFile(path)
    scale = TPB / float(mid.ticks_per_beat)
    out: List[Raw] = []
    note_tracks = [(i, t) for i, t in enumerate(mid.tracks)
                   if any(m.type == 'note_on' and m.velocity > 0 for m in t)]
    if split is None:
        assert len(note_tracks) == 2, '預期右手、左手兩軌'
    for index, (track_no, track) in enumerate(note_tracks):     # 第一條 = 右手
        now = 0
        active: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for msg in track:
            now += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note].append((now, msg.velocity))
            elif msg.type in ('note_off', 'note_on') and active[msg.note]:
                start, vel = active[msg.note].pop(0)          # 先進先出
                s, e = _snap(int(round(start * scale))), _snap(int(round(now * scale)))
                # 管弦／雙鋼琴編制的 MIDI 會超出 88 鍵（骷髏之舞最高到 G8）。存檔時鋼琴音域
                # 外的音會被處理掉，掛在它身上的隱藏音就沒了寄主、被迫變回看得見的音。
                # 超出的整個八度移進來。
                pitch = msg.note
                while pitch > 108:
                    pitch -= 12
                while pitch < 21:
                    pitch += 12
                if bar_starts is not None:
                    bar = bisect.bisect_right(bar_starts, s)
                else:
                    bar = int((s - origin[0] * scale) // bar_ticks + origin[1])
                hand = split(pitch, bar, e - s, track_no) if split is not None else index
                if hand is None:
                    continue
                raw = Raw(s, max(e, s + SNAP_TICKS), pitch, hand, vel, bar_ticks, track_no)
                raw.bar = bar
                # 格線要看小節內的位置，不是全曲的絕對 tick：拍號不規則的檔案小節起點
                # 不在整拍上（《鐘》中段），拿絕對 tick 取餘數一顆都選不到。
                if bar_starts is not None:
                    raw.pos = s - bar_starts[bar - 1]
                else:
                    raw.pos = s - (int(round(origin[0] * scale)) + (bar - origin[1]) * bar_ticks)
                out.append(raw)
    # 同一刻、同一手、同一個音重複記了好幾次（《冬風》第 5 小節的 F7 一次三顆）：只留最長的。
    # 不清掉的話三顆疊在同一個鍵道上，玩家按一下只吃得到一顆。
    longest: Dict[Tuple[int, int, int], Raw] = {}
    for raw in out:
        key = (raw.start, raw.hand, raw.pitch)
        if key not in longest or raw.dur > longest[key].dur:
            longest[key] = raw
    out = list(longest.values())
    out.sort(key=lambda n: (n.start, n.hand, n.pitch))
    return out


# ----------------------------------------------------------------------
# 選音規則：回傳「要打的」音符
# ----------------------------------------------------------------------
Rule = Callable[[List[Raw]], List[Raw]]


def _onsets(notes: Sequence[Raw]) -> Dict[Tuple[int, int], List[Raw]]:
    groups: Dict[Tuple[int, int], List[Raw]] = defaultdict(list)
    for n in notes:
        groups[(n.hand, n.start)].append(n)
    return groups


def top_voice(notes: Sequence[Raw], hand: int, grid: int = 0,
              min_dur: int = 0, tracks=None) -> List[Raw]:
    """該手每個起音只留最高音；`grid` > 0 時只留落在格線上的起音；`tracks` 限定來源軌。"""
    out = []
    for (h, start), group in _onsets(notes).items():
        if h != hand or (grid and group[0].pos % grid):
            continue
        if tracks is not None:
            group = [n for n in group if n.track in tracks]
            if not group:
                continue
        best = max(group, key=lambda n: n.pitch)
        if best.dur >= min_dur:
            out.append(best)
    return out


def octaves(notes: Sequence[Raw], hand: int, tracks=None) -> List[Raw]:
    """八度課：每個起音留最高音，和它下面剛好一個八度的那顆（有的話）。

    來源常常是三個八度疊在一起（A5+A4+A3），一隻手撐不了兩個八度，只取上面那一對。
    """
    out = []
    for (h, _start), group in _onsets(notes).items():
        if h != hand:
            continue
        if tracks is not None:
            group = [n for n in group if n.track in tracks]
        if not group:
            continue
        top = max(group, key=lambda n: n.pitch)
        out.append(top)
        below = [n for n in group if n.pitch == top.pitch - 12]
        if below:
            out.append(below[0])
    return out


def every(notes: Sequence[Raw], hand: int) -> List[Raw]:
    """該手的每一顆，含和弦內聲部和裝飾音（進階課主練的那隻手）。"""
    return [n for n in notes if n.hand == hand]


def chords(notes: Sequence[Raw], hand: int, cap: int = 3) -> List[Raw]:
    """該手每個起音留最外側的 `cap` 顆（和弦課用）。"""
    out = []
    for (h, _start), group in _onsets(notes).items():
        if h != hand:
            continue
        group = sorted(group, key=lambda n: -n.pitch)
        out.extend(group[:cap])
    return out


def running_or_chords(notes: Sequence[Raw], hand: int, cap: int = 3,
                      busy: int = 6, grid: int = 480) -> List[Raw]:
    """一小節裡這隻手起音 >= `busy` 個就是跑動（只留格線上的最高音），否則留和弦。"""
    by_bar: Dict[int, List[Raw]] = defaultdict(list)
    for n in notes:
        if n.hand == hand:
            by_bar[n.bar].append(n)
    out = []
    for bar_notes in by_bar.values():
        if len({n.start for n in bar_notes}) >= busy:
            out.extend(top_voice(bar_notes, hand, grid))
        else:
            out.extend(chords(bar_notes, hand, cap))
    return out


def melody(notes: Sequence[Raw], grid: int = 240, accent: int = 95) -> List[Raw]:
    """旋律的形狀：兩手合起來看，每個格線上的起音只留最高的一顆。

    格線之間的強音（突然砸下去的和弦）也留：強弱課要的就是那一下。和格線上的音
    靠得太近（半個格線以內）的就不留，免得兩下擠在一起。
    """
    by_start: Dict[int, List[Raw]] = defaultdict(list)
    for n in notes:
        by_start[n.start].append(n)
    chosen: Dict[int, Raw] = {}
    for start, group in by_start.items():
        if group[0].pos % grid == 0:
            chosen[start] = max(group, key=lambda n: n.pitch)
    for start, group in sorted(by_start.items()):
        if start in chosen or max(n.velocity for n in group) < accent:
            continue
        if all(abs(start - other) >= grid // 2 for other in chosen):
            chosen[start] = max(group, key=lambda n: n.pitch)
    return list(chosen.values())


def rule_union(*rules: Rule) -> Rule:
    def run(notes: List[Raw]) -> List[Raw]:
        seen, out = set(), []
        for rule in rules:
            for n in rule(notes):
                if id(n) not in seen:
                    seen.add(id(n))
                    out.append(n)
        return out
    return run


def _k545_hands(pitch: int, bar: int, length: int, track: int = 0) -> int:
    """K.545 的 MIDI 只有一軌。只用到兩段音階，兩段各自一條分界就分得乾淨：

    * 呈示部右手音階（第 5–10 小節）：右手從 D4 往上跑，左手的和弦在 C4 以下；
      G4 以下、一拍以上的長音是左手的伴奏（第 5 小節開頭那顆 F4）。
    * 再現部左手音階（MIDI 第 78–81 小節）：左手在 F4 以下跑，右手的和弦在 C5 以上。
    """
    if bar < 60:
        if pitch < 67 and length >= TPB * 0.9:
            return LH
        return RH if pitch >= 62 else LH
    return RH if pitch >= 67 else LH


# 雙鋼琴版《骷髏之舞》：軌 1 = 鋼琴二左手、2 = 鋼琴二右手、3 = 鋼琴一右手、4 = 鋼琴一左手；
# 低音、定音鼓、鈸不是鋼琴的聲音，不收。
_DANSE_TRACK_HANDS = {1: LH, 2: RH, 3: RH, 4: LH}


def _danse_hands(pitch: int, bar: int, length: int, track: int):
    return _DANSE_TRACK_HANDS.get(track)


# 每一份來源 MIDI：一小節幾個 tick（換算成每拍 TPB 之後）、一小節幾拍（也是譜面的拍號）。
SOURCES = {
    'k265': dict(bar_ticks=960, beats=2, split=None,
                 author='W. A. Mozart《小星星變奏曲》K.265'),
    'k545': dict(bar_ticks=1920, beats=4, split=_k545_hands,
                 author='W. A. Mozart《鋼琴奏鳴曲》K.545'),
    # 2/2，第一軌右手、第二軌左手。前 4 小節是慢板引子，第 5 小節起才是快板。
    'winterwind': dict(bar_ticks=1920, beats=4, split=None,
                       author='F. Chopin《練習曲》Op.25 No.11「冬風」'),
    # 這份 MIDI 的拍號事件寫 4/4，實際是 3/4 的圓舞曲：音型每 720 tick（原檔）一循環。
    'danse': dict(bar_ticks=1440, beats=3, split=_danse_hands,
                  author='C. Saint-Saëns《骷髏之舞》雙鋼琴版'),
    # 6/8：一小節六個八分音符＝三拍。拍號在曲中變好幾次（開頭 2/8、4/8，華彩段落
    # 49/4、193/4），小節號照 MIDI 的拍號事件逐小節算（bar_map），和樂譜、曲庫譜面一致。
    'campanella': dict(bar_ticks=1440, beats=3, split=None, bar_map=True,
                       author='F. Liszt《鐘》La Campanella'),
    # 自製的滑奏：不讀檔。一小節一趟（奇數小節往上、偶數往下），每一節隔 step_ticks。
    'glissando': dict(bar_ticks=1920, beats=4, split=None, generate=glissando_notes,
                      low=60, high=84, step_ticks=40, bars=4, author='滑奏練習（自製）'),
    # 3/8：一小節一拍半。`ts` 是寫進譜面的拍號（沒寫就是 beats/4）。
    'scarbo': dict(bar_ticks=720, beats=1.5, ts=(3, 8), split=None, bar_map=True,
                   author='M. Ravel《夜之加斯巴》Scarbo'),
    # 現場演奏，段落用「秒」指定。src_bpm 是演奏者大約的速度，只用來換算 tick。
    'prokofiev': dict(bar_ticks=1920, beats=4, split=None, performance=True, src_bpm=132,
                      author='S. Prokofiev《第七號鋼琴奏鳴曲》Op.83（V. Dvorkine 演奏）'),
}


# 音階課的兩手：主練的手全部音，另一隻手每拍一顆並鎖成長押；鍵道一個音級一格。
_SCALE_RH = dict(rule=rule_union(lambda ns: every(ns, RH), lambda ns: top_voice(ns, LH, grid=480)),
                 lock=LH, lanes=dict(kind='scale', hand=RH, region=(9, 27), width=4, other=(0, 7)))
_SCALE_LH = dict(rule=rule_union(lambda ns: every(ns, LH), lambda ns: top_voice(ns, RH, grid=480)),
                 lock=RH, lanes=dict(kind='scale', hand=LH, region=(0, 18), width=4, other=(20, 27)))


# 旋轉課（《冬風》）：主練的手每一顆都打，照音高擺——一上一下的鋸齒在畫面上就是左右小跳、
# 整串往一個方向走。寬 2：相鄰兩顆差 3~7 個半音，換算成 2 格以上不會疊。
_ROT_RH = dict(rule=rule_union(lambda ns: every(ns, RH), lambda ns: top_voice(ns, LH, grid=480)),
               lock=LH, lanes=dict(kind='linear', hand=RH, region=(8, 27), width=2, other=(0, 6)))
_ROT_LH = dict(rule=rule_union(lambda ns: every(ns, LH), lambda ns: top_voice(ns, RH, grid=480)),
               lock=RH, lanes=dict(kind='linear', hand=LH, region=(0, 19), width=2, other=(21, 27)))
_ROT_BOTH = dict(rule=rule_union(lambda ns: every(ns, RH), lambda ns: every(ns, LH)),
                 lanes=dict(kind='linear', hand=RH, region=(15, 27), width=2, other=(0, 12),
                            other_width=2))


# 八度課：主練的手每個起音一對八度（鍵道固定隔 5 格＝撐開的手型），另一隻手在拍點上鎖住。
_P1R, _P1L, _P2L = {3}, {4}, {1}
# `tracks`：這一段只收哪幾軌。雙鋼琴版兩台琴常常在同一個音域重複同一個和弦，全部收進來
# 就是每秒 57 個音、兩台琴的力度疊在一起——聽起來糊成一團還會爆音。每段只留主練的那台
# 琴和它需要的伴奏（左手段的八度在第二台琴，和弦留第一台）。
_OCT_RH = dict(rule=rule_union(lambda ns: octaves(ns, RH, _P1R),
                               lambda ns: top_voice(ns, LH, grid=480, tracks=_P1L | _P2L)),
               lock=LH, lanes=dict(kind='octave', hand=RH, region=(9, 27), width=3, other=(0, 7)),
               tracks=_P1R | _P1L)
_OCT_LH = dict(rule=rule_union(lambda ns: octaves(ns, LH, _P2L),
                               lambda ns: top_voice(ns, RH, grid=480, tracks=_P1R)),
               lock=RH, lanes=dict(kind='octave', hand=LH, region=(0, 18), width=3, other=(20, 27)),
               tracks=_P2L | _P1R | _P1L)
_OCT_BOTH = dict(rule=rule_union(lambda ns: octaves(ns, RH, _P1R),
                                 lambda ns: octaves(ns, LH, _P1L)),
                 lanes=dict(kind='octave', hand=RH, region=(14, 27), width=3, other=(0, 13),
                            other_kind='octave'),
                 tracks=_P1R | _P1L)


# 和弦跳躍課：和弦最多三顆（八度＋中間音），寬 2 的音符照音程排開。
_CHORD_LH = dict(rule=rule_union(lambda ns: chords(ns, LH, cap=3),
                                 lambda ns: top_voice(ns, RH, grid=480)),
                 lock=RH, lanes=dict(kind='chord', hand=LH, region=(0, 20), width=2,
                                     other=(22, 27)))
_CHORD_BOTH = dict(rule=rule_union(lambda ns: chords(ns, RH, cap=3),
                                   lambda ns: chords(ns, LH, cap=3)),
                   lanes=dict(kind='chord', hand=RH, region=(15, 27), width=2, other=(0, 12),
                              other_kind='chord', other_width=2))


# 強弱課兩段各自一把尺（音域差很多，共用的話第一段會被壓扁）。
_DYN_A = dict(lanes=dict(kind='contour', hand=RH, region=(1, 26), width=4, other=(0, 0)))
_DYN_B = dict(lanes=dict(kind='contour', hand=RH, region=(1, 26), width=4, other=(0, 0)))


def _segment_option(lesson: dict, index: int, key: str):
    """段落自己的設定優先（第 4 個欄位），沒有就用整課的。"""
    segment = lesson['segments'][index]
    if len(segment) > 3 and key in segment[3]:
        return segment[3][key]
    return lesson.get(key)


# ----------------------------------------------------------------------
# 課程表（照這個順序編號，全課程也照這個順序）
# ----------------------------------------------------------------------
# (tier, 顯示名稱, 資料夾前綴)
TIERS = [('basic', '初階', '新手教學初階'), ('advanced', '進階', '新手教學進階')]
# segments: (mode, [(第幾小節, 到第幾小節), ...], 說明)
LESSONS = [
    # ================= 初階 =================
    dict(tier='basic', key='tap', title='點擊與判定線', bpm=80, level=1,
         source='主題 第 1–16 小節',
         goal='音符碰到判定線的那一刻按下去。只有右手，左手的伴奏會自動響。',
         rule=lambda ns: top_voice(ns, RH, grid=240),
         # 只有右手：鍵道照音高直接擺（排譜器只看得到一隻手時會亂拆成兩手）。
         lanes=dict(kind='linear', hand=RH, region=(4, 23), width=4, other=(0, 1)),
         segments=[('demo', [(1, 8)], '先看：音符落到判定線時才按，早一點按也會等到線上才算。'),
                   ('play', [(9, 16)], '換你：同一段旋律，跟著剛才的節奏按。')]),
    dict(tier='basic', key='hands', title='紅藍左右手', bpm=80, level=1,
         source='主題 第 17–48 小節',
         goal='紅色是右手、藍色是左手。按音符寬度裡任何一個鍵都算。',
         rule=rule_union(lambda ns: top_voice(ns, RH, grid=240), lambda ns: top_voice(ns, LH)),
         segments=[('demo', [(17, 32)], '紅色音符給右手、藍色給左手，兩手一起進來。'),
                   ('play', [(33, 48)], '換你：兩手一起。按在音符寬度內任何一鍵都可以。')]),
    dict(tier='basic', key='alternate', title='左右交替', bpm=80, level=2,
         source='第 5 變奏 第 233–280 小節',
         goal='兩手輪流進來，拍子常落在反拍。等音符到線上再按，不要搶拍。',
         rule=rule_union(lambda ns: top_voice(ns, RH), lambda ns: top_voice(ns, LH)),
         segments=[('demo', [(233, 240)], '右手、左手你一句我一句，中間有休止。'),
                   ('play', [(241, 248)], '換你：前半段。'),
                   ('demo', [(249, 264)], '後半段兩手靠得更近。'),
                   ('play', [(265, 280)], '換你：後半段。')]),
    dict(tier='basic', key='chords', title='和弦', bpm=72, level=2,
         source='第 6 變奏 第 281–328 小節',
         goal='好幾顆同時按。只按音符上的鍵，整片亂壓在演奏會模式會被扣分。',
         rule=rule_union(lambda ns: running_or_chords(ns, RH),
                         lambda ns: running_or_chords(ns, LH)),
         segments=[('demo', [(281, 288)], '右手三音和弦，一次按下三個位置。'),
                   ('play', [(289, 296)], '換你：前半段。'),
                   ('demo', [(297, 312)], '後半段換左手按和弦、右手只按正拍。'),
                   ('play', [(313, 328)], '換你：後半段。')]),
    dict(tier='basic', key='holds', title='長押', bpm=72, level=3,
         source='第 8 變奏 第 377–424 小節',
         goal='長條要按住到尾端才放。太早放開只拿得到 Good。',
         rule=rule_union(lambda ns: top_voice(ns, RH),
                         lambda ns: top_voice(ns, LH, grid=240)),
         holds=True,
         segments=[('demo', [(377, 384)], '小調的長音：按下去，等尾端到線再放。'),
                   ('play', [(385, 392)], '換你：前半段。'),
                   ('demo', [(393, 408)], '後半段長音和左手的短音交錯。'),
                   ('play', [(409, 424)], '換你：後半段。')]),
    dict(tier='basic', key='slides', title='滑奏', bpm=80, level=3, midi='glissando',
         source='自製：右手白鍵 C4–C6 來回滑',
         goal='滑奏：按住不放，順著方向一口氣滑過去，不用一顆一顆按。',
         rule=lambda ns: every(ns, RH),
         slides=True,
         # 照音高擺：往上滑就是一路往右的一條斜線，滑回來就是往左。
         lanes=dict(kind='linear', hand=RH, region=(2, 27), width=3, other=(0, 1)),
         segments=[('demo', [(1, 4)], '從左滑到右，再從右滑回左：手指貼著鍵盤一路滑。'),
                   ('play', [(1, 4)], '換你：按住滑過去，再滑回來。')]),
    dict(tier='basic', key='trills', title='顫音', bpm=72, level=4,
         source='第 12 變奏 第 497–538 小節',
         goal='兩個相鄰的音快速來回。顫音音符按住後在範圍內左右交替按。',
         rule=rule_union(lambda ns: top_voice(ns, RH),
                         lambda ns: top_voice(ns, LH, grid=480)),
         holds=True, trills=True,
         segments=[('demo', [(497, 508)], '右手的顫音：在音符範圍裡兩根手指輪流按。'),
                   ('play', [(521, 532)], '換你：同一段旋律。'),
                   ('demo', [(509, 520)], '後半段兩手一起來回。'),
                   ('play', [(509, 520), (533, 538)], '換你：兩手顫音，接著收尾。')]),
    dict(tier='basic', key='leaps', title='跳躍', bpm=72, level=4,
         source='第 3 變奏 第 137–184 小節',
         goal='右手的三連音常常從高音一口氣掉下來。大跳時眼睛先看落點，手先移過去。',
         rule=rule_union(lambda ns: top_voice(ns, RH, min_dur=61),
                         lambda ns: top_voice(ns, LH, grid=480)),
         # 鍵道照音高等比例擺：掉一個八度就真的橫跨半個鍵盤，不讓排譜器壓扁。
         lanes=dict(kind='linear', hand=RH, region=(6, 27), width=3, other=(0, 5)),
         segments=[('demo', [(137, 144)], '右手從 E6 跳到 F5：音符橫跨一大段，先看落點。'),
                   ('play', [(145, 152)], '換你：前半段。'),
                   ('demo', [(153, 168)], '後半段高低來回跳。'),
                   ('play', [(169, 184)], '換你：後半段。')]),
    dict(tier='basic', key='crossing', title='交叉手', bpm=72, level=4,
         source='第 10 變奏 第 449–472 小節',
         goal='左手越過右手去彈高音。看顏色，不要看位置：藍色在右邊還是左手。',
         rule=rule_union(lambda ns: top_voice(ns, LH),
                         lambda ns: top_voice(ns, RH, grid=240)),
         holds=True,
         segments=[('demo', [(449, 456)], '藍色音符跑到紅色右邊了——那是左手跨過去。'),
                   ('play', [(449, 456)], '換你：同一段。'),
                   ('demo', [(457, 472)], '後半段左手在低音和高音之間來回跨。'),
                   ('play', [(457, 472)], '換你：後半段。')]),
    dict(tier='basic', key='adagio', title='慢板與踏板', bpm=40, level=5,
         source='第 11 變奏（慢板）第 473–496 小節',
         goal='慢板：長音、裝飾音和紫色的踏板音符。聲音的強弱也是表情的一部分。',
         rule=rule_union(lambda ns: top_voice(ns, RH, min_dur=31),
                         lambda ns: top_voice(ns, LH, grid=240)),
         holds=True, slides=True, pedal=True,
         segments=[('demo', [(473, 480)], '慢慢來：長音按住、快速下行用滑的，紫色是踏板。'),
                   ('play', [(489, 496)], '換你：同一段旋律再來一次。'),
                   ('demo', [(481, 488)], '中段的裝飾音很密，先聽它怎麼唱。'),
                   ('play', [(481, 488)], '換你：中段。')]),
    dict(tier='basic', key='recital', title='演奏會模式', bpm=76, level=6, recital=True,
         source='第 9 變奏 第 425–448 小節 ＋ 尾奏 第 539–549 小節',
         goal=('這一課自動用演奏會模式。評審看四項：完整度、旋律（不要一次壓三鍵以上）、'
               '失誤錯音（不要按到音符外）、情感與表情（強音大聲、弱音輕聲）。'),
         rule=rule_union(lambda ns: top_voice(ns, RH), lambda ns: top_voice(ns, LH)),
         holds=True, slides=True, pedal=True,
         segments=[('demo', [(425, 432)], '示範：每一顆都按在音符上、強弱照著來。'),
                   ('play', [(425, 448), (539, 549)], '畢業演奏：從頭彈到尾奏，評審正在聽。')]),
    # ================= 進階：主練的手全部音都要打 =================
    dict(tier='advanced', key='scales', title='音階', bpm=100, level=6, midi='k545',
         source='K.545 第一樂章 第 5–10 小節（右手）、第 50–53 小節（左手）',
         goal='進階：音階右手、左手各練一次。主練的手每一顆十六分音符都要打，另一隻手按住長音。',
         holds=True,
         segments=[('demo', [(5, 10)], '右手音階：一格一格往上、再一格一格下來，像走樓梯。', _SCALE_RH),
                   ('play', [(33, 38)], '換你：右手。', _SCALE_RH),
                   ('demo', [(78, 81)], '換左手：同樣的樓梯搬到低音區，右手按住和弦。', _SCALE_LH),
                   ('play', [(123, 126)], '換你：左手。', _SCALE_LH)]),
    dict(tier='advanced', key='left-runs', title='左手快速音群', bpm=100, level=6,
         source='第 2 變奏 第 97–136 小節',
         goal='進階：這次換左手跑完整的音群，右手每拍按住一個長音。',
         rule=rule_union(lambda ns: every(ns, LH),
                         lambda ns: top_voice(ns, RH, grid=480)),
         holds=True, lock=RH,
         segments=[('demo', [(97, 120)], '藍色的音群在低音區來回，眼睛看左邊。'),
                   ('play', [(121, 136)], '換你：左手跑、右手按住。')]),
    dict(tier='advanced', key='arpeggios', title='琶音', bpm=80, level=6,
         source='第 4 變奏 第 185–232 小節',
         goal='進階：左手每一顆琶音都要打（Do–Mi–Sol–Do–Mi），右手每拍按住一個長音。和音階比，每一步都是跳的。',
         rule=rule_union(lambda ns: every(ns, LH),
                         lambda ns: top_voice(ns, RH, grid=480)),
         holds=True, lock=RH,
         # 琶音要「跳」：每小節以最低音為起點，每一個音級 2 格——三度隔 4 格、八度隔 14 格。
         # 音階課是一個音級 1 格，兩課並排看一眼就分得出來。
         lanes=dict(kind='arpeggio', hand=LH, region=(0, 21), width=3, other=(22, 27)),
         segments=[('demo', [(185, 192)], '左手琶音：Do–Mi–Sol–Do–Mi 一路往上跳，每一步隔好幾格。'),
                   ('play', [(193, 200)], '換你：前半段。'),
                   ('demo', [(201, 216)], '後半段的琶音會先跳一個八度再往上。'),
                   ('play', [(217, 232)], '換你：後半段。')]),
    dict(tier='advanced', key='staccato', title='斷奏', bpm=100, level=7, midi='prokofiev',
         source='普羅高菲夫《第七號鋼琴奏鳴曲》第一樂章開頭（現場演奏 1.5–14.2 秒）',
         goal='進階：按下去馬上放開，聲音短而乾脆。斷奏音符按住不放會被判慢，早放沒關係。',
         rule=rule_union(lambda ns: every(ns, RH), lambda ns: every(ns, LH)),
         staccato=True, holds=True,
         # 兩手平行八度：兩手用同一把尺照音高擺，一個八度固定 6 格，每一組八度都長一樣。
         # 寬 2：和弦裡差四度（5 個半音＝2~3 格）的兩顆才不會疊。
         lanes=dict(kind='contour', hand=RH, region=(0, 27), width=2, other=(0, 0), octave=6),
         segments=[('demo', [(1.5, 8.7)], '兩手相差一個八度，一起點下去就彈起來；長條才要按住。'),
                   ('play', [(1.5, 8.7)], '換你：短的馬上放，長的按住。'),
                   ('demo', [(8.7, 11.5)], '兩手反方向移動，每一顆都是短促的一下。'),
                   ('play', [(11.5, 14.2)], '換你：同一個音型再來兩次。')]),
    dict(tier='advanced', key='repeated', title='同音連打', bpm=80, level=7, midi='campanella',
         source='李斯特《鐘》第 52–59 小節',
         goal='進階：同一個鍵快速重複按。手指輪流換著按比同一根手指硬敲穩，按完要馬上放。',
         rule=rule_union(lambda ns: every(ns, RH), lambda ns: top_voice(ns, LH, grid=480)),
         lock=LH,
         lanes=dict(kind='linear', hand=RH, region=(8, 27), width=3, other=(0, 6)),
         segments=[('demo', [(52, 55)], '同一個位置連按兩下，再跳上八度：B5、B5、B6。'),
                   ('play', [(56, 59)], '換你：連打要放開再按，不要壓著不放。')]),
    dict(tier='advanced', key='fast-leaps', title='快速跳躍', bpm=88, level=7, midi='campanella',
         source='李斯特《鐘》第 6–9、41–44 小節',
         goal='進階：右手在旋律和高音 D#7 之間快速來回跳，一次跨一到兩個八度。眼睛先看落點。',
         rule=rule_union(lambda ns: every(ns, RH), lambda ns: top_voice(ns, LH, grid=480)),
         lock=LH,
         # 照音高擺：跳兩個八度就真的橫跨大半個右手區。
         lanes=dict(kind='linear', hand=RH, region=(6, 27), width=3, other=(0, 5)),
         segments=[('demo', [(6, 9)], '旋律一顆、D#7 一顆輪流：手在兩個位置之間來回。'),
                   # 第 14–17 小節是同一段，但多了幾顆 85ms 的裝飾音，比示範難——遊玩段
                   # 要和示範一樣，所以直接再放一次第 6–9 小節。
                   ('play', [(6, 9)], '換你：同一段旋律。'),
                   ('demo', [(41, 44)], '只剩跳躍：D#5、D#6、D#7 三個位置，一次跳兩個八度。'),
                   ('play', [(41, 44)], '換你：三個位置來回跳。')]),
    dict(tier='advanced', key='chord-leaps', title='快速和弦跳躍', bpm=80, level=7,
         midi='campanella',
         source='李斯特《鐘》第 127–131、139–142 小節',
         goal='進階：整個和弦一起跳。手先擺好和弦的形狀，跳的時候手型不變，整隻手移過去。',
         segments=[('demo', [(127, 131)], '左手：低音八度、跳上去按和弦、再跳回低音。', _CHORD_LH),
                   ('play', [(127, 131)], '換你：左手和弦跳躍，右手按住長音。', _CHORD_LH),
                   ('demo', [(139, 142)], '兩手一起：三音和弦一路換，中間突然跳上高八度再回來。', _CHORD_BOTH),
                   ('play', [(139, 142)], '換你：兩手和弦跳躍。', _CHORD_BOTH)]),
    dict(tier='advanced', key='dynamics', title='強弱控制', bpm=72, level=7, midi='scarbo',
         # 不轉顫音：第 384–390 小節左手 C–D 來回正是漸強本身，包成顫音就看不到、也判不到強弱。
         recital=True, keep_velocity=True, holds=True,
         source='拉威爾《夜之加斯巴》Scarbo 第 153–171、382–396 小節',
         goal=('進階：力度照原曲，從極弱到極強。這一課自動用演奏會模式，外圈亮起的音符有'
               '強弱要求：強音重重按、弱音輕輕碰。需要有力度的鍵盤。'),
         # 使用者：音要省多一點，把旋律的形狀做出來。每個八分音符只留兩手最高的一顆
         # （格線之間的強音重擊也留），其餘藏起來照樣響；鍵道不分手，照音高排。
         rule=lambda ns: melody(ns, grid=240),
         segments=[('demo', [(153, 171)], '很輕的同音慢慢變大聲，突然砸下最強的和弦，再退回很輕。', _DYN_A),
                   ('play', [(153, 171)], '換你：輕的真的要輕，強的一口氣按到底。', _DYN_A),
                   ('demo', [(382, 396)], '低音從極弱一路漸強到最強，接著一路往下漸弱到幾乎聽不見。', _DYN_B),
                   ('play', [(382, 396)], '換你：一整段漸強，再一整段漸弱。', _DYN_B)]),
    dict(tier='advanced', key='octaves', title='高速八度', bpm=176, level=7, midi='danse',
         source='聖桑《骷髏之舞》雙鋼琴版 第 351–361、431–437、366–369 小節',
         goal='進階：一隻手撐開一個八度，整個手腕一起快速上下。右手、左手、兩手各練一次。',
         segments=[('demo', [(351, 361)], '右手八度：大拇指和小指撐開，兩顆一起按，手型不要變。', _OCT_RH),
                   ('play', [(351, 361)], '換你：右手八度。', _OCT_RH),
                   ('demo', [(431, 437)], '換左手：低音區的八度來回，右手在拍點上按住和弦。', _OCT_LH),
                   ('play', [(431, 437)], '換你：左手八度。', _OCT_LH),
                   ('demo', [(366, 369)], '兩手一起：平行八度一路往上爬，這是全曲的高潮。', _OCT_BOTH),
                   ('play', [(366, 369)], '換你：兩手八度。', _OCT_BOTH)]),
    dict(tier='advanced', key='rotation', title='旋轉', bpm=72, level=7, midi='winterwind',
         source='蕭邦《冬風》練習曲 Op.25 No.11 第 5–8（右手）、41–44（左手）、85–86 小節（雙手）',
         goal=('進階：音一上一下交錯、整串往同一個方向走（鋸齒狀音型）。手指來不及一根一根抬，'
               '要靠手腕左右轉動帶過去。右手、左手、雙手各練一次。'),
         holds=True,
         segments=[('demo', [(5, 6)], '右手：上面的音一路半音往下，和下面的音一上一下交錯。手腕跟著左右轉。', _ROT_RH),
                   ('play', [(7, 8)], '換你：右手每一顆都打，左手按住和弦。', _ROT_RH),
                   ('demo', [(41, 42)], '換左手：同樣的鋸齒搬到低音區，右手按住上面的和弦。', _ROT_LH),
                   ('play', [(43, 44)], '換你：左手旋轉。', _ROT_LH),
                   ('demo', [(85, 86)], '兩手一起：左右手同時一上一下交錯。', _ROT_BOTH),
                   ('play', [(85, 86)], '換你：雙手旋轉。', _ROT_BOTH)]),
]


# ----------------------------------------------------------------------
# 一課：放到自己的時間軸上（從 0 開始）
# ----------------------------------------------------------------------
class Placed:
    """一顆搬到教學時間軸上的音。"""

    def __init__(self, raw: Raw, start_ms: int, end_ms: int, segment: int):
        self.raw, self.start, self.end, self.segment = raw, start_ms, end_ms, segment
        self.bar = raw.bar
        #: 講解空檔裡的背景音樂（淡出／淡入）：力度倍率。None＝正式段落的音。
        self.fade = None


class Built:
    """處理完的一課：音符、段落、小節時間表都在這一課自己的時間軸上。"""

    def __init__(self, no: int, lesson: dict):
        self.no, self.lesson = no, lesson
        self.notes: List[GNote] = []
        self.visible: List[GNote] = []
        self.segments: List[dict] = []
        self.bar_starts: List[int] = []
        self.duration_ms = 0
        self.pedal: List[List[float]] = []
        self.orphans = self.slides = self.trills = self.clipped = self.gap_music = 0
        source = SOURCES[lesson.get('midi', 'k265')]
        self.beats = source['beats']
        self.ts = tuple(source.get('ts') or (int(source['beats']), 4))


def place(lesson: dict, notes: List[Raw]):
    source = SOURCES[lesson.get('midi', 'k265')]
    bar_ticks, beats = source['bar_ticks'], source['beats']
    origin_tick, origin_bar = source.get('origin_scaled', (0, 1))
    starts = source.get('bar_starts')
    quarter_ms = 60000.0 / lesson['bpm']
    bar_ms = quarter_ms * beats

    def t(ticks: float) -> int:
        return int(round(ticks / TPB * quarter_ms))

    by_bar: Dict[int, List[Raw]] = defaultdict(list)
    for n in notes:
        by_bar[n.bar].append(n)

    placed: List[Placed] = []
    visible_ids = set()
    segments = []
    beat_ms = quarter_ms if quarter_ms <= COUNT_IN_MAX_BEAT_MS else quarter_ms / 2
    per_second = source.get('src_bpm', 0) / 60.0 * TPB

    def ticks(ms: float) -> int:
        return int(round(ms / quarter_ms * TPB))

    def edge_ticks(ranges) -> Tuple[int, int]:
        """一段在來源裡從哪個 tick 開始、到哪個 tick 結束。"""
        lo, hi = ranges[0][0], ranges[-1][1]
        if source.get('performance'):
            return int(round(lo * per_second)), int(round(hi * per_second))
        if starts is not None:
            return starts[lo - 1], starts[min(hi, len(starts) - 1)]
        return (origin_tick + (lo - origin_bar) * bar_ticks,
                origin_tick + (hi - origin_bar + 1) * bar_ticks)

    def gap_music(tick_from: int, tick_to: int, chart_ms: float, tracks, index: int,
                  fade_from: float, fade_to: float) -> None:
        """把來源 [tick_from, tick_to) 的音放到譜面 chart_ms 起，力度由 fade_from 漸變到 fade_to。

        講解的時候畫面上沒有音符，但不能一片安靜（使用者要求）：前半接著上一段往下
        彈、慢慢淡出，後半從這一段前面幾小節慢慢淡入，直接接進段落。全部是隱藏音，
        寄主是這一段的第一顆音符——比寄主早的隱藏音遊戲會照時間自動播。
        """
        # 來源開頭之前沒有音：照原本的起點算位置，只是前面那段自然是空的。
        if tick_to <= max(0, tick_from):
            return
        span = float(tick_to - tick_from)
        for n in notes:
            if not (max(0, tick_from) <= n.start < tick_to):
                continue
            if tracks is not None and n.track not in tracks:
                continue
            s0 = int(round(chart_ms)) + t(n.start - tick_from)
            e0 = int(round(chart_ms)) + t(min(n.end, tick_to) - tick_from)
            p = Placed(n, s0, max(e0, s0 + 1), index)
            p.fade = fade_from + (fade_to - fade_from) * ((n.start - tick_from) / span)
            placed.append(p)

    def has_music(tick_from: int, tick_to: int, tracks) -> bool:
        return any(max(0, tick_from) <= n.start < tick_to
                   and (tracks is None or n.track in tracks) for n in notes)

    previous = None          # (上一段在來源裡結束的 tick, 上一段的聲部, 上一段開始的 tick)

    def continuation(last_tick: int, last_tracks, last_first: int, length: int) -> int:
        """上一段之後接著彈的起點。來源到底了（自製的滑奏、取到曲子最後）就從上一段開頭再彈。"""
        return last_tick if has_music(last_tick, last_tick + length, last_tracks) else last_first
    cursor_bars = 0
    for index, segment_def in enumerate(lesson['segments']):
        mode, ranges, caption = segment_def[:3]
        rule = _segment_option(lesson, index, 'rule')
        tracks = _segment_option(lesson, index, 'tracks')
        # 這一段前面要留：說明（第一段還有課程目標）+ 三拍，湊整小節。
        need = read_ms(caption) + COUNT_IN_BEATS * beat_ms + 200
        if index == 0:
            need += read_ms(lesson['goal'])
        gap_bars = max(1, math.ceil(need / bar_ms - 1e-6))
        cursor_bars += gap_bars
        seg_start_bar = cursor_bars
        gap_start_ms = (seg_start_bar - gap_bars) * bar_ms
        gap_ms = gap_bars * bar_ms
        first_tick, own_end_tick = edge_ticks(ranges)
        # 前半淡出上一段的後續；上一段後面沒有音樂（第一段、來源到底了）就整段拿來淡入。
        tail_ms = 0.0
        if previous is not None:
            last_tick, last_tracks, last_first = previous
            length = ticks(gap_ms / 2.0)
            from_tick = continuation(last_tick, last_tracks, last_first, length)
            if has_music(from_tick, from_tick + length, last_tracks):
                tail_ms = gap_ms / 2.0
                gap_music(from_tick, from_tick + length, gap_start_ms, last_tracks, index,
                          1.0, FADE_FLOOR)
        lead_ms = gap_ms - tail_ms
        lead_from = first_tick - ticks(lead_ms)
        if not has_music(lead_from, first_tick, tracks):
            # 段落從曲子開頭開始（或自製的滑奏），前面沒東西：放這一段自己的結尾，
            # 像反覆記號一樣接回開頭。
            lead_from, first_tick = own_end_tick - ticks(lead_ms), own_end_tick
        gap_music(lead_from, first_tick, seg_start_bar * bar_ms - lead_ms,
                  tracks, index, FADE_FLOOR, 1.0)
        previous = (edge_ticks(ranges)[1], tracks, edge_ticks(ranges)[0])
        for lo, hi in ranges:
            if source.get('performance'):
                # 秒數區段：拿起音落在 [lo, hi) 秒的音，長度換算成整小節往後推。
                per_second = source['src_bpm'] / 60.0 * TPB
                base_tick = int(round(lo * per_second))
                limit_tick = int(round(hi * per_second))
                chunk = [n for n in notes if base_tick <= n.start < limit_tick]
                offset_ms = cursor_bars * bar_ms
                chosen = rule(chunk)
                visible_ids.update((id(n), index) for n in chosen)
                for n in chunk:
                    s0 = int(round(offset_ms)) + t(n.start - base_tick)
                    e0 = int(round(offset_ms)) + t(min(n.end, limit_tick) - base_tick)
                    placed.append(Placed(n, s0, max(e0, s0 + 1), index))
                cursor_bars += max(1, math.ceil(t(limit_tick - base_tick) / bar_ms - 1e-6))
                continue
            chunk = [n for b in range(lo, hi + 1) for n in by_bar[b]
                     if tracks is None or n.track in tracks]
            if starts is not None:
                base_tick, limit_tick = starts[lo - 1], starts[hi]
            else:
                base_tick = origin_tick + (lo - origin_bar) * bar_ticks
                limit_tick = origin_tick + (hi - origin_bar + 1) * bar_ticks
            offset_ms = cursor_bars * bar_ms
            chosen = rule(chunk)
            visible_ids.update((id(n), index) for n in chosen)
            for n in chunk:
                s = int(round(offset_ms)) + t(n.start - base_tick)
                e = int(round(offset_ms)) + t(min(n.end, limit_tick) - base_tick)
                placed.append(Placed(n, s, max(e, s + 1), index))
            cursor_bars += hi - lo + 1
        segments.append(dict(
            mode=mode,
            startMs=int(round(seg_start_bar * bar_ms)),
            endMs=int(round(cursor_bars * bar_ms)),
            sourceBars=', '.join(('%.1f-%.1f 秒' if source.get('performance') else '%d-%d') % r
                                 for r in ranges),
            beatMs=int(round(beat_ms)),
            caption=caption,
        ))
    if previous is not None:
        # 課尾留白也接著彈、淡出。寄主是全課程裡下一課的第一顆音（write_song 掛），
        # 單課譜面後面沒有東西，就不放。段落編號用「最後一段 + 1」標記。
        last_tick, last_tracks, last_first = previous
        length = ticks(TAIL_BARS * bar_ms)
        from_tick = continuation(last_tick, last_tracks, last_first, length)
        gap_music(from_tick, from_tick + length, cursor_bars * bar_ms,
                  last_tracks, len(lesson['segments']), 1.0, FADE_FLOOR)
    total_bars = cursor_bars + TAIL_BARS
    bar_starts = [int(round(i * bar_ms)) for i in range(total_bars + 1)]
    return placed, visible_ids, segments, bar_starts


def to_gnotes(placed: List[Placed], visible_ids, lesson: dict,
              quarter_ms: float) -> Tuple[List[GNote], List[GNote]]:
    notes, visible = [], []
    long_ms = LONG_MIN_TICKS / TPB * quarter_ms
    # 力度對齊：每一課的中位數拉到 VELOCITY_MIDDLE，對比壓成一半、夾在合理範圍。
    # 來源差太多——《鐘》的 MIDI 大多 40 上下（幾乎聽不到），《骷髏之舞》每一顆都是
    # 127（刺耳），接在同一份全課程裡音量會一下子沒了、一下子爆掉。
    velocities = sorted(p.raw.velocity for p in placed)
    middle = velocities[len(velocities) // 2] if velocities else VELOCITY_MIDDLE
    for i, p in enumerate(sorted(placed, key=lambda p: (p.start, p.raw.hand, -p.raw.pitch))):
        g = GNote(None, i)
        g.start, g.end = p.start, p.end
        g.gate = g.end - g.start
        g.min_key, g.max_key = lane_center_to_width3_range(
            midi_pitch_to_game_lane_index(p.raw.pitch))
        g.hand = p.raw.hand
        g._source_hand = p.raw.hand
        g.track = p.raw.hand
        g.pitch = p.raw.pitch
        if lesson.get('keep_velocity'):
            # 強弱課：力度就是教材，照原曲。
            g.velocity = int(p.raw.velocity)
        else:
            g.velocity = int(max(VELOCITY_RANGE[0], min(VELOCITY_RANGE[1], round(
                VELOCITY_MIDDLE + (p.raw.velocity - middle) * VELOCITY_CONTRAST))))
        g.channel = 0
        g.off_velocity = 0
        g.note_type = 0
        if p.fade is not None:
            # 講解空檔的背景音樂：永遠看不見，力度照淡入淡出縮放。
            g.velocity = max(1, int(round(g.velocity * p.fade)))
        g._gap_music = p.fade is not None
        g.hidden = p.fade is not None or (id(p.raw), p.segment) not in visible_ids
        g._segment = p.segment
        g._bar = p.bar
        if (not g.hidden and lesson.get('staccato')
                and p.raw.dur <= STACCATO_SOURCE_TICKS):
            # 斷奏音符：判定看「放開的時間有沒有拖過結束時間」，提早放一律算對。
            # 結束時間寫得短，玩家就得按了馬上放。
            g.note_type = D.KIND_STACCATO
            g.end = g.start + STACCATO_MS
            g.gate = STACCATO_MS
        elif not g.hidden and lesson.get('holds') and g.gate >= long_ms:
            g.note_type = D.KIND_HOLD
            g.end = max(g.start + int(long_ms * 0.5), g.end - HOLD_TAIL_ADVANCE_MS)
            g.gate = g.end - g.start
        notes.append(g)
        if not g.hidden:
            visible.append(g)
    return notes, visible


# 自然音階的第幾級（升降記號算半級）。音階的一步是 1 級，琶音的一步是 2 級（三度）。
_DEGREE = {0: 0, 1: 0.5, 2: 1, 3: 1.5, 4: 2, 5: 3, 6: 3.5, 7: 4, 8: 4.5, 9: 5, 10: 5.5, 11: 6}


def _degree(pitch: int) -> float:
    return (pitch // 12) * 7 + _DEGREE[pitch % 12]


def _place_linear(notes, lo_lane, hi_lane, width, key, octave: int = 0) -> None:
    """照 `key(pitch)` 等比例攤進 [lo_lane, hi_lane]，最多一單位一格。

    `octave` > 0：一個八度固定這麼多格（放不下就一格一格縮）。比例是 octave/12，
    八度的格數是整數，任何兩顆差八度的音在畫面上間隔都一樣——等比例縮成 0.57 的話，
    四捨五入會讓同一個八度一下 6 格一下 7 格（斷奏課兩手平行八度看起來歪的原因）。
    """
    if not notes:
        return
    low = min(key(n.pitch) for n in notes)
    high = max(key(n.pitch) for n in notes)
    room = hi_lane - lo_lane + 1 - width
    k = min(1.0, room / float(high - low)) if high > low else 0.0
    if octave > 0 and high > low:
        while octave > 1 and (high - low) * octave / 12.0 > room:
            octave -= 1
        k = octave / 12.0
    base = lo_lane + int(round((room - (high - low) * k) / 2.0))
    for n in notes:
        n.min_key = base + int(round((key(n.pitch) - low) * k))
        n.max_key = n.min_key + width - 1


#: 八度兩顆之間固定隔幾格。固定的間隔就是固定的手型：玩家一眼看出「撐開」。
OCTAVE_SPAN = 5


def _place_octaves(notes, lo_lane, hi_lane, width) -> None:
    """每個起音的下面那顆照音高擺，上面那顆（八度）固定在它右邊 OCTAVE_SPAN 格。"""
    groups: Dict[int, List[GNote]] = defaultdict(list)
    for n in notes:
        groups[n.start].append(n)
    if not groups:
        return
    lows = {when: min(g, key=lambda n: n.pitch).pitch for when, g in groups.items()}
    low, high = min(lows.values()), max(lows.values())
    room = hi_lane - lo_lane + 1 - width - OCTAVE_SPAN
    k = min(1.0, room / float(high - low)) if high > low else 0.0
    base = lo_lane + int(round((room - (high - low) * k) / 2.0))
    for when, group in groups.items():
        start = base + int(round((lows[when] - low) * k))
        for n in group:
            offset = OCTAVE_SPAN if n.pitch > lows[when] else 0
            n.min_key = start + offset
            n.max_key = n.min_key + width - 1


#: 和弦課：一個八度佔幾格。三音和弦（八度＋中間音）用寬 2 的音符剛好不重疊。
CHORD_SPAN = 7


def _place_chords(notes, lo_lane, hi_lane, width) -> None:
    """每個起音的最低音照音高擺；同一組其他音照「離最低音幾個半音」放在右邊。

    一個八度固定 `CHORD_SPAN` 格，所以同樣的和弦形狀永遠長一樣——玩家記的是手型，
    跳的時候只看整組往哪裡移。
    """
    groups: Dict[int, List[GNote]] = defaultdict(list)
    for n in notes:
        groups[n.start].append(n)
    if not groups:
        return
    lows = {when: min(g, key=lambda n: n.pitch).pitch for when, g in groups.items()}
    # 每顆音離最低音幾格。二度只有 1 格、比音符寬度窄，會和隔壁那顆疊在一起（使用者回報
    # 和弦跳躍課「重疊」）——由低往高排，至少隔一個音符寬。
    offsets: Dict[int, int] = {}
    for when, group in groups.items():
        previous = None
        for n in sorted(group, key=lambda n: n.pitch):
            interval = min(12, n.pitch - lows[when])
            offset = int(round(interval / 12.0 * CHORD_SPAN))
            if previous is not None:
                offset = max(offset, previous + width)
            offsets[id(n)] = previous = offset
    shape = max(offsets.values())
    low, high = min(lows.values()), max(lows.values())
    room = max(0, hi_lane - lo_lane + 1 - width - shape)
    k = min(1.0, room / float(high - low)) if high > low else 0.0
    base = lo_lane + int(round((room - (high - low) * k) / 2.0))
    for when, group in groups.items():
        start = base + int(round((lows[when] - low) * k))
        for n in group:
            n.min_key = start + offsets[id(n)]
            n.max_key = n.min_key + width - 1


def spread_lanes(visible: Sequence[GNote], kind: str, hand: int, region: Tuple[int, int],
                 width: int, other: Tuple[int, int], other_width: int = 3,
                 other_kind: str = 'linear', octave: int = 0) -> None:
    """鍵道直接由音高決定，取代排譜器（音階、琶音、跳躍課用）。

    排譜器會把音程壓扁：一步三度和一步二度排出來都是 1~2 格，玩家看不出音階和琶音
    差在哪，大跳也不像大跳。這幾課要的正是那個差別：

    * ``scale``：一個音級一格，寬音符彼此疊著，連成一條樓梯。
    * ``arpeggio``：以每小節的最低音為起點，一個音級 2 格；各小節的起點再照音高
      分布在區域裡。三度隔 4 格、八度隔 14 格。
    * ``linear``：一個半音一格、塞不下就等比例縮，大跳就是真的橫跨半個鍵盤。
    * ``octave``：八度的下面那顆照音高擺，上面那顆固定隔 5 格。
    * ``chord``：和弦的最低音照音高擺，其他音照音程放右邊，一個八度 7 格。
    * ``contour``：不分手，所有音照音高擺在同一把尺上（強弱課要看旋律的形狀）。

    另一隻手擠到 `other` 那一小塊，兩手不重疊。
    """
    if kind == 'contour':
        # 兩手用同一把尺：旋律往上畫面就往右、往下就往左，形狀一眼看得出來。
        _place_linear(list(visible), region[0], region[1], width, float, octave)
        # 同時按的二度（差 1~2 個半音）換算不到一個音符寬會疊在一起：由低往高推開。
        groups: Dict[int, List[GNote]] = defaultdict(list)
        for n in visible:
            groups[n.start].append(n)
        for group in groups.values():
            group.sort(key=lambda n: (n.pitch, n.min_key))
            for lower, upper in zip(group, group[1:]):
                if upper.min_key < lower.min_key + width:
                    upper.min_key = lower.min_key + width
                    upper.max_key = upper.min_key + width - 1
            overflow = group[-1].max_key - region[1]
            if overflow > 0:
                for n in group:
                    n.min_key -= overflow
                    n.max_key -= overflow
        return
    main = [n for n in visible if n.hand == hand]
    rest = [n for n in visible if n.hand != hand]
    if kind == 'octave':
        _place_octaves(main, region[0], region[1], width)
    elif kind == 'chord':
        _place_chords(main, region[0], region[1], width)
    elif kind == 'scale':
        _place_linear(main, region[0], region[1], width, _degree)
    elif kind == 'linear':
        _place_linear(main, region[0], region[1], width, float)
    elif kind == 'arpeggio' and main:
        per_degree = 2.0
        by_bar: Dict[Tuple[int, int], List[GNote]] = defaultdict(list)
        for n in main:
            by_bar[(n._segment, n._bar)].append(n)
        roots = {key: min(_degree(n.pitch) for n in notes) for key, notes in by_bar.items()}
        low_root, high_root = min(roots.values()), max(roots.values())
        room = region[1] - region[0] + 1 - width
        for key, notes in by_bar.items():
            span = max(_degree(n.pitch) for n in notes) - roots[key]
            k = min(per_degree, room / float(span)) if span > 0 else per_degree
            spare = max(0, room - int(round(span * k)))
            share = ((roots[key] - low_root) / float(high_root - low_root)
                     if high_root > low_root else 0.0)
            base = region[0] + int(round(spare * share))
            for n in notes:
                n.min_key = base + int(round((_degree(n.pitch) - roots[key]) * k))
                n.max_key = n.min_key + width - 1
    if other_kind == 'octave':
        _place_octaves(rest, other[0], other[1], other_width)
    elif other_kind == 'chord':
        _place_chords(rest, other[0], other[1], other_width)
    else:
        _place_linear(rest, other[0], other[1], other_width, float)


#: 講解空檔的背景音樂淡到最小聲時的力度倍率（0 會整顆不響，聽起來是斷掉不是淡掉）。
FADE_FLOOR = 0.2

#: 長押被截短時，離撞到的那顆至少留這麼多毫秒（來得及放開再按）。
COLLISION_GAP_MS = 40


def clear_lane_collisions(visible: Sequence[GNote], quarter_ms: float) -> int:
    """長押／斷奏還按著時，不能有別的音落在它的鍵道上。

    鍵道是照音高直接擺的（強弱課的旋律形狀、斷奏課的連續和弦），擺法本身不看時間，
    所以會出現「長音還沒放，下一顆音高相近的音就落在同一格」——畫面上是疊在一起、
    手上是同一個位置要同時按住又再按一次（使用者回報重疊）。鍵道是教材，不動；改的是
    長度：截到撞到的那顆之前。截完太短的長押改成點擊；斷奏本來就短，只往前收。
    回傳改了幾顆。
    """
    ordered = sorted(visible, key=lambda n: n.start)
    changed = 0
    for a in ordered:
        if a.hidden or a.note_type not in (D.KIND_HOLD, D.KIND_STACCATO):
            continue
        hits = [b.start for b in ordered
                if b is not a and not b.hidden and a.start < b.start < a.end
                and b.min_key <= a.max_key and a.min_key <= b.max_key]
        if not hits:
            continue
        first = min(hits)
        if a.note_type == D.KIND_STACCATO:
            gap = min(COLLISION_GAP_MS, (first - a.start) // 4)
            a.end = max(a.start + 1, first - gap)
        else:
            a.end = max(a.start + 1, first - COLLISION_GAP_MS)
            if a.end - a.start < quarter_ms * 0.5:
                a.note_type = D.KIND_TAP
        a.gate = a.end - a.start
        changed += 1
    return changed


def _first_host(visible: Sequence[GNote]) -> Dict[int, GNote]:
    """每一段第一顆能當背景音樂寄主的音符：一般、長押、斷奏優先，整段都是滑奏才用滑奏。"""
    firsts: Dict[int, GNote] = {}
    slides: Dict[int, GNote] = {}
    for n in sorted(visible, key=lambda n: n.start):
        if n.hidden:
            continue
        kind = int(n.note_type)
        if kind in (D.KIND_TAP, D.KIND_HOLD, D.KIND_STACCATO):
            firsts.setdefault(n._segment, n)
        elif not kind & D.FLAG_TRILL:
            slides.setdefault(n._segment, n)
    for segment, n in slides.items():
        firsts.setdefault(segment, n)
    return firsts


#: 強弱課看得見的音只用這三個力度：弱、中、強。
VELOCITY_TIERS = (36, 78, 118)


def tier_velocities(visible: Sequence[GNote]) -> None:
    """強弱課：看得見的音照原曲力度的高低分三等份，改成固定的弱／中／強。

    遊戲的強弱標記（VelocityBands）只標整份譜最極端的約 18%，而且要「同一個力度值」
    成片才收得進去。原曲的漸強是 54、59、64、69……一顆一個值，結果只有開頭那串
    最輕的 33 被標成弱、整段漸強幾乎沒有標記（使用者：只有前面標力度）。分成三個
    固定值，弱和強各佔約三分之一，都會被標出來。隱藏音不動，聽到的還是原曲的漸變。
    """
    # 照名次切，不照數值切：K.265 的 MIDI 力度幾乎全是 80，照數值切的話 57% 都算強，
    # 超過遊戲收「強」的上限（40%）就一顆都不標。同樣力度時右手（旋律）排前面、
    # 音高高的排前面——旋律比伴奏響，正是演奏會評審「表情」那一項要的。
    if len(visible) < 3:
        return
    ranked = sorted(visible, key=lambda n: (n.velocity, n.hand == RH, n.pitch, -n.start))
    third = len(ranked) // 3
    for rank, n in enumerate(ranked):
        if rank < third:
            n.velocity = VELOCITY_TIERS[0]
        elif rank >= len(ranked) - third:
            n.velocity = VELOCITY_TIERS[2]
        else:
            n.velocity = VELOCITY_TIERS[1]


def attach_gap_music(notes: Sequence[GNote], visible: Sequence[GNote]) -> int:
    """講解空檔的背景音樂掛到「它後面那一段」的第一顆音符上。

    一定要掛在比它晚的寄主：遊戲只會自動播放比寄主早的隱藏音，掛到上一段的音符上
    的話，要等那顆被打到才排程，而且時間早就過了、整串會擠在同一刻響出來。
    顫音不能當寄主（存檔會變孤兒），滑奏鏈也避開，只挑一般、長押、斷奏。排鍵道、
    做滑奏都結束之後才掛，寄主的種類才是定案的。回傳掛了幾顆。
    """
    # 整段都是滑奏（滑奏課）時只好掛在滑奏上：遊戲端自動播放不看寄主種類。
    firsts = _first_host(visible)
    tail_segment = max((n._segment for n in visible), default=-1) + 1
    attached = 0
    for n in notes:
        if not n._gap_music:
            continue
        if n._segment >= tail_segment:
            n._lesson_tail = True          # 課尾淡出：寄主在下一課，write_song 再掛
            continue
        host = firsts.get(n._segment)
        if host is None or host.start <= n.start:
            # 沒有合適的寄主（不該發生）：這顆就不放，免得變成要打的孤兒。
            n._drop = True
            continue
        n._sub_host = host
        attached += 1
    return attached


def lock_hand(visible: Sequence[GNote], hand: int, quarter_ms: float) -> int:
    """進階課的「另一隻手」：每一顆都變成按到下一顆之前的長押，把那隻手鎖住。

    主練的手全部音都要打，另一隻手不需要練什麼，但不能閒著——閒著的手會去幫忙打
    主練那隻手的音。按住一個長音剛好佔住它，也保留和聲的低音。
    """
    gap = max(80, int(quarter_ms * 0.12))
    by_segment: Dict[int, List[GNote]] = defaultdict(list)
    for n in visible:
        if n.hand == hand:
            by_segment[n._segment].append(n)
    locked = 0
    for notes in by_segment.values():
        notes.sort(key=lambda n: n.start)
        for index, note in enumerate(notes):
            if index + 1 < len(notes):
                end = notes[index + 1].start - gap
            else:
                end = note.start + int(quarter_ms * 1.5)
            if end - note.start < quarter_ms * 0.5:
                continue
            note.note_type = D.KIND_HOLD
            note.end = end
            note.gate = end - note.start
            locked += 1
    return locked


def _single_runs(visible: Sequence[GNote], gap_ms: float) -> List[List[GNote]]:
    """同一隻手、同一段落、每刻一顆、間隔 <= gap_ms 的連續音。"""
    runs = []
    by_hand = defaultdict(lambda: defaultdict(list))
    for n in visible:
        by_hand[(n.hand, n._segment)][n.start].append(n)
    for at_time in by_hand.values():
        current, prev = [], None
        for when in sorted(at_time):
            group = at_time[when]
            if len(group) != 1 or (prev is not None and when - prev > gap_ms):
                if len(current) > 1:
                    runs.append(current)
                current = []
            if len(group) == 1:
                current.append(group[0])
                prev = when
            else:
                prev = None
        if len(current) > 1:
            runs.append(current)
    return runs


def make_slides(visible: Sequence[GNote], quarter_ms: float) -> int:
    """單調、步進 <= 2 半音、至少 5 顆的樓梯變成一條滑奏。"""
    made = 0
    for run in _single_runs(visible, quarter_ms / 4 + 5):
        i = 0
        while i < len(run) - 1:
            direction = 1 if run[i + 1].pitch > run[i].pitch else -1
            j = i + 1
            while (j < len(run)
                   and 0 < (run[j].pitch - run[j - 1].pitch) * direction <= 2
                   and D._kind(run[j - 1]) != D.KIND_HOLD):
                j += 1
            stair = run[i:j]
            if len(stair) >= 5:
                made += 1
                for n in stair:
                    n.note_type = D.FLAG_SLIDE
                    n._made_slide = True
                    n._slide_run = made
                    n.end = min(n.end, n.start + int(quarter_ms / 4))
                    n.gate = n.end - n.start
                i = j
            else:
                i += 1
    D._chain_slides(list(visible))
    return made


def make_trills(visible: List[GNote], quarter_ms: float) -> int:
    """兩個音來回 >= 5 擊變成顫音：頭變 0x40，其餘藏進頭裡。"""
    made = 0
    for run in _single_runs(visible, quarter_ms / 4 + 5):
        i = 0
        while i < len(run) - 1:
            pair = {run[i].pitch, run[i + 1].pitch}
            if len(pair) != 2 or abs(run[i].pitch - run[i + 1].pitch) > 3:
                i += 1
                continue
            j = i + 2
            while (j < len(run) and run[j].pitch in pair
                   and run[j].pitch != run[j - 1].pitch):
                j += 1
            if j - i >= 5:
                # 顫音結尾的迴音（B–C 這種收尾）一起收進來，否則留下兩顆
                # 32 分音符要單獨打，比顫音本身還難。長音是解決音，要留著打。
                stroke = run[i + 1].start - run[i].start
                lo, hi = min(pair) - 2, max(pair) + 2
                while (j < len(run)
                       and run[j].start - run[j - 1].start <= stroke + 10
                       and lo <= run[j].pitch <= hi
                       and run[j].gate <= stroke * 2
                       and D._kind(run[j]) != D.KIND_HOLD):
                    j += 1
            seg = run[i:j]
            if len(seg) >= 5:
                head = seg[0]
                head.note_type = D.FLAG_TRILL
                head.end = max(seg[-1].start + 1, seg[-1].end - D.TRILL_TAIL_ADVANCE_MS)
                head.gate = head.end - head.start
                for n in seg[1:]:
                    n.hidden = True
                    n._sub_host = head
                    visible.remove(n)
                made += 1
                i = j
            else:
                i += 1
    return made


def build_lesson(no: int, lesson: dict, raws: Dict[str, List[Raw]], label: str = '') -> Built:
    built = Built(no, lesson)
    quarter_ms = 60000.0 / lesson['bpm']
    placed, visible_ids, segments, bar_starts = place(lesson, raws[lesson.get('midi', 'k265')])
    notes, visible = to_gnotes(placed, visible_ids, lesson, quarter_ms)
    if lesson.get('keep_velocity'):
        # 只有強弱控制課要看得到強弱標記；初階的演奏會課不需要（使用者）。
        tier_velocities(visible)
    for index in range(len(segments)):
        lock = _segment_option(lesson, index, 'lock')
        if lock is not None:
            lock_hand([n for n in visible if n._segment == index], lock, quarter_ms)

    # 顫音、滑奏要在排譜之前認：它們決定哪些音是「一個動作」
    built.trills = make_trills(visible, quarter_ms) if lesson.get('trills') else 0
    hidden = [n for n in notes if n.hidden and getattr(n, '_sub_host', None) is None
              and not n._gap_music]
    built.orphans = D._attach_hosts(hidden, visible)

    goal = replace(D.TARGETS['normal'], note_width=4, max_hand_span=11)
    # 排譜器只讀 bpm 來推拍長；每一課用自己的速度排。
    D._arrange(SimpleNamespace(bpm=lesson['bpm']), visible, goal, None)
    # 排譜器只在「兩條以上有音符的音軌」時才照音軌分手；只有一隻手看得見的課（點擊、
    # 滑奏）它會照音高中位數硬拆成兩手——第 1 課 30 顆右手旋律有 14 顆被改成藍色。
    # 手是來源決定的，排完一律還原。
    for n in visible:
        n.hand = n._source_hand
    built.slides = make_slides(visible, quarter_ms) if lesson.get('slides') else 0
    D._cap_hand_span(visible, goal.max_hand_span)
    D._separate_hands(visible)
    D._resolve_hold_corridors(visible, max_span=goal.max_hand_span)
    D._straighten_slide_chains(visible, max_span=goal.max_hand_span)
    D._relink_slides(visible)
    # 用同一套擺法的段落一起算音域：示範段和遊玩段的同一顆音才會在同一格。
    by_layout: Dict[int, Tuple[dict, List[int]]] = {}
    for index in range(len(segments)):
        lanes = _segment_option(lesson, index, 'lanes')
        if lanes:
            by_layout.setdefault(id(lanes), (lanes, []))[1].append(index)
    for lanes, indices in by_layout.values():
        spread_lanes([n for n in visible if n._segment in indices], **lanes)
    built.clipped = clear_lane_collisions(visible, quarter_ms)
    built.gap_music = attach_gap_music(notes, visible)
    for n in notes:
        n.track = None

    if lesson.get('pedal'):
        beats = [int(round(i * quarter_ms))
                 for i in range(int(bar_starts[-1] / quarter_ms) + 1)]
        built.pedal = generate_pedal.generate_pedal_spans(notes, beats)

    for index, segment in enumerate(segments):
        segment['lesson'] = no
        segment['recital'] = bool(lesson.get('recital'))
        if index == 0:
            # 一課的第一段帶課名和目標：遊戲端在它前面先講這一課要學什麼。
            segment['lessonTitle'] = lesson['title']
            segment['lessonLabel'] = label or lesson['title']
            segment['goal'] = lesson['goal']
    notes = [n for n in notes if not getattr(n, "_drop", False)]
    built.notes, built.visible, built.segments = notes, visible, segments
    built.bar_starts, built.duration_ms = bar_starts, bar_starts[-1]
    return built


# ----------------------------------------------------------------------
# 寫出一份譜面（單課或全課程）
# ----------------------------------------------------------------------
def write_silent_wav(path: str, seconds: float) -> None:
    rate = 8000
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b'\x80' * int(rate * seconds))


def _pack_trills(notes: List[GNote]) -> List[GNote]:
    """顫音的每一擊收進顫音自己的 sub_note，從音符清單拿掉。

    處理期間每一擊是掛在顫音頭上的隱藏音（排譜器、寄主指定都不會碰到它），但存檔時
    編輯器規定顫音**不能**當隱藏音的寄主——隱藏音會被當成孤兒、變回看得見的音。
    所以寫檔前換成編輯器自己的顫音格式（`make_trill_from_notes` 那一種）。必須在
    時間平移之後做：sub_note 存的是絕對時間。
    """
    strokes: Dict[int, List[GNote]] = defaultdict(list)
    for n in notes:
        host = getattr(n, '_sub_host', None)
        if n.hidden and host is not None and int(host.note_type) & D.FLAG_TRILL:
            strokes[id(host)].append(n)
    if not strokes:
        return notes
    packed = set()
    for head in notes:
        mine = strokes.get(id(head))
        if not mine:
            continue
        mine.sort(key=lambda n: n.start)
        first = copy.copy(head)                  # 頭那一擊只到下一擊為止
        first.note_type = 0
        first.end = max(first.start + 1, mine[0].start)
        head.sub_elems = [_trill_sub_from_note(n, int(head.hand)) for n in [first] + mine]
        packed.update(id(n) for n in mine)
    return [n for n in notes if id(n) not in packed]


def write_song(songs_root: str, folder: str, display: str, level: int,
               parts: Sequence[Built], header: dict, author: str = AUTHOR) -> dict:
    """把一課或好幾課依序接起來，寫成一首歌。"""
    notes: List[GNote] = []
    segments: List[dict] = []
    bar_starts: List[int] = []
    pedal: List[List[float]] = []
    offset = 0
    # 曲繪：每一課畫自己的重點段落（時間平移之前，用這一課自己的時間軸）；全課程拼成一張。
    lesson_covers = [tutorial_cover.render(part.visible,
                                           *tutorial_cover.pick_window(part.visible, part.segments))
                     for part in parts]
    cover = lesson_covers[0] if len(lesson_covers) == 1 else tutorial_cover.composite(lesson_covers)

    # 課尾淡出的音掛到下一課第一顆音上；最後一課（或單課）後面沒有音，拿掉。
    for index, part in enumerate(parts):
        following = _first_host(parts[index + 1].visible) if index + 1 < len(parts) else {}
        host = following[min(following)] if following else None
        keep = []
        for n in part.notes:
            if getattr(n, '_lesson_tail', False):
                if host is None:
                    continue
                n._sub_host = host
            keep.append(n)
        part.notes = keep
    for part in parts:
        for n in part.notes:
            n.start += offset
            n.end += offset
        segments.extend(dict(s, startMs=s['startMs'] + offset, endMs=s['endMs'] + offset)
                        for s in part.segments)
        starts = [b + offset for b in part.bar_starts]
        bar_starts.extend(starts[1:] if bar_starts else starts)
        pedal.extend([a + offset, b + offset] for a, b in part.pedal)
        notes.extend(part.notes)
        offset += part.duration_ms
    notes = _pack_trills(notes)
    # 滑奏鏈的編號每一課各自從 1 開始，接起來會撞號：清掉重串。
    visible = [n for part in parts for n in part.visible]
    for n in visible:
        if int(n.note_type) & D.FLAG_SLIDE:
            n.note_index = None
    D._chain_slides(visible)

    for s in segments:
        first = bisect.bisect_right(bar_starts, s['startMs']) - 1
        last = bisect.bisect_left(bar_starts, s['endMs'])
        s['chartBars'] = '%d-%d' % (first + 1, last)

    name = folder
    model = NoteModel.create_new(name, parts[0].lesson['bpm'], offset / 1000.0, parts[0].ts[0])
    model.time_sig_denominator = parts[0].ts[1]
    # 小節時間表：每一課保留自己的速度。
    model._write_beat_entries(list(enumerate(bar_starts)), mark_precise=True)
    # 拍號：每一課沿用來源的拍號，換課時才記一筆變化。
    changes, at, last = [], 0, None
    for part in parts:
        if part.ts != last:
            changes.append((at, part.ts[0], part.ts[1]))
            last = part.ts
        at += part.duration_ms
    model.time_sig_changes = changes
    model._sync_time_sig_changes_out()
    model.notes_tree = sorted(notes, key=lambda n: (n.start, n.min_key))
    model.music_end_ms = float(offset)
    if pedal:
        model.pedal_spans = pedal
        model.pedal_origin = 'auto'
    model.rebuild_display_cache()

    song_dir = os.path.join(songs_root, folder)
    diff_dir = os.path.join(song_dir, 'Normal')
    os.makedirs(os.path.join(diff_dir, 'source'), exist_ok=True)
    model.save_xml(os.path.join(diff_dir, 'source', name + '.xml'))
    model.save_json(os.path.join(diff_dir, name + '.json'))
    write_silent_wav(os.path.join(song_dir, name + '.wav'), offset / 1000.0 + 2)
    cover.save(os.path.join(song_dir, name + '.png'))

    rel = 'songs/%s' % folder
    register = dict(displayName=display, author=author,
                    difficulties=[dict(difficultyName='Normal',
                                       difficultyLevel=level,
                                       chartFileName='%s/Normal/%s' % (rel, name),
                                       audioResourcePath='%s/%s' % (rel, name),
                                       coverResourcePath='%s/%s' % (rel, name),
                                       noBackgroundMusic=True)])
    with open(os.path.join(song_dir, 'register.json'), 'w', encoding='utf-8') as f:
        json.dump(register, f, ensure_ascii=False, indent=2)

    tutorial = dict(header, version=2, segments=segments)
    with open(os.path.join(song_dir, 'tutorial.json'), 'w', encoding='utf-8') as f:
        json.dump(tutorial, f, ensure_ascii=False, indent=2)

    return dict(folder=folder, title=display, seconds=round(offset / 1000.0, 1),
                total=sum(len(p.notes) for p in parts),
                visible=sum(len(p.visible) for p in parts),
                orphans=sum(p.orphans for p in parts), clipped=sum(p.clipped for p in parts),
                gap_music=sum(p.gap_music for p in parts),
                slides=sum(p.slides for p in parts), trills=sum(p.trills for p in parts),
                pedal=len(pedal),
                segments=[dict(mode=s['mode'], lesson=s['lesson'], chartBars=s['chartBars'],
                               sourceBars=s['sourceBars']) for s in segments])


def build(midi_paths: Dict[str, str], songs_root: str) -> List[dict]:
    raws = {key: source['generate'](source) for key, source in SOURCES.items()
            if source.get('generate')}
    for key, path in midi_paths.items():
        source = SOURCES[key]
        if source.get('performance'):
            raws[key] = read_performance(path, source)
            continue
        origin = source.get('origin', (0, 1))
        source['bar_starts'] = midi_bar_starts(path) if source.get('bar_map') else None
        raws[key] = read_midi(path, source['bar_ticks'], source['split'], origin,
                              source['bar_starts'])
        # origin 是原檔 tick，換算成每拍 TPB 的 tick 給 place() 用
        tpb = mido.MidiFile(path).ticks_per_beat
        source['origin_scaled'] = (int(round(origin[0] * TPB / float(tpb))), origin[1])

    report = []
    folders = []
    for tier, tier_name, prefix in TIERS:
        lessons = [lesson for lesson in LESSONS if lesson['tier'] == tier]

        def fresh() -> List[Built]:
            # 每首歌各自重建：save_xml 會改寫音符（note_index、寄主的 sub_note），
            # 共用同一批 GNote 的話，後寫的那首會帶著前一首留下的痕跡。
            return [build_lesson(no, lesson, raws, '%s %d：%s' % (tier_name, no, lesson['title']))
                    for no, lesson in enumerate(lessons, start=1)]

        course = '%s00' % prefix
        sources = []
        for lesson in lessons:
            author = SOURCES[lesson.get('midi', 'k265')]['author']
            if author not in sources:
                sources.append(author)
        report.append(write_song(
            songs_root, course, '%s教學：全課程（1–%d 課）' % (tier_name, len(lessons)),
            max(lesson['level'] for lesson in lessons), fresh(),
            dict(lesson=0, title='%s全課程' % tier_name, source='、'.join(sources),
                 recital=False, goal='照順序上完%s的每一課。每一課先看示範、再換你。' % tier_name),
            AUTHOR))
        folders.append(course)
        for built in fresh():
            folder = '%s%02d' % (prefix, built.no)
            lesson = built.lesson
            report.append(write_song(
                songs_root, folder, '%s %d：%s' % (tier_name, built.no, lesson['title']),
                lesson['level'], [built],
                dict(lesson=built.no, title=lesson['title'], source=lesson['source'],
                     bpm=lesson['bpm'], recital=bool(lesson.get('recital')),
                     goal=lesson['goal']),
                SOURCES[lesson.get('midi', 'k265')]['author']))
            folders.append(folder)

    _remove_stale(songs_root, folders)
    _register_category(songs_root, folders)
    return report


def _remove_stale(root: str, keep: Sequence[str]) -> None:
    """舊版的教學資料夾（名字是「新手教學」開頭、裡面有 tutorial.json）不在這次的清單就刪掉，
    曲庫索引裡的那一筆也一起拿掉——否則選歌畫面會同時出現新舊兩套課程。"""
    keep = set(keep)
    removed = set()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if (name.startswith(CATEGORY) and name not in keep and os.path.isdir(path)
                and os.path.isfile(os.path.join(path, 'tutorial.json'))):
            shutil.rmtree(path)
            removed.add(name)
    if not removed:
        return
    path = os.path.join(root, 'library.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    data['songs'] = [song for song in data.get('songs', [])
                     if not (isinstance(song, dict) and song.get('folderName') in removed)]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _register_category(root: str, folders: Sequence[str]) -> None:
    path = os.path.join(root, 'library.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    songs = data.setdefault('songs', [])
    known = {s.get('folderName'): s for s in songs if isinstance(s, dict)}
    for folder in folders:
        entry = known.get(folder)
        if entry is None:
            entry = {'id': 'portable:%s' % folder, 'folderName': folder}
            songs.append(entry)
        entry['category'] = CATEGORY
        entry['categories'] = [CATEGORY]
    if CATEGORY not in data.setdefault('categories', []):
        data['categories'].append(CATEGORY)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    path = os.path.join(root, 'songlist.json')
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault('categories', {})[CATEGORY] = list(folders)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    rows = build({'k265': sys.argv[1], 'k545': sys.argv[3], 'danse': sys.argv[4],
                  'campanella': sys.argv[5], 'prokofiev': sys.argv[6],
                  'scarbo': sys.argv[7], 'winterwind': sys.argv[8]}, sys.argv[2])
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
