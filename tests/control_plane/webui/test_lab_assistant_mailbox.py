from __future__ import annotations

import threading
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox


def _request(
    *,
    request_id: str = "mailbox_req_01",
    idempotency_key: str = "idem_mailbox_01",
) -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "delegate durable mailbox work",
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    return policy.build_typed_delegation_request(
        classification=classification,
        surface="mailbox",
        owner_ref={"task_id": "task_b6c4", "ledger_uid": "PIS-20260312-224201Z-B6C4"},
        summary="Deliver typed mailbox request",
        requested_action="queue mailbox work",
        expected_evidence=[{"kind": "mailbox_record", "description": "durable mailbox request", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review mailbox delivery state"},
        request_id=request_id,
        idempotency_key=idempotency_key,
        correlation_id=f"corr_{request_id}",
        launch_turn_id=f"turn_{request_id}",
        launch_cause="scheduled_build",
    )


def test_mailbox_claim_race_allows_only_one_worker_to_claim(tmp_path: Path) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="mailbox.test.enqueue")
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def _claim(worker_id: str) -> None:
        barrier.wait()
        results.append(
            mailbox.claim_next_request(
                tmp_path,
                worker_id=worker_id,
                writer=f"mailbox.test.{worker_id}",
                lease_s=60,
            )
        )

    first = threading.Thread(target=_claim, args=("worker_a",))
    second = threading.Thread(target=_claim, args=("worker_b",))
    first.start()
    second.start()
    first.join()
    second.join()

    claimed = [row for row in results if row.get("claimed")]
    rejected = [row for row in results if not row.get("claimed")]
    assert len(claimed) == 1
    assert len(rejected) == 1
    assert claimed[0]["request_record"]["state"] == "claimed"
    snapshot = mailbox.read_mailbox_snapshot(tmp_path)
    assert snapshot["counts"]["claimed"] == 1
    assert snapshot["counts"]["queued"] == 0


def test_mailbox_lease_expiry_replay_and_restart_honesty(tmp_path: Path) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="mailbox.test.enqueue")
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_expire",
        writer="mailbox.test.claim",
        lease_s=30,
        now_utc="2026-03-12T23:00:00Z",
    )
    assert claimed["claimed"] is True

    recovered = mailbox.recover_expired_claims(
        tmp_path,
        writer="mailbox.test.recover",
        now_utc="2026-03-12T23:01:00Z",
    )
    assert recovered["expired_request_ids"] == ["mailbox_req_01"]

    after_restart = mailbox.read_mailbox_snapshot(tmp_path, initialize_if_missing=False)
    assert after_restart["counts"]["lease_expired"] == 1
    expired = after_restart["requests"][0]
    assert expired["stale_claim_risk"] is True
    assert expired["ambiguous_delivery"] is True

    replayed = mailbox.replay_request(
        tmp_path,
        request_id="mailbox_req_01",
        writer="mailbox.test.replay",
        reason="lease_expired_retry",
    )
    assert replayed["request_record"]["state"] == "replayed"

    reclaimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_retry",
        writer="mailbox.test.claim_again",
        lease_s=60,
    )
    assert reclaimed["claimed"] is True
    assert reclaimed["request_record"]["claim_count"] == 2
    assert reclaimed["request_record"]["replay_count"] == 1


def test_mailbox_duplicate_suppression_uses_durable_idempotency_keys(tmp_path: Path) -> None:
    first = mailbox.enqueue_request(
        tmp_path,
        request=_request(request_id="mailbox_req_primary", idempotency_key="idem_shared"),
        writer="mailbox.test.enqueue1",
    )
    second = mailbox.enqueue_request(
        tmp_path,
        request=_request(request_id="mailbox_req_duplicate", idempotency_key="idem_shared"),
        writer="mailbox.test.enqueue2",
    )

    assert first["duplicate_suppressed"] is False
    assert second["duplicate_suppressed"] is True
    assert second["request_record"]["request_id"] == "mailbox_req_primary"
    assert "idempotency_key:idem_shared" in second["matched_keys"]
    snapshot = mailbox.read_mailbox_snapshot(tmp_path)
    assert snapshot["request_count"] == 1
    assert "idempotency_key:idem_shared" in snapshot["requests"][0]["idempotency_keys"]


