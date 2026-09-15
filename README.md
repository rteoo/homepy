# Homepy

<p align="center">
  A dependency-free Python client for Home Assistant, with REST and WebSocket
  access plus explicit JSON tools for agents.
</p>

<p align="center">
  <a href="https://github.com/rteoo/homepy/actions/workflows/tests.yml"><img src="https://github.com/rteoo/homepy/actions/workflows/tests.yml/badge.svg" alt="CI status"></a>
  <a href="https://img.shields.io/badge/python-3.11%2B-3776AB.svg"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB.svg" alt="Python 3.11 or later"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
</p>

Homepy is a synchronous wrapper for Home Assistant's REST API and selected
WebSocket capabilities. It returns JSON-compatible data for states, services,
registries, events, calendars, history, templates, intents, and Conversation.
Runtime code uses only Python's standard library; no third-party runtime
dependencies are required.

## Highlights

- REST access to entity states, services, history, logbook, calendars, cameras,
  templates, configuration checks, events, intents, and Conversation.
- Dependency-free RFC 6455 WebSocket support for area, device, and entity-registry
  discovery plus bounded event observation.
- Framework-neutral JSON function definitions and dispatch for agent runtimes.
- Explicit action policy: service calls are disabled by default and can be
  restricted to an exact service allowlist.
- TLS verification, custom CA support, bounded responses, safe errors, disabled
  redirects, ignored ambient proxies, and no automatic mutation retries.
- Preserves unknown fields inside valid Home Assistant responses.
- Python 3.11 or later, with no live Home Assistant or household data required
  for local testing.

## Quick start

Clone the repository and install it into the Python environment used by your
application or agent:

```powershell
git clone https://github.com/rteoo/homepy.git
cd homepy
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install .
```

Homepy also supports editable installs during development:

```powershell
python -m pip install -e .
```

Create a long-lived access token in your Home Assistant profile and provide it
through a secret manager or environment variable. Never put a real token in
source code, tool arguments, shell command arguments, or a committed file.
Homepy does not read `.env` files automatically.

```powershell
$env:HA_TOKEN = "<token supplied by your secret manager>"
python -m homepy health
python -m homepy states --domain light
```

There is no token command-line option. The `homepy` console command and
`python -m homepy` are both available after installation.

## Connect

Without a host setting, Homepy connects to
`http://homeassistant.local:8123`. Configure the connection with environment
variables or constructor arguments:

| Setting | Purpose | Default |
| --- | --- | --- |
| `HA_TOKEN` | Required Home Assistant access token | None |
| `HA_URL` | Full URL; takes precedence over `HA_HOST` | None |
| `HA_HOST` | Hostname, IPv4, or bracketed IPv6 address | `homeassistant.local` |
| `HA_PORT` | Override the connection port | 8123 for a bare host |
| `HA_TIMEOUT` | Socket timeout in seconds | 10 |
| `HA_CA_FILE` | Custom CA file for HTTPS | System trust store |

```python
import os

from homepy import HomeAssistant

token = os.environ["HA_TOKEN"]
ha = HomeAssistant(token, host="homeassistant.local")
print(ha.health())

lan = HomeAssistant(token, host="192.168.1.50")
tailnet = HomeAssistant(token, host="100.101.102.103")
https = HomeAssistant(token, host="https://ha.example.ts.net")
custom_port = HomeAssistant(token, host="192.168.1.50", port=8124)
```

Full URLs retain their scheme and explicit port. A bare host uses HTTP port
8123. Use a reachable LAN address, Tailscale IP, MagicDNS hostname, or existing
HTTPS endpoint; Tailscale must already provide routing and does not replace
Home Assistant authentication.

Reverse-proxy path prefixes are supported, and a trailing `/api` is accepted.
For a full URL without an explicit port, the normal scheme port is used: HTTP
80 or HTTPS 443.

## Read Home Assistant

The client exposes direct Python methods that return Home Assistant data without
discarding integration-specific fields:

```python
from homepy import HomeAssistant

ha = HomeAssistant.from_env()

states = ha.get_states(domain="light")
one_state = ha.get_state("light.desk")
services = ha.get_services()
areas = ha.get_areas()
devices = ha.get_devices()
entities = ha.get_entity_registry()
```

History requires a nonempty list of entity IDs. Timestamp arguments accept
timezone-aware Python `datetime` values or ISO strings with an explicit
timezone. Availability and permissions depend on the integrations enabled in
your Home Assistant instance and the token user.

## Control devices

Discover states and services before choosing an action:

```python
ha.call_service(
    "light",
    "turn_on",
    {"brightness_pct": 40},
    target={"entity_id": "light.desk"},
)

ha.call_service("scene", "turn_on", target={"entity_id": "scene.evening"})

forecast = ha.call_service(
    "weather",
    "get_forecasts",
    {"type": "daily"},
    target={"entity_id": "weather.home"},
    return_response=True,
)
```

`target` accepts entity, device, area, floor, and label selectors. These are
placed at the top level of the REST service body; duplicate selectors are
rejected rather than overwritten.

**`set_state()` does not operate a physical device.** It changes the state
representation held by Home Assistant. Use `call_service()` for device control.
The ordinary service-call result is a list of changed states; with
`return_response=True`, it also includes service response data. Read the target
state again when your workflow needs confirmation of its final state.

## Use from an agent

`AgentTools` exposes a narrow, framework-neutral set of JSON function
descriptors:

