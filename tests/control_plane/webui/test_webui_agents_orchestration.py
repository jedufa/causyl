from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from tools import runtime_policy
from infra.host import verification_actions
from infra.host import verification_contract as verification_contract
from infra.webui import server_agents as agents
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui import server_agents_orchestrator as orch


def _state_path(tmp_path: Path, workflow_id: str) -> Path:
    return tmp_path / "governance" / "agents" / "runtime" / "orchestration" / f"workflow__{workflow_id}.json"


def _events_path(tmp_path: Path, workflow_id: str) -> Path:
    return tmp_path / "governance" / "agents" / "runtime" / "orchestration" / f"workflow__{workflow_id}.events.jsonl"


def test_normalize_verifier_verdict_downgrades_environment_limited_fail_to_unknown() -> None:
    verdict = orch._normalize_verifier_verdict(
        raw_verdict="FAIL",
        feedback=(
            "VERDICT: FAIL\n"
            "Environment-limited review. Playwright is not installed in this environment and the remaining execution gap is manual checks only."
        ),
        latest_host_result={"failure_kind": "environment_blocked"},
    )

    assert verdict == "UNKNOWN"


def test_normalize_verifier_verdict_keeps_concrete_failures_as_fail() -> None:
    verdict = orch._normalize_verifier_verdict(
        raw_verdict="FAIL",
        feedback=(
            "VERDICT: FAIL\n"
            "High: the new regression is broken and hangs under local execution."
        ),
        latest_host_result={"failure_kind": "timeout"},
    )

    assert verdict == "FAIL"


def test_orchestration_run_completes_with_reports_and_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    calls: list[tuple[str, str]] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        calls.append((str(role), str(message)))
        if role == "generalist" and "control decision" in message:
            content = '{"decision":"next_subphase","reason":"accepted"}'
        elif role == "generalist" and "Intent Validation Agent" in message:
            content = "intent validation plan\nverdict: pass"
        elif role == "generalist":
            content = "SE brief\n- scope\n- acceptance"
        elif role == "builder":
            content = "builder output\nfiles changed"
        else:
            content = "VERDICT: PASS\nNo findings."
        return {"ok": True, "assistant": {"content": content}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Implement D1 and D2",
        options={"subphases": ["D1", "D2"], "max_iterations": 3},
    )
    wf = str(started["workflow_id"])

    result = orch.run_workflow(workflow_id=wf, max_steps=40)
    assert result["ok"] is True
    state = result["state"]
    assert str(state["status"]) == "completed"
    assert str(state["turn"]) == "done"
    assert len(list(state.get("reports") or [])) >= 2
    assert "intent" in str(state.get("intent_validation_report") or "").lower()

    sp = _state_path(tmp_path, wf)
    ep = _events_path(tmp_path, wf)
    assert sp.exists()
    assert ep.exists()
    assert any(role == "builder" for role, _ in calls)
    assert any(role == "assurer" for role, _ in calls)


def test_orchestration_uses_mobile_prompt_files_for_all_roles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    seen: list[tuple[str, str]] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen.append((str(role), str((options or {}).get("prompt_file") or "")))
        if role == "generalist":
            content = "SE brief"
        elif role == "builder":
            content = "builder output"
        else:
            content = "VERDICT: PASS"
        return {"ok": True, "assistant": {"content": content}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Prompt mapping check",
        options={"subphases": ["D1"], "max_iterations": 1},
    )

    wf = str(started["workflow_id"])
    orch.run_workflow(workflow_id=wf, max_steps=20)

    assert ("generalist", "role__generalist_mobile.md") in seen
    assert ("builder", "role__builder_mobile.md") in seen
    assert ("assurer", "role__verifier_mobile.md") in seen


def test_orchestration_canonicalizes_legacy_prompt_file_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen: list[tuple[str, str]] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen.append((str(role), str((options or {}).get("prompt_file") or "")))
        return {"ok": True, "assistant": {"content": "SE brief"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    state = {
        "workflow_id": "wf-test",
        "binding": {"type": "task_id", "id": "a7k"},
        "turn_timeout_s": 7000,
        "heartbeat_interval_s": 60,
    }
    orch._run_role_turn(
        state=state,
        role="generalist",
        turn="se_tasking",
        message="brief",
        options={"prompt_file": "role__generalist.md"},
    )

    assert seen == [("generalist", "role__generalist_mobile.md")]


def test_orchestration_canonicalizes_legacy_prompt_file_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen: list[tuple[str, str]] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen.append((str(role), str((options or {}).get("prompt_file") or "")))
        return {"ok": True, "assistant": {"content": "SE brief"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    state = {
        "workflow_id": "wf-test",
        "binding": {"type": "task_id", "id": "a7k"},
        "turn_timeout_s": 7000,
        "heartbeat_interval_s": 60,
    }
    orch._run_role_turn(
        state=state,
        role="generalist",
        turn="se_tasking",
        message="brief",
        options={
            "prompt_file": str(
                tmp_path / "governance" / "agents" / "prompts" / "role__generalist.md"
            )
        },
    )

    assert seen == [("generalist", "role__generalist_mobile.md")]


def test_record_builder_commit_journal_writes_pending_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    state = {
        "workflow_id": "wf-test",
        "binding": {"type": "task_id", "id": "mbl"},
        "subphases": ["PIS"],
        "subphase_index": 0,
        "iteration": 1,
    }
    builder_output = """
## Change Summary
- Added a narrow orchestrator regression fix.

## Files Changed
- infra/webui/server_agents_orchestrator.py

## Validation Commands Run
- python -m py_compile infra/webui/server_agents_orchestrator.py
""".strip()

    journal_path = orch._record_builder_commit_journal(state=state, builder_output=builder_output)

    assert journal_path is not None
    entry = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert entry["author"]["role"] == "builder"
    assert entry["author"]["binding"] == {"type": "task_id", "id": "mbl"}
    assert entry["files"] == ["infra/webui/server_agents_orchestrator.py"]
    assert entry["validation"] == ["python -m py_compile infra/webui/server_agents_orchestrator.py"]
    assert entry["summary_bullets"] == ["Added a narrow orchestrator regression fix."]


def test_extract_host_verification_request_preserves_timeout_overrides() -> None:
    request = orch._extract_host_verification_request(
        "VERDICT: UNKNOWN\n"
        "HOST_VERIFICATION_REQUEST_JSON:\n"
        '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest"},"reason":"run proof","timeout_s":3600,"stall_timeout_s":1200}'
    )

    assert request["action_id"] == "android_unit_test"
    assert request["timeout_s"] == 3600
    assert request["stall_timeout_s"] == 1200


def test_build_host_verification_request_uses_action_default_timeout_when_unspecified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    state = {
        "workflow_id": "wf-host-default",
        "iteration": 1,
        "binding": {"type": "task_id", "id": "a7k"},
    }

    normalized = orch._build_host_verification_request(
        state,
        {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.ui.AdminUiStateTest",
            },
            "reason": "run proof",
        },
    )

    assert normalized["policy"]["timeout_s"] == int(
        verification_actions.get_action_spec("android_unit_test").timeout_s_default
    )
    assert normalized["policy"]["stall_timeout_s"] is None


def test_start_workflow_canonicalizes_ledger_objective_and_persists_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(
        title="Checkpointed autopilot influence proof",
        slug="checkpointed-autopilot-influence-proof",
        pis_markdown="pis body",
    )
    uid = str(created["uid"])

    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": uid},
        phase_label="WF-01",
        objective=f"Implement and verify {uid} with WF-01 (Builder -> Verifier, retry up to 3 iterations).",
        options={"max_iterations": 5},
    )

    state = started["state"]
    wf = str(started["workflow_id"])
    assert str(state["objective"]) == f"Implement and verify {uid} with WF-01."
    assert int(state["max_iterations"]) == 5

    events = _events_path(tmp_path, wf).read_text(encoding="utf-8").splitlines()
    started_event = json.loads(events[0])
    assert started_event["payload"]["objective"] == f"Implement and verify {uid} with WF-01."
    assert int(started_event["payload"]["max_iterations"]) == 5


def test_orchestration_failed_state_persists_and_resume_via_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen = {"builder_calls": 0}

    def _fake_send_message_fail_once(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in message:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"ok"}'}}
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "intent done"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            seen["builder_calls"] += 1
            if seen["builder_calls"] == 1:
                raise TimeoutError("builder turn timed out")
            return {"ok": True, "assistant": {"content": "builder fixed output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message_fail_once)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Resume-safe flow",
        options={"subphases": ["D1"], "max_iterations": 2},
    )
    wf = str(started["workflow_id"])

    failed = orch.run_workflow(workflow_id=wf, max_steps=4)
    assert failed["ok"] is False
    st_failed = failed["state"]
    assert str(st_failed["status"]) == "failed"
    assert str(st_failed["turn"]) == "builder_run"
    assert "timed out" in str(st_failed.get("last_error") or "")

    persisted = json.loads(_state_path(tmp_path, wf).read_text(encoding="utf-8"))
    assert str(persisted["status"]) == "failed"

    resumed_step = orch.step_workflow(workflow_id=wf)
    st_step = resumed_step["state"]
    assert str(st_step["status"]) == "idle"
    assert str(st_step["turn"]) == "verifier_run"

    done = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert done["ok"] is True
    assert str(done["state"]["status"]) == "completed"


def test_run_workflow_resume_from_idle_builder_checkpoint_after_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    seen_roles: list[str] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen_roles.append(str(role))
        if role == "generalist" and "control decision" in str(message):
            return {"ok": True, "assistant": {"content": '{"decision":"stop_phase","reason":"enough"}'}}
        if role == "generalist" and "Intent Validation Agent" in str(message):
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Change Summary\n"
                        "- generated job_spec.json, manifest.yaml, report.json via tools.run_job\n"
                        "- immutable git_ref and seed were recorded"
                    )
                },
            }
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nresume ok"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Resume existing persisted workflow",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])

    seeded = orch.step_workflow(workflow_id=wf)
    assert str((seeded.get("state") or {}).get("turn") or "") == "builder_run"

    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "idle"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    seen_roles.clear()
    resumed = orch.run_workflow(workflow_id=wf, max_steps=20)

    assert resumed["ok"] is True
    assert str(resumed["state"]["status"]) == "completed"
    assert seen_roles[0] == "builder"

    events = [json.loads(line) for line in _events_path(tmp_path, wf).read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in events].count("workflow_started") == 1


