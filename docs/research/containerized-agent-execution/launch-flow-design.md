# `otaman launch` — Launch Flow Design

> **Author**: cli-agent  
> **Date**: 2026-05-27  
> **Tasks**: containerized-agent-execution 3.1 + 3.2 + 3.3 + 3.4  
> **Output**: Design of `otaman launch <org> <agent>`, pluggable execution backends,
>             `launch-settings.yaml` per-Org schema extension, backend precedence,
>             and docker exec TTY handling.

---

## Background

`otaman launch` is the developer ergonomics command for attaching to an agent session
running inside an Organisation container. The container is already running (started by
`docker compose up -d` from `~/orgs/<slug>/config/compose.yml`). The CLI's job is to
exec into it with the right TTY settings and environment.

Three pluggable execution backends cover the main deployment topologies:
1. **local** — same machine; `docker exec -it`
2. **ssh** — remote Linux host; `ssh` then `docker exec -it`
3. **remote-socket** — Docker daemon on a remote host, exposed via TCP or TLS socket

---

## Command surface

```
otaman launch <org> <agent> [options]

positional arguments:
  org     Organisation slug (e.g. "acme-corp")
  agent   Agent name (e.g. "cli-agent", "core-agent")

options:
  --backend local|ssh|remote-socket
            Execution backend override.  Precedence: CLI flag > per-Org default > error.
  --no-tty  Disable TTY allocation (non-interactive; for scripted invocations)
  --env KEY=VAL  Pass extra environment variable into the exec session
  --workdir PATH  Working directory inside the container (default: /workspace)
  --shell PATH   Shell to use (default: /bin/bash)
  --timeout SECONDS  Connection timeout for ssh / remote-socket backends (default: 30)
  --dry-run  Print the resolved command without executing it
```

### Examples

```bash
# Local backend (most common for dev)
otaman launch acme-corp cli-agent

# Explicit backend override
otaman launch acme-corp cli-agent --backend ssh

# Non-interactive (scripted)
otaman launch acme-corp cli-agent --no-tty --backend local

# Dry run (shows the resolved docker exec command)
otaman launch acme-corp cli-agent --dry-run
  [dry-run] docker exec -it otaman-org-acme-corp claude --agent cli-agent
```

---

## Execution backends

### Backend 1 — local (`docker exec -it`)

Used when the Docker daemon runs on the same machine as the operator.

```
Resolution:  docker exec -it otaman-org-<org-slug> <shell> -l -c "claude --agent <agent>"
```

**Full command:**
```bash
docker exec -it \
  --workdir /workspace \
  --env OTAMAN_AGENT=<agent> \
  --env OTAMAN_ORG=<org-slug> \
  otaman-org-<org-slug> \
  /bin/bash -l -c "claude --agent <agent>"
```

**Container name convention:** `otaman-org-<org-slug>` (per deploy-agent `docker-compose-template.md`).

**Container lookup:** Before exec, CLI verifies the container exists and is running:
```python
result = subprocess.run(
    ["docker", "inspect", "--format", "{{.State.Running}}", f"otaman-org-{org_slug}"],
    capture_output=True, text=True,
)
if result.stdout.strip() != "true":
    raise SystemExit(f"Container 'otaman-org-{org_slug}' is not running. "
                     f"Start it with: docker compose -f ~/orgs/{org_slug}/config/compose.yml up -d")
```

---

### Backend 2 — ssh (`ssh` + `docker exec -it`)

Used when the Docker host is a remote Linux server.

```
Resolution:
  1. Open SSH connection to <host>:<port>
  2. Run: docker exec -it otaman-org-<org-slug> ...
```

**Full invocation:**
```bash
ssh -t \
  -p <port> \
  -i <identity_file> \
  -o StrictHostKeyChecking=accept-new \
  <user>@<host> \
  "docker exec -it --workdir /workspace --env OTAMAN_AGENT=<agent> --env OTAMAN_ORG=<org-slug> otaman-org-<org-slug> /bin/bash -l -c 'claude --agent <agent>'"
```

`-t` allocates a pseudo-TTY on the SSH session; the inner `docker exec -it` allocates another for the container. Both are needed for correct terminal dimensions + SIGWINCH propagation.

