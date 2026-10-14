from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_lab_assistant_web_operations as ops
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: dict | None = None) -> None:
        payload = json.dumps(body or {}).encode("utf-8")
        self.path = path
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def _operator_snapshot(
    utc: str = "2026-03-13T18:00:00Z",
    *,
    conflicts: list[dict] | None = None,
    delegation_proof: dict | None = None,
    active_tasks: list[dict] | None = None,
    completed_tasks: list[dict] | None = None,
    task_register: dict | None = None,
    execution_surface: dict | None = None,
    status_contract: dict | None = None,
) -> dict:
    return {
        "schema_version": "lab_assistant_operator_snapshot_v1",
        "utc": utc,
        "operator_state": "waiting_on_job",
        "active_tasks": active_tasks if active_tasks is not None else [{"task_id": "task-1"}],
        "completed_tasks": completed_tasks if completed_tasks is not None else [],
        "open_loops": [{"loop_id": "loop-1"}],
        "backend_projection": {"state": "reachable"},
        "task_register": task_register if task_register is not None else {"snapshot_projection": {"stale": False}},
        "execution_surface": execution_surface if execution_surface is not None else {"state": "fresh", "tasks": []},
        "status_contract": status_contract if status_contract is not None else {"contract_state": "fresh", "fields": []},
        "operator_summary": {
            "status_language": {
                "state": "waiting",
                "headline": "Work is waiting on an external step",
                "success_claim_allowed": False,
            },
            "truth_conflicts": conflicts if conflicts is not None else [],
        },
        "delegation_proof": delegation_proof
        if delegation_proof is not None
        else {
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False, "reason": ""},
            "report_paths": {},
        },
    }


def test_web_operations_snapshot_surfaces_queue_workflow_disagreement_and_drilldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": True,
                "status": "running",
                "status_detail": "workflow_running",
                "current_workflow_id": "wf-live-1",
                "current_workflow_status": "running",
            },
            "events": [],
        },
    )
    monkeypatch.setattr(
        ops._agent_orch,
        "list_workflows",
        lambda *, limit=50: {
            "ok": True,
            "count": 1,
            "items": [
                {
                    "workflow_id": "wf-live-1",
                    "status": "failed",
                    "updated_utc": "2026-03-13T18:00:00Z",
                    "binding": {"type": "ledger_uid", "id": "PIS-20260313-060105Z-U7V2"},
                }
            ],
        },
    )
    monkeypatch.setattr(
        ops._agent_orch,
        "get_status",
        lambda *, workflow_id, events_limit=120: {"workflow_id": workflow_id, "state": {"status": "failed"}},
    )
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-13T18:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [{"job_id": "sched-1"}])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["schema_version"] == "lab_assistant_web_operations_console_v1"
    assert payload["dashboard"]["status_language"]["state"] == "waiting"
    assert payload["drill_down"]["queue"]["api_path"] == "/api/mobile/backlog/execution_queue"
    assert payload["drill_down"]["workflow_current"]["api_path"].startswith("/api/agents/orchestration/status?workflow_id=wf-live-1")
    assert payload["disagreements"][0]["kind"] == "queue_workflow_status_disagreement"
    assert payload["disagreements"][0]["queue_status"] == "running"
    assert payload["disagreements"][0]["workflow_status"] == "failed"
    assert payload["error_triage"]["state"] == "conflicted"
    assert payload["error_triage"]["conflict_count"] == 1
    assert any(row["source"] == "queue_vs_workflow" for row in payload["error_triage"]["conflicts"])
    assert any(
        row["api_path"].startswith("/api/agents/orchestration/conversation?workflow_id=wf-live-1")
        for row in payload["forensic_replay"]["entry_points"]
    )
    assert any(
        "/api/agents/session?role=builder&ledger_uid=PIS-20260313-060105Z-U7V2" in row["api_path"]
        for row in payload["forensic_replay"]["entry_points"]
    )


