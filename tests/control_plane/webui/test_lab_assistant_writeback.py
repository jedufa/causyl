from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in register_obj.get(bucket_name, []):
            if task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def _seed_task(root: Path, *, task_id: str = "task_e2f8", ledger_uid: str = "PIS-20260312-224201Z-E2F8") -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="e2f8.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "Assimilate delegated outcome",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "wait for delegated result",
        },
    )["task_register"]


def _classification(surface: str) -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": f"delegate via {surface}",
            "preferred_surface": surface,
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
        }
    )


def _mailbox_request(task_id: str) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-224201Z-E2F8"},
        summary="Queue delegated work",
        requested_action="enqueue mailbox request",
        expected_evidence=[{"kind": "mailbox_record", "description": "mailbox state", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review delegated result", "owner": "assistant"},
        request_id=f"delegate_{task_id}",
        idempotency_key=f"idem_{task_id}",
        correlation_id=f"corr_{task_id}",
        launch_turn_id=f"turn_{task_id}",
        launch_cause="scheduled_build",
    )


def _workflow_request(task_id: str) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-224201Z-E2F8"},
        summary="Run workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow digest", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )


def _deliver_mailbox_result(root: Path, *, task_id: str) -> dict:
    return correlation.reattach_results_for_turn(
        root,
        launch_turn_id=f"turn_{task_id}",
        current_turn_id=f"turn_{task_id}_review",
        writer="e2f8.reattach",
        mark_delivered=True,
    )


def test_completed_detached_job_assimilates_into_canonical_task_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    task_id = seeded["active_tasks"][0]["task_id"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.mailbox.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_e2f8", writer="e2f8.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_e2f8",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_complete",
        job_surface="workflow",
        writer="e2f8.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_complete",
        status="completed",
        summary="workflow finished",
        writer="e2f8.result",
        producer="test",
        result_id="job_result_e2f8_complete",
        evidence_refs=["runtime/artifacts/workflow_digest.json"],
        now_utc="2026-03-13T00:20:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    outcome = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    record = writeback.read_task_record(tmp_path, task_id=task_id)
    event_lines = (tmp_path / "runtime" / "state" / "lab_assistant" / "writeback" / "writeback_events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()

    assert outcome["task_outcome"]["reconciled_status"] == "completed"
    assert task["status"] == "completed"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "successful_assimilation"
    assert record["assimilation_status"] == "successful_assimilation"
    assert record["delegation_request_id"] == f"delegate_{task_id}"
    assert any("task_reconciled" in line for line in event_lines)


def test_queued_detached_job_persists_pending_assimilation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_pending")
    task_id = "task_e2f8_pending"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.pending.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_pending", writer="e2f8.pending.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_pending",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_pending",
        job_surface="workflow",
        writer="e2f8.pending.handoff",
    )

    writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.pending.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert task["status"] == "waiting_on_job"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "pending_assimilation"
    assert record["assimilation_status"] == "pending_assimilation"
    assert record["latest_delegation_status"] == "waiting_on_job"


def test_missing_evidence_assimilates_as_ambiguous_and_opens_blocked_loop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_ambiguous")
    task_id = "task_e2f8_ambiguous"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.workflow.bind",
        request=_workflow_request(task_id),
        launch_result={"workflow_id": "wf_e2f8_ambiguous"},
    )
    ref = bound["delegation_ref"]
    state_path = Path(ref["status_refs"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"workflow_id": "wf_e2f8_ambiguous", "status": "completed"}), encoding="utf-8")

    writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.workflow.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    blocked_loops = [loop for loop in task_register["open_loops"] if loop["linked_task_id"] == task_id and loop["status"] == "open"]
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert task["status"] == "interrupted_recovering"
    assert task["blocked_reason"]
    assert record["assimilation_status"] == "ambiguous_assimilation"
    assert any(loop["kind"] == "blocked_question" for loop in blocked_loops)


