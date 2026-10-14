from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_pipeline_healer as healer
from tools.bin.scheduler_verify_job import verify_job
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import server_maintenance as maint
from infra.webui import server_lab_assistant_task_register as register


def _save_once_job(tmp_path: Path, *, job_id: str, depends_on: list[str] | None = None, enabled: bool = True) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "enabled": enabled,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "depends_on": list(depends_on or []),
            "prompt": f"Prompt for {job_id}.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )


def _save_loop_job(tmp_path: Path, *, job_id: str, enabled: bool = True) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": enabled,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": f"Prompt for {job_id}.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "runtime": {"agent_tech": "deepseek", "model": "deepseek-chat", "fallback_on_quota": True},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 5,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"agent_tech": "deepseek", "model": "deepseek-chat", "fallback_on_quota": True},
                "verifier_runtime": {"agent_tech": "deepseek", "model": "deepseek-chat", "fallback_on_quota": True},
            },
        },
        repo_root=tmp_path,
    )


def _write_receipt(tmp_path: Path, *, job_id: str, run_id: str, status: str, error: str, builder: str = "", verifier: str = "") -> None:
    path = (sched.receipts_dir(tmp_path) / sched._safe_name(job_id) / f"{sched._safe_name(run_id)}.json").resolve()
    sched._write_json_atomic(
        path,
        {
            "job_id": job_id,
            "run_id": run_id,
            "job_kind": "build_verify_loop",
            "status": status,
            "error": error,
            "last_builder_message": builder,
            "last_verifier_message": verifier,
            "loop": {
                "iterations": [
                    {
                        "builder": {"content": builder, "status": "error" if not builder else "ok"},
                        "verifier": {"content": verifier, "status": "error" if not verifier else "ok"},
                    }
                ]
            },
        },
    )


