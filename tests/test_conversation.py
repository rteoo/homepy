"""Conversation contracts over loopback HTTP, without a household instance."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import unittest

from homepy import HomeAssistant, ResponseError, TransportError
from test_integration import home_assistant_server


class ConversationTests(unittest.TestCase):
    def test_wire_payload_and_continuation_preserve_data(self):
        response = {
            "response": {"response_type": "action_done", "data": {"success": [], "failed": [{"id": "light.desk"}]}, "future": 1},
            "conversation_id": "conversation-test", "continue_conversation": True, "extra": [1],
        }
        with home_assistant_server(responses={"/ha/api/conversation/process": response}) as (url, requests):
            client = HomeAssistant("test-only-token", host=url)
            self.assertEqual(client.process_conversation(" Ligue a luz ☀ "), response)
            self.assertEqual(requests[-1]["body"], {"text": " Ligue a luz ☀ "})
            result = client.process_conversation("Again", language="en", agent_id="selected-agent", conversation_id="conversation-test")
            self.assertEqual(result, response)
            self.assertEqual(requests[-1]["body"], {
                "text": "Again", "language": "en", "agent_id": "selected-agent", "conversation_id": "conversation-test",
            })
            self.assertEqual([request["method"] for request in requests], ["POST", "POST"])

    def test_domain_errors_and_future_types_remain_data(self):
        for response_type in ("error", "query_answer", "future_response"):
            result = {"response": {"response_type": response_type, "data": {"code": "no_intent_match"}}, "conversation_id": None}
            with self.subTest(response_type=response_type), home_assistant_server(responses={"/ha/api/conversation/process": result}) as (url, _):
                self.assertEqual(HomeAssistant("test-only-token", host=url).process_conversation("A request"), result)

    def test_invalid_inputs_do_not_send(self):
        with home_assistant_server() as (url, requests):
            client = HomeAssistant("test-only-token", host=url)
            for value in (None, "", "  \n", 5, True):
                with self.subTest(text=value), self.assertRaises(ValueError):
                    client.process_conversation(value)
            for name in ("language", "agent_id", "conversation_id"):
                for value in ("", 5, True, []):
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        client.process_conversation("A request", **{name: value})
            self.assertEqual(requests, [])

    def test_invalid_envelopes_raise_sanitized_response_errors(self):
        valid = {"response": {"response_type": "query_answer"}}
        responses = [[], {}, {"response": []}, {"response": {}}, {"response": {"response_type": 1}},
                     {"response": {"response_type": "error", "data": []}},
                     {**valid, "conversation_id": False}, {**valid, "continue_conversation": "true"}]
        for response in responses:
            with self.subTest(response=response), home_assistant_server(responses={"/ha/api/conversation/process": response}) as (url, _):
                with self.assertRaises(ResponseError) as caught:
                    HomeAssistant("test-only-token", host=url).process_conversation("private sentence")
                self.assertNotIn("private sentence", str(caught.exception))
                self.assertNotIn("test-only-token", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_timeout_after_acceptance_is_not_replayed(self):
        received = []
        release = Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                release.wait(1)
                self.close_connection = True

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            client = HomeAssistant("fixture-token", host=f"http://127.0.0.1:{server.server_port}", timeout=0.1)
            with self.assertRaises(TransportError) as caught:
                client.process_conversation("private sentence")
            self.assertEqual(caught.exception.category, "timeout")
            self.assertEqual(received, [{"text": "private sentence"}])
            self.assertNotIn("private sentence", str(caught.exception))
            self.assertNotIn("fixture-token", str(caught.exception))
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
