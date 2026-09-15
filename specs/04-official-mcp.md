# Evaluate native Home Assistant MCP alongside Homepy

## Problem Statement

The original goal is to let agents use Home Assistant. Home Assistant already
offers an MCP server, but its Assist capabilities and exposure rules differ from
the REST and WebSocket APIs. Without a documented comparison, we could duplicate
existing integration work or incorrectly promise that MCP provides full UI access.

## Solution

Produce an evidence-backed compatibility guide and an opt-in verification
procedure. Explain when a caller should use native MCP, when Homepy is useful,
and how to evaluate local or Tailscale connectivity without changing household
settings or exposing credentials.

## User Stories

1. As an agent operator, I want to know what native MCP supports, so that I do not build a redundant adapter.
2. As a Python developer, I want to know where Homepy remains useful, so that I can choose a deterministic API.
3. As a user, I want Assist exposure rules explained, so that I know which entities an MCP client can access.
4. As an operator, I want raw API permissions distinguished from Assist exposure, so that I do not assume identical access boundaries.
5. As a local user, I want to understand where the MCP client runs, so that a private hostname is not mistakenly handed to an unreachable cloud client.
6. As a Tailscale user, I want routing prerequisites recorded, so that I can distinguish network access from authentication.
7. As an operator, I want to check compatibility without actions, so that an evaluation does not operate my home.
8. As an operator, I want authentication choices documented without storing credentials, so that the guide is safe to share.
9. As a maintainer, I want exact versions and evidence recorded, so that compatibility claims can be reproduced.
10. As a user, I want configuration and administration gaps stated, so that I do not mistake Assist for complete UI coverage.
11. As an agent developer, I want transport and feature differences identified, so that I can choose a compatible MCP client.
12. As an operator, I want clear unverified statuses when an instance is unavailable, so that a documentation review is not presented as a live test.

## Implementation Decisions

- Deliver a compatibility matrix and a concise local-network verification
  runbook. This is an evaluation/documentation increment; no new MCP server,
  Python MCP client, dependency, or agent runtime configuration is added.
- Compare four surfaces: existing Homepy REST, proposed Homepy WebSocket
  discovery/observation, native MCP with the built-in Assist API, and UI
  administration features deferred from this release. Mark proposed Homepy
  features as planned until they are implemented and verified.
- Cover entity control, state/context access, exact service calls, registry
  discovery, event streaming, history, conversations, dashboard editing, and
  Supervisor administration. Use supported/unsupported/conditional/unverified
  statuses with a source or observed result for every cell. Do not infer absence
  merely because a feature is missing from one page; use unverified where needed.
- Document the configured MCP endpoint, authentication options, and current
  transport requirements from official documentation. Keep built-in Assist
  behavior distinct from custom LLM APIs; tools and resources can depend on the
  selected API and Home Assistant release. Do not assume all clients support the
  same transport or authentication flow.
- Explain that Assist's exposed entities constrain its tools. Raw API access
  follows the token user's server permissions and does not automatically apply
  that filter. Homepy's service allowlist controls its own tool dispatcher, not
  the MCP server or direct Python usage.
- Separate direct local client execution from cloud-hosted execution. A local
  hostname or tailnet address is useful only when the executing client can reach
  it. Tailscale connectivity does not remove Home Assistant authentication.
  Recommend no tunnel creation or public exposure as part of this evaluation.
- Use placeholders in all examples. Do not place real tokens in command lines,
  generated documents, logs, or screenshots. Document supported environment or
  credential-storage mechanisms only after verifying the chosen client's docs.
- The verification procedure first records Home Assistant version, selected MCP
  API, client name/version, execution location, transport, and intended network
  route. Keep private host details in a separately authorized local result rather
  than a public compatibility document.
- With separately authorized access, verify connection/authentication, protocol
  initialization, and tool listing. Inspect only a specifically reviewed read
  capability if needed; never call arbitrary tools to discover whether they
  mutate. Do not enumerate or publish household data as a test artifact.
- Record auth failure, endpoint absence, unsupported transport, and routing
  failure as distinct outcomes using sanitized descriptions. Failures must not
  trigger automatic integration installation, permission changes, runtime edits,
  or repeated login loops.
- A successful read test proves only that tested capability in that exact
  configuration. Device actions, exposure enforcement with multiple accounts,
  OAuth variants, cloud reachability, and custom LLM APIs remain unverified unless
  explicitly exercised under appropriate authorization.
- The guide can be completed from official sources without an instance, provided
  the live-results section clearly says not run. The recommendation should state
  the remaining evidence needed to choose an actual deployment route.

## Testing Decisions

- This increment tests documentation claims rather than runtime code. Review
  every matrix entry against official sources or an explicitly recorded result,
  check link destinations, and search examples for real credentials or host data.
- Use the requested skill's high-level verification boundary: the real MCP client
  at its public protocol surface for any authorized live check. Do not mock a
  successful household connection or claim a simulator verifies integration setup.
- Acceptance requires the matrix, placeholder-only runbook, documented policy
  differences, clearly separated planned and implemented features, and a record
  of all unavailable live checks. No new runtime tests or dependencies are needed
  for a documentation-only deliverable.

## Out of Scope

Installing/enabling the integration, changing exposed entities, generating tokens,
changing agent runtime settings, OAuth authorization on the user's behalf,
publishing household metadata, adding MCP proxies, changing network exposure,
device actions, custom LLM APIs, and implementing a Homepy MCP server.

## Further Notes

Start with the official [MCP Server integration](https://www.home-assistant.io/integrations/mcp_server/)
and its [Core implementation](https://github.com/home-assistant/core/tree/2026.9.1/homeassistant/components/mcp_server).
Verify setup instructions against the selected agent client's own documentation
when that client is known. The evaluated native capability may remove the need
for a separate MCP layer, but that is a deployment decision, not evidence that
Homepy's deterministic REST interface is unnecessary.
