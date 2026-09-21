"""`otaman release ...` — the owner side of cross-repo release assembly.

Today this is one subcommand: `clear-fragments`, the counterpart to deploy's
assembler. At a cut, deploy reads every bundled checkout's `changelog.d/`, renders
the notes, and records a manifest — per repo, the exact filenames and content
hashes it consumed. Each owning repo then clears its own consumed fragments.

The design constraint that shapes everything below: the manifest, not a glob, is
the authority on what gets deleted (release-notes-sibling-coverage 1.2). Clearing
by pattern is what makes a lagging clear destructive — `rm changelog.d/*.md` in a
repo that added a fragment after the cut deletes copy that was never released,
and nothing downstream can tell it ever existed. So this deletes by exact
filename, refuses anything glob-shaped, and verifies the content hash before
unlinking. A fragment that changed since the cut is SKIPPED, not deleted: its
current text was not what got consumed, so it belongs in the next cut.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI

#: Characters that make a manifest entry a pattern rather than a name. A manifest
#: is machine-written, so any of these means the file was hand-edited or forged —
#: refuse the whole run instead of guessing which expansion was intended.
_GLOB_CHARS = frozenset("*?[]{}!")

#: Never removable by a clear, even if a manifest names it. The README is the
#: convention note that makes `changelog.d/` self-explanatory to a contributor;
#: gate 3.1 checks it survives a clear.
_PROTECTED = frozenset({"README.md", "readme.md", ".gitkeep"})


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _repo_name(explicit: str | None, root: Path) -> str:
    """Which repo's entries to clear — the one we are standing in.

    Prefers the git remote's basename over the directory name: a checkout cloned
    to a different local folder (CI does this constantly) must still match the
    manifest key deploy recorded from the remote.
    """
    if explicit:
        return explicit
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    except (OSError, subprocess.SubprocessError):
        pass
    return root.name


def _load_manifest(path: Path) -> tuple[object | None, str | None]:
    """``(manifest, error)`` — parsed via core's reader, never a local parser."""
    from otaman_cli.changelog_gate import REMEDY, changelog_manifest

    core = changelog_manifest()
    if core is None:
        return None, f"cannot read the manifest — {REMEDY}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read manifest {str(path)!r}: {exc}"
    try:
        from otaman_cli.yaml_fast import fast_parse

        data = fast_parse(raw)
    except Exception:  # noqa: BLE001 - fall through to the json attempt
        data = None
    if not isinstance(data, dict):
        try:
            import json

            data = json.loads(raw)
        except Exception:  # noqa: BLE001 - neither yaml nor json
            return None, f"manifest {str(path)!r} is neither YAML nor JSON"
    if not isinstance(data, dict):
        return None, f"manifest {str(path)!r} does not contain a manifest mapping"
    # A release record may embed the manifest under a key rather than being one.
    block = data.get("consumed_fragments") or data.get("manifest") or data
    return core.from_dict(block if isinstance(block, dict) else None), None