def test_mailbox_dead_letter_blocks_replay_and_future_claims(tmp_path: Path) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="mailbox.test.enqueue", max_delivery_attempts=1)
    mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_dead",
        writer="mailbox.test.claim",
        lease_s=10,
        now_utc="2026-03-12T23:10:00Z",
    )
    mailbox.recover_expired_claims(
        tmp_path,
        writer="mailbox.test.recover",
        now_utc="2026-03-12T23:11:00Z",
    )
    dead = mailbox.replay_request(
        tmp_path,
        request_id="mailbox_req_01",
        writer="mailbox.test.replay",
        reason="retry_after_expiry",
    )

    assert dead["request_record"]["state"] == "dead_lettered"
    assert dead["request_record"]["dead_letter_reason"].startswith("delivery_attempts_exhausted:")
    with pytest.raises(mailbox.MailboxError, match="dead-lettered"):
        mailbox.replay_request(
            tmp_path,
            request_id="mailbox_req_01",
            writer="mailbox.test.replay_again",
            reason="should_fail",
        )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_other",
        writer="mailbox.test.claim_again",
        lease_s=60,
    )
    assert claimed["claimed"] is False


def test_mailbox_enqueue_rejects_invalid_typed_envelope_contract(tmp_path: Path) -> None:
    request_without_owner = _request()
    request_without_owner["owner_ref"] = {}
    with pytest.raises(
        policy.DelegationPolicyError,
        match="owner_ref requires at least one",
    ):
        mailbox.enqueue_request(
            tmp_path,
            request=request_without_owner,
            writer="mailbox.test.invalid_owner",
        )

    request_without_evidence = _request()
    request_without_evidence["expected_evidence"] = []
    with pytest.raises(
        policy.DelegationPolicyError,
        match="expected_evidence must declare at least one",
    ):
        mailbox.enqueue_request(
            tmp_path,
            request=request_without_evidence,
            writer="mailbox.test.invalid_evidence",
        )

    request_without_next_decision = _request()
    request_without_next_decision["next_decision"] = {}
    with pytest.raises(
        policy.DelegationPolicyError,
        match="next_decision kind",
    ):
        mailbox.enqueue_request(
            tmp_path,
            request=request_without_next_decision,
            writer="mailbox.test.invalid_decision",
        )

    snapshot = mailbox.read_mailbox_snapshot(tmp_path, initialize_if_missing=True)
    assert snapshot["request_count"] == 0


def test_mailbox_claim_handoff_marks_boundary_without_owning_job_lifecycle(tmp_path: Path) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="mailbox.test.enqueue")
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_job",
        writer="mailbox.test.claim",
        lease_s=60,
    )
    record = claimed["request_record"]
    handed_off = mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_01",
        worker_id="worker_job",
        claim_id=record["active_claim"]["claim_id"],
        detached_job_id="job_b6c4_01",
        job_surface="workflow",
        writer="mailbox.test.handoff",
    )

    assert handed_off["request_record"]["state"] == "handed_off"
    assert handed_off["request_record"]["handoff"]["detached_job_id"] == "job_b6c4_01"
    assert handed_off["job_lifecycle"]["job_record"]["state"] == "queued"
    assert jobs.read_job(tmp_path, detached_job_id="job_b6c4_01")["request_id"] == "mailbox_req_01"
    snapshot = mailbox.read_mailbox_snapshot(tmp_path)
    assert snapshot["counts"]["handed_off"] == 1


