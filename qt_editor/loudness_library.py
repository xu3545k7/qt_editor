"""批次量測曲庫的音量，把增益寫回每首歌的 register.json。

nos-clone 這一端**不改音檔**：量出來的增益寫進 register.json，遊戲載入時套在音量
上。原因是 UserSongs 裡放的是使用者自己的檔案，改寫它們既難還原，也會讓「原始音源」
這件事永遠說不清楚。輸出 Hiraeth 歌曲包的時候才把增益烘進 WAV（那邊必須是單一檔案）。

伴奏和鋼琴兩軌**各自**對齊目標（使用者的選擇），所以兩軌的增益是分開算的。

用法：

    python -m qt_editor.loudness_library "D:/Nostalgia/Nostalgia-clone/UserSongs"
    python -m qt_editor.loudness_library <root> --target -16 --force --csv out.csv
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple

from . import loudness

#: 寫進 register.json 的格式標記。改演算法或預設值就換版本，舊的會自動重量。
SCHEMA = 'nos-clone-loudness-v1'
#: 演算法版本：影響量測結果的改動要動它。
TOOL_VERSION = 1

_AUDIO_EXTS = ('.wav',)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def resolve_audio(song_dir: str, resource: Optional[str]) -> Optional[str]:
    """`songs/Abiogenesis/Abiogenesis_ele` → 這台機器上的實際檔案。"""
    if not resource:
        return None
    name = resource.replace('\\', '/').split('/')[-1]
    for ext in _AUDIO_EXTS:
        candidate = os.path.join(song_dir, name + ext)
        if os.path.isfile(candidate):
            return candidate
    # 大小寫或副檔名不同的情況，掃一次資料夾。
    if os.path.isdir(song_dir):
        for entry in sorted(os.listdir(song_dir)):
            stem, ext = os.path.splitext(entry)
            if stem.lower() == name.lower() and ext.lower() in _AUDIO_EXTS:
                return os.path.join(song_dir, entry)
    return None


def collect_tracks(register: dict) -> List[str]:
    """這首歌用到的音訊資源路徑，去重後照出現順序。"""
    out: List[str] = []
    for diff in register.get('difficulties') or []:
        for key in ('audioResourcePath', 'pianoAudioResourcePath'):
            value = diff.get(key)
            if value and value not in out:
                out.append(value)
    return out


def measure_song(song_dir: str, register: dict, target_lufs: float,
                 peak_limit_dbtp: float, force: bool = False) -> Tuple[dict, List[dict]]:
    """量一首歌的每一軌，回傳 (要寫進 register 的區塊, 每一軌的結果)。"""
    previous = register.get('audioLoudness') or {}
    previous_tracks = previous.get('tracks') or {}
    reusable = (not force
                and previous.get('schema') == SCHEMA
                and previous.get('toolVersion') == TOOL_VERSION
                and abs(float(previous.get('targetLufs', 1e9)) - target_lufs) < 1e-6
                and abs(float(previous.get('peakLimitDbtp', 1e9)) - peak_limit_dbtp) < 1e-6)

    tracks: Dict[str, dict] = {}
    rows: List[dict] = []
    for resource in collect_tracks(register):
        path = resolve_audio(song_dir, resource)
        row = {'song': os.path.basename(song_dir), 'resource': resource, 'file': path or ''}
        if path is None:
            row.update(status='missing', reason='找不到音檔')
            rows.append(row)
            continue

        digest = _sha256(path)
        old = previous_tracks.get(resource)
        # 同一個檔案、同一組設定就不重量。只看檔名或「處理過」旗標會漏掉換檔的情況。
        if reusable and old and old.get('sha256') == digest:
            tracks[resource] = old
            row.update(old)
            row['status'] = old.get('status', 'ok')
            row['reused'] = True
            rows.append(row)
            continue

        try:
            measured = loudness.measure_file(path)
        except loudness.LoudnessError as error:
            # 無聲的音軌是**正常**的：只有按鍵音的曲子和教學曲就是配一段靜音當伴奏。
            # 它們不是壞檔，只是沒有東西可以對齊，所以不寫增益、也不算失敗。
            silent = '靜音' in str(error) or '門限' in str(error)
            row.update(status='silent' if silent else 'failed', reason=str(error))
            rows.append(row)
            continue

        plan = loudness.plan_gain(measured, target_lufs, peak_limit_dbtp)
        entry = {
            'gainDb': round(plan.gain_db, 2),
            'status': plan.status,
            'lufs': round(measured.lufs_i, 2),
            'peakDbtp': round(measured.true_peak_dbtp, 2),
            'lra': round(measured.lra, 2),
            'sampleRate': measured.rate,
            'channels': measured.channels,
            'frames': measured.frames,
            'sha256': digest,
        }
        if plan.reason:
            entry['reason'] = plan.reason
        tracks[resource] = entry
        row.update(entry)
        row['reused'] = False
        rows.append(row)

    block = {
        'schema': SCHEMA,
        'toolVersion': TOOL_VERSION,
        'targetLufs': target_lufs,
        'peakLimitDbtp': peak_limit_dbtp,
        'tracks': tracks,
    }
    return block, rows


def scan(root: str, target_lufs: float = loudness.DEFAULT_TARGET_LUFS,
         peak_limit_dbtp: float = loudness.DEFAULT_PEAK_LIMIT_DBTP,
         force: bool = False, write: bool = True,
         progress=None) -> List[dict]:
    """掃過整個 UserSongs，回傳每一軌的結果。"""
    rows: List[dict] = []
    if not os.path.isdir(root):
        raise ValueError('找不到曲庫資料夾：%s' % root)

    for name in sorted(os.listdir(root)):
        song_dir = os.path.join(root, name)
        register_path = os.path.join(song_dir, 'register.json')
        if not os.path.isfile(register_path):
            continue
        with open(register_path, 'r', encoding='utf-8') as f:
            register = json.load(f)

        block, song_rows = measure_song(song_dir, register, target_lufs, peak_limit_dbtp, force)
        rows.extend(song_rows)
        if progress:
            progress(name, song_rows)

        if write and block['tracks']:
            register['audioLoudness'] = block
            tmp = register_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(register, f, ensure_ascii=False, indent=2)
            os.replace(tmp, register_path)
    return rows


def write_csv(rows: List[dict], path: str) -> None:
    import csv
    fields = ['song', 'resource', 'status', 'lufs', 'peakDbtp', 'lra', 'gainDb',
              'sampleRate', 'channels', 'frames', 'reused', 'reason', 'file']
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _main(argv: List[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='量測曲庫音量並寫回 register.json')
    parser.add_argument('root', help='UserSongs 資料夾')
    parser.add_argument('--target', type=float, default=loudness.DEFAULT_TARGET_LUFS)
    parser.add_argument('--peak', type=float, default=loudness.DEFAULT_PEAK_LIMIT_DBTP)
    parser.add_argument('--force', action='store_true', help='忽略快取，全部重量')
    parser.add_argument('--dry-run', action='store_true', help='只量測，不寫檔')
    parser.add_argument('--csv', help='把結果寫成 CSV')
    args = parser.parse_args(argv)

    # 曲名有中文，而 Windows 的主控台預設是 cp950：不換編碼的話，印進度的那一行會
    # 丟 UnicodeEncodeError，整個掃描在中途炸掉 —— 量測本身沒問題，死在輸出。
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    def progress(song: str, song_rows: List[dict]) -> None:
        for row in song_rows:
            if row.get('reused'):
                continue
            print('%-28s %-34s %-12s %8s %8s %8s' % (
                song[:28], os.path.basename(row.get('resource') or '')[:34],
                row.get('status', ''), row.get('lufs', ''), row.get('peakDbtp', ''),
                row.get('gainDb', '')))

    rows = scan(args.root, args.target, args.peak, args.force,
                write=not args.dry_run, progress=progress)
    if args.csv:
        write_csv(rows, args.csv)

    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.get('status', '?')] = counts.get(row.get('status', '?'), 0) + 1
    print('\n共 %d 軌：%s' % (len(rows), ', '.join('%s=%d' % kv for kv in sorted(counts.items()))))
    return 0


if __name__ == '__main__':  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
