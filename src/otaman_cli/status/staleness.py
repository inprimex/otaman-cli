"""The staleness rule — one definition, shared by every status surface (1.2).

Measured 2026-09-16 07:58 UTC: `otaman status` reported 4 working / 1 waiting
while `updated_at == since` for EVERY record. Nothing had been touched since its
state was set; cli's "working" record was 8h15m old and deploy had been
"working" 7.5h with its last commit 13h old. The surface could not tell
"working for eight hours" from "died eight hours ago", and reported the louder
one — so spec-agent had to diff git commit times against status timestamps to
decide whether to nudge. A confidently wrong surface trains its readers to stop
reading it (the blocked-tracker class).

Heartbeats (otaman-plugin 1.1) keep a live session's record fresh. This module
is the other half — render-time truth, so a crashed session cannot present as
busy even if nothing ever refreshes it again. Belt and braces, deliberately:
either half alone leaves the confidently-wrong case open.

Scope is `working` and `waiting` ONLY. Those are the states that CLAIM a live
session, so only those can lie about one:

* `idle` is exempt — an idle agent is not claiming anything, and an old idle
  record is simply an agent that has been quiet.
* `blocked` is exempt — being parked on a dependency is a true statement that
  stays true without anyone refreshing it.
* Any state outside the enum is exempt. The live fleet has `human` in state
  `afk`, 89 days old, and that is correct: it is a deliberate human state, not
  an unheard-from session. Rendering it STALE would be the same confidently-
  wrong failure pointed the other way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

#: Default TTL. A session's hooks fire per tool-batch, so a live agent refreshes
#: far more often than this; 30 minutes is long enough that a single slow
#: operation (a full test run, a long clone) never flips a live session to STALE,
#: and short enough that a dead one stops claiming work within the hour.
DEFAULT_TTL_SECONDS = 1800

#: States that claim a live session, and therefore can lie about one.
STALEABLE = frozenset({"working", "waiting"})


def ttl_seconds(root: Path) -> int:
    """The staleness TTL for the program at *root*.

    Reads `agent_presence_ttl_seconds` (top level or nested under `platform:`,
    matching how `agent_presence` itself is read). Any missing/invalid value
    falls back to the default rather than disabling the rule — a program with a
    typo in its TTL should still catch dead sessions.
    """
    try:
        from otaman_cli.yaml_fast import load_file

        doc = load_file(root / "platform.yaml", {}) or {}
        if not isinstance(doc, dict):
            return DEFAULT_TTL_SECONDS
        val = doc.get("agent_presence_ttl_seconds")
        if val is None:
            plat = doc.get("platform")
            val = plat.get("agent_presence_ttl_seconds") if isinstance(plat, dict) else None
        if val is None:
            return DEFAULT_TTL_SECONDS
        ttl = int(val)
        return ttl if ttl > 0 else DEFAULT_TTL_SECONDS
    except Exception:  # noqa: BLE001 - unreadable config → the default, never off
        return DEFAULT_TTL_SECONDS


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def age_seconds(record, *, now: datetime | None = None) -> float | None:
    """Seconds since *record*'s `updated_at`, or None when it is unparseable."""
    stamp = _parse(getattr(record, "updated_at", "") or "")
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - stamp).total_seconds()


def _state_value(record) -> str:
    state = getattr(record, "state", "")
    return str(getattr(state, "value", state) or "")


def is_stale(record, *, ttl: int = DEFAULT_TTL_SECONDS, now: datetime | None = None) -> bool:
    """Is this record claiming a live session it has not heard from?

    False for every exempt state (see the module docstring) and for a record
    whose `updated_at` cannot be parsed — an unreadable stamp is a reason to
    doubt the reader, not to accuse the agent of being dead.
    """
    if _state_value(record) not in STALEABLE:
        return False
    age = age_seconds(record, now=now)
    return age is not None and age > ttl


def human_age(seconds: float | None) -> str:
    """`8h15m` / `42m` / `30s` — compact, for naming a last-seen time inline."""
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    days, rem = divmod(seconds, 86400)
    hours, minutes = divmod(rem, 3600)
    if days:
        return f"{days}d{hours}h"
    return f"{hours}h{minutes // 60}m"


def last_seen(record, *, now: datetime | None = None) -> str:
    """How long ago this record was last touched, e.g. `last seen 8h15m ago`."""
    return f"last seen {human_age(age_seconds(record, now=now))} ago"


def render_state(record, *, ttl: int = DEFAULT_TTL_SECONDS, now: datetime | None = None) -> str:
    """The state to SHOW: `STALE` in place of a claim nobody has backed up.

    Every surface renders through this rather than reading `record.state`
    directly, so `status`, `check`, the console fleet line and doctor cannot
    drift into disagreeing about who is alive.
    """
    if is_stale(record, ttl=ttl, now=now):
        return "STALE"
    return _state_value(record)


def stale_note(record, *, ttl: int = DEFAULT_TTL_SECONDS, now: datetime | None = None) -> str:
    """`STALE (was working, last seen 8h15m ago)`, or "" when not stale.

    Names BOTH the claim and the last-seen time: "stale" alone tells a reader
    something is wrong without telling them what was lost.
    """
    if not is_stale(record, ttl=ttl, now=now):
        return ""
    return f"STALE (was {_state_value(record)}, {last_seen(record, now=now)})"


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "STALEABLE",
    "age_seconds",
    "human_age",
    "is_stale",
    "last_seen",
    "render_state",
    "stale_note",
    "ttl_seconds",
]