**Config keys in `launch-settings.yaml`** (see §Schema below):
- `host` — SSH hostname or IP
- `port` — SSH port (default: 22)
- `user` — SSH user (default: current Unix user)
- `identity_file` — path to SSH private key (default: `~/.ssh/id_ed25519`)
- `strict_host_key_checking` — `accept-new` | `yes` | `no` (default: `accept-new`)

---

### Backend 3 — remote-socket (`DOCKER_HOST` via TCP/TLS)

Used when the Docker daemon exposes a remote socket (e.g., in a CI environment or a dedicated Docker host with TLS mutual auth).

```
Resolution:  DOCKER_HOST=<socket-url> docker exec -it otaman-org-<org-slug> ...
```

**Full invocation:**
```bash
DOCKER_HOST=<socket-url> \
DOCKER_TLS_VERIFY=1 \
DOCKER_CERT_PATH=<cert-dir> \
docker exec -it \
  --workdir /workspace \
  --env OTAMAN_AGENT=<agent> \
  --env OTAMAN_ORG=<org-slug> \
  otaman-org-<org-slug> \
  /bin/bash -l -c "claude --agent <agent>"
```

**Config keys in `launch-settings.yaml`**:
- `socket_url` — Docker socket URL (e.g., `tcp://docker.example.com:2376`)
- `tls` — `true|false` (default: `true` for TCP connections)
- `cert_path` — path to directory containing `ca.pem`, `cert.pem`, `key.pem`

---

## `launch-settings.yaml` schema extension

The existing `launch-settings.yaml` uses a per-account schema. The new schema adds
a `per_org` section alongside the existing `accounts` section.

### Before (existing per-account schema, preserved unchanged)

```yaml
# launch-settings.yaml — existing schema (abbreviated)
default_account: personal
accounts:
  personal:
    claude_path: /home/user/.local/bin/claude
    model: claude-opus-4-7
  work:
    claude_path: /home/user/.local/bin/claude
    model: claude-sonnet-4-6
```

### After (extended schema — backwards compatible)

```yaml
# launch-settings.yaml — extended schema
schema_version: 2          # bumped from 1; v1 files without schema_version are treated as v1

default_account: personal
accounts:
  personal:
    claude_path: /home/user/.local/bin/claude
    model: claude-opus-4-7
  work:
    claude_path: /home/user/.local/bin/claude
    model: claude-sonnet-4-6

# NEW: per-Org launch configuration
per_org:
  acme-corp:
    backend: local                     # default backend for this Org
    # backend: ssh | remote-socket     # alternatives
    container_name: otaman-org-acme-corp  # override if non-standard naming
    shell: /bin/bash                   # default: /bin/bash
    workdir: /workspace                # default: /workspace
    extra_env: {}                      # key/value pairs always injected

  staging:
    backend: ssh
    ssh:
      host: staging.acme.com
      port: 22
      user: otaman
      identity_file: ~/.ssh/id_ed25519_staging

  ci-runner:
    backend: remote-socket
    remote_socket:
      socket_url: tcp://docker.ci.acme.com:2376
      tls: true
      cert_path: ~/.docker/certs/ci-runner/
```

### Schema definition (for validation)

```python
# launch-settings schema (per-org section)
PER_ORG_SCHEMA = {
    "backend": {"type": "string", "enum": ["local", "ssh", "remote-socket"]},
    "container_name": {"type": "string"},  # optional override
    "shell": {"type": "string"},
    "workdir": {"type": "string"},
    "extra_env": {"type": "object"},
    "ssh": {
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "integer", "default": 22},
            "user": {"type": "string"},
            "identity_file": {"type": "string"},
            "strict_host_key_checking": {
                "type": "string",
                "enum": ["accept-new", "yes", "no"],
                "default": "accept-new",
            },
        },
        "required": ["host"],
    },
    "remote_socket": {
        "type": "object",
        "properties": {
            "socket_url": {"type": "string"},
            "tls": {"type": "boolean", "default": True},
            "cert_path": {"type": "string"},
        },
        "required": ["socket_url"],
    },
}
```

### Backwards compatibility

