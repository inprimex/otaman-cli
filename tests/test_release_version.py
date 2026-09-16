"""version-authority 1.3 — exactly one version answers "what am I running".

The otaman-deploy RELEASE is authoritative. A component version (this package's,
a sibling repo's tag, a branch position) must never be presented as the installed
version: "fixed in 0.4.0" is unverifiable for a tenant whose installation is a
deploy release.

The concrete defect: `otaman --version` printed `otaman 0.4.0` (otaman-cli via
importlib.metadata) while the live deploy release was v0.5.7.
"""

from __future__ import annotations

import pytest

from otaman_cli.release_version import (
    banner_version,
    format_version,
    resolve_release_version,
)


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "release.yaml"


# ---------------------------------------------------------------------------
# resolution


def test_release_read_from_the_marker(marker):
    marker.write_text("release: v0.5.7\nchannel: ee\n", encoding="utf-8")
    r = resolve_release_version("0.4.0", candidates=[(marker, ("release", "version"))])
    assert r.resolved is True
    assert r.release == "v0.5.7" and r.quotable == "v0.5.7"
    assert r.component == "0.4.0"
    assert str(marker) in r.source


def test_unresolved_when_no_marker_exists(tmp_path):
    missing = tmp_path / "nope.yaml"
    r = resolve_release_version("0.4.0", candidates=[(missing, ("release",))])
    assert r.resolved is False and r.release == "" and r.quotable == ""
    assert r.component == "0.4.0"  # the component is still known


def test_first_candidate_wins(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("release: v1\n", encoding="utf-8")
    b.write_text("release: v2\n", encoding="utf-8")
    r = resolve_release_version("x", candidates=[(a, ("release",)), (b, ("release",))])
    assert r.release == "v1"


def test_falls_through_an_empty_candidate(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("channel: ee\n", encoding="utf-8")  # no release key
    b.write_text("release: v2\n", encoding="utf-8")
    r = resolve_release_version("x", candidates=[(a, ("release",)), (b, ("release",))])
    assert r.release == "v2"


def test_edition_version_is_read_as_a_FALLBACK(tmp_path):
    """CORRECTED (deploy-agent, 2026-09-16). This originally asserted the
    opposite, on my premise that `edition.yaml`'s `version:` is "never
    refreshed" — it read 0.3.0 against a live v0.5.7. That premise was wrong:
    the field IS rewritten every bootstrap (five EE tenants all read 0.5.7
    minutes after that roll); the 0.3.0 was the fleet DEV BOX, which releases are
    never rolled to. Accurate for an old machine, not a broken mechanism.

    So it is a legitimate fallback — a truthful answer on any upgraded tenant
    today, rather than a blank until deploy's writer reaches them."""
    ed = tmp_path / "edition.yaml"
    ed.write_text('edition: ee\nchannel: ee\nversion: "0.5.7"\n', encoding="utf-8")
    r = resolve_release_version("0.4.0", candidates=[(ed, ("release", "version"))])
    assert r.resolved is True and r.release == "0.5.7"


def test_release_yaml_wins_over_the_edition_fallback(tmp_path):
    """Preference order matters: `release.yaml` carries the TAG (`v0.5.7`),
    which is what canon says to quote; edition.yaml has only the bare form."""
    rel, ed = tmp_path / "release.yaml", tmp_path / "edition.yaml"
    rel.write_text("release: v0.5.7\n", encoding="utf-8")
    ed.write_text('version: "0.5.7"\n', encoding="utf-8")
    r = resolve_release_version(
        "0.4.0", candidates=[(rel, ("release", "version")), (ed, ("release", "version"))]
    )
    assert r.release == "v0.5.7"  # the tag, not the bare version


@pytest.mark.parametrize(
    "body", ["", "not: a mapping\n- list\n", "::: not yaml :::", "release:\n", "release: '   '\n"]
)
def test_unreadable_or_empty_markers_mean_unknown_never_a_crash(tmp_path, body):
    """An unreadable marker must not become a confident wrong answer."""
    p = tmp_path / "release.yaml"
    p.write_text(body, encoding="utf-8")
    assert resolve_release_version("x", candidates=[(p, ("release",))]).resolved is False


def test_non_string_release_value_is_ignored(tmp_path):
    p = tmp_path / "release.yaml"
    p.write_text("release: 507\n", encoding="utf-8")
    assert resolve_release_version("x", candidates=[(p, ("release",))]).resolved is False


# ---------------------------------------------------------------------------
# rendering — the component version must never read as the thing to quote


def test_resolved_output_leads_with_the_release_and_labels_the_component(marker):
    marker.write_text("release: v0.5.7\n", encoding="utf-8")
    out = format_version(resolve_release_version("0.4.0", candidates=[(marker, ("release",))]))
    assert "v0.5.7" in out
    assert "quote this version" in out
    assert "component: 0.4.0" in out
    # the component must not be the first thing a reader sees
    assert out.index("v0.5.7") < out.index("0.4.0")


def test_unresolved_output_says_what_the_number_is_NOT(tmp_path):
    out = format_version(
        resolve_release_version("0.4.0", candidates=[(tmp_path / "none.yaml", ("release",))])
    )
    assert "0.4.0" in out
    assert "NOT your installed version" in out
    assert "otaman-deploy release" in out
    # never a bare `otaman <version>` line a tenant would quote
    assert not out.startswith("otaman 0.4.0")


def test_banner_shows_the_release_when_known(marker):
    marker.write_text("release: v0.5.7\n", encoding="utf-8")
    assert banner_version(
        resolve_release_version("0.4.0", candidates=[(marker, ("release",))])
    ) == ("v0.5.7")


def test_banner_labels_the_component_when_unknown(tmp_path):
    v = banner_version(
        resolve_release_version("0.4.0", candidates=[(tmp_path / "none.yaml", ("release",))])
    )
    assert v == "cli 0.4.0"  # labelled, so the banner never implies a shipping version


# ---------------------------------------------------------------------------
# the CLI surface


def test_version_flag_no_longer_prints_a_bare_component_version(capsys, monkeypatch):
    """REGRESSION: `otaman --version` printed `otaman 0.4.0` — a sibling package
    version a tenant would reasonably quote as their installed version."""
    import otaman_cli.main as m

    monkeypatch.setattr(m, "VERSION", "0.4.0", raising=False)
    monkeypatch.setattr("sys.argv", ["otaman", "--version"])
    m.main()
    out = capsys.readouterr().out
    assert "otaman 0.4.0" not in out
    assert "0.4.0" in out  # still reported, as a component