def test_mailbox_claim_handoff_retry_repairs_partial_failure_after_boundary_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="mailbox.test.enqueue")
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_retry",
        writer="mailbox.test.claim",
        lease_s=60,
    )["request_record"]

    real_accept = mailbox._job_lifecycle.accept_mailbox_handoff
    calls = {"count": 0}

    def _flaky_accept(repo_root: Path, *, request_record: dict, writer: str, now_utc: str = "") -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            raise mailbox._job_lifecycle.DetachedJobLifecycleError("simulated_lifecycle_write_failure")
        return real_accept(repo_root, request_record=request_record, writer=writer, now_utc=now_utc)

    monkeypatch.setattr(mailbox._job_lifecycle, "accept_mailbox_handoff", _flaky_accept)

    with pytest.raises(mailbox.MailboxError, match="simulated_lifecycle_write_failure"):
        mailbox.accept_claim_handoff(
            tmp_path,
            request_id="mailbox_req_01",
            worker_id="worker_retry",
            claim_id=claimed["active_claim"]["claim_id"],
            detached_job_id="job_retry_mailbox_01",
            job_surface="workflow",
            writer="mailbox.test.handoff",
        )

    stored_after_failure = mailbox._load_record(tmp_path, "mailbox_req_01")
    assert stored_after_failure["state"] == "handed_off"
    assert stored_after_failure["handoff"]["detached_job_id"] == "job_retry_mailbox_01"
    with pytest.raises(jobs.DetachedJobLifecycleError, match="detached job not found"):
        jobs.read_job(tmp_path, detached_job_id="job_retry_mailbox_01")

    recovered = mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_01",
        worker_id="worker_retry",
        claim_id=claimed["active_claim"]["claim_id"],
        detached_job_id="job_retry_mailbox_01",
        job_surface="workflow",
        writer="mailbox.test.handoff.retry",
    )

    assert calls["count"] == 2
    assert recovered["request_record"]["state"] == "handed_off"
    assert recovered["job_lifecycle"]["job_record"]["state"] == "queued"
    assert jobs.read_job(tmp_path, detached_job_id="job_retry_mailbox_01")["request_id"] == "mailbox_req_01"


def test_mailbox_claim_handoff_rejects_detached_job_id_collision_without_mutating_request(tmp_path: Path) -> None:
    mailbox.enqueue_request(
        tmp_path,
        request=_request(request_id="mailbox_req_first", idempotency_key="idem_first"),
        writer="mailbox.test.enqueue.first",
    )
    first_claim = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_first",
        writer="mailbox.test.claim.first",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_first",
        worker_id="worker_first",
        claim_id=first_claim["active_claim"]["claim_id"],
        detached_job_id="job_collision_b6c4",
        job_surface="workflow",
        writer="mailbox.test.handoff.first",
    )

    second_request = _request(request_id="mailbox_req_second", idempotency_key="idem_second")
    second_request["summary"] = "Deliver typed mailbox request second"
    second_request["requested_action"] = "queue mailbox work second"
    second_request["next_decision"] = {"kind": "review_evidence", "summary": "Review second mailbox delivery state"}
    mailbox.enqueue_request(
        tmp_path,
        request=second_request,
        writer="mailbox.test.enqueue.second",
    )
    second_claim = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_second",
        writer="mailbox.test.claim.second",
        lease_s=60,
    )["request_record"]

    with pytest.raises(mailbox.MailboxError, match="detached job id collision"):
        mailbox.accept_claim_handoff(
            tmp_path,
            request_id="mailbox_req_second",
            worker_id="worker_second",
            claim_id=second_claim["active_claim"]["claim_id"],
            detached_job_id="job_collision_b6c4",
            job_surface="workflow",
            writer="mailbox.test.handoff.second",
        )

    second_record = mailbox._load_record(tmp_path, "mailbox_req_second")
    assert second_record["state"] == "claimed"
    assert second_record["handoff"]["detached_job_id"] == ""
    assert second_record["handoff"]["job_surface"] == ""
    assert second_record["handoff"]["accepted_utc"] == ""
    assert second_record["handoff"]["worker_id"] == ""
    assert second_record["handoff"]["claim_id"] == ""
    assert jobs.read_job(tmp_path, detached_job_id="job_collision_b6c4")["request_id"] == "mailbox_req_first"
