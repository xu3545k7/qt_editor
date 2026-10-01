"""XML 對齊 PAN、額外資訊存 JSON：繞一圈回來要一模一樣。

工作流程是「JSON 是原始檔、XML 是給本家（PAN）的相容輸出」。本家的 XML 裡有些
東西 JSON 沒有對應欄位（header 的音域與 first_bpm、音符原本的編號、hand=2 的周邊
座標、劇情譜的佔位座標、sub_note 自己的音高與力度）。那些不能重算——重算就等於
把原檔的資訊改掉——所以存進 JSON 帶著走，重建 XML 時原樣寫回。

實測 682 份可讀官譜（另外 103 份是 2 位元組的空佔位檔），680 份「XML→JSON→XML」
逐欄一致；剩 2 份是**官檔自己有重複的音符編號**，沿用會有歧義，那種情況重編號。
掃描腳本見交接：scratchpad/roundtrip_pan.py。
"""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PyQt5.QtWidgets import QApplication

from qt_editor.models import NoteModel

_app = QApplication.instance() or QApplication([])


def note_xml(index, start, end, scale, lo, hi, note_type=0, hand=0,
             sub_scale=None, sub_velocity=80, sub_track=1):
    """一顆音符。`sub_scale` 不給就和音符同音高。"""
    return f"""  <note>
    <index __type="s32">{index}</index>
    <start_timing_msec __type="s32">{start}</start_timing_msec>
    <end_timing_msec __type="s32">{end}</end_timing_msec>
    <gate_time_msec __type="s32">{end - start}</gate_time_msec>
    <scale_piano __type="u8">{scale}</scale_piano>
    <min_key_index __type="s32">{lo}</min_key_index>
    <max_key_index __type="s32">{hi}</max_key_index>
    <note_type __type="s32">{note_type}</note_type>
    <hand __type="s32">{hand}</hand>
    <key_kind __type="s32">0</key_kind>
    <param1 __type="s32">0</param1>
    <param2 __type="s32">0</param2>
    <param3 __type="s32">0</param3>
    <sub_note_data>
      <sub_note>
        <start_timing_msec __type="s32">{start}</start_timing_msec>
        <end_timing_msec __type="s32">{end}</end_timing_msec>
        <scale_piano __type="u8">{scale if sub_scale is None else sub_scale}</scale_piano>
        <velocity __type="u8">{sub_velocity}</velocity>
        <track_index __type="s32">{sub_track}</track_index>
      </sub_note>
    </sub_note_data>
  </note>
"""


#: 刻意塞進每一種「JSON 沒有對應欄位」的東西
CHART = """<?xml version="1.0" encoding="utf-8"?>
<music_score>
  <header>
    <max_scale __type="s32">75</max_scale>
    <min_scale __type="s32">20</min_scale>
    <file_version __type="s16">1</file_version>
    <first_bpm __type="s64">12000000</first_bpm>
    <music_finish_time_msec __type="s32">12000</music_finish_time_msec>
  </header>
  <note_data>
""" + ''.join([
    # 編號不從 0 開始、也不連號（官譜常態）
    note_xml(901, 1000, 1200, 40, 5, 7, sub_scale=36),
    note_xml(903, 2000, 2200, 45, 10, 12, sub_scale=41),
    # hand=2 的周邊座標（超出 1..28）
    note_xml(905, 3000, 3200, 50, 30, 34, hand=2, sub_scale=50),
    # 劇情譜的佔位座標
    note_xml(907, 4000, 4200, 55, -2147483648, -2147483646, sub_scale=55),
]) + """  </note_data>
  <event_data>
    <!-- PAN 的速度事件是 type 0、值是 BPM×100000 -->
    <event>
      <index __type="s32">0</index>
      <start_timing_msec __type="s32">0</start_timing_msec>
      <type __type="s32">0</type>
      <value __type="s32">12000000</value>
    </event>
  </event_data>
  <beat_data>
""" + ''.join(f"""    <beat>
      <index __type="s32">{i}</index>
      <start_timing_msec __type="s32">{i * 500}</start_timing_msec>
    </beat>
""" for i in range(25)) + """  </beat_data>
  <track_info>
    <track>
      <index __type="s32">1</index>
      <name __type="str">key_apiano1</name>
    </track>
    <track>
      <index __type="s32">2</index>
      <name __type="str">key_cat1</name>
    </track>
  </track_info>
</music_score>
"""


