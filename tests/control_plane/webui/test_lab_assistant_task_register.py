from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_routing_policy as routing
from infra.webui import server_lab_assistant_operator_snapshot as snapshot


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in register_obj.get(bucket_name, []):
            if task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def test_task_register_survives_reload_with_active_tasks_and_open_loops(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    first = register.load_or_init_task_register(tmp_path)
    res = register.upsert_task(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="test.writer",
        task={
            "task_id": "task_bd05_software",
            "kind": "software",
            "title": "Implement BD05 register",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-161622Z-BD05",
            "next_expected_action": "add tests",
        },
    )
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(res["task_register"]["write_generation"]),
        writer="test.writer",
        open_loop={
            "loop_id": "loop_wait_verify",
            "kind": "waiting_on_verification",
            "status": "open",
            "linked_task_id": "task_bd05_software",
            "note": "await focused verification",
        },
    )

    second = register.load_or_init_task_register(tmp_path)

    assert second["register_id"] == first["register_id"]
    assert [task["task_id"] for task in second["active_tasks"]] == ["task_bd05_software"]
    assert [loop["loop_id"] for loop in second["open_loops"]] == ["loop_wait_verify"]


def test_task_register_mutations_are_append_only_and_reject_stale_writes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    res = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="task.writer",
        task={
            "task_id": "task_materials",
            "kind": "materials",
            "title": "Review coupon fabrication results",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "compare stress curves",
        },
    )

    assert res["changed"] is True
    with pytest.raises(register.TaskRegisterError, match="stale write rejected"):
        register.set_constraint(
            tmp_path,
            expected_generation=int(current["write_generation"]),
            writer="stale.writer",
            constraint={"key": "proof_requirement", "value": "host verifier before merge"},
        )

    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert any("task_register_initialized" in line for line in events)
    assert any("task_upserted" in line for line in events)
    assert any("task_register_write_rejected" in line for line in events)


def test_retire_task_removes_active_row_and_resolves_linked_open_loops(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    after_task = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="task.writer",
        task={
            "task_id": "task_retire_me",
            "kind": "software",
            "title": "Retire failed ad hoc audit",
            "status": "failed",
            "owner": "assistant",
            "source": "scheduler",
            "next_expected_action": "decide whether to relaunch",
        },
    )["task_register"]
    after_loop = register.upsert_open_loop(
        tmp_path,
        expected_generation=int(after_task["write_generation"]),
        writer="task.writer",
        open_loop={
            "loop_id": "writeback::task_retire_me::blocked_question",
            "kind": "blocked_question",
            "status": "open",
            "linked_task_id": "task_retire_me",
            "note": "failed run still needs operator decision",
        },
    )["task_register"]

    retired = register.retire_task(
        tmp_path,
        expected_generation=int(after_loop["write_generation"]),
        writer="task.writer",
        task_id="task_retire_me",
        resolution_note="retired after failed ad hoc audit",
    )["task_register"]

    assert all(task["task_id"] != "task_retire_me" for task in retired["active_tasks"])
    loop = next(loop for loop in retired["open_loops"] if loop["loop_id"] == "writeback::task_retire_me::blocked_question")
    assert loop["status"] == "resolved"
    assert loop["resolved_utc"]
    assert loop["note"] == "retired after failed ad hoc audit"


def test_task_upsert_defaults_intake_path_to_compatibility_shim(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    upserted = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="compat.writer",
        task={
            "task_id": "task_compat_path",
            "kind": "software",
            "title": "Compatibility shim write",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record compatibility path",
        },
    )["task_register"]
    task = next(task for task in upserted["active_tasks"] if task["task_id"] == "task_compat_path")

    assert task["intake_path"] == "compatibility_shim"


def test_task_upsert_preserves_existing_intake_path_on_partial_update(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    first = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="compat.writer",
        task={
            "task_id": "task_compat_preserve",
            "kind": "software",
            "title": "Compatibility path preserved",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "intake_path": "compatibility_shim",
            "next_expected_action": "set explicit path",
        },
    )["task_register"]
    second = register.upsert_task(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="compat.writer",
        task={
            "task_id": "task_compat_preserve",
            "kind": "software",
            "title": "Compatibility path preserved",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "preserve path",
        },
    )["task_register"]

    task = next(task for task in second["active_tasks"] if task["task_id"] == "task_compat_preserve")

    assert task["intake_path"] == "compatibility_shim"


def test_task_upsert_rejects_forbidden_intake_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    with pytest.raises(register.TaskRegisterError, match="intake_path=forbidden writes are blocked"):
        register.upsert_task(
            tmp_path,
            expected_generation=int(current["write_generation"]),
            writer="compat.writer",
            task={
                "task_id": "task_forbidden",
                "kind": "software",
                "title": "Reject forbidden boundary",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "intake_path": "forbidden",
                "next_expected_action": "should fail",
            },
        )


def test_scheduler_binding_summary_treats_swarm_as_scheduler_like_surface() -> None:
    summary = register.scheduler_binding_summary(
        {
            "delegation_refs": [{"surface": "swarm", "subsystem_id": "swarm_job_001"}],
            "execution_surface_ownership": {
                "selected_surface": "swarm",
                "reference_status": "execution_ref_active",
                "execution_id": "swarm_job_001",
            },
            "task_runs": [
                {
                    "run_id": "swarm_run_001",
                    "surface": "swarm",
                    "run_state": "running",
                    "execution_id": "swarm_job_001",
                }
            ],
            "current_run_id": "swarm_run_001",
        }
    )

    assert summary["has_scheduler_binding"] is True
    assert summary["has_live_scheduler_binding"] is True
    assert summary["has_only_scheduler_surfaces"] is True
    assert summary["scheduler_surfaces"] == ["swarm"]
    assert summary["binding_sources"] == ["delegation_refs", "execution_surface_ownership", "task_runs"]
    assert summary["live_binding_sources"] == ["delegation_refs", "execution_surface_ownership", "task_runs"]
    assert summary["job_ids"] == ["swarm_job_001"]


def test_scheduler_binding_summary_keeps_stale_scheduler_like_bindings_non_live() -> None:
    summary = register.scheduler_binding_summary(
        {
            "execution_surface_ownership": {
                "selected_surface": "scheduler",
                "reference_status": "execution_ref_stale",
                "execution_id": "scheduler_job_001",
            },
            "task_runs": [
                {
                    "run_id": "scheduler_run_001",
                    "surface": "scheduler",
                    "run_state": "stale",
                    "execution_id": "scheduler_job_001",
                }
            ],
            "current_run_id": "scheduler_run_001",
        }
    )

    assert summary["has_scheduler_binding"] is True
    assert summary["has_live_scheduler_binding"] is False
    assert summary["has_only_scheduler_surfaces"] is True
    assert summary["scheduler_surfaces"] == ["scheduler"]
    assert summary["binding_sources"] == ["execution_surface_ownership", "task_runs"]
    assert summary["live_binding_sources"] == []
    assert summary["job_ids"] == ["scheduler_job_001"]


def test_task_register_load_accepts_legacy_rows_missing_intake_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    legacy_register = dict(current)
    legacy_register["active_tasks"] = [
        {
            "task_id": "legacy_task_no_intake_path",
            "kind": "software",
            "title": "Legacy intake row",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "resume work",
            "delegation_refs": [],
        }
    ]

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.write_text(json.dumps(legacy_register), encoding="utf-8")
    if summary_path.exists():
        summary_path.unlink()

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(task for task in reloaded["active_tasks"] if task["task_id"] == "legacy_task_no_intake_path")

    assert task["intake_path"] == "compatibility_shim"


def test_task_register_replay_accepts_legacy_rows_missing_intake_path_in_committed_event(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    legacy_register = dict(current)
    legacy_register["active_tasks"] = [
        {
            "task_id": "legacy_task_no_intake_path",
            "kind": "software",
            "title": "Legacy intake row",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "resume work",
            "delegation_refs": [],
        }
    ]

    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    base_event = {
        "schema_version": register.TASK_REGISTER_EVENT_SCHEMA_VERSION,
        "event_id": "legacy-event-init",
        "event": "task_register_initialized",
        "utc": "2026-03-27T00:00:00Z",
        "payload": {"register": dict(legacy_register)},
    }
    committed_event = {
        "schema_version": register.TASK_REGISTER_EVENT_SCHEMA_VERSION,
        "event_id": "legacy-event-commit",
        "event": "task_register_committed",
        "utc": "2026-03-27T00:00:01Z",
        "payload": {"register": dict(legacy_register), "summary_cache": {}},
    }
    events_path.write_text("\n".join([json.dumps(base_event, sort_keys=True), json.dumps(committed_event, sort_keys=True)]) + "\n", encoding="utf-8")

    replayed = register.replay_task_register_events(tmp_path)
    task = next(task for task in replayed["active_tasks"] if task["task_id"] == "legacy_task_no_intake_path")

    assert task["intake_path"] == "compatibility_shim"


def test_summary_regenerates_after_cache_deletion(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="summary.writer",
        task={
            "task_id": "task_lab_ops",
            "kind": "lab_ops",
            "title": "Check scheduler residue",
            "status": "waiting",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "inspect failed scheduled job",
        },
    )["task_register"]
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    summary_path.unlink()

    rebuilt = register.rebuild_summary_cache(tmp_path)
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)

    assert rebuilt["summary_cache"]["active_task_count"] == 1
    assert summary_path.exists()
    assert snapshot["summary_cache"]["register_generation"] == int(updated["write_generation"])
    assert snapshot["task_register"]["summary_stale"] is False


def test_summary_suppresses_internal_helper_residue_and_linked_open_loops(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="summary.helper",
        task={
            "task_id": "task_internal_helper",
            "kind": "software",
            "title": "[Codex Scheduled Controller Contract] - You are the Codex executor of record for a scheduled write-capable job.",
            "status": "waiting_on_job",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "Wait for helper completion.",
        },
    )["task_register"]
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(updated["write_generation"]),
        writer="summary.helper",
        open_loop={
            "loop_id": "loop_internal_helper_wait",
            "kind": "waiting_on_verification",
            "status": "open",
            "linked_task_id": "task_internal_helper",
            "note": "Internal helper still waiting on proof.",
        },
    )

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    summary = snapshot["summary_cache"]

    assert summary["active_task_count"] == 0
    assert summary["open_loop_count"] == 0
    assert summary["suppressed_internal_helper_task_count"] == 1
    assert summary["suppressed_internal_helper_open_loop_count"] == 1
    assert all("Codex Scheduled Controller Contract" not in line for line in summary["lines"])


def test_mixed_surface_scheduler_residue_cleanup_preserves_non_scheduler_run(tmp_path: Path) -> None:
    task = {
        "task_id": "task_mixed_residue",
        "status": "completed",
        "updated_utc": "2026-05-02T00:00:00Z",
        "execution_surface_ownership": {
            "selected_surface": "scheduler",
            "reference_status": "execution_ref_stale",
            "execution_id": "verify_task_mixed_residue",
        },
        "task_runs": [
            {
                "run_id": "turn_task_mixed_residue",
                "surface": "codex_worker",
                "run_state": "completed",
                "execution_id": "turn_task_mixed_residue",
            },
            {
                "run_id": "verify_task_mixed_residue",
                "surface": "scheduler",
                "run_state": "stale",
                "execution_id": "verify_task_mixed_residue",
            },
        ],
        "current_run_id": "verify_task_mixed_residue",
    }

    changed = recovery._clear_task_scheduler_binding_residue(
        task,
        writer="test.scheduler_residue",
        live_job_ids=set(),
    )

    assert changed is True
    summary = register.scheduler_binding_summary(task)
    assert summary["has_scheduler_binding"] is False
    assert task["task_runs"] == [
        {
            "run_id": "turn_task_mixed_residue",
            "surface": "codex_worker",
            "run_state": "completed",
            "execution_id": "turn_task_mixed_residue",
        }
    ]
    assert task["current_run_id"] == "turn_task_mixed_residue"
    assert "execution_surface_ownership" not in task


