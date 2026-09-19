# -*- coding: utf-8 -*-
"""從一份譜面生出 normal / hard / extreme 三個難度。

實測官方 2242 份譜（606 首 × normal/hard/extreme + 424 份 real，215 萬顆音符）
得到的結論，決定了這裡的做法：

**同一首歌的四個難度，音訊事件集完全相同。** 每首歌各難度的
`sum(max(1, nsub))` 除以 real 的值**剛好是 1.000**。降難度從來不刪音符——它把
音符折進鄰近可見音符的 `sub_note_data`，也就是編輯器「遊戲譜面隱藏 NOTE」用的
同一套機制。所以難度生成 = **決定哪些事件拿得到一個看得見的按鍵**。

**鍵道位置是每個難度各自重排的，不是繼承來的。** 在 real 和某難度都可見、且
時間音高都一樣的音符，鍵道只有 54.5%（extreme）／38.5%（hard）／22.7%
（normal）對得上。所以生成器一定要重跑排譜器，光隱藏音符是不夠的。

**哪一顆會變成看得見的那顆**：它吸收的那一組裡的外聲部，而且分左右手——右手
取最高音 90%（real）／74%（normal）；左手最高 69%、最低 30%。內聲部只有 1~12%
的機會當寄主。

**hand=2 不是左右手合併**：normal 的 hand=2 音符有 78% 出現在「該時刻只有
hand=2」的地方。它是簡單譜用的「未指定手」。

滑音刻意不生成。官方的滑音是低難度的語彙（normal 4.2%、hard 8.0%、extreme
4.5%、real 0.8%），但哪裡該用滑音是編曲判斷，猜錯要一顆一顆改回來——和
`_classify_articulations` 預設關掉的理由一樣。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from bisect import bisect_left, bisect_right
from typing import (Any, Dict, List, NamedTuple, Optional, Sequence, Set,
                    Tuple)

#: 生成的難度名，由易到難。
DIFFICULTIES = ('normal', 'hard', 'extreme')


@dataclass(frozen=True)
class DifficultyTargets:
    """一個難度的目標值，全部來自官方語料的實測中位數。"""

    name: str
    #: 匯出時用的難度名（資料夾名／register.json 的 difficultyName）。
    #: 內部 key 沿用官方語料的名字（extreme），顯示名照這個遊戲的習慣。
    label: str
    #: **發音點要保留幾成**（相對於來源）。這是主控項。
    #:
    #: 官方 30 首四難度齊全的曲子實測，發音點保留 64% / 78% / 91%，而每個
    #: 發音點的顆數只留 80% / 78% / 89%——也就是說降難度主要是**把和弦拆薄**，
    #: 節奏幾乎不動。extreme 尤其明顯：91% 的發音點都在，差別幾乎只在雙音變
    #: 單音。
    #:
    #: 用比例而不是絕對間隔，是因為比例會跟著曲子的密度走：同樣一條規則套在
    #: 簡單曲和難曲上，得到的都是「相對於這首歌的低難度」。拿絕對的間隔中位數
    #: 當目標會讓密的曲子被砍過頭——實測我的保留率只有 41/61/81%，比官方少了
    #: 一大截，使用者的原話是「官方 expert 只是把雙音改單音指法而已，我們是不是
    #: 砍太多」。
    onset_ratio: float
    #: 同一隻手相鄰發音點的間隔中位數（ms）。只當**上限**：特別密的曲子照比例
    #: 砍完還是太快時，用它再壓一次。鬼火的 real 是 17.9 nps（官方 real 中位
    #: 12.45），照 64% 的比例它的 Normal 會有 11 nps，那不是 Normal。
    same_hand_gap_ms: int
    #: 同時看得見幾顆
    max_simultaneous: int
    #: **一隻手**同時要應付幾件事，把還按著的長押也算進去。
    #:
    #: `max_simultaneous` 只數「這一刻新發音的顆數」，長押按下去之後那隻手就
    #: 一直被佔著，但它不再是任何一組的成員，所以數不到。實測 Expert 有 14 個
    #: 時刻單手是「2 個新音 + 2 個按住中 = 4 件事、橫跨 12 格」——手不可能
    #: 這樣張開。
    max_hand_load: int
    #: 每保留幾次，就連著多留一顆（＝留下一段原速的快速句）。
    #:
    #: 官方**不抬高下限**：normal / hard / extreme 的同手間隔 p10 全都是
    #: 79ms、100ms 以下的全都佔 24%，差別只在上半部（中位 190/176/168、
    #: p75 395/326/304）。也就是快速句在每個難度都在，變的是句與句之間的
    #: 空隙。用「每個間隔都不得小於 T」去砍會把快速句整批消滅——實測 100ms
    #: 以下的比例從官方的 24% 掉到 0%，整首變成平均速度的節拍器。
    #:
    #: 每 k 次保留配一個「成對」的話，短間隔佔比是 1/(k+1)，所以 k=3 對應
    #: 官方的 24%。（token bucket 在這裡沒用：鬼火整首都是 88ms 的均勻密集，
    #: 桶子永遠存不到第二個 token，退化成等間距。）
    burst: int
    #: 同一隻手、同一時刻的音符，最外側到最外側最多佔幾條鍵道。
    #:
    #: 官方實測的跨度分布差很多：normal 中位 5、p90 **13**、p99 23；
    #: hard 中位 4、p90 9；extreme 中位 6、p90 8、p99 10。低難度反而更寬——
    #: 它同時發聲的顆數少，兩顆之間可以拉得很開來表現音程。
    #:
    #: 上限訂太緊會逼出「完全貼合」：Normal 的 2 顆寬 5 就是 10 格，卡在 10
    #: 等於一點空隙都留不下，實測 100% 的和弦被壓成貼合、看不出音程差別。
    max_hand_span: int
    #: 滑音佔可見音符的目標比例。官方 normal 2.0%、hard 2.4%、extreme 3.7%，
    #: real 幾乎是 0——滑音是**低難度的語彙**：一整串音只要滑一次，節奏留住了
    #: 但手指不用一顆一顆打。
    slide_ratio: float
    #: 顫音佔可見音符的目標比例。官方 0.099% / 0.209% / 0.287%（real 0.111%），
    #: 長度中位 721 / 774 / 481ms。很稀疏，但每個難度都有。
    trill_ratio: float
    #: 距離下一顆同手音在這個時間內，就算「後面接快速句」。
    #: 官方 extreme 在 <120ms 的情形下有 **99.0%** 是單音（2 顆只有 0.6%）
    #: ——手要立刻去打下一顆，沒有餘裕按和弦。
    fast_follow_ms: int
    #: 音符寬度（佔幾條鍵道）的眾數
    note_width: int
    #: 寬度要不要**完全一致**。三個難度都是 True——收窄的音符在低難度更不能
    #: 有：愈簡單的譜，音符愈該一眼看得出來，寬度 2 的鍵在寬度 5 的譜裡看起來
    #: 像雜訊。官方的一致度是 normal 66% / hard 93% / extreme 99%，所以
    #: normal、hard 這裡比官方更嚴。
    #:
    #: 打開之後三條收窄路徑（剛好 4 音同手、整組跨度、近音程對）和容量收窄
    #: 全部停用——它們都是拿 `dense_width` 去壓。壓不下時官方本來就改用鍵道
    #: 重疊而不是收窄。
    uniform_width: bool
    #: hand=2（未指定手）的比例。**預設 0**：官方 normal 有 54.3%，但
    #: nos-clone 是 `hand == 0 ? 紅 : 藍`，hand=2 會整批畫成左手藍色——
    #: 實測 normal 有 335/833 顆本來是右手的音變成藍色，看起來就是「左右手
    #: 亂標註」。等遊戲端有了第三種外觀再把官方比例填回來。
    hand2_ratio: float
    #: 長押（note_type 2）的比例。官方是 3.9 / 5.4 / 8.0%，低難度這裡再壓
    #: 得更低——按住並在對的時間放開是比點擊難一階的動作，簡單譜不該常出現。
    long_ratio: float
    #: 長押的最短長度（ms）。比這個短的一律改成一般音符：短長押要求的是
    #: 「按下去、幾乎立刻放開」，在慢速譜上比同樣長度的點擊更難，而且視覺上
    #: 幾乎看不出是長押。
    min_long_ms: int
    #: 相對於來源譜面的可見音符比例。**只用來回報**，不當控制項——
    #: 它是官方語料的中位數，而來源譜面不一定落在中位數上。
    visible_ratio: float
    #: 難度等級的合法範圍（含兩端）。官方 357 首完整資料量出來是
    #: normal 1~8、hard 3~11、extreme 5~12、real 9~15，中位分別是 4/7/11/13。
    level_band: Tuple[int, int]


TARGETS: Dict[str, DifficultyTargets] = {
    # 間隔目標用**實測官方中位數**（60 份取樣：190/176/168），不用舊筆記的
    # 403/312/198。差別在 normal：舊值是把 hand=2 當第三隻手分桶量出來的，
    # 官方 normal 有 54% 是 hand=2，一分桶間隔就被拉長了一倍。
    # 中位數目標。**不是**直接抄官方的 190/176/168——官方三個難度的中位數
    # 很接近，難度差在上半部（p75 395/326/304）。照抄的話三個難度會擠成
    # 7.6 / 9.9 / 12.0 nps，看不出階梯。這裡拉開中位數來做出階梯，快速句
    # 則由 `burst` 保住（<100ms 的佔比 17/17/27%，官方是 24%）。
    'normal':  DifficultyTargets('normal',  'Normal', 0.64, 400, 2, 2, 2, 13, 0.020, 0.0010, 120, 5, True, 0.0, 0.020, 700, 0.427, (1, 6)),
    'hard':    DifficultyTargets('hard',    'Hard',   0.78, 240, 2, 2, 2,  9, 0.024, 0.0021, 120, 4, True, 0.0, 0.040, 500, 0.493, (3, 11)),
    'extreme': DifficultyTargets('extreme', 'Expert', 0.91, 100, 3, 2, 2,  9, 0.037, 0.0029, 120, 3, True, 0.0, 0.080, 300, 0.824, (6, 12)),
}

#: 同一首歌的 real 等級 → 其他三個難度的等級（官方 357 首的中位數）。
#:
#: 等級是綁在**同一首歌**上的，不是各自照密度給的：real 12 的曲子，它的
#: extreme 幾乎一定是 10、hard 是 7、normal 是 3。落差本身也隨等級縮小
#: （real − extreme 在 real 10 時是 3，到 real 15 只剩 3 但比例縮了）。
LEVEL_BY_REAL: Dict[int, Tuple[int, int, int]] = {
    9:  (1, 3, 5),
    10: (3, 6, 7),
    11: (3, 6, 9),
    12: (3, 7, 10),
    13: (4, 7, 11),
    14: (4, 8, 12),
    15: (5, 9, 12),
}


#: 由譜面自己的指標推等級：`level = a*nps + b*peak + c`。
#:
#: 在官方語料上最小平方擬出來的（119 份／難度），平均誤差 0.85 級、±1 級內
#: 61~69%。`peak` 是「最忙的那一秒有幾顆」——密度的尖峰比平均更能代表難度。
#:
#: 單用它不夠準，所以和 `LEVEL_BY_REAL` 併用：前者看這份譜實際長什麼樣，
#: 後者看這首歌本身有多難，兩個誤差來源不一樣，平均起來比任一個都穩。
LEVEL_FIT: Dict[str, Tuple[float, float, float]] = {
    'normal':  (0.1243,  0.0266, 2.569),
    'hard':    (0.3734, -0.0301, 5.131),
    'extreme': (0.3479,  0.0154, 6.534),
}


def peak_notes_per_second(notes: Sequence[Any]) -> int:
    """最忙的那一秒有幾顆音。"""
    times = sorted(int(n.start) for n in notes)
    best = left = 0
    for right in range(len(times)):
        while times[right] - times[left] > 1000:
            left += 1
        best = max(best, right - left + 1)
    return best


def level_from_indicators(difficulty: str, notes_per_second: float,
                          peak: int) -> int:
    """只看譜面指標的等級估計。"""
    key = str(difficulty or '').lower()
    goal = TARGETS.get(key)
    if goal is None:
        raise ValueError('unknown difficulty: %r' % (difficulty,))
    a, b, c = LEVEL_FIT[key]
    bottom, top = goal.level_band
    return max(bottom, min(top, int(round(a * notes_per_second + b * peak + c))))


def worth_generating(real_level: int, difficulty: str) -> bool:
    """來源夠難、值得生這個難度嗎。

    每個難度都有等級下限（Normal 1、Hard 3、Expert 6）。來源只有 Lv.5 的曲子
    生不出「比它簡單的 Expert」——Expert 的下限就是 6。那種情況該直接不生，
    而不是硬給一個和來源一樣或更高的等級。
    """
    key = str(difficulty or '').lower()
    goal = TARGETS.get(key)
    if goal is None:
        raise ValueError('unknown difficulty: %r' % (difficulty,))
    return int(real_level) <= 0 or int(real_level) > goal.level_band[0]


def level_for(difficulty: str, notes_per_second: float, peak: int,
              real_level: int = 0) -> int:
    """最終等級：譜面指標與來源等級表各出一半。

    使用者的要求是「不要只照 real 難度推斷」——同一個 real 等級的兩首歌，
    生出來的譜可以差很多（來源的密度、和弦厚度都不同），只看來源會給出一樣
    的等級。反過來只看指標也不夠：指標模型的平均誤差是 0.85 級。
    """
    measured = level_from_indicators(difficulty, notes_per_second, peak)
    if not real_level or real_level <= 0:
        return measured
    goal = TARGETS[str(difficulty).lower()]
    bottom, top = goal.level_band
    # 生出來的難度一定比來源簡單——它是來源砍出來的。指標和來源表都可能給出
    # 「和來源一樣」的數字（實測 asyurasyura 的 Expert 被判成 10、來源也是
    # 10），那對玩家是錯的資訊。
    top = min(top, int(real_level) - 1)
    if top < bottom:
        top = bottom
    blended = (measured + estimate_level(real_level, difficulty)) / 2.0
    return max(bottom, min(top, int(round(blended))))


def estimate_level(real_level: int, difficulty: str) -> int:
    """由來源（real）的等級推算某個難度該標幾級，並夾進該難度的範圍。

    表外的等級用最後兩列（或最前兩列）的斜率外推——鬼火的 real 是 16，比官方
    表的上限還高一級。
    """
    key = str(difficulty or '').lower()
    goal = TARGETS.get(key)
    if goal is None:
        raise ValueError('unknown difficulty: %r' % (difficulty,))
    column = {'normal': 0, 'hard': 1, 'extreme': 2}[key]
    known = sorted(LEVEL_BY_REAL)
    real = int(real_level)
    if real in LEVEL_BY_REAL:
        value = LEVEL_BY_REAL[real][column]
    elif real < known[0]:
        low, high = known[0], known[1]
        slope = (LEVEL_BY_REAL[high][column] - LEVEL_BY_REAL[low][column]) / float(high - low)
        value = LEVEL_BY_REAL[low][column] + slope * (real - low)
    else:
        low, high = known[-2], known[-1]
        slope = (LEVEL_BY_REAL[high][column] - LEVEL_BY_REAL[low][column]) / float(high - low)
        value = LEVEL_BY_REAL[high][column] + slope * (real - high)
    bottom, top = goal.level_band
    return max(bottom, min(top, int(round(value))))


#: 官方三個難度的同手間隔分布（60 份取樣）。`p10` 和 `<100ms` 三個難度一樣，
#: 是「快速句不隨難度消失」的直接證據。
OFFICIAL_GAP_SHAPE = {
    'normal':  {'p10': 79, 'under_100': 0.243, 'median': 190, 'p75': 395},
    'hard':    {'p10': 79, 'under_100': 0.242, 'median': 176, 'p75': 326},
    'extreme': {'p10': 79, 'under_100': 0.246, 'median': 168, 'p75': 304},
}

#: 官方語料量到的同時可見顆數（normal 2 / hard 3 / extreme 4~5）。這裡的
#: Expert 3 / Hard 2 / Normal 2 比官方更保守。
OFFICIAL_MAX_SIMULTANEOUS = {'normal': 2, 'hard': 3, 'extreme': 5}

#: 官方語料量到的長押比例，供對照。normal/hard 這裡刻意壓得更低。
OFFICIAL_LONG_RATIO = {'normal': 0.039, 'hard': 0.054, 'extreme': 0.080}

#: 官方語料量到的 hand=2 比例。nos-clone 畫得出第三種外觀之後，把它填回
#: `TARGETS` 的 `hand2_ratio` 就會恢復官方行為。
OFFICIAL_HAND2_RATIO = {'normal': 0.543, 'hard': 0.145, 'extreme': 0.029}

#: 同一個「發音組」的時間容許量，和排譜器的 `onset_tolerance_ms` 一致。
ONSET_TOLERANCE_MS = 35


class DifficultyResult(NamedTuple):
    """生成結果。數字都是給人看的——生成器的參數是猜出來的，要能驗。"""

    difficulty: str
    total: int              #: 音訊事件總數（不會變）
    visible: int            #: 看得見、要按的音符數
    hidden: int             #: 折進寄主 sub_note 的音符數
    orphans: int            #: 找不到寄主而被迫留下的音符數（應該是 0）
    visible_ratio: float    #: visible / total
    notes_per_sec: float
    gap_ms: float           #: 同手發音間隔中位數（含 hand=2，對照官方表）
    gap_hands_ms: float     #: 同上但只看實體左右手 —— 真正的手速指標
    gap_target_ms: int      #: 官方的目標值
    max_simultaneous: int
    hand2_ratio: float
    long_ratio: float
    peak_notes: int         #: 最忙的那一秒有幾顆
    gap_threshold_ms: int   #: 二分搜尋收斂到的門檻，除錯用


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _pitch(note: Any) -> int:
    value = getattr(note, 'pitch', None)
    return int(value) if value is not None else 0


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _onset_groups(notes: Sequence[Any]) -> List[List[Any]]:
    """依起音時間分組，容許 `ONSET_TOLERANCE_MS`。"""
    ordered = sorted(notes, key=lambda n: (int(n.start), _pitch(n)))
    groups: List[List[Any]] = []
    for note in ordered:
        if groups and int(note.start) - int(groups[-1][0].start) <= ONSET_TOLERANCE_MS:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def _outer_first(notes: Sequence[Any]) -> List[Any]:
    """一隻手之內的保留順序：由外聲部往內。

    最高音先，再來最低音，然後次高、次低……官方的寄主分布就是這個形狀
    （右手最高音 90%、左手最高 69% 最低 30%，內聲部只有個位數的百分比）。
    """
    ordered = sorted(notes, key=_pitch, reverse=True)
    out: List[Any] = []
    low, high = 0, len(ordered) - 1
    take_top = True
    while low <= high:
        if take_top:
            out.append(ordered[low])
            low += 1
        else:
            out.append(ordered[high])
            high -= 1
        take_top = not take_top
    return out


# ----------------------------------------------------------------------
# 選音
# ----------------------------------------------------------------------
def _cap_chords(groups: Sequence[Sequence[Any]], cap: int,
                protected: Optional[Set[int]] = None) -> List[Any]:
    """每個發音組只留 `cap` 顆，兩手輪流各取自己的外聲部。

    輪流是為了讓兩隻手都留得住東西——只照音高取的話，normal（cap=2）會把
    整組都給音域高的那隻手，另一隻手整段消失。
    """
    kept: List[Any] = []
    for group in groups:
        if len(group) <= cap:
            kept.extend(group)
            continue
        by_hand: Dict[int, List[Any]] = {}
        for note in group:
            by_hand.setdefault(int(getattr(note, 'hand', 0)), []).append(note)
        queues = [_outer_first(v) for _k, v in sorted(by_hand.items())]
        picked: List[Any] = [n for n in group if id(n) in (protected or set())]
        for queue in queues:
            queue[:] = [n for n in queue if id(n) not in (protected or set())]
        index = 0
        while len(picked) < cap and any(queues):
            queue = queues[index % len(queues)]
            if queue:
                picked.append(queue.pop(0))
            index += 1
            if index > cap * len(queues) * 4:      # 保險，理論上到不了
                break
        kept.extend(picked)
    return kept


def _restore_soft(visible: Sequence[Any]) -> int:
    """soft（note_type 1）一律還原成一般音符。

    soft 是編輯器專用的表情記號，官方語料裡完全沒有；`_classify_articulations`
    也預設不寫它。但來源譜面可能是人手標的，稀疏化留下來之後就殘留在低難度上
    ——實測全庫的生成難度有 3.4~3.6% 是 soft。降難度是要讓譜更好讀，多一種
    只有這裡才有的記號沒有意義。
    """
    changed = 0
    for note in visible:
        if _kind(note) in (KIND_SOFT, KIND_STACCATO):
            note.note_type = int(note.note_type) & (FLAG_SLIDE | FLAG_TRILL)
            changed += 1
    return changed


def _thin_before_fast(kept: Sequence[Any], fast_ms: int,
                      protected: Optional[Set[int]] = None) -> List[Any]:
    """後面緊接著快速音時，那一刻同手只留冠音（最高音）。

    官方 extreme 實測：距離下一顆同手音 <120ms 時，**99.0%** 是單音，2 顆只有
    0.6%；放寬到 120~250ms 才有 12.6% 是兩顆，≥250ms 是 25.8%。手要立刻去打
    下一顆，沒有餘裕按和弦——保留節奏點不代表那個點要按滿。

    留最高音是因為那是旋律線；官方的寄主分布也是右手最高音 90%。
    """
    if fast_ms <= 0:
        return list(kept)
    by_hand: Dict[int, Dict[int, List[Any]]] = {}
    for note in kept:
        hand = int(getattr(note, 'hand', 0))
        by_hand.setdefault(hand, {}).setdefault(int(note.start), []).append(note)
    dropped: Set[int] = set()
    for at_time in by_hand.values():
        times = sorted(at_time)
        for index, when in enumerate(times[:-1]):
            group = at_time[when]
            if len(group) < 2 or times[index + 1] - when >= fast_ms:
                continue
            keep = max(group, key=lambda n: (_pitch(n), int(n.min_key)))
            for note in group:
                # 滑音鏈是一個滑過去的動作，不是要同時按下的和弦
                if note is not keep and id(note) not in (protected or set()):
                    dropped.add(id(note))
    return [n for n in kept if id(n) not in dropped]


#: 同一條滑音鏈內，相鄰兩顆的時間上限。官方的滑音間隔中位是 97~120ms。
SLIDE_CHAIN_GAP_MS = 200

#: 顫音尾端要比最後一擊短多少毫秒。和長押的「尾端前移」同一個預設值。
TRILL_TAIL_ADVANCE_MS = 40

#: 一條滑音至少要幾節。手寫譜最短的鏈是 2 節。
MIN_SLIDE_CHAIN = 2


def _slide_chains(notes: Sequence[Any]) -> List[List[Any]]:
    """把來源的滑音串成鏈：同一隻手、時間相鄰的一串 note_type 0x04。"""
    slides = sorted((n for n in notes
                     if int(getattr(n, 'note_type', 0)) & FLAG_SLIDE),
                    key=lambda n: (int(getattr(n, 'hand', 0)), int(n.start)))
    out: List[List[Any]] = []
    current: List[Any] = []
    for note in slides:
        # 鏈的界線只看**出身**和**時間**，不看鍵道。
        #
        # 自製的滑音只跟同一段階梯的同伴串在一起——只靠時間分組的話，兩段
        # 各自獨立的階梯會被黏成一條，實測 Melodiniq 的 Hard 黏出兩條各 28
        # 節、鍵道來回跳 ±6 的「滑音」。
        #
        # 來源本來就有的沒有出身標記，照舊只看同一手、起音相差不超過
        # `SLIDE_CHAIN_GAP_MS`。**不可以**再加鍵道條件：鍵道正是排譜器會弄
        # 壞的東西（它用 35ms 容差把一段 8ms 一顆的快速下行當成和弦，一口氣
        # 攤成 23/18/13/8/3），拿撐開後的鍵道去切，作者寫好的一條 18 節滑音
        # 會被剁成四段，`_straighten_slide_chains` 也就收不回來了。
        if current:
            mine = getattr(note, '_slide_run', None)
            theirs = getattr(current[-1], '_slide_run', None)
            joinable = mine == theirs if (mine is not None or theirs is not None) else True
        else:
            joinable = False
        if (current
                and joinable
                and int(getattr(note, 'hand', 0)) == int(getattr(current[-1], 'hand', 0))
                and int(note.start) - int(current[-1].start) <= SLIDE_CHAIN_GAP_MS):
            current.append(note)
        else:
            if current:
                out.append(current)
            current = [note]
    if current:
        out.append(current)
    return out


#: 一串「連得起來」的相鄰音符，最大時間間隔。官方滑音的間隔中位是 97~120ms。
RUN_GAP_MS = 140
#: 階梯要幾顆才值得壓成滑音。官方的滑音串長中位是 3~4。
MIN_STAIR = 4
#: 顫音要幾擊才成立，以及它的長度上限。官方顫音長度中位 481~774ms。
MIN_TRILL_STROKES = 5
MAX_TRILL_MS = 1200


def _runs(notes: Sequence[Any], gap_ms: int = RUN_GAP_MS) -> List[List[Any]]:
    """同一隻手、時間連續（間隔 <= `gap_ms`）、每刻只有一顆的一串音符。

    每刻只有一顆是重點：階梯和顫音都是單音的快速句，和弦不算。
    """
    out: List[List[Any]] = []
    by_hand: Dict[int, Dict[int, List[Any]]] = {}
    for note in notes:
        by_hand.setdefault(int(getattr(note, 'hand', 0)), {}) \
               .setdefault(int(note.start), []).append(note)
    for at_time in by_hand.values():
        current: List[Any] = []
        previous = None
        for when in sorted(at_time):
            group = at_time[when]
            if len(group) != 1 or (previous is not None and when - previous > gap_ms):
                if len(current) > 1:
                    out.append(current)
                current = []
            if len(group) == 1:
                current.append(group[0])
                previous = when
            else:
                previous = None
        if len(current) > 1:
            out.append(current)
    return out


def _trill_segments(run: Sequence[Any]) -> List[List[Any]]:
    """一段樂句裡「兩音來回」的片段（彼此不重疊）。

    以前要求**整段樂句**從頭到尾都是兩音來回，於是顫音只要前後接著別的音就
    整段漏掉。實測全庫 320 段兩音來回裡有 171 段（53%）是這樣漏的——它們接
    著被當成一般的快速句稀疏化，留下斷斷續續的節奏點，而那正是顫音最不該被
    拆的地方（來回是一個持續動作，拆開之後每一顆都要單獨打）。
    """
    out: List[List[Any]] = []
    start = 0
    while start < len(run) - 1:
        pair = {_pitch(run[start]), _pitch(run[start + 1])}
        if len(pair) != 2:
            start += 1
            continue
        stop = start + 1
        while stop + 1 < len(run):
            following = _pitch(run[stop + 1])
            if following not in pair or following == _pitch(run[stop]):
                break
            stop += 1
        if stop - start + 1 >= MIN_TRILL_STROKES:
            out.append(list(run[start:stop + 1]))
            start = stop + 1
        else:
            start += 1
    return out


def _split_long_trill(segment: Sequence[Any]) -> List[List[Any]]:
    """太長的來回切成連續幾顆顫音，而不是整段放棄。

    官方顫音長度中位 481~774ms，`MAX_TRILL_MS` 是上限。超過的以前直接不壓，
    實測全庫有 39 段（12%）這樣漏掉，最長的 1982ms、16 擊——那種段落留成一
    顆一顆打是最難的，正好和「降低難度」的目的相反。
    """
    out: List[List[Any]] = []
    current: List[Any] = [segment[0]]
    for note in segment[1:]:
        if int(note.start) - int(current[0].start) > MAX_TRILL_MS:
            out.append(current)
            current = [note]
        else:
            current.append(note)
    out.append(current)
    return [chunk for chunk in out if len(chunk) >= MIN_TRILL_STROKES]


def _compress_trills(notes: Sequence[Any], ratio: float) -> int:
    """快速的兩音交替壓成一個顫音。

    交替是「同一組手指來回」——譜面上一顆一顆打很吃力，但它其實是一個持續的
    動作。官方每個難度都有顫音（0.099% / 0.209% / 0.287%，長度中位 481~774ms），
    只是很稀疏。

    做法和隱藏音符同一套：第一顆變成顫音的頭（`note_type |= 0x40`、`end` 拉到
    整串結束），其餘藏進它的 `sub_note`。遊戲端的 `RefreshTrill` 會照 subNote
    自己的時間把每一擊播出來。

    預算有限（官方就是這麼稀疏），所以**擊數多的優先**：越長的來回留成一顆一
    顆打就越難，壓成顫音的收益也越大。以前是照時間先到先得。
    """
    budget = int(len(notes) * float(ratio))
    if budget <= 0:
        return 0
    candidates: List[List[Any]] = []
    for run in _runs(notes):
        for segment in _trill_segments(run):
            if any(int(getattr(n, 'note_type', 0)) & (FLAG_SLIDE | FLAG_TRILL)
                   or getattr(n, 'hidden', False) for n in segment):
                continue                    # 已經是滑音／顫音，或本來就藏著
            candidates.extend(_split_long_trill(segment))
    candidates.sort(key=lambda seg: (-len(seg), int(seg[0].start)))

    made = 0
    for segment in candidates:
        if made >= budget:
            break
        head = segment[0]
        head.note_type = (int(head.note_type) & ~NOTE_KIND_MASK) | FLAG_TRILL
        # 尾端比最後一擊再短一點，和長押同一套做法（長押是「尾端前移固定
        # ms」，預設 40）。拉到最後一擊的結尾會讓顫音條緊貼著後面的音符，
        # 看起來像連在一起；而且顫音是持續按住的手勢，尾巴留一點空隙才知道
        # 什麼時候可以放開。
        head.end = max(int(segment[-1].start) + 1,
                       int(segment[-1].end) - TRILL_TAIL_ADVANCE_MS)
        head.end = max(int(head.end), int(head.start) + 1)
        head.gate = int(head.end) - int(head.start)
        for note in segment[1:]:
            note.hidden = True
            note._sub_host = head
        made += 1
    return made


def _compress_staircases(notes: Sequence[Any], ratio: float) -> int:
    """單調的長階梯壓成滑音。

    一整串音只要滑一次——節奏全部留住，手指卻不用一顆一顆打。官方的滑音正是
    這樣用的：normal 2.0%、hard 2.4%、extreme 3.7%，串長中位 3~4、間隔中位
    97~120ms、串內單調 78~100%、音高步進中位 3 半音；real 幾乎沒有滑音，因為
    最高難度就是要你一顆一顆打。

    回傳新做出來的滑音顆數。
    """
    budget = int(len(notes) * float(ratio))
    if budget <= 0:
        return 0
    made = 0
    for run in _runs(notes):
        if made >= budget:
            break
        if len(run) < MIN_STAIR:
            continue
        if any(int(getattr(n, 'note_type', 0)) & (FLAG_SLIDE | FLAG_TRILL)
               or getattr(n, 'hidden', False) for n in run):
            continue
        if any(int(a.end) - int(b.start) > int(b.start) - int(a.start)
               for a, b in zip(run, run[1:])):
            # 前一顆還按著的時候下一顆就進來，而且一路疊到最後——那不是階梯，
            # 是**一個音一個音疊起來的持續和弦**。滑音是手指滑過去、一顆接一
            # 顆放開的動作，重疊的音沒辦法用一次滑動打完。
            #
            # 實測 Ocean's Wish 的 Normal：來源右手是五顆各持續 2.4 秒的長押
            # （音高 62/66/69/71/74，起音差 61~67ms），音高單調、間隔也大於
            # 和弦容差，於是被當成階梯壓成滑音。壓完之後五個節點各自長 2.4
            # 秒、鍵道 16-20/16-20/17-21/18-22/19-23 幾乎完全重疊，畫面上就
            # 是五根長條疊在一起互相跨越。
            #
            # 判準是「重疊的時間有沒有超過一步的間距」：圓滑奏那種前後音略微
            # 相接（重疊遠小於間距）仍然算階梯。
            continue
        if any(int(b.start) - int(a.start) <= ONSET_TOLERANCE_MS
               for a, b in zip(run, run[1:])):
            # 節與節之間要是**各自的發音點**，不是同一個和弦裡的幾顆。官方
            # 滑音的節間隔中位是 97~120ms；靠得比和弦容差還近的那是刮奏。
            #
            # 壓成滑音會讓整條受保護、躲過收頂與稀疏化，之後比例超標又被
            # 還原成一般音符——保護吃掉了、密度一顆沒減。實測 Unwelcome
            # School 的來源有一條 76->52 的半音下行、每 7ms 一顆，就這樣把
            # 25 顆全帶進 Lv.3 的 Normal，排譜器再把每 6 顆當成一個和弦攤成
            # 23/20/15/10/5/0，跨度 28 條（整個鍵盤）。
            continue
        steps = [_pitch(b) - _pitch(a) for a, b in zip(run, run[1:])]
        if not steps or not (all(x > 0 for x in steps) or all(x < 0 for x in steps)):
            continue                            # 不單調就不是階梯
        if max(abs(x) for x in steps) > 5:
            continue                            # 跳太大的不是階梯
        # 出身要記住：這一段階梯是一條滑音。只靠「時間相鄰＋鍵道接近」
        # 分組的話，兩段各自獨立的階梯會被黏成一條——實測 Melodiniq 的
        # Expert 黏出一條 15 節、步進 -3/+2 完美交替的「滑音」，那其實是
        # 下行的斷奏音型，不是一個滑的動作。
        made += 1
        for note in run:
            note.note_type = (int(note.note_type) & ~NOTE_KIND_MASK) | FLAG_SLIDE
            note._made_slide = True
            note._slide_run = made
        made += len(run) - 1
    _chain_slides(notes)
    return made


def _chain_slides(notes: Sequence[Any]) -> int:
    """把滑音串成鏈：`param1` 指前一顆、`param2` 指下一顆，端點填 -1。

    只標 `note_type` 是不夠的——遊戲端要靠 `note_index` 和 `param1/param2` 才
    知道哪幾顆是同一條滑鍵。實測生出來的滑音是 `index=None, param1=0,
    param2=0`：完全沒有串鏈，而 `param2 == 0` 在格式上代表「未設定」，
    `index 0` 還會讓鏈結被誤判成未串鏈而觸發推測連線。

    `note_index` 從 1 開始（0 在格式上等於「沒有」），而且要避開譜面裡已經用
    掉的號碼——存檔時是照這個號碼寫出去的。
    """
    used = {int(n.note_index) for n in notes
            if getattr(n, 'note_index', None) is not None}
    nxt = max(used) + 1 if used else 1
    nxt = max(1, nxt)
    chains = _slide_chains([n for n in notes
                            if int(getattr(n, 'note_type', 0)) & FLAG_SLIDE])
    linked = 0
    for chain in chains:
        chain.sort(key=lambda n: (int(n.start), int(n.min_key)))
        for note in chain:
            if getattr(note, 'note_index', None) is None:
                while nxt in used:
                    nxt += 1
                note.note_index = nxt
                used.add(nxt)
                nxt += 1
        for index, note in enumerate(chain):
            note.param1 = chain[index - 1].note_index if index > 0 else -1
            note.param2 = (chain[index + 1].note_index
                           if index < len(chain) - 1 else -1)
            note.param3 = 0
        linked += len(chain)
    return linked


def _trim_generated_slides(visible: Sequence[Any], ratio: float) -> int:
    """把**自己造的**滑音修剪到目標比例；來源本來就有的一律不動。

    預算沒辦法在壓縮的當下就算準——那時候還不知道稀疏化會砍掉多少。滑音是受
    保護的（整條留），所以砍完一般音符之後它的比例會被放大：實測 Melodiniq 的
    Normal 變成 8.7%，官方是 2.0%。

    多出來的整條**還原成一般音符**（音符留著，只是不再是滑音），不是刪掉——
    節奏要保住，那才是壓成滑音的初衷。先還原短的，長的比較划算。
    """
    mine = [n for n in visible if getattr(n, '_made_slide', False)]
    allowed = int(len(visible) * float(ratio))
    if len(mine) <= allowed:
        return 0
    chains = _slide_chains(mine)
    chains.sort(key=len)
    reverted = 0
    for chain in chains:
        if len(mine) - reverted <= allowed:
            break
        for note in chain:
            note.note_type = int(note.note_type) & ~FLAG_SLIDE
            note._made_slide = False
        reverted += len(chain)
    if reverted:
        # 鏈少了，剩下的要重新串——不然被還原的那幾顆還被別人的 param 指著。
        _chain_slides(visible)
    return reverted


def _relink_slides(visible: Sequence[Any]) -> int:
    """所有會動到鍵道的步驟跑完之後，重新串一次滑音鏈。

    鏈是在 `_compress_staircases` 裡就串好的，但那時候鍵道還沒排——之後
    `_arrange`、`_cap_hand_span`、`_separate_hands`、`_resolve_hold_corridors`
    每一步都會搬鍵道，串好的鏈就對不上實際位置了。舊的重串只在
    `_trim_generated_slides` 真的還原了東西時才跑，所以平常那一條鏈從頭
    到尾都是排版前的。實測 Melodiniq 的 Hard 只有兩條鏈、各 28 節，節間
    跨度到 6 個鍵道，一條都不單調。

    重串之後落單的（不足 `MIN_SLIDE_CHAIN` 節）**自製**滑音還原成一般
    音符——一節構不成滑音；來源本來就有的一律不動。
    """
    chains = _slide_chains([n for n in visible
                            if int(getattr(n, 'note_type', 0)) & FLAG_SLIDE])
    dropped = 0
    for chain in chains:
        if len(chain) >= MIN_SLIDE_CHAIN:
            continue
        for note in chain:
            if not getattr(note, '_made_slide', False):
                continue
            note.note_type = int(note.note_type) & ~FLAG_SLIDE
            note._made_slide = False
            note.param1 = -1
            note.param2 = -1
            dropped += 1
    _chain_slides(visible)
    return dropped


def _chain_units(notes: Sequence[Any]) -> Dict[int, List[Any]]:
    """`id(任一成員)` → 它所屬的整條滑音鏈（只含 2 顆以上的鏈）。"""
    out: Dict[int, List[Any]] = {}
    for chain in _slide_chains(notes):
        if len(chain) < 2:
            continue
        for note in chain:
            out[id(note)] = chain
    return out


def _protected_slides(notes: Sequence[Any]) -> Set[int]:
    """滑音鏈的全部成員。稀疏化要整條留、整條走，不能拆。

    滑音是**一個動作**——手指滑過去一次，中間幾顆不是分開按的。拆成兩三顆
    既毀掉那個手勢（鏈結指到不存在的音符），也沒有變簡單：滑音本來就是低難度
    的語彙（官方 normal 2.0%、hard 2.4%、extreme 3.7%，real 幾乎是 0）。

    實測全庫 48 條來源滑音鏈有 **45 條被拆散**，最糟的是 Melodiniq 的 15 顆鏈
    在 Normal 只剩 2 顆。
    """
    keep: Set[int] = set()
    for chain in _slide_chains(notes):
        if len(chain) >= 2:
            keep.update(id(n) for n in chain)
    return keep


#: `note_type` 的低兩位是列舉：0 tap、1 soft、**2 hold**、3 staccato。
#: 用 `& 0x02` 判長押會把 staccato(3) 也算進去——3 也有那個位元。0x04 是滑音、
#: 0x40 是顫音，那兩個才是真的旗標。
NOTE_KIND_MASK = 0x03
KIND_TAP, KIND_SOFT, KIND_HOLD, KIND_STACCATO = 0, 1, 2, 3
FLAG_SLIDE, FLAG_TRILL = 0x04, 0x40


def _kind(note: Any) -> int:
    return int(getattr(note, 'note_type', 0)) & NOTE_KIND_MASK


def _is_long(note: Any) -> bool:
    return _kind(note) == KIND_HOLD and int(note.end) > int(note.start)


def _cap_hand_load(kept: Sequence[Any], max_load: int,
                   protected: Optional[Set[int]] = None) -> List[Any]:
    """一隻手同時要應付的事情不得超過 `max_load`，**按住中的長押也算**。

    `_cap_chords` 數的是「這一刻新發音的顆數」，但長押按下去之後那隻手就一直
    被佔著，而它不再是任何一組的成員，所以數不到。實測 Expert 有 14 個時刻是
    「2 個新音 + 2 個按住中 = 4 件事、橫跨 12 格」——一隻手張不開。

    超載時從內聲部開始拿掉（`_outer_first` 的順序）。長押已經佔滿這隻手時
    就整組拿掉——那一刻那隻手本來就沒有餘裕，另一隻手還是會有東西可打。
    """
    holds = sorted((n for n in kept if _is_long(n)), key=lambda n: int(n.start))
    by_hand_holds: Dict[int, List[Any]] = {}
    for hold in holds:
        by_hand_holds.setdefault(int(getattr(hold, 'hand', 0)), []).append(hold)

    groups: Dict[Tuple[int, int], List[Any]] = {}
    for note in kept:
        groups.setdefault((int(note.start), int(getattr(note, 'hand', 0))), []).append(note)

    dropped: Set[int] = set()
    for (when, hand) in sorted(groups):
        group = [n for n in groups[(when, hand)] if id(n) not in dropped]
        if not group:
            continue
        holding = [h for h in by_hand_holds.get(hand, ())
                   if id(h) not in dropped
                   and int(h.start) < when < int(h.end)]
        # 沒有地板：長押已經佔滿這隻手時，那一刻就該是空的——手正被兩根
        # 手指按著，再要求敲一下是做不到的。留「至少一顆」的地板會放行
        # 「2 個按住中 + 1 個新音 = 3 件事」，實測 Expert 還剩 9 處。
        allowed = max(0, int(max_load) - len(holding))
        if len(group) <= allowed:
            continue
        for note in _outer_first(group)[allowed:]:
            if id(note) in (protected or set()):
                continue                        # 滑音鏈整條留
            dropped.add(id(note))
    return [n for n in kept if id(n) not in dropped]


def _has_long(notes: Sequence[Any]) -> bool:
    return any(_kind(n) == KIND_HOLD for n in notes)


def _thin_by_bucket(kept: Sequence[Any], spacing_ms: float, burst: int,
                    weights: Optional[Dict[int, int]] = None,
                    protected: Optional[Set[int]] = None) -> List[Any]:
    """稀疏化：**厚的和弦先留**，剩下的空間再照間距分給單音。

    官方 2242 份譜實測，降難度時發音點的存活率完全由「那一刻有幾顆音」決定：

        同刻顆數      normal   hard   extreme
        1（單音）      46.9%   48.6%    75.4%
        2             80.3%   84.5%    95.3%
        3             88.1%   97.0%    99.8%
        4 以上         91.7%   98.3%   100.0%

    厚的和弦幾乎全留、單音砍掉一半以上。而「旋律有沒有動」幾乎沒有差別
    （65.1% vs 72.4%，還反向），「留下的是不是最高音」只有 48.8%——所以不是
    照冠音挑，是照**和弦厚度**挑。音樂上說得通：和弦是和聲與節奏的錨點，
    快速句裡的單音是經過音。

    兩段：

      1. **錨點**：照厚度由大到小走過所有發音點，離已接受的同手發音點夠遠
         （`spacing_ms`）就收下。厚度相同時照時間，結果才是決定性的。
      2. **快速句**：再照時間走一遍，每 `burst` 個錨點就把緊接著的那一顆也
         收下。官方三個難度的同手間隔 p10 都是 79ms、100ms 以下的都佔 24%
         ——快速句不隨難度消失，變的只是句與句之間的空隙。只做第 1 段的話
         每個間隔都會 >= spacing，整首變成等速的節拍器。

    `weights` 是 (起音時間 → 那一刻在**來源**有幾顆音)。用來源而不是用收頂
    之後的數量：收頂已經把厚和弦削成 2~3 顆，再拿它當厚度就分不出「本來是
    厚和弦」和「本來就是單音」。
    """
    if spacing_ms <= 0:
        return list(kept)
    every = max(1, int(burst))
    # 滑音鏈以**一整條**為單位參與稀疏化：選中它的起點就整條留下，沒選中就
    # 整條走。無條件保留是不行的——實測 Melodiniq 的 Normal 會變成 109 顆
    # 一般音符配 259 顆滑音，整份譜七成是滑音。
    chains = _chain_units(kept)
    heads: Dict[int, List[Any]] = {}
    for note in kept:
        chain = chains.get(id(note))
        if chain is not None and note is chain[0]:
            heads[int(note.start)] = chain
    by_hand: Dict[int, List[Any]] = {}
    for note in kept:
        chain = chains.get(id(note))
        if chain is not None and note is not chain[0]:
            continue                            # 只讓鏈頭代表整條
        by_hand.setdefault(int(getattr(note, 'hand', 0)), []).append(note)

    survivors: List[Any] = []
    for notes in by_hand.values():
        at_time: Dict[int, List[Any]] = {}
        for note in notes:
            at_time.setdefault(int(note.start), []).append(note)
        times = sorted(at_time)

        def thickness(when: int) -> int:
            # 滑音鏈最優先。它佔住整段時間（那隻手在滑，做不了別的），照厚度
            # 排的話鏈頭通常只是一顆單音、排到很後面，等輪到它時空間已經被
            # 短音佔滿——實測 Melodiniq 的 Normal/Hard 會把整條 15 顆的鏈丟掉。
            #
            # 但滑音是**便宜的**：滑過去一次而已，官方在每個難度都有
            # （normal 2.0%、hard 2.4%、extreme 3.7%，real 幾乎是 0），正是
            # 「保留節奏又不加難度」的手段。
            if when in heads:
                return 1000
            if weights is not None:
                return int(weights.get(when, len(at_time[when])))
            return len(at_time[when])

        # 1. 錨點：厚的先挑。滑音鏈佔的是**整條的時間**，不是一個點——
        #    只用鏈頭比距離的話，後面的音會壓在鏈的中段上。
        def reach(when: int) -> int:
            chain = heads.get(when)
            return int(chain[-1].start) if chain else when

        accepted: List[int] = []
        for when in sorted(times, key=lambda t: (-thickness(t), t)):
            near = False
            for other in accepted:
                if (when < reach(other) + spacing_ms
                        and other < reach(when) + spacing_ms):
                    near = True
                    break
            if not near:
                accepted.insert(bisect_left(accepted, when), when)

        # 2. 快速句：每 `every` 個錨點補一顆緊鄰的。
        #    `every <= 1` 是「完全不補」——`taken % 1` 永遠是 0，不擋掉的話
        #    每個錨點都會補一顆，等於整份加倍。
        chosen = set(accepted)
        taken = 0
        for when in (accepted if every > 1 else ()):
            taken += 1
            if taken % every:
                continue
            index = bisect_right(times, when)
            if index < len(times) and times[index] not in chosen:
                chosen.add(times[index])

        for when in sorted(chosen):
            for note in at_time[when]:
                chain = chains.get(id(note))
                if chain is not None and note is chain[0]:
                    survivors.extend(chain)     # 選中鏈頭 = 整條留
                else:
                    survivors.append(note)
    return survivors


def _source_thickness(groups: Sequence[Sequence[Any]]) -> Dict[int, int]:
    """起音時間 → 那一刻在**來源**有幾顆音（收頂之前）。"""
    out: Dict[int, int] = {}
    for group in groups:
        for note in group:
            when = int(note.start)
            out[when] = max(out.get(when, 0), len(group))
    return out


def _same_hand_gap(notes: Sequence[Any]) -> float:
    """同一隻手相鄰發音點間隔的中位數。

    照 `hand` 欄位分組。套了 hand=2 之後這個值會**變大**——未指定手自成一組，
    等於把原本同一隻手的連續音拆成兩串。官方表上的 403/312/198 就是這樣量的
    （他們的 normal 有 54% 是 hand=2），所以要和官方比就得在套完之後量；
    但決定「要砍到多稀」時得在套之前量，那才是真正一隻手要應付的速度。
    """
    by_hand: Dict[int, set] = {}
    for note in notes:
        by_hand.setdefault(int(getattr(note, 'hand', 0)), set()).add(int(note.start))
    gaps: List[float] = []
    for times in by_hand.values():
        ordered = sorted(times)
        gaps.extend(b - a for a, b in zip(ordered, ordered[1:]))
    return _median(gaps)


def _onsets_of(notes: Sequence[Any]) -> int:
    return len({int(n.start) for n in notes})


def _solve_bucket_rate(kept: Sequence[Any], goal: 'DifficultyTargets', burst: int,
                       weights: Optional[Dict[int, int]] = None,
                       protected: Optional[Set[int]] = None) -> float:
    """解出保留間距。

    兩個條件各解一次，取**較嚴的那個**（間距大的）：

      1. 發音點保留比例（主控）——照官方 64/78/91%。
      2. 同手間隔中位數（上限）——只有特別密的曲子會被它管到。

    兩者對間距都是單調的（隔得愈開留下的愈少、間隔愈大），所以二分找得到。
    """
    if not kept:
        return 0.0
    total = _onsets_of(kept)
    if not total:
        return 0.0

    def solve(hit) -> float:
        if hit(0.0):
            return 0.0                          # 來源本來就夠疏
        low, high = 1.0, 4000.0
        best = high
        for _ in range(30):
            mid = (low + high) / 2.0
            if hit(mid):
                best = mid
                high = mid
            else:
                low = mid
            if high - low < 0.5:
                break
        return best

    def by_ratio(spacing: float) -> bool:
        got = _onsets_of(_thin_by_bucket(kept, spacing, burst, weights, protected))
        return got <= total * float(goal.onset_ratio)

    def by_gap(spacing: float) -> bool:
        gap = _same_hand_gap(_thin_by_bucket(kept, spacing, burst, weights, protected))
        if gap <= 0.0:
            # 量不出間隔 —— 每隻手最多只剩一個發音點了，不可能再更疏。
            #
            # 以前這裡把 0 讀成「間隔 0 毫秒 = 無限快」，於是二分一路往更疏的方
            # 向走到 4000ms 的上限，整首只留一顆音符。**曲子越短越容易中**：實測
            # 1.9 秒的譜，normal 和 hard 都只剩 20 顆裡的 1 顆，而 extreme 留 15
            # 顆 —— 難度階梯整個反過來。4 秒以上的譜就正常，所以一直沒被發現。
            #
            # 中位數為 0 只可能是「沒有間隔可量」：同一隻手的發音點是取過集合
            # 的，兩個相異的時間至少差 1 毫秒。
            return True
        return gap >= goal.same_hand_gap_ms

    return max(solve(by_ratio), solve(by_gap))


def _attach_hosts(hidden: Sequence[Any], visible: Sequence[Any]) -> int:
    """替每顆隱藏音符指定寄主，回傳找不到寄主的顆數。

    直接寫進 `_sub_host`，不靠存檔時那個 120ms 的就近搜尋——降到 normal
    之後隱藏音符離最近的可見音符常常遠超過 120ms，靠猜的話會被判成孤兒、
    然後被迫變回可見（`_merge_hidden_into_hosts` 的保護），整個變難度就白做了。
    官方的 sub_note 本來就有自己的 start/end，離寄主很遠是常態。

    挑法：同一隻手優先，再取時間最近、音高最近的那顆。
    """
    if not visible:
        return len(hidden)
    # trill 不能當寄主。遊戲端看到 trill 會走 RefreshTrill，把寄主的 subNotes
    # 當成**顫音的每一擊**連發——折進去的隱藏音符就變成顫音的一部分被機關槍
    # 掃出來。實測全庫有 7 處（feng-yanno135miao 的 Hard、V 的三個難度）。
    ordered = sorted((n for n in visible
                      if not (int(getattr(n, 'note_type', 0)) & 0x40)),
                     key=lambda n: int(n.start))
    if not ordered:
        ordered = sorted(visible, key=lambda n: int(n.start))
    starts = [int(n.start) for n in ordered]
    from bisect import bisect_left

    orphans = 0
    for note in hidden:
        when = int(note.start)
        hand = int(getattr(note, 'hand', 0))
        index = bisect_left(starts, when)
        # 前後各看幾顆就夠——再遠的在時間上一定輸
        window = ordered[max(0, index - 6):index + 6] or ordered
        host = min(window, key=lambda v: (
            0 if int(getattr(v, 'hand', 0)) == hand else 1,
            abs(int(v.start) - when),
            abs(_pitch(v) - _pitch(note)),
        ))
        if host is None:
            orphans += 1
            continue
        note._sub_host = host
    return orphans


# ----------------------------------------------------------------------
# 難度尾巴：長押比例與 hand=2
# ----------------------------------------------------------------------
def _tap_duration(visible: Sequence[Any]) -> int:
    """這份譜面「一般音符」的典型長度。

    轉成一般音符的長押要收到這個長度，**不能歸零**：`end` 同時是 keysound
    的發聲長度（sub_note 的 end_timing_msec 就是從它來的），設成 0 等於把那
    顆音的聲音也拿掉。實測鬼火的一般音符中位是 83ms、最短 71ms，一顆都不是 0。
    """
    taps = [int(n.end) - int(n.start) for n in visible
            if _kind(n) != KIND_HOLD
            and int(n.end) > int(n.start)]
    return int(_median(taps)) if taps else 100


def _to_tap(note: Any, duration: int) -> None:
    """降成**乾淨的**一般音符，只保留滑音／顫音旗標。

    以前是 `note_type & ~0x02`，但低兩位是列舉不是位元組合：staccato(3) 清掉
    0x02 之後變成 1，也就是 **soft**。實測 Melodiniq 的來源有 3 顆 staccato、
    0 顆 soft，生出來卻多了 soft——那些 soft 是生成器自己造的。
    """
    flags = int(note.note_type) & (FLAG_SLIDE | FLAG_TRILL)
    note.note_type = flags
    length = min(int(note.end) - int(note.start), max(1, int(duration)))
    note.end = int(note.start) + max(1, length)
    note.gate = int(note.end) - int(note.start)


def _apply_long_ratio(visible: Sequence[Any], ratio: float,
                      min_long_ms: int = 0) -> int:
    """把長押收到目標比例，並砍掉太短的長押。

    兩道各自獨立的篩子：

    * **太短的**一律轉掉（`min_long_ms`）。短長押要求「按下去、幾乎立刻
      放開」，在慢速譜上比同樣長度的點擊更難，而且視覺上看不出是長押。
    * **超過比例的**從最短的開始轉，直到降到 `ratio`。長的那些留著——它們
      才是音樂上真的要按住的音。
    """
    changed = 0
    duration = _tap_duration(visible)
    if min_long_ms > 0:
        for note in visible:
            if (_kind(note) == KIND_HOLD
                    and int(note.end) - int(note.start) < int(min_long_ms)):
                _to_tap(note, duration)
                changed += 1
    longs = [n for n in visible if _kind(n) == KIND_HOLD]
    allowed = int(len(visible) * ratio)
    if len(longs) <= allowed:
        return changed
    longs.sort(key=lambda n: int(n.end) - int(n.start))
    for note in longs[:len(longs) - allowed]:
        _to_tap(note, duration)
        changed += 1
    return changed


def _cap_hand_span(visible: Sequence[Any], max_span: int,
                   total_lanes: int = 28) -> int:
    """同一隻手、同一時刻的音符不得跨超過 `max_span` 條鍵道。

    官方 extreme 的同手同時跨度中位是 6、p99 是 10，超過 9 格的只佔 1.2%
    ——一隻手張不開那麼大。排譜器是照音程算間隙的（`_chord_pair_gap` 大音程
    會留到 2 格空），三顆寬 3 的音加上兩個 2 格空就是 13 格。

    做法是往中心壓：保持音高順序和寬度不變，把每顆往組中心移，直到整組塞進
    `max_span`。壓到貼合還是不夠寬時就接受重疊——官方在塞不下時本來就是讓
    鍵道重疊，而不是把音符收窄。
    """
    # 分組要和排譜器一致：它是用 `onset_tolerance_ms`（35ms，和這裡的
    # `ONSET_TOLERANCE_MS` 同值）認和弦的。這裡以前用**完全相同的 start**
    # 分組，於是排譜器攤開成一個和弦、這裡卻看成好幾顆各自獨立的音，跨度
    # 違規就永遠檢查不到。實測 Unwelcome School：來源有一條 76->52 的半音
    # 下行（每 7ms 一顆），排譜器每 6 顆當成一個和弦攤成 23/20/15/10/5/0，
    # 跨度 28（整個鍵盤），而這一步一顆都沒動——那 4 顆音高差 1 個半音、
    # 鍵道卻差 23 條，完全讀不出旋律往哪走。
    by_hand: Dict[int, List[Any]] = {}
    for note in visible:
        by_hand.setdefault(int(getattr(note, 'hand', 0)), []).append(note)
    groups: Dict[Tuple[int, int], List[Any]] = {}
    for hand, members in by_hand.items():
        for group in _onset_groups(members):
            groups[(int(group[0].start), hand)] = group
    fixed = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        low = min(int(n.min_key) for n in members)
        high = max(int(n.max_key) for n in members)
        span = high - low + 1
        if span <= max_span:
            continue
        members.sort(key=lambda n: (int(n.min_key), _pitch(n)))
        widths = [int(n.max_key) - int(n.min_key) + 1 for n in members]
        packed = sum(widths)
        # 貼合就是物理下限：兩顆寬 5 的音相鄰就佔 10 格，Normal 要求 9 格是
        # 做不到的。所以目標取「要求」和「貼合」的較大者，壓到貼合為止。
        target = max(int(max_span), packed)
        if span <= target:
            continue
        slack = target - packed
        gaps = len(members) - 1
        base, extra = (slack // gaps, slack % gaps) if gaps else (0, 0)
        start = int(round((low + high) / 2.0 - (target - 1) / 2.0))
        start = max(0, min(total_lanes - target, start))
        cursor = start
        for i, note in enumerate(members):
            note.min_key = cursor
            note.max_key = cursor + widths[i] - 1
            cursor += widths[i]
            if i < gaps:
                cursor += base + (1 if i < extra else 0)
        fixed += 1
    return fixed


def _separate_hands(visible: Sequence[Any], total_lanes: int = 28,
                    clearance: int = 1) -> int:
    """同時發聲時，右手（hand=0）整組要在左手上方，中間至少留 `clearance` 條。

    官方 extreme 實測（1670 個兩手同時發聲的時刻）：「右手最低 − 左手最高」
    中位是 6、**最小是 1**，重疊或左右顛倒的比例是 **0.0%**——一次都沒有。

    `_cap_hand_span` 是一隻手一隻手往自己的中心壓的，兩手各壓各的就可能壓進
    對方的區域：實測 Expert 有 4 處（0.6%），例如左手佔 14-16、右手佔 12-20，
    左手整個被右手夾在中間。

    做法是把兩組各自剛體平移、平分需要的位移；一邊頂到鍵盤邊界時全部由另一邊
    吸收。組內的相對位置完全不動，所以跨度壓縮的成果不會被破壞。
    """
    groups: Dict[int, Dict[int, List[Any]]] = {}
    for note in visible:
        hand = int(getattr(note, 'hand', 0))
        groups.setdefault(int(note.start), {}).setdefault(hand, []).append(note)

    fixed = 0
    for hands in groups.values():
        right = hands.get(0)
        left = hands.get(1)
        if not right or not left:
            continue
        right_low = min(int(n.min_key) for n in right)
        left_high = max(int(n.max_key) for n in left)
        need = (left_high + clearance + 1) - right_low
        if need <= 0:
            continue

        up = need // 2 + need % 2
        down = need - up
        # 頂到邊界的那一邊讓另一邊全吸收
        room_up = (total_lanes - 1) - max(int(n.max_key) for n in right)
        room_down = min(int(n.min_key) for n in left)
        if up > room_up:
            down += up - room_up
            up = room_up
        if down > room_down:
            up = min(room_up, up + (down - room_down))
            down = room_down
        if up <= 0 and down <= 0:
            continue
        for note in right:
            note.min_key = int(note.min_key) + up
            note.max_key = int(note.max_key) + up
        for note in left:
            note.min_key = int(note.min_key) - down
            note.max_key = int(note.max_key) - down
        fixed += 1
    return fixed


def _lane_overlap(a: Any, b: Any) -> bool:
    return (int(a.min_key) <= int(b.max_key)
            and int(a.max_key) >= int(b.min_key))


def _time_overlap(a: Any, b: Any) -> bool:
    a_end = max(int(a.end), int(a.start) + 1)
    b_end = max(int(b.end), int(b.start) + 1)
    return int(a.start) < b_end and int(b.start) < a_end


#: 讓開長押走廊時，一顆音最遠可以搬多少條鍵道。
MAX_CORRIDOR_SHIFT = 4

#: 讓開**顫音**走廊時的上限。顫音沒有「拆掉」這條退路，只好讓得遠一點。
MAX_TRILL_CORRIDOR_SHIFT = 8


def _resolve_hold_corridors(visible: Sequence[Any], total_lanes: int = 28,
                            tolerance_ms: int = 35,
                            max_span: int = 0, clearance: int = 1) -> Tuple[int, int]:
    """長押按住的期間，它那幾條鍵道不准再出現別的音符。

    長押是「按著不放」，畫面上是一根貫穿的長條。另一顆音壓在同幾條鍵道上時，
    玩家看到的是兩個東西疊在一起，分不出要不要放開——實測 Expert 有 3 處是
    這樣（長押 16-18、音符 18-20，共用第 18 道）。

    兩段處理，順序有意義：

      1. **先讓音符**。找一個同寬度、當下沒有別人佔用的位置，取離原位最近的
         那個。這樣長押保得住，而且鍵道只動了那一顆。
      2. **讓不開才拆長押**。降成一般音符，長度收到一般音符的典型值——長押
         的音樂意義比不上「看得懂要按什麼」。

    回傳 (移動的音符數, 被拆掉的長押數)。
    """
    # trill 也算走廊：遊戲端畫長條的條件是 `isTrillCached || type == "hold"`
    # ——顫音同樣是一根貫穿的長條。實測 feng-yanno135miao 的 Normal/Hard 有
    # 左手音符壓在右手 trill 的尾巴上（同樣鍵道、在顫音的整段期間內）。
    notes = sorted(visible, key=lambda n: int(n.start))
    holds = [n for n in notes
             if (_kind(n) == KIND_HOLD
                 or int(getattr(n, 'note_type', 0)) & FLAG_TRILL)
             and int(n.end) > int(n.start)]
    if not holds:
        return (0, 0)

    moved = broken = 0
    duration = _tap_duration(visible)
    for hold in holds:
        if not (_kind(hold) == KIND_HOLD
                or int(getattr(hold, 'note_type', 0)) & FLAG_TRILL):
            continue                            # 前一輪已經被拆掉了
        for note in notes:
            if note is hold or not _time_overlap(note, hold):
                continue
            if abs(int(note.start) - int(hold.start)) <= tolerance_ms:
                continue                        # 同時發聲，那是和弦不是走廊
            if not _lane_overlap(note, hold):
                continue
            trill = bool(int(getattr(hold, 'note_type', 0)) & FLAG_TRILL)
            # 顫音讓得比較遠。長押讓不開還有退路（降成一般音符），顫音沒有
            # ——它的每一擊都記在 subNotes 裡，降成一般音符等於把整段丟掉，
            # 所以讓不開就只能留著重疊。實測 feng-yanno135miao：上限 4 條時
            # 10 顆顫音有 7 顆的鍵道被別的音符壓住（顫音在畫面上是一根貫穿的
            # 長條，壓住就分不清該按哪個），放寬到 8 條之後只剩 1 顆。
            reach = MAX_TRILL_CORRIDOR_SHIFT if trill else MAX_CORRIDOR_SHIFT
            if _move_out_of_the_way(note, hold, notes, total_lanes,
                                    max_span, clearance, max_shift=reach):
                moved += 1
            elif trill:
                # 讓不開就讓它重疊，那比整段顫音消失好。
                continue
            else:
                _to_tap(hold, duration)
                broken += 1
                break
    return (moved, broken)


def _placement_checker(note: Any, notes: Sequence[Any],
                       max_span: int = 0, clearance: int = 1):
    """回傳一個「`note` 現在這個鍵道位置可不可以」的判斷函式。

    鄰居只算一次，之後可以反覆問——搬位置是一格一格試的，每試一次都重算
    鄰居就變成 O(鍵道數 x 音符數)。
    """
    when = int(note.start)
    hand = int(getattr(note, 'hand', 0))
    busy = [other for other in notes
            if other is not note and _time_overlap(other, note)]
    siblings = [other for other in notes
                if other is not note and int(other.start) == when
                and int(getattr(other, 'hand', 0)) == hand]
    opposite = [other for other in notes
                if int(other.start) == when
                and int(getattr(other, 'hand', 0)) != hand
                and int(getattr(other, 'hand', 0)) in (0, 1)]

    def acceptable() -> bool:
        width = int(note.max_key) - int(note.min_key) + 1
        if any(_lane_overlap(note, other) for other in busy):
            return False
        if max_span > 0 and siblings:
            low = min([int(note.min_key)] + [int(x.min_key) for x in siblings])
            high = max([int(note.max_key)] + [int(x.max_key) for x in siblings])
            allowed = max(int(max_span), width * (len(siblings) + 1))
            if high - low + 1 > allowed:
                return False
        if opposite and hand in (0, 1):
            other_low = min(int(x.min_key) for x in opposite)
            other_high = max(int(x.max_key) for x in opposite)
            mine_low = min([int(note.min_key)] + [int(x.min_key) for x in siblings])
            mine_high = max([int(note.max_key)] + [int(x.max_key) for x in siblings])
            if hand == 0 and mine_low - other_high <= clearance:
                return False
            if hand == 1 and other_low - mine_high <= clearance:
                return False
        return True

    return acceptable


def _straighten_slide_chains(visible: Sequence[Any], max_span: int = 0,
                             clearance: int = 1, total_lanes: int = 28) -> int:
    """一條滑音鏈的鍵道要照音高排好。

    排譜是一顆一顆決定鍵道的，不知道這幾顆是同一條滑音。實測 Melodiniq 的
    Expert：手寫版把嚴格下行的音高（81 -> 58）映成嚴格下行的鍵道
    （20 -> 10），生成版卻映成 22, 19, 20, 17, 19, 16, 18 ... 一路鋸齒。
    同一個「滑」的動作要玩家的手來回甩，而遊戲端的 `GetSlideContactRange`
    還會照鏈結把接觸範圍撐到那些亂跳的鄰節上，判定也跟著糊掉。

    先試**重排鏈內已經用掉的那幾條鍵道**：把整條的起始鍵道取出來排序，再照
    音高順序發回去。用掉的鍵道集合完全不變，所以疏密和左右手的位置都不會
    跑掉，只有順序改了。

    重排之後仍然跨得比 `max_span` 開、或還留著大於 `_MAX_CHAIN_STEP` 的步進
    時，改用**重新分配**：排譜器是用 35ms 容差認和弦的，一段 8ms 一顆的快速
    下行會被當成「五顆同時按」，於是五節攤成 23/18/13/8/3，跨度 25 條。但滑
    音的節是一節接一節、不是同時按，鍵道本來就可以重疊——手寫譜正是這樣寫
    的（25-27、24-26、23-25，每節只挪一格）。

    放不下去的時候**整條一起平移**去躲，不要整條放棄：一條 28 節的鏈幾乎注
    定會有某一節撞到別人，一撞就全退的話等於沒做——實測系ぎて 那條 28 節的
    滑音就是這樣被退回去，留下步進 20 的鋸齒。平移保住形狀（單調、小步進），
    只是換個位置擺。真的都放不下才還原。
    """
    chains = _slide_chains([n for n in visible
                            if int(getattr(n, 'note_type', 0)) & FLAG_SLIDE])
    straightened = 0
    for chain in chains:
        if len(chain) < 3:
            continue
        chain = sorted(chain, key=lambda n: int(n.start))
        pitches = [_pitch(n) for n in chain]
        if len(set(pitches)) < 2:
            continue                        # 全同音，沒有該有的順序
        steps = [b - a for a, b in zip(pitches, pitches[1:])]
        rising = sum(1 for x in steps if x > 0)
        falling = sum(1 for x in steps if x < 0)
        before = [(int(n.min_key), int(n.max_key)) for n in chain]
        widths = [stop - start + 1 for start, stop in before]
        widest = max(widths)
        allowed = max(int(max_span), widest) if max_span > 0 else 0
        if bool(rising) != bool(falling):
            # 音高單調：只重排既有鍵道，改動最小。
            lanes = sorted(start for start, _stop in before)
            if falling:
                lanes.reverse()
        else:
            lanes = [start for start, _stop in before]
        span = max(lanes) + widest - min(lanes)
        gaps = [abs(b - a) for a, b in zip(lanes, lanes[1:])]
        plans = [lanes]
        if ((allowed and span > allowed)
                or (gaps and max(gaps) > _MAX_CHAIN_STEP)):
            # 撐太開、或還留著大步進，就照音高比例重新分配。音高不單調的鏈
            # 也走這條——以前那種鏈是整條跳過的，於是系ぎて 的 Expert 留下
            # 24, 21, 18, 15, 12, 23, 20, 17 ... 這種鋸齒（排譜器把每 35ms
            # 五顆當成和弦攤開的痕跡），步進到 11。
            #
            # 兩種跨度都試，緊的優先：
            #
            # * 壓進 `max_span`——最好看，但 28 節擠進 9 條之後幾乎一定會有
            #   某一節撞到別人，而拉直是整條成立或整條還原的。
            # * 退而求其次，用鏈**原本**的跨度重排。重新分配的目的是修順序和
            #   步進，不是把鏈壓窄——滑音是手一路滑過去的，跨得開本來就沒關
            #   係，而且每一節都待在排譜器原本給的位置附近，撞到的機會小得多。
            plans = []
            if allowed and allowed < span:
                plans.append(_respace_chain(chain, before, allowed, widest))
            plans.append(_respace_chain(chain, before, span, widest))

        outside = [n for n in visible if id(n) not in {id(x) for x in chain}]
        checks = [_placement_checker(n, outside, max_span, clearance)
                  for n in chain]

        def place(target: List[int], shift: int) -> bool:
            """把整條擺到 `target` 再平移 `shift`。**只**回報有沒有超出鍵盤。

            合不合法要另外用 `failures()` 問。這兩件事以前混在同一個回傳值
            裡，於是「排得下但有幾節撞到」的擺法會被當成「排不下」跳過，
            挑不到「卡住最少」的那一個，後面的微調也就永遠不會執行。
            """
            if min(target) + shift < 0:
                return False
            if max(target) + shift + widest > total_lanes:
                return False
            for note, start, width in zip(chain, target, widths):
                note.min_key = start + shift
                note.max_key = start + shift + width - 1
            return True

        def failures() -> List[Any]:
            return [note for note, ok in zip(chain, checks) if not ok()]

        done = False
        best = None                         # (放不下的節數, plan, shift)
        for target in plans:
            if [start for start, _stop in before] == target:
                done = True                 # 本來就排好了
                break
            for shift in _nearby_shifts(_CHAIN_SHIFT_REACH):
                if not place(target, shift):
                    continue                # 超出鍵盤，這個平移不能用
                bad = failures()
                if not bad:
                    straightened += 1
                    done = True
                    break
                if best is None or len(bad) < best[0]:
                    best = (len(bad), target, shift)
            if done:
                break

        # 一節放不下就整條還原太浪費了：實測系ぎて 的 Expert 那條 28 節的鏈，
        # 壓進 9 條鍵道之後只有 3 節卡住，卻讓整條退回原本步進 11 的鋸齒。
        # 改成挑「卡住最少」的那個擺法，再把那幾節各自小幅度挪開——挪動範圍
        # 只有 `_CHAIN_NUDGE` 條，形狀還是原本那條線。
        if not done and best is not None:
            place(best[1], best[2])
            if all(_nudge_into_place(note, visible, chain, max_span, clearance,
                                    total_lanes)
                   for note in failures()):
                straightened += 1
                done = True
        if not done:
            for note, (low_key, high_key) in zip(chain, before):
                note.min_key, note.max_key = low_key, high_key
    return straightened


#: 同一條滑音鏈內，相鄰兩節的鍵道步進超過這個值就重新分配。手寫譜
#: （Melodiniq Master，259 節 53 條）量出來的步進最大就是 3，一節都沒超過。
_MAX_CHAIN_STEP = 3

#: 拉直撞到障礙時，整條最多平移幾條鍵道去躲。
_CHAIN_SHIFT_REACH = 8


#: 拉直之後，個別放不下的節最多再挪幾條鍵道。
_CHAIN_NUDGE = 3


def _nudge_into_place(note: Any, visible: Sequence[Any], chain: Sequence[Any],
                      max_span: int, clearance: int, total_lanes: int) -> bool:
    """把鏈上某一節就近挪開，挪不動回 False。同鏈的鄰節不算障礙。"""
    outside = [n for n in visible if id(n) not in {id(x) for x in chain}]
    acceptable = _placement_checker(note, outside, max_span, clearance)
    width = int(note.max_key) - int(note.min_key) + 1
    original = int(note.min_key)
    for offset in _nearby_shifts(_CHAIN_NUDGE):
        start = original + offset
        if start < 0 or start + width > total_lanes:
            continue
        note.min_key, note.max_key = start, start + width - 1
        if acceptable():
            return True
    note.min_key, note.max_key = original, original + width - 1
    return False


def _nearby_shifts(reach: int) -> List[int]:
    """0, -1, +1, -2, +2 ... 由近而遠，原地優先。"""
    out = [0]
    for step in range(1, int(reach) + 1):
        out.append(-step)
        out.append(step)
    return out


def _respace_chain(chain: Sequence[Any],
                   before: Sequence[Tuple[int, int]],
                   max_span: int, widest: int) -> List[int]:
    """把一條被撐開的滑音鏈重新排進 `max_span` 條鍵道裡。

    鍵道照**音高比例**分配，不是照先後順序：作者寫的滑音不一定單調（可以滑
    出去再滑回來），照順序發會把來回的形狀抹平成一條斜線。照音高發則是原本
    那條旋律線的等比例縮小，形狀留著。

    位置沿用原本的中心，整條的移動量才最小；節與節之間允許重疊，因為滑音是
    一節接一節按的，不是和弦。
    """
    allowed = max(int(max_span), int(widest))
    low = min(start for start, _stop in before)
    high = max(stop for _start, stop in before)
    centre = (low + high) / 2.0
    base = max(0, int(round(centre - allowed / 2.0)))
    reach = max(0, allowed - int(widest))
    pitches = [_pitch(note) for note in chain]
    lowest, highest = min(pitches), max(pitches)
    span = max(1, highest - lowest)
    return [base + int(round(reach * (pitch - lowest) / float(span)))
            for pitch in pitches]


def _move_out_of_the_way(note: Any, hold: Any, notes: Sequence[Any],
                         total_lanes: int, max_span: int = 0,
                         clearance: int = 1, max_shift: int = 0) -> bool:
    """把 `note` 搬到離原位最近、當下沒人佔用的同寬位置。搬不動回 False。

    `max_shift` 是最遠搬多少條鍵道（0 代表不限）。一定要限——鍵道位置就是
    玩家讀到的音高，搬太遠等於把這顆音從旋律線上摘下來丟到別的地方。實測
    Cocytus 的 Normal：這一步搬了 34 顆，中位 6 條、p90 16 條、最遠 20 條
    （鍵盤共 28 條），整份譜的「音高上行、鍵道跟著上行」從來源的 100% 掉到
    86%。搬不動就交給呼叫端的退路（拆長押，或讓它重疊）。

    候選位置還要通過另外兩關，否則會把前面兩道處理的成果毀掉：

    * **同手跨度**：這一刻同手那一組的最外緣距離不得超過 `max_span`。不檢查
      的話會被推到鍵盤的另一端——實測「系ぎて」的 Hard 出現跨度 28（整個
      鍵盤），就是這樣來的。
    * **兩手分離**：右手仍要整組在左手上方、中間留 `clearance` 條。

    這兩關以前沒有，因為走廊處理排在最後，而跨度壓縮和兩手分離都排在它前面。
    """
    width = int(note.max_key) - int(note.min_key) + 1
    acceptable = _placement_checker(note, notes, max_span, clearance)

    original = int(note.min_key)
    reach = min(int(max_shift), total_lanes) if max_shift > 0 else total_lanes
    for offset in range(1, reach + 1):
        for start in (original - offset, original + offset):
            if start < 0 or start + width > total_lanes:
                continue
            note.min_key, note.max_key = start, start + width - 1
            if acceptable():
                return True
    note.min_key, note.max_key = original, original + width - 1
    return False


def _apply_hand2(visible: Sequence[Any], ratio: float) -> int:
    """把「該時刻只有自己」的音符改成 hand=2（未指定手）。

    官方 normal 的 hand=2 有 78% 落在這種時刻，比例則是 normal 54.3%、
    hard 14.5%、extreme 2.9%。一定要排完譜才做——排譜是靠 hand 決定鍵道的。
    """
    if ratio <= 0:
        return 0
    at_time: Dict[int, List[Any]] = {}
    for note in visible:
        at_time.setdefault(int(note.start), []).append(note)
    alone = [group[0] for _when, group in sorted(at_time.items()) if len(group) == 1]
    wanted = int(len(visible) * ratio)
    changed = 0
    for note in alone[:wanted]:
        note.hand = 2
        changed += 1
    return changed


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def generate(model: Any, difficulty: str,
             style: Optional[str] = None,
             targets: Optional[DifficultyTargets] = None) -> DifficultyResult:
    """就地把 `model` 變成指定難度。呼叫端負責先複製／`push_history`。

    順序是有意義的（排譜器的通道會互相覆蓋，見 arranger-pass-ordering）：

      1. 先取消所有隱藏 —— 從完整的事件集開始，不然重複生成會愈疊愈少。
      2. 和弦收頂 → 節奏稀疏化，決定哪些看得見。
      3. 指定寄主、標記隱藏。
      4. **只拿可見音符重跑排譜器** —— 鍵道是每個難度各自重排的。
      5. 最後才套長押比例和 hand=2 —— 這兩個會干擾排譜的輸入。
    """
    key = str(difficulty or '').lower()
    goal = targets or TARGETS.get(key)
    if goal is None:
        raise ValueError('unknown difficulty: %r' % (difficulty,))

    notes = list(model.notes_tree)
    total = len(notes)
    if not notes:
        return DifficultyResult(goal.name, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0,
                                goal.same_hand_gap_ms, 0, 0.0, 0.0, 0, 0)

    # 1. 回到完整的事件集
    for note in notes:
        note.hidden = False
        if getattr(note, '_sub_host', None) is not None:
            note._sub_host = None
        # 壓縮會改寫 note_type（階梯變滑音、交替變顫音），重複生成時要先還原
        # 成來源的樣子，否則第二次會疊在第一次的結果上。
        note._made_slide = False
        if not hasattr(note, '_source_note_type'):
            note._source_note_type = int(getattr(note, 'note_type', 0))
            note._source_end = int(note.end)
            # hand 也要記：單軌的來源沒有音軌可信，排譜器會照音高把它拆成
            # 左右手。不還原的話第二次生成是從「被拆過的手」開始跑的。
            note._source_hand = int(getattr(note, 'hand', 0))
        # 無條件套用：第一次是原封不動，之後才是真的還原。分成兩條路的話第一次
        # 和後續走的流程不一樣，同一份譜連生兩次會得到不同的結果。
        note.note_type = int(note._source_note_type)
        note.end = int(note._source_end)
        note.gate = int(note.end) - int(note.start)
        note.hand = int(note._source_hand)

    # 2. 選音
    # 2a. 先把長階梯壓成滑音、快速交替壓成顫音。兩者都是「一個動作打完一串」
    #     ——節奏全部留住，難度卻不會上去。要排在收頂和稀疏化**之前**，那兩步
    #     才知道這些是便宜的、該優先留。
    # 只拿可見音符找顫音——和階梯壓縮一致。隱藏的是別人的 sub_note，
    # 拿它當顫音的頭等於把一顆看不見的音變成要打的東西。
    _compress_trills([n for n in notes if not getattr(n, 'hidden', False)],
                     goal.trill_ratio)
    _compress_staircases([n for n in notes if not getattr(n, 'hidden', False)],
                         goal.slide_ratio)
    notes = [n for n in model.notes_tree]

    groups = _onset_groups([n for n in notes if not getattr(n, 'hidden', False)])
    # 厚度要在收頂**之前**量：收頂已經把厚和弦削成 2~3 顆，再拿它當厚度就
    # 分不出「本來是厚和弦」和「本來就是單音」。
    thickness = _source_thickness(groups)
    # 滑音鏈在收頂**之前**認出來：收頂會把長鏈削掉一半，之後就看不出原本是
    # 一整條了。
    protected = _protected_slides(notes)
    kept = _cap_chords(groups, goal.max_simultaneous, protected)
    threshold = _solve_bucket_rate(kept, goal, goal.burst, thickness, protected)
    kept = _thin_by_bucket(kept, threshold, goal.burst, thickness, protected)
    # 快速音之前的和弦收成單音，要在稀疏化**之後**——稀疏化決定了哪些發音點
    # 留下來，也就決定了誰的後面才是快速句。
    kept = _thin_before_fast(kept, goal.fast_follow_ms, protected)
    # 長押佔手，要在稀疏化**之後**算——稀疏化決定了哪些長押留得下來。
    kept = _cap_hand_load(kept, goal.max_hand_load, protected)

    # 3. 隱藏其餘的
    alive = {id(n) for n in kept}
    hidden = [n for n in notes if id(n) not in alive]
    # 顫音的每一擊已經指定好寄主了（就是顫音本身），不要被就近搜尋覆蓋掉。
    pending = [n for n in hidden
               if getattr(n, '_sub_host', None) is None
               or id(getattr(n, '_sub_host', None)) not in alive]
    orphans = _attach_hosts(pending, kept)
    for note in hidden:
        note.hidden = True

    # 4. 只排可見音符
    _arrange(model, kept, goal, style)

    # 5. 難度尾巴
    _cap_hand_span(kept, goal.max_hand_span)
    # 兩手分離要在跨度壓縮**之後**：壓縮是一隻手一隻手往自己的中心擠的，
    # 正是它把兩手擠到同一片鍵道上的。
    _separate_hands(kept)
    # 長押走廊要在跨度壓縮**之後**解——壓縮會把音符往中心擠，正是它把音符
    # 推進長押的鍵道裡的。
    _resolve_hold_corridors(kept, max_span=goal.max_hand_span)
    _apply_long_ratio(kept, goal.long_ratio, goal.min_long_ms)
    # 一定要排在長押處理**之後**：`_to_tap` 會動到 note_type，先清乾淨沒用。
    _trim_generated_slides(kept, goal.slide_ratio)
    # 鍵道到這一步才定下來：先讓每一條滑音的鍵道照音高排好，再重串鏈結。
    _straighten_slide_chains(kept, max_span=goal.max_hand_span)
    _relink_slides(kept)
    # 隱藏音符也要清：它們在音高模式下看得見，而且會寫進檔案。型別對遊戲沒有
    # 影響（隱藏音符只貢獻 keysound），但留著 soft／staccato 只會讓人以為
    # 那些記號還在。
    _restore_soft(notes)
    # hand=2 會把同一隻手的連續音拆成兩串，量出來的間隔就不是手速了，
    # 所以先把真正的手速記下來。
    gap_hands = _same_hand_gap(kept)
    _apply_hand2(kept, goal.hand2_ratio)

    model.rebuild_display_cache()
    model.dirty = True
    return _measure(goal, notes, kept, hidden, orphans, int(threshold), gap_hands)


def _arrange(model: Any, visible: Sequence[Any],
             goal: DifficultyTargets, style: Optional[str]) -> None:
    from .smart_chart import (
        arrange_midi_notes, normalise_style, settings_for_style, STYLE_EATHER,
    )

    if style is None:
        try:
            from .settings import settings as _prefs
            style = _prefs.get('chart_style')
        except Exception:                       # noqa: BLE001
            style = STYLE_EATHER
    style = normalise_style(style)
    beat_ms = 60_000.0 / max(1.0, float(getattr(model, 'bpm', 120.0) or 120.0))
    # 寬度是難度常數（normal 5、hard 4、extreme 3 都是眾數，hard 佔 93%、
    # extreme 佔 99%）。擠不下時收窄一格，和排譜器原本的 dense_width 同義。
    config = settings_for_style(
        style,
        beat_ms=beat_ms,
        # **不要重判表情記號。** 排譜器的長押判定是相對於「該手自己的發音
        # 間距」的（hold_tail_threshold = max(短×2, 典型×1.5, 長)），那是為
        # 了 MIDI 匯入設計的。稀疏化之後間距變大、音符長度沒變，於是沒有一
        # 顆過得了門檻——實測鬼火降 normal 時長押從 273 顆被判成 8 顆。
        #
        # 而且這裡的來源是**已經排好的譜面**，長押是既有事實（從原始 MIDI
        # 的長度來的，可能還被人手改過），不是待推測的東西。稀疏化只會拿掉
        # 音符、不會讓長押變得更難按（下一顆同手音只會更遠）。
        classify_articulations=False,
        normal_width=goal.note_width,
        # 收窄一律走 dense_width：剛好 4 音、整組跨度、近音程對三條路徑都是
        # `min(width, dense_width)`，容量不足時再由 min_note_width 兜底。
        # 把這兩個都設成該難度的寬度，就等於「不准收窄」。
        dense_width=(goal.note_width if goal.uniform_width
                     else max(2, goal.note_width - 1)),
        min_note_width=(goal.note_width if goal.uniform_width else 2),
        # 同手同時最多 2~3 顆，寬度 3~5 —— 最寬的情形是 Normal 兩顆寬 5 共
        # 10 格，離 28 格的容量還遠，所以「不准收窄」不會撞到容量問題。
    )
    # 來源的左右手是既有事實，不要重猜。
    #
    # `_assign_hands` 只有在「兩條以上帶音符的音軌」時才把音軌當權威，否則
    # 改用音高平均去猜，而且 `_hands_came_from_tracks` 為假時還會多跑一次
    # `_reduce_hand_leaps`。這個曲庫的譜面存成 XML 之後**完全沒有 track**
    # （實測鬼火 3474 顆全是 None），所以每次排譜都會重猜——實測 Expert 有
    # 80 顆、Normal 有 32 顆被改到和作者相反的手。
    #
    # 把 track 暫時設成 hand，就是在告訴排譜器「這兩隻手是來源給的」，走
    # trust_two_track_hands 那條路。排完再還原，檔案不會多出 track 欄位。
    saved_tracks = [getattr(n, 'track', None) for n in visible]
    for note in visible:
        note.track = 1 if int(getattr(note, 'hand', 0)) else 0
    try:
        arrange_midi_notes(list(visible), config)
    finally:
        for note, track in zip(visible, saved_tracks):
            note.track = track
    model.chart_style = style


def _measure(goal: DifficultyTargets, notes: Sequence[Any],
             visible: Sequence[Any], hidden: Sequence[Any],
             orphans: int, threshold: int,
             gap_hands: float = 0.0) -> DifficultyResult:
    span_ms = max(1, max(int(n.end) for n in notes) - min(int(n.start) for n in notes))
    at_time: Dict[int, int] = {}
    for note in visible:
        at_time[int(note.start)] = at_time.get(int(note.start), 0) + 1
    longs = sum(1 for n in visible if _kind(n) == KIND_HOLD)
    hand2 = sum(1 for n in visible if int(getattr(n, 'hand', 0)) == 2)
    count = len(visible) or 1
    return DifficultyResult(
        difficulty=goal.name,
        total=len(notes),
        visible=len(visible),
        hidden=len(hidden),
        orphans=orphans,
        visible_ratio=len(visible) / float(len(notes) or 1),
        notes_per_sec=len(visible) / (span_ms / 1000.0),
        gap_ms=_same_hand_gap(visible),
        gap_hands_ms=gap_hands,
        gap_target_ms=goal.same_hand_gap_ms,
        max_simultaneous=max(at_time.values()) if at_time else 0,
        hand2_ratio=hand2 / float(count),
        long_ratio=longs / float(count),
        peak_notes=peak_notes_per_second(visible),
        gap_threshold_ms=int(threshold),
    )
