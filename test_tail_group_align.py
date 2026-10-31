# -*- coding: utf-8 -*-
"""「同 start 的尾端一致」在介面上的兩個入口。

使用者：「長押尾端一致（又是選單）改成在終止時間調整加上同 start 一致最晚和
最早的按鈕，並且放在上面（比較常用）」。所以位置本身就是需求的一部分——
**要在最上面**。

兩個入口同一個操作：右鍵「時間與時值 → 設定終止時間」的最上面兩項，以及
工具選單「對齊起始/終止時間…」對話框最上面的兩顆按鈕。
"""

import unittest

from PyQt5.QtWidgets import QApplication, QLabel, QMenu, QPushButton

from qt_editor.align_time_dialog import AlignTimeDialog
from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel

_app = QApplication.instance() or QApplication([])


def note(idx, start, end, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, end, end - start
    n.pitch, n.hand, n.note_type = pitch, 0, 2
    n.min_key, n.max_key = 4, 6
    return n


class MenuPlacementTests(unittest.TestCase):
    """放在最上面是需求，不是風格。"""

    def view(self, selected=(0, 1, 2, 3)):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        m.notes_tree = [note(0, 1000, 1500, 60), note(1, 1000, 1800, 64),
                        note(2, 1000, 1300, 67), note(3, 2000, 2400, 60)]
        m.rebuild_display_cache()
        v = ChartView()
        v.model = m
        v.selected = set(selected)
        return v

    def end_submenu(self, v):
        # 一定要把 menu 留在 self 上：它一被 Python 回收，底下的子選單就被
        # Qt 刪掉，再去讀 actions() 會噴「wrapped C/C++ object has been deleted」。
        self._menu = QMenu()
        v._ctx_build_time_menu(self._menu, bool(v.selected))
        for a in self._menu.actions():
            if a.text() == '設定終止時間':
                return a.menu()
        self.fail('找不到「設定終止時間」子選單')

    def test_the_two_items_are_at_the_very_top(self):
        sub = self.end_submenu(self.view())
        texts = [a.text() for a in sub.actions()]
        self.assertTrue(texts[0].startswith('同 start 一致：最晚'), texts[:3])
        self.assertTrue(texts[1].startswith('同 start 一致：最早'), texts[:3])

    def test_they_sit_above_the_absolute_align_items(self):
        sub = self.end_submenu(self.view())
        texts = [a.text() for a in sub.actions()]
        first_abs = next(i for i, x in enumerate(texts) if x.startswith('一律對齊'))
        self.assertLess(1, first_abs)

    def test_the_label_says_how_much_it_will_touch(self):
        sub = self.end_submenu(self.view())
        self.assertIn('1 組 / 3 顆', sub.actions()[0].text())

    def test_the_start_submenu_does_not_get_them(self):
        """同 start 分組對「設定起始時間」沒有意義。"""
        v = self.view()
        self._menu = QMenu()
        v._ctx_build_time_menu(self._menu, True)
        sub = next(a.menu() for a in self._menu.actions()
                   if a.text() == '設定起始時間')
        self.assertFalse(any('同 start' in a.text() for a in sub.actions()))

    def test_without_a_same_start_group_they_are_greyed_out(self):
        sub = self.end_submenu(self.view(selected=(0, 3)))
        self.assertFalse(sub.actions()[0].isEnabled())


class ViewActionTests(unittest.TestCase):
    def view(self):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        m.notes_tree = [note(0, 1000, 1500, 60), note(1, 1000, 1800, 64),
                        note(2, 1000, 1300, 67), note(3, 2000, 2400, 60)]
        m.rebuild_display_cache()
        v = ChartView()
        v.model = m
        v.selected = {0, 1, 2, 3}
        return v

    def test_latest(self):
        v = self.view()
        self.assertEqual(v.align_selected_tails_by_start('latest'), 2)
        self.assertEqual([n.end for n in v.model.notes_tree],
                         [1800, 1800, 1800, 2400])

    def test_it_is_undoable(self):
        v = self.view()
        v.align_selected_tails_by_start('latest')
        v.model.undo()
        v.model.rebuild_display_cache()
        self.assertEqual([n.end for n in v.model.notes_tree],
                         [1500, 1800, 1300, 2400])

    def test_no_change_leaves_no_history_behind(self):
        """已經一致的時候不該在 undo 堆上留一筆空的。"""
        v = self.view()
        v.align_selected_tails_by_start('latest')
        depth = len(v.model.undo_stack)
        self.assertEqual(v.align_selected_tails_by_start('latest'), 0)
        self.assertEqual(len(v.model.undo_stack), depth)

    def test_the_selection_survives(self):
        v = self.view()
        v.align_selected_tails_by_start('earliest')
        self.assertEqual(v.selected, {0, 1, 2, 3})

    def test_nothing_selected_does_nothing(self):
        v = self.view()
        v.selected = set()
        self.assertEqual(v.align_selected_tails_by_start('latest'), 0)

    def test_the_group_count_only_counts_groups_of_two_or_more(self):
        v = self.view()
        self.assertEqual(v.selection_start_groups(), (1, 3))
        v.selected = {0, 3}
        self.assertEqual(v.selection_start_groups(), (0, 0))


class DialogTests(unittest.TestCase):
    STATS = {'min_start': 1000, 'max_start': 2000,
             'min_end': 1300, 'max_end': 2400}

    def test_the_buttons_come_before_the_target_radios(self):
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=2, group_notes=5)
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        self.assertIn('同 start', labels[0])

    def test_the_heading_says_how_much_it_will_touch(self):
        """會動到幾組／幾顆是唯一會變的資訊，要在標題上看得到。"""
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=2, group_notes=5)
        self.assertIn('2 組 / 共 5 顆', dlg.findChildren(QLabel)[0].text())

    def test_the_heading_says_so_when_there_is_nothing_to_do(self):
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=0, group_notes=0)
        self.assertIn('沒有同 start', dlg.findChildren(QLabel)[0].text())

    def test_pressing_one_reports_the_pick_and_accepts(self):
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=2, group_notes=5)
        self.assertIsNone(dlg.group_pick())
        dlg.findChildren(QPushButton)[0].click()
        self.assertEqual(dlg.group_pick(), 'latest')
        self.assertEqual(dlg.result(), AlignTimeDialog.Accepted)

    def test_the_second_button_is_earliest(self):
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=2, group_notes=5)
        dlg.findChildren(QPushButton)[1].click()
        self.assertEqual(dlg.group_pick(), 'earliest')

    def test_without_groups_they_are_disabled(self):
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=0, group_notes=0)
        self.assertFalse(dlg.findChildren(QPushButton)[0].isEnabled())
        self.assertFalse(dlg.findChildren(QPushButton)[1].isEnabled())

    def test_the_old_path_is_untouched(self):
        """沒按上面那兩顆時，對話框還是原本的「對到一個絕對 ms」。"""
        dlg = AlignTimeDialog(None, stats=self.STATS, groups=2, group_notes=5)
        self.assertIsNone(dlg.group_pick())
        self.assertEqual(dlg.target(), 'start')
        self.assertEqual(dlg.value_ms(), self.STATS['min_start'])


if __name__ == '__main__':
    unittest.main()