def test_task_register_replay_rebuilds_snapshot_from_event_log(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    after_task = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="replay.writer",
        task={
            "task_id": "task_experiment",
            "kind": "experiment",
            "title": "Run thermal soak assay",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "schedule incubator slot",
        },
    )["task_register"]
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(after_task["write_generation"]),
        writer="replay.writer",
        open_loop={
            "loop_id": "loop_operator_decision",
            "kind": "waiting_on_operator",
            "status": "open",
            "linked_task_id": "task_experiment",
            "note": "confirm acceptable soak window",
        },
    )
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink()
    summary_path.unlink()

    replayed = register.load_or_init_task_register(tmp_path)

    assert [task["task_id"] for task in replayed["active_tasks"]] == ["task_experiment"]
    assert [loop["loop_id"] for loop in replayed["open_loops"]] == ["loop_operator_decision"]
    assert summary_path.exists()


def test_task_register_replays_latest_committed_row_after_snapshot_write_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    original_write = register._write_json_atomic

    def _crash_after_commit(path: Path, obj: dict) -> None:
        if Path(path).resolve() == register_path.resolve():
            events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
                encoding="utf-8"
            )
            if "task_register_committed" in events and int(obj.get("write_generation") or 0) == int(current["write_generation"]) + 1:
                raise RuntimeError("simulated crash before snapshot rewrite")
        original_write(path, obj)

    monkeypatch.setattr(register, "_write_json_atomic", _crash_after_commit)

    with pytest.raises(RuntimeError, match="simulated crash before snapshot rewrite"):
        register.upsert_task(
            tmp_path,
            expected_generation=int(current["write_generation"]),
            writer="crash.writer",
            task={
                "task_id": "task_crash_window",
                "kind": "software",
                "title": "Survive snapshot write crash",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "next_expected_action": "replay committed row",
            },
        )

    monkeypatch.setattr(register, "_write_json_atomic", original_write)
    register_path.unlink()
    if summary_path.exists():
        summary_path.unlink()

    replayed = register.load_or_init_task_register(tmp_path)

    assert [task["task_id"] for task in replayed["active_tasks"]] == ["task_crash_window"]
    assert summary_path.exists()


def test_task_register_replay_fails_closed_on_malformed_event_log(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="corrupt.writer",
        task={
            "task_id": "task_corrupt",
            "kind": "software",
            "title": "Keep replay strict",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "fail closed on malformed log",
        },
    )
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events_path.write_text(lines[0] + "\n{bad json\n", encoding="utf-8")

    with pytest.raises(register.TaskRegisterError, match="task_register_events.jsonl is malformed"):
        register.replay_task_register_events(tmp_path)


def test_task_register_snapshot_skips_full_integrity_scan_for_oversized_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="oversized.writer",
        task={
            "task_id": "task_oversized_log",
            "kind": "software",
            "title": "Avoid full startup integrity scan",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "bound validation memory",
        },
    )
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    payload = events_path.read_text(encoding="utf-8")
    oversized = payload + (" " * 8192)
    events_path.write_text(oversized, encoding="utf-8")

    monkeypatch.setenv("EIEL_TASK_REGISTER_EVENT_LOG_VALIDATION_MAX_BYTES", "128")

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    integrity = snapshot["integrity"]["event_log"]

    assert integrity["status"] == "ok"
    assert integrity["validation_mode"] == "size_capped"
    assert integrity["validation_skipped"] is True
    assert "strict validation deferred for oversized event log" in integrity["detail"]


def test_task_register_auto_compacts_oversized_event_log_and_keeps_replay_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    monkeypatch.setenv("EIEL_TASK_REGISTER_EVENT_LOG_COMPACT_MAX_BYTES", "20000")
    current = register.load_or_init_task_register(tmp_path)

    first = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="compact.writer",
        task={
            "task_id": "task_auto_compact",
            "kind": "software",
            "title": "X" * 12000,
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "grow event log enough to compact",
        },
    )["task_register"]
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="compact.writer",
        open_loop={
            "loop_id": "loop_auto_compact",
            "kind": "waiting_on_operator",
            "status": "open",
            "linked_task_id": "task_auto_compact",
            "note": "Y" * 12000,
        },
    )

    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    archive_dir = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "archive"
    archive_files = sorted(archive_dir.glob("task_register_events__*.jsonl.gz"))

    assert archive_files
    events_text = events_path.read_text(encoding="utf-8")
    assert "task_register_event_log_compacted" in events_text

    replayed = register.replay_task_register_events(tmp_path)
    assert any(task["task_id"] == "task_auto_compact" for task in replayed["active_tasks"])
    assert any(loop["loop_id"] == "loop_auto_compact" for loop in replayed["open_loops"])


def test_task_register_auto_compaction_prunes_old_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    monkeypatch.setenv("EIEL_TASK_REGISTER_EVENT_LOG_COMPACT_MAX_BYTES", "20000")
    monkeypatch.setenv("EIEL_TASK_REGISTER_EVENT_LOG_ARCHIVE_RETENTION_DAYS", "7")

    archive_dir = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stale_archive = archive_dir / "task_register_events__19990101T000000Z.jsonl.gz"
    stale_archive.write_bytes(b"old archive")
    stale_archive.touch()
    old_ts = 946684800
    os.utime(stale_archive, (old_ts, old_ts))

    current = register.load_or_init_task_register(tmp_path)
    first = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="compact.writer",
        task={
            "task_id": "task_archive_prune",
            "kind": "software",
            "title": "Z" * 12000,
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "trigger compaction and prune old archive",
        },
    )["task_register"]
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="compact.writer",
        open_loop={
            "loop_id": "loop_archive_prune",
            "kind": "waiting_on_operator",
            "status": "open",
            "linked_task_id": "task_archive_prune",
            "note": "W" * 12000,
        },
    )

    assert not stale_archive.exists()


def test_task_register_stores_routing_decision_on_upsert(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    decision = routing.decide_short_or_delegated_route(
        {
            "summary": "Direct answer for a short request",
            "estimated_duration_s": 20,
            "preferred_surface": "",
        }
    )
    upserted = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="routing.writer",
        task={
            "task_id": "task_routing_decision",
            "kind": "software",
            "title": "Persist routing decision",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "persist routed contract",
            "routing_decision": decision,
        },
    )["task_register"]

    stored = next(task for task in upserted["active_tasks"] if task["task_id"] == "task_routing_decision")
    assert stored["routing_decision"]["decision_outcome"] == "direct"
    assert stored["routing_decision"]["status"] == "accepted"
    assert stored["routing_decision"]["decision_revision"] == decision["decision_revision"]
    assert stored["routing_decision"]["execution_surface_ownership"]["owner_uid"] == routing.ROUTING_DECISION_OWNER_UID
    assert "constraint_flags" in stored["routing_decision"]
    assert stored["routing_decision"]["request_factors"] == decision["request_factors"]


def test_task_register_rejects_stale_routing_decision_update_on_existing_task(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    decision = routing.decide_short_or_delegated_route(
        {
            "summary": "Short direct answer request",
            "estimated_duration_s": 20,
            "preferred_surface": "",
        }
    )
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="routing.writer",
        task={
            "task_id": "task_routing_conflict",
            "kind": "software",
            "title": "Reject stale routing update",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "defend merge safety",
            "routing_decision": decision,
        },
    )["task_register"]

    stale = dict(decision)
    stale["decision_revision"] = int(decision["decision_revision"]) - 1
    with pytest.raises(register.TaskRegisterError, match="stale routing decision rejected"):
        register.upsert_task(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="routing.writer",
            task={
                "task_id": "task_routing_conflict",
                "kind": "software",
                "title": "Reject stale routing update",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "next_expected_action": "defend merge safety",
                "routing_decision": stale,
            },
        )

    reloaded = register.load_or_init_task_register(tmp_path)
    stored = next(task for task in reloaded["active_tasks"] if task["task_id"] == "task_routing_conflict")
    assert stored["routing_decision"]["decision_revision"] == decision["decision_revision"]


def test_task_execution_surface_ownership_records_required_refs_and_revision(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execowner.writer",
        task={
            "task_id": "task_exec_owner_positive",
            "kind": "software",
            "title": "Track execution surface ownership",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260326-203500Z-A7D1",
            "next_expected_action": "persist execution refs",
        },
    )["task_register"]
    updated = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_positive",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_001",
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_exec_owner_positive")
    assert task["execution_surface_ownership"]["selected_surface"] == "pipeline"
    assert task["execution_surface_ownership"]["execution_id"] == "run_001"
    assert task["execution_surface_ownership"]["execution_ref"] == "subsystem_pipeline:run_001"
    assert task["execution_surface_ownership"]["binding_ref"] == "binding_001"
    assert task["execution_surface_ownership"]["job_uri"].endswith("pipeline/state.json")
    assert task["execution_surface_ownership"]["trace_id"] == "trace_pipeline_001"
    assert task["execution_surface_ownership"]["reference_status"] == "execution_ref_active"
    assert task["execution_surface_ownership"]["reference_revision"] == 1
    assert task["execution_surface_ownership"]["owner_uid"] == register._EXECUTION_OWNER_UID
    events = (
        tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    ).read_text(encoding="utf-8")
    assert "task_execution_surface_ownership_recorded" in events


def test_task_execution_surface_ownership_marks_missing_refs_as_unresolved(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execowner.writer",
        task={
            "task_id": "task_exec_owner_missing",
            "kind": "software",
            "title": "Detect unresolved execution refs",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "preserve unresolved state",
        },
    )["task_register"]
    updated = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_missing",
        selected_surface="scheduler",
        execution_id="",
        execution_ref="",
        binding_ref="",
        job_uri="",
        trace_id="",
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_exec_owner_missing")
    assert task["execution_surface_ownership"]["reference_status"] == "execution_ref_unresolved"
    assert task["execution_surface_ownership"]["reference_revision"] == 1


def test_task_execution_surface_ownership_writes_run_record_and_preserves_current_run_id(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execowner.writer",
        task={
            "task_id": "task_exec_owner_run_record",
            "kind": "software",
            "title": "Keep canonical run record",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "write run rows",
        },
    )["task_register"]
    first = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_run_record",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_001",
    )["task_register"]
    task = next(task for task in first["active_tasks"] if task["task_id"] == "task_exec_owner_run_record")
    assert task.get("task_runs")
    assert task.get("current_run_id") == "run_001"
    assert task["execution_surface_ownership"]["reference_status"] == "execution_ref_active"
    assert int(task["execution_surface_ownership"]["reference_revision"]) == 1

    rerun = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_run_record",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_001",
    )["task_register"]
    rerun_task = next(task for task in rerun["active_tasks"] if task["task_id"] == "task_exec_owner_run_record")
    run_rows = list(rerun_task.get("task_runs") or [])
    assert len(run_rows) == 1
    assert run_rows[0]["run_id"] == "run_001"
    assert run_rows[0]["run_state"] == "running"
    assert run_rows[0]["revision"] == 2
    assert run_rows[0]["attention_state"] == "monitoring"
    assert rerun_task["current_run_id"] == "run_001"


