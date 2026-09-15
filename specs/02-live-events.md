# Observe bounded Home Assistant event streams

## Problem Statement

Homepy's event-listing operation describes registered listeners; it does not
subscribe to live events. Agents must currently poll for state changes, and a
long-running unbounded subscription would fit poorly inside a finite tool call.

## Solution

Provide a synchronous, closeable event stream for Python and a bounded event
collection tool for agents. CLI users can consume newline-delimited events.
Every observation has explicit limits and exposes failures without claiming
that an interrupted stream was complete.

## User Stories

1. As a Python user, I want to subscribe to an event type, so that I can observe changes without polling.
2. As an agent developer, I want to observe state changes, so that an agent can check the outcome of a separate action.
3. As a user, I want to distinguish observation from action execution, so that watching cannot control a device.
4. As an agent operator, I want bounded duration and event count, so that a tool call finishes predictably.
5. As a CLI user, I want each event flushed as a JSON line, so that downstream scripts can react immediately.
6. As a Python user, I want to stop and close a stream early, so that I can release resources after finding an event.
7. As an operator, I want subscription acknowledgement before events are accepted, so that setup errors are not mistaken for an empty stream.
8. As an operator, I want disconnects reported explicitly, so that missing observations are visible.
9. As an operator, I want cancellation to stop network activity, so that interrupted agents do not leave subscriptions behind.
10. As a developer, I want complete event fields preserved, so that integration-specific event data remains useful.
11. As an operator, I want finite buffering and byte limits, so that event bursts cannot consume unbounded memory.
12. As a REST-only user, I want live observation without installing a dependency, so that existing commands remain available unchanged.
13. As a maintainer, I want deterministic simulator tests, so that subscriptions can be verified without household activity.

## Implementation Decisions

- Build on the registry specification's internal standard-library WebSocket
  transport, endpoint derivation, authentication, safe error classification,
  message bounds, TLS settings, and logging restrictions.
- Add `watch_events` to the public HomeAssistant client. It accepts one required
  nonempty `event_type`, plus keyword-only `max_events` and `duration`, defaulting
  to 100 events and 30 seconds. Each limit may be omitted with None, but at least
  one must be supplied. Reject booleans, nonpositive or nonfinite numbers, and
  nonintegral counts. There is no implicit all-events subscription.
- Return a closeable EventStream context manager and iterator. Construction is
  local; entering the context or first iteration establishes the connection.
  Each yielded value is the unmodified event object from a matching subscription
  envelope. A stream owns its own connection and is not shared across threads.
- Authenticate, send `subscribe_events` for the chosen type, and require a
  successful acknowledgement before yielding. Route event envelopes by the
  acknowledged subscription ID. Reject malformed envelopes and unexpected IDs
  rather than presenting them as observed events.
- The configured connection timeout bounds setup, using the registry deadline
  model. Observation duration starts after acknowledgement and uses a monotonic
  clock. Receive timeouts consume the remaining observation budget; they do not
  reset it. A duration with no events is a successful empty observation.
- On count or duration completion, expose `stop_reason` as `max_events` or
  `duration`. If both limits are reached while returning the final permitted
  event, count takes precedence. `close` is idempotent and records `closed` for
  early consumer termination. Local close before setup performs no network I/O.
- While connected, attempt `unsubscribe_events` with a fresh command ID and the
  subscription ID, within the one-second shutdown budget. Always close the
  connection, even if acknowledgement is missing. Discard in-flight events during
  shutdown; never turn cancellation into another request or replay.
- Unexpected peer closure, including a normal WebSocket close before an explicit
  local limit, is a TransportError. Authentication/protocol failures retain the
  shared mappings. Iteration failures close the stream and propagate; they must
  not become successful short collections.
- Do not automatically reconnect in this increment. Without an event cursor,
  reconnecting cannot prove uninterrupted delivery. Applications may explicitly
  open another stream and refresh state, but no gap-free or exactly-once guarantee
  is offered. Upstream reconnect patterns are references for a later feature.
- Keep transport buffering finite and add a 16 MiB cumulative UTF-8 event-payload
  limit per observation, independent of the per-message limit. On overflow, fail
  with a safe ResponseError; never silently drop events. The byte count is over
  decoded event payloads serialized as compact UTF-8 JSON, so the rule is
  reproducible. A stream is an observation window, not a durable event archive.
- Add CLI `watch` with required `--event-type`, `--max-events`, and `--duration`
  options using the same defaults. Emit one event JSON object per stdout line,
  flushing each one, with no ordinary JSON wrapper or success footer. Errors use
  JSON stderr and a nonzero status. Ctrl-C closes the stream and exits 130.
  Document that previously emitted lines can remain valid when the exit fails.
- Add `include_events=False` to AgentTools and matching CLI tool-registration
  option. When enabled, expose `ha_collect_events` with a required event type,
  count from 1 to 100, and duration greater than zero and at most 30 seconds.
  Defaults are 100 and 30; both agent limits are always active. Return an object
  containing `events` and `stop_reason`. Do not return a live iterator to an LLM.
- The collector uses the public stream rather than a second protocol path. An
  interrupted or overflowing collection returns an error through the shared
  boundary, not partial success. Disabling the tool rejects dispatch before I/O.
- Preserve the existing `get_events` behavior and its documentation as listener
  discovery. Event payloads are server data and may contain sensitive household
  information; they are returned to the authorized caller but never diagnostic
  logs. Observation does not imply Assist exposure filtering.

## Testing Decisions

- Use the public EventStream, CLI subprocess, and agent dispatch with the same
  loopback protocol simulator introduced for registry discovery. Good tests
  assert externally visible events, limits, failures, and released connections.
- Cover acknowledgement, matching IDs, unknown fields, zero events, exact count
  limit, duration completion, early close, repeated close, invalid arguments,
  connection failure, malformed data, and close before first iteration.
- Exercise server pings and fragmented messages while observing. Verify
  continuous traffic does not extend the duration and queue/byte limits remain
  finite. Use short controlled intervals and generous scheduling tolerance;
  avoid assertions on exact elapsed milliseconds or private timer calls.
- Prove an unexpected closure fails without reconnecting. Confirm no second
  connection, no resubscription, and no mutation command are sent automatically.
- Check bounded cleanup with missing unsubscribe acknowledgement and a broken
  socket. Verify Ctrl-C behavior on supported hosts separately from unit mocks.
- Check NDJSON parsing and flushing, error-only stderr, failure status after
  partial output, and agent collection output with both ordinary stop reasons.
- Retain redaction tests for tokens, response details, traceback chains, and
  application DEBUG logging. Validate denied agent calls send no traffic.
- Acceptance requires the dependency-free regression suite, installed
  CLI tests, and package builds passing. Simulator success is not evidence of
  live household event delivery or Tailscale reachability.

## Out of Scope

Automatic reconnect or replay, event persistence, shared subscriptions, a cached
state mirror, filtering entity IDs server-side, all-events subscriptions,
unbounded agent tools, durable background workers, automatic device actions,
exactly-once delivery, and gap-free observation claims.

## Further Notes

The wire contract is described by the official
[WebSocket event API](https://developers.home-assistant.io/docs/api/websocket/).
The official [JavaScript client](https://github.com/home-assistant/home-assistant-js-websocket)
is useful prior art for reconnecting and subscription lifecycle, but it is not
a Python dependency and its reconnect behavior is deliberately deferred here.
