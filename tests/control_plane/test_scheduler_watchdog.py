from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path

from tools.bin import scheduler_pipeline_healer as healer
from tools.bin import scheduler_watchdog as watchdog
from tools.bin import scheduler_watchdog_timer as timer
from infra.controlplane import scheduled_jobs as sched
import time


def _stabilize_watchdog_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_repair_watchdog_report_pointers", lambda repo_root, mode: ([], []))
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog._operator_snapshot, "build_operator_snapshot", lambda repo_root: {"operator_summary": {"truth_conflicts": []}})
    monkeypatch.setattr(watchdog._execution_proof, "read_latest_report", lambda repo_root, now_utc="": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False}})
    monkeypatch.setattr(
        watchdog._watchdog_timer,
        "timer_status_snapshot",
        lambda: {
            "installed": True,
            "units": {
                "audit_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
                "selfheal_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
            },
        },
    )


def test_run_watchdog_selfheal_reports_execution_state_repair_without_scheduler_work_backlog(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    called = {"healer": False}

    def _fake_run_healer(**kwargs):
        called["healer"] = True
        return {"summary": {"finding_count": 1, "repairs_applied": 1}, "apply_mode": True}

    monkeypatch.setattr(healer, "run_healer", _fake_run_healer)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path, allow_pipeline_healer=True)

    assert called["healer"] is False
    assert result["maintenance_ledger"]["scheduler_work_item_count"] == 0
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "execution_state_disagreement")
    assert decision["decision_kind"] == "monitor_only"
    assert decision["scheduler_disposition"] == "ready_for_scheduler"
    assert decision["recommended_actions"] == [
        "reconcile_scheduler_runtime_state",
        "clear_stale_runtime_claims",
        "scheduler_pipeline_healer_full_recovery",
    ]
    assert not any(str(action.get("action") or "") == "pipeline_healer" for action in result["actions"])


def test_run_watchdog_defaults_to_legacy_scheduler_backend(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path, scheduler_mode="legacy")

    assert result["scheduler_backend"] == "legacy"


def test_run_watchdog_v2_backend_reads_surface_bundle_without_legacy_scheduler_mutation(tmp_path: Path, monkeypatch) -> None:
    legacy_calls = {"list_jobs": 0}

    def _legacy_list_jobs(*args, **kwargs):
        legacy_calls["list_jobs"] += 1
        raise AssertionError("v2 watchdog mode must not read legacy scheduler jobs")

    monkeypatch.setattr(sched, "list_jobs", _legacy_list_jobs)
    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_shadow", "canonical_owner": "legacy_scheduler"},
                "surfaces": {
                    "watch": [
                        {"job_id": "v2_job_1", "decision": "retired"},
                        {"job_id": "v2_job_2", "decision": "active"},
                        {"job_id": "v2_job_3", "decision": "blocked"},
                    ],
                    "api": [],
                    "web": [],
                    "mobile": [],
            },
        },
    )

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert legacy_calls["list_jobs"] == 0
    assert result["scheduler_backend"] == "v2"
    assert result["scheduler_v2"]["parent_uid"] == "PIS-V2-PARENT"
    assert result["summary"]["total_jobs"] == 3
    assert result["summary"]["ready_jobs"] == 0
    assert result["summary"]["running_jobs"] == 1
    assert result["summary"]["blocked_jobs"] == 1
    assert result["selfheal_mutating_actions_allowed"] is False
    assert result["tick_result"]["decision_only_boundary"] is True
    assert not result["scheduler_work_items"]


def test_run_watchdog_v2_selfheal_recovers_idle_ready_head_with_blocked_dependents(tmp_path: Path, monkeypatch) -> None:
    requested = {}

    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_ready_head",
                        "child_uid": "PIS-READY",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_blocked_tail",
                        "child_uid": "PIS-BLOCKED",
                        "decision": "blocked",
                        "reason": "dependency_pending:PIS-READY",
                        "unmet_dependencies": ["pis_ready_head"],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["job_id"] = job_id
        requested["request_source"] = request_source
        return {"ok": True, "job_id": job_id, "queued": True, "reason": "run_now_requested"}

    monkeypatch.setattr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True})
    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested == {
        "job_id": "pis_ready_head",
        "request_source": "watchdog_v2_progress_recovery",
    }
    assert result["selfheal_mutating_actions_allowed"] is True
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "sched_v2_stalled_ready_head"
    assert any(f["kind"] == "sched_v2_stalled_ready_head" for f in result["findings"])
    assert any(a["action"] == "resume_stalled_v2_ready_head" and a["ok"] is True for a in result["actions"])
    assert not result["scheduler_work_items"]


def test_run_watchdog_v2_selfheal_uses_dispatch_job_id_for_explicit_pass_chain_ready_head(tmp_path: Path, monkeypatch) -> None:
    requested = {}

    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_ready_tail_projection",
                        "dispatch_job_id": "pis_ready_pass_head",
                        "child_uid": "PIS-READY",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_blocked_tail",
                        "child_uid": "PIS-BLOCKED",
                        "decision": "blocked",
                        "reason": "dependency_pending:PIS-READY",
                        "unmet_dependencies": ["pis_ready_tail_projection"],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["job_id"] = job_id
        requested["request_source"] = request_source
        return {"ok": True, "job_id": job_id, "queued": True, "reason": "run_now_requested"}

    monkeypatch.setattr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True})
    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested == {
        "job_id": "pis_ready_pass_head",
        "request_source": "watchdog_v2_progress_recovery",
    }
    action = next(item for item in result["actions"] if item["action"] == "resume_stalled_v2_ready_head")
    assert action["job_id"] == "pis_ready_tail_projection"
    assert action["dispatch_job_id"] == "pis_ready_pass_head"
    finding = next(item for item in result["findings"] if item["kind"] == "sched_v2_stalled_ready_head")
    assert finding["details"]["job_id"] == "pis_ready_tail_projection"
    assert finding["details"]["dispatch_job_id"] == "pis_ready_pass_head"


def test_watchdog_maintenance_run_writes_family_scoped_latest_pointer_for_v2_parent(tmp_path: Path) -> None:
    scope = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-TESTA")

    result = watchdog._write_watchdog_maintenance_run(
        repo_root=tmp_path,
        now=watchdog._utc_now(),
        mode="audit",
        run_id="watchdog_run_scope_a",
        summary={"total_jobs": 1},
        health={"health": "degraded", "reason": "sched_v2_surface_blocked"},
        findings=[{"kind": "sched_v2_surface_blocked"}],
        direct_actions=[],
        truth_reconciliation={"overall_state": "truth_ambiguous"},
        root_causes=[{"condition_id": "execution_state_disagreement", "risk_level": "high", "finding_count": 1}],
        playbook_decisions=[],
        scheduler_work_items=[],
        containments=[],
        incidents=[],
        predictions=[],
        escalation_bundles=[],
        scope=scope,
    )

    scoped_latest = Path(result["scoped_latest_path"])
    assert scoped_latest.exists()
    payload = json.loads(scoped_latest.read_text(encoding="utf-8"))
    assert payload["scope"]["parent_uid"] == "PIS-20260413-TESTA"
    assert payload["scope"]["scheduler_backend"] == "v2"


def test_watchdog_repeated_condition_history_is_family_scoped(tmp_path: Path) -> None:
    scope_a = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-A")
    scope_b = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-B")

    for idx, scope in enumerate((scope_a, scope_a, scope_b), start=1):
        watchdog._write_watchdog_maintenance_run(
            repo_root=tmp_path,
            now=watchdog._utc_now(),
            mode="audit",
            run_id=f"watchdog_scope_{idx}",
            summary={"total_jobs": 1},
            health={"health": "degraded", "reason": "truth_ambiguous"},
            findings=[{"kind": "execution_state_disagreement"}],
            direct_actions=[],
            truth_reconciliation={"overall_state": "truth_ambiguous"},
            root_causes=[{"condition_id": "execution_state_disagreement", "risk_level": "high", "finding_count": 1}],
            playbook_decisions=[],
            scheduler_work_items=[],
            containments=[],
            incidents=[],
            predictions=[],
            escalation_bundles=[],
            scope=scope,
        )

    repeated_a = watchdog._watchdog_repeated_maintenance_condition_ids(
        repo_root=tmp_path,
        current_condition_ids=["execution_state_disagreement"],
        scope=scope_a,
        threshold=3,
    )
    repeated_b = watchdog._watchdog_repeated_maintenance_condition_ids(
        repo_root=tmp_path,
        current_condition_ids=["execution_state_disagreement"],
        scope=scope_b,
        threshold=3,
    )

    assert repeated_a == ["execution_state_disagreement"]
    assert repeated_b == []


def test_watchdog_escalation_index_writes_family_scoped_latest_pointer(tmp_path: Path) -> None:
    scope = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-SCOPE")

    bundles = watchdog._write_bounded_codex_escalation_bundles(
        repo_root=tmp_path,
        mode="audit",
        now=watchdog._utc_now(),
        run_id="watchdog_escalation_scope_a",
        health={"health": "degraded"},
        findings=[{"kind": "execution_state_disagreement", "details": {"summary": "family interference"}}],
        truth_reconciliation={"overall_state": "truth_ambiguous"},
        playbook_decisions=[
            {
                "condition_id": "execution_state_disagreement",
                "decision_kind": "containment",
                "risk_level": "high",
                "diagnosis": "Cross-family interference left truth ambiguous.",
            }
        ],
        incidents=[],
        predictions=[],
        scope=scope,
    )

    assert len(bundles) == 1
    escalation_root = watchdog._watchdog_escalation_root(tmp_path)
    scoped_latest = escalation_root / "latest_audit__family_parent__PIS-20260413-SCOPE.json"
    assert scoped_latest.exists()
    payload = json.loads(scoped_latest.read_text(encoding="utf-8"))
    assert payload["scope"]["parent_uid"] == "PIS-20260413-SCOPE"


