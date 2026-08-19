"""Edition read-side for the CE/EE split (ce-ee-release-channels 3.2).

``~/.otaman/edition.yaml`` is IDENTITY, not enforcement (design Q3):
it tells UX code what the install is; capabilities are gated by package
presence (import probe), never by this file. Consumer contract (Q3a,
co-signed deploy 20260819T123215 / bridge 20260819T123701):

- read-only consumer; never writes the file
- missing/unparseable file => edition UNKNOWN; probes decide behavior
- readers MUST ignore unknown keys (forward-compat)
- probe-vs-file mismatch => one-line diagnostic, no enforcement

The absent-runner UX lives here so every runner-assuming command
(watchdog, session spawn, runner config) explains the CE state with a
hosted-tier pointer instead of erroring raw.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

DEFAULT_EDITION_PATH = Path.home() / ".otaman" / "edition.yaml"

HOSTED_TIER_POINTER = "https://otaman.ai (hosted teams tier)"

_VALID_EDITIONS = ("ce", "ee")


def read_edition_file(path: Path | None = None) -> dict:
    """Parse edition.yaml; ``{}`` on missing/unparseable/non-mapping.

    Unknown keys are preserved in the returned dict but never interpreted
    — callers only read the keys they know (Q3a forward-compat rule).
    """
    p = path or DEFAULT_EDITION_PATH
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_edition(path: Path | None = None) -> str:
    """Return ``"ce"``, ``"ee"``, or ``"unknown"`` (identity only, Q3)."""
    edition = read_edition_file(path).get("edition")
    if isinstance(edition, str) and edition.strip().lower() in _VALID_EDITIONS:
        return edition.strip().lower()
    return "unknown"


def runner_package_present() -> bool:
    """Import-probe for the local runner package (the ENFORCEMENT side)."""
    try:
        return importlib.util.find_spec("otaman_runner") is not None
    except Exception:
        # A broken package on sys.path is "present" for identity purposes;
        # find_spec raising (e.g. ValueError on a damaged spec) must not
        # crash a UX-only probe.
        return True


def edition_mismatch_diagnostic(path: Path | None = None) -> str | None:
    """One-line probe-vs-file diagnostic, or None when consistent (Q3a).

    Never enforces anything: an edited edition.yaml grants nothing and
    breaks nothing — this only helps a human notice the inconsistency.
    """
    edition = get_edition(path)
    if edition == "unknown":
        return None
    present = runner_package_present()
    if edition == "ce" and present:
        return (
            "edition.yaml says 'ce' but the otaman-runner package is importable "
            "— likely a pre-split install upgraded over the CE channel (harmless; "
            "the runner keeps working)."
        )
    if edition == "ee" and not present:
        return (
            "edition.yaml says 'ee' but the otaman-runner package is absent — "
            "the file identifies the edition, it does not grant capabilities; "
            "reinstall via the EE channel to restore the runner."
        )
    return None


def absent_runner_notice(command: str, path: Path | None = None) -> list[str] | None:
    """CE explanation lines for a runner-assuming command, or None.

    Returns lines only when this install identifies as CE — then the
    missing runner endpoint is the EXPECTED state, not an error worth a
    raw stack of hints. Unknown edition keeps the existing raw error
    (probes decide behavior when identity is absent, per Q3a).
    """
    if get_edition(path) != "ce":
        return None
    return [
        f"This install is Otaman CE — it ships without a local runner, so "
        f"'{command}' has nothing to connect to.",
        f"Runner-backed sessions are part of the hosted tier: {HOSTED_TIER_POINTER}.",
        "Manual and direct-SSH workflows are fully supported on CE.",
    ]


__all__ = [
    "DEFAULT_EDITION_PATH",
    "HOSTED_TIER_POINTER",
    "absent_runner_notice",
    "edition_mismatch_diagnostic",
    "get_edition",
    "read_edition_file",
    "runner_package_present",
]