def test_web_operations_control_stays_pending_until_durable_queue_truth_confirms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "active": False,
        "status": "idle",
        "status_detail": "idle",
        "current_workflow_id": "",
        "current_workflow_status": "",
        "stop_requested": False,
    }

    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(ops._execution_queue, "start_queue", lambda *, options=None: {"ok": True, "state": dict(state)})
    monkeypatch.setattr(ops._execution_queue, "status", lambda: {"state": dict(state), "events": []})
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    result = ops.invoke_runtime_control(tmp_path, action_id="execution_queue_start", writer="test")
    assert result["invocation"]["ok"] is True
    assert result["confirmation"]["state"] == "pending"

    state["active"] = True
    state["status"] = "running"
    state["status_detail"] = "workflow_running"

    snapshot = ops.operations_console_snapshot(tmp_path)
    first = snapshot["runtime_controls"]["recent_controls"][0]
    assert first["control_request_id"] == result["control_request_id"]
    assert first["confirmation"]["state"] == "confirmed"


def test_web_operations_watchdog_restart_control_and_activity_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(
        ops._watchdog_timer,
        "invoke_action",
        lambda action: {"ok": True, "action": action, "status": {"installed": True, "units": {"audit_timer": {"active_state": "active"}}}},
    )
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {"schema_version": "scheduler_watchdog_timer_status_v1", "installed": True, "units": {"audit_timer": {"active_state": "active"}}},
    )

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_selfheal.json").write_text(
        json.dumps(
            {
                "utc": "2026-04-02T04:10:00Z",
                "direct_watchdog_actions": [{"action": "repair_watchdog_latest_report_pointer", "ok": True}],
                "playbook_decisions": [{"decision_kind": "scheduler_execution_request", "condition_id": "execution_state_disagreement", "risk_level": "medium"}],
                "scheduler_work_items": [{"condition_id": "execution_state_disagreement", "scheduler_disposition": "ready_for_scheduler", "requested_actions": ["reconcile_scheduler_runtime_state"]}],
            }
        ),
        encoding="utf-8",
    )
    (reports_root / "latest_selfheal.md").write_text("line one\nline two\n", encoding="utf-8")
    maintenance_root = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance"
    maintenance_root.mkdir(parents=True, exist_ok=True)
    (maintenance_root / "events.jsonl").write_text(
        json.dumps({"schema_version": "scheduler_watchdog_maintenance_event_v1", "run_id": "wdrun_demo", "mode": "selfheal"}) + "\n",
        encoding="utf-8",
    )

    result = ops.invoke_runtime_control(tmp_path, action_id="watchdog_restart_timers", writer="test")
    activity = ops.watchdog_activity_snapshot(tmp_path, log_lines=20)

    assert result["invocation"]["ok"] is True
    assert result["invocation"]["response"]["action"] == "restart"
    assert activity["recent_log"]["mode"] == "selfheal"
    assert activity["recent_log"]["lines"] == ["line one", "line two"]
    assert any(row["source"] == "direct_watchdog_action" for row in activity["recent_actions"])
    assert any(row["source"] == "scheduler_work_item" for row in activity["recent_actions"])
    assert activity["recent_maintenance_events"][0]["run_id"] == "wdrun_demo"


def test_watchdog_activity_snapshot_uses_generated_utc_for_newest_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_audit.json").write_text(json.dumps({"generated_utc": "2026-04-02T04:00:00Z"}), encoding="utf-8")
    (reports_root / "latest_selfheal.json").write_text(json.dumps({"generated_utc": "2026-04-02T05:00:00Z"}), encoding="utf-8")
    (reports_root / "latest_selfheal.md").write_text("selfheal latest\n", encoding="utf-8")

    activity = ops.watchdog_activity_snapshot(tmp_path, log_lines=20)

    assert activity["recent_log"]["mode"] == "selfheal"
    assert activity["recent_log"]["lines"] == ["selfheal latest"]


