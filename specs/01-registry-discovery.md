# Discover areas, devices, and registered entities

## Problem Statement

Homepy can read current entity states and call services, but an agent cannot
reliably discover the IDs that connect entities to devices and rooms. Asking it
to operate on a room therefore requires manually supplied identifiers or guesses.
Runtime state data alone does not describe the installation's registry structure.

## Solution

Add three explicit, read-only registry operations through Home Assistant's
WebSocket API. Python users, CLI users, and agents receive registry records with
the original fields intact. Existing REST users keep their current installation
and behavior. The new standard-library transport connects only when requested.

## User Stories

1. As an agent developer, I want to list areas, so that my agent can discover room IDs without guessing.
2. As an agent developer, I want to list devices, so that my agent can relate hardware to rooms.
3. As an agent developer, I want to list registered entities, so that my agent can relate controls to devices.
4. As a user, I want registry entries without current states retained, so that disabled or unavailable entities do not disappear from discovery.
5. As a user, I want unknown fields preserved, so that integration-specific information remains available.
6. As a local user, I want the existing hostname and port defaults, so that discovery connects like the REST client.
7. As a Tailscale user, I want the same reachable hostname or IP, so that I do not configure a second network route.
8. As a reverse-proxy user, I want path prefixes and TLS settings preserved, so that discovery works through my deployment.
9. As a REST-only user, I want no additional runtime dependency, so that my current installation stays small.
10. As a CLI user, I want structured JSON results and safe errors, so that scripts can consume discovery consistently.
11. As an agent operator, I want explicit discovery-tool registration, so that I control the tools exposed to a model.
12. As an operator, I want clear authentication and unsupported-command failures, so that I can diagnose access and compatibility issues.
13. As an operator, I want bounded requests and closed connections, so that a broken server does not strand my agent.
14. As a maintainer, I want tests against actual protocol exchanges, so that mocks do not hide an invalid wire contract.

## Implementation Decisions

- Extend the public HomeAssistant client with `get_areas`, `get_devices`, and
  `get_entity_registry`. Each takes no endpoint-specific arguments and returns a
  list of JSON objects. Do not add registry writes or a generic command escape
  hatch to the public agent interface.
- Use the command types `config/area_registry/list`,
  `config/device_registry/list`, and `config/entity_registry/list`. Before coding,
  verify all three against the released Home Assistant version selected for
  fixtures. Never substitute `list_for_display`: it has a different contract.
- Validate each result as a list of objects and require the relevant identity
  field (`area_id`, `id`, or `entity_id`) to be a nonempty string. Preserve null
  relationship fields and unknown fields. Do not require a state for every entry.
- Return raw relationships without merging records into an invented atomic
  inventory. Entity-level area assignment can differ from device-level area
  assignment; documentation must explain that callers resolve these deliberately.
- Introduce one internal standard-library WebSocket transport owning URL derivation,
  authentication, bounded message handling, and protocol error conversion.
  Registry operations use one connection per call, one command, and deterministic
  cleanup. No shared background connection or concurrent request router is needed.
- Derive the endpoint from existing normalized configuration: HTTP becomes WS,
  HTTPS becomes WSS, the effective port and prefix remain, and the endpoint is
  `/api/websocket` beneath that prefix. Do not add separate WebSocket environment
  variables or change the existing accepted host syntax.
- Follow the server's `auth_required`, client authentication message, and
  `auth_ok` exchange before sending a command. Use the advertised HA version in
  contract fixtures without assuming it proves feature availability. Use an integer
  command ID and accept only a matching successful result. Do not negotiate
  coalesced messages in this increment.
- A request has a monotonic overall deadline equal to the configured timeout,
  covering connection establishment, authentication, and command completion.
  Set each blocking phase's timeout from the remaining budget. Bound cleanup
  separately to at most one second, and document that small shutdown allowance.
  Synchronous OS DNS resolution can exceed the application deadline; document
  that platform limitation rather than claiming a strict wall-clock guarantee.
