"""hitl-confirmation-adapters 1.2 (part 2) — TOTP enrollment + adapter.

Covers the storage contract (ref-not-value: the base32 seed lands 0600 in
the tenant dotenv, only a `totp_secret_ref` lands in hitl.yaml), the
enroll command (email resolution, method validation, one-time otpauth
display), and the TOTPAdapter's verify path — including the security
property that a non-interactive/agent session cannot satisfy it even when
a human is enrolled.

Isolation: the autouse `_isolated_tenant_home` conftest fixture points both
the tenant hitl.yaml and secrets.env at tmp, so nothing here touches a real
`~/.otaman`.
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from otaman_cli.hitl import adapters
from otaman_cli.hitl import commands as hitl_cmds
from otaman_cli.hitl import config as cfg
from otaman_cli.hitl.adapters import TOTPAdapter, TTYAdapter, select_adapter
from otaman_cli.hitl.totp import totp_now

EMAIL = "roman@inprimex.com"


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot/restore the module-global adapter registry per test (the
    enroll/confirm flow mutates real tenant state, not the registry, but a
    test may register fakes)."""
    saved = list(adapters._REGISTRY)
    try:
        yield
    finally:
        adapters._REGISTRY[:] = saved


# ---------------------------------------------------------------------------
# config.py — slug, key, ref round-trip


def test_email_slug_lowercases_and_replaces_nonalnum():
    assert cfg.email_slug("Roman.Starikov@Inprimex.com") == "roman-starikov-inprimex-com"


def test_totp_key_prefixes_slug():
    assert cfg.totp_key_for("a+b@x.io") == "HITL_TOTP_a-b-x-io"


def test_set_and_read_enrollment_round_trip():
    cfg.set_totp_enrollment(EMAIL, "HITL_TOTP_roman-inprimex-com")
    enrollments = cfg.totp_enrollments(cfg.load_hitl_config())
    assert EMAIL in enrollments
    assert enrollments[EMAIL] == {
        "type": "dotenv",
        "name": "HITL_TOTP_roman-inprimex-com",
        "scope": "tenant",
    }


def test_set_enrollment_preserves_other_keys():
    path = cfg.hitl_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    path.write_text(
        yaml.safe_dump(
            {"allow_insecure_chat_approval": True, "enrollment": {"other@x.io": {"note": "keep"}}}
        ),
        encoding="utf-8",
    )
    cfg.set_totp_enrollment(EMAIL, "HITL_TOTP_roman-inprimex-com")
    data = cfg.load_hitl_config()
    assert data["allow_insecure_chat_approval"] is True
    assert data["enrollment"]["other@x.io"] == {"note": "keep"}
    assert data["enrollment"][EMAIL]["totp_secret_ref"]["name"] == "HITL_TOTP_roman-inprimex-com"


