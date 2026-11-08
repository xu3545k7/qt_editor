# -*- coding: utf-8 -*-
"""拍線間距的體檢：量化之前先知道這份譜的格線能不能信。

量化的做法是「把相鄰兩條拍線之間切成 N 等分」（`ChartView.quantize_notes`），
所以結果完全取決於拍線本身的間距：

・間距均勻   → 子格線等寬，量化出來的音符間隔也等長。
・間距不均勻 → 每一段被切成**寬度不同**的子格線。音符落上去之後間隔長短不一，
               而它們確實都在格線上，於是再按一次量化就回報「本來就在格線上」。

使用者回報的就是後者：「有的音符稍微偏離節拍線、有的時間靠得比較近，但製譜器
說全部量化好了」。那句話沒說錯，只是它比對的是製譜器自己算出來的格線，不是
畫面上的節拍線——這個模組就是用來把這件事先講出來。

## 量過整個曲庫（112 份譜）之後分三類

  ・68 份完全等距      → 量化放心用
  ・8 份只有平滑起伏   → 真的彈性速度。拍線本來就該動，照它切分是對的，
                         對齊後各段的毫秒間隔不一樣長也是正確的
  ・36 份有孤立尖刺    → 漏了拍點（間距是鄰居的兩倍）或多了一條（一半），
                         格線在那裡斷掉

另外 15 份的間距中位數對不上宣告的 BPM（`旅` 誤差 17.8%、`Winter Wind` 11.1%），
整個格線在錯的速度上。

「平滑起伏」和「孤立尖刺」要分開看，因為處理方式相反：前者不能動，後者要補
拍點。分法是拿每個間距和**左右鄰居的中位數**比——取中位數所以單一個尖刺不會
把自己的基準拉高。實測漏一筆的指紋是 ×2.00、多一筆是 ×0.50，而真的彈性速度
最多到 ×1.3，所以 1.6／0.65 這兩條線把它們分得很開。

這一層刻意不綁 PyQt：iPad 版也要用（見 `test_core_is_portable.py`）。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, List, Sequence

#: 間距是鄰居的幾倍才算「漏了拍點」。
SPIKE_HIGH = 1.6
#: 間距只有鄰居的幾分之幾才算「多了一條拍線」。
SPIKE_LOW = 0.65
#: 平滑起伏的門檻：超過就不算等距，但還不到尖刺。
ROUGH = 0.06
#: 間距中位數對宣告 BPM 的容許相對誤差。
BPM_FIT = 0.03
#: 起伏超過這個百分比就不比對宣告的 BPM。速度真的在變的譜，間距中位數不代表
#: 任何一段的速度，拿它去對檔頭那個標示只會報出一條沒有意義的警告。那種譜的
#: 問題（如果有）會以尖刺的形式被抓到。
STEADY_ROUGH = 25.0
#: 判斷尖刺時左右各看幾個鄰居。
NEIGHBOURS = 4
#: 低於這個條數就不下結論——樣本太少，鄰居中位數沒有意義。
MIN_LINES = 8

#: 拍線間距可以是「一拍」的幾倍。涵蓋兩小節一筆（8）到三十二分一筆（0.125）。
SIMPLE_RATIOS = (8.0, 6.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 2.0 / 3.0,
                 0.5, 1.0 / 3.0, 0.25, 1.0 / 6.0, 0.125)


def fmt_ms(ms: float) -> str:
    """毫秒 → 分:秒.毫秒，給使用者指位置用。"""
    ms = max(0, int(round(ms)))
    return '%d:%02d.%03d' % (ms // 60000, (ms // 1000) % 60, ms % 1000)


@dataclass
class Spike:
    """一個孤立的異常間距。"""

    index: int          #: 第幾個間距（對整份譜的間距序列，0 起）
    at_ms: int          #: 這個間距左端那條拍線的時間
    to_ms: int          #: 右端那條
    gap: int            #: 這個間距幾毫秒
    local: float        #: 左右鄰居的間距中位數
    ratio: float        #: gap / local
    #: 併過的尖刺要明講自己是哪一種：兩個短間距併起來之後 `gap` 約等於 `local`，
    #: 從 `ratio` 推會翻成「漏了拍點」，正好相反。
    forced_kind: str = ''

    @property
    def kind(self) -> str:
        """`'missing'`＝間距太大（漏了拍點）、`'extra'`＝太小（多了一條）。"""
        if self.forced_kind:
            return self.forced_kind
        return 'missing' if self.ratio >= 1.0 else 'extra'

    @property
    def where(self) -> int:
        """叫使用者去看哪個時間點。

        間距太小是因為右端那條線是多出來的（插進來的那條把原本的間距切成
        一小段 + 一大段，被標記的是前面那一小段），所以要指右端。間距太大
        則是漏掉的線落在這一段**裡面**，指左端讓使用者從那裡往後找。
        """
        return self.to_ms if self.kind == 'extra' else self.at_ms


@dataclass
class GridReport:
    """一份譜的拍線體檢結果。"""

    lines: int = 0                          #: 幾條拍線
    gaps: int = 0                            #: 納入判斷的間距數
    median_gap: float = 0.0
    spikes: List[Spike] = field(default_factory=list)
    rough: int = 0                           #: 平滑起伏的間距數
    bpm: float = 0.0
    bpm_ratio: float = 0.0                   #: 間距中位數 ÷ 一拍
    bpm_fit: float = 0.0                     #: 對最近的簡單倍數的相對誤差
    enough: bool = False                     #: 樣本夠不夠下結論

    # ── 判斷 ──────────────────────────────────────────────────────────
    @property
    def missing(self) -> List[Spike]:
        return [s for s in self.spikes if s.kind == 'missing']

    @property
    def extra(self) -> List[Spike]:
        return [s for s in self.spikes if s.kind == 'extra']

    @property
    def bpm_mismatch(self) -> bool:
        """間距中位數對不上宣告的 BPM。只在格線穩定時才下這個結論。"""
        return (self.enough and self.bpm > 0 and self.bpm_fit > BPM_FIT
                and self.rough_pct <= STEADY_ROUGH)

    @property
    def rough_pct(self) -> float:
        return 100.0 * self.rough / self.gaps if self.gaps else 0.0

    @property
    def rubato(self) -> bool:
        """只有平滑起伏、沒有尖刺——真的速度變化，不是壞掉。"""
        return self.enough and not self.spikes and self.rough > 0

    @property
    def trustworthy(self) -> bool:
        """量化的結果能不能照字面相信（彈性速度算可以）。"""
        return not self.spikes and not self.bpm_mismatch

    @property
    def even(self) -> bool:
        """完全等距：量化後的毫秒間隔一定等長。"""
        return self.trustworthy and self.rough == 0

    # ── 給使用者看的文字 ──────────────────────────────────────────────
    def warnings(self) -> List[str]:
        """真的有問題才回傳；沒問題回空的。"""
        out: List[str] = []
        if not self.enough:
            return out
        miss, ext = self.missing, self.extra
        if miss:
            avg = statistics.median([s.ratio for s in miss])
            out.append(
                '有 %d 處拍線間距是鄰居的 %.1f 倍左右，看起來是漏了拍點，'
                '格線在那裡會斷掉（第一處在 %s）。那幾段會被切成過寬的子格線，'
                '量化後的音符間隔就會比別處長。'
                % (len(miss), avg, fmt_ms(miss[0].where)))
        if ext:
            avg = statistics.median([s.ratio for s in ext])
            out.append(
                '有 %d 處多出一條拍線（間距只有鄰居的 %.0f%%，第一處在 %s）。'
                '那一小段會被切得過細，音符會被吸到不該去的位置。'
                % (len(ext), 100.0 * avg, fmt_ms(ext[0].where)))
        if self.bpm_mismatch:
            out.append(
                '拍線間距（中位 %.0f ms）對不上宣告的 BPM %.4g：一拍是 %.1f ms，'
                '間距相當於 %.2f 拍，不是整數也不是常見的分數。整個格線可能'
                '在錯的速度上。'
                % (self.median_gap, self.bpm, 60000.0 / self.bpm,
                   self.bpm_ratio))
        return out

    def notes(self) -> List[str]:
        """不是警告，但量化前該知道的事。"""
        if not self.enough or self.spikes:
            return []
        if self.rough:
            return ['拍線間距有 %.0f%% 的起伏，看起來是真的速度變化。量化會照'
                    '每一段的實際寬度切分（這是對的），所以對齊之後各段的毫秒'
                    '間隔本來就不會一樣長。' % self.rough_pct]
        return []

    def summary(self) -> str:
        """一行狀態，給對話框當標題用。"""
        if not self.enough:
            return '拍線太少（%d 條），不足以判斷格線是否均勻。' % self.lines
        head = '拍線 %d 條，間距中位 %.0f ms' % (self.lines, self.median_gap)
        if self.spikes:
            return '%s；有 %d 處斷點。' % (head, len(self.spikes))
        if self.bpm_mismatch:
            return '%s；對不上宣告的 BPM。' % head
        if self.rough:
            return '%s；起伏 %.0f%%（速度變化）。' % (head, self.rough_pct)
        return '%s；完全等距。' % head


def _spikes_of(gaps: Sequence[int], lines: Sequence[int],
               offset: int) -> List[Spike]:
    """拿每個間距和左右鄰居的中位數比，挑出孤立的異常。

    `gaps` 是已經去掉頭尾的那一段，`offset` 是它在整份譜的間距序列裡從第幾個
    開始，用來把位置換回真正的拍線時間。
    """
    out: List[Spike] = []
    for i, g in enumerate(gaps):
        lo = max(0, i - NEIGHBOURS)
        hi = min(len(gaps), i + NEIGHBOURS + 1)
        nb = [gaps[j] for j in range(lo, hi) if j != i]
        if not nb:
            continue
        local = float(statistics.median(nb))
        if local <= 0:
            continue
        ratio = g / local
        if ratio >= SPIKE_HIGH or ratio <= SPIKE_LOW:
            k = i + offset
            out.append(Spike(index=k, at_ms=int(lines[k]),
                             to_ms=int(lines[k + 1]), gap=int(g),
                             local=local, ratio=ratio))
    return _merge_extras(out, lines)


def _merge_extras(spikes: List[Spike], lines: Sequence[int]) -> List[Spike]:
    """把「同一條多餘拍線造成的兩個短間距」併成一筆。

    一條線插在間距正中間，會把一個 500ms 的間距切成 250 + 250——兩個都短、
    兩個都被標記，於是報成「有 2 處多出一條拍線」，但其實只多了一條。相鄰
    兩個短間距加起來約等於正常間距時，就是這種情形；它們之間那條線
    （`lines[第一個的 index + 1]`）正是多出來的那條。

    插在偏一邊的不會進這裡：`UFO_Real_11` 是 58 + 442，後面那段 442/500 還有
    八成多，本來就只有一個間距被標記。
    """
    out: List[Spike] = []
    i = 0
    while i < len(spikes):
        a = spikes[i]
        b = spikes[i + 1] if i + 1 < len(spikes) else None
        if (b is not None and a.kind == 'extra' and b.kind == 'extra'
                and b.index == a.index + 1
                and abs(a.gap + b.gap - a.local) <= 0.15 * a.local):
            out.append(Spike(
                index=a.index, at_ms=a.at_ms,
                # 多出來的是中間那條，不是兩端
                to_ms=int(lines[a.index + 1]),
                gap=min(a.gap, b.gap), local=a.local,
                # 報「間距只有鄰居的幾成」時看的是比較短的那一段
                ratio=min(a.ratio, b.ratio), forced_kind='extra'))
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def check(lines: Sequence[Any], bpm: float = 0.0) -> GridReport:
    """體檢一組拍線（毫秒）。`bpm` 給 0 就跳過「對不上宣告 BPM」那一項。"""
    pts = sorted({int(round(float(x))) for x in lines})
    rep = GridReport(lines=len(pts), bpm=float(bpm or 0.0))
    if len(pts) < MIN_LINES:
        return rep

    all_gaps = [pts[i + 1] - pts[i] for i in range(len(pts) - 1)]
    # **頭尾兩端都不納入判斷**，因為兩邊本來就常常是不完整的拍：
    #  ・開頭：弱起小節。`flight_of_the_bamboo_cutter` 第一個間距 923ms、其餘
    #    1846ms；`one_voice` 909 對 1818——都剛好是一半，是正常的弱起。
    #  ・結尾：樂曲結束的收尾標記。實測 `Immaculate` 最後一個間距 43ms、
    #    `UFO_Real_11` 58ms，而鄰居是 300~500ms。
    # 不排除的話這兩種正常資料每一份都會被報成「多了一條拍線」。
    offset = 1 if len(all_gaps) >= 5 else 0
    body = all_gaps[offset:len(all_gaps) - offset] if offset else all_gaps
    if not body:
        return rep

    rep.gaps = len(body)
    rep.median_gap = float(statistics.median(body))
    rep.enough = True
    rep.spikes = _spikes_of(body, pts, offset)

    spiky = {s.index for s in rep.spikes}
    med = rep.median_gap
    if med > 0:
        # body 的第 i 個就是整份譜的第 i+offset 個間距
        rep.rough = sum(1 for i, g in enumerate(body)
                        if (i + offset) not in spiky
                        and abs(g - med) > ROUGH * med)

    if rep.bpm > 0 and med > 0:
        quarter = 60000.0 / rep.bpm
        rep.bpm_ratio = med / quarter
        rep.bpm_fit = min(abs(rep.bpm_ratio - s) / s for s in SIMPLE_RATIOS)
    return rep


def check_model(model: Any) -> GridReport:
    """體檢一份 `NoteModel`（只用 `get_beat_entries()` 和 `bpm`）。"""
    try:
        entries = model.get_beat_entries() or []
    except Exception:
        return GridReport()
    return check([ms for _idx, ms in entries],
                 float(getattr(model, 'bpm', 0.0) or 0.0))
