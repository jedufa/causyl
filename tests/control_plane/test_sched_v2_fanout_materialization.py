from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import sched as v2_sched
from infra.controlplane.sched.fanout import materialize_fanout_for_intent
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance/intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_prompt_template(root: Path) -> None:
    prompt_path = root / "docs/prompts/lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")


def _spec() -> dict:
    return {
        "source_intent": {"uid": "IS-20260410-181000Z-FN01", "title": "Materialized Fanout"},
        "parent": {"uid": "PIS-20260410-181001Z-PA01", "title": "Materialized Fanout Parent"},
        "parallel_children": [
            {"uid": "PIS-20260410-181002Z-AA01", "title": "Explore A"},
            {"uid": "PIS-20260410-181003Z-BB02", "title": "Explore B"},
            {"uid": "PIS-20260410-181004Z-CC03", "title": "Explore C"},
        ],
        "integration_child": {"uid": "PIS-20260410-181005Z-IN04", "title": "Integrate fanout"},
        "validation_child": {"uid": "PIS-20260410-181006Z-VA05", "title": "Validate fanout"},
        "closeout_child": {"uid": "PIS-20260410-181007Z-CL06", "title": "Close fanout"},
    }


def _existing_intent_fanout_spec() -> dict:
    spec = _spec()
    spec.pop("source_intent")
    return spec


def test_materialized_fanout_for_existing_intent_updates_sheet_without_recreating_it(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    _write_prompt_template(tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    intents.create_sheet(
        uid_override="IS-20260410-181000Z-FN01",
        title="Existing Fanout Intent",
        intent_markdown="# Existing Fanout Intent\n\nUse fanout as implementation methodology.\n",
        project_id="causyl",
    )

    result = materialize_fanout_for_intent(
        "IS-20260410-181000Z-FN01",
        _existing_intent_fanout_spec(),
        repo_root=tmp_path,
    )
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=result["parent_uid"],
        run_at_utc="2026-04-10T18:20:00Z",
        repo_root=tmp_path,
    )
    sheet = intents.read_sheet("IS-20260410-181000Z-FN01")

    assert result["intent_sheet_mode"] == "existing"
    assert result["intent_uid"] == "IS-20260410-181000Z-FN01"
    assert sheet["meta"]["derived_pis_uids"] == ["PIS-20260410-181001Z-PA01"]
    assert sheet["meta"]["validating_pis_uids"] == ["PIS-20260410-181006Z-VA05"]
    assert sheet["meta"]["decomposition_class"] == "workflow"
    assert sheet["meta"]["decomposition_target_range"] == {"min": 6, "max": 6}
    assert "Existing Fanout Intent" in sheet["intent_markdown"]
    assert [job.child_uid for job in plan.jobs] == result["child_uids"]
    assert plan.source_intent_uid == "IS-20260410-181000Z-FN01"
    jobs = {job.child_uid: job for job in plan.jobs}
    assert jobs["PIS-20260410-181002Z-AA01"].depends_on_uids == ()
    assert jobs["PIS-20260410-181003Z-BB02"].depends_on_uids == ()
    assert jobs["PIS-20260410-181004Z-CC03"].depends_on_uids == ()
    assert jobs["PIS-20260410-181005Z-IN04"].depends_on_uids == (
        "PIS-20260410-181002Z-AA01",
        "PIS-20260410-181003Z-BB02",
        "PIS-20260410-181004Z-CC03",
    )
    assert jobs["PIS-20260410-181006Z-VA05"].gate == "complete"
    assert jobs["PIS-20260410-181007Z-CL06"].gate == "close_and_promote"
    assert (tmp_path / result["artifact_path"]).exists()
    assert result["compiled_work_order"]["status"] == "compiled_emitted"
    assert (tmp_path / result["compiled_work_order"]["compiled_artifact_rel"]).exists()