def test_enrollment_without_ref_is_not_listed():
    cfg.set_totp_enrollment(EMAIL, "HITL_TOTP_x")
    # A second human present but TOTP-less must not appear as enrolled.
    path = cfg.hitl_config_path()
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["enrollment"]["ttyonly@x.io"] = {"some": "flag"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    enrollments = cfg.totp_enrollments(cfg.load_hitl_config())
    assert set(enrollments) == {EMAIL}


def test_load_missing_config_is_empty():
    assert cfg.load_hitl_config() == {}
    assert cfg.totp_enrollments(cfg.load_hitl_config()) == {}


# ---------------------------------------------------------------------------
# enroll command — storage contract


def _enroll(argv):
    return hitl_cmds.cmd_enroll({"_argv": argv})


def test_enroll_writes_seed_to_dotenv_and_ref_to_hitl(capsys):
    from otaman_core._secrets import tenant_secrets_path

    rc = _enroll(["totp", "--email", EMAIL])
    assert rc == 0

    # Seed value lives in the tenant dotenv...
    dotenv = tenant_secrets_path()
    assert dotenv.is_file()
    key = cfg.totp_key_for(EMAIL)
    assert key in dotenv.read_text(encoding="utf-8")

    # ...but hitl.yaml holds only a reference — never the value.
    hitl_text = cfg.hitl_config_path().read_text(encoding="utf-8")
    enrollments = cfg.totp_enrollments(cfg.load_hitl_config())
    assert enrollments[EMAIL]["name"] == key
    # The base32 seed must not have leaked into hitl.yaml.
    from otaman_core._secrets import SecretRef, resolve

    seed = resolve(SecretRef([enrollments[EMAIL]]))
    assert seed and seed not in hitl_text


def test_enroll_dotenv_is_0600():
    from otaman_core._secrets import tenant_secrets_path

    _enroll(["totp", "--email", EMAIL])
    mode = tenant_secrets_path().stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_enroll_prints_otpauth_uri_once(capsys):
    _enroll(["totp", "--email", EMAIL])
    out = capsys.readouterr().out
    assert "otpauth://totp/" in out
    assert "issuer=Otaman" in out


def test_enroll_stored_seed_verifies_a_live_code():
    from otaman_core._secrets import SecretRef, resolve

    _enroll(["totp", "--email", EMAIL])
    ref = cfg.totp_enrollments(cfg.load_hitl_config())[EMAIL]
    seed = resolve(SecretRef([ref]))
    now = int(time.time())
    from otaman_cli.hitl.totp import verify_totp

    assert verify_totp(seed, totp_now(seed, timestamp=now), timestamp=now)


def test_enroll_rejects_unknown_method():
    assert _enroll(["sms", "--email", EMAIL]) != 0
    assert _enroll([]) != 0


def test_enroll_requires_resolvable_email(monkeypatch):
    # No project root → cannot resolve from roster → must demand --email.
    monkeypatch.setattr(hitl_cmds, "find_project_root", lambda: None)
    assert _enroll(["totp"]) != 0


def test_enroll_resolves_single_roster_human(monkeypatch, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "human-roster:\n  - name: Roman\n    email: roman@inprimex.com\n    roles: [cofounder]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hitl_cmds, "find_project_root", lambda: root)
    assert _enroll(["totp"]) == 0
    assert EMAIL in cfg.totp_enrollments(cfg.load_hitl_config())


def test_enroll_refuses_ambiguous_roster(monkeypatch, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "human-roster:\n"
        "  - name: A\n    email: a@x.io\n    roles: [cto]\n"
        "  - name: B\n    email: b@x.io\n    roles: [cpo]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hitl_cmds, "find_project_root", lambda: root)
    assert _enroll(["totp"]) != 0
    assert cfg.totp_enrollments(cfg.load_hitl_config()) == {}


# ---------------------------------------------------------------------------
# TOTPAdapter — configuration gate + verify path


def test_adapter_unconfigured_before_enroll():
    assert TOTPAdapter().is_configured() is False


def test_adapter_configured_after_enroll_and_selected():
    _enroll(["totp", "--email", EMAIL])
    assert TOTPAdapter().is_configured() is True
    # Strongest-configured wins: enrolled TOTP is now REQUIRED over TTY.
    assert isinstance(select_adapter(), TOTPAdapter)
    assert not isinstance(select_adapter(), TTYAdapter)


def test_adapter_confirm_approves_valid_code_and_names_human():
    _enroll(["totp", "--email", EMAIL])
    fixed = 1_700_000_000
    from otaman_core._secrets import SecretRef, resolve

    seed = resolve(SecretRef([cfg.totp_enrollments(cfg.load_hitl_config())[EMAIL]]))
    code = totp_now(seed, timestamp=fixed)
    with (
        mock.patch("otaman_cli.hitl.adapters.sys.stdin.isatty", return_value=True),
        mock.patch("time.time", return_value=fixed),
        mock.patch("builtins.input", return_value=code),
    ):
        result = TOTPAdapter().confirm("approve X")
    assert result.approved is True
    assert result.adapter == "totp"
    assert result.human_id == EMAIL


def test_adapter_confirm_rejects_wrong_code():
    _enroll(["totp", "--email", EMAIL])
    with (
        mock.patch("otaman_cli.hitl.adapters.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="000000"),
    ):
        result = TOTPAdapter().confirm("approve X")
    assert result.approved is False
    assert result.human_id is None


def test_adapter_confirm_refuses_without_tty_even_when_enrolled():
    # The core security property: an agent Bash-tool session (no interactive
    # TTY) cannot satisfy TOTP, so it can never approve on the human's behalf.
    _enroll(["totp", "--email", EMAIL])
    with mock.patch("otaman_cli.hitl.adapters.sys.stdin.isatty", return_value=False):
        result = TOTPAdapter().confirm("approve X")
    assert result.approved is False


def test_adapter_confirm_multi_human_matches_the_right_seed():
    _enroll(["totp", "--email", "a@x.io"])
    _enroll(["totp", "--email", "b@x.io"])
    fixed = 1_700_000_000
    from otaman_core._secrets import SecretRef, resolve

    ref_b = cfg.totp_enrollments(cfg.load_hitl_config())["b@x.io"]
    code_b = totp_now(resolve(SecretRef([ref_b])), timestamp=fixed)
    with (
        mock.patch("otaman_cli.hitl.adapters.sys.stdin.isatty", return_value=True),
        mock.patch("time.time", return_value=fixed),
        mock.patch("builtins.input", return_value=code_b),
    ):
        result = TOTPAdapter().confirm("approve X")
    assert result.approved is True
    assert result.human_id == "b@x.io"


def test_enroll_is_idempotent_rotates_seed():
    from otaman_core._secrets import SecretRef, resolve

    _enroll(["totp", "--email", EMAIL])
    seed1 = resolve(SecretRef([cfg.totp_enrollments(cfg.load_hitl_config())[EMAIL]]))
    _enroll(["totp", "--email", EMAIL])
    seed2 = resolve(SecretRef([cfg.totp_enrollments(cfg.load_hitl_config())[EMAIL]]))
    # Re-enrolling rotates the secret (a fresh device provisioning) and keeps
    # exactly one enrollment for the email.
    assert seed1 != seed2
    assert list(cfg.totp_enrollments(cfg.load_hitl_config())).count(EMAIL) == 1
