"""Known-retired platform.yaml fields (scan-schema-conformance 1.1).

The scan pipeline (plugin ``discover_repos``) historically stamped
``is_spec_repo: true`` onto each spec repo's ``repos[]`` entry — but the live
platform schema declares ``repos.items`` as ``additionalProperties: false``, so
that field makes a scan-generated draft FAIL ``otaman init``'s own validation.
The top-level ``specs:`` block is the sole spec-repo marker; ``is_spec_repo`` is
redundant.

This module is the single source of truth for which fields are retired, so the
two removal sites — scan output (``post_scan``) and the init/validate tolerant
migration (``_normalize_ce_platform_yaml_for_validation``) — can never drift
apart. Deliberately dependency-free so the hot validation path pays no import
cost.
"""

from __future__ import annotations

#: Retired keys on each ``repos[]`` entry (the live schema rejects them).
RETIRED_REPO_FIELDS: tuple[str, ...] = ("is_spec_repo",)

#: Retired top-level keys (none yet; the mechanism is here for the next one).
RETIRED_TOP_FIELDS: tuple[str, ...] = ()


def strip_retired_fields(doc: dict) -> list[str]:
    """Remove known-retired keys from *doc* in place; return the removed field
    names (with a count suffix when a repo field recurs across entries) for a
    human-readable note. Idempotent — a clean doc yields an empty list."""
    removed: list[str] = []
    for field in RETIRED_TOP_FIELDS:
        if field in doc:
            doc.pop(field, None)
            removed.append(field)
    repos = doc.get("repos")
    if isinstance(repos, list):
        repo_hits: dict[str, int] = {}
        for entry in repos:
            if not isinstance(entry, dict):
                continue
            for field in RETIRED_REPO_FIELDS:
                if field in entry:
                    entry.pop(field, None)
                    repo_hits[field] = repo_hits.get(field, 0) + 1
        for field, n in repo_hits.items():
            removed.append(f"{field} (x{n} repo entries)" if n > 1 else field)
    return removed


__all__ = ["RETIRED_REPO_FIELDS", "RETIRED_TOP_FIELDS", "strip_retired_fields"]
