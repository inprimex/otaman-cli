# otaman-cli

The `otaman` command-line binary — local project management, remote bridge operations, and interactive agent launching from a single entry point.

## Status

| Command group | Shipped | Roadmap |
|---|---|---|
| `otaman init` — scaffold .agents/ + CLAUDE.md files | shipped | — |
| `otaman scan` — detect repos, draft platform.yaml | shipped | — |
| `otaman validate` — lint platform.yaml + ownership map | shipped | — |
| `otaman migrate` — project-root → otaman folder layout | shipped | — |
| `otaman doctor` — environment readiness check | shipped | — |
| `otaman check` / `otaman ack` — bus message ops | shipped | — |
| `otaman status` — project-wide summary (remote) | shipped | — |
| `otaman complete` — mark OpenSpec tasks done + broadcast | shipped | — |
| `otaman afk` — toggle remote-approval AFK mode | shipped | — |
| `otaman bus tail` — stream bus events | shipped | — |
| `otaman audit query` — query audit log (remote) | shipped | — |
| `otaman launch <agent>` — spawn session via runner | shipped | runner HTTP client (ADR-009) |
| Account / profile management | shipped | multi-account OIDC (Step 4) |
| `otaman assign` — send task-assignment bus message | shipped | — |
| `otaman propose` — create spec-change-request | shipped | — |
| Interactive TUI | — | Step 3 |

## What this repo owns

- **The `otaman` binary** — single entry point for all local and remote operations.
- **Local ops** — `init`, `scan`, `validate`, `migrate`, `doctor`: work entirely from the local filesystem, no bridge required.
- **Remote ops** — `status`, `afk`, `complete`, `bus tail`, `audit query`: thin HTTP clients that talk to the bridge API; fail gracefully if no bridge is reachable.
- **Bus ops** — `check`, `ack`, `send`, `assign`, `propose`, `complete`: read/write the message bus; work locally via file bus or remotely via bridge.
- **Interactive launch** — `otaman launch <agent>`: sends a `POST /spawn` to the local runner daemon (ADR-009).
- **Account management** — local credential store, profile switching, future OIDC token refresh.

## Dependencies

- Python 3.11+
- `uv` (workspace package manager)
- `otaman-core` (shared protocols, path resolution, secret-source chain)
- `otaman-bridge` reachable at runtime for remote subcommands (optional — local subcommands work without it)
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

- [ADR-009 (unified spawner)](https://github.com/inprimex/otaman-meta/blob/main/adrs/ADR-009-unified-spawner.md) — launch subcommand design
- [polyrepo-structure.md](https://github.com/inprimex/otaman-meta/blob/main/polyrepo-structure.md) — ownership map
- [phased-roadmap.md](https://github.com/inprimex/otaman-meta/blob/main/phased-roadmap.md) — Step 1–7 sequencing
- [otaman.dev](https://otaman.dev) — platform docs

## License

AGPL-3.0 (community edition). Commercial license available for teams that cannot ship source — see [otaman.dev](https://otaman.dev).
