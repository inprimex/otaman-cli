"""agent-credential-access 3.1 — `otaman connection` CRUD + values-free inventory.

Covers the per-scope connections.yaml writer (create/update/delete, values-free
by construction), the resolve-backed list/show inventory (with the "no backing
key" badge from list_keys + missing_secret_refs), the propose-and-confirm gate
(metadata never auto-guessed), and the check wiring (reachability+auth report,
persisted last-check, --fix path).

Isolation: the autouse conftest fixture redirects the tenant connections.yaml
and the CheckReport store to tmp; program-scope files live under a tmp root.
"""

from __future__ import annotations

from unittest import mock

import pytest

from otaman_cli.commands import connection as conn_cmd
from otaman_cli.connections import store


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A tmp program root with a platform.yaml; make it the resolved project."""
    r = tmp_path / "prog"
    r.mkdir()
    (r / "platform.yaml").write_text("project: demo\nversion: '1.0'\n", encoding="utf-8")
    monkeypatch.setattr(conn_cmd, "find_project_root", lambda: r)
    return r


def _run(args):
    return conn_cmd.cmd_connection(args)


# ---------------------------------------------------------------------------
# store.py — the connections.yaml writer


def test_scope_write_path_program_and_tenant(tmp_path):
    assert store.scope_write_path("program", tmp_path) == tmp_path / "connections.yaml"
    # tenant path goes through the (conftest-isolated) resolver
    assert store.scope_write_path("tenant", tmp_path).name == "connections.yaml"


def test_scope_write_path_rejects_org(tmp_path):
    with pytest.raises(ValueError, match="scope must be one of"):
        store.scope_write_path("org", tmp_path)


def test_upsert_insert_then_replace_preserves_others(tmp_path):
    p = tmp_path / "connections.yaml"
    assert store.upsert_connection(p, {"name": "a", "type": "git-https", "endpoint": "x"}) is False
    assert store.upsert_connection(p, {"name": "b", "type": "ssh", "endpoint": "y"}) is False
    # replace 'a'
    assert store.upsert_connection(p, {"name": "a", "type": "api", "endpoint": "z"}) is True
    conns = {c["name"]: c for c in store.load_connections(p)}
    assert conns["a"]["type"] == "api"
    assert conns["b"]["type"] == "ssh"  # untouched


def test_upsert_writes_only_known_fields(tmp_path):
    p = tmp_path / "connections.yaml"
    store.upsert_connection(
        p, {"name": "a", "type": "pat", "endpoint": "github.com", "token": "SHOULD-NOT-PERSIST"}
    )
    text = p.read_text(encoding="utf-8")
    assert "SHOULD-NOT-PERSIST" not in text
    assert "token" not in text


def test_delete_removes_and_reports(tmp_path):
    p = tmp_path / "connections.yaml"
    store.upsert_connection(p, {"name": "a", "type": "git-https", "endpoint": "x"})
    assert store.delete_connection(p, "a") is True
    assert store.delete_connection(p, "a") is False
    assert store.load_connections(p) == []


def test_load_absent_is_empty(tmp_path):
    assert store.load_connections(tmp_path / "nope.yaml") == []


# ---------------------------------------------------------------------------
# create — propose-and-confirm (metadata never auto-guessed)


def test_create_requires_type_and_endpoint(root, capsys):
    assert _run(["create", "gh"]) == 1
    assert "Missing required metadata" in capsys.readouterr().out


def test_create_persists_with_yes(root):
    rc = _run(
        [
            "create",
            "gh",
            "--type",
            "pat",
            "--endpoint",
            "github.com",
            "--secret-ref",
            "GH_PAT",
            "--yes",
        ]
    )
    assert rc == 0
    conns = store.load_connections(root / "connections.yaml")
    assert conns == [
        {
            "name": "gh",
            "type": "pat",
            "endpoint": "github.com",
            "secret_ref": "GH_PAT",
            "scope": "program",
        }
    ]


def test_create_refuses_without_confirmation_noninteractive(root, capsys):
    # No TTY and no --yes → refuse; nothing persisted (never auto-guess/commit).
    with mock.patch("sys.stdin.isatty", return_value=False):
        rc = _run(["create", "gh", "--type", "pat", "--endpoint", "github.com"])
    assert rc == 1
    assert not (root / "connections.yaml").exists()
    assert "Refusing to persist without confirmation" in capsys.readouterr().err


def test_create_interactive_confirm_persists(root):
    with (
        mock.patch("sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="y"),
    ):
        rc = _run(["create", "gh", "--type", "pat", "--endpoint", "github.com"])
    assert rc == 0
    assert store.find_connection(root / "connections.yaml", "gh") is not None


def test_create_rejects_duplicate(root):
    _run(["create", "gh", "--type", "pat", "--endpoint", "github.com", "--yes"])
    rc = _run(["create", "gh", "--type", "api", "--endpoint", "other", "--yes"])
    assert rc == 1


def test_create_tenant_scope(root, monkeypatch):
    rc = _run(["create", "hub", "--type", "pat", "--endpoint", "e", "--scope", "tenant", "--yes"])
    assert rc == 0
    tenant_path = store.scope_write_path("tenant", root)
    assert store.find_connection(tenant_path, "hub") is not None
    assert not (root / "connections.yaml").exists()  # not in program scope


# ---------------------------------------------------------------------------
# list / show — values-free inventory


def test_list_empty(root, capsys):
    assert _run(["list"]) == 0
    assert "No connections configured" in capsys.readouterr().out


def test_list_renders_and_badges_unbacked(root, capsys):
    _run(
        [
            "create",
            "gh",
            "--type",
            "pat",
            "--endpoint",
            "github.com",
            "--secret-ref",
            "NO_KEY",
            "--yes",
        ]
    )
    assert _run(["list"]) == 0
    out = capsys.readouterr().out
    assert "gh" in out and "github.com" in out and "NO_KEY" in out
    assert "no backing key" in out  # secret_ref has no matching backend key


def test_show_found_and_not_found(root, capsys):
    _run(
        ["create", "gh", "--type", "pat", "--endpoint", "github.com", "--secret-ref", "GH", "--yes"]
    )
    assert _run(["show", "gh"]) == 0
    out = capsys.readouterr().out
    assert "github.com" in out and "GH" in out
    assert _run(["show", "missing"]) == 1


# ---------------------------------------------------------------------------
# update / delete


def test_update_applies_changes(root):
    _run(["create", "gh", "--type", "pat", "--endpoint", "github.com", "--yes"])
    assert _run(["update", "gh", "--endpoint", "ghe.corp", "--secret-ref", "GH2", "--yes"]) == 0
    c = store.find_connection(root / "connections.yaml", "gh")
    assert c["endpoint"] == "ghe.corp"
    assert c["secret_ref"] == "GH2"
    assert c["type"] == "pat"  # unchanged


def test_update_not_found(root):
    assert _run(["update", "ghost", "--endpoint", "x", "--yes"]) == 1


def test_delete_removes_with_yes(root):
    _run(["create", "gh", "--type", "pat", "--endpoint", "github.com", "--yes"])
    assert _run(["delete", "gh", "--yes"]) == 0
    assert store.load_connections(root / "connections.yaml") == []


def test_delete_not_found(root):
    assert _run(["delete", "ghost", "--yes"]) == 1


# ---------------------------------------------------------------------------
# check — reachability + auth, persisted last-check


def test_check_reports_ok_for_reachable_no_secret(root, monkeypatch, capsys):
    # A connection with no secret_ref is auth-not-required; reachable http → ok.
    _run(["create", "site", "--type", "git-https", "--endpoint", "example.com", "--yes"])
    monkeypatch.setattr(conn_cmd, "_http_probe", lambda endpoint: True)
    assert _run(["check", "site"]) == 0
    out = capsys.readouterr().out
    assert "site: ok" in out


def test_check_reports_failure_without_mutating(root, monkeypatch, capsys):
    _run(["create", "site", "--type", "git-https", "--endpoint", "example.com", "--yes"])
    monkeypatch.setattr(conn_cmd, "_http_probe", lambda endpoint: False)
    assert _run(["check", "site"]) == 1
    assert "unreachable" in capsys.readouterr().out.lower()


def test_check_persists_last_check_visible_in_list(root, monkeypatch, capsys):
    _run(["create", "site", "--type", "git-https", "--endpoint", "example.com", "--yes"])
    monkeypatch.setattr(conn_cmd, "_http_probe", lambda endpoint: True)
    _run(["check", "site"])
    capsys.readouterr()
    _run(["list"])
    out = capsys.readouterr().out
    # last-check now shows a status/timestamp, not the "—" placeholder
    assert "ok ·" in out


def test_check_all(root, monkeypatch, capsys):
    _run(["create", "a", "--type", "git-https", "--endpoint", "a.com", "--yes"])
    _run(["create", "b", "--type", "git-https", "--endpoint", "b.com", "--yes"])
    monkeypatch.setattr(conn_cmd, "_http_probe", lambda endpoint: True)
    assert _run(["check", "--all"]) == 0
    out = capsys.readouterr().out
    assert "a: ok" in out and "b: ok" in out


def test_check_unknown_name(root):
    assert _run(["check", "ghost"]) == 1


# ---------------------------------------------------------------------------
# dispatch + no-value invariant


def test_unknown_action(root, capsys):
    assert _run(["frobnicate"]) == 2


def test_not_in_project(monkeypatch, capsys):
    monkeypatch.setattr(conn_cmd, "find_project_root", lambda: None)
    assert conn_cmd.cmd_connection(["list"]) == 1


def test_http_probe_treats_4xx_as_reachable():
    import urllib.error

    def fake_urlopen(req, timeout=5):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert conn_cmd._http_probe("example.com") is True


def test_http_probe_transport_error_is_unreachable():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("no route")):
        assert conn_cmd._http_probe("example.com") is False
