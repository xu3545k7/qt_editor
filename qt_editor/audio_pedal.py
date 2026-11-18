# -*- coding: utf-8 -*-
"""以音檔補上踏板：AI 轉譜，但只把 CC64 搬到譜面上，其餘一律不動。

使用者：「可以在踏板不合需求或 midi 沒有踏板的情況下，作為一個工具……
基本上就是轉 midi，但是只把踏板拿到譜面上，其餘都不要動。」

**為什麼需要這條路。** 譜面的踏板本來有兩個來源：來源 MIDI 的 CC64
（`NoteModel.apply_midi_expression_from_source`），和依和聲猜的
`generate_pedal.py`。前者最可信但常常根本不存在——曲庫裡 32 份譜面的來源
MIDI 連一個 CC64 事件都沒有，因為那些是照譜打的 transcription；後者是猜的，
召回 75%／精確 64%。音檔是第三個來源，而且是**唯一真的聽過那場演奏**的那個：
錄音裡的延音是演奏者實際踩出來的。

## 這支只做兩件事

1. 從 AI 轉出的 MIDI 讀 CC64（音符一顆都不要）。
2. 把那些毫秒從**音檔的時間軸**換算到**譜面的時間軸**。

第二件事才是真正的工作。轉出來的 MIDI 時間軸是音檔本身的時間，而譜面不是：
它可能補過開頭的靜音（`audio_lead_in`）、可能吸附到小節格上、可能整份是手畫
的。差個幾百毫秒，踏板就踩在錯的和聲上——而那在畫面上完全看不出來，只有聽
才會發現整首糊掉。所以對位要自己量，不能假設是 0。

## 對位怎麼做

和 `models._choose_pairing` 不同，這裡**不比對音高**。那一套的前提是「來源
MIDI 是這份譜面的祖先」，每顆音都該對得上；AI 轉出來的音符是從音訊猜的，
起音 F1 97%、音長只有 57%，拿音高去配會被那 3% 牽著走。這裡要的其實只有一
個數字（兩條時間軸差多少），所以直接量**起音時間的分布**：

* 粗對位：所有「譜面起音 − 音檔起音」丟進直方圖，取最高的那一格。真正的位移
  會讓好幾千顆音同時落在同一格，背景雜訊堆不出那種高度。
* 細對位：用粗位移找最近鄰，取差值的**中位數**（不是平均——配錯的那幾顆
  可以差好幾秒，平均會被拉走）。
* 漂移：每 10 秒取一個錨點，兩錨點之間線性內插。譜面是固定 BPM 而錄音不是
  的時候，整首的位移會慢慢跑掉，單一個常數接不住。

對位品質要**報出去**。這套一定湊得出一個答案，選錯音檔也一樣——擋下來的不是
演算法，是使用者看到「吻合率 12%」。

這支不碰 Qt，也不碰 NoteModel：吃的是數字，吐的是數字，好測。
"""

import bisect
from typing import Dict, List, Optional, Sequence, Tuple

#: 粗對位的直方圖格寬（毫秒）。比這更細就被量化誤差打散，更粗就分不出來。
BIN_MS = 20
#: 粗對位往前後找多遠。開頭補一個小節的靜音大約 2 秒，8 秒很有餘裕。
SEARCH_MS = 8000
#: 挑錨點用的容差。吸附過的譜面本身就會偏半個十六分音符（160 BPM 下 ±47ms），
#: 所以不能設得像力度配對那樣只有 10ms。
MATCH_MS = 80
#: 判斷「是不是同一首歌」用的容差。見 `Alignment.confidence`。
CONFIDENCE_MS = 25
#: 信心值低於這條就該警告使用者。見 `Alignment.confidence`。
MIN_CONFIDENCE = 0.70
#: 粗對位最多取幾顆譜面音符。全取是 O(N×M)，取樣之後峰值一樣清楚。
SAMPLE_CAP = 1200
#: 每隔多久取一個漂移錨點。
ANCHOR_WINDOW_MS = 10000
#: 一個錨點視窗至少要有幾顆對得上的音，否則那一段不設錨點。
MIN_ANCHOR_HITS = 8
#: 一個視窗裡的位移要多一致才算數（中位絕對差）。見 `_anchors`。
MAX_ANCHOR_SPREAD_MS = 20
#: 錨點最多重算幾輪。一輪往兩頭各長出大約一個視窗，所以要夠一首歌的長度；
#: 配不到更多就提早收工，沒有漂移的曲子實際只跑兩輪。見 `align` 的第 ④ 步。
REFINE_PASSES = 24
#: 短於這個長度的踏板段落丟掉。見 `tidy_spans`。
MIN_SPAN_MS = 60
#: 偵測到的「踩下」一律晚這麼多毫秒，要往前推回去。見 `unlag_presses`。
PRESS_LAG_MS = 120
#: 換踏時至少留下的間隔。見 `unlag_presses`。
MIN_LIFT_MS = 1

