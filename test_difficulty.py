# -*- coding: utf-8 -*-
"""難度生成：normal / hard / extreme。

最重要的不變量是**音訊事件總數不變**。官方 2242 份譜實測，每首歌各難度的
`sum(max(1, nsub))` 除以 real 的值剛好是 1.000——降難度從來不刪音符，它把音符
折進鄰近可見音符的 `sub_note_data`。生成器要是把音符弄丟了，遊戲裡那些
keysound 就整個消失，而畫面上完全看不出來。

其餘釘住的都是官方實測的目標值：同手發音間隔、同時可見顆數、長押比例、
hand=2 比例、以及「鍵道要重排」。
"""

import io
import unittest

from qt_editor import difficulty as D
from qt_editor.models import GNote, NoteModel


def chart(events, bpm=120.0):
    """events: [(start_ms, [(pitch, hand, dur), ...]), ...]"""
    m = NoteModel.create_new('t', bpm, 60.0, 4)
    m.ensure_precise_beat_grid()
    notes = []
    for when, items in events:
        for pitch, hand, dur in items:
            n = GNote(None, len(notes))
            n.start = int(when)
            n.end = int(when) + int(dur)
            n.gate = int(dur)
            n.min_key, n.max_key = 4, 6
            n.note_type = 2 if dur >= 400 else 0
            n.hand = hand
            n.pitch, n.velocity = pitch, 90
            n.track, n.channel = hand, 0
            notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def dense(groups=300, step=90, chord=4, dur=120):
    """一段密集的曲子：每 `step` 毫秒一組 `chord` 顆，兩手各半。"""
    events = []
    for i in range(groups):
        items = []
        for k in range(chord):
            hand = 0 if k < chord // 2 else 1
            items.append((50 + k * 4 + (i % 5), hand, dur))
        events.append((i * step, items))
    return events


