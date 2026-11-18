# -*- coding: utf-8 -*-
"""冠音與低音的旋律線：算給人看，不改譜面。

使用者的判準是「官方寫譜會遵守冠音的旋律線」：音高往上走，冠音的鍵道就要往右
走，而且同一個音高盡量釘在同一格。排譜器收尾時照這條規則拉
（`smart_chart._repair_melody_slope`），但**手排的時候看不見它**——排完也不知道
哪裡破了線。這支模組把同一條規則算出來，讓畫面標得出來、選得起來、數得出幾處。

定義一律照排譜器那一套，不另立第二套語意
----------------------------------------

* **同時起音**＝起音差 ≤ `ONSET_MS`（＝排譜器 `onset_tolerance_ms` 的 35ms）。
  切法和 `smart_chart._clusters` 一樣：和**這一組的第一顆**比，不是和前一顆比。
* **冠音**＝一組裡 (音高, 右緣) 最大的那顆；**低音**＝ (音高, −左緣) 最小的。
* **分手各自一條線**：右手的冠音是旋律，左手的冠音是伴奏的頂，兩條不混在一起。
  （這裡的 `hand` 只用來分線、不用來判對錯，即使它是 AI 猜錯的也只是把兩條線
  畫歪，不會冤枉任何一顆音符。）
* **前後相接**＝中間**空**不到 `WINDOW_MS`（600ms，＝ `melody_slope_window_ms`）。
  比的是前一組放開到下一組按下去，**不是兩個起音差多遠**——比起音的話一顆長押
  就會把線切斷，而實測那種假斷線佔了所有斷線的 58.7%（見 `chains`）。超過
  當樂句斷了，不比步伐。

方向看**右緣**（`max_key`）不是中心——右緣是這個專案裡的排序權威（見
`models` 的寬度調整與 `_repair_melody_slope` 的 strict 項），中心會被寬度變化
帶著跑。

三種「破線」
------------

* `KIND_DIRECTION`：音高動了、右緣沒跟著動（停在同一格或往反方向）。官方
  424 份 Real 的冠音 78.7% 守住方向，排譜器對這一項是重罰。
* `KIND_REPEAT`：同一個音高在 `REPEAT_MS`（4 秒）內被放到不同格。實測使用者
  手改譜時最一致的訊號就是這個（4 秒內同音同格 69.7% → 78.3%）。
* `KIND_SLOPE`：**走對邊了，但步伐差太多**——半音差兩個卻飛了六格，或是跳了
  一個八度卻只挪一格。方向只管「有沒有走對邊」，這一項管「走多遠」，也就是
  音高→鍵道那條線的局部斜率。實測使用者的 221 份譜、187057 步（只算走對邊
  的）：差距中位數 1 格、九成在 2 格內、95% 在 3 格內、99% 在 4 格內。所以
  門檻 `SLOPE_TOLERANCE` 取 **3 格**（2.4% 的步、平均一份譜 20 處）；標記的
  長度跟著差距長，差愈多愈顯眼——使用者要的是「差太多的飆起來」。

**低音線只畫不判。** 同一批官方譜裡低音有 38.6% 是反方向走的（先讓旋律，和聲
掛在下面），所以拿方向規則去檢查低音只會製造一堆假警報。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import TOTAL_GAME_KEYS

#: 同時起音的容許值（毫秒）。
ONSET_MS = 35

#: 中間**空**這麼久以內才算同一條線（毫秒）。比的是前一組放開到下一組按下去，
#: 不是兩個起音差多遠——理由見 `chains`。
WINDOW_MS = 600

#: 同一個音高隔這麼久以內，還該釘在同一格（毫秒）。
REPEAT_MS = 4000

#: 步伐和「這個音程該走幾格」差這麼多（格）以上才算破。見模組說明的實測分布。
SLOPE_TOLERANCE = 3.0

KIND_DIRECTION = 'direction'
KIND_REPEAT = 'repeat'
KIND_SLOPE = 'slope'

#: 三種破線的中文說法，報告與提示共用一份。
KIND_NAMES = {
    KIND_DIRECTION: '方向',
    KIND_REPEAT: '同音不同格',
    KIND_SLOPE: '步伐差太多',
}

#: `breaks` 預設全查。少查哪一種就自己指定 `kinds`。
ALL_KINDS = (KIND_DIRECTION, KIND_REPEAT, KIND_SLOPE)

HAND_NAMES = {0: '右手', 1: '左手', 2: '周邊'}


# ── 基本篩選 ──────────────────────────────────────────────────────────

def usable(note: Any) -> bool:
    """這顆音符算不算在旋律線裡。

    沒有音高的排除：整個模組都靠音高定義冠音。隱藏音符也排除——它不佔按鍵、
    畫面上是掛在寄主底下的半透明影子，把它算成冠音會指到一顆玩家按不到的音。
    """
    if getattr(note, 'pitch', None) is None:
        return False
    return not getattr(note, 'hidden', False)


def _pitch(note: Any) -> int:
    return int(getattr(note, 'pitch', 0) or 0)


def _start(note: Any) -> int:
    return int(getattr(note, 'start', 0) or 0)


def _end(note: Any) -> int:
    """這顆音符響到什麼時候（長押算到尾巴，而且不准比 `start` 早）。"""
    return max(_start(note), int(getattr(note, 'end', 0) or 0))


def _hand(note: Any) -> int:
    return int(getattr(note, 'hand', 0) or 0)


# ── 切成同時起音的組 ──────────────────────────────────────────────────

def clusters(notes: Iterable[Any], onset_ms: int = ONSET_MS) -> List[List[Any]]:
    """照起音時間切組。和 `smart_chart._clusters` 同一套規則。

    比的對象是**這一組的第一顆**（`anchor`），不是前一顆：和前一顆比的話，
    一串間隔 30ms 的音符會被滾成同一組，整段琶音變成一個和弦。
    """
    ordered = sorted((n for n in notes if usable(n)),
                     key=lambda n: (_start(n), _pitch(n), int(getattr(n, 'idx', 0))))
    out: List[List[Any]] = []
    current: List[Any] = []
    anchor = 0
    for note in ordered:
        when = _start(note)
        if not current:
            anchor = when
            current = [note]
        elif when - anchor <= int(onset_ms):
            current.append(note)
        else:
            out.append(current)
            anchor = when
            current = [note]
    if current:
        out.append(current)
    return out


@dataclass
class Event:
    """一隻手在某一刻按下去的那一疊。"""
    start: int
    hand: int
    members: List[Any] = field(default_factory=list)
    top: Any = None          # 冠音
    bottom: Any = None       # 低音


def events(notes: Iterable[Any], by_hand: bool = True,
           onset_ms: int = ONSET_MS) -> Dict[int, List[Event]]:
    """算出每隻手的事件序列（照時間排好）。

    `by_hand=False` 時整份譜只有一條線（key 一律是 0）——分手欄位本身就可能是
    錯的，想看「整體最高音」的時候用這個。
    """
    out: Dict[int, List[Event]] = {}
    for group in clusters(notes, onset_ms):
        by: Dict[int, List[Any]] = {}
        for note in group:
            by.setdefault(_hand(note) if by_hand else 0, []).append(note)
        for hand, members in by.items():
            top = max(members, key=lambda n: (_pitch(n), int(n.max_key)))
            # 同音高同時按下去（重複音符造成的病態情況）取左緣近的那顆，和冠音
            # 取右緣遠的那顆對稱。排譜器那邊這個平手是用 `-min_key` 破的（＝取
            # 右邊那顆），不對稱；它只是個決定性的平手規則、從來沒有意義，而這
            # 裡是畫給人看的，對稱比較好解釋。
            bottom = min(members, key=lambda n: (_pitch(n), int(n.min_key)))
            out.setdefault(hand, []).append(
                Event(min(_start(n) for n in members), hand,
                      list(members), top, bottom))
    for series in out.values():
        series.sort(key=lambda e: e.start)
    return out


def tops(notes: Iterable[Any], by_hand: bool = True,
         onset_ms: int = ONSET_MS) -> List[Any]:
    """所有的冠音（照時間排好）。"""
    found = [e.top for series in events(notes, by_hand, onset_ms).values()
             for e in series]
    return sorted(found, key=lambda n: (_start(n), _pitch(n)))


def bottoms(notes: Iterable[Any], by_hand: bool = True,
            onset_ms: int = ONSET_MS) -> List[Any]:
    """所有的低音（照時間排好）。"""
    found = [e.bottom for series in events(notes, by_hand, onset_ms).values()
             for e in series]
    return sorted(found, key=lambda n: (_start(n), _pitch(n)))


def chains(series: Sequence[Event],
           window_ms: int = WINDOW_MS) -> List[List[Event]]:
    """把一隻手的事件切成一條一條相接的線。畫線的時候一條畫一筆。

    「相接」比的是**中間空了多久**（前一組放開 → 下一組按下去），不是兩個
    起音差多遠。比起音的話，一顆長押就會把它和下一顆切成兩條線，哪怕中間
    一點縫都沒有——使用者：「沒有連續的音符也要連，不能前後只是隔一點點就
    忽略旋律線」。

    實測 60 份譜、61288 步：照起音切會斷 9.5%，其中 **58.7% 是假的**（空拍
    其實在門檻以內）。改成照空拍切之後斷線剩 3.9%，而且多出來的 2809 步
    **連規則也跟著管得到**（守方向 97.8% / 48076 步 → 97.3% / 50885 步）
    ——慢的段落以前整段沒被檢查過，兩顆二分音符的起音就差超過 600ms 了。

    「這一組結束」取**組裡最晚放開的那一顆**，不是冠音自己：同一份切法要同時
    給低音線和規則用，手還壓著鍵就不該算斷。（兩種取法實測只差 8 處。）
    """
    out: List[List[Event]] = []
    run: List[Event] = []
    for event in series:
        if run and (event.start - max(_end(n) for n in run[-1].members)
                    > int(window_ms)):
            out.append(run)
            run = []
        run.append(event)
    if run:
        out.append(run)
    return [c for c in out if c]


# ── 破線 ──────────────────────────────────────────────────────────────

@dataclass
class Break:
    """一處不守規則的地方。指的永遠是**後面那顆**——要動的是它。"""
    kind: str
    hand: int
    first: Any               # 前一顆冠音
    second: Any              # 後一顆冠音
    pitch_delta: int = 0     # 音高差（second − first）
    edge_delta: int = 0      # 右緣差（second − first）
    excess: float = 0.0      # 只有 KIND_SLOPE 用：實走 − 該走（正＝走太遠）

    @property
    def start(self) -> int:
        return _start(self.second)

    @property
    def suggest(self) -> int:
        """`second` 的右緣該往哪邊移幾格，才不算破線（正數＝往右）。

        方向破線只要求**走對邊、至少一格**，不是要求走多遠——「大音程少走一點
        可以接受」是使用者自己講的，步伐要走多少是排譜器那條目標曲線的事。
        """
        if self.kind == KIND_REPEAT:
            return -self.edge_delta
        if self.kind == KIND_SLOPE:
            # 走太遠（excess > 0）就往回縮，走太少就再往同一邊走。
            way = 1 if self.pitch_delta > 0 else -1
            return int(round(-self.excess)) * way
        want = 1 if self.pitch_delta > 0 else -1
        return want - self.edge_delta if self.edge_delta * want <= 0 else 0

    @property
    def label(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)


def _direction_break(first: Any, second: Any, hand: int) -> Optional[Break]:
    pitch_delta = _pitch(second) - _pitch(first)
    if pitch_delta == 0:
        return None
    edge_delta = int(second.max_key) - int(first.max_key)
    if edge_delta != 0 and edge_delta * pitch_delta > 0:
        return None
    return Break(KIND_DIRECTION, hand, first, second, pitch_delta, edge_delta)


def _slope_break(first: Any, second: Any, hand: int,
                 curve: Optional[Dict[int, float]] = None,
                 tolerance: float = SLOPE_TOLERANCE,
                 slope: float = 0.0) -> Optional[Break]:
    """走對邊、但走的格數和這個音程該走的差太多。

    走錯邊或沒動的不在這裡報——那是 `KIND_DIRECTION` 的事，兩種刻意不重疊，
    一處只會被標一次。

    `step_for` 可能從學到的曲線拿到負數（譜如果整段是反著排的，中位數就會是
    負的），那時「該走幾格」沒有意義，夾到 0 當「至少別往回走」。
    """
    pitch_delta = _pitch(second) - _pitch(first)
    if pitch_delta == 0:
        return None
    edge_delta = int(second.max_key) - int(first.max_key)
    if edge_delta * pitch_delta <= 0:
        return None
    want = max(0.0, step_for(curve, pitch_delta, slope))
    excess = abs(edge_delta) - want
    if abs(excess) <= float(tolerance):
        return None
    return Break(KIND_SLOPE, hand, first, second, pitch_delta, edge_delta,
                 excess)


def breaks(notes: Iterable[Any], by_hand: bool = True,
           onset_ms: int = ONSET_MS, window_ms: int = WINDOW_MS,
           repeat_ms: int = REPEAT_MS,
           kinds: Sequence[str] = ALL_KINDS,
           curve: Optional[Dict[int, float]] = None,
           slope_tolerance: float = SLOPE_TOLERANCE,
           slope: float = 0.0) -> List[Break]:
    """找出冠音線上所有不守規則的地方，照時間排好。

    三種檢查看的距離不一樣，刻意的：方向和步伐只在同一條線（600ms）內比，
    因為樂句斷了之後的第一顆沒有「上一步」可言；同音不同格則看 4 秒，那是一
    個音高在聽覺上還認得是同一個位置的範圍。

    `curve` ＝「這個音程該走幾格」，給步伐那一項用。不給就用量好的
    `DEFAULT_STEPS`；要照這份譜自己的習慣來就傳 `learn_steps(整份譜)`。注意
    **別拿視窗裡那幾十顆去學**，樣本不夠的音程會整個退回預設表，標記會跟著
    捲動閃動。
    """
    wanted = set(kinds)
    found: List[Break] = []
    for hand, series in events(notes, by_hand, onset_ms).items():
        if wanted & {KIND_DIRECTION, KIND_SLOPE}:
            for chain in chains(series, window_ms):
                for first, second in zip(chain, chain[1:]):
                    if KIND_DIRECTION in wanted:
                        hit = _direction_break(first.top, second.top, hand)
                        if hit is not None:
                            found.append(hit)
                    if KIND_SLOPE in wanted:
                        hit = _slope_break(first.top, second.top, hand, curve,
                                           slope_tolerance, slope)
                        if hit is not None:
                            found.append(hit)
        if KIND_REPEAT in wanted:
            seen: Dict[int, Any] = {}
            for event in series:
                note = event.top
                before = seen.get(_pitch(note))
                if (before is not None
                        and _start(note) - _start(before) <= int(repeat_ms)
                        and int(note.max_key) != int(before.max_key)):
                    found.append(Break(
                        KIND_REPEAT, hand, before, note, 0,
                        int(note.max_key) - int(before.max_key)))
                seen[_pitch(note)] = note
    found.sort(key=lambda b: (b.start, b.kind))
    return found


def repair_direction(notes: Sequence[Any], movable: Any = None,
                     total_lanes: int = TOTAL_GAME_KEYS, max_shift: int = 4,
                     repeat_budget: int = 1, by_hand: bool = True,
                     onset_ms: int = ONSET_MS,
                     window_ms: int = WINDOW_MS) -> int:
    """把破了方向的冠音拉回去。回傳動到幾顆音符。

    為什麼需要：「照前面的小節排後面」重排出來的後半段，冠音守方向只有 ~80%
    （實測 12 份譜合計 767 處破線），而使用者自己的 60 份譜是 97.3%——重排
    是照「音高 → 鍵道中位數」擺的，那張表沒有「上一顆在哪裡」的概念。

    搬法和排譜器一致：**整組剛體平移**，和絃內部的幾何一格都不動。平移量先取
    剛好夠走對邊的那一格（`Break.suggest`），撞到別人才往同方向多讓，最多
    `max_shift` 格。三道不准：出界、和同一刻別的音符重疊、組裡有不准動的音符。

    `repeat_budget` ＝**最多可以拆掉幾處「同音同格」來換一處方向**。這兩條規則
    使用者都在守，而且會互相打架（排譜器當初也踩過，見 `_repair_melody_slope`
    的 `melody_slope_far_repeat_weight`）。12 份譜、後半段重排完之後實測：

        budget   守方向            同音同格
        （不修）  82.2%             80.3%
        0        87.0%             81.0%
        1        92.1%             76.6%     ← 預設
        2        95.1%             71.1%
        不限     95.8%             70.1%

    使用者自己的 60 份譜是 **守方向 97.3%、同音同格 82.6%**（這張表本身是在
    `chains` 改成照空拍切之前量的，相對順序不受影響）。1 是離那個側寫
    最近的一檔（兩項差距加起來 11.7pp，0 是 12.4、2 是 14.2）；而且方向是他
    自己講出口的規則（「官方寫譜會遵守冠音的旋律線」），同音同格只是個傾向。
    重疊在每一檔都是零增加。

    **會不會弄壞別的地方**：會，但很少。往前走的貪婪修法把 B 挪開之後，
    B→C 那一步有可能跟著破掉，而 C 不見得挪得動。同一批實測：原本 837 處，
    修好 440、新弄壞 38（佔修好的 8.6%），淨 −402。

    `movable` 是一個「可以動的音符 id 集合」（`id(note)`）。「照前面排後面」
    只把重排的那些交進來——照抄過去的鍵道來自使用者自己排好的前半段，不該被
    這道收尾改掉。`None` 代表整份都可以動。

    重疊只看**同一刻**那一組，和 `arrange_by_example.spread_group` 同一條線：
    長押走廊那種跨時間的佔用是排譜器才管得動的事，這裡不假裝做得到。
    """
    pool = list(notes)
    groups = clusters(pool, onset_ms)
    owner: Dict[int, List[Any]] = {}
    for group in groups:
        for note in group:
            owner[id(note)] = group

    def free(note: Any) -> bool:
        if int(getattr(note, 'note_type', 0) or 0) & 0x40:
            # 顫音的鍵道還連著它每一格子音符，要搬得整顆一起搬——和
            # arrange_by_example.arrangeable 同一條規則。
            return False
        return movable is None or id(note) in movable

    def legal(members: Sequence[Any], shift: int) -> bool:
        group = owner.get(id(members[0]), members)
        others = [n for n in group if id(n) not in {id(m) for m in members}]
        for note in members:
            lo, hi = int(note.min_key) + shift, int(note.max_key) + shift
            if lo < 0 or hi > int(total_lanes) - 1:
                return False
            for other in others:
                if lo <= int(other.max_key) and int(other.min_key) <= hi:
                    return False
        return True

    # 同音錨點：(手, 音高) → 照時間排好的出現序列。選平移量時用它避開
    # 「把剛好對齊的同音推開」——只挑第一個搬得動的格子，實測同音不同格會從
    # 643 處變成 1082 處，等於修好方向的同時弄壞另一條使用者更在意的規則。
    anchors: Dict[Tuple[int, int], List[Any]] = {}
    for note in sorted((n for n in pool if usable(n)), key=_start):
        anchors.setdefault(
            ((_hand(note) if by_hand else 0), _pitch(note)), []).append(note)

    def repeat_cost(members: Sequence[Any], shift: int) -> int:
        """這個平移量會多破壞幾處同音同格（負數＝反而修好了幾處）。"""
        inside = {id(n) for n in members}
        cost = 0
        for note in members:
            series = anchors.get(((_hand(note) if by_hand else 0),
                                  _pitch(note)), ())
            near = [n for n in series
                    if id(n) not in inside
                    and abs(_start(n) - _start(note)) <= REPEAT_MS]
            if not near:
                continue
            # 只看前後最近的各一顆：密集重複音的話全部都比是 O(n²)，而且
            # 更遠的那些本來就由它們自己那一步去管。
            before = [n for n in near if _start(n) <= _start(note)]
            after = [n for n in near if _start(n) > _start(note)]
            picks = ([before[-1]] if before else []) + ([after[0]] if after else [])
            for other in picks:
                was = int(note.max_key) == int(other.max_key)
                now = int(note.max_key) + shift == int(other.max_key)
                if was and not now:
                    cost += 1
                elif now and not was:
                    cost -= 1
        return cost

    moved = 0
    for hand, series in events(pool, by_hand, onset_ms).items():
        for chain in chains(series, window_ms):
            for first, second in zip(chain, chain[1:]):
                hit = _direction_break(first.top, second.top, hand)
                if hit is None:
                    continue
                members = second.members
                if not all(free(n) for n in members):
                    continue
                want = 1 if hit.pitch_delta > 0 else -1
                options = []
                shift = hit.suggest
                while abs(shift) <= int(max_shift):
                    if legal(members, shift):
                        options.append((repeat_cost(members, shift),
                                        abs(shift), shift))
                    shift += want
                if not options:
                    continue
                cost, _far, shift = min(options)
                if cost > int(repeat_budget):
                    # 怎麼挪都會拆掉同音同格，而且拆得比 `repeat_budget` 還多：
                    # 寧可留著這一處方向破線。兩條規則使用者都在守，沒有哪一條
                    # 該用另一條去換。
                    continue
                for note in members:
                    note.min_key = int(note.min_key) + shift
                    note.max_key = int(note.max_key) + shift
                moved += len(members)
    return moved


#: 1~14 個半音「右緣該走幾格」的退路表。取自 smart_chart 的 eather 風格
#: `melody_step_top`——那是從使用者 25 份手寫譜（約 4 萬組）量出來的，而且在
#: 瑠璃の鳥那份樣本外的譜上驗過。表上是**中心**位移，這裡當成右緣位移用：
#: 寬度大多是 2~3 格而且前後一致，兩者差一個常數，步伐是一樣的。
DEFAULT_STEPS = (0.84, 1.02, 1.38, 1.69, 2.13, 2.77, 3.19, 3.87,
                 4.20, 4.65, 4.96, 5.05, 5.62, 5.91)


def learn_steps(notes: Iterable[Any], by_hand: bool = True,
                onset_ms: int = ONSET_MS, window_ms: int = WINDOW_MS,
                min_samples: int = 2) -> Dict[int, float]:
    """從已經排好的音符量出「冠音差幾個半音 → 右緣走幾格」。

    取**中位數**不是平均：手排偶爾會為了閃開別的音符把一顆挪開，那是離群值。
    樣本少於 `min_samples` 的音程不收——只出現過一次的步伐沒有代表性，寧可
    退回 `DEFAULT_STEPS`。
    """
    seen: Dict[int, List[float]] = {}
    for hand, series in events(notes, by_hand, onset_ms).items():
        for chain in chains(series, window_ms):
            for first, second in zip(chain, chain[1:]):
                delta = _pitch(second.top) - _pitch(first.top)
                if delta == 0:
                    continue
                step = int(second.top.max_key) - int(first.top.max_key)
                seen.setdefault(abs(delta), []).append(
                    float(step if delta > 0 else -step))
    return {k: _median(v) for k, v in seen.items() if len(v) >= int(min_samples)}


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return (ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2.0)


def step_for(curve: Optional[Dict[int, float]], interval: int,
             slope: float = 0.0) -> float:
    """這個音程的冠音該走幾格（正數）。學到的優先，其次量好的表，最後才是斜率。"""
    size = abs(int(interval))
    if not size:
        return 0.0
    if curve and size in curve:
        return float(curve[size])
    if size <= len(DEFAULT_STEPS):
        return float(DEFAULT_STEPS[size - 1])
    # 超出表的範圍（14 個半音以上）：用表尾的斜率外插，不是直接截斷。
    tail = DEFAULT_STEPS[-1] / float(len(DEFAULT_STEPS))
    return float(DEFAULT_STEPS[-1] + (size - len(DEFAULT_STEPS)) * max(tail, slope))


def _keeps_direction(edge: int, previous_top: Any, delta: int) -> bool:
    """把冠音放在 `edge` 之後，和前一顆的方向還對不對。"""
    if not delta:
        return True
    step = int(edge) - int(previous_top.max_key)
    return step * (1 if delta > 0 else -1) > 0


def lead_by_top(notes: Sequence[Any], movable: Any = None,
                curve: Optional[Dict[int, float]] = None,
                anchor: Any = None, slope: float = 0.0,
                drift_slack: int = 1, max_adjust: int = 3,
                repeat_slack: int = 2,
                total_lanes: int = TOTAL_GAME_KEYS, by_hand: bool = True,
                onset_ms: int = ONSET_MS, window_ms: int = WINDOW_MS) -> int:
    """冠音帶頭重排：先決定冠音去哪，同一組其他的音符整組跟著走。

    使用者的說法是「冠音和之前比，其他的部分在守住旋律的前提下根據那些冠音的
    變化重排」。和 `repair_direction` 的差別是**主動**而不是補破洞：

    * `repair_direction` 只在方向反掉的時候動，動的量是「剛好走對邊」。
    * `lead_by_top` 每一組都算一次目標：上一個冠音的右緣 ＋ 這個音程該走幾格
      （`step_for`）。音高沒變就回到**同一格**，所以同音同格是順便守住的，
      不是另外補的。

    `anchor` 是「這個音高本來該放在哪個中心」（＝ `arrange_by_example` 學到的
    那張表）。相對鏈會累積誤差——一開始射偏、後面就偏到底（排譜器踩過同一個
    坑，見 `_snap_repeated_pitch_lanes` 的 drift 檢查）——所以目標要夾在絕對
    位置的 ±`drift_slack` 格內。

    `repeat_slack`：目標離「這個音高上次待的那一格」只差這麼多的時候，就吸過去。
    純照步伐曲線擺會把同音同格打散（82.0% → 68.1%），而那是使用者手改時最一致
    的訊號；吸附只讓步 1 格，換回大部分。

    擠不下的時候在目標附近找最近的合法位置（`max_adjust` 格內）。不准的事和
    `repair_direction` 一樣：出界、和同一刻別的音符重疊、組裡有不准動的音符
    （顫音永遠不准）。回傳動到幾顆。

    「樂句斷了」比的是**中間空了多久**，不是兩個起音差多遠（和 `chains` 同一
    條線）。比起音的話，長押後面那一組會因為「沒有上一顆」被整組跳過不重排。

    **實測**（2026-11-17 重量：20 份譜、後半段 10424 顆，拿使用者自己排好的
    那一半當答案；`chains` 已經是照空拍切的版本）：

        收尾        完全相同   ±1 格   守方向   同音同格   重疊
        不做        27.4%     64.6%   82.5%   83.8%     94
        冠音帶頭     28.7%     68.6%   90.5%   83.3%     88

    同一批、把「樂句斷了」改回比起音的話是 28.7 / 69.0 / 90.1 / 82.3 / 90
    ——守方向 +0.4、同音同格 +1.0、重疊 −2、±1 格 −0.4。**動得很小**，改它是
    因為「長押之後整組不重排」本身是個洞，不是因為分數。

    每一項都不輸，±1 格多了 5.8pp。調參的過程（12 份譜那一輪）：

        drift / pull   完全相同   ±1 格   守方向   同音同格
        1 / 0          27.3%     65.5%   91.2%   68.1%
        1 / 1          25.3%     66.2%   91.4%   78.5%
        1 / 2          25.6%     66.8%   91.4%   81.0%   ← 預設
        1 / 3          25.6%     66.7%   91.3%   81.1%
        2 / 1          25.3%     63.0%   93.5%   74.2%
        3 / 0          22.7%     59.7%   94.5%   64.8%

    `drift_slack` 放得愈鬆，守方向愈高但**愈不像使用者排的**：相對鏈一放開，
    「這個音高上次放在哪」那張表就沒了話語權，而那張表才是最準的那一個。
    `repeat_slack` 到 2 就飽和了。

    只補破洞的 `repair_direction` 同一批是 25.5% / 61.1% / 91.2% / 78.5%
    ——方向一樣好，但 ±1 格差了 5.7pp：它只在出事的地方動，不會把整句的位置
    對齊到旋律該有的樣子。
    """
    pool = list(notes)
    groups = clusters(pool, onset_ms)
    owner: Dict[int, List[Any]] = {}
    for group in groups:
        for note in group:
            owner[id(note)] = group

    def free(note: Any) -> bool:
        if int(getattr(note, 'note_type', 0) or 0) & 0x40:
            return False
        return movable is None or id(note) in movable

    def legal(members: Sequence[Any], shift: int) -> bool:
        group = owner.get(id(members[0]), members)
        inside = {id(m) for m in members}
        for note in members:
            lo, hi = int(note.min_key) + shift, int(note.max_key) + shift
            if lo < 0 or hi > int(total_lanes) - 1:
                return False
            for other in group:
                if id(other) in inside:
                    continue
                if lo <= int(other.max_key) and int(other.min_key) <= hi:
                    return False
        return True

    moved = 0
    for _hand, series in events(pool, by_hand, onset_ms).items():
        previous: Optional[Event] = None
        #: 這隻手每個音高上次待的 (時間, 右緣)。照時間走，所以參考段落會先把它
        #: 填好，後面重排的就吸得到前半段的位置。
        seen: Dict[int, Tuple[int, int]] = {}
        for event in series:
            if previous is not None and (
                    event.start - max(_end(n) for n in previous.members)
                    > window_ms):
                # 樂句斷了，下一顆重新起頭。和 `chains` 同一條線：比的是中間
                # **空**了多久，不是兩個起音差多遠——比起音的話，長押後面那一組
                # 會因為「沒有上一顆」被整組跳過不重排。
                previous = None
            top = event.top
            if previous is None or not all(free(n) for n in event.members):
                # 動不了的那一組**仍然是下一組的「上一個冠音」**：它就在譜上，
                # 下一步要和它比，不是跳過去和更早的比。
                previous = event
                seen[_pitch(top)] = (event.start, int(top.max_key))
                continue
            delta = _pitch(top) - _pitch(previous.top)
            target = float(previous.top.max_key)
            if delta:
                way = 1.0 if delta > 0 else -1.0
                target += way * step_for(curve, delta, slope)
            want = int(round(target))
            if anchor is not None:
                centre = anchor(top)
                if centre is not None:
                    # 絕對位置是用中心表示的，換算成右緣要加半個寬度。
                    half = (int(top.max_key) - int(top.min_key)) / 2.0
                    absolute = int(round(float(centre) + half))
                    want = max(absolute - int(drift_slack),
                               min(absolute + int(drift_slack), want))
            before = seen.get(_pitch(top))
            if (before is not None
                    and event.start - before[0] <= REPEAT_MS
                    and abs(before[1] - want) <= int(repeat_slack)
                    and _keeps_direction(before[1], previous.top, delta)):
                # 吸回同一格，但**不准因此走錯邊**：兩條規則打架時方向優先，
                # 那是使用者守到 97.3% 的那一條，同音同格只有 82.6%。
                want = before[1]
            shift = want - int(top.max_key)
            # 目標被佔住時往哪邊讓：**順著旋律走的那一邊先試**。往回讓的話
            # 很容易剛好停在前一顆的那一格，那就是一處方向破線。
            way = 1 if delta >= 0 else -1
            for extra in range(0, int(max_adjust) + 1):
                picks = ((shift,) if not extra
                         else (shift + way * extra, shift - way * extra))
                hit = next((s for s in picks if s and legal(event.members, s)), None)
                if hit is None and not extra and shift == 0:
                    break                       # 已經在目標上
                if hit is not None:
                    for note in event.members:
                        note.min_key = int(note.min_key) + hit
                        note.max_key = int(note.max_key) + hit
                    moved += len(event.members)
                    break
            previous = event
            seen[_pitch(top)] = (event.start, int(top.max_key))
    return moved


def summary(found: Sequence[Break]) -> Dict[str, int]:
    """每一種破線各幾處。報告用。"""
    out = {kind: 0 for kind in KIND_NAMES}
    for item in found:
        out[item.kind] = out.get(item.kind, 0) + 1
    return out


def rate(notes: Iterable[Any], by_hand: bool = True,
         onset_ms: int = ONSET_MS, window_ms: int = WINDOW_MS) -> Tuple[int, int]:
    """(守住方向的步數, 總共幾步)。只看方向，不看同音不同格。

    分母是「音高有動、而且前後相接」的步數——同音高的那些步沒有方向可言，
    算進分母只會讓一份同音很多的譜看起來分數比較高。
    """
    good = total = 0
    for hand, series in events(notes, by_hand, onset_ms).items():
        for chain in chains(series, window_ms):
            for first, second in zip(chain, chain[1:]):
                if _pitch(second.top) == _pitch(first.top):
                    continue
                total += 1
                if _direction_break(first.top, second.top, hand) is None:
                    good += 1
    return good, total
