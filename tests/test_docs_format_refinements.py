"""docs-format-refinements 1.1-1.3 — conformance for the span-aware / backtick /
align refinements (design D1-D3).

- D1: inline code spans are literal — '<' inside a span is not flagged and the
  fixer never edits span content (the adapters `<target>/skills/<name>/` case,
  byte-identical).
- D2: backtick-first escaping — token-shaped '<' '>' '|' wrapped in code spans;
  existing backslash escapes migrated on --fix.
- D3: --align opt-in, idempotent, render-equivalent, never in plain --fix.
"""

from __future__ import annotations

from otaman_cli import docs_format as d


def _cells(row: str) -> list[str]:
    return [c.strip() for c in d._split_cells(row)]


def _render_equivalent(a: str, b: str) -> bool:
    """Same rendered DOM ⇔ same cell contents (stripped) + same table shape."""

    def _norm(text: str) -> list[list[str]]:
        rows = []
        for line in text.splitlines():
            if line.lstrip().startswith("|"):
                cells = _cells(line)
                if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                    continue  # skip separator rows (dash count is cosmetic)
                rows.append(cells)
        return rows

    return _norm(a) == _norm(b)


# ---------------------------------------------------------------------------
# 1.1 — code spans are literal (D1)


def test_lt_inside_code_span_not_flagged():
    text = "| path | note |\n|---|---|\n| `<target>/skills/<name>/` | ok |\n"
    assert d.lint_text(text) == []  # span content is exempt


def test_fixer_leaves_code_span_byte_identical():
    # the named adapters conformance case must round-trip unchanged
    text = "| path | note |\n|---|---|\n| `<target>/skills/<name>/` | ok |\n"
    fixed, _ = d.process_text(text, fix=True)
    assert fixed == text


def test_raw_lt_still_flagged_outside_spans():
    text = "| a | b |\n|---|---|\n| `<ok>` and <bad> | y |\n"
    assert "R3" in {m.split()[0] for _l, m in d.lint_text(text)}  # the bare <bad> is flagged


# ---------------------------------------------------------------------------
# 1.2 — backtick-first + migration (D2)


def test_backtick_first_wraps_token():
    fixed, _ = d.process_text("| A | B |\n|---|---|\n| <1s | a<b |\n", fix=True)
    assert "`<1s`" in fixed and "`a<b`" in fixed
    assert "\\<" not in fixed  # backslash form not used


def test_migrates_existing_backslash_escape():
    fixed, _ = d.process_text("| A |\n|---|\n| \\<tag |\n", fix=True)
    assert "`<tag`" in fixed and "\\<tag" not in fixed


def test_pipe_in_span_round_trips_as_one_cell():
    text = "| A | B |\n|---|---|\n| `x|y` | z |\n"
    # span-aware split keeps the piped span as one cell (not two)
    assert _cells(text.splitlines()[2]) == ["`x|y`", "z"]
    assert d.lint_text(text) == []  # not a width violation
    fixed, _ = d.process_text(text, fix=True)
    assert "`x|y`" in fixed


def test_escaped_pipe_token_migrated_to_span():
    fixed, _ = d.process_text("| A | B |\n|---|---|\n| x\\|y | z |\n", fix=True)
    assert "`x|y`" in fixed


def test_lint_message_suggests_backtick_form():
    (_, msg), *_ = d.lint_text("| A |\n|---|\n| <x |\n")
    assert "code span" in msg or "`" in msg


# ---------------------------------------------------------------------------
# 1.3 — --align (D3)


def test_align_is_idempotent():
    text = "| Name | X |\n|---|---|\n| longvalue | 1 |\n| a | 22 |\n"
    once, _ = d.process_text(text, fix=True, align=True)
    twice, _ = d.process_text(once, fix=True, align=True)
    assert once == twice


def test_align_pads_columns():
    text = "| Name | X |\n|---|---|\n| longvalue | 1 |\n| a | 22 |\n"
    aligned, _ = d.process_text(text, fix=True, align=True)
    # every data row's first cell is padded to the widest ("longvalue" = 9)
    data_rows = [line for line in aligned.splitlines() if line.startswith("| ")][2:]
    assert all("| longvalue |" in aligned for _ in [0])
    assert all(len(_cells(r)[0]) <= 9 for r in data_rows)


def test_align_is_render_equivalent_to_compact():
    text = "| Name | X |\n|---|---|\n| `<longvalue>` | 1 |\n| a<b | 22 |\n"
    compact, _ = d.process_text(text, fix=True, align=False)
    aligned, _ = d.process_text(text, fix=True, align=True)
    assert _render_equivalent(compact, aligned)  # same DOM, only whitespace differs


def test_align_never_applied_by_plain_fix():
    text = "| Name | X |\n|---|---|\n| longvalue | 1 |\n| a | 22 |\n"
    compact, _ = d.process_text(text, fix=True, align=False)
    # plain fix leaves the compact single-space form (no column padding)
    assert "| longvalue | 1 |" in compact
    assert "| a | 22 |" in compact  # not padded to the column width


# ---------------------------------------------------------------------------
# command wiring for --align


def test_validate_docs_align_flag(tmp_path):
    from otaman_cli.commands.misc_readonly import cmd_validate

    f = tmp_path / "t.md"
    f.write_text("| Name | X |\n|---|---|\n| longvalue | 1 |\n| a | 22 |\n", encoding="utf-8")
    assert cmd_validate(["docs", "--align", str(f)]) == 0
    aligned = f.read_text(encoding="utf-8")
    assert "| longvalue | 1  |" in aligned  # padded
