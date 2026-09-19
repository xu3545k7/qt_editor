"""AI 鋼琴轉譜：安裝轉譜環境、呼叫轉譜程序。

製譜器打包成單檔 exe，torch（GPU 版裝好約 5GB）不可能包進去——每次啟動都要
解壓一次。所以轉譜跑在另外安裝的環境裡：

    %LOCALAPPDATA%\\NostalgiaChartEditor\\ai_transcribe\\
        python\\          Python 3.11 embeddable + torch + piano_transcription_inference
        models\\          模型權重（安裝時驗 SHA256）
        worker.py         從製譜器複製過去的 ai_transcribe_worker.py
        installed.json    裝好的標記（版本、torch、裝置）

第一次用的時候由製譜器自己下載安裝，各步驟都可以重跑（已完成的會跳過），
中途取消或斷線下次接著裝。有 NVIDIA 顯卡就裝 CUDA 12.8 版的 torch
（RTX 50 系列最低要這版；RTX 5080 實測兩三分鐘的曲子約 6 秒），
沒有就裝 CPU 版（小很多；16 執行緒的 CPU 實測約音檔長度的三分之一）。

這支不碰 Qt：安裝與轉譜都吃 log / progress 回呼和一個取消旗標，
介面在 ai_transcribe_dialog。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from typing import Callable, Dict, List, Optional

#: 環境格式改了（換 Python 版本、換套件組合）就加一，舊環境會被當成沒裝。
ENV_VERSION = 1

PYTHON_VERSION = '3.11.9'
PYTHON_URL = ('https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip'
              % (PYTHON_VERSION, PYTHON_VERSION))
GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'
TORCH_INDEX = {
    'cuda': 'https://download.pytorch.org/whl/cu128',
    'cpu': 'https://download.pytorch.org/whl/cpu',
}
#: 套件本身只宣告了名稱沒鎖版本；鎖住確定可用的組合。
PACKAGES = [
    'piano_transcription_inference==0.0.6',
    'torchlibrosa==0.1.0',
    'librosa>=0.10',
    'soundfile>=0.12',   # 0.12 起內建的 libsndfile 讀得了 mp3
    'mido',
    'matplotlib',        # 套件的 models.py 會 import pyplot，沒用到但少了就 import 失敗
]

CHECKPOINT_NAME = 'CRNN_note_F1=0.9677_pedal_F1=0.9186.pth'
CHECKPOINT_SHA256 = 'c3fa9730725bf4a762f1c14bc80cd5986eacda01b026f5a4a2525cd607876141'
CHECKPOINT_SIZE = 171966578
#: 原始出處是 Zenodo，但它常常 504；兩個 Hugging Face 鏡像的檔案 SHA256 相同。
CHECKPOINT_URLS = [
    'https://huggingface.co/asigalov61/bytedance_piano_transcription/resolve/main/'
    'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth',
    'https://huggingface.co/Genius-Society/piano_trans/resolve/main/'
    'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth',
    'https://zenodo.org/records/4034264/files/'
    'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1',
]

AUDIO_EXTENSIONS = ('.wav', '.flac', '.ogg', '.mp3')

Log = Callable[[str], None]
Progress = Callable[[str, int, int], None]      # (階段, 已完成, 總數)；總數 0 = 不定量


class Cancelled(Exception):
    """使用者按了取消。"""


# ── 路徑 ────────────────────────────────────────────────────────────────

def env_root() -> str:
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'NostalgiaChartEditor', 'ai_transcribe')


def python_exe(root: Optional[str] = None) -> str:
    return os.path.join(root or env_root(), 'python', 'python.exe')


def checkpoint_path(root: Optional[str] = None) -> str:
    return os.path.join(root or env_root(), 'models', CHECKPOINT_NAME)


def worker_path(root: Optional[str] = None) -> str:
    return os.path.join(root or env_root(), 'worker.py')


def _marker_path(root: Optional[str] = None) -> str:
    return os.path.join(root or env_root(), 'installed.json')


def bundled_worker_source() -> str:
    """製譜器帶著的 worker 原始檔（打包後在 _MEIPASS/qt_editor 底下）。"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'qt_editor', 'ai_transcribe_worker.py')  # type: ignore[attr-defined]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_transcribe_worker.py')


def default_output_path(audio_path: str) -> str:
    """轉出的 MIDI 放在音檔旁邊：`歌名.ai.mid`（曲庫掃描不看 .mid，不會被誤認成譜面）。"""
    return os.path.splitext(audio_path)[0] + '.ai.mid'


