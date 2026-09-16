"""Memoized/fast reads behind the console surfaces (console perf work).

The console was slow for a measurable reason, not a mysterious one. On the live
program (5473 active bus messages, 53 changes, 69 capabilities):

    home (entry screen)   7818 ms -> 3272 ms
    artifacts (value)     7992 ms -> 1881 ms
    messages queue        1902 ms ->  829 ms  (335 ms on a second visit)
    lifecycle             3800 ms -> 1188 ms

Three causes, all of them repeated work rather than volume:

1. the same platform.yaml re-parsed 107 times per render (`_specs_changes_dir`)
2. one `git log` spawned PER CHANGE — 106 subprocesses, ~3.0 s
3. the bus directory scanned THREE times per Home render, YAML-parsing ~5500
   frontmatters to find the ~735 that any lister wanted

These tests pin the mechanisms, and — more importantly — that none of it
changed what the surfaces SHOW.
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli import yaml_fast
from otaman_cli.console import bus_index


@pytest.fixture(autouse=True)
def _clean_caches():
    yaml_fast.clear_cache()
    bus_index.clear_cache()
    yield
    yaml_fast.clear_cache()
    bus_index.clear_cache()


# ---------------------------------------------------------------------------
# yaml_fast


def test_load_file_parses(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("project: demo\nversion: '1.0'\n", encoding="utf-8")
    assert yaml_fast.load_file(f) == {"project": "demo", "version": "1.0"}


def test_load_file_memoizes_one_parse(tmp_path, monkeypatch):
    f = tmp_path / "a.yaml"
    f.write_text("k: v\n", encoding="utf-8")
    calls = []
    real = yaml_fast.fast_parse
    monkeypatch.setattr(yaml_fast, "fast_parse", lambda t: (calls.append(1), real(t))[1])
    for _ in range(20):
        yaml_fast.load_file(f)
    assert len(calls) == 1  # 20 reads, one parse


def test_an_edited_file_reparses(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("k: one\n", encoding="utf-8")
    assert yaml_fast.load_file(f) == {"k": "one"}
    f.write_text("k: two-longer\n", encoding="utf-8")  # size changes → new key
    assert yaml_fast.load_file(f) == {"k": "two-longer"}


def test_missing_and_broken_files_degrade(tmp_path):
    assert yaml_fast.load_file(tmp_path / "nope.yaml", {}) == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text("{[not yaml", encoding="utf-8")
    assert yaml_fast.load_file(bad, {}) == {}


def test_clear_cache_forces_a_reread(tmp_path, monkeypatch):
    f = tmp_path / "a.yaml"
    f.write_text("k: v\n", encoding="utf-8")
    yaml_fast.load_file(f)
    assert yaml_fast.cache_size() == 1
    yaml_fast.clear_cache()
    assert yaml_fast.cache_size() == 0


def test_the_fast_loader_agrees_with_pyyaml(tmp_path):
    """The speed must not come from parsing differently."""
    doc = (
        "project: demo\n"
        "version: '1.0'\n"
        "flag: true\n"
        "when: 2026-09-16T21:31:47Z\n"
        "items:\n  - a\n  - b\n"
        "nested:\n  key: value\n"
    )
    f = tmp_path / "a.yaml"
    f.write_text(doc, encoding="utf-8")
    assert yaml_fast.load_file(f) == yaml.safe_load(doc)


# ---------------------------------------------------------------------------
# bus_index — the two tiers


def test_flat_tags_parses_flat_frontmatter():
    tags = bus_index.flat_tags("to: human\ntype: info\nx-cc: true\n")
    assert tags == {"to": "human", "type": "info", "x-cc": "true"}


def test_flat_tags_strips_quotes():
    assert bus_index.flat_tags('to: "human"\n')["to"] == "human"


@pytest.mark.parametrize(
    "text",
    [
        "to: human\nnested:\n  key: v\n",  # indented
        "- a\n- b\n",  # sequence
        "just a line\n",  # no key
    ],
)
def test_flat_tags_refuses_what_it_cannot_parse(text):
    """It must decline rather than guess — the cheap tier is allowed to skip a
    message, never to describe one."""
    assert bus_index.flat_tags(text) is None


def test_the_cheap_tier_never_builds_a_row(tmp_path, monkeypatch):
    """The reason for two tiers: YAML TYPES values. 5451 of the live bus's
    timestamps parse to datetime, whose str() is `2026-05-20 08:41:22+00:00`,
    not the `...T...Z` in the file. Building rows from flat strings would
    silently change displayed timestamps and the sort that uses them.
    """
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    active = root / ".agents" / "bus" / "active"
    active.mkdir(parents=True)
    (active / "acks").mkdir()
    (active / "20260916T213147-a-to-human-info.md").write_text(
        "---\nto: human\nfrom: spec-agent\ntype: info\ntimestamp: 2026-09-16T21:31:47Z\n---\n\n"
        "## Subject: hello\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Program, "bus_paths", lambda self: (active, active / "acks"), raising=False)
    (entry,) = bus_index.active_entries(Program(name="d", root=root))

    # cheap tier: raw strings, for filtering only
    assert entry.tag("timestamp") == "2026-09-16T21:31:47Z"
    # authoritative tier: YAML typing preserved, as the rows have always used
    assert str(entry.fm["timestamp"]) == str(yaml.safe_load("t: 2026-09-16T21:31:47Z\n")["t"])


def test_flag_coerces_like_yaml():
    """`x-cc: false` must be False, not a truthy string."""
    assert bus_index.BusEntry(path=None, tags={"x-cc": "true"}).flag("x-cc") is True
    assert bus_index.BusEntry(path=None, tags={"x-cc": "false"}).flag("x-cc") is False
    assert bus_index.BusEntry(path=None, tags={}).flag("x-cc") is False


def test_the_authoritative_parse_is_lazy(tmp_path, monkeypatch):
    """Scanning must not parse what no lister keeps — that was the 728 ms."""
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    active = root / ".agents" / "bus" / "active"
    active.mkdir(parents=True)
    (active / "acks").mkdir()
    for i in range(12):
        (active / f"20260901T{i:06d}-x-to-core-agent-info.md").write_text(
            f"---\nto: core-agent\ntype: info\ntimestamp: 2026-09-01T00:00:{i:02d}Z\n---\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(Program, "bus_paths", lambda self: (active, active / "acks"), raising=False)
    entries = bus_index.active_entries(Program(name="d", root=root))
    assert len(entries) == 12
    tags, parsed = bus_index.cache_size()
    assert tags == 12
    assert parsed == 0  # nothing authoritative parsed just by scanning
    _ = entries[0].fm  # ask for one
    assert bus_index.cache_size()[1] == 1  # only what was asked for


# ---------------------------------------------------------------------------
# the batched git log


def test_last_touch_uses_one_batched_log(tmp_path, monkeypatch):
    """This was one `git log` PER CHANGE — 106 spawns, ~3.0 s of one render."""
    import otaman_cli.lifecycle as L

    L._TOUCH_CACHE.clear()
    calls: list[tuple] = []

    class R:
        returncode = 0

        def __init__(self, out=""):
            self.stdout = out

    def fake_git(repo, *args, **kw):
        calls.append(args)
        if args[:1] == ("rev-parse",) and args[1] == "--show-toplevel":
            return R(str(tmp_path))
        if args[:1] == ("rev-parse",):
            return R("abc123")
        return R("2026-09-16\nopenspec/changes/alpha/tasks.md\nopenspec/changes/beta/tasks.md\n")

    monkeypatch.setattr(L, "_git", fake_git)
    changes = tmp_path / "openspec" / "changes"
    (changes / "alpha").mkdir(parents=True)
    (changes / "beta").mkdir()

    assert L._last_real_touch(changes / "alpha") == "2026-09-16"
    assert L._last_real_touch(changes / "beta") == "2026-09-16"
    assert sum(1 for c in calls if c[:1] == ("log",)) == 1  # ONE log for both


def test_last_touch_maps_archived_names(tmp_path, monkeypatch):
    import otaman_cli.lifecycle as L

    L._TOUCH_CACHE.clear()

    class R:
        returncode = 0

        def __init__(self, out=""):
            self.stdout = out

    def fake_git(repo, *args, **kw):
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return R(str(tmp_path))
        if args[:1] == ("rev-parse",):
            return R("sha")
        return R("2026-09-17\nopenspec/changes/archive/2026-09-17-thing/tasks.md\n")

    monkeypatch.setattr(L, "_git", fake_git)
    d = tmp_path / "openspec" / "changes" / "archive" / "2026-09-17-thing"
    d.mkdir(parents=True)
    assert L._last_real_touch(d) == "2026-09-17"


def test_unknown_change_reports_question_mark(tmp_path, monkeypatch):
    import otaman_cli.lifecycle as L

    L._TOUCH_CACHE.clear()
    monkeypatch.setattr(L, "_git", lambda *a, **k: None)  # git unavailable
    assert L._last_real_touch(tmp_path / "whatever") == "?"


# ---------------------------------------------------------------------------
# refresh really re-reads


def test_every_refresh_action_invalidates_the_caches():
    """`r` means "read the world again". A screen that refreshed without
    clearing would serve a cached answer and make the key a lie."""
    import inspect

    from otaman_cli.console import app

    offenders = []
    for name, obj in vars(app).items():
        if not inspect.isclass(obj) or not name.endswith("Screen"):
            continue
        fn = obj.__dict__.get("action_refresh")
        if fn is None:
            continue
        if "invalidate_read_caches" not in inspect.getsource(fn):
            offenders.append(name)
    assert offenders == [], f"refresh without cache invalidation: {offenders}"


def test_invalidate_clears_both_caches(tmp_path):
    from otaman_cli.console.app import invalidate_read_caches

    f = tmp_path / "a.yaml"
    f.write_text("k: v\n", encoding="utf-8")
    yaml_fast.load_file(f)
    assert yaml_fast.cache_size() == 1
    invalidate_read_caches()
    assert yaml_fast.cache_size() == 0
    assert bus_index.cache_size() == (0, 0)


# ---------------------------------------------------------------------------
# read-only vs round-trip


def test_yaml_read_is_documented_as_display_only():
    """`yaml_load` round-trips through ruamel so `yaml_dump` preserves a
    human's comments; `yaml_read` discards them for speed. Feeding a yaml_read
    result back to yaml_dump would silently strip those comments."""
    from otaman_cli.registries.loader import yaml_read

    assert "DISPLAY ONLY" in (yaml_read.__doc__ or "")
    assert "NEVER" in (yaml_read.__doc__ or "")


def test_yaml_read_returns_plain_data(tmp_path):
    from otaman_cli.registries.loader import yaml_read

    f = tmp_path / "r.yaml"
    f.write_text("# a comment\noutcomes:\n  - id: JTBD-1\n", encoding="utf-8")
    data = yaml_read(f)
    assert data == {"outcomes": [{"id": "JTBD-1"}]}
    assert type(data) is dict  # not a ruamel CommentedMap


def test_yaml_read_missing_file(tmp_path):
    from otaman_cli.registries.loader import yaml_read

    assert yaml_read(tmp_path / "nope.yaml") == {}
