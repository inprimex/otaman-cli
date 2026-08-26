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


def test_human_usage(capsys):
    assert cmd_human([]) == 1
    assert "Usage" in capsys.readouterr().out


def test_human_registered_in_help():
    from otaman_cli.commands import get

    assert get("human") is not None


# ---------------------------------------------------------------------------
# enroll / remove — shell to deploy's mechanism (mocked; no real sudo)


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_enroll_shells_to_mechanism_and_parses_output(monkeypatch, capsys):
    import otaman_cli.commands.human as h

    calls = {}

    def fake(mech_args):
        calls["args"] = mech_args
        return _Result(0, "enrolled roman\nFINGERPRINT=SHA256:abc123\nROSTER_ID=roman\n")

    monkeypatch.setattr(h, "run_mechanism", fake)
    rc = cmd_human(["enroll", "roman", "--key", "/keys/roman.pub", "--tenant", "otaman-dev"])
    assert rc == 0
    assert calls["args"] == ["roman", "/keys/roman.pub", "--tenant", "otaman-dev"]
    out = capsys.readouterr().out
    assert "Enrolled roman" in out and "SHA256:abc123" in out


def test_enroll_requires_roster_id_and_key(capsys):
    assert cmd_human(["enroll", "roman"]) == 1  # missing --key
    assert cmd_human(["enroll", "--key", "x"]) == 1  # missing roster-id


def test_enroll_surfaces_mechanism_failure(monkeypatch, capsys):
    import otaman_cli.commands.human as h

    monkeypatch.setattr(
        h, "run_mechanism", lambda a: _Result(3, "", "[human-enroll] ERROR: bad key")
    )
    rc = cmd_human(["enroll", "roman", "--key", "bad"])
    assert rc == 3
    assert "ERROR: bad key" in capsys.readouterr().out


def test_remove_shells_with_remove_flag(monkeypatch, capsys):
    import otaman_cli.commands.human as h

    calls = {}

    def fake(mech_args):
        calls["args"] = mech_args
        return _Result(0, "removed roman SHA256:abc on otaman-dev (0 keys)\n")

    monkeypatch.setattr(h, "run_mechanism", fake)
    rc = cmd_human(["remove", "roman", "--fingerprint", "SHA256:abc"])
    assert rc == 0
    assert calls["args"] == ["--remove", "roman", "--fingerprint", "SHA256:abc"]
    assert "removed roman" in capsys.readouterr().out


def test_remove_requires_roster_id(capsys):
    assert cmd_human(["remove"]) == 1


def test_mechanism_path_override(monkeypatch):
    import otaman_cli.commands.human as h

    monkeypatch.setenv("OTAMAN_HUMAN_ENROLL_MECHANISM", "/custom/enroll.sh")
    assert h._mechanism_path() == "/custom/enroll.sh"
    monkeypatch.delenv("OTAMAN_HUMAN_ENROLL_MECHANISM", raising=False)
    assert h._mechanism_path() == h.DEFAULT_MECHANISM
