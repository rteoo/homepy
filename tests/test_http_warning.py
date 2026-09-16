import unittest
import warnings

from homepy.config import ConnectionConfig


class HttpWarningTests(unittest.TestCase):
    def test_remote_http_warns_without_disclosing_token(self):
        for host in ("homeassistant.local", "http://192.0.2.1", "http://100.64.0.1"):
            with self.subTest(host=host), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                config = ConnectionConfig("synthetic-private-token", host=host)
                self.assertEqual(len(caught), 1)
                self.assertIn("unencrypted", str(caught[0].message))
                self.assertNotIn("synthetic-private-token", str(caught[0].message))
                self.assertTrue(config.base_url.startswith("http://"))

    def test_https_and_literal_loopback_do_not_warn(self):
        for host in ("https://example.invalid", "127.0.0.1", "::1"):
            with self.subTest(host=host), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ConnectionConfig("synthetic-token", host=host)
                self.assertEqual(caught, [])
