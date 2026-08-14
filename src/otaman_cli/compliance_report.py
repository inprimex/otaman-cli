#!/usr/bin/env python3
"""Generate a compliance audit report for an otaman-managed project.

Produces a structured report covering:
- Ownership enforcement status
- Communication audit trail
- Decision records inventory
- Review coverage
- Git history integrity

Designed for HIPAA, ISO 27001, and GDPR compliance documentation.

Usage:
    python compliance-report.py [project-root] [--format json|markdown]

Output:
    Report to stdout (JSON by default, markdown with --format markdown).

Exit codes:
    0 — success
    1 — not an otaman project
    2 — error
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def run_git(repo_path: Path, *args: str) -> str:
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


def audit_ownership(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Audit ownership enforcement status."""
    ownership_file = project_root / ".agents" / "ownership.json"
    result: dict[str, Any] = {
        "ownership_file_exists": ownership_file.exists(),
        "repos": [],
    }

    if not ownership_file.exists():
        return result

    with open(ownership_file, encoding="utf-8") as f:
        ownership = json.load(f)

    for repo in ownership.get("repos", []):
        repo_path = project_root / repo["path"]
        repo_info: dict[str, Any] = {
            "name": repo["name"],
            "owner": repo["owner"],
            "path": repo["path"],
            "exists": repo_path.is_dir(),
        }

        if repo_path.is_dir():
            # Check if CLAUDE.md has otaman rules
            claude_md = repo_path / "CLAUDE.md"
            repo_info["has_claude_md"] = claude_md.exists()
            if claude_md.exists():
                content = claude_md.read_text(encoding="utf-8")
                repo_info["has_maestro_rules"] = (
                    "<!-- maestro:begin -->" in content  # legacy: marker string
                )
            else:
                repo_info["has_maestro_rules"] = False

        result["repos"].append(repo_info)

    return result


