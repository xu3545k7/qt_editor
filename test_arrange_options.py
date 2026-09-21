"""MIDI 轉譜的選項：模式、鍵道範圍、和絃重疊、自訂參數表。"""

import unittest

from qt_editor.arrange_options import (MODE_CUSTOM, MODE_EATHER, MODE_FLAT,
                                       MODE_OFFICIAL, TOTAL_GAME_KEYS,
                                       ArrangeOptions, arrange_notes, coerce_value,
                                       flat_layout)
from qt_editor.models import GNote, NoteModel
from qt_editor.smart_chart import STYLE_EATHER, STYLE_OFFICIAL


def note(idx, start, pitch, hand=0):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 200, 200
    n.pitch = pitch
    n.hand = hand
    n.min_key, n.max_key = 0, 2
    n.note_type = 0
    return n


def scale(count=16, step=200):
    """一串上行音，右手。"""
    return [note(i, i * step, 48 + i * 2) for i in range(count)]


def chord(pitches, hand=0):
    """同一刻的一疊音。"""
    return [note(i, 0, p, hand) for i, p in enumerate(pitches)]


def spans(notes):
    return [(int(n.min_key), int(n.max_key)) for n in notes]


def overlaps(notes):
    ranges = sorted((int(n.min_key), int(n.max_key)) for n in notes)
    return sum(ranges[i - 1][1] >= ranges[i][0] for i in range(1, len(ranges)))


class OptionsTests(unittest.TestCase):
    def test_defaults_are_the_whole_keyboard_and_eather(self):
        opt = ArrangeOptions().normalised()
        self.assertEqual((opt.lane_lo, opt.lane_hi), (0, TOTAL_GAME_KEYS - 1))
        self.assertEqual(opt.mode, MODE_EATHER)
        self.assertFalse(opt.lanes_limited)
        self.assertFalse(opt.overlap_allowed(), 'Eather 預設不重疊')

    def test_official_allows_overlap_by_default(self):
        self.assertTrue(ArrangeOptions(mode=MODE_OFFICIAL).overlap_allowed())

    def test_the_overlap_choice_beats_the_mode(self):
        self.assertFalse(
            ArrangeOptions(mode=MODE_OFFICIAL, allow_chord_overlap=False).overlap_allowed())
        self.assertTrue(
            ArrangeOptions(mode=MODE_EATHER, allow_chord_overlap=True).overlap_allowed())

    def test_backwards_range_is_fixed_and_too_narrow_is_widened(self):
        opt = ArrangeOptions(lane_lo=20, lane_hi=5).normalised()
        self.assertEqual((opt.lane_lo, opt.lane_hi), (5, 20))
        opt = ArrangeOptions(lane_lo=10, lane_hi=10).normalised()
        self.assertGreaterEqual(opt.lane_span, 3, '至少要放得下一顆寬度 3')

    def test_round_trip_through_settings_json(self):
        opt = ArrangeOptions(mode=MODE_CUSTOM, lane_lo=4, lane_hi=23,
                             allow_chord_overlap=True, base_style=STYLE_OFFICIAL,
                             overrides={'normal_width': 2, 'melody_step_top': (1.0, 2.0)})
        back = ArrangeOptions.from_dict(opt.to_dict())
        self.assertEqual(back.mode, MODE_CUSTOM)
        self.assertEqual((back.lane_lo, back.lane_hi), (4, 23))
        self.assertIs(back.allow_chord_overlap, True)
        self.assertEqual(back.base_style, STYLE_OFFICIAL)
        self.assertEqual(back.overrides['normal_width'], 2)
        self.assertEqual(back.overrides['melody_step_top'], (1.0, 2.0),
                         'JSON 存回來的 list 要轉回 tuple')

    def test_unknown_override_keys_are_dropped(self):
        back = ArrangeOptions.from_dict({'overrides': {'nope': 1, 'total_lanes': 5}})
        self.assertEqual(back.overrides, {}, '沒有的欄位與鎖住的欄位都不收')

    def test_build_settings_carries_range_overlap_and_overrides(self):
        opt = ArrangeOptions(mode=MODE_CUSTOM, lane_lo=4, lane_hi=23,
                             base_style=STYLE_EATHER, overrides={'normal_width': 2})
        cfg = opt.build_settings(beat_ms=400.0)
        self.assertEqual(cfg.total_lanes, 20)
        self.assertEqual(cfg.normal_width, 2)
        self.assertFalse(cfg.allow_chord_overlap)
        self.assertEqual(cfg.beat_ms, 400.0)

    def test_overrides_only_apply_to_custom(self):
        cfg = ArrangeOptions(mode=MODE_EATHER,
                             overrides={'normal_width': 1}).build_settings()
        self.assertEqual(cfg.normal_width, 3, '不是自訂模式就照風格走')

    def test_style_of_each_mode(self):
        self.assertEqual(ArrangeOptions(mode=MODE_OFFICIAL).style(), STYLE_OFFICIAL)
        self.assertEqual(ArrangeOptions(mode=MODE_FLAT).style(), STYLE_EATHER)
        self.assertEqual(
            ArrangeOptions(mode=MODE_CUSTOM, base_style=STYLE_OFFICIAL).style(),
            STYLE_OFFICIAL)

    def test_coerce_value_from_text(self):
        self.assertEqual(coerce_value('normal_width', '2'), 2)
        self.assertEqual(coerce_value('melody_step_top', '1, 2.5'), (1.0, 2.5))
        self.assertIs(coerce_value('snap_block_drift_slack', ''), None)
        self.assertIs(coerce_value('snap_repeat_final', 'false'), False)


