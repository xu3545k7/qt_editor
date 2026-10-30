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
p.add_argument('--model', default='transkun')
p.add_argument('--separate', action='store_true')
p.add_argument('--separate-model', dest='separate_model')
p.add_argument('--separate-out', dest='separate_out')
p.add_argument('--env')
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
emit('log', message='轉譜模型：%s' % a.model)
open(a.output, 'wb').write(b'MThd')
if a.wav_out:
    open(a.wav_out, 'wb').write(b'RIFF')
emit('done', output=a.output, notes=12, pedals=2, seconds=1.0, device='cpu',
     model=a.model, elapsed=0.1)
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

    def test_want_wav_false_leaves_no_wav_beside_the_mp3(self):
        # 「只轉成 MIDI 檔」：使用者的音樂資料夾裡不該多出一個幾十 MB 的副產物
        mp3 = os.path.join(self.root, 'song.mp3')
        with open(mp3, 'wb') as fh:
            fh.write(b'ID3')
        with open(mp3 + '.mode', 'w') as fh:
            fh.write('ok')
        out = os.path.join(self.root, '轉出來的.mid')
        result = AT.transcribe(mp3, out, threading.Event(), self.logs.append,
                               lambda *a: None, root=self.root, want_wav=False)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(result['notes'], 12)
        self.assertEqual(result['wav'], '')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'song.wav')))

    def test_want_wav_false_still_reports_an_existing_wav(self):
        # 上次轉過留下的 WAV 還在：不重解，但仍然告訴呼叫端它在哪
        mp3 = os.path.join(self.root, 'song.mp3')
        with open(mp3, 'wb') as fh:
            fh.write(b'ID3')
        with open(mp3 + '.mode', 'w') as fh:
            fh.write('ok')
        wav = os.path.join(self.root, 'song.wav')
        with open(wav, 'wb') as fh:
            fh.write(b'mine')
        result = AT.transcribe(mp3, os.path.join(self.root, 'x.mid'), threading.Event(),
                               self.logs.append, lambda *a: None, root=self.root,
                               want_wav=False)
        self.assertEqual(result['wav'], wav)
        with open(wav, 'rb') as fh:
            self.assertEqual(fh.read(), b'mine')

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

    @unittest.skipUnless(os.name == 'posix',
                         'venv 在 Windows 上建的是 Scripts/python.exe，這個要在 mac/Linux 跑')
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
        # 鍵要照 find_host_python 組路徑的方式組（Windows 上 join 出來是反斜線）
        old_py = os.path.join('/opt/a', 'python3.9')
        new_py = os.path.join('/opt/b', 'python3.12')
        versions = {old_py: (3, 9), new_py: (3, 12)}
        with mock.patch.object(AT.shutil, 'which', lambda _name: None), \
                mock.patch.object(AT, 'HOST_PYTHON_DIRS', ('/opt/a', '/opt/b')), \
                mock.patch.object(AT, 'HOST_PYTHON_NAMES', ('python3.9', 'python3.12')), \
                mock.patch.object(AT.os.path, 'isfile', lambda p: p in versions), \
                mock.patch.object(AT, '_host_python_version', versions.get), \
                mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(AT.find_host_python(), new_py)

    def test_xcodes_python_is_the_last_resort(self):
        # venv 會連回建立它的直譯器，Xcode 更新一搬家整個環境就壞了
        xcode = '/Applications/Xcode.app/Contents/Developer/usr/bin/python3.9'
        local = os.path.join('/usr/local/bin', 'python3.9')
        versions = {xcode: (3, 9), local: (3, 9)}
        with mock.patch.object(AT.shutil, 'which', lambda _name: xcode), \
                mock.patch.object(AT, 'HOST_PYTHON_DIRS', ('/usr/local/bin',)), \
                mock.patch.object(AT, 'HOST_PYTHON_NAMES', ('python3.9',)), \
                mock.patch.object(AT.os.path, 'isfile', lambda p: p in versions), \
                mock.patch.object(AT, '_host_python_version', versions.get), \
                mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(AT.find_host_python(), local)


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


