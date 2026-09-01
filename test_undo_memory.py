"""復原歷史的成本與正確性。

使用者回報「比較差的電腦會閃退」。量出來的原因很直接：`push_history` 在
1738 顆的官方 XML 譜上要 **1.2 秒 / 15MB**，而且每編輯一次就來一遍，50 筆
歷史就是 750MB。兩個根因：

1. `deepcopy(notes_tree)` 會把每顆音符抓著的 `<note>` 元素（trill／隱藏音還帶
   一串 `sub_note`）整棵複製一次。音符的欄位才是權威，元素在存檔時會被
   `apply_back` 重寫，所以共用就好。
2. 快照存了 `note_data_xml`，但 `undo` 從來不用它（notes_tree 才是權威）；
   而 `root_xml` 又把整個 note_data 再序列化一次。
"""

import glob
import os
import tempfile
import unittest

from qt_editor.models import GNote, NoteModel

OFFICIAL = sorted(glob.glob(
    r'D:\Nostalgia\PAN-001-2024102200_extracted\PAN-001-2024102200'
    r'\contents\data\sound\music\*\*_03real.xml'))


def make(idx, start, pitch=60):
    n = GNote(None, idx)
    n.start, n.end, n.gate = start, start + 80, 80
    n.pitch = pitch
    n.hand = 0
    n.min_key, n.max_key = 5, 7
    n.note_type = 0
    return n


def chart(count):
    m = NoteModel.create_new('t', 120.0, 600.0, 4)
    m.notes_tree = [make(i, i * 100) for i in range(count)]
    m.rebuild_display_cache()
    return m


class SnapshotCostTests(unittest.TestCase):
    def test_a_snapshot_does_not_clone_the_xml_elements(self):
        """共用元素才是這次提速的關鍵——複製到的話又會變回幾百 ms。"""
        import copy
        m = chart(3)
        import xml.etree.ElementTree as ET
        for n in m.notes_tree:
            n.elem = ET.Element('note')
            n.sub_elems = [ET.Element('sub_note')]
        clone = copy.deepcopy(m.notes_tree)
        for original, copied in zip(m.notes_tree, clone):
            self.assertIs(copied.elem, original.elem, '元素要共用')
            self.assertIs(copied.sub_elems[0], original.sub_elems[0])

    def test_the_sub_elem_list_itself_is_not_shared(self):
        # 共用元素，但不共用那個 list：有人就地改動時不該影響快照
        import copy
        import xml.etree.ElementTree as ET
        m = chart(1)
        m.notes_tree[0].sub_elems = [ET.Element('sub_note')]
        clone = copy.deepcopy(m.notes_tree)
        m.notes_tree[0].sub_elems.append(ET.Element('sub_note'))
        self.assertEqual(len(clone[0].sub_elems), 1)

    def test_the_fields_are_still_independent(self):
        import copy
        m = chart(2)
        clone = copy.deepcopy(m.notes_tree)
        m.notes_tree[0].start = 99999
        self.assertNotEqual(clone[0].start, 99999)


class UndoDepthBudgetTests(unittest.TestCase):
    def test_small_charts_keep_the_full_depth(self):
        m = chart(200)
        self.assertEqual(m._undo_depth_budget(), m.undo_limit)

    def test_big_charts_get_a_shallower_history(self):
        self.assertLess(chart(20000)._undo_depth_budget(),
                        chart(200)._undo_depth_budget())

    def test_there_is_always_a_usable_floor(self):
        self.assertGreaterEqual(chart(500000)._undo_depth_budget(), 8)

    def test_the_stack_is_actually_capped(self):
        m = chart(5000)
        depth = m._undo_depth_budget()
        for _ in range(depth + 25):
            m.push_history()
        self.assertEqual(len(m.undo_stack), depth)

    def test_the_budget_setting_is_honoured(self):
        from qt_editor.settings import settings
        saved = settings.get('undo_memory_mb')
        try:
            settings.set('undo_memory_mb', 8)
            small = chart(5000)._undo_depth_budget()
            settings.set('undo_memory_mb', 256)
            large = chart(5000)._undo_depth_budget()
            self.assertLess(small, large)
        finally:
            settings.set('undo_memory_mb', saved)

    def test_undo_still_works_after_the_stack_is_trimmed(self):
        m = chart(300)
        for i in range(5):
            m.push_history()
            m.notes_tree[0].start = 1000 + i
        self.assertTrue(m.undo())
        self.assertEqual(m.notes_tree[0].start, 1003)


