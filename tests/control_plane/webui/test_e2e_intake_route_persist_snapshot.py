from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_intake as intake
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_routing_policy as routing
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger" / "items").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "webui" / "verification_jobs").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _create_minimal_ledger_item(root: Path, *, slug: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    hex_suffix = uuid.uuid4().hex[:6].upper()
    ledger_uid = f"PIS-{now.strftime('%Y%m%d%H%M%S')}Z{hex_suffix}"
    item_dir = root / "governance" / "ledger" / "items" / f"{ledger_uid}__{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "pis.md").write_text(
        "\n".join(
            [
                "# Test PIS Item",
                "",
                f"**UID:** {ledger_uid}",
                "",
                "## Title",
                "Test Item for E2E Intake-Route-Persist-Snapshot Suite",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ledger_uid


def _seed_intake(root: Path, *, label: str) -> dict:
    current = register.load_or_init_task_register(root)
    ledger_uid = _create_minimal_ledger_item(root, slug=f"e2e-{label}")
    result = intake.persist_structured_request_intake(
        root,
        expected_generation=int(current["write_generation"]),
        writer="e2e.intake",
        raw_request_text=f"Implement {label} flow with durable intake route persist snapshot.",
        force_active_work=True,
        ledger_uid=ledger_uid,
    )
    result["ledger_uid"] = ledger_uid
    return result


def _delegate_request(*, task_id: str, ledger_uid: str, surface: str) -> dict:
    preferred_surface = surface if surface in policy.ALLOWED_V1_SURFACES else ""
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": f"delegate via {surface}",
            "preferred_surface": preferred_surface,
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
        }
    )
    return policy.build_typed_delegation_request(
        classification=classification,
        surface=surface,
        owner_ref={"task_id": task_id, "ledger_uid": ledger_uid},
        summary=f"Use {surface}",
        requested_action=f"launch {surface}",
        expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": f"Review {surface} result", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )


def _normalize_expected_paths(repo_root: Path, items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        path = Path(str(item))
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        else:
            path = path.resolve()
        out.append(str(path))
    return out


def _assert_evidence_ids(repo_root: Path, ref: dict, launch_result: dict, surface: str) -> None:
    status_refs = [str(Path(str(path)).resolve()) for path in list(ref.get("status_refs") or [])]
    artifact_refs = [str(Path(str(path)).resolve()) for path in list(ref.get("artifact_refs") or [])]
    evidence_candidates = [str(Path(str(path)).resolve()) for path in list(ref.get("evidence_candidates") or [])]

    assert status_refs or artifact_refs or evidence_candidates

    for key in ("status_refs", "artifact_refs", "evidence_candidates"):
        expected = launch_result.get(key)
        if expected:
            normalized_expected = _normalize_expected_paths(repo_root, list(expected))
            actual = {
                "status_refs": status_refs,
                "artifact_refs": artifact_refs,
                "evidence_candidates": evidence_candidates,
            }[key]
            for item in normalized_expected:
                assert item in actual

    if surface == "workflow":
        assert any(str(path).endswith("workflow_digest.json") for path in evidence_candidates)
    elif surface == "verifier":
        assert any(str(path).endswith("result.json") for path in evidence_candidates)
    elif surface == "scheduler":
        run_id = launch_result.get("run_id")
        if run_id:
            assert any(str(path).endswith(f"{run_id}.json") for path in evidence_candidates)
    elif surface in {"retrieval", "direct_turn_job"}:
        assert evidence_candidates


def test_e2e_casual_intake_does_not_persist(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="e2e.casual",
        raw_request_text="hello there",
        force_active_work=False,
    )

    assert result["ok"] is True
    assert result["persisted"] is False
    assert result["reason"] == "casual_chat"

    payload = snapshot.build_operator_snapshot(tmp_path)
    assert payload["active_tasks"] == []
    assert payload["open_loops"] == []


def test_e2e_clarification_intake_blocks_and_snapshot_waits(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="e2e.clarify",
        raw_request_text="Fix it.",
        force_active_work=True,
    )

    assert result["persisted"] is True
    assert result["intake"]["readiness"] == "clarification_needed"

    payload = snapshot.build_operator_snapshot(tmp_path)
    assert payload["operator_state"] == "waiting_on_operator"
    assert payload["active_tasks"][0]["status"] == "blocked"
    assert payload["open_loops"]


def test_route_direct_answer_path_selected() -> None:
    decision = routing.decide_short_or_delegated_route(
        {
            "summary": "quick guidance",
            "estimated_duration_s": 5,
            "requires_new_evidence": False,
        }
    )
    assert decision["decision_outcome"] == "direct"
    assert decision["status"] == "accepted"
    assert decision["selected_surface"] == ""


@pytest.mark.parametrize(
    ("surface", "routing_facts", "launch_result"),
    [
        ("execution_queue", {"multi_step": False, "cross_role": False, "queue_capacity": 4, "queue_backlog": 0}, {"run_id": "eq_e2e"}),
        ("workflow", {"multi_step": True, "workflow_step_count": 3}, {"workflow_id": "wf_e2e"}),
        (
            "scheduler",
            {
                "requires_schedule": True,
                "needs_immediate_start": False,
                "scheduler_safe": True,
                "scheduler_job_kind": "build_verify_loop",
                "scheduler_max_iterations": 2,
                "explicit_scheduler_justification": "overnight iteration",
                "code_changes_expected": True,
            },
            {"job_id": "sj_e2e", "run_id": "sj_20260327T000000Z_abcd12"},
        ),
        (
            "verifier",
            {"verification_required": True, "code_changes_expected": False, "multi_step": False},
            {
                "request_id": "vrf_e2e",
                "workflow_id": "wf_e2e",
                "action_id": "android_unit_test",
                "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.SmokeTest"},
            },
        ),
        (
            "retrieval",
            {"retrieval_required": True, "read_only": True, "code_changes_expected": False},
            {
                "ref_id": "ret_e2e",
                "status_refs": ["reports/ret_status.json"],
                "artifact_refs": ["reports/ret_artifacts"],
                "evidence_candidates": ["reports/ret_evidence.json"],
            },
        ),
        (
            "direct_turn_job",
            {"multi_step": False, "cross_role": False, "requires_commit_checkpoint": False},
            {
                "ref_id": "turn_e2e",
                "status_refs": ["runtime/state/turn_job/status.json"],
                "artifact_refs": ["runtime/state/turn_job/artifacts"],
                "evidence_candidates": ["runtime/state/turn_job/result.json"],
            },
        ),
    ],
)
def test_delegated_route_and_bind_persists(surface: str, routing_facts: dict, launch_result: dict, tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    intake_result = _seed_intake(tmp_path, label=f"delegated-{surface}")

    request = _delegate_request(
        task_id=intake_result["task_id"],
        ledger_uid=intake_result["ledger_uid"],
        surface=surface,
    )
    routed = routing.route_typed_delegation_request(request, routing_facts=routing_facts)
    assert routed["ok"] is True

    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(intake_result["task_register"]["write_generation"]),
        writer=f"e2e.bind.{surface}",
        request=routed["request"],
        launch_result=launch_result,
    )
    task = next(t for t in bound["task_register"]["active_tasks"] if t["task_id"] == intake_result["task_id"])
    assert any(ref["surface"] == routed["request"]["surface"] for ref in task["delegation_refs"])
    _assert_evidence_ids(tmp_path, bound["delegation_ref"], launch_result, surface)


def test_binding_failure_when_owner_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "delegate via workflow",
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
        }
    )
    request = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": "missing_task", "ledger_uid": "PIS-00000000-000000Z-TEST"},
        summary="Use workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "durable proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review proof", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )

    with pytest.raises(bindings.DelegationBindingsError):
        bindings.bind_delegation_request(
            tmp_path,
            expected_generation=int(current["write_generation"]),
            writer="e2e.bind.fail",
            request=request,
            launch_result={"workflow_id": "wf_missing"},
        )


