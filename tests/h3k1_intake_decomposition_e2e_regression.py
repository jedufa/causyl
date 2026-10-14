"""End-to-end regression suite for H3K1 intake and decomposition workstream.

Tests the combined behavior of:
- I4A1: Structured request intake, clarification state, missing-information detection
- J5B2: Assumption, constraint, and scope-negotiation recording contract
- K6C3: Acceptance-target, proof-target, and review-boundary capture
- L7D4: Work decomposition, milestone, and checkpoint design contract
- M8E5: Serial-vs-parallel dependency mapping and safe fanout rules
- N9F6: Replanning trigger, plan revision, and rationale trail contract
- P1G7: Planning-to-execution handoff and rationale preservation contract

This suite proves durable intake capture, clarification blocking, assumption recording,
acceptance-target persistence, and decomposition planning as one coherent system.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_intake as intake
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


pytestmark = [pytest.mark.regression, pytest.mark.e2e, pytest.mark.integration]


def _init_repo(root: Path) -> None:
    """Initialize repository structure for testing."""
    (root / "governance" / "ledger" / "items").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def _create_minimal_ledger_item(root: Path) -> str:
    """Create a minimal ledger item for decomposition artifact storage and return its UID.

    Decomposition artifacts require ledger binding. This helper creates a minimal
    test PIS item structure.
    """
    # Generate a valid PIS UID matching ledger_storage.validate_uid():
    # PIS-YYYYMMDD-HHMMSSZ-XXXX
    now = datetime.datetime.now(datetime.timezone.utc)
    hex_suffix = uuid.uuid4().hex[:4].upper()
    ledger_uid = f"PIS-{now.strftime('%Y%m%d-%H%M%S')}Z-{hex_suffix}"
    # Ledger items are stored in governance/ledger/patch_instruction_sheets/ subdirectory
    item_dir = root / "governance" / "ledger" / "items" / f"{ledger_uid}__test-decomposition-artifact"
    item_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal pis.md for the item
    pis_content = f"""# Test PIS Item

**UID:** {ledger_uid}

## Title
Test Decomposition Item for H3K1 Regression Suite

