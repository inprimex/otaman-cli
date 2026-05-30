#!/usr/bin/env python3
"""
otaman vocab lint — prototype (task 3.2)

Runnable spike that demonstrates the v0 lint algorithm:
  - word-boundary regex matching for canonical + synonym mentions
  - exclusion of code blocks, frontmatter, URLs
  - finding categories: synonym-used, deprecated-term, unused-term

Run:  python3 lint_prototype.py

Output matches the "Expected output" example in cli-vocab-lint-prototype.md.

NOT production code — single-file spike, hardcoded inputs, no persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Minimal data model (same shape as the doc's spec, just enough for the spike)


@dataclass(frozen=True)
class Term:
    id: str
    canonical: str
    definition: str
    synonyms: tuple[str, ...] = ()
    status: str = "active"
    replaced_by: str | None = None


@dataclass
class Finding:
    category: str          # synonym-used | deprecated-term | unused-term
    severity: str          # advisory | warn | error
    file: str
    line: int
    matched_text: str = ""
    suggested_canonical: str = ""
    term_id: str = ""
    deprecated_term_id: str = ""


# ---------------------------------------------------------------------------
# Hardcoded sample registry — matches the doc's example


REGISTRY: list[Term] = [
    Term(
        id="vocab-tenant",
        canonical="Tenant",
        definition="A customer organization with isolated workspace + data scope.",
        synonyms=("Organization", "Account"),
        status="active",
    ),
    Term(
        id="vocab-workspace",
        canonical="Workspace",
        definition="(deprecated) The data + UI scope owned by a tenant.",
        synonyms=(),
        status="deprecated",
        replaced_by="vocab-tenant",
    ),
    Term(
        id="vocab-rtgs",
        canonical="Real-time gross settlement",
        definition="Settlement system that settles transactions individually in real time.",
        synonyms=("RTGS",),
        status="active",
    ),
]


# ---------------------------------------------------------------------------
# Sample document being linted — matches the doc's example


EXAMPLE_DOC_PATH = "outcomes/JTBD-3-invite-colleagues.md"
EXAMPLE_DOC = """\
---
title: JTBD-3 — Invite colleagues
status: drafting
---

# JTBD-3 — Invite colleagues

When a Tenant admin needs to add team members, they invite colleagues via email.

The Organization receives a notification email when new members join.

This is the Workspace where collaboration happens. The Account is the billing root.

```python
# This is code; "Account" inside code should NOT lint
class Account: pass
```

See https://docs.example.com/Organization for details — URL is excluded.
"""


# ---------------------------------------------------------------------------
# Exclusion: strip frontmatter, fenced code, inline code, URLs, HTML comments


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_excluded(text: str) -> str:
    """Replace excluded regions with same-shape whitespace to preserve line numbers."""
    def _blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    text = _FRONTMATTER_RE.sub(_blank, text)
    text = _FENCED_CODE_RE.sub(_blank, text)
    text = _HTML_COMMENT_RE.sub(_blank, text)
    text = _INLINE_CODE_RE.sub(_blank, text)
    text = _URL_RE.sub(_blank, text)
    return text


# ---------------------------------------------------------------------------
# Scan: produce findings from a single document


def scan_doc(path: str, text: str, registry: list[Term]) -> tuple[list[Finding], set[str]]:
    """Return (findings, mentioned_term_ids).

    mentioned_term_ids feeds the unused-term detector at registry level.
    """
    findings: list[Finding] = []
    mentioned: set[str] = set()
    stripped = _strip_excluded(text)
    lines = stripped.splitlines()

    # Build the matchers
    matchers: list[tuple[re.Pattern, Term, str]] = []
    # Canonical first so it takes priority over synonyms when the canonical
    # IS a substring of a synonym (rare but possible)
    for t in registry:
        canonical_re = re.compile(r"\b" + re.escape(t.canonical) + r"\b")
        matchers.append((canonical_re, t, "canonical"))
        for syn in t.synonyms:
            syn_re = re.compile(r"\b" + re.escape(syn) + r"\b")
            matchers.append((syn_re, t, "synonym"))

    for line_idx, line in enumerate(lines, start=1):
        for pattern, term, kind in matchers:
            for m in pattern.finditer(line):
                matched_text = m.group(0)
                mentioned.add(term.id)

                if term.status == "deprecated" and kind == "canonical":
                    # The canonical form of a deprecated term IS the deprecation hit
                    replacement_canonical = ""
                    if term.replaced_by:
                        repl = next((r for r in registry if r.id == term.replaced_by), None)
                        if repl:
                            replacement_canonical = repl.canonical
                    findings.append(Finding(
                        category="deprecated-term",
                        severity="warn",
                        file=path,
                        line=line_idx,
                        matched_text=matched_text,
                        suggested_canonical=replacement_canonical,
                        term_id=term.replaced_by or term.id,
                        deprecated_term_id=term.id,
                    ))
                elif kind == "synonym":
                    findings.append(Finding(
                        category="synonym-used",
                        severity="advisory",
                        file=path,
                        line=line_idx,
                        matched_text=matched_text,
                        suggested_canonical=term.canonical,
                        term_id=term.id,
                    ))
                # kind == "canonical" and status == "active" → silent pass
    return findings, mentioned


# ---------------------------------------------------------------------------
# Aggregate + format


def render_findings(findings: list[Finding]) -> str:
    """Match the human-readable format from cli-vocab-lint-prototype.md."""
    out: list[str] = []
    for f in findings:
        line1 = f"{f.file}:{f.line}"
        if f.category == "synonym-used":
            line2 = (
                f"  [advisory] synonym-used: \"{f.matched_text}\""
                f" → suggest \"{f.suggested_canonical}\" ({f.term_id})"
            )
        elif f.category == "deprecated-term":
            line2 = (
                f"  [warn]     deprecated-term: \"{f.matched_text}\""
                f" — replaced by \"{f.suggested_canonical}\" ({f.term_id})"
            )
        else:
            line2 = f"  [{f.severity}] {f.category}: {f.matched_text}"
        out.append(line1)
        out.append(line2)
        out.append("")
    return "\n".join(out).rstrip()


def find_unused(registry: list[Term], mentioned: set[str]) -> list[str]:
    return [t.id for t in registry if t.id not in mentioned and t.status == "active"]


# ---------------------------------------------------------------------------
# Main


def main() -> int:
    findings, mentioned = scan_doc(EXAMPLE_DOC_PATH, EXAMPLE_DOC, REGISTRY)
    unused = find_unused(REGISTRY, mentioned)

    print(render_findings(findings))
    print()
    print("Summary")
    print("-" * 72)
    print(f"  Files scanned:    1")
    print(f"  Findings:         {len(findings)}"
          f" ({sum(1 for f in findings if f.severity == 'warn')} warn,"
          f" {sum(1 for f in findings if f.severity == 'advisory')} advisory)")
    if unused:
        print(f"  Unused terms:     {len(unused)} — {', '.join(unused)}")
    else:
        print(f"  Unused terms:     0")

    # Exit code: 0 clean, 1 advisory/warn, 2 errors
    if any(f.severity == "error" for f in findings):
        return 2
    if findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
