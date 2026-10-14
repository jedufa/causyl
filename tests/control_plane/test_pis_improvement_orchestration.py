from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import pis_improvement_orchestration as orchestration
from infra.controlplane import pis_improvement_session_contract as contract


def _reference_examples() -> dict:
    return json.loads(Path("governance/pis_improvement_session_reference_examples_v1.json").read_text(encoding="utf-8"))


def _draft_session() -> dict:
    session = copy.deepcopy(_reference_examples()["examples"][0]["session_record"])
    session["workflow_state"] = {
        "status": "draft",
        "current_iteration": 0,
        "last_transition_utc": "2026-04-12T06:21:00Z",
    }
    session["revision_ledger"] = {
        "ledger_schema_version": contract.SESSION_SCHEMA_VERSION,
        "critique_rounds": [],
        "revision_deltas": [],
        "recommendation_history": [],
    }
    session["artifact_pointers"] = {
        "session_manifest": "runtime/test_pis_improvement/demo/session.json",
        "candidate_snapshot": "runtime/test_pis_improvement/demo/candidate_snapshot.json",
        "revision_ledger": "runtime/test_pis_improvement/demo/revision_ledger.json",
        "final_recommendation": "runtime/test_pis_improvement/demo/final_recommendation.md",
    }
    return session


def _round_payload() -> dict:
    return {
        "round_id": "round_001",
        "candidate_ref": "cand_child_contract",
        "critique_packet": {
            "summary": "Revision history must preserve every critique-to-resolution mapping explicitly.",
            "packet_provenance": {
                "source_pis_hash": "a" * 64,
                "cited_artifacts": [
                    "docs/contracts/contract_pis_improvement_session_v1.md",
                    "governance/pis_improvement_session_contract_v1.json",
                ],
            },
            "critique_items": [
                {
                    "critique_id": "critique_item_001",
                    "category": "resolution_gap",
                    "severity": "major",
                    "finding": "A later replay cannot prove which criticism was answered if the round only keeps a blob rewrite.",
                    "evidence": "The aggregate session must preserve linked critique and reviser packet payloads for each round.",
                    "proposed_resolution": "Store one round bundle with critique and reviser packets plus a durable revision delta ref.",
                }
            ],
            "overall_verdict": "revision_required",
        },
        "reviser_packet": {
            "summary": "Persisted a round bundle and aggregate rebuild path so each critique item still has a durable resolution record after interruption.",
            "resolution_items": [
                {
                    "critique_id": "critique_item_001",
                    "disposition": "addressed",
                    "response": "Round bundles now store the critique and reviser packets before the aggregate session is rewritten.",
                    "change_summary": "Recovery rebuilds the aggregate session from durable round records instead of losing completed work.",
                }
            ],
            "overall_status": "ready_for_review",
        },
        "proposed_revision_text": "--- a/demo\n+++ b/demo\n@@\n+durable improvement round bundle\n",
        "change_summary": "Persist deterministic round artifacts before aggregate session updates.",
    }


def _final_recommendation() -> dict:
    return {
        "recommendation_id": "recommendation_001",
        "outcome": "adopt_revision",
        "winning_candidate_ref": "cand_child_contract",
        "rationale_markdown": "# Final recommendation\n\nAdopt the persisted round-bundle path.\n",
    }


