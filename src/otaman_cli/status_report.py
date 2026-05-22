#!/usr/bin/env python3
"""Generate cross-repo status report for an otaman-managed project.

Usage:
    python status-report.py [project-root] [repo-filter]

Output:
    JSON status report to stdout.

Exit codes:
    0 — success
    1 — not an otaman project (no .agents/)
    2 — error
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def run_git(repo_path: Path, *args: str) -> str:
    """Run a git command in a repo and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def get_repo_git_status(repo_path: Path) -> dict[str, Any]:
    """Get git status for a single repo."""
    if not repo_path.is_dir():
        return {"exists": False}

    if not (repo_path / ".git").exists():
        return {"exists": True, "is_git": False}

    branch = run_git(repo_path, "branch", "--show-current") or "detached"
    status_output = run_git(repo_path, "status", "--porcelain")
    modified = len([l for l in status_output.splitlines() if l.strip()]) if status_output else 0

    # Ahead/behind
    ahead = 0
    behind = 0
    ab_output = run_git(repo_path, "rev-list", "--left-right", "--count", f"{branch}...origin/{branch}")
    if ab_output and "\t" in ab_output:
        parts = ab_output.split("\t")
        ahead = int(parts[0])
        behind = int(parts[1])

    # Last commit
    last_commit = run_git(repo_path, "log", "-1", "--format=%h %s", "--no-decorate")

    # Recent log (last 5)
    recent_log = run_git(repo_path, "log", "-5", "--format=%h %ar %s", "--no-decorate")

    return {
        "exists": True,
        "is_git": True,
        "branch": branch,
        "modified_files": modified,
        "clean": modified == 0,
        "ahead": ahead,
        "behind": behind,
        "last_commit": last_commit,
        "recent_log": recent_log.splitlines() if recent_log else [],
    }


def count_bus_messages(bus_dir: Path, agent_filter: str | None = None) -> dict[str, int]:
    """Count bus messages by status."""
    counts = {"pending": 0, "read": 0, "resolved": 0}
    if not bus_dir.is_dir():
        return counts

    for f in bus_dir.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            # Parse frontmatter
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue

            if agent_filter and fm.get("to") != agent_filter and fm.get("to") != "all":
                continue

            status = fm.get("status", "pending")
            if status in counts:
                counts[status] += 1
        except (OSError, yaml.YAMLError):
            continue

    return counts


def get_pending_messages(bus_dir: Path) -> list[dict[str, Any]]:
    """Get details of pending messages."""
    messages = []
    if not bus_dir.is_dir():
        return messages

    for f in sorted(bus_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict) or fm.get("status") != "pending":
                continue

            # Extract subject line
            subject = ""
            for line in content.split("---", 2)[-1].splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break

            messages.append({
                "id": fm.get("id", ""),
                "from": fm.get("from", ""),
                "to": fm.get("to", ""),
                "priority": fm.get("priority", "normal"),
                "type": fm.get("type", ""),
                "subject": subject,
            })
        except (OSError, yaml.YAMLError):
            continue

    return messages


def get_pending_reviews(reviews_dir: Path) -> list[dict[str, Any]]:
    """Get pending reviews."""
    reviews = []
    pending_dir = reviews_dir / "pending"
    if not pending_dir.is_dir():
        return reviews

    for f in sorted(pending_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue
            reviews.append({
                "reviewer": fm.get("reviewer", ""),
                "scope": fm.get("scope", ""),
                "status": fm.get("status", ""),
                "date": fm.get("date", ""),
                "file": f.name,
            })
        except (OSError, yaml.YAMLError):
            continue

    return reviews


def get_proposals(agents_dir: Path) -> list[dict[str, Any]]:
    """Get active proposals (fallback mode)."""
    proposals = []
    proposals_dir = agents_dir / "proposals"
    if not proposals_dir.is_dir():
        return proposals

    for f in sorted(proposals_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue
            if fm.get("status") in ("implemented", "rejected"):
                continue

            # Extract title from first ## heading
            title = ""
            for line in content.split("---", 2)[-1].splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    title = stripped[3:].strip()
                    break

            proposals.append({
                "id": fm.get("id", ""),
                "status": fm.get("status", "proposed"),
                "author": fm.get("author", ""),
                "title": title,
                "file": f.name,
            })
        except (OSError, yaml.YAMLError):
            continue

    return proposals


def main() -> int:
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1]).resolve()
    else:
        from otaman_core._resolve import find_maestro_root
        project_root = find_maestro_root() or Path.cwd().resolve()
    repo_filter = sys.argv[2] if len(sys.argv) > 2 else None

    agents_dir = project_root / ".agents"
    if not agents_dir.is_dir():
        print(json.dumps({"error": "Not an otaman project — .agents/ directory not found"}))
        return 1

    # Load config
    config_path = project_root / "platform.yaml"
    if not config_path.exists():
        print(json.dumps({"error": "platform.yaml not found"}))
        return 1

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    bus_path = project_root / config.get("communication", {}).get("bus_path", ".agents/bus")

    # Build report
    report: dict[str, Any] = {
        "project": config.get("project", "unknown"),
        "repos": [],
        "messages": count_bus_messages(bus_path),
        "pending_messages": get_pending_messages(bus_path),
        "pending_reviews": get_pending_reviews(agents_dir / "reviews"),
        "proposals": get_proposals(agents_dir),
    }

    all_repos = config.get("repos", [])
    report["repo_counts"] = {
        "total": len(all_repos),
        "active": sum(1 for r in all_repos if not r.get("disabled", False)),
        "disabled": sum(1 for r in all_repos if r.get("disabled", False)),
    }

    for repo_cfg in all_repos:
        name = repo_cfg["name"]
        if repo_filter and name != repo_filter:
            continue

        repo_path = project_root / repo_cfg["path"]
        git_status = get_repo_git_status(repo_path)

        agent_msgs = count_bus_messages(bus_path, repo_cfg.get("owner"))

        report["repos"].append({
            "name": name,
            "owner": repo_cfg.get("owner", ""),
            "path": repo_cfg["path"],
            "disabled": repo_cfg.get("disabled", False),
            **git_status,
            "pending_messages": agent_msgs.get("pending", 0),
        })

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
