"""Canonical launch-request and selected-block summary helpers for the verification workflow slice."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


SUMMARY_SCHEMA_VERSION = "verification_workflow_launch_contract_summary_v1"
LAUNCH_REQUEST_SCHEMA_VERSION = "verification_workflow_launch_request_v1"
SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION = "verification_workflow_selected_block_summary_v1"
PIS_UID = "PIS-20260414-043438Z-K1M4"
WORKFLOW_FAMILY = "verification_workflow_live_stress_v1"
WORKFLOW_FAMILY_PARENT_UID = "PIS-20260414-043437Z-J7L3"
ALLOWED_OUTPUT_MODES: Tuple[str, ...] = ("dense_test_pack", "child_pis_family")
ALLOWED_VERDICTS: Tuple[str, ...] = ("accept", "expand", "reject")
LAUNCHABLE_VERDICTS = frozenset({"accept", "expand"})
DEFAULT_MAX_ITERATIONS = 4
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-043438Z-K1M4__define-verification-workflow-launch-contract-and-block-selection-surface/"
    "artifacts/stress/launch_contract_summary.json"
)
R5S8_LAUNCH_REQUEST_CONTRACT_ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050101Z-R5S8__define-verification-launch-request-schema-preflight-validation-and-durable-reque/"
    "artifacts/stress/launch_contract/launch_request_contract_v1.json"
)
R5S8_LAUNCH_REQUEST_NEGATIVE_CASES_ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050101Z-R5S8__define-verification-launch-request-schema-preflight-validation-and-durable-reque/"
    "artifacts/stress/launch_contract/launch_request_negative_cases_v1.json"
)
S6T9_SELECTED_BLOCK_CONTRACT_ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050102Z-S6T9__define-selected-block-qualification-verdicts-and-bounded-summary-contract/"
    "artifacts/stress/launch_contract/selected_block_summary_contract_v1.json"
)
S6T9_SELECTED_BLOCK_NEGATIVE_CASES_ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050102Z-S6T9__define-selected-block-qualification-verdicts-and-bounded-summary-contract/"
    "artifacts/stress/launch_contract/selected_block_negative_cases_v1.json"
)


class VerificationWorkflowLaunchError(ValueError):
    """Raised when launch input or block-summary data cannot be normalized honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VerificationWorkflowLaunchContractV1:
    version: str
    uid: str
    workflow_family: str
    artifact_rel: Path
    launch_request_schema: str
    selected_block_summary_schema: str
    allowed_output_modes: Tuple[str, ...]
    allowed_verdicts: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(value: Any, *, code: str, field: str) -> str:
    text = _clean_text(value)
    if not text:
        raise VerificationWorkflowLaunchError(code, f"missing required field: {field}")
    return text


def _normalize_adjacent_seam(record: Mapping[str, Any]) -> Dict[str, str]:
    seam_id = _require_text(
        record.get("seam_id"),
        code="selected_block_summary_missing_adjacent_seam_field",
        field="adjacent_truth_seams[].seam_id",
    )
    relationship = _require_text(
        record.get("relationship_to_block"),
        code="selected_block_summary_missing_adjacent_seam_field",
        field="adjacent_truth_seams[].relationship_to_block",
    )
    disposition = _require_text(
        record.get("disposition"),
        code="selected_block_summary_missing_adjacent_seam_field",
        field="adjacent_truth_seams[].disposition",
    ).lower()
    if disposition not in {"included", "monitored", "excluded"}:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_invalid_adjacent_seam_disposition",
            "adjacent truth seam disposition must be included, monitored, or excluded",
        )
    note = _require_text(
        record.get("note"),
        code="selected_block_summary_missing_adjacent_seam_field",
        field="adjacent_truth_seams[].note",
    )
    return {
        "seam_id": seam_id,
        "relationship_to_block": relationship,
        "disposition": disposition,
        "note": note,
    }


