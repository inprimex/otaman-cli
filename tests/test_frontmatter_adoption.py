"""shared-logic-single-home 1.2 — one bus-frontmatter parser.

Mirrors `test_bus_stem_adoption`: the transitional fallback may not drift from
core, so both go through the same matrix. The cases are the ones that made cli's
four local parsers differ from each other — a `cc:` scalar where a list was
expected, an `x-cc` left as the string "true" by a type-losing writer, and the
frontmatterless file a read surface must skip rather than crash on.
"""

from __future__ import annotations

import pytest

from otaman_cli.frontmatter_gate import FALLBACK, frontmatter, is_core_backed

core = pytest.importorskip("otaman_core.frontmatter")

DOCS = [
    "---\nid: x\nfrom: a\nto: b\ncc: [c, d]\nx-cc: true\n---\n\nbody\n",
    "---\nid: x\ncc: solo\n---\n\nbody\n",
    '---\nid: x\nx-cc: "true"\n---\n\nbody\n',
    "---\nid: x\nx-cc: false\n---\n\nbody\n",
    "---\ncc:\n  - a\n  - b\n---\n\nbody\n",
    "---\ncc: []\n---\n\n",
    "---\ncc:\n---\n\n",
    "no frontmatter at all\n",
    "",
    "---\nnot: [a mapping\n---\n\nbroken yaml\n",
    "---\n- just\n- a\n- list\n---\n\nnot a mapping\n",
    "---\nid: x\ntimestamp: 2026-09-21T14:00:00Z\n---\n\ntyped scalar\n",
]


@pytest.mark.parametrize("doc", DOCS, ids=range(len(DOCS)))
def test_fallback_parse_matches_core(doc):
    assert FALLBACK.parse(doc) == core.parse(doc)


@pytest.mark.parametrize("doc", DOCS, ids=range(len(DOCS)))
def test_fallback_cc_and_x_cc_match_core(doc):
    fm, _ = core.parse(doc)
    assert FALLBACK.cc_recipients(fm) == core.cc_recipients(fm)
    assert FALLBACK.is_cc_copy(fm) == core.is_cc_copy(fm)


def test_gate_resolves_core_when_present():
    assert is_core_backed()
    assert frontmatter() is core


def test_string_true_x_cc_is_a_cc_copy():
    """The case a plain `fm.get("x-cc")` truthy check got right by accident and
    a stricter `is True` would have got wrong: a type-losing writer or a
    hand-edited file leaves the string."""
    fm, _ = frontmatter().parse('---\nx-cc: "true"\n---\n\nb\n')
    assert frontmatter().is_cc_copy(fm) is True


def test_scalar_cc_normalizes_to_a_list():
    fm, _ = frontmatter().parse("---\ncc: just-one-agent\n---\n\nb\n")
    assert frontmatter().cc_recipients(fm) == ["just-one-agent"]


def test_frontmatterless_file_yields_empty_not_none():
    """core's contract, and the reason the two call sites keep their own sentinel.

    A read surface iterating a bus directory must SKIP a non-message, not crash
    on it — so the parser returns `({}, text)`. cli's `cleanup_bus` and
    `hitl.messages` translate that back to `None` at their own boundary, because
    their callers distinguish "not a message" from "empty block".
    """
    fm, body = frontmatter().parse("plain file\n")
    assert fm == {}
    assert body == "plain file\n"


def test_laggard_core_still_parses(monkeypatch):
    """The degradation claim, exercised: `otaman check` must survive an old core."""
    import builtins
    import sys

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "otaman_core.frontmatter" or (
            name == "otaman_core" and len(a) > 2 and a[2] and "frontmatter" in a[2]
        ):
            raise ImportError("simulated core without frontmatter")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.delitem(sys.modules, "otaman_core.frontmatter", raising=False)

    from otaman_cli.frontmatter_gate import frontmatter as gate

    api = gate()
    assert api is FALLBACK, "the laggard path did not engage — this test proves nothing"
    fm, body = api.parse("---\nid: x\ncc: [a]\n---\n\nbody\n")
    assert fm["id"] == "x"
    assert api.cc_recipients(fm) == ["a"]
    assert body.strip() == "body"


def test_deliberately_local_parsers_stay_local():
    """Two frontmatter-shaped readers are NOT routed to core, on purpose.

    Pinned so a later sweep does not "finish the job" and silently undo either:
    `models_report` reads agent-definition files for five string fields (a
    different domain, and YAML-typing them would change their types), and
    `console.bus._frontmatter_head` reads a BOUNDED 8KB head — the bound that
    took the console scan over ~3000 messages from 7-9s.
    """
    from otaman_cli import models_report
    from otaman_cli.console import bus

    assert callable(models_report.parse_frontmatter)
    assert callable(bus._frontmatter_head)
    assert "limit" in bus._frontmatter_head.__code__.co_varnames
