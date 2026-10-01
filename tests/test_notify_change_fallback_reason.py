"""`notify-change` told the human WHY it chose its recipients — and it was wrong.

A CTO-review finding I verified rather than took on faith, and the measurement
changed what the fix is. The review said `notify_change` "silently misroutes every
spec-change notification when platform.yaml fails to parse, exit 0". Measured: an
unparseable platform.yaml makes `_resolve_specs_path` return None first, so the
command exits **1** with a named error. That path was already correct.

What IS reachable, measured against scaffolded programs:

    healthy                        -> ['cli-agent']              exit 0
    unparseable platform.yaml      -> (refused earlier)          exit 1
    repos: is a mapping, not a list-> ['spec-agent','human']     exit 0
    repos: entries carry no owner  -> ['spec-agent','human']     exit 0
    repos: key absent entirely     -> ['spec-agent','human']     exit 0
    annotation matches no repo     -> ['spec-agent','human']     exit 0

Four situations collapsed into one list, and the message body then asserted:

    Fallback: `spec-agent` when no tasks.md exists; `spec-agent, human`
    when no annotations.

False in all four — there WERE annotations. And false in the one that matters most:
a typo'd `@otaman-cl` routes the entire dispatch away from the agent who owns the
work, and the human receiving it is told the change assigns nobody.

So the defect is not the routing. The routing is a deliberate under-notify. The
defect is that the notification MISSTATES its own reason, which is what makes a
typo undiagnosable from the thing it produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.notify_change import (
    FALLBACK_RECIPIENTS,
    derive_recipients,
    notify_change,
    resolve_recipients,
)

PLATFORM_OK = """\
project: demo
version: '1.0'
specs:
  path: ../demo-specs
repos:
  - name: otaman-cli
    owner: cli-agent
"""


def _scaffold(tmp_path: Path, platform: str, *, tasks: str = "- [ ] 1.1 @otaman-cli do a thing\n"):
    proj = tmp_path / "demo"
    specs = tmp_path / "demo-specs"
    proj.mkdir()
    (proj / "platform.yaml").write_text(platform, encoding="utf-8")
    change = specs / "openspec" / "changes" / "ch1"
    change.mkdir(parents=True)
    if tasks is not None:
        (change / "tasks.md").write_text(tasks, encoding="utf-8")
    return proj, specs


# ---------------------------------------------------------------------------
# The reason is stated, and it is the right one.


def test_a_healthy_program_resolves_the_owner_with_no_reason(tmp_path):
    proj, specs = _scaffold(tmp_path, PLATFORM_OK)

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == ["cli-agent"]
    assert reason == "", "a correct dispatch needs no commentary"


@pytest.mark.parametrize(
    ("platform", "fragment"),
    [
        (
            "project: demo\nspecs:\n  path: ../demo-specs\nrepos:\n  otaman-cli: cli-agent\n",
            "`repos:` is not a list",
        ),
        (
            "project: demo\nspecs:\n  path: ../demo-specs\nrepos:\n  - name: otaman-cli\n",
            "names no owners",
        ),
        ("project: demo\nspecs:\n  path: ../demo-specs\n", "declares no `repos:`"),
        (
            "project: demo\nspecs:\n  path: ../demo-specs\n"
            "repos:\n  - name: otaman-web\n    owner: web-agent\n",
            "match no `repos[].name`",
        ),
    ],
)
def test_each_reachable_fallback_names_its_own_cause(tmp_path, platform, fragment):
    """THE DEFECT. All four produced `['spec-agent', 'human']` and a body asserting
    "when no annotations" — four different problems, one false explanation."""
    proj, specs = _scaffold(tmp_path, platform)

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == FALLBACK_RECIPIENTS
    assert fragment in reason, f"the reason did not name the cause: {reason!r}"


def test_a_typod_annotation_is_named_with_the_text_that_was_typed(tmp_path):
    """The expensive case. `@otaman-cl` routes the dispatch away from cli-agent, and
    the reason has to carry the string so the typo is visible, not just the fact."""
    proj, specs = _scaffold(tmp_path, PLATFORM_OK, tasks="- [ ] 1.1 @otaman-cl do a thing\n")

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == FALLBACK_RECIPIENTS
    assert "otaman-cl" in reason


def test_no_annotations_is_the_one_case_the_old_text_described(tmp_path):
    """The legitimate case: nobody is assigned yet. It keeps its own wording rather
    than inheriting a generic one."""
    proj, specs = _scaffold(tmp_path, PLATFORM_OK, tasks="- [ ] 1.1 do a thing\n")

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == FALLBACK_RECIPIENTS
    assert "no `@otaman-<repo>` annotation" in reason


def test_no_tasks_md_goes_to_spec_agent_alone_with_no_reason(tmp_path):
    """Unchanged behaviour, and not a fallback: there is nothing to assign from, so
    the change's author is the only recipient that makes sense."""
    proj, specs = _scaffold(tmp_path, PLATFORM_OK, tasks=None)

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == ["spec-agent"]
    assert reason == ""


