from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger


def _wait_for_active_run(job_id: str, *, repo_root: Path, timeout_s: float = 5.0) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = sched.load_state(job_id, repo_root=repo_root)
        run_id = str(state.get("active_run_id") or "").strip()
        if run_id:
            return run_id
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for active_run_id for {job_id}")


def test_execute_job_preserves_mid_run_cancel_and_records_repo_residue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})

    continue_event = threading.Event()
    entered_turn = threading.Event()

    def _fake_git_status_porcelain(*, repo_root: Path | None = None) -> str:
        root = Path(repo_root or tmp_path).resolve()
        lines: list[str] = []
        if (root / "touched.txt").exists():
            lines.append(" M touched.txt")
        if (root / "new.txt").exists():
            lines.append("?? new.txt")
        return "\n".join(lines)

    def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        (tmp_path / "touched.txt").write_text("updated\n", encoding="utf-8")
        (tmp_path / "new.txt").write_text("created\n", encoding="utf-8")
        entered_turn.set()
        assert continue_event.wait(timeout=5.0), "test never released scheduled single-turn worker"
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "Changed files:\n"
                    "- touched.txt\n\n"
                    "Validation Commands Run:\n"
                    "- python -m py_compile touched.txt\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched, "_git_status_porcelain", _fake_git_status_porcelain)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    job_id = "cancel_and_residue_001"
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "tsk_cancel_residue"},
            "prompt": "Make a bounded change and report the touched files.",
            "schedule": {"mode": "once", "run_at": "2026-04-02T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )

    result_box: dict[str, object] = {}

    def _run_job() -> None:
        result_box["result"] = sched.execute_job(
            job_id,
            repo_root=tmp_path,
            trigger="manual",
            force=True,
            now_utc="2026-04-02T10:05:00Z",
        )

    thread = threading.Thread(target=_run_job, name="scheduler-cancel-residue-test", daemon=True)
    thread.start()

    run_id = _wait_for_active_run(job_id, repo_root=tmp_path)
    assert entered_turn.wait(timeout=5.0), "scheduled worker never entered send_message"

    cancel = sched.cancel_job(job_id, reason="operator_test", repo_root=tmp_path)
    assert cancel["ok"] is True
    assert cancel["status"] == "cancel_requested"
    assert cancel["stop_proof_pending"] is True

    continue_event.set()
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "execute_job thread did not finish"

    result = dict(result_box["result"])
    assert result["ok"] is True
    receipt = sched.load_receipt(job_id, run_id, repo_root=tmp_path)
    state = sched.load_state(job_id, repo_root=tmp_path)

    assert receipt["resource_claims"]["claimant_uid"] == f"scheduled_job:{job_id}"
    assert receipt["resource_claims"]["release_error"] == ""
    assert [row["terminal_status"] for row in receipt["resource_claims"]["claims"]] == ["released"]
    assert receipt["lifecycle"]["cancel"]["requested"] is True
    assert receipt["lifecycle"]["cancel"]["request_run_id"] == run_id
    assert receipt["lifecycle"]["cancel"]["result"] == "finished_before_stop"
    assert receipt["repo_residue"]["declared_changed_files"] == ["touched.txt"]
    assert sorted(receipt["repo_residue"]["introduced_dirty_paths"]) == ["new.txt", "touched.txt"]
    assert receipt["repo_residue"]["dirty_paths_not_declared"] == ["new.txt"]
    assert receipt["repo_residue"]["validation_commands"] == ["python -m py_compile touched.txt"]
    assert state["last_lifecycle_cancel_requested"] is True
    assert state["last_lifecycle_cancel_outcome"] == "finished_before_stop"
    assert state["last_repo_residue_changed_count"] == 2
    assert state["last_repo_residue_undeclared_count"] == 1
    assert state["cancel_requested_utc"] == ""


def test_cancel_job_finalizes_immediately_when_active_claim_is_stale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "cancel_stale_claim_001",
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "tsk_cancel_stale"},
            "prompt": "Cancel stale run.",
            "schedule": {"mode": "once", "run_at": "2026-04-02T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )

    run_id = "sj_cancel_stale_001"
    sched._write_state(
        "cancel_stale_claim_001",
        {
            "job_id": "cancel_stale_claim_001",
            "active_run_id": run_id,
            "active_started_utc": "2026-04-02T10:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("cancel_stale_claim_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "cancel_stale_claim_001",
                "run_id": run_id,
                "pid": 999999,
                "claimed_utc": "2026-04-02T10:00:00Z",
                "heartbeat_utc": "2026-04-02T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    sched._record_scheduled_job_runtime_claim_acquired(
        job_id="cancel_stale_claim_001",
        run_id=run_id,
        trigger="scheduler",
        claimed_at_utc="2026-04-02T10:00:00Z",
        claim_row={"job_kind": "single_turn"},
        repo_root=tmp_path,
    )

    cancel = sched.cancel_job(
        "cancel_stale_claim_001",
        reason="operator_test",
        repo_root=tmp_path,
        now_utc="2026-04-02T10:10:00Z",
    )

    assert cancel["ok"] is True
    assert cancel["status"] == "cancelled"
    assert cancel["stop_proof_pending"] is False
    assert cancel["receipt"]["resource_claims"]["claimant_uid"] == "scheduled_job:cancel_stale_claim_001"
    assert cancel["receipt"]["resource_claims"]["release_error"] == ""
    assert [row["terminal_status"] for row in cancel["receipt"]["resource_claims"]["claims"]] == ["released"]
    state = sched.load_state("cancel_stale_claim_001", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "cancelled"
    assert state["cancel_requested_utc"] == ""
    lineage = resource_claim_ledger.read_claim_lineage(
        sched._scheduled_job_runtime_claim_uid("cancel_stale_claim_001", run_id),
        repo_root=tmp_path,
    )
    assert [row["status"] for row in lineage] == ["acquired", "released"]
