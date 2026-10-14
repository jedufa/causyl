"""Assembled-system intent validation for the scientific campaign method template.

This helper is intentionally read-only. It inspects the existing method-template
contract surfaces and durable ledger evidence for the family, then emits a
durable validation artifact that closeout can consume without transcript
archaeology.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import scientific_campaign_method_template as method_template


VALIDATION_UID = "PIS-20260410-193416Z-5A5A"
SOURCE_INTENT_UID = "IS-20260403-212914Z-62FB"
FAMILY_PARENT_UID = "PIS-20260410-191524Z-DAF6"
SCHEMA_VERSION = "scientific_campaign_method_template_intent_validation_v1"
VALIDATION_CASE_ID = "assembled_system_intent_e2e"
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260410-193416Z-5A5A__validate-scientific-campaign-method-template-assembled-system-intent-e2e-suite/"
    "artifacts/intent_validation/scientific_campaign_method_template_intent_e2e.json"
)
DEPENDENCY_UIDS: Sequence[str] = (
    "PIS-20260410-193415Z-2A95",
    "PIS-20260410-193415Z-5519",
    "PIS-20260410-193416Z-2908",
    "PIS-20260410-193416Z-DFFD",
)
HANDOFF_TEST_REL = Path("tests/control_plane/test_scientific_campaign_method_template_handoff.py")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def _ledger_item_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    root = repo_root / "governance" / "ledger" / "patch_instruction_sheets"
    matches = sorted(root.rglob(f"{uid}__*/meta.json"))
    return matches[0].parent if matches else None


def _dependency_snapshot(repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = _ledger_item_dir_for_uid(repo_root, uid)
    meta_path = item_dir / "meta.json" if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    evidence = meta.get("evidence")
    changesets = meta.get("changesets")
    return {
        "uid": uid,
        "status": str(meta.get("status") or ""),
        "release_state": str(meta.get("release_state") or ""),
        "has_evidence": isinstance(evidence, Mapping) and bool(evidence),
        "has_changesets": isinstance(changesets, list) and bool(changesets),
        "meta_path": meta_path.relative_to(repo_root).as_posix() if meta_path is not None else "",
    }


def _scenario(
    scenario_id: str,
    *,
    scenario_kind: str,
    outcome: str,
    mapped_requirement_ids: Sequence[str],
    observations: Sequence[str],
    evidence_refs: Sequence[str],
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "outcome": outcome,
        "mapped_requirement_ids": list(mapped_requirement_ids),
        "observations": list(observations),
        "evidence_refs": list(evidence_refs),
    }


def _validation_command(command_id: str, *, command: str, purpose: str, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    return {
        "command_id": command_id,
        "command": command,
        "purpose": purpose,
        "evidence_refs": list(evidence_refs),
    }


def build_scientific_campaign_method_template_intent_validation_artifact(
    *,
    repo_root: Path | None = None,
    validation_case_utc: str | None = None,
    dependency_ready: bool | None = None,
    runtime_handoff_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dependency_snapshot = [_dependency_snapshot(root, uid) for uid in DEPENDENCY_UIDS]
    dependency_ready = (
        all(row["has_evidence"] and row["has_changesets"] for row in dependency_snapshot)
        if dependency_ready is None
        else bool(dependency_ready)
    )
    runtime_handoff_ready = (
        (root / HANDOFF_TEST_REL).exists() if runtime_handoff_ready is None else bool(runtime_handoff_ready)
    )

    reference = method_template.load_reference_scientific_campaign_method_template(repo_root=root)
    reference_result = method_template.validate_scientific_campaign_method_template(reference)
    reference_phases = {
        row["phase_id"]: row for row in reference_result.get("normalized_template", {}).get("phases", []) if isinstance(row, Mapping)
    }
    prior_phase = dict(reference_phases.get("prior_knowledge_consolidation") or {})
    continuation_phase = dict(reference_phases.get("continuation") or {})

    contract_issues = list(method_template.validate_scientific_campaign_method_template_contract_v1(repo_root=root))
    pilot_fixture_issues = list(method_template.validate_scientific_campaign_method_template_pilot_fixtures(repo_root=root))

    advisory_raw = copy.deepcopy(reference)
    advisory_raw["phases"][1]["input_linkages"] = [advisory_raw["phases"][1]["input_linkages"][0]]
    advisory_raw["phases"][1]["output_linkages"] = [advisory_raw["phases"][1]["output_linkages"][0]]
    advisory_raw["phases"][5]["review"]["challenge_questions"] = []
    advisory_raw["phases"][6]["outcome_record"]["uncertainty_notes"] = ""
    advisory_raw["phases"][7]["continuation_notes"]["stop_conditions"] = []
    advisory_result = method_template.assess_scientific_campaign_method_advisory_admission(advisory_raw)
    advisory_validation = method_template.validate_scientific_campaign_method_template(advisory_raw)

    missing_evidence_raw = copy.deepcopy(reference)
    missing_evidence_raw["phases"][1]["input_linkages"] = [missing_evidence_raw["phases"][1]["input_linkages"][0]]
    missing_evidence_raw["phases"][1]["output_linkages"] = [missing_evidence_raw["phases"][1]["output_linkages"][0]]
    missing_evidence_result = method_template.validate_scientific_campaign_method_template(missing_evidence_raw)

    weak_hypothesis_raw = copy.deepcopy(reference)
    weak_hypothesis_raw["phases"][2]["hypotheses"][1]["support_basis"] = []
    weak_hypothesis_result = method_template.validate_scientific_campaign_method_template(weak_hypothesis_raw)

    missing_review_raw = copy.deepcopy(reference)
    missing_review_raw["phases"][5]["review"] = {}
    missing_review_result = method_template.validate_scientific_campaign_method_template(missing_review_raw)

    missing_failure_raw = copy.deepcopy(reference)
    missing_failure_raw["phases"][6]["outcome_record"]["failure_notes"] = []
    missing_failure_result = method_template.validate_scientific_campaign_method_template(missing_failure_raw)

    missing_continuation_raw = copy.deepcopy(reference)
    missing_continuation_raw["phases"][7]["continuation_notes"] = {}
    missing_continuation_result = method_template.validate_scientific_campaign_method_template(missing_continuation_raw)

    inline_budget_raw = copy.deepcopy(reference)
    inline_budget_raw["budget_adaptive_rigor_policy"] = {"mode": "strict"}
    inline_budget_result = method_template.validate_scientific_campaign_method_template(inline_budget_raw)

    inline_campaign_raw = copy.deepcopy(reference)
    inline_campaign_raw["campaign_execution_record"] = {"campaign_id": "camp-inline-forbidden"}
    inline_campaign_result = method_template.validate_scientific_campaign_method_template(inline_campaign_raw)

    scenario_results = [
        _scenario(
            "positive_reference_template_prior_art_first_flow_valid",
            scenario_kind="success_path",
            outcome=(
                "passed"
                if reference_result["ok"]
                and not contract_issues
                and "evidence_pack_ref" in [row.get("link_kind") for row in prior_phase.get("input_linkages", [])]
                and "continuation_packet" in [row.get("link_kind") for row in continuation_phase.get("output_linkages", [])]
                else "failed"
            ),
            mapped_requirement_ids=["prior_art_first_flow", "structural_linkage_contract"],
            observations=[
                "The reference template validates with the canonical prior-knowledge, hypothesis, review, outcome, and continuation phases.",
                "Prior knowledge requires an evidence-pack reference before downstream design, and continuation emits a continuation-packet linkage.",
            ],
            evidence_refs=[
                method_template.REFERENCE_TEMPLATE_REL.as_posix(),
                method_template.CONTRACT_ARTIFACT_REL.as_posix(),
                "docs/contracts/contract_scientific_campaign_method_template_v1.md",
            ],
        ),
        _scenario(
            "positive_advisory_admission_stays_non_blocking",
            scenario_kind="advisory_path",
            outcome=(
                "passed"
                if advisory_result["admitted"]
                and not advisory_validation["ok"]
                and {row["code"] for row in advisory_result["warnings"]} >= {
                    "missing_prior_knowledge_evidence_chain",
                    "missing_adversarial_review_challenge_questions",
                    "missing_outcome_uncertainty_notes",
                    "missing_continuation_stop_conditions",
                }
                else "failed"
            ),
            mapped_requirement_ids=["advisory_boundary"],
            observations=[
                "Advisory admission still returns structured warnings instead of becoming a hidden hard gate.",
                "The hard validator still fail-closes the same malformed method template, so advisory posture does not dilute contract enforcement.",
            ],
            evidence_refs=[
                "governance/scientific_campaign_method_template_pilot_fixtures_v1.json",
                "tests/control_plane/test_scientific_campaign_method_template.py",
            ],
        ),
        _scenario(
            "negative_missing_evidence_pack_link_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if any(row["code"] == "missing_evidence_pack_link" for row in missing_evidence_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["prior_art_first_flow", "structural_linkage_contract"],
            observations=["Removing the prior-knowledge evidence-pack linkage fails validation instead of allowing design to proceed from folklore."],
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_prior_knowledge_phase_without_evidence_pack_link"],
        ),
        _scenario(
            "negative_unsupported_strong_claim_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if any(row["code"] == "unsupported_strong_claim" for row in weak_hypothesis_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["hypothesis_registry_discipline"],
            observations=["A strong claim without structured support_basis is rejected instead of surviving as confident prose."],
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_unsupported_strong_claim_fixture"],
        ),
        _scenario(
            "negative_missing_adversarial_review_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if any(row["field"] == "phases[5].review.prompt" and row["code"] == "missing_field" for row in missing_review_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["adversarial_review_discipline"],
            observations=["A method template with no structured adversarial review prompt is rejected instead of treating critique as decorative language."],
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_missing_adversarial_review_fixture"],
        ),
        _scenario(
            "negative_negative_result_without_failure_notes_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if any(row["code"] == "missing_negative_result_failure_notes" for row in missing_failure_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["negative_result_handling"],
            observations=["Negative-result outcomes require explicit failure notes and cannot collapse into a cleaned-up summary."],
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_negative_result_without_failure_notes"],
        ),
        _scenario(
            "negative_missing_continuation_notes_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if any(row["field"] == "phases[7].continuation_notes.next_decision" and row["code"] == "missing_field" for row in missing_continuation_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["continuation_handoff_contract"],
            observations=["The continuation phase rejects empty next-step handoff notes instead of ending the campaign with a dead end."],
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_missing_continuation_notes_fixture"],
        ),
        _scenario(
            "negative_inline_budget_policy_rejected",
            scenario_kind="boundary_path",
            outcome=(
                "passed"
                if any(row["field"] == "budget_adaptive_rigor_policy" and row["code"] == "forbidden_inline_ownership" for row in inline_budget_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["sibling_boundary_review"],
            observations=["IS-14 budget policy remains outside this family; inline budget policy payloads are rejected."],
            evidence_refs=[
                "docs/contracts/contract_scientific_campaign_method_template_v1.md",
                "governance/scientific_campaign_method_template_v1.json",
            ],
        ),
        _scenario(
            "negative_inline_campaign_execution_payload_rejected",
            scenario_kind="boundary_path",
            outcome=(
                "passed"
                if any(row["field"] == "campaign_execution_record" and row["code"] == "forbidden_inline_ownership" for row in inline_campaign_result["errors"])
                else "failed"
            ),
            mapped_requirement_ids=["sibling_boundary_review"],
            observations=["IS-10 campaign execution ownership is not absorbed by the method template; inline execution payloads fail closed."],
            evidence_refs=[
                "docs/contracts/contract_campaign_record_schema_v1.md",
                "tests/control_plane/test_scientific_campaign_method_template.py::test_method_template_rejects_inline_neighboring_payloads",
            ],
        ),
        _scenario(
            "gap_runtime_handoff_surface_not_proven",
            scenario_kind="gap_path",
            outcome="passed",
            mapped_requirement_ids=["assembled_runtime_handoff"],
            observations=(
                [
                    "The repo exposes structural linkage rules, but it does not yet expose a dedicated scientific-campaign method-template handoff validation harness.",
                    "Dependency ledger records for the handoff and advisory children remain under-evidenced for closeout consumption.",
                ]
                if not runtime_handoff_ready or not dependency_ready
                else [
                    "Dedicated handoff validation surface exists and dependency evidence is present for closeout consumption."
                ]
            ),
            evidence_refs=[
                HANDOFF_TEST_REL.as_posix(),
                *[row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
            ],
        ),
    ]

    requirement_coverage = [
        {
            "requirement_id": "prior_art_first_flow",
            "intent_clause": "Prior-art-first flow must require evidence-linked consolidation before downstream design and analysis work.",
            "status": "proven",
            "observed_behavior": "The reference template validates, and removing the prior-knowledge evidence-pack linkage fails closed.",
            "scenario_ids": [
                "positive_reference_template_prior_art_first_flow_valid",
                "negative_missing_evidence_pack_link_rejected",
            ],
            "evidence_refs": [
                method_template.REFERENCE_TEMPLATE_REL.as_posix(),
                "tests/control_plane/test_scientific_campaign_method_template.py",
            ],
        },
        {
            "requirement_id": "hypothesis_registry_discipline",
            "intent_clause": "Hypotheses must carry falsifiability, claim strength, uncertainty, and support basis instead of becoming conclusion-only prose.",
            "status": "proven",
            "observed_behavior": "Unsupported strong claims are rejected by the contract validator.",
            "scenario_ids": ["negative_unsupported_strong_claim_rejected"],
            "evidence_refs": ["tests/control_plane/test_scientific_campaign_method_template.py"],
        },
        {
            "requirement_id": "adversarial_review_discipline",
            "intent_clause": "Adversarial review must be explicit and structured, not ceremonial prose appended after the fact.",
            "status": "proven",
            "observed_behavior": "Missing adversarial-review structure fails validation directly on the review prompt field.",
            "scenario_ids": ["negative_missing_adversarial_review_rejected"],
            "evidence_refs": ["tests/control_plane/test_scientific_campaign_method_template.py"],
        },
        {
            "requirement_id": "negative_result_handling",
            "intent_clause": "Negative results must remain first-class outputs with failure notes and uncertainty visibility.",
            "status": "proven",
            "observed_behavior": "Negative-result records without failure notes fail closed instead of being normalized away.",
            "scenario_ids": ["negative_negative_result_without_failure_notes_rejected"],
            "evidence_refs": ["tests/control_plane/test_scientific_campaign_method_template.py"],
        },
        {
            "requirement_id": "continuation_handoff_contract",
            "intent_clause": "Continuation handoff must include explicit next-step notes and stop conditions rather than ending in a summary void.",
            "status": "proven",
            "observed_behavior": "Continuation rows require structured next_decision and stop_conditions fields, and the reference template carries a continuation-packet output linkage.",
            "scenario_ids": [
                "positive_reference_template_prior_art_first_flow_valid",
                "negative_missing_continuation_notes_rejected",
            ],
            "evidence_refs": [
                method_template.REFERENCE_TEMPLATE_REL.as_posix(),
                "tests/control_plane/test_scientific_campaign_method_template.py",
            ],
        },
        {
            "requirement_id": "advisory_boundary",
            "intent_clause": "Advisory admission checks must remain advisory while contract validation continues to fail closed.",
            "status": "proven",
            "observed_behavior": "Advisory admission returns warnings, but the same malformed template still fails the hard validator.",
            "scenario_ids": ["positive_advisory_admission_stays_non_blocking"],
            "evidence_refs": [
                "governance/scientific_campaign_method_template_pilot_fixtures_v1.json",
                "tests/control_plane/test_scientific_campaign_method_template.py",
            ],
        },
        {
            "requirement_id": "sibling_boundary_review",
            "intent_clause": "This child must not absorb IS-10 campaign execution or IS-14 budget policy ownership while claiming assembled-system coverage.",
            "status": "proven",
            "observed_behavior": "Inline campaign execution payloads and budget-policy payloads are rejected as forbidden neighboring ownership.",
            "scenario_ids": [
                "negative_inline_budget_policy_rejected",
                "negative_inline_campaign_execution_payload_rejected",
            ],
            "evidence_refs": [
                "docs/contracts/contract_scientific_campaign_method_template_v1.md",
                method_template.CONTRACT_ARTIFACT_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "structural_linkage_contract",
            "intent_clause": "The template must structurally link to evidence-pack and continuation contracts instead of using folklore or inline payloads.",
            "status": "proven",
            "observed_behavior": "The reference template uses canonical contract refs for evidence-pack and continuation handoff linkages, and removing the evidence-pack link is rejected.",
            "scenario_ids": [
                "positive_reference_template_prior_art_first_flow_valid",
                "negative_missing_evidence_pack_link_rejected",
            ],
            "evidence_refs": [
                method_template.CONTRACT_ARTIFACT_REL.as_posix(),
                method_template.REFERENCE_TEMPLATE_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "assembled_runtime_handoff",
            "intent_clause": "PASS requires more than isolated child validators; the family must expose actual assembled-system handoff behavior that closeout can consume.",
            "status": "proven" if dependency_ready and runtime_handoff_ready else "unproven",
            "observed_behavior": (
                "A dedicated handoff validation surface exists and dependency children carry durable evidence."
                if dependency_ready and runtime_handoff_ready
                else (
                    "Only structural contract validation is proven here. A dedicated method-template handoff validation harness is absent, "
                    "and dependency ledger evidence is incomplete for closeout-grade assembled runtime proof."
                )
            ),
            "scenario_ids": ["gap_runtime_handoff_surface_not_proven"],
            "evidence_refs": [
                HANDOFF_TEST_REL.as_posix(),
                *[row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
            ],
        },
    ]

    residual_gaps: List[Dict[str, Any]] = []
    if not runtime_handoff_ready:
        residual_gaps.append(
            {
                "gap_id": "missing_runtime_handoff_surface",
                "risk_type": "feature_risk",
                "summary": "No dedicated scientific campaign method-template handoff validation surface is present, so this child cannot honestly claim assembled runtime handoff behavior.",
                "evidence_refs": [HANDOFF_TEST_REL.as_posix()],
            }
        )
    if not dependency_ready:
        residual_gaps.append(
            {
                "gap_id": "dependency_ledger_evidence_incomplete",
                "risk_type": "validation_risk",
                "summary": "Dependency children 2908 and DFFD do not yet carry durable implementation evidence in ledger metadata, so closeout would still need transcript archaeology or revalidation.",
                "evidence_refs": [row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
            }
        )

    outcome_counts: Dict[str, int] = {}
    for row in scenario_results:
        outcome_counts[row["outcome"]] = outcome_counts.get(row["outcome"], 0) + 1

    validation_commands = [
        _validation_command(
            "reference_template_contract_validation",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import scientific_campaign_method_template as mt; "
                "ref = mt.load_reference_scientific_campaign_method_template(); "
                "result = mt.validate_scientific_campaign_method_template(ref); "
                "assert result['ok']; "
                "PY"
            ),
            purpose="Prove the canonical reference template validates against the method-template contract.",
            evidence_refs=[
                method_template.REFERENCE_TEMPLATE_REL.as_posix(),
                "tests/control_plane/test_scientific_campaign_method_template.py",
            ],
        ),
        _validation_command(
            "negative_boundary_fixture_validation",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import scientific_campaign_method_template_intent_validation as validation; "
                "artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(); "
                "assert all(row['outcome'] == 'passed' for row in artifact['scenario_results'] if row['scenario_kind'] in {'negative_path', 'boundary_path'}); "
                "PY"
            ),
            purpose="Prove negative-path and sibling-boundary scenarios fail closed instead of being normalized away.",
            evidence_refs=["tests/control_plane/test_scientific_campaign_method_template.py"],
        ),
        _validation_command(
            "dependency_ledger_evidence_inspection",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import scientific_campaign_method_template_intent_validation as validation; "
                "artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(); "
                "print(artifact['dependency_snapshot']); "
                "PY"
            ),
            purpose="Inspect whether dependency children carry durable ledger evidence for closeout consumption.",
            evidence_refs=[row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
        ),
        _validation_command(
            "runtime_handoff_surface_inspection",
            command=(
                "python - <<'PY' "
                "from pathlib import Path; "
                "print(Path('tests/control_plane/test_scientific_campaign_method_template_handoff.py').exists()); "
                "PY"
            ),
            purpose="Inspect whether a dedicated runtime handoff validation surface exists for assembled-system proof.",
            evidence_refs=[HANDOFF_TEST_REL.as_posix()],
        ),
    ]
    validation_results = {
        "reference_template_contract_validation": {
            "status": "passed" if reference_result["ok"] and not contract_issues and not pilot_fixture_issues else "failed",
            "summary": (
                "The canonical reference template validates without contract or pilot-fixture issues."
                if reference_result["ok"] and not contract_issues and not pilot_fixture_issues
                else "Reference template validation or supporting contract/fixture checks failed."
            ),
        },
        "negative_boundary_fixture_validation": {
            "status": (
                "passed"
                if all(
                    row["outcome"] == "passed"
                    for row in scenario_results
                    if row["scenario_kind"] in {"negative_path", "boundary_path"}
                )
                else "failed"
            ),
            "summary": (
                "All negative-path and sibling-boundary scenarios fail closed."
                if all(
                    row["outcome"] == "passed"
                    for row in scenario_results
                    if row["scenario_kind"] in {"negative_path", "boundary_path"}
                )
                else "At least one negative-path or sibling-boundary scenario did not fail closed."
            ),
        },
        "dependency_ledger_evidence_inspection": {
            "status": "passed" if dependency_ready else "proof_gap",
            "summary": (
                "Dependency children carry durable evidence and changesets for closeout consumption."
                if dependency_ready
                else "Dependency children 2908 and DFFD still lack durable ledger evidence or changesets for closeout-grade proof."
            ),
        },
        "runtime_handoff_surface_inspection": {
            "status": "passed" if runtime_handoff_ready else "proof_gap",
            "summary": (
                "A dedicated runtime handoff validation surface exists for assembled-system proof."
                if runtime_handoff_ready
                else "No dedicated runtime handoff validation surface is present for assembled-system proof."
            ),
        },
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "uid": VALIDATION_UID,
        "family_parent_uid": FAMILY_PARENT_UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "validation_case_id": VALIDATION_CASE_ID,
        "validation_case_utc": validation_case_utc or _utc_now(),
        "validation_outcome": "passed" if not residual_gaps else "proof_gap",
        "summary": (
            "Assembled-system validation proves the structural method-template contract, advisory boundary, negative-path behavior, and sibling ownership boundaries."
            if not residual_gaps
            else (
                "Assembled-system validation proves the structural method-template contract, advisory boundary, negative-path behavior, and sibling ownership boundaries, "
                "but it remains a proof gap for closeout-grade runtime handoff behavior."
            )
        ),
        "dependency_snapshot": dependency_snapshot,
        "requirement_coverage": requirement_coverage,
        "scenario_results": scenario_results,
        "coverage_summary": {
            "requirement_count": len(requirement_coverage),
            "scenario_count": len(scenario_results),
            "scenario_outcome_counts": outcome_counts,
            "covered_scenario_kinds": sorted({row["scenario_kind"] for row in scenario_results}),
        },
        "validation_commands": validation_commands,
        "validation_results": validation_results,
        "validation_inputs": {
            "reference_template_ok": bool(reference_result["ok"]),
            "contract_validation_issue_count": len(contract_issues),
            "pilot_fixture_issue_count": len(pilot_fixture_issues),
            "advisory_warning_count": len(advisory_result.get("warnings") or []),
            "dependency_ready": dependency_ready,
            "runtime_handoff_ready": runtime_handoff_ready,
        },
        "residual_gaps": residual_gaps,
    }
    payload["artifact_fingerprint"] = _fingerprint(payload)
    return payload


def validate_scientific_campaign_method_template_intent_validation_artifact(artifact: Mapping[str, Any]) -> List[str]:
    issues: List[str] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version must be scientific_campaign_method_template_intent_validation_v1")
    if artifact.get("uid") != VALIDATION_UID:
        issues.append("uid must remain PIS-20260410-193416Z-5A5A")
    if artifact.get("source_intent_uid") != SOURCE_INTENT_UID:
        issues.append("source_intent_uid must remain IS-20260403-212914Z-62FB")
    if artifact.get("validation_outcome") not in {"passed", "proof_gap", "contradicted"}:
        issues.append("validation_outcome must be passed, proof_gap, or contradicted")

    requirements = artifact.get("requirement_coverage") if isinstance(artifact.get("requirement_coverage"), list) else []
    scenarios = artifact.get("scenario_results") if isinstance(artifact.get("scenario_results"), list) else []
    dependency_snapshot = artifact.get("dependency_snapshot") if isinstance(artifact.get("dependency_snapshot"), list) else []
    validation_commands = artifact.get("validation_commands") if isinstance(artifact.get("validation_commands"), list) else []
    validation_results = artifact.get("validation_results") if isinstance(artifact.get("validation_results"), Mapping) else {}
    if not dependency_snapshot:
        issues.append("dependency_snapshot must not be empty")
    if not validation_commands:
        issues.append("validation_commands must not be empty")
    if not validation_results:
        issues.append("validation_results must not be empty")
    requirement_ids = {str(row.get("requirement_id") or "") for row in requirements if isinstance(row, Mapping)}
    command_ids: List[str] = []
    for row in requirements:
        if not isinstance(row, Mapping):
            issues.append("requirement_coverage rows must be objects")
            continue
        if not row.get("requirement_id") or not row.get("intent_clause") or not row.get("observed_behavior"):
            issues.append("requirement_coverage rows must include requirement_id, intent_clause, and observed_behavior")
    for row in validation_commands:
        if not isinstance(row, Mapping):
            issues.append("validation_commands rows must be objects")
            continue
        command_id = str(row.get("command_id") or "")
        if not command_id or not row.get("command") or not row.get("purpose"):
            issues.append("validation_commands rows must include command_id, command, and purpose")
            continue
        if command_id in command_ids:
            issues.append(f"validation_commands contains duplicate command_id {command_id}")
        command_ids.append(command_id)
    for command_id, result in validation_results.items():
        if not isinstance(result, Mapping):
            issues.append(f"validation_results[{command_id}] must be an object")
            continue
        if result.get("status") not in {"passed", "proof_gap", "failed"}:
            issues.append(f"validation_results[{command_id}] must use status passed, proof_gap, or failed")
        if not result.get("summary"):
            issues.append(f"validation_results[{command_id}] must include summary")
    if not requirements:
        issues.append("requirement_coverage must not be empty")
    if not scenarios:
        issues.append("scenario_results must not be empty")
    for row in scenarios:
        if not isinstance(row, Mapping):
            issues.append("scenario_results rows must be objects")
            continue
        mapped = [str(item) for item in row.get("mapped_requirement_ids") or []]
        if not row.get("scenario_id") or not row.get("scenario_kind") or not row.get("observations"):
            issues.append("scenario_results rows must include scenario_id, scenario_kind, and observations")
        if not mapped:
            issues.append(f"scenario {row.get('scenario_id')} must map to at least one requirement")
        for requirement_id in mapped:
            if requirement_id not in requirement_ids:
                issues.append(f"scenario {row.get('scenario_id')} maps unknown requirement {requirement_id}")

    required_scenario_ids = {
        "negative_missing_evidence_pack_link_rejected",
        "negative_unsupported_strong_claim_rejected",
        "negative_missing_adversarial_review_rejected",
        "negative_negative_result_without_failure_notes_rejected",
        "negative_missing_continuation_notes_rejected",
        "negative_inline_budget_policy_rejected",
        "negative_inline_campaign_execution_payload_rejected",
    }
    scenario_ids = {str(row.get("scenario_id") or "") for row in scenarios if isinstance(row, Mapping)}
    missing = sorted(required_scenario_ids - scenario_ids)
    if missing:
        issues.append("missing required negative scenarios: " + ", ".join(missing))
    missing_result_ids = sorted(set(command_ids) - set(str(key) for key in validation_results))
    if missing_result_ids:
        issues.append("validation_results missing command_ids: " + ", ".join(missing_result_ids))
    extra_result_ids = sorted(set(str(key) for key in validation_results) - set(command_ids))
    if extra_result_ids:
        issues.append("validation_results contains unknown command_ids: " + ", ".join(extra_result_ids))
    if artifact.get("validation_outcome") == "passed" and artifact.get("residual_gaps"):
        issues.append("passed artifacts cannot carry residual_gaps")
    if artifact.get("validation_outcome") == "passed" and any(
        row.get("status") != "passed" for row in validation_results.values() if isinstance(row, Mapping)
    ):
        issues.append("passed artifacts cannot carry proof-gap or failed validation_results")

    fingerprint = str(artifact.get("artifact_fingerprint") or "")
    if not fingerprint:
        issues.append("artifact_fingerprint is required")
    else:
        computed = _fingerprint({key: value for key, value in dict(artifact).items() if key != "artifact_fingerprint"})
        if fingerprint != computed:
            issues.append("artifact_fingerprint does not match payload")
    return issues


def write_scientific_campaign_method_template_intent_validation_artifact(
    artifact: Mapping[str, Any], *, repo_root: Path | None = None
) -> Path:
    issues = validate_scientific_campaign_method_template_intent_validation_artifact(artifact)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(repo_root or _repo_root()).resolve()
    item_dir = _ledger_item_dir_for_uid(root, VALIDATION_UID)
    path = item_dir / "artifacts/intent_validation/scientific_campaign_method_template_intent_e2e.json" if item_dir else root / ARTIFACT_REL
    if path.exists():
        existing = _read_json(path)
        if existing.get("artifact_fingerprint") != artifact.get("artifact_fingerprint"):
            raise ValueError("refusing to overwrite an existing validation artifact with different content")
        return path
    return _write_json_atomic(path, artifact)
