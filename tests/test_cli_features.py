from __future__ import annotations

from io import StringIO
import json
import math
import subprocess
import sys
import unittest

from homepy.agent import AgentToolError, AgentTools
from homepy.cli import main
from homepy.exceptions import WebSocketCommandError


class _Stream:
    def __init__(self, events, stop_reason="max_events", error=None):
        self.events = events
        self.stop_reason = stop_reason
        self.error = error
        self.closed = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def __iter__(self):
        yield from self.events
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed += 1


class _InterruptStream(_Stream):
    def __iter__(self):
        raise KeyboardInterrupt


class _ErrorStream(_Stream):
    def __iter__(self):
        yield from self.events
        raise RuntimeError("SECRET private event failure")


class _FlushOutput(StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1
        super().flush()


class _BrokenOutput:
    def write(self, _value):
        raise BrokenPipeError

    def flush(self):
        raise BrokenPipeError


class _Client:
    def __init__(self):
        self.calls = []
        self.stream = _Stream([{"event_type": "state_changed", "data": {"x": 1}}])

    def get_areas(self):
        self.calls.append("get_areas")
        return [{"area_id": "office", "extra": None}]

    def get_devices(self):
        self.calls.append("get_devices")
        return [{"id": "device-1"}]

    def get_entity_registry(self):
        self.calls.append("get_entity_registry")
        return [{"entity_id": "light.desk"}]

    def watch_events(self, event_type, *, max_events, duration):
        self.calls.append(("watch_events", event_type, max_events, duration))
        return self.stream

    def process_conversation(self, text, *, language=None, agent_id=None, conversation_id=None):
        self.calls.append(("conversation", text, language, agent_id, conversation_id))
        return {"response": {"response_type": "query_answer", "data": {"speech": {"plain": {"speech": "ok"}}}}}


class _CommandErrorClient(_Client):
    def get_areas(self):
        self.calls.append("get_areas")
        raise WebSocketCommandError("unauthorized")


class AgentFeatureTests(unittest.TestCase):
    def test_discovery_and_event_tools_are_opt_in(self):
        client = _Client()
        tools = AgentTools(client, include_discovery=True, include_events=True)
        names = {item["function"]["name"] for item in tools.tools}
        self.assertTrue({"ha_get_areas", "ha_get_devices", "ha_get_entity_registry", "ha_collect_events"} <= names)
        self.assertEqual(tools.dispatch("ha_get_areas", {}), [{"area_id": "office", "extra": None}])
        self.assertEqual(tools.dispatch("ha_collect_events", {"event_type": "state_changed"})["stop_reason"], "max_events")

        denied = AgentTools(client)
        with self.assertRaisesRegex(AgentToolError, "disabled"):
            denied.dispatch("ha_get_areas", {})
        with self.assertRaisesRegex(AgentToolError, "disabled"):
            denied.dispatch("ha_collect_events", {"event_type": "state_changed"})

    def test_conversation_policy_and_builtin_agent(self):
        client = _Client()
        allowed = AgentTools(client, allow_actions=True, allow_conversation=True)
        self.assertIn("ha_process_conversation", {item["function"]["name"] for item in allowed.tools})
        allowed.dispatch("ha_process_conversation", {"text": "What time is it?", "language": "en"})
        self.assertEqual(client.calls[-1], ("conversation", "What time is it?", "en", "home_assistant", None))

        for kwargs in (
            {},
            {"allow_actions": True, "allowed_services": []},
            {"allow_actions": True, "allowed_services": ["light.turn_on"]},
        ):
            with self.assertRaises(TypeError):
                AgentTools(client, allow_conversation=True, **kwargs)

        # Dispatch retains a defense in depth check if a caller mutates policy
        # state after construction.
        blocked = AgentTools(client, allow_actions=True, allow_conversation=True)
        blocked.allowed_services = frozenset()
        with self.assertRaises(AgentToolError):
            blocked.dispatch("ha_process_conversation", {"text": "hello"})

    def test_event_limits_reject_invalid_values_without_io(self):
        client = _Client()
        tools = AgentTools(client, include_events=True)
        invalid = (
            {"event_type": "state_changed", "max_events": True},
            {"event_type": "state_changed", "max_events": 0},
            {"event_type": "state_changed", "max_events": 101},
            {"event_type": "state_changed", "duration": math.nan},
            {"event_type": "state_changed", "duration": math.inf},
            {"event_type": "state_changed", "duration": 0},
            {"event_type": "state_changed", "duration": 31},
            {"event_type": "state_changed", "duration": 10**100},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(AgentToolError):
                tools.dispatch("ha_collect_events", arguments)
        self.assertEqual(client.calls, [])

    def test_conversation_rejects_injected_agent_and_empty_text_without_io(self):
        client = _Client()
        tools = AgentTools(client, allow_actions=True, allow_conversation=True)
        for arguments in (
            {"text": "hello", "agent_id": "other"},
            {"text": "   "},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(AgentToolError):
                tools.dispatch("ha_process_conversation", arguments)
        self.assertEqual(client.calls, [])

    def test_command_code_survives_agent_boundary(self):
        with self.assertRaises(AgentToolError) as raised:
            AgentTools(_CommandErrorClient(), include_discovery=True).dispatch("ha_get_areas", {})
        self.assertEqual(raised.exception.to_details()["command_code"], "unauthorized")


class CLIFeatureTests(unittest.TestCase):
    def run_cli(self, argv, client=None):
        client = client or _Client()
        out, err = StringIO(), StringIO()
        status = main(argv, environ={"HA_TOKEN": "secret"}, client_factory=lambda *a, **kw: client, stdout=out, stderr=err)
        return status, out.getvalue(), err.getvalue(), client

    def test_watch_is_ndjson_without_footer(self):
        status, stdout, stderr, client = self.run_cli(["watch", "--event-type", "state_changed", "--max-events", "1", "--duration", "2"])
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual([json.loads(line) for line in stdout.splitlines()], client.stream.events)
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertEqual(client.calls[-1], ("watch_events", "state_changed", 1, 2.0))

    def test_watch_accepts_cli_limits_above_agent_ceiling_and_empty_stream(self):
        client = _Client()
        client.stream = _Stream([] , stop_reason="duration")
        status, stdout, stderr, client = self.run_cli(
            ["watch", "--event-type", "state_changed", "--max-events", "101", "--duration", "31"], client
        )
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(client.calls[-1], ("watch_events", "state_changed", 101, 31.0))

    def test_watch_flushes_each_line_and_sanitizes_partial_failures(self):
        client = _Client()
        output = _FlushOutput()
        error = StringIO()
        status = main(
            ["watch", "--event-type", "state_changed"],
            environ={"HA_TOKEN": "secret"},
            client_factory=lambda *a, **kw: client,
            stdout=output,
            stderr=error,
        )
        self.assertEqual(status, 0)
        self.assertEqual(output.flushes, 1)

        client.stream = _ErrorStream([{"event_type": "state_changed", "data": {"x": 1}}])
        output, error = StringIO(), StringIO()
        status = main(
            ["watch", "--event-type", "state_changed"],
            environ={"HA_TOKEN": "secret"},
            client_factory=lambda *a, **kw: client,
            stdout=output,
            stderr=error,
        )
        self.assertEqual(status, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(error.getvalue())["error"]["code"], "request_failed")
        self.assertNotIn("SECRET", error.getvalue())

    def test_watch_keyboard_interrupt_and_broken_output_cleanup(self):
        client = _Client()
        client.stream = _InterruptStream([])
        output, error = StringIO(), StringIO()
        status = main(
            ["watch", "--event-type", "state_changed"],
            environ={"HA_TOKEN": "secret"},
            client_factory=lambda *a, **kw: client,
            stdout=output,
            stderr=error,
        )
        self.assertEqual(status, 130)
        self.assertEqual(error.getvalue(), "")
        self.assertGreaterEqual(client.stream.closed, 1)

        client.stream = _Stream([{"event_type": "state_changed"}])
        status = main(
            ["watch", "--event-type", "state_changed"],
            environ={"HA_TOKEN": "secret"},
            client_factory=lambda *a, **kw: client,
            stdout=_BrokenOutput(),
            stderr=error,
        )
        self.assertEqual(status, 1)
        self.assertGreaterEqual(client.stream.closed, 1)

    def test_static_definitions_need_no_token_or_client(self):
        def unexpected_factory(*args, **kwargs):
            raise AssertionError("client must not be constructed")

        out, err = StringIO(), StringIO()
        status = main(
            ["tools", "--include-discovery", "--include-events", "--allow-actions", "--allow-conversation"],
            environ={}, client_factory=unexpected_factory, stdout=out, stderr=err,
        )
        names = {item["function"]["name"] for item in json.loads(out.getvalue())}
        self.assertEqual(status, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("ha_process_conversation", names)

    def test_command_code_survives_cli_boundary(self):
        status, stdout, stderr, _ = self.run_cli(["areas"], client=_CommandErrorClient())
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        details = json.loads(stderr)["error"]
        self.assertEqual(details["code"], "websocket_command_error")
        self.assertEqual(details["command_code"], "unauthorized")

    def test_closed_stdout_pipe_has_no_shutdown_traceback(self):
        script = (
            "import sys\n"
            "from homepy.cli import main\n"
            "class S:\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *args): self.close()\n"
            "    def __iter__(self): return iter([{'event_type': 'x'}])\n"
            "    def close(self): pass\n"
            "class C:\n"
            "    def watch_events(self, *args, **kwargs): return S()\n"
            "sys.stdin.buffer.read(1)\n"
            "raise SystemExit(main(['watch', '--event-type', 'x'], environ={'HA_TOKEN': 'secret'}, client_factory=lambda *a, **k: C()))\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdout.close()
        process.stdin.write(b"x")
        process.stdin.close()
        process.wait(timeout=10)
        assert process.stderr is not None
        stderr = process.stderr.read()
        process.stderr.close()
        self.assertNotIn(b"BrokenPipeError", stderr)
        self.assertNotIn(b"Traceback", stderr)

    def test_direct_registry_commands_enable_discovery(self):
        for command, method in (("areas", "get_areas"), ("devices", "get_devices"), ("entity-registry", "get_entity_registry")):
            status, stdout, stderr, client = self.run_cli([command])
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(client.calls, [method])
            self.assertIsInstance(json.loads(stdout), list)

    def test_conversation_requires_both_flags_and_rejects_allowlist_before_factory(self):
        for extra in (
            ["--allow-conversation"],
            ["--allow-actions", "--allow-conversation", "--allowed-service", "light.turn_on"],
        ):
            factory_calls = []

            def factory(*args, **kwargs):
                factory_calls.append(True)
                return _Client()

            out, err = StringIO(), StringIO()
            status = main(
                ["conversation", "--text", "turn on the light", *extra],
                environ={"HA_TOKEN": "secret"}, client_factory=factory, stdout=out, stderr=err,
            )
            self.assertEqual(status, 2)
            self.assertEqual(factory_calls, [])
            self.assertEqual(json.loads(err.getvalue())["error"]["code"], "conversation_policy")

    def test_cli_event_limits_reject_before_client_creation(self):
        factory_calls = []

        def factory(*args, **kwargs):
            factory_calls.append(True)
            return _Client()

        for option, value in (("--max-events", "0"), ("--duration", "nan"), ("--duration", "inf")):
            out, err = StringIO(), StringIO()
            status = main(
                ["watch", "--event-type", "state_changed", option, value],
                environ={"HA_TOKEN": "secret"}, client_factory=factory, stdout=out, stderr=err,
            )
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(err.getvalue())["error"]["code"], "invalid_arguments")
        self.assertEqual(factory_calls, [])


if __name__ == "__main__":
    unittest.main()
