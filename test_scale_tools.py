"""調性高亮 / 鎖調 / 量化 / 幽靈音符。

三個都是「編輯時的輔助」：高亮和鎖調管音高、量化管時間、幽靈音符管視野。
共同的要求是不能偷改資料——高亮和幽靈只影響畫面，鎖調只影響之後放的音，
真的動到譜面的（量化、吸到調內）都要可以還原。
"""

import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor.chart_view import ChartView
from qt_editor.models import GNote, NoteModel
from qt_editor.music_theory import Key
from qt_editor.settings import settings

_app = QApplication.instance() or QApplication([])

C_MAJOR_PITCHES = [60, 62, 64, 65, 67, 69, 71, 72]


def make_note(idx, start, pitch, hand=0, end=None):
    n = GNote(None, idx)
    n.start = start
    n.end = end if end is not None else start + 200
    n.gate = n.end - n.start
    n.pitch = pitch
    n.hand = hand
    n.velocity = 80
    n.min_key = 4
    n.max_key = 6
    n.note_type = 0
    return n


class ViewCase(unittest.TestCase):
    """每個測試都把三個開關恢復成預設，免得互相汙染。"""

    DEFAULTS = {'pitch_scale_highlight': True,
                'pitch_scale_lock': False,
                'ghost_other_hand': True}

    def build(self, notes=(), bpm=120.0):
        self.view = ChartView()
        self.view.resize(1200, 720)
        self.model = NoteModel.create_new('t', bpm, 30.0, 4)
        self.model.notes_tree = list(notes)
        self.model.rebuild_display_cache()
        self.view.load_model(self.model)
        self.view.rebuild_mapper()
        self.view.set_view_mode('pitch')
        return self.view

    def setUp(self):
        for k, v in self.DEFAULTS.items():
            settings.set(k, v)

    def tearDown(self):
        for k, v in self.DEFAULTS.items():
            settings.set(k, v)


class ScaleHighlightTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.build([make_note(i, i * 300, p)
                    for i, p in enumerate(C_MAJOR_PITCHES)])

    def test_it_highlights_the_detected_key(self):
        self.assertEqual(sorted(self.view._highlight_pitch_classes()),
                         [0, 2, 4, 5, 7, 9, 11])

    def test_an_empty_chart_highlights_nothing(self):
        view = self.build([])
        self.assertIsNone(view._highlight_pitch_classes())
        self.assertIsNone(view._active_key())

    def test_a_manual_key_overrides_detection(self):
        self.view.set_pattern_params(key_override=Key(6, 'major'))
        self.assertEqual(sorted(self.view._highlight_pitch_classes()),
                         sorted(Key(6, 'major').pitch_classes))

    def test_pattern_generation_uses_the_same_key(self):
        # 高亮說是這個調、生成音階卻用別的調會很怪
        self.assertEqual(self.view.pattern_key().tonic,
                         self.view._active_key().tonic)

    def test_an_empty_chart_still_generates_from_c_major(self):
        view = self.build([])
        self.assertEqual((view.pattern_key().tonic, view.pattern_key().mode),
                         (0, 'major'))

    def test_the_setting_turns_it_off(self):
        settings.set('pitch_scale_highlight', False)
        self.assertFalse(self.view._scale_highlight_on())

    def test_it_only_applies_in_pitch_mode(self):
        self.view.set_view_mode('measure')
        self.assertFalse(self.view._scale_highlight_on())

    def test_the_detection_result_is_cached(self):
        calls = []
        real = self.view.detect_chart_key

        def counted():
            calls.append(1)
            return real()

        self.view.detect_chart_key = counted
        self.view._invalidate_key_cache()
        for _ in range(20):
            self.view._highlight_pitch_classes()
        self.assertEqual(len(calls), 1, '每次重繪都重掃全譜會很慢')

    def test_editing_invalidates_the_cache(self):
        self.view._highlight_pitch_classes()
        self.assertIsNotNone(self.view._in_key_cache)
        self.view.note_edited.emit()
        self.assertIsNone(self.view._in_key_cache)


