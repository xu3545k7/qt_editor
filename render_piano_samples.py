"""把 SoundFont 離線烤成遊戲用的鋼琴取樣庫。

目標是遊戲播出來的東西和製譜器（FluidSynth 直接合成）逐取樣一致，所以這裡
不自己發明取樣配置，而是**直接照抄音源自己的結構**：拆開 SF2 的 pdta，把它
的 key zone 與力度分層讀出來，一個 zone 烤一個檔。

目前用的是 **Nice-Steinway-v3.8.sf2 的「Bright Steinway」preset**：12 層力度
× 84 個 key zone = 1008 個檔、約 780 MB。力度層之間差的是低通截止頻率
（710 Hz 一路開到全開），所以是真的音色變化，不只是音量。

每一層用該層的最高力度渲染（SNR 最好），層內其他力度用一張實測的 128 段增益
表縮下去。**這一步是近似而不是精確還原**：SF2 規格的預設 modulator 本身就有
「力度→濾波截止」，所以層內的低力度其實也該更暗一點。分層越窄誤差越小——實測
Nice-Steinway 在力度 ≥60 的層內音色差都在 3% 以內，而舊音源 UprightPianoKW
只有 2 層（0~80、81~127），整個弱奏區間都被壓成同一種音色。

（舊註解說「層內只改音量不改音色」，那是比對振幅包絡得到的結論，而包絡看不到
頻譜，實測不成立。）

遊戲端的用法：
    音高 P、力度 V → 找 V 落在哪一層 → 找 P 在那一層的哪個 zone
    → clip = 該 zone 的檔案
    → AudioSource.pitch = 2^((P - root_key)/12)
    → volume = velocity_gain[V]

每個取樣都是 note-on 之後**不送 note-off**，讓琴音自然衰減到底——收音時機是
遊戲端包絡的事。syuten 的踏板覆蓋率 97.6%，尾巴是真的會被聽到的。

用法：
    python render_piano_samples.py --dry-run                     # 只看結構
    python render_piano_samples.py --soundfont <sf2> --preset "Bright Steinway"
    python render_piano_samples.py                               # 用製譜器的預設音源

換音源時**製譜器那邊也要一起換**（`midi_preview.DEFAULT_SOUNDFONT`），否則預覽
和遊戲會是兩種音色。
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from qt_editor.midi_preview import (
    MidiPreviewNote,
    MidiPreviewSynth,
    default_soundfont,
)

SAMPLE_RATE = 44_100
MIDI_LOWEST = 21          # A0
MIDI_HIGHEST = 108        # C8

# 低音的衰減比高音長得多。統一用最長的那個會浪費一堆尾巴都是靜音的空間，
# 所以依音高給不同的渲染長度，之後再修掉尾端的靜音。真實鋼琴低音要 20 秒
# 以上才衰減完，全部追到底太浪費，剩下的用長淡出接住。
TAIL_SECONDS_LOW = 16.0
TAIL_SECONDS_HIGH = 3.5

FADE_OUT_MS = 40.0            # 自然衰減完的，只要去掉修剪處的喀聲
TRUNCATED_FADE_OUT_MS = 450.0 # 撞到長度上限的，要長淡出才不會像被吞掉
SILENCE_FLOOR_DB = -72.0
TARGET_PEAK_DB = -1.0

# 這裡刻意**不做**任何壓縮。midi_preview 的 `OUTPUT_GAIN=2.5 + tanh 軟膝`是套在
# 整首混音上的，那份訊號只有 0.007% 的取樣超過 -6 dBFS，軟膝幾乎不作用；同一組
# 參數搬到「已經正規化到峰值 -1 dBFS 的單音」上，每個音的起音瞬態都遠在膝點之上，
# 等於整顆音被壓扁——鋼琴的音色幾乎全在起音，壓掉就是變鈍變髒。
#
# 響度要在混音端拿（SettingsManager.pianoVoiceVolume），那是純增益，不動波形。

# 量增益表用的音高。層內是純衰減、而且與音高無關，取幾個平均掉捨入誤差就好。
GAIN_PROBE_PITCHES = (48, 60, 72)
GAIN_PROBE_MS = 400

# SF2 generator 編號
GEN_KEY_RANGE = 43
GEN_VEL_RANGE = 44
GEN_INSTRUMENT = 41
GEN_SAMPLE_ID = 53


# ── SoundFont 結構 ────────────────────────────────────────────────────────

def _riff_chunks(data: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        chunk_id = data[i:i + 4].decode("ascii", "replace")
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        yield chunk_id, i + 8, size
        i += 8 + size + (size & 1)


def read_soundfont_layout(path: Path, preset_name: Optional[str] = None) -> Tuple[List[Tuple[int, int]], Dict[Tuple[int, int], List[Tuple[int, int]]]]:
    """讀出 SF2 的力度分層，以及每一層底下的 key zone。

    回傳 (力度層清單, {力度層: [key zone, ...]})，兩者都由低到高排序。
    """
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise ValueError(f"不是 SoundFont 檔案：{path}")

    pdta = None
    for chunk_id, off, size in _riff_chunks(data, 12, len(data)):
        if chunk_id == "LIST" and data[off:off + 4] == b"pdta":
            pdta = (off + 4, off + size)
    if pdta is None:
        raise ValueError("SF2 缺少 pdta 區塊")

    found = {cid: (off, size) for cid, off, size in _riff_chunks(data, *pdta)}
    for required in ("igen", "ibag"):
        if required not in found:
            raise ValueError(f"SF2 缺少 {required} 區塊")

    gen_off, gen_size = found["igen"]
    bag_off, bag_size = found["ibag"]
    gens = [struct.unpack("<HH", data[gen_off + i * 4: gen_off + i * 4 + 4])
            for i in range(gen_size // 4)]
    bags = [struct.unpack("<HH", data[bag_off + i * 4: bag_off + i * 4 + 4])
            for i in range(bag_size // 4)]

    # 力度分層可以掛在 **preset** 層也可以掛在 instrument 層，SF2 規格兩種都
    # 合法。舊版只掃 instrument（igen/ibag），碰到 Nice-Steinway 那種在 preset
    # 層分 12 層的音源就只看得到 1 層，整個力度維度被吃掉。所以照規格走完整條
    # preset zone → instrument → instrument zone 的路，兩邊的範圍取交集。
    for required in ("phdr", "pbag", "pgen", "inst"):
        if required not in found:
            raise ValueError(f"SF2 缺少 {required} 區塊")

    def records(name: str, size: int) -> List[bytes]:
        offset, length = found[name]
        return [data[offset + i * size: offset + (i + 1) * size]
                for i in range(length // size)]

    def pairs(name: str) -> List[Tuple[int, int]]:
        offset, length = found[name]
        return [struct.unpack("<HH", data[offset + i * 4: offset + i * 4 + 4])
                for i in range(length // 4)]

    preset_gens = pairs("pgen")
    preset_bags = pairs("pbag")
    inst_gens = gens
    inst_bags = bags
    presets = [
        (record[:20].split(b"\0")[0].decode("latin1", "replace"),
         *struct.unpack("<HHH", record[20:26]))
        for record in records("phdr", 38)
    ]
    instruments = [struct.unpack("<H", record[20:22])[0]
                   for record in records("inst", 22)]

    def zone_ranges(gen_list, lo: int, hi: int):
        """一個 zone 的 (key 範圍, 力度範圍, instrument 或 sample 編號)。"""
        key_range = None
        vel_range = None
        target = None
        for operator, amount in gen_list[lo:hi]:
            if operator == GEN_KEY_RANGE:
                key_range = (amount & 0xFF, amount >> 8)
            elif operator == GEN_VEL_RANGE:
                vel_range = (amount & 0xFF, amount >> 8)
            elif operator in (GEN_INSTRUMENT, GEN_SAMPLE_ID):
                target = amount
        return key_range, vel_range, target

    def overlap(first, second):
        if first is None:
            return second
        if second is None:
            return first
        low = max(first[0], second[0])
        high = min(first[1], second[1])
        return (low, high) if low <= high else None

    # 沒指定就用第一個 bank 0 的 preset（Nice-Steinway 的 preset 0 是
    # 「Steinway Grand」，1~3 是 Bright / Mellow / Dark 變體）。
    chosen = None
    for index in range(len(presets) - 1):          # 最後一筆是 EOP 終結記錄
        name, number, bank, bag_index = presets[index]
        if preset_name is not None:
            if name.strip().lower() == preset_name.strip().lower():
                chosen = index
                break
        elif bank == 0 and (chosen is None or number < presets[chosen][1]):
            chosen = index
    if chosen is None:
        raise ValueError("找不到 preset：%s" % (preset_name or "bank 0"))

    zones: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    start, stop = presets[chosen][3], presets[chosen + 1][3]
    for pbag_index in range(start, stop):
        preset_key, preset_vel, instrument = zone_ranges(
            preset_gens, preset_bags[pbag_index][0], preset_bags[pbag_index + 1][0])
        if instrument is None or instrument + 1 >= len(instruments):
            continue                                # 全域 zone，沒有指向樂器
        for ibag_index in range(instruments[instrument], instruments[instrument + 1]):
            inst_key, inst_vel, sample = zone_ranges(
                inst_gens, inst_bags[ibag_index][0], inst_bags[ibag_index + 1][0])
            if sample is None:
                continue                            # 樂器的全域 zone
            key_range = overlap(preset_key, inst_key)
            vel_range = overlap(preset_vel, inst_vel) or (0, 127)
            if key_range is None:
                continue                            # 兩層的範圍不相交
            zones.setdefault(vel_range, [])
            if key_range not in zones[vel_range]:
                zones[vel_range].append(key_range)

    bands = sorted(zones)
    for band in bands:
        zones[band].sort()
    return (bands, zones)


def list_presets(path: Path) -> List[Tuple[str, int, int]]:
    """音源裡的 (名稱, preset 編號, bank)，不含結尾的 EOP 終結記錄。"""
    data = path.read_bytes()
    pdta = None
    for chunk_id, off, size in _riff_chunks(data, 12, len(data)):
        if chunk_id == "LIST" and data[off:off + 4] == b"pdta":
            pdta = (off + 4, off + size)
    if pdta is None:
        raise ValueError("SF2 缺少 pdta 區塊")
    found = {cid: (off, size) for cid, off, size in _riff_chunks(data, *pdta)}
    offset, length = found["phdr"]
    out = []
    for i in range(length // 38 - 1):
        record = data[offset + i * 38: offset + (i + 1) * 38]
        name = record[:20].split(b"\0")[0].decode("latin1", "replace").strip()
        number, bank, _bag = struct.unpack("<HHH", record[20:26])
        out.append((name, number, bank))
    return out


def resolve_preset(path: Path, wanted: Optional[str]) -> Tuple[int, Optional[str]]:
    """把使用者給的名稱或編號解析成 (preset 編號, preset 名稱)。

    編號要傳給 FluidSynth 做 program select，名稱要給 `read_soundfont_layout`
    找 zone —— 兩邊必須指到同一個 preset，否則會拿 A 的結構去烤 B 的音色。
    """
    presets = list_presets(path)
    if not presets:
        raise ValueError("SF2 裡沒有 preset")
    if wanted is None:
        chosen = min((p for p in presets if p[2] == 0), key=lambda p: p[1],
                     default=presets[0])
    elif str(wanted).isdigit():
        number = int(wanted)
        chosen = next((p for p in presets if p[1] == number), None)
        if chosen is None:
            raise ValueError("找不到 preset 編號 %d，可選：%s"
                             % (number, [(p[0], p[1]) for p in presets]))
    else:
        chosen = next((p for p in presets
                       if p[0].lower() == str(wanted).strip().lower()), None)
        if chosen is None:
            raise ValueError("找不到 preset「%s」，可選：%s"
                             % (wanted, [p[0] for p in presets]))
    return (chosen[1], chosen[0])


# ── 渲染 ─────────────────────────────────────────────────────────────────

def tail_seconds(pitch: int) -> float:
    span = max(1, MIDI_HIGHEST - MIDI_LOWEST)
    ratio = min(1.0, max(0.0, (pitch - MIDI_LOWEST) / span))
    return TAIL_SECONDS_LOW + (TAIL_SECONDS_HIGH - TAIL_SECONDS_LOW) * ratio


def flush(synth: MidiPreviewSynth, ms: float = 250.0) -> None:
    """空跑一段把上一次的殘響放掉。

    `render()` 會 `fluid_synth_system_reset`，但那清的是發聲中的 voice，殘響單元
    的尾巴留在原地。實測烤出來的檔開頭會帶著上一顆音的尾巴（約 -60 dBFS），害
    每個檔的起音位置差 0~33 個取樣，而且結果跟渲染順序有關。空跑一段就乾淨了。
    """
    synth.render([], 0.0, ms)


def render_mono(synth: MidiPreviewSynth, pitch: int, velocity: int, ms: float) -> List[int]:
    """渲染單一個音，不送 note-off，回傳單聲道樣本。"""
    note = MidiPreviewNote(start_ms=0.0, end_ms=ms * 2.0, pitch=pitch, velocity=velocity)
    pcm = synth.render([note], 0.0, ms)
    frames = len(pcm) // 4
    samples = struct.unpack("<%dh" % (frames * 2), pcm[: frames * 4])
    return [(samples[i * 2] + samples[i * 2 + 1]) // 2 for i in range(frames)]


def measure_velocity_gains(
    synth: MidiPreviewSynth,
    bands: Sequence[Tuple[int, int]],
) -> List[float]:
    """量出每個 MIDI 力度相對於「所屬層的參考力度」的線性增益。

    層內是純衰減，所以這張表配上兩個取樣就能精確重現任何力度，不是近似。

    用 RMS 而不是峰值：峰值是單一取樣點，會跟著波形相位抖動——實測用峰值量
    出來的表每 8 個力度就會出現一次 0.2 dB 的倒退。層內包絡形狀既然相同，
    RMS 比值就是同一個衰減比，只是估得穩定得多。
    """
    peaks: Dict[int, float] = {}
    for velocity in range(1, 128):
        total = 0.0
        for pitch in GAIN_PROBE_PITCHES:
            flush(synth)
            samples = render_mono(synth, pitch, velocity, GAIN_PROBE_MS)
            if samples:
                total += math.sqrt(sum(v * v for v in samples) / len(samples))
        peaks[velocity] = total / len(GAIN_PROBE_PITCHES)

    gains = [0.0] * 128
    for band in bands:
        reference = peaks.get(band[1], 0.0)
        if reference <= 0:
            continue
        for velocity in range(band[0], band[1] + 1):
            gains[velocity] = peaks.get(velocity, 0.0) / reference
    return gains


def trim_and_fade(mono: Sequence[int], floor_amplitude: float) -> Tuple[List[int], bool]:
    """修掉尾端靜音並淡出，回傳 (樣本, 是不是被長度上限截斷的)。"""
    end = len(mono)
    while end > 0 and abs(mono[end - 1]) <= floor_amplitude:
        end -= 1
    if end <= 0:
        return ([], False)

    # 一個取樣都沒被修掉，代表它是撞到渲染長度上限才停的，不是自己衰減完的。
    truncated = end == len(mono)
    out = list(mono[:end])
    fade_ms = TRUNCATED_FADE_OUT_MS if truncated else FADE_OUT_MS
    fade_samples = min(len(out), int(SAMPLE_RATE * fade_ms / 1000.0))
    for i in range(fade_samples):
        gain = (fade_samples - i) / fade_samples
        index = len(out) - fade_samples + i
        out[index] = int(out[index] * gain)
    return (out, truncated)


def write_unity_meta(path: Path) -> None:
    """替 wav 寫出 Unity 的匯入設定。

    **`normalize` 一定要關掉。** 力度層之間的音量差就寫在波形裡（最輕的層峰值
    -35.9 dBFS，最重的 -1.0），逐檔正規化會把 35 dB 的動態全部拉成一樣大聲，
    velocity_gain 是相對於「所屬層的參考力度」的比值，補不回來。舊的取樣庫
    `.meta` 是 `forceToMono: 1` + `normalize: 1`，等於一直在做這件事。

    GUID 從檔名雜湊出來，重烤時同一個檔會拿到同一個 GUID。取樣是用
    `Resources.Load` 按路徑取的，沒有任何場景靠 GUID 參照它們。
    """
    import hashlib

    guid = hashlib.md5(path.name.encode("utf-8")).hexdigest()
    path.with_suffix(path.suffix + ".meta").write_text(
        "fileFormatVersion: 2\n"
        "guid: %s\n"
        "AudioImporter:\n"
        "  externalObjects: {}\n"
        "  serializedVersion: 8\n"
        "  defaultSettings:\n"
        "    serializedVersion: 2\n"
        "    loadType: 0\n"                 # Decompress On Load：發聲時不佔 CPU
        "    sampleRateSetting: 0\n"
        "    sampleRateOverride: 44100\n"
        "    compressionFormat: 2\n"        # Vorbis
        "    quality: 1\n"
        "    conversionMode: 0\n"
        "    preloadAudioData: 0\n"         # 1008 個檔，開場不要一次全載
        "  platformSettingOverrides: {}\n"
        "  forceToMono: 0\n"                # 本來就是單聲道
        "  normalize: 0\n"                  # ← 關鍵：別動波形的音量
        "  loadInBackground: 1\n"
        "  ambisonic: 0\n"
        "  3D: 1\n"
        "  userData: \n"
        "  assetBundleName: \n"
        "  assetBundleVariant: \n" % guid,
        encoding="utf-8",
    )


def write_wav(path: Path, mono: Sequence[int]) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(struct.pack("<%dh" % len(mono), *mono))


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1]
                    / "Nostalgia-clone" / "Assets" / "Resources" / "PianoSamples"),
        help="輸出資料夾（預設是 Unity 的 Resources/PianoSamples）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出音源結構與會產生的檔案數，不真的渲染")
    parser.add_argument("--soundfont", default=None,
                        help="要烤的 SF2（預設用製譜器的內建音源）")
    parser.add_argument("--preset", default=None,
                        help="要用哪一個 preset：名稱或編號。一份音源常有好幾種調音"
                             "（Nice-Steinway 的 0~3 是 Steinway Grand / Bright /"
                             " Mellow / Dark），預設取 bank 0 編號最小的那個")
    args = parser.parse_args(argv)

    # 不指定音源就跟著製譜器走——**連 preset 一起跟**。只跟檔名不跟 preset 的話，
    # 以後有人不加 --preset 重烤一次，就會把 preset 0 的音色烤進遊戲，而製譜器還在
    # 播 preset 1，兩邊悄悄分岔。
    if args.soundfont:
        soundfont = Path(args.soundfont)
        wanted = args.preset
    else:
        soundfont, default_preset = default_soundfont()
        wanted = args.preset if args.preset is not None else str(default_preset)
    preset_number, preset_label = resolve_preset(soundfont, wanted)
    bands, zones_by_band = read_soundfont_layout(soundfont, preset_label)
    total_files = sum(len(zones_by_band[band]) for band in bands)

    print("音源：%s（preset %d「%s」）" % (soundfont.name, preset_number, preset_label))
    print("力度分層 %d 層：%s" % (len(bands), bands))
    for band in bands:
        zone_list = zones_by_band[band]
        widths = [hi - lo + 1 for lo, hi in zone_list]
        print("  力度 %3d~%3d：%2d 個 key zone，寬度 %d~%d 個半音"
              % (band[0], band[1], len(zone_list), min(widths), max(widths)))
    print("共 %d 個取樣檔" % total_files)

    out_dir = Path(args.out)
    print("輸出到 %s" % out_dir)
    if args.dry_run:
        return 0

    # 重烤前先清掉舊的，否則上一版的命名會留在資料夾裡變成孤兒。
    if out_dir.exists():
        for stale in list(out_dir.glob("piano_*.wav")) + list(out_dir.glob("piano_*.wav.meta")):
            stale.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    synth = MidiPreviewSynth(SAMPLE_RATE, soundfont_path=str(soundfont),
                             preset=preset_number)
    try:
        print("\n量測力度→增益表（%d 個力度 × %d 個音高）…"
              % (127, len(GAIN_PROBE_PITCHES)))
        started = time.time()
        velocity_gains = measure_velocity_gains(synth, bands)
        print("  完成，耗時 %.1fs" % (time.time() - started))

        # 渲染結果**寫到暫存檔**而不是留在記憶體裡。整批共用一個增益，所以得先
        # 全部渲染完才知道峰值；12 層 × 84 zone 全留在 Python list 裡是十幾 GB
        # （每個 int 物件 28 bytes），2 層的舊音源僥倖撐得住而已。
        raw_dir = out_dir / ".raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        rendered: Dict[Tuple[int, int, int], Path] = {}
        peak = 0
        done = 0
        started = time.time()
        for band_index, band in enumerate(bands):
            for low_key, high_key in zones_by_band[band]:
                # 用 zone 的中間鍵當根音，遊戲端最多只要移調半個 zone 的寬度。
                root = (low_key + high_key) // 2
                length_ms = tail_seconds(root) * 1000.0
                flush(synth)
                mono = render_mono(synth, root, band[1], length_ms)
                peak = max(peak, max((abs(v) for v in mono), default=0))
                raw_path = raw_dir / ("%d_%d_%d.raw" % (band_index, low_key, high_key))
                raw_path.write_bytes(struct.pack("<%dh" % len(mono), *mono))
                rendered[(band_index, low_key, high_key)] = raw_path
                done += 1
                if done % 25 == 0 or done == total_files:
                    print("  [%4d/%4d] 力度層 %2d  key %3d~%3d  (已用 %5.1fs)"
                          % (done, total_files, band_index, low_key, high_key,
                             time.time() - started))
    finally:
        synth.close()

    # 整批共用一個增益：逐檔正規化會抹平力度層之間的音量差。
    if peak <= 0:
        print("錯誤：渲染出來全是靜音", file=sys.stderr)
        return 1
    target = 32767 * (10.0 ** (TARGET_PEAK_DB / 20.0))
    gain = target / peak
    floor_amplitude = target * (10.0 ** (SILENCE_FLOOR_DB / 20.0)) / gain
    print("\n整批峰值 %d → 套用增益 %.3f（目標 %.1f dBFS，純線性、無壓縮）"
          % (peak, gain, TARGET_PEAK_DB))

    manifest_bands = []
    for band_index, band in enumerate(bands):
        manifest_bands.append({
            "index": band_index,
            "low_velocity": band[0],
            "high_velocity": band[1],
            "reference_velocity": band[1],
            "zones": [],
        })

    total_bytes = 0
    truncated_count = 0
    for (band_index, low_key, high_key), raw_path in sorted(rendered.items()):
        raw = raw_path.read_bytes()
        mono = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
        raw_path.unlink()
        trimmed, truncated = trim_and_fade(mono, floor_amplitude)
        truncated_count += 1 if truncated else 0
        scaled = [max(-32768, min(32767, int(value * gain))) for value in trimmed]
        root = (low_key + high_key) // 2
        name = "piano_k%03d_v%d" % (root, band_index)
        path = out_dir / (name + ".wav")
        write_wav(path, scaled)
        write_unity_meta(path)
        total_bytes += path.stat().st_size
        manifest_bands[band_index]["zones"].append({
            "low_key": low_key,
            "high_key": high_key,
            "root_key": root,
            "sample": name,
            "seconds": round(len(scaled) / SAMPLE_RATE, 3),
        })

    try:
        raw_dir.rmdir()
    except OSError:
        pass

    # 最低那一層從力度 1 開始的音源不少（Nice-Steinway 就是），力度 0 會在遊戲端
    # 的 FindBand 掉到最後一層＝最大聲那層。把下限補到 0 堵住這個洞。
    if manifest_bands:
        manifest_bands[0]["low_velocity"] = 0

    manifest = {
        "soundfont": soundfont.name,
        "preset": preset_label,
        "preset_number": preset_number,
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "resource_folder": "PianoSamples",
        "lowest_pitch": MIDI_LOWEST,
        "highest_pitch": MIDI_HIGHEST,
        # velocity_gain[v] 是力度 v 相對於「v 所屬那一層的參考力度」的線性增益。
        "velocity_gain": [round(value, 6) for value in velocity_gains],
        "velocity_bands": manifest_bands,
    }
    manifest_path = out_dir / "piano_samples.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("寫出 %d 個 wav，共 %.1f MB" % (total_files, total_bytes / 1048576))
    print("撞到長度上限、靠長淡出收尾的：%d / %d" % (truncated_count, total_files))
    print("清單：%s" % manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