class ModelChoiceTests(_FakeEnv):
    """轉譜模型：預設 transkun，裝不起來就退回 ByteDance。

    為什麼預設是 transkun：在**我們自己的曲庫**實測（14 首乾淨鋼琴軌對照
    raw/*.mid、mir_eval、先扣掉全域偏移），起音 F1 97.3% vs 93.4%、14 首全贏；
    音長（起音＋結束）56.7% vs 44.8%；速度一樣。

    注意 MAESTRO 上的排名是反的（ByteDance 起音 96.72 贏 95.3）——論文的素材是
    獨奏古典，我們的是遊戲音樂鋼琴翻彈，不能互推。
    """

    def _model_log(self):
        for line in self.logs:
            if line.startswith('轉譜模型：'):
                return line.split('：', 1)[1]
        return ''

    def test_the_default_is_transkun(self):
        self.assertEqual(AT.DEFAULT_MODEL, 'transkun')

    def test_it_passes_the_chosen_model_through(self):
        self._mode('ok')
        out = AT.default_output_path(self.audio)
        with mock.patch.object(AT, 'transkun_installed', lambda root=None: True):
            result = AT.transcribe(self.audio, out, threading.Event(),
                                   self.logs.append, lambda *a: None,
                                   root=self.root, model='transkun')
        self.assertEqual(self._model_log(), 'transkun')
        self.assertEqual(result.get('model'), 'transkun')

    def test_bytedance_can_still_be_chosen(self):
        self._mode('ok')
        out = AT.default_output_path(self.audio)
        AT.transcribe(self.audio, out, threading.Event(), self.logs.append,
                      lambda *a: None, root=self.root, model='bytedance')
        self.assertEqual(self._model_log(), 'bytedance')

    def test_it_falls_back_when_transkun_is_missing(self):
        """轉得出來比轉不出來重要——沒裝就退回去，而且要講。"""
        self._mode('ok')
        out = AT.default_output_path(self.audio)
        with mock.patch.object(AT, 'transkun_installed', lambda root=None: False):
            AT.transcribe(self.audio, out, threading.Event(), self.logs.append,
                          lambda *a: None, root=self.root, model='transkun')
        self.assertEqual(self._model_log(), 'bytedance')
        self.assertTrue(any('還沒裝' in line for line in self.logs), self.logs)

    def test_no_choice_means_the_default(self):
        self._mode('ok')
        out = AT.default_output_path(self.audio)
        with mock.patch.object(AT, 'transkun_installed', lambda root=None: True):
            AT.transcribe(self.audio, out, threading.Event(), self.logs.append,
                          lambda *a: None, root=self.root)
        self.assertEqual(self._model_log(), AT.DEFAULT_MODEL)

    def test_every_model_in_the_menu_has_measured_numbers(self):
        keys = [k for k, _label, _note in AT.MODELS]
        self.assertIn('transkun', keys)
        self.assertIn('bytedance', keys)
        self.assertEqual(keys[0], AT.DEFAULT_MODEL, '預設的要排第一個')
        for _k, label, note in AT.MODELS:
            self.assertTrue(label and note)
            self.assertIn('F1', note, '選單上要寫實測數字')


class TranskunInstallTests(unittest.TestCase):
    """transkun 的安裝：一律 --no-deps，torchaudio 版本要跟著 torch。"""

    def test_the_packages_are_pinned_to_what_inference_needs(self):
        names = ' '.join(AT.TRANSKUN_PACKAGES)
        for want in ('transkun', 'pretty_midi', 'moduleconf', 'pydub'):
            self.assertIn(want, names)
        self.assertNotIn('ncls', names,
                         'ncls 建不起來（要編譯器），而且推論用不到')
        self.assertNotIn('tensorboard', names, '那是訓練才要的')

    def test_torchaudio_follows_the_installed_torch(self):
        """讓 pip 自己解依賴會把 torch 降版，RTX 50 系列就只剩 CPU。"""
        with mock.patch.object(AT, 'python_exe', lambda root=None: sys.executable), \
                mock.patch.object(AT.subprocess, 'run') as run:
            run.return_value = mock.Mock(stdout='2.11.0+cu128')
            args = AT._torchaudio_args('/nope', 'cu128')
        self.assertIn('torchaudio==2.11.0+cu128', args)

    def test_it_falls_back_to_plain_torchaudio_when_torch_is_unreadable(self):
        with mock.patch.object(AT, 'python_exe', lambda root=None: '/nope/python'):
            args = AT._torchaudio_args('/nope', 'cu128')
        self.assertIn('torchaudio', args)


