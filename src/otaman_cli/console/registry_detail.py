"""Textual-free detail renderers for outcome / solution registry nodes.

The console artifact tree (console-ux-redesign) shows outcomes → solutions →
changes as clipped one-line rows; pressing enter on an outcome or solution must
open the FULL artifact content — the in-console equivalent of ``otaman outcome
show`` / ``otaman solution show`` (cofounder-agent addendum, Roman feedback), not
a status-only popup. These builders load the raw registry and format the detail
as plain text; the screen just renders it in a scroll pane.
"""

from __future__ import annotations

from typing import Any

from otaman_cli.console.bus import Program


def _load_raw(program: Program, kind: str) -> dict | None:
    """Raw registry dict for *kind* (``outcomes`` | ``solutions``), or None when
    the file doesn't resolve/exist."""
    try:
        from otaman_cli.registries.loader import resolve_registry_path, yaml_load

        p = resolve_registry_path(program.root, kind)
        if not (p and p.is_file()):
            return None
        return yaml_load(p) or {}
    except Exception:  # noqa: BLE001 - unreadable/unresolved registry → no detail
        return None


def _find(items: list, oid: str) -> dict | None:
    for it in items:
        if isinstance(it, dict) and it.get("id") == oid:
            return it
    return None


def _transitions_tail(entry: dict, limit: int = 6) -> list[str]:
    trans = entry.get("transitions") or []
    if not trans:
        return []
    out = ["", "  Transitions (recent)"]
    for t in trans[-limit:]:
        if not isinstance(t, dict):
            continue
        arrow = f" → {t.get('to')}" if t.get("to") else ""
        frm = f"{t.get('from')} " if t.get("from") else ""
        note = f"  ({t['note']})" if t.get("note") else ""
        out.append(
            f"    {t.get('at', '')}  {t.get('by', '')}  {t.get('action', '')} "
            f"{frm}{arrow}{note}".rstrip()
        )
    return out


def outcome_detail_text(program: Program, oid: str, *, scope: str | None = None) -> str:
    """Full outcome detail — the console equivalent of ``otaman outcome show``.

    *scope* role-scopes the surface (team-mode 2.4b): ``"value"`` hides the
    costing fields, ``"costing"`` hides the pure-value narrative, ``None`` shows
    all. Core (id/status/priority/impact/release + the JTBD statement) is always
    shown."""
    data = _load_raw(program, "outcomes")
    if data is None:
        return "(outcomes registry unavailable — run `otaman outcome list` for the error)"
    o: dict[str, Any] | None = _find(data.get("outcomes") or [], oid)
    if not o:
        return f"Outcome not found: {oid}"
    show_value = scope != "costing"
    show_costing = scope != "value"
    lines = [f"Outcome: {o['id']}", ""]
    if scope:
        lines.append(f"  [role view: {scope}]")
        lines.append("")
    lines.append(f"  Status:           {o.get('status')}")
    lines.append(f"  Priority:         {o.get('priority')}")
    lines.append(f"  Impact:           {o.get('impact') or '-'}")
    lines.append(f"  Release:          {o.get('release') or '-'}")
    if show_value:
        lines.append(f"  Category:         {o.get('category') or '-'}")
        lines.append(f"  Persona:          {o.get('persona') or '-'}")
    if show_costing:
        lines.append(f"  Estimate-req'd:   {o.get('estimate-requested')}")
        lines.append(f"  Chosen-solution:  {o.get('chosen-solution') or '-'}")
        lines.append(f"  Cost-accepted:    {o.get('cost-accepted')}")
    lines.append("")
    lines.append("  JTBD statement")
    stmt = o.get("statement") or {}
    lines.append(f"    As a       {stmt.get('as-a')}")
    lines.append(f"    I want to  {stmt.get('i-want-to')}")
    lines.append(f"    Outcome    {stmt.get('incremental-outcome')}")
    lines.append(f"    So I can   {stmt.get('so-i-can')}")
    if stmt.get("ultimate-outcome"):
        lines.append(f"    Ultimate   {stmt.get('ultimate-outcome')}")
    if show_value and o.get("product-notes"):
        lines.append("")
        lines.append("  Product notes")
        for line in str(o["product-notes"]).splitlines() or [o["product-notes"]]:
            lines.append(f"    {line}")
    lines.extend(_transitions_tail(o))
    lines.append("")
    lines.append(f"  created: {o.get('created')}   updated: {o.get('updated')}")
    return "\n".join(lines)