def test_web_operations_watchdog_projection_flags_false_calm_from_open_incident_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {"schema_version": "scheduler_watchdog_timer_status_v1", "installed": True, "units": {}},
    )

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_audit.json").write_text(
        json.dumps({"generated_utc": "2026-04-02T04:00:00Z", "health": {"health": "healthy"}, "root_causes": [], "incidents": [], "predictions": []}),
        encoding="utf-8",
    )
    incidents_root = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents"
    incidents_root.mkdir(parents=True, exist_ok=True)
    (incidents_root / "latest.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-04-02T04:10:00Z",
                "open_count": 1,
                "incidents": [{"incident_id": "incident__truth_ambiguous", "condition_id": "truth_ambiguous", "state": "open", "risk_level": "high"}],
            }
        ),
        encoding="utf-8",
    )

    payload = ops.operations_console_snapshot(tmp_path)

    alertability = payload["watchdog"]["latest"]["audit"]["alertability"]
    assert alertability["false_calm_detected"] is True
    assert alertability["detectability_lag_seconds"] == 600
    assert payload["watchdog"]["latest"]["audit"]["incident_count"] == 1
    assert any(row["code"] == "watchdog_false_calm_detected" for row in payload["error_triage"]["gaps"])
    assert any(row["code"] == "watchdog_detectability_lag" for row in payload["error_triage"]["gaps"])


def test_web_operations_watchdog_projection_flags_wrong_cause_when_incident_and_root_cause_disagree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {"schema_version": "scheduler_watchdog_timer_status_v1", "installed": True, "units": {}},
    )

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_audit.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-04-02T04:10:00Z",
                "health": {"health": "degraded"},
                "root_causes": [{"condition_id": "runtime_storage_health"}],
                "incidents": [],
                "predictions": [],
            }
        ),
        encoding="utf-8",
    )
    incidents_root = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents"
    incidents_root.mkdir(parents=True, exist_ok=True)
    (incidents_root / "latest.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-04-02T04:10:00Z",
                "open_count": 1,
                "incidents": [{"incident_id": "incident__truth_ambiguous", "condition_id": "truth_ambiguous", "state": "open", "risk_level": "high"}],
            }
        ),
        encoding="utf-8",
    )

    payload = ops.operations_console_snapshot(tmp_path)

    alertability = payload["watchdog"]["latest"]["audit"]["alertability"]
    assert alertability["wrong_cause_detected"] is True
    assert any(row["code"] == "watchdog_wrong_cause_projection" for row in payload["error_triage"]["gaps"])


def test_web_operations_triage_uses_newer_worse_selfheal_alertability_not_just_healthy_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {"schema_version": "scheduler_watchdog_timer_status_v1", "installed": True, "units": {}},
    )

    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_audit.json").write_text(
        json.dumps({"generated_utc": "2026-04-02T04:00:00Z", "health": {"health": "healthy"}, "root_causes": [], "incidents": [], "predictions": []}),
        encoding="utf-8",
    )
    (reports_root / "latest_selfheal.json").write_text(
        json.dumps({"generated_utc": "2026-04-02T04:20:00Z", "health": {"health": "healthy"}, "root_causes": [], "incidents": [], "predictions": []}),
        encoding="utf-8",
    )
    incidents_root = tmp_path / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents"
    incidents_root.mkdir(parents=True, exist_ok=True)
    (incidents_root / "latest.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-04-02T04:20:00Z",
                "open_count": 1,
                "incidents": [{"incident_id": "incident__truth_ambiguous", "condition_id": "truth_ambiguous", "state": "open", "risk_level": "high"}],
            }
        ),
        encoding="utf-8",
    )

    payload = ops.operations_console_snapshot(tmp_path)

    selfheal_alertability = payload["watchdog"]["latest"]["selfheal"]["alertability"]
    assert selfheal_alertability["false_calm_detected"] is True
    assert any(row["code"] == "watchdog_false_calm_detected" for row in payload["error_triage"]["gaps"])
    assert payload["error_triage"]["state"] == "degraded"