def test_execute_codex_escalation_bundle_dedupes_only_within_same_binding_scope(tmp_path: Path, monkeypatch) -> None:
    scope_a = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-A")
    scope_b = watchdog._watchdog_scope(scheduler_backend="v2", parent_uid="PIS-20260413-B")
    bundle_path = tmp_path / "bundle_b.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduler_watchdog_codex_escalation_v1",
                "condition_id": "execution_state_disagreement",
                "escalation_class": "bounded_repair_codex",
                "run_id": "watchdog_bundle_b",
                "binding": scope_b["binding"],
            }
        ),
        encoding="utf-8",
    )

    receipt_root = watchdog._watchdog_codex_runs_root(tmp_path)
    receipt_root.mkdir(parents=True, exist_ok=True)
    (receipt_root / "older__execution_state_disagreement.json").write_text(
        json.dumps(
            {
                "condition_id": "execution_state_disagreement",
                "turn_id": "turn_active_a",
                "binding": scope_a["binding"],
                "result": {"ok": True},
                "role": "builder",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(watchdog, "_codex_receipt_is_active", lambda receipt, repo_root: bool(receipt))
    sent: list[dict] = []
    monkeypatch.setattr(
        watchdog._agents,
        "send_message",
        lambda **kwargs: sent.append(kwargs) or {"ok": True, "turn": {"turn_id": "turn_new"}, "assistant": {"status": "ok"}},
    )

    result = watchdog.execute_codex_escalation_bundle(repo_root=tmp_path, bundle_path=str(bundle_path))

    assert result.get("deduped") is not True
    assert len(sent) == 1
    assert sent[0]["binding"] == scope_b["binding"]


def test_run_watchdog_v2_selfheal_reports_missing_ready_head_without_run_now(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_missing_head",
                        "child_uid": "PIS-MISSING",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_blocked_tail",
                        "child_uid": "PIS-BLOCKED",
                        "decision": "blocked",
                        "reason": "dependency_pending:PIS-MISSING",
                        "unmet_dependencies": ["pis_missing_head"],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )
    monkeypatch.setattr(sched, "load_job", lambda job_id, repo_root=None: (_ for _ in ()).throw(FileNotFoundError(job_id)))

    requested = {"called": False}

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["called"] = True
        return {"ok": True}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested["called"] is False
    assert any(f["kind"] == "sched_v2_surface_job_missing" for f in result["findings"])
    assert result["scheduler_work_items"] == []


def test_run_watchdog_v2_marks_active_and_ready_surface_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    requested = {"called": False}

    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_active",
                        "child_uid": "PIS-ACTIVE",
                        "decision": "active",
                        "reason": "active_run_present",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_ready",
                        "child_uid": "PIS-READY",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )
    monkeypatch.setattr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True})

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["called"] = True
        return {"ok": True}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested["called"] is False
    assert any(f["kind"] == "sched_v2_surface_active_ready_conflict" for f in result["findings"])
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "sched_v2_surface_active_ready_conflict"


def test_run_watchdog_v2_marks_multiple_ready_heads_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    requested = {"called": False}

    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_ready_1",
                        "child_uid": "PIS-READY-1",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_ready_2",
                        "child_uid": "PIS-READY-2",
                        "decision": "admit",
                        "reason": "due",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_blocked_tail",
                        "child_uid": "PIS-BLOCKED",
                        "decision": "blocked",
                        "reason": "dependency_pending:PIS-READY-1",
                        "unmet_dependencies": ["pis_ready_1"],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )
    monkeypatch.setattr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True})

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["called"] = True
        return {"ok": True}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested["called"] is False
    assert any(f["kind"] == "sched_v2_multiple_ready_heads" for f in result["findings"])
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "sched_v2_multiple_ready_heads"


def test_run_watchdog_v2_marks_multiple_active_heads_as_ambiguous(tmp_path: Path, monkeypatch) -> None:
    requested = {"called": False}

    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {
                        "job_id": "pis_active_1",
                        "child_uid": "PIS-ACTIVE-1",
                        "decision": "active",
                        "reason": "active_run_present",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_active_2",
                        "child_uid": "PIS-ACTIVE-2",
                        "decision": "active",
                        "reason": "active_run_present",
                        "unmet_dependencies": [],
                    },
                    {
                        "job_id": "pis_blocked_tail",
                        "child_uid": "PIS-BLOCKED",
                        "decision": "blocked",
                        "reason": "dependency_pending:PIS-ACTIVE-2",
                        "unmet_dependencies": ["pis_active_2"],
                    },
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        },
    )

    def _fake_request_run_now(job_id: str, *, repo_root=None, request_source=""):
        requested["called"] = True
        return {"ok": True}

    monkeypatch.setattr(sched, "request_run_now", _fake_request_run_now)

    result = watchdog.run_watchdog(
        mode="selfheal",
        repo_root=tmp_path,
        scheduler_mode="v2",
        v2_parent_uid="PIS-V2-PARENT",
    )

    assert requested["called"] is False
    assert any(f["kind"] == "sched_v2_multiple_active_heads" for f in result["findings"])
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "sched_v2_multiple_active_heads"
    assert result["truth_reconciliation"]["overall_state"] == "truth_ambiguous"


