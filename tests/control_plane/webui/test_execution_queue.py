from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.controlplane import job_budget_cost_ledger_store
from infra.webui import server_execution_queue as eq
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_routes_and_main as routes
from infra.webui.ledger.ledger_manager import ChangeLedger


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: dict | None = None) -> None:
        raw = json.dumps(body or {}).encode("utf-8")
        self.path = path
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
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


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "operator@example.invalid"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "EIEL Test"], cwd=str(root), check=True)
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


def _reset_queue(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    if eq._WORKER_STOP is not None:
        eq._WORKER_STOP.set()
    eq._STOP_AFTER_CURRENT.clear()
    monkeypatch.setattr(eq, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(eq, "_WORKER", None)
    monkeypatch.setattr(eq, "_WORKER_STOP", None)


def _bootstrap_task_register(root: Path) -> None:
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)


def _seed_task_for_ledger_uid(root: Path, *, task_id: str, ledger_uid: str, title: str) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="execution_queue.test.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": title,
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "queue execution",
        },
    )["task_register"]


def _commit_repo_state(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", message],
        cwd=str(root),
        check=True,
        timeout=20,
    )


def _wait_for_terminal_state(timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    last = eq.status()
    while time.time() < deadline:
        last = eq.status()
        state = last.get("state") if isinstance(last.get("state"), dict) else {}
        if not bool(state.get("active")):
            return last
        time.sleep(0.05)
    raise TimeoutError(f"queue did not reach terminal state: {last}")


def test_execution_queue_runs_prioritized_items_in_order_and_commits_each_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _commit_repo_state(tmp_path, "seed queue items")

    started: list[str] = []
    committed: list[str] = []

    def _fake_start_workflow(*, binding, phase_label: str, objective: str, options=None):
        uid = str((binding or {}).get("id") or "")
        started.append(uid)
        return {"ok": True, "workflow_id": f"wf-{uid}", "state": {"workflow_id": f"wf-{uid}", "status": "idle"}}

    def _fake_run_workflow(*, workflow_id: str, max_steps: int = 40, heartbeat_interval_s=None, timeout_s=None):
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "done": True,
            "state": {"workflow_id": workflow_id, "status": "completed", "last_error": ""},
        }

    def _fake_commit_worktree(*, uid: str, workflow_id: str, timeout_s: int):
        committed.append(uid)
        return {
            "log_path": str(tmp_path / f"{uid}.commit.log"),
            "commit_sha": f"sha-{uid[-4:]}",
            "tag": f"baseline-{len(committed):03d}",
        }

    monkeypatch.setattr(eq._orch, "start_workflow", _fake_start_workflow)
    monkeypatch.setattr(eq._orch, "run_workflow", _fake_run_workflow)
    monkeypatch.setattr(eq, "_commit_worktree", _fake_commit_worktree)

    started_payload = eq.start_queue()
    assert started_payload["ok"] is True

    finished = _wait_for_terminal_state()
    state = finished["state"]
    report = finished["batch_report"]
    assert state["status"] == "idle"
    assert state["last_completed_uid"] == second
    assert started == [first, second]
    assert committed == [first, second]
    assert report["outcome"] == "completed"
    assert report["total_count"] == 2
    assert report["completed_count"] == 2
    assert report["commit_boundary_mode"] == "between_items"

    first_meta = ledger.read_item(first)["meta"]
    second_meta = ledger.read_item(second)["meta"]
    assert first_meta["status"] == "implemented"
    assert second_meta["status"] == "implemented"
    assert first_meta["execution_priority"] is None
    assert second_meta["execution_priority"] is None


