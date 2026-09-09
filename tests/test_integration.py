"""Exercise the public client and CLI over real loopback HTTP, never a home."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import unittest
from unittest.mock import Mock

from homepy import HomeAssistant
from homepy.cli import main
from homepy.exceptions import APIError, AuthenticationError


def fixture_environment(url):
    """Keep process essentials while excluding the caller's HA configuration."""
    return {**{key: value for key, value in os.environ.items() if not key.startswith("HA_")},
            "HA_TOKEN": "test-only-token", "HA_URL": url}


@contextmanager
def home_assistant_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append({
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(raw) if raw else None,
            })
            status = 200
            content_type = "application/json"
            headers = {}
            if self.headers.get("Authorization") != "Bearer test-only-token":
                status, payload = 401, {"message": "credential should never appear"}
            elif self.path == "/ha/api/":
                payload = {"message": "API running."}
            elif self.path == "/ha/api/states/light.desk":
                payload = {"entity_id": "light.desk", "state": "on", "attributes": {}}
            elif self.path == "/ha/api/states":
                payload = [{"entity_id": "light.desk", "state": "on", "attributes": {}}]
            elif self.path == "/ha/api/services/light/turn_on":
                payload = [{"entity_id": "light.desk", "state": "on"}]
            elif self.path == "/ha/api/services/weather/get_forecasts?return_response=":
                payload = {"changed_states": [], "service_response": {"weather.home": {"forecast": []}}}
            elif self.path == "/ha/api/services/switch/turn_on":
                status, payload = 500, {"message": "sensitive internal response"}
            elif self.path == "/ha/api/config":
                status, payload = 302, {}
                headers["Location"] = "/unexpected-redirect"
            elif self.path == "/ha/api/template":
                content_type, payload = "text/plain; charset=utf-8", "Living room: 23°C".encode()
            elif self.path == "/ha/api/camera_proxy/camera.front":
                content_type, payload = "image/jpeg", b"\xff\xd8test-image\xff\xd9"
            else:
                status, payload = 404, {"message": "unknown fixture endpoint"}
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ha", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class HTTPIntegrationTests(unittest.TestCase):
    def test_discover_and_control_device_over_http(self):
        with home_assistant_server() as (url, requests):
            ha = HomeAssistant("test-only-token", host=url)
            self.assertEqual(ha.health(), {"message": "API running."})
            self.assertEqual(ha.get_state("light.desk")["state"], "on")
            result = ha.call_service("light", "turn_on", {"brightness": 120}, target={"entity_id": "light.desk"})
            self.assertEqual(result[0]["entity_id"], "light.desk")
            self.assertEqual(requests[-1]["body"], {"brightness": 120, "entity_id": "light.desk"})
            self.assertEqual(requests[-1]["method"], "POST")
            self.assertTrue(all(item["authorization"] == "Bearer test-only-token" for item in requests))

    def test_service_response_text_and_binary_are_preserved(self):
        with home_assistant_server() as (url, _requests):
            ha = HomeAssistant("test-only-token", host=url)
            result = ha.call_service("weather", "get_forecasts", {"type": "daily"}, return_response=True)
            self.assertEqual(result["service_response"]["weather.home"]["forecast"], [])
            self.assertEqual(ha.render_template("unused fixture"), "Living room: 23°C")
            self.assertEqual(ha.get_camera_image("camera.front"), b"\xff\xd8test-image\xff\xd9")

    def test_authentication_failure_is_typed(self):
        with home_assistant_server() as (url, _requests):
            with self.assertRaises(AuthenticationError) as caught:
                HomeAssistant("incorrect-test-token", host=url).health()
            self.assertEqual(caught.exception.status_code, 401)
            self.assertNotIn("incorrect-test-token", str(caught.exception))
            self.assertNotIn("credential should never appear", str(caught.exception))

    def test_redirect_is_not_followed_and_mutation_not_retried(self):
        with home_assistant_server() as (url, requests):
            ha = HomeAssistant("test-only-token", host=url)
            with self.assertRaises(APIError):
                ha.get_config()
            self.assertEqual(len(requests), 1)
            with self.assertRaises(APIError) as caught:
                ha.call_service("switch", "turn_on", {"entity_id": "switch.fan"})
            self.assertEqual(len(requests), 2)
            self.assertNotIn("sensitive internal response", str(caught.exception))

    def test_cli_runs_with_environment_credentials(self):
        with home_assistant_server() as (url, requests):
            env = fixture_environment(url)
            root = Path(__file__).resolve().parents[1]
            result = subprocess.run([sys.executable, "-m", "homepy", "health"], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"message": "API running."})
            self.assertEqual(len(requests), 1)

    def test_cli_denies_actions_without_sending_http(self):
        with home_assistant_server() as (url, requests):
            env = fixture_environment(url)
            root = Path(__file__).resolve().parents[1]
            result = subprocess.run([sys.executable, "-m", "homepy", "call", "light", "turn_on"],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error", json.loads(result.stderr))
            self.assertNotIn("test-only-token", result.stderr)
            self.assertEqual(requests, [])

    def test_cli_action_allowlist_and_json_tool_dispatch_over_http(self):
        with home_assistant_server() as (url, requests):
            env = fixture_environment(url)
            root = Path(__file__).resolve().parents[1]
            command = [sys.executable, "-m", "homepy", "call", "light", "turn_on",
                       "--target", '{"entity_id":"light.desk"}', "--allow-actions",
                       "--allowed-service"]
            denied = subprocess.run(command + ["light.turn_off"], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(json.loads(denied.stderr)["error"]["code"], "service_denied")
            self.assertEqual(requests, [])
            allowed = subprocess.run(command + ["light.turn_on"], cwd=root, env=env,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(json.loads(allowed.stdout)[0]["state"], "on")
            self.assertEqual(requests[-1]["body"], {"entity_id": "light.desk"})
            read = subprocess.run([sys.executable, "-m", "homepy", "tool", "ha_get_state",
                                   "--arguments", '{"entity_id":"light.desk"}'],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(json.loads(read.stdout)["entity_id"], "light.desk")
            self.assertEqual(len(requests), 2)

    def test_cli_connection_precedence_and_ca_are_forwarded(self):
        env = {"HA_TOKEN": "test-only-token", "HA_URL": "https://ha.example.invalid",
               "HA_HOST": "ignored.example.invalid", "HA_PORT": "443",
               "HA_TIMEOUT": "2.5", "HA_CA_FILE": "test-ca.pem"}
        client = Mock()
        client.health.return_value = {"message": "API running."}
        factory = Mock(return_value=client)
        for argv, expected in [
            (["health"], {"host": "https://ha.example.invalid", "port": 443, "timeout": 2.5}),
            (["--host", "other.example.invalid", "--port", "8124", "--timeout", "5", "health"],
             {"host": "other.example.invalid", "port": 8124, "timeout": 5.0}),
        ]:
            with self.subTest(argv=argv):
                out, err = StringIO(), StringIO()
                self.assertEqual(main(argv, environ=env, client_factory=factory, stdout=out, stderr=err), 0)
                factory.assert_called_with("test-only-token", ca_file="test-ca.pem", **expected)
                self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
