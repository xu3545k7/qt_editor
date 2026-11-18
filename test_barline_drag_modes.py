# -*- coding: utf-8 -*-
"""拖曳小節線的兩個模式。

使用者：「拖曳小節線開啟後應該有兩個模式……如果鎖定音符應該是可以開吸附拍點
……根據拍子縮放拖曳的小節線和上一個小節線之間的拍子……兩個都要能開關。」

兩顆開關，都掛在「拖曳小節線」那顆按鈕的下拉裡：

* **吸附拍點** —— 候選是三種點的聯集、取最近的（使用者：「兩種都吸，取最近的」）：
  音符終點、音符起音點、存檔裡的拍線。只在「鎖定音符」開著時會吸；不鎖定時
  音符跟著縮放，吸附沒有意義（使用者定案：「不要做任何吸附」）。
* **縮放小節內的拍點** —— 拖完之後小節內的拍線保留相對位置等比例縮放，而不是
  依拍號均分。這條路以前**寫死均分**，而「修改小節 BPM」對話框卻是看檢視模式
  （`time_uniform`）決定——同一件事兩種結果。

這裡釘住的是「哪些點會被吸」「關掉就真的不吸」「兩種重排方式真的不一樣」，
因為這三件事改掉都不會當掉，只會讓拖出來的格線悄悄跑掉。
"""

import unittest

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor.models import GNote, NoteModel          # noqa: E402


def make(idx, start, dur=200):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch, n.hand, n.note_type = 60, 0, 0
    n.velocity = 90
    n.min_key, n.max_key = 4, 4
    return n


def chart(bars=12):
    m = NoteModel.create_new('t', 120.0, bars * 2.0, 4)
    m.ensure_precise_beat_grid()
    m.notes_tree = [make(i, 1000 + 500 * i) for i in range(8)]
    m.rebuild_display_cache()
    return m


def view(locked=True, snap=True, scale=True):
    from qt_editor.chart_view import ChartView
    v = ChartView()
    v.resize(1200, 720)
    v.load_model(chart())
    v.rebuild_mapper()
    v.barline_drag_enabled = True
    v.notes_locked = locked
    v.barline_snap_enabled = snap
    v.barline_scale_beats = scale
    return v


class WhatItSnapsToTests(unittest.TestCase):
    """使用者：「兩種都吸，取最近的」——音符終點、起音點、拍線都算。"""

    def test_a_note_end_is_a_target(self):
        v = view()
        kinds = dict(v._barline_snap_targets())
        self.assertEqual(kinds.get(1200), '音符尾')

    def test_a_note_start_is_a_target(self):
        v = view()
        kinds = dict(v._barline_snap_targets())
        self.assertEqual(kinds.get(1500), '起音')

    def test_a_beat_line_is_a_target(self):
        """拍線也要能吸——想讓小節線落在既有的細分格上時就是要這個。"""
        v = view()
        kinds = dict(v._barline_snap_targets())
        beats = [int(ms) for _i, ms in v.model.get_beat_entries()]
        self.assertTrue(beats, '這份測試譜沒有拍線，前提就不成立')
        self.assertTrue(any(kinds.get(b) == '拍線' for b in beats),
                        '一條拍線都沒有進候選')

    def test_a_note_end_wins_over_a_beat_line_at_the_same_ms(self):
        """同一個毫秒上「對上尾拍」比「對上一條線」具體，留原本的說法。"""
        v = view()
        beats = [int(ms) for _i, ms in v.model.get_beat_entries()]
        v.model.notes_tree.append(make(99, beats[2] - 200))   # end 正好壓在拍線上
        v._snap_targets_cache = None
        self.assertEqual(dict(v._barline_snap_targets()).get(beats[2]), '音符尾')

    def test_the_targets_follow_the_beat_grid(self):
        """改過 BPM 之後不能還吸到舊的拍線位置（快取簽章要含拍線）。"""
        v = view()
        before = set(ms for ms, _k in v._barline_snap_targets())
        v.model.set_measure_bpm(2, 150.0, uniform=False, adjust_notes=False)
        self.assertNotEqual(set(ms for ms, _k in v._barline_snap_targets()), before)


