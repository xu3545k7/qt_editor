"""跨手交界的推進量：左手最高音 → 右手最低音之間該隔幾格。

這條是三個症狀的共同來源——右手整體被推右、鍵道白白少 2 格、以及同一個音高
在不同和絃裡跳軌（右手的位置會隨左手最高音浮動）。

量 Eather 的譜 43336 組同時發聲的跨手配對，每一個音程都比程式碼要求的**少剛好
2 格**，差值正是 `hand_boundary_margin`：

    半音差   1~6  7~11  12~16  17~21  22~26  27~33
    他        3.0   4.0    6.0    8.0   10.0   12.0
    程式碼    5.0   5.8    7.8    9.9   12.0   14.0

拿掉 margin、把下限從 5 改成 3 之後，`interval * (5.0/12)` 直接命中每一格。
官方風格維持原本的 2.0 / 5.0（那是從官方語料量的，沒有理由跟著改）。
"""

import unittest

from qt_editor.smart_chart import (
    _chord_advance,
    _hand_sizes,
    settings_for_style,
)

# (半音差, Eather 實測中位數) —— 取自 43336 組配對的分段中位
MEASURED = [(3, 3.0), (9, 4.0), (14, 6.0), (19, 8.0), (24, 10.0), (30, 12.0)]


def advance(interval, style='eather', hands=(1, 0), widths=(3, 3)):
    return _chord_advance(
        1, [50, 50 + interval], list(hands), list(widths),
        _hand_sizes(list(hands)), settings_for_style(style),
    )


class EatherBoundaryTests(unittest.TestCase):
    def test_it_matches_what_he_actually_writes(self):
        for interval, measured in MEASURED:
            self.assertAlmostEqual(
                advance(interval), measured, delta=0.5,
                msg='%d 半音：要求 %.2f 格，他實際寫 %.1f'
                    % (interval, advance(interval), measured))

    def test_no_flat_margin_is_added(self):
        # 每一格都少 2 就是 margin 幹的；margin 還在的話這條會差 2 格
        self.assertEqual(settings_for_style('eather').hand_boundary_margin, 0.0)

    def test_close_intervals_are_not_forced_apart(self):
        # 舊的下限是 5.0，他寫 3.0
        self.assertLessEqual(advance(1), 3.5)
        self.assertLessEqual(advance(6), 3.5)

    def test_it_still_grows_with_the_interval(self):
        values = [advance(i) for i in (3, 9, 14, 19, 24)]
        self.assertEqual(values, sorted(values), '推進量該隨音程單調遞增')

    def test_it_is_capped_so_a_huge_leap_cannot_eat_the_keyboard(self):
        self.assertLessEqual(advance(40), 13.0)

    def test_same_hand_is_untouched(self):
        # 這條只管跨手；同手走階梯表
        same = _chord_advance(1, [50, 62], [0, 0], [3, 3],
                              _hand_sizes([0, 0]), settings_for_style('eather'))
        self.assertAlmostEqual(same, 5.0, delta=0.01)


class OfficialStyleUnchanged(unittest.TestCase):
    """官方那組值是從官方語料量的，不該跟著 Eather 改。"""

    def test_the_margin_survives(self):
        settings = settings_for_style('official')
        self.assertEqual(settings.hand_boundary_margin, 2.0)
        self.assertEqual(settings.hand_boundary_min_distance, 5.0)

    def test_official_stays_two_lanes_wider(self):
        for interval, _ in MEASURED:
            gap = advance(interval, 'official') - advance(interval, 'eather')
            self.assertAlmostEqual(gap, 2.0, delta=0.01,
                                   msg='%d 半音的差值變了' % interval)



class SnapMustNotFlattenTests(unittest.TestCase):
    """吸附不得把相鄰的不同音高壓到同一軌。

    吸附是整隻手剛體平移，會把旁邊不相干的音一起拖過去。實測 3 半音的壓平率
    因此從 0.0% 衝到 8.3%（Eather 的譜是 3.0%），而且一眼就看得出來——使用者的
    原話是「68 到 73 完全沒有階梯」「44 和 49 一直共用同軌」。

    門檻 3：半音和全音壓平是他自己也在做的（1 半音 20.2%、2 半音 5.0%），
    那種本來就不需要各佔一軌，擋掉反而會把同軌率一起賠掉。
    """

    def test_eather_forbids_flattening_thirds_and_wider(self):
        self.assertEqual(
            settings_for_style('eather').snap_flat_min_semitones, 3)

    def test_official_is_untouched(self):
        self.assertEqual(
            settings_for_style('official').snap_flat_min_semitones, 0)

    def test_the_threshold_leaves_semitones_alone(self):
        # 門檻若降到 1 或 2 就會擋掉他自己的做法
        self.assertGreaterEqual(
            settings_for_style('eather').snap_flat_min_semitones, 3)
if __name__ == '__main__':
    unittest.main()
