from __future__ import annotations

import io
import json
import os
import re
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui.ledger import idea_note_manager
from infra.webui import server_agents
from infra.webui import server_http_core as http_core
from infra.webui import server_lab_assistant_host as lab_host
from infra.webui import server_lab_assistant_turns as lab_turns
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {}
        self.rfile = io.BytesIO(b"{}")
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


def _make_post_req(cfg: routes.WebUIConfig, path: str, payload: dict) -> _DummyReq:
    req = _DummyReq(cfg=cfg, path=path)
    req.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))
    req.headers = {"Content-Length": str(req.rfile.getbuffer().nbytes)}
    return req


def _plane_direct_eval_rows(*, intents: set[str]) -> list[dict]:
    path = Path("/home/user/causyl/runtime/state/plane_intent_mining/plane_direct_eval_seed.jsonl")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("intent") or "") in intents:
                rows.append(row)
    return rows


def _write_logged_mobile_apk_job(
    logs_dir: Path,
    *,
    job_id: str,
    cwd_abs: str,
    channel: str = "debug",
    running: bool = False,
    returncode: int = 1,
) -> None:
    payload = {
        "schema_version": "admin_cmd_job_v1",
        "job_id": str(job_id),
        "kind": f"mobile_apk_build:{channel}",
        "cmd": f"infra/mobile/build_server_apk.sh {channel}",
        "cwd_rel": "",
        "cwd_abs": str(cwd_abs),
        "utc_started": "2026-03-10T17:00:00Z",
        "utc_finished": "" if running else "2026-03-10T17:04:00Z",
        "running": bool(running),
        "killed": False,
        "returncode": int(returncode),
        "timeout_sec": 3600.0,
        "duration_sec": 240.0,
        "error": "",
        "stdout": "> Task :app:compileDebugKotlin FAILED\n",
        "stderr": "e: Unresolved reference: buttonRef\n",
        "output_seen": True,
        "last_output_utc": "2026-03-10T17:03:55Z",
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    (logs_dir / f"20260310T170000Z__mobile_apk_build_{channel}__{job_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_admin_server_refresh_runtime_returns_live_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/server/refresh_runtime")
    seen: dict[str, bool] = {}

    def _fake_refresh_snapshot() -> dict:
        seen["called"] = True
        return {
            "schema_version": "admin_server_runtime_refresh_v1",
            "utc": "2026-03-12T00:00:00Z",
            "ok": True,
            "message": "Live runtime settings refreshed for future work. In-flight turns/jobs keep prior settings.",
            "scope": {
                "applies_to": "future_work_started_after_refresh",
                "does_not_apply_to": "in_flight_turns_or_jobs",
            },
            "agents": {"ok": True},
            "orchestrator": {"control_mode": "deterministic"},
            "mobile_runtime_policy": {"chat_turn_timeout_s": 3600},
        }

    monkeypatch.setattr(routes, "_refresh_live_runtime_snapshot", _fake_refresh_snapshot)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "admin_server_runtime_refresh_v1"
    assert payload["ok"] is True
    assert payload["message"] == "Live runtime settings refreshed for future work. In-flight turns/jobs keep prior settings."
    assert payload["scope"]["applies_to"] == "future_work_started_after_refresh"
    assert payload["scope"]["does_not_apply_to"] == "in_flight_turns_or_jobs"
    assert seen["called"] is True


def test_admin_resource_concurrency_route_returns_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/resource_concurrency")
    monkeypatch.setattr(
        routes,
        "_lab_assistant_resource_concurrency_payload",
        lambda: {
            "schema_version": "shared_resource_concurrency_snapshot_v1",
            "status": "ok",
            "resource_count": 1,
            "waiting_claim_count": 2,
            "resources": [{"resource": {"canonical_key": "job_family:scheduler:cpu_light"}}],
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["status"] == "ok"
    assert payload["resource_count"] == 1


def test_admin_server_restart_rejects_when_visible_work_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(cfg, "/api/admin/server/restart", {})

    monkeypatch.setattr(
        routes,
        "_admin_restart_preflight",
        lambda _cfg, state=None: {
            "schema_version": "admin_server_restart_preflight_v1",
            "utc": "2026-03-30T10:00:00Z",
            "ok": False,
            "safe_to_restart": False,
            "blockers": [{"kind": "active_tasks_present", "count": 1}],
            "operator_snapshot_summary": {"operator_state": "waiting_on_job", "active_task_count": 1, "open_loop_count": 0},
            "scheduler": {"active_job_lock_count": 0},
        },
    )

    restarted: dict[str, bool] = {"called": False}
    monkeypatch.setattr(routes, "_admin_restart_webui", lambda _cfg: restarted.__setitem__("called", True))

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _read_payload(req)
    assert payload["error"] == "restart_blocked_active_work"
    assert payload["safe_to_restart"] is False
    assert restarted["called"] is False


def test_admin_server_restart_allows_idle_plane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(cfg, "/api/admin/server/restart", {})

    monkeypatch.setattr(
        routes,
        "_admin_restart_preflight",
        lambda _cfg, state=None: {
            "schema_version": "admin_server_restart_preflight_v1",
            "utc": "2026-03-30T10:00:00Z",
            "ok": True,
            "safe_to_restart": True,
            "blockers": [],
            "operator_snapshot_summary": {"operator_state": "idle", "active_task_count": 0, "open_loop_count": 0},
            "scheduler": {"active_job_lock_count": 0},
        },
    )

    restarted: dict[str, bool] = {"called": False}
    monkeypatch.setattr(routes, "_admin_restart_webui", lambda _cfg: restarted.__setitem__("called", True))

    class _ImmediateThread:
        def __init__(self, *, target=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            if self._target is not None:
                self._target()

    monkeypatch.setattr(routes.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(routes.time, "sleep", lambda _seconds: None)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["ok"] is True
    assert payload["preflight"]["safe_to_restart"] is True
    assert restarted["called"] is True


def test_admin_restart_preflight_treats_visible_tasks_and_loops_as_warnings_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    state = http_core._ServerState(cfg)

    monkeypatch.setattr(
        http_core,
        "_admin_repo_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        http_core._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _repo_root: {
            "operator_state": "waiting_on_job",
            "active_tasks": [{"task_id": "task-1"}],
            "open_loops": [{"loop_id": "loop-1"}],
        },
    )
    monkeypatch.setattr(
        http_core._server_maintenance,
        "job_lock_snapshot",
        lambda repo_root=None: {"active_locks": [], "stale_locks": []},
    )

    payload = http_core._admin_restart_preflight(cfg, state=state)

    assert payload["safe_to_restart"] is True
    assert payload["blockers"] == []
    assert [row["kind"] for row in payload["warnings"]] == [
        "active_tasks_present",
        "open_loops_present",
    ]


def test_admin_scheduler_watchdog_routes_return_status_and_accept_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._lab_web_ops,
        "watchdog_snapshot",
        lambda repo_root: {
            "timer_status": {"installed": True, "units": {"audit_timer": {"active_state": "active"}}},
            "latest": {"audit": {"ok": True}},
        },
    )
    monkeypatch.setattr(
        routes._lab_web_ops,
        "watchdog_latest_snapshot",
        lambda repo_root, mode="": {"audit": {"ok": True}} if not mode else {mode: {"ok": True}},
    )
    monkeypatch.setattr(
        routes._lab_web_ops,
        "watchdog_activity_snapshot",
        lambda repo_root, event_limit=12, control_limit=12, log_lines=80, action_limit=20: {
            "schema_version": "scheduler_watchdog_activity_snapshot_v1",
            "recent_log": {"mode": "selfheal", "lines": ["a", "b"]},
            "recent_actions": [{"source": "direct_watchdog_action", "action": "repair_watchdog_latest_report_pointer"}],
        },
    )
    monkeypatch.setattr(
        routes._lab_web_ops,
        "invoke_runtime_control",
        lambda repo_root, action_id, params=None, writer="": {
            "schema_version": "lab_assistant_web_operations_control_result_v1",
            "action_id": action_id,
            "confirmation": {"state": "confirmed"},
        },
    )

    status_req = _DummyReq(cfg=cfg, path="/api/admin/scheduler_watchdog/status")
    routes.WebUIRequestHandler.do_GET(status_req)  # type: ignore[arg-type]
    assert status_req._status == 200
    status_payload = _read_payload(status_req)
    assert status_payload["schema_version"] == "admin_scheduler_watchdog_status_v1"
    assert status_payload["timer_status"]["installed"] is True

    latest_req = _DummyReq(cfg=cfg, path="/api/admin/scheduler_watchdog/latest?mode=audit")
    routes.WebUIRequestHandler.do_GET(latest_req)  # type: ignore[arg-type]
    assert latest_req._status == 200
    latest_payload = _read_payload(latest_req)
    assert latest_payload["schema_version"] == "admin_scheduler_watchdog_latest_v1"
    assert latest_payload["latest"]["audit"]["ok"] is True

    activity_req = _DummyReq(cfg=cfg, path="/api/admin/scheduler_watchdog/activity?log_lines=20")
    routes.WebUIRequestHandler.do_GET(activity_req)  # type: ignore[arg-type]
    assert activity_req._status == 200
    activity_payload = _read_payload(activity_req)
    assert activity_payload["schema_version"] == "admin_scheduler_watchdog_activity_v1"
    assert activity_payload["activity"]["recent_log"]["mode"] == "selfheal"

    control_req = _make_post_req(
        cfg,
        "/api/admin/scheduler_watchdog/control",
        {"action_id": "watchdog_restart_timers", "writer": "test"},
    )
    routes.WebUIRequestHandler.do_POST(control_req)  # type: ignore[arg-type]
    assert control_req._status == 200
    control_payload = _read_payload(control_req)
    assert control_payload["action_id"] == "watchdog_restart_timers"


def test_api_health_includes_lab_assistant_host_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/health")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_host_health_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_host_health_v1",
            "status": "blocked",
            "mode": "blocked_storage_missing",
            "blocked": True,
            "summary": "storage missing",
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["status"] == "ok"
    assert payload["lab_assistant_host_status"] == "blocked"
    assert payload["lab_assistant_host_mode"] == "blocked_storage_missing"
    assert payload["lab_assistant_host"]["blocked"] is True


def test_admin_lab_assistant_health_route_returns_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/health")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_host_health_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_host_health_v1",
            "status": "ok",
            "mode": "clean_boot",
            "blocked": False,
            "summary": "clean",
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_host_health_v1"
    assert payload["mode"] == "clean_boot"
    assert payload["blocked"] is False


def test_admin_lab_assistant_task_register_route_returns_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/task_register")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_task_register_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "task_register": {"register_id": "task_register_demo", "active_tasks": [], "open_loops": []},
            "summary_cache": {"active_task_count": 0},
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_task_register_snapshot_v1"
    assert payload["task_register"]["register_id"] == "task_register_demo"


def test_admin_lab_assistant_task_register_route_returns_conflict_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/task_register")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_task_register_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "task_register": None,
            "summary_cache": None,
            "error": "task register not initialized",
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _read_payload(req)
    assert payload["task_register"] is None


def test_admin_lab_assistant_task_register_conflict_keeps_snapshot_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/task_register")

    snapshot_payload = {
        "schema_version": "lab_assistant_operator_snapshot_v1",
        "generated_utc": "2026-03-27T00:00:00Z",
        "freshness": {"stale": False, "stale_sources": []},
        "operator_summary": {"truth_conflicts": []},
        "task_register": {"register_id": "task_register_demo", "write_generation": 5, "summary_stale": False},
        "assistant_state": {
            "assistant_id": "assistant_demo",
            "current_state": "active",
            "write_generation": 7,
        },
        "host_health": {"status": "ok", "mode": "clean_boot", "blocked": False},
        "delegation_proof": {
            "schema_version": "lab_assistant_delegation_proof_v1",
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False},
        },
        "execution_truth_ownership": {
            "schema_version": "lab_assistant_execution_truth_ownership_v1",
            "uid": "PIS-20260313-100601Z-M9R2",
        },
    }

    monkeypatch.setattr(routes, "_lab_assistant_diagnostic_snapshot_payload", lambda repo_root=None: snapshot_payload)
    monkeypatch.setattr(
        routes,
        "_lab_assistant_task_register_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "task_register": None,
            "summary_cache": None,
            "error": "task register not initialized",
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _read_payload(req)
    assert payload["task_register"] is None
    assert payload["diagnostic_boundary"]["canonical_snapshot"]["api_path"] == "/api/admin/lab_assistant/snapshot"
    mismatch = payload["diagnostic_mismatch"]
    assert mismatch["state"] == "degraded_mismatch"
    assert mismatch["snapshot_available"] is True


def test_admin_lab_assistant_execution_truth_ownership_route_returns_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/execution_truth_ownership")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_execution_truth_ownership_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_execution_truth_ownership_v1",
            "uid": "PIS-20260313-100601Z-M9R2",
            "domains": [
                {"domain_id": "runtime_truth", "canonical_owner": "execution_backend"},
                {"domain_id": "operator_projection", "canonical_owner": "web_console_projection"},
            ],
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_execution_truth_ownership_v1"
    assert payload["uid"] == "PIS-20260313-100601Z-M9R2"
    assert payload["domains"][0]["domain_id"] == "runtime_truth"


def test_admin_lab_assistant_backend_api_boundary_route_returns_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/backend_api_boundary")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_backend_api_boundary_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_backend_api_boundary_v1",
            "uid": "PIS-20260313-100600Z-L8Q1",
            "read_surfaces": [{"surface_id": "health", "mutating": False}],
            "write_surfaces": [{"command_id": "send_turn", "mutating": True}],
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_backend_api_boundary_v1"
    assert payload["uid"] == "PIS-20260313-100600Z-L8Q1"
    assert payload["read_surfaces"][0]["surface_id"] == "health"
    assert payload["write_surfaces"][0]["command_id"] == "send_turn"


def test_agents_send_route_suppresses_duplicate_replay_via_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    seen: dict[str, int] = {"calls": 0}

    def _fake_send_message(**kwargs):
        seen["calls"] += 1
        return {
            "ok": True,
            "assistant": {"content": "ok"},
            "session": [],
            "binding": dict(kwargs.get("binding") or {}),
        }

    monkeypatch.setattr(routes._agents, "send_message", _fake_send_message)

    request = {
        "request_id": "req-send-001",
        "idempotency_key": "idem-send-001",
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mbl"},
        "message": "hello",
    }
    req_first = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(req_first)  # type: ignore[arg-type]
    first_payload = _read_payload(req_first)
    assert req_first._status == 200
    assert first_payload["ok"] is True
    assert first_payload["boundary_result"]["status"] == "accepted"

    req_second = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(req_second)  # type: ignore[arg-type]
    second_payload = _read_payload(req_second)
    assert req_second._status == 200
    assert second_payload["ok"] is False
    assert second_payload["boundary_result"]["status"] == "duplicate_suppressed"
    assert second_payload["boundary_result"]["reason_code"] == "duplicate_suppressed"
    assert seen["calls"] == 1


def test_schedules_run_now_route_rejects_idempotency_key_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    seen: dict[str, int] = {"calls": 0}

    def _fake_request_run_now(job_id: str, *, request_source: str = "manual", **kwargs):
        seen["calls"] += 1
        return {"ok": True, "job_id": job_id, "request_source": request_source, "queued": True}

    monkeypatch.setattr(routes._scheduled_jobs, "request_run_now", _fake_request_run_now)

    req_first = _make_post_req(
        cfg,
        "/api/schedules/run_now",
        {"request_id": "req-job-001", "idempotency_key": "idem-job-001", "job_id": "job-a"},
    )
    routes.WebUIRequestHandler.do_POST(req_first)  # type: ignore[arg-type]
    first_payload = _read_payload(req_first)
    assert req_first._status == 200
    assert first_payload["ok"] is True
    assert first_payload["boundary_result"]["status"] == "accepted"

    req_conflict = _make_post_req(
        cfg,
        "/api/schedules/run_now",
        {"request_id": "req-job-002", "idempotency_key": "idem-job-001", "job_id": "job-b"},
    )
    routes.WebUIRequestHandler.do_POST(req_conflict)  # type: ignore[arg-type]
    conflict_payload = _read_payload(req_conflict)
    assert req_conflict._status == 409
    assert conflict_payload["ok"] is False
    assert conflict_payload["boundary_result"]["status"] == "rejected"
    assert conflict_payload["boundary_result"]["reason_code"] == "idempotency_conflict"
    assert seen["calls"] == 1


def test_agents_send_route_timeout_is_ambiguous_and_retry_is_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    seen: dict[str, int] = {"calls": 0}

    def _timeout_send_message(**kwargs):
        seen["calls"] += 1
        raise TimeoutError("simulated transport timeout")

    monkeypatch.setattr(routes._agents, "send_message", _timeout_send_message)

    request = {
        "request_id": "req-timeout-001",
        "idempotency_key": "idem-timeout-001",
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mbl"},
        "message": "hello timeout",
    }
    req_timeout = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(req_timeout)  # type: ignore[arg-type]
    timeout_payload = _read_payload(req_timeout)
    assert req_timeout._status == 504
    assert timeout_payload["ok"] is False
    assert timeout_payload["boundary_result"]["status"] == "ambiguous_timeout"
    assert timeout_payload["boundary_result"]["reason_code"] == "ambiguous_timeout"
    assert timeout_payload["boundary_result"]["retry_owner"] == "caller_retry_same_idempotency_key"

    req_retry = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(req_retry)  # type: ignore[arg-type]
    retry_payload = _read_payload(req_retry)
    assert req_retry._status == 200
    assert retry_payload["ok"] is False
    assert retry_payload["boundary_result"]["status"] == "duplicate_suppressed"
    assert seen["calls"] == 1


def test_admin_lab_assistant_task_register_route_is_pure_on_real_empty_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/task_register")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    payload = _read_payload(req)
    assert req._status == 409
    assert payload["task_register"] is None
    assert payload["assistant_state"] is None
    assert before == after


def test_admin_lab_assistant_delegation_policy_route_returns_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/delegation_policy")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_delegation_policy_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_answer_delegate_policy_v1",
            "direct_answer_window_s": 60,
            "allowed_surfaces": [{"surface": "workflow", "purpose": "bounded multi-step work"}],
            "supported_classifications": ["answer_directly", "delegate", "blocked", "waiting_on_operator"],
            "next_decision_kinds": ["review_evidence"],
            "notes": ["typed requests declare evidence and next decision up front"],
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_answer_delegate_policy_v1"
    assert payload["direct_answer_window_s"] == 60
    assert payload["allowed_surfaces"][0]["surface"] == "workflow"


def test_admin_lab_assistant_snapshot_route_returns_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/snapshot")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "idle",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
            "active_tasks": [],
            "open_loops": [],
            "delegated_work": [],
            "freshness": {"stale": False, "stale_sources": [], "markers": []},
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_operator_snapshot_v1"
    assert payload["operator_state"] == "idle"
    assert payload["assistant_identity"]["assistant_id"] == "assistant_demo"


def test_admin_lab_assistant_delegation_proof_route_returns_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/delegation_proof")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_delegation_proof_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_delegated_execution_proof_report_v1",
            "proof_run_id": "proof_demo",
            "summary": {"overall_status": "ok", "counts": {"pass": 6, "warn": 0, "stale": 0, "fail": 0}, "trace_count": 1},
            "checks": [],
            "traces": [],
            "freshness": {"stale": False, "age_s": 12, "max_age_s": 1800, "reason": ""},
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_delegated_execution_proof_report_v1"
    assert payload["summary"]["overall_status"] == "ok"
    assert payload["freshness"]["stale"] is False


def test_admin_lab_assistant_snapshot_route_is_pure_on_real_empty_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/snapshot")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    payload = _read_payload(req)
    assert req._status == 200
    assert payload["schema_version"] == "lab_assistant_operator_snapshot_v1"
    assert payload["operator_state"] in {"blocked", "idle"}
    assert before == after


def test_admin_lab_assistant_export_and_restore_routes_call_host_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    export_req = _make_post_req(cfg, "/api/admin/lab_assistant/export", {"note": "export now"})
    restore_req = _make_post_req(
        cfg,
        "/api/admin/lab_assistant/restore",
        {"manifest_path": str(tmp_path / "manifest.json"), "target_repo_root": str(tmp_path / "target"), "note": "restore now"},
    )
    seen: dict[str, object] = {}

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._lab_host,
        "export_host_baseline",
        lambda repo_root, note="": {
            "ok": True,
            "schema_version": "lab_assistant_host_export_v1",
            "manifest_path": str(Path(repo_root) / "runtime" / "state" / "lab_assistant" / "host" / "exports" / "x" / "manifest.json"),
            "note": note,
        },
    )

    def _fake_restore(*, manifest_path: Path, target_repo_root: Path, note: str = "") -> dict:
        seen["manifest_path"] = str(manifest_path)
        seen["target_repo_root"] = str(target_repo_root)
        seen["note"] = str(note)
        return {"ok": True, "schema_version": "lab_assistant_host_restore_v1", "target_repo_root": str(target_repo_root)}

    monkeypatch.setattr(routes._lab_host, "restore_host_baseline", _fake_restore)

    routes.WebUIRequestHandler.do_POST(export_req)  # type: ignore[arg-type]
    routes.WebUIRequestHandler.do_POST(restore_req)  # type: ignore[arg-type]

    export_payload = _read_payload(export_req)
    restore_payload = _read_payload(restore_req)
    assert export_req._status == 200
    assert export_payload["schema_version"] == "lab_assistant_host_export_v1"
    assert restore_req._status == 200
    assert restore_payload["schema_version"] == "lab_assistant_host_restore_v1"
    assert seen["manifest_path"] == str(tmp_path / "manifest.json")
    assert seen["target_repo_root"] == str(tmp_path / "target")
    assert seen["note"] == "restore now"


def test_admin_lab_assistant_restore_route_rejects_live_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(
        cfg,
        "/api/admin/lab_assistant/restore",
        {"manifest_path": str(tmp_path / "manifest.json"), "target_repo_root": str(tmp_path)},
    )
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_host_restore_v1"
    assert "live repo root" in str(payload["error"])


def test_admin_lab_assistant_restore_route_surfaces_staged_payload_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "live"
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    for root in (live_root, source_root):
        (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
        (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
        (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
        (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)

    lab_host.bootstrap_host_baseline(source_root)
    lab_host.mark_clean_shutdown(source_root, reason="test_clean")
    export = lab_host.export_host_baseline(source_root, note="tamper payload")
    manifest_path = Path(str(export["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_rel = str(manifest["files"][0]["relpath"])
    payload_file = manifest_path.parent / "payload" / first_rel
    payload_file.write_text("tampered", encoding="utf-8")

    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(
        cfg,
        "/api/admin/lab_assistant/restore",
        {"manifest_path": str(manifest_path), "target_repo_root": str(target_root)},
    )
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: live_root)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_host_restore_v1"
    assert "restore validation failed before target mutation" in str(payload["error"])


def test_mobile_agent_spec_defaults_are_stable() -> None:
    assert routes._mobile_agent_spec("p") == {
        "thread_id": "mpl",
        "role": "generalist",
        "prompt_file": "role__plane_mobile.md",
    }
    assert routes._mobile_agent_spec("a") == {
        "thread_id": "mbl",
        "role": "generalist",
        "prompt_file": "role__generalist_mobile.md",
    }
    assert routes._mobile_agent_spec("a2") == {
        "thread_id": "mb2",
        "role": "generalist",
        "prompt_file": "role__generalist_mobile.md",
    }
    assert routes._mobile_agent_spec("a3") == {
        "thread_id": "mb3",
        "role": "generalist",
        "prompt_file": "role__generalist_mobile.md",
    }
    assert routes._mobile_agent_spec("o") == {
        "thread_id": "mor",
        "role": "generalist",
        "prompt_file": "role__orchestrator_mobile.md",
    }
    assert routes._mobile_agent_spec("b") == {
        "thread_id": "mbu",
        "role": "builder",
        "prompt_file": "role__builder_mobile.md",
    }
    assert routes._mobile_agent_spec("v") == {
        "thread_id": "mvr",
        "role": "assurer",
        "prompt_file": "role__verifier_mobile.md",
    }


def test_mobile_assistant_shell_projects_topic_identity_per_tab_from_durable_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a2")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "idle",
            "summary": "waiting for next turn",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
        },
    )

    def _fake_read_session(*, role: str, binding: dict, tail: int = 80, inbox_tail: int = 50) -> dict:
        tid = str((binding or {}).get("id") or "")
        rows = []
        if tid == "mb2":
            rows = [{"sender": "assistant", "content": "done", "meta": {"chat_header_title": "Admin Toggle Fix"}}]
        if tid == "mb3":
            rows = [{"sender": "assistant", "content": "done", "meta": {"chat_header_title": "overly long invalid marker title"}}]
        return {"session": rows}

    monkeypatch.setattr(routes._agents, "read_session", _fake_read_session)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_assistant_shell_v1"
    assert payload["one_face_shell"]["selected_agent_key"] == "a2"
    tabs = {str(row["agent_key"]): row for row in payload["tabs"]}
    assert set(tabs.keys()) == {"a", "a2", "a3", "o", "b", "v"}
    assert tabs["o"]["topic"]["header_title"] == "ORCH"
    assert tabs["o"]["topic"]["header_title_source"] == "fixed_tab"
    assert tabs["a2"]["topic"]["header_title"] == "Admin Toggle Fix"
    assert tabs["a2"]["topic"]["header_title_source"] == "agent_marker"
    assert tabs["a3"]["topic"]["header_title"] == "AGT"
    assert tabs["a3"]["topic"]["header_title_source"] == "default_tab"


def test_mobile_assistant_shell_navigation_preserves_active_task_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a3&thread_id=mb3")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "idle",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
        },
    )
    monkeypatch.setattr(routes._agents, "read_session", lambda **kwargs: {"session": []})

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    nav = payload["navigation"]
    assert nav["context"]["binding"] == {"type": "task_id", "id": "mb3"}
    assert nav["context"]["agent_key"] == "a3"
    assert nav["surfaces"]["chat"]["query"] == {"agent_key": "a3", "thread_id": "mb3"}
    assert nav["surfaces"]["backlog"]["query"] == {"agent_key": "a3", "thread_id": "mb3"}
    assert nav["surfaces"]["execution"]["query"] == {"agent_key": "a3", "thread_id": "mb3", "pane": "execution_queue"}
    assert nav["surfaces"]["audit"]["query"] == {"agent_key": "a3", "thread_id": "mb3"}


def test_mobile_assistant_shell_selected_tab_uses_selected_thread_id_in_tab_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a&thread_id=mb2")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "idle",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
        },
    )

    def _fake_read_session(*, role: str, binding: dict, tail: int = 80, inbox_tail: int = 50) -> dict:
        tid = str((binding or {}).get("id") or "")
        if tid == "mb2":
            return {"session": [{"sender": "assistant", "content": "done", "meta": {"chat_header_title": "Custom Thread Title"}}]}
        return {"session": []}

    monkeypatch.setattr(routes._agents, "read_session", _fake_read_session)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    tabs = {str(row["agent_key"]): row for row in payload["tabs"]}
    assert payload["one_face_shell"]["selected_thread_id"] == "mb2"
    assert tabs["a"]["selected"] is True
    assert tabs["a"]["thread_id"] == "mb2"
    assert tabs["a"]["topic"]["header_title"] == "Custom Thread Title"
    assert payload["navigation"]["context"]["binding"] == {"type": "task_id", "id": "mb2"}


def test_mobile_assistant_shell_coerces_hidden_selected_tab_to_default_generalist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=p&thread_id=mpl")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "idle",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
        },
    )
    monkeypatch.setattr(routes._agents, "read_session", lambda **kwargs: {"session": []})

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["one_face_shell"]["selected_agent_key"] == "a"
    tabs = {str(row["agent_key"]): row for row in payload["tabs"]}
    assert tabs["a"]["selected"] is True