def test_task_upsert_preserves_task_runs_and_current_run_id_when_omitted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="runs.omit.writer",
        task={
            "task_id": "task_exec_owner_omit",
            "kind": "software",
            "title": "Preserve run rows on partial upsert",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "keep row",
        },
    )["task_register"]
    after_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="runs.omit.writer",
        task_id="task_exec_owner_omit",
        selected_surface="pipeline",
        execution_id="run_omit_001",
        execution_ref="subsystem_pipeline:run_omit_001",
        binding_ref="binding_omit_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_omit_001",
    )["task_register"]
    with_run = next(task for task in after_run["active_tasks"] if task["task_id"] == "task_exec_owner_omit")
    assert with_run.get("task_runs")
    assert with_run.get("current_run_id")
    assert with_run["execution_surface_ownership"]["execution_id"] == "run_omit_001"

    rebuilt = register.upsert_task(
        tmp_path,
        expected_generation=int(after_run["write_generation"]),
        writer="runs.omit.writer",
        task={
            "task_id": "task_exec_owner_omit",
            "kind": "software",
            "title": "Preserve run rows on partial upsert",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "change title",
        },
    )["task_register"]
    final_task = next(task for task in rebuilt["active_tasks"] if task["task_id"] == "task_exec_owner_omit")
    assert final_task.get("current_run_id") == with_run["current_run_id"]
    assert final_task.get("task_runs")
    assert len(final_task["task_runs"]) == len(with_run["task_runs"])


def test_task_run_checkpoint_records_resume_decision(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="checkpoint.writer",
        task={
            "task_id": "task_checkpoint_resume",
            "kind": "software",
            "title": "Checkpoint resume decision",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record checkpoint",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="checkpoint.writer",
        task_id="task_checkpoint_resume",
        selected_surface="pipeline",
        execution_id="run_checkpoint_001",
        execution_ref="subsystem_pipeline:run_checkpoint_001",
        binding_ref="binding_checkpoint_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_checkpoint_001",
    )["task_register"]
    updated = register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="checkpoint.writer",
        task_id="task_checkpoint_resume",
        run_id="run_checkpoint_001",
        checkpoint={
            "checkpoint_id": "cp_001",
            "checkpoint_status": "recorded",
            "checkpoint_note": "Safe checkpoint reached.",
            "progress_percent": 45.0,
            "progress_summary": "Planning complete, execution mid-way.",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_001.json"],
        },
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_checkpoint_resume")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_checkpoint_001")
    assert run_row["checkpoint"]["checkpoint_id"] == "cp_001"
    assert run_row["checkpoint"]["checkpoint_status"] == "recorded"
    assert run_row["checkpoint"]["progress_percent"] == 45.0
    assert run_row["resume_decision"]["decision"] == "resume"
    assert run_row["resume_decision"]["decision_source"] == "checkpoint.writer"
    assert run_row["revision"] == 2


def test_task_run_checkpoint_ambiguous_requires_blocked_decision(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="checkpoint.ambiguous",
        task={
            "task_id": "task_checkpoint_ambiguous",
            "kind": "software",
            "title": "Ambiguous checkpoint",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record ambiguous checkpoint",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="checkpoint.ambiguous",
        task_id="task_checkpoint_ambiguous",
        selected_surface="pipeline",
        execution_id="run_checkpoint_amb_001",
        execution_ref="subsystem_pipeline:run_checkpoint_amb_001",
        binding_ref="binding_checkpoint_amb_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_checkpoint_amb_001",
    )["task_register"]
    updated = register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="checkpoint.ambiguous",
        task_id="task_checkpoint_ambiguous",
        run_id="run_checkpoint_amb_001",
        checkpoint={
            "checkpoint_id": "cp_amb_001",
            "checkpoint_status": "ambiguous",
            "checkpoint_note": "Partial state without evidence.",
        },
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_checkpoint_ambiguous")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_checkpoint_amb_001")
    assert run_row["resume_decision"]["decision"] == "blocked"


def test_task_run_checkpoint_accepts_failed_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="checkpoint.failed",
        task={
            "task_id": "task_checkpoint_failed",
            "kind": "software",
            "title": "Failed checkpoint",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record failed checkpoint",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="checkpoint.failed",
        task_id="task_checkpoint_failed",
        selected_surface="codex_worker",
        execution_id="run_checkpoint_failed_001",
        execution_ref="subsystem_codex:run_checkpoint_failed_001",
        binding_ref="binding_checkpoint_failed_001",
        job_uri=str(tmp_path / "runtime" / "state" / "codex" / "state.json"),
        trace_id="trace_checkpoint_failed_001",
    )["task_register"]
    updated = register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="checkpoint.failed",
        task_id="task_checkpoint_failed",
        run_id="run_checkpoint_failed_001",
        checkpoint={
            "checkpoint_id": "cp_failed_001",
            "checkpoint_status": "failed",
            "checkpoint_note": "Worker failed before completion.",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_failed_001.json"],
        },
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_checkpoint_failed")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_checkpoint_failed_001")
    assert run_row["checkpoint"]["checkpoint_status"] == "failed"
    assert run_row["checkpoint"]["checkpoint_note"] == "Worker failed before completion."


def test_task_run_intervention_records_pause_request_on_running_run(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intervention.pause",
        task={
            "task_id": "task_intervention_pause",
            "kind": "software",
            "title": "Pause intervention",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record intervention",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="intervention.pause",
        task_id="task_intervention_pause",
        selected_surface="pipeline",
        execution_id="run_intervention_pause_001",
        execution_ref="subsystem_pipeline:run_intervention_pause_001",
        binding_ref="binding_intervention_pause_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_intervention_pause_001",
    )["task_register"]
    updated = register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="intervention.pause",
        task_id="task_intervention_pause",
        run_id="run_intervention_pause_001",
        action="pause",
        reason="Operator requested stop-after-current behavior.",
        requested_by="operator",
    )["task_register"]
    task = next(task for task in updated["active_tasks"] if task["task_id"] == "task_intervention_pause")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_intervention_pause_001")
    latest = run_row["interventions"][-1]
    assert latest["action"] == "pause"
    assert latest["requested_by"] == "operator"
    assert latest["outcome_state"] == "accepted"
    assert latest["outcome_reason"] == "pending_execution_surface_application"


def test_task_run_intervention_conflict_is_recorded_explicitly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intervention.conflict",
        task={
            "task_id": "task_intervention_conflict",
            "kind": "software",
            "title": "Conflicting intervention",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record intervention conflict",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="intervention.conflict",
        task_id="task_intervention_conflict",
        selected_surface="pipeline",
        execution_id="run_intervention_conflict_001",
        execution_ref="subsystem_pipeline:run_intervention_conflict_001",
        binding_ref="binding_intervention_conflict_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_intervention_conflict_001",
    )["task_register"]
    first = register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="intervention.conflict",
        task_id="task_intervention_conflict",
        run_id="run_intervention_conflict_001",
        action="pause",
        reason="Pause first.",
        requested_by="operator",
    )["task_register"]
    second = register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="intervention.conflict",
        task_id="task_intervention_conflict",
        run_id="run_intervention_conflict_001",
        action="resume",
        reason="Resume before the pause is resolved.",
        requested_by="operator",
    )["task_register"]
    task = next(task for task in second["active_tasks"] if task["task_id"] == "task_intervention_conflict")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_intervention_conflict_001")
    latest = run_row["interventions"][-1]
    assert latest["action"] == "resume"
    assert latest["outcome_state"] == "conflict"
    assert latest["conflict_with_intervention_id"]


def test_task_run_intervention_replays_from_append_only_run_events(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intervention.replay",
        task={
            "task_id": "task_intervention_replay",
            "kind": "software",
            "title": "Replay intervention truth",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "replay intervention history",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="intervention.replay",
        task_id="task_intervention_replay",
        selected_surface="pipeline",
        execution_id="run_intervention_replay_001",
        execution_ref="subsystem_pipeline:run_intervention_replay_001",
        binding_ref="binding_intervention_replay_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_intervention_replay_001",
    )["task_register"]
    updated = register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="intervention.replay",
        task_id="task_intervention_replay",
        run_id="run_intervention_replay_001",
        action="pause",
        reason="Replay-safe pause request.",
        requested_by="operator",
    )["task_register"]
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink()
    summary_path.unlink()

    replayed = register.load_or_init_task_register(tmp_path)
    task = next(task for task in replayed["active_tasks"] if task["task_id"] == "task_intervention_replay")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == "run_intervention_replay_001")
    latest = run_row["interventions"][-1]
    assert latest["action"] == "pause"
    assert latest["outcome_state"] == "accepted"


def test_task_register_replay_rebuilds_task_run_rows(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    after_task = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="task_run_replay.writer",
        task={
            "task_id": "task_exec_owner_replay",
            "kind": "software",
            "title": "Replay run rows",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "ensure rows replay",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(after_task["write_generation"]),
        writer="task_run_replay.writer",
        task_id="task_exec_owner_replay",
        selected_surface="pipeline",
        execution_id="run_replay_001",
        execution_ref="subsystem_pipeline:run_replay_001",
        binding_ref="binding_replay_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_replay_001",
    )["task_register"]
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink()
    summary_path.unlink()

    replayed = register.load_or_init_task_register(tmp_path)
    replay_task = next(task for task in replayed["active_tasks"] if task["task_id"] == "task_exec_owner_replay")
    assert replay_task.get("task_runs")
    assert replay_task.get("current_run_id") == "run_replay_001"


def test_task_register_replay_reconstructs_task_run_truth_from_append_only_run_events(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    with_run = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="task_run_append.writer",
        task={
            "task_id": "task_run_append",
            "kind": "software",
            "title": "Append-only run event reconstruction",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "replay from run events",
        },
    )["task_register"]
    first = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="task_run_append.writer",
        task_id="task_run_append",
        selected_surface="pipeline",
        execution_id="run_append_001",
        execution_ref="subsystem_pipeline:run_append_001",
        binding_ref="binding_append_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_append_001",
    )["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="task_run_append.writer",
        task_id="task_run_append",
        selected_surface="pipeline",
        execution_id="run_append_002",
        execution_ref="subsystem_pipeline:run_append_002",
        binding_ref="binding_append_002",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_append_002",
    )["task_register"]
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    register_path.unlink()
    summary_path.unlink()
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    events = [row for row in events if str(row.get("event") or "") != "task_register_committed"]
    events_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in events) + "\n", encoding="utf-8")

    replayed = register.replay_task_register_events(tmp_path)
    replay_task = next(task for task in replayed["active_tasks"] if task["task_id"] == "task_run_append")
    run_ids = sorted([str(row.get("run_id") or "") for row in list(replay_task.get("task_runs") or [])])
    assert replay_task.get("current_run_id") == "run_append_002"
    assert run_ids == ["run_append_001", "run_append_002"]



