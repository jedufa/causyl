from __future__ import annotations

from pathlib import Path

from infra.host import verification_contract
from infra.webui import server_agents_orchestrator as orch


def test_orchestration_reuses_matching_host_verification_result_without_rerunning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen = {"assurer": 0}

    request_params = {
        "gradle_task": ":app:testDebugUnitTest",
        "test_selector": "com.eiel.chat.ChatViewModelWorkflowStateTest",
    }
    request_digest = verification_contract.request_digest(
        action_id="android_unit_test",
        params=request_params,
    )

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in str(message or ""):
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"verified"}'}}
        if role == "generalist" and "Intent Validation Agent" in str(message or ""):
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        seen["assurer"] += 1
        if seen["assurer"] == 1:
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "VERDICT: UNKNOWN\nNeed host proof.\n"
                        "HOST_VERIFICATION_REQUEST_JSON:\n"
                        '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.ChatViewModelWorkflowStateTest"},"reason":"run the targeted Android unit test"}'
                    )
                },
            }
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nReplayed host evidence is sufficient."}}

    def _unexpected_host_runner(_request: dict) -> dict:
        raise AssertionError("host verification should have been replayed from existing evidence")

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    monkeypatch.setattr(orch._agents, "_run_host_verification_request", _unexpected_host_runner)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase Replay",
        objective="Reuse prior host verification proof",
        options={"subphases": ["R1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    orch.step_workflow(workflow_id=wf)
    orch.step_workflow(workflow_id=wf)
    state = orch.get_status(workflow_id=wf, events_limit=20)["state"]
    state["latest_host_verification_result"] = {
        "schema_version": verification_contract.SCHEMA_RESULT,
        "request_id": "vrf_cached_001",
        "request_digest": request_digest,
        "workflow_id": wf,
        "binding": {"type": "task_id", "id": "a7k"},
        "ok": True,
        "status": "passed",
        "action_id": "android_unit_test",
        "started_utc": "2026-04-02T09:00:00Z",
        "finished_utc": "2026-04-02T09:00:01Z",
        "duration_ms": 1000,
        "exit_code": 0,
        "cwd": str(tmp_path / "mobile" / "android"),
        "argv": ["./gradlew", ":app:testDebugUnitTest"],
        "evidence": {"stdout_path": "", "stderr_path": "", "artifact_paths": [], "summary": "cached pass"},
        "artifact_lineage": {"schema_version": "eiel_verification_result_artifact_lineage_v1"},
        "failure_kind": "",
        "runner": {"host": "local", "runner_version": "v1"},
    }
    orch._save_state(state)

    result = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert result["ok"] is True
    final_state = result["state"]
    latest = final_state["latest_host_verification_result"]
    assert latest["replayed"] is True
    assert latest["reentry_source"] == "workflow_state.latest"
    assert latest["request_digest"] == request_digest
    assert seen["assurer"] == 2

    events = orch.get_status(workflow_id=wf, events_limit=200)["events"]
    requested = [row for row in events if str(row.get("event") or "") == "host_verification_requested"]
    started_rows = [row for row in events if str(row.get("event") or "") == "host_verification_started"]
    finished = [row for row in events if str(row.get("event") or "") == "host_verification_finished"]
    assert requested[-1]["payload"]["replay_existing_result"] is True
    assert started_rows[-1]["payload"]["replay_existing_result"] is True
    assert finished[-1]["payload"]["replayed"] is True
    assert finished[-1]["payload"]["reentry_source"] == "workflow_state.latest"


def test_verification_contract_result_includes_artifact_lineage_and_binding(tmp_path: Path) -> None:
    request = verification_contract.normalize_request(
        {
            "workflow_id": "wf-lineage",
            "turn": "verifier_iteration_1",
            "requested_by_role": "assurer",
            "binding": {"type": "task_id", "id": "a7k"},
            "action": {
                "action_id": "android_unit_test",
                "params": {
                    "gradle_task": ":app:testDebugUnitTest",
                    "test_selector": "com.eiel.chat.ChatViewModelWorkflowStateTest",
                },
            },
            "reason": "targeted Android proof",
        },
        repo=tmp_path,
    )

    result = verification_contract.build_result(
        request=request,
        ok=True,
        status="passed",
        exit_code=0,
        started_utc="2026-04-02T09:00:00Z",
        finished_utc="2026-04-02T09:00:01Z",
        duration_ms=1000,
        cwd=str(tmp_path / "mobile" / "android"),
        argv=["./gradlew", ":app:testDebugUnitTest"],
        evidence={
            "stdout_path": str(Path(request["artifacts"]["root_dir"]) / "stdout.txt"),
            "stderr_path": str(Path(request["artifacts"]["root_dir"]) / "stderr.txt"),
            "artifact_paths": [str(Path(request["artifacts"]["root_dir"]) / "artifacts" / "TEST-proof.xml")],
            "summary": "1 test passed",
        },
        failure_kind="",
    )

    lineage = result["artifact_lineage"]
    assert result["request_digest"] == request["request_digest"]
    assert result["binding"] == {"type": "task_id", "id": "a7k"}
    assert lineage["schema_version"] == "eiel_verification_result_artifact_lineage_v1"
    assert lineage["job_dir"] == request["artifacts"]["root_dir"]
    assert lineage["request_path"].endswith("/request.json")
    assert lineage["status_path"].endswith("/status.json")
    assert lineage["result_path"].endswith("/result.json")
    assert lineage["stdout_path"].endswith("/stdout.txt")
    assert lineage["stderr_path"].endswith("/stderr.txt")
    assert lineage["artifact_paths"][0].endswith("/artifacts/TEST-proof.xml")
    assert lineage["request_digest"] == request["request_digest"]