def test_mobile_assistant_shell_keeps_blocked_upstream_truth_even_with_topic_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a")

    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda repo_root=None: {
            "schema_version": "lab_assistant_operator_snapshot_v1",
            "operator_state": "blocked",
            "backend_projection": {"state": "degraded"},
            "summary": "task register evidence missing",
            "assistant_identity": {"assistant_id": "assistant_demo", "assistant_label": "Jeff"},
        },
    )
    monkeypatch.setattr(
        routes._agents,
        "read_session",
        lambda **kwargs: {"session": [{"sender": "assistant", "meta": {"chat_header_title": "Admin Toggle Fix"}}]},
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["upstream_truth"]["operator_state"] == "blocked"
    assert payload["upstream_truth"]["backend_condition"] == "degraded"
    assert payload["upstream_truth"]["summary"] == "task register evidence missing"
    tabs = {str(row["agent_key"]): row for row in payload["tabs"]}
    assert tabs["a"]["topic"]["header_title"] == "Admin Toggle Fix"
    assert tabs["a"]["topic"]["header_title_source"] == "agent_marker"


def test_user_visible_assistant_content_suppresses_invalid_host_request_trace_for_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        server_agents,
        "_trace_host_verification",
        lambda event, message="", **kwargs: seen.append((str(event), dict(kwargs))),
    )

    raw = (
        "HOST_VERIFICATION_REQUEST_JSON:\n"
        "```json\n"
        '{"action": "android_unit_test"}\n'
        "```"
    )

    visible = server_agents._user_visible_assistant_content(raw)

    assert visible == "Requested host verification."
    assert seen == []


