import threading
import unittest
from unittest.mock import patch

from homepy.config import ConnectionConfig
from homepy.exceptions import ResponseError, TransportError, WebSocketAuthenticationError, WebSocketCommandError
from homepy.websocket_transport import WebSocketTransport

try:
    from ws_fixture import WebSocketFixture
except ModuleNotFoundError:  # direct module invocation from the repository root
    from tests.ws_fixture import WebSocketFixture


class WebSocketTransportTests(unittest.TestCase):
    def config(self, fixture, **kwargs):
        values = {"token": "synthetic-secret", "host": fixture.host, "timeout": 2}
        values.update(kwargs)
        return ConnectionConfig(**values)

    def test_auth_command_and_prefix(self):
        ready = threading.Event()

        def script(sock, server):
            server.send_json(sock, {"type": "auth_required", "ha_version": "2026.9.1"})
            auth = server.recv_json(sock)
            self.assertEqual(auth["type"], "auth")
            self.assertEqual(auth["access_token"], "synthetic-secret")
            server.send_json(sock, {"type": "auth_ok", "ha_version": "2026.9.1"}, fragmented=True)
            command = server.recv_json(sock)
            self.assertEqual(command, {"id": 1, "type": "config/area_registry/list"})
            server.send_json(sock, {"id": 1, "type": "result", "success": True, "result": [{"area_id": "kitchen", "extra": 1}]})
            ready.set()

        fixture = WebSocketFixture(script)
        try:
            result = WebSocketTransport(self.config(fixture)).request("config/area_registry/list")
            self.assertEqual(result[0]["extra"], 1)
            self.assertTrue(ready.wait(1))
            self.assertEqual(fixture.error, None)
        finally:
            fixture.thread.join(2)

    def test_authentication_failure_is_safe(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            auth = server.recv_json(sock)
            self.assertEqual(auth["type"], "auth")
            server.send_json(sock, {"type": "auth_invalid", "message": "synthetic-secret"})

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(WebSocketAuthenticationError) as caught:
                WebSocketTransport(self.config(fixture)).request("x")
            self.assertNotIn("synthetic-secret", str(caught.exception))
        finally:
            fixture.thread.join(2)

    def test_failed_command_has_stable_code(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            server.recv_json(sock)
            server.send_json(sock, {"id": 1, "type": "result", "success": False, "error": {"code": ["secret"]}})

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(WebSocketCommandError) as caught:
                WebSocketTransport(self.config(fixture)).request("unknown")
            self.assertEqual(caught.exception.command_code, "unknown_error")
        finally:
            fixture.thread.join(2)

    def test_wrong_id_and_malformed_json_are_response_errors(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            server.recv_json(sock)
            server.send_json(sock, {"id": True, "type": "result", "success": True, "result": []})

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(ResponseError):
                WebSocketTransport(self.config(fixture)).request("x")
        finally:
            fixture.thread.join(2)

    def test_redirect_and_unsolicited_negotiation_are_rejected(self):
        for headers in ((), (b"Sec-WebSocket-Extensions: permessage-deflate",)):
            with self.subTest(headers=headers):
                fixture = WebSocketFixture(
                    lambda *_args: None,
                    status_line=b"HTTP/1.1 302 Found" if not headers else b"HTTP/1.1 101 Switching Protocols",
                    response_headers=headers,
                )
                try:
                    with self.assertRaises((TransportError, ResponseError)):
                        WebSocketTransport(self.config(fixture)).request("x")
                finally:
                    fixture.thread.join(2)

    def test_binary_invalid_json_and_oversized_frames_are_rejected(self):
        scripts = (
            lambda sock, server: self._after_auth(sock, server, lambda: server.send_binary(sock, b"binary")),
            lambda sock, server: self._after_auth(sock, server, lambda: server.send_raw(sock, b"\x81\x01{")),
            lambda sock, server: self._after_auth(sock, server, lambda: server.send_raw(sock, b"\x81\x7f" + (16 * 1024 * 1024 + 1).to_bytes(8, "big"))),
        )
        for script in scripts:
            fixture = WebSocketFixture(script)
            try:
                with self.assertRaises(ResponseError):
                    WebSocketTransport(self.config(fixture)).request("x")
            finally:
                fixture.thread.join(2)

    def test_ping_is_answered_while_receiving_fragmented_result(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            server.recv_json(sock)
            server.send_ping(sock, b"probe")
            server.send_json(sock, {"id": 1, "type": "result", "success": True, "result": {"ok": True}}, fragmented=True)
            opcode, payload = server.recv_frame(sock)
            self.assertEqual((opcode, payload), (10, b"probe"))

        fixture = WebSocketFixture(script)
        try:
            self.assertEqual(WebSocketTransport(self.config(fixture)).request("x"), {"ok": True})
        finally:
            fixture.thread.join(2)
            self.assertIsNone(fixture.error)

    def test_partial_frame_consumes_overall_deadline(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            server.recv_json(sock)
            payload = b'{"id":1,"type":"result","success":true,"result":null}'
            sock.sendall(bytes((0x81, len(payload))) + payload[:1])
            threading.Event().wait(0.15)
            sock.sendall(payload[1:])

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(TransportError) as caught:
                WebSocketTransport(self.config(fixture, timeout=0.05)).request("x")
            self.assertEqual(caught.exception.category, "timeout")
        finally:
            fixture.thread.join(2)

    def test_nested_json_is_safely_rejected(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            server.recv_json(sock)
            nested = b"[" * 20000 + b"]" * 20000
            server.send_raw(sock, bytes((0x81, 126)) + len(nested).to_bytes(2, "big") + nested)

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(ResponseError):
                WebSocketTransport(self.config(fixture)).request("x")
        finally:
            fixture.thread.join(2)

    def _after_auth(self, sock, server, response):
        server.send_json(sock, {"type": "auth_required"})
        server.recv_json(sock)
        server.send_json(sock, {"type": "auth_ok"})
        server.recv_json(sock)
        response()


if __name__ == "__main__":
    unittest.main()
