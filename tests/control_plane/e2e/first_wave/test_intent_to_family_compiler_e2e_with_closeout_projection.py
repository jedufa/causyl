from __future__ import annotations

from infra.controlplane import sched as v2_sched
from infra.controlplane.sched.surfaces import build_internal_surface_bundle

from .helpers.assertions import assert_no_fake_promotion, assert_projection_equivalent
from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.runtime import create_minimal_family, write_success_state


CONTRACT = ScenarioContract(
    scenario_id="intent_to_family_compiler_e2e_with_closeout_projection",
    operator_job="Submit an intent and see a compiled family with implementation, validation, closeout, and terminal surface parity.",
    capability="Compiled family shape, validation tail, closeout tail, completed-family projection, and shadow-mode non-promotion.",
    durable_truth=(
        "intent ledger state",
        "parent family ledger state",
        "compiled job specs",
        "runtime state files",
        "surface bundle parity",
    ),
    manual_repair_eliminated="Hand-checking whether the validation/closeout tail exists and whether the plane surfaces agree on terminal family state.",
    primary_intent_sheets=("IS-15",),
    supporting_intent_sheets=("IS-16",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic child-success state seeding",
        "temp-repo family compilation",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "fabricating closeout state",
        "writing projection records manually",
    ),
)


def test_intent_to_family_compiler_e2e_with_closeout_projection(first_wave_root) -> None:
    assert_scenario_contract(CONTRACT)
    uids = create_minimal_family(first_wave_root)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=uids["parent"],
        run_at_utc="2026-04-10T00:00:00Z",
        repo_root=first_wave_root,
    )
    jobs = {job.child_uid: job for job in plan.jobs}

    write_success_state(first_wave_root, job_id=jobs[uids["implementation"]].job_id)
    write_success_state(first_wave_root, job_id=jobs[uids["validation"]].job_id)
    write_success_state(first_wave_root, job_id=jobs[uids["closeout"]].job_id)

    shadow = v2_sched.run_shadow_tick(
        parent_uid=uids["parent"],
        run_at_utc="2026-04-10T00:10:00Z",
        repo_root=first_wave_root,
    )
    evaluation = shadow["evaluation"]
    assert evaluation["family_status"] == "completed"
    assert evaluation["current_stage"] == "closeout"
    assert evaluation["mutates_canonical_state"] is False
    assert {row["decision"] for row in evaluation["admitted"]} == {"retired"}

    bundle = build_internal_surface_bundle(
        repo_root=first_wave_root,
        parent_uid=uids["parent"],
        run_at_utc="2026-04-10T00:10:00Z",
    )
    assert bundle["status"] == "ready"
    assert_projection_equivalent(bundle)
    rows = {row["child_uid"]: row for row in bundle["surfaces"]["api"]}
    assert rows[uids["implementation"]]["decision"] == "retired"
    assert rows[uids["validation"]]["gate"] == "complete"
    assert rows[uids["closeout"]]["gate"] == "close_and_promote"

    parent_meta_path = (
        first_wave_root
        / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04"
        / f"{uids['parent']}__parent/meta.json"
    )
    intent_meta_path = (
        first_wave_root
        / "governance/ledger/intent_sheets/causyl/active/2026/04"
        / f"{uids['intent']}__intent/meta.json"
    )
    assert_no_fake_promotion(parent_meta_path=parent_meta_path, intent_meta_path=intent_meta_path)
