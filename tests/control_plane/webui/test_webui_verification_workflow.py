from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_routes_and_main as routes
from infra.controlplane.verification_workflow_launch import VerificationWorkflowLaunchError


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


def test_verification_workflow_launch_route_creates_scheduler_backed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    seen: dict[str, object] = {}

    def _launch(payload: dict, *, repo_root: Path | None = None, run_now: bool = False) -> dict:
        seen["payload"] = dict(payload)
        seen["repo_root"] = repo_root
        seen["run_now"] = run_now
        return {
            "schema_version": "verification_workflow_launch_result_v1",
            "durable_request": {"request_id": "workflow_demo", "request_fingerprint": "fp-123"},
            "review_packet": {"review_state": "ready_for_confirm"},
            "scheduler_packet": {"job_id": "verify_workflow::workflow_demo"},
            "scheduled_job": {"job_id": "verification_workflow_workflow_demo", "job_kind": "project_runner"},
            "run_now": {"ok": True, "queued": True},
        }

    monkeypatch.setattr(
        routes._verification_workflow_launch,
        "create_scheduler_backed_verification_launch",
        _launch,
    )

    body = json.dumps(
        {
            "request_id": "workflow_demo",
            "verification_goal": "Stress one bounded workflow.",
            "requested_output_mode": "dense_test_pack",
            "selected_block_summary": {
                "qualification_verdict": "accept",
                "block_ref": "demo/block",
                "block_label": "Demo Block",
                "operator_goal": "Stress one bounded workflow.",
                "risk_unit_summary": "One bounded seam.",
                "qualification_basis": ["bounded"],
                "included_paths": ["infra/demo.py"],
            },
            "phase_b_anchor_payload": {"scenario_id": "anchor-1"},
            "run_now": True,
        }
    ).encode("utf-8")
    req = _DummyReq(cfg=cfg, path="/api/verification_workflow/launch", body=body)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _json_body(req)
    assert payload["schema_version"] == "verification_workflow_launch_v1"
    assert payload["durable_request"]["request_id"] == "workflow_demo"
    assert payload["scheduled_job"]["job_kind"] == "project_runner"
    assert payload["run_now"]["queued"] is True
    assert seen["repo_root"] == tmp_path
    assert seen["run_now"] is True
    assert seen["payload"] == {
        "request_id": "workflow_demo",
        "verification_goal": "Stress one bounded workflow.",
        "requested_output_mode": "dense_test_pack",
        "selected_block_summary": {
            "qualification_verdict": "accept",
            "block_ref": "demo/block",
            "block_label": "Demo Block",
            "operator_goal": "Stress one bounded workflow.",
            "risk_unit_summary": "One bounded seam.",
            "qualification_basis": ["bounded"],
            "included_paths": ["infra/demo.py"],
        },
        "phase_b_anchor_payload": {"scenario_id": "anchor-1"},
    }


def test_verification_workflow_launch_route_fails_closed_on_invalid_launch_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(
        routes._verification_workflow_launch,
        "create_scheduler_backed_verification_launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            VerificationWorkflowLaunchError("selected_block_rejected", "selected block verdict reject blocks launch")
        ),
    )

    body = json.dumps(
        {
            "request_id": "workflow_rejected",
            "verification_goal": "Stress one bounded workflow.",
            "requested_output_mode": "dense_test_pack",
            "selected_block_summary": {"qualification_verdict": "reject"},
            "phase_b_anchor_payload": {"scenario_id": "anchor-1"},
        }
    ).encode("utf-8")
    req = _DummyReq(cfg=cfg, path="/api/verification_workflow/launch", body=body)

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _json_body(req)
    assert "selected block verdict reject blocks launch" in payload["error"]
