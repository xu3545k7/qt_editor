"""Pitch-aware, non-destructive MIDI-to-chart lane arrangement.

The arranger intentionally keeps every MIDI note.  It treats notes whose
starts are close together as one visual gesture, preserves their pitch order,
and packs both hands into non-overlapping lane regions.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import exp
from time import perf_counter
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SmartChartSettings:
    total_lanes: int = 28
    onset_tolerance_ms: int = 35
    beat_ms: float = 500.0
    classify_articulations: bool = False
    # 表情記號的自動判定，預設全部關閉。soft／staccato／滑音都是演奏詮釋，
    # 猜錯的話要一顆一顆改回來，代價比漏標高，所以交給人判斷。判斷邏輯都
    # 留在 _classify_articulations 裡，需要時把對應的開關打開即可。
    # 只有 hold（長押）仍然自動寫 —— 那是從 MIDI 長度直接看得出來的事實，
    # 不是詮釋。trill 本來就只統計、不寫入。
    classify_staccato: bool = False
    classify_soft: bool = False
    classify_slide: bool = False
    # staccato 判定：到下一顆同手音的間隔至少要是該手常態間隔的幾倍
    staccato_gap_ratio: float = 1.5
    # 滑音：相鄰兩顆的時間間隔上限（超過就是彈得出來的音，不該用滑音）
    slide_max_gap_ms: int = 80
    hold_corridor_min_beats: float = 0.40
    long_note_order_min_beats: float = 16.00
    phrase_window_ms: int = 2000
    close_time_ms: int = 260
    macro_trend_window_ms: int = 700
    macro_pitch_threshold: float = 1.0
    macro_anchor_weight: float = 1.0
    macro_transition_weight: float = 7.0
    # 前後相接音符的順序約束時間窗。原本 140ms 只涵蓋極近的相鄰音，
    # 間隔稍長的前後配對完全不受約束（反向率 3.0%，官方 1.8%）。
    # 放寬到 500ms 之後反向降到 1.9%，與官方同級。
    pitch_trend_window_ms: int = 500
    # 兩手合計最高音那條線要嚴格單調：音高升則右緣必須嚴格右移，反之亦然。
    # 這條線比單手的旋律頂端更重要，所以用大很多的時間窗約束。
    top_edge_window_ms: int = 2500
    # 相鄰最高音位移量與「應有比例」的容許誤差（鍵道）
    top_step_tolerance: int = 0
    top_step_lanes_per_semitone: float = 0.5
    # 回溯修正：卡住時往前幾個事件找可以讓路的音符
    backtrack_lookback: int = 4
    backtrack_lane_reach: int = 3
    # 連帶移動：最多把連續幾個事件的區塊當成一個整體一起平移
    backtrack_chain_span: int = 4
    backtrack_passes: int = 3
    # 輪廓回溯允許把同音位置帶偏多少格（0 = 完全不准變差）
    backtrack_anchor_slack: float = 0.0
    # 同音高位置一致性：一個樂句視窗內，同一音高應該落在同一位置
    pitch_consistency_window_ms: int = 4000
    # reach 3 太小：實測同一個音高在 375ms 內被擺到差 5 格的地方，這個通道
    # 一次只能移 3 格就放棄了。使用者要的是「前後同音要在同軌」，所以放寬到
    # 能一次跨過整個和絃寬度＋交界的距離。
    pitch_consistency_reach: int = 6
    pitch_consistency_passes: int = 4
    # 「前後同音同軌」：直接對齊前一次出現，而不是視窗中位數
    snap_repeat_window_ms: int = 4000
    snap_repeat_reach: int = 6
    snap_repeat_passes: int = 3
    # 吸附是否排在所有搬動音符的通道之後再收一次（= 同音同軌的最高優先）
    snap_repeat_final: bool = True
    # 前置約束：擺放前先把同音高的目標位置錨定在一起
    pitch_anchor_weight: float = 0.85
    pitch_anchor_window_ms: int = 4000
    # 修復通道接受一次移動時，「偏離音高錨點」要算多少成本
    dp_anchor_deviation_weight: float = 8.0
    # 後段修復通道挑選擺法時，錨點偏離要算多少成本
    repair_anchor_weight: float = 1.0
    # 前後相接的度數距離：每個半音應該對應幾格鍵道（官方 real 實測 ≈0.45），
    # 以及它在移動判準裡的權重。
    sequence_lanes_per_semitone: float = 0.50
    # 超過一個八度之後，官方的鍵道距不再等比例成長（斜率掉到約 0.25）
    # 換手判斷：同一隻手在這個時間窗內跳超過這麼多半音就開始罰
    hand_leap_window_ms: int = 700
    hand_leap_semitones: int = 12
    hand_leap_weight: float = 1.0
    hand_track_bias: float = 2.0
    sequence_knee_semitones: int = 12
    sequence_wide_lanes_per_semitone: float = 0.25
    sequence_interval_weight: float = 6.0
    # 單一修復通道的時間預算（秒），超過就收手避免卡住 UI
    # 3 秒會讓密集譜多留 2 個順序違規；8 秒收斂到和不限時間一樣（14 個），
    # 而且總耗時只從 10.8 增到 17 秒左右。
    repair_time_budget_sec: float = 8.0
    pitch_order_projection_passes: int = 12
    edge_margin: int = 1
    normal_width: int = 3
    dense_width: int = 2
    # 單手同時 4 個音以上就收窄每一顆 —— 那是容量問題（真的塞不下），
    # 和「為了修順序而收窄」不同，後者才是最後手段。
    dense_hand_threshold: int = 4
    # 小度數（含）以內的相鄰音高改用寬度 2 —— 兩顆寬度 3 的音符中心距最少
    # 就是 3，半音到五度會全部擠在同一個距離上、看不出度數差別。收成寬度 2
    # 之後中心距才能降到 2，度數層次才做得出來。
    close_chord_interval_semitones: int = 3
    minimum_pitch_span: int = 18
    max_octave_center_distance: float = 5.0
    # 同手、同時發聲的「兩顆」音，官方 real 的中心距是階梯狀的（n=101769）：
    # ≤4 半音 3 格、5~7 半音 4 格、≥8 半音一律 5 格（8 到 24 半音量出來全是 5）。
    chord_pair_close_semitones: int = 4
    chord_pair_close_lanes: float = 3.0
    chord_pair_mid_semitones: int = 7
    chord_pair_mid_lanes: float = 4.0
    # 同手同時 3 顆以上就完全「貼合」，不再照音程留空隙。官方 real 的 k=3
    # 外圍中心距固定 6 格 —— 外圍音程從 5 半音到 24 半音全部都是 6，而且
    # 中間那顆固定落在正中央（音程比例 0.25/0.50/0.75 通通對應鍵道比例 0.50）。
    # 也就是三顆寬度 3 的音符並排、共 9 格，內部完全不表現度數。
    chord_flush_min_notes: int = 3
    # 「度數不夠」的跨度區間（半音）：收窄率的高峰在中間那一段，見
    # _hand_span_wants_narrowing
    # 收窄的跨度帶。使用者定調：「度數不到八度要收窄」＋「度數短但有空間就
    # 不用收窄」——合起來就是**中間那一段**才收。跨度太小時本來就排得開、
    # 用位置表現就好；跨度接近八度時位置擠不下，收窄才真的解決問題。
    # 這也正好是他自己手寫譜的形狀（k=3 各跨度收窄率 12%/28%/33%/13%，峰值 7~11）。
    narrow_span_min: int = 7
    narrow_span_max: int = 11
    # 整組收窄的跨度帶：7~11 半音 = 不到一個八度、但也不是擠在一起的短度數
    narrow_whole_span_min: int = 7
    narrow_whole_span_max: int = 11
    # 單手最高音嚴格排序收尾：整組最多平移幾格、跑幾輪
    top_step_strict_reach: int = 3
    top_step_strict_passes: int = 4
    # 整組移不動時，允不允許單獨移最高音那一顆（會犧牲一點和絃貼合）
    # 整組移不動時，允不允許單獨移最高音那一顆（會犧牲一點和絃貼合）
    top_step_strict_solo: bool = True
    # 順序修復用整數位移搬音符時，「把同手和絃拆出空隙」要付的成本（每格）。
    # 沒有這一項的話 _discrete_final_order_repair 會把擺放時排好的貼合拆掉
    # ——實測它一個人就讓 k≥3 貼合率掉 159pp。
    chord_flush_weight: float = 3.0
    # 最後重排和絃時，整組最多平移幾格去找合法位置
    chord_flush_reach: int = 4
    # 一個八度至少要隔開幾個鍵道中心。沒有下限的話，音域寬的和弦會把
    # 局部斜率壓得很小，八度被擠成 3 格（等於音符寬度的緊密排列）。
    min_octave_center_distance: float = 5.0
    # 單一音程最多撐開幾個鍵道（避免超大跳把整組推爆）
    max_interval_center_distance: float = 12.0
    # desired 比音程對應間距寬多少以內就吸回去（鍵道）
    interval_snap_tolerance: int = 2
    global_position_weight: float = 0.65
    global_edge_band_fraction: float = 0.12
    global_edge_band_min_semitones: int = 4
    local_peak_prominence_semitones: int = 4
    dp_shift_limit: int = 14
    dp_anchor_weight: float = 0.08
    dp_transition_weight: float = 1.4
    dp_reserve_weight: float = 1.0
    # 邊緣錨定原本是 48，會為了把樂句極值推到鍵盤兩端而把整組音符搬離
    # 音高該在的位置 —— 前後段同一個音高差到 8~9 格、每半音佔幾格變動近
    # 兩倍，就是它造成的。降到 4 之後段落分布與音高的相關從 0.67 升到 0.94。
    dp_extreme_edge_weight: float = 4.0
    # 大區塊之間的音高映射漂移上限。映射（pitch→lane 的中心與斜率）只准
    # 慢慢改，換音域時是循序漸進地移過去，而不是一個區塊一個樣。
    # 真正的音高跳躍仍然照跳，被限制的只有「映射本身」。
    map_drift_lanes_per_sec: float = 1.0
    map_slope_rate_per_sec: float = 0.25
    dp_drift_weight: float = 24.0
    # 決定 pitch→lane 映射時，兩手的音域一起算，而且用較長的記憶窗：
    # 某一手暫時休息不該讓整個映射平移。
    hand_memory_ms: int = 4000
    # 鍵盤不是線性的：一個八度在中央大約佔 5~6 個鍵道，靠近左右邊緣壓縮到
    # 大約 3 個。用一條三次曲線塑形，中央撐開、邊緣收攏。
    octave_lanes_center: float = 6.0
    octave_lanes_edge: float = 2.5
    # 空間不夠時可以縮寬度，但最窄只能到 2（人工譜面沒有寬度 1 的音符）
    min_note_width: int = 2
    # 兩手交界處額外留的鍵道（官方 real 實測：同度數下跨手比同手多 2 格）
    hand_boundary_margin: float = 2.0
    # 跨手中心距的下限——官方小度數跨手也維持 5 格左右，不會貼在一起
    hand_boundary_min_distance: float = 5.0
    # 樂句：靜止超過這麼久算換句；整句平移最多這麼多鍵道
    phrase_gap_ms: int = 400
    phrase_shift_reach: int = 3
    # 順序檢查要往後看幾個和絃（1 = 只看緊接著的下一個）
    pitch_order_lookahead: int = 2
    tie_break_passes: int = 3
    # 分層排譜：先把樂句區塊放到全域音高刻度上，再排塊內
    # 關閉。這一層想解決的「樂句之間的高低關係」已經由 _align_phrase_extremes
    # （整句平移）做到 85%/85%（官方 78.4%/81.5%），分層再進場只是重複收費：
    # 每塊各自的偏移量會讓同一個音高散開（實測同音散布 5.5→5.8）。
    # 要重開的話，得先想清楚它能提供 _align_phrase_extremes 給不了的東西。
    block_layout: bool = False
    block_max_shift: float = 2.5   # 整塊最多挪這麼多鍵道
    # 趨勢分塊：超過這個度數算大跳（切點）；區塊至少要這麼多顆才算數
    trend_jump_semitones: int = 12
    trend_min_notes: int = 3
    use_trend_blocks: bool = True
    # 來源 MIDI 剛好兩軌時，直接把那兩軌當成左右手，後面不再改動。
    # 實測 16/18 個來源 MIDI 是兩軌，人工譜的分手就是照音軌走的。
    trust_two_track_hands: bool = True
    # 一隻手同時最多幾顆。官方是照「盡量平均」分手的：同時 4 音有 51% 排成
    # 2/2、44% 排成 3/1，只有 2% 給同一手 4 顆；5 音 91% 是 3/2、6 音 71%
    # 是 3/3、7 音 62% 是 4/3。整體「最忙的那隻手拿 4 顆以上」只佔 0.65%。
    # 實際上限是 max(3, ceil(N/2))。
    max_hand_chord_notes: int = 3
    # 一隻手在同一組裡搆得到的鍵道跨度。官方 real 難度 30495 個同時發聲組，
    # 單手跨度 99.7% 在 9 條以內（10 條以上只有 0.3%）。
    hand_span_lanes: int = 9


# ---------------------------------------------------------------------------
# 轉譜風格
# ---------------------------------------------------------------------------
# 兩套風格是**實測出來的兩種不同做法**，不是鬆緊度的差別：
#
#                       官方 real 語料        Eather 的手寫譜
#   寬度 2 的比率        2.7%                  14.6%
#   同手和絃鍵道重疊     28.1%                 0.8%
#   收窄的時機           剛好單手同時 4 音     整組跨度 7~11 半音
#                        （88.4%；k=5 掉回     （k=3 收窄率 12/28/33/13%，
#                          24.2%，改用重疊）     峰值在中間那一段）
#
# 也就是：**官方靠「讓鍵道重疊」擠空間，這個曲庫靠「收窄」擠空間**。
# 兩者的和絃內部幾何、分手上限、度數階梯表是共用的（那些是官方語料量出來
# 的物理事實，手寫譜也遵守）。
STYLE_OFFICIAL = 'official'
STYLE_EATHER = 'eather'
# 舊的設定檔存的是 'user'，讀得懂就好，不必叫使用者重設。
STYLE_USER = STYLE_EATHER
_STYLE_ALIASES = {'user': STYLE_EATHER, 'mine': STYLE_EATHER}
CHART_STYLES = (STYLE_EATHER, STYLE_OFFICIAL)


def normalise_style(style: Any) -> str:
    """把設定檔／參數裡的風格名正規化。未知的一律退回 Eather 風格。"""
    name = str(style or '').lower()
    name = _STYLE_ALIASES.get(name, name)
    return name if name in CHART_STYLES else STYLE_EATHER


def settings_for_style(
    style: str = STYLE_EATHER, **overrides: Any
) -> "SmartChartSettings":
    """依風格產生一份設定。未知的風格名一律退回 Eather 風格。"""
    if normalise_style(style) == STYLE_OFFICIAL:
        base: Dict[str, Any] = dict(
            # 官方不用跨度收窄——只有「剛好單手同時 4 音」才收，其餘靠重疊。
            # 把跨度帶設成永遠不成立即可（min > max）。
            narrow_span_min=99,
            narrow_span_max=0,
            narrow_whole_span_min=99,
            narrow_whole_span_max=0,
            close_chord_interval_semitones=0,
            # 官方 real 有 0.8% 的滑音、hard 甚至到 8%，那是官方的語彙。
            classify_slide=True,
            # soft / staccato 是編輯器專用型別，官方資料裡完全沒有。
            classify_soft=False,
            classify_staccato=False,
            # 官方的同音位置沒有這個曲庫要求得那麼死（2 秒窗內中位數 0，
            # 但整首散得比較開），所以不跑最後那道吸附。
            snap_repeat_final=False,
        )
    else:
        base = dict(
            narrow_span_min=7,
            narrow_span_max=11,
            narrow_whole_span_min=7,
            narrow_whole_span_max=11,
            close_chord_interval_semitones=3,
            # 表情記號一律由人判斷
            classify_slide=False,
            classify_soft=False,
            classify_staccato=False,
            # 前後同音同軌是這個曲庫的最高優先項
            snap_repeat_final=True,
        )
    base.update(overrides)
    return SmartChartSettings(**base)


@dataclass
class SmartChartStats:
    notes: int = 0
    groups: int = 0
    hand_changes: int = 0
    width_two_notes: int = 0
    width_restored_notes: int = 0
    width_clamped_notes: int = 0
    hand_boundary_widened: int = 0
    phrase_shifts: int = 0
    ties_broken: int = 0
    block_layouts: int = 0
    forced_width_one_notes: int = 0
    unresolved_overlaps: int = 0
    pitch_order_violations: int = 0
    dp_shifted_groups: int = 0
    context_compressed_groups: int = 0
    global_edge_anchored_groups: int = 0
    macro_adjusted_groups: int = 0
    macro_trend_violations: int = 0
    top_edge_violations: int = 0
    articulation_changes: int = 0
    slide_notes: int = 0
    trill_patterns: int = 0
    hold_corridor_conflicts: int = 0
    motif_reuses: int = 0
    motion_limited_groups: int = 0
    small_interval_restores: int = 0
    small_interval_unresolved: int = 0
    nearby_order_repairs: int = 0
    top_step_repairs: int = 0
    backtrack_moves: int = 0
    pitch_consistency_moves: int = 0
    pitch_anchor_adjusted: int = 0
    chord_reflushed: int = 0
    hand_top_strict_repairs: int = 0


def _start(note: Any) -> int:
    return int(getattr(note, "start", 0))


def _width(note: Any) -> int:
    return int(note.max_key) - int(note.min_key) + 1


def _pitch(note: Any) -> int:
    # 排譜過程中音高不會變（動的是鍵道），但這個函式在修復迴圈裡被呼叫
    # 將近一億次，getattr + int() 就吃掉 20 秒。開頭先把值釘在音符上，
    # 這裡直接讀屬性；`_prime_pitch_cache` 進出各清一次，避免使用者改了
    # 音高之後讀到舊值。
    try:
        return note._sc_pitch
    except AttributeError:
        pass
    value = getattr(note, "pitch", None)
    if value is not None:
        return int(value)
    return int(round((int(note.min_key) + int(note.max_key)) / 2.0))


def _prime_pitch_cache(notes: Sequence[Any], enable: bool = True) -> None:
    """把每顆音符的音高釘成純 int 屬性（或在結束時清掉）。"""
    for note in notes:
        if enable:
            value = getattr(note, "pitch", None)
            note._sc_pitch = (
                int(value) if value is not None
                else int(round((int(note.min_key) + int(note.max_key)) / 2.0))
            )
        else:
            try:
                del note._sc_pitch
            except AttributeError:
                pass


def _velocity(note: Any) -> int:
    value = getattr(note, "velocity", None)
    if value is not None:
        return int(value)
    sub_notes = list(getattr(note, "sub_notes", []) or [])
    if sub_notes:
        return int(getattr(sub_notes[0], "velocity", 100))
    return 100


def _classify_articulations(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> Tuple[int, int, int]:
    """Infer articulations from each hand's local onset-spacing distribution."""
    beat_ms = max(80.0, float(settings.beat_ms))
    changed = 0
    by_hand: Dict[int, List[Any]] = {0: [], 1: []}
    for note in notes:
        by_hand[int(getattr(note, "hand", 0))].append(note)

    for hand_notes in by_hand.values():
        ordered = sorted(hand_notes, key=lambda note: (_start(note), _pitch(note)))
        distinct_starts = sorted({_start(note) for note in ordered})
        onset_gaps = [
            second - first
            for first, second in zip(
                distinct_starts, distinct_starts[1:]
            )
            if second > first
        ]
        if onset_gaps:
            short_spacing = max(1.0, _quantile(onset_gaps, 0.35))
            typical_spacing = max(
                short_spacing,
                float(median(onset_gaps)),
            )
            long_spacing = max(
                typical_spacing,
                _quantile(onset_gaps, 0.75),
            )
        else:
            short_spacing = beat_ms
            typical_spacing = beat_ms
            long_spacing = beat_ms
        # A hold must remain meaningfully long after reserving one short
        # rhythmic unit for the next same-hand action. Both values come from
        # this hand's actual rhythm rather than fixed fractions of a beat.
        hold_tail_threshold = max(
            short_spacing * 2.00,
            typical_spacing * 1.50,
            long_spacing,
        )
        staccato_duration = min(
            short_spacing * 0.45,
            typical_spacing * 0.30,
        )
        for note in ordered:
            old_type = int(getattr(note, "note_type", 0) or 0)
            if old_type not in (0, 2, 3):
                continue
            duration = max(1, int(getattr(note, "end", _start(note) + 1)) - _start(note))
            next_index = bisect_right(distinct_starts, _start(note))
            has_next = next_index < len(distinct_starts)
            next_start = (
                distinct_starts[next_index] if has_next else None
            )
            ioi = (
                max(1, int(next_start) - _start(note))
                if next_start is not None
                else max(1, int(round(typical_spacing)))
            )
            # Pedal data often makes duration extend far beyond later notes.
            # Only the portion available before the next onset is playable,
            # then subtract the hand's shortest normal action spacing.
            playable_before_next = (
                min(float(duration), float(ioi))
                if has_next
                else float(duration)
            )
            adjusted_tail = max(
                0.0,
                playable_before_next - short_spacing,
            )
            gate_ratio = duration / max(1.0, float(ioi))
            if (
                settings.classify_soft
                and _velocity(note) <= 45
                and duration <= short_spacing * 0.90
            ):
                new_type = 1  # soft
            elif adjusted_tail >= hold_tail_threshold:
                new_type = 2  # hold
            elif (
                duration <= staccato_duration
                and gate_ratio <= 0.50
                # staccato 不只是「自己短」，還要求「下一顆同手音很遠」——
                # 短音接短音那是快速樂句，不是斷奏。樂句結尾（後面沒有同手
                # 音了）視為無限遠，一定符合。
                and (
                    not has_next
                    or ioi >= max(
                        typical_spacing * float(settings.staccato_gap_ratio),
                        long_spacing,
                    )
                )
            ):
                # 使用者要求：staccato 不自動寫。判斷條件本身留著（要開回來
                # 只要把 classify_staccato 打開），但預設落回 tap —— 斷奏是
                # 演奏詮釋，猜錯就得一顆一顆改回來，代價比漏標高。
                new_type = 3 if settings.classify_staccato else 0
            else:
                new_type = 0  # tap
            if new_type != old_type:
                note.note_type = new_type
                changed += 1

    slide_notes = 0
    # 滑音只在「時間上不可能用指法彈」的時候才用。原本是 beat_ms×0.25
    # （120bpm 就是 125ms），但 125ms 一顆音是正常的十六分音符，手指彈得出來。
    # 這裡再加一個絕對上限，真正的刮奏才會落進來。
    max_gap = min(
        max(30, int(round(beat_ms * 0.25))),
        max(20, int(settings.slide_max_gap_ms)),
    )
    # param1/param2 參照的是音符存檔時的 <index>，而存檔的 <index> 就是音符
    # 在 notes_tree 裡的位置。舊版寫的是 `getattr(note, "index", ...)`，但
    # GNote 根本沒有 `index` 這個屬性（它叫 note_index），所以每次都掉進
    # fallback、寫成「在這條滑音裡的第幾顆」(0,1,2,3)，存檔後鏈結全部指錯，
    # 畫面上就是一根一根分開的滑音。
    position = {id(note): index for index, note in enumerate(notes)}
    for hand_notes in by_hand.values():
        ordered = sorted(hand_notes, key=lambda note: (_start(note), _pitch(note)))
        run: List[Any] = []
        direction = 0

        def flush() -> None:
            nonlocal slide_notes, changed, run
            if not settings.classify_slide or len(run) < 4:
                run = []
                return
            for index, item in enumerate(run):
                old_type = int(getattr(item, "note_type", 0) or 0)
                if old_type in (0, 3, 4):
                    if old_type != 4:
                        changed += 1
                    item.note_type = 4
                    # 同時把 note_index 釘成自己的位置，存檔時的「舊 index →
                    # 新序號」對照表才認得這些音符（MIDI 匯入的音符本來是
                    # None，對照表查不到就不會重寫，鏈結一樣會壞）。
                    item.note_index = position[id(item)]
                    item.param1 = (
                        position[id(run[index - 1])] if index > 0 else -1
                    )
                    item.param2 = (
                        position[id(run[index + 1])]
                        if index + 1 < len(run)
                        else -1
                    )
                    item.param3 = 0
                    slide_notes += 1
            run = []

        for note in ordered:
            if not run:
                run = [note]
                direction = 0
                continue
            previous = run[-1]
            gap = _start(note) - _start(previous)
            delta = _pitch(note) - _pitch(previous)
            step_direction = 1 if delta > 0 else -1 if delta < 0 else 0
            eligible = (
                0 < gap <= max_gap
                and 0 < abs(delta) <= 2
                and int(getattr(note, "note_type", 0) or 0) in (0, 3)
                and int(getattr(previous, "note_type", 0) or 0) in (0, 3)
                and (direction == 0 or direction == step_direction)
            )
            if eligible:
                run.append(note)
                direction = step_direction
            else:
                flush()
                run = [note]
                direction = 0
        flush()
    trill_patterns = 0
    trill_gap = max(25, int(round(beat_ms * 0.20)))
    for hand_notes in by_hand.values():
        ordered = sorted(hand_notes, key=lambda note: (_start(note), _pitch(note)))
        index = 0
        while index + 5 < len(ordered):
            first_pitch = _pitch(ordered[index])
            second_pitch = _pitch(ordered[index + 1])
            if first_pitch == second_pitch or abs(first_pitch - second_pitch) > 4:
                index += 1
                continue
            end = index + 2
            while end < len(ordered):
                previous = ordered[end - 1]
                current = ordered[end]
                if not (
                    0 < _start(current) - _start(previous) <= trill_gap
                    and _pitch(current)
                    == (first_pitch if (end - index) % 2 == 0 else second_pitch)
                ):
                    break
                end += 1
            if end - index >= 6:
                # Keep every MIDI note, but expose the detected pattern so the
                # editor can offer lossless manual trill consolidation.
                trill_patterns += 1
                index = end
            else:
                index += 1
    return changed, slide_notes, trill_patterns


# 分組只跟音符的「起始時間」有關，而修復通道只改鍵道不改時間，所以在一次
# arrange_midi_notes 期間結果是不變的。這裡快取起來 —— 原本 `_top_edge_pairs`
# 之類的函式在修復迴圈裡會重建上百次，光排序就吃掉數秒。
_CLUSTER_CACHE: Dict[Tuple[int, int, int], List[List[Any]]] = {}


def _clear_cluster_cache() -> None:
    _CLUSTER_CACHE.clear()


def _clusters(notes: Sequence[Any], tolerance_ms: int) -> List[List[Any]]:
    key = (id(notes), len(notes), int(tolerance_ms))
    cached = _CLUSTER_CACHE.get(key)
    if cached is not None:
        return cached
    result = _clusters_uncached(notes, tolerance_ms)
    _CLUSTER_CACHE[key] = result
    return result