## Goal
Provide ledger binding for decomposition artifact storage in test harness.
"""
    (item_dir / "pis.md").write_text(pis_content)
    return ledger_uid


def _bootstrap(root: Path) -> None:
    """Bootstrap assistant state and host baseline."""
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _seed_h3k1_contracts_and_plan(root: Path, *, task_id_suffix: str) -> dict:
    """Seed intake + scope + acceptance + decomposition for H3K1 integration checks."""
    current = register.load_or_init_task_register(root)
    ledger_uid = _create_minimal_ledger_item(root)
    intake_result = intake.persist_structured_request_intake(
        root,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text=f"Implement H3K1 planning flow {task_id_suffix} with durable decomposition and handoff.",
        force_active_work=True,
        ledger_uid=ledger_uid,
    )
    scoped = register.update_task_scope_contract(
        root,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        assumptions=[
            {
                "assumption_id": f"assume_scope_{task_id_suffix}",
                "statement": "Planning state remains canonical in BD05 task register.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "requires_operator_confirmation": False,
                "blocking": False,
                "confirmed_by": "operator",
                "source": "operator",
                "rationale": "H3K1 contract requires durable task truth.",
            }
        ],
    )
    accepted = register.update_task_acceptance_contract(
        root,
        expected_generation=int(scoped["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        acceptance_targets=[
            {
                "target_id": f"target_h3k1_{task_id_suffix}",
                "statement": "Combined H3K1 planning contract is durable and inspectable.",
                "target_type": "operator_surface",
                "source": "operator",
                "rationale": "Acceptance requires inspectable planning state.",
                "active": True,
            }
        ],
        proof_targets=[
            {
                "proof_id": f"proof_h3k1_{task_id_suffix}",
                "statement": "Targeted regression tests prove I4A1 through P1G7 behavior.",
                "proof_class": "test",
                "status": "pending",
                "required": True,
                "blocking": True,
                "source": "operator",
                "rationale": "Proof target is explicit in H3K1 flow.",
            }
        ],
    )
    planned = register.update_task_decomposition(
        root,
        expected_generation=int(accepted["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        mode="milestone_plan",
        rationale="Split persistence and projection milestones to keep ordering and rationale explicit.",
        decision={
            "estimated_step_count": 4,
            "risk_flags": ["durability", "operator_surface"],
            "requires_operator_checkpoint": True,
            "needs_ordering": True,
            "decision_summary": "Decompose before execution handoff.",
        },
        linked_assumption_ids=[f"assume_scope_{task_id_suffix}"],
        linked_acceptance_target_ids=[f"target_h3k1_{task_id_suffix}"],
        active_milestone_id="milestone_persist",
        active_checkpoint_id="checkpoint_contract",
        dependency_mapping={
            "fanout_policy": "serial_only",
            "summary": "Projection depends on persistence and remains serial.",
            "milestones": [
                {
                    "milestone_id": "milestone_persist",
                    "depends_on": [],
                    "execution_mode": "serial_only",
                    "reason_codes": ["root_prerequisite"],
                    "reason_summary": "Persistence is the root prerequisite.",
                    "caution_flags": ["unsafe_to_parallelize"],
                    "shared_resource_keys": [],
                },
                {
                    "milestone_id": "milestone_project",
                    "depends_on": ["milestone_persist"],
                    "execution_mode": "serial_only",
                    "reason_codes": ["shared_resource"],
                    "reason_summary": "Projection reuses the same task truth surface.",
                    "caution_flags": ["shared_resource"],
                    "shared_resource_keys": ["runtime/state/lab_assistant/task_register/task_register.json"],
                },
            ],
        },
        milestones=[
            {
                "milestone_id": "milestone_persist",
                "title": "Persist planning contract",
                "rationale": "Task truth must be durable before projection.",
                "exit_criteria": ["Planning fields are durable."],
                "assumption_ids": [f"assume_scope_{task_id_suffix}"],
                "acceptance_target_ids": [f"target_h3k1_{task_id_suffix}"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_contract",
                        "title": "Write decomposition state",
                        "rationale": "Checkpoint anchors current revision state.",
                        "exit_criteria": ["Task register row and artifact agree."],
                        "assumption_ids": [f"assume_scope_{task_id_suffix}"],
                        "acceptance_target_ids": [f"target_h3k1_{task_id_suffix}"],
                    }
                ],
            },
            {
                "milestone_id": "milestone_project",
                "title": "Project planning state",
                "rationale": "Operator snapshot should expose milestone/checkpoint truth.",
                "exit_criteria": ["Operator snapshot shows the active plan."],
                "assumption_ids": [f"assume_scope_{task_id_suffix}"],
                "acceptance_target_ids": [f"target_h3k1_{task_id_suffix}"],
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint_snapshot",
                        "title": "Expose plan summary",
                        "rationale": "Read-side summary must match canonical task truth.",
                        "exit_criteria": ["Bootstrap and snapshot include plan counts."],
                        "assumption_ids": [f"assume_scope_{task_id_suffix}"],
                        "acceptance_target_ids": [f"target_h3k1_{task_id_suffix}"],
                    }
                ],
            },
        ],
    )
    return {"ledger_uid": ledger_uid, "task_id": intake_result["task_id"], "task_register": planned["task_register"]}


# ============================================================================
# I4A1: Structured Request Intake and Clarification State Tests
# ============================================================================


def test_i4a1_raw_request_captures_into_structured_intake(tmp_path: Path) -> None:
    """Test that raw operator request is captured as structured intake record."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Implement a feature to handle user authentication with JWT tokens.",
        force_active_work=True,
    )

    assert result["ok"] is True
    assert result["persisted"] is True
    assert result["intake"]["raw_request_text"] == "Implement a feature to handle user authentication with JWT tokens."
    assert result["intake"]["work_state"] == "active_work"
    assert result["intake"]["inferred_intent"]
    assert result["intake"]["work_class"] in {"software", "lab_ops", "experiment", "materials", "general"}