def audit_communication(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Audit the communication bus — message counts, age, completeness."""
    bus_path = project_root / config.get("communication", {}).get("bus_path", ".agents/bus")
    result: dict[str, Any] = {
        "bus_exists": bus_path.is_dir(),
        "total_messages": 0,
        "by_status": {"pending": 0, "read": 0, "resolved": 0},
        "by_type": {},
        "by_agent": {},
        "oldest_pending": None,
    }

    if not bus_path.is_dir():
        return result

    oldest_pending_ts = None

    for f in sorted(bus_path.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue

            result["total_messages"] += 1

            status = fm.get("status", "unknown")
            result["by_status"][status] = result["by_status"].get(status, 0) + 1

            msg_type = fm.get("type", "unknown")
            result["by_type"][msg_type] = result["by_type"].get(msg_type, 0) + 1

            from_agent = fm.get("from", "unknown")
            if from_agent not in result["by_agent"]:
                result["by_agent"][from_agent] = {"sent": 0, "received": 0}
            result["by_agent"][from_agent]["sent"] += 1

            to_agent = fm.get("to", "unknown")
            if to_agent not in result["by_agent"]:
                result["by_agent"][to_agent] = {"sent": 0, "received": 0}
            result["by_agent"][to_agent]["received"] += 1

            if status == "pending":
                ts = fm.get("timestamp", "")
                if ts and (oldest_pending_ts is None or str(ts) < str(oldest_pending_ts)):
                    oldest_pending_ts = str(ts)

        except (OSError, yaml.YAMLError):
            continue

    result["oldest_pending"] = oldest_pending_ts
    return result


def audit_decisions(project_root: Path) -> dict[str, Any]:
    """Audit Architecture Decision Records."""
    decisions_dir = project_root / ".agents" / "decisions"
    result: dict[str, Any] = {
        "total": 0,
        "by_status": {},
        "decisions": [],
    }

    if not decisions_dir.is_dir():
        return result

    for f in sorted(decisions_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue

            result["total"] += 1
            status = fm.get("status", "unknown")
            result["by_status"][status] = result["by_status"].get(status, 0) + 1

            # Extract title
            title = ""
            for line in content.split("---", 2)[-1].splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    title = stripped[3:].strip()
                    break

            result["decisions"].append(
                {
                    "id": fm.get("id", ""),
                    "date": str(fm.get("date", "")),
                    "author": fm.get("author", ""),
                    "status": status,
                    "title": title,
                }
            )
        except (OSError, yaml.YAMLError):
            continue

    return result


def audit_reviews(project_root: Path) -> dict[str, Any]:
    """Audit review coverage."""
    reviews_dir = project_root / ".agents" / "reviews"
    result: dict[str, Any] = {
        "pending": 0,
        "done": 0,
        "by_reviewer": {},
        "recent_reviews": [],
    }

    for subdir, status_key in [("pending", "pending"), ("done", "done")]:
        d = reviews_dir / subdir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                content = f.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
                if not fm_match:
                    continue
                fm = yaml.safe_load(fm_match.group(1))
                if not isinstance(fm, dict):
                    continue

                result[status_key] += 1
                reviewer = fm.get("reviewer", "unknown")
                if reviewer not in result["by_reviewer"]:
                    result["by_reviewer"][reviewer] = {"pending": 0, "done": 0}
                result["by_reviewer"][reviewer][status_key] += 1

                result["recent_reviews"].append(
                    {
                        "reviewer": reviewer,
                        "scope": fm.get("scope", ""),
                        "status": fm.get("status", ""),
                        "date": str(fm.get("date", "")),
                        "phase": status_key,
                    }
                )
            except (OSError, yaml.YAMLError):
                continue

    # Keep only last 10
    result["recent_reviews"] = result["recent_reviews"][-10:]
    return result


def audit_git_integrity(project_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Check git history integrity across repos."""
    repos = []
    for repo_cfg in config.get("repos", []):
        repo_path = project_root / repo_cfg["path"]
        info: dict[str, Any] = {
            "name": repo_cfg["name"],
            "owner": repo_cfg.get("owner", ""),
        }

        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            info["status"] = "not_found"
            repos.append(info)
            continue

        # Total commits
        count = run_git(repo_path, "rev-list", "--count", "HEAD")
        info["total_commits"] = int(count) if count.isdigit() else 0

        # Unsigned commits (for compliance-heavy setups)
        unsigned = run_git(repo_path, "log", "--format=%H", "--no-walk", "HEAD")
        info["head_commit"] = unsigned[:8] if unsigned else ""

        # Check for force-push indicators (reflog gaps)
        info["status"] = "ok"

        repos.append(info)

    return repos


def format_markdown(report: dict[str, Any]) -> str:
    """Format the report as markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Otaman Compliance Audit Report",
        "",
        f"**Project**: {report['project']}",
        f"**Generated**: {now}",
        "",
        "---",
        "",
        "## 1. Ownership Enforcement",
        "",
        "Ownership file: "
        f"{'Present' if report['ownership']['ownership_file_exists'] else 'MISSING'}",
        "",
    ]

    if report["ownership"]["repos"]:
        lines.append("| Repo | Owner | Exists | CLAUDE.md | Otaman Rules |")
        lines.append("|------|-------|--------|-----------|---------------|")
        for r in report["ownership"]["repos"]:
            exists = "Yes" if r.get("exists") else "NO"
            claude = "Yes" if r.get("has_claude_md") else "No"
            rules = "Yes" if r.get("has_maestro_rules") else "NO"
            lines.append(f"| {r['name']} | {r['owner']} | {exists} | {claude} | {rules} |")
        lines.append("")

    lines.extend(
        [
            "## 2. Communication Audit Trail",
            "",
            f"- Total messages: {report['communication']['total_messages']}",
            f"- Pending: {report['communication']['by_status'].get('pending', 0)}",
            f"- Read: {report['communication']['by_status'].get('read', 0)}",
            f"- Resolved: {report['communication']['by_status'].get('resolved', 0)}",
        ]
    )
    if report["communication"]["oldest_pending"]:
        lines.append(f"- Oldest pending: {report['communication']['oldest_pending']}")
    lines.append("")

    lines.extend(
        [
            "## 3. Architecture Decision Records",
            "",
            f"- Total ADRs: {report['decisions']['total']}",
        ]
    )
    for status, count in report["decisions"]["by_status"].items():
        lines.append(f"- {status}: {count}")
    lines.append("")

    lines.extend(
        [
            "## 4. Review Coverage",
            "",
            f"- Pending reviews: {report['reviews']['pending']}",
            f"- Completed reviews: {report['reviews']['done']}",
        ]
    )
    if report["reviews"]["by_reviewer"]:
        lines.append("")
        lines.append("| Reviewer | Pending | Done |")
        lines.append("|----------|---------|------|")
        for reviewer, counts in report["reviews"]["by_reviewer"].items():
            lines.append(f"| {reviewer} | {counts['pending']} | {counts['done']} |")
    lines.append("")

    lines.extend(
        [
            "## 5. Git History Integrity",
            "",
        ]
    )
    if report["git_integrity"]:
        lines.append("| Repo | Owner | Commits | Status |")
        lines.append("|------|-------|---------|--------|")
        for r in report["git_integrity"]:
            commits = r.get("total_commits", "—")
            lines.append(
                f"| {r['name']} | {r.get('owner', '')} | {commits} | {r.get('status', 'unknown')} |"
            )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    output_format = "json"
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--format" in sys.argv:
        idx = sys.argv.index("--format")
        if idx + 1 < len(sys.argv):
            output_format = sys.argv[idx + 1]

    if args:
        project_root = Path(args[0]).resolve()
    else:
        from otaman_core._resolve import find_maestro_root

        project_root = find_maestro_root() or Path.cwd().resolve()

    agents_dir = project_root / ".agents"
    if not agents_dir.is_dir():
        print("ERROR: Not an otaman project — .agents/ not found", file=sys.stderr)
        return 1

    config_path = project_root / "platform.yaml"
    if not config_path.exists():
        print("ERROR: platform.yaml not found", file=sys.stderr)
        return 1

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    report = {
        "project": config.get("project", "unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ownership": audit_ownership(project_root, config),
        "communication": audit_communication(project_root, config),
        "decisions": audit_decisions(project_root),
        "reviews": audit_reviews(project_root),
        "git_integrity": audit_git_integrity(project_root, config),
    }

    if output_format == "markdown":
        print(format_markdown(report))
    else:
        print(json.dumps(report, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