def _write_pause_manifest(tmp_path: Path, *job_ids: str) -> None:
    path = maint.pause_manifest_path(repo_root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{\n  "schema_version": "scheduler_pause_state_v1",\n  "paused_job_ids": [' + ", ".join(f'"{jid}"' for jid in job_ids) + "]\n}\n",
        encoding="utf-8",
    )


def _seed_task_register(repo_root: Path, *, task_id: str) -> None:
    current = register.load_or_init_task_register(repo_root)
    register.upsert_task(
        repo_root,
        expected_generation=int(current.get("write_generation") or 0),
        writer="pipeline_healer.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"task {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "run pipeline healer",
        },
    )


def _write_ledger_item(tmp_path: Path, *, uid: str, slug: str, title: str, status: str) -> Path:
    item_dir = (tmp_path / "governance" / "ledger" / "items" / f"{uid}__{slug}").resolve()
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text(f"# PIS\n\n**UID:** {uid}\n", encoding="utf-8")
    (item_dir / "meta.json").write_text(
        json.dumps({"uid": uid, "title": title, "status": status}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (item_dir / "events.jsonl").write_text("", encoding="utf-8")
    return item_dir


def test_once_failure_slot_is_reusable_and_healer_clears_legacy_slot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_once_job(tmp_path, job_id="headjob")
    _save_once_job(tmp_path, job_id="depjob", depends_on=["headjob"])

    sched._write_state(
        "headjob",
        {
            "last_status": "error",
            "last_slot_key": "2026-03-15T00:00:00Z",
            "last_finished_utc": "2026-03-15T00:01:00Z",
        },
        repo_root=tmp_path,
    )

    spec = sched.load_job("headjob", repo_root=tmp_path)
    state = sched.load_state("headjob", repo_root=tmp_path)
    due = sched.due_status(spec, state=state, repo_root=tmp_path, now=sched._parse_utc("2026-03-15T01:00:00Z"))
    assert due["eligible"] is True
    assert due["reason"] == "due_failed_slot_reusable"

    report = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
        allow_reconcile_jobs=True,
    )

    action_types = {(row["finding_type"], row["decision"]) for row in report["actions"]}
    assert ("once_slot_consumed_on_failure", "applied") in action_types
    updated = sched.load_state("headjob", repo_root=tmp_path)
    assert str(updated.get("last_slot_key") or "") == ""


def test_pipeline_healer_records_execution_surface_ownership(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _seed_task_register(tmp_path, task_id="mb3")
    _save_once_job(tmp_path, job_id="headjob")
    _save_once_job(tmp_path, job_id="depjob", depends_on=["headjob"])

    sched._write_state(
        "headjob",
        {
            "last_status": "error",
            "last_slot_key": "2026-03-15T00:00:00Z",
            "last_finished_utc": "2026-03-15T00:01:00Z",
        },
        repo_root=tmp_path,
    )

    report = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    assert any(row.get("ok") for row in report.get("surface_bindings", []))

    register_state = register.load_or_init_task_register(tmp_path)
    task = next(row for row in register_state.get("active_tasks", []) if row.get("task_id") == "mb3")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "healer"
    assert ownership.get("reference_status") == "execution_ref_active"


def test_disabled_head_with_blocked_dependents_is_detected_and_can_be_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_once_job(tmp_path, job_id="headjob", enabled=False)
    _save_once_job(tmp_path, job_id="depjob", depends_on=["headjob"])
    sched._write_state("headjob", {"last_status": "blocked"}, repo_root=tmp_path)

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("disabled_chain_head_with_blocked_dependents", "headjob") in finding_types
    assert ("dependency_chain_deadlock", "headjob") in finding_types

    dry_run = healer.run_healer(
        repo_root=tmp_path,
        apply=False,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    dry_details = {
        row["finding_type"]: row["details"] for row in dry_run["actions"] if row["job_id"] == "headjob"
    }
    assert dry_details["disabled_chain_head_with_blocked_dependents"]["reason"] == "operator_required_enable_gate"

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=True,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("disabled_chain_head_with_blocked_dependents", "applied") in action_types
    assert sched.load_job("headjob", repo_root=tmp_path)["enabled"] is True


def test_paused_head_with_blocked_dependents_is_auto_enabled_without_operator_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_once_job(tmp_path, job_id="headjob", enabled=False)
    _save_once_job(tmp_path, job_id="depjob", depends_on=["headjob"])
    _write_pause_manifest(tmp_path, "headjob")
    sched._write_state("headjob", {"last_status": "blocked"}, repo_root=tmp_path)

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("paused_chain_head_with_blocked_dependents", "headjob") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        allow_reconcile_jobs=True,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("paused_chain_head_with_blocked_dependents", "applied") in action_types
    assert sched.load_job("headjob", repo_root=tmp_path)["enabled"] is True


def test_stranded_active_run_is_detected_and_recovered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_once_job(tmp_path, job_id="stuckjob")
    sched._write_state(
        "stuckjob",
        {
            "job_id": "stuckjob",
            "active_run_id": "sj_stranded_001",
            "active_started_utc": "2026-03-15T00:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("stranded_active_run", "stuckjob") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
        allow_reconcile_jobs=True,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("stranded_active_run", "applied") in action_types

    state = sched.load_state("stuckjob", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "interrupted"
    assert state["last_recovered_run_id"] == "sj_stranded_001"
    assert state["last_recovered_reason"] == "recovery_assistant_stranded_active_run"

    events = sched.list_execution_events(repo_root=tmp_path)
    assert any(
        row["event"] == "active_run_recovered"
        and row["job_id"] == "stuckjob"
        and row["run_id"] == "sj_stranded_001"
        for row in events
    )


def test_verify_job_reports_stranded_active_run_clearly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_once_job(tmp_path, job_id="stuckjob")
    sched._write_state(
        "stuckjob",
        {
            "job_id": "stuckjob",
            "active_run_id": "sj_stranded_001",
            "active_started_utc": "2026-03-15T00:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    result = verify_job("stuckjob", repo_root=tmp_path)
    assert result["verdict"] == "FAIL"
    assert any("stranded active run" in item for item in result["failures"])


def test_repeated_empty_proof_gap_runtime_failure_is_detected_and_runtime_repaired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_loop_job(tmp_path, job_id="loopjob")
    sched._write_state(
        "loopjob",
        {
            "last_status": "proof_gap",
            "last_finished_utc": "2026-03-15T00:03:00Z",
        },
        repo_root=tmp_path,
    )
    error = "required validation evidence missing: builder output did not include a `Validation Commands Run` section"
    _write_receipt(tmp_path, job_id="loopjob", run_id="sj_20260315T000300Z_a", status="proof_gap", error=error)
    _write_receipt(tmp_path, job_id="loopjob", run_id="sj_20260315T000200Z_b", status="proof_gap", error=error)
    _write_receipt(tmp_path, job_id="loopjob", run_id="sj_20260315T000100Z_c", status="proof_gap", error=error)

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("repeated_empty_proof_gap_runtime_failure", "loopjob") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=True,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("repeated_empty_proof_gap_runtime_failure", "applied") in action_types
    saved = sched.load_job("loopjob", repo_root=tmp_path)
    assert saved["runtime"]["agent_tech"] == "gemini"
    assert saved["loop"]["builder_runtime"]["agent_tech"] == "gemini"
    assert saved["loop"]["verifier_runtime"]["agent_tech"] == "gemini"


def test_repeated_identical_verifier_failure_is_detected_and_semantically_retried(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_loop_job(tmp_path, job_id="loopjob")
    saved = sched.load_job("loopjob", repo_root=tmp_path)
    saved["loop"]["max_iterations"] = 5
    sched.save_job(saved, repo_root=tmp_path)
    sched._write_state(
        "loopjob",
        {
            "last_status": "failed_verification",
            "last_finished_utc": "2026-03-15T00:03:00Z",
        },
        repo_root=tmp_path,
    )
    fail_text = "VERDICT: FAIL\nNeed missing regression coverage for snapshot rebuild path."
    for run_id in ["sj_20260315T000300Z_a", "sj_20260315T000200Z_b", "sj_20260315T000100Z_c"]:
        _write_receipt(tmp_path, job_id="loopjob", run_id=run_id, status="failed_verification", error=fail_text, builder="builder output", verifier=fail_text)

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("repeated_identical_verifier_failure", "loopjob") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=True,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("repeated_identical_verifier_failure", "applied") in action_types
    repaired = sched.load_job("loopjob", repo_root=tmp_path)
    assert repaired["runtime"]["agent_tech"] == "gemini"
    assert repaired["loop"]["builder_runtime"]["agent_tech"] == "gemini"
    assert repaired["loop"]["max_iterations"] == 6


def test_repeated_verified_commit_failure_is_detected_and_repaired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_loop_job(tmp_path, job_id="loopjob")
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    sched._write_state(
        "loopjob",
        {
            "last_status": "error",
            "last_run_id": "sj_20260315T000300Z_a",
            "last_finished_utc": "2026-03-15T00:03:00Z",
        },
        repo_root=tmp_path,
    )
    verifier = "VERDICT: PASS\nverified with concrete evidence"
    for run_id in ["sj_20260315T000300Z_a", "sj_20260315T000200Z_b", "sj_20260315T000100Z_c"]:
        path = (sched.receipts_dir(tmp_path) / "loopjob" / f"{run_id}.json").resolve()
        sched._write_json_atomic(
            path,
            {
                "job_id": "loopjob",
                "job_kind": "build_verify_loop",
                "run_id": run_id,
                "job_attempt": 1,
                "status": "error",
                "error": "post-success git commit/tag failed (exit_code=17)",
                "slot_key": "2026-03-15T00:00:00Z",
                "git_commit_tag": {"ok": False, "exit_code": 17, "stderr": "index.lock"},
                "loop": {
                    "iterations": [
                        {
                            "builder": {"content": "## Validation Commands Run\n- pytest -q", "runtime_send": {"selected_agent_tech": "codex", "selected_model": "gpt-5.4-mini"}},
                            "verifier": {"content": verifier, "runtime_send": {"selected_agent_tech": "codex", "selected_model": "gpt-5.4"}},
                        }
                    ]                },
            },
        )

    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: " M infra/controlplane/scheduled_jobs.py")
    monkeypatch.setattr(
        sched,
        "_run_post_success_commit_tag_with_retries",
        lambda **kwargs: {"ok": True, "exit_code": 0, "stdout": "tagged", "stderr": "", "attempts": [{"attempt": 1, "ok": True, "exit_code": 0}]},
    )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("repeated_verified_commit_failure", "loopjob") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
        allow_reconcile_jobs=True,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("repeated_verified_commit_failure", "applied") in action_types
    updated_receipt = sched.load_receipt("loopjob", "sj_20260315T000300Z_a", repo_root=tmp_path)
    assert updated_receipt["status"] == "ok"
    assert str(updated_receipt.get("error") or "") == ""
    updated_state = sched.load_state("loopjob", repo_root=tmp_path)
    assert updated_state["last_status"] == "ok"
    assert updated_state["last_slot_key"] == "2026-03-15T00:00:00Z"


def test_successful_ledger_bound_job_with_stale_status_creates_reconcile_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(healer, "_bind_healer_actions", lambda **kwargs: [])
    uid = "PIS-20260327-132825Z-81D2"
    item_dir = _write_ledger_item(
        tmp_path,
        uid=uid,
        slug="canonical-task-run-truth-model-for-platform-managed-work",
        title="Canonical Task + Run Truth Model For Platform-Managed Work",
        status="draft",
    )
    sched.save_job(
        {
            "job_id": "pis_81d2_20260327_canonical-task-run-truth-model-f",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "prompt": "Implement and verify.",
            "context_refs": [str((item_dir / "pis.md").resolve().relative_to(tmp_path)).replace('\\', '/')],
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner", "fallback_models": ["gpt-5.4"]},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"agent_tech": "deepseek", "model": "deepseek-reasoner", "fallback_models": ["gpt-5.4"]},
                "verifier_runtime": {"agent_tech": "openai", "model": "gpt-5.4", "fallback_models": ["deepseek-reasoner"]},
            },
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "pis_81d2_20260327_canonical-task-run-truth-model-f",
        {
            "last_status": "ok",
            "last_run_id": "sj_20260315T000300Z_a",
            "last_finished_utc": healer._utc_iso(),
            "last_receipt_path": str((sched.receipts_dir(tmp_path) / "pis_81d2_20260327_canonical-task-run-truth-model-f" / "sj_20260315T000300Z_a.json").resolve()),
        },
        repo_root=tmp_path,
    )
    _write_receipt(
        tmp_path,
        job_id="pis_81d2_20260327_canonical-task-run-truth-model-f",
        run_id="sj_20260315T000300Z_a",
        status="ok",
        error="",
        builder="## Validation Commands Run\n- pytest -q",
        verifier="VERDICT: PASS\nverified",
    )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("stale_ledger_status_after_success", "pis_81d2_20260327_canonical-task-run-truth-model-f") in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
        allow_reconcile_jobs=True,
    )
    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("stale_ledger_status_after_success", "applied") in action_types
    reconcile_job = sched.load_job("verify_81d2_20260327_status_reconcile", repo_root=tmp_path)
    assert reconcile_job["binding"] == {"type": "ledger_uid", "id": uid}
    assert reconcile_job["job_kind"] == "build_verify_loop"
    assert reconcile_job["runtime"]["model"] == "deepseek-reasoner"


def test_stale_ledger_status_requires_explicit_reconcile_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(healer, "_bind_healer_actions", lambda **kwargs: [])
    uid = "PIS-20260327-132825Z-81D2"
    item_dir = _write_ledger_item(
        tmp_path,
        uid=uid,
        slug="needs-explicit-reconcile-gate",
        title="Needs Explicit Reconcile Gate",
        status="draft",
    )
    sched.save_job(
        {
            "job_id": "ledger_gate_job",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "prompt": "Implement and verify.",
            "context_refs": [str((item_dir / "pis.md").resolve().relative_to(tmp_path)).replace('\\', '/')],
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "ledger_gate_job",
        {
            "last_status": "ok",
            "last_run_id": "sj_20260315T000300Z_a",
            "last_finished_utc": healer._utc_iso(),
        },
        repo_root=tmp_path,
    )
    _write_receipt(
        tmp_path,
        job_id="ledger_gate_job",
        run_id="sj_20260315T000300Z_a",
        status="ok",
        error="",
        builder="## Validation Commands Run\n- pytest -q",
        verifier="VERDICT: PASS\nverified",
    )

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        allow_reconcile_jobs=False,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )

    action = next(row for row in applied["actions"] if row["job_id"] == "ledger_gate_job")
    assert action["decision"] == "deferred"
    assert action["details"]["reason"] == "operator_required_reconcile_gate"


def test_incomplete_verified_ledger_truth_after_success_is_classified_for_reconcile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(healer, "_bind_healer_actions", lambda **kwargs: [])
    uid = "PIS-20260327-132825Z-81D2"
    item_dir = _write_ledger_item(
        tmp_path,
        uid=uid,
        slug="incomplete-verified-ledger-truth",
        title="Incomplete Verified Ledger Truth",
        status="verified",
    )
    meta_path = item_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["evidence"] = {
        "artifact_path": "artifacts/verification/status_reconcile.json",
        "implementation_reconciled": True,
    }
    meta["changesets"] = []
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    job_id = "ledger_verified_but_incomplete_job"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "prompt": "Implement and verify.",
            "context_refs": [str((item_dir / "pis.md").resolve().relative_to(tmp_path)).replace("\\", "/")],
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_id,
        {
            "last_status": "ok",
            "last_run_id": "sj_20260315T000300Z_a",
            "last_finished_utc": healer._utc_iso(),
        },
        repo_root=tmp_path,
    )
    _write_receipt(
        tmp_path,
        job_id=job_id,
        run_id="sj_20260315T000300Z_a",
        status="ok",
        error="",
        builder="## Validation Commands Run\n- pytest -q",
        verifier="VERDICT: PASS\nverified",
    )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("incomplete_verified_ledger_after_success", job_id) in finding_types

    applied = healer.run_healer(
        repo_root=tmp_path,
        apply=True,
        repeat_failure_threshold=3,
        idle_minutes=60,
        allow_enable_heads=False,
        allow_runtime_repair=False,
        allow_kick=False,
        allow_reconcile_jobs=True,
        max_repairs_per_job=2,
        max_repairs_global=10,
    )

    action_types = {(row["finding_type"], row["decision"]) for row in applied["actions"]}
    assert ("incomplete_verified_ledger_after_success", "applied") in action_types
    reconcile_job = sched.load_job("verify_81d2_20260327_status_reconcile", repo_root=tmp_path)
    assert reconcile_job["binding"] == {"type": "ledger_uid", "id": uid}


def test_repeated_contract_shape_failure_is_classified_for_semantic_retry_tuning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(healer, "_bind_healer_actions", lambda **kwargs: [])
    uid = "PIS-20260327-132825Z-81D2"
    item_dir = _write_ledger_item(
        tmp_path,
        uid=uid,
        slug="repairable-contract-failure",
        title="Repairable Contract Failure",
        status="draft",
    )
    job_id = "repairable_contract_shape_job"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "prompt": "Implement and verify.",
            "context_refs": [str((item_dir / "pis.md").resolve().relative_to(tmp_path)).replace('\\', '/')],
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_id,
        {
            "last_status": "failed_verification",
            "last_run_id": "sj_20260315T000300Z_c",
            "last_finished_utc": healer._utc_iso(),
        },
        repo_root=tmp_path,
    )
    for suffix in ("a", "b", "c"):
        _write_receipt(
            tmp_path,
            job_id=job_id,
            run_id=f"sj_20260315T000300Z_{suffix}",
            status="failed_verification",
            error="verdict_not_on_first_line",
            builder="Changed files:\n- infra/controlplane/scheduled_jobs.py\n\nValidation Commands Run:\n- pytest -q",
            verifier="Summary first\nVERDICT: PASS\nlooks good",
        )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("repeated_contract_shape_failure", job_id) in finding_types


def test_invalid_pass_pending_actions_scope_widening_is_not_downgraded_to_contract_shape_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(healer, "_bind_healer_actions", lambda **kwargs: [])
    uid = "PIS-20260327-132825Z-81D2"
    item_dir = _write_ledger_item(
        tmp_path,
        uid=uid,
        slug="unsafe-pass-pending-actions",
        title="Unsafe PASS_PENDING_ACTIONS",
        status="draft",
    )
    job_id = "unsafe_pass_pending_actions_job"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "prompt": "Implement and verify.",
            "context_refs": [str((item_dir / "pis.md").resolve().relative_to(tmp_path)).replace('\\', '/')],
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_id,
        {
            "last_status": "failed_verification",
            "last_run_id": "sj_20260315T000300Z_c",
            "last_finished_utc": healer._utc_iso(),
        },
        repo_root=tmp_path,
    )
    for suffix in ("a", "b", "c"):
        _write_receipt(
            tmp_path,
            job_id=job_id,
            run_id=f"sj_20260315T000300Z_{suffix}",
            status="failed_verification",
            error="invalid PASS_PENDING_ACTIONS contract: pending_actions_scope_widening:infra/controlplane/scheduled_jobs.py",
            builder="Changed files:\n- infra/controlplane/scheduled_jobs.py\n\nValidation Commands Run:\n- pytest -q",
            verifier="VERDICT: PASS_PENDING_ACTIONS\nunsafe scope",
        )

    findings = healer.detect_findings(repo_root=tmp_path, repeat_failure_threshold=3, idle_minutes=60)
    finding_types = {(row["finding_type"], row["job_id"]) for row in findings["findings"]}
    assert ("repeated_contract_shape_failure", job_id) not in finding_types
