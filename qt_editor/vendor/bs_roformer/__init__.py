# -*- coding: utf-8 -*-
"""BS-RoFormer 模型定義（ZFTurbo/Music-Source-Separation-Training，MIT）。

**不要在這裡直接 import `bs_roformer`**：它需要 torch / einops / beartype，而那些
只裝在轉譜環境裡，製譜器主程序沒有。eager import 會讓 `unittest discover` 掃到這個
套件時整個炸掉。要用的時候自己 `from .bs_roformer import BSRoformer`。
"""

__all__ = ['BSRoformer']


def __getattr__(name):
    if name == 'BSRoformer':
        from .bs_roformer import BSRoformer
        return BSRoformer
    raise AttributeError(name)
