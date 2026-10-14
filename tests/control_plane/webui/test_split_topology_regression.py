from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
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


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def test_split_topology_contract_routes_expose_expected_seam_uids(tmp_path: Path) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    ownership_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/execution_truth_ownership")
    boundary_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/backend_api_boundary")
    topology_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/split_topology_contract")

    routes.WebUIRequestHandler.do_GET(ownership_req)  # type: ignore[arg-type]
    routes.WebUIRequestHandler.do_GET(boundary_req)  # type: ignore[arg-type]
    routes.WebUIRequestHandler.do_GET(topology_req)  # type: ignore[arg-type]

    ownership_payload = _read_payload(ownership_req)
    boundary_payload = _read_payload(boundary_req)
    topology_payload = _read_payload(topology_req)
    assert ownership_req._status == 200
    assert boundary_req._status == 200
    assert topology_req._status == 200
    assert ownership_payload["uid"] == "PIS-20260313-100601Z-M9R2"
    assert boundary_payload["uid"] == "PIS-20260313-100600Z-L8Q1"
    assert "PIS-20260313-100601Z-M9R2" in boundary_payload["depends_on"]
    assert "PIS-20260313-100603Z-P2T4" in boundary_payload["depends_on"]
    assert topology_payload["uid"] == "PIS-20260313-091500Z-ABCD"
    assert set(topology_payload["children"]) == {
        "PIS-20260313-100604Z-Q3U5",
        "PIS-20260313-100605Z-R4V6",
        "PIS-20260313-100606Z-S5W7",
    }


def test_split_topology_snapshot_route_marks_stale_projection_when_recovery_unsettled(    tmp_path: Path,
    monkeypatch,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    req = _DummyReq(cfg=routes.WebUIConfig(runs_root=tmp_path), path="/api/admin/lab_assistant/snapshot")

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    payload = _read_payload(req)
    assert req._status == 200
    assert payload["backend_projection"]["state"] == "stale"
    assert payload["operator_summary"]["status_language"]["state"] == "stale"
    assert payload["backend_projection"]["canonical_execution_owner"] == "execution_backend"
    assert payload["backend_projection"]["projection_owner"] == "web_console_projection"


def test_split_topology_agents_send_duplicate_replay_is_suppressed_across_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    seen = {"calls": 0}

    def _fake_send_message(**kwargs):
        seen["calls"] += 1
        return {"ok": True, "assistant": {"content": "ok"}, "session": [], "binding": dict(kwargs.get("binding") or {})}

    monkeypatch.setattr(routes._agents, "send_message", _fake_send_message)
    request = {
        "request_id": "req-send-topology-001",
        "idempotency_key": "idem-send-topology-001",
        "role": "builder",
        "binding": {"type": "task_id", "id": "mbu"},
        "message": "prove split seam",
    }

    first_req = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(first_req)  # type: ignore[arg-type]
    first_payload = _read_payload(first_req)
    assert first_req._status == 200
    assert first_payload["ok"] is True
    assert first_payload["boundary_result"]["status"] == "accepted"

    second_req = _make_post_req(cfg, "/api/agents/send", request)
    routes.WebUIRequestHandler.do_POST(second_req)  # type: ignore[arg-type]
    second_payload = _read_payload(second_req)
    assert second_req._status == 200
    assert second_payload["ok"] is False
    assert second_payload["boundary_result"]["status"] == "duplicate_suppressed"
    assert second_payload["boundary_result"]["reason_code"] == "duplicate_suppressed"
    assert seen["calls"] == 1


def test_split_topology_schedules_timeout_stays_ambiguous_and_does_not_double_launch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    seen = {"calls": 0}

    def _timeout_request_run_now(*_args, **_kwargs):
        seen["calls"] += 1
        raise TimeoutError("simulated scheduler timeout")

    monkeypatch.setattr(routes._scheduled_jobs, "request_run_now", _timeout_request_run_now)
    request = {
        "request_id": "req-job-timeout-001",
        "idempotency_key": "idem-job-timeout-001",
        "job_id": "overnight_20260313_t6x8",
    }

    timeout_req = _make_post_req(cfg, "/api/schedules/run_now", request)
    routes.WebUIRequestHandler.do_POST(timeout_req)  # type: ignore[arg-type]
    timeout_payload = _read_payload(timeout_req)
    assert timeout_req._status == 504
    assert timeout_payload["ok"] is False
    assert timeout_payload["boundary_result"]["status"] == "ambiguous_timeout"
    assert timeout_payload["boundary_result"]["reason_code"] == "ambiguous_timeout"
    assert timeout_payload["boundary_result"]["retry_owner"] == "caller_retry_same_idempotency_key"

    retry_req = _make_post_req(cfg, "/api/schedules/run_now", request)
    routes.WebUIRequestHandler.do_POST(retry_req)  # type: ignore[arg-type]
    retry_payload = _read_payload(retry_req)
    assert retry_req._status == 200
    assert retry_payload["ok"] is False
    assert retry_payload["boundary_result"]["status"] == "duplicate_suppressed"
    assert seen["calls"] == 1
