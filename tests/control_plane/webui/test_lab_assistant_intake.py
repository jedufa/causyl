from __future__ import annotations

import json
from pathlib import Path
import pytest

from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_intake as intake
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in list(register_obj.get(bucket_name) or []):
            if isinstance(task, dict) and task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def test_active_request_with_malformed_input_rejected_before_persistence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="",
    )
    snapshot = register.load_or_init_task_register(tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "malformed_input"
    assert "raw_request_text is required" in result["error"]
    assert snapshot["write_generation"] == current["write_generation"]
    assert snapshot["active_tasks"] == []


def test_active_request_with_missing_information_persists_blocked_intake_and_clarification_loop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Fix it.",
        force_active_work=True,
    )

    task_register = result["task_register"]
    task = _find_task(task_register, result["task_id"])
    loop = next(loop for loop in task_register["open_loops"] if loop["linked_task_id"] == result["task_id"])
    reloaded = register.load_or_init_task_register(tmp_path)

    assert result["persisted"] is True
    assert task["intake_path"] == "canonical"
    assert task["status"] == "blocked"
    assert task["intake"]["readiness"] == "clarification_needed"
    assert any(row["field"] == "scope_anchor" for row in task["intake"]["missing_information"])
    assert loop["kind"] == "waiting_on_operator"
    assert loop["status"] == "open"
    assert _find_task(reloaded, result["task_id"])["intake"]["readiness"] == "clarification_needed"


def test_canonical_intake_path_is_recorded_for_active_work_entrypoint(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Update infra/webui/server_lab_assistant_task_register.py to track canonical intake paths.",
    )

    task = next(task for task in result["task_register"]["active_tasks"] if task["task_id"] == result["task_id"])
    reloaded = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    persisted = next(task for task in reloaded["active_tasks"] if task["task_id"] == result["task_id"])

    assert task["source"] == "operator_intake"
    assert task["intake_path"] == "canonical"
    assert persisted["intake_path"] == "canonical"
    assert task["intake"]["schema_version"] == "lab_assistant_request_intake_v1"


def test_duplicate_active_request_is_idempotent_and_reuses_canonical_task_id(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    raw = "Update infra/webui/server_lab_assistant_task_register.py to tighten canonical intake persistence."
    first = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text=raw,
        force_active_work=True,
        ledger_uid="PIS-20260326-203500Z-B8E2",
    )
    second = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="intake.test.retry",
        raw_request_text=raw,
        force_active_work=True,
        ledger_uid="PIS-20260326-203500Z-B8E2",
    )

    final = register.load_or_init_task_register(tmp_path)
    by_task_id = [task for task in final["active_tasks"] if task["task_id"] == first["task_id"]]
    same_id_matches = [task for task in final["active_tasks"] if task["task_id"] == second["task_id"]]

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["task_id"] == second["task_id"]
    assert len(by_task_id) == 1
    assert len(same_id_matches) == 1
    assert first["task_id"] == same_id_matches[0]["task_id"]
    assert by_task_id[0]["request_fingerprint"] == same_id_matches[0]["request_fingerprint"]


def test_duplicate_active_request_is_idempotent_under_request_time_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    stamps = [
        "2026-03-27T04:00:00Z",
        "2026-03-27T04:00:05Z",
        "2026-03-27T04:00:10Z",
    ]
    idx = 0

    def drifted_utc_now() -> str:
        nonlocal idx
        value = stamps[min(idx, len(stamps) - 1)]
        idx += 1
        return value

    monkeypatch.setattr(intake, "_utc_now_iso", drifted_utc_now)

    raw = "Update infra/webui/server_lab_assistant_task_register.py to tighten canonical intake metadata."
    first = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text=raw,
        force_active_work=True,
        ledger_uid="PIS-20260326-203500Z-B8E2",
    )
    second = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="intake.test.replay",
        raw_request_text=raw,
        force_active_work=True,
        ledger_uid="PIS-20260326-203500Z-B8E2",
    )
    final = register.load_or_init_task_register(tmp_path)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["task_id"] == second["task_id"]
    matching_tasks = [task for task in final["active_tasks"] if task["task_id"] == first["task_id"]]
    first_task = next(task for task in first["task_register"]["active_tasks"] if task["task_id"] == first["task_id"])
    second_task = next(task for task in second["task_register"]["active_tasks"] if task["task_id"] == second["task_id"])
    assert first_task["request_fingerprint"] == second_task["request_fingerprint"]
    assert len(matching_tasks) == 1
    assert matching_tasks[0]["request_fingerprint"] == first_task["request_fingerprint"]


