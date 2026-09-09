import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import traceback
import unittest
from unittest.mock import patch

from homepy.config import ConnectionConfig
from homepy.exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
    ResponseError,
    TransportError,
    error_details,
)
from homepy.transport import MAX_RESPONSE_BYTES, Transport


class _Handler(BaseHTTPRequestHandler):
    requests = []
    mode = "ok"

    def do_GET(self):
        self.__class__.requests.append(
            (self.path, self.headers.get("Authorization"), self.headers.get("Accept"))
        )
        if self.path.endswith("/auth"):
            self.send_response(401)
            self.end_headers()
            return
        if self.path.endswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        if self.path.endswith("/error"):
            self.send_response(503)
            self.end_headers()
            return
        if self.path.endswith("/bad-json"):
            body = b"not json"
        elif self.path.endswith("/truncated"):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"short")
            self.close_connection = True
            return
        elif self.path.endswith("/slow"):
            threading.Event().wait(0.1)
            self.close_connection = True
            return
        elif self.path.endswith("/large"):
            self.send_response(200)
            self.send_header("Content-Length", str(MAX_RESPONSE_BYTES + 1))
            self.end_headers()
            return
        elif self.path.endswith("/text"):
            body = b"hello"
        else:
            body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append(
            (
                self.path,
                self.headers.get("Authorization"),
                self.headers.get("Accept"),
                self.headers.get("Content-Type"),
                body,
            )
        )
        self.send_response(200)
        result = json.dumps({"received": json.loads(body)}).encode()
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *_args):
        pass


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Handler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"http://127.0.0.1:{cls.server.server_port}/proxy/api/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def config(self, **kwargs):
        values = {"token": "secret", "host": self.host}
        values.update(kwargs)
        return ConnectionConfig(**values)

    def test_config_normalizes_prefix_and_hides_token(self):
        config = self.config()
        self.assertEqual(config.base_url, self.host.rstrip("/").removesuffix("/api"))
        self.assertNotIn("secret", repr(config))
        for token in ("bad\r\ntoken", "bad token", "bad\x00token", "bad\u2603"):
            with self.subTest(token=repr(token)), self.assertRaises(ConfigurationError):
                ConnectionConfig(token)

    def test_explicit_https_default_and_ipv6(self):
        self.assertEqual(ConnectionConfig("x", "https://example.test").base_url, "https://example.test:443")
        self.assertEqual(ConnectionConfig("x", "http://[::1]").base_url, "http://[::1]:80")
        self.assertEqual(ConnectionConfig("x", "::1").base_url, "http://[::1]:8123")

    def test_env_prefers_url(self):
        with patch.dict("os.environ", {"HA_TOKEN": "x", "HA_URL": "https://example.test/base/api", "HA_HOST": "ignored"}, clear=True):
            config = ConnectionConfig.from_env()
            self.assertEqual(config.base_url, "https://example.test:443/base")

    def test_wire_json_text_bytes_and_query(self):
        transport = Transport(self.config())
        self.assertEqual(transport.request("GET", "states", params={"a": "b"}), {"ok": True})
        self.assertEqual(_Handler.requests[-1][2], "application/json")
        self.assertEqual(transport.request("GET", "text", response_type="text"), "hello")
        self.assertEqual(_Handler.requests[-1][2], "text/plain")
        self.assertEqual(transport.request("GET", "text", response_type="bytes"), b"hello")
        self.assertEqual(_Handler.requests[-1][2], "*/*")
        self.assertEqual(_Handler.requests[-3][0], "/proxy/api/states?a=b")

    def test_json_body_and_bearer(self):
        result = Transport(self.config()).request("POST", "states", data={"x": 1, "café": "😀"})
        self.assertEqual(result, {"received": {"x": 1, "café": "😀"}})
        self.assertEqual(_Handler.requests[-1][1], "Bearer secret")
        self.assertEqual(_Handler.requests[-1][3], "application/json; charset=utf-8")
        self.assertIn("café".encode("utf-8"), _Handler.requests[-1][4])

    def test_safe_path_validation(self):
        transport = Transport(self.config())
        for path in (
            "/states", "../states", "states/../x", "states/%2e%2e/x", "states/%5cx",
            "http://evil", "states?x=1", "states%3fx=1", "states#x",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                transport.request("GET", path)

    def test_error_types_and_bounded_malformed_responses(self):
        transport = Transport(self.config())
        with self.assertRaises(AuthenticationError):
            transport.request("GET", "auth")
        with self.assertRaises(NotFoundError):
            transport.request("GET", "missing")
        with self.assertRaises(APIError) as caught:
            transport.request("GET", "error")
        self.assertEqual(caught.exception.status_code, 503)
        with self.assertRaises(ResponseError):
            transport.request("GET", "bad-json")
        with self.assertRaises(ResponseError):
            transport.request("GET", "large", response_type="bytes")

    def test_nonfinite_json_and_tls_setup_are_safe(self):
        with self.assertRaises(ValueError):
            Transport(self.config()).request("POST", "states", data={"value": float("nan")})
        config = ConnectionConfig("secret", "https://example.test", ca_file="Z:\\missing\\ca.pem")
        with self.assertRaises(TransportError) as caught:
            Transport(config).request("GET", "states")
        self.assertNotIn("missing", str(caught.exception))
        self.assertEqual(caught.exception.category, "tls")
        self.assertEqual(error_details(caught.exception)["category"], "tls")

    def test_transport_categories_are_stable_and_sanitized(self):
        failures = (
            (socket.gaierror("secret-dns"), "dns"),
            (ConnectionRefusedError("secret-refused"), "refused"),
            (TimeoutError("secret-timeout"), "timeout"),
            (socket.timeout("secret-timeout"), "timeout"),
            (OSError("secret-network"), "network"),
        )
        transport = Transport(self.config())
        messages = {}
        for failure, category in failures:
            with self.subTest(category=category), patch.object(Transport, "_connection", side_effect=failure):
                with self.assertRaises(TransportError) as caught:
                    transport.request("GET", "states")
                self.assertEqual(caught.exception.category, category)
                messages[category] = str(caught.exception)
                details = error_details(caught.exception)
                self.assertEqual(details["code"], "transport_error")
                self.assertEqual(details["category"], category)
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("secret", "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(len(set(messages.values())), len(messages))

    def test_tls_contexts_use_public_ssl_configuration(self):
        verified = Transport(ConnectionConfig("x", "https://example.test"))._connection()
        self.assertTrue(verified._context.check_hostname)
        self.assertEqual(verified._context.verify_mode, 2)  # ssl.CERT_REQUIRED
        verified.close()
        unverified = Transport(ConnectionConfig("x", "https://example.test", verify_ssl=False))._connection()
        self.assertFalse(unverified._context.check_hostname)
        self.assertEqual(unverified._context.verify_mode, 0)  # ssl.CERT_NONE
        unverified.close()

    def test_error_details_preserves_public_codes_and_status(self):
        expected = {
            AuthenticationError(status_code=403): "authentication_error",
            NotFoundError(): "not_found",
            APIError("ignored", status_code=500): "api_error",
            ResponseError("ignored"): "response_error",
            ConfigurationError("ignored"): "configuration_error",
            TransportError(category="dns"): "transport_error",
            Exception("secret"): "request_failed",
        }
        for error, code in expected.items():
            with self.subTest(code=code):
                details = error_details(error)
                self.assertEqual(details["code"], code)
                self.assertNotIn("secret", str(details))
        self.assertEqual(error_details(AuthenticationError(status_code=403))["status_code"], 403)
        self.assertEqual(error_details(APIError("ignored", status_code=500))["status_code"], 500)

    def test_network_failure_is_safe(self):
        config = ConnectionConfig("top-secret", "http://127.0.0.1", port=1, timeout=0.2)
        with self.assertRaises(TransportError) as caught:
            Transport(config).request("GET", "states")
        self.assertNotIn("top-secret", str(caught.exception))
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_truncated_camera_bytes_are_rejected(self):
        with self.assertRaisesRegex(ResponseError, "truncated"):
            Transport(self.config()).request("GET", "truncated", response_type="bytes")

    def test_socket_read_times_out(self):
        with self.assertRaises(TransportError):
            Transport(self.config(timeout=0.02)).request("GET", "slow")


if __name__ == "__main__":
    unittest.main()
