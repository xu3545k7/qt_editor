# -*- coding: utf-8 -*-
"""轉譜前先分離鋼琴。

為什麼要有這一步，以及為什麼是 BS-Rofo-SW：曲庫 8 首實測（`<曲>_piano.wav` +
`<曲>_ele.wav` 相加當混音，對照 `raw/*.mid`、起音 ±50ms）——

    直接餵混音              29.3%
    demucs htdemucs_6s     35.5%
    BS-Rofo-SW             59.7%   ← 補回差距的 47%
    原本就乾淨的鋼琴軌      93.3%

這裡測的是接線：模組介面、安裝判斷、worker 參數、部署會不會把檔案帶過去。
真正的分離品質要跑模型，不在單元測試裡。
"""

import os
import shutil
import tempfile
import unittest

from qt_editor import ai_transcribe as AT
from qt_editor import piano_separate as PS


class InstallDetectionTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_an_empty_environment_has_no_separator(self):
        self.assertFalse(PS.is_installed(self.root))

    def test_a_truncated_download_does_not_count_as_installed(self):
        folder = PS.model_dir(self.root)
        os.makedirs(folder)
        open(os.path.join(folder, PS.CONFIG_NAME), 'w').close()
        with open(os.path.join(folder, PS.CKPT_NAME), 'wb') as fh:
            fh.write(b'x' * 1024)              # 下載到一半
        self.assertFalse(PS.is_installed(self.root), '大小不對就不算裝好')

    def test_the_right_size_counts_as_installed(self):
        folder = PS.model_dir(self.root)
        os.makedirs(folder)
        open(os.path.join(folder, PS.CONFIG_NAME), 'w').close()
        with open(os.path.join(folder, PS.CKPT_NAME), 'wb') as fh:
            fh.truncate(PS.CKPT_SIZE)
        self.assertTrue(PS.is_installed(self.root))


class ConfigTests(unittest.TestCase):
    def test_the_missing_sixth_stem_is_filled_in(self):
        """設定檔只列 5 個名字但 num_stems 是 6，少的那個是 Other。"""
        config = {'model': {'num_stems': 6},
                  'training': {'instruments': ['Bass', 'Drums', 'Vocals',
                                               'Guitar', 'Piano']}}
        names = PS.stem_names(config)
        self.assertEqual(len(names), 6)
        self.assertIn(PS.WANTED, names)
        self.assertEqual(names[-1], 'Other')

    def test_we_only_take_the_piano_stem(self):
        """加回別軌實測更差：Piano 60.4% / +Guitar 59.5% / +Other 47.0%。"""
        self.assertEqual(PS.WANTED, 'Piano')

    def test_the_packages_never_let_pip_resolve_torch(self):
        """自動解依賴會把 torch 降版，RTX 50 系列需要 cu128。"""
        self.assertNotIn('torch', [p.split('=')[0].split('>')[0] for p in PS.PACKAGES])


class WorkerWiringTests(unittest.TestCase):
    def test_the_worker_takes_the_separate_flags(self):
        import argparse
        import inspect

        from qt_editor import ai_transcribe_worker as W

        source = inspect.getsource(W.main)
        for flag in ('--separate', '--separate-out', '--env'):
            self.assertIn(flag, source, 'worker 少了 %s' % flag)
        self.assertTrue(hasattr(W, 'run_separation'))
        self.assertIsInstance(argparse.ArgumentParser(), argparse.ArgumentParser)

    def test_transcribe_passes_the_flag_through(self):
        import inspect
        self.assertIn('separate', inspect.signature(AT.transcribe).parameters)
        source = inspect.getsource(AT.transcribe)
        self.assertIn("'--separate'", source)
        self.assertIn('separator_installed', source,
                      '沒裝分離模型時不該加 --separate')

    def test_a_failed_separation_falls_back_to_the_original(self):
        """分離壞掉不該擋住轉譜——退回原始音檔，只是結果差。"""
        import inspect

        from qt_editor import ai_transcribe_worker as W
        source = inspect.getsource(W.run_separation)
        self.assertIn('return args.input', source)


