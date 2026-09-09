"""Framework-neutral JSON tools for Home Assistant.

The module deliberately knows only the small client interface described in
``PLAN.md``.  It does not inspect or invoke arbitrary client attributes, which
keeps an agent's callable surface explicit and reviewable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from typing import Any

from .exceptions import error_details


class AgentToolError(Exception):
    """A safe, user-facing error raised while validating or dispatching a tool."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str | None = None,
        status_code: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.category = category
        self.status_code = status_code
        self.hint = hint
        super().__init__(message)

    def to_details(self) -> dict[str, str | int]:
        """Return the same safe fields exposed by ``error_details``."""

        details: dict[str, str | int] = {"code": self.code, "message": self.message}
        if self.category is not None:
            details["category"] = self.category
        if self.status_code is not None:
            details["status_code"] = self.status_code
        if self.hint is not None:
            details["hint"] = self.hint
        return details


def _json_compatible(value: Any) -> Any:
    """Round-trip a result through strict JSON and hide serializer details."""

    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise AgentToolError("invalid_response", "Home Assistant returned unsupported data") from None


def _client_error(exc: Exception) -> AgentToolError:
    """Map a client failure through the shared safe exception classifier."""

    details = error_details(exc)
    category = details.get("category")
    status_code = details.get("status_code")
    hint = details.get("hint")
    return AgentToolError(
        str(details["code"]),
        str(details["message"]),
        category=category if isinstance(category, str) else None,
        status_code=status_code if isinstance(status_code, int) else None,
        hint=hint if isinstance(hint, str) else None,
    )


