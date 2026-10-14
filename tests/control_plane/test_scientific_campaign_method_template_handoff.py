from __future__ import annotations

from infra.controlplane import scientific_campaign_method_template as method_template


def _link_kinds(phase: dict, field: str) -> set[str]:
    return {
        str(link.get("link_kind") or "")
        for link in list(phase.get(field) or [])
        if isinstance(link, dict)
    }


def test_reference_template_exposes_runtime_handoff_links() -> None:
    reference = method_template.load_reference_scientific_campaign_method_template()
    phases = {
        str(row.get("phase_id") or ""): row
        for row in list(reference.get("phases") or [])
        if isinstance(row, dict)
    }

    prior = phases["prior_knowledge_consolidation"]
    continuation = phases["continuation"]

    assert _link_kinds(prior, "input_linkages") >= {"campaign_phase_record", "evidence_pack_ref"}
    assert _link_kinds(continuation, "output_linkages") >= {"campaign_phase_record", "continuation_packet"}
    assert all(link.get("link_mode") == "reference_only" for link in prior["input_linkages"])
    assert all(link.get("link_mode") == "reference_only" for link in continuation["output_linkages"])


def test_pilot_fixtures_keep_handoff_links_and_pass_advisory_admission() -> None:
    payload = method_template.load_scientific_campaign_method_template_pilot_fixtures()

    for row in list(payload.get("fixtures") or []):
        template = row["method_template"]
        phases = {
            str(phase.get("phase_id") or ""): phase
            for phase in list(template.get("phases") or [])
            if isinstance(phase, dict)
        }
        prior = phases["prior_knowledge_consolidation"]
        continuation = phases["continuation"]

        assert _link_kinds(prior, "input_linkages") >= {"campaign_phase_record", "evidence_pack_ref"}
        assert _link_kinds(continuation, "output_linkages") >= {"campaign_phase_record", "continuation_packet"}
        assert method_template.validate_scientific_campaign_method_template(template)["ok"] is True
        assert method_template.assess_scientific_campaign_method_advisory_admission(template)["warning_count"] == 0


def test_degraded_handoff_template_warns_advisory_but_still_fail_closes() -> None:
    template = method_template.load_reference_scientific_campaign_method_template()
    template["phases"][1]["input_linkages"] = [
        link for link in template["phases"][1]["input_linkages"] if link.get("link_kind") != "evidence_pack_ref"
    ]
    template["phases"][1]["output_linkages"] = [
        link for link in template["phases"][1]["output_linkages"] if link.get("link_kind") != "evidence_pack_ref"
    ]
    template["phases"][7]["continuation_notes"]["stop_conditions"] = []

    advisory = method_template.assess_scientific_campaign_method_advisory_admission(template)
    validation = method_template.validate_scientific_campaign_method_template(template)

    assert advisory["admitted"] is True
    assert {row["code"] for row in advisory["warnings"]} >= {
        "missing_prior_knowledge_evidence_chain",
        "missing_continuation_stop_conditions",
    }
    assert validation["ok"] is False
    assert {row["code"] for row in validation["errors"]} >= {"missing_evidence_pack_link"}
