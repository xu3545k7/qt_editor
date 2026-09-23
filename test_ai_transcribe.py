"""AI 轉譜的安裝與子程序協定（不連網、不需要 torch：用假的 worker 代替）。"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from qt_editor import ai_transcribe as AT
from qt_editor import platform_support as P


FAKE_WORKER = r'''
import argparse, json, sys, time
p = argparse.ArgumentParser()
p.add_argument('--selftest', action='store_true')
p.add_argument('--input'); p.add_argument('--output'); p.add_argument('--checkpoint')
p.add_argument('--device', default='auto'); p.add_argument('--batch', type=int, default=0)
p.add_argument('--wav-out', dest='wav_out')
a = p.parse_args()
def emit(kind, **kw):
    kw['type'] = kind
    print(json.dumps(kw, ensure_ascii=False), flush=True)
mode = open(a.input + '.mode').read().strip() if a.input else 'ok'
if a.selftest:
    emit('done', torch='9.9', cuda='', device='cpu', device_name='')
    sys.exit(0)
print('套件自己印的一行', flush=True)
emit('log', message='音檔 1.0 秒')
if mode == 'slow':
    for i in range(200):
        emit('progress', stage='transcribe', done=i, total=200)
        time.sleep(0.05)
if mode == 'error':
    emit('error', message='讀不到這個音檔', trace='Traceback...')
    sys.exit(1)
if mode == 'crash':
    sys.exit(3)
for i in range(3):
    emit('progress', stage='transcribe', done=i + 1, total=3)
open(a.output, 'wb').write(b'MThd')
if a.wav_out:
    open(a.wav_out, 'wb').write(b'RIFF')
emit('done', output=a.output, notes=12, pedals=2, seconds=1.0, device='cpu', elapsed=0.1)
'''


class _FakeEnv(unittest.TestCase):
    """一個裝好了的假環境：python.exe 用目前的直譯器，worker 換成假的。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ai_transcribe_test_')
        self.worker_src = os.path.join(self.root, 'fake_worker.py')
        with open(self.worker_src, 'w', encoding='utf-8') as fh:
            fh.write(FAKE_WORKER)
        os.makedirs(os.path.dirname(AT.checkpoint_path(self.root)))
        open(AT.checkpoint_path(self.root), 'wb').close()
        with open(os.path.join(self.root, 'installed.json'), 'w', encoding='utf-8') as fh:
            json.dump({'env_version': AT.ENV_VERSION, 'device': 'cpu'}, fh)
        self._patches = [
            mock.patch.object(AT, 'python_exe', lambda root=None: sys.executable),
            mock.patch.object(AT, 'bundled_worker_source', lambda: self.worker_src),
        ]
        for p in self._patches:
            p.start()
        self.audio = os.path.join(self.root, '歌 名.wav')
        with open(self.audio, 'wb') as fh:
            fh.write(b'RIFF')
        self.logs, self.progress = [], []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _mode(self, mode):
        with open(self.audio + '.mode', 'w') as fh:
            fh.write(mode)

    def _run(self, cancel=None):
        out = AT.default_output_path(self.audio)
        return out, AT.transcribe(self.audio, out, cancel or threading.Event(),
                                  self.logs.append,
                                  lambda s, d, t: self.progress.append((s, d, t)),
                                  root=self.root)