def test_run_workflow_reconciles_inflight_restart_to_interrupted_recovery_checkpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    def _fail_if_builder_runs(*, role: str, binding, message: str, options=None):
        raise AssertionError(f"unexpected role execution during interruption reconciliation: {role}")

    monkeypatch.setattr(orch._agents, "send_message", _fail_if_builder_runs)

    def _fake_capture_nonclean(state: dict) -> dict:
        state["repo_residue"] = {
            "capture_status": "captured",
            "summary_artifact": "governance/agents/runtime/orchestration/workflow__wf-test.artifacts/repo_residue/summary.json",
        }
        return state

    monkeypatch.setattr(orch, "_capture_nonclean_workflow_repo_residue", _fake_capture_nonclean)

    state = {
        "workflow_id": "wf-test",
        "binding": {"type": "task_id", "id": "a7k"},
        "phase_label": "WF-01",
        "objective": "Recover honestly",
        "status": "running",
        "turn": "builder_run",
        "active_turn_name": "builder_run",
        "active_role": "builder",
        "active_turn_started_utc": "2026-03-13T03:00:00Z",
        "active_slice_timeout_s": 300,
        "stuck_retry_limit": 2,
        "repo_residue": {"capture_status": "pending"},
        "reports": [],
        "workflow_digest": {},
    }
    orch._save_state(state)

    out = orch.run_workflow(workflow_id="wf-test", max_steps=5)

    assert out["ok"] is False
    assert out["noop_reason"] == "interrupted_recovery_required"
    reconciled = out["state"]
    assert str(reconciled["status"]) == "interrupted"
    assert str(reconciled["turn"]) == "se_unblock"
    checkpoint = reconciled.get("checkpoint_boundary") if isinstance(reconciled.get("checkpoint_boundary"), dict) else {}
    assert checkpoint["boundary_state"] == "recovery_checkpoint"
    assert checkpoint["resume_policy"] == "control_recovery_before_role_retry"
    interrupted = reconciled.get("interrupted_context") if isinstance(reconciled.get("interrupted_context"), dict) else {}
    assert interrupted["interrupted_turn"] == "builder_run"
    assert interrupted["residue_summary_artifact"].endswith("summary.json")

    events = orch.get_status(workflow_id="wf-test", events_limit=40)["events"]
    assert any(str(row.get("event") or "") == "workflow_interrupted_reconciled" for row in events)


def test_run_workflow_recovers_from_interrupted_checkpoint_via_se_unblock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "Change Summary\n"
                        "- generated job_spec.json, manifest.yaml, report.json via tools.run_job\n"
                        "- immutable git_ref and seed were recorded"
                    )
                },
            }
        if role == "assurer":
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nrecovered"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    state = {
        "workflow_id": "wf-interrupted",
        "binding": {"type": "task_id", "id": "a7k"},
        "phase_label": "WF-01",
        "objective": "Recover honestly",
        "status": "interrupted",
        "status_detail": "interrupted_requires_recovery",
        "turn": "se_unblock",
        "stuck_retry_limit": 2,
        "stuck_role": "builder",
        "stuck_context": {
            "role": "builder",
            "turn": "builder_run",
            "detail": "interrupted_restart",
            "workflow_repeat_count": 1,
            "retry_limit": 2,
        },
        "interrupted_context": {
            "interrupted_turn": "builder_run",
            "interrupted_role": "builder",
            "resume_policy": "control_recovery_before_role_retry",
        },
        "subphases": ["PIS"],
        "subphase_index": 0,
        "iteration": 1,
        "max_iterations": 1,
        "repo_residue": {"capture_status": "captured"},
        "reports": [],
        "workflow_digest": {},
    }
    orch._save_state(state)

    out = orch.run_workflow(workflow_id="wf-interrupted", max_steps=6)

    assert out["ok"] is True
    assert str(out["state"]["status"]) == "completed"
    assert str(out["state"]["turn"]) == "done"


def test_run_workflow_idempotent_resume_noops_while_already_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    builder_started = threading.Event()
    release_builder = threading.Event()
    seen = {"builder_calls": 0}
    results: dict[str, dict] = {}

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in str(message):
            return {"ok": True, "assistant": {"content": '{"decision":"stop_phase","reason":"enough"}'}}
        if role == "generalist" and "Intent Validation Agent" in str(message):
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            seen["builder_calls"] += 1
            builder_started.set()
            assert release_builder.wait(timeout=5.0)
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nok"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Idempotent resume lock",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    seeded = orch.step_workflow(workflow_id=wf)
    assert str((seeded.get("state") or {}).get("turn") or "") == "builder_run"

    def _run_first() -> None:
        results["first"] = orch.run_workflow(workflow_id=wf, max_steps=1)

    thread = threading.Thread(target=_run_first, daemon=True)
    thread.start()
    assert builder_started.wait(timeout=5.0)

    second = orch.run_workflow(workflow_id=wf, max_steps=1)
    release_builder.set()
    thread.join(timeout=5.0)

    assert seen["builder_calls"] == 1
    assert second["ok"] is True
    assert second["steps_run"] == 0
    assert second["noop_reason"] == "already_running"
    assert str((second.get("state") or {}).get("turn") or "") == "builder_run"
    assert results["first"]["steps_run"] == 1


def test_run_workflow_resume_noops_for_terminal_failed_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Terminal resume noop",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "failed"
    state["turn"] = "builder_run"
    state["last_error"] = "workflow failed previously"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    resumed = orch.run_workflow(workflow_id=wf, max_steps=20)

    assert resumed["ok"] is False
    assert resumed["steps_run"] == 0
    assert resumed["done"] is True
    assert resumed["noop_reason"] == "terminal"
    assert resumed["error"] == "workflow failed previously"