def test_execution_queue_stops_on_workflow_failure_and_preserves_remaining_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _commit_repo_state(tmp_path, "seed queue items")

    commit_calls: list[str] = []

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )
    monkeypatch.setattr(
        eq._orch,
        "run_workflow",
        lambda *, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None: {
            "ok": False,
            "workflow_id": workflow_id,
            "done": True,
            "error": "compile failed",
            "state": {"workflow_id": workflow_id, "status": "failed", "last_error": "compile failed"},
        },
    )
    monkeypatch.setattr(
        eq,
        "_commit_worktree",
        lambda *, uid, workflow_id, timeout_s: commit_calls.append(uid) or {"log_path": "", "commit_sha": "", "tag": ""},
    )

    eq.start_queue()
    finished = _wait_for_terminal_state()
    state = finished["state"]

    assert state["status"] == "failed"
    assert state["last_failed_uid"] == first
    assert state["last_failed_stage"] == "workflow"
    assert commit_calls == []

    first_meta = ledger.read_item(first)["meta"]
    second_meta = ledger.read_item(second)["meta"]
    assert first_meta["execution_priority"] == 0
    assert second_meta["execution_priority"] == 100
    assert first_meta["status"] == "draft"
    assert second_meta["status"] == "draft"


def test_execution_queue_reconcile_marks_commit_interruption_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    state = eq._default_state()
    state.update(
        {
            "active": True,
            "status": "running",
            "current_uid": "PIS-20260311-120000Z-AAAA",
            "current_workflow_id": "wf-test",
            "current_phase": "committing",
        }
    )
    eq._save_state(state)

    payload = eq.ensure_worker_running()
    current = payload["state"]
    assert current["active"] is False
    assert current["status"] == "failed"
    assert current["last_failed_stage"] == "reconcile"
    assert "interrupted during commit" in current["last_error"]


def test_execution_queue_stop_after_current_pauses_before_starting_next_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _commit_repo_state(tmp_path, "seed queue items")

    started: list[str] = []
    committed: list[str] = []
    release_first = threading.Event()
    stop_result: dict = {}

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: started.append(str(binding["id"])) or {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )
    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
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

    eq.start_queue()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        state = eq.status()["state"]
        if str(state.get("current_workflow_id") or "").strip():
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"queue never started a workflow: {eq.status()}")

    stopper = threading.Thread(
        target=lambda: stop_result.update(eq.stop_queue(mode="after_current")),
        name="execution-queue-stop-test",
        daemon=True,
    )
    stopper.start()
    time.sleep(0.1)
    release_first.set()
    stopper.join(timeout=10.0)
    assert not stopper.is_alive()

    stop_state = stop_result["state"]
    assert bool(stop_state["stop_requested"]) or stop_state["status"] == "paused"

    finished = _wait_for_terminal_state(timeout_s=25.0)
    state = finished["state"]
    report = finished["batch_report"]
    assert state["status"] == "paused"
    assert state["status_detail"] == "stopped_after_current"
    assert started == [first]
    assert committed == [first]
    assert report["outcome"] == "paused_partial"
    assert report["resumable"] is True
    assert report["completed_count"] == 1
    assert report["remaining_count"] == 1

    first_meta = ledger.read_item(first)["meta"]
    second_meta = ledger.read_item(second)["meta"]
    assert first_meta["status"] == "implemented"
    assert first_meta["execution_priority"] is None
    assert second_meta["status"] == "draft"
    assert second_meta["execution_priority"] == 100


def test_execution_queue_stop_after_current_records_pause_intervention_in_task_register(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)
    _bootstrap_task_register(tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _seed_task_for_ledger_uid(tmp_path, task_id="task_queue_first", ledger_uid=first, title="First item queue task")
    _commit_repo_state(tmp_path, "seed queue items with task register binding")

    release_first = threading.Event()

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
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
        lambda *, uid, workflow_id, timeout_s: {
            "log_path": str(tmp_path / f"{uid}.commit.log"),
            "commit_sha": f"sha-{uid[-4:]}",
            "tag": "baseline-001",
        },
    )

    eq.start_queue()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        state = eq.status()["state"]
        if str(state.get("current_workflow_id") or "").strip() == f"wf-{first}":
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"queue never started first workflow: {eq.status()}")

    stop_result = eq.stop_queue(mode="after_current")
    assert bool(stop_result["state"]["stop_requested"]) is True
    release_first.set()
    _wait_for_terminal_state(timeout_s=25.0)

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_queue_first")
    assert task["current_run_id"] == f"wf-{first}"
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == f"wf-{first}")
    latest = run_row["interventions"][-1]
    assert run_row["surface"] == "pipeline"
    assert latest["action"] == "pause"
    assert latest["requested_by"] == "operator"
    assert latest["reason"] == "Operator requested execution_queue stop-after-current."