class SnappingCanBeTurnedOffTests(unittest.TestCase):
    def test_on_it_snaps(self):
        v = view(snap=True)
        ms, hit = v._barline_snap(1200 + 20)
        self.assertEqual(hit, 1200)
        self.assertEqual(ms, 1200.0)
        self.assertEqual(v._barline_snap_kind, '音符尾')

    def test_off_it_does_not(self):
        v = view(snap=False)
        ms, hit = v._barline_snap(1200 + 20)
        self.assertIsNone(hit)
        self.assertEqual(ms, 1220.0)
        self.assertEqual(v._barline_snap_kind, '')

    def test_too_far_away_is_left_alone(self):
        """使用者要的是「順手對上」，不是「被拉去很遠的地方」。"""
        v = view(snap=True)
        far = 1200 + v.BARLINE_SNAP_MS + 40
        _ms, hit = v._barline_snap(far)
        self.assertIsNone(hit)

    def test_not_locking_notes_never_snaps(self):
        """不鎖定音符時音符跟著縮放，吸附沒有意義（使用者定案）。"""
        v = view(locked=False, snap=True)
        self.assertEqual(v._barline_snap_targets(), [])
        _ms, hit = v._barline_snap(1200)
        self.assertIsNone(hit)


class HowTheInnerBeatsAreRearrangedTests(unittest.TestCase):
    """小節**內**的拍線：等比例縮放 vs 依拍號均分，是兩種不同的結果。"""

    def inner(self, model, measure_idx=2):
        lo, hi = model.get_measure_time_range(measure_idx)
        return [int(ms) for _i, ms in model.get_beat_entries()
                if lo is not None and lo < int(ms) < hi]

    def nudged(self):
        """把一小節裡的一條拍線手動挪開，兩種做法才分得出來。"""
        m = chart()
        lo, hi = m.get_measure_time_range(2)
        beats = [[int(i), int(ms)] for i, ms in m.get_beat_entries()]
        moved = False
        for row in beats:
            if lo < row[1] < hi:
                row[1] = int(row[1] + (hi - lo) * 0.12)       # 往後挪一點
                moved = True
                break
        self.assertTrue(moved, '這一小節裡沒有可以挪的拍線')
        m._write_beat_entries([(i, ms) for i, ms in beats], mark_precise=True)
        return m

    def test_scaling_keeps_the_nudge(self):
        m = self.nudged()
        lo, hi = m.get_measure_time_range(2)
        rel_before = [(b - lo) / float(hi - lo) for b in self.inner(m)]
        m.set_measure_bpm(2, 150.0, uniform=False, adjust_notes=False)
        lo2, hi2 = m.get_measure_time_range(2)
        rel_after = [(b - lo2) / float(hi2 - lo2) for b in self.inner(m)]
        self.assertEqual(len(rel_before), len(rel_after))
        for a, b in zip(rel_before, rel_after):
            self.assertAlmostEqual(a, b, delta=0.01,
                                   msg='等比例縮放不該改變相對位置')

    def test_uniform_pulls_it_back_onto_the_grid(self):
        m = self.nudged()
        lo, hi = m.get_measure_time_range(2)
        rel_before = [(b - lo) / float(hi - lo) for b in self.inner(m)]
        m.set_measure_bpm(2, 150.0, uniform=True, adjust_notes=False)
        lo2, hi2 = m.get_measure_time_range(2)
        rel_after = [(b - lo2) / float(hi2 - lo2) for b in self.inner(m)]
        self.assertNotEqual([round(x, 3) for x in rel_before],
                            [round(x, 3) for x in rel_after],
                            '均分應該把挪過的拍線拉回規則格上')

    def test_the_drag_asks_for_scaling_not_uniform(self):
        """以前這條路寫死 uniform=True。別再寫回去。"""
        import inspect
        from qt_editor import chart_view
        src = inspect.getsource(chart_view.ChartView.mouseReleaseEvent)
        self.assertIn('uniform=not self.barline_scale_beats', src)