def test_run_watchdog_detects_stale_lock_even_when_only_stale_lock_snapshot_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_repair_watchdog_report_pointers", lambda repo_root, mode: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog._operator_snapshot, "build_operator_snapshot", lambda repo_root: {"operator_summary": {"truth_conflicts": []}})
    monkeypatch.setattr(watchdog._execution_proof, "read_latest_report", lambda repo_root, now_utc="": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False}})
    monkeypatch.setattr(
        watchdog._watchdog_timer,
        "timer_status_snapshot",
        lambda: {
            "installed": True,
            "units": {
                "audit_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
                "selfheal_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
            },
        },
    )
    sched.save_job(
        {
            "job_id": "ghost_lock_job",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "ghost_lock_job"},
            "prompt": "noop",
            "schedule": {"mode": "once", "run_at": "2026-04-13T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )
    lock_path = sched.locks_dir(tmp_path) / "ghost_lock_job.lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "ghost_lock_job",
                "run_id": "sj_ghost_lock",
                "pid": 999999,
                "claimed_utc": "2026-04-13T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path, scheduler_mode="legacy")

    stale_lock = next(item for item in result["findings"] if item["kind"] == "stale_lock")
    assert stale_lock["details"]["job_id"] == "ghost_lock_job"
    assert stale_lock["details"]["lock_snapshot_stale_reason"] in {"dead_pid", "stale_heartbeat"}


def test_run_watchdog_detects_terminal_receipt_state_conflict(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    sched.save_job(
        {
            "job_id": "receipt_conflict_job",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "receipt_conflict_job"},
            "prompt": "noop",
            "schedule": {"mode": "once", "run_at": "2026-04-13T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
        },
        repo_root=tmp_path,
    )
    receipt_path = sched.receipts_dir(tmp_path) / "receipt_conflict_job" / "sj_receipt_conflict.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": "receipt_conflict_job",
                "run_id": "sj_receipt_conflict",
                "status": "ok",
                "finished_at": watchdog._utc_iso(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    now_utc = watchdog._utc_iso()
    sched._write_state(
        "receipt_conflict_job",
        {
            "job_id": "receipt_conflict_job",
            "active_run_id": "sj_receipt_conflict",
            "active_started_utc": now_utc,
            "last_status": "running",
            "last_receipt_path": str(receipt_path),
            "updated_utc": now_utc,
        },
        repo_root=tmp_path,
    )
    lock_path = sched.locks_dir(tmp_path) / "receipt_conflict_job.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "receipt_conflict_job",
                "run_id": "sj_receipt_conflict",
                "pid": os.getpid(),
                "claimed_utc": now_utc,
                "heartbeat_utc": now_utc,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    conflict = next(item for item in result["findings"] if item["kind"] == "terminal_receipt_state_conflict")
    assert conflict["details"]["job_id"] == "receipt_conflict_job"
    assert conflict["details"]["receipt_status"] == "ok"
    assert result["truth_reconciliation"]["overall_state"] == "truth_degraded"


def test_run_watchdog_detects_terminal_receipt_state_conflict_without_active_run_id(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    sched.save_job(
        {
            "job_id": "receipt_conflict_no_active_run_job",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "receipt_conflict_no_active_run_job"},
            "prompt": "noop",
            "schedule": {"mode": "once", "run_at": "2026-04-13T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
        },
        repo_root=tmp_path,
    )
    receipt_path = sched.receipts_dir(tmp_path) / "receipt_conflict_no_active_run_job" / "sj_receipt_conflict_002.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": "receipt_conflict_no_active_run_job",
                "run_id": "sj_receipt_conflict_002",
                "status": "ok",
                "finished_at": watchdog._utc_iso(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sched._write_state(
        "receipt_conflict_no_active_run_job",
        {
            "job_id": "receipt_conflict_no_active_run_job",
            "active_run_id": "",
            "last_status": "running",
            "last_receipt_path": str(receipt_path),
            "updated_utc": watchdog._utc_iso(),
        },
        repo_root=tmp_path,
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    conflict = next(item for item in result["findings"] if item["kind"] == "terminal_receipt_state_conflict")
    assert conflict["details"]["job_id"] == "receipt_conflict_no_active_run_job"
    assert conflict["details"]["active_run_id"] == ""
    assert conflict["details"]["receipt_run_id"] == "sj_receipt_conflict_002"
    assert conflict["details"]["receipt_status"] == "ok"


def test_run_watchdog_auto_backend_uses_v2_when_parent_uid_is_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_WATCHDOG_SCHED_V2_PARENT_UID", "PIS-V2-PARENT")
    monkeypatch.setattr(
        watchdog.sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **kwargs: {
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready_without_family",
            "projection_equivalent": True,
            "parent_uid": str(kwargs.get("parent_uid") or ""),
            "ownership": {"mode": "v2_shadow", "canonical_owner": "legacy_scheduler"},
            "surfaces": {"watch": [], "api": [], "web": [], "mobile": []},
        },
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path, scheduler_mode="auto")

    assert result["scheduler_backend"] == "v2"
    assert result["scheduler_v2"]["parent_uid"] == "PIS-V2-PARENT"


def test_run_watchdog_maintenance_ledger_persists_replayable_run_and_receipt_correlation(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    ledger = result["maintenance_ledger"]
    run_path = Path(ledger["run_path"])
    latest_path = Path(ledger["latest_path"])
    event_log_path = Path(ledger["event_log_path"])
    assert run_path.exists()
    assert latest_path.exists()
    assert event_log_path.exists()

    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in event_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert run_payload["schema_version"] == "scheduler_watchdog_maintenance_run_v1"
    assert run_payload["run_id"] == ledger["run_id"]
    assert latest_payload["run_id"] == ledger["run_id"]
    assert events[-1]["run_id"] == ledger["run_id"]
    assert events[-1]["scheduler_work_item_count"] == 0
    assert run_payload["authority_boundary"]["execution_authority"] == "scheduler_plus_bounded_watchdog_progress_recovery"
    assert run_payload["playbook_decisions"][0]["decision_kind"] == "monitor_only"
    assert run_payload["scheduler_work_items"] == []


def test_run_watchdog_selfheal_skips_direct_mutations_when_active_jobs_exist(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [{"job_id": "stale_2", "active_run_id": "r2"}])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [{"job_id": "active_1", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}],
    )

    called = {"healer": False}

    def _fake_run_healer(**kwargs):
        called["healer"] = True
        return {"summary": {"finding_count": 1, "repairs_applied": 1}, "apply_mode": True}

    monkeypatch.setattr(healer, "run_healer", _fake_run_healer)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert result["selfheal_mutating_actions_allowed"] is False
    assert result["selfheal_active_job_ids"] == ["active_1"]
    assert called["healer"] is False
    assert any(f["kind"] == "active_runs_present" for f in result["findings"])
    assert any(f["kind"] == "tick_skipped_due_to_active_runs" for f in result["findings"])
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "execution_state_disagreement")
    assert decision["decision_kind"] == "monitor_only"
    assert decision["scheduler_disposition"] == "queue_when_scheduler_is_idle"
    assert not any(a["action"] == "pipeline_healer" for a in result["actions"])
    assert not any(a["action"] == "delete_stale_lock" for a in result["actions"])
    assert not any(a["action"] == "clear_orphan_running_state" for a in result["actions"])


def test_run_watchdog_selfheal_records_stale_state_as_scheduler_work_even_when_active_jobs_exist(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    lock_path = tmp_path / "stale.lock.json"
    lock_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": str(lock_path)}], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [{"job_id": "stale_2", "active_run_id": "r2"}])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [{"job_id": "active_1", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}],
    )
    cleared: list[tuple[str, str]] = []

    def _fake_clear(job_id: str, *, repo_root: Path, reason: str) -> bool:
        cleared.append((job_id, reason))
        return True

    monkeypatch.setattr(watchdog, "_clear_stale_state", _fake_clear)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert cleared == []
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "execution_state_disagreement")
    assert decision["recommended_actions"][:2] == [
        "reconcile_scheduler_runtime_state",
        "clear_stale_runtime_claims",
    ]
    assert decision["scheduler_disposition"] == "queue_when_scheduler_is_idle"


def test_run_watchdog_selfheal_resumes_idle_stalled_dependency_head_and_ticks_scheduler(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    now = watchdog._utc_now()
    initial_jobs = [
        {
            "job_id": "head_1",
            "enabled": True,
            "schedule": {"mode": "once", "run_at": "2026-04-06T09:00:00Z"},
            "due": {"eligible": False, "reason": "retry_backoff"},
            "state": {"last_status": "failed_verification", "last_finished_utc": watchdog._utc_iso(now - watchdog.dt.timedelta(minutes=8))},
        },
        {
            "job_id": "dep_1",
            "enabled": True,
            "schedule": {"mode": "once", "run_at": "2026-04-06T09:00:00Z"},
            "depends_on": ["head_1"],
            "due": {"eligible": False, "reason": "dependency_not_ok:head_1"},
            "state": {"last_status": "blocked"},
        },
    ]
    recovered_jobs = [
        {
            "job_id": "head_1",
            "enabled": True,
            "schedule": {"mode": "once", "run_at": "2026-04-06T09:00:00Z"},
            "due": {"eligible": False, "reason": "run_now_requested"},
            "state": {"last_status": "queued", "active_run_id": "sj_demo"},
        },
        initial_jobs[1],
    ]
    calls = {"list_jobs": 0, "request_run_now": [], "ticks": 0}

    def _fake_list_jobs(repo_root=None, now_utc=None):
        calls["list_jobs"] += 1
        return recovered_jobs if calls["list_jobs"] > 1 else initial_jobs

    monkeypatch.setattr(sched, "list_jobs", _fake_list_jobs)
    monkeypatch.setattr(
        sched,
        "request_run_now",
        lambda job_id, repo_root=None, requested_utc=None, request_source="manual": calls["request_run_now"].append((job_id, request_source)) or {
            "ok": True,
            "job_id": job_id,
            "run_id": "run_now_demo",
            "queued": True,
            "reason": "run_now_requested",
        },
    )
    monkeypatch.setattr(sched, "scheduler_tick", lambda repo_root=None, limit=10: calls.__setitem__("ticks", calls["ticks"] + 1) or {"executed_count": 1})

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert calls["request_run_now"] == [("head_1", "watchdog_progress_recovery")]
    assert calls["ticks"] == 1
    assert any(a["action"] == "resume_stalled_dependency_head" and a["ok"] is True for a in result["actions"])
    assert any(a["action"] == "scheduler_tick_for_progress_recovery" and a["ok"] is True for a in result["actions"])
    assert not any(f["kind"] == "stalled_progress_chain_unrecovered" for f in result["findings"])


def test_run_watchdog_selfheal_executes_bounded_codex_when_progress_recovery_does_not_restore_movement(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    now = watchdog._utc_now()
    stalled_jobs = [
        {
            "job_id": "head_1",
            "enabled": True,
            "schedule": {"mode": "once", "run_at": "2026-04-06T09:00:00Z"},
            "due": {"eligible": False, "reason": "retry_backoff"},
            "state": {"last_status": "failed_verification", "last_finished_utc": watchdog._utc_iso(now - watchdog.dt.timedelta(minutes=9))},
        },
        {
            "job_id": "dep_1",
            "enabled": True,
            "schedule": {"mode": "once", "run_at": "2026-04-06T09:00:00Z"},
            "depends_on": ["head_1"],
            "due": {"eligible": False, "reason": "dependency_not_ok:head_1"},
            "state": {"last_status": "blocked"},
        },
    ]
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: stalled_jobs)
    monkeypatch.setattr(
        sched,
        "request_run_now",
        lambda job_id, repo_root=None, requested_utc=None, request_source="manual": {
            "ok": True,
            "job_id": job_id,
            "run_id": "run_now_demo",
            "queued": True,
            "reason": "run_now_requested",
        },
    )
    monkeypatch.setattr(sched, "scheduler_tick", lambda repo_root=None, limit=10: {"executed_count": 0})
    executed: list[str] = []

    def _fake_execute_codex(*, repo_root: Path, bundle_path: str, **kwargs) -> dict:
        executed.append(bundle_path)
        return {"ok": True, "receipt_path": str(tmp_path / "receipt.json"), "role": "builder"}

    monkeypatch.setattr(watchdog, "execute_codex_escalation_bundle", _fake_execute_codex)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert any(f["kind"] == "stalled_progress_chain_unrecovered" for f in result["findings"])
    assert any(a["action"] == "execute_bounded_codex_escalation" and a["ok"] is True for a in result["actions"])
    assert len(executed) == 1


def test_run_watchdog_selfheal_escalates_to_codex_after_three_repeated_repairs_for_same_job_problem(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "job_repeat_1",
                "enabled": True,
                "state": {"last_status": "blocked"},
                "due": {"eligible": False, "reason": "retry_backoff"},
            }
        ],
    )
    receipts_root = healer._receipts_dir(tmp_path)
    finding = {
        "finding_type": "repeated_runtime_signature_failure",
        "repair_class": "runtime_route_repair",
        "job_id": "job_repeat_1",
        "summary": "Job is repeating the same runtime failure signature.",
        "evidence": {"latest_error": "same crash", "latest_run_id": "sj_repeat_latest"},
    }
    for idx in range(1, 4):
        report = {
            "schema_version": healer.RECEIPT_SCHEMA_VERSION,
            "run_id": f"ph_repeat_{idx}",
            "generated_utc": f"2026-04-08T0{idx}:00:00Z",
            "apply_mode": True,
            "findings": [finding],
            "actions": [
                {
                    "job_id": "job_repeat_1",
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "decision": "applied",
                    "details": {"changed": True},
                }
            ],
            "summary": {"finding_count": 1, "repairs_applied": 1},
        }
        healer._write_json((receipts_root / f"ph_repeat_{idx}.json").resolve(), report)
    healer._write_json(healer._latest_report_path(tmp_path), report)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: json.loads(healer._latest_report_path(repo_root).read_text(encoding="utf-8")))
    executed: list[str] = []

    def _fake_execute_codex(*, repo_root: Path, bundle_path: str, **kwargs) -> dict:
        executed.append(bundle_path)
        return {"ok": True, "receipt_path": str(tmp_path / "repeat_receipt.json"), "role": "builder"}

    monkeypatch.setattr(watchdog, "execute_codex_escalation_bundle", _fake_execute_codex)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert any(f["kind"] == "watchdog_repeat_repair_exhausted" for f in result["findings"])
    bundle = next(item for item in result["escalation_bundles"] if item["condition_id"] == "repeat_watchdog_job_problem")
    assert bundle["escalation_class"] == "bounded_repair_codex"
    assert any(
        a["action"] == "execute_bounded_codex_escalation"
        and a["ok"] is True
        and a["condition_id"] == "repeat_watchdog_job_problem"
        for a in result["actions"]
    )
    assert len(executed) == 1


def test_watchdog_repeat_repair_exhaustion_resets_after_terminal_success_receipt(tmp_path: Path) -> None:
    receipts_root = healer._receipts_dir(tmp_path)
    finding = {
        "finding_type": "repeated_runtime_signature_failure",
        "repair_class": "runtime_route_repair",
        "job_id": "job_repeat_healthy_gap",
        "summary": "Job is repeating the same runtime failure signature.",
        "evidence": {"latest_error": "same crash", "latest_run_id": "sj_repeat_latest"},
    }
    for idx in range(1, 4):
        report = {
            "schema_version": healer.RECEIPT_SCHEMA_VERSION,
            "run_id": f"ph_repeat_gap_{idx}",
            "generated_utc": f"2026-04-08T0{idx}:00:00Z",
            "apply_mode": True,
            "findings": [finding],
            "actions": [
                {
                    "job_id": "job_repeat_healthy_gap",
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "decision": "applied",
                    "details": {"changed": True},
                }
            ],
            "summary": {"finding_count": 1, "repairs_applied": 1},
        }
        healer._write_json((receipts_root / f"ph_repeat_gap_{idx}.json").resolve(), report)
    job_receipts_root = sched.receipts_dir(tmp_path) / "job_repeat_healthy_gap"
    job_receipts_root.mkdir(parents=True, exist_ok=True)
    (job_receipts_root / "success_after_storm.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "finished_at": "2026-04-08T04:00:00Z",
                "run_id": "sj_repeat_success",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    healer._write_json(
        healer._latest_report_path(tmp_path),
        {
            "schema_version": healer.RECEIPT_SCHEMA_VERSION,
            "run_id": "ph_repeat_gap_latest",
            "generated_utc": "2026-04-08T05:00:00Z",
            "apply_mode": True,
            "findings": [finding],
            "actions": [
                {
                    "job_id": "job_repeat_healthy_gap",
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "decision": "applied",
                    "details": {"changed": True},
                }
            ],
            "summary": {"finding_count": 1, "repairs_applied": 1},
        },
    )

    findings = watchdog._watchdog_repeat_job_problem_findings(
        repo_root=tmp_path,
        pipeline_healer_result=json.loads(healer._latest_report_path(tmp_path).read_text(encoding="utf-8")),
    )

    assert findings == []


def test_watchdog_repeated_maintenance_condition_ids_reset_after_healthy_gap(tmp_path: Path) -> None:
    runs_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog" / "maintenance" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    payloads = [
        {
            "run_id": "run_1",
            "root_causes": [{"condition_id": "execution_state_disagreement"}],
            "findings": [],
        },
        {
            "run_id": "run_2",
            "root_causes": [{"condition_id": "execution_state_disagreement"}],
            "findings": [],
        },
        {
            "run_id": "run_3",
            "root_causes": [{"condition_id": "healthy_gap"}],
            "findings": [],
        },
    ]
    for idx, payload in enumerate(payloads, start=1):
        (runs_root / f"20260408_run_{idx}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    repeated = watchdog._watchdog_repeated_maintenance_condition_ids(
        repo_root=tmp_path,
        current_condition_ids=["execution_state_disagreement"],
    )

    assert repeated == []


def test_run_watchdog_selfheal_recommends_pipeline_healer_request_by_default(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    called = {"healer": False}

    def _fake_run_healer(**kwargs):
        called["healer"] = True
        return {"summary": {"finding_count": 1, "repairs_applied": 1}, "apply_mode": True}

    monkeypatch.setattr(healer, "run_healer", _fake_run_healer)
    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)
    assert called["healer"] is False
    assert result["allow_pipeline_healer"] is True
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "execution_state_disagreement")
    assert "scheduler_pipeline_healer_full_recovery" in decision["recommended_actions"]


def test_clear_stale_state_clears_loop_and_request_residue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state(
        "ghost_job",
        {
            "active_run_id": "sj_ghost",
            "active_started_utc": "2026-03-31T10:00:00Z",
            "active_loop_iteration": 2,
            "active_loop_stage": "verify",
            "active_loop_completed_iterations": 1,
            "active_loop_max_iterations": 4,
            "active_loop_updated_utc": "2026-03-31T10:05:00Z",
            "run_now_requested_utc": "2026-03-31T09:59:00Z",
            "run_now_request_run_id": "run_now_ghost",
            "run_now_request_source": "manual",
            "cancel_requested_utc": "2026-03-31T10:06:00Z",
            "cancel_request_reason": "operator",
            "cancel_request_run_id": "sj_ghost",
            "cancel_request_receipt_path": "/tmp/cancel.json",
            "postpone_requested_utc": "2026-03-31T10:07:00Z",
            "postpone_request_reason": "operator",
            "postpone_request_run_id": "sj_ghost",
            "postpone_request_new_run_at": "2026-04-01T10:00:00Z",
            "postpone_request_receipt_path": "/tmp/postpone.json",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )

    changed = watchdog._clear_stale_state("ghost_job", repo_root=tmp_path, reason="stale_lock")
    state = sched.load_state("ghost_job", repo_root=tmp_path)

    assert changed is True
    assert state["active_run_id"] == ""
    assert state["active_loop_stage"] == ""
    assert state["active_loop_iteration"] == 0
    assert state["run_now_request_run_id"] == ""
    assert state["cancel_request_run_id"] == ""
    assert state["postpone_request_new_run_at"] == ""
    assert state["last_status"] == "blocked"


def test_run_watchdog_suppresses_stale_pipeline_healer_history_for_non_enabled_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [{"job_id": "active_job", "enabled": True, "state": {"last_status": "", "active_run_id": ""}}],
    )
    monkeypatch.setattr(
        watchdog,
        "_latest_pipeline_healer",
        lambda repo_root: {
            "generated_utc": "2026-03-28T00:00:00Z",
            "summary": {"finding_count": 2, "repairs_applied": 1, "repairs_deferred": 1},
            "findings": [
                {"job_id": "old_job", "finding_type": "stale_ledger_status_after_success"},
                {"job_id": "active_job", "finding_type": "stale_ledger_status_after_success"},
            ],
            "actions": [
                {"job_id": "old_job", "decision": "applied"},
                {"job_id": "active_job", "decision": "deferred"},
            ],
        },
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    ph = result["pipeline_healer"]
    assert ph["stale"] is True
    assert ph["summary"]["finding_count"] == 1
    assert ph["summary"]["historical_findings_suppressed"] == 1
    assert len(ph["findings"]) == 1
    assert ph["findings"][0]["job_id"] == "active_job"
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "pipeline_healer_stale"


def test_run_watchdog_reports_idle_health_when_no_enabled_jobs(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "idle"


def test_run_watchdog_selfheal_reports_invalid_commit_journal_repair_without_scheduler_work_backlog(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    pending = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    bad = pending / "broken.json"
    bad.write_text('{"schema_version":"commit_journal_entry_v1","title":"Broken"', encoding="utf-8")

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert any(f["kind"] == "commit_journal_invalid_fragments" for f in result["findings"])
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "commit_journal_hygiene")
    assert decision["recommended_actions"] == ["quarantine_invalid_commit_journal_fragments"]
    assert decision["scheduler_disposition"] == "ready_for_scheduler"
    assert not any(a["action"] == "quarantine_invalid_commit_journal_fragments" for a in result["actions"])
    invalid_dir = tmp_path / "runtime" / "state" / "commit_journal" / "failed" / "invalid"
    assert not invalid_dir.exists()
    assert bad.exists()


def test_run_watchdog_reports_runtime_storage_low(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(total=10, used=9, free=400 * 1024 * 1024))

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    assert any(f["kind"] == "runtime_storage_critical" for f in result["findings"])


def test_run_watchdog_does_not_hide_runtime_storage_critical_behind_active_busy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {"job_id": "job_active_storage", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}
        ],
    )
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(total=10, used=9, free=400 * 1024 * 1024))

    result = watchdog.run_watchdog(mode="observe", repo_root=tmp_path)

    assert any(f["kind"] == "runtime_storage_critical" for f in result["findings"])
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "runtime_storage_health"


def test_run_watchdog_does_not_hide_stale_lock_behind_active_busy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {"job_id": "job_active_lock", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}
        ],
    )
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "job_active_lock", "path": "/tmp/ghost.lock"}], []))

    result = watchdog.run_watchdog(mode="observe", repo_root=tmp_path)

    assert any(f["kind"] == "stale_lock" for f in result["findings"])
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "runtime_recovery_needed"


