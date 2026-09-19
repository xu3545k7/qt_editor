"""測試不准寫使用者的偏好設定檔。

`settings.set()` 是立刻落盤的，而測試會大量呼叫它來擺弄各種開關——結果是跑完
一次測試套件，**使用者的偏好設定就被改掉了**。

實際發生過：`test_scale_tools` 把 `pitch_column_mode` 設成 'scale' 當作它的
預設值，使用者下次開啟編輯器就變成調性分色；而空白譜面偵測不到調性，
`_draw_scale_highlight` 直接 return —— 整片沒有分色，看起來像功能壞掉。
使用者的回報是「我剛進來 黑白分色沒套用 啥都沒套」。

在記憶體裡照常生效（測試要的是這個），只是不落盤。
"""

import json
import os
import unittest

from qt_editor import settings as settings_mod
from qt_editor.settings import settings


class SettingsStayOnDisk(unittest.TestCase):
    def snapshot(self):
        path = settings_mod._SETTINGS_FILE
        if not os.path.isfile(path):
            return None
        with open(path, encoding='utf-8') as f:
            return f.read()

    def test_setting_a_value_does_not_touch_the_file(self):
        before = self.snapshot()
        settings.set('pitch_column_mode', 'scale')
        settings.set('place_grid_mode', 'never')
        self.assertEqual(self.snapshot(), before,
                         '測試把使用者的設定檔改掉了')

    def test_the_value_still_applies_in_memory(self):
        settings.set('place_grid_mode', 'never')
        self.assertEqual(settings.get('place_grid_mode'), 'never',
                         '不落盤不代表不生效，測試要靠這個')

    def test_the_guard_is_on_while_testing(self):
        self.assertTrue(settings_mod._under_test())

    def test_the_env_var_can_force_a_write(self):
        old = os.environ.get('NOS_SETTINGS_WRITE')
        os.environ['NOS_SETTINGS_WRITE'] = '1'
        try:
            self.assertFalse(settings_mod._under_test())
        finally:
            if old is None:
                os.environ.pop('NOS_SETTINGS_WRITE', None)
            else:
                os.environ['NOS_SETTINGS_WRITE'] = old

    def test_the_shipped_defaults_are_what_a_new_user_gets(self):
        # 這幾個是使用者一開啟就會看到的東西，預設值不該被測試汙染
        self.assertEqual(settings_mod._DEFAULTS['pitch_column_mode'], 'blackwhite')
        self.assertEqual(settings_mod._DEFAULTS['place_grid_mode'], 'placement')

    def test_the_users_file_is_not_left_on_a_test_value(self):
        """檔案裡的值必須是合法的選項，不能是測試用的怪值。"""
        path = settings_mod._SETTINGS_FILE
        if not os.path.isfile(path):
            self.skipTest('沒有設定檔')
        with open(path, encoding='utf-8') as f:
            saved = json.load(f)
        mode = saved.get('pitch_column_mode', 'blackwhite')
        self.assertIn(mode, ('blackwhite', 'scale'))


if __name__ == '__main__':
    unittest.main()