def test_canonical_task_write_failure_does_not_mark_intake_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    def fail_write(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated task register write failure")

    monkeypatch.setattr(register, "_write_json_atomic", fail_write)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Update infra/webui/server_lab_assistant_intake.py to enforce canonical intake IDs.",
        force_active_work=True,
        ledger_uid="PIS-20260326-203500Z-B8E2",
    )

    snapshot = register.load_or_init_task_register(tmp_path)
    assert result["ok"] is False
    assert result["persisted"] is False
    assert result["reason"] == "intake_capture_failed"
    assert str(result.get("error") or "").startswith("simulated task register write failure")
    assert snapshot["write_generation"] == current["write_generation"]
    assert snapshot["active_tasks"] == []


def test_casual_chat_does_not_graduate_into_durable_active_work(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Thanks for the update.",
    )
    snapshot_now = register.load_or_init_task_register(tmp_path)

    assert result["persisted"] is False
    assert result["reason"] == "casual_chat"
    assert snapshot_now["write_generation"] == current["write_generation"]
    assert snapshot_now["active_tasks"] == []


def test_specific_request_is_not_misclassified_as_clarification_needed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Update infra/webui/server_lab_assistant_task_register.py to persist structured intake metadata and add targeted tests.",
    )

    task = next(task for task in result["task_register"]["active_tasks"] if task["task_id"] == result["task_id"])
    assert task["status"] == "active"
    assert task["intake"]["readiness"] == "good_enough_to_execute_directly"
    assert task["intake"]["missing_information"] == []


def test_bootstrap_and_operator_snapshot_surface_persisted_intake_summary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Implement durable structured intake capture for lab assistant requests across the task register and fresh-turn bootstrap.",
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == result["task_id"])

    assert "intake=" in packet["text"]
    assert task["intake"]["readiness"] in {"good_enough_to_plan", "good_enough_to_execute_directly"}
    assert task["intake"]["summary"]