def _clusters_uncached(notes: Sequence[Any], tolerance_ms: int) -> List[List[Any]]:
    ordered = sorted(notes, key=lambda n: (_start(n), _pitch(n), int(getattr(n, "index", 0))))
    result: List[List[Any]] = []
    current: List[Any] = []
    anchor = 0
    for note in ordered:
        when = _start(note)
        if not current or when - anchor <= tolerance_ms:
            if not current:
                anchor = when
            current.append(note)
        else:
            result.append(current)
            current = [note]
            anchor = when
    if current:
        result.append(current)
    return result


def _quantile(values: Sequence[int], fraction: float) -> float:
    if not values:
        return 60.0
    ordered = sorted(int(v) for v in values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lo = int(position)
    hi = min(len(ordered) - 1, lo + 1)
    part = position - lo
    return ordered[lo] * (1.0 - part) + ordered[hi] * part


def _hand_split_cap(size: int, settings: SmartChartSettings) -> int:
    """一組同時發聲的音符，單手最多能拿幾顆。

    上限取自官方 real 難度「最忙的那隻手按幾顆」的 95 百分位（785 份譜面）：

        N=2 → 2   N=3 → 3   N=4 → 3   N=5 → 4   N=6 → 4   N=7 → 4   N=8 → 5

    原本只有 `max(3, ceil(N/2))`，在 N=5、6、8 都太緊：N=5 有 12% 的官方組是
    4/1、N=6 有 28% 是 4/2 或 2/4（分配比例 3/3 只佔 55%），一律禁掉的結果就是
    把右手和絃的最低音硬切給左手。N≤4 和 N=7 兩邊算出來一樣，不受影響。
    """
    base = max(int(settings.max_hand_chord_notes), (size + 1) // 2)
    if size >= 8:
        return max(base, 5)
    if size >= 5:
        return max(base, 4)
    return base


def _hands_came_from_tracks(notes: Sequence[Any]) -> bool:
    """分手是不是直接來自音軌（而不是照音高中位數猜的）。

    條件要和 `_assign_hands` 裡那個分支一致：有兩條以上帶音符的音軌才算。
    """
    tracks = {int(getattr(note, "track"))
              for note in notes if getattr(note, "track", None) is not None}
    return len(tracks) >= 2


def _assign_hands(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: Optional[SmartChartSettings] = None,
) -> int:
    """Infer stable hands, then remove crossed hands inside close-time groups."""
    settings = settings or SmartChartSettings()
    old_hands = {id(note): int(getattr(note, "hand", 0)) for note in notes}
    by_track: Dict[int, List[Any]] = {}
    for note in notes:
        track = getattr(note, "track", None)
        if track is not None:
            by_track.setdefault(int(track), []).append(note)

    note_tracks = [track for track, items in by_track.items() if items]
    # 音軌數剛好兩軌時，那兩軌**就是**左右手——來源 MIDI 本來就是這樣寫的，
    # 沒有任何推測空間。實測這個曲庫 18 個來源 MIDI 有 16 個是兩軌，人工譜
    # 的分手和音軌一致；排譜器再去動它只會製造 0~2% 的分歧。三軌以上（有
    # 指揮軌、或 Designant 那種 20 軌）才需要靠音高平均去猜，那時候後面的
    # 修正才有意義。
    track_is_authoritative = (
        bool(settings.trust_two_track_hands) and len(note_tracks) == 2
    )
    if len(note_tracks) >= 2:
        track_means = {
            track: sum(_pitch(note) for note in by_track[track]) / len(by_track[track])
            for track in note_tracks
        }
        ordered_tracks = sorted(note_tracks, key=lambda track: (track_means[track], track))
        split = max(1, len(ordered_tracks) // 2)
        left_tracks = set(ordered_tracks[:split])
        for note in notes:
            track = getattr(note, "track", None)
            note.hand = 1 if track is not None and int(track) in left_tracks else 0
    else:
        pitch_mid = float(median([_pitch(note) for note in notes])) if notes else 60.0
        for note in notes:
            note.hand = 1 if _pitch(note) < pitch_mid else 0

    # A close gesture must not have low right-hand notes crossing high
    # left-hand notes. Preserve the inferred number of notes per hand while
    # assigning the lower pitches to the left hand.
    span_limit = max(3, int(settings.hand_span_lanes))

    def _reaches(part: Sequence[Any]) -> bool:
        """這幾顆音一隻手搆不搆得到（用鍵道量，和官方同一個尺度）。"""
        if len(part) <= 1:
            return True
        return (max(int(getattr(n, "max_key", 0)) for n in part)
                - min(int(getattr(n, "min_key", 0)) for n in part)) <= span_limit

    if track_is_authoritative:
        for note in notes:
            note.hand_locked = True
        return sum(
            old_hands[id(note)] != int(getattr(note, "hand", 0)) for note in notes
        )

    for group in groups:
        left_count = sum(int(getattr(note, "hand", 0)) == 1 for note in group)
        ordered = sorted(
            group, key=lambda n: (_pitch(n), _start(n), int(getattr(n, "index", 0)))
        )
        # 音軌／中位數切出來的分手可能把一整個和絃都塞給同一隻手。官方不會
        # 這樣做（同時 4 音只有 2% 是 4/0），所以分界超出可彈範圍時就改採
        # 官方最常見的那種分法 —— 盡量平均：4 音 2/2、5 音 3/2、6 音 3/3、
        # 7 音 4/3。同時 2~3 音維持原判（官方 2/0 佔 36%、3/0 佔 16%）。
        cap = _hand_split_cap(len(group), settings)
        preferred = left_count
        if not (len(group) - cap <= left_count <= cap):
            preferred = len(group) // 2

        # **但「盡量平均」不能凌駕於手搆不搆得到。** 左手 41、53 加右手
        # 68、72、75、80 這種寫法，硬切 3/3 會把 68 判給左手，於是左手要同時
        # 按 41~68（27 個半音）——一隻手做不到，而且官方也不會這樣排：real 難度
        # 30495 個同時發聲組裡，單手鍵道跨度 99.7% 在 9 條以內。所以先挑出兩邊
        # 都搆得到的分界，再從裡面選最接近「偏好」的那一個。
        options = [k for k in range(len(group) + 1)
                   if _reaches(ordered[:k]) and _reaches(ordered[k:])]
        if options:
            middle = len(group) // 2
            left_count = min(options, key=lambda k: (abs(k - preferred), abs(k - middle), k))
        else:
            left_count = preferred

        if left_count <= 0 or left_count >= len(group):
            continue
        for index, note in enumerate(ordered):
            note.hand = 1 if index < left_count else 0

    return sum(old_hands[id(note)] != int(getattr(note, "hand", 0)) for note in notes)


def _reduce_hand_leaps(
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """重挑每一組的左右手分界，避免同一隻手在短時間內大跳。

    `_assign_hands` 是靜態的：照音軌、或照全曲音高中位數切一刀，再在每組內
    強制「低音給左手」。它完全沒有看時間上的連續性，所以會出現一隻手前一顆
    在低音區、下一顆突然跳兩個八度的排法——實際上那種地方人是換手彈的。
    實測同手前後跳躍超過八度的比例：官方 9.1%，這裡改之前 13.5%。

    因為 `_assign_hands` 已經把每組化簡成「最低的 left_count 顆屬於左手」，
    所以這裡只要重挑那個分界索引就好，不會破壞組內不交叉的性質。
    """
    window = max(1, int(settings.hand_leap_window_ms))
    reach = max(1, int(settings.hand_leap_semitones))
    weight = float(settings.hand_leap_weight)
    bias = float(settings.hand_track_bias)
    changed = 0
    last: Dict[int, Tuple[int, int]] = {}      # hand -> (start, pitch)

    def leap_cost(hand: int, when: int, pitch: int) -> float:
        seen = last.get(hand)
        if seen is None or when - seen[0] > window:
            return 0.0
        excess = abs(pitch - seen[1]) - reach
        return weight * excess if excess > 0 else 0.0

    for group in sorted(groups, key=lambda g: min(_start(n) for n in g)):
        ordered = sorted(
            group,
            key=lambda n: (_pitch(n), _start(n), int(getattr(n, "index", 0))),
        )
        original = sum(int(getattr(n, "hand", 0)) == 1 for n in ordered)
        if not ordered:
            continue
        # 兩軌 MIDI 的分手是來源檔講明的，不是猜的——不要為了「避免大跳」
        # 去改它。真的要換手的話，那是使用者在編輯器裡自己決定的事。
        if any(getattr(n, "hand_locked", False) for n in ordered):
            for index, note in enumerate(ordered):
                last[int(getattr(note, "hand", 0))] = (_start(note), _pitch(note))
            continue
        best_count, best_cost = original, None
        cap = _hand_split_cap(len(ordered), settings)
        lo_count = max(0, len(ordered) - cap)
        hi_count = min(len(ordered), cap)
        # 這裡也要吃「手搆不搆得到」。`_assign_hands` 已經挑過一次可行的分界，
        # 但這一輪是照大跳成本重挑的，不加這道限制就會把剛剛的結果推翻回去——
        # 實測 Miku 消失那份：_assign_hands 把單手跨度超過 9 條的從 24 組壓到 0，
        # 這裡再跑一次又變回 29 組。
        span_limit = max(3, int(settings.hand_span_lanes))

        def reaches(part: Sequence[Any]) -> bool:
            if len(part) <= 1:
                return True
            return (max(int(getattr(n, "max_key", 0)) for n in part)
                    - min(int(getattr(n, "min_key", 0)) for n in part)) <= span_limit

        # 候選要看**整個**範圍，不能只看 cap 那一段：Miku 消失那份就是這樣壞的——
        # 6 音的 cap 只准 3/3，而唯一搆得到的分法是 2/4，於是 cap 內找不到可行解，
        # 又退回去選 3/3，把 `_assign_hands` 剛挑好的結果推翻。
        allowed = [count for count in range(len(ordered) + 1)
                   if reaches(ordered[:count]) and reaches(ordered[count:])]
        if not allowed:
            # 整組本來就搆不到（例如同時 7 音散在兩個八度），那就不必再挑剔。
            allowed = list(range(lo_count, hi_count + 1)) or [original]
        for count in allowed:
            cost = bias * abs(count - original)
            # 偏離官方那種「盡量平均」的分法要付代價，但那是偏好、不是硬限制。
            cost += bias * max(0, lo_count - count, count - hi_count)
            for index, note in enumerate(ordered):
                hand = 1 if index < count else 0
                cost += leap_cost(hand, _start(note), _pitch(note))
            if best_cost is None or cost < best_cost:
                best_count, best_cost = count, cost
        if best_count != original:
            for index, note in enumerate(ordered):
                hand = 1 if index < best_count else 0
                if int(getattr(note, "hand", 0)) != hand:
                    note.hand = hand
                    changed += 1
        for index, note in enumerate(ordered):
            hand = 1 if index < best_count else 0
            last[hand] = (_start(note), _pitch(note))
    return changed


def _shape_lane(lane_value: float, settings: SmartChartSettings) -> float:
    """把線性的鍵道位置塑形成「中央寬、邊緣窄」的分布。

    遊戲鍵盤不是線性的：量人工譜面得到一個八度在中央大約佔 6 個鍵道，
    到左右邊緣壓縮到大約 3 個。用 f(u) = (u + b·u³) / (1 + b) 塑形，
    b = (r − 1) / 3、r = 邊緣八度寬 / 中央八度寬。這個式子在 u = ±1 仍然
    等於 ±1（邊緣鍵道用得到），而導數比恰好是 r，中央被撐開 1/(1+b) 倍。
    """
    center = (settings.total_lanes - 1) / 2.0
    half = max(1e-6, center)
    u = max(-1.0, min(1.0, (float(lane_value) - center) / half))
    ratio = float(settings.octave_lanes_edge) / max(1e-6, float(settings.octave_lanes_center))
    ratio = max(0.05, min(1.0, ratio))
    b = (ratio - 1.0) / 3.0
    shaped = (u + b * u * u * u) / (1.0 + b)
    return center + half * shaped


def _local_pitch_maps(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> Dict[int, Tuple[float, float]]:
    """Return note id -> (desired lane center, local lanes-per-semitone)."""
    ordered = sorted(notes, key=_start)
    starts = [_start(note) for note in ordered]
    all_pitches = [_pitch(note) for note in ordered]
    result: Dict[int, Tuple[float, float]] = {}
    half_window = max(1, settings.phrase_window_ms // 2)
    lane_lo = float(settings.edge_margin)
    lane_hi = float(settings.total_lanes - 1 - settings.edge_margin)
    lane_span = max(1.0, lane_hi - lane_lo)
    global_pitch_lo = min(all_pitches)
    global_pitch_hi = max(all_pitches)
    global_pitch_span = max(1.0, float(global_pitch_hi - global_pitch_lo))
    global_pitch_center = (global_pitch_lo + global_pitch_hi) / 2.0
    global_slope = min(
        lane_span / global_pitch_span,
        float(settings.max_octave_center_distance) / 12.0,
    )
    global_weight = max(0.0, min(1.0, float(settings.global_position_weight)))

    previous: Dict[int, Tuple[int, float, float]] = {}
    # 映射本身（中心音高 + 斜率）跨區塊的漂移限速用
    mapping_prev: Optional[Tuple[int, float, float]] = None
    for group in groups:
        when = min(_start(note) for note in group)
        lo = bisect_left(starts, when - half_window)
        hi = bisect_right(starts, when + half_window)
        local_pitches = [_pitch(note) for note in ordered[lo:hi]]
        # 兩手一起考慮：只看當下 ±1 秒的話，某一手一休息，局部音域的
        # 上下界就會整個縮進來，映射中心跟著平移，害另一手的音符明明
        # 音高沒變卻換了鍵道。改成用較長的記憶窗、且每隻手各自取分位數
        # 再取聯集，讓暫時休息的那一手仍然佔著它的音域。
        mem_lo = bisect_left(starts, when - settings.hand_memory_ms)
        mem_hi = bisect_right(starts, when + settings.hand_memory_ms)
        per_hand: Dict[int, List[int]] = {}
        for note in ordered[mem_lo:mem_hi]:
            per_hand.setdefault(int(getattr(note, "hand", 0)), []).append(_pitch(note))
        if per_hand:
            pitch_lo = min(_quantile(ps, 0.08) for ps in per_hand.values())
            pitch_hi = max(_quantile(ps, 0.92) for ps in per_hand.values())
        else:
            pitch_lo = _quantile(local_pitches, 0.08)
            pitch_hi = _quantile(local_pitches, 0.92)
        if pitch_hi - pitch_lo < settings.minimum_pitch_span:
            center_pitch = (pitch_lo + pitch_hi) / 2.0
            pitch_lo = center_pitch - settings.minimum_pitch_span / 2.0
            pitch_hi = center_pitch + settings.minimum_pitch_span / 2.0
        pitch_span = max(1.0, pitch_hi - pitch_lo)
        # The game's visual keyboard is much narrower than a real 88-key
        # piano. Do not let local-range expansion make an octave wider than
        # five lane centers. Wider spacing is allowed later only when packing
        # a simultaneous chord would otherwise overlap.
        slope = min(
            lane_span / pitch_span,
            float(settings.max_octave_center_distance) / 12.0,
        )
        # 下限：八度不能被壓得比 min_octave_center_distance 還窄
        slope = max(slope, float(settings.min_octave_center_distance) / 12.0)
        center_pitch = (pitch_lo + pitch_hi) / 2.0
        center_lane = (settings.total_lanes - 1) / 2.0

        # ── 映射漂移限速 ────────────────────────────────────────────
        # 區塊之間換音域時，若直接用新區塊自己的中心/斜率，整段的鍵道
        # 分布會在一瞬間整個平移。這裡限制映射每秒能移動幾個鍵道，
        # 空間不夠就分好幾個區塊慢慢移過去（循序漸進）。
        if mapping_prev is not None:
            prev_when, prev_center, prev_slope = mapping_prev
            dt = max(0.0, (when - prev_when) / 1000.0)
            max_lane_shift = max(0.0, settings.map_drift_lanes_per_sec) * dt
            # 中心音高從 prev_center 移到 center_pitch，造成的鍵道位移
            raw_shift = (prev_center - center_pitch) * slope
            if abs(raw_shift) > max_lane_shift and slope > 1e-6:
                allowed = max_lane_shift if raw_shift > 0 else -max_lane_shift
                center_pitch = prev_center - allowed / slope
            max_slope_delta = max(0.0, settings.map_slope_rate_per_sec) * dt
            slope = max(
                prev_slope - max_slope_delta,
                min(prev_slope + max_slope_delta, slope),
            )
        mapping_prev = (when, center_pitch, slope)

        group_targets: Dict[int, float] = {}
        for note in group:
            pitch = _pitch(note)
            local_base = center_lane + (pitch - center_pitch) * slope
            global_base = (
                center_lane + (pitch - global_pitch_center) * global_slope
            )
            base = (
                global_weight * global_base
                + (1.0 - global_weight) * local_base
            )
            # 線性位置先算出來，再套非線性鍵盤曲線（中央撐開、邊緣收攏）
            base = _shape_lane(base, settings)
            base = max(0.0, min(settings.total_lanes - 1.0, base))
            hand = int(getattr(note, "hand", 0))
            prev = previous.get(hand)
            if prev is not None:
                prev_time, prev_pitch, prev_lane = prev
                delta_ms = max(0, when - prev_time)
                closeness = exp(-delta_ms / max(1.0, float(settings.close_time_ms)))
                expected = prev_lane + (pitch - prev_pitch) * slope
                base = closeness * expected + (1.0 - closeness) * base
            group_targets[id(note)] = max(
                0.0, min(settings.total_lanes - 1.0, base)
            )
            result[id(note)] = (group_targets[id(note)], slope)

        for hand in (0, 1):
            hand_notes = [note for note in group if int(getattr(note, "hand", 0)) == hand]
            if not hand_notes:
                continue
            previous[hand] = (
                when,
                float(median([_pitch(note) for note in hand_notes])),
                float(median([group_targets[id(note)] for note in hand_notes])),
            )
    return result



def _anchor_pitch_positions(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """前置約束：擺放之前就把「同一音高」的目標位置拉到共同值。

    事後拉齊受限於一堆硬條件（組內不重疊、長押走廊、最高音順序），很多該
    移的移不動。改成在 placement 之前就把 desired 錨定好，後續所有修復就
    是從一個已經一致的版面出發，需要動的量小很多。

    錨點取「±window 內同音高的 desired 中位數」，用 `pitch_anchor_weight`
    做加權混合 —— 保留一點局部適應力，換調時仍然跟得上。
    """
    weight = max(0.0, min(1.0, float(settings.pitch_anchor_weight)))
    if weight <= 0.0:
        return 0
    ordered = sorted(notes, key=_start)
    by_pitch: Dict[int, List[Any]] = {}
    for note in ordered:
        by_pitch.setdefault(_pitch(note), []).append(note)
    window = max(1, int(settings.pitch_anchor_window_ms))

    adjusted = 0
    for items in by_pitch.values():
        if len(items) < 2:
            continue
        times = [_start(note) for note in items]
        centers = [desired[id(note)][0] for note in items]
        for index, note in enumerate(items):
            when = times[index]
            lo = bisect_left(times, when - window)
            hi = bisect_right(times, when + window)
            if hi - lo < 2:
                continue
            anchor = median(centers[lo:hi])
            center, slope = desired[id(note)]
            blended = (1.0 - weight) * center + weight * anchor
            if abs(blended - center) < 0.01:
                continue
            desired[id(note)] = (
                max(0.0, min(settings.total_lanes - 1.0, blended)),
                slope,
            )
            adjusted += 1
    return adjusted



def _pitch_anchor_table(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> Dict[int, List[Tuple[int, float]]]:
    """每個音高在各時間點「應該」落在的鍵道中心（依時間排序的取樣點）。

    前置錨定只影響擺放的起點，之後的修復通道各自自由位移就把它洗掉了。
    這張表讓那些通道在決定要不要接受一次移動時，可以把「偏離錨點多遠」
    算進成本 —— 錨點成為貫穿整個流程的共同語言，而不只是初始值。
    """
    table: Dict[int, List[Tuple[int, float]]] = {}
    for note in sorted(notes, key=_start):
        table.setdefault(_pitch(note), []).append(
            (_start(note), desired[id(note)][0])
        )
    return table


def _anchor_for(
    table: Dict[int, List[Tuple[int, float]]],
    pitch: int,
    when: int,
    settings: SmartChartSettings,
) -> Optional[float]:
    items = table.get(int(pitch))
    if not items or len(items) < 2:
        return None
    window = max(1, int(settings.pitch_anchor_window_ms))
    times = [t for t, _ in items]
    lo = bisect_left(times, when - window)
    hi = bisect_right(times, when + window)
    if hi - lo < 2:
        return None
    return median([c for _t, c in items[lo:hi]])


def _apply_motif_memory(
    groups: Sequence[Sequence[Any]],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """Reuse the lane envelope of repeated four-event pitch/rhythm motifs."""
    reused = 0
    quantum = max(1.0, float(settings.beat_ms) / 16.0)
    for hand in (0, 1):
        history: List[Tuple[Tuple[int, ...], int]] = []
        memory: Dict[Tuple[Tuple[Tuple[int, ...], int], ...], float] = {}
        previous_time: Optional[int] = None
        for group in groups:
            hand_notes = [
                note
                for note in group
                if int(getattr(note, "hand", 0)) == hand
            ]
            if not hand_notes:
                continue
            when = min(_start(note) for note in hand_notes)
            rhythm_bin = (
                0
                if previous_time is None
                else int(round((when - previous_time) / quantum))
            )
            signature = (
                tuple(sorted(_pitch(note) for note in hand_notes)),
                rhythm_bin,
            )
            history.append(signature)
            previous_time = when
            if len(history) < 4:
                continue
            key = tuple(history[-4:])
            current_center = float(
                median([desired[id(note)][0] for note in hand_notes])
            )
            remembered = memory.get(key)
            if remembered is None:
                memory[key] = current_center
                continue
            reused += 1
            delta = 0.8 * (remembered - current_center)
            if abs(delta) < 0.25:
                continue
            for note in hand_notes:
                center, slope = desired[id(note)]
                desired[id(note)] = (
                    max(
                        0.0,
                        min(
                            settings.total_lanes - 1.0,
                            center + delta,
                        ),
                    ),
                    slope,
                )
    return reused


def _limit_hand_motion(
    groups: Sequence[Sequence[Any]],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """Cap unjustified rapid lane jumps while retaining real pitch leaps."""
    limited = 0
    beat_ms = max(80.0, float(settings.beat_ms))
    for hand in (0, 1):
        previous: Optional[Tuple[int, float, float]] = None
        for group in groups:
            hand_notes = [
                note
                for note in group
                if int(getattr(note, "hand", 0)) == hand
            ]
            if not hand_notes:
                continue
            when = min(_start(note) for note in hand_notes)
            pitch_center = float(median([_pitch(note) for note in hand_notes]))
            lane_center = float(
                median([desired[id(note)][0] for note in hand_notes])
            )
            if previous is not None:
                gap_ms = max(1, when - previous[0])
                pitch_delta = abs(pitch_center - previous[1])
                if pitch_delta == 0:
                    base_limit = 0.5
                elif pitch_delta <= 4:
                    base_limit = 2.0
                else:
                    base_limit = min(7.0, 2.0 + pitch_delta * 0.35)
                time_scale = max(1.0, gap_ms / (beat_ms * 0.5))
                allowed = min(10.0, base_limit * time_scale)
                lane_delta = lane_center - previous[2]
                if abs(lane_delta) > allowed:
                    corrected = previous[2] + (
                        allowed if lane_delta > 0 else -allowed
                    )
                    shift = corrected - lane_center
                    for note in hand_notes:
                        center, slope = desired[id(note)]
                        desired[id(note)] = (
                            max(
                                0.0,
                                min(
                                    settings.total_lanes - 1.0,
                                    center + shift,
                                ),
                            ),
                            slope,
                        )
                    lane_center = corrected
                    limited += 1
            previous = (when, pitch_center, lane_center)
    return limited


def _optimize_group_shifts(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> Tuple[int, int, int]:
    """Use dynamic programming to reserve space for nearby pitch extremes.

    Each close-time group receives one integer horizontal shift.  Emission
    cost reserves lanes for lower/higher notes in the surrounding phrase;
    transition cost prevents nearby groups from jumping between unrelated
    layouts.  When both sides need more room than is available, the current
    group's desired centers are compressed before the DP pass.
    """
    if not groups:
        return 0, 0

    ordered_notes = sorted(notes, key=_start)
    starts = [_start(note) for note in ordered_notes]
    global_pitch_lo = min(_pitch(note) for note in ordered_notes)
    global_pitch_hi = max(_pitch(note) for note in ordered_notes)
    global_pitch_span = max(1, global_pitch_hi - global_pitch_lo)
    edge_band = max(
        int(settings.global_edge_band_min_semitones),
        int(round(global_pitch_span * float(settings.global_edge_band_fraction))),
    )
    half_window = max(1, settings.phrase_window_ms // 2)
    edge_lo = float(settings.edge_margin)
    edge_hi = float(settings.total_lanes - 1 - settings.edge_margin)
    anchor_table = _pitch_anchor_table(notes, desired, settings)
    anchor_weight = float(settings.dp_anchor_deviation_weight)
    transformed: List[Dict[int, float]] = []
    reservations: List[Tuple[float, float]] = []
    edge_anchors: List[Tuple[bool, bool]] = []
    group_times: List[int] = []
    compressed_groups = 0
    edge_anchored_groups = 0

    for group in groups:
        when = min(_start(note) for note in group)
        group_times.append(when)
        lo = bisect_left(starts, when - half_window)
        hi = bisect_right(starts, when + half_window)
        nearby = ordered_notes[lo:hi]
        group_pitch_lo = min(_pitch(note) for note in group)
        group_pitch_hi = max(_pitch(note) for note in group)
        # 左右要保留多少空間，必須用和 _local_pitch_maps 同一套「兩手一起、
        # 記憶較長」的音域。只看 ±1 秒的話，另一手一休息，保留量就整個
        # 改變，同一顆音高會被推到不同鍵道。
        mem_lo = bisect_left(starts, when - settings.hand_memory_ms)
        mem_hi = bisect_right(starts, when + settings.hand_memory_ms)
        memory = ordered_notes[mem_lo:mem_hi]
        nearby_pitch_lo = min((_pitch(note) for note in memory), default=group_pitch_lo)
        nearby_pitch_hi = max((_pitch(note) for note in memory), default=group_pitch_hi)
        slope = float(median([desired[id(note)][1] for note in group]))
        left_reserve = max(0.0, (group_pitch_lo - nearby_pitch_lo) * slope)
        right_reserve = max(0.0, (nearby_pitch_hi - group_pitch_hi) * slope)
        reserve_lo = min(edge_hi, edge_lo + left_reserve)
        reserve_hi = max(edge_lo, edge_hi - right_reserve)

        raw = {id(note): float(desired[id(note)][0]) for note in group}
        raw_lo = min(raw.values())
        raw_hi = max(raw.values())
        raw_span = raw_hi - raw_lo
        available_span = max(0.0, reserve_hi - reserve_lo)
        scale = 1.0
        if raw_span > 0.0 and raw_span > available_span:
            scale = max(0.35, available_span / raw_span)
            compressed_groups += 1
        raw_mid = (raw_lo + raw_hi) / 2.0
        squeezed = {
            note_id: raw_mid + (center - raw_mid) * scale
            for note_id, center in raw.items()
        }
        transformed.append(squeezed)
        reservations.append((reserve_lo, reserve_hi))
        group_ids = {id(note) for note in group}
        bottom_hand = int(getattr(min(group, key=_pitch), "hand", 0))
        top_hand = int(getattr(max(group, key=_pitch), "hand", 0))
        nearby_bottom_hand = [
            _pitch(note)
            for note in nearby
            if id(note) not in group_ids
            and int(getattr(note, "hand", 0)) == bottom_hand
        ]
        nearby_top_hand = [
            _pitch(note)
            for note in nearby
            if id(note) not in group_ids
            and int(getattr(note, "hand", 0)) == top_hand
        ]
        prominence = int(settings.local_peak_prominence_semitones)
        local_low = bool(nearby_bottom_hand) and group_pitch_lo <= (
            global_pitch_lo + global_pitch_hi
        ) / 2.0 and (
            group_pitch_lo <= min(nearby_bottom_hand)
            and float(median(nearby_bottom_hand)) - group_pitch_lo >= prominence
        )
        local_high = bool(nearby_top_hand) and group_pitch_hi >= (
            global_pitch_lo + global_pitch_hi
        ) / 2.0 and (
            group_pitch_hi >= max(nearby_top_hand)
            and group_pitch_hi - float(median(nearby_top_hand)) >= prominence
        )
        anchor_left = local_low or (
            group_pitch_lo <= global_pitch_lo + edge_band
            and nearby_pitch_lo >= group_pitch_lo
        )
        anchor_right = local_high or (
            group_pitch_hi >= global_pitch_hi - edge_band
            and nearby_pitch_hi <= group_pitch_hi
        )
        # 雙手同時發聲的組不做邊緣錨定。DP 是「整組剛性平移」，一旦因為
        # 左手觸底而把整組釘到左邊界，同組的右手音也會被一起拖過去；下一
        # 個右手單獨的組沒有這個錨定，同一顆音高就換了鍵道。雙手同組本來
        # 就已經橫跨很寬的範圍，錨定也帶不來多少額外的版面利用。
        if len({int(getattr(note, "hand", 0)) for note in group}) > 1:
            anchor_left = anchor_right = False
        edge_anchors.append((anchor_left, anchor_right))
        if anchor_left or anchor_right:
            edge_anchored_groups += 1

    limit = max(0, int(settings.dp_shift_limit))
    states = list(range(-limit, limit + 1))
    costs: Dict[int, float] = {}
    backrefs: List[Dict[int, int]] = []

    for group_index, group in enumerate(groups):
        centers = transformed[group_index]
        reserve_lo, reserve_hi = reservations[group_index]
        anchor_left, anchor_right = edge_anchors[group_index]
        current_costs: Dict[int, float] = {}
        current_backrefs: Dict[int, int] = {}
        for shift in states:
            shifted_lo = min(centers.values()) + shift
            shifted_hi = max(centers.values()) + shift
            if shifted_lo < 0.0 or shifted_hi > settings.total_lanes - 1:
                continue
            reserve_penalty = (
                max(0.0, reserve_lo - shifted_lo) ** 2
                + max(0.0, shifted_hi - reserve_hi) ** 2
            ) * float(settings.dp_reserve_weight)
            extreme_penalty = 0.0
            if anchor_left:
                extreme_penalty += max(0.0, shifted_lo - edge_lo) ** 2
            if anchor_right:
                extreme_penalty += max(0.0, edge_hi - shifted_hi) ** 2
            extreme_penalty *= float(settings.dp_extreme_edge_weight)
            anchor_penalty = 0.0
            if anchor_weight > 0.0:
                when = group_times[group_index]
                for note in group:
                    target = _anchor_for(anchor_table, _pitch(note), when, settings)
                    if target is None:
                        continue
                    anchor_penalty += abs(
                        centers[id(note)] + shift - target
                    )
                anchor_penalty *= anchor_weight
            emission = (
                reserve_penalty
                + extreme_penalty
                + anchor_penalty
                + float(settings.dp_anchor_weight) * shift * shift
            )
            if not costs:
                current_costs[shift] = emission
                current_backrefs[shift] = 0
                continue
            gap_ms = max(0, group_times[group_index] - group_times[group_index - 1])
            continuity = float(settings.dp_transition_weight) * exp(
                -gap_ms / max(1.0, float(settings.close_time_ms) * 2.0)
            )
            # 循序漸進：整組平移每秒最多移動 map_drift_lanes_per_sec 個鍵道，
            # 超過的部分課重罰。需要換到很遠的音域時，DP 會分好幾個組慢慢
            # 移過去，而不是在一個組之間整段跳過去。真正的音高跳動不受限
            # ——那是 emission 端的 centers 決定的，這裡限的只有整組平移量。
            allowed_step = max(
                0.0, float(settings.map_drift_lanes_per_sec) * gap_ms / 1000.0
            )
            drift_weight = float(settings.dp_drift_weight)

            def transition(previous_shift_value: int, target: int = shift) -> float:
                delta = abs(target - previous_shift_value)
                excess = max(0.0, delta - allowed_step)
                return (
                    continuity * (target - previous_shift_value) ** 2
                    + drift_weight * excess * excess
                )

            previous_shift, previous_cost = min(
                costs.items(),
                key=lambda item: item[1] + transition(item[0]),
            )
            current_costs[shift] = (
                emission + previous_cost + transition(previous_shift)
            )
            current_backrefs[shift] = previous_shift
        if not current_costs:
            # A pathological out-of-range target still gets a safe zero-shift
            # state; final lane packing will clamp it.
            current_costs = {0: min(costs.values(), default=0.0)}
            current_backrefs = {0: min(costs, key=costs.get) if costs else 0}
        costs = current_costs
        backrefs.append(current_backrefs)

    chosen = min(costs, key=costs.get)
    path = [chosen]
    for group_index in range(len(groups) - 1, 0, -1):
        chosen = backrefs[group_index][chosen]
        path.append(chosen)
    path.reverse()

    shifted_groups = 0
    for group, centers, shift in zip(groups, transformed, path):
        if shift:
            shifted_groups += 1
        for note in group:
            desired[id(note)] = (
                max(
                    0.0,
                    min(settings.total_lanes - 1.0, centers[id(note)] + shift),
                ),
                desired[id(note)][1],
            )
    return shifted_groups, compressed_groups, edge_anchored_groups


def _apply_macro_group_contours(
    groups: Sequence[Sequence[Any]],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """Lay out each hand's phrase contour before packing individual notes.

    The state is the center lane of an onset-group/hand, not an individual
    note.  This prevents a dense chord from moving the whole phrase in the
    opposite direction merely because its local collision solution changed.
    """
    adjusted = 0
    lane_max = settings.total_lanes - 1
    for hand in (0, 1):
        events: List[Tuple[int, Sequence[Any], float, float, Dict[int, float]]] = []
        for group in groups:
            hand_notes = [
                note
                for note in group
                if int(getattr(note, "hand", 0)) == hand
            ]
            if not hand_notes:
                continue
            when = min(_start(note) for note in hand_notes)
            pitch_center = float(median([_pitch(note) for note in hand_notes]))
            anchor_center = float(
                median([desired[id(note)][0] for note in hand_notes])
            )
            slope = float(median([desired[id(note)][1] for note in hand_notes]))
            # Preserve the already-computed local compression.  The macro
            # pass moves the group envelope; it must not re-expand its notes.
            offsets = {
                id(note): float(desired[id(note)][0]) - anchor_center
                for note in hand_notes
            }
            events.append(
                (when, hand_notes, pitch_center, anchor_center, offsets)
            )
        if not events:
            continue

        costs: Dict[int, float] = {}
        backrefs: List[Dict[int, int]] = []
        for event_index, event in enumerate(events):
            when, hand_notes, pitch_center, anchor_center, offsets = event
            min_offset = min(offsets.values())
            max_offset = max(offsets.values())
            valid_states = [
                center
                for center in range(settings.total_lanes)
                if center + min_offset >= 0.0
                and center + max_offset <= lane_max
            ]
            if not valid_states:
                valid_states = list(range(settings.total_lanes))
            current_costs: Dict[int, float] = {}
            current_backrefs: Dict[int, int] = {}
            for center in valid_states:
                emission = float(settings.macro_anchor_weight) * (
                    center - anchor_center
                ) ** 2
                if not costs:
                    current_costs[center] = emission
                    current_backrefs[center] = center
                    continue

                previous = events[event_index - 1]
                gap_ms = max(0, when - previous[0])
                pitch_delta = pitch_center - previous[2]
                slope = float(
                    median(
                        [desired[id(note)][1] for note in hand_notes]
                        + [
                            desired[id(note)][1]
                            for note in previous[1]
                        ]
                    )
                )
                expected_delta = max(
                    -8.0, min(8.0, pitch_delta * slope)
                )
                continuity = float(settings.macro_transition_weight) * exp(
                    -gap_ms
                    / max(1.0, float(settings.macro_trend_window_ms))
                )
                candidates: List[Tuple[float, int]] = []
                for previous_center, previous_cost in costs.items():
                    lane_delta = center - previous_center
                    if (
                        gap_ms <= settings.macro_trend_window_ms
                        and abs(pitch_delta)
                        >= float(settings.macro_pitch_threshold)
                        and lane_delta * pitch_delta < 0
                    ):
                        continue
                    candidates.append(
                        (
                            previous_cost
                            + continuity
                            * (lane_delta - expected_delta) ** 2,
                            previous_center,
                        )
                    )
                # Bounds can occasionally make the directional constraint
                # impossible. Keep a finite fallback; the final projection
                # will compress this pair to equality if required.
                if not candidates:
                    candidates = [
                        (
                            previous_cost
                            + continuity
                            * (center - previous_center - expected_delta) ** 2,
                            previous_center,
                        )
                        for previous_center, previous_cost in costs.items()
                    ]
                transition_cost, predecessor = min(candidates)
                current_costs[center] = emission + transition_cost
                current_backrefs[center] = predecessor
            costs = current_costs
            backrefs.append(current_backrefs)

        chosen = min(costs, key=costs.get)
        path = [chosen]
        for event_index in range(len(events) - 1, 0, -1):
            chosen = backrefs[event_index][chosen]
            path.append(chosen)
        path.reverse()

        for event, center in zip(events, path):
            _, hand_notes, _, anchor_center, offsets = event
            if abs(center - anchor_center) >= 0.5:
                adjusted += 1
            for note in hand_notes:
                desired[id(note)] = (
                    max(
                        0.0,
                        min(
                            float(lane_max),
                            float(center) + offsets[id(note)],
                        ),
                    ),
                    desired[id(note)][1],
                )
    return adjusted


def _rank_matched_notes(
    first: Sequence[Any], second: Sequence[Any]
) -> List[Tuple[Any, Any]]:
    """Match nearby chord voices monotonically by pitch rank."""
    left = sorted(first, key=lambda note: (_pitch(note), int(getattr(note, "index", 0))))
    right = sorted(second, key=lambda note: (_pitch(note), int(getattr(note, "index", 0))))
    count = min(len(left), len(right))
    if count <= 0:
        return []
    if count == 1:
        if len(left) == 1:
            return [(left[0], min(right, key=lambda note: abs(_pitch(note) - _pitch(left[0]))))]
        return [(min(left, key=lambda note: abs(_pitch(note) - _pitch(right[0]))), right[0])]

    def sampled(items: Sequence[Any]) -> List[Any]:
        return [
            items[int(round(index * (len(items) - 1) / (count - 1)))]
            for index in range(count)
        ]

    return list(zip(sampled(left), sampled(right)))


def _nearby_voice_pairs(
    notes: Sequence[Any], settings: SmartChartSettings
) -> List[Tuple[Any, Any]]:
    groups = _clusters(notes, settings.onset_tolerance_ms)
    pairs: List[Tuple[Any, Any]] = []
    # Follow each hand's immediately consecutive events.  Comparing every
    # group against every other group in the window over-constrains changing
    # chord sizes (and can create mutually incompatible voice matches).
    for hand in (0, 1):
        hand_events: List[Tuple[int, List[Any]]] = []
        for group in groups:
            hand_notes = [
                note
                for note in group
                if int(getattr(note, "hand", 0)) == hand
            ]
            if hand_notes:
                hand_events.append(
                    (min(_start(note) for note in hand_notes), hand_notes)
                )
        # 不只比對「緊接著的下一個和絃」——那樣兩個事件以上的間隔就完全
        # 沒有人檢查，會出現 82 排在 83 右邊、85 和 97 疊在同一格這種
        # 明顯的錯序（它們中間隔了別的和絃，舊版的配對根本看不到）。
        reach = max(1, int(settings.pitch_order_lookahead))
        for index, (first_start, first) in enumerate(hand_events):
            for step in range(1, reach + 1):
                if index + step >= len(hand_events):
                    break
                second_start, second = hand_events[index + step]
                if second_start - first_start > settings.pitch_trend_window_ms:
                    break
                pairs.extend(_rank_matched_notes(first, second))
    return pairs


def _enforce_nearby_pitch_order(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """Project rank-matched cross-group voices onto pitch-order constraints."""
    voice_pairs = _nearby_voice_pairs(notes, settings)
    repairs = 0
    passes = max(1, int(settings.pitch_order_projection_passes))
    for _ in range(passes):
        changed = 0
        for first, second in voice_pairs:
            first_pitch = _pitch(first)
            second_pitch = _pitch(second)
            if first_pitch == second_pitch:
                continue
            low, high = (
                (first, second)
                if first_pitch < second_pitch
                else (second, first)
            )
            low_center, low_slope = desired[id(low)]
            high_center, high_slope = desired[id(high)]
            if low_center <= high_center:
                continue
            midpoint = (low_center + high_center) / 2.0
            # Keep enough separation to survive odd/even width rounding in
            # the final integer-lane packer.
            desired[id(low)] = (midpoint - 0.75, low_slope)
            desired[id(high)] = (midpoint + 0.75, high_slope)
            repairs += 1
            changed += 1
        if not changed:
            break
    return repairs


def _nearby_pitch_order_violations(
    notes: Sequence[Any], settings: SmartChartSettings
) -> int:
    return len(_violating_voice_pairs(notes, settings))


def _violating_voice_pairs(
    notes: Sequence[Any], settings: SmartChartSettings
) -> List[Tuple[Any, Any]]:
    violations: List[Tuple[Any, Any]] = []
    for first, second in _nearby_voice_pairs(notes, settings):
        if _pitch(first) == _pitch(second):
            continue
        # 同樣以最右格判定，且相等也算違規（見 _pitch_order_violations）
        first_edge = int(first.max_key)
        second_edge = int(second.max_key)
        if (_pitch(first) - _pitch(second)) * (
            first_edge - second_edge
        ) <= 0:
            low, high = (
                (first, second)
                if _pitch(first) < _pitch(second)
                else (second, first)
            )
            violations.append((low, high))
    return violations


def _macro_hand_events(
    groups: Sequence[Sequence[Any]],
    hand: int,
) -> List[Tuple[int, float, float, Sequence[Any]]]:
    events: List[Tuple[int, float, float, Sequence[Any]]] = []
    for group in groups:
        hand_notes = [
            note
            for note in group
            if int(getattr(note, "hand", 0)) == hand
        ]
        if not hand_notes:
            continue
        events.append(
            (
                min(_start(note) for note in hand_notes),
                float(median([_pitch(note) for note in hand_notes])),
                float(
                    median(
                        [
                            (int(note.min_key) + int(note.max_key)) / 2.0
                            for note in hand_notes
                        ]
                    )
                ),
                hand_notes,
            )
        )
    return events


def _top_edge_pairs(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> List[Tuple[Any, Any]]:
    """Return lower/higher top-note pairs that must stay ordered by right edge.

    三條線各自檢查：左手的旋律頂端、右手的旋律頂端，以及**兩手合計的最高
    音**。最後這條是使用者實際在看的那條輪廓線，要求嚴格單調，所以用大得
    多的時間窗（`top_edge_window_ms`）約束，而不是只管相鄰 140ms。
    """
    groups = _clusters(notes, settings.onset_tolerance_ms)
    pairs: List[Tuple[Any, Any]] = []
    for hands in ((0,), (1,), (0, 1)):
        window = (
            settings.top_edge_window_ms
            if len(hands) > 1
            else settings.pitch_trend_window_ms
        )
        events: List[Tuple[int, Any]] = []
        for group in groups:
            hand_notes = [
                note
                for note in group
                if int(getattr(note, "hand", 0)) in hands
            ]
            if not hand_notes:
                continue
            top_pitch = max(_pitch(note) for note in hand_notes)
            top = max(
                (note for note in hand_notes if _pitch(note) == top_pitch),
                key=lambda note: (
                    int(note.max_key),
                    int(note.min_key),
                    int(getattr(note, "index", 0)),
                ),
            )
            events.append((min(_start(note) for note in hand_notes), top))
        for (first_time, first), (second_time, second) in zip(
            events, events[1:]
        ):
            if second_time - first_time > window:
                continue
            if first is second:
                continue
            if _pitch(first) == _pitch(second):
                continue
            low, high = (
                (first, second)
                if _pitch(first) < _pitch(second)
                else (second, first)
            )
            pairs.append((low, high))
    return pairs


def _violating_top_edge_pairs(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> List[Tuple[Any, Any]]:
    return [
        (low, high)
        for low, high in _top_edge_pairs(notes, settings)
        if int(low.max_key) >= int(high.max_key)
    ]


def _enforce_top_edge_targets(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> int:
    """Reserve a strict lane step between consecutive chord-top pitches."""
    pairs = _top_edge_pairs(notes, settings)
    repairs = 0
    for _ in range(max(1, settings.pitch_order_projection_passes)):
        changed = 0
        for low, high in pairs:
            low_center, low_slope = desired[id(low)]
            high_center, high_slope = desired[id(high)]
            if high_center >= low_center + 2.25:
                continue
            midpoint = (low_center + high_center) / 2.0
            desired[id(low)] = (
                max(0.0, midpoint - 1.25),
                low_slope,
            )
            desired[id(high)] = (
                min(settings.total_lanes - 1.0, midpoint + 1.25),
                high_slope,
            )
            repairs += 1
            changed += 1
        if not changed:
            break
    return repairs


def _repair_top_edge_order(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Make higher same-hand chord tops end strictly farther right."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    nearby_map: Dict[int, List[Tuple[Any, Any]]] = {}
    for first, second in _nearby_voice_pairs(notes, settings):
        nearby_map.setdefault(id(first), []).append((first, second))
        nearby_map.setdefault(id(second), []).append((first, second))

    # 進入這道通道時每一組的「度數間距欠缺」。之後只准變好不准變壞 ——
    # 沒有這條線的話，為了最高音輪廓的位移會把同手八度從 5 格擠到 3 格。
    spacing_baseline = {
        id(group): _chord_spacing_deficit(group, settings) for group in groups
    }

    def valid_after_change(changed_notes: Sequence[Any]) -> bool:
        changed_groups = {
            id(note_group[id(note)]): note_group[id(note)]
            for note in changed_notes
        }
        if any(
            _group_overlaps(group) or _pitch_order_violations(group)
            for group in changed_groups.values()
        ):
            return False
        if any(
            _chord_spacing_deficit(group, settings)
            > spacing_baseline.get(key, 0.0)
            for key, group in changed_groups.items()
        ):
            return False
        if not all(
            _range_avoids_hold(
                notes,
                note_group[id(note)],
                note,
                hold_activity,
                int(note.min_key),
                int(note.max_key),
            )
            for note in changed_notes
        ):
            return False
        checked = set()
        for note in changed_notes:
            for first, second in nearby_map.get(id(note), []):
                key = (id(first), id(second))
                if key in checked:
                    continue
                checked.add(key)
                if _pitch(first) == _pitch(second):
                    continue
                first_center = (
                    int(first.min_key) + int(first.max_key)
                ) / 2.0
                second_center = (
                    int(second.min_key) + int(second.max_key)
                ) / 2.0
                if (_pitch(first) - _pitch(second)) * (
                    first_center - second_center
                ) < 0:
                    return False
        return True

    def try_move(changed_notes: Sequence[Any], delta: int) -> bool:
        if not delta:
            return False
        if min(int(note.min_key) + delta for note in changed_notes) < 0:
            return False
        if max(
            int(note.max_key) + delta for note in changed_notes
        ) >= settings.total_lanes:
            return False
        for note in changed_notes:
            note.min_key = int(note.min_key) + delta
            note.max_key = int(note.max_key) + delta
        if valid_after_change(changed_notes):
            return True
        for note in changed_notes:
            note.min_key = int(note.min_key) - delta
            note.max_key = int(note.max_key) - delta
        return False

    repairs = 0
    for _ in range(settings.total_lanes * 3):
        violations = _violating_top_edge_pairs(notes, settings)
        if not violations:
            break
        changed = 0
        for low, high in violations:
            deficit = int(low.max_key) - int(high.max_key) + 1
            # 違規清單是每輪開頭算的，但迴圈中途會動音符：前面幾對修好之後，
            # 這一對可能已經順了。這時 deficit <= 0，而下面的 `old_max - deficit`
            # 是減掉一個負數 —— 音符會被**加寬**，而寬度是難度常數，不該變。
            if deficit <= 0:
                continue
            low_width = int(low.max_key) - int(low.min_key) + 1
            floor = max(1, int(settings.min_note_width))
            if low_width - deficit >= floor:
                old_max = int(low.max_key)
                low.max_key = old_max - deficit
                if valid_after_change((low,)):
                    repairs += 1
                    changed += 1
                    continue
                low.max_key = old_max
            if try_move((high,), deficit):
                repairs += 1
                changed += 1
                continue
            if try_move((low,), -deficit):
                repairs += 1
                changed += 1
                continue
            high_group = note_group[id(high)]
            high_block = [
                note
                for note in high_group
                if int(getattr(note, "hand", 0))
                == int(getattr(high, "hand", 0))
            ]
            if try_move(high_block, deficit):
                repairs += 1
                changed += 1
                continue
            low_group = note_group[id(low)]
            low_block = [
                note
                for note in low_group
                if int(getattr(note, "hand", 0))
                == int(getattr(low, "hand", 0))
            ]
            if try_move(low_block, -deficit):
                repairs += 1
                changed += 1
                continue
            # The melody-top edge outranks the widths of simultaneous chord
            # tones. Collapse the two involved onset groups to free space;
            # their pitch order remains intact because every new lane stays
            # inside its old non-overlapping range.
            narrowed = False
            seen_notes = set()
            for group, side in ((low_group, "high"), (high_group, "low")):
                for note in group:
                    if id(note) in seen_notes:
                        continue
                    seen_notes.add(id(note))
                    if _trim_inner_edge(note, side, settings):
                        repairs += 1
                        narrowed = True
            if narrowed:
                changed += 1
        if not changed:
            # Width is subordinate to the strict top-edge contour. Narrow
            # only the two blocked chord tops, then retry integer movement.
            narrowed = False
            for low, high in violations:
                for note, side in ((low, "high"), (high, "low")):
                    if _trim_inner_edge(note, side, settings):
                        repairs += 1
                        narrowed = True
            if not narrowed:
                break

    # ── 最終強制通道 ──────────────────────────────────────────────
    # 上面的修復要同時滿足「鄰近聲部的中心順序」，而收窄右緣會把中心往左
    # 移 0.5，常常就被那條規則否決 —— 兩條規則直接衝突。最高音的輪廓線是
    # 使用者實際在讀的那條，優先權高於鄰近聲部的中心比較，所以剩下的違規
    # 在這裡放寬中心規則強制修掉；組內不重疊、組內音高順序、長押走廊這三
    # 個硬性正確條件仍然要守住。
    def hard_valid(changed_notes: Sequence[Any]) -> bool:
        changed_groups = {
            id(note_group[id(note)]): note_group[id(note)]
            for note in changed_notes
        }
        if any(
            _group_overlaps(group) or _pitch_order_violations(group)
            for group in changed_groups.values()
        ):
            return False
        return all(
            _range_avoids_hold(
                notes,
                note_group[id(note)],
                note,
                hold_activity,
                int(note.min_key),
                int(note.max_key),
            )
            for note in changed_notes
        )

    for _ in range(64):
        violations = _violating_top_edge_pairs(notes, settings)
        if not violations:
            break
        fixed = False
        for low, high in violations:
            deficit = int(low.max_key) - int(high.max_key) + 1
            if deficit <= 0:
                continue          # 同上：清單過期了，這一對已經順了
            # (a) 收低音那顆的右緣
            width = int(low.max_key) - int(low.min_key) + 1
            if width - deficit >= max(1, int(settings.min_note_width)):
                old_max = int(low.max_key)
                low.max_key = old_max - deficit
                if hard_valid((low,)):
                    repairs += 1
                    fixed = True
                    continue
                low.max_key = old_max
            # (b) 整顆往左移
            if int(low.min_key) - deficit >= 0:
                low.min_key = int(low.min_key) - deficit
                low.max_key = int(low.max_key) - deficit
                if hard_valid((low,)):
                    repairs += 1
                    fixed = True
                    continue
                low.min_key = int(low.min_key) + deficit
                low.max_key = int(low.max_key) + deficit
            # (c) 把高音那顆往右移
            if int(high.max_key) + deficit < settings.total_lanes:
                high.min_key = int(high.min_key) + deficit
                high.max_key = int(high.max_key) + deficit
                if hard_valid((high,)):
                    repairs += 1
                    fixed = True
                    continue
                high.min_key = int(high.min_key) - deficit
                high.max_key = int(high.max_key) - deficit
        if not fixed:
            break
    return repairs


def _repair_small_interval_distinction(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Give nearby small pitch steps distinct centers, using width if blocked."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    activity = _build_hold_activity(groups, settings)

    def valid(note: Any) -> bool:
        group = note_group[id(note)]
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        return _range_avoids_hold(
            notes,
            group,
            note,
            activity,
            int(note.min_key),
            int(note.max_key),
        )

    def try_shift(note: Any, delta: int) -> bool:
        if int(note.min_key) + delta < 0:
            return False
        if int(note.max_key) + delta >= settings.total_lanes:
            return False
        note.min_key = int(note.min_key) + delta
        note.max_key = int(note.max_key) + delta
        if valid(note):
            return True
        note.min_key = int(note.min_key) - delta
        note.max_key = int(note.max_key) - delta
        return False

    repairs = 0
    for _ in range(settings.total_lanes * 2):
        changed = 0
        for first, second in _nearby_voice_pairs(notes, settings):
            pitch_delta = _pitch(first) - _pitch(second)
            if pitch_delta == 0 or abs(pitch_delta) > 4:
                continue
            low, high = (
                (first, second)
                if _pitch(first) < _pitch(second)
                else (second, first)
            )
            low_center = (
                int(low.min_key) + int(low.max_key)
            ) / 2.0
            high_center = (
                int(high.min_key) + int(high.max_key)
            ) / 2.0
            if low_center < high_center:
                continue
            if try_shift(high, 1) or try_shift(low, -1):
                repairs += 1
                changed += 1
                continue
            # With no free lane, make the higher note visually right-heavy or
            # the lower note left-heavy by removing the inner width edge.
            if (int(high.max_key) - int(high.min_key) + 1
                    > max(1, int(settings.min_note_width))):
                old_min = int(high.min_key)
                high.min_key = old_min + 1
                if valid(high):
                    repairs += 1
                    changed += 1
                    continue
                high.min_key = old_min
            if (int(low.max_key) - int(low.min_key) + 1
                    > max(1, int(settings.min_note_width))):
                old_max = int(low.max_key)
                low.max_key = old_max - 1
                if valid(low):
                    repairs += 1
                    changed += 1
                    continue
                low.max_key = old_max
        if not changed:
            break
    repairs += _repair_small_interval_width_cues(notes, groups, settings)
    return repairs


def _repair_small_interval_width_cues(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Show a blocked small pitch step by trimming its inner width edge."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    activity = _build_hold_activity(groups, settings)
    nearby_by_note: Dict[int, List[Tuple[Any, Any]]] = {}
    for first, second in _nearby_voice_pairs(notes, settings):
        nearby_by_note.setdefault(id(first), []).append((first, second))
        nearby_by_note.setdefault(id(second), []).append((first, second))

    def valid(note: Any) -> bool:
        group = note_group[id(note)]
        nearby_order_valid = all(
            _pitch(first) == _pitch(second)
            or (
                (_pitch(first) - _pitch(second))
                * (
                    (
                        int(first.min_key) + int(first.max_key)
                    ) / 2.0
                    - (
                        int(second.min_key) + int(second.max_key)
                    ) / 2.0
                )
                >= 0
            )
            for first, second in nearby_by_note.get(id(note), ())
        )
        return (
            _group_overlaps(group) == 0
            and _pitch_order_violations(group) == 0
            and nearby_order_valid
            and _range_avoids_hold(
                notes,
                group,
                note,
                activity,
                int(note.min_key),
                int(note.max_key),
            )
        )

    repairs = 0
    # A long chromatic run can pass equality along until it reaches an edge.
    # Finish remaining equal centers with width cues instead of more movement.
    for _ in range(4):
        changed = 0
        for first, second in _nearby_voice_pairs(notes, settings):
            if _pitch(first) == _pitch(second) or abs(
                _pitch(first) - _pitch(second)
            ) > 4:
                continue
            low, high = (
                (first, second)
                if _pitch(first) < _pitch(second)
                else (second, first)
            )
            low_center = (
                int(low.min_key) + int(low.max_key)
            ) / 2.0
            high_center = (
                int(high.min_key) + int(high.max_key)
            ) / 2.0
            if low_center < high_center:
                continue
            # 「前後兩顆音」的小度數不可以靠收窄來表現 —— 官方 real 單手同時
            # 1 顆／2 顆的收窄率是 0.0% ／ 0.2%，收窄只出現在同時 3 顆以上
            # （k=3 4.5%、k=4 88.4%）。所以只有本來就在密和絃裡的音符才准收，
            # 其餘一律改用位移，位移不動就維持原寬度。
            if (_may_narrow_for_cue(low, note_group[id(low)], settings)
                    and int(low.max_key) - int(low.min_key) + 1
                    > max(1, int(settings.min_note_width))):
                old_max = int(low.max_key)
                low.max_key = old_max - 1
                if valid(low):
                    repairs += 1
                    changed += 1
                    continue
                low.max_key = old_max
            if (_may_narrow_for_cue(high, note_group[id(high)], settings)
                    and int(high.max_key) - int(high.min_key) + 1
                    > max(1, int(settings.min_note_width))):
                old_min = int(high.min_key)
                high.min_key = old_min + 1
                if valid(high):
                    repairs += 1
                    changed += 1
                    continue
                high.min_key = old_min
            # 加寬優先往左（低音側）長：那樣只有低音那顆的左緣在動，兩顆的
            # 右緣都不變。往右長會推到高音那顆的右緣 —— 那條線是排序權威。
            #
            # 但**加寬不可以超過該難度的標準寬度**。官方 real 的寬度是常數 3
            # （97%，其餘是收窄成 2 的密和絃），沒有任何一顆比 3 寬。這裡原本
            # 沒有上限，四輪跑下來同一顆可以一直長，實測會生出寬度 4~7 的音符。
            # 撞在一起的小度數本來就允許擺不開——官方 k>=5 是讓鍵道重疊，不是
            # 把音符拉寬。
            wide = max(1, int(settings.normal_width))
            if int(low.min_key) > 0 and _width(low) < wide:
                old_min = int(low.min_key)
                low.min_key = old_min - 1
                if valid(low):
                    repairs += 1
                    changed += 1
                    continue
                low.min_key = old_min
            if int(high.max_key) + 1 < settings.total_lanes and _width(high) < wide:
                old_max = int(high.max_key)
                high.max_key = old_max + 1
                if valid(high):
                    repairs += 1
                    changed += 1
                    continue
                high.max_key = old_max
        if not changed:
            break
    return repairs


def _small_interval_unresolved(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> int:
    unresolved = 0
    for first, second in _nearby_voice_pairs(notes, settings):
        if _pitch(first) == _pitch(second) or abs(
            _pitch(first) - _pitch(second)
        ) > 4:
            continue
        first_center = (
            int(first.min_key) + int(first.max_key)
        ) / 2.0
        second_center = (
            int(second.min_key) + int(second.max_key)
        ) / 2.0
        unresolved += (_pitch(first) - _pitch(second)) * (
            first_center - second_center
        ) <= 0
    return unresolved


def _macro_trend_violations(
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> List[Tuple[Sequence[Any], Sequence[Any]]]:
    violations: List[Tuple[Sequence[Any], Sequence[Any]]] = []
    for hand in (0, 1):
        events = _macro_hand_events(groups, hand)
        for first, second in zip(events, events[1:]):
            if second[0] - first[0] > settings.macro_trend_window_ms:
                continue
            pitch_delta = second[1] - first[1]
            if abs(pitch_delta) < max(
                3.0, float(settings.macro_pitch_threshold)
            ):
                continue
            if pitch_delta * (second[2] - first[2]) < 0:
                low, high = (
                    (first[3], second[3])
                    if first[1] < second[1]
                    else (second[3], first[3])
                )
                violations.append((low, high))
    return violations


def _repair_macro_group_contours(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Shift a hand's complete chord block to preserve phrase direction."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    pair_map: Dict[int, List[Tuple[Any, Any]]] = {}
    for first, second in _nearby_voice_pairs(notes, settings):
        pair_map.setdefault(id(first), []).append((first, second))
        pair_map.setdefault(id(second), []).append((first, second))

    def block_center(block: Sequence[Any]) -> float:
        return float(
            median(
                [
                    (int(note.min_key) + int(note.max_key)) / 2.0
                    for note in block
                ]
            )
        )

    def block_is_valid(block: Sequence[Any]) -> bool:
        group = note_group[id(block[0])]
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        if not all(
            _range_avoids_hold(
                notes,
                group,
                note,
                hold_activity,
                int(note.min_key),
                int(note.max_key),
            )
            for note in block
        ):
            return False
        checked = set()
        for note in block:
            for first, second in pair_map.get(id(note), []):
                key = (id(first), id(second))
                if key in checked:
                    continue
                checked.add(key)
                if _pitch(first) == _pitch(second):
                    continue
                first_center = (
                    int(first.min_key) + int(first.max_key)
                ) / 2.0
                second_center = (
                    int(second.min_key) + int(second.max_key)
                ) / 2.0
                if (_pitch(first) - _pitch(second)) * (
                    first_center - second_center
                ) < 0:
                    return False
        return True

    def try_shift(block: Sequence[Any], delta: int) -> bool:
        if not delta:
            return True
        if min(int(note.min_key) + delta for note in block) < 0:
            return False
        if max(int(note.max_key) + delta for note in block) >= settings.total_lanes:
            return False
        for note in block:
            note.min_key = int(note.min_key) + delta
            note.max_key = int(note.max_key) + delta
        if block_is_valid(block):
            return True
        for note in block:
            note.min_key = int(note.min_key) - delta
            note.max_key = int(note.max_key) - delta
        return False

    repairs = 0
    for _ in range(settings.total_lanes * 2):
        violations = _macro_trend_violations(groups, settings)
        if not violations:
            break
        changed = 0
        for low, high in violations:
            distance = max(
                1, int(block_center(low) - block_center(high) + 0.999)
            )
            low_room = min(int(note.min_key) for note in low)
            high_room = settings.total_lanes - 1 - max(
                int(note.max_key) for note in high
            )
            low_delta = -min(distance, low_room)
            high_delta = min(distance, high_room)
            # Prefer the side with more free space; this propagates a contour
            # through dense chains instead of repeatedly swapping one pair.
            choices = (
                ((high, high_delta), (low, low_delta))
                if high_room >= low_room
                else ((low, low_delta), (high, high_delta))
            )
            if any(try_shift(block, delta) for block, delta in choices):
                repairs += 1
                changed += 1
        if not changed:
            narrowed = False
            for low, high in violations:
                for note in list(low) + list(high):
                    # 人工譜與官方譜都沒有寬度 1，收窄一次一格、到下限就停
                    if _trim_inner_edge(note, "high", settings):
                        repairs += 1
                        narrowed = True
            if not narrowed:
                break
    return repairs


def _shrink_to_capacity(
    widths: List[int], capacity: int, min_width: int = 1
) -> int:
    forced = 0
    floor = max(1, int(min_width))
    while sum(widths) > capacity:
        widest = max(widths)
        if widest <= floor:
            break
        # Shrink from the middle voices first; outer voices keep a stronger
        # visual anchor when there is enough room.
        candidates = [i for i, width in enumerate(widths) if width == widest]
        index = min(candidates, key=lambda i: abs(i - (len(widths) - 1) / 2.0))
        widths[index] -= 1
        if widths[index] == 1:
            forced += 1
    return forced


def _chord_pair_gap(interval: int, settings: SmartChartSettings) -> int:
    """同手、同時發聲的兩顆音之間該留幾個「空格」。

    官方 real 的階梯表量出來其實是**間隙**而不是中心距：≥8 半音有 85.7% 是
    間隙 2、5~7 半音是間隙 1、≤4 半音是貼合（間隙 0）。用間隙表達才和寬度
    無關 —— 收窄成寬度 2 之後同一個度數仍然留同樣的空格，中心距自然縮小，
    度數層次才做得出來（寬度 3 時 ≤4 半音全部擠在中心距 3，看不出差別）。
    """
    interval = abs(int(interval))
    if interval <= int(settings.chord_pair_close_semitones):
        return 0
    if interval <= int(settings.chord_pair_mid_semitones):
        return 1
    return 2


def _chord_pair_lanes(
    interval: int,
    settings: SmartChartSettings,
    first_width: Optional[int] = None,
    second_width: Optional[int] = None,
) -> float:
    """同手、同時發聲的兩顆音該有的中心距 = 兩邊半寬相加 + 該留的空格。

    不給寬度時退回 `normal_width`，數值和舊的階梯表完全一樣（3 / 4 / 5）。
    """
    first = int(first_width or settings.normal_width)
    second = int(second_width or settings.normal_width)
    return (first + second) / 2.0 + _chord_pair_gap(interval, settings)


def _chord_flush_cost(
    note: Any,
    group: Sequence[Any],
    settings: SmartChartSettings,
    start: int,
    width: int,
) -> float:
    """把這顆放在 `start` 會和同手和絃的左右鄰居各拉開幾格空隙。

    只算同手、同一組（同時發聲）而且該手有 3 顆以上的情形 —— 官方那種和絃
    是完全貼合的（k=3 貼合率 84%、外圍中心距固定 6 格）。兩顆的和絃照度數
    留空隙，所以不在這裡罰。
    """
    hand = int(getattr(note, "hand", 0))
    mates = [
        other for other in group
        if other is not note and int(getattr(other, "hand", 0)) == hand
    ]
    if len(mates) + 1 < int(settings.chord_flush_min_notes):
        return 0.0
    end = start + width - 1
    left: Optional[int] = None
    right: Optional[int] = None
    for other in mates:
        other_lo = int(other.min_key)
        other_hi = int(other.max_key)
        if other_hi < start:
            gap = start - other_hi - 1
            if left is None or gap < left:
                left = gap
        elif other_lo > end:
            gap = other_lo - end - 1
            if right is None or gap < right:
                right = gap
    return float(max(0, left or 0) + max(0, right or 0))


def _hand_sizes(hands: Sequence[int]) -> Dict[int, int]:
    sizes: Dict[int, int] = {}
    for hand in hands:
        sizes[hand] = sizes.get(hand, 0) + 1
    return sizes


def _chord_advance(
    index: int,
    pitches: Sequence[int],
    hands: Sequence[int],
    widths: Sequence[int],
    sizes: Dict[int, int],
    settings: SmartChartSettings,
) -> float:
    """第 index 顆相對前一顆該前進多少鍵道（含度數留白），與擺放邏輯共用一份。

    同手 3 顆以上原則上貼合，但**度數不夠時要疊上去**：官方量到的相鄰推進量
    是 0 半音→0 格、1 半音→0 格、2 半音→2 格、3 半音以上→3 格（貼合）。
    以前這裡不看音程一律回傳滿寬，結果三顆擠在 2~4 個半音內的和絃還是各佔
    3 格、攤成 9 格寬；官方同樣的情況只佔 3~4 格（k=3、跨度 2~4 半音的中位數
    是 3 格）。重疊不是錯誤而是官方的主要手段——同手和絃有 28% 的組別鍵道
    重疊，k=3 跨度 3~4 半音更高達 95%。

    同手 2 顆照階梯表留空隙，跨手則額外撐開交界。
    """
    flush = float(widths[index - 1])
    same_hand = hands[index] == hands[index - 1]
    interval = abs(pitches[index] - pitches[index - 1])
    if same_hand and sizes.get(hands[index], 0) >= int(settings.chord_flush_min_notes):
        return flush
    if interval <= 0:
        return flush
    if same_hand:
        need = _chord_pair_lanes(
            interval, settings, widths[index - 1], widths[index]
        )
    else:
        rate = float(settings.min_octave_center_distance) / 12.0
        need = min(
            float(settings.max_interval_center_distance),
            max(1.0, interval * rate),
        )
        need = max(
            need + float(settings.hand_boundary_margin),
            float(settings.hand_boundary_min_distance),
        )
    return max(flush, need + (widths[index - 1] - widths[index]) / 2.0)


def _required_group_span(
    ordered_notes: Sequence[Any],
    widths: Sequence[int],
    settings: SmartChartSettings,
) -> float:
    """Lanes the pitch-ordered group needs at these widths, interval gaps included.

    Mirrors the advance rule in :func:`_place_hand` so the caller can tell
    whether full-width notes still fit before reaching for narrowing.
    """
    if not ordered_notes:
        return 0.0
    pitches = [_pitch(note) for note in ordered_notes]
    hands = [int(getattr(note, "hand", 0)) for note in ordered_notes]
    sizes = _hand_sizes(hands)
    span = float(widths[0])
    for index in range(1, len(ordered_notes)):
        span += _chord_advance(index, pitches, hands, widths, sizes, settings)
    return span


def _hand_span_wants_narrowing(
    hand_notes: Sequence[Any], settings: SmartChartSettings
) -> bool:
    """這隻手的「度數不夠」到該收窄了嗎。

    收窄率的高峰不在最擠的地方，而在**中間**那一段跨度。量這個曲庫的手寫譜，
    同手 3 顆時各跨度的收窄率是 0~4 半音 12%、5~6 半音 28%、7~11 半音 33%、
    12+ 半音 13% —— 峰值在 7~11。道理是：跨度太小時收窄也擠不出位置（那種
    要靠位置表現），跨度夠大時本來就放得下，只有中間那段是「音高分得開、
    鍵道卻不夠用」，收窄才真的解決問題。
    """
    if not hand_notes:
        return False
    pitches = [_pitch(note) for note in hand_notes]
    span = max(pitches) - min(pitches)
    if len(hand_notes) < 2:
        return False
    # 兩顆和三顆以上用同一條帶子：度數短（跨度 < narrow_span_min）就不收窄，
    # 那種靠位置表現得出來。真的排不下的情形另外由容量預算 allow_close 處理。
    return (int(settings.narrow_span_min) <= span
            <= int(settings.narrow_span_max))


def _initial_hand_widths_full(
    by_hand: Dict[int, Sequence[Any]],
    settings: SmartChartSettings,
) -> Dict[int, int]:
    """Per-note width with the close-interval rule disabled (density still applies)."""
    out: Dict[int, int] = {}
    for hand_notes in by_hand.values():
        for note, width in zip(
            hand_notes, _initial_hand_widths(hand_notes, settings, False)
        ):
            out[id(note)] = width
    return out


def _initial_hand_widths(
    hand_notes: Sequence[Any],
    settings: SmartChartSettings,
    allow_close_narrow: bool = True,
) -> List[int]:
    """Choose chord widths from density and adjacent pitch intervals."""
    if not hand_notes:
        return []
    # 官方 real 的收窄率是「剛好 4 音」才高：k=1/2/3 是 0.0/0.2/4.5%，
    # k=4 衝到 88.4%，但 k=5 又掉回 24.2%（75.8% 維持寬度 3）。5 音以上
    # 官方不再靠收窄擠空間，而是讓音符重疊，所以這裡也只在剛好 4 音時收窄，
    # 更密的和絃交給容量預算 _shrink_to_capacity 決定。
    if len(hand_notes) == settings.dense_hand_threshold:
        return [settings.dense_width] * len(hand_notes)
    widths = [settings.normal_width] * len(hand_notes)
    if not allow_close_narrow:
        return widths
    pitches = [_pitch(note) for note in hand_notes]
    if max(pitches) - min(pitches) >= 12:
        return widths
    ordered_indices = sorted(
        range(len(hand_notes)),
        key=lambda index: (
            _pitch(hand_notes[index]),
            _start(hand_notes[index]),
            int(getattr(hand_notes[index], "index", 0)),
        ),
    )
    span = max(pitches) - min(pitches)
    if (int(settings.narrow_whole_span_min) <= span
            <= int(settings.narrow_whole_span_max)):
        # 整組收窄：跨度落在「排不太下、但度數又分得開」那一段時，整隻手
        # 一起收成寬度 2 —— 寬度 3 時 36/40/43 這種七度內的和絃會佔滿 9 格，
        # 和跨了一個八度的和絃長得一模一樣，度數完全看不出來。
        # 跨度比 narrow_whole_span_min 還短的就不收：那種有空間、用位置排。
        return [settings.dense_width] * len(hand_notes)
    threshold = max(0, int(settings.close_chord_interval_semitones))
    for first_index, second_index in zip(
        ordered_indices, ordered_indices[1:]
    ):
        if (
            _pitch(hand_notes[second_index])
            - _pitch(hand_notes[first_index])
            <= threshold
        ):
            widths[first_index] = min(
                widths[first_index], settings.dense_width
            )
            widths[second_index] = min(
                widths[second_index], settings.dense_width
            )
    return widths


def _place_hand(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    widths: List[int],
    lane_lo: int,
    lane_hi: int,
    settings: Optional[SmartChartSettings] = None,
) -> None:
    if not notes:
        return
    settings = settings or SmartChartSettings()
    ordered = sorted(
        zip(notes, widths),
        key=lambda item: (_pitch(item[0]), _start(item[0]), int(getattr(item[0], "index", 0))),
    )
    starts: List[int] = []
    placed_widths: List[int] = []
    for note, width in ordered:
        wanted = int(round(desired[id(note)][0] - (width - 1) / 2.0))
        starts.append(max(lane_lo, min(lane_hi - width + 1, wanted)))
        placed_widths.append(width)

    # Project desired positions onto monotonically ordered, disjoint ranges.
    #
    # 只要求「不重疊」的話，同時發聲的音符會被排成肩並肩，中心距剛好等於
    # 音符寬度 —— 一個八度就變成 3 格。人工譜是照度數留空隙的（八度 5~6
    # 格），所以這裡除了不重疊，還要求相鄰兩顆的中心距至少符合音程。
    pitches = [_pitch(note) for note, _ in ordered]
    hands = [int(getattr(note, "hand", 0)) for note, _ in ordered]
    sizes = _hand_sizes(hands)
    cursor = lane_lo
    for index in range(len(starts)):
        floor_start = cursor
        if index > 0:
            # 前進量與 _required_group_span 共用 _chord_advance：同手 3 顆以上
            # 依音程決定（度數不夠就疊上去）、同手 2 顆照官方階梯表留空隙、
            # 跨手額外撐開交界。
            advance = _chord_advance(
                index, pitches, hands, placed_widths, sizes, settings
            )
            # 地板取「上一顆右緣」與「上一顆起點 + 推進量」的較大者：同手
            # 音符不重疊。官方是靠重疊來擠空間（28% 的組別鍵道重疊），但
            # 這個曲庫的手寫譜幾乎不重疊（8 首實測 0.0~4.6%），改用收窄，
            # 所以這裡維持不重疊。
            floor_start = max(
                floor_start,
                int(round(starts[index - 1] + advance)),
            )
        # 貼齊：desired 只比「音程對應的間距」寬一點點時就吸回去。官方 real
        # 有 50% 的八度剛好隔 5 格，分布是尖峰而不是散開的。
        if (index > 0 and floor_start > cursor
                and 0 < starts[index] - floor_start
                <= settings.interval_snap_tolerance):
            starts[index] = floor_start
        starts[index] = max(starts[index], floor_start)
        cursor = starts[index] + placed_widths[index]
    if cursor - 1 > lane_hi:
        starts[-1] = lane_hi - placed_widths[-1] + 1
        for index in range(len(starts) - 2, -1, -1):
            starts[index] = min(
                starts[index],
                starts[index + 1] - placed_widths[index],
            )
        # 真的塞不下時，官方的做法是讓音符在鍵道上重疊，而不是排到鍵盤外面。
        # 實測官方 real：單手同時 5 音有 72.6% 鍵道重疊、6 音 100% 重疊，
        # 但沒有任何一顆超出 0~27。所以這裡每一顆都夾回鍵盤範圍內，多出來
        # 的音符就疊在邊緣 —— 音高順序（起點單調不減）仍然保持。
        cursor = lane_lo
        for index in range(len(starts)):
            start = max(starts[index], cursor)
            start = min(start, lane_hi - placed_widths[index] + 1)
            starts[index] = max(start, lane_lo)
            cursor = starts[index] + placed_widths[index]

    for (note, _), start, width in zip(ordered, starts, placed_widths):
        note.min_key = int(start)
        note.max_key = int(start + width - 1)


def _place_group_jointly(
    group: Sequence[Any],
    targets: Dict[int, Tuple[float, float]],
    width_by_note: Dict[int, int],
    settings: SmartChartSettings,
) -> None:
    """把整組（含左右手）依音高一起排進整條鍵道。

    舊做法是先在左右手之間切一條界線、兩手各自壓縮進自己那半邊，結果同樣
    的半音距離在兩手會對應到差很多的鍵道距離（實測跨手只有同手的 0.45 倍）。
    改成兩手共用同一套 pitch→lane 映射、一起依音高排序放置：不重疊由「排進
    互不相交的鍵道區間」保證，而不是靠切一半。
    """
    ordered = sorted(
        group,
        key=lambda note: (_pitch(note), _start(note), int(getattr(note, "index", 0))),
    )
    if not ordered:
        return
    widths = [width_by_note[id(note)] for note in ordered]
    _place_hand(ordered, targets, widths, 0, settings.total_lanes - 1, settings)


def _place_hand_avoiding_obstacles(
    notes: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    widths: List[int],
    lane_lo: int,
    lane_hi: int,
    obstacles: Sequence[Tuple[int, int]],
) -> None:
    """Pack a hand around active hold lanes when a feasible path exists."""
    if not notes or not obstacles:
        _place_hand(notes, desired, widths, lane_lo, lane_hi)
        return
    ordered = sorted(
        zip(notes, widths),
        key=lambda item: (
            _pitch(item[0]),
            _start(item[0]),
            int(getattr(item[0], "index", 0)),
        ),
    )
    layers: List[Dict[int, Tuple[float, Optional[int]]]] = []
    for index, (note, width) in enumerate(ordered):
        wanted_center = float(desired[id(note)][0])
        feasible = []
        for start in range(lane_lo, lane_hi - width + 2):
            end = start + width - 1
            if any(start <= block_hi and end >= block_lo for block_lo, block_hi in obstacles):
                continue
            feasible.append(start)
        current: Dict[int, Tuple[float, Optional[int]]] = {}
        for start in feasible:
            center = start + (width - 1) / 2.0
            emission = (center - wanted_center) ** 2
            if index == 0:
                current[start] = (emission, None)
                continue
            previous_width = ordered[index - 1][1]
            candidates = [
                (cost + emission, previous_start)
                for previous_start, (cost, _) in layers[-1].items()
                if previous_start + previous_width <= start
            ]
            if candidates:
                current[start] = min(candidates)
        if not current:
            _place_hand(notes, desired, widths, lane_lo, lane_hi)
            return
        layers.append(current)

    start = min(layers[-1], key=lambda item: layers[-1][item][0])
    starts = [start]
    for index in range(len(layers) - 1, 0, -1):
        predecessor = layers[index][starts[-1]][1]
        if predecessor is None:
            _place_hand(notes, desired, widths, lane_lo, lane_hi)
            return
        starts.append(predecessor)
    starts.reverse()
    for (note, width), start in zip(ordered, starts):
        note.min_key = int(start)
        note.max_key = int(start + width - 1)


def _place_group_avoiding_hold_obstacles(
    notes: Sequence[Any],
    group: Sequence[Any],
    desired: Dict[int, Tuple[float, float]],
    widths: Sequence[int],
    activity: Dict[Tuple[int, int], Sequence[Any]],
    settings: SmartChartSettings,
) -> bool:
    """Pack both hands together when one hand is trapped by the other."""
    width_by_id = {
        id(note): int(width)
        for note, width in zip(group, widths)
    }
    ordered = sorted(
        group,
        key=lambda note: (
            0 if int(getattr(note, "hand", 0)) == 1 else 1,
            _pitch(note),
            int(getattr(note, "index", 0)),
        ),
    )
    layers: List[Dict[int, Tuple[float, Optional[int]]]] = []
    for index, note in enumerate(ordered):
        width = width_by_id[id(note)]
        wanted_center = float(desired[id(note)][0])
        feasible: List[int] = []
        for start in range(0, settings.total_lanes - width + 1):
            end = start + width - 1
            if _range_avoids_hold(
                notes, group, note, activity, start, end
            ):
                feasible.append(start)
        current: Dict[int, Tuple[float, Optional[int]]] = {}
        for start in feasible:
            center = start + (width - 1) / 2.0
            emission = (center - wanted_center) ** 2
            if index == 0:
                current[start] = (emission, None)
                continue
            previous_width = width_by_id[id(ordered[index - 1])]
            candidates = [
                (cost + emission, previous_start)
                for previous_start, (cost, _) in layers[-1].items()
                if previous_start + previous_width <= start
            ]
            if candidates:
                current[start] = min(candidates)
        if not current:
            return False
        layers.append(current)
    start = min(layers[-1], key=lambda item: layers[-1][item][0])
    starts = [start]
    for index in range(len(layers) - 1, 0, -1):
        predecessor = layers[index][starts[-1]][1]
        if predecessor is None:
            return False
        starts.append(predecessor)
    starts.reverse()
    for note, start in zip(ordered, starts):
        width = width_by_id[id(note)]
        note.min_key = int(start)
        note.max_key = int(start + width - 1)
    return True


def _repack_groups_at_targets(
    groups: Sequence[Sequence[Any]],
    targets: Dict[int, Tuple[float, float]],
    settings: SmartChartSettings,
) -> None:
    """Repack existing widths around corrected centers without deleting notes."""
    for group in groups:
        by_hand = {
            hand: sorted(
                [
                    note
                    for note in group
                    if int(getattr(note, "hand", 0)) == hand
                ],
                key=lambda note: (
                    _pitch(note),
                    _start(note),
                    int(getattr(note, "index", 0)),
                ),
            )
            for hand in (0, 1)
        }
        width_by_note = {
            id(note): int(note.max_key) - int(note.min_key) + 1
            for hand_notes in by_hand.values()
            for note in hand_notes
        }
        _place_group_jointly(group, targets, width_by_note, settings)


def _may_narrow_for_cue(
    note: Any, group: Sequence[Any], settings: SmartChartSettings
) -> bool:
    """這顆音符可不可以為了「表現前後小度數」而被收窄。

    官方 real 的收窄完全集中在同時發聲的密和絃：單手同時 1/2/3/4 顆的收窄率
    是 0.0% / 0.2% / 4.5% / 88.4%。孤立的一顆或兩顆音絕對不會為了顯示半音差
    而變窄，那種差別是用位置做的。
    """
    hand = int(getattr(note, "hand", 0))
    same_hand = sum(
        1 for other in group if int(getattr(other, "hand", 0)) == hand
    )
    return same_hand >= int(settings.chord_flush_min_notes)


def _trim_inner_edge(note: Any, side: str, settings: SmartChartSettings) -> bool:
    """把音符往內收一格來讓出中心位置，但不會低於最小寬度。

    舊版是直接壓成寬度 1（`max_key = min_key`）。人工譜面裡沒有寬度 1 的
    音符，最窄就是 2，所以改成一次只收一格、收到下限就停。外層本來就是
    迭代修復，收不動時會自動改用位移的手段。
    """
    floor = max(1, int(settings.min_note_width))
    width = int(note.max_key) - int(note.min_key) + 1
    if width <= floor:
        return False
    if side == "high":
        note.max_key = int(note.max_key) - 1
    else:
        note.min_key = int(note.min_key) + 1
    return True



def _octave_lanes_at(edge: float, settings: SmartChartSettings) -> float:
    """某個鍵道位置上，一個八度應該佔幾個鍵道。

    量人工譜得到：中央約 6 個、靠邊緣收到 2.5 個，中間以二次曲線過渡
    （這正是 `_shape_lane` 那條三次曲線的導數輪廓）。
    """
    center = (settings.total_lanes - 1) / 2.0
    u = max(-1.0, min(1.0, (float(edge) - center) / max(1e-6, center)))
    return (
        float(settings.octave_lanes_center)
        + (float(settings.octave_lanes_edge) - float(settings.octave_lanes_center))
        * u * u
    )


def _expected_top_step(edge: float, pitch_delta: int, settings: SmartChartSettings) -> int:
    """相鄰最高音之間，右緣「應該」位移幾個鍵道。

    人工譜的規律：半音一律只差 1 個鍵道、全音也是 1、三四度 2、五到七度
    3、八到十一度 4、八度 5~6。也就是 |Δ音高| × 該位置的每半音鍵道數，
    下限 1（音高有動，位置就必須動，這是嚴格排序的要求）。
    """
    del edge  # 步進量用固定比例，不套邊緣衰減（見下方說明）
    # 靜態映射在鍵盤邊緣是壓縮的，但「相鄰最高音的步進量」量出來幾乎是
    # 固定比例：人工譜的中位值是 半音→1、全音→1、三四度→2、五七度→3、
    # 八到十一度→4、八度→6，換算約 0.5 鍵道/半音，下限 1。
    step = abs(int(pitch_delta)) * float(settings.top_step_lanes_per_semitone)
    return max(1, int(step + 0.5))


def _repair_top_edge_proportion(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """讓相鄰最高音的右緣位移量與音高間隔成比例。

    只有順序對還不夠：半音級的一步被拉開 4~6 個鍵道，看起來就像跳了一個
    大跳。這裡把位移量修到 `_expected_top_step` 的容差內，但絕不破壞既有
    的嚴格排序（方向必須維持），也不動組內重疊 / 組內音高順序 / 長押走廊。
    """
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    tolerance = max(0, int(settings.top_step_tolerance))

    def hard_valid(note: Any) -> bool:
        group = note_group[id(note)]
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        return _range_avoids_hold(
            notes, group, note, hold_activity,
            int(note.min_key), int(note.max_key),
        )

    def top_events() -> List[Tuple[int, Any]]:
        events: List[Tuple[int, Any]] = []
        for group in groups:
            top_pitch = max(_pitch(note) for note in group)
            top = max(
                (note for note in group if _pitch(note) == top_pitch),
                key=lambda note: (int(note.max_key), int(note.min_key)),
            )
            events.append((min(_start(note) for note in group), top))
        return events

    repairs = 0
    for _ in range(8):
        changed = 0
        events = top_events()
        for (first_time, first), (second_time, second) in zip(events, events[1:]):
            if second_time - first_time > settings.top_edge_window_ms:
                continue
            pitch_delta = _pitch(second) - _pitch(first)
            if pitch_delta == 0:
                continue
            first_edge = int(first.max_key)
            second_edge = int(second.max_key)
            actual = second_edge - first_edge
            if actual == 0:
                continue                      # 順序問題交給 _repair_top_edge_order
            want = _expected_top_step(
                (first_edge + second_edge) / 2.0, pitch_delta, settings
            )
            signed_want = want if pitch_delta > 0 else -want
            if abs(actual - signed_want) <= tolerance:
                continue
            delta = signed_want - actual      # second 需要移動多少
            if int(second.min_key) + delta < 0:
                continue
            if int(second.max_key) + delta >= settings.total_lanes:
                continue
            # 移動後仍必須維持嚴格排序的方向
            if (second_edge + delta - first_edge) * pitch_delta <= 0:
                continue
            second.min_key = int(second.min_key) + delta
            second.max_key = int(second.max_key) + delta
            if hard_valid(second):
                repairs += 1
                changed += 1
            else:
                second.min_key = int(second.min_key) - delta
                second.max_key = int(second.max_key) - delta
        if not changed:
            break
    return repairs



def _backtrack_top_contour(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """回溯修正最高音輪廓：卡住的時候往前改，而不是只硬擠當下這顆。

    前面的音符一旦放錯位置，後面即使左右都有空位也很難救 —— 因為每個修復
    通道都只動「當下這一顆」。這裡改成在一個回看視窗內做局部搜尋：對每個
    有缺陷的相鄰配對，往前 `backtrack_lookback` 個事件逐一嘗試位移，取能讓
    視窗總分下降最多的那一步。

    分數的權重讓「高音優先」：音高越高的配對權重越大，所以必須妥協時會犧
    牲低音那一側，保住高音的輪廓。
    """
    if not groups:
        return 0
    events: List[Tuple[int, Any, Sequence[Any]]] = []
    for group in groups:
        top_pitch = max(_pitch(note) for note in group)
        top = max(
            (note for note in group if _pitch(note) == top_pitch),
            key=lambda note: (int(note.max_key), int(note.min_key)),
        )
        events.append((min(_start(note) for note in group), top, group))
    if len(events) < 2:
        return 0

    all_pitches = [_pitch(top) for _, top, _ in events]
    pitch_lo = min(all_pitches)
    pitch_span = max(1.0, float(max(all_pitches) - pitch_lo))
    hold_activity = _build_hold_activity(groups, settings)

    def weight(pitch: int) -> float:
        # 高音權重大：必須妥協時優先保住高音那一側
        return 1.0 + 2.0 * (float(pitch) - pitch_lo) / pitch_span

    seq_mates = _build_sequence_neighbours(notes, settings)

    def pair_cost(index: int) -> float:
        first_time, first, _ = events[index]
        second_time, second, _ = events[index + 1]
        if second_time - first_time > settings.top_edge_window_ms:
            return 0.0
        pitch_delta = _pitch(second) - _pitch(first)
        if pitch_delta == 0:
            return 0.0
        edge_delta = int(second.max_key) - int(first.max_key)
        w = weight(max(_pitch(first), _pitch(second)))
        if pitch_delta * edge_delta <= 0:
            return 100.0 * w                      # 順序違規：最重
        if abs(pitch_delta) <= 2 and abs(edge_delta) != 1:
            return 20.0 * w                       # 半音/全音必須剛好 1 格
        # 度數距離：官方 real 的前後步進約 0.45 格/半音
        want = _wanted_interval_lanes(pitch_delta, settings)
        return abs(abs(edge_delta) - want) * w

    def window_cost(lo: int, hi: int) -> float:
        return sum(pair_cost(i) for i in range(max(0, lo), min(hi, len(events) - 1)))

    # 同上：輪廓回溯也不准把同手兩顆的度數間距擠掉。
    spacing_baseline = {
        id(group): _chord_spacing_deficit(group, settings) for group in groups
    }
    # 也不准把「同一個音高」帶離它原本的位置。這一道是整條流程裡把同音打散
    # 最兇的（實測一個檔案就 +45），而後面的 _repair_pitch_position_consistency
    # 只救得回一部分。允許維持或改善，不允許變差。
    anchors = _build_position_anchors(notes, settings)
    anchor_baseline = {
        id(note): _anchor_deviation(anchors, note, settings) for note in notes
    }
    hold_dependents = _build_hold_dependents(groups, hold_activity)

    def hard_valid(note: Any, group: Sequence[Any]) -> bool:
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        if _chord_spacing_deficit(group, settings) > spacing_baseline.get(
            id(group), 0.0
        ):
            return False
        if _anchor_deviation(anchors, note, settings) > anchor_baseline.get(
            id(note), 0.0
        ) + float(settings.backtrack_anchor_slack):
            return False
        if not _hold_corridor_clear(
            notes, hold_activity, hold_dependents, (note,)
        ):
            return False
        return _range_avoids_hold(
            notes, group, note, hold_activity,
            int(note.min_key), int(note.max_key),
        )

    lookback = max(0, int(settings.backtrack_lookback))
    reach = max(1, int(settings.backtrack_lane_reach))
    repairs = 0
    for _ in range(max(1, int(settings.backtrack_passes))):
        improved = False
        for index in range(len(events) - 1):
            if pair_cost(index) <= 0.0:
                continue
            lo = max(0, index - lookback)
            hi = index + 2
            base = window_cost(lo - 1, hi + 1)
            # 候選移動集合：
            #   單顆 → 該事件同手的整個區塊 → 連續數個事件的區塊「連帶移動」
            # 最後一種是為了大跨距（例如八度）：單顆挪不了那麼遠而不撞到別的
            # 音符，但把整段一起平移就保持了段內的相對關係。
            candidates: List[Tuple[List[Any], List[Sequence[Any]]]] = []
            for j in range(lo, index + 2):
                if j >= len(events):
                    break
                _, note, group = events[j]
                same_hand = int(getattr(note, "hand", 0))
                block = [
                    other for other in group
                    if int(getattr(other, "hand", 0)) == same_hand
                ]
                candidates.append(([note], [group]))
                if len(block) > 1:
                    candidates.append((list(block), [group]))
                for span in range(2, max(2, int(settings.backtrack_chain_span)) + 1):
                    end = j + span
                    if end > min(len(events), index + 2):
                        break
                    movers: List[Any] = []
                    groups_involved: List[Sequence[Any]] = []
                    for k in range(j, end):
                        _t, _n, g = events[k]
                        hand = int(getattr(_n, "hand", 0))
                        movers.extend(
                            o for o in g
                            if int(getattr(o, "hand", 0)) == hand
                        )
                        groups_involved.append(g)
                    if movers:
                        candidates.append((movers, groups_involved))

            best: Optional[Tuple[float, List[Any], int]] = None
            for movers, groups_involved in candidates:
                for delta in range(-reach, reach + 1):
                    if delta == 0:
                        continue
                    if any(int(x.min_key) + delta < 0
                           or int(x.max_key) + delta >= settings.total_lanes
                           for x in movers):
                        continue
                    for x in movers:
                        x.min_key = int(x.min_key) + delta
                        x.max_key = int(x.max_key) + delta
                    # 移動了組內部分音符，整個組的重疊/順序都要重驗
                    ok = all(
                        hard_valid(x, g)
                        for g in groups_involved
                        for x in g
                    )
                    score = window_cost(lo - 1, hi + 1) if ok else base
                    for x in movers:
                        x.min_key = int(x.min_key) - delta
                        x.max_key = int(x.max_key) - delta
                    if not ok or score >= base:
                        continue
                    if best is None or score < best[0]:
                        best = (score, movers, delta)
            if best is None:
                continue
            _, movers, delta = best
            for x in movers:
                x.min_key = int(x.min_key) + delta
                x.max_key = int(x.max_key) + delta
            repairs += 1
            improved = True
        if not improved:
            break
    return repairs



def _cross_hand_deficit(
    group: Sequence[Any], settings: SmartChartSettings
) -> float:
    """這一組裡兩手交界比「該有的間距」窄了多少（鍵道，越大越糟）。"""
    ordered = sorted(group, key=lambda note: (_pitch(note), int(note.min_key)))
    deficit = 0.0
    for first, second in zip(ordered, ordered[1:]):
        if int(getattr(first, "hand", 0)) == int(getattr(second, "hand", 0)):
            continue
        need = _cross_hand_target(_pitch(second) - _pitch(first), settings)
        actual = (
            (int(second.min_key) + int(second.max_key)) / 2.0
            - (int(first.min_key) + int(first.max_key)) / 2.0
        )
        if actual < need:
            deficit += need - actual
    return deficit


def _chord_spacing_deficit(
    group: Sequence[Any], settings: SmartChartSettings
) -> float:
    """同手「剛好兩顆」的中心距，比該有的度數間距窄了多少（鍵道）。

    官方 real 的同手兩音是階梯狀的：≤4 半音 3 格、5~7 半音 4 格、≥8 半音 5 格
    （12 半音 n=44888，中位數就是 5.0）。3 顆以上官方本來就是貼合的，那裡沒有
    度數空隙可言，所以不納入。
    """
    by_hand: Dict[int, List[Any]] = {}
    for note in group:
        by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
    deficit = 0.0
    for items in by_hand.values():
        if len(items) != 2:
            continue
        first, second = sorted(items, key=_pitch)
        interval = _pitch(second) - _pitch(first)
        if interval <= 0:
            continue
        need = _chord_pair_lanes(
            interval, settings,
            int(first.max_key) - int(first.min_key) + 1,
            int(second.max_key) - int(second.min_key) + 1,
        )
        actual = (
            (int(second.min_key) + int(second.max_key)) / 2.0
            - (int(first.min_key) + int(first.max_key)) / 2.0
        )
        if actual < need:
            deficit += need - actual
    return deficit


def _repair_pitch_position_consistency(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """同一個音高在一個樂句視窗內，應該落在同一個鍵道位置。

    相鄰配對的順序全部合法、映射層也已經固定，畫面上那條線仍然會滑動 ——
    因為映射之後還有十幾道修復通道各自用整數位移搬動音符，累積起來就把
    同一個音高帶到不同位置（實測同音高相鄰有 50% 位置不同）。這裡在所有
    修復跑完之後把它們拉回共同位置。

    目標值取「視窗內同音高的右緣中位數」；只在硬性條件（組內不重疊、組內
    音高順序、長押走廊）允許時才移動，而且一次最多移動 `reach` 格。
    """
    ordered = sorted(notes, key=_start)
    by_pitch: Dict[int, List[Any]] = {}
    for note in ordered:
        by_pitch.setdefault(_pitch(note), []).append(note)
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    hold_dependents = _build_hold_dependents(groups, hold_activity)
    window = max(1, int(settings.pitch_consistency_window_ms))
    reach = max(1, int(settings.pitch_consistency_reach))

    # 最高音那條線的順序優先權高於位置一致性：先記下每個群組的最高音與
    # 它前後的鄰居，移動時若會弄反順序就不接受。
    top_seq: List[Tuple[int, Any]] = []
    for group in groups:
        top_pitch = max(_pitch(note) for note in group)
        top = max(
            (note for note in group if _pitch(note) == top_pitch),
            key=lambda note: (int(note.max_key), int(note.min_key)),
        )
        top_seq.append((min(_start(note) for note in group), top))
    top_index = {id(note): i for i, (_t, note) in enumerate(top_seq)}

    def top_order_ok(note: Any) -> bool:
        i = top_index.get(id(note))
        if i is None:
            return True
        for j in (i - 1, i + 1):
            if not (0 <= j < len(top_seq)):
                continue
            lo_i, hi_i = (i, j) if i < j else (j, i)
            t_lo, n_lo = top_seq[lo_i]
            t_hi, n_hi = top_seq[hi_i]
            if t_hi - t_lo > settings.top_edge_window_ms:
                continue
            pitch_delta = _pitch(n_hi) - _pitch(n_lo)
            if pitch_delta == 0:
                continue
            edge_delta = int(n_hi.max_key) - int(n_lo.max_key)
            if pitch_delta * edge_delta <= 0:
                return False
        return True

    def hard_valid(note: Any) -> bool:
        group = note_group[id(note)]
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        if not _range_avoids_hold(
            notes, group, note, hold_activity,
            int(note.min_key), int(note.max_key),
        ):
            return False
        # 拉齊同音位置也不可以把同手和絃拆出空隙 —— 官方 k≥3 是完全貼合的。
        if _chord_flush_cost(
            note, group, settings,
            int(note.min_key), int(note.max_key) - int(note.min_key) + 1,
        ) > 0:
            return False
        return top_order_ok(note)

    repairs = 0
    for _ in range(max(1, int(settings.pitch_consistency_passes))):
        moved = 0
        for pitch, items in by_pitch.items():
            if len(items) < 2:
                continue
            times = [_start(note) for note in items]
            for index, note in enumerate(items):
                when = times[index]
                lo = bisect_left(times, when - window)
                hi = bisect_right(times, when + window)
                neighbours = [
                    items[k] for k in range(lo, hi) if k != index
                ]
                if not neighbours:
                    continue
                target = int(round(median([int(x.max_key) for x in neighbours])))
                delta = target - int(note.max_key)
                if delta == 0:
                    continue
                delta = max(-reach, min(reach, delta))
                if int(note.min_key) + delta < 0:
                    continue
                if int(note.max_key) + delta >= settings.total_lanes:
                    continue
                # 拉齊位置不可以把兩手交界擠回去。官方 real 的兩手中位距是 9
                # 鍵道、小度數也維持 5 格；沒有這道防線的話這個通道會把交界
                # 從 8 格壓到 7 格，得再靠 _widen_hand_boundary 撐回來，而那
                # 一道又會把剛拉齊的同音位置再打散。
                group = note_group[id(note)]
                boundary_before = _cross_hand_deficit(group, settings)
                # 也不可以把同手兩顆的度數間距擠掉 —— 實測沒有這道防線時，
                # 這個通道一個人就把 141 組 ≥8 半音的同手雙音壓到 5 格以下，
                # 明明是八度卻貼在一起。
                spacing_before = _chord_spacing_deficit(group, settings)
                # 先試「整隻手在這一組一起平移」。單顆移動在和絃裡一定會被
                # `_chord_flush_cost` 擋掉（動一顆就拉出空隙），所以只要這顆
                # 音在和絃裡，舊版等於完全拉不齊 —— 實測一個音高會散在十幾
                # 個不同的右緣上。整組剛體平移不動內部幾何，才拉得動。
                hand = int(getattr(note, "hand", 0))
                block = [
                    other for other in group
                    if int(getattr(other, "hand", 0)) == hand
                ]
                attempts = [block, [note]] if len(block) > 1 else [[note]]
                applied = False
                for movers in attempts:
                    for item in movers:
                        item.min_key = int(item.min_key) + delta
                        item.max_key = int(item.max_key) + delta
                    if (
                        all(0 <= int(x.min_key) and int(x.max_key)
                            < settings.total_lanes for x in movers)
                        and all(hard_valid(x) for x in movers)
                        and _hold_corridor_clear(
                            notes, hold_activity, hold_dependents, movers)
                        and _cross_hand_deficit(group, settings) <= boundary_before
                        and _chord_spacing_deficit(group, settings) <= spacing_before
                    ):
                        repairs += 1
                        moved += 1
                        applied = True
                        break
                    for item in movers:
                        item.min_key = int(item.min_key) - delta
                        item.max_key = int(item.max_key) - delta
                if not applied:
                    continue
        if not moved:
            break
    return repairs



def _snap_repeated_pitch_lanes(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """把「前後出現的同一個音高」直接吸到同一個鍵道。

    `_repair_pitch_position_consistency` 對齊的目標是「±4 秒視窗內的中位數」，
    那是統計意義上的集中，不等於使用者要的「前一顆在哪、這一顆就在哪」。
    這裡改成直接看前一次出現：時間差在視窗內、右緣不同，就把這一組（整隻手
    剛體平移，和絃幾何不動）搬去對齊前一顆。

    硬性條件和一致性通道一樣：組內不重疊、組內音高順序、長押走廊、兩手交界
    不變差、同手度數間距不變差。搬不動就維持原狀。
    """
    if not groups:
        return 0
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    hold_dependents = _build_hold_dependents(groups, hold_activity)
    window = max(1, int(settings.snap_repeat_window_ms))
    reach = max(1, int(settings.snap_repeat_reach))

    anchors = _build_position_anchors(notes, settings)
    by_pitch: Dict[int, List[Any]] = {}
    for note in sorted(notes, key=_start):
        by_pitch.setdefault(_pitch(note), []).append(note)

    # 每隻手的「同一時刻最高音」序列。吸附排在 _repair_hand_top_edge_strict
    # 之後，如果不看這條線，剛排好的高低關係會被整組平移推翻 —— 實測不守
    # 這條時最高音不合格率從 0.78% 爆到 14.12%。
    top_window = max(1, int(settings.pitch_trend_window_ms))
    top_events: Dict[int, List[Any]] = {}
    top_index: Dict[int, Tuple[int, int]] = {}
    for group in sorted(groups, key=lambda g: min(_start(n) for n in g)):
        by_hand_group: Dict[int, List[Any]] = {}
        for note in group:
            by_hand_group.setdefault(int(getattr(note, "hand", 0)), []).append(note)
        for hand, members in by_hand_group.items():
            top = max(members, key=lambda n: (_pitch(n), int(n.max_key)))
            seq = top_events.setdefault(hand, [])
            top_index[id(top)] = (hand, len(seq))
            seq.append(top)

    def top_pair_bad(first: Any, second: Any) -> bool:
        if _pitch(first) == _pitch(second):
            return False
        edge = int(second.max_key) - int(first.max_key)
        if edge == 0:
            return True
        return (_pitch(second) - _pitch(first)) * edge < 0

    def top_order_bad(movers: Sequence[Any]) -> int:
        seen = set()
        count = 0
        for note in movers:
            entry = top_index.get(id(note))
            if entry is None:
                continue
            hand, index = entry
            seq = top_events[hand]
            for other in (index - 1, index + 1):
                if not (0 <= other < len(seq)):
                    continue
                lo, hi = (index, other) if index < other else (other, index)
                key = (hand, lo, hi)
                if key in seen:
                    continue
                seen.add(key)
                if _start(seq[hi]) - _start(seq[lo]) > top_window:
                    continue
                count += top_pair_bad(seq[lo], seq[hi])
        return count

    def valid(group: Sequence[Any], movers: Sequence[Any],
              deficit: float, spacing: float) -> bool:
        if any(int(n.min_key) < 0 or int(n.max_key) >= settings.total_lanes
               for n in movers):
            return False
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        if _cross_hand_deficit(group, settings) > deficit:
            return False
        if _chord_spacing_deficit(group, settings) > spacing:
            return False
        if not _hold_corridor_clear(
            notes, hold_activity, hold_dependents, movers
        ):
            return False
        return all(
            _range_avoids_hold(
                notes, group, note, hold_activity,
                int(note.min_key), int(note.max_key),
            )
            for note in movers
        )

    snapped = 0
    for _ in range(max(1, int(settings.snap_repeat_passes))):
        moved = 0
        for pitch, items in by_pitch.items():
            if len(items) < 2:
                continue
            for previous, current in zip(items, items[1:]):
                if _start(current) - _start(previous) > window:
                    continue
                delta = int(previous.max_key) - int(current.max_key)
                if delta == 0 or abs(delta) > reach:
                    continue
                group = note_group[id(current)]
                hand = int(getattr(current, "hand", 0))
                block = [
                    other for other in group
                    if int(getattr(other, "hand", 0)) == hand
                ]
                whole = list(group)
                deficit = _cross_hand_deficit(group, settings)
                spacing = _chord_spacing_deficit(group, settings)
                # 三種搬法，由「動得最少」往「動得最多」試：
                #   1. 這隻手在這一組的音符一起移（和絃幾何不動）
                #   2. 整組（含另一隻手）一起移 —— 跨手距離和兩手的內部幾何
                #      都原封不動，所以交界那條防線擋不住它，能救回很多被
                #      「交界不准變差」卡掉的情形
                #   3. 只移這一顆（最後手段）
                attempts = []
                if len(block) > 1:
                    attempts.append(block)
                if len(whole) > len(block):
                    attempts.append(whole)
                attempts.append([current])
                for movers in attempts:
                    drift_before = sum(
                        _anchor_deviation(anchors, item, settings)
                        for item in movers
                    )
                    top_before = top_order_bad(movers)
                    for item in movers:
                        item.min_key = int(item.min_key) + delta
                        item.max_key = int(item.max_key) + delta
                    drift_after = sum(
                        _anchor_deviation(anchors, item, settings)
                        for item in movers
                    )
                    # 整組平移會連帶動到另一隻手，所以要求「整體同音偏離不增加」
                    if (valid(group, movers, deficit, spacing)
                            and top_order_bad(movers) <= top_before
                            and (len(movers) <= len(block)
                                 or drift_after <= drift_before)):
                        snapped += 1
                        moved += 1
                        break
                    for item in movers:
                        item.min_key = int(item.min_key) - delta
                        item.max_key = int(item.max_key) - delta
        if not moved:
            break
    return snapped


def _build_position_anchors(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> Dict[int, Tuple[List[int], List[int]]]:
    """依「目前實際位置」建立每個音高的錨點索引（時間, 右緣）。

    後段修復通道拿不到 `desired`，而且此時位置已被前面幾道通道改過，所以
    錨點直接從現況統計。用它當成本項是把同一音高的位置互相拉近，自洽。
    """
    table: Dict[int, Tuple[List[int], List[int]]] = {}
    for note in sorted(notes, key=_start):
        times, edges = table.setdefault(_pitch(note), ([], []))
        times.append(_start(note))
        edges.append(int(note.max_key))
    return table


def _anchor_deviation(
    anchors: Dict[int, Tuple[List[int], List[int]]],
    note: Any,
    settings: SmartChartSettings,
    edge: Optional[int] = None,
) -> float:
    """這顆音符（或假設它移到 `edge`）離同音高錨點多遠。"""
    entry = anchors.get(_pitch(note))
    if not entry:
        return 0.0
    times, edges = entry
    if len(times) < 2:
        return 0.0
    window = max(1, int(settings.pitch_anchor_window_ms))
    when = _start(note)
    lo = bisect_left(times, when - window)
    hi = bisect_right(times, when + window)
    if hi - lo < 2:
        return 0.0
    target = median(edges[lo:hi])
    current = int(note.max_key) if edge is None else int(edge)
    return abs(float(current) - float(target))



def _build_sequence_neighbours(
    notes: Sequence[Any], settings: SmartChartSettings
) -> Dict[int, List[Any]]:
    """每顆音符在同一隻手、時間上前後相鄰的夥伴（給度數距離成本用）。"""
    table: Dict[int, List[Any]] = {}
    by_hand: Dict[int, List[Any]] = {}
    for note in notes:
        by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
    window = max(1, int(settings.pitch_trend_window_ms))
    for hand_notes in by_hand.values():
        hand_notes.sort(key=lambda n: (_start(n), _pitch(n)))
        starts = [_start(n) for n in hand_notes]
        for index, note in enumerate(hand_notes):
            when = starts[index]
            lo = bisect_left(starts, when - window)
            hi = bisect_right(starts, when + window)
            mates = [
                hand_notes[k] for k in range(lo, hi)
                if k != index and _pitch(hand_notes[k]) != _pitch(note)
            ]
            if mates:
                table[id(note)] = mates
    return table


def _wanted_interval_lanes(pitch_delta: int, settings: SmartChartSettings) -> float:
    """前後相接的兩顆，度數應該對應到幾格鍵道。

    量官方 real 全語料（423 譜 / 39.9 萬對）得到：1度 1.3、2度 1.4、3度 2.2、
    5度 2.9、7度 3.7、9度 4.7、八度 5.2 —— 一個八度內大約 `|Δ音高| × 0.5`。

    但超過八度之後官方會**收斂**，不再等比例拉開：14度 5.9、16度 6.5、
    20度 7.8、24度 7.8、30度 10.1，斜率掉到約 0.25 格/半音。原本這裡是無上限
    的線性，所以大跳躍被拉到 12.6 格（官方 10.1），把整個「>=八度」的平均從
    6.13 推到 7.18。鍵盤只有 28 格，大跳躍本來就必須壓縮。
    """
    delta = abs(int(pitch_delta))
    knee = max(1, int(settings.sequence_knee_semitones))
    near = float(settings.sequence_lanes_per_semitone)
    if delta <= knee:
        return max(1.0, delta * near)
    return max(
        1.0,
        knee * near
        + (delta - knee) * float(settings.sequence_wide_lanes_per_semitone),
    )


def _sequence_interval_cost(
    neighbours: Dict[int, List[Any]],
    note: Any,
    settings: SmartChartSettings,
    edge: Optional[int] = None,
) -> float:
    """這顆音符（或假設它移到 `edge`）與前後夥伴的度數距離偏離多少。"""
    mates = neighbours.get(id(note))
    if not mates:
        return 0.0
    here = float(int(note.max_key) if edge is None else int(edge))
    total = 0.0
    for other in mates:
        want = _wanted_interval_lanes(_pitch(other) - _pitch(note), settings)
        total += abs(abs(here - float(int(other.max_key))) - want)
    return total / float(len(mates))


def _repair_final_nearby_pitch_order(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Repair order after width packing, which can move centers by a lane."""
    repairs = 0
    for _ in range(8):
        targets = {
            id(note): (
                (int(note.min_key) + int(note.max_key)) / 2.0,
                0.0,
            )
            for note in notes
        }
        changed = _enforce_nearby_pitch_order(notes, targets, settings)
        if not changed:
            break
        repairs += changed
        _repack_groups_at_targets(groups, targets, settings)
        if not _nearby_pitch_order_violations(notes, settings):
            break
    return repairs


def _discrete_final_order_repair(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Resolve residual half-lane inversions with feasible integer moves."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)
    anchors = _build_position_anchors(notes, settings)
    seq_mates = _build_sequence_neighbours(notes, settings)
    repairs = 0

    def center(note: Any) -> float:
        return (int(note.min_key) + int(note.max_key)) / 2.0

    def feasible_starts(note: Any) -> List[int]:
        width = int(note.max_key) - int(note.min_key) + 1
        group = note_group[id(note)]
        # 同手剛好兩顆時，度數間距只准維持或變好，不准被順序修復擠掉。
        # 門檻取 min(該有的間距, 目前的間距)，所以現況一定通過，不會讓
        # 這顆音符完全沒有可行位置。
        hand = int(getattr(note, "hand", 0))
        same_hand = [
            other for other in group
            if other is not note and int(getattr(other, "hand", 0)) == hand
        ]
        partner = same_hand[0] if len(same_hand) == 1 else None
        partner_center = 0.0
        spacing_floor = 0.0
        if partner is not None:
            partner_center = (
                int(partner.min_key) + int(partner.max_key)
            ) / 2.0
            current = abs(
                (int(note.min_key) + int(note.max_key)) / 2.0 - partner_center
            )
            spacing_floor = min(
                _chord_pair_lanes(
                    _pitch(note) - _pitch(partner), settings, width,
                    int(partner.max_key) - int(partner.min_key) + 1,
                ),
                current,
            )
        result: List[int] = []
        for start in range(0, settings.total_lanes - width + 1):
            end = start + width - 1
            if not _range_avoids_hold(
                notes, group, note, hold_activity, start, end
            ):
                continue
            candidate_center = (start + end) / 2.0
            if (partner is not None
                    and abs(candidate_center - partner_center) < spacing_floor):
                continue
            valid = True
            for other in group:
                if other is note:
                    continue
                other_start = int(other.min_key)
                other_end = int(other.max_key)
                other_center = (other_start + other_end) / 2.0
                if start <= other_end and end >= other_start:
                    valid = False
                    break
                if int(getattr(other, "hand", 0)) == int(
                    getattr(note, "hand", 0)
                ):
                    if _pitch(note) < _pitch(other) and candidate_center > other_center:
                        valid = False
                        break
                    if _pitch(note) > _pitch(other) and candidate_center < other_center:
                        valid = False
                        break
                elif int(getattr(note, "hand", 0)) == 1 and end >= other_start:
                    valid = False
                    break
                elif int(getattr(note, "hand", 0)) == 0 and start <= other_end:
                    valid = False
                    break
            if valid:
                result.append(start)
        return result

    # 48 輪全跑完在密譜上要十幾秒，而且這是整條流程裡呼叫三次的熱點。
    # 順序修復是收斂式的，後面幾輪本來就撿不到多少，超時就收手。
    deadline = perf_counter() + max(0.0, float(settings.repair_time_budget_sec))
    for pass_index in range(48):
        if pass_index and perf_counter() > deadline:
            break
        violations = _violating_voice_pairs(notes, settings)
        if not violations:
            break
        # Dense edge chords can make two different note widths differ by half
        # a lane even though neither note has anywhere left to move.  Pitch
        # order is the hard rule, so after ordinary moves have had a chance,
        # collapse only the still-conflicting voices to one lane.  The new
        # range remains inside the old range and therefore cannot introduce
        # an overlap in its onset group.
        if pass_index == 12:
            # 舊版在這裡直接把還在衝突的音符壓成單一鍵道。人工譜（52 首
            # 14 萬顆音符）完全沒有寬度 1，所以改成一次只收一格、收到
            # min_note_width 就停；收不動時後面的位移嘗試會接手。
            narrowed = set()
            floor_w = max(1, int(settings.min_note_width))
            for low, high in violations:
                involved = list(note_group[id(low)]) + list(
                    note_group[id(high)]
                )
                for note in involved:
                    if id(note) in narrowed:
                        continue
                    narrowed.add(id(note))
                    # 只收「本來就在密和絃裡」的音符。舊版是把兩個衝突組的
                    # 每一顆都收一格，於是前後兩顆孤立音的順序問題會讓一整
                    # 排音符變窄 —— 實測這一道就製造了 1281 顆 k<3 的寬度 2。
                    # 官方 real 的 k=1／k=2 收窄率是 0.0%／0.2%。
                    if not _may_narrow_for_cue(
                        note, note_group[id(note)], settings
                    ):
                        continue
                    if int(note.max_key) - int(note.min_key) + 1 <= floor_w:
                        continue
                    # 往中心收一格：低音收右緣、高音收左緣
                    if note is high or _pitch(note) >= _pitch(high):
                        note.min_key = int(note.min_key) + 1
                    else:
                        note.max_key = int(note.max_key) - 1
                    repairs += 1
        changed = 0
        for low, high in violations:
            low_width = int(low.max_key) - int(low.min_key) + 1
            high_width = int(high.max_key) - int(high.min_key) + 1
            low_current = center(low)
            high_current = center(high)
            choices: List[Tuple[float, int, int]] = []
            for low_start in feasible_starts(low):
                low_center = low_start + (low_width - 1) / 2.0
                for high_start in feasible_starts(high):
                    high_center = high_start + (high_width - 1) / 2.0
                    if low_center > high_center:
                        continue
                    cost = (
                        (low_center - low_current) ** 2
                        + (high_center - high_current) ** 2
                        + float(settings.sequence_interval_weight) * (
                            _sequence_interval_cost(
                                seq_mates, low, settings,
                                low_start + low_width - 1)
                            + _sequence_interval_cost(
                                seq_mates, high, settings,
                                high_start + high_width - 1)
                        )
                        + float(settings.repair_anchor_weight) * (
                            _anchor_deviation(
                                anchors, low, settings,
                                low_start + low_width - 1)
                            + _anchor_deviation(
                                anchors, high, settings,
                                high_start + high_width - 1)
                        )
                        + float(settings.chord_flush_weight) * (
                            _chord_flush_cost(
                                low, note_group[id(low)], settings,
                                low_start, low_width)
                            + _chord_flush_cost(
                                high, note_group[id(high)], settings,
                                high_start, high_width)
                        )
                    )
                    choices.append((cost, low_start, high_start))
            if not choices:
                low_group = note_group[id(low)]
                high_group = note_group[id(high)]
                low_min = min(int(note.min_key) for note in low_group)
                low_max = max(int(note.max_key) for note in low_group)
                high_min = min(int(note.min_key) for note in high_group)
                high_max = max(int(note.max_key) for note in high_group)
                group_choices: List[Tuple[float, int, int]] = []
                for low_delta in range(-low_min, settings.total_lanes - low_max):
                    for high_delta in range(
                        -high_min, settings.total_lanes - high_max
                    ):
                        if low_current + low_delta > high_current + high_delta:
                            continue
                        if not all(
                            _range_avoids_hold(
                                notes,
                                low_group,
                                note,
                                hold_activity,
                                int(note.min_key) + low_delta,
                                int(note.max_key) + low_delta,
                            )
                            for note in low_group
                        ):
                            continue
                        if not all(
                            _range_avoids_hold(
                                notes,
                                high_group,
                                note,
                                hold_activity,
                                int(note.min_key) + high_delta,
                                int(note.max_key) + high_delta,
                            )
                            for note in high_group
                        ):
                            continue
                        cost = (
                            low_delta * low_delta * len(low_group)
                            + high_delta * high_delta * len(high_group)
                        )
                        group_choices.append((cost, low_delta, high_delta))
                if not group_choices:
                    continue
                _, low_delta, high_delta = min(
                    group_choices, key=lambda item: item[0]
                )
                if not low_delta and not high_delta:
                    continue
                for note in low_group:
                    note.min_key = int(note.min_key) + low_delta
                    note.max_key = int(note.max_key) + low_delta
                for note in high_group:
                    note.min_key = int(note.min_key) + high_delta
                    note.max_key = int(note.max_key) + high_delta
                repairs += 1
                changed += 1
                continue
            _, low_start, high_start = min(choices, key=lambda item: item[0])
            if (
                low_start == int(low.min_key)
                and high_start == int(high.min_key)
            ):
                continue
            low.min_key = low_start
            low.max_key = low_start + low_width - 1
            high.min_key = high_start
            high.max_key = high_start + high_width - 1
            repairs += 1
            changed += 1
        if not changed:
            break
    return repairs


def _greedy_group_order_repair(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Move complete onset groups when local note fixes would merely oscillate."""
    note_group: Dict[int, Sequence[Any]] = {
        id(note): group for group in groups for note in group
    }
    hold_activity = _build_hold_activity(groups, settings)

    def hold_valid(items: Sequence[Any]) -> bool:
        return all(
            _range_avoids_hold(
                notes,
                note_group[id(note)],
                note,
                hold_activity,
                int(note.min_key),
                int(note.max_key),
            )
            for note in items
        )

    def score() -> Tuple[int, float]:
        # 注意：這裡曾經加過「全體音符離音高錨點的平均距離」當第二關鍵字，
        # 但 score() 在候選搜尋的內圈被呼叫，實測會產生上千萬次 O(n) 掃描
        # （單曲多花 19 秒），而這個通道在一般譜面上根本跑不到幾次修復。
        # 成本遠大於效益，已移除。
        violations = _violating_voice_pairs(notes, settings)
        amount = sum(
            max(
                0.0,
                (int(low.min_key) + int(low.max_key)) / 2.0
                - (int(high.min_key) + int(high.max_key)) / 2.0,
            )
            for low, high in violations
        )
        return len(violations), amount

    repairs = 0
    # 收窄寬度是最後手段：先窮盡「移動」的可能，移不動了才動寬度。
    # 舊版在每一輪開頭就先收窄，而且 `if narrowed: continue` 會直接跳過
    # 移動搜尋 —— 等於把最後手段當成第一手段。
    #
    # 這個迴圈每一輪都要重算 _violating_voice_pairs（內含 _clusters），
    # 病態輸入下會跑滿 256 輪、單曲多花數十秒。加上時間預算讓它在收斂
    # 不了時提早收手 —— 剩下的違規交給後面的通道處理。
    tabu = set()
    deadline = perf_counter() + max(0.0, float(settings.repair_time_budget_sec))
    for _ in range(256):
        if perf_counter() > deadline:
            break
        violations = _violating_voice_pairs(notes, settings)
        if not violations:
            break
        current_score = score()
        best: Optional[
            Tuple[Tuple[int, float], Sequence[Any], int, Tuple[str, int]]
        ] = None
        seen = set()
        for low, high in violations:
            for note, delta in ((low, -1), (high, 1)):
                key = (id(note), delta, "note")
                if key in seen:
                    continue
                seen.add(key)
                target_key = ("note", id(note))
                if (target_key, -delta) in tabu:
                    continue
                if int(note.min_key) + delta < 0:
                    continue
                if int(note.max_key) + delta >= settings.total_lanes:
                    continue
                group = note_group[id(note)]
                note.min_key = int(note.min_key) + delta
                note.max_key = int(note.max_key) + delta
                layout_valid = (
                    _group_overlaps(group) == 0
                    and _pitch_order_violations(group) == 0
                    and hold_valid((note,))
                )
                candidate_score = score() if layout_valid else current_score
                note.min_key = int(note.min_key) - delta
                note.max_key = int(note.max_key) - delta
                if not layout_valid:
                    continue
                if candidate_score <= current_score and (
                    best is None or candidate_score < best[0]
                ):
                    best = (candidate_score, (note,), delta, target_key)
            for note, delta in ((low, -1), (high, 1)):
                group = note_group[id(note)]
                key = (id(group), delta, "group")
                if key in seen:
                    continue
                seen.add(key)
                target_key = ("group", id(group))
                if (target_key, -delta) in tabu:
                    continue
                if delta < 0 and min(int(item.min_key) for item in group) <= 0:
                    continue
                if delta > 0 and max(int(item.max_key) for item in group) >= settings.total_lanes - 1:
                    continue
                for item in group:
                    item.min_key = int(item.min_key) + delta
                    item.max_key = int(item.max_key) + delta
                layout_valid = hold_valid(group)
                candidate_score = score() if layout_valid else current_score
                for item in group:
                    item.min_key = int(item.min_key) - delta
                    item.max_key = int(item.max_key) - delta
                if not layout_valid:
                    continue
                if candidate_score > current_score:
                    continue
                if best is None or candidate_score < best[0]:
                    best = (candidate_score, group, delta, target_key)
        if best is None:
            # A monotone chain can require temporarily moving one inversion
            # into the preceding pair before it reaches free space.  Permit
            # one directed, locally valid step; the tabu direction prevents
            # simply moving the same target back on the next iteration.
            for low, high in violations:
                for note, delta in ((low, -1), (high, 1)):
                    target_key = ("note", id(note))
                    if (target_key, -delta) in tabu:
                        continue
                    if int(note.min_key) + delta < 0:
                        continue
                    if int(note.max_key) + delta >= settings.total_lanes:
                        continue
                    group = note_group[id(note)]
                    note.min_key = int(note.min_key) + delta
                    note.max_key = int(note.max_key) + delta
                    valid = (
                        _group_overlaps(group) == 0
                        and _pitch_order_violations(group) == 0
                        and hold_valid((note,))
                    )
                    note.min_key = int(note.min_key) - delta
                    note.max_key = int(note.max_key) - delta
                    if valid:
                        best = (current_score, (note,), delta, target_key)
                        break
                if best is not None:
                    break
            if best is None:
                for low, high in violations:
                    for note, delta in ((low, -1), (high, 1)):
                        group = note_group[id(note)]
                        target_key = ("group", id(group))
                        if (target_key, -delta) in tabu:
                            continue
                        if delta < 0 and min(
                            int(item.min_key) for item in group
                        ) <= 0:
                            continue
                        if delta > 0 and max(
                            int(item.max_key) for item in group
                        ) >= settings.total_lanes - 1:
                            continue
                        for item in group:
                            item.min_key = int(item.min_key) + delta
                            item.max_key = int(item.max_key) + delta
                        valid = hold_valid(group)
                        for item in group:
                            item.min_key = int(item.min_key) - delta
                            item.max_key = int(item.max_key) - delta
                        if valid:
                            best = (current_score, group, delta, target_key)
                            break
                    if best is not None:
                        break
        if best is None:
            # 所有移動都無解，這時才收窄內側邊緣
            narrowed = False
            for low, high in violations:
                if _trim_inner_edge(low, "high", settings):
                    repairs += 1
                    narrowed = True
                if _trim_inner_edge(high, "low", settings):
                    repairs += 1
                    narrowed = True
            if narrowed:
                continue
            break
        _, group, delta, target_key = best
        for item in group:
            item.min_key = int(item.min_key) + delta
            item.max_key = int(item.max_key) + delta
        tabu.add((target_key, delta))
        repairs += 1
    return repairs


def _group_overlaps(group: Sequence[Any]) -> int:
    ranges = sorted(
        (int(note.min_key), int(note.max_key), _pitch(note)) for note in group
    )
    return sum(ranges[index - 1][1] >= ranges[index][0] for index in range(1, len(ranges)))


def _hold_obstacles(
    notes: Sequence[Any],
    group: Sequence[Any],
    hand: int,
    activity: Optional[Dict[Tuple[int, int], Sequence[Any]]] = None,
) -> List[Tuple[int, int]]:
    when = min(_start(note) for note in group)
    group_ids = {id(note) for note in group}
    candidates = (
        tuple(activity.get((id(group), 0), ()))
        + tuple(activity.get((id(group), 1), ()))
        if activity is not None
        else notes
    )
    return [
        (int(note.min_key), int(note.max_key))
        for note in candidates
        if id(note) not in group_ids
        and _start(note) < when
        and int(getattr(note, "end", _start(note) + 1)) > when
    ]


def _build_hold_activity(
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> Dict[Tuple[int, int], Sequence[Any]]:
    activity: Dict[Tuple[int, int], Sequence[Any]] = {}
    active: Dict[int, List[Any]] = {0: [], 1: []}
    for group in groups:
        when = min(_start(note) for note in group)
        for hand in (0, 1):
            active[hand] = [
                note
                for note in active[hand]
                if int(getattr(note, "end", _start(note) + 1)) > when
            ]
            activity[(id(group), hand)] = tuple(active[hand])
        for note in group:
            duration = int(
                getattr(note, "end", _start(note) + 1)
            ) - _start(note)
            note_type = int(getattr(note, "note_type", 0) or 0)
            minimum_beats = (
                float(settings.hold_corridor_min_beats)
                if note_type == 2
                else float(settings.long_note_order_min_beats)
            )
            if duration >= float(settings.beat_ms) * minimum_beats:
                active[int(getattr(note, "hand", 0))].append(note)
    return activity


def _range_avoids_hold(
    notes: Sequence[Any],
    group: Sequence[Any],
    note: Any,
    activity: Dict[Tuple[int, int], Sequence[Any]],
    start: int,
    end: int,
) -> bool:
    active_notes = (
        tuple(activity.get((id(group), 0), ()))
        + tuple(activity.get((id(group), 1), ()))
    )
    note_pitch = _pitch(note)
    group_ids = {id(item) for item in group}
    for active_note in active_notes:
        if id(active_note) in group_ids:
            continue
        block_lo = int(active_note.min_key)
        block_hi = int(active_note.max_key)
        if start <= block_hi and end >= block_lo:
            return False
        active_pitch = _pitch(active_note)
        if note_pitch > active_pitch and start <= block_hi:
            return False
        if note_pitch < active_pitch and end >= block_lo:
            return False
    return True


def _build_hold_dependents(
    groups: Sequence[Sequence[Any]],
    activity: Dict[Tuple[int, int], Sequence[Any]],
) -> Dict[int, List[Sequence[Any]]]:
    """反向索引：這顆長押的走廊涵蓋了哪些組。

    `_range_avoids_hold` 只答得出「這顆有沒有撞進別人的長押走廊」。搬動的
    如果是**長押自己**，它會直接壓到走廊裡的後續音，而那些音不在自己的組
    裡，舊的檢查完全看不到 —— 實測「同音同軌」的通道就是這樣把長押搬去和
    它自己走廊裡的同音符疊在一起的。
    """
    out: Dict[int, List[Sequence[Any]]] = {}
    for group in groups:
        for hand in (0, 1):
            for active in activity.get((id(group), hand), ()):
                out.setdefault(id(active), []).append(group)
    return out


def _hold_corridor_clear(
    notes: Sequence[Any],
    activity: Dict[Tuple[int, int], Sequence[Any]],
    dependents: Dict[int, List[Sequence[Any]]],
    movers: Sequence[Any],
) -> bool:
    """搬動這些音符之後，它們各自的長押走廊有沒有被自己壓到。"""
    for note in movers:
        for group in dependents.get(id(note), ()):
            for item in group:
                if not _range_avoids_hold(
                    notes, group, item, activity,
                    int(item.min_key), int(item.max_key),
                ):
                    return False
    return True


def _neighbour_order_cost(
    movers: Sequence[Any], neighbours: Dict[int, List[Any]]
) -> int:
    """這批音符與「同手、時間相鄰」夥伴之間的順序違規數（含相等）。

    任何會改變 `max_key` 或整體位置的收尾修復都要驗這個：組內檢查看不到
    跨組的前後順序，不驗的話交界撐開了、寬度還原了，前後順序卻壞掉。
    """
    bad = 0
    for note in movers:
        edge = int(note.max_key)
        for mate in neighbours.get(id(note), ()):
            if (_pitch(note) - _pitch(mate)) * (edge - int(mate.max_key)) <= 0:
                bad += 1
    return bad


def _trend_blocks(notes, settings):
    """依「動作」分塊，而不是依休止分句。

    休止分句會把一段連續的音樂切在錯的地方——真正該當成一個單位的是同一種
    動作：一路往下的樓梯、一路往上的樓梯、在幾個音上來回敲（八度打音那種）。
    這些動作各自有自己該有的形狀，混在一起排就會互相干擾。

    切點：方向反轉（要連續兩步同向才算真的轉向，避免被單一顆裝飾音切碎）、
    休止太久、或是出現大跳。來回震盪的圖形不因為方向反轉而切，因為那本來
    就是它的特徵。
    """
    gap = max(1, int(settings.phrase_gap_ms))
    jump = max(1, int(settings.trend_jump_semitones))
    by_hand = {}
    for note in notes:
        by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
    out = []
    for hand_notes in by_hand.values():
        hand_notes.sort(key=lambda n: (_start(n), _pitch(n)))
        run = []
        direction = 0
        for note in hand_notes:
            if not run:
                run = [note]
                direction = 0
                continue
            prev = run[-1]
            step = _pitch(note) - _pitch(prev)
            cut = _start(note) - _start(prev) > gap or abs(step) > jump
            if not cut and step:
                sign = 1 if step > 0 else -1
                if direction and sign != direction:
                    # 來回震盪（音高只在少數幾個值之間跳）不算轉向
                    span = max(_pitch(x) for x in run) - min(_pitch(x) for x in run)
                    if span > jump or len(set(_pitch(x) for x in run)) > 4:
                        cut = True
                direction = sign if not cut else 0
            if cut:
                if len(run) >= int(settings.trend_min_notes):
                    out.append(run)
                run = []
                direction = 0
            run.append(note)
        if len(run) >= int(settings.trend_min_notes):
            out.append(run)
    return out


def _phrases(notes, settings):
    """同一隻手、以靜止時間斷開的樂句。"""
    by_hand = {}
    for note in notes:
        by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
    gap = max(1, int(settings.phrase_gap_ms))
    out = []
    for hand_notes in by_hand.values():
        hand_notes.sort(key=lambda n: _start(n))
        run = [hand_notes[0]] if hand_notes else []
        for a, b in zip(hand_notes, hand_notes[1:]):
            if _start(b) - _start(a) > gap:
                if len(run) >= 4:
                    out.append(run)
                run = []
            run.append(b)
        if len(run) >= 4:
            out.append(run)
    return out


def _extreme_cost(prev, cur):
    """相鄰兩個樂句的最高/最低音，音高關係有沒有反映到鍵道上。"""
    bad = 0
    for pick in (max, min):
        a = pick(prev, key=_pitch)
        b = pick(cur, key=_pitch)
        dp = _pitch(b) - _pitch(a)
        if dp == 0:
            continue
        dl = int(b.max_key) - int(a.max_key)
        if dl == 0 or dp * dl < 0:
            bad += 1
    return bad


def _align_phrase_extremes(notes, groups, settings):
    """整個樂句一起平移，讓它和前一句的最高/最低音關係正確。

    前面的修復都是看「相鄰幾顆」的順序，樂句與樂句之間的高低關係沒有人管，
    所以會出現前後兩句明明音域差很多、畫出來卻疊在同一條線上。實測官方的
    相鄰樂句最低音正確率 81.5%，這裡改之前只有 74.4%。

    只平移、不改寬度也不動樂句內部的相對位置——內部已經排好了。
    """
    moved = 0
    activity = _build_hold_activity(groups, settings)
    anchors = _build_position_anchors(notes, settings)
    neighbours = _build_sequence_neighbours(notes, settings)
    origin = {id(n): g for g in groups for n in g}
    lane_hi = int(settings.total_lanes) - 1
    reach = max(1, int(settings.phrase_shift_reach))
    phrases = _phrases(notes, settings)
    by_hand = {}
    for ph in phrases:
        by_hand.setdefault(int(getattr(ph[0], "hand", 0)), []).append(ph)
    for hand_phrases in by_hand.values():
        hand_phrases.sort(key=lambda p: _start(p[0]))
        for index in range(1, len(hand_phrases)):
            prev, cur = hand_phrases[index - 1], hand_phrases[index]
            base = _extreme_cost(prev, cur)
            if not base:
                continue
            base_drift = sum(
                _anchor_deviation(anchors, n, settings) for n in cur
            ) / max(1, len(cur))
            best, best_cost = 0, (base, 0, base_drift)
            for delta in range(-reach, reach + 1):
                if delta == 0:
                    continue
                if any(int(n.min_key) + delta < 0 or int(n.max_key) + delta > lane_hi
                       for n in cur):
                    continue
                before_order = _neighbour_order_cost(cur, neighbours)
                if not _shift_notes(notes, cur, cur, delta, activity, lane_hi):
                    continue
                ok = all(
                    _range_avoids_hold(notes, origin[id(n)], n, activity,
                                       int(n.min_key), int(n.max_key))
                    for n in cur
                )
                touched = {id(origin[id(n)]): origin[id(n)] for n in cur}
                if ok and any(_group_overlaps(g) for g in touched.values()):
                    ok = False
                # 樂句整句平移會把句中每一個音高都帶到新位置。同一個音高在
                # 前後句之間跳來跳去就是這樣來的（實測這一道 +16），所以在
                # 「樂句極值成本」相同時改用同音錨點偏離當第二順位的判準。
                drift = sum(
                    _anchor_deviation(anchors, n, settings) for n in cur
                ) / max(1, len(cur))
                cost = (_extreme_cost(prev, cur),
                        _neighbour_order_cost(cur, neighbours) - before_order,
                        drift)
                _shift_notes(notes, cur, cur, -delta, activity, lane_hi, force=True)
                if ok and cost[1] <= 0 and cost < best_cost:
                    best, best_cost = delta, cost
            if best:
                _shift_notes(notes, cur, cur, best, activity, lane_hi, force=True)
                moved += 1
    return moved


def _apply_block_layout(notes, groups, desired, settings):
    """分層排譜第一層：每顆音符的位置由全域音高刻度決定，區塊只做整體微調。

    上一版是「以區塊自己的音域中點為中心縮放」，縮放係數依區塊寬度而定，
    結果同一個音高在寬區塊和窄區塊會落到不同鍵道（實測同音散布 5.5→6.0）。
    那不是「塊間用全域尺規」，是「塊心用全域尺規、塊內各自縮放」。

    改成：位置 = 全域刻度(音高) + 整塊共用的一個偏移量。
    這樣同一個音高在任何區塊都落在同一個鍵道附近（尺規只有一把），而區塊
    仍然可以整體挪一點來讓出空間——正是「稍微位移整塊排好的音符」。
    偏移量取「原本排法和全域刻度的平均差距」，並且夾在 block_max_shift 內。
    """
    if not settings.block_layout or not notes:
        return 0
    pitches = [_pitch(n) for n in notes]
    lo_p, hi_p = min(pitches), max(pitches)
    span_p = max(1, hi_p - lo_p)
    lanes = float(settings.total_lanes - 1)

    def global_lane(pitch):
        # 官方的全域音高→鍵道是 S 形（中央一個八度約 8 格、邊緣約 4 格），
        # 直接沿用既有的塑形曲線
        return _shape_lane((pitch - lo_p) / span_p * lanes, settings)

    limit = float(settings.block_max_shift)
    blocks = (_trend_blocks(notes, settings) if settings.use_trend_blocks
              else _phrases(notes, settings))
    moved = 0
    for block in blocks:
        entries = [(n, desired.get(id(n))) for n in block]
        entries = [(n, e) for n, e in entries if e is not None]
        if not entries:
            continue
        offset = sum(e[0] - global_lane(_pitch(n)) for n, e in entries) / len(entries)
        offset = max(-limit, min(limit, offset))
        for note, (_centre, width) in entries:
            desired[id(note)] = (global_lane(_pitch(note)) + offset, width)
        moved += 1
    return moved


def _break_order_ties(notes, groups, settings):
    """收尾：把還停在同一格的前後音符，用一格的位移拆開。

    前面的修復是「整組重排」，一旦空間吃緊就只能讓兩顆停在同一個右緣，
    於是畫面上兩個不同音高看起來一樣高。這裡只做最小動作——把其中一顆
    往正確方向挪一格——所以在別的手段都放棄之後仍然常常成功。

    優先挪高音那顆往右；不行再挪低音那顆往左。整組候選都驗過才接受。
    """
    fixed = 0
    activity = _build_hold_activity(groups, settings)
    neighbours = _build_sequence_neighbours(notes, settings)
    origin = {id(n): g for g in groups for n in g}
    lane_hi = int(settings.total_lanes) - 1
    for _ in range(max(1, int(settings.tie_break_passes))):
        pairs = _violating_voice_pairs(notes, settings)
        if not pairs:
            break
        changed = 0
        for low, high in pairs:
            for note, delta in ((high, 1), (low, -1), (high, -1), (low, 1)):
                group = origin.get(id(note))
                if group is None:
                    continue
                if int(note.min_key) + delta < 0 or int(note.max_key) + delta > lane_hi:
                    continue
                before = _neighbour_order_cost([note], neighbours)
                if not _shift_notes(notes, group, [note], delta, activity, lane_hi):
                    continue
                ok = (
                    not _group_overlaps(group)
                    and not _pitch_order_violations(group)
                    and _neighbour_order_cost([note], neighbours) < before
                )
                if not ok:
                    _shift_notes(notes, group, [note], -delta, activity,
                                 lane_hi, force=True)
                    continue
                fixed += 1
                changed += 1
                break
        if not changed:
            break
    return fixed


def _cross_hand_target(interval: int, settings: SmartChartSettings) -> float:
    """同時發聲、跨手相鄰兩顆該有的中心距（與 `_place_hand` 同一套規則）。"""
    rate = float(settings.min_octave_center_distance) / 12.0
    need = min(
        float(settings.max_interval_center_distance),
        max(1.0, abs(interval) * rate),
    )
    return max(
        need + float(settings.hand_boundary_margin),
        float(settings.hand_boundary_min_distance),
    )


def _widen_hand_boundary(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """把被後續修復擠回去的兩手交界重新撐開。

    `_place_hand` 一開始就留了交界間距，但後面十幾道修復是照「音高順序」和
    「長押走廊」在搬音符，沒有人知道哪一條縫是兩手的交界，於是又被壓回同手
    的緊密間距。官方 real 的兩手幾乎不貼在一起（空隙 ≤0 只有 3%，同手是
    45%），所以這裡在最後把交界補回來。

    整手一起平移，不動單顆——單顆移動會破壞同手內部已經排好的度數關係。
    """
    moved = 0
    activity = _build_hold_activity(groups, settings)
    anchors = _build_position_anchors(notes, settings)
    neighbours = _build_sequence_neighbours(notes, settings)
    lane_hi = int(settings.total_lanes) - 1
    order_cost = lambda movers: _neighbour_order_cost(movers, neighbours)
    for group in groups:
        ordered = sorted(
            group,
            key=lambda note: (_pitch(note), _start(note), int(getattr(note, "index", 0))),
        )
        if len(ordered) < 2:
            continue
        for index in range(1, len(ordered)):
            low, high = ordered[index - 1], ordered[index]
            low_hand = int(getattr(low, "hand", 0))
            high_hand = int(getattr(high, "hand", 0))
            if low_hand == high_hand:
                continue
            need = _cross_hand_target(_pitch(high) - _pitch(low), settings)
            current = (
                (int(high.min_key) + int(high.max_key)) / 2.0
                - (int(low.min_key) + int(low.max_key)) / 2.0
            )
            deficit = int(need - current + 0.999) if current < need else 0
            if deficit <= 0:
                continue
            # 高音側整手往上、低音側整手往下，各分擔一半
            up = [n for n in group if int(getattr(n, "hand", 0)) == high_hand
                  and _pitch(n) >= _pitch(high)]
            down = [n for n in group if int(getattr(n, "hand", 0)) == low_hand
                    and _pitch(n) <= _pitch(low)]
            before = order_cost(up) + order_cost(down)
            for up_shift in range(deficit, -1, -1):
                down_shift = deficit - up_shift
                if not _shift_notes(notes, group, up, up_shift, activity, lane_hi):
                    continue
                if not _shift_notes(notes, group, down, -down_shift, activity, lane_hi):
                    _shift_notes(notes, group, up, -up_shift, activity, lane_hi, force=True)
                    continue
                ok = (
                    not _group_overlaps(group)
                    and not _pitch_order_violations(group)
                    and order_cost(up) + order_cost(down) <= before
                )
                if not ok:
                    _shift_notes(notes, group, up, -up_shift, activity, lane_hi, force=True)
                    _shift_notes(notes, group, down, down_shift, activity, lane_hi, force=True)
                    continue
                moved += 1
                break
    return moved


def _shift_notes(
    notes: Sequence[Any],
    group: Sequence[Any],
    movers: Sequence[Any],
    delta: int,
    activity: Any,
    lane_hi: int,
    force: bool = False,
) -> bool:
    """把一組音符整體平移 delta 個鍵道；越界或撞到長押走廊就整批放棄。"""
    if not delta or not movers:
        return True
    for note in movers:
        new_low = int(note.min_key) + delta
        new_high = int(note.max_key) + delta
        if not force and (new_low < 0 or new_high > lane_hi):
            return False
        if not force and not _range_avoids_hold(
            notes, group, note, activity, new_low, new_high
        ):
            return False
    for note in movers:
        note.min_key = int(note.min_key) + delta
        note.max_key = int(note.max_key) + delta
    return True


def _clamp_note_widths(
    notes: Sequence[Any],
    settings: SmartChartSettings,
) -> int:
    """任何比標準寬度寬的音符都收回去。

    寬度在官方語料裡是**難度常數**：real 97% 是 3、extreme 99% 是 3，剩下的是
    收窄成 2 的密和絃，沒有一顆比標準寬。排譜有二十幾道修復通道會互相拆台
    （見 arranger 的通道順序註解），只要有一道往外長一格就會留在成品裡，所以
    這裡當成收尾的不變量再掃一次，而不是只信任每一道自己守規矩。

    收的是**左緣**：右緣 `max_key` 是排序與視覺的權威，整套修復的結論都建立在
    它上面。只收左緣的話音符只會變小、右緣不動，不可能生出新的重疊或順序問題，
    所以放在所有通道之後跑是安全的。
    """
    limit = max(1, int(settings.normal_width))
    clamped = 0
    for note in notes:
        if _width(note) <= limit:
            continue
        note.min_key = int(note.max_key) - limit + 1
        clamped += 1
    return clamped


def _restore_unneeded_narrowing(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """把「其實不需要收窄」的音符還原回正常寬度。

    收窄本來就該是最後手段，但中途十幾道修復各自收了一格之後，後面的位移
    往往又把空間讓了出來，沒有人把寬度還回去。量官方 real 語料：單手同時
    1/2/3 音的收窄率只有 0.0% / 0.2% / 6.3%，而同時 4 音是 88%。所以這裡
    只還原「非高密度和絃」的音符，4 音以上的收窄保持不動。

    優先往低音側加寬，因為順序的權威是右緣（`max_key`），低音側加寬不會
    動到任何排序結論；低音側卡住才試高音側，並且要重驗順序。
    """
    restored = 0
    target = max(1, int(settings.normal_width))
    dense = max(2, int(settings.dense_hand_threshold))
    activity = _build_hold_activity(groups, settings)
    neighbours = _build_sequence_neighbours(notes, settings)
    lane_hi = int(settings.total_lanes) - 1
    for group in groups:
        by_hand: Dict[int, List[Any]] = {}
        for note in group:
            by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
        for hand_notes in by_hand.values():
            if len(hand_notes) >= dense:
                continue          # 同時 4 音的收窄是正常演奏表現，不還原
            if _hand_span_wants_narrowing(hand_notes, settings):
                # 只有落在「度數不夠」那段跨度的收窄才是刻意的，不還原。
                # 其餘（例如三顆擠在 4 個半音內）中途被修復通道收掉的寬度
                # 仍然要還原——手寫譜在那一段的收窄率只有 12%，放著不管會
                # 變成 75%。
                continue
            for note in hand_notes:
                while int(note.max_key) - int(note.min_key) + 1 < target:
                    low, high = int(note.min_key), int(note.max_key)
                    # 往低音側加寬不動 max_key，順序結論不變，所以優先；
                    # 往高音側會改右緣，得連跨組的前後順序一起驗。
                    before = _neighbour_order_cost([note], neighbours)
                    # 一律往左（低音側）加寬：右緣 max_key 是排序與視覺的
                    # 權威，2→3 如果往右長，那條線就跟著跑掉了。左邊卡住就
                    # 維持寬度 2，不改右緣。
                    for new_low, new_high in ((low - 1, high),):
                        if new_low < 0 or new_high > lane_hi:
                            continue
                        if not _range_avoids_hold(
                            notes, group, note, activity, new_low, new_high
                        ):
                            continue
                        note.min_key, note.max_key = new_low, new_high
                        if (
                            _group_overlaps(group)
                            or _pitch_order_violations(group)
                            or _neighbour_order_cost([note], neighbours) > before
                        ):
                            note.min_key, note.max_key = low, high
                            continue
                        restored += 1
                        break
                    else:
                        break
    return restored


def _hold_corridor_conflicts(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    conflicts = 0
    activity = _build_hold_activity(groups, settings)
    for group in groups:
        for note in group:
            conflicts += not _range_avoids_hold(
                notes,
                group,
                note,
                activity,
                int(note.min_key),
                int(note.max_key),
            )
    return conflicts


def _repair_hold_corridors(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Repack only groups that a later correction moved onto an active hold."""
    repairs = 0
    activity = _build_hold_activity(groups, settings)
    origin_group = {
        id(note): group for group in groups for note in group
    }
    for group in groups:
        by_hand = {
            hand: sorted(
                [
                    note
                    for note in group
                    if int(getattr(note, "hand", 0)) == hand
                ],
                key=lambda note: (_pitch(note), int(note.min_key)),
            )
            for hand in (0, 1)
        }
        for hand, hand_notes in by_hand.items():
            obstacles = _hold_obstacles(
                notes, group, hand, activity
            )
            if not hand_notes or not obstacles:
                continue
            if all(
                _range_avoids_hold(
                    notes,
                    group,
                    note,
                    activity,
                    int(note.min_key),
                    int(note.max_key),
                )
                for note in hand_notes
            ):
                continue
            other = by_hand[1 - hand]
            if hand == 1 and other:
                lane_lo = 0
                lane_hi = min(int(note.min_key) for note in other) - 1
            elif hand == 0 and other:
                lane_lo = max(int(note.max_key) for note in other) + 1
                lane_hi = settings.total_lanes - 1
            else:
                lane_lo = 0
                lane_hi = settings.total_lanes - 1
            widths = [
                int(note.max_key) - int(note.min_key) + 1
                for note in hand_notes
            ]
            targets = {
                id(note): (
                    (int(note.min_key) + int(note.max_key)) / 2.0,
                    0.0,
                )
                for note in hand_notes
            }
            before = [
                (int(note.min_key), int(note.max_key))
                for note in hand_notes
            ]
            capacity = max(0, lane_hi - lane_lo + 1)
            candidates = [
                list(widths),
                # 收窄是為了閃開長押走廊，但寬度下限仍然是 min_note_width
                [max(int(settings.min_note_width), width - 1) for width in widths],
                [max(1, int(settings.min_note_width))] * len(widths),
            ]
            placed = False
            for candidate_widths in candidates:
                if sum(candidate_widths) > capacity:
                    continue
                for note, (start, end) in zip(hand_notes, before):
                    note.min_key = start
                    note.max_key = end
                _place_hand_avoiding_obstacles(
                    hand_notes,
                    targets,
                    candidate_widths,
                    lane_lo,
                    lane_hi,
                    obstacles,
                )
                placed = all(
                    _range_avoids_hold(
                        notes,
                        group,
                        note,
                        activity,
                        int(note.min_key),
                        int(note.max_key),
                    )
                    for note in hand_notes
                )
                if placed:
                    break
            if not placed:
                for note, (start, end) in zip(hand_notes, before):
                    note.min_key = start
                    note.max_key = end
                continue
            after = [
                (int(note.min_key), int(note.max_key))
                for note in hand_notes
            ]
            repairs += before != after
        # A hand can be completely boxed in by its own active holds while the
        # other hand occupies the only free side. Repack the complete onset
        # group so the other hand may move aside instead of accepting a start
        # inside a hold.
        if any(
            not _range_avoids_hold(
                notes,
                group,
                note,
                activity,
                int(note.min_key),
                int(note.max_key),
            )
            for note in group
        ):
            before = [
                (int(note.min_key), int(note.max_key))
                for note in group
            ]
            desired = {
                id(note): (
                    (int(note.min_key) + int(note.max_key)) / 2.0,
                    0.0,
                )
                for note in group
            }
            widths = [
                int(note.max_key) - int(note.min_key) + 1
                for note in group
            ]
            candidates = [
                list(widths),
                # 收窄是為了閃開長押走廊，但寬度下限仍然是 min_note_width
                [max(int(settings.min_note_width), width - 1) for width in widths],
                [max(1, int(settings.min_note_width))] * len(widths),
            ]
            placed = False
            for candidate_widths in candidates:
                for note, (start, end) in zip(group, before):
                    note.min_key = start
                    note.max_key = end
                if not _place_group_avoiding_hold_obstacles(
                    notes,
                    group,
                    desired,
                    candidate_widths,
                    activity,
                    settings,
                ):
                    continue
                placed = (
                    _group_overlaps(group) == 0
                    and _pitch_order_violations(group) == 0
                    and all(
                        _range_avoids_hold(
                            notes,
                            group,
                            note,
                            activity,
                            int(note.min_key),
                            int(note.max_key),
                        )
                        for note in group
                    )
                )
                if placed:
                    break
            if placed:
                repairs += before != [
                    (int(note.min_key), int(note.max_key))
                    for note in group
                ]
            else:
                for note, (start, end) in zip(group, before):
                    note.min_key = start
                    note.max_key = end
                # The new pitch may fall between two adjacent sustained
                # pitches, leaving no legal lane even at width one. Treat all
                # notes sounding at this instant as a temporary chord and
                # open the required gap by moving/shrinking the sustained
                # notes as well.
                active_notes = list(
                    tuple(activity.get((id(group), 0), ()))
                    + tuple(activity.get((id(group), 1), ()))
                )
                combined: List[Any] = []
                seen_ids = set()
                related_notes: List[Any] = list(group)
                for active_note in active_notes:
                    related_notes.extend(origin_group[id(active_note)])
                for item in related_notes:
                    if id(item) not in seen_ids:
                        combined.append(item)
                        seen_ids.add(id(item))
                combined_before = [
                    (int(note.min_key), int(note.max_key))
                    for note in combined
                ]
                combined_desired = {
                    id(note): (
                        (int(note.min_key) + int(note.max_key)) / 2.0,
                        0.0,
                    )
                    for note in combined
                }
                combined_widths = [
                    int(note.max_key) - int(note.min_key) + 1
                    for note in combined
                ]
                sounding_placed = False
                floor_w = max(1, int(settings.min_note_width))
                width_candidates = (
                    combined_widths,
                    [max(floor_w, width - 1) for width in combined_widths],
                    [floor_w] * len(combined_widths),
                )
                placement_candidates = [
                    (candidate, True)
                    for candidate in width_candidates
                ] + [
                    (candidate, False)
                    for candidate in width_candidates
                ]
                for candidate_widths, reserve_gaps in placement_candidates:
                    width_by_id = {
                        id(note): int(width)
                        for note, width in zip(
                            combined, candidate_widths
                        )
                    }
                    pitch_ordered = sorted(
                        combined,
                        key=lambda note: (
                            _pitch(note),
                            int(getattr(note, "index", 0)),
                        ),
                    )
                    gap_after = {
                        id(note): (
                            1
                            if reserve_gaps
                            and index + 1 < len(pitch_ordered)
                            and _pitch(note)
                            < _pitch(pitch_ordered[index + 1])
                            else 0
                        )
                        for index, note in enumerate(pitch_ordered)
                    }
                    effective_widths = [
                        width_by_id[id(note)] + gap_after[id(note)]
                        for note in combined
                    ]
                    if sum(effective_widths) > settings.total_lanes:
                        continue
                    for note, (start, end) in zip(
                        combined, combined_before
                    ):
                        note.min_key = start
                        note.max_key = end
                    _place_hand(
                        combined,
                        combined_desired,
                        effective_widths,
                        0,
                        settings.total_lanes - 1,
                    )
                    for note in combined:
                        note.max_key = (
                            int(note.min_key)
                            + width_by_id[id(note)]
                            - 1
                        )
                    sounding_placed = (
                        _group_overlaps(combined) == 0
                        and _pitch_order_violations(combined) == 0
                        and all(
                            _range_avoids_hold(
                                notes,
                                group,
                                note,
                                activity,
                                int(note.min_key),
                                int(note.max_key),
                            )
                            for note in group
                        )
                    )
                    if sounding_placed:
                        break
                if sounding_placed:
                    repairs += combined_before != [
                        (int(note.min_key), int(note.max_key))
                        for note in combined
                    ]
                else:
                    for note, (start, end) in zip(
                        combined, combined_before
                    ):
                        note.min_key = start
                        note.max_key = end
    return repairs


def _repair_sustain_directionally(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """Open persistent pitch gaps without repacking earlier gaps closed."""
    repairs = 0
    origin_group = {
        id(note): group for group in groups for note in group
    }
    for _ in range(4):
        activity = _build_hold_activity(groups, settings)
        changed = 0
        for group in groups:
            active = list(
                tuple(activity.get((id(group), 0), ()))
                + tuple(activity.get((id(group), 1), ()))
            )
            if not active:
                continue
            for note in sorted(group, key=lambda item: _pitch(item)):
                if _range_avoids_hold(
                    notes,
                    group,
                    note,
                    activity,
                    int(note.min_key),
                    int(note.max_key),
                ):
                    continue
                note_pitch = _pitch(note)
                group_lower = [
                    other for other in group
                    if other is not note and _pitch(other) < note_pitch
                ]
                group_higher = [
                    other for other in group
                    if other is not note and _pitch(other) > note_pitch
                ]
                lower = [
                    item for item in active if _pitch(item) < note_pitch
                ] + group_lower
                higher = [
                    item for item in active if _pitch(item) > note_pitch
                ] + group_higher

                def bounds() -> Tuple[int, int]:
                    lo = (
                        max(
                            int(item.max_key)
                            for item in lower
                            if _pitch(item)
                            == max(_pitch(x) for x in lower)
                        )
                        + 1
                        if lower
                        else 0
                    )
                    hi = (
                        min(
                            int(item.min_key)
                            for item in higher
                            if _pitch(item)
                            == min(_pitch(x) for x in higher)
                        )
                        - 1
                        if higher
                        else settings.total_lanes - 1
                    )
                    return lo, hi

                lo, hi = bounds()
                if lo > hi and higher:
                    threshold = min(_pitch(item) for item in higher)
                    move_items: List[Any] = []
                    seen = set()
                    for active_note in active:
                        if _pitch(active_note) < threshold:
                            continue
                        for item in origin_group[id(active_note)]:
                            if (
                                _pitch(item) >= threshold
                                and id(item) not in seen
                            ):
                                move_items.append(item)
                                seen.add(id(item))
                    if move_items and max(
                        int(item.max_key) for item in move_items
                    ) < settings.total_lanes - 1:
                        for item in move_items:
                            item.min_key = int(item.min_key) + 1
                            item.max_key = int(item.max_key) + 1
                        repairs += 1
                        changed += 1
                        lo, hi = bounds()
                if lo > hi and lower:
                    threshold = max(_pitch(item) for item in lower)
                    move_items = []
                    seen = set()
                    for active_note in active:
                        if _pitch(active_note) > threshold:
                            continue
                        for item in origin_group[id(active_note)]:
                            if (
                                _pitch(item) <= threshold
                                and id(item) not in seen
                            ):
                                move_items.append(item)
                                seen.add(id(item))
                    if move_items and min(
                        int(item.min_key) for item in move_items
                    ) > 0:
                        for item in move_items:
                            item.min_key = int(item.min_key) - 1
                            item.max_key = int(item.max_key) - 1
                        repairs += 1
                        changed += 1
                        lo, hi = bounds()
                if lo > hi:
                    continue
                occupied = {
                    lane
                    for other in group
                    if other is not note
                    for lane in range(
                        int(other.min_key),
                        int(other.max_key) + 1,
                    )
                }
                feasible = [
                    lane for lane in range(lo, hi + 1)
                    if lane not in occupied
                ]
                if not feasible and higher:
                    threshold = min(_pitch(item) for item in higher)
                    move_items = []
                    seen = set()
                    for active_note in active:
                        if _pitch(active_note) < threshold:
                            continue
                        for item in origin_group[id(active_note)]:
                            if (
                                _pitch(item) >= threshold
                                and id(item) not in seen
                            ):
                                move_items.append(item)
                                seen.add(id(item))
                    if move_items and max(
                        int(item.max_key) for item in move_items
                    ) < settings.total_lanes - 1:
                        for item in move_items:
                            item.min_key = int(item.min_key) + 1
                            item.max_key = int(item.max_key) + 1
                        repairs += 1
                        changed += 1
                        lo, hi = bounds()
                        feasible = [
                            lane for lane in range(lo, hi + 1)
                            if lane not in occupied
                        ]
                if not feasible and lower:
                    threshold = max(_pitch(item) for item in lower)
                    move_items = []
                    seen = set()
                    for active_note in active:
                        if _pitch(active_note) > threshold:
                            continue
                        for item in origin_group[id(active_note)]:
                            if (
                                _pitch(item) <= threshold
                                and id(item) not in seen
                            ):
                                move_items.append(item)
                                seen.add(id(item))
                    if move_items and min(
                        int(item.min_key) for item in move_items
                    ) > 0:
                        for item in move_items:
                            item.min_key = int(item.min_key) - 1
                            item.max_key = int(item.max_key) - 1
                        repairs += 1
                        changed += 1
                        lo, hi = bounds()
                        feasible = [
                            lane for lane in range(lo, hi + 1)
                            if lane not in occupied
                        ]
                if not feasible:
                    continue
                width = int(note.max_key) - int(note.min_key) + 1
                current = (
                    int(note.min_key) + int(note.max_key)
                ) / 2.0
                lane = min(feasible, key=lambda item: abs(item - current))
                # 舊版把音符壓成單一鍵道去對齊 feasible 車道；改成整顆平移
                # 過去、保留原寬度（官方譜沒有寬度 1）。
                new_min = int(round(lane - (width - 1) / 2.0))
                new_min = max(0, min(settings.total_lanes - width, new_min))
                new_max = new_min + width - 1
                if int(note.min_key) != new_min or int(note.max_key) != new_max:
                    note.min_key = new_min
                    note.max_key = new_max
                    repairs += 1
                    changed += 1
        if not changed:
            break
    return repairs


def _reflush_hand_chords(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """把同手同時 3 顆以上的和絃整組重排成貼合，中心維持原位。

    官方那種和絃是完全貼合的：k=3 貼合率 84%、外圍中心距**固定 6 格**（外圍
    音程從 5 半音到 24 半音量出來全部是 6），中間那顆固定落在正中央 —— 也就
    是三顆並排、內部完全不表現度數。度數只在「同手兩顆」和「跨手」表現。

    擺放時 `_place_hand` 已經照這條規則排好，但後面十幾道順序／走廊修復是
    一顆一顆搬的：實測光 `_discrete_final_order_repair` 一道就把 k≥3 的貼合率
    拆掉 159pp。所以這裡在最後把整組當成剛體重排一次，只有完全合法
    （組內不重疊、組內音高順序、長押走廊、兩手交界不變差、前後錯序不變多）
    才接受。
    """
    reach = max(0, int(settings.chord_flush_reach))
    activity = _build_hold_activity(groups, settings)
    anchors = _build_position_anchors(notes, settings)
    dependents = _build_hold_dependents(groups, activity)
    pairs_by_note: Dict[int, List[Tuple[Any, Any]]] = {}
    for first, second in _nearby_voice_pairs(notes, settings):
        pairs_by_note.setdefault(id(first), []).append((first, second))
        pairs_by_note.setdefault(id(second), []).append((first, second))

    def pair_bad(pair: Tuple[Any, Any]) -> bool:
        first, second = pair
        if _pitch(first) == _pitch(second):
            return False
        return (_pitch(first) - _pitch(second)) * (
            int(first.max_key) - int(second.max_key)
        ) <= 0

    fixed = 0
    for group in groups:
        by_hand: Dict[int, List[Any]] = {}
        for note in group:
            by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
        for hand, members in by_hand.items():
            if len(members) < 2:
                continue
            items = sorted(members, key=lambda n: (_pitch(n), int(n.min_key)))
            widths = [int(n.max_key) - int(n.min_key) + 1 for n in items]
            pitches = [_pitch(note) for note in items]
            hands = [hand] * len(items)
            sizes = {hand: len(items)}
            low = min(int(n.min_key) for n in items)
            high = max(int(n.max_key) for n in items)
            watched = [
                pair for note in items for pair in pairs_by_note.get(id(note), ())
            ]
            bad_before = sum(pair_bad(pair) for pair in watched)
            deficit_before = _cross_hand_deficit(group, settings)
            saved = [(int(n.min_key), int(n.max_key)) for n in items]
            # 寬度也一起還原：中途十幾道修復為了讓路把音符收成 2，等到這裡
            # 空間通常已經讓出來了，但和絃一旦排成貼合就再也沒有相鄰空格可
            # 以加寬（實測 k=3 收窄率因此卡在 8.7%，官方只有 4.5%）。所以
            # 先用「應有寬度」試排，排不下才退回目前的寬度。
            target = _initial_hand_widths(
                items, settings, _hand_span_wants_narrowing(items, settings)
            )
            options = [target]
            if widths != target:
                options.append(widths)
            placed = False
            for option in options:
                # 目標幾何：同手 3 顆以上貼合、剛好兩顆照官方階梯表留空隙。
                offsets = [0]
                for index in range(1, len(items)):
                    offsets.append(offsets[-1] + int(round(_chord_advance(
                        index, pitches, hands, option, sizes, settings
                    ))))
                span = offsets[-1] + option[-1]
                if span > settings.total_lanes:
                    continue
                if (option is widths
                        and high - low + 1 == span
                        and all(
                            int(note.min_key) - low == offset
                            for note, offset in zip(items, offsets)
                        )):
                    placed = True     # 已經是目標幾何，不用動
                    break
                # 首選是「最高音的右緣不動」的擺法 —— 那條線是視覺權威，也是
                # 同音位置一致性判斷的依據，收空隙時應該讓低音往上靠，而不是
                # 把整個和絃平移。放不下才退而求其次維持整組中心。
                top_anchor = high - (offsets[-1] + option[-1] - 1)
                centred = int(round((low + high) / 2.0 - span / 2.0))
                limit = settings.total_lanes - span
                candidates = [top_anchor, centred]
                for offset in range(1, reach + 1):
                    candidates.extend((top_anchor - offset, top_anchor + offset))
                best: Optional[Tuple[Tuple[float, float], int]] = None
                seen: set = set()
                for rank, start in enumerate(candidates):
                    if start < 0 or start > limit or start in seen:
                        continue
                    seen.add(start)
                    for note, width, offset in zip(items, option, offsets):
                        note.min_key = start + offset
                        note.max_key = start + offset + width - 1
                    if _group_overlaps(group) or _pitch_order_violations(group):
                        continue
                    if _cross_hand_deficit(group, settings) > deficit_before:
                        continue
                    if sum(pair_bad(pair) for pair in watched) > bad_before:
                        continue
                    if any(
                        not _range_avoids_hold(
                            notes, group, note, activity,
                            int(note.min_key), int(note.max_key),
                        )
                        for note in items
                    ):
                        continue
                    if not _hold_corridor_clear(
                        notes, activity, dependents, items
                    ):
                        continue
                    # 合法的擺法裡挑「最高音最貼近同音高錨點」的那個 —— 收
                    # 空隙不該把那條線帶偏，官方在 2 秒窗內同音高是不動的。
                    score = (
                        _anchor_deviation(anchors, items[-1], settings,
                                          int(items[-1].max_key)),
                        float(rank),
                    )
                    if best is None or score < best[0]:
                        best = (score, start)
                if best is None:
                    continue
                start = best[1]
                for note, width, offset in zip(items, option, offsets):
                    note.min_key = start + offset
                    note.max_key = start + offset + width - 1
                fixed += 1
                placed = True
                break
            if not placed:
                for note, (start, end) in zip(items, saved):
                    note.min_key = start
                    note.max_key = end
    return fixed


def _repair_hand_top_edge_strict(
    notes: Sequence[Any],
    groups: Sequence[Sequence[Any]],
    settings: SmartChartSettings,
) -> int:
    """收尾：讓每隻手「同一時刻的最高音」在前後之間嚴格分得出高低。

    使用者要的是 56 / 54 / 59 就該落在 22 / 21 / 23 —— 音高順序必須原封不動
    反映在最高音的右緣上，而且**不可以同格**（同格等於看不出高低）。前面的
    通道是照比例和輪廓在擺，壓縮到最後常常把兩個不同音高壓成同一個右緣：
    加這道之前是反向 0.97% ＋ 同格 1.03%。

    位移優先是剛體（整隻手在該組的音符一起移，和絃內部的貼合與度數完全不
    動）；整組移不動時才單獨移最高音那一顆 —— 會拉出一點和絃空隙，但「看得
    出誰高誰低」比和絃貼合更優先，這是使用者指定的順序。

    試過用 DP 把整條鏈一次解（成本 = 違規數優先、位移量其次），結果反而
    退步到 1.70%：DP 的可行位移是「假設其他事件不動」算的，逐一套用時前面
    的移動會讓後面的解失效，退回之後比貪心更亂。所以維持貪心。
    """
    if not groups:
        return 0
    activity = _build_hold_activity(groups, settings)
    dependents = _build_hold_dependents(groups, activity)
    window = max(1, int(settings.pitch_trend_window_ms))
    reach = max(1, int(settings.top_step_strict_reach))

    hand_events: Dict[int, List[Tuple[int, Sequence[Any], List[Any], Any]]] = {}
    for group in groups:
        by_hand: Dict[int, List[Any]] = {}
        for note in group:
            by_hand.setdefault(int(getattr(note, "hand", 0)), []).append(note)
        for hand, members in by_hand.items():
            top = max(members, key=lambda n: (_pitch(n), int(n.max_key)))
            hand_events.setdefault(hand, []).append(
                (min(_start(n) for n in members), group, members, top)
            )
    for events in hand_events.values():
        events.sort(key=lambda item: item[0])

    def bad(first: Any, second: Any) -> bool:
        if _pitch(first) == _pitch(second):
            return False
        edge = int(second.max_key) - int(first.max_key)
        if edge == 0:
            return True
        return (_pitch(second) - _pitch(first)) * edge < 0

    def neighbour_bad(events, index: int) -> int:
        count = 0
        for other in (index - 1, index + 1):
            if not (0 <= other < len(events)):
                continue
            lo, hi = (index, other) if index < other else (other, index)
            if events[hi][0] - events[lo][0] > window:
                continue
            count += bad(events[lo][3], events[hi][3])
        return count

    def shift(movers: Sequence[Any], delta: int) -> None:
        for note in movers:
            note.min_key = int(note.min_key) + delta
            note.max_key = int(note.max_key) + delta

    def legal(group: Sequence[Any], movers: Sequence[Any],
              deficit: float, spacing: float) -> bool:
        if any(int(n.min_key) < 0 or int(n.max_key) >= settings.total_lanes
               for n in movers):
            return False
        if _group_overlaps(group) or _pitch_order_violations(group):
            return False
        if _cross_hand_deficit(group, settings) > deficit:
            return False
        if _chord_spacing_deficit(group, settings) > spacing:
            return False
        if not _hold_corridor_clear(notes, activity, dependents, movers):
            return False
        return all(
            _range_avoids_hold(
                notes, group, note, activity,
                int(note.min_key), int(note.max_key),
            )
            for note in movers
        )

    repairs = 0
    for _ in range(max(1, int(settings.top_step_strict_passes))):
        changed = 0
        for events in hand_events.values():
            for index in range(len(events) - 1):
                first_time, _fg, _fm, first = events[index]
                second_time, _sg, _sm, second = events[index + 1]
                if second_time - first_time > window:
                    continue
                if not bad(first, second):
                    continue
                want = 1 if _pitch(second) > _pitch(first) else -1
                stages = [(index + 1, False), (index, False)]
                if settings.top_step_strict_solo:
                    stages += [(index + 1, True), (index, True)]
                fixed = False
                for target, solo in stages:
                    _t, group, members, top = events[target]
                    movers = [top] if solo else members
                    sign = want if target == index + 1 else -want
                    base_bad = neighbour_bad(events, target)
                    deficit = _cross_hand_deficit(group, settings)
                    spacing = _chord_spacing_deficit(group, settings)
                    for step in range(1, reach + 1):
                        delta = sign * step
                        shift(movers, delta)
                        if (legal(group, movers, deficit, spacing)
                                and not bad(events[index][3], events[index + 1][3])
                                and neighbour_bad(events, target) <= base_bad):
                            repairs += 1
                            changed += 1
                            fixed = True
                            break
                        shift(movers, -delta)
                    if fixed:
                        break
        if not changed:
            break
    return repairs


def _pitch_order_violations(group: Sequence[Any]) -> int:
    """音高順序以「音符最右那一格」為準，不是中心。

    玩家讀的是音符右緣的相對位置；用中心比較會在寬度不同時得到和視覺相反
    的結論（例如寬 3 與寬 2 的中心差半格，但右緣其實已經排對了）。
    """
    ordered = sorted(group, key=lambda note: (_pitch(note), int(note.min_key)))
    violations = 0
    previous_pitch = None
    previous_edge = None
    for note in ordered:
        pitch = _pitch(note)
        edge = int(note.max_key)
        # 嚴格：音高有高低之分，最右格就必須有大小之分。相等不算排好
        # （舊版只把 `<` 算違規，等於留了一個「並排」的漏洞）。
        if (
            previous_pitch is not None
            and pitch > previous_pitch
            and previous_edge is not None
            and edge <= previous_edge
        ):
            violations += 1
        previous_pitch = pitch
        previous_edge = edge
    return violations


def arrange_midi_notes(
    notes: Iterable[Any],
    settings: SmartChartSettings | None = None,
) -> SmartChartStats:
    """Arrange MIDI-backed notes in place without removing or retiming them."""
    config = settings or SmartChartSettings()
    note_list = list(notes)
    _clear_cluster_cache()
    stats = SmartChartStats(notes=len(note_list))
    if not note_list:
        return stats
    _prime_pitch_cache(note_list)

    groups = _clusters(note_list, config.onset_tolerance_ms)
    stats.groups = len(groups)
    stats.hand_changes = _assign_hands(note_list, groups, config)
    # 大跳修正**只在分手是猜出來的時候**才跑。兩軌以上的 MIDI，音軌就是作曲者
    # 自己寫的左右手，那比任何啟發式都準；實測它只把同手大跳率壓下 0.4~1.2 個
    # 百分點（17.4→16.2、17.9→17.5、8.9→8.4、12.0→11.4），代價卻是把 100 顆上下
    # 的音符判到和音軌相反的手（初音未來的消失：8 顆 → 119 顆）。那正是「明顯是
    # 右手的變左手／左手的變右手」的來源。
    if not _hands_came_from_tracks(note_list):
        stats.hand_changes += _reduce_hand_leaps(groups, config)
    if config.classify_articulations:
        (
            stats.articulation_changes,
            stats.slide_notes,
            stats.trill_patterns,
        ) = _classify_articulations(note_list, config)
    desired = _local_pitch_maps(note_list, groups, config)
    stats.block_layouts = _apply_block_layout(note_list, groups, desired, config)
    stats.motif_reuses = _apply_motif_memory(groups, desired, config)
    stats.pitch_anchor_adjusted = _anchor_pitch_positions(note_list, desired, config)
    (
        stats.dp_shifted_groups,
        stats.context_compressed_groups,
        stats.global_edge_anchored_groups,
    ) = _optimize_group_shifts(note_list, groups, desired, config)
    stats.macro_adjusted_groups = _apply_macro_group_contours(
        groups, desired, config
    )
    stats.motion_limited_groups = _limit_hand_motion(
        groups, desired, config
    )
    stats.nearby_order_repairs = _enforce_nearby_pitch_order(
        note_list, desired, config
    )
    stats.nearby_order_repairs += _enforce_top_edge_targets(
        note_list, desired, config
    )

    for group in groups:
        by_hand = {
            hand: sorted(
                [note for note in group if int(getattr(note, "hand", 0)) == hand],
                key=lambda note: (_pitch(note), _start(note), int(getattr(note, "index", 0))),
            )
            for hand in (0, 1)
        }
        # 寬度仍照「單手」的密度/音程決定（那是演奏概念），但容量預算是
        # 整組共用一份，不再各自切一半 —— 否則一手擠爆時另一手明明有空位
        # 也用不到，兩邊都被壓縮。
        ordered = sorted(
            group,
            key=lambda note: (_pitch(note), _start(note), int(getattr(note, "index", 0))),
        )
        # 小度數收窄是「排不下才用」的手段：官方 real 語料裡 k=2/3 和絃只有
        # 5~12% 會收窄，反而單手同時 4 音有 88% 收窄。所以先試整組滿寬，
        # 放得下就完全不收。
        full = _initial_hand_widths_full(by_hand, config)
        # 「整組滿寬塞不進 28 格」幾乎不會成立，所以小度數收窄形同沒開過。
        # 這個曲庫的手寫譜是靠**收窄**擠空間的（不像官方靠重疊）：實測 8 首
        # 的寬度 2 比率 14.6%，官方只有 3.0%；同手 3 顆時各跨度是
        # 12%/28%/33%/13%，官方是 1.7%/5.6%/3.3%/0.3%。所以改成「這隻手同時
        # 有 3 顆以上就允許收窄」，塞不下的情形一樣保留。
        allow_close = _required_group_span(ordered, [full[id(n)] for n in ordered], config) > config.total_lanes
        widths: Dict[int, List[int]] = {}
        for hand, hand_notes in by_hand.items():
            allow = allow_close or _hand_span_wants_narrowing(hand_notes, config)
            widths[hand] = _initial_hand_widths(hand_notes, config, allow)
        width_by_note: Dict[int, int] = {}
        for hand, hand_notes in by_hand.items():
            for note, width in zip(hand_notes, widths[hand]):
                width_by_note[id(note)] = width
        ordered_widths = [width_by_note[id(note)] for note in ordered]
        stats.forced_width_one_notes += _shrink_to_capacity(
            ordered_widths, config.total_lanes, config.min_note_width
        )
        for note, width in zip(ordered, ordered_widths):
            width_by_note[id(note)] = width
        _place_group_jointly(group, desired, width_by_note, config)

        stats.unresolved_overlaps += _group_overlaps(group)

    stats.nearby_order_repairs += _repair_final_nearby_pitch_order(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _discrete_final_order_repair(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _greedy_group_order_repair(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_top_edge_order(
        note_list, groups, config
    )
    # Top-edge and width repairs can move a note that itself remains active
    # into later groups. Re-sweep chronologically so those future constraints
    # are evaluated after every visual adjustment.
    for _ in range(4):
        repaired = _repair_hold_corridors(
            note_list, groups, config
        )
        stats.nearby_order_repairs += repaired
        if not repaired:
            break
    stats.nearby_order_repairs += _repair_sustain_directionally(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_hold_corridors(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_macro_group_contours(
        note_list, groups, config
    )
    # Macro movement never intentionally introduces a close voice inversion,
    # but an inversion that was blocked by the original chord boundaries may
    # become movable afterward. Give the hard local constraint one final pass.
    stats.nearby_order_repairs += _discrete_final_order_repair(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _greedy_group_order_repair(
        note_list, groups, config
    )
    # The top-note right edge is the final visual authority.
    stats.nearby_order_repairs += _repair_top_edge_order(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_hold_corridors(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_macro_group_contours(
        note_list, groups, config
    )
    stats.small_interval_restores += _repair_small_interval_distinction(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_top_edge_order(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_hold_corridors(
        note_list, groups, config
    )
    stats.small_interval_restores += _repair_small_interval_distinction(
        note_list, groups, config
    )
    # Movement-based small-interval recovery may propagate a chromatic run
    # into a neighbouring voice. Restore the hard pitch order while keeping
    # every candidate outside active hold corridors, then use width only for
    # any final small interval that has no free lane.
    stats.nearby_order_repairs += _discrete_final_order_repair(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _greedy_group_order_repair(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_hold_corridors(
        note_list, groups, config
    )
    stats.small_interval_restores += _repair_small_interval_width_cues(
        note_list, groups, config
    )
    stats.nearby_order_repairs += _repair_top_edge_order(
        note_list, groups, config
    )
    # 最高音只固定「趨勢」（嚴格排序），不強制每一步的位移量 —— 人工譜對
    # 步進量其實是寬鬆的（實測 50.7% 不符合固定比例），硬套反而會把音符
    # 從正確的縱向間距上推開。步進比例修復保留在 _repair_top_edge_proportion，
    # 需要時再接回流程。
    stats.pitch_consistency_moves = _repair_pitch_position_consistency(
        note_list, groups, config
    )
    if stats.pitch_consistency_moves:
        # 拉齊位置有可能讓某對最高音的順序退步，重跑順序收尾
        stats.nearby_order_repairs += _repair_top_edge_order(
            note_list, groups, config
        )
    stats.backtrack_moves = _backtrack_top_contour(note_list, groups, config)
    if stats.backtrack_moves:
        # 回溯有可能為了比例讓某對順序退步，最後再收一次順序
        stats.nearby_order_repairs += _repair_top_edge_order(
            note_list, groups, config
        )
    stats.phrase_shifts = _align_phrase_extremes(note_list, groups, config)
    stats.ties_broken = _break_order_ties(note_list, groups, config)
    stats.hand_boundary_widened = _widen_hand_boundary(
        note_list, groups, config
    )
    stats.width_restored_notes = _restore_unneeded_narrowing(
        note_list, groups, config
    )
    # 位置一致性是最後一道搬動音符的通道。官方在 2 秒窗內同一個音高幾乎不動
    # （同音鍵道差的中位數是 0、只有 10% 差到 3 格以上），而 _backtrack_top_contour
    # ／_align_phrase_extremes ／_widen_hand_boundary 是照「輪廓」和「交界」在
    # 搬音符、沒有人看同音高：實測它們合計把「同音相距 ≥3 格」推高 +83 個百分點，
    # 剛好把前面那道 _repair_pitch_position_consistency 的 -51 吃掉。
    # 這道自己會擋住「壓縮兩手交界」的移動，所以放在 _widen_hand_boundary 後面
    # 也不會把交界又擠回去。
    consistency_moves = _repair_pitch_position_consistency(
        note_list, groups, config
    )
    stats.pitch_consistency_moves += consistency_moves
    stats.pitch_consistency_moves += _snap_repeated_pitch_lanes(
        note_list, groups, config
    )
    if consistency_moves:
        stats.nearby_order_repairs += _repair_top_edge_order(
            note_list, groups, config
        )
        stats.nearby_order_repairs += _repair_hold_corridors(
            note_list, groups, config
        )
        # 上面兩道會為了讓路臨時把音符收窄成 2，只有這裡會還原。
        stats.width_restored_notes += _restore_unneeded_narrowing(
            note_list, groups, config
        )
    # 和絃貼合是最後一道：整組當剛體重排，把前面逐顆搬動拆開的空隙收回來。
    stats.chord_reflushed = _reflush_hand_chords(note_list, groups, config)
    # 最後一道：單手最高音的高低必須看得出來。只做整組剛體平移，所以上面
    # 排好的和絃幾何（貼合／度數間距）原封不動。
    stats.hand_top_strict_repairs = _repair_hand_top_edge_strict(
        note_list, groups, config
    )
    # 「前後同音同軌」是使用者指定的最高優先項，所以吸附排在所有搬動音符的
    # 通道之後再收一次 —— 中段那一次跑完，後面還有交界、貼合、最高音輪廓
    # 五道會把它推開（實測同軌率因此從 64.7% 掉到 59.4%）。這一次沒有人會
    # 再動它。搬法一樣是剛體：整隻手 → 整組 → 單顆。
    if config.snap_repeat_final:
        stats.pitch_consistency_moves += _snap_repeated_pitch_lanes(
            note_list, groups, config
        )
    # 寬度的不變量收尾：只收左緣，右緣（排序權威）不動，所以不會推翻上面任何
    # 一道的結論。
    stats.width_clamped_notes = _clamp_note_widths(note_list, config)
    stats.width_two_notes = sum(
        int(note.max_key) - int(note.min_key) + 1 == 2 for note in note_list
    )
    stats.unresolved_overlaps = sum(_group_overlaps(group) for group in groups)
    stats.pitch_order_violations = sum(
        _pitch_order_violations(group) for group in groups
    )
    stats.pitch_order_violations += _nearby_pitch_order_violations(
        note_list, config
    )
    stats.macro_trend_violations = len(
        _macro_trend_violations(groups, config)
    )
    stats.top_edge_violations = len(
        _violating_top_edge_pairs(note_list, config)
    )
    stats.hold_corridor_conflicts = _hold_corridor_conflicts(
        note_list, groups, config
    )
    stats.small_interval_unresolved = _small_interval_unresolved(
        note_list, config
    )
    _clear_cluster_cache()
    _prime_pitch_cache(note_list, False)
    return stats

# ---------------------------------------------------------------------------
# 簡化版智慧排序（給編輯器右鍵用）
# ---------------------------------------------------------------------------

def interval_sort_notes(
    notes: Iterable[Any],
    settings: SmartChartSettings | None = None,
    lane_min: Optional[int] = None,
    lane_max: Optional[int] = None,
) -> Dict[str, int]:
    """依**音程**把一組音符排到鍵道上，就地修改，回傳統計。

    這是 `arrange_midi_notes` 的簡化路徑：只做「音高 → 鍵道位置」這一件事，
    不重新分左右手、不改音符類型、不動時間、不跑那一整串修復通道。給編輯器
    的右鍵「智慧排序」用——使用者已經自己選好了要排的那一段。

    和舊的 Alloc Section 差在哪：Alloc 是**依音高排名等距**分配，所以小二度和
    大七度會被排成一樣的間隔，度數層次整個消失。這裡改用官方語料量出來的
    `_wanted_interval_lanes()`（一個八度內約 |Δ半音| × 0.5，超過八度斜率降到
    0.25），間隔多大就排多開。

    lane_min / lane_max 給定可用的鍵道範圍（含），預設用這組音符目前占用的
    範圍。排出來比範圍寬時整體等比壓縮，比範圍窄時**不會**硬拉開——自然的
    音程間距本身就是目標值。

    同音高一律落在同一個鍵道位置（同一個音在畫面上該在同一個地方）。沒有
    pitch 的音符不動。
    """
    config = settings or SmartChartSettings()
    note_list = [n for n in notes if getattr(n, 'pitch', None) is not None]
    result = {'notes': len(note_list), 'moved': 0, 'narrowed': 0, 'unresolved': 0}
    if len(note_list) < 2:
        return result

    lo = int(lane_min) if lane_min is not None else min(int(n.min_key) for n in note_list)
    hi = int(lane_max) if lane_max is not None else max(int(n.max_key) for n in note_list)
    lo = max(0, min(config.total_lanes - 1, lo))
    hi = max(lo, min(config.total_lanes - 1, hi))

    # 1) 音高 → 相對鍵道偏移，間距由音程決定
    pitches = sorted({int(n.pitch) for n in note_list})
    offsets: Dict[int, float] = {pitches[0]: 0.0}
    for prev, cur in zip(pitches, pitches[1:]):
        step = _wanted_interval_lanes(cur - prev, config)
        step = min(step, float(config.max_interval_center_distance))
        offsets[cur] = offsets[prev] + step

    # 2) 放不下就整體等比壓縮（相對比例保留 = 仍然照音程）
    width_of = {p: max(1, max(int(n.max_key) - int(n.min_key) + 1
                              for n in note_list if int(n.pitch) == p))
                for p in pitches}
    span_needed = offsets[pitches[-1]] + width_of[pitches[-1]] - 1
    avail = float(hi - lo)
    if span_needed > avail and offsets[pitches[-1]] > 0:
        room = max(0.0, avail - (width_of[pitches[-1]] - 1))
        scale = room / offsets[pitches[-1]]
        offsets = {p: v * scale for p, v in offsets.items()}

    # 3) 套用；每顆保留自己的寬度
    for note in note_list:
        width = max(1, int(note.max_key) - int(note.min_key) + 1)
        new_min = lo + int(round(offsets[int(note.pitch)]))
        new_min = max(0, min(config.total_lanes - width, new_min))
        if new_min != int(note.min_key):
            note.min_key = new_min
            note.max_key = new_min + width - 1
            result['moved'] += 1

    # 4) 同時發聲的音符不能疊在一起：先收窄，再往右推
    for group in _clusters(note_list, config.onset_tolerance_ms):
        ordered = sorted(group, key=lambda n: (int(n.min_key), int(n.pitch)))
        for left, right in zip(ordered, ordered[1:]):
            if int(left.max_key) < int(right.min_key):
                continue
            # 先試收窄左邊那顆（官方在小度數密集處就是這樣處理）
            if int(left.max_key) - int(left.min_key) + 1 > config.min_note_width:
                left.max_key = int(left.min_key) + config.min_note_width - 1
                result['narrowed'] += 1
            if int(left.max_key) < int(right.min_key):
                continue
            width = max(1, int(right.max_key) - int(right.min_key) + 1)
            new_min = int(left.max_key) + 1
            if new_min + width - 1 <= config.total_lanes - 1:
                right.min_key = new_min
                right.max_key = new_min + width - 1
                result['moved'] += 1
            else:
                result['unresolved'] += 1

    _clear_cluster_cache()
    return result
