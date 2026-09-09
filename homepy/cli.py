"""Command-line interface for the Home Assistant client and agent tools."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable, TextIO

from .agent import AgentToolError, AgentTools
from .exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    HomeAssistantError,
    NotFoundError,
    ResponseError,
    TransportError,
)


class CLIError(Exception):
    """A sanitized command-line error suitable for JSON stderr output."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's full usage text is useful for --help, but errors should
        # follow the CLI's stable JSON stderr contract and reveal no internals.
        raise CLIError("invalid_arguments", "Invalid command-line arguments")


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _add_connection_options(parser: argparse.ArgumentParser, *, suppressed: bool = False) -> None:
    default = argparse.SUPPRESS if suppressed else None
    parser.add_argument("--host", default=default, help="Home Assistant host or URL (default: homeassistant.local)")
    parser.add_argument("--port", type=int, default=default, help="Home Assistant port")
    parser.add_argument("--timeout", type=float, default=default, help="HTTP timeout in seconds (default: 10)")


def _add_action_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allow-actions", action="store_true", help="Enable service calls for this invocation")
    parser.add_argument(
        "--allowed-service",
        action="append",
        default=None,
        metavar="DOMAIN.SERVICE",
        help="Permit only this exact service (repeatable; empty means deny all)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m homepy",
        description="Read Home Assistant state or explicitly call an agent tool.",
    )
    _add_connection_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    health = subparsers.add_parser("health", help="Check Home Assistant API health")
    _add_connection_options(health, suppressed=True)

    states = subparsers.add_parser("states", help="Get entity states")
    _add_connection_options(states, suppressed=True)
    states.add_argument("--domain", help="Filter states by domain")

    state = subparsers.add_parser("state", help="Get one entity state")
    _add_connection_options(state, suppressed=True)
    state.add_argument("entity_id", help="Entity ID, such as light.desk")

    services = subparsers.add_parser("services", help="List available services")
    _add_connection_options(services, suppressed=True)

    call = subparsers.add_parser("call", help="Call a Home Assistant service (actions are disabled by default)")
    _add_connection_options(call, suppressed=True)
    call.add_argument("domain", help="Service domain, such as light")
    call.add_argument("service", help="Service name, such as turn_on")
    call.add_argument("--data", help="Service data as a JSON object")
    call.add_argument("--target", help="Target selectors as a JSON object")
    call.add_argument("--return-response", action="store_true", help="Request the service response")
    _add_action_options(call)

    tools = subparsers.add_parser("tools", help="List framework-neutral JSON tool definitions")
    _add_connection_options(tools, suppressed=True)
    tools.add_argument("--allow-actions", action="store_true", help="Include the service-call tool")

    tool = subparsers.add_parser("tool", help="Dispatch one framework-neutral JSON tool")
    _add_connection_options(tool, suppressed=True)
    tool.add_argument("name", help="Tool name")
    tool.add_argument("--arguments", required=True, help="Tool arguments as a JSON object")
    _add_action_options(tool)

    return parser