def test_i4a1_missing_information_blocks_clarification_needed_state(tmp_path: Path) -> None:
    """Test that requests with missing required info block into clarification_needed state."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Fix it.",
        force_active_work=True,
    )

    task_register = result["task_register"]
    task = next(t for t in task_register["active_tasks"] if t["task_id"] == result["task_id"])
    loop = next(l for l in task_register["open_loops"] if l["linked_task_id"] == result["task_id"])

    assert task["status"] == "blocked"
    assert task["intake"]["readiness"] == "clarification_needed"
    assert len(task["intake"]["missing_information"]) > 0
    assert loop["kind"] == "waiting_on_operator"
    assert loop["status"] == "open"


def test_i4a1_specific_request_proceeds_without_clarification(tmp_path: Path) -> None:
    """Test that specific requests don't require clarification."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Add a test for the new JWT authentication feature in tests/auth/test_jwt_tokens.py",
    )

    task_register = result["task_register"]
    task = next(t for t in task_register["active_tasks"] if t["task_id"] == result["task_id"])

    assert task["status"] == "active"
    assert task["intake"]["readiness"] in {"good_enough_to_execute_directly", "good_enough_to_plan"}
    assert len(task["intake"]["missing_information"]) == 0


def test_i4a1_casual_chat_does_not_create_durable_work(tmp_path: Path) -> None:
    """Test that casual chat doesn't escalate to durable active work."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Thanks for the help!",
    )

    assert result["persisted"] is False
    assert result["reason"] == "casual_chat"
    snapshot_after = register.load_or_init_task_register(tmp_path)
    assert snapshot_after["write_generation"] == current["write_generation"]


def test_i4a1_clarification_needed_state_survives_restart(tmp_path: Path) -> None:
    """Test that clarification_needed state persists across fresh turns."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    # First intake: creates blocked task
    result1 = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Build something.",
        force_active_work=True,
    )

    # Simulate restart: reload task register fresh
    reloaded = register.load_or_init_task_register(tmp_path)
    reloaded_task = next(t for t in reloaded["active_tasks"] if t["task_id"] == result1["task_id"])

    assert reloaded_task["status"] == "blocked"
    assert reloaded_task["intake"]["readiness"] == "clarification_needed"
    assert len(reloaded_task["intake"]["missing_information"]) > 0


# ============================================================================
# J5B2: Assumption and Scope-Negotiation Recording Tests
# ============================================================================


def test_j5b2_explicit_assumptions_are_recorded_durably(tmp_path: Path) -> None:
    """Test that explicit assumptions can be recorded and persisted."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    # Intake
    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Implement API endpoint for user authentication.",
    )

    # Scope contract with assumptions
    scope_result = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        assumptions=[
            {
                "assumption_id": "assume_jwt_only",
                "statement": "JWT is the only authentication method required for MVP.",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "confirmed_by": "operator",
                "requires_operator_confirmation": False,
                "blocking": False,
                "source": "operator_input",
                "rationale": "MVP scope explicitly limits auth to JWT only.",
            }
        ],
    )

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(t for t in reloaded["active_tasks"] if t["task_id"] == intake_result["task_id"])

    assert "scope_contract" in task
    assert len(task["scope_contract"]["assumptions"]) > 0
    assumption = task["scope_contract"]["assumptions"][0]
    assert assumption["statement"] == "JWT is the only authentication method required for MVP."
    assert assumption["approval_state"] == "operator_confirmed"


def test_j5b2_provisional_assumptions_are_surfaced_for_confirmation(tmp_path: Path) -> None:
    """Test that provisional (inferred) assumptions are marked for operator confirmation."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Add caching to the database query layer.",
    )

    scope_result = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        assumptions=[
            {
                "assumption_id": "infer_redis_cache",
                "statement": "Redis will be used for caching.",
                "kind": "inferred",
                "approval_state": "provisional",
                "operator_approved": False,
                "requires_operator_confirmation": True,
                "blocking": False,
                "source": "assistant_inference",
                "rationale": "Redis is the standard caching solution in the codebase.",
            }
        ],
    )

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(t for t in reloaded["active_tasks"] if t["task_id"] == intake_result["task_id"])

    assumption = task["scope_contract"]["assumptions"][0]
    assert assumption["kind"] == "inferred"
    assert assumption["approval_state"] == "provisional"
    assert assumption["requires_operator_confirmation"] is True
    assert assumption["operator_approved"] is False