def test_web_operations_triage_surfaces_partial_queue_failure_with_remaining_backlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: _operator_snapshot())
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": False,
                "status": "failed",
                "status_detail": "failed",
                "current_workflow_id": "",
                "current_workflow_status": "",
                "stop_requested": False,
                "last_failed_stage": "workflow",
            },
            "events": [],
            "batch_report": {
                "outcome": "failed_partial",
                "remaining_count": 3,
                "failed_count": 1,
                "completed_count": 2,
            },
        },
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {"schema_version": "scheduler_watchdog_timer_status_v1", "installed": True, "units": {}},
    )

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["error_triage"]["state"] == "degraded"
    assert any(row["code"] == "execution_queue_remaining_backlog_after_partial_failure" for row in payload["error_triage"]["gaps"])


def test_web_operations_refresh_scope_and_confirmation_wording_are_honest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_snapshot = _operator_snapshot(utc="2026-03-13T18:00:00Z")
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: operator_snapshot)
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": False,
                "status": "idle",
                "status_detail": "idle",
                "current_workflow_id": "",
                "current_workflow_status": "",
                "stop_requested": False,
            },
            "events": [],
        },
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-13T18:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(ops, "_utc_iso", lambda value=None: "2026-03-13T18:00:00Z")

    result = ops.invoke_runtime_control(tmp_path, action_id="refresh_runtime", writer="test")
    assert result["confirmation"]["state"] == "confirmed"
    assert "future work" in result["confirmation"]["summary"].lower()
    assert "in-flight turns/jobs are unchanged" in result["confirmation"]["summary"].lower()

    snapshot = ops.operations_console_snapshot(tmp_path)
    controls = snapshot["runtime_controls"]
    refresh_action = next(row for row in controls["available_actions"] if row["action_id"] == "refresh_runtime")
    assert "future work" in refresh_action["summary"].lower()
    assert "in-flight turns/jobs keep" in refresh_action["summary"].lower()
    assert controls["refresh_scope"]["applies_to"] == "future_work_started_after_refresh"
    assert controls["refresh_scope"]["does_not_apply_to"] == "in_flight_turns_or_jobs"
    assert controls["refresh_scope"]["summary"]
    assert controls["refresh_scope"]["claim_ceiling_summary"]


def test_web_operations_refresh_confirmation_accepts_generated_utc_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_snapshot = _operator_snapshot(utc="2026-03-13T18:00:00Z")
    operator_snapshot.pop("utc", None)
    operator_snapshot["generated_utc"] = "2026-03-13T18:00:00Z"
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: operator_snapshot)
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False},
            "events": [],
        },
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-13T18:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(ops, "_utc_iso", lambda value=None: "2026-03-13T18:00:00Z")

    result = ops.invoke_runtime_control(tmp_path, action_id="refresh_runtime", writer="test")

    assert result["confirmation"]["state"] == "confirmed"
    assert result["confirmation"]["durable_signal"] == "refresh_response_ok_and_operator_snapshot.generated_utc>=requested_utc"


def test_web_operations_triage_treats_proof_gap_as_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: _operator_snapshot(
            delegation_proof={
                "summary": {"overall_status": "proof_gap"},
                "freshness": {"stale": False, "reason": ""},
                "report_paths": {},
            }
        ),
    )
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["error_triage"]["state"] == "degraded"
    assert payload["error_triage"]["gap_count"] >= 1
    assert any(row["code"] == "delegation_proof_missing_or_stale" for row in payload["error_triage"]["gaps"])