class KeyboardDimTests(ViewCase):
    """調外音的琴鍵壓暗，不可以波及旁邊的調內鍵。

    第一版是「畫完鍵盤再蓋一層半透明灰」，但白鍵的繪製範圍本來就墊在黑鍵
    底下，蓋上去會連旁邊的黑鍵一起塗掉——鍵的形狀、邊框、黑白層次全部消失，
    看起來就是一塊灰板子壓在鍵盤上。
    """

    #: 白鍵取最下面（黑鍵構不到那裡）；黑鍵要橫掃整個寬度——白鍵的繪製範圍
    #: 只和黑鍵的**邊緣**重疊（實測 C 白鍵 632~658、C# 黑鍵 652~663），只取
    #: 正中央的話正好避開被弄髒的那幾像素，測了等於沒測。
    BLACK_SAMPLE_FRACTIONS = (0.12, 0.3, 0.5, 0.7, 0.88)

    def setUp(self):
        super().setUp()
        self.build([make_note(i, i * 300, 60 + i) for i in range(8)])
        self.view.resize(1400, 760)
        # C# 大調：調內含五個黑鍵，而且每個黑鍵旁邊都有調外的白鍵——這正是
        # 會出事的排列。C 大調沒有任何調內黑鍵，拿它測抓不到東西。
        self.view.set_pattern_params(key_override=Key(1, 'major'))
        self.in_key = set(self.view._highlight_pitch_classes())

    def render(self):
        from PyQt5.QtGui import QPainter, QPixmap
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view.render(qp)
        finally:
            qp.end()
        return pix.toImage()

    def samples(self, img, pitch):
        from qt_editor.chart_view import _is_black_pitch, keyboard_height
        slot = self.view._pitch_to_slot(pitch)
        x1, x2 = self.view._key_draw_span(slot)
        top = self.view._piano_top_py()
        strip = keyboard_height()
        if _is_black_pitch(pitch):
            y = int(top + strip * 0.25)
            return [img.pixel(int(x1 + (x2 - x1) * f), y)
                    for f in self.BLACK_SAMPLE_FRACTIONS]
        return [img.pixel(int((x1 + x2) / 2), int(top + strip * 0.9))]

    def dirtied(self, plain, img, want_in_key: bool):
        """和沒開高亮相比，哪些鍵的顏色變了。"""
        from qt_editor.chart_view import PITCH_GRID_KEYS, PITCH_MIDI_MIN
        out = []
        for i in range(PITCH_GRID_KEYS):
            pitch = PITCH_MIDI_MIN + i
            if (pitch % 12 in self.in_key) != want_in_key:
                continue
            if self.samples(img, pitch) != self.samples(plain, pitch):
                out.append(pitch)
        return out

    def test_no_in_key_key_is_touched(self):
        # 舊做法（畫完再蓋一層灰）在這裡會弄髒 36 個調內黑鍵
        settings.set('pitch_scale_highlight', False)
        plain = self.render()
        settings.set('pitch_scale_highlight', True)
        lit = self.render()
        self.assertEqual(self.dirtied(plain, lit, want_in_key=True), [],
                         '調內的鍵被旁邊調外鍵的暗色波及了')

    def test_out_of_key_keys_do_get_dimmed(self):
        settings.set('pitch_scale_highlight', False)
        plain = self.render()
        settings.set('pitch_scale_highlight', True)
        lit = self.render()
        self.assertTrue(self.dirtied(plain, lit, want_in_key=False),
                        '調外的鍵本來就該變暗')

    def test_turning_it_off_restores_the_plain_keyboard(self):
        settings.set('pitch_scale_highlight', True)
        self.render()
        settings.set('pitch_scale_highlight', False)
        off = self.render()
        again = self.render()
        self.assertEqual(self.dirtied(off, again, want_in_key=True), [])
        self.assertEqual(self.dirtied(off, again, want_in_key=False), [])


class ScaleLockTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.build([make_note(i, i * 300, p)
                    for i, p in enumerate(C_MAJOR_PITCHES)])

    def test_off_by_default(self):
        self.assertFalse(self.view._scale_lock_on())
        self.assertEqual(self.view._lock_pitch(61), 61)

    def test_on_it_snaps_an_out_of_key_pitch(self):
        settings.set('pitch_scale_lock', True)
        self.assertIn(self.view._lock_pitch(61), (60, 62))

    def test_an_in_key_pitch_is_untouched(self):
        settings.set('pitch_scale_lock', True)
        for p in (60, 62, 64, 65, 67, 69, 71):
            self.assertEqual(self.view._lock_pitch(p), p)

    def test_no_key_means_no_lock(self):
        view = self.build([])
        settings.set('pitch_scale_lock', True)
        self.assertEqual(view._lock_pitch(61), 61)

    def test_it_does_not_apply_outside_pitch_mode(self):
        settings.set('pitch_scale_lock', True)
        self.view.set_view_mode('measure')
        self.assertEqual(self.view._lock_pitch(61), 61)

    def test_placing_a_note_obeys_the_lock(self):
        settings.set('pitch_scale_lock', True)
        slot = self.view._pitch_to_slot(61)          # C#，不在 C 大調
        px = sum(self.view._key_span(slot)) / 2.0
        from PyQt5.QtCore import QPoint
        before = len(self.model.notes_tree)
        self.view._place_note_at(QPoint(int(px), 300))
        self.assertEqual(len(self.model.notes_tree), before + 1)
        placed = max(self.model.notes_tree, key=lambda n: n.idx)
        self.assertIn(placed.pitch, (60, 62), '應該被吸到調內')

    def test_arrow_keys_stay_chromatic(self):
        # 鎖調不該把「我就是要這顆升記號」的逃生口關掉
        settings.set('pitch_scale_lock', True)
        note = self.model.notes_tree[0]
        self.view.selected = {note.idx}
        self.view.shift_selected_pitch(1, sync_keys=True)
        self.assertEqual(note.pitch, 61)


