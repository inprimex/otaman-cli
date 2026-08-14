"""Unit tests for registry helpers: roles, transitions, bus_messages, loader."""

from __future__ import annotations

import io

import pytest

from otaman_cli.registries import bus_messages, transitions
from otaman_cli.registries.loader import (
    find_business_repo,
    resolve_registry_path,
    yaml_dump,
    yaml_load,
)
from otaman_cli.registries.platform_ext import ProgramExtensions
from otaman_cli.registries.roles import (
    authz_advisory,
    is_transition_only_field,
    required_roles_for,
    resolve_operating_actor,
    resolve_roles,
)

# ---------------------------------------------------------------------------
# transitions


def test_make_transition_minimal():
    t = transitions.make_transition(actor="cpo-agent", action="create", to="Drafting")
    assert t["by"] == "cpo-agent"
    assert t["action"] == "create"
    assert t["to"] == "Drafting"
    assert "at" in t
    assert "from" not in t  # omitted when None


def test_make_transition_with_from_and_note():
    t = transitions.make_transition(
        actor="x",
        action="promote",
        from_="Drafting",
        to="Backlog",
        note="ready",
    )
    assert t["from"] == "Drafting"
    assert t["to"] == "Backlog"
    assert t["note"] == "ready"


def test_append_transition_creates_list_if_absent():
    entity: dict = {"id": "JTBD-1-x"}
    transitions.append_transition(entity, {"at": "now", "by": "x", "action": "create"})
    assert len(entity["transitions"]) == 1


def test_append_transition_appends_to_existing_list():
    entity = {"transitions": [{"at": "a", "by": "x", "action": "create"}]}
    transitions.append_transition(entity, {"at": "b", "by": "y", "action": "promote"})
    assert len(entity["transitions"]) == 2


# ---------------------------------------------------------------------------
# roles


def test_resolve_operating_actor_env_wins(monkeypatch):
    monkeypatch.setenv("OTAMAN_AGENT", "scripted-agent")
    assert resolve_operating_actor() == "scripted-agent"


def test_resolve_operating_actor_falls_back_to_human(monkeypatch, tmp_path):
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    # An empty cwd with no .otaman walk match → "human"
    assert resolve_operating_actor(cwd=tmp_path) == "human"


def test_resolve_roles_returns_multiple():
    platform = ProgramExtensions.model_validate(
        {
            "role-assignments": {"cpo": "roman", "ceo": "roman", "cto": "cto-agent"},
        }
    )
    assert sorted(resolve_roles("roman", platform)) == ["ceo", "cpo"]
    assert resolve_roles("cto-agent", platform) == ["cto"]
    assert resolve_roles("nobody", platform) == []


def test_required_roles_for_known_op():
    assert required_roles_for("outcome.accept-cost") == ("ceo",)
    assert required_roles_for("outcome.retire") == ("cpo", "ceo")
    assert required_roles_for("outcome.list") == ()


def test_authz_advisory_authorized_no_warning():
    err = io.StringIO()
    ok = authz_advisory("outcome.accept-cost", "roman", ["ceo"], stderr=err)
    assert ok is True
    assert err.getvalue() == ""


def test_authz_advisory_unauthorized_logs_but_returns_true():
    err = io.StringIO()
    ok = authz_advisory("outcome.accept-cost", "roman", ["cto"], stderr=err)
    assert ok is True  # v1 advisory: never blocks
    assert "WARN" in err.getvalue()
    assert "requires role" in err.getvalue()
    assert "outcome.accept-cost" in err.getvalue()


def test_authz_advisory_any_role_op_silent():
    err = io.StringIO()
    authz_advisory("outcome.list", "anyone", [], stderr=err)
    assert err.getvalue() == ""


def test_transition_only_fields():
    assert is_transition_only_field("status")
    assert is_transition_only_field("chosen-solution")
    assert is_transition_only_field("transitions")
    assert is_transition_only_field("id")
    assert not is_transition_only_field("product-notes")
    assert not is_transition_only_field("category")


# ---------------------------------------------------------------------------
# loader — path resolution + yaml round-trip


def test_yaml_load_returns_empty_dict_for_missing_file(tmp_path):
    p = tmp_path / "missing.yaml"
    assert yaml_load(p) == {}


