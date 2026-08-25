"""agent-credential-access 3.2 — doctor no-leak check.

`check_connection_value_leaks` resolves each connection's secret_ref to its
value (call-site) and asserts the value never appears in a rendered
connection surface. Includes the regression gate: an injected leak (a
connection whose endpoint IS the secret value) MUST be caught, and the
report must name the surface/ref without ever printing the value.

Isolation: the autouse conftest fixture redirects the tenant secrets.env +
connections.yaml to tmp; the program root is a tmp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import doctor
from otaman_cli.connections import store

_SECRET = "s3cr3t-tok3n-VALUE-do-not-leak"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "prog"
    r.mkdir()
    (r / "platform.yaml").write_text("project: demo\nversion: '1.0'\n", encoding="utf-8")
    return r


def _write_secret(name: str, value: str) -> None:
    from otaman_core._secrets import tenant_secrets_path, upsert_dotenv_secret

    upsert_dotenv_secret(tenant_secrets_path(), name, value)


def _add_connection(root: Path, **fields) -> None:
    store.upsert_connection(root / "connections.yaml", fields)


def test_ok_when_no_connections(root):
    r = doctor.check_connection_value_leaks(root)
    assert r["status"] == "ok"
    assert r["details"]["connections"] == 0


def test_ok_when_value_free_surface(root):
    # Normal 3.1 rendering: secret_ref (name) is shown, the value never is.
    _write_secret("GH_PAT", _SECRET)
    _add_connection(root, name="gh", type="pat", endpoint="github.com", secret_ref="GH_PAT")
    r = doctor.check_connection_value_leaks(root)
    assert r["status"] == "ok"
    assert r["details"]["resolvable_secrets"] == 1


def test_ok_when_secret_ref_unbacked(root):
    # secret_ref present but no backing key → nothing resolvable to leak.
    _add_connection(root, name="gh", type="pat", endpoint="github.com", secret_ref="NO_KEY")
    r = doctor.check_connection_value_leaks(root)
    assert r["status"] == "ok"
    assert r["details"]["resolvable_secrets"] == 0


def test_fails_when_value_appears_in_surface(root):
    # Regression gate: inject a leak by making the endpoint equal the value.
    _write_secret("GH_PAT", _SECRET)
    _add_connection(root, name="gh", type="pat", endpoint=_SECRET, secret_ref="GH_PAT")
    r = doctor.check_connection_value_leaks(root)
    assert r["status"] == "fail"
    assert r["issues"]
    assert all(i["severity"] == "critical" for i in r["issues"])


def test_failure_report_never_contains_the_value(root):
    _write_secret("GH_PAT", _SECRET)
    _add_connection(root, name="gh", type="pat", endpoint=_SECRET, secret_ref="GH_PAT")
    r = doctor.check_connection_value_leaks(root)
    # The check itself must not echo the value — only the ref/surface.
    blob = str(r["issues"])
    assert _SECRET not in blob
    assert "GH_PAT" in blob


def test_detects_leak_in_list_surface(root):
    # Two connections; the leak is via one endpoint — list renders both.
    _write_secret("K", _SECRET)
    _add_connection(root, name="clean", type="git-https", endpoint="ok.com", secret_ref="K")
    _add_connection(root, name="leaky", type="pat", endpoint=_SECRET, secret_ref="K")
    r = doctor.check_connection_value_leaks(root)
    assert r["status"] == "fail"
    surfaces = {i["issue"].split("`connection ")[1].split("`")[0] for i in r["issues"]}
    assert "list" in surfaces  # value appears in the shared list surface


def test_registered_in_run_doctor(root):
    report = doctor.run_doctor(root)
    checks = {c["check"] for c in report["checks"]}
    assert "connection_value_leaks" in checks
