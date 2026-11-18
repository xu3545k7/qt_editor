# -*- coding: utf-8 -*-
"""樂句級的旋律輔助：同一句又來了——移了調，或是中間插了裝飾音。

`melody_line` 看的是**相鄰兩顆**（方向、同音同格、步伐），這支看的是**一整句**。
使用者的說法是「冠音以外，加上做位移往高或低音但同旋律，以及中間插裝飾音感測」
——兩件事其實是同一件：這一句和前面那一句是**同一條旋律**，只是

* 整句搬去別的音高（`ECHO_SHIFT`，往高或低音位移），或者
* 中間多塞了幾顆（`ornaments`，裝飾音）。

所以這裡只有一套比對，兩種結果都從它掉出來。

怎麼認出「同一句」
------------------

比的是冠音線（`melody_line.events` 的 `top`，分手各自一條），因為旋律就在那裡。

1. **錨點**：連續 `ANCHOR_STEPS` 個音程（半音差）完全一樣、而且每一步的時間
   間隔也一樣。只回頭看最近 `RECENT_ANCHORS` 個同指紋的位置——同一個音型在
   一首裡可能出現幾十次，每一個都比一遍是 O(n²)，而使用者要看的是「剛剛那一句」。
2. **從錨點往前後走**：固定一個音高差（`offset`），一顆一顆對。對不上時容許
   跳過 `MAX_INSERT` 顆以內：跳回聲那邊的 ⇒ **裝飾音**（它多出來的），跳本體
   那邊的 ⇒ **省略**（它少了一顆）。再接不上就收尾。
3. 兩段**不准在時間上重疊**：週期性的音型（同一個音連按六下那種）會自己吃自己，
   本體和回聲變成同一段的兩半，看起來像找到了其實什麼也沒說。
**認不出來的時候**：錨點要「連續 3 個乾淨的音程」，所以一句短到被中間那顆
裝飾音切成兩半（兩邊各湊不滿 3 個）就找不到。實際的譜不太會這樣——句子通常
夠長，裝飾音後面還留著一段乾淨的——但造測試資料時要記得。

4. 配對的那些音程裡至少要有 `MIN_MOVES` 個不是 0：「A A A A A A」對上
   「D D D D D D」在數學上是移調重複，在音樂上什麼都不是——那是伴奏的固定音型，
   而且這個曲庫裡滿地都是。

實測（使用者的 60 份譜、61408 顆冠音）
--------------------------------------

    回聲             2236 處（移調 1033、原樣 1203）；每份中位 29、九成 87、最多 145
    插了裝飾音        239 處、457 顆（佔冠音 0.74%）
    少了音符          241 處

60 份裡沒有一份是零處——這是個到處都在的結構。整份譜算一次要 9.3ms（最慢的
3691 顆那份 79ms），所以**算整份再快取**，不是每幀算視窗：本體常常在視窗外面，
只看視窗的話標記會隨著捲動自己閃掉。

整句該平移幾格
--------------

「同旋律」不等於「同形狀」——實測移調的回聲裡，**句內的鍵道形狀完全一樣只有
24%**（句子愈長還愈低，5 個音程以上只有 17%）。所以這裡不標形狀，只標**整句的
位移**：

    整句平移的方向    對 88.9%、沒動 8.6%、反了 2.5%（n=1033）
    |實際 − 該平移|   ≤1 格 69.5%、≤2 格 87.9%、≤3 格 95.6%

門檻 `SHIFT_TOLERANCE` 取 3 格（和 `melody_line.SLOPE_TOLERANCE` 同一個理由：
95% 那一檔）。最後判破的是移調回聲裡的 **7.5%**（走錯邊 12 處 ＋ 差超過 3 格
71 處，每份 1.3 處）。

「該平移幾格」用 `melody_line.step_for`，相鄰一步的步伐表**直接拿來量一整句**
（`PHRASE_STEP_FACTOR = 1.0`）。第一版打了 0.8 的折，那是看兩個八度樣本看出來
的；量過全部 3439 句之後確定只有八度以上該打折，而那只佔三成——詳見
`PHRASE_STEP_FACTOR` 的註解。

為什麼值得標
------------

移了調的回聲是「照範本排後面」最容易排歪的地方：那張「這個音高上次放在哪」的
表對新的音高**一筆資料都沒有**（見 [[arrange-by-example]]），所以它只能靠迴歸
外插。標出來至少看得到「這一句和前面那一句是同一句」，右鍵也選得起來、抄得過去。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import melody_line as ML

#: 錨點：連續幾個音程完全一樣才算一個候選。2 個會讓回聲多一倍（每份 59 處），
#: 多出來的大半是四顆音的巧合；4 個只剩 19.6 處，真的重複會被漏掉。
ANCHOR_STEPS = 3

#: 同一個指紋只回頭比最近幾個位置。
RECENT_ANCHORS = 4

#: 回聲離本體最多多久（毫秒）。超過這個距離，使用者在畫面上也看不到本體了。
ECHO_GAP_MS = 12000

#: 一個縫裡最多容許多出幾顆（裝飾音）。
MAX_INSERT = 2

#: 配對的音程裡至少要有幾個不是 0（＝這一句真的有旋律，不是固定音型）。
MIN_MOVES = 2

#: 整句平移和「該平移」差幾格以上算破。見模組說明的實測分布。
SHIFT_TOLERANCE = 3.0

#: 整句該平移幾格 ＝ `step_for` × 這個係數。**1.0，也就是不打折**。
#:
#: 第一版取 0.8，理由是「一整句搬家比每一步的步伐加起來保守」——但那是從兩個
#: 八度的樣本看出來的。全部 3439 句移調回聲、拿他實際移的量當答案：
#:
#:     係數      ≤1 格   ≤2 格   ≤3 格   判破
#:     ×0.8     60.8%   85.0%   94.6%   6.2%
#:     ×1.0     69.3%   88.5%   95.4%   7.9%   ← 每一檔都比較準
#:     1.0/0.85 66.8%   87.4%   94.8%   8.4%   （八度以上才打折）
#:
#: 分音程看「實際平移 / 表上的格數」的中位數：1~4 個半音 1.00、5~11 個半音
#: 1.00、八度 0.86、八度以上 0.89。所以打折這件事**只有八度以上才成立**，
#: 而八度以上只佔三成——拿它的係數套到全部，等於把另外七成推歪。八度那一檔
#: 單看平均誤差是 0.8 略好（1.24 比 1.31），但用「差幾格」去數還是 1.0 贏。
PHRASE_STEP_FACTOR = 1.0

#: 相對時間要對得多準（毫秒）：樂句裡相鄰間隔中位數的 35%，夾在上下限之間。
#: 和 `pattern_transfer._time_tolerance` 同一套——那是右鍵「套用排列分布」在用的
#: 容許值，同一件事不該有兩個數字。
TOL_RATIO = 0.35
MIN_TOL = 40.0
MAX_TOL = 150.0

ECHO_SAME = 'same'
ECHO_SHIFT = 'shift'

ECHO_NAMES = {ECHO_SAME: '原樣重複', ECHO_SHIFT: '移調重複'}

#: `echoes` 預設兩種都找。
ALL_ECHOES = (ECHO_SAME, ECHO_SHIFT)


@dataclass
class Echo:
    """一處「同一句又來了」。`pairs` 的第二個欄位永遠是**後面那一句**。"""
    hand: int
    kind: str
    offset: int                              # 音高差（回聲 − 本體，半音）
    pairs: List[Tuple[Any, Any]] = field(default_factory=list)
    ornaments: List[Any] = field(default_factory=list)   # 回聲多出來的
    missing: List[Any] = field(default_factory=list)     # 回聲少掉的
    shift: float = 0.0                       # 實際整句平移幾格（右緣中位數）
    want: float = 0.0                        # 該平移幾格（帶正負）

    @property
    def source(self) -> List[Any]:
        return [a for a, _b in self.pairs]

    @property
    def repeat(self) -> List[Any]:
        return [b for _a, b in self.pairs]

    @property
    def notes(self) -> List[Any]:
        """回聲這一句的所有音符（含裝飾音），照時間排好。"""
        return sorted(self.repeat + list(self.ornaments),
                      key=lambda n: int(n.start))

    @property
    def length(self) -> int:
        return len(self.pairs)

    @property
    def start(self) -> int:
        return min(int(n.start) for n in self.notes)

    @property
    def end(self) -> int:
        return max(int(n.start) for n in self.notes)

    @property
    def source_start(self) -> int:
        return min(int(n.start) for n in self.source)

    @property
    def off_by(self) -> float:
        """實際平移 − 該平移（正＝往右移太多）。"""
        return self.shift - self.want

    @property
    def bad(self) -> bool:
        """整句移了調，位置卻沒跟著移（或移反了、移太多）。

        原樣重複不判：它的「該平移」是 0，而同一個音高該不該回到同一格是
        `melody_line.KIND_REPEAT` 的事，同一件事不標兩次。

        **「沒動」本身不算破**：移一兩個半音時該平移不到一格，整句留在原地
        是對的（實測移 −1 半音時使用者整句平移的中位數就是 0）。只有在該走
        至少一格、卻往反方向走的時候才算走錯邊。
        """
        if self.kind != ECHO_SHIFT:
            return False
        if abs(self.want) >= 1.0 and self.shift * self.offset < 0:
            return True
        return abs(self.off_by) > SHIFT_TOLERANCE

    @property
    def suggest(self) -> int:
        """整句還該往哪邊移幾格（正＝往右）。"""
        return int(round(self.want - self.shift))

    @property
    def label(self) -> str:
        if self.kind == ECHO_SHIFT:
            return '同句 %+d 半音' % int(self.offset)
        return '同句'


def _tolerance(times: Sequence[int]) -> float:
    gaps = [b - a for a, b in zip(times, times[1:]) if b - a > 5]
    if not gaps:
        return MAX_TOL
    return max(MIN_TOL, min(MAX_TOL, TOL_RATIO * ML._median(gaps)))


def _edge(note: Any) -> int:
    return int(note.max_key)


class _Line:
    """一隻手的冠音序列，加上比對要用的那幾張表。"""

    def __init__(self, tops: Sequence[Any]):
        self.tops = list(tops)
        self.pitch = [ML._pitch(t) for t in self.tops]
        self.time = [ML._start(t) for t in self.tops]
        self.tol = _tolerance(self.time)
        self.steps = [self.pitch[k + 1] - self.pitch[k]
                      for k in range(len(self.tops) - 1)]
        self.gaps = [self.time[k + 1] - self.time[k]
                     for k in range(len(self.tops) - 1)]

    def __len__(self) -> int:
        return len(self.tops)

    def fits(self, i: int, j: int, a: int, b: int, offset: int) -> bool:
        """本體第 a 顆和回聲第 b 顆對得上嗎（(i, j) 是錨點）。

        音高差要**剛好**等於整句的移調量——差一個半音就是另一個音，不是同一句。
        相對時間容許 `tol`：同一份譜是定速的，所以不用管快慢比例。
        """
        if self.pitch[b] - self.pitch[a] != offset:
            return False
        return abs((self.time[b] - self.time[j])
                   - (self.time[a] - self.time[i])) <= self.tol


def _walk(line: _Line, i: int, j: int, offset: int, stop: int,
          back: bool = False) -> Tuple[List[Tuple[int, int]],
                                       List[int], List[int]]:
    """從錨點 (i, j) 往後（或往前）對。回傳 (配對, 回聲多出來的, 回聲少掉的)。

    `stop`：往後走時本體不准走到這個 index（＝回聲的第一顆），往前走時回聲不准
    走到這個 index 以下（＝本體的最後一顆）。兩段在時間上重疊的話，「這一句又
    來了」就只是在講同一段的前半和後半。
    """
    way = -1 if back else 1
    pairs: List[Tuple[int, int]] = []
    inserts: List[int] = []
    skips: List[int] = []
    a, b = i + way, j + way
    n = len(line)
    while 0 <= a < n and 0 <= b < n and (b > stop if back else a < stop):
        if line.fits(i, j, a, b, offset):
            pairs.append((a, b))
            a += way
            b += way
            continue
        found: Optional[Tuple[str, int]] = None
        for skip in range(1, MAX_INSERT + 1):
            nb = b + way * skip
            if not 0 <= nb < n:
                break
            if line.fits(i, j, a, nb, offset):
                found = ('insert', skip)
                break
        if found is None:
            for skip in range(1, MAX_INSERT + 1):
                na = a + way * skip
                if not 0 <= na < n:
                    break
                if line.fits(i, j, na, b, offset):
                    found = ('skip', skip)
                    break
        if found is None:
            break
        kind, skip = found
        if kind == 'insert':
            inserts.extend(b + way * s for s in range(skip))
            b += way * skip
        else:
            skips.extend(a + way * s for s in range(skip))
            a += way * skip
        if not (0 <= a < n and 0 <= b < n and (b > stop if back else a < stop)):
            break
        pairs.append((a, b))
        a += way
        b += way
    return pairs, inserts, skips


def _moves(line: _Line, pairs: Sequence[Tuple[int, int]]) -> int:
    """配對的音程裡有幾個不是 0（看本體那一邊）。"""
    idx = sorted(a for a, _b in pairs)
    return sum(1 for x, y in zip(idx, idx[1:])
               if line.pitch[y] != line.pitch[x])


def _build(line: _Line, hand: int, i: int, j: int, offset: int,
           curve: Optional[Dict[int, float]], slope: float) -> Optional[Echo]:
    back = _walk(line, i, j, offset, i, back=True)
    # 回聲的第一顆決定本體往後最多走到哪：兩段不准疊在一起。
    first_b = min([b for _a, b in back[0]] + [j])
    fwd = _walk(line, i, j, offset, first_b)
    pairs = sorted(back[0] + [(i, j)] + fwd[0])
    if _moves(line, pairs) < MIN_MOVES:
        return None
    shifts = [_edge(line.tops[b]) - _edge(line.tops[a]) for a, b in pairs]
    want = (ML.step_for(curve, offset, slope) * PHRASE_STEP_FACTOR
            * (1.0 if offset > 0 else -1.0)) if offset else 0.0
    return Echo(hand, ECHO_SHIFT if offset else ECHO_SAME, offset,
                [(line.tops[a], line.tops[b]) for a, b in pairs],
                [line.tops[k] for k in sorted(back[1] + fwd[1])],
                [line.tops[k] for k in sorted(back[2] + fwd[2])],
                ML._median(shifts), want)


def _pick(found: Sequence[Echo]) -> List[Echo]:
    """同一段音符不要被兩處回聲蓋住：長的優先，照時間回傳。"""
    taken: set = set()
    keep: List[Echo] = []
    for echo in sorted(found, key=lambda e: (-e.length, e.start)):
        span = {id(n) for n in echo.notes}
        if span & taken:
            continue
        taken |= span
        keep.append(echo)
    keep.sort(key=lambda e: e.start)
    return keep


def echoes(notes: Iterable[Any], by_hand: bool = True,
           onset_ms: int = ML.ONSET_MS, gap_ms: int = ECHO_GAP_MS,
           anchor_steps: int = ANCHOR_STEPS,
           kinds: Sequence[str] = ALL_ECHOES,
           curve: Optional[Dict[int, float]] = None,
           slope: float = 0.0) -> List[Echo]:
    """找出「同一句又來了」的地方，照時間排好。

    `curve` ＝「這個音程該走幾格」，給「整句該平移多少」用（`learn_steps`
    學到的優先）。**整份譜一起算**，不要只傳視窗裡那幾十顆：本體可能在視窗
    外面，捲動時標記就會自己閃掉。
    """
    wanted = set(kinds)
    out: List[Echo] = []
    for hand, series in ML.events(notes, by_hand, onset_ms).items():
        line = _Line([e.top for e in series])
        steps = max(1, int(anchor_steps))
        if len(line) <= steps:
            continue
        seen: Dict[tuple, List[int]] = defaultdict(list)
        for j in range(len(line) - steps):
            sig = tuple(line.steps[j:j + steps])
            best: Optional[Echo] = None
            for i in seen[sig][-RECENT_ANCHORS:]:
                if (line.time[j] - line.time[i] > int(gap_ms)
                        or i + steps > j):
                    continue
                if not all(abs(line.gaps[i + k] - line.gaps[j + k]) <= line.tol
                           for k in range(steps)):
                    continue
                offset = line.pitch[j] - line.pitch[i]
                kind = ECHO_SHIFT if offset else ECHO_SAME
                if kind not in wanted:
                    continue
                echo = _build(line, hand, i, j, offset, curve, slope)
                if echo is not None and (best is None
                                         or echo.length > best.length):
                    best = echo
            seen[sig].append(j)
            if best is not None:
                out.append(best)
    return _pick(out)


# ── 照本體把這一句排好 ────────────────────────────────────────────────

#: 目標格被佔住時，最多往旁邊讓幾格。見 `apply_shape` 的實測表。
MAX_ADJUST = 2


@dataclass
class Applied:
    """`apply_shape` 做了什麼。`exact`/`nudged`/`blocked` 的單位是**一組冠音**。"""
    phrases: int = 0         # 動到幾句
    moved: int = 0           # 動到幾顆音符（含跟著走的和弦內音）
    exact: int = 0           # 幾組排到了本體的形狀上
    nudged: int = 0          # 幾組擠不下、讓到附近最近的空位
    blocked: int = 0         # 幾組附近也沒空位，原樣留著
    ornaments: int = 0       # 幾顆裝飾音跟著走

    @property
    def groups(self) -> int:
        return self.exact + self.nudged + self.blocked


def apply_shape(notes: Iterable[Any], found: Sequence[Echo],
                by_hand: bool = True, onset_ms: int = ML.ONSET_MS,
                total_lanes: int = ML.TOTAL_GAME_KEYS,
                max_adjust: int = MAX_ADJUST) -> Applied:
    """把回聲照**本體的形狀**排好：每一顆都擺到「本體那一顆的位置 ＋ 整句位移」。

    使用者的說法是「套得不像我排的沒問題，我排的不一樣反而算是失誤，除非沒空間」
    ——所以這裡不管模組說明裡那個「同形狀只有 24%」的實測，那 76% 照他的標準
    是手滑，不是風格。唯一的例外就是他講的那一句：**沒空間**。

    整句位移用哪一個
    ----------------

    * 這一句沒被判破（`Echo.bad` 是 False）⇒ 用他**實際**移的量（`shift` 的
      四捨五入）。位移在 3 格容許內就是他的決定，這個動作只修形狀，不該順手
      把整句搬走。
    * 判破了 ⇒ 用**該移**的量（`want`）。畫面上標出來的就是這個差距，按下去
      當然要把它補掉。
    * 原樣重複的 `want` 是 0、`shift` 多半也是 0，等於就地把形狀對齊——那正好
      是 `melody_line.KIND_REPEAT`（同音同格）整句一起做。

    它會動到什麼
    ------------

    照本體抄會把句內形狀從 **15.8% 拉到 87.8%**（207 份有移調回聲的譜、3741 句、
    13054 顆），另外兩條規則跟著動：

        做法                    形狀對   方向     同音不同格
        不排                    15.8%    6361     28113     ← 基準（處數）
        照本體排（這個動作）     87.8%    +1042    +3894
        ＋整份把方向拉回來       73.4%    +88      +3793

    **「同音不同格」那一欄不用管**——2026-11-16 使用者講明「同音同軌沒必要，
    形狀比較重要」。而且它本來也閃不掉：整句照本體的形狀搬去新的音高，就是放棄
    「這個音高上次待在哪一格」，除非音高→鍵道剛好是一條直線，兩條不可能同時
    成立。試過「會拆掉同音同格的就不排」，形狀只剩 33.2%，等於沒在排。

    方向那一欄補得回來：`repair_direction`（選單上的「把破掉的方向拉回來」）能
    把 +1042 收回到 +88，但它會回頭改掉 14.4pp 的形狀。**所以不內建**——形狀
    比較重要是使用者說的，那一道收尾留給他按完看報告再自己決定（Ctrl+Z 回得去）。
    另外試過「只把句首句尾拉回方向」（救得少、形狀賠得差不多）和「整句排不乾淨
    就整句不排」（放棄 442 句、一成二，才把 +1042 壓到 +632），兩個都不划算。

    沒空間的時候
    ------------

    和 `lead_by_top` 同一套三道不准：出界、和**同一刻**別的音符重疊（含另一手）、
    組裡有不准動的音符（顫音的鍵道連著子音符，見 `repair_direction`）。目標格
    被佔住時往旁邊讓，最多 `max_adjust` 格，讓不出來就原樣留著並記在 `blocked`。

    `max_adjust` 取 2（而不是 `lead_by_top` 的 3）：這裡是「照本體抄」，讓太遠
    就已經不是本體的形狀了，寧可留著讓使用者自己看。實測（207 份有移調回聲的
    譜、3741 句、22984 組冠音）：

        讓幾格   排到位    讓開     沒空間
        0        96.2%     ——      3.8%（871 組）
        1        96.1%     2.1%    1.8%
        2        96.1%     2.9%    0.9%（213 組）  ← 預設
        3        96.1%     3.4%    0.4%

    動得多大：每一組冠音要移的格數是 **0 格 53.3%**（本來就在本體的形狀上）、
    1 格 27.7%、2 格 9.9%、3 格以上 9.1%（最遠 21 格）。換句話說這個動作會動到
    回聲裡**將近一半**的音符，不是只修零星幾顆。

    搬的是**整組**：冠音去哪，同一刻同一手的和弦內音整組跟著走，和絃內部一格
    都不動——和排譜器、`repair_direction`、`lead_by_top` 都是同一條線。

    裝飾音沒有本體可以抄，所以跟著**時間上最近的那一顆配對音符**走同樣的量，
    整句才不會留一顆在原地。

    `found` 照時間處理：前面那一句如果本身也是更前面那一句的回聲，它會先被排好，
    後面這一句抄到的就是排好之後的位置（一條鏈接得起來）。
    """
    pool = list(notes)
    owner: Dict[int, List[Any]] = {}
    for group in ML.clusters(pool, onset_ms):
        for note in group:
            owner[id(note)] = group
    stacks: Dict[int, List[Any]] = {}
    for _hand, series in ML.events(pool, by_hand, onset_ms).items():
        for event in series:
            stacks[id(event.top)] = event.members

    def free(stack: Sequence[Any]) -> bool:
        return not any(int(getattr(n, 'note_type', 0) or 0) & 0x40
                       for n in stack)

    def legal(stack: Sequence[Any], shift: int) -> bool:
        group = owner.get(id(stack[0]), stack)
        inside = {id(m) for m in stack}
        for note in stack:
            lo, hi = int(note.min_key) + shift, int(note.max_key) + shift
            if lo < 0 or hi > int(total_lanes) - 1:
                return False
            for other in group:
                if id(other) in inside:
                    continue
                if lo <= int(other.max_key) and int(other.min_key) <= hi:
                    return False
        return True

    out = Applied()

    def place(stack: Sequence[Any], want: int) -> Optional[int]:
        """平移 `want` 格。擠不下就找附近最近的，都不行回 `None`。"""
        if not want:
            # 已經在本體的形狀上。這裡**不檢查** legal：本來就疊著的音符不該
            # 被這個動作順手推開，那是「處理左右重疊」的事。
            return 0
        if not free(stack):
            return None
        way = 1 if want > 0 else -1
        for extra in range(0, int(max_adjust) + 1):
            picks = ((want,) if not extra
                     else (want + way * extra, want - way * extra))
            hit = next((s for s in picks if legal(stack, s)), None)
            if hit is None:
                continue
            for note in stack:
                note.min_key = int(note.min_key) + hit
                note.max_key = int(note.max_key) + hit
            out.moved += len(stack)
            return hit
        return None

    for echo in found:
        base = int(round(echo.want if echo.bad else echo.shift))
        moves: List[Tuple[int, int]] = []        # (時間, 實際平移了幾格)
        before = out.moved
        for source, repeat in echo.pairs:
            stack = stacks.get(id(repeat), [repeat])
            want = _edge(source) + base - _edge(repeat)
            hit = place(stack, want)
            if hit is None:
                out.blocked += 1
                continue
            moves.append((ML._start(repeat), hit))
            if hit == want:
                out.exact += 1
            else:
                out.nudged += 1
        for note in echo.ornaments:
            if not moves:
                break
            when = ML._start(note)
            near = min(moves, key=lambda m: abs(m[0] - when))
            if place(stacks.get(id(note), [note]), near[1]):
                out.ornaments += 1          # 0（本來就對）和 None（沒空間）都不算
        if out.moved > before:
            out.phrases += 1
    return out


def ornaments(found: Sequence[Echo]) -> List[Any]:
    """所有「比本體多出來」的音符，照時間排好。"""
    out = [n for echo in found for n in echo.ornaments]
    return sorted(out, key=lambda n: int(n.start))


def summary(found: Sequence[Echo]) -> Dict[str, int]:
    """幾處原樣、幾處移調、幾處插了裝飾音、幾處位移沒跟上。報告用。"""
    out = {ECHO_SAME: 0, ECHO_SHIFT: 0, 'ornament': 0, 'bad': 0,
           'ornament_notes': 0}
    for echo in found:
        out[echo.kind] = out.get(echo.kind, 0) + 1
        if echo.ornaments:
            out['ornament'] += 1
            out['ornament_notes'] += len(echo.ornaments)
        if echo.bad:
            out['bad'] += 1
    return out