def solution_detail_text(program: Program, sid: str, *, scope: str | None = None) -> str:
    """Full solution detail — the console equivalent of ``otaman solution show``.

    *scope* role-scopes the surface (team-mode 2.4b): ``"costing"`` shows the
    t-shirt/effort/cto-notes and hides pros/cons; ``"value"`` shows pros/cons and
    hides the costing; ``None`` shows all."""
    data = _load_raw(program, "solutions")
    if data is None:
        return "(solutions registry unavailable — run `otaman solution list` for the error)"
    s: dict[str, Any] | None = _find(data.get("solutions") or [], sid)
    if not s:
        return f"Solution not found: {sid}"
    show_value = scope != "costing"
    show_costing = scope != "value"
    lines = [f"Solution: {s['id']}", ""]
    if scope:
        lines.append(f"  [role view: {scope}]")
        lines.append("")
    lines.append(f"  Outcome:        {s.get('outcome-id')}")
    lines.append(f"  Status:         {s.get('status')}")
    if show_costing:
        lines.append(f"  T-shirt:        {s.get('t-shirt') or '-'}")
        lines.append(f"  Effort-days:    {s.get('effort-days') or '-'}")
    lines.append(f"  Release:        {s.get('release') or '-'}")
    lines.append("")
    lines.append("  Description")
    for line in str(s.get("description") or "").splitlines() or [s.get("description", "")]:
        lines.append(f"    {line}")
    if show_value and s.get("pros"):
        lines.append("")
        lines.append("  Pros")
        lines.extend(f"    • {p}" for p in s["pros"])
    if show_value and s.get("cons"):
        lines.append("")
        lines.append("  Cons")
        lines.extend(f"    • {c}" for c in s["cons"])
    if s.get("dependencies"):
        lines.append("")
        lines.append("  Dependencies")
        for d in s["dependencies"]:
            if isinstance(d, dict):
                ref = d.get("ref") or d.get("name") or "?"
                lines.append(f"    [{d.get('kind')}] {ref}")
    if show_costing and s.get("cto-notes"):
        lines.append("")
        lines.append("  CTO notes")
        for line in str(s["cto-notes"]).splitlines() or [s["cto-notes"]]:
            lines.append(f"    {line}")
    lines.extend(_transitions_tail(s))
    lines.append("")
    lines.append(f"  created: {s.get('created')}   updated: {s.get('updated')}")
    return "\n".join(lines)


def node_detail_text(
    program: Program, kind: str, node_id: str, *, scope: str | None = None
) -> str | None:
    """Detail text for an outcome/solution tree node, or None for other kinds.
    *scope* role-scopes the surface (team-mode 2.4b)."""
    if kind == "outcome":
        return outcome_detail_text(program, node_id, scope=scope)
    if kind == "solution":
        return solution_detail_text(program, node_id, scope=scope)
    return None


def role_scope(program: Program) -> str | None:
    """The role-scoped detail view for the acting human (team-mode 2.4b):
    founder → ``None`` (founder-mode, all keys visible per canon); a cto without
    the founder hat → ``"costing"``; anyone else / unverified → ``None`` (never
    hide the surface from an operator we can't scope)."""
    try:
        import os

        from otaman_core.human_roster import load_human_roster, resolve_roster_human

        roster = load_human_roster(program.root / "platform.yaml")
        entry = resolve_roster_human(roster, os.environ.get("OTAMAN_HUMAN"))
        hats = {r.lower() for r in (getattr(entry, "roles", None) or [])} if entry else set()
    except Exception:  # noqa: BLE001 - roster unavailable → no scoping (show all)
        return None
    if "founder" in hats:
        return None  # founder-mode: all keys visible
    if "cto" in hats:
        return "costing"
    return None


__all__ = [
    "outcome_detail_text",
    "solution_detail_text",
    "node_detail_text",
    "role_scope",
]
