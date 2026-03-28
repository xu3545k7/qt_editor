"""
i18n.py
=======
國際化翻譯模組。
支援語言：zh_tw（繁體中文）、zh_cn（簡體中文）、en（English）。

用法
----
from .i18n import t
label = t('action_open')            # 直接取字串
label = t('dlg_bpm_label', 120.0)   # 帶格式化參數
"""

from __future__ import annotations

_LANG: str = 'zh_tw'

# ---------------------------------------------------------------------------
# 字串表
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {

    # ──────────────────────────────────────────────────────────────────────
    # 繁體中文（預設）
    # ──────────────────────────────────────────────────────────────────────
    'zh_tw': {
        # 選單標題
        'menu_file':            '檔案(&F)',
        'menu_edit':            '編輯(&E)',
        'menu_audio':           '音訊(&U)',
        'menu_tools':           '工具(&T)',
        'menu_view':            '檢視(&V)',
        'menu_settings':        '設定(&G)',

        # 檔案選單
        'action_new_chart':     '新增譜面(&N)…',
        'action_open':          '開啟(&O)…',
        'action_import_midi_sub':   '匯入 MIDI 音軌',
        'action_open_midi_right':   '開啟右手 MIDI…',
        'action_open_midi_left':    '開啟左手 MIDI…',
        'dlg_midi_right_done':      '右手音符已從 MIDI 匯入（{0} 個音符）。',
        'dlg_midi_left_done':       '左手音符已從 MIDI 匯入（{0} 個音符）。',
        'dlg_midi_no_notes':        'MIDI 中找不到對應手的音符。',
        'action_save':          '儲存(&S)',
        'action_save_as':       '另存新檔…',
        'action_save_json':     '儲存為 JSON…',
        'action_quit':          '離開(&Q)',

        # 編輯選單
        'action_undo':          '復原',
        'action_select_all':    '全選',
        'action_deselect':      '取消選取',
        'action_delete':        '刪除選取  Del',
        'action_duplicate':     '就地複製  C',
        'action_copy':          '複製',
        'action_paste':         '貼上',
        'action_width2':        '設定寬度 = 2',
        'action_width3':        '設定寬度 = 3',
        'action_type_tap':      '類型：Tap        T',
        'action_type_soft':     '類型：Soft',
        'action_type_long':     '類型：Long       H',
        'action_type_staccato': '類型：Staccato   K',
        'action_right_hand':    '右手  R',
        'action_left_hand':     '左手  L',
        'action_shift_pitch':   'Shift Pitch…',

        # 音訊選單
        'action_load_wav':      '載入 WAV…',
        'action_hit_sound':     '🥁 含打擊聲（過音符時響）',
        'action_play_window':   '▶ 播放區段           P',
        'action_play_sel':      '▶ 播放選取           Shift+P',
        'action_play_full':     '▶ 播放整首           Ctrl+P',
        'action_pause':         '⏸ 暫停',
        'action_resume':        '⏵ 繼續',
        'action_stop':          '■ 停止          S',
        'action_restart':       '↺ 重新播放',

        # 工具選單
        'action_auto_sort':         '範圍內自動排序     Shift+A',
        'action_resort_all':        '全譜重整排序',
        'action_resolve_overlaps':  '整理重疊 Resolve Overlaps…',
        'action_adjust_bpm':        '調整 BPM…',
        'action_adjust_beats':      '調整小節拍數…',
        'action_adjust_offset':     '調整起始偏移…',
        'action_add_measure':       '新增小節…',
        'action_delete_measure':    '刪除小節…',

        # 檢視選單
        'action_zoom_in':       '放大 (+)',
        'action_zoom_out':      '縮小 (-)',
        'action_scroll_invert': '切換捲動反向',

        # 設定選單
        'action_preferences':   '偏好設定…',

        # 工具列
        'tb_open':          '開啟',
        'tb_save':          '儲存',
        'tb_new_chart':     '新增譜面',
        'tb_undo':          '復原',
        'tb_auto_sort':     '範圍內自動排序',
        'tb_zoom_out':      '縮小 −',
        'tb_zoom_in':       '放大 ＋',
        'tb_play':          '▶ 播放區段',
        'tb_play_full':     '▶ 播放整首',
        'tb_stop':          '■ 停止',
        'tb_pause':         '⏸ 暫停',
        'tb_resume':        '⏵ 繼續',
        'tb_hit_sound':     '🥁 打擊聲',
        'tb_hit_sound_tip': '播放時經過音符發出打擊聲',
        'tb_music_vol':     '音樂音量',
        'tb_music2_vol':    '第二音源音量',
        'tb_hit_vol':       '打擊音量',
        'tb_preview':        '🎬 預覽譜面',
        'tb_preview_tip':    '以圖片方式預覽整份譜面',
        'tb_time_uniform':       '⏱ 小節均分模式',
        'tb_time_uniform_tip':   '切換時間均分 / 小節均分檢視方式',
        'tb_time_uniform_on':    '⏱ 時間均分模式',
        'tb_time_uniform_off':   '⏱ 小節均分模式',
        'tb_note_input':     '✒ 放置模式',
        'tb_note_input_tip': '點擊譜面即可在對齊的2位置新增音符',
        'tb_add_measure':    '新增小節',
        'tb_delete_measure': '刪除小節',
        'tb_note_ dur_label': '音符時値：',
        'tb_note_hand_r':    '右手',
        'tb_note_hand_l':    '左手',        'tb_main':          '主工具列',

        # 狀態列
        'status_open_file':     '請開啟檔案',
        'status_sel':           '已選取：{0}',        'status_audio_none':    '音訊：未載入',
        'status_audio_loaded':  '音訊：{0}',
        'status_hint':          ('↑↓捲動  ←→鍵位  Ctrl+Z復原  Del刪除  '
                                 'H=長 T=Tap K=顫  L=左 R=右  '
                                 'C=複製  P=播放區段  Ctrl+P=播放整首  S=停止  Shift+A=範圍內自動排序'),

        # ChartView 狀態列
        'status_window':        '視窗 {0}ms..{1}ms  ({2:.2f}..{3:.2f} beat)  ΔW={4:.2f}  已選取:{5}  BPM:{6:.1f}',

        # 主視窗 title
        'wnd_title':            'NOS chart maker',
        'wnd_no_file':          '（未開啟）',

        # 對話框 — 檔案
        'dlg_open_title':       '開啟譜面',
        'dlg_file_filter':      'All supported (*.xml *.json *.mid *.midi);;XML (*.xml);;JSON (*.json);;MIDI (*.mid *.midi)',
        'dlg_warn':             '警告',
        'dlg_midi_no_conv':     '找不到 midi_to_xml_converter，無法轉換 MIDI。',
        'dlg_load_fail_title':  '載入失敗',
        'dlg_load_fail_msg':    '無法載入檔案：\n{0}',
        'dlg_save_as_title':    '另存新檔',
        'dlg_save_json_title':  '儲存為 JSON',
        'dlg_save_ok_title':    '儲存成功',
        'dlg_save_ok_msg':      '儲存成功！\n路徑：{0}',
        'dlg_save_fail_title':  '儲存失敗',
        'dlg_save_fail_msg':    '無法儲存：\n{0}',
        'dlg_load_wav_title':   '載入 WAV',
        'dlg_wav_filter':       'WAV 音訊 (*.wav);;All files (*)',
        'dlg_wav_fail_msg':     '無法載入音訊：{0}',
        'dlg_no_audio_title':   '未載入音訊',
        'dlg_no_audio_msg':     '還沒有載入 WAV 音訊檔案。\n是否現在要載入？',

        # 對話框 — 工具
        'dlg_shift_pitch_title':    'Shift Pitch',
        'dlg_shift_pitch_label':    '調整 pitch 偏移量（正/負整數）：',
        'dlg_resolve_title':        '整理重疊',
        'dlg_resolve_label':        '最小間距（ms）：',
        'dlg_no_overlaps_title':    '提示',
        'dlg_no_overlaps_msg':      '沒有需要整理的重疊。',
        'dlg_bpm_title':            '調整 BPM',
        'dlg_bpm_label':            '目前 BPM = {0:.2f}\n新 BPM：',
        'dlg_beats_title':          '調整小節拍數',
        'dlg_beats_label':          '目前 beats_per_bar = {0}\n新值：',
        'dlg_offset_title':         '調整起始偏移',
        'dlg_offset_label':         '所有音符時間偏移量（ms，正/負整數）：',

        # 對話框 — 新增譜面
        'dlg_new_chart_no_file_needed':  '',
        'dlg_add_measure_title':  '新增小節',
        'dlg_add_measure_label':  '新小節的 BPM（目前 {0:.1f}）：',
        'dlg_delete_measure_title': '刪除小節',
        'dlg_delete_measure_msg':   '確定要刪除第 {0} 小節（{1}ms ~ {2}ms）？\n\n小節內有 {3} 個音符，刪除後將一併移除，且後續小節會往前平移。',
        'dlg_delete_measure_empty': '確定要刪除第 {0} 小節（{1}ms ~ {2}ms）？\n\n該小節內沒有音符。',
        'dlg_delete_measure_no_data': '找不到小節資料，請先確保譜面包含 beat_data。',
        'action_set_measure_bpm':       '修改小節 BPM…',
        'tb_set_measure_bpm':           '⟳ BPM',
        'dlg_set_measure_bpm_title':    '修改小節 BPM',
        'dlg_set_measure_bpm_label':    '小節 {0}（目前 {1:.1f} BPM）→ 新 BPM：',
        'dlg_measure_done':           '小節操作完成。',

        # 對話框 — 關閉
        'dlg_unsaved_title':    '未儲存的變更',
        'dlg_unsaved_msg':      '有未儲存的變更，確定要離開？',

        # 屬性對話框
        'prop_title':       '編輯音符 #{0}',
        'prop_err_title':   '輸入錯誤',
        'prop_err_msg':     '欄位格式不正確：\n{0}',
        'prop_hand_hint':   'hand: 0=右手  1=左手',

        # 播放偏移對話框
        'dlg_pb_offset_title':      '播放偏移設定',
        'dlg_pb_offset_dir':        '方向：',
        'dlg_pb_offset_ms':         '時間：',
        'dlg_pb_offset_beat':       '拍數：',
        'dlg_pb_offset_beat_unit':  ' 拍',
        'dlg_pb_offset_advance':    '提前 ▲',
        'dlg_pb_offset_delay':      '延後 ▼',
        'tb_offset':                '⏱ 偏移',
        'tb_offset_tip':            '設定播放提前/延後偏移',
        'status_offset':            '偏移：{0}ms',
        'status_offset_none':       '偏移：無',

        # 匯出完整曲目對話框
        'action_export_song':       '匯出完整曲目…',
        'dlg_export_title':         '匯出完整曲目',
        'dlg_export_append':        '追加難度至現有曲目',
        'dlg_export_display_name':  '曲名：',
        'dlg_export_author':        '作者：',
        'dlg_export_diff_name':     '難度名稱：',
        'dlg_export_diff_level':    '定數：',
        'dlg_export_cover':         '曲繪：',
        'dlg_export_browse':        '瀏覽…',
        'dlg_export_audio':         '音源：',
        'dlg_export_audio_hint_adv':    '將依據提前 {0}ms 裁切音源',
        'dlg_export_audio_hint_delay':  '將依據延後 {0}ms 裁切音源',
        'dlg_export_audio_hint_none':   '無偏移，直接複製音源',
        'dlg_export_cover_pick':    '選擇曲繪圖片',
        'dlg_export_err_no_name':   '請輸入曲名。',
        'dlg_export_err_no_diff':   '請輸入難度名稱。',
        'dlg_export_ok_title':      '匯出成功',
        'dlg_export_ok_msg':        '曲目已匯出至：\n{0}',
        'dlg_export_fail_title':    '匯出失敗',
        'dlg_export_fail_msg':      '匯出過程發生錯誤：\n{0}',
        'dlg_export_no_audio':      '尚未載入音訊，無法匯出音源。\n請先載入 WAV。',
        'dlg_export_no_chart':      '目前沒有譜面資料可匯出。',
        'dlg_export_browse_folder': '選擇曲目資料夾…',
        'dlg_export_pick_folder':   '選擇曲目或母資料夾',
        'dlg_export_select_song':   '選擇曲目',
        'dlg_export_found_songs':   '在所選資料夾中找到 {0} 個曲目，請選擇：',
        'dlg_export_no_songs':      '所選資料夾中找不到任何有效曲目（需包含 register.json）。',
        'dlg_export_cover_auto':    '（已從註冊表自動帶入）',

        # 設定對話框
        'settings_title':       '偏好設定',
        'settings_language':    '語言',
        'settings_scroll_dir':  '滾輪方向',
        'settings_normal':      '正向',
        'settings_reversed':    '反向',
        'settings_restart_note': '切換語言後將自動重新啟動程式。',
    },

    # ──────────────────────────────────────────────────────────────────────
    # 簡體中文
    # ──────────────────────────────────────────────────────────────────────
    'zh_cn': {
        'menu_file':            '文件(&F)',
        'menu_edit':            '编辑(&E)',
        'menu_audio':           '音频(&U)',
        'menu_tools':           '工具(&T)',
        'menu_view':            '视图(&V)',
        'menu_settings':        '设置(&G)',

        'action_open':          '打开(&O)…',
        'action_new_chart':     '新建谱面(&N)…',
        'action_import_midi_sub':   '导入 MIDI 音轨',
        'action_open_midi_right':   '打开右手 MIDI…',
        'action_open_midi_left':    '打开左手 MIDI…',
        'dlg_midi_right_done':      '右手音符已从 MIDI 导入（{0} 个音符）。',
        'dlg_midi_left_done':       '左手音符已从 MIDI 导入（{0} 个音符）。',
        'dlg_midi_no_notes':        'MIDI 中找不到对应手的音符。',
        'action_save':          '保存(&S)',
        'action_save_as':       '另存为…',
        'action_save_json':     '保存为 JSON…',
        'action_quit':          '退出(&Q)',

        'action_undo':          '撤销',
        'action_select_all':    '全选',
        'action_deselect':      '取消选择',
        'action_delete':        '删除选中  Del',
        'action_duplicate':     '原地复制  C',
        'action_copy':          '复制',
        'action_paste':         '粘贴',
        'action_width2':        '设置宽度 = 2',
        'action_width3':        '设置宽度 = 3',
        'action_type_tap':      '类型：Tap        T',
        'action_type_soft':     '类型：Soft',
        'action_type_long':     '类型：Long       H',
        'action_type_staccato': '类型：Staccato   K',
        'action_right_hand':    '右手  R',
        'action_left_hand':     '左手  L',
        'action_shift_pitch':   'Shift Pitch…',

        'action_load_wav':      '加载 WAV…',
        'action_hit_sound':     '🥁 含打击声（过音符时响）',
        'action_play_window':   '▶ 播放区段           P',
        'action_play_sel':      '▶ 播放选中           Shift+P',
        'action_play_full':     '▶ 播放整首           Ctrl+P',
        'action_pause':         '⏸ 暂停',
        'action_resume':        '⏵ 继续',
        'action_stop':          '■ 停止          S',
        'action_restart':       '↺ 重新播放',

        'action_auto_sort':         '范围内自动排序     Shift+A',
        'action_resort_all':        '全谱重整排序',
        'action_resolve_overlaps':  '整理重叠…',
        'action_adjust_bpm':        '调整 BPM…',
        'action_adjust_beats':      '调整小节拍数…',
        'action_adjust_offset':     '调整起始偏移…',
        'action_add_measure':       '新增小节…',
        'action_delete_measure':    '删除小节…',

        'action_zoom_in':       '放大 (+)',
        'action_zoom_out':      '缩小 (-)',
        'action_scroll_invert': '切换滚动方向',

        'action_preferences':   '设置…',

        'tb_open':          '打开',
        'tb_save':          '保存',
        'tb_new_chart':     '新建谱面',
        'tb_undo':          '撤销',
        'tb_auto_sort':     '范围内自动排序',
        'tb_zoom_out':      '缩小 −',
        'tb_zoom_in':       '放大 ＋',
        'tb_play':          '▶ 播放区段',
        'tb_play_full':     '▶ 播放整首',
        'tb_stop':          '■ 停止',
        'tb_pause':         '⏸ 暂停',
        'tb_resume':        '⏵ 继续',
        'tb_hit_sound':     '🥁 打击声',
        'tb_hit_sound_tip': '播放时经过音符发出打击声',
        'tb_music_vol':     '音乐音量',
        'tb_music2_vol':    '第二音源音量',
        'tb_hit_vol':       '打击音量',
        'tb_preview':        '🎬 预览谱面',
        'tb_preview_tip':    '以图片方式预览整份谱面',
        'tb_time_uniform':       '⏱ 小节均分模式',
        'tb_time_uniform_tip':   '切换时间均分 / 小节均分检视方式',
        'tb_time_uniform_on':    '⏱ 时间均分模式',
        'tb_time_uniform_off':   '⏱ 小节均分模式',
        'tb_note_input':     '✒ 放置模式',
        'tb_note_input_tip': '点击谱面即可在对齐的位置新增音符',
        'tb_add_measure':    '新增小节',
        'tb_delete_measure': '删除小节',
        'tb_note_ dur_label': '音符时值：',
        'tb_note_hand_r':    '右手',
        'tb_note_hand_l':    '左手',
        'tb_main':          '主工具栏',

        'status_open_file':     '请打开文件',
        'status_sel':           '选中：{0}',
        'status_audio_none':    '音频：未加载',
        'status_audio_loaded':  '音频：{0}',
        'status_hint':          ('↑↓滚动  ←→键位  Ctrl+Z撤销  Del删除  '
                                 'H=长 T=Tap K=颤  L=左 R=右  '
                                 'C=复制  P=播放区段  Ctrl+P=播放整首  S=停止  Shift+A=范围内自动排序'),

        'status_window':        '视窗 {0}ms..{1}ms  ({2:.2f}..{3:.2f} beat)  ΔW={4:.2f}  已选中:{5}  BPM:{6:.1f}',

        'wnd_title':            'NOS chart maker',
        'wnd_no_file':          '（未打开）',

        'dlg_open_title':       '打开谱面',
        'dlg_file_filter':      'All supported (*.xml *.json *.mid *.midi);;XML (*.xml);;JSON (*.json);;MIDI (*.mid *.midi)',
        'dlg_warn':             '警告',
        'dlg_midi_no_conv':     '找不到 midi_to_xml_converter，无法转换 MIDI。',
        'dlg_load_fail_title':  '加载失败',
        'dlg_load_fail_msg':    '无法加载文件：\n{0}',
        'dlg_save_as_title':    '另存为',
        'dlg_save_json_title':  '保存为 JSON',
        'dlg_save_ok_title':    '保存成功',
        'dlg_save_ok_msg':      '保存成功！\n路径：{0}',
        'dlg_save_fail_title':  '保存失败',
        'dlg_save_fail_msg':    '无法保存：\n{0}',
        'dlg_load_wav_title':   '加载 WAV',
        'dlg_wav_filter':       'WAV 音频 (*.wav);;All files (*)',
        'dlg_wav_fail_msg':     '无法加载音频：{0}',
        'dlg_no_audio_title':   '未加载音频',
        'dlg_no_audio_msg':     '尚未加载 WAV 音频文件。\n是否立即加载？',

        'dlg_shift_pitch_title':    'Shift Pitch',
        'dlg_shift_pitch_label':    '调整 pitch 偏移量（正/负整数）：',
        'dlg_resolve_title':        '整理重叠',
        'dlg_resolve_label':        '最小间距（ms）：',
        'dlg_no_overlaps_title':    '提示',
        'dlg_no_overlaps_msg':      '没有需要整理的重叠。',
        'dlg_bpm_title':            '调整 BPM',
        'dlg_bpm_label':            '当前 BPM = {0:.2f}\n新 BPM：',
        'dlg_beats_title':          '调整小节拍数',
        'dlg_beats_label':          '当前 beats_per_bar = {0}\n新值：',
        'dlg_offset_title':         '调整起始偏移',
        'dlg_offset_label':         '所有音符时间偏移量（ms，正/负整数）：',        'dlg_add_measure_title':    '新增小节',
        'dlg_add_measure_label':    '新小节的 BPM（当前 {0:.1f}）：',
        'dlg_delete_measure_title': '删除小节',
        'dlg_delete_measure_msg':   '确定要删除第 {0} 小节（{1}ms ~ {2}ms）？\n\n小节内有 {3} 个音符，删除后将一并移除，且后续小节会向前平移。',
        'dlg_delete_measure_empty': '确定要删除第 {0} 小节（{1}ms ~ {2}ms）？\n\n该小节内没有音符。',
        'dlg_delete_measure_no_data': '找不到小节数据，请确保谱面包含 beat_data。',
        'action_set_measure_bpm':       '修改小节 BPM…',
        'tb_set_measure_bpm':           '⟳ BPM',
        'dlg_set_measure_bpm_title':    '修改小节 BPM',
        'dlg_set_measure_bpm_label':    '小节 {0}（当前 {1:.1f} BPM）→ 新 BPM：',
        'dlg_measure_done':          '小节操作完成。',
        'dlg_unsaved_title':    '未保存的更改',
        'dlg_unsaved_msg':      '有未保存的更改，确定要退出？',

        'prop_title':       '编辑音符 #{0}',
        'prop_err_title':   '输入错误',
        'prop_err_msg':     '字段格式不正确：\n{0}',
        'prop_hand_hint':   'hand: 0=右手  1=左手',

        # 播放偏移对话框
        'dlg_pb_offset_title':      '播放偏移设置',
        'dlg_pb_offset_dir':        '方向：',
        'dlg_pb_offset_ms':         '时间：',
        'dlg_pb_offset_beat':       '拍数：',
        'dlg_pb_offset_beat_unit':  ' 拍',
        'dlg_pb_offset_advance':    '提前 ▲',
        'dlg_pb_offset_delay':      '延后 ▼',
        'tb_offset':                '⏱ 偏移',
        'tb_offset_tip':            '设置播放提前/延后偏移',
        'status_offset':            '偏移：{0}ms',
        'status_offset_none':       '偏移：无',

        # 导出完整曲目对话框
        'action_export_song':       '导出完整曲目…',
        'dlg_export_title':         '导出完整曲目',
        'dlg_export_append':        '追加难度至现有曲目',
        'dlg_export_display_name':  '曲名：',
        'dlg_export_author':        '作者：',
        'dlg_export_diff_name':     '难度名称：',
        'dlg_export_diff_level':    '定数：',
        'dlg_export_cover':         '曲绘：',
        'dlg_export_browse':        '浏览…',
        'dlg_export_audio':         '音源：',
        'dlg_export_audio_hint_adv':    '将依据提前 {0}ms 裁切音源',
        'dlg_export_audio_hint_delay':  '将依据延后 {0}ms 裁切音源',
        'dlg_export_audio_hint_none':   '无偏移，直接复制音源',
        'dlg_export_cover_pick':    '选择曲绘图片',
        'dlg_export_err_no_name':   '请输入曲名。',
        'dlg_export_err_no_diff':   '请输入难度名称。',
        'dlg_export_ok_title':      '导出成功',
        'dlg_export_ok_msg':        '曲目已导出至：\n{0}',
        'dlg_export_fail_title':    '导出失败',
        'dlg_export_fail_msg':      '导出过程发生错误：\n{0}',
        'dlg_export_no_audio':      '尚未加载音频，无法导出音源。\n请先加载 WAV。',
        'dlg_export_no_chart':      '当前没有谱面数据可导出。',
        'dlg_export_browse_folder': '选择曲目文件夹…',
        'dlg_export_pick_folder':   '选择曲目或父文件夹',
        'dlg_export_select_song':   '选择曲目',
        'dlg_export_found_songs':   '在所选文件夹中找到 {0} 个曲目，请选择：',
        'dlg_export_no_songs':      '所选文件夹中找不到任何有效曲目（需包含 register.json）。',
        'dlg_export_cover_auto':    '（已从注册表自动带入）',

        'settings_title':       '设置',
        'settings_language':    '语言',
        'settings_scroll_dir':  '滚轮方向',
        'settings_normal':      '正向',
        'settings_reversed':    '反向',
        'settings_restart_note': '切换语言后将自动重新启动程序。',
    },

    # ──────────────────────────────────────────────────────────────────────
    # English
    # ──────────────────────────────────────────────────────────────────────
    'en': {
        'menu_file':            'File(&F)',
        'menu_edit':            'Edit(&E)',
        'menu_audio':           'Audio(&U)',
        'menu_tools':           'Tools(&T)',
        'menu_view':            'View(&V)',
        'menu_settings':        'Settings(&G)',

        'action_open':          'Open(&O)…',
        'action_new_chart':     'New Chart(&N)…',
        'action_import_midi_sub':   'Import MIDI Track',
        'action_open_midi_right':   'Open Right-Hand MIDI…',
        'action_open_midi_left':    'Open Left-Hand MIDI…',
        'dlg_midi_right_done':      'Right-hand notes imported from MIDI ({0} notes).',
        'dlg_midi_left_done':       'Left-hand notes imported from MIDI ({0} notes).',
        'dlg_midi_no_notes':        'No notes found for the selected hand in MIDI.',
        'action_save':          'Save(&S)',
        'action_save_as':       'Save As…',
        'action_save_json':     'Save as JSON…',
        'action_quit':          'Quit(&Q)',

        'action_undo':          'Undo',
        'action_select_all':    'Select All',
        'action_deselect':      'Deselect All',
        'action_delete':        'Delete Selected  Del',
        'action_duplicate':     'Duplicate in Place  C',
        'action_copy':          'Copy',
        'action_paste':         'Paste',
        'action_width2':        'Set Width = 2',
        'action_width3':        'Set Width = 3',
        'action_type_tap':      'Type: Tap            T',
        'action_type_soft':     'Type: Soft',
        'action_type_long':     'Type: Long           H',
        'action_type_staccato': 'Type: Staccato       K',
        'action_right_hand':    'Right Hand  R',
        'action_left_hand':     'Left Hand   L',
        'action_shift_pitch':   'Shift Pitch…',

        'action_load_wav':      'Load WAV…',
        'action_hit_sound':     '🥁 Play Hit Sound (on note)',
        'action_play_window':   '▶ Play Segment       P',
        'action_play_sel':      '▶ Play Selection     Shift+P',
        'action_play_full':     '▶ Play Full Song     Ctrl+P',
        'action_pause':         '⏸ Pause',
        'action_resume':        '⏵ Resume',
        'action_stop':          '■ Stop               S',
        'action_restart':       '↺ Restart',

        'action_auto_sort':         'Auto Sort in Range   Shift+A',
        'action_resort_all':        'Resort All Notes by Pitch',
        'action_resolve_overlaps':  'Resolve Overlaps…',
        'action_adjust_bpm':        'Adjust BPM…',
        'action_adjust_beats':      'Adjust Beats per Bar…',
        'action_adjust_offset':     'Adjust Start Offset…',
        'action_add_measure':       'Add Measure…',
        'action_delete_measure':    'Delete Measure…',

        'action_zoom_in':       'Zoom In (+)',
        'action_zoom_out':      'Zoom Out (-)',
        'action_scroll_invert': 'Toggle Scroll Direction',

        'action_preferences':   'Preferences…',

        'tb_open':          'Open',
        'tb_save':          'Save',
        'tb_new_chart':     'New Chart',
        'tb_undo':          'Undo',
        'tb_auto_sort':     'Auto Sort',
        'tb_zoom_out':      'Zoom −',
        'tb_zoom_in':       'Zoom ＋',
        'tb_play':          '▶ Play Segment',
        'tb_play_full':     '▶ Play Full',
        'tb_stop':          '■ Stop',
        'tb_pause':         '⏸ Pause',
        'tb_resume':        '⏵ Resume',
        'tb_hit_sound':     '🥁 Hit Sound',
        'tb_hit_sound_tip': 'Play hit sound when passing a note',
        'tb_music_vol':     'Music Vol',
        'tb_music2_vol':    'Second audio volume',
        'tb_hit_vol':       'Hit Vol',
        'tb_preview':        '🎬 Preview Chart',
        'tb_preview_tip':    'Preview the full chart with note images',
        'tb_time_uniform':       '⏱ Measure Uniform',
        'tb_time_uniform_tip':   'Toggle time-uniform / measure-uniform view',
        'tb_time_uniform_on':    '⏱ Time Uniform',
        'tb_time_uniform_off':   '⏱ Measure Uniform',
        'tb_note_input':     '✒ Input Mode',
        'tb_note_input_tip': 'Click chart to place notes at the snapped beat position',
        'tb_add_measure':    'Add Measure',
        'tb_delete_measure': 'Del Measure',
        'tb_note_ dur_label': 'Duration:',
        'tb_note_hand_r':    'Right',
        'tb_note_hand_l':    'Left',
        'tb_main':          'Main Toolbar',

        'status_open_file':     'Please open a file',
        'status_sel':           'Selected: {0}',
        'status_audio_none':    'Audio: Not loaded',
        'status_audio_loaded':  'Audio: {0}',
        'status_hint':          ('↑↓Scroll  ←→Lane  Ctrl+Z Undo  Del Delete  '
                                 'H=Long T=Tap K=Staccato  L=Left R=Right  '
                                 'C=Dup  P=Play Segment  Ctrl+P=Play Full  S=Stop  Shift+A=Auto Sort'),

        'status_window':        'Window {0}ms..{1}ms  ({2:.2f}..{3:.2f} beat)  ΔW={4:.2f}  Selected:{5}  BPM:{6:.1f}',

        'wnd_title':            'NOS chart maker',
        'wnd_no_file':          '(No File)',

        'dlg_open_title':       'Open Chart',
        'dlg_file_filter':      'All supported (*.xml *.json *.mid *.midi);;XML (*.xml);;JSON (*.json);;MIDI (*.mid *.midi)',
        'dlg_warn':             'Warning',
        'dlg_midi_no_conv':     'Cannot find midi_to_xml_converter, unable to convert MIDI.',
        'dlg_load_fail_title':  'Load Failed',
        'dlg_load_fail_msg':    'Failed to load file:\n{0}',
        'dlg_save_as_title':    'Save As',
        'dlg_save_json_title':  'Save as JSON',
        'dlg_save_ok_title':    'Save Successful',
        'dlg_save_ok_msg':      'Saved successfully!\nPath: {0}',
        'dlg_save_fail_title':  'Save Failed',
        'dlg_save_fail_msg':    'Failed to save:\n{0}',
        'dlg_load_wav_title':   'Load WAV',
        'dlg_wav_filter':       'WAV Audio (*.wav);;All files (*)',
        'dlg_wav_fail_msg':     'Cannot load audio: {0}',
        'dlg_no_audio_title':   'Audio Not Loaded',
        'dlg_no_audio_msg':     'No WAV audio file loaded.\nLoad one now?',

        'dlg_shift_pitch_title':    'Shift Pitch',
        'dlg_shift_pitch_label':    'Pitch offset (positive/negative integer):',
        'dlg_resolve_title':        'Resolve Overlaps',
        'dlg_resolve_label':        'Minimum gap (ms):',
        'dlg_no_overlaps_title':    'Notice',
        'dlg_no_overlaps_msg':      'No overlaps to resolve.',
        'dlg_bpm_title':            'Adjust BPM',
        'dlg_bpm_label':            'Current BPM = {0:.2f}\nNew BPM:',
        'dlg_beats_title':          'Adjust Beats per Bar',
        'dlg_beats_label':          'Current beats_per_bar = {0}\nNew value:',
        'dlg_offset_title':         'Adjust Start Offset',
        'dlg_offset_label':         'Time offset for all notes (ms, positive/negative integer):',
        'dlg_add_measure_title':    'Add Measure',
        'dlg_add_measure_label':    'BPM for new measure (current {0:.1f}):',
        'dlg_delete_measure_title': 'Delete Measure',
        'dlg_delete_measure_msg':   'Delete measure {0} ({1}ms ~ {2}ms)?\n\nThis measure contains {3} notes, all of which will be deleted, and subsequent measures will be shifted forward.',
        'dlg_delete_measure_empty': 'Delete measure {0} ({1}ms ~ {2}ms)?\n\nThis measure contains no notes.',
        'dlg_delete_measure_no_data': 'No measure data found. Please ensure the chart has beat_data.',
        'action_set_measure_bpm':       'Set Measure BPM…',
        'tb_set_measure_bpm':           '⟳ BPM',
        'dlg_set_measure_bpm_title':    'Set Measure BPM',
        'dlg_set_measure_bpm_label':    'Bar {0} (current {1:.1f} BPM) → New BPM:',
        'dlg_measure_done':          'Measure operation complete.',

        'dlg_unsaved_title':    'Unsaved Changes',
        'dlg_unsaved_msg':      'There are unsaved changes. Are you sure you want to quit?',

        'prop_title':       'Edit Note #{0}',
        'prop_err_title':   'Input Error',
        'prop_err_msg':     'Invalid field format:\n{0}',
        'prop_hand_hint':   'hand: 0=Right  1=Left',

        # Playback offset dialog
        'dlg_pb_offset_title':      'Playback Offset',
        'dlg_pb_offset_dir':        'Direction:',
        'dlg_pb_offset_ms':         'Time:',
        'dlg_pb_offset_beat':       'Beats:',
        'dlg_pb_offset_beat_unit':  ' beat(s)',
        'dlg_pb_offset_advance':    'Advance ▲',
        'dlg_pb_offset_delay':      'Delay ▼',
        'tb_offset':                '⏱ Offset',
        'tb_offset_tip':            'Set playback advance/delay offset',
        'status_offset':            'Offset: {0}ms',
        'status_offset_none':       'Offset: None',

        # Export song dialog
        'action_export_song':       'Export Full Song…',
        'dlg_export_title':         'Export Full Song',
        'dlg_export_append':        'Append difficulty to existing song',
        'dlg_export_display_name':  'Song Name:',
        'dlg_export_author':        'Author:',
        'dlg_export_diff_name':     'Difficulty:',
        'dlg_export_diff_level':    'Level:',
        'dlg_export_cover':         'Cover Art:',
        'dlg_export_browse':        'Browse…',
        'dlg_export_audio':         'Audio:',
        'dlg_export_audio_hint_adv':    'Audio will be trimmed with {0}ms advance',
        'dlg_export_audio_hint_delay':  'Audio will be trimmed with {0}ms delay',
        'dlg_export_audio_hint_none':   'No offset, audio will be copied as-is',
        'dlg_export_cover_pick':    'Select Cover Image',
        'dlg_export_err_no_name':   'Please enter a song name.',
        'dlg_export_err_no_diff':   'Please enter a difficulty name.',
        'dlg_export_ok_title':      'Export Successful',
        'dlg_export_ok_msg':        'Song exported to:\n{0}',
        'dlg_export_fail_title':    'Export Failed',
        'dlg_export_fail_msg':      'Error during export:\n{0}',
        'dlg_export_no_audio':      'No audio loaded. Cannot export audio.\nPlease load a WAV first.',
        'dlg_export_no_chart':      'No chart data to export.',
        'dlg_export_browse_folder': 'Select Song Folder…',
        'dlg_export_pick_folder':   'Select Song or Parent Folder',
        'dlg_export_select_song':   'Select Song',
        'dlg_export_found_songs':   'Found {0} songs in the selected folder. Please choose:',
        'dlg_export_no_songs':      'No valid songs found in the selected folder (must contain register.json).',
        'dlg_export_cover_auto':    '(auto-loaded from register)',

        'settings_title':       'Preferences',
        'settings_language':    'Language',
        'settings_scroll_dir':  'Scroll Direction',
        'settings_normal':      'Normal',
        'settings_reversed':    'Reversed',
        'settings_restart_note': 'Changing language will automatically restart the app.',
    },
}


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def set_lang(lang: str) -> None:
    """設定目前語言（'zh_tw' / 'zh_cn' / 'en'）。"""
    global _LANG
    if lang in _STRINGS:
        _LANG = lang


def get_lang() -> str:
    return _LANG


def t(key: str, *args) -> str:
    """取得翻譯字串，可帶格式化參數。"""
    table = _STRINGS.get(_LANG, _STRINGS['zh_tw'])
    s = table.get(key, _STRINGS['zh_tw'].get(key, key))
    if args:
        try:
            return s.format(*args)
        except Exception:
            return s
    return s
