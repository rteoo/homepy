# Homepy

A Python client for Home Assistant's REST API, with JSON tools that other agents
can call. Python 3.11 or later; no third-party runtime dependencies.

It reads entities and available services, calls device actions, retrieves history,
calendars and camera snapshots, renders templates, and handles intents. Unknown
integration-specific fields remain available in the returned dictionaries.

## Install

Install into the Python environment used by your agent:

```powershell
python -m pip install C:\Users\rodri\Projects\homepy
```

Alternatively, install a built wheel without downloading dependencies:

```powershell
python -m pip install --no-index --no-deps C:\Users\rodri\Projects\homepy\dist\homepy-0.1.0-py3-none-any.whl
```

Source builds use [setuptools 77 or later](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html).
The installed package has no runtime
dependencies. Both `homepy` and `python -m homepy` provide the JSON CLI after
installation. These commands install this local project, not a PyPI package
with a matching name.

## Connect

Create a long-lived access token in your Home Assistant profile and supply it as
`HA_TOKEN` through your agent's secret manager or environment. Never put a real
token in source code, tool arguments, shell command arguments, or a committed file.
Homepy does not read `.env` files automatically.

From this checkout, Python can import `homepy` directly:

```python
from homepy import HomeAssistant

ha = HomeAssistant.from_env()
print(ha.health())
lights = ha.get_states(domain="light")
services = ha.get_services()
```

After installation, the same imports work from any directory in that Python
environment. Source-only use is also supported by adding this checkout's root
directory to the agent's `PYTHONPATH`.

Without a host setting, Homepy connects to `http://homeassistant.local:8123`.

| Setting | Purpose | Default |
| --- | --- | --- |
| `HA_TOKEN` | Required Home Assistant access token | None |
| `HA_URL` | Full URL; takes precedence over `HA_HOST` | None |
| `HA_HOST` | Hostname, IPv4, or bracketed IPv6 address | `homeassistant.local` |
| `HA_PORT` | Override the connection port | 8123 for a bare host |
| `HA_TIMEOUT` | Socket timeout in seconds | 10 |
| `HA_CA_FILE` | Custom certificate authority file for HTTPS | System trust |

Connection examples (token already supplied securely):

```python
import os
from homepy import HomeAssistant

token = os.environ["HA_TOKEN"]
local = HomeAssistant(token)                              # homeassistant.local:8123
lan = HomeAssistant(token, host="192.168.1.50")            # HTTP :8123
tailnet = HomeAssistant(token, host="100.101.102.103")      # HTTP :8123
magic_dns = HomeAssistant(token, host="homeassistant")     # HTTP :8123
https = HomeAssistant(token, host="https://ha.example.ts.net")  # HTTPS :443
custom = HomeAssistant(token, host="192.168.1.50", port=8124)
ipv6 = HomeAssistant(token, host="[fd7a:115c:a1e0::1234]")
```

A full URL uses its explicit port or the normal scheme port (HTTP 80, HTTPS 443).
Include `:8123` in a full URL when needed. Reverse-proxy path prefixes work, and
a trailing `/api` is accepted. The wrapper retains the requested bare-host default
of 8123; set the actual port if your installation differs.

Tailscale must already provide connectivity from the machine running the agent
to Home Assistant, directly or through a subnet router. Use the reachable
Tailscale IP, MagicDNS hostname, or existing HTTPS endpoint. Homepy does not set up
Tailscale, change its access rules, or replace Home Assistant authentication.

## Control devices

Discover entities with `get_states()` and service fields with `get_services()`
before choosing an action. Service and entity availability depends on the
integrations enabled in your instance.

```python
ha.call_service(
    "light", "turn_on",
    {"brightness_pct": 40},
    target={"entity_id": "light.desk"},
)

ha.call_service("scene", "turn_on", target={"entity_id": "scene.evening"})
ha.call_service("script", "turn_on", target={"entity_id": "script.bedtime"})
ha.call_service(
    "climate", "set_temperature",
    {"temperature": 23},
    target={"entity_id": "climate.living_room"},
)

forecast = ha.call_service(
    "weather", "get_forecasts",
    {"type": "daily"},
    target={"entity_id": "weather.home"},
    return_response=True,
)
```

`target` accepts entity, device, area, floor, and label selectors. The wrapper puts
these selectors at the top level of the REST service body. Duplicate selectors
between `target` and `service_data` are rejected rather than overwritten.

Use `return_response=True` only for a service that supports or requires response
data. The ordinary result is a list of changed states; with response data it is
an object containing `changed_states` and `service_response`. A changed-state
list can include unrelated concurrent changes. Read the target state again when
your workflow needs confirmation of its final state.

**`set_state()` does not operate a physical device.** It changes the state
representation held by Home Assistant. Use `call_service()` for device control.
The Python client permits mutations directly; the agent adapter adds an explicit
capability policy for whichever functions you choose to expose.

## Use from an agent

