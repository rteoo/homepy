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

## Public repository privacy gate

This repository is public. Treat committed files, commit/tag messages and
identities, PR descriptions, CI logs, and release assets as permanent disclosures.

Before committing or publishing:

- Stage only explicit task-owned paths. Inspect the full staged diff and file
  list, including untracked additions and binary contents/metadata. Do not commit
  generated artifacts, installers, archives, diagnostic dumps, or backups merely
  because they were produced during the task.
- Never include credentials, tokens, cookies, private keys, signing material,
  `.env` contents, live settings, recordings/transcripts, clipboard/snippet data,
  personal emails, phones, addresses, CPF/CNPJ identifiers, household/device
  details, private network endpoints, confidential client data, or private
  product/roadmap details. Use synthetic fixtures and generic paths (`$HOME`,
  `%USERPROFILE%`); sanitize screenshots and examples. Preserve legitimate public
  license/copyright attribution. Documented synthetic test credentials are allowed
  only for their narrow fixture purpose; never broadly allowlist real secrets.
- Run the available secret scanner on staged content before committing and on
  every outgoing commit/ref before pushing. Manually review privacy data and
  metadata that scanners miss. If no scanner is available, disclose the gap and
  complete a documented manual review; never claim a scanner ran. Separately run
  `git diff --cached --check` for formatting.
- Set repository-local `user.email = rteoo@users.noreply.github.com` and
  `user.useConfigOnly = true`. Verify effective author/committer identities before
  each commit and tagger identity before an annotated tag, including environment
  and command-line overrides. New owner-authored metadata must use that noreply
  address. Preserve legitimate third-party contributor attribution.
- Before an authorized push, inspect the exact remote/refspecs and every outgoing
  commit/tag, message, and reachable history. Never merge or push a pre-redaction
  branch/tag that reintroduces private identities or data. `.gitignore`, noreply
  configuration, and a clean working tree do not prove tracked files or history
  safe. Preserve hooks, signing, secret-scanning push protection, and branch
  protections; never bypass them.
- If a leak is found, stop committing/publishing the affected material. Report
  only redacted categories and locations, never the sensitive value. Deleting a
  file later does not erase Git/PR/release history. Credential rotation, history
  rewrites, force pushes, ref deletions, and external cleanup need explicit
  authorization for exact targets. This policy grants no push, PR, release, or
  history-rewrite authorization.
