# Homepy

Homepy is a Python 3.11+ synchronous wrapper for Home Assistant's REST API.
Runtime code and tests use the standard library. Source lives in `homepy/`;
tests live in `tests/`. See `README.md` for public usage and `PLAN.md` for scope.

## Architecture

- `config.py`: immutable connection settings, environment parsing, URL defaults.
- `transport.py`: one HTTP connection per request, bounded responses, TLS and errors.
- `client.py`: endpoint paths, payloads, and query serialization.
- `agent.py`: JSON function descriptors and service capability policy.
- `cli.py`: JSON stdout/stderr boundary and environment/argument precedence.

Bare hosts default to HTTP port 8123. Full URLs retain their scheme port unless
overridden. Tailscale is ordinary host routing supplied by the deployment.
Service calls control devices; state writes only modify Home Assistant's state
representation. Preserve that distinction in API names and examples.

## Verified local commands

Run from the repository root in PowerShell:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q homepy tests
python -m homepy --help
```

The current host has Python 3.14.6. Tests mock Home Assistant or start loopback
HTTP servers; they require no real token or household instance. Keep tests
independent of ambient `HA_*` settings. Live device control is outside the test
suite. There is no established push, deployment, or autosync workflow.

## Change checks

For endpoint changes, verify the current Home Assistant REST docs and preserve
unknown response fields. Cover wire paths, payloads, query flags, failure behavior,
and CLI/agent parity as appropriate. Never retry mutations automatically.
Retain safe exception messages, credential redaction, disabled redirects, and
the distinction between no service allowlist and an empty allowlist.