class SnapToKeyTests(ViewCase):
    def setUp(self):
        super().setUp()
        notes = [make_note(i, i * 300, p) for i, p in enumerate(C_MAJOR_PITCHES)]
        notes.append(make_note(len(notes), 3000, 61))     # 離調的 C#
        notes.append(make_note(len(notes), 3300, 66))     # 離調的 F#
        self.build(notes)

    def test_it_moves_only_the_out_of_key_notes(self):
        key = self.view._active_key()
        moved = self.view.snap_selected_to_key(key)
        self.assertEqual(moved, 2)
        self.assertTrue(all(key.contains(n.pitch) for n in self.model.notes_tree))

    def test_running_it_twice_with_the_same_key_is_idempotent(self):
        key = self.view._active_key()
        self.view.snap_selected_to_key(key)
        self.assertEqual(self.view.snap_selected_to_key(key), 0)

    def test_re_detecting_between_runs_can_drift(self):
        # 吸完之後全譜的音高分布就變了，自動偵測可能翻到關係調去。這正是
        # 主視窗在信心不足時要先問過使用者、把調釘住再傳進來的原因。
        self.view.snap_selected_to_key()
        first = self.view._active_key()
        self.view.snap_selected_to_key()
        self.assertIsNotNone(first)

    def test_an_explicit_key_wins_over_detection(self):
        self.view.snap_selected_to_key(Key(6, 'major'))
        self.assertTrue(all(Key(6, 'major').contains(n.pitch)
                            for n in self.model.notes_tree))

    def test_it_respects_the_selection(self):
        target = self.model.notes_tree[-1]
        self.view.selected = {target.idx}
        self.assertEqual(self.view.snap_selected_to_key(), 1)
        self.assertEqual(self.model.notes_tree[-2].pitch, 61, '沒選到的不該被動')

    def test_it_is_undoable(self):
        before = [n.pitch for n in self.model.notes_tree]
        self.view.snap_selected_to_key()
        self.model.undo()
        self.assertEqual([n.pitch for n in self.model.notes_tree], before)

    def test_no_key_is_a_no_op(self):
        view = self.build([])
        self.assertEqual(view.snap_selected_to_key(), 0)


