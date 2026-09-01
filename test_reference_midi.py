import unittest

from qt_editor.models import GNote, NoteModel


def onote(start, pitch, lane, idx=0, width=1):
    n = GNote(None, idx)
    n.start = start
    n.end = start + 100
    n.gate = 100
    n.pitch = pitch
    n.min_key = lane
    n.max_key = lane + width - 1
    return n


def mnote(start, end, pitch, hand=0, track=0):
    return {'start_timing_msec': start, 'end_timing_msec': end,
            'scale_piano': pitch, 'hand': hand, 'track': track}


class RebuildFromReferenceMidiTests(unittest.TestCase):
    def test_pitch_and_time_come_from_midi(self):
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 5, 0)]
        midi = [mnote(1000, 1300, 72), mnote(2000, 2010, 48)]
        n = m.rebuild_from_reference_midi(midi)
        self.assertEqual(n, 2)
        self.assertEqual((m.notes_tree[0].start, m.notes_tree[0].end, m.notes_tree[0].pitch),
                         (1000, 1300, 72))
        self.assertEqual((m.notes_tree[1].start, m.notes_tree[1].pitch), (2000, 48))
        # 長度 >= 180ms → long(2)，短的 → tap(0)
        self.assertEqual(m.notes_tree[0].note_type, 2)
        self.assertEqual(m.notes_tree[1].note_type, 0)

    def test_lane_distribution_is_time_local(self):
        # 前段 pitch60 在 lane3；後段 pitch60 在 lane20。同一音高、不同時間 → 不同 lane。
        m = NoteModel()
        early = [onote(t, 60, 3, i) for i, t in enumerate((0, 100, 200, 300))]
        late = [onote(t, 60, 20, 10 + i) for i, t in enumerate((5000, 5100, 5200, 5300))]
        m.notes_tree = early + late
        midi = [mnote(100, 150, 60), mnote(5100, 5150, 60)]
        m.rebuild_from_reference_midi(midi, window_ms=2000)
        # 前段音符落在 lane3 附近、後段落在 lane20 附近
        self.assertEqual(m.notes_tree[0].min_key, 3)
        self.assertEqual(m.notes_tree[1].min_key, 20)

    def test_lane_follows_local_linear_pitch_map(self):
        # 局部線性：pitch50->lane2, 60->lane6, 70->lane10  (slope 0.4)
        m = NoteModel()
        m.notes_tree = [
            onote(0, 50, 2, 0), onote(100, 60, 6, 1), onote(200, 70, 10, 2),
            onote(300, 55, 4, 3), onote(400, 65, 8, 4),
        ]
        m.rebuild_from_reference_midi([mnote(250, 300, 65)], window_ms=2000)
        # pitch65 → 介於 lane8 附近（線性外推）
        self.assertTrue(6 <= m.notes_tree[0].min_key <= 10,
                        f'got {m.notes_tree[0].min_key}')

    def test_lane_clamped_in_range(self):
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 27, 0)]  # 邊界 lane
        m.rebuild_from_reference_midi([mnote(0, 50, 120)], window_ms=2000)
        self.assertTrue(0 <= m.notes_tree[0].min_key <= 27)
        self.assertTrue(0 <= m.notes_tree[0].max_key <= 27)

    def test_hand_follows_midi_track_order(self):
        # 直接照 MIDI 音軌順序：第一音軌→右手(0)、第二→左手(1)。
        # 故意讓第一軌音高「較低」，證明是走軌序而非音高。
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 5, 0)]
        midi = [
            mnote(0, 100, 48, track=1), mnote(100, 200, 50, track=1),  # 第一軌(低音)→右手
            mnote(0, 100, 72, track=2), mnote(100, 200, 74, track=2),  # 第二軌(高音)→左手
        ]
        m.rebuild_from_reference_midi(midi)
        hb = {n.pitch: n.hand for n in m.notes_tree}
        self.assertEqual(hb[48], 0)  # 第一軌 → 右手
        self.assertEqual(hb[50], 0)
        self.assertEqual(hb[72], 1)  # 第二軌 → 左手
        self.assertEqual(hb[74], 1)

    def test_hand_ignores_empty_tempo_track(self):
        # track index 從 1、2 開始（track0 是無音符的 tempo 軌 → 不出現）
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 5, 0)]
        midi = [
            mnote(0, 100, 60, track=1), mnote(0, 100, 62, track=2),
        ]
        m.rebuild_from_reference_midi(midi)
        hb = {n.pitch: n.hand for n in m.notes_tree}
        self.assertEqual(hb[60], 0)  # 第一個有音符的軌 → 右手
        self.assertEqual(hb[62], 1)

    def test_hand_swap(self):
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 5, 0)]
        midi = [mnote(0, 100, 48, track=1), mnote(0, 100, 72, track=2)]
        m.rebuild_from_reference_midi(midi, swap_hands=True)
        hb = {n.pitch: n.hand for n in m.notes_tree}
        self.assertEqual(hb[48], 1)  # 翻轉後 第一軌 → 左手
        self.assertEqual(hb[72], 0)

    def test_hand_single_track_pitch_split(self):
        m = NoteModel()
        m.notes_tree = [onote(0, 60, 5, 0)]
        midi = [mnote(0, 100, 72, track=0), mnote(100, 200, 48, track=0)]
        m.rebuild_from_reference_midi(midi)
        hb = {n.pitch: n.hand for n in m.notes_tree}
        self.assertEqual(hb[72], 0)  # >=60 右手
        self.assertEqual(hb[48], 1)  # <60 左手

    def test_set_beat_grid_ms_json(self):
        m = NoteModel()  # root=None → JSON 路徑
        cnt = m.set_beat_grid_ms([0, 500, 1000, 1000, 1500])  # 含重複
        self.assertEqual(cnt, 4)  # 去重後 4 個
        entries = m.get_beat_entries()
        self.assertEqual([ms for _, ms in entries], [0, 500, 1000, 1500])


if __name__ == '__main__':
    unittest.main()
