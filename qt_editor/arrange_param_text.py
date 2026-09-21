"""排譜參數的中文名稱與說明（給「MIDI 轉譜」對話框的參數表用）。

說明文字**不在這裡重寫**——直接抓 `smart_chart.SmartChartSettings` 裡那個欄位
上面的中文註解。那些註解本來就寫著每個參數是量什麼、為什麼是這個值，兩邊各寫
一份遲早會不一致。

`LABELS` 少一個欄位就會在測試裡被抓出來（見 test_arrange_options）。
"""

from __future__ import annotations

import inspect
import re
from dataclasses import fields
from typing import Dict, List, Tuple

from .smart_chart import SmartChartSettings

#: 分組：(組名, 欄位). 沒列到的欄位會被歸到「其他」。
GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ('基本', (
        'onset_tolerance_ms', 'edge_margin', 'normal_width', 'dense_width',
        'min_note_width', 'width_anchor', 'dense_hand_threshold',
        'hand_span_lanes', 'max_hand_chord_notes', 'minimum_pitch_span',
    )),
    ('表情記號', (
        'classify_articulations', 'classify_staccato', 'classify_soft',
        'classify_slide', 'staccato_gap_ratio', 'slide_max_gap_ms',
    )),
    ('左右手', (
        'trust_two_track_hands', 'hand_track_bias', 'hand_leap_window_ms',
        'hand_leap_semitones', 'hand_leap_weight', 'hand_memory_ms',
        'hand_boundary_margin', 'hand_boundary_min_distance',
        'cross_hand_slack_lanes',
    )),
    ('和絃與音程間距', (
        'close_chord_interval_semitones', 'chord_pair_close_semitones',
        'chord_pair_close_lanes', 'chord_pair_mid_semitones', 'chord_pair_mid_lanes',
        'chord_pair_far_semitones', 'chord_pair_far_gap', 'chord_pair_wide_semitones',
        'chord_pair_wide_gap', 'chord_flush_min_notes', 'chord_flush_weight',
        'chord_flush_reach', 'interval_snap_tolerance', 'min_octave_center_distance',
        'max_octave_center_distance', 'max_interval_center_distance',
        'octave_lanes_center', 'octave_lanes_edge', 'narrow_span_min',
        'narrow_span_max', 'narrow_whole_span_min', 'narrow_whole_span_max',
    )),
    ('旋律線（冠音／低音）', (
        'top_edge_window_ms', 'top_step_tolerance', 'top_step_lanes_per_semitone',
        'top_step_strict_reach', 'top_step_strict_passes', 'top_step_strict_solo',
        'melody_slope_passes', 'melody_slope_window_ms', 'melody_slope_reach',
        'melody_slope_bottom_weight', 'melody_slope_repeat_weight',
        'melody_slope_min_gain', 'melody_slope_far_repeat_weight',
        'melody_slope_far_repeat_ms', 'melody_slope_after_snap',
        'melody_slope_whole_group', 'melody_step_top', 'melody_step_bottom',
        'small_top_step_fix', 'small_top_step_side_slack',
        'small_top_step_repeat_slack',
    )),
    ('前後同音要在同一軌', (
        'pitch_consistency_window_ms', 'pitch_consistency_reach',
        'pitch_consistency_passes', 'snap_flat_min_semitones',
        'snap_repeat_window_ms', 'snap_repeat_reach', 'snap_repeat_passes',
        'snap_repeat_final', 'snap_block_drift_slack', 'pitch_anchor_weight',
        'pitch_anchor_window_ms', 'dp_anchor_deviation_weight', 'repair_anchor_weight',
    )),
    ('音高順序與修復', (
        'pitch_trend_window_ms', 'pitch_order_lookahead',
        'pitch_order_projection_passes', 'sequence_lanes_per_semitone',
        'sequence_knee_semitones', 'sequence_wide_lanes_per_semitone',
        'sequence_interval_weight', 'backtrack_lookback', 'backtrack_lane_reach',
        'backtrack_chain_span', 'backtrack_passes', 'backtrack_anchor_slack',
        'tie_break_passes', 'repair_time_budget_sec',
    )),
    ('樂句與整體版面', (
        'phrase_window_ms', 'phrase_gap_ms', 'phrase_shift_reach', 'close_time_ms',
        'macro_trend_window_ms', 'macro_pitch_threshold', 'macro_anchor_weight',
        'macro_transition_weight', 'use_trend_blocks', 'trend_jump_semitones',
        'trend_min_notes', 'block_layout', 'block_max_shift',
        'global_position_weight', 'global_edge_band_fraction',
        'global_edge_band_min_semitones', 'local_peak_prominence_semitones',
        'dp_shift_limit', 'dp_anchor_weight', 'dp_transition_weight',
        'dp_reserve_weight', 'dp_extreme_edge_weight', 'dp_drift_weight',
        'map_drift_lanes_per_sec', 'map_slope_rate_per_sec',
    )),
    ('長條', (
        'hold_corridor_min_beats', 'long_note_order_min_beats',
    )),
)

