from __future__ import annotations

from infra.controlplane import campaign_phase_synthesis


def _seed_plan() -> dict:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_intent_to_campaign_demo_v2",
        "plan_version": "1.1.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Intent To Campaign Compiler Demo",
            "created_at_utc": "2026-04-11T12:10:03Z",
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "ledger_refs": [
                "PIS-20260411-113558Z-0B60",
                "PIS-20260411-113559Z-8B17",
            ],
        },
        "vocabulary_boundary": {
            "intent_sheet": [
                "operator goal framing",
                "mission non-goals",
                "allowed broad ambiguity",
            ],
            "campaign_plan": [
                "phased plan structure",
                "review gate placement",
                "named ambiguity policy",
            ],
            "pis": [
                "bounded implementation slice",
                "proof target",
                "narrow acceptance surface",
            ],
            "job": [
                "router payload",
                "runtime receipt",
                "execution lifecycle",
            ],
        },
        "ambiguity_policy": {
            "allowed_in_campaign_plan": [],
            "must_resolve_before_pis": [],
            "named_contract_violation": "campaign_plan_contract_violation",
        },
        "provenance": {
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "upstream_contract_refs": [
                "governance/campaign_record_contract_v1.json",
                "governance/scientific_campaign_method_template_v1.json",
                "governance/budget_adaptive_research_policy_v1.json",
            ],
            "supporting_artifact_refs": [
                "governance/ledger/intent_sheets/causyl/verified/2026/04/IS-20260403-212914Z-C623__intent-sheet-to-campaign-plan-compiler/intent_sheet.md"
            ],
            "lineage_hash": "sha256:intent_to_campaign_demo_v2",
        },
    }


def _valid_request() -> dict:
    return {
        "schema_version": "campaign_phase_synthesis_request_v1",
        "campaign_plan_seed": _seed_plan(),
        "mission_scope": "multi_stage_campaign",
        "intent_summary": "Turn broad operator research intent into a phased campaign plan with bounded child-PIS handoffs.",
        "operator_constraints": [
            "Keep the planning layer advisory and non-executable.",
            "Expose unresolved ambiguity instead of inventing certainty.",
            "Preserve explicit review gates before downstream PIS generation.",
        ],
        "required_outputs": [
            "phase map",
            "ambiguity register",
            "review-gate note",
            "child-PIS readiness note",
        ],
        "decomposition_triggers": [
            "broad intent spans more than one independent proof boundary",
            "later child slices need different acceptance targets",
        ],
        "review_gate_topics": [
            "scope approval",
            "decomposition approval",
        ],
        "requested_budget_mode": "campaign",
        "ambiguities": [
            {
                "topic": "Exact experiment sequence may remain open until prior-knowledge review is complete.",
                "resolution_timing": "allowed_in_campaign_plan",
            },
            {
                "topic": "Target phase owner must be named before any child PIS is emitted.",
                "resolution_timing": "must_resolve_before_pis",
            },
            {
                "topic": "Concrete validation surface for each child PIS must be fixed before handoff.",
                "resolution_timing": "must_resolve_before_pis",
            },
        ],
    }


def test_compile_campaign_phase_plan_returns_bounded_multi_phase_plan() -> None:
    result = campaign_phase_synthesis.compile_campaign_phase_plan(_valid_request())

    assert result["ok"] is True
    assert result["status"] == "compiled"
    assert result["reason_codes"] == []
    assert result["computed_fingerprint"] != ""
    assert result["phase_generation_trace"] == [
        "validated_request_and_campaign_plan_seed",
        "selected_multi_phase_path",
        "attached_explicit_review_gate_and_ambiguity_policy",
    ]

    plan = result["campaign_plan"]
    assert plan is not None
    assert [phase["phase_id"] for phase in plan["phases"]] == [
        "phase_01_bound_scope_and_review",
        "phase_02_design_and_decomposition_gate",
        "phase_03_prepare_child_pis_handoff",
    ]
    assert any(
        "review gate" in item.lower()
        for phase in plan["phases"]
        for item in phase["deliverables"]
    )
    assert "Exact experiment sequence may remain open until prior-knowledge review is complete." in plan["ambiguity_policy"][
        "allowed_in_campaign_plan"
    ]
    assert "Target phase owner must be named before any child PIS is emitted." in plan["ambiguity_policy"][
        "must_resolve_before_pis"
    ]
    assert plan["phases"][-1]["pis_readiness"] == "ready_for_pis_generation"


