"""End-to-end tests for `maestro git-host pr` + `post-review` subcommands.

Exercises the CLI dispatch path, not the adapter internals (those have
their own unit tests). Uses a fake adapter so we don't hit HTTP.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

CLI_PY = None  # cli/cli_main.py replaced by otaman_cli.main module invocation
# git_host now imported from otaman_core; cli dispatcher imported as package module
from otaman_core import git_host as gh  # noqa: E402

from otaman_cli import main as cli_main  # noqa: E402

# ---------------------------------------------------------------------------
# Fake adapter — no HTTP


class FakeAdapter:
    provider = "github"
    host = "github.com"

    def __init__(self, *, prs=None, comments=None):
        self.prs = prs or []
        self.post_calls = []
        self.comments = comments or []

    def list_open_prs(self, slug):
        return [p for p in self.prs if p.state == "open"]

    def get_pr(self, slug, number):
        for p in self.prs:
            if p.number == number:
                return p
        raise gh.GitHostError(f"PR #{number} not found", status=404)

    def get_pr_for_branch(self, slug, branch):
        for p in self.prs:
            if p.head_ref == branch:
                return p
        return None

    def post_comment(self, slug, pr_number, body):
        if not body.strip():
            raise ValueError("body must be non-empty")
        c = gh.Comment(
            id=9999,
            author="maestro-bot",
            body=body,
            created_at="2026-04-25T12:00:00Z",
            url=f"https://github.com/{slug}/pull/{pr_number}#issuecomment-9999",
        )
        self.post_calls.append((slug, pr_number, body))
        return c

    def list_comments(self, slug, pr_number):
        return list(self.comments)


@pytest.fixture
def maestro_workspace(tmp_path, monkeypatch):
    """Maestro root with platform.yaml and a managed repo that has an origin."""
    monkeypatch.setenv("MAESTRO_GH_CLI_TEST", "ghp_fake")
    maestro = tmp_path / "maestro"
    maestro.mkdir()
    (maestro / "platform.yaml").write_text(
        "project: test\n"
        "git_host:\n"
        "  provider: github\n"
        "  token: MAESTRO_GH_CLI_TEST\n"
        "repos:\n"
        "  - name: app\n    path: ../app\n    owner: backend\n",
        encoding="utf-8",
    )
    # Fake repo without a real git clone — we'll stub detect_remote_for_repo.
    repo = tmp_path / "app"
    repo.mkdir()
    # Don't cd; we'll pass --repo explicitly.
    return {"root": tmp_path, "maestro": maestro, "repo": repo}


def _run_cli(args: list[str], cwd: Path):
    """Invoke cli/cli_main.py's main() with the given args, return rc+stdout."""
    # cli_main is imported at top
    buf = io.StringIO()
    orig_argv = sys.argv[:]
    orig_cwd = Path.cwd()
    try:
        sys.argv = ["maestro"] + args
        import os as _os

        _os.chdir(str(cwd))
        with redirect_stdout(buf):
            rc = cli_main.main()
    finally:
        sys.argv = orig_argv
        import os as _os

        _os.chdir(str(orig_cwd))
    return rc, buf.getvalue()


def _fake_remote_info(slug="octo/app"):
    owner, repo = slug.split("/")
    return gh.RemoteInfo(
        provider="github",
        host="github.com",
        owner=owner,
        repo=repo,
        raw_url=f"git@github.com:{slug}.git",
    )


def _make_pr(number=42, head_ref="feature/x", **overrides):
    return gh.PullRequest(
        number=number,
        title=overrides.get("title", "Add widget"),
        state=overrides.get("state", "open"),
        author=overrides.get("author", "octocat"),
        head_ref=head_ref,
        base_ref="main",
        head_sha="abc123",
        url=f"https://github.com/octo/app/pull/{number}",
        body=overrides.get("body", "body"),
        draft=overrides.get("draft", False),
        raw={},
    )


# ---------------------------------------------------------------------------


