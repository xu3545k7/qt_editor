# 在 macOS 上建置製譜器

## 先決條件

- macOS 11 以上
- Python 3.11 或 3.12（`brew install python@3.12`）
  - 3.13 以上不建議：`audioop` 被移除了，音量與混音會自己關掉。
- Homebrew（音源預覽要用它裝 FluidSynth）

## 建置

把整個 `qt_editor` 資料夾複製到 Mac 上，然後：

```bash
cd qt_editor
chmod +x build_mac.sh
./build_mac.sh
```

腳本會自己做這些事：建虛擬環境、裝相依套件、`brew install fluid-synth`、
把 FluidSynth 與它相依的 dylib 複製進 `vendor/fluidsynth/mac/`、
從 `icon.png` 生 `icon.icns`、跑 PyInstaller、做 ad-hoc 簽章。

產物：`dist_mac/Nos Chart Maker.app`

## 鋼琴音色

音源檔 `soundfonts/Nice-Steinway-v3.8.sf2` 有 187MB，不在 git repo 裡。
要有鋼琴音色，就把 Windows 那台的整個 `soundfonts/` 資料夾一起複製過來，
再跑建置。沒有它 App 還是開得起來，只是音源預覽會說找不到音源。

## 第一次打開被擋

macOS 會擋沒有 Apple 開發者簽章的 App：

- 在 Finder 對 App 按右鍵 → 打開，或
- `xattr -dr com.apple.quarantine "dist_mac/Nos Chart Maker.app"`

要發給別人而不被擋，需要 Apple Developer ID 憑證與 `notarytool` 公證。

## Mac 版沒有的功能

| 功能 | 為什麼 |
|---|---|
| AI 轉譜（音檔 → 譜面） | 轉譜環境裝的是 Windows 的嵌入式 Python，要重做成 venv 版 |
| 啟動遊戲、曲庫連結、更新 | 遊戲是 Windows 的 Unity build，找遊戲靠 `.exe`、連曲庫靠 NTFS junction |
| Hiraeth 工具（管理器、啟動街機版） | 那套工具整個是 Windows 專用的第三方環境 |

選單上這些項目會變灰並寫出原因，不會按下去才壞掉。
**譜面編輯、MIDI 匯入、自動排譜、難度生成、JSON/XML 匯出、Hiraeth 歌曲包輸出
（ZIP）都可以正常使用。**

## 出問題時

- **沒有聲音**：`python -c "import sounddevice; print(sounddevice.query_devices())"`
  在虛擬環境裡跑一次，看有沒有輸出裝置。
- **音源預覽說找不到 FluidSynth**：`brew install fluid-synth`，
  或確認 `vendor/fluidsynth/mac/` 裡有 `libfluidsynth*.dylib`。
- **PyQt5 裝不起來**：確認 Python 是 3.11／3.12，而且 `pip` 是最新的。
  Apple Silicon 需要 PyQt5 5.15.11 以上。
- **App 打不開、也沒有錯誤訊息**：從終端機直接跑
  `"dist_mac/Nos Chart Maker.app/Contents/MacOS/Nos Chart Maker"`，
  錯誤會印在終端機上。

設定檔在 `~/Library/Application Support/NostalgiaChartEditor/settings.json`
（不是 App 裡面——寫進 `.app` 會破壞簽章）。