- Files without `schema_version` are treated as v1 (no `per_org` section).
- The `accounts` section schema is unchanged.
- A v2 file without a `per_org` section is valid (per-Org defaults are used for all Orgs).
- An unknown Org in `per_org` triggers a warning, not an error.

---

## Backend override precedence

```
CLI flag (--backend)
  └── per-Org default in launch-settings.yaml (per_org.<slug>.backend)
        └── ERROR: no backend configured
              (no global default — operator must configure per Org or pass --backend)
```

Implementation:

```python
def resolve_backend(org_slug: str, cli_backend: str | None,
                    settings: dict) -> str:
    """Resolve the execution backend with documented precedence."""
    if cli_backend:
        return cli_backend
    per_org = settings.get("per_org", {})
    org_cfg = per_org.get(org_slug, {})
    if "backend" in org_cfg:
        return org_cfg["backend"]
    raise SystemExit(
        f"No backend configured for org '{org_slug}'.\n"
        f"  Set it in launch-settings.yaml under per_org.{org_slug}.backend\n"
        f"  or pass --backend local|ssh|remote-socket on the command line."
    )
```

**There is no global default backend.** This is intentional: silently defaulting to
`local` on a multi-host deployment could execute in the wrong place. Operators must be
explicit about where each Org runs.

---

## TTY handling and signal forwarding

### TTY allocation

`docker exec -it` requires `-i` (stdin) and `-t` (TTY) together for interactive sessions.
The CLI must detect whether the calling terminal supports TTY allocation:

```python
import sys, os

def _should_allocate_tty(force_no_tty: bool) -> bool:
    if force_no_tty:
        return False
    # Both stdin and stdout must be a TTY
    return sys.stdin.isatty() and sys.stdout.isatty()
```

When `--no-tty` is passed or the terminal doesn't support TTY:
- Drop `-t` from `docker exec`
- Keep `-i` for stdin passthrough
- Log `[i] Non-interactive mode — TTY not allocated`

### Terminal size

When TTY is allocated, the container's pseudo-terminal must start at the correct dimensions:

```python
import shutil
cols, rows = shutil.get_terminal_size(fallback=(80, 24))
# Pass via docker exec --env: COLUMNS + LINES (bash reads these)
extra_env = {"COLUMNS": str(cols), "LINES": str(rows)}
```

`docker exec` inherits the outer terminal's `SIGWINCH` signal automatically in TTY mode, so window resizes are propagated without additional handling.

### Signal forwarding (SIGINT, SIGTERM)

When the CLI subprocess (`docker exec` or `ssh ... docker exec`) is running:

```python
import signal, subprocess

proc = subprocess.Popen(cmd, ...)

def _forward(sig, _frame):
    try:
        proc.send_signal(sig)
    except ProcessLookupError:
        pass  # already exited

signal.signal(signal.SIGINT, _forward)
signal.signal(signal.SIGTERM, _forward)
```

**SIGINT (Ctrl-C):** Forwarded to the inner process. The Claude session handles it gracefully (checkpoint save, clean exit).
**SIGTERM:** Forwarded to allow graceful shutdown. The container's SIGTERM handler (bridge SIGTERM graceful shutdown per bridge-agent task 4.2) is responsible for draining.
**SIGHUP:** Not forwarded — terminal disconnect leaves the container running (session survives disconnect).

### Exit code passthrough

```python
proc = subprocess.Popen(cmd)
proc.wait()
sys.exit(proc.returncode)
```

The `docker exec` exit code equals the inner command's exit code (0 = clean exit, 1+ = error). The CLI passes it through unchanged to the calling shell.

---

## Coordination with deploy-agent (task 3.4)

### Container name convention

Container name: `otaman-org-<org-slug>` — confirmed with deploy-agent's `docker-compose-template.md`:

```yaml
# compose.yml (excerpt)
services:
  agent:
    container_name: otaman-org-${ORG_SLUG}
    ...
```

The CLI derives the container name as `f"otaman-org-{org_slug}"` unless overridden by `per_org.<slug>.container_name` in `launch-settings.yaml`.

### Full `docker exec` command (local backend)

