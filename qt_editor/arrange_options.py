"""MIDI 轉譜的選項：模式、鍵道範圍、要不要允許和絃重疊、自訂參數。

這裡只放邏輯（不碰 Qt），對話框在 `arrange_dialog.py`、實際執行在
`models.NoteModel.arrange_with_options`。

四種模式：
  flat      直接平攤——音高線性對應鍵道，不跑任何排譜通道。
  eather    這個曲庫的風格（收窄擠空間、幾乎不重疊、表情記號自己標）。
  official  官方語料的風格（靠鍵道重疊擠空間、自動標滑音）。
  custom    以上面某一種為底，再套自己的參數表。

鍵道範圍是用「把 total_lanes 縮成範圍寬度，排完再整體右移」做的：排譜器裡
四十幾處邊界判斷都是拿 `settings.total_lanes` 當 0~N-1，平移一次就全部成立，
不必一處一處改。
"""

from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, Optional, Tuple

from .smart_chart import (
    STYLE_EATHER, STYLE_OFFICIAL, SmartChartSettings, arrange_midi_notes,
    normalise_style, settings_for_style,
)

TOTAL_GAME_KEYS = 28

MODE_FLAT = 'flat'
MODE_EATHER = 'eather'
MODE_OFFICIAL = 'official'
MODE_CUSTOM = 'custom'
MODES = (MODE_FLAT, MODE_EATHER, MODE_OFFICIAL, MODE_CUSTOM)

MODE_LABELS = {
    MODE_FLAT: '直接平攤',
    MODE_EATHER: 'Eather 智能轉譜',
    MODE_OFFICIAL: '官方風格智能轉譜',
    MODE_CUSTOM: '自訂',
}

#: 自訂模式裡不開放調整的欄位：由別的選項決定，放進參數表只會互相打架。
LOCKED_FIELDS = ('total_lanes', 'allow_chord_overlap', 'beat_ms')


def normalise_mode(mode: Any) -> str:
    name = str(mode or '').lower()
    if name in MODES:
        return name
    if name == 'user':
        return MODE_EATHER
    return MODE_EATHER


def _field_types() -> Dict[str, Any]:
    return {f.name: f.type for f in fields(SmartChartSettings)}


def coerce_value(name: str, value: Any) -> Any:
    """把對話框（或存起來的 JSON）給的值轉成該欄位應有的型別。

    存進 settings.json 再讀回來時 tuple 會變成 list，直接餵給排譜器有些地方
    會出錯，所以一律照現有預設值的型別轉回去。
    """
    current = getattr(SmartChartSettings(), name)
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on', '是')
        return bool(value)
    if isinstance(current, tuple):
        if isinstance(value, str):
            parts = [p for p in value.replace('，', ',').split(',') if p.strip()]
            return tuple(float(p) for p in parts)
        return tuple(float(v) for v in value)
    if current is None:
        # Optional[float]：空字串＝不限制
        if value in (None, '', '無'):
            return None
        return float(value)
    if isinstance(current, int):
        return int(round(float(value)))
    if isinstance(current, float):
        return float(value)
    return value


