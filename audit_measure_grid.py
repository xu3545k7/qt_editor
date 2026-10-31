# -*- coding: utf-8 -*-
"""拿整個曲庫稽核小節格線：插入／刪除／末尾新增／改拍號／小節長度。

動 `models.py` 的小節與拍點邏輯之前先跑一次留下基線，改完再跑一次對照。
單元測試抓不到這類病——它們是「某些真實譜面的格線長得不像我以為的樣子」。

    python audit_measure_grid.py base          # 基線
    # ...改程式...
    python audit_measure_grid.py after         # 對照
    diff audit-base.txt audit-after.txt

要和 HEAD 對照就開一個 worktree，用 `QTE_ROOT` 指過去：

    git worktree add /tmp/head HEAD
    QTE_ROOT=/tmp/head python audit_measure_grid.py head

四條不變量（插入／刪除／新增共用）：
  1. 小節數 = 原本 ± 改動數
  2. 沒被動到的小節**平移同一個量**
  3. 它們的長度不變
  4. 音符相對自己小節起點的位移不變

改拍號另外一組：
  1. 小節數不變
  2. 編輯點之前的小節完全不動
  3. 被改的那一小節長度 = 分子×4÷分母 個四分音符（照它自己的 BPM）
  4. 之後的小節長度不變

量穩定行為之前要先做一次**同值編輯暖身**：有些譜第一次編輯會觸發一次性校正
（`_resnap_time_sig_changes` 的 8ms 吸附），那是校正不是破壞。

**「小節」那一路有既有的偽陽性**（約 8 份）：它拿全域 BPM 當基準，而有些譜的
格線和 header 的 BPM 本來就不符（entrance、新手教學進階05~09、DF）。看的是
**前後的差**，不是絕對數字。
"""
from __future__ import annotations

import glob
import io
import os
import sys
import traceback

# 跑別的版本（例如 git worktree 裡的 HEAD）來做前後對照時設 QTE_ROOT
sys.path.insert(0, os.environ.get(
    'QTE_ROOT', os.path.dirname(os.path.abspath(__file__))))

from qt_editor.models import NoteModel     # noqa: E402

TOL = 2            # 毫秒容差：進位誤差不算錯

#: 預設拿哪些資料夾當語料。用 QTE_CORPUS（os.pathsep 分隔）覆寫。
DEFAULT_CORPUS = ('D:/Nostalgia/UserSongs', 'D:/Nostalgia/Nostalgia-clone')


def bars_of(m, limit=4000):
    out = []
    for i in range(min(m.count_measures(), limit)):
        s, e = m.get_measure_time_range(i)
        if s is None or e is None:
            break
        out.append((int(s), int(e)))
    return out


def note_offsets(m, bars):
    """每顆音符 →（第幾小節, 相對小節起點的毫秒）。"""
    out = {}
    for n in m.notes_tree:
        s = int(n.start)
        for bi, (bs, be) in enumerate(bars):
            if bs <= s < be:
                out[id(n)] = (bi, s - bs)
                break
    return out


def load(path):
    m = NoteModel()
    try:
        m.load_xml(path)
        if m.root is None or m.root.find('beat_data') is None:
            return None
        m.rebuild_display_cache()
    except Exception:          # Unity 的 link.xml 之類的非譜面檔
        return None
    return m


def warm_up(m):
    """同值編輯暖身：濾掉一次性校正。"""
    try:
        bars = bars_of(m)
        if not bars:
            return False
        i = min(2, len(bars) - 1)
        s, _e = bars[i]
        num = int(m.get_beats_per_bar_at_ms(s))
        den = int(m.time_sig_denominator or 4)
        for cms, _cn, cd in m.time_sig_changes:
            if cms <= s:
                den = int(cd)
            else:
                break
        m.set_measure_time_signature(i, num, den, uniform=True)
        m.rebuild_display_cache()
        return True
    except Exception:
        return False


