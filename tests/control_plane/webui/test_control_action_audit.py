from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments_shared.experiment_records import build_initial_run_record
from experiments_shared.experiment_records import write_run_record
from infra.webui import server_control_action_audit as audit
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


def _audit_rows(repo_root: Path) -> list[dict]:
    path = audit.events_path(repo_root)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_run_record(
    repo_root: Path,
    *,
    run_id: str = "run-001",
    project_id: str = "demo",
    state: str = "running",
    confidence: str = "durable",
    terminal: bool | None = None,
) -> Path:
    run_dir = repo_root / "wrapper" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record = build_initial_run_record(
        run_id=run_id,
        job_id="job-001",
        job_spec_hash="sha256:job",
        project_id=project_id,
        created_at_utc="2026-04-09T05:00:00Z",
        launch={"entry_module": f"experiments.projects.{project_id}.runner"},
    )
    lifecycle = dict(record.get("lifecycle") or {})
    lifecycle["state"] = state
    lifecycle["confidence"] = confidence
    lifecycle["terminal"] = bool(terminal) if terminal is not None else state in {"succeeded", "failed", "skipped", "cancelled"}
    lifecycle["transition_reason"] = "test_fixture"
    if lifecycle["terminal"]:
        lifecycle["ended_at_utc"] = "2026-04-09T05:01:00Z"
        lifecycle["exit_code"] = 1 if state == "failed" else 0
    record["lifecycle"] = lifecycle
    write_run_record(run_dir, record)
    return run_dir


def test_admin_server_refresh_runtime_writes_durable_audit_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/server/refresh_runtime")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {
            "schema_version": "admin_server_runtime_refresh_v1",
            "utc": "2026-04-09T05:05:00Z",
            "ok": True,
            "message": "Live runtime settings refreshed for future work. In-flight turns/jobs keep prior settings.",
            "scope": {
                "applies_to": "future_work_started_after_refresh",
                "does_not_apply_to": "in_flight_turns_or_jobs",
            },
            "runtime_refresh_boundary": {
                "future_turn_only_keys": ["chat_header_title"],
                "future_job_only_keys": [],
                "restart_required_keys": [],
            },
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["ok"] is True

    rows = _audit_rows(tmp_path)
    assert len(rows) == 2
    assert rows[-1]["action_id"] == "refresh_runtime"
    assert rows[-1]["finalized"] is True
    assert rows[-1]["verification"]["state"] == "confirmed"
    assert rows[-1]["result"]["outcome"] == "applied"
    assert rows[-1]["policy_snapshot"]["refresh_scope"]["applies_to"] == "future_work_started_after_refresh"


def test_admin_server_restart_denial_is_recorded_as_final_audit_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/server/restart", body={})
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_admin_restart_preflight",
        lambda _cfg, state=None: {
            "schema_version": "admin_server_restart_preflight_v1",
            "utc": "2026-04-09T05:10:00Z",
            "ok": False,
            "safe_to_restart": False,
            "blockers": [{"kind": "scheduler_job_locks_present", "count": 1}],
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    rows = _audit_rows(tmp_path)
    assert len(rows) == 2
    assert rows[-1]["action_id"] == "server_restart"
    assert rows[-1]["finalized"] is True
    assert rows[-1]["decision"]["state"] == "denied"
    assert rows[-1]["result"]["outcome"] == "denied"


def test_restart_reconciliation_marks_nonfinal_control_action_interrupted_after_restart(tmp_path: Path) -> None:
    request = audit.begin_control_action(
        tmp_path,
        action_id="server_restart",
        actor={"kind": "admin_guard"},
        params={"force": False},
        writer="test.restart",
        request_path="/api/admin/server/restart",
        target={"surface": "the plane", "resource": "webui_server"},
        authorization={"state": "authorized", "method": "admin_guard"},
        decision={"state": "accepted", "reason_code": "restart_scheduled"},
        executor={"state": "restart_scheduled"},
        verification={"state": "pending_restart_reconciliation"},
        result={"outcome": "accepted", "reason_code": "restart_scheduled"},
    )

    summary = audit.reconcile_after_restart(tmp_path, writer="test.startup")

    assert summary["reconciled_count"] == 1
    history = audit.recent_control_action_history(tmp_path, limit=5)
    assert history[0]["audit_id"] == request["audit_id"]
    assert history[0]["finalized"] is True
    assert history[0]["verification"]["state"] == "interrupted_after_restart"
    assert history[0]["result"]["outcome"] == "interrupted"


def test_restart_reconciliation_confirms_realized_state_from_durable_run_truth(tmp_path: Path) -> None:
    run_dir = _write_run_record(tmp_path, state="running", confidence="durable")
    request = audit.begin_control_action(
        tmp_path,
        action_id="restart",
        actor={"kind": "operator", "token_id": "token-1"},
        writer="test.restart.realized",
        request_path="/api/admin/experiment/run/control",
        target={"surface": "the plane", "resource": "experiment_run", "run_dir": str(run_dir)},
        correlation={"experiment_id": "demo", "run_id": "run-001", "job_id": "job-001"},
        policy_snapshot={"policy_hash": "policy-123"},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "restart_scheduled"},
        verification={"state": "pending_restart_reconciliation", "expected_lifecycle_states": ["running"]},
        result={"outcome": "accepted"},
    )

    summary = audit.reconcile_after_restart(tmp_path, writer="test.startup")

    assert summary["reconciled_count"] == 1
    history = audit.recent_control_action_history(tmp_path, limit=5)
    assert history[0]["audit_id"] == request["audit_id"]
    assert history[0]["verification"]["state"] == "realized_state_confirmed"
    assert history[0]["verification"]["run_truth"]["state"] == "running"
    assert history[0]["result"]["outcome"] == "realized"
    assert history[0]["correlation"]["experiment_id"] == "demo"
    assert history[0]["correlation"]["operator_identity"]["token_id"] == "token-1"


