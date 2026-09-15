"""Public registry response validation, independent of wire framing."""

import unittest
from unittest.mock import patch

from homepy import HomeAssistant, ResponseError, WebSocketAuthenticationError, WebSocketCommandError
from homepy.exceptions import error_details


class RegistryClientTests(unittest.TestCase):
    def test_commands_and_registry_relationships(self):
        client = HomeAssistant("fixture-token")
        cases = (
            (client.get_areas, "area_registry", "area_id", "room"),
            (client.get_devices, "device_registry", "id", "device"),
            (client.get_entity_registry, "entity_registry", "entity_id", "sensor.unavailable"),
        )
        for method, registry, identity, value in cases:
            entries = [{identity: value, "device_id": None, "future": {"preserved": True}}]
            with self.subTest(registry=registry), patch("homepy.websocket_transport.WebSocketTransport.request", return_value=entries) as request:
                self.assertEqual(method(), entries)
                request.assert_called_once_with(f"config/{registry}/list")
            with patch("homepy.websocket_transport.WebSocketTransport.request", return_value=[]):
                self.assertEqual(method(), [])

    def test_invalid_identity_and_containers_are_safe(self):
        client = HomeAssistant("fixture-token")
        for method, identity in ((client.get_areas, "area_id"), (client.get_devices, "id"), (client.get_entity_registry, "entity_id")):
            for payload in (None, {}, ["private"], [{}], [{identity: ""}], [{identity: None}], [{identity: True}]):
                with self.subTest(method=method.__name__, payload=payload), patch("homepy.websocket_transport.WebSocketTransport.request", return_value=payload):
                    with self.assertRaises(ResponseError) as caught:
                        method()
                    self.assertNotIn("private", str(caught.exception))

    def test_protocol_errors_do_not_invent_http_status(self):
        self.assertEqual(error_details(WebSocketAuthenticationError()), {
            "code": "authentication_error", "message": "Home Assistant authentication failed",
        })
        for code in ("unknown_command", "unauthorized", "invalid_format", "unknown_error"):
            details = error_details(WebSocketCommandError(code))
            self.assertEqual(details["command_code"], code)
            self.assertNotIn("status_code", details)
        for raw in ("private-error", [], {}, None):
            self.assertEqual(error_details(WebSocketCommandError(raw))["command_code"], "unknown_error")


if __name__ == "__main__":
    unittest.main()