#: 欄位 -> 中文名稱
LABELS: Dict[str, str] = {
    # 基本
    'total_lanes': '總鍵道數',
    'beat_ms': '一拍幾毫秒',
    'allow_chord_overlap': '允許和絃鍵道重疊',
    'onset_tolerance_ms': '同時發聲的容許誤差（毫秒）',
    'edge_margin': '左右各留幾格不用',
    'normal_width': '一般音符寬度',
    'dense_width': '密集時的音符寬度',
    'min_note_width': '最小音符寬度',
    'width_anchor': '收窄時哪一邊不動（right＝右緣不動）',
    'dense_hand_threshold': '單手同時幾音就全部收窄',
    'hand_span_lanes': '單手可用的鍵道寬度',
    'max_hand_chord_notes': '單手和絃最多幾音',
    'minimum_pitch_span': '最小音高跨度（半音）',
    # 表情記號
    'classify_articulations': '自動判定表情記號',
    'classify_staccato': '自動標斷奏',
    'classify_soft': '自動標 soft',
    'classify_slide': '自動標滑奏',
    'staccato_gap_ratio': '斷奏：間隔要是常態的幾倍',
    'slide_max_gap_ms': '滑奏：相鄰兩顆的間隔上限（毫秒）',
    # 左右手
    'trust_two_track_hands': '兩軌以上就相信音軌的分手',
    'hand_track_bias': '偏向音軌原本那隻手的權重',
    'hand_leap_window_ms': '換手判斷：時間窗（毫秒）',
    'hand_leap_semitones': '換手判斷：大跳門檻（半音）',
    'hand_leap_weight': '換手判斷：罰則權重',
    'hand_memory_ms': '手的位置記憶（毫秒）',
    'hand_boundary_margin': '兩手交界額外加寬幾格',
    'hand_boundary_min_distance': '兩手交界最小距離（格）',
    'cross_hand_slack_lanes': '兩手距離可以被擠掉幾格',
    # 和絃與音程
    'close_chord_interval_semitones': '幾半音以內改用窄寬度',
    'chord_pair_close_semitones': '同手兩音：近音程到幾半音',
    'chord_pair_close_lanes': '同手兩音：近音程的中心距（格）',
    'chord_pair_mid_semitones': '同手兩音：中音程到幾半音',
    'chord_pair_mid_lanes': '同手兩音：中音程的中心距（格）',
    'chord_pair_far_semitones': '同手兩音：遠音程到幾半音',
    'chord_pair_far_gap': '同手兩音：遠音程留幾格空',
    'chord_pair_wide_semitones': '同手兩音：超遠音程到幾半音',
    'chord_pair_wide_gap': '同手兩音：超遠音程留幾格空',
    'chord_flush_min_notes': '幾音以上的和絃才做齊邊',
    'chord_flush_weight': '和絃齊邊的權重',
    'chord_flush_reach': '和絃齊邊：可移幾格',
    'interval_snap_tolerance': '貼齊音程間距的容許（格）',
    'min_octave_center_distance': '八度的中心距下限（格）',
    'max_octave_center_distance': '八度的中心距上限（格）',
    'max_interval_center_distance': '任何音程的中心距上限（格）',
    'octave_lanes_center': '中央區的一個八度佔幾格',
    'octave_lanes_edge': '邊緣區的一個八度佔幾格',
    'narrow_span_min': '收窄的跨度下限（半音）',
    'narrow_span_max': '收窄的跨度上限（半音）',
    'narrow_whole_span_min': '整組收窄的跨度下限（半音）',
    'narrow_whole_span_max': '整組收窄的跨度上限（半音）',
    # 旋律線
    'top_edge_window_ms': '冠音單調約束的時間窗（毫秒）',
    'top_step_tolerance': '冠音步伐的容許誤差（格）',
    'top_step_lanes_per_semitone': '冠音：每半音幾格',
    'top_step_strict_reach': '冠音嚴格修：可移幾格',
    'top_step_strict_passes': '冠音嚴格修：遍數',
    'top_step_strict_solo': '冠音嚴格修：只移一顆',
    'melody_slope_passes': '旋律斜率：遍數',
    'melody_slope_window_ms': '旋律斜率：時間窗（毫秒）',
    'melody_slope_reach': '旋律斜率：可移幾格',
    'melody_slope_bottom_weight': '旋律斜率：低音線的權重',
    'melody_slope_repeat_weight': '旋律斜率：同音不同軌的成本',
    'melody_slope_min_gain': '旋律斜率：至少改善多少才搬',
    'melody_slope_far_repeat_weight': '旋律斜率：較遠同音的權重',
    'melody_slope_far_repeat_ms': '旋律斜率：較遠同音的時間（毫秒）',
    'melody_slope_after_snap': '斜率修補排在同音吸附之後',
    'melody_slope_whole_group': '移不動時整組（兩手）一起平移',
    'melody_step_top': '冠音步伐表（1～14 半音各幾格）',
    'melody_step_bottom': '低音步伐表（1～14 半音各幾格）',
    'small_top_step_fix': '冠音差 1～2 半音就走剛好 1 格',
    'small_top_step_side_slack': '小步伐修補：另一側可以差幾格',
    'small_top_step_repeat_slack': '小步伐修補：可以拆開幾組同音',
    # 同音同軌
    'pitch_consistency_window_ms': '同音同軌：時間窗（毫秒）',
    'pitch_consistency_reach': '同音同軌：一次可移幾格',
    'pitch_consistency_passes': '同音同軌：遍數',
    'snap_flat_min_semitones': '同音吸附：幾半音以內才准壓平',
    'snap_repeat_window_ms': '同音吸附：時間窗（毫秒）',
    'snap_repeat_reach': '同音吸附：可移幾格',
    'snap_repeat_passes': '同音吸附：遍數',
    'snap_repeat_final': '同音吸附排在所有通道之後再收一次',
    'snap_block_drift_slack': '同音吸附：整塊最多飄幾格（空白＝不限）',
    'pitch_anchor_weight': '擺放前的同音錨定權重',
    'pitch_anchor_window_ms': '同音錨定的時間窗（毫秒）',
    'dp_anchor_deviation_weight': '修復通道：偏離錨點的成本',
    'repair_anchor_weight': '後段修復：錨點偏離的權重',
    # 音高順序
    'pitch_trend_window_ms': '前後順序約束的時間窗（毫秒）',
    'pitch_order_lookahead': '順序檢查往後看幾顆',
    'pitch_order_projection_passes': '順序投影的遍數',
    'sequence_lanes_per_semitone': '前後音程：每半音幾格',
    'sequence_knee_semitones': '音程的轉折點（半音）',
    'sequence_wide_lanes_per_semitone': '轉折之後每半音幾格',
    'sequence_interval_weight': '前後音程的權重',
    'backtrack_lookback': '回溯：往前找幾個事件',
    'backtrack_lane_reach': '回溯：最多讓幾格',
    'backtrack_chain_span': '回溯：連帶移動幾個事件',
    'backtrack_passes': '回溯的遍數',
    'backtrack_anchor_slack': '回溯：可以把同音帶偏幾格',
    'tie_break_passes': '平手處理的遍數',
    'repair_time_budget_sec': '單一修復通道的時間上限（秒）',
    # 樂句與版面
    'phrase_window_ms': '樂句視窗（毫秒）',
    'phrase_gap_ms': '樂句分界的空白（毫秒）',
    'phrase_shift_reach': '樂句對齊：可移幾格',
    'close_time_ms': '算「前後相接」的時間（毫秒）',
    'macro_trend_window_ms': '大方向趨勢的視窗（毫秒）',
    'macro_pitch_threshold': '大方向趨勢的音高門檻（半音）',
    'macro_anchor_weight': '大方向：錨點權重',
    'macro_transition_weight': '大方向：轉折權重',
    'use_trend_blocks': '用趨勢分段',
    'trend_jump_semitones': '趨勢切段的跳躍（半音）',
    'trend_min_notes': '趨勢段至少幾顆',
    'block_layout': '用區塊版面',
    'block_max_shift': '區塊的最大位移（格）',
    'global_position_weight': '全域位置的權重',
    'global_edge_band_fraction': '邊緣帶佔全曲音域的比例',
    'global_edge_band_min_semitones': '邊緣帶至少幾半音',
    'local_peak_prominence_semitones': '局部高低點的突出度（半音）',
    'dp_shift_limit': 'DP：位移上限（格）',
    'dp_anchor_weight': 'DP：錨點權重',
    'dp_transition_weight': 'DP：轉移權重',
    'dp_reserve_weight': 'DP：保留空間的權重',
    'dp_extreme_edge_weight': 'DP：極端音靠邊的權重',
    'dp_drift_weight': 'DP：整體飄移的罰則',
    'map_drift_lanes_per_sec': '對應位置每秒可飄幾格',
    'map_slope_rate_per_sec': '對應斜率每秒的變化上限',
    # 長條
    'hold_corridor_min_beats': '長條走廊的最短長度（拍）',
    'long_note_order_min_beats': '長音不受順序約束的長度（拍）',
}

