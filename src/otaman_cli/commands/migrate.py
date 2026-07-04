"""`otaman migrate` — migrated from main.py.

DESTRUCTIVE-CROSS-DIRECTORY (destructive-command-safety design.md):
resolves a project root by upward path-walking, then moves/deletes
across it and git-inits a new repo -- exactly the incident this spec
change addresses (2026-07-04 `otaman migrate` incident). Tasks 1.2/1.5:
identity echo, confirm gate (or --yes), and a --dry-run it previously
had none of.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import C, UI
from otaman_cli.safety import confirm_destructive_operation

_ARTIFACT_NAMES = ("platform.yaml", ".agents", ".claude")
_SCRIPT_NAMES = ("launch-agents.ps1", "launch-agents.sh", "LAUNCH-AGENTS.md")


def cmd_migrate(args: list[str]) -> int:
    """Migrate existing otaman deployment to a dedicated otaman folder."""
    dry_run = False
    yes = False
    positional: list[str] = []
    for a in args:
        if a == "--dry-run":
            dry_run = True
        elif a in ("--yes", "-y"):
            yes = True
        else:
            positional.append(a)
    args = positional

    UI.header("Otaman Migrate" + (" (dry-run)" if dry_run else ""))

    # Find existing project root (old layout: platform.yaml in a non-git parent)
    root = find_project_root()
    if not root:
        UI.error("No platform.yaml or .agents/ found")
        UI.muted("Run from within an existing otaman-managed project.")
        return 1

    # Check if already in a git repo (might already be migrated)
    git_dir = root / ".git"
    if git_dir.is_dir():
        UI.warn(f"{root} already has a .git/ directory")
        print(f"  This may already be a dedicated otaman folder. Proceed with caution.\n")

    # Determine otaman folder name
    config_path = root / "platform.yaml"
    if not config_path.exists():
        UI.error(f"platform.yaml not found at {root}")
        return 1

    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        project_name = config.get("project", root.name)
    except Exception:
        config = {}
        project_name = root.name
    repo_count = len(config.get("repos", []) or [])

    if args:
        maestro_name = args[0]
    else:
        maestro_name = f"{project_name}-otaman"

    maestro_dir = root / maestro_name
    if maestro_dir.exists() and any(maestro_dir.iterdir()):
        UI.error(f"{maestro_dir} already exists and is not empty")
        return 1

    # Identity echo (task 1.2): resolved root + project name + repo count,
    # before any mutation and before the dry-run/confirm branch below --
    # this is the check that would have caught the incident in the first
    # second ("Migrating otaman-meta (project: otaman, 16 repos) -- is
    # this the project you meant?").
    UI.kv("Migrating from", str(root), C.BOLD)
    UI.kv("  project", project_name)
    UI.kv("  repos", str(repo_count))
    UI.kv("Otaman folder", str(maestro_dir), C.BOLD)
    print()

    # Read-only pre-check of what would move, before creating anything --
    # used for both the dry-run report and the confirm-gate's "nothing to
    # migrate" short-circuit, without duplicating the move logic below.
    would_move = [n for n in _ARTIFACT_NAMES + _SCRIPT_NAMES if (root / n).exists()]
    if not would_move:
        UI.warn(f"Nothing to migrate — no otaman artifacts found at {root}")
        return 1

    if dry_run:
        UI.muted("DRY RUN — preview only, nothing will execute")
        for name in would_move:
            UI.muted(f"  would move {name}")
        UI.muted(f"  would rewrite repo paths in {maestro_name}/platform.yaml (./repo -> ../repo)")
        UI.muted(f"  would git init + commit in {maestro_name}/")
        UI.muted("  would write .otaman markers in each repo")
        return 0

    if not confirm_destructive_operation(
        f"This will move {len(would_move)} item(s) out of {root} into a new "
        f"dedicated folder and git-init it:",
        [f"{root} -> {maestro_dir}"],
        yes=yes,
    ):
        UI.muted("Aborted — no changes made.")
        return 1

    # Create otaman folder
    maestro_dir.mkdir(parents=True, exist_ok=True)

    # Move artifacts
    moved: list[str] = []
    for item_name in _ARTIFACT_NAMES:
        src = root / item_name
        dst = maestro_dir / item_name
        if src.exists():
            import shutil
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
                shutil.rmtree(str(src))
            else:
                shutil.copy2(str(src), str(dst))
                src.unlink()
            moved.append(item_name)
            UI.ok(f"Moved {item_name}")

    # Move launch scripts
    for pattern in _SCRIPT_NAMES:
        src = root / pattern
        dst = maestro_dir / pattern
        if src.exists():
            import shutil
            shutil.copy2(str(src), str(dst))
            src.unlink()
            moved.append(pattern)
            UI.ok(f"Moved {pattern}")

    # Rewrite repo paths in platform.yaml: ./repo -> ../repo
    new_config_path = maestro_dir / "platform.yaml"
    content = new_config_path.read_text(encoding="utf-8")
    # Replace ./repo paths with ../repo (otaman folder is now one level deeper)
    import re
    content = re.sub(r'path:\s*\./([^\s]+)', r'path: ../\1', content)
    # Also fix specs.path if it points to a sibling
    content = re.sub(r'(specs:\s*\n\s*path:\s*)\./([^\s]+)', r'\1../\2', content)
    new_config_path.write_text(content, encoding="utf-8")
    UI.ok("Rewrote repo paths in platform.yaml (./repo -> ../repo)")

    # Git init
    subprocess.run(["git", "init", str(maestro_dir)], capture_output=True)
    UI.ok(f"Initialized git repo in {maestro_name}/")

    # Generate .gitignore
    gitignore = maestro_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Runtime artifacts (not versioned)\n"
            ".agents/bus/\n"
            ".agents/blocked/\n"
            ".agents/queue/\n"
            ".agents/sessions/\n"
            ".agents/current-agent\n",
            encoding="utf-8",
        )
        UI.ok("Created .gitignore")

    # Write .otaman markers in each repo (includes agent: <owner> field per D2)
    try:
        with open(new_config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for repo in config.get("repos", []):
            repo_dir = (maestro_dir / repo["path"]).resolve()
            owner = repo.get("owner", "")
            if repo_dir.is_dir():
                rel = os.path.relpath(maestro_dir.resolve(), repo_dir)
                rel_posix = Path(rel).as_posix()
                marker = repo_dir / ".otaman"
                agent_line = ("agent: " + owner + chr(10)) if owner else ""
                marker.write_text(
                    f"# Path to otaman folder\n{rel_posix}\n{agent_line}",
                    encoding="utf-8",
                )
                # Append to repo .gitignore
                gi = repo_dir / ".gitignore"
                needs_entry = True
                if gi.exists():
                    gi_content = gi.read_text(encoding="utf-8")
                    if ".otaman" in gi_content.splitlines():
                        needs_entry = False
                if needs_entry:

                    with open(gi, "a", encoding="utf-8") as f:
                        f.write(chr(10) + ".otaman" + chr(10))

                label = f" (agent: {owner})" if owner else ""
                UI.ok(f"Marker {repo['name']}/.otaman -> {rel_posix}{label}")
        # Also write agent: human to otaman-meta itself (D5)
        meta_marker = maestro_dir / ".otaman"
        if meta_marker.exists():
            existing = meta_marker.read_text(encoding="utf-8")
            if "agent:" not in existing:
                meta_marker.write_text(existing.rstrip() + chr(10) + "agent: human" + chr(10), encoding="utf-8")


    except Exception as e:
        UI.warn(f"Could not write .otaman markers: {e}")

    # Initial commit
    subprocess.run(
        ["git", "-C", str(maestro_dir), "add", "-A"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(maestro_dir), "commit", "-m", "Initial otaman migration"],
        capture_output=True,
    )
    UI.ok("Committed initial state")

    print()
    UI.ok("Migration complete!")
    UI.muted("Next steps:")
    UI.muted(f"  1. cd {maestro_dir}")
    UI.muted("  2. Review platform.yaml (verify repo paths)")
    UI.muted("  3. otaman init  (reinstall hooks with new paths)")
    UI.muted("  4. Launch agents from the otaman folder")
    return 0


register(CommandSpec(
    name="migrate",
    handler=cmd_migrate,
    help="Migrate legacy layout to dedicated otaman folder",
))
