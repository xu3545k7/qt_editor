import json
"""隱藏音符的存檔往返。

隱藏音符不會單獨寫成 `<note>`——它的音是併回寄主的 `sub_note_data`。這條路上
原本有四個各自獨立的洞，任何一個都會讓音整顆消失：

1. `apply_back` 不把 `sub_elems` 寫回 XML 元素。從檔案載入的音符 `elem` 已經
   存在，存檔時是直接把舊 elem 掛回去，併進來的 sub 一個字都沒寫出去。
2. 寄主自己沒有 sub 時（編輯器新增的音符），併完只剩隱藏音那一顆，載入時
   `len(subs) < 2` 直接跳過，於是寄主的音變成隱藏音的音、隱藏音符本身沒了。
3. 找不到寄主的隱藏音符被直接略過——不寫成 note、也不併給任何人 = 消失。
4. 從 sub 拆回隱藏音符時抄寄主的時間，而官方的 sub 各自帶 start/end，
   往返一次頭尾就被改掉（12 首官方譜有 9 首對不起來）。
"""

import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel


def make(idx, start, pitch, hidden=False, centre=6, dur=300):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = 0
    n.velocity = 90
    n.min_key, n.max_key = centre - 1, centre + 1
    n.note_type = 0
    n.hidden = hidden
    return n


class HiddenNoteRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def save_load(self, notes, ext='.xml'):
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = list(notes)
        m.rebuild_display_cache()
        path = os.path.join(self.dir, 'c' + ext)
        (m.save_xml if ext == '.xml' else m.save_json)(path)
        back = NoteModel()
        (back.load_xml if ext == '.xml' else back.load_json)(path)
        return back

    def summary(self, m):
        return sorted((n.pitch, bool(getattr(n, 'hidden', False)))
                      for n in m.notes_tree)

    def test_a_hidden_note_survives_xml(self):
        back = self.save_load([make(0, 1000, 60), make(1, 1000, 64, hidden=True)])
        self.assertEqual(self.summary(back), [(60, False), (64, True)])

    def test_a_hidden_note_survives_json(self):
        back = self.save_load([make(0, 1000, 60), make(1, 1000, 64, hidden=True)],
                              ext='.json')
        self.assertEqual(self.summary(back), [(60, False), (64, True)])

    def test_two_hidden_notes_on_one_host(self):
        back = self.save_load([make(0, 500, 60),
                               make(1, 500, 64, hidden=True),
                               make(2, 500, 67, hidden=True)])
        self.assertEqual(self.summary(back),
                         [(60, False), (64, True), (67, True)])

    def test_a_hostless_hidden_note_is_kept_visible_not_lost(self):
        # 沒有寄主可掛就不能隱藏，但音一定要留著
        back = self.save_load([make(0, 0, 60), make(1, 9000, 72, hidden=True)])
        self.assertEqual(sorted(n.pitch for n in back.notes_tree), [60, 72])

    def test_the_host_keeps_its_own_pitch(self):
        # 寄主自己沒有 sub 時要現做一個，否則寄主的音會被隱藏音的音頂掉
        back = self.save_load([make(0, 1000, 60), make(1, 1000, 64, hidden=True)])
        visible = [n for n in back.notes_tree if not getattr(n, 'hidden', False)]
        self.assertEqual([n.pitch for n in visible], [60])

    def test_a_hidden_note_added_to_a_loaded_chart_survives(self):
        """最常見的情境：開啟既有檔案、標一顆隱藏、再存檔。

        這時寄主的 `elem` 來自檔案，走的是 `apply_back` 那條路。
        """
        first = os.path.join(self.dir, 'a.xml')
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = [make(0, 1000, 60)]
        m.rebuild_display_cache()
        m.save_xml(first)

        m2 = NoteModel()
        m2.load_xml(first)
        self.assertIsNotNone(m2.notes_tree[0].elem, '這個測試要走 apply_back 那條路')
        m2.notes_tree.append(make(1, 1000, 64, hidden=True))
        m2.rebuild_display_cache()
        second = os.path.join(self.dir, 'b.xml')
        m2.save_xml(second)

        back = NoteModel()
        back.load_xml(second)
        self.assertEqual(self.summary(back), [(60, False), (64, True)])

    def test_hidden_note_timing_is_its_own_not_the_hosts(self):
        back = self.save_load([make(0, 1000, 60, dur=400),
                               make(1, 1000, 64, hidden=True, dur=400)])
        hidden = [n for n in back.notes_tree if getattr(n, 'hidden', False)]
        self.assertEqual(len(hidden), 1)
        self.assertEqual((hidden[0].start, hidden[0].end), (1000, 1400))

    def test_deleting_the_hidden_note_removes_its_sound(self):
        first = os.path.join(self.dir, 'a.xml')
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = [make(0, 1000, 60), make(1, 1000, 64, hidden=True)]
        m.rebuild_display_cache()
        m.save_xml(first)

        m2 = NoteModel()
        m2.load_xml(first)
        m2.notes_tree = [n for n in m2.notes_tree
                         if not getattr(n, 'hidden', False)]
        m2.rebuild_display_cache()
        second = os.path.join(self.dir, 'b.xml')
        m2.save_xml(second)

        back = NoteModel()
        back.load_xml(second)
        self.assertEqual(self.summary(back), [(60, False)])

    def test_velocity_is_preserved(self):
        a = make(0, 1000, 60)
        b = make(1, 1000, 64, hidden=True)
        b.velocity = 42
        back = self.save_load([a, b])
        hidden = [n for n in back.notes_tree if getattr(n, 'hidden', False)]
        self.assertEqual(hidden[0].velocity, 42)

    def test_a_chart_without_hidden_notes_is_unaffected(self):
        back = self.save_load([make(0, 0, 60), make(1, 500, 64)])
        self.assertEqual(self.summary(back), [(60, False), (64, False)])


