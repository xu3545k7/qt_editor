"""XML / JSON 都要能完整還原 MIDI 帶進來的力度與踏板。

以前有兩個破口：

1. 踏板存檔時四捨五入到整數毫秒。鋼琴家快速換踏的兩段之間只差 1.4ms，四捨五入
   後間隔縮到 1ms 以內，讀檔時 `_normalise_pedal_spans` 就把它們併成一段——那
   一抬正是為了把延音清掉，合併之後整段糊在一起。實測 76 段變 31 段。
2. XML 完全沒有存強弱記號（JSON 有）。
"""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qt_editor.models import GNote, NoteModel


def make_note(idx, start, pitch, velocity, hand=0):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200
    n.gate = 200
    n.pitch = pitch
    n.hand = hand
    n.velocity = velocity
    n.min_key = 4
    n.max_key = 6
    return n


class RoundTripTests(unittest.TestCase):
    """同一份資料存成 XML / JSON 再讀回來，力度、踏板、強弱記號都要一模一樣。"""

    FORMATS = (('xml', 'save_xml', 'load_xml'), ('json', 'save_json', 'load_json'))

    def _source(self):
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = [make_note(i, i * 500, 60 + i, 30 + i * 12) for i in range(6)]
        m.rebuild_display_cache()
        # 快速換踏：兩段之間只差 1.4ms
        m.pedal_spans = [[2.778, 1334.723], [1336.112, 2668.057],
                         [2669.446, 4001.391]]
        m.dynamics_add(0, 0, 96, ramp=True)
        m.dynamics_add(0, 2000, 48, ramp=False)
        m.dynamics_add(1, 500, 64, ramp=False)
        return m

    def _roundtrip(self, m, ext, save, load):
        path = os.path.join(tempfile.mkdtemp(), 'rt.' + ext)
        getattr(m, save)(path)
        back = NoteModel()
        getattr(back, load)(path)
        return back

    def test_velocities_survive(self):
        src = self._source()
        want = [n.velocity for n in sorted(src.notes_tree, key=lambda n: n.start)]
        for ext, save, load in self.FORMATS:
            back = self._roundtrip(src, ext, save, load)
            got = [n.velocity for n in sorted(back.notes_tree, key=lambda n: n.start)]
            self.assertEqual(got, want, ext)

    def test_rapid_pedal_changes_are_not_merged_away(self):
        src = self._source()
        for ext, save, load in self.FORMATS:
            back = self._roundtrip(src, ext, save, load)
            self.assertEqual(len(back.pedal_spans), 3,
                             '%s：換踏被併掉了' % ext)

    def test_pedal_times_stay_within_a_millisecond(self):
        src = self._source()
        for ext, save, load in self.FORMATS:
            back = self._roundtrip(src, ext, save, load)
            for before, after in zip(src.pedal_spans, back.pedal_spans):
                self.assertAlmostEqual(after[0], before[0], delta=1.0, msg=ext)
                self.assertAlmostEqual(after[1], before[1], delta=1.0, msg=ext)

    def test_saving_twice_does_not_drift(self):
        src = self._source()
        for ext, save, load in self.FORMATS:
            once = self._roundtrip(src, ext, save, load)
            twice = self._roundtrip(once, ext, save, load)
            self.assertEqual([list(s) for s in twice.pedal_spans],
                             [list(s) for s in once.pedal_spans], ext)

    def test_dynamics_survive_in_both_formats(self):
        src = self._source()
        for ext, save, load in self.FORMATS:
            back = self._roundtrip(src, ext, save, load)
            self.assertEqual(back.dynamics_marks(0),
                             [[0.0, 96.0, True], [2000.0, 48.0, False]], ext)
            self.assertEqual(back.dynamics_marks(1), [[500.0, 64.0, False]], ext)

    def test_no_pedal_or_dynamics_means_no_extra_nodes(self):
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        m.notes_tree = [make_note(0, 0, 60, 90)]
        m.rebuild_display_cache()
        for ext, save, load in self.FORMATS:
            back = self._roundtrip(m, ext, save, load)
            self.assertEqual(back.pedal_spans, [], ext)
            self.assertEqual(back.dynamics, {}, ext)

    def test_spans_shorter_than_a_millisecond_are_dropped(self):
        # 取整之後長度歸零的區間不該留下 start==end 的殘骸
        self.assertEqual(NoteModel._pedal_spans_for_file([[10.2, 10.4]]), [])
        self.assertEqual(NoteModel._pedal_spans_for_file([[10.0, 20.0]]), [(10, 20)])


class OfficialSubNoteVelocityTests(unittest.TestCase):
    """官方格式的力度記在 `<sub_note>` 上，`<note>` 根本沒有 velocity 欄位。

    以前載入時只讀 sub 的 `scale_piano`，velocity 留在元素裡沒進記憶體——檔案
    存回去是無損的，但編輯器看不到那些力度：音符上的數字空白、強弱曲線沒資料、
    放新音抄不到鄰居、鋼琴音軌只能用預設值。
    """

    def _chart(self):
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        note = make_note(0, 0, 60, None)
        sub = ET.Element('sub_note')
        for tag, val, typ in (('start_timing_msec', 0, 's32'),
                              ('end_timing_msec', 200, 's32'),
                              ('scale_piano', 40, 's32'),
                              ('velocity', 83, 'u8'),
                              ('track_index', 1, 's32')):
            el = ET.SubElement(sub, tag)
            el.text = str(val)
            el.set('__type', typ)
        note.sub_elems = [sub]
        m.notes_tree = [note]
        m.rebuild_display_cache()
        return m, note

    def test_velocity_is_taken_from_the_sub_note(self):
        m, note = self._chart()
        self.assertIsNone(note.velocity)
        self.assertEqual(m._load_velocity_from_subs(), 1)
        self.assertEqual(note.velocity, 83)

    def test_an_existing_velocity_is_not_overwritten(self):
        m, note = self._chart()
        note.velocity = 44
        self.assertEqual(m._load_velocity_from_subs(), 0)
        self.assertEqual(note.velocity, 44)

    def test_editing_the_velocity_writes_back_into_the_sub_note(self):
        m, note = self._chart()
        m._load_velocity_from_subs()
        note.velocity = 7
        m._write_velocity_into_subs()
        self.assertEqual(note.sub_elems[0].findtext('velocity'), '7')

    def test_a_note_without_subs_keeps_its_own_field(self):
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        note = make_note(0, 0, 60, 91)
        m.notes_tree = [note]
        m.rebuild_display_cache()
        m._write_velocity_into_subs()       # 沒有 sub 可寫，不該炸
        self.assertEqual(note.velocity, 91)

    def test_out_of_range_values_are_clamped(self):
        m, note = self._chart()
        note.sub_elems[0].find('velocity').text = '999'
        m._load_velocity_from_subs()
        self.assertLessEqual(note.velocity, 127)


if __name__ == '__main__':
    unittest.main()
