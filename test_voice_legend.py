# -*- coding: utf-8 -*-
"""多軌 MIDI 的樂器圖例：哪個顏色是哪個樂器。

樂器名稱取自 MIDI 的 track_name / instrument_name meta，沒有名稱才退回
program_change 的 GM 音色名。只有「還沒排譜的 MIDI」才畫 —— 那時候畫面是照
聲部上色的，圖例才對得上；排過譜之後一律紅藍分左右手，不需要圖例。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import mido
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from qt_editor.models import NoteModel, gm_program_name

_APP = QApplication.instance() or QApplication([])
NUL = chr(0)


def write_midi(path, tracks):
    """tracks: [(名稱, program, [音高...]), ...]"""
    mid = mido.MidiFile(ticks_per_beat=480)
    for index, (name, program, pitches) in enumerate(tracks):
        track = mido.MidiTrack()
        mid.tracks.append(track)
        if index == 0:
            track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
        if name is not None:
            track.append(mido.MetaMessage('track_name', name=name, time=0))
        if program is not None:
            track.append(mido.Message('program_change', program=program,
                                      channel=index, time=0))
        for pitch in pitches:
            track.append(mido.Message('note_on', note=pitch, velocity=90,
                                      channel=index, time=0))
            track.append(mido.Message('note_off', note=pitch, velocity=0,
                                      channel=index, time=240))
    mid.save(path)
    return path


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


class VoiceNameTests(unittest.TestCase):
    def test_track_name_is_used(self):
        path = write_midi(tmp('named.mid'),
                          [('Piano', 0, [60, 62]), ('Bass', 0, [36, 38])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        self.assertEqual(set(model.midi_voice_names.values()), {'Piano', 'Bass'})

    def test_program_change_fills_in_when_there_is_no_name(self):
        path = write_midi(tmp('unnamed.mid'),
                          [(None, 40, [60, 62]), (None, 42, [36, 38])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        self.assertEqual(set(model.midi_voice_names.values()),
                         {'Violin', 'Cello'})

    def test_default_piano_program_is_not_appended(self):
        """program 0 是幾乎所有 MIDI 的預設值，接在名稱後面只是雜訊。

        實測 Designant 二十軌全是 program 0，接上去會讓「Bass」變成
        「Bass（Acoustic Grand Piano）」。
        """
        path = write_midi(tmp('default.mid'),
                          [('Bass', 0, [36, 38]), ('Lead', 0, [72, 74])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        for name in model.midi_voice_names.values():
            self.assertNotIn('Acoustic Grand Piano', name)

    def test_name_and_program_are_combined_when_they_differ(self):
        path = write_midi(tmp('both.mid'),
                          [('Melody', 40, [60, 62]), ('Low', 42, [36, 38])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        self.assertIn('Melody（Violin）', model.midi_voice_names.values())

    def test_control_characters_are_stripped(self):
        """meta 字串常常補 NUL，直接拿去畫會出現方塊字。"""
        path = write_midi(tmp('nul.mid'),
                          [('Piano' + NUL, 0, [60]), ('Bass' + NUL, 0, [36])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        for name in model.midi_voice_names.values():
            self.assertNotIn(NUL, name)

    def test_utf8_track_name_is_decoded(self):
        """mido 一律用 latin-1 解 meta 字串，實際上幾乎都是 UTF-8。

        latent-kingdom.mid 的軌名是「钢琴」，不修的話會顯示成 'é¢ç´'。
        """
        path = tmp('utf8.mid')
        mid = mido.MidiFile(ticks_per_beat=480)
        for index, raw in enumerate(('钢琴'.encode('utf-8').decode('latin-1'),
                                     'Piano')):
            track = mido.MidiTrack(); mid.tracks.append(track)
            if index == 0:
                track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
            track.append(mido.MetaMessage('track_name', name=raw, time=0))
            track.append(mido.Message('note_on', note=60 + index * 12,
                                      velocity=90, channel=index, time=0))
            track.append(mido.Message('note_off', note=60 + index * 12,
                                      velocity=0, channel=index, time=240))
        mid.save(path)
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        self.assertIn('钢琴', model.midi_voice_names.values())

    def test_plain_ascii_names_survive_the_decode(self):
        """純英文的名稱不能被那個轉碼改壞。"""
        path = write_midi(tmp('ascii.mid'),
                          [('Piano', 0, [60]), ('Bass', 0, [36])])
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        self.assertEqual(set(model.midi_voice_names.values()), {'Piano', 'Bass'})

    def test_gm_table_edges(self):
        self.assertEqual(gm_program_name(0), 'Acoustic Grand Piano')
        self.assertEqual(gm_program_name(127), 'Gunshot')
        self.assertEqual(gm_program_name(None), '')
        self.assertEqual(gm_program_name(999), '')


class VoiceLegendDrawTests(unittest.TestCase):
    def _view_for(self, path):
        """用裸的 ChartView，不要經過 MainWindow。

        MainWindow 會在重繪時把自己那份 model 套回格子裡，測試設上去的
        model 會被換掉——結果就是「直接呼叫 _voice_legend_entries() 回空
        list，重繪卻畫出圖例」這種對不起來的現象。圖例是 ChartView 的事，
        直接測它。
        """
        from qt_editor.chart_view import ChartView

        view = ChartView()
        model = NoteModel(); model.load_midi(path, auto_arrange=False)
        view.model = model
        model.rebuild_display_cache()
        view.resize(1280, 768)
        view._update_unit_bounds()
        return view

    def _render(self, view):
        # widget 還沒顯示過時 width()/height() 是預設的 100x30，要先 render
        # 一次讓版面把尺寸定下來，否則 QImage 會建成 100x30、圖例畫在
        # x=1192 完全落在圖外（查這個花了不少時間）。
        settle = QImage(8, 8, QImage.Format_ARGB32)
        view.render(settle)
        image = QImage(view.width(), view.height(), QImage.Format_ARGB32)
        image.fill(QColor(0, 0, 0))
        view.render(image)
        return image

    def test_legend_is_drawn_inside_the_widget(self):
        """尺寸要照 widget 實際大小算，不能自己假設。

        測試時 resize() 會被版面覆寫（實測設 1000x720、實際是 1276x765），
        照假設的寬度去找圖例會找不到——那是測試錯，不是功能壞。
        """
        path = write_midi(tmp('four.mid'), [
            ('Piano', 0, [60, 62]), ('Bass', 0, [36, 38]),
            ('Lead', 0, [72, 74]), ('Pad', 0, [48, 50])])
        view = self._view_for(path)
        self._render(view)
        rect = getattr(view, '_voice_legend_rect', None)
        self.assertIsNotNone(rect, '圖例沒有畫出來')
        x, y, w, h = rect
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertGreaterEqual(x, 0, '圖例跑到畫面左邊外面')
        self.assertLessEqual(x + w, view.width(), '圖例跑到畫面右邊外面')
        self.assertLessEqual(y + h, view.height(), '圖例超出畫面下緣')

    def test_legend_actually_paints_pixels(self):
        path = write_midi(tmp('paint.mid'),
                          [('Piano', 0, [60, 62]), ('Bass', 0, [36, 38])])
        view = self._view_for(path)
        image = self._render(view)
        rect = getattr(view, '_voice_legend_rect', None)
        self.assertIsNotNone(rect)
        x, y, w, h = rect
        hits = 0
        for py in range(y + 2, min(y + h - 2, image.height())):
            for px in range(x + 2, min(x + w - 2, image.width())):
                colour = image.pixelColor(px, py)
                if abs(colour.red() - 24) <= 6 and abs(colour.blue() - 28) <= 6:
                    hits += 1
        self.assertGreater(hits, 100, '圖例區域看不到底色，等於沒畫上去')

    def test_single_voice_gets_no_legend(self):
        path = write_midi(tmp('one.mid'), [('Piano', 0, [60, 62, 64])])
        view = self._view_for(path)
        self._render(view)
        self.assertIsNone(getattr(view, '_voice_legend_rect', None))

    def test_arranged_chart_gets_no_legend(self):
        """排過譜之後一律以左右手上色，圖例對不上就不該畫。"""
        path = write_midi(tmp('arr.mid'),
                          [('Piano', 0, [60, 62]), ('Bass', 0, [36, 38])])
        view = self._view_for(path)
        view.model.midi_unarranged = False
        # 判斷邏輯本身（不依賴有沒有真的重繪）
        self.assertEqual(view._voice_legend_entries(), [])
        # 重繪之後也不能留下框。先清掉舊值，這樣「沒被重繪」和「決定不畫」
        # 才分得出來——ChartView 在分割模式下是共用的，前一個測試的值會留著。
        view._voice_legend_rect = None
        self._render(view)
        self.assertIsNone(getattr(view, '_voice_legend_rect', None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
