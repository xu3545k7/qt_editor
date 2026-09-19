"""JSON 是原始檔、XML 是給 PAN（本家）的相容輸出。

以前編輯器存的 XML 在 PAN 裡幾乎是空譜：編輯器新增的音符沒有 `<sub_note>`
（PAN 會整顆丟掉）、沒有 `<track_info>`、Staccato 的 note_type 3 被 PAN 當成
長押、還多出好幾個 PAN 沒有的區段。曲庫 345 份轉出來全部不合格。
"""

import glob
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qt_editor.models import (GNote, NoteModel, build_slide_index_map,
                              slide_next_note)
from qt_editor.pan_format import DEFAULT_EFFECT_EVENTS, validate

OFFICIAL = sorted(glob.glob(
    r'D:\Nostalgia\PAN-001-2024102200_extracted\PAN-001-2024102200'
    r'\contents\data\sound\music\*\*_03real.xml'))


def note(idx, start, pitch=60, note_type=0, hand=0, lanes=(10, 12), end=None):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 200 if end is None else end
    n.gate = n.end - n.start
    n.pitch = pitch
    n.hand = hand
    n.note_type = note_type
    n.min_key, n.max_key = lanes
    return n


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


class EditorChart:
    """什麼奇怪東西都有的譜：Soft／Staccato、沒音高、自動彈、滑鍵、踏板、強弱。"""

    @staticmethod
    def build():
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        notes = [
            note(0, 0, 60),
            note(1, 500, 62, note_type=1),              # Soft
            note(2, 1000, 64, note_type=3),             # Staccato
            note(3, 1500, None),                        # 沒有音高
            note(4, 2000, 67, hand=2),                  # 自動彈
            note(5, 2500, 69, note_type=2, end=3500),   # 長押
        ]
        # 滑鍵鏈故意放在音符清單的前面（時間在後）
        chain = [note(10 + i, 4000 + i * 250, 72 + i, note_type=4,
                      lanes=(8 + i, 10 + i)) for i in range(3)]
        for i, c in enumerate(chain):
            c.note_index = 100 + i
        for i, c in enumerate(chain):
            c.param1 = chain[i - 1].note_index if i else -1
            c.param2 = chain[i + 1].note_index if i < 2 else -1
        m.notes_tree = chain + notes
        m.rebuild_display_cache()
        m.pedal_spans = [[0.0, 900.0]]
        m.dynamics_add(0, 0, 80, ramp=False)
        return m


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.m = EditorChart.build()
        self.path = tmp('chart.xml')
        self.m.save_xml(self.path)
        self.root = ET.parse(self.path).getroot()

    def test_passes_the_pan_rules(self):
        result = validate(self.path)
        self.assertTrue(result.ok, result.summary())

    def test_every_note_has_a_sub_note_and_a_track(self):
        tracks = {int(t.findtext('index')) for t in self.root.iter('track')}
        self.assertTrue(tracks)
        for n in self.root.iter('note'):
            subs = n.findall('sub_note_data/sub_note')
            self.assertTrue(subs)
            self.assertIn(int(subs[0].findtext('track_index')), tracks)

    def test_editor_only_types_are_written_as_tap(self):
        types = {int(n.findtext('note_type')) for n in self.root.iter('note')}
        self.assertFalse(types & {1, 3})

    def test_the_chart_in_memory_is_not_changed_by_saving(self):
        self.assertIn(3, [n.note_type for n in self.m.notes_tree])
        self.assertTrue(self.m.pedal_spans)

    def test_no_sections_pan_does_not_know(self):
        self.assertEqual([c.tag for c in self.root],
                         ['header', 'note_data', 'event_data', 'beat_data', 'track_info'])

    def test_notes_are_in_time_order(self):
        starts = [int(n.findtext('start_timing_msec')) for n in self.root.iter('note')]
        self.assertEqual(starts, sorted(starts))

    def test_the_slide_chain_survives_reordering(self):
        back = NoteModel()
        back.load_xml(self.path)
        slides = sorted((n for n in back.notes_tree if n.note_type == 4), key=lambda n: n.start)
        index_map = build_slide_index_map(back.notes_tree)
        self.assertIs(slide_next_note(slides[0], back.notes_tree, index_map), slides[1])
        self.assertIs(slide_next_note(slides[1], back.notes_tree, index_map), slides[2])
        self.assertEqual(slides[0].param1, -1)
        self.assertEqual(slides[2].param2, -1)

    def test_events_have_a_real_tempo_and_the_effect_defaults(self):
        events = [(int(e.findtext('start_timing_msec')), int(e.findtext('type')),
                   int(e.findtext('value'))) for e in self.root.iter('event')]
        self.assertIn((0, 0, 12000000), events)
        for ty, value in DEFAULT_EFFECT_EVENTS.items():
            self.assertIn((0, ty, value), events)

    def test_beats_are_one_per_beat_from_zero(self):
        beats = self.root.findall('beat_data/beat')
        self.assertEqual([int(b.findtext('index')) for b in beats], list(range(len(beats))))
        times = [int(b.findtext('start_timing_msec')) for b in beats]
        self.assertEqual(times[1] - times[0], 500)       # 120 BPM 一拍 500ms

    def test_reloading_keeps_every_note(self):
        back = NoteModel()
        back.load_xml(self.path)
        self.assertEqual(len(back.notes_tree), len(self.m.notes_tree))

    def test_format_flags(self):
        self.assertTrue(self.m.pan_xml)
        self.m.save_json(tmp('c.json'))
        self.assertFalse(self.m.pan_xml)
        self.assertFalse(NoteModel.create_new('t', 120.0, 5.0, 4).pan_xml)


