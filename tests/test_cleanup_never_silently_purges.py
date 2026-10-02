"""`otaman cleanup` destroyed 591 messages on its first successful run in four months.

deploy-agent, 20261001T220034, after running the real thing on otaman-dev:

    left active     2113
    reached archive 1522
    DELETED          591      = archive/2026-05/ (212) + archive/2026-06/ (379)

Recovered only because that bus is a git repo with the deletions uncommitted. A tenant
without that is simply short 591 messages. An emergency-halt went out fleet-wide.

Two defects in one invocation, and the parse fix (cli #234) is what made the second
reachable for the first time:

**(a) Step 3 assumes an archived month has already had `delete_days` of life AS AN
ARCHIVED MONTH.** False the first time the archiver runs after an outage: Step 2 created
2026-05 and 2026-06, Step 3 purged them seconds later.

**(b) `cleanup` deleted by default.** The command reads as hygiene. Destroying 591
messages is not what an operator expects from it.

And the reporting defect deploy put FIRST, which is why nobody caught it in the dry run:

    [+] Archived: 2113 message(s)        <- truncated block
    [!] Deleted: 2 archive(s)            <- 591 messages, counted as DIRECTORIES
          2026-05 (212 messages)         <- reads like an archive manifest
          2026-06 (379 messages)
    [!] (dry run — no changes made)      <- past-tense "Deleted" in a dry run

Deploy reported that the word "delete" never appeared. Measured: it did appear — but as
`Deleted: 2 archive(s)`, past tense, inside a dry run, between two larger blocks, with the
count being of directories and the word "permanently" absent. Their conclusion was right
and their mechanism was slightly off, which matters only because the fix differs: the word
was not missing, the FRAMING was. A dry run that does not distinguish moved from destroyed
is not a preview.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from otaman_cli.cleanup_bus import cleanup


def _bus(root, messages, *, agents=("cli-agent",)):
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    (root / ".agents" / "agents.yaml").write_text(
        "agents:\n" + "".join(f"  - name: {a}\n    role: developer\n" for a in agents),
        encoding="utf-8",
    )
    for name, days, to in messages:
        ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        (active / f"{name}.md").write_text(
            f"---\nid: {name}\nfrom: x\nto: {to}\ntype: info\n"
            f"timestamp: {ts}\nstatus: pending\n---\n\n## Subject: s\nb\n",
            encoding="utf-8",
        )
        (active / "acks" / f"{name}.{to}.ack").write_text("resolved\n", encoding="utf-8")
    return active


def _archive_month(root, month, count=2):
    """A month directory that already EXISTS — i.e. archived by some earlier run."""
    month_dir = root / ".agents" / "bus" / "archive" / month
    month_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (month_dir / f"old{i}.md").write_text("---\nid: x\n---\n", encoding="utf-8")
    return month_dir


# ---------------------------------------------------------------------------
# (b) purging is opt-in.


def test_purging_does_not_happen_by_default(tmp_path):
    """THE incident. `cleanup` with no flags must not destroy anything."""
    root = tmp_path / "p"
    _bus(root, [])
    month = _archive_month(root, "2026-01", count=3)

    report = cleanup(root, dry_run=False)

    assert month.is_dir(), "cleanup deleted an archive month with no --purge"
    assert report["purge_withheld"] is True
    assert report["deleted_months"] == ["2026-01"]
    assert report["deleted_message_count"] == 3


def test_purging_happens_with_the_flag(tmp_path):
    """The capability is unchanged — only the default moved."""
    root = tmp_path / "p"
    _bus(root, [])
    month = _archive_month(root, "2026-01", count=3)

    report = cleanup(root, dry_run=False, purge=True)

    assert not month.exists()
    assert report["purge_withheld"] is False
    assert report["deleted_message_count"] == 3


def test_a_dry_run_with_purge_destroys_nothing(tmp_path):
    root = tmp_path / "p"
    _bus(root, [])
    month = _archive_month(root, "2026-01", count=3)

    report = cleanup(root, dry_run=True, purge=True)

    assert month.is_dir(), "a dry run deleted a month"
    assert report["deleted_message_count"] == 3


def test_a_month_inside_the_retention_window_is_untouched(tmp_path):
    """Unchanged behaviour: `delete_days` still governs WHICH months are candidates."""
    root = tmp_path / "p"
    _bus(root, [])
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m")
    month = _archive_month(root, recent, count=2)

    report = cleanup(root, dry_run=False, purge=True)

    assert month.is_dir()
    assert report["deleted_months"] == []


# ---------------------------------------------------------------------------
# (a) a month this run archived into is never purged in the same invocation.


def test_a_month_this_run_created_is_not_purged(tmp_path):
    """THE mechanism. A 200-day-old message is archived by Step 2 into a month that is
    immediately past `delete_days` — and was purged seconds later. Even WITH --purge,
    the month this run wrote must survive: `delete_days` measures time spent as an
    archived message, and that clock starts now."""
    root = tmp_path / "p"
    _bus(root, [("20260101T000001-x-to-cli-agent-a", 200, "cli-agent")])

    report = cleanup(root, dry_run=False, purge=True)

    assert report["archived"] == ["20260101T000001-x-to-cli-agent-a.md"]
    assert report["deleted_months"] == [], "the month it just created was purged"
    assert report["purge_skipped_fresh"], "and the refusal was not stated"
    archived_file = next((root / ".agents" / "bus" / "archive").rglob("*.md"), None)
    assert archived_file is not None, "the message is gone from both active and archive"


def test_the_refusal_names_the_month_and_its_size(tmp_path):
    """Kept silently would be the same class of defect one level down."""
    root = tmp_path / "p"
    _bus(root, [("20260101T000001-x-to-cli-agent-a", 200, "cli-agent")])

    report = cleanup(root, dry_run=False, purge=True)

    assert any("(1 messages)" in entry for entry in report["purge_skipped_fresh"])


def test_a_dry_run_predicts_the_same_refusal(tmp_path):
    """A preview of a different operation than the real one is not a preview — which is
    the whole lesson of this incident."""
    root = tmp_path / "p"
    _bus(root, [("20260101T000001-x-to-cli-agent-a", 200, "cli-agent")])

    dry = cleanup(root, dry_run=True, purge=True)

    assert dry["purge_skipped_fresh"], "the dry run did not predict the refusal"
    assert dry["deleted_months"] == []


def test_an_older_month_is_still_purgeable_alongside_a_fresh_one(tmp_path):
    """The refusal is scoped to months THIS run touched, not to purging in general."""
    root = tmp_path / "p"
    _bus(root, [("20260101T000001-x-to-cli-agent-a", 200, "cli-agent")])
    stale = _archive_month(root, "2025-01", count=4)

    report = cleanup(root, dry_run=False, purge=True)

    assert not stale.exists(), "a month from an earlier run was spared"
    assert report["deleted_months"] == ["2025-01"]
    assert report["purge_skipped_fresh"], "and the fresh month was still kept"


# ---------------------------------------------------------------------------
# The reporting, which deploy put first.


class _Result:
    returncode = 0
    stderr = ""

    def __init__(self, report):
        self.stdout = json.dumps(report)


def _render(tmp_path, monkeypatch, capsys, report, args):
    from otaman_cli.commands import cleanup as cleanup_cmd

    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cleanup_cmd, "run_script", lambda *a, **kw: _Result(report))
    cleanup_cmd.cmd_cleanup(args)
    return capsys.readouterr().out


_BASE = {
    "migrated": 0,
    "archived": ["m.md"],
    "status_orphans": [],
    "active_count": 1,
    "archive_count": 0,
    "errors": [],
    "skipped_unparseable": 0,
    "skipped_no_frontmatter": 0,
    "skipped_samples": [],
    "held_unacked": 0,
    "deleted": [],
    "deleted_months": [],
    "deleted_message_count": 0,
    "purge_withheld": False,
    "purge_skipped_fresh": [],
}


def test_the_dry_run_counts_messages_not_directories(tmp_path, monkeypatch, capsys):
    """591 messages rendered as "2 archive(s)". The number an operator's eye lands on
    has to be the number of things that stop existing."""
    report = {
        **_BASE,
        "deleted_months": ["2026-05", "2026-06"],
        "deleted_message_count": 591,
    }
    out = _render(tmp_path, monkeypatch, capsys, report, ["--dry-run", "--purge"])

    assert "591 message(s)" in out
    assert "2 archive(s)" not in out


def test_the_dry_run_says_permanently_and_unrecoverable(tmp_path, monkeypatch, capsys):
    """ "Permanently", plus the recoverability of what is being destroyed.

    The second half used to read "Unrecoverable unless this directory is
    version-controlled" — true, and a condition the operator could not evaluate while
    the run could. It is now MEASURED per month (cli: purge-names-the-unrecoverable),
    so this asserts the measured sentence for each of the three states instead of one
    conditional. A report with no `deleted_detail` is the pre-measurement shape, which
    must still warn rather than fall silent.
    """
    report = {
        **_BASE,
        "deleted_months": ["2026-05"],
        "deleted_message_count": 212,
        "deleted_unbacked": 212,
        "deleted_detail": [{"month": "2026-05", "messages": 212, "unbacked": 212}],
    }
    out = _render(tmp_path, monkeypatch, capsys, report, ["--dry-run", "--purge"])

    assert "Would DELETE PERMANENTLY" in out
    assert "212 of those message(s) are NOT in git HEAD" in out
    assert "nothing to restore them from" in out

    unknown = {
        **_BASE,
        "deleted_months": ["2026-05"],
        "deleted_message_count": 212,
        "deleted_unbacked": 0,
        "deleted_detail": [{"month": "2026-05", "messages": 212, "unbacked": None}],
    }
    out = _render(tmp_path, monkeypatch, capsys, unknown, ["--dry-run", "--purge"])
    assert "Would DELETE PERMANENTLY" in out
    assert "unrecoverable" in out, "an unmeasurable month must still warn"

    backed = {
        **_BASE,
        "deleted_months": ["2026-05"],
        "deleted_message_count": 212,
        "deleted_unbacked": 0,
        "deleted_detail": [{"month": "2026-05", "messages": 212, "unbacked": 0}],
    }
    out = _render(tmp_path, monkeypatch, capsys, backed, ["--dry-run", "--purge"])
    assert "Would DELETE PERMANENTLY" in out, "committed does not mean harmless"
    assert "restorable" in out


def test_archival_is_labelled_recoverable(tmp_path, monkeypatch, capsys):
    """The distinction the old output collapsed: moved is not destroyed."""
    out = _render(tmp_path, monkeypatch, capsys, dict(_BASE), ["--dry-run"])

    assert "moved, recoverable" in out


def test_a_dry_run_speaks_in_the_future_tense(tmp_path, monkeypatch, capsys):
    """Past-tense "Deleted" inside a dry run is what made the output ambiguous."""
    report = {**_BASE, "deleted_months": ["2026-05"], "deleted_message_count": 212}
    dry = _render(tmp_path, monkeypatch, capsys, report, ["--dry-run", "--purge"])
    real = _render(tmp_path, monkeypatch, capsys, report, ["--purge"])

    assert "Would archive" in dry and "Would DELETE" in dry
    assert "DELETED PERMANENTLY" in real and "Would DELETE" not in real


def test_the_withheld_case_says_what_was_kept_and_how_to_proceed(tmp_path, monkeypatch, capsys):
    report = {
        **_BASE,
        "deleted_months": ["2026-05", "2026-06"],
        "deleted_message_count": 591,
        "purge_withheld": True,
    }
    out = _render(tmp_path, monkeypatch, capsys, report, ["--dry-run"])

    assert "WITHHELD: 591 message(s)" in out
    assert "--purge" in out
    assert "irreversible" in out
    assert "DELETE PERMANENTLY" not in out, "nothing was deleted, so do not say it was"


def test_the_fresh_month_refusal_is_rendered(tmp_path, monkeypatch, capsys):
    report = {**_BASE, "purge_skipped_fresh": ["2026-05 (212 messages)"]}
    out = _render(tmp_path, monkeypatch, capsys, report, ["--purge"])

    assert "retention starts now" in out
    assert "2026-05 (212 messages)" in out


def test_a_clean_run_mentions_neither_purging_nor_withholding(tmp_path, monkeypatch, capsys):
    """No noise on the common path."""
    out = _render(tmp_path, monkeypatch, capsys, dict(_BASE), ["--dry-run"])

    assert "PERMANENTLY" not in out
    assert "WITHHELD" not in out
    assert "retention starts now" not in out


def test_the_help_line_warns_that_purge_deletes(tmp_path):
    """`otaman help` is where an operator learns what a flag does before using it."""
    from otaman_cli import commands as registry

    spec = registry.get("cleanup")

    assert "--purge" in spec.help
    assert "DELETE" in spec.help


@pytest.mark.parametrize("flag", ["--purge"])
def test_the_flag_reaches_the_script(tmp_path, monkeypatch, flag):
    """A flag the command swallows would make the surface lie about what it ran."""
    from otaman_cli.commands import cleanup as cleanup_cmd

    seen = {}

    def fake(*args, **kwargs):
        seen["args"] = args
        return _Result(dict(_BASE))

    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cleanup_cmd, "run_script", fake)
    cleanup_cmd.cmd_cleanup([flag])

    assert flag in seen["args"]
