# -*- coding: utf-8 -*-
"""生成難度寫回樂曲資料夾：找得到資料夾、挑對來源、不覆蓋手寫的譜。"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_editor import song_folder as SF
from qt_editor.difficulty import TARGETS, generate
from qt_editor.models import NoteModel
from test_difficulty import chart, dense


def register(entries):
    return {'songName': '測試曲', 'difficulties': entries}


def entry(label, level, song, stem):
    return {'difficultyName': label, 'difficultyLevel': level,
            'chartFileName': 'songs/%s/%s/%s' % (song, label, stem)}


class SongFolderTests(unittest.TestCase):
    """`register.json` 是樂曲資料夾的入口；一切都從它往外推。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.song = 'TestSong'
        self.song_dir = os.path.join(self.root, self.song)
        os.makedirs(os.path.join(self.song_dir, 'Real'))
        self.stem = 'chart'
        self.real = os.path.join(self.song_dir, 'Real', self.stem + '.json')
        model = chart(dense(groups=80, step=120, chord=3))
        model.save_json(self.real)
        self.write_register([entry('Real', 14, self.song, self.stem)])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_register(self, entries):
        path = os.path.join(self.song_dir, 'register.json')
        with io.open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(json.dumps(register(entries), ensure_ascii=False,
                                    indent=2))

    def read_register(self):
        with io.open(os.path.join(self.song_dir, 'register.json'),
                     encoding='utf-8') as handle:
            return json.loads(handle.read())

    # ── 找資料夾 ─────────────────────────────────────────────
    def test_it_finds_the_song_folder_from_a_chart_deep_inside(self):
        deep = os.path.join(self.song_dir, 'Normal', 'source', 'x.xml')
        os.makedirs(os.path.dirname(deep))
        io.open(deep, 'w', encoding='utf-8').write('<a/>')
        self.assertEqual(SF.find_song_folder(deep), self.song_dir)

    def test_a_chart_outside_any_song_folder_is_refused(self):
        loose = os.path.join(self.root, 'loose.xml')
        io.open(loose, 'w', encoding='utf-8').write('<a/>')
        with self.assertRaises(SF.SongFolderError):
            SF.plan(loose)

    # ── 挑來源與目標 ─────────────────────────────────────────
    def test_the_open_chart_is_the_source(self):
        plan = SF.plan(self.real)
        self.assertEqual(plan.source_label, 'Real')
        self.assertEqual(plan.source_level, 14)
        self.assertEqual(plan.wanted, ['normal', 'hard', 'extreme'])

    def test_a_handwritten_difficulty_is_never_overwritten(self):
        """沒有 `source/` 那份 XML 就代表是人寫的，不能蓋掉。"""
        os.makedirs(os.path.join(self.song_dir, 'Hard'))
        self.write_register([entry('Real', 14, self.song, self.stem),
                             entry('Hard', 8, self.song, self.stem)])
        plan = SF.plan(self.real)
        self.assertNotIn('hard', plan.wanted)
        self.assertTrue(any(key == 'hard' and '手寫' in why
                            for key, why in plan.skipped),
                        plan.skipped)

    def _mark_generated(self, label: str) -> None:
        mark = os.path.join(self.song_dir, label, 'source', self.stem + '.xml')
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        with io.open(mark, 'w', encoding='utf-8') as fh:
            fh.write('<a/>')

    def test_a_generated_difficulty_is_left_alone_by_default(self):
        """已經在那裡的難度不算缺。

        舊的預設是每次都重生自己生過的那些，於是補過一輪之後再掃，整個曲庫
        還是一片「要補」—— 那個畫面說的不是事實。
        """
        self._mark_generated('Hard')
        self.write_register([entry('Real', 14, self.song, self.stem),
                             entry('Hard', 8, self.song, self.stem)])
        plan = SF.plan(self.real)
        self.assertNotIn('hard', plan.wanted)
        self.assertIn('hard', [key for key, _why in plan.skipped])

    def test_a_generated_difficulty_is_regenerated_when_asked(self):
        """來源譜改過、或演算法換了，才需要重做 —— 那時要自己勾。"""
        self._mark_generated('Hard')
        self.write_register([entry('Real', 14, self.song, self.stem),
                             entry('Hard', 8, self.song, self.stem)])
        self.assertIn('hard', SF.plan(self.real, regenerate=True).wanted)

    def test_a_handwritten_difficulty_is_never_regenerated(self):
        """手寫的即使勾了重生成也不覆蓋 —— 那是使用者的工作。"""
        self.write_register([entry('Real', 14, self.song, self.stem),
                             entry('Hard', 8, self.song, self.stem)])
        self.assertNotIn('hard', SF.plan(self.real, regenerate=True).wanted)

    def test_a_handwritten_real_with_its_own_source_xml_is_still_the_source(self):
        """`source/` 也是使用者存 XML 的習慣，不能拿它判定 Real 是生成的。

        實測曲庫裡有 13 個手寫的 Real 帶著 source/xml。少了「標籤要是我們會
        生的那三個」這道判斷，整首會被判成「只剩生成的難度」而補不了。
        """
        mark = os.path.join(self.song_dir, 'Real', 'source', self.stem + '.xml')
        os.makedirs(os.path.dirname(mark))
        io.open(mark, 'w', encoding='utf-8').write('<a/>')
        entries = [entry('Real', 14, self.song, self.stem)]
        self.write_register(entries)
        self.assertFalse(SF.is_generated(self.song_dir, entries[0]))
        plan = SF.plan_song(self.song_dir)
        self.assertEqual(plan.source_label, 'Real')
        self.assertEqual(plan.wanted, ['normal', 'hard', 'extreme'])

    def test_a_source_too_easy_generates_nothing_harder_than_itself(self):
        self.write_register([entry('Real', 3, self.song, self.stem)])
        plan = SF.plan(self.real)
        self.assertNotIn('extreme', plan.wanted)

    def test_a_source_without_a_level_is_refused(self):
        self.write_register([entry('Real', 0, self.song, self.stem)])
        with self.assertRaises(SF.SongFolderError):
            SF.plan(self.real)

    # ── 自己指定資料夾 ───────────────────────────────────────
    def test_a_chart_outside_can_target_a_folder_you_pick(self):
        """剛轉好、還沒歸檔的譜面要能指定寫進哪一首。"""
        loose = os.path.join(self.root, 'freshly_converted.json')
        shutil.copy2(self.real, loose)
        with self.assertRaises(SF.SongFolderError):
            SF.plan(loose)                      # 自動偵測找不到
        plan = SF.plan(loose, song_dir=self.song_dir, source_level=14)
        self.assertEqual(plan.song_dir, self.song_dir)
        self.assertEqual(plan.source_level, 14)
        self.assertEqual(plan.chart_name, 'freshly_converted')
        self.assertTrue(plan.wanted)

    def test_a_picked_folder_without_a_register_is_refused(self):
        empty = os.path.join(self.root, 'NotASong')
        os.makedirs(empty)
        with self.assertRaises(SF.SongFolderError):
            SF.plan(self.real, song_dir=empty)

    def test_the_given_level_overrides_the_register(self):
        """等級是外面給的時候，要照它推低難度，不是照 register 裡那個。"""
        low = SF.plan(self.real, source_level=6)
        self.assertEqual(low.source_level, 6)
        self.assertNotIn('extreme', low.wanted)   # Lv.6 生不出更簡單的 Expert
        high = SF.plan(self.real, source_level=18)
        self.assertIn('extreme', high.wanted)

    def test_the_source_difficulty_is_never_regenerated_over_itself(self):
        """來源自己那一格要跳過，免得把來源蓋掉。"""
        self.write_register([entry('Expert', 12, self.song, self.stem)])
        plan = SF.plan(self.real)
        self.assertNotIn('extreme', plan.wanted)
        self.assertTrue(any(key == 'extreme' and '來源' in why
                            for key, why in plan.skipped), plan.skipped)

    # ── 寫出去 ───────────────────────────────────────────────
    def test_it_writes_the_layout_the_game_reads(self):
        plan = SF.plan(self.real)
        records = []
        for key in ('normal',):
            copy = NoteModel()
            copy.load_json(self.real)
            result = generate(copy, key)
            records.append(SF.write_difficulty(plan, key, copy, result))
        SF.commit(plan, records)

        label = TARGETS['normal'].label
        chart = os.path.join(self.song_dir, label, self.stem + '.json')
        source = os.path.join(self.song_dir, label, 'source', self.stem + '.xml')
        self.assertTrue(os.path.exists(chart), '遊戲讀的 json 沒寫出來')
        self.assertTrue(os.path.exists(source), 'source/xml 沒留下來')

        entries = self.read_register()['difficulties']
        names = [e['difficultyName'] for e in entries]
        self.assertEqual(names[0], label, '新難度要排在既有難度前面')
        self.assertIn('Real', names, '既有難度不能被擠掉')
        fresh = entries[0]
        self.assertLess(fresh['difficultyLevel'], 14, '低難度的等級要低於來源')
        self.assertEqual(fresh['chartFileName'],
                         'songs/%s/%s/%s' % (self.song, label, self.stem))

    def test_committing_twice_does_not_duplicate_the_entry(self):
        for _ in range(2):
            plan = SF.plan(self.real)
            copy = NoteModel()
            copy.load_json(self.real)
            result = generate(copy, 'normal')
            SF.commit(plan, [SF.write_difficulty(plan, 'normal', copy, result)])
        names = [e['difficultyName'] for e in self.read_register()['difficulties']]
        self.assertEqual(names.count(TARGETS['normal'].label), 1, names)


if __name__ == '__main__':
    unittest.main()