- Limit each decoded message to 16 MiB. The synchronous transport does not add a
  prefetch queue. Disable compression and ambient proxy discovery; do not follow
  redirects. These are explicit implementation choices, not upstream defaults.
- Reuse verified TLS and private-CA behavior. Keep credentials exclusively in
  the authentication exchange; do not include them in URLs. The transport must
  never log authentication frames, tokens, or raw server messages, even when
  the application enables DEBUG logging.
- Use WebSocketAuthenticationError for authentication rejection, ResponseError for
  malformed JSON/envelopes/oversized data, and existing transport categories for
  network failures. Introduce WebSocketCommandError under HomeAssistantError for
  unsuccessful command results. Its stable boundary code is
  `websocket_command_error`; expose a safe `command_code` selected from
  `unknown_command`, `unauthorized`, `invalid_format`, and `unknown_error`, never
  arbitrary server text. Extend the shared error classifier once for all callers.
- Add CLI commands `areas`, `devices`, and `entity-registry` with existing
  connection options. Each produces one JSON value or the existing JSON error
  envelope and nonzero status.
- Add `include_discovery=False` to AgentTools. When true, expose
  `ha_get_areas`, `ha_get_devices`, and `ha_get_entity_registry`; each accepts an
  empty argument object. Direct dispatch must reject disabled tools before
  connecting. CLI tool listing/dispatch gains `--include-discovery`; direct
  registry CLI commands do not require this additional flag.
- Keep WebSocket support in the standard library and preserve zero runtime
  dependencies. REST imports, commands, and tests remain installation-free.
  Registration of tool schemas performs neither network access nor setup work.

## Testing Decisions

- The main seam is the public client and CLI against a loopback simulator. Extend
  the existing HTTP fixture approach with scripted WebSocket server responses.
  Assert requested command types, authentication order, results, and cleanup;
  do not test private helper structure.
- Prove all three registry methods and CLI commands preserve extra fields, null
  relationships, empty registries, and entries without states.
- Exercise bad authentication, wrong IDs, unsuccessful commands, malformed JSON,
  wrong result containers, oversized and fragmented messages, binary messages,
  absent authentication completion, slow message delivery, and disconnects.
- Prove the overall deadline cannot be extended by partial progress. Verify
  cleanup after every failure and no automatic request replay.
- Use loopback TLS fixtures for trusted/private CA, hostname mismatch, and failed
  verification. Exercise proxy environment variables and redirect responses to
  prove credentials never reach a second endpoint. Use synthetic credentials.
- Retain focused policy tests proving disabled discovery sends no request and
  unknown tool arguments are rejected. Test token redaction in exceptions,
  traceback chains, CLI stderr, agent errors, and captured DEBUG logs.
- Test the dependency-free distribution outside the checkout on Python 3.11 and
  the current supported host runtime. Tests must not import or require a
  third-party WebSocket dependency.
- Acceptance requires all existing tests passing, all new contract scenarios
  passing, clean package builds, and installed client/CLI parity. A live instance
  is supplementary evidence and must be reported separately, never implied by
  simulator results.

## Out of Scope

Registry creation, updates or deletion; automatic area-name resolution; dashboard
or integration editing; event subscriptions; shared persistent sessions; caching;
automatic reconnect; retries; asynchronous public APIs; Supervisor operations;
changes to household permissions or Assist exposure settings.

## Further Notes

The upstream command protocol is documented in the
[WebSocket API](https://developers.home-assistant.io/docs/api/websocket/).
Command contracts should be checked in the corresponding Core
[area](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/config/area_registry.py),
[device](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/config/device_registry.py),
and [entity](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/config/entity_registry.py)
registry handlers; the latter two are implementation verification targets, not
claimed live-instance evidence. The transport implementation is intentionally
owned by Homepy and has no third-party dependency contract.
