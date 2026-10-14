from __future__ import annotations

from infra.controlplane import sched as v2_sched
from infra.controlplane.sched.surfaces import build_internal_surface_bundle

from .helpers.assertions import assert_projection_equivalent
from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.runtime import create_existing_fanout_family, write_failed_state, write_success_state


CONTRACT = ScenarioContract(
    scenario_id="fanout_workflow_e2e_with_partial_branch_failure",
    operator_job="Run bounded parallel exploration and get honest blockage when one branch fails.",
    capability="Fanout compiles into ordinary family nodes, branch failure is durable, and downstream stages remain blocked truthfully.",
    durable_truth=(
        "fanout materialization artifact",
        "compiled family graph",
        "child runtime state files",
        "cross-surface family projection",
    ),
    manual_repair_eliminated="Manually deciding whether integration or closeout should proceed after a failed exploration branch.",
    primary_intent_sheets=("IS-09",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic branch-result state seeding",
        "temp-repo family compilation",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "bypassing scheduler admission for the claimed execution path",
        "writing projection records manually",
    ),
)


def test_fanout_workflow_e2e_with_partial_branch_failure(first_wave_root) -> None:
    assert_scenario_contract(CONTRACT)
    family = create_existing_fanout_family(first_wave_root)
    result = family["materialized"]
    plan = family["plan"]
    jobs = {job.child_uid: job for job in plan.jobs}

    write_success_state(first_wave_root, job_id=jobs["PIS-20260410-181002Z-AA01"].job_id)
    write_failed_state(first_wave_root, job_id=jobs["PIS-20260410-181003Z-BB02"].job_id)
    write_success_state(first_wave_root, job_id=jobs["PIS-20260410-181004Z-CC03"].job_id)

    shadow = v2_sched.run_shadow_tick(
        parent_uid=result["parent_uid"],
        run_at_utc="2026-04-10T18:30:00Z",
        repo_root=first_wave_root,
    )
    evaluation = shadow["evaluation"]
    decisions = {row["child_uid"]: row for row in evaluation["admitted"]}

    assert evaluation["family_status"] == "failed"
    assert evaluation["current_stage"] == "implement"
    assert decisions["PIS-20260410-181003Z-BB02"]["reason"] == "failed"
    assert decisions["PIS-20260410-181005Z-IN04"]["decision"] == "blocked"
    assert decisions["PIS-20260410-181005Z-IN04"]["reason"] == "dependency_pending:PIS-20260410-181003Z-BB02"
    assert decisions["PIS-20260410-181006Z-VA05"]["decision"] == "blocked"
    assert decisions["PIS-20260410-181007Z-CL06"]["decision"] == "blocked"

    bundle = build_internal_surface_bundle(
        repo_root=first_wave_root,
        parent_uid=result["parent_uid"],
        run_at_utc="2026-04-10T18:30:00Z",
    )
    assert bundle["status"] == "ready"
    assert_projection_equivalent(bundle)
    api_rows = {row["child_uid"]: row for row in bundle["surfaces"]["api"]}
    assert api_rows["PIS-20260410-181003Z-BB02"]["reason"] == "failed"
    assert api_rows["PIS-20260410-181005Z-IN04"]["reason"] == "dependency_pending:PIS-20260410-181003Z-BB02"
    assert api_rows["PIS-20260410-181007Z-CL06"]["gate"] == "close_and_promote"
    assert (first_wave_root / result["artifact_path"]).exists()
