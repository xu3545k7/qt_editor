"""AI 鋼琴轉譜的工作程序：音檔 → MIDI（含力度與 CC64 踏板）。

**這支不在製譜器裡執行。** torch 動輒好幾 GB，塞不進單檔 exe，所以它跑在
`ai_transcribe` 另外裝好的 Python 環境裡，製譜器用子程序叫它、讀它的 stdout。

模型是 ByteDance 的 piano_transcription_inference（高解析度鋼琴轉譜，
只用獨奏鋼琴訓練過——丟整首混音會轉出一堆雜音）。這裡只借它的模型與後處理，
讀音檔和分段推論自己做：
- 它自帶的 load_audio 只走 ffmpeg，Windows 上通常沒有；改用 soundfile，
  wav / flac / ogg / mp3 都讀得到。
- 它的 forward 一次只推一段、進度用 print 印；換成可調批次、印 JSON 進度。

stdout 協定：每行一個 JSON（`{"type": "progress"|"log"|"done"|"error", ...}`），
不是 JSON 的行（套件自己 print 的）呼叫端當成一般紀錄。
"""

import argparse
import json
import os
import sys
import time
import traceback


def emit(kind, **fields):
    fields['type'] = kind
    sys.stdout.write(json.dumps(fields, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def _apple_chip_name():
    """晶片型號（例如 `Apple M4`）。CUDA 那邊回報的是顯卡名字，這裡對應。"""
    import subprocess

    try:
        out = subprocess.run(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return 'MPS'
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else 'MPS'


def _mps_available(torch):
    backend = getattr(torch.backends, 'mps', None)
    try:
        return bool(backend is not None and backend.is_available())
    except Exception:                           # noqa: BLE001
        return False


def _mps_mismatch(torch):
    """拿 CPU 當基準對一遍 MPS 的結果，不一致就回傳原因（一致回空字串）。

    MPS 的 kernel 不像 CUDA 那麼成熟，模型用到的卷積與雙向 GRU 在某些 torch
    版本上會算錯（不是報錯，是安靜地算出別的數字）。轉出一整首錯的譜比慢一點
    糟糕得多，所以先用同一組權重跑這兩種運算比對。
    """
    from torch import nn

    torch.manual_seed(0)
    image = torch.randn(1, 1, 16, 16)
    sequence = torch.randn(1, 12, 8)
    conv = nn.Conv2d(1, 4, 3)
    gru = nn.GRU(8, 8, batch_first=True, bidirectional=True)
    device = torch.device('mps')
    with torch.no_grad():
        want = (conv(image), gru(sequence)[0])
        got = (conv.to(device)(image.to(device)),
               gru.to(device)(sequence.to(device))[0])
        for expected, actual in zip(want, got):
            gap = (expected - actual.cpu()).abs().max().item()
            if gap > 1e-3:
                return '最大誤差 %.4g' % gap
    return ''


def pick_device(requested):
    import torch

    if requested == 'cpu':
        return 'cpu', ''
    if requested in ('auto', 'mps') and _mps_available(torch):
        try:
            mismatch = _mps_mismatch(torch)
        except Exception as exc:                # noqa: BLE001
            return 'cpu', 'MPS 無法執行（%s），改用 CPU' % str(exc).splitlines()[0]
        if mismatch:
            return 'cpu', 'MPS 算出來的結果和 CPU 不一致（%s），改用 CPU' % mismatch
        return 'mps', _apple_chip_name()
    if requested == 'mps':
        return 'cpu', 'torch 看不到 MPS，改用 CPU'
    if not torch.cuda.is_available():
        if requested == 'cuda':
            return 'cpu', 'torch 看不到 CUDA，改用 CPU'
        return 'cpu', ''
    # is_available() 為真不代表這張卡有對應的 kernel（新卡配舊 torch 會在第一個
    # 運算才炸 "no kernel image"），先實際跑一個卷積試試。
    try:
        x = torch.randn(1, 1, 8, 8, device='cuda')
        w = torch.randn(1, 1, 3, 3, device='cuda')
        torch.nn.functional.conv2d(x, w).sum().item()
    except Exception as exc:                    # noqa: BLE001
        return 'cpu', 'GPU 無法執行（%s），改用 CPU' % str(exc).splitlines()[0]
    return 'cuda', torch.cuda.get_device_name(0)


def _default_batch(device):
    """一次推幾段。MPS 的記憶體和系統共用，批次開太大會被換頁拖慢。"""
    return {'cuda': 8, 'mps': 4}.get(device, 1)


def load_audio(path, sample_rate):
    import numpy as np
    import soundfile

    try:
        data, rate = soundfile.read(path, dtype='float32', always_2d=True)
    except Exception as exc:                    # noqa: BLE001
        raise RuntimeError('讀不到這個音檔（支援 wav / flac / ogg / mp3）：%s' % exc)
    audio = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if rate != sample_rate:
        import librosa
        audio = librosa.resample(audio, orig_sr=rate, target_sr=sample_rate)
    return np.ascontiguousarray(audio, dtype=np.float32), rate, len(data) / float(rate), data


def make_forward(chunk):
    """取代套件的 forward：可批次、回報 JSON 進度。輸出格式與原版相同。"""
    import numpy as np
    import torch

    def forward(model, x, batch_size=1):   # 套件用關鍵字傳 batch_size=1，忽略它
        output = {}
        device = next(model.parameters()).device
        total = len(x)
        emit('progress', stage='transcribe', done=0, total=total)
        model.eval()
        for start in range(0, total, chunk):
            batch = torch.as_tensor(np.asarray(x[start:start + chunk], dtype=np.float32),
                                    device=device)
            with torch.no_grad():
                result = model(batch)
            for key, value in result.items():
                output.setdefault(key, []).append(value.data.cpu().numpy())
            emit('progress', stage='transcribe', done=min(total, start + chunk),
                 total=total)
        return {key: np.concatenate(parts, axis=0) for key, parts in output.items()}

    return forward


def selftest(args):
    import torch
    import piano_transcription_inference  # noqa: F401  確認整條依賴都 import 得起來

    device, note = pick_device(args.device)
    emit('done', torch=torch.__version__, cuda=torch.version.cuda or '',
         device=device, device_name=note)


def transcribe(args):
    import torch
    from piano_transcription_inference import PianoTranscription, inference, sample_rate

    started = time.time()
    device, note = pick_device(args.device)
    if note:
        emit('log', message=('使用 %s：%s' % (device.upper(), note))
             if device != 'cpu' else note)
    emit('progress', stage='load_audio', done=0, total=1)
    audio, native_rate, seconds, original = load_audio(args.input, sample_rate)
    emit('log', message='音檔 %.1f 秒（%d Hz）' % (seconds, native_rate))
    if args.wav_out:
        # 製譜器只播得了 WAV：mp3 / ogg / flac 順便解碼一份原音質的 WAV 放旁邊
        import soundfile
        soundfile.write(args.wav_out, original, native_rate, subtype='PCM_16')
        emit('log', message='已轉出 WAV：%s' % args.wav_out)
    del original

    emit('progress', stage='load_model', done=0, total=1)
    # torch 2.6 起 torch.load 預設 weights_only=True，這份舊 checkpoint 裡除了
    # 權重還有別的物件；檔案在安裝時已驗過 SHA256，放心關掉。
    original_load = torch.load

    def _load(*a, **kw):
        kw.setdefault('weights_only', False)
        return original_load(*a, **kw)

    torch.load = _load
    try:
        transcriptor = PianoTranscription(device=torch.device(device),
                                          checkpoint_path=args.checkpoint)
    finally:
        torch.load = original_load

    # 套件只認得 'cuda' 和 'cpu'（它拿字串比對），給它 mps 會被當成 CPU。自己把
    # 模型搬過去就好：推論的批次是照模型所在的裝置建的（見 make_forward）。
    transcriptor.model.to(torch.device(device))
    transcriptor.device = torch.device(device)

    batch = args.batch if args.batch > 0 else _default_batch(device)
    inference.forward = make_forward(batch)
    result = transcriptor.transcribe(audio, args.output)
    emit('done', output=args.output, notes=len(result['est_note_events']),
         pedals=len(result['est_pedal_events'] or []), seconds=round(seconds, 3),
         device=device, elapsed=round(time.time() - started, 2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selftest', action='store_true')
    parser.add_argument('--input')
    parser.add_argument('--output')
    parser.add_argument('--checkpoint')
    parser.add_argument('--device', default='auto', choices=('auto', 'cuda', 'mps', 'cpu'))
    parser.add_argument('--batch', type=int, default=0)
    parser.add_argument('--wav-out', dest='wav_out')
    args = parser.parse_args()
    try:
        if args.selftest:
            selftest(args)
        else:
            transcribe(args)
    except Exception as exc:                    # noqa: BLE001
        emit('error', message=str(exc) or exc.__class__.__name__,
             trace=traceback.format_exc())
        return 1
    return 0


if __name__ == '__main__':
    os.environ.setdefault('MPLBACKEND', 'Agg')   # models.py 會 import pyplot
    sys.exit(main())