class ArrangeRangeTests(unittest.TestCase):
    def test_every_note_lands_inside_the_range(self):
        notes = scale()
        arrange_notes(notes, ArrangeOptions(lane_lo=6, lane_hi=21))
        self.assertTrue(all(6 <= lo and hi <= 21 for lo, hi in spans(notes)),
                        spans(notes))

    def test_the_full_keyboard_still_uses_the_edges(self):
        notes = scale(24)
        arrange_notes(notes, ArrangeOptions())
        lo = min(s[0] for s in spans(notes))
        hi = max(s[1] for s in spans(notes))
        self.assertLess(lo, 6)
        self.assertGreater(hi, TOTAL_GAME_KEYS - 7)

    def test_a_narrow_range_still_keeps_pitch_order(self):
        notes = scale(12)
        arrange_notes(notes, ArrangeOptions(lane_lo=10, lane_hi=19))
        # 排譜器會自己分手，左右手各自佔一段鍵道，所以只看同一隻手裡的順序
        for hand in (0, 1):
            centres = [(int(n.min_key) + int(n.max_key)) / 2
                       for n in notes if int(n.hand) == hand]
            self.assertEqual(centres, sorted(centres),
                             '音高上行，同一隻手的鍵道不能倒退')

    def test_flat_mode_spreads_pitches_over_the_range(self):
        notes = scale(10)
        stats, opt = arrange_notes(notes, ArrangeOptions(mode=MODE_FLAT,
                                                         lane_lo=4, lane_hi=15))
        self.assertIsNone(stats, '平攤沒有統計')
        self.assertTrue(all(4 <= lo and hi <= 15 for lo, hi in spans(notes)))
        self.assertEqual(spans(notes)[0][0], 4, '最低音貼左界')
        self.assertEqual(spans(notes)[-1][1], 15, '最高音貼右界')
        self.assertTrue(all(hi - lo == 2 for lo, hi in spans(notes)), '平攤一律寬 3')

    def test_flat_layout_ignores_notes_without_pitch(self):
        notes = scale(4)
        notes[0].pitch = None
        self.assertEqual(flat_layout(notes), 3)


