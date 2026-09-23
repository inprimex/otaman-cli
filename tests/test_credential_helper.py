"""shared-agent-memory 1.3 — the git credential helper (D4).

The connections contract was always "secret_ref is a key name; resolution
happens at the call site". The call site was missing, so tenants hand-rolled
one — a script, an askpass shim, a token in a URL. The mildef incident is what
that costs, and it is the scenario D4 names as the test: the same task with the
helper must need zero bespoke files.

These tests are mostly about what the helper must NOT do. For a credential
helper the happy path is the easy half; the dangerous half is persisting a
value, answering for a host nobody registered, or leaking a secret onto a
channel someone reads.
"""

from __future__ import annotations

import io

import pytest

from otaman_cli.commands.credential_helper import cmd_credential_helper

pytest.importorskip("otaman_core.connections")

SECRET = "ghp_do_not_persist_me"


@pytest.fixture
def program(isolate_bus, monkeypatch):
    """A program with one registered connection and its secret in the cascade."""
    root = isolate_bus
    (root / "connections.yaml").write_text(
        "connections:\n"
        "  - name: origin\n"
        "    type: git\n"
        "    endpoint: https://github.com/inprimex/otaman-cli.git\n"
        "    secret_ref: GITHUB_TOKEN\n",
        encoding="utf-8",
    )
    # The PROGRAM credential layer is `<root>/.otaman/secrets.env`, not
    # `<root>/secrets.env` — the cascade's own layout (credential_layer_paths).
    (root / ".otaman").mkdir(exist_ok=True)
    (root / ".otaman" / "secrets.env").write_text(f"GITHUB_TOKEN={SECRET}\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _set_secret(root, text: str) -> None:
    (root / ".otaman" / "secrets.env").write_text(text, encoding="utf-8")


def _get(monkeypatch, capsys, request_text: str, op: str = "get"):
    monkeypatch.setattr("sys.stdin", io.StringIO(request_text))
    rc = cmd_credential_helper([op])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _files_under(root):
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# what it must never do


def test_store_persists_nothing(program, monkeypatch, capsys):
    """The operation git calls to SAVE a credential. Honouring it would write
    the value to disk on the first successful push and quietly turn this helper
    into the thing it replaces."""
    before = _files_under(program)
    rc, out, _err = _get(
        monkeypatch, capsys, f"protocol=https\nhost=github.com\npassword={SECRET}\n\n", op="store"
    )
    assert rc == 0
    assert out == "", "store must be silent"
    assert _files_under(program) == before, "store wrote something to disk"


def test_erase_succeeds_without_touching_disk(program, monkeypatch, capsys):
    before = _files_under(program)
    rc, out, _err = _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n", op="erase")
    assert rc == 0 and out == ""
    assert _files_under(program) == before


def test_get_writes_no_file_either(program, monkeypatch, capsys):
    before = _files_under(program)
    _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    assert _files_under(program) == before, "resolving a credential left a file behind"


def test_an_unregistered_host_gets_nothing(program, monkeypatch, capsys):
    rc, out, err = _get(monkeypatch, capsys, "protocol=https\nhost=evil.example\n\n")
    assert rc == 0, "git must be able to fall through to the next helper"
    assert out == ""
    assert SECRET not in out and SECRET not in err


def test_a_suffix_lookalike_host_is_not_served(program, monkeypatch, capsys):
    """`evil-github.com` must not be matched by `github.com`. A suffix match
    would hand the credential to a host the operator never registered."""
    rc, out, err = _get(monkeypatch, capsys, "protocol=https\nhost=evil-github.com\n\n")
    assert out == ""
    assert SECRET not in out and SECRET not in err


def test_the_secret_never_reaches_stderr(program, monkeypatch, capsys):
    """Diagnostics go to stderr, which ends up in CI logs and scrollback."""
    _rc, _out, err = _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    assert SECRET not in err


def test_a_missing_secret_names_the_ref_not_the_value(program, monkeypatch, capsys):
    _set_secret(program, "SOMETHING_ELSE=x\n")
    rc, out, err = _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    assert rc == 0 and out == ""
    assert "GITHUB_TOKEN" in err, "the operator needs to know WHICH ref is unset"
    assert "SOMETHING_ELSE" not in err, "must not disclose what else the layer holds"


# ---------------------------------------------------------------------------
# what it must do


def test_get_emits_the_resolved_credential_in_gits_protocol(program, monkeypatch, capsys):
    rc, out, _err = _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    assert rc == 0
    lines = out.splitlines()
    assert f"password={SECRET}" in lines
    assert any(line.startswith("username=") for line in lines)
    assert out.endswith("\n\n"), "git's block terminator is required"


def test_the_value_is_read_at_invocation_not_cached(program, monkeypatch, capsys):
    """D4: resolved at invocation. A rotated secret must take effect next call."""
    _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    _set_secret(program, "GITHUB_TOKEN=rotated_value\n")
    _rc, out, _err = _get(monkeypatch, capsys, "protocol=https\nhost=github.com\n\n")
    assert "password=rotated_value" in out
    assert SECRET not in out, "served a stale cached value after rotation"


def test_an_unknown_operation_is_a_protocol_error(program, monkeypatch, capsys):
    """Not a no-op: silently succeeding would hide a git/helper mismatch."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cmd_credential_helper(["frobnicate"]) == 2


def test_help_names_the_wiring(capsys):
    assert cmd_credential_helper([]) == 1
    out = capsys.readouterr().out
    assert "credential.helper" in out, "the operator has to be told how to wire it"


def test_a_request_with_no_host_is_answered_with_nothing(program, monkeypatch, capsys):
    rc, out, _err = _get(monkeypatch, capsys, "protocol=https\n\n")
    assert rc == 0 and out == ""


def test_unknown_request_keys_are_tolerated(program, monkeypatch, capsys):
    """git adds request fields over time; a helper that chokes on one breaks."""
    rc, out, _err = _get(
        monkeypatch, capsys, "protocol=https\nhost=github.com\nwwwauth[]=Basic\n\n"
    )
    assert rc == 0
    assert f"password={SECRET}" in out
