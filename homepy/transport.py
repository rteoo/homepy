"""Bounded, direct HTTP transport for Home Assistant's REST API."""

from __future__ import annotations

import http.client
import json
import ssl
from typing import Any, Mapping
from urllib.parse import unquote, urlencode

from .config import ConnectionConfig
from .exceptions import APIError, AuthenticationError, NotFoundError, ResponseError, TransportError


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
# ceiling: responses are capped at 16 MiB until streaming is added to the API.


class Transport:
    """Issue one direct HTTP request at a time, without retries or redirects."""

    def __init__(self, config: ConnectionConfig):
        if not isinstance(config, ConnectionConfig):
            raise TypeError("config must be a ConnectionConfig")
        self.config = config

    def _path(self, path: str) -> str:
        if not isinstance(path, str):
            raise ValueError("path must be a string")
        decoded = unquote(path)
        if (
            "?" in path
            or "#" in path
            or "?" in decoded
            or "#" in decoded
            or path.startswith("/")
            or "\\" in path
            or "\\" in decoded
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
        ):
            raise ValueError("path must be API-relative and cannot contain query or fragment")
        if path.startswith("\\") or "://" in path:
            raise ValueError("path must be API-relative")
        if decoded.startswith("/") or any(segment in {".", ".."} for segment in decoded.split("/")):
            raise ValueError("path traversal is not allowed")
        prefix = self.config.path_prefix
        return f"{prefix}/api/{path}" if path else f"{prefix}/api/"

    @staticmethod
    def _body(data: Any) -> tuple[bytes | str | None, str | None]:
        if data is None:
            return None, None
        if isinstance(data, (dict, list, tuple, int, float, bool)):
            try:
                return json.dumps(data, separators=(",", ":"), allow_nan=False), "application/json"
            except (TypeError, ValueError):
                raise ValueError("request data is not JSON serializable") from None
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data), "application/octet-stream"
        if isinstance(data, str):
            return data.encode("utf-8"), "text/plain; charset=utf-8"
        raise TypeError("request data must be JSON-compatible, text, or bytes")

    def _connection(self) -> http.client.HTTPConnection:
        # timeout: this is the socket connect/read timeout, not a whole-request deadline.
        kwargs = {"timeout": self.config.timeout}
        if self.config._scheme == "https":
            context = (
                ssl.create_default_context(cafile=self.config.ca_file)
                if self.config.verify_ssl
                else ssl._create_unverified_context()
            )
            return http.client.HTTPSConnection(self.config._hostname, self.config.effective_port, context=context, **kwargs)
        return http.client.HTTPConnection(self.config._hostname, self.config.effective_port, **kwargs)

    @staticmethod
    def _read_bounded(response: http.client.HTTPResponse) -> bytes:
        raw_length = response.getheader("Content-Length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                raise ResponseError("invalid response length") from None
            if length < 0 or length > MAX_RESPONSE_BYTES:
                raise ResponseError("response exceeds the maximum permitted size")
        chunks: list[bytes] = []
        total = 0
        try:
            if raw_length is not None:
                remaining = length
                while remaining:
                    chunk = response.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ResponseError("response was truncated")
                    chunks.append(chunk)
                    total += len(chunk)
                    remaining -= len(chunk)
            else:
                while True:
                    chunk = response.read(min(_CHUNK_SIZE, MAX_RESPONSE_BYTES - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ResponseError("response exceeds the maximum permitted size")
                    chunks.append(chunk)
        except http.client.IncompleteRead:
            raise ResponseError("response was truncated") from None
        return b"".join(chunks)

    def request(self, method: str, path: str, *, params: Mapping[str, Any] | None = None,
                data: Any = None, response_type: str = "json") -> Any:
        if not isinstance(method, str) or not method or any(ord(ch) < 33 or ord(ch) > 126 for ch in method):
            raise ValueError("method is invalid")
        method = method.upper()
        if response_type not in {"json", "text", "bytes"}:
            raise ValueError("response_type must be json, text, or bytes")
        request_path = self._path(path)
        if params is not None:
            if not isinstance(params, Mapping):
                raise TypeError("params must be a mapping")
            try:
                query = urlencode(params, doseq=True)
            except (TypeError, ValueError):
                raise ValueError("params are not URL-serializable") from None
            if query:
                request_path += "?" + query
        body, content_type = self._body(data)
        headers = {"Authorization": f"Bearer {self.config.token}", "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            connection = self._connection()
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            payload = self._read_bounded(response)
            status = response.status
        except ResponseError:
            raise
        except (OSError, ValueError, TimeoutError, ssl.SSLError, http.client.HTTPException):
            raise TransportError("Home Assistant request failed") from None
        finally:
            if "connection" in locals():
                try:
                    connection.close()
                except OSError:
                    pass
        if status in {401, 403}:
            raise AuthenticationError(status_code=status)
        if status == 404:
            raise NotFoundError(status_code=status)
        if status < 200 or status >= 300:
            raise APIError(f"Home Assistant returned HTTP status {status}", status_code=status)
        if response_type == "bytes":
            return payload
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise ResponseError("response was not valid UTF-8") from None
        if response_type == "text":
            return text
        try:
            return json.loads(text, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise ResponseError("response was not valid JSON") from None
