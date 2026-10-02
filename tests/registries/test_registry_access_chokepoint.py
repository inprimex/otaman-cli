"""registry-access-contract 1.2 — the chokepoint holds, and its debt is written down.

The capability spec: "every registry access goes through the access contract; a direct
registry-file access outside it is a conformance defect." Task 1.2 asks for the rewire
plus a "grep-guard with debt register for any survivor", and task 3.1 verifies with
"grep confirms zero direct file reads outside the contract".

So the guard is not a hand-maintained list of exceptions in a test: it reads the
survivors from `docs/registry-access-debt.md`. A new survivor can only pass by being
justified in writing, and a justification that no longer describes the code fails too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "otaman_cli"
DEBT_DOC = REPO / "docs" / "registry-access-debt.md"

#: Raw file verbs — the ways a module can open a YAML file for itself.
RAW_VERBS = ("yaml_load(", "yaml_dump(", "yaml_read(", "load_file(")

#: Marks a module as talking about a REGISTER (as opposed to any other YAML).
REGISTER_MARKERS = (
    "resolve_registry_path",
    "strategy_repo(",
    '"outcomes.yaml"',
    '"solutions.yaml"',
    '"personas.yaml"',
    "`outcomes.yaml`",
    "`solutions.yaml`",
    "`personas.yaml`",
)

#: The door itself is never a survivor.
EXEMPT = {"registries/access.py"}

#: YAML files that are NOT registers. A raw verb whose argument names one of these is
#: reading configuration, not a register — `platform.yaml` is the program's config (and
#: where the register's own home is declared), `llm-routes.yaml` has no id-keyed
#: records. Matched on the CALL LINE, so a module may legitimately read config with a
#: raw verb and still be held to the contract for every register it touches.
NOT_A_REGISTER = (
    "platform.yaml",
    # the same file reached through a variable (`load_file(platform_yaml_path, {})`)
    "platform_yaml",
    "program-meta.yaml",
    "llm-routes.yaml",
    "agents.yaml",
    ".openspec.yaml",
)


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def allowed_survivors() -> set[str]:
    """Paths listed under `## Allowed survivors` in the debt register.

    Parsed from the doc rather than duplicated here: the register is the authority on
    which survivors are justified, so the guard cannot disagree with it.
    """
    text = DEBT_DOC.read_text(encoding="utf-8")
    section = text.split("## Allowed survivors", 1)
    assert len(section) == 2, "the debt register has no `## Allowed survivors` section"
    body = section[1].split("\n## ", 1)[0]
    found = set()
    for line in body.splitlines():
        if not line.startswith("- `"):
            continue
        m = re.match(r"- `([^`]+)`", line)
        assert m, f"malformed survivor line: {line!r}"
        found.add(m.group(1))
    return found


def _register_verb_lines(text: str) -> list[tuple[int, str]]:
    """Raw-verb call lines that are not visibly reading a non-register file."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if not any(v in line for v in RAW_VERBS):
            continue
        if any(name in line for name in NOT_A_REGISTER):
            continue
        hits.append((n, line.strip()))
    return hits


def direct_accessors() -> dict[str, list[tuple[int, str]]]:
    """Modules that open a registry file themselves: a raw verb + a register marker.

    Per-LINE, not per-file: a module that reads `platform.yaml` with a raw verb and
    every register through the contract is conformant, and a file-granular guard would
    either miss that distinction or demand a bogus justification for it.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = _rel(path)
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if not any(m in text for m in REGISTER_MARKERS):
            continue
        hits = _register_verb_lines(text)
        if hits:
            out[rel] = hits
    return out


def test_every_direct_registry_accessor_is_a_justified_survivor():
    """The guard: no module opens a registry file unless the debt register says why."""
    survivors = {s.removeprefix("src/otaman_cli/") for s in allowed_survivors()}
    found = direct_accessors()
    unjustified = sorted(f"{rel}:{found[rel][0][0]}" for rel in found if rel not in survivors)
    assert not unjustified, (
        "these modules open a registry file outside the access contract and are not in "
        f"docs/registry-access-debt.md: {unjustified}\n"
        "  Rewire onto otaman_cli.registries.access, or justify the survivor in the "
        "debt register."
    )


def test_the_debt_register_has_no_stale_survivors():
    """A survivor that no longer accesses a register must leave the register.

    Without this half, the allowlist only ever grows and stops describing the code —
    the failure mode of every hand-kept exception list.
    """
    accessors = set(direct_accessors())
    stale = sorted(
        s for s in allowed_survivors() if s.removeprefix("src/otaman_cli/") not in accessors
    )
    assert not stale, (
        f"docs/registry-access-debt.md lists survivors that no longer open a registry "
        f"file: {stale}. Delete the entry — the debt is paid."
    )


def test_every_survivor_path_exists():
    for rel in sorted(allowed_survivors()):
        assert (REPO / rel).is_file(), f"debt register names a missing file: {rel}"


@pytest.mark.parametrize(
    "module",
    ["cli_outcome.py", "cli_solution.py", "cli_persona.py"],
)
def test_the_rewired_write_paths_hold_no_raw_verbs(module: str):
    """The three write surfaces carry no raw YAML verb at all — not even in a fallback.

    An old bundle refuses (`access.NO_CONTRACT`); it does not write around the door.
    Asserted per-module so a regression names the surface that reopened the second home.
    """
    text = (SRC / "registries" / module).read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    offenders = [v for v in RAW_VERBS if v in code]
    assert not offenders, f"{module} calls {offenders} — rewire onto registries.access"
