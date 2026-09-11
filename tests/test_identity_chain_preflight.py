"""identity-chain-preflight 1.1/1.3 — proactive human-identity-chain checks.

A tenant where the chain was never wired (zero OTAMAN_HUMAN-annotated
authorized_keys) must WARN, distinctly from the inert-annotation mode
(annotations present but sshd ignores them)."""

from __future__ import annotations

from otaman_cli.identity_preflight import identity_chain_preflight, surface_preflight_warnings

_ANNOTATED = 'environment="OTAMAN_HUMAN=roman" ssh-ed25519 AAAAC3Nz roman@host\n'
_PLAIN = "ssh-ed25519 AAAAC3Nz roman@host\n"


def _ak(tmp_path, body):
    p = tmp_path / "authorized_keys"
    p.write_text(body, encoding="utf-8")
    return p


def _sshd(tmp_path, body):
    p = tmp_path / "sshd_config"
    p.write_text(body, encoding="utf-8")
    return p


def test_never_wired_warns(tmp_path):
    ak = _ak(tmp_path, _PLAIN)  # a key, but no OTAMAN_HUMAN annotation
    sshd = _sshd(tmp_path, "PermitUserEnvironment yes\n")
    warns = identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd)
    assert len(warns) == 1
    assert "unwired" in warns[0] and "OTAMAN_HUMAN=<id>" in warns[0]


def test_missing_authorized_keys_warns(tmp_path):
    warns = identity_chain_preflight(
        authorized_keys_path=tmp_path / "nope", sshd_config_path=tmp_path / "nope2"
    )
    assert len(warns) == 1 and "unwired" in warns[0]


def test_inert_annotation_warns_distinctly(tmp_path):
    ak = _ak(tmp_path, _ANNOTATED)
    sshd = _sshd(tmp_path, "PermitUserEnvironment no\n")  # annotations ignored
    warns = identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd)
    assert len(warns) == 1
    assert "inert" in warns[0] and "PermitUserEnvironment" in warns[0]


def test_default_sshd_permit_off_is_inert(tmp_path):
    ak = _ak(tmp_path, _ANNOTATED)
    sshd = _sshd(tmp_path, "# no PermitUserEnvironment line → sshd default is off\n")
    warns = identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd)
    assert len(warns) == 1 and "inert" in warns[0]


def test_wired_and_permitted_is_clean(tmp_path):
    ak = _ak(tmp_path, _ANNOTATED)
    sshd = _sshd(tmp_path, "PermitUserEnvironment yes\n")
    assert identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd) == []


def test_authorizedkeyscommand_suppresses_inert_warning(tmp_path):
    ak = _ak(tmp_path, _ANNOTATED)
    sshd = _sshd(tmp_path, "PermitUserEnvironment no\nAuthorizedKeysCommand /usr/bin/otaman-akc\n")
    assert identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd) == []


def test_permit_list_naming_otaman_human_is_clean(tmp_path):
    ak = _ak(tmp_path, _ANNOTATED)
    sshd = _sshd(tmp_path, "PermitUserEnvironment OTAMAN_HUMAN,LANG\n")
    assert identity_chain_preflight(authorized_keys_path=ak, sshd_config_path=sshd) == []


def test_surface_helper_prints_each(tmp_path, monkeypatch):
    # never-wired real default paths would vary by host — force one warning
    monkeypatch.setattr(
        "otaman_cli.identity_preflight.identity_chain_preflight",
        lambda **k: ["w1", "w2"],
    )
    printed = []
    n = surface_preflight_warnings(printed.append)
    assert n == 2 and printed == ["w1", "w2"]
