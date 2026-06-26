"""CC fan-out helpers — port of bus_server.py:157-283 (cli-send-cc-fanout-parity).

Closes the parity gap noted in incidents:
  - 2026-06-22 — my drift report CC'd to plugin-agent + human was lost
    because cmd_send wrote only the primary file
  - 2026-06-26 — plugin-agent's response confirming the asymmetry bit them

Canonical source: ``otaman-plugin/src/otaman_plugin/servers/bus_server.py``
lines 157-283.  These helpers are ported (not redesigned) so the on-disk
shape of a CC copy is byte-for-byte identical regardless of which entry
point (bash cmd_send vs. MCP otaman_send) wrote it.

Long-term plan: extract these into ``otaman-core`` so both implementations
share the same primitive (the Option D path in the original 2026-06-09
discussion).  Until that lands, syncing the two copies is a manual chore
documented in both files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def load_routing_rules(root: Path) -> list[dict[str, Any]]:
    """Load ``bus.routing_rules`` from ``platform.yaml`` at the project root.

    Returns an empty list when the file is missing, malformed, or contains
    no ``bus.routing_rules`` section.  Pure YAML parse — schema validation
    happens at rule evaluation time.

    Mirrors ``bus_server.py:157``.
    """
    platform = root / "platform.yaml"
    if not platform.is_file():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        data = yaml.safe_load(platform.read_text(encoding="utf-8")) or {}
    except (Exception,):  # yaml.YAMLError + OSError; broad to match bus_server
        return []
    bus_cfg = data.get("bus") or {}
    rules = bus_cfg.get("routing_rules") or []
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def evaluate_routing_rules(
    rules: list[dict[str, Any]],
    to: str,
    priority: str,
    msg_type: str | None = None,
) -> set[str]:
    """Return the union of ``cc:`` lists from all rules that match.

    Per bus-cc-routing design Q3: rules are evaluated in order, but all
    matching rules contribute (union, not first-match-wins).  A rule
    matches when every ``when.<field>`` constraint is satisfied
    (AND semantics):

    - ``when.to: <name>`` — exact string equality with *to*.
    - ``when.priority: <val>`` — equals the single value, or appears in a
      list (OR semantics for the list form).
    - ``when.type: <val>`` (outcome-proposal-routing 1.1) — matches when
      *msg_type* equals the single value, or appears in a list.  A rule
      with ``when.type`` set never matches when the caller passes
      ``msg_type=None``.

    Unknown ``when`` keys cause the rule to be skipped silently — keeps
    the evaluator forward-compatible with future ``when`` extensions
    without breaking older clients.

    Mirrors ``bus_server.py:182``.
    """
    cc_union: set[str] = set()
    supported_when_keys = {"to", "priority", "type"}
    for rule in rules:
        when = rule.get("when") or {}
        if not isinstance(when, dict):
            continue
        if not set(when.keys()).issubset(supported_when_keys):
            continue
        if "to" in when and when["to"] != to:
            continue
        if "priority" in when:
            pri = when["priority"]
            if isinstance(pri, list):
                if priority not in pri:
                    continue
            elif pri != priority:
                continue
        if "type" in when:
            if msg_type is None:
                continue
            typ = when["type"]
            if isinstance(typ, list):
                if msg_type not in typ:
                    continue
            elif typ != msg_type:
                continue
        cc_list = rule.get("cc") or []
        if not isinstance(cc_list, list):
            continue
        for name in cc_list:
            if isinstance(name, str) and name:
                cc_union.add(name)
    return cc_union


def compute_effective_cc(
    to: str,
    priority: str,
    explicit_cc: list[str] | None,
    routing_rules: list[dict[str, Any]],
    msg_type: str | None = None,
) -> list[str]:
    """Compose the effective CC list per bus-cc-routing Q1.

    - Union of explicit sender ``cc`` and routing-rule-derived ``cc``
    - Deduplicated (set semantics) but returned in a stable insertion order
      so test assertions and the on-disk message stay deterministic
    - The primary ``to`` recipient is excluded even if a rule names them

    Mirrors ``bus_server.py:227``.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    candidates: list[str] = []
    if explicit_cc:
        candidates.extend(c for c in explicit_cc if isinstance(c, str) and c)
    candidates.extend(
        sorted(evaluate_routing_rules(routing_rules, to, priority, msg_type))
    )
    for name in candidates:
        if name == to or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def inject_x_cc(content: str) -> str:
    """Insert ``x-cc: true`` into the existing frontmatter of *content*.

    The line is appended after the last frontmatter field, before the
    closing ``---`` delimiter.  The original message file is never mutated;
    this helper is called only when writing per-recipient CC copies.

    Mirrors ``bus_server.py:271``.
    """
    m = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not m:
        return content  # malformed frontmatter; caller will not reach here
    head, fm_body, tail = m.group(1), m.group(2), m.group(3)
    new_fm = fm_body.rstrip("\n") + "\nx-cc: true"
    return head + new_fm + tail + content[m.end():]


def cc_copy_filename(
    *, timestamp: str, from_agent: str, cc_recipient: str, slug: str,
) -> str:
    """Build the on-disk stem for a CC copy.

    Format mirrors the primary message file naming convention:
        <timestamp>-<from-agent>-to-<cc-recipient>-<slugified-subject>.md

    The ``-to-<recipient>`` segment uses the CC recipient (NOT the
    primary ``to:``) so each recipient's inbox glob picks up its copy.
    """
    safe_cc = cc_recipient.replace("/", "-")
    return f"{timestamp}-{from_agent}-to-{safe_cc}-{slug}.md"


__all__ = [
    "load_routing_rules",
    "evaluate_routing_rules",
    "compute_effective_cc",
    "inject_x_cc",
    "cc_copy_filename",
]