def _bounded_summary_text(*, block_label: str, verdict: str) -> str:
    if verdict == "accept":
        return (
            f"The plane will launch verification planning for {block_label} as the current bounded risk unit. "
            "This is a launch contract only; it does not prove runtime behavior, completion, or repair yield."
        )
    if verdict == "expand":
        return (
            f"The plane will launch verification planning for {block_label} with the named adjacent seams included "
            "before execution. This is a launch contract only; it does not prove runtime behavior, completion, or repair yield."
        )
    return (
        f"The plane will not launch verification planning for {block_label} until the boundary is clarified. "
        "This is a launch contract only; it does not prove runtime behavior, completion, or repair yield."
    )


def _check_bounded_wording(text: str) -> None:
    lowered = text.lower()
    forbidden = [
        "runtime proof",
        "runtime correctness",
        "completed successfully",
        "guarantees correctness",
        "verification already passed",
        "proved robust",
        "safe unattended repair",
        "safe to run unattended",
    ]
    for token in forbidden:
        if token in lowered:
            raise VerificationWorkflowLaunchError(
                "selected_block_summary_overclaims_runtime_proof",
                f"selected-block summary uses over-claiming wording: {token}",
            )


def verification_workflow_launch_contract_v1() -> VerificationWorkflowLaunchContractV1:
    return VerificationWorkflowLaunchContractV1(
        version=SUMMARY_SCHEMA_VERSION,
        uid=PIS_UID,
        workflow_family=WORKFLOW_FAMILY,
        artifact_rel=ARTIFACT_REL,
        launch_request_schema=LAUNCH_REQUEST_SCHEMA_VERSION,
        selected_block_summary_schema=SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION,
        allowed_output_modes=ALLOWED_OUTPUT_MODES,
        allowed_verdicts=ALLOWED_VERDICTS,
    )


def build_selected_block_summary(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    verdict = _require_text(
        payload.get("qualification_verdict"),
        code="selected_block_summary_missing_verdict",
        field="qualification_verdict",
    ).lower()
    if verdict not in ALLOWED_VERDICTS:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_invalid_verdict",
            "qualification_verdict must be accept, expand, or reject",
        )

    block_ref = _require_text(
        payload.get("block_ref"), code="selected_block_summary_missing_block_ref", field="block_ref"
    )
    block_label = _require_text(
        payload.get("block_label"), code="selected_block_summary_missing_block_label", field="block_label"
    )
    operator_goal = _require_text(
        payload.get("operator_goal"),
        code="selected_block_summary_missing_operator_goal",
        field="operator_goal",
    )
    risk_unit_summary = _require_text(
        payload.get("risk_unit_summary"),
        code="selected_block_summary_missing_risk_unit_summary",
        field="risk_unit_summary",
    )
    qualification_basis = _clean_list(payload.get("qualification_basis") or [])
    if not qualification_basis:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_missing_qualification_basis",
            "qualification_basis must name why the chosen block is accepted, expanded, or rejected",
        )
    included_paths = _clean_list(payload.get("included_paths") or [])
    if not included_paths:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_missing_included_paths",
            "included_paths must remain explicit for the operator-facing summary",
        )
    adjacent_truth_seams = [
        _normalize_adjacent_seam(record)
        for record in (payload.get("adjacent_truth_seams") or [])
        if isinstance(record, Mapping)
    ]
    if not adjacent_truth_seams:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_missing_adjacent_truth_seams",
            "adjacent_truth_seams must record the seams that were reviewed",
        )

    expansion_notes = _clean_list(payload.get("expansion_notes") or [])
    rejection_reasons = _clean_list(payload.get("rejection_reasons") or [])
    if verdict == "expand" and not expansion_notes:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_expand_requires_notes",
            "expanded selected blocks must carry explicit expansion_notes",
        )
    if verdict == "reject" and not rejection_reasons:
        raise VerificationWorkflowLaunchError(
            "selected_block_summary_reject_requires_reasons",
            "rejected selected blocks must carry rejection_reasons",
        )

    provided_bounded_summary = _clean_text(payload.get("bounded_operator_summary"))
    if provided_bounded_summary:
        _check_bounded_wording(provided_bounded_summary)
    bounded_summary = _bounded_summary_text(block_label=block_label, verdict=verdict)
    _check_bounded_wording(bounded_summary)
    explanation = {
        "block_ref": block_ref,
        "block_label": block_label,
        "qualification_verdict": verdict,
        "operator_goal": operator_goal,
        "risk_unit_summary": risk_unit_summary,
        "qualification_basis": qualification_basis,
        "included_paths": included_paths,
        "adjacent_truth_seams": adjacent_truth_seams,
        "expansion_notes": expansion_notes,
        "rejection_reasons": rejection_reasons,
        "bounded_operator_summary": bounded_summary,
        "what_this_does_not_prove": (
            "The summary only bounds what the workflow intends to verify next. "
            "It does not prove runtime correctness, scheduler delivery, completion, or repair effectiveness."
        ),
        "stale_summary_policy": (
            "Treat the selected-block summary as stale and require recomputation if the request fingerprint, "
            "qualification inputs, or adjacent-seam review changes."
        ),
        "launch_readiness": verdict in LAUNCHABLE_VERDICTS,
    }
    return {
        "schema_version": SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION,
        **explanation,
        "summary_fingerprint": _fingerprint(explanation),
    }


