"""security-gates-hook-c 1.2 — policy wiring over core's gate config.

Core owns the vocabulary and the resolution: `security-gates:` parsing, the
per-language defaults, the per-repo union and overrides, and the canonical layer
order. This is the cli side — evaluating that config for a real program,
reporting it in doctor, and the one check that is a policy rather than a
configuration question: whether a suppression carries a justification.

Three deliberate positions:

**An unreadable config is NOT an absent one.** A program with no
`security-gates:` block has declared no ladder — there is nothing to report. A
block that exists and does not parse means the gates could not be evaluated, and
reporting that as "no gates configured" would be the fail-open class spec-agent
found in plugin's dispatch gate and I then found twice in mine (cli #223).

**Opt-out is rendered, never dropped.** The spec says a docs-only repo's opt-out
skips the hook VISIBLY. A repo missing from the report is indistinguishable from
a repo nobody configured.

**The suppression rule covers SECURITY suppressions, and distinguishes bare
from coded.** The spec's failing scenario is `# nosemgrep` with no
justification. My first version also matched ruff's and mypy's markers,
which found 14 "bare" suppressions in this repo — every one a `# type: ignore`
on an `import yaml` line. A rule that matches what it was not written for is how
a gate earns the reputation that gets it switched off, so the markers are the
ladder's own scanners.

Within those, a bare marker FAILS and a marker carrying an OPTIONAL rule code but
no prose is counted and named: the code says which rule was silenced, never why.

One asymmetry worth stating, because it looks inconsistent otherwise:
`# nosec B101` is code-only (the code is extra detail), while `# noqa: S310`
with no prose is BARE. For ruff the code is required syntax — you cannot
suppress without it — so it is part of the marker rather than an explanation of
it. A gate that accepted `# noqa: S310` as self-justifying would accept every
ruff security suppression ever written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Verdict words shared with the runtime-freshness surface, so one doctor run
#: does not speak two dialects. `skipped` is added for the opt-out case, which
#: freshness has no equivalent for.
FRESH = "fresh"
STALE = "stale"
NOT_CHECKED = "not-checked"
SKIPPED = "skipped"

#: SECURITY-tool suppressions only — the markers the ladder's own scanners emit.
#:
#: Deliberately NOT ruff's or mypy's markers. Those silence a style linter
#: and a type checker; this gate is about security findings, and the spec's
#: failing scenario is `# nosemgrep` with no justification. Measured before
#: narrowing: the broad set produced 14 "bare" findings in this repo and every
#: one was `# type: ignore` on a `import yaml` line — a rule matching things it
#: was not written for, which is how a gate earns the reputation that gets it
#: switched off.
_MARKERS = (
    r"#\s*nosemgrep",
    r"#\s*nosec",
    r"#\s*gitleaks:\s*allow",
    r"#\s*trivy:\s*ignore",
    r"#\s*osv-scanner:\s*ignore",
    r"//\s*nosemgrep",
    r"//\s*nolint:\s*gosec",
    # ruff's `S` rules ARE the security ones (the bandit port), so in a
    # ruff-based repo a security suppression is written as a noqa carrying an
    # S-code. Matching noqa wholesale would drag in every style suppression;
    # matching only the S-coded ones catches the form this toolchain uses. Found
    # by noticing the narrowed set reported zero while such a suppression sat in
    # commands/connection.py.
    #
    # Written WITHOUT the literal marker on purpose: spelling it out here made
    # ruff parse this comment as a real directive, and made this scanner count a
    # comment ABOUT a suppression as one. A detector that matches its own
    # documentation is a detector with a false positive built in.
    r"#\s*noqa:\s*S[0-9]+",
)
_MARKER_RE = re.compile("|".join(f"(?:{m})" for m in _MARKERS))
_RULE_CODE_RE = re.compile(r"^\s*:?\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*")
_BRACKET_CODE_RE = re.compile(r"^\s*\[[^\]]+\]")


@dataclass(frozen=True)
class RepoGates:
    """One repo's resolved ladder, as doctor renders it."""

    repo: str
    verdict: str
    reason: str
    layers: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()
    opt_out: bool = False


@dataclass(frozen=True)
class Suppression:
    """One suppression marker and whether it justifies itself."""

    path: str
    line: int
    text: str
    justified: bool
    coded_only: bool = False