# ============================================================================
# K6C3: Acceptance-Target and Proof-Target Capture Tests
# ============================================================================


def test_k6c3_acceptance_targets_are_persisted_durably(tmp_path: Path) -> None:
    """Test that acceptance targets (definition of done) are captured and persisted."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Implement JWT authentication endpoint.",
    )

    acceptance_result = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        acceptance_targets=[
            {
                "target_id": "api_endpoint_exists",
                "statement": "POST /auth/token endpoint exists and accepts username+password",
                "target_type": "execution_result",
                "blocking": True,
                "source": "operator",
                "rationale": "JWT endpoint is core to the implementation",
                "active": True,
            }
        ],
        proof_targets=[
            {
                "proof_id": "auth_integration_test",
                "statement": "Integration test verifies JWT generation and token validation",
                "proof_class": "test",
                "status": "pending",
                "required": True,
                "blocking": True,
                "source": "operator",
                "rationale": "Integration tests are required for JWT functionality",
            }
        ],
    )

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(t for t in reloaded["active_tasks"] if t["task_id"] == intake_result["task_id"])

    assert "acceptance_contract" in task
    assert len(task["acceptance_contract"]["acceptance_targets"]) > 0
    assert len(task["acceptance_contract"]["proof_targets"]) > 0
    target = task["acceptance_contract"]["acceptance_targets"][0]
    assert target["target_id"] == "api_endpoint_exists"
    assert target["statement"]
    assert target["active"] is True


def test_k6c3_operator_review_gates_are_recorded(tmp_path: Path) -> None:
    """Test that operator review gates are captured for high-risk work."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Update database schema migration scripts.",
    )

    acceptance_result = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        review_gates=[
            {
                "gate_id": "schema_review",
                "statement": "Operator must review schema changes before migration",
                "status": "pending",
                "blocking": True,
                "applies_at": "before_execution",
                "source": "operator",
                "rationale": "Schema migrations require careful operator review for safety",
            }
        ],
    )

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(t for t in reloaded["active_tasks"] if t["task_id"] == intake_result["task_id"])

    assert "acceptance_contract" in task
    assert len(task["acceptance_contract"]["review_gates"]) > 0
    gate = task["acceptance_contract"]["review_gates"][0]
    assert gate["status"] == "pending"
    assert gate["gate_id"] == "schema_review"


# ============================================================================
# L7D4: Work Decomposition Tests
# ============================================================================


def test_l7d4_work_can_be_decomposed_into_milestones(tmp_path: Path) -> None:
    """Test that work can be decomposed into milestones with rationale."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    # Create ledger item for decomposition artifact storage
    ledger_uid = _create_minimal_ledger_item(tmp_path)

    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Implement full user authentication system with JWT, OAuth, and SAML support.",
        ledger_uid=ledger_uid,
    )

    # Create decomposition with milestones
    decomp_result = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        mode="milestone_plan",
        rationale="Work requires multiple milestones: JWT foundation, then OAuth integration",
        decision={"decomposition_type": "milestone_based", "num_milestones": 2, "estimated_step_count": 5},
        milestones=[
            {
                "milestone_id": "jwt_foundation",
                "title": "JWT token generation and validation foundation",
                "description": "Establish core JWT token lifecycle management",
                "prerequisites": [],
                "exit_criteria": ["JWT endpoint live", "Tests passing"],
                "rationale": "JWT is the simplest and most critical auth method",
                "checkpoints": [
                    {
                        "checkpoint_id": "jwt_cp_1",
                        "title": "JWT token generation",
                        "exit_criteria": ["JWT generation working"],
                        "description": "Implement JWT token generation",
                        "rationale": "Foundation for JWT support",
                    }
                ],
            },
            {
                "milestone_id": "oauth_support",
                "title": "OAuth provider integration",
                        "exit_criteria": ["OAuth working"],
                "description": "Add OAuth2 provider support on top of JWT foundation",
                "prerequisites": [],
                "exit_criteria": ["OAuth endpoint live", "Integration tests passing"],
                "rationale": "OAuth builds on JWT foundation but requires separate implementation",
                "checkpoints": [
                    {
                        "checkpoint_id": "oauth_cp_1",
                        "title": "OAuth provider integration",
                        "exit_criteria": ["OAuth working"],
                        "description": "Integrate OAuth on top of JWT",
                        "rationale": "Second auth method",
                    }
                ],
            },
        ],
    )

    reloaded = register.load_or_init_task_register(tmp_path)
    task = next(t for t in reloaded["active_tasks"] if t["task_id"] == intake_result["task_id"])

    assert "decomposition_contract" in task
    assert task["decomposition_contract"]["mode"] == "milestone_plan"
    assert len(task["decomposition_contract"]["milestones"]) == 2
    milestone = task["decomposition_contract"]["milestones"][0]
    assert milestone["title"]
    assert milestone["rationale"]
    assert milestone["exit_criteria"]


# ============================================================================
# M8E5: Dependency Mapping and Safe Fanout Rules
# ============================================================================


def test_m8e5_dependency_mapping_is_durable_and_serial_safe(tmp_path: Path) -> None:
    """Test that dependency mapping persists and serial safety is explicit."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_h3k1_contracts_and_plan(tmp_path, task_id_suffix="m8e5")

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    operator = snapshot.build_operator_snapshot(tmp_path)
    task = next(task for task in operator["active_tasks"] if task["task_id"] == seeded["task_id"])

    assert "edges=1" in packet["text"]
    assert "forced_serial=2" in packet["text"]
    assert task["decomposition_contract"]["dependency_edge_count"] == 1
    assert task["decomposition_contract"]["fanout_policy"] == "serial_only"
    assert task["decomposition_contract"]["forced_serial_count"] == 2