class DeployTests(unittest.TestCase):
    def test_the_separator_files_travel_with_the_worker(self):
        """worker 在轉譜環境裡是獨立檔案，分離模組要跟著過去才 import 得到。"""
        root = tempfile.mkdtemp()
        try:
            AT.deploy_worker(root)
            self.assertTrue(os.path.isfile(os.path.join(root, 'worker.py')))
            self.assertTrue(os.path.isfile(os.path.join(root, 'piano_separate.py')))
            self.assertTrue(os.path.isfile(
                os.path.join(root, 'vendor', 'bs_roformer', 'bs_roformer.py')))
            self.assertTrue(os.path.isfile(
                os.path.join(root, 'vendor', 'bs_roformer', 'LICENSE')),
                '引用的程式碼要帶著授權')
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_spec_bundles_them(self):
        """打包之後也要帶得出去。"""
        spec = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'NostalgiaChartEditor.spec')
        with open(spec, encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('piano_separate.py', text)
        self.assertIn("qt_editor/vendor", text)


if __name__ == '__main__':
    unittest.main()


class ModelChoiceTests(unittest.TestCase):
    """兩個分離器可選。使用者：「留著，分離的時候可以選」。"""

    def test_both_models_are_offered(self):
        self.assertEqual(set(PS.MODELS), {'bsrofo', 'demucs'})
        for info in PS.MODELS.values():
            self.assertTrue(info['label'])
            self.assertTrue(info['note'], '每個都要講清楚代價')

    def test_the_default_is_the_one_that_measured_better(self):
        best = max(PS.MODELS, key=lambda k: PS.MODELS[k]['f1'])
        self.assertEqual(PS.DEFAULT_MODEL, best)
        self.assertEqual(PS.DEFAULT_MODEL, 'bsrofo')

    def test_the_separate_entry_point_routes_by_model(self):
        import inspect
        source = inspect.getsource(PS.separate)
        self.assertIn('_separate_demucs', source)
        self.assertIn('_separate_bsrofo', source)

    def test_demucs_takes_only_the_piano_stem(self):
        import inspect
        source = inspect.getsource(PS._separate_demucs)
        self.assertIn("'piano'", source.lower())
        self.assertIn(PS.DEMUCS_MODEL, source + inspect.getsource(PS))

    def test_the_model_name_reaches_the_worker(self):
        import inspect

        from qt_editor import ai_transcribe_worker as W
        self.assertIn('--separate-model', inspect.getsource(W.main))
        self.assertIn('model=', inspect.getsource(W.run_separation))
        self.assertIn("'--separate-model'", inspect.getsource(AT.transcribe))


class DialogTests(unittest.TestCase):
    """選擇視窗。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_it_defaults_to_the_better_model(self):
        from qt_editor.ai_transcribe_dialog import SeparateDialog
        dlg = SeparateDialog(None, {'bsrofo': True, 'demucs': True})
        self.assertEqual(dlg.choice(), PS.DEFAULT_MODEL)

    def test_skipping_returns_nothing(self):
        from qt_editor.ai_transcribe_dialog import SeparateDialog
        dlg = SeparateDialog(None, {'bsrofo': True, 'demucs': True})
        dlg._skip.setChecked(True)
        self.assertEqual(dlg.choice(), '')

    def test_a_piano_stem_defaults_to_no_separation(self):
        """檔名看起來已經是鋼琴分軌就預設不分離。

        以前這種檔案**整個對話框都不開**，於是轉譜模型也跟著選不到——使用者：
        「沒看到轉譜選項，只有分離器」。現在照樣開，只是「不分離」預設勾好。
        """
        from qt_editor.ai_transcribe_dialog import SeparateDialog
        dlg = SeparateDialog(None, {'bsrofo': True, 'demucs': True},
                             piano_already=True)
        self.assertTrue(dlg._skip.isChecked())
        self.assertEqual(dlg.choice(), '')
        self.assertTrue(dlg._models, '模型那一組還是要在')

    def test_an_uninstalled_model_is_still_offered(self):
        """沒裝的也要列出來（標「要先下載」），不然使用者不知道有這個選擇。"""
        from qt_editor.ai_transcribe_dialog import SeparateDialog
        dlg = SeparateDialog(None, {'bsrofo': False, 'demucs': False})
        self.assertEqual(len(dlg._buttons), 2)
