# -*- coding: utf-8 -*-
"""編輯器裡的旋律線輔助：畫得出來、選得起來、修得動。

`melody_line` 本身的規則在 test_melody_line.py；這裡只管介面那一層——開關、
選取的範圍、以及「什麼都沒改就不要留下一步空的 undo」。
"""

import contextlib
import io
import unittest
from unittest import mock

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from qt_editor import melody_line as ML
from qt_editor import chart_view
from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])

KEYS = ('melody_top_line', 'melody_bottom_line',
        'melody_break_marks', 'melody_repeat_marks', 'melody_slope_marks',
        'overlap_marks', 'overlap_tail_marks',
        'phrase_echo_marks', 'phrase_same_marks', 'phrase_ornament_marks')


def make(idx, start, pitch, lane_hi, width=3, hand=0, dur=200):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = hand
    n.max_key = lane_hi
    n.min_key = max(0, lane_hi - width + 1)
    return n


class WhereTheLineTouchesTheNoteTests(unittest.TestCase):
    """落點在**起音**那一邊，不是音符方塊的正中央。

    使用者：「不應該從音符中間，而是開始才對，畢竟按下去不會是中間做的事情」。
    方塊是從 start 畫到 end 的，取中點等於把長押往後挪半個音長。
    """

    def setUp(self):
        self.view = ChartView()
        self.view.resize(900, 700)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        self.long = make(0, 400, 60, 10, dur=1500)      # 很長的長押
        self.short = make(1, 2400, 67, 14, dur=60)
        model.notes_tree = [self.long, self.short]
        model.rebuild_display_cache()
        self.view.load_model(model)
        self.view.rebuild_mapper()

    def test_the_point_sits_at_the_start_of_a_long_note(self):
        rect = self.view._note_rect(self.long)
        at = self.view._melody_point(self.long, 'top')
        # 時間往上走，所以起音在方塊的**下緣**
        self.assertAlmostEqual(at.y(), rect.bottom(), delta=0.5)
        self.assertGreater(abs(at.y() - rect.center().y()), 10.0)

    def test_a_short_note_barely_moves(self):
        rect = self.view._note_rect(self.short)
        at = self.view._melody_point(self.short, 'top')
        self.assertAlmostEqual(at.y(), rect.bottom(), delta=chart_view.MIN_NOTE_HEIGHT_PX)

    def test_the_edges_are_still_the_edges(self):
        rect = self.view._note_rect(self.long)
        self.assertAlmostEqual(
            self.view._melody_point(self.long, 'top').x(), rect.right(), delta=1.0)
        self.assertAlmostEqual(
            self.view._melody_point(self.long, 'bottom').x(), rect.left(), delta=1.0)


class MelodyViewTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.view = ChartView()
        self.view.resize(900, 700)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        # 一條破掉的線：音高往上走，鍵道停在原地。
        model.notes_tree = [make(0, 200, 60, 10), make(1, 500, 67, 10),
                            make(2, 900, 72, 16)]
        model.rebuild_display_cache()
        self.view.load_model(model)
        self.view.rebuild_mapper()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    # ── 開關 ──────────────────────────────────────────────────────

    def test_what_is_on_out_of_the_box(self):
        for k in KEYS:
            settings._data.pop(k, None)
        top, bottom, mark, repeat, slope = self.view._melody_flags()
        # 冠音線、破線記號、步伐記號預設開（使用者點名要的就是這些）；低音線
        # 只是參考、同音不同格一首會有上百處，兩個都預設關。
        self.assertEqual((top, mark, slope), (True, True, True))
        self.assertEqual((bottom, repeat), (False, False))
        # 重疊也預設開，兩種都標。
        self.assertEqual(self.view._overlap_flags(), (True, True))

    def layer(self):
        """只把旋律線那一層畫到一張黑圖上。

        整張 render 比不出東西：視窗預設 16 秒高，三顆音符全擠在最下面，而
        鍵盤鋪在那裡、又畫在旋律線之後，整條線都被蓋住。真的在用的時候視窗
        只有一兩個小節，不會這樣。
        """
        pm = QPixmap(self.view.size())
        pm.fill(Qt.black)
        qp = QPainter(pm)
        try:
            self.view._draw_melody_line(qp)
        finally:
            qp.end()
        return pm.toImage()

    @staticmethod
    def blank(image) -> bool:
        return all(image.pixelColor(x, y) == QColor(Qt.black)
                   for y in range(0, image.height(), 7)
                   for x in range(0, image.width(), 7))

    def test_the_paint_pass_runs_it(self):
        calls = []
        self.view._draw_melody_line = lambda qp: calls.append(1)
        pm = QPixmap(self.view.size())
        pm.fill()
        self.view.render(pm)
        self.assertEqual(len(calls), 1)

    def test_nothing_is_drawn_when_every_switch_is_off(self):
        settings._data.update(melody_top_line=False, melody_bottom_line=False,
                              melody_break_marks=True)
        self.assertTrue(self.blank(self.layer()))
        settings._data.update(melody_top_line=True)
        self.assertFalse(self.blank(self.layer()))

    def test_the_marks_are_a_separate_switch(self):
        settings._data.update(melody_top_line=True, melody_break_marks=True,
                              melody_repeat_marks=False)
        marked = self.layer()
        settings._data.update(melody_break_marks=False)
        plain = self.layer()
        self.assertFalse(self.blank(plain))       # 線還在
        self.assertNotEqual(marked, plain)        # 記號不見了

    def test_pitch_mode_draws_the_line_but_not_the_marks(self):
        """線在那裡是旋律輪廓（寫譜的底稿要看），但三個記號講的都是鍵道。"""
        settings._data.update(melody_top_line=True, melody_break_marks=True)
        self.view.set_view_mode('pitch')
        shown = self.layer()
        self.assertFalse(self.blank(shown))         # 線照畫
        settings._data.update(melody_break_marks=False)
        self.assertEqual(shown, self.layer())       # 記號本來就沒畫，關掉也一樣

    # ── 選取 ──────────────────────────────────────────────────────

    def test_selecting_the_top_voice(self):
        self.view.select_melody_voice('top')
        self.assertEqual(self.view.selected, {0, 1, 2})

    def test_selecting_the_broken_ones_only(self):
        self.view.select_melody_breaks(ML.KIND_DIRECTION)
        # 破的是第二步的後面那顆
        self.assertEqual(self.view.selected, {1})

    def test_an_existing_selection_limits_the_search(self):
        self.view.selected = {0}
        self.view.select_melody_voice('top')
        self.assertEqual(self.view.selected, {0})

    # ── 修補 ──────────────────────────────────────────────────────

    def test_repairing_moves_the_note_and_can_be_undone(self):
        self.view.repair_melody_breaks()
        self.assertEqual(self.view.model.notes_tree[1].max_key, 11)
        self.assertTrue(self.view.model.undo())
        self.assertEqual(self.view.model.notes_tree[1].max_key, 10)

    def test_nothing_to_fix_leaves_no_empty_undo_step(self):
        model = self.view.model
        model.notes_tree = [make(0, 200, 60, 10), make(1, 500, 67, 14)]
        model.rebuild_display_cache()
        depth = len(model.undo_stack)
        self.view.repair_melody_breaks()
        self.assertEqual(len(model.undo_stack), depth)

    def test_an_unfixable_break_does_not_leave_an_empty_undo_step(self):
        model = self.view.model
        # 兩顆都貼在最右邊，往右沒有格子可以去。
        model.notes_tree = [make(0, 200, 60, 27, width=1),
                            make(1, 500, 67, 27, width=1)]
        model.rebuild_display_cache()
        depth = len(model.undo_stack)
        self.view.repair_melody_breaks()
        self.assertEqual(len(model.undo_stack), depth)
        self.assertEqual(model.notes_tree[1].max_key, 27)

    def test_a_selection_limits_what_may_move(self):
        self.view.selected = {0}          # 只准動第一顆，但要動的是第二顆
        self.view.repair_melody_breaks()
        self.assertEqual(self.view.model.notes_tree[1].max_key, 10)

    def test_jumping_to_the_next_break_selects_it(self):
        self.view.goto_next_melody_break()
        self.assertEqual(self.view.selected, {1})