def test_run_watchdog_reports_timer_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [{"job_id": "job1", "enabled": True, "state": {"last_status": ""}}])
    monkeypatch.setattr(
        watchdog._watchdog_timer,
        "timer_status_snapshot",
        lambda: {
            "installed": True,
            "units": {
                "audit_timer": {"active_state": "inactive", "unit_file_state": "disabled", "last_trigger_utc": "2026-04-01T08:00:00Z"},
                "selfheal_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": "2026-04-01T08:10:00Z"},
            },
        },
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    assert any(f["kind"] == "watchdog_timer_inactive" for f in result["findings"])
    assert any(f["kind"] == "watchdog_timer_disabled" for f in result["findings"])
    assert result["health"]["reason"] == "watchdog_timer_drift"


def test_run_watchdog_selfheal_repairs_missing_latest_report_pointer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog, "_tick_if_due_and_idle", lambda repo_root: {"ran_tick": False, "due_eligible_count": 0, "active_count": 0})
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(watchdog._watchdog_timer, "timer_status_snapshot", lambda: {"installed": True, "units": {}})

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    archived_json = reports_root / "selfheal_20260401T000000Z.json"
    archived_md = reports_root / "selfheal_20260401T000000Z.md"
    archived_json.write_text('{"ok": true}\n', encoding="utf-8")
    archived_md.write_text("# report\n", encoding="utf-8")

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert any(f["kind"] == "watchdog_latest_report_pointer_needs_repair" for f in result["findings"])
    assert any(a["action"] == "repair_watchdog_latest_report_pointer" and a["ok"] is True for a in result["actions"])
    assert (reports_root / "latest_selfheal.json").exists()
    assert (reports_root / "latest_selfheal.md").exists()


def test_run_watchdog_selfheal_reports_runtime_residue_cleanup_without_scheduler_work_backlog(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    stale_dir = tmp_path / "runtime" / "reports" / "tmp_old_bundle"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "artifact.txt").write_text("x", encoding="utf-8")
    os.utime(stale_dir, (0, 0))
    os.utime(stale_dir / "artifact.txt", (0, 0))

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert any(f["kind"] == "runtime_residue_cleanup_needed" for f in result["findings"])
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "runtime_residue_hygiene")
    assert decision["recommended_actions"] == ["cleanup_runtime_residue"]
    assert not any(a["action"] == "cleanup_runtime_residue" for a in result["actions"])
    assert stale_dir.exists()