```python
from homepy import HomeAssistant
from homepy.agent import AgentTools

ha = HomeAssistant.from_env()
agent = AgentTools(
    ha,
    allow_actions=True,
    allowed_services={"light.turn_on", "light.turn_off", "scene.turn_on"},
)

tool_definitions = agent.tools  # Pass these Chat Completions-style descriptors to your framework.
result = agent.dispatch("ha_get_state", {"entity_id": "light.desk"})
```

The adapter supplies function-tool JSON definitions and validates incoming
arguments before dispatch. It exposes entity discovery, a single entity's state,
service discovery, and service calls. With the default `allow_actions=False`,
the service-call tool is neither advertised nor callable. An empty
`allowed_services` collection allows no services; `None` permits any service
when actions are enabled.

The allowlist limits service names, not individual entities or service payloads.
Your agent orchestrator remains responsible for user authorization and choosing
permitted targets. Tool schemas and the adapter are not a security sandbox.
Read results can contain private household data; send them only to approved
destinations.

For asynchronous agent runtimes, run synchronous client calls in a worker thread:

```python
import asyncio

state = await asyncio.to_thread(ha.get_state, "light.desk")
```

Cancelling the coroutine does not cancel a request already running in that
thread, and does not undo a device action.

## JSON command line

With `HA_TOKEN` set securely, run these in the installed environment or from the
checkout:

```powershell
python -m homepy health
python -m homepy states --domain light
python -m homepy state light.desk
python -m homepy services
python -m homepy tools
python -m homepy --host 100.101.102.103 health
python -m homepy call light turn_on --target '{"entity_id":"light.desk"}' --allow-actions
python -m homepy tool ha_get_state --arguments '{"entity_id":"light.desk"}'
```

Successful commands write JSON to stdout. Failures write JSON to stderr and use a
nonzero exit status. There is no token command-line option. `call` and mutating
`tool` invocations need `--allow-actions`. Use `--help` on a command for its full
options.

`python -m homepy tools` lists the tool schemas without a token or network
connection. `--allowed-service DOMAIN.SERVICE` can be repeated on `call` and
`tool` invocations to restrict the enabled action tool.

## REST surface

| Methods | Purpose |
| --- | --- |
| `health`, `get_config`, `get_components` | Availability and configuration |
| `get_states`, `get_state` | Current entities and states |
| `set_state`, `delete_state` | State representation creation/update/removal |
| `get_services`, `call_service` | Discover and invoke integration actions |
| `get_events`, `fire_event` | Event listener discovery and event firing |
| `get_history`, `get_logbook` | Recorded state changes and activity |
| `get_error_log` | Current Home Assistant error log as text |
| `get_camera_image` | Snapshot bytes |
| `get_calendars`, `get_calendar_events` | Calendars and events |
| `render_template` | Home Assistant template output as text |
| `check_config` | Validate Home Assistant configuration |
| `handle_intent` | Execute an intent through the intent integration |

History takes a nonempty list of entity IDs. Timestamp arguments accept aware
Python `datetime` values or ISO strings with a timezone. Endpoint availability
and permissions depend on Home Assistant's installed integrations and token user.

## Failures and connection behavior

Catch `HomeAssistantError` for client failures. More specific exceptions include
`ConfigurationError`, `TransportError`, `AuthenticationError`, `NotFoundError`,
`APIError`, and `ResponseError`. HTTP failures expose `status_code`. Invalid
endpoint arguments raise `ValueError` or `TypeError` before a request is sent.

TLS certificate verification is enabled by default. Use `ca_file` or `HA_CA_FILE`
for a private CA. HTTP redirects are rejected, ambient HTTP proxies are ignored,
and failed actions are not retried automatically. Errors omit raw response
bodies, request payloads, URLs, and credentials. If a mutation times out or its
response is lost, its outcome is unknown: check the device state before deciding
whether to issue another action.

Responses are capped at 16 MiB, including camera snapshots and history. Request a
smaller history window if that cap is reached. The timeout bounds socket
operations, not the total duration of a peer that continuously sends data.

## Scope

This implements the [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/).
It controls devices through services available on that API. It does not automate
browser clicks, edit dashboards or integration registries, manage Supervisor,
or implement [WebSocket subscriptions](https://developers.home-assistant.io/docs/api/websocket/).
Those surfaces need separate clients; REST coverage does not imply every UI
administration operation is available.

## Development

```powershell
python -m unittest discover -s tests -v
python -m compileall -q homepy tests
```

With `build` and setuptools already available in your development environment,
build the source archive and a wheel from that archive:

```powershell
python -m build --no-isolation
```

Artifacts are written to `dist/`. The wheel contains the runtime package, its
`py.typed` marker, license, and metadata; the source archive also includes tests
and the implementation plan.

Tests use fake data and loopback HTTP servers. They do not contact or operate a
real Home Assistant installation. See [PLAN.md](PLAN.md) for architecture and
verification scope.
