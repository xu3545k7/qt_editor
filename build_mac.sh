#!/bin/bash
# Build the Nostalgia chart editor as a macOS .app bundle.
#
#   cd qt_editor
#   bash build_mac.sh
#
# Output: dist_mac/Nos Chart Maker <version>.app
# The version comes from qt_editor/version.py -- bump that one line to cut a
# new build; nothing else needs editing.
# Notes in Chinese live in README-mac.md; this script stays ASCII-only on
# purpose, so that copying it between machines cannot corrupt it.
#
# Options:
#   --no-brew     skip Homebrew / FluidSynth (no soundfont preview)
#   --no-venv     use the current Python environment instead of a venv
#   --debug       print every command (same as bash -x)

# No `set -u`: an unset variable here is never worth aborting a build for,
# and the venv activate script trips it on some macOS bash versions.
set -eo pipefail

echo "=== build_mac.sh starting ==="

cd "$(dirname "$0")" || exit 1
echo "working directory: $(pwd)"

USE_BREW=1
USE_VENV=1
for arg in "$@"; do
  case "$arg" in
    --no-brew) USE_BREW=0 ;;
    --no-venv) USE_VENV=0 ;;
    --debug)   set -x ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

say()  { printf '\n== %s\n' "$1"; }
warn() { printf 'WARNING: %s\n' "$1" >&2; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

if [ "$(uname)" != "Darwin" ]; then
  die "This script only runs on macOS (uname says $(uname))."
fi
echo "machine: $(uname -m)"

# ---------------------------------------------------------------- Python
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || \
  die "python3 not found. Install Python 3.11 or 3.12: brew install python@3.12"
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "Python $PYVER at $(command -v "$PY")"
case "$PYVER" in
  3.10|3.11|3.12) ;;
  # The macOS built-in python3 is 3.9. The editor is developed on 3.10+, so a
  # 3.9 build silently misses things like bisect's key= argument -- and when
  # that blows up inside paintEvent, PyQt5 calls qFatal() and the app just
  # vanishes. Build with a newer Python: brew install python@3.12
  3.9) warn "Python 3.9 (probably the macOS built-in). The editor targets 3.10+; 3.9 builds can crash at runtime. Prefer: PYTHON=python3.12 bash build_mac.sh" ;;
  *) warn "Python $PYVER is untested. PyQt5 and audioop are a problem on 3.13+; 3.11 or 3.12 is safer." ;;
esac

# ------------------------------------------------------------------ venv
VENV="${VENV:-.venv-mac}"
if [ "$USE_VENV" = "1" ]; then
  if [ ! -d "$VENV" ]; then
    say "Creating virtual environment: $VENV"
    "$PY" -m venv "$VENV" || die "could not create the virtual environment"
  fi
  # shellcheck disable=SC1090
  . "$VENV/bin/activate" || die "could not activate $VENV"
  PY="python"
fi

say "Installing dependencies"
"$PY" -m pip install --upgrade pip wheel || die "pip could not update itself"
"$PY" -m pip install -r requirements-mac.txt || \
  die "dependency install failed. The output above says which package; PyQt5 needs Python 3.11 or 3.12 and pip >= 23."

# ------------------------------------------------------------ FluidSynth
# Needed for soundfont preview, MIDI audition and audio rendering on export.
say "FluidSynth"
if [ "$USE_BREW" = "0" ]; then
  echo "skipped (--no-brew)"
elif ! command -v brew >/dev/null 2>&1; then
  warn "Homebrew not found: soundfont preview will not work. See https://brew.sh then: brew install fluid-synth"
