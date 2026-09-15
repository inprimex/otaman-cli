"""release-notes-fragments 1.2 — the merge-time changelog-fragment gate.

Live incident 2026-09-11: release notes for v0.5.2–v0.5.4, drafted by
summarizing commit/PR history, spliced raw internal CLI error strings into
public copy. Release-cut can only assemble what the fragment pile contains, so
the discipline has to hold at MERGE time — this is that gate.

The classification is an allowlist of not-shipped paths with everything else
treated as shipped, so the check fails toward REQUIRING a fragment: a false
block is visible and clearable in one line, a false pass silently drops a
customer-facing note that nothing downstream can recover.
"""

from __future__ import annotations

import pytest

from otaman_cli.changelog_fragment import (
    FragmentConfig,
    evaluate,
    fragment_paths,
    fragment_required_by_policy,
    has_exemption,
    is_shipped_path,
    resolve_fragment_config,
    shipped_paths,
)

#: core's shipped standard (GIT_STANDARD_RULES) as this check reads it.
CORE_RULES = {
    "require_changelog_fragment": True,
    "changelog_fragment": {
        "dir": "changelog.d",
        "filename": "<pr>.<category>.md",
        "categories": ["feature", "fix", "doc", "removal", "misc"],
        "exemption_marker": "changelog: exempt",
    },
}


# ---------------------------------------------------------------------------
# config comes from core's policy, never redefined here


def test_config_read_from_policy_rules():
    cfg = resolve_fragment_config(CORE_RULES)
    assert cfg.dir == "changelog.d"
    assert cfg.exemption_marker == "changelog: exempt"
    assert "feature" in cfg.categories


def test_config_falls_back_when_block_absent():
    cfg = resolve_fragment_config({"require_changelog_fragment": True})
    assert cfg.dir == "changelog.d" and cfg.categories  # shipped defaults
    cfg2 = resolve_fragment_config(None)
    assert cfg2.dir == "changelog.d"


def test_config_honors_a_program_override():
    cfg = resolve_fragment_config(
        {"changelog_fragment": {"dir": "notes", "categories": ["x"], "exemption_marker": "skip"}}
    )
    assert cfg.dir == "notes" and cfg.categories == ("x",) and cfg.exemption_marker == "skip"


def test_rule_off_or_absent_means_not_required():
    assert fragment_required_by_policy(CORE_RULES) is True
    assert fragment_required_by_policy({"require_changelog_fragment": False}) is False
    assert fragment_required_by_policy({}) is False


def test_example_path_names_the_pr_when_known():
    cfg = resolve_fragment_config(CORE_RULES)
    assert cfg.example_path(148) == "changelog.d/148.feature.md"
    assert cfg.example_path() == "changelog.d/<pr>.feature.md"


# ---------------------------------------------------------------------------
# the shipped-code classification


@pytest.mark.parametrize(
    "path",
    [
        "src/otaman_cli/main.py",
        "pyproject.toml",
        "scripts/launch.sh",
        "src/otaman_cli/templates/launch.j2",
    ],
)
def test_shipped_paths(path):
    assert is_shipped_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/test.yml",
        "docs/guide.rst",
        "doc/guide.rst",
        "tests/test_x.py",
        "test/test_x.py",
        ".gitignore",
        ".editorconfig",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "src/otaman_cli/notes.md",  # a .md under src is still documentation
        "changelog.d/148.feature.md",  # the pile itself
    ],
)
def test_not_shipped_paths(path):
    assert is_shipped_path(path) is False


def test_dot_github_is_not_shipped_regression():
    """REGRESSION: `_normalize` once used lstrip("./"), which strips a character
    SET — so `.github/...` lost its leading dot and escaped the CI pattern,
    producing a false 'shipped code' verdict. Caught by a smoke test on real
    paths, pinned here."""
    assert is_shipped_path(".github/workflows/test.yml") is False
    assert is_shipped_path("./.github/workflows/test.yml") is False


def test_leading_dot_slash_does_not_hide_shipped_code():
    assert is_shipped_path("./src/otaman_cli/main.py") is True


def test_backslash_paths_are_normalized():
    assert is_shipped_path(r"tests\test_x.py") is False
    assert is_shipped_path(r"src\otaman_cli\main.py") is True


def test_shipped_paths_filters():
    got = shipped_paths(["src/a.py", "docs/b.md", "tests/c.py", "pyproject.toml"])
    assert got == ["src/a.py", "pyproject.toml"]


def test_blank_and_empty_paths_ignored():
    assert is_shipped_path("") is False
    assert is_shipped_path("   ") is False


# ---------------------------------------------------------------------------
# fragment recognition


def test_valid_fragment_is_recognized():
    cfg = resolve_fragment_config(CORE_RULES)
    assert fragment_paths(["changelog.d/148.feature.md"], cfg) == ["changelog.d/148.feature.md"]


def test_fragment_requires_a_known_category():
    cfg = resolve_fragment_config(CORE_RULES)
    # a stray note in the pile is not a fragment
    assert fragment_paths(["changelog.d/notes.md", "changelog.d/148.md"], cfg) == []
    assert fragment_paths(["changelog.d/148.bogus.md"], cfg) == []


def test_fragment_category_is_case_insensitive():
    cfg = resolve_fragment_config(CORE_RULES)
    assert fragment_paths(["changelog.d/148.FEATURE.MD"], cfg) == ["changelog.d/148.FEATURE.MD"]


def test_fragment_must_live_in_the_configured_dir():
    cfg = resolve_fragment_config(CORE_RULES)
    assert fragment_paths(["elsewhere/148.feature.md"], cfg) == []


