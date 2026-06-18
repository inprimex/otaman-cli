# otaman-cli

The `otaman` command-line binary — local project management, remote bridge
operations, and interactive agent launching from a single entry point.

## What this repo owns

- **The `otaman` binary** — single entry point for all local and remote
  operations.
- **Local ops** — `init`, `scan`, `validate`, `migrate`, `doctor`: work
  entirely from the local filesystem, no bridge required.
- **Remote ops** — `status`, `afk`, `complete`, `bus tail`, `audit query`:
  thin HTTP clients that talk to the bridge API; fail gracefully if no
  bridge is reachable.
- **Bus ops** — `check`, `ack`, `send`, `assign`, `propose`, `complete`:
  read/write the message bus; work locally via file bus or remotely via
  bridge.
- **Interactive launch** — `otaman launch <agent>`: sends a `POST /spawn`
  to the local runner daemon.
- **Account management** — local credential store, profile switching.

## Repository layout

| Directory | Contents |
|---|---|
| `src/otaman_cli/` | Package source — sub-command implementations, identity resolution, secret-source chain. |
| `tests/` | Pytest suite covering the public sub-commands and helpers. |
| `cli/` | Legacy shell-wrapper entry-points kept for backwards-compatible launchers. |
| `archive/` | Internal-only material kept in-tree for project history; not part of the public surface. See `archive/README.md`. |

## Dependencies

- Python 3.11+
- `uv` (workspace package manager)
- `otaman-core` (shared protocols, path resolution, secret-source chain)
- `otaman-bridge` reachable at runtime for remote sub-commands (optional —
  local sub-commands work without it)
- `otaman-runner` reachable at runtime for `otaman launch` (optional)

## Quick start (development)

```bash
# Install with dev + test extras
uv sync --package otaman-cli --extra test

# Run the test suite
uv run --package otaman-cli pytest

# Try the binary directly
uv run --package otaman-cli otaman --help

# Run doctor against your local workspace
uv run --package otaman-cli otaman doctor
```

## See also

- [docs.otaman.ai](https://docs.otaman.ai) — platform documentation
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute
- [`SECURITY.md`](SECURITY.md) — reporting security issues

## License

AGPL-3.0 (community edition). Commercial license available for teams that
cannot ship source — see [docs.otaman.ai](https://docs.otaman.ai).
