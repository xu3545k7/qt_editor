#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a song-select preview for charts that ship no audible recording.

A keysound-only song points ``audioResourcePath`` at a file of pure digital
silence so that the timeline runs while every sound comes from the notes the
player strikes.  That works in gameplay and leaves the song select screen mute,
and "this song has no preview" and "this song is broken" look the same from the
player's side.

Where the library keeps the real recording beside the silent one -- ``X_silent``
next to ``X`` -- the game finds it by itself and this script is not needed.  This
is for the songs where no recording exists at all: it renders one from the chart,
using the very samples the game plays when it synthesises that song.

    python qt_editor/render_song_preview.py                 # 只補缺的
    python qt_editor/render_song_preview.py --force         # 全部重算
    python qt_editor/render_song_preview.py <library root>

The result is written next to the song as ``<audio stem>_preview.wav``, which is
where ``SongSelectionManager`` looks.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import struct
import sys
import wave

PREVIEW_SECONDS = 45.0
# 前後各淡一段。預覽是循環播的，硬切的接縫每一圈都會聽到。
FADE_SECONDS = 1.5
# 峰值留一點餘裕：混完之後正規化到這裡，而不是貼著滿刻度。
TARGET_PEAK = 0.82
BUCKET_COUNT = 48


def note_pitch(note):
    """MIDI pitch, falling back to the legacy 1..88 keyboard index."""
    pitch = note.get("pitch") or 0
    if pitch <= 0:
        scale = note.get("scale_piano") or 0
        if scale > 0:
            pitch = scale + 20
    return pitch


def load_manifest(project_root):
    path = os.path.join(project_root, "Nostalgia-clone", "Assets", "Resources",
                        "PianoSamples", "piano_samples.json")
    if not os.path.exists(path):
        sys.exit("no piano sample manifest at " + path)
    return json.load(io.open(path, encoding="utf-8")), os.path.dirname(path)


def pick_zone(manifest, pitch, velocity):
    """The band and zone the game would choose for this note.

    Mirrors ``PianoSampleManifest.Pick`` rather than reinventing a mapping: the
    preview has to sound like the song does, and the only way to guarantee that
    is to select the same file the game would.
    """
    band = None
    for candidate in manifest["velocity_bands"]:
        if candidate["low_velocity"] <= velocity <= candidate["high_velocity"]:
            band = candidate
            break
    if band is None:
        band = manifest["velocity_bands"][-1]

    for zone in band["zones"]:
        if zone["low_key"] <= pitch <= zone["high_key"]:
            return zone
    # 超出取樣範圍的音高用最近的 zone，靠變速補足。
    return min(band["zones"], key=lambda z: abs(z["root_key"] - pitch))