class ConversionTests(unittest.TestCase):
    def test_preview_lists_what_will_be_lost(self):
        preview = EditorChart.build().pan_conversion_preview()
        self.assertEqual(preview['Soft 音符換成 Tap'], 1)
        self.assertEqual(preview['Staccato 音符換成 Tap'], 1)
        self.assertEqual(preview['延音踏板區段（PAN 沒有踏板）'], 1)
        self.assertEqual(preview['強弱記號（PAN 沒有）'], 1)

    def test_convert_changes_the_chart_and_can_be_undone(self):
        m = EditorChart.build()
        m.push_history()
        m.convert_to_pan()
        self.assertFalse({1, 3} & {n.note_type for n in m.notes_tree})
        self.assertEqual(m.pedal_spans, [])
        self.assertEqual(m.pan_conversion_preview(), {})
        m.undo()
        self.assertIn(3, [n.note_type for n in m.notes_tree])
        self.assertTrue(m.pedal_spans)

    def test_a_clean_chart_needs_nothing(self):
        m = NoteModel.create_new('t', 120.0, 5.0, 4)
        m.notes_tree = [note(0, 0)]
        self.assertEqual(m.pan_conversion_preview(), {})


class EventTests(unittest.TestCase):
    def test_events_round_trip_in_both_formats(self):
        m = EditorChart.build()
        m.events = [[0, 0, 12000000], [3000, 0, 9000000], [0, 1, 100], [5000, 9, 1]]
        for ext, save, load in (('xml', 'save_xml', 'load_xml'), ('json', 'save_json', 'load_json')):
            path = tmp('e.' + ext)
            getattr(m, save)(path)
            back = NoteModel()
            getattr(back, load)(path)
            self.assertEqual(back.sorted_events(), m.sorted_events(), ext)

    def test_undo_brings_events_back(self):
        m = EditorChart.build()
        m.events = [[0, 0, 12000000]]
        m.push_history()
        m.events = []
        m.undo()
        self.assertEqual(m.events, [[0, 0, 12000000]])

    def test_tempo_events_follow_measure_bpm(self):
        m = NoteModel.create_new('t', 120.0, 20.0, 4)
        m.set_measure_bpm(2, 90.0)
        events = m.tempo_events_from_measures()
        self.assertEqual(events[0], [0, 0, 12000000])
        start = m.get_measure_time_range(2)[0]
        at_start = [v for ms, ty, v in events if ms == start and ty == 0]
        self.assertEqual(len(at_start), 1)
        self.assertAlmostEqual(at_start[0] / 100000.0, 90.0, delta=0.05)   # 小節長度取整到 ms

    def test_missing_tempo_events_are_filled_in_on_export(self):
        m = EditorChart.build()
        m.events = [[0, 1, 110]]                  # 只有音效，沒有速度
        types = [ty for _ms, ty, _v in m.pan_events()]
        self.assertIn(0, types)
        self.assertIn(1, types)


