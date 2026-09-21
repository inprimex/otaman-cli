"""shared-logic-single-home 1.3 — one home for the bus stem convention.

Two things are pinned here.

**The fallback may not drift from core.** `bus_stem_gate` carries a transitional
shim so a laggard otaman-core cannot take out `otaman send` (see that module for
why refusing was not an option). A second implementation is only acceptable if
something forces it to stay identical, so both go through the same input matrix
— including the inputs the old hand-rolled slugifiers got wrong.

**The call sites may not hand-build stems again.** 1.3's value is not that the
four writers produce the same strings today; it is that there is one place to
change when the convention moves. A structural check keeps them routed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from otaman_cli.bus_stem_gate import FALLBACK, bus_stem, is_core_backed

core = pytest.importorskip("otaman_core.bus_stem")

SRC = Path(__file__).resolve().parent.parent / "src" / "otaman_cli"

#: Inputs chosen for the edges, not the happy path: the empty slug, the
#: all-punctuation slug, the boundary where truncation lands on a hyphen, and
#: the `/` that would otherwise open a subdirectory in a filename.
SLUG_CASES = [
    "Specs changed in otaman-specs",
    "MERGED: core-invokable gate (rnsc 1.3)",
    "",
    "!!!",
    "---",
    "a" * 60,
    "word " * 20,
    "trailing-hyphen-at-thirty-chars--x",
    "Ünïcödé subject line",
    "slash/in/subject",
]


@pytest.mark.parametrize("text", SLUG_CASES)
@pytest.mark.parametrize("max_len", [None, 30, 40])
def test_fallback_slugify_matches_core(text, max_len):
    assert FALLBACK.slugify(text, max_len=max_len) == core.slugify(text, max_len=max_len)


@pytest.mark.parametrize(
    "sender,recipient,slug",
    [
        ("cli-agent", "core-agent", "hello"),
        ("otaman-specs", "cli-agent", "change-name-spec-change"),
        ("human", "all", "spec-change-approved"),
        ("org/team", "other/team", "slashes-in-route"),
    ],
)
def test_fallback_build_stem_matches_core(sender, recipient, slug):
    kw = {"timestamp": "20260921T221501", "sender": sender, "recipient": recipient, "slug": slug}
    assert FALLBACK.build_stem(**kw) == core.build_stem(**kw)
    assert FALLBACK.build_filename(**kw) == core.build_filename(**kw)


@pytest.mark.parametrize(
    "name",
    [
        "20260921T221501-cli-agent-to-core-agent-hello.md",
        "20260921T221501-cli-agent-to-core-agent-hello",
        "not-a-stem.md",
        "",
    ],
)
def test_fallback_timestamp_of_matches_core(name):
    assert FALLBACK.timestamp_of(name) == core.timestamp_of(name)


def test_gate_resolves_core_when_present():
    """In this workspace core IS present — the shim must not be what ships."""
    assert is_core_backed()
    assert bus_stem() is core


def test_slug_never_yields_a_stem_ending_in_a_bare_hyphen():
    """The latent bug the local slugifiers carried.

    `re.sub(...).strip("-")[:30]` truncates AFTER stripping, so a slug whose
    30th character is a hyphen ended the stem with one; and a subject that
    reduced to nothing produced a stem ending in `-`. Several call sites had no
    fallback token at all.
    """
    for text in ("!!!", "", "---", "trailing-hyphen-at-thirty-chars--x"):
        for max_len in (None, 30):
            slug = bus_stem().slugify(text, max_len=max_len)
            assert slug, f"{text!r} slugged to nothing"
            assert not slug.endswith("-"), f"{text!r} -> {slug!r}"


# ---------------------------------------------------------------------------
# the call sites stay routed


#: Files allowed to spell the route format themselves, with the reason.
_ROUTE_LITERAL_ALLOWED = {
    "bus_stem_gate.py": "the transitional fallback — held to core by the tests above",
}

#: Lines allowed to mention the route format for READING, not writing.
#: `f"-to-{agent}-" in stem` asks whether a stem is addressed to an agent; it
#: builds no filename and cannot drift the convention. Routing it through
#: parse_stem would mean loading the agent registry to answer a membership
#: question, which is a worse trade.
_READ_SIDE_ALLOWED = {
    ("commands/bus_messaging.py", "in stem or stem.endswith"),
    # Not a bus route at all: a lifecycle phase label that happens to read
    # `<phase>-to-<phase>`. Kept explicit rather than narrowing the detector,
    # because narrowing it further is what created its first blind spot.
    ("commands/misc_readonly.py", "transition = "),
}


def _route_literals() -> list[str]:
    """f-strings that spell the `<sender>-to-<recipient>` route format by hand.

    AST over the f-string's literal parts, not a substring scan of the line.
    The first version matched the text `-to-{`, which silently missed every
    writer with a LITERAL recipient — `f"{ts}-human-to-all-{slug}-…"` — and
    five such writers existed. A guard that passes while the thing it guards
    is still wrong is worse than no guard.
    """
    out: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if path.name in _ROUTE_LITERAL_ALLOWED:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if "-to-" not in literal or "\n" in literal:
                # A newline means prose — help text and multi-line messages
                # mention "-to-" innocently. A route format never spans lines.
                continue
            if not (node.values and isinstance(node.values[0], ast.FormattedValue)):
                # Every stem starts with its interpolated timestamp. A literal
                # first segment means a fragment being MATCHED, not built.
                continue
            line = lines[node.lineno - 1].strip()
            if any(rel == f and frag in line for f, frag in _READ_SIDE_ALLOWED):
                continue
            out.append(f"{rel}:{node.lineno}: {line}")
    return out


def test_no_call_site_hand_builds_the_route_format():
    offenders = _route_literals()
    assert not offenders, (
        "These build the bus route format by hand instead of calling "
        "bus_stem().build_stem()/build_filename():\n  " + "\n  ".join(offenders)
    )


def test_the_route_check_can_see_a_violation():
    """Pins the detector against BOTH shapes, including the one it used to miss."""
    samples = [
        'f"{ts}-{repo}-to-{recipient}-{change}-spec-change.md"',  # interpolated recipient
        'f"{now_ts}-human-to-all-{slug}-spec-change-approved.md"',  # LITERAL recipient
        'f"{ts}-human-to-{target}-nudge-{name}"',  # a stem, no .md
    ]
    for src in samples:
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                lit = "".join(
                    v.value
                    for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                found = found or "-to-" in lit
        assert found, f"detector no longer sees: {src}"


def test_a_laggard_core_still_writes_stems(monkeypatch):
    """The degradation claim, exercised rather than asserted.

    `bus_stem_gate` chose to fall back instead of refusing because stem
    construction backs `otaman send` and `notify-change`. That is only true if
    the fallback actually engages when core lacks the module — so this
    simulates the laggard bundle and requires a usable stem to come out.
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "otaman_core.bus_stem" or (
            name == "otaman_core" and len(a) > 2 and a[2] and "bus_stem" in a[2]
        ):
            raise ImportError("simulated core without bus_stem")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.delitem(sys.modules, "otaman_core.bus_stem", raising=False)

    from otaman_cli.bus_stem_gate import bus_stem as gate

    api = gate()
    assert api is FALLBACK, "the laggard path did not engage — this test proves nothing"
    assert (
        api.build_stem(
            timestamp="20260921T230000", sender="cli-agent", recipient="core-agent", slug="hello"
        )
        == "20260921T230000-cli-agent-to-core-agent-hello"
    )
    assert api.slugify("!!!") == "item"
    assert api.timestamp_of("20260921T230000-a-to-b-c.md") == "20260921T230000"
