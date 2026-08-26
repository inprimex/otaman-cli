"""interactive-human-console 2.1 (cli side) — `otaman human` + roster identity.

Aligns console identity resolution with deploy-agent's live provisioning
roster (/etc/otaman/human-roster.yaml, keyed by roster_id — contract
20260826T213316), keeping the platform.yaml name/email fallback for
CE/self-serve. `otaman human list` is the values-free read side.

The autouse conftest fixture isolates tenant_roster_path to tmp.
"""

from __future__ import annotations

from otaman_cli.commands.human import cmd_human
from otaman_cli.console import identity as _identity
from otaman_cli.console.identity import resolve_identity


def _write_tenant_roster(*rows: dict):
    import yaml

    p = _identity.tenant_roster_path()  # module attr → conftest-isolated to tmp
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"humans": list(rows)}), encoding="utf-8")
    return p


def _program_with_platform_roster(tmp_path, *, names):
    root = tmp_path / "prog"
    root.mkdir()
    lines = "".join(f"  - name: {n}\n    email: {n}@x.io\n    roles: [dev]\n" for n in names)
    root.joinpath("platform.yaml").write_text(
        f"project: p\nhuman-roster:\n{lines}", encoding="utf-8"
    )
    return root


# ---------------------------------------------------------------------------
# identity resolution — tenant roster (roster_id) is authoritative


def test_verified_via_tenant_roster_roster_id(tmp_path, monkeypatch):
    _write_tenant_roster({"roster_id": "roman", "fingerprint": "SHA256:abc"})
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ident = resolve_identity(tmp_path / "prog")  # no platform roster needed
    assert ident.verified is True
    assert ident.audit_label == "roman"


def test_verified_via_platform_fallback_when_no_tenant_roster(tmp_path, monkeypatch):
    root = _program_with_platform_roster(tmp_path, names=["roman"])
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")  # matches platform name
    assert resolve_identity(root).verified is True


def test_unverified_when_in_no_roster(tmp_path, monkeypatch):
    _write_tenant_roster({"roster_id": "roman", "fingerprint": "SHA256:abc"})
    monkeypatch.setenv("OTAMAN_HUMAN", "ghost")
    ident = resolve_identity(tmp_path / "prog")
    assert ident.verified is False
    assert "unverified-identity" in ident.audit_label


def test_unverified_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    ident = resolve_identity(tmp_path / "prog")
    assert ident.operator == "unknown-operator" and ident.verified is False


# ---------------------------------------------------------------------------
# `otaman human list` — values-free read side


def test_human_list_empty(capsys):
    assert cmd_human(["list"]) == 0
    assert "No enrolled humans" in capsys.readouterr().out


def test_human_list_renders_fingerprints_not_keys(capsys):
    _write_tenant_roster(
        {
            "roster_id": "roman",
            "fingerprint": "SHA256:abc123",
            "key_type": "ssh-ed25519",
            "comment": "roman@laptop",
        }
    )
    assert cmd_human(["list"]) == 0
    out = capsys.readouterr().out
    assert "roman" in out and "SHA256:abc123" in out and "ssh-ed25519" in out
    # a raw key must never appear — only the fingerprint
    assert "PRIVATE KEY" not in out and "ssh-ed25519 AAAA" not in out


def test_human_usage_and_unwired_verbs(capsys):
    assert cmd_human([]) == 1  # usage
    assert cmd_human(["enroll", "roman", "--key", "x"]) == 2  # honestly not-wired yet
    assert "not wired yet" in capsys.readouterr().out


def test_human_registered_in_help():
    from otaman_cli.commands import get

    assert get("human") is not None
