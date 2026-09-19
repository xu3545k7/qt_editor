"""
export_song_dialog.py
=====================
「儲存為完整曲目格式」對話框。
- 新曲目模式：使用者填入曲名、作者、難度名稱、定數、選曲繪，
  匯出到 songs/<曲名>/ 下。
- 追加難度模式：選擇資料夾（母資料夾會掃描子曲目讓使用者挑；
  若直接選到含 register.json 的子資料夾則直接帶入），自動讀取
  register.json 並預填曲名/作者/曲繪等，只需填新的難度名稱與定數。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QInputDialog,
)

from .i18n import t

_base_dir = None
if getattr(sys, 'frozen', False):
    # When packaged by PyInstaller, __file__ points into the temp extraction
    # directory. Prefer the current working directory (the folder the EXE
    # was started from) as the project root so exported files land in the
    # real workspace rather than the temp dir.
    _base_dir = os.getcwd()
else:
    _base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))

# Prefer the workspace's Nostalgia-clone Assets Resources songs folder when available.
# Fallback to the legacy UnityProject/nos_clone path for older setups.
_candidate_nostalgia = os.path.normpath(
    os.path.join(_base_dir, os.pardir, 'Nostalgia-clone', 'Assets', 'Resources', 'songs')
)
_candidate_legacy = os.path.normpath(
    os.path.join(_base_dir, 'UnityProject', 'nos_clone', 'Assets', 'Resources', 'songs')
)
# 匯入的曲目其實住在 UserSongs——index（library.json / songlist.json）也在那裡。
# 這個專案根本沒有 Assets/Resources/songs，所以只認前兩個候選的話，匯出根目錄
# 會指到一個不存在的路徑，分類下拉就永遠只剩預設值。
#
# 往上找而不是寫死一層：打包成 exe 之後 `_base_dir` 是**啟動時的工作目錄**
# （見上面），從 dist\ 按下去和從專案根目錄按下去差一層，寫死就會落空。
def _find_songs_root(start: str) -> str:
    """從 start 往上找 Nostalgia-clone 的曲目目錄，找不到回空字串。"""
    tails = (
        os.path.join('Nostalgia-clone', 'Assets', 'Resources', 'songs'),
        os.path.join('Nostalgia-clone', 'UserSongs'),
    )
    here = os.path.normpath(start)
    for _ in range(5):
        for tail in tails:
            candidate = os.path.join(here, tail)
            if os.path.isdir(candidate):
                return os.path.normpath(candidate)
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return ''


_candidate_walked = _find_songs_root(_base_dir)
if os.path.isdir(_candidate_nostalgia):
    SONGS_ROOT = _candidate_nostalgia
elif _candidate_walked:
    SONGS_ROOT = _candidate_walked
else:
    SONGS_ROOT = _candidate_legacy


def _scan_songs_in_folder(folder: str) -> list[tuple[str, str]]:
    """掃描 folder 底下所有含 register.json 的子資料夾。
    回傳 [(display_text, full_path), ...]。"""
    results: list[tuple[str, str]] = []
    if not os.path.isdir(folder):
        return results
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, 'register.json')):
            results.append((name, full))
    return results


def _is_song_folder(folder: str) -> bool:
    """判斷 folder 本身是否為有效曲目資料夾（含 register.json）。"""
    return os.path.isfile(os.path.join(folder, 'register.json'))


def _resolve_cover_from_register(data: dict, song_folder: str) -> str:
    """從 register.json 的 coverResourcePath 解析出實際曲繪檔案路徑。"""
    diffs = data.get('difficulties', [])
    if not diffs:
        return ''
    cover_res = diffs[0].get('coverResourcePath', '')
    if not cover_res:
        return ''
    # coverResourcePath 格式: "songs/<folder>/<name>"（無副檔名）
    # Resources 根目錄 = SONGS_ROOT/../..
    resources_root = os.path.normpath(os.path.join(SONGS_ROOT, os.pardir, os.pardir))
    for ext in ('.png', '.jpg', '.jpeg'):
        cand = os.path.normpath(os.path.join(resources_root, cover_res + ext))
        if os.path.isfile(cand):
            return cand
    # fallback: 直接在曲目資料夾裡找同名圖片
    display = data.get('displayName', '')
    if display:
        for ext in ('.png', '.jpg', '.jpeg'):
            cand = os.path.join(song_folder, display + ext)
            if os.path.isfile(cand):
                return cand
    return ''


#: 遊戲端 ExternalSongLibrary.DefaultCategory
DEFAULT_CATEGORY = 'Other'


class ExportSongDialog(QDialog):
    """完整曲目匯出對話框。"""

    def __init__(self, parent=None, *,
                 offset_ms: int = 0,
                 wav_path: str = '',
                 chart_json_path: str = '',
                 default_root: str = '',
                 game_root: str = ''):
        super().__init__(parent)
        self.setWindowTitle(t('dlg_export_title'))
        self.setMinimumWidth(520)
        self._offset_ms = offset_ms
        self._wav_path = wav_path
        self._chart_json_path = chart_json_path

        self._existing_register: Optional[dict] = None
        self._selected_song_folder: str = ''   # 追加模式選中的曲目資料夾完整路徑
        # 匯出根目錄（songs 資料夾）；空 → 用自動偵測的 SONGS_ROOT
        self._export_root: str = default_root or (SONGS_ROOT if os.path.isdir(SONGS_ROOT) else '')
        # 找得到遊戲曲庫（製譜器放在遊戲資料夾裡）就直接放進去，不用選位置
        self._game_root: str = game_root if game_root and os.path.isdir(game_root) else ''
        if self._game_root:
            self._export_root = self._game_root

        main_layout = QVBoxLayout(self)

        # ── 匯出根目錄（songs 資料夾）─────────────────────────────
        root_row = QHBoxLayout()
        root_row.addWidget(QLabel(t('dlg_export_to_game') if self._game_root else t('dlg_export_root')))
        self._le_root = QLineEdit(self._export_root)
        self._le_root.setReadOnly(True)
        root_row.addWidget(self._le_root)
        self._btn_browse_root = QPushButton(t('dlg_export_browse_folder'))
        self._btn_browse_root.clicked.connect(self._browse_export_root)
        self._btn_browse_root.setVisible(not self._game_root)
        root_row.addWidget(self._btn_browse_root)
        main_layout.addLayout(root_row)

        # ── 追加模式選項 ──────────────────────────────────────────
        mode_row = QHBoxLayout()
        self._chk_append = QCheckBox(t('dlg_export_append'))
        self._chk_append.toggled.connect(self._on_append_toggled)
        mode_row.addWidget(self._chk_append)

        self._btn_browse_folder = QPushButton(t('dlg_export_browse_folder'))
        self._btn_browse_folder.setEnabled(False)
        self._btn_browse_folder.clicked.connect(self._browse_song_folder)
        mode_row.addWidget(self._btn_browse_folder)

        mode_row.addStretch()
        main_layout.addLayout(mode_row)

        # 顯示已選曲目
        self._lbl_selected_song = QLabel('')
        self._lbl_selected_song.setStyleSheet('color: #2266aa; font-weight: bold;')
        main_layout.addWidget(self._lbl_selected_song)

        # ── 表單欄位 ──────────────────────────────────────────────
        form = QFormLayout()

        self._le_display = QLineEdit()
        form.addRow(t('dlg_export_display_name'), self._le_display)

        self._le_author = QLineEdit()
        form.addRow(t('dlg_export_author'), self._le_author)

        self._le_diff_name = QLineEdit()
        form.addRow(t('dlg_export_diff_name'), self._le_diff_name)

        self._spin_level = QSpinBox()
        self._spin_level.setRange(1, 99)
        self._spin_level.setValue(10)
        form.addRow(t('dlg_export_diff_level'), self._spin_level)

        # 曲繪
        cover_row = QHBoxLayout()
        self._le_cover = QLineEdit()
        self._le_cover.setReadOnly(True)
        cover_row.addWidget(self._le_cover)
        self._btn_cover = QPushButton(t('dlg_export_browse'))
        self._btn_cover.clicked.connect(self._browse_cover)
        cover_row.addWidget(self._btn_cover)
        form.addRow(t('dlg_export_cover'), cover_row)

        # 背景影片（選填）。遊戲端讀 register.json 難度項的 videoPath 與
        # videostartTime，路徑格式和曲繪一樣是 songs/<資料夾>/<名稱>（不含副檔名）。
        video_row = QHBoxLayout()
        self._le_video = QLineEdit()
        self._le_video.setReadOnly(True)
        self._le_video.setPlaceholderText('選填')
        video_row.addWidget(self._le_video)
        self._btn_video = QPushButton(t('dlg_export_browse'))
        self._btn_video.clicked.connect(self._browse_video)
        video_row.addWidget(self._btn_video)
        self._btn_video_clear = QPushButton('清除')
        self._btn_video_clear.clicked.connect(lambda: self._le_video.setText(''))
        video_row.addWidget(self._btn_video_clear)
        form.addRow('背景影片', video_row)

        self._spin_video_start = QDoubleSpinBox()
        self._spin_video_start.setRange(-60.0, 60.0)
        self._spin_video_start.setDecimals(2)
        self._spin_video_start.setSingleStep(0.1)
        self._spin_video_start.setSuffix(' 秒')
        self._spin_video_start.setToolTip(
            '影片相對於音樂起點的偏移。正值＝影片晚一點開始。\n'
            '注意：填 0 不是「和音樂同時開始」——遊戲會改用 pre-roll，'
            '在音樂之前就先播，整首都會早一點。\n'
            '要讓影片鎖在音樂上請填正值；音訊前面補了幾秒靜音（例如整體位移），'
            '這裡就填幾秒。')
        form.addRow('影片起始偏移', self._spin_video_start)

        # 分類：存在匯出根目錄的 library.json，不是 register.json
        self._cb_category = QComboBox()
        self._cb_category.setEditable(True)
        self._cb_category.setToolTip(
            '選歌畫面的分類頁籤。可以直接打新的分類名稱。\n'
            '存在 <匯出根目錄>/library.json，不在 register.json 裡。')
        form.addRow('分類', self._cb_category)
        self._reload_categories()

        # 無背景音樂：純鋼琴曲不需要伴奏，遊戲裡只該響玩家打出來的音。
        # 遊戲端 ExternalSongLibrary 驗證時 audioResourcePath 是**必填**
        # （RequireAsset），所以不能留空——改成輸出一段等長的無聲音訊，
        # 再把鋼琴音軌欄位拿掉，聲音就只剩 keysound。
        self._chk_no_bgm = QCheckBox('無背景音樂（遊戲中只播玩家打出的鋼琴音）')
        self._chk_no_bgm.setToolTip(
            '純鋼琴曲用。輸出一段和譜面等長的無聲音訊當背景音樂，\n'
            '並且不輸出鋼琴音軌——遊戲裡就只會響 keysound。\n'
            '（遊戲的音訊欄位是必填的，所以是放無聲而不是留空。）')
        main_layout.addWidget(self._chk_no_bgm)

        # 音源提示
        offset_text = ''
        if not wav_path:
            # 沒有音源也照樣匯出，只在這裡小字標註＋選要怎麼處理背景音樂
            offset_text = t('dlg_export_audio_none')
            self._chk_no_bgm.setVisible(False)
        elif offset_ms > 0:
            offset_text = t('dlg_export_audio_hint_adv', offset_ms)
        elif offset_ms < 0:
            offset_text = t('dlg_export_audio_hint_delay', abs(offset_ms))
        else:
            offset_text = t('dlg_export_audio_hint_none')
        self._lbl_audio = QLabel(offset_text)
        self._cb_no_audio = QComboBox()
        for key, value in (('dlg_export_no_audio_silent', 'silent'),
                           ('dlg_export_no_audio_midi', 'midi'),
                           ('dlg_export_no_audio_reuse', 'reuse')):
            self._cb_no_audio.addItem(t(key), value)
        if not wav_path:
            self._lbl_audio.setWordWrap(True)
            self._lbl_audio.setStyleSheet('color: gray; font-size: 11px;')
            audio_box = QVBoxLayout()
            audio_box.setContentsMargins(0, 0, 0, 0)
            audio_box.setSpacing(2)
            audio_box.addWidget(self._lbl_audio)
            audio_box.addWidget(self._cb_no_audio)
            form.addRow(t('dlg_export_audio'), audio_box)
            self._sync_no_audio_choices()
        else:
            self._cb_no_audio.setVisible(False)
            form.addRow(t('dlg_export_audio'), self._lbl_audio)

        main_layout.addLayout(form)

        # ── 按鈕 ──────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self._validate_and_accept)
        bbox.rejected.connect(self.reject)
        main_layout.addWidget(bbox)

    # ── 事件處理 ──────────────────────────────────────────────────
    def _on_append_toggled(self, checked: bool):
        self._btn_browse_folder.setEnabled(checked)
        if not checked:
            self._existing_register = None
            if not self._wav_path:
                self._sync_no_audio_choices()
            self._selected_song_folder = ''
            self._lbl_selected_song.setText('')
            self._le_display.setReadOnly(False)
            self._le_display.clear()
            self._le_author.setReadOnly(False)
            self._le_author.clear()
            self._le_cover.clear()
            self._btn_cover.setEnabled(True)

    def _browse_export_root(self):
        """選擇匯出根目錄（songs 資料夾）。"""
        start_dir = self._export_root if os.path.isdir(self._export_root) else ''
        folder = QFileDialog.getExistingDirectory(
            self, t('dlg_export_root_pick'), start_dir)
        if folder:
            self._export_root = os.path.normpath(folder)
            self._le_root.setText(self._export_root)
            # 分類是從這個目錄的索引檔讀出來的，換了目錄就要重讀一次，
            # 不然下拉會停在開視窗當下那份（常常是空的）。
            self._reload_categories()

    def _browse_song_folder(self):
        """讓使用者選擇資料夾，自動判斷是母資料夾或子資料夾。"""
        # 預設開在匯出根目錄（遊戲曲庫）；沒有才用 SONGS_ROOT
        start_dir = next((d for d in (self._export_root, SONGS_ROOT) if d and os.path.isdir(d)), '')
        folder = QFileDialog.getExistingDirectory(
            self, t('dlg_export_pick_folder'), start_dir)
        if not folder:
            return

        if _is_song_folder(folder):
            # 直接選到含 register.json 的子資料夾 → 直接帶入
            self._apply_song_folder(folder)
        else:
            # 當作母資料夾掃描
            songs = _scan_songs_in_folder(folder)
            if not songs:
                QMessageBox.information(
                    self, t('dlg_export_select_song'), t('dlg_export_no_songs'))
                return
            # 彈出選擇清單
            names = [s[0] for s in songs]
            chosen, ok = QInputDialog.getItem(
                self,
                t('dlg_export_select_song'),
                t('dlg_export_found_songs', len(songs)),
                names, 0, False,
            )
            if not ok or not chosen:
                return
            # 找到對應的完整路徑
            for name, full_path in songs:
                if name == chosen:
                    self._apply_song_folder(full_path)
                    break

    def _apply_song_folder(self, song_folder: str):
        """讀取 song_folder 內的 register.json 並帶入所有欄位。"""
        reg_path = os.path.join(song_folder, 'register.json')
        if not os.path.isfile(reg_path):
            return
        try:
            with open(reg_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        self._existing_register = data
        self._selected_song_folder = song_folder
        folder_name = os.path.basename(song_folder)
        self._lbl_selected_song.setText(f'📁 {folder_name}')

        # 帶入曲名、作者
        self._le_display.setText(data.get('displayName', folder_name))
        self._le_display.setReadOnly(True)
        self._le_author.setText(data.get('author', ''))
        self._le_author.setReadOnly(True)

        # 帶入曲繪（從 register 自動解析）
        if not self._wav_path:
            self._sync_no_audio_choices()

        cover = _resolve_cover_from_register(data, song_folder)
        if cover:
            self._le_cover.setText(cover + '  ' + t('dlg_export_cover_auto'))
            self._btn_cover.setEnabled(False)   # 自動帶入時不需要手動選
        else:
            self._le_cover.clear()
            self._btn_cover.setEnabled(True)    # 找不到才讓使用者自己選

    def _existing_has_music(self) -> bool:
        for diff in (self._existing_register or {}).get('difficulties', []) or []:
            if diff.get('audioResourcePath') and not diff.get('noBackgroundMusic'):
                return True
        return False

    def _sync_no_audio_choices(self) -> None:
        """「沿用這首原本的音樂」只有追加到有音樂的曲目時能選；選到那首就預設用它。"""
        from PyQt5.QtCore import Qt as _Qt
        reuse = self._cb_no_audio.findData('reuse')
        item = self._cb_no_audio.model().item(reuse)
        can_reuse = self._existing_has_music()
        item.setFlags(item.flags() | _Qt.ItemIsEnabled if can_reuse
                      else item.flags() & ~_Qt.ItemIsEnabled)
        if can_reuse:
            self._cb_no_audio.setCurrentIndex(reuse)
        elif self._cb_no_audio.currentData() == 'reuse':
            self._cb_no_audio.setCurrentIndex(self._cb_no_audio.findData('silent'))

    def no_audio_mode(self) -> str:
        """沒載入音源時怎麼處理背景音樂：'silent'／'midi'／'reuse'；有音源回傳空字串。"""
        if self._wav_path:
            return ''
        return self._cb_no_audio.currentData() or 'silent'

    def _browse_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '選擇背景影片', self._export_root or '',
            '影片 (*.mp4 *.webm *.mov);;所有檔案 (*.*)')
        if path:
            self._le_video.setText(path)

    def _library_path(self) -> str:
        return os.path.join(self._export_root or '', 'library.json')

    def _songlist_path(self) -> str:
        return os.path.join(self._export_root or '', 'songlist.json')

    def _reload_categories(self):
        """把匯出根目錄 library.json 裡已有的分類填進下拉。

        遊戲端 `ExternalSongLibrary` 的分類是存在 library.json（索引檔），
        每首歌一筆 `category` / `categories`，另外還有一份全域 `categories` 清單。
        """
        current = self._cb_category.currentText().strip()
        names = []

        def _add(value):
            text = str(value).strip() if value else ''
            if text and text not in names:
                names.append(text)

        try:
            import json
            with open(self._library_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            for name in (data.get('categories') or []):
                if name and name not in names:
                    names.append(str(name))
            for song in (data.get('songs') or []):
                for key in ('category',):
                    value = song.get(key)
                    if value and value not in names:
                        names.append(str(value))
                for value in (song.get('categories') or []):
                    if value and value not in names:
                        names.append(str(value))
        except Exception:                       # noqa: BLE001
            pass

        # songlist.json 是同一個目錄裡的另一份索引，分類放在 categories 這個
        # 「分類 -> 曲目資料夾」的字典。兩份不一定同步，兩邊都收。
        try:
            import json
            with open(self._songlist_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            groups = data.get('categories')
            if isinstance(groups, dict):
                for name in groups:
                    _add(name)
            elif isinstance(groups, list):
                for name in groups:
                    _add(name)
        except Exception:                       # noqa: BLE001
            pass

        if DEFAULT_CATEGORY not in names:
            names.append(DEFAULT_CATEGORY)
        self._cb_category.clear()
        self._cb_category.addItems(names)
        self._cb_category.setCurrentText(current or DEFAULT_CATEGORY)

    def _category_of_folder(self, folder_name: str) -> str:
        """library.json 裡這個曲目資料夾目前的分類；沒有就回預設值。"""
        try:
            import json
            with open(self._library_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            for song in (data.get('songs') or []):
                if str(song.get('folderName') or '') == folder_name:
                    return str(song.get('category') or DEFAULT_CATEGORY)
        except Exception:                       # noqa: BLE001
            pass
        return DEFAULT_CATEGORY

    def _browse_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t('dlg_export_cover_pick'), '',
            'Images (*.png *.jpg *.jpeg);;All Files (*)',
        )
        if path:
            self._le_cover.setText(path)

    def _validate_and_accept(self):
        # 追加模式若已用完整路徑選定曲目，匯出根目錄可忽略；否則必須有效
        if not (self._chk_append.isChecked() and self._selected_song_folder):
            if not self._export_root or not os.path.isdir(self._export_root):
                QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_err_no_root'))
                return
        if not self._le_display.text().strip():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_err_no_name'))
            return
        if not self._le_diff_name.text().strip():
            QMessageBox.warning(self, t('dlg_warn'), t('dlg_export_err_no_diff'))
            return
        self.accept()

    # ── 結果 getter ───────────────────────────────────────────────
    def display_name(self) -> str:
        return self._le_display.text().strip()

    def author(self) -> str:
        return self._le_author.text().strip()

    def diff_name(self) -> str:
        return self._le_diff_name.text().strip()

    def diff_level(self) -> int:
        return self._spin_level.value()

    def cover_path(self) -> str:
        """回傳曲繪路徑（去掉「自動帶入」提示文字）。"""
        raw = self._le_cover.text().strip()
        # 移除尾端的 i18n 提示（若有）
        auto_hint = t('dlg_export_cover_auto')
        if raw.endswith(auto_hint):
            raw = raw[:-len(auto_hint)].strip()
        return raw

    def exports_to_game(self) -> bool:
        return bool(self._game_root)

    def export_root(self) -> str:
        """回傳匯出根目錄（songs 資料夾）；空字串代表未設定。"""
        return self._export_root

    def is_append_mode(self) -> bool:
        return self._chk_append.isChecked()

    def append_folder(self) -> str:
        """回傳追加模式下選定的曲目資料夾名稱（僅 basename）。"""
        if self._selected_song_folder:
            return os.path.basename(self._selected_song_folder)
        return ''

    def append_folder_full(self) -> str:
        """回傳追加模式下選定的曲目資料夾完整路徑。"""
        return self._selected_song_folder

    def no_background_music(self) -> bool:
        """這首是不是「只播玩家打出來的聲音」。

        沒載入音源時勾選框是鎖住的，但追加難度要沿用原曲的音樂，所以這裡照使用者
        原本的意思回答：只看有音源時的勾選；沒音源交給匯出流程決定。"""
        return bool(self._chk_no_bgm.isChecked()) and bool(self._wav_path)

    def video_path(self) -> str:
        """使用者選的影片來源檔；沒選就是空字串。"""
        return self._le_video.text().strip()

    def video_start_sec(self) -> float:
        return float(self._spin_video_start.value())

    def category(self) -> str:
        name = self._cb_category.currentText().strip()
        return name or DEFAULT_CATEGORY

    def existing_register(self) -> Optional[dict]:
        return self._existing_register
