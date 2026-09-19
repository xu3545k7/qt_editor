#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precompute the song-select difficulty analysis for every chart.

The game shows a chart's note count, density profile and technique tags when the
player hovers its difficulty bookmark.  Deriving that at hover time means reading
a ~700 KB chart, which is far too slow to feel instant, so this writes the answer
next to each chart as ``<chart>.analysis.json`` and the game just reads it.

    python qt_editor/build_chart_analysis.py            # only what is missing or stale
    python qt_editor/build_chart_analysis.py --force    # rebuild everything
    python qt_editor/build_chart_analysis.py <root>     # a different library root

The metrics here must stay in step with ``ChartAnalysisCache`` in
``Assets/Scripts/ChartManange/ChartAnalysis.cs``, which computes the same numbers
as a fallback for charts that have no cache yet.  The thresholds the tags use
were measured from this library's charts; see the C# side for those.
"""

import argparse
import io
import json
import math
import os
import sys

CACHE_VERSION = 10
BUCKET_COUNT = 48
SECTION_SECONDS = 12.0
# 和整首的耐力用同一條線：每秒 12 顆音以上算「忙」。
BUSY_DENSITY = 12.0
SKIP_NAMES = {"register.json", "library.json", "songlist.json", "song_list.json"}


def iter_chart_files(root):
    for folder, _, names in os.walk(root):
        for name in names:
            if not name.endswith(".json"):
                continue
            if name in SKIP_NAMES or name.endswith(".analysis.json"):
                continue
            yield os.path.join(folder, name)


def cache_path_for(chart_path):
    stem = chart_path[:-5] if chart_path.endswith(".json") else chart_path
    return stem + ".analysis.json"


def note_pitch(note):
    """MIDI pitch, falling back to the legacy 1..88 keyboard index."""
    pitch = note.get("pitch") or 0
    if pitch <= 0:
        scale = note.get("scale_piano") or 0
        if scale > 0:
            pitch = scale + 20
    return pitch


def build_onsets(notes):
    """Per hand, one entry per start time holding that hand's lowest and highest note."""
    hands = ({}, {})
    pitched = 0
    for note in notes:
        pitch = note_pitch(note)
        if pitch <= 0:
            continue
        pitched += 1
        table = hands[0] if (note.get("hand") or 0) == 0 else hands[1]
        time = note.get("startTime") or 0
        entry = table.get(time)
        if entry is None:
            table[time] = [pitch, pitch]
        else:
            if pitch < entry[0]:
                entry[0] = pitch
            if pitch > entry[1]:
                entry[1] = pitch
    return [sorted((t, v[0], v[1]) for t, v in table.items()) for table in hands], pitched


def centre(onset):
    return (onset[1] + onset[2]) * 0.5


def count_leaps(seq):
    """Quick same-hand moves that jump or reach an octave."""
    leaps = 0
    moves = 0
    for i in range(1, len(seq)):
        if seq[i][0] - seq[i - 1][0] > 250:
            continue
        moves += 1
        if abs(centre(seq[i]) - centre(seq[i - 1])) >= 12 or seq[i][2] - seq[i][1] >= 12:
            leaps += 1
    return leaps, moves


def count_arpeggio_notes(seq):
    """Four or more chord-tone steps in one direction: a broken chord."""
    total = 0
    i = 0
    while i < len(seq) - 1:
        j = i
        direction = 0
        length = 1
        while j < len(seq) - 1:
            if seq[j + 1][0] - seq[j][0] > 200:
                break
            interval = centre(seq[j + 1]) - centre(seq[j])
            if not 2 <= abs(interval) <= 7:
                break
            step = 1 if interval > 0 else -1
            if direction == 0:
                direction = step
            elif step != direction:
                break
            j += 1
            length += 1
        if length >= 4:
            total += length
        i = max(j, i + 1)
    return total


