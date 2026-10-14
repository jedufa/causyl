from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_plane_tab_routes_verification_workflow_launch_as_typed_mutation_without_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__plane_mobile.md").write_text("plane mobile prompt", encoding="utf-8")

    prompt = (
        'launch verification workflow\n'
        '{'
        '"request_id":"verification_launch_demo",'
        '"verification_goal":"Stress one bounded workflow.",'
        '"requested_output_mode":"dense_test_pack",'
        '"selected_block_summary":{"qualification_verdict":"accept","block_ref":"demo/block","block_label":"Demo Block",'
        '"operator_goal":"Stress one bounded workflow.","risk_unit_summary":"One bounded seam.",'
        '"qualification_basis":["bounded"],"included_paths":["infra/demo.py"]},'
        '"phase_b_anchor_payload":{"scenario_id":"anchor-1"},'
        '"run_now":true'
        '}'
    )

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_read_request_json", lambda _h, max_bytes=0: {"message": prompt, "agent_key": "p"})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)

    seen: dict[str, object] = {}

    def _launch(payload: dict, *, repo_root: Path | None = None, run_now: bool = False) -> dict:
        seen["payload"] = dict(payload)
        seen["repo_root"] = repo_root
        seen["run_now"] = run_now
        return {
            "durable_request": {
                "request_id": "verification_launch_demo",
                "request_fingerprint": "fp-demo",
            },
            "scheduled_job": {"job_id": "verification_workflow_verification_launch_demo"},
            "run_now": {"ok": True, "queued": True},
        }

    monkeypatch.setattr(
        routes._verification_workflow_launch,
        "create_scheduler_backed_verification_launch",
        _launch,
    )
    monkeypatch.setattr(
        routes._agents,
        "append_session_message",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("typed plane mutations should not append manual session rows")
        ),
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
    assert "Queued verification workflow `verification_launch_demo`." in body
    assert "scheduled_job=verification_workflow_verification_launch_demo" in body
    assert "run_now=queued" in body
    assert seen["run_now"] is True
    assert seen["repo_root"] == tmp_path
    assert seen["payload"] == {
        "request_id": "verification_launch_demo",
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
    assert "Queued verification workflow `verification_launch_demo`." in replay["content"]