def normalize_launch_request(raw: Mapping[str, Any], *, created_utc: str) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    request_id = _require_text(payload.get("request_id"), code="launch_request_missing_request_id", field="request_id")
    verification_goal = _require_text(
        payload.get("verification_goal"),
        code="launch_request_missing_verification_goal",
        field="verification_goal",
    )
    selected_block = payload.get("selected_block_summary")
    if not isinstance(selected_block, Mapping):
        raise VerificationWorkflowLaunchError(
            "launch_request_missing_selected_block_summary",
            "selected_block_summary must be provided before launch normalization",
        )
    block_summary = build_selected_block_summary(selected_block)
    if not block_summary["launch_readiness"]:
        raise VerificationWorkflowLaunchError(
            "launch_request_block_not_launchable",
            "selected_block_summary is rejected and cannot be launched",
        )

    requested_output_mode = _clean_text(payload.get("requested_output_mode") or "child_pis_family").lower()
    if requested_output_mode not in ALLOWED_OUTPUT_MODES:
        raise VerificationWorkflowLaunchError(
            "launch_request_invalid_output_mode",
            f"requested_output_mode must be one of: {', '.join(ALLOWED_OUTPUT_MODES)}",
        )

    max_iterations_raw = payload.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise VerificationWorkflowLaunchError(
            "launch_request_invalid_max_iterations",
            "max_iterations must be an integer between 1 and 4",
        ) from exc
    if max_iterations < 1 or max_iterations > 4:
        raise VerificationWorkflowLaunchError(
            "launch_request_invalid_max_iterations",
            "max_iterations must be an integer between 1 and 4",
        )

    operator_intent = {
        "request_id": request_id,
        "block_ref": block_summary["block_ref"],
        "verification_goal": verification_goal,
        "requested_output_mode": requested_output_mode,
        "max_iterations": max_iterations,
        "operator_note": _clean_text(payload.get("operator_note")),
        "request_instance_token": _clean_text(payload.get("request_instance_token")),
    }
    normalized_request = {
        "workflow_family": WORKFLOW_FAMILY,
        "workflow_family_parent_uid": WORKFLOW_FAMILY_PARENT_UID,
        "launch_contract_uid": PIS_UID,
        "scheduler_job_kind": "build_verify_loop",
        "selected_block_summary_fingerprint": block_summary["summary_fingerprint"],
        "selected_block_verdict": block_summary["qualification_verdict"],
        "request_id": request_id,
        "block_ref": block_summary["block_ref"],
        "verification_goal": verification_goal,
        "requested_output_mode": requested_output_mode,
        "max_iterations": max_iterations,
        "request_instance_token": _clean_text(payload.get("request_instance_token")),
    }
    fingerprint_payload = {
        "workflow_family": normalized_request["workflow_family"],
        "block_ref": normalized_request["block_ref"],
        "verification_goal": normalized_request["verification_goal"],
        "requested_output_mode": normalized_request["requested_output_mode"],
        "max_iterations": normalized_request["max_iterations"],
        "selected_block_summary_fingerprint": normalized_request["selected_block_summary_fingerprint"],
    }
    if normalized_request["request_instance_token"]:
        fingerprint_payload["request_instance_token"] = normalized_request["request_instance_token"]
    request_fingerprint = _fingerprint(fingerprint_payload)
    return {
        "schema_version": LAUNCH_REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "created_utc": _require_text(created_utc, code="launch_request_missing_created_utc", field="created_utc"),
        "workflow_family": WORKFLOW_FAMILY,
        "workflow_family_parent_uid": WORKFLOW_FAMILY_PARENT_UID,
        "launch_contract_uid": PIS_UID,
        "request_fingerprint": request_fingerprint,
        "raw_operator_intent": operator_intent,
        "normalized_request": normalized_request,
        "durable_record_contract": {
            "canonical_source_of_truth": "durable_launch_request_record",
            "must_be_written_before_scheduler_submission": True,
            "stores_raw_operator_intent_and_normalized_request": True,
            "scheduler_rows_are_reference_only": True,
            "retry_reuses_existing_record_when_request_fingerprint_matches": True,
        },
        "preflight": {
            "status": "ready",
            "rejection_codes": [],
            "normalization_notes": [
                "selected-block summary fingerprint is bound into the launch request",
                "max_iterations is capped to the workflow family limit",
            ],
        },
    }


