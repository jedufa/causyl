from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as task_register
from infra.controlplane import scheduled_jobs as sched
from tools.bin import scheduler_watchdog as watchdog


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)


def _seed_task_register(root: Path, *, write_generation: int, task_ids: list[str]) -> None:
    host.bootstrap_host_baseline(root)
    register = dict(task_register.load_or_init_task_register(root))
    register["write_generation"] = write_generation
    register["active_tasks"] = [
        {
            "task_id": task_id,
            "kind": "software",
            "title": f"Task {task_id}",
            "status": "queued",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "execute",
            "updated_utc": host._utc_now_iso(),
        }
        for task_id in task_ids
    ]
    task_register._set_generation(register, writer="test.host_baseline.seed")
    task_register._write_json_atomic(task_register._register_path(root), register)
    task_register._write_summary_cache(root, register)


def test_bootstrap_classifies_clean_then_interrupted_restart(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    first = host.bootstrap_host_baseline(tmp_path)
    second = host.bootstrap_host_baseline(tmp_path)

    assert first["mode"] == "clean_boot"
    assert first["status"] == "ok"
    assert second["mode"] == "interrupted_restart"
    assert second["status"] == "ok"


def test_export_does_not_mutate_current_boot_receipt(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    first = host.bootstrap_host_baseline(tmp_path)
    exported = host.export_host_baseline(tmp_path, note="routine export")
    current = host.host_health_snapshot(tmp_path)

    assert exported["ok"] is True
    assert current["mode"] == "clean_boot"
    assert current["last_boot_id"] == first["boot_id"]


def test_bootstrap_classifies_blocked_storage_missing_without_restore(tmp_path: Path) -> None:
    receipt = host.bootstrap_host_baseline(tmp_path)

    assert receipt["mode"] == "blocked_storage_missing"
    assert receipt["status"] == "blocked"
    assert "missing_required:agent_runtime,ledger_state" in ",".join(receipt["reasons"])


def test_bootstrap_classifies_blocked_storage_readonly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    ledger_root = tmp_path / "governance" / "ledger"
    original_mode = ledger_root.stat().st_mode
    os.chmod(ledger_root, 0o555)
    try:
        receipt = host.bootstrap_host_baseline(tmp_path)
    finally:
        os.chmod(ledger_root, original_mode)

    assert receipt["mode"] == "blocked_storage_readonly"
    assert receipt["status"] == "blocked"
    assert "read_only:ledger_state" in ",".join(receipt["reasons"])


def test_export_restore_round_trip_preserves_reserved_assistant_state_and_detects_restored_host(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _init_repo(source_root)
    (source_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": "lab-assistant-v1"}),
        encoding="utf-8",
    )
    source_boot = host.bootstrap_host_baseline(source_root)
    host.mark_clean_shutdown(source_root, reason="test_clean")

    export = host.export_host_baseline(source_root, note="replacement-host drill")
    restored = host.restore_host_baseline(
        manifest_path=Path(str(export["manifest_path"])),
        target_repo_root=target_root,
        note="restore into target",
    )
    target_boot = host.bootstrap_host_baseline(target_root)
    target_identity = json.loads(
        (target_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").read_text(
            encoding="utf-8"
        )
    )

    assert source_boot["install_id"]
    assert restored["partial_restore"] is False
    assert target_boot["mode"] == "restored_host"
    assert target_boot["install_id"] == source_boot["install_id"]
    assert target_identity["assistant_id"] == "lab-assistant-v1"


def test_scheduler_truth_snapshot_fails_closed_on_phantom_active_run_block_and_runtime_sickness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda repo_root=None, now_utc=None: [
            {
                "job_id": "job_waiting_on_ghost",
                "state": {"active_run_id": "", "active_claim_present": False},
                "due": {"reason": "active_run"},
            }
        ],
    )
    monkeypatch.setattr(
        host,
        "_utc_now_iso",
        lambda: "2026-04-13T15:00:00Z",
    )

    monkeypatch.setattr(
        watchdog,
        "run_watchdog",
        lambda *, repo_root=None, mode="observe": {
            "health": {"health": "degraded", "reason": "runtime_storage_health"},
            "findings": [{"kind": "runtime_storage_critical"}],
        },
    )

    truth = host._scheduler_truth_snapshot(tmp_path)

    assert truth["coherent"] is False
    assert truth["blocked_by_active_run_job_ids"] == ["job_waiting_on_ghost"]
    assert "runtime_storage_critical" in truth["watchdog_finding_kinds"]


def test_scheduler_truth_snapshot_fails_closed_on_orphan_scheduler_binding_after_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host.bootstrap_host_baseline(tmp_path)
    register = task_register.load_or_init_task_register(tmp_path)
    register["active_tasks"] = [
        {
            "task_id": "task_remote_restore",
            "kind": "software",
            "title": "Remote restore ownership drift",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "reconcile",
            "updated_utc": host._utc_now_iso(),
            "execution_surface_ownership": {
                "schema_version": task_register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                "selected_surface": "scheduler",
                "execution_id": "remote_job_001",
                "execution_ref": "scheduler:remote_job_001",
                "binding_ref": "execution_queue:remote_job_001",
                "job_uri": "governance/agents/runtime/execution_queue/state.json",
                "trace_id": "trace_remote_001",
                "reference_status": "execution_ref_active",
                "reference_status_reason": "restored_from_other_host",
                "reference_revision": 1,
                "owner_uid": task_register._EXECUTION_OWNER_UID,
            },
        }
    ]
    task_register._set_generation(register, writer="host.restore.orphan.seed")
    task_register._write_json_atomic(task_register._register_path(tmp_path), register)
    task_register._write_summary_cache(tmp_path, register)

    monkeypatch.setattr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [])
    monkeypatch.setattr(
        watchdog,
        "run_watchdog",
        lambda *, repo_root=None, mode="observe": {"health": {"health": "healthy", "reason": "healthy"}, "findings": []},
    )

    truth = host._scheduler_truth_snapshot(tmp_path)

    assert truth["coherent"] is False
    assert truth["orphan_scheduler_binding_task_ids"] == ["task_remote_restore"]


def test_bootstrap_classifies_partial_restore_degraded_when_required_surface_is_missing_after_restore(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _init_repo(source_root)
    host.bootstrap_host_baseline(source_root)
    host.mark_clean_shutdown(source_root, reason="test_clean")

    export = host.export_host_baseline(source_root, note="partial restore drill")
    host.restore_host_baseline(
        manifest_path=Path(str(export["manifest_path"])),
        target_repo_root=target_root,
        note="restore into target",
    )
    shutil_target = target_root / "governance" / "ledger"
    if shutil_target.exists():
        for child in sorted(shutil_target.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        shutil_target.rmdir()

    receipt = host.bootstrap_host_baseline(target_root)

    assert receipt["mode"] == "partial_restore_degraded"
    assert receipt["status"] == "degraded"


def test_health_snapshot_reprobes_current_surface_loss(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    ledger_root = tmp_path / "governance" / "ledger"
    ledger_root.rmdir()

    health = host.host_health_snapshot(tmp_path)

    assert health["mode"] == "blocked_storage_missing"
    assert health["status"] == "blocked"


def test_restore_rejects_nonempty_target_and_tampered_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _init_repo(source_root)
    host.bootstrap_host_baseline(source_root)
    host.mark_clean_shutdown(source_root, reason="test_clean")
    export = host.export_host_baseline(source_root, note="strict restore")
    manifest_path = Path(str(export["manifest_path"]))

    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "stale.txt").write_text("stale", encoding="utf-8")
    with pytest.raises(ValueError, match="absent or empty"):
        host.restore_host_baseline(
            manifest_path=manifest_path,
            target_repo_root=target_root,
            note="should reject nonempty target",
        )
    assert list(tmp_path.glob(".lab_assistant_restore_stage_*")) == []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_sha256"] = "deadbeef"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    clean_target = tmp_path / "clean_target"
    with pytest.raises(ValueError, match="manifest_sha256 mismatch"):
        host.restore_host_baseline(
            manifest_path=manifest_path,
            target_repo_root=clean_target,
            note="tampered manifest",
        )


def test_restore_receipt_surfaces_backup_age_and_durable_truth_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _init_repo(source_root)
    (source_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": "lab-assistant-v1"}),
        encoding="utf-8",
    )
    _seed_task_register(source_root, write_generation=7, task_ids=["task-a"])
    seeded_register = task_register.read_task_register_snapshot(source_root, initialize_if_missing=False)["task_register"]
    host.bootstrap_host_baseline(source_root)
    export = host.export_host_baseline(source_root, note="backup age drill")
    restored = host.restore_host_baseline(
        manifest_path=Path(str(export["manifest_path"])),
        target_repo_root=target_root,
        note="restore target",
    )

    receipt = json.loads((target_root / "runtime" / "state" / "lab_assistant" / "host" / "restore_receipt.json").read_text(encoding="utf-8"))
    health = host.host_health_snapshot(target_root, initialize_if_missing=False)

    assert restored["source_backup_age_s"] is not None
    assert receipt["source_backup_age_s"] is not None
    assert receipt["source_durable_truth_snapshot"]["assistant_id"] == "lab-assistant-v1"
    assert receipt["source_durable_truth_snapshot"]["task_register_write_generation"] == int(
        seeded_register["write_generation"]
    )
    assert health["restore_context"]["source_backup_age_s"] is not None
    assert health["restore_context"]["source_durable_truth_snapshot"]["assistant_id"] == "lab-assistant-v1"


def test_run_backup_restore_host_replacement_drill_preserves_durable_truth(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _init_repo(source_root)
    (source_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": "lab-assistant-v1"}),
        encoding="utf-8",
    )
    _seed_task_register(source_root, write_generation=9, task_ids=["task-a", "task-b"])
    host.bootstrap_host_baseline(source_root)
    host.mark_clean_shutdown(source_root, reason="before drill")

    report = host.run_backup_restore_host_replacement_drill(source_root, note="replacement-host drill")

    assert report["status"] == "pass"
    assert report["mismatches"] == []
    assert report["target_boot"]["mode"] == "restored_host"
    assert report["source_durable_truth_snapshot"]["task_register_sha256"] == report["target_durable_truth_snapshot"]["task_register_sha256"]


def test_run_backup_restore_host_replacement_drill_reconciles_inherited_live_scheduler_truth(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _init_repo(source_root)
    (source_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": "lab-assistant-v1"}),
        encoding="utf-8",
    )
    host.bootstrap_host_baseline(source_root)

    from infra.controlplane import scheduled_jobs as sched

    sched.save_job(
        {
            "job_id": "replacement_host_live_claim_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "task_continuity_001"},
            "prompt": "Replacement host continuity validation job.",
            "schedule": {"mode": "once", "run_at": "2026-04-13T00:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "decision_memo", "format": "markdown"},
            "policy": {"max_runtime_seconds": 1800},
        },
        repo_root=source_root,
    )
    sched._write_state(
        "replacement_host_live_claim_001",
        {
            "active_run_id": "sj_replacement_host_live_claim_001",
            "last_status": "running",
            "last_started_utc": "2026-04-13T05:00:00Z",
        },
        repo_root=source_root,
    )
    claim_path = sched._claim_path("replacement_host_live_claim_001", repo_root=source_root)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "job_id": "replacement_host_live_claim_001",
                "run_id": "sj_replacement_host_live_claim_001",
                "claimed_utc": "2026-04-13T05:00:00Z",
                "heartbeat_utc": "2026-04-13T05:00:10Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = host.run_backup_restore_host_replacement_drill(source_root, note="replacement-host drill")

    assert report["status"] == "pass"
    assert report["pre_reconcile_scheduler_truth"]["coherent"] is False
    assert report["post_reconcile_scheduler_truth"]["coherent"] is True
    assert report["reconciliation_required"] is True
    assert report["scheduler_reconciliation"]["claim_audit"]["cleared_count"] == 1


def test_scheduler_truth_snapshot_is_not_coherent_when_task_register_is_unreadable(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    (
        tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    ).write_text(json.dumps({"tasks": []}) + "\n", encoding="utf-8")

    truth = host._scheduler_truth_snapshot(tmp_path)

    assert truth["coherent"] is False
    assert "task_register_snapshot_unreadable" in truth["watchdog_finding_kinds"]


def test_scheduler_truth_snapshot_is_not_coherent_when_scheduler_projection_is_ambiguous(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    sched.save_job(
        {
            "job_id": "host_ambiguous_restore_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "host_ambiguous_restore_task"},
            "prompt": "noop",
            "schedule": {"mode": "once", "run_at": "2026-04-13T10:00:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "host_ambiguous_restore_001",
        {
            "job_id": "host_ambiguous_restore_001",
            "active_run_id": "",
            "last_status": "running",
            "updated_utc": host._utc_now_iso(),
        },
        repo_root=tmp_path,
    )

    truth = host._scheduler_truth_snapshot(tmp_path)

    assert truth["coherent"] is False
    assert truth["ambiguous_projection_job_ids"] == ["host_ambiguous_restore_001"]
