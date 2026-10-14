from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from infra.webui import server_codex_programmatic_surface as codex_surface
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_turns as turns


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _seed_task(
    root: Path,
    *,
    task_id: str = "task_5437",
    ledger_uid: str = "PIS-20260312-161622Z-5437",
    title: str = "Recover delegated work",
) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="recovery.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": title,
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "reconcile delegated work after restart",
        },
    )["task_register"]


def _request() -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "recover workflow job",
            "preferred_surface": "workflow",
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
        }
    )
    req = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": "task_5437", "ledger_uid": "PIS-20260312-161622Z-5437"},
        summary="Recover workflow job",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow digest", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )
    req["request_id"] = "delegate_5437"
    return req


def _seed_bound_workflow(root: Path) -> dict:
    seeded = _seed_task(root)
    return bindings.bind_delegation_request(
        root,
        expected_generation=int(seeded["write_generation"]),
        writer="recovery.bind",
        request=_request(),
        launch_result={"workflow_id": "wf_5437"},
    )


def _seed_mailbox_detached_job(root: Path, *, task_id: str = "task_5437_detached", detached_job_id: str = "job_5437_detached") -> dict:
    seeded = _seed_task(root, task_id=task_id, title="Recover detached work")
    request = policy.build_typed_delegation_request(
        classification=policy.classify_answer_vs_delegate(
            {
                "summary": "recover detached job",
                "preferred_surface": "workflow",
                "estimated_duration_s": 300,
                "requires_new_evidence": True,
                "outlives_turn": True,
            }
        ),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260313-030601Z-V2W4"},
        summary="Recover detached work",
        requested_action="handoff detached work",
        expected_evidence=[{"kind": "job_record", "description": "detached job lifecycle", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review detached result", "owner": "assistant"},
        request_id=f"delegate_{task_id}",
        correlation_id=f"corr_{task_id}",
        launch_turn_id=f"turn_{task_id}",
        launch_cause="scheduled_build",
    )
    bound = bindings.bind_delegation_request(
        root,
        expected_generation=int(seeded["write_generation"]),
        writer="recovery.detached.bind",
        request=request,
        launch_result={},
    )
    mailbox.enqueue_request(root, request=request, writer="recovery.detached.enqueue")
    claimed = mailbox.claim_next_request(
        root,
        worker_id="worker_5437_detached",
        writer="recovery.detached.claim",
        lease_s=60,
        now_utc="2026-03-13T02:00:00Z",
    )["request_record"]
    mailbox.accept_claim_handoff(
        root,
        request_id=request["request_id"],
        worker_id="worker_5437_detached",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id=detached_job_id,
        job_surface="workflow",
        writer="recovery.detached.handoff",
    )
    jobs.heartbeat_job(
        root,
        detached_job_id=detached_job_id,
        writer="recovery.detached.heartbeat",
        worker_id="worker_5437_detached",
        progress_message="work in flight before restart",
        now_utc="2026-03-13T02:01:00Z",
    )
    return bound


def _simulate_unclean_restart(root: Path) -> None:
    host.bootstrap_host_baseline(root)
    host.bootstrap_host_baseline(root)


def test_reconcile_active_job_restart_reports_waiting_on_job(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    bound = _seed_bound_workflow(tmp_path)
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_5437", "status": "running"})

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    assistant_state = state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)["assistant_state"]
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert snapshot["status"] == "waiting_on_job"
    assert assistant_state["current_state"] == "waiting_on_job"
    assert task_register["active_tasks"][0]["status"] == "waiting_on_job"
    assert "delegated job(s) still running" in snapshot["summary"]


def test_reconcile_finished_job_restart_reports_completed_honestly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    original_state = state.load_or_init_assistant_state(tmp_path)
    bound = _seed_bound_workflow(tmp_path)
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_5437", "status": "completed"})
    _write_json(Path(ref["evidence_candidates"][0]), {"summary": "durable proof"})

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    assistant_state = state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)["assistant_state"]
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert snapshot["status"] == "ready"
    assert snapshot["counts"]["completed"] == 1
    assert assistant_state["assistant_id"] == original_state["assistant_id"]
    assert assistant_state["current_state"] == "idle"
    assert task_register["active_tasks"] == []
    assert task_register["completed_tasks"][0]["status"] == "completed"
    assert "finished while away" in snapshot["summary"]


def test_reconcile_handoff_without_lifecycle_recovers_as_interrupted_not_waiting_on_job(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path)
    request = policy.build_typed_delegation_request(
        classification=policy.classify_answer_vs_delegate(
            {
                "summary": "recover detached job",
                "preferred_surface": "workflow",
                "estimated_duration_s": 300,
                "requires_new_evidence": True,
                "outlives_turn": True,
            }
        ),
        surface="mailbox",
        owner_ref={"task_id": "task_5437", "ledger_uid": "PIS-20260312-161622Z-5437"},
        summary="Recover detached work",
        requested_action="handoff detached work",
        expected_evidence=[{"kind": "job_record", "description": "detached job lifecycle", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review detached result", "owner": "assistant"},
        request_id="delegate_5437_gap",
        correlation_id="corr_5437_gap",
        launch_turn_id="turn_5437_gap",
        launch_cause="scheduled_build",
    )
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="recovery.handoff_gap.bind",
        request=request,
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_5437_gap",
        writer="recovery.handoff_gap.claim",
        lease_s=60,
        now_utc="2026-03-13T02:00:00Z",
    )["request_record"]

    def _flaky_accept(*args, **kwargs):
        raise mailbox._job_lifecycle.DetachedJobLifecycleError("simulated_lifecycle_write_failure")

    monkeypatch.setattr(mailbox._job_lifecycle, "accept_mailbox_handoff", _flaky_accept)
    with pytest.raises(mailbox.MailboxError):
        mailbox.accept_claim_handoff(
            tmp_path,
            request_id=request["request_id"],
            worker_id="worker_5437_gap",
            claim_id=str(claimed["active_claim"]["claim_id"]),
            detached_job_id="wf_5437_gap",
            job_surface="workflow",
            writer="recovery.handoff_gap.handoff",
        )

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert snapshot["status"] != "waiting_on_job"
    assert task_register["active_tasks"] == []
    assert task_register["residue_tasks"][0]["status"] == "interrupted_recovering"


def test_reconcile_failed_job_restart_marks_blocked_and_open_loop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    bound = _seed_bound_workflow(tmp_path)
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_5437", "status": "failed"})

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    assistant_state = state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)["assistant_state"]
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert snapshot["status"] == "blocked"
    assert assistant_state["current_state"] == "blocked"
    assert task_register["active_tasks"] == []
    assert task_register["residue_tasks"][0]["status"] == "failed"
    assert any(loop["kind"] == "blocked_question" and loop["status"] == "open" for loop in task_register["open_loops"])


def test_reconcile_startup_runs_detached_job_boot_reconciliation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_mailbox_detached_job(tmp_path)

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    assistant_state = state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)["assistant_state"]
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    job_record = jobs.read_job(tmp_path, detached_job_id="job_5437_detached")

    assert snapshot["detached_job_reconciliation"]["ran"] is True
    assert snapshot["detached_job_reconciliation"]["changed_job_ids"] == ["job_5437_detached"]
    assert snapshot["status"] == "interrupted_recovering"
    assert assistant_state["current_state"] == "interrupted_recovering"
    assert task_register["active_tasks"] == []
    assert task_register["residue_tasks"][0]["status"] == "interrupted_recovering"
    assert job_record["state"] == "timed_out"
    assert "detached job(s) reconciled after restart" in snapshot["summary"]