def launch_contract_summary_v1() -> Dict[str, Any]:
    sample_block = build_selected_block_summary(
        {
            "block_ref": "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
            "block_label": "scheduler launch handoff",
            "qualification_verdict": "expand",
            "operator_goal": "stress one real scheduler-driven verification workflow slice",
            "risk_unit_summary": "scheduler launch plus the adjacent queue-claim seam is the smallest honest unit",
            "qualification_basis": [
                "the launch handoff is the operator-facing claim under attack",
                "the queue-claim seam can change whether launch truth is honest",
            ],
            "included_paths": [
                "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
                "runtime/state/scheduler_watchdog/",
            ],
            "adjacent_truth_seams": [
                {
                    "seam_id": "scheduler_claim_state",
                    "relationship_to_block": "queue claim and active-run materialization",
                    "disposition": "included",
                    "note": "Needed so launch truth does not ignore claimed-but-unmaterialized runs.",
                },
                {
                    "seam_id": "watchdog_projection",
                    "relationship_to_block": "operator-visible degraded state",
                    "disposition": "monitored",
                    "note": "Watched as a downstream truth surface, but not owned by this launch contract slice.",
                },
            ],
            "expansion_notes": [
                "Include the queue-claim seam because it can falsify launch truth without changing the operator-selected file alone."
            ],
        }
    )
    sample_request = normalize_launch_request(
        {
            "request_id": "verification_workflow_launch_demo",
            "verification_goal": "Launch a bounded verification workflow family without over-claiming proof.",
            "requested_output_mode": "child_pis_family",
            "max_iterations": 4,
            "operator_note": "Use the bounded family path first.",
            "selected_block_summary": sample_block,
        },
        created_utc="2026-04-14T05:20:00Z",
    )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "pis_uid": PIS_UID,
        "workflow_family": WORKFLOW_FAMILY,
        "maximum_supported_claim": "launch_contract_defined_only",
        "request_contract": {
            "schema_version": LAUNCH_REQUEST_SCHEMA_VERSION,
            "required_operator_fields": [
                "request_id",
                "verification_goal",
                "selected_block_summary",
            ],
            "supported_output_modes": list(ALLOWED_OUTPUT_MODES),
            "max_iterations_range": [1, 4],
            "fail_closed_rules": [
                "selected-block verdict reject blocks launch",
                "missing request_id or verification_goal blocks launch",
                "invalid requested_output_mode blocks launch",
                "out-of-range max_iterations blocks launch",
            ],
            "durable_truth_rules": [
                "persist one durable launch request record before scheduler submission",
                "preserve raw operator intent alongside normalized launch fields",
                "reuse the same durable record when request_fingerprint matches on retry",
            ],
        },
        "selected_block_summary_contract": {
            "schema_version": SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION,
            "allowed_verdicts": list(ALLOWED_VERDICTS),
            "launchable_verdicts": sorted(LAUNCHABLE_VERDICTS),
            "required_fields": [
                "block_ref",
                "block_label",
                "qualification_verdict",
                "operator_goal",
                "risk_unit_summary",
                "qualification_basis",
                "included_paths",
                "adjacent_truth_seams",
            ],
            "bounded_wording_rules": [
                "summary text must stay operator-readable",
                "summary text must say it does not prove runtime behavior or completion",
                "summary text must fail closed on over-claiming proof language",
            ],
            "stale_summary_policy": (
                "Treat the summary as stale if the request fingerprint, qualification inputs, or adjacent-seam review changes."
            ),
        },
        "sample_selected_block_summary": sample_block,
        "sample_launch_request": sample_request,
        "deferred_to_siblings": [
            "operator launch review and confirm/cancel handoff surface",
            "post-launch run card and phase status rendering",
            "planning-only claim ceiling and stale-plan messaging after launch",
        ],
    }


