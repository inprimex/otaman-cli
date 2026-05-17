"""Tests for OnboardAudit — JSONL emitter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otaman_cli.onboard.audit import OnboardAudit, CE_SOURCE, CE_SPECVERSION


class TestEmit:
    def test_writes_cloudevents_envelope(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        path = audit.emit("otaman.onboard.test", {"key": "val"}, actor="alice")
        assert path.is_file()
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["specversion"] == CE_SPECVERSION
        assert envelope["source"] == CE_SOURCE
        assert envelope["type"] == "otaman.onboard.test"
        assert envelope["data"]["actor"] == "alice"
        assert envelope["data"]["key"] == "val"
        assert envelope["data"]["result"] == "success"

    def test_failure_result(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        path = audit.emit("type.x", {}, actor="a", result="failure")
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["data"]["result"] == "failure"


class TestConvenienceHelpers:
    def test_user_added(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        audit.user_added(actor="ceo@example.com", subject="alice@example.com", roles=["otaman:developer"])
        path = next(tmp_path.glob("*.jsonl"))
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["type"] == "otaman.onboard.user_added"
        assert envelope["data"]["subject"] == "alice@example.com"
        assert envelope["data"]["roles"] == ["otaman:developer"]

    def test_user_add_failed_sets_failure_result(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        audit.user_add_failed(actor="ceo", subject="alice@example.com", error="bad role")
        path = next(tmp_path.glob("*.jsonl"))
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["type"] == "otaman.onboard.user_add_failed"
        assert envelope["data"]["result"] == "failure"
        assert envelope["data"]["error"] == "bad role"

    def test_doctor_run_success(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        audit.doctor_run(actor="alice", fail_count=0, warn_count=2)
        path = next(tmp_path.glob("*.jsonl"))
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["data"]["result"] == "success"
        assert envelope["data"]["fail_count"] == 0
        assert envelope["data"]["warn_count"] == 2

    def test_doctor_run_failure_when_fail_count_nonzero(self, tmp_path):
        audit = OnboardAudit(tmp_path)
        audit.doctor_run(actor="alice", fail_count=1, warn_count=0)
        path = next(tmp_path.glob("*.jsonl"))
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert envelope["data"]["result"] == "failure"