def test_mobile_stream_builder_agent_uses_builder_role_and_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__builder_mobile.md").write_text("builder mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "b"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._lab_bootstrap, "render_fresh_turn_bootstrap_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))
    monkeypatch.setenv("EIEL_MOBILE_CHAT_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("EIEL_MOBILE_CHAT_REASONING_EFFORT", "low")

    seen: dict = {}

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            seen["binding"] = dict(binding)
            seen["role"] = str(role)
            seen["bootstrap_text"] = str(bootstrap_text)

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            seen["stream_binding"] = dict(binding)
            seen["stream_role"] = str(role)
            seen["stream_message"] = str(message)
            seen["timeout_s"] = int(timeout_s)
            seen["model"] = str(model)
            seen["reasoning_effort"] = str(reasoning_effort)
            yield {"type": "delta", "delta": "ok"}
            yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"delta": "ok"}' in body
    assert "data: [DONE]" in body
    assert seen["binding"] == {"type": "task_id", "id": "mbu"}
    assert seen["role"] == "builder"
    assert "builder mobile prompt" in seen["bootstrap_text"]
    assert seen["model"] == "gpt-5.3-codex"
    assert seen["reasoning_effort"] == "low"


def test_mobile_stream_done_payload_omits_internal_operator_blob_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__builder_mobile.md").write_text("builder mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "b"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._lab_bootstrap, "render_fresh_turn_bootstrap_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "ok"}
            yield {
                "type": "done",
                "ok": True,
                "content": "ok",
                "operator_intake": {"request": "waste"},
                "task_register": {"active_tasks": [{"id": "task_1"}]},
                "assistant_followup": {"content": "followup"},
                "host_verification_result": {"ok": True},
                "assistant": {"meta": {"chat_header_title": "Builder"}},
            }

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"type": "done", "ok": true}' in body
    assert "operator_intake" not in body
    assert "task_register" not in body
    assert "assistant_followup" not in body
    assert "host_verification_result" not in body
    assert "data: [DONE]" in body


def test_mobile_stream_falls_back_to_next_codex_model_on_quota_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__builder_mobile.md").write_text("builder mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "b"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._lab_bootstrap, "render_fresh_turn_bootstrap_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))
    monkeypatch.setenv("EIEL_MOBILE_CHAT_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("EIEL_MOBILE_CHAT_FALLBACK_MODELS", "gpt-5.3-codex-spark,gpt-5.2-codex")

    seen_models: list[str] = []

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            seen_models.append(str(model))
            if str(model) == "gpt-5.3-codex":
                raise routes._agents.CodexRuntimeError("insufficient_quota")  # type: ignore[attr-defined]
            yield {"type": "delta", "delta": "ok"}
            yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen_models == ["gpt-5.3-codex", "gpt-5.3-codex-spark"]
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"delta": "ok"}' in body
    assert "data: [DONE]" in body


def test_mobile_stream_rejects_invalid_agent_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "x"})

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _read_payload(req)
    assert str(payload.get("error") or "") == "agent_key must be one of: p, a, a2, a3, o, b, v"


def test_mobile_stream_generalist_lane_keys_keep_distinct_thread_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._lab_bootstrap, "render_fresh_turn_bootstrap_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    seen: list[tuple[str, dict[str, str], dict[str, str]]] = []

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            seen.append((str(role), dict(binding), {"bootstrap_text": str(bootstrap_text)}))

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            seen.append((str(role), dict(binding), {"message": str(message), "timeout_s": str(timeout_s)}))
            yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    for agent_key, expected_thread_id, expected_prompt in (
        ("p", "mpl", "plane"),
        ("a", "mbl", "generalist"),
        ("a2", "mb2", "generalist"),
        ("a3", "mb3", "generalist"),
    ):
        req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
        monkeypatch.setattr(
            routes,
            "_read_request_json",
            lambda _h, max_bytes=0, agent_key=agent_key: {"message": f"hello-{agent_key}", "agent_key": agent_key},
        )
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
        assert req._status == 200
        ensure_role, ensure_binding, ensure_meta = seen[-2]
        stream_role, stream_binding, stream_meta = seen[-1]
        assert ensure_role == "generalist"
        assert stream_role == "generalist"
        assert ensure_binding == {"type": "task_id", "id": expected_thread_id}
        assert stream_binding == {"type": "task_id", "id": expected_thread_id}
        assert expected_prompt in ensure_meta["bootstrap_text"]
        assert stream_meta["message"] == f"hello-{agent_key}"


def test_mobile_stream_appends_durable_bootstrap_and_keeps_message_current_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "current ask", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(
        routes,
        "_mobile_effective_history",
        lambda tid, tail=200: [
            {"role": "user", "content": "older question"},
            {"role": "assistant", "content": "older answer"},
        ],
    )
    monkeypatch.setattr(routes._lab_bootstrap, "fresh_turn_recent_chat_rows", lambda: 2)
    monkeypatch.setattr(
        routes._lab_bootstrap,
        "render_fresh_turn_bootstrap_text",
        lambda repo_root, recent_chat_rows=None, max_chars=None: f"durable bootstrap::{len(recent_chat_rows or [])}",
    )
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    seen: dict[str, object] = {}

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            seen["bootstrap_text"] = str(bootstrap_text)

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            seen["message"] = str(message)
            yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen["bootstrap_text"] == "generalist mobile prompt\n\ndurable bootstrap::2"
    assert seen["message"] == "current ask"


def test_mobile_stream_start_benchmark_reuses_last_user_task_and_appends_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _h, max_bytes=0: {"message": "Start the benchmark", "agent_key": "a"},
    )
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "user", "content": "Implement the backlog execution staging redesign."},
            {"role": "assistant", "content": "Working on it."},
        ],
    )

    appended: list[dict] = []
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: appended.append(dict(kwargs)) or dict(kwargs))

    seen: dict[str, object] = {}

    def _fake_start_background_benchmark(**kwargs):
        seen.update(kwargs)
        return {"benchmark_id": "benchjob_test", "status": "queued"}

    monkeypatch.setattr(routes._agent_benchmark, "start_background_benchmark", _fake_start_background_benchmark)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert "Started benchmark benchjob_test." in body
    assert seen["objective"] == "Implement the backlog execution staging redesign."
    assert callable(seen["on_complete"])

    callback = seen["on_complete"]
    assert callable(callback)
    callback(
        {
            "benchmark_id": "benchjob_test",
            "status": "completed_with_failures",
            "report_text": "Benchmark benchjob_test finished: completed_with_failures.\nworkflow: completed, 11.0s\nswarm: partial, 9.0s, checks failed=1\nReport: /tmp/report.md",
            "summary_path": "/tmp/report.md",
        }
    )

    assistant_rows = [row for row in appended if str(row.get("sender") or "") == "assistant"]
    assert any("Started benchmark benchjob_test." in str(row.get("content") or "") for row in assistant_rows)
    assert any("Report: /tmp/report.md" in str(row.get("content") or "") for row in assistant_rows)


def test_agents_benchmark_start_reuses_last_user_task_for_bound_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/agents/benchmark/start")
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _h, max_bytes=0: {"thread_id": "mbl", "label": "Bound benchmark"},
    )
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "user", "content": "Ship the zero-input benchmark launch path."},
            {"role": "assistant", "content": "On it."},
        ],
    )

    seen: dict[str, object] = {}

    def _fake_start_background_benchmark(**kwargs):
        seen.update(kwargs)
        return {"benchmark_id": "benchjob_direct", "status": "queued"}

    monkeypatch.setattr(routes._agent_benchmark, "start_background_benchmark", _fake_start_background_benchmark)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "agents_benchmark_start_v1"
    assert payload["benchmark_id"] == "benchjob_direct"
    assert seen["objective"] == "Ship the zero-input benchmark launch path."
    assert seen["binding"] == {"type": "task_id", "id": "mbl"}
    assert seen["label"] == "Bound benchmark"


