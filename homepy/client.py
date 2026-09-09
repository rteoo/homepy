"""Synchronous client for Home Assistant's REST API."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Self
from urllib.parse import quote

from .config import ConnectionConfig
from .transport import Transport


JSONValue = Any
Timestamp = datetime | str


def _boolean(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _segment(value: str, name: str) -> str:
    """Encode one URL path segment after rejecting traversal markers."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    path_parts = value.replace("\\", "/").split("/")
    if any(part in {".", ".."} for part in path_parts) or "\\" in value or "\x00" in value:
        raise ValueError(f"{name} contains an unsafe path segment")
    return quote(value, safe="")


def _timestamp(value: Timestamp, name: str) -> str:
    """Return an ISO timestamp, requiring an explicit timezone."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a datetime or ISO timestamp")
    parsed = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed_dt = datetime.fromisoformat(parsed)
    except ValueError:
        raise ValueError(f"{name} must be a valid ISO timestamp") from None
    if parsed_dt.tzinfo is None or parsed_dt.utcoffset() is None:
        raise ValueError(f"{name} must include an explicit timezone")
    return value


def _params_timestamp(params: dict[str, Any], key: str, value: Timestamp | None) -> None:
    if value is not None:
        params[key] = _timestamp(value, key)


class HomeAssistant:
    """A small, synchronous Home Assistant REST API client."""

    def __init__(
        self,
        token: str,
        host: str = "homeassistant.local",
        *,
        port: int | None = None,
        timeout: float = 10.0,
        verify_ssl: bool = True,
        ca_file: str | None = None,
    ) -> None:
        self.config = ConnectionConfig(
            token,
            host,
            port=port,
            timeout=timeout,
            verify_ssl=verify_ssl,
            ca_file=ca_file,
        )
        self._transport = Transport(self.config)

    @classmethod
    def from_env(cls) -> Self:
        """Create a client from ``ConnectionConfig.from_env()``."""
        instance = cls.__new__(cls)
        instance.config = ConnectionConfig.from_env()
        instance._transport = Transport(instance.config)
        return instance

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Any = None,
        response_type: str = "json",
    ) -> JSONValue:
        return self._transport.request(
            method,
            path,
            params=params,
            data=data,
            response_type=response_type,
        )

    def health(self) -> dict[str, Any]:
        """Return the API health message."""
        return self._request("GET", "")

    def get_config(self) -> dict[str, Any]:
        """Return Home Assistant's current configuration."""
        return self._request("GET", "config")

    def get_components(self) -> list[Any]:
        """Return the loaded integration components."""
        return self._request("GET", "components")

    def get_states(self, *, domain: str | None = None) -> list[dict[str, Any]]:
        """Return states, optionally filtering by the entity domain locally."""
        if domain is not None and (not isinstance(domain, str) or not domain):
            raise ValueError("domain must be a non-empty string")
        states = self._request("GET", "states")
        if domain is None:
            return states
        prefix = f"{domain}."
        return [state for state in states if state.get("entity_id", "").startswith(prefix)]

    def get_state(self, entity_id: str) -> dict[str, Any]:
        """Return one entity state."""
        return self._request("GET", f"states/{_segment(entity_id, 'entity_id')}")

    def set_state(
        self,
        entity_id: str,
        state: Any,
        attributes: Mapping[str, Any] | None = None,
        force_update: bool = False,
    ) -> dict[str, Any]:
        """Set Home Assistant's representation of an entity state."""
        _boolean(force_update, "force_update")
        data: dict[str, Any] = {"state": state}
        if attributes is not None:
            data["attributes"] = dict(attributes)
        if force_update:
            data["force_update"] = True
        return self._request("POST", f"states/{_segment(entity_id, 'entity_id')}", data=data)

    def delete_state(self, entity_id: str) -> dict[str, Any]:
        """Delete an entity state representation."""
        return self._request("DELETE", f"states/{_segment(entity_id, 'entity_id')}")

    def get_events(self) -> list[dict[str, Any]]:
        """Return event listeners."""
        return self._request("GET", "events")

    def fire_event(
        self, event_type: str, event_data: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fire an event with optional event data."""
        data = None if event_data is None else dict(event_data)
        return self._request("POST", f"events/{_segment(event_type, 'event_type')}", data=data)

    def get_services(self) -> list[dict[str, Any]]:
        """Return registered services."""
        return self._request("GET", "services")

    def call_service(
        self,
        domain: str,
        service: str,
        service_data: Mapping[str, Any] | None = None,
        *,
        target: Mapping[str, Any] | None = None,
        return_response: bool = False,
    ) -> JSONValue:
        """Call a service, flattening target selectors into its JSON body."""
        _boolean(return_response, "return_response")
        data: dict[str, Any] = {} if service_data is None else dict(service_data)
        if target is not None:
            allowed = {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
            unknown = set(target) - allowed
            if unknown:
                raise ValueError("unsupported target selector")
            collisions = set(data).intersection(target)
            if collisions:
                raise ValueError("target conflicts with service data")
            data.update(target)
        params = {"return_response": ""} if return_response else None
        path = f"services/{_segment(domain, 'domain')}/{_segment(service, 'service')}"
        return self._request("POST", path, params=params, data=data)

    def get_history(
        self,
        entity_ids: str | Iterable[str],
        *,
        start: Timestamp | None = None,
        end: Timestamp | None = None,
        minimal_response: bool = False,
        no_attributes: bool = False,
        significant_changes_only: bool = False,
    ) -> list[Any]:
        """Return historical changes for one or more required entity IDs."""
        if isinstance(entity_ids, str):
            ids = [entity_ids]
        else:
            ids = list(entity_ids)
        if not ids or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("entity_ids must contain at least one non-empty ID")
        _boolean(minimal_response, "minimal_response")
        _boolean(no_attributes, "no_attributes")
        _boolean(significant_changes_only, "significant_changes_only")
        params: dict[str, Any] = {"filter_entity_id": ",".join(ids)}
        _params_timestamp(params, "end_time", end)
        for key, enabled in (
            ("minimal_response", minimal_response),
            ("no_attributes", no_attributes),
            ("significant_changes_only", significant_changes_only),
        ):
            if enabled:
                params[key] = ""
        path = "history/period"
        if start is not None:
            path += f"/{quote(_timestamp(start, 'start'), safe='')}"
        return self._request("GET", path, params=params)

    def get_logbook(
        self,
        start: Timestamp | None = None,
        end: Timestamp | None = None,
        *,
        entity_id: str | None = None,
    ) -> list[Any]:
        """Return logbook entries for an optional time range and entity."""
        params: dict[str, Any] = {}
        _params_timestamp(params, "end_time", end)
        if entity_id is not None:
            params["entity"] = entity_id
        path = "logbook"
        if start is not None:
            path += f"/{quote(_timestamp(start, 'start'), safe='')}"
        return self._request("GET", path, params=params or None)

    def get_error_log(self) -> str:
        """Return the current session's error log as text."""
        return self._request("GET", "error_log", response_type="text")

    def get_camera_image(self, entity_id: str) -> bytes:
        """Return a camera snapshot as bytes."""
        return self._request(
            "GET", f"camera_proxy/{_segment(entity_id, 'entity_id')}", response_type="bytes"
        )

    def get_calendars(self) -> list[dict[str, Any]]:
        """Return calendar entities."""
        return self._request("GET", "calendars")

    def get_calendar_events(
        self, entity_id: str, start: Timestamp, end: Timestamp
    ) -> list[Any]:
        """Return calendar events in the exclusive start/end interval."""
        params = {"start": _timestamp(start, "start"), "end": _timestamp(end, "end")}
        return self._request(
            "GET", f"calendars/{_segment(entity_id, 'entity_id')}", params=params
        )

    def render_template(
        self, template: str, variables: Mapping[str, Any] | None = None
    ) -> str:
        """Render a Home Assistant template as plain text."""
        data: dict[str, Any] = {"template": template}
        if variables is not None:
            data["variables"] = dict(variables)
        return self._request("POST", "template", data=data, response_type="text")

    def check_config(self) -> dict[str, Any]:
        """Validate Home Assistant's configuration."""
        return self._request("POST", "config/core/check_config")

    def handle_intent(
        self,
        name: str | Mapping[str, Any],
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle an intent by name, or forward a complete intent object."""
        if isinstance(name, Mapping):
            if data is not None:
                raise ValueError("data cannot be supplied with a complete intent object")
            payload = dict(name)
        else:
            if not isinstance(name, str) or not name:
                raise ValueError("name must be a non-empty string")
            payload = {"name": name}
            if data is not None:
                payload["data"] = dict(data)
        return self._request("POST", "intent/handle", data=payload)


__all__ = ["HomeAssistant"]
