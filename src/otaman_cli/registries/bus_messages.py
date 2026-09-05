"""Bus message builders + emitters for the 7 outcome/solution message types
(Appendix F.3, task 6.2).

Message types:
    - outcome-estimate-requested
    - outcome-estimates-ready
    - outcome-cost-accepted
    - outcome-cost-rejected
    - outcome-status-changed
    - solution-status-changed
    - solution-recommendation

These reuse the existing otaman-meta bus file convention used elsewhere in
``otaman_cli.main`` (filename pattern ``<ts>-<from>-to-<to>-<type>.md`` in
``.agents/bus/active/``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from otaman_cli.registries.transitions import utc_now_iso

_YAML = YAML()
_YAML.indent(mapping=2, sequence=4, offset=2)


VALID_MESSAGE_TYPES = frozenset(
    {
        "outcome-estimate-requested",
        "outcome-estimates-ready",
        "outcome-cost-accepted",
        "outcome-cost-rejected",
        "outcome-status-changed",
        "solution-status-changed",
        "solution-recommendation",
    }
)


# ---------------------------------------------------------------------------
# Builders


def build_outcome_estimate_requested(outcome: dict[str, Any], from_actor: str) -> dict[str, Any]:
    """Built when ``otaman outcome request-estimate`` runs."""
    return {
        "type": "outcome-estimate-requested",
        "from": from_actor,
        "to": "cto-agent",
        "payload": {
            "outcome-id": outcome["id"],
            "requested-by": from_actor,
            "at": utc_now_iso(),
            "priority": outcome.get("priority", "P2"),
            "impact": outcome.get("impact"),
            "statement": dict(outcome["statement"]) if outcome.get("statement") else None,
        },
    }


def build_outcome_estimates_ready(
    outcome_id: str,
    solutions: list[dict[str, Any]],
    from_actor: str,
    recommended: str | None = None,
) -> dict[str, Any]:
    """Built when CTO has sized solutions and wants to surface them to CEO/CPO."""
    return {
        "type": "outcome-estimates-ready",
        "from": from_actor,
        "to": "cpo-agent",
        "payload": {
            "outcome-id": outcome_id,
            "solutions": [
                {"id": s["id"], "t-shirt": s.get("t-shirt"), "effort-days": s.get("effort-days")}
                for s in solutions
            ],
            "estimated-by": from_actor,
            "at": utc_now_iso(),
            "recommended": recommended,
        },
    }


def build_outcome_cost_accepted(
    outcome: dict[str, Any],
    solution: dict[str, Any],
    from_actor: str,
) -> dict[str, Any]:
    """Built when ``otaman outcome accept-cost`` runs."""
    return {
        "type": "outcome-cost-accepted",
        "from": from_actor,
        "to": "cto-agent",
        "payload": {
            "outcome-id": outcome["id"],
            "chosen-solution": solution["id"],
            "accepted-by": from_actor,
            "at": utc_now_iso(),
            "effort-days": solution.get("effort-days"),
            "t-shirt": solution.get("t-shirt"),
            "release": outcome.get("release") or solution.get("release"),
        },
    }


def build_outcome_cost_rejected(
    outcome: dict[str, Any],
    from_actor: str,
    note: str | None = None,
    rejected_solution: str | None = None,
) -> dict[str, Any]:
    """Built when ``otaman outcome reject-cost`` runs."""
    return {
        "type": "outcome-cost-rejected",
        "from": from_actor,
        "to": "cto-agent",
        "payload": {
            "outcome-id": outcome["id"],
            "rejected-solution": rejected_solution,
            "rejected-by": from_actor,
            "at": utc_now_iso(),
            "note": note,
        },
    }


def build_outcome_status_changed(
    outcome: dict[str, Any],
    from_status: str,
    to_status: str,
    from_actor: str,
    action: str,
) -> dict[str, Any]:
    """Built on any outcome status mutation (promote/demote/retire)."""
    return {
        "type": "outcome-status-changed",
        "from": from_actor,
        "to": "all",
        "payload": {
            "outcome-id": outcome["id"],
            "from": from_status,
            "to": to_status,
            "by": from_actor,
            "at": utc_now_iso(),
            "action": action,
        },
    }


def build_solution_status_changed(
    solution: dict[str, Any],
    from_status: str,
    to_status: str,
    from_actor: str,
    action: str,
) -> dict[str, Any]:
    """Built on any solution status mutation (select/promote-to-complete/discard)."""
    return {
        "type": "solution-status-changed",
        "from": from_actor,
        "to": "all",
        "payload": {
            "solution-id": solution["id"],
            "outcome-id": solution.get("outcome-id"),
            "from": from_status,
            "to": to_status,
            "by": from_actor,
            "at": utc_now_iso(),
            "action": action,
        },
    }


def build_solution_recommendation(
    outcome_id: str,
    recommended_solution: str,
    score: float,
    rationale: str,
    alternatives: list[dict[str, Any]],
    from_actor: str,
) -> dict[str, Any]:
    """Built by auto-triage when a solution is recommended after `solution add`."""
    return {
        "type": "solution-recommendation",
        "from": from_actor,
        "to": "cpo-agent",
        "payload": {
            "outcome-id": outcome_id,
            "recommended-solution": recommended_solution,
            "score": score,
            "rationale": rationale,
            "alternatives": alternatives,
            "recommended-by": from_actor,
            "at": utc_now_iso(),
        },
    }


# ---------------------------------------------------------------------------
# Emitter (filesystem-based — reuses the otaman bus convention)


def emit(
    msg: dict[str, Any],
    bus_active_dir: Path,
) -> Path:
    """Write *msg* to ``<bus_active_dir>/<ts>-<from>-to-<to>-<type>.md``.

    Returns the absolute path of the created file.
    """
    if msg.get("type") not in VALID_MESSAGE_TYPES:
        raise ValueError(f"unknown bus message type: {msg.get('type')!r}")

    ts = utc_now_iso().replace(":", "").replace("-", "").rstrip("Z")
    sender = msg.get("from", "unknown").replace("/", "-")
    recipient = msg.get("to", "unknown").replace("/", "-")
    msg_type = msg["type"]

    filename = f"{ts}-{sender}-to-{recipient}-{msg_type}.md"
    path = bus_active_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    # Body format: YAML frontmatter + a brief markdown subject section,
    # matching the existing otaman bus message convention.
    payload = msg.get("payload", {})
    frontmatter = {
        "id": f"{ts}-{sender}-to-{recipient}-{msg_type}",
        "from": sender,
        "to": recipient,
        "priority": "normal",
        "type": msg_type,
        "timestamp": payload.get("at") or utc_now_iso(),
        "status": "pending",
    }

    import io

    sio = io.StringIO()
    _YAML.dump(frontmatter, sio)
    fm_yaml = sio.getvalue()

    sio2 = io.StringIO()
    _YAML.dump(payload, sio2)
    payload_yaml = sio2.getvalue()

    body = f"---\n{fm_yaml}---\n\n## Subject: {msg_type}\n\n```yaml\n{payload_yaml}```\n"
    from otaman_cli.bus_write import write_message_exclusive

    # Never overwrite a same-second sibling (propose-hardening).
    return write_message_exclusive(path, body)


__all__ = [
    "VALID_MESSAGE_TYPES",
    "build_outcome_estimate_requested",
    "build_outcome_estimates_ready",
    "build_outcome_cost_accepted",
    "build_outcome_cost_rejected",
    "build_outcome_status_changed",
    "build_solution_status_changed",
    "build_solution_recommendation",
    "emit",
]
