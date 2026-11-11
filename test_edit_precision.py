# -*- coding: utf-8 -*-
"""編輯精度（時值下拉）：常駐、放在「常用」、兩顆同步。

使用者：「請把編輯經度放在常用 使其在放置以外的情況也可以做調整」

為什麼它不該跟著放置模式收起來：那顆下拉管的不只是「放下去的音符多長」——

    • 格線的間隔（`ChartView.grid_layers` 的「跟著編輯精度」那一層）
    • 長條尾端拖拉、往上拖拉長縮短時一格有多大（`ChartView.drag_step_beats`）

這兩件事在沒開放置模式的時候照樣會用到，而原本它被放進 `note_input_group`、
關了放置模式整組 `setVisible(False)`，所以根本改不到。
"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication, QComboBox, QLabel, QMenu

_app = QApplication.instance() or QApplication([])


class PrecisionToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def tbs(self):
        return self.win._toolbars[0]

    # ── 常駐 ──────────────────────────────────────────────────
    def test_it_is_not_in_the_group_that_hides_with_placement(self):
        tbs = self.tbs()
        self.assertNotIn(tbs.dur_combo, tbs.note_input_group,
                         '時值被收進放置那組的話，關了放置模式就改不到')
        for combo in (tbs.hand_combo, tbs.width_combo, tbs.type_combo):
            self.assertIn(combo, tbs.note_input_group,
                          '手／寬度／類型確實只有放置時才有意義，維持收起來')

    def test_it_stays_visible_when_placement_is_off(self):
        """用 `isVisibleTo` 不是 `isVisible`：測試裡視窗沒 show，`isVisible()`
        一律是 False，拿它斷言等於什麼都沒驗。`isVisibleTo` 看的是有沒有被
        明確藏起來，那才是 `_refresh_toolbar_groups` 做的事。"""
        tbs = self.tbs()
        tbs.note_input_act.setChecked(False)
        self.win._refresh_toolbar_groups()

        def shown(w):
            return w.isVisibleTo(w.parentWidget())

        for combo in (tbs.hand_combo, tbs.width_combo, tbs.type_combo):
            self.assertFalse(shown(combo), '放置參數關了就該收起來')
        self.assertTrue(shown(tbs.dur_combo), '精度不准跟著被藏起來')
        self.assertTrue(shown(tbs.dur_label))
        self.assertTrue(shown(tbs.grid_btn), '格線設定本來就不跟著放置模式')

        # 反面：開了放置模式，那三顆要回來，精度照樣在
        tbs.note_input_act.setChecked(True)
        self.win._refresh_toolbar_groups()
        for combo in (tbs.hand_combo, tbs.width_combo, tbs.type_combo):
            self.assertTrue(shown(combo))
        self.assertTrue(shown(tbs.dur_combo))

    def test_changing_it_with_placement_off_really_reaches_the_view(self):
        """重點：關著放置模式也要真的改到譜面的拖拉單位。"""
        self.tbs().note_input_act.setChecked(False)
        self.win._refresh_toolbar_groups()
        items = self.win._note_dur_items
        quarter = next(i for i, (_n, b) in enumerate(items) if abs(b - 1.0) < 1e-9)
        sixteenth = next(i for i, (_n, b) in enumerate(items) if abs(b - 0.25) < 1e-9)
        self.win._on_dur_combo_changed(quarter)
        self.assertAlmostEqual(self.win.view.drag_step_beats(), 1.0, places=6)
        self.win._on_dur_combo_changed(sixteenth)
        self.assertAlmostEqual(self.win.view.drag_step_beats(), 0.25, places=6)

    def test_it_has_a_label_of_its_own(self):
        tbs = self.tbs()
        self.assertIsNotNone(tbs.dur_label)
        self.assertEqual(tbs.dur_label.text(), '精度')

    def test_the_tip_no_longer_says_placement_only(self):
        tip = self.tbs().dur_combo.toolTip()
        self.assertNotIn('放置音符模式下生效', tip)
        self.assertIn('精度', tip)

    # ── 「常用」分頁 ──────────────────────────────────────────
    def common_page(self):
        tbs = self.tbs()
        return self.win._build_tab_common(tbs), tbs

    def test_the_common_tab_pins_a_precision_combo(self):
        page, tbs = self.common_page()
        mirrors = tbs.mirror_combos.get('dur_combo') or []
        self.assertEqual(len(mirrors), 1, '常用上要有一顆精度下拉')
        self.assertIsInstance(mirrors[0], QComboBox)
        labels = [w.text() for w in page.findChildren(QLabel)]
        self.assertIn('精度', labels)
        page.deleteLater()

    def test_the_pinned_combo_is_listed_for_syncing(self):
        _page, tbs = self.common_page()
        found = self.win._combos_named('dur_combo')
        self.assertIn(tbs.dur_combo, found, '本尊要在裡面')
        for mirror in tbs.mirror_combos['dur_combo']:
            self.assertIn(mirror, found, '鏡像也要在裡面')

    def test_rebuilding_the_tab_drops_the_old_mirror(self):
        """常用每次切過去都重建。留著舊的參照會碰到已刪除的 C++ 物件。"""
        _p1, tbs = self.common_page()
        first = tbs.mirror_combos['dur_combo'][0]
        _p2, tbs = self.common_page()
        self.assertEqual(len(tbs.mirror_combos['dur_combo']), 1,
                         '登記簿沒清掉，鏡像會越疊越多')
        self.assertIsNot(tbs.mirror_combos['dur_combo'][0], first)

    def test_the_two_combos_stay_in_step(self):
        _page, tbs = self.common_page()
        mirror = tbs.mirror_combos['dur_combo'][0]
        items = self.win._note_dur_items
        eighth = next(i for i, (_n, b) in enumerate(items) if abs(b - 0.5) < 1e-9)
        self.win._on_dur_combo_changed(eighth)
        self.assertEqual(tbs.dur_combo.currentIndex(), eighth)
        self.assertEqual(mirror.currentIndex(), eighth, '常用那顆沒跟上')

    def test_the_mirror_offers_the_same_items(self):
        _page, tbs = self.common_page()
        mirror = tbs.mirror_combos['dur_combo'][0]
        self.assertEqual([mirror.itemText(i) for i in range(mirror.count())],
                         [tbs.dur_combo.itemText(i)
                          for i in range(tbs.dur_combo.count())])

    def test_the_empty_hint_still_appears_when_no_buttons_earned_a_place(self):
        """釘住的精度不該讓「還是空的」那句話消失。"""
        saved = self.win._toolbar_usage.top
        self.win._toolbar_usage.top = lambda: []
        try:
            page, _tbs = self.common_page()
            text = ' '.join(w.text() for w in page.findChildren(QLabel))
            self.assertIn('按鈕還是空的', text)
            self.assertIn('精度', text, '提示出現時精度還是要在')
            page.deleteLater()
        finally:
            self.win._toolbar_usage.top = saved

    # ── 格線選單跟著改名 ──────────────────────────────────────
    def test_the_grid_menu_uses_the_same_name(self):
        """同一個東西在介面上不要兩個名字。"""
        menu = QMenu()
        self.win._fill_grid_menu(menu)
        texts = [a.text() for a in menu.actions()]
        menu.deleteLater()
        self.assertIn('跟著編輯精度', texts)
        self.assertNotIn('跟著放置時值', texts)


if __name__ == '__main__':
    unittest.main()
