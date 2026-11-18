"""NosMania（啟動器、曲庫管理、Hiraeth 輸出）的介面文字翻譯。

原文就是繁體中文，程式裡直接寫 `tr('繁中原文')`；這裡查簡中、英文和日文。
簡中和英文在 `_TABLE`（一鍵一組二元組），日文在 `_JA`（一鍵一條）。
製譜器本體用 `i18n.t(key)`，兩邊共用同一個目前語言（`i18n.get_lang()`）。

英文裡的 `%1$s`、`%2$s` 表示參數順序和中文不同，`tr` 會換回一般的 `%s` 順序。
由 scratchpad 的 gen_ui_text.py 產生；新增字串時照格式補一行。
"""

from __future__ import annotations

import re

from .i18n import get_lang

LANGUAGES = (
    ('zh_tw', '繁體中文'),
    ('zh_cn', '简体中文'),
    ('en', 'English'),
    ('ja', '日本語'),
)

_POSITIONAL = re.compile(r"%(\d)\$([sdr])")


class _Reordered(str):
    """參數順序和原文不同的譯文：`%` 運算時照 `%1$s` 的編號取參數。"""

    def __mod__(self, args):
        if not isinstance(args, tuple):
            args = (args,)
        return _POSITIONAL.sub(lambda m: ("%" + m.group(2)) % (args[int(m.group(1)) - 1],), str(self))


def tr(text: str) -> str:
    """繁體原文換成目前語言。

    日文走另一張表（`_JA`），不是 `_TABLE` 的第三欄：`_TABLE` 是 gen_ui_text.py
    產生的 (簡, 英) 二元組，多塞一欄要連那支產生器和現有的測試一起改。日文用繁體
    原文當鍵另開一張表，和遊戲端 `Resources/Locale/ja.json` 的做法一致。

    **日文漏翻退英文，不退繁體。** 日文畫面上冒出一句繁體中文像是程式壞了。
    """
    lang = get_lang()
    if lang == 'zh_tw':
        return text
    if lang == 'ja':
        value = _JA.get(text)
        if value:
            return value
        entry = _TABLE.get(text)
        return entry[1] if entry else text
    entry = _TABLE.get(text)
    if entry is None:
        return text
    value = entry[0] if lang == 'zh_cn' else entry[1]
    return _Reordered(value) if '$' in value and _POSITIONAL.search(value) else value


