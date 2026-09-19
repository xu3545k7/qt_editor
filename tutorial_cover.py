"""新手教學的曲繪：把譜面的重點段落照遊戲的樣子畫出來。

* 單課：挑第一個遊玩段裡音符最密的一小段，畫成往遠處延伸的軌道＋落下的玻璃音符
  （用遊戲自己的音符圖，紅＝右手、藍＝左手），一眼看出這課在練什麼形狀。
* 全課程：每一課的小圖拼成一張（使用者要求）。

只用 PIL：Qt 在 offscreen 模式下畫不出字，這裡也不需要字。
"""
from __future__ import annotations

import math
import os
from typing import List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ART = os.path.join(os.path.dirname(__file__), '..', 'Nostalgia-clone', 'Assets', 'Art', 'Classical', 'Notes')
GEM = os.path.join(os.path.dirname(__file__), '..', 'Nostalgia-clone', 'Assets', 'Resources', 'graphic')

COVER = 700
LANES = 28
#: 攤平的軌道（透視前）。高一點，遠處的音符壓扁後才不會糊成一團。
FLAT_W, FLAT_H = 840, 1500
JUDGE_Y = FLAT_H - 70
#: 透視後軌道的四個角：近端寬、遠端窄。
NEAR_L, NEAR_R, NEAR_Y = 20, COVER - 20, COVER - 10
FAR_L, FAR_R, FAR_Y = 245, COVER - 245, 70

GOLD = (214, 170, 92)
FLAG_SLIDE, FLAG_TRILL = 0x04, 0x40
KIND_HOLD, KIND_STACCATO = 2, 3

_sprites = {}


def _sprite(name: str, folder: str = ART) -> Image.Image:
    if name not in _sprites:
        _sprites[name] = Image.open(os.path.join(folder, name + '.png')).convert('RGBA')
    return _sprites[name]


def _perspective_coeffs(src: Sequence[Tuple[float, float]], dst: Sequence[Tuple[float, float]]):
    """PIL 的 PERSPECTIVE 要「輸出座標 → 輸入座標」的 8 個係數。"""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    return np.linalg.solve(np.array(rows, float), np.array(rhs, float)).tolist()


def _backdrop() -> Image.Image:
    """深色舞台：中間偏下一團暖光，四周壓暗。"""
    y, x = np.mgrid[0:COVER, 0:COVER].astype(float) / COVER
    glow = np.exp(-(((x - 0.5) / 0.55) ** 2 + ((y - 0.78) / 0.5) ** 2))
    base = np.array([14, 10, 12], float)
    warm = np.array([70, 44, 30], float)
    rgb = base + warm * glow[..., None]
    vignette = 1 - 0.55 * np.clip(np.hypot(x - 0.5, y - 0.55) * 1.4, 0, 1) ** 2
    rgb *= vignette[..., None]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')


#: 斷奏紋章（遊戲裡浮在音符上方、面向鏡頭的箭頭）：寬＝一個鍵道 × 0.9 × 1.5（Note.prefab 的
#: staccatoIndicatorScale 與 NoteController.StaccatoMarkWiden）。
MARK_WIDTH_LANES = 0.9 * 1.5


