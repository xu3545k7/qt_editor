# -*- coding: utf-8 -*-
"""先把鋼琴從混音裡分出來，再餵給 AI 轉譜。

為什麼要這一步
--------------
ByteDance 的轉譜模型是對「只有鋼琴的錄音」訓練的。實測（曲庫 8 首，用既有的
`<曲>_piano.wav` + `<曲>_ele.wav` 相加當混音，對照 `raw/*.mid`、起音 ±50ms）：

| 輸入 | 轉譜 F1（有效 6 首平均）|
|---|---|
| 直接餵混音 | 29.3% |
| demucs htdemucs_6s 的 piano 軌 | 35.5% |
| **BS-Rofo-SW 的 Piano 軌** | **59.7%** |
| 原本就乾淨的鋼琴軌 | 93.3% |

所以分離這一步補回「混音 → 乾淨軌」差距的 **47%**，而 demucs 只補回 10%。
8 首裡 BS-Rofo 每一首都贏 demucs。（兩首被排除：Chronomia、Ocean's Wish 連乾淨
軌都只有 11.6% / 20%，那是參考 MIDI 和音檔根本對不上，不是分離器的問題。）

**只取 Piano 軌**。試過把其他軌加回來：Piano+Guitar 59.5%、Piano+Other 47.0%、
三軌全加 46.1%，都比單獨 Piano 的 60.4% 差——洩漏到別軌的不是鋼琴，是干擾。

模型
----
`BS-Rofo-SW`（6 軌 BS-RoFormer：Bass / Drums / Vocals / Guitar / Piano / Other），
權重 699MB 從 Hugging Face 抓，模型定義在 `vendor/bs_roformer`（MIT）。

MVSep 自己的 Piano 模型分數更高（SDR 6.19 vs htdemucs 6 軌的 2.23），但**沒有
公開權重**，只能用他們的網站，所以不是選項。
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional

#: 權重與設定檔（和轉譜模型放同一個環境目錄下的 models/）
MODEL_DIR = 'separator'
CKPT_NAME = 'BS-Rofo-SW-Fixed.ckpt'
CONFIG_NAME = 'BS-Rofo-SW-Fixed.yaml'
CKPT_SIZE = 699412152
#: 來源：https://huggingface.co/Blakus/bs_roformer_sw_6stem
BASE_URL = 'https://huggingface.co/Blakus/bs_roformer_sw_6stem/resolve/main/'

#: 分離模型要的額外套件。torch 已經在轉譜環境裡了，這些都很小。
#: 一律 `--no-deps` 裝：讓 pip 自己解依賴會把 torch 降版，而 RTX 50 系列需要
#: cu128（見 ai_transcribe 的說明）。
PACKAGES = [
    'ml_collections',
    'omegaconf',
    'antlr4-python3-runtime==4.9.3',   # omegaconf 要，但它沒宣告
    'absl-py',                         # ml_collections 要
    'beartype',
    'rotary_embedding_torch',
    'hyper_connections',
    'einops',
    'PyYAML',
    'soxr',                            # 重取樣；librosa 那條會拉進 numba
]

#: 我們只要這一軌
WANTED = 'Piano'

#: 可以選的分離器。`bsrofo` 是預設——實測它每一首都贏（見上面的表）。
#: demucs 留著當第二個選擇：它快一點（RTX 5080 上 8 秒 vs 12 秒），而且有些曲子
#: 的鋼琴和伴奏重疊方式不同，多一個可以比。
MODELS = {
    'bsrofo': {
        'label': 'BS-Rofo-SW',
        'note': '實測 8 首全勝，混音轉譜 F1 29% → 60%。要另外下載 667MB',
        'f1': 59.7,
    },
    'demucs': {
        'label': 'HTDemucs (htdemucs_6s)',
        'note': '快一點，但鋼琴那一軌弱很多：F1 只到 36%。模型第一次用時自動下載',
        'f1': 35.5,
    },
}
DEFAULT_MODEL = 'bsrofo'

#: demucs 的套件名（它自己會管模型下載）
DEMUCS_PACKAGE = 'demucs'
DEMUCS_MODEL = 'htdemucs_6s'


def model_dir(env_root: str) -> str:
    return os.path.join(env_root, 'models', MODEL_DIR)


def is_installed(env_root: str) -> bool:
    """權重在不在、大小對不對。"""
    folder = model_dir(env_root)
    ckpt = os.path.join(folder, CKPT_NAME)
    config = os.path.join(folder, CONFIG_NAME)
    if not (os.path.isfile(ckpt) and os.path.isfile(config)):
        return False
    return os.path.getsize(ckpt) == CKPT_SIZE


def download_size_mb() -> int:
    """給安裝前的確認對話框。"""
    return CKPT_SIZE // (1024 * 1024)


def stem_names(config: Dict) -> List[str]:
    """設定檔裡的軌名。少一軌是 `Other`——6 軌但只列了 5 個名字。"""
    names = list((config.get('training') or {}).get('instruments') or [])
    want = int((config.get('model') or {}).get('num_stems') or len(names))
    while len(names) < want:
        names.append('Other')
    return names


def demucs_available(env_root: str = '') -> bool:
    """demucs 裝了沒。它的模型是第一次用的時候自己下載的，所以只看套件。"""
    try:
        import demucs.api                                  # noqa: F401
        return True
    except ImportError:
        return False


def available_models(env_root: str) -> Dict[str, bool]:
    """每個分離器現在能不能用。"""
    return {'bsrofo': is_installed(env_root), 'demucs': demucs_available(env_root)}


def separate(audio_path: str, out_path: str, env_root: str,
             device: str = 'auto', model: str = DEFAULT_MODEL,
             progress: Optional[Callable[[float], None]] = None) -> str:
    """把 `audio_path` 的鋼琴分出來寫到 `out_path`，回傳 `out_path`。

    在轉譜環境裡跑（torch 在那裡），所以這個函式本身是給 worker 用的，不是給
    製譜器主程序。
    """
    if model == 'demucs':
        return _separate_demucs(audio_path, out_path, device, progress)
    return _separate_bsrofo(audio_path, out_path, env_root, device, progress)


def _separate_demucs(audio_path: str, out_path: str, device: str,
                     progress: Optional[Callable[[float], None]]) -> str:
    """htdemucs_6s 的 piano 軌。模型由 demucs 自己下載與快取。"""
    import soundfile as sf
    import torch
    from demucs.api import Separator

    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    separator = Separator(model=DEMUCS_MODEL, device=device,
                          progress=False, callback=None)
    _origin, stems = separator.separate_audio_file(audio_path)
    key = next((k for k in stems if k.lower() == 'piano'), None)
    if key is None:
        raise RuntimeError('demucs 沒有輸出鋼琴軌：%s' % sorted(stems))
    if progress is not None:
        progress(1.0)
    sf.write(out_path, stems[key].cpu().numpy().T, separator.samplerate)
    return out_path


def _separate_bsrofo(audio_path: str, out_path: str, env_root: str,
                     device: str,
                     progress: Optional[Callable[[float], None]]) -> str:
    import numpy as np
    import soundfile as sf
    import torch
    import yaml

    folder = model_dir(env_root)
    with open(os.path.join(folder, CONFIG_NAME), encoding='utf-8') as fh:
        # 設定檔用了 `!!python/tuple`，safe_load 讀不了
        config = yaml.load(fh, Loader=yaml.UnsafeLoader)

    # 這個模組有兩種跑法：在製譜器裡是 `qt_editor.piano_separate`（套件內），
    # 在轉譜環境裡是 worker 旁邊的一個單檔（沒有套件）。兩邊都要能找到 vendor。
    try:
        from .vendor.bs_roformer import BSRoformer
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from vendor.bs_roformer import BSRoformer
    model = BSRoformer(**dict(config['model']))
    state = torch.load(os.path.join(folder, CKPT_NAME), map_location='cpu',
                       weights_only=False)
    model.load_state_dict(state.get('state_dict', state))
    model.eval()

    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(device)

    audio, sr = sf.read(audio_path, always_2d=True, dtype='float32')
    want_sr = int(config['audio']['sample_rate'])
    if sr != want_sr:
        import soxr
        audio = soxr.resample(audio, sr, want_sr)
        if audio.ndim == 1:
            audio = audio[:, None]
        sr = want_sr
    if audio.shape[1] == 1:                      # 模型吃立體聲
        audio = np.repeat(audio, 2, axis=1)
    elif audio.shape[1] > 2:
        audio = audio[:, :2]

    mix = torch.from_numpy(audio.T).to(device)   # (channel, time)
    names = stem_names(config)
    try:
        index = names.index(WANTED)
    except ValueError:
        raise RuntimeError('設定檔裡沒有 %s 軌：%s' % (WANTED, names))

    chunk = int(config['audio']['chunk_size'])
    overlap = int((config.get('inference') or {}).get('num_overlap') or 2)
    step = max(1, chunk // max(1, overlap))
    # 重疊相加：每一塊乘上一條兩端為 0 的窗，邊界才不會有接縫
    window = torch.hann_window(chunk, periodic=True, device=device)

    total = mix.shape[1]
    out = torch.zeros(2, total + chunk, device=device)
    weight = torch.zeros(total + chunk, device=device)
    padded = torch.nn.functional.pad(mix, (0, chunk))

    starts = list(range(0, total, step))
    with torch.no_grad():
        for done, start in enumerate(starts):
            piece = padded[:, start:start + chunk]
            if piece.shape[1] < chunk:
                piece = torch.nn.functional.pad(piece, (0, chunk - piece.shape[1]))
            estimate = model(piece.unsqueeze(0))          # (1, stems, ch, time)
            picked = estimate[0, index]
            out[:, start:start + chunk] += picked * window
            weight[start:start + chunk] += window
            if progress is not None:
                progress((done + 1) / len(starts))

    out = out[:, :total] / weight[:total].clamp(min=1e-8)
    sf.write(out_path, out.cpu().numpy().T, sr)
    return out_path


def install_record(env_root: str) -> Optional[Dict]:
    path = os.path.join(model_dir(env_root), 'installed.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:                                      # noqa: BLE001
        return None