def test_failed_workflow_captures_repo_residue_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    (tmp_path / "tracked.txt").write_text("before\n", encoding="utf-8")
    snapshots: list[dict] = [
        {
            "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
            "captured_utc": "2026-03-10T00:00:00Z",
            "repo_root": str(tmp_path),
            "git_available": True,
            "git_error": "",
            "head": "abc123",
            "branch": "main",
            "status_short": "",
            "tracked_dirty_paths": [],
            "untracked_paths": [],
            "dirty_path_count": 0,
            "entries": [],
        }
    ]

    def _fake_snapshot() -> dict:
        return snapshots.pop(0)

    monkeypatch.setattr(orch, "_collect_repo_dirty_snapshot", _fake_snapshot)
    monkeypatch.setattr(
        orch,
        "_tracked_patch_text",
        lambda paths: (
            "diff --git a/tracked.txt b/tracked.txt\n"
            "--- a/tracked.txt\n"
            "+++ b/tracked.txt\n"
            "@@\n"
            "+after\n"
            if paths == ["tracked.txt"]
            else ""
        ),
    )

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            (tmp_path / "tracked.txt").write_text("before\nafter\n", encoding="utf-8")
            (tmp_path / "note.txt").write_text("scratch\n", encoding="utf-8")
            snapshots.append(
                {
                    "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
                    "captured_utc": "2026-03-10T00:01:00Z",
                    "repo_root": str(tmp_path),
                    "git_available": True,
                    "git_error": "",
                    "head": "abc123",
                    "branch": "main",
                    "status_short": " M tracked.txt\n?? note.txt",
                    "tracked_dirty_paths": ["tracked.txt"],
                    "untracked_paths": ["note.txt"],
                    "dirty_path_count": 2,
                    "entries": [
                        orch._repo_entry_for_path(relpath="tracked.txt", tracked_dirty=True, untracked=False),
                        orch._repo_entry_for_path(relpath="note.txt", tracked_dirty=False, untracked=True),
                    ],
                }
            )
            raise TimeoutError("builder turn timed out")
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nblocked"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Capture repo residue on failure",
        options={"subphases": ["D1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=4)
    state = result["state"]

    assert result["ok"] is False
    assert str(state.get("status") or "") == "failed"
    assert "builder turn timed out" in str(state.get("last_error") or "")
    residue = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    assert str(residue.get("capture_status") or "") == "captured"
    assert int(residue.get("baseline_dirty_path_count") or 0) == 0
    assert int(residue.get("changed_since_start_count") or 0) == 2

    summary_path = tmp_path / str(residue.get("summary_artifact") or "")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["clean_at_start_changed_paths"] == ["note.txt", "tracked.txt"]
    assert summary["new_untracked_paths"] == ["note.txt"]
    assert summary["preexisting_dirty_changed_paths"] == []

    patch_path = tmp_path / str(residue.get("patch_artifact") or "")
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "tracked.txt" in patch_text
    assert "+after" in patch_text
    assert "note.txt" not in patch_text

    failure_snapshot = json.loads((tmp_path / str(residue.get("current_artifact") or "")).read_text(encoding="utf-8"))
    failure_entries = {
        str(row.get("path") or ""): row
        for row in failure_snapshot.get("entries") or []
        if isinstance(row, dict)
    }
    assert str(failure_entries["note.txt"].get("copy_status") or "") == "saved"
    assert str(failure_entries["tracked.txt"].get("copy_status") or "") == "saved"

    events = orch.get_status(workflow_id=wf, events_limit=120)["events"]
    assert any(str(ev.get("event") or "") == "workflow_repo_residue_captured" for ev in events)


def test_failed_workflow_marks_preexisting_dirty_paths_as_ambiguous_residue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    (tmp_path / "tracked.txt").write_text("base\nuser-dirty\n", encoding="utf-8")
    snapshots: list[dict] = [
        {
            "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
            "captured_utc": "2026-03-10T00:00:00Z",
            "repo_root": str(tmp_path),
            "git_available": True,
            "git_error": "",
            "head": "abc123",
            "branch": "main",
            "status_short": " M tracked.txt",
            "tracked_dirty_paths": ["tracked.txt"],
            "untracked_paths": [],
            "dirty_path_count": 1,
            "entries": [
                orch._repo_entry_for_path(relpath="tracked.txt", tracked_dirty=True, untracked=False),
            ],
        }
    ]

    def _fake_snapshot() -> dict:
        return snapshots.pop(0)

    monkeypatch.setattr(orch, "_collect_repo_dirty_snapshot", _fake_snapshot)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            (tmp_path / "tracked.txt").write_text("base\nuser-dirty\nbuilder-touch\n", encoding="utf-8")
            snapshots.append(
                {
                    "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
                    "captured_utc": "2026-03-10T00:01:00Z",
                    "repo_root": str(tmp_path),
                    "git_available": True,
                    "git_error": "",
                    "head": "abc123",
                    "branch": "main",
                    "status_short": " M tracked.txt",
                    "tracked_dirty_paths": ["tracked.txt"],
                    "untracked_paths": [],
                    "dirty_path_count": 1,
                    "entries": [
                        orch._repo_entry_for_path(relpath="tracked.txt", tracked_dirty=True, untracked=False),
                    ],
                }
            )
            raise TimeoutError("builder turn timed out")
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nblocked"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Separate preexisting dirty residue",
        options={"subphases": ["D1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=4)
    residue = (result.get("state") or {}).get("repo_residue") if isinstance((result.get("state") or {}).get("repo_residue"), dict) else {}

    assert result["ok"] is False
    summary = json.loads((tmp_path / str(residue.get("summary_artifact") or "")).read_text(encoding="utf-8"))
    assert summary["clean_at_start_changed_paths"] == []
    assert summary["preexisting_dirty_changed_paths"] == ["tracked.txt"]
    assert str(residue.get("patch_artifact") or "") == ""

    baseline_snapshot = json.loads((tmp_path / str(residue.get("baseline_artifact") or "")).read_text(encoding="utf-8"))
    baseline_entries = {
        str(row.get("path") or ""): row
        for row in baseline_snapshot.get("entries") or []
        if isinstance(row, dict)
    }
    assert str(baseline_entries["tracked.txt"].get("copy_status") or "") == "saved"


def test_failed_workflow_with_evidence_gap_captures_repo_residue_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    (tmp_path / "tracked.txt").write_text("before\n", encoding="utf-8")
    snapshots: list[dict] = [
        {
            "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
            "captured_utc": "2026-03-10T00:00:00Z",
            "repo_root": str(tmp_path),
            "git_available": True,
            "git_error": "",
            "head": "abc123",
            "branch": "main",
            "status_short": "",
            "tracked_dirty_paths": [],
            "untracked_paths": [],
            "dirty_path_count": 0,
            "entries": [],
        }
    ]

    def _fake_snapshot() -> dict:
        return snapshots.pop(0)

    monkeypatch.setattr(orch, "_collect_repo_dirty_snapshot", _fake_snapshot)
    monkeypatch.setattr(
        orch,
        "_tracked_patch_text",
        lambda paths: (
            "diff --git a/tracked.txt b/tracked.txt\n"
            "--- a/tracked.txt\n"
            "+++ b/tracked.txt\n"
            "@@\n"
            "+after\n"
            if paths == ["tracked.txt"]
            else ""
        ),
    )

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nCompile blocked."}}
        if role == "generalist" and "control decision" in message:
            return {"ok": True, "assistant": {"content": '{"decision":"stop_phase","reason":"compile blocker"}'}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            (tmp_path / "tracked.txt").write_text("before\nafter\n", encoding="utf-8")
            snapshots.append(
                {
                    "schema_version": orch.SCHEMA_REPO_SNAPSHOT,
                    "captured_utc": "2026-03-10T00:01:00Z",
                    "repo_root": str(tmp_path),
                    "git_available": True,
                    "git_error": "",
                    "head": "abc123",
                    "branch": "main",
                    "status_short": " M tracked.txt",
                    "tracked_dirty_paths": ["tracked.txt"],
                    "untracked_paths": [],
                    "dirty_path_count": 1,
                    "entries": [
                        orch._repo_entry_for_path(relpath="tracked.txt", tracked_dirty=True, untracked=False),
                    ],
                }
            )
            return {"ok": True, "assistant": {"content": "builder output without run evidence"}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nStill broken."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Capture residue on evidence-gapped workflow",
        options={"subphases": ["D1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)

    assert result["ok"] is False
    state = result["state"]
    assert str(state.get("status") or "") == "failed"
    assert str(state.get("status_detail") or "") == "intent_validation_failed"
    assert str(state.get("verifier_verdict") or "") == "FAIL"
    assert orch._workflow_completed_cleanly(state) is False

    residue = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    assert str(residue.get("capture_status") or "") == "captured"
    assert int(residue.get("changed_since_start_count") or 0) == 1

    summary_path = tmp_path / str(residue.get("summary_artifact") or "")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["clean_at_start_changed_paths"] == ["tracked.txt"]

    patch_path = tmp_path / str(residue.get("patch_artifact") or "")
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "tracked.txt" in patch_text
    assert "+after" in patch_text

    events = orch.get_status(workflow_id=wf, events_limit=120)["events"]
    assert any(str(ev.get("event") or "") == "intent_validation_done" for ev in events)
    assert any(str(ev.get("event") or "") == "workflow_repo_residue_captured" for ev in events)


def test_orchestration_list_and_cancel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nneeds changes"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Cancelable flow",
        options={},
    )
    wf = str(started["workflow_id"])

    orch.step_workflow(workflow_id=wf)
    listed = orch.list_workflows(binding={"type": "task_id", "id": "a7k"}, limit=20)
    assert listed["ok"] is True
    assert any(str(x.get("workflow_id") or "") == wf for x in listed["items"])

    cancelled = orch.cancel_workflow(workflow_id=wf, reason="manual stop")
    assert cancelled["ok"] is True
    assert str(cancelled["state"]["status"]) == "cancelled"

    status = orch.get_status(workflow_id=wf, events_limit=50)
    assert status["ok"] is True
    assert any(str(ev.get("event") or "") == "workflow_cancelled" for ev in status["events"])


def test_cancel_workflow_requests_stop_after_current_for_running_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    state = {
        "workflow_id": "wf-stop-after-current",
        "binding": {"type": "task_id", "id": "a7k"},
        "phase_label": "WF-01",
        "objective": "Stop after checkpoint",
        "status": "running",
        "turn": "builder_run",
        "active_turn_name": "builder_run",
        "active_role": "builder",
        "subphases": ["PIS"],
        "subphase_index": 0,
        "iteration": 1,
        "repo_residue": {"capture_status": "pending"},
        "reports": [],
        "workflow_digest": {},
    }
    orch._save_state(state)

    requested = orch.cancel_workflow(workflow_id="wf-stop-after-current", reason="manual stop")

    assert requested["ok"] is True
    requested_state = requested["state"]
    assert str(requested_state["status"]) == "running"
    assert bool(requested_state["stop_after_current_requested"]) is True
    checkpoint = requested_state.get("checkpoint_boundary") if isinstance(requested_state.get("checkpoint_boundary"), dict) else {}
    assert checkpoint["resume_policy"] == "requires_recovery_review"

    idle_state = dict(requested_state)
    idle_state["status"] = "idle"
    orch._save_state(idle_state)

    stopped = orch.step_workflow(workflow_id="wf-stop-after-current")

    assert stopped["ok"] is True
    assert bool(stopped.get("done")) is True
    assert str(stopped["state"]["status"]) == "cancelled"
    assert str(stopped["state"]["status_detail"]) == "stopped_after_current"

    events = orch.get_status(workflow_id="wf-stop-after-current", events_limit=40)["events"]
    event_names = [str(row.get("event") or "") for row in events]
    assert "workflow_stop_after_current_requested" in event_names
    assert "workflow_stopped_after_current" in event_names


def test_intent_validation_report_is_structured_and_tracks_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in message:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"accepted"}'}}
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nAll checks green."}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief for D1"}}
        if role == "builder":
            content = (
                "Change Summary\n"
                "- generated job_spec.json, manifest.yaml, report.json via tools.run_job\n"
                "- immutable git_ref and seed were recorded"
            )
            return {"ok": True, "assistant": {"content": content}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nNo findings."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Implement D1 loop with traceable artifacts",
        options={"subphases": ["D1"], "max_iterations": 2},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert result["ok"] is True
    report = str((result.get("state") or {}).get("intent_validation_report") or "")
    assert "1) Intended Change" in report
    assert "2) What Was Run" in report
    assert "3) Pass/Fail Results" in report
    assert "4) Intent Validation Verdict" in report
    assert "5) Residual Risks/Gaps" in report
    assert "Intent validated: YES" in report
    assert "job_spec: yes" in report
    assert "manifest: yes" in report
    assert "report: yes" in report
    assert "immutable_git_ref: yes" in report
    assert "seed: yes" in report


def test_intent_validation_report_marks_missing_evidence_as_risk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in message:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"accepted"}'}}
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "validation narrative only"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output without run evidence"}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nmissing traceability evidence"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="D1 risk surfacing",
        options={"subphases": ["D1"], "max_iterations": 2},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert result["ok"] is True
    report = str((result.get("state") or {}).get("intent_validation_report") or "")
    assert "Intent validated: NO" in report
    assert "Missing evidence markers:" in report


def test_benchmark_intent_validation_uses_repo_residue_instead_of_builder_markers() -> None:
    state = {
        "phase_label": "Benchmark",
        "verifier_verdict": "PASS",
        "repo_residue": {"changed_since_start_count": 1},
        "builder_output": "builder output without run evidence",
    }

    summary = orch._intent_validation_summary(state=state, raw_report="VERDICT: PASS\nlooks good")
    report = orch._format_intent_validation_report(state=state, raw_report="VERDICT: PASS\nlooks good")

    assert summary["intent_validated"] == "YES"
    assert summary["evidence"] == {"repo_change": True}
    assert "Benchmark evidence:" in report
    assert "repo_change: yes" in report
    assert "Benchmark repo change was captured." in report


def test_benchmark_intent_validation_marks_missing_repo_change_as_risk() -> None:
    state = {
        "phase_label": "benchmark",
        "verifier_verdict": "PASS",
        "repo_residue": {"changed_since_start_count": 0},
        "builder_output": "job_spec manifest report wrapper seed git_ref",
    }

    summary = orch._intent_validation_summary(state=state, raw_report="")
    terminal = orch._intent_validation_terminal_state(state=state, raw_report="")
    report = orch._format_intent_validation_report(state=state, raw_report="")

    assert summary["intent_validated"] == "YES"
    assert summary["missing_evidence"] == ["repo_change"]
    assert terminal["status_detail"] == "completed"
    assert "Missing benchmark evidence: repo_change" in report


def test_benchmark_intent_validation_accepts_builder_files_plus_validation_as_delivery_proof() -> None:
    state = {
        "phase_label": "Benchmark",
        "verifier_verdict": "PASS",
        "repo_residue": {"changed_since_start_count": 0},
        "builder_output": (
            "**Change Summary**\nDid the task.\n\n"
            "**Files Changed**\n- BENCHMARK_SMOKE_NOTE.txt\n\n"
            "**Validation Commands Run**\n- python -c \"print('ok')\"\n"
        ),
    }

    summary = orch._intent_validation_summary(state=state, raw_report="VERDICT: PASS\nlooks good")
    terminal = orch._intent_validation_terminal_state(state=state, raw_report="VERDICT: PASS\nlooks good")
    report = orch._format_intent_validation_report(state=state, raw_report="VERDICT: PASS\nlooks good")

    assert summary["intent_validated"] == "YES"
    assert summary["evidence"] == {
        "repo_change": False,
        "builder_files": True,
        "builder_validation": True,
    }
    assert terminal["status"] == "completed"
    assert "builder_files: yes" in report
    assert "builder_validation: yes" in report


def test_intent_validation_prompt_requires_explicit_verdict_line() -> None:
    prompt = orch._compose_intent_prompt(
        {
            "objective": "Verify a benchmark micro-task",
            "reports": ["builder delivered the file"],
        }
    )

    assert "Return with first line exactly: VERDICT: PASS or VERDICT: FAIL or VERDICT: UNKNOWN" in prompt


def test_orchestration_defaults_to_single_pis_stage_and_directive_optional(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    seen_messages: list[str] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen_messages.append(str(message))
        if role == "generalist" and "control decision" in message:
            return {"ok": True, "assistant": {"content": '{"decision":"stop_phase","reason":"enough"}'}}
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "VERDICT: PASS"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="",
        objective="Do PIS thing",
        options={"max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    st = started["state"]
    assert st["subphases"] == ["PIS"]
    assert st.get("execution_directive") == ""

    orch.step_workflow(workflow_id=wf)
    orch.step_workflow(workflow_id=wf)
    assert any("PIS stage: PIS" in m for m in seen_messages)


def test_se_tasking_is_deterministic_by_default_and_skips_generalist_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fail_if_called(*, role: str, binding, message: str, options=None):
        raise AssertionError(f"unexpected agent call during deterministic se_tasking: {role}")

    monkeypatch.setattr(orch._agents, "send_message", _fail_if_called)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Tighten workflow trust",
        options={"subphases": ["PIS"], "max_iterations": 2},
    )
    wf = str(started["workflow_id"])

    out = orch.step_workflow(workflow_id=wf)

    assert out["ok"] is True
    state = out["state"]
    assert state["turn"] == "builder_run"
    assert "1) Scope" in str(state.get("se_brief") or "")
    events = orch.get_status(workflow_id=wf, events_limit=20)["events"]
    done = next(row for row in events if str(row.get("event") or "") == "se_tasking_done")
    assert str((done.get("payload") or {}).get("brief_source") or "") == "deterministic"


def test_se_task_prompt_includes_authoritative_max_iterations() -> None:
    prompt = orch._compose_se_task_prompt(
        {
            "binding": {"type": "task_id", "id": "a7k"},
            "phase_label": "Orchestrator Single-Panel Web UI",
            "subphases": ["PIS"],
            "subphase_index": 0,
            "objective": "Fix workflow iteration mismatch",
            "max_iterations": 4,
        }
    )

    assert "Max Iterations: 4" in prompt
    assert "Do not invent or restate a different retry budget." in prompt


def test_conversation_items_show_authoritative_max_iterations() -> None:
    items = orch._conversation_items(
        {
            "binding": {"type": "ledger_uid", "id": "PIS-20260307-211556Z-2A48"},
            "created_utc": "2026-03-08T02:00:00Z",
            "objective": "Fix workflow iteration mismatch",
            "execution_directive": "maximum correction cycles before escalation is 4",
            "max_iterations": 4,
        },
        [],
    )

    user_item = next(item for item in items if str(item.get("title") or "") == "User -> Orchestrator")
    text = str(user_item.get("text") or "")
    assert "Workflow config: max_iterations=4." in text
    assert "Fix workflow iteration mismatch" in text


def test_get_conversation_prefers_session_dialogue_for_roles_with_real_messages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Conversation sourcing regression",
        options={"max_iterations": 1},
    )
    wf = str(started["workflow_id"])

    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["created_utc"] = "2026-03-08T10:00:00Z"
    state["updated_utc"] = "2026-03-08T10:05:00Z"
    state["last_step_utc"] = "2026-03-08T10:05:00Z"
    state["se_brief"] = "Synthetic SE brief"
    state["builder_output"] = "Synthetic builder output"
    state["verifier_feedback"] = "Synthetic verifier feedback"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    paths = agents._resolve_paths({"type": "task_id", "id": "a7k"})
    chat_dir = Path(paths["chat_dir"])
    (chat_dir / "session__builder.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "agent_chat_message_v1",
                "sender": "assistant",
                "utc": "2026-03-08T10:03:00Z",
                "content": "Actual builder discussion",
            }
        ) + "\n",
        encoding="utf-8",
    )
    (chat_dir / "session__generalist.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "agent_chat_message_v1",
                "sender": "assistant",
                "utc": "2026-03-08T10:02:00Z",
                "content": "Actual orchestrator discussion",
            }
        ) + "\n",
        encoding="utf-8",
    )

    out = orch.get_conversation(workflow_id=wf)
    items = out["items"]
    texts = [str(row.get("text") or "") for row in items]

    assert "Actual orchestrator discussion" in texts
    assert "Actual builder discussion" in texts
    assert "Synthetic builder output" not in texts
    assert "Synthetic SE brief" not in texts
    assert "Synthetic verifier feedback" in texts


