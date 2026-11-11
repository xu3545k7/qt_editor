# -*- coding: utf-8 -*-
"""隱藏音符的寄主怎麼挑，以及「沒有寄主」是合法狀態。

使用者指定的規則：

* 「隱藏音符固定綁時間短距離內」
* 「比所選擇附近隱藏音還早但在符合之中最晚的同手音符」

也就是**同一隻手、比它早、而且在 `HIDDEN_HOST_WINDOW_MS` 之內的可見音符裡
最晚的那一顆**。為什麼一定要「比它早」：遊戲端的 sub_note 是**寄主被打到的
那一刻才排程**的，之後各自照自己的 `start_timing_msec` 出聲。寄主在前面，
keysound 才等得到；寄主在後面就只能丟給自動播放佇列。

而「找不到寄主」以前會在存檔時被 `_merge_hidden_into_hosts` 偷偷
`note.hidden = False`，而 `_unmerge_hidden_from_hosts` 只還原 `sub_elems`、
不還原 `hidden` —— 所以存一次 XML，使用者放的那顆「無論如何都會響的 key 音」
就永久變成一顆要打的音符。使用者：「請把無主音保護關掉，它可以存在並保存」。
"""

import json
import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel


def make(idx, start, pitch, hidden=False, hand=0, centre=6, dur=300):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + dur, dur
    n.pitch = pitch
    n.hand = hand
    n.velocity = 90
    n.min_key, n.max_key = centre - 1, centre + 1
    n.note_type = 0
    n.hidden = hidden
    return n


def chart(notes):
    m = NoteModel.create_new('t', 120.0, 30.0, 4)
    m.notes_tree = list(notes)
    m.rebuild_display_cache()
    return m


def host_of(m, hidden, **kw):
    return dict((id(n), h) for n, h in m.resolve_hidden_hosts(**kw))[id(hidden)]


class HostIsTheNoteBeforeItTests(unittest.TestCase):
    def test_the_latest_one_before_it_wins(self):
        early, late, after = make(0, 300, 60), make(1, 950, 62), make(2, 1200, 64)
        ghost = make(3, 1000, 67, hidden=True)
        self.assertIs(host_of(chart([early, late, after, ghost]), ghost), late)

    def test_the_same_instant_counts_as_earlier(self):
        """和絃音折進同一個按鍵是最常見的用法，那些寄主和它同刻。"""
        host = make(0, 1000, 60)
        ghost = make(1, 1000, 64, hidden=True)
        self.assertIs(host_of(chart([host, ghost]), ghost), host)

    def test_a_later_note_is_never_the_host(self):
        after = make(0, 1200, 60)
        ghost = make(1, 1000, 64, hidden=True)
        self.assertIsNone(host_of(chart([after, ghost]), ghost),
                          '往後找到的寄主等不到，不算寄主')

    def test_the_other_hand_is_never_the_host(self):
        other = make(0, 900, 60, hand=1)
        ghost = make(1, 1000, 64, hidden=True, hand=0)
        self.assertIsNone(host_of(chart([other, ghost]), ghost))

    def test_too_far_back_does_not_count(self):
        window = NoteModel.HIDDEN_HOST_WINDOW_MS
        far = make(0, 1000 - window - 1, 60)
        near = make(1, 1000 - window, 62)
        ghost = make(2, 1000, 67, hidden=True)
        self.assertIsNone(host_of(chart([far, ghost]), ghost),
                          '超過時間窗就不算')
        self.assertIsNotNone(host_of(chart([near, ghost]), ghost),
                             '剛好在窗邊上要算')

    def test_a_tie_goes_to_the_nearest_pitch(self):
        low, high = make(0, 1000, 48), make(1, 1000, 66)
        ghost = make(2, 1000, 67, hidden=True)
        self.assertIs(host_of(chart([low, high, ghost]), ghost), high)

    def test_the_recorded_host_still_wins(self):
        """官方的 sub_note 自帶時間，離寄主很遠、甚至在寄主之前都是常態。

        所以從 XML 拆出來時記下的 `_sub_host` 永遠優先，不然 chopin_nocturn2
        的 normal 譜 321 顆隱藏音符會掉成 240 顆。
        """
        host = make(0, 5000, 60)
        ghost = make(1, 1000, 67, hidden=True)
        ghost._sub_host = host
        self.assertIs(host_of(chart([host, ghost]), ghost), host)

    def test_a_trill_is_still_not_a_host(self):
        """顫音的 sub_note 是每一下敲擊，併進去的音會變成多出來的敲擊。"""
        trill = make(0, 900, 60)
        trill.note_type = 64
        ghost = make(1, 1000, 67, hidden=True)
        self.assertIsNone(host_of(chart([trill, ghost]), ghost))


class AHostlessHiddenNoteIsLegalTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def orphan_chart(self):
        """一顆隱藏音符，前面沒有任何同手音符可以掛。"""
        m = chart([make(0, 1200, 60), make(1, 1000, 67, hidden=True)])
        ghost = m.notes_tree[1]
        self.assertIsNone(host_of(m, ghost),
                          '這些測試需要一顆找不到寄主的隱藏音符')
        return m, ghost

    def test_saving_xml_does_not_unhide_it(self):
        m, ghost = self.orphan_chart()
        m.save_xml(os.path.join(self.dir, 'orphan.xml'))
        self.assertTrue(ghost.hidden, '存一次 XML 就把它取消隱藏了')

    def test_the_xml_still_carries_the_sound(self):
        """寫 XML 時走退路：XML 沒有「獨立的隱藏音符」，掛不上就整顆消失。"""
        path = os.path.join(self.dir, 'carried.xml')
        m, _ghost = self.orphan_chart()
        m.save_xml(path)
        back = NoteModel()
        back.load_xml(path)
        self.assertEqual(
            sum(1 for n in back.notes_tree if getattr(n, 'hidden', False)), 1)

    def test_json_keeps_it_hidden_with_no_host_recorded(self):
        path = os.path.join(self.dir, 'orphan.json')
        m, _ghost = self.orphan_chart()
        m.save_json(path)
        with open(path, encoding='utf-8') as fh:
            buried = [d for d in json.load(fh)['notes'] if d.get('hidden')]
        self.assertEqual(len(buried), 1)
        self.assertNotIn('hostIndex', buried[0],
                         '沒有寄主就不要記一個假的 —— 遊戲端有自己的退路')
        back = NoteModel()
        back.load_json(path)
        self.assertEqual(
            sum(1 for n in back.notes_tree if getattr(n, 'hidden', False)), 1)

    def test_a_chart_of_nothing_but_hidden_notes_keeps_them_hidden(self):
        """連退路都沒有（一顆可見音符都沒有）時也不准改譜。"""
        ghost = make(0, 1000, 67, hidden=True)
        m = chart([ghost])
        m._merge_hidden_into_hosts()
        try:
            self.assertTrue(ghost.hidden)
        finally:
            m._unmerge_hidden_from_hosts()


if __name__ == '__main__':
    unittest.main()
