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
import sys

_SETTINGS_BASE_DIR = (
    os.getcwd()
    if getattr(sys, 'frozen', False)
    else os.path.dirname(__file__)
)
_SETTINGS_FILE = os.path.join(_SETTINGS_BASE_DIR, 'settings.json')

_DEFAULTS: dict[str, object] = {
    'language':      'zh_tw',   # 'zh_tw' | 'zh_cn' | 'en'
    # 轉譜風格：'eather'（Eather 的手寫譜風格）| 'official'（官方語料風格）
    'chart_style':   'eather',
    'scroll_invert': False,     # bool
    # When exporting songs, if True attempt to auto-process audio
    # (parse offset from filename like +1000ms / -300ms and apply padding/trim).
    'export_auto_process_audio': True,
    # Optional trim at end in ms when processing exports
    'export_trim_end_ms': 0,
    # Last-used export destination (songs root). Empty = use auto-detected SONGS_ROOT.
    'export_songs_root': '',
    # 音高數字顯示 MIDI 編號(21~108) 而不是遊戲的 scale_piano(1~88)
    'show_midi_pitch': False,
    # 音效裝置輸出延遲補償（ms）：判定線比聲音早到就調大。見 AudioPlayer.current_ms
    'audio_latency_ms': 0,
    # 音高模式：力度用音符亮度表示 / 在音符上顯示力度數字
    'pitch_velocity_shading': True,
    'pitch_velocity_numbers': True,
    # 音高模式：左右邊緣的強弱曲線欄（左手在左、右手在右）
    'pitch_dynamics_lane': True,
    # 音高模式左側的延音踏板欄
    'pitch_pedal_lane': True,
    # 音高模式：把調內的音格與琴鍵標亮（調性取自工具列的「調性」下拉）
    'pitch_scale_highlight': True,
    # 鎖調：放音符時只吸調內音。會改變輸入行為，預設關閉
    'pitch_scale_lock': False,
    # 只編一隻手時，另一手畫成半透明的幽靈音符當參考
    'ghost_other_hand': True,
    # 復原歷史的記憶體預算（MB）。大譜面會自動變淺，見 _undo_depth_budget
    'undo_memory_mb': 64,
    # 快捷鍵（QKeySequence 字串，空字串 = 不綁）
    'shortcut_cycle_view': '1',
    'shortcut_note_input': '2',
    'shortcut_play_pause': 'Space',
    'shortcut_play_full': '',
    'shortcut_play_window': '',
    'shortcut_stop': '',
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
