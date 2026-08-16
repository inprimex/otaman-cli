"""Tests for otaman_cli._runner_registry and the `otaman runner` CLI.

Covers `specs/otaman-runner-platforms/spec.md`. Re-authored from closed
PR #82 with its review's test fixes: POSIX mode assertions carry win32
skips (repo convention), and CLI subprocess sandboxing uses the
OTAMAN_PLATFORMS_DIR / OTAMAN_RUNNER_TOKEN_FILE overrides instead of a
HOME redirect (which Windows Python ignores — USERPROFILE wins).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli import _runner_registry as rr

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: chmod/symlink semantics don't apply on Windows",
)


def _make_platform_yaml(tmp_path: Path, name: str, filename: str = "platform.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(f"name: {name}\nrepos: []\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# platforms add|list|remove


class TestPlatformsRegistry:
    def test_add_creates_symlink(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        target = _make_platform_yaml(tmp_path, "acme")
        result = rr.platforms_add(target, dir_override=str(pdir))
        assert result["status"] == "installed"
        link = pdir / "acme.yaml"
        assert link.is_symlink()
        # compare through the module's own normalization (Windows readlink
        # returns \\?\-prefixed extended paths that resolve() keeps)
        assert rr._resolve_link_target(link) == target.resolve()

    @_POSIX_ONLY
    def test_platforms_dir_created_0700(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        rr.platforms_add(_make_platform_yaml(tmp_path, "acme"), dir_override=str(pdir))
        assert stat.S_IMODE(pdir.stat().st_mode) == 0o700

    def test_add_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(rr.PlatformsError, match="Not a readable file"):
            rr.platforms_add(tmp_path / "nope.yaml", dir_override=str(tmp_path / "platforms"))

    def test_add_non_yaml_suffix_raises(self, tmp_path: Path) -> None:
        """Review finding #7: the runner skips resolved non-.yaml targets —
        registering one must be an upfront error, not a silent no-op."""
        p = tmp_path / "platform.yml"
        p.write_text("name: acme\n", encoding="utf-8")
        with pytest.raises(rr.PlatformsError, match="does not end in .yaml"):
            rr.platforms_add(p, dir_override=str(tmp_path / "platforms"))

    def test_add_missing_name_field_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "platform.yaml"
        p.write_text("repos: []\n", encoding="utf-8")
        with pytest.raises(rr.PlatformsError, match="name"):
            rr.platforms_add(p, dir_override=str(tmp_path / "platforms"))

    @pytest.mark.parametrize("bad", ["../rogue", "a/b", ".hidden", "a b", "..", "x" * 65])
    def test_add_pathy_name_raises(self, tmp_path: Path, bad: str) -> None:
        """Review finding #3: unsanitized names became symlink paths
        ('name: ../rogue' escaped the registry dir)."""
        p = tmp_path / "platform.yaml"
        p.write_text(f"name: '{bad}'\nrepos: []\n", encoding="utf-8")
        with pytest.raises(rr.PlatformsError, match="not usable as a registry filename"):
            rr.platforms_add(p, dir_override=str(tmp_path / "platforms"))
        assert not (tmp_path / "rogue.yaml").exists()

    def test_remove_pathy_name_raises(self, tmp_path: Path) -> None:
        (tmp_path / "platforms").mkdir()
        with pytest.raises(rr.PlatformsError, match="not usable as a registry filename"):
            rr.platforms_remove("../escape", dir_override=str(tmp_path / "platforms"))

    def test_add_idempotent_same_target(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        target = _make_platform_yaml(tmp_path, "acme")
        rr.platforms_add(target, dir_override=str(pdir))
        assert rr.platforms_add(target, dir_override=str(pdir))["status"] == "already-installed"

    def test_add_collision_without_force_raises(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        rr.platforms_add(_make_platform_yaml(tmp_path, "acme", "one.yaml"), dir_override=str(pdir))
        with pytest.raises(rr.PlatformsError, match="refusing"):
            rr.platforms_add(
                _make_platform_yaml(tmp_path, "acme", "two.yaml"), dir_override=str(pdir)
            )

    def test_add_collision_with_force_replaces(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        rr.platforms_add(_make_platform_yaml(tmp_path, "acme", "one.yaml"), dir_override=str(pdir))
        t2 = _make_platform_yaml(tmp_path, "acme", "two.yaml")
        result = rr.platforms_add(t2, force=True, dir_override=str(pdir))
        assert result["status"] == "installed"
        assert rr._resolve_link_target(pdir / "acme.yaml") == t2.resolve()

    def test_list_empty_dir(self, tmp_path: Path) -> None:
        assert rr.platforms_list(dir_override=str(tmp_path / "platforms")) == []

    def test_list_states(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        ok_target = _make_platform_yaml(tmp_path, "okprog", "ok.yaml")
        rr.platforms_add(ok_target, dir_override=str(pdir))
        gone = _make_platform_yaml(tmp_path, "goner", "gone.yaml")
        rr.platforms_add(gone, dir_override=str(pdir))
        gone.unlink()
        (pdir / "manual.yaml").write_text("name: manual\n", encoding="utf-8")
        states = {e["name"]: e["state"] for e in rr.platforms_list(dir_override=str(pdir))}
        assert states == {"okprog": "ok", "goner": "dangling", "manual": "unmanaged"}

    def test_remove_deletes_symlink_only(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        target = _make_platform_yaml(tmp_path, "acme")
        rr.platforms_add(target, dir_override=str(pdir))
        rr.platforms_remove("acme", dir_override=str(pdir))
        assert not (pdir / "acme.yaml").exists()
        assert target.is_file()  # original untouched

    def test_remove_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(rr.PlatformsError, match="not registered"):
            rr.platforms_remove("nope", dir_override=str(tmp_path / "platforms"))

    def test_remove_unmanaged_regular_file_raises(self, tmp_path: Path) -> None:
        pdir = tmp_path / "platforms"
        pdir.mkdir(parents=True)
        (pdir / "manual.yaml").write_text("name: manual\n", encoding="utf-8")
        with pytest.raises(rr.PlatformsError, match="not a symlink"):
            rr.platforms_remove("manual", dir_override=str(pdir))


# ---------------------------------------------------------------------------
# token install|rotate|show


class TestTokenFile:
    @_POSIX_ONLY
    def test_install_creates_0600_file(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        result = rr.token_install(file_override=str(token_path))
        assert result["status"] == "installed"
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    @_POSIX_ONLY
    def test_install_parent_dir_is_0700(self, tmp_path: Path) -> None:
        token_path = tmp_path / "cfg" / "runner.token"
        rr.token_install(file_override=str(token_path))
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700

    def test_install_writes_single_line(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        result = rr.token_install(file_override=str(token_path))
        assert token_path.read_text(encoding="utf-8") == result["token"] + "\n"

    def test_atomic_write_leaves_no_tmp(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        rr.token_install(file_override=str(token_path))
        rr.token_rotate(file_override=str(token_path))
        token_files = [p.name for p in tmp_path.iterdir() if "token" in p.name]
        assert token_files == ["runner.token"]  # no .tmp remnants

    def test_install_twice_without_force_does_not_overwrite(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        first = rr.token_install(file_override=str(token_path))
        second = rr.token_install(file_override=str(token_path))
        assert second["status"] == "already-installed"
        assert second["token"] == first["token"]

    def test_install_force_regenerates(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        first = rr.token_install(file_override=str(token_path))
        second = rr.token_install(force=True, file_override=str(token_path))
        assert second["status"] == "reinstalled"
        assert second["token"] != first["token"]

    def test_rotate_without_existing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(rr.TokenError, match="No token installed"):
            rr.token_rotate(file_override=str(tmp_path / "runner.token"))

    @_POSIX_ONLY
    def test_rotate_replaces_value_preserves_mode(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        first = rr.token_install(file_override=str(token_path))
        result = rr.token_rotate(file_override=str(token_path))
        assert result["token"] != first["token"]
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    def test_show_without_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(rr.TokenError, match="No token installed"):
            rr.token_show(file_override=str(tmp_path / "runner.token"))

    def test_show_returns_value(self, tmp_path: Path) -> None:
        token_path = tmp_path / "runner.token"
        installed = rr.token_install(file_override=str(token_path))
        assert rr.token_show(file_override=str(token_path))["token"] == installed["token"]

    def test_mask_token_hides_middle(self) -> None:
        masked = rr.mask_token("mykbSjoJDEGGEDZtLYUtkRSNyjXvYTKHX5gsHFdULJs")
        assert masked.startswith("mykb") and masked.endswith("ULJs")
        assert "Sjo" not in masked


# ---------------------------------------------------------------------------
# CLI subcommands (subprocess, real dispatch path)


class TestRunnerCli:
    @staticmethod
    def _run(args: list[str], sandbox: Path, input_text: str | None = None):
        """Sandbox via the CLI's own env overrides (portable, unlike HOME
        which Windows Python ignores); strip the isolate_bus pin + propagate
        sys.path per repo convention."""
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "NO_COLOR": "1",
            "OTAMAN_PLATFORMS_DIR": str(sandbox / "platforms"),
            "OTAMAN_RUNNER_TOKEN_FILE": str(sandbox / "runner.token"),
        }
        for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
            env.pop(_var, None)
        return subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", "runner", *args],
            capture_output=True,
            text=True,
            input=input_text,
            env=env,
            cwd=str(sandbox),
        )

    def test_bare_runner_shows_usage_and_exits_nonzero(self, tmp_path: Path) -> None:
        r = self._run([], tmp_path)
        assert r.returncode != 0
        assert "platforms" in r.stdout and "token" in r.stdout

    def test_unknown_subcommand(self, tmp_path: Path) -> None:
        r = self._run(["frobnicate"], tmp_path)
        assert r.returncode != 0
        assert "Unknown runner subcommand: frobnicate" in r.stdout

    def test_platforms_add_and_list(self, tmp_path: Path) -> None:
        platform_yaml = _make_platform_yaml(tmp_path, "acme")
        r = self._run(["platforms", "add", str(platform_yaml)], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Registered 'acme'" in r.stdout
        r = self._run(["platforms", "list"], tmp_path)
        assert r.returncode == 0
        assert "acme" in r.stdout

    def test_platforms_remove(self, tmp_path: Path) -> None:
        self._run(["platforms", "add", str(_make_platform_yaml(tmp_path, "acme"))], tmp_path)
        r = self._run(["platforms", "remove", "acme"], tmp_path)
        assert r.returncode == 0
        assert "Removed 'acme'" in r.stdout

    def test_token_install_then_show_masked(self, tmp_path: Path) -> None:
        r = self._run(["token", "install"], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Installed token" in r.stdout
        r = self._run(["token", "show"], tmp_path)
        assert r.returncode == 0
        assert "..." in r.stdout  # masked

    def test_token_show_reveal(self, tmp_path: Path) -> None:
        self._run(["token", "install"], tmp_path)
        raw = (tmp_path / "runner.token").read_text(encoding="utf-8").strip()
        r = self._run(["token", "show", "--reveal"], tmp_path)
        assert r.returncode == 0
        assert raw in r.stdout

    def test_token_rotate_non_tty_without_yes_refuses(self, tmp_path: Path) -> None:
        """Review finding #5: the old bespoke prompt crashed with EOFError
        here; the shared safety gate refuses cleanly instead."""
        self._run(["token", "install"], tmp_path)
        before = (tmp_path / "runner.token").read_text(encoding="utf-8")
        r = self._run(["token", "rotate"], tmp_path, input_text="")
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        assert (tmp_path / "runner.token").read_text(encoding="utf-8") == before

    def test_token_rotate_yes_flag(self, tmp_path: Path) -> None:
        self._run(["token", "install"], tmp_path)
        before = (tmp_path / "runner.token").read_text(encoding="utf-8").strip()
        r = self._run(["token", "rotate", "--yes"], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (tmp_path / "runner.token").read_text(encoding="utf-8").strip() != before
        assert "--token-source" in r.stdout  # truthful adoption advisory

    def test_token_rotate_without_install_errors(self, tmp_path: Path) -> None:
        r = self._run(["token", "rotate", "--yes"], tmp_path)
        assert r.returncode != 0
        assert "No token installed" in r.stdout

    def test_valueless_token_file_flag_is_an_error(self, tmp_path: Path) -> None:
        """Review finding #4: a dropped --token-file value used to silently
        rotate the DEFAULT token. Now a hard usage error, nothing rotated."""
        self._run(["token", "install"], tmp_path)
        before = (tmp_path / "runner.token").read_text(encoding="utf-8")
        r = self._run(["token", "rotate", "--yes", "--token-file"], tmp_path)
        assert r.returncode == 2
        assert "valueless" in r.stdout.lower() or "Unknown or valueless" in r.stdout
        assert (tmp_path / "runner.token").read_text(encoding="utf-8") == before

    def test_unknown_flag_is_an_error(self, tmp_path: Path) -> None:
        self._run(["token", "install"], tmp_path)
        r = self._run(["token", "rotate", "--ys"], tmp_path)
        assert r.returncode == 2
        assert "--ys" in r.stdout
