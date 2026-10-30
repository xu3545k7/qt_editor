# -*- coding: utf-8 -*-
"""補裝 AI 轉譜環境缺的元件。不用開製譜器。

轉譜環境（約 5.7GB）本來就裝好了，但後來多出來的元件不會自己補上。把
`ENV_VERSION` 加一號會讓整個環境被當成沒裝、跳出重裝 5.7GB 的流程，而缺的
通常只有幾十 MB——所以走「缺什麼補什麼」這條路。

    python install_transcribe_extras.py              # 補必要的（目前＝transkun）
    python install_transcribe_extras.py --all        # 連分離器一起（+699MB）
    python install_transcribe_extras.py --list       # 只看現在有什麼
    python install_transcribe_extras.py transkun     # 指名要哪幾個

跑完會自己做一次自我檢查，並把實際裝到的版本印出來。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_editor import ai_transcribe as AT        # noqa: E402


def log(message: str) -> None:
    print(message, flush=True)


def progress(stage: str, done: int, total: int) -> None:
    if total:
        print('  %s %d/%d' % (stage, done, total), end='\r', flush=True)


def show_status(root: str) -> None:
    print('轉譜環境：%s' % root)
    if not AT.is_installed(root):
        print('  ✗ 還沒裝好。請先在製譜器裡裝一次（檔案 → AI 轉譜）。')
        return
    info = AT.installed_info(root) or {}
    print('  torch %s（%s）／裝置 %s %s'
          % (info.get('torch', '?'), info.get('variant', '?'),
             info.get('device', '?'), info.get('device_name', '')))
    status = AT.component_status(root)
    for key, label, required, size in AT.COMPONENTS:
        mark = '✓' if status.get(key) else '✗'
        tail = '' if status.get(key) else '（約 %d MB%s）' % (
            size, '，必要' if required else '，選配')
        print('  %s %s%s' % (mark, label, tail))


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    keys = [key for key, _l, _r, _s in AT.COMPONENTS]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # 不用 argparse 的 choices：配上 nargs='*' 的話，什麼都不給（空 list）
    # 也會被拿去比對選項、報「invalid choice: []」。自己擋，訊息也清楚一點。
    parser.add_argument('components', nargs='*',
                        help='指名要補哪幾個（%s）；不給就補必要的' % '／'.join(keys))
    parser.add_argument('--all', action='store_true', help='連選配的也一起補')
    parser.add_argument('--list', action='store_true', help='只看狀態，不安裝')
    parser.add_argument('--root', help='轉譜環境的位置（預設照製譜器的設定）')
    args = parser.parse_args()

    root = args.root or AT.env_root()
    show_status(root)
    if args.list:
        return 0
    if not AT.is_installed(root):
        return 1

    want = list(args.components)
    unknown = [k for k in want if k not in keys]
    if unknown:
        print()
        print('不認得的元件：%s（可以用的：%s）' % ('、'.join(unknown), '、'.join(keys)))
        return 2
    if not want:
        want = AT.missing_components(root, required_only=not args.all)
    else:
        status = AT.component_status(root)
        want = [k for k in want if not status.get(k)]
    if not want:
        print()
        print('沒有要補的，收工。')
        return 0

    labels = {k: label for k, label, _r, _s in AT.COMPONENTS}
    print()
    print('要補：%s' % '、'.join(labels.get(k, k) for k in want))
    try:
        failed = AT.install_components(want, threading.Event(), log, progress, root)
    except AT.Cancelled:
        print('已取消。')
        return 1
    print()
    show_status(root)
    if failed:
        print()
        print('這些還是不能用：%s' % '、'.join(labels.get(k, k) for k in failed))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
