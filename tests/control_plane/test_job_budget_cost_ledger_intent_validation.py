from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from infra.controlplane import job_budget_cost_ledger_store as budget_store
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_job_budget_admin as budget_admin
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
    return {
        "ok": True,
        "assistant": {
            "content": f"role={role} binding={binding['type']}:{binding['id']} prompt={message}",
            "status": "ok",
            "meta": {"options": dict(options or {})},
        },
    }


def _init_operator_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)
    register.load_or_init_task_register(root)


def _budgeted_job_spec(job_id: str) -> dict:
    return {
        "job_id": job_id,
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mb3"},
        "prompt": f"Run job {job_id}.",
        "schedule": {"mode": "once", "run_at": "2026-04-06T18:30:00Z", "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
    }


def _append_allocation(repo_root: Path, *, job_id: str, amount_minor_units: int) -> None:
    budget_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": f"{job_id}:alloc:001",
            "job_uid": job_id,
            "recorded_at_utc": "2026-04-06T18:00:00Z",
            "amount_minor_units": amount_minor_units,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=repo_root,
    )


def _append_cost(repo_root: Path, *, job_id: str, amount_minor_units: int, recorded_at_utc: str) -> None:
    budget_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": f"{job_id}:cost:{recorded_at_utc}",
            "job_uid": job_id,
            "recorded_at_utc": recorded_at_utc,
            "amount_minor_units": amount_minor_units,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=repo_root,
    )


def test_job_budget_cost_ledger_intent_validation_proves_clear_flow_restart_replay_and_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    job_id = "intent_budget_clear_001"
    spec = sched.save_job(_budgeted_job_spec(job_id), repo_root=tmp_path)
    _append_allocation(tmp_path, job_id=job_id, amount_minor_units=1_000)
    _append_cost(tmp_path, job_id=job_id, amount_minor_units=250, recorded_at_utc="2026-04-06T18:05:00Z")

    execution = sched.execute_job(
        job_id,
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-04-06T18:30:00Z",
    )
    assert execution["ok"] is True

    receipt = sched.load_receipt(job_id, execution["run_id"], repo_root=tmp_path)
    assert receipt["budget_admission"]["enforced"] is True
    assert receipt["budget_admission"]["allowed"] is True
    assert receipt["budget_admission"]["snapshot"]["remaining_minor_units"] == 750

    inspection = budget_admin.inspect_job_budget_ledger(job_uid=job_id)
    assert inspection["snapshot"]["allocated_minor_units"] == 1_000
    assert inspection["snapshot"]["cost_debit_minor_units"] == 250
    assert inspection["snapshot"]["remaining_minor_units"] == 750

    summary = sched.job_summary(spec, state=sched.load_state(job_id, repo_root=tmp_path), repo_root=tmp_path)
    assert summary["budget_status"]["status"] == "clear"
    assert "Budget clear" in summary["budget_status"]["operator_summary"]

    reloaded_store = importlib.reload(budget_store)
    replay = reloaded_store.replay_job_budget_state(job_id, repo_root=tmp_path)
    assert replay == {
        "job_uid": job_id,
        "unit": "usd_cents",
        "allocated_minor_units": 1_000,
        "cost_debit_minor_units": 250,
        "cost_credit_minor_units": 0,
        "remaining_minor_units": 750,
    }


def test_job_budget_cost_ledger_intent_validation_proves_blocked_overspend_recovery_and_operator_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    job_id = "intent_budget_blocked_001"
    spec = sched.save_job(_budgeted_job_spec(job_id), repo_root=tmp_path)
    _append_allocation(tmp_path, job_id=job_id, amount_minor_units=100)
    _append_cost(tmp_path, job_id=job_id, amount_minor_units=125, recorded_at_utc="2026-04-06T18:05:00Z")

    execution = sched.execute_job(
        job_id,
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-04-06T18:30:00Z",
    )
    assert execution["ok"] is False
    assert execution["reason"] == "job_budget_recovery_blocked"

    receipt = sched.load_receipt(job_id, execution["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "blocked"
    assert receipt["budget_admission"]["allowed"] is False
    assert receipt["budget_admission"]["recovery_outcome"]["status"] == "blocked"
    assert receipt["budget_admission"]["snapshot"]["remaining_minor_units"] == -25

    summary = sched.job_summary(spec, state=sched.load_state(job_id, repo_root=tmp_path), repo_root=tmp_path)
    assert summary["budget_status"]["status"] == "recovery_blocked"
    assert summary["budget_status"]["blocked"] is True

    inspection = budget_admin.inspect_job_budget_ledger(job_uid=job_id)
    assert inspection["snapshot"]["remaining_minor_units"] == -25
    assert inspection["recovery_follow_up"]["status"] == "blocked"
    assert inspection["recovery_outcome_records"][0]["reason_code"] == "overspend_detected"

    operator_snapshot = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-06T19:00:00Z")
    notification = next(row for row in operator_snapshot["operator_summary"]["notifications"] if row["kind"] == "job_budget_attention")
    assert notification["severity"] == "danger"
    assert notification["jobs"][0]["job_id"] == job_id
    assert job_id in notification["message"]


def test_job_budget_cost_ledger_intent_validation_proves_manual_review_on_unreadable_ledger_and_operator_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    job_id = "intent_budget_review_required_001"
    spec = sched.save_job(_budgeted_job_spec(job_id), repo_root=tmp_path)
    paths = budget_store.ledger_paths_for_job(job_id, repo_root=tmp_path)
    paths["allocation_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["allocation_log"].write_text("{not-json}\n", encoding="utf-8")

    execution = sched.execute_job(
        job_id,
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-04-06T18:30:00Z",
    )
    assert execution["ok"] is False
    assert execution["reason"] == "job_budget_recovery_review_required"

    receipt = sched.load_receipt(job_id, execution["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "blocked"
    assert receipt["budget_admission"]["allowed"] is False
    assert receipt["budget_admission"]["recovery_outcome"]["status"] == "review_required"
    assert receipt["budget_admission"]["recovery_outcome"]["reason"] == "ledger_inconsistency_detected"
    assert "manual review" in receipt["budget_admission"]["message"]

    summary = sched.job_summary(spec, state=sched.load_state(job_id, repo_root=tmp_path), repo_root=tmp_path)
    assert summary["budget_status"]["status"] == "review_required"
    assert summary["budget_status"]["blocked"] is True
    assert "manual review" in summary["budget_status"]["operator_summary"]

    with pytest.raises(ValueError, match="malformed JSONL entry"):
        budget_admin.inspect_job_budget_ledger(job_uid=job_id)

    recovery_rows = budget_store.read_recovery_outcome_records(job_id, repo_root=tmp_path)
    assert recovery_rows[0]["reason_code"] == "ledger_inconsistency_detected"

    operator_snapshot = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-06T19:00:00Z")
    notification = next(row for row in operator_snapshot["operator_summary"]["notifications"] if row["kind"] == "job_budget_attention")
    assert notification["severity"] == "danger"
    assert notification["jobs"][0]["job_id"] == job_id
    assert job_id in notification["message"]
