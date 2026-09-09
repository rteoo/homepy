"""Immutable and safe Home Assistant connection settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import ipaddress
import math
import os
from urllib.parse import unquote, urlsplit

from .exceptions import ConfigurationError


_DEFAULT_HOST = "homeassistant.local"
_SCHEMES = {"http", "https"}


def _invalid(detail: str) -> ConfigurationError:
    return ConfigurationError(f"invalid Home Assistant connection configuration: {detail}")


def _validate_port(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise _invalid("port must be an integer from 1 to 65535")
    return value


def _format_host(hostname: str) -> str:
    if not hostname:
        raise _invalid("host is empty")
    try:
        parsed = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    return f"[{parsed.compressed}]" if parsed.version == 6 else parsed.compressed


def _authority_url(value: str, explicit_port: int | None) -> tuple[str, str, int, str]:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("host must be a non-empty string")
    value = value.strip()
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _invalid("host contains control characters")
    has_scheme = "://" in value
    bare_ipv6 = False
    if not has_scheme and ":" in value and not value.startswith("["):
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            pass
        else:
            bare_ipv6 = True
    candidate = value if has_scheme else (f"http://[{value}]" if bare_ipv6 else f"http://{value}")
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        embedded_port = parts.port
    except ValueError:
        raise _invalid("host URL is malformed") from None
    if parts.scheme not in _SCHEMES or not hostname:
        raise _invalid("host must be a hostname or an http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise _invalid("credentials in host URLs are not allowed")
    if parts.query or parts.fragment:
        raise _invalid("query and fragment in host URLs are not allowed")
    if bare_ipv6:
        hostname, embedded_port = value, None
    port = explicit_port if explicit_port is not None else embedded_port
    if port is None:
        port = 8123 if not has_scheme else (443 if parts.scheme == "https" else 80)
    _validate_port(port)
    prefix = parts.path.rstrip("/")
    decoded_prefix = unquote(prefix)
    if "\\" in decoded_prefix or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded_prefix):
        raise _invalid("host path contains invalid characters")
    if decoded_prefix.startswith("/") and any(part in {".", ".."} for part in decoded_prefix.split("/")):
        raise _invalid("host path contains traversal")
    if prefix == "/api":
        prefix = ""
    elif prefix.endswith("/api"):
        prefix = prefix[: -len("/api")].rstrip("/")
    if ".." in prefix.split("/"):
        raise _invalid("host path contains traversal")
    return parts.scheme, hostname, port, prefix


@dataclass(frozen=True, repr=False)
class ConnectionConfig:
    """Validated immutable settings for a Home Assistant REST connection."""

    token: str = field(repr=False)
    host: str = _DEFAULT_HOST
    port: int | None = None
    timeout: float = 10.0
    verify_ssl: bool = True
    ca_file: str | None = None
    _scheme: str = field(init=False, repr=False)
    _hostname: str = field(init=False, repr=False)
    _effective_port: int = field(init=False, repr=False)
    _path_prefix: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token:
            raise _invalid("token must be a non-empty string")
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ord(ch) > 127 for ch in self.token):
            raise _invalid("token contains invalid header characters")
        if not isinstance(self.verify_ssl, bool):
            raise _invalid("verify_ssl must be a boolean")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise _invalid("timeout must be a finite positive number")
        if not math.isfinite(float(self.timeout)) or float(self.timeout) <= 0:
            raise _invalid("timeout must be a finite positive number")
        explicit_port = _validate_port(self.port)
        if self.ca_file is not None and not isinstance(self.ca_file, str):
            raise _invalid("ca_file must be a path string")
        scheme, hostname, effective_port, prefix = _authority_url(self.host, explicit_port)
        object.__setattr__(self, "timeout", float(self.timeout))
        object.__setattr__(self, "_scheme", scheme)
        object.__setattr__(self, "_hostname", hostname)
        object.__setattr__(self, "_effective_port", effective_port)
        object.__setattr__(self, "_path_prefix", prefix)

    @property
    def base_url(self) -> str:
        authority = f"{_format_host(self._hostname)}:{self._effective_port}"
        return f"{self._scheme}://{authority}{self._path_prefix}"

    @property
    def effective_port(self) -> int:
        return self._effective_port

    @property
    def path_prefix(self) -> str:
        return self._path_prefix

    def __repr__(self) -> str:
        return (
            "ConnectionConfig(token=<redacted>, "
            f"host={self.host!r}, port={self.port!r}, timeout={self.timeout!r}, "
            f"verify_ssl={self.verify_ssl!r}, ca_file={self.ca_file!r})"
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
    ) -> "ConnectionConfig":
        """Build configuration from an environment mapping and explicit overrides.

        Explicit ``host``, ``port``, and ``timeout`` values take precedence.
        Parsing of their corresponding environment values is skipped when an
        override is present, so an unrelated malformed environment variable
        cannot defeat a valid command-line setting.
        """

        source = os.environ if environ is None else environ
        token = source.get("HA_TOKEN")
        resolved_host = host if host is not None else source.get("HA_URL") or source.get("HA_HOST") or _DEFAULT_HOST

        if port is None:
            port_raw = source.get("HA_PORT")
            try:
                resolved_port = int(port_raw) if port_raw else None
            except (TypeError, ValueError):
                raise _invalid("HA_PORT must be an integer from 1 to 65535") from None
        else:
            resolved_port = port

        if timeout is None:
            timeout_raw = source.get("HA_TIMEOUT")
            try:
                resolved_timeout = float(timeout_raw) if timeout_raw else 10.0
            except (TypeError, ValueError):
                raise _invalid("HA_TIMEOUT must be a finite positive number") from None
        else:
            resolved_timeout = timeout

        if token is None:
            raise _invalid("HA_TOKEN is required")
        return cls(
            token,
            host=resolved_host,
            port=resolved_port,
            timeout=resolved_timeout,
            ca_file=source.get("HA_CA_FILE") or None,
        )
