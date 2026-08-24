"""Read/write helpers for the tenant-scope ``~/.otaman/hitl.yaml``.

hitl-confirmation-adapters 1.2. The enrollment map is keyed by a human's
email (the human-roster canonical key). Per core's committed
``hitl-schema.yaml`` and the Option A' storage decision, each human's TOTP
enrollment stores a REFERENCE — ``totp_secret_ref`` — never the secret
value; the base32 seed lives in the tenant dotenv (``~/.otaman/secrets.env``,
0600), written via ``otaman_core._secrets.upsert_dotenv_secret`` and
resolved on demand. This module only touches the ref, never the value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def hitl_config_path(home: Path | None = None) -> Path:
    """Canonical tenant-scope hitl.yaml path (``~/.otaman/hitl.yaml``).

    The single indirection every read/write funnels through: like
    ``otaman_core.confirmations.default_ledger_path`` (the confirmation
    ledger also lives outside tmp at ``~/.otaman``), the test suite isolates
    against the real file by monkeypatching THIS function, so no in-process
    test ever touches a developer's real ``hitl.yaml``.
    """
    base = home or Path.home()
    return base / ".otaman" / "hitl.yaml"


def email_slug(email: str) -> str:
    """Slug rule frozen with core (PR #23): lowercase, non-alnum → '-'.

    The dotenv key is ``HITL_TOTP_<email_slug>``; this MUST match core's
    resolve-by-ref lookup byte-for-byte, so it lives in one place.
    """
    out = []
    for ch in email.strip().lower():
        out.append(ch if ch.isalnum() else "-")
    return "".join(out)


def totp_key_for(email: str) -> str:
    """The tenant-dotenv key name holding this human's TOTP seed."""
    return f"HITL_TOTP_{email_slug(email)}"


def load_hitl_config(path: Path | None = None) -> dict[str, Any]:
    """Parse hitl.yaml; ``{}`` on missing/unparseable/non-mapping."""
    p = path or hitl_config_path()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def totp_enrollments(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map ``email -> totp_secret_ref`` for every human with a TOTP ref.

    A ``totp_secret_ref`` is a source spec (e.g.
    ``{type: dotenv, name: HITL_TOTP_<slug>, scope: tenant}``). Humans
    without one are omitted (they are TTY-only).
    """
    enrollment = config.get("enrollment")
    if not isinstance(enrollment, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for email, human in enrollment.items():
        if not isinstance(human, dict):
            continue
        ref = human.get("totp_secret_ref")
        if isinstance(ref, dict) and ref.get("type"):
            out[str(email)] = ref
    return out


def set_totp_enrollment(email: str, key: str, *, path: Path | None = None) -> Path:
    """Upsert ``enrollment[email].totp_secret_ref`` (a ref), preserving the rest.

    Writes only the reference — the schema's ref-not-value invariant. Other
    humans, other adapters, and the tenant flag are left untouched.
    """
    import yaml

    p = path or hitl_config_path()
    config = load_hitl_config(p)
    enrollment = config.get("enrollment")
    if not isinstance(enrollment, dict):
        enrollment = {}
        config["enrollment"] = enrollment
    human = enrollment.get(email)
    if not isinstance(human, dict):
        human = {}
        enrollment[email] = human
    human["totp_secret_ref"] = {"type": "dotenv", "name": key, "scope": "tenant"}

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(config, sort_keys=True, default_flow_style=False), encoding="utf-8")
    return p


__all__ = [
    "email_slug",
    "hitl_config_path",
    "load_hitl_config",
    "set_totp_enrollment",
    "totp_enrollments",
    "totp_key_for",
]
