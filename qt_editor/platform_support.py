"""平台差異集中在這裡。

這個專案本來是純 Windows 的：找遊戲看 `.exe`、連曲庫用 NTFS junction、
播音效用 winsound、Hiraeth 那套工具整個是 Windows 的。要在 macOS 上跑，
與其散在各處寫 `if sys.platform`，不如把「這台機器能不能做這件事」集中成
一份查詢表，UI 直接照它決定要不要顯示功能。

`IS_WINDOWS` / `IS_MAC` 是模組層的常數，測試要模擬別的平台時改
`platform_support.IS_MAC` 這類屬性即可（函式都是每次讀屬性，不是讀快照）。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

IS_WINDOWS = sys.platform.startswith('win')
IS_MAC = sys.platform == 'darwin'
IS_LINUX = not IS_WINDOWS and not IS_MAC

#: 只有 Windows 能用的功能 -> 在別的平台上要怎麼跟使用者說
WINDOWS_ONLY_FEATURES = {
    # Hiraeth 那套工具是 Windows 專用的第三方環境（SONG_MANAGER.bat、
    # 內嵌的 python.exe、spice64.exe），沒有 Mac 版可言。
    'hiraeth': 'Hiraeth 工具只能在 Windows 上用',
    # AI 轉譜下載的是 Windows 的嵌入式 Python，整個安裝流程要重做。
    'ai_transcribe': 'AI 轉譜目前只做了 Windows 的執行環境',
    # 遊戲本體是 Windows 的 Unity build；找遊戲、連曲庫都是 .exe / junction。
    'game_launch': '遊戲目前只有 Windows 版',
    'junction': '曲庫連結在這個平台上用符號連結（symlink）',
}


def feature_available(name: str) -> bool:
    """這個平台能不能用某個功能。未知的名稱一律當成可以。"""
    if name not in WINDOWS_ONLY_FEATURES:
        return True
    if name == 'junction':
        return True                     # 兩邊都做得到，只是做法不同
    return IS_WINDOWS


def feature_reason(name: str) -> str:
    """功能被關掉的理由（給 UI 顯示）。"""
    if feature_available(name):
        return ''
    return WINDOWS_ONLY_FEATURES.get(name, '')


def app_name_for_paths() -> str:
    return 'NostalgiaChartEditor'


def user_data_dir(app: Optional[str] = None) -> str:
    """每位使用者自己的資料夾（設定、自動備份、下載的執行環境放這裡）。

    macOS 的 `.app` 是唯讀（而且寫進去會破壞簽章），所以打包之後**不能**把
    設定寫在執行檔旁邊——那是 `Foo.app/Contents/MacOS/`。
    """
    app = app or app_name_for_paths()
    if IS_WINDOWS:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, app)
    if IS_MAC:
        return os.path.join(os.path.expanduser('~/Library/Application Support'), app)
    base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, app)


def executable_suffix() -> str:
    """遊戲／製譜器的執行檔長什麼樣：Windows 是 .exe，macOS 是 .app。"""
    if IS_WINDOWS:
        return '.exe'
    if IS_MAC:
        return '.app'
    return ''


def looks_like_app(path: str) -> bool:
    """這個路徑看起來像一個可執行的應用程式嗎（跨平台）。"""
    name = os.path.basename(str(path or ''))
    if IS_WINDOWS:
        return name.lower().endswith('.exe')
    if IS_MAC:
        return name.lower().endswith('.app') or (
            os.path.isfile(path) and os.access(path, os.X_OK))
    return os.path.isfile(path) and os.access(path, os.X_OK)


def launch_command(path: str) -> list:
    """啟動一個應用程式要下什麼指令。

    macOS 的 `.app` 是資料夾，不能直接 Popen，要嘛 `open -a`，要嘛跑
    `Contents/MacOS/<名字>`。這裡用 `open`，行為和使用者自己雙擊一樣。
    """
    path = str(path)
    if IS_MAC and path.lower().endswith('.app'):
        return ['open', '-a', path]
    return [path]


def make_dir_link(link: str, target: str) -> bool:
    """把 `link` 指到 `target`（資料夾）。成功回 True。

    Windows 用 junction（不需要管理員權限），其他平台用 symlink。
    """
    if IS_WINDOWS:
        import subprocess
        try:
            subprocess.run(
                ['cmd', '/c', 'mklink', '/J', link, target],
                check=True, capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            return True
        except Exception:                       # noqa: BLE001
            return False
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
