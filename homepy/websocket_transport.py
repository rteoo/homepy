"""Small, bounded, dependency-free WebSocket transport for Home Assistant."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import struct
import time
from typing import Any

from .config import ConnectionConfig
from .exceptions import ResponseError, TransportError, WebSocketAuthenticationError, WebSocketCommandError


# ceiling: 16 MiB per message; raise only with a reviewed memory budget and fixtures.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
CLOSE_ALLOWANCE = 1.0
_HANDSHAKE_LIMIT = 64 * 1024


class WebSocketTimeout(TimeoutError):
    """Internal receive timeout, kept free of socket and peer details."""


class WebSocketClosed(Exception):
    """Internal signal for a peer close before the caller's expected limit."""


def _reject_constant(_value: str) -> None:
    raise ValueError


def _host_header(config: ConnectionConfig) -> str:
    host = config._hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default = (config._scheme == "https" and config.effective_port == 443) or (
        config._scheme == "http" and config.effective_port == 80
    )
    return host if default else f"{host}:{config.effective_port}"


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    value = deadline - time.monotonic()
    if value <= 0:
        raise WebSocketTimeout
    return value


def _transport_error(exc: BaseException) -> TransportError:
    if isinstance(exc, socket.gaierror):
        return TransportError(category="dns")
    if isinstance(exc, ConnectionRefusedError):
        return TransportError(category="refused")
    if isinstance(exc, (TimeoutError, socket.timeout, WebSocketTimeout)):
        return TransportError(category="timeout")
    if isinstance(exc, ssl.SSLError):
        return TransportError(category="tls")
    return TransportError(category="network")