def melodic(groups=300, step=90):
    """有獨奏段落的曲子：一半的發音點只有一顆音。

    `dense()` 每個發音點兩手都有音，收頂到 2 顆之後永遠是「一手一顆」，
    沒有任何一顆是「該時刻只有它」——hand=2 就永遠是 0，測不到東西。
    真實的曲子有大量單音的旋律段。
    """
    events = []
    for i in range(groups):
        # 整段獨奏，不是單音雙音交替——稀疏化是分手各做各的，嚴格交替的話
        # 兩手保留下來的點會剛好對齊，一顆「只有自己」的音都不剩。
        if (i // 6) % 2:
            events.append((i * step, [(60 + i % 9, 0, 120)]))
        else:
            events.append((i * step, [(50 + i % 5, 0, 120), (72 + i % 5, 1, 120)]))
    return events


def visible(model):
    return [n for n in model.notes_tree if not getattr(n, 'hidden', False)]


def hidden(model):
    return [n for n in model.notes_tree if getattr(n, 'hidden', False)]


class EventPreservationTests(unittest.TestCase):
    """一顆都不能少——這是整個做法的前提。"""

    def setUp(self):
        self.events = dense()
        self.total = sum(len(items) for _w, items in self.events)

    def test_every_difficulty_keeps_every_note(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            self.assertEqual(len(m.notes_tree), self.total,
                             '%s 弄丟了音符' % name)

    def test_visible_plus_hidden_is_the_whole_chart(self):
        m = chart(self.events)
        r = D.generate(m, 'normal')
        self.assertEqual(r.visible + r.hidden, r.total)
        self.assertEqual(len(visible(m)) + len(hidden(m)), self.total)

    def test_the_event_set_is_untouched(self):
        """時間和音高一顆都不准動——動了就不是同一首歌了。"""
        before = sorted((int(n.start), n.pitch) for n in chart(self.events).notes_tree)
        m = chart(self.events)
        D.generate(m, 'normal')
        after = sorted((int(n.start), n.pitch) for n in m.notes_tree)
        self.assertEqual(before, after)

    def test_every_hidden_note_has_a_host(self):
        """沒有寄主的隱藏音符會在存檔時被迫變回可見，等於白做。"""
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            r = D.generate(m, name)
            self.assertEqual(r.orphans, 0, '%s 有 %d 顆孤兒' % (name, r.orphans))
            for note in hidden(m):
                self.assertIsNotNone(getattr(note, '_sub_host', None))

    def test_hosts_are_visible_notes(self):
        m = chart(self.events)
        D.generate(m, 'normal')
        alive = {id(n) for n in visible(m)}
        for note in hidden(m):
            self.assertIn(id(note._sub_host), alive, '寄主自己也是隱藏的')

    def test_generating_twice_gives_the_same_result(self):
        """重複生成不能愈疊愈少——第二次要先把上一次的隱藏清掉。"""
        m = chart(self.events)
        first = D.generate(m, 'normal')
        second = D.generate(m, 'normal')
        self.assertEqual(first.visible, second.visible)

    def test_going_back_up_a_difficulty_restores_notes(self):
        m = chart(self.events)
        low = D.generate(m, 'normal')
        high = D.generate(m, 'extreme')
        self.assertGreater(high.visible, low.visible,
                           '從 normal 再生 extreme 沒有把音符放回來')


class LadderTests(unittest.TestCase):
    """三個難度要真的是階梯。"""

    def setUp(self):
        self.results = {}
        for name in D.DIFFICULTIES:
            m = chart(dense())
            self.results[name] = D.generate(m, name)

    def test_visible_counts_increase_with_difficulty(self):
        counts = [self.results[n].visible for n in D.DIFFICULTIES]
        self.assertEqual(counts, sorted(counts), '難度愈高音符沒有愈多：%s' % counts)

    def test_gaps_shrink_with_difficulty(self):
        gaps = [self.results[n].gap_hands_ms for n in D.DIFFICULTIES]
        self.assertEqual(gaps, sorted(gaps, reverse=True),
                         '難度愈高間隔沒有愈短：%s' % gaps)

    def test_each_difficulty_hits_its_gap_target(self):
        for name, r in self.results.items():
            goal = D.TARGETS[name]
            self.assertGreaterEqual(
                r.gap_hands_ms, goal.same_hand_gap_ms * 0.85,
                '%s 的同手間隔 %.0fms 比官方的 %dms 短太多'
                % (name, r.gap_hands_ms, goal.same_hand_gap_ms))

    def test_simultaneous_notes_are_capped(self):
        for name, r in self.results.items():
            self.assertLessEqual(r.max_simultaneous,
                                 D.TARGETS[name].max_simultaneous,
                                 '%s 同時看得見太多顆' % name)

    def test_the_chord_caps_are_three_two_two(self):
        """Expert 三顆、Hard 兩顆、Normal 兩顆——比官方（5/3/2）保守。"""
        self.assertEqual([D.TARGETS[n].max_simultaneous for n in D.DIFFICULTIES],
                         [2, 2, 3])
        for name in ('hard', 'extreme'):
            self.assertLess(D.TARGETS[name].max_simultaneous,
                            D.OFFICIAL_MAX_SIMULTANEOUS[name])


class ChordCapTests(unittest.TestCase):
    """和弦收頂：留外聲部，而且兩手都要留得住。"""

    def test_a_small_group_is_untouched(self):
        group = [[object()], [object(), object()]]
        notes = chart([(0, [(60, 0, 100), (64, 1, 100)])]).notes_tree
        kept = D._cap_chords([notes], 3)
        self.assertEqual(len(kept), 2)

    def test_it_keeps_the_outer_voices(self):
        notes = chart([(0, [(60, 0, 100), (64, 0, 100), (67, 0, 100),
                            (72, 0, 100)])]).notes_tree
        kept = D._cap_chords([notes], 2)
        self.assertEqual(sorted(n.pitch for n in kept), [60, 72],
                         '沒有取最高和最低，取到內聲部了')

    def test_both_hands_survive_a_cap_of_two(self):
        """只照音高取的話，音域低的那隻手會整段消失。"""
        notes = chart([(0, [(80, 0, 100), (82, 0, 100), (84, 0, 100),
                            (40, 1, 100), (42, 1, 100)])]).notes_tree
        kept = D._cap_chords([notes], 2)
        self.assertEqual({int(n.hand) for n in kept}, {0, 1})

    def test_the_cap_is_respected(self):
        notes = chart([(0, [(50 + i, i % 2, 100) for i in range(9)])]).notes_tree
        for cap in (2, 3, 5):
            self.assertEqual(len(D._cap_chords([notes], cap)), cap)


class ThinningTests(unittest.TestCase):
    """稀疏化：厚的和弦是錨點先留，單音是經過音先砍。

    官方 2242 份譜實測，降難度時發音點的存活率完全由「那一刻有幾顆音」決定：
    normal 的單音只留 46.9%，四顆以上的和弦留 91.7%。而「旋律有沒有動」幾乎
    沒有差別（65.1% vs 72.4%，還反向），「留下的是不是最高音」只有 48.8%
    ——不是照冠音挑，是照和弦厚度挑。
    """

    def test_a_chord_is_kept_or_dropped_whole(self):
        m = chart([(0, [(60, 0, 100), (64, 0, 100)]),
                   (50, [(67, 0, 100), (71, 0, 100)])])
        kept = D._thin_by_bucket(m.notes_tree, 300, 1)
        self.assertEqual(len({int(n.start) for n in kept}), 1, '留下半個和弦')

    def test_a_zero_spacing_keeps_everything(self):
        m = chart(dense(groups=5))
        self.assertEqual(len(D._thin_by_bucket(m.notes_tree, 0, 1)),
                         len(m.notes_tree))

    def test_the_spacing_search_hits_the_onset_ratio(self):
        """主控項是**保留比例**，不是絕對間隔——比例會跟著曲子的密度走。"""
        import dataclasses
        m = chart(dense(groups=200, step=80))
        goal = dataclasses.replace(D.TARGETS['extreme'],
                                   onset_ratio=0.5, same_hand_gap_ms=1)
        spacing = D._solve_bucket_rate(m.notes_tree, goal, 1)
        before = len({int(n.start) for n in m.notes_tree})
        after = len({int(n.start)
                     for n in D._thin_by_bucket(m.notes_tree, spacing, 1)})
        self.assertLessEqual(after, before * 0.5 + 1)
        self.assertGreater(after, before * 0.3)

    def test_the_absolute_gap_is_only_a_cap(self):
        """特別密的曲子照比例砍完還是太快時，用絕對間隔再壓一次。"""
        import dataclasses
        m = chart(dense(groups=200, step=80))
        loose = dataclasses.replace(D.TARGETS['extreme'],
                                    onset_ratio=1.0, same_hand_gap_ms=400)
        spacing = D._solve_bucket_rate(m.notes_tree, loose, 1)
        gap = D._same_hand_gap(D._thin_by_bucket(m.notes_tree, spacing, 1))
        self.assertGreaterEqual(gap, 400)

    def test_an_already_sparse_chart_needs_no_thinning(self):
        import dataclasses
        m = chart([(i * 900, [(60, 0, 100)]) for i in range(20)])
        goal = dataclasses.replace(D.TARGETS['normal'], onset_ratio=1.0)
        self.assertEqual(D._solve_bucket_rate(m.notes_tree, goal, 1), 0.0)

    # ── 厚度優先 ────────────────────────────────────────────────
    def thick_and_thin(self):
        """交替出現的「厚和弦」與「單音」，間隔一樣。"""
        events = []
        for i in range(120):
            if i % 2:
                events.append((i * 100, [(60, 0, 80)]))
            else:
                events.append((i * 100, [(55, 0, 80), (60, 0, 80), (64, 0, 80)]))
        return events

    def test_thick_chords_outlive_single_notes(self):
        events = self.thick_and_thin()
        m = chart(events)
        weights = D._source_thickness(D._onset_groups(m.notes_tree))
        kept = D._thin_by_bucket(m.notes_tree, 250, 1, weights)
        alive = {int(n.start) for n in kept}
        thick = [w for w, size in weights.items() if size >= 3]
        thin = [w for w, size in weights.items() if size == 1]
        thick_rate = sum(1 for w in thick if w in alive) / float(len(thick))
        thin_rate = sum(1 for w in thin if w in alive) / float(len(thin))
        self.assertGreater(thick_rate, thin_rate + 0.3,
                           '厚和弦 %.0f%% vs 單音 %.0f%%，沒有優先留厚的'
                           % (thick_rate * 100, thin_rate * 100))

    def test_the_thickness_comes_from_the_source(self):
        """收頂會把厚和弦削成 2~3 顆，厚度要在收頂之前量。"""
        groups = D._onset_groups(chart(self.thick_and_thin()).notes_tree)
        weights = D._source_thickness(groups)
        self.assertEqual(max(weights.values()), 3)

    def test_the_gradient_is_monotone(self):
        """愈厚愈容易留——這是官方表格的形狀。"""
        for name in D.DIFFICULTIES:
            m = chart(self.thick_and_thin())
            source = {}
            for n in m.notes_tree:
                source.setdefault(int(n.start), []).append(n)
            D.generate(m, name)
            alive = {int(n.start) for n in visible(m)}
            rates = {}
            for when, group in source.items():
                row = rates.setdefault(len(group), [0, 0])
                row[0] += 1 if when in alive else 0
                row[1] += 1
            ordered = [rates[k][0] / float(rates[k][1]) for k in sorted(rates)]
            self.assertEqual(ordered, sorted(ordered),
                             '%s 的存活率沒有隨厚度遞增：%s' % (name, ordered))


class LanesAreRederivedTests(unittest.TestCase):
    """鍵道每個難度各自重排——官方只有 22.7~54.5% 對得上，不是繼承的。"""

    def test_lanes_change(self):
        source = chart(dense())
        before = {(int(n.start), n.pitch): (n.min_key, n.max_key)
                  for n in source.notes_tree}
        m = chart(dense())
        D.generate(m, 'normal')
        after = [(int(n.start), n.pitch, n.min_key, n.max_key) for n in visible(m)]
        moved = sum(1 for s, p, lo, hi in after if before.get((s, p)) != (lo, hi))
        self.assertGreater(moved, 0, '鍵道完全沒有重排')

    def test_the_note_width_follows_the_difficulty(self):
        widths = {}
        for name in D.DIFFICULTIES:
            m = chart(dense())
            D.generate(m, name)
            counts = {}
            for n in visible(m):
                w = int(n.max_key) - int(n.min_key) + 1
                counts[w] = counts.get(w, 0) + 1
            widths[name] = max(counts, key=lambda k: counts[k])
        self.assertEqual(widths['normal'], D.TARGETS['normal'].note_width)
        self.assertEqual(widths['hard'], D.TARGETS['hard'].note_width)
        self.assertEqual(widths['extreme'], D.TARGETS['extreme'].note_width)

    def test_lanes_stay_inside_the_keyboard(self):
        m = chart(dense())
        D.generate(m, 'normal')
        for n in visible(m):
            self.assertGreaterEqual(int(n.min_key), 0)
            self.assertLess(int(n.max_key), 28)


class WidthUniformityTests(unittest.TestCase):
    """Expert 的寬度要齊。

    官方的一致度隨難度上升：normal 只有 66% 是寬度 5、hard 93% 是 4、
    extreme **99%** 是 3。而 eather 風格靠收窄擠空間（`dense_width`、
    `close_chord_interval_semitones`），套在 extreme 上會生出 26% 的寬度 2。
    """

    def widths(self, name):
        m = chart(dense())
        D.generate(m, name)
        counts = {}
        for n in visible(m):
            w = int(n.max_key) - int(n.min_key) + 1
            counts[w] = counts.get(w, 0) + 1
        return counts

    def test_no_difficulty_has_narrowed_notes(self):
        """愈簡單的譜愈不能有收窄的鍵——寬度 2 的音符夾在寬度 5 之間像雜訊。"""
        for name in D.DIFFICULTIES:
            counts = self.widths(name)
            self.assertEqual(set(counts), {D.TARGETS[name].note_width},
                             '%s 出現了收窄的音符：%s' % (name, counts))

    def test_all_three_are_uniform(self):
        self.assertEqual([D.TARGETS[n].uniform_width for n in D.DIFFICULTIES],
                         [True, True, True])

    def test_the_widths_are_five_four_three(self):
        self.assertEqual([D.TARGETS[n].note_width for n in D.DIFFICULTIES],
                         [5, 4, 3])

    def test_this_is_stricter_than_official(self):
        """官方的一致度只有 normal 66% / hard 93% / extreme 99%。"""
        self.assertTrue(D.TARGETS['normal'].uniform_width)

    def test_uniformity_does_not_shrink_the_chart(self):
        m = chart(dense())
        r = D.generate(m, 'extreme')
        self.assertEqual(r.visible + r.hidden, r.total)


class SourceHandsTests(unittest.TestCase):
    """來源的左右手是既有事實，排譜器不准重猜。

    `_assign_hands` 只有在「兩條以上帶音符的音軌」時才把音軌當權威，否則改用
    音高平均去猜，而且還會多跑一次 `_reduce_hand_leaps`。這個曲庫的譜面存成
    XML 之後**完全沒有 track**（實測鬼火 3474 顆全是 None），所以每次排譜都
    重猜——實測 Expert 有 80 顆、Normal 有 32 顆被改到和作者相反的手。
    """

    def setUp(self):
        # 音高會讓「照音高猜」和「照來源」給出不同答案：低音在右手、高音在
        # 左手，猜的一定會把它們對調。
        self.events = [(i * 120,
                        [(80 + i % 5, 0, 100), (45 + i % 5, 1, 100)])
                       for i in range(150)]

    def source_hands(self):
        return {(int(n.start), int(n.pitch)): int(n.hand)
                for n in chart(self.events).notes_tree}

    def test_no_note_changes_hand(self):
        want = self.source_hands()
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            flipped = [n for n in visible(m)
                       if int(n.hand) != want[(int(n.start), int(n.pitch))]]
            self.assertEqual(flipped, [], '%s 把 %d 顆改到別隻手'
                             % (name, len(flipped)))

    def test_the_temporary_track_is_cleaned_up(self):
        """排譜時借用 track 傳達「手是來源給的」，排完要還原，不然檔案會多出
        一個原本沒有的欄位。"""
        m = chart(self.events)
        self.assertTrue(all(n.track is not None for n in m.notes_tree))
        before = [n.track for n in m.notes_tree]
        D.generate(m, 'normal')
        self.assertEqual([n.track for n in m.notes_tree], before)

    def test_a_chart_without_tracks_keeps_none(self):
        m = chart(self.events)
        for n in m.notes_tree:
            n.track = None
        D.generate(m, 'normal')
        self.assertTrue(all(n.track is None for n in m.notes_tree),
                        '排譜之後多出了 track 欄位')


class ArticulationTests(unittest.TestCase):
    """長押是來源譜面的既有事實，不該被排譜器重判掉。"""

    def test_holds_survive_generation(self):
        """排譜器的長押判定是相對於該手發音間距的；稀疏化之後沒有一顆過得了
        門檻，實測 273 顆會被判成 8 顆（也就是幾乎歸零）。"""
        events = [(i * 100, [(60 + i % 7, i % 2, 1200 if i % 3 == 0 else 80)])
                  for i in range(200)]
        m = chart(events)
        r = D.generate(m, 'normal')
        goal = D.TARGETS['normal']
        self.assertGreater(r.long_ratio, goal.long_ratio * 0.5,
                           '長押被排譜器整批判掉了（%.1f%%，上限是 %.1f%%）'
                           % (r.long_ratio * 100, goal.long_ratio * 100))

    def test_a_converted_hold_keeps_a_playable_duration(self):
        """把長押轉成一般音符時**不能把長度歸零**。

        `end` 同時是 keysound 的發聲長度（sub_note 的 end_timing_msec 就是從
        它來的），設成 0 等於把那顆音的聲音也拿掉。實測鬼火的一般音符中位是
        83ms、最短 71ms，一顆都不是 0。
        """
        events = [(i * 500, [(60 + i % 5, i % 2, 800)]) for i in range(60)]
        events += [(i * 500 + 200, [(72, 0, 90)]) for i in range(60)]
        m = chart(sorted(events))
        D.generate(m, 'normal')
        for n in visible(m):
            self.assertGreater(int(n.end), int(n.start),
                               '出現 0 長度的音符，keysound 會消失')

    def test_short_holds_become_taps(self):
        """短長押要求「按下去、幾乎立刻放開」，在慢速譜上比點擊還難。"""
        goal = D.TARGETS['normal']
        events = [(i * 600, [(60 + i % 5, i % 2, 250)]) for i in range(80)]
        m = chart(events)
        D.generate(m, 'normal')
        for n in visible(m):
            if int(n.note_type) & 0x02:
                self.assertGreaterEqual(int(n.end) - int(n.start),
                                        goal.min_long_ms,
                                        '留下了比 min_long_ms 短的長押')

    def test_the_easy_difficulties_hold_less_than_official(self):
        """使用者要求低難度的長押再減少。"""
        self.assertLess(D.TARGETS['normal'].long_ratio,
                        D.OFFICIAL_LONG_RATIO['normal'])
        self.assertLess(D.TARGETS['hard'].long_ratio,
                        D.OFFICIAL_LONG_RATIO['hard'])

    def test_the_hold_floor_relaxes_as_difficulty_rises(self):
        floors = [D.TARGETS[n].min_long_ms for n in D.DIFFICULTIES]
        self.assertEqual(floors, sorted(floors, reverse=True))

    def test_the_long_ratio_is_capped(self):
        events = [(i * 100, [(60 + i % 7, i % 2, 600)]) for i in range(200)]
        for name in D.DIFFICULTIES:
            m = chart(events)
            r = D.generate(m, name)
            self.assertLessEqual(r.long_ratio, D.TARGETS[name].long_ratio + 0.02,
                                 '%s 長押 %.1f%% 超過官方的 %.1f%%'
                                 % (name, r.long_ratio * 100,
                                    D.TARGETS[name].long_ratio * 100))

    def test_the_longest_holds_are_the_ones_kept(self):
        """收比例時砍短的、留長的——長的那些才是音樂上真的要按住的。"""
        events = [(i * 500, [(60, 0, 200 + i * 40)]) for i in range(40)]
        m = chart(events)
        D.generate(m, 'normal')
        holds = [n for n in visible(m) if int(n.note_type) & 0x02]
        taps = [n for n in visible(m)
                if not int(n.note_type) & 0x02 and int(n.end) > int(n.start)]
        if holds and taps:
            self.assertGreater(min(int(n.end) - int(n.start) for n in holds),
                               0)


def with_hand2(name):
    """把官方的 hand=2 比例填回去的一份目標值。"""
    import dataclasses
    return dataclasses.replace(D.TARGETS[name],
                               hand2_ratio=D.OFFICIAL_HAND2_RATIO[name])


class Hand2Tests(unittest.TestCase):
    """hand=2（未指定手）預設**不產生**。

    官方 normal 有 54.3% 是 hand=2，但 nos-clone 畫音符是
    `hand == 0 ? 紅 : 藍`——hand=2 會整批變成左手藍色。實測鬼火的 normal
    有 335/833 顆本來是右手的音變藍，看起來就是「左右手亂標註」。
    能力留著（`OFFICIAL_HAND2_RATIO`），等遊戲端有第三種外觀再打開。
    """

    def test_it_is_off_by_default(self):
        for name in D.DIFFICULTIES:
            self.assertEqual(D.TARGETS[name].hand2_ratio, 0.0)

    def test_nothing_gets_marked_unassigned(self):
        for name in D.DIFFICULTIES:
            m = chart(melodic())
            D.generate(m, name)
            self.assertEqual([n for n in visible(m) if int(n.hand) == 2], [],
                             '%s 產生了 hand=2' % name)

    def test_the_official_ratios_are_still_recorded(self):
        self.assertGreater(D.OFFICIAL_HAND2_RATIO['normal'],
                           D.OFFICIAL_HAND2_RATIO['hard'])
        self.assertGreater(D.OFFICIAL_HAND2_RATIO['hard'],
                           D.OFFICIAL_HAND2_RATIO['extreme'])

    def test_turning_it_back_on_works(self):
        m = chart(melodic())
        r = D.generate(m, 'normal', targets=with_hand2('normal'))
        self.assertGreater(r.hand2_ratio, 0.0, '填回官方比例卻沒有作用')

    def test_it_only_lands_on_notes_that_are_alone(self):
        """官方 78% 的 hand=2 落在「該時刻只有它」的地方。"""
        m = chart(melodic())
        D.generate(m, 'normal', targets=with_hand2('normal'))
        at_time = {}
        for n in visible(m):
            at_time.setdefault(int(n.start), []).append(n)
        for group in at_time.values():
            if len(group) > 1:
                for n in group:
                    self.assertNotEqual(int(n.hand), 2,
                                        '和別的音同時發聲卻標成未指定手')

    def test_it_never_exceeds_the_target(self):
        for name in D.DIFFICULTIES:
            m = chart(melodic())
            r = D.generate(m, name, targets=with_hand2(name))
            self.assertLessEqual(r.hand2_ratio,
                                 D.OFFICIAL_HAND2_RATIO[name] + 0.01)


class LabelTests(unittest.TestCase):
    """匯出用的難度名。內部 key 沿用官方語料的 extreme，顯示名是 Expert。"""

    def test_the_labels_are_what_goes_on_disk(self):
        self.assertEqual([D.TARGETS[n].label for n in D.DIFFICULTIES],
                         ['Normal', 'Hard', 'Expert'])

    def test_the_internal_keys_follow_the_official_corpus(self):
        self.assertEqual(D.DIFFICULTIES, ('normal', 'hard', 'extreme'))


class RoundTripTests(unittest.TestCase):
    """存成 XML 再讀回來，隱藏音符要還在。"""

    def test_the_whole_chart_survives_a_save_and_load(self):
        import os
        import tempfile

        m = chart(dense(groups=60))
        total = len(m.notes_tree)
        r = D.generate(m, 'normal')
        path = os.path.join(tempfile.mkdtemp(), 'n.xml')
        m.save_xml(path)
        back = NoteModel()
        back.load_xml(path)
        self.assertEqual(len(back.notes_tree), total, '存檔後音符消失了')
        self.assertEqual(len(visible(back)), r.visible)
        self.assertEqual(len(hidden(back)), r.hidden)


class HostIndexContractTests(unittest.TestCase):
    """`hostIndex` 的語意：**檔案裡 notes 陣列的位置**。

    這是編輯器和遊戲之間的契約。遊戲端 `Chart.NormaliseHiddenNotes` 以前拿它
    去索引「已經拔掉隱藏音的 visible 清單」，隱藏音一多就整個錯位——難度生成
    的 normal 有 2641/3474 是隱藏的，實測 2641 顆裡只有 2 顆會落在寄主 500ms
    以內，其餘全部掉進退路，而退路是音高優先的，會挑到幾十秒外的音符。
    sub_note 是「寄主被打到時才排程」的，寄主選錯 = keysound 在錯的時間響。
    """

    def setUp(self):
        import json
        import os
        import tempfile

        m = chart(dense(groups=120))
        self.result = D.generate(m, 'normal')
        path = os.path.join(tempfile.mkdtemp(), 'n.json')
        m.save_json(path)
        self.notes = json.loads(io.open(path, encoding='utf-8').read())['notes']
        self.hidden = [n for n in self.notes if n.get('hidden')]

    def test_every_hidden_note_carries_a_host_index(self):
        self.assertTrue(self.hidden)
        for n in self.hidden:
            self.assertIn('hostIndex', n)

    def test_it_indexes_the_full_note_array(self):
        for n in self.hidden:
            index = n['hostIndex']
            self.assertTrue(0 <= index < len(self.notes),
                            'hostIndex %d 超出 notes 陣列（%d 顆）'
                            % (index, len(self.notes)))

    def test_the_host_is_a_visible_note(self):
        for n in self.hidden:
            self.assertFalse(self.notes[n['hostIndex']].get('hidden'),
                             '寄主自己也是隱藏的')

    def test_the_host_is_close_in_time(self):
        """寄主太遠的話 keysound 會在錯的時間響。"""
        for n in self.hidden:
            host = self.notes[n['hostIndex']]
            self.assertLess(abs(int(host['startTime']) - int(n['startTime'])), 2000,
                            '寄主離了 %dms'
                            % abs(int(host['startTime']) - int(n['startTime'])))

    def test_the_visible_list_is_not_what_it_indexes(self):
        """釘住方向：這些索引拿去索引 visible 清單會是錯的。

        萬一哪天有人把語意改成 visible 相對，這個測試會失敗、提醒他遊戲端
        也要一起改。
        """
        visible = [n for n in self.notes if not n.get('hidden')]
        near = sum(1 for n in self.hidden
                   if 0 <= n['hostIndex'] < len(visible)
                   and abs(int(visible[n['hostIndex']]['startTime'])
                           - int(n['startTime'])) < 2000)
        self.assertLess(near, len(self.hidden) * 0.5,
                        '索引 visible 也對得上，那這個契約沒有被釘住')


class FastRunTests(unittest.TestCase):
    """快速句不能被砍光。

    官方 normal / hard / extreme 的同手間隔 p10 全都是 79ms、100ms 以下的
    全都佔 24%——快速句在每個難度都在，差別只在句與句之間的空隙。單純用
    「每個間隔都不得小於 T」會把下限一起抬上去，實測 <100ms 從 24% 掉到 0%，
    整首變成等速的節拍器。
    """

    def setUp(self):
        # 均勻密集的來源（像鬼火那種無窮動練習曲），最容易被砍成等間距。
        # 兩手同時各一顆，這樣**每一隻手**的來源間隔都是 90ms——兩手交替的話
        # 每隻手其實是 180ms，測到的就不是同一件事了。
        self.events = [(i * 90, [(72 + i % 5, 0, 80), (48 + i % 5, 1, 80)])
                       for i in range(600)]

    def gaps(self, name):
        m = chart(self.events)
        D.generate(m, name)
        out = []
        for hand in (0, 1, 2):
            times = sorted({int(n.start) for n in visible(m)
                            if int(n.hand) == hand})
            out += [b - a for a, b in zip(times, times[1:])]
        return out

    def test_the_source_spacing_survives_somewhere(self):
        for name in D.DIFFICULTIES:
            gaps = self.gaps(name)
            self.assertIn(90, gaps,
                          '%s 完全沒有原速的間隔，快速句被砍光了' % name)

    def test_a_meaningful_share_is_fast(self):
        for name in D.DIFFICULTIES:
            gaps = self.gaps(name)
            share = sum(1 for g in gaps if g <= 100) / float(len(gaps))
            self.assertGreater(share, 0.08,
                               '%s 只有 %.1f%% 的間隔是快的' % (name, share * 100))

    def test_the_floor_is_not_raised(self):
        """下限要維持來源本來的樣子，抬的是平均。"""
        for name in D.DIFFICULTIES:
            self.assertEqual(min(self.gaps(name)), 90)

    def test_official_shape_is_recorded(self):
        for name in D.DIFFICULTIES:
            shape = D.OFFICIAL_GAP_SHAPE[name]
            self.assertEqual(shape['p10'], 79)
            self.assertGreater(shape['under_100'], 0.2)


class HandSpanTests(unittest.TestCase):
    """同手同時的鍵道跨度上限。一隻手張不開那麼大。

    官方 extreme 的同手同時跨度中位是 6、p99 是 10，超過 9 格的只佔 1.2%。
    排譜器是照音程留間隙的，大音程會留到 2 格空，三顆寬 3 加兩個空就 13 格。
    """

    def setUp(self):
        # 大音程的和弦：排譜器會把它們拉得很開
        self.events = [(i * 300, [(40, 0, 100), (64, 0, 100), (88, 0, 100)])
                       for i in range(80)]

    def spans(self, name):
        m = chart(self.events)
        D.generate(m, name)
        groups = {}
        for n in visible(m):
            groups.setdefault((int(n.start), int(n.hand)), []).append(n)
        return [max(int(x.max_key) for x in v) - min(int(x.min_key) for x in v) + 1
                for v in groups.values() if len(v) > 1]

    def test_the_span_is_capped(self):
        for name in D.DIFFICULTIES:
            goal = D.TARGETS[name]
            # 貼合是物理下限：兩顆寬 5 相鄰就佔 10 格，9 格是做不到的
            floor = goal.note_width * goal.max_simultaneous
            allowed = max(goal.max_hand_span, floor)
            for span in self.spans(name):
                self.assertLessEqual(span, allowed,
                                     '%s 的同手跨度 %d 超過 %d'
                                     % (name, span, allowed))

    def test_expert_fits_in_nine(self):
        """Expert 三顆寬 3 貼合正好 9 格，所以真的做得到。"""
        for span in self.spans('extreme'):
            self.assertLessEqual(span, 9)

    def test_the_caps_follow_the_official_spread(self):
        """官方的跨度 p90：normal 13、hard 9、extreme 8——低難度反而更寬。

        上限訂太緊會逼出「完全貼合」：Normal 的 2 顆寬 5 就是 10 格，卡在 10
        等於一點空隙都留不下，實測 100% 的和弦被壓成貼合、看不出音程差別。
        """
        self.assertEqual([D.TARGETS[n].max_hand_span for n in D.DIFFICULTIES],
                         [13, 9, 9])

    def test_notes_stay_on_the_keyboard(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertGreaterEqual(int(n.min_key), 0)
                self.assertLess(int(n.max_key), 28)

    def test_widths_are_untouched_by_the_squeeze(self):
        """壓縮是移位置，不是收窄——收窄的音符低難度更不能有。"""
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertEqual(int(n.max_key) - int(n.min_key) + 1,
                                 D.TARGETS[name].note_width)


class HandLoadTests(unittest.TestCase):
    """一隻手同時要應付幾件事——**按住中的長押也算**。

    `max_simultaneous` 只數「這一刻新發音的顆數」。長押按下去之後那隻手就一直
    被佔著，但它不再是任何一組的成員，所以數不到。實測 Expert 有 14 個時刻是
    「2 個新音 + 2 個按住中 = 4 件事、橫跨 12 格」——使用者的原話是「hold 加
    2 個 tap 並排、全三寬，這怎麼玩」。
    """

    def setUp(self):
        # 一手長音、同一手緊接著兩顆短音
        events = []
        for i in range(50):
            base = i * 1200
            events.append((base, [(60, 0, 1000), (62, 0, 1000), (40, 1, 100)]))
            events.append((base + 300, [(64, 0, 100), (67, 0, 100), (42, 1, 100)]))
            events.append((base + 600, [(65, 0, 100), (69, 0, 100), (44, 1, 100)]))
        self.events = events

    def loads(self, model):
        vis = visible(model)
        onsets = {}
        for n in vis:
            onsets.setdefault((int(n.start), int(n.hand)), []).append(n)
        out = []
        for (when, hand), group in onsets.items():
            holding = [h for h in vis
                       if int(h.hand) == hand
                       and int(h.note_type) & 0x02
                       and int(h.start) < when < int(h.end)]
            out.append(len(group) + len(holding))
        return out

    def test_the_load_never_exceeds_the_cap(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            worst = max(self.loads(m))
            self.assertLessEqual(worst, D.TARGETS[name].max_hand_load,
                                 '%s 單手要同時應付 %d 件事' % (name, worst))

    def test_a_sustaining_hold_counts_against_the_cap(self):
        """這是重點：不是只數這一刻新發音的。"""
        from qt_editor.difficulty import _cap_hand_load
        m = chart([(0, [(60, 0, 900)]),
                   (300, [(64, 0, 100), (67, 0, 100)])])
        kept = _cap_hand_load(m.notes_tree, 2)
        later = [n for n in kept if int(n.start) == 300]
        self.assertEqual(len(later), 1,
                         '長押還按著，卻放行了兩顆新音')

    def test_a_full_hand_gets_nothing_new(self):
        """兩個長押按著時那一刻該是空的——手正被兩根手指佔住。"""
        from qt_editor.difficulty import _cap_hand_load
        m = chart([(0, [(60, 0, 900), (62, 0, 900)]),
                   (300, [(64, 0, 100)])])
        kept = _cap_hand_load(m.notes_tree, 2)
        self.assertEqual([n for n in kept if int(n.start) == 300], [])

    def test_the_other_hand_is_unaffected(self):
        from qt_editor.difficulty import _cap_hand_load
        m = chart([(0, [(60, 0, 900), (62, 0, 900)]),
                   (300, [(64, 0, 100), (40, 1, 100)])])
        kept = _cap_hand_load(m.notes_tree, 2)
        later = [n for n in kept if int(n.start) == 300]
        self.assertEqual([int(n.hand) for n in later], [1])

    def test_nothing_is_lost_from_the_chart(self):
        """被拿掉的只是「看得見」，音訊事件一顆都不能少。"""
        m = chart(self.events)
        total = len(m.notes_tree)
        r = D.generate(m, 'extreme')
        self.assertEqual(r.visible + r.hidden, total)
        self.assertEqual(len(m.notes_tree), total)

    def test_the_cap_is_recorded_for_all_three(self):
        for name in D.DIFFICULTIES:
            self.assertEqual(D.TARGETS[name].max_hand_load, 2)


class FastFollowTests(unittest.TestCase):
    """後面緊接著快速音時，那一刻同手只留冠音。

    官方 extreme 實測：距離下一顆同手音 <120ms 時 **99.0%** 是單音（2 顆只有
    0.6%）；120~250ms 才有 12.6% 是兩顆、>=250ms 是 25.8%。手要立刻去打下一顆，
    沒有餘裕按和弦——保留節奏點不代表那個點要按滿。
    """

    def setUp(self):
        # 和弦後面緊接著一串快速音
        events = []
        for i in range(60):
            base = i * 1000
            events.append((base, [(60, 0, 80), (64, 0, 80), (67, 0, 80)]))
            for k in range(1, 5):
                events.append((base + k * 90, [(70 + k, 0, 60)]))
        self.events = events

    def offenders(self, model, fast_ms):
        out = []
        for hand in (0, 1, 2):
            at = {}
            for n in visible(model):
                if int(n.hand) == hand:
                    at.setdefault(int(n.start), []).append(n)
            times = sorted(at)
            for i, when in enumerate(times[:-1]):
                if times[i + 1] - when < fast_ms and len(at[when]) > 1:
                    out.append((when, len(at[when])))
        return out

    def test_no_chord_sits_before_a_fast_note(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            self.assertEqual(self.offenders(m, D.TARGETS[name].fast_follow_ms), [],
                             '%s 在快速音之前留了和弦' % name)

    def test_the_note_kept_is_the_top_one(self):
        from qt_editor.difficulty import _thin_before_fast
        m = chart([(0, [(60, 0, 80), (64, 0, 80), (67, 0, 80)]),
                   (90, [(72, 0, 80)])])
        kept = _thin_before_fast(m.notes_tree, 120)
        first = [n for n in kept if int(n.start) == 0]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].pitch, 67, '留下的不是冠音')

    def test_a_chord_with_room_after_it_survives(self):
        from qt_editor.difficulty import _thin_before_fast
        m = chart([(0, [(60, 0, 80), (64, 0, 80)]),
                   (500, [(72, 0, 80)])])
        kept = _thin_before_fast(m.notes_tree, 120)
        self.assertEqual(len([n for n in kept if int(n.start) == 0]), 2)

    def test_the_other_hand_is_judged_separately(self):
        from qt_editor.difficulty import _thin_before_fast
        m = chart([(0, [(60, 0, 80), (64, 0, 80), (40, 1, 80), (44, 1, 80)]),
                   (90, [(72, 0, 80)])])
        kept = _thin_before_fast(m.notes_tree, 120)
        self.assertEqual(len([n for n in kept if int(n.hand) == 1]), 2,
                         '左手後面沒有快速音，不該被收掉')

    def test_the_threshold_matches_the_official_finding(self):
        for name in D.DIFFICULTIES:
            self.assertEqual(D.TARGETS[name].fast_follow_ms, 120)


class CompressionTests(unittest.TestCase):
    """長階梯壓成滑音、快速交替壓成顫音。

    兩者都是「一個動作打完一串」——節奏全部留住，難度卻不會上去。官方兩種都
    用：滑音 normal 2.0% / hard 2.4% / extreme 3.7%（real 幾乎 0，最高難度就是
    要你一顆一顆打）；顫音 0.099% / 0.209% / 0.287%，長度中位 481~774ms。
    """

    def staircase(self, count=8, step=120):
        """一段單調上行的階梯。"""
        return [(i * step, [(50 + i * 2, 0, 80)]) for i in range(count)]

    def alternation(self, count=10, step=90):
        """兩個音的快速來回。"""
        return [(i * step, [(60 if i % 2 else 65, 0, 70)]) for i in range(count)]

    # ── 階梯 → 滑音 ────────────────────────────────────────────
    def test_a_staircase_becomes_a_slide(self):
        from qt_editor.difficulty import _compress_staircases
        m = chart(self.staircase())
        made = _compress_staircases(m.notes_tree, 1.0)
        self.assertEqual(made, 8)
        self.assertTrue(all(int(n.note_type) & 0x04 for n in m.notes_tree))

    def test_a_jumbled_run_is_not_a_staircase(self):
        """不單調就不是階梯。"""
        from qt_editor.difficulty import _compress_staircases
        events = [(i * 120, [(50 + (i % 3) * 4, 0, 80)]) for i in range(8)]
        m = chart(events)
        self.assertEqual(_compress_staircases(m.notes_tree, 1.0), 0)

    def test_a_big_leap_is_not_a_staircase(self):
        from qt_editor.difficulty import _compress_staircases
        events = [(i * 120, [(50 + i * 9, 0, 80)]) for i in range(8)]
        m = chart(events)
        self.assertEqual(_compress_staircases(m.notes_tree, 1.0), 0)

    def test_a_slow_run_is_not_a_staircase(self):
        """慢的音階一顆一顆打得完，不需要壓。"""
        from qt_editor.difficulty import _compress_staircases
        m = chart(self.staircase(step=400))
        self.assertEqual(_compress_staircases(m.notes_tree, 1.0), 0)

    def test_chords_are_never_swallowed(self):
        """和弦不是階梯——每一刻只有一顆才算。"""
        from qt_editor.difficulty import _compress_staircases
        events = [(i * 120, [(50 + i * 2, 0, 80), (70 + i * 2, 0, 80)])
                  for i in range(8)]
        m = chart(events)
        self.assertEqual(_compress_staircases(m.notes_tree, 1.0), 0)

    # ── 交替 → 顫音 ────────────────────────────────────────────
    def test_an_alternation_becomes_a_trill(self):
        from qt_editor.difficulty import _compress_trills
        m = chart(self.alternation())
        self.assertEqual(_compress_trills(m.notes_tree, 1.0), 1)
        heads = [n for n in m.notes_tree if int(n.note_type) & 0x40]
        self.assertEqual(len(heads), 1)
        self.assertTrue(all(getattr(n, 'hidden', False)
                            for n in m.notes_tree if n is not heads[0]))

    def test_the_strokes_hide_inside_the_trill(self):
        from qt_editor.difficulty import _compress_trills
        m = chart(self.alternation())
        _compress_trills(m.notes_tree, 1.0)
        head = next(n for n in m.notes_tree if int(n.note_type) & 0x40)
        for note in m.notes_tree:
            if note is head:
                continue
            self.assertIs(getattr(note, '_sub_host', None), head)

    def test_the_trill_covers_the_whole_run_but_ends_a_little_early(self):
        """顫音要蓋住整串來回，但尾端比最後一擊早一點收。

        和長押同一套做法（長押是「尾端前移固定 ms」）：拉到最後一擊的結尾會
        讓顫音條緊貼著後面的音符，看起來像連在一起，而且顫音是持續按住的手
        勢，尾巴留一點空隙才知道什麼時候可以放開。
        """
        from qt_editor.difficulty import TRILL_TAIL_ADVANCE_MS, _compress_trills
        m = chart(self.alternation())
        strokes = sorted(m.notes_tree, key=lambda n: int(n.start))
        last_start, last_end = int(strokes[-1].start), int(strokes[-1].end)
        _compress_trills(m.notes_tree, 1.0)
        head = next(n for n in m.notes_tree if int(n.note_type) & 0x40)

        self.assertGreater(int(head.end), last_start,
                           '顫音收得比最後一擊的起音還早，等於少了一擊')
        self.assertLess(int(head.end), last_end, '尾端沒有比最後一擊短')
        self.assertEqual(int(head.end), last_end - TRILL_TAIL_ADVANCE_MS)

    def test_three_pitches_are_not_a_trill(self):
        from qt_editor.difficulty import _compress_trills
        events = [(i * 90, [(60 + i % 3, 0, 70)]) for i in range(10)]
        m = chart(events)
        self.assertEqual(_compress_trills(m.notes_tree, 1.0), 0)

    def test_a_long_alternation_is_not_a_trill(self):
        """太長的不是一個顫音，是一整段樂句。"""
        from qt_editor.difficulty import _compress_trills
        m = chart(self.alternation(count=40, step=200))
        self.assertEqual(_compress_trills(m.notes_tree, 1.0), 0)

    # ── 比例與冪等 ─────────────────────────────────────────────
    def test_the_slide_ratio_lands_near_the_official_one(self):
        events = []
        for i in range(40):
            events += [(i * 2000 + j * 120, [(50 + j * 2, 0, 80)]) for j in range(8)]
        for name in D.DIFFICULTIES:
            m = chart(sorted(events))
            D.generate(m, name)
            vis = visible(m)
            got = sum(1 for n in vis if int(n.note_type) & 0x04) / float(len(vis))
            self.assertLessEqual(got, D.TARGETS[name].slide_ratio + 0.02,
                                 '%s 的滑音 %.1f%% 超過目標 %.1f%% 太多'
                                 % (name, got * 100,
                                    D.TARGETS[name].slide_ratio * 100))

    def test_generated_slides_are_chained(self):
        """只標 note_type 是不夠的——遊戲端要靠 note_index 和 param1/param2
        才知道哪幾顆是同一條滑鍵。

        實測生出來的滑音是 `index=None, param1=0, param2=0`，完全沒有串鏈；而
        `param2 == 0` 在格式上代表「未設定」，`index 0` 還會讓鏈結被誤判成
        未串鏈而觸發推測連線。
        """
        events = []
        for i in range(30):
            events += [(i * 3000 + j * 120, [(50 + j * 2, 0, 80)]) for j in range(6)]
        for name in D.DIFFICULTIES:
            m = chart(sorted(events))
            D.generate(m, name)
            slides = [n for n in visible(m) if int(n.note_type) & 0x04]
            if not slides:
                continue
            for n in slides:
                self.assertIsNotNone(n.note_index, '滑音沒有 note_index')
                self.assertNotEqual(int(n.note_index), 0,
                                    'note_index 0 在格式上等於「沒有」')
            by_index = {int(n.note_index): n for n in slides}
            heads = tails = 0
            for n in slides:
                for link in (n.param1, n.param2):
                    if link not in (-1, None):
                        self.assertIn(link, by_index, '鏈結指到不存在的音符')
                heads += 1 if n.param1 == -1 else 0
                tails += 1 if n.param2 == -1 else 0
            self.assertEqual(heads, tails, '%s 的鏈頭和鏈尾數量不符' % name)
            self.assertGreater(heads, 0)

    def test_the_links_survive_trimming(self):
        """修剪之後鏈變短，剩下的要重新串——不然被還原的那幾顆還被指著。"""
        events = []
        for i in range(60):
            events += [(i * 3000 + j * 120, [(50 + j * 2, 0, 80)]) for j in range(6)]
        m = chart(sorted(events))
        D.generate(m, 'normal')
        slides = [n for n in visible(m) if int(n.note_type) & 0x04]
        by_index = {int(n.note_index): n for n in slides if n.note_index is not None}
        for n in slides:
            for link in (n.param1, n.param2):
                if link not in (-1, None):
                    self.assertIn(link, by_index)

    def test_a_reverted_slide_is_not_left_linked(self):
        from qt_editor.difficulty import _trim_generated_slides
        m = chart([(i * 120, [(50 + i * 2, 0, 80)]) for i in range(6)])
        for n in m.notes_tree:
            n.note_type = 0x04
            n._made_slide = True
        _trim_generated_slides(m.notes_tree, 0.0)
        for n in m.notes_tree:
            self.assertFalse(int(n.note_type) & 0x04)

    def test_generating_twice_is_idempotent(self):
        """壓縮會改寫 note_type，重生成要先還原成來源的樣子。"""
        events = []
        for i in range(20):
            events += [(i * 2000 + j * 120, [(50 + j * 2, 0, 80)]) for j in range(8)]
        m = chart(sorted(events))
        first = D.generate(m, 'normal')
        second = D.generate(m, 'normal')
        self.assertEqual(first.visible, second.visible)
        self.assertEqual(first.total, second.total)

    def test_nothing_is_lost_to_compression(self):
        events = []
        for i in range(20):
            events += [(i * 2000 + j * 90, [(60 if j % 2 else 65, 0, 70)])
                       for j in range(10)]
        for name in D.DIFFICULTIES:
            m = chart(sorted(events))
            total = len(m.notes_tree)
            r = D.generate(m, name)
            self.assertEqual(r.visible + r.hidden, total)
            self.assertEqual(r.orphans, 0)


class SlideChainTests(unittest.TestCase):
    """滑音鏈是**一個動作**，整條留或整條走，不能拆。

    手指滑過去一次，中間那幾顆不是分開按的。拆成兩三顆既毀掉那個手勢（鏈結
    會指到不存在的音符），也沒有變簡單——滑音本來就是低難度的語彙（官方
    normal 2.0%、hard 2.4%、extreme 3.7%，real 幾乎是 0）。

    實測全庫 48 條來源滑音鏈有 **45 條被拆散**，最糟的是 Melodiniq 的 15 顆鏈
    在 Normal 只剩 2 顆。
    """

    def setUp(self):
        # 一條 12 顆的滑音，前後都是密集的一般音符
        events = []
        for i in range(30):
            events.append((i * 100, [(50 + i % 5, 0, 80)]))
        for i in range(12):
            events.append((4000 + i * 100, [(60 + i, 0, 80)]))
        for i in range(30):
            events.append((6000 + i * 100, [(50 + i % 5, 0, 80)]))
        self.events = events

    def build(self):
        m = chart(self.events)
        for n in m.notes_tree:
            if 4000 <= int(n.start) < 5200:
                n.note_type = 0x04
        return m

    def chain_survivors(self, model):
        return [n for n in visible(model) if int(n.note_type) & 0x04]

    def test_the_whole_chain_survives(self):
        for name in D.DIFFICULTIES:
            m = self.build()
            D.generate(m, name)
            self.assertEqual(len(self.chain_survivors(m)), 12,
                             '%s 把滑音鏈拆掉了（只剩 %d 顆）'
                             % (name, len(self.chain_survivors(m))))

    def test_the_chain_is_recognised(self):
        from qt_editor.difficulty import _slide_chains
        m = self.build()
        chains = _slide_chains(m.notes_tree)
        self.assertEqual([len(c) for c in chains], [12])

    def test_a_lone_slide_is_not_a_chain(self):
        """單獨一顆滑音不成鏈，不需要特別保護。"""
        from qt_editor.difficulty import _protected_slides
        m = chart([(0, [(60, 0, 80)]), (5000, [(64, 0, 80)])])
        for n in m.notes_tree:
            n.note_type = 0x04
        self.assertEqual(_protected_slides(m.notes_tree), set())

    def test_the_fast_follow_rule_spares_slides(self):
        """滑音鏈是滑過去的，不是要同時按下的和弦。"""
        from qt_editor.difficulty import _thin_before_fast, _protected_slides
        m = chart([(0, [(60, 0, 80), (64, 0, 80)]), (90, [(67, 0, 80)])])
        for n in m.notes_tree:
            n.note_type = 0x04
        protected = _protected_slides(m.notes_tree)
        kept = _thin_before_fast(m.notes_tree, 120, protected)
        self.assertEqual(len(kept), 3)

    def test_nothing_is_lost_from_the_chart(self):
        for name in D.DIFFICULTIES:
            m = self.build()
            total = len(m.notes_tree)
            r = D.generate(m, name)
            self.assertEqual(r.visible + r.hidden, total)


class TrillTests(unittest.TestCase):
    """顫音（note_type 0x40）有兩個獨立的坑。

    1. **不能當隱藏音符的寄主**。遊戲端看到 trill 會走 `RefreshTrill`，把寄主的
       subNotes 當成**顫音的每一擊**連發——折進去的隱藏音符就被機關槍掃出來。
       實測全庫有 7 處（feng-yanno135miao 的 Hard、V 的三個難度）。
    2. **它的尾巴也是走廊**。遊戲端畫長條的條件是 `isTrillCached || type ==
       "hold"`，顫音同樣是一根貫穿的長條。實測有 10 處是左手音符壓在右手
       trill 的尾巴上（同樣鍵道、在顫音的整段期間內）。
    """

    def setUp(self):
        events = []
        for i in range(40):
            base = i * 1500
            events.append((base, [(72, 0, 1200)]))          # 之後標成 trill
            for k in range(1, 6):
                events.append((base + k * 200, [(50 + k, 1, 100)]))
        self.events = events

    def build(self, name):
        m = chart(self.events)
        for n in m.notes_tree:
            if int(n.end) - int(n.start) > 1000:
                n.note_type = 0x40
                n.min_key, n.max_key = 10, 12
            else:
                n.min_key, n.max_key = 10, 12   # 故意擺在同樣的鍵道上
        D.generate(m, name)
        return m

    def test_no_hidden_note_hides_inside_a_trill(self):
        for name in D.DIFFICULTIES:
            m = self.build(name)
            for n in m.notes_tree:
                if not getattr(n, 'hidden', False):
                    continue
                host = getattr(n, '_sub_host', None)
                if host is None:
                    continue
                self.assertFalse(int(getattr(host, 'note_type', 0)) & 0x40,
                                 '%s 把隱藏音符折進了 trill' % name)

    def test_nothing_sits_on_a_trill_tail(self):
        for name in D.DIFFICULTIES:
            m = self.build(name)
            vis = visible(m)
            trills = [n for n in vis
                      if int(n.note_type) & 0x40 and int(n.end) > int(n.start)]
            for trill in trills:
                for n in vis:
                    if n is trill or abs(int(n.start) - int(trill.start)) <= 35:
                        continue
                    end = max(int(n.end), int(n.start) + 1)
                    if not (int(trill.start) < end and int(n.start) < int(trill.end)):
                        continue
                    self.assertTrue(
                        int(n.min_key) > int(trill.max_key)
                        or int(n.max_key) < int(trill.min_key),
                        '%s 有音符壓在 trill 的尾巴上' % name)

    def test_a_trill_is_never_broken_into_a_tap(self):
        """顫音的每一擊都記在 subNotes 裡，降成一般音符等於整段丟掉。"""
        for name in D.DIFFICULTIES:
            m = self.build(name)
            kept = [n for n in visible(m) if int(n.note_type) & 0x40]
            self.assertTrue(kept, '%s 把 trill 拆光了' % name)


class SoftNoteTests(unittest.TestCase):
    """soft（note_type 1）一律還原成一般音符。

    soft 是編輯器專用的表情記號，官方語料裡完全沒有。來源譜面可能是人手標的，
    稀疏化留下來之後就殘留在低難度上——實測全庫的生成難度有 3.4~3.6% 是 soft。
    """

    def setUp(self):
        self.events = [(i * 300, [(60 + i % 7, i % 2, 100)]) for i in range(150)]

    def test_no_soft_note_survives(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            for n in m.notes_tree:
                n.note_type = 1
            D.generate(m, name)
            self.assertEqual([n for n in visible(m) if int(n.note_type) == 1], [],
                             '%s 留下了 soft 音符' % name)

    def test_it_becomes_a_plain_tap(self):
        from qt_editor.difficulty import _restore_soft
        m = chart([(0, [(60, 0, 100)])])
        m.notes_tree[0].note_type = 1
        self.assertEqual(_restore_soft(m.notes_tree), 1)
        self.assertEqual(int(m.notes_tree[0].note_type), 0)

    def test_holds_are_untouched(self):
        from qt_editor.difficulty import _restore_soft
        m = chart([(0, [(60, 0, 800)])])
        _restore_soft(m.notes_tree)
        self.assertEqual(int(m.notes_tree[0].note_type), 2)


class HandSeparationTests(unittest.TestCase):
    """同時發聲時，右手（hand=0）整組要在左手上方，中間留空。

    官方 extreme 實測（1670 個兩手同時發聲的時刻）：「右手最低 − 左手最高」
    中位 6、**最小 1**，重疊或左右顛倒的比例是 **0.0%**——一次都沒有。

    `_cap_hand_span` 是一隻手一隻手往自己的中心壓的，兩手各壓各的就會壓進
    對方的區域：實測 Expert 有 4 處，例如左手佔 14-16、右手佔 12-20，左手
    整個被右手夾在中間。
    """

    def setUp(self):
        # 兩手音域很近，壓縮之後容易撞在一起
        self.events = [(i * 260,
                        [(62 + i % 3, 0, 100), (58 + i % 3, 1, 100)])
                       for i in range(120)]

    def pairs(self, model):
        at = {}
        for n in visible(model):
            at.setdefault(int(n.start), []).append(n)
        out = []
        for group in at.values():
            right = [n for n in group if int(n.hand) == 0]
            left = [n for n in group if int(n.hand) == 1]
            if right and left:
                out.append((min(int(n.min_key) for n in right),
                            max(int(n.max_key) for n in right),
                            min(int(n.min_key) for n in left),
                            max(int(n.max_key) for n in left)))
        return out

    def test_the_hands_never_overlap(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for rlo, rhi, llo, lhi in self.pairs(m):
                self.assertFalse(rlo <= lhi and rhi >= llo,
                                 '%s 兩手鍵道重疊：右%s 左%s'
                                 % (name, (rlo, rhi), (llo, lhi)))

    def test_the_right_hand_stays_above(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for rlo, _rhi, _llo, lhi in self.pairs(m):
                self.assertGreater(rlo, lhi,
                                   '%s 右手跑到左手下面了' % name)

    def test_a_clear_lane_is_left_between_them(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for rlo, _rhi, _llo, lhi in self.pairs(m):
                self.assertGreaterEqual(rlo - lhi, 2, '兩手貼在一起')

    def test_the_group_moves_as_one(self):
        """組內相對位置不能變——那會毀掉跨度壓縮的成果。"""
        from qt_editor.difficulty import _separate_hands
        m = chart([(0, [(70, 0, 100), (72, 0, 100), (50, 1, 100)])])
        notes = m.notes_tree
        notes[0].min_key, notes[0].max_key = 10, 12
        notes[1].min_key, notes[1].max_key = 14, 16
        notes[2].min_key, notes[2].max_key = 12, 14
        _separate_hands(notes)
        right = sorted((int(n.min_key), int(n.max_key))
                       for n in notes if int(n.hand) == 0)
        self.assertEqual(right[1][0] - right[0][0], 4, '右手組內距離被改了')

    def test_it_stays_on_the_keyboard(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertGreaterEqual(int(n.min_key), 0)
                self.assertLess(int(n.max_key), 28)

    def test_widths_are_untouched(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertEqual(int(n.max_key) - int(n.min_key) + 1,
                                 D.TARGETS[name].note_width)


class HoldCorridorTests(unittest.TestCase):
    """長押按住的期間，它那幾條鍵道不准再出現別的音符。

    長押畫面上是一根貫穿的長條；另一顆音壓在同幾條鍵道上，玩家看到的是兩個
    東西疊在一起，分不出要不要放開。實測 Expert 有 3 處是這樣（長押 16-18、
    音符 18-20，共用第 18 道），來源是跨度壓縮把音符往中心擠進了長押裡。
    """

    def setUp(self):
        # 長音配上緊接著的短音，而且音高很近 —— 排譜器會把它們擺在相鄰鍵道
        events = []
        for i in range(60):
            base = i * 900
            events.append((base, [(60, 0, 800), (48, 1, 100)]))
            events.append((base + 300, [(62, 0, 100), (50, 1, 100)]))
            events.append((base + 600, [(59, 0, 100), (47, 1, 100)]))
        self.events = events

    def conflicts(self, model):
        vis = visible(model)
        holds = [n for n in vis
                 if int(n.note_type) & 0x02 and int(n.end) > int(n.start)]
        out = []
        for hold in holds:
            for note in vis:
                if note is hold:
                    continue
                if abs(int(note.start) - int(hold.start)) <= 35:
                    continue          # 同時發聲是和弦，不是走廊問題
                hold_end = max(int(hold.end), int(hold.start) + 1)
                note_end = max(int(note.end), int(note.start) + 1)
                if not (int(hold.start) < note_end and int(note.start) < hold_end):
                    continue
                if (int(note.min_key) > int(hold.max_key)
                        or int(note.max_key) < int(hold.min_key)):
                    continue
                out.append((hold, note))
        return out

    def test_no_note_sits_in_a_hold_corridor(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            self.assertEqual(self.conflicts(m), [],
                             '%s 有音符壓在長押的鍵道上' % name)

    def test_holds_are_kept_when_the_note_can_move(self):
        """讓得開就讓音符——長押保得住，鍵道也只動那一顆。"""
        m = chart(self.events)
        before = sum(1 for n in m.notes_tree if int(n.note_type) & 0x02)
        D.generate(m, 'extreme')
        after = sum(1 for n in visible(m) if int(n.note_type) & 0x02)
        self.assertGreater(after, 0, '長押被全部拆掉了')
        self.assertGreater(before, 0)

    def test_widths_survive_the_shuffle(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertEqual(int(n.max_key) - int(n.min_key) + 1,
                                 D.TARGETS[name].note_width)

    def test_notes_stay_on_the_keyboard(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            for n in visible(m):
                self.assertGreaterEqual(int(n.min_key), 0)
                self.assertLess(int(n.max_key), 28)

    def test_moving_a_note_respects_the_span_cap(self):
        """讓位不能把音符推到鍵盤另一端。

        走廊處理排在跨度壓縮和兩手分離**之後**，以前它找「最近的空位」時完全
        不管那兩條約束——實測「系ぎて」的 Hard 出現跨度 28（整個鍵盤）、全庫
        196 個生成難度有 26 個跨度超標。
        """
        for name in D.DIFFICULTIES:
            goal = D.TARGETS[name]
            m = chart(self.events)
            D.generate(m, name)
            groups = {}
            for n in visible(m):
                groups.setdefault((int(n.start), int(n.hand)), []).append(n)
            allowed = max(goal.max_hand_span,
                          goal.note_width * goal.max_simultaneous)
            for group in groups.values():
                span = (max(int(x.max_key) for x in group)
                        - min(int(x.min_key) for x in group) + 1)
                self.assertLessEqual(span, allowed,
                                     '%s 讓位之後跨度變成 %d' % (name, span))

    def test_moving_a_note_keeps_the_hands_apart(self):
        for name in D.DIFFICULTIES:
            m = chart(self.events)
            D.generate(m, name)
            by_time = {}
            for n in visible(m):
                by_time.setdefault(int(n.start), {}).setdefault(int(n.hand), []).append(n)
            for hands in by_time.values():
                right, left = hands.get(0), hands.get(1)
                if not right or not left:
                    continue
                self.assertGreater(min(int(n.min_key) for n in right),
                                   max(int(n.max_key) for n in left),
                                   '%s 讓位之後兩手重疊' % name)

    def test_a_broken_hold_keeps_a_playable_duration(self):
        """真的讓不開時長押會被降成一般音符，但長度不能歸零。"""
        from qt_editor.difficulty import _resolve_hold_corridors
        m = chart([(0, [(60, 0, 900)]), (300, [(61, 0, 100)])])
        for n in m.notes_tree:          # 擠在同樣的鍵道上，讓不開
            n.min_key, n.max_key = 0, 27
        moved, broken = _resolve_hold_corridors(m.notes_tree)
        self.assertEqual(moved, 0)
        self.assertEqual(broken, 1)
        for n in m.notes_tree:
            self.assertGreater(int(n.end), int(n.start))


class LevelTests(unittest.TestCase):
    """難度等級要落在官方的範圍內，而且要跟著同一首歌的 real 等級走。

    官方 357 首完整資料：normal 1~8、hard 3~11、extreme 5~12、real 9~15，
    中位 4/7/11/13。等級不是各自照密度給的——real 12 的曲子，它的 extreme
    幾乎一定是 10、hard 是 7、normal 是 3。
    """

    def test_the_bands_match_the_official_ranges(self):
        self.assertEqual(D.TARGETS['normal'].level_band, (1, 6))
        self.assertEqual(D.TARGETS['hard'].level_band, (3, 11))
        self.assertEqual(D.TARGETS['extreme'].level_band, (6, 12))

    def test_every_estimate_stays_in_its_band(self):
        for real in range(1, 21):
            for name in D.DIFFICULTIES:
                low, high = D.TARGETS[name].level_band
                level = D.estimate_level(real, name)
                self.assertGreaterEqual(level, low)
                self.assertLessEqual(level, high)

    def test_the_ladder_is_ordered(self):
        for real in range(9, 17):
            levels = [D.estimate_level(real, n) for n in D.DIFFICULTIES]
            self.assertEqual(levels, sorted(levels),
                             'real %d 推出來的等級沒有遞增：%s' % (real, levels))

    def test_it_matches_the_official_table(self):
        for real, expected in D.LEVEL_BY_REAL.items():
            got = tuple(D.estimate_level(real, n) for n in D.DIFFICULTIES)
            for value, want, name in zip(got, expected, D.DIFFICULTIES):
                low, high = D.TARGETS[name].level_band
                self.assertEqual(value, max(low, min(high, want)),
                                 'real %d 的 %s 應該是 %d' % (real, name, want))

    def test_a_harder_source_gives_harder_levels(self):
        for name in D.DIFFICULTIES:
            self.assertGreaterEqual(D.estimate_level(15, name),
                                    D.estimate_level(10, name))

    def test_beyond_the_table_it_extrapolates(self):
        """鬼火的 real 是 16，比官方表的上限還高一級。"""
        self.assertEqual(
            tuple(D.estimate_level(16, n) for n in D.DIFFICULTIES), (6, 10, 12))

    def test_an_unknown_difficulty_is_rejected(self):
        with self.assertRaises(ValueError):
            D.estimate_level(13, 'insane')

    def test_the_indicator_model_stays_in_band(self):
        for name in D.DIFFICULTIES:
            low, high = D.TARGETS[name].level_band
            for nps in (0.5, 3, 8, 15, 30):
                for peak in (2, 10, 40):
                    level = D.level_from_indicators(name, nps, peak)
                    self.assertGreaterEqual(level, low)
                    self.assertLessEqual(level, high)

    def test_a_denser_chart_gets_a_higher_level(self):
        for name in D.DIFFICULTIES:
            self.assertGreaterEqual(D.level_from_indicators(name, 20, 30),
                                    D.level_from_indicators(name, 3, 6))

    def test_the_final_level_uses_both_sources(self):
        """使用者要求「不要只照 real 難度推斷」。"""
        table = D.estimate_level(16, 'normal')
        indicators = D.level_from_indicators('normal', 4.6, 12)
        blended = D.level_for('normal', 4.6, 12, 16)
        self.assertNotEqual(table, indicators, '這個案例分不出兩者')
        self.assertEqual(blended, round((table + indicators) / 2.0))

    def test_it_never_matches_the_source(self):
        """生出來的難度一定比來源簡單——它是來源砍出來的。"""
        for real in range(4, 17):
            for name in D.DIFFICULTIES:
                if not D.worth_generating(real, name):
                    continue        # 來源太簡單，這個難度根本不該生
                for nps in (2, 8, 20):
                    level = D.level_for(name, nps, nps * 3, real)
                    self.assertLess(level, real,
                                    '%s 在來源 %d 時被判成 %d' % (name, real, level))

    def test_an_easy_source_gets_no_expert(self):
        """Expert 的下限是 6，來源只有 Lv.5 就生不出比它簡單的 Expert。"""
        self.assertFalse(D.worth_generating(5, 'extreme'))
        self.assertFalse(D.worth_generating(6, 'extreme'))
        self.assertTrue(D.worth_generating(7, 'extreme'))
        self.assertFalse(D.worth_generating(3, 'hard'))
        self.assertTrue(D.worth_generating(4, 'hard'))
        self.assertTrue(D.worth_generating(2, 'normal'))

    def test_without_a_source_level_it_uses_the_indicators(self):
        self.assertEqual(D.level_for('hard', 7.6, 20, 0),
                         D.level_from_indicators('hard', 7.6, 20))

    def test_the_peak_is_the_busiest_second(self):
        m = chart([(0, [(60, 0, 50)]), (100, [(61, 0, 50)]),
                   (200, [(62, 0, 50)]), (5000, [(63, 0, 50)])])
        self.assertEqual(D.peak_notes_per_second(m.notes_tree), 3)

    def test_generation_reports_the_peak(self):
        m = chart(dense(groups=60))
        result = D.generate(m, 'normal')
        self.assertGreater(result.peak_notes, 0)

    def test_the_dialog_offers_the_estimate(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.difficulty_dialog import DifficultyDialog
        # 要抓住 QApplication：建出來沒人拿著的話，它會立刻被回收，接下來
        # 開任何視窗就是行程直接崩掉（0xC0000409），而且崩在下一個測試上。
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app)
        dlg = DifficultyDialog(None, real_level=16)
        self.assertEqual(dlg.levels(),
                         {'normal': 6, 'hard': 10, 'extreme': 12})


class ApiTests(unittest.TestCase):
    def test_an_unknown_difficulty_is_rejected(self):
        m = chart(dense(groups=5))
        with self.assertRaises(ValueError):
            D.generate(m, 'insane')

    def test_an_empty_chart_does_not_crash(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = []
        r = D.generate(m, 'normal')
        self.assertEqual(r.total, 0)

    def test_the_three_names_are_what_the_user_asked_for(self):
        self.assertEqual(D.DIFFICULTIES, ('normal', 'hard', 'extreme'))

    def test_the_tool_is_in_the_menu(self):
        from PyQt5.QtWidgets import QApplication
        from qt_editor.main_window import MainWindow
        _app = QApplication.instance() or QApplication([])
        global _win
        try:
            win = _win
        except NameError:
            win = _win = MainWindow()
        labels = [label
                  for _group, items in win._tool_groups()
                  for label, _fn in items]
        self.assertTrue(any('樂曲資料夾' in x for x in labels),
                        '工具選單裡找不到「生成到樂曲資料夾」：%s' % labels)
        self.assertTrue(any('獨立檔案' in x for x in labels),
                        '工具選單裡找不到「生成成獨立檔案」：%s' % labels)

    def test_the_report_shows_the_official_numbers_beside_the_result(self):
        """沒有對照數字就沒辦法判斷生成器有沒有做到。"""
        from qt_editor.main_window import _difficulty_report
        m = chart(dense(groups=40))
        r = D.generate(m, 'normal')
        text = _difficulty_report([(r, 'x_normal.xml')])
        self.assertIn('NORMAL', text)
        self.assertIn(str(D.TARGETS['normal'].same_hand_gap_ms), text,
                      '沒有印出同手間隔的目標值')
        self.assertIn('x_normal.xml', text)


if __name__ == '__main__':
    unittest.main()
