# -*- coding: utf-8 -*-
"""量化對話框上的拍線警告，以及「沒有音符需要移動」那句話。

使用者回饋：「時間戳好像有點點問題，有的稍微偏離了節拍線有的時間靠得比較近，
但是製譜器告訴我所有的 note 是量化好的，我不太懂這是哪方面的問題。」

量化的格線是「把相鄰兩條拍線之間切成等分」，所以拍線歪掉的時候，量化照樣會
成功、也照樣回報「已對齊」——那句話沒說錯，但它只講了「在製譜器的格線上」。
這裡釘兩件事：

  1. 拍線有問題時，對話框在按下去**之前**就要講（而且要在格點選擇的上面，
     因為格線本身斷掉時選多細的格點都沒意義）。
  2. 那句「沒有音符需要移動」要說清楚它比對的是什麼。

反面也要釘：乾淨的譜不准多出一塊警告。這種提示一旦到處亮就會被忽略。
"""

import contextlib
import io
import unittest

from PyQt5.QtWidgets import QApplication, QGroupBox, QLabel

from qt_editor import beat_grid_check as bgc
from qt_editor.main_window import MainWindow
from qt_editor.models import GNote, NoteModel
from qt_editor.quantize_dialog import QuantizeDialog

_app = QApplication.instance() or QApplication([])


def even(step, n, start=0):
    return [start + int(round(step * i)) for i in range(n)]


def model_with(lines, bpm=120.0):
    m = NoteModel.create_new('t', bpm, 60.0, 4)
    m._epb_mode = None
    m._epb_count = None
    m.json_meta = {}
    m._write_beat_data_pairs([(i, int(t)) for i, t in enumerate(lines)])
    notes = []
    for i in range(8):
        n = GNote(None, i)
        n.start, n.gate = 500 * i, 400
        n.end = n.start + n.gate
        n.pitch, n.hand, n.note_type = 60 + i, 0, 0
        n.min_key, n.max_key = 10, 12
        n.track = 1
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def broken_lines():
    """漏掉一個拍點。"""
    lines = even(500, 40)
    del lines[20]
    return lines


def ramp_lines():
    """平滑的速度變化（彈性速度）。"""
    lines, t, step = [0], 0.0, 500.0
    for _ in range(39):
        t += step
        step *= 1.015
        lines.append(int(round(t)))
    return lines


GRIDS = (('1/4 音符', 1.0), ('1/8 音符', 0.5), ('1/16 音符', 0.25))


def labels_of(dlg):
    return [w.text() for w in dlg.findChildren(QLabel)]


class DialogTests(unittest.TestCase):
    """對話框上那塊「拍線檢查」。"""

    def setUp(self):
        self._open = []

    def tearDown(self):
        # 沒有父物件的 QDialog 要自己收。留給 Python GC 的話，解構會發生在
        # 之後某個隨機的時間點，整套測試跑到後面就會 segfault（實測跑到
        # `test_undo_memory` 掛掉，和那支測試本身無關）。
        for d in self._open:
            d.close()
            d.deleteLater()
        self._open.clear()
        _app.processEvents()

    def make(self, **kw):
        d = QuantizeDialog(None, grids=GRIDS, sel_count=0, **kw)
        self._open.append(d)
        return d

    def dlg(self, lines, bpm=120.0):
        rep = bgc.check(lines, bpm) if lines else None
        return self.make(grid_report=rep), rep

    def test_a_clean_chart_gets_no_warning_block(self):
        dlg, _ = self.dlg(even(500, 40))
        self.assertIsNone(dlg.report_box, '乾淨的譜不該多出一塊警告')
        titles = [b.title() for b in dlg.findChildren(QGroupBox)]
        self.assertNotIn('拍線檢查', titles)

    def test_a_broken_chart_gets_one(self):
        dlg, _ = self.dlg(broken_lines())
        self.assertIsNotNone(dlg.report_box)
        self.assertEqual(dlg.report_box.title(), '拍線檢查')

    def test_the_warning_names_the_problem(self):
        dlg, _ = self.dlg(broken_lines())
        text = '\n'.join(labels_of(dlg))
        self.assertIn('漏了拍點', text)

    def test_the_warning_points_at_a_time(self):
        dlg, rep = self.dlg(broken_lines())
        text = '\n'.join(labels_of(dlg))
        self.assertIn(bgc.fmt_ms(rep.spikes[0].where), text)

    def test_it_says_quantize_will_still_claim_success(self):
        """最重要的一句：做完會回報「已對齊」，但那不代表沒問題。"""
        dlg, _ = self.dlg(broken_lines())
        text = '\n'.join(labels_of(dlg))
        self.assertIn('已對齊', text)

    def test_it_sits_above_the_grid_choice(self):
        """格線斷掉時選多細的格點都沒意義，所以要在選擇的上面。"""
        dlg, _ = self.dlg(broken_lines())
        root = dlg.layout()
        order = [root.itemAt(i).widget() for i in range(root.count())]
        boxes = [w for w in order if isinstance(w, QGroupBox)]
        self.assertIs(boxes[0], dlg.report_box)

    def test_rubato_gets_an_explanation_not_a_warning(self):
        dlg, rep = self.dlg(ramp_lines())
        self.assertTrue(rep.rubato)
        self.assertIsNotNone(dlg.report_box, '速度變化也要說一聲')
        text = '\n'.join(labels_of(dlg))
        self.assertIn('速度變化', text)
        self.assertNotIn('⚠', text, '彈性速度不是錯誤，不該掛警告符號')

    def test_no_report_at_all_is_fine(self):
        dlg = self.make()
        self.assertIsNone(dlg.report_box)
        self.assertEqual(dlg.params()['grid_beats'], 0.25)

    def test_the_hint_no_longer_overpromises(self):
        """舊的提示說「有轉速的曲子也對得上」，那句話漏了前提。"""
        dlg, _ = self.dlg(even(500, 40))
        text = '\n'.join(labels_of(dlg))
        self.assertIn('拍線本身的間距不對時', text)


