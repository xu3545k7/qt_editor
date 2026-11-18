"""照前面手排好的小節，自動排後面相似的段落。

使用者要的流程：

    輸入目前的小節 → 前面的音符當參考 → 逐一比對後面的小節 →
    自動排好後面相似的部分 → 匹配率太低的就去遷就匹配率高的部分重新排

所以這裡做兩件不同的事，順序也不能換：

1. **照抄**（`ACTION_COPY`）。拿去和參考小節比，對得準的音符把參考的鍵道
   （和左右手）套上去。比對與套用整個走 `pattern_transfer`——那就是右鍵
   「複製／套用排列分布」在用的同一套 DP 對齊。手動做得到的事自動做一遍，
   不要另寫第二套語意。

2. **遷就**（`ACTION_REMAP`）。對不上的不能放著不管（使用者：「匹配率太低的
   就去遷就匹配率高的部分進行重新排」），但也不能叫整份排譜器重排——那會照
   它自己的音域映射來排，和前面手排的部分對不起來。改成從**參考段落＋剛剛
   照抄成功的那些音符**量出一條「音高 → 鍵道中心」的對應，用它來擺這些音符。
   學習樣本包含第 1 步的成果，這就是「遷就匹配率高的部分」。

決定的單位是「一顆音符」，不是整個小節，也不是一隻手
----------------------------------------------------
一開始是整個小節一起判。實測（使用者指的那份 Gregorius AI 轉譜，182 小節
4848 顆，參考 1~25 小節）：第 26~36 小節的右手是第 25 小節右手的原樣重複
（0.89），左手一直在變（0.22~0.57），整節一起比被平均成 0.52~0.75，那個明顯
的重複就被拖到門檻以下——40 個小節裡只找到 3 個。

第二版改成照 `hand` 分開比，使用者否決了：**分手本身就可能是錯的**（AI 轉譜
的 hand 是猜的），拿一個會錯的欄位當切分依據，等於把它的錯誤放大。

所以現在整個小節一起比對，但**一顆一顆決定**：DP 對齊出來的每一對都帶著自己
的代價，代價夠小的（時間和音高都對得上，見 `GOOD_PAIR_COST`）才算「相似的
部分」、照抄過去；同一個小節裡其餘的音符走重排。左手在變、右手在重複的小節
就會自然地一半照抄、一半重排，而且完全不看 `hand` 那一欄。

`plan()` 只量不改，`apply()` 才動譜面——對話框要先給人看「會動幾顆」。
呼叫端負責 `push_history()` 與 `rebuild_display_cache()`。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import melody_line as ML
from . import pattern_transfer as PT
from .models import TOTAL_GAME_KEYS

#: 一對音符要多準才算「相似的部分」。`pattern_transfer` 的代價是
#: 時間差／容許值（上限 1）＋ 音高差／2（上限 2），完全吻合是 0。
#: 0.6 放行「音高一樣、時間差在容許值內」和「差 1 個半音、時間幾乎不差」，
#: 擋掉差 2 個半音以上的——那種配對是 DP 為了連起序列而硬湊的。
GOOD_PAIR_COST = 0.6

#: 一個小節要有多少比例的音符對得上，才值得照抄。低於這個就整節重排：
#: 只有兩三顆對上的小節去照抄，會在一片重排的音符中間留下幾顆外來的鍵道。
DEFAULT_THRESHOLD = 0.5

#: 每個目標小節最多拿幾個參考小節去做 DP 對齊。先用便宜的指紋挑出前幾名，
#: 不然 180 個小節 × 25 個參考小節就是四千多次 DP，介面會凍住。
MAX_CANDIDATES = 6

#: 學出來的「鍵道中心 / 半音」容許範圍。超出這個範圍的斜率不是樂譜，是雜訊：
#: 參考段落只有兩顆相鄰音高、又被放在鍵盤兩端時，迴歸會給出 27 格／半音。
#: 上限取 2.0 ＝ 一個八度鋪滿 24 格，已經是整個鍵盤了，比這更斜一定是雜訊。
MIN_SLOPE = 0.05
MAX_SLOPE = 2.0

ACTION_COPY = 'copy'        # 這個小節有夠多音符對得上：對得準的照抄
ACTION_REMAP = 'remap'      # 對不上，照學到的映射重排
ACTION_KEEP = 'keep'        # 對不上、而且使用者不要重排


@dataclass
class Bar:
    """一個小節的切片。"""
    index: int                         # 0-based
    lo: int                            # 起 ms
    hi: int                            # 迄 ms
    notes: List[Any] = field(default_factory=list)


@dataclass
class BarPlan:
    """一個目標小節的處置：哪幾顆照抄、哪幾顆重排。"""
    index: int
    action: str
    source: Optional[int] = None       # 對上的參考小節（0-based）
    coverage: float = 0.0              # 這個小節的匹配率
    similarity: float = 0.0            # `pattern_transfer` 的整體相似度
    transpose: int = 0
    scale: float = 1.0
    #: (來源排列, 目標音符) —— 對得準、要照抄的那些
    good: List[Tuple[PT.Shape, Any]] = field(default_factory=list)
    #: 同一個小節裡**要重排**的音符。使用者關掉重排時這裡是空的——它的語意是
    #: 「待辦」而不是「沒對上的」，不然 `apply` 會照著它動那些該留在原地的音符。
    leftover: List[Any] = field(default_factory=list)
    #: 這個小節本來有幾顆（不管最後動不動），報告用
    total: int = 0

    @property
    def notes(self) -> int:
        return int(self.total)


@dataclass
class Plan:
    from_measure: int                  # 1-based，和介面上看到的一樣
    reference_measures: List[int] = field(default_factory=list)
    reference_notes: int = 0
    bars: List[BarPlan] = field(default_factory=list)
    threshold: float = DEFAULT_THRESHOLD
    #: 排不出計畫時的原因（譜面沒有小節線、沒有音高、參考段落空的…）
    reason: str = ''

    @property
    def ok(self) -> bool:
        return not self.reason and bool(self.bars)

    def of(self, action: str) -> List[BarPlan]:
        return [b for b in self.bars if b.action == action]

    @property
    def copied(self) -> List[BarPlan]:
        return self.of(ACTION_COPY)

    @property
    def notes_to_copy(self) -> int:
        return sum(len(b.good) for b in self.copied)

    @property
    def notes_to_remap(self) -> int:
        return sum(len(b.leftover) for b in self.bars
                   if b.action in (ACTION_COPY, ACTION_REMAP))

    @property
    def mean_coverage(self) -> float:
        got = self.copied
        return sum(b.coverage for b in got) / len(got) if got else 0.0

    @property
    def transposed(self) -> List[BarPlan]:
        return [b for b in self.copied if b.transpose]


@dataclass
class Stats:
    bars_copied: int = 0
    bars_remapped: int = 0
    notes_copied: int = 0
    notes_remapped: int = 0
    notes_changed: int = 0
    #: 收尾照冠音重新對過位置的音符數（`keep_melody`）
    melody_fixed: int = 0


# ── 切片 ──────────────────────────────────────────────────────────────

def arrangeable(note: Any) -> bool:
    """這顆音符的鍵道動得嗎？

    顫音不動：它的鍵道還連著每一格子音符，要改得整顆一起搬（`pattern_transfer`
    也是同一條規則）。隱藏音符不動：畫面上看不到，改了也看不出來。
    """
    if int(getattr(note, 'note_type', 0) or 0) & 0x40:
        return False
    return not getattr(note, 'hidden', False)


def measure_bars(model: Any) -> List[Bar]:
    """把譜面切成小節。

    音符用 `start` 落在 [起, 迄) 裡來歸屬——判定看的是起音時間，長條跨過小節線
    仍然屬於它起音的那個小節。
    """
    count = int(model.count_measures() or 0)
    if count <= 0:
        return []
    bars: List[Bar] = []
    for i in range(count):
        lo, hi = model.get_measure_time_range(i)
        if lo is None or hi is None or hi <= lo:
            bars.append(Bar(i, 0, 0))
        else:
            bars.append(Bar(i, int(lo), int(hi)))
    edges = [b.lo for b in bars]
    for note in model.notes_tree:
        if not arrangeable(note):
            continue
        when = int(note.start)
        # 小節是連續遞增的，所以二分搜尋就夠；逐一掃是 O(音符 × 小節)，
        # 4848 顆 × 182 小節的譜光是切片就要好幾秒。
        idx = bisect_right(edges, when) - 1
        if 0 <= idx < len(bars) and bars[idx].hi > bars[idx].lo \
                and bars[idx].lo <= when < bars[idx].hi:
            bars[idx].notes.append(note)
    return bars


# ── 便宜的指紋：先篩掉顯然不像的參考小節 ─────────────────────────────

#: 指紋把小節內的時間切成幾格（1/48 小節 ≈ 四四拍的 32 分三連音）
FINGERPRINT_STEPS = 48


def fingerprint(bar: Bar) -> frozenset:
    """(小節內的位置, 離最低音幾個半音) 的集合。

    位置用「佔小節的幾分之幾」而不是毫秒，所以變速或不同拍號的小節也比得起來；
    音高用相對最低音，所以移調的重複樂句照樣對上。完全不看 `hand`——分手是猜
    出來的，可能是錯的。
    """
    span = max(1, int(bar.hi) - int(bar.lo))
    pitches = [int(n.pitch) for n in bar.notes if n.pitch is not None]
    base = min(pitches) if pitches else 0
    out = set()
    for n in bar.notes:
        slot = int(round((int(n.start) - int(bar.lo)) / span * FINGERPRINT_STEPS))
        rel = (int(n.pitch) - base) if n.pitch is not None else -99
        out.add((slot, rel))
    return frozenset(out)


def rough_score(a: frozenset, b: frozenset) -> float:
    """兩份指紋的 Jaccard 相似度。只用來排名，不當成最後的匹配率。"""
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ── 學「音高 → 鍵道中心」 ─────────────────────────────────────────────

@dataclass
class PitchLine:
    """一條音高→鍵道中心的對應。"""
    exact: Dict[int, float] = field(default_factory=dict)
    slope: float = 0.0
    intercept: float = 0.0
    samples: int = 0

    def centre(self, pitch: Optional[int]) -> Optional[float]:
        if pitch is None:
            return None
        hit = self.exact.get(int(pitch))
        if hit is not None:
            # 這個音高在參考段落裡出現過，就照它當時被放在哪裡——學到的直線
            # 只是給沒出現過的音高用的退路。
            return hit
        if self.samples < 2:
            return None
        return self.slope * float(pitch) + self.intercept


@dataclass
class LaneModel:
    """整份譜一條線，**不分手**。

    本來是每隻手各一條（同一個音高左手放的位置和右手可能不一樣）。拿掉的理由
    不是品味而是實測：7 份手排譜 7822 顆重排的音符，分手版落在使用者原本位置
    24.7% / ±1 格 52.0%，不分手版 24.8% / 51.9%——差不到 0.1%，因為真正在起
    作用的是「這個音高上次放在哪」那張表，直線只是沒見過的音高的退路。

    既然分手一毛錢都沒買到，就不要依賴它：使用者說「分手友會錯，要看資料本身」。
    把 hand 亂填一遍再跑，分手版會掉到 24.3%，不分手版一動也不動。
    """
    line: PitchLine = field(default_factory=PitchLine)

    def centre(self, note: Any) -> Optional[float]:
        return self.line.centre(None if note.pitch is None else int(note.pitch))

    @property
    def samples(self) -> int:
        return self.line.samples


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _fit(by_pitch: Dict[int, List[float]]) -> PitchLine:
    """一組 (音高 → 看過的鍵道中心) 配成一條線。

    同一個音高出現很多次（而且被放在不同鍵道）時取中位數，不是平均——手排的譜
    偶爾會為了避開別的音符把某一顆挪開，那是離群值。
    """
    fit = PitchLine()
    fit.exact = {p: _median(v) for p, v in by_pitch.items()}
    fit.samples = sum(len(v) for v in by_pitch.values())
    xs = sorted(fit.exact)
    if len(xs) >= 2:
        ys = [fit.exact[x] for x in xs]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        var = sum((x - mx) ** 2 for x in xs)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = (cov / var) if var > 1e-9 else 0.0
        # 夾在合理範圍內：樣本少的時候迴歸出來的斜率可以是 0 或 5，兩者都不是
        # 樂譜。夾完要重算截距，否則整條線會整體偏移。
        slope = max(MIN_SLOPE, min(MAX_SLOPE, slope))
        fit.slope = slope
        fit.intercept = my - slope * mx
    return fit


def learn_lane_model(notes: Sequence[Any]) -> LaneModel:
    """從已經排好的音符量出音高→鍵道中心。"""
    seen: Dict[int, List[float]] = {}
    for note in notes:
        if note.pitch is None or not arrangeable(note):
            continue
        seen.setdefault(int(note.pitch), []).append(
            (int(note.min_key) + int(note.max_key)) / 2.0)
    return LaneModel(line=_fit(seen))


def spread_group(notes: Sequence[Any], total_lanes: int = TOTAL_GAME_KEYS) -> int:
    """把同一刻的一組音符推開到不重疊，維持各自的寬度與音高順序。

    只在真的疊到時才推（照音高從低到高走一次，疊到就往右讓），所以沒有衝突的
    和弦不會被改成某種固定排法。整疊超出右邊界時把整疊往左移回來，移到最低那顆
    貼齊 0 號鍵就停；真的塞不下（整疊比鍵盤還寬）就維持重疊，不硬擠。

    **不分手**：同一刻同一個鍵道上有兩顆音就是打不出來，和它們屬於哪隻手無關，
    而分手還可能是錯的。回傳動到幾顆。
    """
    ordered = sorted(notes, key=lambda n: (int(n.pitch or 0), int(n.min_key)))
    if len(ordered) < 2:
        return 0
    before = [(int(n.min_key), int(n.max_key)) for n in ordered]
    for prev, cur in zip(ordered, ordered[1:]):
        if int(cur.min_key) <= int(prev.max_key):
            shift = int(prev.max_key) + 1 - int(cur.min_key)
            cur.min_key = int(cur.min_key) + shift
            cur.max_key = int(cur.max_key) + shift
    over = int(ordered[-1].max_key) - (int(total_lanes) - 1)
    if over > 0:
        over = min(over, int(ordered[0].min_key))
        for n in ordered:
            n.min_key = int(n.min_key) - over
            n.max_key = int(n.max_key) - over
    # 整疊比鍵盤還寬時上面那一步退不完，剩下的硬夾回範圍內。**寧可重疊也不要
    # 出界**：鍵道 28 以上的音符是壞資料（存檔驗證會擋、遊戲畫不出來），而重疊
    # 只是不好打。實測那份 182 小節的譜，不夾的話有音符跑到第 29 格。
    for n in ordered:
        width = int(n.max_key) - int(n.min_key)
        lo = max(0, min(int(total_lanes) - 1 - width, int(n.min_key)))
        n.min_key, n.max_key = lo, lo + width
    changed = 0
    for (lo, hi), n in zip(before, ordered):
        if (int(n.min_key), int(n.max_key)) != (lo, hi):
            changed += 1
    return changed


def remap_notes(notes: Sequence[Any], model: LaneModel,
                total_lanes: int = TOTAL_GAME_KEYS,
                fixed: Sequence[Any] = (), tolerance_ms: int = 0) -> int:
    """照學到的映射擺 `notes`，再把同刻撞在一起的推開。回傳改了幾顆。

    `fixed` 是同一刻可能存在、但**不准動**的音符（照抄過去的那些）：它們要參與
    推擠的計算，否則重排的音符會疊到剛排好的上面。
    """
    changed = 0
    for note in notes:
        centre = model.centre(note)
        if centre is None:
            continue
        width = max(1, int(note.max_key) - int(note.min_key) + 1)
        lo = int(round(float(centre) - (width - 1) / 2.0))
        lo = max(0, min(int(total_lanes) - width, lo))
        if (int(note.min_key), int(note.max_key)) != (lo, lo + width - 1):
            note.min_key, note.max_key = lo, lo + width - 1
            changed += 1

    movable = {id(n) for n in notes}
    groups: List[List[Any]] = []
    for note in sorted(list(notes) + list(fixed), key=lambda n: int(n.start)):
        if groups and int(note.start) - int(groups[-1][0].start) <= int(tolerance_ms):
            groups[-1].append(note)
        else:
            groups.append([note])
    for group in groups:
        if any(id(n) in movable for n in group):
            spread_group(group, total_lanes)
    return changed


# ── 計畫 ──────────────────────────────────────────────────────────────

def plan(model: Any, from_measure: int, threshold: float = DEFAULT_THRESHOLD,
         remap_low: bool = True, max_candidates: int = MAX_CANDIDATES,
         good_cost: float = GOOD_PAIR_COST) -> Plan:
    """量出「後面每個小節要怎麼辦」，不改譜面。

    `from_measure` 是 1-based 的小節號（和介面上看到的一樣）：它**自己也算在
    要排的範圍裡**，參考段落是它前面那些。
    """
    out = Plan(from_measure=int(from_measure), threshold=float(threshold))
    bars = measure_bars(model)
    if not bars:
        out.reason = '這份譜沒有小節線，分不出小節'
        return out
    if not any(n.pitch is not None for n in model.notes_tree):
        # 比對和重排兩條路都要音高：比對靠音高差認重複，重排靠音高決定鍵道。
        # 沒有音高時整個工具只會一顆都不動，所以在這裡就講清楚。
        out.reason = ('這份譜沒有音高資料，比不出相似度也排不出鍵道。\n'
                      '先用 工具 → 以 MIDI 修復 →「從 MIDI 還原音高與表情」')
        return out

    first = max(1, int(from_measure)) - 1          # 轉成 0-based
    if first <= 0:
        out.reason = '要從第 2 小節以後開始，前面才有東西可以當參考'
        return out
    if first >= len(bars):
        out.reason = '第 %d 小節後面沒有小節了' % int(from_measure)
        return out

    refs = [(bar, fingerprint(bar)) for bar in bars[:first] if bar.notes]
    if not refs:
        out.reason = '前面 %d 個小節裡沒有音符，沒有東西可以當參考' % first
        return out
    out.reference_measures = [bar.index for bar, _p in refs]
    out.reference_notes = sum(len(bar.notes) for bar, _p in refs)

    captured: Dict[int, PT.Distribution] = {}      # 小節 index → 排列分布
    for bar in bars[first:]:
        if not bar.notes:
            continue
        out.bars.append(_plan_one(bar, refs, captured, threshold, remap_low,
                                  max_candidates, good_cost))
    return out


def _plan_one(bar: Bar, refs: Sequence[Tuple[Bar, frozenset]],
              captured: Dict[int, PT.Distribution], threshold: float,
              remap_low: bool, max_candidates: int,
              good_cost: float) -> BarPlan:
    mine = fingerprint(bar)
    ranked = sorted(refs, key=lambda r: -rough_score(mine, r[1]))
    best: Optional[Tuple[float, Bar, PT.Match, List[Tuple[PT.Shape, Any]]]] = None
    for ref, _print in ranked[:max(1, int(max_candidates))]:
        dist = captured.get(ref.index)
        if dist is None:
            dist = PT.capture(ref.notes)
            captured[ref.index] = dist
        if not dist.shapes:
            continue
        result = PT.match(dist, bar.notes)
        # 匹配率看的是「這個小節有幾顆真的對上了」，不是 DP 的總分：整體相似度
        # 會被另一半在變的聲部稀釋，而我們本來就只打算照抄對上的那些。
        good = [(shape, note) for shape, note, cost in result.pairs
                if cost <= float(good_cost)]
        coverage = len(good) / float(max(1, len(bar.notes)))
        if best is None or coverage > best[0]:
            best = (coverage, ref, result, good)
    if best is None:
        return BarPlan(bar.index, ACTION_REMAP if remap_low else ACTION_KEEP,
                       total=len(bar.notes),
                       leftover=list(bar.notes) if remap_low else [])
    coverage, ref, result, good = best
    if coverage >= float(threshold) and good:
        taken = {id(note) for _s, note in good}
        return BarPlan(bar.index, ACTION_COPY, source=ref.index,
                       coverage=coverage, similarity=result.similarity,
                       transpose=result.transpose, scale=result.scale,
                       good=good, total=len(bar.notes),
                       leftover=[n for n in bar.notes if id(n) not in taken]
                       if remap_low else [])
    return BarPlan(bar.index, ACTION_REMAP if remap_low else ACTION_KEEP,
                   source=ref.index, coverage=coverage, total=len(bar.notes),
                   similarity=result.similarity,
                   leftover=list(bar.notes) if remap_low else [])


def _shift_shape(shape: PT.Shape, lanes: int,
                 total_lanes: int = TOTAL_GAME_KEYS) -> PT.Shape:
    """把一份排列整個往旁邊移幾個鍵道，維持寬度。"""
    width = int(shape.max_key) - int(shape.min_key)
    lo = max(0, min(int(total_lanes) - 1 - width, int(shape.min_key) + int(lanes)))
    return PT.Shape(shape.offset, shape.pitch, shape.hand, lo, lo + width)


def apply(model: Any, prepared: Plan, copy_hand: bool = True,
          shift_transposed: bool = True, keep_melody: bool = True,
          tolerance_ms: int = 0) -> Stats:
    """照計畫改譜面。呼叫端負責 `push_history` 與 `rebuild_display_cache`。

    順序是刻意的：**先照抄，再學映射，最後才重排**。學習樣本要包含剛剛照抄好
    的音符，那才是「遷就匹配率高的部分」；反過來先重排的話，學到的是還沒對齊
    的舊鍵道。

    `shift_transposed`：樂句原樣重複但整段移了調時，把照抄過去的鍵道也跟著
    平移（移幾個半音 × 參考段落量到的「鍵道／半音」）。實測 7 份手排譜
    2674 顆照抄的音符：沒移調的落在使用者原本的位置 40.2%、±1 格 70.9%，
    移了調的只有 19.3% / 63.5%——鍵道照抄過去，音高卻高了五度，整段就偏掉。

    `keep_melody`：重排完**再照冠音走一次**（`melody_line.lead_by_top`，只動重排
    的那些，照抄的不碰）。使用者的說法是「冠音和之前比，其他的部分在守住旋律的
    前提下根據那些冠音的變化重排」。

    為什麼需要：上面那張「音高 → 鍵道中位數」沒有「上一顆在哪裡」的概念，所以
    同一句裡音高往上走、鍵道卻停在原地是常態。收尾改成先決定冠音要去哪（上一個
    冠音 ＋ 這個音程該走幾格），同一組其他的音符整組跟著走。

    實測 20 份譜、後半段 10424 顆，和使用者自己排好的那一半比：
    完全相同 27.4% → 28.7%、±1 格 64.6% → 68.6%、守方向 82.5% → 90.5%，
    同音同格 83.8% → 82.3%（持平），重疊 79 → 75（沒變多）。
    """
    stats = Stats()
    if not prepared.ok:
        return stats

    boundary = _measure_start(model, prepared.from_measure)
    reference = [n for n in model.notes_tree if int(n.start) < boundary]
    slope = learn_lane_model(reference).line.slope if shift_transposed else 0.0

    for item in prepared.copied:
        pairs = []
        shift = int(round(item.transpose * slope)) if slope else 0
        for shape, note in item.good:
            pairs.append(((_shift_shape(shape, shift) if shift else shape),
                          note, 0.0))
        # 借 `pattern_transfer.apply` 來寫：MIDI 模式下改手要連音軌一起改，
        # 那段邏輯只有它有。這裡只把「對得準」的那些交給它。
        changed = PT.apply(model, PT.Match(pairs=pairs), copy_hand=copy_hand)
        stats.bars_copied += 1
        stats.notes_copied += len(item.good)
        stats.notes_changed += changed

    leftovers = [item for item in prepared.bars if item.leftover]
    if not leftovers:
        return stats

    copied_ids = {id(note) for item in prepared.copied for _s, note in item.good}
    learn_from = [n for n in model.notes_tree
                  if int(n.start) < boundary or id(n) in copied_ids]
    lane_model = learn_lane_model(learn_from)
    if not lane_model.samples:
        return stats

    for item in leftovers:
        fixed = [note for _s, note in item.good]
        changed = remap_notes(item.leftover, lane_model, TOTAL_GAME_KEYS,
                              fixed=fixed, tolerance_ms=tolerance_ms)
        if item.action == ACTION_REMAP:
            stats.bars_remapped += 1
        stats.notes_remapped += len(item.leftover)
        stats.notes_changed += changed

    if keep_melody:
        # 整份譜一起看，不是只看重排的那一段：重排的第一組要和它**前面**那一組
        # （參考段落的最後一個冠音）比，才知道這一步該走幾格。可以動的只有重排
        # 的那些，照抄過去的鍵道來自使用者自己排好的前半段。
        movable = {id(n) for item in leftovers for n in item.leftover}
        reference = [n for n in model.notes_tree if id(n) not in movable]
        stats.melody_fixed = ML.lead_by_top(
            model.notes_tree, movable=movable,
            curve=ML.learn_steps(reference),
            anchor=lane_model.centre, slope=lane_model.line.slope)
    return stats


def _measure_start(model: Any, from_measure: int) -> int:
    """第 `from_measure`（1-based）小節的起點；算不出來就回 0。"""
    lo, _hi = model.get_measure_time_range(max(0, int(from_measure) - 1))
    return int(lo) if lo is not None else 0
