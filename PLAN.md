# Homepy implementation plan

Build a reusable Python 3.11+ wrapper for Home Assistant's documented REST API.
The package uses the standard library at runtime and returns JSON-compatible
dicts/lists, text for templates/logs, and bytes for camera snapshots.

## Architecture and ownership

- `homepy/config.py`, `transport.py`, `exceptions.py`: immutable connection
  settings, host normalization, bearer authentication, bounded HTTP requests,
  TLS validation, safe errors. No redirects or automatic action retries.
- `homepy/client.py`: `HomeAssistant` endpoint methods and `from_env()`.
- `homepy/agent.py`, `cli.py`, `__main__.py`: framework-neutral JSON tool
  definitions/dispatch and a CLI. Agent mutations are opt-in and can be limited
  to exact service names.
- Parent integration: packaging, docs, examples, full-suite verification,
  independent review, and local HTTP integration checks.

## Shared interface

`ConnectionConfig(token, host="homeassistant.local", port=None, timeout=10.0,
verify_ssl=True, ca_file=None)` exposes normalized `base_url`. Bare hosts use
HTTP and port 8123. Explicit URLs preserve their scheme and default scheme port;
explicit port overrides are permitted. Reverse proxy path prefixes are retained;
a trailing `/api` is normalized away. Credentials/query/fragment in hosts are
rejected. `ConnectionConfig.from_env()` reads `HA_TOKEN`, `HA_URL` (preferred),
`HA_HOST`, `HA_PORT`, `HA_TIMEOUT`, and `HA_CA_FILE`.

`Transport(config).request(method, path, *, params=None, data=None,
response_type="json")` takes only API-relative paths (e.g. `states/light.desk`,
empty string for `/api/`). JSON, text, or bytes are returned. Public exceptions:
`HomeAssistantError`, `ConfigurationError`, `TransportError`, `AuthenticationError`,
`NotFoundError`, `APIError`, `ResponseError`. `APIError.status_code` is available.
Errors never include bearer tokens, request bodies, response bodies, or raw URLs.

`HomeAssistant(token, host="homeassistant.local", *, port=None, timeout=10.0,
verify_ssl=True, ca_file=None)` exposes `config`, `from_env()`, and endpoint methods:
`health`, `get_config`, `get_components`, `get_states`, `get_state`, `set_state`,
`delete_state`, `get_events`, `fire_event`, `get_services`, `call_service`,
`get_history`, `get_logbook`, `get_error_log`, `get_camera_image`, `get_calendars`,
`get_calendar_events`, `render_template`, `check_config`, `handle_intent`.

`call_service(domain, service, service_data=None, *, target=None,
return_response=False)` flattens target selectors (`entity_id`, `device_id`,
`area_id`, `floor_id`, `label_id`) into REST service data; conflicting keys raise
ValueError. All endpoint methods return server data without hiding extra fields.
`get_states(*, domain=None)` filters locally. `get_history(entity_ids, *, start=None,
end=None, minimal_response=False, no_attributes=False,
significant_changes_only=False)` requires nonempty entity IDs. Calendar dates
are passed via `get_calendar_events(entity_id, start, end)`. Timestamps accept
timezone-aware datetime objects or ISO strings with an explicit timezone.

## Scope and proof

The source of truth is https://developers.home-assistant.io/docs/api/rest/ .
Services operate physical devices; setting state only changes HA's representation.
WebSocket event streaming, dashboard/registry editing, and Supervisor APIs are
outside this REST release. Tailscale supplies routing and does not replace the
Home Assistant token. No live household actions will be run during development.

Use `python -m unittest discover -s tests -v` and real loopback HTTP tests to prove
wire behavior, errors, agent dispatch, and CLI operation. Record any live-host
verification gap. Approved packaging uses setuptools as a build-only dependency
and installs the built package into a fresh local virtual environment for proof.
There are no runtime dependencies, pushes, or external mutations. Local task
commits preserve the implementation and reviewed fixes.

## Delivery status

- Implemented all listed REST methods, connection settings, agent tools, and CLI.
- Verified 48 tests on Windows Python 3.14.6, including real loopback HTTP,
  CLI subprocesses, timeout/truncation handling, and action policy. Compileall
  passed; source syntax also parses using the Python 3.11 grammar. Python 3.11
  runtime execution and live Home Assistant/Tailscale connectivity were not tested.
- Completed parent review and an independent read-only agent/CLI review.
- Added approved setuptools packaging for version 0.1.1, the `homepy` console
  command, explicit runtime package contents, and the typed-package marker.
- Built both distributions with existing setuptools 81.0.0 and build 1.5.0.
  The wheel was built from the source archive, then installed offline with no
  dependencies into the repository's fresh `.venv`.
- Verified installed metadata, wheel/source archive contents, Python imports,
  real loopback HTTP, and both CLI entry points from outside the source checkout.
  The installed package declares no runtime dependencies. No host tooling was
  upgraded, and no remote mutations were made.

## Review remediation

- Reproduced the reported transport-error ambiguity, skipped subclass initializer,
  malformed state/config response handling, and misleading empty-allowlist help.
- Added safe transport categories and hints to Python, agent, and CLI errors;
  consolidated the exception classification table and environment parser.
- Restored normal subclass initialization and validate endpoint return containers
  at runtime, preserving unknown fields. The `py.typed` marker remains; no static
  type checker was available or installed, so static type checking is unverified.
- Corrected CLI policy help and made installation paths portable. Bare-host
  port 8123 and explicit URL scheme-port behavior are intentionally unchanged.
- Used explicit UTF-8 request bytes, format-appropriate Accept headers, public TLS
  context construction, and simpler connection cleanup and JSON rejection.
  Removed redundant tool copying, mutable defaults, and broad TypeError labeling.
- Saved the baseline before remediation and retained local review commits on
  `fix/review-correctness`. Optional CI/linter setup and publishing metadata are
  outside these correctness fixes; no new dependency was introduced.