class TestPrList:
    def test_lists_open_prs(self, maestro_workspace):
        fake = FakeAdapter(
            prs=[
                _make_pr(number=1, title="A"),
                _make_pr(number=2, title="B"),
            ]
        )
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, out = _run_cli(
                ["git-host", "pr", "list", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert "#1" in out and "#2" in out
        assert "A" in out and "B" in out

    def test_empty(self, maestro_workspace):
        fake = FakeAdapter(prs=[])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, out = _run_cli(
                ["git-host", "pr", "list", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert "No open PRs" in out


class TestPrGet:
    def test_get_by_number(self, maestro_workspace):
        fake = FakeAdapter(prs=[_make_pr(number=42, title="Real PR")])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, out = _run_cli(
                ["git-host", "pr", "get", "42", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert "Real PR" in out
        assert "#42" in out

    def test_not_found(self, maestro_workspace):
        fake = FakeAdapter(prs=[])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, _ = _run_cli(
                ["git-host", "pr", "get", "999", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 2  # adapter raised GitHostError

    def test_invalid_number(self, maestro_workspace):
        fake = FakeAdapter()
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, _ = _run_cli(
                ["git-host", "pr", "get", "not-a-number", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 1


class TestPrComment:
    def test_post_with_body_flag(self, maestro_workspace):
        fake = FakeAdapter(prs=[_make_pr(number=42)])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, out = _run_cli(
                ["git-host", "pr", "comment", "42", "--repo", "app", "--body", "LGTM"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert len(fake.post_calls) == 1
        assert fake.post_calls[0] == ("octo/app", 42, "LGTM")
        assert "#9999" in out

    def test_empty_body_fails(self, maestro_workspace):
        fake = FakeAdapter(prs=[_make_pr(number=42)])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, _ = _run_cli(
                ["git-host", "pr", "comment", "42", "--repo", "app", "--body", "   "],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 1
        assert fake.post_calls == []


# ---------------------------------------------------------------------------
# post-review


class TestPostReview:
    def _setup_review(self, maestro_dir: Path, body: str = "### CTO Review\n\nLooks good.") -> Path:
        pending = maestro_dir / ".agents" / "reviews" / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        review = pending / "2026-04-25-cto-review.md"
        review.write_text(body, encoding="utf-8")
        return review

    def test_posts_latest_review_by_default(self, maestro_workspace):
        review = self._setup_review(maestro_workspace["maestro"])
        fake = FakeAdapter(prs=[_make_pr(number=42, head_ref="my-branch")])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
            patch(
                "otaman_cli.commands.git_host._git_host_current_branch", return_value="my-branch"
            ),
        ):
            rc, out = _run_cli(
                ["git-host", "post-review", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert len(fake.post_calls) == 1
        slug, pr_number, body = fake.post_calls[0]
        assert (slug, pr_number) == ("octo/app", 42)
        assert "Looks good" in body
        assert "maestro-plugin" in body  # wrapper header present
        assert review.name in body

    def test_no_review_file_errors(self, maestro_workspace):
        # Don't create any review files.
        (maestro_workspace["maestro"] / ".agents" / "reviews" / "pending").mkdir(
            parents=True,
            exist_ok=True,
        )
        fake = FakeAdapter(prs=[_make_pr(number=42, head_ref="my-branch")])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, _ = _run_cli(
                ["git-host", "post-review", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 1

    def test_explicit_pr_number(self, maestro_workspace):
        self._setup_review(maestro_workspace["maestro"])
        fake = FakeAdapter(prs=[_make_pr(number=7)])
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
        ):
            rc, _ = _run_cli(
                ["git-host", "post-review", "--pr", "7", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 0
        assert fake.post_calls[0][1] == 7

    def test_no_pr_for_branch_errors(self, maestro_workspace):
        self._setup_review(maestro_workspace["maestro"])
        fake = FakeAdapter(prs=[])  # no PR for the branch
        with (
            patch.object(gh, "get_adapter", return_value=fake),
            patch.object(gh, "detect_remote_for_repo", return_value=_fake_remote_info()),
            patch(
                "otaman_cli.commands.git_host._git_host_current_branch",
                return_value="orphan-branch",
            ),
        ):
            rc, _ = _run_cli(
                ["git-host", "post-review", "--repo", "app"],
                cwd=maestro_workspace["maestro"],
            )
        assert rc == 1