def test_run_watchdog_writes_escalation_bundle_for_degraded_root_cause(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": str(tmp_path / "missing.lock")}], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [{"job_id": "job1", "enabled": True, "state": {"last_status": ""}}])
    monkeypatch.setattr(watchdog._watchdog_timer, "timer_status_snapshot", lambda: {"installed": True, "units": {}})

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    bundles = result["escalation_bundles"]
    assert bundles
    assert any(bundle["condition_id"] == "execution_state_disagreement" for bundle in bundles)
    latest = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog" / "escalations" / "latest_audit.json"
    assert latest.exists()


def test_run_watchdog_selfheal_reports_plane_truth_reconciliation_and_refreshes_proof_without_scheduler_work_backlog(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(
        watchdog._operator_snapshot,
        "build_operator_snapshot",
        lambda repo_root: {"operator_summary": {"truth_conflicts": [{"kind": "stale_task_register_with_open_work"}]}},
    )

    called: dict[str, int] = {"recovery": 0, "summary_cache": 0, "proof": 0}
    monkeypatch.setattr(
        watchdog._lab_recovery,
        "reconcile_startup",
        lambda repo_root, writer="": called.__setitem__("recovery", called["recovery"] + 1),
    )
    monkeypatch.setattr(
        watchdog._lab_task_register,
        "rebuild_summary_cache",
        lambda repo_root, writer="": called.__setitem__("summary_cache", called["summary_cache"] + 1),
    )
    monkeypatch.setattr(
        watchdog._execution_proof,
        "read_latest_report",
        lambda repo_root, now_utc="": {"summary": {"overall_status": "missing"}, "freshness": {"stale": True, "reason": "missing_proof_report"}},
    )

    def _fake_record(repo_root, writer="", now_utc=""):
        called["proof"] += 1
        return {"proof_run_id": "proof_demo", "summary": {"overall_status": "proof_gap"}}

    monkeypatch.setattr(watchdog._execution_proof, "record_execution_proof_report", _fake_record)

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert called["recovery"] == 0
    assert called["summary_cache"] == 0
    assert called["proof"] == 1
    assert not any(a["action"] == "plane_truth_selfheal" for a in result["actions"])
    assert any(a["action"] == "refresh_delegation_proof" and a["ok"] is True for a in result["actions"])
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "plane_truth_reconciliation_request")
    assert decision["recommended_actions"] == ["reconcile_plane_truth_projection"]


def test_run_watchdog_audit_identifies_stale_scheduler_task_register_residue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog, "_tick_if_due_and_idle", lambda repo_root: {"ran_tick": False, "due_eligible_count": 0, "active_count": 0})
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(watchdog._execution_proof, "read_latest_report", lambda repo_root, now_utc="": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False}})
    monkeypatch.setattr(watchdog._operator_snapshot, "build_operator_snapshot", lambda repo_root: {"operator_summary": {"truth_conflicts": []}})

    now = watchdog._utc_now()
    stale_utc = watchdog._utc_iso(now - watchdog.dt.timedelta(hours=2))
    register = {
        "write_generation": 5,
        "active_tasks": [
            {
                "task_id": "smk",
                "status": "interrupted_recovering",
                "updated_utc": stale_utc,
                "delegation_refs": [{"surface": "scheduler", "subsystem_id": "swarm_live_smoke_1513_003"}],
            }
        ],
        "completed_tasks": [],
        "open_loops": [
            {
                "loop_id": "writeback::smk::blocked_question",
                "kind": "blocked_question",
                "status": "open",
                "linked_task_id": "smk",
                "updated_utc": stale_utc,
            }
        ],
    }
    monkeypatch.setattr(
        watchdog._lab_task_register,
        "read_task_register_snapshot",
        lambda repo_root, initialize_if_missing=False: {"task_register": register},
    )
    monkeypatch.setattr(watchdog._watchdog_timer, "timer_status_snapshot", lambda: {"installed": True, "units": {}})

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    residue = [f for f in result["findings"] if f["kind"] == "stale_scheduler_task_register_residue"]
    assert residue
    assert residue[0]["details"]["task_ids"] == ["smk"]
    assert residue[0]["details"]["residue_tasks"] == [
        {
            "task_id": "smk",
            "referenced_job_ids": ["swarm_live_smoke_1513_003"],
            "binding_sources": ["delegation_refs"],
            "live_binding_sources": ["delegation_refs"],
        }
    ]
    assert residue[0]["details"]["open_loop_count"] == 1
    assert not any(a["action"] == "cleanup_stale_scheduler_task_register_residue" for a in result["actions"])


def test_run_watchdog_selfheal_reports_stale_scheduler_task_register_residue_without_scheduler_work_backlog(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    now = watchdog._utc_now()
    stale_utc = watchdog._utc_iso(now - watchdog.dt.timedelta(hours=2))
    register = {
        "write_generation": 5,
        "active_tasks": [
            {
                "task_id": "smk",
                "status": "interrupted_recovering",
                "updated_utc": stale_utc,
                "delegation_refs": [{"surface": "scheduler", "subsystem_id": "swarm_live_smoke_1513_003"}],
            }
        ],
        "completed_tasks": [],
        "open_loops": [
            {
                "loop_id": "writeback::smk::blocked_question",
                "kind": "blocked_question",
                "status": "open",
                "linked_task_id": "smk",
                "updated_utc": stale_utc,
            }
        ],
    }

    monkeypatch.setattr(
        watchdog._lab_task_register,
        "read_task_register_snapshot",
        lambda repo_root, initialize_if_missing=False: {"task_register": register},
    )

    called = {"recovery": 0}
    monkeypatch.setattr(
        watchdog._lab_recovery,
        "reconcile_startup",
        lambda repo_root, writer="": called.__setitem__("recovery", called["recovery"] + 1) or {"status": "ready"},
    )

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert called["recovery"] == 0
    assert len(register["active_tasks"]) == 1
    assert len(register["open_loops"]) == 1
    assert result["scheduler_work_items"] == []
    decision = next(item for item in result["playbook_decisions"] if item["condition_id"] == "stale_scheduler_task_register_residue")
    assert decision["recommended_actions"] == ["cleanup_stale_scheduler_task_register_residue"]
    assert decision["recommended_action_targets"] == {
        "residue_tasks": [
            {
                "task_id": "smk",
                "referenced_job_ids": ["swarm_live_smoke_1513_003"],
                "binding_sources": ["delegation_refs"],
                "live_binding_sources": ["delegation_refs"],
            }
        ],
        "residue_open_loops": [{"loop_id": "writeback::smk::blocked_question"}],
    }
    assert not any(a["action"] == "cleanup_stale_scheduler_task_register_residue" for a in result["actions"])


def test_cleanup_stale_scheduler_task_register_residue_clears_hidden_scheduler_runtime_from_non_active_buckets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    register = dict(watchdog._lab_task_register.load_or_init_task_register(tmp_path))
    register["completed_tasks"] = [
        {
            "task_id": "task_completed_hidden",
            "kind": "software",
            "title": "Completed hidden runtime",
            "status": "completed",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "none",
            "updated_utc": "2026-04-13T00:00:00Z",
            "execution_surface_ownership": {"selected_surface": "scheduler", "updated_utc": "2026-04-13T00:00:00Z"},
            "task_runs": [{"run_id": "job_completed_hidden", "surface": "scheduler", "run_state": "completed"}],
            "current_run_id": "job_completed_hidden",
        }
    ]
    register["residue_tasks"] = [
        {
            "task_id": "task_residue_hidden",
            "kind": "software",
            "title": "Residue hidden runtime",
            "status": "interrupted_recovering",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "inspect residue",
            "updated_utc": "2026-04-13T00:00:00Z",
            "execution_surface_ownership": {"selected_surface": "scheduler", "updated_utc": "2026-04-13T00:00:00Z"},
            "task_runs": [{"run_id": "job_residue_hidden", "surface": "scheduler", "run_state": "blocked"}],
            "current_run_id": "job_residue_hidden",
        }
    ]
    register["open_loops"] = [
        {
            "loop_id": "writeback::task_residue_hidden::blocked_question",
            "kind": "blocked_question",
            "status": "open",
            "linked_task_id": "task_residue_hidden",
            "updated_utc": "2026-04-13T00:00:00Z",
        }
    ]
    watchdog._lab_task_register._set_generation(register, writer="watchdog.cleanup.seed")
    watchdog._lab_task_register._write_json_atomic(
        watchdog._lab_task_register._register_path(tmp_path),
        register,
    )
    watchdog._lab_task_register._write_summary_cache(tmp_path, register)
    monkeypatch.setattr(watchdog._lab_recovery, "_live_scheduler_job_ids", lambda repo_root: set())

    result = watchdog._cleanup_stale_scheduler_task_register_residue(
        repo_root=tmp_path,
        now=watchdog._utc_now(),
        writer="watchdog.cleanup.test",
    )

    assert result["ok"] is True
    assert result["removed_task_count"] == 0
    assert result["cleaned_task_count"] == 2
    assert result["removed_open_loop_count"] == 0

    cleaned = watchdog._lab_task_register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    completed_task = cleaned["completed_tasks"][0]
    residue_task = cleaned["residue_tasks"][0]
    assert watchdog._lab_task_register.scheduler_binding_summary(completed_task)["has_live_scheduler_binding"] is False
    assert watchdog._lab_task_register.scheduler_binding_summary(residue_task)["has_live_scheduler_binding"] is False
    assert completed_task.get("task_runs")
    assert residue_task.get("task_runs")
    assert completed_task["task_runs"][0]["run_id"] == "job_completed_hidden"
    assert completed_task["task_runs"][0]["surface"] == "scheduler"
    assert completed_task["task_runs"][0]["run_state"] == "completed"
    assert residue_task["task_runs"][0]["run_id"] == "job_residue_hidden"
    assert residue_task["task_runs"][0]["surface"] == "scheduler"
    assert residue_task["task_runs"][0]["run_state"] == "blocked"
    assert completed_task.get("delegation_refs") == []
    assert residue_task.get("delegation_refs") == []
    assert "execution_surface_ownership" not in completed_task
    assert "execution_surface_ownership" not in residue_task


def test_run_watchdog_audit_identifies_stale_scheduler_task_register_residue_for_mixed_surface_non_active_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog, "_tick_if_due_and_idle", lambda repo_root: {"ran_tick": False, "due_eligible_count": 0, "active_count": 0})
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(watchdog._execution_proof, "read_latest_report", lambda repo_root, now_utc="": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False}})
    monkeypatch.setattr(watchdog._operator_snapshot, "build_operator_snapshot", lambda repo_root: {"operator_summary": {"truth_conflicts": []}})
    monkeypatch.setattr(watchdog._watchdog_timer, "timer_status_snapshot", lambda: {"installed": True, "units": {}})

    now = watchdog._utc_now()
    stale_utc = watchdog._utc_iso(now - watchdog.dt.timedelta(hours=2))
    fresh_utc = watchdog._utc_iso(now - watchdog.dt.timedelta(minutes=5))
    register = {
        "write_generation": 7,
        "active_tasks": [],
        "completed_tasks": [
            {
                "task_id": "mixed_stale",
                "status": "completed",
                "updated_utc": stale_utc,
                "execution_surface_ownership": {
                    "selected_surface": "scheduler",
                    "reference_status": "execution_ref_stale",
                    "execution_id": "verify_mixed_stale",
                },
                "task_runs": [
                    {"run_id": "turn_mixed_stale", "surface": "codex_worker", "run_state": "completed"},
                    {"run_id": "verify_mixed_stale", "surface": "scheduler", "run_state": "stale", "execution_id": "verify_mixed_stale"},
                ],
            },
            {
                "task_id": "mixed_live",
                "status": "completed",
                "updated_utc": fresh_utc,
                "execution_surface_ownership": {
                    "selected_surface": "scheduler",
                    "reference_status": "execution_ref_active",
                    "execution_id": "verify_mixed_live",
                },
                "task_runs": [
                    {"run_id": "turn_mixed_live", "surface": "codex_worker", "run_state": "completed"},
                    {"run_id": "verify_mixed_live", "surface": "scheduler", "run_state": "running", "execution_id": "verify_mixed_live"},
                ],
            },
        ],
        "open_loops": [
            {
                "loop_id": "writeback::mixed_stale::blocked_question",
                "kind": "blocked_question",
                "status": "open",
                "linked_task_id": "mixed_stale",
                "updated_utc": stale_utc,
            }
        ],
    }
    monkeypatch.setattr(
        watchdog._lab_task_register,
        "read_task_register_snapshot",
        lambda repo_root, initialize_if_missing=False: {"task_register": register},
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    residue = [f for f in result["findings"] if f["kind"] == "stale_scheduler_task_register_residue"]
    assert residue
    assert residue[0]["details"]["task_ids"] == ["mixed_stale"]
    assert residue[0]["details"]["loop_ids"] == ["writeback::mixed_stale::blocked_question"]
    assert residue[0]["details"]["residue_tasks"] == [
        {
            "task_id": "mixed_stale",
            "referenced_job_ids": ["verify_mixed_stale"],
            "binding_sources": ["execution_surface_ownership", "task_runs"],
            "live_binding_sources": [],
        }
    ]


def test_run_watchdog_reports_unreadable_task_register_instead_of_crashing(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    def _boom(repo_root, initialize_if_missing=False):
        raise RuntimeError("malformed task register")

    monkeypatch.setattr(watchdog._lab_task_register, "read_task_register_snapshot", _boom)

    result = watchdog.run_watchdog(mode="observe", repo_root=tmp_path)

    findings = [f for f in result["findings"] if f["kind"] == "task_register_snapshot_unreadable"]
    assert findings
    assert findings[0]["details"]["type"] == "RuntimeError"
    assert findings[0]["details"]["message"] == "malformed task register"


def test_run_watchdog_does_not_hide_unreadable_task_register_behind_active_busy(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "active_unreadable_register",
                "enabled": True,
                "state": {"active_run_id": "sj_live", "last_status": "running"},
            }
        ],
    )

    def _boom(repo_root, initialize_if_missing=False):
        raise RuntimeError("malformed task register")

    monkeypatch.setattr(watchdog._lab_task_register, "read_task_register_snapshot", _boom)

    result = watchdog.run_watchdog(mode="observe", repo_root=tmp_path)

    findings = [f for f in result["findings"] if f["kind"] == "task_register_snapshot_unreadable"]
    assert findings
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "task_register_snapshot_unreadable"