@dataclass
class Report:
    """What `otaman doctor` renders for the security ladder."""

    repos: list[RepoGates] = field(default_factory=list)
    unjustified: list[Suppression] = field(default_factory=list)
    coded_only: int = 0
    justified: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.unjustified


def _core() -> Any | None:
    """core's security-gates module, or None on an install that predates it."""
    try:
        from otaman_core import security_gates
    except Exception:  # noqa: BLE001 - absent → not-checked, never "no gates"
        return None
    needed = ("parse_security_gates", "resolve_repo_gates", "LAYERS")
    return security_gates if all(hasattr(security_gates, n) for n in needed) else None


def evaluate(root: Path, config: dict[str, Any] | None = None) -> Report:
    """Resolve every repo's ladder from `security-gates:` in platform.yaml."""
    report = Report()
    core = _core()
    if core is None:
        report.error = (
            "otaman-core does not carry the security-gates schema "
            "(security-gates-hook-c 1.1) — cannot evaluate; update the bundle"
        )
        return report

    if config is None:
        try:
            import yaml

            config = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 - unreadable platform.yaml → not-checked
            report.error = f"platform.yaml could not be read: {type(exc).__name__}"
            return report

    block = config.get("security-gates")
    if block is None:
        return report  # nothing declared — nothing to report, and that is not a failure

    try:
        parsed = core.parse_security_gates(block)
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        # An unreadable block is NOT an absent one. Reporting "no gates" here
        # would be the fail-open shape this fleet has now found three times.
        report.error = f"security-gates: exists but does not parse — {exc}"
        return report

    for repo in config.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        name = str(repo.get("name") or "").strip()
        if not name:
            continue
        languages = tuple(str(x) for x in (repo.get("languages") or ()))
        resolved = core.resolve_repo_gates(parsed, name, languages)
        report.repos.append(_verdict_for(resolved))
    return report


def _verdict_for(resolved: Any) -> RepoGates:
    """Map a resolved ladder onto the shared verdict vocabulary."""
    if getattr(resolved, "opt_out", False):
        return RepoGates(
            repo=resolved.repo,
            verdict=SKIPPED,
            reason="opted out of the ladder — skipped visibly, not dropped",
            opt_out=True,
        )
    layers = tuple(la.layer for la in resolved.layers)
    blocking = tuple(la.layer for la in resolved.layers if getattr(la, "blocking", False))
    if not layers:
        return RepoGates(
            repo=resolved.repo,
            verdict=NOT_CHECKED,
            reason=(
                "no layer resolved for this repo — its languages match no "
                "per-language defaults and it declares no overrides"
            ),
        )
    if not blocking:
        return RepoGates(
            repo=resolved.repo,
            verdict=STALE,
            reason="every resolved layer is advisory — nothing here can block a PR",
            layers=layers,
        )
    return RepoGates(
        repo=resolved.repo,
        verdict=FRESH,
        reason=f"{len(blocking)} blocking layer(s): {', '.join(blocking)}",
        layers=layers,
        blocking=blocking,
    )


def scan_suppressions(paths: list[Path]) -> tuple[list[Suppression], int, int]:
    """``(unjustified, coded_only_count, justified_count)`` over *paths*.

    A suppression justifies itself with PROSE. A rule code says which rule was
    silenced, never why — so it is counted separately rather than accepted as a
    justification or failed as a bare marker.
    """
    unjustified: list[Suppression] = []
    coded_only = justified = 0
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            match = _MARKER_RE.search(line)
            if not match:
                continue
            tail = line[match.end() :]
            prose = _BRACKET_CODE_RE.sub("", _RULE_CODE_RE.sub("", tail))
            prose = re.sub(r"^[\s\-–:]+", "", prose).strip()
            if not tail.strip():
                unjustified.append(
                    Suppression(
                        path=str(path), line=number, text=line.strip()[:100], justified=False
                    )
                )
            elif not prose:
                coded_only += 1
            else:
                justified += 1
    return unjustified, coded_only, justified


def python_sources(root: Path) -> list[Path]:
    """The repo's own Python sources — not vendored or build output."""
    skip = {".git", "__pycache__", ".venv", "node_modules", "build", "dist", ".ruff_cache"}
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


__all__ = [
    "FRESH",
    "NOT_CHECKED",
    "SKIPPED",
    "STALE",
    "RepoGates",
    "Report",
    "Suppression",
    "evaluate",
    "python_sources",
    "scan_suppressions",
]