def test_yaml_round_trip(tmp_path):
    p = tmp_path / "x.yaml"
    yaml_dump({"outcomes": [{"id": "JTBD-1-a"}]}, p)
    data = yaml_load(p)
    assert data["outcomes"][0]["id"] == "JTBD-1-a"


def test_find_business_repo_via_env(monkeypatch, tmp_path):
    target = tmp_path / "biz"
    target.mkdir()
    monkeypatch.setenv("OTAMAN_BUSINESS_DIR", str(target))
    assert find_business_repo(tmp_path / "ignored") == target.resolve()


def test_find_business_repo_from_platform_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_BUSINESS_DIR", raising=False)
    parent = tmp_path / "p"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    biz = parent / "biz"
    biz.mkdir()
    (meta / "platform.yaml").write_text(
        "repos:\n  - name: biz\n    path: ../biz\n    owner: cpo-agent\n",
        encoding="utf-8",
    )
    assert find_business_repo(meta) == biz.resolve()


def test_find_business_repo_falls_back_to_main_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_BUSINESS_DIR", raising=False)
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text(
        "repos:\n  - name: myproj\n    path: .\n    owner: main-agent\n",
        encoding="utf-8",
    )
    assert find_business_repo(meta) == meta.resolve()


def test_find_business_repo_returns_none_when_no_match(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_BUSINESS_DIR", raising=False)
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text("repos: []\n", encoding="utf-8")
    assert find_business_repo(meta) is None


def test_resolve_registry_path_outcomes(monkeypatch, tmp_path):
    monkeypatch.setenv("OTAMAN_BUSINESS_DIR", str(tmp_path / "biz"))
    (tmp_path / "biz").mkdir()
    (tmp_path / "platform.yaml").write_text("repos: []\n", encoding="utf-8")
    p = resolve_registry_path(tmp_path, "outcomes")
    assert p == (tmp_path / "biz" / "outcomes.yaml").resolve()


def test_resolve_registry_path_unknown_kind_raises(tmp_path):
    with pytest.raises(ValueError):
        resolve_registry_path(tmp_path, "bogus")


# ---------------------------------------------------------------------------
# bus_messages — builders + emitter


def test_build_outcome_estimate_requested_shape():
    o = {
        "id": "JTBD-1-a",
        "priority": "P1",
        "impact": "M",
        "statement": {"as-a": "x", "i-want-to": "y", "incremental-outcome": "z", "so-i-can": "w"},
    }
    msg = bus_messages.build_outcome_estimate_requested(o, "cpo-agent")
    assert msg["type"] == "outcome-estimate-requested"
    assert msg["from"] == "cpo-agent"
    assert msg["to"] == "cto-agent"
    assert msg["payload"]["outcome-id"] == "JTBD-1-a"
    assert msg["payload"]["impact"] == "M"


def test_build_outcome_cost_accepted_shape():
    o = {"id": "JTBD-1-a", "release": "Sprint-1"}
    s = {"id": "SOL-1-a", "effort-days": 3, "t-shirt": "Small"}
    msg = bus_messages.build_outcome_cost_accepted(o, s, "human")
    assert msg["type"] == "outcome-cost-accepted"
    assert msg["payload"]["chosen-solution"] == "SOL-1-a"
    assert msg["payload"]["effort-days"] == 3


def test_build_outcome_status_changed_shape():
    o = {"id": "JTBD-1-a"}
    msg = bus_messages.build_outcome_status_changed(
        o, "Drafting", "Backlog", "cpo-agent", "promote"
    )
    assert msg["to"] == "all"
    assert msg["payload"]["from"] == "Drafting"
    assert msg["payload"]["to"] == "Backlog"


def test_emit_writes_message_file(tmp_path):
    msg = bus_messages.build_outcome_status_changed(
        {"id": "JTBD-1-a"},
        "Drafting",
        "Backlog",
        "cpo-agent",
        "promote",
    )
    path = bus_messages.emit(msg, tmp_path)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "outcome-status-changed" in text
    assert "JTBD-1-a" in text
    assert "type: outcome-status-changed" in text


def test_emit_rejects_unknown_message_type(tmp_path):
    with pytest.raises(ValueError):
        bus_messages.emit({"type": "bogus", "from": "x", "to": "y"}, tmp_path)