def test_start_workflow_normalizes_legacy_retry_budget_from_objective(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": "PIS-20260308-022623Z-E964"},
        phase_label="WF-01",
        objective="Implement and verify PIS-20260308-022623Z-E964 with WF-01 (Builder -> Verifier, retry up to 3 iterations).",
        options={"max_iterations": 4},
    )

    state = started["state"]
    assert int(state["max_iterations"]) == 4
    assert str(state["objective"]) == "Implement and verify PIS-20260308-022623Z-E964 with WF-01."
    assert "retry up to" not in str(state["objective"])


def test_start_workflow_ledger_binding_uses_canonical_objective_for_fresh_launcher_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": "PIS-20260308-022623Z-E964"},
        phase_label="WF-01",
        objective="Checkpointed autopilot influence proof phase 1 resume targeted verification",
        options={"max_iterations": 4, "execution_directive": "execute the attached PIS"},
    )

    state = started["state"]
    assert int(state["max_iterations"]) == 4
    assert str(state["objective"]) == "Implement and verify PIS-20260308-022623Z-E964 with WF-01."
    assert str(state["execution_directive"]) == "execute the attached PIS"


def test_infra_blocked_verifier_retry_does_not_consume_iteration_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen = {"assurer_calls": 0, "decide_calls": 0}

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "control decision" in message:
            seen["decide_calls"] += 1
            if seen["decide_calls"] == 1:
                return {"ok": True, "assistant": {"content": '{"decision":"extra_iteration","reason":"infra retry"}'}}
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"verified"}'}}
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\\nDone."}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        seen["assurer_calls"] += 1
        if seen["assurer_calls"] == 1:
            return {"ok": False, "assistant": {"content": "[agent_error] worker limit reached"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\\nNo findings."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="",
        objective="Do PIS thing",
        options={"max_iterations": 1, "max_infra_retries": 2},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert result["ok"] is True
    st = result["state"]
    assert str(st.get("status")) == "completed"
    assert int(st.get("iteration") or 0) == 1
    assert seen["assurer_calls"] >= 2


def test_orchestration_heartbeat_and_timeout_config_applied(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen_options: list[dict] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen_options.append(dict(options or {}))
        if role == "generalist":
            time.sleep(1.2)
            return {"ok": True, "assistant": {"content": "SE brief"}}
        return {"ok": True, "assistant": {"content": "ok"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="heartbeat test",
        options={"subphases": ["D1"], "max_iterations": 1, "heartbeat_interval_s": 1, "timeout_s": 77},
    )
    wf = str(started["workflow_id"])
    orch.step_workflow(workflow_id=wf)
    status = orch.get_status(workflow_id=wf, events_limit=200)

    assert seen_options
    assert int(seen_options[0].get("timeout_s") or 0) == 77
    assert int(seen_options[0].get("heartbeat_interval_s") or 0) == 1
    assert any(str(ev.get("event") or "") == "role_turn_heartbeat" for ev in status["events"])


def test_run_workflow_argument_overrides_persist_to_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        return {"ok": True, "assistant": {"content": "SE brief"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="runtime override test",
        options={"subphases": ["D1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    orch.run_workflow(workflow_id=wf, max_steps=1, heartbeat_interval_s=33, timeout_s=44)
    state = json.loads(_state_path(tmp_path, wf).read_text(encoding="utf-8"))
    assert int(state.get("heartbeat_interval_s") or 0) == 33
    assert int(state.get("turn_timeout_s") or 0) == 44


def test_max_iterations_zero_clamps_to_one_and_runs_default_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    seen_roles: list[str] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        seen_roles.append(str(role))
        if role == "generalist" and "control decision" in str(message):
            return {"ok": True, "assistant": {"content": '{"decision":"stop_phase","reason":"done"}'}}
        if role == "generalist" and "Intent Validation Agent" in str(message):
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nDone."}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nNo findings."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="",
        objective="iteration clamp regression test",
        options={"max_iterations": 0},
    )
    wf = str(started["workflow_id"])
    st0 = started["state"]
    assert int(st0.get("max_iterations")) == 1
    assert int(st0.get("iteration")) == 1
    assert str(st0.get("turn") or "") == "se_tasking"

    ran = orch.run_workflow(workflow_id=wf, max_steps=20)
    assert ran["ok"] is True
    assert ran["done"] is True
    assert "builder" in seen_roles
    assert "assurer" in seen_roles


def test_start_workflow_defaults_turn_timeout_to_runtime_policy_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="default timeout regression test",
        options={"subphases": ["D1"], "max_iterations": 1},
    )

    state = started["state"]
    assert int(state.get("turn_timeout_s") or 0) == 1800


def test_start_workflow_uses_current_runtime_policy_without_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(
        runtime_policy,
        "load_runtime_policy",
        lambda: {
            "agents.orchestrator_turn_timeout_s": "1234",
            "agents.workflow.builder_slice_timeout_s": "222",
            "agents.workflow.control_mode": "agentic",
        },
    )

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="live policy regression test",
        options={"subphases": ["D1"], "max_iterations": 1},
    )

    state = started["state"]
    assert int(state.get("turn_timeout_s") or 0) == 1234
    assert int((state.get("role_slice_timeouts") or {}).get("builder") or 0) == 222
    assert str(state.get("control_mode") or "") == "agentic"


def test_builder_prompt_phase_e_progressive_master_contract() -> None:
    state = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260304-104147Z-75C1"},
        "phase_label": "Orchestrator Single-Panel Web UI",
        "objective": "Phase E execution",
        "subphases": ["E1", "E2", "E3"],
        "subphase_index": 0,
        "iteration": 1,
        "se_brief": "Implement E1.",
        "execution_directive": "Execute progressively.",
        "verifier_feedback": "Address previous failures.",
    }
    msg = orch._compose_builder_prompt(state)
    assert "Phase E Master Prompt (Progressive Sub-Phases)" in msg
    assert "PIS UID: PIS-20260304-104147Z-75C1" in msg
    assert "PIS stage: E1" in msg
    assert "Sub-phase Order (fixed): E1, E2, E3" in msg
    assert "Phase-E hard gates" in msg
    assert "E1 required metrics:" in msg
    assert "Structural Innovation Rate (SIR)" in msg
    assert "Determinism Sentinel" in msg
    assert "entry gate" in msg.lower()
    assert "stop/continue decision gate" in msg.lower()
    assert "next_subphase" in msg
    assert "extra_iteration" in msg
    assert "stop_phase" in msg
    assert "emergence/obs_logging.py" in msg
    assert "emergence/decision_debug.py" in msg
    assert "docs/specs/schemas/run_meta_v1.json" in msg
    assert "python -m py_compile <modified files>" in msg
    assert "python -m tools.diagnostics.static_rng_scan --repo_root . --json" in msg
    assert "pytest -q tests/evolvability/phase_e" in msg
    assert "tools/diagnostics/verify_p0a_determinism_gate.sh --with-tests" in msg
    assert "Return output with sections:" in msg
    assert "- Change Summary" in msg
    assert "- Files Changed" in msg
    assert "- Validation Commands Run" in msg
    assert "- Residual Risks" in msg


def test_builder_prompt_phase_e_e2_includes_failure_taxonomy_requirements() -> None:
    state = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260305-185022Z-1294"},
        "phase_label": "Orchestrator Single-Panel Web UI",
        "objective": "Phase E execution",
        "subphases": ["E1", "E2"],
        "subphase_index": 1,
        "iteration": 1,
        "se_brief": "Implement E2.",
    }
    msg = orch._compose_builder_prompt(state)
    assert "PIS stage: E2" in msg
    assert "E2 required failure categories:" in msg
    assert "drift_collapse" in msg
    assert "maintenance_domination" in msg
    assert "structural_stagnation" in msg
    assert "bloat_ceiling_hit" in msg
    assert "mutation_explosion" in msg
    assert "identity_mimicry" in msg
    assert "rng_desync" in msg
    assert "hidden_scalar_reward_channel" in msg
    assert "E2 acceptance gate" in msg


def test_builder_prompt_non_phase_e_keeps_default_template() -> None:
    state = {
        "phase_label": "Orchestrator Single-Panel Web UI",
        "subphases": ["D1", "D2"],
        "subphase_index": 0,
        "iteration": 2,
        "se_brief": "Do D1 work",
    }
    msg = orch._compose_builder_prompt(state)
    assert "Phase E Master Prompt (Progressive Sub-Phases)" not in msg
    assert "Context Label: Orchestrator Single-Panel Web UI" in msg
    assert "PIS stage: D1" in msg
    assert "Iteration: 2" in msg


def test_phase_e_runtime_gate_blocks_forced_next_subphase_when_e1_not_passed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        msg = str(message or "")
        if role == "generalist" and "control decision" in msg:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"force advance"}'}}
        if role == "generalist" and "Intent Validation Agent" in msg:
            return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nblocked"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nE1 not accepted"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": "PIS-20260305-185022Z-1294"},
        phase_label="Orchestrator Single-Panel Web UI",
        objective="Phase E execution",
        options={"subphases": ["E1", "E2"], "max_iterations": 2},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=4)
    st = result["state"]

    assert int(st.get("subphase_index", -1)) == 0
    assert str(st.get("subphases", [])[0]) == "E1"
    assert int(st.get("iteration") or 0) == 2
    assert str(st.get("turn") or "") == "builder_run"
    reports = [str(x) for x in (st.get("reports") or [])]
    assert any("decision=extra_iteration" in row for row in reports)


def test_phase_e_runtime_progresses_to_e2_only_after_e1_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    builder_prompts: list[str] = []

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        msg = str(message or "")
        if role == "generalist" and "control decision" in msg:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"accepted"}'}}
        if role == "generalist" and "Intent Validation Agent" in msg:
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            builder_prompts.append(msg)
            return {"ok": True, "assistant": {"content": "builder output"}}
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\naccepted"}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": "PIS-20260305-185022Z-1294"},
        phase_label="Orchestrator Single-Panel Web UI",
        objective="Phase E execution",
        options={"subphases": ["E1", "E2"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=6)
    st = result["state"]

    assert int(st.get("subphase_index", -1)) == 1
    assert str(st.get("subphases", [])[1]) == "E2"
    assert len(builder_prompts) >= 2
    assert "PIS stage: E1" in builder_prompts[0]
    assert "PIS stage: E2" in builder_prompts[1]


def test_orchestration_runs_host_verification_and_replays_verifier(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    assurer_prompts: list[str] = []
    seen = {"assurer_calls": 0}

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        msg = str(message or "")
        if role == "generalist" and "control decision" in msg:
            return {"ok": True, "assistant": {"content": '{"decision":"next_subphase","reason":"verified"}'}}
        if role == "generalist" and "Intent Validation Agent" in msg:
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nintent"}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output"}}
        assurer_prompts.append(msg)
        seen["assurer_calls"] += 1
        if seen["assurer_calls"] == 1:
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
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nHost evidence is sufficient."}}

    def _fake_host_request(request: dict) -> dict:
        normalized = verification_contract.normalize_request(request, repo=tmp_path)
        return {
            "schema_version": verification_contract.SCHEMA_RESULT,
            "request_id": str(normalized["request_id"]),
            "workflow_id": str(normalized["workflow_id"]),
            "ok": True,
            "status": "passed",
            "action_id": "android_unit_test",
            "started_utc": "2026-03-09T00:00:00Z",
            "finished_utc": "2026-03-09T00:00:01Z",
            "duration_ms": 1000,
            "exit_code": 0,
            "cwd": str(tmp_path / "mobile" / "android"),
            "argv": [
                "./gradlew",
                ":app:testDebugUnitTest",
                "--tests",
                "com.eiel.chat.ChatViewModelWorkflowStateTest",
            ],
            "evidence": {
                "stdout_path": str(tmp_path / "runtime" / "state" / "webui" / "verification_jobs" / "stdout.txt"),
                "stderr_path": str(tmp_path / "runtime" / "state" / "webui" / "verification_jobs" / "stderr.txt"),
                "artifact_paths": [],
                "summary": "1 test passed",
            },
            "failure_kind": "",
        }

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)
    monkeypatch.setattr(orch, "_run_host_verification_request", _fake_host_request)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="Phase D",
        objective="Host verification loop",
        options={"subphases": ["D1"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)
    state = result["state"]

    assert result["ok"] is True
    assert str(state.get("status") or "") == "completed"
    assert seen["assurer_calls"] == 2
    assert len(list(state.get("host_verification_results") or [])) == 1
    assert "Host Verification Evidence" in assurer_prompts[-1]
    assert "1 test passed" in assurer_prompts[-1]

    status = orch.get_status(workflow_id=wf, events_limit=200)
    event_names = [str(ev.get("event") or "") for ev in status["events"]]
    assert "host_verification_requested" in event_names
    assert "host_verification_started" in event_names
    assert "host_verification_finished" in event_names


def test_wf_phase_uses_canonical_objective_on_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)

    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": "PIS-20260308-022623Z-E964"},
        phase_label="WF-01",
        objective="Checkpointed autopilot influence proof",
        options={"max_iterations": 4},
    )

    state = started["state"]
    assert str(state["phase_label"]) == "WF-01"
    assert str(state["objective"]) == "Implement and verify PIS-20260308-022623Z-E964 with WF-01."
    assert int(state["max_iterations"]) == 4


