"""調性偵測與音階／琶音生成。

刻意不依賴 Qt 或 GNote，只吃 MIDI 音高數字、吐音高數字，方便單獨測試。

用途：編輯器要「一次放下一組音階或琶音，而且聽起來要在這首的調上」。所以先
從現有音符推出調性，再照那個調的音階去生成音高。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PITCH_CLASS_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)      # 自然小音階


@dataclass(frozen=True)
class Scale:
    """一種音階：主音之上的半音位移。

    `keyed=False` 表示這個音階沒有調性可言（半音階、全音階、減音階都是對稱的，
    移調之後還是自己），生成時就不該硬套譜面的主音——見 `build_pattern`。
    """

    key: str
    name: str
    steps: Tuple[int, ...]
    keyed: bool = True


#: 支援的音階。順序就是 UI 下拉的順序。
SCALES: Tuple[Scale, ...] = (
    Scale('major',            '大調',           MAJOR_STEPS),
    Scale('minor',            '小調',           MINOR_STEPS),   # 自然小音階
    Scale('harmonic_minor',   '和聲小音階',      (0, 2, 3, 5, 7, 8, 11)),
    Scale('melodic_minor',    '旋律小音階（上行）', (0, 2, 3, 5, 7, 9, 11)),
    Scale('dorian',           '多利安調式',      (0, 2, 3, 5, 7, 9, 10)),
    Scale('phrygian',         '弗里吉安調式',    (0, 1, 3, 5, 7, 8, 10)),
    Scale('lydian',           '利地安調式',      (0, 2, 4, 6, 7, 9, 11)),
    Scale('mixolydian',       '米索利地安調式',  (0, 2, 4, 5, 7, 9, 10)),
    Scale('locrian',          '洛克里安調式',    (0, 1, 3, 5, 6, 8, 10)),
    Scale('major_pentatonic', '大調五聲音階',    (0, 2, 4, 7, 9)),
    Scale('minor_pentatonic', '小調五聲音階',    (0, 3, 5, 7, 10)),
    Scale('blues',            '藍調音階',        (0, 3, 5, 6, 7, 10)),
    Scale('whole_tone',       '全音階',          (0, 2, 4, 6, 8, 10), keyed=False),
    Scale('chromatic',        '半音階',          tuple(range(12)), keyed=False),
    Scale('octatonic_hw',     '減音階（半全）',  (0, 1, 3, 4, 6, 7, 9, 10), keyed=False),
    Scale('octatonic_wh',     '減音階（全半）',  (0, 2, 3, 5, 6, 8, 9, 11), keyed=False),
)

SCALE_BY_KEY: Dict[str, Scale] = {s.key: s for s in SCALES}


def scale_of(mode: str) -> Scale:
    """音階 id → `Scale`。認不得的一律當大調，生成才不會整個炸掉。"""
    return SCALE_BY_KEY.get(str(mode), SCALE_BY_KEY['major'])


#: 「插入音階／琶音」的音型清單：(顯示名, kind)。工具列和對話框共用這一份。
#: 前四個吃譜面調性（`scale` 就是這個調自己的音階），後面是指定音階。
PATTERN_KINDS: Tuple[Tuple[str, str], ...] = (
    ('音階（依調性）', 'scale'),
    ('三度進行',       'thirds'),
    ('琶音（三和弦）', 'arpeggio'),
    ('琶音（七和弦）', 'arpeggio7'),
) + tuple(
    (s.name, s.key) for s in SCALES if s.key not in ('major', 'minor')
)

PATTERN_KIND_NAMES: Dict[str, str] = dict(
    (value, label) for label, value in PATTERN_KINDS)

# Krumhansl–Schmuckler 的調性輪廓。數字是「該音級在這個調裡有多重要」，
# 拿譜面的音級分布去和 24 個旋轉（12 音 × 大小調）比相關係數，最像的就是調性。
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                  2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                  2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


@dataclass(frozen=True)
class Key:
    """一個調：主音的音級（0=C）與大小調。"""

    tonic: int
    mode: str = 'major'          # `SCALES` 裡的 id；偵測只會給 'major'/'minor'
    confidence: float = 0.0      # 0~1，偵測有多確定

    @property
    def scale(self) -> Scale:
        return scale_of(self.mode)

    @property
    def steps(self) -> Tuple[int, ...]:
        return self.scale.steps

    @property
    def pitch_classes(self) -> Tuple[int, ...]:
        return tuple(sorted((self.tonic + s) % 12 for s in self.steps))

    def name(self) -> str:
        return '%s %s' % (PITCH_CLASS_NAMES[self.tonic % 12], self.scale.name)

    def contains(self, pitch: int) -> bool:
        return int(pitch) % 12 in self.pitch_classes


def _correlate(hist: Sequence[float], profile: Sequence[float], rotation: int) -> float:
    """把 profile 轉 `rotation` 個半音之後，和 hist 的皮爾森相關係數。"""
    rotated = [profile[(i - rotation) % 12] for i in range(12)]
    n = 12
    mean_h = sum(hist) / n
    mean_p = sum(rotated) / n
    num = sum((hist[i] - mean_h) * (rotated[i] - mean_p) for i in range(n))
    den_h = sum((hist[i] - mean_h) ** 2 for i in range(n)) ** 0.5
    den_p = sum((rotated[i] - mean_p) ** 2 for i in range(n)) ** 0.5
    if den_h <= 0 or den_p <= 0:
        return 0.0
    return num / (den_h * den_p)


def pitch_class_histogram(pitches: Iterable[int],
                          weights: Optional[Iterable[float]] = None) -> List[float]:
    """音級分布。`weights` 給音長時，長音的權重比較大（比單純數顆數準）。"""
    hist = [0.0] * 12
    ws = list(weights) if weights is not None else None
    for i, pitch in enumerate(pitches):
        if pitch is None:
            continue
        w = 1.0 if ws is None else float(ws[i] if i < len(ws) else 1.0)
        hist[int(pitch) % 12] += max(0.0, w)
    return hist


def detect_key(pitches: Iterable[int],
               weights: Optional[Iterable[float]] = None) -> Optional[Key]:
    """從一堆音高推出調性；資料太少或完全沒有就回 None。

    `confidence` 是最佳與次佳相關係數的差距（正規化到 0~1）——差距大表示這個
    調明顯勝出，差距小表示模稜兩可（例如關係大小調），UI 可以據此提醒使用者。
    """
    hist = pitch_class_histogram(pitches, weights)
    if sum(hist) <= 0:
        return None
    scored: List[Tuple[float, int, str]] = []
    for tonic in range(12):
        scored.append((_correlate(hist, _MAJOR_PROFILE, tonic), tonic, 'major'))
        scored.append((_correlate(hist, _MINOR_PROFILE, tonic), tonic, 'minor'))
    scored.sort(key=lambda item: item[0], reverse=True)
    best, second = scored[0], scored[1]
    spread = max(0.0, best[0] - second[0])
    confidence = max(0.0, min(1.0, spread * 4.0))
    return Key(tonic=best[1], mode=best[2], confidence=confidence)


def snap_to_key(pitch: int, key: Key, prefer_up: bool = True) -> int:
    """把音高移到調內最近的音；本來就在調內就不動。

    距離一樣近時看 `prefer_up`，這樣連續生成才不會忽上忽下。
    """
    pitch = int(pitch)
    if key.contains(pitch):
        return pitch
    # 找到 6 為止：五聲、藍調這種音階的空隙比七聲音階大，只找 3 個半音會漏掉
    for delta in (1, 2, 3, 4, 5, 6):
        ups, downs = pitch + delta, pitch - delta
        if prefer_up:
            candidates = (ups, downs)
        else:
            candidates = (downs, ups)
        for cand in candidates:
            if key.contains(cand):
                return cand
    return pitch


def scale_index_of(pitch: int, key: Key) -> int:
    """調內音高 → 以主音 C-1 為 0 的「音階級數」序號（可為負）。

    級數是連續的：同一個八度內 7 個音，跨八度就 +7，所以只要對序號加減就能
    在音階上走，不必自己處理半音/全音。
    """
    pitch = int(snap_to_key(pitch, key))
    octave, rel = divmod(pitch - key.tonic, 12)
    steps = key.steps
    nearest = min(range(len(steps)), key=lambda i: abs(steps[i] - rel))
    return octave * len(steps) + nearest


def pitch_at_scale_index(index: int, key: Key) -> int:
    """音階級數 → MIDI 音高（`scale_index_of` 的反向）。"""
    steps = key.steps
    octave, degree = divmod(int(index), len(steps))
    return key.tonic + octave * 12 + steps[degree]


def build_scale(key: Key, start_pitch: int, count: int,
                direction: int = 1, step: int = 1) -> List[int]:
    """從 `start_pitch` 沿著音階走 `count` 個音。

    `step=1` 是級進（Do Re Mi），`step=2` 就是三度跳進；`direction` 用 +1/-1
    決定上行下行。起音不在調內會先吸到調內。
    """
    if count <= 0:
        return []
    base = scale_index_of(start_pitch, key)
    sign = 1 if direction >= 0 else -1
    return [pitch_at_scale_index(base + sign * step * i, key) for i in range(count)]


def build_arpeggio(key: Key, start_pitch: int, count: int,
                   direction: int = 1, seventh: bool = False) -> List[int]:
    """從 `start_pitch` 這個級數當根音，照和弦音（1-3-5[-7]）往上/下堆。

    走的是**調內**的三度疊置，所以在大調上得到大三和弦、在小調上得到小三和弦，
    不用另外指定和弦品質——這正是「符合這首調性」的意思。
    """
    if count <= 0:
        return []
    tones = [0, 2, 4, 6] if seventh else [0, 2, 4]
    base = scale_index_of(start_pitch, key)
    sign = 1 if direction >= 0 else -1
    out: List[int] = []
    for i in range(count):
        octave, which = divmod(i, len(tones))
        offset = tones[which] + octave * len(key.steps)
        out.append(pitch_at_scale_index(base + sign * offset, key))
    return out


def pattern_key(kind: str, key: Key, start_pitch: int) -> Key:
    """某個音型實際要用的調。

    `scale`/`thirds`/`arpeggio*` 用譜面的調；其他 kind 是指定音階：
    有調性的（五聲、藍調、各調式）掛在譜面的主音上——C 大調就給 C 藍調；
    對稱音階（半音階、全音階、減音階）移調之後還是自己，主音只是「從哪個音
    開始數」，就直接掛在下筆的那個音上，這樣拖出來的第一顆就是點到的音。
    """
    if kind in ('scale', 'thirds', 'arpeggio', 'arpeggio7'):
        return key
    scale = SCALE_BY_KEY.get(str(kind))
    if scale is None:
        return key
    tonic = int(start_pitch) % 12 if not scale.keyed else key.tonic
    return Key(tonic=tonic, mode=scale.key, confidence=key.confidence)


def build_pattern(kind: str, key: Key, start_pitch: int, count: int,
                  direction: int = 1) -> List[int]:
    """UI 用的統一入口。

    `kind` 是 'scale' | 'thirds' | 'arpeggio' | 'arpeggio7'，或 `SCALES` 裡的
    任一個音階 id（'chromatic'、'blues'、'dorian' …），後者就是照那個音階級進。
    `direction=0` 表示上行之後再原路下行（去掉重複的頂點）。
    """
    use = pattern_key(kind, key, start_pitch)

    def once(dir_: int) -> List[int]:
        if kind == 'arpeggio':
            return build_arpeggio(use, start_pitch, count, dir_)
        if kind == 'arpeggio7':
            return build_arpeggio(use, start_pitch, count, dir_, seventh=True)
        if kind == 'thirds':
            return build_scale(use, start_pitch, count, dir_, step=2)
        return build_scale(use, start_pitch, count, dir_)

    if direction != 0:
        return once(direction)
    up = once(1)
    if len(up) < 2:
        return up
    # 折返：頂點只彈一次，所以下行從倒數第二個開始
    return up + list(reversed(up[:-1]))
