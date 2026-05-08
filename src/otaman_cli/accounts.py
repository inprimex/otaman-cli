"""maestro accounts — add / list / remove / install-shell-aliases.

Mutates ``launch-settings.yaml`` (the launcher config) in place. Uses
line-based editing for adds and removes so comments and ordering in the
rest of the file survive unchanged.

Entry points are invoked from cli/maestro.py; each subcommand accepts a
list of already-parsed args plus keyword options.
"""

from __future__ import annotations

import argparse
import os
import platform as _platform
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)

from otaman_core._resolve import find_maestro_root


DEFAULT_SETTINGS_FILENAME = "launch-settings.yaml"


# ---------------------------------------------------------------------------
# File resolution


def resolve_settings_path(cli_override: str | None = None) -> Path:
    """Locate launch-settings.yaml.

    Priority:
      1. ``--settings PATH`` CLI override
      2. ``<maestro-root>/launch-settings.yaml``
      3. ``./launch-settings.yaml`` in the current working directory
    """
    if cli_override:
        return Path(cli_override).expanduser().resolve()

    root = find_maestro_root()
    if root is not None:
        return root / DEFAULT_SETTINGS_FILENAME

    return Path.cwd() / DEFAULT_SETTINGS_FILENAME


# ---------------------------------------------------------------------------
# Parsing helpers


