"""unified-launcher-profiles 1.2 — the generated launcher actually RUNS right.

These render the template and execute the resulting bash against stub binaries,
rather than asserting on the template text. That is deliberate: every bug found
while building this passed both a Jinja render and `bash -n`, and only showed up
when the script ran.

  * `profile_console "$c" && SEAT_CONSOLE=1` was the last statement in its
    branch, so a profile WITHOUT a console made the compound false, that false
    became the function's return status, and the caller's `|| exit 1` killed the
    launch. Every non-console profile was unusable.
  * "nothing started" was treated as failure unconditionally, so a console-only
    profile exited before the console was ever seated — the one thing it exists
    to do.

Neither is visible in the template source. Both are obvious the moment you run it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

from otaman_cli.launch_profiles import parse  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "init" / "templates"

LAUNCH = {
    "launch": {
        "console": {"socket": "otaman-human", "session": "otaman-console"},
        "profiles": [
            {"name": "backend", "agents": ["core-agent"]},
            {"name": "solo-console", "console": True},
        ],
    }
}

_STUBS = {
    "tmux": '#!/usr/bin/env bash\ncase " $* " in *" has-session "*) exit 1 ;; esac\n'
    'echo "TMUX: $*" >> "$TRACE"\nexit 0\n',
    "otaman": '#!/usr/bin/env bash\necho "OTAMAN: $*" >> "$TRACE"\nexit 0\n',
    "claude": "#!/usr/bin/env bash\nexit 0\n",
    "curl": '#!/usr/bin/env bash\necho "CURL: $*" >> "$TRACE"\nprintf 200\nexit 0\n',
}


def _render(tmp_path: Path, *, mode: str, launch) -> Path:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)))
    connection = (
        {"mode": "mesh", "mesh": {"http_base": "http://runner", "token_source": "TOK"}}
        if mode == "mesh"
        else {"mode": "local"}
    )
    out = env.get_template("launch.sh.j2").render(
        agents=[{"name": "cli-agent", "enabled": True}, {"name": "core-agent", "enabled": True}],
        agent_repos={"cli-agent": "otaman-cli", "core-agent": "otaman-core"},
        connection=connection,
        tmux={"session_prefix": "ot", "layout": "tiled"},
        launch=launch,
    )
    script = tmp_path / f"launch-{mode}.sh"
    script.write_text(out, encoding="utf-8")
    return script


def _stubs(tmp_path: Path) -> Path:
    binp = tmp_path / "bin"
    binp.mkdir(exist_ok=True)
    for name, body in _STUBS.items():
        f = binp / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    return binp


def _run(script: Path, arg: str | None, tmp_path: Path):
    """``(returncode, trace_text)`` from running the generated launcher."""
    trace = tmp_path / "trace.txt"
    trace.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{_stubs(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        "TRACE": str(trace),
        "TOK": "token",
    }
    proc = subprocess.run(
        ["bash", str(script), *([arg] if arg else [])],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, trace.read_text(encoding="utf-8")


def _agents_started(trace: str) -> int:
    return sum(1 for line in trace.splitlines() if "new-session" in line or "/spawn" in line)


def _consoles_seated(trace: str) -> int:
    return sum(1 for line in trace.splitlines() if "console seat" in line)


#: These EXECUTE the generated POSIX launcher, so they run where that launcher
#: is the artifact a tenant actually uses. Windows tenants get `launch.ps1`
#: instead, and the `#!/usr/bin/env bash` stubs here are not executable there
#: (chmod is a no-op), so running them would test the harness, not the launcher.
#:
#: Note what this does NOT skip: macOS. Its bash is 3.2, and that is precisely
#: where `mapfile` silently did nothing and a profile started the whole fleet
#: while reporting success — a bug only a macOS run could find.
pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX launcher; Windows uses launch.ps1"),
]
MODES = ["local", "mesh"]


@pytest.mark.parametrize("mode", MODES)
def test_the_template_renders_and_parses(tmp_path, mode):
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


@pytest.mark.parametrize("mode", MODES)
def test_no_argument_without_a_tty_launches_full(tmp_path, mode):
    """D3 back-compat: every existing automated caller gets today's behaviour."""
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    rc, trace = _run(script, None, tmp_path)
    assert rc == 0
    assert _agents_started(trace) == 2


@pytest.mark.parametrize("mode", MODES)
def test_a_profile_launches_only_its_agents(tmp_path, mode):
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    rc, trace = _run(script, "backend", tmp_path)
    assert rc == 0, "a non-console profile must not read as a failed selection"
    assert _agents_started(trace) == 1
    assert "core-agent" in trace and "cli-agent" not in trace


