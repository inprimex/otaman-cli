"""Tests for otaman_cli.onboard.state — users.yaml schema + persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.onboard.state import (
    KNOWN_ROLES,
    StateError,
    User,
    find_user,
    load_users,
    save_users,
    upsert_user,
    validate_email,
    validate_roles,
)


class TestValidateEmail:
    @pytest.mark.parametrize("email", [
        "alice@example.com",
        "alice.bob@example.co.uk",
        "alice+otaman@example.com",
    ])
    def test_valid(self, email):
        validate_email(email)  # no raise

    @pytest.mark.parametrize("email", [
        "",
        "alice",
        "alice@",
        "@example.com",
        "alice example.com",
    ])
    def test_invalid(self, email):
        with pytest.raises(StateError, match="not a valid email"):
            validate_email(email)


class TestValidateRoles:
    def test_valid_single(self):
        validate_roles(["otaman:developer"])

    def test_valid_multiple(self):
        validate_roles(["otaman:developer", "otaman:approver"])

    def test_empty_rejected(self):
        with pytest.raises(StateError, match="must not be empty"):
            validate_roles([])

    def test_unknown_role_rejected(self):
        with pytest.raises(StateError, match="unknown role"):
            validate_roles(["otaman:wizard"])

    def test_bare_name_rejected(self):
        """Bare names without `otaman:` prefix don't satisfy KNOWN_ROLES.

        The CLI's _parse_roles() adds the prefix before calling this.
        """
        with pytest.raises(StateError, match="unknown role"):
            validate_roles(["developer"])


class TestUserDataclass:
    def test_round_trip_via_dict(self):
        u = User(
            email="alice@example.com",
            display_name="Alice",
            roles=["otaman:developer"],
            unix_user="alice",
            unix_groups=["otaman-greenbin"],
            telegram_id=12345,
            user_id="zitadel-uuid-1",
            created_at="2026-05-14T10:00:00+00:00",
            last_seen=None,
            enabled=True,
        )
        d = u.to_dict()
        u2 = User.from_dict(d)
        assert u == u2

    def test_missing_email_raises(self):
        with pytest.raises(StateError, match="missing required field: email"):
            User.from_dict({"roles": ["otaman:developer"]})

    def test_missing_roles_raises(self):
        with pytest.raises(StateError, match="missing required field: roles"):
            User.from_dict({"email": "alice@example.com"})

    def test_defaults_filled_when_optional_missing(self):
        u = User.from_dict({
            "email": "alice@example.com",
            "roles": ["otaman:developer"],
        })
        assert u.display_name == "alice"  # email-local-part default
        assert u.unix_user is None
        assert u.unix_groups == []
        assert u.enabled is True


class TestPersistence:
    def test_save_then_load_round_trip(self, tmp_path):
        users = [
            User(email="alice@example.com", display_name="Alice", roles=["otaman:developer"]),
            User(email="bob@example.com", display_name="Bob", roles=["otaman:viewer"]),
        ]
        save_users(users, tmp_path)
        loaded = load_users(tmp_path)
        assert [u.email for u in loaded] == ["alice@example.com", "bob@example.com"]

    def test_load_missing_file_returns_empty_list(self, tmp_path):
        assert load_users(tmp_path) == []

    def test_load_malformed_yaml_raises(self, tmp_path):
        (tmp_path / "users.yaml").write_text("not: valid: yaml: ::: [")
        with pytest.raises(StateError, match="parse error"):
            load_users(tmp_path)

    def test_load_users_not_a_list_raises(self, tmp_path):
        (tmp_path / "users.yaml").write_text("users: this-is-a-string\n")
        with pytest.raises(StateError, match="must be a list"):
            load_users(tmp_path)


class TestFindUser:
    def test_finds_existing(self):
        users = [
            User(email="a@x.com", display_name="A", roles=["otaman:viewer"]),
            User(email="b@x.com", display_name="B", roles=["otaman:viewer"]),
        ]
        assert find_user(users, "b@x.com").email == "b@x.com"

    def test_returns_none_when_missing(self):
        assert find_user([], "ghost@x.com") is None


class TestUpsert:
    def _user(self, email="alice@example.com", **kw):
        defaults = dict(
            email=email,
            display_name="Alice",
            roles=["otaman:developer"],
        )
        defaults.update(kw)
        return User(**defaults)

    def test_first_add_appends(self, tmp_path):
        u = self._user()
        result, added = upsert_user(tmp_path, u)
        assert added is True
        assert result.email == u.email
        assert result.created_at  # filled by upsert
        # Persisted
        loaded = load_users(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].email == u.email

    def test_idempotent_re_add_is_no_op(self, tmp_path):
        u = self._user()
        upsert_user(tmp_path, u)
        # Re-add identical fields
        _, added = upsert_user(tmp_path, self._user())
        assert added is False
        loaded = load_users(tmp_path)
        assert len(loaded) == 1

    def test_conflicting_re_add_raises(self, tmp_path):
        u = self._user()
        upsert_user(tmp_path, u)
        # Re-add with different role
        conflicting = self._user(roles=["otaman:admin"])
        with pytest.raises(StateError, match="already exists with different fields"):
            upsert_user(tmp_path, conflicting)

    def test_invalid_email_rejected(self, tmp_path):
        with pytest.raises(StateError, match="not a valid email"):
            upsert_user(tmp_path, self._user(email="not-an-email"))

    def test_invalid_role_rejected(self, tmp_path):
        u = self._user()
        u.roles = ["otaman:wizard"]
        with pytest.raises(StateError, match="unknown role"):
            upsert_user(tmp_path, u)


class TestKnownRoles:
    def test_admin_present(self):
        assert "otaman:admin" in KNOWN_ROLES

    def test_all_four_roles(self):
        assert KNOWN_ROLES == frozenset({
            "otaman:admin",
            "otaman:approver",
            "otaman:developer",
            "otaman:viewer",
        })
