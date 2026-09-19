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

def _base_dir() -> str:
    """設定檔放哪裡。

    打包之後要放在 **exe 旁邊**，不是目前的工作目錄。工作目錄不是這個程式的
    性質，而是「它怎麼被啟動的」的性質：從檔案總管點兩下是 exe 的資料夾、
    從捷徑是捷徑的「開始位置」、用副檔名關聯打開一份譜面則是那份譜面的資料
    夾。同一個程式因此會讀到三個不同的 settings.json，使用者看到的是「設定
    每次都自己跑掉」。

    啟動器又刻意用 `cwd=editor_exe 的資料夾` 去開製譜器（launcher.py），所以
    舊的規則還會讓兩支 exe 各自存一份。
    """
    if not getattr(sys, 'frozen', False):
        return os.path.dirname(__file__)
    return os.path.dirname(os.path.abspath(sys.executable))


def _pick_settings_file() -> str:
    """exe 旁邊那一份。舊版寫在工作目錄，找得到就沿用那一份的內容（見 load）。"""
    return os.path.join(_base_dir(), 'settings.json')


_SETTINGS_BASE_DIR = _base_dir()
_SETTINGS_FILE = _pick_settings_file()

#: 舊版（寫在工作目錄）留下的設定檔。只讀不寫，讀完就寫回新的位置。
_LEGACY_SETTINGS_FILE = os.path.join(os.getcwd(), 'settings.json')

_DEFAULTS: dict[str, object] = {
    # 這三個是實際在用、卻一直漏了登記的。load() 只留下出現在這張表裡的鍵，
    # 所以它們每次重開都會被丟掉 —— 使用者看到的是「深色模式存不起來」。
    'dark_mode':          False,
    'keyboard_height_px': 0,      # 0 = 照版面自己算
    'show_statusbar':     True,
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
    # 改音高時鍵道跟著搬（＝重排譜面）。預設關閉：調音高只改音高，排好的
    # 譜面不會被動到。放置模式下鍵道一律跟著音高走，不受這個設定影響。
    'pitch_edit_moves_lanes': False,
    # 只編一隻手時，另一手畫成半透明的幽靈音符當參考
    'ghost_other_hand': True,
    # 音符要佔鍵道寬度的百分比（40~100）。100 = 貼滿整個鍵道；調小會在左右
    # 留出間隙，密集譜比較看得出一顆一顆。點選判定跟著縮，所見即所點。
    'note_width_pct': 100,
    # 放置模式放下音符時，用內建鋼琴音把那一顆彈出來。
    # 播放中本來就會響（主播放是整段預先 render 的，播到一半新增的音不在裡面），
    # 這個開關讓它在**沒有播放時**也響，也可以整個關掉。
    'place_note_sound': True,
    # 暫停後捲去看別的地方，按繼續時從畫面上判定線那一刻開始播；
    # 關掉就一律回到暫停點續播。
    'resume_from_view': True,
    # 自動儲存：每隔幾分鐘把還沒存的改動寫成備份（不覆蓋原檔），崩潰後重開
    # 可以救回來。見 autosave.py。
    'autosave_enabled': True,
    'autosave_interval_min': 3,
    # 放置格線（間隔＝放置模式選的時值）：
    #   'placement' 只有放置模式開著時畫（預設）
    #   'always'    一直畫
    #   'never'     不畫
    'place_grid_mode': 'placement',
    # 格線配置：放置時值那一層要不要畫，再加上自己勾的幾層（N 分音符，可以搭配，
    # 例如 4 分＋16 分）。越粗的畫得越明顯，重疊的地方只畫粗的那條。
    'grid_follow_placement': True,
    'grid_divisions': [],
    # 音高模式的欄位分色：'blackwhite'（黑白鍵，預設）或 'scale'（調性）
    'pitch_column_mode': 'blackwhite',
    # 長押主體要佔音符寬度的百分比（20~100）。100 = 和音符頭一樣寬；預設窄一點，
    # 讓壓在頭後面的長條不會把密集段落糊成一整片。
    'hold_width_pct': 55,
    # 復原歷史的記憶體預算（MB）。大譜面會自動變淺，見 _undo_depth_budget
    'undo_memory_mb': 64,
    # 快捷鍵（QKeySequence 字串，空字串 = 不綁）
    'shortcut_cycle_view': '1',
    'shortcut_note_input': '2',
    'shortcut_play_pause': 'Space',
    'shortcut_play_full': '',
    'shortcut_play_window': '',
    'shortcut_stop': '',
    # 往後／往前和方向鍵一致：畫面往上捲是往後（時間變晚）
    'shortcut_next_measure': 'PgUp',
    'shortcut_prev_measure': 'PgDown',
    'shortcut_song_start': 'Home',
    'shortcut_song_end': 'End',
    'shortcut_dur_shorter': '[',
    'shortcut_dur_longer': ']',
    'shortcut_toggle_hand': 'Q',
    'shortcut_toggle_tap_hold': 'W',
    'shortcut_toggle_width': 'E',
    'shortcut_measures_bpm': '',
    'shortcut_events': '',
    # 輸出 Hiraeth 歌曲包（ZIP）的資料夾
    'hiraeth_export_dir': '',
    'hiraeth_mix_piano': True,
    'hiraeth_hold_gap_ms': 80,
    # 輸出 ZIP 時要不要處理長條尾端（關掉就照原樣輸出，間距設定不用）
    'hiraeth_process_hold_tails': True,
    # 輸出 ZIP 時譜面開頭要空幾小節（官方譜中位數就是 1 小節）
    'hiraeth_lead_in_bars': 1,
    # 轉成官方格式（PAN XML、Hiraeth ZIP）時長押長度的比例（%）。JSON 不受影響。
    'official_hold_length_pct': 80,
    # 曲庫管理：上次管理的曲庫、打包好的遊戲 exe
    'song_library_root': '',
    'game_exe_path': '',
    # NosMania 啟動器：製譜器 exe（沒跟啟動器放在一起時才用得到）
    'editor_exe_path': '',
    # Hiraeth（PAN 歌曲管理版）套件解壓在哪（裡面有 SONG_MANAGER.bat）
    'hiraeth_root': '',
    # 放置模式自訂的「N 分音符」（一個全音符分成 N 份）
    'custom_note_divisions': [],
    # 操作紀錄：把每次編輯的前後差異寫進 qt_editor/logs/*.jsonl，
    # 給「使用者跑完工具又手動改了什麼」這種演算法調校用。純本機檔案。
    'oplog_enabled': True,
}