def test_reconcile_startup_fails_closed_when_canonical_register_is_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register_events.jsonl"
    register_path.unlink()
    summary_path.unlink()
    events_path.unlink()

    snapshot = recovery.reconcile_startup(tmp_path)

    assert snapshot["status"] == "interrupted_recovering"
    assert snapshot["continuity_proven"] is False
    assert "task register missing after restart" in snapshot["surface_gaps"]
    assert "canonical task register missing; recovery refused to synthesize a blank register" in snapshot["surface_gaps"]
    assert not register_path.exists()


def test_reconcile_startup_fails_closed_when_canonical_register_is_unreadable_even_with_event_log(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, title="Unreadable canonical register after restart")

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    register_path.write_text("{bad\n", encoding="utf-8")
    _simulate_unclean_restart(tmp_path)

    snapshot = recovery.reconcile_startup(tmp_path)

    assert snapshot["status"] == "interrupted_recovering"
    assert snapshot["continuity_proven"] is False
    assert "task register unreadable after restart" in snapshot["surface_gaps"]
    assert "task register missing after restart" not in snapshot["surface_gaps"]
    assert "canonical task register missing; recovery refused to synthesize a blank register" not in snapshot["surface_gaps"]


def test_reconcile_startup_reconciles_nonterminal_direct_turns(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_turns", title="Track direct-turn restart status")
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "task_turns"},
        role="builder",
        message="in-flight before restart",
        writer="recovery.turns.test",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="recovery.turns.test",
        new_state="running",
        state_reason="work in progress",
    )

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    turn = turns.read_turn(tmp_path, turn_id=turn_id)

    assert snapshot["direct_turn_reconciliation"]["ran"] is True
    assert snapshot["direct_turn_reconciliation"]["changed_turn_ids"] == [turn_id]
    assert "direct turn(s) marked awaiting_resume after restart" in snapshot["summary"]
    assert turn["state"] == "awaiting_resume"