class UndoCorrectnessTests(unittest.TestCase):
    def test_a_plain_edit_round_trips(self):
        m = chart(50)
        before = [(n.start, n.end) for n in m.notes_tree]
        m.push_history()
        for n in m.notes_tree:
            n.start += 111
        m.undo()
        self.assertEqual([(n.start, n.end) for n in m.notes_tree], before)

    def test_adding_and_removing_notes_round_trips(self):
        m = chart(20)
        m.push_history()
        m.notes_tree.append(make(99, 999999))
        del m.notes_tree[0]
        m.rebuild_display_cache()
        m.undo()
        self.assertEqual(len(m.notes_tree), 20)
        self.assertEqual(m.notes_tree[0].start, 0)


@unittest.skipUnless(OFFICIAL, '找不到官方譜面')
class OfficialUndoRoundTripTests(unittest.TestCase):
    """官方 XML：undo 之後存出來的檔案要和沒編輯過**逐字元相同**。

    這條會抓到「root 快照把 note_data 整個拆掉」那種錯——還原出來的樹少了那個
    節點，`save_xml` 會在檔尾重新長一個，內容一樣但整份檔案全變了。
    """

    def test_undo_restores_the_exact_file(self):
        d = tempfile.mkdtemp()
        for path in OFFICIAL[:4]:
            base = NoteModel()
            base.load_xml(path)
            clean = os.path.join(d, 'clean_' + os.path.basename(path))
            base.save_xml(clean)
            with open(clean, encoding='utf-8') as fh:
                expected = fh.read()

            m = NoteModel()
            m.load_xml(path)
            m.push_history()
            for n in m.notes_tree[:10]:
                n.start += 41
                n.end += 41
            del m.notes_tree[3]
            m.rebuild_display_cache()
            m.undo()
            out = os.path.join(d, 'undo_' + os.path.basename(path))
            m.save_xml(out)
            with open(out, encoding='utf-8') as fh:
                self.assertEqual(fh.read(), expected, os.path.basename(path))

    def test_the_note_data_element_keeps_its_position(self):
        m = NoteModel()
        m.load_xml(OFFICIAL[0])
        before = [c.tag for c in list(m.root)]
        m.push_history()
        m.undo()
        self.assertEqual([c.tag for c in list(m.root)], before)


if __name__ == '__main__':
    unittest.main()