def test_agents_benchmark_start_fails_when_bound_thread_has_no_reusable_user_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/agents/benchmark/start")
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"thread_id": "mbl"})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "assistant", "content": "No user task yet."},
            {"role": "user", "content": "Start the benchmark"},
            {"role": "user", "content": "Swarm it"},
        ],
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _read_payload(req)
    assert payload["error"] == "No earlier user task was found for this binding. Provide `objective` to start the benchmark."


def test_mobile_session_history_uses_agent_default_thread_when_thread_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/session_history?agent_key=v")
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {"mvr": [{"role": "assistant", "content": "verified"}]})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["thread_id"] == "mvr"
    assert payload["history"] == [{"role": "assistant", "content": "verified"}]


def test_mobile_session_history_uses_new_generalist_lane_default_thread_when_thread_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/session_history?agent_key=a2")
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {"mb2": [{"role": "assistant", "content": "lane two"}]})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["thread_id"] == "mb2"
    assert payload["history"] == [{"role": "assistant", "content": "lane two"}]


def test_mobile_session_history_prefers_canonical_agent_transcript_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/session_history?thread_id=mor")
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {"mor": [{"role": "assistant", "content": "stale cache"}]})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "user", "content": "resume this"},
            {"role": "assistant", "content": "canonical reply"},
        ],
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["thread_id"] == "mor"
    assert payload["history"] == [
        {"role": "user", "content": "resume this"},
        {"role": "assistant", "content": "canonical reply"},
    ]


def test_mobile_session_history_requests_full_canonical_history_for_display(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/session_history?thread_id=mor")
    seen: dict[str, int] = {}

    def _session_history_rows(**kwargs):
        seen["tail"] = int(kwargs.get("tail", -1))
        return [{"role": "assistant", "content": "full"}]

    monkeypatch.setattr(routes._agents, "session_history_rows", _session_history_rows)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen["tail"] == routes._mobile_history_window_rows()


def test_session_history_rows_uses_requested_tail_for_mobile_task_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(server_agents, "_read_jsonl", lambda _path, max_lines=0: [{"sender": "assistant", "content": f"tail-{max_lines}"}])

    rows = server_agents.session_history_rows(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        tail=37,
    )

    assert rows == [{"role": "assistant", "content": "tail-37"}]


def test_append_session_message_suppresses_adjacent_mobile_stream_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(server_agents, "_refresh_mobile_bubble_projection", lambda **kwargs: None)

    payload = {
        "role": "generalist",
        "binding": {"type": "task_id", "id": "mbl"},
        "sender": "user",
        "content": "same prompt",
        "status": "ok",
        "meta": {"source": "mobile_chat_stream"},
    }

    first = server_agents.append_session_message(**payload)
    second = server_agents.append_session_message(**payload)

    session_file = tmp_path / "governance" / "agents" / "sessions" / "mbl" / "session__generalist.jsonl"
    lines = [line for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(lines) == 1
    assert "deduped" not in first
    assert second["deduped"] is True


def test_append_session_message_compacts_large_mobile_task_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(server_agents, "_refresh_mobile_bubble_projection", lambda **kwargs: None)
    monkeypatch.setenv("EIEL_MOBILE_TASK_SESSION_MAX_BYTES", "400")
    monkeypatch.setenv("EIEL_MOBILE_TASK_SESSION_MAX_LINES", "200")

    session_dir = tmp_path / "governance" / "agents" / "sessions" / "mbl"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / "session__generalist.jsonl"
    rows = []
    for idx in range(220):
        rows.append(
            json.dumps(
                {
                    "schema_version": "agent_chat_message_v1",
                    "utc": f"2026-03-11T22:{idx // 60:02d}:{idx % 60:02d}Z",
                    "role_panel": "generalist",
                    "sender": "assistant" if idx % 2 else "user",
                    "content": f"message-{idx}-" + ("x" * 6000),
                    "status": "ok",
                    "binding": {"type": "task_id", "id": "mbl"},
                    "meta": {"source": "mobile_chat_stream"},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    session_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    server_agents.append_session_message(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        sender="assistant",
        content="tail message",
        meta={"source": "mobile_chat_stream"},
    )

    kept = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(kept) <= 200
    assert kept[-1]["content"] == "tail message"
    assert kept[0]["content"].startswith("message-")


def test_mobile_sessions_init_reconciles_legacy_cache_into_canonical_session_and_bubbles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    legacy_cache = tmp_path / "governance" / "webui" / "runtime" / "mobile_chat_sessions.json"
    legacy_cache.parent.mkdir(parents=True, exist_ok=True)
    legacy_cache.write_text(
        json.dumps(
            {
                "schema_version": "mobile_chat_sessions_v1",
                "updated_at_utc": "2026-03-09T00:00:00Z",
                "sessions": {
                    "mbl": [
                        {"role": "user", "content": "old user"},
                        {"role": "assistant", "content": "old assistant"},
                    ]
                },
                "caps": {},
            }
        ),
        encoding="utf-8",
    )

    chat_dir = tmp_path / "governance" / "agents" / "sessions" / "mbl"
    chat_dir.mkdir(parents=True, exist_ok=True)
    session_file = chat_dir / "session__generalist.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "schema_version": "agent_chat_message_v1",
                "utc": "2026-03-09T01:00:00Z",
                "role_panel": "generalist",
                "sender": "user",
                "content": "new user",
                "status": "ok",
                "binding": {"type": "task_id", "id": "mbl"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "agent_chat_message_v1",
                "utc": "2026-03-09T01:00:01Z",
                "role_panel": "generalist",
                "sender": "assistant",
                "content": "new assistant",
                "status": "ok",
                "binding": {"type": "task_id", "id": "mbl"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_mobile_sessions_primary_path",
        lambda: (tmp_path / "runtime" / "state" / "webui" / "mobile_chat_sessions.json").resolve(),
    )
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)

    routes._mobile_sessions_init(cfg)

    lines = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["content"] for row in lines] == ["old user", "old assistant", "new user", "new assistant"]
    assert lines[0]["meta"]["source"] == "mobile_history_recovery"
    primary_cache = tmp_path / "runtime" / "state" / "webui" / "mobile_chat_sessions.json"
    primary_payload = json.loads(primary_cache.read_text(encoding="utf-8"))
    assert primary_payload["sessions"]["mbl"] == [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old assistant"},
        {"role": "user", "content": "new user"},
        {"role": "assistant", "content": "new assistant"},
    ]
    bubble = tmp_path / "governance" / "agents" / "mobile_bubbles" / "generalist_a.md"
    bubble_text = bubble.read_text(encoding="utf-8")
    assert "old user" in bubble_text
    assert "old assistant" in bubble_text
    assert "new assistant" in bubble_text


def test_mobile_sessions_init_backfills_known_mobile_lanes_from_canonical_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    primary = tmp_path / "runtime" / "state" / "webui" / "mobile_chat_sessions.json"
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        json.dumps(
            {
                "schema_version": "mobile_chat_sessions_v1",
                "updated_at_utc": "2026-03-09T00:00:00Z",
                "sessions": {},
                "caps": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes, "_mobile_sessions_primary_path", lambda: primary.resolve())
    monkeypatch.setattr(routes, "_mobile_sessions_legacy_candidates", lambda _cfg: [])

    def _session_history_rows(**kwargs):
        binding = kwargs.get("binding") or {}
        role = str(kwargs.get("role") or "")
        tid = str(binding.get("id") or "")
        if tid == "mbu" and role == "builder":
            return [
                {"role": "user", "content": "hello builder"},
                {"role": "assistant", "content": "builder reply"},
            ]
        if tid == "mvr" and role == "assurer":
            return [
                {"role": "user", "content": "hello verifier"},
                {"role": "assistant", "content": "verifier reply"},
            ]
        return []

    monkeypatch.setattr(routes._agents, "session_history_rows", _session_history_rows)
    monkeypatch.setattr(routes._agents, "refresh_mobile_bubble_projections", lambda: [])

    routes._mobile_sessions_init(cfg)

    payload = json.loads(primary.read_text(encoding="utf-8"))
    sessions = payload.get("sessions") or {}
    assert sessions["mbu"] == [
        {"role": "user", "content": "hello builder"},
        {"role": "assistant", "content": "builder reply"},
    ]
    assert sessions["mvr"] == [
        {"role": "user", "content": "hello verifier"},
        {"role": "assistant", "content": "verifier reply"},
    ]


def test_mobile_sessions_init_replaces_cache_with_canonical_history_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    primary = tmp_path / "runtime" / "state" / "webui" / "mobile_chat_sessions.json"
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        json.dumps(
            {
                "schema_version": "mobile_chat_sessions_v1",
                "updated_at_utc": "2026-03-09T00:00:00Z",
                "sessions": {
                    "mbl": [
                        {"role": "user", "content": "stale cached user"},
                        {"role": "assistant", "content": "stale cached assistant"},
                    ]
                },
                "caps": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes, "_mobile_sessions_primary_path", lambda: primary.resolve())
    monkeypatch.setattr(routes, "_mobile_sessions_legacy_candidates", lambda _cfg: [])
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "user", "content": "canonical user"},
            {"role": "assistant", "content": "canonical assistant"},
        ]
        if str((kwargs.get("binding") or {}).get("id") or "") == "mbl"
        else [],
    )
    monkeypatch.setattr(routes._agents, "refresh_mobile_bubble_projections", lambda: [])

    routes._mobile_sessions_init(cfg)

    payload = json.loads(primary.read_text(encoding="utf-8"))
    assert payload["sessions"]["mbl"] == [
        {"role": "user", "content": "canonical user"},
        {"role": "assistant", "content": "canonical assistant"},
    ]


def test_mobile_sessions_init_discovers_hidden_mobile_cache_files_under_runtime_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path / "runtime" / "live")
    hidden_cache = tmp_path / "runtime" / "live" / "archive" / "20260307" / "mobile_chat_sessions.json"
    hidden_cache.parent.mkdir(parents=True, exist_ok=True)
    hidden_cache.write_text(
        json.dumps(
            {
                "schema_version": "mobile_chat_sessions_v1",
                "updated_at_utc": "2026-03-07T12:00:00Z",
                "sessions": {
                    "mb2": [
                        {"role": "user", "content": "lane two hidden user"},
                        {"role": "assistant", "content": "lane two hidden assistant"},
                    ]
                },
                "caps": {},
            }
        ),
        encoding="utf-8",
    )

    primary = tmp_path / "runtime" / "state" / "webui" / "mobile_chat_sessions.json"
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_mobile_sessions_primary_path", lambda: primary.resolve())
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "refresh_mobile_bubble_projections", lambda: [])

    routes._mobile_sessions_init(cfg)

    payload = json.loads(primary.read_text(encoding="utf-8"))
    assert payload["sessions"]["mb2"] == [
        {"role": "user", "content": "lane two hidden user"},
        {"role": "assistant", "content": "lane two hidden assistant"},
    ]


def test_mobile_session_stats_prefers_canonical_agent_transcript_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/session_stats?thread_id=mor")
    monkeypatch.setattr(
        routes._agents,
        "session_history_rows",
        lambda **kwargs: [
            {"role": "user", "content": "resume this"},
            {"role": "assistant", "content": "canonical reply"},
            {"role": "user", "content": "continue"},
        ],
    )
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {"mor": 20})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["thread_id"] == "mor"
    assert payload["messages"] == 3
    assert payload["turns"] == 2
    assert payload["chars"] == len("resume thiscanonical replycontinue")


def test_mobile_session_state_payload_prefers_durable_turn_replay_over_stream_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._agents,
        "read_session",
        lambda **kwargs: {"stream_state": {"status": "streaming", "content": "stale cache blob"}},
    )

    claim = lab_turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "mbl"},
        role="generalist",
        message="hello",
        writer="test.mobile_state",
        advisory_transport_id="transport-mobile-state",
    )
    turn_id = str(claim["turn"]["turn_id"])
    lab_turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.mobile_state",
        new_state="running",
        state_reason="reattach probe",
    )
    lab_turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.mobile_state",
        event_type="output_delta",
        text="Hello",
    )

    payload = routes._mobile_session_state_payload("mbl")

    assert payload["stream_state"] is not None
    assert payload["stream_state"]["source"] == "durable_turn_projection"
    assert payload["stream_state"]["turn_id"] == turn_id
    assert payload["stream_state"]["content"] == "Hello"