# ── 狀態 ────────────────────────────────────────────────────────────────

def installed_info(root: Optional[str] = None) -> Optional[Dict]:
    """裝好了就回傳標記內容，否則 None。只看標記與幾個關鍵檔案，不啟動 Python。"""
    try:
        with open(_marker_path(root), encoding='utf-8') as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    if info.get('env_version') != ENV_VERSION:
        return None
    if not os.path.isfile(python_exe(root)) or not os.path.isfile(checkpoint_path(root)):
        return None
    return info


def is_installed(root: Optional[str] = None) -> bool:
    return installed_info(root) is not None


def has_nvidia_gpu() -> bool:
    exe = shutil.which('nvidia-smi')
    if not exe:
        return False
    try:
        out = subprocess.run([exe, '-L'], capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and 'GPU' in out.stdout


def estimated_download_mb(variant: str) -> int:
    """給安裝前的確認對話框用的大概數字（實際依 torch 版本而定）。"""
    return 3300 if variant == 'cuda' else 500


def estimated_disk_mb(variant: str) -> int:
    """裝好後的大小（GPU 版實測 torch 2.11+cu128 約 4.9GB，CUDA 函式庫佔大半）。"""
    return 5000 if variant == 'cuda' else 1500


# ── 子程序共用 ──────────────────────────────────────────────────────────

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    # 製譜器（或開發時的系統 Python）的設定不能漏進轉譜環境
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'VIRTUAL_ENV'):
        env.pop(key, None)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    env['MPLBACKEND'] = 'Agg'
    return env


class _Proc:
    """跑一個子程序、逐行轉給 on_line；cancel 旗標一立起來就砍掉。"""

    def __init__(self, cancel: threading.Event):
        self.cancel = cancel

    def run(self, args: List[str], on_line: Callable[[str], None],
            cwd: Optional[str] = None) -> int:
        if self.cancel.is_set():
            raise Cancelled()
        with subprocess.Popen(
            args, cwd=cwd, env=_child_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', errors='replace', bufsize=1,
            creationflags=_NO_WINDOW,
        ) as proc:
            watcher = threading.Thread(target=self._watch, args=(proc,), daemon=True)
            watcher.start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip('\r\n')
                    if line:
                        on_line(line)
                code = proc.wait()
            finally:
                if proc.poll() is None:
                    proc.kill()
        if self.cancel.is_set():
            raise Cancelled()
        return code

    def _watch(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            if self.cancel.wait(0.2):
                try:
                    proc.kill()
                except OSError:
                    pass
                return


# ── 安裝 ────────────────────────────────────────────────────────────────

def _download(url: str, dest: str, cancel: threading.Event, progress: Progress,
              stage: str, expected_size: int = 0) -> None:
    """下載到 dest（先寫 .part，完成才改名，避免半個檔被當成好的）。"""
    tmp = dest + '.part'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'NostalgiaChartEditor'})
    with urllib.request.urlopen(request, timeout=60) as resp, open(tmp, 'wb') as fh:
        total = int(resp.headers.get('Content-Length') or expected_size or 0)
        done = 0
        while True:
            if cancel.is_set():
                break
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            done += len(block)
            progress(stage, done, total)
    if cancel.is_set():
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise Cancelled()
    os.replace(tmp, dest)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _install_python(root: str, cancel: threading.Event, log: Log, progress: Progress) -> None:
    exe = python_exe(root)
    if os.path.isfile(exe):
        log('Python 已存在，略過。')
        return
    target = os.path.dirname(exe)
    archive = os.path.join(root, 'downloads', 'python-embed.zip')
    log('下載 Python %s（embeddable）…' % PYTHON_VERSION)
    _download(PYTHON_URL, archive, cancel, progress, 'python')
    if os.path.isdir(target):
        shutil.rmtree(target)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    # embeddable 版預設不載入 site（沒有 site-packages、裝不了套件），打開它。
    for name in os.listdir(target):
        if name.endswith('._pth'):
            pth = os.path.join(target, name)
            with open(pth, encoding='utf-8') as fh:
                lines = fh.read().splitlines()
            lines = ['import site' if ln.strip() == '#import site' else ln for ln in lines]
            if 'import site' not in lines:
                lines.append('import site')
            with open(pth, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(lines) + '\n')
    os.remove(archive)