#: 併進既有踏板的方式。
REPLACE = 'replace'
FILL = 'fill'


class Alignment(object):
    """兩條時間軸的關係：加上 `offset_ms` 就是從音檔時間換到譜面時間。

    `anchors` 是 (譜面毫秒, 音檔毫秒) 的遞增序列，用來接住整首慢慢跑掉的
    漂移；少於兩個錨點時退回單一個位移。
    """

    def __init__(self, offset_ms: float = 0.0, match_ratio: float = 0.0,
                 matched: int = 0, total: int = 0, drift_ms: float = 0.0,
                 anchors: Optional[List[Tuple[int, int]]] = None,
                 source_onsets: int = 0, confidence: float = 0.0):
        self.offset_ms = float(offset_ms)
        self.match_ratio = float(match_ratio)
        self.matched = int(matched)
        self.total = int(total)
        self.drift_ms = float(drift_ms)
        self.anchors = list(anchors or ())
        self.source_onsets = int(source_onsets)
        #: 把容差收到 `CONFIDENCE_MS` 之後還對得上的比例——「是不是同一首歌」。
        #:
        #: `match_ratio` 回答不了這個問題：在 ±80ms 下拿別首歌的音檔去對，
        #: 實測照樣有 41–92% 的吻合率（音符夠密就一定撞得到）。容差收到
        #: ±25ms 之後才分得開——6×6 的交叉實測裡，對的 98–100%、錯的
        #: 16–48%，中間空了 50 個百分點。
        self.confidence = float(confidence)

    @property
    def usable(self) -> bool:
        """有沒有量到東西。品質好不好是另一回事，交給使用者看信心值決定。"""
        return self.total > 0 and self.matched > 0

    @property
    def trustworthy(self) -> bool:
        """像不像同一首歌。假的時候**警告**而不是擋下來——吸附到格線上的
        譜面本來就會偏，那種偏法是合法的，只有使用者知道。"""
        return self.confidence >= MIN_CONFIDENCE

    def __repr__(self) -> str:                                  # pragma: no cover
        return ('Alignment(offset=%+.0fms, match=%.1f%%, confidence=%.1f%% (%d/%d), '
                'drift=%.0fms, anchors=%d)'
                % (self.offset_ms, self.match_ratio * 100, self.confidence * 100,
                   self.matched, self.total, self.drift_ms, len(self.anchors)))


class Report(object):
    """一次「以音檔補踏板」的結果，給對話框照著講。"""

    def __init__(self, spans: List[List[float]], alignment: Alignment,
                 source_spans: int = 0, dropped_short: int = 0,
                 kept_existing: int = 0, coverage: float = 0.0,
                 median_ms: float = 0.0):
        self.spans = spans
        self.alignment = alignment
        self.source_spans = int(source_spans)
        self.dropped_short = int(dropped_short)
        self.kept_existing = int(kept_existing)
        self.coverage = float(coverage)
        self.median_ms = float(median_ms)

    def __len__(self) -> int:
        return len(self.spans)


# ── 讀檔 ──────────────────────────────────────────────────────────────

