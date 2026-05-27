"""CE / EE edition detection.

Design rule (per proposal.md §5 + design.md Q7):
    Edition is detected ONCE at flow start and stored as a single string
    ``active_edition`` ("ce" | "ee").  Downstream logic consults that field;
    there are NO ``if edition == 'ee'`` branches in calling code — each step
    only declares ``min_edition`` and the runner filters.

Detection order (first match wins):
    1. ``OTAMAN_EDITION`` env var (explicit override — useful in containers)
    2. ``OTAMAN_LICENSE_FILE`` env var → validate the pointed file
    3. ``~/.otaman/license.key``
    4. ``/etc/otaman/license.key``
    5. Absence of any valid license → CE

A "valid" license file currently means: exists, is non-empty, and starts
with the magic prefix ``OTAMAN-EE-``.  Full cryptographic validation is
bridge-agent's responsibility (ADR-010); this module intentionally stays
thin — enough to gate the UI correctly.

Security notes:
    - ``OTAMAN_EDITION`` override emits a warning to stderr so operators can
      detect accidental env-var leaks in logs (LOW finding from PR #6 review).
    - ``OTAMAN_LICENSE_FILE`` is resolved via ``Path.resolve()`` before use to
      prevent path-traversal attacks (LOW finding from PR #6 review).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

EDITION_CE = "ce"
EDITION_EE = "ee"

_LICENSE_MAGIC = "OTAMAN-EE-"
_LICENSE_SEARCH_PATHS = [
    Path.home() / ".otaman" / "license.key",
    Path("/etc/otaman/license.key"),
]


def _is_valid_license(path: Path) -> bool:
    """Return True if *path* looks like a valid EE license file (fast check)."""
    try:
        text = path.read_text(encoding="ascii", errors="ignore").strip()
        return bool(text) and text.startswith(_LICENSE_MAGIC)
    except OSError:
        return False


def detect_edition() -> str:
    """Return ``"ee"`` if a valid EE license is found, else ``"ce"``."""
    # 1. Explicit override — warn so operators notice env-var leaks
    env_edition = os.environ.get("OTAMAN_EDITION", "").lower()
    if env_edition in (EDITION_CE, EDITION_EE):
        print(
            f"[otaman] WARNING: OTAMAN_EDITION env var is set to '{env_edition}'. "
            "Remove this variable in production to rely on license file detection.",
            file=sys.stderr,
        )
        return env_edition

    # 2. Env-var license file path — resolve to prevent path traversal
    env_lic = os.environ.get("OTAMAN_LICENSE_FILE", "")
    if env_lic:
        try:
            lic_path = Path(env_lic).resolve()
        except Exception:
            return EDITION_CE
        if _is_valid_license(lic_path):
            return EDITION_EE
        # File was specified but invalid — do NOT silently fall through;
        # return CE and let the runner warn the user.
        return EDITION_CE

    # 3–4. Well-known paths
    for candidate in _LICENSE_SEARCH_PATHS:
        if _is_valid_license(candidate):
            return EDITION_EE

    return EDITION_CE


def detect_mode(platform_yaml_path: Path | None = None) -> int:
    """Return 1 or 2 based on environment / existing platform.yaml.

    Mode 2+ if:
      - ``OTAMAN_ZITADEL_URL`` env var is set, OR
      - an existing platform.yaml declares ``mode: 2`` (or higher)

    Mode 1 otherwise (default).  ``--mode`` flag overrides; the runner
    passes it in directly rather than calling this function.
    """
    if os.environ.get("OTAMAN_ZITADEL_URL", ""):
        return 2

    if platform_yaml_path and platform_yaml_path.is_file():
        try:
            import yaml  # PyYAML — fast read
            data = yaml.safe_load(platform_yaml_path.read_text(encoding="utf-8")) or {}
            mode_val = int(data.get("mode", 1))
            if mode_val >= 2:
                return 2
        except Exception:
            pass

    return 1