def test_web_operations_triage_treats_snapshot_projection_and_status_contract_degradation_as_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: _operator_snapshot(
            task_register={"snapshot_projection": {"stale": True}},
            status_contract={"contract_state": "degraded", "fields": []},
            execution_surface={"state": "degraded", "tasks": []},
            active_tasks=[
                {
                    "task_id": "task-1",
                    "status_contract": {
                        "task_id": "task-1",
                        "contract_state": "degraded",
                        "mismatch": True,
                        "mismatch_reason": "projected_status_differs_from_task_register",
                    }
                }
            ],
        ),
    )
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": "", "stop_requested": False}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["error_triage"]["state"] == "conflicted"
    assert any(row["source"] == "task_status_contract" for row in payload["error_triage"]["conflicts"])
    assert any(row["code"] == "task_register_snapshot_projection_stale" for row in payload["error_triage"]["gaps"])
    assert any(row["code"] == "status_contract_degraded" for row in payload["error_triage"]["gaps"])
    assert any(row["code"] == "execution_surface_projection_degraded" for row in payload["error_triage"]["gaps"])


@pytest.mark.parametrize(
    ("operator_snapshot", "expected_state", "expected_conflict_count", "expected_gap_codes"),
    [
        (
            _operator_snapshot(
                conflicts=[],
                task_register={"snapshot_projection": {"stale": True}},
                active_tasks=[],
            ),
            "degraded",
            0,
            {"task_register_snapshot_projection_stale"},
        ),
        (
            _operator_snapshot(
                conflicts=[],
                status_contract={"contract_state": "degraded", "fields": []},
                active_tasks=[],
            ),
            "degraded",
            0,
            {"status_contract_degraded"},
        ),
        (
            _operator_snapshot(
                conflicts=[],
                execution_surface={"state": "degraded", "tasks": []},
                active_tasks=[],
            ),
            "degraded",
            0,
            {"execution_surface_projection_degraded"},
        ),
        (
            _operator_snapshot(
                conflicts=[{"kind": "canonical_run_blocked_but_projection_active", "summary": "run conflict"}],
            ),
            "conflicted",
            1,
            set(),
        ),
    ],
)
def test_web_operations_triage_matrix_keeps_projection_and_conflict_truth_honest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_snapshot: dict,
    expected_state: str,
    expected_conflict_count: int,
    expected_gap_codes: set[str],
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: operator_snapshot)
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": False,
                "status": "idle",
                "status_detail": "idle",
                "current_workflow_id": "",
                "current_workflow_status": "",
                "stop_requested": False,
            },
            "events": [],
        },
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    triage = payload["error_triage"]
    assert triage["state"] == expected_state
    assert triage["conflict_count"] == expected_conflict_count
    assert {row["code"] for row in triage["gaps"]} >= expected_gap_codes


def test_web_operations_forensic_artifact_inspection_surfaces_classified_refs_and_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: _operator_snapshot(
            conflicts=[
                {
                    "kind": "register_completed_but_projection_incomplete",
                    "summary": "Task register says completed but projection remains incomplete.",
                    "task_id": "task-1",
                }
            ],
            active_tasks=[
                {
                    "task_id": "task-1",
                    "evidence_refs": [
                        "runtime/state/lab_assistant/execution_reconciliation/reports/recon_1.json",
                        "runtime/state/lab_assistant/execution_reconciliation/receipts/recon_1.json",
                        "runtime/artifacts/scheduled_jobs/repo_residue/summary.json",
                    ],
                }
            ],
        ),
    )
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": False,
                "status": "idle",
                "status_detail": "idle",
                "current_workflow_id": "",
                "current_workflow_status": "",
            },
            "events": [],
        },
    )
    monkeypatch.setattr(
        ops._agent_orch,
        "list_workflows",
        lambda *, limit=50: {
            "ok": True,
            "count": 1,
            "items": [
                {
                    "workflow_id": "wf-u7v2-1",
                    "status": "completed",
                    "binding": {"type": "ledger_uid", "id": "PIS-20260313-060105Z-U7V2"},
                    "updated_utc": "2026-03-13T18:00:00Z",
                }
            ],
        },
    )
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-13T18:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [{"job_id": "sched-u7v2"}])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["error_triage"]["state"] == "conflicted"
    assert payload["error_triage"]["conflict_count"] == 1
    inspection = payload["forensic_replay"]["artifact_inspection"]
    assert inspection["evidence_ref_count"] == 3
    assert inspection["classified_ref_counts"]["report"] == 1
    assert inspection["classified_ref_counts"]["receipt"] == 1
    assert inspection["classified_ref_counts"]["residue"] == 1
    assert any("/api/schedules/job?job_id=sched-u7v2" in row["api_path"] for row in inspection["inspection_links"])