def test_restart_reconciliation_marks_unmatched_durable_run_truth_failed(tmp_path: Path) -> None:
    run_dir = _write_run_record(tmp_path, state="failed", confidence="durable", terminal=True)
    audit.begin_control_action(
        tmp_path,
        action_id="restart",
        actor={"kind": "operator"},
        writer="test.restart.failed",
        request_path="/api/admin/experiment/run/control",
        target={"surface": "the plane", "resource": "experiment_run", "run_dir": str(run_dir)},
        correlation={"experiment_id": "demo", "run_id": "run-001"},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "restart_scheduled"},
        verification={"state": "pending_restart_reconciliation", "expected_lifecycle_states": ["running"]},
        result={"outcome": "accepted"},
    )

    summary = audit.reconcile_after_restart(tmp_path, writer="test.startup")

    assert summary["reconciled_count"] == 1
    history = audit.recent_control_action_history(tmp_path, limit=5)
    assert history[0]["verification"]["state"] == "failed"
    assert history[0]["verification"]["run_truth"]["state"] == "failed"
    assert history[0]["result"]["outcome"] == "failed"
    assert history[0]["result"]["reason_code"] == "realized_state_not_confirmed"


def test_restart_reconciliation_marks_uncertain_durable_run_truth_uncertain(tmp_path: Path) -> None:
    run_dir = _write_run_record(tmp_path, state="uncertain_interrupted", confidence="uncertain")
    audit.begin_control_action(
        tmp_path,
        action_id="restart",
        actor={"kind": "operator"},
        writer="test.restart.uncertain",
        request_path="/api/admin/experiment/run/control",
        target={"surface": "the plane", "resource": "experiment_run", "run_dir": str(run_dir)},
        correlation={"experiment_id": "demo", "run_id": "run-001"},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "restart_scheduled"},
        verification={"state": "pending_restart_reconciliation", "expected_lifecycle_states": ["running"]},
        result={"outcome": "accepted"},
    )

    summary = audit.reconcile_after_restart(tmp_path, writer="test.startup")

    assert summary["reconciled_count"] == 1
    history = audit.recent_control_action_history(tmp_path, limit=5)
    assert history[0]["verification"]["state"] == "realization_uncertain_after_restart"
    assert history[0]["verification"]["run_truth"]["state"] == "uncertain_interrupted"
    assert history[0]["result"]["outcome"] == "uncertain"
    assert history[0]["result"]["reason_code"] == "restart_reconciliation_uncertain"


def test_operations_console_surfaces_control_action_audit_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit.begin_control_action(
        tmp_path,
        action_id="refresh_runtime",
        actor={"kind": "admin_guard"},
        writer="test.refresh",
        request_path="/api/admin/server/refresh_runtime",
        target={"surface": "the plane", "resource": "runtime_policy_snapshots"},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "running"},
        verification={"state": "pending"},
    )
    audit.finalize_control_action(
        tmp_path,
        audit_id=audit.recent_control_action_history(tmp_path, limit=1)[0]["audit_id"],
        action_id="refresh_runtime",
        actor={"kind": "admin_guard"},
        writer="test.refresh",
        request_path="/api/admin/server/refresh_runtime",
        target={"surface": "the plane", "resource": "runtime_policy_snapshots"},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "completed"},
        verification={"state": "confirmed"},
        result={"outcome": "applied"},
        finalized=True,
    )
    monkeypatch.setattr(
        ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: {
            "operator_state": "idle",
            "active_tasks": [],
            "open_loops": [],
            "backend_projection": {"state": "reachable"},
            "operator_summary": {"status_language": {"state": "idle"}},
        },
    )
    monkeypatch.setattr(
        ops._execution_queue,
        "status",
        lambda: {"state": {"active": False, "status": "idle", "status_detail": "idle", "current_workflow_id": "", "current_workflow_status": ""}, "events": []},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    payload = ops.operations_console_snapshot(tmp_path)

    assert payload["runtime_controls"]["audit_history"][0]["action_id"] == "refresh_runtime"
    assert payload["runtime_controls"]["audit_history"][0]["result"]["outcome"] == "applied"
    assert payload["runtime_controls"]["audit_history"][0]["target"]["resource"] == "runtime_policy_snapshots"