def test_mobile_stream_writes_through_to_canonical_agent_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__orchestrator_mobile.md").write_text("orchestrator mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "o"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    seen: dict[str, object] = {}

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        seen["role"] = role
        seen["binding"] = dict(binding)
        seen["message"] = message
        seen["options"] = dict(options or {})
        yield {"type": "start", "turn_id": "turn-mobile-route-001"}
        yield {"type": "delta", "delta": "ok"}
        yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen["role"] == "generalist"
    assert seen["binding"] == {"type": "task_id", "id": "mor"}
    assert seen["message"] == "hello"
    assert seen["options"]["prompt_file"] == "role__orchestrator_mobile.md"
    stream_text = req.wfile.getvalue().decode("utf-8")
    assert '"delta": "ok"' in stream_text
    assert "[DONE]" in stream_text


def test_mobile_stream_passes_client_request_identity_into_transport_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _h, max_bytes=0: {
            "message": "hello",
            "agent_key": "a",
            "request_id": "req-mobile-123",
            "idempotency_key": "idem-mobile-123",
        },
    )
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    seen: dict[str, object] = {}

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        seen["options"] = dict(options or {})
        yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen["options"]["transport_id"] == "req-mobile-123"


def test_plane_tab_stream_requires_boundary_acceptance_and_marks_transcript_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "p"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    boundary_calls: list[dict[str, object]] = []
    seen: dict[str, object] = {}

    def _boundary(**kwargs):
        boundary_calls.append(dict(kwargs))
        return {"status": "accepted", "request_id": "req_plane_1", "command_id": "send_turn"}

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        seen["role"] = role
        seen["binding"] = dict(binding)
        seen["message"] = message
        seen["options"] = dict(options or {})
        yield {"type": "start", "turn_id": "turn-plane-route-001"}
        yield {"type": "delta", "delta": "ok"}
        yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)
    monkeypatch.setattr(routes, "_process_boundary_mutation", _boundary)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert len(boundary_calls) == 1
    assert boundary_calls[0]["command_id"] == "send_turn"
    assert boundary_calls[0]["payload"]["binding"] == {"type": "task_id", "id": "mpl"}
    assert seen["role"] == "generalist"
    assert seen["binding"] == {"type": "task_id", "id": "mpl"}
    assert seen["message"] == "hello"
    assert seen["options"]["prompt_file"] == "role__plane_mobile.md"
    assert str(seen["options"]["transport_id"]).startswith("mobile_plane_send_")
    assert seen["options"]["transport_id"] == boundary_calls[0]["request_id"]
    stream_text = req.wfile.getvalue().decode("utf-8")
    assert '"plane_routed": true' in stream_text


def test_plane_tab_answers_queue_question_directly_without_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _h, max_bytes=0: {"message": "how many jobs in the queue?", "agent_key": "p"},
    )
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)

    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: (_ for _ in ()).throw(AssertionError("direct plane answers should not append manual session rows")))
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "list_jobs",
        lambda: [
            {
                "job_id": "job_a",
                "enabled": True,
                "display_title": "job a",
                "schedule": {"mode": "once", "run_at": "2026-03-30T20:00:00Z"},
                "state": {"active_run_id": "", "last_status": ""},
            },
            {
                "job_id": "job_b",
                "enabled": False,
                "display_title": "job b",
                "schedule": {"mode": "once", "run_at": "2026-03-30T21:00:00Z"},
                "state": {"active_run_id": "", "last_status": "blocked"},
            },
        ],
    )
    monkeypatch.setattr(
        routes,
        "_process_boundary_mutation",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("boundary mutation should not run for direct plane queue answers")),
    )

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            raise AssertionError("Codex manager should not run for direct plane queue answers")

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            raise AssertionError("Codex stream should not run for direct plane queue answers")

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert "2 job specs" in body
    assert "1 are enabled" in body
    assert "job_a" in body
    assert "data: [DONE]" in body
    projection = lab_turns.recent_turn_projections(
        tmp_path,
        limit=4,
        binding={"type": "task_id", "id": "mpl"},
        role="generalist",
    )
    assert projection["total_matching_turns"] == 1
    turn_id = str(projection["turns"][0]["turn_id"])
    replay = lab_turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)
    assert projection["turns"][0]["state"] == "completed"
    assert replay["replay_status"] == "completed"
    assert replay["content"].startswith("There are 2 job specs in the scheduler inbox.")
    history = routes._mobile_session_history_payload("mpl")
    assert history["history"] == [
        {"role": "user", "content": "how many jobs in the queue?", "plane_routed": True},
        {
            "role": "assistant",
            "content": (
                "There are 2 job specs in the scheduler inbox. "
                "1 are enabled, 0 are running, and 1 are blocked. "
                "The next enabled job would be `job_a` (job a) at 2026-03-30T20:00:00Z."
            ),
            "plane_routed": True,
        },
    ]


def test_plane_tab_routes_schedule_job_prompt_as_typed_mutation_without_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")

    prompt = (
        'create job\n'
        '{"job_id":"job_plane_write","job_kind":"pipeline","binding":{"type":"task_id","id":"mpl"},'
        '"schedule":{"mode":"once","run_at":"2026-03-30T20:00:00Z"},'
        '"runtime":{"executor_agent_tech":"codex","model":"gpt-5-codex"},'
        '"prompt":"Run the pipeline now","run_now":true}'
    )

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": prompt, "agent_key": "p"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)

    saved_payloads: list[dict[str, object]] = []
    run_now_calls: list[tuple[str, str]] = []

    def _save_job(payload: dict) -> dict:
        saved_payloads.append(dict(payload))
        return {
            "job_id": str(payload.get("job_id") or "job_plane_write"),
            "job_kind": str(payload.get("job_kind") or "pipeline"),
            "binding": dict(payload.get("binding") or {}),
            "schedule": dict(payload.get("schedule") or {}),
            "runtime": dict(payload.get("runtime") or {}),
            "prompt": str(payload.get("prompt") or ""),
            "display_title": "job_plane_write",
        }

    def _run_now(job_id: str, request_source: str = "") -> dict:
        run_now_calls.append((job_id, request_source))
        return {"ok": True, "queued": True}

    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: (_ for _ in ()).throw(AssertionError("typed plane mutations should not append manual session rows")))
    monkeypatch.setattr(routes._scheduled_jobs, "save_job", _save_job)
    monkeypatch.setattr(routes._scheduled_jobs, "request_run_now", _run_now)
    monkeypatch.setattr(
        routes,
        "_process_boundary_mutation",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("boundary mutation should not run for typed plane mutations")),
    )

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            raise AssertionError("Codex manager should not run for typed plane mutations")

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            raise AssertionError("Codex stream should not run for typed plane mutations")

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert "Created scheduled job `job_plane_write`" in body
    assert "run_now=queued" in body
    assert saved_payloads and saved_payloads[0]["job_id"] == "job_plane_write"
    assert run_now_calls == [("job_plane_write", "plane_mobile_prompt")]
    assert routes._plane_mobile_direct_reply(prompt) is None
    projection = lab_turns.recent_turn_projections(
        tmp_path,
        limit=4,
        binding={"type": "task_id", "id": "mpl"},
        role="generalist",
    )
    assert projection["total_matching_turns"] == 1
    turn_id = str(projection["turns"][0]["turn_id"])
    replay = lab_turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)
    assert projection["turns"][0]["state"] == "completed"
    assert replay["replay_status"] == "completed"
    assert "Created scheduled job `job_plane_write`" in replay["content"]
    history = routes._mobile_session_history_payload("mpl")
    assert history["history"] == [
        {"role": "user", "content": " ".join(prompt.split()), "plane_routed": True},
        {
            "role": "assistant",
            "content": (
                "Created scheduled job `job_plane_write` (job_plane_write). kind=pipeline "
                "executor=codex model=gpt-5-codex schedule=once run_now=queued"
            ),
            "plane_routed": True,
        },
    ]


def test_plane_direct_eval_seed_status_prompts_have_direct_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes,
        "_lab_assistant_operator_snapshot_payload",
        lambda: {
            "operator_state": "idle",
            "active_tasks": [],
            "open_loops": [],
            "backend_projection": {"state": "reachable", "recovery_status": "ready"},
        },
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "list_jobs",
        lambda: [
            {
                "job_id": "job_a",
                "enabled": True,
                "display_title": "job a",
                "schedule": {"mode": "once", "run_at": "2026-03-30T20:00:00Z"},
                "state": {"active_run_id": "", "last_status": ""},
            },
            {
                "job_id": "job_b",
                "enabled": False,
                "display_title": "job b",
                "schedule": {"mode": "once", "run_at": "2026-03-30T21:00:00Z"},
                "state": {"active_run_id": "", "last_status": "blocked"},
            },
        ],
    )

    rows = _plane_direct_eval_rows(intents={"direct_status_queue", "direct_status_health"})
    prompts = [str(row.get("canonical_prompt") or "").strip() for row in rows]
    # The mined corpus contains some long explanatory prompts that mention health but are not
    # themselves direct liveness checks. Keep this regression focused on prompts the helper claims.
    supported = [
        prompt
        for prompt in prompts
        if routes._PLANE_MOBILE_QUEUE_RE.search(prompt) or routes._PLANE_MOBILE_HEALTH_RE.search(prompt)
    ]
    assert supported
    for prompt in supported:
        reply = routes._plane_mobile_direct_reply(prompt)
        assert reply is not None, prompt
        assert reply.strip(), prompt


def test_mobile_stream_forwards_commentary_events_before_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "commentary", "text": "Checking the generatorist path."}
            yield {"type": "done", "ok": True, "content": "Final answer"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"event": "commentary", "commentary": "Checking the generatorist path."}' in body
    assert 'data: {"delta": "Final answer"}' in body
    assert "data: [DONE]" in body


def test_mobile_stream_uses_incremental_deltas_when_runtime_emits_growing_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    appended: list[dict] = []
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: appended.append(dict(kwargs)))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "commentary", "text": "Checking the generatorist path."}
            yield {"type": "delta", "delta": "Final", "content": "Final"}
            yield {"type": "delta", "delta": " answer", "content": "Final answer"}
            yield {"type": "done", "ok": True, "content": "Final answer"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"event": "commentary", "commentary": "Checking the generatorist path."}' in body
    assert 'data: {"delta": "Final"}' in body
    assert 'data: {"delta": " answer"}' in body
    assert "data: [DONE]" in body
    assistant_rows = [row for row in appended if row.get("sender") == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "Final answer"


def test_mobile_stream_emits_structured_keepalive_events_while_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))
    monkeypatch.setattr(routes, "_MOBILE_STREAM_KEEPALIVE_INTERVAL_S", 0.02, raising=False)

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        time.sleep(0.7)
        yield {"type": "delta", "delta": "Final answer"}
        yield {"type": "done", "ok": True, "content": "Final answer"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"event":"keepalive"}' in body
    assert 'data: {"delta": "Final answer"}' in body
    assert "data: [DONE]" in body


def test_mobile_stream_keepalive_is_independent_of_backend_first_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    class _LockedBuffer(io.BytesIO):
        def __init__(self) -> None:
            super().__init__()
            self._lock = threading.Lock()

        def write(self, b):  # type: ignore[override]
            with self._lock:
                return super().write(b)

        def flush(self) -> None:  # type: ignore[override]
            return

        def snapshot(self) -> str:
            with self._lock:
                return self.getvalue().decode("utf-8")

    req.wfile = _LockedBuffer()
    release = threading.Event()

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_STREAM_KEEPALIVE_INTERVAL_S", 0.02, raising=False)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        release.wait(0.5)
        yield {"type": "delta", "delta": "Final answer"}
        yield {"type": "done", "ok": True, "content": "Final answer"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    worker = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(req,))  # type: ignore[arg-type]
    worker.start()
    time.sleep(0.08)
    partial_body = req.wfile.snapshot()
    release.set()
    worker.join(timeout=1.0)

    assert req._status == 200
    assert 'data: {"event":"keepalive"}' in partial_body
    assert '"delta": "Final answer"' not in partial_body
    body = req.wfile.snapshot()
    assert '"delta": "Final answer"' in body
    assert "data: [DONE]" in body


