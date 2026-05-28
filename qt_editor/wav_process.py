#!/usr/bin/env python3
"""
wav_process.py
----------------
簡單的 WAV 裁切 / 補零 (pad/trim) 工具。

功能：
- 以毫秒為單位在開頭加入靜音（pad）或從開頭裁切（trim）。
- 支援從檔名自動解析 offset，例如："name_+1000ms.wav" 或 "name_-500ms.wav"。
- 可指定結尾裁切（--trim-end-ms）。
- 提供 --test 模式，會產生一個測試 WAV 並示範 pad/trim 行為。

使用範例：
  python wav_process.py input.wav output.wav --offset-ms 1000   # 在開頭加入 1000ms 靜音
  python wav_process.py input.wav output.wav --offset-ms -500   # 從開頭裁切 500ms
  python wav_process.py input.wav                                 # 若檔名含 +/-NNNms，會自動套用並輸出 input_fixed.wav

注意：此工具使用標準庫 wave，適用於 PCM WAV 檔案（常見 16-bit/44.1kHz）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import wave
from typing import Optional


def parse_offset_from_filename(path: str) -> Optional[int]:
    """從檔名搜尋像 '+1000ms' 或 '-500ms' 的片段，回傳毫秒整數（含符號）。"""
    name = os.path.basename(path)
    m = re.search(r'([+-]\d+)ms', name)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def process_wav(input_path: str, output_path: str, offset_ms: int = 0, trim_end_ms: int = 0) -> None:
    """將 offset_ms 應用到 input_path 並輸出到 output_path。
    正數 offset_ms => 在開頭 PAD 靜音（將聲音往後移）；
    負數 offset_ms => 從開頭 TRIM（將聲音往前移）。
    trim_end_ms 可以指定結尾要裁切的毫秒數（預設 0）。
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")

    with wave.open(input_path, 'rb') as wr_in:
        nchannels = wr_in.getnchannels()
        sampwidth = wr_in.getsampwidth()
        framerate = wr_in.getframerate()
        nframes = wr_in.getnframes()
        comptype = wr_in.getcomptype()
        compname = wr_in.getcompname()

        if comptype != 'NONE':
            print(f"Warning: non-PCM WAV (comptype={comptype}) may not be supported.")

        frames_to_offset = int(round(abs(offset_ms) * framerate / 1000.0))
        frames_to_trim_end = int(round(max(0, trim_end_ms) * framerate / 1000.0))

        # Open output and set same params
        with wave.open(output_path, 'wb') as wr_out:
            wr_out.setnchannels(nchannels)
            wr_out.setsampwidth(sampwidth)
            wr_out.setframerate(framerate)
            wr_out.setcomptype(comptype, compname)

            CHUNK_FRAMES = 4096

            if offset_ms >= 0:
                # PAD: write silence first
                to_pad = frames_to_offset
                if to_pad > 0:
                    silence_frame = b'\x00' * (nchannels * sampwidth)
                    # write in chunks
                    while to_pad > 0:
                        f = min(CHUNK_FRAMES, to_pad)
                        wr_out.writeframes(silence_frame * f)
                        to_pad -= f

                # copy from input, but stop early if trimming end
                total_frames = nframes
                frames_to_copy = max(0, total_frames - frames_to_trim_end)
                copied = 0
                while copied < frames_to_copy:
                    toread = min(CHUNK_FRAMES, frames_to_copy - copied)
                    data = wr_in.readframes(toread)
                    if not data:
                        break
                    wr_out.writeframes(data)
                    copied += toread

            else:
                # TRIM: skip frames at start
                to_skip = frames_to_offset
                skipped = 0
                while skipped < to_skip:
                    t = min(CHUNK_FRAMES, to_skip - skipped)
                    _ = wr_in.readframes(t)
                    skipped += t

                # then copy until remaining frames minus end-trim
                total_frames = nframes
                remaining = max(0, total_frames - frames_to_offset - frames_to_trim_end)
                copied = 0
                while copied < remaining:
                    toread = min(CHUNK_FRAMES, remaining - copied)
                    data = wr_in.readframes(toread)
                    if not data:
                        break
                    wr_out.writeframes(data)
                    copied += toread


def _generate_sine_wav(path: str, duration_s: float = 1.0, freq_hz: float = 440.0, framerate: int = 44100) -> None:
    import math
    import struct

    nchannels = 1
    sampwidth = 2  # 16-bit
    nframes = int(duration_s * framerate)
    amplitude = 0.5 * (2 ** (8 * sampwidth - 1) - 1)

    with wave.open(path, 'wb') as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        for i in range(nframes):
            t = i / framerate
            value = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t))
            w.writeframes(struct.pack('<h', value))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description='Simple WAV pad/trim utility')
    p.add_argument('input', nargs='?', help='input WAV file')
    p.add_argument('output', nargs='?', help='output WAV file (optional)')
    p.add_argument('--offset-ms', type=int, default=None,
                   help='offset in ms (positive=pad start, negative=trim start). If omitted, will try to parse from filename.')
    p.add_argument('--trim-end-ms', type=int, default=0, help='trim this many ms from end')
    p.add_argument('--test', action='store_true', help='generate test WAV and demonstrate pad/trim')
    args = p.parse_args(argv)

    if args.test:
        base = os.path.dirname(__file__)
        test_in = os.path.join(base, 'test_in.wav')
        test_pad = os.path.join(base, 'test_pad_+1000ms.wav')
        test_trim = os.path.join(base, 'test_trim_-300ms.wav')
        print('Generating test WAV ->', test_in)
        _generate_sine_wav(test_in, duration_s=2.0)
        print('Pad 1000ms ->', test_pad)
        process_wav(test_in, test_pad, offset_ms=1000)
        print('Trim 300ms ->', test_trim)
        process_wav(test_in, test_trim, offset_ms=-300)
        print('Test files created.')
        return 0

    if not args.input:
        p.print_help()
        return 1

    inp = args.input
    out = args.output
    if out is None:
        root, ext = os.path.splitext(inp)
        out = root + '_fixed' + ext

    offset = args.offset_ms
    if offset is None:
        offset = parse_offset_from_filename(inp)
        if offset is None:
            print('No offset specified and none detected in filename; defaulting to 0ms')
            offset = 0
        else:
            print(f'Auto-detected offset from filename: {offset} ms')

    print(f'Processing: {inp} -> {out} (offset={offset} ms, trim_end={args.trim_end_ms} ms)')
    try:
        process_wav(inp, out, offset_ms=offset, trim_end_ms=args.trim_end_ms)
    except Exception as e:
        print('Error processing WAV:', e)
        return 2

    print('Done.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
