from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_execution_queue as eq
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_execution_reconciliation as recon
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui.ledger.ledger_manager import ChangeLedger


pytestmark = [pytest.mark.regression, pytest.mark.integration]


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "operator@example.invalid"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "S6U2 Regression"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "tag.gpgSign", "false"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "core.hooksPath", "/dev/null"], cwd=str(root), check=True)
    (root / ".gitignore").write_text("*.pyc\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init"],
        cwd=str(root),
        check=True,
        timeout=20,
    )


def _commit_repo_state(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", message],
        cwd=str(root),
        check=True,
        timeout=20,
    )


def _reset_queue(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    if eq._WORKER_STOP is not None:
        eq._WORKER_STOP.set()
    eq._STOP_AFTER_CURRENT.clear()
    monkeypatch.setattr(eq, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(eq, "_WORKER", None)
    monkeypatch.setattr(eq, "_WORKER_STOP", None)


def _wait_for_terminal_state(timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last = eq.status()
    while time.time() < deadline:
        last = eq.status()
        state = last.get("state") if isinstance(last.get("state"), dict) else {}
        if not bool(state.get("active")):
            return last
        time.sleep(0.05)
    raise TimeoutError(f"queue did not reach terminal state: {last}")


def test_s6u2_unified_execution_regression_covers_pause_handoff_reconcile_and_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_execution_queue", eq)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First substrate item", slug="s6u2-first", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second substrate item", slug="s6u2-second", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _commit_repo_state(tmp_path, "seed queue priorities")

    started: list[str] = []
    committed: list[str] = []
    send_calls: list[dict[str, Any]] = []
    release_first = threading.Event()
    stop_result: dict[str, Any] = {}

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: started.append(str(binding["id"])) or {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id: str, max_steps: int = 40, heartbeat_interval_s=None, timeout_s=None) -> dict[str, Any]:
        if workflow_id == f"wf-{first}":
            release_first.wait(timeout=5.0)
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "done": True,
            "state": {"workflow_id": workflow_id, "status": "completed", "last_error": ""},
        }

    monkeypatch.setattr(eq._orch, "run_workflow", _fake_run_workflow)
    monkeypatch.setattr(
        eq,
        "_commit_worktree",
        lambda *, uid, workflow_id, timeout_s: committed.append(uid)
        or {"log_path": str(tmp_path / f"{uid}.commit.log"), "commit_sha": f"sha-{uid[-4:]}", "tag": "baseline-001"},
    )

    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda *, role, binding, message, options=None: send_calls.append(
            {"role": role, "binding": dict(binding), "message": message, "options": dict(options or {})}
        )
        or {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}},
    )

    eq.start_queue()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        current = eq.status().get("state") if isinstance(eq.status().get("state"), dict) else {}
        if str(current.get("current_workflow_id") or "").strip():
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"queue never started a workflow: {eq.status()}")

    stopper = threading.Thread(target=lambda: stop_result.update(eq.stop_queue(mode="after_current")), daemon=True)
    stopper.start()
    time.sleep(0.1)
    release_first.set()
    stopper.join(timeout=10.0)
    assert not stopper.is_alive()

    queue_payload = _wait_for_terminal_state(timeout_s=25.0)
    queue_state = queue_payload["state"]
    queue_report = queue_payload["batch_report"]
    assert queue_state["status"] == "paused"
    assert queue_state["status_detail"] == "stopped_after_current"
    assert queue_report["outcome"] == "paused_partial"
    assert queue_report["commit_boundary_mode"] == "between_items"
    assert started == [first]
    assert committed == [first]

    sched.save_job(
        {
            "job_id": "s6u2_paused_queue_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260313-030603Z-X4Y6"},
            "prompt": "Run S6U2 substrate verification.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    sched_tick = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-08T04:00:00Z")
    assert sched_tick["executed_count"] == 0
    assert len(sched_tick["skipped"]) == 1
    assert sched_tick["skipped"][0]["reason"] == "execution_queue_paused"
    assert send_calls == []

    sched_state = sched.load_state("s6u2_paused_queue_loop_001", repo_root=tmp_path)
    assert sched_state["last_nonlaunch_status"] == "deferred"
    assert sched_state["last_nonlaunch_reason"] == "execution_queue_paused"
    receipt_path = Path(sched_state["last_nonlaunch_receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "deferred"
    assert receipt["ownership"]["handoff_state"] == "not_launched"

    handoff = jobs.accept_mailbox_handoff(
        tmp_path,
        request_record={
            "request_id": "req_s6u2_01",
            "state": "handed_off",
            "request": {
                "request_id": "req_s6u2_01",
                "correlation_id": "corr_s6u2_01",
                "launch_turn_id": "turn_s6u2_01",
                "owner_ref": {"task_id": "task_s6u2_01", "ledger_uid": "PIS-20260313-030606Z-B7C9"},
            },
            "handoff": {
                "detached_job_id": "job_s6u2_01",
                "accepted_utc": "2026-03-13T02:00:00Z",
                "job_surface": "workflow",
                "claim_id": "claim_s6u2_01",
                "worker_id": "worker_s6u2_01",
            },
        },
        writer="execution.s6u2.accept",
        timeout_s=0,
    )
    assert handoff["job_record"]["state"] == "queued"

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_s6u2_01",
        writer="execution.s6u2.heartbeat",
        worker_id="worker_s6u2_01",
        progress_message="workflow step started",
        now_utc="2026-03-13T02:00:30Z",
    )
    reconciled = jobs.reconcile_jobs(
        tmp_path,
        writer="execution.s6u2.reconcile",
        mode="startup",
        now_utc="2026-03-13T02:06:00Z",
    )
    job_record = jobs.read_job(tmp_path, detached_job_id="job_s6u2_01")
    assert reconciled["changed_job_ids"] == ["job_s6u2_01"]
    assert job_record["state"] == "interrupted"
    assert job_record["state_reason"] == "startup_reconcile_missing_heartbeat"

    workflow_state_path = (
        tmp_path / "governance" / "agents" / "runtime" / "orchestration" / "workflow__wf_s6u2_conflict.json"
    )
    workflow_digest_path = (
        tmp_path
        / "governance"
        / "agents"
        / "runtime"
        / "orchestration"
        / "workflow__wf_s6u2_conflict.artifacts"
        / "workflow_digest.json"
    )
    residue_summary_path = (
        tmp_path
        / "governance"
        / "agents"
        / "runtime"
        / "orchestration"
        / "workflow__wf_s6u2_conflict.artifacts"
        / "repo_residue"
        / "summary.json"
    )
    for path in (workflow_state_path, workflow_digest_path, residue_summary_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    workflow_state_path.write_text(json.dumps({"workflow_id": "wf_s6u2_conflict", "status": "completed"}), encoding="utf-8")
    workflow_digest_path.write_text(json.dumps({"summary": "workflow passed"}), encoding="utf-8")
    residue_summary_path.write_text(json.dumps({"residue": "captured"}), encoding="utf-8")

    report = recon.record_task_reconciliation_report(
        tmp_path,
        task_id="task_s6u2_01",
        ledger_uid="PIS-20260313-030606Z-B7C9",
        task_status="active",
        refs=[
            {
                "surface": "workflow",
                "subsystem_id": "wf_s6u2_conflict",
                "delegation_request_id": "req_s6u2_01",
                "status_refs": [str(workflow_state_path)],
                "artifact_refs": [str(residue_summary_path)],
            },
            {
                "surface": "scheduler",
                "subsystem_id": "s6u2_paused_queue_loop_001",
                "delegation_request_id": "req_sched_s6u2_01",
                "status_refs": [str(Path(sched._state_path("s6u2_paused_queue_loop_001", repo_root=tmp_path)))],
                "artifact_refs": [str(receipt_path)],
            },
            {
                "surface": "detached_job",
                "subsystem_id": "job_s6u2_01",
                "delegation_request_id": "req_s6u2_01",
                "status_refs": [str(tmp_path / "runtime" / "state" / "lab_assistant" / "detached_jobs" / "jobs" / "job_s6u2_01.json")],
            },
        ],
        syntheses=[
            {
                "request_id": "req_s6u2_01",
                "status": "completed",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": [str(workflow_digest_path)],
            },
            {
                "request_id": "req_sched_s6u2_01",
                "status": "waiting_on_job",
                "native_status": "deferred",
                "evidence_status": "present",
                "evidence_refs": [str(receipt_path)],
            },
            {
                "request_id": "req_s6u2_01",
                "status": "interrupted",
                "native_status": "interrupted",
                "evidence_status": "present",
                "evidence_refs": [str(tmp_path / "runtime" / "state" / "lab_assistant" / "detached_jobs" / "jobs" / "job_s6u2_01.json")],
            },
        ],
        writer="execution.s6u2.reconciliation",
    )
    projection = report["projection"]
    assert projection["status"] == "ambiguous_artifact"
    assert projection["ambiguous"] is True
    assert projection["conflict_count"] >= 1
    assert "success_conflicts_with_other_truth" in projection["reason_codes"]
    assert any(str(path).endswith("repo_residue/summary.json") for path in report["durable_refs"]["residue_refs"])
    assert str(receipt_path) in report["durable_refs"]["receipt_refs"]
    assert Path(report["report_paths"]["report_path"]).exists()
    assert Path(report["report_paths"]["receipt_path"]).exists()
    assert Path(report["report_paths"]["latest_task_report_path"]).exists()