def test_reconcile_mixed_delegated_and_plain_tasks_preserves_both_in_register(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    bound = _seed_bound_workflow(tmp_path)
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_5437", "status": "completed"})
    _write_json(Path(ref["evidence_candidates"][0]), {"summary": "durable proof"})
    _seed_task(
        tmp_path,
        task_id="task_plain_recovery",
        ledger_uid="PIS-20260312-224201Z-E2F8",
        title="Keep plain recovery task",
    )

    _simulate_unclean_restart(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    tasks_by_id = {
        task["task_id"]: task
        for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
        for task in task_register[bucket_name]
    }

    assert snapshot["counts"]["completed"] == 1
    assert set(tasks_by_id) == {"task_5437", "task_plain_recovery"}
    assert tasks_by_id["task_5437"]["status"] == "completed"
    assert tasks_by_id["task_plain_recovery"]["status"] == "interrupted_recovering"


def test_duplicate_request_replay_reattaches_instead_of_launching_again(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    first = _seed_bound_workflow(tmp_path)

    second = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="recovery.duplicate",
        request=_request(),
        launch_result={"workflow_id": "wf_should_not_launch"},
    )
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    journal_lines = (tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "idempotency_journal.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()

    assert second["duplicate_suppressed"] is True
    assert task_register["active_tasks"][0]["delegation_refs"][0]["subsystem_id"] == "wf_5437"
    assert len(task_register["active_tasks"][0]["delegation_refs"]) == 1
    assert len(journal_lines) == 2
    assert "duplicate_reattached" in journal_lines[1]


def test_duplicate_request_replay_reattaches_from_journal_when_bind_crashed_before_register_write(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path)

    request = _request()
    provisional_ref = recovery.attach_idempotency_keys(
        bindings._build_binding_ref(  # type: ignore[attr-defined]
            tmp_path,
            request,
            {"workflow_id": "wf_crash_window", "native_status": "running"},
        ),
        request=request,
    )
    recovery.record_delegation_idempotency(
        tmp_path,
        request=request,
        status="launch_observed",
        writer="recovery.crash_window",
        delegation_ref=provisional_ref,
    )

    duplicate = recovery.find_duplicate_delegation(tmp_path, request=request, ignore_request_id="different_request")

    assert duplicate["duplicate"] is True
    assert duplicate["source"] == "idempotency_journal"
    assert duplicate["delegation_ref"]["subsystem_id"] == "wf_crash_window"


def test_partial_restore_recovery_fails_closed_with_durable_note(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, title="Survive partial restore")
    _write_json(
        tmp_path / "runtime" / "state" / "lab_assistant" / "host" / "restore_receipt.json",
        {
            "schema_version": "lab_assistant_host_restore_receipt_v1",
            "restored_utc": "2026-03-12T22:11:49Z",
            "source_manifest_sha256": "abc123",
            "partial_restore": True,
            "missing_files": ["runtime/state/lab_assistant/task_register/task_register.json"],
            "errors": ["missing_payload_file:runtime/state/lab_assistant/task_register/task_register.json"],
            "restored_file_count": 1,
            "note": "partial restore test",
        },
    )

    host.bootstrap_host_baseline(tmp_path)
    snapshot = recovery.reconcile_startup(tmp_path)
    assistant_state = state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)["assistant_state"]
    bootstrap_packet = __import__(
        "infra.webui.server_lab_assistant_bootstrap", fromlist=["build_fresh_turn_bootstrap"]
    ).build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert snapshot["status"] == "partial_restore"
    assert assistant_state["current_state"] == "blocked"
    assert snapshot["continuity_proven"] is False
    assert "partial restore detected" in snapshot["summary"]
    assert "partial restore detected" in bootstrap_packet["text"]


def test_missing_assistant_surfaces_fail_closed_instead_of_claiming_ready(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    snapshot = recovery.reconcile_startup(tmp_path)

    assert snapshot["status"] == "interrupted_recovering"
    assert snapshot["continuity_proven"] is False
    assert snapshot["assistant_state_present"] is False
    assert snapshot["task_register_present"] is False
    assert "assistant runtime state missing after restart" in snapshot["summary"]
    assert "task register missing after restart" in snapshot["summary"]


def test_reconcile_startup_repairs_missing_register_from_event_log_before_degrading(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    assistant_state = state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, title="Repair register from event log")

    register._register_path(tmp_path).unlink(missing_ok=True)
    register._summary_cache_path(tmp_path).unlink(missing_ok=True)
    _simulate_unclean_restart(tmp_path)

    snapshot = recovery.reconcile_startup(tmp_path)
    repaired_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert snapshot["task_register_present"] is True
    assert repaired_register["assistant_id_ref"] == assistant_state["assistant_id"]
    assert repaired_register["active_tasks"] == []
    assert repaired_register["residue_tasks"][0]["title"] == "Repair register from event log"
    assert "task register missing after restart" not in snapshot["summary"]


def test_reconcile_startup_rebuilds_public_surface_run_truth_and_snapshot_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)

    expected_surface_by_task: dict[str, str] = {}

    def _bind_surface(*, task_id: str, surface: str, launch_result: dict, ledger_uid: str) -> None:
        seeded = _seed_task(
            tmp_path,
            task_id=task_id,
            ledger_uid=ledger_uid,
            title=f"Recover {surface} surface truth",
        )
        request = policy.build_typed_delegation_request(
            classification={"classification": "delegate", "reason_codes": [f"recovery_surface_{surface}"]},
            surface=surface,
            owner_ref={"task_id": task_id, "ledger_uid": ledger_uid},
            summary=f"Recover {surface} surface",
            requested_action=f"launch {surface}",
            expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
            next_decision={"kind": "review_evidence", "summary": f"Review {surface} result", "owner": "assistant"},
            input_payload={"scope": "focused"},
            request_id=f"req_{task_id}",
            idempotency_key=f"idem_{task_id}",
            correlation_id=f"corr_{task_id}",
            launch_turn_id=f"turn_{task_id}",
        )
        bindings.bind_delegation_request(
            tmp_path,
            expected_generation=int(seeded["write_generation"]),
            writer=f"recovery.surface.{surface}",
            request=request,
            launch_result=launch_result,
        )
        expected_surface_by_task[task_id] = register._normalize_execution_surface(surface)

    delegated_cases = {
        "workflow": {"workflow_id": "wf_recovery_surface"},
        "execution_queue": {"run_id": "eq_recovery_surface"},
        "scheduler": {"job_id": "job_recovery_surface", "run_id": "sj_20260328T120000Z_surface"},
        "verifier": {
            "request_id": "vrf_recovery_surface",
            "workflow_id": "wf_recovery_surface_verifier",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.HostVerificationSentinelTest"},
        },
        "watchdog": {},
        "healer": {},
        "retrieval": {"ref_id": "retrieval_recovery_surface"},
        "admin": {"operation_id": "admin_recovery_surface"},
        "ledger": {"uid": "PIS-20260327-124630Z-RWVP"},
        "mailbox": {},
    }
    for index, (surface, launch_result) in enumerate(delegated_cases.items(), start=1):
        _bind_surface(
            task_id=f"task_recovery_surface_{index}",
            surface=surface,
            launch_result=launch_result,
            ledger_uid=f"PIS-20260328-81D2-SURFACE-{index:02d}",
        )

    _seed_task(
        tmp_path,
        task_id="task_recovery_direct_turn",
        ledger_uid="PIS-20260327-132825Z-66E0",
        title="Recover direct-turn surface truth",
    )
    turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "task_recovery_direct_turn"},
        role="builder",
        message="recover direct turn",
        writer="recovery.direct_turn",
    )
    expected_surface_by_task["task_recovery_direct_turn"] = "codex_worker"

    codex_seeded = _seed_task(
        tmp_path,
        task_id="task_recovery_codex",
        ledger_uid="PIS-20260327-132825Z-0EEC",
        title="Recover codex surface truth",
    )
    codex_surface.launch_codex_run(
        tmp_path,
        expected_generation=int(codex_seeded["write_generation"]),
        writer="recovery.codex_surface",
        task_id="task_recovery_codex",
        run_id="codex_recovery_run_001",
        execution_ref="governance/agents/runtime/workers_state.json#codex_recovery_run_001",
        binding_ref="ledger_uid::PIS-20260327-132825Z-0EEC::builder",
        job_uri="governance/agents/runtime/workers_state.json",
        trace_id="trace_codex_recovery_001",
    )
    expected_surface_by_task["task_recovery_codex"] = "codex_worker"

    register._register_path(tmp_path).unlink(missing_ok=True)
    register._summary_cache_path(tmp_path).unlink(missing_ok=True)
    _simulate_unclean_restart(tmp_path)

    recovery_snapshot = recovery.reconcile_startup(tmp_path)
    repaired_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    operator_snapshot = snapshot.build_operator_snapshot(tmp_path)

    repaired_by_id = {
        task["task_id"]: task
        for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
        for task in repaired_register[bucket_name]
    }
    projected_by_id = {task["task_id"]: task for task in operator_snapshot["execution_surface"]["tasks"]}

    assert recovery_snapshot["task_register_present"] is True
    assert set(expected_surface_by_task).issubset(set(repaired_by_id))
    assert set(expected_surface_by_task).issubset(set(projected_by_id))

    for task_id, expected_surface in expected_surface_by_task.items():
        repaired_task = repaired_by_id[task_id]
        current_run_id = str(repaired_task.get("current_run_id") or "")
        assert current_run_id
        run_row = next(row for row in repaired_task.get("task_runs") or [] if row.get("run_id") == current_run_id)
        assert run_row["surface"] == expected_surface
        assert run_row["execution_ref"]
        assert run_row["binding_ref"]
        assert run_row["revision"] >= 1

        projected = projected_by_id[task_id]
        assert projected["selected_surface"] == expected_surface
        assert projected["current_run_id"] == current_run_id
        assert projected["provenance"]["source"] == "BD05.task_register.task_runs[current_run_id]"