def count_finger_run_notes(seq, gap=110, minimum=5):
    """Near-stepwise notes in a row: a scale or a finger passage.

    ``gap`` is the most time allowed between notes and ``minimum`` the shortest
    run that counts, so the same walk serves both the ordinary passage and the
    high-speed one -- 110ms is about nine notes a second, 70ms about fourteen.
    """
    total = 0
    i = 0
    while i < len(seq) - 1:
        j = i
        length = 1
        while j < len(seq) - 1:
            if seq[j + 1][0] - seq[j][0] > gap:
                break
            if abs(centre(seq[j + 1]) - centre(seq[j])) > 4:
                break
            j += 1
            length += 1
        if length >= minimum:
            total += length
        i = max(j, i + 1)
    return total


def count_thick_run_onsets(seq):
    """Chords sitting inside a fast run: doubling that never slows down.

    Playing 1-5 and 3 together in the middle of a scale or an arpeggio, at the
    same tempo as the single notes around it, is a different job from either the
    run or the chord alone -- the hand has to keep the passage moving while some
    fingers are committed.  Counted as the doubled onsets inside runs that
    already qualify as fast passagework.
    """
    total = 0
    i = 0
    while i < len(seq) - 1:
        j = i
        length = 1
        while j < len(seq) - 1:
            if seq[j + 1][0] - seq[j][0] > 110:
                break
            if abs(centre(seq[j + 1]) - centre(seq[j])) > 4:
                break
            j += 1
            length += 1
        if length >= 5:
            for k in range(i, j + 1):
                if seq[k][2] > seq[k][1]:
                    total += 1
        i = max(j, i + 1)
    return total


def count_trill_notes(seq):
    """Six or more fast alternations between the same two pitches."""
    total = 0
    i = 0
    while i < len(seq) - 2:
        j = i
        length = 1
        while j < len(seq) - 2:
            if seq[j + 1][0] - seq[j][0] > 140:
                break
            if seq[j + 2][1] != seq[j][1] or seq[j + 2][2] != seq[j][2]:
                break
            if seq[j + 1][1] == seq[j][1]:
                break
            j += 1
            length += 1
        if length >= 6:
            total += length
        i = max(j, i + 1)
    return total


def measure_independence(notes):
    """One finger holding while the others keep moving.

    A note only counts when a note of the same hand that started strictly
    earlier is still sounding, so the members of a block chord do not count
    each other.  Uses note lengths only, so it works on charts without pitch.
    """
    hands = ([], [])
    for note in notes:
        start = note.get("startTime") or 0
        end = max(note.get("endTime") or 0, start)
        hands[0 if (note.get("hand") or 0) == 0 else 1].append((start, end))

    overlapping = 0
    total = 0
    for hand in hands:
        hand.sort()
        latest_end = None
        cursor = 0
        for start, _end in hand:
            total += 1
            while cursor < len(hand) and hand[cursor][0] < start:
                if latest_end is None or hand[cursor][1] > latest_end:
                    latest_end = hand[cursor][1]
                cursor += 1
            # 30ms of slack: a chord released a hair late is not independence.
            if latest_end is not None and latest_end > start + 30:
                overlapping += 1
    return (overlapping / total) if total else 0.0


def count_repeated_notes(seq):
    """Four or more strikes of the same single pitch in quick succession."""
    total = 0
    i = 0
    while i < len(seq) - 1:
        j = i
        length = 1
        while j < len(seq) - 1:
            if seq[j + 1][0] - seq[j][0] > 160:
                break
            if seq[j][1] != seq[j][2]:
                break
            if seq[j + 1][1] != seq[j][1] or seq[j + 1][2] != seq[j][2]:
                break
            j += 1
            length += 1
        if length >= 4:
            total += length
        i = max(j, i + 1)
    return total


def measure_peak_hand_rate(notes):
    """Busiest second of a single hand, counted in strikes rather than notes."""
    hands = (set(), set())
    for note in notes:
        hands[0 if (note.get("hand") or 0) == 0 else 1].add(note.get("startTime") or 0)

    peak = 0
    for hand in hands:
        onsets = sorted(hand)
        start = 0
        for i, time in enumerate(onsets):
            while time - onsets[start] > 1000:
                start += 1
            peak = max(peak, i - start + 1)
    return peak


