from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import budget_adaptive_research_policy as contract


REFERENCE_PACK_FINGERPRINT = "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87"


def _policy() -> dict:
    return json.loads(Path("governance/budget_adaptive_research_policy_v1.json").read_text(encoding="utf-8"))


def _fixtures() -> dict:
    return json.loads(Path("governance/budget_adaptive_research_policy_reference_examples_v1.json").read_text(encoding="utf-8"))


def _fixture(fixture_id: str) -> dict:
    return next(row for row in _fixtures()["fixtures"] if row["fixture_id"] == fixture_id)


def _reference_pack_ref() -> dict:
    return {
        "pack_id": "rep-20260410-reference",
        "pack_schema_version": "research_evidence_pack_v1",
        "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
        "pack_fingerprint": REFERENCE_PACK_FINGERPRINT,
        "summary": "Reference evidence pack can be located from durable planning state.",
    }


def test_budget_adaptive_research_policy_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.budget_adaptive_research_policy_contract_v1()

    assert payload.version == "budget_adaptive_research_policy_contract_v1"
    assert payload.uid == "PIS-20260411-081500Z-A4D1"
    assert payload.canonical_artifact_rel.as_posix() == "governance/budget_adaptive_research_policy_v1.json"
    assert payload.reference_fixtures_rel.as_posix() == "governance/budget_adaptive_research_policy_reference_examples_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_budget_adaptive_research_policy_v1.md"
    assert payload.mode_ids == ("micro", "standard", "overnight", "campaign", "expedition")
    assert "deliverable_floors_must_be_present_for_every_mode" in payload.fail_closed_rules


def test_budget_adaptive_research_policy_artifact_matches_runtime_exports() -> None:
    payload = _policy()

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_fixtures_rel"] == contract.REFERENCE_FIXTURES_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["mode_required_fields"]) == contract.MODE_REQUIRED_FIELDS
    assert tuple(payload["canonical_mode_order"]) == contract.MODE_IDS


def test_budget_adaptive_research_policy_validates_all_first_class_modes() -> None:
    result = contract.validate_budget_adaptive_research_policy(_policy())

    assert result["ok"] is True
    assert result["errors"] == []
    assert [row["mode_id"] for row in result["normalized_policy"]["modes"]] == list(contract.MODE_IDS)
    assert result["normalized_policy"]["scaling_law"]["fixed_method_template_phase_ids"] == list(
        contract.scientific_campaign_method_template.PHASE_IDS
    )


def test_budget_adaptive_research_policy_reference_examples_are_self_consistent() -> None:
    assert contract.validate_budget_adaptive_research_policy_reference_examples() == ()


def test_reference_fixture_rejects_under_scoped_micro_behavior() -> None:
    result = contract.validate_reference_fixture(_fixture("under_scoped_micro_missing_floor"), policy=_policy())

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "missing_deliverable_floor" in codes
    assert "under_scoped_hypothesis_count" in codes


def test_reference_fixture_rejects_over_broad_campaign_behavior() -> None:
    result = contract.validate_reference_fixture(_fixture("over_broad_campaign_behaves_like_expedition"), policy=_policy())

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "over_scoped_literature_breadth" in codes
    assert "over_scoped_hypothesis_count" in codes
    assert "over_scoped_verification_strictness" in codes


def test_budget_adaptive_research_policy_rejects_missing_deliverable_floor() -> None:
    raw = _policy()
    raw["modes"][1]["minimum_deliverables"] = []

    result = contract.validate_budget_adaptive_research_policy(raw)

    assert result["ok"] is False
    assert any(row["field"] == "modes[1].minimum_deliverables" and row["code"] == "missing_deliverable_floor" for row in result["errors"])


def test_budget_adaptive_research_policy_contract_v1_returns_no_issues() -> None:
    assert contract.validate_budget_adaptive_research_policy_contract_v1() == ()


def test_normalization_does_not_mutate_input_policy_or_fixture() -> None:
    raw_policy = _policy()
    raw_fixture = _fixture("compliant_micro_brief_synthesis")
    before_policy = copy.deepcopy(raw_policy)
    before_fixture = copy.deepcopy(raw_fixture)

    contract.validate_budget_adaptive_research_policy(raw_policy)
    contract.validate_reference_fixture(raw_fixture, policy=raw_policy)

    assert raw_policy == before_policy
    assert raw_fixture == before_fixture


def test_budget_adaptive_planning_guidance_accepts_evidence_linked_standard_continue() -> None:
    result = contract.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "standard",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Need one more bounded synthesis pass to resolve the shortlist.",
            "continuation_packet_id": "pkt_standard_001",
            "evidence_pack_refs": [_reference_pack_ref()],
            "advisory_only": False,
        },
        repo_root=Path("."),
    )

    receipt = result["receipt"]
    assert result["ok"] is True
    assert result["errors"] == []
    assert receipt["policy_state"] == "accepted"
    assert receipt["convergence_posture"] == "bounded_convergence"
    assert receipt["wave_cap"] == 2
    assert receipt["effective_continuation_decision"] == "continue"


def test_budget_adaptive_planning_guidance_rejects_over_wave_cap_when_not_advisory() -> None:
    result = contract.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "micro",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Try one more wave anyway.",
            "advisory_only": False,
        }
    )

    assert result["ok"] is False
    assert result["receipt"]["policy_state"] == "rejected"
    assert result["receipt"]["effective_continuation_decision"] == "stop"
    assert "over_wave_cap" in result["receipt"]["reason_codes"]


def test_budget_adaptive_planning_guidance_downgrades_proof_blind_continuation_in_advisory_mode() -> None:
    result = contract.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "overnight",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Need broader synthesis before converging.",
            "advisory_only": True,
        }
    )

    assert result["ok"] is False
    assert result["receipt"]["policy_state"] == "downgraded"
    assert result["receipt"]["effective_continuation_decision"] == "stop"
    assert result["receipt"]["reason_codes"] == ["proof_blind_continuation"]


def test_budget_adaptive_planning_guidance_rejects_premature_campaign_stop_without_durable_refs() -> None:
    result = contract.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "campaign",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "stop",
            "continuation_rationale": "Stop now.",
            "advisory_only": False,
        }
    )

    assert result["ok"] is False
    assert result["receipt"]["policy_state"] == "rejected"
    assert result["receipt"]["effective_continuation_decision"] == "continue"
    assert "premature_convergence" in result["receipt"]["reason_codes"]
