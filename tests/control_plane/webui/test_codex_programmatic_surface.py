from __future__ import annotations

from pathlib import Path

import pytest

from infra.webui import server_agents
from infra.webui import server_codex_programmatic_surface as codex_surface
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as register


class _FakeCodexManager:
    def ensure_worker(self, *, binding, role, bootstrap_text):
        return {"ok": True, "worker_id": "worker-codex-surface"}

    def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
        return {
            "ok": True,
            "worker_id": "worker-codex-surface",
            "protocol": "oneshot",
            "exit_code": 0,
            "content": "Durable Codex answer",
        }


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    (root / "governance" / "codex_migration_exceptions.json").write_text(
        "{"
        "\"schema_version\":\"codex_migration_exceptions_v1\","
        "\"entries\":[{"
        "\"exception_id\":\"legacy-register-work\","
        "\"script_path\":\"tools/legacy/register_work.py\","
        "\"reason\":\"Temporary migration bridge while legacy Codex callers are moved onto the canonical programmatic surface.\","
        "\"added_utc\":\"2026-03-27T13:28:25Z\""
        "}]"
        "}\n",
        encoding="utf-8",
    )


def _patch_codex_send_message_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        server_agents,
        "load_prompt_bundle",
        lambda role, binding, prompt_file=None, workflow=False: {
            "instructions": "builder prompt",
            "base": {"sha256": "base"},
            "role": {"sha256": "role"},
        },
    )
    monkeypatch.setattr(server_agents, "_build_repo_context", lambda message, context_rows: {"text": "", "files": []})
    monkeypatch.setattr(server_agents, "_refresh_mobile_bubble_projection", lambda **kwargs: None)
    monkeypatch.setattr(server_agents, "_maybe_dispatch_direct_host_verification", lambda **kwargs: {})
    monkeypatch.setattr(server_agents, "_codex_manager", lambda: _FakeCodexManager())


def test_codex_surface_register_launch_and_evidence_writeback_use_canonical_task_run_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    registered = codex_surface.register_codex_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="codex.surface.register",
        task={
            "task_id": "cdx01",
            "kind": "software",
            "title": "Codex durable task",
            "status": "active",
            "ledger_uid": "PIS-20260327-132825Z-0EEC",
            "next_expected_action": "launch codex run",
        },
    )
    launched = codex_surface.launch_codex_run(
        tmp_path,
        expected_generation=int(registered["task_register"]["write_generation"]),
        writer="codex.surface.launch",
        task_id="cdx01",
        run_id="codex_run_001",
        execution_ref="governance/agents/runtime/workers_state.json#codex_run_001",
        binding_ref="ledger_uid::PIS-20260327-132825Z-0EEC::builder",
        job_uri="governance/agents/runtime/workers_state.json",
        trace_id="trace_codex_001",
    )
    written = codex_surface.write_codex_run_evidence(
        tmp_path,
        expected_generation=int(launched["task_register"]["write_generation"]),
        writer="codex.surface.writeback",
        task_id="cdx01",
        run_id="codex_run_001",
        summary="checkpoint persisted",
        evidence_refs=["runtime/artifacts/codex/checkpoint.json"],
        payload={"kind": "builder_report", "status": "ok"},
        progress_percent=40.0,
    )

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(row for row in snapshot["active_tasks"] if row["task_id"] == "cdx01")
    run = next(row for row in task["task_runs"] if row["run_id"] == "codex_run_001")
    events = codex_surface.read_codex_surface_events(tmp_path)

    assert task["execution_surface_ownership"]["selected_surface"] == "codex_worker"
    assert task["current_run_id"] == "codex_run_001"
    assert run["checkpoint"]["checkpoint_status"] == "recorded"
    assert run["checkpoint"]["progress_summary"] == "checkpoint persisted"
    assert run["checkpoint"]["evidence_refs"][0] == "runtime/artifacts/codex/checkpoint.json"
    assert written["payload_ref"]
    assert (tmp_path / written["payload_ref"]).exists()
    assert run["resume_decision"]["decision"] == "resume"
    assert [row["event"] for row in events] == ["task_registered", "run_launched", "run_evidence_written"]


def test_codex_surface_public_contract_is_documented() -> None:
    assert "Codex-managed durable work" in (codex_surface.__doc__ or "")
    assert "canonical task truth" in (codex_surface.register_codex_task.__doc__ or "")
    assert "supported launch path for durable Codex work" in (codex_surface.launch_codex_run.__doc__ or "")
    assert "canonical run truth" in (codex_surface.write_codex_run_evidence.__doc__ or "")
    assert "Direct mutation of managed state paths is blocked" in (codex_surface.enforce_codex_migration_guard.__doc__ or "")


def test_codex_send_message_path_writes_canonical_run_truth_via_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    _patch_codex_send_message_runtime(tmp_path, monkeypatch)

    result = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="implement the codex surface wiring in server agents",
    )

    task_id = str(result.get("task_id") or "")
    turn_id = str((result.get("turn") or {}).get("turn_id") or "")
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(row for row in snapshot["active_tasks"] if row["task_id"] == task_id)
    run = next(row for row in task["task_runs"] if row["run_id"] == turn_id)
    events = codex_surface.read_codex_surface_events(tmp_path)

    assert result["ok"] is True
    assert task["current_run_id"] == turn_id
    assert task["execution_surface_ownership"]["selected_surface"] == "codex_worker"
    assert run["checkpoint"]["checkpoint_status"] == "recorded"
    assert run["checkpoint"]["progress_summary"] == "Durable Codex answer"
    assert run["checkpoint"]["evidence_refs"][0] == "governance/agents/sessions/a7k/session__builder.jsonl"
    assert run["checkpoint"]["evidence_refs"][1].startswith("runtime/state/lab_assistant/codex_surface/payloads/")
    assert [row["event"] for row in events] == ["run_launched", "run_evidence_written"]


def test_codex_migration_guard_rejects_unallowlisted_direct_mutation_of_managed_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"

    with pytest.raises(codex_surface.CodexMigrationGuardError):
        codex_surface.enforce_codex_migration_guard(
            tmp_path,
            writer="codex.surface.guard",
            operation="write_text",
            target_path=str(target),
            caller_path="tools/scripts/direct_task_register_edit.py",
            task_id="cdx01",
        )

    events = codex_surface.read_codex_migration_events(tmp_path)
    assert len(events) == 1
    assert events[0]["status"] == "rejected"
    assert events[0]["target_path"] == "runtime/state/lab_assistant/task_register/task_register.json"


def test_codex_migration_guard_allows_allowlisted_legacy_script_but_records_exception(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = "runtime/state/lab_assistant/task_register/task_register.json"

    result = codex_surface.enforce_codex_migration_guard(
        tmp_path,
        writer="codex.surface.guard",
        operation="append_jsonl",
        target_path=target,
        caller_path="tools/legacy/register_work.py",
        task_id="cdx02",
    )

    events = codex_surface.read_codex_migration_events(tmp_path)
    assert result["guarded"] is True
    assert result["migration_exception"]["exception_id"] == "legacy-register-work"
    assert len(events) == 1
    assert events[0]["status"] == "migration_exception"
    assert events[0]["migration_exception"]["script_path"] == "tools/legacy/register_work.py"
