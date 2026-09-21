#!/bin/bash
# 在 macOS 上建置製譜器（.app）。在 qt_editor/ 底下執行：
#
#     chmod +x build_mac.sh && ./build_mac.sh
#
# 做的事：檢查相依、把 Homebrew 的 FluidSynth 複製進來、生 .icns、
# 跑 PyInstaller、做 ad-hoc 簽章。產物在 dist/Nos Chart Maker.app
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || die "這個腳本只能在 macOS 上跑。"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || die "找不到 python3。先裝 Python 3.11 或 3.12（brew install python@3.12）。"
PYVER="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "Python $PYVER（$PY）"
case "$PYVER" in
  3.9|3.10|3.11|3.12) ;;
  *) echo "警告：這個版本沒試過。PyQt5 與 audioop 在 3.13 之後會有問題，建議用 3.11 或 3.12。" ;;
esac

# ── 虛擬環境 ─────────────────────────────────────────────────────────
VENV="${VENV:-.venv-mac}"
if [ ! -d "$VENV" ]; then
  say "建立虛擬環境 $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1090
source "$VENV/bin/activate"
say "安裝相依套件"
python -m pip install --upgrade pip wheel >/dev/null
python -m pip install -r requirements-mac.txt

# ── FluidSynth ───────────────────────────────────────────────────────
# 音源預覽、MIDI 試聽、匯出時算音訊都要它。
say "FluidSynth"
if ! command -v brew >/dev/null; then
  echo "沒有 Homebrew：音源預覽會不能用。要的話先裝 https://brew.sh 再 brew install fluid-synth"
else
  brew list fluid-synth >/dev/null 2>&1 || brew install fluid-synth
  BREW_LIB="$(brew --prefix)/lib"
  mkdir -p vendor/fluidsynth/mac
  # 連同它自己相依的那些 dylib 一起複製進來，離開這台機器也能跑
  copy_deps() {
    local lib="$1"
    local base; base="$(basename "$lib")"
    [ -f "vendor/fluidsynth/mac/$base" ] && return 0
    cp -L "$lib" "vendor/fluidsynth/mac/$base" 2>/dev/null || return 0
    otool -L "$lib" | tail -n +2 | awk '{print $1}' | while read -r dep; do
      case "$dep" in
        /opt/homebrew/*|/usr/local/*) [ -f "$dep" ] && copy_deps "$dep" ;;
      esac
    done
  }
  FS_LIB="$(ls "$BREW_LIB"/libfluidsynth*.dylib 2>/dev/null | head -1 || true)"
  if [ -n "$FS_LIB" ]; then
    copy_deps "$FS_LIB"
    echo "已複製 $(ls vendor/fluidsynth/mac | wc -l | tr -d ' ') 個 dylib"
  else
    echo "找不到 libfluidsynth：音源預覽會不能用。"
  fi
fi

# ── 音源 ─────────────────────────────────────────────────────────────
say "音源"
if [ -f soundfonts/Nice-Steinway-v3.8.sf2 ]; then
  echo "找到主音源，會打包進去（約 187MB）。"
else
  echo "沒有 soundfonts/Nice-Steinway-v3.8.sf2——App 會蓋起來但沒有鋼琴音色。"
  echo "從 Windows 那台把整個 soundfonts/ 資料夾複製過來再跑一次就有了。"
fi

# ── 圖示 ─────────────────────────────────────────────────────────────
say "圖示"
if [ ! -f qt_editor/icon.icns ] && [ -f qt_editor/icon.png ]; then
  TMPSET="$(mktemp -d)/icon.iconset"
  mkdir -p "$TMPSET"
  for size in 16 32 64 128 256 512; do
    sips -z $size $size qt_editor/icon.png --out "$TMPSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) qt_editor/icon.png \
         --out "$TMPSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMPSET" -o qt_editor/icon.icns
  echo "已產生 qt_editor/icon.icns"
else
  echo "已有 icon.icns（或沒有 icon.png 可以轉）"
fi

# ── 建置 ─────────────────────────────────────────────────────────────
say "PyInstaller"
python -m PyInstaller NostalgiaChartEditor-mac.spec --noconfirm \
  --distpath dist_mac --workpath build_tmp_mac

APP="dist_mac/Nos Chart Maker.app"
[ -d "$APP" ] || die "沒有產出 $APP，看上面的錯誤訊息。"

# ── 簽章 ─────────────────────────────────────────────────────────────
# ad-hoc 簽章：這台機器自己跑夠用了。要給別人就得用 Developer ID + notarytool，
# 不然對方會看到「無法打開，因為無法驗證開發者」。
say "ad-hoc 簽章"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "簽章失敗（可以忽略，之後可能要 xattr -dr com.apple.quarantine）"

say "完成"
echo "$PWD/$APP"
echo
echo "第一次打開如果被擋：在 Finder 對 App 按右鍵 → 打開，或"
echo "  xattr -dr com.apple.quarantine \"$PWD/$APP\""
