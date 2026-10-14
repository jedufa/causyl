from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: bytes = b"") -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=_DummyState(cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


class _DummyState:
    def __init__(self, cfg: routes.WebUIConfig) -> None:
        self.cfg = cfg
        self.run_index: dict[str, tuple[Path, Path]] = {}

    def clear_run_index(self) -> None:
        self.run_index.clear()

    def set_run_index_item(self, run_key: str, run_root: Path, sim_root: Path) -> None:
        self.run_index[str(run_key)] = (run_root, sim_root)


def _json_body(req: _DummyReq) -> dict:
    raw = req.wfile.getvalue().decode("utf-8")
    start = raw.find("{")
    assert start >= 0
    return json.loads(raw[start:])


def test_schedules_get_and_save_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-08T04:00:00Z", "last_error": ""})
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda **kwargs: {"ok": True, "assistant": {"content": "scheduled ok", "status": "ok", "meta": {}}},
    )
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    body = json.dumps(
        {
            "job_id": "webui_schedule_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Write a report.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    ).encode("utf-8")
    save_req = _DummyReq(cfg=cfg, path="/api/schedules/job/save", body=body)
    routes.WebUIRequestHandler.do_POST(save_req)  # type: ignore[arg-type]
    assert save_req._status == 200
    saved = _json_body(save_req)
    assert saved["job"]["job_id"] == "webui_schedule_001"

    list_req = _DummyReq(cfg=cfg, path="/api/schedules/jobs")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    assert list_req._status == 200
    listed = _json_body(list_req)
    listed_job = next(row for row in listed["jobs"] if row["job_id"] == "webui_schedule_001")
    assert listed_job["scheduler_projection_v1"]["projected_status"] == "queued"
    assert listed["scheduler_projection_contract"]["uid"] == "PIS-20260408-181100Z-D1E1"
    assert listed["scheduler"]["running"] is True

    def _fake_scheduler_tick(*, limit: int = 10, **_kwargs: object) -> dict:
        run_id = "sj_test_webui_schedule_001"
        receipt_path = (
            sched.receipts_dir(tmp_path)
            / sched._safe_name("webui_schedule_001")
            / f"{sched._safe_name(run_id)}.json"
        ).resolve()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "schema_version": "scheduled_job_receipt_v1",
            "job_id": "webui_schedule_001",
            "job_kind": "single_turn",
            "job_shape": "verification_loop_job",
            "job_shape_source": "compatibility_inferred_from_job_kind",
            "job_shape_compatibility_mode": True,
            "run_id": run_id,
            "status": "ok",
            "ok": True,
            "trigger": "scheduler",
            "slot_key": "2026-03-08T03:30:00Z",
            "started_utc": "2026-03-08T04:00:00Z",
            "finished_utc": "2026-03-08T04:00:01Z",
        }
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        sched._write_state(
            "webui_schedule_001",
            {
                "job_id": "webui_schedule_001",
                "last_status": "ok",
                "last_trigger": "scheduler",
                "last_slot_key": "2026-03-08T03:30:00Z",
                "last_started_utc": "2026-03-08T04:00:00Z",
                "last_finished_utc": "2026-03-08T04:00:01Z",
                "last_receipt_path": str(receipt_path),
                "active_run_id": "",
                "updated_utc": "2026-03-08T04:00:01Z",
            },
            repo_root=tmp_path,
        )
        return {
            "ok": True,
            "submitted": [{"job_id": "webui_schedule_001"}],
            "submitted_count": 1,
            "executed": [{"job_id": "webui_schedule_001", "ok": True, "receipt_path": str(receipt_path)}],
            "executed_count": 1,
            "deferred": [],
            "deferred_count": 0,
            "skipped": [],
            "skipped_count": 0,
            "active_count": 0,
        }

    monkeypatch.setattr(routes._scheduled_jobs, "scheduler_tick", _fake_scheduler_tick)

    tick_req = _DummyReq(cfg=cfg, path="/api/schedules/tick", body=json.dumps({"limit": 10}).encode("utf-8"))
    routes.WebUIRequestHandler.do_POST(tick_req)  # type: ignore[arg-type]
    assert tick_req._status == 200
    ticked = _json_body(tick_req)
    assert ticked["ok"] is True

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if sched.list_receipts("webui_schedule_001", repo_root=tmp_path):
            break
        time.sleep(0.05)
    assert len(sched.list_receipts("webui_schedule_001", repo_root=tmp_path)) == 1

    job_req = _DummyReq(cfg=cfg, path="/api/schedules/job?job_id=webui_schedule_001")
    routes.WebUIRequestHandler.do_GET(job_req)  # type: ignore[arg-type]
    assert job_req._status == 200
    job_payload = _json_body(job_req)
    assert job_payload["job"]["state"]["last_status"] == "ok"
    assert job_payload["job"]["scheduler_projection_v1"]["projected_status"] == "completed_once"
    assert job_payload["scheduler_projection_contract"]["version"] == "scheduler_projection_contract_v1"
    assert len(job_payload["receipts"]) == 1


def test_runs_matrix_includes_scheduled_jobs_without_run_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda **_kwargs: [
            {
                "job_id": "matrix_visible_job_001",
                "job_kind": "build_verify_loop",
                "display_title": "Matrix visible job",
                "enabled": True,
                "prompt_preview": "Show this launched job in matrix.",
                "schedule": {"mode": "once", "run_at": "2026-06-28T18:00:00Z", "timezone": "UTC"},
                "state": {"active_run_id": "sj_20260628T180000Z_abcd12", "last_status": "running"},
                "due": {"eligible": False, "reason": "active_run", "due_utc": ""},
                "scheduler_projection_v1": {
                    "schema_version": "scheduler_projection_row_v1",
                    "job_id": "matrix_visible_job_001",
                    "projected_status": "running",
                    "projection_reason": "active_run",
                },
                "scheduler_status": "running",
                "scheduler_status_reason": "active_run",
            }
        ],
    )
    cfg = routes.WebUIConfig(runs_root=tmp_path / "empty_runs", static_dir=tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/runs")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _json_body(req)
    rows = payload["runs"]
    assert len(rows) == 1
    row = rows[0]
    assert row["source_kind"] == "scheduled_job"
    assert row["job_id"] == "matrix_visible_job_001"
    assert row["run_key"] == "scheduled:matrix_visible_job_001"
    assert row["status"] == "RUNNING"
    assert row["live_state"] == "RUNNING"


def test_schedules_save_accepts_orchestrator_alias(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    body = json.dumps(
        {
            "job_id": "webui_schedule_orchestrator_001",
            "enabled": True,
            "role": "orchestrator",
            "binding": {"type": "task_id", "id": "mor"},
            "prompt": "Produce a coordination memo.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    ).encode("utf-8")
    save_req = _DummyReq(cfg=cfg, path="/api/schedules/job/save", body=body)
    routes.WebUIRequestHandler.do_POST(save_req)  # type: ignore[arg-type]
    assert save_req._status == 200

    saved = sched.load_job("webui_schedule_orchestrator_001", repo_root=tmp_path)
    assert saved["role"] == "orchestrator"


def test_schedules_job_view_includes_full_prompt_and_hidden_fields_for_editor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_editor_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": "PIS-20260330-120000Z-ABCD"},
            "schedule": {
                "mode": "interval",
                "every_minutes": 45,
                "start_at_utc": "2026-03-30T12:30:00Z",
                "timezone": "UTC",
            },
            "prompt": "Line one for the full prompt.\nLine two must remain intact.",
            "runtime": {
                "agent_tech": "codex",
                "model": "gpt-5.4",
                "executor_agent_tech": "pipeline",
            },
            "loop": {"max_iterations": 4},
            "policy": {"allow_overlap": True, "max_runtime_seconds": 900},
            "expected_output": {"kind": "report", "format": "markdown"},
        },
        repo_root=tmp_path,
    )

    job_req = _DummyReq(cfg=cfg, path="/api/schedules/job?job_id=webui_schedule_editor_001")
    routes.WebUIRequestHandler.do_GET(job_req)  # type: ignore[arg-type]
    assert job_req._status == 200

    payload = _json_body(job_req)
    job = payload["job"]
    assert job["prompt"] == "Line one for the full prompt.\nLine two must remain intact."
    assert job["job_kind"] == "build_verify_loop"
    assert job["loop"]["max_iterations"] == 4
    assert job["runtime"]["executor_agent_tech"] == "pipeline"
    assert job["policy"]["allow_overlap"] is True
    assert job["schedule"]["mode"] == "interval"
    assert job["schedule"]["every_minutes"] == 45
    assert job["schedule"]["start_at_utc"] == "2026-03-30T12:30:00Z"


def test_schedules_job_view_surfaces_budget_status_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_budget_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Show budget state.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
        },
        repo_root=tmp_path,
    )
    sched._job_budget_cost_ledger_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": "webui_schedule_budget_001:alloc:001",
            "job_uid": "webui_schedule_budget_001",
            "recorded_at_utc": "2026-03-08T03:00:00Z",
            "amount_minor_units": 500,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=tmp_path,
    )
    sched._job_budget_cost_ledger_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": "webui_schedule_budget_001:cost:001",
            "job_uid": "webui_schedule_budget_001",
            "recorded_at_utc": "2026-03-08T03:05:00Z",
            "amount_minor_units": 125,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=tmp_path,
    )

    job_req = _DummyReq(cfg=cfg, path="/api/schedules/job?job_id=webui_schedule_budget_001")
    routes.WebUIRequestHandler.do_GET(job_req)  # type: ignore[arg-type]
    assert job_req._status == 200

    payload = _json_body(job_req)
    assert payload["job"]["budget_status"]["present"] is True
    assert payload["job"]["budget_status"]["remaining_minor_units"] == 375
    assert payload["job"]["budget_status"]["status"] == "clear"