def _clear_fragments(argv: list[str]) -> int:
    """Delete exactly the fragments a recorded cut consumed from THIS repo."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="otaman release clear-fragments",
        description="Delete the fragments a release cut consumed from this repo.",
    )
    parser.add_argument("manifest", help="the release record / manifest file recorded at cut")
    parser.add_argument("--repo", default=None, help="manifest key to clear (default: this repo)")
    parser.add_argument(
        "--dir", default="changelog.d", help="fragment directory (default: changelog.d)"
    )
    parser.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    parser.add_argument("--no-commit", action="store_true", help="delete but do not commit")
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete even when a fragment's content changed since the cut",
    )
    args = parser.parse_args(argv)

    root = Path.cwd()
    manifest, err = _load_manifest(Path(args.manifest))
    if err:
        return _bail(err, code=2)

    repo = _repo_name(args.repo, root)
    entries = manifest.for_repo(repo)  # type: ignore[union-attr]
    if not entries:
        known = ", ".join(manifest.repos()) or "(none)"  # type: ignore[union-attr]
        UI.ok(f"nothing to clear — manifest records no fragments for {repo!r}")
        UI.muted(f"  repos in this manifest: {known}")
        UI.muted("  wrong repo? pass --repo <name>")
        return 0

    frag_dir = root / args.dir
    from otaman_cli.changelog_gate import changelog_manifest

    core = changelog_manifest()

    # Validate EVERY entry before deleting ANY of them. This has to be two
    # passes, not one loop that refuses when it reaches a bad name: entries are
    # sorted, so a legitimate `101.feature.md` is deleted long before the loop
    # reaches a forged `README.md` and bails — leaving a half-applied clear from
    # a manifest we just declared untrustworthy. One bad entry means the
    # manifest has been hand-edited, and then none of its rows are authority.
    for entry in entries:
        name = entry.filename
        if set(name) & _GLOB_CHARS:
            return _bail(
                f"manifest entry {name!r} looks like a glob — refusing the whole run.\n"
                "  A manifest names exact files; a pattern means it was hand-edited.",
                code=2,
            )
        if "/" in name or "\\" in name or name in {".", ".."}:
            return _bail(
                f"manifest entry {name!r} is not a bare filename — refusing the whole run.\n"
                f"  Fragments live flat in {args.dir}/; a path separator means traversal.",
                code=2,
            )
        if name in _PROTECTED:
            return _bail(
                f"manifest entry {name!r} is a protected file — refusing the whole run.\n"
                "  The convention note is not a fragment; a manifest naming it is malformed.",
                code=2,
            )

    deleted: list[str] = []
    skipped: list[tuple[str, str]] = []

    for entry in entries:
        name = entry.filename
        target = frag_dir / name
        if not target.is_file():
            skipped.append((name, "already gone"))
            continue

        # The content check is the real guard. An owner who edits a fragment
        # after the cut has written copy that was NOT released; deleting it
        # loses it silently, and the next cut would never see it.
        if entry.content_hash and core is not None:
            try:
                current = core.fragment_hash(target.read_text(encoding="utf-8"))
            except OSError as exc:
                skipped.append((name, f"unreadable: {exc}"))
                continue
            if current != entry.content_hash and not args.force:
                skipped.append((name, "changed since the cut — kept for the next one"))
                continue

        if args.dry_run:
            deleted.append(name)
            continue
        try:
            target.unlink()
        except OSError as exc:
            skipped.append((name, f"cannot delete: {exc}"))
            continue
        deleted.append(name)

    verb = "would clear" if args.dry_run else "cleared"
    if deleted:
        UI.ok(f"{verb} {len(deleted)} fragment(s) from {args.dir}/ for {repo}")
        for name in deleted:
            UI.muted(f"  {name}")
    else:
        UI.ok(f"nothing to clear for {repo} — every recorded fragment is already resolved")
    for name, why in skipped:
        UI.muted(f"  skipped {name}: {why}")

    if deleted and not args.dry_run and not args.no_commit:
        release = getattr(manifest, "release", "") or "release"
        message = (
            f"chore(changelog): clear fragments consumed by {release}\n\n"
            f"Cleared by exact manifest filename, {len(deleted)} fragment(s).\n"
            "Generated by `otaman release clear-fragments`."
        )
        rc = subprocess.run(
            ["git", "-C", str(root), "add", "--", *[f"{args.dir}/{n}" for n in deleted]],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            UI.warn(f"staged nothing — git add failed: {rc.stderr.strip()}")
            return 0
        rc = subprocess.run(
            ["git", "-C", str(root), "commit", "-m", message],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            UI.warn(f"deleted, but not committed: {rc.stderr.strip() or rc.stdout.strip()}")
            return 0
        UI.ok("committed the clear")

    return 0


def cmd_release(args: list[str]) -> int:
    if not args or args[0] in {"-h", "--help", "help"}:
        UI.info("otaman release <subcommand>")
        UI.muted("  clear-fragments <manifest>   Delete fragments a cut consumed from this repo")
        return 0 if args else 1
    sub, rest = args[0], args[1:]
    if sub == "clear-fragments":
        return _clear_fragments(rest)
    return _bail(f"unknown `otaman release` subcommand: {sub!r}")


register(
    CommandSpec(
        name="release",
        handler=cmd_release,
        help="Release assembly (owner side): clear-fragments",
    )
)

__all__ = ["cmd_release"]