class OverlapMarkTests(unittest.TestCase):
    """重疊的標記。畫的是兩顆音符方塊的**交集**，不是旁邊放一個記號。"""

    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.view = ChartView()
        self.view.resize(900, 700)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        # 第 0、1 顆同時起音又共用第 10 格 ＝ 紅的那種；
        # 第 2 顆落在第 1 顆的音長裡 ＝ 琥珀虛線那種。
        model.notes_tree = [make(0, 200, 60, 10), make(1, 210, 64, 12),
                            make(2, 300, 67, 13)]
        model.rebuild_display_cache()
        self.view.load_model(model)
        self.view.rebuild_mapper()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def layer(self):
        """只把重疊那一層畫到黑圖上（理由同 MelodyViewTests.layer）。"""
        pm = QPixmap(self.view.size())
        pm.fill(Qt.black)
        qp = QPainter(pm)
        try:
            self.view._draw_overlap_marks(qp)
        finally:
            qp.end()
        return pm.toImage()

    def blank(self, image) -> bool:
        return all(image.pixelColor(x, y) == QColor(Qt.black)
                   for y in range(0, image.height(), 3)
                   for x in range(0, image.width(), 3))

    def test_the_paint_pass_runs_it(self):
        calls = []
        self.view._draw_overlap_marks = lambda qp: calls.append(1)
        pm = QPixmap(self.view.size())
        pm.fill()
        self.view.render(pm)
        self.assertEqual(len(calls), 1)

    def test_it_draws_something(self):
        settings._data.update(overlap_marks=True, overlap_tail_marks=True)
        self.assertFalse(self.blank(self.layer()))

    def test_the_switch_turns_it_off(self):
        settings._data.update(overlap_marks=False, overlap_tail_marks=True)
        self.assertTrue(self.blank(self.layer()))

    def test_the_mild_kind_is_its_own_switch(self):
        settings._data.update(overlap_marks=True, overlap_tail_marks=True)
        both = self.layer()
        settings._data.update(overlap_tail_marks=False)
        only_bad = self.layer()
        self.assertFalse(self.blank(only_bad))      # 紅的那種還在
        self.assertNotEqual(both, only_bad)

    def test_pitch_mode_draws_nothing(self):
        # 那個模式的橫軸是音高不是鍵道，疊不疊和佔不佔同一個按鍵是兩件事。
        settings._data.update(overlap_marks=True)
        self.view.set_view_mode('pitch')
        self.assertTrue(self.blank(self.layer()))

    def test_a_hidden_note_cannot_overlap(self):
        """隱藏音符不佔按鍵，算成重疊只會冤枉寄主。"""
        self.view.model.notes_tree[1].hidden = True
        self.assertEqual(
            [k for k, _a, _b in self.view._overlap_found()
             if k == 'chord'], [])

    def test_selecting_takes_both_notes(self):
        self.view.select_overlaps()
        self.assertEqual(self.view.selected, {0, 1, 2})

    def test_selecting_only_the_bad_kind(self):
        self.view.select_overlaps('chord')
        self.assertEqual(self.view.selected, {0, 1})

    def test_an_existing_selection_limits_the_search(self):
        self.view.selected = {0, 1}                 # 範圍 → 200–210ms
        self.view.select_overlaps()
        # 第 2 顆（start=300）那一對在範圍外，不該被選進來
        self.assertEqual(self.view.selected, {0, 1})

    def test_jumping_selects_the_pair(self):
        self.view.goto_next_overlap()
        self.assertEqual(len(self.view.selected), 2)

    def test_the_context_menu_items_really_fire(self):
        """右鍵選單的 lambda 打錯字是看不出來的，按下去才會知道。"""
        from PyQt5.QtWidgets import QMenu
        menu = QMenu()
        self.view._ctx_build_overlap_menu(menu, False)
        labels = [a.text() for a in menu.actions()]
        self.assertEqual(len(labels), 3, labels)
        for act in menu.actions():
            self.view.selected = set()
            act.trigger()
        self.assertTrue(self.view.selected)         # 最後一項是「跳到下一處」

    def test_jumping_with_nothing_to_find(self):
        said = []
        self.view.status_changed.connect(said.append)
        self.view.model.notes_tree = [make(0, 200, 60, 4)]
        self.view.goto_next_overlap()
        self.assertEqual(self.view.selected, set())
        self.assertTrue(said and '沒有' in said[-1], said)


