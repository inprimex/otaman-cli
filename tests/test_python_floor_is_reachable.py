"""The declared `requires-python` floor must be one a user can actually stand on.

Found while repointing `cleanup_bus` onto core's `parse_bus_timestamp` (core #102).
cli #234 had normalized fractional seconds to six digits because **3.10's**
`fromisoformat` accepts only 3 or 6 — a careful guard for an interpreter this package
has never been installable on: `otaman-core` is a hard dependency and declares
`requires-python = ">=3.11"`, so a 3.10 resolve fails before any of this runs.

A declared floor below a dependency's floor is a claim nothing checks. It does not
fail at install with a clear message; it fails later, deeper, somewhere a tenant has
to read a traceback to understand. Same class as every other unchecked claim this repo
has been closing: the cost is not the wrong number, it is that nothing noticed.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

#: Siblings this package depends on, by pyproject name -> checkout directory. Only
#: the otaman ones: a third-party floor is pip's to enforce at resolve time, and this
#: guard exists for the siblings whose pyproject sits next to ours and drifts with it.
SIBLINGS = {
    "otaman-core": "otaman-core",
    "otaman-adapters": "otaman-adapters",
    "otaman-bridge": "otaman-bridge",
    "otaman-plugin": "otaman-plugin",
}

_FLOOR = re.compile(r'^requires-python\s*=\s*"[^0-9]*(?P<major>\d+)\.(?P<minor>\d+)', re.M)


def _floor(pyproject: pathlib.Path) -> tuple[int, int] | None:
    """`(major, minor)` from a pyproject's `requires-python`, or None."""
    if not pyproject.is_file():
        return None
    match = _FLOOR.search(pyproject.read_text(encoding="utf-8"))
    if match is None:
        return None
    return int(match.group("major")), int(match.group("minor"))


def test_this_package_declares_a_floor():
    assert _floor(ROOT / "pyproject.toml") is not None, "requires-python is missing"


def test_the_declared_dependencies_are_the_ones_this_guard_knows_about():
    """A new otaman dependency must be added to SIBLINGS, or this guard quietly stops
    covering it — the stale-register failure mode, so the register is checked."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = set(re.findall(r'^\s*"(otaman-[a-z-]+)"', text, re.M))

    assert declared <= set(SIBLINGS), (
        f"otaman dependencies not covered by this guard: {sorted(declared - set(SIBLINGS))}"
    )


@pytest.mark.parametrize(("name", "directory"), sorted(SIBLINGS.items()))
def test_our_floor_is_not_below_a_dependencys_floor(name, directory):
    """THE finding. cli said >=3.10 while otaman-core says >=3.11.

    Skipped when the sibling is not checked out: this is the dev and CI workspace
    shape (the test workflow checks every sibling out), and a wheel-only run has no
    pyproject to read. A skip states that it could not check — it does not pass.
    """
    ours = _floor(ROOT / "pyproject.toml")
    theirs = _floor(WORKSPACE / directory / "pyproject.toml")
    if theirs is None:
        pytest.skip(f"{name} is not checked out beside this repo — floor not checked")

    assert ours >= theirs, (
        f"{ROOT.name} declares >={ours[0]}.{ours[1]} but depends on {name}, which "
        f"declares >={theirs[0]}.{theirs[1]} — a {ours[0]}.{ours[1]} install cannot "
        "resolve, so the floor is a claim nothing can satisfy"
    )


def test_the_floor_matches_what_ci_actually_runs():
    """A floor CI never exercises is a floor nobody has tried. CI runs one version;
    the declared floor must not be above it, or the matrix tests something the
    package forbids."""
    workflow = ROOT / ".github" / "workflows" / "test.yml"
    if not workflow.is_file():
        pytest.skip("no test workflow to read")
    versions = {
        (int(m.group(1)), int(m.group(2)))
        for m in re.finditer(r"python-version:\s*'?(\d+)\.(\d+)", workflow.read_text("utf-8"))
    }
    if not versions:
        pytest.skip("the workflow pins no python-version")
    ours = _floor(ROOT / "pyproject.toml")

    assert ours <= min(versions), (
        f"declared floor >={ours[0]}.{ours[1]} is above CI's lowest {min(versions)} — "
        "CI would be testing a version the package declares unsupported"
    )