def test_schedules_api_surfaces_shared_projection_aliases_and_fail_closed_on_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_parent_failed_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3-parent"},
            "prompt": "Parent failed.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:00:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )
    sched._write_state("webui_schedule_parent_failed_001", {"last_status": "failed"}, repo_root=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_conflict_001",
            "enabled": True,
            "role": "generalist",
            "depends_on": ["webui_schedule_parent_failed_001"],
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Conflicting durable truth.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "webui_schedule_conflict_001",
        {
            "last_status": "ok",
            "last_nonlaunch_reason": "legacy_ok_should_not_win",
        },
        repo_root=tmp_path,
    )

    list_req = _DummyReq(cfg=cfg, path="/api/schedules/jobs")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    assert list_req._status == 200
    listed = _json_body(list_req)
    listed_job = next(row for row in listed["jobs"] if row["job_id"] == "webui_schedule_conflict_001")
    assert listed_job["scheduler_projection_v1"]["projected_status"] == "blocked"
    assert listed_job["scheduler_status"] == "blocked"
    assert listed_job["scheduler_status_reason"].startswith("dependency_")
    assert listed_job["scheduler_truth_degraded"] is False
    assert listed_job["scheduler_status_source"] == "scheduler_projection_v1"

    job_req = _DummyReq(cfg=cfg, path="/api/schedules/job?job_id=webui_schedule_conflict_001")
    routes.WebUIRequestHandler.do_GET(job_req)  # type: ignore[arg-type]
    assert job_req._status == 200
    detail = _json_body(job_req)
    assert detail["job"]["state"]["last_status"] == "ok"
    assert detail["job"]["scheduler_projection_v1"]["projected_status"] == "blocked"
    assert detail["job"]["scheduler_status"] == "blocked"
    assert detail["job"]["scheduler_truth_degraded"] is False


