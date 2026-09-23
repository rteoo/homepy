import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import warnings

from homepy import HomeAssistant, InsecureTransportWarning
from homepy.config import ConnectionConfig


class HttpWarningTests(unittest.TestCase):
    def test_remote_http_warns_without_disclosing_token(self):
        for host in ("homeassistant.local", "http://192.0.2.1", "http://100.64.0.1"):
            with self.subTest(host=host), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                config = ConnectionConfig("synthetic-private-token", host=host)
                self.assertEqual(len(caught), 1)
                self.assertIs(caught[0].category, InsecureTransportWarning)
                self.assertIn("unencrypted", str(caught[0].message))
                self.assertNotIn("synthetic-private-token", str(caught[0].message))
                self.assertTrue(config.base_url.startswith("http://"))

    def test_https_and_literal_loopback_do_not_warn(self):
        for host in ("https://example.invalid", "127.0.0.1", "::1"):
            with self.subTest(host=host), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ConnectionConfig("synthetic-token", host=host)
                self.assertEqual(caught, [])

    def test_each_entry_point_warns_once_at_the_callers_line(self):
        environ = {"HA_TOKEN": "synthetic-token", "HA_URL": "http://192.0.2.1"}
        entry_points = {
            "ConnectionConfig": lambda: ConnectionConfig("synthetic-token", host="192.0.2.1"),
            "ConnectionConfig.from_env": lambda: ConnectionConfig.from_env(environ),
            "HomeAssistant": lambda: HomeAssistant("synthetic-token", "192.0.2.1"),
            "HomeAssistant.from_env": HomeAssistant.from_env,
        }
        for name, build in entry_points.items():
            with self.subTest(entry_point=name), patch.dict(os.environ, environ, clear=True), \
                    warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                build()
                self.assertEqual(len(caught), 1)
                self.assertEqual(Path(caught[0].filename).resolve(), Path(__file__).resolve())

    def test_cli_reports_warning_inside_the_single_json_stderr_document(self):
        # A subprocess observes the real sys.stderr, where Python's own warning
        # text would otherwise appear beside the JSON document.
        script = (
            "from homepy.cli import main\n"
            "from homepy.exceptions import APIError\n"
            "class Client:\n"
            "    def health(self): raise APIError('private body', status_code=503)\n"
            "raise SystemExit(main(['--host', '192.0.2.1', 'health'],"
            " environ={'HA_TOKEN': 'synthetic-private-token'}, client_factory=lambda *a, **k: Client()))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        report = json.loads(result.stderr)
        self.assertEqual(report["error"]["code"], "api_error")
        self.assertEqual(report["warning"]["code"], "insecure_transport")
        self.assertNotIn("synthetic-private-token", result.stderr)
        self.assertNotIn("private body", result.stderr)