def test_duplicate_terminal_reconcile_is_idempotent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_duplicate")
    task_id = "task_e2f8_duplicate"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.duplicate.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_duplicate", writer="e2f8.duplicate.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_duplicate",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_duplicate",
        job_surface="workflow",
        writer="e2f8.duplicate.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_duplicate",
        status="completed",
        summary="done once",
        writer="e2f8.duplicate.result",
        producer="test",
        result_id="job_result_duplicate",
        now_utc="2026-03-13T00:22:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.duplicate.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.duplicate.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["task_outcome"]["reconciled_status"] == "completed"
    assert second["writeback_record"]["applied"] is False
    assert generation_after_first == generation_after_second
    assert record["attempt_count"] == 2


def test_weaker_runtime_noise_does_not_overwrite_successful_canonical_completion(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_precedence")
    task_id = "task_e2f8_precedence"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.precedence.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_precedence", writer="e2f8.precedence.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_precedence",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_precedence",
        job_surface="workflow",
        writer="e2f8.precedence.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_precedence",
        status="completed",
        summary="canonical success",
        writer="e2f8.precedence.result",
        producer="test",
        result_id="job_result_precedence",
        now_utc="2026-03-13T00:24:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)
    writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.precedence.writeback")

    lifecycle_path = jobs.job_path(tmp_path, "job_e2f8_precedence")
    current = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    current["state"] = "heartbeat_stale"
    current["state_reason"] = "simulated_weaker_runtime_noise"
    lifecycle_path.write_text(json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.precedence.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert task["status"] == "completed"
    assert record["assimilation_status"] == "successful_assimilation"
    assert record["decision_reason"] == "retained_stronger_canonical_terminal_truth"


def test_stronger_terminal_evidence_closes_prior_ambiguous_blocked_loop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_recover_truth")
    task_id = "task_e2f8_recover_truth"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.recover.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_recover_truth",
        writer="e2f8.recover.claim",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_recover_truth",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_recover_truth",
        job_surface="workflow",
        writer="e2f8.recover.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_recover_truth",
        status="completed",
        summary="completed and awaiting canonical delivery",
        writer="e2f8.recover.result.pending",
        producer="test",
        result_id="job_result_recover_truth_pending",
        evidence_refs=["runtime/artifacts/workflow_digest.json"],
        now_utc="2026-03-13T00:25:00Z",
    )

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.recover.writeback.pending")
    first_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    first_task = _find_task(first_snapshot, task_id)
    first_loops = [
        loop for loop in first_snapshot["open_loops"] if loop["linked_task_id"] == task_id and loop["status"] == "open"
    ]

    _deliver_mailbox_result(tmp_path, task_id=task_id)
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.recover.writeback.stronger")
    second_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    second_task = _find_task(second_snapshot, task_id)
    second_loops = [
        loop for loop in second_snapshot["open_loops"] if loop["linked_task_id"] == task_id and loop["status"] == "open"
    ]
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert first_task["status"] == "interrupted_recovering"
    assert any(loop["kind"] == "blocked_question" for loop in first_loops)
    assert second["task_outcome"]["reconciled_status"] == "completed"
    assert second_task["status"] == "completed"
    assert second_loops == []
    assert record["assimilation_status"] == "successful_assimilation"


def test_missing_task_records_failed_assimilation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)

    outcome = writeback.reconcile_task(tmp_path, task_id="task_missing_e2f8", writer="e2f8.missing.writeback")
    record = writeback.read_task_record(tmp_path, task_id="task_missing_e2f8")

    assert outcome["task_outcome"]["assimilation_status"] == "failed_assimilation"
    assert record["assimilation_status"] == "failed_assimilation"
    assert record["applied"] is False


def test_duplicate_terminal_job_result_replays_are_applied_once(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_replay")
    task_id = "task_e2f8_replay"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.replay.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_e2f8_replay", writer="e2f8.replay.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_e2f8_replay",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_replay",
        job_surface="workflow",
        writer="e2f8.replay.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_replay",
        status="completed",
        summary="duplicate delivery completed",
        writer="e2f8.replay.result",
        producer="test",
        result_id="job_result_replay_1",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_e2f8_replay.json"],
        now_utc="2026-03-13T02:20:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.replay.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])

    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_replay",
        status="completed",
        summary="duplicate delivery completed",
        writer="e2f8.replay.result.dup",
        producer="test",
        result_id="job_result_replay_1",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_e2f8_replay.json"],
        now_utc="2026-03-13T02:20:30Z",
    )
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.replay.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["writeback_record"]["applied"] is True
    assert second["writeback_record"]["applied"] is False
    assert generation_after_first == generation_after_second
    assert record["attempt_count"] == 2
    assert first["task_outcome"]["reconciled_status"] == "completed"
    assert second["task_outcome"]["reconciled_status"] == "completed"