def _flat_track(notes, t0: float, t1: float, marks: list = None) -> Image.Image:
    """攤平的軌道：t0 在判定線、t1 在最上面。"""
    img = Image.new('RGBA', (FLAT_W, FLAT_H), (18, 16, 20, 255))
    draw = ImageDraw.Draw(img)
    lane_w = FLAT_W / float(LANES)
    # 黑大理石的淡紋路
    rng = np.random.default_rng(7)
    for _ in range(26):
        x0 = rng.uniform(0, FLAT_W); y0 = rng.uniform(0, FLAT_H)
        pts = [(x0 + rng.normal(0, 60) * k, y0 + 90 * k) for k in range(6)]
        draw.line(pts, fill=(44, 40, 46, 255), width=2)
    for lane in range(0, LANES + 1, 4):
        x = lane * lane_w
        draw.line([(x, 0), (x, FLAT_H)], fill=(70, 58, 44, 255), width=2)
    draw.rectangle([0, 0, 5, FLAT_H], fill=GOLD + (255,))
    draw.rectangle([FLAT_W - 6, 0, FLAT_W, FLAT_H], fill=GOLD + (255,))

    def y_of(ms: float) -> float:
        return JUDGE_Y - (ms - t0) / (t1 - t0) * JUDGE_Y

    head_h = int(lane_w * 1.25)
    ordered = sorted(notes, key=lambda n: -n.start)  # 遠的先畫，近的蓋上去
    tails = Image.new('RGBA', img.size, (0, 0, 0, 0))
    heads = Image.new('RGBA', img.size, (0, 0, 0, 0))
    for n in ordered:
        if n.end < t0 or n.start > t1:
            continue
        right = int(n.hand) == 0
        x0 = int(n.min_key * lane_w + 2)
        x1 = int((n.max_key + 1) * lane_w - 2)
        width = max(6, x1 - x0)
        kind = int(n.note_type)
        is_long = kind == KIND_HOLD or bool(kind & (FLAG_SLIDE | FLAG_TRILL))
        if is_long and n.end - n.start > 60:
            top = max(-10, y_of(n.end))
            bottom = min(FLAT_H, y_of(n.start))
            if bottom - top > 4:
                tail = _sprite('GlassHoldTail_Red' if right else 'GlassHoldTail_Blue')
                tw = max(4, int(width * 0.72))
                piece = tail.resize((tw, int(bottom - top)), Image.BILINEAR)
                tails.alpha_composite(piece, (x0 + (width - tw) // 2, int(top)))
        y = y_of(n.start)
        if y < -head_h or y > FLAT_H:
            continue
        head = _sprite('GlassNote_Red' if right else 'GlassNote_Blue').resize((width, head_h), Image.LANCZOS)
        heads.alpha_composite(head, (x0, int(y - head_h)))
        if kind == KIND_STACCATO:
            # 寶石照遊戲的比例：音符寬的 0.3，至少一個遊戲音符高（這裡以 0.56 個鍵道算）。
            gem = _sprite('StaccatoGem_Red' if right else 'StaccatoGem_Blue', GEM)
            size = int(min(lane_w * 0.95, max(width * 0.3, lane_w * 0.56)))
            gem = gem.resize((size, size), Image.LANCZOS)
            heads.alpha_composite(gem, (x0 + (width - size) // 2, int(y - head_h / 2 - size / 2)))
            if marks is not None:
                marks.append((x0 + width / 2.0, y - head_h, right))     # 音符遠端的邊
    img.alpha_composite(tails)
    # 音符帶一點光暈（遊戲裡音符是發光的）
    glow = heads.filter(ImageFilter.GaussianBlur(9))
    glow_arr = np.array(glow, float)
    glow_arr[..., 3] *= 0.8
    img.alpha_composite(Image.fromarray(glow_arr.astype(np.uint8), 'RGBA'))
    img.alpha_composite(heads)
    # 判定線
    line = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ImageDraw.Draw(line).rectangle([0, JUDGE_Y - 4, FLAT_W, JUDGE_Y + 4], fill=GOLD + (255,))
    img.alpha_composite(line.filter(ImageFilter.GaussianBlur(10)))
    img.alpha_composite(line)
    return img


def render(notes, t0: float, t1: float) -> Image.Image:
    """一段 [t0, t1] 的譜面畫成 COVER×COVER 的曲繪。"""
    marks: list = []
    flat = _flat_track(notes, t0, t1, marks)
    flat_corners = [(0, 0), (FLAT_W, 0), (FLAT_W, FLAT_H), (0, FLAT_H)]
    cover_corners = [(FAR_L, FAR_Y), (FAR_R, FAR_Y), (NEAR_R, NEAR_Y), (NEAR_L, NEAR_Y)]
    coeffs = _perspective_coeffs(flat_corners, cover_corners)
    track = flat.transform((COVER, COVER), Image.PERSPECTIVE, coeffs, Image.BICUBIC,
                           fillcolor=(0, 0, 0, 0))
    # 遠端淡進黑暗
    y = np.linspace(0, 1, COVER)
    fade = np.clip((y - FAR_Y / COVER) / 0.28, 0, 1) ** 1.3
    arr = np.array(track, float)
    arr[..., 3] *= fade[:, None]
    track = Image.fromarray(arr.astype(np.uint8), 'RGBA')
    cover = _backdrop()
    cover.alpha_composite(track)
    _draw_marks(cover, marks, _perspective_coeffs(cover_corners, flat_corners), fade)
    return cover.convert('RGB')


def _project(c, x: float, y: float) -> Tuple[float, float]:
    w = c[6] * x + c[7] * y + 1.0
    return (c[0] * x + c[1] * y + c[2]) / w, (c[3] * x + c[4] * y + c[5]) / w


def _draw_marks(cover: Image.Image, marks, to_cover, fade) -> None:
    """斷奏紋章是立著的（面向鏡頭），所以在透視之後才畫：位置跟著音符投影，大小照那個深度縮放。"""
    lane_w = FLAT_W / float(LANES)
    for x, y, right in sorted(marks, key=lambda m: m[1]):     # 遠的先畫
        px, py = _project(to_cover, x, y)
        left_x, _ = _project(to_cover, x - lane_w / 2.0, y)
        right_x, _ = _project(to_cover, x + lane_w / 2.0, y)
        width = int((right_x - left_x) * MARK_WIDTH_LANES)
        if width < 4 or not (0 <= py < COVER):
            continue
        art = _sprite('StaccatoMark_Red' if right else 'StaccatoMark_Blue', GEM)
        height = max(2, int(width * art.height / float(art.width)))
        piece = np.array(art.resize((width, height), Image.LANCZOS), float)
        piece[..., 3] *= fade[int(py)]
        cover.alpha_composite(Image.fromarray(piece.astype(np.uint8), 'RGBA'),
                              (int(px - width / 2.0), int(py - height - width * 0.3)))


def pick_window(visible, segments) -> Tuple[float, float]:
    """重點段落：第一個遊玩段（沒有就第一段）裡，音符最密的那一小段。"""
    plays = [s for s in segments if s['mode'] == 'play'] or list(segments)
    seg = plays[0]
    beat = float(seg.get('beatMs') or 500)
    length = max(2500.0, min(6000.0, beat * 8))
    starts = sorted(n.start for n in visible if seg['startMs'] <= n.start < seg['endMs'])
    if not starts:
        return seg['startMs'], seg['startMs'] + length
    best, best_count = starts[0], -1
    j = 0
    for i, s in enumerate(starts):
        if s + length > seg['endMs'] + 1:
            break
        while j < len(starts) and starts[j] < s + length:
            j += 1
        if j - i > best_count:
            best, best_count = s, j - i
    t0 = best - length * 0.06
    return t0, t0 + length


def composite(covers: List[Image.Image]) -> Image.Image:
    """全課程：每一課的小圖排成方格，金色細框。"""
    n = len(covers)
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    gap = 6
    cell = (COVER - gap * (cols + 1)) // cols
    height = rows * cell + gap * (rows + 1)
    sheet = Image.new('RGB', (COVER, COVER), (16, 12, 14))
    top = (COVER - height) // 2
    draw = ImageDraw.Draw(sheet)
    for index in range(rows * cols):
        r, c = divmod(index, cols)
        x = gap + c * (cell + gap)
        y = top + gap + r * (cell + gap)
        if index < n:
            sheet.paste(covers[index].resize((cell, cell), Image.LANCZOS), (x, y))
        else:
            draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(28, 22, 22))
        draw.rectangle([x - 1, y - 1, x + cell, y + cell], outline=GOLD, width=2)
    return sheet