def test_builder_run_records_commit_journal_fragment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(
        title="Mobile chat interruption persistence hardening",
        slug="mobile-chat-interruption-persistence-hardening",
        pis_markdown="pis body",
    )
    uid = str(created["uid"])

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief\n- scope\n- acceptance"}}
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "**Change Summary**\n"
                        "- Tightened stream interruption handling.\n"
                        "- Preserved retry state after partial disconnects.\n\n"
                        "**Files Changed**\n"
                        "- `infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt`\n"
                        "- `infra/mobile/android/app/src/test/java/com/eiel/chat/ChatViewModelWorkflowStateTest.kt`\n\n"
                        "**Validation Commands Run**\n"
                        "- `python -m py_compile infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt`\n"
                        "- `pytest -q tests/control_plane/webui/test_webui_agents_orchestration.py`\n\n"
                        "**Residual Risks**\n"
                        "- Full device validation still pending.\n"
                    )
                },
            }
        return {"ok": True, "assistant": {"content": "VERDICT: PASS\nNo findings."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "ledger_uid", "id": uid},
        phase_label="WF-01",
        objective=f"Implement and verify {uid} with WF-01.",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])

    orch.step_workflow(workflow_id=wf)
    result = orch.step_workflow(workflow_id=wf)
    assert result["ok"] is True

    pending_dir = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    entries = sorted(pending_dir.glob("*.json"))
    assert len(entries) == 1

    payload = json.loads(entries[0].read_text(encoding="utf-8"))
    assert payload["author"]["role"] == "builder"
    assert payload["author"]["binding"] == {"type": "ledger_uid", "id": uid}
    assert "Tightened stream interruption handling." in payload["summary_bullets"]
    assert "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt" in payload["files"]
    assert "python -m py_compile infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt" in payload["validation"]

    state = json.loads(_state_path(tmp_path, wf).read_text(encoding="utf-8"))
    assert str(state.get("latest_builder_journal_entry") or "").endswith(entries[0].name)