def read_midi(path: str) -> Tuple[List[float], List[List[float]]]:
    """AI 轉出的 MIDI → (起音毫秒串列, 踏板段落毫秒串列)。

    用 `ai_grid.read_ai_midi` 而不是 `NoteModel.load_midi`：這裡要的是原始的
    時間與 CC64，排譜、分手、鍵道那一整套跑了也用不到，而那才是匯入裡真正
    花時間的部分。
    """
    from .ai_grid import read_ai_midi

    notes, pedals = read_ai_midi(path)
    onsets = [float(start) * 1000.0 for start, _end, _pitch, _vel in notes]
    spans = [[float(a) * 1000.0, float(b) * 1000.0] for a, b in pedals]
    return onsets, spans


# ── 對位 ──────────────────────────────────────────────────────────────

def _nearest(values: Sequence[int], when: float) -> Optional[int]:
    """`values` 裡離 `when` 最近的那個。空的話回 None。"""
    if not values:
        return None
    index = bisect.bisect_left(values, when)
    best = None
    for candidate in values[max(0, index - 1):index + 2]:
        if best is None or abs(candidate - when) < abs(best - when):
            best = candidate
    return best


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _tidy(onsets: Sequence[float]) -> List[int]:
    """起音時間正規化成遞增、去重的整數毫秒。

    同一個時間點的和弦有好幾顆音，但對位問的是「這個時間點在不在」，
    重複的只會把直方圖的峰值灌水、讓密集的段落壓過稀疏的段落。
    """
    return sorted(set(int(round(float(t))) for t in onsets))


