"""Command-line interface for the Home Assistant client and agent tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable, TextIO

from .agent import AgentToolError, AgentTools
from .config import ConnectionConfig
from .exceptions import error_details


class CLIError(Exception):
    """A sanitized command-line error suitable for JSON stderr output."""

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

    @classmethod
    def from_details(cls, details: Mapping[str, str | int]) -> "CLIError":
        category = details.get("category")
        status_code = details.get("status_code")
        hint = details.get("hint")
        return cls(
            str(details["code"]),
            str(details["message"]),
            category=category if isinstance(category, str) else None,
            status_code=status_code if isinstance(status_code, int) else None,
            hint=hint if isinstance(hint, str) else None,
        )

    def to_details(self) -> dict[str, str | int]:
        details: dict[str, str | int] = {"code": self.code, "message": self.message}
        if self.category is not None:
            details["category"] = self.category
        if self.status_code is not None:
            details["status_code"] = self.status_code
        if self.hint is not None:
            details["hint"] = self.hint
        return details


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
        help="Permit only this exact service (repeatable; omission is unrestricted only with --allow-actions)",
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
    except Exception as exc:
        raise CLIError.from_details(error_details(exc)) from None


def _make_client(args: argparse.Namespace, environ: Mapping[str, str], client_factory: Callable[..., Any]) -> Any:
    if not environ.get("HA_TOKEN"):
        raise CLIError("missing_token", "HA_TOKEN environment variable is required")
    try:
        config = ConnectionConfig.from_env(
            environ,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
        )
    except Exception as exc:
        raise CLIError.from_details(error_details(exc)) from None
    factory_kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "timeout": config.timeout,
    }
    if config.ca_file is not None:
        factory_kwargs["ca_file"] = config.ca_file
    return client_factory(config.token, **factory_kwargs)


def _new_agent_tools(
    client: Any,
    *,
    allow_actions: bool = False,
    allowed_services: list[str] | None = None,
) -> AgentTools:
    try:
        return AgentTools(
            client,
            allow_actions=allow_actions,
            allowed_services=allowed_services,
        )
    except TypeError:
        raise CLIError("invalid_arguments", "Invalid service policy") from None


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
        return _new_agent_tools(client).dispatch(
            "ha_get_states", {"domain": args.domain} if args.domain is not None else {}
        )

    if args.command == "state":
        return _new_agent_tools(client).dispatch("ha_get_state", {"entity_id": args.entity_id})

    if args.command == "services":
        return _new_agent_tools(client).dispatch("ha_get_services", {})

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
        return _new_agent_tools(
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
        return _new_agent_tools(
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
    except Exception as exc:
        raise CLIError.from_details(error_details(exc)) from None
    try:
        # Keep stdout valid JSON and reject non-JSON values without exposing
        # serializer or client exception details.
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise CLIError("invalid_response", "Command returned unsupported data") from None


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
        _write_json(err, {"error": exc.to_details()})
        return 2
    except Exception:
        # The CLI is an agent boundary: never print a traceback or exception
        # detail that might carry a token, URL, request body, or host data.
        _write_json(err, {"error": {"code": "internal_error", "message": "Command failed"}})
        return 1


__all__ = ["CLIError", "build_parser", "main"]