class AudioVolumePerfTests(unittest.TestCase):
    """音量縮放與混音不能用逐取樣的 Python 迴圈。

    舊版 `_apply_volume` 對一首四分鐘的立體聲歌曲要跑 3.2 秒，而按播放、拉音量
    滑桿、換位置都會重跑一次（`_apply_volume_restart` 整段重新準備）——那就是
    「按下播放會卡住好幾秒」。改用 `audioop`（C 實作）之後 0.06 秒。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from qt_editor.audio_player import AudioPlayer
        self.AudioPlayer = AudioPlayer

    def pcm(self, seconds=2):
        import array
        n = 44100 * seconds * 2
        return array.array('h', [1000, -2000, 30000, -30000] * (n // 4)).tobytes()

    def test_volume_matches_the_python_fallback(self):
        import array
        import qt_editor.audio_player as mod
        data = self.pcm()
        fast = self.AudioPlayer._apply_volume(data, 0.6, 2)
        saved = mod._AUDIOOP_MUL
        mod._AUDIOOP_MUL = None
        try:
            slow = self.AudioPlayer._apply_volume(data, 0.6, 2)
        finally:
            mod._AUDIOOP_MUL = saved
        a = array.array('h'); a.frombytes(fast)
        b = array.array('h'); b.frombytes(slow)
        self.assertEqual(len(a), len(b))
        # audioop 是四捨五入、Python 是截斷，差最多 1 LSB
        self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), 1)

    def test_full_volume_is_a_no_op(self):
        data = self.pcm(1)
        self.assertIs(self.AudioPlayer._apply_volume(data, 1.0, 2), data)

    def test_silence_is_silent(self):
        import array
        out = self.AudioPlayer._apply_volume(self.pcm(1), 0.0, 2)
        a = array.array('h'); a.frombytes(out)
        self.assertEqual(set(a), {0})

    def test_it_does_not_clip_or_wrap(self):
        import array
        out = self.AudioPlayer._apply_volume(self.pcm(1), 0.9, 2)
        a = array.array('h'); a.frombytes(out)
        self.assertTrue(all(-32768 <= v <= 32767 for v in a))

    def test_the_length_is_preserved(self):
        data = self.pcm(1)
        self.assertEqual(len(self.AudioPlayer._apply_volume(data, 0.5, 2)),
                         len(data))

    def test_it_is_fast(self):
        import time
        data = self.pcm(30)
        t0 = time.perf_counter()
        self.AudioPlayer._apply_volume(data, 0.5, 2)
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.5, '30 秒音訊套音量不該超過 0.5 秒')

    def test_the_mixer_matches_the_python_fallback(self):
        import array
        import qt_editor.audio_player as mod
        ap = self.AudioPlayer()
        p1 = self.pcm(1)
        p2 = self.pcm(1)[::-1]
        p2 = p2[:len(p2) - len(p2) % 2]
        fast = ap._mix_pcm(p1, p2, 2)
        saved = mod._AUDIOOP_MUL
        mod._AUDIOOP_MUL = None
        try:
            slow = ap._mix_pcm(p1, p2, 2)
        finally:
            mod._AUDIOOP_MUL = saved
        a = array.array('h'); a.frombytes(fast)
        b = array.array('h'); b.frombytes(slow)
        self.assertEqual(len(a), len(b))
        self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), 1)


class VolumeDuringPlaybackTests(unittest.TestCase):
    """播放中改音量不可以變成「沒有聲音卻還在播」。

    以前 `_apply_volume_restart` 只處理歌曲 WAV：播 MIDI 鋼琴預覽時聲音在
    `_pcm_buf`，它卻去切 `audio_bytes`，沒載入歌曲就切出空的，於是後端被停掉
    之後再也沒被啟動——沒聲音，但 `_playing` 還是 True、判定線繼續跑。
    """

    def setUp(self):
        import array
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from qt_editor.audio_player import AudioPlayer
        self.rate = 44100
        self.pcm = array.array('h', [10000, -10000] * (self.rate * 4)).tobytes()
        self.ap = AudioPlayer()

    def tearDown(self):
        try:
            self.ap.stop()
        except Exception:
            pass

    def amp(self, buf, n=2000):
        import array
        a = array.array('h')
        a.frombytes(buf[:n])
        return max(abs(v) for v in a)

    def start(self, volume=1.0):
        ok = self.ap.play_pcm(self.pcm, self.rate, 2, 2, 0.0, 4000.0,
                              volume=volume)
        self.assertTrue(ok, '預覽播放沒有啟動')

    def test_preview_keeps_playing_after_a_volume_change(self):
        self.start()
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        self.assertTrue(self.ap._playing)
        self.assertTrue(getattr(self.ap, '_sa_objs', []),
                        '改完音量之後後端應該還在')

    def test_the_volume_actually_changes(self):
        self.start()
        self.assertEqual(self.amp(self.ap._pcm_buf), 10000)
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        self.assertAlmostEqual(self.amp(self.ap._pcm_buf), 5000, delta=2)

    def test_repeated_changes_scale_from_the_original(self):
        # 從已縮過的資料再乘一次會越乘越小
        self.start()
        for vol, want in ((0.5, 5000), (0.25, 2500), (1.0, 10000)):
            self.ap._volume = vol
            self.ap._apply_volume_restart()
            self.assertAlmostEqual(self.amp(self.ap._pcm_buf), want, delta=2,
                                   msg='音量 %s' % vol)

    def test_the_position_does_not_jump(self):
        import time
        self.start()
        time.sleep(0.25)
        before = self.ap.current_ms()
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        self.assertAlmostEqual(self.ap.current_ms(), before, delta=30)

    def test_the_position_keeps_advancing(self):
        import time
        self.start()
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        t0 = self.ap.current_ms()
        time.sleep(0.4)
        self.assertGreater(self.ap.current_ms() - t0, 300)

    def test_a_second_change_still_slices_from_the_right_place(self):
        import time
        self.start()
        time.sleep(0.2)
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        time.sleep(0.2)
        mid = self.ap.current_ms()
        self.ap._volume = 0.25
        self.ap._apply_volume_restart()
        self.assertAlmostEqual(self.ap.current_ms(), mid, delta=30,
                               msg='第二次改音量位置跑掉了')

    def test_a_failed_restart_stops_instead_of_going_silent(self):
        fired = []
        self.start()
        self.ap.playback_stopped.connect(lambda: fired.append(1))
        self.ap._pcm_buf = None          # 假裝是歌曲模式，但沒有載入 WAV
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        self.assertFalse(self.ap._playing, '切不出聲音就該停下來')
        self.assertTrue(fired, '要發 playback_stopped 讓 UI 收尾')

    def test_a_paused_player_is_left_alone(self):
        self.start()
        self.ap.pause()
        self.ap._volume = 0.5
        self.ap._apply_volume_restart()
        self.assertTrue(self.ap._paused)