def test_mobile_stream_emits_chat_title_metadata_and_strips_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    appended: list[dict] = []
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: appended.append(dict(kwargs)))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "Ship the fix.", "content": "Ship the fix."}
            yield {"type": "done", "ok": True, "content": "Ship the fix.\nCHAT_HEADER_TITLE: Admin Toggle Fix"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"delta": "Ship the fix."}' in body
    assert 'data: {"event": "metadata", "chat_title": "Admin Toggle Fix"}' in body
    assert "CHAT_HEADER_TITLE:" not in body
    assistant_rows = [row for row in appended if row.get("sender") == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "Ship the fix.\nCHAT_HEADER_TITLE: Admin Toggle Fix"
    assert assistant_rows[0]["meta"]["chat_header_title"] == "Admin Toggle Fix"


def test_mobile_stream_dispatches_direct_host_verification_and_persists_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    seen: dict[str, str] = {}

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {
                "type": "done",
                "ok": True,
                "content": (
                    "Need host proof.\n\n"
                    "HOST_VERIFICATION_REQUEST_JSON:\n"
                    '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HttpChatApiTest"},"reason":"prove it"}'
                ),
            }

    def _fake_dispatch_direct_host_verification(*, content, role, binding, session_file, **_kwargs):
        seen["content"] = str(content)
        seen["role"] = str(role)
        seen["thread_id"] = str((binding or {}).get("id") or "")
        seen["session_file"] = str(session_file)
        return {
            "requested": True,
            "result": {"ok": True, "action_id": "android_unit_test", "request_id": "vrf-1"},
            "assistant_followup": {
                "sender": "assistant",
                "content": "Host verification result:\nPASS",
            },
        }

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())
    monkeypatch.setattr(routes._agents, "_maybe_dispatch_direct_host_verification", _fake_dispatch_direct_host_verification)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    assert seen["role"] == "generalist"
    assert seen["thread_id"] == "mbl"
    assert seen["session_file"].endswith("session__generalist.jsonl")
    assert "HOST_VERIFICATION_REQUEST_JSON:" in seen["content"]
    assert routes._mobile_session_history("mbl") == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Need host proof."},
        {"role": "assistant", "content": "Host verification result:\nPASS"},
    ]
    assert "data: [DONE]" in req.wfile.getvalue().decode("utf-8")


def test_mobile_stream_emits_progress_while_waiting_for_direct_host_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_HOST_VERIFICATION_PROGRESS_INTERVAL_S", 0.01)
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {
                "type": "done",
                "ok": True,
                "content": (
                    "Need host proof.\n\n"
                    "HOST_VERIFICATION_REQUEST_JSON:\n"
                    '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HttpChatApiTest"},"reason":"prove it"}'
                ),
            }

    def _fake_dispatch_direct_host_verification(*, content, role, binding, session_file, **_kwargs):
        time.sleep(0.05)
        return {
            "requested": True,
            "result": {"ok": True, "action_id": "android_unit_test", "request_id": "vrf-2"},
            "assistant_followup": {
                "sender": "assistant",
                "content": "Host verification result:\nPASS",
            },
        }

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())
    monkeypatch.setattr(routes._agents, "_maybe_dispatch_direct_host_verification", _fake_dispatch_direct_host_verification)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"delta": "Need host proof.' in body
    assert (
        'data: {"event": "commentary", "commentary": "Running host verification...", '
        '"phase": "host_verification_wait"}'
    ) in body
    assert "HOST_VERIFICATION_REQUEST_JSON:" not in body
    assert "data: [DONE]" in body
    assert routes._mobile_session_history("mbl") == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Need host proof."},
        {"role": "assistant", "content": "Host verification result:\nPASS"},
    ]


def test_mobile_stream_rejects_overlapping_turns_for_same_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {})
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    started = threading.Event()
    release = threading.Event()

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        started.set()
        release.wait(timeout=5)
        yield {"type": "done", "ok": True, "content": "first reply"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    def _make_req(payload: dict[str, str]) -> _DummyReq:
        req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
        req.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))
        req.headers = {"Content-Length": str(req.rfile.getbuffer().nbytes)}
        return req

    first_req = _make_req({"message": "first", "agent_key": "a"})
    second_req = _make_req({"message": "second", "agent_key": "a"})

    thread = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(first_req,))  # type: ignore[arg-type]
    thread.start()
    assert started.wait(timeout=2)

    routes.WebUIRequestHandler.do_POST(second_req)  # type: ignore[arg-type]
    second_body = second_req.wfile.getvalue().decode("utf-8")
    assert 'another turn is already running for this tab' in second_body

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_mobile_stream_allows_same_request_id_to_reattach_without_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {})
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    started = threading.Event()
    release = threading.Event()
    call_count = {"count": 0}

    def _fake_send_message_stream_events(*, role, binding, message, options=None):
        call_count["count"] += 1
        if call_count["count"] == 1:
            started.set()
            release.wait(timeout=5)
        yield {"type": "done", "ok": True, "content": "first reply"}

    monkeypatch.setattr(routes._agents, "send_message_stream_events", _fake_send_message_stream_events)

    def _make_req() -> _DummyReq:
        payload = {"message": "first", "agent_key": "a", "request_id": "req-mobile-1", "idempotency_key": "req-mobile-1"}
        req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
        req.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))
        req.headers = {"Content-Length": str(req.rfile.getbuffer().nbytes)}
        return req

    first_req = _make_req()
    second_req = _make_req()

    thread = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(first_req,))  # type: ignore[arg-type]
    thread.start()
    assert started.wait(timeout=2)

    routes.WebUIRequestHandler.do_POST(second_req)  # type: ignore[arg-type]
    release.set()
    thread.join(timeout=5)

    second_body = second_req.wfile.getvalue().decode("utf-8")
    assert 'another turn is already running for this tab' not in second_body
    assert "data: [DONE]" in second_body


def test_mobile_stream_reaps_stale_in_memory_claim_when_worker_is_not_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__builder_mobile.md").write_text("builder mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {
        "mbu::builder": {
            "control_id": "stale-1",
            "key": "mbu::builder",
            "tid": "mbu",
            "role": "builder",
            "request_id": "old-request",
            "created_monotonic": 1.0,
            "terminal_status": "",
            "terminal_message": "",
            "terminal_event": threading.Event(),
            "superseded": False,
        }
    })
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAM_STALE_GRACE_S", 0.0, raising=False)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "b"})
    monkeypatch.setattr(routes._agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))

    class _Mgr:
        def get_worker(self, *, binding, role):
            return {"ok": True, "exists": True, "status": "ready", "worker_id": "mbu-builder"}

        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "done", "ok": True, "content": "ok"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'another turn is already running for this tab' not in body
    assert "data: [DONE]" in body
    assert routes._MOBILE_ACTIVE_STREAMS == {}