```bash
docker exec -it \
  --workdir /workspace \
  --env OTAMAN_AGENT=cli-agent \
  --env OTAMAN_ORG=acme-corp \
  --env COLUMNS=220 \
  --env LINES=50 \
  otaman-org-acme-corp \
  /bin/bash -l -c "claude --agent cli-agent"
```

Notes:
- `/bin/bash -l` is a login shell so `.bash_profile` / `.bashrc` inside the container runs (PATH, OTAMAN_ROOT, etc. are configured there).
- `-c "claude --agent <agent>"` starts Claude Code pointing at the correct agent identity.
- `OTAMAN_AGENT` and `OTAMAN_ORG` are injected for use by hooks / scripts inside the container.

### `otaman launch <org> --start` (bonus: start-if-stopped)

If the container for the org is stopped (e.g., after reboot), the CLI offers to start it:

```
$ otaman launch acme-corp cli-agent
  [!] Container 'otaman-org-acme-corp' is not running.
  Start it? [Y/n]: y
  [i] Running: docker compose -f ~/orgs/acme-corp/config/compose.yml up -d
  [+] Container started.  Waiting for health check...
  [+] Ready.  Attaching to cli-agent session...
```

This is a UX convenience — the `--start` flag does it non-interactively.

---

## Argparse wiring (sketch)

```python
# main.py additions

p_launch = sub.add_parser(
    "launch",
    help="attach to an agent session in an Organisation container",
)
p_launch.add_argument("org", help="Organisation slug (e.g. 'acme-corp')")
p_launch.add_argument("agent", help="agent name (e.g. 'cli-agent')")
p_launch.add_argument(
    "--backend",
    choices=["local", "ssh", "remote-socket"],
    help="execution backend (overrides per-org default in launch-settings.yaml)",
)
p_launch.add_argument("--no-tty", action="store_true",
                       help="disable TTY allocation (non-interactive)")
p_launch.add_argument("--env", metavar="KEY=VAL", action="append",
                       help="extra environment variable (repeatable)")
p_launch.add_argument("--workdir", metavar="PATH", default="/workspace")
p_launch.add_argument("--shell", metavar="PATH", default="/bin/bash")
p_launch.add_argument("--timeout", type=int, default=30,
                       help="connection timeout for ssh / remote-socket (seconds)")
p_launch.add_argument("--start", action="store_true",
                       help="start the container if stopped before attaching")
p_launch.add_argument("--dry-run", action="store_true",
                       help="print the resolved command without executing")
p_launch.set_defaults(func=cmd_launch)
```

---

## Implementation module sketch

```
src/otaman_cli/launch/
  __init__.py
  cli.py          # cmd_launch() dispatcher + argparse wiring
  settings.py     # load_launch_settings(), resolve_backend()
  backends/
    __init__.py
    local.py      # build_local_cmd()
    ssh.py        # build_ssh_cmd()
    remote.py     # build_remote_socket_cmd()
  tty.py          # _should_allocate_tty(), _get_terminal_size(), _forward_signals()
```

Each `build_*_cmd()` function returns a `list[str]` (subprocess-ready command).
`cmd_launch()` resolves the backend, builds the command, sets up signal forwarding,
and `exec`s via `subprocess.Popen`.

---

## Open questions for design.md

1. **`claude --agent <agent>` invocation** — should the `claude` binary path inside the container be hardcoded to `/usr/local/bin/claude` or read from a container-local `launch-settings.yaml`? Proposal: hardcoded to `/usr/local/bin/claude` in v1 (installed by the Dockerfile); configurable in v2 via `launch-settings.yaml` per-org `claude_path` override.

2. **SSH multiplexing** — for frequent `otaman launch` usage against the same remote host, SSH ControlMaster multiplexing would reduce connection overhead. Proposal: opt-in via `launch-settings.yaml` `ssh.control_master: true` in v1.5.

3. **Agent identity** — should `launch` auto-set the agent identity inside the container (via `otaman set-agent <agent>` before the Claude session starts), or is it always pre-configured in the container's environment? Proposal: inject `OTAMAN_AGENT` as env var; the container's `.bashrc` calls `otaman set-agent $OTAMAN_AGENT` on login shell start.
