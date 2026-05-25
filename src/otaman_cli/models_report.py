#!/usr/bin/env python3
"""Report on model/effort frontmatter across otaman commands and agents.

Reads the shipped frontmatter from the plugin's commands/*.md and agents/*.md,
optionally compares against user preferences in platform.yaml's `models:` block,
and prints a report plus mid-session commands the user can run to align the
session with their preferences.

Usage:
    models-report.py                  # show shipped defaults
    models-report.py --diff           # show diff vs platform.yaml models: block
    models-report.py --suggest        # print /model + /effort reminders
"""

from __future__ import annotations

import re
import os
import sys
from pathlib import Path

# Project-root resolver: shared with otaman-core kernel.
from otaman_core._resolve import find_maestro_root as find_project_root


def _find_plugin_root() -> Path:
    """Locate the dir containing commands/ + agents/ for frontmatter scanning.

    Resolution chain (transitional during Stages 2-3 of the carve):
    1. OTAMAN_PLUGIN_ROOT env var (operator override)
    2. Sibling otaman-plugin checkout (post-Stage 4)
    3. Sibling otaman-plugin (or legacy: maestro-plugin) checkout
    4. Fallback: this package's parent dir (empty report if no commands/agents)

    Stage 4 simplifies this when otaman-plugin owns commands/ + agents/.
    """
    env = os.environ.get("OTAMAN_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent_levels in (3, 4):
        try:
            base = here.parents[parent_levels]
        except IndexError:
            continue
        for name in ("otaman-plugin", "maestro-plugin"):  # legacy: also scan old plugin dir name
            cand = base / name
            if (cand / "commands").is_dir() and (cand / "agents").is_dir():
                return cand
    return here.parent.parent


PLUGIN_ROOT = _find_plugin_root()


def parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter as a flat dict. Returns {} on parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_body = text[3:end]
    fields: dict[str, str] = {}
    for line in fm_body.splitlines():
        m = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith(("#", "-", "[", "{", "\"", "'")) and key not in ("name", "description", "model", "effort", "color"):
            continue
        # Only capture scalar fields we care about
        if key in ("name", "description", "model", "effort", "color"):
            fields[key] = val.strip("\"'")
    return fields


def collect(pattern_dir: Path, kind: str) -> list[dict]:
    """Collect frontmatter for every .md file under pattern_dir. Returns list of entries."""
    entries = []
    if not pattern_dir.exists():
        return entries
    for md in sorted(pattern_dir.rglob("*.md")):
        if md.name.startswith("_"):
            continue
        fm = parse_frontmatter(md)
        if not fm:
            continue
        name = fm.get("name") or md.stem
        entries.append(
            {
                "kind": kind,
                "name": name,
                "path": md.relative_to(PLUGIN_ROOT).as_posix(),
                "model": fm.get("model", ""),
                "effort": fm.get("effort", ""),
            }
        )
    return entries


def load_platform_models() -> dict:
    """Read the `models:` block from platform.yaml if present."""
    root = find_project_root()
    if not root:
        return {}
    cfg = root / "platform.yaml"
    if not cfg.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data.get("models") or {}


def print_table(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    print("  " + "-" * 70)
    print(f"  {'Name':<36} {'Model':<10} {'Effort':<10}")
    print("  " + "-" * 70)
    for r in rows:
        model = r.get("model") or "(inherit)"
        effort = r.get("effort") or "(inherit)"
        print(f"  {r['name']:<36} {model:<10} {effort:<10}")


def print_shipped(commands: list[dict], agents: list[dict]) -> None:
    print_table("Commands (shipped defaults)", commands)
    print_table("Agents (shipped defaults)", agents)


def diff_entry(shipped: dict, override: dict) -> dict:
    """Return {'model': (shipped, override)?, 'effort': ...} only for fields that differ."""
    d = {}
    for key in ("model", "effort"):
        s = shipped.get(key) or ""
        o = override.get(key) if isinstance(override, dict) else None
        if o is None:
            continue
        if str(o) != s:
            d[key] = (s or "(inherit)", str(o))
    return d


def print_diff(commands: list[dict], agents: list[dict], overrides: dict) -> None:
    cmd_overrides = overrides.get("commands") or {}
    agent_overrides = overrides.get("agents") or {}

    any_diff = False

    def _process(entries, overrides_map, label):
        nonlocal any_diff
        header_printed = False
        for e in entries:
            # Match by "/otaman:name" (preferred) or "/maestro:name" (legacy: old prefix) or bare name
            key_full = f"/otaman:{e['name']}" if label == "Commands" else e["name"]
            override = overrides_map.get(key_full) or overrides_map.get(e["name"])
            if not override:
                continue
            diff = diff_entry(e, override)
            if not diff:
                continue
            if not header_printed:
                print(f"\n{label} — platform.yaml overrides (differ from shipped):")
                header_printed = True
            any_diff = True
            parts = []
            for field, (s, o) in diff.items():
                parts.append(f"{field}: {s} -> {o}")
            print(f"  {e['name']:<36} {'; '.join(parts)}")

    _process(commands, cmd_overrides, "Commands")
    _process(agents, agent_overrides, "Agents")

    if not any_diff:
        print("\n  No overrides found in platform.yaml (or they match shipped defaults).")


def print_suggestions(commands: list[dict], overrides: dict) -> None:
    cmd_overrides = overrides.get("commands") or {}
    if not cmd_overrides:
        print("\n  No `models.commands` overrides in platform.yaml.")
        print("  Tip: add a block like this to platform.yaml:")
        print("""
  models:
    commands:
      "/otaman:propose":  # legacy: also matches /maestro: prefix
        model: opus
        effort: xhigh
""")
        return

    print("\n  Mid-session switches (type these in Claude Code before invoking the command):")
    for cmd_key, override in cmd_overrides.items():
        if not isinstance(override, dict):
            continue
        parts = []
        if override.get("model"):
            parts.append(f"/model {override['model']}")
        if override.get("effort"):
            parts.append(f"/effort {override['effort']}")
        if parts:
            print(f"    before {cmd_key}: {' ; '.join(parts)}")

    print("\n  To persist for the whole project (all commands use these):")
    print("    Edit .claude/settings.local.json and add:")
    print('      {"model": "sonnet", "effortLevel": "medium"}')
    print("    Note: this is global for the session, not per-command.")


# ---------------------------------------------------------------------------
# Phase C — writable session-tier subcommands: show / set-default / set-repo /
# set-agent / unset-*. Writes to platform.yaml's `models:` block with minimal
# YAML rewriting (PyYAML round-trip). Comments in `models:` are not preserved;
# comments elsewhere in platform.yaml are.


def _load_config_dict(target: str = "auto") -> tuple[Path | None, dict]:
    """Return (path, data) for the YAML file we'll write to.

    Args:
        target: one of
          - ``"platform"``     — always write to platform.yaml (legacy default)
          - ``"launch-settings"`` — always write to launch-settings.yaml
            (launcher-local; useful when the launcher folder is decoupled
            from the otaman folder)
          - ``"auto"``         — auto-detect: if launch-settings.yaml
            exists in the cwd/otaman-root AND looks like a launcher file
            (has ``connections:`` or ``accounts:``), write there; else
            platform.yaml.
    """
    root = find_project_root()
    if not root:
        # No otaman / launcher folder discoverable — check cwd directly
        root = Path.cwd()
    try:
        import yaml  # type: ignore
    except ImportError:
        return None, {}

    if target == "launch-settings":
        cfg = root / "launch-settings.yaml"
    elif target == "platform":
        cfg = root / "platform.yaml"
    else:  # auto
        launch_settings = root / "launch-settings.yaml"
        platform_yaml = root / "platform.yaml"
        if launch_settings.exists() and _looks_like_launcher(launch_settings):
            cfg = launch_settings
        elif platform_yaml.exists():
            cfg = platform_yaml
        elif launch_settings.exists():
            cfg = launch_settings
        else:
            return None, {}

    if not cfg.exists():
        # Fine — we'll create the file on write
        return cfg, {}
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return cfg, {}
    return cfg, data


def _looks_like_launcher(launch_settings: Path) -> bool:
    """True if launch-settings.yaml contains launcher-specific keys."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(launch_settings.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return False
    return bool(
        isinstance(data, dict)
        and (data.get("connections") or data.get("accounts")
             or data.get("active_connection"))
    )


# Backward-compat alias for any external callers of the old name
def _load_platform_dict() -> tuple[Path | None, dict]:
    return _load_config_dict("platform")


def _write_models_block(cfg_path: Path, data: dict, models: dict) -> None:
    """Round-trip a YAML config with the ``models:`` block replaced.

    PyYAML round-trip loses comments/ordering inside models: (acceptable —
    it's a small structured block). Comments elsewhere in the file are
    preserved by rewriting only the models: region. If the file doesn't
    exist yet, creates it containing just the models: block.
    """
    import yaml  # type: ignore
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        if models:
            cfg_path.write_text(
                yaml.dump({"models": models}, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        else:
            cfg_path.write_text("", encoding="utf-8")
        return
    text = cfg_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Locate an existing top-level `models:` block
    start = end = -1
    for i, line in enumerate(lines):
        if line.rstrip("\r\n").startswith("models:") and not line.startswith(" "):
            start = i
            break
    if start != -1:
        # Find the next top-level key (unindented, non-comment, non-blank)
        end = len(lines)
        for j in range(start + 1, len(lines)):
            s = lines[j]
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            if not (s.startswith(" ") or s.startswith("\t")):
                end = j
                break

    # Render the new block (empty models: is omitted to avoid `models: {}`)
    if models:
        block = yaml.dump({"models": models}, default_flow_style=False, sort_keys=False)
    else:
        block = ""

    if start != -1:
        new_lines = lines[:start] + ([block] if block else []) + lines[end:]
    else:
        # Append at end, preceded by a blank line for readability
        if text and not text.endswith("\n"):
            lines.append("\n")
        if lines and lines[-1].strip():
            lines.append("\n")
        new_lines = lines + ([block] if block else [])
    cfg_path.write_text("".join(new_lines), encoding="utf-8")


def _update_platform_models(mutator, target: str = "auto") -> int:
    """Load config file, let ``mutator(models)`` modify in place, write back.

    ``target`` selects which file: ``platform``, ``launch-settings``, or
    ``auto`` (prefers launch-settings.yaml when the current folder looks
    like a launcher folder).
    """
    cfg_path, data = _load_config_dict(target)
    if cfg_path is None:
        print("ERROR: no platform.yaml or launch-settings.yaml found in "
              "current directory or parents",
              file=sys.stderr)
        return 2
    # If the file doesn't exist yet (e.g. creating launch-settings.yaml fresh),
    # start from an empty data dict.
    if not cfg_path.exists():
        data = {}
    models = data.get("models") or {}
    if not isinstance(models, dict):
        print(f"ERROR: {cfg_path.name} 'models:' is not a mapping (got "
              f"{type(models).__name__})", file=sys.stderr)
        return 2
    mutator(models)
    # Prune empty nested dicts so the file stays clean
    for key in ("by_repo", "by_agent", "commands", "agents"):
        if key in models and not models[key]:
            del models[key]
    _write_models_block(cfg_path, data, models)
    # Return the path so callers can tell the user where it went
    print(f"  (wrote {cfg_path})")
    return 0


_VALID_MODELS = ("opus", "sonnet", "haiku", "inherit")
_VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max", "inherit")


def _validate_tier(model: str, effort: str | None) -> str | None:
    """Return an error string if inputs are invalid, None if OK."""
    if model and model.lower() not in _VALID_MODELS:
        return f"invalid model {model!r}; choose from {_VALID_MODELS}"
    if effort and effort.lower() not in _VALID_EFFORTS:
        return f"invalid effort {effort!r}; choose from {_VALID_EFFORTS}"
    return None


def cmd_show(args) -> int:
    """Print the resolution chain for a given (repo, agent) pair."""
    # Delay import so --help works without scripts/ on path.
    try:
        from otaman_cli._models_resolve import explain_chain
    except ImportError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    root = find_project_root()
    if not root:
        print("ERROR: no platform.yaml / .agents/ found", file=sys.stderr)
        return 2

    for line in explain_chain(
        root,
        repo=args.repo,
        agent=args.agent,
        cli_model=args.model,
        cli_effort=args.effort,
    ):
        print(line)
    return 0


def cmd_set_default(args) -> int:
    err = _validate_tier(args.model or "", args.effort)
    if err:
        print(f"ERROR: {err}", file=sys.stderr); return 2

    def mutate(models: dict) -> None:
        if args.model:
            models["default"] = args.model.lower()
        if args.effort:
            models["default_effort"] = args.effort.lower()

    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        fields = []
        if args.model: fields.append(f"default={args.model.lower()}")
        if args.effort: fields.append(f"default_effort={args.effort.lower()}")
        print(f"Set project defaults: {', '.join(fields) or '(no fields given)'}")
    return rc


def cmd_set_repo(args) -> int:
    err = _validate_tier(args.model or "", args.effort)
    if err:
        print(f"ERROR: {err}", file=sys.stderr); return 2

    def mutate(models: dict) -> None:
        by_repo = models.setdefault("by_repo", {})
        entry = by_repo.setdefault(args.repo, {})
        if args.model:
            entry["model"] = args.model.lower()
        if args.effort:
            entry["effort"] = args.effort.lower()
        if not entry:
            del by_repo[args.repo]  # nothing to set; clean up

    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        print(f"Set models.by_repo.{args.repo}: "
              f"model={args.model or '-'}, effort={args.effort or '-'}")
    return rc


def cmd_set_agent(args) -> int:
    err = _validate_tier(args.model or "", args.effort)
    if err:
        print(f"ERROR: {err}", file=sys.stderr); return 2

    def mutate(models: dict) -> None:
        by_agent = models.setdefault("by_agent", {})
        entry = by_agent.setdefault(args.agent, {})
        if args.model:
            entry["model"] = args.model.lower()
        if args.effort:
            entry["effort"] = args.effort.lower()
        if not entry:
            del by_agent[args.agent]

    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        print(f"Set models.by_agent.{args.agent}: "
              f"model={args.model or '-'}, effort={args.effort or '-'}")
    return rc


def cmd_unset_default(args) -> int:  # noqa: ARG001
    def mutate(models: dict) -> None:
        models.pop("default", None)
        models.pop("default_effort", None)
    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        print("Cleared project defaults.")
    return rc


def cmd_unset_repo(args) -> int:
    def mutate(models: dict) -> None:
        by_repo = models.get("by_repo") or {}
        by_repo.pop(args.repo, None)
    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        print(f"Cleared models.by_repo.{args.repo}")
    return rc


def cmd_unset_agent(args) -> int:
    def mutate(models: dict) -> None:
        by_agent = models.get("by_agent") or {}
        by_agent.pop(args.agent, None)
    rc = _update_platform_models(mutate, target=getattr(args, "target", "auto"))
    if rc == 0:
        print(f"Cleared models.by_agent.{args.agent}")
    return rc


def main(argv: list[str]) -> int:
    # Legacy flag-style for backwards compat: `--diff`, `--suggest`, bare.
    # Anything else is parsed as an argparse subcommand.
    if not argv or argv[0] in ("--diff", "--suggest", "-h", "--help"):
        return _main_legacy(argv)

    # New subcommand form.
    import argparse
    parser = argparse.ArgumentParser(
        prog="otaman models",
        description="Inspect and configure session model/effort tiers",
    )
    subs = parser.add_subparsers(dest="subcommand", required=True)

    p_shipped = subs.add_parser(
        "shipped",
        help="Print shipped command/agent frontmatter (legacy table)",
    )
    p_shipped.set_defaults(func=lambda a: _main_legacy([]))

    p_diff = subs.add_parser(
        "diff",
        help="Compare shipped defaults with platform.yaml models: overrides",
    )
    p_diff.set_defaults(func=lambda a: _main_legacy(["--diff"]))

    p_suggest = subs.add_parser(
        "suggest",
        help="Print /model+/effort commands matching platform.yaml overrides",
    )
    p_suggest.set_defaults(func=lambda a: _main_legacy(["--suggest"]))

    p_show = subs.add_parser(
        "show",
        help="Show the effective session tier and which rule fired",
    )
    p_show.add_argument("--repo", help="Repo name for by_repo lookup")
    p_show.add_argument("--agent", help="Agent identity for by_agent lookup")
    p_show.add_argument("--model", help="CLI model override (for simulation)")
    p_show.add_argument("--effort", help="CLI effort override (for simulation)")
    p_show.set_defaults(func=cmd_show)

    # Shared file-target flags — attached to every writer subcommand
    def _add_target_flags(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument(
            "--launch-settings", dest="target", action="store_const",
            const="launch-settings",
            help="Write to launch-settings.yaml (launcher-local, "
                 "useful when the launcher folder is separate from the "
                 "otaman folder)",
        )
        g.add_argument(
            "--platform", dest="target", action="store_const",
            const="platform",
            help="Write to platform.yaml (project-wide; default when in "
                 "an otaman folder)",
        )
        p.set_defaults(target="auto")

    p_setdef = subs.add_parser(
        "set-default",
        help="Set project-wide default model/effort",
    )
    p_setdef.add_argument("--model", help="opus | sonnet | haiku")
    p_setdef.add_argument("--effort", help="low | medium | high | xhigh | max")
    _add_target_flags(p_setdef)
    p_setdef.set_defaults(func=cmd_set_default)

    p_setrepo = subs.add_parser(
        "set-repo",
        help="Set per-repo override (models.by_repo.<name>)",
    )
    p_setrepo.add_argument("repo")
    p_setrepo.add_argument("--model")
    p_setrepo.add_argument("--effort")
    _add_target_flags(p_setrepo)
    p_setrepo.set_defaults(func=cmd_set_repo)

    p_setagent = subs.add_parser(
        "set-agent",
        help="Set per-agent-identity override (models.by_agent.<name>)",
    )
    p_setagent.add_argument("agent")
    p_setagent.add_argument("--model")
    p_setagent.add_argument("--effort")
    _add_target_flags(p_setagent)
    p_setagent.set_defaults(func=cmd_set_agent)

    p_undef = subs.add_parser(
        "unset-default", help="Clear models.default + default_effort",
    )
    _add_target_flags(p_undef)
    p_undef.set_defaults(func=cmd_unset_default)

    p_unrepo = subs.add_parser(
        "unset-repo", help="Remove a per-repo override",
    )
    p_unrepo.add_argument("repo")
    _add_target_flags(p_unrepo)
    p_unrepo.set_defaults(func=cmd_unset_repo)

    p_unagent = subs.add_parser(
        "unset-agent", help="Remove a per-agent override",
    )
    p_unagent.add_argument("agent")
    _add_target_flags(p_unagent)
    p_unagent.set_defaults(func=cmd_unset_agent)

    args = parser.parse_args(argv)
    return args.func(args)


def _main_legacy(argv: list[str]) -> int:
    """Original shipped / --diff / --suggest behavior (no subcommand)."""
    mode = "shipped"
    for a in argv:
        if a == "--diff":
            mode = "diff"
        elif a == "--suggest":
            mode = "suggest"
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0

    commands = collect(PLUGIN_ROOT / "commands", "command")
    agents = collect(PLUGIN_ROOT / "agents", "agent")

    if mode == "shipped":
        print_shipped(commands, agents)
        return 0

    overrides = load_platform_models()
    if not overrides:
        print("  No `models:` block found in platform.yaml (or PyYAML missing).")
        print("  Showing shipped defaults instead:")
        print_shipped(commands, agents)
        return 0

    if mode == "diff":
        print_shipped(commands, agents)
        print_diff(commands, agents, overrides)
    elif mode == "suggest":
        print_suggestions(commands, overrides)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
