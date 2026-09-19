"""轉成官方格式（PAN XML／Hiraeth ZIP）時長押長度預設縮成 80%，JSON 不變。

JSON／MIDI 的長度是聲音的長度，官方譜的長押比較短。只縮「長度還不是官方長度」
的譜：從官方 XML 讀進來的不縮，否則開 XML 再存回去每存一次短 20%。
"""

import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qt_editor.models import GNote, NoteModel
from qt_editor.settings import settings


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


class OfficialHoldLengthTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = settings._data.get('official_hold_length_pct')
        settings._data['official_hold_length_pct'] = 80

    def tearDown(self):
        settings._data['official_hold_length_pct'] = self._saved if self._saved is not None else 80
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_holds_are_80_percent_in_xml(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        self.assertEqual(durations(self.path('a.xml')),
                         [(2, 800), (0, 100), (4, 1000), (10, 800)])

    def test_the_chart_in_memory_and_json_are_untouched(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        self.assertEqual(m.notes_tree[0].end, 1000)
        m.save_json(self.path('a.json'))
        with open(self.path('a.json'), encoding='utf-8') as fh:
            notes = json.load(fh)['notes']
        self.assertEqual(notes[0]['endTime'] - notes[0]['startTime'], 1000)

    def test_saving_xml_again_does_not_shrink_twice(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        m.save_xml(self.path('a.xml'))                  # 同一份（記憶體還是 JSON 長度）
        self.assertEqual(durations(self.path('a.xml'))[0], (2, 800))
        back = NoteModel()
        back.load_xml(self.path('a.xml'))               # 讀進來的是官方長度
        back.save_xml(self.path('b.xml'))
        self.assertEqual(durations(self.path('b.xml'))[0], (2, 800))

    def test_xml_to_json_to_xml_does_not_shrink_twice(self):
        m = chart()
        m.save_xml(self.path('a.xml'))
        back = NoteModel()
        back.load_xml(self.path('a.xml'))
        back.save_json(self.path('b.json'))
        again = NoteModel()
        again.load_json(self.path('b.json'))
        again.save_xml(self.path('c.xml'))
        self.assertEqual(durations(self.path('c.xml'))[0], (2, 800))

    def test_the_setting_controls_the_ratio(self):
        settings._data['official_hold_length_pct'] = 100
        m = chart()
        m.save_xml(self.path('a.xml'))
        self.assertEqual(durations(self.path('a.xml'))[0], (2, 1000))

    def test_hiraeth_zip_uses_it_too(self):
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
        self.assertEqual(durations(os.path.join(self.dir, 'expert.xml'))[0], (2, 800))


if __name__ == '__main__':
    unittest.main()