def test_mobile_stream_forwards_snapshot_only_final_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": "hello", "agent_key": "a"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    appended: list[dict] = []
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: appended.append(dict(kwargs)))

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "", "content": "Final answer"}
            yield {"type": "done", "ok": True, "content": "Final answer"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert 'data: {"delta": "Final answer"}' in body
    assert "data: [DONE]" in body
    assistant_rows = [row for row in appended if row.get("sender") == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "Final answer"


def test_mobile_backlog_page_serves_dedicated_static_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/mobile/backlog")
    seen: dict[str, str] = {}

    def _fake_serve_static_file(_handler, _static_dir, rel_path: str) -> bool:
        seen["rel_path"] = str(rel_path)
        return True

    monkeypatch.setattr(routes, "_serve_static_file", _fake_serve_static_file)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert seen["rel_path"] == "mobile_backlog.html"


def test_mobile_backlog_endpoint_filters_to_mobile_relevant_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/backlog?limit=50")

    class _Ledger:
        def list_items(self, *, limit: int = 200):
            assert limit == 50
            return [
                {"uid": "PIS-20260309-120000Z-AAAA", "status": "draft", "release_state": "draft", "title": "draft item"},
                {"uid": "PIS-20260309-120001Z-BBBB", "status": "ready_to_implement", "release_state": "draft", "title": "ready item"},
                {"uid": "PIS-20260309-120002Z-CCCC", "status": "done", "release_state": "released", "title": "closed item"},
                {"uid": "PIS-20260309-120003Z-DDDD", "status": "done", "release_state": "draft", "title": "not released yet"},
            ]

    monkeypatch.setattr(routes, "ChangeLedger", _Ledger)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_backlog_v1"
    assert [it["uid"] for it in payload["items"]] == [
        "PIS-20260309-120000Z-AAAA",
        "PIS-20260309-120001Z-BBBB",
        "PIS-20260309-120003Z-DDDD",
    ]
    assert payload["counts"] == {"draft": 1, "ready": 2, "total": 3}


def test_mobile_backlog_endpoint_projects_topic_tags_and_supports_topic_group_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/backlog?limit=50&topic=mobile&group=topic")

    class _Ledger:
        def list_items(self, *, limit: int = 200):
            assert limit == 50
            return [
                {
                    "uid": "PIS-20260309-120000Z-AAAA",
                    "status": "draft",
                    "release_state": "draft",
                    "title": "mobile ui",
                    "tags": ["mobile", "PIS-20260309-120010Z-ZZZZ", "bad space", "operator-experience"],
                },
                {
                    "uid": "PIS-20260309-120001Z-BBBB",
                    "status": "ready_to_implement",
                    "release_state": "draft",
                    "title": "web ui",
                    "topic_tags": ["operator-experience", "web"],
                },
            ]

    monkeypatch.setattr(routes, "ChangeLedger", _Ledger)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "list_jobs", lambda: [])

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_backlog_v1"
    assert payload["filters"] == {"topic": "mobile", "group_mode": "topic"}
    assert [it["uid"] for it in payload["items"]] == ["PIS-20260309-120000Z-AAAA"]
    assert payload["items"][0]["topic_tags"] == ["mobile", "operator-experience"]
    assert payload["groups"] == [
        {"key": "mobile", "count": 1, "uids": ["PIS-20260309-120000Z-AAAA"]},
        {"key": "operator-experience", "count": 1, "uids": ["PIS-20260309-120000Z-AAAA"]},
    ]
    assert payload["topic_catalog"] == [
        {"tag": "mobile", "count": 1},
        {"tag": "operator-experience", "count": 1},
    ]


def test_mobile_backlog_endpoint_projects_scheduled_jobs_only_for_bound_ledger_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/backlog?limit=20")

    class _Ledger:
        def list_items(self, *, limit: int = 200):
            assert limit == 20
            return [
                {
                    "uid": "PIS-20260309-120000Z-AAAA",
                    "status": "draft",
                    "release_state": "draft",
                    "title": "mobile ui",
                    "topic_tags": ["mobile", "ledger"],
                },
                {
                    "uid": "PIS-20260309-120001Z-BBBB",
                    "status": "ready_to_implement",
                    "release_state": "draft",
                    "title": "web ui",
                    "topic_tags": ["web"],
                },
            ]

    monkeypatch.setattr(routes, "ChangeLedger", _Ledger)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "list_jobs",
        lambda: [
            {
                "job_id": "job-bound",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": "PIS-20260309-120000Z-AAAA"},
                "due": {"reason": "due"},
            },
            {
                "job_id": "job-task",
                "job_kind": "single_turn",
                "enabled": True,
                "binding": {"type": "task_id", "id": "mbu"},
                "due": {"reason": "due"},
            },
            {
                "job_id": "job-missing",
                "job_kind": "single_turn",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": "PIS-20260309-120099Z-ZZZZ"},
                "due": {"reason": "due"},
            },
        ],
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_backlog_v1"
    assert payload["scheduled_bindings"] == {
        "PIS-20260309-120000Z-AAAA": [
            {
                "job_id": "job-bound",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "due_reason": "due",
                "topic_tags": ["mobile", "ledger"],
            }
        ]
    }


def test_mobile_cockpit_endpoint_projects_shared_contract_and_latest_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/cockpit?limit=20&selected_uid=PIS-20260309-120000Z-AAAA")

    monkeypatch.setattr(
        routes._mobile_cockpit,
        "cockpit_payload",
        lambda **kwargs: {
            "schema_version": "mobile_cockpit_v1",
            "utc": "2026-04-02T09:00:00Z",
            "contracts": {
                "schema_version": "mobile_cockpit_contract_v1",
                "contract_check": {"ok": True},
                "entity_contracts": {"pis": {"owner": "ledger"}},
            },
            "selection": {
                "selected_uids": ["PIS-20260309-120000Z-AAAA"],
                "primary_uid": "PIS-20260309-120000Z-AAAA",
                "selected_count": 1,
            },
            "pis": {
                "items": [{"uid": "PIS-20260309-120000Z-AAAA", "status": "draft", "title": "mobile ui"}],
                "topic_catalog": [],
                "groups": [],
                "scheduled_bindings": {},
                "counts": {"draft": 1, "ready": 0, "total": 1},
            },
            "campaigns": {
                "latest": {"campaign_id": "mcamp-20260402t090000z", "launch_outcome": "started"},
            },
            "execution_queue": {
                "state": {"status": "running", "current_uid": "PIS-20260309-120000Z-AAAA"},
                "queue_preview": [],
                "batch_report": {"total_count": 1},
                "worker_alive": True,
            },
            "artifacts": {"status": "ok", "latest_handoff": {"name": "handoff.md", "preview_text": "preview"}},
            "run_triage": {"status": "running", "failure_case": {"failure_class": ""}},
            "economics": {"status": "degraded", "token_usage": {"total_tokens": 0}, "waste_warning": "token_usage_unavailable"},
            "archive_snapshots": {"status": "ok", "latest_repo_snapshot": {"snapshot_id": "repo.zip"}, "run_archives": []},
            "chronicle": {"status": "ok", "entries": [{"entry_id": "chron-1", "kind": "campaign", "summary": "Campaign started", "source": "mobile_cockpit_campaign"}]},
            "shell": {"status": "low_trust", "warnings": ["economics:degraded"], "fallback_actions": ["refresh_projection"], "executor_identity": {"campaign_id": "mcamp-20260402t090000z"}},
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_cockpit_v1"
    assert payload["contracts"]["contract_check"]["ok"] is True
    assert payload["campaigns"]["latest"]["campaign_id"] == "mcamp-20260402t090000z"
    assert payload["selection"]["primary_uid"] == "PIS-20260309-120000Z-AAAA"
    assert payload["artifacts"]["latest_handoff"]["name"] == "handoff.md"
    assert payload["archive_snapshots"]["latest_repo_snapshot"]["snapshot_id"] == "repo.zip"
    assert payload["chronicle"]["entries"][0]["kind"] == "campaign"
    assert payload["shell"]["status"] == "low_trust"


def test_mobile_cockpit_endpoint_embeds_real_backlog_projection_with_open_idea_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/cockpit?limit=20")

    ledger = routes._mobile_cockpit.ChangeLedger(repo_root=tmp_path)
    ledger.create_item(
        title="Cockpit-visible PIS",
        uid_override="PIS-20260501-070715Z-B606",
        pis_markdown="# Patch Instruction Sheet\n\nVisible from the cockpit.\n",
    )
    open_note = idea_note_manager.IdeaNoteLedger(repo_root=tmp_path).create_note(
        title="Open idea note",
        uid_override="IDEA-20260501-070716Z-A1B2",
        idea_markdown="# Idea Note\n\nThis one should be projected.\n",
        status="active",
        why_now="Operator should be able to pick this up from the plane.",
        priority_signal="high leverage",
        resume_context="Show this note in the cockpit backlog.",
        resume_blockers=["Needs a visible backlog row."],
    )
    discarded_note = idea_note_manager.IdeaNoteLedger(repo_root=tmp_path).create_note(
        title="Discarded idea note",
        uid_override="IDEA-20260501-070717Z-C3D4",
        idea_markdown="# Idea Note\n\nThis one should stay hidden.\n",
        status="discarded",
        why_now="Historical only.",
        resume_context="Should not be resumable.",
    )

    monkeypatch.setattr(routes._mobile_cockpit, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        routes._mobile_cockpit._execution_queue,
        "status",
        lambda: {
            "state": {"status": "idle", "run_id": ""},
            "queue_preview": [],
            "batch_report": {"total_count": 0},
            "worker_alive": False,
        },
    )
    monkeypatch.setattr(routes._mobile_cockpit, "_load_latest_campaign", lambda: {})
    monkeypatch.setattr(
        routes._mobile_cockpit,
        "_archive_snapshot_payload",
        lambda: {"status": "degraded", "latest_repo_snapshot": {}, "run_archives": []},
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    backlog_items = payload["pis"]["items"]
    assert [item["uid"] for item in backlog_items] == [
        "PIS-20260501-070715Z-B606",
        open_note["uid"],
    ]
    idea_item = next(item for item in backlog_items if item["uid"] == open_note["uid"])
    assert idea_item["backlog_kind"] == "idea_note"
    assert idea_item["release_state"] == "pre_intent"
    assert idea_item["why_now"] == "Operator should be able to pick this up from the plane."
    assert idea_item["priority_signal"] == "high leverage"
    assert idea_item["resume_context"] == "Show this note in the cockpit backlog."
    assert idea_item["resume_blockers"] == ["Needs a visible backlog row."]
    assert all(item["uid"] != discarded_note["uid"] for item in backlog_items)


def test_mobile_backlog_static_contract_exposes_phone_friendly_filter_controls() -> None:
    html = (Path(__file__).resolve().parents[3] / "infra" / "webui" / "static" / "mobile_backlog.html").read_text(
        encoding="utf-8"
    )
    assert "id=\"topicFilterSelect\"" in html
    assert "id=\"groupModeSelect\"" in html
    assert "id=\"groupSummary\"" in html
    assert "id=\"btnLaunchCampaign\"" in html
    assert "id=\"dependencyModeSelect\"" in html
    assert "id=\"btnExportHandoff\"" in html
    assert "id=\"btnCreateArchive\"" in html
    assert "id=\"artifactPreview\"" in html
    assert "id=\"chroniclePreview\"" in html
    assert "id=\"shellSummary\"" in html
    assert "@media (max-width: 680px)" in html


def test_mobile_backlog_prioritize_endpoint_promotes_selected_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/backlog/prioritize")
    seen: dict[str, str | int] = {}

    class _Ledger:
        def prioritize_item(self, uid: str):
            seen["uid"] = str(uid)
            return {
                "ok": True,
                "uid": str(uid),
                "execution_priority": -1000,
                "updated_utc": "2026-03-11T20:00:00Z",
            }

        def append_event(self, uid: str, *, event_type: str, payload: dict):
            seen["event_uid"] = str(uid)
            seen["event_type"] = str(event_type)
            seen["event_priority"] = int(payload.get("execution_priority"))
            return {"ok": True}

    monkeypatch.setattr(routes, "ChangeLedger", _Ledger)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"uid": "PIS-20260311-120000Z-A1B2"})

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_backlog_priority_update_v1"
    assert payload["uid"] == "PIS-20260311-120000Z-A1B2"
    assert payload["execution_priority"] == -1000
    assert seen["uid"] == "PIS-20260311-120000Z-A1B2"
    assert seen["event_uid"] == "PIS-20260311-120000Z-A1B2"
    assert seen["event_type"] == "mobile_backlog_prioritized"


def test_mobile_backlog_endpoint_includes_open_idea_notes_with_operator_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/backlog?limit=50")

    class _Ledger:
        def list_items(self, *, limit: int = 200):
            assert limit == 50
            return [
                {
                    "uid": "PIS-20260309-120000Z-AAAA",
                    "status": "draft",
                    "release_state": "draft",
                    "title": "mobile ui",
                    "topic_tags": ["mobile"],
                }
            ]

    class _IdeaLedger:
        def __init__(self, *, repo_root=None):
            assert repo_root is not None

        def list_notes(self, *, limit: int = 200):
            assert limit == 50
            return [
                {
                    "uid": "IDEA-20260309-120100Z-I1A2",
                    "status": "parked",
                    "title": "idea backlog slice",
                    "updated_utc": "2026-03-09T12:01:00Z",
                    "created_utc": "2026-03-09T12:01:00Z",
                    "topic_tags": ["mobile", "ideas"],
                    "why_now": "Operator needs the next thin intake layer.",
                    "priority_signal": "high leverage",
                    "resume_context": "Expose this in the plane before it disappears again.",
                    "resume_blockers": ["Need an operator surface."],
                },
                {
                    "uid": "IDEA-20260309-120101Z-I1A3",
                    "status": "discarded",
                    "title": "discarded idea",
                    "updated_utc": "2026-03-09T12:02:00Z",
                },
            ]

    monkeypatch.setattr(routes, "ChangeLedger", _Ledger)
    monkeypatch.setattr(routes, "_IdeaNoteLedger", _IdeaLedger, raising=False)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "list_jobs", lambda: [])

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert [it["uid"] for it in payload["items"]] == [
        "PIS-20260309-120000Z-AAAA",
        "IDEA-20260309-120100Z-I1A2",
    ]
    idea = next(it for it in payload["items"] if it["uid"] == "IDEA-20260309-120100Z-I1A2")
    assert idea["backlog_kind"] == "idea_note"
    assert idea["release_state"] == "pre_intent"
    assert idea["why_now"] == "Operator needs the next thin intake layer."
    assert idea["priority_signal"] == "high leverage"
    assert idea["resume_context"] == "Expose this in the plane before it disappears again."
    assert idea["resume_blockers"] == ["Need an operator surface."]


def test_idea_note_routes_round_trip_detail_and_action_against_ledger_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(idea_note_manager.ledger_storage, "repo_root_from_here", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_token="tok")
    ledger = idea_note_manager.IdeaNoteLedger(repo_root=tmp_path)
    created = ledger.create_note(
        title="Idea action route",
        idea_markdown="# Idea Note\n\nGive the operator a real way back in.\n",
        why_now="Need a first-class pre-intent surface.",
        priority_signal="now",
        resume_context="Add explicit resume and park actions.",
        resume_blockers=["Need a projected list view."],
    )
    uid = str(created["uid"])

    detail_req = _DummyReq(cfg=cfg, path=f"/api/idea_notes/{uid}")
    routes.WebUIRequestHandler.do_GET(detail_req)  # type: ignore[arg-type]
    assert detail_req._status == 200
    detail_payload = _read_payload(detail_req)
    assert detail_payload["meta"]["uid"] == uid
    assert detail_payload["meta"]["resume_context"] == "Add explicit resume and park actions."

    post_req = _make_post_req(
        cfg,
        f"/api/idea_notes/{uid}/action",
        {
            "action": "park",
            "note": "pause until the operator picks it back up",
            "resume_context": "Add explicit resume and park actions.",
            "resume_blockers": ["Need a projected list view."],
        },
    )
    post_req.headers["X-Admin-Token"] = "tok"
    routes.WebUIRequestHandler.do_POST(post_req)  # type: ignore[arg-type]
    assert post_req._status == 200
    action_payload = _read_payload(post_req)
    assert action_payload["status"] == "parked"

    ledger_record = ledger.read_note(uid)
    assert ledger_record["meta"]["status"] == "parked"

    refreshed_req = _DummyReq(cfg=cfg, path=f"/api/idea_notes/{uid}")
    routes.WebUIRequestHandler.do_GET(refreshed_req)  # type: ignore[arg-type]
    refreshed_payload = _read_payload(refreshed_req)
    assert refreshed_payload["meta"]["status"] == ledger_record["meta"]["status"]
    assert refreshed_payload["meta"]["resume_context"] == ledger_record["meta"]["resume_context"]
    assert refreshed_payload["meta"]["resume_blockers"] == ledger_record["meta"]["resume_blockers"]


def test_idea_note_action_route_fails_closed_for_auth_and_invalid_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(idea_note_manager.ledger_storage, "repo_root_from_here", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_token="tok")
    ledger = idea_note_manager.IdeaNoteLedger(repo_root=tmp_path)
    created = ledger.create_note(
        title="Idea auth failure",
        idea_markdown="# Idea Note\n\nDon't mutate on bad requests.\n",
        why_now="Need fail-closed behavior.",
        resume_context="Keep the idea note stable.",
    )
    uid = str(created["uid"])

    unauthorized_req = _make_post_req(cfg, f"/api/idea_notes/{uid}/action", {"action": "discard"})
    routes.WebUIRequestHandler.do_POST(unauthorized_req)  # type: ignore[arg-type]
    assert unauthorized_req._status == 401
    assert ledger.read_note(uid)["meta"]["status"] == "draft"

    invalid_req = _make_post_req(cfg, f"/api/idea_notes/{uid}/action", {"action": "dance"})
    invalid_req.headers["X-Admin-Token"] = "tok"
    routes.WebUIRequestHandler.do_POST(invalid_req)  # type: ignore[arg-type]
    assert invalid_req._status == 400
    invalid_payload = _read_payload(invalid_req)
    assert "unsupported idea note action" in invalid_payload["error"]
    assert ledger.read_note(uid)["meta"]["status"] == "draft"


def test_mobile_cockpit_campaign_launch_endpoint_uses_shared_launch_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(
        cfg,
        "/api/mobile/cockpit/campaigns/launch",
        {
            "selected_uids": ["PIS-20260311-120000Z-A1B2", "PIS-20260311-120001Z-B2C3"],
            "dependency_mode": "selected_order",
            "phase_label": "WF-01",
            "max_iterations": 2,
        },
    )
    seen: dict[str, object] = {}

    def _fake_launch_campaign(**kwargs):
        seen.update(kwargs)
        return {
            "schema_version": "mobile_cockpit_campaign_v1",
            "utc": "2026-04-02T09:00:00Z",
            "campaign": {
                "campaign_id": "mcamp-20260402t090000z",
                "launch_outcome": "started",
            },
        }

    monkeypatch.setattr(routes._mobile_cockpit, "launch_campaign", _fake_launch_campaign)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["campaign"]["campaign_id"] == "mcamp-20260402t090000z"
    assert seen["selected_uids"] == ["PIS-20260311-120000Z-A1B2", "PIS-20260311-120001Z-B2C3"]
    assert seen["dependency_mode"] == "selected_order"
    assert seen["phase_label"] == "WF-01"
    assert seen["origin_surface"] == "the_plane_mobile_backlog"
    assert seen["workflow_options"] == {"max_iterations": 2}


def test_mobile_cockpit_artifact_handoff_endpoint_exports_ledger_bound_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(
        cfg,
        "/api/mobile/cockpit/artifacts/handoff",
        {"ledger_uid": "PIS-20260311-120000Z-A1B2", "role": "builder"},
    )
    seen: dict[str, object] = {}

    def _fake_export_handoff(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "artifact_path": "governance/agents/sessions/PIS-20260311-120000Z-A1B2/handoff.md"}

    monkeypatch.setattr(routes._agents, "export_handoff", _fake_export_handoff)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_cockpit_artifact_handoff_v1"
    assert seen["role"] == "builder"
    assert seen["binding"] == {"type": "ledger_uid", "id": "PIS-20260311-120000Z-A1B2"}


def test_mobile_cockpit_archive_create_endpoint_returns_snapshot_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="secret")
    req = _make_post_req(cfg, "/api/mobile/cockpit/archive/create?token=secret", {"debug": False})
    archive_path = tmp_path / "archives" / "snapshot.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text("zip", encoding="utf-8")

    monkeypatch.setattr(routes, "_admin_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_run_snapshot_script", lambda repo_root, debug=False: (archive_path, "", "", 0))

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_cockpit_archive_create_v1"
    assert payload["snapshot_id"] == "snapshot.zip"
    assert payload["download_url"] == "/api/admin/archive/download/snapshot.zip"