def test_run_role_turn_repeated_command_note_triggers_stuck_escalation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "codex")

    def _fake_stream_events(*, role: str, binding, message: str, options=None):
        cmd_note = "Running `./gradlew --no-daemon :app:testDebugUnitTest --tests com.eiel.chat.ui.AdminUiStateTest`."
        yield {"type": "commentary", "text": cmd_note}
        yield {"type": "commentary", "text": cmd_note}
        yield {"type": "commentary", "text": cmd_note}

    monkeypatch.setattr(orch._agents, "send_message_stream_events", _fake_stream_events)

    state = {
        "workflow_id": "wf-test",
        "binding": {"type": "task_id", "id": "a7k"},
        "heartbeat_interval_s": 60,
        "turn_timeout_s": 300,
        "failure_retry_bound": 2,
        "role_progress_timeouts": {"builder": 60},
    }

    out = orch._run_role_turn(state=state, role="builder", turn="builder_run", message="do the patch")

    assert out["ok"] is False
    assert "same command/status note" in str(out.get("error") or out.get("stuck_reason") or "")
    status = orch.get_status(workflow_id="wf-test", events_limit=80)
    event_names = [str(row.get("event") or "") for row in status["events"]]
    assert "role_command_started" in event_names
    assert "role_activity_fingerprint" in event_names
    assert any(
        str((row.get("payload") or {}).get("reason") or "").startswith("Still on ./gradlew")
        for row in status["events"]
        if str(row.get("event") or "") == "role_waiting_command"
    )