def test_web_operations_honestly_degrades_when_proof_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: _operator_snapshot(
            delegation_proof={
                "summary": {"overall_status": "missing"},
                "freshness": {"stale": True, "reason": "missing_proof_report"},
                "report_paths": {},
            },
            active_tasks=[{"task_id": "task-1", "evidence_refs": []}],
        ),
    )
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": True,
                "status": "running",
                "status_detail": "workflow_running",
                "current_workflow_id": "wf-missing",
                "current_workflow_status": "running",
            },
            "events": [],
        },
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._agent_orch, "get_status", lambda *, workflow_id, events_limit=120: {"workflow_id": workflow_id, "error": "workflow not found"})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-13T18:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["error_triage"]["state"] == "degraded"
    assert payload["error_triage"]["gap_count"] >= 2
    assert any(row["code"] == "delegation_proof_missing_or_stale" for row in payload["error_triage"]["gaps"])
    assert any(row["code"] == "workflow_detail_unavailable" for row in payload["forensic_replay"]["gaps"])


def test_admin_web_operations_routes_expose_snapshot_and_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._lab_web_ops,
        "operations_console_snapshot",
        lambda repo_root, workflow_limit=50, control_limit=30: {
            "schema_version": "lab_assistant_web_operations_console_v1",
            "dashboard": {"queue_active": False},
            "runtime_controls": {"recent_controls": []},
            "disagreements": [],
        },
    )
    monkeypatch.setattr(
        routes._lab_web_ops,
        "invoke_runtime_control",
        lambda repo_root, action_id, params=None, writer="": {
            "schema_version": "lab_assistant_web_operations_control_result_v1",
            "action_id": action_id,
            "confirmation": {"state": "pending"},
        },
    )

    get_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/web_operations")
    routes.WebUIRequestHandler.do_GET(get_req)  # type: ignore[arg-type]
    assert get_req._status == 200
    assert _read_payload(get_req)["schema_version"] == "lab_assistant_web_operations_console_v1"

    post_req = _DummyReq(
        cfg=cfg,
        path="/api/admin/lab_assistant/web_operations/control",
        body={"action_id": "execution_queue_start", "params": {}, "writer": "test"},
    )
    routes.WebUIRequestHandler.do_POST(post_req)  # type: ignore[arg-type]
    assert post_req._status == 200
    assert _read_payload(post_req)["action_id"] == "execution_queue_start"


