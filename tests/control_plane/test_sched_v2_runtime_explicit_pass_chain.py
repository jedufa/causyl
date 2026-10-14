from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import sched as v2_sched
from infra.controlplane import scheduled_jobs as legacy_sched
from infra.controlplane import scheduled_jobs as sched


def _write_family(root: Path) -> str:
    parent_uid = "PIS-20260502-210000Z-PA01"
    intent_uid = "IS-20260502-210000Z-IN01"
    impl_uid = "PIS-20260502-210001Z-IM01"
    validation_uid = "PIS-20260502-210002Z-VA01"
    closeout_uid = "PIS-20260502-210003Z-CL01"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{parent_uid}__parent"
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
                    "execution_mode": "explicit_pass_chain_v1",
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
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{uid}__{slug}"
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


def _write_strict_family(root: Path) -> str:
    parent_uid = "PIS-20260502-220000Z-PA02"
    intent_uid = "IS-20260502-220000Z-IN02"
    impl_a_uid = "PIS-20260502-220001Z-AA01"
    impl_b_uid = "PIS-20260502-220002Z-BB02"
    validation_uid = "PIS-20260502-220003Z-VA03"
    closeout_uid = "PIS-20260502-220004Z-CL04"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{parent_uid}__parent"
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
                    "execution_mode": "explicit_pass_chain_v1",
                    "child_uids": [impl_a_uid, impl_b_uid, validation_uid, closeout_uid],
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
        (impl_a_uid, "impl-a", "Implementation A", "execution_child", 1, []),
        (impl_b_uid, "impl-b", "Implementation B", "execution_child", 2, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 3, [impl_a_uid, impl_b_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 4, [impl_a_uid, impl_b_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{uid}__{slug}"
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


def test_sched_v2_runtime_projects_activity_from_intermediate_explicit_pass(tmp_path: Path) -> None:
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T21:10:00Z",
        repo_root=tmp_path,
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
    )

    impl_job = plan.jobs[0]
    assert len(impl_job.projection_job_ids) == 3

    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{impl_job.projection_job_ids[0]}.json").write_text(
        json.dumps(
            {
                "job_id": impl_job.projection_job_ids[0],
                "last_shape_canonical_state": "succeeded",
                "last_status": "ok",
            }
        ),
        encoding="utf-8",
    )
    (state_dir / f"{impl_job.projection_job_ids[1]}.json").write_text(
        json.dumps(
            {
                "job_id": impl_job.projection_job_ids[1],
                "active_run_id": "sj_pass2",
                "last_shape_canonical_state": "running_iteration",
                "last_status": "running",
            }
        ),
        encoding="utf-8",
    )

    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    runtime_by_uid = {row["child_uid"]: row for row in runtime_records}

    assert runtime_by_uid[impl_job.child_uid]["job_id"] == impl_job.projection_job_ids[1]
    assert runtime_by_uid[impl_job.child_uid]["dispatch_job_id"] == impl_job.projection_job_ids[1]
    assert runtime_by_uid[impl_job.child_uid]["runtime_state"] == "running_iteration"
    assert runtime_by_uid[impl_job.child_uid]["active_run_id"] == "sj_pass2"


def test_sched_v2_runtime_ignores_pre_migration_success_for_explicit_pass_chain(tmp_path: Path) -> None:
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T21:10:00Z",
        repo_root=tmp_path,
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
    )

    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Simulate old pre-migration single-job success records that must not satisfy
    # explicit pass-chain children or their validation/closeout tails.
    legacy_job_ids = [
        legacy_sched._job_id_from_ledger_uid(plan.jobs[0].child_uid, plan.jobs[0].title)
    ]
    for legacy_job_id in legacy_job_ids:
        (state_dir / f"{legacy_job_id}.json").write_text(
            json.dumps(
                {
                    "job_id": legacy_job_id,
                    "last_shape_canonical_state": "succeeded",
                    "last_status": "ok",
                }
            ),
            encoding="utf-8",
        )

    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    runtime_by_uid = {row["child_uid"]: row for row in runtime_records}

    assert runtime_by_uid[plan.jobs[0].child_uid]["runtime_state"] == ""
    assert runtime_by_uid[plan.jobs[1].child_uid]["runtime_state"] == ""
    assert runtime_by_uid[plan.jobs[2].child_uid]["runtime_state"] == ""

    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}

    assert decisions[plan.jobs[0].child_uid].decision == "admit"
    assert decisions[plan.jobs[1].child_uid].decision == "blocked"
    assert decisions[plan.jobs[2].child_uid].decision == "blocked"


def test_save_job_clears_terminal_state_when_rebatched_spec_changes(tmp_path: Path) -> None:
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T21:10:00Z",
        repo_root=tmp_path,
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
    )
    validation_spec = next(job for job in plan.raw_compilation["job_specs"] if job["job_id"] == "pis_va01_20260502_validation")
    sched.save_job(dict(validation_spec), repo_root=tmp_path)
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "pis_va01_20260502_validation.json"
    state_path.write_text(
        json.dumps(
            {
                "job_id": "pis_va01_20260502_validation",
                "last_shape_canonical_state": "succeeded",
                "last_status": "ok",
            }
        ),
        encoding="utf-8",
    )

    changed = dict(validation_spec)
    changed["display_title"] = "Validation rerun after explicit rebatch"
    sched.save_job(changed, repo_root=tmp_path)

    assert not state_path.exists()