def test_run_watchdog_selfheal_does_not_mutate_plane_truth_when_active_jobs_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog, "_tick_if_due_and_idle", lambda repo_root: {"ran_tick": False, "due_eligible_count": 0, "active_count": 0})
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [{"job_id": "active_1", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}],
    )
    monkeypatch.setattr(
        watchdog._operator_snapshot,
        "build_operator_snapshot",
        lambda repo_root: {"operator_summary": {"truth_conflicts": [{"kind": "canonical_run_blocked_but_projection_active"}]}},
    )
    called = {"recovery": 0, "summary_cache": 0}
    monkeypatch.setattr(
        watchdog._lab_recovery,
        "reconcile_startup",
        lambda repo_root, writer="": called.__setitem__("recovery", called["recovery"] + 1),
    )
    monkeypatch.setattr(
        watchdog._lab_task_register,
        "rebuild_summary_cache",
        lambda repo_root, writer="": called.__setitem__("summary_cache", called["summary_cache"] + 1),
    )

    result = watchdog.run_watchdog(mode="selfheal", repo_root=tmp_path)

    assert called["recovery"] == 0
    assert called["summary_cache"] == 0
    assert any(f["kind"] == "plane_truth_conflict_detected_but_not_auto_repairable" for f in result["findings"])
    assert not any(a["action"] == "plane_truth_selfheal" for a in result["actions"])


def test_operator_snapshot_needs_self_heal_only_for_repairable_truth_conflict_kinds() -> None:
    assert watchdog._operator_snapshot_needs_self_heal(
        {"operator_summary": {"truth_conflicts": [{"kind": "stale_task_register_with_open_work"}]}}
    ) is True
    assert watchdog._operator_snapshot_needs_self_heal(
        {"operator_summary": {"truth_conflicts": [{"kind": "recovery_not_settled_with_only_completed_tasks"}]}}
    ) is True
    assert watchdog._operator_snapshot_needs_self_heal(
        {"operator_summary": {"truth_conflicts": [{"kind": "canonical_run_blocked_but_projection_active"}]}}
    ) is False
    assert watchdog._operator_snapshot_needs_self_heal(
        {"operator_summary": {"truth_conflicts": [{"kind": "register_completed_but_projection_incomplete"}]}}
    ) is False


def test_reconcile_watchdog_truth_exposes_canonical_precedence_and_fallback_order() -> None:
    result = watchdog._reconcile_watchdog_truth(findings=[{"kind": "stale_lock"}])

    assert result["overall_state"] == "truth_degraded"
    fact = next(row for row in result["facts"] if row["fact_class"] == "scheduler_execution_state")
    assert fact["canonical_owner"] == "scheduler_state"
    assert fact["observed_sources"] == ["active_job_claim"]
    assert fact["fallback_order"] == [
        "scheduled_job_state",
        "active_job_claim",
        "pipeline_healer_report",
        "operator_snapshot",
    ]


def test_reconcile_watchdog_truth_marks_conflict_as_truth_ambiguous() -> None:
    result = watchdog._reconcile_watchdog_truth(
        findings=[{"kind": "plane_truth_conflict_detected_but_not_auto_repairable"}]
    )

    assert result["overall_state"] == "truth_ambiguous"
    assert result["ambiguous_fact_classes"] == ["plane_truth_projection"]
    fact = next(row for row in result["facts"] if row["fact_class"] == "plane_truth_projection")
    assert fact["state"] == "truth_ambiguous"
    assert fact["ambiguity_reason"] == "canonical_conflict_requires_review"


def test_run_watchdog_audit_reports_nonrepairable_plane_truth_conflict_without_selfheal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(watchdog, "_tick_if_due_and_idle", lambda repo_root: {"ran_tick": False, "due_eligible_count": 0, "active_count": 0})
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(
        watchdog._operator_snapshot,
        "build_operator_snapshot",
        lambda repo_root: {"operator_summary": {"truth_conflicts": [{"kind": "canonical_run_blocked_but_projection_active"}]}},
    )
    monkeypatch.setattr(watchdog._execution_proof, "read_latest_report", lambda repo_root, now_utc="": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False}})

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    assert any(f["kind"] == "plane_truth_conflict_detected_but_not_auto_repairable" for f in result["findings"])
    assert not any(a["action"] == "plane_truth_selfheal" for a in result["actions"])


def test_run_watchdog_truth_ambiguous_records_containment_incident_and_bounded_escalation(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(
        watchdog._operator_snapshot,
        "build_operator_snapshot",
        lambda repo_root: {"operator_summary": {"truth_conflicts": [{"kind": "canonical_run_blocked_but_projection_active"}]}},
    )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    assert result["truth_reconciliation"]["overall_state"] == "truth_ambiguous"
    assert any(f["kind"] == "truth_ambiguous" for f in result["findings"])
    assert result["containments"]
    assert any(item["condition_id"] == "truth_ambiguous" for item in result["containments"])
    assert result["incidents"]
    assert any(item["condition_id"] == "truth_ambiguous" for item in result["incidents"])
    bundle = next(item for item in result["escalation_bundles"] if item["condition_id"] == "truth_ambiguous")
    assert bundle["escalation_class"] == "advisory_codex"
    assert bundle["verification_required"] is True
    assert bundle["watchdog_followup_goal"] == "reduce_repeat_codex_escalation_for_same_failure_type"
    assert any("watchdog improvement" in row for row in bundle["success_bar"])


def test_execute_codex_escalation_bundle_dispatches_via_server_agents_and_writes_receipt(tmp_path: Path, monkeypatch) -> None:
    bundle_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog" / "escalations"
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_root / "wdrun_selfheal_demo__execution_state_disagreement.json"
    bundle = {
        "schema_version": "scheduler_watchdog_codex_escalation_v1",
        "run_id": "wdrun_selfheal_demo",
        "condition_id": "execution_state_disagreement",
        "escalation_class": "bounded_repair_codex",
        "bounded_execution_mode": "bounded_patch",
        "diagnosis": "Scheduled execution state disagrees with live ownership truth.",
        "risk_level": "medium",
        "file_scope": [
            "infra/bin/scheduler_watchdog.py",
            "infra/controlplane/server_maintenance.py",
        ],
        "success_bar": [
            "Keep edits inside the declared file scope.",
            "Produce explicit verification evidence for every claimed repair.",
            "After fixing the immediate issue, add a bounded watchdog improvement for this failure type so the plane is less likely to require Codex next time.",
        ],
        "watchdog_followup_goal": "reduce_repeat_codex_escalation_for_same_failure_type",
        "verification_requirements": {
            "commands": [
                "pytest tests/control_plane/test_scheduler_watchdog.py -q",
            ]
        },
        "evidence": [{"kind": "stale_lock", "details": {"summary": "claim drift"}}],
    }
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    calls: list[dict] = []

    def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "binding": binding, "message": message, "options": dict(options or {})})
        return {
            "ok": True,
            "assistant": {"status": "ok", "content": "bounded repair analysis"},
            "turn": {"turn_id": "turn_watchdog_codex_001"},
        }

    monkeypatch.setattr(watchdog._agents, "send_message", _fake_send_message)

    result = watchdog.execute_codex_escalation_bundle(
        repo_root=tmp_path,
        bundle_path=str(bundle_path),
    )

    assert result["ok"] is True
    assert result["role"] == "builder"
    assert len(calls) == 1
    assert calls[0]["binding"] == {"type": "task_id", "id": "wdg"}
    assert calls[0]["options"]["agent_tech"] == "codex"
    assert "Allowed file scope:" in calls[0]["message"]
    assert "infra/bin/scheduler_watchdog.py" in calls[0]["message"]
    assert "Watchdog follow-up requirement:" in calls[0]["message"]
    assert "reduce future Codex escalation" in calls[0]["message"]
    assert "Verification requirements:" in calls[0]["message"]
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "scheduler_watchdog_codex_execution_receipt_v1"
    assert receipt["condition_id"] == "execution_state_disagreement"
    assert receipt["turn_id"] == "turn_watchdog_codex_001"
    assert receipt["assistant_status"] == "ok"