def test_operations_console_snapshot_includes_watchdog_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda repo_root: {"operator_state": "ok", "operator_summary": {}, "backend_projection": {}, "active_tasks": [], "open_loops": []})
    monkeypatch.setattr(ops._execution_queue, "status", lambda: {"state": {"active": False, "status": "idle"}})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-03-29T00:00:00Z"})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda repo_root=None: [])
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda limit=50: {"count": 0, "items": []})
    monkeypatch.setattr(
        ops._watchdog_timer,
        "timer_status_snapshot",
        lambda: {
            "schema_version": "scheduler_watchdog_timer_status_v1",
            "installed": True,
            "units": {
                "audit_timer": {"active_state": "active", "unit_file_state": "enabled"},
                "selfheal_timer": {"active_state": "active", "unit_file_state": "enabled"},
            },
        },
    )
    reports_root = tmp_path / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "latest_audit.json").write_text(
        json.dumps(
            {
                "utc": "2026-03-29T20:00:00Z",
                "ok": True,
                "health": {"health": "healthy"},
                "summary": {"enabled_jobs": 1},
                "truth_reconciliation": {"overall_state": "truth_ok", "fact_count": 2, "ambiguous_fact_count": 0},
                "authority_boundary": {
                    "execution_authority": "scheduler_plus_bounded_watchdog_progress_recovery",
                    "watchdog_role": "diagnose_classify_and_bounded_progress_recover",
                    "codex_role": "advisory_or_bounded_repair_only",
                    "direct_watchdog_mutation_forbidden": ["scheduler_job_state"],
                },
                "maintenance_ledger": {"run_id": "run_demo", "scheduler_work_item_count": 1},
                "playbook_decisions": [{"decision_id": "dec_1", "condition_id": "execution_state_disagreement"}],
                "scheduler_work_items": [{"request_id": "req_1", "condition_id": "execution_state_disagreement"}],
                "containments": [],
                "incidents": [{"incident_id": "incident__execution_state_disagreement", "condition_id": "execution_state_disagreement"}],
                "predictions": [],
                "escalation_bundles": [],
                "root_causes": [{"condition_id": "execution_state_disagreement", "risk_level": "medium", "summary": "demo", "finding_count": 1}],
            }
        ),
        encoding="utf-8",
    )

    payload = ops.operations_console_snapshot(tmp_path)

    watchdog = payload["runtime_controls"]["watchdog"]
    assert payload["watchdog"]["timer_status"]["installed"] is True
    assert watchdog["timer_status"]["installed"] is True
    assert watchdog["latest"]["audit"]["ok"] is True
    assert watchdog["latest"]["audit"]["health"]["health"] == "healthy"
    assert watchdog["latest"]["audit"]["truth_reconciliation"]["overall_state"] == "truth_ok"
    assert watchdog["latest"]["audit"]["authority_boundary"]["execution_authority"] == "scheduler_plus_bounded_watchdog_progress_recovery"
    assert watchdog["latest"]["audit"]["maintenance_ledger"]["run_id"] == "run_demo"
    assert watchdog["latest"]["audit"]["scheduler_request_count"] == 1
    assert watchdog["latest"]["audit"]["incident_count"] == 1
    assert watchdog["workstream_contract"]["parent_uid"] == "PIS-20260402-062800Z-U1J7"
    assert watchdog["workstream_contract"]["decision_only_program"] is False
    assert any(row["action_id"] == "watchdog_step_selfheal" for row in payload["runtime_controls"]["available_actions"])


def test_web_operations_surface_projection_state_and_snapshot_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_snapshot = _operator_snapshot(
        task_register={"snapshot_projection": {"stale": True}},
        execution_surface={"state": "fresh", "tasks": []},
        status_contract={"state": "degraded", "contract_state": "degraded", "fields": []},
    )
    operator_snapshot["backend_projection"] = {
        "state": "stale",
        "uses_cached_projection": True,
        "projection_stale": True,
    }
    operator_snapshot["projection_contract"] = {
        "default_operator_projection": {
            "api_path": "/api/admin/lab_assistant/snapshot",
            "canonical_for_operator_use": True,
        },
        "ownership": {
            "projection_owner": "web_console_projection",
            "canonical_truth_owner": "BD05.task_register",
        },
        "projection_state": {
            "cached_truth_must_be_marked": True,
        },
    }

    monkeypatch.setattr(ops._lab_operator_snapshot, "build_operator_snapshot", lambda _root: operator_snapshot)
    monkeypatch.setattr(ops._execution_queue, "status", lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle"}, "events": []})
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["dashboard"]["projection_state"]["state"] == "stale"
    assert payload["dashboard"]["projection_state"]["cached_truth_must_be_marked"] is True
    assert payload["dashboard"]["projection_state"]["canonical_operator_projection_api_path"] == "/api/admin/lab_assistant/snapshot"
    assert payload["projection_contract"]["default_operator_projection"]["canonical_for_operator_use"] is True