class HiddenNoteJsonHostTests(unittest.TestCase):
    """XML -> JSON -> XML 走一圈，隱藏音符不能掉。

    官方的 sub_note 自帶 start/end，和寄主差超過 120ms 是常態。寄主是誰只記在
    記憶體的 _sub_host 裡，JSON 以前沒帶這個資訊 —— 於是重新存成 XML 時
    resolve_hidden_hosts 的就近搜尋找不到寄主，那些音符被當成孤兒取消隱藏。
    實測 chopin_nocturn2 的 normal 譜 321 顆會掉成 240 顆。匯出給遊戲走的正是
    JSON，所以這條路徑一定要通。
    """

    OFFICIAL = os.path.join(r'D:\Nostalgia\PAN-001-2024102200_extracted\PAN-001-2024102200',
        'contents', 'data', 'sound', 'music',
        'm_c0002_chopin_nocturn2', 'm_c0002_chopin_nocturn2_00normal.xml')

    def _hidden(self, model):
        return sum(1 for n in model.notes_tree if getattr(n, 'hidden', False))

    def test_xml_json_xml_keeps_every_hidden_note(self):
        if not os.path.exists(self.OFFICIAL):
            self.skipTest('官方樣本不在')
        first = NoteModel(); first.load_xml(self.OFFICIAL)
        expected = self._hidden(first)
        self.assertGreater(expected, 100)
        tmp = tempfile.mkdtemp()
        as_json = os.path.join(tmp, 'roundtrip.json')
        first.save_json(as_json)
        second = NoteModel(); second.load_json(as_json)
        self.assertEqual(self._hidden(second), expected)
        back = os.path.join(tmp, 'roundtrip.xml')
        second.save_xml(back)
        third = NoteModel(); third.load_xml(back)
        self.assertEqual(self._hidden(third), expected)

    def test_json_records_the_host_of_each_hidden_note(self):
        if not os.path.exists(self.OFFICIAL):
            self.skipTest('官方樣本不在')
        model = NoteModel(); model.load_xml(self.OFFICIAL)
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, 'hosts.json')
        model.save_json(path)
        with open(path, encoding='utf-8') as fh:
            notes = json.load(fh)['notes']
        hidden = [d for d in notes if d.get('hidden')]
        self.assertTrue(hidden)
        self.assertTrue(all('hostIndex' in d for d in hidden),
                        '有隱藏音符沒有記下寄主')


if __name__ == '__main__':
    unittest.main()