def test_run_role_turn_wait_snapshot_records_real_wait_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(orch, "_role_waiting_notice_s", lambda progress_timeout_s: 1)
    monkeypatch.setattr(
        orch,
        "_describe_role_wait_state",
        lambda *, state, role: {
            "pid": 4242,
            "summary": "Waiting on local commands: gradlew :app:testDebugUnitTest --tests com.eiel.chat.ChatViewModelWorkflowStateTest (41s)",
            "fingerprint": "wait-fp",
            "fingerprint_summary": "gradlew::app:testDebugUnitTest",
            "processes": [],
        },
    )

    def _fake_stream_events(*, role: str, binding, message: str, options=None):
        time.sleep(1.2)
        yield {"type": "done", "ok": True, "assistant": {"content": "ok"}}

    monkeypatch.setattr(orch._agents, "send_message_stream_events", _fake_stream_events)

    state = {
        "workflow_id": "wf-wait",
        "binding": {"type": "task_id", "id": "a7k"},
        "heartbeat_interval_s": 60,
        "turn_timeout_s": 300,
        "failure_retry_bound": 2,
        "role_progress_timeouts": {"builder": 60},
    }

    out = orch._run_role_turn(state=state, role="builder", turn="builder_run", message="do the patch")

    assert out["ok"] is True
    status = orch.get_status(workflow_id="wf-wait", events_limit=80)
    waiting_events = [row for row in status["events"] if str(row.get("event") or "") == "role_waiting_command"]
    assert waiting_events
    payload = waiting_events[-1]["payload"]
    assert payload["worker_pid"] == 4242
    assert "gradlew :app:testDebugUnitTest" in payload["reason"]


def test_run_role_turn_wait_snapshot_with_repo_progress_does_not_trigger_blocked_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(orch, "_role_waiting_notice_s", lambda progress_timeout_s: 1)
    monkeypatch.setattr(
        orch,
        "_describe_role_wait_state",
        lambda *, state, role: {
            "pid": 4242,
            "summary": "Waiting for builder command output.",
            "fingerprint": "wait-fp",
            "fingerprint_summary": "builder-wait",
            "processes": [],
        },
    )
    progress_rows = [
        {"marker": "", "changed": False, "has_progress": False, "ever_progress": False, "path_count": 0, "note": "", "summary": "", "error": ""},
        {
            "marker": "repo-progress-1",
            "changed": True,
            "has_progress": True,
            "ever_progress": True,
            "path_count": 1,
            "note": "Observed repo changes while builder was running: BENCHMARK_SMOKE_NOTE.txt.",
            "summary": "BENCHMARK_SMOKE_NOTE.txt",
            "error": "",
        },
        {
            "marker": "repo-progress-1",
            "changed": False,
            "has_progress": True,
            "ever_progress": True,
            "path_count": 1,
            "note": "",
            "summary": "BENCHMARK_SMOKE_NOTE.txt",
            "error": "",
        },
    ]

    def _fake_repo_progress(*, state, role):
        if progress_rows:
            return progress_rows.pop(0)
        return {
            "marker": "repo-progress-1",
            "changed": False,
            "has_progress": True,
            "ever_progress": True,
            "path_count": 1,
            "note": "",
            "summary": "BENCHMARK_SMOKE_NOTE.txt",
            "error": "",
        }

    monkeypatch.setattr(orch, "_observe_repo_progress_for_turn", _fake_repo_progress)

    def _fake_stream_events(*, role: str, binding, message: str, options=None):
        time.sleep(1.2)
        time.sleep(5.2)
        yield {"type": "done", "ok": True, "assistant": {"content": "ok"}}

    monkeypatch.setattr(orch._agents, "send_message_stream_events", _fake_stream_events)

    state = {
        "workflow_id": "wf-repo-progress",
        "binding": {"type": "task_id", "id": "a7k"},
        "heartbeat_interval_s": 60,
        "turn_timeout_s": 300,
        "failure_retry_bound": 2,
        "role_progress_timeouts": {"builder": 60},
    }

    out = orch._run_role_turn(state=state, role="builder", turn="builder_run", message="do the patch")

    assert out["ok"] is True
    events = orch.get_status(workflow_id="wf-repo-progress", events_limit=80)["events"]
    assert "role_turn_interrupted" not in [str(row.get("event") or "") for row in events]


def test_run_role_turn_response_started_but_never_finished_triggers_cleanup_watchdog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(orch, "_role_progress_timeout_s", lambda state, role: 10)
    monkeypatch.setattr(orch, "_normalize_role_output_grace_s", lambda raw: 1)
    monkeypatch.setattr(
        orch,
        "_stop_codex_role_worker",
        lambda *, state, role, reason: {"ok": True, "stopped": True, "reason": reason},
    )

    def _fake_stream_events(*, role: str, binding, message: str, options=None):
        yield {"type": "delta", "text": "drafting"}
        time.sleep(1.3)

    monkeypatch.setattr(orch._agents, "send_message_stream_events", _fake_stream_events)

    state = {
        "workflow_id": "wf-output-grace",
        "binding": {"type": "task_id", "id": "a7k"},
        "heartbeat_interval_s": 1,
        "turn_timeout_s": 300,
        "role_progress_timeouts": {"builder": 60},
    }

    out = orch._run_role_turn(state=state, role="builder", turn="builder_run", message="do the patch")

    assert out["ok"] is False
    reason_text = str(out.get("error") or out.get("stuck_reason") or "")
    assert (
        "did not finish cleanly" in reason_text
        or "without a final completion event" in reason_text
    )
    status = orch.get_status(workflow_id="wf-output-grace", events_limit=80)
    event_names = [str(row.get("event") or "") for row in status["events"]]
    assert "role_turn_interrupted" in event_names


def test_run_host_verification_request_mirrors_status_progress_into_workflow_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    state = {
        "workflow_id": "wf-host-progress",
        "binding": {"type": "task_id", "id": "a7k"},
        "turn": "host_verification",
        "status": "running",
    }
    orch._save_state(state)
    request = orch._build_host_verification_request(
        state,
        {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.ui.AdminUiStateTest",
            },
            "reason": "run proof",
            "timeout_s": 3600,
            "stall_timeout_s": 1200,
        },
    )

    def _fake_host_runner(normalized: dict) -> dict:
        job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
        verification_contract.write_json_atomic(
            job_dir / "status.json",
            {
                "request_id": str(normalized["request_id"]),
                "workflow_id": str(normalized["workflow_id"]),
                "action_id": "android_unit_test",
                "status": "running",
                "phase": "streaming_output",
                "progress_note": "Gradle test execution is running on the host",
                "message": "streaming output",
                "remaining_s": 3550,
                "subprocess_pid": 5151,
                "last_output_utc": "2026-03-11T16:00:00Z",
                "timeout_s": 3600,
                "stall_timeout_s": 1200,
                "updated_utc": "2026-03-11T16:00:00Z",
            },
        )
        time.sleep(0.1)
        result = verification_contract.build_result(
            request=normalized,
            ok=True,
            status="passed",
            exit_code=0,
            started_utc="2026-03-11T16:00:00Z",
            finished_utc="2026-03-11T16:00:01Z",
            duration_ms=1000,
            cwd=str(tmp_path / "mobile" / "android"),
            argv=["./gradlew", ":app:testDebugUnitTest"],
            evidence={
                "stdout_path": str(job_dir / "stdout.txt"),
                "stderr_path": str(job_dir / "stderr.txt"),
                "artifact_paths": [],
                "summary": "1 test passed",
            },
            failure_kind="",
        )
        verification_contract.write_json_atomic(job_dir / "result.json", result)
        return result

    monkeypatch.setattr(orch._agents, "_run_host_verification_request", _fake_host_runner)

    result = orch._run_host_verification_request(state=state, request=request)

    assert result["ok"] is True
    persisted = json.loads(_state_path(tmp_path, "wf-host-progress").read_text(encoding="utf-8"))
    assert str(persisted.get("status_detail") or "") == "running_waiting_host_verification"
    assert "Gradle test execution is running on the host" in str(persisted.get("active_progress_note") or "")
    assert int(persisted.get("active_worker_pid") or 0) == 5151
    events = orch.get_status(workflow_id="wf-host-progress", events_limit=80)["events"]
    progress_events = [row for row in events if str(row.get("event") or "") == "host_verification_progress"]
    assert progress_events
    assert progress_events[-1]["payload"]["phase"] == "streaming_output"


