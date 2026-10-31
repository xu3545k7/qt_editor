# -*- coding: utf-8 -*-
"""智慧重排吸附：用「幾個小節 ＋ BPM」的序列重建整條小節線，再把音符吸附上去。

## 要解決的問題

AI 轉譜會給整首一個固定 BPM。那個值猜錯的時候（實測一份 POTENTIAL 被判成
199.95，實際是 150/155/160 的變拍），錯的格線會把音符的 `start` 一起吸歪，
整份譜就爛在那裡。使用者的原話：

> 音符 ms 大致準確，但拍子可能被判定的固定判錯了 200，導致很多音符的 start
> 被吸附影響。我希望我的新分布拍子數量總和和速度可以讓她還原成正常的樣子，
> **所以小節範圍不能固定，會繼承錯誤的判斷**。

最後那句是整個設計的關鍵：**小節編號本身就是錯判的產物，不能當輸入。**
所以這裡收的是「第一段 N 個小節 150 BPM、第二段 M 個小節 155 BPM…」，從譜面
開頭依序鋪下去，小節編號是**算出來的結果**。

第一版把小節範圍當輸入、固定小節數，於是一小節的長度被「跨度 ÷ 小節數」綁死，
只表達得出幾個百分比以內的 BPM 變化——使用者給的 150/155/160 在一份 199.95 的
譜上**每一段都被「BPM 太慢」擋掉**，看起來像按了沒反應。那個版本已整個換掉。

## 怎麼做

1. 照序列算出每一拍的時間，從 `grid_start_ms()` 開始鋪。
2. `model.set_beat_grid_ms()` 整批覆寫格線——**完全不碰 `set_measure_bpm` /
   `set_measure_time_signature`**，所以也躲開了那些已知會把格線整段重切的坑
   （見 measure-grid-tiny-bars：插入 12/993、刪除 21、改拍號會刪音符…）。
3. 音符吸附到新格線上：只搬 `start`，長度跟著平移。被錯格線吸歪的那些，最近的
   新格點就是最好的還原。

音符的 ms 不會被整體縮放，所以**樂曲總長度不變**。格線長度和譜面長度對不上時
會回報差多少（含換算成幾個小節），由使用者調整段落。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

#: 一拍至少這麼長（毫秒），防止 BPM 填成天文數字之後鋪出幾十萬個拍點
MIN_BEAT_MS = 10.0


@dataclass
class Segment:
    """一段結構：`bars` 個小節，每小節 `numerator`/`denominator` 拍、速度 `bpm`。

    注意這裡**沒有小節編號**——編號是鋪完之後才知道的（見模組說明）。
    """

    bars: int
    bpm: float
    numerator: int = 4
    denominator: int = 4

    @property
    def beat_ms(self) -> float:
        """一「拍」多長。拍＝分母所指的音值，4/4 的一拍就是四分音符。"""
        return 4.0 * 60000.0 / (max(1, int(self.denominator))
                                * max(1e-6, float(self.bpm)))

    @property
    def beats(self) -> int:
        return max(0, int(self.bars)) * max(1, int(self.numerator))

    @property
    def total_ms(self) -> float:
        return self.beats * self.beat_ms

    @property
    def bar_ms(self) -> float:
        return max(1, int(self.numerator)) * self.beat_ms


@dataclass
class Plan:
    """算好的計畫。`apply()` 之前先給使用者看。"""

    segments: List[Segment] = field(default_factory=list)
    start_ms: float = 0.0
    beats: List[float] = field(default_factory=list)
    grid_end_ms: float = 0.0      #: 新格線涵蓋到的時間
    needed_ms: float = 0.0        #: 譜面需要涵蓋到的時間（最後一顆音符的結束）
    bars: int = 0
    ok: bool = False
    reason: str = ''
    moved: int = 0                #: 套用之後填：被搬動的音符數
    max_shift_ms: float = 0.0     #: …以及最大位移

    @property
    def short_by_ms(self) -> float:
        """格線比譜面短多少（負的表示鋪太長）。"""
        return self.needed_ms - self.grid_end_ms


def normalise(segments: Sequence[Segment]) -> List[Segment]:
    """丟掉空的與不合理的段落。**順序就是使用者給的順序，不排序。**"""
    out: List[Segment] = []
    for seg in segments:
        bars = max(0, int(seg.bars))
        num = max(1, int(seg.numerator))
        den = max(1, int(seg.denominator))
        bpm = float(seg.bpm)
        if bars <= 0 or bpm <= 0:
            continue
        candidate = Segment(bars=bars, bpm=bpm, numerator=num, denominator=den)
        if candidate.beat_ms < MIN_BEAT_MS:
            continue
        out.append(candidate)
    return out


def grid_start_ms(model: Any) -> float:
    """新格線從哪裡開始鋪：沿用譜面原本的第一個拍點。

    不從 0 開始——開頭那段導入空白是刻意的（見 ai_grid 的 pad），從 0 鋪會把
    整首的拍子相位移掉。
    """
    entries = model.get_beat_entries() or []
    if entries:
        return float(entries[0][1])
    return 0.0


def build_beats(segments: Sequence[Segment], start_ms: float) -> List[float]:
    """把段落序列鋪成一串拍點時間（最後多一條收尾線）。

    段落內部用**累加**而不是「段落起點 + i × 拍長」：後者在拍長除不盡的時候
    每一拍都帶一點捨入誤差，交界處會差出半毫秒。每一段的起點是前一段算完的
    游標，所以誤差也不會跨段累積。
    """
    beats: List[float] = []
    cursor = float(start_ms)
    for seg in segments:
        step = seg.beat_ms
        for _i in range(seg.beats):
            beats.append(cursor)
            cursor += step
    if beats:
        beats.append(cursor)
    return beats


#: 自動偵測時掃的 BPM 範圍。擋在 130~230 是為了避開半速／倍速歧義——
#: 不限制的話一首 200 的曲子有一半的視窗會選 100（音符落在 8 分上時，100 的
#: 16 分格和 200 的 8 分格是同一組點），看起來像速度亂跳。
DETECT_LO, DETECT_HI = 130.0, 230.0
#: 一個偵測視窗多長（毫秒）。6 秒在 200 BPM 下約 5 小節，夠穩又跟得上變速。
DETECT_WINDOW_MS = 6000.0
#: 視窗裡至少要這麼多音符才判斷
DETECT_MIN_NOTES = 20
#: 相鄰視窗的 BPM 差在這個比例內就併成同一段
DETECT_MERGE_RATIO = 0.02


def grid_offset(times: Sequence[float], bpm: float, division: int = 4) -> float:
    """這些起音離最近格點有多遠，用「半格」當單位。

    0 ＝ 每顆都在格點上，**0.5 ＝ 和隨機分布一樣**（上限 1.0）。除以半格這個
    正規化很重要：直接比毫秒的話格子大的 BPM 會佔便宜。

    **相位是算出來的，不是假設從 t=0 開始。** 原本直接取 `t % cell`，等於假設
    格線的原點在 0；評估曲子中段的視窗時那個假設是錯的，同一段在 6 秒視窗下
    偏差 0.16、用 18 秒視窗量卻變成 1.4（看起來比隨機還差）。這裡用環形平均
    求出最合適的相位再量，才是「這段音符有沒有照這個速度排」。
    """
    cell = 60000.0 / max(1e-6, float(bpm)) / max(1, int(division))
    if not times:
        return 0.5
    import math
    # 把 t mod cell 當成圓上的角度，環形平均就是最佳相位
    sin_sum = cos_sum = 0.0
    for t in times:
        angle = 2.0 * math.pi * (float(t) % cell) / cell
        sin_sum += math.sin(angle)
        cos_sum += math.cos(angle)
    phase = cell * (math.atan2(sin_sum, cos_sum) % (2.0 * math.pi)) / (2.0 * math.pi)
    total = 0.0
    for t in times:
        r = (float(t) - phase) % cell
        total += min(r, cell - r) / (cell / 2.0)
    return total / len(times)


def detect_segments(onsets: Sequence[Any], numerator: int = 4,
                    denominator: int = 4) -> List[Segment]:
    """從起音量出速度結構，回傳可以直接填進表格的段落。

    `onsets` 收 `(秒, 音高, 力度)` 三元組（`ai_grid.onsets_for_detection()` 的
    格式）；只給毫秒數字的話會被當成秒數換算回去，方便舊呼叫端。

    **直接用 `beat_detect.estimate_curve`。** 我本來自己寫了一套逐視窗掃格線
    的偵測，17 首實測被它打爆：平均偏差 0.493 vs 0.172（隨機是 0.5，等於我那套
    幾乎沒比亂猜好），16 比 1 輸。原因是 `estimate_curve` 給的是**逐拍**的時間
    表，貼得上連續的速度漂移；量化成幾個定速段落的掃描法表達不出來。

    而 `BeatCurve.segments` 的分數和它的逐拍形式**完全相同**（17 首每一首都
    一樣），所以「用段落表達」不損失任何東西——段落只是同一條曲線的摘要。

    **要餵原始未吸附的 MIDI。** 已經存成譜面的那份量出來只會確認原本的錯誤
    判斷（實測 POTENTIAL：吸附後的譜量出「一段 199.95」，原始 MIDI 量得到
    變速結構）。
    """
    from . import beat_detect as BD

    items: List[Tuple[float, int, int]] = []
    for item in onsets:
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            items.append((float(item[0]), int(item[1]), int(item[2])))
        else:
            items.append((float(item) / 1000.0, 60, 90))   # 只給毫秒的舊呼叫端
    if len(items) < 50:
        return []
    items.sort()
    try:
        curve = BD.estimate_curve(items, numerator=numerator)
    except Exception:                                      # noqa: BLE001
        return []
    if curve is None or not curve.segments:
        return []
    out: List[Segment] = []
    for seg in curve.segments:
        bars = max(1, int(seg.bars))
        out.append(Segment(bars=bars, bpm=round(seg.bpm, 2),
                           numerator=int(curve.numerator or numerator),
                           denominator=denominator))
    return out


def detect_start_ms(onsets: Sequence[Any], numerator: int = 4) -> Optional[float]:
    """偵測出的第一個小節起點（毫秒）。`detect_segments` 的段落要從這裡鋪。"""
    from . import beat_detect as BD

    items: List[Tuple[float, int, int]] = []
    for item in onsets:
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            items.append((float(item[0]), int(item[1]), int(item[2])))
        else:
            items.append((float(item) / 1000.0, 60, 90))
    if len(items) < 50:
        return None
    items.sort()
    try:
        curve = BD.estimate_curve(items, numerator=numerator)
    except Exception:                                      # noqa: BLE001
        return None
    if curve is None or not curve.beats:
        return None
    return float(curve.beats[0]) * 1000.0


def song_end_ms(model: Any) -> float:
    """譜面需要被格線蓋到的時間：最後一顆音符的結束。"""
    return float(max((int(n.end) for n in model.notes_tree), default=0))


def bars_to_cover(model: Any, segment: Segment,
                  before: Optional[Sequence[Segment]] = None) -> int:
    """照 `segment` 的 BPM 與拍號，還要幾個小節才蓋得到曲末。

    使用者：「畢竟我用精準 bpm 加上拍號，應該能判斷出來總長度了」——就是這個。
    `before` 是已經鋪好的前面幾段，會先把它們佔掉的時間扣掉。

    取 ceil：寧可多鋪一個空小節，也不要讓曲末那幾拍沒有小節線。
    """
    used = sum(seg.total_ms for seg in (before or ()))
    left = song_end_ms(model) - grid_start_ms(model) - used
    bar = segment.bar_ms
    if bar <= 0 or left <= 0:
        return 0
    return int(-(-left // bar))            # ceil


def plan(model: Any, segments: Sequence[Segment]) -> Plan:
    """算出新格線會長什麼樣子。**不動譜面。**"""
    segs = normalise(segments)
    item = Plan(segments=segs, start_ms=grid_start_ms(model))
    if not segs:
        item.reason = '沒有段落'
        return item
    item.beats = build_beats(segs, item.start_ms)
    item.grid_end_ms = item.beats[-1] if item.beats else item.start_ms
    item.bars = sum(seg.bars for seg in segs)
    item.needed_ms = song_end_ms(model)
    item.ok = len(item.beats) >= 2
    if not item.ok:
        item.reason = '段落太短，鋪不出格線'
    return item


def grid_points(beats: Sequence[float], division: int = 4) -> List[float]:
    """拍點 → 吸附用的格點（一拍切 `division` 格）。"""
    points: List[float] = []
    div = max(1, int(division))
    for a, b in zip(beats, beats[1:]):
        step = (b - a) / div
        for k in range(div):
            points.append(a + k * step)
    if beats:
        points.append(beats[-1])
    points.sort()
    return points


def snap_notes(model: Any, beats: Sequence[float],
               division: int = 4) -> Tuple[int, float]:
    """把音符的 `start` 吸到新格線上，長度跟著平移。

    回傳 (搬動了幾顆, 最大位移毫秒)。

    **只搬 `start`、長度不變**：使用者的問題是 start 被錯的格線吸歪，長度是另
    一回事（而且長度有自己的工具）。整首不做任何縮放，所以總長度不會變。

    ## 比格子還密的音串不各自吸附

    這首譜 54% 的相鄰起音間隔比一個 16 分格（75ms）還密，最密的只有 29ms
    （200 BPM 的 32 分是 37.5ms）。每顆各自吸到最近格點的話那些快速音會被壓
    在一起——實測不同的起音時刻從 1170 掉到 1090，使用者看到的是「很多高速音
    被貼在一起」。

    所以改成：**和前一組的間隔小於一格時，整串跟著前一組平移同一個量**，保留
    內部的相對時間。串的第一顆仍然吸到格線上，所以起點對拍、內部的快速感不會
    被壓平。這和 `ai_grid._run_starts` 處理滑音是同一招，只是這裡不限定音高
    單調（顫音、震音、跑動都要保住），改用「比格子密」當唯一條件。

    同一刻的音符（和弦）本來就該一起動，所以是按**原始起音時刻分組**之後
    整組套同一個位移。
    """
    if len(beats) < 2:
        return 0, 0.0
    points = grid_points(beats, division)
    if len(points) < 2:
        return 0, 0.0
    cell = min(b - a for a, b in zip(points, points[1:]))

    groups: dict = {}
    for note in model.notes_tree:
        groups.setdefault(int(note.start), []).append(note)

    moved = 0
    worst = 0.0
    prev_start = None
    prev_delta = 0
    for start in sorted(groups):
        if prev_start is not None and (start - prev_start) < cell:
            # 比一格還密：跟著前一組平移，保留相對時間
            delta = prev_delta
        else:
            i = bisect.bisect_left(points, float(start))
            best = None
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(points):
                    if best is None or abs(points[j] - start) < abs(best - start):
                        best = points[j]
            delta = 0 if best is None else int(round(best - start))
            # 位移超過半格就不吸。最近格點本來就不該比半格還遠——會發生是因為
            # 格線沒蓋到那裡（實測格線比曲末短 1 秒時，尾巴的音符被拉回最後一
            # 個格點、位移 272ms）。這一條同時擋掉「格線外」和「離太遠」，也把
            # 所有位移框在半格內。
            if abs(delta) > cell / 2.0 + 1:
                delta = 0
        prev_start, prev_delta = start, delta
        if delta == 0:
            continue
        for note in groups[start]:
            length = int(note.end) - int(note.start)
            note.start = int(note.start) + delta
            note.end = note.start + max(1, length)
            note.gate = max(1, note.end - note.start)
            moved += 1
        worst = max(worst, abs(float(delta)))
    return moved, worst


def apply(model: Any, item: Plan, snap: bool = True, division: int = 4) -> Plan:
    """照計畫覆寫格線，並（選擇性地）把音符吸附上去。"""
    if not item.ok:
        return item
    model.set_beat_grid_ms(list(item.beats))
    if snap:
        item.moved, item.max_shift_ms = snap_notes(model, item.beats, division)
    model.dirty = True
    return item


#: 重生小節線最多鋪幾個小節。曲庫裡最長的譜也只有幾百個小節。
MAX_REGEN_BARS = 4000
#: 合理的 BPM 範圍。`normalise` 只擋「一拍短到不合理」（MIN_BEAT_MS），擋不到
#: **太慢**——BPM 填 0.0001 的話一拍 10 分鐘、整條格線橫跨 27 天，而它只鋪一個
#: 小節，所以 MAX_REGEN_BARS 也攔不住。兩頭都要有界。
MIN_REGEN_BPM, MAX_REGEN_BPM = 20.0, 600.0


def regenerate(model: Any, bpm: float, numerator: int = 4,
               denominator: int = 4, start_ms: Optional[float] = None) -> Plan:
    """丟掉現在的小節線，照一個固定 BPM 重新鋪一條，**音符一顆都不動**。

    使用者要的「鎖定精確位置、重生小節線」。和這個模組原本的用法差在哪：

    ・`apply(..., snap=True)` 是「格線是對的，把音符搬上去」——AI 判錯速度、
      音符被吸歪的時候用。
    ・這裡是「音符是對的，格線是垃圾」——重算一條乾淨的格線套上去，音符的
      毫秒位置原封不動。所以**沒有吸附選項**，有的話就不是這個功能了。

    小節數自己算到蓋過最後一顆音符（`bars_to_cover`）。`start_ms` 不給就沿用
    原本的第一個拍點（`grid_start_ms`）——開頭那段導入空白是刻意的。
    """
    if start_ms is None:
        start_ms = grid_start_ms(model)
    # 一定要過 `normalise`：它擋掉「一拍短到不合理」的 BPM。直接自己建 Segment
    # 的話，BPM 填 0.0001 會鋪出一條一拍 10 分鐘、橫跨 27 天的格線（實測過）。
    if not MIN_REGEN_BPM <= float(bpm) <= MAX_REGEN_BPM:
        item = Plan(segments=[], start_ms=float(start_ms))
        item.reason = ('BPM 要在 %g 到 %g 之間（填了 %g）'
                       % (MIN_REGEN_BPM, MAX_REGEN_BPM, float(bpm)))
        return item
    probe = normalise([Segment(bars=1, bpm=float(bpm), numerator=int(numerator),
                               denominator=int(denominator))])
    if not probe:
        item = Plan(segments=[], start_ms=float(start_ms))
        item.reason = 'BPM 不合理（一拍至少要 %d ms）' % int(MIN_BEAT_MS)
        return item
    seg = probe[0]
    need = bars_to_cover(model, seg)
    if need > MAX_REGEN_BARS:
        item = Plan(segments=[], start_ms=float(start_ms))
        item.reason = ('這個速度要鋪 %d 個小節（上限 %d）——BPM 可能填太高了'
                       % (need, MAX_REGEN_BARS))
        return item
    whole = Segment(bars=max(1, need), bpm=seg.bpm,
                    numerator=seg.numerator, denominator=seg.denominator)
    item = Plan(segments=[whole], start_ms=float(start_ms))
    item.beats = build_beats([whole], item.start_ms)
    item.grid_end_ms = item.beats[-1] if item.beats else item.start_ms
    item.bars = whole.bars
    item.needed_ms = song_end_ms(model)
    item.ok = len(item.beats) >= 2
    if not item.ok:
        item.reason = 'BPM 太低或譜面太短，鋪不出格線'
    return item


def bpm_from_span(span_ms: float, bars: float, numerator: int = 4,
                  denominator: int = 4) -> Optional[float]:
    """「這一段是 N 個小節」→ BPM。編曲軟體的 set tempo from selection。

    Logic 的「Adjust Tempo using…」、Reaper 的「Set project tempo from time
    selection」、Cubase 的 Time Warp 都是同一招：素材是對的，你知道它是幾小節，
    速度讓軟體算。對我們來說它正好是「鎖定精確位置」那條路——音符不動，格線
    去遷就它。

    `bar_ms = numerator × 4 × 60000 ÷ (denominator × bpm)`，反解即可。
    """
    span = float(span_ms)
    count = float(bars)
    if span <= 0 or count <= 0:
        return None
    bar_ms = span / count
    bpm = int(numerator) * 4.0 * 60000.0 / (max(1, int(denominator)) * bar_ms)
    if not MIN_REGEN_BPM <= bpm <= MAX_REGEN_BPM:
        return None
    return bpm


def detect_bpm(model: Any, numerator: int = 4) -> Optional[float]:
    """從譜面自己的音符起音量一個 BPM 出來。量不到回 None。

    用在「重生小節線」的自動偵測。注意這是拿**譜面上的**音符量的，如果那些
    音符已經被錯的格線吸附過，量到的就會是那個錯誤（見模組說明裡
    `detect_segments` 的警告）——所以對話框要講清楚，原始的 .ai.mid 才是乾淨的。
    """
    onsets = sorted({float(n.start) for n in model.notes_tree})
    if len(onsets) < DETECT_MIN_NOTES:
        return None
    segs = detect_segments(onsets, numerator=numerator)
    if not segs:
        return None
    # 取涵蓋最多小節的那一段：變速譜上也拿得到主速度
    return float(max(segs, key=lambda s: s.bars).bpm)


def describe(item: Plan) -> str:
    """給使用者看的摘要。"""
    if not item.ok:
        return '沒有可以套用的段落：%s' % (item.reason or '不明')
    lines = ['共 %d 個小節、%d 個拍點，從 %.0f ms 鋪到 %.0f ms'
             % (item.bars, len(item.beats), item.start_ms, item.grid_end_ms)]
    for i, seg in enumerate(item.segments, 1):
        lines.append('  %d. %d 小節 × %d/%d @ %.2f BPM ＝ %.0f ms'
                     % (i, seg.bars, seg.numerator, seg.denominator,
                        seg.bpm, seg.total_ms))
    gap = item.short_by_ms
    last_bar = item.segments[-1].bar_ms if item.segments else 0.0
    if abs(gap) < 1:
        lines.append('格線剛好蓋到最後一顆音符。')
    elif gap > 0:
        extra = (' 約 %.1f 個小節' % (gap / last_bar)) if last_bar > 0 else ''
        lines.append('⚠ 格線比最後一顆音符早結束 %.0f ms（還差%s）——'
                     '後面那段沒有小節線。' % (gap, extra))
    else:
        extra = (' 約 %.1f 個小節' % (-gap / last_bar)) if last_bar > 0 else ''
        lines.append('格線比最後一顆音符多鋪了 %.0f ms（%s），多出來的小節是空的。'
                     % (-gap, extra.strip()))
    if item.moved:
        lines.append('吸附了 %d 顆音符，最大位移 %.0f ms。'
                     % (item.moved, item.max_shift_ms))
    return '\n'.join(lines)