def test_orchestration_persists_round_artifacts_and_completed_session(tmp_path: Path) -> None:
    session = _draft_session()
    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )

    assert result["ok"] is True
    assert result["runtime_state"]["status"] == "completed"

    session_path = tmp_path / "runtime/test_pis_improvement/demo/session.json"
    round_record_path = tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001/round_record.json"
    candidate_snapshot_path = tmp_path / "runtime/test_pis_improvement/demo/candidate_snapshot.json"
    recommendation_path = tmp_path / "runtime/test_pis_improvement/demo/final_recommendation.md"

    assert session_path.exists()
    assert round_record_path.exists()
    assert candidate_snapshot_path.exists()
    assert recommendation_path.exists()

    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    validation = contract.validate_pis_improvement_session(persisted)
    assert validation["ok"] is True
    assert persisted["workflow_state"]["current_iteration"] == 1
    assert persisted["revision_ledger"]["recommendation_history"][0]["outcome"] == "adopt_revision"

    snapshot = json.loads(candidate_snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["deterministic_order"] == ["cand_child_contract", "cand_source_parent"]


def test_orchestration_persists_multiple_rounds_with_default_profiles(tmp_path: Path) -> None:
    session = _draft_session()
    session["iteration_policy"]["default_iteration_cap"] = 2

    round_one = _round_payload()
    round_two = _round_payload()
    round_two["round_id"] = "round_002"
    round_two["critique_packet"]["summary"] = "The second pass still needs explicit replay-safe provenance for the revised recommendation."
    round_two["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    round_two["critique_packet"]["critique_items"][0]["finding"] = "A second critique round should survive restart with its own durable record."
    round_two["critique_packet"]["critique_items"][0]["evidence"] = "Replay needs both rounds preserved in deterministic iteration order."
    round_two["critique_packet"]["critique_items"][0]["proposed_resolution"] = "Persist a second round bundle and keep the default profile ids embedded in aggregate session truth."
    round_two["reviser_packet"]["summary"] = "Recorded the second review pass without overriding the default profile linkage."
    round_two["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"
    round_two["reviser_packet"]["resolution_items"][0]["response"] = "The second round now persists through the same bundle-and-rebuild path."
    round_two["reviser_packet"]["resolution_items"][0]["change_summary"] = "Aggregate session truth retains both rounds with canonical reviewer and reviser profile ids."
    round_two["proposed_revision_text"] = "--- a/demo\n+++ b/demo\n@@\n+durable improvement round bundle second pass\n"
    round_two["change_summary"] = "Persist a second deterministic round bundle."

    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[round_one, round_two],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )

    assert result["ok"] is True
    assert result["runtime_state"]["status"] == "completed"
    assert result["runtime_state"]["current_iteration"] == 2

    session_path = tmp_path / "runtime/test_pis_improvement/demo/session.json"
    round_one_path = tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001/round_record.json"
    round_two_path = tmp_path / "runtime/test_pis_improvement/demo/rounds/round_002/round_record.json"

    assert round_one_path.exists()
    assert round_two_path.exists()

    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted["workflow_state"]["current_iteration"] == 2
    assert [row["round_id"] for row in persisted["revision_ledger"]["critique_rounds"]] == ["round_001", "round_002"]
    assert [row["round_id"] for row in persisted["revision_ledger"]["revision_deltas"]] == ["round_001", "round_002"]
    assert persisted["revision_ledger"]["critique_rounds"][0]["reviewer_profile_id"] == "reviewer_default"
    assert persisted["revision_ledger"]["critique_rounds"][1]["reviewer_profile_id"] == "reviewer_default"
    assert persisted["revision_ledger"]["revision_deltas"][0]["reviser_profile_id"] == "reviser_default"
    assert persisted["revision_ledger"]["revision_deltas"][1]["reviser_profile_id"] == "reviser_default"
    assert contract.validate_pis_improvement_session(persisted)["ok"] is True


def test_orchestration_fails_closed_on_invalid_reviser_packet(tmp_path: Path) -> None:
    session = _draft_session()
    bad_round = _round_payload()
    bad_round["reviser_packet"]["resolution_items"] = []

    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[bad_round],
        overwrite=True,
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_reviser_packet"

    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "invalid_reviser_packet"
    assert not (tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001/round_record.json").exists()


def test_orchestration_blocks_when_rounds_exceed_iteration_cap(tmp_path: Path) -> None:
    session = _draft_session()
    session["iteration_policy"]["default_iteration_cap"] = 1

    round_one = _round_payload()
    round_two = _round_payload()
    round_two["round_id"] = "round_002"
    round_two["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    round_two["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"

    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[round_one, round_two],
        overwrite=True,
    )

    assert result["ok"] is False
    assert result["error"] == "iteration_cap_exceeded"

    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["current_iteration"] == 1
    assert runtime_state["last_completed_round_id"] == "round_001"
    assert runtime_state["failure_code"] == "iteration_cap_exceeded"
    assert (tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001/round_record.json").exists()
    assert not (tmp_path / "runtime/test_pis_improvement/demo/rounds/round_002/round_record.json").exists()


def test_orchestration_rejects_duplicate_round_id_before_overwrite(tmp_path: Path) -> None:
    session = _draft_session()
    initial = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )
    assert initial["ok"] is True

    duplicate = _round_payload()
    duplicate["critique_packet"]["summary"] = "A duplicate round id should block instead of overwriting prior durable history."
    duplicate["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    duplicate["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"
    duplicate["reviser_packet"]["summary"] = "Attempted to reuse the same round id with different content."

    blocked = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        rounds=[duplicate],
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "duplicate_round_id"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "duplicate_round_id"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["round_id"] for row in persisted["revision_ledger"]["critique_rounds"]] == ["round_001"]


def test_orchestration_rejects_duplicate_delta_id_before_overwrite(tmp_path: Path) -> None:
    session = _draft_session()
    initial = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )
    assert initial["ok"] is True

    duplicate = _round_payload()
    duplicate["round_id"] = "round_002"
    duplicate["delta_id"] = "delta_001"
    duplicate["critique_packet"]["summary"] = "A duplicate delta id should block instead of creating ambiguous replay history."
    duplicate["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    duplicate["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"

    blocked = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        rounds=[duplicate],
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "duplicate_delta_id"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "duplicate_delta_id"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["delta_id"] for row in persisted["revision_ledger"]["revision_deltas"]] == ["delta_001"]


def test_orchestration_rejects_unsafe_round_id_path_component(tmp_path: Path) -> None:
    session = _draft_session()
    unsafe = _round_payload()
    unsafe["round_id"] = "../escaped"

    blocked = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[unsafe],
        overwrite=True,
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "invalid_round_id"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "invalid_round_id"
    assert not (tmp_path / "runtime/test_pis_improvement/escaped").exists()


def test_completed_session_rejects_additional_rounds(tmp_path: Path) -> None:
    session = _draft_session()
    completed = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )

    assert completed["ok"] is True

    extra_round = _round_payload()
    extra_round["round_id"] = "round_002"
    extra_round["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    extra_round["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"

    blocked = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        rounds=[extra_round],
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "session_already_completed"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["current_iteration"] == 1
    assert runtime_state["last_completed_round_id"] == "round_001"
    assert runtime_state["failure_code"] == "session_already_completed"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["round_id"] for row in persisted["revision_ledger"]["critique_rounds"]] == ["round_001"]


def test_rebuild_recovers_completed_round_after_aggregate_write_failure(tmp_path: Path, monkeypatch) -> None:
    session = _draft_session()
    original = orchestration._write_session_aggregate
    state = {"calls": 0}

    def _flaky_write(repo_root: Path, payload: dict) -> None:
        state["calls"] += 1
        if state["calls"] == 3:
            raise OSError("simulated aggregate rewrite failure")
        original(repo_root, payload)

    monkeypatch.setattr(orchestration, "_write_session_aggregate", _flaky_write)

    failed = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )

    assert failed["ok"] is False
    assert failed["error"] == "aggregate_persist_failed"
    assert (tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001/round_record.json").exists()

    monkeypatch.setattr(orchestration, "_write_session_aggregate", original)
    recovered = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert recovered["ok"] is True
    assert recovered["runtime_state"]["status"] == "completed"

    session_path = tmp_path / "runtime/test_pis_improvement/demo/session.json"
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted["workflow_state"]["current_iteration"] == 1
    assert persisted["revision_ledger"]["critique_rounds"][0]["round_id"] == "round_001"
    assert contract.validate_pis_improvement_session(persisted)["ok"] is True


def test_rebuild_blocks_on_incomplete_round_bundle(tmp_path: Path) -> None:
    session = _draft_session()
    materialized = orchestration.materialize_pis_improvement_session(session, repo_root=tmp_path, overwrite=True)

    assert materialized["ok"] is True

    round_dir = tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "round_record.json").write_text(
        json.dumps(
            {
                "schema_version": orchestration.ROUND_RECORD_SCHEMA_VERSION,
                "session_id": session["session_id"],
                "round_id": "round_001",
                "iteration_index": 1,
                "candidate_ref": "cand_child_contract",
                "recorded_at_utc": "2026-04-12T06:25:00Z",
                "critique_round": {
                    "round_id": "round_001",
                    "iteration_index": 1,
                    "candidate_ref": "cand_child_contract",
                    "reviewer_profile_id": "reviewer_default",
                    "critique_packet_ref": "runtime/test_pis_improvement/demo/rounds/round_001/critique_packet.json",
                    "critique_packet": _round_payload()["critique_packet"],
                    "status": "accepted",
                },
                "revision_delta": {
                    "delta_id": "delta_001",
                    "round_id": "round_001",
                    "reviser_profile_id": "reviser_default",
                    "proposed_revision_ref": "runtime/test_pis_improvement/demo/rounds/round_001/revision_delta.patch",
                    "reviser_packet": _round_payload()["reviser_packet"],
                    "change_summary": _round_payload()["change_summary"],
                },
            }
        ),
        encoding="utf-8",
    )

    blocked = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "incomplete_round_bundle"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "incomplete_round_bundle"


