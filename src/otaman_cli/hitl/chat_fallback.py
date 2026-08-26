"""Security primitives for the insecure chat-approval fallback (hitl 1.3).

Design (hitl-confirmation-adapters design.md, reconciled PR #221): chat
approval is an explicit RISK ACCEPTANCE the tenant opts into via
``allow_insecure_chat_approval`` in ``~/.otaman/hitl.yaml``. It is NOT
cryptographic — the guardrails are friction + audit:

- **Two-step read-to-confirm phrase-echo (PRIMARY control).** A request step
  mints a one-time short-TTL phrase and appends it to the END of the pending
  proposal document; the phrase NEVER appears in terminal output. Confirm
  succeeds only when the human echoes that phrase (proving they opened the
  real proposal). A cron tick / autonomous loop structurally cannot produce
  that echo. The phrase lives in a file the agent CAN read — so the honest
  limit is stated plainly: an adversarial agent could extract it; the policy
  rule (the agent SHALL NOT read/reproduce the phrase — an audited violation)
  plus the two-turn shape are friction, not proof of humanness.
- **`OTAMAN_SESSION_MODE` positive marker (defense-in-depth).** Autonomous
  spawners set ``headless``/``cron``; present + autonomous → refuse early.
  ABSENT → do NOT refuse on that basis (the phrase-echo blocks autonomous
  flows regardless). TTY is deliberately NOT the gate — an attended agent
  chat session (chat-approval's target context) is itself no-TTY.
- **Rate limit + audit.** At most one pending nonce at a time; a per-day cap;
  every action is audit-logged (proposal, nonce id, session id, human,
  timestamp — never the phrase) for post-hoc human review.

This module is pure/testable: it owns session-mode detection, the tenant
nonce state, phrase mint/append/verify, and the audit log. The
`approve request`/`approve confirm` command wiring lives in commands/approve.py.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

# Short TTL bounds ONLY a single confirmation ATTEMPT — never the proposal
# (Roman: approval is never time-blocked; an expired phrase is just re-minted).
PHRASE_TTL_SECONDS = 900  # 15 minutes
DAILY_CAP = 10  # max chat-approval requests per tenant per day

AUTONOMOUS_MODES = frozenset({"headless", "cron"})

# Phrase = 4 words from a small curated list — human-friendly to read + echo,
# dependency-free. ~64^4 space is friction (not crypto), which is the point.
_WORDS = (
    "amber anchor basil beacon birch bramble cedar cobalt copper coral crimson "
    "delta ember fable falcon fern flint garnet ginger granite harbor hazel indigo "
    "ivory jade juniper kelp lagoon lantern lichen lilac maple marble meadow moss "
    "nectar oak ochre onyx opal otter pebble pewter quartz quill raven reef rowan "
    "saffron sable slate sorrel spruce tamarind teal thistle topaz umber verbena "
    "walnut willow yarrow zephyr"
).split()

_PHRASE_RE = re.compile(r"^[a-z]+(-[a-z]+){3}$")

# Markers delimiting the appended block so re-requests replace (never stack) it.
_BLOCK_START = "<!-- otaman-hitl-confirm:start -->"
_BLOCK_END = "<!-- otaman-hitl-confirm:end -->"
_BLOCK_RE = re.compile(
    re.escape(_BLOCK_START) + r".*?" + re.escape(_BLOCK_END) + r"\n?",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# tenant-scope paths (monkeypatched by tests; mirror the ledger/hitl pattern)


def chat_state_path(home: Path | None = None) -> Path:
    """Pending-nonce + daily-cap state: ``~/.otaman/hitl-chat.json`` (0600)."""
    return (home or Path.home()) / ".otaman" / "hitl-chat.json"


def chat_audit_path(home: Path | None = None) -> Path:
    """Append-only audit log: ``~/.otaman/hitl-chat-audit.log`` (0600)."""
    return (home or Path.home()) / ".otaman" / "hitl-chat-audit.log"


# ---------------------------------------------------------------------------
# session-mode detection (defense-in-depth marker)


def session_mode() -> str | None:
    """The positive autonomy marker from ``OTAMAN_SESSION_MODE`` (or None).

    Normalized to ``interactive``/``headless``/``cron``; any other/absent
    value is None (unknown — NOT treated as autonomous; the phrase-echo is
    the real gate).
    """
    raw = os.environ.get("OTAMAN_SESSION_MODE", "").strip().lower()
    return raw if raw in {"interactive", "headless", "cron"} else None


def is_autonomous_context() -> bool:
    """True only when a spawner POSITIVELY marked this session headless/cron."""
    return session_mode() in AUTONOMOUS_MODES


# ---------------------------------------------------------------------------
# flag


def chat_approval_enabled(config: dict) -> bool:
    """Whether the tenant opted in via ``allow_insecure_chat_approval: true``.

    *config* is a parsed tenant ``hitl.yaml`` mapping (tenant-only flag; core's
    validator forbids enabling it at program scope).
    """
    return config.get("allow_insecure_chat_approval") is True


# ---------------------------------------------------------------------------
# phrase


def generate_phrase() -> str:
    """A fresh 4-word one-time phrase, e.g. ``otter-slate-verbena-quill``."""
    return "-".join(secrets.choice(_WORDS) for _ in range(4))


def is_phrase_shaped(text: str) -> bool:
    """Cheap shape check used before comparing (four lowercase hyphen words)."""
    return bool(_PHRASE_RE.match(text.strip()))


# ---------------------------------------------------------------------------
# proposal-document phrase block


def strip_phrase_block(text: str) -> str:
    """Remove any prior appended confirm block (idempotent re-request)."""
    return _BLOCK_RE.sub("", text).rstrip() + "\n"


def render_phrase_block(phrase: str, *, stem: str, nonce_id: str) -> str:
    """The block appended to the proposal doc. Contains the phrase (this file
    IS what the human reads); the command's STDOUT never does."""
    return (
        f"\n{_BLOCK_START}\n"
        "## Human confirmation required (read-to-confirm)\n\n"
        "You are reading the actual proposal — good. To approve it, copy the "
        "phrase below into a new message, then run:\n\n"
        f"    otaman approve confirm {stem} <phrase>\n\n"
        f"Confirmation phrase: **{phrase}**\n\n"
        f"<!-- nonce:{nonce_id} -->\n"
        f"{_BLOCK_END}\n"
    )


