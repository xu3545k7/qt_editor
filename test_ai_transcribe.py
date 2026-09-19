"""AI 轉譜的安裝與子程序協定（不連網、不需要 torch：用假的 worker 代替）。"""

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


class InstallPythonTests(unittest.TestCase):
    def test_embeddable_pth_gets_site_enabled(self):
        root = tempfile.mkdtemp(prefix='ai_transcribe_test_')
        try:
            src = os.path.join(root, 'src.zip')
            with zipfile.ZipFile(src, 'w') as zf:
                zf.writestr('python.exe', b'')
                zf.writestr('python311._pth', 'python311.zip\n.\n\n# comment\n#import site\n')

            def fake_download(url, dest, cancel, progress, stage, expected_size=0):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)

            with mock.patch.object(AT, '_download', fake_download):
                AT._install_python(root, threading.Event(), lambda m: None,
                                   lambda *a: None)
            with open(os.path.join(root, 'python', 'python311._pth')) as fh:
                lines = fh.read().splitlines()
            self.assertIn('import site', lines)
            self.assertNotIn('#import site', lines)
            self.assertTrue(os.path.isfile(AT.python_exe(root)))
            self.assertFalse(os.path.exists(os.path.join(root, 'downloads', 'python-embed.zip')))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(AT._parse('{"type": "log", "message": "x"}')['message'], 'x')
        self.assertIsNone(AT._parse('Segment 1 / 3'))
        self.assertIsNone(AT._parse('{not json'))
        self.assertIsNone(AT._parse('{"no_type": 1}'))


if __name__ == '__main__':
    unittest.main()