_TABLE = {
    '這首沒有獨立的鋼琴音軌，主音訊裡可能已經有鋼琴了——按鍵音會和它疊在一起（同一個音聽兩次）。': ('这首没有独立的钢琴音轨，主音频里可能已经有钢琴了——按键音会和它叠在一起（同一个音听两次）。',
         'This song has no separate piano track, so the main audio probably already contains the piano - the key sounds would double it.'),
    '保留按鍵音（velocity 不歸零）': ('保留按键音（velocity 不归零）',
         'Keep key sounds (velocity not zeroed)'),
    '目前開著的這一份譜 → 一包，放進上面選的那一格。': ('当前打开的这一份谱 → 一个包，放进上面选的那一格。',
         'The chart you have open becomes one package, in the slot chosen above.'),
    '選一個有 register.json 的樂曲資料夾。': ('选一个有 register.json 的乐曲文件夹。',
         'Pick a song folder that has a register.json.'),
    '會輸出 %d 包：': ('会输出 %d 个包：',
         'Will export %d package(s):'),
    '（分成好幾包是因為這些難度用不同的音訊檔——一包只能有一份 music.wav。）': ('（分成好几个包是因为这些难度用不同的音频文件——一个包只能有一份 music.wav。）',
         '(Several packages because these difficulties use different audio - one package holds a single music.wav.)'),
    '選一個曲庫資料夾。': ('选一个曲库文件夹。',
         'Pick a song library folder.'),
    '整個曲庫 → %d 包（略過 %d 項）。每首歌的難度分配規則和單首一樣。': ('整个曲库 → %d 个包（跳过 %d 项）。每首歌的难度分配规则和单首一样。',
         'Whole library: %d package(s) (%d skipped). Difficulty slots follow the same rule per song.'),
    '（沒有）': ('（没有）',
         '(none)'),
    '按鍵音模式且沒有背景音樂：music.wav 是靜音，聲音全部由按鍵發出': ('按键音模式且没有背景音乐：music.wav 是静音，声音全部由按键发出',
         'Key-sound mode with no background music: music.wav is silent; all sound comes from the keys'),
    '按鍵音模式：鋼琴軌不混進音樂檔，由按鍵發聲': ('按键音模式：钢琴轨不混进音乐文件，由按键发声',
         'Key-sound mode: the piano track is not mixed into the music file; the keys sound it'),
    '\n\n注意：Hiraeth 歌曲包原本沒有按鍵音（力度是 0），聲音全在音樂檔裡。': ('\n\n注意：Hiraeth 歌曲包原本没有按键音（力度是 0），声音全在音乐文件里。',
         '\n\nNote: Hiraeth packages have no key sounds (velocity 0); all sound is in the music file.'),
    'NosMania · 曲庫管理': ('NosMania · 曲库管理',
         'NosMania · Song Library'),
    '遊戲位置…': ('游戏位置…',
         'Game location…'),
    '▶ 啟動遊戲': ('▶ 启动游戏',
         '▶ Launch game'),
    '搜尋曲名、作者、資料夾…': ('搜索曲名、作者、文件夹…',
         'Search title, artist, folder…'),
    '只看有問題的': ('只看有问题的',
         'Only songs with problems'),
    '匯入 ▾': ('导入 ▾',
         'Import ▾'),
    'Hiraeth 歌曲包（ZIP）…': ('Hiraeth 歌曲包（ZIP）…',
         'Hiraeth package (ZIP)…'),
    '樂曲資料夾…': ('乐曲文件夹…',
         'Song folder…'),
    '新增分類…': ('新增分类…',
         'New category…'),
    '曲目資料': ('曲目资料',
         'Song info'),
    '選曲畫面的一句介紹（輸出 Hiraeth ZIP 也會用）': ('选曲画面的一句介绍（输出 Hiraeth ZIP 也会用）',
         'One-line intro on the song select screen (also used for Hiraeth ZIP)'),
    '曲名': ('曲名',
         'Title'),
    '作者': ('作者',
         'Artist'),
    '分類': ('分类',
         'Category'),
    '樂曲評論': ('乐曲评论',
         'Comment'),
    '儲存曲目資料': ('保存曲目资料',
         'Save song info'),
    '難度（直接改名稱、等級；雙擊譜面欄在製譜器打開）': ('难度（直接改名称、等级；双击谱面栏在制谱器打开）',
         'Difficulties (edit name and level in place; double-click the chart column to open it in the chart editor)'),
    '深入檢查這首（打開譜面看鍵道、音高）': ('深入检查这首（打开谱面看键道、音高）',
         'Deep check this song (reads charts for lanes and pitch)'),
    '選擇曲庫資料夾…': ('选择曲库文件夹…',
         'Choose library folder…'),
    '全部分類': ('全部分类',
         'All categories'),
    '新增分類': ('新增分类',
         'New category'),
    '分類名稱：': ('分类名称：',
         'Category name:'),
    '匯入': ('导入',
         'Import'),
    '放進哪個分類？': ('放进哪个分类？',
         'Which category?'),
    '選擇 Hiraeth 歌曲包': ('选择 Hiraeth 歌曲包',
         'Choose Hiraeth packages'),
    '匯入 %d 首。': ('导入 %d 首。',
         'Imported %d songs.'),
    '匯入 Hiraeth 歌曲包': ('导入 Hiraeth 歌曲包',
         'Import Hiraeth packages'),
    '選擇樂曲資料夾（有 register.json）': ('选择乐曲文件夹（有 register.json）',
         'Choose a song folder (with register.json)'),
    '選擇遊戲執行檔': ('选择游戏可执行文件',
         'Choose the game executable'),
    '遊戲 (*.exe)': ('游戏 (*.exe)',
         'Game (*.exe)'),
    '曲庫': ('曲库',
         'Library'),
    '開啟資料夾': ('打开文件夹',
         'Open folder'),
    '重新整理': ('刷新',
         'Refresh'),
    '復原上一步': ('撤销上一步',
         'Undo last change'),
    '刪除曲目': ('删除曲目',
         'Delete song'),
    '輸出 Hiraeth ZIP': ('输出 Hiraeth ZIP',
         'Export Hiraeth ZIP'),
    '補齊曲庫的難度': ('补齐曲库的难度',
         'Fill in missing difficulties'),
    '正在補齊難度…': ('正在补齐难度…',
         'Filling in difficulties…'),
    '中途失敗了：%s': ('中途失败了：%s',
         'Stopped part way: %s'),
    '正在生成 %s…（%d / %d）': ('正在生成 %s…（%d / %d）',
         'Generating %s… (%d / %d)'),
    '補好 %d 首，共 %d 個難度。\n\n%s': ('补好 %d 首，共 %d 个难度。\n\n%s',
         'Filled in %d songs, %d difficulties in all.\n\n%s'),
    '%s（%d 個）': ('%s（%d 个）',
         '%s (%d)'),
    '備份曲庫': ('备份曲库',
         'Back up the library'),
    '把曲庫存成 ZIP': ('把曲库存成 ZIP',
         'Save the library as a ZIP'),
    '還沒選曲庫。': ('还没选曲库。',
         'No library chosen yet.'),
    '這個曲庫裡沒有東西可以打包。': ('这个曲库里没有东西可以打包。',
         'There is nothing in this library to pack.'),
    '正在打包…': ('正在打包…',
         'Packing…'),
    '正在打包 %s…（%d / %d）': ('正在打包 %s…（%d / %d）',
         'Packing %s… (%d / %d)'),
    '打包失敗：%s': ('打包失败：%s',
         'Packing failed: %s'),
    '已存成：\n%s\n\n%d 個檔案，%.1f MB': ('已存成：\n%s\n\n%d 个文件，%.1f MB',
         'Saved to:\n%s\n\n%d files, %.1f MB'),
    '匯入曲庫備份': ('导入曲库备份',
         'Import a library backup'),
    '選擇曲庫備份 ZIP': ('选择曲库备份 ZIP',
         'Choose a library backup ZIP'),
    '正在匯入…': ('正在导入…',
         'Importing…'),
    '正在匯入 %s…（%d / %d）': ('正在导入 %s…（%d / %d）',
         'Importing %s… (%d / %d)'),
    '讀不了這個 ZIP：%s': ('读不了这个 ZIP：%s',
         'Cannot read this ZIP: %s'),
    '這個 ZIP 裡找不到樂曲資料夾（要有 register.json）。': ('这个 ZIP 里找不到乐曲文件夹（要有 register.json）。',
         'No song folders (with register.json) in this ZIP.'),
    '匯入 %d 首。': ('导入 %d 首。',
         'Imported %d songs.'),
    '\n\n失敗：\n': ('\n\n失败：\n',
         '\n\nFailed:\n'),
    '匯出乾淨版遊戲（只留新手教學）…': ('导出干净版游戏（只留新手教学）…',
         'Export a clean copy of the game (tutorials only)…'),
    '匯出乾淨版': ('导出干净版',
         'Clean copy'),
    '匯出乾淨版遊戲': ('导出干净版游戏',
         'Export a clean copy of the game'),
    '找不到遊戲資料夾。先在上面設定「遊戲位置…」。': ('找不到游戏文件夹。先在上面设置「游戏位置…」。',
         'No game folder found. Set "Game location…" above first.'),
    '這個曲庫裡找不到「%s」分類的曲目，匯出的版本會一首歌都沒有。要繼續嗎？': ('这个曲库里找不到「%s」分类的曲目，导出的版本会一首歌都没有。要继续吗？',
         'No songs in the "%s" category, so the exported copy will have none at all. Continue?'),
    '這個遊戲資料夾裡沒有東西可以打包。': ('这个游戏文件夹里没有东西可以打包。',
         'There is nothing in this game folder to pack.'),
    '已存成：%s': ('已存成：%s',
         'Saved to: %s'),
    '留下 %d 首教學曲，拿掉 %d 首曲目，共 %d 個檔案、%.0f MB。': ('留下 %d 首教学曲，拿掉 %d 首曲目，共 %d 个文件、%.0f MB。',
         'Kept %d tutorial songs, dropped %d songs; %d files, %.0f MB.'),
    '曲庫工具 ▾': ('曲库工具 ▾',
         'Library tools ▾'),
    '掃描曲庫，補齊缺少的難度…': ('扫描曲库，补齐缺少的难度…',
         'Scan the library and fill in missing difficulties…'),
    '把整個曲庫備份成 ZIP…': ('把整个曲库备份成 ZIP…',
         'Back up the whole library as a ZIP…'),
    '從曲庫備份 ZIP 匯入…': ('从曲库备份 ZIP 导入…',
         'Import from a library backup ZIP…'),
    '深入檢查全部': ('深入检查全部',
         'Deep check all'),
    '難度': ('难度',
         'Difficulty'),
    '狀態': ('状态',
         'Status'),
    '等級': ('等级',
         'Level'),
    '無背景音樂': ('无背景音乐',
         'No BGM'),
    '譜面': ('谱面',
         'Chart'),
    '在製譜器開啟': ('在制谱器打开',
         'Open in chart editor'),
    '上移': ('上移',
         'Move up'),
    '下移': ('下移',
         'Move down'),
    '刪除難度': ('删除难度',
         'Delete difficulty'),
    '儲存難度變更': ('保存难度变更',
         'Save difficulty changes'),
    '還沒選曲庫：按上面的下拉「選擇曲庫資料夾…」選 UserSongs': ('还没选曲库：按上面的下拉“选择曲库文件夹…”选 UserSongs',
         'No library selected: use "Choose library folder…" above and pick UserSongs'),
    '選擇曲庫（UserSongs）資料夾': ('选择曲库（UserSongs）文件夹',
         'Choose the library (UserSongs) folder'),
    '%d 首歌%s': ('%d 首歌%s',
         '%d songs%s'),
    '沒有曲繪': ('没有曲绘',
         'No cover'),
    '已儲存「%s」': ('已保存“%s”',
         'Saved "%s"'),
    '已儲存「%s」的難度': ('已保存“%s”的难度',
         'Saved difficulties of "%s"'),
    '已刪除難度 %s': ('已删除难度 %s',
         'Deleted difficulty %s'),
    '已刪除「%s」': ('已删除“%s”',
         'Deleted "%s"'),
    '復原': ('撤销',
         'Undo'),
    '沒有可以復原的動作。': ('没有可以撤销的动作。',
         'Nothing to undo.'),
    '\n\n失敗：\n': ('\n\n失败：\n',
         '\n\nFailed:\n'),
    '✓ 沒有發現問題': ('✓ 没有发现问题',
         '✓ No problems found'),
    '深入檢查完成：%d 首有問題': ('深入检查完成：%d 首有问题',
         'Deep check done: %d songs with problems'),
    '曲庫管理': ('曲库管理',
         'Song Library'),
    '找不到這個難度的譜面檔。': ('找不到这个难度的谱面文件。',
         'The chart file for this difficulty is missing.'),
    '找不到製譜器。': ('找不到制谱器。',
         'Chart editor not found.'),
    '已在製譜器開啟 %s（存檔後遊戲選曲會自動重整）': ('已在制谱器打开 %s（保存后游戏选曲会自动刷新）',
         'Opened %s in the chart editor (the game refreshes song select after you save)'),
    '遊戲：%s': ('游戏：%s',
         'Game: %s'),
    '按「遊戲位置…」選打包好的遊戲 exe': ('按“游戏位置…”选打包好的游戏 exe',
         'Use "Game location…" to pick the built game exe'),
    '啟動遊戲': ('启动游戏',
         'Launch game'),
    '找不到遊戲。把 NosMania 放在遊戲 build 資料夾那一層（或裡面），或按「遊戲位置…」選 exe。\n\n在 Unity 裡測試的話直接按 Play，曲庫管理的改動一樣會即時重整。': ('找不到游戏。把 NosMania 放在游戏 build 文件夹那一层（或里面），或按“游戏位置…”选 exe。\n\n在 Unity 里测试的话直接按 Play，曲库管理的改动一样会即时刷新。',
         'Game not found. Put NosMania next to (or inside) the game build folder, or use "Game location…" to pick the exe.\n\nWhen testing in Unity just press Play; library changes still refresh live.'),
    '遊戲已經開著了（改動會自動重整）': ('游戏已经开着了（改动会自动刷新）',
         'The game is already running (changes refresh automatically)'),
    '已啟動 %s': ('已启动 %s',
         'Launched %s'),
    '遊戲資料夾裡的曲庫\u3000%s': ('游戏文件夹里的曲库\u3000%s',
         'Library in the game folder  %s'),
    '上次用的曲庫\u3000%s': ('上次用的曲库\u3000%s',
         'Last used library  %s'),
    '曲庫\u3000%s': ('曲库\u3000%s',
         'Library  %s'),
    '（找不到）': ('（找不到）',
         '(missing)'),
    '\u3000（已通知遊戲重整）': ('\u3000（已通知游戏刷新）',
         '  (game notified to refresh)'),
    '從「%s」拿掉難度 %s？\n（譜面檔會留著，可以按「復原上一步」）': ('从“%s”拿掉难度 %s？\n（谱面文件会留着，可以按“撤销上一步”）',
         'Remove difficulty %2$s from "%1$s"?\n(The chart file is kept; you can use "Undo last change")'),
    '刪除「%s」？\n\n整首會移到回收區（%s），可以按「復原上一步」搬回來。': ('删除“%s”？\n\n整首会移到回收区（%s），可以按“撤销上一步”搬回来。',
         'Delete "%s"?\n\nThe whole song moves to the trash (%s); "Undo last change" brings it back.'),
    '復原「%s」？': ('撤销“%s”？',
         'Undo "%s"?'),
    '已復原：%s\u3000（已通知遊戲重整）': ('已撤销：%s\u3000（已通知游戏刷新）',
         'Undone: %s  (game notified to refresh)'),
    '已新增分類「%s」': ('已新增分类“%s”',
         'Added category "%s"'),
    '已匯入「%s」\u3000（已通知遊戲重整）': ('已导入“%s”\u3000（已通知游戏刷新）',
         'Imported "%s"  (game notified to refresh)'),
    '（未設定）': ('（未设定）',
         '(not set)'),
    '這個遊戲旁邊還沒有曲庫。\n\n要讓它直接用目前管理的曲庫嗎？\n%s\n\n會建立一個資料夾連結，不複製檔案：之後在這裡、Unity、遊戲裡改的都是同一份。': ('这个游戏旁边还没有曲库。\n\n要让它直接用目前管理的曲库吗？\n%s\n\n会创建一个文件夹链接，不复制文件：之后在这里、Unity、游戏里改的都是同一份。',
         'This game has no library next to it yet.\n\nLet it use the library you are managing?\n%s\n\nThis creates a folder link without copying files: edits here, in Unity and in the game all share one copy.'),
    '發生錯誤：%s': ('发生错误：%s',
         'Error: %s'),
    '啟動失敗：%s': ('启动失败：%s',
         'Launch failed: %s'),
    '，%d 首有問題': ('，%d 首有问题',
         ', %d with problems'),
    '這個遊戲用的是它旁邊的曲庫：\n%s\n\n和目前管理的不是同一份。要切換過去管理它嗎？': ('这个游戏用的是它旁边的曲库：\n%s\n\n和目前管理的不是同一份。要切换过去管理它吗？',
         'This game uses the library next to it:\n%s\n\nIt is not the one you are managing. Switch to it?'),
    '建立連結失敗：%s': ('创建链接失败：%s',
         'Could not create the link: %s'),
    '遊戲 · 曲庫 · 製譜器': ('游戏 · 曲库 · 制谱器',
         'Game · Library · Chart editor'),
    '進入遊戲': ('进入游戏',
         'Play'),
    '開啟遊戲。曲庫或譜面改動會在選曲畫面自動重整。': ('打开游戏。曲库或谱面改动会在选曲画面自动刷新。',
         'Start the game. Library and chart changes refresh on the song select screen.'),
    '管理曲庫': ('管理曲库',
         'Song Library'),
    '曲目資料、難度、分類、匯入匯出、刪除與復原。': ('曲目资料、难度、分类、导入导出、删除与撤销。',
         'Song info, difficulties, categories, import/export, delete and undo.'),
    '製作譜面': ('制作谱面',
         'Make Charts'),
    '打開製譜器，編輯或新建譜面。': ('打开制谱器，编辑或新建谱面。',
         'Open the chart editor to edit or create charts.'),
    '製譜器位置…': ('制谱器位置…',
         'Chart editor location…'),
    '正在開啟製譜器…': ('正在打开制谱器…',
         'Opening the chart editor…'),
    '選擇製譜器執行檔': ('选择制谱器可执行文件',
         'Choose the chart editor executable'),
    '製譜器 (*.exe)': ('制谱器 (*.exe)',
         'Chart editor (*.exe)'),
    '曲庫：%s': ('曲库：%s',
         'Library: %s'),
    '製譜器：%s': ('制谱器：%s',
         'Chart editor: %s'),
    '● 遊戲執行中': ('● 游戏运行中',
         '● Game running'),
    '已交給開著的製譜器': ('已交给开着的制谱器',
         'Sent to the running chart editor'),
    '找不到製譜器。把 Nos Chart Maker 放在 NosMania 同一層或遊戲資料夾裡，或按「製譜器位置…」選 exe。': ('找不到制谱器。把 Nos Chart Maker 放在 NosMania 同一层或游戏文件夹里，或按“制谱器位置…”选 exe。',
         'Chart editor not found. Put Nos Chart Maker next to NosMania or in the game folder, or use "Chart editor location…" to pick the exe.'),
    '未設定': ('未设定',
         'not set'),
    '未選擇': ('未选择',
         'not selected'),
    '%d 首': ('%d 首',
         '%d songs'),
    '開發版': ('开发版',
         'dev build'),
    '開啟製譜器失敗：%s': ('打开制谱器失败：%s',
         'Could not open the chart editor: %s'),
    '選擇…': ('选择…',
         'Browse…'),
    '在 Hiraeth 管理器按「导入歌曲包」選這些 ZIP 就能匯入（可以一次多選）。': ('在 Hiraeth 管理器按“导入歌曲包”选这些 ZIP 就能导入（可以一次多选）。',
         'In the Hiraeth manager, click "导入歌曲包" and select these ZIPs to import them (multi-select works).'),
    '準備中…': ('准备中…',
         'Preparing…'),
    '取消': ('取消',
         'Cancel'),
    '輸出 Hiraeth 歌曲包': ('输出 Hiraeth 歌曲包',
         'Export Hiraeth packages'),
    '輸出 Hiraeth 歌曲包（ZIP）': ('输出 Hiraeth 歌曲包（ZIP）',
         'Export Hiraeth packages (ZIP)'),
    '這首歌的樂曲資料夾（register.json 裡的全部難度）': ('这首歌的乐曲文件夹（register.json 里的全部难度）',
         "This song's folder (every difficulty in register.json)"),
    '整個曲庫（每首一包，跳過新手教學）': ('整个曲库（每首一包，跳过新手教学）',
         'Whole library (one package per song, tutorials skipped)'),
    '只輸出目前開著的譜面': ('只输出目前开着的谱面',
         'Only the chart that is open now'),
    '要輸出什麼': ('要输出什么',
         'What to export'),
    '難度格': ('难度格',
         'Slot'),
    '曲繪（PNG／JPG，可空白）': ('曲绘（PNG／JPG，可空白）',
         'Cover (PNG/JPG, optional)'),
    '沒有載入音樂 → 用內建音源把譜面算成鋼琴音軌': ('没有载入音乐 → 用内建音源把谱面算成钢琴音轨',
         'No music loaded → render the chart as a piano track with the built-in synth'),
    '音樂': ('音乐',
         'Music'),
    '選曲畫面顯示的一句介紹（可空白）': ('选曲画面显示的一句介绍（可空白）',
         'One-line intro on the song select screen (optional)'),
    '把鋼琴音軌疊到背景音樂上': ('把钢琴音轨叠到背景音乐上',
         'Mix the piano track into the background music'),
    'Hiraeth 不發按鍵音，只放音樂檔。\n樂曲資料夾／曲庫：有 pianoAudioResourcePath 的曲子把那份鋼琴音軌疊上去。\n目前譜面：用內建音源把譜面算成鋼琴音軌再疊上去。': ('Hiraeth 不发按键音，只放音乐文件。\n乐曲文件夹／曲库：有 pianoAudioResourcePath 的曲子把那份钢琴音轨叠上去。\n目前谱面：用内建音源把谱面算成钢琴音轨再叠上去。',
         'Hiraeth plays no key sounds, only the music file.\nSong folder / library: songs with pianoAudioResourcePath get that piano track mixed in.\nOpen chart: the chart is rendered as a piano track with the built-in synth and mixed in.'),
    '輸出前對每份譜跑「處理長條尾端」：長條尾端到同手下一顆至少留這麼多': ('输出前对每份谱跑“处理长条尾端”：长条尾端到同手下一颗至少留这么多',
         'Runs "fix hold tails" on every chart before export: at least this much gap between a hold tail and the next note of the same hand'),
    '長條尾端最小間距': ('长条尾端最小间距',
         'Min hold tail gap'),
    '輸出到資料夾': ('输出到文件夹',
         'Output folder'),
    '輸出': ('输出',
         'Export'),
    '選擇樂曲資料夾': ('选择乐曲文件夹',
         'Choose a song folder'),
    '選擇曲庫（UserSongs）': ('选择曲库（UserSongs）',
         'Choose the library (UserSongs)'),
    '選擇曲繪': ('选择曲绘',
         'Choose a cover'),
    '圖片 (*.png *.jpg *.jpeg)': ('图片 (*.png *.jpg *.jpeg)',
         'Images (*.png *.jpg *.jpeg)'),
    '輸出到哪個資料夾': ('输出到哪个文件夹',
         'Export to which folder'),
    '輸出 %d 個 ZIP 到：%s': ('输出 %d 个 ZIP 到：%s',
         'Exported %d ZIPs to: %s'),
    '打開資料夾': ('打开文件夹',
         'Open folder'),
    'song.json 的 slogan，最多 %d 字。\n樂曲資料夾模式：會存回那首歌的 register.json，下次自動帶出來。\n整個曲庫模式：每首用各自 register.json 裡存的評論。': ('song.json 的 slogan，最多 %d 字。\n乐曲文件夹模式：会存回那首歌的 register.json，下次自动带出来。\n整个曲库模式：每首用各自 register.json 里存的评论。',
         "The slogan in song.json, up to %d characters.\nSong folder mode: saved back to that song's register.json and filled in next time.\nWhole library mode: each song uses the comment stored in its own register.json."),
    'Hiraeth 歌曲包': ('Hiraeth 歌曲包',
         'Hiraeth packages'),
    '只輸出目前開著的譜面（要在製譜器裡用）': ('只输出目前开着的谱面（要在制谱器里用）',
         'Only the chart that is open now (use from the chart editor)'),
    'REAL 等級 1～3.5，每格 0.5': ('REAL 等级 1～3.5，每格 0.5',
         'REAL level 1–3.5 in steps of 0.5'),
    '請填曲名。': ('请填曲名。',
         'Please enter a title.'),
    '請選輸出資料夾。': ('请选输出文件夹。',
         'Please choose an output folder.'),
    '有做轉換的：': ('有做转换的：',
         'Converted:'),
    '略過：': ('略过：',
         'Skipped:'),
    '這個資料夾沒有 register.json，不是樂曲資料夾。': ('这个文件夹没有 register.json，不是乐曲文件夹。',
         'This folder has no register.json, so it is not a song folder.'),
    '找不到曲庫資料夾。': ('找不到曲库文件夹。',
         'Library folder not found.'),
    '失敗 %d 包：': ('失败 %d 包：',
         '%d packages failed:'),
    '・…另外 %d 包': ('・…另外 %d 包',
         '・…and %d more'),
    '曲庫裡沒有「%s」': ('曲库里没有“%s”',
         'The library has no "%s"'),
    'register.json 讀不出來': ('register.json 读不出来',
         'register.json cannot be read'),
    '沒有任何難度': ('没有任何难度',
         'No difficulties'),
    '沒有可以復原的動作': ('没有可以撤销的动作',
         'Nothing to undo'),
    '曲名不能空白': ('曲名不能空白',
         'The title cannot be empty'),
    '修改「%s」的曲目資料': ('修改“%s”的曲目资料',
         'Edit song info of "%s"'),
    '難度不存在': ('难度不存在',
         'Difficulty does not exist'),
    '難度名稱不能空白': ('难度名称不能空白',
         'The difficulty name cannot be empty'),
    '等級要在 1～99': ('等级要在 1～99',
         'The level must be 1–99'),
    '修改「%s」的難度 %s': ('修改“%s”的难度 %s',
         'Edit difficulty %2$s of "%1$s"'),
    '調整「%s」的難度順序': ('调整“%s”的难度顺序',
         'Reorder difficulties of "%s"'),
    '至少要留一個難度；要整首拿掉請用「刪除曲目」': ('至少要留一个难度；要整首拿掉请用“删除曲目”',
         'Keep at least one difficulty; use "Delete song" to remove the whole song'),
    '刪除「%s」的難度 %s': ('删除“%s”的难度 %s',
         'Delete difficulty %2$s of "%1$s"'),
    '刪除「%s」': ('删除“%s”',
         'Delete "%s"'),
    '分類名稱不合格（不能空白、不能叫 ALL、最多 40 字、不能有 \\/:*?"<>|）': ('分类名称不合格（不能空白、不能叫 ALL、最多 40 字、不能有 \\/:*?"<>|）',
         'Invalid category name (not empty, not ALL, at most 40 characters, none of \\/:*?"<>|)'),
    '已經有這個分類': ('已经有这个分类',
         'This category already exists'),
    '新增分類「%s」': ('新增分类“%s”',
         'Add category "%s"'),
    '這個資料夾沒有 register.json，不是樂曲資料夾': ('这个文件夹没有 register.json，不是乐曲文件夹',
         'This folder has no register.json, so it is not a song folder'),
    '這首已經在曲庫裡了': ('这首已经在曲库里了',
         'This song is already in the library'),
    '匯入「%s」': ('导入“%s”',
         'Import "%s"'),
    '找不到曲庫資料夾：%s': ('找不到曲库文件夹：%s',
         'Library folder not found: %s'),
    '「%s」的 register.json 讀不出來': ('“%s”的 register.json 读不出来',
         'register.json of "%s" cannot be read'),
    '已經有叫「%s」的難度': ('已经有叫“%s”的难度',
         'A difficulty named "%s" already exists'),
    '等級要是數字': ('等级要是数字',
         'The level must be a number'),
    'ZIP 裡沒有 song.json，不是 Hiraeth 歌曲包': ('ZIP 里没有 song.json，不是 Hiraeth 歌曲包',
         'The ZIP has no song.json, so it is not a Hiraeth package'),
    'song.json 的 charts 沒有可用的難度': ('song.json 的 charts 没有可用的难度',
         'song.json has no usable charts'),
    'ZIP 裡沒有 music.wav': ('ZIP 里没有 music.wav',
         'The ZIP has no music.wav'),
    '有拍子線落在曲末 2 秒之後': ('有小节线落在曲末 2 秒之后',
         'A beat line falls more than 2 seconds after the end of the song'),
    'ZIP 包得太多層了': ('ZIP 包得太多层了',
         'The ZIP is nested too deeply'),
    'ZIP 裡的歌曲包都匯不進來：\n%s': ('ZIP 里的歌曲包都汇不进来：\n%s',
         'None of the packages inside the ZIP could be imported:\n%s'),
    '裡面沒有歌曲包': ('里面没有歌曲包',
         'There is no package inside'),
    '%s：譜面沒有音符': ('%s：谱面没有音符',
         '%s: the chart has no notes'),
    '%s：鍵道超過 27（舊的 1 開始寫法）': ('%s：键道超过 27（旧的 1 开始写法）',
         '%s: lanes above 27 (old 1-based layout)'),
    '%s：%d/%d 顆沒有音高（無背景音樂時會沒聲音）': ('%s：%d/%d 颗没有音高（无背景音乐时会没声音）',
         '%s: %d/%d notes have no pitch (silent without BGM)'),
    '曲庫裡已經有「%s」，無法搬回': ('曲库里已经有“%s”，无法搬回',
         '"%s" already exists in the library; cannot restore it'),
    '「%s」的回收區資料夾不見了（%s），沒有東西可以搬回來。\n這筆歷史保留著，沒有動到曲庫。':
        ('“%s”的回收区文件夹不见了（%s），没有东西可以搬回来。\n这笔历史保留着，没有动到曲库。',
         'The recycle folder for "%s" is gone (%s); there is nothing to '
         'restore.\nThis history entry is kept and the library was not '
         'touched.'),
    'Hiraeth 的 REAL 上限，比它高的都一樣': ('Hiraeth 的 REAL 上限，比它高的都一样',
         "Hiraeth's REAL ceiling; anything higher looks the same"),
    '超過 15 被夾掉': ('超过 15 被夹掉', 'clamped; above 15 is not allowed'),
    '等級被 Hiraeth 的刻度壓掉：%s。REAL 只有 1／2／2.5／3／3.5，normal/hard/EX 只收 1~15。':
        ('等级被 Hiraeth 的刻度压掉：%s。REAL 只有 1／2／2.5／3／3.5，normal/hard/EX 只收 1~15。',
         "Levels flattened by Hiraeth's scale: %s. REAL has only "
         '1/2/2.5/3/3.5, and normal/hard/EX accept 1-15 only.'),
    '這份 Hiraeth 的 importer 是原版，會把帶按鍵音的包退件。要用這個模式得先改 tools/import_tools/importer.py。':
        ('这份 Hiraeth 的 importer 是原版，会把带按键音的包退件。要用这个模式得先改 tools/import_tools/importer.py。',
         "This Hiraeth install has the stock importer; it rejects packages "
         'that keep key sounds. Patch tools/import_tools/importer.py first.'),
    '找不到 Hiraeth 的 importer，沒辦法先確認它收不收按鍵音。':
        ('找不到 Hiraeth 的 importer，没办法先确认它收不收按键音。',
         'Cannot find the Hiraeth importer, so key-sound support could not '
         'be checked up front.'),
    '%s 要是 PCM16 立體聲、取樣率 %s': ('%s 要是 PCM16 立体声、采样率 %s',
         '%s must be PCM16 stereo at %s Hz'),
    '%s 超過 128MB': ('%s 超过 128MB', '%s is over 128MB'),
    '%s 超過 16MB': ('%s 超过 16MB', '%s is over 16MB'),
    '%s 沒有聲音資料': ('%s 没有声音数据', '%s has no audio data'),
    '%s 讀不了：%s': ('%s 读不了：%s', 'Cannot read %s: %s'),
    'preview.wav 超過 %d 秒': ('preview.wav 超过 %d 秒',
         'preview.wav is longer than %d seconds'),
    '%s 含 DOCTYPE/ENTITY，importer 不收': ('%s 含 DOCTYPE/ENTITY，importer 不收',
         '%s contains DOCTYPE/ENTITY, which the importer rejects'),
    '包裡有 %d 個檔案，超過 %d 個': ('包里有 %d 个文件，超过 %d 个',
         'The package has %d files, over the %d limit'),
    '把按鍵音修補套到這份 Hiraeth': ('把按键音修补套到这份 Hiraeth',
         'Patch this Hiraeth install to accept key sounds'),
    '套不上去': ('套不上去', 'Could not patch'),
    '按鍵音修補': ('按键音修补', 'Key-sound patch'),
    '讀不到 importer：%s': ('读不到 importer：%s', 'Cannot read the importer: %s'),
    '這份 importer 已經可以收按鍵音了，沒有改動。':
        ('这份 importer 已经可以收按键音了，没有改动。',
         'This importer already accepts key sounds; nothing was changed.'),
    '在 importer 裡找到 %d 處擋力度的程式碼（預期剛好 1 處），不敢亂改。請自己比對 %s。':
        ('在 importer 里找到 %d 处挡力度的代码（预期刚好 1 处），不敢乱改。请自己比对 %s。',
         'Found %d velocity guards in the importer (expected exactly 1), so '
         'nothing was changed. Compare it against %s yourself.'),
    '已套用（改的是第 %d 行）%s': ('已套用（改的是第 %d 行）%s',
         'Patched line %d%s'),
    '，原版備份到 %s': ('，原版备份到 %s', '; the stock file was backed up to %s'),
    '，原本就有備份，沒有覆蓋': ('，原本就有备份，没有覆盖',
         '; an existing backup was kept'),
    'ZIP 打不開：%s': ('ZIP 打不开：%s',
         'Cannot open the ZIP: %s'),
    '%s：找不到譜面檔': ('%s：找不到谱面文件',
         '%s: chart file missing'),
    '%s：找不到音訊檔': ('%s：找不到音频文件',
         '%s: audio file missing'),
    '%s：找不到鋼琴音軌': ('%s：找不到钢琴音轨',
         '%s: piano track missing'),
    '整包超過 300MB': ('整包超过 300MB',
         'Package larger than 300MB'),
    'format 或 package_id 不合格': ('format 或 package_id 不合格',
         'Invalid format or package_id'),
    'version 要是正整數': ('version 要是正整数',
         'version must be a positive integer'),
    'xml 檔和 charts 對不上': ('xml 文件和 charts 对不上',
         'XML files do not match charts'),
    '譜面超過 16MB': ('谱面超过 16MB',
         'Chart larger than 16MB'),
    '根節點不是 music_score': ('根节点不是 music_score',
         'Root element is not music_score'),
    '譜面長度和音訊差超過 2 秒': ('谱面长度和音频差超过 2 秒',
         'Chart and audio lengths differ by more than 2 seconds'),
    'header 不合格（file_version=1、min_scale=1、max_scale=88）': ('header 不合格（file_version=1、min_scale=1、max_scale=88）',
         'Invalid header (file_version=1, min_scale=1, max_scale=88)'),
    '音符數不合格': ('音符数不合格',
         'Invalid note count'),
    '音符時間或音高不合格': ('音符时间或音高不合格',
         'Invalid note time or pitch'),
    'track 不是 key_apiano1 或對不上': ('track 不是 key_apiano1 或对不上',
         'track is not key_apiano1 or does not match'),
    '缺 BPM 或拍子資料': ('缺 BPM 或拍子资料',
         'Missing BPM or beat data'),
    '內建鋼琴音源沒有準備好': ('内建钢琴音源没有准备好',
         'The built-in piano synth is not ready'),
    '譜面沒有音高，算不出鋼琴音軌': ('谱面没有音高，算不出钢琴音轨',
         'The chart has no pitches; cannot render a piano track'),
    '沒有可用的譜面': ('没有可用的谱面',
         'No usable charts'),
    '沒有曲繪，用純色底': ('没有曲绘，用纯色底',
         'No cover; using a plain background'),
    '多出不接受的檔案：': ('多出不接受的文件：',
         'Unexpected files:'),
    'slogan（樂曲評論）不合格，最多 %d 字': ('slogan（乐曲评论）不合格，最多 %d 字',
         'Invalid slogan (comment), at most %d characters'),
    'charts 不合格': ('charts 不合格',
         'Invalid charts'),
    'music.wav 超過 128MiB': ('music.wav 超过 128MiB',
         'music.wav larger than 128MiB'),
    '音符欄位缺漏或不是整數': ('音符字段缺漏或不是整数',
         'Note fields missing or not integers'),
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide，手 0／1）': ('音符类型／手／key_kind 不合格（只收 Tap、Long、Slide，手 0／1）',
         'Invalid note type/hand/key_kind (only Tap, Long, Slide; hand 0/1)'),
    '音符 index 沒有從 0 連號': ('音符 index 没有从 0 连号',
         'Note indexes are not consecutive from 0'),
    'sub_note 力度不是 0': ('sub_note 力度不是 0',
         'sub_note velocity is not 0'),
    'sub_note 時間或音高不合格': ('sub_note 时间或音高不合格',
         'Invalid sub_note time or pitch'),
    '不支援 %d 聲道的音訊': ('不支持 %d 声道的音频',
         'Audio with %d channels is not supported'),
    '測試用難度': ('测试用难度',
         'test difficulty'),
    '%s：沒有 register.json，略過': ('%s：没有 register.json，略过',
         '%s: no register.json, skipped'),
    '處理長條尾端（間距 %d ms）%d 條': ('处理长条尾端（间距 %d ms）%d 条',
         'Fixed hold tails (gap %d ms): %d'),
    '沒有背景音樂：用內建音源把譜面算成鋼琴音軌': ('没有背景音乐：用内建音源把谱面算成钢琴音轨',
         'No BGM: rendered the chart as a piano track with the built-in synth'),
    '音訊比譜面短，尾端補 %.1f 秒靜音': ('音频比谱面短，尾端补 %.1f 秒静音',
         'Audio shorter than the chart; padded %.1f s of silence'),
    '%d 顆顫音改成長押': ('%d 颗颤音改成长押',
         '%d trills changed to holds'),
    '拿掉 %d 顆自動彈的音符': ('拿掉 %d 颗自动弹的音符',
         'Removed %d auto-play notes'),
    '沒通過 Hiraeth 的檢查：': ('没通过 Hiraeth 的检查：',
         'Failed the Hiraeth check:'),
    '缺少 ': ('缺少 ',
         'Missing '),
    'slogan 有 Shift_JIS 編不了的字': ('slogan 有 Shift_JIS 编不了的字',
         'slogan has characters Shift_JIS cannot encode'),
    ' 不合格': (' 不合格',
         ' is invalid'),
    '%s 等級 %r 超出範圍': ('%s 等级 %r 超出范围',
         '%s level %r out of range'),
    'music.wav 要是 PCM16 雙聲道 16000/22050/44100/48000Hz': ('music.wav 要是 PCM16 双声道 16000/22050/44100/48000Hz',
         'music.wav must be PCM16 stereo 16000/22050/44100/48000Hz'),
    '鍵範圍不合格': ('键范围不合格',
         'Invalid key range'),
    '%s / %s：%s，略過': ('%s / %s：%s，略过',
         '%s / %s: %s, skipped'),
    '沒有背景音樂：用鋼琴音軌 %s': ('没有背景音乐：用钢琴音轨 %s',
         'No BGM: using piano track %s'),
    '疊上鋼琴音軌 %s': ('叠上钢琴音轨 %s',
         'Mixed in piano track %s'),
    '疊上內建音源算的鋼琴音軌': ('叠上内建音源算的钢琴音轨',
         'Mixed in a piano track rendered with the built-in synth'),
    ' 有 Shift_JIS 編不了的字': (' 有 Shift_JIS 编不了的字',
         ' has characters Shift_JIS cannot encode'),
    '%s 找不到譜面檔，略過': ('%s 找不到谱面文件，略过',
         '%s chart file missing, skipped'),
    '混音後整體降 %.1f dB 避免破音': ('混音后整体降 %.1f dB 避免破音',
         'Lowered the mix by %.1f dB to avoid clipping'),
    'Hiraeth 的匯入規則比 PAN 嚴格：<b>不發按鍵音</b>（聲音全部來自音樂檔，所以會把背景音樂和鋼琴音軌疊在一起）、顫音會改成長押、Soft／Staccato 改成 Tap、自動彈的音符拿掉、難度只有 normal／hard／expert／real（real 等級 1～3.5）。同一首歌每次輸出的 package_id 都一樣、版本號會遞增，重新匯入就是更新。': ('Hiraeth 的导入规则比 PAN 严格：<b>不发按键音</b>（声音全部来自音乐文件，所以会把背景音乐和钢琴音轨叠在一起）、颤音会改成长押、Soft／Staccato 改成 Tap、自动弹的音符拿掉、难度只有 normal／hard／expert／real（real 等级 1～3.5）。同一首歌每次输出的 package_id 都一样、版本号会递增，重新导入就是更新。',
         'Hiraeth is stricter than PAN: <b>no key sounds</b> (all sound comes from the music file, so the background music and piano track are mixed together), trills become holds, Soft/Staccato become Tap, auto-play notes are removed, and only normal/hard/expert/real difficulties exist (real level 1–3.5). Each song keeps the same package_id and the version increases on every export, so re-importing updates it.'),
    '語言': ('语言',
         'Language'),
    '開頭補了空白小節（%.2f 秒），音訊前面補等長靜音': ('开头补了空白小节（%.2f 秒），音频前面补等长静音',
         'Added empty bars at the start (%.2f s); the audio gets the same amount of silence'),
    ' 小節': (' 小节',
         ' bars'),
    '開頭留白': ('开头留白',
         'Lead-in'),
    '第一顆音符前面至少空幾小節：不夠就在譜面前面補空白小節，音訊前面補等長的靜音（JSON 原檔不動）。\n量過官方 682 份譜：第一顆音符中位數就落在第 4 拍，也就是空一整小節。\n0 = 照譜面原樣輸出。': ('第一颗音符前面至少空几小节：不够就在谱面前面补空白小节，音频前面补等长的静音（JSON 原文件不动）。\n量过官方 682 份谱：第一颗音符中位数就落在第 4 拍，也就是空一整小节。\n0 = 照谱面原样输出。',
         'At least this many empty bars before the first note: empty bars are added to the chart and the same amount of silence to the audio (the JSON source is untouched).\nMeasured over 682 official charts: the first note lands on beat 4 (median) - one whole bar.\n0 = export the chart as it is.'),
    '批量匯入（掃描整個資料夾）…': ('批量导入（扫描整个文件夹）…',
         'Batch import (scan a folder)…'),
    '批量匯入': ('批量导入',
         'Batch import'),
    '批量匯入 %d 首\u3000（已通知遊戲重整）': ('批量导入 %d 首\u3000（已通知游戏刷新）',
         'Batch imported %d songs  (game notified to refresh)'),
    '更新製譜器': ('更新制谱器',
         'Update chart editor'),
    '更新遊戲': ('更新游戏',
         'Update game'),
    '更新曲庫': ('更新曲库',
         'Update library'),
    '更新 ▾': ('更新 ▾',
         'Update ▾'),
    '更新製譜器（選新的 exe／ZIP）…': ('更新制谱器（选新的 exe／ZIP）…',
         'Update the chart editor (pick a new exe/ZIP)…'),
    '更新遊戲（選新的 build 資料夾／ZIP）…': ('更新游戏（选新的 build 文件夹／ZIP）…',
         'Update the game (pick a new build folder/ZIP)…'),
    '更新曲庫（選曲庫資料夾／ZIP）…': ('更新曲库（选曲库文件夹／ZIP）…',
         'Update the library (pick a library folder/ZIP)…'),
    '新版在哪裡？': ('新版在哪里？',
         'Where is the new version?'),
    '選檔案（ZIP／exe）': ('选文件（ZIP／exe）',
         'Pick a file (ZIP/exe)'),
    '選資料夾': ('选文件夹',
         'Pick a folder'),
    '更新了 %d 首（同名的整首換掉，其他保留）。': ('更新了 %d 首（同名的整首换掉，其他保留）。',
         'Updated %d songs (same-named songs replaced, everything else kept).'),
    '更新檔 (*.zip *.exe)': ('更新文件 (*.zip *.exe)',
         'Update (*.zip *.exe)'),
    '已更新製譜器：\n%s\n\n舊版改名保留在同一個資料夾。': ('已更新制谱器：\n%s\n\n旧版改名保留在同一个文件夹。',
         'Chart editor updated:\n%s\n\nThe old one is renamed and kept in the same folder.'),
    '先用「遊戲位置…」指定現在的遊戲。': ('先用“游戏位置…”指定现在的游戏。',
         'Use "Game location…" to point at the current game first.'),
    '遊戲正開著，請先關掉再更新。': ('游戏正开着，请先关掉再更新。',
         'The game is running; close it before updating.'),
    '已更新遊戲：換了 %d 個檔案。\n保留：%s': ('已更新游戏：换了 %d 个文件。\n保留：%s',
         'Game updated: %d files replaced.\nKept: %s'),
    '先在曲庫管理選一份曲庫。': ('先在曲库管理选一份曲库。',
         'Choose a library in the song library window first.'),
    '更新失敗：%s': ('更新失败：%s',
         'Update failed: %s'),
    '（無）': ('（无）',
         '(none)'),
    '這個資料夾裡找不到任何樂曲（沒有 register.json）': ('这个文件夹里找不到任何乐曲（没有 register.json）',
         'No songs in this folder (nothing with a register.json)'),
    '更新曲庫（%d 首）': ('更新曲库（%d 首）',
         'Update library (%d songs)'),
    '批量匯入 %d 首': ('批量导入 %d 首',
         'Batch import %d songs'),
    '找不到遊戲資料夾': ('找不到游戏文件夹',
         'Game folder not found'),
    '這不是資料夾也不是 ZIP：%s': ('这不是文件夹也不是 ZIP：%s',
         'Not a folder and not a ZIP: %s'),
    '裡面找不到製譜器的 exe（檔名要有 Chart Maker）': ('里面找不到制谱器的 exe（文件名要有 Chart Maker）',
         'No chart editor exe inside (the file name must contain Chart Maker)'),
    '不知道要裝到哪裡：先用「製譜器位置…」指定現在那一支': ('不知道要装到哪里：先用“制谱器位置…”指定现在那一支',
         'Nowhere to install to: use "Chart editor location…" to point at the current one first'),
    '裡面找不到遊戲 build（要有 exe 和同名的 _Data 資料夾）': ('里面找不到游戏 build（要有 exe 和同名的 _Data 文件夹）',
         'No game build inside (it needs an exe and a matching _Data folder)'),
    '套用到勾選的': ('套用到勾选的',
         'Apply to checked'),
    '選擇要掃描的資料夾': ('选择要扫描的文件夹',
         'Choose a folder to scan'),
    '來源資料夾': ('来源文件夹',
         'Source folder'),
    '全選': ('全选',
         'Select all'),
    '全不選': ('全不选',
         'Select none'),
    '只選新的': ('只选新的',
         'Only new ones'),
    '全部設為': ('全部设为',
         'Set all to'),
    '曲目': ('曲目',
         'Song'),
    '來源': ('来源',
         'Source'),
    '掃到 %d 首，勾選 %d 首': ('扫到 %d 首，勾选 %d 首',
         'Found %d songs, %d checked'),
    '樂曲資料夾': ('乐曲文件夹',
         'Song folder'),
    'Hiraeth ZIP': ('Hiraeth ZIP',
         'Hiraeth ZIP'),
    '曲庫已有同名，會另存一份': ('曲库已有同名，会另存一份',
         'Already in the library; imported as a copy'),

    'Hiraeth 套件': ('Hiraeth 套件',
         'Hiraeth toolkit'),
    '把歌曲包匯入 PAN 歌曲管理版、看它的曲庫、管它的遊戲。': ('把歌曲包导入 PAN 歌曲管理版、看它的曲库、管它的游戏。',
         'Import packages into the PAN song manager, browse its library, control its game.'),
    '強制停止遊戲': ('强制停止游戏',
         'Force stop the game'),
    '遊戲沒有在跑': ('游戏没有在跑',
         'The game is not running'),
    '強制停止遊戲？\n\n沒存的成績會不見（一般情況請在遊戲裡正常離開）。': ('强制停止游戏？\n\n没存的成绩会不见（一般情况请在游戏里正常离开）。',
         'Force stop the game?\n\nUnsaved results are lost (normally quit from inside the game).'),
    '已強制停止遊戲': ('已强制停止游戏',
         'Game force stopped'),
    '停不掉，可能已經關了': ('停不掉，可能已经关了',
         'Could not stop it; it may already be closed'),
    '響度 %.1f LUFS → %.1f（%+.1f dB，峰值 %.1f dBTP）': ('响度 %.1f LUFS → %.1f（%+.1f dB，峰值 %.1f dBTP）',
         'Loudness %.1f LUFS → %.1f (%+.1f dB, peak %.1f dBTP)'),
    '這首離目標還差 %.1f LU：要再大聲就得壓低峰值 %.1f dB，已經超出自動處理的範圍': ('这首离目标还差 %.1f LU：要再大声就得压低峰值 %.1f dB，已经超出自动处理的范围',
         'Still %.1f LU below target: going louder would need %.1f dB of peak reduction, beyond what is done automatically'),
    '沒有調整響度：%s': ('没有调整响度：%s',
         'Loudness left alone: %s'),
    '這不是 Hiraeth 套件資料夾（找不到 SONG_MANAGER.bat）': ('这不是 Hiraeth 套件文件夹（找不到 SONG_MANAGER.bat）',
         'Not a Hiraeth toolkit folder (no SONG_MANAGER.bat)'),
    'Hiraeth 的遊戲正開著，匯入前要先停止': ('Hiraeth 的游戏正开着，导入前要先停止',
         'The Hiraeth game is running; stop it before importing'),
    'Hiraeth 的遊戲正開著，刪除前要先停止': ('Hiraeth 的游戏正开着，删除前要先停止',
         'The Hiraeth game is running; stop it before deleting'),
    'Hiraeth 管理器太久沒回應': ('Hiraeth 管理器太久没回应',
         'The Hiraeth manager timed out'),
    'Hiraeth 管理器沒有回應': ('Hiraeth 管理器没有回应',
         'The Hiraeth manager gave no answer'),
    '選擇 Hiraeth 套件資料夾': ('选择 Hiraeth 套件文件夹',
         'Choose the Hiraeth toolkit folder'),
    '讀取 Hiraeth 曲庫…': ('读取 Hiraeth 曲库…',
         'Reading the Hiraeth library…'),
    '選擇歌曲資料夾（有 song.json）': ('选择歌曲文件夹（有 song.json）',
         'Choose a song folder (with song.json)'),
    'Hiraeth 的遊戲正開著，管理器會鎖住不給改。要強制停止再繼續嗎？': ('Hiraeth 的游戏正开着，管理器会锁住不给改。要强制停止再继续吗？',
         'The Hiraeth game is running and locks its manager. Force stop it and continue?'),
    '套件位置': ('套件位置',
         'Toolkit folder'),
    '開啟 Hiraeth 管理器': ('打开 Hiraeth 管理器',
         'Open the Hiraeth manager'),
    '匯入歌曲包（ZIP）…': ('导入歌曲包（ZIP）…',
         'Import packages (ZIP)…'),
    '匯入歌曲資料夾…': ('导入歌曲文件夹…',
         'Import a song folder…'),
    '從 Hiraeth 刪除選取': ('从 Hiraeth 删除选取',
         'Delete selected from Hiraeth'),
    '強制停止 Hiraeth 遊戲': ('强制停止 Hiraeth 游戏',
         'Force stop the Hiraeth game'),
    '版本': ('版本',
         'Version'),
    '還沒指定 Hiraeth 套件：按「選擇…」選解壓好的資料夾（裡面有 SONG_MANAGER.bat）': ('还没指定 Hiraeth 套件：按“选择…”选解压好的文件夹（里面有 SONG_MANAGER.bat）',
         'No Hiraeth toolkit yet: use "Browse…" and pick the unpacked folder (the one with SONG_MANAGER.bat)'),
    'Hiraeth 裡有 %d 首': ('Hiraeth 里有 %d 首',
         'Hiraeth has %d songs'),
    '匯入 %d 個歌曲包…': ('导入 %d 个歌曲包…',
         'Importing %d packages…'),
    '刪除 %d 首…': ('删除 %d 首…',
         'Deleting %d songs…'),
    'Hiraeth 的遊戲沒有在跑': ('Hiraeth 的游戏没有在跑',
         'The Hiraeth game is not running'),
    '強制停止 Hiraeth 的遊戲？沒存的成績會不見。': ('强制停止 Hiraeth 的游戏？没存的成绩会不见。',
         'Force stop the Hiraeth game? Unsaved results are lost.'),
    '已停止：%s': ('已停止：%s',
         'Stopped: %s'),
    '從 Hiraeth 刪掉這 %d 首？\n（它自己會留備份）': ('从 Hiraeth 删掉这 %d 首？\n（它自己会留备份）',
         'Delete these %d songs from Hiraeth?\n(It keeps its own backup.)'),
    '（沒有正在跑的）': ('（没有正在跑的）',
         '(nothing running)'),
    '一鍵同步曲庫': ('一键同步曲库',
         'Sync the library'),
    '開始同步': ('开始同步',
         'Start syncing'),
    '全部重來': ('全部重来',
         'Redo everything'),
    '同步曲庫…': ('同步曲库…',
         'Syncing the library…'),
    '一鍵同步曲庫 → Hiraeth': ('一键同步曲库 → Hiraeth',
         'Sync library → Hiraeth'),
    '找不到 nos-clone 的曲庫，先在曲庫管理選一份。': ('找不到 nos-clone 的曲库，先在曲库管理选一份。',
         'No nos-clone library found; pick one in the song library window first.'),
    '要同步 %d 首，%d 首沒改過會略過。\n\n輸出成 Hiraeth 歌曲包再匯入，同一首是更新不會變兩份。': ('要同步 %d 首，%d 首没改过会略过。\n\n输出成 Hiraeth 歌曲包再导入，同一首是更新不会变两份。',
         'Syncing %d songs; %d unchanged ones are skipped.\n\nThey are exported as Hiraeth packages and imported; the same song updates rather than duplicating.'),
    '沒有需要同步的曲目': ('没有需要同步的曲目',
         'Nothing needs syncing'),
    '同步完成：匯入 %d 首，沒改過略過 %d 首。': ('同步完成：导入 %d 首，没改过略过 %d 首。',
         'Sync done: %d imported, %d unchanged and skipped.'),
    '（中途取消，已經做完的那幾首有匯入）': ('（中途取消，已经做完的那几首有导入）',
         '(Cancelled part way; the songs already built were imported)'),
    '失敗：': ('失败：',
         'Failed:'),
    '匯入 Hiraeth…': ('导入 Hiraeth…',
         'Importing into Hiraeth…'),
    '一鍵同步曲庫（雙向）': ('一键同步曲库（双向）',
         'Sync library (both ways)'),
    '兩邊互補，不會刪任何東西：\n・送去 Hiraeth：%d 首（%d 首沒改過會略過）\n・搬回 nos-clone：%d 首（Hiraeth 有、這邊沒有的）': ('两边互补，不会删任何东西：\n・送去 Hiraeth：%d 首（%d 首没改过会略过）\n・搬回 nos-clone：%d 首（Hiraeth 有、这边没有的）',
         'Each side fills in what the other lacks; nothing is deleted:\n・To Hiraeth: %d songs (%d unchanged ones skipped)\n・Back to nos-clone: %d songs (in Hiraeth but not here)'),
    '同步完成：送去 Hiraeth %d 首，搬回 nos-clone %d 首，沒改過略過 %d 首。': ('同步完成：送去 Hiraeth %d 首，搬回 nos-clone %d 首，没改过略过 %d 首。',
         'Sync done: %d sent to Hiraeth, %d brought back to nos-clone, %d unchanged and skipped.'),
    'Hiraeth 裡找不到這首的檔案：%s': ('Hiraeth 里找不到这首的文件案：%s',
         'No files for this song in Hiraeth: %s'),
    'Hiraeth 裡找不到這首的音樂檔：%s': ('Hiraeth 里找不到这首的音乐文件：%s',
         'No music file for this song in Hiraeth: %s'),
    'Hiraeth 裡找不到這首的譜面：%s': ('Hiraeth 里找不到这首的谱面：%s',
         'No charts for this song in Hiraeth: %s'),
    'Nos-clone': ('Nos-clone',
         'Nos-clone'),
    'Hiraeth（PAN 歌曲管理版）': ('Hiraeth（PAN 歌曲管理版）',
         'Hiraeth (PAN song manager)'),
    '選 Nos-clone 或 Hiraeth 開始玩，也可以在那裡強制停止。': ('选 Nos-clone 或 Hiraeth 开始玩，也可以在那里强制停止。',
         'Start Nos-clone or Hiraeth, or force stop them from there.'),
    '這個資料夾裡的遊戲': ('这个文件夹里的游戏',
         'The game in this folder'),
    '第四格指定的那份套件': ('第四格指定的那份套件',
         'The toolkit set in the fourth card'),
    '啟動': ('启动',
         'Start'),
    '強制停止': ('强制停止',
         'Force stop'),
    '還沒指定 Hiraeth 套件（在第四格裡選）': ('还没指定 Hiraeth 套件（在第四格里选）',
         'No Hiraeth toolkit yet (pick one in the fourth card)'),
    '還沒指定遊戲：按首頁的「遊戲位置…」': ('还没指定游戏：按首页的“游戏位置…”',
         'No game yet: use "Game location…" on the home screen'),
    '強制停止 %s？\n\n沒存的成績會不見（一般情況請在遊戲裡正常離開）。': ('强制停止 %s？\n\n没存的成绩会不见（一般情况请在游戏里正常离开）。',
         'Force stop %s?\n\nUnsaved results are lost (normally quit from inside the game).'),
    '已強制停止 %s': ('已强制停止 %s',
         '%s force stopped'),
    '● 執行中：%s': ('● 执行中：%s',
         '● Running: %s'),
    '● 執行中\u3000': ('● 执行中\u3000',
         '● Running  '),
    '這份 Hiraeth 套件不完整，開不了遊戲': ('这份 Hiraeth 套件不完整，开不了游戏',
         'This Hiraeth toolkit is incomplete; it cannot start the game'),
    '修剪結尾空白（%d 首）': ('修剪结尾空白（%d 首）',
         'Trim trailing blank (%d songs)'),
    '修剪結尾空白': ('修剪结尾空白',
         'Trim trailing blank'),
    '修剪結尾空白…': ('修剪结尾空白…',
         'Trim trailing blank…'),
    '遊戲走到譜面的「曲終」才結束，曲終設得比最後一顆音符（和最後的聲音）晚太多，玩完就要對著空畫面等。這裡把曲終收到內容結束後一小段，音訊也可以跟著剪短（原檔會備份）。': ('游戏走到谱面的“曲终”才结束，曲终设得比最后一颗音符（和最后的声音）晚太多，玩完就要对着空画面等。这里把曲终收到内容结束后一小段，音频也可以跟着剪短（原文件会备份）。',
         'The game ends when the chart reaches its end time, so an end time set long after the last note (and the last sound) leaves the player staring at an empty screen. This pulls the end time in to shortly after the content, and can trim the audio to match (the original is backed up).'),
    '同時剪短音訊檔': ('同时剪短音频文件',
         'Trim the audio file too'),
    '原檔會先備份到 UserSongs_editor/audio_backup。無背景音樂的曲子不會動。': ('原文件会先备份到 UserSongs_editor/audio_backup。无背景音乐的曲子不会动。',
         'Originals are backed up to UserSongs_editor/audio_backup. Songs with no background music are left alone.'),
    '重新掃描': ('重新扫描',
         'Scan again'),
    '修剪勾選的': ('修剪勾选的',
         'Trim checked'),
    '空白超過': ('空白超过',
         'Blank longer than'),
    '才算；修剪後留': ('才算；修剪后留',
         'counts; keep'),
    '收尾': ('收尾',
         'as the tail'),
    '曲終': ('曲终',
         'End time'),
    '最後的內容': ('最后的内容',
         'Last content'),
    '空白': ('空白',
         'Blank'),
    '音訊長度': ('音频长度',
         'Audio length'),
    '%d 首結尾空白超過 %.1f 秒': ('%d 首结尾空白超过 %.1f 秒',
         '%d songs with more than %.1f s of trailing blank'),
    '修好 %d 首（其中 %d 首連音訊一起剪短）。\n可以按曲庫管理的「復原上一步」還原譜面；音訊原檔在 UserSongs_editor/audio_backup。': ('修好 %d 首（其中 %d 首连音频一起剪短）。\n可以按曲库管理的“撤销上一步”还原谱面；音频原文件在 UserSongs_editor/audio_backup。',
         'Fixed %d songs (%d also had their audio trimmed).\n"Undo last change" in the song library restores the charts; the original audio is in UserSongs_editor/audio_backup.'),

    '兩種結尾空白都會抓：譜面的「曲終」拖在最後一顆音符後面（玩完要對著空畫面等），還有音訊檔尾巴那一大段靜音（檔案變大，Hiraeth 輸出也會卡在「譜面長度和音訊差 2 秒」）。曲終只會往前收，音訊會留到曲終之後一小段，原檔先備份。': ('两种结尾空白都会抓：谱面的“曲终”拖在最后一颗音符后面（玩完要对着空画面等），还有音频文件尾巴那一大段静音（文件变大，Hiraeth 导出也会卡在“谱面长度和音频差 2 秒”）。曲终只会往前收，音频会留到曲终之后一小段，原文件先备份。',
         'Both kinds of trailing blank are found: a chart end time sitting long after the last note (you wait on an empty screen), and a long silent tail in the audio file (bigger files, and Hiraeth export trips on its "chart and audio differ by 2 s" check). The end time is only pulled in, never pushed out; the audio keeps a short tail past the end time, and originals are backed up.'),
    '原檔（譜面與音訊）會先備份到 UserSongs_editor/tail_trim_backup。無背景音樂的曲子不會動音訊。': ('原文件（谱面与音频）会先备份到 UserSongs_editor/tail_trim_backup。无背景音乐的曲子不会动音频。',
         'Originals (charts and audio) are backed up to UserSongs_editor/tail_trim_backup. Songs with no background music keep their audio untouched.'),
    '音訊空白': ('音频空白',
         'Audio blank'),
    '修好 %d 首（其中 %d 首連音訊一起剪短）。\n原本的譜面與音訊備份在 UserSongs_editor/tail_trim_backup。': ('修好 %d 首（其中 %d 首连音频一起剪短）。\n原本的谱面与音频备份在 UserSongs_editor/tail_trim_backup。',
         'Fixed %d songs (%d also had their audio trimmed).\nThe original charts and audio are in UserSongs_editor/tail_trim_backup.'),
    '新手教學：結尾在跑字幕，預設不修': ('新手教学：结尾在跑字幕，默认不修',
         'Tutorial: subtitles run at the end, so it is left alone by default'),
    '（其中 %d 首是新手教學，預設沒勾）': ('（其中 %d 首是新手教学，默认没勾）',
         ' (%d of them are tutorials, unchecked by default)'),
    '連尾奏一起剪': ('连尾奏一起剪',
         'Cut the outro too'),
    '最後一顆音符之後就沒事做了，但音樂常常還在放。勾了會從那裡收尾（音樂淡出），沒勾只剪掉沒聲音的部分。': ('最后一颗音符之后就没事做了，但音乐常常还在放。勾了会从那里收尾（音乐淡出），没勾只剪掉没声音的部分。',
         'After the last note there is nothing left to play, but the music often keeps going. Checked, the song ends there (the music fades out); unchecked, only silent parts are trimmed.'),
    '音符後': ('音符后',
         'After last note'),
    '結尾多出 %.1f 秒沒事做，剪掉': ('结尾多出 %.1f 秒没事做，剪掉',
         'Trimmed %.1f s of dead time from the end'),
    '從 ZIP 開啟遊戲': ('从 ZIP 开启游戏',
         'Open a game from a ZIP'),
    '從 ZIP 開啟遊戲…': ('从 ZIP 开启游戏…',
         'Open a game from a ZIP…'),
    '選一份打包好的遊戲（ZIP 或資料夾），裝到 NosMania 旁邊，順便認出裡面有沒有製譜器和曲庫。': ('选一份打包好的游戏（ZIP 或文件夹），装到 NosMania 旁边，顺便认出里面有没有制谱器和曲库。',
         'Pick a packaged game (ZIP or folder), install it next to NosMania, and detect whether it ships a chart editor and a song library.'),
    '遊戲包 (*.zip)': ('游戏包 (*.zip)',
         'Game package (*.zip)'),
    '裝到哪個資料夾': ('装到哪个文件夹',
         'Install into which folder'),
    '裝好了：%s': ('装好了：%s',
         'Installed: %s'),
    '・遊戲：%s': ('・游戏：%s',
         '・Game: %s'),
    '・製譜器：%s': ('・制谱器：%s',
         '・Chart editor: %s'),
    '・製譜器：沒有（可以自己複製一支進去）': ('・制谱器：没有（可以自己复制一支进去）',
         '・Chart editor: none (you can copy one in yourself)'),
    '・曲庫：%d 首': ('・曲库：%d 首',
         '・Library: %d songs'),
    '・曲庫：沒有（第一次啟動遊戲會問要不要用現在這份）': ('・曲库：没有（第一次启动游戏会问要不要用现在这份）',
         '・Library: none (the first launch offers to use the one you are managing)'),
    '安裝失敗：%s': ('安装失败：%s',
         'Install failed: %s'),
    '選一份打包好的遊戲（ZIP 或資料夾），裝到 NosMania 旁邊，順便認出裡面有沒有製譜器和曲庫。NosMania 旁邊放著的遊戲包會自己找到。': ('选一份打包好的游戏（ZIP 或文件夹），装到 NosMania 旁边，顺便认出里面有没有制谱器和曲库。NosMania 旁边放着的游戏包会自己找到。',
         'Pick a packaged game (ZIP or folder), install it next to NosMania, and detect whether it ships a chart editor and a song library. A game package sitting next to NosMania is found by itself.'),
    '裝這一包': ('装这一包',
         'Install this one'),
    '選別的…': ('选别的…',
         'Pick another…'),
    'NosMania 旁邊找到一包遊戲：\n%s\n\n要裝這一包嗎？': ('NosMania 旁边找到一包游戏：\n%s\n\n要装这一包吗？',
         'Found a game package next to NosMania:\n%s\n\nInstall this one?'),
    '從這包裝起來：%s': ('从这包装起来：%s',
         'Install from: %s'),
    '裝到這裡？\n%s': ('装到这里？\n%s',
         'Install here?\n%s'),
    '旁邊有還沒解壓的遊戲包：%s': ('旁边有还没解压的游戏包：%s',
         'An unpacked game package is sitting next to it: %s'),
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide、Trill，手 0／1）': ('音符类型／手／key_kind 不合格（只收 Tap、Long、Slide、Trill，手 0／1）',
         'Invalid note type/hand/key_kind (only Tap, Long, Slide, Trill; hand 0/1)'),
    '%d 顆顫音照原樣保留': ('%d 颗颤音照原样保留',
         '%d trills kept as trills'),
    'Hiraeth 的匯入規則比 PAN 嚴格：<b>不發按鍵音</b>（聲音全部來自音樂檔，所以會把背景音樂和鋼琴音軌疊在一起）、Soft／Staccato 改成 Tap、自動彈的音符拿掉、難度只有 normal／hard／expert／real（real 等級 1～3.5）。同一首歌每次輸出的 package_id 都一樣、版本號會遞增，重新匯入就是更新。': ('Hiraeth 的导入规则比 PAN 严格：<b>不发按键音</b>（声音全部来自音乐文件，所以会把背景音乐和钢琴音轨叠在一起）、Soft／Staccato 改成 Tap、自动弹的音符拿掉、难度只有 normal／hard／expert／real（real 等级 1～3.5）。同一首歌每次输出的 package_id 都一样、版本号会递增，重新导入就是更新。',
         'Hiraeth is stricter than PAN: <b>no key sounds</b> (all sound comes from the music file, so the background music and piano track are mixed together), Soft/Staccato become Tap, auto-play notes are removed, and only normal/hard/expert/real difficulties exist (real level 1–3.5). Each song keeps the same package_id and the version increases on every export, so re-importing updates it.'),
    'nos-clone → Hiraeth（覆蓋）': ('nos-clone → Hiraeth（覆盖）',
         'nos-clone → Hiraeth (overwrite)'),
    'Hiraeth → nos-clone（覆蓋）': ('Hiraeth → nos-clone（覆盖）',
         'Hiraeth → nos-clone (overwrite)'),
    '單向覆蓋 ▾': ('单向覆盖 ▾',
         'Overwrite ▾'),
    '以一邊為準，把另一邊換成一樣的（同步是兩邊互補、不刪東西）': ('以一边为准，把另一边换成一样的（同步是两边互补、不删东西）',
         'Make one side match the other (sync fills in both sides and never deletes)'),
    'nos-clone → Hiraeth（用 nos-clone 覆蓋）…': ('nos-clone → Hiraeth（用 nos-clone 覆盖）…',
         'nos-clone → Hiraeth (nos-clone wins)…'),
    'Hiraeth → nos-clone（用 Hiraeth 覆蓋）…': ('Hiraeth → nos-clone（用 Hiraeth 覆盖）…',
         'Hiraeth → nos-clone (Hiraeth wins)…'),
    '覆蓋': ('覆盖',
         'Overwrite'),
    'nos-clone 曲庫裡的每一首都會重新輸出送到 Hiraeth，Hiraeth 裡同一首會被換掉（不管有沒有改過，所以會比同步久）。\n\nHiraeth 自己會留更新前的備份。': ('nos-clone 曲库里的每一首都会重新输出送到 Hiraeth，Hiraeth 里同一首会被换掉（不管有没有改过，所以会比同步久）。\n\nHiraeth 自己会留更新前的备份。',
         'Every song in the nos-clone library is exported again and sent to Hiraeth, replacing the same song there (changed or not, so it takes longer than a sync).\n\nHiraeth keeps its own backup of what it replaces.'),
    '同時刪掉 Hiraeth 裡 nos-clone 沒有的曲目': ('同时删掉 Hiraeth 里 nos-clone 没有的曲目',
         'Also delete songs in Hiraeth that nos-clone does not have'),
    'Hiraeth 裡的每一首都會搬回 nos-clone，nos-clone 裡同一首會被換掉。\n\n⚠ 這個方向會失真：Hiraeth 存的是簡化過的 XML，Soft／Staccato 會變 Tap、自動彈的音符會不見、長押是縮過的長度。被換掉的原本那份會搬到回收區，可以從曲庫管理的「復原上一步」或回收區拿回來。\n\n分類沿用原本那首的。新手教學一律不動。': ('Hiraeth 里的每一首都会搬回 nos-clone，nos-clone 里同一首会被换掉。\n\n⚠ 这个方向会失真：Hiraeth 存的是简化过的 XML，Soft／Staccato 会变 Tap、自动弹的音符会不见、长押是缩过的长度。被换掉的原本那份会搬到回收站，可以从曲库管理的“撤销上一步”或回收站拿回来。\n\n分类沿用原本那首的。新手教学一律不动。',
         'Every song in Hiraeth is brought back into nos-clone, replacing the same song there.\n\n⚠ This direction loses detail: Hiraeth stores simplified XML, so Soft/Staccato become Tap, auto-play notes are gone and holds are shortened. The replaced originals go to the trash and can be recovered with "Undo last change" in the song library or from the trash.\n\nCategories are kept from the original songs. Tutorials are never touched.'),
    '同時把 nos-clone 裡 Hiraeth 沒有的曲目搬到回收區': ('同时把 nos-clone 里 Hiraeth 没有的曲目搬到回收站',
         'Also move songs Hiraeth does not have to the trash'),
    '覆蓋完成：送到 Hiraeth %d 首。': ('覆盖完成：送到 Hiraeth %d 首。',
         'Overwrite done: %d songs sent to Hiraeth.'),
    '覆蓋完成：換掉 %d 首、新增 %d 首。': ('覆盖完成：换掉 %d 首、新增 %d 首。',
         'Overwrite done: %d replaced, %d added.'),
    '刪掉 Hiraeth 多出來的 %d 首。': ('删掉 Hiraeth 多出来的 %d 首。',
         'Deleted %d extra songs from Hiraeth.'),
    '%d 首 Hiraeth 沒有的搬到回收區。': ('%d 首 Hiraeth 没有的搬到回收站。',
         'Moved %d songs Hiraeth does not have to the trash.'),
    "還沒指定曲庫：先在首頁的「位置設定…」選曲庫資料夾，或從 ZIP 安裝一份。": ("还没指定曲库：先在首页的“位置设定…”选曲库文件夹，或从 ZIP 安装一份。",
         "No song library yet: choose a library folder in \"Locations…\" on the home page, or install one from a ZIP."),
    "連結曲庫失敗：%s": ("连结曲库失败：%s",
         "Could not link the song library: %s"),
    "這個遊戲資料夾裡有它自己的一份曲庫：\n%s\n\n和目前管理的不是同一份，遊戲會用它自己那份。要切換過去管理它嗎？": ("这个游戏文件夹里有它自己的一份曲库：\n%s\n\n和目前管理的不是同一份，游戏会用它自己那份。要切换过去管理它吗？",
         "This game folder has its own song library:\n%s\n\nIt is not the one you are managing, and the game will use its own. Switch to managing it?"),
    "安裝遊戲": ("安装游戏",
         "Install game"),
    "安裝曲庫": ("安装曲库",
         "Install song library"),
    "曲庫包 (*.zip)": ("曲库包 (*.zip)",
         "Song library package (*.zip)"),
    "NosMania 旁邊找到一包曲庫：\n%s\n\n要裝這一包嗎？": ("NosMania 旁边找到一包曲库：\n%s\n\n要装这一包吗？",
         "Found a song library package next to NosMania:\n%s\n\nInstall it?"),
    "曲庫要解壓到哪個資料夾（會在裡面放 UserSongs）": ("曲库要解压到哪个文件夹（会在里面放 UserSongs）",
         "Folder to extract the library into (UserSongs goes inside it)"),
    "裝好了：%s\n曲目 %d 首": ("装好了：%s\n曲目 %d 首",
         "Installed: %s\n%d songs"),
    "位置設定": ("位置设定",
         "Locations"),
    "三樣東西分開放，NosMania 記住位置去連結。沒指定的話，會找 NosMania 所在的資料夾和它下一層。": ("三样东西分开放，NosMania 记住位置去连结。没指定的话，会找 NosMania 所在的文件夹和它下一层。",
         "The three parts live in separate places and NosMania remembers where. Anything not set is looked for in NosMania's folder and one level below."),
    "位置設定…": ("位置设定…",
         "Locations…"),
    "遊戲、曲庫、製譜器分開放：在這裡指定各自的位置，或從 ZIP 安裝": ("游戏、曲库、制谱器分开放：在这里指定各自的位置，或从 ZIP 安装",
         "Game, song library and chart editor are kept separately: set where each one is, or install from a ZIP"),
    "・這包裡附了曲庫：%d 首": ("・这包里附了曲库：%d 首",
         "・This package includes a song library: %d songs"),
    "・曲庫：還沒有，到「位置設定…」選一份或從 ZIP 安裝": ("・曲库：还没有，到“位置设定…”选一份或从 ZIP 安装",
         "・Song library: none yet. Choose one or install from a ZIP in \"Locations…\""),
    "遊戲本體": ("游戏本体",
         "Game"),
    "製譜器": ("制谱器",
         "Chart editor"),
    "執行檔 (*.exe)": ("可执行文件 (*.exe)",
         "Executable (*.exe)"),
    "找不到製譜器。按首頁的「位置設定…」選製譜器 exe（或把 Nos Chart Maker 放在 NosMania 同一層）。": ("找不到制谱器。按首页的“位置设定…”选制谱器 exe（或把 Nos Chart Maker 放在 NosMania 同一层）。",
         "Chart editor not found. Choose its exe in \"Locations…\" on the home page (or put Nos Chart Maker next to NosMania)."),
    "製譜器要放在哪個資料夾": ("制谱器要放在哪个文件夹",
         "Folder for the chart editor"),
    "先在「位置設定…」指定現在的遊戲。": ("先在“位置设定…”指定现在的游戏。",
         "Set the current game in \"Locations…\" first."),
    "還沒指定遊戲：按首頁的「位置設定…」": ("还没指定游戏：按首页的“位置设定…”",
         "No game set: use \"Locations…\" on the home page"),
    "・曲庫：進遊戲時會連到 %s": ("・曲库：进游戏时会连到 %s",
         "・Song library: linked to %s when the game starts"),
    "從 ZIP 安裝…": ("从 ZIP 安装…",
         "Install from ZIP…"),
    "這不是遊戲 exe（旁邊要有同名的 _Data 資料夾）。": ("这不是游戏 exe（旁边要有同名的 _Data 文件夹）。",
         "This is not the game exe (it needs a _Data folder with the same name next to it)."),
    "這個資料夾裡沒有 library.json，看起來不是曲庫。\n還是要用它當曲庫嗎？": ("这个文件夹里没有 library.json，看起来不是曲库。\n还是要用它当曲库吗？",
         "This folder has no library.json and does not look like a song library.\nUse it as the library anyway?"),
    "從旁邊的 ZIP 安裝：%s": ("从旁边的 ZIP 安装：%s",
         "Install from the ZIP next to NosMania: %s"),
    "遊戲資料夾裡有一個叫 UserSongs 的檔案，無法建立連結": ("游戏文件夹里有一个叫 UserSongs 的文件，无法建立连结",
         "The game folder has a file named UserSongs, so the link cannot be created"),
    "建立資料夾連結失敗：%s": ("建立文件夹连结失败：%s",
         "Could not create the folder link: %s"),
    "這個資料夾不是曲庫（裡面沒有 library.json）": ("这个文件夹不是曲库（里面没有 library.json）",
         "This folder is not a song library (no library.json inside)"),
    "ZIP 裡找不到曲庫（要有 library.json）": ("ZIP 里找不到曲库（要有 library.json）",
         "No song library in the ZIP (it needs library.json)"),
    "解壓沒有完成（被取消了？）：%s": ("解压没有完成（被取消了？）：%s",
         "Extraction did not finish (cancelled?): %s"),
    "這裡已經有一份曲庫：\n%s\n\n要把新的曲目併進去，請用「更新曲庫」。": ("这里已经有一份曲库：\n%s\n\n要把新的曲目并进去，请用“更新曲库”。",
         "There is already a song library here:\n%s\n\nTo merge new songs into it, use \"Update song library\"."),
    "從 ZIP 安裝曲庫…": ("从 ZIP 安装曲库…",
         "Install song library from ZIP…"),
    "還沒有曲庫。先選一份曲庫資料夾，或從曲庫 ZIP 安裝一份。": ("还没有曲库。先选一份曲库文件夹，或从曲库 ZIP 安装一份。",
         "No song library yet. Choose a library folder, or install one from a library ZIP."),
    "原本的曲庫資料夾不見了：\n%s\n\n（例如重新 build 遊戲時整個資料夾被換掉）。先選一份曲庫，或從曲庫 ZIP 安裝一份。": ("原本的曲库文件夹不见了：\n%s\n\n（例如重新 build 游戏时整个文件夹被换掉）。先选一份曲库，或从曲库 ZIP 安装一份。",
         "The song library folder is gone:\n%s\n\n(for example, the whole folder was replaced when the game was rebuilt). Choose a library, or install one from a library ZIP."),
    "資料夾不見了，按「位置設定…」重選": ("文件夹不见了，按“位置设定…”重选",
         "folder missing, choose again in \"Locations…\""),
    "連續的 soft 轉成滑奏 %d 條、顫音 %d 個": ("连续的 soft 转成滑奏 %d 条、颤音 %d 个",
         "Consecutive soft notes became %d slide chains and %d trills"),
    "處理長條尾端": ("处理长条尾端",
         "Process hold tails"),
    "勾選：長條尾端到同手下一顆至少留右邊的間距，同鍵道有別的音就裁短。\n不勾：長條完全照原樣輸出（同一個鍵上重疊的音也不處理）。": ("勾选：长条尾端到同手下一颗至少留右边的间距，同键道有别的音就裁短。\n不勾：长条完全照原样输出（同一个键上重叠的音也不处理）。",
         "Checked: leave at least the gap on the right between a hold tail and the same hand's next note, and shorten holds that run into another note on the same key.\nUnchecked: holds are exported exactly as they are (overlaps on the same key are left too)."),
    "沒有處理長條尾端（照原樣輸出）": ("没有处理长条尾端（照原样输出）",
         "Hold tails not processed (exported as they are)"),
    '相容沒改過的 Hiraeth（要給別人匯入就勾著）': ('兼容没改过的 Hiraeth（要给别人导入就勾着）',
         'Compatible with an unmodified Hiraeth (leave on if others will import it)'),
    '相容性': ('兼容性',
         'Compatibility'),
    'REAL 等級 1～3（原版 Hiraeth 只收這三格）': ('REAL 等级 1～3（原版 Hiraeth 只收这三格）',
         'REAL level 1-3 (stock Hiraeth accepts only these three)'),
    '沒有勾：這個包只有 importer 改過的 Hiraeth 裝得進去。別人匯入會**整包**退件（REAL display level must be 1/2/3…），自己這台因為改過，匯得進去看不出問題。': ('没有勾：这个包只有 importer 改过的 Hiraeth 装得进去。别人导入会**整包**退件（REAL display level must be 1/2/3…），自己这台因为改过，导得进去看不出问题。',
         'Unchecked: only a Hiraeth with a patched importer can install this package. Someone else gets the **whole package** rejected (REAL display level must be 1/2/3...); yours is patched, so it imports fine and hides the problem.'),
    '沒有勾相容：對方的 importer 必須改過才匯得進去。': ('没有勾兼容：对方的 importer 必须改过才导得进去。',
         'Compatibility off: the other side needs a patched importer to install this.'),
    '原版 Hiraeth 的 REAL 只有 1／2／3': ('原版 Hiraeth 的 REAL 只有 1／2／3',
         'Stock Hiraeth has only REAL 1/2/3'),
    '相容模式不能保留按鍵音：原版 importer 只收 sub_note 力度 0 的包。': ('兼容模式不能保留按键音：原版 importer 只收 sub_note 力度 0 的包。',
         'Compatibility mode cannot keep key sounds: the stock importer only accepts packages whose sub-notes have velocity 0.'),
    '等級被 Hiraeth 的刻度壓掉：%s。%s，normal/hard/EX 只收 1~15。': ('等级被 Hiraeth 的刻度压掉：%s。%s，normal/hard/EX 只收 1~15。',
         'Levels flattened to Hiraeth scale: %s. %s, and normal/hard/EX accept only 1-15.'),
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide%s，手 0／1）': ('音符类型／手／key_kind 不合格（只收 Tap、Long、Slide%s，手 0／1）',
         'Unsupported note type/hand/key kind (only Tap, Long, Slide%s, hand 0/1)'),
    '、Trill': ('、Trill',
         ', Trill'),
    '原版 importer 的 REAL 只有 1／2／3': ('原版 importer 的 REAL 只有 1／2／3',
         'The stock importer has only REAL 1/2/3'),
    'REAL 只有 1／2／2.5／3／3.5': ('REAL 只有 1／2／2.5／3／3.5',
         'REAL has only 1/2/2.5/3/3.5'),
}


