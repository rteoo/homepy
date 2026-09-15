"""Independent checks of malformed peers and lifecycle budgets."""

import json
import socket
import struct
import time
import traceback
import unittest
from unittest.mock import Mock

from homepy import ConnectionConfig, HomeAssistant, ResponseError, TransportError
from homepy.websocket_transport import WebSocketSession
from ws_fixture import WebSocketFixture


class AdversarialWebSocketTests(unittest.TestCase):
    @staticmethod
    def authenticated(sock, server):
        server.send_json(sock, {"type": "auth_required"})
        server.recv_json(sock)
        server.send_json(sock, {"type": "auth_ok"})
        server.recv_json(sock)

    def test_malformed_frames_fail_without_exposing_peer_data(self):
        for frame in (
            b"\x81\x81xxxx!",  # Masked server data.
            b"\x81\x7e\x00\x01x",  # Noncanonical 16-bit size.
            b"\x81\x7f" + struct.pack("!Q", 125),
            b"\x81\x7f" + struct.pack("!Q", 1 << 63),
            b"\x80\x02{}",  # Orphan continuation.
            b"\x89\x7e",  # Oversized control frame.
            b"\x09\x00",  # Fragmented ping.
            b"\xc1\x02{}",  # Unnegotiated RSV extension.
            b"\x83\x00",  # Reserved opcode.
            b"\x81\x01\xff",  # Invalid UTF-8.
            b"\x88\x01x",  # Invalid close payload length.
            b"\x88\x02\x03\xed",  # Forbidden close code 1005.
            b"\x88\x03\x03\xe8\xff",  # Invalid close reason UTF-8.
        ):
            with self.subTest(frame=frame):
                def script(sock, server):
                    self.authenticated(sock, server)
                    sock.sendall(frame)

                server = WebSocketFixture(script)
                try:
                    with self.assertRaises(ResponseError) as caught:
                        HomeAssistant("synthetic-private-token", host=server.host).get_areas()
                    rendered = "".join(traceback.format_exception(caught.exception))
                    self.assertNotIn("synthetic-private-token", rendered)
                finally:
                    server.thread.join(2)
                self.assertFalse(server.thread.is_alive())
                self.assertIsNone(server.error)

    def test_continuous_partial_progress_does_not_reset_deadline(self):
        def script(sock, server):
            self.authenticated(sock, server)
            payload = json.dumps({"id": 1, "type": "result", "success": True, "result": []}).encode()
            try:
                sock.sendall(bytes((0x81, len(payload))))
                for index in range(0, len(payload), 4):
                    sock.sendall(payload[index:index + 4])
                    time.sleep(0.05)
            except OSError:
                pass  # Client closes when the overall budget expires.

        server = WebSocketFixture(script)
        start = time.monotonic()
        try:
            with self.assertRaises(TransportError) as caught:
                HomeAssistant("synthetic-token", host=server.host, timeout=0.3).get_areas()
            self.assertEqual(caught.exception.category, "timeout")
            self.assertLess(time.monotonic() - start, 1.5)
        finally:
            server.thread.join(2)
        self.assertFalse(server.thread.is_alive())
        self.assertIsNone(server.error)

    def test_interrupt_during_close_still_disposes_socket(self):
        sock = Mock(spec=socket.socket)
        sock.sendall.side_effect = KeyboardInterrupt
        session = WebSocketSession(sock, ConnectionConfig("synthetic-token"))
        with self.assertRaises(KeyboardInterrupt):
            session.close()
        sock.close.assert_called_once()
        session.close()
        sock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
