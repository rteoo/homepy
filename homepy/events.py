"""Closeable, bounded Home Assistant event observations."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Iterator

from .config import ConnectionConfig
from .exceptions import ResponseError, TransportError
from .websocket_transport import (
    CLOSE_ALLOWANCE,
    WebSocketClosed,
    WebSocketTimeout,
    WebSocketSession,
    WebSocketTransport,
    _remaining,
)


# ceiling: observations contain at most 16 MiB of event data; use separate windows
# for larger workloads until a reviewed persistence API exists.
MAX_EVENT_BYTES = 16 * 1024 * 1024


def _validate_limits(max_events: int | None, duration: float | None) -> None:
    if max_events is None and duration is None:
        raise ValueError("max_events or duration must be supplied")
    if max_events is not None and (
        isinstance(max_events, bool) or not isinstance(max_events, int) or max_events <= 0
    ):
        raise ValueError("max_events must be a positive integer or None")
    if duration is not None:
        try:
            valid_duration = (
                not isinstance(duration, bool)
                and isinstance(duration, (int, float))
                and math.isfinite(float(duration))
                and float(duration) > 0
            )
        except (OverflowError, ValueError):
            valid_duration = False
        if not valid_duration:
            raise ValueError("duration must be a finite positive number or None")


class EventStream(Iterator[dict[str, Any]]):
    """A single authenticated subscription with finite count/time limits."""

    def __init__(
        self,
        config: ConnectionConfig,
        event_type: str,
        *,
        max_events: int | None = 100,
        duration: float | None = 30,
    ) -> None:
        if not isinstance(config, ConnectionConfig):
            raise TypeError("config must be a ConnectionConfig")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        _validate_limits(max_events, duration)
        self.config = config
        self.event_type = event_type
        self.max_events = max_events
        self.duration = float(duration) if duration is not None else None
        self.stop_reason: str | None = None
        self._transport = WebSocketTransport(config)
        self._session: WebSocketSession | None = None
        self._subscription_id: int | None = None
        self._next_id = 1
        self._observation_deadline: float | None = None
        self._event_count = 0
        self._event_bytes = 0
        self._started = False
        self._terminal_failure = False

    def __enter__(self) -> "EventStream":
        self._ensure_started()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def __iter__(self) -> "EventStream":
        return self

    def _ensure_started(self) -> None:
        if self._started or self.stop_reason == "closed" or self._terminal_failure:
            return
        self._started = True
        deadline = time.monotonic() + self.config.timeout
        try:
            session = self._transport.open(deadline=deadline)
            # Own the session before any setup operation so every failure path
            # releases it, including a malformed or unsuccessful acknowledgement.
            self._session = session
            subscription_id = self._next_id
            self._next_id += 1
            self._subscription_id = subscription_id
            session.send(
                {"id": subscription_id, "type": "subscribe_events", "event_type": self.event_type},
                _remaining(deadline),
            )
            ack = session.receive(_remaining(deadline))
            if type(ack.get("id")) is not int or ack.get("id") != subscription_id or ack.get("type") != "result":
                raise ResponseError("WebSocket subscription response envelope was invalid")
            if type(ack.get("success")) is not bool:
                raise ResponseError("WebSocket subscription response envelope was invalid")
            if ack["success"] is not True:
                error = ack.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                from .websocket_transport import WebSocketCommandError

                if not isinstance(code, str) or code not in {"unknown_command", "unauthorized", "invalid_format"}:
                    code = "unknown_error"
                raise WebSocketCommandError(command_code=code)
            if "result" not in ack:
                raise ResponseError("WebSocket subscription response envelope was invalid")
            self._observation_deadline = (
                time.monotonic() + self.duration if self.duration is not None else None
            )
        except WebSocketTimeout:
            self._abort()
            raise TransportError(category="timeout") from None
        except WebSocketClosed:
            self._abort()
            raise TransportError(category="network") from None
        except BaseException:
            self._abort()
            raise

    def __next__(self) -> dict[str, Any]:
        self._ensure_started()
        if self.stop_reason is not None:
            raise StopIteration
        if self._session is None:
            raise StopIteration
        try:
            remaining = _remaining(self._observation_deadline)
        except WebSocketTimeout:
            self._finish("duration")
            raise StopIteration
        try:
            message = self._session.receive(remaining)
        except WebSocketTimeout:
            self._finish("duration")
            raise StopIteration
        except WebSocketClosed:
            self._abort()
            raise TransportError(category="network") from None
        except (TransportError, ResponseError):
            self._abort()
            raise
        except BaseException:
            self._abort()
            raise
        if (
            message.get("type") != "event"
            or type(message.get("id")) is not int
            or message.get("id") != self._subscription_id
            or not isinstance(message.get("event"), dict)
        ):
            self._abort()
            raise ResponseError("WebSocket event envelope was invalid")
        event = message["event"]
        try:
            encoded = json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self._abort()
            raise ResponseError("WebSocket event payload was not valid JSON") from None
        if self._event_bytes + len(encoded) > MAX_EVENT_BYTES:
            self._abort()
            raise ResponseError("event observation exceeds the maximum permitted size")
        if (
            self._observation_deadline is not None
            and time.monotonic() >= self._observation_deadline
            and (self.max_events is None or self._event_count + 1 < self.max_events)
        ):
            self._finish("duration")
            raise StopIteration
        self._event_bytes += len(encoded)
        self._event_count += 1
        if self.max_events is not None and self._event_count >= self.max_events:
            # Settle the reason before returning the last permitted event so a
            # consumer can inspect it without making a speculative next call.
            self._finish("max_events")
        return event

    def _finish(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
        self._unsubscribe_and_close()

    def _abort(self) -> None:
        self._terminal_failure = True
        self._unsubscribe_and_close()

    def _unsubscribe_and_close(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        cleanup_deadline = time.monotonic() + CLOSE_ALLOWANCE
        try:
            if self._subscription_id is not None:
                command_id = self._next_id
                self._next_id += 1
                try:
                    session.send(
                        {"id": command_id, "type": "unsubscribe_events", "subscription": self._subscription_id},
                        _remaining(cleanup_deadline),
                    )
                    while True:
                        response = session.receive(_remaining(cleanup_deadline))
                        if (
                            type(response.get("id")) is int
                            and response.get("id") == command_id
                            and response.get("type") == "result"
                        ):
                            break
                except Exception:
                    pass
        finally:
            session.close(max(0.0, cleanup_deadline - time.monotonic()))

    def close(self) -> None:
        """Stop the observation; repeated calls have no effect."""
        if self.stop_reason is None:
            self.stop_reason = "closed"
        self._unsubscribe_and_close()


__all__ = ["EventStream", "MAX_EVENT_BYTES"]