def test_schedules_jobs_payload_falls_back_inprocess_when_snapshot_helper_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_build_schedules_jobs_payload_inprocess",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("in-process listing timed out")),
    )
    monkeypatch.setattr(
        routes,
        "_build_schedules_jobs_payload",
        lambda **_kwargs: {
            "schema_version": "scheduled_jobs_list_v1",
            "utc": "2026-03-08T04:00:00Z",
            "jobs": [
                {
                    "job_id": "webui_schedule_fallback_001",
                    "state": {"last_status": ""},
                    "scheduler_status": "queued",
                }
            ],
            "events": [],
            "scheduler_projection_contract": routes._scheduler_projection_contract_payload(),
        },
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "scheduler_service_status",
        lambda: {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-08T04:00:00Z", "last_error": ""},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_scheduler_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_watchdog_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_healer_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_fallback_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Use the fallback payload.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )

    list_req = _DummyReq(cfg=cfg, path="/api/schedules/jobs")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    assert list_req._status == 200

    payload = _json_body(list_req)
    listed_job = next(row for row in payload["jobs"] if row["job_id"] == "webui_schedule_fallback_001")
    assert listed_job["job_id"] == "webui_schedule_fallback_001"
    assert payload.get("jobs_unavailable") is not True
    assert payload["snapshot_last_error"] == "TimeoutError: in-process listing timed out"
    assert payload["summary"] == "Jobs view served from subprocess fallback after in-process listing failure."


def test_schedules_jobs_payload_includes_scheduler_v2_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "scheduler_service_status",
        lambda: {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-08T04:00:00Z", "last_error": ""},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_scheduler_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_watchdog_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_healer_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **_kwargs: {
            "schema_version": routes._sched_v2_surfaces.SURFACE_BUNDLE_SCHEMA_VERSION,
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260412-060451Z-EB85",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 2,
                            "total_children": 6,
                            "active_children": 1,
                            "blocked_children": 1,
                            "failed_children": 0,
                        },
                    }
                }
            },
            "surfaces": {"api": [], "watch": [], "web": [], "mobile": []},
        },
    )
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_v2_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Use scheduler v2 payload.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        },
        repo_root=tmp_path,
    )

    list_req = _DummyReq(cfg=cfg, path="/api/schedules/jobs")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    assert list_req._status == 200

    payload = _json_body(list_req)
    assert payload["scheduler_v2"]["status"] == "ready"
    assert payload["scheduler_v2"]["ownership"]["mode"] == "v2_primary"
    assert payload["scheduler_v2"]["shadow_payload"]["evaluation"]["projection"]["family_parent_uid"] == "PIS-20260412-060451Z-EB85"


