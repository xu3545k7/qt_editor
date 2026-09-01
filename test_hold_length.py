import unittest

from qt_editor.models import (
    classify_hold_length, hold_fix_candidate,
    LONG_BIT, SKIN_BIT, TRILL_NOTE_TYPE, SLIDE_NOTE_TYPE,
    STACCATO_NOTE_TYPE, SOFT_NOTE_TYPE,
)


class ClassifyHoldLengthTests(unittest.TestCase):
    def test_tiers(self):
        # tap_th = 1/8 (0.5 拍), hold_th = 1/4 (1.0 拍)
        self.assertEqual(classify_hold_length(0.25, 0.5, 1.0), 'tap')
        self.assertEqual(classify_hold_length(0.5, 0.5, 1.0), 'mid')   # 邊界含下界
        self.assertEqual(classify_hold_length(0.75, 0.5, 1.0), 'mid')
        self.assertEqual(classify_hold_length(1.0, 0.5, 1.0), 'long')  # 邊界含下界
        self.assertEqual(classify_hold_length(4.0, 0.5, 1.0), 'long')


class HoldFixCandidateTests(unittest.TestCase):
    def test_only_pure_holds(self):
        self.assertTrue(hold_fix_candidate(LONG_BIT))           # 2 純長押
        self.assertTrue(hold_fix_candidate(LONG_BIT | SKIN_BIT))  # 10 長押+skin
        # 非處理對象
        self.assertFalse(hold_fix_candidate(0))                 # tap
        self.assertFalse(hold_fix_candidate(SOFT_NOTE_TYPE))    # soft
        self.assertFalse(hold_fix_candidate(STACCATO_NOTE_TYPE))  # staccato(3)
        self.assertFalse(hold_fix_candidate(SLIDE_NOTE_TYPE))   # slide
        self.assertFalse(hold_fix_candidate(TRILL_NOTE_TYPE))   # trill

    def test_tap_conversion_keeps_skin_bit(self):
        # 轉 tap = 清 LONG_BIT，保留其它位元
        self.assertEqual((LONG_BIT | SKIN_BIT) & ~LONG_BIT, SKIN_BIT)
        self.assertEqual(LONG_BIT & ~LONG_BIT, 0)


if __name__ == '__main__':
    unittest.main()