class SlopeMarkTests(unittest.TestCase):
    """步伐差太多：從那顆冠音拉一條針，差愈多針愈長。"""

    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        self.view = ChartView()
        self.view.resize(900, 700)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        # 2 個半音該走 1 格，這裡飛了 10 格。
        model.notes_tree = [make(0, 200, 60, 6), make(1, 500, 62, 16)]
        model.rebuild_display_cache()
        self.view.load_model(model)
        self.view.rebuild_mapper()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def test_the_spike_is_its_own_switch(self):
        settings._data.update(melody_top_line=True, melody_break_marks=False,
                              melody_repeat_marks=False,
                              melody_slope_marks=True)
        marked = self.layer()
        settings._data.update(melody_slope_marks=False)
        plain = self.layer()
        self.assertFalse(self.blank(plain))         # 線還在
        self.assertNotEqual(marked, plain)

    def layer(self):
        pm = QPixmap(self.view.size())
        pm.fill(Qt.black)
        qp = QPainter(pm)
        try:
            self.view._draw_melody_line(qp)
        finally:
            qp.end()
        return pm.toImage()

    def blank(self, image) -> bool:
        return all(image.pixelColor(x, y) == QColor(Qt.black)
                   for y in range(0, image.height(), 3)
                   for x in range(0, image.width(), 3))

    def grow(self):
        self.view.model.notes_tree += [make(2, 1000, 60, 6),
                                       make(3, 1300, 62, 16)]
        # rebuild 每次都指派一張新的 list，快取就是靠這個課掉的
        self.view.model.rebuild_display_cache()

    def test_the_curve_is_learnt_from_the_whole_chart(self):
        """不能只拿視窗裡那幾顆去學，不然標記會跟著捲動閃掉。"""
        self.assertEqual(self.view._melody_curve(), {})   # 一個樣本，不收
        self.grow()
        with mock.patch.object(chart_view, 'CURVE_MAX_AGE_S', 0.0):
            self.assertEqual(self.view._melody_curve(), {2: 10.0})

    def test_it_does_not_relearn_every_frame_while_dragging(self):
        """改過時間的编輯每一幀都給新 list，而整份量一次在最密的譜要 9ms。"""
        self.view._melody_curve()
        self.grow()
        self.assertEqual(self.view._melody_curve(), {})    # 還是舊的
        with mock.patch.object(chart_view, 'CURVE_MAX_AGE_S', 0.0):
            self.assertEqual(self.view._melody_curve(), {2: 10.0})

    def test_selecting_the_ones_that_differ_too_much(self):
        self.view.select_melody_breaks(ML.KIND_SLOPE)
        self.assertEqual(self.view.selected, {1})