def test_round_record_is_written_last_and_partial_bundle_does_not_advance_iteration(tmp_path: Path, monkeypatch) -> None:
    session = _draft_session()
    original_write_json_atomic = orchestration.fsio.write_json_atomic

    def _flaky_write_json_atomic(path: Path, payload: dict) -> None:
        if path.name == "round_record.json":
            raise OSError("simulated round record write failure")
        original_write_json_atomic(path, payload)

    monkeypatch.setattr(orchestration.fsio, "write_json_atomic", _flaky_write_json_atomic)
    failed = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )

    assert failed["ok"] is False
    assert failed["error"] == "aggregate_persist_failed"
    round_dir = tmp_path / "runtime/test_pis_improvement/demo/rounds/round_001"
    assert (round_dir / "critique_packet.json").exists()
    assert (round_dir / "reviser_packet.json").exists()
    assert (round_dir / "revision_delta.patch").exists()
    assert not (round_dir / "round_record.json").exists()

    monkeypatch.setattr(orchestration.fsio, "write_json_atomic", original_write_json_atomic)
    recovered = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        rounds=[_round_payload()],
    )

    assert recovered["ok"] is True
    assert recovered["runtime_state"]["status"] == "resumable"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert persisted["workflow_state"]["current_iteration"] == 1
    assert [row["round_id"] for row in persisted["revision_ledger"]["critique_rounds"]] == ["round_001"]


