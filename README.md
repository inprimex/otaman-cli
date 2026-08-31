# otaman-cli

> **Otaman platform:** [otaman-core](https://github.com/inprimex/otaman-core) · **otaman-cli (you are here)** · [otaman-plugin](https://github.com/inprimex/otaman-plugin) · [otaman-bridge](https://github.com/inprimex/otaman-bridge) · [otaman-runner](https://github.com/inprimex/otaman-runner) · [otaman-adapters](https://github.com/inprimex/otaman-adapters)

The `otaman` command-line binary — local project management, remote bridge
operations, and interactive agent launching from a single entry point.

## What this repo owns

- **The `otaman` binary** — single entry point for all local and remote
  operations.
- **Local ops** — `init`, `scan`, `validate`, `migrate`, `doctor`: work
  entirely from the local filesystem, no bridge required.
- **Remote ops** — `status`, `afk`, `complete`: thin HTTP clients that talk
  to the bridge API; fail gracefully if no bridge is reachable.
- **Bus ops** — `check`, `ack`, `send`, `assign`, `propose`, `complete`:
  read/write the message bus; work locally via file bus or remotely via
  bridge.
- **Interactive launch** — `otaman session spawn`: spawns a Claude session
  under the logged-in user's identity via a `POST /spawn` to the local
  runner daemon.
- **Account management** — local credential store, profile switching.

## Repository layout

| Directory | Contents |
|---|---|
| `src/otaman_cli/` | Package source — sub-command implementations, identity resolution, secret-source chain. |
| `tests/` | Pytest suite covering the public sub-commands and helpers. |
| `cli/` | Legacy shell-wrapper entry-points kept for backwards-compatible launchers. |

## Dependencies

- Python 3.10+
- `uv` (workspace package manager)
- `otaman-core` (shared protocols, path resolution, secret-source chain)
- `otaman-bridge` reachable at runtime for remote sub-commands (optional —
  local sub-commands work without it)
- `otaman-runner` reachable at runtime for `otaman session spawn` (optional)

## Quick start (development)

Dependencies resolve against sibling checkouts of the other Otaman packages —
see [`.github/workflows/test.yml`](.github/workflows/test.yml) for the exact
workspace shape CI uses.

```bash
# Install with test extras
uv sync --extra test

# Run the test suite
uv run pytest

# Try the binary directly
uv run otaman --help

# Run doctor against your local workspace
uv run otaman doctor
```

## See also

- [docs.otaman.ai](https://docs.otaman.ai) — platform documentation
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute
- [`SECURITY.md`](SECURITY.md) — reporting security issues

## License

AGPL-3.0 (community edition). Commercial license available for teams that
cannot ship source — see [docs.otaman.ai](https://docs.otaman.ai).