def test_schedules_jobs_payload_marks_scheduler_v2_advisory_when_jobs_view_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes,
        "_build_schedules_jobs_payload_inprocess",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated_inprocess_timeout")),
    )
    monkeypatch.setattr(
        routes,
        "_build_schedules_jobs_payload",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("simulated_subprocess_timeout")),
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "scheduler_service_status",
        lambda: {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-08T04:00:00Z", "last_error": ""},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_scheduler_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_watchdog_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "ensure_healer_service_liveness",
        lambda **_kwargs: {"health": "healthy", "reason": "normal", "restart_attempted": False},
    )
    monkeypatch.setattr(
        routes._sched_v2_surfaces,
        "build_internal_surface_bundle",
        lambda **_kwargs: {
            "schema_version": routes._sched_v2_surfaces.SURFACE_BUNDLE_SCHEMA_VERSION,
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260412-060451Z-EB85",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 2,
                            "total_children": 6,
                            "active_children": 1,
                            "blocked_children": 1,
                            "failed_children": 0,
                        },
                    }
                }
            },
            "surfaces": {"api": [], "watch": [], "web": [], "mobile": []},
        },
    )
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    list_req = _DummyReq(cfg=cfg, path="/api/schedules/jobs")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    assert list_req._status == 200

    payload = _json_body(list_req)
    assert payload["jobs_unavailable"] is True
    assert payload["snapshot_stale"] is True
    assert payload["observation_integrity"]["status"] == "degraded"
    assert payload["observation_integrity"]["advisory_only"] is True
    assert "jobs_unavailable" in payload["observation_integrity"]["reasons"]
    assert "snapshot_stale" in payload["observation_integrity"]["reasons"]
    assert payload["scheduler_v2"]["observation_advisory"]["status"] == "degraded"
    assert payload["scheduler_v2"]["observation_advisory"]["advisory_only"] is True