def _is_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_object(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise AgentToolError("invalid_arguments", "Tool arguments must be a JSON object")
    # Copying also prevents a caller from changing the values while dispatch
    # is in progress, and ensures only ordinary string keys reach validation.
    copied = dict(arguments)
    if any(not isinstance(key, str) for key in copied):
        raise AgentToolError("invalid_arguments", "Tool argument names must be strings")
    return copied


def _validate_keys(
    arguments: Mapping[str, Any], allowed: set[str], required: set[str] | None = None
) -> None:
    required = required or set()
    unknown = set(arguments) - allowed
    if unknown:
        raise AgentToolError("invalid_arguments", "Unknown tool argument")
    missing = required - set(arguments)
    if missing:
        raise AgentToolError("invalid_arguments", "Missing required tool argument")


def _validate_string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not _is_string(value):
        raise AgentToolError("invalid_arguments", f"{name} must be a non-empty string")
    return value


def _validate_optional_string(arguments: Mapping[str, Any], name: str) -> str | None:
    if name not in arguments or arguments[name] is None:
        return None
    return _validate_string(arguments, name)


def _validate_optional_object(arguments: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    if name not in arguments or arguments[name] is None:
        return None
    value = arguments[name]
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise AgentToolError("invalid_arguments", f"{name} must be a JSON object")
    try:
        normalized = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise AgentToolError("invalid_arguments", f"{name} must contain JSON-compatible values") from None
    if not isinstance(normalized, dict):
        raise AgentToolError("invalid_arguments", f"{name} must be a JSON object")
    return normalized


def _validate_optional_bool(arguments: Mapping[str, Any], name: str, default: bool = False) -> bool:
    if name not in arguments:
        return default
    value = arguments[name]
    if type(value) is not bool:
        raise AgentToolError("invalid_arguments", f"{name} must be a boolean")
    return value


class AgentTools:
    """Expose a narrow, policy-controlled set of Home Assistant tools.

    ``allowed_services=None`` means every service is allowed when actions are
    enabled.  An empty iterable means no service is allowed; this distinction
    is intentional and is retained after construction.
    """

    _ACTION_TOOL = "ha_call_service"

    def __init__(
        self,
        client: Any,
        *,
        allow_actions: bool = False,
        allowed_services: Iterable[str] | None = None,
    ) -> None:
        if type(allow_actions) is not bool:
            raise TypeError("allow_actions must be a boolean")
        if isinstance(allowed_services, (str, bytes)):
            raise TypeError("allowed_services must be an iterable of service names")
        if allowed_services is None:
            normalized_services = None
        else:
            try:
                normalized_services = frozenset(allowed_services)
            except TypeError:
                raise TypeError("allowed_services must be an iterable of service names") from None
            if any(not isinstance(item, str) or not item for item in normalized_services):
                raise TypeError("allowed_services must contain non-empty strings")
        self.client = client
        self.allow_actions = allow_actions
        self.allowed_services = normalized_services

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible function descriptors."""

        return self.tool_definitions()

    def get_tools(self) -> list[dict[str, Any]]:
        """Alias useful to frameworks that call their registry ``get_tools``."""

        return self.tool_definitions()

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "ha_get_states",
                    "description": "Get Home Assistant entity states, optionally filtered by domain.",
                    "parameters": {
                        "type": "object",
                        "properties": {"domain": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ha_get_state",
                    "description": "Get the current state of one Home Assistant entity.",
                    "parameters": {
                        "type": "object",
                        "properties": {"entity_id": {"type": "string"}},
                        "required": ["entity_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ha_get_services",
                    "description": "List Home Assistant service domains and services.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if self.allow_actions:
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": self._ACTION_TOOL,
                        "description": "Call a Home Assistant service. Physical device actions require explicit policy.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "domain": {"type": "string"},
                                "service": {"type": "string"},
                                "service_data": {"type": "object"},
                                "target": {"type": "object"},
                                "return_response": {"type": "boolean"},
                            },
                            "required": ["domain", "service"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return definitions

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call one known client method while keeping client errors private."""

        try:
            # Keep this allowlist explicit: a tool name can never become an
            # arbitrary attribute lookup on the client.
            if method_name == "get_states":
                method = self.client.get_states
            elif method_name == "get_state":
                method = self.client.get_state
            elif method_name == "get_services":
                method = self.client.get_services
            elif method_name == "call_service":
                method = self.client.call_service
            else:  # pragma: no cover - all callers are fixed below
                raise AgentToolError("client_error", "Home Assistant client is unavailable")
            if not callable(method):
                raise AgentToolError("client_error", "Home Assistant client is unavailable")
            return _json_compatible(method(*args, **kwargs))
        except AgentToolError:
            raise
        except Exception as exc:
            # Do not expose exception text: HTTP errors can accidentally contain
            # URLs, request data, or credentials supplied by a client adapter.
            raise _client_error(exc) from None

    def dispatch(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Validate and invoke one registered tool, returning JSON data."""

        if not isinstance(name, str) or not name:
            raise AgentToolError("unknown_tool", "Unknown Home Assistant tool")
        args = _validate_object(arguments)

        if name == "ha_get_states":
            _validate_keys(args, {"domain"})
            domain = _validate_optional_string(args, "domain")
            return self._call("get_states", domain=domain)

        if name == "ha_get_state":
            _validate_keys(args, {"entity_id"}, {"entity_id"})
            return self._call("get_state", _validate_string(args, "entity_id"))

        if name == "ha_get_services":
            _validate_keys(args, set())
            return self._call("get_services")

        if name == self._ACTION_TOOL:
            if not self.allow_actions:
                raise AgentToolError("actions_disabled", "Home Assistant actions are disabled")
            _validate_keys(args, {"domain", "service", "service_data", "target", "return_response"}, {"domain", "service"})
            domain = _validate_string(args, "domain")
            service = _validate_string(args, "service")
            service_name = f"{domain}.{service}"
            if self.allowed_services is not None and service_name not in self.allowed_services:
                raise AgentToolError("service_denied", "Home Assistant service is not allowed")
            service_data = _validate_optional_object(args, "service_data")
            target = _validate_optional_object(args, "target")
            return_response = _validate_optional_bool(args, "return_response")
            return self._call(
                "call_service",
                domain,
                service,
                service_data,
                target=target,
                return_response=return_response,
            )

        raise AgentToolError("unknown_tool", "Unknown Home Assistant tool")


__all__ = ["AgentToolError", "AgentTools"]