class EditingAHiddenNoteTests(unittest.TestCase):
    """改隱藏音符之後要存得下來。

    使用者報的：「對隱藏音符的編輯好像保存不了」。

    `_merge_hidden_into_hosts` 以前直接沿用**載入時那個 sub 元素**
    （`se = subs[0] if subs else ...`），所以在編輯器裡改了時間或音高，存成
    XML 之後全部回到原值。實測官方 menuetto 的 normal 譜：把一顆隱藏音符往後
    移 137ms、拉長 192ms、音高 +2，存檔再開回來拿到的是一字未改的原值。

    JSON 沒這個問題——它把隱藏音符平鋪成一般的 note 欄位（含 `hostIndex`）。
    這也是為什麼遊戲端看不出異常：匯出給遊戲走的是 JSON。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def loaded_chart(self):
        """存一份再讀回來，讓隱藏音符持有來自檔案的 sub 元素。"""
        path = os.path.join(self.dir, 'a.xml')
        m = NoteModel.create_new('t', 120.0, 30.0, 4)
        m.notes_tree = [make(0, 1000, 60), make(1, 1000, 64, hidden=True)]
        m.rebuild_display_cache()
        m.save_xml(path)
        back = NoteModel()
        back.load_xml(path)
        return back

    def hidden(self, m):
        out = [n for n in m.notes_tree if getattr(n, 'hidden', False)]
        self.assertEqual(len(out), 1, '這個測試需要剛好一顆隱藏音符')
        return out[0]

    def resave(self, m, name='b.xml'):
        path = os.path.join(self.dir, name)
        m.save_xml(path)
        back = NoteModel()
        back.load_xml(path)
        return back

    def test_the_sub_element_really_comes_from_the_file(self):
        """前提：沒有 sub 元素的話走的是另一條（本來就正確的）路。"""
        note = self.hidden(self.loaded_chart())
        self.assertTrue(getattr(note, 'sub_elems', None),
                        '這個測試要測「沿用檔案裡的 sub」那條路')

    def test_moving_it_survives_the_save(self):
        m = self.loaded_chart()
        note = self.hidden(m)
        note.start += 137
        note.end += 137
        note.gate = note.end - note.start
        want = (note.start, note.end)
        self.assertEqual((self.hidden(self.resave(m)).start,
                          self.hidden(self.resave(m)).end), want)

    def test_stretching_it_survives_the_save(self):
        m = self.loaded_chart()
        note = self.hidden(m)
        note.end += 192
        note.gate = note.end - note.start
        self.assertEqual(self.hidden(self.resave(m)).end, note.end)

    def test_changing_its_pitch_survives_the_save(self):
        m = self.loaded_chart()
        note = self.hidden(m)
        note.pitch += 2
        self.assertEqual(self.hidden(self.resave(m)).pitch, note.pitch)

    def test_all_three_at_once(self):
        m = self.loaded_chart()
        note = self.hidden(m)
        note.start += 137
        note.end += 329
        note.gate = note.end - note.start
        note.pitch += 2
        want = (note.start, note.end, note.pitch)
        got = self.hidden(self.resave(m))
        self.assertEqual((got.start, got.end, got.pitch), want)

    def test_json_was_already_fine(self):
        """JSON 那條路本來就對，不要在修 XML 的時候把它弄壞。"""
        m = self.loaded_chart()
        note = self.hidden(m)
        note.start += 137
        note.end += 329
        note.pitch += 2
        want = (note.start, note.end, note.pitch)
        path = os.path.join(self.dir, 'c.json')
        m.save_json(path)
        back = NoteModel()
        back.load_json(path)
        got = self.hidden(back)
        self.assertEqual((got.start, got.end, got.pitch), want)

    def test_not_editing_it_changes_nothing(self):
        """沒動過的隱藏音符存檔要原封不動。

        官方檔的往返是逐欄位比對的（實測 192 份有隱藏音符的官方譜要完全相同），
        所以寫回去只在值真的不一樣時才動那個元素。
        """
        m = self.loaded_chart()
        before = [(n.start, n.end, n.pitch, bool(getattr(n, 'hidden', False)))
                  for n in sorted(m.notes_tree, key=lambda x: (x.start, x.pitch))]
        back = self.resave(m, 'd.xml')
        after = [(n.start, n.end, n.pitch, bool(getattr(n, 'hidden', False)))
                 for n in sorted(back.notes_tree, key=lambda x: (x.start, x.pitch))]
        self.assertEqual(after, before)

    def test_saving_twice_does_not_duplicate(self):
        """連續存檔不該讓隱藏音符愈存愈多（`_unmerge_hidden_from_hosts` 的職責）。"""
        m = self.loaded_chart()
        note = self.hidden(m)
        note.start += 50
        note.end += 50
        first = self.resave(m, 'e1.xml')
        second = self.resave(m, 'e2.xml')
        self.assertEqual(len(first.notes_tree), len(second.notes_tree))
        self.assertEqual(self.hidden(second).start, note.start)

    def test_the_lane_only_survives_in_json(self):
        """隱藏音符的鍵道只有 JSON 存得住，XML 存不住——而且那是刻意的。

        官方的 `sub_note` 沒有鍵道欄位，XML 是 PAN 相容輸出，`save_xml` 會把
        編輯器自己的 `src_*` 欄位全部濾掉。試過在 sub 上寫 src_min_key/
        src_max_key：元素裡確實有，存出來的檔案是 0 處。
        所以隱藏音符載入 XML 時鍵道一律沿用寄主的。

        這個測試釘住現狀，免得有人以為是漏洞又去 XML 那邊補一次。
        """
        m = self.loaded_chart()
        note = self.hidden(m)
        host_lane = (note.min_key, note.max_key)
        note.min_key, note.max_key = host_lane[0] - 4, host_lane[1] - 4

        back_xml = self.resave(m, 'lane.xml')
        self.assertEqual((self.hidden(back_xml).min_key,
                          self.hidden(back_xml).max_key), host_lane,
                         'XML 應該沿用寄主的鍵道')

        path = os.path.join(self.dir, 'lane.json')
        m.save_json(path)
        back_json = NoteModel()
        back_json.load_json(path)
        self.assertEqual((self.hidden(back_json).min_key,
                          self.hidden(back_json).max_key),
                         (host_lane[0] - 4, host_lane[1] - 4),
                         'JSON 要存得住鍵道')
