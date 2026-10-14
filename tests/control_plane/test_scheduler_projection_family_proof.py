from __future__ import annotations

from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import scheduler_projection_family_proof as proof


def _bound_proof_artifact() -> Path:
    base = Path("governance") / "ledger" / "patch_instruction_sheets" / "causyl"
    rel = (
        Path("2026")
        / "04"
        / "PIS-20260408-181104Z-H5J5__prove-unified-scheduler-projection-consistency-across-the-plane"
        / "artifacts"
        / "verification"
        / "scheduler_projection_family_parity_audit.md"
    )
    for ledger_status in ("verified", "active"):
        candidate = base / ledger_status / rel
        if candidate.exists():
            return candidate
    return base / "verified" / rel


def _save_once_job(
    tmp_path: Path,
    *,
    job_id: str,
    run_at: str,
    prompt: str,
    depends_on: list[str] | None = None,
) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": job_id},
            "depends_on": list(depends_on or []),
            "prompt": prompt,
            "schedule": {"mode": "once", "run_at": run_at, "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )


def test_scheduler_projection_family_proof_keeps_watch_api_and_phone_summaries_in_lockstep(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    _save_once_job(
        tmp_path,
        job_id="projection_active_001",
        run_at="2026-04-08T05:30:00Z",
        prompt="Active ownership case.",
    )
    sched._write_state(
        "projection_active_001",
        {
            "active_run_id": "sj_projection_active_001",
            "active_started_utc": "2026-04-08T05:31:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    sched.save_job(
        {
            "job_id": "projection_failed_parent_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "role": "builder",
            "binding": {"type": "ledger_uid", "id": "PIS-20260408-181104Z-H5J5"},
            "prompt": "Failed parent case.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T05:00:00Z", "timezone": "UTC"},
            "loop": {"max_iterations": 1},
            "runtime": {"executor_agent_tech": "codex"},
            "expected_output": {"kind": "report", "format": "markdown"},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "projection_failed_parent_001",
        {
            "last_status": "failed_verification",
            "last_trigger": "scheduler",
            "last_finished_utc": "2026-04-08T06:04:45Z",
            "last_slot_key": "2026-04-08T05:00:00Z",
        },
        repo_root=tmp_path,
    )

    _save_once_job(
        tmp_path,
        job_id="projection_blocked_child_001",
        run_at="2026-04-08T05:10:00Z",
        prompt="Dependency blocked case.",
        depends_on=["projection_failed_parent_001"],
    )

    _save_once_job(
        tmp_path,
        job_id="projection_run_now_001",
        run_at="2026-04-08T09:00:00Z",
        prompt="Run-now queued case.",
    )
    sched._write_state(
        "projection_run_now_001",
        {
            "last_status": "ok",
            "run_now_requested_utc": "2026-04-08T06:04:00Z",
            "last_slot_key": "2026-04-08T09:00:00Z",
        },
        repo_root=tmp_path,
    )

    _save_once_job(
        tmp_path,
        job_id="projection_completed_once_001",
        run_at="2026-04-08T04:45:00Z",
        prompt="Completed once case.",
    )
    sched._write_state(
        "projection_completed_once_001",
        {
            "last_status": "ok",
            "last_reconciliation_reason": "already_executed",
            "last_slot_key": "2026-04-08T04:45:00Z",
        },
        repo_root=tmp_path,
    )

    report = proof.collect_scheduler_projection_surface_proof(repo_root=tmp_path, now_utc="2026-04-08T06:05:00Z")

    assert report["all_cases_match"] is True
    assert report["all_degraded_flags_match"] is True
    assert report["mismatches"] == []
    assert report["degraded_mismatches"] == []
    assert report["case_count"] == 5
    assert report["summary"] == {
        "total_jobs": 5,
        "enabled_jobs": 5,
        "running_jobs": 1,
        "blocked_jobs": 1,
        "next_job_id": "projection_run_now_001",
        "next_candidate_bucket": "runnable_now",
        "next_due_utc": "2026-04-08T06:04:00Z",
        "next_reason": "run_now_requested",
    }

    cases = {row["job_id"]: row for row in report["cases"]}
    assert cases["projection_active_001"]["statuses"] == {
        "api_list": "active",
        "api_detail": "active",
        "watch": "active",
        "next_job_summary": "active",
    }
    assert cases["projection_failed_parent_001"]["statuses"] == {
        "api_list": "failed",
        "api_detail": "failed",
        "watch": "failed",
        "next_job_summary": "failed",
    }
    assert cases["projection_blocked_child_001"]["statuses"] == {
        "api_list": "blocked",
        "api_detail": "blocked",
        "watch": "blocked",
        "next_job_summary": "blocked",
    }
    assert cases["projection_run_now_001"]["statuses"] == {
        "api_list": "queued",
        "api_detail": "queued",
        "watch": "queued",
        "next_job_summary": "queued",
    }
    assert cases["projection_completed_once_001"]["statuses"] == {
        "api_list": "completed_once",
        "api_detail": "completed_once",
        "watch": "completed_once",
        "next_job_summary": "completed_once",
    }
    assert cases["projection_completed_once_001"]["reasons"]["api_list"] == "already_executed"
    assert cases["projection_blocked_child_001"]["reasons"]["watch"].startswith("dependency_")


def test_scheduler_projection_family_proof_keeps_ambiguous_completed_once_conflict_visible_on_all_surfaces(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    ambiguous_row = sched.job_summary(
        {
            "job_id": "projection_completed_once_conflict_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "projection_completed_once_conflict_001"},
            "prompt": "Completed-once conflict should fail closed.",
            "schedule": {"mode": "once", "run_at": "2026-04-08T04:45:00Z", "timezone": "UTC"},
        },
        state={
            "last_status": "ok",
            "last_reconciliation_reason": "already_executed",
        },
        due={
            "eligible": True,
            "reason": "due",
            "slot_key": "2026-04-08T04:45:00Z",
            "due_utc": "2026-04-08T04:45:00Z",
        },
        repo_root=tmp_path,
        now=sched._parse_utc("2026-04-08T06:05:00Z"),
    )

    case = proof.collect_scheduler_projection_surface_case(api_list_row=ambiguous_row, api_detail_row=ambiguous_row)

    assert case["statuses"] == {
        "api_list": "ambiguous",
        "api_detail": "ambiguous",
        "watch": "ambiguous",
        "next_job_summary": "ambiguous",
    }
    assert case["degraded"] == {
        "api_list": True,
        "api_detail": True,
        "watch": True,
        "next_job_summary": True,
    }
    assert case["reasons"] == {
        "api_list": "completed_once_conflicts_with_due_eligibility",
        "api_detail": "completed_once_conflicts_with_due_eligibility",
        "watch": "completed_once_conflicts_with_due_eligibility",
        "next_job_summary": "completed_once_conflicts_with_due_eligibility",
    }
    assert case["parity_ok"] is True
    assert case["degraded_parity_ok"] is True


def test_bound_scheduler_projection_proof_artifact_lists_family_cases_and_surfaces() -> None:
    artifact = _bound_proof_artifact()
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")

    assert "PIS-20260408-181104Z-H5J5" in body
    assert "watch, API list, API detail, and phone-facing next/queue summaries" in body
    assert "projection_active_001" in body
    assert "projection_blocked_child_001" in body
    assert "projection_run_now_001" in body
    assert "projection_completed_once_001" in body
    assert "projection_failed_parent_001" in body
    assert "projection_completed_once_conflict_001" in body
    assert "completed_once_conflicts_with_due_eligibility" in body
    assert "specialized verifier review is still required" in body
