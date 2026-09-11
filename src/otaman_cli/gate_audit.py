"""Append-only gate-waiver audit trail (spec-gate-hardening 1.3).

Every gate violation that PROCEEDS under ``warn``/``self-waive`` leaves a durable
record in ``.agents/audit/gate-waivers.jsonl`` — one line per waived violation,
recording change, action, violation, actor, and time. A waiver that leaves no
trace is a defect (the pmeets incident: an action waived in the evening with no
record it had been refused in the morning). ``otaman spec status`` reads these
back to annotate affected changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_AUDIT_REL = (".agents", "audit", "gate-waivers.jsonl")


def _audit_path(root: Path) -> Path:
    p = root
    for part in _AUDIT_REL:
        p = p / part
    return p


def append_waivers(
    root: Path,
    *,
    change: str,
    action: str,
    violations: list[str] | tuple[str, ...],
    actor: str,
    at: str,
) -> Path:
    """Append one JSONL entry per violation. Append-only (never rewrites prior
    entries); creates ``.agents/audit/`` on first use. Returns the log path."""
    path = _audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for v in violations:
            entry = {
                "change": change,
                "action": action,
                "violation": v,
                "actor": actor,
                "at": at,
            }
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


def read_waivers(root: Path) -> list[dict[str, Any]]:
    """All waiver entries, oldest first. Malformed lines are skipped (the trail is
    best-effort for reading; writing is the load-bearing half)."""
    path = _audit_path(root)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def waiver_summary_for(root: Path, change: str) -> dict[str, int]:
    """``{action: count}`` of waived violations recorded for *change* (empty when
    none) — the source for ``otaman spec status``'s per-change annotation."""
    counts: dict[str, int] = {}
    for e in read_waivers(root):
        if e.get("change") == change:
            action = str(e.get("action") or "?")
            counts[action] = counts.get(action, 0) + 1
    return counts


def annotate(root: Path, change: str) -> str:
    """A short ``(N waived <action>, …)`` suffix for *change*, or ``""`` when
    clean. Sorted by action for stable output."""
    counts = waiver_summary_for(root, change)
    if not counts:
        return ""
    parts = [f"{n} waived {action}" for action, n in sorted(counts.items())]
    return " (" + ", ".join(parts) + ")"


__all__ = ["append_waivers", "read_waivers", "waiver_summary_for", "annotate"]