class QuantizeTests(ViewCase):
    def setUp(self):
        super().setUp()
        # 120 BPM → 一拍 500ms、16 分音符 125ms。故意放在格線旁邊
        self.notes = [make_note(0, 10, 60), make_note(1, 260, 62),
                      make_note(2, 495, 64), make_note(3, 1000, 65)]
        self.build(self.notes)

    def test_full_strength_lands_on_the_grid(self):
        moved = self.view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.assertEqual(moved, 3, '本來就在格線上的那顆不算')
        for n in self.notes:
            self.assertEqual(n.start % 125, 0, n.start)

    def test_half_strength_goes_halfway(self):
        self.view.quantize_notes(0.25, 0.5, whole_chart=True)
        self.assertEqual(self.notes[0].start, 5)      # 10 → 0 的一半
        self.assertEqual(self.notes[1].start, 255)    # 260 → 250 的一半

    def test_zero_strength_does_nothing(self):
        self.assertEqual(self.view.quantize_notes(0.25, 0.0, whole_chart=True), 0)

    def test_an_already_aligned_chart_reports_nothing_moved(self):
        self.view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.assertEqual(self.view.quantize_notes(0.25, 1.0, whole_chart=True), 0)

    def test_length_is_preserved_by_default(self):
        before = [n.end - n.start for n in self.notes]
        self.view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.assertEqual([n.end - n.start for n in self.notes], before)

    def test_also_length_aligns_the_end_too(self):
        self.view.quantize_notes(0.25, 1.0, whole_chart=True, also_length=True)
        for n in self.notes:
            self.assertEqual(n.end % 125, 0, n.end)

    def test_a_note_never_collapses_to_zero_length(self):
        # 200ms 的音在 1/4 音符格線上，頭尾會被吸到同一格
        self.view.quantize_notes(1.0, 1.0, whole_chart=True,
                                 also_length=True, min_gate_beats=0.25)
        for n in self.notes:
            self.assertGreaterEqual(n.end - n.start, 125, (n.start, n.end))

    def test_gate_follows_the_new_length(self):
        self.view.quantize_notes(0.25, 1.0, whole_chart=True, also_length=True)
        for n in self.notes:
            self.assertEqual(n.gate, n.end - n.start)

    def test_it_only_touches_the_selection(self):
        self.view.selected = {self.notes[0].idx}
        self.assertEqual(self.view.quantize_notes(0.25, 1.0), 1)
        self.assertEqual(self.notes[1].start, 260, '沒選到的不該被動')

    def test_whole_chart_overrides_the_selection(self):
        self.view.selected = {self.notes[0].idx}
        self.assertEqual(self.view.quantize_notes(0.25, 1.0, whole_chart=True), 3)

    def test_it_is_undoable(self):
        before = [(n.start, n.end) for n in self.notes]
        self.view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.model.undo()
        self.assertEqual([(n.start, n.end) for n in self.model.notes_tree], before)

    def test_the_grid_follows_the_tempo(self):
        # 60 BPM → 一拍 1000ms，所以 1/4 拍的格線是 250ms 而不是 125ms
        notes = [make_note(0, 260, 60)]
        view = self.build(notes, bpm=60.0)
        view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.assertEqual(notes[0].start, 250)

    def test_starts_never_go_negative(self):
        notes = [make_note(0, 5, 60)]
        view = self.build(notes)
        view.quantize_notes(0.25, 1.0, whole_chart=True)
        self.assertGreaterEqual(notes[0].start, 0)

    def test_the_ui_grid_list_is_usable(self):
        for label, beats in ChartView.QUANTIZE_GRIDS:
            self.assertTrue(label)
            self.assertGreater(beats, 0)


class GhostNoteTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.notes = [make_note(0, 0, 72, hand=0), make_note(1, 300, 74, hand=0),
                      make_note(2, 600, 48, hand=1), make_note(3, 900, 50, hand=1)]
        self.build(self.notes)

    def test_nothing_is_a_ghost_by_default(self):
        self.assertFalse(any(self.view._is_ghost(n) for n in self.notes))

    def test_filtering_to_the_right_hand_ghosts_the_left(self):
        self.view.set_hand_filter(0)
        self.assertEqual([self.view._is_ghost(n) for n in self.notes],
                         [False, False, True, True])

    def test_filtering_to_the_left_hand_ghosts_the_right(self):
        self.view.set_hand_filter(1)
        self.assertEqual([self.view._is_ghost(n) for n in self.notes],
                         [True, True, False, False])

    def test_select_all_skips_the_ghosts(self):
        self.view.set_hand_filter(0)
        self.view.select_all()
        self.assertEqual(self.view.selected, {0, 1})

    def test_switching_the_filter_drops_the_now_invisible_selection(self):
        self.view.select_all()
        self.assertEqual(len(self.view.selected), 4)
        self.view.set_hand_filter(1)
        self.assertEqual(self.view.selected, {2, 3})

    def test_going_back_to_both_hands_ungshosts_everything(self):
        self.view.set_hand_filter(0)
        self.view.set_hand_filter('all')
        self.assertFalse(any(self.view._is_ghost(n) for n in self.notes))

    def test_preview_mode_shows_the_real_chart(self):
        # 預覽畫的是遊戲實際外觀，不能少一隻手
        self.view.set_hand_filter(0)
        self.view.preview_mode = True
        self.assertFalse(any(self.view._is_ghost(n) for n in self.notes))

    def test_an_unknown_filter_value_falls_back_to_both_hands(self):
        self.view.set_hand_filter('nope')
        self.assertEqual(self.view.hand_filter, 'all')

    def test_the_setting_only_hides_the_shadow_not_the_filter(self):
        settings.set('ghost_other_hand', False)
        self.view.set_hand_filter(0)
        self.assertFalse(self.view._ghosts_on())
        self.assertTrue(self.view._is_ghost(self.notes[2]),
                        '關掉影子只是不畫，還是選不到')

    def test_ghosts_are_not_hit_testable(self):
        self.view.set_hand_filter(0)
        self.view._visible = []
        from PyQt5.QtGui import QPainter, QPixmap
        pix = QPixmap(self.view.size())
        qp = QPainter(pix)
        try:
            self.view._draw_notes(qp)
        finally:
            qp.end()
        self.assertTrue(all(n.hand == 0 for _r, n in self.view._visible))


if __name__ == '__main__':
    unittest.main()
