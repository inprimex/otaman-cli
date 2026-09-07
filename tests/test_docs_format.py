"""docs-format-check 1.4 — conformance tests for the fence-aware table lint/fix.

Replays the four 2026-09-07 corpus breakage classes (raw '<' eats a row;
template-inherited short rows; bad separator; unfenced ASCII art untouched by
--fix and flagged by lint), plus the no-targets no-op (D2) and fence-skip. Also
covers `otaman validate docs` wiring and the doctor advisory row.
"""

from __future__ import annotations

from otaman_cli import docs_format as d


def _rules(problems):
    return {msg.split()[0] for _ln, msg in problems}


# ---------------------------------------------------------------------------
# the four breakage classes


def test_class1_raw_lt_flagged_and_escaped():
    text = "| A | B |\n|---|---|\n| <tag | y |\n"
    assert "R3" in _rules(d.lint_text(text))
    fixed, _ = d.process_text(text, fix=True)
    assert "\\<tag" in fixed and "<tag" not in fixed.replace("\\<tag", "")


def test_class2_short_row_padded():
    text = "| A | B | C |\n|---|---|---|\n| 1 | 2 |\n"
    assert "R1" in _rules(d.lint_text(text))
    fixed, _ = d.process_text(text, fix=True)
    # the short row is padded to 3 cells
    assert fixed.splitlines()[2].count("|") == 4


def test_class2_overflow_merged_into_last_cell():
    text = "| A | B |\n|---|---|\n| 1 | 2 | 3 | 4 |\n"
    assert "R1" in _rules(d.lint_text(text))
    fixed, _ = d.process_text(text, fix=True)
    row = fixed.splitlines()[2]
    assert row.count("|") == 3  # merged back to 2 cells (header width)
    assert "2" in row and "3" in row and "4" in row  # overflow merged into last cell


def test_class3_bad_separator_flagged_and_rebuilt():
    text = "| A | B |\n|--|--|\n| 1 | 2 |\n"
    assert "R2" in _rules(d.lint_text(text))
    fixed, _ = d.process_text(text, fix=True)
    assert fixed.splitlines()[1] == "|---|---|"


def test_class4_unfenced_pipe_art_untouched_by_fix_and_flagged():
    art = "|  __  |  __  |\n| (oo) | (oo) |\n|______|______|\n"
    new, problems = d.process_text(art, fix=True)
    assert new == art  # NEVER modified
    assert any("R-art" in msg for _ln, msg in problems)  # flagged as needing a fence


# ---------------------------------------------------------------------------
# fence skip + clean


def test_fenced_content_never_touched():
    text = "```\n| not | a | real table\n| x |\n```\n"
    new, problems = d.process_text(text, fix=True)
    assert new == text and problems == []


def test_clean_table_has_no_problems():
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    assert d.lint_text(text) == []


# ---------------------------------------------------------------------------
# expand_targets


def test_expand_targets_folder_globs_md(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("y", encoding="utf-8")
    (tmp_path / "c.txt").write_text("z", encoding="utf-8")
    files = d.expand_targets([str(tmp_path)])
    names = {f.name for f in files}
    assert names == {"a.md", "b.md"}  # .txt excluded from folder walk


def test_expand_targets_explicit_file_any_ext(tmp_path):
    p = tmp_path / "notes.markdown"
    p.write_text("x", encoding="utf-8")
    assert d.expand_targets([str(p)]) == [p]  # explicit file taken as-is


# ---------------------------------------------------------------------------
# validate docs command (D2 no-op + lint/fix)


def test_validate_docs_no_targets_is_noop(capsys):
    from otaman_cli.commands.misc_readonly import cmd_validate

    rc = cmd_validate(["docs", "--fix"])
    out = capsys.readouterr().out
    assert rc == 0 and "Usage:" in out and "No targets" in out


def test_validate_docs_lint_exit_1_then_fix_clean(tmp_path, capsys):
    from otaman_cli.commands.misc_readonly import cmd_validate

    f = tmp_path / "bad.md"
    f.write_text("| A | B | C |\n|---|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    assert cmd_validate(["docs", str(f)]) == 1  # lint finds R1
    capsys.readouterr()
    assert cmd_validate(["docs", "--fix", str(f)]) == 0  # fix repairs
    capsys.readouterr()
    assert cmd_validate(["docs", str(f)]) == 0  # now clean


def test_validate_docs_fix_leaves_pipe_art(tmp_path):
    from otaman_cli.commands.misc_readonly import cmd_validate

    f = tmp_path / "art.md"
    original = "|  __  |  __  |\n| (oo) | (oo) |\n|______|______|\n"
    f.write_text(original, encoding="utf-8")
    cmd_validate(["docs", "--fix", str(f)])
    assert f.read_text(encoding="utf-8") == original  # untouched


def test_bare_validate_still_validates_platform(tmp_path, monkeypatch, capsys):
    # regression: `validate` without `docs` keeps its platform.yaml behavior
    from otaman_cli.commands import misc_readonly

    called = {}

    def _fake_run(*a, **k):
        called["args"] = a
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(misc_readonly, "run_script", _fake_run)
    monkeypatch.setattr(
        misc_readonly,
        "_normalize_ce_platform_yaml_for_validation",
        lambda p: (p, []),
    )
    misc_readonly.cmd_validate([])
    assert "validate-platform.py" in called["args"]


# ---------------------------------------------------------------------------
# config + doctor row


def test_config_defaults_and_overrides(tmp_path):
    (tmp_path / "platform.yaml").write_text("project: demo\n", encoding="utf-8")
    inc, exc = d.load_docs_format_config(tmp_path)
    assert inc == ["**/*.md"] and exc == []
    (tmp_path / "platform.yaml").write_text(
        "project: demo\ndocs-format:\n  include: ['docs/**/*.md']\n  exclude: ['campaigns/**']\n",
        encoding="utf-8",
    )
    inc, exc = d.load_docs_format_config(tmp_path)
    assert inc == ["docs/**/*.md"] and exc == ["campaigns/**"]


def test_doctor_docs_format_counts_and_excludes(tmp_path):
    from otaman_cli.commands.doctor import _check_docs_format

    (tmp_path / "platform.yaml").write_text(
        "project: demo\ndocs-format:\n  include: ['**/*.md']\n  exclude: ['campaigns/**']\n",
        encoding="utf-8",
    )
    (tmp_path / "good.md").write_text("| A |\n|---|\n| 1 |\n", encoding="utf-8")
    (tmp_path / "bad.md").write_text("| A | B |\n|---|---|\n| 1 |\n", encoding="utf-8")
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "campaigns" / "log.md").write_text("| A | B |\n|--|\n| oops |\n", encoding="utf-8")
    result = _check_docs_format(tmp_path)
    assert result["files_with_issues"] == 1  # only bad.md; campaigns/ excluded
    assert result["total_issues"] >= 1


def test_doctor_docs_format_report_quiet_when_clean(tmp_path, capsys):
    from otaman_cli.commands.doctor import _check_docs_format, _print_docs_format_report

    (tmp_path / "platform.yaml").write_text("project: demo\n", encoding="utf-8")
    (tmp_path / "ok.md").write_text("| A |\n|---|\n| 1 |\n", encoding="utf-8")
    _print_docs_format_report(_check_docs_format(tmp_path))
    assert capsys.readouterr().out == ""
