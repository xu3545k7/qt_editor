"""啟動器 → 製譜器：已經開著的製譜器直接叫它開譜，不另開一個。

製譜器啟動時開一個本機具名管道（QLocalServer）；啟動器送一行 JSON：
    {"open": "<譜面路徑>"}   開這份譜（目前的譜沒存會先問）
    {"show": true}           帶到最前面
    {"lang": "en"}           換介面語言（可以和上面兩個一起送，先換語言）
連不上就表示製譜器沒開，由啟動器自己執行 exe。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from PyQt5.QtCore import QObject
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

try:
    from PyQt5 import sip
except ImportError:                                 # 舊版 PyQt5 的 sip 在最上層
    try:
        import sip                                  # type: ignore[no-redef]
    except ImportError:
        sip = None                                  # type: ignore[assignment]

SERVER_NAME = 'NosMania.ChartEditor'


def _deleted(obj) -> bool:
    """這個 QObject 底下的 C++ 物件是不是已經被刪掉了（Python 這邊還拿得到 wrapper）。"""
    if sip is None:
        return False
    try:
        return bool(sip.isdeleted(obj))
    except (TypeError, RuntimeError):
        return False


def allow_foreground() -> None:
    """Windows 只讓前景程式把別的視窗帶到前面；送訊息前先放行。"""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)     # ASFW_ANY
    except Exception:                                   # noqa: BLE001
        pass


def send(message: dict, timeout_ms: int = 800) -> bool:
    """送給開著的製譜器；沒有開著的就回傳 False。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    allow_foreground()
    sock.write(json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n')
    ok = sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return ok


class EditorServer(QObject):
    """掛在製譜器主視窗上，收到訊息就開譜或顯示。"""

    def __init__(self, window, name: str = SERVER_NAME):
        super().__init__(window)
        self._window = window
        self._server = QLocalServer(self)
        self._buffers = {}
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(name):
            # 已經有另一個製譜器在聽：這一個就不接，啟動器會找到先開的那個
            logging.info('editor ipc: listen failed (%s)', self._server.errorString())

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        """不再接受連線，並把還連著的 socket 的訊號斷開。

        socket 是排程刪除（`deleteLater`）的，可能活得比伺服器久；訊號留著沒斷，
        之後才送到的 `disconnected` 會叫回已經沒有 C++ 物件的自己身上 —— 關掉
        製譜器視窗時就是一聲 RuntimeError。
        """
        for sock in list(self._buffers):
            self._detach(sock)
        self._server.close()

    def _detach(self, sock: QLocalSocket) -> None:
        """這個 socket 處理完了：忘掉它的緩衝，也不要再收它的訊號。"""
        self._buffers.pop(sock, None)
        try:
            sock.readyRead.disconnect()
            sock.disconnected.disconnect()
        except (RuntimeError, TypeError):            # 已被刪掉，或本來就沒接上
            pass

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b''
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._on_ready(s, final=True))

    def _on_ready(self, sock: QLocalSocket, final: bool = False) -> None:
        # 視窗關掉之後才送到的訊號：這時碰 self 的任何屬性都會丟 RuntimeError
        # （wrapper 還在、C++ 物件沒了）。什麼都不做就好。
        if _deleted(self) or _deleted(sock):
            return
        data = self._buffers.get(sock, b'') + bytes(sock.readAll())
        *lines, rest = data.split(b'\n')
        self._buffers[sock] = rest
        if final:
            if rest.strip():
                lines.append(rest)
            self._detach(sock)
            sock.deleteLater()
        for line in lines:
            self.handle(line)

    def handle(self, line: bytes) -> Optional[dict]:
        try:
            message = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
        if message.get('lang'):
            self._window.apply_language(message['lang'])
        if message.get('library'):
            try:
                from .app import remember_library
            except ImportError:
                from qt_editor.app import remember_library
            remember_library(message['library'])
        path = message.get('open')
        if path:
            self._window.open_chart_from_library(path)
        elif message.get('show'):
            self._window.enter_editor()
        return message
