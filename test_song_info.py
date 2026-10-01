# -*- coding: utf-8 -*-
"""歌曲資訊的紀錄機制：填過一次就不用再填。

使用者的工作流程是「改一點譜 → 匯出 → 進遊戲聽 → 再改 → 再匯出」，每次匯出
都要重打曲名、作者、曲繪、難度、等級、分類。這裡驗證三層退路都會命中，而且
不該沿用的東西（別首歌的曲繪、別份譜的難度名稱）不會跟著跑。
"""

import json
import os
import tempfile
import unittest

from PyQt5.QtWidgets import QApplication

from qt_editor import song_info

_app = QApplication.instance() or QApplication([])


class SongInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = os.path.join(self.tmp, 'song_info.json')
        os.environ[song_info._STORE_ENV] = self.store
        song_info.reset_cache()
        # 同一首歌、兩份譜（兩個難度），共用一個音源
        self.audio = os.path.join(self.tmp, 'music.wav')
        self.chart_a = os.path.join(self.tmp, 'hard.json')
        self.chart_b = os.path.join(self.tmp, 'expert.json')
        self.cover = os.path.join(self.tmp, 'cover.png')
        for path in (self.audio, self.chart_a, self.chart_b, self.cover):
            open(path, 'wb').close()

    def tearDown(self):
        os.environ.pop(song_info._STORE_ENV, None)
        os.environ.pop('NOS_SETTINGS_WRITE', None)
        song_info.reset_cache()

    def filled(self, **extra):
        info = {'title': '極樂淨土', 'author': 'Eather', 'cover': self.cover,
                'category': 'J-POP', 'diff_name': 'HARD', 'diff_level': 12}
        info.update(extra)
        return info

    # ── 三層退路 ──────────────────────────────────────────────────
    def test_the_same_chart_comes_back_complete(self):
        """同一份譜再匯出：整個對話框都不用碰。"""
        song_info.remember(self.chart_a, self.audio, self.filled())
        got = song_info.recall(self.chart_a, self.audio)
        for key, value in self.filled().items():
            self.assertEqual(got.get(key), value, key)

    def test_another_difficulty_of_the_same_song_gets_the_shared_fields(self):
        """新開一個難度：曲名／作者／曲繪／分類已經在了。"""
        song_info.remember(self.chart_a, self.audio, self.filled())
        got = song_info.recall(self.chart_b, self.audio)
        self.assertEqual(got.get('title'), '極樂淨土')
        self.assertEqual(got.get('author'), 'Eather')
        self.assertEqual(got.get('cover'), self.cover)
        self.assertEqual(got.get('category'), 'J-POP')

    def test_another_difficulty_does_not_inherit_the_difficulty_fields(self):
        """難度名稱和定數是一份譜自己的，不能跟著跑到別的難度。"""
        song_info.remember(self.chart_a, self.audio, self.filled())
        got = song_info.recall(self.chart_b, self.audio)
        self.assertIsNone(got.get('diff_name'))
        self.assertIsNone(got.get('diff_level'))

    def test_a_brand_new_song_only_inherits_author_and_category(self):
        """全新的一首歌：不用再打自己的名字，但曲名／曲繪不能是別首的。"""
        song_info.remember(self.chart_a, self.audio, self.filled())
        other_chart = os.path.join(self.tmp, 'other.json')
        other_audio = os.path.join(self.tmp, 'other.wav')
        got = song_info.recall(other_chart, other_audio)
        self.assertEqual(got.get('author'), 'Eather')
        self.assertEqual(got.get('category'), 'J-POP')
        self.assertIsNone(got.get('title'))
        self.assertIsNone(got.get('cover'))

    def test_the_chart_entry_wins_over_the_song_entry(self):
        song_info.remember(self.chart_a, self.audio, self.filled())
        song_info.remember(self.chart_b, self.audio,
                           self.filled(title='別的名字', diff_name='EXPERT'))
        self.assertEqual(song_info.recall(self.chart_a, self.audio)['title'], '別的名字',
                         '同一首歌的共用欄位要跟著最後一次填的')
        self.assertEqual(song_info.recall(self.chart_a, self.audio)['diff_name'], 'HARD',
                         '這份譜自己的難度名稱不該被另一份蓋掉')

    def test_blank_values_never_overwrite_a_remembered_one(self):
        song_info.remember(self.chart_a, self.audio, self.filled())
        song_info.remember(self.chart_a, self.audio, {'author': '', 'title': ''})
        got = song_info.recall(self.chart_a, self.audio)
        self.assertEqual(got.get('author'), 'Eather')
        self.assertEqual(got.get('title'), '極樂淨土')

    def test_a_path_is_the_same_key_however_it_is_spelled(self):
        song_info.remember(self.chart_a, self.audio, self.filled())
        odd = os.path.join(self.tmp, '.', os.path.basename(self.chart_a)).upper()
        self.assertEqual(song_info.recall(odd, '').get('diff_name'), 'HARD')

    # ── 落盤 ──────────────────────────────────────────────────────
    def test_it_survives_a_restart(self):
        os.environ['NOS_SETTINGS_WRITE'] = '1'          # 測試預設不落盤，這裡要測落盤
        song_info.remember(self.chart_a, self.audio, self.filled())
        self.assertTrue(os.path.isfile(self.store))
        song_info.reset_cache()                          # 等於重開製譜器
        self.assertEqual(song_info.recall(self.chart_a, self.audio).get('title'), '極樂淨土')

    def test_running_the_tests_does_not_touch_the_users_file(self):
        song_info.remember(self.chart_a, self.audio, self.filled())
        self.assertFalse(os.path.isfile(self.store), '測試不該把紀錄寫進磁碟')

    def test_a_broken_store_does_not_block_the_export(self):
        with open(self.store, 'w', encoding='utf-8') as fh:
            fh.write('{ 這不是 JSON')
        song_info.reset_cache()
        self.assertEqual(song_info.recall(self.chart_a, self.audio), {})
        song_info.remember(self.chart_a, self.audio, self.filled())   # 不該炸
        self.assertEqual(song_info.recall(self.chart_a, self.audio).get('title'), '極樂淨土')

    def test_old_entries_are_dropped_instead_of_growing_forever(self):
        for i in range(song_info.MAX_ENTRIES + 20):
            song_info.remember(os.path.join(self.tmp, 'c%d.json' % i), '',
                               {'diff_name': 'D%d' % i})
        charts = song_info._load()['charts']
        self.assertEqual(len(charts), song_info.MAX_ENTRIES)
        self.assertEqual(song_info.recall(os.path.join(self.tmp, 'c0.json'), ''), {},
                         '最久沒用到的要被丟掉')
        last = os.path.join(self.tmp, 'c%d.json' % (song_info.MAX_ENTRIES + 19))
        self.assertEqual(song_info.recall(last, '').get('diff_name'),
                         'D%d' % (song_info.MAX_ENTRIES + 19))

    def test_the_stored_file_is_readable_json(self):
        os.environ['NOS_SETTINGS_WRITE'] = '1'
        song_info.remember(self.chart_a, self.audio, self.filled())
        with open(self.store, encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual(sorted(data), ['charts', 'last', 'songs', 'version'])


class ExportDialogRemembersTests(unittest.TestCase):
    """對話框真的會帶出來——紀錄模組對了但沒接上去的話這裡會抓到。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ[song_info._STORE_ENV] = os.path.join(self.tmp, 'song_info.json')
        song_info.reset_cache()
        self.audio = os.path.join(self.tmp, 'music.wav')
        self.chart = os.path.join(self.tmp, 'hard.json')
        self.cover = os.path.join(self.tmp, 'cover.png')
        for path in (self.audio, self.chart, self.cover):
            open(path, 'wb').close()

    def tearDown(self):
        os.environ.pop(song_info._STORE_ENV, None)
        song_info.reset_cache()

    def _dialog(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        return ExportSongDialog(None, wav_path=self.audio, chart_json_path=self.chart,
                                default_root=self.tmp)

    def test_the_second_time_the_form_is_already_filled_in(self):
        first = self._dialog()
        first._le_display.setText('極樂淨土')
        first._le_author.setText('Eather')
        first._le_diff_name.setText('HARD')
        first._spin_level.setValue(12)
        first._le_cover.setText(self.cover)
        first._cb_category.setCurrentText('J-POP')
        first._validate_and_accept()

        second = self._dialog()
        self.assertEqual(second.display_name(), '極樂淨土')
        self.assertEqual(second.author(), 'Eather')
        self.assertEqual(second.diff_name(), 'HARD')
        self.assertEqual(second.diff_level(), 12)
        self.assertEqual(second.cover_path(), self.cover)
        self.assertEqual(second.category(), 'J-POP')

    def test_a_cover_that_has_been_deleted_is_not_carried_over(self):
        first = self._dialog()
        first._le_display.setText('t')
        first._le_diff_name.setText('HARD')
        first._le_cover.setText(self.cover)
        first._validate_and_accept()
        os.remove(self.cover)
        self.assertEqual(self._dialog().cover_path(), '', '死路徑不該帶進來')


class RegisterDefaultsTests(unittest.TestCase):
    """第一次匯出（還沒有任何紀錄）：照譜面所在樂曲資料夾的 register.json 填。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ[song_info._STORE_ENV] = os.path.join(self.tmp, 'song_info.json')
        song_info.reset_cache()
        self.song_dir = os.path.join(self.tmp, 'Cocytus_623f5f94')
        os.makedirs(os.path.join(self.song_dir, 'Expert'))
        self.chart = os.path.join(self.song_dir, 'Expert', 'cocytus.json')
        open(self.chart, 'wb').close()
        self.cover = os.path.join(self.song_dir, 'cover.png')
        open(self.cover, 'wb').close()
        with open(os.path.join(self.song_dir, 'register.json'), 'w', encoding='utf-8') as fh:
            json.dump({'displayName': 'Cocytus', 'author': 'xi',
                       'difficulties': [{'difficultyName': 'Expert'}]}, fh)
        with open(os.path.join(self.tmp, 'library.json'), 'w', encoding='utf-8') as fh:
            json.dump({'songs': [{'folderName': 'Cocytus_623f5f94',
                                  'category': 'Arcaea'}]}, fh)

    def tearDown(self):
        os.environ.pop(song_info._STORE_ENV, None)
        song_info.reset_cache()

    def test_the_song_folder_fills_the_form_on_the_very_first_export(self):
        from qt_editor.export_song_dialog import ExportSongDialog
        dlg = ExportSongDialog(None, chart_json_path=self.chart, default_root=self.tmp)
        self.assertEqual(dlg.display_name(), 'Cocytus', '曲名要照 register 的 displayName')
        self.assertEqual(dlg.author(), 'xi')
        self.assertEqual(dlg.cover_path(), self.cover, '匯入的歌曲繪叫 cover.png')
        self.assertEqual(dlg.category(), 'Arcaea', '分類要照 library.json 現在的值')

    def test_a_remembered_value_beats_the_register(self):
        """使用者上次改過曲名，就以他改的為準。"""
        from qt_editor.export_song_dialog import ExportSongDialog
        song_info.remember(self.chart, '', {'title': '我改過的名字'})
        dlg = ExportSongDialog(None, chart_json_path=self.chart, default_root=self.tmp)
        self.assertEqual(dlg.display_name(), '我改過的名字')
        self.assertEqual(dlg.author(), 'xi', '沒改過的還是照 register 填')


class HiraethDialogRemembersTests(unittest.TestCase):
    """Hiraeth 歌曲包對話框的「只輸出目前開著的譜面」那一組欄位。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ[song_info._STORE_ENV] = os.path.join(self.tmp, 'song_info.json')
        song_info.reset_cache()
        self.audio = os.path.join(self.tmp, 'music.wav')
        self.chart = os.path.join(self.tmp, 'expert.json')
        self.cover = os.path.join(self.tmp, 'cover.png')
        for path in (self.audio, self.chart, self.cover):
            open(path, 'wb').close()

    def tearDown(self):
        os.environ.pop(song_info._STORE_ENV, None)
        song_info.reset_cache()

    def _dialog(self):
        from qt_editor.hiraeth_export_dialog import HiraethExportDialog
        from qt_editor.models import NoteModel
        model = NoteModel.create_new('t', 120.0, 8.0, 4)
        model.current_file = self.chart
        return HiraethExportDialog(None, '', self.tmp, model, audio_path=self.audio)

    def test_author_slot_level_and_cover_come_back(self):
        from qt_editor import hiraeth_export as H
        first = self._dialog()
        first.rb_chart.setChecked(True)
        first.artist_edit.setText('Eather')
        first.slot_combo.setCurrentIndex(H.SLOTS.index('hard'))
        first.level_spin.setValue(11)
        first.cover_edit.setText(self.cover)
        first._remember()

        second = self._dialog()
        self.assertEqual(second.artist_edit.text(), 'Eather')
        self.assertEqual(second.slot_combo.currentText(), 'hard')
        self.assertEqual(second.level_spin.value(), 11)
        self.assertEqual(second.cover_edit.text(), self.cover)

    def test_a_real_slot_level_is_not_clamped_by_the_other_range(self):
        """REAL 的等級是 1～3.5，其他難度是 1～15：帶回來時別被夾掉。"""
        from qt_editor import hiraeth_export as H
        first = self._dialog()
        first.rb_chart.setChecked(True)
        first.slot_combo.setCurrentIndex(H.SLOTS.index('real'))
        first.level_spin.setValue(3.0)
        first._remember()

        second = self._dialog()
        self.assertEqual(second.slot_combo.currentText(), 'real')
        self.assertEqual(second.level_spin.value(), 3.0)


if __name__ == '__main__':
    unittest.main()