```python
from homepy import HomeAssistant
from homepy.agent import AgentTools

ha = HomeAssistant.from_env()
agent = AgentTools(
    ha,
    allow_actions=True,
    allowed_services={"light.turn_on", "light.turn_off", "scene.turn_on"},
    include_discovery=True,
)

tool_definitions = agent.tools
result = agent.dispatch("ha_get_state", {"entity_id": "light.desk"})
```

The default `allow_actions=False` hides and denies the service-call tool. An
empty `allowed_services` collection denies every service; `None` permits any
service only when actions are explicitly enabled. Discovery and event tools are
also opt-in with `include_discovery=True` and `include_events=True`.

The allowlist limits service names, not individual entities or payloads. The
agent orchestrator remains responsible for user authorization and target
selection; tool schemas are not a security sandbox.

For asynchronous runtimes, run synchronous calls in a worker thread:

```python
import asyncio

state = await asyncio.to_thread(ha.get_state, "light.desk")
```

Cancelling that coroutine does not cancel a request already running in the
worker thread or undo a device action.

## JSON command line

All commands write JSON. Successful output goes to stdout; failures go to
stderr and use a nonzero exit status.

```powershell
python -m homepy health
python -m homepy states --domain light
python -m homepy state light.desk
python -m homepy services
python -m homepy areas
python -m homepy devices
python -m homepy entity-registry
python -m homepy tools
python -m homepy --host 100.101.102.103 health
python -m homepy call light turn_on --target '{"entity_id":"light.desk"}' --allow-actions
python -m homepy tool ha_get_state --arguments '{"entity_id":"light.desk"}'
```

`python -m homepy tools` lists tool schemas without a token or network
connection. Add `--allowed-service DOMAIN.SERVICE` repeatedly to restrict an
enabled action tool. Use `--help` on a command for all options.

## Events and Conversation

WebSocket registry reads and event observation use separate connections. Event
streams are finite, closeable, and bounded:

```python
with ha.watch_events("state_changed", max_events=10, duration=15) as events:
    for event in events:
        print(event)

print(events.stop_reason)  # max_events, duration, or closed
```

`watch_events()` uses one connection, a 16 MiB message limit, a cumulative event
payload limit, and a one-second cleanup allowance. It does not reconnect,
retry, redirect, use ambient proxies or compression, or promise gap-free
delivery. The CLI emits one flushed NDJSON event per line:

```powershell
python -m homepy watch --event-type state_changed --max-events 10 --duration 15
python -m homepy tools --include-discovery --include-events
python -m homepy tool ha_collect_events --include-events --arguments '{"event_type":"state_changed","duration":5}'
```

Python callers may set either event limit to `None`, but at least one limit must
remain active. Agent event collection is always capped at 100 events and 30
seconds. A registered entity need not have a current state, and entity and
device area assignments can differ.

Conversation access is separately authorized:

```python
reply = ha.process_conversation("What time is it?", language="en")
```

At the agent and CLI boundaries, Conversation requires both
`allow_actions`/`--allow-actions` and `allow_conversation`/`--allow-conversation`.
It cannot be combined with an `allowed_services` collection, including an empty
one. The built-in `home_assistant` agent is selected at those boundaries;
direct Python callers may provide `agent_id`.

```powershell
python -m homepy conversation --text "What time is it?" --allow-actions --allow-conversation
```

An HTTP 200 response can still contain a structured Assist domain error; that
response is preserved. A timeout leaves the outcome unknown and is never
retried.

See [MCP compatibility](docs/mcp-compatibility.md) for the boundary between
Homepy and Home Assistant's native MCP/Assist surfaces.

## API surface

| Methods | Purpose |
| --- | --- |
| `health`, `get_config`, `get_components` | Availability and configuration |
| `get_states`, `get_state` | Current entity states |
| `set_state`, `delete_state` | State representation changes |
| `get_services`, `call_service` | Discover and invoke integration actions |
| `get_events`, `fire_event` | Event discovery and firing |
| `get_history`, `get_logbook` | Recorded state changes and activity |
| `get_error_log`, `get_camera_image` | Error text and camera bytes |
| `get_calendars`, `get_calendar_events` | Calendars and events |
| `render_template`, `check_config`, `handle_intent` | Template, config, and intent operations |
| `get_areas`, `get_devices`, `get_entity_registry` | WebSocket registry discovery |
| `watch_events`, `process_conversation` | Bounded events and Conversation |

## Data safety and limitations

- TLS certificate verification is enabled by default; use `ca_file` or
  `HA_CA_FILE` for a private CA.
- HTTP redirects are rejected, ambient HTTP proxies are ignored, and failed
  actions are never retried automatically.
- Errors omit credentials, request bodies, response bodies, URLs, and raw
  server/OS details. Response bodies are capped at 16 MiB.
- If a mutation times out or its response is lost, its outcome is unknown.
  Check the device state before deciding whether to issue another action.
- `TransportError.category` distinguishes DNS, refused-connection, timeout, TLS,
  and other network failures. HTTP errors expose `status_code`.
- Homepy does not automate browser clicks, edit dashboards or integration
  registries, or manage Supervisor. Those surfaces need separate clients.
- Live Home Assistant connectivity, Tailscale routing, household actions, and
  static type checking are outside the local test suite's verification boundary.

## Develop and build

Run the complete local verification gates from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q homepy tests
python -m homepy --help
```

Build the source archive and wheel when the existing build tools are available:

```powershell
python -m build --no-isolation
```

Artifacts are written to `dist/`. Tests use synthetic data and loopback
HTTP/WebSocket/TLS servers; they do not contact or operate a real Home Assistant
installation. See [PLAN.md](PLAN.md) for architecture and verification scope.

## License

Homepy is released under the [MIT License](LICENSE).