def launch_request_contract_artifact_v1() -> Dict[str, Any]:
    summary = launch_contract_summary_v1()
    return {
        "schema_version": LAUNCH_REQUEST_SCHEMA_VERSION,
        "launch_contract_uid": PIS_UID,
        "workflow_family": WORKFLOW_FAMILY,
        "request_contract": deepcopy(summary["request_contract"]),
        "sample_launch_request": deepcopy(summary["sample_launch_request"]),
        "notes": [
            "This artifact defines the preflight-normalized launch request only.",
            "Selected-block qualification semantics remain source inputs, not restated proof claims.",
            "Persist the durable launch request record before scheduler submission.",
        ],
    }


def launch_request_negative_cases_v1() -> Dict[str, Any]:
    return {
        "schema_version": "verification_workflow_launch_request_negative_cases_v1",
        "launch_contract_uid": PIS_UID,
        "cases": [
            {
                "case_id": "missing_selected_block_summary",
                "input_delta": {
                    "selected_block_summary": None,
                },
                "expected_error_code": "launch_request_missing_selected_block_summary",
                "reason": "launch normalization must fail closed before scheduler submission when block review data is absent",
            },
            {
                "case_id": "invalid_output_mode",
                "input_delta": {
                    "requested_output_mode": "magic",
                },
                "expected_error_code": "launch_request_invalid_output_mode",
                "reason": "only bounded workflow output modes are allowed in this launch-contract slice",
            },
            {
                "case_id": "rejected_selected_block",
                "input_delta": {
                    "selected_block_summary": {
                        "block_ref": "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
                        "block_label": "scheduler launch handoff",
                        "qualification_verdict": "reject",
                        "operator_goal": "stress one real scheduler-driven verification workflow slice",
                        "risk_unit_summary": "the selected block omits the queue-claim seam that owns launch truth",
                        "qualification_basis": [
                            "the operator-selected file alone is not a stable risk unit",
                        ],
                        "included_paths": [
                            "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
                        ],
                        "adjacent_truth_seams": [
                            {
                                "seam_id": "scheduler_claim_state",
                                "relationship_to_block": "queue claim and active-run materialization",
                                "disposition": "excluded",
                                "note": "Excluded here to demonstrate the fail-closed rejection path.",
                            }
                        ],
                        "rejection_reasons": [
                            "The chosen block omits the seam that can independently contradict launch truth.",
                        ],
                    },
                },
                "expected_error_code": "launch_request_block_not_launchable",
                "reason": "rejected block summaries must not produce a launchable durable request record",
            },
        ],
    }


def selected_block_summary_contract_artifact_v1() -> Dict[str, Any]:
    summary = launch_contract_summary_v1()
    return {
        "schema_version": SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION,
        "launch_contract_uid": PIS_UID,
        "workflow_family": WORKFLOW_FAMILY,
        "selected_block_summary_contract": deepcopy(summary["selected_block_summary_contract"]),
        "sample_selected_block_summary": deepcopy(summary["sample_selected_block_summary"]),
        "notes": [
            "This artifact defines bounded pre-launch explanation only.",
            "Qualification verdicts must be reproducible from the durable launch request inputs.",
            "The selected-block summary must fail closed if it starts sounding like runtime proof.",
        ],
    }


