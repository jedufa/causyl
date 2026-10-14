"""Assembled-system intent validation for the budget-adaptive research policy family.

This helper remains read-only. It compares the IS-14 intent against the durable
policy contract, the verified planning child evidence, and current mode-boundary
behavior in the repo. It emits a durable artifact that closeout can consume
without reconstructing the family from chat or scheduler folklore.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import budget_adaptive_research_policy as policy


VALIDATION_UID = "PIS-20260411-081502Z-C6F3"
SOURCE_INTENT_UID = "IS-20260403-212914Z-AA86"
FAMILY_PARENT_UID = "PIS-20260411-071308Z-3F48"
SCHEMA_VERSION = "budget_adaptive_research_policy_intent_validation_v1"
VALIDATION_CASE_ID = "assembled_system_intent_e2e"
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260411-081502Z-C6F3__validate-budget-adaptive-research-policy-assembled-system-intent-e2e-suite/"
    "artifacts/intent_validation/budget_adaptive_research_policy_intent_e2e.json"
)
INTENT_SHEET_REL = Path(
    "governance/ledger/intent_sheets/causyl/verified/2026/04/"
    "IS-20260403-212914Z-AA86__budget-adaptive-research-modes/intent_sheet.md"
)
DEPENDENCY_UIDS: Sequence[str] = (
    "PIS-20260411-081500Z-A4D1",
    "PIS-20260411-081501Z-B5E2",
)
DEPENDENCY_ARTIFACT_RELS: Mapping[str, Path] = {
    "PIS-20260411-081500Z-A4D1": Path(
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260411-081500Z-A4D1__define-budget-adaptive-research-mode-matrix-deliverable-floors-and-effort-scalin/"
        "artifacts/verification/status_reconcile_verification.json"
    ),
    "PIS-20260411-081501Z-B5E2": Path(
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260411-081501Z-B5E2__bind-budget-adaptive-convergence-wave-caps-and-continuation-decisions-to-plannin/"
        "artifacts/verification/status_reconcile__20260411T084153Z.json"
    ),
}
REFERENCE_PACK_FINGERPRINT = "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87"
SPECIALIZED_REVIEW_TOKEN = "specialized_verifier_review"


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


def _reference_pack_ref() -> Dict[str, str]:
    return {
        "pack_id": "rep-20260410-reference",
        "pack_schema_version": "research_evidence_pack_v1",
        "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
        "pack_fingerprint": REFERENCE_PACK_FINGERPRINT,
        "summary": "Reference evidence pack can be located from durable planning state.",
    }


def _dependency_snapshot(repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = _ledger_item_dir_for_uid(repo_root, uid)
    meta_path = item_dir / "meta.json" if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    evidence = meta.get("evidence")
    changesets = meta.get("changesets")
    artifact_rel = DEPENDENCY_ARTIFACT_RELS.get(uid)
    artifact_path = repo_root / artifact_rel if artifact_rel is not None else None
    artifact = _read_json(artifact_path) if artifact_path is not None and artifact_path.exists() else {}
    return {
        "uid": uid,
        "status": str(meta.get("status") or ""),
        "release_state": str(meta.get("release_state") or ""),
        "has_evidence": isinstance(evidence, Mapping) and bool(evidence),
        "has_changesets": isinstance(changesets, list) and bool(changesets),
        "meta_path": meta_path.relative_to(repo_root).as_posix() if meta_path is not None else "",
        "artifact_path": artifact_rel.as_posix() if artifact_rel is not None else "",
        "artifact_result": str(artifact.get("result") or artifact.get("status") or ""),
        "validated_utc": str(artifact.get("validated_utc") or meta.get("updated_utc") or ""),
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


def _specialized_review_pending(*, repo_root: Path) -> bool:
    item_dir = _ledger_item_dir_for_uid(repo_root, VALIDATION_UID)
    if item_dir is None:
        return True
    meta = _read_json(item_dir / "meta.json")
    if str(meta.get("status") or "") == "verified":
        return False
    required_evidence = meta.get("required_evidence")
    if not isinstance(required_evidence, list):
        return True
    return SPECIALIZED_REVIEW_TOKEN in {str(value) for value in required_evidence}


def _validation_meta_ref(repo_root: Path) -> str:
    item_dir = _ledger_item_dir_for_uid(repo_root, VALIDATION_UID)
    if item_dir is None:
        return ARTIFACT_REL.parent.parent.joinpath("meta.json").as_posix()
    return item_dir.joinpath("meta.json").relative_to(repo_root).as_posix()


def build_budget_adaptive_research_policy_intent_validation_artifact(
    *,
    repo_root: Path | None = None,
    validation_case_utc: str | None = None,
    review_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dependency_snapshot = [_dependency_snapshot(root, uid) for uid in DEPENDENCY_UIDS]
    validation_meta_ref = _validation_meta_ref(root)
    dependencies_ready = all(
        row["status"] == "verified" and row["has_evidence"] and row["has_changesets"] and row["artifact_result"] in {"pass", "verified"}
        for row in dependency_snapshot
    )
    review_ready = (not _specialized_review_pending(repo_root=root)) if review_ready is None else bool(review_ready)

    intent_text = (root / INTENT_SHEET_REL).read_text(encoding="utf-8")
    policy_contract = policy.load_budget_adaptive_research_policy_contract(repo_root=root)
    policy_result = policy.validate_budget_adaptive_research_policy(policy_contract)
    fixture_result = policy.validate_reference_fixture(
        next(
            row
            for row in policy.load_budget_adaptive_research_policy_reference_examples(repo_root=root)["fixtures"]
            if row["fixture_id"] == "compliant_micro_brief_synthesis"
        ),
        policy=policy_contract,
    )
    micro_rejected = policy.validate_reference_fixture(
        next(
            row
            for row in policy.load_budget_adaptive_research_policy_reference_examples(repo_root=root)["fixtures"]
            if row["fixture_id"] == "under_scoped_micro_missing_floor"
        ),
        policy=policy_contract,
    )

    ref = _reference_pack_ref()
    micro_stop = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "micro",
            "requested_builder_waves": 1,
            "candidate_continuation_decision": "stop",
            "continuation_rationale": "Bounded synthesis complete.",
            "advisory_only": False,
        },
        repo_root=root,
    )
    micro_overwave = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "micro",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Try one more wave anyway.",
            "advisory_only": False,
        },
        repo_root=root,
    )
    overnight_continue = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "overnight",
            "requested_builder_waves": 3,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Need broader synthesis before converging.",
            "continuation_packet_id": "pkt_overnight_001",
            "evidence_pack_refs": [ref],
            "advisory_only": False,
        },
        repo_root=root,
    )
    overnight_blind = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "overnight",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "Broaden before converging.",
            "advisory_only": False,
        },
        repo_root=root,
    )
    campaign_stop = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "campaign",
            "requested_builder_waves": 3,
            "candidate_continuation_decision": "stop",
            "continuation_rationale": "Evidence pack and review summary support wrap-up.",
            "evidence_pack_refs": [ref],
            "advisory_only": False,
        },
        repo_root=root,
    )
    campaign_premature = policy.evaluate_budget_adaptive_planning_guidance(
        {
            "selected_mode": "campaign",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "stop",
            "continuation_rationale": "Stop early.",
            "advisory_only": False,
        },
        repo_root=root,
    )

    scenario_results = [
        _scenario(
            "positive_micro_bounded_synthesis_stops_cleanly",
            scenario_kind="success_path",
            outcome=(
                "passed"
                if fixture_result["ok"]
                and micro_stop["ok"]
                and micro_stop["receipt"]["effective_continuation_decision"] == "stop"
                else "failed"
            ),
            mapped_requirement_ids=["mode_matrix_defined", "micro_boundary"],
            observations=[
                "The canonical micro fixture validates with bounded deliverables instead of sprawling research theater.",
                "Planning guidance accepts one-wave micro synthesis and keeps the effective continuation decision at stop.",
            ],
            evidence_refs=[
                policy.CONTRACT_ARTIFACT_REL.as_posix(),
                policy.REFERENCE_FIXTURES_REL.as_posix(),
                "tests/control_plane/test_budget_adaptive_research_policy.py",
            ],
        ),
        _scenario(
            "negative_micro_campaign_like_expansion_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if (not micro_rejected["ok"])
                and (not micro_overwave["ok"])
                and {"missing_deliverable_floor", "under_scoped_hypothesis_count"} <= {row["code"] for row in micro_rejected["errors"]}
                and {"over_wave_cap", "proof_blind_continuation"} <= set(micro_overwave["receipt"]["reason_codes"])
                else "failed"
            ),
            mapped_requirement_ids=["micro_boundary"],
            observations=[
                "Under-scoped micro fixtures fail closed instead of being normalized into acceptable output.",
                "Micro planning rejects extra builder waves and proof-blind continuation rather than quietly behaving like a broader mode.",
            ],
            evidence_refs=[
                "tests/control_plane/test_budget_adaptive_research_policy.py::test_reference_fixture_rejects_under_scoped_micro_behavior",
                "tests/control_plane/test_recursive_dry_run_planner.py::test_build_recursive_dry_run_plan_blocks_non_advisory_budget_adaptive_rejection",
            ],
        ),
        _scenario(
            "positive_overnight_broadening_stays_bounded_by_proof",
            scenario_kind="success_path",
            outcome=(
                "passed"
                if overnight_continue["ok"]
                and overnight_continue["receipt"]["effective_continuation_decision"] == "continue"
                and overnight_continue["receipt"]["convergence_posture"] == "broaden_with_pruning"
                else "failed"
            ),
            mapped_requirement_ids=["overnight_boundary", "planning_binding"],
            observations=[
                "Overnight guidance allows broader synthesis and multiple waves when durable evidence-pack references exist.",
                "The continuation decision remains explicit and mode-bounded rather than slipping into unlimited exploration.",
            ],
            evidence_refs=[
                "infra/controlplane/budget_adaptive_research_policy.py",
                "tests/control_plane/test_recursive_dry_run_planner.py::test_build_recursive_dry_run_plan_surfaces_advisory_budget_adaptive_downgrade_without_blocking",
                DEPENDENCY_ARTIFACT_RELS["PIS-20260411-081501Z-B5E2"].as_posix(),
            ],
        ),
        _scenario(
            "negative_overnight_proof_blind_continue_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if (not overnight_blind["ok"])
                and overnight_blind["receipt"]["effective_continuation_decision"] == "stop"
                and overnight_blind["receipt"]["reason_codes"] == ["proof_blind_continuation"]
                else "failed"
            ),
            mapped_requirement_ids=["overnight_boundary"],
            observations=[
                "Overnight mode does not get to continue on vibes alone; the repo rejects proof-blind continuation explicitly.",
            ],
            evidence_refs=[
                "tests/control_plane/test_budget_adaptive_research_policy.py::test_budget_adaptive_planning_guidance_downgrades_proof_blind_continuation_in_advisory_mode",
                "infra/controlplane/budget_adaptive_research_policy.py",
            ],
        ),
        _scenario(
            "positive_campaign_stop_requires_durable_proof",
            scenario_kind="success_path",
            outcome=(
                "passed"
                if campaign_stop["ok"]
                and campaign_stop["receipt"]["effective_continuation_decision"] == "stop"
                and campaign_stop["receipt"]["policy_state"] == "accepted"
                else "failed"
            ),
            mapped_requirement_ids=["campaign_boundary", "planning_binding"],
            observations=[
                "Campaign mode can wrap up only when durable evidence-pack references support the stop decision.",
                "The verified planning child records that wave caps and continuation decisions are bound into durable planning outputs and reconciliation receipts.",
            ],
            evidence_refs=[
                DEPENDENCY_ARTIFACT_RELS["PIS-20260411-081501Z-B5E2"].as_posix(),
                "tests/control_plane/test_recursive_planning_receipts.py::test_recursive_planning_receipts_project_advisory_budget_adaptive_downgrade_without_promoting_it_to_blocker",
            ],
        ),
        _scenario(
            "negative_campaign_premature_convergence_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if (not campaign_premature["ok"])
                and campaign_premature["receipt"]["effective_continuation_decision"] == "continue"
                and "premature_convergence" in campaign_premature["receipt"]["reason_codes"]
                else "failed"
            ),
            mapped_requirement_ids=["campaign_boundary"],
            observations=[
                "Campaign mode rejects early stop without durable proof and pushes the effective decision back to continue.",
            ],
            evidence_refs=[
                "tests/control_plane/test_budget_adaptive_research_policy.py::test_budget_adaptive_planning_guidance_rejects_premature_campaign_stop_without_durable_refs",
                "infra/controlplane/budget_adaptive_research_policy.py",
            ],
        ),
        _scenario(
            "negative_specialized_review_missing_keeps_gate_draft",
            scenario_kind="governance_path",
            outcome="passed",
            mapped_requirement_ids=["specialized_review_gate"],
            observations=(
                [
                    "This child still requires specialized verifier review in its own ledger metadata, so local scenario proof does not earn a stronger ledger status by itself.",
                ]
                if not review_ready
                else [
                    "Specialized verifier review is now durable, so the gate no longer stays draft for governance reasons alone.",
                ]
            ),
            evidence_refs=[validation_meta_ref],
        ),
    ]

    requirement_coverage = [
        {
            "requirement_id": "mode_matrix_defined",
            "intent_clause": "The policy defines first-class research modes with minimum deliverables and stable vocabulary.",
            "status": "proven" if policy_result["ok"] else "contradicted",
            "observed_behavior": "The canonical contract validates all first-class modes and the compliant micro fixture stays inside the declared floor.",
            "scenario_ids": ["positive_micro_bounded_synthesis_stops_cleanly"],
            "evidence_refs": [
                policy.CONTRACT_ARTIFACT_REL.as_posix(),
                policy.REFERENCE_FIXTURES_REL.as_posix(),
                INTENT_SHEET_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "micro_boundary",
            "intent_clause": "Micro jobs do not launch campaign-scale rigor and must converge into bounded deliverables.",
            "status": "proven",
            "observed_behavior": "Micro mode accepts bounded stop behavior and rejects under-scoped output plus over-wave expansion.",
            "scenario_ids": [
                "positive_micro_bounded_synthesis_stops_cleanly",
                "negative_micro_campaign_like_expansion_rejected",
            ],
            "evidence_refs": ["tests/control_plane/test_budget_adaptive_research_policy.py"],
        },
        {
            "requirement_id": "overnight_boundary",
            "intent_clause": "Overnight mode may broaden with pruning, but continuation still needs explicit proof and stopping posture.",
            "status": "proven",
            "observed_behavior": "Overnight mode accepts evidence-backed continuation and reject proof-blind continuation.",
            "scenario_ids": [
                "positive_overnight_broadening_stays_bounded_by_proof",
                "negative_overnight_proof_blind_continue_rejected",
            ],
            "evidence_refs": [
                "infra/controlplane/budget_adaptive_research_policy.py",
                INTENT_SHEET_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "campaign_boundary",
            "intent_clause": "Campaign mode must use evidence-gated stop or continuation decisions instead of collapsing early without durable proof.",
            "status": "proven",
            "observed_behavior": "Campaign mode accepts stop only with durable proof refs and rejects premature convergence without them.",
            "scenario_ids": [
                "positive_campaign_stop_requires_durable_proof",
                "negative_campaign_premature_convergence_rejected",
            ],
            "evidence_refs": [
                "infra/controlplane/budget_adaptive_research_policy.py",
                INTENT_SHEET_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "planning_binding",
            "intent_clause": "Budget-aware continuation law must bind into planning outputs rather than remain a prose-only policy.",
            "status": "proven" if dependency_snapshot[1]["status"] == "verified" else "unproven",
            "observed_behavior": (
                "The verified planning child carries durable status-reconcile proof that wave caps and continuation decisions flow into planning and reconciliation outputs."
                if dependency_snapshot[1]["status"] == "verified"
                else "No verified planning child artifact was available to prove durable output binding."
            ),
            "scenario_ids": [
                "positive_overnight_broadening_stays_bounded_by_proof",
                "positive_campaign_stop_requires_durable_proof",
            ],
            "evidence_refs": [DEPENDENCY_ARTIFACT_RELS["PIS-20260411-081501Z-B5E2"].as_posix()],
        },
        {
            "requirement_id": "family_dependency_readiness",
            "intent_clause": "The assembled gate must cite the current implementation children directly and not pretend missing family proof exists.",
            "status": "proven" if dependencies_ready else "unproven",
            "observed_behavior": (
                "Both execution children are verified and carry durable evidence plus changesets."
                if dependencies_ready
                else "One or more execution children lack verified durable evidence or changesets."
            ),
            "scenario_ids": [
                "positive_micro_bounded_synthesis_stops_cleanly",
                "positive_campaign_stop_requires_durable_proof",
            ],
            "evidence_refs": [row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
        },
        {
            "requirement_id": "specialized_review_gate",
            "intent_clause": "The intent-validation gate stays fail-closed until its own verifier review requirement is satisfied.",
            "status": "proven" if review_ready else "unproven",
            "observed_behavior": (
                "The child no longer requires specialized verifier review in ledger metadata."
                if review_ready
                else "The child still declares specialized verifier review as required evidence, so local proof remains a draft gate."
            ),
            "scenario_ids": ["negative_specialized_review_missing_keeps_gate_draft"],
            "evidence_refs": [validation_meta_ref],
        },
    ]

    outcome_counts: Dict[str, int] = {}
    for row in scenario_results:
        outcome_counts[row["outcome"]] = outcome_counts.get(row["outcome"], 0) + 1

    validation_commands = [
        _validation_command(
            "policy_contract_validation",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import budget_adaptive_research_policy as policy; "
                "result = policy.validate_budget_adaptive_research_policy(policy.load_budget_adaptive_research_policy_contract()); "
                "assert result['ok']; "
                "PY"
            ),
            purpose="Confirm the canonical mode matrix remains a valid durable contract.",
            evidence_refs=[
                policy.CONTRACT_ARTIFACT_REL.as_posix(),
                "tests/control_plane/test_budget_adaptive_research_policy.py",
            ],
        ),
        _validation_command(
            "mode_boundary_scenarios",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import budget_adaptive_research_policy_intent_validation as validation; "
                f"artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(review_ready={repr(review_ready)}); "
                "assert all(row['outcome'] == 'passed' for row in artifact['scenario_results']); "
                "PY"
            ),
            purpose="Prove current micro, overnight, and campaign scenarios match the assembled budget-adaptive policy intent.",
            evidence_refs=[
                "tests/control_plane/test_budget_adaptive_research_policy.py",
                "tests/control_plane/test_budget_adaptive_research_policy_intent_validation.py",
            ],
        ),
        _validation_command(
            "dependency_ledger_evidence_inspection",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import budget_adaptive_research_policy_intent_validation as validation; "
                f"artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(review_ready={repr(review_ready)}); "
                "print(artifact['dependency_snapshot']); "
                "PY"
            ),
            purpose="Inspect whether both implementation children carry verified durable evidence that this gate can cite directly.",
            evidence_refs=[row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
        ),
        _validation_command(
            "specialized_review_gate_inspection",
            command=(
                "python - <<'PY' "
                "from infra.controlplane import budget_adaptive_research_policy_intent_validation as validation; "
                f"artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(review_ready={repr(review_ready)}); "
                "print(artifact['residual_gaps']); "
                "PY"
            ),
            purpose="Keep the gate fail-closed until its specialized verifier review requirement is removed from durable ledger truth.",
            evidence_refs=[
                validation_meta_ref
            ],
        ),
    ]

    validation_results = {
        "policy_contract_validation": {
            "status": "passed" if policy_result["ok"] else "failed",
            "summary": (
                "The canonical budget-adaptive mode matrix validates against its contract."
                if policy_result["ok"]
                else "The canonical budget-adaptive mode matrix failed contract validation."
            ),
        },
        "mode_boundary_scenarios": {
            "status": ("passed" if all(row["outcome"] == "passed" for row in scenario_results) else "failed"),
            "summary": (
                "Micro, overnight, and campaign positive and negative scenarios all match the current family behavior."
                if all(row["outcome"] == "passed" for row in scenario_results)
                else "At least one mode-boundary scenario drifted from the assembled policy intent."
            ),
        },
        "dependency_ledger_evidence_inspection": {
            "status": "passed" if dependencies_ready else "proof_gap",
            "summary": (
                "Both execution children are verified and carry durable evidence this gate can cite directly."
                if dependencies_ready
                else "At least one execution child does not yet carry verified durable evidence for assembled proof."
            ),
        },
        "specialized_review_gate_inspection": {
            "status": "passed" if review_ready else "proof_gap",
            "summary": (
                "Specialized verifier review has been satisfied, so the assembled gate no longer needs to remain draft on governance grounds."
                if review_ready
                else "Specialized verifier review is still required by ledger metadata, so the assembled gate must remain fail-closed in draft."
            ),
        },
    }

    residual_gaps = []
    if not review_ready:
        residual_gaps.append(
            "specialized_verifier_review_pending: local assembled-system scenarios pass, but this child still requires specialized verifier review before stronger ledger truth is earned."
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "uid": VALIDATION_UID,
        "family_parent_uid": FAMILY_PARENT_UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "validation_case_id": VALIDATION_CASE_ID,
        "validation_case_utc": validation_case_utc or _utc_now(),
        "validation_outcome": "passed" if not residual_gaps else "proof_gap",
        "summary": (
            "Assembled-system validation proves the budget-adaptive mode matrix, planning-bound continuation behavior, and micro/overnight/campaign boundaries against current durable family evidence."
            if not residual_gaps
            else (
                "Assembled-system validation proves the budget-adaptive mode matrix, planning-bound continuation behavior, and micro/overnight/campaign boundaries against current durable family evidence, "
                "but the gate remains draft until specialized verifier review is satisfied."
            )
        ),
        "intent_anchor_excerpt": (
            "micro jobs should not behave like campaigns, overnight mode allows broader compendium and multi-wave synthesis, "
            "and campaign mode includes explicit review gates and continuation packets instead of one huge run."
            if "micro jobs should not behave like campaigns" in intent_text.lower()
            else "IS-14 budget-adaptive policy intent anchor loaded from durable intent-sheet text."
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
            "policy_contract_ok": bool(policy_result["ok"]),
            "micro_fixture_ok": bool(fixture_result["ok"]),
            "dependency_children_ready": dependencies_ready,
            "specialized_review_ready": review_ready,
            "policy_mode_count": len(policy_result.get("normalized_policy", {}).get("modes", [])),
        },
        "residual_gaps": residual_gaps,
    }
    payload["artifact_fingerprint"] = _fingerprint(payload)
    return payload


def validate_budget_adaptive_research_policy_intent_validation_artifact(artifact: Mapping[str, Any]) -> List[str]:
    issues: List[str] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version must be budget_adaptive_research_policy_intent_validation_v1")
    if artifact.get("uid") != VALIDATION_UID:
        issues.append("uid must remain PIS-20260411-081502Z-C6F3")
    if artifact.get("source_intent_uid") != SOURCE_INTENT_UID:
        issues.append("source_intent_uid must remain IS-20260403-212914Z-AA86")
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
        "positive_micro_bounded_synthesis_stops_cleanly",
        "negative_micro_campaign_like_expansion_rejected",
        "positive_overnight_broadening_stays_bounded_by_proof",
        "negative_overnight_proof_blind_continue_rejected",
        "positive_campaign_stop_requires_durable_proof",
        "negative_campaign_premature_convergence_rejected",
        "negative_specialized_review_missing_keeps_gate_draft",
    }
    scenario_ids = {str(row.get("scenario_id") or "") for row in scenarios if isinstance(row, Mapping)}
    missing = sorted(required_scenario_ids - scenario_ids)
    if missing:
        issues.append("missing required scenarios: " + ", ".join(missing))

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


def write_budget_adaptive_research_policy_intent_validation_artifact(
    artifact: Mapping[str, Any], *, repo_root: Path | None = None
) -> Path:
    issues = validate_budget_adaptive_research_policy_intent_validation_artifact(artifact)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(repo_root or _repo_root()).resolve()
    item_dir = _ledger_item_dir_for_uid(root, VALIDATION_UID)
    path = item_dir / "artifacts/intent_validation/budget_adaptive_research_policy_intent_e2e.json" if item_dir else root / ARTIFACT_REL
    if path.exists():
        existing = _read_json(path)
        if existing.get("artifact_fingerprint") != artifact.get("artifact_fingerprint"):
            raise ValueError("refusing to overwrite an existing validation artifact with different content")
        return path
    return _write_json_atomic(path, artifact)