class TranscribeProtocolTests(_FakeEnv):
    def test_success_writes_midi_and_reports(self):
        self._mode('ok')
        out, result = self._run()
        self.assertTrue(out.endswith('歌 名.ai.mid'))
        self.assertTrue(os.path.isfile(out))
        self.assertFalse(os.path.exists(out + '.part.mid'))
        self.assertEqual(result['notes'], 12)
        self.assertEqual(result['pedals'], 2)
        self.assertEqual(result['output'], out)
        self.assertIn(('transcribe', 3, 3), self.progress)
        # 非 JSON 的行與 log 訊息都進紀錄，中文不亂碼
        self.assertIn('套件自己印的一行', self.logs)
        self.assertIn('音檔 1.0 秒', self.logs)

    def test_worker_error_message_surfaces(self):
        self._mode('error')
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertEqual(str(ctx.exception), '讀不到這個音檔')
        self.assertFalse(os.path.exists(AT.default_output_path(self.audio)))

    def test_silent_crash_reports_exit_code(self):
        self._mode('crash')
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn('3', str(ctx.exception))

    def test_cancel_kills_worker_quickly(self):
        self._mode('slow')
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(AT.Cancelled):
            self._run(cancel)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertFalse(os.path.exists(AT.default_output_path(self.audio)))

    def test_non_wav_input_also_gets_a_wav(self):
        mp3 = os.path.join(self.root, 'song.mp3')
        with open(mp3, 'wb') as fh:
            fh.write(b'ID3')
        with open(mp3 + '.mode', 'w') as fh:
            fh.write('ok')
        out = AT.default_output_path(mp3)
        result = AT.transcribe(mp3, out, threading.Event(), self.logs.append,
                               lambda *a: None, root=self.root)
        wav = os.path.join(self.root, 'song.wav')
        self.assertEqual(result['wav'], wav)
        with open(wav, 'rb') as fh:
            self.assertEqual(fh.read(), b'RIFF')
        self.assertFalse(os.path.exists(wav + '.part.wav'))

    def test_existing_wav_is_not_overwritten(self):
        mp3 = os.path.join(self.root, 'song.mp3')
        with open(mp3, 'wb') as fh:
            fh.write(b'ID3')
        with open(mp3 + '.mode', 'w') as fh:
            fh.write('ok')
        wav = os.path.join(self.root, 'song.wav')
        with open(wav, 'wb') as fh:
            fh.write(b'mine')
        result = AT.transcribe(mp3, AT.default_output_path(mp3), threading.Event(),
                               self.logs.append, lambda *a: None, root=self.root)
        self.assertEqual(result['wav'], wav)
        with open(wav, 'rb') as fh:
            self.assertEqual(fh.read(), b'mine')

    def test_wav_input_reports_itself(self):
        self._mode('ok')
        _out, result = self._run()
        self.assertEqual(result['wav'], self.audio)

    def test_empty_file_gets_a_clear_message(self):
        # 下載失敗留下的 0 位元組檔案：不要讓 libsndfile 說「檔案不存在」
        self._mode('ok')
        open(self.audio, 'wb').close()
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn('0 位元組', str(ctx.exception))

    def test_refuses_when_not_installed(self):
        os.remove(os.path.join(self.root, 'installed.json'))
        with self.assertRaises(RuntimeError):
            self._run()

    def test_worker_is_refreshed_from_editor_copy(self):
        self._mode('ok')
        with open(AT.worker_path(self.root), 'w') as fh:
            fh.write('raise SystemExit(9)')        # 舊版 worker
        self._run()
        with open(AT.worker_path(self.root), encoding='utf-8') as fh:
            self.assertIn("emit('done'", fh.read())


class InstalledInfoTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ai_transcribe_test_')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, version):
        os.makedirs(os.path.dirname(AT.python_exe(self.root)))
        open(AT.python_exe(self.root), 'wb').close()
        os.makedirs(os.path.dirname(AT.checkpoint_path(self.root)))
        open(AT.checkpoint_path(self.root), 'wb').close()
        with open(os.path.join(self.root, 'installed.json'), 'w') as fh:
            json.dump({'env_version': version}, fh)

    def test_current_version_counts(self):
        self._write(AT.ENV_VERSION)
        self.assertTrue(AT.is_installed(self.root))

    def test_old_version_is_not_installed(self):
        self._write(AT.ENV_VERSION - 1)
        self.assertFalse(AT.is_installed(self.root))

    def test_missing_checkpoint_is_not_installed(self):
        self._write(AT.ENV_VERSION)
        os.remove(AT.checkpoint_path(self.root))
        self.assertFalse(AT.is_installed(self.root))


class _PlatformPatch:
    """換掉 platform_support 的平台常數（ai_transcribe 每次都讀屬性，不讀快照）。"""

    def __init__(self, system='darwin'):
        self.system = system

    def __enter__(self):
        self._saved = (P.IS_WINDOWS, P.IS_MAC, P.IS_LINUX)
        P.IS_WINDOWS = self.system.startswith('win')
        P.IS_MAC = self.system == 'darwin'
        P.IS_LINUX = not P.IS_WINDOWS and not P.IS_MAC
        return self

    def __exit__(self, *_exc):
        P.IS_WINDOWS, P.IS_MAC, P.IS_LINUX = self._saved
        return False


