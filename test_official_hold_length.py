"""長押的長度照畫面上的樣子寫出去：製譜器是所見即所得。

以前轉成官方格式（PAN XML／Hiraeth ZIP）時會把「長度還不是官方長度」的譜
乘上 `official_hold_length_pct`（預設 80%），理由是 MIDI 的長度是聲音長度、
官方譜的長押比較短。代價是製譜器不再所見即所得：使用者把長條尾端對齊小節線，
進遊戲卻短 20%（一小節 2000ms 的長條差 400ms），而且畫面上每條長押都比遊戲裡
長 25%。現在不縮了，所以這裡測的是「長度一個 ms 都不能動」。
"""

import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qt_editor.models import GNote, NoteModel


def chart():
    m = NoteModel.create_new('t', 120.0, 10.0, 4)
    notes = []
    for i, (start, end, nt) in enumerate(((0, 1000, 2), (2000, 2100, 0), (3000, 4000, 4),
                                          (5000, 6000, 10))):
        n = GNote(None, i)
        n.start, n.end, n.gate = start, end, end - start
        n.pitch, n.hand, n.note_type = 60 + i, 0, nt
        n.min_key, n.max_key = 10, 12
        notes.append(n)
    m.notes_tree = notes
    m.rebuild_display_cache()
    return m


def durations(path):
    root = ET.parse(path).getroot()
    return [(int(n.findtext('note_type')),
             int(n.findtext('end_timing_msec')) - int(n.findtext('start_timing_msec')))
            for n in root.iter('note')]


def ends(path):
    root = ET.parse(path).getroot()
    return [int(n.findtext('end_timing_msec')) for n in root.iter('note')]


class HoldLengthIsWhatYouSeeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_xml_keeps_the_lengths_on_screen(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        self.assertEqual(durations(self.path('a.xml')),
                         [(2, 1000), (0, 100), (4, 1000), (10, 1000)])

    def test_a_tail_on_a_barline_stays_on_the_barline(self):
        """使用者回報的情況：對齊小節線的長條，進遊戲卻沒對齊。"""
        m = NoteModel.create_new('t', 120.0, 20.0, 4)       # 一小節 2000ms
        n = GNote(None, 0)
        n.start, n.end, n.gate = 2000, 4000, 2000
        n.pitch, n.hand, n.note_type = 60, 0, 2
        n.min_key, n.max_key = 10, 12
        m.notes_tree = [n]
        m.rebuild_display_cache()
        bar_start, bar_end = m.get_measure_time_range(1)
        self.assertEqual((bar_start, bar_end), (2000, 4000))
        m.save_xml(self.path('a.xml'))
        self.assertEqual(ends(self.path('a.xml')), [bar_end])

    def test_the_chart_in_memory_and_json_are_untouched(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        self.assertEqual(m.notes_tree[0].end, 1000)
        m.save_json(self.path('a.json'))
        with open(self.path('a.json'), encoding='utf-8') as fh:
            notes = json.load(fh)['notes']
        self.assertEqual(notes[0]['endTime'] - notes[0]['startTime'], 1000)

    def test_round_trips_never_change_a_length(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        first = durations(self.path('a.xml'))

        m.save_xml(self.path('a.xml'))                  # 同一份存兩次
        self.assertEqual(durations(self.path('a.xml')), first)

        back = NoteModel()                              # XML → XML
        back.load_xml(self.path('a.xml'))
        back.save_xml(self.path('b.xml'))
        self.assertEqual(durations(self.path('b.xml')), first)

        back.save_json(self.path('b.json'))             # XML → JSON → XML
        again = NoteModel()
        again.load_json(self.path('b.json'))
        again.save_xml(self.path('c.xml'))
        self.assertEqual(durations(self.path('c.xml')), first)

    def test_no_stale_setting_can_shrink_holds_again(self):
        """舊的 settings.json 裡還留著 80 的話，也不能再影響輸出。"""
        from qt_editor.settings import settings

        settings._data['official_hold_length_pct'] = 50
        try:
            m = chart()
            m.save_xml(self.path('a.xml'))
            self.assertEqual(durations(self.path('a.xml'))[0], (2, 1000))
        finally:
            settings._data.pop('official_hold_length_pct', None)

    def test_hiraeth_zip_keeps_them_too(self):
        import zipfile

        from qt_editor import hiraeth_export as H
        m = chart()
        m.notes_tree = m.notes_tree[:2]                  # 長押 + tap
        m.rebuild_display_cache()
        m.save_json(self.path('song.json'))
        loaded = NoteModel()
        loaded.load_json(self.path('song.json'))
        plan = H.PackagePlan('nostalgia-clone.test-hold', 'Hold', '', [
            H.ChartSource('expert', 'expert', 10, model=loaded)], render_piano_layer=False)
        result = H.build_package(plan, self.path('out'),
                                 renderer=lambda _m: (b'\0' * 4 * 44100 * 12, 44100))
        self.assertEqual(result.error, '')
        with zipfile.ZipFile(result.zip_path) as archive:
            archive.extract('expert.xml', self.dir)
        self.assertEqual(durations(os.path.join(self.dir, 'expert.xml'))[0], (2, 1000))


if __name__ == '__main__':
    unittest.main()
