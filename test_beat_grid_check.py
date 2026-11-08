# -*- coding: utf-8 -*-
"""拍線體檢：量化之前先判斷這份譜的格線能不能信。

使用者回饋：「時間戳好像有點點問題，有的稍微偏離了節拍線有的時間靠得比較近，
但是製譜器告訴我所有的 note 是量化好的，我不太懂這是哪方面的問題。」

那句「本來就在格線上」沒說錯——它比對的是製譜器自己算出來的格線，而量化的
格線是「把相鄰兩條拍線之間切成 N 等分」。拍線間距一旦不均勻，子格線就寬度
不一，音符落上去之後間隔長短不一，但它們確實都在格線上。這個模組負責在量化
之前把這件事講出來。

最難的一件事是**分出「真的速度變化」和「壞掉的拍點」**，因為處理方式相反：
前者不能動，後者要補。所以這裡大半的測試在釘這條界線，另外兩組在釘「譜面
頭尾的不完整拍不算壞掉」——不排除的話每一份譜都會被誤報。
"""

import unittest

from qt_editor import beat_grid_check as bgc
from qt_editor.models import GNote, NoteModel


def even(step, n, start=0):
    """等距拍線。"""
    return [start + int(round(step * i)) for i in range(n)]


def report(lines, bpm=120.0):
    return bgc.check(lines, bpm)


class EvenGridTests(unittest.TestCase):
    """等距的格線不該有任何意見。"""

    def test_a_clean_grid_says_nothing(self):
        r = report(even(500, 40))              # 120 BPM 一拍一筆
        self.assertTrue(r.enough)
        self.assertTrue(r.even)
        self.assertTrue(r.trustworthy)
        self.assertEqual(r.spikes, [])
        self.assertEqual(r.warnings(), [])
        self.assertEqual(r.notes(), [])
        self.assertIn('完全等距', r.summary())

    def test_whole_milliseconds_are_not_a_complaint(self):
        """210 BPM 一拍 285.714ms，拍線只能是 285/286 交替——那不是病。"""
        r = report(even(60000.0 / 210, 60), bpm=210.0)
        self.assertEqual(r.spikes, [])
        self.assertEqual(r.warnings(), [])

    def test_one_entry_per_bar_is_fine(self):
        r = report(even(2000, 30), bpm=120.0)   # 一小節一筆 = 4 拍
        self.assertFalse(r.bpm_mismatch)
        self.assertAlmostEqual(r.bpm_ratio, 4.0, places=2)

    def test_an_eighth_note_grid_is_fine(self):
        r = report(even(250, 40), bpm=120.0)    # 八分一筆 = 0.5 拍
        self.assertFalse(r.bpm_mismatch)
        self.assertAlmostEqual(r.bpm_ratio, 0.5, places=2)

    def test_a_six_eight_eighth_grid_is_fine(self):
        r = report(even(200, 40), bpm=150.0)    # 6/8 的八分音符
        self.assertFalse(r.bpm_mismatch)


class MissingPointTests(unittest.TestCase):
    """漏了拍點：間距是鄰居的兩倍。"""

    def setUp(self):
        self.lines = even(414, 40)
        # 把第 20 條拿掉 → 第 19 個間距變成兩倍
        self.gone = self.lines[20]
        del self.lines[20]
        self.r = report(self.lines, bpm=145.0)

    def test_it_is_found(self):
        self.assertEqual(len(self.r.missing), 1)
        self.assertEqual(self.r.extra, [])

    def test_the_ratio_is_about_two(self):
        self.assertAlmostEqual(self.r.missing[0].ratio, 2.0, places=1)

    def test_it_points_at_the_gap_not_past_it(self):
        """漏掉的線落在這一段裡面，所以指左端讓使用者往後找。"""
        s = self.r.missing[0]
        self.assertLess(s.at_ms, self.gone)
        self.assertGreater(s.to_ms, self.gone)
        self.assertEqual(s.where, s.at_ms)

    def test_it_is_not_trustworthy(self):
        self.assertFalse(self.r.trustworthy)
        self.assertFalse(self.r.even)
        self.assertFalse(self.r.rubato)

    def test_the_warning_says_what_it_is(self):
        w = self.r.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn('漏了拍點', w[0])
        self.assertIn(bgc.fmt_ms(self.r.missing[0].where), w[0])

    def test_several_are_counted(self):
        lines = even(414, 60)
        for i in (45, 30, 15):          # 從後往前刪，索引才不會跑掉
            del lines[i]
        r = report(lines, bpm=145.0)
        self.assertEqual(len(r.missing), 3)
        self.assertIn('有 3 處', r.warnings()[0])