_FIELD_RE = re.compile(r'^    (\w+)\s*:')
_docs_cache: Dict[str, str] = {}


def _settings_source() -> str:
    """SmartChartSettings 的原始碼。

    打包成 exe 之後 `inspect.getsource` 讀不到（模組是從 PYZ 來的），所以
    spec 另外把 `smart_chart.py` 當資料檔帶著，這裡退回去直接讀那個檔。
    """
    try:
        return inspect.getsource(SmartChartSettings)
    except (OSError, TypeError):
        pass
    import os
    import sys
    roots = [getattr(sys, '_MEIPASS', ''), os.path.dirname(os.path.abspath(__file__))]
    for root in roots:
        if not root:
            continue
        for path in (os.path.join(root, 'qt_editor', 'smart_chart.py'),
                     os.path.join(root, 'smart_chart.py')):
            try:
                text = open(path, encoding='utf-8').read()
            except OSError:
                continue
            head = text.find('class SmartChartSettings')
            if head < 0:
                continue
            tail = text.find(chr(10) + 'STYLE_OFFICIAL', head)
            return text[head:tail if tail > 0 else len(text)]
    return ''


def _load_docs() -> Dict[str, str]:
    """把每個欄位上面那幾行中文註解抓出來當說明。"""
    if _docs_cache:
        return _docs_cache
    source = _settings_source()
    if not source:
        _docs_cache['__empty__'] = ''
        return _docs_cache
    comment: List[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            comment.append(stripped.lstrip('#:').lstrip('#').strip())
            continue
        match = _FIELD_RE.match(line)
        if match and comment:
            _docs_cache[match.group(1)] = '\n'.join(comment)
        comment = []
    _docs_cache.setdefault('__empty__', '')
    return _docs_cache


def label_for(name: str) -> str:
    """中文名稱。沒登記的欄位就用原本的欄位名（新參數不會整個消失）。"""
    return LABELS.get(name, name)


def doc_for(name: str) -> str:
    """欄位的說明（排譜器原始碼裡的中文註解），沒有就回空字串。"""
    return _load_docs().get(name, '')


def tooltip_for(name: str) -> str:
    """提示：欄位名 + 說明。調參數時要對得上程式碼，所以欄位名一定留著。"""
    doc = doc_for(name)
    return '%s\n%s' % (name, doc) if doc else name


def grouped_fields() -> List[Tuple[str, List[str]]]:
    """分組後的欄位順序；沒列進 GROUPS 的歸「其他」，一個都不會漏。"""
    known = {f.name for f in fields(SmartChartSettings)}
    used: set = set()
    out: List[Tuple[str, List[str]]] = []
    for title, names in GROUPS:
        picked = [n for n in names if n in known]
        used.update(picked)
        if picked:
            out.append((title, picked))
    rest = [f.name for f in fields(SmartChartSettings) if f.name not in used]
    if rest:
        out.append(('其他', rest))
    return out