def _pip(root: str, args: List[str], cancel: threading.Event, log: Log,
         progress: Progress, stage: str) -> None:
    def on_line(line: str) -> None:
        # --progress-bar raw 會印「Progress 123 of 456」
        if line.startswith('Progress ') and ' of ' in line:
            try:
                done, total = line[len('Progress '):].split(' of ')
                progress(stage, int(done), int(total))
                return
            except ValueError:
                pass
        log(line)

    code = _Proc(cancel).run(
        [python_exe(root), '-m', 'pip', 'install', '--no-warn-script-location',
         '--progress-bar', 'raw'] + args, on_line)
    if code != 0:
        raise RuntimeError('pip 安裝失敗（代碼 %d），詳細訊息在上面的紀錄裡。' % code)


def _install_pip(root: str, cancel: threading.Event, log: Log, progress: Progress) -> None:
    probe = subprocess.run([python_exe(root), '-m', 'pip', '--version'], env=_child_env(),
                           capture_output=True, text=True, creationflags=_NO_WINDOW)
    if probe.returncode == 0:
        log('pip 已存在，略過。')
        return
    script = os.path.join(root, 'downloads', 'get-pip.py')
    log('下載 pip…')
    _download(GET_PIP_URL, script, cancel, progress, 'pip')
    code = _Proc(cancel).run([python_exe(root), script, '--no-warn-script-location'], log)
    if code != 0:
        raise RuntimeError('安裝 pip 失敗（代碼 %d）。' % code)
    os.remove(script)


def _install_checkpoint(root: str, cancel: threading.Event, log: Log,
                        progress: Progress) -> None:
    dest = checkpoint_path(root)
    if os.path.isfile(dest) and os.path.getsize(dest) == CHECKPOINT_SIZE:
        if _sha256(dest) == CHECKPOINT_SHA256:
            log('模型權重已存在，略過。')
            return
    errors = []
    for url in CHECKPOINT_URLS:
        host = url.split('/')[2]
        log('下載模型權重（約 165MB，來源 %s）…' % host)
        try:
            _download(url, dest, cancel, progress, 'model', CHECKPOINT_SIZE)
        except Cancelled:
            raise
        except Exception as exc:                # noqa: BLE001
            errors.append('%s：%s' % (host, exc))
            log('　失敗：%s' % exc)
            continue
        if _sha256(dest) == CHECKPOINT_SHA256:
            return
        os.remove(dest)
        errors.append('%s：檔案內容不符（SHA256）' % host)
        log('　檔案內容不符，換下一個來源。')
    raise RuntimeError('模型權重下載失敗：\n' + '\n'.join(errors))


def selftest(root: Optional[str] = None, device: str = 'auto',
             cancel: Optional[threading.Event] = None, log: Optional[Log] = None) -> Dict:
    """在轉譜環境裡 import 全部依賴並試 GPU，回傳 torch 版本與實際會用的裝置。"""
    root = root or env_root()
    result: Dict = {}
    errors: List[str] = []
    logs: List[str] = []

    def on_line(line: str) -> None:
        msg = _parse(line)
        if msg is None:
            logs.append(line)
            if log:
                log(line)
        elif msg['type'] == 'done':
            result.update(msg)
        elif msg['type'] == 'error':
            errors.append(msg.get('trace') or msg.get('message', ''))

    code = _Proc(cancel or threading.Event()).run(
        [python_exe(root), worker_path(root), '--selftest', '--device', device], on_line)
    if code != 0 or not result:
        raise RuntimeError('轉譜環境自我檢查失敗：\n' + ('\n'.join(errors) or '\n'.join(logs[-20:])))
    return result


def install(cancel: threading.Event, log: Log, progress: Progress,
            variant: Optional[str] = None, root: Optional[str] = None) -> Dict:
    """安裝（或補完）轉譜環境。已完成的步驟會跳過。回傳 installed.json 的內容。"""
    root = root or env_root()
    os.makedirs(root, exist_ok=True)
    if variant is None:
        variant = 'cuda' if has_nvidia_gpu() else 'cpu'
    log('安裝位置：%s' % root)
    log('torch 版本：%s' % ('CUDA 12.8（NVIDIA 顯卡）' if variant == 'cuda' else 'CPU'))
    try:
        os.remove(_marker_path(root))       # 裝到一半的環境不能被當成好的
    except OSError:
        pass

    _install_python(root, cancel, log, progress)
    _install_pip(root, cancel, log, progress)
    log('安裝 torch（這一步最大，請耐心等）…')
    _pip(root, ['torch', '--index-url', TORCH_INDEX[variant]], cancel, log, progress, 'torch')
    log('安裝轉譜套件…')
    _pip(root, PACKAGES, cancel, log, progress, 'packages')
    _install_checkpoint(root, cancel, log, progress)
    shutil.copyfile(bundled_worker_source(), worker_path(root))

    log('自我檢查…')
    progress('selftest', 0, 0)
    check = selftest(root, cancel=cancel, log=log)
    info = {
        'env_version': ENV_VERSION,
        'variant': variant,
        'torch': check.get('torch', ''),
        'cuda': check.get('cuda', ''),
        'device': check.get('device', ''),
        'device_name': check.get('device_name', ''),
    }
    with open(_marker_path(root), 'w', encoding='utf-8') as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    if variant == 'cuda' and info['device'] != 'cuda':
        log('注意：裝的是 GPU 版，但這張顯卡跑不起來，轉譜會用 CPU（%s）' % info['device_name'])
    log('完成：torch %s，使用 %s%s' % (
        info['torch'], 'GPU' if info['device'] == 'cuda' else 'CPU',
        ('（%s）' % info['device_name']) if info['device_name'] else ''))
    return info