def test_rebuild_keeps_recommendation_history_append_only_and_idempotent(tmp_path: Path) -> None:
    session = _draft_session()
    initial = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )

    assert initial["ok"] is True

    same_recommendation = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert same_recommendation["ok"] is True
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["recommendation_id"] for row in persisted["revision_ledger"]["recommendation_history"]] == ["recommendation_001"]

    second_recommendation = {
        "recommendation_id": "recommendation_002",
        "outcome": "keep_source",
        "winning_candidate_ref": "cand_child_contract",
        "rationale_markdown": "# Final recommendation\n\nKeep the source after a second deterministic decision.\n",
    }
    appended = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=second_recommendation,
    )

    assert appended["ok"] is True
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["recommendation_id"] for row in persisted["revision_ledger"]["recommendation_history"]] == [
        "recommendation_001",
        "recommendation_002",
    ]


def test_rebuild_blocks_conflicting_recommendation_reuse(tmp_path: Path) -> None:
    session = _draft_session()
    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )
    assert result["ok"] is True

    conflicting = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation={
            "recommendation_id": "recommendation_001",
            "outcome": "keep_source",
            "winning_candidate_ref": "cand_child_contract",
            "rationale_markdown": "# Final recommendation\n\nConflicting recommendation reuse.\n",
        },
    )

    assert conflicting["ok"] is False
    assert conflicting["error"] == "recommendation_history_conflict"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "recommendation_history_conflict"