def test_partial_resolution_notifies_the_owners_it_found_and_says_what_it_missed(tmp_path):
    """Owners resolved for SOME annotations. Nothing fell back — the recipients are
    real owners — so this must not be described as a fallback, which would be a
    second false statement in exactly the place the first one was."""
    proj, specs = _scaffold(
        tmp_path,
        PLATFORM_OK,
        tasks="- [ ] 1.1 @otaman-cli mine\n- [ ] 1.2 @otaman-nope theirs\n",
    )

    recipients, reason = resolve_recipients(specs, "ch1", proj / "platform.yaml")

    assert recipients == ["cli-agent"], "the resolvable owner is still notified"
    assert recipients != FALLBACK_RECIPIENTS
    assert "otaman-nope" in reason


# ---------------------------------------------------------------------------
# The list itself did not change.


@pytest.mark.parametrize(
    "platform",
    [
        PLATFORM_OK,
        "project: demo\nspecs:\n  path: ../demo-specs\n",
        "project: demo\nspecs:\n  path: ../demo-specs\nrepos:\n  - name: otaman-web\n"
        "    owner: web-agent\n",
    ],
)
def test_derive_recipients_returns_exactly_what_it_did_before(tmp_path, platform):
    """The public wrapper is unchanged — this is an additive reason, not a routing
    change, and `spec-change-hook.sh` parity must hold."""
    proj, specs = _scaffold(tmp_path, platform)

    assert (
        derive_recipients(specs, "ch1", proj / "platform.yaml")
        == resolve_recipients(specs, "ch1", proj / "platform.yaml")[0]
    )


# ---------------------------------------------------------------------------
# What the message and the summary carry.


def _notify(tmp_path, platform, **kw):
    proj, specs = _scaffold(tmp_path, platform, **kw)
    rc, summary = notify_change(proj, "ch1")
    bodies = [Path(p).read_text(encoding="utf-8") for p in summary.get("message_paths") or []]
    return rc, summary, bodies


def test_the_written_message_states_the_real_reason(tmp_path):
    rc, summary, bodies = _notify(
        tmp_path,
        "project: demo\nspecs:\n  path: ../demo-specs\n",
    )

    assert rc == 0
    assert summary["recipients"] == FALLBACK_RECIPIENTS
    assert bodies, "a notification must have been written"
    for body in bodies:
        assert "**Why these recipients**" in body
        assert "declares no `repos:`" in body
        # The sentence that was false in four of five cases.
        assert "when no annotations" not in body


def test_a_correct_dispatch_keeps_the_plain_derivation_sentence(tmp_path):
    """The explanation must not become noise on every healthy notification."""
    rc, summary, bodies = _notify(tmp_path, PLATFORM_OK)

    assert rc == 0
    assert summary["recipients"] == ["cli-agent"]
    assert summary["fallback_reason"] == ""
    for body in bodies:
        assert "**Why these recipients**" not in body
        assert "derived from `tasks.md`" in body


def test_the_summary_carries_the_reason_for_the_caller(tmp_path):
    """The operator can fix a typo'd annotation and sees the summary, not the body
    they just wrote."""
    _rc, summary, _bodies = _notify(tmp_path, PLATFORM_OK, tasks="- [ ] 1.1 @otaman-cl typo\n")

    assert summary["fallback_reason"]
    assert "otaman-cl" in summary["fallback_reason"]


def test_an_unparseable_platform_yaml_still_refuses_early(tmp_path):
    """The CTO review's stated trigger, measured: this exits 1 with a named error,
    because the specs path is resolved from the same file first. Pinned so the
    finding's premise stays on record as checked rather than assumed."""
    proj, _specs = _scaffold(tmp_path, "repos: [\n  - name: x\n")
    rc, summary = notify_change(proj, "ch1")

    assert rc == 1
    assert "specs repo path" in summary["error"]
