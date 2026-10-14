from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_turns as lab_turns
from infra.webui import server_routes_and_main as routes

from tests.control_plane.verification_workflow_test_support import (
    broad_scheduler_verification_payload,
    drive_verification_campaign_to_completion,
    stage_verification_workflow_repo,
    verification_workflow_launch_payload,
)


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: bytes = b"") -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _json_body(req: _DummyReq) -> dict:
    raw = req.wfile.getvalue().decode("utf-8")
    start = raw.find("{")
    assert start >= 0
    return json.loads(raw[start:])


def test_api_verification_workflow_launch_run_now_executes_via_scheduler_tick(tmp_path: Path, monkeypatch) -> None:
    stage_verification_workflow_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    tick_clock = {"value": 0}
    real_parse = sched._parse_utc

    def _next_utc():
        value = tick_clock["value"]
        tick_clock["value"] += 1
        return real_parse("2026-04-14T10:15:00Z") + timedelta(seconds=value)

    monkeypatch.setattr(sched, "_utc_now", _next_utc)
    monkeypatch.setattr(routes._scheduled_jobs, "_utc_now", _next_utc)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    monkeypatch.setattr(routes._scheduled_jobs, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    body = json.dumps(
        {
            **verification_workflow_launch_payload(request_id="verification_workflow_api_e2e"),
            "run_now": True,
        }
    ).encode("utf-8")
    req = _DummyReq(cfg=cfg, path="/api/verification_workflow/launch", body=body)
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _json_body(req)
    job_id = payload["scheduled_job"]["job_id"]
    fingerprint = payload["durable_request"]["request_fingerprint"]

    tick = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-04-14T10:05:00Z", limit=10)
    assert tick["submitted_count"] == 1
    assert tick["submitted"][0]["job_id"] == job_id

    state = sched.load_state(job_id, repo_root=tmp_path)
    campaign_path = tmp_path / "runtime" / "state" / "verification_workflow" / "campaign_runs" / f"{fingerprint}.json"
    runner_output_path = tmp_path / "experiments" / "projects" / "verification_workflow" / "verification_workflow_run_output.json"
    assert state["last_status"] == "ok"
    assert campaign_path.exists()
    assert runner_output_path.exists()
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert campaign["request_id"] == "verification_workflow_api_e2e"
    assert campaign["anchor_report"]["normalized_defect_candidates"]


def test_api_verification_workflow_launch_broad_scheduler_goal_executes_via_planner_bridge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage_verification_workflow_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    tick_clock = {"value": 0}
    real_parse = sched._parse_utc

    def _next_utc():
        value = tick_clock["value"]
        tick_clock["value"] += 1
        return real_parse("2026-04-14T10:15:00Z") + timedelta(seconds=value)

    monkeypatch.setattr(sched, "_utc_now", _next_utc)
    monkeypatch.setattr(routes._scheduled_jobs, "_utc_now", _next_utc)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    monkeypatch.setattr(routes._scheduled_jobs, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    body = json.dumps({**broad_scheduler_verification_payload(request_id="verification_workflow_api_broad"), "run_now": True}).encode(
        "utf-8"
    )
    req = _DummyReq(cfg=cfg, path="/api/verification_workflow/launch", body=body)
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _json_body(req)
    job_id = payload["scheduled_job"]["job_id"]
    fingerprint = payload["durable_request"]["request_fingerprint"]
    assert payload["planner_packet"]["planner_family"] == "static_verification_planner_v1"

    campaign_path = tmp_path / "runtime" / "state" / "verification_workflow" / "campaign_runs" / f"{fingerprint}.json"
    observed_submission = False
    tick = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-04-14T10:15:00Z", limit=20)
    if tick["submitted_count"] >= 1 and any(row["job_id"] == job_id for row in tick["submitted"]):
        observed_submission = True
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    state = sched.load_state(job_id, repo_root=tmp_path)
    assert observed_submission is True
    assert state["last_status"] == "ok"
    assert campaign["execution_status"] == "running_children"
    assert campaign["current_wave_index"] >= 1
    assert len(campaign["all_child_jobs"]) >= 8
    assert len(campaign["queued_child_jobs"]) >= 8


def test_plane_mobile_verification_workflow_prompt_launches_and_executes_via_scheduler_tick(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage_verification_workflow_repo(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    monkeypatch.setattr(routes._scheduled_jobs, "_runtime_backend_telemetry", lambda: {"backend": "test"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)

    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")
    payload = verification_workflow_launch_payload(request_id="verification_workflow_mobile_e2e")
    prompt = "launch verification workflow\n" + json.dumps({**payload, "run_now": True}, separators=(",", ":"))
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": prompt, "agent_key": "p"})

    monkeypatch.setattr(
        routes._agents,
        "append_session_message",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("typed plane mutations should not append manual session rows")),
    )

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            raise AssertionError("Codex manager should not run for typed plane mutations")

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            raise AssertionError("Codex stream should not run for typed plane mutations")

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert "Queued verification workflow `verification_workflow_mobile_e2e`." in body

    job_id = "verification_workflow_verification_workflow_mobile_e2e"
    tick = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-04-14T10:06:00Z", limit=10)
    assert tick["submitted_count"] == 1
    assert tick["submitted"][0]["job_id"] == job_id

    state = sched.load_state(job_id, repo_root=tmp_path)
    assert state["last_status"] == "ok"
    projection = lab_turns.recent_turn_projections(
        tmp_path,
        limit=4,
        binding={"type": "task_id", "id": "mpl"},
        role="generalist",
    )
    assert projection["total_matching_turns"] == 1
    replay = lab_turns.replay_turn_events(tmp_path, turn_id=str(projection["turns"][0]["turn_id"]), after_event_seq=0)
    assert "Queued verification workflow `verification_workflow_mobile_e2e`." in replay["content"]