def test_task_register_replay_fails_closed_for_stale_task_run_event_revision(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    with_run = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="run_event_fails.writer",
        task={
            "task_id": "task_run_stale_event",
            "kind": "software",
            "title": "Reject stale run event history",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "detect malformed run history",
        },
    )["task_register"]
    first = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="run_event_fails.writer",
        task_id="task_run_stale_event",
        selected_surface="pipeline",
        execution_id="run_stale_001",
        execution_ref="subsystem_pipeline:run_stale_001",
        binding_ref="binding_stale_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_stale_001",
    )["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="run_event_fails.writer",
        task_id="task_run_stale_event",
        selected_surface="pipeline",
        execution_id="run_stale_001",
        execution_ref="subsystem_pipeline:run_stale_001",
        binding_ref="binding_stale_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_stale_001",
    )["task_register"]
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    run_events = [
        row
        for row in events
        if str(row.get("event") or "") == "task_run_recorded"
        and str((row.get("payload") or {}).get("task_id") or "") == "task_run_stale_event"
    ]
    assert run_events, "expected run events for target task"
    stale = json.loads(json.dumps(run_events[-1], sort_keys=True))
    stale_payload = dict(stale.get("payload") or {})
    stale_row = dict(stale_payload.get("run_row") or {})
    stale_payload["run_revision"] = max(0, int(stale_payload.get("run_revision") or 0) - 1)
    stale_row["revision"] = stale_payload["run_revision"]
    stale_payload["run_row"] = stale_row
    stale["payload"] = stale_payload
    events.append(stale)
    events_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in events) + "\n", encoding="utf-8")

    register_path.unlink()
    if summary_path.exists():
        summary_path.unlink()

    with pytest.raises(register.TaskRegisterError, match="run_event has stale revision"):
        register.replay_task_register_events(tmp_path)


def test_task_register_replay_projection_prefers_canonical_task_run_over_stale_task_register_ownership(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    with_task = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="run_snapshot_projection.writer",
        task={
            "task_id": "task_run_prefers_replayed_run",
            "kind": "software",
            "title": "Replay-safe projection should trust run row",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "project from replayed task run",
        },
    )["task_register"]
    first = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(with_task["write_generation"]),
        writer="run_snapshot_projection.writer",
        task_id="task_run_prefers_replayed_run",
        selected_surface="pipeline",
        execution_id="run_replay_proj_001",
        execution_ref="subsystem_pipeline:run_replay_proj_001",
        binding_ref="binding_replay_proj_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_replay_proj_001",
    )["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="run_snapshot_projection.writer",
        task_id="task_run_prefers_replayed_run",
        selected_surface="pipeline",
        execution_id="run_replay_proj_002",
        execution_ref="subsystem_pipeline:run_replay_proj_002",
        binding_ref="binding_replay_proj_002",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_replay_proj_002",
    )["task_register"]

    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in events:
        if str(row.get("event") or "") != "task_register_committed":
            continue
        payload = dict(row.get("payload") or {})
        register_payload = payload.get("register")
        if not isinstance(register_payload, dict):
            continue
        task_rows = list(register_payload.get("active_tasks") or [])
        for task_row in task_rows:
            if str(task_row.get("task_id") or "") == "task_run_prefers_replayed_run":
                task_row["execution_surface_ownership"] = {
                    "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                    "selected_surface": "scheduler",
                    "execution_id": "intruder_replay_001",
                    "execution_ref": "scheduler:intruder_replay_001",
                    "binding_ref": "intruder_binding_replay_001",
                    "job_uri": "runtime/state/scheduler/jobs/intruder_replay_001.json",
                    "trace_id": "trace_scheduler_intruder_replay_001",
                    "reference_status": "execution_ref_active",
                    "reference_status_reason": "stale snapshot corruption",
                    "reference_revision": 99,
                    "owner_uid": register._EXECUTION_OWNER_UID,
                }
                break
        payload["register"] = dict(register_payload)
        payload["register"]["active_tasks"] = task_rows
        row["payload"] = payload

    events_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in events) + "\n", encoding="utf-8"
    )

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink(missing_ok=True)
    summary_path.unlink(missing_ok=True)

    replayed = register.load_or_init_task_register(tmp_path)
    replay_task = next(task for task in replayed["active_tasks"] if task["task_id"] == "task_run_prefers_replayed_run")
    assert replay_task.get("current_run_id") == "run_replay_proj_002"
    projection = snapshot._execution_surface_projection(task_register=replayed, now_utc="2026-03-27T00:00:00Z")
    run_rows = projection.get("tasks") or []
    projected = next(task for task in run_rows if task["task_id"] == "task_run_prefers_replayed_run")
    assert projected["execution_id"] == "run_replay_proj_002"
    assert projected["current_run_id"] == "run_replay_proj_002"
    assert projected["selected_surface"] == "pipeline"
    assert projected["provenance"]["source"] == "BD05.task_register.task_runs[current_run_id]"


def test_task_execution_surface_ownership_duplicate_handoff_requires_conflict_gate(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execowner.writer",
        task={
            "task_id": "task_exec_owner_conflict",
            "kind": "software",
            "title": "Reject duplicate handoff without conflict gate",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "protect unresolved critical refs",
        },
    )["task_register"]
    first = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_conflict",
        selected_surface="pipeline",
        execution_id="",
        execution_ref="",
        binding_ref="",
        job_uri="",
        trace_id="",
    )["task_register"]
    first_task = next(task for task in first["active_tasks"] if task["task_id"] == "task_exec_owner_conflict")
    assert first_task["execution_surface_ownership"]["selected_surface"] == "pipeline"
    assert first_task["execution_surface_ownership"]["reference_status"] == "execution_ref_unresolved"
    second = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_conflict",
        selected_surface="scheduler",
        execution_id="job_002",
        execution_ref="scheduler:job_002",
        binding_ref="binding_002",
        job_uri=str(tmp_path / "runtime" / "state" / "scheduler" / "jobs" / "job_002.json"),
        trace_id="trace_job_002",
    )["task_register"]
    second_task = next(task for task in second["active_tasks"] if task["task_id"] == "task_exec_owner_conflict")
    assert second_task["execution_surface_ownership"]["selected_surface"] == "pipeline"
    assert second_task["execution_surface_ownership"]["reference_status"] == "execution_ref_conflict"
    assert second_task["execution_surface_ownership"]["reference_revision"] == 2
    assert second_task["execution_surface_ownership"]["execution_id"] == ""
    third = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(second["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_conflict",
        selected_surface="scheduler",
        execution_id="job_002",
        execution_ref="scheduler:job_002",
        binding_ref="binding_002",
        job_uri=str(tmp_path / "runtime" / "state" / "scheduler" / "jobs" / "job_002.json"),
        trace_id="trace_job_002",
        allow_conflict_overwrite=True,
    )["task_register"]
    third_task = next(task for task in third["active_tasks"] if task["task_id"] == "task_exec_owner_conflict")
    assert third_task["execution_surface_ownership"]["selected_surface"] == "scheduler"
    assert third_task["execution_surface_ownership"]["reference_status"] == "execution_ref_active"
    assert third_task["execution_surface_ownership"]["reference_revision"] == 3
    assert third_task["execution_surface_ownership"]["execution_id"] == "job_002"


def test_task_execution_surface_ownership_stale_reconnect_preserves_refs_and_marks_stale(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execowner.writer",
        task={
            "task_id": "task_exec_owner_stale",
            "kind": "software",
            "title": "Preserve refs on stale reconnect",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "rehydrate execution refs",
        },
    )["task_register"]
    baseline = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_stale",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_pipeline_001",
    )["task_register"]
    baseline_task = next(task for task in baseline["active_tasks"] if task["task_id"] == "task_exec_owner_stale")
    assert baseline_task["execution_surface_ownership"]["reference_status"] == "execution_ref_active"

    stale_reconnect = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(baseline["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_stale",
        selected_surface="pipeline",
        execution_id="run_999",
        execution_ref="subsystem_pipeline:run_999",
        binding_ref="binding_999",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state_stale.json"),
        trace_id="trace_pipeline_999",
        reference_status="execution_ref_stale",
    )["task_register"]
    stale_task = next(task for task in stale_reconnect["active_tasks"] if task["task_id"] == "task_exec_owner_stale")
    assert stale_task["execution_surface_ownership"]["selected_surface"] == "pipeline"
    assert stale_task["execution_surface_ownership"]["reference_status"] == "execution_ref_stale"
    assert stale_task["execution_surface_ownership"]["execution_id"] == "run_001"
    assert stale_task["execution_surface_ownership"]["execution_ref"] == "subsystem_pipeline:run_001"
    assert stale_task["execution_surface_ownership"]["binding_ref"] == "binding_001"
    assert stale_task["execution_surface_ownership"]["job_uri"].endswith("pipeline/state.json")
    assert stale_task["execution_surface_ownership"]["trace_id"] == "trace_pipeline_001"
    assert stale_task["execution_surface_ownership"]["reference_revision"] == 2
    assert "stale" in stale_task["execution_surface_ownership"]["reference_status_reason"].lower()

    blocked_switch = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(stale_reconnect["write_generation"]),
        writer="execowner.writer",
        task_id="task_exec_owner_stale",
        selected_surface="scheduler",
        execution_id="job_002",
        execution_ref="scheduler:job_002",
        binding_ref="binding_002",
        job_uri=str(tmp_path / "runtime" / "state" / "scheduler" / "jobs" / "job_002.json"),
        trace_id="trace_job_002",
    )["task_register"]
    blocked_task = next(task for task in blocked_switch["active_tasks"] if task["task_id"] == "task_exec_owner_stale")
    assert blocked_task["execution_surface_ownership"]["selected_surface"] == "pipeline"
    assert blocked_task["execution_surface_ownership"]["reference_status"] == "execution_ref_conflict"
    assert blocked_task["execution_surface_ownership"]["execution_id"] == "run_001"
    assert blocked_task["execution_surface_ownership"]["reference_revision"] == 3


def test_task_register_conflict_is_detectable_as_merge_needed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    after_first = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="conflict.writer",
        task={
            "task_id": "task_shared",
            "kind": "software",
            "title": "Prototype actuator control shim",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "sketch API",
        },
    )["task_register"]
    after_second = register.upsert_task(
        tmp_path,
        expected_generation=int(after_first["write_generation"]),
        writer="conflict.writer",
        task={
            "task_id": "task_shared",
            "kind": "fabrication",
            "title": "Prototype actuator fixture",
            "status": "blocked",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "merge task identity facts",
        },
    )["task_register"]
    task = _find_task(after_second, "task_shared")
    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    )

    assert task["merge_status"] == "merge_needed"
    assert sorted(task["conflict_fields"]) == ["kind", "title"]
    assert "task_conflict_detected" in events


