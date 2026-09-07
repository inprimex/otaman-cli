"""SLE 2.1 / D3 — the doctor spec-lifecycle section (WARN-only surface).

Shares the derivation with `otaman spec status` + the console view. Surfaces the
stalled buckets (WARN day 1 / ERROR day 3) and the month's ratification count;
never changes doctor's exit code here (block-mode enforcement is the gates' job,
SLE 2.2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from otaman_cli.commands.doctor import _check_spec_lifecycle, _print_spec_lifecycle_report


def _prog(tmp_path):
    r = tmp_path / "prog"
    (r / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (r / "specs" / "openspec" / "changes").mkdir(parents=True)
    (r / "platform.yaml").write_text("project: demo\nspecs:\n  path: specs\n", encoding="utf-8")
    return r


def _approved(root, title, *, days_ago):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stem = ts.replace("-", "").replace(":", "") + "-human-to-all-spec-change-approved"
    (root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\ntype: spec-change-approved\ntimestamp: {ts}\n---\n\n## Subject: Approved: {title}\n",
        encoding="utf-8",
    )


def test_stale_approval_is_a_fail_finding(tmp_path):
    root = _prog(tmp_path)
    _approved(root, "stuck", days_ago=5)
    result = _check_spec_lifecycle(root)
    assert result["available"] is True
    assert len(result["stalled"]) == 1
    assert result["stalled"][0]["level"] == "error"
    assert "stuck" in result["stalled"][0]["message"]


def test_fresh_approval_not_flagged(tmp_path):
    root = _prog(tmp_path)
    _approved(root, "fresh", days_ago=0)
    result = _check_spec_lifecycle(root)
    assert result["stalled"] == []  # under a day → ok, not surfaced


def test_ratifications_counted(tmp_path):
    root = _prog(tmp_path)
    now = datetime.now(timezone.utc)
    folder = root / "specs" / "openspec" / "changes" / "ratified-change"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text("- [x] done @otaman-cli\n", encoding="utf-8")
    import yaml

    (folder / ".openspec.yaml").write_text(
        yaml.safe_dump(
            {
                "stage": "approved",
                "ratified": True,
                "ratified_at": now.strftime("%Y-%m-15T00:00:00Z"),
            }
        ),
        encoding="utf-8",
    )
    result = _check_spec_lifecycle(root)
    assert result["ratifications"] == 1


def test_report_quiet_when_clean(tmp_path, capsys):
    root = _prog(tmp_path)
    _print_spec_lifecycle_report(_check_spec_lifecycle(root))
    assert capsys.readouterr().out == ""  # no stalled + no ratifications → silent


def test_report_prints_section_when_stalled(tmp_path, capsys):
    root = _prog(tmp_path)
    _approved(root, "stuck", days_ago=5)
    _print_spec_lifecycle_report(_check_spec_lifecycle(root))
    out = capsys.readouterr().out
    assert "Spec Lifecycle" in out and "FAIL" in out and "stuck" in out