class TheOptionsLiveUnderTheDragToggleTests(unittest.TestCase):
    def window(self):
        import contextlib
        import io as _io
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            win = MainWindow()
        self.addCleanup(lambda: (setattr(win.view.model, 'dirty', False), win.close()))
        return win

    def test_both_options_are_offered(self):
        win = self.window()
        acts = win._toolbars[0].barline_option_acts
        self.assertEqual(sorted(acts), ['barline_scale_beats', 'barline_snap_enabled'])
        for act in acts.values():
            self.assertTrue(act.isCheckable())

    def test_there_is_no_dropdown_until_you_turn_it_on(self):
        """使用者：「要打開才可以開下拉選單」。

        打得開卻全是灰色的選單只會讓人困惑——看得到東西、按不下去、也沒有半句
        話說明為什麼。沒有 menu 時 Qt 連那個箭頭都不畫。
        """
        win = self.window()
        btn = win._toolbars[0].barline_drag_btn
        win._on_barline_drag_toggled(False)
        self.assertIsNone(btn.menu(), '關著的時候不該有下拉')
        win._on_barline_drag_toggled(True)
        self.assertIsNotNone(btn.menu(), '開著就要有下拉')

    def test_the_toolbar_does_not_jump_when_it_appears(self):
        """箭頭長出來不該把右邊的按鈕全部推走。"""
        win = self.window()
        btn = win._toolbars[0].barline_drag_btn
        win._on_barline_drag_toggled(False)
        narrow = btn.sizeHint().width()
        win._on_barline_drag_toggled(True)
        self.assertEqual(btn.sizeHint().width(), narrow)

    def test_the_options_are_still_disabled_when_off(self):
        """第二道保險：萬一選單被別的路徑掛上去，項目也按不動。"""
        win = self.window()
        win._on_barline_drag_toggled(False)
        acts = win._toolbars[0].barline_option_acts
        self.assertFalse(any(a.isEnabled() for a in acts.values()))
        win._on_barline_drag_toggled(True)
        self.assertTrue(all(a.isEnabled() for a in acts.values()))

    def test_the_labels_say_what_they_do(self):
        """使用者：「那些選項代表啥」。選單項目要自己把做什麼說完，
        不能只寫在滑鼠停上去才看得到的提示裡。"""
        win = self.window()
        win._on_barline_drag_toggled(True)
        labels = [a.text() for a in win._toolbars[0].barline_drag_btn.menu().actions()
                  if not a.isSeparator()]
        self.assertTrue(any('吸附' in x and ('音符' in x or '拍線' in x) for x in labels),
                        '吸附那一項沒講吸到什麼：%r' % labels)
        self.assertTrue(any('縮放' in x and '均分' in x for x in labels),
                        '縮放那一項沒講關掉會怎樣：%r' % labels)

    def test_the_menu_says_what_the_group_is_about(self):
        win = self.window()
        win._on_barline_drag_toggled(True)
        heads = [a.text() for a in win._toolbars[0].barline_drag_btn.menu().actions()
                 if a.isSeparator() and a.text()]
        self.assertTrue(heads, '選單最上面沒有說明這一組在管什麼')

    def test_a_freshly_built_toolbar_matches_the_setting(self):
        """下拉的有無是在建構途中決定的，不能靠事後刷新（那時這條工具列
        還沒註冊進 `_toolbars`）。"""
        from qt_editor.settings import settings as S
        for want in (False, True):
            S.set('barline_drag_enabled', bool(want))
            win = self.window()
            btn = win._toolbars[0].barline_drag_btn
            self.assertEqual(btn.menu() is not None, bool(want),
                             '設定是 %s，下拉卻相反' % want)
        S.set('barline_drag_enabled', False)

    def test_the_button_tip_points_at_the_arrow(self):
        """還沒開的人要知道「開了之後會多一個箭頭」。"""
        win = self.window()
        self.assertIn('箭頭', win._toolbars[0].barline_drag_act.toolTip())

    def test_toggling_reaches_every_pane(self):
        """每一格 ChartView 各有旗標，漏掉一格就會像沒生效。"""
        win = self.window()
        win._on_barline_option_toggled('barline_snap_enabled', False)
        self.assertTrue(all(not p.barline_snap_enabled for p in win._panes))
        win._on_barline_option_toggled('barline_snap_enabled', True)
        self.assertTrue(all(p.barline_snap_enabled for p in win._panes))

    def test_both_default_on(self):
        win = self.window()
        self.assertTrue(win._panes[0].barline_snap_enabled)
        self.assertTrue(win._panes[0].barline_scale_beats)

    def test_the_tip_tells_you_the_snapping_can_be_turned_off(self):
        from qt_editor.main_window import MainWindow
        self.assertIn('下拉', MainWindow._notes_locked_tip())


if __name__ == '__main__':
    unittest.main()
