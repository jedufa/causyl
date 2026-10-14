"""Deterministic compiled prompt rendering from IR and context manifest."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

from infra.controlplane import builder_turn_contract as _builder_contract
from infra.controlplane import verifier_review_contract as _verifier_contract


ROLE_SET = {"builder", "verifier", "repair", "generalist"}
ROLE_ALIASES = {
    "orchestrator": "generalist",
    "assurer": "generalist",
}
_BUILDER_SECTION_LABELS = {
    "goal": "Goal",
    "done": "Done",
    "changed_files": "Changed files",
    "validation_commands": "Validation Commands Run",
    "assumptions": "Assumptions Made",
    "residual_risks": "Residual Risks / Gaps",
    "next_owner": "Next Owner",
    "continuation_point": "Continuation Point",
    "notes": "Notes",
}
_VERIFIER_BLOCK_LABELS = {
    "findings": "Findings",
    "boundary_review": "Boundary Review",
    "proof_review": "Proof Review",
    "validation_review": "Validation Review",
    "confidence_hints": "Confidence",
    "pending_actions": "Pending Actions",
    "stop_rule": "Stop Rule",
    "terminal_blockers": "Terminal Blockers",
    "one_iteration_assessment": "One-Iteration Assessment",
}


def _canonical_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    canonical = ROLE_ALIASES.get(normalized, normalized)
    if canonical not in ROLE_SET:
        raise ValueError("role must be builder, verifier, repair, generalist, orchestrator, or assurer")
    return canonical


def _json_list(value: Any) -> list[Any]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except Exception:
        return []
    return list(loaded) if isinstance(loaded, list) else []


def _role_contract_metadata(canonical_role: str) -> Dict[str, str]:
    if canonical_role == "builder":
        contract = _builder_contract.builder_turn_contract_v1()
        return {
            "contract_label": "Builder Contract",
            "contract_version": contract.version,
            "result_packet_version": _builder_contract.RESULT_PACKET_VERSION,
            "contract_doc_rel": contract.canonical_doc_rel.as_posix(),
            "contract_revision": _builder_contract.builder_turn_contract_revision(),
        }
    if canonical_role == "verifier":
        contract = _verifier_contract.verifier_review_contract_v1()
        return {
            "contract_label": "Verifier Contract",
            "contract_version": contract.version,
            "result_packet_version": _verifier_contract.RESULT_PACKET_VERSION,
            "contract_doc_rel": contract.canonical_doc_rel.as_posix(),
            "contract_revision": _verifier_contract.verifier_review_contract_revision(),
        }
    return {}


def _resolved_role_contract_metadata(canonical_role: str, metadata: Mapping[str, Any]) -> Dict[str, str]:
    fallback = _role_contract_metadata(canonical_role)
    if canonical_role == "builder":
        return {
            **fallback,
            "contract_version": str(metadata.get("builder_contract_version") or fallback.get("contract_version") or "").strip(),
            "result_packet_version": str(
                metadata.get("builder_result_packet_version") or fallback.get("result_packet_version") or ""
            ).strip(),
            "contract_doc_rel": str(metadata.get("builder_contract_doc_rel") or fallback.get("contract_doc_rel") or "").strip(),
            "contract_revision": str(metadata.get("builder_contract_revision") or fallback.get("contract_revision") or "").strip(),
        }
    if canonical_role == "verifier":
        return {
            **fallback,
            "contract_version": str(metadata.get("verifier_contract_version") or fallback.get("contract_version") or "").strip(),
            "result_packet_version": str(
                metadata.get("verifier_result_packet_version") or fallback.get("result_packet_version") or ""
            ).strip(),
            "contract_doc_rel": str(metadata.get("verifier_contract_doc_rel") or fallback.get("contract_doc_rel") or "").strip(),
            "contract_revision": str(metadata.get("verifier_contract_revision") or fallback.get("contract_revision") or "").strip(),
        }
    return fallback


def _builder_required_section_labels(normalized_ir: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in _builder_contract.required_builder_sections(normalized_ir=normalized_ir):
        label = _BUILDER_SECTION_LABELS.get(str(key), "")
        if label and label not in labels:
            labels.append(label)
    return labels


def _verifier_required_block_labels(normalized_ir: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    required_keys = list(_verifier_contract.required_verifier_sections(normalized_ir=normalized_ir))
    if not required_keys:
        required_keys = ["findings", "boundary_review", "proof_review", "validation_review"]
    for key in required_keys:
        label = _VERIFIER_BLOCK_LABELS.get(str(key), "")
        if label and label not in labels:
            labels.append(label)
    return labels


def _role_contract_payload(canonical_role: str, normalized_ir: Mapping[str, Any], metadata: Mapping[str, Any]) -> Dict[str, Any]:
    contract_meta = _resolved_role_contract_metadata(canonical_role, metadata)
    payload: Dict[str, Any] = dict(contract_meta)
    if canonical_role == "builder":
        payload["required_sections"] = _builder_required_section_labels(normalized_ir)
    elif canonical_role == "verifier":
        payload["required_blocks"] = _verifier_required_block_labels(normalized_ir)
    return payload


def validate_compiled_prompt_artifact(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    role = _canonical_role(str(artifact.get("canonical_role") or artifact.get("role") or ""))
    prompt_text = str(artifact.get("prompt_text") or "")
    errors: list[str] = []
    contract_version = str(artifact.get("contract_version") or "").strip()
    result_packet_version = str(artifact.get("result_packet_version") or "").strip()
    contract_doc_rel = str(artifact.get("contract_doc_rel") or "").strip()
    contract_revision = str(artifact.get("contract_revision") or "").strip()
    contract = artifact.get("contract") if isinstance(artifact.get("contract"), dict) else {}

    if role in {"builder", "verifier"}:
        if not contract_version:
            errors.append("missing_contract_version")
        if not result_packet_version:
            errors.append("missing_result_packet_version")
        if not contract_doc_rel:
            errors.append("missing_contract_doc_rel")
        if not contract_revision:
            errors.append("missing_contract_revision")
        header = "Builder Contract:" if role == "builder" else "Verifier Contract:"
        if header not in prompt_text:
            errors.append("missing_contract_header")
        if not contract_version or f"contract_version: {contract_version}" not in prompt_text:
            errors.append("missing_contract_version_line")
        if not result_packet_version or f"result_packet_version: {result_packet_version}" not in prompt_text:
            errors.append("missing_result_packet_version_line")
        if not contract_doc_rel or f"contract_doc: {contract_doc_rel}" not in prompt_text:
            errors.append("missing_contract_doc_line")
        if not contract_revision or f"contract_revision: {contract_revision}" not in prompt_text:
            errors.append("missing_contract_revision_line")
        if not contract:
            errors.append("missing_contract_payload")
        if role == "builder":
            required_sections = [str(item).strip() for item in list(contract.get("required_sections") or []) if str(item).strip()]
            if not required_sections:
                errors.append("missing_required_sections_payload")
            required_sections_line = f"required_sections: {', '.join(required_sections)}" if required_sections else ""
            if not required_sections_line or required_sections_line not in prompt_text:
                errors.append("missing_required_sections_line")
        if role == "verifier":
            required_blocks = [str(item).strip() for item in list(contract.get("required_blocks") or []) if str(item).strip()]
            if not required_blocks:
                errors.append("missing_required_blocks_payload")
            required_blocks_line = f"required_blocks: {', '.join(required_blocks)}" if required_blocks else ""
            if not required_blocks_line or required_blocks_line not in prompt_text:
                errors.append("missing_required_blocks_line")

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "compiled_prompt_contract_invalid",
        "errors": errors,
    }


def _lines_for_prompt(*, role: str, normalized_ir: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    requested_role = str(role or "").strip().lower()
    canonical_role = _canonical_role(requested_role)
    lines = [
        f"Compiled Role: {canonical_role}",
        f"IR Version: {normalized_ir.get('ir_version')}",
        f"IR Fingerprint: {manifest.get('ir_fingerprint')}",
        f"Manifest Fingerprint: {manifest.get('fingerprint')}",
        "",
        "Goal:",
        str(normalized_ir.get("goal") or ""),
        "",
        "Mode:",
        str(normalized_ir.get("mode") or ""),
        "",
        "Claim Scope:",
        str(normalized_ir.get("claim_scope") or ""),
        "",
        "Required Output Kind:",
        str(normalized_ir.get("required_output_kind") or ""),
        "",
        "Constraints:",
    ]
    for row in list(normalized_ir.get("constraints") or []):
        lines.append(f"- {row}")
    lines.extend(["", "Allowed Files:"])
    allowed_files = list(normalized_ir.get("allowed_files") or [])
    if allowed_files:
        for row in allowed_files:
            lines.append(f"- {row}")
    else:
        lines.append("- <none>")
    lines.extend(["", "Validation Plan:"])
    validation_plan = list(normalized_ir.get("validation_plan") or [])
    if validation_plan:
        for row in validation_plan:
            lines.append(f"- {row}")
    else:
        lines.append("- <none>")
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    execution_family = normalized_ir.get("execution_family") if isinstance(normalized_ir.get("execution_family"), dict) else {}
    compiled_mode = str(metadata.get("compiled_work_order_mode") or "").strip()
    job_shape = str(metadata.get("job_shape") or "").strip()
    if job_shape:
        lines.extend(
            [
                "",
                "Job Shape:",
                f"- name: {job_shape}",
                f"- source: {str(metadata.get('job_shape_source') or '').strip()}",
                f"- compatibility_mode: {str(bool(metadata.get('job_shape_compatibility_mode'))).lower()}",
            ]
        )
    family_kind = str(execution_family.get("contract_kind") or metadata.get("execution_family_contract_kind") or "").strip()
    if family_kind:
        lines.extend(["", "Execution Family Contract:"])
        for label, source, key in (
            ("Contract Kind", execution_family, "contract_kind"),
            ("Phase", execution_family, "phase"),
            ("Source Intent UID", execution_family, "source_intent_uid"),
            ("Parent UID", execution_family, "parent_uid"),
            ("Child UID", execution_family, "child_uid"),
            ("Child Kind", execution_family, "child_kind"),
            ("Required Final Child Kind", execution_family, "required_final_child_kind"),
            ("Required Final Child UID", execution_family, "required_final_child_uid"),
            ("Integration Audit Mode", execution_family, "integration_audit_mode"),
            ("Trust Hardening Mode", execution_family, "trust_hardening_mode"),
            ("Status Promotion Policy", execution_family, "status_promotion_policy"),
            ("Receipt Selection Policy", execution_family, "receipt_selection_policy"),
        ):
            value = str(source.get(key) or "").strip()
            if value:
                lines.append(f"- {label}: {value}")
        for label, key in (
            ("Child UIDs", "child_uids"),
            ("Child Job IDs", "child_job_ids"),
            ("Allowed Family Repo Paths", "allowed_repo_paths"),
            ("Allowed Family Ledger Paths", "allowed_ledger_paths"),
            ("Closeout Rules", "closeout_rules"),
        ):
            rows = list(execution_family.get(key) or [])
            if rows:
                lines.append(f"- {label}: {', '.join([str(row) for row in rows])}")
    lines.extend(["", "Resolved Context Manifest:"])
    files = list(manifest.get("files") or [])
    if files:
        for row in files:
            lines.append(f"- {row.get('path')} [{row.get('source')}]")
    else:
        lines.append("- <none>")
    omitted = list(manifest.get("omitted_refs") or [])
    if omitted:
        lines.extend(["", "Omitted Context Refs:"])
        for row in omitted:
            lines.append(f"- {row.get('path')} ({row.get('reason')})")
    continuation_bootstrap = manifest.get("continuation_bootstrap") if isinstance(manifest.get("continuation_bootstrap"), dict) else {}
    if continuation_bootstrap:
        lines.extend(
            [
                "",
                "Continuation Bootstrap:",
                f"- status: {str(continuation_bootstrap.get('status') or 'none')}",
                f"- selected_source: {str(continuation_bootstrap.get('selected_source') or 'none')}",
                f"- reason: {str(continuation_bootstrap.get('reason') or '')}",
            ]
        )
        visible_gaps = [str(item).strip() for item in list(continuation_bootstrap.get("visible_gaps") or []) if str(item).strip()]
        if visible_gaps:
            lines.append(f"- visible_gaps: {', '.join(visible_gaps)}")
    if compiled_mode:
        lines.extend(["", "Compiled Work Order:"])
        lines.append(f"- mode: {compiled_mode}")
        status = str(metadata.get("compiled_work_order_status") or "").strip()
        if status:
            lines.append(f"- status: {status}")
        artifact_rel = str(metadata.get("compiled_work_order_artifact_rel") or "").strip()
        if artifact_rel:
            lines.append(f"- artifact_rel: {artifact_rel}")
        if compiled_mode == "authoritative":
            for label, key in (
                ("handoff_kind", "compiled_work_order_handoff_kind"),
                ("precedence", "compiled_work_order_precedence"),
                ("effective_path", "compiled_work_order_effective_path"),
                ("scope_objective", "compiled_work_order_scope_objective"),
                ("required_context_bundle", "compiled_work_order_required_context_bundle"),
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    lines.append(f"- {label}: {value}")
            in_scope = [str(item) for item in _json_list(metadata.get("compiled_work_order_scope_in_scope")) if str(item).strip()]
            out_of_scope = [str(item) for item in _json_list(metadata.get("compiled_work_order_scope_out_of_scope")) if str(item).strip()]
            validation_checks = [str(item) for item in _json_list(metadata.get("compiled_work_order_validation_checks")) if str(item).strip()]
            expected_outputs = [row for row in _json_list(metadata.get("compiled_work_order_expected_outputs")) if isinstance(row, dict)]
            if in_scope:
                lines.append(f"- in_scope: {', '.join(in_scope)}")
            if out_of_scope:
                lines.append(f"- out_of_scope: {', '.join(out_of_scope)}")
            if validation_checks:
                lines.append(f"- validation_checks: {', '.join(validation_checks)}")
            if expected_outputs:
                for row in expected_outputs:
                    output_id = str(row.get("output_id") or "").strip()
                    kind = str(row.get("kind") or "").strip()
                    summary = str(row.get("summary") or "").strip()
                    required = str(bool(row.get("required", True))).lower()
                    lines.append(f"- expected_output: {output_id} [{kind}] required={required} | {summary}")
        else:
            fallback_reason = str(metadata.get("compiled_work_order_fallback_reason") or "").strip()
            if fallback_reason:
                lines.append(f"- fallback_reason: {fallback_reason}")
            reason_codes = [str(item) for item in _json_list(metadata.get("compiled_work_order_reason_codes")) if str(item).strip()]
            if reason_codes:
                lines.append(f"- reason_codes: {', '.join(reason_codes)}")
    lines.extend(
        [
            "",
            "Output Contract:",
            f"- emit only content consistent with required_output_kind={normalized_ir.get('required_output_kind')}",
            f"- stay within claim_scope={normalized_ir.get('claim_scope')}",
            "- do not widen context beyond the resolved manifest",
        ]
    )
    contract_meta = _role_contract_payload(canonical_role, normalized_ir, metadata)
    if canonical_role == "builder":
        required_sections = list(contract_meta.get("required_sections") or [])
        lines.extend(
            [
                "",
                "Builder Contract:",
                f"- contract_version: {contract_meta.get('contract_version')}",
                f"- result_packet_version: {contract_meta.get('result_packet_version')}",
                f"- contract_doc: {contract_meta.get('contract_doc_rel')}",
                f"- contract_revision: {contract_meta.get('contract_revision')}",
                f"- required_sections: {', '.join(required_sections)}",
                "- fail closed if a required builder section is missing or scope claims exceed Changed files",
            ]
        )
    if canonical_role == "verifier":
        verifier_contract_version = str(metadata.get("verifier_contract_version") or "").strip()
        verifier_result_packet_version = str(metadata.get("verifier_result_packet_version") or "").strip()
        required_blocks = list(contract_meta.get("required_blocks") or [])
        lines.extend(
            [
                "",
                "Verifier Contract:",
                f"- contract_version: {contract_meta.get('contract_version')}",
                f"- result_packet_version: {contract_meta.get('result_packet_version')}",
                f"- contract_doc: {contract_meta.get('contract_doc_rel')}",
                f"- contract_revision: {contract_meta.get('contract_revision')}",
                f"- required_blocks: {', '.join(required_blocks)}",
                "- state an explicit VERDICT line",
                "- classify structural invalidity before semantic judgment",
            ]
        )
        if verifier_contract_version == "verifier_review_contract_v1" or verifier_result_packet_version == "verifier_review_packet_v1":
            lines.extend(
                [
                    "- use canonical review blocks: Findings, Boundary Review, Proof Review, Validation Review",
                    "- keep Confidence advisory-only; it cannot replace proof review",
                ]
            )
    return lines


def render_compiled_prompt(
    *,
    role: str,
    normalized_ir: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    canonical_role = _canonical_role(role)
    lines = _lines_for_prompt(role=canonical_role, normalized_ir=normalized_ir, manifest=manifest)
    prompt_text = "\n".join(lines).rstrip() + "\n"
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    artifact = {
        "prompt_version": "compiled_prompt_v1",
        "role": str(role or "").strip().lower(),
        "canonical_role": canonical_role,
        "ir_fingerprint": str(manifest.get("ir_fingerprint") or ""),
        "manifest_fingerprint": str(manifest.get("fingerprint") or ""),
        "prompt_text": prompt_text,
        "prompt_sha256": prompt_hash,
    }
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    artifact.update(_resolved_role_contract_metadata(canonical_role, metadata))
    artifact["contract"] = _role_contract_payload(canonical_role, normalized_ir, metadata)
    validation = validate_compiled_prompt_artifact(artifact)
    if not validation["ok"]:
        raise ValueError(f"compiled prompt artifact invalid: {', '.join(validation['errors'])}")
    return artifact