def test_rebuild_fails_closed_when_final_recommendation_persist_fails(tmp_path: Path, monkeypatch) -> None:
    session = _draft_session()
    initial = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )
    assert initial["ok"] is True

    original = orchestration._write_final_recommendation_text

    def _broken_recommendation_writer(repo_root: Path, payload: dict, content: str) -> None:
        raise OSError("simulated recommendation write failure")

    monkeypatch.setattr(orchestration, "_write_final_recommendation_text", _broken_recommendation_writer)
    failed = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert failed["ok"] is False
    assert failed["error"] == "final_recommendation_persist_failed"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["current_iteration"] == 1
    assert runtime_state["last_completed_round_id"] == "round_001"
    assert runtime_state["failure_code"] == "final_recommendation_persist_failed"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert persisted["revision_ledger"]["recommendation_history"] == []

    monkeypatch.setattr(orchestration, "_write_final_recommendation_text", original)
    recovered = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert recovered["ok"] is True
    assert recovered["runtime_state"]["status"] == "completed"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert persisted["revision_ledger"]["critique_rounds"][0]["round_id"] == "round_001"
    assert [row["recommendation_id"] for row in persisted["revision_ledger"]["recommendation_history"]] == [
        "recommendation_001"
    ]


def test_rebuild_fails_closed_when_aggregate_persist_fails_during_finalization(tmp_path: Path, monkeypatch) -> None:
    session = _draft_session()
    initial = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        overwrite=True,
    )
    assert initial["ok"] is True

    original = orchestration._write_session_aggregate

    state = {"calls": 0}

    def _broken_aggregate_writer(repo_root: Path, payload: dict) -> None:
        state["calls"] += 1
        if state["calls"] >= 2:
            raise OSError("simulated rebuild aggregate failure")
        original(repo_root, payload)

    monkeypatch.setattr(orchestration, "_write_session_aggregate", _broken_aggregate_writer)
    failed = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert failed["ok"] is False
    assert failed["error"] == "aggregate_persist_failed"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["current_iteration"] == 1
    assert runtime_state["last_completed_round_id"] == "round_001"
    assert runtime_state["failure_code"] == "aggregate_persist_failed"
    assert (tmp_path / "runtime/test_pis_improvement/demo/final_recommendation.md").exists()
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert persisted["revision_ledger"]["recommendation_history"] == []

    monkeypatch.setattr(orchestration, "_write_session_aggregate", original)
    recovered = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        final_recommendation=_final_recommendation(),
    )

    assert recovered["ok"] is True
    assert recovered["runtime_state"]["status"] == "completed"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert persisted["revision_ledger"]["critique_rounds"][0]["round_id"] == "round_001"
    assert [row["recommendation_id"] for row in persisted["revision_ledger"]["recommendation_history"]] == [
        "recommendation_001"
    ]


def test_orchestration_blocks_new_rounds_after_completion(tmp_path: Path) -> None:
    session = _draft_session()
    result = orchestration.orchestrate_pis_improvement_session(
        session,
        repo_root=tmp_path,
        rounds=[_round_payload()],
        final_recommendation=_final_recommendation(),
        overwrite=True,
    )
    assert result["ok"] is True

    extra_round = _round_payload()
    extra_round["round_id"] = "round_002"
    extra_round["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    extra_round["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"
    blocked = orchestration.orchestrate_pis_improvement_session(
        tmp_path / "runtime/test_pis_improvement/demo/session.json",
        repo_root=tmp_path,
        rounds=[extra_round],
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "session_already_completed"
    runtime_state = json.loads((tmp_path / "runtime/test_pis_improvement/demo/runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "blocked"
    assert runtime_state["failure_code"] == "session_already_completed"
    persisted = json.loads((tmp_path / "runtime/test_pis_improvement/demo/session.json").read_text(encoding="utf-8"))
    assert [row["round_id"] for row in persisted["revision_ledger"]["critique_rounds"]] == ["round_001"]