def test_operator_snapshot_dedupes_duplicate_task_rows_for_same_ledger_uid(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    current["active_tasks"] = [
        {
            "task_id": "task_f84f_154917z",
            "title": "Close IS-11 Intent Boundary and Family Acceptance",
            "ledger_uid": "PIS-20260409-154917Z-F84F",
            "kind": "software",
            "owner": "assistant",
            "source": "scheduler",
            "status": "active",
            "updated_utc": "2026-04-09T17:42:08Z",
            "created_utc": "2026-04-09T17:22:40Z",
            "next_expected_action": "finish canonical closeout",
            "blocked_reason": "waiting on canonical closeout",
            "delegation_refs": [],
            "task_runs": [],
            "intake": {
                "raw_request_text": "Close IS-11 Intent Boundary and Family Acceptance",
                "inferred_intent": "Close IS-11 Intent Boundary and Family Acceptance",
                "work_class": "software",
                "work_state": "active_work",
                "readiness": "good_enough_to_execute_directly",
                "summary": "canonical closeout task",
            },
        },
        {
            "task_id": "task_intake_scheduled_advisory_backend_request_you_are_an_ad_c4e6536cfb",
            "title": "[Scheduled Advisory Backend Request] You are an advisory backend called by a Codex executor.",
            "ledger_uid": "PIS-20260409-154917Z-F84F",
            "kind": "software",
            "owner": "assistant",
            "source": "operator_intake",
            "status": "active",
            "updated_utc": "2026-04-09T17:30:23Z",
            "created_utc": "2026-04-09T17:30:23Z",
            "next_expected_action": "shadow advisory row",
            "blocked_reason": "",
            "delegation_refs": [],
            "task_runs": [],
            "intake": {
                "raw_request_text": "You are an advisory backend called by a Codex executor.",
                "inferred_intent": "Advisory backend request",
                "work_class": "software",
                "work_state": "active_work",
                "readiness": "good_enough_to_execute_directly",
                "summary": "shadow advisory row",
            },
        },
    ]
    task_register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    task_register_path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    operator = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-09T17:42:30Z")

    assert not any(task["task_id"].startswith("task_intake_") for task in operator["active_tasks"] + operator["completed_tasks"])
    canonical = [task for task in operator["active_tasks"] if task["ledger_uid"] == "PIS-20260409-154917Z-F84F"]
    assert len(canonical) == 1
    assert canonical[0]["task_id"] == "task_f84f_154917z"
    assert canonical[0]["status"] == "active"


def test_bootstrap_and_operator_snapshot_surface_scope_contract_summary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    persisted = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Update infra/webui/server_lab_assistant_task_register.py to record durable assumptions and scope changes.",
    )
    scoped = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(persisted["task_register"]["write_generation"]),
        writer="scope.test",
        task_id=persisted["task_id"],
        assumptions=[
            {
                "assumption_id": "assume_targeted_surface",
                "statement": "Only the task register and read-side summaries need code changes for this child.",
                "kind": "inferred",
                "approval_state": "provisional",
                "operator_approved": False,
                "requires_operator_confirmation": True,
                "blocking": False,
                "source": "assistant_inference",
                "rationale": "The PIS explicitly excludes execution routing and acceptance targets.",
            }
        ],
        scope_revision={
            "revision_id": "scope_summary_surface",
            "summary": "Expose assumption status in bootstrap and operator snapshot.",
            "in_scope": ["bootstrap text", "operator snapshot"],
            "out_of_scope": ["execution routing"],
            "rationale": "Operator-visible summaries are in scope for J5B2.",
            "source": "assistant",
        },
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == persisted["task_id"])

    assert "assumptions=1" in packet["text"]
    assert "scope=Expose assumption status in bootstrap and ope" in packet["text"]
    assert task["scope_contract"]["provisional_assumption_count"] == 1
    assert task["scope_contract"]["current_scope_summary"] == "Expose assumption status in bootstrap and operator snapshot."
    assert scoped["task_register"]["write_generation"] > persisted["task_register"]["write_generation"]


def test_bootstrap_and_operator_snapshot_surface_acceptance_and_review_boundaries(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    persisted = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Update the task register to capture durable acceptance targets, proof requirements, and operator review gates.",
    )
    accepted = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(persisted["task_register"]["write_generation"]),
        writer="acceptance.test",
        task_id=persisted["task_id"],
        acceptance_targets=[
            {
                "target_id": "acceptance_visible",
                "statement": "Acceptance targets are visible without chat replay.",
                "target_type": "operator_surface",
                "source": "operator",
                "rationale": "Operators need inspectable planning truth.",
                "active": True,
            }
        ],
        proof_targets=[
            {
                "proof_id": "proof_targeted_tests",
                "statement": "Targeted tests prove persistence and review gates.",
                "proof_class": "test",
                "required": True,
                "blocking": True,
                "status": "pending",
                "source": "operator",
                "rationale": "Required local proof stays pending until run.",
            }
        ],
        review_gates=[
            {
                "gate_id": "gate_operator",
                "statement": "Operator approval is required before closeout.",
                "reviewer_role": "operator",
                "applies_at": "before_closeout",
                "status": "pending",
                "blocking": True,
                "source": "operator",
                "rationale": "Human review boundary must persist.",
            }
        ],
        completion_state={
            "implementation_complete": True,
            "evidence_complete": False,
            "operator_approved": False,
            "summary": "Implementation landed; proof and operator approval remain pending.",
        },
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == persisted["task_id"])

    assert "done=implementation_complete" in packet["text"]
    assert "review_pending=1" in packet["text"]
    assert task["acceptance_contract"]["done_stage"] == "implementation_complete"
    assert task["acceptance_contract"]["pending_proof_target_count"] == 1
    assert task["acceptance_contract"]["pending_review_gate_count"] == 1
    assert accepted["task_register"]["write_generation"] > persisted["task_register"]["write_generation"]


