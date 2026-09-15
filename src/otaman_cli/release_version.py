"""Which version answers "what am I running" (version-authority 1.3).

Exactly one version is authoritative for a tenant: the **otaman-deploy release**.
Everything else — this package's version, a sibling repo's tag, a branch position
— is a component detail. Reporting a component version as THE installed version
is how "it's fixed in 0.4.0" becomes a claim no tenant can verify, because 0.4.0
is an otaman-cli package version and their installation is a deploy release.

The concrete defect: `otaman --version` printed `otaman 0.4.0` (this package, via
importlib.metadata) while the live deploy release was v0.5.7.

**The release marker does not exist yet.** Searched at implementation time
(2026-09-16): no manifest under ``~/.otaman/``, no writer in otaman-deploy's
scripts. ``~/.otaman/edition.yaml`` carries a ``version:`` field, but it is
written once at install and never refreshed — it read 0.3.0 against a live v0.5.7
— and the file documents itself as identity/UX only. Presenting that as the
installed version would be worse than the wrong-but-fresh package version, so it
is NOT read as the release pointer.

So this module resolves the release from a marker when one exists and says so
honestly when none does. Deploy owns the marker (asked 20260915T233409); the
candidate paths below are checked in order, so the moment it is written the
answer becomes complete with no change here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Candidate markers, most specific first. A dedicated file is preferred: a file
#: whose single job is "what release is installed" is harder to let drift than a
#: field inside a file about something else — and drift is the bug being fixed.
_CANDIDATES = (
    (Path.home() / ".otaman" / "release.yaml", ("release", "version")),
    (Path.home() / ".otaman" / "edition.yaml", ("release",)),  # `release:`, NOT `version:`
)


@dataclass(frozen=True)
class VersionReport:
    """What to show, and what a tenant should quote."""

    release: str
    """The otaman-deploy release, or ``""`` when no marker is present."""

    component: str
    """This package's version — a component detail, never the answer."""

    source: str
    """Where the release came from, for an honest report."""

    @property
    def resolved(self) -> bool:
        return bool(self.release)

    @property
    def quotable(self) -> str:
        """The version a tenant should quote, or ``""`` when unknown."""
        return self.release


def _read_marker(path: Path, keys: tuple[str, ...]) -> str:
    """The first of *keys* present in *path*, or ``""``.

    Deliberately tolerant: a marker that cannot be read means "unknown", never a
    crash and never a guess — an unreadable file must not become a confident
    wrong answer.
    """
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/unreadable/unparseable → unknown
        return ""
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_release_version(component: str, *, candidates=None) -> VersionReport:
    """The authoritative release version, or an honest "unknown" (1.3)."""
    for path, keys in candidates if candidates is not None else _CANDIDATES:
        found = _read_marker(path, keys)
        if found:
            return VersionReport(release=found, component=component, source=str(path))
    return VersionReport(release="", component=component, source="")


def format_version(report: VersionReport) -> str:
    """The `otaman --version` answer.

    Resolved: leads with the release, and marks the package version as a
    component so it cannot be mistaken for the thing to quote. Unresolved: says
    what the number IS and what it is not, rather than printing a bare version a
    tenant would reasonably quote as their installed version.
    """
    if report.resolved:
        return (
            f"otaman {report.release}  (otaman-deploy release — quote this version)\n"
            f"  otaman-cli component: {report.component}"
        )
    return (
        f"otaman-cli component: {report.component}\n"
        "  This is the otaman-cli package version, NOT your installed version.\n"
        "  Your installation is an otaman-deploy release; quote that when\n"
        "  reporting an issue or confirming a fix has shipped."
    )


def banner_version(report: VersionReport) -> str:
    """The short form for the help banner — release when known, else a labelled
    component version so the banner never implies a shipping version."""
    return report.release if report.resolved else f"cli {report.component}"


__all__ = [
    "VersionReport",
    "banner_version",
    "format_version",
    "resolve_release_version",
]