else
  brew list fluid-synth >/dev/null 2>&1 || brew install fluid-synth || \
    warn "brew install fluid-synth failed; continuing without it"
  BREW_LIB="$(brew --prefix)/lib"
  mkdir -p vendor/fluidsynth/mac
  # Copy the library plus the Homebrew dylibs it depends on, so the bundle
  # still works on a Mac without Homebrew.
  copy_deps() {
    lib="$1"
    base="$(basename "$lib")"
    if [ -f "vendor/fluidsynth/mac/$base" ]; then
      return 0
    fi
    cp -L "$lib" "vendor/fluidsynth/mac/$base" 2>/dev/null || return 0
    for dep in $(otool -L "$lib" | tail -n +2 | awk '{print $1}'); do
      case "$dep" in
        /opt/homebrew/*|/usr/local/*)
          if [ -f "$dep" ]; then copy_deps "$dep"; fi
          ;;
      esac
    done
  }
  FS_LIB="$(ls "$BREW_LIB"/libfluidsynth*.dylib 2>/dev/null | head -1)"
  if [ -n "$FS_LIB" ]; then
    copy_deps "$FS_LIB"
    echo "copied $(ls vendor/fluidsynth/mac | wc -l | tr -d ' ') dylibs into vendor/fluidsynth/mac"
  else
    warn "libfluidsynth not found under $BREW_LIB: soundfont preview will not work"
  fi
fi

# ------------------------------------------------------------- soundfont
say "SoundFont"
if [ -f soundfonts/Nice-Steinway-v3.8.sf2 ]; then
  echo "found the main soundfont; it will be bundled (about 187 MB)"
else
  warn "soundfonts/Nice-Steinway-v3.8.sf2 is missing: the app builds but has no piano sound. Copy the soundfonts/ folder from the Windows machine and run again."
fi

# ------------------------------------------------------------------ icon
say "Icon"
if [ -f qt_editor/icon.icns ]; then
  echo "qt_editor/icon.icns already exists"
elif [ -f qt_editor/icon.png ]; then
  ICONSET="$(mktemp -d)/icon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 64 128 256 512; do
    sips -z "$size" "$size" qt_editor/icon.png \
         --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1 || true
    sips -z "$((size*2))" "$((size*2))" qt_editor/icon.png \
         --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1 || true
  done
  iconutil -c icns "$ICONSET" -o qt_editor/icon.icns 2>/dev/null && \
    echo "generated qt_editor/icon.icns" || warn "could not generate the icon; the app will use a generic one"
else
  warn "qt_editor/icon.png missing; the app will use a generic icon"
fi

# --------------------------------------------------------------- version
# qt_editor/version.py is the single source of truth. The spec reads the same
# file to name the bundle and fill CFBundleShortVersionString, so this has to
# read it the same way -- keep the two in step or the check below fails.
say "Version"
VERSION="$("$PY" -c "
import ast
tree = ast.parse(open('qt_editor/version.py', encoding='utf-8').read())
print(next(ast.literal_eval(n.value) for n in tree.body
           if isinstance(n, ast.Assign)
           and any(getattr(t, 'id', '') == '__version__' for t in n.targets)))
")" || die "could not read __version__ from qt_editor/version.py"
echo "$VERSION"

# ----------------------------------------------------------- PyInstaller
say "PyInstaller"
"$PY" -m PyInstaller NostalgiaChartEditor-mac.spec --noconfirm \
  --distpath dist_mac --workpath build_tmp_mac || die "PyInstaller failed; the output above says why"

APP="dist_mac/Nos Chart Maker $VERSION.app"
[ -d "$APP" ] || die "no $APP was produced; see the PyInstaller output above"

# --------------------------------------------------------------- signing
# Ad-hoc signature: enough for this machine. Handing the app to someone else
# needs a Developer ID certificate and notarytool, or they get
# "cannot be opened because the developer cannot be verified".
say "Ad-hoc code signing"
codesign --force --deep --sign - "$APP" 2>/dev/null && echo "signed" || \
  warn "codesign failed; you may need: xattr -dr com.apple.quarantine \"$PWD/$APP\""

say "Done"
echo "$PWD/$APP"
echo
echo "If macOS refuses to open it: right-click the app in Finder and choose Open,"
echo "or run: xattr -dr com.apple.quarantine \"$PWD/$APP\""
echo
echo "To see errors from the app itself, run the binary directly:"
echo "  \"$PWD/$APP/Contents/MacOS/Nos Chart Maker\""
