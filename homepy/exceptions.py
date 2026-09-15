"""Public exceptions for the Home Assistant REST client."""

from __future__ import annotations


class HomeAssistantError(Exception):
    """Base class for errors raised by homepy."""


class ConfigurationError(HomeAssistantError):
    """The connection configuration is invalid."""


class TransportError(HomeAssistantError):
    """The request could not be completed at the network transport layer."""

    _CATEGORIES = frozenset({"dns", "refused", "timeout", "tls", "network"})

    def __init__(self, message: str | None = None, *, category: str = "network"):
        if not isinstance(category, str) or category not in self._CATEGORIES:
            category = "network"
        self.category = category
        self.hint = _TRANSPORT_HINTS.get(category, "Check network connectivity to Home Assistant.")
        super().__init__(_TRANSPORT_MESSAGES[category] if message is None else message)


class APIError(TransportError):
    """Home Assistant returned an HTTP error response."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(APIError):
    """Home Assistant rejected the supplied credentials."""

    def __init__(self, message: str = "Home Assistant authentication failed", *, status_code: int = 401):
        super().__init__(message, status_code=status_code)


class NotFoundError(APIError):
    """The requested Home Assistant resource was not found."""

    def __init__(self, message: str = "Home Assistant resource was not found", *, status_code: int = 404):
        super().__init__(message, status_code=status_code)


class ResponseError(TransportError):
    """The server response could not be safely consumed or decoded."""


class WebSocketAuthenticationError(HomeAssistantError):
    """Authentication failed after the HTTP upgrade; no HTTP status is implied."""

    def __init__(self) -> None:
        super().__init__("Home Assistant WebSocket authentication failed")


class WebSocketCommandError(HomeAssistantError):
    """A WebSocket command failed with a sanitized protocol error code."""

    def __init__(self, command_code: str = "unknown_error") -> None:
        allowed = {"unknown_command", "unauthorized", "invalid_format", "unknown_error"}
        self.command_code = command_code if isinstance(command_code, str) and command_code in allowed else "unknown_error"
        super().__init__("Home Assistant WebSocket command failed")


_TRANSPORT_MESSAGES = {
    "dns": "Home Assistant hostname could not be resolved",
    "refused": "Home Assistant connection was refused",
    "timeout": "Home Assistant connection timed out",
    "tls": "Home Assistant TLS connection could not be established",
    "network": "Home Assistant network request failed",
}

_TRANSPORT_HINTS = {
    "dns": "Check the Home Assistant hostname or DNS configuration.",
    "refused": "Check the host and port, and that Home Assistant is running.",
    "timeout": "Check network connectivity or increase the configured timeout.",
    "tls": "Check the HTTPS certificate or configured CA file.",
    "network": "Check network connectivity to Home Assistant.",
}


def error_details(exc: Exception) -> dict[str, str | int]:
    """Return a stable, sanitized description for an exception.

    The returned values are deliberately selected from fixed strings and typed
    status codes. Exception text, request data, URLs, and credentials never
    cross this boundary.
    """
    if isinstance(exc, WebSocketAuthenticationError):
        return {"code": "authentication_error", "message": "Home Assistant authentication failed"}
    if isinstance(exc, WebSocketCommandError):
        return {
            "code": "websocket_command_error",
            "message": "Home Assistant WebSocket command failed",
            "command_code": exc.command_code,
        }
    if isinstance(exc, AuthenticationError):
        return {
            "code": "authentication_error",
            "message": "Home Assistant authentication failed",
            "status_code": exc.status_code,
        }
    if isinstance(exc, NotFoundError):
        return {
            "code": "not_found",
            "message": "Home Assistant resource was not found",
            "status_code": exc.status_code,
        }
    if isinstance(exc, APIError):
        return {
            "code": "api_error",
            "message": "Home Assistant rejected the request",
            "status_code": exc.status_code,
        }
    if isinstance(exc, ResponseError):
        return {"code": "response_error", "message": "Home Assistant returned an invalid response"}
    if isinstance(exc, ConfigurationError):
        return {"code": "configuration_error", "message": "Home Assistant configuration failed"}
    if isinstance(exc, TransportError):
        category = exc.category if isinstance(exc.category, str) and exc.category in _TRANSPORT_HINTS else "network"
        return {
            "code": "transport_error",
            "message": "Home Assistant transport failed",
            "category": category,
            "hint": _TRANSPORT_HINTS[category],
        }
    if isinstance(exc, HomeAssistantError):
        return {"code": "home_assistant_error", "message": "Home Assistant request failed"}
    return {"code": "request_failed", "message": "Home Assistant request failed"}


__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "HomeAssistantError",
    "NotFoundError",
    "ResponseError",
    "TransportError",
    "WebSocketAuthenticationError",
    "WebSocketCommandError",
    "error_details",
]
