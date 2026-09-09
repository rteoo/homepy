import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from homepy.client import HomeAssistant


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = HomeAssistant("token", "192.0.2.1")
        self.request = patch("homepy.client.Transport.request").start()
        self.addCleanup(patch.stopall)

    def test_basic_endpoints(self) -> None:
        self.request.side_effect = [{"message": "API running."}, {"version": "2026"}]
        self.assertEqual(self.client.health(), {"message": "API running."})
        self.assertEqual(self.client.get_config(), {"version": "2026"})
        self.assertEqual(self.request.call_args_list[0].args, ("GET", ""))
        self.assertEqual(self.request.call_args_list[1].args, ("GET", "config"))

    def test_state_filter_and_state_writes(self) -> None:
        self.request.return_value = [
            {"entity_id": "light.kitchen", "extra": True},
            {"entity_id": "sensor.kitchen"},
        ]
        self.assertEqual(self.client.get_states(domain="light"), [{"entity_id": "light.kitchen", "extra": True}])
        self.request.return_value = {"entity_id": "light.kitchen"}
        self.client.set_state("light.kitchen", "on", {"friendly_name": "Kitchen"}, True)
        self.assertEqual(
            self.request.call_args.kwargs["data"],
            {"state": "on", "attributes": {"friendly_name": "Kitchen"}, "force_update": True},
        )
        self.request.reset_mock()
        self.assertRaises(ValueError, self.client.get_states, domain="")
        self.request.assert_not_called()
        self.assertRaises(ValueError, self.client.set_state, "light.kitchen", "on", force_update="false")

    def test_paths_are_encoded_and_traversal_rejected(self) -> None:
        self.request.return_value = {}
        self.client.get_state("sensor/a")
        self.assertRaises(ValueError, self.client.get_state, "..")
        self.assertEqual(self.request.call_args.args, ("GET", "states/sensor%2Fa"))

    def test_service_target_and_presence_query(self) -> None:
        self.request.return_value = {"changed_states": [], "new_field": 1}
        result = self.client.call_service(
            "light", "turn_on", {"brightness": 100}, target={"entity_id": ["light.a"]}, return_response=True
        )
        self.assertEqual(result["new_field"], 1)
        self.assertEqual(self.request.call_args.kwargs["params"], {"return_response": ""})
        self.assertEqual(
            self.request.call_args.kwargs["data"], {"brightness": 100, "entity_id": ["light.a"]}
        )
        self.assertRaises(
            ValueError,
            self.client.call_service,
            "light",
            "turn_on",
            {"entity_id": "light.a"},
            target={"entity_id": "light.b"},
        )

    def test_history_requires_ids_and_aware_times(self) -> None:
        self.request.return_value = []
        self.client.get_history(
            ["sensor.a", "sensor.b"],
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            minimal_response=True,
        )
        self.assertEqual(self.request.call_args.kwargs["params"]["filter_entity_id"], "sensor.a,sensor.b")
        self.assertIn("minimal_response", self.request.call_args.kwargs["params"])
        self.assertRaises(ValueError, self.client.get_history, [])
        self.assertRaises(ValueError, self.client.get_history, ["sensor.a"], start="2026-01-01T00:00:00")
        self.assertRaises(ValueError, self.client.get_history, ["sensor.a"], minimal_response="false")

    def test_text_bytes_and_calendar_endpoints(self) -> None:
        self.request.side_effect = ["errors", b"jpeg", []]
        self.assertEqual(self.client.get_error_log(), "errors")
        self.assertEqual(self.client.get_camera_image("camera.front"), b"jpeg")
        self.client.get_calendar_events(
            "calendar.home", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00+00:00"
        )
        self.assertEqual(self.request.call_args.kwargs["params"]["start"], "2026-01-01T00:00:00Z")

    def test_remaining_endpoint_contracts(self) -> None:
        self.request.return_value = [{"extra": True}]
        self.assertEqual(self.client.get_components(), [{"extra": True}])
        self.assertEqual(self.request.call_args.args, ("GET", "components"))

        self.request.return_value = [{"event": "custom"}]
        self.assertEqual(self.client.get_events(), [{"event": "custom"}])
        self.assertEqual(self.request.call_args.args, ("GET", "events"))

        self.request.return_value = {"message": "Event custom fired."}
        self.client.fire_event("custom/event", {"value": 1})
        self.assertEqual(self.request.call_args.args, ("POST", "events/custom%2Fevent"))
        self.assertEqual(self.request.call_args.kwargs["data"], {"value": 1})

        self.request.return_value = [{"domain": "custom", "services": ["turn on"]}]
        self.assertEqual(self.client.get_services(), [{"domain": "custom", "services": ["turn on"]}])
        self.assertEqual(self.request.call_args.args, ("GET", "services"))

        self.request.return_value = {"message": "Entity removed."}
        self.client.delete_state("sensor.desk")
        self.assertEqual(self.request.call_args.args, ("DELETE", "states/sensor.desk"))

        self.request.return_value = []
        self.client.get_logbook(
            "2026-01-01T00:00:00Z", "2026-01-02T00:00:00+00:00", entity_id="sensor.desk"
        )
        self.assertEqual(self.request.call_args.args, ("GET", "logbook/2026-01-01T00%3A00%3A00Z"))
        self.assertEqual(
            self.request.call_args.kwargs["params"],
            {"end_time": "2026-01-02T00:00:00+00:00", "entity": "sensor.desk"},
        )

        self.request.return_value = [{"entity_id": "calendar.home"}]
        self.assertEqual(self.client.get_calendars(), [{"entity_id": "calendar.home"}])
        self.assertEqual(self.request.call_args.args, ("GET", "calendars"))

        self.request.return_value = "Rendered"
        self.assertEqual(self.client.render_template("{{ value }}", {"value": 1}), "Rendered")
        self.assertEqual(self.request.call_args.kwargs["response_type"], "text")
        self.assertEqual(self.request.call_args.kwargs["data"], {"template": "{{ value }}", "variables": {"value": 1}})

        self.request.return_value = {"result": "valid", "errors": None}
        self.assertEqual(self.client.check_config(), {"result": "valid", "errors": None})
        self.assertEqual(self.request.call_args.args, ("POST", "config/core/check_config"))
        self.assertIsNone(self.request.call_args.kwargs["data"])

        self.request.return_value = {"speech": {"plain": {"speech": "Timer set"}}}
        self.assertEqual(
            self.client.handle_intent("SetTimer", {"seconds": "30"}),
            {"speech": {"plain": {"speech": "Timer set"}}},
        )
        self.assertEqual(self.request.call_args.args, ("POST", "intent/handle"))
        self.assertEqual(self.request.call_args.kwargs["data"], {"name": "SetTimer", "data": {"seconds": "30"}})


if __name__ == "__main__":
    unittest.main()