class PhraseMarkTests(unittest.TestCase):
    """樂句：同一句又來了（移了調／插了裝飾音）。

    畫的是沿著冠音的一條緞帶：本體點線、回聲實線，位移沒跟上就換成洋紅。
    """

    #: 八顆、裡面不含移調的重複（不然找到的第一處會是句子自己的後半對前半），
    #: 而且夠長——錨點要連續 3 個乾淨的音程，中間插一顆裝飾音還要湊得出來。
    TUNE = (60, 64, 62, 67, 65, 71, 69, 74)
    LANES = (10, 13, 12, 16, 15, 19, 18, 22)
    STEP = 180
    ECHO_AT = 2000

    def setUp(self):
        self._saved = {k: settings._data.get(k) for k in KEYS}
        settings._data.update(phrase_echo_marks=True, phrase_same_marks=False,
                              phrase_ornament_marks=True)
        self.view = ChartView()
        self.view.resize(900, 700)
        self.show(self.chart())

    def show(self, model):
        """載進去並把兩句都捲進視窗——畫面外的緞帶本來就不畫。"""
        self.view.load_model(model)
        self.view.rebuild_mapper()
        self.view.window_size_unit = 16.0
        self.view.scroll_to_ms(0)

    def echo_idx(self):
        """回聲那一句的 idx。`rebuild_display_cache` 會照時間重編，不能寫死。"""
        return {n.idx for n in self.view.model.notes_tree
                if int(n.start) >= self.ECHO_AT}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v

    def chart(self, offset=12, lane_shift=0, ornament=False):
        """本體一句，回聲一句（預設移高八度但鍵道沒跟著移 ＝ 位移沒跟上）。"""
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        notes = [make(k, k * self.STEP, p, lane)
                 for k, (p, lane) in enumerate(zip(self.TUNE, self.LANES))]
        notes += [make(100 + k, self.ECHO_AT + k * self.STEP,
                       p + offset, lane + lane_shift)
                  for k, (p, lane) in enumerate(zip(self.TUNE, self.LANES))]
        if ornament:
            # 插在回聲的第 2、3 顆中間，後面還留著五個乾淨的音程給錨點用
            notes.append(make(200, self.ECHO_AT + self.STEP + 60,
                              63 + offset, 12 + lane_shift))
        model.notes_tree = notes
        model.rebuild_display_cache()
        return model

    def layer(self):
        """只把樂句那一層畫到黑圖上（理由同 MelodyViewTests.layer）。"""
        pm = QPixmap(self.view.size())
        pm.fill(Qt.black)
        qp = QPainter(pm)
        try:
            self.view._draw_phrase_marks(qp)
        finally:
            qp.end()
        return pm.toImage()

    def blank(self, image) -> bool:
        return all(image.pixelColor(x, y) == QColor(Qt.black)
                   for y in range(0, image.height(), 3)
                   for x in range(0, image.width(), 3))

    # ── 畫出來 ────────────────────────────────────────────────────

    def test_the_paint_pass_runs_it(self):
        calls = []
        self.view._draw_phrase_marks = lambda qp: calls.append(1)
        pm = QPixmap(self.view.size())
        pm.fill()
        self.view.render(pm)
        self.assertEqual(len(calls), 1)

    def test_it_draws_the_echo(self):
        self.assertFalse(self.blank(self.layer()))

    def test_the_switch_turns_it_off(self):
        settings._data.update(phrase_echo_marks=False,
                              phrase_ornament_marks=False)
        self.assertTrue(self.blank(self.layer()))

    def test_a_plain_repeat_has_its_own_switch(self):
        """原樣重複預設不畫：它比移調的還多，兩種都畫太吵。"""
        self.show(self.chart(offset=0))
        self.assertTrue(self.blank(self.layer()))
        settings._data.update(phrase_same_marks=True)
        self.view._phrase_cache = None
        self.assertFalse(self.blank(self.layer()))

    def test_the_ornament_is_marked_even_when_the_phrase_is_not(self):
        """裝飾音多半出現在原樣重複的句子裡，不該被那個開關一起關掉。"""
        self.show(self.chart(offset=0, ornament=True))
        settings._data.update(phrase_same_marks=False,
                              phrase_ornament_marks=True)
        self.assertFalse(self.blank(self.layer()))
        settings._data.update(phrase_ornament_marks=False)
        self.view._phrase_cache = None
        self.assertTrue(self.blank(self.layer()))

    def test_pitch_mode_draws_it_too(self):
        """移調重複在音高模式下最清楚：同一個形狀往旁邊平移。"""
        self.view.set_view_mode('pitch')
        self.assertFalse(self.blank(self.layer()))

    def test_the_lane_hint_is_dropped_in_pitch_mode(self):
        """「該再移 N 格」的箭頭指的是鍵道，在橫軸是音高的畫面上會指到別的音。"""
        with mock.patch.object(ChartView, '_draw_melody_arrow') as arrow:
            self.layer()
            self.assertTrue(arrow.called)
            arrow.reset_mock()
            self.view.set_view_mode('pitch')
            self.layer()
            self.assertFalse(arrow.called)

    def test_the_bad_one_is_a_different_colour(self):
        bad = self.layer()
        self.show(self.chart(lane_shift=4))                 # 位移跟上了
        self.assertFalse(self.blank(self.layer()))
        self.assertNotEqual(bad, self.layer())

    # ── 整份算一次、快取 ──────────────────────────────────────────

    def test_it_does_not_look_for_phrases_every_frame(self):
        """改時間的編輯每幀都會指派一張新 list，重算的話最慢要 79ms。"""
        calls = []
        real = chart_view.MP.echoes
        with mock.patch.object(chart_view.MP, 'echoes',
                               side_effect=lambda *a, **k: (calls.append(1),
                                                            real(*a, **k))[1]):
            self.view._phrase_echoes()
            notes = self.view.model.notes
            self.view.model.notes = list(notes)     # 新 list、同樣的顆數
            self.view._phrase_echoes()
            self.assertEqual(len(calls), 1)

    def test_loading_another_chart_drops_the_old_phrases(self):
        """顆數剛好一樣時，留著舊結果會把上一份譜的樂句畫到這一份上。"""
        self.assertTrue(self.view._phrase_echoes())
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        model.notes_tree = [make(k, k * 300, 60 + k, 5)
                            for k in range(len(self.TUNE) * 2)]
        model.rebuild_display_cache()
        self.view.load_model(model)
        live = {id(n) for n in model.notes_tree}
        for echo in self.view._phrase_echoes():
            self.assertTrue(all(id(n) in live
                                for n in echo.notes + echo.source))

    def test_deleting_a_note_forces_a_fresh_look(self):
        """舊結果抓著刪掉的音符的話，畫面上會留下一條指著空處的緞帶。"""
        calls = []
        real = chart_view.MP.echoes
        with mock.patch.object(chart_view.MP, 'echoes',
                               side_effect=lambda *a, **k: (calls.append(1),
                                                            real(*a, **k))[1]):
            self.view._phrase_echoes()
            self.view.model.notes = self.view.model.notes[:-1]
            self.view._phrase_echoes()
            self.assertEqual(len(calls), 2)

    # ── 選起來、跳過去 ────────────────────────────────────────────

    def test_selecting_takes_the_later_phrase(self):
        self.view.select_echo_notes()
        self.assertEqual(self.view.selected, self.echo_idx())

    def test_selecting_the_source_takes_the_earlier_one(self):
        self.view.select_echo_notes('source')
        self.assertEqual(self.view.selected,
                         set(range(len(self.TUNE))))

    def test_the_selection_includes_the_ornaments(self):
        self.show(self.chart(ornament=True))
        self.view.select_echo_notes()
        self.assertEqual(self.view.selected, self.echo_idx())
        self.assertEqual(len(self.view.selected), len(self.TUNE) + 1)

    def test_selecting_only_the_ones_that_did_not_follow(self):
        self.view.select_echo_notes(bad_only=True)
        self.assertEqual(self.view.selected, self.echo_idx())
        self.view.deselect_all()
        self.show(self.chart(lane_shift=4))                 # 跟上了
        self.view.select_echo_notes(bad_only=True)
        self.assertEqual(self.view.selected, set())

    def test_selecting_the_ornaments_alone(self):
        self.show(self.chart(ornament=True))
        self.view.select_ornaments()
        ornament = {n.idx for n in self.view.model.notes_tree
                    if int(n.start) == self.ECHO_AT + self.STEP + 60}
        self.assertEqual(self.view.selected, ornament)

    def test_jumping_selects_the_whole_phrase(self):
        said = []
        self.view.status_changed.connect(said.append)
        self.view.goto_next_echo()
        self.assertEqual(self.view.selected, self.echo_idx())
        self.assertTrue(said and '移調重複' in said[-1], said)

    def test_jumping_with_nothing_to_find(self):
        said = []
        self.view.status_changed.connect(said.append)
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        model.notes_tree = [make(0, 200, 60, 4)]
        model.rebuild_display_cache()
        self.view.load_model(model)
        self.view.goto_next_echo()
        self.assertEqual(self.view.selected, set())
        self.assertTrue(said and '找不到' in said[-1], said)

    def test_the_context_menu_items_really_fire(self):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu()
        self.view._ctx_build_phrase_menu(menu, False)
        acts = [a for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(len(acts), 7, [a.text() for a in acts])
        for act in acts:
            act.trigger()                   # 打錯字的 lambda 只有按下去才知道

    # ── 照本體排 ──────────────────────────────────────────────────

    def lanes(self):
        return [(int(n.start), int(n.max_key))
                for n in sorted(self.view.model.notes_tree,
                                key=lambda n: int(n.start))]

    def test_it_lays_the_echo_out_like_its_source(self):
        """預設的譜是「整句移了八度但鍵道沒跟著移」，排完就該是本體 ＋ 該移的量。"""
        echo = self.view._phrase_found()[0]
        self.assertTrue(echo.bad)
        src = [int(n.max_key) for n in echo.source]
        want = int(round(echo.want))
        self.view.apply_phrase_shape()
        self.assertEqual([int(n.max_key) for n in echo.repeat],
                         [l + want for l in src])

    def test_it_says_what_it_cost(self):
        said = []
        self.view.status_changed.connect(said.append)
        self.view.apply_phrase_shape()
        self.assertTrue(said and '方向破線' in said[-1], said)

    def test_it_can_be_undone(self):
        before = self.lanes()
        self.view.apply_phrase_shape()
        self.assertNotEqual(self.lanes(), before)
        self.view.model.undo()
        self.assertEqual(self.lanes(), before)

    def test_nothing_to_do_does_not_eat_a_ctrl_z(self):
        self.view.load_model(self.chart(lane_shift=4))      # 本來就排好了
        self.view.rebuild_mapper()
        depth = len(self.view.model.undo_stack)
        said = []
        self.view.status_changed.connect(said.append)
        self.view.apply_phrase_shape()
        self.assertEqual(len(self.view.model.undo_stack), depth)
        self.assertTrue(said and '本來就在' in said[-1], said)

    def test_a_selection_limits_it_to_that_stretch(self):
        self.view.select_echo_notes('source')       # 只框到本體那一句
        said = []
        self.view.status_changed.connect(said.append)
        before = self.lanes()
        self.view.apply_phrase_shape()
        self.assertEqual(self.lanes(), before)
        self.assertTrue(said and '一句也沒有' in said[-1], said)

    def test_it_lays_out_what_is_marked(self):
        """原樣重複的標記關著就不排它——選單排的和眼睛看到的是同一批。"""
        self.view.load_model(self.chart(offset=0, lane_shift=3))
        self.view.rebuild_mapper()
        bent = sorted(self.view.model.notes_tree, key=lambda n: int(n.start))[-1]
        bent.min_key = int(bent.min_key) + 4       # 回聲的最後一顆排歪了
        bent.max_key = int(bent.max_key) + 4
        self.view.model.rebuild_display_cache()
        self.view._phrase_cache = None
        before = self.lanes()
        self.view.apply_phrase_shape()
        self.assertEqual(self.lanes(), before)
        settings._data.update(phrase_same_marks=True)
        self.view._phrase_cache = None
        self.view.apply_phrase_shape()
        self.assertNotEqual(self.lanes(), before)

    def test_only_the_ones_that_did_not_follow(self):
        self.view.load_model(self.chart(lane_shift=4))      # 位移跟上了
        self.view.rebuild_mapper()
        before = self.lanes()
        self.view.apply_phrase_shape(bad_only=True)
        self.assertEqual(self.lanes(), before)

    def test_it_looks_again_before_the_next_press(self):
        """舊的 `shift` 留著的話，連按第二下會再搬一次。"""
        self.view.apply_phrase_shape()
        self.assertIsNone(self.view._phrase_cache)
        after = self.lanes()
        self.view.apply_phrase_shape()
        self.assertEqual(self.lanes(), after)


class MelodyMenuTests(unittest.TestCase):
    """主視窗那一層：檢查放在哪、開關在不在。"""

    @classmethod
    def setUpClass(cls):
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def test_the_check_sits_with_the_other_lane_tools(self):
        groups = dict((name, [label for label, _fn in items])
                      for name, items in self.win._tool_groups())
        self.assertIn('鍵道排列', groups)
        self.assertTrue(any('冠音' in label for label in groups['鍵道排列']),
                        groups['鍵道排列'])

    def tables(self):
        return (tuple(self.win.MELODY_VIEW_OPTIONS)
                + tuple(self.win.PHRASE_VIEW_OPTIONS)
                + tuple(self.win.OVERLAP_VIEW_OPTIONS))

    def test_every_switch_has_an_action(self):
        keys = [key for key, _l, _d, _t in self.tables()]
        self.assertEqual(sorted(self.win._mark_acts), sorted(keys))
        for key, _label, default, _tip in self.tables():
            act = self.win._mark_acts[key]
            self.assertTrue(act.isCheckable())
            self.assertEqual(act.isChecked(),
                             bool(settings.get(key, default)), key)

    def test_the_menu_defaults_match_the_view(self):
        on = [key for key, _l, default, _t in self.tables() if default]
        self.assertEqual(on, ['melody_top_line', 'melody_break_marks',
                              'melody_slope_marks',
                              'phrase_echo_marks', 'phrase_ornament_marks',
                              'overlap_marks', 'overlap_tail_marks'])

    def test_overlap_is_its_own_section(self):
        """重疊不是旋律線的事，開關也不該混在那一張表裡。樂句同理。"""
        melody = [k for k, _l, _d, _t in self.win.MELODY_VIEW_OPTIONS]
        self.assertTrue(all(k.startswith('melody_') for k in melody), melody)
        phrase = [k for k, _l, _d, _t in self.win.PHRASE_VIEW_OPTIONS]
        self.assertTrue(all(k.startswith('phrase_') for k in phrase), phrase)

    def test_the_report_counts_the_phrases_too(self):
        """報告和畫面要走同一份快取，不然兩邊各說一套。"""
        model = NoteModel.create_new('t', 120.0, 30.0, 4)
        tune = (60, 64, 62, 67, 65, 71, 69, 74)
        lanes = (10, 13, 12, 16, 15, 19, 18, 22)
        notes = [make(k, k * 180, p, lane)
                 for k, (p, lane) in enumerate(zip(tune, lanes))]
        notes += [make(100 + k, 2000 + k * 180, p + 12, lane)
                  for k, (p, lane) in enumerate(zip(tune, lanes))]
        model.notes_tree = notes
        model.rebuild_display_cache()
        self.win.view.load_model(model)
        seen = []
        from qt_editor import main_window as MW
        with mock.patch.object(MW, 'QMessageBox') as box:
            box.return_value.setText.side_effect = seen.append
            box.return_value.clickedButton.return_value = None
            self.win.melody_line_report()
        self.assertTrue(seen, '報告沒有跑出文字')
        self.assertIn('同一句又來了', seen[0])
        self.assertIn('移調 1 處', seen[0])
        self.win.view.model.dirty = False

    def test_the_jump_can_be_given_a_shortcut(self):
        table = dict((key, method)
                     for key, _label, method in self.win._SHORTCUT_ACTIONS)
        for key in ('shortcut_next_melody_break', 'shortcut_next_overlap'):
            self.assertIn(key, table)
            self.assertTrue(callable(getattr(self.win, table[key])))

    def test_toggling_writes_the_setting(self):
        act = self.win._mark_acts['melody_top_line']
        before = settings._data.get('melody_top_line')
        try:
            # 翻到另一邊才會觸發 toggled——設成它本來就是的值不會寫任何東西。
            act.setChecked(not act.isChecked())
            self.assertEqual(bool(settings.get('melody_top_line', True)),
                             act.isChecked())
        finally:
            if before is None:
                settings._data.pop('melody_top_line', None)
            else:
                settings._data['melody_top_line'] = before
            settings.save()


if __name__ == '__main__':
    unittest.main()