class ExtraPointTests(unittest.TestCase):
    """多了一條拍線：間距只有鄰居的一小部分。"""

    def setUp(self):
        self.lines = even(500, 40)
        self.added = self.lines[20] + 58       # UFO_Real_11 的實際樣子
        self.lines.append(self.added)
        self.lines.sort()
        self.r = report(self.lines, bpm=120.0)

    def test_it_is_found(self):
        self.assertEqual(len(self.r.extra), 1)
        self.assertEqual(self.r.missing, [])

    def test_it_points_at_the_extra_line_itself(self):
        self.assertEqual(self.r.extra[0].where, self.added)
        self.assertEqual(self.r.extra[0].to_ms, self.added)

    def test_the_warning_says_what_it_is(self):
        w = self.r.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn('多出一條拍線', w[0])
        self.assertIn(bgc.fmt_ms(self.added), w[0])

    def test_a_doubled_line_is_also_caught(self):
        """間距剛好一半（多插在正中間）也算。"""
        lines = even(500, 40)
        lines.append(lines[20] + 250)
        lines.sort()
        r = report(lines, bpm=120.0)
        self.assertEqual(len(r.extra), 1)


class ChartEndsTests(unittest.TestCase):
    """譜面頭尾的不完整拍是正常資料，不是壞掉。"""

    def test_a_pickup_bar_is_not_an_extra_line(self):
        """`one_voice`：第一個間距 909ms、其餘 1818ms，弱起小節。"""
        lines = [0] + even(1818, 40, start=909)
        r = report(lines, bpm=132.0)
        self.assertEqual(r.spikes, [])
        self.assertEqual(r.warnings(), [])

    def test_a_music_end_marker_is_not_an_extra_line(self):
        """`Immaculate` 最後一個間距 43ms、`UFO_Real_11` 58ms。"""
        lines = even(300, 40)
        lines.append(lines[-1] + 43)
        r = report(lines, bpm=200.0)
        self.assertEqual(r.spikes, [])
        self.assertEqual(r.warnings(), [])

    def test_both_ends_at_once(self):
        lines = [0] + even(1846, 40, start=923)
        lines.append(lines[-1] + 50)
        r = report(lines, bpm=130.0)
        self.assertEqual(r.spikes, [])

    def test_a_real_break_next_to_the_end_still_counts(self):
        """排除的只有最後一個間距，倒數第二個還是要看。"""
        lines = even(300, 40)
        lines.insert(len(lines) - 1, lines[-2] + 43)
        r = report(lines, bpm=200.0)
        self.assertEqual(len(r.extra), 1)


class RubatoTests(unittest.TestCase):
    """平滑的速度變化：拍線本來就該動，量化照它切分是對的。"""

    def setUp(self):
        # 每一條慢 1.5%，走完 40 條大約慢了 1.8 倍——但每一步都和鄰居差不多
        lines, t, step = [0], 0.0, 400.0
        for _ in range(39):
            t += step
            step *= 1.015
            lines.append(int(round(t)))
        self.r = report(lines, bpm=150.0)

    def test_it_is_not_called_broken(self):
        self.assertEqual(self.r.spikes, [])
        self.assertEqual(self.r.warnings(), [])

    def test_it_is_still_flagged_as_uneven(self):
        self.assertTrue(self.r.rubato)
        self.assertFalse(self.r.even)
        self.assertGreater(self.r.rough, 0)

    def test_quantizing_it_is_trustworthy(self):
        """沒有斷點，所以照每段實際寬度切分是對的。"""
        self.assertTrue(self.r.trustworthy)

    def test_it_explains_why_the_gaps_will_differ(self):
        n = self.r.notes()
        self.assertEqual(len(n), 1)
        self.assertIn('速度變化', n[0])

    def test_a_spike_beats_a_ramp(self):
        """同時有起伏和斷點時，斷點才是重點（notes 讓位給 warnings）。"""
        lines, t, step = [0], 0.0, 400.0
        for _ in range(39):
            t += step
            step *= 1.015
            lines.append(int(round(t)))
        del lines[20]
        r = report(lines, bpm=150.0)
        self.assertTrue(r.warnings())
        self.assertEqual(r.notes(), [])


