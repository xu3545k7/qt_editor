"""輸出 Hiraeth 歌曲包（ZIP）：規則照 Hiraeth importer，整個曲庫 96 包實際匯入成功的那一套。"""

import json
import os
import shutil
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
import zipfile

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from qt_editor import hiraeth_export as H  # noqa: E402
from qt_editor.models import GNote, NoteModel  # noqa: E402


def write_wav(path, seconds, rate=22050, channels=1, width=2):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b'\x01\x00' * (width // 2) * channels * int(seconds * rate))


def note(idx, start, note_type=0, hand=0, pitch=60, end=None):
    n = GNote(None, idx)
    n.start, n.end = start, (start + 200 if end is None else end)
    n.gate = n.end - n.start
    n.pitch, n.hand, n.note_type = pitch, hand, note_type
    n.min_key, n.max_key = 10, 12
    return n


def chart_json(path, extra=()):
    m = NoteModel.create_new('t', 120.0, 10.0, 4)
    m.notes_tree = [note(0, 0), note(1, 500, note_type=1), note(2, 1000, note_type=64, end=1800),
                    note(3, 2000, hand=2), note(4, 2500, note_type=3)] + list(extra)
    m.rebuild_display_cache()
    m.save_json(path)


def make_song(root, name='テスト曲', no_bgm=False, piano=True):
    song = os.path.join(root, name)
    for sub in ('Normal', 'Master', 'Piano', 'Test'):
        os.makedirs(os.path.join(song, sub))
    chart_json(os.path.join(song, 'Normal', 'c.json'))
    chart_json(os.path.join(song, 'Master', 'c.json'))
    chart_json(os.path.join(song, 'Piano', 'c.json'))
    chart_json(os.path.join(song, 'Test', 'c.json'))
    write_wav(os.path.join(song, 'bgm.wav'), 10)
    write_wav(os.path.join(song, 'bgm_piano.wav'), 10, rate=32000, channels=2)
    if piano:
        write_wav(os.path.join(song, 'layer_piano.wav'), 10)
    from PIL import Image
    Image.new('RGB', (40, 50), (200, 10, 10)).save(os.path.join(song, 'cover.jpg'))
    base = 'songs/%s/' % name

    def diff(dname, level, sub, audio='bgm'):
        d = {'difficultyName': dname, 'difficultyLevel': level,
             'chartFileName': base + sub + '/c', 'audioResourcePath': base + audio,
             'coverResourcePath': base + 'cover'}
        if piano:
            d['pianoAudioResourcePath'] = base + 'layer_piano'
        if no_bgm:
            d['noBackgroundMusic'] = True
        return d
    register = {'displayName': '彩云追月', 'author': '广东乐团', 'difficulties': [
        diff('Normal', 4, 'Normal'), diff('Master', 15, 'Master'), diff('TEST', 3, 'Test'),
        diff('Master_piano', 13, 'Piano', audio='bgm_piano')]}
    with open(os.path.join(song, 'register.json'), 'w', encoding='utf-8') as fh:
        json.dump(register, fh, ensure_ascii=False)
    return song


def fake_renderer(model):
    return b'\0\0\0\0' * 44100 * 12, 44100


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.song = make_song(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_difficulties_are_grouped_by_audio(self):
        plans, skipped = H.plan_song_folder(self.song)
        self.assertEqual(len(plans), 2)
        main, variant = plans
        self.assertEqual({c.slot: c.name for c in main.charts},
                         {'normal': 'Normal', 'real': 'Master'})
        self.assertEqual([c.slot for c in variant.charts], ['real'])
        self.assertIn('[Master_piano]', variant.title)
        self.assertTrue(any('TEST' in s for s in skipped))

    def test_titles_are_made_shift_jis_safe(self):
        plans, _ = H.plan_song_folder(self.song)
        self.assertEqual(plans[0].title, '彩云追月')
        plans[0].artist.encode('shift_jisx0213')
        self.assertIn('東', plans[0].artist)

    def test_package_id_is_stable(self):
        a, _ = H.plan_song_folder(self.song)
        b, _ = H.plan_song_folder(self.song)
        self.assertEqual([p.package_id for p in a], [p.package_id for p in b])
        self.assertNotEqual(a[0].package_id, a[1].package_id)
        # 和整個曲庫匯入時用的是同一種 id（資料夾名 + 變體），重新匯入才會是更新
        self.assertEqual(a[0].package_id, H.package_id('テスト曲', ''))

    def test_library_skips_tutorials_and_folders_without_register(self):
        os.makedirs(os.path.join(self.root, '新手教學初階00'))
        os.makedirs(os.path.join(self.root, 'no_register'))
        plans, skipped = H.plan_library(self.root)
        self.assertEqual(len(plans), 2)
        self.assertTrue(any('no_register' in s for s in skipped))
        self.assertFalse(any('新手教學' in s for s in skipped))

    def test_line_breaks_in_titles_become_spaces(self):
        self.assertEqual(H.sjis_safe("遠航星的告別 - \nVoyaging  Star's\tFarewell"),
                         "遠航星的告別 - Voyaging Star's Farewell")

    def test_real_levels(self):
        # 9、10、11 都是 REAL 1（沒有 1.5）；12 → 2、13 → 2.5、14 → 3、15 以上 → 3.5
        self.assertEqual([H.real_level(x) for x in (5, 9, 10, 11, 12, 13, 14, 15, 18)],
                         [1, 1, 1, 1, 2, 2.5, 3, 3.5, 3.5])
        self.assertNotIn(1.5, H.REAL_LEVELS)
        self.assertIsInstance(H.real_level(12), int, '整數寫成 2 不是 2.0')


def write_sine(path, seconds, dbfs, rate=44100, channels=2):
    """真的有內容的測試音：DC 訊號量不出響度（會被當成靜音）。"""
    import math
    import struct
    amp = 10.0 ** (dbfs / 20.0)
    frames = []
    for n in range(int(seconds * rate)):
        v = int(round(amp * math.sin(2.0 * math.pi * 440.0 * n / rate) * 32767.0))
        frames.append(struct.pack('<h', max(-32768, min(32767, v))) * channels)
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b''.join(frames))


class LoudnessBuildTests(unittest.TestCase):
    """輸出 ZIP 時把整首混好的音訊對齊目標響度。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, **kwargs):
        song = make_song(self.root, piano=False)
        write_sine(os.path.join(song, 'bgm.wav'), 6.0, -24.0)
        plans, _ = H.plan_song_folder(song)
        out = os.path.join(self.root, 'o')
        result = H.build_package(plans[0], out, version=7, renderer=fake_renderer, **kwargs)
        folder = os.path.join(self.root, 'unzipped')
        shutil.rmtree(folder, ignore_errors=True)
        with zipfile.ZipFile(result.zip_path) as archive:
            archive.extractall(folder)
        return result, os.path.join(folder, 'music.wav')

    def test_quiet_song_is_brought_up_to_target(self):
        from qt_editor import loudness
        result, music = self.build()
        measured = loudness.measure_file(music)
        self.assertAlmostEqual(measured.lufs_i, loudness.DEFAULT_TARGET_LUFS, delta=1.0)
        self.assertLessEqual(measured.true_peak_dbtp, loudness.DEFAULT_PEAK_LIMIT_DBTP + 0.3)
        self.assertTrue(any('LUFS' in note for note in result.notes), result.notes)

    def test_can_be_turned_off(self):
        from qt_editor import loudness
        _result, music = self.build(normalize_loudness=False)
        measured = loudness.measure_file(music)
        self.assertAlmostEqual(measured.lufs_i, -24.0, delta=1.0)

    def test_other_target(self):
        from qt_editor import loudness
        _result, music = self.build(target_lufs=-18.0)
        measured = loudness.measure_file(music)
        self.assertAlmostEqual(measured.lufs_i, -18.0, delta=1.0)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, 'out')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, song, index=0):
        plans, _ = H.plan_song_folder(song)
        return H.build_package(plans[index], self.out, version=7, renderer=fake_renderer)

    def unzip(self, result):
        folder = os.path.join(self.root, 'unzipped')
        shutil.rmtree(folder, ignore_errors=True)
        with zipfile.ZipFile(result.zip_path) as archive:
            names = archive.namelist()
            archive.extractall(folder)
        return folder, names

    def test_zip_passes_the_hiraeth_rules(self):
        result = self.build(make_song(self.root))
        self.assertEqual(result.error, '')
        folder, names = self.unzip(result)
        self.assertEqual(sorted(names), ['cover.png', 'music.wav', 'normal.xml', 'real.xml', 'song.json'])
        self.assertTrue(all('/' not in n for n in names), '全部放在 ZIP 根目錄')
        self.assertEqual(H.validate_package(folder), [])
        with open(os.path.join(folder, 'song.json'), encoding='utf-8') as fh:
            spec = json.load(fh)
        self.assertEqual(spec['charts'], {'normal': 4, 'real': 3.5})       # Master 等級 15 → REAL 3.5
        self.assertEqual(spec['version'], 7)

    def test_chart_rules(self):
        folder, _ = self.unzip(self.build(make_song(self.root)))
        root = ET.parse(os.path.join(folder, 'normal.xml')).getroot()
        self.assertEqual(root.findtext('header/min_scale'), '1')
        self.assertEqual(root.findtext('header/max_scale'), '88')
        notes = root.findall('note_data/note')
        self.assertEqual(len(notes), 4, '自動彈的那顆要拿掉')
        # 測試譜裡唯一有長度的就是那顆顫音：以前被改成 2（長押），現在要是 64
        self.assertEqual({n.findtext('note_type') for n in notes}, {'0', '64'},
                         '顫音要保留成 64，不能變長押')
        trill = [n for n in notes if n.findtext('note_type') == '64'][0]
        self.assertTrue(trill.findall('sub_note_data/sub_note'), '顫音要帶著它的 sub_note')
        self.assertEqual({n.findtext('hand') for n in notes}, {'0'})
        self.assertEqual({s.findtext('velocity') for s in root.iter('sub_note')}, {'0'})
        self.assertEqual({t.findtext('name') for t in root.iter('track')}, {'key_apiano1'})

    def test_notes_describe_the_conversion(self):
        result = self.build(make_song(self.root))
        # 兩個難度（normal、real）各一顆，加總
        self.assertIn('2 顆顫音照原樣保留', result.notes)
        self.assertIn('拿掉 2 顆自動彈的音符', result.notes)

    def test_audio_is_converted_and_cover_scaled_up(self):
        folder, _ = self.unzip(self.build(make_song(self.root), index=1))   # 32kHz 雙聲道那份
        with wave.open(os.path.join(folder, 'music.wav')) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (2, 2, 44100))
        from PIL import Image
        with Image.open(os.path.join(folder, 'cover.png')) as image:
            self.assertGreaterEqual(min(image.size), 74)

    def test_no_background_uses_the_piano_layer(self):
        result = self.build(make_song(self.root, no_bgm=True, piano=True))
        self.assertTrue(any('鋼琴音軌 layer_piano.wav' in n for n in result.notes))

    def test_no_background_and_no_piano_renders_one(self):
        result = self.build(make_song(self.root, no_bgm=True, piano=False))
        self.assertEqual(result.error, '')
        self.assertTrue(any('內建音源' in n for n in result.notes))

    def test_short_audio_is_padded(self):
        song = make_song(self.root, piano=False)             # 疊上 10 秒的鋼琴音軌就不短了
        write_wav(os.path.join(song, 'bgm.wav'), 1)          # 譜面到 2.7 秒，音訊只有 1 秒
        result = self.build(song)
        self.assertEqual(result.error, '')
        self.assertTrue(any('補' in n for n in result.notes))

    def test_a_broken_chart_is_reported_not_zipped(self):
        song = make_song(self.root)
        os.remove(os.path.join(song, 'Normal', 'c.json'))
        os.remove(os.path.join(song, 'Master', 'c.json'))
        result = self.build(song)
        self.assertTrue(result.error)
        self.assertEqual(result.zip_path, '')


class MixTests(unittest.TestCase):
    def test_quiet_layers_are_added_as_is(self):
        import audioop
        base = audioop.tostereo(b'\x10\x00' * 100, 2, 1, 1)
        layer = audioop.tostereo(b'\x20\x00' * 100, 2, 1, 1)
        mixed, rate, lowered = H.mix_pcm(base, 44100, layer, 44100)
        self.assertEqual(rate, 44100)
        self.assertEqual(lowered, 0.0)
        self.assertEqual(audioop.getsample(mixed, 2, 0), 0x30)

    def test_loud_layers_are_lowered_instead_of_clipping(self):
        import audioop
        loud = audioop.tostereo(b'\x00\x60' * 100, 2, 1, 1)        # 24576
        mixed, _rate, lowered = H.mix_pcm(loud, 44100, loud, 44100)
        self.assertAlmostEqual(lowered, 3.52, delta=0.05)          # 峰值 49152 → 降到 32767
        self.assertLessEqual(audioop.max(mixed, 2), 32767)
        self.assertGreater(audioop.getsample(mixed, 2, 0), 30000)

    def test_lengths_and_rates_are_matched(self):
        base = b'\x01\x00' * 2 * 44100              # 1 秒 44.1k
        layer = b'\x01\x00' * 2 * 22050 * 2         # 2 秒 22.05k
        mixed, rate, _ = H.mix_pcm(base, 44100, layer, 22050)
        self.assertEqual(rate, 44100)
        self.assertAlmostEqual(len(mixed) / 4 / rate, 2.0, delta=0.01)


class PianoLayerAndHoldTailTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_piano_layer_is_mixed_in(self):
        plans, _ = H.plan_song_folder(make_song(self.root))
        result = H.build_package(plans[0], os.path.join(self.root, 'o'), renderer=fake_renderer)
        self.assertEqual(result.error, '')
        self.assertIn('疊上鋼琴音軌 layer_piano.wav', result.notes)

    def test_mixing_can_be_turned_off(self):
        plans, _ = H.plan_song_folder(make_song(self.root))
        plans[0].mix_piano = False
        result = H.build_package(plans[0], os.path.join(self.root, 'o'), renderer=fake_renderer)
        self.assertFalse(any('疊上' in n for n in result.notes))

    def test_hold_tails_leave_at_least_80ms(self):
        song = make_song(self.root)
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        m.notes_tree = [note(0, 0, note_type=2, end=990), note(1, 1000),
                        note(2, 2000, note_type=2, end=2950), note(3, 3000)]
        m.rebuild_display_cache()
        m.save_json(os.path.join(song, 'Normal', 'c.json'))
        plans, _ = H.plan_song_folder(song)
        result = H.build_package(plans[0], os.path.join(self.root, 'o'), renderer=fake_renderer)
        self.assertEqual(result.error, '')
        self.assertTrue(any('處理長條尾端（間距 80 ms）' in n for n in result.notes), result.notes)
        with zipfile.ZipFile(result.zip_path) as archive:
            root = ET.fromstring(archive.read('normal.xml'))
        notes = [(int(n.findtext('start_timing_msec')), int(n.findtext('end_timing_msec')),
                  n.findtext('note_type')) for n in root.iter('note')]
        holds = [(s, e) for s, e, t in notes if t == '2']
        starts = sorted(s for s, _e, _t in notes)
        for s, e in holds:
            nxt = min(x for x in starts if x > s)
            self.assertGreaterEqual(nxt - e, 80, (s, e, nxt))

    def test_dialog_does_not_allow_less_than_80(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        dlg = HiraethExportDialog(None, None, self.root, m)
        self.assertEqual(dlg.gap_spin.minimum(), 80)
        dlg.gap_spin.setValue(20)
        self.assertEqual(dlg.hold_gap_ms(), 80)


class ChartModeSongJsonTests(unittest.TestCase):
    """「只輸出目前譜面」填的值要原封不動寫進 song.json。

    回報的實例（Conflict (VILA Remix).zip）：選 real 填 3，song.json 卻寫 1——
    輸出時把對話框的 REAL 等級又當成遊戲難度等級換算了一次（3 < 11 → 1）。
    package_id 也是用檔案完整路徑算的（c-users-zhuanz-desktop-…），搬個位置就變成另一首。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.model = NoteModel.create_new('t', 120.0, 10.0, 4)
        self.model.notes_tree = [note(0, 0), note(1, 500)]
        self.model.rebuild_display_cache()
        self.model.current_file = os.path.join(self.root, 'somewhere', 'chart.json')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def export(self, slot, level, title='Conflict (VILA Remix)'):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        dlg = HiraethExportDialog(None, None, self.root, self.model)
        dlg.title_edit.setText(title)
        dlg.artist_edit.setText('siromaru + cranky')
        dlg.slot_combo.setCurrentText(slot)
        dlg.level_spin.setValue(level)
        plans, _ = dlg.plans()
        result = H.build_package(plans[0], os.path.join(self.root, 'o'), renderer=fake_renderer)
        self.assertEqual(result.error, '')
        with zipfile.ZipFile(result.zip_path) as archive:
            return json.loads(archive.read('song.json'))

    def test_real_level_is_written_as_typed(self):
        for level in (1, 2, 2.5, 3, 3.5):
            self.assertEqual(self.export('real', level)['charts'], {'real': level})
        # 沒有 REAL 1.5：數字框停在 1.5 也會輸出成 1
        self.assertEqual(self.export('real', 1.5)['charts'], {'real': 1})

    def test_other_levels_are_written_as_typed(self):
        self.assertEqual(self.export('expert', 12)['charts'], {'expert': 12})

    def test_title_and_artist_are_written(self):
        spec = self.export('hard', 8)
        self.assertEqual((spec['title'], spec['artist']), ('Conflict (VILA Remix)', 'siromaru + cranky'))

    def test_package_id_does_not_depend_on_where_the_file_is(self):
        first = self.export('real', 3)['package_id']
        self.model.current_file = os.path.join(self.root, 'moved', 'elsewhere.json')
        self.assertEqual(self.export('expert', 12)['package_id'], first)
        self.assertNotIn('users', first)

    def test_switching_slots_keeps_the_typed_level(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        dlg = HiraethExportDialog(None, None, self.root, self.model)
        dlg.slot_combo.setCurrentText('expert')
        dlg.level_spin.setValue(12)
        dlg.slot_combo.setCurrentText('real')
        dlg.level_spin.setValue(2.5)
        dlg.slot_combo.setCurrentText('expert')
        self.assertEqual(dlg.level_spin.value(), 12)
        dlg.slot_combo.setCurrentText('real')
        self.assertEqual(dlg.level_spin.value(), 2.5)


class SloganTests(unittest.TestCase):
    """樂曲評論（song.json 的 slogan）：輸出 ZIP 時要有地方寫。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.song = make_song(self.root)
        from qt_editor.settings import settings
        self._saved = {k: settings._data.get(k) for k in ('hiraeth_export_dir', 'hiraeth_mix_piano',
                                                         'hiraeth_hold_gap_ms')}

    def tearDown(self):
        from qt_editor.settings import settings
        for k, v in self._saved.items():
            if v is None:
                settings._data.pop(k, None)
            else:
                settings._data[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def song_json(self, plan):
        result = H.build_package(plan, os.path.join(self.root, 'o'), renderer=fake_renderer)
        self.assertEqual(result.error, '')
        with zipfile.ZipFile(result.zip_path) as archive:
            return json.loads(archive.read('song.json'))

    def dialog(self):
        from unittest import mock
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        m.notes_tree = [note(0, 0)]
        m.rebuild_display_cache()
        dlg = HiraethExportDialog(None, self.song, self.root, m)
        dlg.out_edit.setText(os.path.join(self.root, 'o'))
        self._patch = mock.patch('qt_editor.hiraeth_export_dialog.settings.set')
        self._patch.start()
        self.addCleanup(self._patch.stop)
        return dlg

    def test_slogan_from_register_goes_into_song_json(self):
        H.write_slogan(self.song, '雨の日に聴きたい一曲')
        plans, _ = H.plan_song_folder(self.song)
        self.assertEqual(self.song_json(plans[0])['slogan'], '雨の日に聴きたい一曲')

    def test_dialog_prefills_and_saves_back_to_register(self):
        H.write_slogan(self.song, '舊的評論')
        dlg = self.dialog()
        self.assertEqual(dlg.slogan_edit.text(), '舊的評論')
        dlg.slogan_edit.setText('新的評論')
        dlg._accept()
        self.assertEqual(H.read_slogan(self.song), '新的評論')
        plans, _ = dlg.plans()
        self.assertEqual(self.song_json(plans[0])['slogan'], '新的評論')

    def test_chart_mode_slogan(self):
        dlg = self.dialog()
        dlg.rb_chart.setChecked(True)
        dlg.title_edit.setText('My Chart')
        dlg.slogan_edit.setText('只有這一份')
        plans, _ = dlg.plans()
        self.assertEqual(self.song_json(plans[0])['slogan'], '只有這一份')

    def test_slogan_is_cleaned(self):
        self.assertEqual(H.clean_slogan('第一行\n第二行'), '第一行 第二行')
        self.assertEqual(len(H.clean_slogan('あ' * 300)), H.MAX_SLOGAN)
        self.assertIn('東', H.clean_slogan('广东'))

    def test_library_mode_uses_each_register(self):
        H.write_slogan(self.song, '每首自己的')
        dlg = self.dialog()
        dlg.rb_library.setChecked(True)
        self.assertFalse(dlg.slogan_edit.isEnabled())
        dlg.slogan_edit.setText('不該被用到')
        plans, _ = dlg.plans()
        self.assertTrue(plans)
        self.assertTrue(all(p.slogan == '每首自己的' for p in plans))


class ValidatorTests(unittest.TestCase):
    def test_keysounds_are_rejected(self):
        root = tempfile.mkdtemp()
        try:
            out = os.path.join(root, 'o')
            plans, _ = H.plan_song_folder(make_song(root))
            result = H.build_package(plans[0], out, renderer=fake_renderer)
            folder = os.path.join(root, 'x')
            with zipfile.ZipFile(result.zip_path) as archive:
                archive.extractall(folder)
            path = os.path.join(folder, 'normal.xml')
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(text.replace('<velocity __type="u8">0</velocity>', '<velocity __type="u8">90</velocity>', 1))
            self.assertTrue(any('力度' in p for p in H.validate_package(folder)))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class CurrentChartTests(unittest.TestCase):
    def test_exporting_the_open_chart_does_not_change_it(self):
        root = tempfile.mkdtemp()
        try:
            path = os.path.join(root, 'c.json')
            chart_json(path)
            model = NoteModel()
            model.load_json(path)
            model.dirty = True
            from qt_editor.hiraeth_export_dialog import MODE_CHART, HiraethExportDialog
            dlg = HiraethExportDialog(None, None, root, model, audio_path='')
            self.assertEqual(dlg.mode(), MODE_CHART)
            dlg.title_edit.setText('我的譜')
            dlg.slot_combo.setCurrentText('real')
            self.assertEqual(dlg.level_spin.maximum(), 3.5)
            dlg.level_spin.setValue(2.5)
            plans, _ = dlg.plans()
            result = H.build_package(plans[0], os.path.join(root, 'o'), renderer=fake_renderer)
            self.assertEqual(result.error, '')
            self.assertIn(2, [n.hand for n in model.notes_tree], '原本的譜不能被改')
            self.assertEqual(model.current_file, path)
            self.assertTrue(model.dirty)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class LeadInTests(unittest.TestCase):
    """開頭留白：官方譜第一顆音符中位數就落在第 4 拍（一整小節），輸出 ZIP 預設補到那樣。

    補的是空白小節（拍點還是從 0 開始），音訊前面補等長的靜音，JSON 原檔不動。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def model(self, first_ms=0):
        m = NoteModel.create_new('t', 120.0, 10.0, 4)     # 120 BPM 4/4 → 一小節 2000ms
        m.notes_tree = [note(0, first_ms), note(1, first_ms + 500)]
        m.rebuild_display_cache()
        return m

    def test_a_chart_that_starts_at_zero_gets_one_empty_bar(self):
        m = self.model(0)
        delta = H.apply_lead_in([m], 1)
        self.assertEqual(delta, 2000)
        self.assertEqual(H.first_note_ms(m), 2000)
        self.assertEqual(m.get_measure_at_ms(2000.0), 1, '第一顆音符落在第 2 小節')
        self.assertEqual(min(ms for _idx, ms in m.get_beat_entries()), 0, '拍點還是從 0 開始')

    def test_a_chart_that_already_waits_is_left_alone(self):
        m = self.model(2400)
        self.assertEqual(H.apply_lead_in([m], 1), 0)
        self.assertEqual(H.first_note_ms(m), 2400)

    def test_zero_bars_means_no_change(self):
        m = self.model(0)
        self.assertEqual(H.apply_lead_in([m], 0), 0)
        self.assertEqual(H.first_note_ms(m), 0)

    def test_every_difficulty_shifts_by_the_same_amount(self):
        a, b = self.model(0), self.model(300)
        delta = H.apply_lead_in([a, b], 1)
        self.assertEqual(delta, 2000)
        self.assertEqual((H.first_note_ms(a), H.first_note_ms(b)), (2000, 2300))

    def build(self, lead_bars):
        # 同一個測試會建兩包，兩份來源要分開放（make_song 不會覆蓋既有資料夾）
        base = os.path.join(self.root, 'src%d' % lead_bars)
        os.makedirs(base, exist_ok=True)
        song = make_song(base)
        plans, _ = H.plan_song_folder(song)
        plan = plans[0]
        plan.lead_in_bars = lead_bars
        out = os.path.join(self.root, 'out%d' % lead_bars)
        result = H.build_package(plan, out, version=7, renderer=fake_renderer)
        self.assertEqual(result.error, '')
        folder = os.path.join(self.root, 'un%d' % lead_bars)
        with zipfile.ZipFile(result.zip_path) as archive:
            archive.extractall(folder)
        starts = sorted(int(n.findtext('start_timing_msec'))
                        for n in ET.parse(os.path.join(folder, 'real.xml')).getroot().iter('note'))
        with wave.open(os.path.join(folder, 'music.wav')) as w:
            audio_ms = w.getnframes() / w.getframerate() * 1000.0
        return result, starts, audio_ms, folder

    def test_the_zip_gets_the_lead_in_and_the_audio_follows(self):
        _r0, plain, audio0, _f = self.build(0)
        result, shifted, audio1, folder = self.build(1)
        self.assertEqual(plain[0], 0, '前提：這份譜從 0 開始')
        self.assertEqual(shifted[0], 2000)
        self.assertEqual([s - 2000 for s in shifted], plain, '整份譜一起往後，不只是第一顆')
        self.assertAlmostEqual(audio1 - audio0, 2000, delta=30, msg='音訊要補一樣長的靜音')
        self.assertTrue(any('開頭' in n for n in result.notes))
        self.assertEqual(H.validate_package(folder), [])

    def test_the_silence_is_really_silent(self):
        _r, _starts, _ms, folder = self.build(1)
        with wave.open(os.path.join(folder, 'music.wav')) as w:
            head = w.readframes(int(w.getframerate() * 1.9))
        self.assertEqual(set(head), {0})

    def test_the_source_json_is_untouched(self):
        song = make_song(self.root)
        chart = os.path.join(song, 'Normal', 'c.json')
        with open(chart, encoding='utf-8') as fh:
            before = fh.read()
        plans, _ = H.plan_song_folder(song)
        H.build_package(plans[0], os.path.join(self.root, 'o'), version=7, renderer=fake_renderer)
        with open(chart, encoding='utf-8') as fh:
            self.assertEqual(fh.read(), before)

    def test_the_dialog_remembers_the_setting(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        from qt_editor.settings import settings
        saved = settings._data.get('hiraeth_lead_in_bars')
        try:
            settings._data['hiraeth_lead_in_bars'] = 2
            dlg = HiraethExportDialog(None, make_song(self.root), self.root, None)
            self.assertEqual(dlg.lead_in_bars(), 2)
            plans, _ = dlg.plans()
            self.assertTrue(all(p.lead_in_bars == 2 for p in plans))
            dlg.close()
        finally:
            settings._data['hiraeth_lead_in_bars'] = saved if saved is not None else 1


class HoldLaneConflictTests(unittest.TestCase):
    """最後的 XML 上清長條：走廊與尾巴不准碰到同鍵道的音，同手前後要留間隔。"""

    def xml(self, notes):
        import xml.etree.ElementTree as ET
        root = ET.Element('music_score')
        data = ET.SubElement(root, 'note_data')
        for start, end, lo, hi, kind, hand in notes:
            n = ET.SubElement(data, 'note')
            for tag, value in (('start_timing_msec', start), ('end_timing_msec', end),
                               ('gate_time_msec', end - start), ('min_key_index', lo),
                               ('max_key_index', hi), ('note_type', kind), ('hand', hand)):
                ET.SubElement(n, tag).text = str(value)
        return root

    def rows(self, root):
        return [(int(n.findtext('start_timing_msec')), int(n.findtext('end_timing_msec')),
                 int(n.findtext('note_type'))) for n in root.iter('note')]

    def test_other_hand_in_the_corridor_cuts_the_hold(self):
        root = self.xml([(0, 1000, 10, 12, 2, 0), (500, 600, 11, 13, 0, 1)])
        self.assertEqual(H.trim_holds_against_lane_conflicts(root, 80), (1, 0))
        self.assertEqual(self.rows(root)[0], (0, 420, 2))

    def test_tail_touching_the_next_note_in_the_same_lane(self):
        root = self.xml([(0, 1000, 10, 12, 2, 1), (1040, 1100, 10, 12, 0, 0)])
        H.trim_holds_against_lane_conflicts(root, 80)
        self.assertEqual(self.rows(root)[0][1], 960)

    def test_same_hand_next_note_after_release_gets_the_gap(self):
        root = self.xml([(0, 1000, 10, 12, 2, 0), (1030, 1100, 20, 22, 0, 0)])
        H.trim_holds_against_lane_conflicts(root, 80)
        self.assertEqual(self.rows(root)[0][1], 950)

    def test_arpeggio_in_other_lanes_is_left_alone(self):
        root = self.xml([(0, 1000, 10, 12, 2, 0), (300, 400, 14, 16, 0, 0), (600, 700, 18, 20, 0, 0)])
        self.assertEqual(H.trim_holds_against_lane_conflicts(root, 80), (0, 0))

    def test_trim_is_repeated_until_stable(self):
        # 同鍵道 1100 → 裁到 1020；這時 1000 起音的同手音變成「放開前」…
        # 反過來：同手音 950 本來在放開前，同鍵道 1000 把尾巴裁到 920 之後它變成放開後 30ms
        root = self.xml([(0, 1100, 10, 12, 2, 0), (950, 1000, 20, 22, 0, 0), (1000, 1050, 10, 12, 0, 1)])
        H.trim_holds_against_lane_conflicts(root, 80)
        self.assertEqual(self.rows(root)[0][1], 870)

    def test_too_short_becomes_a_tap(self):
        root = self.xml([(0, 1000, 10, 12, 2, 0), (100, 200, 10, 12, 0, 1)])
        self.assertEqual(H.trim_holds_against_lane_conflicts(root, 80), (1, 1))
        self.assertEqual(self.rows(root)[0], (0, 20, 0))


class PackageFileNameTests(unittest.TestCase):
    def test_names_do_not_collide(self):
        a = H.PackagePlan(H.package_id('エンドマークに希望と涙を添えて [Master piano]'), 'Master piano', '', [])
        b = H.PackagePlan(H.package_id('系ぎて [Master piano]'), 'Master piano', '', [])
        self.assertNotEqual(H.package_file_name(a), H.package_file_name(b))
        self.assertTrue(H.package_file_name(a).startswith('Master_piano_'))
        self.assertEqual(H.package_file_name(a), H.package_file_name(a))
        c = H.PackagePlan(H.package_id('雨露霜雪'), '雨露霜雪', '', [])
        self.assertTrue(H.package_file_name(c).startswith('song_'))
        self.assertNotIn('.', H.package_file_name(c))


class SoftRunExportTests(unittest.TestCase):
    """Hiraeth 沒有 soft：連續的 soft 在歌曲包裡要變成滑奏鏈／顫音。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_diagonal_softs_become_a_linked_slide_chain(self):
        song = make_song(self.root)
        run = []
        for i in range(6):
            n = note(100 + i, 4000 + i * 20, note_type=1, pitch=60 + i)
            n.min_key, n.max_key = 8 + i, 10 + i
            run.append(n)
        chart_json(os.path.join(song, 'Master', 'c.json'), extra=run)
        plans, _ = H.plan_song_folder(song)
        plan = [p for p in plans if any(c.slot == 'real' for c in p.charts)][0]
        result = H.build_package(plan, os.path.join(self.root, 'out'), renderer=fake_renderer)
        self.assertEqual(result.error, '')
        self.assertTrue(any('滑奏' in line for line in result.notes), result.notes)
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(result.zip_path) as archive:
            root = ET.fromstring(archive.read('real.xml'))
        notes = list(root.iter('note'))
        types = [int(n.findtext('note_type')) for n in notes]
        self.assertNotIn(1, types)
        slides = [n for n in notes if int(n.findtext('note_type')) == 4]
        self.assertEqual(len(slides), 6)
        index = {int(n.findtext('index')): n for n in notes}
        heads = [n for n in slides if int(n.findtext('param1')) == -1]
        self.assertEqual(len(heads), 1)
        # 從鏈頭一路走到鏈尾，六顆都走得到
        seen, cur = 0, heads[0]
        while cur is not None:
            seen += 1
            nxt = int(cur.findtext('param2'))
            cur = index.get(nxt) if nxt != -1 else None
        self.assertEqual(seen, 6)


class HoldTailOptionTests(unittest.TestCase):
    """「處理長條尾端」可以關掉：關掉就照原樣輸出，不再強制最少 80ms。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, gap):
        song = make_song(self.root)
        hold = note(100, 4000, note_type=2, end=4500)
        tail = note(101, 4520, pitch=62)            # 同一隻手、同一個鍵道，只差 20ms
        chart_json(os.path.join(song, 'Master', 'c.json'), extra=[hold, tail])
        plans, _ = H.plan_song_folder(song)
        plan = [p for p in plans if any(c.slot == 'real' for c in p.charts)][0]
        result = H.build_package(plan, os.path.join(self.root, 'out_%d' % gap), renderer=fake_renderer,
                                 hold_gap_ms=gap)
        self.assertEqual(result.error, '')
        with zipfile.ZipFile(result.zip_path) as archive:
            root = ET.fromstring(archive.read('real.xml'))
        holds = [(int(n.findtext('end_timing_msec')) - int(n.findtext('start_timing_msec')))
                 for n in root.iter('note') if n.findtext('note_type') == '2']
        return result, holds

    # 長押照畫面上的長度輸出，只有下一顆音符要求的間距會裁到它（以前這裡還會
    # 再乘 80%，見 build_pan_xml 的說明）
    def test_default_trims_the_tail(self):
        _result, holds = self.build(80)
        self.assertEqual(holds, [440])              # 4520 − 80 − 4000

    def test_off_leaves_holds_as_they_are(self):
        result, holds = self.build(H.NO_HOLD_PROCESSING)
        self.assertEqual(holds, [500])              # 原本的長度，沒有裁
        self.assertTrue(any('沒有處理長條尾端' in line for line in result.notes), result.notes)

    def test_setting_helper(self):
        class Fake(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)
        self.assertEqual(H.hold_gap_setting(Fake()), 80)
        self.assertEqual(H.hold_gap_setting(Fake(hiraeth_hold_gap_ms=120)), 120)
        self.assertEqual(H.hold_gap_setting(Fake(hiraeth_hold_gap_ms=20)), 80)      # 開著時仍然最少 80
        self.assertEqual(H.hold_gap_setting(Fake(hiraeth_process_hold_tails=False)), 0)

    def test_dialog_checkbox(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        m = NoteModel.create_new('t', 120.0, 10.0, 4)
        dlg = HiraethExportDialog(None, None, self.root, m)
        dlg.tail_check.setChecked(True)
        dlg.gap_spin.setValue(100)
        self.assertEqual(dlg.hold_gap_ms(), 100)
        dlg.tail_check.setChecked(False)
        self.assertEqual(dlg.hold_gap_ms(), 0)
        self.assertFalse(dlg.gap_spin.isEnabled())
        dlg.deleteLater()


if __name__ == '__main__':
    unittest.main()


class PackageTailTests(unittest.TestCase):
    """輸出的歌曲包結尾不能留一大段沒事做。

    譜面的 music_finish_time_msec 就是音訊長度，所以音訊尾巴多長，玩家打完最後
    一顆音符就要等多久（實測 Hiraeth 裡 LaVI 等 43 秒、Testify 40 秒）。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_trim_keeps_the_music_and_drops_the_dead_air(self):
        rate = 8000
        loud = b'\x00\x20\x00\x20' * int(rate * 10)          # 10 秒有聲音
        quiet = b'\0\0\0\0' * int(rate * 30)                 # 30 秒靜音
        pcm, cut = H.trim_package_tail(loud + quiet, rate, last_note_ms=8000, tail_ms=1500)
        self.assertAlmostEqual(len(pcm) / 4 / rate * 1000.0, 11500, delta=60)
        self.assertAlmostEqual(cut, 28500, delta=60)

    def test_the_last_note_is_never_cut_off(self):
        rate = 8000
        pcm, cut = H.trim_package_tail(b'\0\0\0\0' * int(rate * 40), rate,
                                       last_note_ms=30000, tail_ms=1500)
        self.assertAlmostEqual(len(pcm) / 4 / rate * 1000.0, 31500, delta=60)

    def test_a_tidy_package_is_left_alone(self):
        rate = 8000
        pcm, cut = H.trim_package_tail(b'\x00\x20\x00\x20' * int(rate * 10), rate,
                                       last_note_ms=9000, tail_ms=1500)
        self.assertEqual(cut, 0)

    def test_the_zip_audio_ends_with_the_chart(self):
        song = make_song(self.root)
        # 音訊 10 秒，譜面不到 3 秒 → 舊版會讓曲終停在 10 秒
        plans, _ = H.plan_song_folder(song)
        plan = plans[0]
        plan.mix_piano = False
        result = H.build_package(plan, os.path.join(self.root, 'out'), version=7,
                                 renderer=fake_renderer)
        self.assertEqual(result.error, '')
        folder = os.path.join(self.root, 'un')
        with zipfile.ZipFile(result.zip_path) as archive:
            archive.extractall(folder)
        with wave.open(os.path.join(folder, 'music.wav')) as w:
            audio_ms = w.getnframes() / w.getframerate() * 1000.0
        root = ET.parse(os.path.join(folder, 'real.xml')).getroot()
        last = max(int(n.findtext('end_timing_msec')) for n in root.iter('note'))
        self.assertLess(audio_ms - last, 3000, '打完最後一顆音符不該再等好幾秒')
        self.assertGreaterEqual(audio_ms, last, '音訊不能比譜面短')
        self.assertEqual(H.validate_package(folder), [])
