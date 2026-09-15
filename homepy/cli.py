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
        command_code: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.category = category
        self.status_code = status_code
        self.hint = hint
        self.command_code = command_code
        super().__init__(message)

    @classmethod
    def from_details(cls, details: Mapping[str, str | int]) -> "CLIError":
        category = details.get("category")
        status_code = details.get("status_code")
        hint = details.get("hint")
        command_code = details.get("command_code")
        return cls(
            str(details["code"]),
            str(details["message"]),
            category=category if isinstance(category, str) else None,
            status_code=status_code if isinstance(status_code, int) else None,
            hint=hint if isinstance(hint, str) else None,
            command_code=command_code if isinstance(command_code, str) else None,
        )

    def to_details(self) -> dict[str, str | int]:
        details: dict[str, str | int] = {"code": self.code, "message": self.message}
        if self.category is not None:
            details["category"] = self.category
        if self.status_code is not None:
            details["status_code"] = self.status_code
        if self.hint is not None:
            details["hint"] = self.hint
        if self.command_code is not None:
            details["command_code"] = self.command_code
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


def _add_agent_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include-discovery", action="store_true", help="Include registry discovery tools")
    parser.add_argument("--include-events", action="store_true", help="Include bounded event collection tools")
    parser.add_argument("--allow-conversation", action="store_true", help="Enable the conversation tool")


def _add_event_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event-type", required=True, help="Event type to observe")
    parser.add_argument("--max-events", type=int, default=100, help="Maximum events to emit (default: 100)")
    parser.add_argument("--duration", type=float, default=30.0, help="Observation duration in seconds (default: 30)")


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

    areas = subparsers.add_parser("areas", help="List Home Assistant areas")
    _add_connection_options(areas, suppressed=True)

    devices = subparsers.add_parser("devices", help="List Home Assistant devices")
    _add_connection_options(devices, suppressed=True)

    registry = subparsers.add_parser("entity-registry", help="List registered Home Assistant entities")
    _add_connection_options(registry, suppressed=True)

    watch = subparsers.add_parser("watch", help="Observe a bounded event stream as NDJSON")
    _add_connection_options(watch, suppressed=True)
    _add_event_options(watch)

    conversation = subparsers.add_parser("conversation", help="Submit text to Home Assistant Conversation")
    _add_connection_options(conversation, suppressed=True)
    conversation.add_argument("--text", required=True, help="Conversation text")
    conversation.add_argument("--language", help="Input language")
    conversation.add_argument("--conversation-id", help="Continue an existing conversation")
    _add_action_options(conversation)
    conversation.add_argument("--allow-conversation", action="store_true", help="Enable conversation requests")

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
    _add_agent_options(tools)

    tool = subparsers.add_parser("tool", help="Dispatch one framework-neutral JSON tool")
    _add_connection_options(tool, suppressed=True)
    tool.add_argument("name", help="Tool name")
    tool.add_argument("--arguments", required=True, help="Tool arguments as a JSON object")
    _add_action_options(tool)
    _add_agent_options(tool)

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
    include_discovery: bool = False,
    include_events: bool = False,
    allow_conversation: bool = False,
) -> AgentTools:
    try:
        return AgentTools(
            client,
            allow_actions=allow_actions,
            allowed_services=allowed_services,
            include_discovery=include_discovery,
            include_events=include_events,
            allow_conversation=allow_conversation,
        )
    except TypeError:
        raise CLIError("invalid_arguments", "Invalid service policy") from None


def _run(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    client_factory: Callable[..., Any],
    *,
    stdout: TextIO | None = None,
) -> Any:
    # Tool schemas are static and safe to inspect without credentials or a
    # network client.  This makes discovery usable by shell agents before they
    # have an HA connection configured.
    if args.command == "tools":
        _validate_agent_policy(args)
        return AgentTools(
            object(),
            allow_actions=args.allow_actions,
            include_discovery=args.include_discovery,
            include_events=args.include_events,
            allow_conversation=args.allow_conversation,
        ).tool_definitions()

    if args.command == "watch":
        _validate_event_limits(args.max_events, args.duration)
        return _run_watch(args, environ, client_factory, stdout=stdout)

    if args.command in {"conversation", "tool"}:
        _validate_agent_policy(args)

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

    if args.command in {"areas", "devices", "entity-registry"}:
        name = {
            "areas": "ha_get_areas",
            "devices": "ha_get_devices",
            "entity-registry": "ha_get_entity_registry",
        }[args.command]
        return _new_agent_tools(client, include_discovery=True).dispatch(name, {})

    if args.command == "conversation":
        arguments: dict[str, Any] = {"text": args.text}
        if args.language is not None:
            arguments["language"] = args.language
        if args.conversation_id is not None:
            arguments["conversation_id"] = args.conversation_id
        return _new_agent_tools(
            client,
            allow_actions=args.allow_actions,
            allowed_services=args.allowed_service,
            allow_conversation=args.allow_conversation,
        ).dispatch("ha_process_conversation", arguments)

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
            include_discovery=args.include_discovery,
            include_events=args.include_events,
            allow_conversation=args.allow_conversation,
        ).dispatch(args.name, arguments)

    raise CLIError("invalid_arguments", "Unknown command")


def _validate_agent_policy(args: argparse.Namespace) -> None:
    """Reject conversational policy conflicts before constructing a client."""

    if getattr(args, "allow_conversation", False) and (
        not getattr(args, "allow_actions", False)
        or getattr(args, "allowed_service", None) is not None
    ):
        raise CLIError(
            "conversation_policy",
            "Conversation requires --allow-actions and no --allowed-service",
        )


def _validate_event_limits(max_events: Any, duration: Any) -> None:
    if type(max_events) is not int or max_events <= 0:
        raise CLIError("invalid_arguments", "--max-events must be a positive integer")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise CLIError("invalid_arguments", "--duration must be finite and greater than zero")
    if duration <= 0 or not math.isfinite(duration):
        raise CLIError("invalid_arguments", "--duration must be finite and greater than zero")


def _run_watch(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    client_factory: Callable[..., Any],
    *,
    stdout: TextIO | None = None,
) -> int:
    """Emit a bounded public event stream as flushed JSON lines."""

    client = _make_client(args, environ, client_factory)
    out = stdout if stdout is not None else sys.stdout
    stream = None
    try:
        stream = client.watch_events(
            args.event_type, max_events=args.max_events, duration=args.duration
        )
        with stream as active:
            for event in active:
                try:
                    _write_json(out, event)
                    out.flush()
                except OSError:
                    # Closed pipes surface as EPIPE on POSIX and may be EINVAL
                    # on Windows. Prevent Python's exit-time flush from failing
                    # again; the context manager still releases the subscription.
                    if out is sys.stdout:
                        try:
                            with open(os.devnull, "wb") as sink:
                                os.dup2(sink.fileno(), out.fileno())
                        except (OSError, ValueError):
                            pass
                    return 1
        return 0
    except KeyboardInterrupt:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        return 130
    except CLIError:
        raise
    except Exception as exc:
        raise CLIError.from_details(error_details(exc)) from None


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
        result = _run(args, env, factory, stdout=out)
        if args.command == "watch":
            return int(result)
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
