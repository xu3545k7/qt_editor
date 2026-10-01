# -*- coding: utf-8 -*-
"""重新判斷左右手：照「手往哪裡移動最省」分，而不是照音高切一刀。

為什麼需要這個
--------------
`smart_chart._assign_hands` 只有在來源 MIDI 有兩條音軌時才知道真正的分手；
單軌來源（AI 音訊轉譜就是）走的是另一條路——**用全曲音高的中位數切一刀**，
再在每個同時發音組裡把「最低的 k 顆」給左手。結果有兩個問題：

1. 同一條旋律線只要跨過那條線就換手，畫面上看起來紅藍亂跳。
2. 「手搆不搆得到」是用**鍵道**量的（上限 9 條），但 28 條鍵道要蓋 88 個琴鍵，
   一條鍵道平均 3 個半音——9 條 ≈ 28 個半音，比兩個八度還寬。實測那份 AI 譜有
   29 個時刻單手跨超過一個八度，其中 18 個就是這樣通過檢查的。

做法
----
把音符按起音時間分組，逐組決定「這一組哪幾顆給左手」。每個分法的代價是：

* **移動**：手從上一個位置移到這一組的距離（半音）。這是主要項——旋律線連續
  就不會換手。
* **張不開**：單手同時（含還按著的長條）跨距超過 `max_span` 個半音，超出多少
  罰多少。這裡用**半音**量，不用鍵道。
* **交叉**：左手彈得比右手高。真的會發生，所以是罰分不是禁止。
* **一手包辦**：四顆以上的和弦全給同一隻手（官方幾乎不這樣寫，見記憶
  chord-overlap-and-hand-split）。

因為「這一組怎麼分」會影響後面幾組的代價，用 beam search 往前找，不是每組各自
貪心。

回傳改了幾顆。呼叫端要自己決定要不要把 `track` 也跟著拆成兩條——單軌的譜下次
再跑排譜器時，`_assign_hands` 會照音高中位數重新切一刀把這裡的結果蓋掉
（見記憶 hand-vs-track）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 同一隻手最多幾根手指
MAX_FINGERS = 5
#: 單手同時按的音最多跨幾個半音（八度；再寬就是張不開了）
MAX_SPAN = 12
#: 每個半音的移動代價
W_MOVE = 1.6
#: 張不開：超出 MAX_SPAN 的每個半音
W_SPAN = 8.0
#: 還按著的長條造成的跨距，罰得輕一點——踏板和換指可以補，而且那顆音已經按下去
#: 了，真正要判斷的是「新的這顆該給誰」
W_SPAN_HELD = 2.0
#: 交叉：左手比右手高的每個半音
W_CROSS = 2.0
#: 四顆以上的和弦全給同一隻手
W_HOG = 6.0
#: 接續同一條線的獎勵：這隻手剛剛才在附近彈過，就讓它接下去
W_LINE = 8.0
#: 「剛剛」「附近」各是多少（ms、半音）
LINE_MS = 150
LINE_SEMITONES = 5
# 不對「閒置很久的手」打折：原本讓閒置 700ms 以上的手移動免費，想法是「手有
# 時間走過去」，但實測那正是「一條跑動中間插進幾顆別隻手」的來源——閒著的那隻
# 手可以零成本搶走另一手正在跑的音階。拿掉之後，58 條跑動裡中途換手的從 14 條
# 降到 2 條。
#: beam 寬度
BEAM = 12
#: 起音時間差幾毫秒以內算同一組
GROUP_MS = 12


def _pitch(note: Any) -> int:
    return int(getattr(note, 'pitch', None) or 0)


def _start(note: Any) -> int:
    return int(getattr(note, 'start', 0))


#: note_type 的長押位元（見記憶 note-type-bitmask）
LONG_BIT = 2


def _occupies_until(note: Any) -> int:
    """這顆音把手指佔到什麼時候。

    只有**真的長押**才佔著手指。Tap 的 `end` 是聲音結束的時間（AI 轉譜是
    note-off、MIDI 轉譜是延音），不是手指離鍵的時間：這份譜 tap 的長度中位數
    268 ms，而發音間隔只有 53 ms，把 tap 也算成佔用的話一隻手瞬間就「五指全
    滿」，後面的音全被逼到另一隻手——實測會讓「單手跨超過八度」從 29 個時刻
    暴增到 519 個。
    """
    if int(getattr(note, 'note_type', 0)) & LONG_BIT:
        return int(getattr(note, 'end', 0))
    return _start(note)


def group_by_onset(notes: Sequence[Any], window: int = GROUP_MS) -> List[List[Any]]:
    """按起音時間分組。差在 `window` 以內算同時。"""
    ordered = sorted(notes, key=lambda n: (_start(n), _pitch(n)))
    out: List[List[Any]] = []
    for note in ordered:
        if out and _start(note) - _start(out[-1][0]) <= window:
            out[-1].append(note)
        else:
            out.append([note])
    return out


class _Hand:
    """一隻手：位置、上次動的時間、還按著的長條。"""

    __slots__ = ('pos', 'when', 'held')

    def __init__(self, pos: float, when: int, held: Tuple[Tuple[int, int], ...] = ()):
        self.pos = pos
        self.when = when
        self.held = held

    def active(self, now: int) -> List[int]:
        return [p for p, end in self.held if end > now]

    def advance(self, now: int, pitches: Sequence[int], ends: Sequence[int]) -> '_Hand':
        held = tuple((p, e) for p, e in self.held if e > now)
        held = held + tuple(zip(pitches, ends))
        if not pitches:
            return _Hand(self.pos, self.when, held)
        return _Hand(sum(pitches) / float(len(pitches)), now, held)

    def key(self) -> Tuple[int, int]:
        return (int(round(self.pos)), len(self.held))


def _subsets(size: int) -> List[int]:
    """這一組有幾種分法（bitmask，bit=1 代表給左手）。

    六顆以內把所有組合都試過；再多就只試「照音高切一刀」的分法——那時候組合
    數會爆掉，而且那麼大的和弦本來就不會交叉。
    """
    if size <= 6:
        return list(range(1 << size))
    return [(1 << k) - 1 for k in range(size + 1)]


def _span_cost(struck: Sequence[int], held: Sequence[int], max_span: int) -> float:
    """這隻手張不張得開。新按的和還按著的分開罰，理由見 `W_SPAN_HELD`。"""
    cost = 0.0
    if len(struck) >= 2:
        over = (max(struck) - min(struck)) - max_span
        if over > 0:
            cost += W_SPAN * over
    both = list(struck) + list(held)
    if struck and held and len(both) >= 2:
        over = (max(both) - min(both)) - max_span
        if over > 0:
            cost += W_SPAN_HELD * over
    return cost


def _move_cost(hand: _Hand, pitches: Sequence[int], now: int) -> float:
    """這隻手從上一個位置移到這一組要走多遠（半音），扣掉接線的獎勵。

    光看「誰比較近」不夠：另一隻手從頭到尾沒動過，它停在初始位置，跑動經過那
    附近時就會變成「比較近的那一隻」，於是一條音階跑到一半換手。所以**剛剛才
    在附近彈過**的那隻手有額外的獎勵——那就是「這條線是我在彈」的意思。
    """
    if not pitches:
        return 0.0
    centre = sum(pitches) / float(len(pitches))
    cost = W_MOVE * abs(centre - hand.pos)
    if now - hand.when <= LINE_MS and abs(centre - hand.pos) <= LINE_SEMITONES:
        cost -= W_LINE
    return cost


def assign(notes: Sequence[Any], max_span: int = MAX_SPAN,
           beam: int = BEAM, window: int = GROUP_MS) -> int:
    """重新判斷左右手（`note.hand`：0 右、1 左）。回傳改了幾顆。"""
    pitched = [n for n in notes if getattr(n, 'pitch', None) is not None]
    if len(pitched) < 2:
        return 0
    groups = group_by_onset(pitched, window)

    # 開局：用第一組的音高中心當兩手的起點，讓第一組不會被移動代價綁住
    first = groups[0]
    centre = sum(_pitch(n) for n in first) / float(len(first))
    start = (_Hand(centre - 12, -10 ** 6), _Hand(centre + 12, -10 ** 6))
    states: List[Tuple[float, Tuple[_Hand, _Hand], List[int]]] = [(0.0, start, [])]

    for group in groups:
        now = _start(group[0])
        ordered = sorted(group, key=_pitch)
        pitches = [_pitch(n) for n in ordered]
        ends = [_occupies_until(n) for n in ordered]
        nxt: Dict[Tuple, Tuple[float, Tuple[_Hand, _Hand], List[int]]] = {}
        for cost, (left, right), picks in states:
            l_held = left.active(now)
            r_held = right.active(now)
            for mask in _subsets(len(ordered)):
                li = [i for i in range(len(ordered)) if mask >> i & 1]
                ri = [i for i in range(len(ordered)) if not mask >> i & 1]
                # 手指數只管**這一刻新按下去的**。還按著的長條不佔名額：鋼琴家
                # 會換指、會踩踏板放掉，把它算成佔用會讓一隻手「五指全滿」之後
                # 把後面的音全推給另一手。
                if len(li) > MAX_FINGERS or len(ri) > MAX_FINGERS:
                    continue
                lp = [pitches[i] for i in li]
                rp = [pitches[i] for i in ri]
                extra = (_span_cost(lp, l_held, max_span)
                         + _span_cost(rp, r_held, max_span))
                extra += _move_cost(left, lp, now) + _move_cost(right, rp, now)
                if lp and rp and max(lp) > min(rp):
                    extra += W_CROSS * (max(lp) - min(rp))
                if len(ordered) >= 4 and (not lp or not rp):
                    extra += W_HOG
                nl = left.advance(now, lp, [ends[i] for i in li])
                nr = right.advance(now, rp, [ends[i] for i in ri])
                key = (nl.key(), nr.key())
                total = cost + extra
                if key not in nxt or total < nxt[key][0]:
                    nxt[key] = (total, (nl, nr), picks + [mask])
        if not nxt:                                  # 理論上不會發生，保險
            continue
        states = sorted(nxt.values(), key=lambda s: s[0])[:beam]

    best = states[0][2]
    changed = 0
    for group_index, group in enumerate(groups):
        mask = best[group_index] if group_index < len(best) else 0
        for i, note in enumerate(sorted(group, key=_pitch)):
            hand = 1 if mask >> i & 1 else 0
            if int(getattr(note, 'hand', 0)) != hand:
                note.hand = hand
                changed += 1
    return changed
