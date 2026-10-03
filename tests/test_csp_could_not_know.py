"""csp 1.5 — "could not know" is not "selected nobody", and the roles table is shown.

core #122 (csp 1.4, committed main ef44bf9) split two facts that cli had been
rendering identically:

    critics=()  could_not_evaluate=('target_role',)  -> could NOT KNOW, input named
    critics=()  could_not_evaluate=()                -> evaluated, selected nobody

The second is a config finding; the first is a limit of what a checkout can see. cli
asserted the second for the first all day yesterday — a `role-based` hook rendered
"role-based selected nobody" with `evaluated=True` (#275 removed the claim by taking
role-based off the evaluable list; core #122 makes it evaluable again *and* reports
the missing input, which is what this task renders).

core also added the config's top-level `roles:` table, so `role-based` resolves from
configuration alone, and the roster view shows it: "selected nobody" reads differently
once you can see the table is empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import critic_policy


def _program(tmp_path: Path, config: str) -> Path:
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n"
        "  - name: a\n    path: ../a\n    owner: a-agent\n"
        "  - name: b\n    path: ../b\n    owner: b-agent\n",
        encoding="utf-8",
    )
    (root / critic_policy.CONFIG_NAME).write_text(config, encoding="utf-8")
    return root


ROLE_BASED = "hooks:\n  scr-critique:\n    primary: role-based\nroles:\n  b-agent: [reviewer]\n"
#: The same config as a dict, for tests that need to hand core a parsed object.
ROLE_BASED_PARSED = {
    "hooks": {"scr-critique": {"primary": "role-based"}},
    "roles": {"b-agent": ["reviewer"]},
}


# ---------------------------------------------------------------------------
# the distinction


def test_an_unknowable_hook_says_COULD_NOT_KNOW_and_names_the_input(tmp_path):
    (hook,) = critic_policy.load(_program(tmp_path, ROLE_BASED)).hooks

    assert hook.could_not_know == ("target_role",)
    assert "could not know" in hook.note
    assert "target_role" in hook.note, "the missing input must be named, not just the state"
    assert "selected nobody" not in hook.note, (
        "the defect this task removes: an unanswerable hook reported as an empty selection"
    )


def test_an_unknowable_hook_is_NOT_marked_evaluated(tmp_path):
    """`evaluated` gates every "this is the set" rendering downstream.

    Marking an unanswerable hook evaluated publishes an empty set as a result — the
    same defect one layer up, which is why this is asserted separately from the note.
    """
    (hook,) = critic_policy.load(_program(tmp_path, ROLE_BASED)).hooks

    assert hook.evaluated is False
    assert hook.critics == ()


def test_selected_nobody_still_reads_as_selected_nobody(tmp_path, monkeypatch):
    """The other side of the split: a hook core DID evaluate, which chose no one, must
    not be relabelled unknowable — it is a real finding about the config.

    Exercised through a stub engine rather than a config, because core #122's
    parse-time refusal removed the configurations that produced this state from a
    checkout: every pairing it accepts can select for the self-owned shape, and
    `sensitivity-scoped` alone is now refused outright. The branch still has to be
    right — an older core, a sensitivity override, or a clearance drop all reach it —
    so it is tested where it can be reached.
    """
    root = _program(tmp_path, ROLE_BASED)
    real = critic_policy._core()

    class _EvaluatedEmpty:
        SelectionContext = real.SelectionContext
        parse_verification_gates = staticmethod(real.parse_verification_gates)
        POLICIES = real.POLICIES

        @staticmethod
        def select_critics(config, hook, ctx):
            res = real.select_critics(config, hook, ctx)
            return type(res)(**{**res.__dict__, "critics": (), "could_not_evaluate": ()})

    monkeypatch.setattr(critic_policy, "_core", lambda: _EvaluatedEmpty)

    (hook,) = critic_policy.load(root).hooks

    assert hook.could_not_know == (), "no input was missing — core evaluated this"
    assert hook.evaluated is True
    assert hook.critics == ()
    assert "selected nobody" in hook.note


# ---------------------------------------------------------------------------
# the probe must not call an unknowable chain uncovered


def test_the_self_owned_probe_stays_silent_when_the_chain_is_unknowable(tmp_path):
    """Reporting "no critic for a-agent's own proposal" when the answer is unknown
    would be the same false claim one level up from the one this task fixes."""
    root = _program(
        tmp_path,
        "hooks:\n  scr-critique:\n    primary: stakeholder-affected\n    fallback: role-based\n"
        "roles:\n  b-agent: [reviewer]\n",
    )

    (hook,) = critic_policy.load(root).hooks

    assert hook.self_owned_uncovered == (), (
        f"claimed knowledge of an unknowable chain: {hook.self_owned_uncovered}"
    )


# ---------------------------------------------------------------------------
# the roles table


def test_the_roster_carries_the_roles_table(tmp_path):
    surface = critic_policy.load(_program(tmp_path, ROLE_BASED))

    assert surface.roster.roles == [("b-agent", ("reviewer",))]


def test_an_absent_roles_table_is_visible_rather_than_blank(tmp_path, capsys, monkeypatch):
    """`role-based` selects from this table, so an empty one is the explanation for a
    hook that chooses no one — and a blank section explains nothing.

    core #122 refuses a config that uses role-based without a roles table, so an empty
    table is only reachable on an older core. Reached here by stubbing the parse to
    return a config whose `roles` is empty — a skipped test would prove nothing, and
    the renderer still has to say something sensible on that bundle.
    """
    import otaman_cli.commands.policy as P

    root = _program(tmp_path, ROLE_BASED)
    real = critic_policy._core()
    permissive = real.parse_verification_gates(ROLE_BASED_PARSED)
    object.__setattr__(permissive, "roles", {}) if hasattr(
        permissive, "__dataclass_fields__"
    ) else None

    class _OlderCore:
        SelectionContext = real.SelectionContext
        POLICIES = real.POLICIES

        @staticmethod
        def parse_verification_gates(raw):
            return permissive

        @staticmethod
        def select_critics(config, hook, ctx):
            return real.select_critics(config, hook, ctx)

    monkeypatch.setattr(critic_policy, "_core", lambda: _OlderCore)
    monkeypatch.setattr(P, "find_project_root", lambda: root)

    P.cmd_policy(["critics"])

    text = "".join(capsys.readouterr())
    assert "Roles" in text
    assert "No roles declared" in text
    assert "role-based selection has nobody to choose from" in text


def test_the_policy_surface_renders_could_not_know(tmp_path, capsys, monkeypatch):
    import otaman_cli.commands.policy as P

    root = _program(tmp_path, ROLE_BASED)
    monkeypatch.setattr(P, "find_project_root", lambda: root)

    P.cmd_policy(["critics"])

    text = "".join(capsys.readouterr())
    assert "could not know who this selects" in text
    assert "target_role" in text
    assert "b-agent" in text, "the roles table is rendered too"


def test_doctor_reports_could_not_know_separately_from_a_config_finding(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    result = check_critic_policy(_program(tmp_path, ROLE_BASED))

    assert result["details"]["role_holders"] == 1
    cnk = [i for i in result.get("issues", []) if "could not know" in i["message"]]
    assert cnk, f"doctor did not report it: {result.get('issues')}"
    assert "target_role" in cnk[0]["message"]
    assert cnk[0]["severity"] == "low", (
        "a limit of the surface is not a defect in the config — the severities differ"
    )


# ---------------------------------------------------------------------------
# core's parse-time refusals reach the operator


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            "hooks:\n  h:\n    primary: stakeholder-affected\n    fallback: sensitivity-scoped\n",
            "pairing",
        ),
        ("hooks:\n  h:\n    primary: role-based\n", "roles table"),
        ("hooks:\n  h:\n    primary: sensitivity-scoped\nrolez: {}\n", "unknown top-level"),
    ],
)
def test_a_refused_config_is_NOT_CHECKED_with_cores_reason(tmp_path, config, expected):
    """core #122 refuses these at parse. cli must surface the reason, not swallow it
    into "no policy declared" — the fail-open conflation this module exists to avoid.
    """
    surface = critic_policy.load(_program(tmp_path, config))

    assert surface.error, "a refused config must be an error, not silence"
    assert expected in surface.error, surface.error
    assert surface.hooks == []


def test_no_test_file_patches_find_project_root_by_bare_assignment():
    """A guard for the defect this file introduced, because its cost is invisible.

    `P.find_project_root = lambda: root` is never undone, so it leaks a dead `tmp_path`
    into every LATER test file in the session. Whether that is harmless or breaks 21
    tests depends on ALPHABETICAL ORDER: two files had done it for weeks and sorted after
    `test_policy_*`; adding `test_csp_*` put a leak ahead of them, and `otaman policy
    apply/diff/check-merge/validate` all failed in the full suite while every file passed
    on its own. A guard, not a convention, because the failure never points at the cause.
    """
    offenders = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if (
                stripped.startswith(("P.", "policy_cmd."))
                and "find_project_root = " in stripped
                and "monkeypatch" not in stripped
            ):
                offenders.append(f"{path.name}:{n}")
    assert not offenders, (
        "patch it with monkeypatch.setattr so it is restored at teardown: " + ", ".join(offenders)
    )