def test_sched_v2_runtime_advances_explicit_pass_chain_one_pass_at_a_time(tmp_path: Path) -> None:
    parent_uid = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T21:10:00Z",
        repo_root=tmp_path,
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
    )

    impl_job = plan.jobs[0]
    validation_job = plan.jobs[1]
    closeout_job = plan.jobs[2]
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)

    def _write_success(job_id: str) -> None:
        (state_dir / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "last_shape_canonical_state": "succeeded",
                    "last_status": "ok",
                }
            ),
            encoding="utf-8",
        )

    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_job.child_uid].decision == "admit"
    assert decisions[impl_job.child_uid].dispatch_job_id == impl_job.projection_job_ids[0]
    assert decisions[validation_job.child_uid].decision == "blocked"
    assert decisions[closeout_job.child_uid].decision == "blocked"

    _write_success(impl_job.projection_job_ids[0])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    runtime_by_uid = {row["child_uid"]: row for row in runtime_records}
    assert runtime_by_uid[impl_job.child_uid]["job_id"] == impl_job.projection_job_ids[0]
    assert runtime_by_uid[impl_job.child_uid]["dispatch_job_id"] == impl_job.projection_job_ids[1]
    assert runtime_by_uid[impl_job.child_uid]["retired"] is False
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_job.child_uid].decision == "admit"
    assert decisions[impl_job.child_uid].dispatch_job_id == impl_job.projection_job_ids[1]
    assert decisions[validation_job.child_uid].decision == "blocked"

    _write_success(impl_job.projection_job_ids[1])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    runtime_by_uid = {row["child_uid"]: row for row in runtime_records}
    assert runtime_by_uid[impl_job.child_uid]["job_id"] == impl_job.projection_job_ids[1]
    assert runtime_by_uid[impl_job.child_uid]["dispatch_job_id"] == impl_job.projection_job_ids[2]
    assert runtime_by_uid[impl_job.child_uid]["retired"] is False
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_job.child_uid].decision == "admit"
    assert decisions[impl_job.child_uid].dispatch_job_id == impl_job.projection_job_ids[2]
    assert decisions[validation_job.child_uid].decision == "blocked"

    _write_success(impl_job.projection_job_ids[2])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    runtime_by_uid = {row["child_uid"]: row for row in runtime_records}
    assert runtime_by_uid[impl_job.child_uid]["job_id"] == impl_job.projection_job_ids[2]
    assert runtime_by_uid[impl_job.child_uid]["dispatch_job_id"] == impl_job.projection_job_ids[2]
    assert runtime_by_uid[impl_job.child_uid]["retired"] is True
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_job.child_uid].decision == "retired"
    assert decisions[validation_job.child_uid].decision == "admit"
    assert decisions[closeout_job.child_uid].decision == "blocked"


def test_sched_v2_runtime_keeps_next_child_blocked_until_prior_child_terminal_pass(tmp_path: Path) -> None:
    parent_uid = _write_strict_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T21:10:00Z",
        repo_root=tmp_path,
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
    )

    impl_a = plan.jobs[0]
    impl_b = plan.jobs[1]
    state_dir = tmp_path / "governance/agents/runtime/scheduled_jobs/state"
    state_dir.mkdir(parents=True, exist_ok=True)

    def _write_success(job_id: str) -> None:
        (state_dir / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "last_shape_canonical_state": "succeeded",
                    "last_status": "ok",
                }
            ),
            encoding="utf-8",
        )

    _write_success(impl_a.projection_job_ids[0])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_a.child_uid].decision == "admit"
    assert decisions[impl_a.child_uid].dispatch_job_id == impl_a.projection_job_ids[1]
    assert decisions[impl_b.child_uid].decision == "blocked"
    assert decisions[impl_b.child_uid].reason.startswith("dependency_pending:")

    _write_success(impl_a.projection_job_ids[1])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_a.child_uid].decision == "admit"
    assert decisions[impl_a.child_uid].dispatch_job_id == impl_a.projection_job_ids[2]
    assert decisions[impl_b.child_uid].decision == "blocked"
    assert decisions[impl_b.child_uid].reason.startswith("dependency_pending:")

    _write_success(impl_a.projection_job_ids[2])
    runtime_records = v2_sched.collect_current_runtime_records(plan, repo_root=tmp_path)
    evaluation = v2_sched.evaluate_scheduler_plan(plan, runtime_records=runtime_records)
    decisions = {row.child_uid: row for row in evaluation.admitted}
    assert decisions[impl_a.child_uid].decision == "retired"
    assert decisions[impl_b.child_uid].decision == "admit"
    assert decisions[impl_b.child_uid].dispatch_job_id == impl_b.projection_job_ids[0]