def uninstall(root: Optional[str] = None) -> None:
    shutil.rmtree(root or env_root(), ignore_errors=True)


def env_size_mb(root: Optional[str] = None) -> int:
    total = 0
    for folder, _dirs, files in os.walk(root or env_root()):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total // (1024 * 1024)


# ── 轉譜 ────────────────────────────────────────────────────────────────

def _parse(line: str) -> Optional[Dict]:
    if not line.startswith('{'):
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) and 'type' in msg else None


def wav_path_for(audio_path: str) -> str:
    """非 WAV 的音檔，轉譜時順便解碼成的 WAV（製譜器只播得了 WAV）。WAV 就是自己。"""
    if audio_path.lower().endswith('.wav'):
        return audio_path
    return os.path.splitext(audio_path)[0] + '.wav'


def transcribe(audio_path: str, output_path: str, cancel: threading.Event,
               log: Log, progress: Progress, device: str = 'auto',
               root: Optional[str] = None) -> Dict:
    """音檔 → MIDI。回傳 worker 的 done 訊息（notes / pedals / device / elapsed…）。

    非 WAV 的輸入順便輸出一份同名 WAV（已經有了就不覆蓋），結果的 `wav` 欄位是它。
    """
    root = root or env_root()
    if not is_installed(root):
        raise RuntimeError('AI 轉譜環境還沒安裝。')
    # 空檔案時 libsndfile 只會說「檔案不存在」，看不出是下載失敗，先自己擋掉。
    if not os.path.isfile(audio_path):
        raise RuntimeError('找不到音檔：%s' % audio_path)
    if os.path.getsize(audio_path) == 0:
        raise RuntimeError('這個音檔是空的（0 位元組），多半是下載沒有成功，請重新下載：\n%s'
                           % audio_path)
    # 製譜器更新後 worker 可能也改了；每次都用製譜器帶的那份。
    source = bundled_worker_source()
    if os.path.isfile(source):
        shutil.copyfile(source, worker_path(root))

    result: Dict = {}
    errors: List[str] = []

    def on_line(line: str) -> None:
        msg = _parse(line)
        if msg is None:
            log(line)
        elif msg['type'] == 'progress':
            progress(msg.get('stage', ''), int(msg.get('done', 0)), int(msg.get('total', 0)))
        elif msg['type'] == 'log':
            log(msg.get('message', ''))
        elif msg['type'] == 'done':
            result.update(msg)
        elif msg['type'] == 'error':
            errors.append(msg.get('message', ''))
            trace = msg.get('trace')
            if trace:
                log(trace)

    tmp_output = output_path + '.part.mid'
    args = [python_exe(root), worker_path(root), '--input', audio_path,
            '--output', tmp_output, '--checkpoint', checkpoint_path(root),
            '--device', device]
    wav = wav_path_for(audio_path)
    tmp_wav = ''
    if wav != audio_path and not os.path.isfile(wav):
        tmp_wav = wav + '.part.wav'
        args += ['--wav-out', tmp_wav]
    code = _Proc(cancel).run(args, on_line)
    if code != 0 or not result or not os.path.isfile(tmp_output):
        for leftover in (tmp_output, tmp_wav):
            try:
                if leftover:
                    os.remove(leftover)
            except OSError:
                pass
        raise RuntimeError(errors[0] if errors else '轉譜程序異常結束（代碼 %d）。' % code)
    os.replace(tmp_output, output_path)
    if tmp_wav and os.path.isfile(tmp_wav):
        os.replace(tmp_wav, wav)
    result['output'] = output_path
    result['wav'] = wav if os.path.isfile(wav) else ''
    return result