class WebSocketSession:
    """One authenticated, single-owner WebSocket connection.

    ``send`` accepts an already validated JSON-compatible envelope and
    ``receive`` returns one decoded text message. Ping/pong control traffic is
    handled internally. The session never reconnects or queues unbounded data.
    """

    def __init__(self, sock: socket.socket, config: ConnectionConfig):
        self._socket = sock
        self._config = config
        self._buffer = bytearray()
        self._closed = False
        self._socket_closed = False
        self._fragment_opcode: int | None = None
        self._fragment_data = bytearray()

    def _read(self, size: int, deadline: float | None) -> bytes:
        if size < 0:
            raise ResponseError("invalid WebSocket frame length")
        if len(self._buffer) < size:
            while len(self._buffer) < size:
                if deadline is not None:
                    self._socket.settimeout(_remaining(deadline))
                else:
                    self._socket.settimeout(None)
                try:
                    block = self._socket.recv(max(4096, min(65536, size - len(self._buffer))))
                except socket.timeout:
                    raise WebSocketTimeout from None
                except (OSError, ssl.SSLError) as exc:
                    raise _transport_error(exc) from None
                if not block:
                    raise WebSocketClosed
                self._buffer.extend(block)
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    def _frame(self, deadline: float | None) -> tuple[bool, int, bytes]:
        first, second = self._read(2, deadline)
        fin = bool(first & 0x80)
        if first & 0x70:
            raise ResponseError("WebSocket response used unsupported extensions")
        opcode = first & 0x0F
        if opcode not in {0, 1, 2, 8, 9, 10}:
            raise ResponseError("unsupported WebSocket frame opcode")
        masked = bool(second & 0x80)
        length = second & 0x7F
        if opcode >= 8:
            if not fin or length == 126 or length == 127:
                raise ResponseError("invalid WebSocket control frame")
            if length > 125:
                raise ResponseError("invalid WebSocket control frame")
        if length == 126:
            length = struct.unpack("!H", self._read(2, deadline))[0]
            if length < 126:
                raise ResponseError("noncanonical WebSocket frame length")
        elif length == 127:
            raw = struct.unpack("!Q", self._read(8, deadline))[0]
            if raw & (1 << 63):
                raise ResponseError("invalid WebSocket frame length")
            length = raw
            if length < 65536:
                raise ResponseError("noncanonical WebSocket frame length")
        if length > MAX_MESSAGE_BYTES:
            raise ResponseError("WebSocket message exceeds the maximum permitted size")
        if masked:
            raise ResponseError("WebSocket server frames must not be masked")
        return fin, opcode, self._read(length, deadline)

    def receive(self, timeout: float | None = None) -> dict[str, Any]:
        """Receive and JSON-decode one complete text message."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            fin, opcode, payload = self._frame(deadline)
            if opcode == 9:  # ping
                self._send_frame(10, payload, _remaining(deadline))
                continue
            if opcode == 10:  # pong
                continue
            if opcode == 8:
                if len(payload) == 1:
                    raise ResponseError("invalid WebSocket close frame")
                if len(payload) >= 2:
                    code = struct.unpack("!H", payload[:2])[0]
                    if not (
                        (1000 <= code <= 1015 and code not in {1004, 1005, 1006, 1015})
                        or 3000 <= code <= 4999
                    ):
                        raise ResponseError("invalid WebSocket close code")
                    try:
                        payload[2:].decode("utf-8")
                    except UnicodeDecodeError:
                        raise ResponseError("invalid WebSocket close reason") from None
                # Reply within the bounded shutdown allowance, then surface the
                # close as an incomplete observation to the caller.
                try:
                    if not self._closed:
                        self._send_frame(8, payload[:125], _remaining(deadline))
                except Exception:
                    pass
                self._closed = True
                self._physical_close()
                raise WebSocketClosed
            if opcode in (1, 2):
                if self._fragment_opcode is not None:
                    raise ResponseError("unexpected WebSocket data frame")
                self._fragment_opcode = opcode
                self._fragment_data = bytearray(payload)
            elif opcode == 0:
                if self._fragment_opcode is None:
                    raise ResponseError("unexpected WebSocket continuation frame")
                self._fragment_data.extend(payload)
            else:
                raise ResponseError("unsupported WebSocket frame opcode")
            if len(self._fragment_data) > MAX_MESSAGE_BYTES:
                raise ResponseError("WebSocket message exceeds the maximum permitted size")
            if not fin:
                continue
            opcode = self._fragment_opcode
            payload = bytes(self._fragment_data)
            self._fragment_opcode = None
            self._fragment_data.clear()
            if opcode != 1:
                raise ResponseError("WebSocket response was not text")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise ResponseError("WebSocket response was not valid UTF-8") from None
            try:
                value = json.loads(text, parse_constant=_reject_constant)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError, RecursionError):
                raise ResponseError("WebSocket response was not valid JSON") from None
            if not isinstance(value, dict):
                raise ResponseError("WebSocket response envelope was not an object")
            return value

    def _send_frame(self, opcode: int, payload: bytes, timeout: float | None = None) -> None:
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ResponseError("WebSocket request exceeds the maximum permitted size")
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = bytes((first, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        data = header + mask + masked
        if timeout is not None:
            self._socket.settimeout(timeout)
        try:
            self._socket.sendall(data)
        except (OSError, ssl.SSLError) as exc:
            raise _transport_error(exc) from None

    def send(self, envelope: dict[str, Any], timeout: float | None = None) -> None:
        if not isinstance(envelope, dict):
            raise TypeError("WebSocket envelope must be an object")
        try:
            text = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            payload = text.encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError("WebSocket envelope is not valid JSON") from None
        self._send_frame(1, payload, timeout)

    def _physical_close(self) -> None:
        if getattr(self, "_socket_closed", False):
            return
        self._socket_closed = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError):
            pass
        try:
            self._socket.close()
        except OSError:
            pass

    def close(self, timeout: float = CLOSE_ALLOWANCE) -> None:
        if getattr(self, "_socket_closed", False):
            return
        self._closed = True
        try:
            self._send_frame(8, b"", timeout)
        except Exception:
            pass
        finally:
            # A caller's KeyboardInterrupt must never bypass physical cleanup.
            self._physical_close()


class WebSocketTransport:
    """Authenticated one-command-per-connection Home Assistant transport."""

    def __init__(self, config: ConnectionConfig):
        if not isinstance(config, ConnectionConfig):
            raise TypeError("config must be a ConnectionConfig")
        self.config = config

    def _connect_socket(self, deadline: float) -> socket.socket:
        timeout = _remaining(deadline)
        sock: socket.socket | None = None
        # socket.create_connection performs OS DNS synchronously; a resolver
        # call may exceed this budget and is reported as a known limitation.
        try:
            sock = socket.create_connection(
                (self.config._hostname, self.config.effective_port), timeout=timeout
            )
            if self.config._scheme == "https":
                try:
                    if self.config.verify_ssl:
                        context = ssl.create_default_context(cafile=self.config.ca_file)
                    else:
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                except (OSError, ValueError):
                    try:
                        sock.close()
                    except OSError:
                        pass
                    raise TransportError(category="tls") from None
                try:
                    sock.settimeout(_remaining(deadline))
                    sock = context.wrap_socket(sock, server_hostname=self.config._hostname)
                except ssl.SSLError:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    raise TransportError(category="tls") from None
                except OSError as exc:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    raise _transport_error(exc) from None
            return sock
        except TransportError:
            raise
        except Exception as exc:
            raise _transport_error(exc) from None
        except BaseException:
            if sock is not None:
                sock.close()
            raise

    def _handshake(self, sock: socket.socket, deadline: float) -> WebSocketSession:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = f"{self.config.path_prefix}/api/websocket" or "/api/websocket"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {_host_header(self.config)}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            sock.settimeout(_remaining(deadline))
            sock.sendall(request)
            buffer = bytearray()
            marker = b"\r\n\r\n"
            while marker not in buffer:
                if len(buffer) >= _HANDSHAKE_LIMIT:
                    raise ResponseError("WebSocket handshake response was too large")
                sock.settimeout(_remaining(deadline))
                chunk = sock.recv(min(4096, _HANDSHAKE_LIMIT - len(buffer)))
                if not chunk:
                    raise ResponseError("WebSocket handshake response was truncated")
                buffer.extend(chunk)
            head_end = buffer.index(marker) + len(marker)
            head = bytes(buffer[:head_end])
            leftover = bytes(buffer[head_end:])
            lines = head[:-4].split(b"\r\n")
            if not lines or lines[0].split(b" ", 2)[:2] != [b"HTTP/1.1", b"101"]:
                raise TransportError(category="network")
            headers: dict[str, str] = {}
            singleton_headers = {"upgrade", "connection", "sec-websocket-key", "sec-websocket-accept"}
            for line in lines[1:]:
                if b":" not in line:
                    raise ResponseError("WebSocket handshake response was malformed")
                name, value = line.split(b":", 1)
                try:
                    name_text = name.decode("ascii").lower()
                    value_text = value.decode("ascii").strip()
                except UnicodeDecodeError:
                    raise ResponseError("WebSocket handshake response was malformed") from None
                if not name_text or any(
                    not (char.isalnum() or char in "!#$%&'*+-.^_`|~") for char in name_text
                ):
                    raise ResponseError("WebSocket handshake response was malformed")
                if name_text in singleton_headers and name_text in headers:
                    raise ResponseError("WebSocket handshake response was malformed")
                headers[name_text] = value_text
            if "websocket" not in {token.strip().lower() for token in headers.get("upgrade", "").split(",")}:
                raise ResponseError("WebSocket handshake response was malformed")
            if "upgrade" not in {token.strip().lower() for token in headers.get("connection", "").split(",")}:
                raise ResponseError("WebSocket handshake response was malformed")
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            if headers.get("sec-websocket-accept") != expected:
                raise ResponseError("WebSocket handshake response was malformed")
            if "sec-websocket-protocol" in headers:
                raise ResponseError("WebSocket handshake response negotiated an unsupported protocol")
            if "sec-websocket-extensions" in headers:
                raise ResponseError("WebSocket handshake response negotiated an unsupported extension")
            session = WebSocketSession(sock, self.config)
            session._buffer.extend(leftover)
            return session
        except (TransportError, ResponseError):
            raise
        except (OSError, ssl.SSLError) as exc:
            raise _transport_error(exc) from None

    def open(self, *, deadline: float | None = None) -> WebSocketSession:
        deadline = time.monotonic() + self.config.timeout if deadline is None else deadline
        sock = self._connect_socket(deadline)
        session: WebSocketSession | None = None
        success = False
        try:
            session = self._handshake(sock, deadline)
            hello = session.receive(_remaining(deadline))
            if hello.get("type") != "auth_required":
                raise ResponseError("WebSocket authentication envelope was invalid")
            session.send({"type": "auth", "access_token": self.config.token}, _remaining(deadline))
            auth = session.receive(_remaining(deadline))
            if auth.get("type") == "auth_invalid":
                raise WebSocketAuthenticationError()
            if auth.get("type") != "auth_ok":
                raise ResponseError("WebSocket authentication envelope was invalid")
            success = True
            return session
        except WebSocketTimeout:
            raise TransportError(category="timeout") from None
        except WebSocketClosed:
            raise TransportError(category="network") from None
        except (TransportError, ResponseError, WebSocketAuthenticationError):
            raise
        except Exception as exc:
            raise _transport_error(exc) from None
        finally:
            # On failure the authenticated session is still closed, and a
            # handshake failure closes the raw socket without read-ahead loss.
            if not success and session is not None:
                session.close(CLOSE_ALLOWANCE)
            elif not success:
                try:
                    sock.close()
                except OSError:
                    pass

    def request(self, command: str) -> Any:
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        deadline = time.monotonic() + self.config.timeout
        session: WebSocketSession | None = None
        try:
            session = self.open(deadline=deadline)
            session.send({"id": 1, "type": command}, _remaining(deadline))
            result = session.receive(_remaining(deadline))
            if type(result.get("id")) is not int or result.get("id") != 1 or result.get("type") != "result":
                raise ResponseError("WebSocket command response envelope was invalid")
            if type(result.get("success")) is not bool:
                raise ResponseError("WebSocket command response envelope was invalid")
            if result["success"] is not True:
                error = result.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                if not isinstance(code, str) or code not in {"unknown_command", "unauthorized", "invalid_format"}:
                    code = "unknown_error"
                raise WebSocketCommandError(command_code=code)
            if "result" not in result:
                raise ResponseError("WebSocket command response envelope was invalid")
            return result.get("result")
        except WebSocketTimeout:
            raise TransportError(category="timeout") from None
        except WebSocketClosed:
            raise TransportError(category="network") from None
        finally:
            if session is not None:
                session.close(CLOSE_ALLOWANCE)


__all__ = ["MAX_MESSAGE_BYTES", "WebSocketSession", "WebSocketTransport"]
