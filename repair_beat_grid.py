# -*- coding: utf-8 -*-
"""把既有譜面 `beat_timings` 裡缺掉的拍點補回去。

使用者回報「6/8 拍號中只有三個拍」。那個病在編輯器的算法裡，已經修好了
（Suspenseful_Third_Day 從 215 個小節修回 108 個，沒有動任何資料）。這支工具
處理的是另一件事：**有些譜的檔案本身少了拍點**。

**補洞**（`fill_beat_index_gaps`）
    index 跳號：Suspenseful_Third_Day 從 143000 跳到 145000，中間那筆不見了，
    那一小節就只有 2 條拍線而不是 3 條。只補「跳號剛好是刻度整數倍」的位置，
    那種位置唯一確定（index 照刻度遞增、ms 線性內插）。純粹是把檔案裡該有
    的東西補回來。

## 「一小節一筆」的譜不在處理範圍內

曲庫裡有 200 多份譜的格線是一小節一筆（MIDI 匯入留下的），它們的小節裡沒有
拍線。那**不是病**：小節線的位置都是對的，4/4 和 6/8 都一樣少，遊戲裡也是
一小節一條線——它就是一條小節格線而不是拍格線。

重鋪成「一拍一筆」要用 `--regrid`，**預設關著**，因為那會改到玩家看到的畫面：
`BeatLineSpawner` 是一筆 `beat_timings` 生一條拍線，試算過會動到 **508 份
檔案**（Abiogenesis 185 → 556 筆，整個曲庫的拍線變四倍密）。那是美術決定，
不是修 bug。

## 為什麼不整檔重存

實測原地 `save_json()` 會順便改掉很多東西：Scherzo 全部 5069 顆音符的
`index`、V 2757 顆、Winter Wind 3174 顆，還有一些 `param1`/`param2` 和
`hostIndex`（存檔重新編號）。那些就算是正確的正規化，也不該當成「修格線」的
副作用塞進使用者的曲庫。所以這支工具**只定點替換 `beat_timings`、
`beat_indices` 這兩個陣列**（重鋪時多寫一個 `beat_entry_quarters`），其餘
位元組原樣不動。

## 安全網

每一份都先在記憶體裡修、驗完四條不變量才寫檔，寫之前留 `.bak`：

  1. 音符一顆都沒動（start/end/gate/pitch/hand/lane 全等）
  2. 小節數不變
  3. 每個小節的起訖毫秒不變
  4. `music_finish_time_msec` 不變

任何一條沒過就跳過那個檔案並印出原因，不會寫壞。

    python repair_beat_grid.py              # 只看報告，不寫檔
    python repair_beat_grid.py --apply      # 真的寫（先備份 .bak）
    python repair_beat_grid.py --regrid     # 連「一小節一筆」的也重鋪（見上面）
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_editor.models import NoteModel     # noqa: E402



#: 兩份曲庫都是活的：Unity 在 Editor 裡讀 `Nostalgia-clone/UserSongs`
#: （`Application.dataPath/../UserSongs`），打包之後讀 exe 旁邊的那份。
DEFAULT_ROOTS = ('D:/Nostalgia/UserSongs',
                 'D:/Nostalgia/Nostalgia-clone/UserSongs')

#: 這些不是譜面檔
SKIP_SUFFIX = ('.analysis.json', 'register.json', 'library.json',
               'songlist.json', 'tutorial.json', 'settings.json')

#: 路徑裡有這些字就是備份或副本，不要動
SKIP_PART = ('_backup', 'usersongs copy', 'songs_backup')


def chart_files(roots):
    out = []
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, '**', '*.json'),
                                     recursive=True)):
            low = path.replace(chr(92), '/').lower()
            if any(low.endswith(s.lower()) for s in SKIP_SUFFIX):
                continue
            if any(part in low for part in SKIP_PART):
                continue
            out.append(path)
    return out


def snapshot(m):
    """修之前的樣子，用來驗「什麼都沒被動到」。"""
    bars = []
    for i in range(m.count_measures()):
        s, e = m.get_measure_time_range(i)
        if s is None or e is None:
            break
        bars.append((int(s), int(e)))
    notes = [(int(n.start), int(n.end), int(n.gate), n.pitch, int(n.hand),
              int(n.min_key), int(n.max_key), int(n.note_type))
             for n in m.notes_tree]
    return {'bars': bars, 'notes': notes,
            'end': int(round(float(m.music_end_ms)))}


def bad_bars(m):
    """拍點筆數不是分子整數倍的小節數，以及第一個的說明。

    分子大於 16 而分母 ≥ 8 的拍號不算（24/16、36/8 是 MIDI 匯入的產物，
    沒有音樂意義——24 個十六分音符就是 6 個四分音符，格線有 6 筆是對的，
    只有「分子」那個數字不能當拍數看）。
    """
    beats = m.get_beat_entries()
    n, why = 0, None
    for bi in range(max(0, m.count_measures() - 1)):   # 最後一筆是收尾標記
        s, e = m.get_measure_time_range(bi)
        if s is None or e is None or e <= s:
            break
        num = max(1, int(m.get_beats_per_bar_at_ms(s)))
        den = m._denominator_at_ms(s)
        if num > 16 and den >= 8:
            continue
        inside = sum(1 for _i, b in beats if s <= b < e)
        if inside >= num and inside % num == 0:
            continue
        n += 1
        if why is None:
            why = '第%d小節 %d/%d 只有 %d 筆拍點（要 %d 的整數倍）' % (
                bi + 1, num, den, inside, num)
    return n, why


def verify(before, m):
    """四條不變量。回傳失敗原因的清單（空的就是過了）。"""
    after = snapshot(m)
    errs = []
    if after['notes'] != before['notes']:
        moved = sum(1 for a, b in zip(before['notes'], after['notes']) if a != b)
        errs.append('音符被動到了（%d 顆不同，數量 %d → %d）'
                    % (moved, len(before['notes']), len(after['notes'])))
    if len(after['bars']) != len(before['bars']):
        errs.append('小節數 %d → %d' % (len(before['bars']), len(after['bars'])))
    else:
        for i, (a, b) in enumerate(zip(before['bars'], after['bars'])):
            if a != b:
                errs.append('第%d小節 %s → %s' % (i + 1, a, b))
                break
    if after['end'] != before['end']:
        errs.append('曲末 %d → %d' % (before['end'], after['end']))
    return errs


def patch_text(text, indices, timings, span=None):
    """把原始 JSON 文字裡的拍點陣列換掉，其餘位元組不動。

    照原本的排版寫（一個值一行、4 空白縮排），這樣 diff 看起來只有數字變動。
    """
    def array(name, values):
        body = ''.join('    %d,\n' % v for v in values)[:-2] + '\n'
        return '"%s": [\n%s  ]' % (name, body)

    for name, values in (('beat_indices', indices), ('beat_timings', timings)):
        m = re.search(r'"%s"\s*:\s*\[' % name, text)
        if m is None:
            raise ValueError('找不到 "%s"' % name)
        close = text.index(']', m.end())
        text = text[:m.start()] + array(name, values) + text[close + 1:]
    if span is not None:
        # 重鋪之後一筆拍點代表的音值要釘住，否則讀回來會重新量一次，量到的和
        # 鋪的時候用的不一樣就會把小節切在拍點中間。插在 beat_timings 前面。
        if '"beat_entry_quarters"' in text:
            text = re.sub(r'"beat_entry_quarters"\s*:\s*[0-9.]+',
                          '"beat_entry_quarters": %s' % repr(float(span)), text)
        else:
            m = re.search(r'(\s*)"beat_timings"\s*:', text)
            text = (text[:m.start()]
                    + '%s"beat_entry_quarters": %s,' % (m.group(1), repr(float(span)))
                    + text[m.start():])
    return text


def main():
    apply = '--apply' in sys.argv[1:]
    regrid = '--regrid' in sys.argv[1:]
    roots = [r for r in os.environ.get('QTE_CORPUS', '').split(os.pathsep) if r]
    roots = roots or list(DEFAULT_ROOTS)
    roots = [r for r in roots if os.path.isdir(r)]

    fixed, skipped, refused, coarse, clean = [], [], [], [], 0
    for path in chart_files(roots):
        m = NoteModel()
        try:
            m.load_json(path)
            if not m.get_beat_entries():
                continue
            m.rebuild_display_cache()
        except Exception as exc:                        # noqa: BLE001
            skipped.append((path, '讀不進來：%s' % exc))
            continue

        n0, why0 = bad_bars(m)
        if not n0:
            clean += 1
            continue

        before = snapshot(m)
        actions, span = [], None
        added = m.fill_beat_index_gaps()
        if added:
            m.rebuild_display_cache()
            actions.append('補 %d 筆拍點' % added)
        # 重鋪只對「一小節一筆」那種舊格線有效（`ensure_precise_beat_grid`
        # 在 explicit 的譜上直接 return）。先補洞再看還需不需要重鋪。
        if regrid and bad_bars(m)[0] and not m._uses_explicit_beat_units():
            n_before = len(m.get_beat_entries())
            m.ensure_precise_beat_grid()
            m.rebuild_display_cache()
            span = float(m.json_meta.get(m.BEAT_ENTRY_QUARTERS_KEY) or 1.0)
            actions.append('重鋪格線（%d → %d 筆，一筆 = %g 個四分音符）'
                           % (n_before, len(m.get_beat_entries()), span))
        if not actions:
            coarse.append((path, n0, why0))
            continue

        errs = verify(before, m)
        if errs:
            refused.append((path, '修完驗不過，沒寫：%s' % '；'.join(errs)))
            continue

        n1, why1 = bad_bars(m)
        beats = m.get_beat_entries()
        row = (path, n0, n1, '＋'.join(actions), why1)
        if apply:
            text = io.open(path, encoding='utf-8').read()
            try:
                new_text = patch_text(text, [int(i) for i, _ms in beats],
                                      [int(ms) for _i, ms in beats], span)
            except (ValueError, AttributeError) as exc:
                refused.append((path, '改不動原始檔：%s' % exc))
                continue
            bak = path + '.bak'
            if not os.path.exists(bak):
                io.open(bak, 'w', encoding='utf-8', newline='').write(text)
            io.open(path, 'w', encoding='utf-8', newline='').write(new_text)
        fixed.append(row)

    print('%s：掃了 %d 個資料夾' % ('寫入' if apply else '試算（沒有寫檔）',
                                   len(roots)))
    print('  本來就好的 %d 份' % clean)
    print('  修好 %d 份：' % len(fixed))
    for path, n0, n1, what, why1 in fixed:
        tail = '' if not n1 else '  （還剩 %d 個小節：%s）' % (n1, why1)
        print('    %-58s %3d → %-3d %s%s'
              % (short(path), n0, n1, what, tail))
    if refused:
        print('  驗不過、沒寫 %d 份：' % len(refused))
        for path, why in refused:
            print('    %-58s %s' % (short(path), why))
    if coarse:
        # 一小節一筆的舊格線。不是病，見檔頭說明；要重鋪用 --regrid。
        songs = sorted({short(p).split('/')[0] for p, _n, _w in coarse})
        print('  沒動（格線是一小節一筆，不是病）%d 份 / %d 首曲目'
              % (len(coarse), len(songs)))
        for path, n0, why in coarse[:6]:
            print('    %-58s %3d 個小節  %s' % (short(path), n0, why))
        if len(coarse) > 6:
            print('    ……其餘 %d 份同類。要重鋪格線用 --regrid（會改到遊戲畫面）'
                  % (len(coarse) - 6))
    if skipped:
        print('  跳過 %d 份：' % len(skipped))
        for path, why in skipped:
            print('    %-58s %s' % (short(path), why))
    if not apply and fixed:
        print('')
        print('要真的寫入就加 --apply（會先留 .bak）。')


def short(path):
    p = path.replace(chr(92), '/')
    for root in ('UserSongs/',):
        i = p.rfind(root)
        if i >= 0:
            return p[i + len(root):]
    return os.path.basename(p)


if __name__ == '__main__':
    # 曲名裡有 ū（syūten）這種字，Windows 的 cp950 主控台印不出來就整支爆掉。
    # 放在這裡而不是模組層：被 import 的時候不該去動呼叫端的 stdout（換掉之後
    # 舊的 wrapper 被回收會把底層的 buffer 一起關掉）。
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                      errors='replace')
    main()
