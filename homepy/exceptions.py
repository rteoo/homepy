"""Public exceptions for the Home Assistant REST client."""


class HomeAssistantError(Exception):
    """Base class for errors raised by homepy."""


class ConfigurationError(HomeAssistantError):
    """The connection configuration is invalid."""


class TransportError(HomeAssistantError):
    """The request could not be completed at the network transport layer."""


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
