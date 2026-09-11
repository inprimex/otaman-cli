"""bus-writer-self-validation 1.2 — the write-time validation gate.

`assert_message_valid` / `write_message_exclusive(validate=True)` refuse to write
a message the validator would reject; nothing reaches disk. This is the chokepoint
that makes a silent-invalid bus message structurally impossible.
"""

from __future__ import annotations

import pytest

from otaman_cli.bus_write import (
    BusMessageValidationError,
    assert_message_valid,
    write_message_exclusive,
)

_VALID = (
    "---\nid: 20260911T120000-cli-agent-to-spec-agent-hi\nfrom: cli-agent\nto: spec-agent\n"
    "priority: normal\ntype: info\ntimestamp: 2026-09-11T12:00:00Z\nstatus: pending\n---\n\n"
    "## Subject: hi\n\nbody\n"
)

# `to: all` with a non-broadcast type is rejected by validate_message.
_INVALID_BROADCAST = (
    "---\nid: 20260911T120000-cli-agent-to-all-hi\nfrom: cli-agent\nto: all\n"
    "priority: normal\ntype: info\ntimestamp: 2026-09-11T12:00:00Z\nstatus: pending\n---\n\n"
    "## Subject: hi\n\nbody\n"
)


def test_assert_message_valid_passes_clean():
    assert_message_valid(_VALID)  # no raise


def test_assert_message_valid_raises_on_invalid():
    with pytest.raises(BusMessageValidationError) as ei:
        assert_message_valid(_INVALID_BROADCAST)
    assert ei.value.errors  # at least one error captured


def test_write_exclusive_refuses_invalid_and_writes_nothing(tmp_path):
    target = tmp_path / "msg.md"
    with pytest.raises(BusMessageValidationError):
        write_message_exclusive(target, _INVALID_BROADCAST, validate=True)
    assert not target.exists()  # nothing written
    assert list(tmp_path.glob("*.md")) == []


def test_write_exclusive_writes_valid_when_validating(tmp_path):
    target = tmp_path / "msg.md"
    out = write_message_exclusive(target, _VALID, validate=True)
    assert out.exists() and out.read_text(encoding="utf-8") == _VALID


def test_validate_default_off_until_console_writers_align(tmp_path):
    # The gate is opt-in (default off) so it doesn't silently break the console
    # info-to-all broadcasts pending their broadcast-type ruling; the conformant
    # writers pass validate=True explicitly.
    target = tmp_path / "msg.md"
    out = write_message_exclusive(target, _INVALID_BROADCAST)  # default: no gate
    assert out.exists()