# ============================================================================
# N9F6: Replanning Trigger and Revision Invalidations
# ============================================================================


def test_n9f6_replanning_invalidation_clears_current_plan_and_surfaces_degraded_state(tmp_path: Path) -> None:
    """Test that evidence-driven replanning invalidates the current plan durably."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_h3k1_contracts_and_plan(tmp_path, task_id_suffix="n9f6")

    invalidated = register.invalidate_task_decomposition(
        tmp_path,
        expected_generation=int(seeded["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=seeded["task_id"],
        replanning_state="degraded",
        summary="Restart evidence invalidated the current milestone plan.",
        trigger={
            "kind": "changed_evidence",
            "summary": "Reload evidence proves the old plan would publish stale truth.",
            "evidence_refs": ["artifact:restart-proof-gap"],
        },
        next_expected_action="settle replacement decomposition before execution handoff",
        blocked_reason="Current plan invalidated by restart evidence.",
    )

    task_register = invalidated["task_register"]
    task = next(task for task in task_register["active_tasks"] if task["task_id"] == seeded["task_id"])
    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)

    assert task["status"] == "degraded"
    assert task["decomposition_contract"]["replanning"]["state"] == "degraded"
    assert task["decomposition_contract"]["current_revision_id"] == ""
    assert task["decomposition_contract"]["mode"] == ""
    assert task["decomposition_contract"]["plan_version"] == 0
    assert "replan=degraded" in packet["text"]


# ============================================================================
# P1G7: Planning-to-Execution Handoff Packaging
# ============================================================================


def test_p1g7_planning_handoff_publishes_artifact_with_rationale_and_dependencies(tmp_path: Path) -> None:
    """Test that planning handoff package is durable and execution-ready."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_h3k1_contracts_and_plan(tmp_path, task_id_suffix="p1g7")

    published = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(seeded["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=seeded["task_id"],
    )["handoff"]
    read_back = register.read_task_planning_handoff(tmp_path, task_id=seeded["task_id"])

    assert published["schema_version"] == register.TASK_PLANNING_HANDOFF_SCHEMA_VERSION
    assert published["launch_readiness"]["launch_ready"] is True
    assert published["launch_readiness"]["state"] == "ready"
    assert published["planning_payload"]["decomposition"]["dependency_edges"] == [
        {"from_milestone_id": "milestone_persist", "to_milestone_id": "milestone_project"}
    ]
    assert published["planning_payload"]["rationale_trail"]["current_revision"]["revision_id"]
    assert Path(tmp_path / published["artifact_ref"]).exists()
    assert read_back["source"] == "artifact"


# ============================================================================
# H3K1 End-to-End Integration: Complete Planning Flow
# ============================================================================


def test_h3k1_complete_planning_flow_with_intake_assumptions_and_acceptance(tmp_path: Path) -> None:
    """Test complete H3K1 flow: intake -> assumptions -> acceptance -> decomposition."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    # Create ledger item for decomposition artifact storage
    ledger_uid = _create_minimal_ledger_item(tmp_path)

    # ---- I4A1: Intake ----
    intake_result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="h3k1.test",
        raw_request_text="Implement multi-method authentication (JWT, OAuth) for the API.",
        ledger_uid=ledger_uid,
    )
    assert intake_result["persisted"] is True

    # ---- J5B2: Assumptions ----
    scope_result = register.update_task_scope_contract(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        assumptions=[
            {
                "assumption_id": "jwt_foundation",
                "statement": "JWT implementation can be completed independently",
                "kind": "inferred",
                "approval_state": "provisional",
                "operator_approved": False,
                "requires_operator_confirmation": True,
                "blocking": False,
                "source": "assistant_inference",
                "rationale": "JWT can be a standalone implementation before OAuth integration",
            },
            {
                "assumption_id": "oauth_provider_ready",
                "statement": "OAuth provider credentials will be available",
                "kind": "explicit",
                "approval_state": "operator_confirmed",
                "operator_approved": True,
                "confirmed_by": "operator",
                "requires_operator_confirmation": False,
                "blocking": False,
                "source": "operator",
                "rationale": "Operator confirmed OAuth provider availability",
            },
        ],
    )

    # ---- K6C3: Acceptance Targets ----
    acceptance_result = register.update_task_acceptance_contract(
        tmp_path,
        expected_generation=int(scope_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        acceptance_targets=[
            {
                "target_id": "jwt_endpoint",
                "statement": "JWT token endpoint is live and validated",
                "target_type": "execution_result",
                "blocking": True,
                "source": "operator",
                "rationale": "JWT is core auth requirement",
                "active": True,
            },
            {
                "target_id": "oauth_endpoint",
                "statement": "OAuth endpoint is live and validated",
                "target_type": "execution_result",
                "blocking": True,
                "source": "operator",
                "rationale": "OAuth is required auth method",
                "active": True,
            },
        ],
        proof_targets=[
            {
                "proof_id": "jwt_tests",
                "statement": "JWT integration tests pass",
                "proof_class": "test",
                "status": "pending",
                "required": True,
                "blocking": True,
                "source": "operator",
                "rationale": "Tests prove JWT functionality",
            },
            {
                "proof_id": "oauth_tests",
                "statement": "OAuth integration tests pass",
                "proof_class": "test",
                "status": "pending",
                "required": True,
                "blocking": True,
                "source": "operator",
                "rationale": "Tests prove OAuth functionality",
            },
        ],
        review_gates=[
            {
                "gate_id": "security_review",
                "statement": "Security team must review token handling",
                "status": "pending",
                "blocking": True,
                "applies_at": "before_execution",
                "source": "operator",
                "rationale": "Auth system requires security review",
            }
        ],
    )

    # ---- L7D4: Decomposition ----
    decomp_result = register.update_task_decomposition(
        tmp_path,
        expected_generation=int(acceptance_result["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=intake_result["task_id"],
        mode="milestone_plan",
        rationale="Work requires two sequential milestones: JWT foundation then OAuth",
        decision={"decomposition_type": "milestone_based", "num_milestones": 2, "estimated_step_count": 5},
        milestones=[
            {
                "milestone_id": "jwt_foundation",
                "title": "JWT Foundation Implementation",
                "description": "Core JWT token lifecycle",
                "prerequisites": [],
                "exit_criteria": ["JWT endpoint live", "Tests passing"],
                "rationale": "JWT is prerequisite for OAuth integration",
                "checkpoints": [
                    {
                        "checkpoint_id": "jwt_cp_1",
                        "title": "JWT implementation",
                        "exit_criteria": ["JWT endpoint ready"],
                        "description": "Implement JWT token generation and validation",
                        "rationale": "Core JWT functionality",
                    }
                ],
            },
            {
                "milestone_id": "oauth_integration",
                "title": "OAuth Provider Integration",
                "description": "OAuth2 support layered on JWT",
                "prerequisites": [],
                "exit_criteria": ["OAuth endpoint live", "Tests passing"],
                "rationale": "OAuth builds on JWT base",
                "checkpoints": [
                    {
                        "checkpoint_id": "oauth_cp_1",
                        "title": "OAuth integration",
                        "exit_criteria": ["OAuth endpoint ready"],
                        "description": "Integrate OAuth provider on top of JWT",
                        "rationale": "OAuth support after JWT foundation",
                    }
                ],
            },
        ],
    )

    # Final verification: reload and check all pieces are present and connected
    final_register = register.load_or_init_task_register(tmp_path)
    final_task = next(t for t in final_register["active_tasks"] if t["task_id"] == intake_result["task_id"])

    # I4A1: Intake exists
    assert final_task["intake"]["raw_request_text"]
    assert final_task["intake"]["readiness"] in register.TASK_INTAKE_READINESS_STATES

    # J5B2: Assumptions recorded
    assert len(final_task["scope_contract"]["assumptions"]) == 2
    assert any(a["approval_state"] == "provisional" for a in final_task["scope_contract"]["assumptions"])
    assert any(a["approval_state"] == "operator_confirmed" for a in final_task["scope_contract"]["assumptions"])

    # K6C3: Acceptance targets recorded
    assert len(final_task["acceptance_contract"]["acceptance_targets"]) == 2
    assert len(final_task["acceptance_contract"]["proof_targets"]) == 2
    assert len(final_task["acceptance_contract"]["review_gates"]) == 1

    # L7D4: Decomposition with milestones
    assert final_task["decomposition_contract"]["mode"] == "milestone_plan"
    assert len(final_task["decomposition_contract"]["milestones"]) == 2

    # Bootstrap and operator snapshot should surface the plan
    bootstrap_packet = bootstrap.build_fresh_turn_bootstrap(tmp_path)
    assert "intake=" in bootstrap_packet["text"]

    operator_snapshot = snapshot.build_operator_snapshot(tmp_path)
    task_snapshot = next(t for t in operator_snapshot["active_tasks"] if t["task_id"] == intake_result["task_id"])
    assert task_snapshot["intake"]


def test_h3k1_complete_i4a1_to_p1g7_flow_includes_replanning_and_handoff_boundaries(tmp_path: Path) -> None:
    """Test full I4A1→P1G7 chain, including degraded replanning truth and launch gating."""
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_h3k1_contracts_and_plan(tmp_path, task_id_suffix="full_chain")

    published_ready = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(seeded["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=seeded["task_id"],
    )["handoff"]
    assert published_ready["launch_readiness"]["launch_ready"] is True

    invalidated = register.invalidate_task_decomposition(
        tmp_path,
        expected_generation=int(register.load_or_init_task_register(tmp_path)["write_generation"]),
        writer="h3k1.test",
        task_id=seeded["task_id"],
        replanning_state="blocked",
        summary="Replacement planning prerequisites are incomplete.",
        trigger={
            "kind": "changed_evidence",
            "summary": "Restart evidence invalidated prior revision.",
            "evidence_refs": ["artifact:restart-gap"],
        },
    )
    blocked_handoff = register.publish_task_planning_handoff(
        tmp_path,
        expected_generation=int(invalidated["task_register"]["write_generation"]),
        writer="h3k1.test",
        task_id=seeded["task_id"],
    )["handoff"]

    operator_snapshot = snapshot.build_operator_snapshot(tmp_path)
    task_snapshot = next(t for t in operator_snapshot["active_tasks"] if t["task_id"] == seeded["task_id"])

    assert blocked_handoff["launch_readiness"]["launch_ready"] is False
    assert blocked_handoff["launch_readiness"]["state"] == "blocked"
    assert "replanning_state_blocked" in blocked_handoff["launch_readiness"]["blocked_reasons"]
    assert task_snapshot["decomposition_contract"]["replanning_state"] == "blocked"