@pytest.mark.parametrize("mode", MODES)
def test_a_profile_resolves_by_menu_position_too(tmp_path, mode):
    """`3` and `backend` must mean the same thing — profiles start at 3."""
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    rc_name, by_name = _run(script, "backend", tmp_path)
    rc_pos, by_pos = _run(script, "3", tmp_path)
    assert rc_name == rc_pos == 0
    assert _agents_started(by_name) == _agents_started(by_pos) == 1


@pytest.mark.parametrize("mode", MODES)
def test_a_console_only_profile_seats_the_console_and_succeeds(tmp_path, mode):
    """Starts no agents on purpose — that must not be read as "nothing happened"."""
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    rc, trace = _run(script, "solo-console", tmp_path)
    assert rc == 0
    assert _agents_started(trace) == 0
    assert _consoles_seated(trace) == 1


@pytest.mark.parametrize("mode", MODES)
def test_an_unknown_selection_fails_loudly(tmp_path, mode):
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    rc, trace = _run(script, "nonsense", tmp_path)
    assert rc != 0
    assert _agents_started(trace) == 0


@pytest.mark.parametrize("mode", MODES)
def test_a_profile_means_the_same_thing_in_every_connection_mode(tmp_path, mode):
    """1.2: agent spawn is factored over ONE list, so a profile cannot be a
    feature that mesh has and local does not."""
    script = _render(tmp_path, mode=mode, launch=parse(LAUNCH))
    _rc, trace = _run(script, "backend", tmp_path)
    assert _agents_started(trace) == 1


@pytest.mark.parametrize("mode", MODES)
def test_an_absent_launch_block_reproduces_legacy_behaviour(tmp_path, mode):
    """D3 — no `launch:` means full-only, no menu, no console step."""
    script = _render(tmp_path, mode=mode, launch=None)
    body = script.read_text(encoding="utf-8")
    assert "console seat" not in body, "legacy launcher must not seat a console"
    assert "Launch which set?" not in body, "legacy launcher must not offer a menu"
    rc, trace = _run(script, None, tmp_path)
    assert rc == 0
    assert _agents_started(trace) == 2
    assert _consoles_seated(trace) == 0


def test_the_console_step_never_writes_tmux_itself(tmp_path):
    """D1 stays in Python: the template calls `otaman console seat`, so the rule
    about which server may hold a human console has exactly one home."""
    body = _render(tmp_path, mode="local", launch=parse(LAUNCH)).read_text(encoding="utf-8")
    seat_block = body.split("Seating the human console")[1]
    assert "otaman console seat" in seat_block
    assert "tmux -L" not in seat_block, "the template is hand-placing the seat"
    assert "/spawn" not in seat_block


def _render_agents(tmp_path: Path, agents, launch=None) -> Path:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)))
    out = env.get_template("launch.sh.j2").render(
        agents=agents,
        agent_repos={},
        connection={"mode": "local"},
        tmux={"session_prefix": "ot", "layout": "tiled"},
        launch=launch,
    )
    script = tmp_path / "launch-empty.sh"
    script.write_text(out, encoding="utf-8")
    return script


def test_a_legacy_launcher_with_no_agents_still_fails(tmp_path):
    """The regression the profile work introduced, and nearly shipped.

    Two bugs stacked here, neither caught by the behavioural tests above
    because both need the "zero agents started" branch:

    1. `_count` was defined INSIDE the `launch:` block, so the legacy guard that
       calls it hit `command not found`.
    2. Keying "did anything happen" on "were agents selected" made a legacy
       launcher with no enabled agents exit 0 — launching nothing, reported as
       success.

    Found by diffing legacy rendered output against pre-change main, not by a
    test. Pinned here so the next edit to that guard cannot reintroduce it.
    """
    script = _render_agents(tmp_path, agents=[])
    rc, trace = _run(script, None, tmp_path)
    assert rc != 0, "a launcher that started nothing reported success"
    assert "_count: command not found" not in trace
    assert _agents_started(trace) == 0


def test_a_console_only_profile_is_the_one_exception(tmp_path):
    """Seating a console IS something happening — that is why the guard keys on
    SEAT_CONSOLE rather than on the agent count."""
    launch = parse(
        {
            "launch": {
                "console": {"socket": "otaman-human"},
                "profiles": [{"name": "solo", "console": True}],
            }
        }
    )
    script = _render_agents(tmp_path, agents=[{"name": "a", "enabled": True}], launch=launch)
    rc, trace = _run(script, "solo", tmp_path)
    assert rc == 0
    assert _agents_started(trace) == 0
    assert _consoles_seated(trace) == 1
