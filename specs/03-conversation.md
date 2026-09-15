# Submit conversation requests with explicit agent policy

## Problem Statement

Homepy exposes named intent handling, but callers cannot submit a sentence to
Home Assistant's conversation API or continue a conversation using its returned
ID. Adding this directly to the current service tool would bypass the meaning
of an exact service allowlist: a sentence can cause actions whose services are
not known to Homepy before the request executes.

## Solution

Add a normal REST conversation method for Python callers and a separately
enabled conversational capability for agents and the CLI. Preserve structured
successes, failures, and continuation information. Keep deterministic service
calls available for workloads that require exact service restrictions.

## User Stories

1. As a Python user, I want to submit a sentence, so that Home Assistant can interpret a natural-language request.
2. As a multilingual user, I want to specify the input language, so that Home Assistant interprets the intended language.
3. As a Python user, I want to select a configured conversation agent, so that I can use my chosen Home Assistant processor.
4. As a user, I want to preserve a conversation ID, so that follow-up requests retain context.
5. As an agent developer, I want structured target results, so that I can distinguish full, partial, and failed actions.
6. As an agent developer, I want recognition errors preserved, so that I can ask for clarification instead of claiming success.
7. As an operator, I want conversation tools disabled by default, so that upgrading Homepy grants no new action capability.
8. As an operator, I want service allowlists protected, so that natural language cannot circumvent existing restrictions.
9. As a CLI user, I want one JSON response, so that conversation calls work in scripts.
10. As a user, I want unknown response fields retained, so that future response metadata remains accessible.
11. As an operator, I want failed or timed-out requests never retried, so that an uncertain action is not repeated.
12. As an operator, I want credentials and conversation text omitted from diagnostic errors, so that troubleshooting does not expose private requests.
13. As a REST-only user, I want this feature without new dependencies, so that I can use it with my current installation.

## Implementation Decisions

- Add `process_conversation` to HomeAssistant with required `text` and optional
  keyword-only `language`, `agent_id`, and `conversation_id`. Validate supplied
  strings as nonempty; reject whitespace-only text. Omit unspecified fields.
  Preserve the user's text rather than silently rewriting it.
- POST the JSON payload to `/api/conversation/process` through the existing REST
  transport. Keep its TLS, timeout, size limits, URL rules, and failure behavior.
  No new dependency or alternative HTTP stack is needed.
- Return the full JSON object. Require a nested `response` object with a string
  `response_type` and object-valued `data` when present. Validate optional
  `conversation_id` as a string or null and `continue_conversation` as a boolean
  when present. Preserve unknown response types and other unknown fields rather
  than claiming a closed schema that prevents future compatibility.
- Treat a valid conversation response whose type is `error` as a successful
  transport exchange containing a domain error. Return it intact. Do not convert
  recognition failure into a network exception, and do not equate HTTP 200 with
  successful device control. CLI exit zero means the response was delivered;
  scripts must inspect the response type and failed targets.
- Keep the Python client stateless: the caller supplies a prior conversation ID
  and stores the returned ID. Do not share conversational context across users,
  agents, instances, or requests implicitly.
- Do not change `handle_intent`, `call_service`, or state-writing semantics.
  Direct Python use is an explicit operation like existing service calls; agent
  capability policy remains at the AgentTools and CLI boundaries.
- Add `allow_conversation=False` to AgentTools. A requested conversation
  capability requires `allow_actions=True` and `allowed_services=None`.
  If any service allowlist is configured, including an empty one, reject that
  policy combination before network access. Do not guess services from text.
- Expose `ha_process_conversation` only under that explicit valid policy. Its
  schema accepts text, language, and conversation ID. In this first increment,
  the tool always explicitly selects the built-in `home_assistant` conversation
  agent. Arbitrary configured agent selection is available only to direct Python
  callers, avoiding another agent-routing capability in the tool policy.
- Enforce policy again on dispatch so a hidden or disabled tool cannot be called
  by name. A conversation request is always action-capable, even when the text
  appears to ask only for information. Schema discovery performs no request.
- Add CLI `conversation` with required `--text` and optional `--language` and
  `--conversation-id`. Require both `--allow-actions` and
  `--allow-conversation`; use the same built-in agent restriction. Any supplied
  service allowlist conflicts and fails before a request. The `tool` command
  supports the same conversation enablement policy.
- Reuse shared sanitized errors for configuration, HTTP, malformed responses,
  and timeouts. Do not include text, response speech, token, host, or raw body in
  diagnostics. Successful outputs intentionally contain the requested server
  response. Document that CLI text arguments can be visible to local process
  inspection and shell history; Python input is preferable for private text.
- Never replay a conversation after timeout or uncertain delivery. A timeout
  cannot prove that an intended action did not occur. Callers must inspect state
  or seek clarification before making a new action request.

## Testing Decisions

- Extend the existing public-client HTTP integration fixture and CLI subprocess
  tests. Assert exact endpoint and JSON fields, omitted optionals, Unicode text,
  continuation IDs, and direct Python agent selection.
- Cover valid query answers, full actions, partial target failures, recognition
  errors, optional fields, unknown fields, and future response types. Assert
  neither the wrapper nor tool boundary invents success from a status code.
- Test malformed response envelopes and invalid input before I/O. Preserve the
  existing HomeAssistantError contract and sanitized error classifier.
- Use a policy matrix over actions enabled/disabled, conversation enabled/disabled,
  and service allowlist None/empty/nonempty. Denied combinations must produce zero
  requests even when dispatch is invoked directly.
- Prove the agent and CLI explicitly send the built-in agent ID and reject an
  injected `agent_id` argument. Direct Python selection remains supported.
- Simulate a server accepting a request and then withholding its response. Assert
  a timeout and exactly one request, with no automatic replay.
- Exercise secrets and private text in failure fixtures; assert no leaks through
  formatted tracebacks, CLI diagnostics, or agent errors. Successful return data
  is checked for faithful preservation rather than blanket redaction.
- Acceptance requires all existing tests plus these cases, compile checks,
  package builds, and installed CLI parity. No real device command is needed to
  establish the client contract.

## Out of Scope

Speech-to-text, text-to-speech, audio pipelines, conversation memory storage,
arbitrary agent routing through LLM tools, interpreting sentences locally,
service prediction, policy changes in Home Assistant, automatic follow-up
actions, action retries, and guarantees about a configured agent's language support.

## Further Notes

The official [Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/)
defines the request and response protocol. Runtime handling follows the
configured Home Assistant instance and its conversation integration. This
capability is independent of the proposed WebSocket dependency and can be
capability uses the existing dependency-free REST transport.