def test_replayed_mailbox_claim_without_terminal_result_stays_pending(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_pending_replay")
    task_id = "task_e2f8_pending_replay"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.pending.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_e2f8_pending",
        writer="e2f8.pending.claim",
        lease_s=10,
        now_utc="2026-03-13T03:00:00Z",
    )["request_record"]
    mailbox.recover_expired_claims(
        tmp_path,
        writer="e2f8.pending.recover",
        now_utc="2026-03-13T03:00:20Z",
    )
    replayed = mailbox.replay_request(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        writer="e2f8.pending.replay",
        reason="lease_expired_retry",
    )
    claimed_again = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_e2f8_pending_retry",
        writer="e2f8.pending.claim_again",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_e2f8_pending_retry",
        claim_id=str(claimed_again["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_pending_replay",
        job_surface="workflow",
        writer="e2f8.pending.handoff",
    )

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.pending.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.pending.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)

    assert replayed["request_record"]["state"] == "replayed"
    assert first["task_outcome"]["assimilation_status"] == "pending_assimilation"
    assert first["writeback_record"]["applied"] is True
    assert second["writeback_record"]["applied"] is False
    assert task["status"] == "waiting_on_job"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "pending_assimilation"
    assert generation_after_first == generation_after_second


def test_duplicate_terminal_job_result_with_different_id_is_idempotent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_replay_diff_id")
    task_id = "task_e2f8_replay_diff_id"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.diffid.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_e2f8_diffid",
        writer="e2f8.diffid.claim",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_e2f8_diffid",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_diff_id",
        job_surface="workflow",
        writer="e2f8.diffid.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_diff_id",
        status="completed",
        summary="duplicate idempotent",
        writer="e2f8.diffid.result",
        producer="test",
        result_id="job_result_replay_diff_id_v1",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_e2f8_diff_id.json"],
        now_utc="2026-03-13T03:10:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.diffid.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])

    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_diff_id",
        status="completed",
        summary="duplicate idempotent",
        writer="e2f8.diffid.result.dup",
        producer="test",
        result_id="job_result_replay_diff_id_v2",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_e2f8_diff_id.json"],
        now_utc="2026-03-13T03:10:30Z",
    )
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.diffid.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["writeback_record"]["applied"] is True
    assert second["writeback_record"]["applied"] is False
    assert first["task_outcome"]["reconciled_status"] == "completed"
    assert second["task_outcome"]["reconciled_status"] == "completed"
    assert generation_after_first == generation_after_second
    assert record["attempt_count"] == 2


def test_internal_synthetic_scheduler_helper_is_retired_after_successful_terminal_writeback(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_e2f8_internal_helper")
    task_id = "task_e2f8_internal_helper"
    renamed = register.upsert_task(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="e2f8.synthetic.rename",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "[Codex Scheduled Controller Contract] - You are the Codex executor of record for a scheduled write-capable job.",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-224201Z-E2F8",
            "next_expected_action": "wait for delegated result",
        },
    )["task_register"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(renamed["write_generation"]),
        writer="e2f8.synthetic.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_synth", writer="e2f8.synthetic.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_synth",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_e2f8_internal_helper",
        job_surface="workflow",
        writer="e2f8.synthetic.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_e2f8_internal_helper",
        status="completed",
        summary="workflow finished",
        writer="e2f8.synthetic.result",
        producer="test",
        result_id="job_result_e2f8_internal_helper",
        evidence_refs=["runtime/artifacts/workflow_digest.json"],
        now_utc="2026-03-13T00:20:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    outcome = writeback.reconcile_task(tmp_path, task_id=task_id, writer="e2f8.synthetic.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert all(task["task_id"] != task_id for task in task_register["active_tasks"])
    assert outcome["writeback_record"]["retired_from_active_tasks"] is True
    assert outcome["task_outcome"]["reconciled_status"] == "completed"