def test_schedules_toggle_rejects_delayed_stale_action_against_live_truth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_stale_toggle_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Reject stale operator toggle.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    body = json.dumps(
        {
            "job_id": "webui_schedule_stale_toggle_001",
            "enabled": False,
            "observation_evidence": {
                "observation_status": "fresh",
                "view_age_s": 601,
                "scheduler_status": "completed_once",
            },
        }
    ).encode("utf-8")
    req = _DummyReq(cfg=cfg, path="/api/schedules/job/toggle", body=body)
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _json_body(req)
    assert payload["error"] == "stale_action_contradicts_live_truth"
    assert payload["mutation_guard"]["allowed"] is False
    assert payload["mutation_guard"]["supports_unsupervised_operation"] is False
    saved = sched.load_job("webui_schedule_stale_toggle_001", repo_root=tmp_path)
    assert saved["enabled"] is True


def test_schedules_run_now_rejects_degraded_current_observation_without_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_stale_run_now_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Reject stale operator resume.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    monkeypatch.setattr(
        routes,
        "_schedules_jobs_payload",
        lambda repo_root=None, wait_timeout_s=8.0: {
            "jobs": [{"job_id": "webui_schedule_stale_run_now_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable", "snapshot_stale"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        },
    )

    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/run_now",
        body=json.dumps({"job_id": "webui_schedule_stale_run_now_001"}).encode("utf-8"),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _json_body(req)
    assert payload["error"] == "stale_observation_requires_ack"
    assert payload["mutation_guard"]["allowed"] is False


def test_schedules_run_now_rejects_stale_override_for_trigger_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_stale_trigger_override_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Do not allow stale override for trigger.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    monkeypatch.setattr(
        routes,
        "_schedules_jobs_payload",
        lambda repo_root=None, wait_timeout_s=8.0: {
            "jobs": [{"job_id": "webui_schedule_stale_trigger_override_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        },
    )

    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/run_now",
        body=json.dumps(
            {
                "job_id": "webui_schedule_stale_trigger_override_001",
                "allow_stale_observation": True,
                "observation_evidence": {"observation_status": "degraded", "view_age_s": 900},
            }
        ).encode("utf-8"),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _json_body(req)
    assert payload["error"] == "stale_override_forbidden_for_action"
    state = sched.load_state("webui_schedule_stale_trigger_override_001", repo_root=tmp_path)
    assert str(state.get("run_now_requested_utc") or "") == ""


def test_schedules_cancel_allows_explicit_stale_override_when_operator_acknowledges_risk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_stale_cancel_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Allow explicit stale override.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    monkeypatch.setattr(
        routes,
        "_schedules_jobs_payload",
        lambda repo_root=None, wait_timeout_s=8.0: {
            "jobs": [{"job_id": "webui_schedule_stale_cancel_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        },
    )

    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/job/cancel",
        body=json.dumps(
            {
                "job_id": "webui_schedule_stale_cancel_001",
                "allow_stale_observation": True,
                "observation_evidence": {"observation_status": "degraded", "view_age_s": 900},
            }
        ).encode("utf-8"),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _json_body(req)
    assert payload["mutation_guard"]["allowed"] is True
    assert payload["mutation_guard"]["supports_unsupervised_operation"] is False
    assert payload["status"] in {"cancelled", "cancel_requested"}


def test_schedules_cancel_rejects_stale_override_without_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    sched.save_job(
        {
            "job_id": "webui_schedule_stale_cancel_missing_evidence_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Missing evidence must not permit stale override.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    monkeypatch.setattr(
        routes,
        "_schedules_jobs_payload",
        lambda repo_root=None, wait_timeout_s=8.0: {
            "jobs": [{"job_id": "webui_schedule_stale_cancel_missing_evidence_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        },
    )

    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/job/cancel",
        body=json.dumps(
            {
                "job_id": "webui_schedule_stale_cancel_missing_evidence_001",
                "allow_stale_observation": True,
            }
        ).encode("utf-8"),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _json_body(req)
    assert payload["error"] == "stale_override_missing_evidence"