def test_bootstrap_and_operator_snapshot_surface_decomposition_summary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022103Z-L7D4"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)

    persisted = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Break this larger planning task into milestones and checkpoints and keep the rationale durable.",
        ledger_uid=ledger_uid,
        force_active_work=True,
    )
    scoped = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(persisted["task_register"]["write_generation"]),
        writer="scope.test",
        task_id=persisted["task_id"],
        assumptions=[
            {
                "assumption_id": "assume_live_plan_in_bd05",
                "statement": "The live plan summary belongs in BD05.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "requires_operator_confirmation": False,
                "blocking": False,
                "confirmed_by": "operator",
                "source": "operator",
                "rationale": "The planning substrate is already defined.",
            }
        ],
    )
    accepted = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(scoped["task_register"]["write_generation"]),
        writer="acceptance.test",
        task_id=persisted["task_id"],
        acceptance_targets=[
            {
                "target_id": "target_visible_plan_shape",
                "statement": "The operator can inspect milestone/checkpoint state without opening the transcript.",
                "target_type": "operator_surface",
                "source": "operator",
                "rationale": "Readable planning state is part of the contract.",
                "active": True,
            }
        ],
        completion_state={
            "implementation_complete": False,
            "evidence_complete": False,
            "operator_approved": False,
            "summary": "Acceptance target exists before execution.",
        },
    )
    register.update_task_decomposition(
        tmp_path,
        expected_generation=int(accepted["task_register"]["write_generation"]),
        writer="decomp.test",
        task_id=persisted["task_id"],
        mode="milestone_plan",
        rationale="This request spans plan persistence and operator visibility, so a flat task would be misleading.",
        decision={
            "estimated_step_count": 4,
            "risk_flags": ["durability", "operator_surface"],
            "requires_operator_checkpoint": True,
            "needs_ordering": True,
            "decision_summary": "Split by persistence and projection boundaries.",
        },
        linked_assumption_ids=["assume_live_plan_in_bd05"],
        linked_acceptance_target_ids=["target_visible_plan_shape"],
        active_milestone_id="milestone_persist",
        active_checkpoint_id="checkpoint_contract",
        dependency_mapping={
            "fanout_policy": "serial_only",
            "summary": "Projection stays serial because it shares the same operator-facing read surface.",
            "milestones": [
                {
                    "milestone_id": "milestone_persist",
                    "depends_on": [],
                    "execution_mode": "serial_only",
                    "reason_codes": ["root_prerequisite"],
                    "reason_summary": "Persistence work has to establish the source of truth first.",
                    "caution_flags": ["unsafe_to_parallelize"],
                    "shared_resource_keys": [],
                },
                {
                    "milestone_id": "milestone_project",
                    "depends_on": ["milestone_persist"],
                    "execution_mode": "serial_only",
                    "reason_codes": ["shared_resource"],
                    "reason_summary": "Projection reuses the same operator-facing read surface, so it stays serial.",
                    "caution_flags": ["shared_resource"],
                    "shared_resource_keys": ["infra/webui/server_lab_assistant_operator_snapshot.py"],
                },
            ],
        },
        milestones=[
            {
                "milestone_id": "milestone_persist",
                "title": "Persist the planning contract",
                "rationale": "The register and ledger artifact have to exist before summaries make sense.",
                "exit_criteria": ["Compact and detailed decomposition state is durable."],
                "assumption_ids": ["assume_live_plan_in_bd05"],
                "acceptance_target_ids": ["target_visible_plan_shape"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_contract",
                        "title": "Write decomposition state",
                        "rationale": "The plan shape needs a durable checkpoint anchor.",
                        "exit_criteria": ["Task row and ledger artifact agree on the current plan."],
                        "assumption_ids": ["assume_live_plan_in_bd05"],
                        "acceptance_target_ids": ["target_visible_plan_shape"],
                    }
                ],
            },
            {
                "milestone_id": "milestone_project",
                "title": "Project the plan into operator views",
                "rationale": "The operator should not have to read raw artifact JSON.",
                "exit_criteria": ["Bootstrap and snapshot summarize the split."],
                "assumption_ids": ["assume_live_plan_in_bd05"],
                "acceptance_target_ids": ["target_visible_plan_shape"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_snapshot",
                        "title": "Expose decomposition summary",
                        "rationale": "Read-side summaries need explicit counts and active pointers.",
                        "exit_criteria": ["Operator snapshot shows milestones and checkpoints."],
                        "assumption_ids": ["assume_live_plan_in_bd05"],
                        "acceptance_target_ids": ["target_visible_plan_shape"],
                    }
                ],
            },
        ],
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == persisted["task_id"])

    assert "plan=milestone_plan" in packet["text"]
    assert "milestones=2" in packet["text"]
    assert "edges=1" in packet["text"]
    assert "forced_serial=2" in packet["text"]
    assert task["decomposition_contract"]["milestone_count"] == 2
    assert task["decomposition_contract"]["checkpoint_count"] == 2
    assert task["decomposition_contract"]["dependency_edge_count"] == 1
    assert task["decomposition_contract"]["fanout_policy"] == "serial_only"
    assert task["decomposition_contract"]["active_checkpoint_id"] == "checkpoint_contract"
    assert task["decomposition_contract"]["artifact_ref"].endswith(".json")


