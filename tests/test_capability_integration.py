"""Public capability checks over real sockets; no Home Assistant instance."""

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from homepy import HomeAssistant, TransportError
from homepy.agent import AgentTools
from ws_fixture import WebSocketFixture, _read_until, _recv_frame, _send_frame


CERTS = Path(__file__).parent / "certs"
ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-capability-token"


@contextmanager
def tls_registry_server():
    """A one-connection TLS peer with a test-only localhost certificate."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERTS / "localhost-cert.pem", CERTS / "localhost-key.pem")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    captured, failures = [], []

    def serve():
        try:
            raw, _ = listener.accept()
            with raw:
                raw.settimeout(3)
                try:
                    connection = context.wrap_socket(raw, server_side=True)
                except (ssl.SSLError, ConnectionResetError):
                    return  # Expected when the client rejects CA or hostname.
                with connection:
                    request = _read_until(connection, b"\r\n\r\n")
                    captured.append(request)
                    key = next(line.split(b":", 1)[1].strip() for line in request.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Key:"))
                    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
                    connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")

                    def send(value):
                        _send_frame(connection, 1, json.dumps(value).encode())

                    send({"type": "auth_required", "ha_version": "2026.9.1"})
                    opcode, auth = _recv_frame(connection)
                    if opcode != 1 or json.loads(auth) != {"type": "auth", "access_token": TOKEN}:
                        raise AssertionError("invalid authentication frame")
                    send({"type": "auth_ok", "ha_version": "2026.9.1"})
                    opcode, command = _recv_frame(connection)
                    if opcode != 1 or json.loads(command) != {"type": "config/area_registry/list", "id": 1}:
                        raise AssertionError("invalid registry command")
                    send({"type": "result", "id": 1, "success": True, "result": [{"area_id": "office", "future": True}]})
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], captured
    finally:
        thread.join(4)
        listener.close()
        if thread.is_alive():
            raise AssertionError("TLS fixture failed to stop")
        if failures:
            raise failures[0]


class CapabilityIntegrationTests(unittest.TestCase):
    def test_wss_custom_ca_prefix_and_no_ambient_proxy(self):
        with tls_registry_server() as (port, captured):
            client = HomeAssistant(TOKEN, host=f"https://localhost:{port}/prefix/api", ca_file=str(CERTS / "localhost-cert.pem"))
            with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"}):
                self.assertEqual(client.get_areas(), [{"area_id": "office", "future": True}])
        self.assertTrue(captured[0].startswith(b"GET /prefix/api/websocket HTTP/1.1\r\n"))
        self.assertNotIn(TOKEN.encode(), captured[0])

    def test_wss_rejects_untrusted_certificate_and_wrong_hostname(self):
        for host, ca_file in (("localhost", None), ("127.0.0.1", str(CERTS / "localhost-cert.pem"))):
            with self.subTest(host=host), tls_registry_server() as (port, captured):
                client = HomeAssistant(TOKEN, host=f"https://{host}:{port}", ca_file=ca_file)
                with self.assertRaises(TransportError) as caught:
                    client.get_areas()
                self.assertEqual(caught.exception.category, "tls")
                self.assertEqual(captured, [])

    def test_wss_explicit_verification_disable(self):
        with tls_registry_server() as (port, _):
            client = HomeAssistant(TOKEN, host=f"https://127.0.0.1:{port}", verify_ssl=False)
            self.assertEqual(client.get_areas()[0]["area_id"], "office")

    @staticmethod
    def event_script(sock, server):
        server.send_json(sock, {"type": "auth_required", "ha_version": "2026.9.1"})
        server.recv_json(sock)
        server.send_json(sock, {"type": "auth_ok"})
        subscribe = server.recv_json(sock)
        if subscribe != {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}:
            raise AssertionError("invalid subscription")
        server.send_json(sock, {"type": "result", "id": 1, "success": True, "result": None})
        for index in range(2):
            server.send_json(sock, {"type": "event", "id": 1, "event": {"event_type": "state_changed", "data": {"index": index}, "future": None}})
        unsubscribe = server.recv_json(sock)
        if unsubscribe != {"type": "unsubscribe_events", "id": 2, "subscription": 1}:
            raise AssertionError("invalid unsubscribe")
        server.send_json(sock, {"type": "result", "id": 2, "success": True, "result": None})

    def test_agent_collection_uses_public_stream(self):
        server = WebSocketFixture(self.event_script)
        try:
            tools = AgentTools(HomeAssistant(TOKEN, host=server.host), include_events=True)
            result = tools.dispatch("ha_collect_events", {"event_type": "state_changed", "max_events": 2, "duration": 2})
            self.assertEqual(result["stop_reason"], "max_events")
            self.assertEqual([event["data"]["index"] for event in result["events"]], [0, 1])
        finally:
            server.thread.join(3)
        self.assertFalse(server.thread.is_alive())
        self.assertIsNone(server.error)

    def test_cli_watch_over_real_websocket(self):
        server = WebSocketFixture(self.event_script)
        env = {key: value for key, value in os.environ.items() if not key.startswith("HA_")}
        env["HA_TOKEN"] = TOKEN
        try:
            result = subprocess.run([sys.executable, "-S", "-m", "homepy", "--host", server.host, "watch", "--event-type", "state_changed", "--max-events", "2", "--duration", "2"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual([json.loads(line)["data"]["index"] for line in result.stdout.splitlines()], [0, 1])
        finally:
            server.thread.join(3)
        self.assertFalse(server.thread.is_alive())
        self.assertIsNone(server.error)


if __name__ == "__main__":
    unittest.main()