class OverlapTests(unittest.TestCase):
    """和絃的鍵道重疊：允許＝塞不下就疊上去；不允許＝一定排開。"""

    #: 6 顆音給 12 格：收窄成寬 2 剛好排得開，不收窄就一定得疊。
    #: 再窄下去連「不允許」也只能疊在邊緣（排譜器塞不下時的保底行為）。
    LANES = dict(lane_lo=8, lane_hi=19)

    def dense(self):
        # 單手同時 6 音，音程窄：不重疊就得收窄或撐開
        return chord([60, 62, 64, 65, 67, 69])

    def test_not_allowed_means_no_overlap(self):
        notes = self.dense()
        arrange_notes(notes, ArrangeOptions(mode=MODE_EATHER,
                                            allow_chord_overlap=False, **self.LANES))
        self.assertEqual(overlaps(notes), 0, spans(notes))

    def test_allowed_lets_a_dense_chord_overlap(self):
        notes = self.dense()
        arrange_notes(notes, ArrangeOptions(mode=MODE_EATHER,
                                            allow_chord_overlap=True, **self.LANES))
        self.assertGreater(overlaps(notes), 0, spans(notes))

    def test_room_enough_means_no_overlap_either_way(self):
        """整條鍵道放得下的時候，允許重疊也不會故意疊上去。"""
        notes = self.dense()
        arrange_notes(notes, ArrangeOptions(allow_chord_overlap=True))
        self.assertEqual(overlaps(notes), 0, spans(notes))

    def test_overlap_is_reported_in_the_stats(self):
        notes = self.dense()
        stats, _opt = arrange_notes(
            notes, ArrangeOptions(allow_chord_overlap=True, **self.LANES))
        self.assertEqual(stats.unresolved_overlaps, overlaps(notes),
                         '統計要報真正的重疊數，不能因為「允許」就報 0')
        self.assertGreater(stats.unresolved_overlaps, 0)

    def test_overlap_setting_does_not_leak_into_the_next_run(self):
        arrange_notes(self.dense(),
                      ArrangeOptions(allow_chord_overlap=True, **self.LANES))
        notes = self.dense()
        arrange_notes(notes,
                      ArrangeOptions(allow_chord_overlap=False, **self.LANES))
        self.assertEqual(overlaps(notes), 0, '上一次開了重疊，不能影響這一次')

    def test_notes_stay_on_the_keyboard_when_overlapping(self):
        notes = chord([48 + i for i in range(10)])
        arrange_notes(notes, ArrangeOptions(lane_lo=8, lane_hi=19,
                                            allow_chord_overlap=True))
        self.assertTrue(all(8 <= lo and hi <= 19 for lo, hi in spans(notes)),
                        spans(notes))