def test_task_register_snapshot_is_generic_across_domains(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    generation = int(current["write_generation"])
    for task_id, kind, title, action in [
        ("task_soft", "software", "Patch queue worker", "update targeted tests"),
        ("task_exp", "experiment", "Prepare solvent screen", "log reagent matrix"),
        ("task_mat", "materials", "Review laminate coupon batch", "compare microscopy notes"),
        ("task_ops", "lab_ops", "Check overnight scheduler runs", "summarize residue"),
    ]:
        res = register.upsert_task(
            tmp_path,
            expected_generation=generation,
            writer="domain.writer",
            task={
                "task_id": task_id,
                "kind": kind,
                "title": title,
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "next_expected_action": action,
            },
        )
        generation = int(res["task_register"]["write_generation"])

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    assert [task["kind"] for task in snapshot["task_register"]["active_tasks"]] == [
        "software",
        "experiment",
        "materials",
        "lab_ops",
    ]
    assert snapshot["summary_cache"]["active_task_count"] == 4


def test_task_scope_contract_persists_assumptions_constraints_non_goals_and_scope_history(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="scope.writer",
        task={
            "task_id": "task_scope_contract",
            "kind": "software",
            "title": "Implement J5B2 scope contract",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record planning facts durably",
        },
    )["task_register"]
    first = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="scope.writer",
        task_id="task_scope_contract",
        assumptions=[
            {
                "assumption_id": "assume_runtime_refresh",
                "statement": "Runtime refresh applies to prompt text only, not already-running jobs.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "requires_operator_confirmation": False,
                "blocking": False,
                "confirmed_by": "operator",
                "source": "operator",
                "rationale": "The operator explicitly asked for runtime/live distinction to stay visible.",
            },
            {
                "assumption_id": "assume_missing_target",
                "statement": "The current task should extend BD05 instead of creating a new mutable plan file.",
                "kind": "inferred",
                "approval_state": "needs_operator_confirmation",
                "operator_approved": False,
                "requires_operator_confirmation": True,
                "blocking": True,
                "source": "assistant_inference",
                "rationale": "The PIS binds planning facts to active work in BD05.",
            },
        ],
        constraints=[
            {
                "constraint_id": "constraint_no_new_blob",
                "statement": "Do not introduce a new mutable planning blob.",
                "scope": "task",
                "source": "operator",
                "rationale": "BD05 remains canonical for active work truth.",
                "active": True,
            }
        ],
        non_goals=[
            {
                "non_goal_id": "non_goal_acceptance_targets",
                "statement": "Do not implement acceptance or proof-target capture here.",
                "source": "operator",
                "rationale": "That belongs to K6C3.",
                "active": True,
            }
        ],
        scope_revision={
            "revision_id": "scope_rev_1",
            "summary": "Limit work to durable assumption and scope recording.",
            "in_scope": ["task register schema", "scope mutation API"],
            "out_of_scope": ["acceptance targets", "routing policy"],
            "rationale": "J5B2 covers planning premises only.",
            "source": "operator",
        },
    )["task_register"]
    second = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="scope.writer",
        task_id="task_scope_contract",
        scope_revision={
            "revision_id": "scope_rev_2",
            "summary": "Also surface planning facts in operator-readable summaries.",
            "in_scope": ["task register schema", "scope mutation API", "read-side summaries"],
            "out_of_scope": ["acceptance targets", "routing policy"],
            "rationale": "The operator needs to see confirmed vs provisional assumptions.",
            "source": "operator_followup",
        },
    )["task_register"]

    task = next(task for task in second["active_tasks"] if task["task_id"] == "task_scope_contract")
    reloaded = register.load_or_init_task_register(tmp_path)
    reloaded_task = next(task for task in reloaded["active_tasks"] if task["task_id"] == "task_scope_contract")
    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    )

    assert task["scope_contract"]["assumptions"][0]["operator_approved"] is True
    assert task["scope_contract"]["assumptions"][1]["approval_state"] == "needs_operator_confirmation"
    assert task["scope_contract"]["constraints"][0]["scope"] == "task"
    assert task["scope_contract"]["non_goals"][0]["statement"] == "Do not implement acceptance or proof-target capture here."
    assert task["scope_contract"]["current_scope"]["revision_id"] == "scope_rev_2"
    assert [row["revision_id"] for row in task["scope_contract"]["scope_history"]] == ["scope_rev_1", "scope_rev_2"]
    assert reloaded_task["scope_contract"]["scope_history"][0]["summary"] == "Limit work to durable assumption and scope recording."
    assert "task_scope_contract_updated" in events


def test_task_scope_contract_rejects_provisional_assumptions_marked_as_operator_approved(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="scope.writer",
        task={
            "task_id": "task_scope_invalid",
            "kind": "software",
            "title": "Reject invalid approval claims",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "fail closed on invalid scope facts",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="cannot be operator-approved without operator_confirmed state"):
        register.update_task_scope_contract(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="scope.writer",
            task_id="task_scope_invalid",
            assumptions=[
                {
                    "assumption_id": "assume_invalid",
                    "statement": "This provisional assumption should not look approved.",
                    "kind": "inferred",
                    "approval_state": "provisional",
                    "operator_approved": True,
                    "requires_operator_confirmation": True,
                    "blocking": False,
                    "source": "assistant_inference",
                    "rationale": "Negative regression case.",
                }
            ],
        )


def test_task_acceptance_contract_persists_targets_proof_and_review_gates(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="acceptance.writer",
        task={
            "task_id": "task_acceptance_contract",
            "kind": "software",
            "title": "Implement K6C3 acceptance capture",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "record acceptance and proof boundaries",
        },
    )["task_register"]

    first = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="acceptance.writer",
        task_id="task_acceptance_contract",
        acceptance_targets=[
            {
                "target_id": "target_persist",
                "statement": "Acceptance targets persist in BD05 task truth.",
                "target_type": "durability",
                "source": "operator",
                "rationale": "Chat replay is not canonical task truth.",
                "active": True,
            }
        ],
        proof_targets=[
            {
                "proof_id": "proof_tests",
                "statement": "Targeted tests prove acceptance-target persistence.",
                "proof_class": "test",
                "required": True,
                "blocking": True,
                "status": "pending",
                "source": "operator",
                "rationale": "Required local proof for this child.",
            }
        ],
        review_gates=[
            {
                "gate_id": "gate_operator_review",
                "statement": "Operator review is required before closeout.",
                "reviewer_role": "operator",
                "applies_at": "before_closeout",
                "status": "pending",
                "blocking": True,
                "source": "operator",
                "rationale": "Higher-risk planning changes need an explicit human boundary.",
            }
        ],
        completion_state={
            "implementation_complete": True,
            "evidence_complete": False,
            "operator_approved": False,
            "summary": "Contract shape landed, proof and review still pending.",
        },
    )["task_register"]

    second = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="acceptance.writer",
        task_id="task_acceptance_contract",
        proof_targets=[
            {
                "proof_id": "proof_tests",
                "statement": "Targeted tests prove acceptance-target persistence.",
                "proof_class": "test",
                "required": True,
                "blocking": True,
                "status": "satisfied",
                "source": "operator",
                "rationale": "Required local proof for this child.",
            }
        ],
        review_gates=[
            {
                "gate_id": "gate_operator_review",
                "statement": "Operator review is required before closeout.",
                "reviewer_role": "operator",
                "applies_at": "before_closeout",
                "status": "approved",
                "blocking": True,
                "source": "operator",
                "rationale": "Higher-risk planning changes need an explicit human boundary.",
            }
        ],
        completion_state={
            "implementation_complete": True,
            "evidence_complete": True,
            "operator_approved": True,
            "summary": "Implementation, proof, and operator approval are all complete.",
        },
    )["task_register"]

    task = next(task for task in second["active_tasks"] if task["task_id"] == "task_acceptance_contract")
    reloaded = register.load_or_init_task_register(tmp_path)
    reloaded_task = next(task for task in reloaded["active_tasks"] if task["task_id"] == "task_acceptance_contract")
    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    )

    assert task["acceptance_contract"]["acceptance_targets"][0]["statement"] == "Acceptance targets persist in BD05 task truth."
    assert task["acceptance_contract"]["proof_targets"][0]["status"] == "satisfied"
    assert task["acceptance_contract"]["review_gates"][0]["status"] == "approved"
    assert task["acceptance_contract"]["completion_state"]["implementation_complete"] is True
    assert task["acceptance_contract"]["completion_state"]["evidence_complete"] is True
    assert task["acceptance_contract"]["completion_state"]["operator_approved"] is True
    assert reloaded_task["acceptance_contract"]["completion_state"]["summary"] == (
        "Implementation, proof, and operator approval are all complete."
    )
    assert "task_acceptance_contract_updated" in events