class InstallPythonTests(unittest.TestCase):
    """Windows 下載 embeddable，mac／Linux 拿系統的 Python 建 venv。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ai_transcribe_test_')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_embeddable_pth_gets_site_enabled(self):
        src = os.path.join(self.root, 'src.zip')
        with zipfile.ZipFile(src, 'w') as zf:
            zf.writestr('python.exe', b'')
            zf.writestr('python311._pth', 'python311.zip\n.\n\n# comment\n#import site\n')

        def fake_download(url, dest, cancel, progress, stage, expected_size=0):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)

        with _PlatformPatch('win32'), mock.patch.object(AT, '_download', fake_download):
            AT._install_python(self.root, threading.Event(), lambda m: None, lambda *a: None)
            exe = AT.python_exe(self.root)
        with open(os.path.join(self.root, 'python', 'python311._pth')) as fh:
            lines = fh.read().splitlines()
        self.assertIn('import site', lines)
        self.assertNotIn('#import site', lines)
        self.assertTrue(os.path.isfile(exe))
        self.assertFalse(os.path.exists(
            os.path.join(self.root, 'downloads', 'python-embed.zip')))

    def test_mac_builds_a_venv_with_the_host_python(self):
        # 真的建一個 venv（不連網），確認 python_exe 指得到裡面的直譯器
        pip_args = []
        with _PlatformPatch('darwin'), \
                mock.patch.object(AT, 'find_host_python', lambda: sys.executable), \
                mock.patch.object(AT, '_pip', lambda root, args, *a: pip_args.append(args)):
            AT._install_python(self.root, threading.Event(), lambda m: None, lambda *a: None)
            exe = AT.python_exe(self.root)
        self.assertTrue(os.path.isfile(exe), exe)
        self.assertIn(['--upgrade', 'pip'], pip_args)      # 系統附的 pip 可能太舊

    def test_mac_without_a_host_python_says_how_to_get_one(self):
        with _PlatformPatch('darwin'), mock.patch.object(AT, 'find_host_python', lambda: ''):
            with self.assertRaises(RuntimeError) as ctx:
                AT._install_python(self.root, threading.Event(), lambda m: None,
                                   lambda *a: None)
        self.assertIn('brew install', str(ctx.exception))

    def test_existing_venv_is_kept(self):
        with _PlatformPatch('darwin'):
            exe = AT.python_exe(self.root)
            os.makedirs(os.path.dirname(exe))
            open(exe, 'wb').close()
            looked, pip_args = [], []
            with mock.patch.object(AT, 'find_host_python',
                                   lambda: looked.append(1) or ''), \
                    mock.patch.object(AT, '_pip',
                                      lambda root, args, *a: pip_args.append(args)):
                AT._install_python(self.root, threading.Event(), lambda m: None,
                                   lambda *a: None)
        self.assertEqual(looked, [])
        # 續裝（venv 已存在）時 pip 的升級還是要跑：放在建立分支裡面的話，
        # 第一次裝到一半失敗後就永遠跳過，舊 pip 會一直留著
        self.assertIn(['--upgrade', 'pip'], pip_args)


class PipFlagTests(unittest.TestCase):
    """venv 內附的 pip 可能很老（Python 3.9 帶 21.x），不能硬給新選項。"""

    def _run_pip(self, pip_version):
        seen = []
        with mock.patch.object(AT, '_pip_version', lambda _root: pip_version), \
                mock.patch.object(AT, 'python_exe', lambda root=None: 'PY'), \
                mock.patch.object(AT._Proc, 'run',
                                  lambda self, args, on_line, cwd=None: seen.append(args) or 0):
            AT._pip('/root', ['torch'], threading.Event(), lambda m: None, lambda *a: None,
                    'torch')
        return seen[0]

    def test_new_pip_gets_the_raw_progress_bar(self):
        self.assertIn('raw', self._run_pip((24, 2)))

    def test_old_pip_does_not(self):
        # pip 21.x 會回「invalid choice: 'raw'」直接失敗
        self.assertNotIn('--progress-bar', self._run_pip((21, 2)))

    def test_unknown_pip_version_plays_safe(self):
        self.assertNotIn('--progress-bar', self._run_pip(()))

    def test_version_parsing(self):
        with mock.patch.object(AT.subprocess, 'run', lambda *a, **kw: mock.Mock(
                returncode=0, stdout='pip 24.2 from /x/pip (python 3.12)')):
            self.assertEqual(AT._pip_version('/root'), (24, 2))
        with mock.patch.object(AT.subprocess, 'run', lambda *a, **kw: mock.Mock(
                returncode=1, stdout='')):
            self.assertEqual(AT._pip_version('/root'), ())


class HostPythonTests(unittest.TestCase):
    def test_the_running_interpreter_is_usable(self):
        self.assertTrue(AT._usable_host_python(sys.executable))

    def test_a_path_that_is_not_python_is_rejected(self):
        self.assertFalse(AT._usable_host_python(os.path.join(os.sep, 'nope', 'python3')))

    def test_falls_back_to_the_running_interpreter(self):
        with mock.patch.object(AT.shutil, 'which', lambda _name: None), \
                mock.patch.object(AT, 'HOST_PYTHON_DIRS', ()):
            self.assertEqual(os.path.realpath(AT.find_host_python()),
                             os.path.realpath(sys.executable))

    def test_a_newer_python_wins(self):
        versions = {'/opt/a/python3.9': (3, 9), '/opt/b/python3.12': (3, 12)}
        with mock.patch.object(AT.shutil, 'which', lambda _name: None), \
                mock.patch.object(AT, 'HOST_PYTHON_DIRS', ('/opt/a', '/opt/b')), \
                mock.patch.object(AT, 'HOST_PYTHON_NAMES', ('python3.9', 'python3.12')), \
                mock.patch.object(AT.os.path, 'isfile', lambda p: p in versions), \
                mock.patch.object(AT, '_host_python_version', versions.get), \
                mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(AT.find_host_python(), '/opt/b/python3.12')

    def test_xcodes_python_is_the_last_resort(self):
        # venv 會連回建立它的直譯器，Xcode 更新一搬家整個環境就壞了
        xcode = '/Applications/Xcode.app/Contents/Developer/usr/bin/python3.9'
        versions = {xcode: (3, 9), '/usr/local/bin/python3.9': (3, 9)}
        with mock.patch.object(AT.shutil, 'which', lambda _name: xcode), \
                mock.patch.object(AT, 'HOST_PYTHON_DIRS', ('/usr/local/bin',)), \
                mock.patch.object(AT, 'HOST_PYTHON_NAMES', ('python3.9',)), \
                mock.patch.object(AT.os.path, 'isfile', lambda p: p in versions), \
                mock.patch.object(AT, '_host_python_version', versions.get), \
                mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(AT.find_host_python(), '/usr/local/bin/python3.9')


class TorchSourceTests(unittest.TestCase):
    def test_mac_takes_torch_from_pypi(self):
        # PyTorch 自己的 cpu／cu128 索引裡沒有 macOS 的輪子，指過去會裝不起來
        with _PlatformPatch('darwin'):
            self.assertEqual(AT._torch_args('mps'), ['torch'])
            self.assertEqual(AT._torch_args('cpu'), ['torch'])

    def test_windows_picks_the_matching_index(self):
        with _PlatformPatch('win32'):
            self.assertIn(AT.TORCH_INDEX['cuda'], AT._torch_args('cuda'))
            self.assertIn(AT.TORCH_INDEX['cpu'], AT._torch_args('cpu'))


class VariantTests(unittest.TestCase):
    def test_device_labels(self):
        self.assertEqual(AT.device_label('cuda'), 'GPU')
        self.assertIn('Apple', AT.device_label('mps'))
        self.assertEqual(AT.device_label('cpu'), 'CPU')
        self.assertEqual(AT.device_label(''), 'CPU')

    def test_apple_silicon_defaults_to_mps(self):
        with _PlatformPatch('darwin'), \
                mock.patch.object(AT.platform, 'machine', lambda: 'arm64'):
            self.assertEqual(AT.default_variant(), 'mps')
            self.assertLess(AT.estimated_download_mb('mps'),
                            AT.estimated_download_mb('cuda'))

    def test_intel_mac_defaults_to_cpu(self):
        with _PlatformPatch('darwin'), \
                mock.patch.object(AT.platform, 'machine', lambda: 'x86_64'):
            self.assertEqual(AT.default_variant(), 'cpu')

    def test_mac_never_looks_for_cuda(self):
        with _PlatformPatch('darwin'):
            self.assertFalse(AT.has_nvidia_gpu())


class EnvRootTests(unittest.TestCase):
    def test_mac_env_lives_in_application_support(self):
        with _PlatformPatch('darwin'):
            root = AT.env_root()
            self.assertIn('Library/Application Support', root.replace(os.sep, '/'))
            self.assertTrue(root.endswith('ai_transcribe'))
            self.assertTrue(AT.python_exe(root).endswith(os.path.join('bin', 'python3')))

    def test_windows_env_uses_python_exe(self):
        with _PlatformPatch('win32'):
            self.assertTrue(AT.python_exe('C:/x').endswith('python.exe'))


def _load_worker():
    """worker 是複製到轉譜環境去跑的獨立腳本，不是套件的一部分，直接讀檔載入。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'qt_editor', 'ai_transcribe_worker.py')
    spec = importlib.util.spec_from_file_location('ai_transcribe_worker_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeTorch:
    """只夠 pick_device 用的假 torch（真的 torch 不在製譜器的環境裡）。"""

    class cuda:
        available = False

        @classmethod
        def is_available(cls):
            return cls.available


class PickDeviceTests(unittest.TestCase):
    """轉譜用哪個裝置：MPS 只在算得對的時候才用，其他情況一律退回 CPU。"""

    def setUp(self):
        self.worker = _load_worker()
        _FakeTorch.cuda.available = False
        self._saved = sys.modules.get('torch')
        sys.modules['torch'] = _FakeTorch

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop('torch', None)
        else:
            sys.modules['torch'] = self._saved

    def _patch_mps(self, available, mismatch='', error=None):
        def fake_mismatch(_torch):
            if error is not None:
                raise error
            return mismatch

        return (mock.patch.object(self.worker, '_mps_available', lambda _t: available),
                mock.patch.object(self.worker, '_mps_mismatch', fake_mismatch))

    def _pick(self, requested, **kw):
        patches = self._patch_mps(**kw)
        for patch in patches:
            patch.start()
        try:
            return self.worker.pick_device(requested)
        finally:
            for patch in patches:
                patch.stop()

    def test_auto_uses_mps_when_it_agrees_with_cpu(self):
        device, note = self._pick('auto', available=True)
        self.assertEqual(device, 'mps')
        self.assertTrue(note)               # 晶片型號，給使用者看的

    def test_the_chip_name_is_reported(self):
        # 對應 CUDA 回報顯卡名字；問不到就退回 'MPS'
        self.assertTrue(self.worker._apple_chip_name())

    def test_wrong_mps_results_fall_back_to_cpu(self):
        # 安靜算錯比慢更糟：整首譜的音高都會壞掉
        device, note = self._pick('auto', available=True, mismatch='最大誤差 0.5')
        self.assertEqual(device, 'cpu')
        self.assertIn('不一致', note)

    def test_mps_that_blows_up_falls_back_to_cpu(self):
        device, note = self._pick('auto', available=True, error=RuntimeError('沒有這個運算'))
        self.assertEqual(device, 'cpu')
        self.assertIn('無法執行', note)

    def test_asking_for_mps_without_mps_says_so(self):
        device, note = self._pick('mps', available=False)
        self.assertEqual(device, 'cpu')
        self.assertIn('MPS', note)

    def test_asking_for_cpu_skips_the_mps_check(self):
        self.assertEqual(self._pick('cpu', available=True, error=AssertionError('不該檢查')),
                         ('cpu', ''))

    def test_auto_without_any_gpu_is_quietly_cpu(self):
        self.assertEqual(self._pick('auto', available=False), ('cpu', ''))

    def test_mps_wins_over_cuda_on_a_mac(self):
        # 不會同時有，但順序要確定：先看 MPS 再看 CUDA
        _FakeTorch.cuda.available = True
        self.assertEqual(self._pick('auto', available=True)[0], 'mps')

    def test_batch_size_per_device(self):
        # MPS 的記憶體是和系統共用的，批次比 CUDA 保守
        self.assertGreater(self.worker._default_batch('cuda'),
                           self.worker._default_batch('mps'))
        self.assertEqual(self.worker._default_batch('cpu'), 1)


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(AT._parse('{"type": "log", "message": "x"}')['message'], 'x')
        self.assertIsNone(AT._parse('Segment 1 / 3'))
        self.assertIsNone(AT._parse('{not json'))
        self.assertIsNone(AT._parse('{"no_type": 1}'))


if __name__ == '__main__':
    unittest.main()
