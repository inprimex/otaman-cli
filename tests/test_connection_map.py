"""agent-credential-access 1.3 — `otaman connection map` (on-demand resource truth).

The values-free map that joins the credential cascade (which layer's file backs a
key, where it lives) with the connection inventory (which credential/Host serves
which external system). Hard invariant: a secret VALUE never appears in any
output, human or --json. The autouse conftest isolates the tenant dotenv; the
program dotenv lives under the tmp root; the ssh_config path is patched to tmp.
"""

from __future__ import annotations

import json

import pytest

from otaman_cli.commands import connection as conn_cmd


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "prog"
    r.mkdir()
    (r / "platform.yaml").write_text("project: demo\nversion: '1.0'\n", encoding="utf-8")
    monkeypatch.setattr(conn_cmd, "find_project_root", lambda: r)
    # ssh_config → tmp, so Host checks never read the real ~/.ssh/config
    ssh_config = tmp_path / "ssh_config"
    monkeypatch.setattr(conn_cmd, "_ssh_config_path", lambda: ssh_config)
    return r


def _write_connections(root, conns):
    lines = ["connections:"]
    for c in conns:
        lines.append(f"  - name: {c['name']}")
        for k, v in c.items():
            if k != "name":
                lines.append(f"    {k}: {v}")
    (root / "connections.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_program_dotenv(root, **kv):
    p = root / ".otaman" / "secrets.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{k}={v}\n" for k, v in kv.items()), encoding="utf-8")


def _write_ssh_host(root_tmp_ssh_config_path, host):
    root_tmp_ssh_config_path.write_text(f"Host {host}\n  IdentityFile ~/.ssh/id_ed25519\n", "utf-8")


def _run(args):
    return conn_cmd.cmd_connection(args)


# ---------------------------------------------------------------------------
# layers section


def test_map_lists_cascade_layers_with_presence(root, capsys):
    _write_program_dotenv(root, TOKEN="secret-value")  # program layer present
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Credential layers" in out
    assert "program" in out and "tenant" in out
    assert str(root / ".otaman" / "secrets.env") in out
    assert "[present]" in out  # program dotenv exists


def test_map_program_layer_absent_when_no_dotenv(root, capsys):
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[absent]" in out  # no program/tenant dotenv written


# ---------------------------------------------------------------------------
# resource map — credential provenance


def test_map_backed_credential_names_winning_layer(root, capsys):
    _write_program_dotenv(root, GH_TOKEN="ghp_xxx")
    _write_connections(
        root,
        [
            {
                "name": "gh",
                "type": "git-https",
                "endpoint": "github.com",
                "secret_ref": "GH_TOKEN",
                "kind": "pat",
            }
        ],
    )
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "github.com" in out and "via gh" in out
    assert "GH_TOKEN → program" in out  # provenance: program layer wins
    assert "ghp_xxx" not in out  # VALUE never printed


def test_map_unbacked_credential_flagged(root, capsys):
    # secret_ref names a key that no layer defines
    _write_connections(
        root,
        [{"name": "api", "type": "api-key", "endpoint": "api.x.io", "secret_ref": "MISSING_KEY"}],
    )
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "MISSING_KEY" in out and "no backing key" in out


# ---------------------------------------------------------------------------
# resource map — ssh Host


def test_map_ssh_host_present(root, capsys, tmp_path):
    _write_ssh_host(tmp_path / "ssh_config", "prod-deploy")
    _write_connections(
        root,
        [
            {
                "name": "deploy",
                "type": "ssh",
                "endpoint": "deploy.x.io",
                "ssh_ref": "prod-deploy",
                "kind": "ssh",
                "ssh_scope": "prod deploy, read-only",
            }
        ],
    )
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "prod-deploy →" in out and "(present)" in out
    assert "prod deploy, read-only" in out  # ssh_scope note surfaced


def test_map_ssh_host_missing_flagged(root, capsys):
    _write_connections(
        root,
        [{"name": "deploy", "type": "ssh", "endpoint": "deploy.x.io", "ssh_ref": "absent-host"}],
    )
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "absent-host →" in out and "(MISSING)" in out


# ---------------------------------------------------------------------------
# empty / filter / json


