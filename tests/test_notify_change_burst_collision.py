"""notify-change must not lose messages in a burst (deploy-agent, 20260921T190949).

Reported live on mildef/haulops: six back-to-back `notify-change` calls, all
exiting 0 and printing correct recipients, left the bus holding 10 messages
instead of ~19. Two changes were represented nowhere at all. The dispatches were
same-second, and the per-recipient filename carried no change name, so the second
write to a given recipient landed on the identical path and `write_text`
overwrote the first.

The failure class is the one `bus_write` was built for — cofounder-agent lost an
SCR the same way via `otaman propose` in 2026-09-05 — but this writer imported
that module's validator and then wrote with plain `write_text`, bypassing the
collision-safe path allocation entirely.

These tests pin BOTH halves of the fix, because either alone still loses data:
the change name in the stem (so distinct changes never contend), and the
create-exclusive write (so anything that still contends is suffixed, not lost).
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.notify_change import notify_change
from tests.test_notify_change import _stage_change, _stage_workspace


def _bus(project: Path) -> Path:
    return project / ".agents" / "bus" / "active"


def test_burst_of_changes_to_one_recipient_loses_nothing(tmp_path: Path):
    """deploy's reproduction, minimized: six changes, one shared recipient."""
    project, specs = _stage_workspace(tmp_path)
    names = [f"change-{i}" for i in range(6)]
    for name in names:
        _stage_change(specs, name, "- [ ] 1.1 @otaman-cli build it\n")

    for name in names:
        rc, _ = notify_change(project, name)
        assert rc == 0

    msgs = list(_bus(project).glob("*spec-change*.md"))
    assert len(msgs) == 6, f"expected 6 messages, found {len(msgs)}"

    # Every change must be represented. This is the assertion that failed live:
    # exit 0 six times, four changes on the bus.
    bodies = "\n".join(m.read_text(encoding="utf-8") for m in msgs)
    missing = [n for n in names if n not in bodies]
    assert not missing, f"changes dispatched but absent from the bus: {missing}"


def test_change_name_appears_in_the_stem(tmp_path: Path):
    """The natural discriminator, and what a human scanning `ls` wants to see."""
    project, specs = _stage_workspace(tmp_path)
    _stage_change(specs, "vehicle-firmware-baseline", "- [ ] 1.1 @otaman-cli x\n")
    rc, _ = notify_change(project, "vehicle-firmware-baseline")
    assert rc == 0
    stem = next(_bus(project).glob("*spec-change*.md")).stem
    assert "vehicle-firmware-baseline" in stem
    # The routing segments ack/check parse must survive ahead of it.
    assert "-to-cli-agent-" in stem
    assert stem.endswith("-spec-change")


def test_same_change_dispatched_twice_in_a_second_is_suffixed_not_overwritten(
    tmp_path: Path,
):
    """The residual collision the change name cannot fix — must not lose either."""
    project, specs = _stage_workspace(tmp_path)
    _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-cli x\n")

    assert notify_change(project, "ch1")[0] == 0
    assert notify_change(project, "ch1")[0] == 0

    msgs = list(_bus(project).glob("*spec-change*.md"))
    assert len(msgs) == 2, f"a re-dispatch overwrote the first: {[m.name for m in msgs]}"


def test_reported_paths_are_the_paths_actually_written(tmp_path: Path):
    """A `-2` suffix must reach the summary, or the caller reports a phantom file.

    bus_write's contract is explicit that callers use the RETURNED path; the
    report line and any downstream reference are wrong otherwise.
    """
    project, specs = _stage_workspace(tmp_path)
    _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-cli x\n")

    notify_change(project, "ch1")
    _, summary = notify_change(project, "ch1")

    for reported in summary["message_paths"]:
        assert Path(reported).is_file(), f"summary names a file that does not exist: {reported}"


def test_multi_recipient_burst_keeps_every_recipient_copy(tmp_path: Path):
    """Fan-out × burst: the shape that lost 2 of 4 recipients live."""
    project, specs = _stage_workspace(tmp_path)
    tasks = "- [ ] 1.1 @otaman-cli a\n- [ ] 1.2 @otaman-core b\n- [ ] 1.3 @otaman-plugin c\n"
    for name in ("alpha", "beta"):
        _stage_change(specs, name, tasks)
        assert notify_change(project, name)[0] == 0

    for agent in ("cli-agent", "core-agent", "plugin-agent"):
        got = list(_bus(project).glob(f"*-to-{agent}-*spec-change*.md"))
        assert len(got) == 2, f"{agent} got {len(got)} of 2 changes"