def selected_block_negative_cases_v1() -> Dict[str, Any]:
    return {
        "schema_version": "verification_workflow_selected_block_negative_cases_v1",
        "launch_contract_uid": PIS_UID,
        "cases": [
            {
                "case_id": "reject_requires_rejection_reasons",
                "input_delta": {
                    "qualification_verdict": "reject",
                    "rejection_reasons": [],
                },
                "expected_error_code": "selected_block_summary_reject_requires_reasons",
                "reason": "rejected blocks must preserve explicit operator-readable reasons",
            },
            {
                "case_id": "expand_requires_expansion_notes",
                "input_delta": {
                    "qualification_verdict": "expand",
                    "expansion_notes": [],
                },
                "expected_error_code": "selected_block_summary_expand_requires_notes",
                "reason": "expanded blocks must record why the boundary widened instead of quietly broadening scope",
            },
            {
                "case_id": "invalid_adjacent_truth_seam_disposition",
                "input_delta": {
                    "adjacent_truth_seams": [
                        {
                            "seam_id": "scheduler_claim_state",
                            "relationship_to_block": "queue claim and active-run materialization",
                            "disposition": "nearby",
                            "note": "Invalid on purpose to prove the fail-closed seam contract.",
                        }
                    ],
                },
                "expected_error_code": "selected_block_summary_invalid_adjacent_seam_disposition",
                "reason": "adjacent truth seam review must use explicit included/monitored/excluded semantics",
            },
        ],
    }


def validate_verification_workflow_launch_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    payload = json.loads((root / ARTIFACT_REL).read_text(encoding="utf-8"))
    expected = launch_contract_summary_v1()
    issues: List[str] = []
    if payload.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        issues.append("schema_version must remain verification_workflow_launch_contract_summary_v1")
    if payload.get("pis_uid") != PIS_UID:
        issues.append("pis_uid must remain bound to PIS-20260414-043438Z-K1M4")
    if payload.get("workflow_family") != WORKFLOW_FAMILY:
        issues.append("workflow_family must remain verification_workflow_live_stress_v1")
    if payload.get("maximum_supported_claim") != "launch_contract_defined_only":
        issues.append("maximum_supported_claim must remain launch_contract_defined_only")

    request_contract = payload.get("request_contract") if isinstance(payload.get("request_contract"), Mapping) else {}
    if request_contract.get("schema_version") != LAUNCH_REQUEST_SCHEMA_VERSION:
        issues.append("request_contract.schema_version must remain verification_workflow_launch_request_v1")
    if tuple(request_contract.get("supported_output_modes") or []) != ALLOWED_OUTPUT_MODES:
        issues.append("request_contract.supported_output_modes drifted from the runtime helper contract")

    block_contract = (
        payload.get("selected_block_summary_contract")
        if isinstance(payload.get("selected_block_summary_contract"), Mapping)
        else {}
    )
    if block_contract.get("schema_version") != SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION:
        issues.append(
            "selected_block_summary_contract.schema_version must remain verification_workflow_selected_block_summary_v1"
        )
    if tuple(block_contract.get("allowed_verdicts") or []) != ALLOWED_VERDICTS:
        issues.append("selected_block_summary_contract.allowed_verdicts drifted from the runtime helper contract")

    sample_block = payload.get("sample_selected_block_summary")
    sample_request = payload.get("sample_launch_request")
    if not isinstance(sample_block, Mapping):
        issues.append("sample_selected_block_summary must remain present")
    elif sample_block.get("schema_version") != SELECTED_BLOCK_SUMMARY_SCHEMA_VERSION:
        issues.append("sample_selected_block_summary must remain a normalized selected-block summary")
    if not isinstance(sample_request, Mapping):
        issues.append("sample_launch_request must remain present")
    elif sample_request.get("schema_version") != LAUNCH_REQUEST_SCHEMA_VERSION:
        issues.append("sample_launch_request must remain a normalized launch request")
    if payload != expected:
        issues.append("launch contract artifact drifted from the canonical runtime helper output")
    return tuple(issues)
