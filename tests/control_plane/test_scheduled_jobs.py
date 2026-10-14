from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from infra.controlplane import continuation_packet_store as packet_store
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import job_budget_cost_ledger_store as budget_store
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger
from infra.webui import server_agents as _agents
from infra.webui import server_lab_assistant_host as host
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


def _valid_loop_builder_output(
    *,
    changed_file: str = "infra/controlplane/scheduled_jobs.py",
    command: str = "python -m py_compile infra/controlplane/scheduled_jobs.py",
) -> str:
    return f"Changed files:\n- {changed_file}\n\nValidation Commands Run:\n- {command}\n"


def _valid_loop_verifier_pass(
    *,
    command: str = "python -m py_compile infra/controlplane/scheduled_jobs.py",
) -> str:
    return (
        "VERDICT: PASS\n"
        "Findings:\n"
        "- none\n\n"
        "Boundary Review:\n"
        "- scope_alignment: pass\n"
        "- contract_alignment: pass\n\n"
        "Proof Review:\n"
        "- evidence_status: sufficient\n"
        "- pass_fail_closed_check: satisfied\n"
        "- remaining_proof_gaps: none\n\n"
        "Validation Review:\n"
        f"- commands_reviewed: {command}\n"
        "- outcome_support: sufficient\n"
    )


def _copy_prompt_fixture(*, repo_root: Path, name: str) -> None:
    """Copy one canonical prompt file into an isolated temporary repo root."""
    src = (_agents._repo_root() / "governance" / "agents" / "prompts" / name).resolve()
    dst = (repo_root / "governance" / "agents" / "prompts" / name).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _seed_task_register(repo_root: Path, *, task_id: str, ledger_uid: str = "") -> None:
    current = register.load_or_init_task_register(repo_root)
    register.upsert_task(
        repo_root,
        expected_generation=int(current.get("write_generation") or 0),
        writer="scheduler.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"task {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "run scheduled job",
        },
    )


def _live_log(label: str, detail: str = "") -> None:
    """Emit an immediate progress line for opt-in live integration tests."""
    suffix = f" | {detail}" if str(detail or "").strip() else ""
    print(f"[scheduler-e2e] {label}{suffix}", flush=True)


class _FakeCodexManager:
    def ensure_worker(self, *, binding, role, bootstrap_text):
        return {"ok": True, "worker_id": "worker-scheduled-job"}

    def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
        return {
            "ok": True,
            "worker_id": "worker-scheduled-job",
            "protocol": "oneshot",
            "exit_code": 0,
            "content": "K2N7_CANONICAL_OK PIS-20260327-173634Z-K2N7",
        }


def _init_ledger_bound_codex_repo(root: Path) -> None:
    host.bootstrap_host_baseline(root)
    item_dir = (
        root
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "verified"
        / "2026"
        / "03"
        / "PIS-20260327-173634Z-K2N7__autonomous-codex-platform-exercise-and-repair-campaign"
    )
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text("# Patch Instruction Sheet (PIS)\n\n**UID:** PIS-20260327-173634Z-K2N7\n", encoding="utf-8")
    (item_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-20260327-173634Z-K2N7",
                "title": "Autonomous Codex Platform Exercise And Repair Campaign",
                "status": "in_progress",
                "schema_version": "ledger_item_v1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (item_dir / "events.jsonl").write_text("", encoding="utf-8")
    _copy_prompt_fixture(repo_root=root, name="_base.md")
    _copy_prompt_fixture(repo_root=root, name="role__generalist_mobile.md")


def _patch_real_server_agents_codex_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setattr(_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        _agents,
        "load_prompt_bundle",
        lambda role, binding, prompt_file=None, workflow=False: {
            "instructions": "generalist prompt",
            "base": {"sha256": "base"},
            "role": {"sha256": "role"},
        },
    )
    monkeypatch.setattr(_agents, "_build_repo_context", lambda message, context_rows: {"text": "", "files": []})
    monkeypatch.setattr(_agents, "_refresh_mobile_bubble_projection", lambda **kwargs: None)
    monkeypatch.setattr(_agents, "_maybe_dispatch_direct_host_verification", lambda **kwargs: {})
    monkeypatch.setattr(_agents, "_codex_manager", lambda: _FakeCodexManager())


def test_scheduled_job_once_execute_writes_receipt_report_and_ledger(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    saved = sched.save_job(
        {
            "job_id": "nightly_generalist_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Review yesterday and write a memo.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "decision_memo", "format": "markdown"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    assert saved["schema_version"] == sched.SCHEMA_VERSION

    listed = sched.list_jobs(now_utc="2026-03-08T04:00:00Z")
    assert len(listed) == 1
    assert listed[0]["due"]["eligible"] is True
    assert listed[0]["job_shape"] == "verification_loop_job"
    assert listed[0]["job_shape_source"] == "explicit"
    assert listed[0]["job_shape_compatibility_mode"] is False

    run = sched.execute_job("nightly_generalist_001", trigger="scheduler", now_utc="2026-03-08T04:00:00Z")
    assert run["ok"] is True
    receipt = sched.load_receipt("nightly_generalist_001", run["run_id"])
    assert receipt["status"] == "ok"
    assert receipt["job_shape"] == "verification_loop_job"
    assert receipt["job_shape_source"] == "explicit"
    assert receipt["job_shape_compatibility_mode"] is False
    assert "Review yesterday" in receipt["assistant"]["content"]
    assert "expected_output: kind=decision_memo format=markdown" in receipt["dispatched_message"]

    report_path = Path(run["report_path"])
    assert report_path.exists()
    assert "Scheduled Job Report" in report_path.read_text(encoding="utf-8")

    ledger_rows = sched.list_execution_events()
    assert [row["event"] for row in ledger_rows] == ["started", "finished"]
    state = sched.load_state("nightly_generalist_001")
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    with pytest.raises(FileNotFoundError):
        sched.load_job("nightly_generalist_001")
    archived = sorted((sched.archive_dir(tmp_path) / "nightly_generalist_001").glob("retired_terminal_once__*.json"))
    assert archived
    archived_payload = json.loads(archived[-1].read_text(encoding="utf-8"))
    assert archived_payload["reason"] == "once_job_completed"
    assert archived_payload["job"]["enabled"] is False
    assert archived_payload["job"]["prompt_meta"]["retired_terminal_once"]["reason"] == "once_job_completed"


def test_load_job_marks_legacy_job_shape_as_compatibility_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_shape_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_shape_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "swarm_launch",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_arch_gov_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4"},
                "display_title": "legacy shape job",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy swarm job",
                "swarm": {
                    "objective": "coordinate child jobs",
                    "mission_type": "delivery_patch",
                    "write_policy": {"mode": "bounded_patch"},
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = sched.load_job("legacy_shape_001", repo_root=tmp_path)
    assert loaded["job_shape"] == "fanout_job"
    assert loaded["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert loaded["job_shape_compatibility_mode"] is True
    assert loaded["job_shape_migration_status"] == "legacy_pending_backfill"
    assert loaded["job_shape_migration_reason"] == ""
    raw = json.loads((inbox / "legacy_shape_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape_migration_status"] == "legacy_pending_backfill"
    assert raw["job_shape_migration_reason"] == ""


def _budgeted_job_spec(job_id: str) -> dict:
    return {
        "job_id": job_id,
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mb3"},
        "prompt": "Review the durable state and report back.",
        "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        "expected_output": {"kind": "decision_memo", "format": "markdown"},
        "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
    }


def _append_budget_allocation(*, repo_root: Path, job_id: str, amount_minor_units: int) -> None:
    budget_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": f"{job_id}:alloc:001",
            "job_uid": job_id,
            "recorded_at_utc": "2026-03-08T03:00:00Z",
            "amount_minor_units": amount_minor_units,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=repo_root,
    )


def _append_budget_cost(*, repo_root: Path, job_id: str, amount_minor_units: int) -> None:
    budget_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": f"{job_id}:cost:001",
            "job_uid": job_id,
            "recorded_at_utc": "2026-03-08T03:10:00Z",
            "amount_minor_units": amount_minor_units,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=repo_root,
    )


def test_execute_job_allows_launch_when_budget_snapshot_has_remaining_funds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(_budgeted_job_spec("budget_job_ok_001"), repo_root=tmp_path)
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_job_ok_001", amount_minor_units=500)
    _append_budget_cost(repo_root=tmp_path, job_id="budget_job_ok_001", amount_minor_units=125)

    result = sched.execute_job(
        "budget_job_ok_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is True
    receipt = sched.load_receipt("budget_job_ok_001", result["run_id"], repo_root=tmp_path)
    assert receipt["budget_admission"]["enforced"] is True
    assert receipt["budget_admission"]["allowed"] is True
    assert receipt["budget_admission"]["snapshot"]["remaining_minor_units"] == 375
    assert receipt["budget_admission"]["snapshot"]["unit"] == "usd_cents"


def test_execute_job_rejects_launch_when_remaining_budget_is_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(_budgeted_job_spec("budget_job_zero_001"), repo_root=tmp_path)
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_job_zero_001", amount_minor_units=200)
    _append_budget_cost(repo_root=tmp_path, job_id="budget_job_zero_001", amount_minor_units=200)

    result = sched.execute_job(
        "budget_job_zero_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is False
    assert result["reason"] == "job_budget_exhausted"
    receipt = sched.load_receipt("budget_job_zero_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "blocked"
    assert receipt["reason"] == "job_budget_exhausted"
    assert receipt["budget_admission"]["allowed"] is False
    assert receipt["budget_admission"]["snapshot"]["remaining_minor_units"] == 0
    events = sched.list_execution_events(repo_root=tmp_path)
    assert [row["event"] for row in events] == ["blocked"]


def test_execute_job_rejects_launch_when_persisted_overspend_recovery_blocks_future_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(_budgeted_job_spec("budget_job_overspend_001"), repo_root=tmp_path)
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_job_overspend_001", amount_minor_units=100)
    _append_budget_cost(repo_root=tmp_path, job_id="budget_job_overspend_001", amount_minor_units=125)

    result = sched.execute_job(
        "budget_job_overspend_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is False
    assert result["reason"] == "job_budget_recovery_blocked"
    receipt = sched.load_receipt("budget_job_overspend_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "blocked"
    assert receipt["budget_admission"]["allowed"] is False
    assert receipt["budget_admission"]["recovery_outcome"]["status"] == "blocked"
    recovery_rows = budget_store.read_recovery_outcome_records("budget_job_overspend_001", repo_root=tmp_path)
    assert len(recovery_rows) == 1
    assert recovery_rows[0]["reason_code"] == "overspend_detected"


def test_execute_job_allows_launch_after_manual_override_recovery_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(_budgeted_job_spec("budget_job_override_001"), repo_root=tmp_path)
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_job_override_001", amount_minor_units=100)
    budget_store.append_recovery_outcome_record(
        {
            "schema_version": budget_store.RECOVERY_OUTCOME_SCHEMA_VERSION,
            "recovery_outcome_uid": "budget_job_override_001:manual_override:001",
            "job_uid": "budget_job_override_001",
            "recorded_at_utc": "2026-03-08T03:20:00Z",
            "actor": "operator",
            "reason_code": "manual_override",
            "follow_up_policy": "allow_future_runs",
            "message": "operator approved continued runs",
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "budget_job_override_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is True
    receipt = sched.load_receipt("budget_job_override_001", result["run_id"], repo_root=tmp_path)
    assert receipt["budget_admission"]["allowed"] is True
    assert receipt["budget_admission"]["recovery_outcome"]["status"] == "clear"
    assert receipt["budget_admission"]["recovery_outcome"]["reason"] == "manual_override"


def test_execute_job_rejects_launch_when_required_budget_ledger_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(_budgeted_job_spec("budget_job_bad_ledger_001"), repo_root=tmp_path)
    paths = budget_store.ledger_paths_for_job("budget_job_bad_ledger_001", repo_root=tmp_path)
    paths["allocation_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["allocation_log"].write_text("{not-json}\n", encoding="utf-8")

    result = sched.execute_job(
        "budget_job_bad_ledger_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is False
    assert result["reason"] == "job_budget_recovery_review_required"
    receipt = sched.load_receipt("budget_job_bad_ledger_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "blocked"
    assert receipt["budget_admission"]["allowed"] is False
    assert receipt["budget_admission"]["reason"] == "job_budget_recovery_review_required"
    assert receipt["budget_admission"]["recovery_outcome"]["status"] == "review_required"
    assert receipt["budget_admission"]["recovery_outcome"]["reason"] == "ledger_inconsistency_detected"
    assert "manual review" in receipt["budget_admission"]["message"]


def test_job_summary_surfaces_budget_state_for_clear_and_blocked_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    clear_job = sched.save_job(
        {
            "job_id": "budget_summary_clear_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Summarize budget state.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
        },
        repo_root=tmp_path,
    )
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_summary_clear_001", amount_minor_units=500)
    _append_budget_cost(repo_root=tmp_path, job_id="budget_summary_clear_001", amount_minor_units=125)

    clear_summary = sched.job_summary(
        clear_job,
        state=sched.load_state("budget_summary_clear_001", repo_root=tmp_path),
        repo_root=tmp_path,
    )

    assert clear_summary["budget_status"]["present"] is True
    assert clear_summary["budget_status"]["status"] == "clear"
    assert clear_summary["budget_status"]["remaining_minor_units"] == 375
    assert "Budget clear" in clear_summary["budget_status"]["operator_summary"]

    blocked_job = sched.save_job(
        {
            "job_id": "budget_summary_blocked_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Summarize blocked budget state.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
        },
        repo_root=tmp_path,
    )
    _append_budget_allocation(repo_root=tmp_path, job_id="budget_summary_blocked_001", amount_minor_units=100)
    _append_budget_cost(repo_root=tmp_path, job_id="budget_summary_blocked_001", amount_minor_units=125)
    budget_store.ensure_recovery_outcome_recorded(
        "budget_summary_blocked_001",
        recorded_at_utc="2026-03-08T04:00:00Z",
        repo_root=tmp_path,
    )

    blocked_summary = sched.job_summary(
        blocked_job,
        state=sched.load_state("budget_summary_blocked_001", repo_root=tmp_path),
        repo_root=tmp_path,
    )

    assert blocked_summary["budget_status"]["present"] is True
    assert blocked_summary["budget_status"]["status"] == "recovery_blocked"
    assert blocked_summary["budget_status"]["blocked"] is True
    assert "remaining budget dropped below zero" in blocked_summary["budget_status"]["operator_summary"]


def test_load_job_persists_legacy_incompatible_marker_for_active_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_shape_bad_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_shape_bad_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_arch_gov_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy shape bad job",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy shape bad job",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    loaded = sched.load_job("legacy_shape_bad_001", repo_root=tmp_path)

    assert loaded["job_shape"] == "verification_loop_job"
    assert loaded["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert loaded["job_shape_compatibility_mode"] is True
    assert loaded["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in loaded["job_shape_migration_reason"]
    raw = json.loads((inbox / "legacy_shape_bad_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in raw["job_shape_migration_reason"]


def test_load_job_persists_legacy_migration_required_marker_for_inactive_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_shape_inactive_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_shape_inactive_001",
                "enabled": False,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy shape inactive job",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy single-turn inactive job",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    loaded = sched.load_job("legacy_shape_inactive_001", repo_root=tmp_path)

    assert loaded["job_shape"] == "verification_loop_job"
    assert loaded["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert loaded["job_shape_compatibility_mode"] is True
    assert loaded["job_shape_migration_status"] == "legacy_migration_required"
    assert loaded["job_shape_migration_reason"] == "legacy record requires explicit job-shape backfill before launch"
    raw = json.loads((inbox / "legacy_shape_inactive_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape_migration_status"] == "legacy_migration_required"
    assert raw["job_shape_migration_reason"] == "legacy record requires explicit job-shape backfill before launch"


def test_save_job_defaults_required_context_bundle_from_job_shape_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    spec = sched.save_job(
        {
            "job_id": "default_bundle_shape_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Explain the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "runtime": {"executor_agent_tech": "codex"},
        }
    )

    assert spec["job_shape"] == "verification_loop_job"
    assert spec["required_context_bundle"] == "lab_core_v1"


def test_save_job_persists_explicit_job_shape_record_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    saved = sched.save_job(
        {
            "job_id": "persist_shape_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Explain the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"executor_agent_tech": "codex"},
        }
    )

    raw = json.loads((sched.jobs_dir(tmp_path) / "persist_shape_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape"] == "verification_loop_job"
    assert raw["job_shape_source"] == "explicit"
    assert raw["job_shape_compatibility_mode"] is False
    assert saved["job_shape"] == raw["job_shape"]
    assert saved["job_shape_source"] == raw["job_shape_source"]
    assert saved["job_shape_compatibility_mode"] is raw["job_shape_compatibility_mode"]


def test_save_job_persists_compatibility_shape_record_fields_when_legacy_job_is_rewritten(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_rewrite_shape_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_rewrite_shape_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4"},
                "display_title": "legacy shape rewrite job",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy single-turn job",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    legacy = sched.load_job("legacy_rewrite_shape_001", repo_root=tmp_path)
    saved = sched.save_job(legacy, repo_root=tmp_path)

    raw = json.loads((inbox / "legacy_rewrite_shape_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape"] == "verification_loop_job"
    assert raw["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert raw["job_shape_compatibility_mode"] is True
    assert raw["job_shape_migration_status"] == "legacy_pending_backfill"
    assert raw["job_shape_migration_reason"] == ""
    assert saved["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert saved["job_shape_compatibility_mode"] is True
    assert saved["job_shape_migration_status"] == "legacy_pending_backfill"


def test_backfill_legacy_job_shape_records_persists_explicit_shape_for_compatible_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_backfill_ok_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_backfill_ok_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy backfill ok",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy backfill ok",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    result = sched.backfill_legacy_job_shape_records(repo_root=tmp_path)

    assert result["backfilled_count"] == 1
    raw = json.loads((inbox / "legacy_backfill_ok_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape"] == "verification_loop_job"
    assert raw["job_shape_source"] == "backfilled_from_job_kind_contract"
    assert raw["job_shape_compatibility_mode"] is False
    assert raw["job_shape_migration_status"] == "legacy_backfilled"
    assert raw["job_shape_migration_reason"] == ""


def test_backfill_legacy_job_shape_records_marks_incompatible_and_due_status_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_backfill_bad_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_backfill_bad_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_arch_gov_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy backfill bad",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy backfill bad",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    result = sched.backfill_legacy_job_shape_records(repo_root=tmp_path)

    assert result["incompatible_count"] == 1
    loaded = sched.load_job("legacy_backfill_bad_001", repo_root=tmp_path)
    assert loaded["job_shape"] == "verification_loop_job"
    assert loaded["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in loaded["job_shape_migration_reason"]
    assert sched.due_status(loaded, repo_root=tmp_path)["reason"] == "legacy_job_shape_incompatible"


def test_backfill_legacy_job_shape_records_skips_inactive_records_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    legacy_path = inbox / "legacy_backfill_inactive_001.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_backfill_inactive_001",
                "enabled": False,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy backfill inactive",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy backfill inactive",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    result = sched.backfill_legacy_job_shape_records(repo_root=tmp_path)

    assert result["processed_count"] == 0
    raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert "job_shape" not in raw
    assert "job_shape_migration_status" not in raw


def test_backfill_legacy_job_shape_records_can_explicitly_include_inactive_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    legacy_path = inbox / "legacy_backfill_inactive_002.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_backfill_inactive_002",
                "enabled": False,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy backfill inactive selected",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy backfill inactive selected",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    result = sched.backfill_legacy_job_shape_records(
        repo_root=tmp_path,
        job_ids=["legacy_backfill_inactive_002"],
        active_only=False,
    )

    assert result["backfilled_count"] == 1
    raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert raw["job_shape"] == "verification_loop_job"
    assert raw["job_shape_source"] == "backfilled_from_job_kind_contract"
    assert raw["job_shape_compatibility_mode"] is False
    assert raw["job_shape_migration_status"] == "legacy_backfilled"
    assert raw["job_shape_migration_reason"] == ""


def test_list_jobs_persists_legacy_incompatible_marker_and_blocks_due_without_prior_load(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_list_bad_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_list_bad_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_arch_gov_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy list bad",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy list bad",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    listed = sched.list_jobs(repo_root=tmp_path, now_utc="2026-04-06T07:00:00Z")

    assert len(listed) == 1
    assert listed[0]["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in listed[0]["job_shape_migration_reason"]
    assert listed[0]["due"]["eligible"] is False
    assert listed[0]["due"]["reason"] == "legacy_job_shape_incompatible"

    raw = json.loads((inbox / "legacy_list_bad_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in raw["job_shape_migration_reason"]

    tick = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-04-06T07:00:00Z")
    assert tick["executed_count"] == 0
    assert any(
        row["job_id"] == "legacy_list_bad_001" and row["reason"] == "legacy_job_shape_incompatible"
        for row in tick["skipped"]
    )


def test_list_jobs_persists_legacy_migration_required_marker_and_blocks_due_without_prior_load(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "legacy_list_inactive_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "legacy_list_inactive_001",
                "enabled": False,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy list inactive",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy list inactive",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    listed = sched.list_jobs(repo_root=tmp_path, now_utc="2026-04-06T07:00:00Z")

    assert len(listed) == 1
    assert listed[0]["job_shape_migration_status"] == "legacy_migration_required"
    assert listed[0]["job_shape_migration_reason"] == "legacy record requires explicit job-shape backfill before launch"
    assert listed[0]["due"]["eligible"] is False
    assert listed[0]["due"]["reason"] == "legacy_job_shape_migration_required"

    raw = json.loads((inbox / "legacy_list_inactive_001.json").read_text(encoding="utf-8"))
    assert raw["job_shape_migration_status"] == "legacy_migration_required"
    assert raw["job_shape_migration_reason"] == "legacy record requires explicit job-shape backfill before launch"


def test_job_summary_projects_shape_lifecycle_for_active_loop_and_terminal_project_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    loop_job = {
        "job_id": "shape_summary_loop_001",
        "job_kind": "build_verify_loop",
        "job_shape": "verification_loop_job",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mb3"},
        "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
        "created_utc": "2026-04-06T06:00:00Z",
        "updated_utc": "2026-04-06T06:00:00Z",
    }
    sched._write_state(
        "shape_summary_loop_001",
        {"active_run_id": "sj_loop", "last_status": "running", "active_loop_stage": "verifier"},
        repo_root=tmp_path,
    )
    loop_summary = sched.job_summary(loop_job, state=sched.load_state("shape_summary_loop_001", repo_root=tmp_path), repo_root=tmp_path)
    assert loop_summary["lifecycle_projection"]["canonical_state"] == "awaiting_verifier"
    assert loop_summary["lifecycle_projection"]["summary_bucket"] == "active_progress"
    assert loop_summary["job_shape_context"]["required_context_bundle"] == "lab_core_v1"
    assert loop_summary["job_shape_context"]["reentry_precedence"] == "durable_contract_then_durable_delta_then_transcript_summary"
    assert loop_summary["job_shape_summary"]["progress_semantics"] == "bounded builder/verifier iteration state"
    assert loop_summary["job_shape_summary"]["child_launch_semantics"] == "no child launches in v1"

    project_job = {
        "job_id": "shape_summary_project_001",
        "job_kind": "project_runner",
        "job_shape": "experiment_run_job",
        "enabled": True,
        "binding": {"type": "none", "id": ""},
        "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
        "project_run": {},
        "created_utc": "2026-04-06T06:00:00Z",
        "updated_utc": "2026-04-06T06:00:00Z",
    }
    sched._write_state(
        "shape_summary_project_001",
        {"last_status": "blocked", "last_finished_utc": "2026-04-06T06:31:00Z"},
        repo_root=tmp_path,
    )
    project_summary = sched.job_summary(project_job, state=sched.load_state("shape_summary_project_001", repo_root=tmp_path), repo_root=tmp_path)
    assert project_summary["lifecycle_projection"]["canonical_state"] == "blocked"
    assert project_summary["lifecycle_projection"]["terminal_summary_class"] == "operator_actionable_terminal"
    assert project_summary["job_shape_summary"]["recovery_semantics"].startswith("Resume from durable checkpoint truth")


def test_job_summary_includes_scheduler_projection_for_dependency_blocked_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "scheduler_projection_dep_parent_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "dep-parent"},
            "prompt": "Dependency parent.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T06:00:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "scheduler_projection_dep_child_001",
            "enabled": True,
            "role": "generalist",
            "depends_on": ["scheduler_projection_dep_parent_001"],
            "binding": {"type": "task_id", "id": "dep-child"},
            "prompt": "Dependency child.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T06:01:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched._write_state("scheduler_projection_dep_parent_001", {"last_status": "failed"}, repo_root=tmp_path)

    listed = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-04-08T06:05:00Z")}
    projection = listed["scheduler_projection_dep_child_001"]["scheduler_projection"]
    projection_v1 = listed["scheduler_projection_dep_child_001"]["scheduler_projection_v1"]

    assert projection["classification"] == "blocked"
    assert projection["dependency"]["blocked"] is True
    assert projection["dependency"]["blocking_job_id"] == "scheduler_projection_dep_parent_001"
    assert projection["ordering"]["priority_bucket"] == "blocked"
    assert projection["ordering"]["executable_now"] is False
    assert projection_v1["projected_status"] == "blocked"
    assert projection_v1["projection_reason"].startswith("dependency_")
    assert projection_v1["next_candidate_bucket"] == "none"
    assert any(row["source_path"] == "due.reason" for row in projection_v1["provenance"])


def test_job_summary_scheduler_projection_marks_conflicting_active_and_terminal_truth_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    spec = {
        "job_id": "scheduler_projection_conflict_001",
        "job_kind": "single_turn",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "dep-conflict"},
        "prompt": "Conflicting active and terminal truth.",
        "schedule": {"mode": "once", "run_at": "2026-04-08T06:00:00Z", "timezone": "UTC"},
    }
    summary = sched.job_summary(
        spec,
        state={
            "active_run_id": "sj_conflict_001",
            "active_claim_present": False,
            "active_claim_stale": False,
            "last_status": "ok",
            "last_started_utc": "2026-04-08T05:59:00Z",
        },
        due={"eligible": False, "reason": "already_executed", "slot_key": "2026-04-08T06:00:00Z", "due_utc": "2026-04-08T06:00:00Z"},
        repo_root=tmp_path,
        now=sched._parse_utc("2026-04-08T06:05:00Z"),
    )

    projection = summary["scheduler_projection"]
    assert projection["classification"] == "ambiguous"
    assert projection["ambiguous"] is True
    assert projection["freshness"]["degraded"] is True
    assert any(item["code"] == "active_claim_untrusted" for item in projection["provenance"]["conflicts"])


def test_job_summary_scheduler_projection_keeps_run_now_ahead_of_completed_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    summary = sched.job_summary(
        {
            "job_id": "scheduler_projection_run_now_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "dep-run-now"},
            "prompt": "Run-now should reopen a once job.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T06:00:00Z", "timezone": "UTC"},
        },
        state={
            "last_status": "ok",
            "run_now_requested_utc": "2026-04-08T06:04:00Z",
            "last_slot_key": "2026-04-08T06:00:00Z",
        },
        due={"eligible": True, "reason": "run_now_requested", "slot_key": "manual:2026-04-08T06:04:00Z", "due_utc": "2026-04-08T06:04:00Z"},
        repo_root=tmp_path,
        now=sched._parse_utc("2026-04-08T06:05:00Z"),
    )

    projection = summary["scheduler_projection"]
    assert projection["classification"] == "queued_run_now"
    assert projection["queued_run_now"] is True
    assert projection["completed_once"] is False
    assert projection["ordering"]["priority_bucket"] == "queued_run_now"


def test_scheduler_projection_priority_keeps_ready_job_ahead_of_earlier_blocked_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "scheduler_projection_ready_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "dep-ready"},
            "prompt": "Ready job.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T06:10:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "scheduler_projection_blocked_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "dep-blocked"},
            "prompt": "Blocked job.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T06:00:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "scheduler_projection_blocked_001",
        {"last_status": "blocked", "last_nonlaunch_status": "blocked", "last_nonlaunch_reason": "repo_guard_dirty_worktree"},
        repo_root=tmp_path,
    )

    listed = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-04-08T06:15:00Z")}
    ready_projection = listed["scheduler_projection_ready_001"]["scheduler_projection"]
    blocked_projection = listed["scheduler_projection_blocked_001"]["scheduler_projection"]

    assert ready_projection["classification"] == "ready"
    assert blocked_projection["classification"] == "blocked"
    assert ready_projection["ordering"]["priority_rank"] < blocked_projection["ordering"]["priority_rank"]


def test_receipt_state_projection_persists_shape_lifecycle_summary() -> None:
    projection = sched._receipt_state_projection(
        {
            "status": "blocked",
            "lifecycle": {
                "status": "blocked",
                "cancel": {"requested": False, "requested_utc": "", "result": ""},
                "timeout": {"expired": False},
                "heartbeat": {"claim_reason": "not_launched"},
            },
            "job_shape_lifecycle": {
                "canonical_state": "blocked",
                "summary_bucket": "terminal_operator_actionable",
                "terminal_summary_class": "operator_actionable_terminal",
                "recovery_class": "resume_from_durable_checkpoint_if_present",
                "recognized": True,
            },
        }
    )
    assert projection["last_shape_canonical_state"] == "blocked"
    assert projection["last_shape_summary_bucket"] == "terminal_operator_actionable"
    assert projection["last_shape_terminal_summary_class"] == "operator_actionable_terminal"


def test_build_loop_builder_message_uses_job_shape_context_contract_for_reentry_fields(tmp_path: Path) -> None:
    message = sched._build_loop_builder_message(
        {
            "job_id": "context_reentry_001",
            "job_kind": "build_verify_loop",
            "job_shape": "verification_loop_job",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement the change.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"repo_change_mode": "strict"},
        },
        run_id="sj_ctx",
        trigger="scheduler",
        iteration=1,
        max_iterations=2,
        repo_root=tmp_path,
    )

    assert "required_context_bundle: lab_core_v1" in message
    assert "context_reentry_precedence: durable_contract_then_durable_delta_then_transcript_summary" in message
    assert "context_transcript_summary_policy: ignored_for_context_resolution" in message
    assert "- job_shape: verification_loop_job" in message
    assert "job_shape_progress_semantics: bounded builder/verifier iteration state" in message
    assert "job_shape_child_launch_semantics: no child launches in v1" in message


def test_job_shape_semantics_projection_reports_missing_shape_explicitly() -> None:
    summary = sched._job_shape_semantics_projection({}, raw_state="blocked")

    assert summary["recognized"] is False
    assert summary["job_shape"] == ""
    assert summary["operator_summary"] == "job shape missing or invalid; scheduler summaries must not guess from prose"
    assert summary["progress_semantics"] == "unknown until durable job_shape is present"
    assert "missing job_kind and persisted job_shape" in summary["error"]


def test_decision_report_uses_explicit_missing_shape_summary_instead_of_guessing() -> None:
    report = sched._decision_report_markdown(
        spec={
            "job_id": "missing_shape_report_001",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Explain the issue.",
        },
        receipt={
            "job_id": "missing_shape_report_001",
            "run_id": "run-1",
            "trigger": "manual",
            "status": "blocked",
            "reason": "not_enough_truth",
            "due": {"reason": "due", "due_utc": "2026-04-06T06:30:00Z", "slot_key": "once"},
        },
    )

    assert "- job_shape_summary: job shape missing or invalid; scheduler summaries must not guess from prose" in report


def test_validate_job_spec_rejects_unknown_or_mismatched_job_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="job_shape must be one of"):
        sched.validate_job_spec(
            {
                "job_id": "bad_shape_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "mystery_job",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
            },
            repo_root=tmp_path,
        )

    with pytest.raises(ValueError, match="does not match expected shape"):
        sched.validate_job_spec(
            {
                "job_id": "mismatch_shape_001",
                "enabled": True,
                "job_kind": "project_runner",
                "job_shape": "verification_loop_job",
                "binding": {"type": "task_id", "id": "mb3"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "project_run": {
                    "schema_version": "canonical_experiment_run_v1",
                    "project_id": "proj",
                    "runner_id": "runner",
                    "run_command": ["echo", "ok"],
                },
            },
            repo_root=tmp_path,
        )


def test_validate_job_spec_rejects_legacy_compatibility_markers_for_new_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="compatibility mode is reserved for legacy records"):
        sched.validate_job_spec(
            {
                "job_id": "compat_shape_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "compatibility_inferred_from_job_kind",
                "job_shape_compatibility_mode": True,
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
        )

    with pytest.raises(ValueError, match="migration status is reserved for legacy records"):
        sched.validate_job_spec(
            {
                "job_id": "compat_shape_002",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
                "job_shape_migration_status": "legacy_pending_backfill",
            },
            repo_root=tmp_path,
        )


def test_validate_job_spec_rejects_unknown_job_shape_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="job_shape_source must be one of"):
        sched.validate_job_spec(
            {
                "job_id": "shape_source_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "made_up_source",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
        )


def test_validate_job_spec_rejects_incoherent_legacy_marker_combinations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(
        ValueError,
        match="job_shape compatibility records must persist both compatibility source and compatibility_mode=true",
    ):
        sched.validate_job_spec(
            {
                "job_id": "legacy_marker_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "explicit",
                "job_shape_compatibility_mode": True,
                "job_shape_migration_status": "legacy_pending_backfill",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
            allow_legacy_job_shape_compatibility=True,
        )

    with pytest.raises(
        ValueError,
        match="job_shape backfilled records must use migration status legacy_backfilled",
    ):
        sched.validate_job_spec(
            {
                "job_id": "legacy_marker_002",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "backfilled_from_job_kind_contract",
                "job_shape_compatibility_mode": False,
                "job_shape_migration_status": "legacy_pending_backfill",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
            allow_legacy_job_shape_compatibility=True,
        )


def test_validate_job_spec_rejects_missing_required_migration_reason_for_legacy_blockers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(
        ValueError,
        match="job_shape migration status legacy_incompatible requires a non-empty job_shape_migration_reason",
    ):
        sched.validate_job_spec(
            {
                "job_id": "legacy_marker_reason_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "compatibility_inferred_from_job_kind",
                "job_shape_compatibility_mode": True,
                "job_shape_migration_status": "legacy_incompatible",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
            allow_legacy_job_shape_compatibility=True,
        )


def test_validate_job_spec_rejects_migration_reason_for_explicit_or_backfilled_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="explicit job_shape records may not persist a migration reason"):
        sched.validate_job_spec(
            {
                "job_id": "explicit_reason_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "explicit",
                "job_shape_migration_status": "explicit",
                "job_shape_migration_reason": "should not be here",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
        )

    with pytest.raises(ValueError, match="job_shape backfilled records may not persist a migration reason"):
        sched.validate_job_spec(
            {
                "job_id": "backfilled_reason_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "backfilled_from_job_kind_contract",
                "job_shape_migration_status": "legacy_backfilled",
                "job_shape_migration_reason": "backfill succeeded but left a note",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
            allow_legacy_job_shape_compatibility=True,
        )

def test_resolve_job_shape_fields_supports_nested_work_order_metadata() -> None:
    fields = sched._resolve_job_shape_fields(
        {
            "work_order": {
                "metadata": {
                    "job_kind": "build_verify_loop",
                    "job_shape": "verification_loop_job",
                    "job_shape_source": "explicit",
                    "job_shape_compatibility_mode": False,
                }
            }
        }
    )

    assert fields["job_shape"] == "verification_loop_job"
    assert fields["job_shape_source"] == "explicit"
    assert fields["job_shape_compatibility_mode"] is False


def test_resolve_job_shape_fields_supports_nested_compiled_execution_metadata() -> None:
    fields = sched._resolve_job_shape_fields(
        {
            "compiled_execution": {
                "ir": {
                    "normalized_ir": {
                        "metadata": {
                            "job_kind": "build_verify_loop",
                            "job_shape": "verification_loop_job",
                            "job_shape_source": "explicit",
                            "job_shape_compatibility_mode": False,
                        }
                    }
                }
            }
        }
    )

    assert fields["job_shape"] == "verification_loop_job"
    assert fields["job_shape_source"] == "explicit"
    assert fields["job_shape_compatibility_mode"] is False


def test_execute_job_claim_persists_explicit_job_shape_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "claim_shape_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Explain the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
        },
        repo_root=tmp_path,
    )

    release_seen: dict[str, Any] = {}
    original_release = sched._release_job_claim

    def _capturing_release(
        job_id: str,
        *,
        repo_root: Path | None = None,
        run_id: str = "",
        terminal_status: str = "",
        detail: str = "",
    ) -> dict[str, Any]:
        claim_path = sched._claim_path(job_id, repo_root=repo_root)
        if claim_path.exists():
            release_seen["claim"] = json.loads(claim_path.read_text(encoding="utf-8"))
        return original_release(
            job_id,
            repo_root=repo_root,
            run_id=run_id,
            terminal_status=terminal_status,
            detail=detail,
        )

    monkeypatch.setattr(sched, "_release_job_claim", _capturing_release)

    result = sched.execute_job(
        "claim_shape_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-06T06:31:00Z",
    )

    assert result["ok"] is True
    receipt = sched.load_receipt("claim_shape_001", result["run_id"], repo_root=tmp_path)
    assert receipt["resource_claims"]["claimant_uid"] == "scheduled_job:claim_shape_001"
    assert receipt["resource_claims"]["run_id"] == result["run_id"]
    assert receipt["resource_claims"]["release_error"] == ""
    assert [row["terminal_status"] for row in receipt["resource_claims"]["claims"]] == ["released"]
    claim = release_seen["claim"]
    assert claim["job_kind"] == "single_turn"
    assert claim["job_shape"] == "verification_loop_job"
    assert claim["job_shape_source"] == "explicit"
    assert claim["job_shape_compatibility_mode"] is False
    lineage = resource_claim_ledger.read_claim_lineage(
        sched._scheduled_job_runtime_claim_uid("claim_shape_001", result["run_id"]),
        repo_root=tmp_path,
    )
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert lineage[0]["resource"]["canonical_key"] == "job_family:scheduler:scheduled_job_runtime"
    assert lineage[-1]["detail"].startswith("runtime claim terminated for scheduled job run")


def test_scheduled_job_runtime_claim_release_records_error_termination(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _always_timeout(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        raise RuntimeError("service unavailable timeout")

    monkeypatch.setattr(sched._agents, "send_message", _always_timeout)

    sched.save_job(
        {
            "job_id": "runtime_claim_error_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 900,
                "agent_turn_retries": 0,
                "job_transient_retries": 0,
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "runtime_claim_error_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("runtime_claim_error_001", result["run_id"], repo_root=tmp_path)
    assert receipt["resource_claims"]["claimant_uid"] == "scheduled_job:runtime_claim_error_001"
    assert receipt["resource_claims"]["release_error"] == ""
    assert [row["terminal_status"] for row in receipt["resource_claims"]["claims"]] == ["released"]
    lineage = resource_claim_ledger.read_claim_lineage(
        sched._scheduled_job_runtime_claim_uid("runtime_claim_error_001", result["run_id"]),
        repo_root=tmp_path,
    )
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert "job_status=error" in lineage[-1]["detail"]
    assert "service unavailable timeout" in lineage[-1]["detail"]


def test_scheduled_job_runtime_claim_release_helper_rejects_out_of_order_and_deduplicates_duplicate_release(
    tmp_path: Path,
) -> None:
    try:
        sched._record_scheduled_job_runtime_claim_release(
            job_id="runtime_claim_helper_001",
            run_id="sj_out_of_order",
            terminal_status="ok",
            recorded_at_utc="2026-04-07T05:55:02Z",
            repo_root=tmp_path,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected release without acquisition to fail closed")
    assert "prior acquired state" in message

    claim = sched._record_scheduled_job_runtime_claim_acquired(
        job_id="runtime_claim_helper_001",
        run_id="sj_dup_release",
        trigger="scheduler",
        claimed_at_utc="2026-04-07T05:55:00Z",
        claim_row={"job_kind": "single_turn"},
        repo_root=tmp_path,
    )
    first = sched._record_scheduled_job_runtime_claim_release(
        job_id="runtime_claim_helper_001",
        run_id="sj_dup_release",
        terminal_status="ok",
        recorded_at_utc="2026-04-07T05:55:01Z",
        repo_root=tmp_path,
    )
    second = sched._record_scheduled_job_runtime_claim_release(
        job_id="runtime_claim_helper_001",
        run_id="sj_dup_release",
        terminal_status="ok",
        recorded_at_utc="2026-04-07T05:55:01Z",
        repo_root=tmp_path,
    )

    assert claim["claim_uid"] == first["claim_uid"] == second["claim_uid"]
    lineage = resource_claim_ledger.read_claim_lineage(claim["claim_uid"], repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]


def test_scheduled_job_runtime_claim_release_helper_clamps_earlier_release_timestamp(
    tmp_path: Path,
) -> None:
    job_id = "runtime_claim_clamp_001"
    run_id = "sj_20260414T203500Z_0904b8"
    sched._record_scheduled_job_runtime_claim_acquired(
        job_id=job_id,
        run_id=run_id,
        trigger="manual",
        claimed_at_utc="2026-04-14T20:35:00Z",
        claim_row={"job_kind": "project_runner"},
        repo_root=tmp_path,
    )

    released = sched._record_scheduled_job_runtime_claim_release(
        job_id=job_id,
        run_id=run_id,
        terminal_status="ok",
        recorded_at_utc="2026-04-14T20:34:41Z",
        detail="prelaunch terminal claim reconciliation",
        repo_root=tmp_path,
    )

    lineage = resource_claim_ledger.read_claim_lineage(
        sched._scheduled_job_runtime_claim_uid(job_id, run_id),
        repo_root=tmp_path,
    )
    assert released["status"] == "released"
    assert released["recorded_at_utc"] == "2026-04-14T20:35:00Z"
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert lineage[-1]["recorded_at_utc"] == "2026-04-14T20:35:00Z"


def test_pipeline_backend_raw_request_persists_explicit_job_shape_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    captured: dict[str, Any] = {}

    def _fake_run_pipeline(
        repo_root: str,
        backend_job_id: str,
        branch_id: str,
        stop_after_stage: str,
        config_overrides: dict,
    ) -> dict:
        job_dir = sched.pipeline_backend_dir(tmp_path) / "jobs" / backend_job_id
        captured["raw_request"] = json.loads((job_dir / "input" / "00_raw_request.json").read_text(encoding="utf-8"))
        run_dir = job_dir / "runs" / "RUN-PIPE-CLAIM"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "07_patch_plan.json").write_text(json.dumps({"summary": {"status": "ok"}}), encoding="utf-8")
        (run_dir / "08_patch_artifact.json").write_text(
            json.dumps({"result": {"artifact_kind": "none", "model_response": {"selected_model": "pipeline:model"}}}),
            encoding="utf-8",
        )
        return {"run_id": "RUN-PIPE-CLAIM", "status": "ok"}

    monkeypatch.setattr("infra.pipeline.pipeline_runner.run_pipeline", _fake_run_pipeline)

    spec = sched.validate_job_spec(
        {
            "job_id": "pipeline_backend_shape_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write an advisory summary.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"agent_tech": "pipeline", "model": "pipeline:model"},
        },
        repo_root=tmp_path,
    )

    result = sched._pipeline_backend_send(
        spec=spec,
        role="generalist",
        binding={"type": "task_id", "id": "mb3"},
        message="Advisory request",
        options={},
        runtime={"agent_tech": "pipeline", "model": "pipeline:model"},
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    metadata = captured["raw_request"]["metadata"]
    assert metadata["scheduler_job_id"] == "pipeline_backend_shape_001"
    assert metadata["job_kind"] == "single_turn"
    assert metadata["job_shape"] == "verification_loop_job"
    assert metadata["job_shape_source"] == "explicit"
    assert metadata["job_shape_compatibility_mode"] is False


def test_resolve_job_shape_fields_marks_nested_job_kind_only_records_as_compatibility_mode() -> None:
    fields = sched._resolve_job_shape_fields(
        {
            "raw_request": {
                "metadata": {
                    "job_kind": "project_runner",
                }
            }
        }
    )

    assert fields["job_shape"] == "experiment_run_job"
    assert fields["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert fields["job_shape_compatibility_mode"] is True


def test_resolve_job_shape_fields_rejects_unknown_nested_shape_value() -> None:
    with pytest.raises(ValueError, match="job_shape must be one of"):
        sched._resolve_job_shape_fields(
            {
                "compiled_execution": {
                    "ir": {
                        "normalized_ir": {
                            "metadata": {
                                "job_kind": "build_verify_loop",
                                "job_shape": "mystery_job",
                            }
                        }
                    }
                }
            }
        )


def test_validate_job_spec_rejects_missing_required_bundle_for_job_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="job_shape verification_loop_job requires required_context_bundle=lab_core_v1"):
        sched.validate_job_spec(
            {
                "job_id": "missing_bundle_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
            },
            repo_root=tmp_path,
        )


def test_save_job_rejects_backfill_markers_for_nonlegacy_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="backfill source is reserved for the legacy backfill path"):
        sched.save_job(
            {
                "job_id": "nonlegacy_backfill_source_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "job_shape_source": "backfilled_from_job_kind_contract",
                "job_shape_migration_status": "legacy_backfilled",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
            },
            repo_root=tmp_path,
        )


def test_validate_job_spec_rejects_forbidden_durable_context_ref_for_job_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="forbidden context refs requested for verification_loop_job"):
        sched.validate_job_spec(
            {
                "job_id": "forbidden_delta_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
                "additional_context_refs": ["governance/agents/sessions/task_123/session__builder.jsonl"],
            },
            repo_root=tmp_path,
        )


def test_validate_job_spec_rejects_forbidden_context_refs_that_remove_required_bundle_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="forbidden_context_refs may not remove required bundle refs"):
        sched.validate_job_spec(
            {
                "job_id": "forbidden_bundle_ref_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement the change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "required_context_bundle": "lab_core_v1",
                "forbidden_context_refs": ["docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md"],
            },
            repo_root=tmp_path,
        )


def test_effective_context_refs_use_job_shape_contract_bundle_before_delta_refs() -> None:
    refs = sched._effective_context_refs(
        {
            "job_kind": "single_turn",
            "job_shape": "verification_loop_job",
            "required_context_bundle": "",
            "context_refs": ["governance/ledger/patch_instruction_sheets/example/pis.md"],
            "additional_context_refs": ["docs/extra.md"],
            "forbidden_context_refs": [],
        }
    )

    assert refs[:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert refs[-2:] == [
        "governance/ledger/patch_instruction_sheets/example/pis.md",
        "docs/extra.md",
    ]


def test_single_turn_ledger_bound_launch_seeds_owner_task_before_scheduler_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _init_ledger_bound_codex_repo(tmp_path)
    _patch_real_server_agents_codex_runtime(tmp_path, monkeypatch)

    sched.save_job(
        {
            "job_id": "k2n7_single_turn_seed_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-173634Z-K2N7"},
            "prompt": "Read the bound ledger item UID and reply with exactly one line: K2N7_CANONICAL_OK PIS-20260327-173634Z-K2N7",
            "context_refs": [
                "governance/ledger/patch_instruction_sheets/PIS-20260327-173634Z-K2N7__autonomous-codex-platform-exercise-and-repair-campaign/pis.md"
            ],
            "required_context_bundle": "lab_core_v1",
            "schedule": {"mode": "once", "run_at": "2026-03-28T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "smoke_signal", "format": "text"},
            "policy": {"max_runtime_seconds": 180},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.4-mini", "execution_mode": "controller_only"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "k2n7_single_turn_seed_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-28T16:20:00Z",
    )
    assert result["ok"] is True

    receipt = sched.load_receipt("k2n7_single_turn_seed_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["binding_result"]["ok"] is True
    assert receipt["assistant"]["content"] == "K2N7_CANONICAL_OK PIS-20260327-173634Z-K2N7"

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    tasks = [row for row in snapshot["active_tasks"] if row.get("ledger_uid") == "PIS-20260327-173634Z-K2N7"]
    assert len(tasks) == 1
    task = tasks[0]
    assert any(ref.get("surface") == "scheduler" for ref in task.get("delegation_refs") or [])
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "codex_worker"
    assert ownership.get("reference_status") == "execution_ref_active"


def test_build_verify_loop_ledger_bound_launch_seeds_owner_task_before_scheduler_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _init_ledger_bound_codex_repo(tmp_path)

    def _fake_controlled_turn(*, role: str, binding: dict, message: str, **_kwargs):
        assert binding.get("task_id")
        assert binding.get("ledger_uid") == "PIS-20260327-173634Z-K2N7"
        if role == "builder":
            content = (
                "Changed Files\n"
                "- infra/controlplane/scheduled_jobs.py\n\n"
                "Validation Commands Run\n"
                "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
            )
        else:
            content = (
                "VERDICT: PASS\n"
                "Findings:\n"
                "- none\n\n"
                "Boundary Review:\n"
                "- scope_alignment: pass\n"
                "- contract_alignment: pass\n\n"
                "Proof Review:\n"
                "- evidence_status: sufficient\n"
                "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                "Validation Review:\n"
                "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                "- outcome_support: sufficient\n"
            )
        result = {"ok": True, "assistant": {"content": content, "status": "ok", "meta": {}}}
        send_meta = {"selected_model": "test-model", "attempts": []}
        advisory_result = {"ok": False, "assistant": {"content": "", "status": "", "meta": {}}}
        advisory_send = {"selected_model": "", "attempts": []}
        return result, send_meta, advisory_result, advisory_send

    monkeypatch.setattr(sched, "_execute_scheduled_controlled_turn", _fake_controlled_turn)

    sched.save_job(
        {
            "job_id": "k2n7_build_verify_seed_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-173634Z-K2N7"},
            "prompt": "Implement the bound ledger item and verify it.",
            "context_refs": [
                "governance/ledger/patch_instruction_sheets/PIS-20260327-173634Z-K2N7__autonomous-codex-platform-exercise-and-repair-campaign/pis.md"
            ],
            "required_context_bundle": "lab_core_v1",
            "schedule": {"mode": "once", "run_at": "2026-03-28T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"max_runtime_seconds": 180, "post_success_commit_tag": False},
            "runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4", "reasoning_effort": "medium"},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 2,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4", "execution_mode": "controller_only"},
                "verifier_runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4", "execution_mode": "controller_only"},
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "k2n7_build_verify_seed_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-28T16:20:00Z",
    )
    assert result["ok"] is True

    receipt = sched.load_receipt("k2n7_build_verify_seed_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["binding_result"]["ok"] is True

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    tasks = [row for row in snapshot["active_tasks"] if row.get("ledger_uid") == "PIS-20260327-173634Z-K2N7"]
    assert len(tasks) == 1
    assert tasks[0].get("task_id")


def test_write_active_loop_state_persists_iteration_and_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state(
        "loop_job_001",
        {
            "active_run_id": "sj_live_001",
            "active_started_utc": "2026-03-27T09:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    updated = sched._write_active_loop_state(
        "loop_job_001",
        run_id="sj_live_001",
        iteration=2,
        stage="verifier",
        completed_iterations=1,
        max_iterations=4,
        repo_root=tmp_path,
    )

    assert updated["active_loop_iteration"] == 2
    assert updated["active_loop_stage"] == "verifier"
    assert updated["active_loop_completed_iterations"] == 1
    assert updated["active_loop_max_iterations"] == 4
    saved = sched.load_state("loop_job_001", repo_root=tmp_path)
    assert saved["active_loop_iteration"] == 2


def test_scheduled_job_daily_runs_once_per_day(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "daily_builder_001",
            "enabled": True,
            "created_utc": "2026-03-07T00:00:00Z",
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbu"},
            "prompt": "Generate a status report.",
            "schedule": {"mode": "daily", "time_of_day": "03:30", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    first = sched.scheduler_tick(now_utc="2026-03-08T04:00:00Z", repo_root=tmp_path)
    second = sched.scheduler_tick(now_utc="2026-03-08T05:00:00Z", repo_root=tmp_path)
    third = sched.scheduler_tick(now_utc="2026-03-09T04:00:00Z", repo_root=tmp_path)

    assert first["executed_count"] == 1
    assert second["executed_count"] == 0
    assert third["executed_count"] == 1

    state = sched.load_state("daily_builder_001", repo_root=tmp_path)
    assert state["last_slot_key"] == "2026-03-09"

    receipts = sched.list_receipts("daily_builder_001", limit=10, repo_root=tmp_path)
    assert len(receipts) == 2
    assert all(r["status"] == "ok" for r in receipts)


def test_scheduled_job_dispatch_message_includes_context_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    captured: dict[str, object] = {}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        captured["role"] = role
        captured["binding"] = dict(binding)
        captured["message"] = message
        captured["options"] = dict(options or {})
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)

    sched.save_job(
        {
            "job_id": "nightly_generalist_context_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Review the latest summaries and write a memo.",
            "context_refs": ["docs/reports/latest.md", "governance/ledger/patch_instruction_sheets/PIS-123/artifacts/"],
            "expected_output": {"kind": "decision_memo", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
        },
        repo_root=tmp_path,
    )

    result = sched.scheduler_tick(now_utc="2026-03-08T04:00:00Z", repo_root=tmp_path)
    assert result["executed_count"] == 1
    message = str(captured["message"])
    assert "[Scheduled Job]" in message
    assert "expected_output: kind=decision_memo format=markdown" in message
    assert "- docs/reports/latest.md" in message
    assert "- governance/ledger/patch_instruction_sheets/PIS-123/artifacts/" in message
    assert str((captured["options"] or {}).get("timeout_s")) == "900"


def test_scheduled_job_runtime_model_and_effort_are_passed_to_agent_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    captured: dict[str, object] = {}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        captured["role"] = role
        captured["binding"] = dict(binding)
        captured["message"] = message
        captured["options"] = dict(options or {})
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)

    sched.save_job(
        {
            "job_id": "nightly_model_control_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
        }
    )

    result = sched.execute_job("nightly_model_control_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True

    options = dict(captured.get("options") or {})
    assert options.get("timeout_s") == 900
    assert options.get("model") == "gpt-5.3-codex"
    assert options.get("reasoning_effort") == "medium"

    receipt = sched.load_receipt("nightly_model_control_001", result["run_id"], repo_root=tmp_path)
    assert receipt["runtime"]["model"] == "gpt-5.3-codex"
    assert receipt["runtime"]["reasoning_effort"] == "medium"


def test_scheduled_job_non_codex_backend_runs_through_codex_controller(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        payload = {"role": role, "message": message, "options": dict(options or {})}
        calls.append(payload)
        agent_tech = str((options or {}).get("agent_tech") or "")
        if agent_tech == "ollama":
            return {"ok": True, "assistant": {"content": "advisory ollama draft", "status": "ok", "meta": {"backend": "ollama"}}}
        return {"ok": True, "assistant": {"content": "codex final", "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send)
    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send)
    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_codex_controller_ollama_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "ollama", "model": "qwen2.5:3b"},
        }
    )

    result = sched.execute_job("nightly_codex_controller_ollama_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert [str((row["options"] or {}).get("agent_tech") or "") for row in calls] == ["ollama", "codex"]
    assert "[Scheduled Advisory Backend Request]" in str(calls[0]["message"])
    assert "required_schema:" in str(calls[0]["message"])
    assert "Advisory Backend Result JSON" in str(calls[1]["message"])
    receipt = sched.load_receipt("nightly_codex_controller_ollama_001", result["run_id"], repo_root=tmp_path)
    assert receipt["execution_contract"]["controller_runtime"]["agent_tech"] == "codex"
    assert receipt["execution_contract"]["advisory_runtime"]["agent_tech"] == "ollama"
    assert receipt["model_telemetry"]["executor_agent_tech"] == "codex"
    assert receipt["model_telemetry"]["advisory_agent_tech"] == "ollama"
    assert receipt["model_telemetry"]["advisory_model"] == "qwen2.5:3b"


def test_scheduled_job_binds_execution_surface_ownership_on_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="mb3", ledger_uid="PIS-20260327-132825Z-3E6D")

    sched.save_job(
        {
            "job_id": "binding_launch_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Run the scheduler binding test.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    result = sched.execute_job("binding_launch_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True

    register_state = register.load_or_init_task_register(tmp_path)
    task = next(row for row in register_state.get("active_tasks", []) if row.get("task_id") == "mb3")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "scheduler"
    assert ownership.get("reference_status") == "execution_ref_active"


def test_scheduled_job_launch_binding_failure_blocks_before_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    called = {"executed": False}

    def _unexpected_execute(*args, **kwargs):
        called["executed"] = True
        raise AssertionError("job execution must not start when launch binding fails")

    monkeypatch.setattr(sched, "_execute_single_turn_job", _unexpected_execute)
    monkeypatch.setattr(
        sched,
        "_bind_scheduler_launch",
        lambda **_kwargs: {"ok": False, "reason": "binding_launch_failed", "error": "owner_ref unresolved"},
    )

    sched.save_job(
        {
            "job_id": "binding_launch_fail_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "missing_task"},
            "prompt": "This should never execute.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    result = sched.execute_job("binding_launch_fail_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")

    assert result["ok"] is False
    assert result["skipped"] is True
    assert "binding_launch_failed" in str(result["reason"] or "")
    assert called["executed"] is False

    state = sched.load_state("binding_launch_fail_001", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "blocked"
    assert state["last_nonlaunch_status"] == "blocked"

    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked"
    assert "binding_launch_failed" in str(receipt["reason"] or "")
    assert receipt["ownership"]["handoff_state"] == "not_launched"
    assert receipt["binding_result"]["ok"] is False
    ledger_rows = sched.list_execution_events(repo_root=tmp_path)
    assert not any(
        row["job_id"] == "binding_launch_fail_001" and row["run_id"] == result["run_id"] and row["event"] == "started"
        for row in ledger_rows
    )


def test_scheduled_job_binding_launch_repeat_guard_blocks_unchanged_retries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched,
        "_bind_scheduler_launch",
        lambda **_kwargs: {"ok": False, "reason": "binding_launch_failed", "error": "owner_ref unresolved"},
    )

    sched.save_job(
        {
            "job_id": "binding_launch_repeat_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "missing_task"},
            "prompt": "This binding should not launch.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    receipts_root = sched.receipts_dir(tmp_path) / "binding_launch_repeat_001"
    receipts_root.mkdir(parents=True, exist_ok=True)
    for minute in range(3):
        run_id = f"sj_binding_repeat_{minute}"
        (receipts_root / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "scheduled_job_receipt_v1",
                    "job_id": "binding_launch_repeat_001",
                    "run_id": run_id,
                    "job_kind": "single_turn",
                    "status": "blocked",
                    "reason": "binding_launch_failed:owner_ref unresolved",
                    "error": "",
                    "binding": {"type": "task_id", "id": "missing_task"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    guarded = sched.execute_job(
        "binding_launch_repeat_001",
        repo_root=tmp_path,
        trigger="manual",
        force=False,
        now_utc="2026-03-08T04:03:00Z",
    )
    assert guarded["ok"] is False
    assert guarded["skipped"] is True
    assert guarded["reason"].startswith("binding_launch_repeat_guard:")
    guarded_receipt = json.loads(Path(guarded["receipt_path"]).read_text(encoding="utf-8"))
    assert guarded_receipt["status"] == "blocked"
    assert guarded_receipt["reason"].startswith("binding_launch_repeat_guard:")
    assert guarded_receipt["execution_guard"]["kind"] == "binding_launch_repeat_guard"
    assert guarded_receipt["execution_guard"]["repeat_count"] == 3

    updated = sched.load_job("binding_launch_repeat_001", repo_root=tmp_path)
    updated["binding"] = {"type": "task_id", "id": "other_missing_task"}
    sched.save_job(updated, repo_root=tmp_path)
    after_change = sched.execute_job(
        "binding_launch_repeat_001",
        repo_root=tmp_path,
        trigger="manual",
        force=False,
        now_utc="2026-03-08T04:04:00Z",
    )
    assert after_change["ok"] is False
    assert after_change["skipped"] is True
    assert "binding_launch_failed" in str(after_change["reason"] or "")
    assert not str(after_change["reason"] or "").startswith("binding_launch_repeat_guard:")


def test_scheduled_job_nonlaunch_records_unresolved_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _seed_task_register(tmp_path, task_id="mb3")

    sched.save_job(
        {
            "job_id": "binding_nonlaunch_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Skip via nonlaunch path.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    spec = sched.load_job("binding_nonlaunch_001", repo_root=tmp_path)
    state = sched.load_state("binding_nonlaunch_001", repo_root=tmp_path)
    decision = sched._record_nonlaunch_decision(
        spec=spec,
        state=state,
        due={"eligible": False, "reason": "not_due", "slot_key": "", "due_utc": ""},
        run_id="sj_nonlaunch_001",
        trigger="scheduler",
        reason="global_concurrency_cap_reached",
        status="deferred",
        event="deferred",
        repo_root=tmp_path,
    )
    assert decision.get("recorded") is True

    register_state = register.load_or_init_task_register(tmp_path)
    task = next(row for row in register_state.get("active_tasks", []) if row.get("task_id") == "mb3")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("reference_status") == "execution_ref_unresolved"
    assert "nonlaunch" in str(ownership.get("reference_status_reason") or "")
    assert decision["binding_result"]["ok"] is True
    assert Path(decision["receipt_path"]).exists()
    assert Path(decision["report_path"]).exists()

    state = sched.load_state("binding_nonlaunch_001", repo_root=tmp_path)
    assert state["last_nonlaunch_reason"] == "global_concurrency_cap_reached"
    assert state["last_nonlaunch_status"] == "deferred"
    assert state["last_nonlaunch_run_id"] == "sj_nonlaunch_001"
    assert state["last_nonlaunch_receipt_path"] == decision["receipt_path"]

    receipt = json.loads(Path(decision["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "deferred"
    assert receipt["reason"] == "global_concurrency_cap_reached"
    assert receipt["ownership"]["handoff_state"] == "not_launched"
    assert receipt["binding_result"]["ok"] is True
    assert receipt["binding_result"]["delegation_ref"]["surface"] == "scheduler"

    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(
        row["event"] == "deferred"
        and row["job_id"] == "binding_nonlaunch_001"
        and row["run_id"] == "sj_nonlaunch_001"
        for row in events
    )


def test_scheduled_job_nonlaunch_clears_active_state_and_uses_binding_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _seed_task_register(tmp_path, task_id="job.alpha:1")

    sched.save_job(
        {
            "job_id": "binding_nonlaunch_override_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "job.alpha:1"},
            "prompt": "Skip via nonlaunch path with seeded binding.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    spec = sched.load_job("binding_nonlaunch_override_001", repo_root=tmp_path)
    state = {
        "active_run_id": "sj_stale_001",
        "active_started_utc": "2026-03-08T03:31:00Z",
        "active_loop_iteration": 2,
        "active_loop_stage": "verifier",
        "active_loop_completed_iterations": 1,
        "active_loop_max_iterations": 3,
        "active_loop_updated_utc": "2026-03-08T03:35:00Z",
        "last_status": "running",
    }
    sched._write_state("binding_nonlaunch_override_001", state, repo_root=tmp_path)

    decision = sched._record_nonlaunch_decision(
        spec=spec,
        state=state,
        due={"eligible": False, "reason": "not_due", "slot_key": "", "due_utc": ""},
        run_id="sj_nonlaunch_override_001",
        trigger="scheduler",
        reason="binding_launch_failed:test",
        status="blocked",
        event="blocked",
        repo_root=tmp_path,
        binding_override={"type": "task_id", "id": "job.alpha:1", "task_id": "job.alpha:1", "ledger_uid": ""},
    )
    assert decision.get("recorded") is True

    updated_state = sched.load_state("binding_nonlaunch_override_001", repo_root=tmp_path)
    assert updated_state["active_run_id"] == ""
    assert updated_state["active_started_utc"] == ""
    assert updated_state["active_loop_iteration"] == 0
    assert updated_state["active_loop_stage"] == ""
    assert updated_state["active_loop_completed_iterations"] == 0
    assert updated_state["active_loop_max_iterations"] == 0
    assert updated_state["active_loop_updated_utc"] == ""
    assert updated_state["last_status"] == "blocked"

    register_state = register.load_or_init_task_register(tmp_path)
    task = next(row for row in register_state.get("active_tasks", []) if row.get("task_id") == "job.alpha:1")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("reference_status") == "execution_ref_unresolved"

    receipt = json.loads(Path(decision["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["binding_result"]["ok"] is True
    assert receipt["binding_result"]["delegation_ref"]["surface"] == "scheduler"


def test_validate_job_spec_accepts_extended_task_id_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    saved = sched.save_job(
        {
            "job_id": "extended_task_binding_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "Task-01.alpha:beta"},
            "prompt": "Exercise extended task ids.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )
    assert saved["binding"]["id"] == "Task-01.alpha:beta"


def test_normalize_loop_uses_build_verify_loop_runtime_kind(monkeypatch) -> None:
    original = sched._normalize_runtime
    seen: list[str] = []

    def _wrapped(raw: Any, *, job_kind: str = "single_turn") -> dict:
        seen.append(str(job_kind))
        return original(raw, job_kind=job_kind)

    monkeypatch.setattr(sched, "_normalize_runtime", _wrapped)

    sched._normalize_loop(
        {
            "builder_runtime": {"model": "gpt-5.4"},
            "verifier_runtime": {"model": "deepseek-chat"},
        }
    )

    assert seen == ["build_verify_loop", "build_verify_loop"]


def test_scheduled_job_pipeline_backend_runs_through_codex_controller(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _pipeline_send(*, spec: dict, role: str, binding: dict, message: str, options: dict | None = None, runtime: dict | None = None, repo_root: Path | None = None) -> dict:
        calls.append(
            {
                "backend": "pipeline",
                "role": role,
                "binding": dict(binding),
                "message": message,
                "options": dict(options or {}),
                "runtime": dict(runtime or {}),
            }
        )
        return {
            "ok": True,
            "assistant": {
                "content": json.dumps(
                    {
                        "backend": "pipeline",
                        "response_format": "pipeline_advisory_v1",
                        "summary": "status=RUNNING; current_stage=script_8_patch_generate",
                        "modify_files": ["infra/controlplane/scheduled_jobs.py"],
                    }
                ),
                "status": "ok",
                "meta": {"backend": "pipeline"},
            },
        }

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(
            {
                "backend": "codex",
                "role": role,
                "binding": dict(binding),
                "message": message,
                "options": dict(options or {}),
            }
        )
        return {"ok": True, "assistant": {"content": "codex final", "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setitem(sched._execute_scheduled_controlled_turn.__globals__, "_pipeline_backend_send", _pipeline_send)
    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_codex_controller_pipeline_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "pipeline", "model": "pipeline:gpt-5-codex"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_codex_controller_pipeline_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert [str(row["backend"]) for row in calls] == ["pipeline", "codex"]
    assert str((calls[0]["runtime"] or {}).get("agent_tech") or "") == "pipeline"
    assert str((calls[0]["runtime"] or {}).get("model") or "") == "pipeline:gpt-5-codex"
    assert "[Scheduled Advisory Backend Request]" in str(calls[0]["message"])
    assert "Advisory Backend Result JSON" in str(calls[1]["message"])

    receipt = sched.load_receipt("nightly_codex_controller_pipeline_001", result["run_id"], repo_root=tmp_path)
    assert receipt["execution_contract"]["controller_runtime"]["agent_tech"] == "codex"
    assert receipt["execution_contract"]["advisory_runtime"]["agent_tech"] == "pipeline"
    assert receipt["advisory_assistant"]["normalized_payload"]["response_format"] == "json"
    assert receipt["runtime_send"]["selected_agent_tech"] == "codex"
    assert receipt["advisory_send"]["selected_agent_tech"] == "pipeline"
    assert receipt["model_telemetry"]["requested_agent_tech"] == "pipeline"


def test_scheduled_job_advisory_backend_failure_does_not_poison_codex_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        payload = {"role": role, "message": message, "options": dict(options or {})}
        calls.append(payload)
        agent_tech = str((options or {}).get("agent_tech") or "")
        if agent_tech == "ollama":
            return {"ok": False, "assistant": {"content": "timeout talking to ollama", "status": "error", "meta": {"backend": "ollama"}}}
        return {"ok": True, "assistant": {"content": "codex final", "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_codex_controller_ollama_fail_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "ollama", "model": "qwen2.5:3b"},
        }
    )

    result = sched.execute_job("nightly_codex_controller_ollama_fail_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert [str((row["options"] or {}).get("agent_tech") or "") for row in calls] == ["ollama", "codex"]
    assert "Advisory Backend Result" not in str(calls[1]["message"])
    receipt = sched.load_receipt("nightly_codex_controller_ollama_fail_001", result["run_id"], repo_root=tmp_path)
    assert receipt["advisory_send"]["selected_agent_tech"] == ""
    assert receipt["advisory_send"]["attempts"][0]["agent_tech"] == "ollama"
    assert receipt["advisory_assistant"]["status"] == "error"
    assert receipt["advisory_assistant"]["normalized_payload"]["response_format"] == "non_json_wrapped"
    assert receipt["runtime_send"]["selected_agent_tech"] == "codex"


def test_runtime_executor_agent_tech_rejects_invalid_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="runtime.executor_agent_tech must be codex, pipeline, or empty"):
        sched.save_job(
            {
                "job_id": "nightly_invalid_executor_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a bounded update.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "runtime": {"executor_agent_tech": "gemini", "agent_tech": "gemini", "model": "gemini-2.5-pro"},
            },
            repo_root=tmp_path,
        )


def test_runtime_executor_agent_tech_accepts_pipeline_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "nightly_pipeline_executor_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "runtime": {"executor_agent_tech": "pipeline", "agent_tech": "pipeline", "model": "pipeline:deepseek-reasoner"},
        }
    )
    assert spec["runtime"]["executor_agent_tech"] == "pipeline"
    assert spec["runtime"]["pipeline_executor_mode"] == sched.PIPELINE_STAGE8_BACKEND_MODE


def test_runtime_advisory_aliases_are_normalized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "nightly_advisory_alias_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "runtime": {"executor_agent_tech": "codex", "advisory_agent_tech": "deepseek", "advisory_model": "deepseek-reasoner"},
        }
    )
    assert spec["runtime"]["agent_tech"] == "deepseek"
    assert spec["runtime"]["advisory_agent_tech"] == "deepseek"
    assert spec["runtime"]["model"] == "deepseek-reasoner"
    assert spec["runtime"]["advisory_model"] == "deepseek-reasoner"


def test_runtime_advisory_alias_conflict_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="runtime.advisory_agent_tech conflicts with runtime.agent_tech"):
        sched.save_job(
            {
                "job_id": "nightly_advisory_conflict_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a bounded update.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "runtime": {"agent_tech": "openai", "advisory_agent_tech": "deepseek", "model": "gpt-5.4"},
            }
        )


def test_runtime_controller_only_rejects_advisory_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="runtime.execution_mode=controller_only cannot also specify advisory_agent_tech"):
        sched.save_job(
            {
                "job_id": "nightly_controller_only_ambiguous_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a bounded update.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "runtime": {"executor_agent_tech": "codex", "advisory_agent_tech": "deepseek", "advisory_model": "deepseek-reasoner", "execution_mode": "controller_only"},
            }
        )


def test_build_verify_loop_pipeline_executor_requires_full_auto(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="build_verify_loop pipeline executor requires runtime.pipeline_executor_mode=full_auto"):
        sched.save_job(
            {
                "job_id": "nightly_pipeline_build_verify_invalid_001",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Implement a bounded fix.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "policy": {"max_runtime_seconds": 900, "post_success_commit_tag": False},
                "runtime": {
                    "executor_agent_tech": "pipeline",
                    "pipeline_executor_mode": "stage8_backend",
                    "agent_tech": "deepseek",
                    "model": "deepseek-chat",
                },
                "loop": {
                    "builder_role": "builder",
                    "verifier_role": "verifier",
                    "max_iterations": 2,
                    "pass_marker": "VERDICT: PASS",
                },
            }
        )


def test_apply_runtime_send_options_does_not_forward_provider_fallback_models() -> None:
    out = sched._apply_runtime_send_options(
        {"timeout_s": 900, "fallback_model": "stale", "fallback_models": ["stale-a", "stale-b"]},
        {
            "model": "gpt-5.4",
            "fallback_model": "deepseek-reasoner",
            "fallback_models": ["deepseek-reasoner"],
            "fallback_on_quota": True,
            "reasoning_effort": "medium",
        },
    )
    assert out["model"] == "gpt-5.4"
    assert out["reasoning_effort"] == "medium"
    assert out["fallback_on_quota"] is True
    assert "fallback_model" not in out
    assert "fallback_models" not in out


def test_runtime_reasoning_effort_allows_openai_extended_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    saved = sched.save_job(
        {
            "job_id": "nightly_openai_reasoning_xhigh_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4", "reasoning_effort": "xhigh"},
        }
    )

    assert saved["runtime"]["reasoning_effort"] == "xhigh"


def test_runtime_execution_mode_rejects_invalid_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="runtime.execution_mode must be advisory_then_controller, advisory_only, controller_only, or empty"):        sched.save_job(
            {
                "job_id": "nightly_invalid_execution_mode_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a bounded update.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "runtime": {"execution_mode": "nonsense", "agent_tech": "deepseek", "model": "deepseek-reasoner"},
            }
        )


def test_runtime_compiled_execution_mode_rejects_invalid_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="runtime.compiled_execution_mode must be legacy, shadow_compile, soft_gate, hard_gate, or empty"):
        sched.save_job(
            {
                "job_id": "nightly_invalid_compiled_execution_mode_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a bounded update.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "runtime": {"compiled_execution_mode": "nonsense"},
            }
        )


def test_pipeline_stage8_config_overrides_propagate_provider_for_deepseek() -> None:
    overrides, model_cfg = sched._pipeline_stage8_config_overrides(
        runtime={
            "model": "deepseek-reasoner",
            "agent_tech": "deepseek",
            "reasoning_effort": "medium",
        },
        jobs_root=Path("/tmp/pipeline-jobs"),
    )

    assert model_cfg["stage8_agent_tech"] == "deepseek"
    assert model_cfg["stage8_provider"] == "deepseek"
    stage8 = overrides["stages"]["script_8_patch_generate"]
    assert stage8["model"] == "deepseek-reasoner"
    assert stage8["requested_agent_tech"] == "deepseek"
    assert stage8["provider"] == "deepseek"


def test_scheduled_job_controller_only_mode_skips_advisory_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "message": message, "options": dict(options or {})})
        return {"ok": True, "assistant": {"content": "codex final", "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_controller_only_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner", "execution_mode": "controller_only"},
        }
    )

    result = sched.execute_job("nightly_controller_only_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert len(calls) == 1
    receipt = sched.load_receipt("nightly_controller_only_001", result["run_id"], repo_root=tmp_path)
    assert receipt["execution_contract"]["execution_mode"] == "controller_only"
    assert receipt["model_telemetry"]["execution_mode"] == "controller_only"
    assert receipt["model_telemetry"]["advisory_agent_tech"] == ""
    stage = receipt["model_telemetry"]["stages"][0]
    assert stage["controller_called"] is True
    assert stage["advisory_called"] is False


def test_build_verify_loop_codex_executor_forces_codex_controller_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    calls: list[tuple[str, str, str]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        agent_tech = str((options or {}).get("agent_tech") or "")
        model = str((options or {}).get("model") or "")
        calls.append((role, agent_tech, model))
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files: none\n\nValidation Commands Run:\n- no-op\n",
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: no-op\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_codex_executor_force_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4", "fallback_models": ["gpt-5.4-mini"]},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_codex_executor_force_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert calls == [
        ("builder", "codex", "gpt-5.4"),
        ("assurer", "codex", "gpt-5.4"),
    ]
    receipt = sched.load_receipt("nightly_codex_executor_force_001", result["run_id"], repo_root=tmp_path)
    row = receipt["loop"]["iterations"][0]
    assert row["builder"]["runtime_send"]["selected_agent_tech"] == "codex"
    assert row["verifier"]["runtime_send"]["selected_agent_tech"] == "codex"


def test_merge_runtime_overrides_preserves_stage_execution_mode() -> None:
    merged = sched._merge_runtime_overrides(
        {
            "agent_tech": "deepseek",
            "executor_agent_tech": "codex",
            "model": "deepseek-reasoner",
            "execution_mode": "controller_only",
        },
        {
            "agent_tech": "openai",
            "model": "gpt-5.4",
            "execution_mode": "advisory_then_controller",
        },
    )
    assert merged["agent_tech"] == "openai"
    assert merged["model"] == "gpt-5.4"
    assert merged["execution_mode"] == "advisory_then_controller"


def test_build_verify_loop_stage_execution_mode_can_diverge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "message": message, "options": dict(options or {})})
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files:\n- <scheduler_managed_scope>\n\nValidation Commands Run:\n- `true`\n\n```diff\n--- a/file\n+++ b/file\n```",
                    "status": "ok",
                    "meta": {"backend": "codex"},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: true\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {"backend": "codex"},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_stage_execution_mode_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner", "execution_mode": "controller_only"},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 2,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner", "execution_mode": "controller_only"},
                "verifier_runtime": {"agent_tech": "openai", "model": "gpt-5.4", "execution_mode": "advisory_then_controller"},
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_stage_execution_mode_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_stage_execution_mode_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    builder_stage = receipt["model_telemetry"]["stages"][0]
    verifier_stage = receipt["model_telemetry"]["stages"][1]
    assert builder_stage["stage"] == "builder"
    assert builder_stage["advisory_called"] is False
    assert verifier_stage["stage"] == "verifier"
    assert verifier_stage["advisory_called"] is True


def test_build_dispatch_message_includes_structured_reentry_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = {
        "job_id": "dispatch_reentry_001",
        "job_kind": "single_turn",
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mb3"},
        "prompt": "Do the bounded task.",
        "required_context_bundle": "lab_core_v1",
    }
    text = sched._build_dispatch_message(spec, trigger="manual", run_id="sj_demo_001", repo_root=tmp_path)
    assert "run_id: sj_demo_001" in text
    assert "[Scheduled Reentry State]" in text
    assert "explicit_reentry_reason:" in text


def test_build_loop_messages_include_structured_reentry_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = {
        "job_id": "loop_reentry_001",
        "job_kind": "build_verify_loop",
        "binding": {"type": "task_id", "id": "mb3"},
        "prompt": "Implement and verify.",
        "required_context_bundle": "lab_core_v1",
    }
    builder = sched._build_loop_builder_message(
        spec,
        run_id="sj_demo_002",
        trigger="manual",
        iteration=2,
        max_iterations=4,
        verifier_feedback="Missing validation evidence.",
        repo_root=tmp_path,
    )
    verifier = sched._build_loop_verifier_message(
        spec,
        run_id="sj_demo_002",
        trigger="manual",
        iteration=2,
        max_iterations=4,
        builder_output="Builder did work.",
        pass_marker="VERDICT: PASS",
        repo_root=tmp_path,
    )
    assert "[Scheduled Reentry State]" in builder
    assert "run_id: sj_demo_002" in builder
    assert "latest_verifier_findings_summary:" in builder
    assert "emit the heading exactly as `Validation Commands Run:`" in builder
    assert "do not rename or substitute that heading" in builder
    assert "include `Validation Commands Run:` even when validation is minimal or a no-op" in builder
    assert "Changed files:" in builder
    assert "Notes:" in builder
    assert "[Scheduled Reentry State]" in verifier
    assert "run_id: sj_demo_002" in verifier
    assert "latest_builder_output_summary:" in verifier
    assert "do not emit `HOST_VERIFICATION_REQUEST_JSON` in this scheduled verifier path" in verifier
    assert "do not request unsupported ledger-only host actions such as `ledger_query`" in verifier
    assert "do not put headings, summaries, or structural-validity lines before the verdict line" in verifier
    assert "Findings:" in verifier
    assert "Boundary Review:" in verifier
    assert "Proof Review:" in verifier
    assert "Validation Review:" in verifier


def test_build_loop_builder_message_surfaces_bound_continuation_packet_when_task_binding_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    (tmp_path / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="sched.packet",
        task={
            "task_id": "task_sched_packet",
            "kind": "software",
            "title": "Resume scheduled work from packet",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "use durable packet on reentry",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="sched.packet",
        task_id="task_sched_packet",
        selected_surface="workflow",
        execution_id="job_sched_packet",
        execution_ref="workflow:job_sched_packet",
        binding_ref="binding_sched_packet",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/job_sched_packet.json",
        trace_id="trace_sched_packet",
    )["task_register"]
    register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="sched.packet",
        task_id="task_sched_packet",
        run_id="job_sched_packet",
        checkpoint={
            "checkpoint_id": "cp_sched_packet",
            "checkpoint_status": "recorded",
            "checkpoint_note": "scheduler resume point",
            "progress_summary": "checkpoint captured",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_sched_packet.json"],
        },
    )
    packet_store.append_packet_revision_once(
        {
            "schema_version": "continuation_packet_v1",
            "packet_id": "detached_job:job_sched_packet",
            "packet_revision": 1,
            "created_utc": "2026-04-07T09:15:00Z",
            "source_run": {
                "job_id": "job_sched_packet",
                "run_id": "req_sched_packet",
                "attempt_id": "turn_sched_packet",
                "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_sched_packet.json"],
            },
            "handoff_brief": "Scheduled reentry should use the durable packet first.",
            "result_summary": {"outcome": "interrupted", "summary": "Resume from the latest durable checkpoint."},
            "evidence_created": [{"evidence_id": "ev_sched", "kind": "unit_test", "summary": "scheduled reentry", "refs": ["tests/control_plane/test_scheduled_jobs.py"]}],
            "artifact_index": [{"artifact_id": "art_sched", "kind": "json", "uri": "runtime/state/lab_assistant/detached_jobs/jobs/job_sched_packet.json", "summary": "job record"}],
            "open_questions": [{"question_id": "q_sched", "question": "Resume now?"}],
            "failed_paths": [{"path_id": "fp_sched", "summary": "Success is not yet proven."}],
            "recommended_next_steps": [{"step_id": "step_sched", "priority": "now", "step": "Resume from the checkpoint."}],
            "confidence": {"level": "medium", "rationale": "Durable checkpoint present."},
            "applicability": {"scope": "immediate_successor", "limitations": ["Lifecycle truth remains authoritative for raw state."]},
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "loop_reentry_packet_001",
        "job_kind": "build_verify_loop",
        "binding": {"type": "task_id", "id": "task_sched_packet"},
        "prompt": "Resume the bounded implementation work.",
        "required_context_bundle": "lab_core_v1",
    }

    builder = sched._build_loop_builder_message(
        spec,
        run_id="sj_demo_packet_001",
        trigger="manual",
        iteration=1,
        max_iterations=2,
        repo_root=tmp_path,
    )

    assert "continuation_packet_bootstrap:" in builder
    assert "continuation_inspection:" in builder
    assert "detached_job:job_sched_packet:r1" in builder
    assert "task_sched_packet" in builder
    assert "Success is not yet proven." in builder


def test_build_loop_builder_message_hardens_scope_for_status_reconcile_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = {
        "job_id": "loop_reentry_scope_001",
        "job_kind": "build_verify_loop",
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
        "required_context_bundle": "lab_core_v1",
    }
    builder = sched._build_loop_builder_message(
        spec,
        run_id="sj_demo_scope_001",
        trigger="manual",
        iteration=1,
        max_iterations=4,
        repo_root=tmp_path,
    )
    assert "inspect child or dependency state as evidence only; do not mutate child items during parent reconcile" in builder
    assert "do not repair stale dependency snapshots in sibling or child `pis.md` files" in builder
    assert "do not widen scope to scheduler-test cleanup, validation-suite cleanup, or unrelated dependency-truth repair" in builder
    assert "if the bound parent remains draft, record that honestly in the bound parent only" in builder
    verifier = sched._build_loop_verifier_message(
        spec,
        run_id="sj_demo_scope_001",
        trigger="manual",
        iteration=1,
        max_iterations=4,
        builder_output="Builder output.",
        pass_marker="VERDICT: PASS",
        repo_root=tmp_path,
    )
    assert "Loop Control:" in verifier
    assert "- confidence_percent: <integer 0-100>" in verifier
    assert "- expected_next_iteration_gain: <integer>" in verifier
    assert "- recommended_action: continue|stop|escalate" in verifier
    assert "- good_enough_for: draft_only|operator_review|not_promotable" in verifier
    assert "do not rename `Loop Control:` or any field names inside it" in verifier
    assert "every field above is required on every verifier reply for this job class; do not leave blanks" in verifier
    assert "set `recommended_action: stop`" in verifier
    assert "confidence_percent:" in verifier
    assert "expected_next_iteration_gain:" in verifier
    assert "recommended_action:" in verifier
    assert "these confidence fields do not change the verdict and must never substitute for proof" in verifier


def test_execute_job_blocks_when_continuation_bootstrap_is_ambiguous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    generation = int(current.get("write_generation") or 0)
    for task_id in ("task_bootstrap_1", "task_bootstrap_2"):
        updated = register.upsert_task(
            tmp_path,
            expected_generation=generation,
            writer="sched.bootstrap.blocked",
            task={
                "task_id": task_id,
                "kind": "software",
                "title": f"task {task_id}",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "ledger_uid": "PIS-20260407-063104Z-D4F4",
                "next_expected_action": "resume from packet",
            },
        )
        generation = int((updated.get("task_register") or {}).get("write_generation") or 0)
    spec = sched.save_job(
        sched.validate_job_spec(
            {
                "job_id": "continuation_bootstrap_blocked_001",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": "PIS-20260407-063104Z-D4F4"},
                "job_kind": "build_verify_loop",
                "loop": {"max_iterations": 1},
                "prompt": "Implement the bound item.",
                "schedule": {"mode": "once", "run_at": "2026-04-07T18:00:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "Blocked continuation bootstrap",
                "required_context_bundle": "lab_core_v1",
                "context_refs": [],
                "additional_context_refs": [],
                "forbidden_context_refs": [],
            },
            now_utc="2026-04-07T17:55:00Z",
            repo_root=tmp_path,
        ),
        repo_root=tmp_path,
    )

    result = sched.execute_job(str(spec.get("job_id") or ""), repo_root=tmp_path, trigger="manual", now_utc="2026-04-07T17:56:00Z")

    assert result["ok"] is False
    assert result["skipped"] is True
    assert result["reason"].startswith("continuation_bootstrap_blocked:")
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked"
    assert receipt["continuation_bootstrap"]["status"] == "blocked"
    assert "multiple active tasks match binding" in receipt["continuation_bootstrap"]["reason"]


def test_seed_single_turn_owner_binding_reuses_existing_ledger_uid_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(current.get("write_generation") or 0),
        writer="sched.seed.reuse",
        task={
            "task_id": "task_existing_d0c7",
            "kind": "software",
            "title": "Existing task",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260407-064150Z-D0C7",
            "next_expected_action": "run scheduled job",
        },
    )
    before = register.read_task_register_snapshot(tmp_path, initialize_if_missing=True)["task_register"]

    seeded = sched._seed_single_turn_owner_binding(
        {
            "job_id": "close_d0c7_20260407_execution_family",
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-064150Z-D0C7"},
            "prompt": "Close the execution family.",
            "display_title": "Close D0C7 Family",
        },
        run_id="sj_demo_reuse_001",
        trigger="scheduler",
        repo_root=tmp_path,
    )

    after = register.read_task_register_snapshot(tmp_path, initialize_if_missing=True)["task_register"]
    matches = [
        dict(row)
        for row in list(after.get("active_tasks") or [])
        if isinstance(row, dict) and str(row.get("ledger_uid") or "").strip() == "PIS-20260407-064150Z-D0C7"
    ]
    assert seeded == {"task_id": "task_existing_d0c7", "ledger_uid": "PIS-20260407-064150Z-D0C7"}
    assert len(matches) == 1
    assert matches[0]["task_id"] == "task_existing_d0c7"
    assert matches[0]["title"] == "Close D0C7 Family"
    assert matches[0]["owner"] == "scheduler"
    assert matches[0]["source"] == "scheduler"
    assert matches[0]["merge_status"] == "merge_needed"
    assert set(matches[0]["conflict_fields"]) == {"source", "title"}
    assert int(after.get("write_generation") or 0) == int(before.get("write_generation") or 0) + 1


def test_build_loop_verifier_message_adds_high_impact_stabilization_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    uid = "PIS-20260402-062300Z-P5D2"
    item_dir = tmp_path / "governance" / "ledger" / "items" / "causyl" / "active" / "2026" / "04" / f"{uid}__validation-claim-hygiene"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text(
        "# Patch Instruction Sheet (PIS)\n## 1. Title\nEnforce Validation Claim Hygiene And Narrow Honest Reporting\n",
        encoding="utf-8",
    )
    spec = {
        "job_id": "loop_reentry_stabilization_001",
        "binding": {"type": "ledger_uid", "id": uid},
        "job_kind": "build_verify_loop",
        "prompt": "Implement the bound stabilization item and verify the exact proof earned.",
        "required_context_bundle": "lab_core_v1",
    }

    verifier = sched._build_loop_verifier_message(
        spec,
        run_id="sj_demo_stab_001",
        trigger="manual",
        iteration=1,
        max_iterations=2,
        builder_output="Builder output.",
        pass_marker="VERDICT: PASS",
        repo_root=tmp_path,
    )

    assert f"high-impact stabilization review for bound ledger item `{uid}`" in verifier
    assert "Findings by Severity:" in verifier
    assert "Boundary Review:" in verifier
    assert "Proof Review:" in verifier
    assert "Validation Run Review:" in verifier
    assert "One-Iteration Assessment:" in verifier
    assert "keep_as_is|patch_only|rebuild_required" in verifier


def test_executor_of_record_audit_allows_read_only_advisory_execution() -> None:
    spec = {
        "job_id": "single_turn_read_only_001",
        "job_kind": "single_turn",
        "binding": {"type": "task_id", "id": "mb3"},
        "policy": {"repo_change_mode": "none"},
        "runtime": {"agent_tech": "deepseek", "execution_mode": "advisory_only"},
    }
    runtime = sched._normalize_runtime(spec.get("runtime"), job_kind="single_turn")
    receipt = {
        "job_kind": "single_turn",
        "runtime": runtime,
        "execution_contract": sched._scheduled_execution_contract(runtime, job_kind="single_turn"),
        "compiled_execution": sched.compiled_execution_ir_from_job_spec(spec),
        "model_telemetry": {"executor_agent_tech": "deepseek"},
    }

    audit = sched._scheduled_executor_of_record_audit(spec=spec, receipt=receipt)

    assert audit["write_capable"] is False
    assert audit["actual_executor"] == "deepseek"
    assert audit["contract_ok"] is True
    assert audit["violation_reason"] == ""


def test_scheduled_job_pipeline_executor_runs_without_codex_controller(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _pipeline_send(
        *,
        spec: dict | None = None,
        role: str,
        binding: dict,
        message: str,
        options: dict | None = None,
        runtime: dict | None = None,
        repo_root: Path | None = None,
    ) -> dict:
        calls.append(
            {
                "backend": "pipeline",
                "spec": dict(spec or {}),
                "role": role,
                "binding": dict(binding),
                "message": message,
                "options": dict(options or {}),
                "runtime": dict(runtime or {}),
            }
        )
        return {"ok": True, "assistant": {"content": "pipeline executor final", "status": "ok", "meta": {"backend": "pipeline"}}}

    def _unexpected_codex_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        raise AssertionError("codex controller should not be used when executor_agent_tech=pipeline")

    monkeypatch.setattr(sched, "_pipeline_backend_send", _pipeline_send)
    monkeypatch.setattr(sched._agents, "send_message", _unexpected_codex_send)

    sched.save_job(
        {
            "job_id": "nightly_pipeline_executor_direct_001",
            "enabled": True,
            "job_kind": "single_turn",
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"executor_agent_tech": "pipeline", "agent_tech": "pipeline", "model": "pipeline:deepseek-reasoner"},
        }
    )

    result = sched.execute_job("nightly_pipeline_executor_direct_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert [str(row["backend"]) for row in calls] == ["pipeline"]
    receipt = sched.load_receipt("nightly_pipeline_executor_direct_001", result["run_id"], repo_root=tmp_path)
    assert receipt["execution_contract"]["controller_runtime"]["agent_tech"] == "pipeline"
    assert receipt["execution_contract"]["controller_runtime"]["executor_agent_tech"] == "pipeline"
    assert receipt["execution_contract"]["advisory_runtime"] == {}
    assert receipt["runtime_send"]["selected_agent_tech"] == "pipeline"


def test_scheduled_build_verify_loop_pipeline_full_auto_uses_pipeline_as_execution_owner(tmp_path: Path, monkeypatch) -> None:
    from infra.pipeline import automation as pipeline_automation

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    monkeypatch.setattr(sched, "_execute_build_verify_loop_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy scheduler loop should not run for pipeline full_auto")))
    monkeypatch.setattr(sched._agents, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex controller should not be used for pipeline full_auto")))
    monkeypatch.setattr(
        sched,
        "_queue_child_implementation_jobs_after_pass",
        lambda spec, finished_utc, repo_root=None: {"enabled": False, "created_count": 0, "skipped_count": 0, "children": []},
    )
    monkeypatch.setattr(
        sched,
        "_promote_pipeline_summary_to_repo",
        lambda *, summary, repo_root=None: {"ok": True, "reason": "", "error": "", "apply_result": {}, "target_files": []},
    )

    proof_dir = tmp_path / "pipeline-proof"
    proof_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths = {}
    for name in [
        "execution_contract_path",
        "validated_artifact_path",
        "apply_result_path",
        "contract_verification_path",
        "repair_decision_path",
        "finalization_path",
    ]:
        p = proof_dir / f"{name}.json"
        p.write_text("{}\n", encoding="utf-8")
        artifact_paths[name] = str(p)
    summary_json = proof_dir / "summary.json"
    summary_md = proof_dir / "summary.md"
    summary_json.write_text("{}\n", encoding="utf-8")
    summary_md.write_text("summary\n", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def _fake_run_full_auto(*, repo_root, prompt_text, raw_request=None, job_id=None, config_overrides=None, auto_approve=True, **kwargs):
        calls.append(
            {
                "repo_root": str(repo_root),
                "prompt_text": str(prompt_text),
                "raw_request": dict(raw_request or {}),
                "job_id": str(job_id or ""),
                "config_overrides": dict(config_overrides or {}),
                "auto_approve": bool(auto_approve),
            }
        )
        return {
            "run_id": "RUN-PIPE-001",
            "status": "ACCEPTED",
            "finalization_state": {"final_status": "accepted"},
            "repair_decision_summary": {"retry_authorization_state": "terminal_accept", "blocking_reason_codes": []},
            "controller": {"best_run_id": "RUN-PIPE-001"},
            "summary_paths": {"json_path": str(summary_json), "markdown_path": str(summary_md)},
            "artifacts": dict(artifact_paths),
        }

    monkeypatch.setattr(pipeline_automation, "run_full_auto", _fake_run_full_auto)

    sched.save_job(
        {
            "job_id": "nightly_pipeline_full_auto_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify the bounded fix.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "post_success_commit_tag": False},
            "required_context_bundle": "lab_core_v1",
            "runtime": {
                "executor_agent_tech": "pipeline",
                "pipeline_executor_mode": "full_auto",
                "agent_tech": "deepseek",
                "model": "deepseek-chat",
            },
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 2,
                "pass_marker": "VERDICT: PASS",
            },
        }
    )

    result = sched.execute_job(
        "nightly_pipeline_full_auto_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["raw_request"]["metadata"]["scheduler_job_id"] == "nightly_pipeline_full_auto_001"
    assert calls[0]["raw_request"]["metadata"]["job_shape"] == "verification_loop_job"
    assert calls[0]["raw_request"]["metadata"]["job_shape_source"] == "explicit"
    assert calls[0]["raw_request"]["metadata"]["job_shape_compatibility_mode"] is False
    assert calls[0]["raw_request"]["metadata"]["pipeline_executor_mode"] == "full_auto"
    receipt = sched.load_receipt("nightly_pipeline_full_auto_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["job_shape"] == "verification_loop_job"
    assert receipt["job_shape_source"] == "explicit"
    assert receipt["job_shape_compatibility_mode"] is False
    assert receipt["execution_contract"]["execution_owner"] == "pipeline"
    assert receipt["execution_contract"]["proof_owner"] == "pipeline"
    assert receipt["execution_contract"]["retry_owner"] == "pipeline"
    assert receipt["pipeline_execution"]["mode"] == sched.PIPELINE_FULL_AUTO_MODE
    assert receipt["pipeline_execution"]["pipeline_run_id"] == "RUN-PIPE-001"
    assert receipt["pipeline_execution"]["repo_promotion"]["ok"] is True
    assert receipt["no_auto_retry"] is True
    assert receipt["model_telemetry"]["executor_agent_tech"] == "pipeline"
    assert receipt["executor_of_record"]["write_capable"] is True
    assert receipt["executor_of_record"]["required_executor"] == "codex"
    assert receipt["executor_of_record"]["actual_executor"] == "pipeline"
    assert receipt["executor_of_record"]["contract_ok"] is False
    assert receipt["executor_of_record"]["violation_reason"] == "write_capable_job_executed_by_non_codex:pipeline"


def test_pipeline_full_auto_once_slot_is_not_reused_after_needs_review(tmp_path: Path, monkeypatch) -> None:
    from infra.pipeline import automation as pipeline_automation

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    monkeypatch.setattr(sched, "_execute_build_verify_loop_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy scheduler loop should not run for pipeline full_auto")))
    monkeypatch.setattr(sched._agents, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex controller should not be used for pipeline full_auto")))

    proof_dir = tmp_path / "pipeline-proof-review"
    proof_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths = {}
    for name in [
        "execution_contract_path",
        "validated_artifact_path",
        "apply_result_path",
        "contract_verification_path",
        "repair_decision_path",
        "finalization_path",
    ]:
        p = proof_dir / f"{name}.json"
        p.write_text("{}\n", encoding="utf-8")
        artifact_paths[name] = str(p)
    summary_json = proof_dir / "summary.json"
    summary_md = proof_dir / "summary.md"
    summary_json.write_text("{}\n", encoding="utf-8")
    summary_md.write_text("summary\n", encoding="utf-8")

    def _fake_run_full_auto(**kwargs):
        return {
            "run_id": "RUN-PIPE-REVIEW-001",
            "status": "WAITING_REVIEW",
            "finalization_state": {"final_status": "needs_review"},
            "repair_decision_summary": {"retry_authorization_state": "operator_review", "blocking_reason_codes": []},
            "controller": {"best_run_id": "RUN-PIPE-REVIEW-001"},
            "summary_paths": {"json_path": str(summary_json), "markdown_path": str(summary_md)},
            "artifacts": dict(artifact_paths),
        }

    monkeypatch.setattr(pipeline_automation, "run_full_auto", _fake_run_full_auto)

    sched.save_job(
        {
            "job_id": "nightly_pipeline_full_auto_review_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify the bounded fix.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "post_success_commit_tag": False},
            "required_context_bundle": "lab_core_v1",
            "runtime": {
                "executor_agent_tech": "pipeline",
                "pipeline_executor_mode": "full_auto",
                "agent_tech": "deepseek",
                "model": "deepseek-chat",
            },
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 2,
                "pass_marker": "VERDICT: PASS",
            },
        }
    )

    result = sched.execute_job(
        "nightly_pipeline_full_auto_review_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_pipeline_full_auto_review_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert "do not enter an operator review queue" in str(receipt.get("error") or "")
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "- status: failed_verification" in report
    assert "do not enter an operator review queue" in report
    state = sched.load_state("nightly_pipeline_full_auto_review_001", repo_root=tmp_path)
    assert state["last_no_auto_retry"] is True
    due = sched.due_status(
        sched.load_job("nightly_pipeline_full_auto_review_001", repo_root=tmp_path),
        state=state,
        now=sched._parse_utc("2026-03-08T04:05:00Z"),
        repo_root=tmp_path,
    )
    assert due["eligible"] is False
    assert due["reason"] == "terminal_no_auto_retry"


def test_scheduled_job_retries_agent_turn_on_transient_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls = {"n": 0}
    sleeps: list[int] = []
    resend_keys: list[str] = []

    def _flaky_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls["n"] += 1
        resend_keys.append(str((options or {}).get("resend_key") or ""))
        if calls["n"] == 1:
            raise RuntimeError("transient network error")
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {"options": dict(options or {})}}}

    monkeypatch.setattr(sched._agents, "send_message", _flaky_send)
    monkeypatch.setattr(sched.time, "sleep", lambda sec: sleeps.append(int(sec)))

    sched.save_job(
        {
            "job_id": "nightly_retry_single_turn_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 1, "agent_turn_retry_backoff_seconds": 5},
            "runtime": {"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
        }
    )

    result = sched.execute_job("nightly_retry_single_turn_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert calls["n"] == 2
    assert [sec for sec in sleeps if sec > 0] == [5]
    assert len(set(resend_keys)) == 1
    assert resend_keys[0].startswith("scheduled_job::nightly_retry_single_turn_001::")
    receipt = sched.load_receipt("nightly_retry_single_turn_001", result["run_id"], repo_root=tmp_path)
    assert receipt["runtime_send"]["selected_model"] == "gpt-5.3-codex"
    assert len(receipt["runtime_send"]["attempts"]) == 2
    assert receipt["runtime_send"]["resend_key"] == resend_keys[0]


def test_scheduled_job_retries_whole_job_for_transient_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"calls": 0}
    sleeps: list[int] = []

    def _timeout_then_ok(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        seen["calls"] += 1
        if seen["calls"] == 1:
            raise RuntimeError("connection timed out")
        return {"ok": True, "assistant": {"content": "ok after retry", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _timeout_then_ok)
    monkeypatch.setattr(sched.time, "sleep", lambda sec: sleeps.append(int(sec)))

    sched.save_job(
        {
            "job_id": "nightly_whole_job_retry_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 900,
                "agent_turn_retries": 0,
                "job_transient_retries": 1,
                "job_transient_retry_backoff_seconds": 9,
            },
        }
    )

    result = sched.execute_job("nightly_whole_job_retry_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert seen["calls"] == 2
    assert sleeps == [9]
    receipt = sched.load_receipt("nightly_whole_job_retry_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["job_retry"]["attempt_count"] == 2
    assert receipt["job_retry"]["retries_used"] == 1
    assert receipt["job_retry"]["succeeded_after_retry"] is True
    assert receipt["job_retry"]["history"][0]["failure_class"] == "transient_runtime_error"
    assert receipt["job_retry"]["history"][0]["retryable"] is True


def test_scheduled_job_does_not_retry_verifier_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}

    def _builder_then_fail_verifier(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nsemantic issue", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _builder_then_fail_verifier)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_no_retry_verifier_fail_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "job_transient_retries": 3},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_no_retry_verifier_fail_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    assert seen["builder"] == 1
    assert seen["verifier"] == 1
    receipt = sched.load_receipt("nightly_no_retry_verifier_fail_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert receipt["job_retry"]["attempt_count"] == 1
    assert receipt["job_retry"]["history"][0]["retryable"] is False
    assert receipt["job_retry"]["history"][0]["failure_class"] == "failed_verification"


def test_scheduled_job_transient_whole_job_retry_exhaustion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sleeps: list[int] = []
    seen = {"calls": 0}

    def _always_timeout(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        seen["calls"] += 1
        raise RuntimeError("service unavailable timeout")

    monkeypatch.setattr(sched._agents, "send_message", _always_timeout)
    monkeypatch.setattr(sched.time, "sleep", lambda sec: sleeps.append(int(sec)))

    sched.save_job(
        {
            "job_id": "nightly_whole_job_retry_exhaust_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 900,
                "agent_turn_retries": 0,
                "job_transient_retries": 2,
                "job_transient_retry_backoff_seconds": 4,
            },
        }
    )

    result = sched.execute_job("nightly_whole_job_retry_exhaust_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    assert seen["calls"] == 3
    assert sleeps == [4, 4]
    receipt = sched.load_receipt("nightly_whole_job_retry_exhaust_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "error"
    assert receipt["job_retry"]["attempt_count"] == 3
    assert receipt["job_retry"]["retries_used"] == 2
    assert len(receipt["job_retry"]["history"]) == 3
    assert receipt["job_retry"]["final_failure_class"] == "transient_runtime_error"


def test_scheduled_job_report_includes_whole_job_retry_history_and_final_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"calls": 0}

    def _timeout_then_ok(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        seen["calls"] += 1
        if seen["calls"] == 1:
            raise RuntimeError("gateway timeout")
        return {"ok": True, "assistant": {"content": "ok after retry", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _timeout_then_ok)

    sched.save_job(
        {
            "job_id": "nightly_whole_job_report_retry_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 900,
                "agent_turn_retries": 0,
                "job_transient_retries": 1,
                "job_transient_retry_backoff_seconds": 1,
            },
        }
    )

    result = sched.execute_job("nightly_whole_job_report_retry_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "- status: ok" in report
    assert "## Whole-Job Retry" in report
    assert "- attempt_count: 2" in report
    assert "- succeeded_after_retry: true" in report
    assert "### Retry History" in report


def test_scheduled_job_falls_back_to_fallback_model_on_quota_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen_models: list[str] = []

    def _quota_then_ok(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        model = str((options or {}).get("model") or "")
        seen_models.append(model)
        if model == "gpt-5.3-codex":
            return {
                "ok": False,
                "assistant": {"content": "insufficient_quota for requested model", "status": "error", "meta": {}},
            }
        return {"ok": True, "assistant": {"content": "ok fallback", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _quota_then_ok)

    sched.save_job(
        {
            "job_id": "nightly_fallback_single_turn_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "model": "gpt-5.3-codex",
                "fallback_model": "gpt-5.1",
                "fallback_on_quota": True,
                "reasoning_effort": "medium",
            },
        }
    )

    result = sched.execute_job("nightly_fallback_single_turn_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert seen_models[:2] == ["gpt-5.3-codex", "gpt-5.1"]
    receipt = sched.load_receipt("nightly_fallback_single_turn_001", result["run_id"], repo_root=tmp_path)
    assert receipt["runtime_send"]["selected_model"] == "gpt-5.1"
    assert bool(receipt["runtime_send"]["attempts"][0]["quota_like"]) is True
    assert str(receipt["runtime_send"]["resend_key_base"]).startswith("scheduled_job::nightly_fallback_single_turn_001::")


def test_scheduled_job_uses_ordered_fallback_chain_on_quota_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen_models: list[str] = []

    def _quota_until_third(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        model = str((options or {}).get("model") or "")
        seen_models.append(model)
        if model in {"gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"}:
            return {
                "ok": False,
                "assistant": {
                    "content": f"model unavailable or insufficient_quota: {model}",
                    "status": "error",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": f"ok via {model}", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _quota_until_third)

    sched.save_job(
        {
            "job_id": "nightly_fallback_chain_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "model": "gpt-5.4",
                "fallback_models": [
                    "gpt-5.3-codex",
                    "gpt-5.3-codex-spark",
                    "gpt-5.2-codex",
                ],
                "fallback_on_quota": True,
                "reasoning_effort": "medium",
            },
        }
    )

    result = sched.execute_job("nightly_fallback_chain_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert seen_models == ["gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2-codex"]
    receipt = sched.load_receipt("nightly_fallback_chain_001", result["run_id"], repo_root=tmp_path)
    assert receipt["runtime_send"]["selected_model"] == "gpt-5.2-codex"
    assert [attempt["model"] for attempt in receipt["runtime_send"]["attempts"]] == seen_models
    assert all(bool(attempt["quota_like"]) for attempt in receipt["runtime_send"]["attempts"][:-1])
    assert bool(receipt["runtime_send"]["attempts"][-1]["quota_like"]) is False


def test_codex_controller_runtime_defaults_come_from_runtime_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        sched._runtime_policy,
        "get_runtime_policy_text",
        lambda key, default, **kwargs: "gpt-5.4" if key == "scheduler.controller.default_model" else default,
    )
    monkeypatch.setattr(
        sched,
        "_runtime_policy_models",
        lambda key, default_csv="": [],
    )
    runtime = {"agent_tech": "deepseek", "model": "deepseek-reasoner", "fallback_models": ["gpt-5.4"]}
    controller = sched._codex_controller_runtime(runtime)
    assert controller["agent_tech"] == "codex"
    assert controller["model"] == "gpt-5.4"
    assert controller["fallback_models"] == []


def test_explicit_deepseek_fallbacks_are_preserved_without_extra_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    saved = sched.save_job(
        {
            "job_id": "deepseek_explicit_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "agent_tech": "deepseek",
                "model": "deepseek-reasoner",
                "fallback_models": ["gpt-5.4"],
            },
        }
    )
    assert saved["runtime"]["model"] == "deepseek-reasoner"
    assert saved["runtime"]["fallback_models"] == ["gpt-5.4"]


def test_xai_runtime_defaults_to_grok_code_fast_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_xai_default_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbx"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "agent_tech": "xai",
            },
        }
    )

    loaded = sched.load_job("nightly_xai_default_001", repo_root=tmp_path)
    assert loaded is not None
    assert loaded["runtime"]["model"] == "grok-code-fast-1"
    assert "grok-code-fast-1" not in list(loaded["runtime"].get("fallback_models") or [])


def test_deepseek_runtime_defaults_to_chat_and_reasoner_pair(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_deepseek_default_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "agent_tech": "deepseek",
            },
        }
    )

    loaded = sched.load_job("nightly_deepseek_default_001", repo_root=tmp_path)
    assert loaded is not None
    assert loaded["runtime"]["model"] == "deepseek-chat"
    assert list(loaded["runtime"].get("fallback_models") or []) == ["deepseek-reasoner"]


def test_build_verify_codex_runtime_defaults_come_from_runtime_policy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_build_verify_default_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Verify and build.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "runtime": {
                "agent_tech": "codex",
            },
        }
    )

    loaded = sched.load_job("nightly_build_verify_default_001", repo_root=tmp_path)
    assert loaded is not None
    assert loaded["runtime"]["model"] == "gpt-5.4"
    assert list(loaded["runtime"].get("fallback_models") or []) == ["deepseek-reasoner"]


def test_delete_job_removes_inbox_spec_and_archives_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_delete_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Delete me.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
        }
    )

    result = sched.delete_job("nightly_delete_001", repo_root=tmp_path)
    assert result["deleted"] is True
    assert not (sched.jobs_dir(tmp_path) / "nightly_delete_001.json").exists()
    archived = sorted((sched.archive_dir(tmp_path) / "nightly_delete_001").glob("deleted__*.json"))
    assert len(archived) == 1
    row = json.loads(archived[0].read_text(encoding="utf-8"))
    assert row["job"]["job_id"] == "nightly_delete_001"
    assert row["purge_history"] is False


def test_delete_job_with_purge_history_removes_state_receipts_and_prompt_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_delete_purge_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Delete me hard.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
        }
    )
    sched._write_json_atomic(
        (sched.receipts_dir(tmp_path) / "nightly_delete_purge_001" / "demo.json").resolve(),
        {"ok": True},
    )
    prompt_dir = sched.job_prompts_dir("nightly_delete_purge_001", tmp_path)
    (prompt_dir / "extra.md").write_text("x", encoding="utf-8")

    result = sched.delete_job("nightly_delete_purge_001", repo_root=tmp_path, purge_history=True)
    assert result["deleted"] is True
    assert not sched._state_path("nightly_delete_purge_001", tmp_path).exists()
    assert not (sched.receipts_dir(tmp_path) / "nightly_delete_purge_001").exists()
    assert not prompt_dir.exists()


def test_delete_job_rejects_active_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_delete_active_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Busy job.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
        }
    )
    sched._write_state(
        "nightly_delete_active_001",
        {"active_run_id": "run_123", "last_status": "running"},
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="active_run_id=run_123"):
        sched.delete_job("nightly_delete_active_001", repo_root=tmp_path)


def test_delete_job_rejects_hidden_live_claim_without_active_run_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_delete_hidden_claim_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mbd"},
            "prompt": "Busy but state lost the active run id.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
        }
    )
    sched._write_state(
        "nightly_delete_hidden_claim_001",
        {"active_run_id": "", "last_status": "running"},
        repo_root=tmp_path,
    )
    sched._write_json_atomic(
        sched._claim_path("nightly_delete_hidden_claim_001", tmp_path),
        {
            "schema_version": "scheduled_job_claim_v1",
            "job_id": "nightly_delete_hidden_claim_001",
            "run_id": "sj_hidden_live_claim",
            "trigger": "scheduler",
            "claimed_utc": sched._utc_iso(),
            "heartbeat_utc": sched._utc_iso(),
            "pid": os.getpid(),
        },
    )

    with pytest.raises(ValueError, match="live runtime claim still exists"):
        sched.delete_job("nightly_delete_hidden_claim_001", repo_root=tmp_path)


def test_scheduled_job_deepseek_backend_runs_through_codex_controller(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        payload = {"role": role, "message": message, "options": dict(options or {})}
        calls.append(payload)
        agent_tech = str((options or {}).get("agent_tech") or "")
        if agent_tech == "deepseek":
            return {
                "ok": True,
                "assistant": {"content": "advisory deepseek draft", "status": "ok", "meta": {"backend": "deepseek"}},
            }
        return {"ok": True, "assistant": {"content": "codex final", "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)

    sched.save_job(
        {
            "job_id": "nightly_codex_controller_deepseek_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a bounded update.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner"},
        }
    )

    result = sched.execute_job("nightly_codex_controller_deepseek_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert [str((row["options"] or {}).get("agent_tech") or "") for row in calls] == ["deepseek", "codex"]
    receipt = sched.load_receipt("nightly_codex_controller_deepseek_001", result["run_id"], repo_root=tmp_path)
    assert receipt["execution_contract"]["controller_runtime"]["agent_tech"] == "codex"
    assert receipt["execution_contract"]["advisory_runtime"]["agent_tech"] == "deepseek"
    assert receipt["runtime_send"]["selected_agent_tech"] == "codex"
    assert receipt["advisory_send"]["selected_agent_tech"] == "deepseek"
    assert receipt["model_telemetry"]["requested_agent_tech"] == "deepseek"


def test_build_verify_loop_retries_builder_with_stable_resend_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[dict[str, object]] = []
    sleeps: list[int] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        payload = {"role": role, "message": message, "options": dict(options or {})}
        calls.append(payload)
        if role == "builder" and len([c for c in calls if str(c["role"]) == "builder"]) == 1:
            raise RuntimeError("transient builder failure")
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied\n"
                    "- remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched.time, "sleep", lambda sec: sleeps.append(int(sec)))
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_retry_loop_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 1, "agent_turn_retry_backoff_seconds": 7},
            "runtime": {"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_retry_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert sleeps == [7]
    builder_calls = [c for c in calls if str(c["role"]) == "builder"]
    verifier_calls = [c for c in calls if str(c["role"]) == "assurer"]
    assert len(builder_calls) == 2
    assert len(verifier_calls) == 1
    assert builder_calls[0]["options"]["resend_key"] == builder_calls[1]["options"]["resend_key"]
    assert str(verifier_calls[0]["options"]["resend_key"]).endswith("::verifier::iter_1")
    receipt = sched.load_receipt("nightly_retry_loop_001", result["run_id"], repo_root=tmp_path)
    iteration = receipt["loop"]["iterations"][0]
    assert iteration["builder"]["runtime_send"]["resend_key"] == builder_calls[0]["options"]["resend_key"]
    assert len(iteration["builder"]["runtime_send"]["attempts"]) == 2


def test_build_verify_loop_gemini_backend_uses_codex_controller_for_each_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    calls: list[tuple[str, str, str]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        agent_tech = str((options or {}).get("agent_tech") or "")
        model = str((options or {}).get("model") or "")
        calls.append((role, agent_tech, model))
        if role == "builder":
            if agent_tech == "gemini":
                return {"ok": True, "assistant": {"content": "gemini builder draft", "status": "ok", "meta": {"backend": "gemini"}}}
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {"backend": "codex"},
                },
            }
        if agent_tech == "gemini":
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nadvisory", "status": "ok", "meta": {"backend": "gemini"}}}
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {"backend": "codex"}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_loop_gemini_backend_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {"agent_tech": "gemini", "model": "gemini-2.5-pro"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_gemini_backend_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert calls == [
        ("builder", "gemini", "gemini-2.5-pro"),
        ("builder", "codex", "gpt-5.4"),
        ("assurer", "gemini", "gemini-2.5-pro"),
        ("assurer", "codex", "gpt-5.4"),
    ]
    receipt = sched.load_receipt("nightly_loop_gemini_backend_001", result["run_id"], repo_root=tmp_path)
    row = receipt["loop"]["iterations"][0]
    assert row["builder"]["runtime_send"]["selected_agent_tech"] == "codex"
    assert row["builder"]["advisory"]["runtime_send"]["selected_agent_tech"] == "gemini"
    assert row["verifier"]["runtime_send"]["selected_agent_tech"] == "codex"
    assert row["verifier"]["advisory"]["runtime_send"]["selected_agent_tech"] == "gemini"


def test_build_verify_loop_falls_back_on_verifier_quota_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[dict[str, object]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        payload = {"role": role, "message": message, "options": dict(options or {})}
        calls.append(payload)
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        model = str((options or {}).get("model") or "")
        if model == "gpt-5.3-codex":
            return {"ok": False, "assistant": {"content": "429 insufficient_quota", "status": "error", "meta": {}}}
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_fallback_loop_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "model": "gpt-5.3-codex",
                "fallback_model": "gpt-5.1",
                "fallback_on_quota": True,
                "reasoning_effort": "medium",
            },
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_fallback_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    verifier_calls = [c for c in calls if str(c["role"]) == "assurer"]
    assert [str(c["options"]["model"]) for c in verifier_calls] == ["gpt-5.3-codex", "gpt-5.1"]
    assert verifier_calls[0]["options"]["resend_key"] != verifier_calls[1]["options"]["resend_key"]
    receipt = sched.load_receipt("nightly_fallback_loop_001", result["run_id"], repo_root=tmp_path)
    iteration = receipt["loop"]["iterations"][0]
    assert iteration["verifier"]["runtime_send"]["selected_model"] == "gpt-5.1"
    assert bool(iteration["verifier"]["runtime_send"]["attempts"][0]["quota_like"]) is True


def test_build_verify_loop_uses_fallback_chain_for_builder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[tuple[str, str]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        model = str((options or {}).get("model") or "")
        calls.append((role, model))
        if role == "builder":
            if model in {"gpt-5.4", "gpt-5.3-codex"}:
                return {
                    "ok": False,
                    "assistant": {"content": "insufficient_quota for requested model", "status": "error", "meta": {}},
                }
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_loop_fallback_chain_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "agent_turn_retries": 0},
            "runtime": {
                "model": "gpt-5.4",
                "fallback_models": ["gpt-5.3-codex", "gpt-5.3-codex-spark"],
                "fallback_on_quota": True,
                "reasoning_effort": "medium",
            },
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_fallback_chain_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    builder_models = [model for role, model in calls if role == "builder"]
    assert builder_models == ["gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"]
    receipt = sched.load_receipt("nightly_loop_fallback_chain_001", result["run_id"], repo_root=tmp_path)
    iteration = receipt["loop"]["iterations"][0]
    assert iteration["builder"]["runtime_send"]["selected_model"] == "gpt-5.3-codex-spark"
    assert [attempt["model"] for attempt in iteration["builder"]["runtime_send"]["attempts"]] == builder_models
    assert str(iteration["verifier"]["runtime_send"]["resend_key_base"]).endswith("::verifier::iter_1")


def test_build_verify_loop_defaults_to_strict_proof_without_blocking_dirty_worktree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    send_calls: list[dict[str, object]] = []

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        send_calls.append({"role": role, "message": message, "options": dict(options or {})})
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: " M infra/controlplane/scheduled_jobs.py")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": True,
            "dirty_preview": " M infra/controlplane/scheduled_jobs.py",
            "commit_journal_pending_count": 1,
            "commit_journal_invalid_count": 0,
            "commit_journal_audit_ok": False,
            "commit_journal_uncovered_paths": ["infra/controlplane/scheduled_jobs.py"],
        },
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_guard_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    saved = sched.load_job("nightly_repo_guard_001", repo_root=tmp_path)
    assert saved["policy"]["repo_change_mode"] == "strict"
    assert saved["policy"]["require_clean_worktree"] is False
    assert saved["policy"]["require_commit_journal"] is True
    assert saved["policy"]["require_validation_evidence"] is True
    assert saved["policy"]["post_success_commit_tag"] is True

    result = sched.execute_job("nightly_repo_guard_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert "skipped" not in result
    assert [row["role"] for row in send_calls] == ["builder", "assurer"]
    receipt = sched.load_receipt("nightly_repo_guard_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["execution_proof"]["proof_gap"] is False
    assert receipt["repo_change_evidence"]["git_dirty"] is True

    state = sched.load_state("nightly_repo_guard_001", repo_root=tmp_path)
    assert state["last_nonlaunch_reason"] == ""
    assert state["last_nonlaunch_status"] == ""


def test_build_verify_loop_repo_change_strict_reports_proof_gap_without_validation_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": "Changed files: infra/controlplane/scheduled_jobs.py", "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nlooks good", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": True,
            "dirty_preview": " M infra/controlplane/scheduled_jobs.py",
            "commit_journal_pending_count": 1,
            "commit_journal_invalid_count": 0,
            "commit_journal_audit_ok": False,
            "commit_journal_uncovered_paths": ["infra/controlplane/scheduled_jobs.py"],
        },
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_proof_gap_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "repo_change_mode": "strict"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_repo_proof_gap_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_repo_proof_gap_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "proof_gap"
    assert receipt["execution_proof"]["proof_gap"] is True
    assert receipt["execution_proof"]["proof_gap_reason"] == "required_validation_commands_missing"
    assert receipt["repo_change_evidence"]["commit_journal_pending_count"] == 1
    assert receipt["validation_claim_hygiene"]["claim_scope"] == "manual_or_missing_validation"
    assert receipt["validation_claim_hygiene"]["manual_only"] is True
    assert "required_validation_commands_missing" in receipt["validation_claim_hygiene"]["remaining_proof_gaps"]
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "- status: proof_gap" in report
    assert "required_validation_commands_missing" in report
    assert "Validation Commands" in report
    assert "Validation Claim Hygiene" in report
    assert "broader subsystem success remains unproven" not in report
    assert "No local validation commands were captured" in report


def test_extract_validation_commands_accepts_bold_heading_style() -> None:
    builder_content = (
        "**Boundary Summary**\n"
        "ok\n\n"
        "**Validation Commands Run**\n"
        "- `python -m py_compile foo.py` (pass)\n"
        "- `pytest -q tests/test_foo.py` (pass)\n\n"
        "**Residual Risks / Proof Gaps**\n"
        "- none\n"
    )

    commands = sched._extract_validation_commands(builder_content)

    assert commands == [
        "`python -m py_compile foo.py` (pass)",
        "`pytest -q tests/test_foo.py` (pass)",
    ]


def test_extract_validation_commands_accepts_plain_heading_and_nested_bullets() -> None:
    builder_content = (
        "Boundary Summary\n"
        "- scope held\n\n"
        "Validation Commands Run\n"
        "- Syntax checks:\n"
        "  - python -m py_compile infra/controlplane/scheduled_jobs.py -- pass\n"
        "- Targeted tests (ran individually for clarity here):\n"
        "  - pytest -q tests/control_plane/test_scheduled_jobs.py::test_extract_validation_commands_accepts_plain_heading_and_nested_bullets -- pass\n\n"
        "Residual Risks / Proof Gaps\n"
        "- none\n"
    )

    commands = sched._extract_validation_commands(builder_content)

    assert commands == [
        "python -m py_compile infra/controlplane/scheduled_jobs.py -- pass",
        "pytest -q tests/control_plane/test_scheduled_jobs.py::test_extract_validation_commands_accepts_plain_heading_and_nested_bullets -- pass",
    ]


def test_extract_validation_commands_accepts_validation_run_heading_variant() -> None:
    builder_content = (
        "### Validation run\n"
        "- find governance/ledger/patch_instruction_sheets -maxdepth 1 -type d | rg 'PIS-20260326-203500Z-(A7D1|B8E2)'\n"
        "- rg -n 'Parent relation|Non-goals|Source of truth|Acceptance|Validation' governance/ledger/patch_instruction_sheets/PIS-20260326-203500Z-*/pis.md\n\n"
        "### Blockers / assumptions\n"
        "- none\n"
    )

    commands = sched._extract_validation_commands(builder_content)

    assert commands == [
        "find governance/ledger/patch_instruction_sheets -maxdepth 1 -type d | rg 'PIS-20260326-203500Z-(A7D1|B8E2)'",
        "rg -n 'Parent relation|Non-goals|Source of truth|Acceptance|Validation' governance/ledger/patch_instruction_sheets/PIS-20260326-203500Z-*/pis.md",
    ]


def test_extract_validation_commands_accepts_local_verification_heading_alias() -> None:
    builder_content = (
        "Goal: reconcile ledger truth.\n\n"
        "Local verification:\n"
        "- `sed -n '1,220p' docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md` `PASS`\n"
        "- `python - <<'PY' ... print statuses for RWVP, D8FB, K2N7, V4H6 ... PY` `PASS`\n"
    )

    commands = sched._extract_validation_commands(builder_content)

    assert commands == [
        "`sed -n '1,220p' docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md` `PASS`",
        "`python - <<'PY' ... print statuses for RWVP, D8FB, K2N7, V4H6 ... PY` `PASS`",
    ]


def test_verifier_loop_control_metadata_extracts_rigid_block_and_marks_present() -> None:
    content = (
        "VERDICT: FAIL\n"
        "Bound parent remains draft because blocking child evidence is unchanged.\n\n"
        "Loop Control:\n"
        "- confidence_percent: 79\n"
        "- confidence_delta_this_iteration: 1\n"
        "- expected_next_iteration_gain: 1\n"
        "- recommended_action: stop\n"
        "- good_enough_for: draft_only\n"
    )

    meta = sched._verifier_loop_control_metadata(content)

    assert meta["confidence_percent"] == 79
    assert meta["confidence_delta_this_iteration"] == 1
    assert meta["expected_next_iteration_gain"] == 1
    assert meta["recommended_action"] == "stop"
    assert meta["good_enough_for"] == "draft_only"
    assert meta["loop_control_header_present"] is True
    assert meta["loop_control_missing"] is False
    assert meta["loop_control_parse_error"] is False
    assert meta["loop_control_detected_fields"]["expected_next_iteration_gain"] is True


def test_verifier_loop_control_metadata_marks_missing_when_reconcile_fields_omitted() -> None:
    content = (
        "VERDICT: FAIL\n"
        "Bound parent remains draft because blocking child evidence is unchanged.\n"
    )

    meta = sched._verifier_loop_control_metadata(content)

    assert meta["confidence_percent"] is None
    assert meta["expected_next_iteration_gain"] is None
    assert meta["recommended_action"] == ""
    assert meta["loop_control_header_present"] is False
    assert meta["loop_control_missing"] is True
    assert meta["loop_control_parse_error"] is False


def test_enforce_verifier_loop_control_injects_continue_defaults_on_first_contract_miss(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    iterations = [
        {
            "iteration": 1,
            "builder": {
                "content": "Changed files:\n- governance/ledger/patch_instruction_sheets/PIS-20260327-124630Z-RWVP__parent/meta.json\n\nValidation Commands Run:\n- pytest -q tests/foo.py\n",
            },
            "verifier": {
                "content": "VERDICT: FAIL\nBound parent remains draft because blocking child evidence is unchanged.\n",
                "meta": {
                    "loop_control": sched._verifier_loop_control_metadata(
                        "VERDICT: FAIL\nBound parent remains draft because blocking child evidence is unchanged.\n"
                    )
                },
            },
        }
    ]
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
    }

    sched._enforce_verifier_loop_control(spec, iterations, repo_root=tmp_path)

    loop_control = iterations[0]["verifier"]["meta"]["loop_control"]
    assert loop_control["loop_control_contract_violation"] is True
    assert loop_control["loop_control_fallback_source"] == "contract_default_continue"
    assert loop_control["confidence_percent"] == 50
    assert loop_control["confidence_delta_this_iteration"] == 0
    assert loop_control["expected_next_iteration_gain"] == 1
    assert loop_control["recommended_action"] == "continue"
    assert loop_control["loop_control_missing"] is False
    assert loop_control["loop_control_parse_error"] is False


def test_verifier_loop_control_metadata_marks_parse_error_for_invalid_values() -> None:
    content = (
        "VERDICT: FAIL\n\n"
        "Loop Control:\n"
        "- confidence_percent: maybe\n"
        "- confidence_delta_this_iteration: 1\n"
        "- expected_next_iteration_gain: low\n"
        "- recommended_action: pause\n"
        "- good_enough_for: draftish\n"
    )

    meta = sched._verifier_loop_control_metadata(content)

    assert meta["loop_control_header_present"] is True
    assert meta["loop_control_missing"] is False
    assert meta["loop_control_parse_error"] is True
    assert meta["confidence_percent"] is None
    assert meta["expected_next_iteration_gain"] is None
    assert meta["recommended_action"] == ""


def test_enforce_verifier_loop_control_injects_stop_fallback_on_repeated_same_blocker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_rel = "governance/ledger/patch_instruction_sheets/PIS-20260327-124630Z-RWVP__parent"
    builder_content = (
        "Changed files:\n"
        f"- {parent_rel}/meta.json\n"
        f"- {parent_rel}/artifacts/status_reconcile_verification.json\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    verifier_content = "VERDICT: FAIL\nBound parent remains draft because blocking child evidence is unchanged.\n"
    iterations = [
        {
            "iteration": 1,
            "builder": {"content": builder_content},
            "verifier": {"content": verifier_content, "meta": {"loop_control": sched._verifier_loop_control_metadata(verifier_content)}},
        },
        {
            "iteration": 2,
            "builder": {"content": builder_content},
            "verifier": {"content": verifier_content, "meta": {"loop_control": sched._verifier_loop_control_metadata(verifier_content)}},
        },
    ]
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
    }

    sched._enforce_verifier_loop_control(spec, iterations, repo_root=tmp_path)

    loop_control = iterations[-1]["verifier"]["meta"]["loop_control"]
    assert loop_control["loop_control_contract_violation"] is True
    assert loop_control["loop_control_fallback_source"] == "repeated_same_blocker_stop"
    assert loop_control["confidence_percent"] == 90
    assert loop_control["confidence_delta_this_iteration"] == 0
    assert loop_control["expected_next_iteration_gain"] == 0
    assert loop_control["recommended_action"] == "stop"
    assert loop_control["good_enough_for"] == "draft_only"


def test_status_reconcile_scope_violation_accepts_parent_only_reconcile_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
    }
    builder_content = (
        "Changed files:\n"
        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
        f"- {parent_dir.relative_to(tmp_path).as_posix()}/artifacts/status_reconcile_verification.json\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    result = sched._status_reconcile_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is False
    assert result["off_target_paths"] == []


def test_status_reconcile_scope_violation_accepts_parent_only_markdown_and_file_wrapped_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
    }
    meta_abs = str((parent_dir / "meta.json").resolve())
    events_rel = f"{parent_dir.relative_to(tmp_path).as_posix()}/events.jsonl"
    artifact_abs = str((parent_dir / "artifacts" / "status_reconcile_verification.json").resolve())
    builder_content = (
        "Changed files:\n"
        f"- [meta.json]({meta_abs})\n"
        f"- <file>{events_rel}</file>\n"
        f"- [status_reconcile_verification.json]({artifact_abs})\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    changed = sched._extract_changed_files(builder_content, repo_root=tmp_path)
    assert changed == [
        f"{parent_dir.relative_to(tmp_path).as_posix()}/meta.json",
        f"{parent_dir.relative_to(tmp_path).as_posix()}/events.jsonl",
        f"{parent_dir.relative_to(tmp_path).as_posix()}/artifacts/status_reconcile_verification.json",
    ]
    result = sched._status_reconcile_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is False
    assert result["off_target_paths"] == []


def test_status_reconcile_scope_violation_rejects_child_and_scheduler_mutations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    child_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124631Z-D8FB__child"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    child_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
        "prompt": (
            "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
            "Judge the bound item itself. Read only the files needed to decide."
        ),
    }
    builder_content = (
        "Changed files:\n"
        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
        f"- {child_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
        "- tests/control_plane/test_scheduled_jobs.py\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    result = sched._status_reconcile_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is True
    assert sorted(result["off_target_paths"]) == sorted(
        [
            f"{child_dir.relative_to(tmp_path).as_posix()}/meta.json",
            "tests/control_plane/test_scheduled_jobs.py",
        ]
    )


def test_execution_family_closeout_scope_violation_accepts_declared_family_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260406-182612Z-BEB9__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260406-183447Z-1246__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-4529__intent"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (intent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260406-182612Z-BEB9"},
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": "PIS-20260406-182612Z-BEB9",
                "child_uids": ["PIS-20260406-183447Z-1246"],
                "source_intent_uid": "IS-20260403-212913Z-4529",
            }
        },
        "prompt": "Close the execution family rooted at `PIS-20260406-182612Z-BEB9` only when the durable child-job evidence honestly supports closure.",
    }
    builder_content = (
        "Changed files:\n"
        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
        f"- {child_dir.relative_to(tmp_path).as_posix()}/events.jsonl\n"
        f"- {intent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
        "- governance/ledger/patch_instruction_sheets/uid_index.json\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    result = sched._execution_family_closeout_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is False
    assert result["off_target_paths"] == []


def test_execution_family_closeout_scope_violation_accepts_family_receipt_changed_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state(
        "job_child_001",
        {
            "last_status": "ok",
            "last_receipt_path": str((sched.receipts_dir(tmp_path) / "job_child_001" / "sj_demo_001.json").resolve()),
        },
        repo_root=tmp_path,
    )
    receipt_path = sched.receipts_dir(tmp_path) / "job_child_001" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(
        receipt_path,
        {
            "repo_residue": {
                "declared_changed_files": [
                    "infra/controlplane/job_budget_cost_ledger_store.py",
                    "tests/control_plane/test_job_budget_cost_ledger_store.py",
                ]
            }
        },
    )
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260406-182612Z-BEB9"},
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": "PIS-20260406-182612Z-BEB9",
                "child_uids": ["PIS-20260406-183447Z-1246"],
                "child_job_ids": ["job_child_001"],
                "source_intent_uid": "IS-20260403-212913Z-4529",
            }
        },
        "prompt": "Audit, integrate, and close the execution family rooted at `PIS-20260406-182612Z-BEB9` only when the durable child-job evidence honestly supports closure.",
    }
    builder_content = (
        "Changed files:\n"
        "- infra/controlplane/job_budget_cost_ledger_store.py\n"
        "- tests/control_plane/test_job_budget_cost_ledger_store.py\n\n"
        "Validation Commands Run:\n"
        "- pytest -q tests/control_plane/test_job_budget_cost_ledger_store.py\n"
    )
    result = sched._execution_family_closeout_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is False
    assert result["off_target_paths"] == []


def test_execution_family_closeout_scope_violation_rejects_unrelated_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260406-182612Z-BEB9"},
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": "PIS-20260406-182612Z-BEB9",
                "child_uids": ["PIS-20260406-183447Z-1246"],
                "source_intent_uid": "IS-20260403-212913Z-4529",
            }
        },
        "prompt": "Close the execution family rooted at `PIS-20260406-182612Z-BEB9` only when the durable child-job evidence honestly supports closure.",
    }
    builder_content = (
        "Changed files:\n"
        "- governance/ledger/patch_instruction_sheets/uid_index.json\n"
        "- tests/control_plane/test_scheduled_jobs.py\n\n"
        "Validation Commands Run:\n"
        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
    )
    result = sched._execution_family_closeout_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is True
    assert result["off_target_paths"] == ["tests/control_plane/test_scheduled_jobs.py"]


def test_execution_family_closeout_scope_violation_ignores_changed_files_none_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260406-182612Z-BEB9"},
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": "PIS-20260406-182612Z-BEB9",
                "child_uids": ["PIS-20260406-183447Z-1246"],
                "source_intent_uid": "IS-20260403-212913Z-4529",
            }
        },
        "prompt": "Close the execution family rooted at `PIS-20260406-182612Z-BEB9` only when the durable child-job evidence honestly supports closure.",
    }
    builder_content = (
        "Changed files:\n"
        "- `none`\n\n"
        "Validation Commands Run:\n"
        "- pytest -q tests/control_plane/test_scheduled_jobs.py\n"
    )
    assert sched._extract_changed_files(builder_content, repo_root=tmp_path) == []
    result = sched._execution_family_closeout_scope_violation(spec, builder_content=builder_content, repo_root=tmp_path)
    assert result["violated"] is False
    assert result["off_target_paths"] == []


def _write_family_meta(dir_path: Path, payload: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "meta.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    (dir_path / "pis.md").write_text(f"# {payload.get('title', payload.get('uid', 'item'))}\n", encoding="utf-8")
    (dir_path / "intent_sheet.md").write_text(f"# {payload.get('title', payload.get('uid', 'item'))}\n", encoding="utf-8")


def _write_family_events(dir_path: Path, events: list[dict[str, object]]) -> None:
    (dir_path / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + ("\n" if events else ""),
        encoding="utf-8",
    )


def test_execution_family_closeout_rejects_pass_when_required_child_job_not_successful(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "draft"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "failed_verification"},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("close_1de7_20260407_execution_family", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert receipt["loop"]["iterations"][-1]["verifier"]["status"] == "truth_rejected"
    errors = receipt["loop"]["iterations"][-1]["verifier"]["meta"]["execution_family_closeout_truth"]["errors"]
    assert "child_job_not_success:pis_z8t9_20260407_prove-resource-concurrency-gover:failed_verification" in errors


def test_execution_family_closeout_rejects_pass_when_required_child_chronology_is_invalid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "draft"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:45Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_z8t9_20260407_prove-resource-concurrency-gover" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_bad_chronology",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_bad_chronology", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_bad_chronology", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    errors = receipt["loop"]["iterations"][-1]["verifier"]["meta"]["execution_family_closeout_truth"]["errors"]
    assert "required_final_child_event_chronology_invalid:PIS-20260407-043008Z-Z8T9" in errors


def test_execution_family_closeout_accepts_pass_when_family_truth_is_coherent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "draft"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "status_reconcile_reviewed", "payload": {"verdict": "pass_verified_dependency_truth"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_z8t9_20260407_prove-resource-concurrency-gover" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_good_truth",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_good_truth", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_good_truth", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"


def test_execution_family_closeout_delivery_guaranteed_terminalizes_parent_integrity_defect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "draft"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "draft",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "draft",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )
    _write_family_events(
        parent_dir,
        [
            {
                "schema_version": "ledger_event_v1",
                "event_id": "evt-1",
                "utc": "2026-04-07T08:07:46Z",
                "type": "family_parent_reconciled",
                "payload": {"closeout_child_uid": "PIS-20260407-043008Z-Z8T9", "status": "verified"},
                "prev_hash": "",
                "hash": "corrupt",
            }
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_z8t9_20260407_prove-resource-concurrency-gover" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_integrity_defect",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "delivery_policy": "delivery_guaranteed"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_integrity_defect", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_integrity_defect", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "executed_with_closeout_integrity_defect"
    assert receipt["loop"]["iterations"][-1]["verifier"]["status"] == "closeout_integrity_defect"
    assert "parent_event_chain_invalid:PIS-20260407-041735Z-1DE7:line_1_hash_mismatch" in receipt["execution_family_closeout_integrity_defect"]["errors"]
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    assert parent_meta["evidence"]["status_recommendation"] == "executed_with_closeout_integrity_defect"
    assert parent_meta["integration_meta"]["family_execution_status"] == "executed_with_closeout_integrity_defect"


def test_execution_family_closeout_delivery_guaranteed_keeps_child_truth_failures_as_findings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "draft"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:45Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_z8t9_20260407_prove-resource-concurrency-gover" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_bad_chronology_delivery",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "delivery_policy": "delivery_guaranteed"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_bad_chronology_delivery", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_bad_chronology_delivery", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "delivered_with_findings"
    assert receipt["delivery_outcome"]["source_terminal_status"] == "failed_verification"
    assert "execution_family_closeout_integrity_defect" not in receipt


def test_execution_family_closeout_accepts_split_validation_and_closeout_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__validation/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043009Z-Q5T8__closeout/meta.json\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nLooks good.", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    validation_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__validation"
    closeout_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043009Z-Q5T8__closeout"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (validation_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (closeout_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (validation_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    (closeout_dir / "artifacts" / "verification" / "resource_concurrency_family_closeout.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(
        parent_dir,
        {
            "uid": "PIS-20260407-041735Z-1DE7",
            "title": "Parent",
            "status": "verified",
            "integration_meta": {
                "intent_validation_child_uid": "PIS-20260407-043008Z-Z8T9",
                "required_final_child_uid": "PIS-20260407-043009Z-Q5T8",
                "required_final_child_kind": "intent_family_closeout",
            },
        },
    )
    _write_family_meta(
        validation_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Validation Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": ["artifacts/verification/resource_concurrency_intent_validation_summary.md"],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
            },
        },
    )
    _write_family_meta(
        closeout_dir,
        {
            "uid": "PIS-20260407-043009Z-Q5T8",
            "title": "Closeout Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:08:46Z",
            "required_evidence": ["artifacts/verification/resource_concurrency_family_closeout.md"],
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        closeout_dir,
        [
            {"utc": "2026-04-07T08:08:46Z", "type": "status_reconcile_reviewed", "payload": {"verdict": "pass_verified_dependency_truth"}},
            {"utc": "2026-04-07T08:08:46Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:08:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:08:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:08:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_q5t8_20260407_closeout" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_q5t8_20260407_closeout",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_split_truth",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043009Z-Q5T8",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9", "PIS-20260407-043009Z-Q5T8"],
                    "child_job_ids": ["pis_q5t8_20260407_closeout"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_split_truth", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_split_truth", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"


def test_execution_family_closeout_recovers_after_truth_rejection_with_remaining_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-041735Z-1DE7__parent/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/meta.json\n"
                        "- governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260407-043008Z-Z8T9__child/events.jsonl\n"
                        "- governance/ledger/intent_sheets/causyl/active/2026/04/IS-20260403-212913Z-60F4__intent/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {
            "ok": True,
            "assistant": {
                "content": _valid_loop_verifier_pass(
                    command="pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-041735Z-1DE7__parent"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04" / "PIS-20260407-043008Z-Z8T9__child"
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "04" / "IS-20260403-212913Z-60F4__intent"
    (child_dir / "artifacts" / "verification").mkdir(parents=True, exist_ok=True)
    (child_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json").write_text("{}", encoding="utf-8")
    (child_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").write_text("# ok\n", encoding="utf-8")
    _write_family_meta(parent_dir, {"uid": "PIS-20260407-041735Z-1DE7", "title": "Parent", "status": "active"})
    _write_family_meta(
        child_dir,
        {
            "uid": "PIS-20260407-043008Z-Z8T9",
            "title": "Final Child",
            "status": "verified",
            "updated_utc": "2026-04-07T08:07:46Z",
            "required_evidence": [
                "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "artifacts/verification/resource_concurrency_dependency_gate.json",
            ],
            "evidence": {
                "resource_concurrency_intent_validation_summary": "artifacts/verification/resource_concurrency_intent_validation_summary.md",
                "resource_concurrency_dependency_gate": "artifacts/verification/resource_concurrency_dependency_gate.json",
            },
        },
    )
    _write_family_meta(
        intent_dir,
        {
            "uid": "IS-20260403-212913Z-60F4",
            "title": "Intent",
            "status": "verified",
            "validating_pis_uids": ["PIS-20260407-043008Z-Z8T9"],
        },
    )
    _write_family_events(
        child_dir,
        [
            {"utc": "2026-04-07T08:07:46Z", "type": "status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:45Z", "type": "verification_checked", "payload": {"result": "pass"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "ledger_status_changed", "payload": {"status": "verified"}},
            {"utc": "2026-04-07T08:07:46Z", "type": "meta_updated", "payload": {}},
        ],
    )

    receipt_path = sched.receipts_dir(tmp_path) / "pis_z8t9_20260407_prove-resource-concurrency-gover" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(receipt_path, {"status": "ok"})
    sched._write_state(
        "pis_z8t9_20260407_prove-resource-concurrency-gover",
        {"last_status": "ok", "last_receipt_path": str(receipt_path.resolve())},
        repo_root=tmp_path,
    )

    original_truth_check = sched._execution_family_closeout_truth_check

    def _patched_truth_check(spec: dict, *, repo_root: Path | None = None) -> dict:
        if seen["verifier"] <= 1:
            return {
                "ok": False,
                "errors": ["required_final_child_event_chronology_invalid:PIS-20260407-043008Z-Z8T9"],
                "details": {},
            }
        return {"ok": True, "errors": [], "details": {}}

    monkeypatch.setattr(sched, "_execution_family_closeout_truth_check", _patched_truth_check)

    sched.save_job(
        {
            "job_id": "close_1de7_20260407_execution_family_recovery",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
            "prompt": "Close the execution family rooted at `PIS-20260407-041735Z-1DE7` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T08:20:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260407-041735Z-1DE7",
                    "source_intent_uid": "IS-20260403-212913Z-60F4",
                    "required_final_child_uid": "PIS-20260407-043008Z-Z8T9",
                    "child_uids": ["PIS-20260407-043008Z-Z8T9"],
                    "child_job_ids": ["pis_z8t9_20260407_prove-resource-concurrency-gover"],
                }
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("close_1de7_20260407_execution_family_recovery", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-07T08:20:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("close_1de7_20260407_execution_family_recovery", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["loop"]["iterations"][0]["verifier"]["status"] == "truth_rejected"
    assert len(receipt["loop"]["iterations"]) == 2
    assert seen["builder"] == 2
    assert seen["verifier"] == 2


def test_save_job_allows_existing_job_prompt_ref_to_be_replaced_by_new_inline_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "prompt_refresh_existing_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Original prompt body.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T10:00:00Z", "timezone": "UTC"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    original_ref = str(spec.get("prompt_ref") or "")
    original_sha = str(spec.get("prompt_sha256") or "")

    updated = sched.save_job(
        {
            **sched.load_job("prompt_refresh_existing_001", repo_root=tmp_path),
            "prompt": "Updated prompt body with materially different content.",
            "prompt_ref": "",
            "prompt_sha256": "",
            "prompt_preview": "",
        },
        repo_root=tmp_path,
    )

    assert str(updated.get("prompt_ref") or "") != original_ref
    assert str(updated.get("prompt_sha256") or "") != original_sha
    assert "Updated prompt body" in (tmp_path / str(updated["prompt_ref"])).read_text(encoding="utf-8")


def test_build_verify_loop_repo_change_policy_none_cannot_bypass_validation_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": "Changed files: infra/controlplane/scheduled_jobs.py", "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nlooks good", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": False,
            "dirty_preview": "",
            "commit_journal_pending_count": 0,
            "commit_journal_invalid_count": 0,
            "commit_journal_audit_ok": True,
            "commit_journal_uncovered_paths": [],
        },
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_policy_none_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 900,
                "repo_change_mode": "none",
                "require_validation_evidence": False,
                "require_clean_worktree": False,
                "require_commit_journal": False,
            },
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    saved = sched.load_job("nightly_repo_policy_none_001", repo_root=tmp_path)
    assert saved["policy"]["repo_change_mode"] == "strict"
    assert saved["policy"]["require_validation_evidence"] is True
    assert saved["policy"]["require_clean_worktree"] is False
    assert saved["policy"]["require_commit_journal"] is True
    assert saved["policy"]["post_success_commit_tag"] is True

    result = sched.execute_job("nightly_repo_policy_none_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_repo_policy_none_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "proof_gap"
    assert receipt["execution_proof"]["required_validation_evidence"] is True
    assert receipt["execution_proof"]["proof_gap"] is True
    assert receipt["execution_proof"]["proof_gap_reason"] == "required_validation_commands_missing"


def test_build_verify_loop_repo_change_strict_accepts_explicit_validation_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "## Validation Commands Run\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied - remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": False,
            "dirty_preview": "",
            "commit_journal_pending_count": 0,
            "commit_journal_invalid_count": 0,
            "commit_journal_audit_ok": True,
            "commit_journal_uncovered_paths": [],
        },
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_proof_ok_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "repo_change_mode": "strict"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_repo_proof_ok_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_repo_proof_ok_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["execution_proof"]["proof_gap"] is False
    assert receipt["execution_proof"]["validation_command_count"] == 1
    assert receipt["execution_proof"]["validation_commands"][0] == "python -m py_compile infra/controlplane/scheduled_jobs.py"
    assert receipt["validation_claim_hygiene"]["claim_scope"] == "bounded_to_explicit_validation_commands"
    assert receipt["validation_claim_hygiene"]["evidence_scope"] == "targeted_commands_only"
    assert receipt["validation_claim_hygiene"]["broad_success_claim_allowed"] is False
    assert "broader_subsystem_or_end_to_end_behavior_not_proven" in receipt["validation_claim_hygiene"]["remaining_proof_gaps"]
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Repo Change Evidence" in report
    assert "Validation Claim Hygiene" in report
    assert "Only the exact validation commands listed are in evidence; broader subsystem success remains unproven." in report


def test_build_verify_loop_runtime_error_is_not_downgraded_into_proof_gap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _scheduled_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": False,
                "assistant": {
                    "content": "controller runtime failed before structured builder output was emitted",
                    "status": "error",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nverified", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_scheduled_message", _scheduled_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": False,
            "dirty_preview": "",
            "commit_journal_pending_count": 0,
            "commit_journal_invalid_count": 0,
            "commit_journal_audit_ok": True,
            "commit_journal_uncovered_paths": [],
        },
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_runtime_error_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "repo_change_mode": "strict"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_repo_runtime_error_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_repo_runtime_error_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "error"
    assert receipt["error"] == "controller runtime failed before structured builder output was emitted"
    assert receipt["execution_proof"]["required_validation_evidence"] is True
    assert receipt["execution_proof"]["validation_command_count"] == 0
    assert receipt["execution_proof"]["proof_gap"] is False
    assert receipt["execution_proof"]["proof_gap_reason"] == ""


def test_build_verify_loop_strict_does_not_block_on_invalid_commit_journal_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_build_repo_change_evidence",
        lambda **kwargs: {
            "git_dirty": False,
            "dirty_preview": "",
            "commit_journal_pending_count": 1,
            "commit_journal_invalid_count": 2,
            "commit_journal_audit_ok": False,
            "commit_journal_uncovered_paths": [],
        },
    )
    monkeypatch.setattr(
        sched._commit_journal,
        "scan_pending_entries",
        lambda **kwargs: {"pending_count": 1, "invalid_count": 2},
    )

    sched.save_job(
        {
            "job_id": "nightly_repo_commit_journal_warn_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "repo_change_mode": "strict"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job(
        "nightly_repo_commit_journal_warn_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_repo_commit_journal_warn_001", str(result["run_id"]), repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["repo_change_evidence"]["commit_journal_invalid_count"] == 2
    state = sched.load_state("nightly_repo_commit_journal_warn_001", repo_root=tmp_path)
    assert state["last_nonlaunch_reason"] == ""


def test_scheduled_job_claim_blocks_duplicate_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "claimed_job_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    claim_path = sched.locks_dir(tmp_path) / "claimed_job_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "claimed_job_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:59:30Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.execute_job("claimed_job_001", trigger="scheduler", now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    assert result["skipped"] is True
    assert result["reason"] == "claimed_elsewhere"


def test_scheduler_tick_skips_job_already_submitted_in_dispatch_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "duplicate_dispatch_guard_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Say hi.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "note", "format": "markdown"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    future: concurrent.futures.Future[Any] = concurrent.futures.Future()
    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS["duplicate_dispatch_guard_001"] = future
        sched._DISPATCH_ACTIVE_JOB_META["duplicate_dispatch_guard_001"] = {
            "submitted_utc": sched._utc_iso(),
            "repo_root": str(tmp_path.resolve()),
        }
    try:
        result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    finally:
        future.set_result({"ok": True})
        sched._get_active_job_count()

    assert result["submitted_count"] == 0
    assert {"job_id": "duplicate_dispatch_guard_001", "reason": "already_dispatched"} in result["skipped"]
    assert sched.list_execution_events() == []


def test_get_active_job_count_drops_orphaned_dispatch_future_without_durable_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "orphaned_dispatch_guard_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Say hi.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "note", "format": "markdown"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T04:00:00Z"))
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()
    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS["orphaned_dispatch_guard_001"] = future
        sched._DISPATCH_ACTIVE_JOB_ROLES["orphaned_dispatch_guard_001"] = "generalist"
        sched._DISPATCH_ACTIVE_JOB_META["orphaned_dispatch_guard_001"] = {"submitted_utc": "2026-03-08T03:58:00Z"}

    assert sched._get_active_job_count() == 0
    assert "orphaned_dispatch_guard_001" not in sched._DISPATCH_ACTIVE_JOBS
    assert "orphaned_dispatch_guard_001" not in sched._DISPATCH_ACTIVE_JOB_ROLES
    assert "orphaned_dispatch_guard_001" not in sched._DISPATCH_ACTIVE_JOB_META


def test_execute_job_terminal_state_reconciles_leftover_claim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "terminal_claim_cleanup_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Say hi.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "note", "format": "markdown"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    original_release = sched._release_scheduled_job_runtime_claim_with_receipt
    monkeypatch.setattr(
        sched,
        "_release_scheduled_job_runtime_claim_with_receipt",
        lambda **kwargs: {"release_error": "simulated_primary_release_failure"},
    )
    try:
        result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    finally:
        monkeypatch.setattr(sched, "_release_scheduled_job_runtime_claim_with_receipt", original_release)

    assert result["executed_count"] == 1
    state = sched.load_state("terminal_claim_cleanup_001", repo_root=tmp_path)
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    assert not (sched.locks_dir(tmp_path) / "terminal_claim_cleanup_001.lock.json").exists()


def test_scheduled_job_stale_claim_is_recovered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "stale_claim_job_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )

    claim_path = sched.locks_dir(tmp_path) / "stale_claim_job_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "stale_claim_job_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.execute_job("stale_claim_job_001", trigger="scheduler", now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert claim_path.exists() is False


def test_scheduled_job_live_claim_with_recent_heartbeat_is_not_stolen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "live_claim_job_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )

    claim_path = sched.locks_dir(tmp_path) / "live_claim_job_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "live_claim_job_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T00:00:00Z",
                "heartbeat_utc": "2026-03-08T03:59:30Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.execute_job("live_claim_job_001", trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    assert result["skipped"] is True
    assert result["reason"] == "claimed_elsewhere"
    claim = sched._read_json(claim_path)
    assert claim["run_id"] == "sj_existing"
    assert sched.list_execution_events() == []


def test_list_jobs_projects_fresh_claim_health_for_active_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "claim_health_live_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Report live heartbeat health.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )
    sched._write_state(
        "claim_health_live_001",
        {
            "job_id": "claim_health_live_001",
            "active_run_id": "sj_live",
            "active_started_utc": "2026-03-08T03:59:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    (sched.locks_dir(tmp_path) / "claim_health_live_001.lock.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "claim_health_live_001",
                "run_id": "sj_live",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:59:00Z",
                "heartbeat_utc": "2026-03-08T03:59:45Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    listed = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    state = listed[0]["state"]
    assert state["active_claim_present"] is True
    assert state["active_claim_stale"] is False
    assert state["active_claim_reason"] == "fresh"
    assert state["active_claim_heartbeat_utc"] == "2026-03-08T03:59:45Z"
    assert float(state["active_claim_age_s"]) == 15.0


def test_list_jobs_projects_missing_claim_as_stale_for_active_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "claim_health_missing_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Report missing heartbeat health.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )
    sched._write_state(
        "claim_health_missing_001",
        {
            "job_id": "claim_health_missing_001",
            "active_run_id": "sj_missing",
            "active_started_utc": "2026-03-08T03:59:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    listed = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    state = listed[0]["state"]
    assert state["active_claim_present"] is False
    assert state["active_claim_stale"] is True
    assert state["active_claim_reason"] == "missing"
    assert state["active_claim_heartbeat_utc"] == ""
    assert state["active_claim_age_s"] is None


def test_claim_heartbeat_refreshes_active_loop_timestamp_only_when_claim_touch_succeeds(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class _OneShotEvent:
        def __init__(self) -> None:
            self._calls = 0

        def wait(self, _interval_s: int) -> bool:
            self._calls += 1
            return self._calls > 1

        def set(self) -> None:
            self._calls = 99

    class _ImmediateThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

        def join(self, timeout: float | None = None) -> None:
            return None

    monkeypatch.setattr(sched.threading, "Event", _OneShotEvent)
    monkeypatch.setattr(sched.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(sched, "_append_event", lambda **kwargs: None)

    monkeypatch.setattr(sched, "_touch_job_claim", lambda job_id, **kwargs: job_id == "heartbeat_ok_001")
    monkeypatch.setattr(
        sched,
        "_touch_active_loop_state",
        lambda job_id, *, run_id, repo_root=None, now=None: calls.append((job_id, run_id)) or {"job_id": job_id, "active_run_id": run_id},
    )

    ok_heartbeat = sched._ClaimHeartbeat(
        job_id="heartbeat_ok_001",
        repo_root=None,
        run_id="sj_ok",
        interval_s=1,
    )
    ok_heartbeat.start()
    ok_heartbeat.stop()

    blocked_heartbeat = sched._ClaimHeartbeat(
        job_id="heartbeat_blocked_001",
        repo_root=None,
        run_id="sj_blocked",
        interval_s=1,
    )
    blocked_heartbeat.start()
    blocked_heartbeat.stop()

    assert calls == [("heartbeat_ok_001", "sj_ok")]


def test_list_jobs_light_skips_context_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "list_jobs_light_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Light listing.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )

    def _boom(_spec, *args, **kwargs):
        raise AssertionError("context resolution should be skipped in light list mode")

    monkeypatch.setattr(sched, "_effective_context_refs", _boom)
    monkeypatch.setattr(sched, "_job_shape_context_obligation_projection", _boom)

    listed = sched.list_jobs(
        repo_root=tmp_path,
        now_utc="2026-03-08T04:00:00Z",
        include_context_resolution=False,
    )

    assert len(listed) == 1
    assert listed[0]["job_id"] == "list_jobs_light_001"
    assert listed[0]["effective_context_refs"] == []
    assert listed[0]["job_shape_context"] == {}


def test_list_jobs_light_skips_reconciliation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "list_jobs_light_002",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Light listing.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )

    def _boom(*args, **kwargs):
        raise AssertionError("reconciliation should be skipped in light list mode")

    monkeypatch.setattr(sched, "_build_scheduler_reconciliation", _boom)

    listed = sched.list_jobs(
        repo_root=tmp_path,
        now_utc="2026-03-08T04:00:00Z",
        include_context_resolution=False,
        include_reconciliation=False,
    )

    assert len(listed) == 1
    assert listed[0]["job_id"] == "list_jobs_light_002"
    assert listed[0]["reconciliation"] == {}


def test_scheduler_live_watch_job_title_and_summary_uses_prompt_preview_fallback() -> None:
    from infra.bin import scheduler_live_watch as watch

    title, summary = watch._job_title_and_summary(
        {
            "job_id": "watch_fallback_001",
            "prompt_preview": "Fallback title line\nMore detail below",
            "binding": {"type": "task_id", "id": "mb3"},
        }
    )

    assert title == "Fallback title line"
    assert summary == "Fallback title line"


def test_scheduler_live_watch_job_title_and_summary_prefers_display_title_over_bound_parent_title(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    monkeypatch.setattr(watch, "_pis_title_and_summary", lambda uid: ("Experiment Control Action Policy", "parent summary"))
    title, summary = watch._job_title_and_summary(
        {
            "job_id": "close_6aac_20260408_execution_family",
            "display_title": "6aac execution family closeout",
            "binding": {"type": "ledger_uid", "id": "PIS-20260408-071120Z-6AAC"},
            "prompt_preview": "Audit, integrate, and close the execution family rooted at 6AAC.",
        }
    )

    assert title == "6aac execution family closeout"
    assert summary == "Audit, integrate, and close the execution family rooted at 6AAC."


def test_scheduler_live_watch_active_family_scope_line_reports_intent_and_remaining(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    meta_by_uid = {
        "PIS-20260407-041735Z-1DE7": {
            "uid": "PIS-20260407-041735Z-1DE7",
            "slug": "resource-concurrency-governa",
            "integration_meta": {
                "child_uids": [
                    "PIS-20260407-043001Z-R2K1",
                    "PIS-20260407-043002Z-S4M2",
                    "PIS-20260407-043008Z-Z8T9",
                ],
                "intent_sheet_uid": "IS-20260403-212913Z-60F4",
            },
        },
        "IS-20260403-212913Z-60F4": {
            "uid": "IS-20260403-212913Z-60F4",
            "slug": "resource-classes-and-scheduler-concurrency-governance",
        },
        "PIS-20260407-043001Z-R2K1": {"uid": "PIS-20260407-043001Z-R2K1", "status": "verified"},
        "PIS-20260407-043002Z-S4M2": {"uid": "PIS-20260407-043002Z-S4M2", "status": "draft"},
        "PIS-20260407-043008Z-Z8T9": {"uid": "PIS-20260407-043008Z-Z8T9", "status": "draft"},
    }

    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: dict(meta_by_uid.get(str(uid).upper(), {})))

    line = watch._active_family_scope_line(
        {
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
        },
        rows=[
            {"binding": {"type": "ledger_uid", "id": "PIS-20260407-043001Z-R2K1"}, "state": {"last_status": "ok"}},
            {"binding": {"type": "ledger_uid", "id": "PIS-20260407-043002Z-S4M2"}, "state": {"last_status": "running"}},
            {"binding": {"type": "ledger_uid", "id": "PIS-20260407-043008Z-Z8T9"}, "state": {"last_status": ""}},
        ],
    )

    assert line == {
        "pis_uid4": "1DE7",
        "intent_uid4": "60F4",
        "progress": "PIS implemented: 1 of 4, verified: 1 of 4 (1DE7 in progress)",
    }


def test_scheduler_live_watch_active_family_scope_line_marks_verified_family_rerun(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    meta_by_uid = {
        "PIS-20260407-041735Z-1DE7": {
            "uid": "PIS-20260407-041735Z-1DE7",
            "status": "verified",
            "integration_meta": {
                "child_uids": [
                    "PIS-20260407-043001Z-R2K1",
                    "PIS-20260407-043002Z-S4M2",
                ],
                "intent_sheet_uid": "IS-20260403-212913Z-60F4",
            },
        },
        "IS-20260403-212913Z-60F4": {"uid": "IS-20260403-212913Z-60F4", "status": "verified"},
        "PIS-20260407-043001Z-R2K1": {"uid": "PIS-20260407-043001Z-R2K1", "status": "verified"},
        "PIS-20260407-043002Z-S4M2": {"uid": "PIS-20260407-043002Z-S4M2", "status": "verified"},
    }

    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: dict(meta_by_uid.get(str(uid).upper(), {})))

    line = watch._active_family_scope_line(
        {
            "job_id": "close_1de7_20260407_execution_family",
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-041735Z-1DE7"},
        },
        rows=[
            {"binding": {"type": "ledger_uid", "id": "PIS-20260407-043001Z-R2K1"}, "state": {"last_status": "ok"}},
            {"binding": {"type": "ledger_uid", "id": "PIS-20260407-043002Z-S4M2"}, "state": {"last_status": "ok"}},
        ],
    )

    assert line == {
        "pis_uid4": "1DE7",
        "intent_uid4": "60F4",
        "progress": "verified family rerun: 1DE7 (family already implemented and verified)",
    }


def test_scheduler_live_watch_job_family_identifier_labels_child_pis_with_intent_sheet(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    meta_by_uid = {
        "PIS-20260409-081310Z-E085": {
            "uid": "PIS-20260409-081310Z-E085",
            "integration_meta": {
                "child_uids": [
                    "PIS-20260409-082100Z-A1F1",
                    "PIS-20260409-082101Z-B2G2",
                    "PIS-20260409-082102Z-C3H3",
                ],
                "implementation_child_uids": [
                    "PIS-20260409-082100Z-A1F1",
                    "PIS-20260409-082101Z-B2G2",
                    "PIS-20260409-082102Z-C3H3",
                ],
                "intent_sheet_uid": "IS-20260403-212914Z-EA50",
            },
        },
        "PIS-20260409-082101Z-B2G2": {
            "uid": "PIS-20260409-082101Z-B2G2",
            "integration_meta": {
                "parent_uid": "PIS-20260409-081310Z-E085",
                "intent_sheet_uid": "IS-20260403-212914Z-EA50",
            },
        },
        "IS-20260403-212914Z-EA50": {
            "uid": "IS-20260403-212914Z-EA50",
            "integration_meta": {"sheet_id": "IS-09"},
        },
    }

    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: dict(meta_by_uid.get(str(uid).upper(), {})))

    label = watch._job_family_identifier(
        {
            "job_id": "implement_a1f1_20260409_child",
            "binding": {"type": "ledger_uid", "id": "PIS-20260409-082101Z-B2G2"},
            "display_title": "Define Child Fanout Work Order Context Bundle Loading and Return Packet Contract",
            "prompt_preview": "Implement child work order contract.",
        }
    )

    assert label == "[PIS 2 of 3 | IS-09]"


def test_scheduler_live_watch_job_family_identifier_labels_family_closeout(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    meta_by_uid = {
        "PIS-20260409-081310Z-E085": {
            "uid": "PIS-20260409-081310Z-E085",
            "tags": ["IS-09"],
        },
    }

    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: dict(meta_by_uid.get(str(uid).upper(), {})))

    label = watch._job_family_identifier(
        {
            "job_id": "close_e085_20260409_execution_family",
            "binding": {"type": "ledger_uid", "id": "PIS-20260409-081310Z-E085"},
            "display_title": "E085 execution family closeout",
            "prompt_preview": "Audit, integrate, and close the execution family rooted at E085.",
        }
    )

    assert label == "[Family Closeout | IS-09]"


def test_scheduler_live_watch_pipeline_job_line_includes_family_identifier(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    monkeypatch.setattr(watch, "_job_family_identifier", lambda row: "[PIS Parent | IS-09]")

    lines = watch._pipeline_job_lines(
        {
            "job_id": "implement_e085_20260409_parent",
            "enabled": True,
            "display_title": "Decompose parent ledger item into child PIS work orders",
            "state": {"last_status": "queued"},
        },
        index=3,
        tz_name="UTC",
        color=False,
        truth={"status": "queued"},
    )

    assert lines == ["  03. [queued] [enabled] [PIS Parent | IS-09] Decompose parent ledger item into child PIS work orders"]


def test_scheduler_live_watch_compact_job_line_places_family_identifier_before_title(monkeypatch) -> None:
    from infra.bin import scheduler_live_watch as watch

    monkeypatch.setattr(watch, "_job_family_identifier", lambda row: "[Intent Closeout 7 of 7 | IS-10]")

    line = watch._compact_job_line(
        {
            "job_id": "q5t8_closeout",
            "display_title": "Close IS-10 Intent Boundary And Family Acceptance",
            "state": {"last_status": "blocked"},
        },
        index=13,
        tz_name="UTC",
        truth={"status": "blocked"},
    )

    assert line == "13. [blocked] [Intent Closeout 7 of 7 | IS-10] Close IS-10 Intent Boundary And Family Acceptance"


def test_scheduler_live_watch_watchdog_busy_line_surfaces_real_intervention() -> None:
    from infra.bin import scheduler_live_watch as watch

    line = watch._watchdog_busy_line(
        {
            "running": True,
            "started_utc": "2026-04-08T05:46:51Z",
            "last_receipt_utc": "2026-04-08T05:30:00Z",
            "last_receipt": {
                "actions": [
                    {
                        "action": "record_playbook_decision",
                    },
                    {
                        "action": "resume_stalled_dependency_head",
                        "job_id": "job_abc_1234",
                        "ok": True,
                    },
                ]
            }
        }
    )

    assert line == "resuming job_abc_1234"


def test_scheduler_live_watch_watchdog_busy_line_shows_running_codex_launch() -> None:
    from infra.bin import scheduler_live_watch as watch

    line = watch._watchdog_busy_line(
        {
            "running": True,
            "started_utc": "2026-04-08T05:46:51Z",
            "last_receipt_utc": "2026-04-08T05:30:00Z",
            "last_receipt": {
                "actions": [
                    {
                        "action": "execute_bounded_codex_escalation",
                        "ok": True,
                        "condition_id": "repeat_watchdog_job_problem",
                    }
                ]
            },
        }
    )

    assert line == "codex launched"


def test_scheduler_live_watch_watchdog_busy_line_uses_finding_hint_when_no_action() -> None:
    from infra.bin import scheduler_live_watch as watch

    line = watch._watchdog_busy_line(
        {
            "running": True,
            "started_utc": "2026-04-08T05:46:51Z",
            "last_receipt_utc": "2026-04-08T05:30:00Z",
            "last_receipt": {
                "findings": [
                    {
                        "kind": "dependency_blocked_chain",
                    }
                ]
            },
        }
    )

    assert line == "audit blocked chain"


def test_scheduler_live_watch_watchdog_busy_line_suppresses_monitor_only_actions() -> None:
    from infra.bin import scheduler_live_watch as watch

    line = watch._watchdog_busy_line(
        {
            "last_receipt": {
                "actions": [
                    {"action": "record_playbook_decision"},
                    {"action": "record_incident"},
                    {"action": "record_prediction"},
                    {"action": "refresh_delegation_proof"},
                ]
            }
        }
    )

    assert line == ""


def test_scheduler_live_watch_watchdog_busy_line_hides_stale_receipt_when_idle() -> None:
    from infra.bin import scheduler_live_watch as watch

    line = watch._watchdog_busy_line(
        {
            "running": True,
            "started_utc": "2026-04-08T05:46:51Z",
            "last_receipt_utc": "2026-04-08T05:50:00Z",
            "last_receipt": {
                "findings": [
                    {
                        "kind": "stale_scheduler_task_register",
                    }
                ]
            },
        }
    )

    assert line == ""


def test_scheduler_live_watch_next_in_line_omits_active_job() -> None:
    from infra.bin import scheduler_live_watch as watch

    payload = {
        "jobs": [
                {
                    "job_id": "pis_h8k8_20260407_prove-continuation-packet-and-lo",
                    "enabled": True,
                    "binding": {"type": "ledger_uid", "id": "PIS-20260407-172958Z-H8K8"},
                    "title": "Prove Continuation Packet and Long-Horizon Memory Contract End-to-End",
                    "prompt_preview": "Prove Continuation Packet and Long-Horizon Memory Contract End-to-End\nMore detail",
                    "state": {
                        "active_run_id": "sj_active_001",
                        "last_status": "running",
                        "active_loop_iteration": 2,
                    "active_loop_stage": "builder",
                },
                "loop": {"max_iterations": 1},
                "job_kind": "build_verify_loop",
            },
                {
                    "job_id": "close_3826_20260407_execution_family",
                    "enabled": True,
                    "binding": {"type": "ledger_uid", "id": "PIS-20260407-055533Z-3826"},
                    "title": "Continuation Packet and Long-Horizon Memory Contract",
                    "prompt_preview": "Continuation Packet and Long-Horizon Memory Contract\nClose the family",
                    "state": {"last_status": "failed_verification"},
                    "due": {"reason": "dependency_pending:pis_h8k8_20260407_prove-continuation-packet-and-lo"},
                    "loop": {"max_iterations": 1},
                    "job_kind": "build_verify_loop",
            },
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-04-07T19:43:24Z"},
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-04-07T19:31:06Z"},
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-04-07T19:41:15Z"},
    }

    rendered = watch._render(
        "http://localhost:8787",
        payload,
        tz_name="Europe/Vienna",
        frame=0,
        color=False,
        include_done=False,
        pipeline_limit=5,
        prefix="",
        verbose=False,
    )

    assert "NEXT IN LINE (showing 1)" in rendered
    assert "ACTIVE\n  01. [active]" in rendered
    assert "(H8K8)" in rendered
    assert "NEXT IN LINE (showing 1)\n  01. [blocked] [Family Closeout | IS-06]" in rendered
    assert "(3826)" in rendered
    assert rendered.count("(H8K8)") == 1


def test_scheduler_tick_blocks_stale_active_run_without_claim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "recover_stale_active_run_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )
    sched._write_state(
        "recover_stale_active_run_001",
        {
            "job_id": "recover_stale_active_run_001",
            "active_run_id": "sj_orphaned",
            "active_started_utc": "2026-03-08T02:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 0
    assert result["skipped"] == [{"job_id": "recover_stale_active_run_001", "reason": "active_run"}]

    state = sched.load_state("recover_stale_active_run_001", repo_root=tmp_path)
    assert state["active_run_id"] == "sj_orphaned"
    assert state["last_status"] == "running"

    events = sched.list_execution_events(repo_root=tmp_path)
    assert all(row["event"] != "active_run_recovered" for row in events)


def test_scheduler_tick_records_stale_claim_cleanup_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    sched.save_job(
        {
            "job_id": "cleanup_stale_claim_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )
    claim_path = sched._claim_path("cleanup_stale_claim_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "cleanup_stale_claim_001",
                "run_id": "sj_stale_claim",
                "pid": 999999,
                "claimed_utc": "2026-03-08T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(
        row["job_id"] == "cleanup_stale_claim_001"
        and row["event"] == "stale_claim_cleaned"
        and row["run_id"] == "sj_stale_claim"
        for row in events
    )


def test_active_claim_health_marks_future_probe_utc_stale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched.save_job(
        {
            "job_id": "future_probe_claim_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )
    claim_path = sched._claim_path("future_probe_claim_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "future_probe_claim_001",
                "run_id": "sj_future_probe",
                "pid": os.getpid(),
                "claimed_utc": "2099-01-01T00:00:00Z",
                "heartbeat_utc": "2099-01-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    health = sched._active_claim_health(
        "future_probe_claim_001",
        repo_root=tmp_path,
        max_runtime_seconds=60,
        expected_run_id="sj_future_probe",
        now=sched._parse_utc("2026-03-08T04:00:00Z"),
    )

    assert health["stale"] is True
    assert health["reason"] == "future_probe_utc"


def test_scheduler_tick_keeps_recent_missing_claim_active_run_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "retain_recent_active_run_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )
    sched._write_state(
        "retain_recent_active_run_001",
        {
            "job_id": "retain_recent_active_run_001",
            "active_run_id": "sj_recent",
            "active_started_utc": "2026-03-08T03:59:40Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 0
    assert result["skipped"] == [{"job_id": "retain_recent_active_run_001", "reason": "active_run"}]

    state = sched.load_state("retain_recent_active_run_001", repo_root=tmp_path)
    assert state["active_run_id"] == "sj_recent"
    events = sched.list_execution_events(repo_root=tmp_path)
    assert all(row["event"] != "active_run_recovered" for row in events)


def test_scheduler_tick_keeps_missing_active_claim_blocked_after_grace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "recover_missing_claim_grace_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
        }
    )
    sched._write_state(
        "recover_missing_claim_grace_001",
        {
            "job_id": "recover_missing_claim_grace_001",
            "active_run_id": "sj_missing_claim",
            "active_started_utc": "2026-03-08T03:58:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 0
    assert result["skipped"] == [{"job_id": "recover_missing_claim_grace_001", "reason": "active_run"}]

    state = sched.load_state("recover_missing_claim_grace_001", repo_root=tmp_path)
    assert state["active_run_id"] == "sj_missing_claim"
    assert state["last_status"] == "running"


def test_scheduler_tick_clears_hidden_claim_without_active_run_before_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "hidden_claim_launch_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )
    sched._write_state(
        "hidden_claim_launch_001",
        {
            "job_id": "hidden_claim_launch_001",
            "active_run_id": "",
            "last_status": "",
            "run_now_requested_utc": "2026-03-08T03:59:00Z",
        },
        repo_root=tmp_path,
    )
    claim_path = sched.locks_dir(tmp_path) / "hidden_claim_launch_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "hidden_claim_launch_001",
                "run_id": "sj_hidden",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:59:00Z",
                "heartbeat_utc": "2026-03-08T03:59:00Z",
                "pid": os.getpid(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")

    assert result["executed_count"] == 1
    assert result["executed"][0]["ok"] is True
    assert claim_path.exists() is False
    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(row["event"] == "hidden_claim_cleaned" for row in events)


def test_scheduler_tick_does_not_clear_fresh_hidden_claim_without_active_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "hidden_claim_fresh_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )
    sched._write_state(
        "hidden_claim_fresh_001",
        {
            "job_id": "hidden_claim_fresh_001",
            "active_run_id": "",
            "last_status": "",
            "run_now_requested_utc": "2026-03-08T03:59:45Z",
        },
        repo_root=tmp_path,
    )
    claim_path = sched.locks_dir(tmp_path) / "hidden_claim_fresh_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "hidden_claim_fresh_001",
                "run_id": "sj_hidden_fresh",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:59:45Z",
                "heartbeat_utc": "2026-03-08T03:59:45Z",
                "pid": os.getpid(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")

    assert result["executed_count"] == 0
    assert claim_path.exists() is True
    events = sched.list_execution_events(repo_root=tmp_path)
    assert all(row["event"] != "hidden_claim_cleaned" for row in events)


def test_scheduler_tick_does_not_count_claim_collision_as_executed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "collision_job_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    claim_path = sched.locks_dir(tmp_path) / "collision_job_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "collision_job_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:59:30Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 0
    assert result["executed"] == []
    assert result["skipped"] == [{"job_id": "collision_job_001", "reason": "claimed_elsewhere"}]


def test_scheduled_job_role_aliases_route_to_runtime_role_and_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    captured: list[dict[str, object]] = []

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        captured.append(
            {
                "role": role,
                "binding": dict(binding),
                "message": message,
                "options": dict(options or {}),
            }
        )
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)

    sched.save_job(
        {
            "job_id": "orchestrator_job_001",
            "enabled": True,
            "role": "orchestrator",
            "binding": {"type": "task_id", "id": "mor"},
            "prompt": "Review the project state.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )
    sched.save_job(
        {
            "job_id": "verifier_job_001",
            "enabled": True,
            "role": "verifier",
            "binding": {"type": "task_id", "id": "mvr"},
            "prompt": "Audit the latest change.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:31:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    orch = sched.execute_job("orchestrator_job_001", trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    ver = sched.execute_job("verifier_job_001", trigger="manual", force=True, now_utc="2026-03-08T04:01:00Z")

    assert orch["ok"] is True
    assert ver["ok"] is True
    assert captured[0]["role"] == "generalist"
    assert captured[0]["options"]["timeout_s"] == 600
    assert captured[0]["options"]["prompt_file"] == "role__orchestrator_mobile.md"
    assert str(captured[0]["options"]["resend_key"]).startswith("scheduled_job::orchestrator_job_001::")
    assert captured[1]["role"] == "assurer"
    assert captured[1]["options"]["timeout_s"] == 600
    assert captured[1]["options"]["prompt_file"] == "role__verifier_mobile.md"
    assert str(captured[1]["options"]["resend_key"]).startswith("scheduled_job::verifier_job_001::")

    orch_receipt = sched.load_receipt("orchestrator_job_001", orch["run_id"])
    ver_receipt = sched.load_receipt("verifier_job_001", ver["run_id"])
    assert orch_receipt["role"] == "orchestrator"
    assert orch_receipt["runtime_role"] == "generalist"
    assert ver_receipt["role"] == "verifier"
    assert ver_receipt["runtime_role"] == "assurer"


def test_scheduled_build_verify_loop_runs_until_verifier_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[dict[str, object]] = []
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "binding": dict(binding), "message": message, "options": dict(options or {})})
        if role == "builder":
            seen["builder"] += 1
            content = _valid_loop_builder_output()
            return {"ok": True, "assistant": {"content": content, "status": "ok", "meta": {}}}
        seen["verifier"] += 1
        verdict = "VERDICT: FAIL\nNeed one more pass." if seen["verifier"] == 1 else _valid_loop_verifier_pass()
        return {"ok": True, "assistant": {"content": verdict, "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 1
    run = result["executed"][0]
    assert run["ok"] is True

    receipt = sched.load_receipt("nightly_loop_001", run["run_id"], repo_root=tmp_path)
    assert receipt["job_kind"] == "build_verify_loop"
    assert receipt["status"] == "ok"
    assert receipt["loop"]["completed_iterations"] == 2


def test_build_verify_loop_policy_defaults_to_default_conditional_repair_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_loop_policy_default_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "required_context_bundle": "lab_core_v1",
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    loaded = sched.load_job("nightly_loop_policy_default_001", repo_root=tmp_path)
    assert loaded["loop"]["loop_policy"] == "default_conditional_repair_loop"


def test_build_verify_loop_defaults_max_iterations_to_one_for_conditional_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_loop_default_iterations_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "required_context_bundle": "lab_core_v1",
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    loaded = sched.load_job("nightly_loop_default_iterations_001", repo_root=tmp_path)
    assert loaded["loop"]["loop_policy"] == "default_conditional_repair_loop"
    assert loaded["loop"]["max_iterations"] == 1


def test_build_verify_repair_alias_maps_to_default_conditional_repair_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_loop_policy_bvr_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "required_context_bundle": "lab_core_v1",
            "policy": {"max_runtime_seconds": 600},
            "loop": {
                "loop_policy": "build_verify_repair",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 1,
                "pass_marker": "VERDICT: PASS",
            },
        },
        repo_root=tmp_path,
    )

    loaded = sched.load_job("nightly_loop_policy_bvr_001", repo_root=tmp_path)
    assert loaded["loop"]["loop_policy"] == "default_conditional_repair_loop"


def test_one_point_five_iterations_alias_maps_to_default_conditional_repair_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "nightly_loop_policy_one_point_five_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "required_context_bundle": "lab_core_v1",
            "policy": {"max_runtime_seconds": 600},
            "loop": {
                "loop_policy": "1.5_iterations",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 1,
                "pass_marker": "VERDICT: PASS",
            },
        },
        repo_root=tmp_path,
    )

    loaded = sched.load_job("nightly_loop_policy_one_point_five_001", repo_root=tmp_path)
    assert loaded["loop"]["loop_policy"] == "default_conditional_repair_loop"
    assert loaded["loop"]["max_iterations"] == 1


def test_full_build_verify_loop_policy_opt_in_preserves_two_verifier_cycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[str] = []
    seen = {"builder": 0, "verifier": 0}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(role)
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        f"builder iteration {seen['builder']}\n\n"
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
        }
        seen["verifier"] += 1
        verdict = "VERDICT: FAIL\nNeed one more pass." if seen["verifier"] == 1 else _valid_loop_verifier_pass()
        return {"ok": True, "assistant": {"content": verdict, "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_full_build_verify_loop_policy_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "required_context_bundle": "lab_core_v1",
            "policy": {"max_runtime_seconds": 600},
            "loop": {
                "loop_policy": "full_build_verify_loop",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_full_build_verify_loop_policy_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_full_build_verify_loop_policy_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["loop"]["loop_policy"] == "full_build_verify_loop"
    assert calls == ["builder", "assurer", "builder", "assurer"]


def test_default_conditional_pass_pending_actions_happy_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[str] = []
    pending_actions = [
        "Update `infra/controlplane/scheduled_jobs.py` to record the repair status in the loop receipt.",
        "Add a focused `pending_actions` regression in `tests/control_plane/test_scheduled_jobs.py`.",
    ]

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(role)
        if role == "builder" and len(calls) == 1:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n"
                        "- tests/control_plane/test_scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py tests/control_plane/test_scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: PASS_PENDING_ACTIONS\n"
                        "Findings:\n"
                        "- low | pending repair actions\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: pending repair actions only\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py tests/control_plane/test_scheduled_jobs.py\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        f"- {pending_actions[0]}\n"
                        f"- {pending_actions[1]}\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- APPLIED: {pending_actions[0]}\n"
                    f"- APPLIED: {pending_actions[1]}\n\n"
                    "Changed files:\n"
                    "- infra/controlplane/scheduled_jobs.py\n"
                    "- tests/control_plane/test_scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py tests/control_plane/test_scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_conditional_pass_pending_actions_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_conditional_pass_pending_actions_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_conditional_pass_pending_actions_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "conditional_pass_repair_applied"
    assert receipt["loop"]["loop_policy"] == "default_conditional_repair_loop"
    assert receipt["loop"]["conditional_repair_applied"] is True
    assert receipt["loop"]["final_verifier_confirmation"] is False
    assert receipt["loop"]["iterations"][1]["verifier"]["status"] == "waived_after_pending_actions"
    assert calls == ["builder", "assurer", "builder"]


def test_default_conditional_fail_with_bounded_repair_contract_is_promoted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[str] = []
    pending_action = "Update `infra/controlplane/scheduled_jobs.py` to record the repair status in the loop receipt."

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(role)
        if role == "builder" and len(calls) == 1:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: FAIL\n"
                        "Findings:\n"
                        "- low | one bounded repair remains\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: pending repair actions only\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        f"- {pending_action}\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- APPLIED: {pending_action}\n\n"
                    "Changed files:\n"
                    "- infra/controlplane/scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_conditional_fail_promoted_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_conditional_fail_promoted_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_conditional_fail_promoted_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "conditional_pass_repair_applied"
    first_meta = receipt["loop"]["iterations"][0]["verifier"]["meta"]
    assert first_meta["conditional_fail_promoted_to_pending_actions"] is True
    assert calls == ["builder", "assurer", "builder"]


def test_default_conditional_fail_without_terminal_blockers_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                    "content": (
                        "VERDICT: FAIL\n"
                        "Findings:\n"
                        "- medium | this should not terminate without a bounded repair or terminal blockers\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: insufficient\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: missing terminal blocker or bounded repair contract\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: insufficient\n"
                    ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_conditional_fail_invalid_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_conditional_fail_invalid_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_conditional_fail_invalid_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert "invalid_conditional_fail_contract" in str(receipt.get("error") or "")


def test_pass_pending_actions_vague_pending_actions_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    calls: list[str] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(role)
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                    "content": (
                        "VERDICT: PASS_PENDING_ACTIONS\n"
                        "Findings:\n"
                        "- low | pending repair actions\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: vague pending action remains\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        "- improve reasoning\n"
                    "Stop Rule:\n"
                    "- one builder repair turn only\n"
                    "- no scope widening\n"
                    "- if builder cannot complete all actions, fail honestly\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_pending_actions_vague_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_pending_actions_vague_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_pending_actions_vague_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert "invalid PASS_PENDING_ACTIONS contract" in receipt["error"]
    assert calls == ["builder", "assurer"]


def test_pass_pending_actions_repair_turn_scope_widening_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    pending_action = "Update `infra/controlplane/scheduled_jobs.py` to record the repair status in the loop receipt."

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder" and "Pending Actions:" not in message:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: PASS_PENDING_ACTIONS\n"
                        "Findings:\n"
                        "- low | pending repair actions\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: pending repair actions only\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        f"- {pending_action}\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- APPLIED: {pending_action}\n\n"
                    "Changed files:\n"
                    "- tests/control_plane/test_scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile tests/control_plane/test_scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_pending_actions_scope_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_pending_actions_scope_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_pending_actions_scope_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert receipt["loop"]["iterations"][1]["verifier"]["status"] == "repair_rejected"
    assert "repair_scope_widening" in "\n".join(receipt["loop"]["iterations"][1]["verifier"]["meta"]["repair_errors"])


def test_pass_pending_actions_ignores_non_repo_slash_phrase_in_pending_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    pending_action = (
        "Extend `infra/mobile/android/app/src/main/java/com/eiel/chat/data/AdminApi.kt` and its snapshot "
        "models/tests so `work_items` parses the backend contract fields."
    )

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            if "Pending Actions:" in message:
                return {
                    "ok": True,
                    "assistant": {
                        "content": (
                            "Pending Action Status:\n"
                            f"- APPLIED: {pending_action}\n\n"
                            "Changed files:\n"
                            "- infra/mobile/android/app/src/main/java/com/eiel/chat/data/AdminApi.kt\n\n"
                            "Validation Commands Run:\n"
                            "- cd infra/mobile/android && ./gradlew app:testDebugUnitTest --tests com.eiel.chat.ui.AdminApiSnapshotSourceOfTruthTest\n"
                        ),
                        "status": "ok",
                        "meta": {},
                    },
                }
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/mobile/android/app/src/main/java/com/eiel/chat/data/AdminApi.kt\n\n"
                        "Validation Commands Run:\n"
                        "- cd infra/mobile/android && ./gradlew app:testDebugUnitTest --tests com.eiel.chat.ui.AdminApiSnapshotSourceOfTruthTest\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS_PENDING_ACTIONS\n"
                    "Findings:\n"
                    "- low | pending repair actions\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: pending_actions\n"
                    "- pass_fail_closed_check: satisfied\n"
                    "- remaining_proof_gaps: pending repair actions only\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: cd infra/mobile/android && ./gradlew app:testDebugUnitTest --tests com.eiel.chat.ui.AdminApiSnapshotSourceOfTruthTest\n"
                    "- outcome_support: sufficient after pending actions\n\n"
                    "Pending Actions:\n"
                    f"- {pending_action}\n"
                    "Stop Rule:\n"
                    "- one builder repair turn only\n"
                    "- no scope widening\n"
                    "- if builder cannot complete all actions, fail honestly\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_pending_actions_nonrepo_phrase_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_pending_actions_nonrepo_phrase_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_pending_actions_nonrepo_phrase_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "conditional_pass_repair_applied"
    contract = receipt["loop"]["iterations"][1]["verifier"]["meta"]["pending_actions_contract"]
    assert contract["ok"] is True
    assert "models/tests" not in list(contract.get("allowed_paths") or [])


def test_pass_pending_actions_repair_turn_partial_failure_is_non_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    pending_action = "Update `infra/controlplane/scheduled_jobs.py` to record the repair status in the loop receipt."

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder" and "Pending Actions:" not in message:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: PASS_PENDING_ACTIONS\n"
                        "Findings:\n"
                        "- low | pending repair actions\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: pending repair actions only\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: cd infra/mobile/android && ./gradlew app:testDebugUnitTest --tests com.eiel.chat.ui.AdminApiSnapshotSourceOfTruthTest\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        f"- {pending_action}\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- NOT APPLIED: {pending_action} | reason: validation failed\n\n"
                    "Changed files:\n"
                    "- infra/controlplane/scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_pending_actions_partial_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_pending_actions_partial_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("nightly_pending_actions_partial_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert receipt["loop"]["iterations"][1]["verifier"]["status"] == "repair_rejected"
    assert "pending_action_not_applied" in "\n".join(receipt["loop"]["iterations"][1]["verifier"]["meta"]["repair_errors"])


def test_pass_pending_actions_repair_turn_preserves_contract_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    pending_action = "Update `infra/controlplane/scheduled_jobs.py` to preserve pending action contract metadata."

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder" and "Pending Actions:" not in message:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: PASS_PENDING_ACTIONS\n"
                        "Findings:\n"
                        "- low | pending repair actions\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: pending_actions\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: pending repair actions only\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: sufficient after pending actions\n\n"
                        "Pending Actions:\n"
                        f"- {pending_action}\n"
                        "Allowed Paths:\n"
                        "- infra/controlplane/scheduled_jobs.py\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- APPLIED: {pending_action}\n\n"
                    "Changed files:\n"
                    "- infra/controlplane/scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_pending_actions_contract_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_pending_actions_contract_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_pending_actions_contract_001", result["run_id"], repo_root=tmp_path)
    waived_meta = receipt["loop"]["iterations"][-1]["verifier"]["meta"]
    contract = waived_meta["pending_actions_contract"]
    assert contract["ok"] is True
    assert contract["verdict"] == sched.PASS_PENDING_ACTIONS_VERDICT
    assert contract["pending_actions"] == [pending_action]
    assert contract["allowed_paths"] == ["infra/controlplane/scheduled_jobs.py"]
    assert contract["errors"] == []


def test_default_conditional_pass_pending_actions_no_second_verifier_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}
    pending_action = "Update `infra/controlplane/scheduled_jobs.py` to record the repair status in the loop receipt."

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder" and "Pending Actions:" not in message:
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        if role == "assurer":
            seen["verifier"] += 1
            return {
                "ok": True,
                "assistant": {
                        "content": (
                            "VERDICT: PASS_PENDING_ACTIONS\n"
                            "Findings:\n"
                            "- low | pending repair actions\n\n"
                            "Boundary Review:\n"
                            "- scope_alignment: pass\n"
                            "- contract_alignment: pass\n\n"
                            "Proof Review:\n"
                            "- evidence_status: pending_actions\n"
                            "- pass_fail_closed_check: satisfied\n"
                            "- remaining_proof_gaps: pending repair actions only\n\n"
                            "Validation Review:\n"
                            "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                            "- outcome_support: sufficient after pending actions\n\n"
                            "Pending Actions:\n"
                            f"- {pending_action}\n"
                        "Stop Rule:\n"
                        "- one builder repair turn only\n"
                        "- no scope widening\n"
                        "- if builder cannot complete all actions, fail honestly\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["builder"] += 1
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Pending Action Status:\n"
                    f"- APPLIED: {pending_action}\n\n"
                    "Changed files:\n"
                    "- infra/controlplane/scheduled_jobs.py\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_conditional_pass_no_second_verifier_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_conditional_pass_no_second_verifier_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert seen == {"builder": 2, "verifier": 1}



def test_due_status_treats_conditional_pass_as_success_for_once_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "conditional_pass_due_status_once_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    state = {
        "last_status": "conditional_pass_repair_applied",
        "last_slot_key": "2026-03-08T03:30:00Z",
    }
    due = sched.due_status(spec, state=state, now=sched._parse_utc("2026-03-08T04:00:00Z"), repo_root=tmp_path)
    assert due["eligible"] is False
    assert due["reason"] == "already_executed"



def test_delivery_guaranteed_builder_only_succeeds_without_verifier(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None, **kwargs) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nunused", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_delivery_builder_only_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260430-120000Z-BD05"},
            "prompt": "Implement with builder only.",
            "schedule": {"mode": "once", "run_at": "2026-04-30T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "delivery_policy": "delivery_guaranteed"},
            "loop": {"builder_role": "builder", "builder_only": True, "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_delivery_builder_only_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-30T10:05:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_delivery_builder_only_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "delivered_builder_only"
    assert receipt["loop"]["builder_only"] is True
    assert seen == {"builder": 1, "verifier": 0}


def test_delivery_guaranteed_verifier_fail_delivers_with_findings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None, **kwargs) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {
            "ok": True,
            "assistant": {"content": "VERDICT: FAIL\nBuilder output is not fully convincing.", "status": "ok", "meta": {}},
        }

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_delivery_with_findings_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260430-120000Z-F1N4"},
            "prompt": "Deliver even if verifier rejects.",
            "schedule": {"mode": "once", "run_at": "2026-04-30T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "delivery_policy": "delivery_guaranteed"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_delivery_with_findings_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-30T10:05:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_delivery_with_findings_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "delivered_with_findings"
    assert receipt["delivery_outcome"]["source_terminal_status"] == "failed_verification"
    assert seen == {"builder": 1, "verifier": 1}


def test_delivery_guaranteed_proof_gap_delivers_unverified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None, **kwargs) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files:\n- infra/controlplane/scheduled_jobs.py\n",
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {"content": "VERDICT: PASS\nLooks good.", "status": "ok", "meta": {}},
        }

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_delivery_unverified_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260430-120000Z-G4P1"},
            "prompt": "Deliver even if validation proof is incomplete.",
            "schedule": {"mode": "once", "run_at": "2026-04-30T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "delivery_policy": "delivery_guaranteed"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("nightly_delivery_unverified_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-04-30T10:05:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_delivery_unverified_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "delivered_unverified"
    assert receipt["delivery_outcome"]["source_terminal_status"] == "proof_gap"
    assert receipt["execution_proof"]["proof_gap"] is True


def test_due_status_keeps_reconciled_once_job_already_executed_after_run_at_rewrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "reconciled_once_due_status_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": "PIS-20260407-202102Z-M4U3"},
            "prompt": "Reconciled once job should stay closed.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T04:27:10Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    state = {
        "last_status": "ok",
        "last_reconciliation_reason": "already_executed",
        "last_slot_key": "2026-04-07T20:37:45Z",
        "last_run_id": "sj_20260407T220518Z_e8306f",
    }
    due = sched.due_status(spec, state=state, now=sched._parse_utc("2026-04-08T07:15:00Z"), repo_root=tmp_path)
    assert due["eligible"] is False
    assert due["reason"] == "already_executed"
    assert due["slot_key"] == "2026-04-08T04:27:10Z"


def test_due_status_treats_conditional_pass_dependency_as_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state("dependency_conditional_pass_001", {"last_status": "conditional_pass_repair_applied"}, repo_root=tmp_path)
    spec = sched.save_job(
        {
            "job_id": "conditional_pass_dependency_ready_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "depends_on": ["dependency_conditional_pass_001"],
            "binding": {"type": "task_id", "id": "dep"},
            "prompt": "Dependency gate check.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    due = sched.due_status(spec, state={}, now=sched._parse_utc("2026-03-08T04:00:00Z"), repo_root=tmp_path)
    assert due["eligible"] is True
    assert due["reason"] == "due"


def test_due_status_treats_delivery_terminal_dependency_as_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state("dependency_delivery_findings_001", {"last_status": "delivered_with_findings"}, repo_root=tmp_path)
    spec = sched.save_job(
        {
            "job_id": "delivery_dependency_ready_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "depends_on": ["dependency_delivery_findings_001"],
            "binding": {"type": "task_id", "id": "dep2"},
            "prompt": "Dependency gate check.",
            "schedule": {"mode": "once", "run_at": "2026-04-30T10:00:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    due = sched.due_status(spec, state={}, now=sched._parse_utc("2026-04-30T10:05:00Z"), repo_root=tmp_path)
    assert due["eligible"] is True
    assert due["reason"] == "due"


def test_due_status_blocks_build_verify_loop_running_without_claim_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    spec = sched.save_job(
        {
            "job_id": "build_verify_missing_claim_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260414-000000Z-TEST"},
            "prompt": "Implement bounded slice.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    state = {
        "job_id": "build_verify_missing_claim_001",
        "active_run_id": "sj_missing_claim",
        "active_started_utc": "2026-03-08T03:58:00Z",
        "last_status": "running",
    }
    due = sched.due_status(spec, state=state, now=sched._parse_utc("2026-03-08T04:00:00Z"), repo_root=tmp_path)
    assert due["eligible"] is False
    assert due["reason"] == "active_run"


def test_scheduled_build_verify_loop_rejects_off_target_status_reconcile_writes_then_recovers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    child_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124631Z-D8FB__child"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    child_dir.mkdir(parents=True, exist_ok=True)
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            if seen["builder"] == 1:
                content = (
                    "Changed files:\n"
                    f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
                    f"- {child_dir.relative_to(tmp_path).as_posix()}/pis.md\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                )
            else:
                content = (
                    "Changed files:\n"
                    f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
                    f"- {parent_dir.relative_to(tmp_path).as_posix()}/artifacts/status_reconcile_verification.json\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                )
            return {"ok": True, "assistant": {"content": content, "status": "ok", "meta": {}}}
        seen["verifier"] += 1
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_loop_scope_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
            "prompt": (
                "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
                "Judge the bound item itself. Read only the files needed to decide."
            ),
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_scope_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True

    receipt = sched.load_receipt("nightly_loop_scope_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["loop"]["completed_iterations"] == 2
    assert receipt["loop"]["iterations"][0]["verifier"]["status"] == "scope_rejected"
    assert receipt["loop"]["iterations"][0]["verifier"]["meta"]["error"] == "off_target_scope"
    assert receipt["loop"]["iterations"][0]["verifier"]["meta"]["off_target_paths"] == [f"{child_dir.relative_to(tmp_path).as_posix()}/pis.md"]
    assert receipt["loop"]["iterations"][1]["verifier"]["content"].startswith("VERDICT: PASS")
    assert seen["verifier"] == 1


def test_scheduled_build_verify_loop_can_economic_stop_status_reconcile_on_repeated_low_gain_fail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
                        f"- {parent_dir.relative_to(tmp_path).as_posix()}/artifacts/status_reconcile_verification.json\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: FAIL\n"
                    "Findings:\n"
                    "- medium | bound parent remains draft because blocking child evidence is unchanged\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: insufficient\n"
                    "- pass_fail_closed_check: satisfied\n"
                    "- remaining_proof_gaps: blocking child evidence unchanged\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    "- outcome_support: insufficient\n\n"
                    "confidence_percent: 79\n"
                    "confidence_delta_this_iteration: 1\n"
                    "expected_next_iteration_gain: 1\n"
                    "recommended_action: stop\n"
                    "good_enough_for: draft_only\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_loop_economic_stop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
            "prompt": (
                "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
                "Judge the bound item itself. Read only the files needed to decide."
            ),
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "job_transient_retries": 2},
            "loop": {
                "loop_policy": "full_build_verify_loop",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 4,
                "pass_marker": "VERDICT: PASS",
            },
        }
    )

    result = sched.execute_job("nightly_loop_economic_stop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False

    receipt = sched.load_receipt("nightly_loop_economic_stop_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "good_enough_keep_current_status"
    assert receipt["loop"]["completed_iterations"] == 2
    loop_control = receipt["loop"]["iterations"][1]["verifier"]["meta"]["loop_control"]
    assert loop_control["confidence_percent"] == 79
    assert loop_control["expected_next_iteration_gain"] == 1
    assert loop_control["recommended_action"] == "stop"
    assert receipt["job_retry"]["history"][0]["failure_class"] == "good_enough_keep_current_status"
    assert seen["builder"] == 2
    assert seen["verifier"] == 2


def test_scheduled_build_verify_loop_can_economic_stop_status_reconcile_on_missing_loop_control(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}
    parent_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260327-124630Z-RWVP__parent"
    (parent_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None, runtime: dict | None = None, repo_root: Path | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n"
                        f"- {parent_dir.relative_to(tmp_path).as_posix()}/artifacts/status_reconcile_verification.json\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {
            "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: FAIL\n"
                        "Findings:\n"
                        "- medium | bound parent remains draft because blocking child evidence is unchanged\n\n"
                        "Boundary Review:\n"
                        "- scope_alignment: pass\n"
                        "- contract_alignment: pass\n\n"
                        "Proof Review:\n"
                        "- evidence_status: insufficient\n"
                        "- pass_fail_closed_check: satisfied\n"
                        "- remaining_proof_gaps: blocking child evidence unchanged\n\n"
                        "Validation Review:\n"
                        "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                        "- outcome_support: insufficient\n"
                    ),
                    "status": "ok",
                    "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_loop_economic_stop_missing_fields_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
            "prompt": (
                "Verify whether ledger item PIS-20260327-124630Z-RWVP has earned stronger ledger status. "
                "Judge the bound item itself. Read only the files needed to decide."
            ),
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "job_transient_retries": 2},
            "loop": {
                "loop_policy": "full_build_verify_loop",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 4,
                "pass_marker": "VERDICT: PASS",
            },
        }
    )

    result = sched.execute_job("nightly_loop_economic_stop_missing_fields_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False

    receipt = sched.load_receipt("nightly_loop_economic_stop_missing_fields_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "good_enough_keep_current_status"
    assert receipt["loop"]["completed_iterations"] == 2
    loop_control = receipt["loop"]["iterations"][1]["verifier"]["meta"]["loop_control"]
    assert loop_control["loop_control_contract_violation"] is True
    assert loop_control["loop_control_fallback_source"] == "repeated_same_blocker_stop"
    assert loop_control["recommended_action"] == "stop"
    assert loop_control["expected_next_iteration_gain"] == 0
    assert seen["builder"] == 2
    assert seen["verifier"] == 2


def test_scheduled_build_verify_loop_retries_repairable_verifier_contract_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None, **kwargs) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        "- infra/controlplane/scheduled_jobs.py\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        if seen["verifier"] == 1:
            return {
                "ok": True,
                "assistant": {
                    "content": "Summary: looks good.\nVERDICT: PASS\nAll good.",
                    "status": "ok",
                    "meta": {},
                    },
                }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_loop_verifier_contract_retry_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
            "prompt": "Implement and verify.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job(
        "nightly_loop_verifier_contract_retry_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )
    receipt = sched.load_receipt("nightly_loop_verifier_contract_retry_001", result["run_id"], repo_root=tmp_path)

    assert result["ok"] is True
    assert receipt["status"] == "ok"
    assert receipt["loop"]["completed_iterations"] == 2
    assert receipt["loop"]["iterations"][0]["verifier"]["status"] == "classification_rejected_continuity_retry"
    assert receipt["loop"]["iterations"][0]["verifier"]["meta"]["continuity_retry"] is True
    assert "compiled_retry_disposition" not in receipt
    assert seen["verifier"] == 2


def test_scheduled_build_verify_loop_ignores_confidence_stop_for_plain_implementation_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0, "verifier": 0}

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            seen["builder"] += 1
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        seen["verifier"] += 1
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: FAIL\n"
                    "Findings:\n"
                    "- medium | implementation still missing one acceptance guard\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: insufficient\n"
                    "- pass_fail_closed_check: satisfied\n"
                    "- remaining_proof_gaps: one acceptance guard missing\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    "- outcome_support: insufficient\n\n"
                    "confidence_percent: 84\n"
                    "confidence_delta_this_iteration: 1\n"
                    "expected_next_iteration_gain: 1\n"
                    "recommended_action: stop\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "nightly_loop_no_economic_stop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {
                "loop_policy": "full_build_verify_loop",
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
            },
        }
    )

    result = sched.execute_job("nightly_loop_no_economic_stop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False

    receipt = sched.load_receipt("nightly_loop_no_economic_stop_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    assert receipt["loop"]["completed_iterations"] == 3
    assert seen["builder"] == 3
    assert seen["verifier"] == 3


def test_build_verify_loop_can_queue_child_implementation_jobs_after_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-203500Z-R7K2", "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_a = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-210001Z-A1B2__child-a"
    child_a.mkdir(parents=True, exist_ok=True)
    (child_a / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-210001Z-A1B2", "title": "Child A", "status": "draft"}), encoding="utf-8")
    (child_a / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_a / "events.jsonl").write_text(
        json.dumps({"type": "created", "payload": {"title": "Child A"}}) + "\n"
        + json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": "PIS-20260326-203500Z-R7K2",
                    "relationship": "execution_child",
                    "critical_path_rank": 1,
                    "depends_on": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    child_b = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-210002Z-C3D4__child-b"
    child_b.mkdir(parents=True, exist_ok=True)
    (child_b / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-210002Z-C3D4", "title": "Child B", "status": "draft"}), encoding="utf-8")
    (child_b / "pis.md").write_text("# Child B\n", encoding="utf-8")
    (child_b / "events.jsonl").write_text(
        json.dumps({"type": "created", "payload": {"title": "Child B"}}) + "\n"
        + json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": "PIS-20260326-203500Z-R7K2",
                    "relationship": "execution_child",
                    "critical_path_rank": 2,
                    "depends_on": ["PIS-20260326-210001Z-A1B2"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    calls: list[dict[str, object]] = []
    seen = {"builder": 0, "verifier": 0}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "binding": dict(binding), "message": message, "options": dict(options or {})})
        if role == "builder":
            seen["builder"] += 1
            if seen["builder"] == 1:
                content = (
                    "Changed files:\n"
                    "- governance/ledger/patch_instruction_sheets/PIS-20260326-203500Z-R7K2__parent/pis.md\n\n"
                    "Validation Commands Run:\n"
                    "- pytest -q test_target.py\n"
                )
            else:
                content = (
                    "Pending Action Status:\n"
                    "- APPLIED: update the decomposition output to satisfy the verifier contract exactly\n\n"
                    "Changed files:\n"
                    "- governance/ledger/patch_instruction_sheets/PIS-20260326-203500Z-R7K2__parent/pis.md\n\n"
                    "Validation Commands Run:\n"
                    "- pytest -q test_target.py\n"
                )
            return {"ok": True, "assistant": {"content": content, "status": "ok", "meta": {}}}
        seen["verifier"] += 1
        verdict = (
            "VERDICT: PASS_PENDING_ACTIONS\n"
            "Findings:\n"
            "- low | add one bounded repair pass before final closure\n\n"
            "Boundary Review:\n"
            "- scope_alignment: pass\n"
            "- contract_alignment: pass\n\n"
            "Proof Review:\n"
            "- evidence_status: pending_actions\n"
            "- pass_fail_closed_check: satisfied\n"
            "- remaining_proof_gaps: pending repair actions only\n\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q test_target.py\n"
            "- outcome_support: sufficient after pending actions\n\n"
            "Pending Actions:\n"
            "- update the decomposition output to satisfy the verifier contract exactly\n"
            "Stop Rule:\n"
            "- one builder repair turn only\n"
            "- no scope widening\n"
            "- if builder cannot complete all actions, fail honestly\n"
            "Terminal Blockers:\n"
            "- none\n"
            if seen["verifier"] == 1
            else _valid_loop_verifier_pass(command="pytest -q test_target.py")
        )
        return {"ok": True, "assistant": {"content": verdict, "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("parent_decompose_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("parent_decompose_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "conditional_pass_repair_applied"
    expected_child_run_at = sched._utc_iso(sched._parse_utc(receipt["finished_utc"]) + sched._dt.timedelta(minutes=5))
    assert receipt["child_job_queue"]["created_count"] == 2
    assert receipt["child_job_queue"]["run_at_utc"] == expected_child_run_at
    assert receipt["child_job_queue"]["family_closeout_job"]["created"] is True

    child_job_a = sched.load_job(sched._job_id_from_ledger_uid("PIS-20260326-210001Z-A1B2", "Child A"), repo_root=tmp_path)
    child_job_b = sched.load_job(sched._job_id_from_ledger_uid("PIS-20260326-210002Z-C3D4", "Child B"), repo_root=tmp_path)
    closeout_job = sched.load_job(sched._family_closeout_job_id("PIS-20260326-203500Z-R7K2"), repo_root=tmp_path)
    assert child_job_a["binding"] == {"type": "ledger_uid", "id": "PIS-20260326-210001Z-A1B2"}
    assert child_job_a["schedule"]["run_at"] == expected_child_run_at
    assert child_job_b["depends_on"] == [child_job_a["job_id"]]
    assert closeout_job["binding"] == {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"}
    assert closeout_job["depends_on"] == [child_job_a["job_id"], child_job_b["job_id"]]
    assert closeout_job["loop"]["max_iterations"] == 1
    assert closeout_job["prompt_meta"]["execution_family_closeout"]["parent_uid"] == "PIS-20260326-203500Z-R7K2"
    assert len(receipt["loop"]["iterations"]) == 2
    assert receipt["loop"]["iterations"][0]["verifier"]["content"].startswith("VERDICT: PASS_PENDING_ACTIONS")
    assert receipt["loop"]["iterations"][1]["verifier"]["status"] == "waived_after_pending_actions"
    assert receipt["loop"]["iterations"][1]["verifier"]["content"].startswith(
        "[no second verifier pass executed; bounded repair stop after PASS_PENDING_ACTIONS"
    )

    assert [row["role"] for row in calls] == ["builder", "assurer", "builder"]
    assert calls[1]["options"]["timeout_s"] == 900
    assert calls[1]["options"]["prompt_file"] == "role__verifier_mobile.md"
    assert str(calls[1]["options"]["resend_key"]).endswith("::verifier::iter_1")

    report_path = Path(result["report_path"])
    report_text = report_path.read_text(encoding="utf-8")


def test_execute_job_requests_next_ready_child_after_successful_decomposition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": parent_uid, "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title, rank, deps in [
        (child_a_uid, "Child A", 1, []),
        (child_b_uid, "Child B", 2, [child_a_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(json.dumps({"uid": uid, "title": title, "status": "draft"}), encoding="utf-8")
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    requests: list[tuple[str, str]] = []

    def _fake_request_run_now(job_id: str, *, repo_root=None, requested_utc=None, request_source="manual"):
        requests.append((job_id, request_source))
        return {"ok": True, "job_id": job_id, "queued": True, "requested_utc": requested_utc or "", "request_source": request_source}

    def _fake_execute_build_verify_loop_job(spec, *, run_id, started_utc=None, repo_root=None, runtime_policy=None, **kwargs):
        return (
            True,
            {
                "schema_version": sched.RECEIPT_SCHEMA_VERSION,
                "job_kind": "build_verify_loop",
                "job_id": str(spec.get("job_id") or ""),
                "run_id": run_id,
                "trigger": "manual",
                "status": "ok",
                "binding": dict(spec.get("binding") or {}),
                "schedule": dict(spec.get("schedule") or {}),
                "runtime": dict(spec.get("runtime") or {}),
                "resolved_prompt": {"prompt_ref": "", "prompt_sha256": "", "text": str(spec.get("prompt") or "")},
                "loop": {"iterations": [], "completed_iterations": 1, "max_iterations": 1},
                "started_utc": started_utc or "2026-03-26T21:00:00Z",
                "finished_utc": started_utc or "2026-03-26T21:00:00Z",
            },
            "",
        )

    monkeypatch.setattr(sched, "_execute_build_verify_loop_job", _fake_execute_build_verify_loop_job)
    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("parent_decompose_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt("parent_decompose_001", result["run_id"], repo_root=tmp_path)
    child_a_job_id = sched._job_id_from_ledger_uid(child_a_uid, "Child A")

    assert result["ok"] is True
    assert receipt["child_job_queue"]["next_ready_job_id"] == child_a_job_id
    assert receipt["child_job_autostart"]["requested"] is True
    assert receipt["child_job_autostart"]["requested_job_id"] == child_a_job_id
    assert requests == [(child_a_job_id, "governance_decomposition_handoff")]


def test_queue_child_implementation_jobs_creates_clarification_continuation_and_parent_followup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_uid = "PIS-20260326-203500Z-R7K2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent item",
                "status": "draft",
                "integration_meta": {
                    "decomposition_readiness": {
                        "state": "clarification_needed",
                        "summary": "Need tighter mission boundaries",
                        "missing_details": ["success contract", "canonical source of truth"],
                    },
                    "child_uids": ["PIS-20260326-210002Z-C3D4"],
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    clarification_uid = "PIS-20260326-210002Z-C3D4"
    clarification_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{clarification_uid}__clarify"
    clarification_dir.mkdir(parents=True, exist_ok=True)
    (clarification_dir / "pis.md").write_text("# Clarify\n", encoding="utf-8")
    (clarification_dir / "meta.json").write_text(
        json.dumps({"uid": clarification_uid, "title": "Clarify scope", "status": "draft"}),
        encoding="utf-8",
    )
    (clarification_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": parent_uid,
                    "relationship": "clarification_child",
                    "critical_path_rank": 1,
                    "depends_on": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched._queue_child_implementation_jobs_after_pass(
        {
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        finished_utc="2026-03-26T21:00:00Z",
        repo_root=tmp_path,
    )

    clarification_job_id = sched._job_id_from_ledger_uid(clarification_uid, "Clarify scope")
    followup_job_id = sched._continuation_job_id("parent_decompose_001", "clarify_followup")
    clarification_job = sched.load_job(clarification_job_id, repo_root=tmp_path)
    followup_job = sched.load_job(followup_job_id, repo_root=tmp_path)

    assert result["created_count"] == 1
    assert result["queue_blocked_reason"] == "clarification_needed"
    assert result["next_ready_job_id"] == clarification_job_id
    assert result["missing_details"] == ["success contract", "canonical source of truth"]
    assert result["clarification_continuation"]["enabled"] is True
    assert result["clarification_continuation"]["clarification_job_ids"] == [clarification_job_id]
    assert result["clarification_continuation"]["parent_followup_job"]["job_id"] == followup_job_id
    assert clarification_job["binding"] == {"type": "ledger_uid", "id": clarification_uid}
    assert followup_job["depends_on"] == [clarification_job_id]
    assert followup_job["prompt_meta"]["autonomous_continuation"]["kind"] == "clarification_followup_parent_decomposition"


def test_recovered_governance_decomposition_requests_next_ready_child_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent item",
                "status": "draft",
                "integration_meta": {
                    "child_uids": [child_a_uid, child_b_uid],
                    "recommended_execution_order": [child_a_uid, child_b_uid],
                    "recommended_first_child_uid": child_a_uid,
                    "decomposition_readiness": {"state": "decomposition_ready"},
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title, rank, deps in [
        (child_a_uid, "Child A", 1, []),
        (child_b_uid, "Child B", 2, [child_a_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(json.dumps({"uid": uid, "title": title, "status": "draft"}), encoding="utf-8")
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    requests: list[tuple[str, str]] = []

    def _fake_request_run_now(job_id: str, *, repo_root=None, requested_utc=None, request_source="manual"):
        requests.append((job_id, request_source))
        return {"ok": True, "job_id": job_id, "queued": True, "requested_utc": requested_utc or "", "request_source": request_source}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = sched._maybe_recover_completed_governance_decomposition(
        job_id="parent_decompose_001",
        state={"job_id": "parent_decompose_001", "active_started_utc": "2026-03-26T21:00:00Z"},
        spec={
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "expected_output": {"kind": "governance_decomposition_patch"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        trigger="recovery",
        recovered_run_id="recover_001",
        recovered_utc="2026-03-26T21:05:00Z",
        repo_root=tmp_path,
    )

    child_a_job_id = sched._job_id_from_ledger_uid(child_a_uid, "Child A")
    assert result["last_status"] == "ok"
    receipt = sched.load_receipt("parent_decompose_001", "recover_001", repo_root=tmp_path)
    assert receipt["child_job_queue"]["next_ready_job_id"] == child_a_job_id
    assert receipt["child_job_autostart"]["requested"] is True
    assert receipt["child_job_autostart"]["requested_job_id"] == child_a_job_id
    assert requests == [(child_a_job_id, "governance_decomposition_recovery_handoff")]


def test_build_verify_loop_does_not_recreate_family_closeout_when_family_already_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-20260326-203500Z-R7K2",
                "title": "Parent",
                "status": "verified",
                "integration_meta": {
                    "child_uids": [
                        "PIS-20260326-210001Z-A1B2",
                        "PIS-20260326-210002Z-C3D4",
                        "PIS-20260326-210003Z-E5F6",
                    ],
                    "required_final_child_uid": "PIS-20260326-210003Z-E5F6",
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, rank, deps in [
        ("PIS-20260326-210001Z-A1B2", "child-a", "Child A", 1, []),
        ("PIS-20260326-210002Z-C3D4", "child-b", "Child B", 2, ["PIS-20260326-210001Z-A1B2"]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "verified"}),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": "PIS-20260326-203500Z-R7K2",
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    sched.save_job(
        {
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        repo_root=tmp_path,
    )
    def _send(*, role: str, **kwargs) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output\n\nValidation Commands Run\n- none (already verified family)", "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nalready verified family", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job("parent_decompose_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt("parent_decompose_001", result["run_id"], repo_root=tmp_path)

    assert receipt["child_job_queue"]["family_closeout_job"]["created"] is False
    assert receipt["child_job_queue"]["family_closeout_job"]["reason"] == "family_already_verified"
    closeout_path = sched.jobs_dir(tmp_path) / f"{sched._safe_name(sched._family_closeout_job_id('PIS-20260326-203500Z-R7K2'))}.json"
    assert closeout_path.exists() is False


def test_build_verify_loop_uses_required_final_child_as_single_closeout_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-20260326-203500Z-R7K2",
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "child_uids": [
                        "PIS-20260326-210001Z-A1B2",
                        "PIS-20260326-210002Z-C3D4",
                    ],
                    "required_final_child_uid": "PIS-20260326-210002Z-C3D4",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, relationship, rank, deps in [
        ("PIS-20260326-210001Z-A1B2", "child-a", "Child A", "execution_child", 1, []),
        ("PIS-20260326-210002Z-C3D4", "child-b", "Validate Parent Intent Assembled-System Intent E2E Suite", "intent_validation_suite", 2, ["PIS-20260326-210001Z-A1B2"]),
        ("PIS-20260326-210003Z-E5F6", "child-c", "Close Parent Intent Boundary, Proof, and Family Reconciliation", "intent_family_closeout", 3, ["PIS-20260326-210001Z-A1B2", "PIS-20260326-210002Z-C3D4"]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": relationship},
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": "PIS-20260326-203500Z-R7K2",
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    sched.save_job(
        {
            "job_id": "parent_decompose_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "implementation"}},
        },
        repo_root=tmp_path,
    )

    def _send(*, role: str, **kwargs) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": _valid_loop_builder_output(command="none"), "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(command="none"), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job("parent_decompose_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt("parent_decompose_001", result["run_id"], repo_root=tmp_path)

    assert receipt["child_job_queue"]["created_count"] == 2
    assert receipt["child_job_queue"]["family_closeout_job"]["created"] is False
    assert receipt["child_job_queue"]["family_closeout_job"]["reason"] == "final_child_owns_family_closeout"
    assert receipt["child_job_queue"]["family_closeout_job"]["required_final_child_uid"] == "PIS-20260326-210002Z-C3D4"
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    repaired_integration = parent_meta["integration_meta"]
    assert repaired_integration["required_final_child_uid"] == "PIS-20260326-210002Z-C3D4"
    impl_job = sched.load_job(sched._job_id_from_ledger_uid("PIS-20260326-210001Z-A1B2", "Child A"), repo_root=tmp_path)
    validation_job = sched.load_job(
        sched._job_id_from_ledger_uid(
            "PIS-20260326-210002Z-C3D4",
            "Validate Parent Intent Assembled-System Intent E2E Suite",
        ),
        repo_root=tmp_path,
    )
    assert impl_job["job_kind"] == "single_turn"
    assert validation_job["job_kind"] == "single_turn"
    assert validation_job["depends_on"] == [impl_job["job_id"]]
    assert impl_job["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert validation_job["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_validation_suite"
    assert validation_job["prompt_meta"]["execution_family_contract"]["required_final_child_uid"] == "PIS-20260326-210002Z-C3D4"
    closeout_path = sched.jobs_dir(tmp_path) / f"{sched._safe_name(sched._family_closeout_job_id('PIS-20260326-203500Z-R7K2'))}.json"
    assert closeout_path.exists() is False


def test_queue_execution_family_closeout_retires_existing_job_when_family_already_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "verified",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "child_uids": ["PIS-20260326-210001Z-A1B2"],
                    "required_final_child_uid": "PIS-20260326-210001Z-A1B2",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_uid = "PIS-20260326-210001Z-A1B2"
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{child_uid}__child-a"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "pis.md").write_text("# Child A\n\nDepends on:\n- none\n", encoding="utf-8")
    (child_dir / "meta.json").write_text(json.dumps({"uid": child_uid, "title": "Child A", "status": "verified"}), encoding="utf-8")
    (child_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": parent_uid,
                    "relationship": "execution_child",
                    "critical_path_rank": 1,
                    "depends_on": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": ["pis_a8c1_20260326_child-a"],
            "display_title": "stale closeout",
            "prompt": "old closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"execution_family_closeout": {"child_uids": [child_uid], "child_job_ids": ["pis_a8c1_20260326_child-a"]}},
        },
        repo_root=tmp_path,
    )
    state_path = sched._state_path(closeout_job_id, repo_root=tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"job_id": closeout_job_id, "active_run_id": "", "run_now_requested_utc": "2026-03-26T21:00:00Z"}),
        encoding="utf-8",
    )

    result = sched._queue_execution_family_closeout_job_after_pass(
        {"job_id": "parent_decompose_001"},
        parent_uid=parent_uid,
        child_rows=[{"uid": child_uid, "title": "Child A"}],
        child_job_ids=["pis_a8c1_20260326_child-a"],
        run_at_utc="2026-03-26T22:00:00Z",
        repo_root=tmp_path,
    )

    assert result["created"] is False
    assert result["reason"] == "family_already_verified"
    assert result["retired_existing"]["retired"] is True
    retired = sched.load_job(closeout_job_id, repo_root=tmp_path)
    assert retired["enabled"] is False
    assert retired["prompt_meta"]["retired_closeout"]["reason"] == "family_already_verified"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["run_now_requested_utc"] == ""
    assert state["last_nonlaunch_reason"] == "retired_closeout:family_already_verified"


def test_queue_execution_family_closeout_retires_existing_job_when_child_dependencies_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps({"uid": parent_uid, "title": "Parent", "status": "draft", "integration_meta": {"child_uids": []}}),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": ["old_child_job"],
            "display_title": "stale closeout",
            "prompt": "old closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"execution_family_closeout": {"child_uids": ["PIS-OLD-CHILD"], "child_job_ids": ["old_child_job"]}},
        },
        repo_root=tmp_path,
    )

    result = sched._queue_execution_family_closeout_job_after_pass(
        {"job_id": "parent_decompose_001"},
        parent_uid=parent_uid,
        child_rows=[],
        child_job_ids=[],
        run_at_utc="2026-03-26T22:00:00Z",
        repo_root=tmp_path,
    )

    assert result["created"] is False
    assert result["reason"] == "missing_child_dependencies"
    assert result["retired_existing"]["retired"] is True
    retired = sched.load_job(closeout_job_id, repo_root=tmp_path)
    assert retired["enabled"] is False
    assert retired["prompt_meta"]["retired_closeout"]["reason"] == "missing_child_dependencies"


def test_queue_execution_family_closeout_retires_existing_job_when_required_final_child_owns_closeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": ["pis_a8c1_20260326_child-a"],
            "display_title": "stale closeout",
            "prompt": "old closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"execution_family_closeout": {"child_uids": [child_uid], "child_job_ids": ["pis_a8c1_20260326_child-a"]}},
        },
        repo_root=tmp_path,
    )

    result = sched._queue_execution_family_closeout_job_after_pass(
        {"job_id": "parent_decompose_001"},
        parent_uid=parent_uid,
        child_rows=[{"uid": child_uid, "title": "Close Parent Intent Boundary, Proof, and Family Reconciliation", "relationship": "intent_family_closeout"}],
        child_job_ids=["pis_a8c1_20260326_child-a"],
        run_at_utc="2026-03-26T22:00:00Z",
        repo_root=tmp_path,
    )

    assert result["created"] is False
    assert result["reason"] == "final_child_owns_family_closeout"
    assert result["required_final_child_uid"] == child_uid
    assert result["retired_existing"]["retired"] is True
    retired = sched.load_job(closeout_job_id, repo_root=tmp_path)
    assert retired["enabled"] is False
    assert retired["prompt_meta"]["retired_closeout"]["reason"] == "final_child_owns_family_closeout"


def test_retired_execution_family_closeout_does_not_reopen_from_legacy_residue_after_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": ["legacy_child_job"],
            "display_title": "stale closeout",
            "prompt": "legacy closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "child_uids": ["PIS-OLD-CHILD"],
                    "child_job_ids": ["legacy_child_job"],
                }
            },
        },
        repo_root=tmp_path,
    )
    state_path = sched._state_path(closeout_job_id, repo_root=tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "job_id": closeout_job_id,
                "active_run_id": "",
                "run_now_requested_utc": "2026-03-26T21:05:00Z",
                "run_now_request_run_id": "legacy-run-now",
                "run_now_request_source": "legacy_replay",
                "last_status": "error",
                "last_nonlaunch_reason": "legacy_residue",
            }
        ),
        encoding="utf-8",
    )

    first = sched._queue_execution_family_closeout_job_after_pass(
        {"job_id": "parent_decompose_001"},
        parent_uid=parent_uid,
        child_rows=[{"uid": child_uid, "title": "Close Parent Intent Boundary, Proof, and Family Reconciliation", "relationship": "intent_family_closeout"}],
        child_job_ids=["pis_a8c1_20260326_child-a"],
        run_at_utc="2026-03-26T22:00:00Z",
        repo_root=tmp_path,
    )
    retired = sched.load_job(closeout_job_id, repo_root=tmp_path)
    retired_state = sched.load_state(closeout_job_id, repo_root=tmp_path)
    listed = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-26T22:10:00Z")}

    assert first["created"] is False
    assert first["reason"] == "final_child_owns_family_closeout"
    assert first["retired_existing"]["retired"] is True
    assert retired["enabled"] is False
    assert retired["prompt_meta"]["retired_closeout"]["reason"] == "final_child_owns_family_closeout"
    assert retired_state["run_now_requested_utc"] == ""
    assert retired_state["run_now_request_run_id"] == ""
    assert retired_state["run_now_request_source"] == ""
    assert retired_state["last_nonlaunch_reason"] == "retired_closeout:final_child_owns_family_closeout"
    assert listed[closeout_job_id]["due"]["eligible"] is False
    assert listed[closeout_job_id]["due"]["reason"] == "disabled"

    second = sched._queue_execution_family_closeout_job_after_pass(
        {"job_id": "parent_decompose_001"},
        parent_uid=parent_uid,
        child_rows=[{"uid": child_uid, "title": "Close Parent Intent Boundary, Proof, and Family Reconciliation", "relationship": "intent_family_closeout"}],
        child_job_ids=["pis_a8c1_20260326_child-a"],
        run_at_utc="2026-03-26T22:15:00Z",
        repo_root=tmp_path,
    )
    reopened = sched.load_job(closeout_job_id, repo_root=tmp_path)

    assert second["created"] is False
    assert second["reason"] == "final_child_owns_family_closeout"
    assert second["retired_existing"]["retired"] is True
    assert second["retired_existing"]["previous_reason"] == "final_child_owns_family_closeout"
    assert reopened["enabled"] is False
    assert reopened["prompt_meta"]["retired_closeout"]["reason"] == "final_child_owns_family_closeout"


def test_queue_child_implementation_jobs_leaves_legacy_tail_shape_unpromoted_without_explicit_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    impl_uid = "PIS-20260326-210001Z-A1B2"
    proof_uid = "PIS-20260326-210002Z-C3D4"
    closeout_uid = "PIS-20260326-210003Z-E5F6"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "proof_child_uid": proof_uid,
                    "required_final_child_uid": proof_uid,
                    "child_uids": [impl_uid, proof_uid, closeout_uid],
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, rank, deps in [
        (impl_uid, "child-a", "Child A", 1, []),
        (proof_uid, "proof", "Prove Parent End-to-End", 2, [impl_uid]),
        (closeout_uid, "closeout", "Close Parent Intent Boundary and Family Acceptance", 3, [impl_uid, proof_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": "execution_child"},
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    for uid, title in [(impl_uid, "Child A"), (proof_uid, "Prove Parent End-to-End"), (closeout_uid, "Close Parent Intent Boundary and Family Acceptance")]:
        sched.save_job(
            {
                "job_id": sched._job_id_from_ledger_uid(uid, title),
                "job_kind": "build_verify_loop",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": uid},
                "depends_on": [],
                "display_title": title,
                "prompt": "old prompt",
                "context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
                "policy": {"max_runtime_seconds": 1800, "post_success_commit_tag": True},
                "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
                "required_context_bundle": "lab_core_v1",
                "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 5, "pass_marker": "VERDICT: PASS"},
                "prompt_meta": {
                    "execution_family_contract": {
                        "contract_kind": "intent_derived_execution_family_v1",
                        "source_intent_uid": "IS-20260326-000001Z-TEST",
                        "parent_uid": parent_uid,
                        "child_uid": uid,
                        "child_kind": "execution_child",
                        "critical_path_rank": 0,
                        "required_final_child_uid": proof_uid,
                        "required_final_child_kind": "",
                    }
                },
            },
            repo_root=tmp_path,
        )

    result = sched._queue_child_implementation_jobs_after_pass(
        {
            "job_id": "parent_decompose_001",
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "build_verify_loop"}},
        },
        finished_utc="2026-03-26T21:00:00Z",
        repo_root=tmp_path,
    )

    assert result["created_count"] == 3
    assert result["skipped_count"] == 0
    validation_job = sched.load_job(sched._job_id_from_ledger_uid(proof_uid, "Prove Parent End-to-End"), repo_root=tmp_path)
    closeout_job = sched.load_job(sched._job_id_from_ledger_uid(closeout_uid, "Close Parent Intent Boundary and Family Acceptance"), repo_root=tmp_path)
    assert validation_job["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert validation_job["prompt_meta"]["execution_family_contract"]["required_final_child_uid"] == proof_uid
    assert validation_job["depends_on"] == [sched._job_id_from_ledger_uid(impl_uid, "Child A")]
    assert closeout_job["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert closeout_job["depends_on"] == [
        sched._job_id_from_ledger_uid(impl_uid, "Child A"),
        sched._job_id_from_ledger_uid(proof_uid, "Prove Parent End-to-End"),
    ]
    repaired_parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    repaired_integration = repaired_parent_meta["integration_meta"]
    assert "intent_validation_child_uid" not in repaired_integration
    assert repaired_integration["required_final_child_uid"] == proof_uid
    assert "required_final_child_kind" not in repaired_integration


def test_successful_family_child_requests_next_eligible_family_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    job_a = sched._job_id_from_ledger_uid(child_a_uid, "Child A")
    job_b = sched._job_id_from_ledger_uid(child_b_uid, "Child B")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": parent_uid, "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title, rank, deps in [
        (child_a_uid, "Child A", 1, []),
        (child_b_uid, "Child B", 2, [child_a_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        meta = {"uid": uid, "title": title, "status": "draft"}
        if uid == child_a_uid:
            meta["status"] = "verified"
            meta["evidence"] = {"claim": "verified child"}
            meta["changesets"] = [{"kind": "implementation", "summary": "changed files", "files": ["foo.py"]}]
        (child_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    common_prompt_meta = {"generated_from_parent_uid": parent_uid}
    sched.save_job(
        {
            "job_id": job_a,
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": child_a_uid},
            "display_title": "Child A",
            "depends_on": [],
            "prompt": "Implement child A",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": job_b,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_b_uid},
            "display_title": "Child B",
            "depends_on": [job_a],
            "prompt": "Implement child B",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_a,
        {
            "job_id": job_a,
            "last_status": "ok",
            "last_trigger": "scheduler",
            "last_reconciliation_reason": "already_executed",
            "last_slot_key": "2026-03-26T21:00:00Z",
            "last_finished_utc": "2026-03-26T21:05:00Z",
            "updated_utc": "2026-03-26T21:05:00Z",
        },
        repo_root=tmp_path,
    )
    sched._write_state(job_b, {"job_id": job_b}, repo_root=tmp_path)

    requests: list[tuple[str, str]] = []

    def _fake_request_run_now(job_id: str, *, repo_root=None, requested_utc=None, request_source="manual"):
        requests.append((job_id, request_source))
        return {"ok": True, "job_id": job_id, "queued": True, "requested_utc": requested_utc or "", "request_source": request_source}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    handoff = sched._request_next_family_job_after_success(
        sched.load_job(job_a, repo_root=tmp_path),
        finished_utc="2026-03-26T21:05:00Z",
        repo_root=tmp_path,
    )

    assert handoff["ok"] is True
    assert handoff["requested"] is True
    assert handoff["requested_job_id"] == job_b
    assert requests == [(job_b, "family_progress_handoff")]


def test_successful_family_child_queues_ledger_truth_reconcile_before_next_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    job_a = sched._job_id_from_ledger_uid(child_a_uid, "Child A")
    job_b = sched._job_id_from_ledger_uid(child_b_uid, "Child B")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": parent_uid, "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title, rank, deps in [
        (child_a_uid, "Child A", 1, []),
        (child_b_uid, "Child B", 2, [child_a_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(json.dumps({"uid": uid, "title": title, "status": "draft"}), encoding="utf-8")
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    common_prompt_meta = {"generated_from_parent_uid": parent_uid}
    sched.save_job(
        {
            "job_id": job_a,
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": child_a_uid},
            "display_title": "Child A",
            "depends_on": [],
            "prompt": "Implement child A",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": job_b,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_b_uid},
            "display_title": "Child B",
            "depends_on": [job_a],
            "prompt": "Implement child B",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_a,
        {
            "job_id": job_a,
            "last_status": "ok",
            "last_trigger": "scheduler",
            "last_reconciliation_reason": "already_executed",
            "last_slot_key": "2026-03-26T21:00:00Z",
            "last_finished_utc": "2026-03-26T21:05:00Z",
            "updated_utc": "2026-03-26T21:05:00Z",
        },
        repo_root=tmp_path,
    )
    sched._write_state(job_b, {"job_id": job_b}, repo_root=tmp_path)

    requests: list[tuple[str, str]] = []

    def _fake_request_run_now(job_id: str, *, repo_root=None, requested_utc=None, request_source="manual"):
        requests.append((job_id, request_source))
        return {"ok": True, "job_id": job_id, "queued": True, "requested_utc": requested_utc or "", "request_source": request_source}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    handoff = sched._post_success_progress_handoff(
        sched.load_job(job_a, repo_root=tmp_path),
        finished_utc="2026-03-26T21:05:00Z",
        repo_root=tmp_path,
    )

    assert handoff["ok"] is True
    assert handoff["handoff_type"] == "ledger_truth_reconcile"
    assert handoff["ledger_uid"] == child_a_uid
    assert handoff["requested"] is True
    assert requests == [("verify_a1b2_20260326_status_reconcile", "ledger_truth_handoff")]
    reconcile_job = sched.load_job("verify_a1b2_20260326_status_reconcile", repo_root=tmp_path)
    assert reconcile_job["prompt_meta"]["generated_from_parent_uid"] == parent_uid


def test_explicit_pass_chain_successful_child_requests_next_child_without_reconcile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    job_a = "pis_a1b2_20260326_child-a_p1of3"
    job_b = "pis_c3d4_20260326_child-b_p1of3"

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent item",
                "status": "draft",
                "integration_meta": {"execution_mode": "explicit_pass_chain_v1"},
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title, rank, deps in [
        (child_a_uid, "Child A", 1, []),
        (child_b_uid, "Child B", 2, [child_a_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "draft", "integration_meta": {"relationship": "execution_child"}}),
            encoding="utf-8",
        )
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    common_prompt_meta = {
        "generated_from_parent_uid": parent_uid,
        "execution_family_contract": {"execution_mode": "explicit_pass_chain_v1"},
        "explicit_pass_chain": {"job_type": "build_or_improve", "pass_index": 1, "pass_count": 3},
    }
    sched.save_job(
        {
            "job_id": job_a,
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": child_a_uid},
            "display_title": "Child A pass 1/3",
            "depends_on": [],
            "prompt": "Implement child A",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "builder_only": True, "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": job_b,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_b_uid},
            "display_title": "Child B pass 1/3",
            "depends_on": [job_a],
            "prompt": "Implement child B",
            "context_refs": [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "builder_only": True, "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )

    captured: list[tuple[str, str]] = []

    def _fake_next(spec, *, finished_utc, repo_root=None):
        captured.append((str(spec.get("job_id") or ""), finished_utc))
        return {"ok": True, "requested": True, "requested_job_id": job_b, "handoff_type": "family_progress_handoff"}

    monkeypatch.setattr(sched, "_request_next_family_job_after_success", _fake_next)

    handoff = sched._post_success_progress_handoff(
        sched.load_job(job_a, repo_root=tmp_path),
        finished_utc="2026-03-26T21:05:00Z",
        repo_root=tmp_path,
    )

    assert handoff["ok"] is True
    assert handoff["requested"] is True
    assert handoff["requested_job_id"] == job_b
    assert handoff.get("handoff_type", "") != "ledger_truth_reconcile"
    assert captured == [(job_a, "2026-03-26T21:05:00Z")]


def test_due_status_blocks_family_dependency_until_dependency_ledger_truth_is_closeout_grade(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210001Z-A1B2"
    child_b_uid = "PIS-20260326-210002Z-C3D4"
    job_a = sched._job_id_from_ledger_uid(child_a_uid, "Child A")
    job_b = sched._job_id_from_ledger_uid(child_b_uid, "Child B")

    for uid, title in [(child_a_uid, "Child A"), (child_b_uid, "Child B")]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(json.dumps({"uid": uid, "title": title, "status": "draft"}), encoding="utf-8")
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text("", encoding="utf-8")

    common_prompt_meta = {"generated_from_parent_uid": parent_uid}
    sched.save_job(
        {
            "job_id": job_a,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_a_uid},
            "prompt": "Implement child A",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": job_b,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_b_uid},
            "depends_on": [job_a],
            "prompt": "Implement child B",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_a,
        {
            "job_id": job_a,
            "last_status": "ok",
            "last_finished_utc": "2026-03-26T21:05:00Z",
            "updated_utc": "2026-03-26T21:05:00Z",
        },
        repo_root=tmp_path,
    )

    due = sched.due_status(sched.load_job(job_b, repo_root=tmp_path), state=sched.load_state(job_b, repo_root=tmp_path), now=sched._parse_utc("2026-03-26T21:05:30Z"), repo_root=tmp_path)

    assert due["eligible"] is False
    assert due["reason"] == f"dependency_pending:{job_a}"
    assert due["dependency_ledger_uid"] == child_a_uid
    assert "ledger_status_draft" in due["dependency_ledger_missing_reasons"]
    assert "ledger_evidence_missing" in due["dependency_ledger_missing_reasons"]
    assert "ledger_changesets_missing" in due["dependency_ledger_missing_reasons"]


def test_due_status_backfills_changesets_from_success_receipt_for_family_implementation_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_a_uid = "PIS-20260326-210011Z-A1B2"
    child_b_uid = "PIS-20260326-210012Z-C3D4"
    job_a = sched._job_id_from_ledger_uid(child_a_uid, "Child A")
    job_b = sched._job_id_from_ledger_uid(child_b_uid, "Child B")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "draft" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "child_uids": [child_a_uid, child_b_uid],
                    "required_final_child_uid": child_b_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, title in [(child_a_uid, "Child A"), (child_b_uid, "Child B")]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{uid}__child"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "verified" if uid == child_a_uid else "draft",
                    "evidence": {"claim": f"{uid} has durable evidence"} if uid == child_a_uid else {},
                    "changesets": [],
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text("", encoding="utf-8")

    common_prompt_meta = {"generated_from_parent_uid": parent_uid}
    sched.save_job(
        {
            "job_id": job_a,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_a_uid},
            "prompt": "Implement child A",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": job_b,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_b_uid},
            "depends_on": [job_a],
            "prompt": "Implement child B",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    receipt_path = (sched.receipts_dir(tmp_path) / sched._safe_name(job_a) / "sj_demo_ok.json").resolve()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(
        receipt_path,
        {
            "job_id": job_a,
            "run_id": "sj_demo_ok",
            "status": "ok",
            "builder": {"content": "## Changed files\n- infra/controlplane/example.py\n"},
            "repo_residue": {"declared_changed_files": ["infra/controlplane/example.py"]},
        },
    )
    sched._write_state(
        job_a,
        {
            "job_id": job_a,
            "last_status": "ok",
            "last_receipt_path": str(receipt_path),
            "last_finished_utc": "2026-03-26T21:05:00Z",
            "updated_utc": "2026-03-26T21:05:00Z",
        },
        repo_root=tmp_path,
    )

    due = sched.due_status(
        sched.load_job(job_b, repo_root=tmp_path),
        state=sched.load_state(job_b, repo_root=tmp_path),
        now=sched._parse_utc("2026-03-26T21:05:30Z"),
        repo_root=tmp_path,
    )

    meta_path = next(
        (
            tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03"
        ).glob(f"{child_a_uid}__*/meta.json")
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert due["eligible"] is True
    assert meta["changesets"] == [
        {
            "kind": "implementation",
            "summary": "Synthesized from successful scheduled receipt changed-files evidence (sj_demo_ok)",
            "files": ["infra/controlplane/example.py"],
            "source": "scheduled_receipt_declared_changed_files_v1",
        }
    ]


def test_due_status_allows_validation_child_dependency_without_changesets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K3"
    validation_uid = "PIS-20260326-210021Z-VA11"
    closeout_uid = "PIS-20260326-210022Z-CL11"
    validation_job_id = sched._job_id_from_ledger_uid(validation_uid, "Validation")
    closeout_job_id = sched._job_id_from_ledger_uid(closeout_uid, "Closeout")

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "draft" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "child_uids": [validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    validation_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{validation_uid}__validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": validation_uid,
                "title": "Validation",
                "status": "verified",
                "evidence": {"artifact": "artifacts/validation.json"},
                "changesets": [],
            }
        ),
        encoding="utf-8",
    )
    (validation_dir / "pis.md").write_text("# Validation\n", encoding="utf-8")
    (validation_dir / "events.jsonl").write_text("", encoding="utf-8")

    closeout_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "draft" / "2026" / "03" / f"{closeout_uid}__closeout"
    closeout_dir.mkdir(parents=True, exist_ok=True)
    (closeout_dir / "meta.json").write_text(json.dumps({"uid": closeout_uid, "title": "Closeout", "status": "draft"}), encoding="utf-8")
    (closeout_dir / "pis.md").write_text("# Closeout\n", encoding="utf-8")
    (closeout_dir / "events.jsonl").write_text("", encoding="utf-8")

    common_prompt_meta = {"generated_from_parent_uid": parent_uid}
    sched.save_job(
        {
            "job_id": validation_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": validation_uid},
            "prompt": "Validate",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": closeout_uid},
            "depends_on": [validation_job_id],
            "prompt": "Close out",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": dict(common_prompt_meta),
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        validation_job_id,
        {
            "job_id": validation_job_id,
            "last_status": "ok",
            "last_finished_utc": "2026-03-26T21:05:00Z",
            "updated_utc": "2026-03-26T21:05:00Z",
        },
        repo_root=tmp_path,
    )

    due = sched.due_status(
        sched.load_job(closeout_job_id, repo_root=tmp_path),
        state=sched.load_state(closeout_job_id, repo_root=tmp_path),
        now=sched._parse_utc("2026-03-26T21:05:30Z"),
        repo_root=tmp_path,
    )

    assert due["eligible"] is True


def test_scheduler_tick_does_not_depend_on_heavy_list_jobs_view(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "tick_lightweight_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-2848"},
            "prompt": "Implement and verify 2848.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    def _explode_list_jobs(*args, **kwargs):
        raise AssertionError("scheduler_tick should not call list_jobs")

    monkeypatch.setattr(sched, "list_jobs", _explode_list_jobs)

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert result["submitted_count"] == 1


def test_execution_family_closeout_short_circuits_when_family_already_verified_at_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    intent_uid = "IS-20260326-000001Z-TEST"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "verified",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{child_uid}__child-a"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_dir / "meta.json").write_text(
        json.dumps({"uid": child_uid, "title": "Child A", "status": "verified", "updated_utc": "2026-03-26T21:01:00Z"}),
        encoding="utf-8",
    )
    (child_dir / "events.jsonl").write_text("", encoding="utf-8")
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "verified" / "2026" / "03" / f"{intent_uid}__intent"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "intent_sheet.md").write_text("# Intent\n", encoding="utf-8")
    (intent_dir / "meta.json").write_text(
        json.dumps({"uid": intent_uid, "title": "Intent", "status": "verified", "validating_pis_uids": [child_uid]}),
        encoding="utf-8",
    )
    (intent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_job_id = sched._job_id_from_ledger_uid(child_uid, "Child A")
    sched.save_job(
        {
            "job_id": child_job_id,
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": child_uid},
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-26T20:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    child_state_path = sched._state_path(child_job_id, repo_root=tmp_path)
    child_state_path.parent.mkdir(parents=True, exist_ok=True)
    child_state_path.write_text(
        json.dumps({"job_id": child_job_id, "last_status": "ok", "last_receipt_path": str((tmp_path / "receipt.json").resolve())}),
        encoding="utf-8",
    )
    (tmp_path / "receipt.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")

    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": [child_job_id],
            "display_title": "closeout",
            "prompt": "closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": parent_uid,
                    "source_intent_uid": intent_uid,
                    "required_final_child_uid": child_uid,
                    "child_uids": [child_uid],
                    "child_job_ids": [child_job_id],
                }
            },
        },
        repo_root=tmp_path,
    )

    def _fail_send(**kwargs):
        raise AssertionError("closeout should short-circuit before agent execution")

    monkeypatch.setattr(sched._agents, "send_message", _fail_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job(closeout_job_id, repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt(closeout_job_id, result["run_id"], repo_root=tmp_path)

    assert result["ok"] is True
    assert receipt["status"] == "ok"
    assert receipt["bound_ledger_item_short_circuit"]["applied"] is True
    assert receipt["bound_ledger_item_short_circuit"]["reason"] == "bound_item_already_verified_at_execution"
    assert receipt["terminal_once_archive"]["archived"] is True
    assert receipt["loop"]["completed_iterations"] == 0
    with pytest.raises(FileNotFoundError):
        sched.load_job(closeout_job_id, repo_root=tmp_path)


def test_bound_status_reconcile_short_circuits_when_bound_item_already_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    uid = "PIS-20260326-210001Z-A1B2"
    item_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{uid}__child-a"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (item_dir / "meta.json").write_text(
        json.dumps({"uid": uid, "title": "Child A", "status": "verified", "updated_utc": "2026-03-26T21:01:00Z"}),
        encoding="utf-8",
    )
    (item_dir / "events.jsonl").write_text("", encoding="utf-8")

    job_id = "verify_a1b2_20260326_status_reconcile"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "display_title": "Verify a1b2 ledger status reconciliation",
            "prompt": "reconcile",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "generated_by_closeout_truth_reconcile": True,
                "ledger_truth_reconcile_for_job_id": "source_job_001",
                "ledger_truth_reconcile_for_uid": uid,
            },
        },
        repo_root=tmp_path,
    )

    def _fail_send(**kwargs):
        raise AssertionError("status reconcile should short-circuit before agent execution")

    monkeypatch.setattr(sched._agents, "send_message", _fail_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job(job_id, repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt(job_id, result["run_id"], repo_root=tmp_path)
    state = sched.load_state(job_id, repo_root=tmp_path)

    assert result["ok"] is True
    assert receipt["status"] == "ok"
    assert receipt["bound_status_reconcile_short_circuit"]["applied"] is True
    assert receipt["bound_status_reconcile_short_circuit"]["reason"] == "bound_item_already_verified_at_execution"
    assert receipt["terminal_once_archive"]["archived"] is True
    assert receipt["loop"]["completed_iterations"] == 0
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    with pytest.raises(FileNotFoundError):
        sched.load_job(job_id, repo_root=tmp_path)


def test_ledger_bound_job_short_circuits_when_bound_item_already_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    uid = "PIS-20260326-210001Z-A1B2"
    item_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{uid}__child-a"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (item_dir / "meta.json").write_text(
        json.dumps({"uid": uid, "title": "Child A", "status": "verified", "updated_utc": "2026-03-26T21:01:00Z"}),
        encoding="utf-8",
    )
    (item_dir / "events.jsonl").write_text("", encoding="utf-8")

    job_id = "pis_a1b2_20260326_child_a"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "display_title": "Child A",
            "prompt": "implement",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {},
        },
        repo_root=tmp_path,
    )

    def _fail_send(**kwargs):
        raise AssertionError("verified ledger-bound job should short-circuit before agent execution")

    monkeypatch.setattr(sched._agents, "send_message", _fail_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job(job_id, repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt(job_id, result["run_id"], repo_root=tmp_path)
    state = sched.load_state(job_id, repo_root=tmp_path)

    assert result["ok"] is True
    assert receipt["status"] == "ok"
    assert receipt["bound_ledger_item_short_circuit"]["applied"] is True
    assert receipt["bound_ledger_item_short_circuit"]["reason"] == "bound_item_already_verified_at_execution"
    assert receipt["terminal_once_archive"]["archived"] is True
    assert receipt["loop"]["completed_iterations"] == 0
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    with pytest.raises(FileNotFoundError):
        sched.load_job(job_id, repo_root=tmp_path)


def test_execution_family_closeout_dependency_admission_executes_and_persists_terminal_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    intent_uid = "IS-20260326-000001Z-TEST"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "verified",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "03" / f"{child_uid}__child-a"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_dir / "meta.json").write_text(
        json.dumps({"uid": child_uid, "title": "Child A", "status": "verified", "updated_utc": "2026-03-26T21:01:00Z"}),
        encoding="utf-8",
    )
    (child_dir / "events.jsonl").write_text("", encoding="utf-8")
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "verified" / "2026" / "03" / f"{intent_uid}__intent"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "intent_sheet.md").write_text("# Intent\n", encoding="utf-8")
    (intent_dir / "meta.json").write_text(
        json.dumps({"uid": intent_uid, "title": "Intent", "status": "verified", "validating_pis_uids": [child_uid]}),
        encoding="utf-8",
    )
    (intent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_job_id = sched._job_id_from_ledger_uid(child_uid, "Child A")
    sched.save_job(
        {
            "job_id": child_job_id,
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": child_uid},
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-26T20:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )

    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": [child_job_id],
            "display_title": "closeout",
            "prompt": "closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": parent_uid,
                    "source_intent_uid": intent_uid,
                    "required_final_child_uid": child_uid,
                    "child_uids": [child_uid],
                    "child_job_ids": [child_job_id],
                }
            },
        },
        repo_root=tmp_path,
    )

    initial = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-26T21:00:00Z")}
    assert initial[closeout_job_id]["due"]["eligible"] is False
    assert initial[closeout_job_id]["due"]["reason"] == f"dependency_not_ok:{child_job_id}"

    child_receipt_path = (tmp_path / "child_receipt.json").resolve()
    child_receipt_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    child_state_path = sched._state_path(child_job_id, repo_root=tmp_path)
    child_state_path.parent.mkdir(parents=True, exist_ok=True)
    child_state_path.write_text(
        json.dumps({"job_id": child_job_id, "last_status": "ok", "last_receipt_path": str(child_receipt_path)}),
        encoding="utf-8",
    )

    admitted = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-26T21:00:01Z")}
    assert admitted[closeout_job_id]["due"]["eligible"] is True
    assert admitted[closeout_job_id]["due"]["reason"] == "due"

    def _fail_send(**kwargs):
        raise AssertionError("closeout should short-circuit before agent execution")

    monkeypatch.setattr(sched._agents, "send_message", _fail_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job(closeout_job_id, repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:01Z")
    receipt = sched.load_receipt(closeout_job_id, result["run_id"], repo_root=tmp_path)
    state = sched.load_state(closeout_job_id, repo_root=tmp_path)

    assert result["ok"] is True
    assert Path(result["receipt_path"]).exists()
    assert receipt["status"] == "ok"
    assert receipt["execution_family_closeout_short_circuit"]["applied"] is True
    assert receipt["execution_family_closeout_short_circuit"]["reason"] == "family_already_verified_at_execution"
    assert receipt["terminal_once_archive"]["archived"] is True
    assert receipt["loop"]["completed_iterations"] == 0
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    assert state["last_receipt_path"] == str(Path(result["receipt_path"]).resolve())
    with pytest.raises(FileNotFoundError):
        sched.load_job(closeout_job_id, repo_root=tmp_path)


def test_execution_family_closeout_queues_child_status_reconcile_before_failing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    intent_uid = "IS-20260326-000001Z-TEST"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{child_uid}__child-a"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_dir / "meta.json").write_text(
        json.dumps({"uid": child_uid, "title": "Child A", "status": "draft", "updated_utc": "2026-03-26T21:01:00Z"}),
        encoding="utf-8",
    )
    (child_dir / "events.jsonl").write_text("", encoding="utf-8")
    intent_dir = tmp_path / "governance" / "ledger" / "intent_sheets" / "causyl" / "active" / "2026" / "03" / f"{intent_uid}__intent"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "intent_sheet.md").write_text("# Intent\n", encoding="utf-8")
    (intent_dir / "meta.json").write_text(
        json.dumps({"uid": intent_uid, "title": "Intent", "status": "draft", "validating_pis_uids": []}),
        encoding="utf-8",
    )
    (intent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_job_id = sched._job_id_from_ledger_uid(child_uid, "Child A")
    sched.save_job(
        {
            "job_id": child_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_uid},
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-26T20:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    child_state_path = sched._state_path(child_job_id, repo_root=tmp_path)
    child_state_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = (tmp_path / "child_receipt.json").resolve()
    receipt_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    child_state_path.write_text(
        json.dumps({"job_id": child_job_id, "last_status": "ok", "last_receipt_path": str(receipt_path)}),
        encoding="utf-8",
    )

    closeout_job_id = sched._family_closeout_job_id(parent_uid)
    sched.save_job(
        {
            "job_id": closeout_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": parent_uid},
            "depends_on": [child_job_id],
            "display_title": "closeout",
            "prompt": "closeout",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": parent_uid,
                    "source_intent_uid": intent_uid,
                    "required_final_child_uid": child_uid,
                    "child_uids": [child_uid],
                    "child_job_ids": [child_job_id],
                }
            },
        },
        repo_root=tmp_path,
    )

    def _send(*, role: str, **kwargs):
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Changed files:\n"
                        f"- {parent_dir.relative_to(tmp_path).as_posix()}/meta.json\n\n"
                        "Validation Commands Run:\n"
                        "- python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    ),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    result = sched.execute_job(closeout_job_id, repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    receipt = sched.load_receipt(closeout_job_id, result["run_id"], repo_root=tmp_path)
    reconcile_job_id = "verify_a1b2_20260326_status_reconcile"
    reconcile_job = sched.load_job(reconcile_job_id, repo_root=tmp_path)

    assert result["ok"] is False
    assert receipt["status"] == "proof_gap"
    assert receipt["error"].startswith("execution_family_closeout_reconcile_queued:")
    assert receipt["loop"]["iterations"][0]["verifier"]["status"] == "reconcile_queued"
    assert reconcile_job["binding"] == {"type": "ledger_uid", "id": child_uid}
    assert reconcile_job["prompt_meta"]["generated_by_closeout_truth_reconcile"] is True


def test_execution_family_closeout_queues_reconcile_for_conditional_pass_child_success(tmp_path: Path) -> None:
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_uid = "PIS-20260326-210001Z-A1B2"
    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "child_uids": [child_uid],
                    "required_final_child_uid": child_uid,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{child_uid}__child-a"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_dir / "meta.json").write_text(json.dumps({"uid": child_uid, "title": "Child A", "status": "draft"}), encoding="utf-8")
    (child_dir / "events.jsonl").write_text("", encoding="utf-8")
    child_job_id = sched._job_id_from_ledger_uid(child_uid, "Child A")
    sched.save_job(
        {
            "job_id": child_job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": child_uid},
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-26T20:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.3-codex-spark"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        child_job_id,
        {"job_id": child_job_id, "last_status": "conditional_pass_repair_applied"},
        repo_root=tmp_path,
    )
    queued = sched._queue_execution_family_closeout_reconcile_jobs(
        {"job_id": "closeout_parent_job"},
        closeout_truth={
            "details": {
                "child_uids": [child_uid],
                "child_job_ids": [child_job_id],
                "child_statuses": {child_uid: "draft"},
                "child_job_statuses": {child_job_id: "conditional_pass_repair_applied"},
            }
        },
        repo_root=tmp_path,
    )
    assert queued["ok"] is True
    assert queued["queued_job_ids"] == ["verify_a1b2_20260326_status_reconcile"]


def test_build_verify_loop_can_queue_child_build_verify_jobs_after_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-203500Z-R7K2", "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_a = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-210001Z-A1B2__child-a"
    child_a.mkdir(parents=True, exist_ok=True)
    (child_a / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-210001Z-A1B2", "title": "Child A", "status": "draft"}), encoding="utf-8")
    (child_a / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_a / "events.jsonl").write_text(
        json.dumps({"type": "created", "payload": {"title": "Child A"}}) + "\n"
        + json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": "PIS-20260326-203500Z-R7K2",
                    "relationship": "execution_child",
                    "critical_path_rank": 1,
                    "depends_on": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": _valid_loop_builder_output(command="pytest -q test_target.py"), "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(command="pytest -q test_target.py"), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "parent_decompose_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "build_verify_loop", "max_iterations": 4}},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("parent_decompose_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    assert result["ok"] is True
    child_job_a = sched.load_job(sched._job_id_from_ledger_uid("PIS-20260326-210001Z-A1B2", "Child A"), repo_root=tmp_path)
    assert child_job_a["job_kind"] == "build_verify_loop"
    assert child_job_a["policy"]["post_success_commit_tag"] is True
    assert child_job_a["loop"]["max_iterations"] == 4
    assert child_job_a["prompt_meta"]["generated_child_mode"] == "build_verify_loop"
    assert child_job_a["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"


def test_build_verify_loop_rejects_illegal_child_queue_relationship_from_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-203500Z-R7K2__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-203500Z-R7K2", "title": "Parent item", "status": "draft"}), encoding="utf-8")
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    child_a = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "PIS-20260326-210001Z-A1B2__child-a"
    child_a.mkdir(parents=True, exist_ok=True)
    (child_a / "meta.json").write_text(json.dumps({"uid": "PIS-20260326-210001Z-A1B2", "title": "Child A", "status": "draft"}), encoding="utf-8")
    (child_a / "pis.md").write_text("# Child A\n", encoding="utf-8")
    (child_a / "events.jsonl").write_text(
        json.dumps({"type": "created", "payload": {"title": "Child A"}}) + "\n"
        + json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": "PIS-20260326-203500Z-R7K2",
                    "relationship": "execution_child",
                    "critical_path_rank": 1,
                    "depends_on": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {"ok": True, "assistant": {"content": _valid_loop_builder_output(command="pytest -q test_target.py"), "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(command="pytest -q test_target.py"), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "parent_decompose_illegal_child_shape_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Decompose parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-26T21:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900},
            "runtime": {"model": "gpt-5.4"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {"queue_child_implementation_jobs": {"enabled": True, "run_after_minutes": 5, "child_mode": "swarm_launch"}},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job("parent_decompose_illegal_child_shape_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-26T21:00:00Z")
    assert result["ok"] is False

    receipt = sched.load_receipt("parent_decompose_illegal_child_shape_001", result["run_id"], repo_root=tmp_path)
    assert receipt["child_job_queue"]["created_count"] == 0
    assert "illegal parent-child job-shape relationship" in receipt["child_job_queue"]["error"]

    child_job_id = sched._job_id_from_ledger_uid("PIS-20260326-210001Z-A1B2", "Child A")
    with pytest.raises(FileNotFoundError):
        sched.load_job(child_job_id, repo_root=tmp_path)


def test_scheduled_build_verify_loop_can_run_post_success_commit_tag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(
        sched,
        "_run_post_success_commit_tag",
        lambda **kwargs: {"ok": True, "command": "bash tools/git/git-commit-tag.sh", "exit_code": 0, "stdout": "tagged", "stderr": ""},
    )

    sched.save_job(
        {
            "job_id": "nightly_loop_tagged_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DF0C"},
            "prompt": "Implement and verify DF0C.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "post_success_commit_tag": True},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_tagged_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True

    receipt = sched.load_receipt("nightly_loop_tagged_001", result["run_id"], repo_root=tmp_path)
    assert receipt["git_commit_tag"]["ok"] is True
    assert receipt["git_commit_tag"]["exit_code"] == 0
    assert len(receipt["git_commit_tag"]["attempts"]) == 1


def test_build_verify_loop_auto_commit_can_be_disabled_explicitly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    called = {"commit": 0}
    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_run_post_success_commit_tag", lambda **kwargs: called.__setitem__("commit", called["commit"] + 1) or {"ok": True, "command": "", "exit_code": 0, "stdout": "", "stderr": ""})

    sched.save_job(
        {
            "job_id": "nightly_loop_no_auto_commit_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DF0C"},
            "prompt": "Implement and verify DF0C.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "post_success_commit_tag": False},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    saved = sched.load_job("nightly_loop_no_auto_commit_001", repo_root=tmp_path)
    assert saved["policy"]["post_success_commit_tag"] is False
    result = sched.execute_job("nightly_loop_no_auto_commit_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_loop_no_auto_commit_001", result["run_id"], repo_root=tmp_path)
    assert "git_commit_tag" not in receipt
    assert called["commit"] == 0


def test_post_success_commit_records_verifier_fragment_when_commit_attempted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS verified with focused checks and concrete evidence\n"
                    "Findings:\n"
                    "- none\n\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass\n"
                    "- contract_alignment: pass\n\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient\n"
                    "- pass_fail_closed_check: satisfied\n"
                    "- remaining_proof_gaps: none\n\n"
                    "Validation Review:\n"
                    "- commands_reviewed: python -m py_compile infra/controlplane/scheduled_jobs.py\n"
                    "- outcome_support: sufficient\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    journal_calls: list[dict[str, object]] = []

    def _fake_write_entry(entry: dict[str, object], *, repo=None):
        journal_calls.append(entry)
        out = tmp_path / "runtime" / "state" / "commit_journal" / "pending" / "fake_scheduler_verify.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}", encoding="utf-8")
        return out

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: " M infra/controlplane/scheduled_jobs.py")
    monkeypatch.setattr(sched._commit_journal, "write_entry", _fake_write_entry)
    monkeypatch.setattr(
        sched,
        "_run_post_success_commit_tag",
        lambda **kwargs: {"ok": True, "command": "bash tools/git/git-commit-tag.sh", "exit_code": 0, "stdout": "tagged", "stderr": ""},
    )

    sched.save_job(
        {
            "job_id": "nightly_loop_verify_fragment_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DF0C"},
            "prompt": "Implement and verify DF0C.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_verify_fragment_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("nightly_loop_verify_fragment_001", result["run_id"], repo_root=tmp_path)
    assert receipt["verification_commit_fragment"]["skipped"] is False
    assert journal_calls
    summary = str(journal_calls[0]["summary_bullets"][0])
    expected_model = sched._scheduler_default_model(job_kind="build_verify_loop")
    assert "VERDICT: PASS verified with focused checks and concrete evidence" in summary
    assert "iterations=1" in summary
    assert f"builder_runtime=codex:{expected_model}" in summary
    assert f"verifier_runtime=codex:{expected_model}" in summary


def test_scheduled_commit_tag_retries_until_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    seen = {"calls": 0, "sleeps": []}

    def _fake_commit(**kwargs):
        seen["calls"] += 1
        if seen["calls"] < 3:
            return {"ok": False, "command": "bash tools/git/git-commit-tag.sh", "exit_code": 1, "stdout": "", "stderr": "index.lock"}
        return {"ok": True, "command": "bash tools/git/git-commit-tag.sh", "exit_code": 0, "stdout": "tagged", "stderr": ""}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_run_post_success_commit_tag", _fake_commit)
    monkeypatch.setattr(sched.time, "sleep", lambda seconds: seen["sleeps"].append(seconds))
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_loop_tagged_retry_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DF0C"},
            "prompt": "Implement and verify DF0C.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 600,
                "post_success_commit_tag": True,
                "post_success_commit_tag_retries": 2,
                "post_success_commit_tag_backoff_seconds": 60,
            },
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_tagged_retry_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    assert seen["calls"] == 3
    assert seen["sleeps"] == [60, 60]

    receipt = sched.load_receipt("nightly_loop_tagged_retry_001", result["run_id"], repo_root=tmp_path)
    assert receipt["git_commit_tag"]["ok"] is True
    assert len(receipt["git_commit_tag"]["attempts"]) == 3


def test_scheduled_job_retry_classification_tracks_post_success_commit_tag_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": _valid_loop_builder_output(),
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": _valid_loop_verifier_pass(), "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)
    monkeypatch.setattr(sched, "_run_post_success_commit_tag", lambda **kwargs: {"ok": False, "command": "bash tools/git/git-commit-tag.sh", "exit_code": 17, "stdout": "", "stderr": "index.lock"})
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")

    sched.save_job(
        {
            "job_id": "nightly_loop_tagged_fail_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DF0C"},
            "prompt": "Implement and verify DF0C.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "post_success_commit_tag": True},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.execute_job("nightly_loop_tagged_fail_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is False

    receipt = sched.load_receipt("nightly_loop_tagged_fail_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "error"
    assert str(receipt["error"]).startswith("post-success git commit/tag failed")
    assert receipt["job_retry"]["final_failure_class"] == "post_success_commit_tag_failed"
    assert receipt["job_retry"]["final_failure_reason"] == "commit_tag_has_dedicated_retry_policy"


def test_scheduled_job_dependencies_delay_until_prior_job_is_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "pis_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-2848"},
            "prompt": "Implement and verify 2848.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )
    sched.save_job(
        {
            "job_id": "pis_002",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "depends_on": ["pis_001"],
            "prompt": "Implement and verify BD05.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    initial = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert initial[1]["due"]["reason"] == "dependency_pending:pis_001"

    sched._write_state("pis_001", {"last_status": "ok"}, repo_root=tmp_path)
    later = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert later[1]["due"]["eligible"] is True


def test_scheduler_tick_records_dependency_block_as_durable_nonlaunch_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "dep_parent_001",
            "job_kind": "build_verify_loop",
            "enabled": False,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-2848"},
            "prompt": "Implement and verify parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )
    sched.save_job(
        {
            "job_id": "dep_child_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "depends_on": ["dep_parent_001"],
            "prompt": "Implement and verify child.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    skipped = [row for row in list(result.get("skipped") or []) if row.get("job_id") == "dep_child_001"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "dependency_not_ok:dep_parent_001"
    assert skipped[0]["decision_status"] == "blocked"
    assert Path(skipped[0]["receipt_path"]).exists()
    assert Path(skipped[0]["report_path"]).exists()

    receipt_path = Path(skipped[0]["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked"
    assert receipt["reason"] == "dependency_not_ok:dep_parent_001"
    assert receipt["ownership"]["handoff_state"] == "not_launched"

    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(row["event"] == "blocked" and row["job_id"] == "dep_child_001" for row in events)


def test_scheduler_tick_keeps_same_tick_dependency_waiting_out_of_durable_blocked_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "dep_parent_pending_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-2848"},
            "prompt": "Implement and verify parent.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )
    sched.save_job(
        {
            "job_id": "dep_child_pending_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260312-161622Z-BD05"},
            "depends_on": ["dep_parent_pending_001"],
            "prompt": "Implement and verify child.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    skipped = [row for row in list(result.get("skipped") or []) if row.get("job_id") == "dep_child_pending_001"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "dependency_pending:dep_parent_pending_001"
    assert "receipt_path" not in skipped[0]

    state = sched.load_state("dep_child_pending_001", repo_root=tmp_path)
    assert str(state.get("last_status") or "") == ""
    assert str(state.get("last_nonlaunch_receipt_path") or "") == ""

    sched._write_state("dep_parent_pending_001", {"last_status": "ok"}, repo_root=tmp_path)
    later = {row["job_id"]: row for row in sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:30Z")}
    assert later["dep_child_pending_001"]["due"]["eligible"] is True

    events = sched.list_execution_events(repo_root=tmp_path)
    assert not any(row["event"] == "blocked" and row["job_id"] == "dep_child_pending_001" for row in events)


def test_scheduler_defers_build_verify_loop_when_execution_queue_paused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched,
        "_execution_queue",
        type(
            "_QueueStub",
            (),
            {"status": staticmethod(lambda: {"ok": True, "state": {"status": "paused", "stop_requested": False}})},
        )(),
    )
    send_calls: list[dict[str, Any]] = []

    def _capture_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        send_calls.append({"role": role, "binding": dict(binding), "message": message, "options": dict(options or {})})
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _capture_send)

    sched.save_job(
        {
            "job_id": "paused_queue_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260313-030603Z-X4Y6"},
            "prompt": "Implement exactly one child PIS.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        }
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert result["executed_count"] == 0
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "execution_queue_paused"
    assert send_calls == []

    state = sched.load_state("paused_queue_loop_001", repo_root=tmp_path)
    assert state["last_nonlaunch_reason"] == "execution_queue_paused"
    assert state["last_nonlaunch_status"] == "deferred"
    assert Path(state["last_nonlaunch_receipt_path"]).exists()

    receipt = json.loads(Path(state["last_nonlaunch_receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "deferred"
    assert receipt["ownership"]["handoff_state"] == "not_launched"

    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(row["event"] == "deferred" and row["job_id"] == "paused_queue_loop_001" for row in events)


def test_scheduled_launch_receipt_explicitly_marks_runtime_handoff_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "handoff_scope_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a handoff memo.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    result = sched.execute_job("handoff_scope_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-08T04:00:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("handoff_scope_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["ownership"]["handoff_state"] == "launched"
    assert receipt["ownership"]["scheduler_scope"] == "launch_intent_and_handoff_evidence"
    assert receipt["ownership"]["runtime_scope"] == "runtime_execution_and_progress"


def test_scheduler_service_executes_due_jobs_and_updates_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    sched.stop_scheduler_service()

    sched.save_job(
        {
            "job_id": "service_job_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write the scheduled report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    real_parse = sched._parse_utc
    monkeypatch.setattr(sched, "_utc_now", lambda: real_parse("2026-03-08T04:00:00Z"))

    state = sched.start_scheduler_service(interval_s=5, repo_root=tmp_path)
    assert state["running"] is True

    deadline = time.time() + 10.0
    while time.time() < deadline:
        status = sched.scheduler_service_status()
        if status.get("last_tick_utc") or status.get("last_error"):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("scheduler service did not tick")

    assert sched.scheduler_service_status()["last_error"] == ""
    deadline = time.time() + 10.0
    receipts = []
    while time.time() < deadline:
        receipts = sched.list_receipts("service_job_001", limit=10)
        if receipts:
            break
        time.sleep(0.05)
    assert len(receipts) == 1
    assert sched.scheduler_service_status()["last_result"]["executed_count"] == 0
    stopped = sched.stop_scheduler_service()
    assert stopped["running"] is False


def test_ensure_watchdog_service_liveness_restarts_stale_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_WATCHDOG_STATE",
        {
            "running": True,
            "interval_s": 120,
            "started_utc": "2026-03-08T03:00:00Z",
            "last_receipt_utc": "2026-03-08T03:01:00Z",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_WATCHDOG_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T03:20:00Z"))

    calls: list[str] = []

    def _stop_watchdog_service(*, timeout_s: float = 2.0) -> dict:
        calls.append(f"stop:{timeout_s}")
        return {"running": False}

    def _start_watchdog_service(*, interval_s: int = 900, repo_root=None) -> dict:
        calls.append(f"start:{interval_s}")
        sched._WATCHDOG_STATE = {
            "running": True,
            "interval_s": interval_s,
            "started_utc": "2026-03-08T03:20:00Z",
            "last_receipt_utc": "2026-03-08T03:20:00Z",
            "repo_root": str(repo_root or tmp_path),
        }
        sched._WATCHDOG_THREAD = _AliveThread()
        return dict(sched._WATCHDOG_STATE)

    monkeypatch.setattr(sched, "stop_watchdog_service", _stop_watchdog_service)
    monkeypatch.setattr(sched, "start_watchdog_service", _start_watchdog_service)

    result = sched.ensure_watchdog_service_liveness(repo_root=tmp_path)

    assert result["restart_attempted"] is True
    assert result["restart_ok"] is True
    assert result["health"] == "healthy"
    assert calls == ["stop:0.5", "start:120"]


def test_ensure_scheduler_service_liveness_does_not_restart_recent_startup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_SCHEDULER_STATE",
        {
            "running": True,
            "interval_s": 30,
            "started_utc": "2026-03-08T04:00:00Z",
            "last_tick_utc": "",
            "last_result": {},
            "last_error": "",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_SCHEDULER_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T04:00:20Z"))
    monkeypatch.setattr(sched, "_get_active_job_count", lambda: 0)

    def _stop_scheduler_service(*, timeout_s: float = 2.0) -> dict:
        raise AssertionError("recent scheduler startup should not be restarted")

    monkeypatch.setattr(sched, "stop_scheduler_service", _stop_scheduler_service)

    result = sched.ensure_scheduler_service_liveness(repo_root=tmp_path)

    assert result["health"] == "degraded"
    assert result["reason"] == "starting_without_progress"
    assert result["restart_attempted"] is False


def test_ensure_scheduler_service_liveness_treats_fresh_durable_active_progress_as_healthy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_SCHEDULER_STATE",
        {
            "running": True,
            "interval_s": 30,
            "started_utc": "2026-03-08T04:00:00Z",
            "last_tick_utc": "",
            "last_result": {},
            "last_error": "",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_SCHEDULER_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T04:10:00Z"))
    monkeypatch.setattr(sched, "_active_job_claims", lambda **kwargs: ["job_live_001"])
    monkeypatch.setattr(
        sched,
        "load_state",
        lambda job_id, repo_root=None: {
            "job_id": job_id,
            "active_run_id": "sj_live_001",
            "active_loop_updated_utc": "2026-03-08T04:09:30Z",
            "updated_utc": "2026-03-08T04:09:30Z",
            "last_status": "running",
        },
    )

    result = sched.ensure_scheduler_service_liveness(repo_root=tmp_path)

    assert result["health"] == "healthy"
    assert result["reason"] == "durable_active_progress_without_tick"
    assert result["restart_attempted"] is False
    assert result["active_claim_count"] == 1
    assert result["active_claim_job_ids"] == ["job_live_001"]


def test_ensure_scheduler_service_liveness_prioritizes_active_claim_continuity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_SCHEDULER_STATE",
        {
            "running": True,
            "interval_s": 30,
            "started_utc": "2026-03-08T03:00:00Z",
            "last_tick_utc": "2026-03-08T03:00:00Z",
            "last_result": {},
            "last_error": "scheduler_tick_timeout:20s",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_SCHEDULER_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T03:20:00Z"))
    monkeypatch.setattr(sched, "_get_active_job_count", lambda: 0)
    monkeypatch.setattr(sched, "_active_job_claims", lambda **kwargs: ["job_live_001"])

    def _stop_scheduler_service(*, timeout_s: float = 2.0) -> dict:
        raise AssertionError("active claims should prevent restart")

    monkeypatch.setattr(sched, "stop_scheduler_service", _stop_scheduler_service)

    result = sched.ensure_scheduler_service_liveness(repo_root=tmp_path)

    assert result["health"] == "degraded"
    assert result["reason"] == "active_job_progress_detected"
    assert result["restart_attempted"] is False
    assert result["active_claim_count"] == 1
    assert result["active_claim_job_ids"] == ["job_live_001"]


def test_scheduler_service_error_clears_stale_last_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _failing_tick(*, repo_root=None, limit=10, await_completion=False):
        raise RuntimeError("boom")

    monkeypatch.setattr(sched, "scheduler_tick", _failing_tick)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda **kwargs: {"running": True})
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda **kwargs: {"health": "healthy"})
    monkeypatch.setattr(sched, "_scheduler_owned_reconciliation_pass", lambda **kwargs: {"ok": True})

    state = sched.start_scheduler_service(interval_s=1, repo_root=tmp_path)
    assert state["running"] is True
    try:
        with sched._SCHEDULER_GUARD:
            sched._SCHEDULER_STATE["last_result"] = {"active_count": 99}
        deadline = time.time() + 5.0
        while time.time() < deadline:
            status = sched.scheduler_service_status()
            if str(status.get("last_error") or "") == "boom":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("scheduler error did not surface")

        status = sched.scheduler_service_status()
        assert status["last_result"] == {}
        assert str(status["last_error"]) == "boom"
    finally:
        sched.stop_scheduler_service(timeout_s=0.5)


def test_start_scheduler_service_uses_scheduler_owned_reconciliation_and_does_not_start_healer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    calls: list[tuple[str, bool]] = []

    def _reconcile(*, repo_root=None, startup=False):
        calls.append(("reconcile", bool(startup)))
        return {"ok": True, "startup": bool(startup)}

    def _start_healer_service(**kwargs):
        raise AssertionError("scheduler service should not auto-start healer authority")

    monkeypatch.setattr(sched, "_scheduler_owned_reconciliation_pass", _reconcile)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda **kwargs: {"running": True})
    monkeypatch.setattr(sched, "start_healer_service", _start_healer_service)
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda **kwargs: {"health": "healthy"})
    monkeypatch.setattr(sched, "_scheduler_service_tick", lambda **kwargs: {"ok": True, "active_count": 0})

    state = sched.start_scheduler_service(interval_s=1, repo_root=tmp_path)
    try:
        assert state["running"] is True
        assert state["startup_reconciliation"]["ok"] is True
        assert calls[0] == ("reconcile", True)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            status = sched.scheduler_service_status()
            if status.get("last_tick_utc"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("scheduler service did not tick")

        status = sched.scheduler_service_status()
        assert status["last_reconciliation"]["ok"] is True
        assert ("reconcile", False) in calls
    finally:
        sched.stop_scheduler_service(timeout_s=0.5)


def test_start_scheduler_service_repeats_bounded_startup_reconciliation_across_restarts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    calls: list[tuple[str, bool]] = []

    def _reconcile(*, repo_root=None, startup=False):
        calls.append(("reconcile", bool(startup)))
        return {"ok": True, "startup": bool(startup), "call_index": len(calls)}

    def _start_healer_service(**kwargs):
        raise AssertionError("scheduler service should not auto-start healer authority")

    monkeypatch.setattr(sched, "_scheduler_owned_reconciliation_pass", _reconcile)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda **kwargs: {"running": True})
    monkeypatch.setattr(sched, "start_healer_service", _start_healer_service)
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda **kwargs: {"health": "healthy"})
    monkeypatch.setattr(sched, "_scheduler_service_tick", lambda **kwargs: {"ok": True, "active_count": 0})

    for _ in range(2):
        state = sched.start_scheduler_service(interval_s=1, repo_root=tmp_path)
        assert state["running"] is True
        assert state["startup_reconciliation"]["ok"] is True
        deadline = time.time() + 5.0
        while time.time() < deadline:
            status = sched.scheduler_service_status()
            if status.get("last_tick_utc"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("scheduler service did not tick after restart")
        stopped = sched.stop_scheduler_service(timeout_s=0.5)
        assert stopped["running"] is False

    startup_calls = [call for call in calls if call == ("reconcile", True)]
    steady_calls = [call for call in calls if call == ("reconcile", False)]
    assert len(startup_calls) == 2
    assert len(steady_calls) >= 2


def test_scheduler_v2_tick_submits_first_dependency_ready_child_without_due_status(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        runtime_policy,
        "get_runtime_policy_text",
        lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default,
    )

    plan = SimpleNamespace(
        parent_uid="PIS-TEST-PARENT",
        jobs=[
            SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
            SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
            SimpleNamespace(job_id="job_c", child_uid="PIS-C", depends_on_uids=("PIS-B",)),
        ],
    )
    monkeypatch.setattr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan)

    state_by_job = {
        "job_a": {"last_status": "ok", "active_run_id": ""},
        "job_b": {"last_status": "failed_verification", "active_run_id": "", "run_now_requested_utc": "2026-03-08T04:00:00Z"},
        "job_c": {"last_status": "", "active_run_id": ""},
    }
    monkeypatch.setattr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id]))
    monkeypatch.setattr(
        sched,
        "load_job",
        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
    )
    monkeypatch.setattr(
        sched,
        "due_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("v2 family tick must not consult due_status")),
    )

    class _Future:
        def done(self) -> bool:
            return False

    submissions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Pool:
        def submit(self, *args, **kwargs):
            submissions.append((args, kwargs))
            return _Future()

    monkeypatch.setattr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool())
    monkeypatch.setattr(sched, "_get_active_job_count", lambda: len(submissions))
    monkeypatch.setattr(sched, "_dispatch_future_active", lambda job_id: False)

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()

    result = sched._scheduler_v2_tick(repo_root=tmp_path, now=sched._parse_utc("2026-03-08T04:00:00Z"))

    assert result["ok"] is True
    assert result["submitted_count"] == 1
    assert result["submitted"] == [{"job_id": "job_b", "projection_job_id": "job_b", "child_uid": "PIS-B"}]
    assert submissions
    submit_args, submit_kwargs = submissions[0]
    assert submit_args[0] is sched.execute_job
    assert submit_args[1] == "job_b"
    assert submit_kwargs["force"] is True

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()


def test_scheduler_v2_tick_hands_off_to_next_dependency_ready_child_without_manual_nudge(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        runtime_policy,
        "get_runtime_policy_text",
        lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default,
    )

    plan = SimpleNamespace(
        parent_uid="PIS-TEST-PARENT",
        jobs=[
            SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
            SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
            SimpleNamespace(job_id="job_c", child_uid="PIS-C", depends_on_uids=("PIS-B",)),
        ],
    )
    monkeypatch.setattr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan)

    state_by_job = {
        "job_a": {"last_status": "ok", "active_run_id": ""},
        "job_b": {"last_status": "", "active_run_id": ""},
        "job_c": {"last_status": "", "active_run_id": ""},
    }
    monkeypatch.setattr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id]))
    monkeypatch.setattr(
        sched,
        "load_job",
        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
    )
    monkeypatch.setattr(
        sched,
        "due_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("v2 family tick must not consult due_status")),
    )

    class _Future:
        def done(self) -> bool:
            return False

    submissions: list[str] = []

    class _Pool:
        def submit(self, *args, **kwargs):
            submissions.append(str(args[1]))
            return _Future()

    monkeypatch.setattr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool())
    monkeypatch.setattr(sched, "_dispatch_future_active", lambda job_id: False)

    def _active_job_count() -> int:
        return len(submissions)

    monkeypatch.setattr(sched, "_get_active_job_count", _active_job_count)

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()

    first = sched._scheduler_v2_tick(repo_root=tmp_path, now=sched._parse_utc("2026-03-08T04:00:00Z"))
    assert first["submitted"] == [{"job_id": "job_b", "projection_job_id": "job_b", "child_uid": "PIS-B"}]
    assert submissions == ["job_b"]

    submissions.clear()
    state_by_job["job_b"] = {"last_status": "ok", "active_run_id": ""}
    second = sched._scheduler_v2_tick(repo_root=tmp_path, now=sched._parse_utc("2026-03-08T04:01:00Z"))
    assert second["submitted"] == [{"job_id": "job_c", "projection_job_id": "job_c", "child_uid": "PIS-C"}]
    assert submissions == ["job_c"]

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()


def test_scheduler_v2_tick_blocks_poisoned_terminal_active_dependency_state(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        runtime_policy,
        "get_runtime_policy_text",
        lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default,
    )

    plan = SimpleNamespace(
        parent_uid="PIS-TEST-PARENT",
        jobs=[
            SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
            SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
        ],
    )
    monkeypatch.setattr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan)

    state_by_job = {
        "job_a": {"last_status": "ok", "active_run_id": "sj_poisoned_a"},
        "job_b": {"last_status": "", "active_run_id": ""},
    }
    monkeypatch.setattr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id]))
    monkeypatch.setattr(
        sched,
        "load_job",
        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
    )

    class _Future:
        def done(self) -> bool:
            return False

    submissions: list[str] = []

    class _Pool:
        def submit(self, *args, **kwargs):
            submissions.append(str(args[1]))
            return _Future()

    monkeypatch.setattr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool())
    monkeypatch.setattr(sched, "_dispatch_future_active", lambda job_id: False)
    monkeypatch.setattr(sched, "_get_active_job_count", lambda: 0)

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()

    result = sched._scheduler_v2_tick(repo_root=tmp_path, now=sched._parse_utc("2026-03-08T04:00:00Z"))

    assert result["submitted_count"] == 0
    assert submissions == []
    assert {"job_id": "job_a", "reason": "truth_ambiguous:terminal_success_and_active_run"} in result["skipped"]
    assert {"job_id": "job_b", "reason": "dependency_truth_ambiguous:PIS-A"} in result["skipped"]


def test_scheduler_v2_tick_marks_multiple_active_children_as_truth_ambiguous(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        runtime_policy,
        "get_runtime_policy_text",
        lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default,
    )

    plan = SimpleNamespace(
        parent_uid="PIS-TEST-PARENT",
        jobs=[
            SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
            SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
            SimpleNamespace(job_id="job_c", child_uid="PIS-C", depends_on_uids=("PIS-B",)),
        ],
    )
    monkeypatch.setattr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan)

    state_by_job = {
        "job_a": {"last_status": "running", "active_run_id": "sj_a"},
        "job_b": {"last_status": "running", "active_run_id": "sj_b"},
        "job_c": {"last_status": "", "active_run_id": ""},
    }
    monkeypatch.setattr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id]))
    monkeypatch.setattr(
        sched,
        "load_job",
        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
    )

    class _Future:
        def done(self) -> bool:
            return False

    submissions: list[str] = []

    class _Pool:
        def submit(self, *args, **kwargs):
            submissions.append(str(args[1]))
            return _Future()

    monkeypatch.setattr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool())
    monkeypatch.setattr(sched, "_dispatch_future_active", lambda job_id: False)
    monkeypatch.setattr(sched, "_get_active_job_count", lambda: 0)

    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()

    result = sched._scheduler_v2_tick(repo_root=tmp_path, now=sched._parse_utc("2026-03-08T04:00:00Z"))

    assert result["submitted_count"] == 0
    assert submissions == []
    assert {"job_id": "job_a", "reason": "truth_ambiguous:multiple_active_children"} in result["skipped"]
    assert {"job_id": "job_b", "reason": "truth_ambiguous:multiple_active_children"} in result["skipped"]
    assert {"job_id": "job_c", "reason": "dependency_truth_ambiguous:multiple_active_children"} in result["skipped"]


def test_list_jobs_marks_running_without_active_run_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "restore_stranded_running_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "restore_stranded_running_task"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )
    sched._write_state(
        "restore_stranded_running_001",
        {
            "job_id": "restore_stranded_running_001",
            "active_run_id": "",
            "last_status": "running",
            "updated_utc": "2026-03-08T03:59:00Z",
        },
        repo_root=tmp_path,
    )

    row = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")[0]

    assert row["scheduler_projection_v1"]["projected_status"] == "ambiguous"
    assert row["scheduler_projection_v1"]["projection_reason"] == "running_status_without_active_run"
    assert row["scheduler_truth_degraded"] is True


def test_scheduler_owned_reconciliation_reconciles_stranded_running_terminal_receipt_after_restore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "restore_terminal_receipt_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "restore_terminal_receipt_task"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )
    receipt_path = sched.receipts_dir(tmp_path) / "restore_terminal_receipt_001" / "sj_restore_terminal_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": "restore_terminal_receipt_001",
                "run_id": "sj_restore_terminal_001",
                "status": "ok",
                "finished_at": "2026-03-08T03:59:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sched._write_state(
        "restore_terminal_receipt_001",
        {
            "job_id": "restore_terminal_receipt_001",
            "active_run_id": "",
            "last_status": "running",
            "last_receipt_path": str(receipt_path.resolve()),
            "updated_utc": "2026-03-08T03:59:30Z",
        },
        repo_root=tmp_path,
    )

    result = sched._scheduler_owned_reconciliation_pass(repo_root=tmp_path, startup=True)
    state = sched.load_state("restore_terminal_receipt_001", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["stranded_running_reconciliation"]["reconciled_count"] == 1
    assert state["last_status"] == "ok"
    assert state["active_run_id"] == ""
    assert state["last_reconciliation_reason"] == "already_executed"
    row = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")[0]
    assert row["scheduler_projection_v1"]["projected_status"] == "completed_once"


def test_scheduler_owned_reconciliation_blocks_stranded_running_without_terminal_receipt_after_restore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "restore_stranded_blocked_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "restore_stranded_blocked_task"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )
    sched._write_state(
        "restore_stranded_blocked_001",
        {
            "job_id": "restore_stranded_blocked_001",
            "active_run_id": "",
            "last_status": "running",
            "updated_utc": "2026-03-08T03:59:30Z",
        },
        repo_root=tmp_path,
    )

    result = sched._scheduler_owned_reconciliation_pass(repo_root=tmp_path, startup=True)
    state = sched.load_state("restore_stranded_blocked_001", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["stranded_running_reconciliation"]["reconciled_count"] == 1
    assert state["last_status"] == "blocked"
    assert state["last_nonlaunch_reason"] == "startup_stranded_running_without_live_claim"
    row = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")[0]
    assert row["scheduler_projection_v1"]["projected_status"] == "blocked"


def test_scheduler_owned_reconciliation_blocks_running_active_run_missing_claim_after_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "restore_active_run_missing_claim_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260414-043438Z-K1M4"},
            "prompt": "Implement and verify K1M4.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
        }
    )
    sched._write_state(
        "restore_active_run_missing_claim_001",
        {
            "job_id": "restore_active_run_missing_claim_001",
            "active_run_id": "sj_restore_missing_claim_001",
            "active_started_utc": "2026-03-08T03:58:00Z",
            "active_loop_iteration": 1,
            "active_loop_stage": "verifier",
            "active_loop_completed_iterations": 0,
            "active_loop_max_iterations": 4,
            "active_loop_updated_utc": "2026-03-08T03:59:00Z",
            "last_status": "running",
            "updated_utc": "2026-03-08T03:59:30Z",
        },
        repo_root=tmp_path,
    )

    result = sched._scheduler_owned_reconciliation_pass(repo_root=tmp_path, startup=True)
    state = sched.load_state("restore_active_run_missing_claim_001", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["stranded_running_reconciliation"]["reconciled_count"] == 1
    assert state["last_status"] == "blocked"
    assert state["active_run_id"] == ""
    assert state["last_nonlaunch_reason"] == "startup_stranded_running_without_live_claim"
    assert state["last_nonlaunch_run_id"] == "sj_restore_missing_claim_001"
    row = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")[0]
    assert row["scheduler_projection_v1"]["projected_status"] == "blocked"


def test_scheduler_owned_reconciliation_ignores_stale_terminal_receipt_for_new_active_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    job_id = "restore_active_run_stale_receipt_001"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260414-043438Z-K1M4"},
            "prompt": "Implement and verify K1M4.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
        }
    )
    receipt_path = sched.receipts_dir(tmp_path) / job_id / "sj_old_cancel.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "run_id": "sj_old_cancel",
                "status": "cancelled",
                "finished_utc": "2026-03-08T03:50:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sched._write_state(
        job_id,
        {
            "job_id": job_id,
            "active_run_id": "sj_current_missing_claim",
            "active_started_utc": "2026-03-08T03:58:00Z",
            "last_status": "running",
            "last_receipt_path": str(receipt_path.resolve()),
            "updated_utc": "2026-03-08T03:59:30Z",
        },
        repo_root=tmp_path,
    )

    result = sched._scheduler_owned_reconciliation_pass(repo_root=tmp_path, startup=True)
    state = sched.load_state(job_id, repo_root=tmp_path)

    assert result["ok"] is True
    assert result["stranded_running_reconciliation"]["reconciled_count"] == 1
    assert state["last_status"] == "blocked"
    assert state["last_nonlaunch_reason"] == "startup_stranded_running_without_live_claim"
    assert state["last_nonlaunch_run_id"] == "sj_current_missing_claim"
    assert state["last_receipt_path"] == ""


def test_sched_v2_runtime_state_does_not_treat_run_now_requested_as_active() -> None:
    from infra.controlplane.sched import runtime as sched_v2_runtime

    assert sched_v2_runtime._runtime_state_from_legacy_state({"run_now_requested_utc": "2026-03-08T04:00:00Z"}) == ""


def test_ensure_healer_service_liveness_restarts_stale_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_HEALER_STATE",
        {
            "running": True,
            "interval_s": 300,
            "started_utc": "2026-03-08T03:00:00Z",
            "last_receipt_utc": "2026-03-08T03:01:00Z",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_HEALER_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T03:40:00Z"))

    calls: list[str] = []

    def _stop_healer_service(*, timeout_s: float = 2.0) -> dict:
        calls.append(f"stop:{timeout_s}")
        return {"running": False}

    def _start_healer_service(*, interval_s: int = 300, repo_root=None) -> dict:
        calls.append(f"start:{interval_s}")
        sched._HEALER_STATE = {
            "running": True,
            "interval_s": interval_s,
            "started_utc": "2026-03-08T03:40:00Z",
            "last_receipt_utc": "2026-03-08T03:40:00Z",
            "repo_root": str(repo_root or tmp_path),
        }
        sched._HEALER_THREAD = _AliveThread()
        return dict(sched._HEALER_STATE)

    monkeypatch.setattr(sched, "stop_healer_service", _stop_healer_service)
    monkeypatch.setattr(sched, "start_healer_service", _start_healer_service)

    result = sched.ensure_healer_service_liveness(repo_root=tmp_path)

    assert result["restart_attempted"] is True
    assert result["restart_ok"] is True
    assert result["health"] == "healthy"
    assert calls == ["stop:0.5", "start:300"]


def test_start_scheduler_service_restarts_when_repo_root_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir(parents=True, exist_ok=True)
    root_b.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[str, str]] = []

    def _fake_scheduler_service_tick(*, repo_root=None, limit=10):
        calls.append(("tick", str(Path(repo_root or tmp_path).resolve())))
        return {
            "ok": True,
            "utc": sched._utc_iso(),
            "backend": "legacy",
            "submitted": [],
            "submitted_count": 0,
            "deferred": [],
            "deferred_count": 0,
            "skipped": [],
            "skipped_count": 0,
            "active_count": 0,
            "executed": [],
            "executed_count": 0,
        }

    monkeypatch.setattr(sched, "_scheduler_service_tick", _fake_scheduler_service_tick)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda interval_s=900, repo_root=None: {"running": True})
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda repo_root=None: {"health": "healthy"})

    def _wait_for_call(target_root: Path) -> bool:
        deadline = time.monotonic() + 1.0
        expected = str(target_root.resolve())
        while time.monotonic() < deadline:
            if any(root == expected for kind, root in calls if kind == "tick"):
                return True
            time.sleep(0.02)
        return any(root == expected for kind, root in calls if kind == "tick")

    first = sched.start_scheduler_service(interval_s=5, repo_root=root_a)
    assert first["repo_root"] == str(root_a.resolve())
    assert _wait_for_call(root_a)

    second = sched.start_scheduler_service(interval_s=5, repo_root=root_b)
    assert second["repo_root"] == str(root_b.resolve())
    assert _wait_for_call(root_b)

    sched.stop_scheduler_service(timeout_s=0.5)


def test_ensure_healer_service_liveness_treats_unstarted_service_as_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_HEALER_STATE", {"running": False, "interval_s": 300, "started_utc": "", "last_receipt_utc": ""})
    monkeypatch.setattr(sched, "_HEALER_THREAD", None)

    def _start_healer_service(**kwargs):
        raise AssertionError("disabled healer service should not be auto-started")

    monkeypatch.setattr(sched, "start_healer_service", _start_healer_service)

    result = sched.ensure_healer_service_liveness(repo_root=tmp_path)

    assert result["health"] == "disabled"
    assert result["reason"] == "scheduler_owned_reconciliation"
    assert result["restart_attempted"] is False


def test_ensure_healer_service_liveness_does_not_restart_recent_startup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        sched,
        "_HEALER_STATE",
        {
            "running": True,
            "interval_s": 300,
            "started_utc": "2026-03-08T03:40:00Z",
            "last_receipt_utc": "",
            "repo_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(sched, "_HEALER_THREAD", _AliveThread())
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-08T03:41:00Z"))

    def _stop_healer_service(*, timeout_s: float = 2.0) -> dict:
        raise AssertionError("recent healer startup should not be restarted")

    monkeypatch.setattr(sched, "stop_healer_service", _stop_healer_service)

    result = sched.ensure_healer_service_liveness(repo_root=tmp_path)

    assert result["health"] == "degraded"
    assert result["reason"] == "starting_without_progress"
    assert result["restart_attempted"] is False


def test_scheduled_job_once_execute_real_codex_end_to_end(tmp_path: Path, monkeypatch) -> None:
    if str(os.environ.get("EIEL_RUN_REAL_CODEX_SCHEDULER_E2E") or "").strip() != "1":
        pytest.skip("set EIEL_RUN_REAL_CODEX_SCHEDULER_E2E=1 to enable the real Codex scheduler smoke test")

    real_cmd = str(os.environ.get("EIEL_TEST_REAL_CODEX_TURN_CMD") or "").strip()
    codex_bin = shutil.which("codex")
    if not real_cmd and not codex_bin:
        pytest.skip("real Codex CLI not available; set EIEL_TEST_REAL_CODEX_TURN_CMD or install `codex`")

    _copy_prompt_fixture(repo_root=tmp_path, name="_base.md")
    _copy_prompt_fixture(repo_root=tmp_path, name="role__generalist_mobile.md")
    _live_log("setup", f"repo_root={tmp_path}")

    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.setenv(
        "EIEL_CODEX_TURN_CMD",
        real_cmd
        or f"{codex_bin} -a never -s workspace-write exec -C {tmp_path} --skip-git-repo-check -",
    )

    _agents._CODEX_MANAGER = None
    binding = {"type": "task_id", "id": "a7k"}
    job_id = "scheduler_codex_e2e_001"
    probe_token = "scheduler-e2e-probe-7429"
    probe_rel = "scheduler_probe.txt"
    probe_path = (tmp_path / probe_rel).resolve()
    try:
        probe_path.write_text(probe_token + "\n", encoding="utf-8")
        _live_log("configure-runtime", f"turn_cmd={os.environ.get('EIEL_CODEX_TURN_CMD', '')}")
        _live_log("probe-file", f"{probe_rel} -> {probe_token}")
        sched.save_job(
            {
                "job_id": job_id,
                "enabled": True,
                "role": "generalist",
                "binding": binding,
                "prompt": (
                    "Read the repo-root file `scheduler_probe.txt` and reply with exactly one line "
                    "in the form `SCHEDULER_E2E_OK <file-content>` using the file content you read."
                ),
                "context_refs": [probe_rel],
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "smoke_signal", "format": "text"},
                "policy": {"max_runtime_seconds": 180},
            }
        )
        _live_log("job-saved", job_id)

        events_path = tmp_path / "governance" / "agents" / "runtime" / "workers_events.jsonl"
        ledger_path = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "ledger" / "executions.jsonl"
        result_box: dict[str, object] = {}
        error_box: dict[str, BaseException] = {}

        def _run_tick() -> None:
            try:
                result_box["result"] = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
            except BaseException as exc:  # pragma: no cover - test should surface the assertion below
                error_box["error"] = exc

        _live_log("tick-start", "scheduler_tick(now_utc=2026-03-08T04:00:00Z)")
        worker_thread = threading.Thread(target=_run_tick, name="scheduler_codex_e2e_tick")
        worker_thread.start()

        seen_event_lines = 0
        seen_ledger_lines = 0
        while worker_thread.is_alive():
            if events_path.exists():
                event_lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in event_lines[seen_event_lines:]:
                    _live_log("worker-event", line)
                seen_event_lines = len(event_lines)
            if ledger_path.exists():
                ledger_lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in ledger_lines[seen_ledger_lines:]:
                    _live_log("scheduler-ledger", line)
                seen_ledger_lines = len(ledger_lines)
            time.sleep(0.2)

        worker_thread.join(timeout=1.0)
        if events_path.exists():
            event_lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in event_lines[seen_event_lines:]:
                _live_log("worker-event", line)
        if ledger_path.exists():
            ledger_lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in ledger_lines[seen_ledger_lines:]:
                _live_log("scheduler-ledger", line)

        if "error" in error_box:
            raise error_box["error"]
        result = dict(result_box.get("result") or {})
        _live_log("tick-finished", json.dumps(result, ensure_ascii=False, sort_keys=True))
        assert result["executed_count"] == 1
        run = result["executed"][0]
        assert run["ok"] is True

        receipt = sched.load_receipt(job_id, run["run_id"], repo_root=tmp_path)
        _live_log("receipt", json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        assert receipt["status"] == "ok"
        receipt_content = str(receipt["assistant"]["content"])
        assert "SCHEDULER_E2E_OK" in receipt_content
        assert probe_token in receipt_content

        report_path = Path(run["report_path"])
        assert report_path.exists()
        _live_log("report-path", str(report_path))
        report_text = report_path.read_text(encoding="utf-8")
        assert "SCHEDULER_E2E_OK" in report_text
        assert probe_token in report_text

        state = sched.load_state(job_id, repo_root=tmp_path)
        _live_log("state", json.dumps(state, ensure_ascii=False, sort_keys=True))
        assert state["last_status"] == "ok"
        assert state["active_run_id"] == ""

        mgr = _agents._codex_manager()
        worker = mgr.get_worker(binding=binding, role="generalist")
        _live_log("worker-status", json.dumps(worker, ensure_ascii=False, sort_keys=True))
        assert worker["exists"] is True
        assert worker["status"] == "ready"

        stop = mgr.stop_worker(binding=binding, role="generalist", reason="scheduler_e2e_test_cleanup")
        _live_log("worker-stop", json.dumps(stop, ensure_ascii=False, sort_keys=True))
        assert stop["ok"] is True
        assert stop["stopped"] is True

        raw_events = events_path.read_text(encoding="utf-8")
        assert "started" in raw_events
        assert "turn_ok" in raw_events
        assert "stopped" in raw_events
    finally:
        _live_log("teardown", "stopping any remaining Codex workers")
        mgr = _agents._CODEX_MANAGER
        if mgr is not None:
            try:
                mgr.stop_all(reason="scheduler_e2e_test_teardown")
            except Exception:
                pass
        _agents._CODEX_MANAGER = None


# ============================================================================
# Operator-Disruption Recovery Tests (C735 PIS mandatory scenarios)
# ============================================================================


def test_startup_claim_audit_clears_stale_claims(tmp_path: Path, monkeypatch) -> None:
    """Test that startup audit clears claims with dead PIDs."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    # Create a job and manually place a stale claim file
    sched.save_job({
        "job_id": "test_stale_claim",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "aud"},
        "prompt": "Audit stale claims.",
        "schedule": {"mode": "daily", "time_of_day": "09:00", "timezone": "UTC"},
    })

    # Manually create a stale claim with a dead PID
    claims_dir = sched.locks_dir(tmp_path)
    claim_file = claims_dir / "test_stale_claim.lock.json"
    claim_file.write_text(
        json.dumps({
            "schema_version": "scheduled_job_claim_v1",
            "job_id": "test_stale_claim",
            "run_id": "sj_test",
            "pid": 99999,  # Non-existent PID
            "claimed_utc": sched._utc_iso(),
        }),
        encoding="utf-8"
    )
    sched._record_scheduled_job_runtime_claim_acquired(
        job_id="test_stale_claim",
        run_id="sj_test",
        trigger="startup_test",
        claimed_at_utc="2026-04-07T05:00:00Z",
        claim_row={"job_kind": "single_turn"},
        repo_root=tmp_path,
    )

    # Run startup audit
    result = sched.startup_claim_audit(repo_root=tmp_path)
    assert result["ok"] is True
    assert result["cleared_count"] == 1
    assert "test_stale_claim" in result["cleared_jobs"]
    assert not claim_file.exists(), "Stale claim file should be removed"
    claim_uid = sched._scheduled_job_runtime_claim_uid("test_stale_claim", "sj_test")
    lineage = resource_claim_ledger.read_claim_lineage(claim_uid, repo_root=tmp_path)
    recoveries = resource_claim_ledger.read_recovery_records_for_claim(claim_uid, repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert [row["action"] for row in recoveries] == ["released"]
    assert recoveries[0]["reason_code"] == "stale_pid_dead"


def test_job_cancellation_produces_terminal_receipt_and_releases_claim(tmp_path: Path, monkeypatch) -> None:
    """Test operator cancellation marks request truthfully while work is active."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="cxl")

    # Create a job
    sched.save_job({
        "job_id": "test_cancel",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "cxl"},
        "prompt": "Cancel this job.",
        "schedule": {"mode": "daily", "time_of_day": "09:00", "timezone": "UTC"},
    })

    # Manually set up an active run (simulating a running job)
    run_id = "sj_test_running_001"
    sched._write_state("test_cancel", {
        "active_run_id": run_id,
        "active_started_utc": sched._utc_iso(),
        "last_status": "running",
    }, repo_root=tmp_path)

    # Create a claim file to simulate an active job
    claim_path = sched._claim_path("test_cancel", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(json.dumps({
        "schema_version": "scheduled_job_claim_v1",
        "job_id": "test_cancel",
        "run_id": run_id,
        "pid": os.getpid(),
        "claimed_utc": sched._utc_iso(),
    }), encoding="utf-8")

    # Verify claim exists
    assert claim_path.exists(), "Claim should exist for active job"

    # Cancel the job
    cancel_res = sched.cancel_job("test_cancel", reason="operator_test", repo_root=tmp_path)
    assert cancel_res["ok"] is True
    assert cancel_res["status"] == "cancel_requested"
    assert cancel_res["stop_proof_pending"] is True
    assert cancel_res["receipt"]["status"] == "cancel_requested"
    assert cancel_res["receipt"].get("binding_result", {}).get("ok") is True

    # Verify claim remains until real stop proof exists
    assert claim_path.exists(), "Claim should remain until cancellation is proven"

    # Verify state was updated without claiming terminal completion
    state = sched.load_state("test_cancel", repo_root=tmp_path)
    assert state["last_status"] == "running"
    assert state["active_run_id"] == run_id
    assert state["cancel_request_run_id"] == run_id
    register_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task_row = register_snapshot["active_tasks"][0]
    assert task_row["execution_surface_ownership"]["selected_surface"] == "scheduler"
    assert task_row["execution_surface_ownership"]["reference_status"] == "execution_ref_stale"


def test_job_postpone_updates_run_at_and_clears_claim(tmp_path: Path, monkeypatch) -> None:
    """Test operator job postpone for once-mode jobs."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="psp")

    # Create a once-mode job
    saved = sched.save_job({
        "job_id": "test_postpone",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "psp"},
        "prompt": "Postpone this job.",
        "schedule": {"mode": "once", "run_at": "2026-03-20T10:00:00Z", "timezone": "UTC"},
    })
    assert saved["schedule"]["run_at"] == "2026-03-20T10:00:00Z"

    # Postpone by 5 days
    postpone_res = sched.postpone_job("test_postpone", days=5, repo_root=tmp_path)
    assert postpone_res["ok"] is True
    assert postpone_res["status"] == "postponed"
    assert "2026-03-25" in postpone_res["new_run_at"]
    assert postpone_res["receipt"].get("binding_result", {}).get("ok") is True

    # Verify spec was updated
    updated_spec = sched.load_job("test_postpone", repo_root=tmp_path)
    assert updated_spec["schedule"]["run_at"] == postpone_res["new_run_at"]
    register_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task_row = register_snapshot["active_tasks"][0]
    assert task_row["execution_surface_ownership"]["selected_surface"] == "scheduler"
    assert task_row["execution_surface_ownership"]["reference_status"] == "execution_ref_stale"


def test_run_now_request_records_scheduler_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="rnr")

    sched.save_job({
        "job_id": "test_run_now",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "rnr"},
        "prompt": "Run now request.",
        "schedule": {"mode": "daily", "time_of_day": "09:00", "timezone": "UTC"},
    })

    request = sched.request_run_now("test_run_now", repo_root=tmp_path, request_source="manual")
    assert request["ok"] is True
    assert request["status"] == "queued"
    assert request["run_id"].startswith("run_now_")
    assert request["binding_result"].get("ok") is True
    register_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task_row = register_snapshot["active_tasks"][0]
    assert task_row["execution_surface_ownership"]["selected_surface"] == "scheduler"
    assert task_row["execution_surface_ownership"]["reference_status"] == "execution_ref_unresolved"


def test_job_postpone_active_run_records_request_without_mutating_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="psa")

    sched.save_job({
        "job_id": "test_postpone_active",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "psa"},
        "prompt": "Postpone this running job.",
        "schedule": {"mode": "once", "run_at": "2026-03-20T10:00:00Z", "timezone": "UTC"},
    })

    run_id = "sj_test_postpone_active_001"
    sched._write_state("test_postpone_active", {
        "active_run_id": run_id,
        "active_started_utc": sched._utc_iso(),
        "last_status": "running",
    }, repo_root=tmp_path)
    claim_path = sched._claim_path("test_postpone_active", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(json.dumps({
        "schema_version": "scheduled_job_claim_v1",
        "job_id": "test_postpone_active",
        "run_id": run_id,
        "pid": os.getpid(),
        "claimed_utc": sched._utc_iso(),
    }), encoding="utf-8")

    postpone_res = sched.postpone_job("test_postpone_active", days=3, repo_root=tmp_path)
    assert postpone_res["status"] == "postpone_requested"
    assert postpone_res["schedule_updated"] is False
    assert postpone_res["stop_proof_pending"] is True
    assert claim_path.exists()

    updated_spec = sched.load_job("test_postpone_active", repo_root=tmp_path)
    assert updated_spec["schedule"]["run_at"] == "2026-03-20T10:00:00Z"
    updated_state = sched.load_state("test_postpone_active", repo_root=tmp_path)
    assert updated_state["postpone_request_run_id"] == run_id


def test_parallel_dispatch_submits_multiple_jobs_to_thread_pool(tmp_path: Path, monkeypatch) -> None:
    """Test that scheduler_tick submits jobs to thread pool (not blocking)."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    # Create 3 eligible daily jobs with a schedule that's due now
    for i in range(3):
        sched.save_job({
            "job_id": f"daily_job_{i}",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "plx"},
            "prompt": "Parallel job submission test.",
            "schedule": {"mode": "daily", "time_of_day": "12:00", "timezone": "UTC"},
            "created_utc": "2026-03-15T00:00:00Z",
        })

    # Run a tick at a time when jobs are eligible (12:00 UTC)
    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-15T12:15:00Z", limit=10)
    assert result["ok"] is True
    # At least one job should be submitted (not all may execute due to concurrency cap)
    assert result["submitted_count"] >= 1 or result["deferred_count"] >= 1


def test_watchdog_detects_stale_claims_and_transitions_to_blocked(tmp_path: Path, monkeypatch) -> None:
    """Test that watchdog transitions stale running jobs to blocked."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    _seed_task_register(tmp_path, task_id="abc")

    # Create and execute a job
    sched.save_job({
        "job_id": "test_stale_running",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "abc"},
        "prompt": "Test watchdog stale claim detection.",
        "schedule": {"mode": "daily", "time_of_day": "09:00", "timezone": "UTC"},
    })

    res = sched.execute_job("test_stale_running", repo_root=tmp_path)
    assert res["ok"] is True

    # Manually corrupt the claim to make it look stale
    state = sched.load_state("test_stale_running", repo_root=tmp_path)
    assert state["last_status"] == "ok"  # Job completed successfully

    # Create a new job that's marked as running but with a stale claim
    sched._write_state("test_stale_running", {
        "active_run_id": "fake_run_001",
        "active_started_utc": "2020-01-01T00:00:00Z",  # Very old
        "last_status": "running",
    }, repo_root=tmp_path)
    claim_path = sched.locks_dir(tmp_path) / "test_stale_running.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "job_id": "test_stale_running",
                "run_id": "fake_run_001",
                "pid": 0,
                "claimed_utc": "2020-01-01T00:00:00Z",
                "heartbeat_utc": "2020-01-01T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    sched._record_scheduled_job_runtime_claim_acquired(
        job_id="test_stale_running",
        run_id="fake_run_001",
        trigger="watchdog_test",
        claimed_at_utc="2020-01-01T00:00:00Z",
        claim_row={"job_kind": "single_turn"},
        repo_root=tmp_path,
    )

    # Run the watchdog transition function
    transitions = sched._transition_stale_running_to_blocked(repo_root=tmp_path)
    assert transitions["ok"] is True
    assert transitions["transitioned_count"] >= 0  # May or may not find stale claims
    claim_uid = sched._scheduled_job_runtime_claim_uid("test_stale_running", "fake_run_001")
    lineage = resource_claim_ledger.read_claim_lineage(claim_uid, repo_root=tmp_path)
    recoveries = resource_claim_ledger.read_recovery_records_for_claim(claim_uid, repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert [row["action"] for row in recoveries] == ["released"]
    register_state = register.load_or_init_task_register(tmp_path)
    task = next(row for row in register_state.get("active_tasks", []) if row.get("task_id") == "abc")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "watchdog"
    assert ownership.get("reference_status") == "execution_ref_conflict"


def test_pipeline_paused_detection_identifies_all_disabled_state(tmp_path: Path, monkeypatch) -> None:
    """Test that watchdog detects pipeline paused (all disabled) state."""
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    # Create 2 jobs
    for i in range(2):
        sched.save_job({
            "job_id": f"paused_job_{i}",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "psd"},
            "prompt": "Pipeline paused detection test.",
            "schedule": {"mode": "daily", "time_of_day": "09:00", "timezone": "UTC"},
        })

    # Disable all jobs
    sched.set_job_enabled("paused_job_0", False, repo_root=tmp_path)
    sched.set_job_enabled("paused_job_1", False, repo_root=tmp_path)

    # Detect pipeline paused state
    paused_state = sched._detect_pipeline_paused(repo_root=tmp_path, alert_minutes=60)
    assert paused_state["is_paused"] is True
    assert paused_state["all_jobs_disabled"] is True


def test_scheduler_tick_role_cap_is_enforced_per_role_not_globally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    builder_future: concurrent.futures.Future[Any] = concurrent.futures.Future()
    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOBS["active_builder_001"] = builder_future
        sched._DISPATCH_ACTIVE_JOB_ROLES["active_builder_001"] = "builder"

    old_dispatch_config = dict(sched._DISPATCH_CONFIG)
    sched._DISPATCH_CONFIG = {
        "global_concurrency_cap": 2,
        "per_role_cap": {"builder": 1, "generalist": 1, "assurer": 1, "verifier": 1, "orchestrator": 1},
    }
    try:
        sched.save_job(
            {
                "job_id": "generalist_due_001",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "prompt": "Write a report.",
                "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
                "policy": {"max_runtime_seconds": 600},
            },
            repo_root=tmp_path,
        )

        original_pool = sched._get_dispatch_pool

        class _ImmediateFuture:
            def __init__(self, result):
                self._result = result
            def done(self):
                return True
            def result(self, timeout=None):
                return self._result

        class _ImmediatePool:
            def submit(self, fn, *args, **kwargs):
                return _ImmediateFuture(fn(*args, **kwargs))

        monkeypatch.setattr(sched, "_get_dispatch_pool", lambda **kwargs: _ImmediatePool())
        monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

        result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    finally:
        sched._DISPATCH_CONFIG = old_dispatch_config
        builder_future.set_result({"ok": True})
        sched._get_active_job_count()

    assert result["submitted_count"] == 1
    assert result["executed_count"] == 1
    assert result["deferred"] == []


def test_scheduler_runtime_resource_policy_prefers_env_over_runtime_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: tmp_path)
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "scheduler.global_concurrency_cap=2\n"
        "scheduler.role_concurrency_caps=builder:3,generalist:2\n"
        "scheduler.resource_class_caps=cpu_light:1\n",
        encoding="utf-8",
    )
    old_dispatch_config = {
        "global_concurrency_cap": int(sched._DISPATCH_CONFIG.get("global_concurrency_cap", 1)),
        "per_role_cap": dict(sched._DISPATCH_CONFIG.get("per_role_cap") or {}),
        "resource_class_caps": dict(sched._DISPATCH_CONFIG.get("resource_class_caps") or {}),
        "policy_source": str(sched._DISPATCH_CONFIG.get("policy_source") or ""),
        "policy_valid": bool(sched._DISPATCH_CONFIG.get("policy_valid", True)),
        "validation_errors": list(sched._DISPATCH_CONFIG.get("validation_errors") or []),
    }
    monkeypatch.setenv("EIEL_SCHEDULER_GLOBAL_CONCURRENCY_CAP", "7")
    monkeypatch.setenv("EIEL_SCHEDULER_ROLE_CONCURRENCY_CAPS", "builder:5,generalist:4")
    try:
        policy = sched.current_runtime_resource_policy()
    finally:
        sched._DISPATCH_CONFIG.update(old_dispatch_config)

    assert policy["ok"] is True
    assert policy["global_concurrency_cap"] == 7
    assert policy["per_role_cap"]["builder"] == 5
    assert policy["per_role_cap"]["generalist"] == 4
    assert policy["resource_class_caps"]["cpu_light"] == 1
    assert policy["sources"]["global_concurrency_cap"] == "environment:EIEL_SCHEDULER_GLOBAL_CONCURRENCY_CAP"
    assert policy["sources"]["resource_class_caps"] == "runtime_policy"


def test_scheduler_runtime_resource_policy_invalid_update_falls_back_to_last_known_good(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: tmp_path)
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    old_dispatch_config = {
        "global_concurrency_cap": int(sched._DISPATCH_CONFIG.get("global_concurrency_cap", 1)),
        "per_role_cap": dict(sched._DISPATCH_CONFIG.get("per_role_cap") or {}),
        "resource_class_caps": dict(sched._DISPATCH_CONFIG.get("resource_class_caps") or {}),
        "policy_source": str(sched._DISPATCH_CONFIG.get("policy_source") or ""),
        "policy_valid": bool(sched._DISPATCH_CONFIG.get("policy_valid", True)),
        "validation_errors": list(sched._DISPATCH_CONFIG.get("validation_errors") or []),
    }
    try:
        policy_path.write_text(
            "scheduler.global_concurrency_cap=3\n"
            "scheduler.role_concurrency_caps=builder:2\n"
            "scheduler.resource_class_caps=cpu_light:1\n",
            encoding="utf-8",
        )
        first = sched.current_runtime_resource_policy()

        policy_path.write_text(
            "scheduler.global_concurrency_cap=broken\n"
            "scheduler.role_concurrency_caps=builder:nope\n"
            "scheduler.resource_class_caps=cpu_light:-1\n",
            encoding="utf-8",
        )
        second = sched.current_runtime_resource_policy()
    finally:
        sched._DISPATCH_CONFIG.update(old_dispatch_config)

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["fallback_to_last_known_good"] is True
    assert second["global_concurrency_cap"] == 3
    assert second["per_role_cap"]["builder"] == 2
    assert second["resource_class_caps"]["cpu_light"] == 1
    assert any("scheduler.global_concurrency_cap:invalid_int:broken" == row for row in second["validation_errors"])
    assert any("scheduler.role_concurrency_caps:invalid_int:builder:nope" == row for row in second["validation_errors"])
    assert any("scheduler.resource_class_caps:non_positive:cpu_light:-1" == row for row in second["validation_errors"])


def test_execute_job_persists_runtime_policy_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "scheduler.global_concurrency_cap=2\n"
        "scheduler.role_concurrency_caps=generalist:1\n"
        "scheduler.resource_class_caps=cpu_light:1\n",
        encoding="utf-8",
    )

    sched.save_job(
        {
            "job_id": "runtime_policy_snapshot_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "snap"},
            "prompt": "Capture runtime policy snapshot.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "runtime_policy_snapshot_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    spec_snapshot = json.loads(Path(receipt["spec_archive"]).read_text(encoding="utf-8"))
    assert receipt["runtime_policy_snapshot"]["global_concurrency_cap"] == 2
    assert receipt["runtime_policy_snapshot"]["per_role_cap"]["generalist"] == 1
    assert receipt["runtime_policy_snapshot"]["resource_class_caps"]["cpu_light"] == 1
    assert spec_snapshot["runtime_policy_snapshot"] == receipt["runtime_policy_snapshot"]


def test_execute_job_records_global_concurrency_blockers_in_deferred_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched,
        "current_runtime_resource_policy",
        lambda repo_root=None: {
            "global_concurrency_cap": 1,
            "per_role_cap": {},
            "resource_class_caps": {},
        },
    )
    monkeypatch.setattr(
        sched,
        "_active_job_claims",
        lambda repo_root=None, exclude_job_id=None: [
            {
                "job_id": "active_job_123",
                "run_id": "sj_active_123",
                "started_utc": "2026-04-13T18:00:00Z",
                "heartbeat_utc": "2026-04-13T18:00:30Z",
                "role": "builder",
            }
        ],
    )

    sched.save_job(
        {
            "job_id": "blocked_by_cap_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "cap"},
            "prompt": "Capture concurrency blockers.",
            "schedule": {"mode": "once", "run_at": "2026-04-13T18:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "blocked_by_cap_001",
        repo_root=tmp_path,
        trigger="manual",
        now_utc="2026-04-13T18:01:00Z",
    )

    assert result["ok"] is False
    assert result["deferred"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["reason"] == "global_concurrency_cap_reached"
    assert receipt["execution_guard"] == {
        "kind": "global_concurrency_cap",
        "global_concurrency_cap": 1,
        "active_job_count": 1,
        "active_jobs": [
            {
                "job_id": "active_job_123",
                "run_id": "sj_active_123",
                "started_utc": "2026-04-13T18:00:00Z",
                "heartbeat_utc": "2026-04-13T18:00:30Z",
                "role": "builder",
            }
        ],
    }


def test_scheduler_tick_recovers_stale_claim_before_skip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "stale_claim_tick_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )

    claim_path = sched.locks_dir(tmp_path) / "stale_claim_tick_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "stale_claim_tick_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")

    assert result["submitted_count"] == 1
    assert result["executed_count"] == 1
    assert all(row["reason"] != "claimed_elsewhere" for row in result["skipped"])
    assert claim_path.exists() is False


def test_scheduler_tick_reconciles_terminal_leftover_claim_before_skip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "terminal_leftover_claim_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
        },
        repo_root=tmp_path,
    )

    state = sched.load_state("terminal_leftover_claim_001", repo_root=tmp_path)
    state.update(
        {
            "job_id": "terminal_leftover_claim_001",
            "active_run_id": "",
            "last_run_id": "sj_newer_terminal",
            "last_started_utc": "2026-03-08T03:35:00Z",
            "last_finished_utc": "2026-03-08T03:42:00Z",
            "last_status": "failed_verification",
        }
    )
    sched._write_state("terminal_leftover_claim_001", state, repo_root=tmp_path)

    claim_path = sched.locks_dir(tmp_path) / "terminal_leftover_claim_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "terminal_leftover_claim_001",
                "run_id": "sj_older_leftover",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:34:00Z",
                "pid": os.getpid(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")

    assert result["submitted_count"] == 1
    assert result["executed_count"] == 1
    assert all(row["reason"] != "claimed_elsewhere" for row in result["skipped"])
    assert claim_path.exists() is False
    event_rows = [row for row in sched.list_execution_events(repo_root=tmp_path) if row["job_id"] == "terminal_leftover_claim_001"]
    assert any(row["event"] == "terminal_claim_reconciled_prelaunch" for row in event_rows)


def test_active_job_claims_ignores_and_cleans_terminal_leftover_claim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "terminal_claim_scan_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
        },
        repo_root=tmp_path,
    )

    state = sched.load_state("terminal_claim_scan_001", repo_root=tmp_path)
    state.update(
        {
            "job_id": "terminal_claim_scan_001",
            "active_run_id": "",
            "last_run_id": "sj_terminal",
            "last_started_utc": "2026-03-08T03:35:00Z",
            "last_finished_utc": "2026-03-08T03:42:00Z",
            "last_status": "failed_verification",
        }
    )
    sched._write_state("terminal_claim_scan_001", state, repo_root=tmp_path)

    claim_path = sched.locks_dir(tmp_path) / "terminal_claim_scan_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "terminal_claim_scan_001",
                "run_id": "sj_leftover",
                "trigger": "scheduler",
                "claimed_utc": "2026-03-08T03:34:00Z",
                "pid": os.getpid(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    active_claims = sched._active_job_claims(repo_root=tmp_path)

    assert active_claims == []
    assert claim_path.exists() is False


def test_pis_creation_runtime_override_preserves_build_verify_loop_runtime() -> None:
    runtime = {
        "agent_tech": "deepseek",
        "model": "deepseek-reasoner",
        "fallback_models": ["gpt-5.4"],
        "fallback_agent_techs": ["openai"],
        "tech_model_sequence": [{"agent_tech": "deepseek", "model": "deepseek-reasoner"}],
    }
    spec = {
        "job_id": "parent_decompose_001",
        "job_kind": "build_verify_loop",
        "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
        "prompt": "Decompose parent PIS into child PIS items.",
    }

    out = sched._apply_pis_creation_runtime_overrides(spec, runtime)

    assert out == runtime


def test_pis_creation_runtime_override_backfills_missing_runtime_from_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        sched._runtime_policy,
        "get_runtime_policy_text",
        lambda key, default, env_names=(): {"scheduler.decompose.runtime_model": "gpt-5.4-mini"}.get(key, default),
    )
    monkeypatch.setattr(
        sched,
        "_runtime_policy_models",
        lambda key, default_csv="": ["gpt-5.2-codex", "deepseek-reasoner"]
        if key == "scheduler.decompose.runtime_fallback_models"
        else [],
    )
    spec = {
        "job_id": "parent_decompose_001",
        "job_kind": "single_turn",
        "title": "Create child PIS items from parent PIS.",
        "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
        "prompt": "Create child PIS items from parent PIS.",
    }

    out = sched._apply_pis_creation_runtime_overrides(spec, {})

    assert out["model"] == "gpt-5.4-mini"
    assert out["agent_tech"] == "openai"
    assert out["fallback_models"] == ["gpt-5.2-codex", "deepseek-reasoner"]
    assert out["fallback_on_quota"] is True


def test_failed_scheduled_job_receipt_includes_forensic_debug_kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})

    def _fake_execute_single_turn_job(spec, *, run_id: str, trigger: str, **_kwargs):
        return (
            False,
            {
                "schema_version": sched.RECEIPT_SCHEMA_VERSION,
                "job_kind": "single_turn",
                "job_id": str(spec.get("job_id") or ""),
                "run_id": run_id,
                "trigger": trigger,
                "status": "error",
                "binding": dict(spec.get("binding") or {}),
                "schedule": dict(spec.get("schedule") or {}),
                "expected_output": dict(spec.get("expected_output") or {}),
                "context_refs": [],
                "required_context_bundle": "",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "effective_context_refs": [],
                "runtime": {},
                "execution_contract": {"execution_owner": "codex", "pipeline_executor_mode": ""},
                "execution_proof": {
                    "proof_gap": True,
                    "proof_gap_reason": "forced_gap",
                    "validation_commands": [],
                },
                "error": "forced failure",
                "reason_code": "forced_failure",
            },
            "forced failure",
        )

    monkeypatch.setattr(sched, "_execute_single_turn_job", _fake_execute_single_turn_job)

    sched.save_job(
        {
            "job_id": "forensic_failure_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Force a failure so the forensic kit is emitted.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    result = sched.execute_job(
        "forensic_failure_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("forensic_failure_001", result["run_id"], repo_root=tmp_path)
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    state = sched.load_state("forensic_failure_001", repo_root=tmp_path)

    assert receipt["forensic_debug_kit"]["classification"] == "failure"
    assert receipt["forensic_debug_kit"]["reason_code"] == "forced_failure"
    assert receipt["forensic_debug_kit"]["proof_gap"] is True
    assert receipt["forensic_debug_kit"]["next_decision_hint"]
    assert "## Forensic Debug Kit" in report_text
    assert state["last_forensic_classification"] == "failure"
    assert state["last_forensic_next_hint"] == receipt["forensic_debug_kit"]["next_decision_hint"]
