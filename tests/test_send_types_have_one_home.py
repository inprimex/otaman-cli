"""`otaman send`'s type list has ONE home, and it is core's.

cli used to keep its own narrower allow-list, and it drifted: `decision-required` was
valid in core, absent from cli's copy, and `cmd_send` hard-rejects an unregistered type,
so the emit every agent's operating rules make a DUTY before blocking exited 2:

    [!] Unknown message type 'decision-required'.

Three agents hit it while following the rule — spec-agent at 05:36, plugin-agent at
07:49 *while blocked*, and the measurement behind cli #278 — and each silently downgraded
to `type: question`. That is the invisible halt the duty exists to prevent, and the class
is already priced at ~11h of fleet delivery (2026-09-25) and a 62-hour frozen gate
(09-26).

#278 guarded the omission locally. spec-agent then ruled (20261003T083552) that the split
belongs to core, which shipped it as `MACHINE_EMITTED_TYPES` + a derived
`HAND_SENDABLE_TYPES` (core #124), with the ask that cli delete its interim list in the
SAME change that imports — an interim copy that still passes its own guard looks finished,
and is the next drift seed.

So these tests no longer assert a partition cli maintains. They assert cli maintains
NOTHING: that the import is live, that the one type whose absence broke a duty survives
the derivation, and that the fallback for an older core fails toward sendable.
"""

from __future__ import annotations

import inspect
import re

from otaman_core.validate_message import (
    HAND_SENDABLE_TYPES,
    MACHINE_EMITTED_TYPES,
    PRIVILEGED_TYPES,
    VALID_TYPES,
)

import otaman_cli.commands.bus_messaging as bus_messaging
from otaman_cli.commands.bus_messaging import MESSAGE_TYPES


def test_the_list_is_cores_not_a_copy():
    """Identity, not equality-by-value: a copy that happens to agree today is exactly
    what drifted before."""
    assert MESSAGE_TYPES is HAND_SENDABLE_TYPES


def test_cli_declares_no_type_list_of_its_own():
    """spec-agent's ask: the interim #278 list must not outlive what replaced it.

    Checked against the SOURCE, because a second list assigned under any other name is
    the same defect wearing a different label.
    """
    src = inspect.getsource(bus_messaging)
    head = src[: src.index("def ")] if "def " in src else src
    literal_sets = re.findall(r"^(\w+)\s*:?\s*[\w\[\], |]*=\s*frozenset\(\s*$", head, re.M)

    assert not literal_sets, (
        f"a literal type set is back in bus_messaging — import core's instead: {literal_sets}"
    )
    assert "MACHINE_EMITTED_TYPES: frozenset" not in src, "the interim #278 copy survived"


def test_decision_required_survives_the_derivation():
    """The regression that paid for all of this."""
    assert "decision-required" in MESSAGE_TYPES
    assert "decision-required" not in PRIVILEGED_TYPES, (
        "privileging it would put the anti-silent-block emit behind the TTY gate that "
        "only `otaman approve` has"
    )
    assert "decision-required" not in MACHINE_EMITTED_TYPES, "an agent sends this by hand"


def test_the_derivation_excludes_exactly_the_two_restricted_classes():
    """Pins what cli relies on core's constant MEANING, so a core change that redefines
    it is caught here rather than by an operator who cannot send a message."""
    assert set(MESSAGE_TYPES) == set(VALID_TYPES) - set(PRIVILEGED_TYPES) - set(
        MACHINE_EMITTED_TYPES
    )
    assert not set(MESSAGE_TYPES) & set(PRIVILEGED_TYPES)
    assert not set(MESSAGE_TYPES) & set(MACHINE_EMITTED_TYPES)


def test_an_older_core_falls_back_toward_SENDABLE_not_refused():
    """The fallback is the half that keeps the original defect from recurring anywhere.

    On a core predating #124 there is no `HAND_SENDABLE_TYPES` to import. cli must not
    carry a second copy of the split, and it must not fail toward refusing: a type cli
    cannot classify has to be OFFERED, because a forgotten hand-sendable type is an
    invisible halt while a forgotten machine type is one odd message machinery ignores.
    """
    src = inspect.getsource(bus_messaging)
    fallback = src[src.index("except ImportError") : src.index("#: The frontmatter keys")]

    assert "VALID_TYPES" in fallback and "PRIVILEGED_TYPES" in fallback
    assert "MACHINE_EMITTED" not in fallback, (
        "the fallback must not re-derive the machine split — that is the second copy"
    )
    # and the shape it produces offers more than it refuses
    derived = frozenset(VALID_TYPES) - PRIVILEGED_TYPES
    assert "decision-required" in derived
    assert set(MESSAGE_TYPES) <= derived, "the live list must be a subset of the fallback"


def test_the_outcome_registrys_narrow_write_guard_is_not_widened():
    """`registries/bus_messages.VALID_MESSAGE_TYPES` is a different thing with a
    confusingly similar name: the 7 types that module's own `build_*` functions produce,
    guarding `write_bus_message`. It must stay narrow — importing core's list there would
    let the outcome registry write any type — but every entry must be real in core.
    """
    from otaman_cli.registries.bus_messages import VALID_MESSAGE_TYPES

    assert set(VALID_MESSAGE_TYPES) <= set(VALID_TYPES), (
        f"invents a type core rejects: {sorted(set(VALID_MESSAGE_TYPES) - set(VALID_TYPES))}"
    )
    assert set(VALID_MESSAGE_TYPES) < set(MESSAGE_TYPES) | set(MACHINE_EMITTED_TYPES), (
        "still a strict subset — if this ever equals the hand-sendable list, someone "
        "'fixed' a deliberate write-guard into a general one"
    )
