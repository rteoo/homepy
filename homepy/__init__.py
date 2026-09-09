"""Home Assistant REST client."""

from .client import HomeAssistant
from .config import ConnectionConfig
from .exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    HomeAssistantError,
    NotFoundError,
    ResponseError,
    TransportError,
)

__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConnectionConfig",
    "HomeAssistant",
    "HomeAssistantError",
    "NotFoundError",
    "ResponseError",
    "TransportError",
]
