"""`otaman init` (+ `--update`/`--shell`/`companion-repos`) — migrated from main.py.

The last F020 migration. This empties main()'s legacy `commands = {...}`
dict entirely (it held only "init") and the shared flag-parsing loop
that fed it -- both are deleted from main() in this same change, since
cmd_init was their only remaining reader. cmd_init now parses its own
flags and handles the `companion-repos` sub-route internally (main()
previously special-cased `rest[0] == "companion-repos"` before the
loop even ran).

Two functions have external callers outside main.py/commands/, both via
lazy in-function imports that needed repointing here:
  - _cmd_init_update  <- otaman_cli/project/cmd_assign.py
  - _scaffold_launcher_after_init <- otaman_cli/onboard/program_init/runner.py

_normalize_ce_platform_yaml_for_validation stays in main.py: it's
shared with commands/misc_readonly.py's `validate`, not exclusive to
init.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, _normalize_ce_platform_yaml_for_validation, run_script


def _ensure_settings_default_mode(root: Path, config: dict) -> None:
    """Ensure defaultMode: auto is in each repo's .claude/settings.local.json.

    Without this, a per-repo settings.local.json with only an allow list implicitly
    sets everything else to 'ask', overriding the user's global auto mode.
    """
    import json as _json

    for repo in config.get("repos", []):
        repo_path_rel = repo.get("path", "")
        if not repo_path_rel:
            continue
        repo_dir = (root / repo_path_rel).resolve()
        if not repo_dir.is_dir():
            continue
        settings_path = repo_dir / ".claude" / "settings.local.json"
        if not settings_path.is_file():
            continue
        try:
            data = _json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        perms = data.setdefault("permissions", {})
        if perms.get("defaultMode") == "auto":
            continue
        perms["defaultMode"] = "auto"
        settings_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _inject_agent_env_into_command(command: str, owner: str) -> str:
    """Prepend OTAMAN_AGENT=<owner> before the 'claude' invocation in a launch command.

    Idempotent: updates an existing OTAMAN_AGENT=<old> if present.
    Handles the common pattern: 'source ... && claude ...' or bare 'claude ...'.
    """
    if not owner:
        return command
    import re as _re

    # Match optional existing OTAMAN_AGENT=<something> + whitespace preceding 'claude'
    pattern = _re.compile(r"(OTAMAN_AGENT=\S+\s+)?claude\b")
    replacement = f"OTAMAN_AGENT={owner} claude"
    new_cmd, n = pattern.subn(replacement, command, count=1)
    if n == 0:
        return command
    return new_cmd


def _cmd_init_shell() -> int:
    """Install the otaman-agent shell function into ~/.bashrc / ~/.zshrc (D3a)."""
    import os as _os

    shell_bin = _os.environ.get("SHELL", "")
    if "zsh" in shell_bin:
        rc_file = Path.home() / ".zshrc"
    else:
        rc_file = Path.home() / ".bashrc"

    MARKER_START = "# >>> otaman-agent: added by `otaman init --shell` >>>"
    MARKER_END = "# <<< otaman-agent <<<"
    SNIPPET = (
        MARKER_START + "\n"
        "otaman-agent() {\n"
        '  if [ -z "$1" ]; then\n'
        '    echo "OTAMAN_AGENT=${OTAMAN_AGENT:-'
        '(unset; resolving from .otaman or current-agent)}"\n'
        "  else\n"
        '    export OTAMAN_AGENT="$1"\n'
        "  fi\n"
        "}\n" + MARKER_END + "\n"
    )

    UI.header("Otaman Init --shell")
    UI.info(f"Shell config file: {rc_file}")
    print()

    # Check idempotency
    if rc_file.is_file():
        existing = rc_file.read_text(encoding="utf-8")
        if MARKER_START in existing:
            UI.ok("otaman-agent function already installed (idempotent).")
            return 0

    # Consent prompt
    try:
        answer = input(f"Append otaman-agent function to {rc_file}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        UI.muted("Aborted.")
        return 1

    if answer not in ("y", "yes"):
        UI.muted("Aborted — no changes made.")
        return 1

    with open(rc_file, "a", encoding="utf-8") as fh:
        fh.write("\n" + SNIPPET)

    UI.ok(f"Installed otaman-agent function in {rc_file}")
    UI.muted("Reload your shell or run: source " + str(rc_file))
    return 0


def _detect_strategic_agents(doc: dict) -> list[str]:
    """outcome-proposal-routing task 3.3 — detect CPO + cofounder agents.

    Two signals, in priority order (per spec):
      1. Explicit `role:` field on an `agents:[]` entry —
         `role: cpo` is the CPO; `role: cofounder` is the cofounder.
      2. Repo name suffix on `repos:[]` — `-business` repo's owner is the
         CPO; `-strategy` repo's owner is the cofounder.

    Returns ordered, deduplicated list: CPO first (if any), then cofounder
    (if any).  An agent that fills both roles appears once.
    """
    cpo_agent: str | None = None
    cofounder_agent: str | None = None

    # Pass 1 — explicit `role:` on agents[] entries
    agents_field = doc.get("agents")
    if isinstance(agents_field, list):
        for a in agents_field:
            if not isinstance(a, dict):
                continue
            name = a.get("name")
            role = a.get("role")
            if not isinstance(name, str) or not name:
                continue
            if role == "cpo" and cpo_agent is None:
                cpo_agent = name
            elif role == "cofounder" and cofounder_agent is None:
                cofounder_agent = name

    # Pass 2 — repo-name suffix (only fills gaps left by pass 1)
    for r in doc.get("repos") or []:
        if not isinstance(r, dict):
            continue
        rname = r.get("name") or ""
        owner = r.get("owner")
        if not isinstance(rname, str) or not isinstance(owner, str) or not owner:
            continue
        if cpo_agent is None and rname.endswith("-business"):
            cpo_agent = owner
        elif cofounder_agent is None and rname.endswith("-strategy"):
            cofounder_agent = owner

    out: list[str] = []
    if cpo_agent:
        out.append(cpo_agent)
    if cofounder_agent and cofounder_agent not in out:
        out.append(cofounder_agent)
    return out


def _ensure_routing_rules(platform_yaml: Path) -> int:
    """bus-cc-routing task 2.5 — ensure `bus.routing_rules` defaults exist.

    Idempotent: returns 0 if no change was needed, 1 if any rule was added.

    Defaults:
        - `to: human` → cc: [spec-agent]                              (always)
        - `to: human, priority: [high, urgent]` → cc: [cpo-agent]    (only when
          cpo-agent owns any repo, or appears in an `agents:` list)

    Implementation note: uses ruamel.yaml for round-trip preservation when an
    `bus:` block already exists.  When the block is entirely absent, appends
    a plain-text block at end-of-file so existing comments/order/quoting are
    untouched.  Trade-off: when adding a single missing rule to an existing
    block we accept ruamel.yaml's modest re-flow.
    """
    if not platform_yaml.is_file():
        return 0
    text = platform_yaml.read_text(encoding="utf-8")
    try:
        import yaml as _yaml

        doc = _yaml.safe_load(text) or {}
    except Exception:
        return 0
    if not isinstance(doc, dict):
        return 0

    # Determine whether cpo-agent is in scope (repos[].owner or agents[].name)
    has_cpo_agent = False
    for r in doc.get("repos") or []:
        if isinstance(r, dict) and r.get("owner") == "cpo-agent":
            has_cpo_agent = True
            break
    if not has_cpo_agent and isinstance(doc.get("agents"), list):
        for a in doc["agents"]:
            if isinstance(a, dict) and a.get("name") == "cpo-agent":
                has_cpo_agent = True
                break

    spec_rule = {"when": {"to": "human"}, "cc": ["spec-agent"]}
    cpo_rule = {"when": {"to": "human", "priority": ["high", "urgent"]}, "cc": ["cpo-agent"]}
    desired: list[dict] = [spec_rule]
    if has_cpo_agent:
        desired.append(cpo_rule)

    bus_block = doc.get("bus") if isinstance(doc.get("bus"), dict) else {}
    existing_rules = bus_block.get("routing_rules") or []

    def _normalize(rule: object) -> tuple:
        """Canonical key for dedup: (sorted when items, sorted cc)."""
        if not isinstance(rule, dict):
            return ()
        when = rule.get("when") or {}
        cc = rule.get("cc") or []
        when_key = (
            tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in when.items()))
            if isinstance(when, dict)
            else ()
        )
        cc_key = tuple(cc) if isinstance(cc, list) else ()
        return (when_key, cc_key)

    existing_keys = {_normalize(r) for r in existing_rules}
    missing = [r for r in desired if _normalize(r) not in existing_keys]

    # outcome-proposal-routing task 3.4 — upsert rule by `when.type` key.
    # Strategic agents (CPO, cofounder) trigger a `when: {type:
    # outcome-proposal}` rule.  Upsert semantics: if a rule with that
    # `when.type` already exists, REPLACE its `cc:` list to match currently
    # detected agents; otherwise append.  No strategic agents → skip
    # silently (do NOT remove an existing rule).
    strategic_agents = _detect_strategic_agents(doc)
    outcome_existing_idx: int | None = None
    outcome_existing_cc: list = []
    for i, r in enumerate(existing_rules):
        if (
            isinstance(r, dict)
            and isinstance(r.get("when"), dict)
            and r["when"].get("type") == "outcome-proposal"
        ):
            outcome_existing_idx = i
            outcome_existing_cc = list(r.get("cc") or [])
            break

    outcome_action: str | None = None
    if strategic_agents:
        if outcome_existing_idx is None:
            outcome_action = "append"
        elif outcome_existing_cc != strategic_agents:
            outcome_action = "replace"
        # else: rule exists and cc matches — no-op

    if not missing and outcome_action is None:
        return 0

    # Path A — no `bus:` block at all: append plain-text YAML at EOF for
    # zero formatting impact on the rest of the file.  Includes the
    # outcome-proposal rule when present (always an "append" here since
    # the block didn't exist).
    if "bus" not in doc:
        all_to_append = list(missing)
        if outcome_action == "append":
            all_to_append.append(
                {
                    "when": {"type": "outcome-proposal"},
                    "cc": list(strategic_agents),
                }
            )
        appended_lines = [
            "",
            "# bus-cc-routing — default routing rules generated by `otaman init`",
            "bus:",
            "  routing_rules:",
        ]
        for r in all_to_append:
            when_keys = list(r["when"].items())
            appended_lines.append("    - when:")
            for k, v in when_keys:
                if isinstance(v, list):
                    appended_lines.append(f"        {k}: [{', '.join(str(x) for x in v)}]")
                else:
                    appended_lines.append(f"        {k}: {v}")
            appended_lines.append(f"      cc: [{', '.join(r['cc'])}]")
        suffix = "\n".join(appended_lines).rstrip() + "\n"
        if not text.endswith("\n"):
            text += "\n"
        platform_yaml.write_text(text + suffix, encoding="utf-8")
        return 1

    # Path B — `bus:` exists, need to add missing rule(s) and/or upsert
    # the outcome-proposal rule.  Round-trip via ruamel.yaml.  Acceptable
    # re-flow tradeoff (rare path).
    try:
        import io as _io

        from ruamel.yaml import YAML as _RuamelYAML

        rt = _RuamelYAML()
        rt.preserve_quotes = True
        rt.indent(mapping=2, sequence=4, offset=2)
        rt.width = 120
        doc_rt = rt.load(text) or {}
        if "bus" not in doc_rt:
            doc_rt["bus"] = {}
        if not isinstance(doc_rt["bus"].get("routing_rules"), list):
            doc_rt["bus"]["routing_rules"] = []
        rules_rt = doc_rt["bus"]["routing_rules"]
        # Append the bus-cc-routing defaults that were missing
        for r in missing:
            rules_rt.append(r)
        # Apply outcome-proposal upsert
        if outcome_action == "append":
            rules_rt.append(
                {
                    "when": {"type": "outcome-proposal"},
                    "cc": list(strategic_agents),
                }
            )
        elif outcome_action == "replace" and outcome_existing_idx is not None:
            # Replace the cc: list on the existing rule, leaving when:
            # intact (preserves any extra fields a future spec might add).
            rules_rt[outcome_existing_idx]["cc"] = list(strategic_agents)
        out = _io.StringIO()
        rt.dump(doc_rt, out)
        platform_yaml.write_text(out.getvalue(), encoding="utf-8")
        return 1
    except Exception:
        return 0


def _cmd_init_update(dry_run: bool = False) -> int:
    """Patch .otaman agent: fields + regenerate launch commands across all repos (--update, D5).

    destructive-command-safety task 1.3: dry_run previously reached
    cmd_init's own flag parsing but was never passed into this function —
    the flag was silently discarded. Now threaded through every mutating
    code path below (each .otaman marker write, the platform.yaml launch-
    command patch, the meta marker write, and the two nested helper calls
    _ensure_settings_default_mode/_ensure_routing_rules), not just the
    top-level function -- a --dry-run that reaches only the entrypoint but
    not a called mutation helper is the exact failure mode being fixed.
    """
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        UI.error(f"platform.yaml not found at {platform_yaml}")
        return 2

    try:
        import yaml as _yaml

        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as e:
        UI.error(f"Failed to read platform.yaml: {e}")
        return 2

    UI.header("Otaman Init --update" + (" (dry-run)" if dry_run else ""))
    updated = 0
    skipped = 0
    launch_updated = 0

    for repo in config.get("repos", []):
        repo_path_rel = repo.get("path", "")
        owner = repo.get("owner", "")
        name = repo.get("name", repo_path_rel)
        if not repo_path_rel:
            continue
        repo_dir = (root / repo_path_rel).resolve()
        if not repo_dir.is_dir():
            UI.muted(f"  skip {name}: directory not found")
            skipped += 1
            continue

        # Patch .otaman agent: field
        marker = repo_dir / ".otaman"
        if marker.is_file():
            existing = marker.read_text(encoding="utf-8")
        else:
            import os as _os

            rel = _os.path.relpath(root.resolve(), repo_dir)
            rel_posix = Path(rel).as_posix()
            existing = "# Path to otaman folder" + chr(10) + rel_posix + chr(10)

        lines_c = existing.splitlines()
        has_agent = any(line_c.strip().startswith("agent:") for line_c in lines_c)
        if has_agent:
            new_l = []
            for line_c in lines_c:
                if line_c.strip().startswith("agent:") and owner:
                    new_l.append("agent: " + owner)
                else:
                    new_l.append(line_c)
            new_content = chr(10).join(new_l) + chr(10)
        else:
            agent_line = ("agent: " + owner + chr(10)) if owner else ""
            new_content = existing.rstrip(chr(10)) + chr(10) + agent_line

        if dry_run:
            UI.muted(
                f"  would update {name}/.otaman" + ((" (agent: " + owner + ")") if owner else "")
            )
        else:
            marker.write_text(new_content, encoding="utf-8")
            UI.ok(name + "/.otaman updated" + ((" (agent: " + owner + ")") if owner else ""))
        updated += 1

        # Count repos whose launch commands would change (D4).
        # Mutation happens below via in-place text patching, not by mutating
        # the parsed `config` dict — yaml.dump would alphabetize keys, drop
        # comments, and break downstream parsers (notably the launcher).
        if owner and isinstance(repo.get("launch"), dict):
            cmds = repo["launch"].get("commands", [])
            if any(_inject_agent_env_into_command(c, owner) != c for c in cmds):
                launch_updated += 1
                UI.muted(f"  {name}: launch commands will be updated with OTAMAN_AGENT={owner}")

    # Write back platform.yaml via in-place text patch so original key order,
    # comments, and quoting style are preserved. We walk the raw lines and
    # track current repo's owner via `  owner: <name>` markers; any line in
    # the same block containing `claude` gets the OTAMAN_AGENT prefix applied.
    if launch_updated > 0:
        if dry_run:
            UI.muted(
                f"  would update platform.yaml ({launch_updated} repo(s) launch commands patched)"
            )
        else:
            try:
                original_text = platform_yaml.read_text(encoding="utf-8")
                owner_line_pat = re.compile(r"^\s+owner:\s*(\S+)")
                current_owner = None
                new_lines = []
                for line in original_text.splitlines(keepends=True):
                    m = owner_line_pat.match(line)
                    if m:
                        current_owner = m.group(1)
                    if current_owner and "claude" in line:
                        line = _inject_agent_env_into_command(line, current_owner)
                    new_lines.append(line)
                platform_yaml.write_text("".join(new_lines), encoding="utf-8")
                UI.ok(f"platform.yaml updated ({launch_updated} repo(s) launch commands patched)")
            except Exception as e:
                UI.warn(f"Failed to write platform.yaml: {e}")

    meta_marker = root / ".otaman"
    if meta_marker.is_file():
        existing = meta_marker.read_text(encoding="utf-8")
        has_agent = any(line.strip().startswith("agent:") for line in existing.splitlines())
        if not has_agent:
            if dry_run:
                UI.muted("  would update otaman-meta/.otaman (agent: human)")
            else:
                meta_marker.write_text(
                    existing.rstrip(chr(10)) + chr(10) + "agent: human" + chr(10), encoding="utf-8"
                )
                UI.ok("otaman-meta/.otaman updated (agent: human)")
            updated += 1
        else:
            UI.muted("otaman-meta/.otaman already has agent: field")
    elif meta_marker.is_dir():
        # Directory-shape .otaman (otaman-meta legacy case): write .otaman/agent
        if dry_run:
            UI.muted("  would write otaman-meta/.otaman/agent (agent: human)")
        else:
            agent_file = meta_marker / "agent"
            agent_file.write_text("human" + chr(10), encoding="utf-8")
            UI.ok("otaman-meta/.otaman/agent written (agent: human)")
        updated += 1
    else:
        UI.muted("otaman-meta/.otaman not found")

    if dry_run:
        UI.muted("  would check .claude/settings.local.json defaultMode across repos")
        UI.muted("  would check platform.yaml bus.routing_rules defaults")
    else:
        # Ensure defaultMode: auto in each repo's settings.local.json
        _ensure_settings_default_mode(root, config)

        # bus-cc-routing task 2.5 — ensure routing_rules defaults exist in platform.yaml
        if _ensure_routing_rules(platform_yaml):
            UI.ok("platform.yaml: bus.routing_rules defaults added")

    # external-audit gate blocker 2/2 (spec-agent 20260818T195901): --update
    # is the documented headless step-2 command (works from any repo dir via
    # the .otaman-marker root chain, no TTY), but it never invoked the
    # generator that writes each repo's orchestration rules — post plugin
    # af48483, that means CLAUDE.local.md was never (re)generated by the
    # command the migration gate tells owners to run. Regenerate here; the
    # generator resolves everything from the platform.yaml path, cwd-free.
    print()
    if dry_run:
        UI.muted("  would regenerate agent config (generate-agent-config.py)")
    else:
        print("Regenerating agent config (queues, ownership, CLAUDE.local.md rules)...")
        gen = run_script("generate-agent-config.py", str(platform_yaml))
        if gen.returncode != 0:
            UI.warn("generate-agent-config failed — marker/launch patches above still applied")
            return gen.returncode

    print()
    UI.kv("Updated", str(updated))
    UI.kv("Skipped", str(skipped))
    if dry_run:
        UI.warn("(dry run — no changes made)")
    return 0


def _detect_sibling_git_repos(cwd: Path) -> list[Path]:
    """Find git repos one level up from *cwd* (excluding cwd itself)."""
    parent = cwd.parent
    if parent == cwd:
        return []
    repos: list[Path] = []
    try:
        for child in parent.iterdir():
            if child == cwd or not child.is_dir():
                continue
            if (child / ".git").exists():
                repos.append(child)
    except OSError:
        return []
    return repos


def _detect_scan_draft(cwd: Path) -> list[Path]:
    """Find ``<subdir>/platform.yaml.draft`` files directly under *cwd*.

    Returned paths point at the draft file itself (not the parent dir).
    Drafts are produced by ``otaman scan`` and live in the
    ``<program>-otaman/`` subdir by convention.
    """
    drafts: list[Path] = []
    try:
        for child in cwd.iterdir():
            if not child.is_dir():
                continue
            candidate = child / "platform.yaml.draft"
            if candidate.is_file():
                drafts.append(candidate)
    except OSError:
        return []
    return drafts


def _init_preflight(args: list[str]) -> int | None:
    """Detect state and route bare `otaman init` to scan or program-init wizard.

    Returns:
        - None: pre-flight skipped or passed; cmd_init should continue normally
        - int: pre-flight handled the command; cmd_init should return this value
    """
    # Only pre-flight bare `otaman init` (no explicit config arg)
    if args:
        return None

    cwd = Path.cwd()
    if (cwd / "platform.yaml").exists():
        return None  # normal init path will pick it up

    # Smart pickup: an `otaman scan` left a draft in <subdir>/platform.yaml.draft.
    # Recognise it so the user doesn't have to manually `mv` before re-running.
    drafts = _detect_scan_draft(cwd)
    if len(drafts) == 1 and sys.stdin.isatty():
        draft_path = drafts[0]
        rel = draft_path.relative_to(cwd)
        print()
        print(f"  Found scan draft: ./{rel}")
        try:
            answer = input("  Promote to platform.yaml and finalize init? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if answer in ("", "y", "yes"):
            target = draft_path.with_name("platform.yaml")
            if target.exists():
                UI.error(f"{target} already exists; refusing to overwrite.")
                UI.muted("Resolve manually or delete the existing file and re-run.")
                return 1
            draft_path.rename(target)
            UI.ok(f"Promoted draft → {target}")
            # Smart-init was invoked from the parent dir (sibling to the meta
            # folder). Downstream steps (`_cmd_init_update`, generate-agent-
            # config) use cwd-walk to find the project root; chdir into the
            # meta folder so they resolve correctly.
            os.chdir(target.parent)
            return cmd_init([str(target)])
        # else: user declined; fall through to existing options
    elif len(drafts) > 1 and sys.stdin.isatty():
        # Multiple drafts — don't auto-pick. Surface them for the user.
        print()
        UI.warn(f"Found {len(drafts)} scan drafts; not sure which to use:")
        for d in drafts:
            UI.muted(f"  {d.relative_to(cwd)}")
        UI.muted("Run `otaman init <path-to-draft>` explicitly, or move one of ")
        UI.muted("them to ./platform.yaml first.")

    # Non-TTY: print improved error and exit
    if not sys.stdin.isatty():
        UI.error("No platform.yaml found.")
        UI.muted("Interactive setup unavailable (non-TTY). Create platform.yaml first:")
        UI.muted("  otaman scan .                  — detect existing repos + draft config")
        UI.muted("  otaman init                    — interactive wizard (run from a TTY)")
        if drafts:
            UI.muted("")
            UI.muted("  Existing scan draft(s) found:")
            for d in drafts:
                UI.muted(f"    {d.relative_to(cwd)}")
            UI.muted("  Finalize one with: mv <draft> <draft-dir>/platform.yaml")
        return 2

    sibling_repos = _detect_sibling_git_repos(cwd)
    cwd_is_git = (cwd / ".git").exists()

    print()
    if sibling_repos:
        n = len(sibling_repos)
        suffix = "" if n == 1 else "s"
        print(f"  Found {n} git repo{suffix} in parent directory.")
        try:
            answer = input("  Scan and generate platform.yaml from them? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if answer in ("", "y", "yes"):
            from otaman_cli.commands.scan import cmd_scan

            return cmd_scan([str(cwd)])
        # User declined scan; fall through to wizard prompt

    print("  No platform.yaml found.")
    try:
        answer = input("  Start a new project with the interactive wizard? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if answer not in ("", "y", "yes"):
        UI.muted("Run `otaman init` again when you're ready.")
        return 0

    # Build a Namespace matching the program-init parser's expectations
    import argparse as _argparse

    from otaman_cli.onboard.program_init import run_program_init

    ns = _argparse.Namespace(
        program=None,
        questions_yaml=None,
        mode=None,
        dry_run=False,
        output_dir=None,
    )

    # Pass single-repo hint to the wizard via env var (task 1.3)
    prior_hint = os.environ.get("OTAMAN_INIT_CWD_IS_GIT")
    if cwd_is_git:
        os.environ["OTAMAN_INIT_CWD_IS_GIT"] = "1"
    try:
        return run_program_init(ns)
    finally:
        if prior_hint is None:
            os.environ.pop("OTAMAN_INIT_CWD_IS_GIT", None)
        else:
            os.environ["OTAMAN_INIT_CWD_IS_GIT"] = prior_hint


def cmd_init_companion_repos(rest: list[str]) -> int:
    """`otaman init companion-repos` — CE-mode in-process scaffolder.

    Flags:
        --program SLUG         Program slug (default: from platform.yaml in cwd)
        --repos KIND[,KIND]    Kinds to scaffold (business, strategy);
                               default: derived from program.processes
        --dry-run              Print plan; no filesystem writes
        --force                Re-scaffold even if target exists (with confirmation)
    """
    from otaman_cli.onboard.scaffold_ce import (
        ScaffoldError,
        scaffold_companion_repos_ce,
    )

    # Parse flags from the raw rest
    program = None
    repos_arg: str | None = None
    dry_run = False
    force = False
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--program" and i + 1 < len(rest):
            program = rest[i + 1]
            i += 2
        elif token == "--repos" and i + 1 < len(rest):
            repos_arg = rest[i + 1]
            i += 2
        elif token in ("--dry-run", "--check"):
            dry_run = True
            i += 1
        elif token == "--force":
            force = True
            i += 1
        else:
            i += 1

    # Locate the meta dir (where platform.yaml lives)
    root = find_project_root()
    if root is None:
        UI.error("No platform.yaml found in cwd or any ancestor.")
        UI.muted("Run `otaman init` first to create the program.")
        return 2
    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        UI.error(f"platform.yaml not found at {platform_yaml}")
        return 2

    # Read program slug + processes from platform.yaml when --program not given
    try:
        import yaml as _yaml

        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        UI.error(f"Failed to read platform.yaml: {exc}")
        return 2

    if program is None:
        program = config.get("project") or config.get("program", {}).get("slug")
        if not program:
            UI.error("Could not infer program slug from platform.yaml.")
            UI.muted("Pass --program SLUG explicitly.")
            return 2

    # Derive repo kinds: explicit --repos > processes-based default
    repo_kinds: list[str] | None = None
    if repos_arg:
        repo_kinds = [r.strip() for r in repos_arg.split(",") if r.strip()]
        if repo_kinds == ["all"]:
            repo_kinds = ["business", "strategy"]

    processes_raw = config.get("processes") or {}
    if isinstance(processes_raw, dict):
        processes = [k for k, v in processes_raw.items() if v]
    elif isinstance(processes_raw, list):
        processes = list(processes_raw)
    else:
        processes = []

    program_name = config.get("description") or config.get("project") or program

    # --force prompt (skipped on non-TTY or --dry-run)
    if force and not dry_run and sys.stdin.isatty():
        UI.warn(f"--force will REMOVE existing companion repo directories for {program}.")
        try:
            answer = input("  Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            UI.muted("Aborted.")
            return 0

    UI.header("Scaffold companion repos" + (" (dry-run)" if dry_run else ""))
    try:
        result = scaffold_companion_repos_ce(
            program_slug=program,
            processes=processes,
            meta_dir=root,
            program_name=program_name,
            force=force,
            dry_run=dry_run,
            repo_kinds=repo_kinds,
        )
    except ScaffoldError as exc:
        UI.error(str(exc))
        return 1

    if not result.repos:
        UI.muted("No companion repos to scaffold (no qualifying processes enabled).")
        return 0

    for repo in result.created:
        marker = "would create" if dry_run else "Scaffolded"
        UI.ok(f"{marker} {repo.kind} at {repo.path} (owner: {repo.owner})")
    for repo in result.skipped:
        UI.muted(f"Skipped {repo.path} — {repo.skipped_reason}")

    if dry_run:
        UI.muted("Dry-run: no files written. Re-run without --dry-run to apply.")
    elif result.platform_yaml_updated:
        UI.ok("platform.yaml repos[] updated")
    return 0


def _scaffold_launcher_after_init(platform_yaml: Path, *, yes: bool) -> None:
    """Run the launcher scaffolder (otaman-init-dev-scaffold spec) after the
    platform-init step succeeds.  Re-runs are idempotent — overwrites the
    generated files; the gitignored local-overrides file is preserved if it
    already has user content (the wizard's example is only emitted on first
    init when no local file exists).
    """
    import yaml as _yaml

    from otaman_cli.init.generator import generate as _generate
    from otaman_cli.init.wizard import run_wizard as _run_wizard

    output_dir = platform_yaml.parent / "launcher"

    # Derive project name + agent names from the just-validated platform.yaml
    try:
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        config = {}
    project_name = str(config.get("project") or "otaman-project")
    agent_names: list[str] = []
    # agent -> repo path relative to the meta dir (first owned repo wins) —
    # the launch scripts cd each pane into its agent's repo before claude.
    agent_repos: dict[str, str] = {}
    for r in config.get("repos") or []:
        if isinstance(r, dict) and r.get("owner"):
            owner = str(r["owner"])
            if owner and owner not in agent_names:
                agent_names.append(owner)
            repo_path = r.get("path")
            if owner and owner not in agent_repos and isinstance(repo_path, str) and repo_path:
                agent_repos[owner] = repo_path

    # otaman-init-dev-scaffold amendment #2: detect the orchestration
    # meta-agent declared in platform.yaml (agents[*].role == "orchestration")
    # and pre-populate it as a locked enabled entry alongside spec-agent.
    # Graceful no-op when platform.yaml has no `agents:` list yet (current
    # schema state — the field is on its way via a separate spec change).
    meta_agent_name: str | None = None
    agents_field = config.get("agents")
    if isinstance(agents_field, list):
        for a in agents_field:
            if isinstance(a, dict) and a.get("role") == "orchestration":
                name = a.get("name")
                if isinstance(name, str) and name:
                    meta_agent_name = name
                    break

    print()
    if yes:
        UI.muted("Generating launcher/ (--yes; all defaults)")
    else:
        UI.header("Launcher scaffold (otaman-init-dev-scaffold)")
    settings = _run_wizard(
        project_name=project_name,
        platform_agent_names=agent_names,
        meta_agent_name=meta_agent_name,
        yes=yes,
    )

    # Preserve an existing launch-settings.local.yaml (user may have customised it);
    # the generator always overwrites the commented example.
    local_path = output_dir / "launch-settings.local.yaml"
    preserved_local: str | None = None
    if local_path.is_file():
        text = local_path.read_text(encoding="utf-8")
        # If file has any non-comment content, preserve it
        live = any(line.strip() and not line.strip().startswith("#") for line in text.splitlines())
        if live:
            preserved_local = text

    result = _generate(
        settings, output_dir, platform_yaml_source=platform_yaml, agent_repos=agent_repos
    )
    if preserved_local is not None:
        local_path.write_text(preserved_local, encoding="utf-8")
        UI.muted(f"  preserved existing {local_path.name} (had user content)")

    UI.ok(f"launch-settings.yaml      {result.settings_yaml.relative_to(output_dir.parent)}")
    UI.ok(
        f"launch-settings.local.yaml {result.local_example.relative_to(output_dir.parent)}"
        if preserved_local is None
        else ""
    )
    UI.ok(
        f"launch.sh                  {result.launch_sh.relative_to(output_dir.parent)} (chmod +x)"
    )
    UI.ok(f"launch.ps1                 {result.launch_ps1.relative_to(output_dir.parent)}")
    if result.platform_yaml_copy is not None:
        UI.ok(
            f"platform.yaml              {result.platform_yaml_copy.relative_to(output_dir.parent)}"
        )
    UI.ok(f".gitignore                 {result.gitignore.relative_to(output_dir.parent)}")


def cmd_init(args: list[str]) -> int:
    """Initialize an otaman project. Creates platform.yaml if none exists.

    With no platform.yaml in cwd, detects context and routes to:
      - `otaman scan .` if git repos are detected one level up
      - the interactive program-init wizard otherwise
    Non-TTY stdin skips routing and prints an instructional error.

    With --update: patches existing .otaman files across all platform repos
    to write the agent: <owner> field and regenerates launch commands with
    OTAMAN_AGENT=<owner> prefix.  Safe to run multiple times (idempotent).

    With --shell: installs the otaman-agent shell function into ~/.bashrc or
    ~/.zshrc after explicit consent.
    """
    # `otaman init companion-repos` — sub-action with its own flags
    # (--program / --repos / --dry-run / --force), dispatched before the
    # generic flag parsing below (previously a main()-level pre-dispatch
    # special-case ahead of the shared flag loop).
    if args and args[0] == "companion-repos":
        return cmd_init_companion_repos(args[1:])

    update = False
    dry_run = False
    skip_doctor = False
    shell = False
    yes = False
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--update":
            update = True
            i += 1
        elif args[i] == "--shell":
            shell = True
            i += 1
        elif args[i] in ("--yes", "-y"):
            yes = True
            i += 1
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif args[i] == "--skip-doctor":
            skip_doctor = True
            i += 1
        elif args[i].startswith("-"):
            i += 1  # skip unknown flags
        else:
            positional.append(args[i])
            i += 1
    args = positional

    # --shell mode: install shell function and exit
    if shell:
        return _cmd_init_shell()

    # --update mode: patch .otaman agent: fields across all repos and exit
    if update:
        return _cmd_init_update(dry_run=dry_run)

    # Pre-flight: smart-init routing when no platform.yaml present (task 1.1, 1.2)
    preflight_rc = _init_preflight(args)
    if preflight_rc is not None:
        return preflight_rc

    config = args[0] if args else "platform.yaml"
    config_path = Path(config).resolve()

    if not config_path.exists():
        UI.error(f"Config not found: {config_path}")
        UI.muted("Run 'otaman scan' first to generate a config, or copy the template.")
        return 2

    if dry_run:
        UI.header("Otaman Init (dry-run)")
    else:
        UI.header("Otaman Init")

    # Validate first.
    # ce-org-agent-bootstrap task 4.1 — accept CE-shaped platform.yaml by
    # normalizing in-memory before validation (alias agent→owner; infer
    # project from parent dir; default version=1.0).  Hints printed but
    # the on-disk file is not rewritten.
    print(f"Validating {config_path.name}...")
    norm_path, hints = _normalize_ce_platform_yaml_for_validation(config_path)
    if hints:
        for h in hints:
            UI.muted(f"  hint: {h}")
    result = run_script("validate-platform.py", str(norm_path), capture=True)
    if norm_path != config_path:
        try:
            norm_path.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        UI.error((result.stdout or "") + (result.stderr or "") or "validate failed (no output)")
        return result.returncode
    UI.ok("Valid")
    print()

    # Generate
    if dry_run:
        print("Generating agent infrastructure [dry-run]...")
    else:
        print("Generating agent infrastructure...")
    script_args = [str(config_path)]
    if dry_run:
        script_args.append("--dry-run")
    result = run_script("generate-agent-config.py", *script_args)
    if result.returncode != 0:
        return result.returncode

    if dry_run:
        print()
        UI.muted("[dry-run] No files written. Re-run without --dry-run to apply.")
        return 0

    # Write agent: fields to each repo's .otaman (task 2.5 — same logic as --update)
    print()
    print("Writing agent: fields to repo .otaman files...")
    _cmd_init_update()

    # otaman-init-dev-scaffold: generate launcher/ folder alongside platform.yaml
    # (launch-settings.yaml + launch-settings.local.yaml + launch.sh + launch.ps1
    # + .gitignore).  Prompts for connection mode + agents + tmux layout unless
    # --yes is passed.
    try:
        _scaffold_launcher_after_init(config_path, yes=yes)
    except Exception as _scaffold_exc:
        UI.warn(f"Launcher scaffold skipped: {_scaffold_exc}")

    if skip_doctor:
        print()
        UI.muted("Skipped doctor check (--skip-doctor). Run `otaman doctor` to verify environment.")
        return 0

    # Run doctor check
    print()
    from otaman_cli.commands.doctor import cmd_doctor

    return cmd_doctor([str(config_path.parent)])


register(
    CommandSpec(
        name="init",
        handler=cmd_init,
        help="Initialize an otaman project (creates platform.yaml if none exists)",
    )
)
