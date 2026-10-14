from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.sched.fanout import FanoutAuthoringError, compile_fanout_authoring_plan, write_fanout_authoring_plan_artifact


def _spec() -> dict:
    return {
        "source_intent": {"uid": "IS-20260410-180000Z-FAN1", "title": "Fanout Investigation"},
        "parent": {"uid": "PIS-20260410-180001Z-PAR1", "title": "Fanout Investigation Parent"},
        "parallel_children": [
            {"uid": "PIS-20260410-180002Z-AAA1", "title": "Explore approach A"},
            {"uid": "PIS-20260410-180003Z-BBB2", "title": "Explore approach B"},
            {"uid": "PIS-20260410-180004Z-CCC3", "title": "Explore approach C"},
        ],
        "integration_child": {"uid": "PIS-20260410-180005Z-INT4", "title": "Integrate fanout findings"},
        "validation_child": {"uid": "PIS-20260410-180006Z-VAL5", "title": "Validate integrated result"},
        "closeout_child": {"uid": "PIS-20260410-180007Z-CLO6", "title": "Close fanout family"},
    }


def test_fanout_authoring_compiles_parallel_children_to_v2_normal_form() -> None:
    plan = compile_fanout_authoring_plan(_spec())

    assert plan["schema_version"] == "sched_v2_fanout_authoring_plan_v1"
    assert plan["workflow_kind"] == "intent_family_v2"
    assert plan["scheduler_boundary"]["forbidden_runtime_branches"] == ["swarm", "fanout", "campaign"]
    assert plan["parallel_groups"] == [
        {
            "group_id": "parallel_exploration",
            "child_uids": [
                "PIS-20260410-180002Z-AAA1",
                "PIS-20260410-180003Z-BBB2",
                "PIS-20260410-180004Z-CCC3",
            ],
        }
    ]

    children = {row["uid"]: row for row in plan["children"]}
    assert children["PIS-20260410-180002Z-AAA1"]["depends_on_uids"] == []
    assert children["PIS-20260410-180003Z-BBB2"]["depends_on_uids"] == []
    assert children["PIS-20260410-180004Z-CCC3"]["depends_on_uids"] == []
    assert children["PIS-20260410-180005Z-INT4"]["depends_on_uids"] == [
        "PIS-20260410-180002Z-AAA1",
        "PIS-20260410-180003Z-BBB2",
        "PIS-20260410-180004Z-CCC3",
    ]
    assert children["PIS-20260410-180006Z-VAL5"]["relationship"] == "intent_validation_suite"
    assert children["PIS-20260410-180006Z-VAL5"]["depends_on_uids"] == ["PIS-20260410-180005Z-INT4"]
    assert children["PIS-20260410-180007Z-CLO6"]["relationship"] == "intent_family_closeout"
    assert children["PIS-20260410-180007Z-CLO6"]["depends_on_uids"] == ["PIS-20260410-180006Z-VAL5"]
    assert plan["parent"]["integration_meta"]["child_uids"] == [row["uid"] for row in plan["children"]]
    assert plan["family_graph"]["required_final_child_uid"] == "PIS-20260410-180007Z-CLO6"
    assert isinstance(plan["authoring_fingerprint"], str) and plan["authoring_fingerprint"]


def test_fanout_authoring_rejects_runtime_dependencies_inside_parallel_group() -> None:
    spec = _spec()
    spec["parallel_children"][0]["depends_on_uids"] = ["PIS-20260410-180003Z-BBB2"]

    with pytest.raises(FanoutAuthoringError, match="parallel_children must not declare dependencies"):
        compile_fanout_authoring_plan(spec)


def test_fanout_authoring_writes_artifact(tmp_path: Path) -> None:
    artifact_rel = Path("runtime/state/controlplane/sched_v2/test_fanout_authoring.json")

    path = write_fanout_authoring_plan_artifact(_spec(), repo_root=tmp_path, artifact_rel=artifact_rel)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / artifact_rel
    assert payload["scheduler_boundary"]["rule"].startswith("fanout is authoring-time only")
