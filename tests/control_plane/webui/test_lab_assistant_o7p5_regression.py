from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_web_operations as ops
from infra.webui import server_routes_and_main as routes


pytestmark = [pytest.mark.regression]


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: bytes = b"{}") -> None:
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


def _json_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _seed_task(
    root: Path,
    *,
    task_id: str,
    status: str = "active",
    title: str = "Operator journey regression",
    next_expected_action: str = "verify cross-surface truth",
) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="o7p5.regression.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": title,
            "status": status,
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260313-060107Z-W9X4",
            "next_expected_action": next_expected_action,
        },
    )["task_register"]


def _request(*, task_id: str, request_id: str, workflow_id: str) -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "o7p5 regression delegation",
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "multi_step": True,
            "mission_alignment": "mission_aligned",
            "mission_alignment_note": "Regression proof for delegated workflow truth is in scope for the plane.",
        }
    )
    request = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260313-060107Z-W9X4"},
        summary="collect durable proof",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "durable evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "review proof", "owner": "assistant"},
        input_payload={"workflow_id": workflow_id},
    )
    request["request_id"] = request_id
    return request


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def test_o7p5_regression_blocked_truth_is_consistent_across_mobile_and_web(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)

    seeded = _seed_task(
        tmp_path,
        task_id="task_o7p5_blocked",
        status="completed",
        title="Looks complete until evidence is checked",
    )
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="o7p5.blocked.bind",
        request=_request(task_id="task_o7p5_blocked", request_id="delegate_o7p5_blocked", workflow_id="wf_o7p5_blocked"),
        launch_result={"workflow_id": "wf_o7p5_blocked"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_o7p5_blocked", "status": "completed"})

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._agents,
        "read_session",
        lambda **_kwargs: {"session": [{"sender": "assistant", "meta": {"chat_header_title": "Admin Toggle Fix"}}]},
    )

    cfg = routes.WebUIConfig(runs_root=tmp_path)
    mobile_req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a")
    routes.WebUIRequestHandler.do_GET(mobile_req)  # type: ignore[arg-type]

    web_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/web_operations")
    routes.WebUIRequestHandler.do_GET(web_req)  # type: ignore[arg-type]

    assert mobile_req._status == 200
    assert web_req._status == 200

    mobile_payload = _json_payload(mobile_req)
    web_payload = _json_payload(web_req)

    assert mobile_payload["upstream_truth"]["operator_state"] == "blocked"
    assert web_payload["dashboard"]["operator_state"] == "blocked"
    assert web_payload["dashboard"]["status_language"]["success_claim_allowed"] is False
    assert web_payload["error_triage"]["state"] == "conflicted"

    tabs = {str(row["agent_key"]): row for row in mobile_payload["tabs"]}
    assert tabs["a"]["topic"]["header_title"] == "Admin Toggle Fix"
    assert tabs["a"]["topic"]["header_title_source"] == "agent_marker"


def test_o7p5_regression_interrupted_reconnect_and_control_confirmation_stay_truthful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_o7p5_interrupted",
        status="active",
        title="Recover interrupted operator task",
        next_expected_action="resume after reconnect",
    )
    recovery.reconcile_startup(tmp_path)

    queue_state = {
        "active": False,
        "status": "idle",
        "status_detail": "idle",
        "current_workflow_id": "",
        "current_workflow_status": "",
        "stop_requested": False,
    }

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes._execution_queue, "status", lambda: {"state": dict(queue_state), "events": []})
    monkeypatch.setattr(ops._execution_queue, "status", lambda: {"state": dict(queue_state), "events": []})
    monkeypatch.setattr(
        ops._execution_queue,
        "start_queue",
        lambda *, options=None: {"ok": True, "state": dict(queue_state), "options": options or {}},
    )
    monkeypatch.setattr(ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": ""})
    monkeypatch.setattr(ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])

    cfg = routes.WebUIConfig(runs_root=tmp_path)

    mobile_shell_req = _DummyReq(cfg=cfg, path="/api/mobile/assistant_shell?agent_key=a")
    routes.WebUIRequestHandler.do_GET(mobile_shell_req)  # type: ignore[arg-type]
    mobile_shell = _json_payload(mobile_shell_req)

    control_req = _DummyReq(
        cfg=cfg,
        path="/api/admin/lab_assistant/web_operations/control",
        body=json.dumps({"action_id": "execution_queue_start", "writer": "o7p5.regression"}).encode("utf-8"),
    )
    routes.WebUIRequestHandler.do_POST(control_req)  # type: ignore[arg-type]
    control_payload = _json_payload(control_req)

    assert mobile_shell_req._status == 200
    assert control_req._status == 200
    assert mobile_shell["upstream_truth"]["operator_state"] == "interrupted_recovering"
    assert mobile_shell["upstream_truth"]["backend_condition"] == "reconnecting"
    assert control_payload["confirmation"]["state"] == "pending"

    queue_state["active"] = True
    queue_state["status"] = "running"
    queue_state["status_detail"] = "workflow_running"

    web_req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/web_operations")
    routes.WebUIRequestHandler.do_GET(web_req)  # type: ignore[arg-type]
    web_payload = _json_payload(web_req)

    mobile_queue_req = _DummyReq(cfg=cfg, path="/api/mobile/backlog/execution_queue")
    routes.WebUIRequestHandler.do_GET(mobile_queue_req)  # type: ignore[arg-type]
    mobile_queue = _json_payload(mobile_queue_req)

    assert web_req._status == 200
    assert mobile_queue_req._status == 200
    assert web_payload["dashboard"]["operator_state"] == "interrupted_recovering"
    assert web_payload["dashboard"]["status_language"]["state"] == "reconnecting"

    latest_control = web_payload["runtime_controls"]["recent_controls"][0]
    assert latest_control["control_request_id"] == control_payload["control_request_id"]
    assert latest_control["confirmation"]["state"] == "confirmed"

    assert mobile_queue["state"]["active"] is True
    assert mobile_queue["state"]["status"] == "running"
    assert mobile_queue["state"]["status_detail"] == "workflow_running"