class ComponentTopUpTests(unittest.TestCase):
    """環境已經裝好、後來才多出必要元件時要**補裝**，不是重裝。

    把 `ENV_VERSION` 加一號會讓 `installed_info()` 回 None、整個環境被當成
    沒裝，於是跳出重裝 5.7GB 的流程——而缺的通常只有幾十 MB。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ai_components_test_')
        os.makedirs(os.path.dirname(AT.checkpoint_path(self.root)))
        open(AT.checkpoint_path(self.root), 'wb').close()
        with open(os.path.join(self.root, 'installed.json'), 'w', encoding='utf-8') as fh:
            json.dump({'env_version': AT.ENV_VERSION, 'device': 'cpu',
                       'variant': 'cpu'}, fh)
        self._patch = mock.patch.object(AT, 'python_exe',
                                        lambda root=None: sys.executable)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _status(self, **kw):
        base = {'transkun': True, 'bsrofo': True, 'demucs': True}
        base.update(kw)
        return mock.patch.object(AT, 'component_status', lambda root=None: base)

    def test_nothing_missing_means_nothing_to_do(self):
        with self._status():
            self.assertEqual(AT.missing_components(self.root), [])

    def test_a_missing_required_component_is_reported(self):
        with self._status(transkun=False):
            self.assertEqual(AT.missing_components(self.root), ['transkun'])

    def test_optional_components_are_not_reported_by_default(self):
        """分離器是選配（+699MB），不該自己跑去裝。"""
        with self._status(bsrofo=False, demucs=False):
            self.assertEqual(AT.missing_components(self.root), [])

    def test_asking_for_everything_includes_the_optional_ones(self):
        with self._status(bsrofo=False, demucs=False):
            got = AT.missing_components(self.root, required_only=False)
        self.assertIn('bsrofo', got)
        self.assertIn('demucs', got)

    def test_an_uninstalled_environment_reports_nothing(self):
        """環境本身沒裝就走原本的安裝流程，不要在那邊喊缺元件。"""
        os.remove(os.path.join(self.root, 'installed.json'))
        self.assertEqual(AT.missing_components(self.root), [])

    def test_installing_runs_only_what_was_asked_for(self):
        calls = []
        with mock.patch.object(AT, '_install_transkun',
                               lambda *a, **k: calls.append('transkun')),                 mock.patch.object(AT, '_install_separator',
                                  lambda *a, **k: calls.append('bsrofo')),                 self._status():
            AT.install_components(['transkun'], threading.Event(),
                                  lambda m: None, lambda *a: None, self.root)
        self.assertEqual(calls, ['transkun'])

    def test_it_reports_what_still_does_not_work(self):
        with mock.patch.object(AT, '_install_transkun', lambda *a, **k: None),                 self._status(transkun=False):
            left = AT.install_components(['transkun'], threading.Event(),
                                         lambda m: None, lambda *a: None, self.root)
        self.assertEqual(left, ['transkun'])

    def test_one_failing_component_does_not_stop_the_others(self):
        calls = []

        def boom(*a, **k):
            raise RuntimeError('下載失敗')

        with mock.patch.object(AT, '_install_transkun', boom),                 mock.patch.object(AT, '_install_separator',
                                  lambda *a, **k: calls.append('bsrofo')),                 self._status():
            AT.install_components(['transkun', 'bsrofo'], threading.Event(),
                                  lambda m: None, lambda *a: None, self.root)
        self.assertEqual(calls, ['bsrofo'])

    def test_cancelling_stops_immediately(self):
        def cancelled(*a, **k):
            raise AT.Cancelled()

        with mock.patch.object(AT, '_install_transkun', cancelled), self._status():
            with self.assertRaises(AT.Cancelled):
                AT.install_components(['transkun'], threading.Event(),
                                      lambda m: None, lambda *a: None, self.root)

    def test_every_component_has_a_label_and_a_size(self):
        for key, label, required, size in AT.COMPONENTS:
            self.assertTrue(key and label)
            self.assertIsInstance(required, bool)
            self.assertGreater(size, 0, '%s 沒寫大概多大' % key)
        required = [k for k, _l, r, _s in AT.COMPONENTS if r]
        self.assertEqual(required, ['transkun'],
                         '只有預設設定跑不起來的才算必要')


class ModelPickerIsVisibleTests(unittest.TestCase):
    """轉譜模型要在**轉譜流程裡**看得到，不是只藏在偏好設定。

    使用者：「沒看到轉譜選項，只有分離器」。同一個決定的兩半（用哪個模型、要不要
    先分離）不該一半在對話框、一半在設定裡。偏好設定那一欄留著當記住的預設值。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, **kw):
        from qt_editor.ai_transcribe_dialog import SeparateDialog
        opts = {'available': {'bsrofo': True, 'demucs': True},
                'models_ready': {'transkun': True, 'bytedance': True},
                'piano_already': False}
        opts.update(kw)
        return SeparateDialog(None, opts['available'], opts['models_ready'],
                              opts['piano_already'])

    def test_both_choices_are_in_the_same_dialog(self):
        from PyQt5.QtWidgets import QGroupBox
        dlg = self.dialog()          # 要留著：不留參考的話 widget 當場就被回收
        titles = [b.title() for b in dlg.findChildren(QGroupBox)]
        self.assertTrue(any('轉譜模型' in t for t in titles), titles)
        self.assertTrue(any('分離器' in t for t in titles), titles)

    def test_every_model_is_offered(self):
        dlg = self.dialog()
        self.assertEqual(sorted(dlg._models), sorted(k for k, _l, _n in AT.MODELS))

    def test_the_default_is_preselected(self):
        self.assertEqual(self.dialog().model(), AT.DEFAULT_MODEL)

    def test_a_model_that_needs_downloading_says_so(self):
        dlg = self.dialog(models_ready={'transkun': False, 'bytedance': True})
        self.assertIn('下載', dlg._models['transkun'].text())
        self.assertNotIn('下載', dlg._models['bytedance'].text())

    def test_the_measured_numbers_are_on_screen(self):
        """選哪個是有代價的決定，不該只給一個不說理由的選項。"""
        dlg = self.dialog()
        for key, btn in dlg._models.items():
            self.assertIn('F1', btn.text(), key)

    def test_a_piano_stem_still_gets_the_model_picker(self):
        """以前這種檔案整個對話框都不開，模型就跟著選不到。"""
        dlg = self.dialog(piano_already=True)
        self.assertTrue(dlg._models)
        self.assertTrue(dlg._skip.isChecked(), '已經是分軌就預設不分離')

    def test_picking_one_reports_it(self):
        dlg = self.dialog()
        dlg._models['bytedance'].setChecked(True)
        self.assertEqual(dlg.model(), 'bytedance')