# ── 四條路 ────────────────────────────────────────────────────────────

def check_shift_invariants(before, after, cut, added, notes_before, m):
    """插入／刪除／新增共用的檢查。cut = 改動點（小節序號），added = ±N。"""
    errs = []
    if len(after) != len(before) + added:
        errs.append('小節數 %d → %d（應 %d）'
                    % (len(before), len(after), len(before) + added))
        return errs
    # 改動點之前：完全不動
    for i in range(min(cut, len(before), len(after))):
        if abs(after[i][0] - before[i][0]) > TOL or abs(after[i][1] - before[i][1]) > TOL:
            errs.append('第%d小節被動到 %s → %s' % (i + 1, before[i], after[i]))
            break
    # 改動點之後：平移同一個量、長度不變
    shifts = set()
    for i in range(cut, len(before)):
        j = i + added
        if not (0 <= j < len(after)):
            continue
        shifts.add(after[j][0] - before[i][0])
        d_before = before[i][1] - before[i][0]
        d_after = after[j][1] - after[j][0]
        if abs(d_after - d_before) > TOL:
            errs.append('第%d小節長度 %d → %d' % (i + 1, d_before, d_after))
            break
    if len(shifts) > 1:
        lo, hi = min(shifts), max(shifts)
        if hi - lo > TOL:
            errs.append('後段位移不一致：%d 種（%d~%d）' % (len(shifts), lo, hi))
    # 音符相對自己小節起點不動
    notes_after = note_offsets(m, after)
    moved = 0
    for key, (bi, off) in notes_before.items():
        if key not in notes_after:
            moved += 1
            continue
        bj, off2 = notes_after[key]
        want = bi + added if bi >= cut else bi
        if bj != want or abs(off2 - off) > TOL:
            moved += 1
    if moved:
        errs.append('%d 顆音符換了小節或偏了位置' % moved)
    return errs