def rows(root, path, fields):
    return [tuple((el.findtext(f) or '') for f in fields) for el in root.findall(path)]


NOTE_FIELDS = ('index', 'start_timing_msec', 'end_timing_msec', 'gate_time_msec',
               'scale_piano', 'min_key_index', 'max_key_index', 'note_type', 'hand',
               'key_kind', 'param1', 'param2', 'param3')
SUB_FIELDS = ('start_timing_msec', 'end_timing_msec', 'scale_piano',
              'velocity', 'track_index')


class PanRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.xml = os.path.join(self.tmp, 'src.xml')
        with open(self.xml, 'w', encoding='utf-8') as fh:
            fh.write(CHART)
        self.original = ET.fromstring(CHART)

    def rebuilt(self):
        """XML → JSON → 讀回 → 重建 XML。"""
        m = NoteModel()
        m.load_xml(self.xml)
        jpath = os.path.join(self.tmp, 'mid.json')
        m.save_json(jpath)
        back = NoteModel()
        back.load_json(jpath)
        back.rebuild_display_cache()
        return back.build_pan_xml()

    def test_header_extras_survive(self):
        """音域和 first_bpm 是原檔帶的，不能照音符重算。"""
        got = self.rebuilt().find('header')
        want = self.original.find('header')
        for tag in ('max_scale', 'min_scale', 'first_bpm'):
            self.assertEqual(got.findtext(tag), want.findtext(tag), tag)

    def test_note_indexes_survive(self):
        """官譜的編號不從 0 開始也不連號，繞一圈要一樣。"""
        self.assertEqual([r[0] for r in rows(self.rebuilt(), 'note_data/note', NOTE_FIELDS)],
                         ['901', '903', '905', '907'])

    def test_every_note_field_survives(self):
        """含 hand=2 的周邊座標與劇情佔位座標。"""
        self.assertEqual(rows(self.rebuilt(), 'note_data/note', NOTE_FIELDS),
                         rows(self.original, 'note_data/note', NOTE_FIELDS))

    def test_sub_notes_survive(self):
        """sub_note 的音高和音符本身不同（官譜常態），只有 JSON 存得下。"""
        self.assertEqual(rows(self.rebuilt(), './/sub_note', SUB_FIELDS),
                         rows(self.original, './/sub_note', SUB_FIELDS))

    def test_tracks_and_beats_and_events_survive(self):
        got, want = self.rebuilt(), self.original
        self.assertEqual(rows(got, 'track_info/track', ('index', 'name')),
                         rows(want, 'track_info/track', ('index', 'name')))
        self.assertEqual(rows(got, 'beat_data/beat', ('index', 'start_timing_msec')),
                         rows(want, 'beat_data/beat', ('index', 'start_timing_msec')))
        self.assertEqual(rows(got, 'event_data/event', ('start_timing_msec', 'type', 'value')),
                         rows(want, 'event_data/event', ('start_timing_msec', 'type', 'value')))

    def test_xml_has_no_extra_sections(self):
        """XML 要和本家一致：不多寫欄位（額外資訊一律存 JSON）。"""
        self.assertEqual([c.tag for c in self.rebuilt()],
                         ['header', 'note_data', 'event_data', 'beat_data', 'track_info'])

    def test_editing_a_lane_wins_over_the_stored_original(self):
        """原值只在「沒被編輯過」時沿用，改過就照改過的寫。"""
        m = NoteModel()
        m.load_xml(self.xml)
        target = next(n for n in m.notes_tree if n.note_index == 905)
        target.min_key, target.max_key = 3, 4
        jpath = os.path.join(self.tmp, 'edited.json')
        m.save_json(jpath)
        back = NoteModel()
        back.load_json(jpath)
        back.rebuild_display_cache()
        root = back.build_pan_xml()
        note = next(n for n in root.findall('note_data/note')
                    if n.findtext('index') == '905')
        # 寫出去的基準是原檔的（這份譜有周邊／佔位座標，判定為 0 起算），
        # 不是固定 +1——那正是以前讓 362 份官譜鍵道右移一格的原因。
        base = back._get_xml_lane_index_base()
        self.assertEqual((note.findtext('min_key_index'), note.findtext('max_key_index')),
                         (str(3 + base), str(4 + base)), '編輯過的鍵道要照新值寫')


if __name__ == '__main__':
    unittest.main()
