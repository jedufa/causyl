from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import sched as v2_sched
from infra.controlplane.explicit_pass_chain_compiler import ExplicitPassChainCompileError
from infra.controlplane.sched import surfaces as sched_surfaces
from infra.controlplane.sched import canary as sched_canary
from infra.controlplane.sched.canary import CanaryBlocked


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(root: Path) -> str:
    parent_uid = "PIS-20260410-030000Z-PA03"
    intent_uid = "IS-20260410-030001Z-IN03"
    impl_uid = "PIS-20260410-030002Z-IM03"
    validation_uid = "PIS-20260410-030003Z-VA03"
    closeout_uid = "PIS-20260410-030004Z-CL03"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [impl_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, relationship, rank, deps in [
        (impl_uid, "impl", "Implementation", "execution_child", 1, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 2, [impl_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 3, [impl_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "draft", "integration_meta": {"relationship": relationship}}),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs/prompts/lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")
    return parent_uid


def test_sched_v2_runtime_admits_only_dependency_ready_jobs(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)

    evaluation = v2_sched.evaluate_scheduler_plan(
        plan,
        runtime_records=[
            {"child_uid": plan.jobs[0].child_uid, "job_id": plan.jobs[0].job_id, "runtime_state": ""},
            {"child_uid": plan.jobs[1].child_uid, "job_id": plan.jobs[1].job_id, "runtime_state": ""},
            {"child_uid": plan.jobs[2].child_uid, "job_id": plan.jobs[2].job_id, "runtime_state": ""},
        ],
    )

    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[plan.jobs[0].child_uid].decision == "admit"
    assert decisions[plan.jobs[1].child_uid].decision == "blocked"
    assert decisions[plan.jobs[2].child_uid].decision == "blocked"
    assert evaluation.mutates_canonical_state is False
    assert evaluation.projection["family_status"] == "queued"
    assert evaluation.continuity["continuity_state"] == "queued"
    assert evaluation.continuity["recovery_outcome"] == "none"
    assert evaluation.shadow_report["status"] == "not_compared"


def test_sched_v2_runtime_preserves_active_run_projection_labels(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{plan.jobs[0].job_id}.json").write_text(
        json.dumps(
            {
                "job_id": plan.jobs[0].job_id,
                "active_run_id": "",
                "last_shape_canonical_state": "succeeded",
                "last_status": "ok",
            }
        ),
        encoding="utf-8",
    )
    (state_dir / f"{plan.jobs[1].job_id}.json").write_text(
        json.dumps(
            {
                "job_id": plan.jobs[1].job_id,
                "active_run_id": "sj_active",
                "last_shape_canonical_state": "running_iteration",
                "last_status": "running",
            }
        ),
        encoding="utf-8",
    )

    payload = v2_sched.run_shadow_tick(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)
    decisions = {row["child_uid"]: row for row in payload["evaluation"]["admitted"]}
    runtime_records = {row["child_uid"]: row for row in payload["runtime_records"]}

    assert runtime_records[plan.jobs[1].child_uid]["runtime_state"] == "running_iteration"
    assert runtime_records[plan.jobs[1].child_uid]["active_run_id"] == "sj_active"
    assert decisions[plan.jobs[1].child_uid]["decision"] == "active"
    assert decisions[plan.jobs[1].child_uid]["reason"] == "active_run_present"
    assert payload["evaluation"]["mutates_canonical_state"] is False


def test_sched_v2_runtime_projects_child_activity_from_explicit_pass_chain_job_ids(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    child_root = tmp_path / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04"
    parent_dir = child_root / f"{parent_uid}__parent"
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"]["execution_mode"] = "explicit_pass_chain_v1"
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")

    from infra.controlplane import explicit_pass_chain_compiler

    compiled = explicit_pass_chain_compiler.compile_explicit_pass_chain(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T00:00:00Z",
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        pass_count=3,
        repo_root=tmp_path,
    )
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    pass_specs = [
        spec
        for spec in list(compiled.get("job_specs") or [])
        if str((spec.get("binding") or {}).get("id") or "").strip() == "PIS-20260410-030002Z-IM03"
    ]
    (state_dir / f"{pass_specs[0]['job_id']}.json").write_text(
        json.dumps({"job_id": pass_specs[0]["job_id"], "last_status": "ok", "last_shape_canonical_state": "succeeded"}),
        encoding="utf-8",
    )
    (state_dir / f"{pass_specs[1]['job_id']}.json").write_text(
        json.dumps(
            {
                "job_id": pass_specs[1]["job_id"],
                "active_run_id": "sj_pass2",
                "last_status": "running",
                "last_shape_canonical_state": "running_iteration",
            }
        ),
        encoding="utf-8",
    )

    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T00:00:00Z",
        repo_root=tmp_path,
        max_iterations=3,
    )
    payload = v2_sched.run_shadow_tick(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T00:00:00Z",
        repo_root=tmp_path,
    )
    runtime_records = {row["child_uid"]: row for row in payload["runtime_records"]}

    impl_runtime = runtime_records[plan.jobs[0].child_uid]
    assert impl_runtime["job_id"] == pass_specs[1]["job_id"]
    assert impl_runtime["runtime_state"] == "running_iteration"
    assert impl_runtime["active_run_id"] == "sj_pass2"


def test_sched_v2_runtime_resolves_external_dependency_job_identity_without_mutating(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    external_uid = "PIS-20260410-030005Z-EX05"
    external_job_id = "pis_external_20260410_foundation"
    child_root = tmp_path / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04"
    impl_events = child_root / "PIS-20260410-030002Z-IM03__impl/events.jsonl"
    impl_events.write_text(
        json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {
                    "parent_uid": parent_uid,
                    "relationship": "execution_child",
                    "critical_path_rank": 1,
                    "depends_on": [external_uid],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    external_dir = child_root / f"{external_uid}__external"
    external_dir.mkdir(parents=True, exist_ok=True)
    (external_dir / "pis.md").write_text("# External\n", encoding="utf-8")
    (external_dir / "meta.json").write_text(
        json.dumps({"uid": external_uid, "title": "External", "status": "verified"}),
        encoding="utf-8",
    )
    (external_dir / "events.jsonl").write_text("", encoding="utf-8")
    inbox = tmp_path / "governance/agents/runtime/scheduled_jobs/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{external_job_id}.json").write_text(
        json.dumps({"job_id": external_job_id, "binding": {"id": external_uid, "type": "ledger_uid"}}),
        encoding="utf-8",
    )
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{external_job_id}.json").write_text(
        json.dumps({"job_id": external_job_id, "last_shape_canonical_state": "succeeded", "last_status": "ok"}),
        encoding="utf-8",
    )

    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)
    payload = v2_sched.run_shadow_tick(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)
    decisions = {row["child_uid"]: row for row in payload["evaluation"]["admitted"]}

    assert plan.jobs[0].depends_on_uids == (external_uid,)
    assert plan.jobs[0].depends_on_job_ids == (external_job_id,)
    assert any(row["child_uid"] == external_uid and row["retired"] is True for row in payload["runtime_records"])
    assert decisions[plan.jobs[0].child_uid]["decision"] == "admit"
    assert payload["evaluation"]["mutates_canonical_state"] is False


def test_sched_v2_runtime_blocks_terminal_success_with_active_flag_as_truth_ambiguous(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)

    evaluation = v2_sched.evaluate_scheduler_plan(
        plan,
        runtime_records=[
            {
                "child_uid": plan.jobs[0].child_uid,
                "job_id": plan.jobs[0].job_id,
                "runtime_state": "succeeded",
                "active": True,
                "active_run_id": "sj_poisoned",
                "retired": False,
            },
            {
                "child_uid": plan.jobs[1].child_uid,
                "job_id": plan.jobs[1].job_id,
                "runtime_state": "",
                "active": False,
                "active_run_id": "",
                "retired": False,
            },
            {
                "child_uid": plan.jobs[2].child_uid,
                "job_id": plan.jobs[2].job_id,
                "runtime_state": "",
                "active": False,
                "active_run_id": "",
                "retired": False,
            },
        ],
    )

    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[plan.jobs[0].child_uid].decision == "blocked"
    assert decisions[plan.jobs[0].child_uid].reason == "truth_ambiguous:terminal_success_and_active_run"
    assert decisions[plan.jobs[1].child_uid].decision == "blocked"
    assert decisions[plan.jobs[1].child_uid].reason == f"dependency_truth_ambiguous:{plan.jobs[0].child_uid}"


def test_sched_v2_runtime_marks_multiple_active_heads_as_truth_ambiguous(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)

    evaluation = v2_sched.evaluate_scheduler_plan(
        plan,
        runtime_records=[
            {
                "child_uid": plan.jobs[0].child_uid,
                "job_id": plan.jobs[0].job_id,
                "runtime_state": "running",
                "active": True,
                "active_run_id": "sj_a",
                "retired": False,
            },
            {
                "child_uid": plan.jobs[1].child_uid,
                "job_id": plan.jobs[1].job_id,
                "runtime_state": "running",
                "active": True,
                "active_run_id": "sj_b",
                "retired": False,
            },
            {
                "child_uid": plan.jobs[2].child_uid,
                "job_id": plan.jobs[2].job_id,
                "runtime_state": "",
                "active": False,
                "active_run_id": "",
                "retired": False,
            },
        ],
    )

    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[plan.jobs[0].child_uid].decision == "blocked"
    assert decisions[plan.jobs[0].child_uid].reason == "truth_ambiguous:multiple_active_children"
    assert decisions[plan.jobs[1].child_uid].decision == "blocked"
    assert decisions[plan.jobs[1].child_uid].reason == "truth_ambiguous:multiple_active_children"
    assert decisions[plan.jobs[2].child_uid].decision == "blocked"
    assert decisions[plan.jobs[2].child_uid].reason == "dependency_truth_ambiguous:multiple_active_children"


def test_sched_v2_shadow_tick_reads_current_state_without_mutating(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    for job in plan.jobs:
        (state_dir / f"{job.job_id}.json").write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "active_run_id": "",
                    "last_shape_canonical_state": "succeeded",
                    "last_status": "ok",
                }
            ),
            encoding="utf-8",
        )

    payload = v2_sched.run_shadow_tick(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=tmp_path)

    assert payload["mode"] == "shadow"
    assert payload["evaluation"]["family_status"] == "completed"
    assert payload["evaluation"]["continuity"]["continuity_state"] == "completed"
    assert payload["evaluation"]["continuity"]["recovery_outcome"] == "none"
    assert payload["evaluation"]["shadow_report"]["status"] == "not_compared"
    assert payload["evaluation"]["mutates_canonical_state"] is False
    assert {row["decision"] for row in payload["evaluation"]["admitted"]} == {"retired"}
    assert {row["retired"] for row in payload["runtime_records"]} == {True}
    assert payload["ownership"]["mode"] == "v2_shadow"
    assert payload["ownership"]["canonical_owner"] == "legacy_scheduler"
    assert payload["ownership"]["v2_can_write_canonical"] is False


def test_sched_v2_ownership_modes_fail_closed_for_canonical_writes(tmp_path: Path) -> None:
    shadow = v2_sched.load_ownership(mode="shadow")
    assert shadow.mode == "v2_shadow"
    assert shadow.v2_can_write_canonical is False
    try:
        v2_sched.assert_canonical_write_allowed(shadow, parent_uid="PIS-20260410-030000Z-PA03")
    except PermissionError as exc:
        assert "canonical writes are blocked" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("shadow mode must not allow canonical writes")

    primary = v2_sched.load_ownership(mode="primary")
    v2_sched.assert_canonical_write_allowed(primary, parent_uid="PIS-20260410-030000Z-PA03")


def test_sched_v2_canary_mode_rejects_unlisted_parent(tmp_path: Path) -> None:
    canary = v2_sched.load_ownership(mode="canary", canary_parent_uids=("PIS-ALLOW",))
    try:
        v2_sched.assert_v2_can_evaluate_family(canary, parent_uid="PIS-DENY")
    except PermissionError as exc:
        assert "does not own parent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("canary mode must reject unlisted parents")


def test_sched_v2_surface_bundle_exposes_equivalent_internal_consumers(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    wiring = tmp_path / "infra/controlplane/sched/production_wiring_contract.json"
    wiring.parent.mkdir(parents=True, exist_ok=True)
    wiring.write_text(Path("infra/controlplane/sched/production_wiring_contract.json").read_text(encoding="utf-8"), encoding="utf-8")

    bundle = v2_sched.build_internal_surface_bundle(
        parent_uid=parent_uid,
        repo_root=tmp_path,
        run_at_utc="2026-04-10T00:00:00Z",
    )

    assert bundle["status"] == "ready"
    assert bundle["process"]["public_ports"] == []
    assert bundle["projection_equivalent"] is True
    assert bundle["surfaces"]["api"] == bundle["surfaces"]["watch"] == bundle["surfaces"]["web"] == bundle["surfaces"]["mobile"]
    assert bundle["consumers"]["watchdog"].endswith("must not directly mutate v2 scheduler truth")


def test_sched_v2_surface_bundle_treats_explicit_pass_missing_children_as_pending_decomposition(
    tmp_path: Path, monkeypatch
) -> None:
    wiring = tmp_path / "infra/controlplane/sched/production_wiring_contract.json"
    wiring.parent.mkdir(parents=True, exist_ok=True)
    wiring.write_text(Path("infra/controlplane/sched/production_wiring_contract.json").read_text(encoding="utf-8"), encoding="utf-8")

    parent_uid = "PIS-20260503-101230Z-7EB6"
    inbox_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / "implement_7eb6_test.json").write_text(
        json.dumps(
            {
                "job_id": "implement_7eb6_test",
                "display_title": "20-Year Life Structure and 12-Week Rebuild Plan",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": parent_uid},
            }
        ),
        encoding="utf-8",
    )
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "implement_7eb6_test.json").write_text(
        json.dumps(
            {
                "job_id": "implement_7eb6_test",
                "run_now_request_run_id": "run_now_1",
                "run_now_requested_utc": "2026-05-03T10:12:33Z",
            }
        ),
        encoding="utf-8",
    )

    def _raise_pending(**kwargs):
        raise ExplicitPassChainCompileError(
            (
                {"code": "missing_children", "message": "missing child rows"},
                {"code": "missing_execution_children", "message": "missing execution children"},
                {"code": "invalid_validation_tail", "message": "missing validation tail"},
                {"code": "invalid_closeout_tail", "message": "missing closeout tail"},
            )
        )

    monkeypatch.setattr(sched_surfaces, "run_shadow_tick", _raise_pending)

    bundle = v2_sched.build_internal_surface_bundle(
        parent_uid=parent_uid,
        repo_root=tmp_path,
        run_at_utc="2026-05-03T10:20:00Z",
    )

    assert bundle["status"] == "pending_decomposition"
    assert bundle["error"] == ""
    assert bundle["surfaces"]["watch"] == [
        {
            "job_id": "implement_7eb6_test",
            "child_uid": parent_uid,
            "title": "20-Year Life Structure and 12-Week Rebuild Plan",
            "node_kind": "family_parent_decomposition",
            "decision": "admit",
            "reason": "run_now_requested",
            "gate": "parent_decomposition",
            "unmet_dependencies": [],
        }
    ]