def test_escalate_role_stuck_carries_worker_wait_context_into_unblock_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    state = {
        "workflow_id": "wf-stuck",
        "binding": {"type": "task_id", "id": "a7k"},
        "active_progress_note": "Running ./gradlew targeted selector.",
        "last_meaningful_progress_utc": "2026-03-11T12:00:00Z",
        "active_turn_started_utc": "2026-03-11T11:58:00Z",
        "active_slice_timeout_s": 300,
        "active_wait_reason": "Waiting on local commands: gradlew ...",
        "active_worker_pid": 5151,
        "active_worker_summary": "Waiting on local commands: gradlew ...",
        "active_wait_repeat_count": 3,
        "turn": "builder_run",
    }

    out = orch._escalate_role_stuck(
        state=state,
        role="builder",
        turn="builder_run",
        reason="builder kept waiting on the same local command/process state 3 times.",
        detail="blocked_validation_loop",
        text="Repeated local Gradle retry loop.",
    )

    assert out["ok"] is True
    prompt = orch._compose_se_unblock_prompt(out["state"])
    assert "Worker PID: 5151" in prompt
    assert "Worker Summary: Waiting on local commands: gradlew ..." in prompt
    assert "Wait Repeat Count: 3" in prompt
    assert "Workflow Repeat Count: 1" in prompt


def test_step_workflow_se_unblock_uses_persisted_stuck_context_after_reload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Resume persisted stuck context",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["turn"] = "se_unblock"
    state["status"] = "idle"
    state["stuck_context"] = {
        "role": "builder",
        "turn": "builder_run",
        "reason": "builder repeated the same selector",
        "fingerprint_summary": "gradlew::ChatViewModelWorkflowStateTest",
        "last_progress_note": "Running ./gradlew targeted selector.",
        "last_meaningful_progress_utc": "2026-03-11T12:00:00Z",
        "wait_reason": "Waiting on local commands: gradlew ...",
        "worker_pid": 4242,
        "worker_summary": "Waiting on local commands: gradlew ...",
        "wait_repeat_count": 2,
        "error": "stuck loop",
        "response_excerpt": "Repeated selector retry",
    }
    state["stuck_role"] = "builder"
    state["stuck_reason"] = "builder repeated the same selector"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    out = orch.step_workflow(workflow_id=wf)

    assert out["ok"] is True
    assert out["decision"] == "retry_builder"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["turn"] == "builder_run"
    assert "Stop broad validation loops." in str(persisted.get("stuck_recovery_note") or "")
    events = orch.get_status(workflow_id=wf, events_limit=80)["events"]
    done = [row for row in events if str(row.get("event") or "") == "se_unblock_done"]
    assert done
    assert str((done[-1].get("payload") or {}).get("decision_source") or "") == "deterministic"


def test_step_workflow_se_unblock_stops_retrying_same_stuck_fingerprint_after_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Cut repeated stuck retries",
        options={"subphases": ["PIS"], "max_iterations": 1, "stuck_retry_limit": 2},
    )
    wf = str(started["workflow_id"])
    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["turn"] = "se_unblock"
    state["status"] = "idle"
    state["stuck_context"] = {
        "role": "builder",
        "turn": "builder_run",
        "reason": "builder repeated the same selector",
        "fingerprint": "fp-123",
        "fingerprint_summary": "gradlew::ChatViewModelWorkflowStateTest",
        "workflow_repeat_count": 2,
        "retry_limit": 2,
    }
    state["stuck_role"] = "builder"
    state["stuck_reason"] = "builder repeated the same selector"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    out = orch.step_workflow(workflow_id=wf)

    assert out["ok"] is True
    assert out["decision"] == "intent_validation"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["turn"] == "intent_validation"
    events = orch.get_status(workflow_id=wf, events_limit=80)["events"]
    exhausted = [row for row in events if str(row.get("event") or "") == "stuck_retry_budget_exhausted"]
    assert exhausted


def test_step_workflow_se_unblock_moves_builder_to_verifier_when_checkpoint_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")
    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-UNBLOCK",
        objective="Stop wasting builder slices on validation loops",
        options={"subphases": ["PIS"], "max_iterations": 1, "stuck_retry_limit": 2},
    )
    wf = str(started["workflow_id"])
    state_path = _state_path(tmp_path, wf)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["turn"] = "se_unblock"
    state["status"] = "idle"
    state["stuck_context"] = {
        "role": "builder",
        "turn": "builder_run",
        "reason": "builder repeated the same gradle selector",
        "detail": "blocked_validation_loop",
        "wait_reason": "gradlew :app:testDebugUnitTest",
        "response_excerpt": (
            "## Change Summary\n"
            "- Tightened workflow trust.\n\n"
            "## Files Changed\n"
            "- infra/webui/server_agents_orchestrator.py\n"
        ),
        "workflow_repeat_count": 1,
        "retry_limit": 2,
    }
    state["stuck_role"] = "builder"
    state["stuck_reason"] = "builder repeated the same gradle selector"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    out = orch.step_workflow(workflow_id=wf)

    assert out["ok"] is True
    assert out["decision"] == "retry_verifier"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["turn"] == "verifier_run"


def test_se_decide_is_deterministic_by_default_and_records_digest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": (
                        "## Change Summary\n"
                        "- Tightened workflow trust.\n\n"
                        "## Files Changed\n"
                        "- infra/webui/server_agents_orchestrator.py\n"
                    )
                },
            }
        if role == "assurer":
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nTargeted proof passed."}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "VERDICT: PASS\nIntent validated."}}
        raise AssertionError(f"unexpected role: {role}")

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-01",
        objective="Deterministic control plane",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])

    orch.step_workflow(workflow_id=wf)  # se_tasking
    orch.step_workflow(workflow_id=wf)  # builder_run
    orch.step_workflow(workflow_id=wf)  # verifier_run
    out = orch.step_workflow(workflow_id=wf)  # se_decide

    assert out["ok"] is True
    persisted = json.loads(_state_path(tmp_path, wf).read_text(encoding="utf-8"))
    digest = persisted.get("workflow_digest") if isinstance(persisted.get("workflow_digest"), dict) else {}
    assert str(persisted.get("turn") or "") == "intent_validation"
    assert str(digest.get("headline") or "").strip() == "Intent validation running"
    assert "tightened workflow trust" in str(digest.get("summary") or "").lower() or "pass" in str(digest.get("summary") or "").lower()

    listed = orch.list_workflows(binding={"type": "task_id", "id": "a7k"}, limit=5)
    item = next(row for row in listed["items"] if str(row.get("workflow_id") or "") == wf)
    assert str(item.get("headline") or "") == str(digest.get("headline") or "")
    assert str(item.get("next_action") or "").strip()

    events = orch.get_status(workflow_id=wf, events_limit=80)["events"]
    decisions = [row for row in events if str(row.get("event") or "") == "se_decision_done"]
    assert decisions
    assert str((decisions[-1].get("payload") or {}).get("decision_source") or "") == "deterministic"


def test_intent_validation_without_clean_proof_marks_workflow_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(orch._agents, "_agents_backend", lambda: "test")

    def _fake_send_message(*, role: str, binding, message: str, options=None):
        if role == "generalist" and "Intent Validation Agent" in message:
            return {"ok": True, "assistant": {"content": "VERDICT: UNKNOWN\nNeed host proof."}}
        if role == "generalist":
            return {"ok": True, "assistant": {"content": "SE brief"}}
        if role == "builder":
            return {"ok": True, "assistant": {"content": "builder output without run evidence"}}
        return {"ok": True, "assistant": {"content": "VERDICT: UNKNOWN\nNeed host proof."}}

    monkeypatch.setattr(orch._agents, "send_message", _fake_send_message)

    started = orch.start_workflow(
        binding={"type": "task_id", "id": "a7k"},
        phase_label="WF-FAIL-HONESTLY",
        objective="Do not count unknown proof as success",
        options={"subphases": ["PIS"], "max_iterations": 1},
    )
    wf = str(started["workflow_id"])
    result = orch.run_workflow(workflow_id=wf, max_steps=20)

    assert result["ok"] is False
    state = result["state"]
    assert str(state.get("status") or "") == "failed"
    assert str(state.get("status_detail") or "") == "evidence_gap"
    digest = state.get("workflow_digest") if isinstance(state.get("workflow_digest"), dict) else {}
    assert str(digest.get("headline") or "") == "Workflow ended without clean proof"
