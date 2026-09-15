"""Tiny RFC 6455 loopback fixture used by the dependency-free WS tests."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from collections.abc import Callable


def _read_until(sock: socket.socket, marker: bytes) -> bytes:
    data = bytearray()
    while marker not in data:
        block = sock.recv(4096)
        if not block:
            raise ConnectionError("fixture peer closed")
        data.extend(block)
    return bytes(data)


def _recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    first, second = _read_exact(sock, 2)
    if second & 0x80 == 0:
        raise AssertionError("fixture expected a masked client frame")
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4)
    payload = _read_exact(sock, length)
    return first & 0x0F, bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        block = sock.recv(count - len(data))
        if not block:
            raise ConnectionError("fixture peer closed")
        data.extend(block)
    return bytes(data)


def _send_frame(sock: socket.socket, opcode: int, payload: bytes, *, fragmented: bool = False) -> None:
    if fragmented and len(payload) > 1:
        split = len(payload) // 2
        first_payload = payload[:split]
        sock.sendall(bytes((opcode, len(first_payload))) + first_payload)
        _send_frame(sock, 0, payload[split:])
        return
    if len(payload) < 126:
        header = bytes((0x80 | opcode, len(payload)))
    elif len(payload) <= 65535:
        header = bytes((0x80 | opcode, 126)) + struct.pack("!H", len(payload))
    else:
        header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", len(payload))
    sock.sendall(header + payload)


class WebSocketFixture:
    """Threaded one-shot server; ``script`` owns protocol-level assertions."""

    def __init__(
        self,
        script: Callable[[socket.socket, "WebSocketFixture"], None],
        *,
        status_line: bytes = b"HTTP/1.1 101 Switching Protocols",
        response_headers: tuple[bytes, ...] = (),
    ):
        self.script = script
        self.status_line = status_line
        self.response_headers = response_headers
        self.received: list[dict] = []
        self.error: BaseException | None = None
        self._stopped = threading.Event()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self._server.settimeout(5)
        self.port = self._server.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.port}/fixture"

    def _run(self) -> None:
        try:
            sock, _ = self._server.accept()
            with sock:
                sock.settimeout(5)
                request = _read_until(sock, b"\r\n\r\n")
                headers = dict(
                    line.split(b":", 1) for line in request.split(b"\r\n")[1:-2] if b":" in line
                )
                key = headers[b"Sec-WebSocket-Key"].strip()
                accept = base64.b64encode(
                    hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
                )
                response = self.status_line + b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                response += b"Sec-WebSocket-Accept: " + accept + b"\r\n"
                if self.response_headers:
                    response += b"\r\n".join(self.response_headers) + b"\r\n"
                response += b"\r\n"
                sock.sendall(response)
                if self.status_line != b"HTTP/1.1 101 Switching Protocols":
                    return
                self.script(sock, self)
        except BaseException as exc:
            if not self._stopped.is_set():
                self.error = exc
        finally:
            self._server.close()

    def close(self) -> None:
        """Stop an idle fixture listener and wait for its worker."""
        self._stopped.set()
        try:
            self._server.close()
        except OSError:
            pass
        self.thread.join(2)

    def send_json(self, sock: socket.socket, value: dict, *, fragmented: bool = False) -> None:
        _send_frame(sock, 1, json.dumps(value, separators=(",", ":")).encode(), fragmented=fragmented)

    def recv_json(self, sock: socket.socket) -> dict:
        opcode, payload = _recv_frame(sock)
        if opcode != 1:
            raise AssertionError(f"unexpected opcode {opcode}")
        value = json.loads(payload)
        self.received.append(value)
        return value

    def recv_frame(self, sock: socket.socket) -> tuple[int, bytes]:
        return _recv_frame(sock)

    def send_raw(self, sock: socket.socket, data: bytes) -> None:
        sock.sendall(data)

    def send_ping(self, sock: socket.socket, payload: bytes = b"ping") -> None:
        _send_frame(sock, 9, payload)

    def send_binary(self, sock: socket.socket, payload: bytes) -> None:
        _send_frame(sock, 2, payload)

    def send_close(self, sock: socket.socket, payload: bytes = b"\x03\xe8") -> None:
        _send_frame(sock, 8, payload)