# ──────────────────────────────────────────────────────────────────────────
# 日本語
#
# 由 scratchpad/ja/wire_ja.py 照 _TABLE 的索引產生，鍵不是手打的：
# 有的鍵開頭就是空白（' 小節'）、有的是全形空白，手打一定對不上。
# ──────────────────────────────────────────────────────────────────────────
_JA = {
    '這首沒有獨立的鋼琴音軌，主音訊裡可能已經有鋼琴了——按鍵音會和它疊在一起（同一個音聽兩次）。':
        'この曲には独立したピアノトラックがないため、メイン音声にすでにピアノが入っている可能性があります——キー音はそれと重なります（同じ音が二重に聞こえます）。',
    '保留按鍵音（velocity 不歸零）':
        'キー音を残す（velocity を 0 にしない）',
    '目前開著的這一份譜 → 一包，放進上面選的那一格。':
        '今開いている譜面 → 1 パッケージ。上で選んだ枠に入ります。',
    '選一個有 register.json 的樂曲資料夾。':
        'register.json のある曲フォルダーを選んでください。',
    '會輸出 %d 包：':
        '%d パッケージを書き出します：',
    '（分成好幾包是因為這些難度用不同的音訊檔——一包只能有一份 music.wav。）':
        '（複数のパッケージに分かれるのは、これらの難易度が別々の音声ファイルを使っているためです——1 パッケージに music.wav は 1 つだけです。）',
    '選一個曲庫資料夾。':
        'ライブラリのフォルダーを選んでください。',
    '整個曲庫 → %d 包（略過 %d 項）。每首歌的難度分配規則和單首一樣。':
        'ライブラリ全体 → %d パッケージ（%d 項目をスキップ）。曲ごとの難易度の割り当ては単曲と同じ規則です。',
    '（沒有）':
        '（なし）',
    '按鍵音模式且沒有背景音樂：music.wav 是靜音，聲音全部由按鍵發出':
        'キー音モードで BGM なし：music.wav は無音で、音はすべてキーから出ます',
    '按鍵音模式：鋼琴軌不混進音樂檔，由按鍵發聲':
        'キー音モード：ピアノトラックは音楽ファイルに混ぜず、キーが鳴らします',
    '\n\n注意：Hiraeth 歌曲包原本沒有按鍵音（力度是 0），聲音全在音樂檔裡。':
        '\n\n注意：Hiraeth の歌曲パッケージにはもともとキー音がなく（velocity は 0）、音はすべて音楽ファイルに入っています。',
    'NosMania · 曲庫管理':
        'NosMania · 曲ライブラリ',
    '遊戲位置…':
        'ゲームの場所…',
    '▶ 啟動遊戲':
        '▶ ゲームを起動',
    '搜尋曲名、作者、資料夾…':
        '曲名・作者・フォルダーを検索…',
    '只看有問題的':
        '問題のある曲だけ',
    '匯入 ▾':
        'インポート ▾',
    'Hiraeth 歌曲包（ZIP）…':
        'Hiraeth 歌曲パッケージ（ZIP）…',
    '樂曲資料夾…':
        '曲フォルダー…',
    '新增分類…':
        'カテゴリーを追加…',
    '曲目資料':
        '曲の情報',
    '選曲畫面的一句介紹（輸出 Hiraeth ZIP 也會用）':
        '選曲画面に出る一行紹介（Hiraeth ZIP の書き出しにも使います）',
    '曲名':
        '曲名',
    '作者':
        '作者',
    '分類':
        'カテゴリー',
    '樂曲評論':
        '曲のコメント',
    '儲存曲目資料':
        '曲の情報を保存',
    '難度（直接改名稱、等級；雙擊譜面欄在製譜器打開）':
        '難易度（名前とレベルはその場で編集、譜面欄をダブルクリックすると譜面エディターで開きます）',
    '深入檢查這首（打開譜面看鍵道、音高）':
        'この曲を詳しく検査（譜面を読んでレーンと音高を見ます）',
    '選擇曲庫資料夾…':
        'ライブラリのフォルダーを選択…',
    '全部分類':
        'すべてのカテゴリー',
    '新增分類':
        'カテゴリーを追加',
    '分類名稱：':
        'カテゴリー名：',
    '匯入':
        'インポート',
    '放進哪個分類？':
        'どのカテゴリーに入れますか？',
    '選擇 Hiraeth 歌曲包':
        'Hiraeth 歌曲パッケージを選択',
    '匯入 %d 首。':
        '%d 曲をインポートしました。',
    '匯入 Hiraeth 歌曲包':
        'Hiraeth 歌曲パッケージをインポート',
    '選擇樂曲資料夾（有 register.json）':
        '曲フォルダーを選択（register.json があるもの）',
    '選擇遊戲執行檔':
        'ゲームの実行ファイルを選択',
    '遊戲 (*.exe)':
        'ゲーム (*.exe)',
    '曲庫':
        'ライブラリ',
    '開啟資料夾':
        'フォルダーを開く',
    '重新整理':
        '再読み込み',
    '復原上一步':
        '直前の操作を元に戻す',
    '刪除曲目':
        '曲を削除',
    '輸出 Hiraeth ZIP':
        'Hiraeth ZIP を書き出す',
    '補齊曲庫的難度':
        'ライブラリの足りない難易度を補う',
    '正在補齊難度…':
        '難易度を補っています…',
    '中途失敗了：%s':
        '途中で失敗しました：%s',
    '正在生成 %s…（%d / %d）':
        '%s を生成中…（%d / %d）',
    '補好 %d 首，共 %d 個難度。\n\n%s':
        '%d 曲、合わせて %d 個の難易度を補いました。\n\n%s',
    '%s（%d 個）':
        '%s（%d 個）',
    '備份曲庫':
        'ライブラリをバックアップ',
    '把曲庫存成 ZIP':
        'ライブラリを ZIP で保存',
    '還沒選曲庫。':
        'ライブラリがまだ選ばれていません。',
    '這個曲庫裡沒有東西可以打包。':
        'このライブラリにはパッケージにできるものがありません。',
    '正在打包…':
        'パッケージを作成中…',
    '正在打包 %s…（%d / %d）':
        '%s をパッケージ中…（%d / %d）',
    '打包失敗：%s':
        'パッケージの作成に失敗：%s',
    '已存成：\n%s\n\n%d 個檔案，%.1f MB':
        '保存しました：\n%s\n\n%d 個のファイル、%.1f MB',
    '匯入曲庫備份':
        'ライブラリのバックアップをインポート',
    '選擇曲庫備份 ZIP':
        'ライブラリのバックアップ ZIP を選択',
    '正在匯入…':
        'インポート中…',
    '正在匯入 %s…（%d / %d）':
        '%s をインポート中…（%d / %d）',
    '讀不了這個 ZIP：%s':
        'この ZIP を読めません：%s',
    '這個 ZIP 裡找不到樂曲資料夾（要有 register.json）。':
        'この ZIP に曲フォルダー（register.json が必要）が見つかりません。',
    '\n\n失敗：\n':
        '\n\n失敗：\n',
    '匯出乾淨版遊戲（只留新手教學）…':
        'きれいなゲームを書き出す（チュートリアルだけ）…',
    '匯出乾淨版':
        'きれいなコピー',
    '匯出乾淨版遊戲':
        'きれいなゲームを書き出す',
    '找不到遊戲資料夾。先在上面設定「遊戲位置…」。':
        'ゲームフォルダーが見つかりません。先に上の「ゲームの場所…」で設定してください。',
    '這個曲庫裡找不到「%s」分類的曲目，匯出的版本會一首歌都沒有。要繼續嗎？':
        'このライブラリに「%s」カテゴリーの曲が見つからないため、書き出したコピーには曲が 1 つも入りません。続けますか？',
    '這個遊戲資料夾裡沒有東西可以打包。':
        'このゲームフォルダーにはパッケージにできるものがありません。',
    '已存成：%s':
        '保存しました：%s',
    '留下 %d 首教學曲，拿掉 %d 首曲目，共 %d 個檔案、%.0f MB。':
        'チュートリアル %d 曲を残し、%d 曲を外しました。ファイル %d 個、%.0f MB。',
    '曲庫工具 ▾':
        'ライブラリツール ▾',
    '掃描曲庫，補齊缺少的難度…':
        'ライブラリを調べて足りない難易度を補う…',
    '把整個曲庫備份成 ZIP…':
        'ライブラリ全体を ZIP でバックアップ…',
    '從曲庫備份 ZIP 匯入…':
        'ライブラリのバックアップ ZIP からインポート…',
    '深入檢查全部':
        'すべて詳しく検査',
    '難度':
        '難易度',
    '狀態':
        '状態',
    '等級':
        'レベル',
    '無背景音樂':
        'BGM なし',
    '譜面':
        '譜面',
    '在製譜器開啟':
        '譜面エディターで開く',
    '上移':
        '上へ',
    '下移':
        '下へ',
    '刪除難度':
        '難易度を削除',
    '儲存難度變更':
        '難易度の変更を保存',
    '還沒選曲庫：按上面的下拉「選擇曲庫資料夾…」選 UserSongs':
        'ライブラリが未選択です：上のプルダウン「ライブラリのフォルダーを選択…」で UserSongs を選んでください',
    '選擇曲庫（UserSongs）資料夾':
        'ライブラリ（UserSongs）のフォルダーを選択',
    '%d 首歌%s':
        '%d 曲%s',
    '沒有曲繪':
        'ジャケットなし',
    '已儲存「%s」':
        '「%s」を保存しました',
    '已儲存「%s」的難度':
        '「%s」の難易度を保存しました',
    '已刪除難度 %s':
        '難易度 %s を削除しました',
    '已刪除「%s」':
        '「%s」を削除しました',
    '復原':
        '元に戻す',
    '沒有可以復原的動作。':
        '元に戻せる操作はありません。',
    '✓ 沒有發現問題':
        '✓ 問題は見つかりませんでした',
    '深入檢查完成：%d 首有問題':
        '詳しい検査が完了：%d 曲に問題あり',
    '曲庫管理':
        '曲ライブラリ',
    '找不到這個難度的譜面檔。':
        'この難易度の譜面ファイルが見つかりません。',
    '找不到製譜器。':
        '譜面エディターが見つかりません。',
    '已在製譜器開啟 %s（存檔後遊戲選曲會自動重整）':
        '%s を譜面エディターで開きました（保存するとゲームの選曲画面が自動で再読み込みします）',
    '遊戲：%s':
        'ゲーム：%s',
    '按「遊戲位置…」選打包好的遊戲 exe':
        '「ゲームの場所…」でビルド済みのゲーム exe を選んでください',
    '啟動遊戲':
        'ゲームを起動',
    '找不到遊戲。把 NosMania 放在遊戲 build 資料夾那一層（或裡面），或按「遊戲位置…」選 exe。\n\n在 Unity 裡測試的話直接按 Play，曲庫管理的改動一樣會即時重整。':
        'ゲームが見つかりません。NosMania をゲームの build フォルダーと同じ階層（またはその中）に置くか、「ゲームの場所…」で exe を選んでください。\n\nUnity でテストするなら Play を押すだけで、ライブラリの変更はそのまま反映されます。',
    '遊戲已經開著了（改動會自動重整）':
        'ゲームはすでに起動しています（変更は自動で反映されます）',
    '已啟動 %s':
        '%s を起動しました',
    '遊戲資料夾裡的曲庫　%s':
        'ゲームフォルダー内のライブラリ　%s',
    '上次用的曲庫　%s':
        '前回使ったライブラリ　%s',
    '曲庫　%s':
        'ライブラリ　%s',
    '（找不到）':
        '（見つかりません）',
    '　（已通知遊戲重整）':
        '　（ゲームに再読み込みを通知しました）',
    '從「%s」拿掉難度 %s？\n（譜面檔會留著，可以按「復原上一步」）':
        '「%s」から難易度 %s を外しますか？\n（譜面ファイルは残るので「直前の操作を元に戻す」で戻せます）',
    '刪除「%s」？\n\n整首會移到回收區（%s），可以按「復原上一步」搬回來。':
        '「%s」を削除しますか？\n\n曲ごとごみ箱（%s）へ移動し、「直前の操作を元に戻す」で戻せます。',
    '復原「%s」？':
        '「%s」を元に戻しますか？',
    '已復原：%s　（已通知遊戲重整）':
        '元に戻しました：%s　（ゲームに再読み込みを通知しました）',
    '已新增分類「%s」':
        'カテゴリー「%s」を追加しました',
    '已匯入「%s」　（已通知遊戲重整）':
        '「%s」をインポートしました　（ゲームに再読み込みを通知しました）',
    '（未設定）':
        '（未設定）',
    '這個遊戲旁邊還沒有曲庫。\n\n要讓它直接用目前管理的曲庫嗎？\n%s\n\n會建立一個資料夾連結，不複製檔案：之後在這裡、Unity、遊戲裡改的都是同一份。':
        'このゲームの隣にはまだライブラリがありません。\n\n今管理しているライブラリをそのまま使わせますか？\n%s\n\nファイルをコピーせずフォルダーのリンクを作ります：ここでも Unity でもゲームでも、同じ 1 つを編集することになります。',
    '發生錯誤：%s':
        'エラーが発生しました：%s',
    '啟動失敗：%s':
        '起動に失敗：%s',
    '，%d 首有問題':
        '、うち %d 曲に問題あり',
    '這個遊戲用的是它旁邊的曲庫：\n%s\n\n和目前管理的不是同一份。要切換過去管理它嗎？':
        'このゲームは隣にあるライブラリを使っています：\n%s\n\n今管理しているものとは別です。そちらの管理に切り替えますか？',
    '建立連結失敗：%s':
        'リンクを作成できません：%s',
    '遊戲 · 曲庫 · 製譜器':
        'ゲーム · ライブラリ · 譜面エディター',
    '進入遊戲':
        'ゲームを始める',
    '開啟遊戲。曲庫或譜面改動會在選曲畫面自動重整。':
        'ゲームを起動します。ライブラリや譜面の変更は選曲画面で自動的に反映されます。',
    '管理曲庫':
        'ライブラリ管理',
    '曲目資料、難度、分類、匯入匯出、刪除與復原。':
        '曲の情報、難易度、カテゴリー、インポートと書き出し、削除と取り消し。',
    '製作譜面':
        '譜面を作る',
    '打開製譜器，編輯或新建譜面。':
        '譜面エディターを開いて、譜面を編集または新規作成します。',
    '製譜器位置…':
        '譜面エディターの場所…',
    '正在開啟製譜器…':
        '譜面エディターを起動しています…',
    '選擇製譜器執行檔':
        '譜面エディターの実行ファイルを選択',
    '製譜器 (*.exe)':
        '譜面エディター (*.exe)',
    '曲庫：%s':
        'ライブラリ：%s',
    '製譜器：%s':
        '譜面エディター：%s',
    '● 遊戲執行中':
        '● ゲーム実行中',
    '已交給開著的製譜器':
        '起動中の譜面エディターに渡しました',
    '找不到製譜器。把 Nos Chart Maker 放在 NosMania 同一層或遊戲資料夾裡，或按「製譜器位置…」選 exe。':
        '譜面エディターが見つかりません。Nos Chart Maker を NosMania と同じ階層かゲームフォルダーに置くか、「譜面エディターの場所…」で exe を選んでください。',
    '未設定':
        '未設定',
    '未選擇':
        '未選択',
    '%d 首':
        '%d 曲',
    '開發版':
        '開発版',
    '開啟製譜器失敗：%s':
        '譜面エディターを開けません：%s',
    '選擇…':
        '参照…',
    '在 Hiraeth 管理器按「导入歌曲包」選這些 ZIP 就能匯入（可以一次多選）。':
        'Hiraeth マネージャーで「导入歌曲包」を押し、これらの ZIP を選べばインポートできます（複数選択できます）。',
    '準備中…':
        '準備中…',
    '取消':
        'キャンセル',
    '輸出 Hiraeth 歌曲包':
        'Hiraeth 歌曲パッケージを書き出す',
    '輸出 Hiraeth 歌曲包（ZIP）':
        'Hiraeth 歌曲パッケージ（ZIP）を書き出す',
    '這首歌的樂曲資料夾（register.json 裡的全部難度）':
        'この曲のフォルダー（register.json にある全難易度）',
    '整個曲庫（每首一包，跳過新手教學）':
        'ライブラリ全体（1 曲 1 パッケージ、チュートリアルは除く）',
    '只輸出目前開著的譜面':
        '今開いている譜面だけ',
    '要輸出什麼':
        '何を書き出すか',
    '難度格':
        '難易度の枠',
    '曲繪（PNG／JPG，可空白）':
        'ジャケット（PNG／JPG、省略可）',
    '沒有載入音樂 → 用內建音源把譜面算成鋼琴音軌':
        '音楽が未読み込み → 内蔵音源で譜面をピアノトラックとして生成します',
    '音樂':
        '音楽',
    '選曲畫面顯示的一句介紹（可空白）':
        '選曲画面に出る一行紹介（省略可）',
    '把鋼琴音軌疊到背景音樂上':
        'ピアノトラックを BGM に重ねる',
    'Hiraeth 不發按鍵音，只放音樂檔。\n樂曲資料夾／曲庫：有 pianoAudioResourcePath 的曲子把那份鋼琴音軌疊上去。\n目前譜面：用內建音源把譜面算成鋼琴音軌再疊上去。':
        'Hiraeth はキー音を鳴らさず、音楽ファイルだけを再生します。\n曲フォルダー／ライブラリ：pianoAudioResourcePath を持つ曲は、そのピアノトラックを重ねます。\n今開いている譜面：内蔵音源で譜面をピアノトラックにしてから重ねます。',
    '輸出前對每份譜跑「處理長條尾端」：長條尾端到同手下一顆至少留這麼多':
        '書き出す前に各譜面へ「ロングの終端を処理」を実行します：ロングの終端から同じ手の次のノーツまで、最低これだけ空けます',
    '長條尾端最小間距':
        'ロング終端の最小間隔',
    '輸出到資料夾':
        '書き出し先フォルダー',
    '輸出':
        '書き出す',
    '選擇樂曲資料夾':
        '曲フォルダーを選択',
    '選擇曲庫（UserSongs）':
        'ライブラリ（UserSongs）を選択',
    '選擇曲繪':
        'ジャケットを選択',
    '圖片 (*.png *.jpg *.jpeg)':
        '画像 (*.png *.jpg *.jpeg)',
    '輸出到哪個資料夾':
        'どのフォルダーへ書き出すか',
    '輸出 %d 個 ZIP 到：%s':
        '%d 個の ZIP を書き出しました：%s',
    '打開資料夾':
        'フォルダーを開く',
    'song.json 的 slogan，最多 %d 字。\n樂曲資料夾模式：會存回那首歌的 register.json，下次自動帶出來。\n整個曲庫模式：每首用各自 register.json 裡存的評論。':
        'song.json の slogan、最大 %d 文字。\n曲フォルダーモード：その曲の register.json に保存され、次回から自動で入ります。\nライブラリ全体モード：曲ごとに自分の register.json のコメントを使います。',
    'Hiraeth 歌曲包':
        'Hiraeth 歌曲パッケージ',
    '只輸出目前開著的譜面（要在製譜器裡用）':
        '今開いている譜面だけ（譜面エディターから使います）',
    'REAL 等級 1～3.5，每格 0.5':
        'REAL のレベルは 1〜3.5、刻みは 0.5',
    '請填曲名。':
        '曲名を入力してください。',
    '請選輸出資料夾。':
        '書き出し先フォルダーを選んでください。',
    '有做轉換的：':
        '変換したもの：',
    '略過：':
        'スキップ：',
    '這個資料夾沒有 register.json，不是樂曲資料夾。':
        'このフォルダーには register.json がないため、曲フォルダーではありません。',
    '找不到曲庫資料夾。':
        'ライブラリフォルダーが見つかりません。',
    '失敗 %d 包：':
        '%d パッケージが失敗：',
    '・…另外 %d 包':
        '・…ほか %d パッケージ',
    '曲庫裡沒有「%s」':
        'ライブラリに「%s」がありません',
    'register.json 讀不出來':
        'register.json を読めません',
    '沒有任何難度':
        '難易度が一つもありません',
    '沒有可以復原的動作':
        '元に戻せる操作はありません',
    '曲名不能空白':
        '曲名は空にできません',
    '修改「%s」的曲目資料':
        '「%s」の曲情報を編集',
    '難度不存在':
        '難易度が存在しません',
    '難度名稱不能空白':
        '難易度名は空にできません',
    '等級要在 1～99':
        'レベルは 1〜99 の範囲です',
    '修改「%s」的難度 %s':
        '「%s」の難易度 %s を編集',
    '調整「%s」的難度順序':
        '「%s」の難易度の並び順を変更',
    '至少要留一個難度；要整首拿掉請用「刪除曲目」':
        '難易度は最低 1 つ残してください。曲ごと消すなら「曲を削除」を使ってください',
    '刪除「%s」的難度 %s':
        '「%s」の難易度 %s を削除',
    '刪除「%s」':
        '「%s」を削除',
    '分類名稱不合格（不能空白、不能叫 ALL、最多 40 字、不能有 \\/:*?"<>|）':
        'カテゴリー名が不正です（空は不可、ALL は不可、40 文字まで、\\/:*?"<>| は使えません）',
    '已經有這個分類':
        'このカテゴリーはすでにあります',
    '新增分類「%s」':
        'カテゴリー「%s」を追加',
    '這個資料夾沒有 register.json，不是樂曲資料夾':
        'このフォルダーには register.json がないため、曲フォルダーではありません',
    '這首已經在曲庫裡了':
        'この曲はすでにライブラリにあります',
    '匯入「%s」':
        '「%s」をインポート',
    '找不到曲庫資料夾：%s':
        'ライブラリフォルダーが見つかりません：%s',
    '「%s」的 register.json 讀不出來':
        '「%s」の register.json を読めません',
    '已經有叫「%s」的難度':
        '「%s」という難易度はすでにあります',
    '等級要是數字':
        'レベルは数値で入力してください',
    'ZIP 裡沒有 song.json，不是 Hiraeth 歌曲包':
        'ZIP に song.json がないため、Hiraeth 歌曲パッケージではありません',
    'song.json 的 charts 沒有可用的難度':
        'song.json の charts に使える難易度がありません',
    'ZIP 裡沒有 music.wav':
        'ZIP に music.wav がありません',
    '有拍子線落在曲末 2 秒之後':
        '曲の終わりより 2 秒以上あとに拍線があります',
    'ZIP 包得太多層了':
        'ZIP の階層が深すぎます',
    'ZIP 裡的歌曲包都匯不進來：\n%s':
        'ZIP の中の歌曲パッケージはどれもインポートできません：\n%s',
    '裡面沒有歌曲包':
        '中に歌曲パッケージがありません',
    '%s：譜面沒有音符':
        '%s：譜面にノーツがありません',
    '%s：鍵道超過 27（舊的 1 開始寫法）':
        '%s：レーンが 27 を超えています（1 から始まる古い書き方）',
    '%s：%d/%d 顆沒有音高（無背景音樂時會沒聲音）':
        '%s：%d/%d 個のノーツに音高がありません（BGM なしだと無音になります）',
    '曲庫裡已經有「%s」，無法搬回':
        'ライブラリにすでに「%s」があるため、戻せません',
    '「%s」的回收區資料夾不見了（%s），沒有東西可以搬回來。\n這筆歷史保留著，沒有動到曲庫。':
        '「%s」のごみ箱フォルダーが見つかりません（%s）。戻せるものがありません。\nこの履歴は残してあり、ライブラリには手を付けていません。',
    'Hiraeth 的 REAL 上限，比它高的都一樣':
        'Hiraeth の REAL の上限で、これより高くても同じ扱いです',
    '超過 15 被夾掉':
        '15 を超えるため切り詰め',
    '等級被 Hiraeth 的刻度壓掉：%s。REAL 只有 1／2／2.5／3／3.5，normal/hard/EX 只收 1~15。':
        'Hiraeth の目盛りでレベルが丸められました：%s。REAL は 1／2／2.5／3／3.5 のみ、normal/hard/EX は 1〜15 のみです。',
    '這份 Hiraeth 的 importer 是原版，會把帶按鍵音的包退件。要用這個模式得先改 tools/import_tools/importer.py。':
        'この Hiraeth の importer は配布版のままで、キー音付きのパッケージを弾きます。このモードを使うには先に tools/import_tools/importer.py を書き換えてください。',
    '找不到 Hiraeth 的 importer，沒辦法先確認它收不收按鍵音。':
        'Hiraeth の importer が見つからないため、キー音を受け付けるかどうか事前に確認できませんでした。',
    '%s 要是 PCM16 立體聲、取樣率 %s':
        '%s は PCM16 ステレオ、サンプリング周波数 %s である必要があります',
    '%s 超過 128MB':
        '%s が 128MB を超えています',
    '%s 超過 16MB':
        '%s が 16MB を超えています',
    '%s 沒有聲音資料':
        '%s に音声データがありません',
    '%s 讀不了：%s':
        '%s を読めません：%s',
    'preview.wav 超過 %d 秒':
        'preview.wav が %d 秒を超えています',
    '%s 含 DOCTYPE/ENTITY，importer 不收':
        '%s に DOCTYPE/ENTITY が含まれており、importer が受け付けません',
    '包裡有 %d 個檔案，超過 %d 個':
        'パッケージ内のファイルが %d 個あり、上限の %d 個を超えています',
    '把按鍵音修補套到這份 Hiraeth':
        'この Hiraeth にキー音対応のパッチを当てる',
    '套不上去':
        'パッチを当てられません',
    '按鍵音修補':
        'キー音パッチ',
    '讀不到 importer：%s':
        'importer を読めません：%s',
    '這份 importer 已經可以收按鍵音了，沒有改動。':
        'この importer はすでにキー音に対応しており、変更していません。',
    '在 importer 裡找到 %d 處擋力度的程式碼（預期剛好 1 處），不敢亂改。請自己比對 %s。':
        'importer の中にベロシティを弾くコードが %d か所見つかりました（ちょうど 1 か所のはずです）。安全のため変更していません。%s と自分で見比べてください。',
    '已套用（改的是第 %d 行）%s':
        '適用しました（変更したのは %d 行目）%s',
    '，原版備份到 %s':
        '、配布版は %s にバックアップしました',
    '，原本就有備份，沒有覆蓋':
        '、もともとバックアップがあったので上書きしていません',
    'ZIP 打不開：%s':
        'ZIP を開けません：%s',
    '%s：找不到譜面檔':
        '%s：譜面ファイルが見つかりません',
    '%s：找不到音訊檔':
        '%s：音声ファイルが見つかりません',
    '%s：找不到鋼琴音軌':
        '%s：ピアノトラックが見つかりません',
    '整包超過 300MB':
        'パッケージ全体が 300MB を超えています',
    'format 或 package_id 不合格':
        'format か package_id が不正です',
    'version 要是正整數':
        'version は正の整数である必要があります',
    'xml 檔和 charts 對不上':
        'xml ファイルと charts が一致しません',
    '譜面超過 16MB':
        '譜面が 16MB を超えています',
    '根節點不是 music_score':
        'ルート要素が music_score ではありません',
    '譜面長度和音訊差超過 2 秒':
        '譜面の長さと音声の長さが 2 秒以上ずれています',
    'header 不合格（file_version=1、min_scale=1、max_scale=88）':
        'header が不正です（file_version=1、min_scale=1、max_scale=88）',
    '音符數不合格':
        'ノーツ数が不正です',
    '音符時間或音高不合格':
        'ノーツの時刻か音高が不正です',
    'track 不是 key_apiano1 或對不上':
        'track が key_apiano1 でないか、一致しません',
    '缺 BPM 或拍子資料':
        'BPM か拍データがありません',
    '內建鋼琴音源沒有準備好':
        '内蔵ピアノ音源の準備ができていません',
    '譜面沒有音高，算不出鋼琴音軌':
        '譜面に音高がないため、ピアノトラックを生成できません',
    '沒有可用的譜面':
        '使える譜面がありません',
    '沒有曲繪，用純色底':
        'ジャケットがないため、単色の背景にします',
    '多出不接受的檔案：':
        '受け付けられない余分なファイル：',
    'slogan（樂曲評論）不合格，最多 %d 字':
        'slogan（曲のコメント）が不正です。最大 %d 文字です',
    'charts 不合格':
        'charts が不正です',
    'music.wav 超過 128MiB':
        'music.wav が 128MiB を超えています',
    '音符欄位缺漏或不是整數':
        'ノーツの項目が欠けているか整数ではありません',
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide，手 0／1）':
        'ノーツの種類／手／key_kind が不正です（Tap、Long、Slide のみ、手は 0／1）',
    '音符 index 沒有從 0 連號':
        'ノーツの index が 0 から連番になっていません',
    'sub_note 力度不是 0':
        'sub_note の velocity が 0 ではありません',
    'sub_note 時間或音高不合格':
        'sub_note の時刻か音高が不正です',
    '不支援 %d 聲道的音訊':
        '%d チャンネルの音声には対応していません',
    '測試用難度':
        'テスト用の難易度',
    '%s：沒有 register.json，略過':
        '%s：register.json がないためスキップ',
    '處理長條尾端（間距 %d ms）%d 條':
        'ロングの終端を処理しました（間隔 %d ms）：%d 本',
    '沒有背景音樂：用內建音源把譜面算成鋼琴音軌':
        'BGM なし：内蔵音源で譜面をピアノトラックとして生成しました',
    '音訊比譜面短，尾端補 %.1f 秒靜音':
        '音声が譜面より短いため、末尾に %.1f 秒の無音を足しました',
    '%d 顆顫音改成長押':
        'トリル %d 個をロングに変更しました',
    '拿掉 %d 顆自動彈的音符':
        '自動演奏のノーツ %d 個を取り除きました',
    '沒通過 Hiraeth 的檢查：':
        'Hiraeth の検査を通りませんでした：',
    '缺少 ':
        '不足 ',
    'slogan 有 Shift_JIS 編不了的字':
        'slogan に Shift_JIS で表せない文字があります',
    ' 不合格':
        ' が不正です',
    '%s 等級 %r 超出範圍':
        '%s のレベル %r が範囲外です',
    'music.wav 要是 PCM16 雙聲道 16000/22050/44100/48000Hz':
        'music.wav は PCM16 ステレオ、16000/22050/44100/48000Hz である必要があります',
    '鍵範圍不合格':
        '鍵の範囲が不正です',
    '%s / %s：%s，略過':
        '%s / %s：%s のためスキップ',
    '沒有背景音樂：用鋼琴音軌 %s':
        'BGM なし：ピアノトラック %s を使用',
    '疊上鋼琴音軌 %s':
        'ピアノトラック %s を重ねました',
    '疊上內建音源算的鋼琴音軌':
        '内蔵音源で生成したピアノトラックを重ねました',
    ' 有 Shift_JIS 編不了的字':
        ' に Shift_JIS で表せない文字があります',
    '%s 找不到譜面檔，略過':
        '%s は譜面ファイルが見つからないためスキップ',
    '混音後整體降 %.1f dB 避免破音':
        'クリップを避けるため、ミックス全体を %.1f dB 下げました',
    'Hiraeth 的匯入規則比 PAN 嚴格：<b>不發按鍵音</b>（聲音全部來自音樂檔，所以會把背景音樂和鋼琴音軌疊在一起）、顫音會改成長押、Soft／Staccato 改成 Tap、自動彈的音符拿掉、難度只有 normal／hard／expert／real（real 等級 1～3.5）。同一首歌每次輸出的 package_id 都一樣、版本號會遞增，重新匯入就是更新。':
        'Hiraeth の取り込み規則は PAN より厳しく、<b>キー音を鳴らしません</b>（音はすべて音楽ファイルから出るので、BGM とピアノトラックを混ぜます）。トリルはロングになり、Soft／Staccato は Tap になり、自動演奏のノーツは取り除かれ、難易度は normal／hard／expert／real だけです（real のレベルは 1〜3.5）。同じ曲の package_id は毎回同じで、バージョン番号が上がるので、入れ直せば更新になります。',
    '語言':
        '言語',
    '開頭補了空白小節（%.2f 秒），音訊前面補等長靜音':
        '曲頭に空の小節（%.2f 秒）を足し、音声の先頭にも同じ長さの無音を足しました',
    ' 小節':
        ' 小節',
    '開頭留白':
        '曲頭の余白',
    '第一顆音符前面至少空幾小節：不夠就在譜面前面補空白小節，音訊前面補等長的靜音（JSON 原檔不動）。\n量過官方 682 份譜：第一顆音符中位數就落在第 4 拍，也就是空一整小節。\n0 = 照譜面原樣輸出。':
        '最初のノーツの前に最低これだけ空の小節を置きます：足りなければ譜面の前に空の小節を足し、音声の先頭にも同じ長さの無音を足します（JSON の原本は変更しません）。\n公式の 682 譜面を計測したところ、最初のノーツの中央値はちょうど 4 拍目、つまり 1 小節まるごと空いていました。\n0 = 譜面をそのまま書き出します。',
    '批量匯入（掃描整個資料夾）…':
        '一括インポート（フォルダーを丸ごと調べる）…',
    '批量匯入':
        '一括インポート',
    '批量匯入 %d 首　（已通知遊戲重整）':
        '%d 曲を一括インポートしました　（ゲームに再読み込みを通知しました）',
    '更新製譜器':
        '譜面エディターを更新',
    '更新遊戲':
        'ゲームを更新',
    '更新曲庫':
        'ライブラリを更新',
    '更新 ▾':
        '更新 ▾',
    '更新製譜器（選新的 exe／ZIP）…':
        '譜面エディターを更新（新しい exe／ZIP を選ぶ）…',
    '更新遊戲（選新的 build 資料夾／ZIP）…':
        'ゲームを更新（新しい build フォルダー／ZIP を選ぶ）…',
    '更新曲庫（選曲庫資料夾／ZIP）…':
        'ライブラリを更新（ライブラリフォルダー／ZIP を選ぶ）…',
    '新版在哪裡？':
        '新しいバージョンはどこにありますか？',
    '選檔案（ZIP／exe）':
        'ファイルを選ぶ（ZIP／exe）',
    '選資料夾':
        'フォルダーを選ぶ',
    '更新了 %d 首（同名的整首換掉，其他保留）。':
        '%d 曲を更新しました（同名の曲は丸ごと差し替え、それ以外はそのまま）。',
    '更新檔 (*.zip *.exe)':
        '更新ファイル (*.zip *.exe)',
    '已更新製譜器：\n%s\n\n舊版改名保留在同一個資料夾。':
        '譜面エディターを更新しました：\n%s\n\n旧版は名前を変えて同じフォルダーに残してあります。',
    '先用「遊戲位置…」指定現在的遊戲。':
        '先に「ゲームの場所…」で今のゲームを指定してください。',
    '遊戲正開著，請先關掉再更新。':
        'ゲームが起動中です。閉じてから更新してください。',
    '已更新遊戲：換了 %d 個檔案。\n保留：%s':
        'ゲームを更新しました：%d 個のファイルを差し替えました。\n残したもの：%s',
    '先在曲庫管理選一份曲庫。':
        '先にライブラリ管理でライブラリを 1 つ選んでください。',
    '更新失敗：%s':
        '更新に失敗：%s',
    '（無）':
        '（なし）',
    '這個資料夾裡找不到任何樂曲（沒有 register.json）':
        'このフォルダーに曲が見つかりません（register.json のあるものがありません）',
    '更新曲庫（%d 首）':
        'ライブラリを更新（%d 曲）',
    '批量匯入 %d 首':
        '%d 曲を一括インポート',
    '找不到遊戲資料夾':
        'ゲームフォルダーが見つかりません',
    '這不是資料夾也不是 ZIP：%s':
        'フォルダーでも ZIP でもありません：%s',
    '裡面找不到製譜器的 exe（檔名要有 Chart Maker）':
        '中に譜面エディターの exe が見つかりません（ファイル名に Chart Maker が必要です）',
    '不知道要裝到哪裡：先用「製譜器位置…」指定現在那一支':
        'インストール先が分かりません：先に「譜面エディターの場所…」で今のものを指定してください',
    '裡面找不到遊戲 build（要有 exe 和同名的 _Data 資料夾）':
        '中にゲームの build が見つかりません（exe と同名の _Data フォルダーが必要です）',
    '套用到勾選的':
        'チェックしたものに適用',
    '選擇要掃描的資料夾':
        '調べるフォルダーを選択',
    '來源資料夾':
        '元のフォルダー',
    '全選':
        'すべて選択',
    '全不選':
        'すべて解除',
    '只選新的':
        '新しいものだけ',
    '全部設為':
        'すべてを次に設定',
    '曲目':
        '曲',
    '來源':
        '元',
    '掃到 %d 首，勾選 %d 首':
        '%d 曲見つかり、%d 曲にチェックを入れました',
    '樂曲資料夾':
        '曲フォルダー',
    'Hiraeth ZIP':
        'Hiraeth ZIP',
    '曲庫已有同名，會另存一份':
        'ライブラリに同名があるため、別に保存します',
    'Hiraeth 套件':
        'Hiraeth ツールキット',
    '把歌曲包匯入 PAN 歌曲管理版、看它的曲庫、管它的遊戲。':
        '歌曲パッケージを PAN 歌曲管理版にインポートし、そのライブラリを見て、そのゲームを操作します。',
    '強制停止遊戲':
        'ゲームを強制終了',
    '遊戲沒有在跑':
        'ゲームは起動していません',
    '強制停止遊戲？\n\n沒存的成績會不見（一般情況請在遊戲裡正常離開）。':
        'ゲームを強制終了しますか？\n\n保存していない成績は失われます（普段はゲーム内から終了してください）。',
    '已強制停止遊戲':
        'ゲームを強制終了しました',
    '停不掉，可能已經關了':
        '止められませんでした。すでに閉じているかもしれません',
    '響度 %.1f LUFS → %.1f（%+.1f dB，峰值 %.1f dBTP）':
        'ラウドネス %.1f LUFS → %.1f（%+.1f dB、ピーク %.1f dBTP）',
    '這首離目標還差 %.1f LU：要再大聲就得壓低峰值 %.1f dB，已經超出自動處理的範圍':
        'この曲は目標まであと %.1f LU：さらに上げるにはピークを %.1f dB 下げる必要があり、自動処理の範囲を超えています',
    '沒有調整響度：%s':
        'ラウドネスは変更していません：%s',
    '這不是 Hiraeth 套件資料夾（找不到 SONG_MANAGER.bat）':
        'これは Hiraeth ツールキットのフォルダーではありません（SONG_MANAGER.bat が見つかりません）',
    'Hiraeth 的遊戲正開著，匯入前要先停止':
        'Hiraeth のゲームが起動中です。インポートの前に停止してください',
    'Hiraeth 的遊戲正開著，刪除前要先停止':
        'Hiraeth のゲームが起動中です。削除の前に停止してください',
    'Hiraeth 管理器太久沒回應':
        'Hiraeth マネージャーが応答しません（タイムアウト）',
    'Hiraeth 管理器沒有回應':
        'Hiraeth マネージャーから返事がありません',
    '選擇 Hiraeth 套件資料夾':
        'Hiraeth ツールキットのフォルダーを選択',
    '讀取 Hiraeth 曲庫…':
        'Hiraeth のライブラリを読み込み中…',
    '選擇歌曲資料夾（有 song.json）':
        '曲フォルダーを選択（song.json があるもの）',
    'Hiraeth 的遊戲正開著，管理器會鎖住不給改。要強制停止再繼續嗎？':
        'Hiraeth のゲームが起動中で、マネージャーがロックされています。強制終了して続けますか？',
    '套件位置':
        'ツールキットの場所',
    '開啟 Hiraeth 管理器':
        'Hiraeth マネージャーを開く',
    '匯入歌曲包（ZIP）…':
        '歌曲パッケージ（ZIP）をインポート…',
    '匯入歌曲資料夾…':
        '曲フォルダーをインポート…',
    '從 Hiraeth 刪除選取':
        '選んだ曲を Hiraeth から削除',
    '強制停止 Hiraeth 遊戲':
        'Hiraeth のゲームを強制終了',
    '版本':
        'バージョン',
    '還沒指定 Hiraeth 套件：按「選擇…」選解壓好的資料夾（裡面有 SONG_MANAGER.bat）':
        'Hiraeth ツールキットが未指定です：「参照…」で展開済みのフォルダー（SONG_MANAGER.bat があるもの）を選んでください',
    'Hiraeth 裡有 %d 首':
        'Hiraeth には %d 曲あります',
    '匯入 %d 個歌曲包…':
        '%d 個の歌曲パッケージをインポート中…',
    '刪除 %d 首…':
        '%d 曲を削除中…',
    'Hiraeth 的遊戲沒有在跑':
        'Hiraeth のゲームは起動していません',
    '強制停止 Hiraeth 的遊戲？沒存的成績會不見。':
        'Hiraeth のゲームを強制終了しますか？保存していない成績は失われます。',
    '已停止：%s':
        '停止しました：%s',
    '從 Hiraeth 刪掉這 %d 首？\n（它自己會留備份）':
        'この %d 曲を Hiraeth から削除しますか？\n（Hiraeth 側にバックアップが残ります）',
    '（沒有正在跑的）':
        '（起動中のものはありません）',
    '一鍵同步曲庫':
        'ライブラリをワンボタン同期',
    '開始同步':
        '同期を開始',
    '全部重來':
        '全部やり直す',
    '同步曲庫…':
        'ライブラリを同期中…',
    '一鍵同步曲庫 → Hiraeth':
        'ライブラリを同期 → Hiraeth',
    '找不到 nos-clone 的曲庫，先在曲庫管理選一份。':
        'nos-clone のライブラリが見つかりません。先にライブラリ管理で 1 つ選んでください。',
    '要同步 %d 首，%d 首沒改過會略過。\n\n輸出成 Hiraeth 歌曲包再匯入，同一首是更新不會變兩份。':
        '%d 曲を同期します。変更のない %d 曲はスキップします。\n\nHiraeth 歌曲パッケージとして書き出してからインポートするので、同じ曲は更新され、二重にはなりません。',
    '沒有需要同步的曲目':
        '同期が必要な曲はありません',
    '同步完成：匯入 %d 首，沒改過略過 %d 首。':
        '同期完了：%d 曲をインポート、変更のない %d 曲をスキップしました。',
    '（中途取消，已經做完的那幾首有匯入）':
        '（途中で中止しました。すでに作り終えた曲はインポートされています）',
    '失敗：':
        '失敗：',
    '匯入 Hiraeth…':
        'Hiraeth へインポート中…',
    '一鍵同步曲庫（雙向）':
        'ライブラリをワンボタン同期（双方向）',
    '兩邊互補，不會刪任何東西：\n・送去 Hiraeth：%d 首（%d 首沒改過會略過）\n・搬回 nos-clone：%d 首（Hiraeth 有、這邊沒有的）':
        '互いに足りない分を補い、何も削除しません：\n・Hiraeth へ送る：%d 曲（変更のない %d 曲はスキップ）\n・nos-clone へ戻す：%d 曲（Hiraeth にあってこちらにないもの）',
    '同步完成：送去 Hiraeth %d 首，搬回 nos-clone %d 首，沒改過略過 %d 首。':
        '同期完了：Hiraeth へ %d 曲、nos-clone へ %d 曲、変更のない %d 曲はスキップしました。',
    'Hiraeth 裡找不到這首的檔案：%s':
        'この曲のファイルが Hiraeth にありません：%s',
    'Hiraeth 裡找不到這首的音樂檔：%s':
        'この曲の音楽ファイルが Hiraeth にありません：%s',
    'Hiraeth 裡找不到這首的譜面：%s':
        'この曲の譜面が Hiraeth にありません：%s',
    'Nos-clone':
        'Nos-clone',
    'Hiraeth（PAN 歌曲管理版）':
        'Hiraeth（PAN 歌曲管理版）',
    '選 Nos-clone 或 Hiraeth 開始玩，也可以在那裡強制停止。':
        'Nos-clone か Hiraeth を選んで遊べます。強制終了もここからできます。',
    '這個資料夾裡的遊戲':
        'このフォルダーにあるゲーム',
    '第四格指定的那份套件':
        '4 枚目のカードで指定したツールキット',
    '啟動':
        '起動',
    '強制停止':
        '強制終了',
    '還沒指定 Hiraeth 套件（在第四格裡選）':
        'Hiraeth ツールキットが未指定です（4 枚目のカードで選んでください）',
    '還沒指定遊戲：按首頁的「遊戲位置…」':
        'ゲームが未指定です：ホーム画面の「ゲームの場所…」で設定してください',
    '強制停止 %s？\n\n沒存的成績會不見（一般情況請在遊戲裡正常離開）。':
        '%s を強制終了しますか？\n\n保存していない成績は失われます（普段はゲーム内から終了してください）。',
    '已強制停止 %s':
        '%s を強制終了しました',
    '● 執行中：%s':
        '● 実行中：%s',
    '● 執行中　':
        '● 実行中　',
    '這份 Hiraeth 套件不完整，開不了遊戲':
        'この Hiraeth ツールキットは不完全で、ゲームを起動できません',
    '修剪結尾空白（%d 首）':
        '末尾の空白を詰める（%d 曲）',
    '修剪結尾空白':
        '末尾の空白を詰める',
    '修剪結尾空白…':
        '末尾の空白を詰める…',
    '遊戲走到譜面的「曲終」才結束，曲終設得比最後一顆音符（和最後的聲音）晚太多，玩完就要對著空畫面等。這裡把曲終收到內容結束後一小段，音訊也可以跟著剪短（原檔會備份）。':
        'ゲームは譜面の「曲の終了時刻」まで進んで終わるので、終了時刻が最後のノーツ（と最後の音）よりずっとあとに設定されていると、弾き終わってから何もない画面を眺めて待つことになります。ここでは終了時刻を中身の終わりの少しあとまで詰め、音声も合わせて短くできます（元のファイルはバックアップします）。',
    '同時剪短音訊檔':
        '音声ファイルも一緒に短くする',
    '原檔會先備份到 UserSongs_editor/audio_backup。無背景音樂的曲子不會動。':
        '元のファイルは UserSongs_editor/audio_backup にバックアップします。BGM のない曲には手を付けません。',
    '重新掃描':
        'もう一度調べる',
    '修剪勾選的':
        'チェックしたものを詰める',
    '空白超過':
        '空白が',
    '才算；修剪後留':
        'を超えるものを対象にし、詰めたあとは',
    '收尾':
        'を残す',
    '曲終':
        '終了時刻',
    '最後的內容':
        '最後の中身',
    '空白':
        '空白',
    '音訊長度':
        '音声の長さ',
    '%d 首結尾空白超過 %.1f 秒':
        '%d 曲が末尾に %.1f 秒を超える空白',
    '修好 %d 首（其中 %d 首連音訊一起剪短）。\n可以按曲庫管理的「復原上一步」還原譜面；音訊原檔在 UserSongs_editor/audio_backup。':
        '%d 曲を修正しました（うち %d 曲は音声も短くしました）。\nライブラリ管理の「直前の操作を元に戻す」で譜面を戻せます。元の音声は UserSongs_editor/audio_backup にあります。',
    '兩種結尾空白都會抓：譜面的「曲終」拖在最後一顆音符後面（玩完要對著空畫面等），還有音訊檔尾巴那一大段靜音（檔案變大，Hiraeth 輸出也會卡在「譜面長度和音訊差 2 秒」）。曲終只會往前收，音訊會留到曲終之後一小段，原檔先備份。':
        '2 種類の末尾の空白を見つけます：譜面の「曲の終了時刻」が最後のノーツよりずっとあとにある場合（弾き終わってから何もない画面を待つことになります）と、音声ファイルの末尾にある長い無音（ファイルが大きくなり、Hiraeth への書き出しも「譜面の長さと音声が 2 秒ずれている」で止まります）。終了時刻は前へ詰めるだけで後ろへは動かさず、音声は終了時刻の少しあとまで残し、元のファイルはバックアップします。',
    '原檔（譜面與音訊）會先備份到 UserSongs_editor/tail_trim_backup。無背景音樂的曲子不會動音訊。':
        '元のファイル（譜面と音声）は UserSongs_editor/tail_trim_backup にバックアップします。BGM のない曲は音声に手を付けません。',
    '音訊空白':
        '音声の空白',
    '修好 %d 首（其中 %d 首連音訊一起剪短）。\n原本的譜面與音訊備份在 UserSongs_editor/tail_trim_backup。':
        '%d 曲を修正しました（うち %d 曲は音声も短くしました）。\n元の譜面と音声は UserSongs_editor/tail_trim_backup にあります。',
    '新手教學：結尾在跑字幕，預設不修':
        'チュートリアル：最後に字幕が流れるため、既定では詰めません',
    '（其中 %d 首是新手教學，預設沒勾）':
        '（うち %d 曲はチュートリアルで、既定ではチェックを外しています）',
    '連尾奏一起剪':
        '後奏も切る',
    '最後一顆音符之後就沒事做了，但音樂常常還在放。勾了會從那裡收尾（音樂淡出），沒勾只剪掉沒聲音的部分。':
        '最後のノーツより後はもう弾くものがありませんが、音楽はまだ鳴り続けていることがよくあります。チェックするとそこで終わらせます（音楽はフェードアウト）。外すと無音の部分だけを詰めます。',
    '音符後':
        'ノーツの後',
    '結尾多出 %.1f 秒沒事做，剪掉':
        '末尾の何もしない %.1f 秒を切り詰めました',
    '從 ZIP 開啟遊戲':
        'ZIP からゲームを開く',
    '從 ZIP 開啟遊戲…':
        'ZIP からゲームを開く…',
    '選一份打包好的遊戲（ZIP 或資料夾），裝到 NosMania 旁邊，順便認出裡面有沒有製譜器和曲庫。':
        'パッケージ済みのゲーム（ZIP かフォルダー）を選んで NosMania の隣にインストールし、中に譜面エディターとライブラリが入っているかどうかも調べます。',
    '遊戲包 (*.zip)':
        'ゲームパッケージ (*.zip)',
    '裝到哪個資料夾':
        'どのフォルダーにインストールするか',
    '裝好了：%s':
        'インストールしました：%s',
    '・遊戲：%s':
        '・ゲーム：%s',
    '・製譜器：%s':
        '・譜面エディター：%s',
    '・製譜器：沒有（可以自己複製一支進去）':
        '・譜面エディター：なし（自分で 1 つコピーして入れられます）',
    '・曲庫：%d 首':
        '・ライブラリ：%d 曲',
    '・曲庫：沒有（第一次啟動遊戲會問要不要用現在這份）':
        '・ライブラリ：なし（初回起動時に、今管理しているものを使うか尋ねます）',
    '安裝失敗：%s':
        'インストールに失敗：%s',
    '選一份打包好的遊戲（ZIP 或資料夾），裝到 NosMania 旁邊，順便認出裡面有沒有製譜器和曲庫。NosMania 旁邊放著的遊戲包會自己找到。':
        'パッケージ済みのゲーム（ZIP かフォルダー）を選んで NosMania の隣にインストールし、中に譜面エディターとライブラリが入っているかどうかも調べます。NosMania の隣に置いたゲームパッケージは自動で見つかります。',
    '裝這一包':
        'このパッケージをインストール',
    '選別的…':
        '別のものを選ぶ…',
    'NosMania 旁邊找到一包遊戲：\n%s\n\n要裝這一包嗎？':
        'NosMania の隣にゲームパッケージが見つかりました：\n%s\n\nこれをインストールしますか？',
    '從這包裝起來：%s':
        'このパッケージからインストール：%s',
    '裝到這裡？\n%s':
        'ここにインストールしますか？\n%s',
    '旁邊有還沒解壓的遊戲包：%s':
        '隣に展開していないゲームパッケージがあります：%s',
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide、Trill，手 0／1）':
        'ノーツの種類／手／key_kind が不正です（Tap、Long、Slide、Trill のみ、手は 0／1）',
    '%d 顆顫音照原樣保留':
        'トリル %d 個をそのまま残しました',
    'Hiraeth 的匯入規則比 PAN 嚴格：<b>不發按鍵音</b>（聲音全部來自音樂檔，所以會把背景音樂和鋼琴音軌疊在一起）、Soft／Staccato 改成 Tap、自動彈的音符拿掉、難度只有 normal／hard／expert／real（real 等級 1～3.5）。同一首歌每次輸出的 package_id 都一樣、版本號會遞增，重新匯入就是更新。':
        'Hiraeth の取り込み規則は PAN より厳しく、<b>キー音を鳴らしません</b>（音はすべて音楽ファイルから出るので、BGM とピアノトラックを混ぜます）。Soft／Staccato は Tap になり、自動演奏のノーツは取り除かれ、難易度は normal／hard／expert／real だけです（real のレベルは 1〜3.5）。同じ曲の package_id は毎回同じで、バージョン番号が上がるので、入れ直せば更新になります。',
    'nos-clone → Hiraeth（覆蓋）':
        'nos-clone → Hiraeth（上書き）',
    'Hiraeth → nos-clone（覆蓋）':
        'Hiraeth → nos-clone（上書き）',
    '單向覆蓋 ▾':
        '一方向で上書き ▾',
    '以一邊為準，把另一邊換成一樣的（同步是兩邊互補、不刪東西）':
        '片方を基準に、もう片方を同じ内容に置き換えます（同期は互いを補い、何も削除しません）',
    'nos-clone → Hiraeth（用 nos-clone 覆蓋）…':
        'nos-clone → Hiraeth（nos-clone で上書き）…',
    'Hiraeth → nos-clone（用 Hiraeth 覆蓋）…':
        'Hiraeth → nos-clone（Hiraeth で上書き）…',
    '覆蓋':
        '上書き',
    'nos-clone 曲庫裡的每一首都會重新輸出送到 Hiraeth，Hiraeth 裡同一首會被換掉（不管有沒有改過，所以會比同步久）。\n\nHiraeth 自己會留更新前的備份。':
        'nos-clone ライブラリの全曲を書き出し直して Hiraeth へ送り、Hiraeth 側の同じ曲を置き換えます（変更の有無に関係なく送るので、同期より時間がかかります）。\n\nHiraeth 側で更新前のバックアップが残ります。',
    '同時刪掉 Hiraeth 裡 nos-clone 沒有的曲目':
        'nos-clone にない曲を Hiraeth から削除する',
    'Hiraeth 裡的每一首都會搬回 nos-clone，nos-clone 裡同一首會被換掉。\n\n⚠ 這個方向會失真：Hiraeth 存的是簡化過的 XML，Soft／Staccato 會變 Tap、自動彈的音符會不見、長押是縮過的長度。被換掉的原本那份會搬到回收區，可以從曲庫管理的「復原上一步」或回收區拿回來。\n\n分類沿用原本那首的。新手教學一律不動。':
        'Hiraeth の全曲を nos-clone へ戻し、nos-clone 側の同じ曲を置き換えます。\n\n⚠ この方向は情報が落ちます：Hiraeth が保存しているのは簡略化した XML なので、Soft／Staccato は Tap になり、自動演奏のノーツは消え、ロングは短くされた長さになります。置き換えられた元のファイルはごみ箱へ移るので、ライブラリ管理の「直前の操作を元に戻す」かごみ箱から取り戻せます。\n\nカテゴリーは元の曲のものを引き継ぎます。チュートリアルには一切手を付けません。',
    '同時把 nos-clone 裡 Hiraeth 沒有的曲目搬到回收區':
        'Hiraeth にない曲を nos-clone からごみ箱へ移す',
    '覆蓋完成：送到 Hiraeth %d 首。':
        '上書き完了：Hiraeth へ %d 曲送りました。',
    '覆蓋完成：換掉 %d 首、新增 %d 首。':
        '上書き完了：%d 曲を置き換え、%d 曲を追加しました。',
    '刪掉 Hiraeth 多出來的 %d 首。':
        'Hiraeth に余分だった %d 曲を削除しました。',
    '%d 首 Hiraeth 沒有的搬到回收區。':
        'Hiraeth にない %d 曲をごみ箱へ移しました。',
    '還沒指定曲庫：先在首頁的「位置設定…」選曲庫資料夾，或從 ZIP 安裝一份。':
        'ライブラリが未指定です：ホーム画面の「場所の設定…」でライブラリフォルダーを選ぶか、ZIP からインストールしてください。',
    '連結曲庫失敗：%s':
        'ライブラリのリンクに失敗：%s',
    '這個遊戲資料夾裡有它自己的一份曲庫：\n%s\n\n和目前管理的不是同一份，遊戲會用它自己那份。要切換過去管理它嗎？':
        'このゲームフォルダーには自分のライブラリがあります：\n%s\n\n今管理しているものとは別で、ゲームは自分のほうを使います。そちらの管理に切り替えますか？',
    '安裝遊戲':
        'ゲームをインストール',
    '安裝曲庫':
        'ライブラリをインストール',
    '曲庫包 (*.zip)':
        'ライブラリパッケージ (*.zip)',
    'NosMania 旁邊找到一包曲庫：\n%s\n\n要裝這一包嗎？':
        'NosMania の隣にライブラリパッケージが見つかりました：\n%s\n\nこれをインストールしますか？',
    '曲庫要解壓到哪個資料夾（會在裡面放 UserSongs）':
        'ライブラリをどのフォルダーに展開するか（その中に UserSongs を置きます）',
    '裝好了：%s\n曲目 %d 首':
        'インストールしました：%s\n曲数 %d',
    '位置設定':
        '場所の設定',
    '三樣東西分開放，NosMania 記住位置去連結。沒指定的話，會找 NosMania 所在的資料夾和它下一層。':
        '3 つはそれぞれ別の場所に置き、NosMania が場所を覚えてつなぎます。指定しなければ、NosMania のあるフォルダーとその 1 つ下を探します。',
    '位置設定…':
        '場所の設定…',
    '遊戲、曲庫、製譜器分開放：在這裡指定各自的位置，或從 ZIP 安裝':
        'ゲーム・ライブラリ・譜面エディターは別々に置きます：ここでそれぞれの場所を指定するか、ZIP からインストールしてください',
    '・這包裡附了曲庫：%d 首':
        '・このパッケージにはライブラリが入っています：%d 曲',
    '・曲庫：還沒有，到「位置設定…」選一份或從 ZIP 安裝':
        '・ライブラリ：まだありません。「場所の設定…」で選ぶか ZIP からインストールしてください',
    '遊戲本體':
        'ゲーム本体',
    '製譜器':
        '譜面エディター',
    '執行檔 (*.exe)':
        '実行ファイル (*.exe)',
    '找不到製譜器。按首頁的「位置設定…」選製譜器 exe（或把 Nos Chart Maker 放在 NosMania 同一層）。':
        '譜面エディターが見つかりません。ホーム画面の「場所の設定…」で譜面エディターの exe を選んでください（または Nos Chart Maker を NosMania と同じ階層に置いてください）。',
    '製譜器要放在哪個資料夾':
        '譜面エディターをどのフォルダーに置くか',
    '先在「位置設定…」指定現在的遊戲。':
        '先に「場所の設定…」で今のゲームを指定してください。',
    '還沒指定遊戲：按首頁的「位置設定…」':
        'ゲームが未指定です：ホーム画面の「場所の設定…」で設定してください',
    '・曲庫：進遊戲時會連到 %s':
        '・ライブラリ：ゲーム起動時に %s へリンクします',
    '從 ZIP 安裝…':
        'ZIP からインストール…',
    '這不是遊戲 exe（旁邊要有同名的 _Data 資料夾）。':
        'これはゲームの exe ではありません（同名の _Data フォルダーが隣に必要です）。',
    '這個資料夾裡沒有 library.json，看起來不是曲庫。\n還是要用它當曲庫嗎？':
        'このフォルダーには library.json がなく、ライブラリには見えません。\nそれでもライブラリとして使いますか？',
    '從旁邊的 ZIP 安裝：%s':
        '隣にある ZIP からインストール：%s',
    '遊戲資料夾裡有一個叫 UserSongs 的檔案，無法建立連結':
        'ゲームフォルダーに UserSongs という名前のファイルがあるため、リンクを作成できません',
    '建立資料夾連結失敗：%s':
        'フォルダーのリンクを作成できません：%s',
    '這個資料夾不是曲庫（裡面沒有 library.json）':
        'このフォルダーはライブラリではありません（中に library.json がありません）',
    'ZIP 裡找不到曲庫（要有 library.json）':
        'ZIP にライブラリが見つかりません（library.json が必要です）',
    '解壓沒有完成（被取消了？）：%s':
        '展開が完了しませんでした（中止されましたか？）：%s',
    '這裡已經有一份曲庫：\n%s\n\n要把新的曲目併進去，請用「更新曲庫」。':
        'ここにはすでにライブラリがあります：\n%s\n\n新しい曲をそこへ取り込むには「ライブラリを更新」を使ってください。',
    '從 ZIP 安裝曲庫…':
        'ZIP からライブラリをインストール…',
    '還沒有曲庫。先選一份曲庫資料夾，或從曲庫 ZIP 安裝一份。':
        'ライブラリがまだありません。ライブラリフォルダーを選ぶか、ライブラリ ZIP からインストールしてください。',
    '原本的曲庫資料夾不見了：\n%s\n\n（例如重新 build 遊戲時整個資料夾被換掉）。先選一份曲庫，或從曲庫 ZIP 安裝一份。':
        '元のライブラリフォルダーが見つかりません：\n%s\n\n（例えばゲームを build し直したときにフォルダーごと置き換わった場合などです）。ライブラリを選ぶか、ライブラリ ZIP からインストールしてください。',
    '資料夾不見了，按「位置設定…」重選':
        'フォルダーが見つかりません。「場所の設定…」で選び直してください',
    '連續的 soft 轉成滑奏 %d 條、顫音 %d 個':
        '連続する soft をスライド %d 本、トリル %d 個に変換しました',
    '處理長條尾端':
        'ロングの終端を処理',
    '勾選：長條尾端到同手下一顆至少留右邊的間距，同鍵道有別的音就裁短。\n不勾：長條完全照原樣輸出（同一個鍵上重疊的音也不處理）。':
        'チェックあり：ロングの終端から同じ手の次のノーツまで右の間隔を最低限空け、同じレーンに別の音があるロングは短くします。\nチェックなし：ロングはそのまま書き出します（同じ鍵の重なりも処理しません）。',
    '沒有處理長條尾端（照原樣輸出）':
        'ロングの終端を処理していません（そのまま書き出し）',
    '相容沒改過的 Hiraeth（要給別人匯入就勾著）':
        '改造していない Hiraeth と互換（他の人に渡すならオンのまま）',
    '相容性':
        '互換性',
    'REAL 等級 1～3（原版 Hiraeth 只收這三格）':
        'REAL レベル 1～3（オリジナルの Hiraeth はこの 3 つだけ）',
    '沒有勾：這個包只有 importer 改過的 Hiraeth 裝得進去。別人匯入會**整包**退件（REAL display level must be 1/2/3…），自己這台因為改過，匯得進去看不出問題。':
        'オフの場合：このパッケージは importer を改造した Hiraeth にしかインストールできません。他の人の環境では**パッケージ全体**が弾かれます（REAL display level must be 1/2/3…）。こちらは改造済みなので気づけません。',
    '沒有勾相容：對方的 importer 必須改過才匯得進去。':
        '互換オフ：相手の importer が改造済みでないとインストールできません。',
    '原版 Hiraeth 的 REAL 只有 1／2／3':
        'オリジナルの Hiraeth の REAL は 1／2／3 のみ',
    '相容模式不能保留按鍵音：原版 importer 只收 sub_note 力度 0 的包。':
        '互換モードではキー音を残せません：オリジナルの importer は sub_note の velocity が 0 のパッケージしか受け付けません。',
    '等級被 Hiraeth 的刻度壓掉：%s。%s，normal/hard/EX 只收 1~15。':
        'レベルが Hiraeth の目盛りに丸められました：%s。%s、normal/hard/EX は 1～15 のみです。',
    '音符類型／手／key_kind 不合格（只收 Tap、Long、Slide%s，手 0／1）':
        'ノーツ種別／手／key_kind が不正です（Tap、Long、Slide%s、手 0／1 のみ）',
    '、Trill':
        '、Trill',
    '原版 importer 的 REAL 只有 1／2／3':
        'オリジナルの importer の REAL は 1／2／3 のみ',
    'REAL 只有 1／2／2.5／3／3.5':
        'REAL は 1／2／2.5／3／3.5 のみ',
}