def test_restored_host_preserves_assistant_identity_and_active_task(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _init_repo(source)
    host.bootstrap_host_baseline(source)
    source_state = state.load_or_init_assistant_state(source)
    _seed_task(source, title="Restore active responsibility")
    export_receipt = host.export_host_baseline(source, note="5437 restore drill")
    manifest_path = Path(export_receipt["manifest_path"])
    payload_root = manifest_path.parent / "payload"
    shutil.copytree(payload_root, target)
    _write_json(
        target / "runtime" / "state" / "lab_assistant" / "host" / "restore_receipt.json",
        {
            "schema_version": "lab_assistant_host_restore_receipt_v1",
            "restored_utc": "2026-03-12T22:11:49Z",
            "source_manifest_path": str(manifest_path),
            "source_export_id": export_receipt["export_id"],
            "source_manifest_sha256": json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_sha256"],
            "partial_restore": False,
            "missing_files": [],
            "errors": [],
            "restored_file_count": export_receipt["file_count"],
            "note": "restore drill",
        },
    )
    _init_repo(target)
    host.bootstrap_host_baseline(target)
    snapshot = recovery.reconcile_startup(target)
    target_state = state.read_assistant_state_snapshot(target, initialize_if_missing=False)["assistant_state"]
    target_register = register.read_task_register_snapshot(target, initialize_if_missing=False)["task_register"]

    assert target_state["assistant_id"] == source_state["assistant_id"]
    assert target_register["assistant_id_ref"] == source_state["assistant_id"]
    assert target_register["active_tasks"] == []
    assert target_register["residue_tasks"][0]["task_id"] == "task_5437"
    assert snapshot["host_health"]["mode"] == "restored_host"
    assert snapshot["status"] == "interrupted_recovering"
    assert snapshot["continuity_proven"] is False
    assert target_state["current_state"] == "interrupted_recovering"
    assert target_register["active_tasks"] == []
    assert target_register["residue_tasks"][0]["status"] == "interrupted_recovering"
    assert "no durable delegation bindings" in snapshot["summary"]


def test_record_recovery_failure_persists_durable_blocked_note(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    snapshot = recovery.record_recovery_failure(
        tmp_path,
        writer="recovery.test",
        error=RuntimeError("boom"),
    )

    persisted = recovery.read_recovery_snapshot(tmp_path)
    assert snapshot["status"] == "blocked"
    assert persisted["status"] == "blocked"
    assert persisted["continuity_proven"] is False
    assert "Startup recovery failed" in persisted["summary"]