def test_execution_queue_resume_keeps_batch_snapshot_order_across_ledger_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    third = str(ledger.create_item(title="Third item", slug="third-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    ledger.update_meta(third, {"execution_priority": 200})
    _commit_repo_state(tmp_path, "seed queue items")

    started: list[str] = []
    committed: list[str] = []
    release_first = threading.Event()

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: started.append(str(binding["id"])) or {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
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
        or {"log_path": str(tmp_path / f"{uid}.commit.log"), "commit_sha": f"sha-{uid[-4:]}", "tag": f"baseline-{len(committed):03d}"},
    )

    eq.start_queue()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        state = eq.status()["state"]
        if str(state.get("current_workflow_id") or "").strip() == f"wf-{first}":
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"queue never started first workflow: {eq.status()}")

    eq.stop_queue(mode="after_current")
    release_first.set()
    paused = _wait_for_terminal_state(timeout_s=25.0)
    assert paused["state"]["status"] == "paused"
    assert paused["batch_report"]["resumable"] is True

    late = str(ledger.create_item(title="Late item", slug="late-item", pis_markdown="pis")["uid"])
    ledger.update_meta(late, {"execution_priority": -50})
    ledger.update_meta(second, {"execution_priority": 999})
    ledger.update_meta(third, {"execution_priority": -100})
    _commit_repo_state(tmp_path, "mutate ledger after pause")

    resumed = eq.start_queue()
    assert resumed["resumed"] is True
    finished = _wait_for_terminal_state(timeout_s=25.0)

    assert finished["state"]["status"] == "idle"
    assert started == [first, second, third]
    assert committed == [first, second, third]
    assert finished["batch_report"]["outcome"] == "completed"
    assert finished["batch_report"]["resume_count"] == 1

    second_meta = ledger.read_item(second)["meta"]
    third_meta = ledger.read_item(third)["meta"]
    late_meta = ledger.read_item(late)["meta"]
    assert second_meta["status"] == "implemented"
    assert third_meta["status"] == "implemented"
    assert late_meta["status"] == "draft"
    assert late_meta["execution_priority"] == -50


def test_execution_queue_failed_batch_reports_partial_commit_boundary_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    ledger.update_meta(second, {"execution_priority": 100})
    _commit_repo_state(tmp_path, "seed queue items")

    started: list[str] = []
    committed: list[str] = []

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: started.append(str(binding["id"])) or {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
        uid = workflow_id.removeprefix("wf-")
        if uid == second:
            return {
                "ok": False,
                "workflow_id": workflow_id,
                "done": True,
                "error": "tests failed",
                "state": {"workflow_id": workflow_id, "status": "failed", "last_error": "tests failed"},
            }
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

    eq.start_queue()
    finished = _wait_for_terminal_state(timeout_s=25.0)
    state = finished["state"]
    report = finished["batch_report"]

    assert state["status"] == "failed"
    assert state["last_failed_uid"] == second
    assert started == [first, second]
    assert committed == [first]
    assert report["outcome"] == "failed_partial"
    assert report["completed_count"] == 1
    assert report["failed_count"] == 1
    assert report["remaining_count"] == 1
    assert report["resumable"] is False
    assert report["commit_boundary_mode"] == "between_items"


def test_execution_queue_status_does_not_rewrite_unchanged_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    state = eq._default_state()
    state["status"] = "idle"
    eq._save_state(state)

    writes: list[dict] = []
    original_save = eq._save_state

    def _spy_save(payload: dict):
        writes.append(dict(payload))
        return original_save(payload)

    monkeypatch.setattr(eq, "_save_state", _spy_save)

    payload = eq.status()
    assert payload["state"]["status"] == "idle"
    assert writes == []


def test_execution_queue_start_rejects_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed queue items")
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(eq.ExecutionQueueError, match="clean git worktree"):
        eq.start_queue()


def test_execution_queue_start_rejects_pending_commit_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed queue items")

    monkeypatch.setattr(eq._commit_journal, "scan_pending_entries", lambda repo: {"pending_count": 1, "invalid_count": 0})

    with pytest.raises(eq.ExecutionQueueError, match="empty pending commit journal"):
        eq.start_queue()


def test_execution_queue_start_accepts_explicit_selected_uids_in_selected_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First item", slug="first-item", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second item", slug="second-item", pis_markdown="pis")["uid"])
    ledger.update_meta(first, {"execution_priority": 100})
    ledger.update_meta(second, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed selected queue items")

    started: list[str] = []

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: started.append(str(binding["id"])) or {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )
    monkeypatch.setattr(
        eq._orch,
        "run_workflow",
        lambda *, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None: {
            "ok": True,
            "workflow_id": workflow_id,
            "done": True,
            "state": {"workflow_id": workflow_id, "status": "completed", "last_error": ""},
        },
    )
    monkeypatch.setattr(
        eq,
        "_commit_worktree",
        lambda *, uid, workflow_id, timeout_s: {"log_path": "", "commit_sha": f"sha-{uid[-4:]}", "tag": ""},
    )

    payload = eq.start_queue(options={"selected_uids": [first, second], "selection_mode": "selected_order"})
    assert payload["ok"] is True

    finished = _wait_for_terminal_state()
    assert started == [first, second]
    assert finished["batch_report"]["completed_count"] == 2
    assert finished["batch_report"]["outcome"] == "completed"


def test_execution_queue_posts_success_lifecycle_cost_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Costed item", slug="costed-item", pis_markdown="pis")["uid"])
    ledger.update_meta(uid, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed queue item for lifecycle cost success")

    calls = {"run_workflow": 0}

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
        calls["run_workflow"] += 1
        if calls["run_workflow"] == 1:
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "done": False,
                "state": {"workflow_id": workflow_id, "status": "running", "last_error": ""},
            }
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
        lambda *, uid, workflow_id, timeout_s: {"log_path": "", "commit_sha": f"sha-{uid[-4:]}", "tag": ""},
    )

    eq.start_queue()
    finished = _wait_for_terminal_state()
    assert finished["state"]["status"] == "idle"

    cost_events = job_budget_cost_ledger_store.read_cost_event_records(uid, repo_root=tmp_path)
    assert [row["cost_event_uid"] for row in cost_events] == [
        f"wf-{uid}:launch",
        f"wf-{uid}:running_started",
        f"wf-{uid}:terminal:succeeded",
    ]
    assert [row["cost_reason_code"] for row in cost_events] == ["reservation", "usage", "usage"]
    assert all(row["job_uid"] == uid for row in cost_events)
    assert all(row["unit"] == "lifecycle_events" for row in cost_events)
    assert all(row["direction"] == "debit" for row in cost_events)
    assert all(row["amount_minor_units"] == 1 for row in cost_events)