def measure_peak_chord_rate(notes):
    """Busiest second of a single hand counted in notes, chord members included.

    ``measure_peak_hand_rate`` counts strikes, so a hand hammering four-note
    chords and a hand playing single notes at the same tempo score the same --
    which is right for "how fast are the hands moving" and wrong for "how much
    is the hand being asked to do".  Notes per onset alone has the opposite
    fault: a slow chorale of six-note chords tops it while being easy to play.
    This is the pair of them at once, and it is what the radar plots.
    """
    hands = ([], [])
    for note in notes:
        hands[0 if (note.get("hand") or 0) == 0 else 1].append(note.get("startTime") or 0)

    peak = 0
    for hand in hands:
        hand.sort()
        start = 0
        for i, time in enumerate(hand):
            while time - hand[start] > 1000:
                start += 1
            peak = max(peak, i - start + 1)
    return peak


def count_octave_run_onsets(seq):
    """Three or more octaves in a row taken at speed: an octave passage."""
    total = 0
    i = 0
    while i < len(seq) - 1:
        j = i
        length = 1
        while j < len(seq) - 1:
            if seq[j + 1][0] - seq[j][0] > 180:
                break
            if seq[j][2] - seq[j][1] != 12 or seq[j + 1][2] - seq[j + 1][1] != 12:
                break
            j += 1
            length += 1
        if length >= 3 and seq[i][2] - seq[i][1] == 12:
            total += length
        i = max(j, i + 1)
    return total


def count_fast_leaps(seq):
    """Octave jumps taken inside 120ms.

    Counted, not divided: the caller measures these against *every* move, the
    same denominator ``count_leaps`` uses.  Dividing by "moves that fast"
    instead made this a conditional probability -- among the moves you happen to
    take quickly, how many were leaps -- so a sparse chart with eleven fast
    moves, three of them jumps, scored higher than a chart that leaps at speed
    all the way through.  It also meant the fast ratio could exceed the plain
    one, which made the pair read as unrelated measures rather than as a subset.
    """
    leaps = 0
    for i in range(1, len(seq)):
        if seq[i][0] - seq[i - 1][0] > 120:
            continue
        if abs(centre(seq[i]) - centre(seq[i - 1])) >= 12:
            leaps += 1
    return leaps


def measure_hand_crossing(right, left):
    """Moments both hands play where the left hand sits entirely above the right."""
    together = 0
    crossed = 0
    right_by_time = {t: (lo, hi) for t, lo, hi in right}
    for time, low, _high in left:
        other = right_by_time.get(time)
        if other is None:
            continue
        together += 1
        if low > other[1]:
            crossed += 1
    return (crossed / together) if together else 0.0


EXCERPT_NOTES = 42


def build_excerpt(notes, times, span_ms):
    """A real passage from the piece, for the book to engrave on its pages.

    Taken from the busiest stretch rather than the opening: intros are often a
    few sparse notes, and a page showing four notes says nothing about the piece.
    Stored as [start_ms, pitch, hand] so the game can lay it out without
    reopening the chart -- the whole point of this cache.
    """
    if not notes or span_ms <= 0:
        return []

    buckets = [0] * BUCKET_COUNT
    for time in times:
        buckets[min(BUCKET_COUNT - 1, max(0, int(time / span_ms * BUCKET_COUNT)))] += 1
    peak = buckets.index(max(buckets))
    start_ms = peak / BUCKET_COUNT * span_ms

    picked = []
    for note in notes:
        if (note.get("startTime") or 0) < start_ms:
            continue
        pitch = note_pitch(note)
        if pitch <= 0:
            continue
        picked.append([note.get("startTime") or 0, pitch, 0 if (note.get("hand") or 0) == 0 else 1])
        if len(picked) >= EXCERPT_NOTES:
            break
    return picked