class BpmMismatchTests(unittest.TestCase):
    """拍線間距對不上宣告的 BPM：整個格線在錯的速度上。"""

    def test_an_odd_ratio_is_reported(self):
        """`旅`：間距中位 1233ms、宣告 120 BPM = 2.47 拍。"""
        r = report(even(1233, 40), bpm=120.0)
        self.assertTrue(r.bpm_mismatch)
        w = [s for s in r.warnings() if '對不上宣告的 BPM' in s]
        self.assertEqual(len(w), 1)
        self.assertIn('2.47', w[0])

    def test_it_makes_the_grid_untrustworthy(self):
        self.assertFalse(report(even(1233, 40), bpm=120.0).trustworthy)

    def test_a_simple_multiple_is_accepted(self):
        for step, ratio in ((500, 1.0), (1000, 2.0), (2000, 4.0),
                            (250, 0.5), (3000, 6.0)):
            with self.subTest(step=step):
                r = report(even(step, 40), bpm=120.0)
                self.assertFalse(r.bpm_mismatch, '%d ms 被誤報' % step)
                self.assertAlmostEqual(r.bpm_ratio, ratio, places=2)

    def test_triplet_grids_are_accepted(self):
        r = report(even(500.0 / 3, 40), bpm=120.0)
        self.assertFalse(r.bpm_mismatch)

    def test_without_a_bpm_it_stays_quiet(self):
        r = report(even(1233, 40), bpm=0.0)
        self.assertFalse(r.bpm_mismatch)
        self.assertEqual(r.warnings(), [])


class NotEnoughDataTests(unittest.TestCase):
    """樣本太少就不要下結論。"""

    def test_a_handful_of_lines_says_nothing(self):
        r = report(even(500, 4))
        self.assertFalse(r.enough)
        self.assertEqual(r.warnings(), [])
        self.assertEqual(r.notes(), [])
        self.assertIn('不足以判斷', r.summary())

    def test_no_lines_at_all(self):
        r = report([])
        self.assertFalse(r.enough)
        self.assertEqual(r.lines, 0)
        self.assertEqual(r.warnings(), [])

    def test_duplicates_are_collapsed(self):
        r = report(even(500, 20) + even(500, 20))
        self.assertEqual(r.lines, 20)


class FormatTests(unittest.TestCase):
    def test_time_is_readable(self):
        self.assertEqual(bgc.fmt_ms(0), '0:00.000')
        self.assertEqual(bgc.fmt_ms(1750), '0:01.750')
        self.assertEqual(bgc.fmt_ms(190898), '3:10.898')

    def test_negative_is_clamped(self):
        self.assertEqual(bgc.fmt_ms(-5), '0:00.000')


class ModelTests(unittest.TestCase):
    """接得上真的 NoteModel。"""

    def model(self, lines, bpm=120.0):
        m = NoteModel.create_new('t', bpm, 60.0, 4)
        m._epb_mode = None
        m._epb_count = None
        m.json_meta = {}
        m._write_beat_data_pairs([(i, int(t)) for i, t in enumerate(lines)])
        n = GNote(None, 0)
        n.start, n.end, n.gate = 0, 400, 400
        n.min_key, n.max_key, n.note_type, n.hand = 4, 6, 0, 0
        n.pitch, n.velocity = 60, 90
        m.notes_tree = [n]
        m.rebuild_display_cache()
        return m

    def test_a_clean_model_passes(self):
        r = bgc.check_model(self.model(even(500, 40)))
        self.assertTrue(r.enough)
        self.assertEqual(r.warnings(), [])

    def test_a_broken_model_is_caught(self):
        lines = even(500, 40)
        del lines[20]
        r = bgc.check_model(self.model(lines))
        self.assertEqual(len(r.missing), 1)

    def test_it_reads_the_bpm_off_the_model(self):
        r = bgc.check_model(self.model(even(1233, 40), bpm=120.0))
        self.assertEqual(r.bpm, 120.0)
        self.assertTrue(r.bpm_mismatch)

    def test_rubbish_in_does_not_raise(self):
        class Dud:
            bpm = 120.0

            def get_beat_entries(self):
                raise RuntimeError('壞了')

        r = bgc.check_model(Dud())
        self.assertFalse(r.enough)
        self.assertEqual(r.warnings(), [])


if __name__ == '__main__':
    unittest.main()
