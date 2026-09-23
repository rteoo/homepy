from __future__ import annotations

from io import StringIO
from io import BytesIO, TextIOWrapper
import json
import traceback
import unittest

from homepy.agent import AgentToolError, AgentTools
from homepy.config import ConnectionConfig
from homepy.cli import main
from homepy.exceptions import APIError, TransportError


# HTTPS keeps the insecure-transport warning out of stderr in fixtures that
# are not about transport security.
HTTPS_URL = "https://ha.example.invalid"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail = False

    def _result(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.fail:
            raise RuntimeError("token=SECRET should never escape")
        return {"method": name, "args": list(args), "kwargs": kwargs}

    def health(self):
        return {"message": "API running."}

    def get_states(self, *, domain=None):
        return self._result("get_states", domain=domain)

    def get_state(self, entity_id):
        return self._result("get_state", entity_id)

    def get_services(self):
        return self._result("get_services")

    def call_service(self, domain, service, service_data=None, *, target=None, return_response=False):
        return self._result(
            "call_service",
            domain,
            service,
            service_data,
            target=target,
            return_response=return_response,
        )


class AgentToolsTests(unittest.TestCase):
    def test_read_descriptors_and_dispatch(self):
        client = FakeClient()
        tools = AgentTools(client)
        self.assertEqual(
            [item["function"]["name"] for item in tools.tool_definitions()],
            ["ha_get_states", "ha_get_state", "ha_get_services"],
        )
        result = tools.dispatch("ha_get_states", {"domain": "light"})
        self.assertEqual(result["method"], "get_states")
        self.assertEqual(client.calls[-1][2], {"domain": "light"})

    def test_action_descriptor_is_hidden_and_dispatch_denied_by_default(self):
        tools = AgentTools(FakeClient())
        self.assertNotIn("ha_call_service", {item["function"]["name"] for item in tools.tools})
        with self.assertRaisesRegex(AgentToolError, "disabled"):
            tools.dispatch("ha_call_service", {"domain": "light", "service": "turn_on"})

    def test_action_policy_preserves_none_and_empty(self):
        unrestricted = AgentTools(FakeClient(), allow_actions=True)
        unrestricted.dispatch("ha_call_service", {"domain": "light", "service": "turn_on"})

        deny_all = AgentTools(FakeClient(), allow_actions=True, allowed_services=[])
        with self.assertRaisesRegex(AgentToolError, "not allowed"):
            deny_all.dispatch("ha_call_service", {"domain": "light", "service": "turn_on"})

        limited_client = FakeClient()
        limited = AgentTools(limited_client, allow_actions=True, allowed_services=["light.turn_on"])
        limited.dispatch("ha_call_service", {"domain": "light", "service": "turn_on"})
        with self.assertRaisesRegex(AgentToolError, "not allowed"):
            limited.dispatch("ha_call_service", {"domain": "light", "service": "turn_off"})

    def test_allowlist_entries_that_can_never_match_are_rejected(self):
        for entry in ("light", "light.", ".turn_on", "light.turn.on"):
            with self.subTest(entry=entry), self.assertRaisesRegex(TypeError, "DOMAIN.SERVICE"):
                AgentTools(FakeClient(), allow_actions=True, allowed_services=[entry])
        client, out, err = FakeClient(), StringIO(), StringIO()
        status = main(
            ["call", "light", "turn_on", "--allow-actions", "--allowed-service", "light"],
            environ={"HA_TOKEN": "SECRET", "HA_URL": HTTPS_URL},
            client_factory=lambda *a, **kw: client,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(err.getvalue())["error"]["code"], "invalid_arguments")
        self.assertEqual(client.calls, [])

    def test_bad_arguments_are_rejected_without_dynamic_dispatch(self):
        tools = AgentTools(FakeClient())
        with self.assertRaisesRegex(AgentToolError, "Unknown"):
            tools.dispatch("ha_get_state", {"entity_id": "light.desk", "__class__": "x"})
        with self.assertRaisesRegex(AgentToolError, "boolean"):
            AgentTools(FakeClient(), allow_actions=True).dispatch(
                "ha_call_service",
                {"domain": "light", "service": "turn_on", "return_response": 1},
            )
        with self.assertRaisesRegex(AgentToolError, "JSON object"):
            tools.dispatch("ha_get_services", [])

    def test_client_errors_are_sanitized(self):
        client = FakeClient()
        client.fail = True
        with self.assertRaises(AgentToolError) as raised:
            AgentTools(client).dispatch("ha_get_services", {})
        self.assertEqual(raised.exception.code, "request_failed")
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertNotIn("SECRET", "".join(traceback.format_exception(raised.exception)))

    def test_shared_error_details_preserve_category_and_status(self):
        class TypedClient(FakeClient):
            def get_services(self):
                raise TransportError("secret", category="refused")

        with self.assertRaises(AgentToolError) as raised:
            AgentTools(TypedClient()).dispatch("ha_get_services", {})
        self.assertEqual(raised.exception.to_details()["category"], "refused")
        self.assertIn("hint", raised.exception.to_details())

        class APIClient(FakeClient):
            def health(self):
                raise APIError("private response body", status_code=503)

        out, err = StringIO(), StringIO()
        status = main(
            ["health"],
            environ={"HA_TOKEN": "SECRET", "HA_URL": HTTPS_URL},
            client_factory=lambda *a, **kw: APIClient(),
            stdout=out,
            stderr=err,
        )
        self.assertEqual(status, 2)
        details = json.loads(err.getvalue())["error"]
        self.assertEqual(details["code"], "api_error")
        self.assertEqual(details["status_code"], 503)
        self.assertNotIn("private response body", err.getvalue())


class CLITests(unittest.TestCase):
    def run_cli(self, argv, client=None, token="SECRET"):
        client = client or FakeClient()
        out, err = StringIO(), StringIO()
        status = main(argv, environ={"HA_TOKEN": token, "HA_URL": HTTPS_URL}, client_factory=lambda *a, **kw: client, stdout=out, stderr=err)
        return status, out.getvalue(), err.getvalue(), client

    def test_states_outputs_json_and_uses_connection_options(self):
        client = FakeClient()
        status, stdout, stderr, _ = self.run_cli(
            ["--host", "ha.local", "--port", "8124", "--timeout", "3", "states", "--domain", "sensor"],
            client,
        )
        self.assertEqual(status, 0)
        # --host overrides the HTTPS fixture URL with plain HTTP to a remote host.
        self.assertEqual(json.loads(stderr), {"warning": {
            "code": "insecure_transport",
            "message": "Plain HTTP sends the bearer token unencrypted; use HTTPS or a verified encrypted tunnel",
        }})
        self.assertEqual(json.loads(stdout)["method"], "get_states")

    def test_environment_overrides_are_shared_and_invalid_values_are_ignored(self):
        source = {
            "HA_TOKEN": "SECRET",
            "HA_URL": "https://example.test/base/api",
            "HA_HOST": "ignored.test",
            "HA_PORT": "not-a-port",
            "HA_TIMEOUT": "not-a-timeout",
            "HA_CA_FILE": "custom-ca.pem",
        }
        config = ConnectionConfig.from_env(source, port=8124, timeout=2.5)
        self.assertEqual(config.base_url, "https://example.test:8124/base")
        self.assertEqual(config.timeout, 2.5)
        self.assertEqual(config.ca_file, "custom-ca.pem")
        self.assertEqual(source["HA_PORT"], "not-a-port")

        captured: dict[str, object] = {}

        def factory(token, **kwargs):
            captured.update(kwargs)
            return FakeClient()

        out, err = StringIO(), StringIO()
        status = main(
            ["--port", "8125", "--timeout", "3", "services"],
            environ=source,
            client_factory=factory,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(status, 0)
        self.assertEqual(captured["host"], "https://example.test/base/api")
        self.assertEqual(captured["port"], 8125)
        self.assertEqual(captured["timeout"], 3.0)
        self.assertEqual(captured["ca_file"], "custom-ca.pem")

    def test_internal_type_error_is_not_mislabeled_as_invalid_arguments(self):
        def broken_factory(*args, **kwargs):
            raise TypeError("internal sentinel")

        out, err = StringIO(), StringIO()
        status = main(
            ["services"],
            environ={"HA_TOKEN": "SECRET", "HA_URL": HTTPS_URL},
            client_factory=broken_factory,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(err.getvalue())["error"]["code"], "internal_error")
        self.assertNotIn("internal sentinel", err.getvalue())

    def test_call_requires_explicit_actions_and_supports_exact_allowlist(self):
        status, stdout, stderr, _ = self.run_cli(["call", "light", "turn_on"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"]["code"], "actions_disabled")

        client = FakeClient()
        status, stdout, stderr, client = self.run_cli(
            [
                "call",
                "light",
                "turn_on",
                "--allow-actions",
                "--allowed-service",
                "light.turn_on",
                "--data",
                '{"brightness": 10}',
                "--target",
                '{"entity_id": "light.desk"}',
                "--return-response",
            ],
            client,
        )
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(client.calls[-1][0], "call_service")
        self.assertEqual(client.calls[-1][1], ("light", "turn_on", {"brightness": 10}))
        self.assertEqual(client.calls[-1][2]["target"], {"entity_id": "light.desk"})

    def test_invalid_json_and_missing_token_are_json_errors(self):
        status, stdout, stderr, _ = self.run_cli(["call", "light", "turn_on", "--data", "nope"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"]["code"], "invalid_json")
        self.assertNotIn("SECRET", stderr)

        out, err = StringIO(), StringIO()
        status = main(["services"], environ={}, client_factory=lambda *a, **kw: FakeClient(), stdout=out, stderr=err)
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(err.getvalue())["error"]["code"], "missing_token")

    def test_tools_listing_is_static_and_does_not_require_token(self):
        out, err = StringIO(), StringIO()
        status = main(["tools", "--allow-actions"], environ={}, stdout=out, stderr=err)
        self.assertEqual(status, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("ha_call_service", {item["function"]["name"] for item in json.loads(out.getvalue())})

    def test_json_output_escapes_unicode_for_ascii_streams(self):
        class UnicodeClient(FakeClient):
            def get_services(self):
                return {"label": "😀"}

        raw_out, raw_err = BytesIO(), BytesIO()
        out = TextIOWrapper(raw_out, encoding="ascii")
        err = TextIOWrapper(raw_err, encoding="ascii")
        status = main(
            ["services"],
            environ={"HA_TOKEN": "SECRET", "HA_URL": HTTPS_URL},
            client_factory=lambda *a, **kw: UnicodeClient(),
            stdout=out,
            stderr=err,
        )
        out.flush()
        err.flush()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(raw_out.getvalue().decode("ascii")), {"label": "😀"})
        self.assertEqual(raw_err.getvalue(), b"")

    def test_tool_command_dispatches_json_and_hides_unknown_failures(self):
        status, stdout, stderr, client = self.run_cli(
            ["tool", "ha_get_state", "--arguments", '{"entity_id":"sensor.temp"}']
        )
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(client.calls[-1][1], ("sensor.temp",))

        client.fail = True
        status, stdout, stderr, _ = self.run_cli(
            ["tool", "ha_get_services", "--arguments", "{}"], client=client
        )
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stderr)["error"]["code"], "request_failed")
        self.assertNotIn("SECRET", stderr)


if __name__ == "__main__":
    unittest.main()
