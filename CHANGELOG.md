# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

0.1.1 was set in `pyproject.toml` but never tagged, so its changes first reached
users in 0.2.0; it links to the commit that carried it instead.

## [0.2.0] - 2026-09-23

### Added

- **Registry discovery.** `get_areas()`, `get_devices()`, and
  `get_entity_registry()` read Home Assistant's registries over a
  dependency-free RFC 6455 WebSocket, with matching CLI commands and opt-in
  agent tools (`include_discovery=True`).
- **Bounded event observation.** `watch_events()` returns a closeable stream
  limited by event count and duration. The CLI `watch` command emits one
  flushed NDJSON line per event; the agent tool (`include_events=True`) is
  capped at 100 events and 30 seconds.
- **Conversation.** `process_conversation()` submits text to Home Assistant
  Assist. Agents and the CLI need both `allow_actions` and
  `allow_conversation`, with no service allowlist.
- **Plain-HTTP warning.** Connecting over plain HTTP to a non-loopback host
  emits `homepy.InsecureTransportWarning`, because the bearer token travels
  unencrypted. Filter it by category once a tunnel is verified.
- CI on Windows and Linux with Python 3.11 and 3.14, capability specs in
  `specs/`, an MCP compatibility guide, and a project icon.

### Changed

- **CLI stderr is always one JSON document.** It carries an `error` key on
  failure and a `warning` key for plain-HTTP connections; Python's own
  warning text no longer appears there.
- **Unusable arguments fail immediately.** Allowlist entries that are not in
  `DOMAIN.SERVICE` form raise `TypeError` instead of silently denying every
  call; empty or non-string `render_template()` templates and `get_logbook()`
  entity IDs raise `ValueError` before any request is sent.

### Fixed

- `HomeAssistant.from_env()` no longer emits the plain-HTTP warning twice, and
  the warning points at the caller's line instead of generated code.
- `watch` exits cleanly when its output pipe closes, without a second
  failure at interpreter shutdown.

## [0.1.1] - 2026-09-09

### Added

- Synchronous REST client covering states, services, events, history,
  logbook, calendars, camera snapshots, templates, config checks, and intents.
- Framework-neutral JSON agent tools with service calls disabled by default
  and optional exact-service allowlists.
- JSON command line available as `homepy` and `python -m homepy`.
- Configuration from `HA_TOKEN`, `HA_URL`, `HA_HOST`, `HA_PORT`,
  `HA_TIMEOUT`, and `HA_CA_FILE`, with TLS verification and custom CA support.
- Bounded responses, rejected redirects, sanitized errors, and no automatic
  retries of mutations.
- `TransportError.category` distinguishes DNS, refused-connection, timeout,
  TLS, and other network failures.

[0.2.0]: https://github.com/rteoo/homepy/releases/tag/v0.2.0
[0.1.1]: https://github.com/rteoo/homepy/commit/c994a58
