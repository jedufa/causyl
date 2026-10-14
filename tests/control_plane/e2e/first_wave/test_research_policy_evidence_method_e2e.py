from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import budget_adaptive_research_policy as budget_policy
from infra.controlplane import scientific_campaign_method_template as method_template

from .helpers.contracts import ScenarioContract, assert_scenario_contract


CONTRACT = ScenarioContract(
    scenario_id="research_policy_evidence_method_e2e",
    operator_job="Plan bounded research work with the right rigor for the budget while preserving evidence and method handoff structure.",
    capability="Budget-adaptive planning guidance consumes durable evidence refs and works against a canonical scientific method template with continuation-aware outputs.",
    durable_truth=(
        "scientific method template artifact",
        "budget-adaptive policy artifact",
        "evidence pack reference",
        "planning guidance receipt",
    ),
    manual_repair_eliminated="Guessing the right rigor level and handoff structure from budget alone without durable evidence or method constraints.",
    primary_intent_sheets=("IS-12", "IS-13", "IS-14"),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "reference template and evidence-pack fixtures",
        "direct planning-guidance evaluation when no richer plane entrypoint exists yet",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "fabricating evidence-pack provenance",
        "writing projection records manually",
    ),
)


REFERENCE_PACK_REF = {
    "pack_id": "rep-20260410-reference",
    "pack_schema_version": "research_evidence_pack_v1",
    "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
    "pack_fingerprint": "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87",
    "summary": "Reference evidence pack can be located from durable planning state.",
}


def test_research_policy_evidence_method_e2e() -> None:
    assert_scenario_contract(CONTRACT)
    raw_template = json.loads(Path("governance/scientific_campaign_method_template_reference_v1.json").read_text(encoding="utf-8"))
    validated_template = method_template.validate_scientific_campaign_method_template(raw_template)
    assert validated_template["ok"] is True

    prior_knowledge = next(
        row for row in validated_template["normalized_template"]["phases"] if row["phase_id"] == "prior_knowledge_consolidation"
    )
    continuation = next(
        row for row in validated_template["normalized_template"]["phases"] if row["phase_id"] == "continuation"
    )
    assert [row["link_kind"] for row in prior_knowledge["input_linkages"]] == [
        "campaign_phase_record",
        "evidence_pack_ref",
    ]
    assert [row["link_kind"] for row in continuation["output_linkages"]] == [
        "campaign_phase_record",
        "continuation_packet",
    ]

    accepted = budget_policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "standard",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Need one more bounded synthesis pass to resolve the shortlist.",
            "continuation_packet_id": "pkt_standard_001",
            "evidence_pack_refs": [REFERENCE_PACK_REF],
            "advisory_only": False,
        },
        repo_root=Path("."),
    )
    receipt = accepted["receipt"]
    assert accepted["ok"] is True
    assert receipt["policy_state"] == "accepted"
    assert receipt["convergence_posture"] == "bounded_convergence"
    assert receipt["effective_continuation_decision"] == "continue"
    assert receipt["has_durable_proof"] is True
    assert receipt["evidence_pack_refs"][0]["pack_artifact_ref"] == "governance/research_evidence_pack_reference_v1.json"

    rejected = budget_policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "campaign",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "stop",
            "continuation_rationale": "Stop now.",
            "advisory_only": False,
        }
    )
    assert rejected["ok"] is False
    assert rejected["receipt"]["policy_state"] == "rejected"
    assert rejected["receipt"]["effective_continuation_decision"] == "continue"
    assert "premature_convergence" in rejected["receipt"]["reason_codes"]