def test_task_acceptance_contract_rejects_evidence_complete_when_required_proof_is_pending(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="acceptance.writer",
        task={
            "task_id": "task_acceptance_evidence_invalid",
            "kind": "software",
            "title": "Reject false evidence completion",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "fail closed on missing proof",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="evidence_complete requires required proof targets to be satisfied or waived"):
        register.update_task_acceptance_contract(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="acceptance.writer",
            task_id="task_acceptance_evidence_invalid",
            proof_targets=[
                {
                    "proof_id": "proof_missing",
                    "statement": "Need targeted tests before evidence-complete.",
                    "proof_class": "test",
                    "required": True,
                    "blocking": True,
                    "status": "pending",
                    "source": "operator",
                    "rationale": "Missing proof must stay missing.",
                }
            ],
            completion_state={
                "implementation_complete": True,
                "evidence_complete": True,
                "operator_approved": False,
                "summary": "This should fail because proof is still pending.",
            },
        )


def test_task_acceptance_contract_rejects_operator_approval_when_review_gate_is_pending(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="acceptance.writer",
        task={
            "task_id": "task_acceptance_review_invalid",
            "kind": "software",
            "title": "Reject false operator approval",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "fail closed on pending review",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="operator_approved requires blocking review gates to be approved or waived"):
        register.update_task_acceptance_contract(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="acceptance.writer",
            task_id="task_acceptance_review_invalid",
            proof_targets=[
                {
                    "proof_id": "proof_done",
                    "statement": "Proof is already satisfied.",
                    "proof_class": "artifact",
                    "required": True,
                    "blocking": True,
                    "status": "satisfied",
                    "source": "operator",
                    "rationale": "Only the review gate should block approval.",
                }
            ],
            review_gates=[
                {
                    "gate_id": "gate_pending",
                    "statement": "Operator still needs to review the change.",
                    "reviewer_role": "operator",
                    "applies_at": "before_closeout",
                    "status": "pending",
                    "blocking": True,
                    "source": "operator",
                    "rationale": "Pending operator gate must stay blocking.",
                }
            ],
            completion_state={
                "implementation_complete": True,
                "evidence_complete": True,
                "operator_approved": True,
                "summary": "This should fail because review is still pending.",
            },
        )


def test_task_decomposition_persists_direct_vs_milestone_plan_and_writes_ledger_artifact(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022103Z-L7D4"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="decomp.writer",
        task={
            "task_id": "task_decomposition",
            "kind": "software",
            "title": "Implement L7D4 decomposition contract",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "persist the planning shape",
        },
    )["task_register"]
    scoped = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="decomp.writer",
        task_id="task_decomposition",
        assumptions=[
            {
                "assumption_id": "assume_bd05_canonical",
                "statement": "BD05 remains canonical for live task truth.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "requires_operator_confirmation": False,
                "blocking": False,
                "confirmed_by": "operator",
                "source": "operator",
                "rationale": "The PIS says current plan shape writes back into BD05.",
            }
        ],
        scope_revision={
            "revision_id": "scope_decomp",
            "summary": "Keep planning truth compact in BD05 and detailed in ledger artifacts.",
            "in_scope": ["decomposition contract", "ledger artifact"],
            "out_of_scope": ["routing"],
            "rationale": "This child stops at planning shape.",
            "source": "operator",
        },
    )["task_register"]
    accepted = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(scoped["write_generation"]),
        writer="decomp.writer",
        task_id="task_decomposition",
        acceptance_targets=[
            {
                "target_id": "target_operator_inspectable",
                "statement": "The operator can inspect the split without chat replay.",
                "target_type": "operator_surface",
                "source": "operator",
                "rationale": "Planning rationale must survive restart.",
                "active": True,
            }
        ],
        completion_state={
            "implementation_complete": False,
            "evidence_complete": False,
            "operator_approved": False,
            "summary": "Acceptance target is recorded before decomposition.",
        },
    )["task_register"]
    direct = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(accepted["write_generation"]),
        writer="decomp.writer",
        task_id="task_decomposition",
        mode="direct",
        rationale="A very small follow-up can stay flat when it is only a couple of bounded edits.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": ["narrow_surface"],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Small enough to handle directly.",
        },
        linked_assumption_ids=["assume_bd05_canonical"],
        linked_acceptance_target_ids=["target_operator_inspectable"],
        direct_summary="Two bounded edits and one test update.",
    )
    milestone = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(direct["task_register"]["write_generation"]),
        writer="decomp.writer",
        task_id="task_decomposition",
        mode="milestone_plan",
        rationale="The task now spans persistence, summaries, and regression proof, so it needs durable checkpoints.",
        decision={
            "estimated_step_count": 5,
            "risk_flags": ["durability", "operator_surface"],
            "requires_operator_checkpoint": True,
            "needs_ordering": True,
            "decision_summary": "Split by persistence and proof boundary.",
        },
        linked_assumption_ids=["assume_bd05_canonical"],
        linked_acceptance_target_ids=["target_operator_inspectable"],
        active_milestone_id="milestone_contract",
        active_checkpoint_id="checkpoint_schema",
        revision_trigger={
            "kind": "changed_evidence",
            "summary": "The task expanded from two direct edits into a multi-surface change with proof and operator projection work.",
            "evidence_refs": ["artifact:decomposition_scope_review"],
        },
        dependency_mapping={
            "fanout_policy": "explicit_safe_fanout",
            "summary": "Schema work must land first; read projections and regression proof can fan out safely after that.",
            "milestones": [
                {
                    "milestone_id": "milestone_contract",
                    "depends_on": [],
                    "execution_mode": "serial_only",
                    "reason_codes": ["root_prerequisite"],
                    "reason_summary": "The durable contract has to exist before any dependent work starts.",
                    "caution_flags": ["unsafe_to_parallelize"],
                    "shared_resource_keys": [],
                },
                {
                    "milestone_id": "milestone_projection",
                    "depends_on": ["milestone_contract"],
                    "execution_mode": "fanout_safe",
                    "fanout_group_id": "post_contract",
                    "reason_codes": ["independent_read_surface"],
                    "reason_summary": "Projection work can run in parallel with proof once the contract is stable.",
                    "caution_flags": [],
                    "shared_resource_keys": [],
                },
                {
                    "milestone_id": "milestone_proof",
                    "depends_on": ["milestone_contract"],
                    "execution_mode": "fanout_safe",
                    "fanout_group_id": "post_contract",
                    "reason_codes": ["independent_regression_proof"],
                    "reason_summary": "Focused regression proof can run alongside projection work after the contract lands.",
                    "caution_flags": [],
                    "shared_resource_keys": [],
                },
            ],
        },
        milestones=[
            {
                "milestone_id": "milestone_contract",
                "title": "Persist decomposition contract",
                "rationale": "Live work needs canonical plan shape in BD05 first.",
                "exit_criteria": ["Task row stores the compact decomposition summary."],
                "assumption_ids": ["assume_bd05_canonical"],
                "acceptance_target_ids": ["target_operator_inspectable"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_schema",
                        "title": "Add task-register decomposition schema",
                        "rationale": "Without schema support the rest is transient theater.",
                        "exit_criteria": ["Register validation accepts direct and milestone plan modes."],
                        "assumption_ids": ["assume_bd05_canonical"],
                        "acceptance_target_ids": ["target_operator_inspectable"],
                    }
                ],
            },
            {
                "milestone_id": "milestone_projection",
                "title": "Surface operator-readable checkpoint state",
                "rationale": "The operator needs readable milestone and checkpoint state after restart.",
                "exit_criteria": ["Bootstrap and snapshot expose milestone/checkpoint counts."],
                "assumption_ids": ["assume_bd05_canonical"],
                "acceptance_target_ids": ["target_operator_inspectable"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_snapshot",
                        "title": "Project decomposition into summaries",
                        "rationale": "Compact projections keep the contract inspectable without opening raw artifacts.",
                        "exit_criteria": ["Read-side projections show mode, milestones, and checkpoints."],
                        "assumption_ids": ["assume_bd05_canonical"],
                        "acceptance_target_ids": ["target_operator_inspectable"],
                    }
                ],
            },
            {
                "milestone_id": "milestone_proof",
                "title": "Prove dependency persistence",
                "rationale": "The dependency contract needs explicit regression proof so unsafe fanout does not sneak back in.",
                "exit_criteria": ["Targeted tests prove dependency persistence and fail-closed fanout rules."],
                "assumption_ids": ["assume_bd05_canonical"],
                "acceptance_target_ids": ["target_operator_inspectable"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_dependency_tests",
                        "title": "Add fanout rule regressions",
                        "rationale": "Parallel safety claims without proof are just optimism with punctuation.",
                        "exit_criteria": ["Tests cover safe fanout and unsafe downgrade/rejection paths."],
                        "assumption_ids": ["assume_bd05_canonical"],
                        "acceptance_target_ids": ["target_operator_inspectable"],
                    }
                ],
            },
        ],
    )

    task = next(task for task in milestone["task_register"]["active_tasks"] if task["task_id"] == "task_decomposition")
    artifact_path = tmp_path / task["decomposition_contract"]["artifact_ref"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    reloaded = register.load_or_init_task_register(tmp_path)
    reloaded_task = next(task for task in reloaded["active_tasks"] if task["task_id"] == "task_decomposition")
    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    )

    assert task["decomposition_contract"]["mode"] == "milestone_plan"
    assert task["decomposition_contract"]["plan_version"] == 2
    assert task["decomposition_contract"]["current_revision_id"]
    assert task["decomposition_contract"]["replanning"]["state"] == "settled"
    assert len(task["decomposition_contract"]["revision_history"]) == 2
    assert task["decomposition_contract"]["revision_history"][0]["state"] == "superseded"
    assert task["decomposition_contract"]["revision_history"][1]["state"] == "current"
    assert task["decomposition_contract"]["plan_summary"]["milestone_count"] == 3
    assert task["decomposition_contract"]["plan_summary"]["checkpoint_count"] == 3
    assert task["decomposition_contract"]["plan_summary"]["dependency_edge_count"] == 2
    assert task["decomposition_contract"]["plan_summary"]["fanout_ready_count"] == 2
    assert task["decomposition_contract"]["plan_summary"]["forced_serial_count"] == 1
    assert task["decomposition_contract"]["plan_summary"]["active_checkpoint_id"] == "checkpoint_schema"
    assert artifact["schema_version"] == register.TASK_DECOMPOSITION_ARTIFACT_SCHEMA_VERSION
    assert artifact["plan_version"] == 2
    assert artifact["current_revision_id"] == task["decomposition_contract"]["current_revision_id"]
    assert len(artifact["revision_history"]) == 2
    assert artifact["linked_assumption_ids"] == ["assume_bd05_canonical"]
    assert artifact["linked_acceptance_target_ids"] == ["target_operator_inspectable"]
    assert artifact["milestones"][0]["checkpoints"][0]["checkpoint_id"] == "checkpoint_schema"
    assert artifact["dependency_mapping"]["fanout_policy"] == "explicit_safe_fanout"
    assert artifact["dependency_mapping"]["summary_counts"]["dependency_edge_count"] == 2
    assert reloaded_task["decomposition_contract"]["artifact_ref"] == task["decomposition_contract"]["artifact_ref"]
    assert reloaded_task["decomposition_contract"]["dependency_mapping"]["milestones"][1]["fanout_group_id"] == "post_contract"
    assert "task_decomposition_updated" in events


def test_task_decomposition_rejects_trivial_milestone_split(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022103Z-L7D4"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="decomp.writer",
        task={
            "task_id": "task_trivial_split",
            "kind": "software",
            "title": "Reject milestone theater",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "fail closed on fake decomposition",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="too trivial for milestone_plan"):
        register.update_task_decomposition(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="decomp.writer",
            task_id="task_trivial_split",
            mode="milestone_plan",
            rationale="This fake split should be rejected.",
            decision={
                "estimated_step_count": 3,
                "risk_flags": [],
                "requires_operator_checkpoint": False,
                "needs_ordering": False,
                "decision_summary": "Pretending one checkpoint is a milestone plan.",
            },
            milestones=[
                {
                    "milestone_id": "milestone_only",
                    "title": "Only milestone",
                    "rationale": "This is not enough structure to justify decomposition.",
                    "exit_criteria": ["A single vague checkpoint exists."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_only",
                            "title": "Only checkpoint",
                            "rationale": "Still trivial.",
                            "exit_criteria": ["Nothing meaningful was gained."],
                        }
                    ],
                }
            ],
        )


def test_task_replanning_invalidates_current_plan_and_survives_reload(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022105Z-N9F6"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="replan.writer",
        task={
            "task_id": "task_replan_contract",
            "kind": "software",
            "title": "Carry revision truth honestly",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "persist the first durable plan",
        },
    )["task_register"]
    planned = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="replan.writer",
        task_id="task_replan_contract",
        mode="direct",
        rationale="The first pass looks like a single direct fix.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": [],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Start direct until stronger evidence arrives.",
        },
        direct_summary="One write path and one test.",
    )["task_register"]

    invalidated = register.invalidate_task_decomposition(
        tmp_path,
        expected_generation=int(planned["write_generation"]),
        writer="replan.writer",
        task_id="task_replan_contract",
        replanning_state="blocked",
        summary="New evidence invalidated the direct path and no replacement plan is settled yet.",
        trigger={
            "kind": "changed_evidence",
            "summary": "A restart proof gap showed the direct plan was false optimism.",
            "evidence_refs": ["artifact:restart_gap"],
        },
        next_expected_action="settle a replacement milestone plan before execution continues",
        blocked_reason="Current plan invalidated by restart proof gap.",
    )["task_register"]

    task = _find_task(invalidated, "task_replan_contract")
    reloaded = register.load_or_init_task_register(tmp_path)
    reloaded_task = _find_task(reloaded, "task_replan_contract")
    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl").read_text(
        encoding="utf-8"
    )

    assert task["status"] == "blocked"
    assert task["decomposition_contract"]["replanning"]["state"] == "blocked"
    assert task["decomposition_contract"]["current_revision_id"] == ""
    assert task["decomposition_contract"]["mode"] == ""
    assert task["decomposition_contract"]["plan_version"] == 0
    assert task["decomposition_contract"]["replanning"]["invalidated_plan_version"] == 1
    assert task["decomposition_contract"]["revision_history"][0]["state"] == "invalidated"
    assert reloaded_task["decomposition_contract"]["replanning"]["state"] == "blocked"
    assert reloaded_task["decomposition_contract"]["current_revision_id"] == ""
    assert "task_decomposition_invalidated" in events


