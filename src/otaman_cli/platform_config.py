"""platform.yaml reads that are not the schema: the specs path, and CE normalization.

Extracted from `main.py` (CTO review 2026-09-26). `_normalize_ce_platform_yaml_for_validation`
is 183 lines of its own — the single largest thing in the entry-point module, and
nothing about an entry point.

Names kept as written: the move is mechanical by design, and the underscore stays
rather than mixing a rename into a relocation.

A leaf: `ui` only.
"""

from __future__ import annotations

import os
from pathlib import Path


def _read_platform_specs_path(root: Path) -> str:
    """Return the specs.path value from platform.yaml, or '' if absent."""
    try:
        import yaml as _yaml

        config_path = root / "platform.yaml"
        if not config_path.is_file():
            return ""
        with open(config_path, encoding="utf-8") as f:
            config = _yaml.safe_load(f) or {}
        return config.get("specs", {}).get("path", "")
    except Exception:
        return ""


def declared_repo_names(root: Path) -> list[str]:
    """The repo NAMES platform.yaml declares, in declaration order.

    What core's stage-1 lint compares a proposal's `affected_repos` against (JTBD-57
    1.1): a proposal routed at a repo the program does not declare is an error
    finding. Names, not paths — `_declared_repo_dirs` in the console answers a
    different question (which directories belong to this program) off the same list.

    Returns `[]` on an unreadable or repo-less platform.yaml, which the lint treats
    as "declares nothing" — so every named repo would be flagged. The callers pass
    the result straight through; a program with no `repos:` block has no repo
    vocabulary to check against, and saying so loudly in a score is better than
    silently accepting any name.
    """
    try:
        import yaml as _yaml

        config_path = root / "platform.yaml"
        if not config_path.is_file():
            return []
        with open(config_path, encoding="utf-8") as f:
            config = _yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 - unreadable config → no declared vocabulary
        return []
    names: list[str] = []
    for repo in (config.get("repos") if isinstance(config, dict) else None) or []:
        if isinstance(repo, dict):
            name = str(repo.get("name") or "").strip()
        else:
            name = str(repo or "").strip()
        if name:
            names.append(name)
    return names


