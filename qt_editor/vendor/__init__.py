# -*- coding: utf-8 -*-
"""外部專案的程式碼，原樣帶著走（各自附授權）。

`bs_roformer/`
    ZFTurbo/Music-Source-Separation-Training 的模型定義（MIT，見該目錄的
    LICENSE）。只取 `bs_roformer.py` 與 `attend.py` 兩個檔，唯一的改動是把
    `from models.bs_roformer.attend import Attend` 改成相對 import。

    為什麼不用 pip 的 `bs-roformer`：那是 lucidrains 的新版架構（1.2.4），和
    2024 年訓練出來的 BS-Rofo-SW 權重對不上，而且會一路拉進 PoPE_pytorch、
    torch_einops_utils 之類的新依賴。這兩個檔是實證跑得動那份權重的組合。
"""