def test_mobile_apk_manifest_exposes_active_build_when_no_release_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/manifest?channel=debug")
    job = http_core.AdminCmdJob(
        job_id="job123",
        kind="mobile_apk_build:debug",
        cmd="infra/mobile/build_server_apk.sh debug",
        cwd_rel="",
        cwd_abs=str(tmp_path),
        utc_started="2026-03-10T17:00:00Z",
        t_started=time.time() - 30.0,
        timeout_sec=3600.0,
        stdout="> Task :app:assembleDebug\n",
        last_output_utc="2026-03-10T17:00:20Z",
    )
    monkeypatch.setattr(routes, "_admin_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_mobile_apk_find_release", lambda _repo_root, _channel: None)
    monkeypatch.setattr(routes, "_mobile_apk_running_build_job", lambda _state, _channel: job)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_apk_manifest_v1"
    assert payload["channel"] == "debug"
    assert payload["release_available"] is False
    assert payload["active_build_job"]["job_id"] == "job123"
    assert payload["active_build_job"]["running"] is True
    assert payload["active_build_job"]["last_output_utc"] == "2026-03-10T17:00:20Z"




def test_mobile_apk_manifest_includes_release_integrity_when_release_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/manifest?channel=debug")
    release = {
        "channel": "debug",
        "package_name": "one.dufour.chatgpt",
        "version_name": "0.2.3-debug",
        "version_code": 42,
        "apk_name": routes._MOBILE_APK_CHANNEL_META["debug"]["apk_name"],
        "apk_size_bytes": 1234,
        "apk_sha256": "abc",
        "built_at": "2026-03-10T17:00:20Z",
        "source_path": f"runtime/artifacts/mobile_apk/debug/{routes._MOBILE_APK_CHANNEL_META['debug']['apk_name']}",
    }
    monkeypatch.setattr(routes, "_admin_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_mobile_apk_find_release", lambda _repo_root, _channel: release)
    monkeypatch.setattr(routes, "_mobile_apk_running_build_job", lambda _state, _channel: None)
    monkeypatch.setattr(routes, "_mobile_apk_latest_build_job", lambda _state, _cfg, _channel: None)
    monkeypatch.setattr(
        routes,
        "_mobile_apk_release_integrity",
        lambda _repo_root, _channel: {
            "schema_version": "mobile_apk_release_integrity_v1",
            "status": "proof_gap",
            "validation_scope": "artifact_build_only",
            "proof_gap": True,
        },
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["release_integrity"]["status"] == "proof_gap"
    assert payload["release_integrity"]["validation_scope"] == "artifact_build_only"
def test_mobile_apk_manifest_exposes_latest_failed_build_when_no_release_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/manifest?channel=debug")
    logs_dir = tmp_path / "admin_cmd_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    _write_logged_mobile_apk_job(logs_dir, job_id="job999", cwd_abs=str(tmp_path))
    monkeypatch.setattr(routes, "_admin_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_cmd_logs_dir", lambda _cfg: logs_dir)
    monkeypatch.setattr(routes, "_mobile_apk_find_release", lambda _repo_root, _channel: None)
    monkeypatch.setattr(routes, "_mobile_apk_running_build_job", lambda _state, _channel: None)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_apk_manifest_v1"
    assert payload["channel"] == "debug"
    assert payload["release_available"] is False
    assert payload["latest_build_job"]["job_id"] == "job999"
    assert payload["latest_build_job"]["returncode"] == 1
    assert payload["latest_build_job"]["stderr"] == "e: Unresolved reference: buttonRef\n"


def test_mobile_apk_cancel_accepts_post_and_targets_build_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/cancel")
    req.headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(b'{"job_id":"job123"}')),
    }
    req.rfile = io.BytesIO(b'{"job_id":"job123"}')
    req.server.state = http_core._ServerState(cfg)

    job = http_core.AdminCmdJob(
        job_id="job123",
        kind="mobile_apk_build:debug",
        cmd="infra/mobile/build_server_apk.sh debug",
        cwd_rel="",
        cwd_abs=str(tmp_path),
        utc_started="2026-03-10T17:00:00Z",
        t_started=time.time() - 5.0,
        timeout_sec=3600.0,
    )
    req.server.state.admin_cmd_jobs[job.job_id] = job

    seen: dict[str, str] = {}

    def _fake_kill_job(state: http_core._ServerState, job_id: str) -> bool:
        seen["job_id"] = str(job_id)
        state.admin_cmd_jobs[job_id].killed = True
        state.admin_cmd_jobs[job_id].running = False
        state.admin_cmd_jobs[job_id].returncode = 143
        return True

    monkeypatch.setattr(routes, "_admin_cmd_kill_job", _fake_kill_job)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_apk_cancel_v1"
    assert payload["ok"] is True
    assert payload["job"]["job_id"] == "job123"
    assert payload["job"]["killed"] is True
    assert payload["job"]["running"] is False
    assert seen["job_id"] == "job123"


def test_mobile_apk_build_status_resolves_finished_logged_job_when_memory_is_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/build_status?job_id=job999")
    req.server.state = http_core._ServerState(cfg)
    logs_dir = tmp_path / "admin_cmd_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    _write_logged_mobile_apk_job(logs_dir, job_id="job999", cwd_abs=str(tmp_path))

    monkeypatch.setattr(routes, "_admin_cmd_logs_dir", lambda _cfg: logs_dir)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_apk_build_status_v1"
    assert payload["job"]["job_id"] == "job999"
    assert payload["job"]["running"] is False
    assert payload["job"]["returncode"] == 1


def test_mobile_apk_cancel_returns_finished_logged_job_when_memory_is_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/mobile_apk/cancel")
    req.headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(b'{"job_id":"job999"}')),
    }
    req.rfile = io.BytesIO(b'{"job_id":"job999"}')
    req.server.state = http_core._ServerState(cfg)
    logs_dir = tmp_path / "admin_cmd_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    _write_logged_mobile_apk_job(logs_dir, job_id="job999", cwd_abs=str(tmp_path))

    monkeypatch.setattr(routes, "_admin_cmd_logs_dir", lambda _cfg: logs_dir)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "mobile_apk_cancel_v1"
    assert payload["ok"] is False
    assert payload["already_finished"] is True
    assert payload["job"]["job_id"] == "job999"
    assert payload["job"]["running"] is False


def test_admin_cmd_job_dict_includes_output_progress_fields() -> None:
    job = http_core.AdminCmdJob(
        job_id="abc123",
        kind="mobile_apk_build:debug",
        cmd="infra/mobile/build_server_apk.sh debug",
        cwd_rel="",
        cwd_abs="/tmp/repo",
        utc_started="2026-03-10T17:00:00Z",
        t_started=time.time() - 5.0,
        timeout_sec=3600.0,
        stdout="Gradle is running\n",
        last_output_utc="2026-03-10T17:00:03Z",
    )

    payload = job.to_dict()

    assert payload["output_seen"] is True
    assert payload["last_output_utc"] == "2026-03-10T17:00:03Z"
    assert payload["stdout"] == "Gradle is running\n"


def test_admin_cmd_job_finishes_when_parent_exits_but_background_child_holds_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    state = http_core._ServerState(cfg)
    logs_dir = tmp_path / "admin_cmd_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(http_core, "_admin_cmd_logs_dir", lambda _cfg: logs_dir)

    job = http_core._admin_cmd_start_job(
        state,
        cfg,
        cmd="sleep 30 & echo CHILD:$!; exit 0",
        kind="cmd",
        timeout_sec=30.0,
    )

    deadline = time.time() + 4.0
    current = job
    while time.time() < deadline:
        with state.lock:
            current = state.admin_cmd_jobs[job.job_id]
        if not current.running:
            break
        time.sleep(0.1)

    child_pid = 0
    m = re.search(r"CHILD:(\d+)", str(current.stdout or ""))
    if m:
        child_pid = int(m.group(1))

    try:
        assert current.running is False
        assert current.returncode == 0
        assert current.utc_finished
        assert any(job.job_id in p.name for p in logs_dir.glob("*.json"))
    finally:
        if child_pid > 0:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_admin_cmd_start_job_exports_job_id_to_child_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    state = http_core._ServerState(cfg)
    logs_dir = tmp_path / "admin_cmd_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(http_core, "_admin_cmd_logs_dir", lambda _cfg: logs_dir)

    job = http_core._admin_cmd_start_job(
        state,
        cfg,
        cmd='printf "JOB:%s\\n" "$EIEL_ADMIN_JOB_ID"',
        kind="cmd",
        timeout_sec=10.0,
    )

    deadline = time.time() + 4.0
    current = job
    while time.time() < deadline:
        with state.lock:
            current = state.admin_cmd_jobs[job.job_id]
        if not current.running:
            break
        time.sleep(0.1)

    assert current.running is False
    assert current.returncode == 0
    assert f"JOB:{job.job_id}" in str(current.stdout or "")


def test_admin_server_restart_rejects_force_override_when_visible_work_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _make_post_req(cfg, "/api/admin/server/restart", {"force": True})

    monkeypatch.setattr(
        routes,
        "_admin_restart_preflight",
        lambda _cfg, state=None: {
            "schema_version": "admin_server_restart_preflight_v1",
            "utc": "2026-03-30T10:00:00Z",
            "ok": False,
            "safe_to_restart": False,
            "blockers": [{"kind": "active_tasks_present", "count": 1, "summary": "1 active task(s) are still visible to the plane."}],
            "operator_snapshot_summary": {"operator_state": "waiting_on_job", "active_task_count": 1, "open_loop_count": 0},
            "scheduler": {"active_job_lock_count": 0},
        },
    )

    restarted: dict[str, bool] = {"called": False}
    monkeypatch.setattr(routes, "_admin_restart_webui", lambda _cfg: restarted.__setitem__("called", True))

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _read_payload(req)
    assert payload["error"] == "restart_blocked_active_work"
    assert payload["safe_to_restart"] is False
    assert payload["force_requested"] is True
    assert restarted["called"] is False