def measure_bpm_curve(chart, span_ms):
    """The tempo across the piece, one value per density bucket.

    ``beat_timings`` means one of two things in this library -- a line per bar in
    some charts and a line per quarter in others -- so the spacing alone does not
    give a tempo.  The test is the one the pedal generator already uses: compare
    the median spacing with a quarter at the declared bpm, and if it is much
    wider the lines are bars, in which case one line spans
    ``numerator * 4 / denominator`` beats.

    Guarded at both ends: a couple of charts carry a degenerate one millisecond
    gap, which reads as 180000 bpm and would flatten every real change to nothing
    once the graph scaled to fit it.
    """
    marks = chart.get("beat_timings") or []
    declared = chart.get("bpm") or chart.get("first_bpm") or 0
    if len(marks) < 4 or span_ms <= 0:
        return []

    gaps = [marks[i + 1] - marks[i] for i in range(len(marks) - 1) if marks[i + 1] > marks[i]]
    if not gaps:
        return []

    # 一條線代表幾拍。宣告的 bpm 和間距中位數本來就決定了答案
    # （bpm = 60000 × 拍數 ÷ 間距），但那要宣告值是對的才成立。
    #
    # 格線的拍數只會是幾個有音樂意義的值，所以算完之後**貼到最近的那一個**；
    # 貼不上去（誤差太大）就代表宣告值本身不可信 —— 初音ミクの消失 標的是
    # bpm 75，格線卻是整齊的 500ms，算出來 0.625 拍，離任何一個合理值都有
    # 25%。那張譜的正解是一條線一個四分音符、120 bpm，宣告的 75 是錯的。
    #
    # 不可信的時候退回「一條線一拍」，再用 40–300 的合理區間把它乘除 2 拉回來。
    median = float(sorted(gaps)[len(gaps) // 2])
    beats = 1.0
    if declared and median > 0:
        estimated = float(declared) * median / 60000.0
        # 貼到最近的 0.5 倍數。**不要**貼到「音樂上合理」的那幾個值：格線本身
        # 可能是不規則的（混著小節線和拍線），此時中位數會落在 2.466 這種地方，
        # 而它離 2.5 只有 1.4% —— 硬貼到 3.0 會把整條曲線拉高兩成。
        beats = max(0.5, min(16.0, round(estimated * 2.0) / 2.0))
        # 貼不上去才代表宣告值本身不可信。初音ミクの消失 標 bpm 75，格線卻是
        # 整齊的 500ms，算出來 0.625 拍 —— 離 0.5 有 25%。那張譜的正解是一條線
        # 一個四分音符、120 bpm，宣告的 75 是錯的。這種就退回「一條線一拍」。
        # 門檻 18%。實測這條線兩邊分得很開：貼得上去的那些誤差最多 12.5%
        # （1.333→1.5、1.777→2.0，都是格線不是宣告拍的整數倍而已），而宣告值
        # 真的錯掉的初音ミクの消失 是 25%。
        if abs(math.log(estimated / beats)) > math.log(1.18):
            beats = 1.0

    # 落在合理區間之外就整體乘除 2，直到回來為止。格線抓錯一個八度是最常見的
    # 錯法，而 40–300 之外的鋼琴譜面速度實務上不存在。
    for _ in range(4):
        tempo = 60000.0 * beats / median
        if tempo < 40.0:
            beats *= 2.0
        elif tempo > 300.0:
            beats /= 2.0
        else:
            break

    buckets = [[] for _ in range(BUCKET_COUNT)]
    for i in range(len(marks) - 1):
        gap = marks[i + 1] - marks[i]
        if gap <= 1:
            continue
        local = 60000.0 * beats / gap
        if not 20.0 <= local <= 400.0:
            continue
        middle = (marks[i] + marks[i + 1]) * 0.5
        index = min(BUCKET_COUNT - 1, max(0, int(middle / span_ms * BUCKET_COUNT)))
        buckets[index].append(local)

    curve = []
    carried = float(declared) if declared else 0.0
    for bucket in buckets:
        if bucket:
            carried = sum(bucket) / len(bucket)
        # 空的格子沿用上一個值，不要掉到 0：換譜面速度的地方本來就沒有小節線。
        curve.append(round(carried, 2))
    return curve if any(curve) else []


def section_busy_fraction(times, span_seconds):
    """How much of a stretch is spent above the busy line."""
    if span_seconds <= 0 or not times:
        return 0.0
    origin = min(times)
    seconds = {}
    for time in times:
        index = int((time - origin) // 1000)
        seconds[index] = seconds.get(index, 0) + 1
    total = max(1, int(span_seconds + 0.999))
    busy = sum(1 for i in range(total) if seconds.get(i, 0) >= BUSY_DENSITY)
    return busy / total


def section_metrics(notes, projection_seconds):
    """The radar's measures for one stretch of a chart.

    Everything the radar plots is intensive -- rates and ratios -- except
    stamina, which is a count of seconds and so can never reach a chart-scale
    threshold inside a twelve second window.  That one is projected: if the whole
    piece carried on like this stretch, how many busy seconds would it have.
    Read that way the section radar answers "what if the whole chart were this
    part", which is the comparison worth drawing over the chart's own shape.
    """
    if not notes:
        return None

    times = [n.get("startTime") or 0 for n in notes]
    span = max(0.5, (max(times) - min(times)) / 1000.0)
    holds = sum(1 for n in notes if (n.get("note_type") or 0) & 0x02)

    metrics = {
        "note_count": len(notes),
        "hold_count": holds,
        "peak_hand_rate": measure_peak_hand_rate(notes),
        "peak_chord_rate": measure_peak_chord_rate(notes),
        "sustained_seconds": round(
            section_busy_fraction(times, span) * projection_seconds, 3),
        "fast_leap_ratio": 0.0,
        "octave_run_ratio": 0.0,
        "hand_cross_ratio": 0.0,
        "finger_run_ratio": 0.0,
        "arpeggio_ratio": 0.0,
        "trill_ratio": 0.0,
        "repeat_ratio": 0.0,
        "thick_run_ratio": 0.0,
    }

    pitched = sum(1 for n in notes if note_pitch(n) > 0)
    if pitched < len(notes) / 2:
        return metrics

    (right_seq, left_seq), _ = build_onsets(notes)
    fast_leaps = moves = octave_runs = 0
    arpeggio = finger = thick = trill = repeated = 0
    for seq in (right_seq, left_seq):
        _seq_leaps, seq_moves = count_leaps(seq)
        moves += seq_moves
        fast_leaps += count_fast_leaps(seq)
        octave_runs += count_octave_run_onsets(seq)
        arpeggio += count_arpeggio_notes(seq)
        finger += count_finger_run_notes(seq)
        thick += count_thick_run_onsets(seq)
        trill += count_trill_notes(seq)
        repeated += count_repeated_notes(seq)

    metrics.update({
        "fast_leap_ratio": round(fast_leaps / moves, 4) if moves else 0.0,
        "octave_run_ratio": round(octave_runs / len(notes), 4),
        "hand_cross_ratio": round(measure_hand_crossing(right_seq, left_seq), 4),
        "finger_run_ratio": round(finger / len(notes), 4),
        "arpeggio_ratio": round(arpeggio / len(notes), 4),
        "trill_ratio": round(trill / len(notes), 4),
        "repeat_ratio": round(repeated / len(notes), 4),
        "thick_run_ratio": round(thick / len(notes), 4),
    })
    return metrics


def pick_sections(notes, times, length_seconds):
    """The busiest and the quietest twelve seconds worth drawing.

    Windows with almost nothing in them are dropped first: every piece has an
    intro or a fade, and the quietest stretch of a chart is otherwise always
    silence, which draws as a dot and says nothing about the writing.
    """
    window = SECTION_SECONDS * 1000.0
    buckets = {}
    for note, time in zip(notes, times):
        buckets.setdefault(int(time // window), []).append(note)

    # 24 顆是一個下限：再稀疏的話，一段跑動就能把比例撐到滿分，因為分母太小。
    usable = [group for group in buckets.values() if len(group) >= 24]
    if len(usable) < 2:
        return None, None

    usable.sort(key=len)
    return (section_metrics(usable[-1], length_seconds),
            section_metrics(usable[0], length_seconds))


def analyse(chart):
    # 隱藏音符不算。它們在遊戲裡沒有按鍵、只會由寄主的 sub_note 發聲，
    # 算進去的話難度分析（雷達圖）就是拿完整的事件集去算——同一首歌的
    # normal 和 real 會長得一模一樣，因為兩者的事件集本來就相同。
    notes = [n for n in (chart.get("notes") or [])
             if not (isinstance(n, dict) and n.get("hidden"))]
    if not notes:
        return None

    times = [n.get("startTime") or 0 for n in notes]
    span_ms = max(chart.get("music_finish_time_msec") or 0, max(times))
    if span_ms <= 0:
        return None
    length_seconds = span_ms / 1000.0

    hold = sum(1 for n in notes if (n.get("note_type") or 0) & 0x02)
    slide = sum(1 for n in notes if (n.get("note_type") or 0) & 0x04)
    trill_flag = sum(1 for n in notes if (n.get("note_type") or 0) & 0x40)
    right_hand = sum(1 for n in notes if (n.get("hand") or 0) == 0)

    pitches = [p for p in (note_pitch(n) for n in notes) if p > 0]

    # Chord runs and distinct onsets, in one pass over the time-ordered list.
    chord_notes = 0
    onsets = 0
    run = 1
    for i in range(1, len(times) + 1):
        if i < len(times) and times[i] == times[i - 1]:
            run += 1
            continue
        if run >= 2:
            chord_notes += run
        onsets += 1
        run = 1

    right_density = [0.0] * BUCKET_COUNT
    left_density = [0.0] * BUCKET_COUNT
    for note, time in zip(notes, times):
        bucket = min(BUCKET_COUNT - 1, max(0, int(time / span_ms * BUCKET_COUNT)))
        if (note.get("hand") or 0) == 0:
            right_density[bucket] += 1.0
        else:
            left_density[bucket] += 1.0

    bucket_seconds = max(0.001, length_seconds / BUCKET_COUNT)
    peak_bucket = 0.0
    sustained = 0.0
    for i in range(BUCKET_COUNT):
        right_density[i] /= bucket_seconds
        left_density[i] /= bucket_seconds
        total_density = right_density[i] + left_density[i]
        peak_bucket = max(peak_bucket, total_density)
        # Endurance is time spent busy, not average business.
        if total_density >= 12.0:
            sustained += bucket_seconds

    # Busiest one-second window.
    peak_density = 0
    start = 0
    for i, time in enumerate(times):
        while time - times[start] > 1000:
            start += 1
        peak_density = max(peak_density, i - start + 1)

    result = {
        "version": CACHE_VERSION,
        "note_count": len(notes),
        "hold_count": hold,
        "slide_count": slide,
        "trill_count": trill_flag,
        "chord_note_count": chord_notes,
        "onset_count": onsets,
        "right_hand_count": right_hand,
        "left_hand_count": len(notes) - right_hand,
        "pitched_note_count": len(pitches),
        "lowest_pitch": min(pitches) if pitches else 0,
        "highest_pitch": max(pitches) if pitches else 0,
        "length_seconds": round(length_seconds, 3),
        "average_density": round(len(notes) / max(0.001, length_seconds), 4),
        "peak_density": peak_density,
        "peak_bucket_total": round(peak_bucket, 4),
        "right_density": [round(v, 3) for v in right_density],
        "left_density": [round(v, 3) for v in left_density],
        "excerpt": build_excerpt(notes, times, span_ms),
        "bpm_curve": measure_bpm_curve(chart, span_ms),
        "peak_section": None,
        "quiet_section": None,
        "sustained_seconds": round(sustained, 3),
        "peak_hand_rate": measure_peak_hand_rate(notes),
        "peak_chord_rate": measure_peak_chord_rate(notes),
        "octave_run_ratio": 0.0,
        "fast_leap_ratio": 0.0,
        "fast_run_ratio": 0.0,
        "thick_run_ratio": 0.0,
        "independence_ratio": round(measure_independence(notes), 4),
        "has_technique": False,
        "octave_ratio": 0.0,
        "repeat_ratio": 0.0,
        "leap_ratio": 0.0,
        "arpeggio_ratio": 0.0,
        "finger_run_ratio": 0.0,
        "trill_ratio": 0.0,
        "hand_cross_ratio": 0.0,
    }

    peak_section, quiet_section = pick_sections(notes, times, length_seconds)
    result["peak_section"] = peak_section
    result["quiet_section"] = quiet_section

    # Below half coverage the pitch line is too patchy to draw conclusions from.
    if len(pitches) < len(notes) / 2:
        return result

    (right_seq, left_seq), _ = build_onsets(notes)
    leaps = moves = arpeggio = finger = fast_finger = thick_run = trill = 0
    octaves = onsets = repeated = 0
    octave_runs = fast_leaps = 0
    for seq in (right_seq, left_seq):
        seq_leaps, seq_moves = count_leaps(seq)
        leaps += seq_leaps
        moves += seq_moves
        arpeggio += count_arpeggio_notes(seq)
        finger += count_finger_run_notes(seq)
        fast_finger += count_finger_run_notes(seq, gap=70, minimum=6)
        thick_run += count_thick_run_onsets(seq)
        trill += count_trill_notes(seq)
        onsets += len(seq)
        octaves += sum(1 for _t, low, high in seq if high - low == 12)
        repeated += count_repeated_notes(seq)
        octave_runs += count_octave_run_onsets(seq)
        fast_leaps += count_fast_leaps(seq)

    result.update({
        "has_technique": True,
        "octave_ratio": round(octaves / onsets, 4) if onsets else 0.0,
        "octave_run_ratio": round(octave_runs / len(notes), 4),
        "fast_leap_ratio": round(fast_leaps / moves, 4) if moves else 0.0,
        "fast_run_ratio": round(fast_finger / len(notes), 4),
        "thick_run_ratio": round(thick_run / len(notes), 4),
        "repeat_ratio": round(repeated / len(notes), 4),
        "leap_ratio": round(leaps / moves, 4) if moves else 0.0,
        "arpeggio_ratio": round(arpeggio / len(notes), 4),
        "finger_run_ratio": round(finger / len(notes), 4),
        "trill_ratio": round(trill / len(notes), 4),
        "hand_cross_ratio": round(measure_hand_crossing(right_seq, left_seq), 4),
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", nargs="?", default=None,
                        help="library root to scan (default: Nostalgia-clone/UserSongs)")
    parser.add_argument("--force", action="store_true",
                        help="rebuild even when the cache is already current")
    args = parser.parse_args()

    root = args.root
    if root is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(os.path.dirname(here), "Nostalgia-clone", "UserSongs")
    if not os.path.isdir(root):
        sys.exit("no such library root: " + root)

    written = skipped = current = failed = 0
    for chart_path in iter_chart_files(root):
        source_bytes = os.path.getsize(chart_path)
        cache_path = cache_path_for(chart_path)

        if not args.force and os.path.exists(cache_path):
            try:
                existing = json.load(io.open(cache_path, encoding="utf-8"))
                if (existing.get("version") == CACHE_VERSION and
                        existing.get("source_bytes") == source_bytes):
                    current += 1
                    continue
            except Exception:
                pass

        try:
            chart = json.load(io.open(chart_path, encoding="utf-8-sig"))
        except Exception:
            skipped += 1
            continue
        if not isinstance(chart, dict) or "notes" not in chart:
            skipped += 1
            continue

        try:
            result = analyse(chart)
        except Exception as error:
            print("  FAILED %s: %s" % (chart_path, error))
            failed += 1
            continue
        if result is None:
            skipped += 1
            continue

        result["source_bytes"] = source_bytes
        with io.open(cache_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
        written += 1

    print("wrote %d, already current %d, not a chart %d, failed %d" %
          (written, current, skipped, failed))


if __name__ == "__main__":
    main()