def read_sample(folder, name, cache):
    if name in cache:
        return cache[name]

    path = os.path.join(folder, name + ".wav")
    with wave.open(path, "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
        channels = handle.getnchannels()
    if width != 2:
        sys.exit("expected 16-bit samples: " + path)

    count = len(frames) // 2
    data = struct.unpack("<%dh" % count, frames)
    if channels > 1:
        data = data[::channels]
    cache[name] = data
    return data


def busiest_start(times, span_ms):
    """Where the preview should begin: the busiest stretch, not the intro."""
    if not times or span_ms <= 0:
        return 0.0
    buckets = [0] * BUCKET_COUNT
    for time in times:
        buckets[min(BUCKET_COUNT - 1, max(0, int(time / span_ms * BUCKET_COUNT)))] += 1
    peak = buckets.index(max(buckets))
    start = peak / BUCKET_COUNT * span_ms
    # 往前退一點，免得預覽從一個樂句的正中間切進來。
    start = max(0.0, start - 2000.0)
    # 再往回收，讓整段視窗都落在曲子裡面。最密的那一格如果靠近結尾，不收的話
    # 視窗有一大半會落在最後一顆音之後，預覽就變成幾秒的音樂加四十秒的空白。
    return max(0.0, min(start, span_ms - PREVIEW_SECONDS * 1000.0))


def render(chart_path, manifest, sample_folder, rate):
    chart = json.load(io.open(chart_path, encoding="utf-8-sig"))
    notes = chart.get("notes") or []
    if not notes:
        return None

    times = [n.get("startTime") or 0 for n in notes]
    span_ms = max(chart.get("music_finish_time_msec") or 0, max(times))
    start_ms = busiest_start(times, span_ms)
    end_ms = start_ms + PREVIEW_SECONDS * 1000.0

    total = int(PREVIEW_SECONDS * rate) + rate
    mix = [0.0] * total
    gains = manifest["velocity_gain"]
    cache = {}
    used = 0

    for note in notes:
        start = note.get("startTime") or 0
        if start < start_ms or start >= end_ms:
            continue
        pitch = note_pitch(note)
        if pitch <= 0:
            continue

        velocity = int(note.get("velocity") or 96)
        velocity = max(1, min(127, velocity))
        zone = pick_zone(manifest, pitch, velocity)
        data = read_sample(sample_folder, zone["sample"], cache)
        if not data:
            continue

        ratio = 2.0 ** ((pitch - zone["root_key"]) / 12.0)
        gain = gains[velocity] if velocity < len(gains) else 1.0
        offset = int((start - start_ms) / 1000.0 * rate)
        used += 1

        # 線性內插的重取樣。zone 幾乎都只跨一兩個半音，比例離 1 很近，預覽這個
        # 用途下聽不出和更好的濾波器有什麼差別。
        length = int(len(data) / ratio)
        limit = min(length, total - offset)
        for i in range(limit):
            position = i * ratio
            index = int(position)
            if index + 1 >= len(data):
                break
            frac = position - index
            value = data[index] * (1.0 - frac) + data[index + 1] * frac
            mix[offset + i] += value * gain

    if used == 0:
        return None

    # 用高百分位而不是絕對峰值來定音量。單一個和弦疊起來的瞬時峰值可以比全曲的
    # 一般音量高二十幾 dB，照那個正規化的話整段預覽會小到聽不見。超過的部分交給
    # tanh 軟膝壓回去，這也是這個專案合成端本來的做法。
    magnitudes = sorted(abs(v) for v in mix if v)
    if not magnitudes:
        return None
    reference = magnitudes[int(len(magnitudes) * 0.995)]
    if reference <= 1.0:
        return None
    scale = TARGET_PEAK / reference

    fade = int(FADE_SECONDS * rate)
    out = bytearray()
    for i, value in enumerate(mix):
        envelope = 1.0
        if i < fade:
            envelope = i / fade
        elif i > total - fade:
            envelope = max(0.0, (total - i) / fade)
        driven = value * scale * envelope
        sample = int(max(-32768, min(32767, math.tanh(driven) * 32767.0)))
        out += struct.pack("<h", sample)
    return bytes(out), used


def iter_keysound_songs(root):
    for folder, _dirs, names in os.walk(root):
        if "register.json" not in names:
            continue
        try:
            data = json.load(io.open(os.path.join(folder, "register.json"), encoding="utf-8-sig"))
        except Exception:
            continue
        for difficulty in data.get("difficulties") or []:
            if difficulty.get("noBackgroundMusic"):
                yield folder, data, difficulty
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    project = os.path.dirname(here)
    root = args.root or os.path.join(project, "Nostalgia-clone", "UserSongs")
    manifest, sample_folder = load_manifest(project)
    rate = manifest.get("sample_rate", 44100)

    written = skipped = 0
    for folder, register, difficulty in iter_keysound_songs(root):
        audio = difficulty.get("audioResourcePath") or ""
        stem = os.path.basename(audio)
        if not stem:
            continue

        # 真的錄音就在旁邊的（X_silent 對上 X），遊戲自己找得到，不用產生。
        if stem.endswith("_silent") and os.path.exists(os.path.join(folder, stem[:-7] + ".wav")):
            skipped += 1
            continue

        target = os.path.join(folder, stem + "_preview.wav")
        if os.path.exists(target) and not args.force:
            skipped += 1
            continue

        chart = difficulty.get("chartFileName") or ""
        chart_path = os.path.join(folder, os.path.basename(os.path.dirname(chart)),
                                  os.path.basename(chart) + ".json")
        if not os.path.exists(chart_path):
            print("  no chart for", stem.encode("ascii", "backslashreplace").decode())
            continue

        result = render(chart_path, manifest, sample_folder, rate)
        if result is None:
            print("  nothing to render for", stem.encode("ascii", "backslashreplace").decode())
            continue

        payload, used = result
        with wave.open(target, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(payload)
        written += 1
        print("  wrote %s (%d notes)" % (
            os.path.basename(target).encode("ascii", "backslashreplace").decode(), used))

    print("rendered %d, skipped %d" % (written, skipped))


if __name__ == "__main__":
    main()