# ---------------------------------------------------------------------------
# exemption marker


def test_exemption_marker_matched_tolerantly():
    cfg = resolve_fragment_config(CORE_RULES)
    for text in (
        "changelog: exempt",
        "Changelog: Exempt",
        "changelog:exempt",
        "docs only.\n\nchangelog:   exempt\n",
    ):
        assert has_exemption(text, cfg) is True, text


def test_exemption_absent_or_empty():
    cfg = resolve_fragment_config(CORE_RULES)
    assert has_exemption(None, cfg) is False
    assert has_exemption("", cfg) is False
    assert has_exemption("mentions changelog but not the marker", cfg) is False


def test_exemption_with_a_markerless_config():
    cfg = FragmentConfig(exemption_marker="")
    assert has_exemption("anything", cfg) is False


# ---------------------------------------------------------------------------
# the verdict — the two spec scenarios first


def test_missing_fragment_blocks_and_names_the_path():
    v = evaluate(["src/a.py"], CORE_RULES, pr=148)
    assert v.required is True and v.ok is False
    assert v.expected_path == "changelog.d/148.feature.md"
    assert v.shipped == ["src/a.py"]


def test_docs_only_pr_with_marker_passes():
    v = evaluate(["docs/g.md"], CORE_RULES, pr=148, exemption_text="changelog: exempt")
    assert v.ok is True


def test_fragment_present_passes():
    v = evaluate(["src/a.py", "changelog.d/148.fix.md"], CORE_RULES, pr=148)
    assert v.ok is True and v.fragments == ["changelog.d/148.fix.md"]


def test_docs_only_passes_without_a_marker_too():
    """A docs-only PR does not touch shipped code, so the requirement never
    attaches — the marker is for asserting exemption on a PR that DOES."""
    v = evaluate(["docs/g.md", "README.md"], CORE_RULES, pr=148)
    assert v.ok is True and v.required is False


def test_shipped_code_with_marker_is_exempted():
    v = evaluate(["src/a.py"], CORE_RULES, pr=148, exemption_text="changelog: exempt")
    assert v.ok is True and v.required is True and v.exempted is True


def test_mixed_ci_and_shipped_still_requires():
    v = evaluate([".github/workflows/t.yml", "src/a.py"], CORE_RULES, pr=148)
    assert v.ok is False and v.shipped == ["src/a.py"]


def test_rule_off_short_circuits_to_pass():
    v = evaluate(["src/a.py"], {"require_changelog_fragment": False}, pr=148)
    assert v.ok is True and v.required is False


def test_a_sibling_prs_fragment_does_not_satisfy_this_pr():
    """When the PR number is known, its OWN fragment counts first — a PR must not
    ride on another's fragment that merely appears in its diff."""
    v = evaluate(["src/a.py", "changelog.d/999.fix.md"], CORE_RULES, pr=148)
    # still OK (a fragment exists in the pile), but the PR-specific match is empty
    assert v.ok is True
    own = [f for f in v.fragments if "148" in f]
    assert own == []


def test_pr_specific_fragment_preferred_when_present():
    v = evaluate(
        ["src/a.py", "changelog.d/999.fix.md", "changelog.d/148.feature.md"],
        CORE_RULES,
        pr=148,
    )
    assert v.ok is True and v.fragments == ["changelog.d/148.feature.md"]


def test_works_without_a_pr_number():
    v = evaluate(["src/a.py"], CORE_RULES)
    assert v.ok is False and v.expected_path == "changelog.d/<pr>.feature.md"
    v2 = evaluate(["src/a.py", "changelog.d/x.fix.md"], CORE_RULES)
    assert v2.ok is True


def test_empty_diff_is_not_a_block():
    v = evaluate([], CORE_RULES, pr=148)
    assert v.ok is True and v.required is False


def test_reason_is_always_populated():
    for paths in ([], ["src/a.py"], ["docs/x.md"], ["src/a.py", "changelog.d/1.fix.md"]):
        assert evaluate(paths, CORE_RULES, pr=1).reason


# ---------------------------------------------------------------------------
# the CLI surface


def test_check_changelog_is_dispatched(monkeypatch, capsys):
    """The action reaches the handler with its parsed flags (no project needed:
    the handler bails on context, which proves dispatch happened)."""
    from otaman_cli.commands import policy as P

    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(P, "_cmd_check_changelog", fake)
    rc = P.cmd_policy(
        [
            "check-changelog",
            "--base",
            "origin/main",
            "--pr",
            "148",
            "--paths",
            "src/a.py",
            "src/b.py",
            "--pr-body",
            "body text",
            "--json",
        ]
    )
    assert rc == 0
    assert seen["base"] == "origin/main"
    assert seen["pr"] == "148"
    assert seen["paths_arg"] == ["src/a.py", "src/b.py"]
    assert seen["body"] == "body text"
    assert seen["as_json"] is True


def test_check_changelog_rejects_a_valueless_flag(capsys):
    from otaman_cli.commands import policy as P

    assert P.cmd_policy(["check-changelog", "--pr"]) == 2
    assert "needs a value" in capsys.readouterr().out


def test_check_changelog_rejects_unknown_argument(capsys):
    from otaman_cli.commands import policy as P

    assert P.cmd_policy(["check-changelog", "--nope"]) == 2
    assert "Unexpected argument" in capsys.readouterr().out


def test_usage_lists_check_changelog(capsys):
    from otaman_cli.commands import policy as P

    P.cmd_policy([])
    assert "check-changelog" in capsys.readouterr().out