def test_execution_queue_resume_does_not_double_post_lifecycle_cost_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Resume item", slug="resume-item", pis_markdown="pis")["uid"])
    ledger.update_meta(uid, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed queue item for lifecycle cost resume")

    calls = {"run_workflow": 0}

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )

    def _fake_run_workflow(*, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None):
        calls["run_workflow"] += 1
        if calls["run_workflow"] == 1:
            if eq._WORKER_STOP is not None:
                eq._WORKER_STOP.set()
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "done": False,
                "state": {"workflow_id": workflow_id, "status": "running", "last_error": ""},
            }
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
        lambda *, uid, workflow_id, timeout_s: {"log_path": "", "commit_sha": f"sha-{uid[-4:]}", "tag": ""},
    )

    eq.start_queue()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        payload = eq.status()
        if payload["worker_alive"] is False and bool(payload["state"]["active"]) is True:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"queue never exposed an interrupted active state: {eq.status()}")

    resumed = eq.ensure_worker_running()
    assert resumed["worker_alive"] is True
    finished = _wait_for_terminal_state()
    assert finished["state"]["status"] == "idle"

    cost_events = job_budget_cost_ledger_store.read_cost_event_records(uid, repo_root=tmp_path)
    assert [row["cost_event_uid"] for row in cost_events] == [
        f"wf-{uid}:launch",
        f"wf-{uid}:running_started",
        f"wf-{uid}:terminal:succeeded",
    ]


