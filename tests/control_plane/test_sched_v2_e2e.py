from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import sched as v2_sched


RUN_AT_UTC = "2026-04-10T00:00:00Z"


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(root: Path) -> dict[str, str]:
    parent_uid = "PIS-20260410-040000Z-PA04"
    intent_uid = "IS-20260410-040001Z-IN04"
    implementation_uid = "PIS-20260410-040002Z-IM04"
    validation_uid = "PIS-20260410-040003Z-VA04"
    closeout_uid = "PIS-20260410-040004Z-CL04"

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
                    "child_uids": [implementation_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    intent_dir = root / "governance/ledger/intent_sheets/causyl/active/2026/04" / f"{intent_uid}__intent"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "intent.md").write_text("# Intent\n", encoding="utf-8")
    (intent_dir / "meta.json").write_text(json.dumps({"uid": intent_uid, "status": "draft"}, indent=2) + "\n", encoding="utf-8")
    (intent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, slug, title, relationship, rank, deps in [
        (implementation_uid, "implementation", "Implementation", "execution_child", 1, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 2, [implementation_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 3, [implementation_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": relationship},
                },
                indent=2,
            )
            + "\n",
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

    return {
        "parent": parent_uid,
        "intent": intent_uid,
        "implementation": implementation_uid,
        "validation": validation_uid,
        "closeout": closeout_uid,
    }


def _state_dir(root: Path) -> Path:
    path = root / "governance/agents/runtime/scheduled_jobs/state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_success_state(root: Path, *, job_id: str) -> None:
    (_state_dir(root) / f"{job_id}.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "active_run_id": "",
                "last_shape_canonical_state": "succeeded",
                "last_status": "ok",
                "last_receipt_path": f"receipts/{job_id}.json",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _decisions(payload: dict) -> dict[str, dict]:
    return {row["child_uid"]: row for row in payload["evaluation"]["admitted"]}


def _meta_status(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["status"]


def test_sched_v2_e2e_progresses_one_intent_family_without_promoting_truth(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    uids = _write_family(tmp_path)
    plan = v2_sched.compile_scheduler_plan(parent_uid=uids["parent"], run_at_utc=RUN_AT_UTC, repo_root=tmp_path)
    jobs = {job.child_uid: job for job in plan.jobs}

    queued = v2_sched.run_shadow_tick(parent_uid=uids["parent"], run_at_utc=RUN_AT_UTC, repo_root=tmp_path)
    queued_decisions = _decisions(queued)
    assert queued["evaluation"]["family_status"] == "queued"
    assert queued["evaluation"]["continuity"] == {
        "continuity_state": "queued",
        "decision_source": "canonical_projection",
        "reason": "normal_family_progress",
        "recovery_outcome": "none",
    }
    assert queued["evaluation"]["shadow_report"]["status"] == "not_compared"
    assert queued["evaluation"]["mutates_canonical_state"] is False
    assert queued_decisions[uids["implementation"]]["decision"] == "admit"
    assert queued_decisions[uids["validation"]]["decision"] == "blocked"
    assert queued_decisions[uids["closeout"]]["decision"] == "blocked"
    assert queued_decisions[uids["validation"]]["reason"] == f"dependency_pending:{uids['implementation']}"

    _write_success_state(tmp_path, job_id=jobs[uids["implementation"]].job_id)
    implementation_done = v2_sched.run_shadow_tick(parent_uid=uids["parent"], run_at_utc=RUN_AT_UTC, repo_root=tmp_path)
    implementation_decisions = _decisions(implementation_done)
    assert implementation_done["evaluation"]["continuity"] == {
        "continuity_state": "queued",
        "decision_source": "canonical_projection",
        "reason": "normal_family_progress",
        "recovery_outcome": "none",
    }
    assert implementation_decisions[uids["implementation"]]["decision"] == "retired"
    assert implementation_decisions[uids["validation"]]["decision"] == "admit"
    assert implementation_decisions[uids["validation"]]["gate"] == "complete"
    assert implementation_decisions[uids["closeout"]]["decision"] == "blocked"
    assert implementation_decisions[uids["closeout"]]["reason"] == f"dependency_pending:{uids['validation']}"

    _write_success_state(tmp_path, job_id=jobs[uids["validation"]].job_id)
    validation_done = v2_sched.run_shadow_tick(parent_uid=uids["parent"], run_at_utc=RUN_AT_UTC, repo_root=tmp_path)
    validation_decisions = _decisions(validation_done)
    assert validation_done["evaluation"]["continuity"] == {
        "continuity_state": "queued",
        "decision_source": "canonical_projection",
        "reason": "normal_family_progress",
        "recovery_outcome": "none",
    }
    assert validation_decisions[uids["implementation"]]["decision"] == "retired"
    assert validation_decisions[uids["validation"]]["decision"] == "retired"
    assert validation_decisions[uids["closeout"]]["decision"] == "admit"
    assert validation_decisions[uids["closeout"]]["gate"] == "close_and_promote"

    parent_meta_path = (
        tmp_path
        / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04"
        / f"{uids['parent']}__parent/meta.json"
    )
    intent_meta_path = (
        tmp_path
        / "governance/ledger/intent_sheets/causyl/active/2026/04"
        / f"{uids['intent']}__intent/meta.json"
    )
    parent_before = parent_meta_path.read_text(encoding="utf-8")
    intent_before = intent_meta_path.read_text(encoding="utf-8")

    _write_success_state(tmp_path, job_id=jobs[uids["closeout"]].job_id)
    completed = v2_sched.run_shadow_tick(parent_uid=uids["parent"], run_at_utc=RUN_AT_UTC, repo_root=tmp_path)
    completed_decisions = _decisions(completed)
    assert completed["evaluation"]["family_status"] == "completed"
    assert completed["evaluation"]["current_stage"] == "closeout"
    assert completed["evaluation"]["continuity"] == {
        "continuity_state": "completed",
        "decision_source": "canonical_projection",
        "reason": "terminal_family",
        "recovery_outcome": "none",
    }
    assert completed["evaluation"]["shadow_report"]["status"] == "not_compared"
    assert {row["decision"] for row in completed_decisions.values()} == {"retired"}
    assert completed["evaluation"]["mutates_canonical_state"] is False

    assert parent_meta_path.read_text(encoding="utf-8") == parent_before
    assert intent_meta_path.read_text(encoding="utf-8") == intent_before
    assert _meta_status(parent_meta_path) == "draft"
    assert _meta_status(intent_meta_path) == "draft"
