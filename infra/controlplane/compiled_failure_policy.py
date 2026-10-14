"""Compiled execution failure taxonomy and retry disposition helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


_REPAIRABLE_BUILDER_REASONS = {
    "missing_changed_files_section",
    "missing_validation_commands_section",
    "missing_required_sections",
    "missing_required_diff_block",
}

_REPAIRABLE_VERIFIER_REASONS = {
    "missing_verdict_header",
    "verdict_not_on_first_line",
    "missing_findings_section",
    "missing_boundary_review_section",
    "missing_proof_review_section",
    "missing_validation_review_section",
    "missing_boundary_review_fields",
    "missing_proof_review_fields",
    "missing_validation_review_fields",
    "pass_without_explicit_proof_review",
    "pass_without_explicit_validation_review",
}


def repeated_structural_failure_disposition(iterations: List[Mapping[str, Any]]) -> Dict[str, Any]:
    if len(iterations) < 2:
        return {"stop": False, "class": "", "reason": "", "retryable": True}
    builder_classes: List[tuple[str, str]] = []
    verifier_classes: List[tuple[str, str]] = []
    for row in iterations:
        builder = row.get("builder") if isinstance(row.get("builder"), dict) else {}
        verifier = row.get("verifier") if isinstance(row.get("verifier"), dict) else {}
        bmeta = builder.get("meta") if isinstance(builder.get("meta"), dict) else {}
        vmeta = verifier.get("meta") if isinstance(verifier.get("meta"), dict) else {}
        bcls = bmeta.get("compiled_output_classification") if isinstance(bmeta.get("compiled_output_classification"), dict) else {}
        vcls = vmeta.get("compiled_output_classification") if isinstance(vmeta.get("compiled_output_classification"), dict) else {}
        builder_classes.append((str(bcls.get("kind") or ""), str(bcls.get("reason") or "")))
        verifier_classes.append((str(vcls.get("kind") or ""), str(vcls.get("reason") or "")))

    def _tail_count(classes: List[tuple[str, str]]) -> tuple[int, tuple[str, str]]:
        latest = classes[-1]
        if not latest[0]:
            return 0, latest
        count = 0
        for candidate in reversed(classes):
            if candidate != latest:
                break
            count += 1
        return count, latest

    builder_repeat_count, builder_latest = _tail_count(builder_classes)
    verifier_repeat_count, verifier_latest = _tail_count(verifier_classes)
    if builder_latest[0] and builder_latest[0] != "builder_structured_artifact":
        threshold = 3 if builder_latest[1] in _REPAIRABLE_BUILDER_REASONS else 2
        if builder_repeat_count >= threshold:
            return {
                "stop": True,
                "class": "compiled_builder_contract_violation",
                "reason": f"repeated_builder_structural_failure:{builder_latest[1] or builder_latest[0]}",
                "retryable": False,
            }
    if verifier_latest[0] and verifier_latest[0] != "verifier_structured_verdict":
        threshold = 3 if verifier_latest[1] in _REPAIRABLE_VERIFIER_REASONS else 2
        if verifier_repeat_count >= threshold:
            return {
                "stop": True,
                "class": "compiled_verifier_contract_violation",
                "reason": f"repeated_verifier_structural_failure:{verifier_latest[1] or verifier_latest[0]}",
                "retryable": False,
            }
    return {"stop": False, "class": "", "reason": "", "retryable": True}


def classify_compiled_retry_disposition(*, receipt: Mapping[str, Any]) -> Dict[str, Any]:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    if iterations:
        last = iterations[-1] if isinstance(iterations[-1], dict) else {}
        last_builder = last.get("builder") if isinstance(last.get("builder"), dict) else {}
        last_verifier = last.get("verifier") if isinstance(last.get("verifier"), dict) else {}
        last_bmeta = last_builder.get("meta") if isinstance(last_builder.get("meta"), dict) else {}
        last_vmeta = last_verifier.get("meta") if isinstance(last_verifier.get("meta"), dict) else {}
        last_bcls = (
            last_bmeta.get("compiled_output_classification")
            if isinstance(last_bmeta.get("compiled_output_classification"), dict)
            else {}
        )
        last_vcls = (
            last_vmeta.get("compiled_output_classification")
            if isinstance(last_vmeta.get("compiled_output_classification"), dict)
            else {}
        )
        if bool(last_bcls.get("ok")) and bool(last_vcls.get("ok")):
            return {"class": "", "reason": "", "retryable": True, "source": ""}
    repeated = repeated_structural_failure_disposition(iterations)
    if repeated.get("stop"):
        return {
            "class": str(repeated.get("class") or ""),
            "reason": str(repeated.get("reason") or ""),
            "retryable": bool(repeated.get("retryable")),
            "source": "compiled_structural_policy",
        }
    for row in reversed(iterations):
        if not isinstance(row, dict):
            continue
        verifier = row.get("verifier") if isinstance(row.get("verifier"), dict) else {}
        vmeta = verifier.get("meta") if isinstance(verifier.get("meta"), dict) else {}
        vcls = vmeta.get("compiled_output_classification") if isinstance(vmeta.get("compiled_output_classification"), dict) else {}
        if vcls and not bool(vcls.get("ok")):
            return {
                "class": "compiled_verifier_contract_violation",
                "reason": str(vcls.get("reason") or "invalid_verifier_output"),
                "retryable": False,
                "source": "compiled_structural_policy",
            }
    return {"class": "", "reason": "", "retryable": True, "source": ""}