def align(chart_onsets: Sequence[float], source_onsets: Sequence[float],
          search_ms: int = SEARCH_MS, tolerance_ms: int = MATCH_MS) -> Alignment:
    """量出「音檔時間 → 譜面時間」要加多少。"""
    chart = _tidy(chart_onsets)
    source = _tidy(source_onsets)
    if not chart or not source:
        return Alignment(total=len(chart), source_onsets=len(source))

    # ① 粗對位：差值直方圖的峰值。左右各併一格，免得真正的位移剛好跨在格線上。
    step = max(1, len(chart) // SAMPLE_CAP)
    hist: Dict[int, int] = {}
    for when in chart[::step]:
        lo = bisect.bisect_left(source, when - search_ms)
        hi = bisect.bisect_right(source, when + search_ms)
        for other in source[lo:hi]:
            key = (when - other) // BIN_MS
            hist[key] = hist.get(key, 0) + 1
    if not hist:
        return Alignment(total=len(chart), source_onsets=len(source))
    peak = max(hist, key=lambda k: hist.get(k, 0) + hist.get(k - 1, 0) + hist.get(k + 1, 0))
    coarse = peak * BIN_MS + BIN_MS / 2.0

    # ② 細對位：用粗位移找最近鄰，取差值的中位數。只收粗位移附近的，
    #    不然整首對不上的那些會把中位數也拖走。
    near = [float(when - other)
            for when, other in _pairs(chart, source, _flat(coarse), BIN_MS * 2)]
    offset = _median(near) if near else coarse

    # ③ 用最終位移重新配對、取錨點。
    predict = _flat(offset)
    pairs = _pairs(chart, source, predict, tolerance_ms)
    anchors = _anchors(pairs)

    # ④ 再用錨點當預測重跑，直到配不到更多為止。
    #
    #    單一個常數只接得住 ±`tolerance_ms` 的漂移。譜面是固定 BPM 而錄音
    #    不是的時候，整首可以跑掉好幾百毫秒——那時第一輪只有中間某一段對得
    #    上，兩頭整片落在容差外。可是**對得上的那一段已經把彎的地方量出來
    #    了**，拿它外推就能再多接住一個視窗，一輪一輪往兩頭長出去。
    #
    #    **配對數沒有成長時也要採用錨點版的預測**，只是不再跑下一輪。先前
    #    這裡一停就把 `predict` 留在常數版，於是一首漂移 145ms 但每顆音都對
    #    得上的曲子（第一輪就滿分，所以第二輪「沒有成長」）信心值只剩 34%
    #    ——對位明明是準的（殘差 p90 僅 1.2ms），報出來的數字卻在亂叫。
    for _ in range(REFINE_PASSES):
        if len(anchors) < 2:
            break
        candidate = _predict(anchors, offset)
        grown = _pairs(chart, source, candidate, tolerance_ms)
        if len(grown) < len(pairs):
            break                           # 退步了：留著上一輪的結果
        improved = len(grown) > len(pairs)
        predict, pairs, anchors = candidate, grown, _anchors(grown)
        if not improved:
            break

    deltas = [c - s for c, s in pairs]
    drift = (max(deltas) - min(deltas)) if deltas else 0.0
    # 信心值用更緊的容差，但用同一個預測——問的是「對齊之後貼得準不準」，
    # 不是「有沒有漂移」。
    tight = _pairs(chart, source, predict, min(CONFIDENCE_MS, tolerance_ms))
    return Alignment(offset_ms=offset,
                     match_ratio=len(pairs) / float(len(chart)),
                     matched=len(pairs), total=len(chart),
                     drift_ms=drift, anchors=anchors,
                     source_onsets=len(source),
                     confidence=len(tight) / float(len(chart)))


def _flat(offset: float):
    """最簡單的預測：整首平移一個常數。"""
    return lambda when: when - offset


def _predict(anchors: Sequence[Tuple[int, int]], offset: float):
    """用錨點預測「這個譜面時間對應到音檔的什麼時候」。

    是 `remap` 的反方向：那邊把踏板從音檔搬到譜面，這邊是配對時要知道該去
    音檔的哪裡找。兩頭之外沿用最近那個錨點的位移量。
    """
    if len(anchors) < 2:
        return _flat(offset)
    charts = [c for c, _s in anchors]

    def predict(when: float) -> float:
        if when <= charts[0]:
            return when - (anchors[0][0] - anchors[0][1])
        if when >= charts[-1]:
            return when - (anchors[-1][0] - anchors[-1][1])
        i = bisect.bisect_right(charts, when) - 1
        c0, s0 = anchors[i]
        c1, s1 = anchors[i + 1]
        if c1 == c0:
            return float(s0)
        return s0 + (s1 - s0) * (when - c0) / float(c1 - c0)

    return predict


def _pairs(chart: Sequence[int], source: Sequence[int], predict,
           tolerance: float) -> List[Tuple[int, int]]:
    """這個預測之下對得上的 (譜面毫秒, 音檔毫秒)。"""
    out: List[Tuple[int, int]] = []
    for when in chart:
        want = predict(when)
        other = _nearest(source, want)
        if other is None:
            continue
        if abs(other - want) <= tolerance:
            out.append((when, other))
    return out


def _anchors(pairs: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """每 `ANCHOR_WINDOW_MS` 挑一個代表點，當作分段線性的錨點。

    挑的是「位移最接近這個視窗中位數」的那一對**真實**音符，不是算出來的
    平均位置：錨點落在真的有音符的地方，內插才不會在空白處亂外推。

    **一個視窗裡的配對必須彼此同意，才配得上一個錨點。** 這條不是潔癖：
    位移量錯了的時候，每顆音還是會找到「某一顆」容差內的音檔音符，那些配
    對的位移各說各話，散在整個 ±`MATCH_MS` 裡；真正對上的時候它們會擠在幾
    毫秒內。沒有這道檢查，一個巧合湊出來的錨點會**鎖死**那一段——下一輪用
    它去預測，又配到同一批巧合，中位數還是它，永遠修不回來（實測一首會漂
    移的曲子因此只有 58% 對得上，而且前段的錨點整片是錯的）。
    """
    if not pairs:
        return []
    buckets: Dict[int, List[Tuple[int, int]]] = {}
    for chart_ms, source_ms in pairs:
        buckets.setdefault(int(chart_ms // ANCHOR_WINDOW_MS), []).append((chart_ms, source_ms))

    picked: List[Tuple[int, int]] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        if len(bucket) < MIN_ANCHOR_HITS:
            continue
        deltas = [c - s for c, s in bucket]
        mid = _median(deltas)
        if _median([abs(d - mid) for d in deltas]) > MAX_ANCHOR_SPREAD_MS:
            continue
        picked.append(min(bucket, key=lambda p: abs((p[0] - p[1]) - mid)))

    # 兩邊都必須嚴格遞增，內插才有意義（和 `NoteModel._build_time_map` 同一條）。
    anchors: List[Tuple[int, int]] = []
    for chart_ms, source_ms in picked:
        if anchors and (chart_ms <= anchors[-1][0] or source_ms <= anchors[-1][1]):
            continue
        anchors.append((chart_ms, source_ms))
    return anchors


def remap(spans: Sequence[Sequence[float]], alignment: Alignment) -> List[List[float]]:
    """把音檔時間軸上的踏板換算到譜面時間軸。

    錨點之間線性內插，頭尾之外沿用最近那個錨點的位移量。錨點不足兩個時整份
    平移 `offset_ms`。

    （`NoteModel._remap_times_to_chart` 做的是同一件數學，但錨點是從音高配對
    來的；這裡的錨點是從起音分布量的，兩邊的前提不同，所以各自保留。）
    """
    anchors = alignment.anchors
    if len(anchors) < 2:
        shift = alignment.offset_ms
        return [[float(a) + shift, float(b) + shift] for a, b in spans]

    sources = [s for _c, s in anchors]

    def convert(value: float) -> float:
        if value <= sources[0]:
            return value + (anchors[0][0] - anchors[0][1])
        if value >= sources[-1]:
            return value + (anchors[-1][0] - anchors[-1][1])
        i = bisect.bisect_right(sources, value) - 1
        s0, c0 = anchors[i][1], anchors[i][0]
        s1, c1 = anchors[i + 1][1], anchors[i + 1][0]
        if s1 == s0:
            return float(c0)
        return c0 + (c1 - c0) * (value - s0) / (s1 - s0)

    return [[convert(float(a)), convert(float(b))] for a, b in spans]


# ── 踩下的延遲 ────────────────────────────────────────────────────────

def unlag_presses(spans: Sequence[Sequence[float]],
                  lag_ms: float = PRESS_LAG_MS,
                  min_lift_ms: float = MIN_LIFT_MS) -> List[List[float]]:
    """把每一段的**踩下**往前推 `lag_ms`，放開一個都不動。

    **為什麼只動踩下。** 量自 8 首有真 CC64 的譜面（拿它們的來源踏板當標準
    答案，比對從同一首的鋼琴分軌轉出來的 CC64）：

        放開的誤差中位是 **+4 ms**（各首 −9 ～ +18）
        踩下的誤差中位是 **+127 ms**（各首 +74 ～ +258）

    這不是模型沒做好，是物理：放開踏板的瞬間整片共鳴一起停，那是聽得出來
    的事件；而**踩下踏板本身不發出任何聲音**——要等到有音符被延長，模型才
    聽得到證據，所以偵測到的踩下一定落在真正踩下之後。

    推回去多少：全域掃描 0～260ms，最高分在 **120 ms**（踩點召回 59%→71%、
    精確 67%→87%）。每首自己最好的常數在 50～300 之間跑，但用「踩下 − 前一次
    放開」的低百分位去估每首的延遲**比固定常數還差**（67/88），所以不估。

    推過頭會撞到前一次放開，這時只留 `min_lift_ms`。那不是妥協而是真相：
    真人的踏板資料本身就有大量 0～1ms 的間隔（Marigold 的 152 段裡有 28 個、
    this_game 的 176 段裡有 29 個），延音踏板的換踏本來就是「放開的同一瞬間
    再踩下去」。留 1ms 而不是 0，是因為 `_normalise_pedal_spans` 只在真的重疊
    時才合併，1ms 就足以讓兩次踩踏維持成兩段。
    """
    out: List[List[float]] = []
    previous_release: Optional[float] = None
    for span in spans:
        start, end = float(span[0]), float(span[1])
        moved = start - float(lag_ms)
        if previous_release is not None:
            moved = max(moved, previous_release + float(min_lift_ms))
        # 推到比自己的放開還晚就沒有意義了，至少留 1ms 的長度。
        out.append([min(moved, end - 1.0), end])
        previous_release = end
    return out


# ── 整理與併入 ────────────────────────────────────────────────────────

def tidy_spans(spans: Sequence[Sequence[float]],
               min_span_ms: float = MIN_SPAN_MS) -> Tuple[List[List[float]], int]:
    """排序、合併重疊、丟掉太短的。回傳 (段落, 丟掉幾段)。

    **太短的要丟。** 從音訊偵測踏板會在真正的踩放之間冒出零星幾毫秒的段落
    （模型對某一幀的猶豫），那些在遊戲裡是一次「踩了又放」——玩家聽得到、
    `PlayablePedal` 的 merge 層還得替它想辦法。真人換踏板只有 0.9% 短於
    300ms（量自 83 份有真 CC64 的譜面），所以短到幾十毫秒的一定是雜訊。
    門檻沒有訂在 300ms，是因為那會連真的快速點踏一起殺掉。

    合併用的規則和 `NoteModel._normalise_pedal_spans` 一樣：**只有真的重疊
    才合併**，間隔 0 代表「這裡要換踏」而不是「一直踩著」。
    """
    clean: List[List[float]] = []
    dropped = 0
    for span in spans:
        lo, hi = float(span[0]), float(span[1])
        if hi < lo:
            lo, hi = hi, lo
        if hi - lo < max(1.0, float(min_span_ms)):
            dropped += 1
            continue
        clean.append([lo, hi])
    clean.sort(key=lambda sp: sp[0])

    merged: List[List[float]] = []
    for lo, hi in clean:
        if merged and lo < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged, dropped


def clamp(spans: Sequence[Sequence[float]], end_ms: float) -> List[List[float]]:
    """收掉落在譜面之外的部分（負的時間、超過最後一顆音的尾巴）。

    音檔常常比譜面長（尾奏、淡出），而換算過去的踏板會跟著跑到譜面結束之後。
    留著不會響，但會讓「覆蓋率」這個數字騙人。
    """
    out: List[List[float]] = []
    limit = float(end_ms)
    for lo, hi in spans:
        lo = max(0.0, float(lo))
        hi = min(limit, float(hi))
        if hi - lo >= 1.0:
            out.append([lo, hi])
    return out


def merge_into(existing: Sequence[Sequence[float]],
               incoming: Sequence[Sequence[float]],
               mode: str = REPLACE) -> Tuple[List[List[float]], int]:
    """把新的踏板併進既有的。回傳 (結果, 保住幾段既有的)。

    `FILL` 走的是 `generate_pedal.fill_gaps` **同一個函式**：真人踩過的地方
    一格都不動，只補它沒蓋到的空隙。那是給「原本的踏板是真的，但只踩了一
    小段」的譜面用的（Testify 的 28 段只覆蓋 21%）。
    """
    if mode != FILL or not existing:
        return ([[float(a), float(b)] for a, b in incoming], 0)

    from generate_pedal import fill_gaps          # noqa: PLC0415  打包後才上 sys.path
    out = fill_gaps(existing, incoming)
    return ([[float(a), float(b)] for a, b in out], len(existing))


# ── 一次做完 ──────────────────────────────────────────────────────────

def build(chart_onsets: Sequence[float], chart_end_ms: float,
          existing: Sequence[Sequence[float]],
          source_onsets: Sequence[float], source_spans: Sequence[Sequence[float]],
          mode: str = REPLACE, min_span_ms: float = MIN_SPAN_MS,
          press_lag_ms: float = PRESS_LAG_MS) -> Report:
    """對位 → 換算 → 補回踩下的延遲 → 整理 → 併入，一次做完。"""
    alignment = align(chart_onsets, source_onsets)
    # 順序有意義：**先**丟雜訊再補踩下的延遲。反過來的話，一段 5ms 的雜訊
    # 被往前推 120ms 就變成 125ms，剛好躲過長度濾網。
    tidied, dropped = tidy_spans(remap(source_spans, alignment), min_span_ms)
    tidied = clamp(unlag_presses(tidied, press_lag_ms), chart_end_ms)
    final, kept = merge_into(existing, tidied, mode)
    final, _more = tidy_spans(final, 1.0)       # 補空隙可能貼出新的相鄰段

    lengths = sorted(b - a for a, b in final)
    coverage = (sum(b - a for a, b in final) / float(chart_end_ms)) if chart_end_ms > 0 else 0.0
    return Report(spans=final, alignment=alignment,
                  source_spans=len(source_spans), dropped_short=dropped,
                  kept_existing=kept, coverage=coverage,
                  median_ms=(lengths[len(lengths) // 2] if lengths else 0.0))
