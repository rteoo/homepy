# Home Assistant MCP compatibility

This guide compares Home Assistant's native MCP server with Homepy's
dependency-free client. It records documented capability boundaries; it is not
evidence of a live installation. The live verification procedure below was not
run because no instance or credentials were authorized.

## Compatibility matrix

| Capability | Homepy REST | Homepy WebSocket (0.2.0) | Native MCP with Assist | UI administration |
| --- | --- | --- | --- | --- |
| Entity state/context | Supported | Registry metadata only; current states use REST | Conditional: Assist exposes permitted entity context | Deferred from this increment |
| Exact service calls | Supported with explicit agent policy | Deferred | Conditional: Assist decides the action and exposed entities | Deferred from this increment |
| Area/device/entity registry reads | Unsupported | Supported: `get_areas`, `get_devices`, `get_entity_registry` | Unverified as a direct MCP capability; Assist context is exposed instead | Deferred from this increment |
| Live event observation | Existing listener listing only | Supported: bounded `watch_events` | No equivalent event stream established by the documented Assist surface | Deferred from this increment |
| History/logbook | Supported | Deferred | Unverified | Deferred from this increment |
| Conversation/Assist | Supported: `process_conversation` | Deferred protocol path | Supported through `/api/mcp/assist` | Deferred from this increment |
| Dashboard editing | Unsupported | Unsupported | Unsupported by the documented Assist surface | Deferred from this increment |
| Integration registry/configuration | Unsupported | Unsupported | Unsupported by the documented Assist surface | Deferred from this increment |
| Supervisor administration | Unsupported | Unsupported | Unsupported by the documented Assist surface | Deferred from this increment |

“Supported” describes an implemented Homepy surface or documented native MCP
capability; it does not claim live-instance verification. “Conditional” depends
on Home Assistant configuration, exposed
entities, user permissions, and the selected MCP client. “Unverified” means the
official sources do not establish the exact cell and no live check was run.

## Native MCP boundary

The official MCP integration exposes Streamable HTTP at `/api/mcp`; the built-in
Assist API is available at `/api/mcp/assist`. The client supplies an
authentication token, or a compatible client can use Home Assistant's OAuth
flow. The configured API and Home Assistant release affect available tools.
Clients without Streamable HTTP support may need a compatible local gateway.
Network reachability must still be supplied by the deployment.

Native MCP control is constrained by Home Assistant's exposed-entities setting.
Raw Homepy REST/WebSocket access follows the token user's server permissions;
Homepy's `allowed_services` policy applies only to its own agent adapter.
Neither policy changes the other surface.

A local hostname or Tailscale address works only when the MCP client runs where
that route is reachable. Tailscale provides routing, not Home Assistant
authentication. This guide recommends no tunnel, public exposure, integration
installation, or settings change.

## Recommendation

Use native MCP for Assist-style control when its exposed-entity boundary and
available tools fit the agent. Use Homepy for explicit REST service requests,
registry discovery, and bounded event observation. Homepy's local policy is
useful only when calls go through its agent adapter; direct Python methods use
the token user's Home Assistant permissions. Neither option provides complete
UI administration. Registry commands were checked against Home Assistant Core
2026.9.1 source; server permissions and older versions can still reject them.

## Placeholder-only verification runbook

Run this only after separately authorizing a specific read-only check. Replace
every bracketed value locally; do not commit the resulting host, token, or
household data.

Record: Home Assistant version `[HA_VERSION]`, API `[assist or configured ID]`,
client and version `[CLIENT]`, execution location `[local or cloud]`, transport
`Streamable HTTP`, and route `[local LAN, Tailscale, or approved HTTPS]`.

1. Configure the chosen MCP client for `https://[HA_HOST]/api/mcp/assist` (or
   the approved endpoint) using its documented credential storage.
2. Initialize the MCP session and list tools. Record only success/failure and
   sanitized protocol errors.
3. If separately approved, invoke one specifically reviewed read capability.
   Do not enumerate household data or probe unknown tools.
4. Classify failure as routing, endpoint absence, authentication, or unsupported
   transport. Do not retry login loops or change configuration automatically.

No live results are recorded here. A successful check proves only the tested
capability in that exact client, release, identity, and route.

## Sources

- [Home Assistant MCP Server integration](https://www.home-assistant.io/integrations/mcp_server/)
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Home Assistant Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/)
- [Home Assistant LLM/MCP API](https://developers.home-assistant.io/docs/core/llm/)