def path_insert(path):
    out = []
    for where in ('頭', '中', '尾'):
        m = load(path)
        if m is None:
            return out
        warm_up(m)
        before = bars_of(m)
        if len(before) < 12:
            return out
        cut = {'頭': 1, '中': len(before) // 2, '尾': len(before) - 3}[where]
        nb = note_offsets(m, before)
        try:
            m.insert_measure(cut, count=4)
        except Exception as exc:
            out.append((where, ['爆了：%s' % exc]))
            continue
        out.append((where, check_shift_invariants(before, bars_of(m), cut, 4, nb, m)))
    return out


def path_delete(path):
    out = []
    for where in ('頭', '中', '尾'):
        m = load(path)
        if m is None:
            return out
        warm_up(m)
        before = bars_of(m)
        if len(before) < 12:
            return out
        cut = {'頭': 1, '中': len(before) // 2, '尾': len(before) - 5}[where]
        nb = {k: v for k, v in note_offsets(m, before).items()
              if not (cut <= v[0] < cut + 2)}
        try:
            m.delete_measure(cut, count=2)
        except Exception as exc:
            out.append((where, ['爆了：%s' % exc]))
            continue
        after = bars_of(m)
        errs = []
        if len(after) != len(before) - 2:
            errs.append('小節數 %d → %d（應 %d）' % (len(before), len(after), len(before) - 2))
        else:
            for i in range(min(cut, len(after))):
                if abs(after[i][0] - before[i][0]) > TOL:
                    errs.append('第%d小節被動到' % (i + 1))
                    break
            shifts = set()
            for i in range(cut + 2, len(before)):
                j = i - 2
                if 0 <= j < len(after):
                    shifts.add(after[j][0] - before[i][0])
                    if abs((after[j][1] - after[j][0]) - (before[i][1] - before[i][0])) > TOL:
                        errs.append('第%d小節長度變了' % (i + 1))
                        break
            if len({s for s in shifts}) > 1 and max(shifts) - min(shifts) > TOL:
                errs.append('後段位移不一致：%d 種' % len(set(shifts)))
        out.append((where, errs))
    return out


def path_append(path):
    m = load(path)
    if m is None:
        return []
    warm_up(m)
    before = bars_of(m)
    if len(before) < 12:
        return []
    nb = note_offsets(m, before)
    try:
        for _ in range(4):
            m._add_measure_once(None)
        m.rebuild_display_cache()
    except Exception as exc:
        return [('末尾', ['爆了：%s' % exc])]
    after = bars_of(m)
    errs = []
    if len(after) != len(before) + 4:
        errs.append('小節數 %d → %d（應 %d）' % (len(before), len(after), len(before) + 4))
    for i in range(min(len(before), len(after))):
        if abs(after[i][0] - before[i][0]) > TOL or abs(after[i][1] - before[i][1]) > TOL:
            errs.append('第%d小節被動到 %s → %s' % (i + 1, before[i], after[i]))
            break
    na = note_offsets(m, after)
    moved = sum(1 for k, v in nb.items() if na.get(k) != v)
    if moved:
        errs.append('%d 顆音符偏了' % moved)
    return [('末尾', errs)]


def path_signature(path):
    out = []
    for num, den in ((3, 4), (6, 8), (2, 2)):
        m = load(path)
        if m is None:
            return out
        warm_up(m)
        before = bars_of(m)
        if len(before) < 12:
            return out
        cut = len(before) // 2
        try:
            bpm_here = float(m.get_measure_bpm(cut))
            m.set_measure_time_signature(cut, num, den, uniform=True)
            m.rebuild_display_cache()
        except Exception as exc:
            out.append(('%d/%d' % (num, den), ['爆了：%s' % exc]))
            continue
        after = bars_of(m)
        errs = []
        if len(after) != len(before):
            errs.append('小節數 %d → %d' % (len(before), len(after)))
        else:
            for i in range(cut):
                if abs(after[i][0] - before[i][0]) > TOL or abs(after[i][1] - before[i][1]) > TOL:
                    errs.append('第%d小節被動到 %s → %s' % (i + 1, before[i], after[i]))
                    break
            want = num * 4.0 * 60000.0 / (den * max(1.0, bpm_here))
            got = after[cut][1] - after[cut][0]
            if abs(got - want) > max(3, want * 0.02):
                errs.append('被改的小節 %d ms，應 %.0f ms（%d/%d @ %.1fBPM）'
                            % (got, want, num, den, bpm_here))
            for i in range(cut + 1, len(before)):
                d0 = before[i][1] - before[i][0]
                d1 = after[i][1] - after[i][0]
                if abs(d1 - d0) > TOL:
                    errs.append('第%d小節長度 %d → %d' % (i + 1, d0, d1))
                    break
        out.append(('%d/%d' % (num, den), errs))
    return out


def path_barlen(path):
    """使用者報的那個：小節長度對不對，以及裡面有幾條拍線。

    **不能拿 `get_measure_bpm` 當基準**——那個值是從小節長度反推的，小節被切半
    時它也跟著變，自己跟自己永遠一致（我第一版就是這樣，什麼都抓不到）。

    獨立的基準：拍點間距**完全均勻**的譜（全曲庫一半以上是這樣），一小節應該是
    `分子×4÷分母` 個四分音符，四分音符長度由全域 BPM 給。間距不均勻的（Winter
    Wind 那種彈性速度）跳過，不然會誤報。

    拍線數另外報：一小節該有「分子」條。這個對「一筆不是一拍」的譜（半小節一筆
    的 anima-xi）會低報，所以只看**比例一致性**——同一份譜裡每個小節的
    「筆數 ÷ 分子」要一樣。
    """
    m = load(path)
    if m is None:
        return []
    beats = m.get_beat_entries()
    if len(beats) < 8 or float(m.bpm or 0) <= 0:
        return []
    steps = [beats[i + 1][1] - beats[i][1] for i in range(len(beats) - 1)
             if beats[i + 1][1] > beats[i][1]]
    if not steps:
        return []
    uniform = (max(steps) - min(steps)) <= max(2, 0.02 * max(steps))
    if not uniform:
        return []                      # 彈性速度的譜不在這條的射程內
    quarter = 60000.0 / float(m.bpm)
    nbars = min(m.count_measures(), 3000)
    bad_len, ratios = None, {}
    for bi in range(nbars - 1):        # 最後一小節的終點是收尾標記，跳過
        s, e = m.get_measure_time_range(bi)
        if s is None or e is None or e <= s:
            break
        num = int(m.get_beats_per_bar_at_ms(s))
        den = int(m.time_sig_denominator or 4)
        for cms, _cn, cd in m.time_sig_changes:
            if cms <= s:
                den = int(cd)
            else:
                break
        want = num * 4.0 / den * quarter
        if bad_len is None and abs((e - s) - want) > max(3.0, want * 0.03):
            bad_len = ('第%d小節 %d/%d 長度 %d ms，應 %.0f ms（%.1fBPM）'
                       % (bi + 1, num, den, e - s, want, m.bpm))
        inside = sum(1 for _i, b in beats if s <= b < e)
        ratios.setdefault(round(inside / float(max(1, num)), 3), []).append(bi + 1)
    bad_ratio = None
    if len(ratios) > 1:
        rows = sorted(ratios.items(), key=lambda kv: -len(kv[1]))
        odd = rows[1]
        bad_ratio = ('拍線/分子 有 %d 種：主流 %.3f，第%d小節是 %.3f'
                     % (len(ratios), rows[0][0], odd[1][0], odd[0]))
    return [('小節長度', [bad_len] if bad_len else []),
            ('拍線一致', [bad_ratio] if bad_ratio else [])]


PATHS = (('插入', path_insert), ('刪除', path_delete),
         ('新增', path_append), ('改拍號', path_signature),
         ("小節", path_barlen))


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else 'run'
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    roots = os.environ.get('QTE_CORPUS', '').split(os.pathsep)
    roots = [r for r in roots if r] or list(DEFAULT_CORPUS)
    files = []
    for root in roots:
        files += glob.glob(os.path.join(root, '**', '*.xml'), recursive=True)
    seen, uniq = set(), []
    for p in sorted(files):
        key = os.path.basename(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    # 先濾掉非譜面的 XML（Nostalgia-clone 裡有 Unity 的 link.xml 之類）
    uniq = [p for p in uniq if load(p) is not None]
    if limit:
        uniq = uniq[:limit]

    here = os.path.dirname(os.path.abspath(__file__))
    log = io.open(os.path.join(here, 'audit-%s.txt' % label), 'w', encoding='utf-8')

    def say(t):
        log.write(t + chr(10))
        log.flush()

    say('稽核 %d 份譜（去重後）' % len(uniq))
    totals = {name: 0 for name, _fn in PATHS}
    checks = {name: 0 for name, _fn in PATHS}
    details = {name: [] for name, _fn in PATHS}
    for path in uniq:
        for name, fn in PATHS:
            try:
                rows = fn(path)
            except Exception:
                rows = [('例外', [traceback.format_exc(limit=1).strip().splitlines()[-1]])]
            for where, errs in rows:
                checks[name] += 1
                if errs:
                    totals[name] += 1
                    details[name].append((os.path.basename(path), where, errs[0]))
    say('')
    say('| 路徑 | 壞 / 檢查 |')
    say('|---|---|')
    for name, _fn in PATHS:
        say('| %s | **%d** / %d |' % (name, totals[name], checks[name]))
    for name, _fn in PATHS:
        say('')
        say('=== %s（前 25 筆）===' % name)
        for fname, where, err in details[name][:25]:
            say('  %-40s [%s] %s' % (fname[:40], where, err))
    log.close()
    print('完成：audit-%s.txt' % label)


if __name__ == '__main__':
    main()