def append_phrase_to_proposal(doc_path: Path, phrase: str, *, stem: str, nonce_id: str) -> None:
    """Replace any prior block and append a fresh confirm block at the END."""
    text = doc_path.read_text(encoding="utf-8")
    base = strip_phrase_block(text)
    doc_path.write_text(
        base + render_phrase_block(phrase, stem=stem, nonce_id=nonce_id), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# nonce state (one pending at a time + per-day cap)


@dataclass(frozen=True)
class ChatNonce:
    stem: str
    nonce_id: str
    phrase: str
    human_id: str
    session_id: str
    created_at: int  # epoch seconds


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/corrupt → fresh state
        return {}
    return data if isinstance(data, dict) else {}


def _write_0600(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover - non-POSIX
        pass
    os.replace(tmp, path)


def _save_state(path: Path, state: dict) -> None:
    _write_0600(path, json.dumps(state, indent=2))


def pending_nonce(path: Path) -> ChatNonce | None:
    """The single currently-pending nonce, or None."""
    rec = _load_state(path).get("pending")
    if not isinstance(rec, dict):
        return None
    try:
        return ChatNonce(
            stem=rec["stem"],
            nonce_id=rec["nonce_id"],
            phrase=rec["phrase"],
            human_id=rec.get("human_id", "human"),
            session_id=rec.get("session_id", "unknown"),
            created_at=int(rec["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def requests_used_today(path: Path, today: str) -> int:
    """How many requests have been minted on *today* (YYYY-MM-DD)."""
    state = _load_state(path)
    return int(state.get("count", 0)) if state.get("day") == today else 0


def daily_cap_reached(path: Path, today: str) -> bool:
    return requests_used_today(path, today) >= DAILY_CAP


def record_request(path: Path, nonce: ChatNonce, *, today: str) -> None:
    """Persist *nonce* as the sole pending one and bump today's counter.

    Any prior pending nonce is REPLACED (one-at-a-time invariant): re-running
    request invalidates the previous phrase.
    """
    state = _load_state(path)
    count = int(state.get("count", 0)) if state.get("day") == today else 0
    _save_state(
        path,
        {
            "day": today,
            "count": count + 1,
            "pending": {
                "stem": nonce.stem,
                "nonce_id": nonce.nonce_id,
                "phrase": nonce.phrase,
                "human_id": nonce.human_id,
                "session_id": nonce.session_id,
                "created_at": nonce.created_at,
            },
        },
    )


def clear_pending(path: Path) -> None:
    """Invalidate the pending nonce (used on confirm success AND on refusal),
    preserving the day/count so the cap still holds."""
    state = _load_state(path)
    state["pending"] = None
    _save_state(path, state)


def verify_phrase(path: Path, stem: str, provided: str, *, now: int) -> tuple[bool, str]:
    """Check *provided* against the pending nonce for *stem*.

    Returns ``(ok, reason)``. On ANY failure the pending nonce is invalidated
    (spec: a bad/absent confirm invalidates the nonce). Success also consumes
    it (one-shot). Never leaks the phrase in *reason*.
    """
    nonce = pending_nonce(path)
    if nonce is None or nonce.stem != stem:
        return False, "no pending confirmation for this proposal — run `approve request` first"
    if now - nonce.created_at > PHRASE_TTL_SECONDS:
        clear_pending(path)
        return False, "the confirmation phrase expired — re-run `approve request` for a fresh one"
    if not secrets.compare_digest(provided.strip(), nonce.phrase):
        clear_pending(path)
        return (
            False,
            "phrase does not match — pending confirmation invalidated; re-run `approve request`",
        )
    clear_pending(path)
    return True, "ok"


# ---------------------------------------------------------------------------
# audit


def audit(
    path: Path,
    *,
    action: str,
    stem: str,
    nonce_id: str,
    human_id: str,
    session_id: str,
    outcome: str,
    timestamp: str,
) -> None:
    """Append one audit line (JSON). Provenance only — the phrase is NEVER
    written to the audit log."""
    line = json.dumps(
        {
            "ts": timestamp,
            "action": action,
            "outcome": outcome,
            "stem": stem,
            "nonce_id": nonce_id,
            "human_id": human_id,
            "session_id": session_id,
        },
        sort_keys=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - non-POSIX
        pass


def session_id() -> str:
    """Best-effort session id for audit provenance."""
    return os.environ.get("OTAMAN_SESSION_ID", "").strip() or "unknown"


__all__ = [
    "AUTONOMOUS_MODES",
    "DAILY_CAP",
    "PHRASE_TTL_SECONDS",
    "ChatNonce",
    "append_phrase_to_proposal",
    "audit",
    "chat_approval_enabled",
    "chat_audit_path",
    "chat_state_path",
    "clear_pending",
    "daily_cap_reached",
    "generate_phrase",
    "is_autonomous_context",
    "is_phrase_shaped",
    "pending_nonce",
    "record_request",
    "render_phrase_block",
    "requests_used_today",
    "session_id",
    "session_mode",
    "strip_phrase_block",
    "verify_phrase",
]