def test_execution_queue_failure_posts_terminal_cost_trail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_git_repo(tmp_path)
    _reset_queue(monkeypatch, tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Failing item", slug="failing-item", pis_markdown="pis")["uid"])
    ledger.update_meta(uid, {"execution_priority": 0})
    _commit_repo_state(tmp_path, "seed queue item for lifecycle cost failure")

    monkeypatch.setattr(
        eq._orch,
        "start_workflow",
        lambda *, binding, phase_label, objective, options=None: {
            "ok": True,
            "workflow_id": f"wf-{binding['id']}",
            "state": {"workflow_id": f"wf-{binding['id']}", "status": "idle"},
        },
    )
    monkeypatch.setattr(
        eq._orch,
        "run_workflow",
        lambda *, workflow_id, max_steps=40, heartbeat_interval_s=None, timeout_s=None: {
            "ok": False,
            "workflow_id": workflow_id,
            "done": True,
            "error": "compile failed",
            "state": {"workflow_id": workflow_id, "status": "failed", "last_error": "compile failed"},
        },
    )

    eq.start_queue()
    finished = _wait_for_terminal_state()
    assert finished["state"]["status"] == "failed"

    cost_events = job_budget_cost_ledger_store.read_cost_event_records(uid, repo_root=tmp_path)
    assert [row["cost_event_uid"] for row in cost_events] == [
        f"wf-{uid}:launch",
        f"wf-{uid}:terminal:failed",
    ]
    assert [row["cost_reason_code"] for row in cost_events] == ["reservation", "usage"]


def test_mobile_execution_queue_routes_delegate_to_server_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    seen: dict[str, dict] = {}

    def _fake_status():
        return {"ok": True, "state": {"status": "idle", "active": False}, "events": [], "queue_preview": [], "worker_alive": False}

    def _fake_start_queue(*, options=None):
        seen["start"] = dict(options or {})
        return {"ok": True, "state": {"status": "running", "active": True}, "events": [], "queue_preview": [], "worker_alive": True}

    def _fake_stop_queue(*, mode="after_current"):
        seen["stop"] = {"mode": mode}
        return {"ok": True, "state": {"status": "paused", "active": False}, "events": [], "queue_preview": [], "worker_alive": False}

    monkeypatch.setattr(routes._execution_queue, "status", _fake_status)
    monkeypatch.setattr(routes._execution_queue, "start_queue", _fake_start_queue)
    monkeypatch.setattr(routes._execution_queue, "stop_queue", _fake_stop_queue)

    get_req = _DummyReq(cfg=cfg, path="/api/mobile/backlog/execution_queue")
    routes.WebUIRequestHandler.do_GET(get_req)  # type: ignore[arg-type]
    assert get_req._status == 200
    assert _read_payload(get_req)["schema_version"] == "mobile_execution_queue_status_v1"

    start_req = _DummyReq(
        cfg=cfg,
        path="/api/mobile/backlog/execution_queue/start",
        body={
            "phase_label": "WF-01",
            "max_iterations": 2,
            "commit_each_success": True,
            "selected_uids": ["PIS-20260402-000000Z-A1B2"],
            "selection_mode": "selected_order",
        },
    )
    routes.WebUIRequestHandler.do_POST(start_req)  # type: ignore[arg-type]
    assert start_req._status == 200
    assert seen["start"]["phase_label"] == "WF-01"
    assert seen["start"]["workflow_options"]["max_iterations"] == 2
    assert seen["start"]["selected_uids"] == ["PIS-20260402-000000Z-A1B2"]
    assert seen["start"]["selection_mode"] == "selected_order"

    stop_req = _DummyReq(
        cfg=cfg,
        path="/api/mobile/backlog/execution_queue/stop",
        body={"mode": "after_current"},
    )
    routes.WebUIRequestHandler.do_POST(stop_req)  # type: ignore[arg-type]
    assert stop_req._status == 200
    assert seen["stop"] == {"mode": "after_current"}
