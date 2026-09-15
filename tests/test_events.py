import unittest
import threading
from unittest.mock import patch

from homepy.config import ConnectionConfig
from homepy.events import EventStream
from homepy.exceptions import ResponseError, TransportError

try:
    from ws_fixture import WebSocketFixture
except ModuleNotFoundError:  # direct module invocation from the repository root
    from tests.ws_fixture import WebSocketFixture


class EventStreamTests(unittest.TestCase):
    def config(self, fixture, **kwargs):
        values = {"token": "synthetic-secret", "host": fixture.host, "timeout": 2}
        values.update(kwargs)
        return ConnectionConfig(**values)

    @staticmethod
    def scripted_events(events, *, close=False):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            subscribe = server.recv_json(sock)
            server.send_json(sock, {"id": subscribe["id"], "type": "result", "success": True, "result": None})
            for event in events:
                server.send_json(sock, {"id": subscribe["id"], "type": "event", "event": event}, fragmented=True)
            if close:
                return
            unsubscribe = server.recv_json(sock)
            server.send_json(sock, {"id": unsubscribe["id"], "type": "result", "success": True, "result": None})

        return script

    def test_exact_count_yields_unmodified_event_and_unsubscribes(self):
        event = {"event_type": "state_changed", "data": {"entity_id": "light.kitchen", "x": 1}, "unknown": [1, 2]}
        fixture = WebSocketFixture(self.scripted_events([event, {"ignored": True}]))
        try:
            stream = EventStream(self.config(fixture), "state_changed", max_events=1, duration=None)
            self.assertEqual(list(stream), [event])
            self.assertEqual(stream.stop_reason, "max_events")
            stream.close()
            fixture.thread.join(2)
            self.assertIsNone(fixture.error)
        finally:
            stream.close()

    def test_duration_without_events_is_successful_empty_observation(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            subscribe = server.recv_json(sock)
            server.send_json(sock, {"id": subscribe["id"], "type": "result", "success": True, "result": None})
            sock.settimeout(2)
            try:
                server.recv_json(sock)
            except (TimeoutError, OSError, ConnectionError):
                pass

        fixture = WebSocketFixture(script)
        try:
            stream = EventStream(self.config(fixture), "state_changed", max_events=None, duration=0.05)
            self.assertEqual(list(stream), [])
            self.assertEqual(stream.stop_reason, "duration")
        finally:
            stream.close()
            fixture.thread.join(2)

    def test_invalid_event_envelope_fails_and_closes(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            subscribe = server.recv_json(sock)
            server.send_json(sock, {"id": subscribe["id"], "type": "result", "success": True, "result": None})
            server.send_json(sock, {"id": 99, "type": "event", "event": {}})

        fixture = WebSocketFixture(script)
        try:
            with self.assertRaises(ResponseError):
                next(EventStream(self.config(fixture), "state_changed", max_events=1, duration=None))
        finally:
            fixture.thread.join(2)

    def test_count_only_observation_does_not_inherit_setup_timeout(self):
        event = {"event_type": "state_changed", "data": {"entity_id": "light.kitchen"}}

        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            subscribe = server.recv_json(sock)
            server.send_json(sock, {"id": subscribe["id"], "type": "result", "success": True, "result": None})
            threading.Event().wait(0.6)
            server.send_json(sock, {"id": subscribe["id"], "type": "event", "event": event})
            unsubscribe = server.recv_json(sock)
            server.send_json(sock, {"id": unsubscribe["id"], "type": "result", "success": True, "result": None})

        fixture = WebSocketFixture(script)
        try:
            stream = EventStream(self.config(fixture, timeout=0.5), "state_changed", max_events=1, duration=None)
            self.assertEqual(next(stream), event)
            self.assertEqual(stream.stop_reason, "max_events")
        finally:
            stream.close()
            fixture.thread.join(2)

    def test_cumulative_event_bytes_fail_without_dropping_events(self):
        fixture = WebSocketFixture(self.scripted_events([]))
        stream = EventStream(self.config(fixture), "state_changed", max_events=3, duration=None)
        class FakeSession:
            def __init__(self):
                self.messages = iter([
                    {"type": "event", "id": 1, "event": {"n": 1}},
                    {"type": "event", "id": 1, "event": {"n": 2}},
                ])
            def receive(self, _timeout=None):
                return next(self.messages)
            def send(self, *_args):
                pass
            def close(self, *_args):
                pass
        stream._started = True
        stream._session = FakeSession()
        stream._subscription_id = 1
        with patch("homepy.events.MAX_EVENT_BYTES", 10):
            self.assertEqual(next(stream), {"n": 1})
            with self.assertRaises(ResponseError):
                next(stream)
        stream.close()
        fixture.close()

    def test_invalid_limits_include_huge_numeric_values(self):
        config = ConnectionConfig("secret")
        for kwargs in (
            {"max_events": True, "duration": None},
            {"max_events": 0, "duration": None},
            {"max_events": None, "duration": 0},
            {"max_events": None, "duration": float("inf")},
            {"max_events": None, "duration": 10**1000},
            {"max_events": None, "duration": "1"},
            {"max_events": None, "duration": None},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                EventStream(config, "state_changed", **kwargs)

    def test_unexpected_peer_close_is_a_transport_failure(self):
        def script(sock, server):
            server.send_json(sock, {"type": "auth_required"})
            server.recv_json(sock)
            server.send_json(sock, {"type": "auth_ok"})
            subscribe = server.recv_json(sock)
            server.send_json(sock, {"id": subscribe["id"], "type": "result", "success": True, "result": None})
            server.send_close(sock)

        fixture = WebSocketFixture(script)
        try:
            stream = EventStream(self.config(fixture), "state_changed", max_events=1, duration=None)
            with self.assertRaises(TransportError):
                next(stream)
            self.assertIsNone(stream._session)
        finally:
            fixture.thread.join(2)

    def test_close_before_first_iteration_does_not_connect(self):
        fixture = WebSocketFixture(lambda *_args: self.fail("unexpected network connection"))
        stream = EventStream(self.config(fixture), "state_changed", max_events=1, duration=None)
        stream.close()
        self.assertEqual(stream.stop_reason, "closed")
        fixture.close()
        self.assertIsNone(fixture.error)


if __name__ == "__main__":
    unittest.main()
