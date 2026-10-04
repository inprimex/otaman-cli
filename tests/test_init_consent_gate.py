"""`otaman init` requires explicit consent before writing — init-consent-gate step 2.

`destructive-command-safety` (accumulated spec, archived 2026-07-04) requires of a
DESTRUCTIVE-CROSS-DIRECTORY command:

    "In an interactive TTY, the command SHALL prompt for `y/N` confirmation after
     echoing the resolved target and before mutating. In a non-interactive context
     (no TTY, or scripted invocation), the command SHALL require an explicit `--yes`
     flag and SHALL refuse to proceed (exit non-zero, no mutation) if `--yes` is
     absent — it SHALL NOT silently assume consent."

`otaman init` has met the ECHO half since the 2026-10-02 incidents and never the
CONSENT half. That is how `otaman init --help` executed a full init from a pipe and
could repoint a `.otaman` marker — a fleet-wide advisory went out for it.

Sequenced deliberately (I sent the assignment, 20261002T171033): deploy's step 1 put
`--yes` into `ce-bootstrap.sh`'s non-TTY `otaman init` call FIRST, so that enforcing
consent here could not break the CE bootstrap the moment it landed. Step 1 is in
deploy's committed main (911fe8c, PR #126) — verified before building this.

The gate consumes `safety.confirm_destructive_operation` rather than re-deriving the
TTY/`--yes` logic, because that helper is the single home for this rule and
`migrate`/`upgrade` already gate through it.
"""

from __future__ import annotations

import pytest

import otaman_cli.commands.init as INIT


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A valid platform.yaml, with every heavy step of init stubbed out."""
    root = tmp_path / "prog"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos:\n  - name: a\n    path: ../a\n    owner: a-agent\n",
        encoding="utf-8",
    )
    (tmp_path / "a").mkdir()

    # chdir OUT of the cli repo. core's resolution chain is marker-file FIRST, then
    # OTAMAN_ROOT, and every repo carries a `.otaman` marker — so a test running from
    # the repo resolves to the REAL otaman-meta however the env is set, which is what
    # core's test-mode guard caught here. `tmp_path` has no marker, so the autouse
    # `isolate_bus` sandbox wins.
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(INIT, "_init_preflight", lambda a: None)
    monkeypatch.setattr(INIT, "_refuse_inside_a_repo_of_a_program", lambda a: None)
    monkeypatch.setattr(INIT, "_scaffold_launcher_after_init", lambda *a, **k: None)
    monkeypatch.setattr(INIT, "_ensure_openspec_cli", lambda *a, **k: None)
    return root / "platform.yaml"


@pytest.fixture
def scripts(monkeypatch):
    """Record every script invocation — the mutation is `generate-agent-config.py`."""
    calls: list[str] = []

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake(name, *args, **kwargs):
        calls.append(name)
        return _Ok()

    monkeypatch.setattr(INIT, "run_script", fake)
    return calls


def _mutated(calls: list[str]) -> bool:
    return any(c.startswith("generate-agent-config") for c in calls)


# ---------------------------------------------------------------------------
# non-interactive: --yes required, refusal is total


def test_no_tty_and_no_yes_REFUSES_and_writes_nothing(program, scripts, monkeypatch):
    """The clause that was missing, and the one the 10-02 incident needed."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = INIT.cmd_init([str(program), "--skip-doctor"])

    assert rc != 0, "a refusal must exit non-zero"
    assert not _mutated(scripts), f"it mutated anyway: {scripts}"


def test_no_tty_WITH_yes_proceeds(program, scripts, monkeypatch):
    """deploy's step 1 depends on this: ce-bootstrap calls init with no TTY and
    `--yes`, and it must still complete."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = INIT.cmd_init([str(program), "--skip-doctor", "--yes"])

    assert rc == 0
    assert _mutated(scripts), "the CE bootstrap's call would break"


def test_the_short_flag_also_consents(program, scripts, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    INIT.cmd_init([str(program), "--skip-doctor", "-y"])

    assert _mutated(scripts)


# ---------------------------------------------------------------------------
# interactive: y/N, defaulting to refuse


@pytest.mark.parametrize("answer", ["y", "yes", "Y", "YES"])
def test_a_tty_yes_proceeds(program, scripts, monkeypatch, answer):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: answer)

    assert INIT.cmd_init([str(program), "--skip-doctor"]) == 0
    assert _mutated(scripts)


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe", "Yeah"])
def test_anything_but_yes_REFUSES(program, scripts, monkeypatch, answer):
    """Defaults to refusing: bare Enter, a typo, and 'Yeah' all decline. A gate that
    accepts near-misses is a gate that trains people to hit Enter."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: answer)

    rc = INIT.cmd_init([str(program), "--skip-doctor"])

    assert rc != 0
    assert not _mutated(scripts), f"{answer!r} was taken as consent"


def test_an_interrupted_prompt_refuses(program, scripts, monkeypatch):
    """Ctrl-C / EOF at the prompt is not consent."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def boom(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", boom)

    assert INIT.cmd_init([str(program), "--skip-doctor"]) != 0
    assert not _mutated(scripts)


# ---------------------------------------------------------------------------
# the gate's placement and wording


def test_dry_run_needs_no_consent(program, scripts, monkeypatch):
    """A dry run mutates nothing, so there is nothing to consent to — and prompting
    there would train operators to answer y by reflex."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = INIT.cmd_init([str(program), "--skip-doctor", "--dry-run"])

    assert rc == 0, "a dry run must not be gated"


def test_the_prompt_RESTATES_which_program_is_being_authorised(
    program, scripts, monkeypatch, capsys
):
    """Between the echo and the prompt sits the validator's output. A prompt that
    makes the operator scroll back to see which program they are authorising defeats
    the echo it depends on."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    INIT.cmd_init([str(program), "--skip-doctor"])

    text = "".join(capsys.readouterr())
    assert "demo" in text, "the project name is not restated at the decision point"
    assert "1 repo" in text, "nor the repo count"


def test_the_refusal_says_nothing_was_written(program, scripts, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    INIT.cmd_init([str(program), "--skip-doctor"])

    assert "nothing was written" in "".join(capsys.readouterr()).lower()


def test_the_gate_consumes_the_shared_helper_not_a_local_copy():
    """One home for the TTY/--yes rule: `migrate` and `upgrade` already gate through
    `safety.confirm_destructive_operation`, and a second implementation here is the
    drift shape that has cost this fleet three incidents in a week."""
    import inspect

    src = inspect.getsource(INIT._confirm_full_init)

    assert "confirm_destructive_operation" in src
    assert "isatty" not in src, "re-derives the TTY check instead of consuming it"