def _under_test() -> bool:
    """跑測試時不要把設定寫進磁碟。

    `settings.set()` 是立刻落盤的，而測試會大量呼叫它來擺弄各種開關——
    結果是跑完一次測試套件，**使用者的偏好設定就被改掉了**。實際發生過：
    `test_scale_tools` 把 `pitch_column_mode` 設成 'scale' 當作它的預設值，
    使用者下次開啟編輯器就變成調性分色，而空白譜面偵測不到調性 = 整片沒有
    分色，看起來像功能壞掉。

    在記憶體裡照常生效（測試要的是這個），只是不落盤。
    設 `NOS_SETTINGS_WRITE=1` 可以強制寫入。
    """
    if os.environ.get('NOS_SETTINGS_WRITE') == '1':
        return False
    if 'unittest' in sys.modules or 'pytest' in sys.modules:
        return True
    name = os.path.basename(sys.argv[0] or '').lower()
    return name.startswith('test') or name.startswith('pytest')


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
            self._load_legacy()
        except Exception as exc:
            print(f'[settings] 無法載入設定：{exc}')

    def _load_legacy(self) -> None:
        """新位置沒有檔案時，撿舊版寫在工作目錄的那一份。

        只在「新的還不存在」時才撿，所以不會把使用者剛改好的設定蓋回舊值。
        """
        legacy = os.path.abspath(_LEGACY_SETTINGS_FILE)
        if legacy == os.path.abspath(_SETTINGS_FILE):
            return
        try:
            with open(legacy, 'r', encoding='utf-8') as f:
                saved = json.load(f)
        except Exception:                               # noqa: BLE001
            return
        for k, v in saved.items():
            if k in _DEFAULTS:
                self._data[k] = v

    def save(self) -> None:
        """將設定寫入磁碟。測試環境不落盤，見 `_under_test`。"""
        if _under_test():
            return
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