def test_execute_codex_escalation_bundle_dedupes_when_same_condition_turn_is_still_active(tmp_path: Path, monkeypatch) -> None:
    bundle_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog" / "escalations"
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_root / "wdrun_selfheal_demo__execution_state_disagreement.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduler_watchdog_codex_escalation_v1",
                "run_id": "wdrun_selfheal_demo",
                "condition_id": "execution_state_disagreement",
                "escalation_class": "bounded_repair_codex",
                "bounded_execution_mode": "bounded_patch",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt_root = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "codex_runs"
    receipt_root.mkdir(parents=True, exist_ok=True)
    active_receipt_path = receipt_root / "older_run__execution_state_disagreement.json"
    active_receipt_path.write_text(
        json.dumps(
            {
                "turn_id": "turn_watchdog_live_001",
                "role": "builder",
                "binding": {"type": "task_id", "id": "wdg"},
                "result": {"ok": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        watchdog._lab_turns,
        "read_turn",
        lambda repo_root, *, turn_id: {"turn_id": turn_id, "state": "running"},
    )
    called = {"send": 0}
    monkeypatch.setattr(
        watchdog._agents,
        "send_message",
        lambda **kwargs: called.__setitem__("send", called["send"] + 1) or {"ok": True},
    )

    result = watchdog.execute_codex_escalation_bundle(repo_root=tmp_path, bundle_path=str(bundle_path))

    assert result["ok"] is True
    assert result["deduped"] is True
    assert result["receipt_path"] == str(active_receipt_path)
    assert called["send"] == 0


def test_run_watchdog_escalation_bundle_can_be_executed_through_codex_consumer(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(
        watchdog._operator_snapshot,
        "build_operator_snapshot",
        lambda repo_root: {"operator_summary": {"truth_conflicts": [{"kind": "canonical_run_blocked_but_projection_active"}]}},
    )

    sent: list[dict] = []

    def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        sent.append({"role": role, "binding": binding, "message": message, "options": dict(options or {})})
        return {
            "ok": True,
            "assistant": {"status": "ok", "content": "advisory analysis"},
            "turn": {"turn_id": "turn_watchdog_codex_002"},
        }

    monkeypatch.setattr(watchdog._agents, "send_message", _fake_send_message)

    run = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    bundle = next(item for item in run["escalation_bundles"] if item["condition_id"] == "truth_ambiguous")
    executed = watchdog.execute_codex_escalation_bundle(
        repo_root=tmp_path,
        bundle_path=str(bundle["bundle_path"]),
    )

    assert executed["ok"] is True
    assert executed["role"] == "generalist"
    assert len(sent) == 1
    assert sent[0]["options"]["agent_tech"] == "codex"
    assert "Condition: truth_ambiguous" in sent[0]["message"]
    receipt = json.loads(Path(executed["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["condition_id"] == "truth_ambiguous"
    assert receipt["turn_id"] == "turn_watchdog_codex_002"


def test_run_watchdog_recurrent_root_cause_emits_prediction_without_overriding_present_evidence(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    events_path = watchdog._watchdog_maintenance_events_path(tmp_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    now = watchdog._utc_now()
    for hours_back in (2, 1):
        watchdog._append_jsonl(
            events_path,
            {
                "schema_version": "scheduler_watchdog_maintenance_event_v1",
                "generated_utc": watchdog._utc_iso(now - watchdog.dt.timedelta(hours=hours_back)),
                "run_id": f"seed_{hours_back}",
                "mode": "audit",
                "root_causes": [{"condition_id": "execution_state_disagreement", "risk_level": "medium", "finding_count": 1}],
            },
        )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    prediction = next(item for item in result["predictions"] if item["condition_id"] == "execution_state_disagreement")
    assert prediction["predicted_recurrence"] is True
    assert prediction["priority_influence"] == "boost"
    assert prediction["present_evidence_authority_preserved"] is True
    assert prediction["confidence"] == "high"


def test_run_watchdog_persists_reproducible_incident_and_prediction_indexes(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], []))
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    events_path = watchdog._watchdog_maintenance_events_path(tmp_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    now = watchdog._utc_now()
    for hours_back in (2, 1):
        watchdog._append_jsonl(
            events_path,
            {
                "schema_version": "scheduler_watchdog_maintenance_event_v1",
                "generated_utc": watchdog._utc_iso(now - watchdog.dt.timedelta(hours=hours_back)),
                "run_id": f"seed_{hours_back}",
                "mode": "audit",
                "root_causes": [{"condition_id": "execution_state_disagreement", "risk_level": "medium", "finding_count": 1}],
            },
        )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    incident = next(item for item in result["incidents"] if item["condition_id"] == "execution_state_disagreement")
    prediction = next(item for item in result["predictions"] if item["condition_id"] == "execution_state_disagreement")
    incident_path = Path(incident["artifact_path"])
    prediction_path = Path(prediction["artifact_path"])
    latest_incidents_path = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents" / "latest.json"
    latest_predictions_path = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "predictions" / "latest.json"

    assert incident_path.exists()
    assert prediction_path.exists()
    assert latest_incidents_path.exists()
    assert latest_predictions_path.exists()

    incident_payload = json.loads(incident_path.read_text(encoding="utf-8"))
    prediction_payload = json.loads(prediction_path.read_text(encoding="utf-8"))
    latest_incidents = json.loads(latest_incidents_path.read_text(encoding="utf-8"))
    latest_predictions = json.loads(latest_predictions_path.read_text(encoding="utf-8"))

    assert incident_payload["incident_id"] == "incident__execution_state_disagreement"
    assert incident_payload["occurrence_count"] == 3
    assert incident_payload["maintenance_memory"]["decay_policy"] == {
        "within_24h_weight": 1.0,
        "within_7d_weight": 0.5,
        "older_than_7d_weight": 0.0,
    }
    assert incident_payload["current_truth_state"] == result["truth_reconciliation"]["overall_state"]
    assert prediction_payload["incident_id"] == incident_payload["incident_id"]
    assert prediction_payload["priority_influence"] == "boost"
    assert prediction_payload["present_evidence_authority_preserved"] is True
    assert latest_incidents["open_count"] == 1
    assert latest_predictions["prediction_count"] == 1
    assert latest_incidents["incidents"][0]["artifact_path"] == str(incident_path)
    assert latest_predictions["predictions"][0]["artifact_path"] == str(prediction_path)


def test_watchdog_scope_matches_treats_unscoped_legacy_history_as_global_only() -> None:
    global_scope = watchdog._watchdog_scope()

    assert watchdog._watchdog_scope_matches(global_scope, {}) is True
    assert watchdog._watchdog_scope_matches(global_scope, {"binding": {"type": "task_id", "id": "wdg"}}) is True
    assert watchdog._watchdog_scope_matches(
        {"scope_kind": "global", "scheduler_backend": "v2", "binding": {"type": "task_id", "id": "wdg"}},
        {},
    ) is False
    assert watchdog._watchdog_scope_matches(
        {"scope_kind": "binding", "scheduler_backend": "legacy", "binding": {"type": "task_id", "id": "other"}},
        {},
    ) is False


def test_run_watchdog_marks_repeated_global_concurrency_cap_deferrals_as_execution_state_disagreement(
    tmp_path: Path, monkeypatch
) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "blocked_job_001",
                "enabled": True,
                "state": {
                    "last_status": "deferred",
                    "last_nonlaunch_status": "deferred",
                    "last_nonlaunch_reason": "global_concurrency_cap_reached",
                },
                "due": {"eligible": False, "reason": "global_concurrency_cap_reached"},
            }
        ],
    )

    receipts_root = sched.receipts_dir(tmp_path) / "blocked_job_001"
    receipts_root.mkdir(parents=True, exist_ok=True)
    for idx in range(3):
        (receipts_root / f"receipt_{idx}.json").write_text(
            json.dumps(
                {
                    "schema_version": "scheduled_job_receipt_v1",
                    "job_id": "blocked_job_001",
                    "run_id": f"sj_blocked_{idx}",
                    "status": "deferred",
                    "reason": "global_concurrency_cap_reached",
                    "execution_guard": {
                        "kind": "global_concurrency_cap",
                        "active_job_count": 1,
                        "global_concurrency_cap": 1,
                        "active_jobs": [
                            {
                                "job_id": "active_job_123",
                                "run_id": "sj_active_123",
                                "started_utc": "2026-04-13T18:00:00Z",
                                "heartbeat_utc": "2026-04-13T18:00:30Z",
                                "role": "builder",
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    finding = next(row for row in result["findings"] if row["kind"] == "global_concurrency_cap_starvation")
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "findings_present"
    assert finding["details"]["job_id"] == "blocked_job_001"
    assert finding["details"]["repeat_count"] == 3
    assert finding["details"]["blocking_job_ids"] == ["active_job_123"]
    assert finding["details"]["blocking_jobs"] == [
        {
            "job_id": "active_job_123",
            "run_id": "sj_active_123",
            "started_utc": "2026-04-13T18:00:00Z",
            "heartbeat_utc": "2026-04-13T18:00:30Z",
            "role": "builder",
        }
    ]
    root_cause = next(row for row in result["root_causes"] if row["condition_id"] == "execution_state_disagreement")
    assert root_cause["finding_count"] >= 1


def test_recent_job_failure_signature_findings_suppress_binding_launch_repeat_guarded_jobs(tmp_path: Path) -> None:
    sched.save_job(
        {
            "job_id": "binding_guarded_job",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "missing_task"},
            "prompt": "guarded",
            "schedule": {"mode": "once", "run_at": "2026-05-01T10:00:00Z"},
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "binding_unguarded_job",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "changed_task"},
            "prompt": "unguarded",
            "schedule": {"mode": "once", "run_at": "2026-05-01T10:00:00Z"},
        },
        repo_root=tmp_path,
    )

    for job_id in ("binding_guarded_job", "binding_unguarded_job"):
        receipts_root = sched.receipts_dir(tmp_path) / job_id
        receipts_root.mkdir(parents=True, exist_ok=True)
        for idx in range(3):
            run_id = f"sj_{job_id}_{idx}"
            (receipts_root / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "schema_version": "scheduled_job_receipt_v1",
                        "job_id": job_id,
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

    findings = watchdog._recent_job_failure_signature_findings(
        repo_root=tmp_path,
        jobs=[
            {
                "job_id": "binding_guarded_job",
                "enabled": True,
                "state": {"last_status": "blocked", "last_nonlaunch_reason": "binding_launch_failed:owner_ref unresolved"},
                "due": {"eligible": False, "reason": "binding_launch_failed:owner_ref unresolved"},
            },
            {
                "job_id": "binding_unguarded_job",
                "enabled": True,
                "state": {"last_status": "blocked", "last_nonlaunch_reason": "binding_launch_failed:owner_ref unresolved"},
                "due": {"eligible": False, "reason": "binding_launch_failed:owner_ref unresolved"},
            },
        ],
    )

    assert [row["details"]["job_id"] for row in findings] == ["binding_unguarded_job"]
    assert findings[0]["kind"] == "repeated_job_failure_signature"
    assert findings[0]["details"]["signature"] == "blocked:binding_launch_failed"


def test_execute_codex_escalation_bundle_rejects_unsupported_escalation_class(tmp_path: Path) -> None:
    bundle_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog" / "escalations"
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_root / "bad_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduler_watchdog_codex_escalation_v1",
                "run_id": "wdrun_bad",
                "condition_id": "truth_ambiguous",
                "escalation_class": "unbounded_codex",
                "bounded_execution_mode": "analysis_only",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with contextlib.ExitStack() as stack:
        import pytest

        exc = stack.enter_context(pytest.raises(ValueError, match="unsupported watchdog escalation class"))
        watchdog.execute_codex_escalation_bundle(repo_root=tmp_path, bundle_path=str(bundle_path))
        assert exc is not None


def test_run_watchdog_marks_dependency_blocked_overdue_chain_as_degraded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_p4r6_20260329_impl",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {"eligible": False, "reason": "dependency_not_ok:pis_n3p5_20260329_impl"},
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "dependency_blocked_chain"
    assert any(f["kind"] == "dependency_blocked_chain" for f in result["findings"])


def test_run_watchdog_marks_overdue_dependency_pending_chain_as_degraded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_latest_pipeline_healer", lambda repo_root: {})
    monkeypatch.setattr(watchdog, "_classify_locks", lambda repo_root, now: ([], []))
    monkeypatch.setattr(watchdog, "_find_orphan_running", lambda repo_root: [])
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_p4r6_20260329_impl",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {"eligible": False, "reason": "dependency_pending:pis_n3p5_20260329_impl"},
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "dependency_blocked_chain"
    finding = next(f for f in result["findings"] if f["kind"] == "dependency_blocked_chain")
    assert finding["details"]["dependency_reason"] == "dependency_pending:pis_n3p5_20260329_impl"


def test_run_watchdog_ignores_disabled_dependency_with_terminal_non_ok_status(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_p4r6_20260329_impl",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {
                    "eligible": False,
                    "reason": "dependency_not_ok:pis_n3p5_20260329_impl",
                    "dependency_job_id": "pis_n3p5_20260329_impl",
                    "dependency_last_status": "failed",
                    "dependency_due_reason": "disabled",
                },
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "healthy"
    assert result["health"]["reason"] == "no_current_findings"
    assert not any(f["kind"] == "dependency_blocked_chain" for f in result["findings"])


def test_run_watchdog_ignores_successful_dependency_waiting_only_on_ledger_handoff_truth(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_child_waiting_on_reconcile",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {
                    "eligible": False,
                    "reason": "dependency_pending:pis_dependency_done",
                    "dependency_job_id": "pis_dependency_done",
                    "dependency_last_status": "ok",
                    "dependency_due_reason": "",
                    "dependency_ledger_uid": "PIS-20260329-230000Z-A1B2",
                    "dependency_ledger_status": "draft",
                    "dependency_ledger_missing_reasons": ["ledger_status_draft", "ledger_evidence_missing"],
                },
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "healthy"
    assert result["health"]["reason"] == "no_current_findings"
    assert not any(f["kind"] == "dependency_blocked_chain" for f in result["findings"])


def test_run_watchdog_keeps_dependency_blocked_chain_for_successful_dependency_with_live_relaunch_intent(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_child_waiting_on_reconcile",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {
                    "eligible": False,
                    "reason": "dependency_pending:pis_dependency_done",
                    "dependency_job_id": "pis_dependency_done",
                    "dependency_last_status": "ok",
                    "dependency_due_reason": "",
                    "dependency_ledger_uid": "PIS-20260329-230000Z-A1B2",
                    "dependency_ledger_status": "draft",
                    "dependency_ledger_missing_reasons": ["ledger_status_draft"],
                    "dependency_run_now_requested_utc": "2026-03-29T23:10:00Z",
                },
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "dependency_blocked_chain"
    finding = next(f for f in result["findings"] if f["kind"] == "dependency_blocked_chain")
    assert finding["details"]["dependency_run_now_requested_utc"] == "2026-03-29T23:10:00Z"


def test_run_watchdog_ignores_dependency_blocked_chain_behind_live_active_run(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_child_waiting_on_live_dependency",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {
                    "eligible": False,
                    "reason": "dependency_pending:pis_dependency_live",
                    "dependency_job_id": "pis_dependency_live",
                    "dependency_last_status": "running",
                    "dependency_due_reason": "active_run",
                    "dependency_active_run_id": "sj_live_dependency",
                },
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "healthy"
    assert result["health"]["reason"] == "no_current_findings"
    assert not any(f["kind"] == "dependency_blocked_chain" for f in result["findings"])


def test_run_watchdog_keeps_dependency_blocked_chain_when_active_run_claim_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "pis_child_waiting_on_ambiguous_dependency",
                "enabled": True,
                "schedule": {"mode": "once", "run_at": "2026-03-29T23:00:00Z", "timezone": "UTC"},
                "due": {
                    "eligible": False,
                    "reason": "dependency_pending:pis_dependency_live",
                    "dependency_job_id": "pis_dependency_live",
                    "dependency_last_status": "running",
                    "dependency_due_reason": "active_run",
                    "dependency_active_run_id": "",
                },
                "state": {"last_status": "blocked", "last_nonlaunch_status": "blocked"},
            }
        ],
    )
    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)
    assert result["health"]["health"] == "degraded"
    assert result["health"]["reason"] == "dependency_blocked_chain"
    finding = next(f for f in result["findings"] if f["kind"] == "dependency_blocked_chain")
    assert finding["details"]["dependency_due_reason"] == "active_run"
    assert finding["details"]["dependency_active_run_id"] == ""


def test_scheduler_watchdog_timer_writes_expected_unit_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(timer, "SYSTEMD_USER_DIR", tmp_path / "user")
    monkeypatch.setattr(timer, "REPO_ROOT", Path("/repo"))
    timer.install_units = timer.install_units
    called = []

    def _fake_systemctl(*args: str, check: bool = True):
        called.append(args)
        class _Result:
            stdout = ""
            stderr = ""
            returncode = 0
        return _Result()

    monkeypatch.setattr(timer, "_systemctl", _fake_systemctl)
    timer.install_units()

    audit_timer = (tmp_path / "user" / "causyl-scheduler-watchdog-audit.timer").read_text(encoding="utf-8")
    selfheal_timer = (tmp_path / "user" / "causyl-scheduler-watchdog-selfheal.timer").read_text(encoding="utf-8")
    audit_service = (tmp_path / "user" / "causyl-scheduler-watchdog-audit.service").read_text(encoding="utf-8")
    selfheal_service = (tmp_path / "user" / "causyl-scheduler-watchdog-selfheal.service").read_text(encoding="utf-8")

    assert "OnCalendar=*:0/15" in audit_timer
    assert "OnCalendar=*:7/15" in selfheal_timer
    assert "ExecStart=/repo/tools/bin/scheduler_watchdog_audit.sh" in audit_service
    assert "ExecStart=/repo/tools/bin/scheduler_watchdog_selfheal.sh" in selfheal_service
    assert ("daemon-reload",) in called


def test_run_watchdog_exposes_bounded_workstream_contract(tmp_path: Path, monkeypatch) -> None:
    _stabilize_watchdog_context(monkeypatch, tmp_path)
    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])

    result = watchdog.run_watchdog(mode="audit", repo_root=tmp_path)

    assert result["workstream_contract"]["parent_uid"] == "PIS-20260402-062800Z-U1J7"
    assert result["workstream_contract"]["decision_only_program"] is False


def test_server_resident_watchdog_service_uses_unified_selfheal_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched.stop_watchdog_service(timeout_s=2.0)
    seen: list[str] = []

    def _fake_run_watchdog(*, mode: str, repo_root: Path, allow_pipeline_healer=None):
        seen.append(mode)
        return {"schema_version": "scheduler_watchdog_v3", "mode": mode, "ok": True}

    import tools.bin.scheduler_watchdog as watchdog_bin

    monkeypatch.setattr(watchdog_bin, "run_watchdog", _fake_run_watchdog)
    status = sched.start_watchdog_service(interval_s=900, repo_root=tmp_path)
    deadline = time.time() + 2.0
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    stopped = sched.stop_watchdog_service(timeout_s=2.0)

    assert status["interval_s"] == 900
    assert seen
    assert set(seen) == {"selfheal"}
    assert stopped["running"] is False
