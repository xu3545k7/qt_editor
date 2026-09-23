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
bash build_mac.sh
```

用 `bash build_mac.sh` 而不是 `./build_mac.sh`：不需要先 `chmod +x`，
也不會受檔案權限影響。看不到任何輸出就表示指令根本沒執行到
（腳本第一行就會印 `=== build_mac.sh starting ===`）。

出錯時加 `--debug` 會印出每一行指令：

```bash
bash build_mac.sh --debug 2>&1 | tee ~/build_mac.log
```

其他選項：`--no-brew`（跳過 FluidSynth，沒有音源預覽）、
`--no-venv`（用目前的 Python 環境，不另外建虛擬環境）。

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

## AI 轉譜（mp3／wav → MIDI → 譜面）

「檔案 → AI 轉譜（音檔 → 譜面）→ 從音檔轉譜…」吃 mp3、wav、flac、ogg。
第一次用會問要不要安裝轉譜環境（約 0.4 GB 下載、1.6 GB 空間），裝在
`~/Library/Application Support/NostalgiaChartEditor/ai_transcribe/`，
中途取消下次會接著裝。

Mac 版不像 Windows 版會自己下載嵌入式 Python，而是**拿系統上的 Python 建 venv**：

- 需要 Python 3.9 以上；建議 3.11 或 3.12（`brew install python@3.12`），
  torch 的輪子最齊。找不到可用的 Python 時會直接說要先裝哪一個。
- 挑直譯器時版本新的優先，並且避開 Xcode 內附的那份（venv 會連回建立它的
  直譯器，Xcode 一更新搬家環境就壞了）。實際挑到哪一個會寫在安裝確認的視窗上。
- Apple 晶片用 GPU（MPS）轉譜；Intel Mac 用 CPU。M5 Pro 實測 2.5 分鐘的曲子
  約 20 秒（同一台用 CPU 約 29 秒），兩者轉出來的音符完全相同。十秒左右的短
  片段反而是 CPU 快——MPS 要暖機。
- 用 MPS 之前會拿 CPU 當基準比對卷積與雙向 GRU 的結果，不一致就自動改用 CPU
  ——安靜算錯的譜比慢的譜麻煩得多。

模型（ByteDance 的高解析度鋼琴轉譜）只用**獨奏鋼琴**訓練過，整首混音會轉出
大量雜音，有鋼琴分軌（`*_piano.wav`）請用分軌。mp3 這類非 WAV 的輸入會順便
解碼一份同名 WAV 放旁邊，載入譜面後直接掛成背景音樂。

## Mac 版沒有的功能

| 功能 | 為什麼 |
|---|---|
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