def test_task_planning_handoff_packages_rationale_and_dependencies_for_execution(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022106Z-P1G7"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="handoff.writer",
        task={
            "task_id": "task_handoff_contract",
            "kind": "software",
            "title": "Package planning handoff",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "publish handoff for execution launch",
        },
    )["task_register"]
    scoped = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_contract",
        assumptions=[
            {
                "assumption_id": "assume_planning_store_canonical",
                "statement": "BD05 and planning artifacts remain canonical.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "requires_operator_confirmation": False,
                "blocking": False,
                "confirmed_by": "operator",
                "source": "operator",
                "rationale": "Planning truth must survive restart.",
            }
        ],
    )["task_register"]
    accepted = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(scoped["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_contract",
        acceptance_targets=[
            {
                "target_id": "target_handoff_package",
                "statement": "Execution launch receives structured planning context.",
                "target_type": "contract",
                "source": "operator",
                "rationale": "Flat prompts lose rationale.",
                "active": True,
            }
        ],
        completion_state={
            "implementation_complete": False,
            "evidence_complete": False,
            "operator_approved": False,
            "summary": "Planning handoff still pending publication.",
        },
    )["task_register"]
    planned = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(accepted["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_contract",
        mode="milestone_plan",
        rationale="The execution launch path needs durable assumptions, acceptance, and dependency rationale.",
        decision={
            "estimated_step_count": 4,
            "risk_flags": ["durability"],
            "requires_operator_checkpoint": True,
            "needs_ordering": True,
            "decision_summary": "Persist handoff before execution launch.",
        },
        linked_assumption_ids=["assume_planning_store_canonical"],
        linked_acceptance_target_ids=["target_handoff_package"],
        active_milestone_id="milestone_contract",
        active_checkpoint_id="checkpoint_handoff",
        dependency_mapping={
            "fanout_policy": "serial_only",
            "summary": "Handoff contract must land before execution launch.",
            "milestones": [
                {
                    "milestone_id": "milestone_contract",
                    "depends_on": [],
                    "execution_mode": "serial_only",
                    "reason_codes": ["root_prerequisite"],
                    "reason_summary": "Contract is the root prerequisite.",
                    "caution_flags": ["unsafe_to_parallelize"],
                    "shared_resource_keys": [],
                },
                {
                    "milestone_id": "milestone_publish",
                    "depends_on": ["milestone_contract"],
                    "execution_mode": "serial_only",
                    "reason_codes": ["ordered_handoff"],
                    "reason_summary": "Publishing depends on contract completion.",
                    "caution_flags": ["unsafe_to_parallelize"],
                    "shared_resource_keys": [],
                },
            ],
        },
        milestones=[
            {
                "milestone_id": "milestone_contract",
                "title": "Persist handoff contract",
                "rationale": "Execution launch should not invent plan context.",
                "exit_criteria": ["Planning fields are durable."],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_handoff",
                        "title": "Write handoff payload rules",
                        "rationale": "Rationale must survive into execution launch.",
                        "exit_criteria": ["Assumptions and acceptance references are linked."],
                        "assumption_ids": ["assume_planning_store_canonical"],
                        "acceptance_target_ids": ["target_handoff_package"],
                    }
                ],
            },
            {
                "milestone_id": "milestone_publish",
                "title": "Publish handoff artifact",
                "rationale": "Execution launch consumes a durable handoff envelope.",
                "exit_criteria": ["Handoff artifact exists in ledger artifacts."],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_publish",
                        "title": "Persist handoff artifact",
                        "rationale": "Restart should not erase launch context.",
                        "exit_criteria": ["Handoff artifact is readable after reload."],
                        "assumption_ids": ["assume_planning_store_canonical"],
                        "acceptance_target_ids": ["target_handoff_package"],
                    }
                ],
            },
        ],
    )["task_register"]

    published = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(planned["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_contract",
    )["handoff"]
    read_back = register.read_task_planning_handoff(tmp_path, task_id="task_handoff_contract")

    assert published["launch_readiness"]["state"] == "ready"
    assert published["launch_readiness"]["launch_ready"] is True
    assert published["planning_payload"]["scope"]["linked_assumption_ids"] == ["assume_planning_store_canonical"]
    assert published["planning_payload"]["acceptance"]["linked_acceptance_target_ids"] == ["target_handoff_package"]
    assert published["planning_payload"]["decomposition"]["checkpoint_rows"][0]["checkpoint_id"] == "checkpoint_handoff"
    assert published["planning_payload"]["decomposition"]["dependency_edges"] == [
        {"from_milestone_id": "milestone_contract", "to_milestone_id": "milestone_publish"}
    ]
    assert published["planning_payload"]["rationale_trail"]["current_revision"]["revision_id"] == published["execution_launch_contract"]["launch_identity"]["current_revision_id"]
    assert published["boundary"]["routing_owner_uid"] == "PIS-20260312-224201Z-D1E7"
    assert Path(tmp_path / published["artifact_ref"]).exists()
    assert read_back["source"] == "artifact"
    assert read_back["handoff"]["launch_readiness"]["launch_ready"] is True


def test_task_planning_handoff_surfaces_blocked_truth_when_replanning_is_unsettled(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022106Z-P1G7"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="handoff.writer",
        task={
            "task_id": "task_handoff_blocked",
            "kind": "software",
            "title": "Surface blocked handoff",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "preserve blocked truth",
        },
    )["task_register"]
    planned = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_blocked",
        mode="direct",
        rationale="A first pass plan exists before invalidation.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": [],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Start direct.",
        },
        direct_summary="Two bounded edits.",
    )["task_register"]
    invalidated = register.invalidate_task_decomposition(
        tmp_path,
        expected_generation=int(planned["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_blocked",
        replanning_state="blocked",
        summary="Current planning prerequisites are incomplete.",
        trigger={
            "kind": "changed_evidence",
            "summary": "Restart evidence invalidated the prior plan.",
            "evidence_refs": ["artifact:restart_gap"],
        },
    )["task_register"]
    published = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(invalidated["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_blocked",
    )["handoff"]

    assert published["launch_readiness"]["launch_ready"] is False
    assert published["launch_readiness"]["state"] == "blocked"
    assert "replanning_state_blocked" in published["launch_readiness"]["blocked_reasons"]


def test_task_planning_handoff_rebuilds_from_canonical_planning_state_after_restart(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022106Z-P1G7"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="handoff.writer",
        task={
            "task_id": "task_handoff_rebuild",
            "kind": "software",
            "title": "Rebuild handoff from canonical state",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "reconstruct handoff if artifact is missing",
        },
    )["task_register"]
    planned = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_rebuild",
        mode="direct",
        rationale="Direct plan is enough for this reconstruction test.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": [],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Publish direct handoff snapshot.",
        },
        direct_summary="One patch and one test.",
    )["task_register"]
    published = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(planned["write_generation"]),
        writer="handoff.writer",
        task_id="task_handoff_rebuild",
    )["handoff"]
    handoff_path = tmp_path / str(published["artifact_ref"])
    handoff_path.unlink()
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink()
    summary_path.unlink()

    reloaded = register.load_or_init_task_register(tmp_path)
    rebuilt = register.read_task_planning_handoff(tmp_path, task_id="task_handoff_rebuild", rebuild_if_missing=True)

    assert reloaded["write_generation"] == planned["write_generation"]
    assert rebuilt["source"] == "reconstructed"
    assert rebuilt["handoff"]["source_of_truth"]["reconstructed_from_canonical_planning_state"] is True
    assert rebuilt["handoff"]["execution_launch_contract"]["launch_identity"]["plan_version"] == 1
    assert rebuilt["handoff"]["launch_readiness"]["launch_ready"] is False


def test_task_decomposition_revision_requires_explicit_trigger(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022105Z-N9F6"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="replan.writer",
        task={
            "task_id": "task_missing_trigger",
            "kind": "software",
            "title": "Reject silent stale-plan overwrite",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "refuse silent revision",
        },
    )["task_register"]
    first = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="replan.writer",
        task_id="task_missing_trigger",
        mode="direct",
        rationale="Start with a minimal plan.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": [],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Small enough for a direct pass.",
        },
        direct_summary="Two edits and one test.",
    )

    with pytest.raises(register.TaskRegisterError, match="revision_trigger is required"):
        register.update_task_decomposition(
            tmp_path,
            expected_generation=int(first["task_register"]["write_generation"]),
            writer="replan.writer",
            task_id="task_missing_trigger",
            mode="milestone_plan",
            rationale="The work grew, but the trigger is missing.",
            decision={
                "estimated_step_count": 4,
                "risk_flags": ["durability"],
                "requires_operator_checkpoint": False,
                "needs_ordering": True,
                "decision_summary": "Would be a revision if the trigger were recorded honestly.",
            },
            milestones=[
                {
                    "milestone_id": "milestone_contract",
                    "title": "Persist plan revision",
                    "rationale": "A replacement plan exists but the trigger is intentionally omitted here.",
                    "exit_criteria": ["Revision contract is written."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_contract",
                            "title": "Write revision facts",
                            "rationale": "Still not enough without the trigger trail.",
                            "exit_criteria": ["Revision facts persist."],
                        }
                    ],
                },
                {
                    "milestone_id": "milestone_proof",
                    "title": "Prove stale plan rejection",
                    "rationale": "Second milestone keeps the split non-trivial while preserving the negative case.",
                    "exit_criteria": ["Focused tests prove the rejection path."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_proof",
                            "title": "Run targeted test",
                            "rationale": "Negative proof is part of the contract, not a side quest.",
                            "exit_criteria": ["Focused regression passes."],
                        }
                    ],
                },
            ],
        )


def test_task_decomposition_rejects_checkpoint_without_rationale(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022103Z-L7D4"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="decomp.writer",
        task={
            "task_id": "task_empty_checkpoint_rationale",
            "kind": "software",
            "title": "Reject empty checkpoint rationale",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "fail closed on empty rationale",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="checkpoint checkpoint_schema rationale is required"):
        register.update_task_decomposition(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="decomp.writer",
            task_id="task_empty_checkpoint_rationale",
            mode="milestone_plan",
            rationale="Checkpoint rationale cannot be blank.",
            decision={
                "estimated_step_count": 4,
                "risk_flags": ["durability"],
                "requires_operator_checkpoint": False,
                "needs_ordering": True,
                "decision_summary": "Structured work still needs real rationale.",
            },
            milestones=[
                {
                    "milestone_id": "milestone_contract",
                    "title": "Persist the contract",
                    "rationale": "The task does have enough work to justify milestones.",
                    "exit_criteria": ["Contract exists."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_schema",
                            "title": "Add schema",
                            "rationale": "",
                            "exit_criteria": ["Schema is present."],
                        }
                    ],
                },
                {
                    "milestone_id": "milestone_proof",
                    "title": "Prove behavior",
                    "rationale": "A second milestone avoids the trivial-split rule and isolates the negative case.",
                    "exit_criteria": ["Regression proof exists."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_tests",
                            "title": "Run targeted tests",
                            "rationale": "Proof still needs its own checkpoint.",
                            "exit_criteria": ["Targeted tests pass."],
                        }
                    ],
                },
            ],
        )


def test_task_decomposition_rejects_hidden_dependency_marked_fanout_safe(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022104Z-M8E5"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="fanout.writer",
        task={
            "task_id": "task_hidden_dependency",
            "kind": "software",
            "title": "Reject hidden dependency fanout",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "fail closed on unsafe fanout",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="cannot be fanout_safe while caution_flags are present"):
        register.update_task_decomposition(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="fanout.writer",
            task_id="task_hidden_dependency",
            mode="milestone_plan",
            rationale="A hidden dependency should force serial execution.",
            decision={
                "estimated_step_count": 4,
                "risk_flags": ["hidden_dependency"],
                "requires_operator_checkpoint": False,
                "needs_ordering": True,
                "decision_summary": "Dependency mapping should reject optimistic fanout.",
            },
            dependency_mapping={
                "fanout_policy": "explicit_safe_fanout",
                "summary": "One milestone hides a prerequisite, so fanout is unsafe.",
                "milestones": [
                    {
                        "milestone_id": "milestone_analyze",
                        "depends_on": [],
                        "execution_mode": "serial_only",
                        "reason_codes": ["root_prerequisite"],
                        "reason_summary": "Analysis has to happen first.",
                        "caution_flags": ["unsafe_to_parallelize"],
                        "shared_resource_keys": [],
                    },
                    {
                        "milestone_id": "milestone_apply",
                        "depends_on": ["milestone_analyze"],
                        "execution_mode": "fanout_safe",
                        "fanout_group_id": "unsafe_group",
                        "reason_codes": ["hidden_dependency"],
                        "reason_summary": "This incorrectly tries to claim hidden work is parallel-safe.",
                        "caution_flags": ["hidden_dependency"],
                        "shared_resource_keys": [],
                    },
                ],
            },
            milestones=[
                {
                    "milestone_id": "milestone_analyze",
                    "title": "Analyze prerequisites",
                    "rationale": "Make hidden dependencies visible first.",
                    "exit_criteria": ["Shared prerequisites are enumerated."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_analyze",
                            "title": "Inspect state",
                            "rationale": "The task has to reveal its real dependencies.",
                            "exit_criteria": ["Known dependencies are captured."],
                        }
                    ],
                },
                {
                    "milestone_id": "milestone_apply",
                    "title": "Apply changes",
                    "rationale": "Actual edits come after dependency analysis.",
                    "exit_criteria": ["Dependent edits are complete."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_apply",
                            "title": "Implement edits",
                            "rationale": "Unsafe fanout should not pass validation.",
                            "exit_criteria": ["Edits are ready."],
                        }
                    ],
                },
            ],
        )


