"""`Outcome.remainder` — the field whose ABSENCE was the rac 1.4 blocker.

`Outcome` is `extra="forbid"`, so a record carrying `remainder:` made the whole
outcomes register unloadable by cli, and `load_outcomes` raises on an
unloadable register by design. core-agent measured and corrected the
attribution (20261011T085447): core #117 is lenient by construction and never
gated loading, so the missing field here — not any absent core delivery — was
what blocked cofounder's 1.4 migration of the seven legacy `Done-Partial` rows
to `Done` + remainder.

Shape follows core canon (`outcomes-registry-schema.yaml`): a string,
`minLength: 1`, OPTIONAL rather than required-if-Done — that rule would reject
the 30 clean `Done` records that cut nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from otaman_cli.registries.outcomes import Outcome, OutcomeStatus, load_outcomes


def _record(**over) -> dict:
    rec = {
        "id": "JTBD-1-ship-the-thing",
        "statement": {
            "as-a": "maintainer",
            "i-want-to": "ship",
            "incremental-outcome": "shipped",
            "so-i-can": "move on",
        },
        "status": "Done",
        "created": "2026-01-01",
        "updated": "2026-01-02",
    }
    rec.update(over)
    return rec


def test_a_done_record_with_a_remainder_validates():
    """The exact shape cofounder's 1.4 migration writes."""
    o = Outcome.model_validate(_record(remainder="the import path was cut to v1 only"))
    assert o.status is OutcomeStatus.DONE
    assert o.remainder == "the import path was cut to v1 only"


def test_remainder_is_optional_so_clean_done_records_still_validate():
    """Required-if-Done would reject the 30 Done records that cut nothing."""
    o = Outcome.model_validate(_record())
    assert o.remainder is None


def test_the_register_loads_when_a_record_carries_a_remainder(tmp_path: Path):
    """The actual blocker, at the level it bit: whole-register loadability.

    `load_outcomes` is strict=True — a present-but-unloadable register RAISES,
    because the console's fallback notice and doctor's unloadable-register
    check are both read off that failure. So one migrated record took the
    entire register down, not just its own row.
    """
    path = tmp_path / "outcomes-declared.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "outcomes": [
                    _record(remainder="deferred the bulk importer"),
                    _record(id="JTBD-2-other-thing"),
                ]
            }
        ),
        encoding="utf-8",
    )

    reg = load_outcomes(path)

    assert [o.id for o in reg.outcomes] == ["JTBD-1-ship-the-thing", "JTBD-2-other-thing"]
    assert reg.get("JTBD-1-ship-the-thing").remainder == "deferred the bulk importer"
    assert reg.get("JTBD-2-other-thing").remainder is None


def test_an_unknown_field_is_still_forbidden():
    """The fix widens the model by exactly one field — it does not open it up.

    `extra="forbid"` is load-bearing: it is what makes a typo'd key a loud
    failure instead of a silently-dropped value.
    """
    with pytest.raises(ValidationError, match="extra"):
        Outcome.model_validate(_record(reminder="typo of remainder"))


def test_done_partial_is_still_abolished():
    """The enum half was already done; assert it stayed done, since the whole
    point of the migration is that this status has no home any more."""
    assert "Done-Partial" not in {s.value for s in OutcomeStatus}
    with pytest.raises(ValidationError):
        Outcome.model_validate(_record(status="Done-Partial"))