def test_bootstrap_and_operator_snapshot_surface_unsettled_replanning_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    ledger_uid = "PIS-20260313-022105Z-N9F6"
    (tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / f"{ledger_uid}__sample-item").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)

    persisted = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="intake.test",
        raw_request_text="Revise the current plan when restart evidence proves the old approach is stale.",
        ledger_uid=ledger_uid,
        force_active_work=True,
    )
    planned = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(persisted["task_register"]["write_generation"]),
        writer="decomp.test",
        task_id=persisted["task_id"],
        mode="direct",
        rationale="The first pass looks small enough for one direct implementation slice.",
        decision={
            "estimated_step_count": 2,
            "risk_flags": [],
            "requires_operator_checkpoint": False,
            "needs_ordering": False,
            "decision_summary": "Start direct until stronger evidence arrives.",
        },
        direct_summary="One direct plan before restart proof lands.",
    )
    invalidated = register.invalidate_task_decomposition(
        tmp_path,
        expected_generation=int(planned["task_register"]["write_generation"]),
        writer="decomp.test",
        task_id=persisted["task_id"],
        replanning_state="degraded",
        summary="Restart evidence invalidated the direct plan and a replacement is not settled yet.",
        trigger={
            "kind": "changed_evidence",
            "summary": "Reload proof showed the direct path would present stale truth after restart.",
            "evidence_refs": ["artifact:restart-proof-gap"],
        },
        next_expected_action="settle a replacement plan before resuming execution",
        blocked_reason="Direct plan invalidated by restart evidence.",
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == persisted["task_id"])
    register_task = next(task for task in invalidated["task_register"]["active_tasks"] if task["task_id"] == persisted["task_id"])
    invalidated_revision_id = register_task["decomposition_contract"]["replanning"]["invalidated_revision_id"]

    assert "replan=degraded" in packet["text"]
    assert "replan_summary=Restart evidence invalidated the direct plan" in packet["text"]
    assert "plan=direct" not in packet["text"]
    assert task["decomposition_contract"]["replanning_state"] == "degraded"
    assert task["decomposition_contract"]["replanning_summary"] == (
        "Restart evidence invalidated the direct plan and a replacement is not settled yet."
    )
    assert task["decomposition_contract"]["invalidated_revision_id"] == invalidated_revision_id
    assert task["decomposition_contract"]["invalidated_plan_version"] == 1
    assert task["decomposition_contract"]["current_revision_id"] == ""
    assert task["decomposition_contract"]["mode"] == ""
    assert task["decomposition_contract"]["plan_version"] == 0
    assert task["decomposition_contract"]["artifact_ref"] == ""
    assert task["decomposition_contract"]["active_milestone_id"] == ""
    assert task["decomposition_contract"]["active_checkpoint_id"] == ""
