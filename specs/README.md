# Homepy agent capabilities: implementation specifications

Drafted 2026-09-09 and updated for the dependency-free Homepy 0.2.0
implementation on 2026-09-14. Source and tests establish implementation status;
these contracts do not establish live-instance verification.

## Delivery order

| Order | Specification | Outcome | Dependency |
| --- | --- | --- | --- |
| 1 | [Registry discovery](01-registry-discovery.md) | Agents can discover areas, devices, and registered entities. | Internal standard-library WebSocket transport. |
| 2 | [Bounded live events](02-live-events.md) | Agents and scripts can observe changes without polling. | Registry specification's WebSocket transport and error contract. |
| Independent | [Conversation requests](03-conversation.md) | Python callers can submit sentences to Assist; agent access has explicit policy. | Existing REST transport; no new dependency. |
| Independent | [Official MCP evaluation](04-official-mcp.md) | A documented choice between native MCP and Homepy for each use case. | Live checks require a separately authorized instance and client. |

The original goal is broader access to Home Assistant through agents. This
increment provides discovery, observation, and conversational control. It does
not promise full UI parity. Dashboard editing, integration configuration flows,
registry writes, and Supervisor administration require subsequent specifications.

## Shared decisions

- Preserve Python 3.11+, the synchronous public client, existing REST methods,
  environment precedence, and documented host/port behavior.
- Preserve unknown server fields within validated response containers. A state
  is current runtime data; a registry entry describes an entity or device and
  may exist without a corresponding state. An area groups devices/entities.
- Share connection configuration and sanitized exception classification across
  REST, WebSocket, CLI, and agent boundaries. Never retry mutations automatically.
- Test public behavior through a loopback Home Assistant simulator. Extend the
  existing HTTP integration approach with WebSocket sessions; use focused agent
  policy tests for proving that denied operations send nothing.
- The implementation remains dependency-free: the base package, WebSocket
  support, CLI, agent adapter, and tests use only the Python standard library.
  Do not add, install, or document a runtime or optional WebSocket dependency.
- WebSocket framing, masking, fragmentation, and TLS remain bounded parts of the
  standard-library implementation. The base installation remains usable without
  any additional package, and tests remain independent of live services.
- Discovery reflects the authenticated user's server permissions. It does not
  inherit Assist's exposed-entity filter. Document that difference explicitly.

## Implementation and publication

The user authorized implementation, local commits, a push, and a pull request
to `rteoo/homepy`. No separate tracker labels or issue metadata are created by
the implementation. The later requirement that every installation remain
dependency-free supersedes the original optional WebSocket dependency proposal.

## Verification limits

Official documentation and selected upstream source informed the contracts.
Links to `dev` are discovery references, not guarantees about an installed
release. Registry list commands were checked against Home Assistant Core
2026.9.1 source. Unsupported commands fail explicitly. No household Home
Assistant instance, Tailscale route, or live credentials were used.