def test_compile_campaign_phase_plan_returns_clarification_required_for_under_bounded_open_ended_intent() -> None:
    request = _valid_request()
    request["mission_scope"] = "open_ended_program"
    request["operator_constraints"] = ["Keep it useful."]
    request["decomposition_triggers"] = []

    result = campaign_phase_synthesis.compile_campaign_phase_plan(request)

    assert result["ok"] is False
    assert result["status"] == "clarification_required"
    assert result["campaign_plan"] is None
    assert "under_bounded_open_ended_plan" in result["reason_codes"]


def test_validate_phase_synthesis_candidate_rejects_single_phase_collapse() -> None:
    request = _valid_request()
    candidate = _seed_plan()
    candidate["ambiguity_policy"] = {
        "allowed_in_campaign_plan": [
            "Exact experiment sequence may remain open until prior-knowledge review is complete."
        ],
        "must_resolve_before_pis": [
            "Target phase owner must be named before any child PIS is emitted.",
            "Concrete validation surface for each child PIS must be fixed before handoff.",
        ],
        "named_contract_violation": "campaign_plan_contract_violation",
    }
    candidate["phases"] = [
        {
            "phase_id": "phase_01_everything_everywhere",
            "label": "Do the entire campaign in one shot",
            "goal": "Collapse broad intent into one giant planning phase.",
            "deliverables": [
                "phase map",
                "child-PIS mapping",
            ],
            "method_template_phase_ids": [
                "question_framing",
                "prior_knowledge_consolidation",
                "hypothesis_registry",
                "experiment_design",
                "analysis_plan",
                "adversarial_review",
                "continuation",
            ],
            "budget_mode": "campaign",
            "allowed_ambiguities": [
                "Exact experiment sequence may remain open until prior-knowledge review is complete."
            ],
            "must_resolve_before_pis": [],
            "pis_readiness": "ready_for_pis_generation",
        }
    ]

    result = campaign_phase_synthesis.validate_phase_synthesis_candidate(request, candidate)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "single_phase_collapse_forbidden" in result["reason_codes"]


def test_validate_phase_synthesis_candidate_rejects_erased_allowed_ambiguity() -> None:
    request = _valid_request()
    candidate = _seed_plan()
    candidate["ambiguity_policy"] = {
        "allowed_in_campaign_plan": [],
        "must_resolve_before_pis": [
            "Target phase owner must be named before any child PIS is emitted.",
            "Concrete validation surface for each child PIS must be fixed before handoff.",
        ],
        "named_contract_violation": "campaign_plan_contract_violation",
    }
    candidate["phases"] = [
        {
            "phase_id": "phase_01_bound_scope_and_review",
            "label": "Bound scope and review",
            "goal": "Set the campaign boundary.",
            "deliverables": [
                "campaign scope review gate",
                "constraint register",
            ],
            "method_template_phase_ids": [
                "question_framing",
                "prior_knowledge_consolidation",
            ],
            "budget_mode": "campaign",
            "allowed_ambiguities": [],
            "must_resolve_before_pis": [],
            "pis_readiness": "advisory_only",
        },
        {
            "phase_id": "phase_02_prepare_child_pis_handoff",
            "label": "Prepare child PIS handoff",
            "goal": "Pretend the ambiguity is gone.",
            "deliverables": [
                "candidate child-PIS set",
                "handoff readiness note",
            ],
            "method_template_phase_ids": [
                "adversarial_review",
                "continuation",
            ],
            "budget_mode": "standard",
            "allowed_ambiguities": [],
            "must_resolve_before_pis": [],
            "pis_readiness": "ready_for_pis_generation",
        },
    ]

    result = campaign_phase_synthesis.validate_phase_synthesis_candidate(request, candidate)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "ambiguity_erased_too_early" in result["reason_codes"]