class NothingMovedMessageTests(unittest.TestCase):
    """「沒有音符需要移動」那句話。"""

    def msg(self, lines, bpm=120.0):
        return MainWindow.quantize_nothing_msg(bgc.check(lines, bpm))

    def test_it_says_which_grid_it_compared_against(self):
        for lines in (even(500, 40), broken_lines(), ramp_lines()):
            with self.subTest(lines=len(lines)):
                self.assertIn('相鄰兩條拍線之間', self.msg(lines))

    def test_a_clean_chart_gets_no_extra_caveat(self):
        m = self.msg(even(500, 40))
        self.assertNotIn('先修拍線', m)
        self.assertNotIn('速度變化', m)

    def test_a_broken_chart_is_told_to_fix_the_lines_first(self):
        m = self.msg(broken_lines())
        self.assertIn('先修拍線', m)
        self.assertIn('不等於', m)

    def test_rubato_is_told_why_the_gaps_differ(self):
        m = self.msg(ramp_lines())
        self.assertIn('速度變化', m)
        self.assertNotIn('先修拍線', m)

    def test_it_survives_no_report(self):
        self.assertIn('相鄰兩條拍線之間',
                      MainWindow.quantize_nothing_msg(None))

    def test_it_survives_a_report_with_too_few_lines(self):
        self.assertIn('相鄰兩條拍線之間', self.msg(even(500, 3)))


class WiringTests(unittest.TestCase):
    """主窗真的會算報告並餵給對話框。"""

    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def run_dialog(self, lines):
        """攔住對話框，把它拿到的 grid_report 交出來。"""
        self.win.view.model = model_with(lines)
        self.win.view.rebuild_mapper()
        seen = {}
        import qt_editor.quantize_dialog as qd
        real = qd.QuantizeDialog

        class Spy(real):
            def __init__(self, *a, **kw):
                seen['report'] = kw.get('grid_report')
                super().__init__(*a, **kw)

            def exec_(self):
                return 0                      # 使用者按取消

        qd.QuantizeDialog = Spy
        try:
            self.win.quantize_dialog()
        finally:
            qd.QuantizeDialog = real
        return seen.get('report')

    def test_the_dialog_is_handed_a_report(self):
        rep = self.run_dialog(even(500, 40))
        self.assertIsNotNone(rep)
        self.assertTrue(rep.enough)

    def test_the_report_describes_this_chart(self):
        rep = self.run_dialog(broken_lines())
        self.assertEqual(len(rep.missing), 1)
        self.assertTrue(rep.warnings())

    def test_a_clean_chart_reports_clean(self):
        rep = self.run_dialog(even(500, 40))
        self.assertEqual(rep.warnings(), [])


if __name__ == '__main__':
    unittest.main()