def test_task_decomposition_rejects_shared_resource_fanout_conflict(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022104Z-M8E5"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="fanout.writer",
        task={
            "task_id": "task_shared_resource_conflict",
            "kind": "software",
            "title": "Reject shared-resource fanout",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "fail closed on contested resource",
        },
    )["task_register"]

    with pytest.raises(register.TaskRegisterError, match="cannot be fanout_safe while shared_resource_keys are present"):
        register.update_task_decomposition(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer="fanout.writer",
            task_id="task_shared_resource_conflict",
            mode="milestone_plan",
            rationale="Shared files should force explicit serial handling.",
            decision={
                "estimated_step_count": 5,
                "risk_flags": ["shared_resource"],
                "requires_operator_checkpoint": False,
                "needs_ordering": True,
                "decision_summary": "Shared resource access cannot be inferred away.",
            },
            dependency_mapping={
                "fanout_policy": "explicit_safe_fanout",
                "summary": "Two milestones touch the same file, so fanout is unsafe.",
                "milestones": [
                    {
                        "milestone_id": "milestone_lock",
                        "depends_on": [],
                        "execution_mode": "serial_only",
                        "reason_codes": ["root_prerequisite"],
                        "reason_summary": "Lock down the shared file contract first.",
                        "caution_flags": ["shared_resource"],
                        "shared_resource_keys": ["infra/webui/server_lab_assistant_task_register.py"],
                    },
                    {
                        "milestone_id": "milestone_patch",
                        "depends_on": ["milestone_lock"],
                        "execution_mode": "fanout_safe",
                        "fanout_group_id": "unsafe_shared_file",
                        "reason_codes": ["shared_resource"],
                        "reason_summary": "This incorrectly tries to parallelize a contested file.",
                        "caution_flags": [],
                        "shared_resource_keys": ["infra/webui/server_lab_assistant_task_register.py"],
                    },
                ],
            },
            milestones=[
                {
                    "milestone_id": "milestone_lock",
                    "title": "Stabilize shared contract",
                    "rationale": "The shared file must be treated as contested.",
                    "exit_criteria": ["Shared-resource boundary is clear."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_lock",
                            "title": "Inspect shared file",
                            "rationale": "Confirm the contested write surface.",
                            "exit_criteria": ["The shared file set is known."],
                        }
                    ],
                },
                {
                    "milestone_id": "milestone_patch",
                    "title": "Apply dependent patch",
                    "rationale": "The real work happens after the shared contract is stable.",
                    "exit_criteria": ["Patch is applied safely."],
                    "checkpoints": [
                        {
                            "checkpoint_id": "checkpoint_patch",
                            "title": "Implement change",
                            "rationale": "Validation must reject shared-resource fanout.",
                            "exit_criteria": ["Change is complete."],
                        }
                    ],
                },
            ],
        )


def test_task_register_read_snapshot_is_pure_when_uninitialized(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    assert snapshot["task_register"] is None
    assert snapshot["assistant_state"] is None
    assert before == after


def test_task_register_malformed_snapshot_is_reported_as_corruption(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="malformed.writer",
        task={
            "task_id": "task_bad_snapshot",
            "kind": "software",
            "title": "Distinguish malformed snapshot",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "report corruption honestly",
        },
    )
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    register_path.write_text("{bad\n", encoding="utf-8")

    with pytest.raises(register.TaskRegisterError, match="task_register.json is malformed"):
        register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)


def test_task_register_snapshot_reports_corrupt_summary_cache_with_partial_write_hint(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="summary.corrupt.writer",
        task={
            "task_id": "task_summary_corrupt",
            "kind": "software",
            "title": "Detect malformed summary cache",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "report corrupt summary cache honestly",
        },
    )

    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    summary_path.write_text('{"register_generation": 1', encoding="utf-8")

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)

    assert snapshot["task_register"]["summary_stale"] is True
    assert "summary_cache_corrupt_or_partial" in snapshot["snapshot_projection"]["stale_reasons"]
    integrity = snapshot["integrity"]["summary_cache"]
    assert integrity["status"] == "malformed"
    assert integrity["partial_write_suspected"] is True


def test_task_register_snapshot_refresh_reports_missing_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="refresh.writer",
        task={
            "task_id": "task_refresh_projection",
            "kind": "software",
            "title": "Track projection freshness",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "regression for snapshot refresh",
        },
    )

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    register_obj = json.loads(register_path.read_text(encoding="utf-8"))
    register_obj.pop("snapshot_projection", None)
    register_path.write_text(json.dumps(register_obj, sort_keys=True), encoding="utf-8")

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)

    assert snapshot["snapshot_projection"]["stale"] is True
    assert "snapshot_projection_missing" in snapshot["snapshot_projection"]["stale_reasons"]
    assert snapshot["snapshot_projection"]["refresh_reason"] in snapshot["snapshot_projection"]["stale_reasons"]
    assert snapshot["snapshot_projection"]["refresh_mode"] == "lazy"


def test_task_register_snapshot_marks_summary_stale_when_summary_cache_generation_lags_register(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="summary.stale.writer",
        task={
            "task_id": "task_summary_cache_stale",
            "kind": "software",
            "title": "Keep snapshot honest when summary cache lags",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "report stale summary truthfully",
        },
    )["task_register"]

    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["register_generation"] = int(updated["write_generation"]) - 1
    summary["generated_utc"] = "2026-03-31T00:00:00Z"
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    persisted_register = json.loads(
        (tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json").read_text(encoding="utf-8")
    )

    assert snapshot["summary_cache"]["register_generation"] == int(updated["write_generation"])
    assert snapshot["summary_cache"]["active_task_count"] == 1
    assert snapshot["task_register"]["summary_stale"] is True
    assert persisted_register["summary_stale"] is False


def test_task_register_snapshot_rebuild_from_events_is_coherent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    first = register.load_or_init_task_register(tmp_path)
    register.upsert_task(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="replay.writer",
        task={
            "task_id": "task_replay_projection",
            "kind": "software",
            "title": "Rebuild snapshot projection from events",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "verify replayed projection staleness",
        },
    )

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_path.unlink()
    summary_path.unlink()

    replayed = register.load_or_init_task_register(tmp_path)
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)

    assert [task["task_id"] for task in replayed["active_tasks"]] == ["task_replay_projection"]
    assert [task["task_id"] for task in snapshot["task_register"]["active_tasks"]] == ["task_replay_projection"]
    assert snapshot["snapshot_projection"]["stale"] is False
    assert snapshot["snapshot_projection"]["refresh_writer"] == "replay.writer"
    assert snapshot["snapshot_projection"]["refresh_generation"] == int(replayed["write_generation"])


def test_task_register_snapshot_refresh_tracks_last_writer_and_generation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    first = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="mutate.writer.one",
        task={
            "task_id": "task_refresh_writer",
            "kind": "software",
            "title": "Track mutation refresh writer",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "check refresh metadata",
        },
    )

    snapshot_after_task = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    assert snapshot_after_task["snapshot_projection"]["stale"] is False
    assert snapshot_after_task["snapshot_projection"]["refresh_writer"] == "mutate.writer.one"
    assert snapshot_after_task["snapshot_projection"]["refresh_generation"] == int(first["task_register"]["write_generation"])

    second = register.record_execution_ref(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="mutate.writer.two",
        execution_ref={
            "kind": "test",
            "ref_id": "exec_ref_projection_1",
            "label": "projection refresh marker",
            "status": "active",
            "task_id": "task_refresh_writer",
            "ledger_uid": "PIS-20260326-203500Z-F3J6",
        },
    )["task_register"]

    snapshot_after_ref = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    assert snapshot_after_ref["snapshot_projection"]["stale"] is False
    assert snapshot_after_ref["snapshot_projection"]["refresh_writer"] == "mutate.writer.two"
    assert snapshot_after_ref["snapshot_projection"]["refresh_generation"] == int(second["write_generation"])
    assert snapshot_after_ref["snapshot_projection"]["refresh_reason"] == "eager_refresh"


def test_task_register_projection_refresh_does_not_mutate_inflight_execution_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="inflight.writer",
        task={
            "task_id": "task_inflight_projection_guard",
            "kind": "software",
            "title": "Protect in-flight projection state",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "ensure in-flight stability",
            "ledger_uid": "PIS-20260326-203500Z-F3J6",
        },
    )["task_register"]

    looped = register.upsert_open_loop(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="inflight.writer",
        open_loop={
            "loop_id": "loop_inflight_projection",
            "kind": "waiting_on_operator",
            "status": "open",
            "linked_task_id": "task_inflight_projection_guard",
            "note": "open-loop must remain unchanged",
        },
    )["task_register"]

    referenced = register.record_execution_ref(
        tmp_path,
        expected_generation=int(looped["write_generation"]),
        writer="inflight.writer",
        execution_ref={
            "kind": "test",
            "ref_id": "ref_inflight_projection_guard",
            "label": "inflight projection regression",
            "status": "active",
            "task_id": "task_inflight_projection_guard",
            "ledger_uid": "PIS-20260326-203500Z-F3J6",
        },
    )["task_register"]

    before = register.load_or_init_task_register(tmp_path)
    before_task = next(
        task
        for task in before["active_tasks"]
        if str(task.get("task_id") or "") == "task_inflight_projection_guard"
    )
    before_loops = [dict(loop) for loop in before["open_loops"]]
    before_refs = [dict(ref) for ref in before["recent_execution_refs"]]

    mutated = register.set_constraint(
        tmp_path,
        expected_generation=int(referenced["write_generation"]),
        writer="mutation.writer",
        constraint={"key": "projection_refresh_guard", "value": "true"},
    )["task_register"]

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)
    after = register.load_or_init_task_register(tmp_path)
    after_task = next(
        task
        for task in after["active_tasks"]
        if str(task.get("task_id") or "") == "task_inflight_projection_guard"
    )
    after_loops = [dict(loop) for loop in after["open_loops"]]
    after_refs = [dict(ref) for ref in after["recent_execution_refs"]]

    assert snapshot["snapshot_projection"]["refresh_reason"] == "eager_refresh"
    assert snapshot["snapshot_projection"]["refresh_writer"] == "mutation.writer"
    assert snapshot["snapshot_projection"]["refresh_generation"] == int(mutated["write_generation"])
    assert snapshot["snapshot_projection"]["stale"] is False
    assert after_task == before_task
    assert after_loops == before_loops
    assert after_refs == before_refs
    assert any(c["key"] == "projection_refresh_guard" for c in after["constraints"])
