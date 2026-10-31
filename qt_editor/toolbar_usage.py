# -*- coding: utf-8 -*-
"""工具列按鈕的使用次數——「常用」分頁靠它長出來。

使用者：「常用初始就是空白（沒這個選項），以玩家的操作紀錄（有在其他 tab 上
曾經切過去用的）放到常用」。

所以這一頁不是誰排的，是**用出來的**：你為了某個功能切到別的分頁按了它，
它就往上爬；從來沒按過的永遠不會出現。

## 為什麼不沿用 `oplog`

`oplog` 記的是**譜面變化**（哪幾顆音符的哪些欄位變了），而且只在有實際改動時
才寫。這裡要的是「你按了哪顆按鈕」——按了但沒造成改動（選取是空的、對話框按
取消）也算，因為那同樣代表「你為了它切過去」。兩者問的問題不一樣。

## 為什麼鍵是標籤文字

按鈕背後的 slot 大多是 lambda，`__name__` 全都是 `<lambda>`，當不了鍵。標籤
是穩定的，而且換介面語言時重新計數也不算壞事——那時候整個介面都變了。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

#: 設定裡放計數的鍵
SETTINGS_KEY = 'toolbar_usage'

#: 「常用」最多放幾顆。再多就不是「常用」了，而且會擠爆分頁。
MAX_COMMON = 8

#: 按過幾次才夠格上去。1 次就上去的話，隨手點一下試試看的東西會一直卡在那裡。
MIN_COUNT = 2


class ToolbarUsage:
    """計數器。讀寫都走 `settings`，所以跨工作階段留得住。"""

    def __init__(self, settings) -> None:
        self._settings = settings

    def _load(self) -> Dict[str, int]:
        raw = self._settings.get(SETTINGS_KEY, None) or {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, int] = {}
        for key, value in raw.items():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    def record(self, label: str) -> int:
        """按了一次。回傳累計次數。"""
        if not label:
            return 0
        counts = self._load()
        counts[label] = counts.get(label, 0) + 1
        self._settings.set(SETTINGS_KEY, counts)
        return counts[label]

    def count(self, label: str) -> int:
        return self._load().get(str(label), 0)

    def top(self, limit: int = MAX_COMMON,
            exclude: Optional[set] = None) -> List[Tuple[str, int]]:
        """最常按的幾顆：[(標籤, 次數)]，多的在前。

        `exclude` 是「已經不用切分頁就按得到」的那些——常用分頁自己的按鈕不該
        把自己的排名愈推愈高（按常用頁上的鏡像也會計數，那是對的：它代表這個
        功能仍然常用；但排序時要拿原本那顆的數字）。
        """
        counts = self._load()
        rows = [(label, n) for label, n in counts.items()
                if n >= MIN_COUNT and label not in (exclude or set())]
        rows.sort(key=lambda item: (-item[1], item[0]))
        return rows[:max(0, int(limit))]

    def forget(self, label: str) -> None:
        counts = self._load()
        if counts.pop(str(label), None) is not None:
            self._settings.set(SETTINGS_KEY, counts)

    def clear(self) -> None:
        self._settings.set(SETTINGS_KEY, {})