def test_sched_v2_cutover_gates_block_primary_without_explicit_allow(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    wiring = tmp_path / "infra/controlplane/sched/production_wiring_contract.json"
    wiring.parent.mkdir(parents=True, exist_ok=True)
    wiring.write_text(Path("infra/controlplane/sched/production_wiring_contract.json").read_text(encoding="utf-8"), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "status": "pass",
                "dimensions": [
                    {"dimension": "operator_truth", "verdict": "pass"},
                    {"dimension": "dependency_admission", "verdict": "pass"},
                    {"dimension": "stress_volume", "verdict": "pass"},
                    {"dimension": "destructive_conditions", "verdict": "pass"},
                    {"dimension": "ownership_isolation", "verdict": "pass"},
                    {"dimension": "internal_wiring", "verdict": "pass"},
                    {"dimension": "complexity", "verdict": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )

    blocked = v2_sched.evaluate_cutover_gates(
        repo_root=tmp_path,
        benchmark_report_path=report,
        parent_uid=parent_uid,
        target_mode="primary",
        allow_primary=False,
    )
    assert blocked["ready"] is False
    assert any(row["name"] == "primary_requires_explicit_allow" and not row["ok"] for row in blocked["gates"])

    allowed = v2_sched.evaluate_cutover_gates(
        repo_root=tmp_path,
        benchmark_report_path=report,
        parent_uid=parent_uid,
        target_mode="primary",
        allow_primary=True,
    )
    assert allowed["ready"] is True
    assert allowed["next_action"] == "enable_v2_primary"


def _write_canary_target(root: Path, parent_uid: str) -> Path:
    target_path = root / "infra/controlplane/sched/real_pis_shadow_canary_target.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(
            {
                "schema_version": "real_pis_scheduler_v2_shadow_canary_target_v1",
                "target_uid": parent_uid,
                "target_kind": "intent_family_parent_pis",
                "source_intent_uid": "IS-20260410-030001Z-IN03",
                "selected_by_child_uid": "PIS-20260410-041856Z-A480",
                "canonical_executor": "scheduler_v1",
                "shadow_executor": "infra.controlplane.sched.run_shadow_tick",
                "rationale": "Test fixture selects one durable real-PIS family parent instead of choosing ad hoc at runtime.",
                "approved_artifact_roots": ["governance/evidence/benchmarks/scheduler", "infra/controlplane/sched"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target_path


def test_real_pis_shadow_canary_writes_isolated_blocked_evidence_without_mutating_truth(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    target_path = _write_canary_target(tmp_path, parent_uid)
    parent_meta = next((tmp_path / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04").glob(f"{parent_uid}__*/meta.json"))
    parent_before = parent_meta.read_text(encoding="utf-8")

    receipt = v2_sched.run_real_pis_shadow_canary(
        run_at_utc="2026-04-10T00:00:00Z",
        repo_root=tmp_path,
        target_path=target_path,
    )

    assert receipt["target"]["target_uid"] == parent_uid
    assert receipt["status"] in {"pass", "blocked"}
    assert receipt["shadow_mode"] == "shadow"
    assert receipt["v1_scheduler_remains_canonical"] is True
    assert receipt["v2_shadow_mutates_canonical_state"] is False
    assert receipt["canonical_truth_mutated"] is False
    assert parent_meta.read_text(encoding="utf-8") == parent_before
    for rel_path in receipt["artifacts"].values():
        assert rel_path.startswith("governance/evidence/benchmarks/scheduler/real_pis_shadow_canary/")
        assert (tmp_path / rel_path).exists()


def test_real_pis_shadow_canary_fails_closed_without_durable_target(tmp_path: Path) -> None:
    try:
        v2_sched.load_canary_target(repo_root=tmp_path)
    except CanaryBlocked as exc:
        assert "durable canary target is missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing canary target must fail closed")


def test_real_pis_shadow_canary_target_rejects_unapproved_artifact_roots(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    target_path = _write_canary_target(tmp_path, parent_uid)
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    payload["approved_artifact_roots"] = ["governance/evidence/benchmarks/scheduler", "runtime/state"]
    target_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    try:
        v2_sched.load_canary_target(repo_root=tmp_path, target_path=target_path)
    except CanaryBlocked as exc:
        assert "unsupported approved_artifact_roots" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unapproved artifact roots must fail closed")


def test_real_pis_shadow_canary_target_rejects_shadow_executor_drift(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    target_path = _write_canary_target(tmp_path, parent_uid)
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    payload["shadow_executor"] = "infra.controlplane.sched.run_primary_tick"
    target_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    try:
        v2_sched.load_canary_target(repo_root=tmp_path, target_path=target_path)
    except CanaryBlocked as exc:
        assert "must use infra.controlplane.sched.run_shadow_tick" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("shadow executor drift must fail closed")


def test_sched_canary_ledger_dir_for_uid_ignores_stale_uid_index_entry(tmp_path: Path) -> None:
    uid = "PIS-20260410-050000Z-PA05"
    items_root = tmp_path / "governance" / "ledger" / "patch_instruction_sheets"
    verified_dir = items_root / "causyl" / "verified" / "2026" / "04" / f"{uid}__verified-parent"
    verified_dir.mkdir(parents=True, exist_ok=True)
    (verified_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (verified_dir / "meta.json").write_text(
        json.dumps({"uid": uid, "title": "Parent", "status": "verified"}),
        encoding="utf-8",
    )
    (verified_dir / "events.jsonl").write_text("", encoding="utf-8")
    stale_dir = items_root / "causyl" / "active" / "2026" / "04" / f"{uid}__verified-parent"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (items_root / "uid_index.json").write_text(
        json.dumps(
            {
                "schema_version": "ledger_uid_registry_v1",
                "generated_utc": "2026-04-10T05:00:00Z",
                "items": {
                    uid: {
                        "path": f"causyl/active/2026/04/{uid}__verified-parent",
                        "slug": "verified-parent",
                        "title": "Parent",
                        "status": "draft",
                        "project_id": "causyl",
                        "storage_bucket": "active",
                        "created_utc": "2026-04-10T05:00:00Z",
                        "updated_utc": "2026-04-10T05:00:00Z",
                    }
                },
                "duplicates": {},
            }
        ),
        encoding="utf-8",
    )

    resolved = sched_canary._ledger_dir_for_uid(tmp_path, uid)

    assert resolved == verified_dir.resolve()