class TrillTests(unittest.TestCase):
    def test_a_trill_keeps_its_strokes_when_loaded(self):
        m = NoteModel.create_new('t', 120.0, 5.0, 4)
        trill = note(0, 0, 60, note_type=64, end=800)
        m.notes_tree = [trill]
        m.save_xml(tmp('x.xml'))                # 先有個合法檔案結構
        root = ET.parse(m.current_file).getroot()
        subs = root.find('note_data/note/sub_note_data')
        for k in range(5):
            se = ET.SubElement(subs, 'sub_note')
            for tag, val, ty in (('start_timing_msec', 100 * k, 's32'),
                                 ('end_timing_msec', 100 * k + 80, 's32'),
                                 ('scale_piano', 40 + k % 2, 'u8'),
                                 ('velocity', 90, 'u8'), ('track_index', 1, 's32')):
                el = ET.SubElement(se, tag)
                el.text, el.attrib['__type'] = str(val), ty
        path = tmp('trill.xml')
        ET.ElementTree(root).write(path, encoding='utf-8')
        back = NoteModel()
        back.load_xml(path)
        self.assertEqual(len(back.notes_tree), 1, '顫音的敲擊不是隱藏音符')
        self.assertEqual(len(back.notes_tree[0].sub_elems), 6)

    def test_a_trill_does_not_host_hidden_notes(self):
        m = NoteModel.create_new('t', 120.0, 5.0, 4)
        trill = note(0, 0, 60, note_type=64, end=800)
        tap = note(1, 20, 50)
        hidden = note(2, 0, 67)
        hidden.hidden = True
        m.notes_tree = [trill, tap, hidden]
        hosts = dict((id(n), h) for n, h in m.resolve_hidden_hosts())
        self.assertIs(hosts[id(hidden)], tap)


class JsonKeepsPanDataTests(unittest.TestCase):
    def test_track_names_and_zones_survive_xml_json_xml(self):
        m = EditorChart.build()
        first = tmp('a.xml')
        m.save_xml(first)
        root = ET.parse(first).getroot()
        for name in root.iter('name'):
            name.text = 'key_cat1'
        zones = ET.SubElement(root, 'velocity_zone_data')
        zone = ET.SubElement(zones, 'velocity_zone')
        for tag, val in (('index', 0), ('start_timing_msec', 100),
                         ('end_timing_msec', 900), ('velocity_type', 1)):
            el = ET.SubElement(zone, tag)
            el.text, el.attrib['__type'] = str(val), 's32'
        ET.ElementTree(root).write(first, encoding='utf-8')

        loaded = NoteModel()
        loaded.load_xml(first)
        as_json = tmp('b.json')
        loaded.save_json(as_json)
        from_json = NoteModel()
        from_json.load_json(as_json)
        last = tmp('c.xml')
        from_json.save_xml(last)
        out = ET.parse(last).getroot()
        self.assertEqual({n.text for n in out.iter('name')}, {'key_cat1'})
        self.assertEqual(out.find('velocity_zone_data/velocity_zone/end_timing_msec').text, '900')
        self.assertTrue(validate(last).ok, validate(last).summary())


@unittest.skipUnless(OFFICIAL, '沒有官方資料')
class OfficialTests(unittest.TestCase):
    def test_official_charts_pass_and_come_back_the_same(self):
        for path in OFFICIAL[:6]:
            self.assertTrue(validate(path).ok, path)
            m = NoteModel()
            m.load_xml(path)
            out = tmp('o.xml')
            m.save_xml(out)
            self.assertTrue(validate(out).ok, validate(out).summary())
            a, b = ET.parse(path).getroot(), ET.parse(out).getroot()

            def sig(r):
                return sorted((n.findtext('start_timing_msec'), n.findtext('scale_piano'),
                               n.findtext('note_type'), n.findtext('hand'),
                               len(n.findall('sub_note_data/sub_note'))) for n in r.iter('note'))
            self.assertEqual(sig(a), sig(b), path)
            self.assertEqual(len(a.findall('event_data/event')), len(b.findall('event_data/event')))
            self.assertEqual(len(a.findall('beat_data/beat')), len(b.findall('beat_data/beat')))


if __name__ == '__main__':
    unittest.main()