def load_settings(path: Path) -> dict[str, Any]:
    """Read launch-settings.yaml. Returns empty dict if file is absent or empty."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _find_block_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """Locate ``key:`` block in a list of file lines.

    Returns (start_index, end_index_exclusive) of the block's range, where
    ``start_index`` is the line with ``key:`` and ``end_index_exclusive``
    is the first line belonging to the next top-level key (or past EOF).
    Returns None if the key is not present as a top-level entry.
    """
    start = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        # Top-level entries have no leading whitespace.
        if stripped.startswith(f"{key}:") and not (line.startswith(" ") or line.startswith("\t")):
            start = i
            break
    if start is None:
        return None

    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if line.startswith("#"):
            continue
        if not (line.startswith(" ") or line.startswith("\t")):
            end = i
            break
    return (start, end)


def _find_account_span(lines: list[str], accounts_span: tuple[int, int], name: str) -> tuple[int, int] | None:
    """Inside the accounts: block, find the span of a specific account entry.

    Account entries look like ``  <name>:`` with two-space indent. Body
    continues until the next same-indent key or end of the accounts block.
    """
    a_start, a_end = accounts_span
    start = None
    indent = None
    for i in range(a_start + 1, a_end):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Detect the entry's indent from the first real child line we see.
        if indent is None:
            leading = line[: len(line) - len(line.lstrip(" \t"))]
            indent = leading
        if line.startswith(indent) and line[len(indent) :].startswith(f"{name}:"):
            # Confirm it's the name at this exact indent, not deeper.
            rest = line[len(indent) :]
            if rest.startswith(f"{name}:") and (
                len(rest) == len(name) + 1 or rest[len(name) + 1] in (" ", "\n", "\r")
            ):
                start = i
                break

    if start is None:
        return None

    end = a_end
    header_indent = lines[start][: len(lines[start]) - len(lines[start].lstrip(" \t"))]
    for i in range(start + 1, a_end):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = line[: len(line) - len(line.lstrip(" \t"))]
        if len(leading) <= len(header_indent):
            end = i
            break
    return (start, end)


# ---------------------------------------------------------------------------
# add


def add_account(
    settings_path: Path,
    name: str,
    config_dir: str,
    label: str | None = None,
) -> list[str]:
    """Add a new account entry to launch-settings.yaml. Idempotent by name.

    Raises ValueError on name conflict. Returns a list of status messages.
    """
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            f"Account name must be alphanumeric / dashes / underscores; got {name!r}"
        )
    if not config_dir:
        raise ValueError("config_dir is required")

    existing = load_settings(settings_path)
    accounts = existing.get("accounts") if isinstance(existing, dict) else None
    if isinstance(accounts, dict) and name in accounts:
        raise ValueError(
            f"Account '{name}' already exists in {settings_path}. "
            f"Remove it first with 'maestro accounts remove {name}'."
        )

    # Build the YAML block for the new account.
    lines_out: list[str] = [f"  {name}:"]
    lines_out.append(f"    config_dir: {_yaml_scalar(config_dir)}")
    if label:
        lines_out.append(f"    label: {_yaml_scalar(label)}")
    new_block = "\n".join(lines_out) + "\n"

    results: list[str] = []

    if not settings_path.exists() or settings_path.stat().st_size == 0:
        # Create a fresh file with the accounts block.
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(f"accounts:\n{new_block}", encoding="utf-8")
        results.append(f"Created: {settings_path} with account '{name}'")
        return results

    content = settings_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    span = _find_block_span(lines, "accounts")
    if span is None:
        # No accounts block yet — append one.
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        if lines and lines[-1] != "\n":
            lines.append("\n")
        lines.append("accounts:\n")
        lines.append(new_block)
    else:
        # Insert new account at end of the existing accounts block.
        _, end = span
        # Find the last non-blank line of the accounts block to keep insertion tidy.
        insert_at = end
        while insert_at > span[0] + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        new_lines = [ln + "\n" if not ln.endswith("\n") else ln
                     for ln in new_block.splitlines(keepends=False)]
        # Re-expand keepends
        new_lines = new_block.splitlines(keepends=True)
        lines = lines[:insert_at] + new_lines + lines[insert_at:]

    settings_path.write_text("".join(lines), encoding="utf-8")
    results.append(f"Added: account '{name}' -> config_dir={config_dir}")
    if label:
        results.append(f"  label: {label}")
    return results


def _yaml_scalar(value: str) -> str:
    """Quote a scalar for safe YAML emission when needed."""
    value = value.replace("\r", "").replace("\n", " ")
    special = any(c in value for c in ':#&*!|>\'"%@`') or value.startswith(("-", "?", "[", "{"))
    if special or not value.strip() or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


# ---------------------------------------------------------------------------
# list


def list_accounts(settings_path: Path) -> list[dict[str, Any]]:
    """Return enriched account records: name, config_dir, label, used_by connections."""
    data = load_settings(settings_path)
    accounts = data.get("accounts") or {}
    connections = data.get("connections") or {}

    usage: dict[str, list[str]] = {}
    if isinstance(connections, dict):
        for conn_name, conn in connections.items():
            if not isinstance(conn, dict):
                continue
            acct = conn.get("account")
            if acct:
                usage.setdefault(acct, []).append(conn_name)

    records: list[dict[str, Any]] = []
    if isinstance(accounts, dict):
        for name, spec in accounts.items():
            spec = spec or {}
            records.append(
                {
                    "name": name,
                    "config_dir": spec.get("config_dir", ""),
                    "label": spec.get("label", ""),
                    "used_by": sorted(usage.get(name, [])),
                }
            )
    records.sort(key=lambda r: r["name"])
    return records


def render_accounts_table(records: list[dict[str, Any]]) -> str:
    """Format account records as a human-readable table."""
    if not records:
        return "(no accounts configured)"

    headers = ("NAME", "CONFIG_DIR", "LABEL", "USED BY")
    rows = [
        (
            r["name"],
            r["config_dir"],
            r["label"] or "-",
            ", ".join(r["used_by"]) if r["used_by"] else "-",
        )
        for r in records
    ]
    widths = [
        max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*headers)]
    out.append(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        out.append(fmt.format(*row))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# remove


def remove_account(
    settings_path: Path,
    name: str,
    *,
    force: bool = False,
) -> list[str]:
    """Remove an account from launch-settings.yaml.

    Refuses to remove if any connection still references the account unless
    ``force=True``. Returns a list of status messages.
    """
    if not settings_path.exists():
        raise FileNotFoundError(f"{settings_path} does not exist")

    data = load_settings(settings_path)
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or name not in accounts:
        raise KeyError(f"Account '{name}' is not defined in {settings_path}")

    # Safety check: any connection using this account?
    connections = data.get("connections") or {}
    referencing: list[str] = []
    if isinstance(connections, dict):
        for conn_name, conn in connections.items():
            if isinstance(conn, dict) and conn.get("account") == name:
                referencing.append(conn_name)

    if referencing and not force:
        raise RuntimeError(
            f"Account '{name}' is still referenced by connection(s): "
            f"{', '.join(referencing)}. "
            f"Update them first, or pass --force to remove anyway."
        )

    content = settings_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    span = _find_block_span(lines, "accounts")
    if span is None:
        raise KeyError(f"Account '{name}' not found in {settings_path}")

    entry_span = _find_account_span(lines, span, name)
    if entry_span is None:
        raise KeyError(f"Account '{name}' not found in {settings_path}")

    e_start, e_end = entry_span
    new_lines = lines[:e_start] + lines[e_end:]

    # If that removal emptied the accounts block, clean up the "accounts:" header
    # and any trailing blank line it left behind.
    remaining_accounts_span = _find_block_span(new_lines, "accounts")
    if remaining_accounts_span is not None:
        a_start, a_end = remaining_accounts_span
        has_child = False
        for i in range(a_start + 1, a_end):
            line = new_lines[i]
            if line.strip() and not line.lstrip().startswith("#"):
                has_child = True
                break
        if not has_child:
            new_lines = new_lines[:a_start] + new_lines[a_end:]

    settings_path.write_text("".join(new_lines), encoding="utf-8")

    results = [f"Removed: account '{name}'"]
    if referencing:
        results.append(
            f"  Warning: removed despite references from: {', '.join(referencing)}"
        )
    return results


# ---------------------------------------------------------------------------
# install-shell-aliases


_MARKER_BEGIN = "# BEGIN MAESTRO ACCOUNTS — generated by `maestro accounts install-shell-aliases`"
_MARKER_END = "# END MAESTRO ACCOUNTS"

_SHELL_RC_DEFAULTS = {
    "bash": "~/.bashrc",
    "zsh": "~/.zshrc",
    "fish": "~/.config/fish/config.fish",
}


def detect_shell() -> str:
    """Best-guess shell for the current platform.

    Unix: derive from ``$SHELL`` (fallback: ``bash``).
    Windows: ``powershell``.
    """
    if _platform.system() == "Windows":
        return "powershell"
    shell_env = os.environ.get("SHELL", "").lower()
    for candidate in ("fish", "zsh", "bash"):
        if shell_env.endswith(f"/{candidate}") or shell_env.endswith(candidate):
            return candidate
    return "bash"


def resolve_rc_path(shell: str, override: str | None = None) -> Path:
    """Resolve the rc file path for a given shell."""
    if override:
        return Path(override).expanduser().resolve()

    if shell == "powershell":
        # Prefer pwsh's per-user profile location. Windows PS 5.1 uses a
        # different path (Documents/WindowsPowerShell/profile.ps1); users
        # on 5.1 can pass --target to override.
        userprofile = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(userprofile) / "Documents" / "PowerShell" / "Profile.ps1"

    default = _SHELL_RC_DEFAULTS.get(shell)
    if not default:
        raise ValueError(f"Unknown shell: {shell}")
    return Path(default).expanduser()


def render_aliases_block(records: list[dict[str, Any]], shell: str) -> str:
    """Render the shell-specific alias block for a list of accounts."""
    if not records:
        return ""

    lines: list[str] = [_MARKER_BEGIN, ""]

    if shell == "powershell":
        for r in records:
            name = r["name"]
            config_dir = r["config_dir"]
            label = r.get("label") or ""
            lines.append(f"# Account: {name}" + (f" ({label})" if label else ""))
            lines.append(f"function claude-{name} {{")
            # Store + restore prior value so functions don't leak state.
            lines.append(f"    $prev = $env:CLAUDE_CONFIG_DIR")
            lines.append(f"    $env:CLAUDE_CONFIG_DIR = '{config_dir}'")
            lines.append(f"    try {{ claude @args }} finally {{ $env:CLAUDE_CONFIG_DIR = $prev }}")
            lines.append("}")
            lines.append("")
    elif shell == "fish":
        for r in records:
            name = r["name"]
            config_dir = r["config_dir"]
            label = r.get("label") or ""
            lines.append(f"# Account: {name}" + (f" ({label})" if label else ""))
            lines.append(f"function claude-{name}")
            lines.append(f"    env CLAUDE_CONFIG_DIR={_bash_single_quote(config_dir)} claude $argv")
            lines.append("end")
            lines.append("")
    else:  # bash / zsh
        for r in records:
            name = r["name"]
            config_dir = r["config_dir"]
            label = r.get("label") or ""
            lines.append(f"# Account: {name}" + (f" ({label})" if label else ""))
            lines.append(
                f"claude-{name}() {{ "
                f"CLAUDE_CONFIG_DIR={_bash_single_quote(config_dir)} command claude \"$@\"; "
                f"}}"
            )
            lines.append("")

    lines.append(_MARKER_END)
    return "\n".join(lines) + "\n"


def _bash_single_quote(value: str) -> str:
    """Wrap a value in single quotes, escaping any embedded apostrophes."""
    return "'" + value.replace("'", "'\\''") + "'"


def install_shell_aliases(
    settings_path: Path,
    shell: str,
    target: Path | None = None,
) -> list[str]:
    """Write shell alias functions for each configured account.

    Uses BEGIN/END markers so re-running updates the block in place without
    disturbing surrounding user content.
    """
    if shell not in {"bash", "zsh", "fish", "powershell"}:
        raise ValueError(
            f"--shell must be one of: bash, zsh, fish, powershell (got {shell!r})"
        )

    records = list_accounts(settings_path)
    if not records:
        raise RuntimeError(
            f"No accounts configured in {settings_path}. "
            f"Add one first with 'maestro accounts add'."
        )

    rc_path = resolve_rc_path(shell, str(target) if target else None)
    rc_path.parent.mkdir(parents=True, exist_ok=True)

    new_block = render_aliases_block(records, shell)

    if rc_path.exists():
        content = rc_path.read_text(encoding="utf-8")
    else:
        content = ""

    begin_idx = content.find(_MARKER_BEGIN)
    end_idx = content.find(_MARKER_END)

    if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
        end_full = end_idx + len(_MARKER_END)
        # Swallow trailing newline after the END marker if present.
        if end_full < len(content) and content[end_full] == "\n":
            end_full += 1
        updated = content[:begin_idx] + new_block + content[end_full:]
        action = "Updated"
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        if content:
            content += "\n"
        updated = content + new_block
        action = "Appended"

    rc_path.write_text(updated, encoding="utf-8")

    messages = [
        f"{action} alias block in: {rc_path}",
        f"  shell: {shell}",
        f"  accounts: {', '.join(r['name'] for r in records)}",
    ]
    if shell in {"bash", "zsh", "fish"}:
        messages.append(f"  activate in current shell: source {rc_path}")
    elif shell == "powershell":
        messages.append(f"  activate in current shell: . \"{rc_path}\"")
    return messages


# ---------------------------------------------------------------------------
# configure-telegram


def _parse_user_ids(raw: str) -> list[int]:
    """Parse a comma-separated list of Telegram user IDs."""
    ids: list[int] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError as exc:
            raise ValueError(
                f"invalid user id {item!r}: must be an integer"
            ) from exc
    return ids


def _build_telegram_block_yaml(
    group_id: int,
    allowed_user_ids: list[int],
    bot_token_env: str,
    auto_create_topics: bool,
    default_topic_id: int | None,
    indent: str = "    ",
) -> str:
    """Render the transport_config YAML block. Caller owns the outer indent."""
    lines = [
        f"{indent}group_id: {group_id}",
        f"{indent}allowed_user_ids: [{', '.join(str(x) for x in allowed_user_ids)}]",
        f"{indent}auto_create_topics: {'true' if auto_create_topics else 'false'}",
    ]
    if default_topic_id is not None:
        lines.append(f"{indent}default_topic_id: {default_topic_id}")
    # Long-form bot_token chain — env first, then dotenv, then keyring. This
    # matches the default resolution order in _secrets.py and is the shape
    # `bridge/config.py` understands.
    lines.extend([
        f"{indent}bot_token:",
        f"{indent}  sources:",
        f"{indent}    - {{ type: env,     name: {bot_token_env} }}",
        f"{indent}    - {{ type: dotenv,  name: {bot_token_env} }}",
        f"{indent}    - {{ type: keyring, service: maestro, account: tg-{bot_token_env.lower()} }}",
    ])
    return "\n".join(lines) + "\n"


def configure_telegram(
    settings_path: Path,
    account: str,
    *,
    group_id: int,
    allowed_user_ids: list[int],
    bot_token_env: str = "",
    auto_create_topics: bool = True,
    default_topic_id: int | None = None,
) -> list[str]:
    """Add or replace the Telegram transport config on an account.

    Requires the account to already exist (run ``accounts add`` first).
    Idempotent: re-running with different values overwrites the existing
    block in place, preserving surrounding content.
    """
    if not settings_path.exists():
        raise FileNotFoundError(
            f"{settings_path} does not exist — run `maestro accounts add {account}` first"
        )

    data = load_settings(settings_path)
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or account not in accounts:
        raise KeyError(
            f"Account {account!r} is not defined. "
            f"Run `maestro accounts add {account} --config-dir ~/.claude-{account}` first."
        )

    if not bot_token_env:
        bot_token_env = f"MAESTRO_TG_BOT_{account.upper()}"

    content = settings_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    accounts_span = _find_block_span(lines, "accounts")
    if accounts_span is None:
        raise RuntimeError(
            "accounts: block not found in settings file (this should not happen "
            "after the account was loaded successfully)."
        )

    entry_span = _find_account_span(lines, accounts_span, account)
    if entry_span is None:
        raise RuntimeError(
            f"Could not locate account {account!r} in settings file "
            f"(parser out of sync with schema?)."
        )

    e_start, e_end = entry_span
    entry_lines = lines[e_start:e_end]

    # Determine child indent by looking at the first indented child line.
    child_indent = "    "  # default 4 spaces under a 2-space account header
    for ln in entry_lines[1:]:
        if ln.strip() and not ln.lstrip().startswith("#"):
            leading = ln[: len(ln) - len(ln.lstrip(" \t"))]
            if leading:
                child_indent = leading
            break

    # Strip any existing transport: / transport_config: / legacy telegram:
    # block so we can re-emit fresh. Anything else (config_dir, label, etc.)
    # stays where it was.
    kept: list[str] = [entry_lines[0]]  # keep the `<account>:` header line
    skip_until_deindent = False
    skip_indent: str | None = None
    for ln in entry_lines[1:]:
        stripped = ln.strip()
        if skip_until_deindent:
            if not stripped or stripped.startswith("#"):
                # Swallow blank/comment lines that belong to the removed block.
                continue
            leading = ln[: len(ln) - len(ln.lstrip(" \t"))]
            if skip_indent is not None and len(leading) > len(skip_indent):
                continue
            skip_until_deindent = False

        if stripped.startswith(("transport:", "transport_config:", "telegram:")):
            # Enter skip mode — drop this header AND its indented children.
            skip_until_deindent = True
            skip_indent = ln[: len(ln) - len(ln.lstrip(" \t"))]
            continue

        kept.append(ln)

    # Ensure the last kept line ends with a newline so the append lands cleanly.
    if kept and not kept[-1].endswith("\n"):
        kept[-1] = kept[-1] + "\n"

    transport_block = (
        f"{child_indent}transport: telegram\n"
        f"{child_indent}transport_config:\n"
        + _build_telegram_block_yaml(
            group_id=group_id,
            allowed_user_ids=allowed_user_ids,
            bot_token_env=bot_token_env,
            auto_create_topics=auto_create_topics,
            default_topic_id=default_topic_id,
            indent=child_indent + "  ",
        )
    )

    new_entry = kept + [transport_block]
    new_lines = lines[:e_start] + new_entry + lines[e_end:]
    settings_path.write_text("".join(new_lines), encoding="utf-8")

    results = [
        f"Configured telegram transport on account '{account}'",
        f"  group_id: {group_id}",
        f"  allowed_user_ids: {allowed_user_ids}",
        f"  bot_token source chain: env({bot_token_env}) → dotenv({bot_token_env}) → keyring",
    ]
    if auto_create_topics:
        results.append("  auto_create_topics: true (bot will create per-project forum topics)")
    if default_topic_id is not None:
        results.append(f"  default_topic_id: {default_topic_id}")
    results.append("")
    results.append("Next steps:")
    results.append(
        f"  1. Put the bot token in the maestro folder's .maestro/secrets.env:"
    )
    results.append(f"       {bot_token_env}=<your-bot-token-here>")
    results.append(f"       chmod 600 .maestro/secrets.env")
    results.append("  2. `pip install otaman-bridge[telegram]` (or `uv pip install otaman-bridge[telegram]` — faster)")
    results.append(f"  3. `maestro bridge run --account {account}`")
    results.append("  4. `maestro afk on 30m`")
    results.append("  5. Trigger a Claude tool call and watch your phone.")
    return results


# ---------------------------------------------------------------------------
# argparse entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maestro accounts",
        description="Manage Claude Code account definitions in launch-settings.yaml",
    )
    parser.add_argument(
        "--settings",
        help=f"Path to {DEFAULT_SETTINGS_FILENAME} (default: maestro-root/launch-settings.yaml)",
    )
    subs = parser.add_subparsers(dest="subcommand", required=True)

    p_add = subs.add_parser("add", help="Add a new account entry")
    p_add.add_argument("name", help="Account name (e.g. personal, riseapps)")
    p_add.add_argument(
        "--config-dir",
        required=True,
        help="CLAUDE_CONFIG_DIR path (e.g. ~/.claude-personal)",
    )
    p_add.add_argument("--label", help="Human-readable label (optional)")

    subs.add_parser("list", help="List configured accounts")

    p_rm = subs.add_parser("remove", help="Remove an account entry")
    p_rm.add_argument("name", help="Account name to remove")
    p_rm.add_argument(
        "--force",
        action="store_true",
        help="Remove even if connections still reference this account",
    )

    p_tg = subs.add_parser(
        "configure-telegram",
        help="Add/update the Telegram transport config on an existing account",
    )
    p_tg.add_argument("account", help="Account name (must already exist)")
    p_tg.add_argument(
        "--group-id", required=True, type=int,
        help="Telegram supergroup ID (negative integer, usually starts with -100)",
    )
    p_tg.add_argument(
        "--allowed-user-ids", required=True,
        help="Comma-separated Telegram user IDs allowed to approve",
    )
    p_tg.add_argument(
        "--bot-token-env",
        default="",
        help="Env var name holding the bot token (default: MAESTRO_TG_BOT_<ACCOUNT>)",
    )
    p_tg.add_argument(
        "--no-auto-create-topics", action="store_true",
        help="Disable forum topic auto-creation (default: enabled)",
    )
    p_tg.add_argument(
        "--default-topic-id", type=int, default=None,
        help="Fallback forum topic thread id for projects without their own",
    )

    p_aliases = subs.add_parser(
        "install-shell-aliases",
        help="Emit `claude-<name>` shell functions for all accounts",
    )
    p_aliases.add_argument(
        "--shell",
        default="auto",
        choices=["auto", "bash", "zsh", "fish", "powershell"],
        help="Target shell (default: auto-detect)",
    )
    p_aliases.add_argument(
        "--target",
        help="Override rc file path (default: shell-specific)",
    )

    args = parser.parse_args(argv)
    settings_path = resolve_settings_path(args.settings)

    if args.subcommand == "add":
        try:
            for msg in add_account(
                settings_path,
                args.name,
                args.config_dir,
                label=args.label,
            ):
                print(msg)
            return 0
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    if args.subcommand == "list":
        records = list_accounts(settings_path)
        print(render_accounts_table(records))
        return 0

    if args.subcommand == "remove":
        try:
            for msg in remove_account(settings_path, args.name, force=args.force):
                print(msg)
            return 0
        except (FileNotFoundError, KeyError, RuntimeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    if args.subcommand == "configure-telegram":
        try:
            user_ids = _parse_user_ids(args.allowed_user_ids)
            if not user_ids:
                raise ValueError("--allowed-user-ids must contain at least one ID")
            for msg in configure_telegram(
                settings_path,
                args.account,
                group_id=args.group_id,
                allowed_user_ids=user_ids,
                bot_token_env=args.bot_token_env,
                auto_create_topics=not args.no_auto_create_topics,
                default_topic_id=args.default_topic_id,
            ):
                print(msg)
            return 0
        except (ValueError, KeyError, FileNotFoundError, RuntimeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    if args.subcommand == "install-shell-aliases":
        shell = args.shell if args.shell != "auto" else detect_shell()
        try:
            target = Path(args.target).expanduser() if args.target else None
            for msg in install_shell_aliases(settings_path, shell, target=target):
                print(msg)
            return 0
        except (ValueError, RuntimeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    return 2  # unreachable — argparse enforces required subcommand


if __name__ == "__main__":
    sys.exit(main())