def _parse_json_object(raw: str | None, option: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CLIError("invalid_json", f"{option} must be valid JSON") from None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CLIError("invalid_json", f"{option} must be a JSON object")
    return value


def _default_client_factory(
    token: str, *, host: str, port: int | None, timeout: float, ca_file: str | None = None
) -> Any:
    try:
        from .client import HomeAssistant
    except Exception:
        raise CLIError("client_error", "Home Assistant client is unavailable") from None
    try:
        return HomeAssistant(token, host=host, port=port, timeout=timeout, ca_file=ca_file)
    except Exception:
        raise CLIError("client_error", "Could not configure Home Assistant client") from None


def _make_client(args: argparse.Namespace, environ: Mapping[str, str], client_factory: Callable[..., Any]) -> Any:
    token = environ.get("HA_TOKEN")
    if not isinstance(token, str) or not token:
        raise CLIError("missing_token", "HA_TOKEN environment variable is required")
    host = args.host if args.host is not None else environ.get("HA_URL") or environ.get("HA_HOST") or "homeassistant.local"
    port = args.port
    if port is None:
        port_raw = environ.get("HA_PORT")
        if port_raw:
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                raise CLIError("invalid_arguments", "HA_PORT must be an integer") from None
    timeout = args.timeout
    if timeout is None:
        timeout_raw = environ.get("HA_TIMEOUT")
        if timeout_raw:
            try:
                timeout = float(timeout_raw)
            except (TypeError, ValueError):
                raise CLIError("invalid_arguments", "HA_TIMEOUT must be a number") from None
        else:
            timeout = 10.0
    if not math.isfinite(timeout) or timeout <= 0:
        raise CLIError("invalid_arguments", "Timeout must be greater than zero")
    ca_file = environ.get("HA_CA_FILE") or None
    factory_kwargs = {"host": host, "port": port, "timeout": timeout}
    if ca_file is not None:
        factory_kwargs["ca_file"] = ca_file
    try:
        return client_factory(token, **factory_kwargs)
    except CLIError:
        raise
    except Exception:
        raise CLIError("client_error", "Could not configure Home Assistant client") from None


def _run(args: argparse.Namespace, environ: Mapping[str, str], client_factory: Callable[..., Any]) -> Any:
    # Tool schemas are static and safe to inspect without credentials or a
    # network client.  This makes discovery usable by shell agents before they
    # have an HA connection configured.
    if args.command == "tools":
        return AgentTools(object(), allow_actions=args.allow_actions).tool_definitions()

    client = _make_client(args, environ, client_factory)

    if args.command == "health":
        return _safe_client_call(client, "health")

    if args.command == "states":
        return AgentTools(client).dispatch("ha_get_states", {"domain": args.domain} if args.domain is not None else {})

    if args.command == "state":
        return AgentTools(client).dispatch("ha_get_state", {"entity_id": args.entity_id})

    if args.command == "services":
        return AgentTools(client).dispatch("ha_get_services", {})

    if args.command == "call":
        arguments: dict[str, Any] = {"domain": args.domain, "service": args.service}
        data = _parse_json_object(args.data, "--data")
        target = _parse_json_object(args.target, "--target")
        if data is not None:
            arguments["service_data"] = data
        if target is not None:
            arguments["target"] = target
        if args.return_response:
            arguments["return_response"] = True
        return AgentTools(
            client,
            allow_actions=args.allow_actions,
            allowed_services=args.allowed_service,
        ).dispatch("ha_call_service", arguments)

    if args.command == "tool":
        arguments = _parse_json_object(args.arguments, "--arguments")
        # --arguments is required by argparse, so None indicates an impossible
        # internal state rather than a user input case.
        if arguments is None:
            raise CLIError("invalid_arguments", "--arguments is required")
        return AgentTools(
            client,
            allow_actions=args.allow_actions,
            allowed_services=args.allowed_service,
        ).dispatch(args.name, arguments)

    raise CLIError("invalid_arguments", "Unknown command")


def _safe_client_call(client: Any, method_name: str) -> Any:
    if method_name != "health":
        raise CLIError("client_error", "Home Assistant client is unavailable")
    try:
        value = client.health()
        # Keep stdout valid JSON and reject non-JSON values without exposing
        # serializer or client exception details.
        return json.loads(json.dumps(value, allow_nan=False))
    except Exception as exc:
        if isinstance(exc, AuthenticationError):
            raise CLIError("authentication_error", "Home Assistant authentication failed") from None
        if isinstance(exc, NotFoundError):
            raise CLIError("not_found", "Home Assistant resource was not found") from None
        if isinstance(exc, APIError):
            raise CLIError("api_error", "Home Assistant rejected the request") from None
        if isinstance(exc, ResponseError):
            raise CLIError("response_error", "Home Assistant returned an invalid response") from None
        if isinstance(exc, ConfigurationError):
            raise CLIError("configuration_error", "Home Assistant configuration failed") from None
        if isinstance(exc, TransportError):
            raise CLIError("transport_error", "Home Assistant transport failed") from None
        if isinstance(exc, HomeAssistantError):
            raise CLIError("home_assistant_error", "Home Assistant request failed") from None
        raise CLIError("request_failed", "Home Assistant request failed") from None


def _write_json(stream: TextIO, value: Any) -> None:
    try:
        # ASCII escaping keeps redirected Windows streams safe even when their
        # code page cannot encode an entity name or response emoji.
        stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
        stream.write("\n")
    except (TypeError, ValueError, OverflowError):
        raise CLIError("invalid_response", "Command returned unsupported data") from None


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process-style status code.

    ``environ`` and ``client_factory`` are injectable solely for deterministic
    tests; production invocation reads ``HA_TOKEN`` and constructs the real
    client.
    """

    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env = environ if environ is not None else os.environ
    factory = client_factory if client_factory is not None else _default_client_factory
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        result = _run(args, env, factory)
        _write_json(out, result)
        return 0
    except (CLIError, AgentToolError) as exc:
        _write_json(err, {"error": {"code": exc.code, "message": exc.message}})
        return 2
    except TypeError:
        _write_json(err, {"error": {"code": "invalid_arguments", "message": "Invalid command-line arguments"}})
        return 2
    except Exception:
        # The CLI is an agent boundary: never print a traceback or exception
        # detail that might carry a token, URL, request body, or host data.
        _write_json(err, {"error": {"code": "internal_error", "message": "Command failed"}})
        return 1


__all__ = ["CLIError", "build_parser", "main"]