def test_duplicate_intake_reuses_task_id(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    ledger_uid = _create_minimal_ledger_item(tmp_path, slug="duplicate-intake")
    first = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="e2e.dup.1",
        raw_request_text="Implement durable intake duplicate protection.",
        force_active_work=True,
        ledger_uid=ledger_uid,
    )
    second = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="e2e.dup.2",
        raw_request_text="Implement durable intake duplicate protection.",
        force_active_work=True,
        ledger_uid=ledger_uid,
    )
    assert second["task_id"] == first["task_id"]


def test_malformed_intake_is_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)

    result = intake.persist_structured_request_intake(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="e2e.malformed",
        raw_request_text="",
        force_active_work=True,
    )

    assert result["ok"] is False
    assert result["persisted"] is False
    assert result["reason"] == "malformed_input"


def test_stale_generation_write_is_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    ledger_uid = _create_minimal_ledger_item(tmp_path, slug="stale-write")

    with pytest.raises(intake.IntakeError):
        intake.persist_structured_request_intake(
            tmp_path,
            expected_generation=int(current["write_generation"]) + 1,
            writer="e2e.stale",
            raw_request_text="Implement a stale generation rejection test.",
            force_active_work=True,
            ledger_uid=ledger_uid,
        )


def test_snapshot_conflict_when_register_completed_but_projection_incomplete(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    intake_result = _seed_intake(tmp_path, label="conflict")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(t for t in task_register["active_tasks"] if t["task_id"] == intake_result["task_id"])
    task["status"] = "completed"
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(task_register["write_generation"]),
        writer="e2e.complete",
        task=task,
    )
    request = _delegate_request(
        task_id=intake_result["task_id"],
        ledger_uid=intake_result["ledger_uid"],
        surface="workflow",
    )
    routed = routing.route_typed_delegation_request(request, routing_facts={"multi_step": True, "workflow_step_count": 2})
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(updated["task_register"]["write_generation"]),
        writer="e2e.bind.conflict",
        request=routed["request"],
        launch_result={"workflow_id": "wf_conflict"},
    )
    ref = bound["delegation_ref"]
    status_path = Path(ref["status_refs"][0])
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps({"workflow_id": "wf_conflict", "status": "completed"}), encoding="utf-8")

    payload = snapshot.build_operator_snapshot(tmp_path)
    conflicts = payload["operator_summary"]["truth_conflicts"]
    assert any(row.get("kind") == "register_completed_but_projection_incomplete" for row in conflicts)


def test_snapshot_marks_stale_register_after_restart(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_intake(tmp_path, label="stale-snapshot")
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    if summary_path.exists():
        summary_path.unlink()

    payload = snapshot.build_operator_snapshot(tmp_path)
    assert payload["freshness"]["stale"] is True
    assert "task_register" in payload["freshness"]["stale_sources"]
    conflicts = payload["operator_summary"]["truth_conflicts"]
    assert any(row.get("kind") == "stale_task_register_with_open_work" for row in conflicts)
