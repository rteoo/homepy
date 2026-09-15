"""Dependency-free Home Assistant REST and WebSocket client."""

from .client import HomeAssistant
from .config import ConnectionConfig
from .events import EventStream
from .exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    HomeAssistantError,
    NotFoundError,
    ResponseError,
    TransportError,
    WebSocketAuthenticationError,
    WebSocketCommandError,
)

__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConnectionConfig",
    "EventStream",
    "HomeAssistant",
    "HomeAssistantError",
    "NotFoundError",
    "ResponseError",
    "TransportError",
    "WebSocketAuthenticationError",
    "WebSocketCommandError",
]