@dataclass
class ArrangeOptions:
    """一次轉譜要用的全部選項。"""

    mode: str = MODE_EATHER
    #: 允許用的鍵道範圍（含兩端，0 起算）
    lane_lo: int = 0
    lane_hi: int = TOTAL_GAME_KEYS - 1
    #: None ＝照模式決定（官方允許、其他不允許）
    allow_chord_overlap: Optional[bool] = None
    #: 自訂模式以哪一種風格為底
    base_style: str = STYLE_EATHER
    #: 自訂模式的參數覆寫
    overrides: Dict[str, Any] = field(default_factory=dict)

    # ── 基本查詢 ──────────────────────────────────────────────────────

    def normalised(self) -> 'ArrangeOptions':
        """夾好範圍、正規化名稱之後的一份副本。"""
        lo = max(0, min(TOTAL_GAME_KEYS - 1, int(self.lane_lo)))
        hi = max(0, min(TOTAL_GAME_KEYS - 1, int(self.lane_hi)))
        if hi < lo:
            lo, hi = hi, lo
        # 最窄也要放得下一顆寬度 3 的音符
        if hi - lo + 1 < 3:
            hi = min(TOTAL_GAME_KEYS - 1, lo + 2)
            lo = max(0, hi - 2)
        return ArrangeOptions(
            mode=normalise_mode(self.mode),
            lane_lo=lo,
            lane_hi=hi,
            allow_chord_overlap=self.allow_chord_overlap,
            base_style=normalise_style(self.base_style),
            overrides=dict(self.overrides or {}),
        )

    @property
    def lane_span(self) -> int:
        return int(self.lane_hi) - int(self.lane_lo) + 1

    @property
    def lanes_limited(self) -> bool:
        return int(self.lane_lo) > 0 or int(self.lane_hi) < TOTAL_GAME_KEYS - 1

    @property
    def is_smart(self) -> bool:
        return normalise_mode(self.mode) != MODE_FLAT

    def style(self) -> str:
        """這次轉譜算哪一種風格（存進 model.chart_style 的那個值）。"""
        mode = normalise_mode(self.mode)
        if mode == MODE_OFFICIAL:
            return STYLE_OFFICIAL
        if mode == MODE_CUSTOM:
            return normalise_style(self.base_style)
        return STYLE_EATHER

    def overlap_allowed(self) -> bool:
        if self.allow_chord_overlap is not None:
            return bool(self.allow_chord_overlap)
        return normalise_mode(self.mode) == MODE_OFFICIAL

    # ── 存／讀（偏好設定裡記住上次用的選項）──────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        opt = self.normalised()
        return {
            'mode': opt.mode,
            'lane_lo': opt.lane_lo,
            'lane_hi': opt.lane_hi,
            'allow_chord_overlap': opt.allow_chord_overlap,
            'base_style': opt.base_style,
            'overrides': {k: (list(v) if isinstance(v, tuple) else v)
                          for k, v in opt.overrides.items()},
        }

    @classmethod
    def from_dict(cls, data: Any) -> 'ArrangeOptions':
        data = data if isinstance(data, dict) else {}
        overlap = data.get('allow_chord_overlap', None)
        if overlap is not None:
            overlap = bool(overlap)
        known = _field_types()
        overrides = {}
        for key, value in (data.get('overrides') or {}).items():
            if key in known and key not in LOCKED_FIELDS:
                try:
                    overrides[key] = coerce_value(key, value)
                except (TypeError, ValueError):
                    continue
        return cls(
            mode=normalise_mode(data.get('mode')),
            lane_lo=int(data.get('lane_lo', 0) or 0),
            lane_hi=int(data.get('lane_hi', TOTAL_GAME_KEYS - 1)),
            allow_chord_overlap=overlap,
            base_style=normalise_style(data.get('base_style')),
            overrides=overrides,
        ).normalised()

    # ── 產生排譜器設定 ────────────────────────────────────────────────

    def build_settings(self, beat_ms: float = 500.0,
                       classify_articulations: bool = True) -> SmartChartSettings:
        opt = self.normalised()
        overrides = dict(opt.overrides) if opt.mode == MODE_CUSTOM else {}
        for name in LOCKED_FIELDS:
            overrides.pop(name, None)
        return settings_for_style(
            opt.style(),
            beat_ms=beat_ms,
            classify_articulations=classify_articulations,
            total_lanes=opt.lane_span,
            allow_chord_overlap=opt.overlap_allowed(),
            **overrides,
        )


def shift_notes_into_range(notes: Iterable[Any], offset: int) -> None:
    """排譜是在 0~span-1 上做的，整批右移到使用者指定的範圍。"""
    if not offset:
        return
    for note in notes:
        note.min_key = int(note.min_key) + offset
        note.max_key = int(note.max_key) + offset


def flat_layout(notes: Iterable[Any], lane_lo: int = 0,
                lane_hi: int = TOTAL_GAME_KEYS - 1, width: int = 3) -> int:
    """直接平攤：音高線性對應鍵道，不跑任何排譜通道。回傳排了幾顆。

    和 `_layout_midi_by_pitch` 的差別只在這裡吃得下鍵道範圍，而且是「轉譜」
    ——排完之後譜面就當成排過了。
    """
    note_list = [n for n in notes if getattr(n, 'pitch', None) is not None]
    if not note_list:
        return 0
    width = max(1, int(width))
    lo, hi = int(lane_lo), int(lane_hi)
    usable = max(0, hi - lo + 1 - width)
    pitches = [int(n.pitch) for n in note_list]
    low, high = min(pitches), max(pitches)
    span = max(1, high - low)
    for note in note_list:
        centre = int(round((int(note.pitch) - low) / span * usable))
        note.min_key = max(lo, min(hi - width + 1, lo + centre))
        note.max_key = note.min_key + width - 1
    return len(note_list)


def arrange_notes(notes: Iterable[Any], options: ArrangeOptions,
                  beat_ms: float = 500.0,
                  classify_articulations: bool = True) -> Tuple[Any, ArrangeOptions]:
    """照選項排一批音符（就地修改）。回傳 (統計, 正規化後的選項)。

    直接平攤沒有統計可言，回傳 None。
    """
    opt = options.normalised()
    note_list = list(notes)
    if not opt.is_smart:
        flat_layout(note_list, opt.lane_lo, opt.lane_hi)
        return None, opt
    stats = arrange_midi_notes(
        note_list,
        opt.build_settings(beat_ms=beat_ms,
                           classify_articulations=classify_articulations),
    )
    shift_notes_into_range(note_list, opt.lane_lo)
    return stats, opt