def test_map_empty_still_shows_layers(root, capsys):
    rc = _run(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Credential layers" in out
    assert "No connections configured" in out


def test_map_scope_filter(root, capsys):
    _write_connections(
        root,
        [
            {"name": "p", "type": "api", "endpoint": "p.io", "scope": "program"},
            {"name": "t", "type": "api", "endpoint": "t.io", "scope": "tenant"},
        ],
    )
    rc = _run(["map", "--scope", "program"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "p.io" in out and "t.io" not in out


def test_map_json_is_values_free_and_structured(root, capsys):
    _write_program_dotenv(root, GH_TOKEN="ghp_secret")
    _write_connections(
        root,
        [
            {
                "name": "gh",
                "type": "git-https",
                "endpoint": "github.com",
                "secret_ref": "GH_TOKEN",
                "kind": "pat",
            }
        ],
    )
    rc = _run(["map", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ghp_secret" not in out  # VALUE never printed
    data = json.loads(out)
    assert {"layers", "resources"} <= data.keys()
    (res,) = data["resources"]
    assert res["system"] == "github.com"
    assert res["credential"]["secret_ref"] == "GH_TOKEN"
    assert res["credential"]["layer"] == "program"
    assert res["credential"]["backed"] is True
    assert any(lyr["scope"] == "program" and lyr["present"] for lyr in data["layers"])


# ---------------------------------------------------------------------------
# org layer (aca 1.5 gate: the cascade's THIRD layer must not be dropped)


def test_infer_org_from_fleet_path(tmp_path):
    r = tmp_path / "orgs" / "acme" / "programs" / "p" / "meta"
    r.mkdir(parents=True)
    assert conn_cmd._infer_org_from_path(r) == "acme"


def test_infer_org_none_off_fleet_layout(tmp_path):
    assert conn_cmd._infer_org_from_path(tmp_path / "random" / "prog") is None


def _fleet_root(tmp_path, monkeypatch):
    """A program root on the fleet ``orgs/<org>/programs/...`` layout so the org
    layer is inferred; ssh_config patched to tmp."""
    r = tmp_path / "orgs" / "acme" / "programs" / "acme" / "meta"
    r.mkdir(parents=True)
    (r / "platform.yaml").write_text("project: acme\nversion: '1.0'\n", encoding="utf-8")
    monkeypatch.setattr(conn_cmd, "find_project_root", lambda: r)
    monkeypatch.setattr(conn_cmd, "_ssh_config_path", lambda: tmp_path / "ssh_config")
    return r


def test_map_renders_all_three_layers_on_fleet_layout(tmp_path, monkeypatch, capsys):
    # the aca-1.5 repro: org secrets.env EXISTS and is where the live PATs are;
    # the map must show program + org + tenant, each present-or-absent.
    from otaman_core import _secrets

    r = _fleet_root(tmp_path, monkeypatch)
    org_env = tmp_path / "org-config" / "secrets.env"
    org_env.parent.mkdir(parents=True)
    org_env.write_text("ORG_PAT=super-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(_secrets, "org_config_secrets_path", lambda org, home=None: org_env)

    _write_connections(
        r,
        [
            {
                "name": "gh",
                "type": "git-https",
                "endpoint": "github.com",
                "secret_ref": "ORG_PAT",
                "kind": "pat",
            }
        ],
    )
    rc = conn_cmd.cmd_connection(["map"])
    out = capsys.readouterr().out
    assert rc == 0
    # all THREE cascade layers render
    assert "program:" in out and "org:" in out and "tenant:" in out
    assert str(org_env) in out  # org layer's file location named…
    assert "[present]" in out  # …and shown present
    # provenance: the org layer wins the key the connection points at
    assert "ORG_PAT → org" in out
    assert "no backing key" not in out  # org-backed → NOT falsely flagged
    assert "super-secret-value" not in out  # VALUE never printed


def test_map_json_includes_org_layer(tmp_path, monkeypatch, capsys):
    from otaman_core import _secrets

    r = _fleet_root(tmp_path, monkeypatch)
    org_env = tmp_path / "org-config" / "secrets.env"
    org_env.parent.mkdir(parents=True)
    org_env.write_text("ORG_PAT=v\n", encoding="utf-8")
    monkeypatch.setattr(_secrets, "org_config_secrets_path", lambda org, home=None: org_env)
    _write_connections(
        r, [{"name": "gh", "type": "pat", "endpoint": "github.com", "secret_ref": "ORG_PAT"}]
    )
    assert conn_cmd.cmd_connection(["map", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(lyr["scope"] == "org" and lyr["present"] for lyr in data["layers"])
    (res,) = data["resources"]
    assert res["credential"]["layer"] == "org" and res["credential"]["backed"] is True


def test_map_json_unbacked_credential(root, capsys):
    _write_connections(
        root,
        [{"name": "api", "type": "api-key", "endpoint": "api.x.io", "secret_ref": "NOPE"}],
    )
    assert _run(["map", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    (res,) = data["resources"]
    assert res["credential"]["backed"] is False
    assert res["credential"]["layer"] is None
