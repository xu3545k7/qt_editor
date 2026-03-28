"""
settings.py
===========
全局設定儲存模組。設定存放於 qt_editor/settings.json。

用法
----
from .settings import settings
settings.load()
lang = settings.get('language')      # 'zh_tw' / 'zh_cn' / 'en'
settings.set('language', 'en')       # 自動寫入磁碟
"""

from __future__ import annotations

import json
import os

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'settings.json')

_DEFAULTS: dict[str, object] = {
    'language':      'zh_tw',   # 'zh_tw' | 'zh_cn' | 'en'
    'scroll_invert': False,     # bool
}


class _Settings:
    def __init__(self) -> None:
        self._data: dict[str, object] = dict(_DEFAULTS)

    # ------------------------------------------------------------------
    def load(self) -> None:
        """從磁碟載入設定（啟動時呼叫一次）。"""
        try:
            with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in _DEFAULTS:
                    self._data[k] = v
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f'[settings] 無法載入設定：{exc}')

    def save(self) -> None:
        """將設定寫入磁碟。"""
        try:
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f'[settings] 無法儲存設定：{exc}')

    # ------------------------------------------------------------------
    def get(self, key: str, default=None):
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()


settings = _Settings()