class ModelTests(unittest.TestCase):
    def model(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = scale(12)
        m.midi_unarranged = True
        m.rebuild_display_cache()
        return m

    def test_arrange_with_options_marks_the_chart_arranged(self):
        m = self.model()
        stats = m.arrange_with_options(ArrangeOptions(lane_lo=5, lane_hi=22))
        self.assertIsNotNone(stats)
        self.assertFalse(m.midi_unarranged)
        self.assertTrue(m.dirty)
        self.assertTrue(all(5 <= n.min_key and n.max_key <= 22 for n in m.notes_tree))

    def test_flat_mode_also_counts_as_arranged(self):
        m = self.model()
        self.assertIsNone(m.arrange_with_options(ArrangeOptions(mode=MODE_FLAT)))
        self.assertFalse(m.midi_unarranged)

    def test_the_style_is_remembered_on_the_model(self):
        m = self.model()
        m.arrange_with_options(ArrangeOptions(mode=MODE_OFFICIAL))
        self.assertEqual(m.chart_style, STYLE_OFFICIAL)


if __name__ == '__main__':
    unittest.main()


class DialogTests(unittest.TestCase):
    """轉譜對話框：選項進得去、出得來，預設集存得住。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from qt_editor.settings import settings
        self._saved = (settings.get('arrange_options'), settings.get('arrange_presets'))
        settings.set('arrange_options', {})
        settings.set('arrange_presets', {})

    def tearDown(self):
        from qt_editor.settings import settings
        settings.set('arrange_options', self._saved[0])
        settings.set('arrange_presets', self._saved[1])

    def dialog(self, **kwargs):
        from qt_editor.arrange_dialog import ArrangeDialog
        return ArrangeDialog(None, **kwargs)

    def test_lane_spins_are_one_based_on_screen(self):
        dlg = self.dialog(options=ArrangeOptions(lane_lo=4, lane_hi=23))
        self.assertEqual((dlg.lane_lo.value(), dlg.lane_hi.value()), (5, 24),
                         '畫面上第 1 格 = 內部的 0')
        self.assertTrue(dlg.limit_lanes.isChecked())
        self.assertEqual((dlg.options().lane_lo, dlg.options().lane_hi), (4, 23))

    def test_unchecking_the_limit_gives_the_whole_keyboard(self):
        dlg = self.dialog(options=ArrangeOptions(lane_lo=4, lane_hi=23))
        dlg.limit_lanes.setChecked(False)
        opt = dlg.options()
        self.assertEqual((opt.lane_lo, opt.lane_hi), (0, TOTAL_GAME_KEYS - 1))
        self.assertFalse(dlg.lane_lo.isEnabled(), '沒勾就不能動那兩個數字')

    def test_overlap_choices(self):
        from qt_editor.arrange_dialog import OVERLAP_OFF, OVERLAP_ON
        dlg = self.dialog()
        self.assertIsNone(dlg.options().allow_chord_overlap, '預設照模式決定')
        dlg.overlap.setCurrentIndex(OVERLAP_ON)
        self.assertIs(dlg.options().allow_chord_overlap, True)
        dlg.overlap.setCurrentIndex(OVERLAP_OFF)
        self.assertIs(dlg.options().allow_chord_overlap, False)

    def test_flat_mode_disables_the_overlap_choice(self):
        dlg = self.dialog()
        dlg._modes[MODE_FLAT].setChecked(True)
        self.assertFalse(dlg.overlap.isEnabled())
        self.assertFalse(dlg.custom_box.isVisible())

    def test_the_table_has_every_arranger_parameter(self):
        from dataclasses import fields
        from qt_editor.arrange_options import LOCKED_FIELDS
        from qt_editor.smart_chart import SmartChartSettings
        dlg = self.dialog()
        names = set(dlg.table._widgets)
        expected = {f.name for f in fields(SmartChartSettings)} - set(LOCKED_FIELDS)
        self.assertEqual(names, expected)

    def test_only_changed_parameters_become_overrides(self):
        dlg = self.dialog()
        dlg._modes[MODE_CUSTOM].setChecked(True)
        self.assertEqual(dlg.options().overrides, {}, '沒改就沒有覆寫')
        dlg.table._set_widget('normal_width', 2)
        self.assertEqual(dlg.options().overrides, {'normal_width': 2})

    def test_switching_the_base_style_reloads_the_table(self):
        dlg = self.dialog()
        dlg._modes[MODE_CUSTOM].setChecked(True)
        dlg.base_style.setCurrentIndex(1)              # 官方
        self.assertEqual(dlg.options().base_style, STYLE_OFFICIAL)
        self.assertEqual(dlg.options().overrides, {},
                         '換底之後表格就是那個風格的值，不該整片變成覆寫')

    def test_overrides_are_dropped_when_the_mode_is_not_custom(self):
        dlg = self.dialog()
        dlg._modes[MODE_CUSTOM].setChecked(True)
        dlg.table._set_widget('normal_width', 2)
        dlg._modes[MODE_EATHER].setChecked(True)
        self.assertEqual(dlg.options().overrides, {})

    def test_accept_remembers_the_options(self):
        from qt_editor.settings import settings
        dlg = self.dialog()
        dlg._modes[MODE_OFFICIAL].setChecked(True)
        dlg.limit_lanes.setChecked(True)
        dlg.lane_lo.setValue(3)
        dlg.lane_hi.setValue(26)
        dlg.accept()
        back = ArrangeOptions.from_dict(settings.get('arrange_options'))
        self.assertEqual(back.mode, MODE_OFFICIAL)
        self.assertEqual((back.lane_lo, back.lane_hi), (2, 25))
        self.assertFalse(dlg.skipped)

    def test_the_next_dialog_starts_from_last_time(self):
        dlg = self.dialog()
        dlg._modes[MODE_OFFICIAL].setChecked(True)
        dlg.accept()
        fresh = self.dialog()          # 要留著參照，不然 Qt 物件會被回收
        self.assertTrue(fresh._modes[MODE_OFFICIAL].isChecked())

    def test_skip_button_only_exists_when_asked_for(self):
        from PyQt5.QtWidgets import QPushButton
        def texts(dlg):
            return [b.text() for b in dlg.findChildren(QPushButton)]
        self.assertFalse(any('先不轉譜' in x for x in texts(self.dialog())))
        dlg = self.dialog(allow_skip=True)
        self.assertTrue(any('先不轉譜' in x for x in texts(dlg)))
        dlg._skip()
        self.assertTrue(dlg.skipped)

    def test_presets_round_trip(self):
        from qt_editor.settings import settings
        dlg = self.dialog()
        dlg._modes[MODE_CUSTOM].setChecked(True)
        dlg.table._set_widget('normal_width', 2)
        dlg.limit_lanes.setChecked(True)
        dlg.lane_lo.setValue(5)
        dlg.lane_hi.setValue(24)
        settings.set('arrange_presets', {'窄一點': dlg.options().to_dict()})
        fresh = self.dialog()
        fresh._refresh_presets('窄一點')
        fresh._load_preset()
        opt = fresh.options()
        self.assertEqual(opt.mode, MODE_CUSTOM)
        self.assertEqual((opt.lane_lo, opt.lane_hi), (4, 23))
        self.assertEqual(opt.overrides, {'normal_width': 2})


class MainWindowTests(unittest.TestCase):
    """主視窗：轉譜對話框接在哪些動作上。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def midi_model(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = scale(12)
        m.midi_unarranged = True
        m.rebuild_display_cache()
        m.dirty = False
        self.win._load_model_all(m)
        return m

    def test_cancelling_the_dialog_leaves_the_chart_alone(self):
        m = self.midi_model()
        before = spans(m.notes_tree)
        self.win.ask_arrange_options = lambda *a, **k: (None, None)
        try:
            self.assertFalse(self.win._arrange_now())
        finally:
            del self.win.ask_arrange_options
        self.assertEqual(spans(m.notes_tree), before)
        self.assertTrue(m.midi_unarranged, '取消就是留在 MIDI 模式')

    def test_arrange_now_uses_the_options_it_was_given(self):
        m = self.midi_model()
        self.assertTrue(self.win._arrange_now(ArrangeOptions(lane_lo=6, lane_hi=21)))
        self.assertFalse(m.midi_unarranged)
        self.assertTrue(all(6 <= lo and hi <= 21 for lo, hi in spans(m.notes_tree)),
                        spans(m.notes_tree))

    def test_rearrange_needs_pitches(self):
        from unittest import mock
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = scale(4)
        for n in m.notes_tree:
            n.pitch = None
        m.rebuild_display_cache()
        m.dirty = False
        self.win._load_model_all(m)
        with mock.patch('qt_editor.main_window.QMessageBox.information') as info:
            self.win.rearrange_dialog()
        self.assertTrue(info.called, '沒有音高就要講清楚，不是默默不動')


class ParamTextTests(unittest.TestCase):
    """參數表的中文名稱與說明。"""

    def fields(self):
        from dataclasses import fields as dc_fields
        from qt_editor.smart_chart import SmartChartSettings
        return [f.name for f in dc_fields(SmartChartSettings)]

    def test_every_parameter_has_a_chinese_name(self):
        from qt_editor.arrange_param_text import LABELS
        missing = [n for n in self.fields() if n not in LABELS]
        self.assertEqual(missing, [], '這些參數還沒翻譯：%s' % missing)

    def test_labels_are_not_just_the_field_name(self):
        from qt_editor.arrange_param_text import LABELS, label_for
        for name in self.fields():
            self.assertNotEqual(LABELS[name], name, name)
            self.assertNotEqual(label_for(name), name)

    def test_labels_are_unique(self):
        from qt_editor.arrange_param_text import LABELS
        names = [LABELS[n] for n in self.fields()]
        self.assertEqual(len(names), len(set(names)), '中文名不能重複')

    def test_an_unknown_field_falls_back_to_its_name(self):
        from qt_editor.arrange_param_text import label_for
        self.assertEqual(label_for('brand_new_knob'), 'brand_new_knob')

    def test_the_tooltip_keeps_the_field_name_and_adds_the_comment(self):
        from qt_editor.arrange_param_text import doc_for, tooltip_for
        tip = tooltip_for('cross_hand_slack_lanes')
        self.assertTrue(tip.startswith('cross_hand_slack_lanes'), tip)
        self.assertIn('兩手', doc_for('cross_hand_slack_lanes'),
                      '說明要來自 smart_chart 裡的註解')

    def test_grouping_covers_every_field_exactly_once(self):
        from qt_editor.arrange_param_text import grouped_fields
        seen = [n for _title, names in grouped_fields() for n in names]
        self.assertEqual(sorted(seen), sorted(self.fields()))
        self.assertEqual(len(seen), len(set(seen)), '同一個參數不能出現在兩組')


class ParamTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def table(self):
        from qt_editor.arrange_dialog import _ParamTable
        self._t = _ParamTable()
        return self._t

    def test_rows_show_the_chinese_name(self):
        from qt_editor.arrange_param_text import label_for
        t = self.table()
        self.assertEqual(t._labels['normal_width'].text(), label_for('normal_width'))
        self.assertIn('normal_width', t._widgets['normal_width'].toolTip())

    def test_search_works_in_chinese_and_english(self):
        t = self.table()
        t.search.setText('寬度')
        self.assertTrue(t._widgets['normal_width'].isVisible()
                        or not t._widgets['normal_width'].isHidden())
        self.assertTrue(t._widgets['snap_repeat_reach'].isHidden(), '不相關的要收起來')
        t.search.setText('snap_repeat')
        self.assertFalse(t._widgets['snap_repeat_reach'].isHidden())
        t.search.setText('')
        self.assertFalse(t._widgets['normal_width'].isHidden())

    def test_searching_a_group_name_brings_the_whole_group(self):
        t = self.table()
        t.search.setText('表情記號')
        self.assertFalse(t._widgets['classify_slide'].isHidden())
        self.assertTrue(t._widgets['normal_width'].isHidden())


class ParamDocFallbackTests(unittest.TestCase):
    """打包之後讀不到原始碼時，說明要從帶著的 smart_chart.py 撈回來。"""

    def test_the_fallback_reads_the_bundled_source(self):
        from unittest import mock
        import qt_editor.arrange_param_text as T
        T._docs_cache.clear()
        try:
            with mock.patch.object(T.inspect, 'getsource', side_effect=OSError):
                self.assertIn('兩手', T.doc_for('cross_hand_slack_lanes'))
        finally:
            T._docs_cache.clear()

    def test_no_source_at_all_is_not_a_crash(self):
        from unittest import mock
        import qt_editor.arrange_param_text as T
        T._docs_cache.clear()
        try:
            with mock.patch.object(T, '_settings_source', return_value=''):
                self.assertEqual(T.doc_for('cross_hand_slack_lanes'), '')
                self.assertEqual(T.tooltip_for('normal_width'), 'normal_width')
        finally:
            T._docs_cache.clear()


class ModeVisibilityTests(unittest.TestCase):
    """「上次按了直接平攤」不能默默一路沿用下去卻沒人看得出來。

    回報：匯入 MIDI 之後譜面「碎碎的」。排譜演算法沒有變（同一份 MIDI 新舊
    程式碼逐項相同），最可能就是模式被記住了。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from qt_editor.settings import settings
        self._saved = settings.get('arrange_options')
        settings.set('arrange_options', {})

    def tearDown(self):
        from qt_editor.settings import settings
        settings.set('arrange_options', self._saved)

    def dialog(self, **kwargs):
        from qt_editor.arrange_dialog import ArrangeDialog
        self._dlg = ArrangeDialog(None, **kwargs)
        return self._dlg

    def test_flat_mode_is_called_out_in_red(self):
        dlg = self.dialog()
        dlg._modes[MODE_FLAT].setChecked(True)
        self.assertIn('不會排譜', dlg.hint.text())
        self.assertIn('b00', dlg.hint.styleSheet(), '要顯眼')

    def test_smart_modes_are_not_scary(self):
        dlg = self.dialog()
        dlg._modes[MODE_EATHER].setChecked(True)
        self.assertNotIn('不會排譜', dlg.hint.text())

    def test_reset_puts_everything_back(self):
        dlg = self.dialog()
        dlg._modes[MODE_FLAT].setChecked(True)
        dlg.limit_lanes.setChecked(True)
        dlg.lane_lo.setValue(6)
        dlg.lane_hi.setValue(12)
        dlg._reset_to_defaults()
        opt = dlg.options()
        self.assertEqual(opt.mode, MODE_EATHER)
        self.assertFalse(opt.lanes_limited)
        self.assertIsNone(opt.allow_chord_overlap)


class AnnounceTests(unittest.TestCase):
    """轉完要講是用哪個模式排的。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def message_for(self, options):
        self.win.statusBar().clearMessage()
        self.win._announce_arrange_mode(options)
        return self.win.statusBar().currentMessage()

    def test_flat_mode_says_so_and_how_to_redo_it(self):
        text = self.message_for(ArrangeOptions(mode=MODE_FLAT))
        self.assertIn('直接平攤', text)
        self.assertIn('沒有跑智能排譜', text)
        self.assertIn('MIDI 轉譜', text, '要告訴人怎麼重排')

    def test_smart_mode_names_the_style(self):
        self.assertIn('Eather', self.message_for(ArrangeOptions(mode=MODE_EATHER)))
        self.assertIn('官方', self.message_for(ArrangeOptions(mode=MODE_OFFICIAL)))

    def test_a_limited_lane_range_is_mentioned(self):
        text = self.message_for(ArrangeOptions(lane_lo=5, lane_hi=20))
        self.assertIn('6', text)
        self.assertIn('21', text)


class TrimPedalHoldsTests(unittest.TestCase):
    """裁「踏板殘響造成的長音」會縮短長押，所以不能每次排譜都做。

    回報：用「重新排整份譜面」之後「很多長音變短」。匯入新 MIDI 該裁（那些
    長度是踏板造成的），但已經排好、長押手動調過的譜不該被動到。
    """

    def test_a_fresh_midi_is_trimmed_by_default(self):
        self.assertTrue(ArrangeOptions().should_trim_pedal_holds(True))

    def test_an_arranged_chart_is_left_alone_by_default(self):
        self.assertFalse(ArrangeOptions().should_trim_pedal_holds(False))

    def test_an_explicit_choice_wins_either_way(self):
        self.assertFalse(
            ArrangeOptions(trim_pedal_holds=False).should_trim_pedal_holds(True))
        self.assertTrue(
            ArrangeOptions(trim_pedal_holds=True).should_trim_pedal_holds(False))

    def test_the_choice_survives_a_round_trip(self):
        for value in (True, False, None):
            back = ArrangeOptions.from_dict(
                ArrangeOptions(trim_pedal_holds=value).to_dict())
            self.assertIs(back.trim_pedal_holds, value)


class TrimInTheWindowTests(unittest.TestCase):
    """主視窗實際上有沒有裁。"""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io as _io
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from qt_editor.main_window import MainWindow
        with contextlib.redirect_stdout(_io.StringIO()):
            cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win.view.model.dirty = False
        cls.win.close()

    def chart(self, unarranged):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.notes_tree = scale(8)
        for n in m.notes_tree:
            n.note_type = 2
            n.end = n.start + 4000        # 手動調長的長押
            n.gate = 4000
        m.midi_unarranged = unarranged
        m.rebuild_display_cache()
        m.dirty = False
        self.win._load_model_all(m)
        return m

    def trimmed_when(self, unarranged, options):
        from unittest import mock
        m = self.chart(unarranged)
        with mock.patch.object(NoteModel, 'trim_pedal_sustained_holds',
                               autospec=True, return_value=0) as trim:
            self.win._arrange_now(options)
        return trim.called

    def test_an_arranged_chart_keeps_its_note_lengths(self):
        self.assertFalse(self.trimmed_when(False, ArrangeOptions()),
                         '已經排好的譜不該被裁長音')

    def test_a_fresh_midi_still_gets_trimmed(self):
        self.assertTrue(self.trimmed_when(True, ArrangeOptions()))

    def test_ticking_the_box_trims_anyway(self):
        self.assertTrue(self.trimmed_when(False, ArrangeOptions(trim_pedal_holds=True)))

    def test_unticking_it_protects_a_fresh_midi_too(self):
        self.assertFalse(self.trimmed_when(True, ArrangeOptions(trim_pedal_holds=False)))

    def test_lengths_really_survive_a_rearrange(self):
        m = self.chart(False)
        before = [(int(n.start), int(n.end)) for n in m.notes_tree]
        self.win._arrange_now(ArrangeOptions())
        self.assertEqual([(int(n.start), int(n.end)) for n in m.notes_tree], before,
                         '重排只動鍵道，時間與長度都不能變')
