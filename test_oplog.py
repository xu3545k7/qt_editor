"""操作紀錄：把編輯前後的差異寫成可分析的 JSONL。

重點不是「按了什麼」，而是**跑完工具之後使用者又手動改了什麼**——那個方向
就是演算法該調的方向。所以每筆都帶統計輪廓（鍵寬、和弦大小、同手重疊率、
左右手比例）和抽樣的前後值。
"""

import json
import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel
from qt_editor import oplog as oplog_mod
from qt_editor.oplog import OpLog, profile


def note(idx, start, lo=4, hi=6, hand=0, pitch=60, dur=200, ntype=0):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch, n.hand, n.velocity = pitch, hand, 90
    n.min_key, n.max_key, n.note_type = lo, hi, ntype
    return n


class FakeModel:
    def __init__(self, notes):
        self.notes_tree = notes
        self.bpm = 120.0


class ProfileTests(unittest.TestCase):
    def test_an_empty_chart_profiles_without_crashing(self):
        self.assertEqual(profile(FakeModel([]))['notes'], 0)

    def test_width_and_hand_are_summarised(self):
        m = FakeModel([note(0, 0, 4, 6), note(1, 500, 4, 4, hand=1)])
        p = profile(m)
        self.assertEqual(p['width_hist'], {'1': 1, '3': 1})
        self.assertEqual(p['mean_width'], 2.0)
        self.assertEqual(p['left_hand_ratio'], 0.5)

    def test_simultaneous_notes_form_one_chord(self):
        m = FakeModel([note(0, 0, 4, 5), note(1, 5, 8, 9), note(2, 900, 4, 5)])
        p = profile(m)
        self.assertEqual(p['chord_size_hist'], {'1': 1, '2': 1})

    def test_overlap_ratio_counts_only_the_same_hand(self):
        same = FakeModel([note(0, 0, 4, 6), note(1, 0, 5, 7)])
        self.assertEqual(profile(same)['overlap_ratio'], 1.0)
        other = FakeModel([note(0, 0, 4, 6), note(1, 0, 5, 7, hand=1)])
        self.assertEqual(profile(other)['same_hand_pairs'], 0)
        self.assertEqual(profile(other)['overlap_ratio'], 0.0)

    def test_disjoint_lanes_are_not_an_overlap(self):
        m = FakeModel([note(0, 0, 0, 2), note(1, 0, 5, 7)])
        self.assertEqual(profile(m)['overlap_ratio'], 0.0)


class LogFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = OpLog()
        self.log.enabled = True         # 測試裡預設是關的，見 _under_test
        self.log._path = os.path.join(self.dir, 'oplog.jsonl')

    def records(self):
        if not os.path.isfile(self.log._path):
            return []
        with open(self.log._path, encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_nothing_is_written_when_disabled(self):
        self.log.enabled = False
        self.log.mark('save')
        self.assertEqual(self.records(), [])

    def test_an_edit_records_what_changed(self):
        m = FakeModel([note(0, 0, 4, 6), note(1, 500, 4, 6)])
        self.log.on_edit(m, 'narrow_chords')
        m.notes_tree[0].max_key = 5
        m.notes_tree[1].hand = 1
        self.log.flush(m)
        rec = self.records()[-1]
        self.assertEqual(rec['kind'], 'action')
        self.assertEqual(rec['action'], 'narrow_chords')
        self.assertEqual(rec['counts'], {'lane': 1, 'hand': 1})

    def test_adds_and_deletes_are_distinguished(self):
        m = FakeModel([note(0, 0)])
        self.log.on_edit(m, 'edit')
        m.notes_tree = [note(1, 700)]
        self.log.flush(m)
        self.assertEqual(self.records()[-1]['counts'], {'added': 1, 'removed': 1})

    def test_a_no_op_is_not_logged(self):
        m = FakeModel([note(0, 0)])
        self.log.on_edit(m, 'nothing')
        self.log.flush(m)
        self.assertEqual(self.records(), [], '沒改到東西不該留下噪音')

    def test_samples_are_capped(self):
        m = FakeModel([note(i, i * 100) for i in range(200)])
        self.log.on_edit(m, 'big')
        for n in m.notes_tree:
            n.hand = 1
        self.log.flush(m)
        rec = self.records()[-1]
        self.assertEqual(rec['counts']['hand'], 200, '計數要完整')
        self.assertLessEqual(len(rec['samples']), oplog_mod.MAX_SAMPLES)

    def test_the_profile_delta_shows_the_direction(self):
        """調演算法就是看這個：工具把輪廓往哪邊推。"""
        m = FakeModel([note(0, 0, 4, 8), note(1, 500, 4, 8)])
        self.log.on_edit(m, 'narrow')
        for n in m.notes_tree:
            n.max_key = 5
        self.log.flush(m)
        rec = self.records()[-1]
        self.assertEqual(rec['profile_delta']['mean_width'], -3.0)

    def test_params_are_attached_to_the_next_action(self):
        m = FakeModel([note(0, 0)])
        self.log.params(gap_ms=40, sequential_gap=True)
        self.log.on_edit(m, 'resolve_hold_tails_dialog')
        m.notes_tree[0].end = 100
        self.log.flush(m)
        rec = self.records()[-1]
        self.assertEqual(rec['params'], {'gap_ms': 40, 'sequential_gap': True})

    def test_params_do_not_leak_into_the_action_after(self):
        m = FakeModel([note(0, 0)])
        self.log.params(gap_ms=40)
        self.log.on_edit(m, 'first')
        m.notes_tree[0].end = 100
        self.log.on_edit(m, 'second')
        m.notes_tree[0].end = 150
        self.log.flush(m)
        first, second = self.records()[-2:]
        self.assertIn('params', first)
        self.assertNotIn('params', second)

    def test_an_undo_right_after_an_action_is_visible(self):
        """緊接在 action 之後的 undo 是最強的負面訊號。"""
        m = FakeModel([note(0, 0)])
        self.log.on_edit(m, 'some_tool')
        m.notes_tree[0].hand = 1
        self.log.undone(m)
        kinds = [r['kind'] for r in self.records()]
        self.assertEqual(kinds[-2:], ['action', 'undo'])

    def test_a_broken_log_path_disables_instead_of_raising(self):
        self.log._path = os.path.join(self.dir, 'nope', 'deep', 'x.jsonl')
        self.log.mark('save')
        self.assertFalse(self.log.enabled, '寫不進去就該自己關掉，不能往外丟例外')


class UnderTestGuardTests(unittest.TestCase):
    """測試套件不該汙染使用者的正式紀錄。"""

    def test_a_fresh_log_is_off_while_testing(self):
        self.assertFalse(OpLog().enabled,
                         '跑測試時新建的 OpLog 應該是關的')

    def test_the_env_var_forces_it_on(self):
        import os as _os
        old = _os.environ.get('NOS_OPLOG')
        _os.environ['NOS_OPLOG'] = '1'
        try:
            self.assertTrue(OpLog().enabled)
        finally:
            if old is None:
                _os.environ.pop('NOS_OPLOG', None)
            else:
                _os.environ['NOS_OPLOG'] = old


    def test_configure_cannot_override_the_test_guard(self):
        """MainWindow 在測試裡也會被建出來，不能讓它把保護蓋掉。"""
        log = OpLog()
        log.configure(True)
        self.assertFalse(log.enabled)

    def test_configure_can_still_turn_it_off(self):
        import os as _os
        _os.environ['NOS_OPLOG'] = '1'
        try:
            log = OpLog()
            log.configure(False)
            self.assertFalse(log.enabled)
            log.configure(True)
            self.assertTrue(log.enabled)
        finally:
            _os.environ.pop('NOS_OPLOG', None)


class RealModelIntegrationTests(unittest.TestCase):
    """真的走 push_history —— 69 個編輯呼叫點共用的那一條路。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (oplog_mod.oplog._path, oplog_mod.oplog.enabled,
                      oplog_mod.oplog._pending)
        oplog_mod.oplog._path = os.path.join(self.dir, 'oplog.jsonl')
        oplog_mod.oplog.enabled = True
        oplog_mod.oplog._pending = None

    def tearDown(self):
        (oplog_mod.oplog._path, oplog_mod.oplog.enabled,
         oplog_mod.oplog._pending) = self.saved

    def records(self):
        p = oplog_mod.oplog._path
        if not os.path.isfile(p):
            return []
        with open(p, encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]

    def model(self):
        m = NoteModel.create_new('t', 120.0, 60.0, 4)
        m.ensure_precise_beat_grid()
        m.notes_tree = [note(i, i * 500) for i in range(4)]
        m.rebuild_display_cache()
        return m

    def test_push_history_names_the_calling_function(self):
        m = self.model()

        def my_tool():
            m.push_history()
        my_tool()
        m.notes_tree[0].hand = 1
        oplog_mod.oplog.flush(m)
        self.assertEqual(self.records()[-1]['action'], 'my_tool')

    def test_a_global_shift_is_recorded_as_a_time_change(self):
        m = self.model()
        m.push_history()
        m.shift_all_time(1000)
        oplog_mod.oplog.flush(m)
        rec = self.records()[-1]
        self.assertEqual(rec['counts'].get('time'), 4)

    def test_logging_never_breaks_the_edit(self):
        m = self.model()
        oplog_mod.oplog.enabled = False
        m.push_history()
        m.shift_all_time(500)
        self.assertEqual(m.notes_tree[0].start, 500)
        self.assertEqual(self.records(), [])


if __name__ == '__main__':
    unittest.main()