def _normalize_ce_platform_yaml_for_validation(config_path: Path) -> tuple[Path, list[str]]:
    """ce-org-agent-bootstrap task 4.1 — normalize CE-shaped platform.yaml in-memory.

    The schema in otaman-core requires `project`, `version`, and per-repo
    `owner`.  CE bootstrap historically wrote `agent:` per repo and
    sometimes omitted `project:` / `version:`.  This helper:

      - Treats `agent:` as alias for `owner:` on each repo entry (and
        strips `agent:` from the validation copy since repo items have
        `additionalProperties: false` in the schema)
      - Infers `project:` from the parent dir name when absent
      - Defaults `version:` to "1.0" when absent
      - Injects a synthetic placeholder repo into the validation copy
        when `repos:` is empty AND a CE-scaffold marker (`runner:` or
        `terminal:`) is present, so the schema's `repos: minItems: 1`
        check passes
      - Returns a path to a tmp file holding the normalized YAML when any
        change was made; otherwise returns the original path
      - Returns a list of human-readable hints for the caller to surface

    The on-disk source file is NEVER modified by this helper.  When changes
    were applied, the caller is responsible for unlinking the returned tmp
    path after the validator runs.

    History:
      - 2026-06-09 (PR #54): original 3 normalizations (agent→owner,
        project, version)
      - 2026-06-09 (PR #55): stripped `runner:` / `terminal:` /
        `agent_bootstrap:` as pass-through pending schema extension
      - 2026-06-10: otaman-core commit 27f2c7c allowlisted those root
        keys (and `bus:`, `agents:`, `orgs:`, `agent_presence:`, plus the
        program-init wizard set).  Pass-through stripping was removed in
        the follow-up cleanup; only the empty-repos placeholder remains
        for the CE org-dir scaffold use case.
    """
    import tempfile as _tmp

    import yaml as _yaml

    hints: list[str] = []
    if not config_path.is_file():
        # Defer to the validator; it will report the missing-file error itself
        return config_path, hints

    try:
        text = config_path.read_text(encoding="utf-8")
        doc = _yaml.safe_load(text) or {}
    except Exception:
        return config_path, hints

    if not isinstance(doc, dict):
        return config_path, hints

    changed = False

    # 1. version default
    if "version" not in doc:
        doc["version"] = "1.0"
        hints.append(
            'platform.yaml: `version:` field missing — defaulted to "1.0" for validation. '
            'Add `version: "1.0"` to the canonical file to silence this hint.'
        )
        changed = True

    # 2. project inferred from parent dir
    if "project" not in doc or not doc.get("project"):
        try:
            parent = config_path.resolve().parent
            inferred = parent.name or "ce-org"
            # Sanitize: lowercase, replace anything non-[a-z0-9-] with '-'
            import re as _re

            inferred = _re.sub(r"[^a-z0-9-]+", "-", inferred.lower()).strip("-") or "ce-org"
        except Exception:
            inferred = "ce-org"
        doc["project"] = inferred
        hints.append(
            f"platform.yaml: `project:` field missing — inferred {inferred!r} from "
            "parent directory name. Add `project: <slug>` to the canonical file."
        )
        changed = True

    # 3. repos: agent → owner alias.  The schema has additionalProperties:
    # false, so we must DROP `agent:` from the validation-time copy after
    # promoting it.  The user's on-disk file is untouched.
    repos = doc.get("repos")
    if isinstance(repos, list):
        promoted = 0
        for r in repos:
            if not isinstance(r, dict):
                continue
            agent_val = r.get("agent")
            owner_val = r.get("owner")
            if agent_val and not owner_val:
                r["owner"] = agent_val
                promoted += 1
            # Strip `agent:` from the validation copy whether or not we
            # promoted (e.g. if both were set, agent is still unknown).
            if "agent" in r:
                r.pop("agent", None)
                changed = True
        if promoted:
            hints.append(
                f"platform.yaml: {promoted} repo entry(ies) use `agent:` field — "
                "aliased to `owner:` for validation. Add `owner:` alongside "
                "`agent:` (same value) in the canonical file."
            )
            changed = True

    # 3b. scan-schema-conformance 1.1 — tolerantly strip known-RETIRED fields
    # (e.g. `is_spec_repo`, stamped onto spec repos by older scans) that the live
    # schema's additionalProperties:false rejects. Never FAIL on a field we know
    # is retired; strip it from the validation copy with a printed note. The
    # on-disk file is untouched — re-run `otaman scan --update` to rewrite it.
    from otaman_cli.onboard.schema_fields import strip_retired_fields

    retired = strip_retired_fields(doc)
    if retired:
        hints.append(
            "platform.yaml: stripped retired field(s) for validation: "
            + ", ".join(retired)
            + " — the top-level `specs:` block is the spec-repo marker now. "
            "Re-run `otaman scan --update` (or hand-edit) to drop them from the file."
        )
        changed = True

    # 4. Empty / missing `repos:` for fresh CE org-dir scaffolds.  The
    # schema's `repos: minItems: 1` constraint remains even after the
    # 2026-06-10 schema extension (otaman-core commit 27f2c7c) that
    # allowlisted `runner:` / `terminal:` / `bus:` / etc. as native root
    # keys.  Detect "CE org-dir scaffold" mode by the presence of a
    # `runner:` or `terminal:` block (both first-class in the schema now,
    # so we read them without stripping).  Inject a single synthetic
    # placeholder repo into the validation copy so the `minItems: 1`
    # check passes; the on-disk file's empty `repos:` is preserved.
    _CE_SCAFFOLD_MARKERS = ("runner", "terminal")
    has_ce_marker = any(k in doc for k in _CE_SCAFFOLD_MARKERS)
    if (
        not isinstance(doc.get("repos"), list) or len(doc.get("repos") or []) == 0
    ) and has_ce_marker:
        # Schema requires name to match ^[A-Za-z][A-Za-z0-9._-]{1,63}$
        # and owner to match ^[a-z][a-z0-9-]{1,63}$.
        doc["repos"] = [
            {
                "name": "ce-org-placeholder",
                "path": ".",
                "owner": "ops-agent",
            }
        ]
        hints.append(
            "platform.yaml: empty/missing `repos:` accepted for fresh "
            "CE org-dir scaffold (detected via runner:/terminal: marker). "
            "Programs will populate `repos:` as they are added; canonical "
            "files should list at least one repo."
        )
        changed = True

    if not changed:
        return config_path, hints

    # Write the normalized doc to a tmp file in the same directory so the
    # validator's relative-path resolution behaves the same way.
    parent_dir = config_path.parent
    try:
        fd, tmp_name = _tmp.mkstemp(
            prefix=".otaman-ce-norm-",
            suffix=".yaml",
            dir=str(parent_dir),
        )
    except OSError:
        # Fall back to system tmp if the parent dir isn't writable
        fd, tmp_name = _tmp.mkstemp(prefix=".otaman-ce-norm-", suffix=".yaml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return config_path, hints
    return tmp_path, hints


__all__ = ["_normalize_ce_platform_yaml_for_validation", "_read_platform_specs_path"]
