"""Deterministic structural classification for builder and verifier outputs."""

from __future__ import annotations

import fnmatch
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Mapping

from infra.controlplane import builder_turn_contract as btc
from infra.controlplane import verifier_review_contract as vrc


_VERDICT_RE = re.compile(r"(?im)^\s*VERDICT:\s*([A-Z_]+)\b")
_VALIDATION_HEADER_RE = re.compile(r"(?im)^\s*(?:[#*]+\s*)?Validation Commands Run\s*:?\s*(?:[*#]+)?\s*$")
_LOCAL_VERIFICATION_RE = re.compile(r"(?im)^\s*(?:[#*]+\s*)?Local verification\s*:?\s*(?:[*#]+)?\s*$")
def _strict_mode(normalized_ir: Mapping[str, Any]) -> bool:
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    mode = str(metadata.get("compiled_execution_mode") or "").strip().lower()
    return mode in {"soft_gate", "hard_gate"}


def _allowed_files_ok(paths: Iterable[str], allowed_files: Iterable[str]) -> bool:
    allowed = [str(row or "").strip() for row in allowed_files if str(row or "").strip()]
    if not allowed or "<scheduler_managed_scope>" in allowed:
        return True
    for path in paths:
        clean = _normalize_changed_file_path(path)
        if not clean:
            continue
        if not any(_allowed_file_matches(clean, candidate) for candidate in allowed):
            return False
    return True


def _normalize_changed_file_path(path: str) -> str:
    value = str(path or "").strip().strip("`").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered == "none" or lowered.startswith("none "):
        return ""
    link_match = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\)", value)
    if link_match:
        value = str(link_match.group(1) or "").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return ""
    candidate = Path(value)
    root = Path.cwd().resolve()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except Exception:
            return ""
    return value.replace("\\", "/").lstrip("./")


def _allowed_file_matches(path: str, allowed: str) -> bool:
    candidate = str(allowed or "").strip()
    if not candidate:
        return False
    normalized = _normalize_changed_file_path(candidate)
    candidate_dir_glob = candidate.endswith("/")
    normalized_dir_glob = bool(normalized) and normalized.endswith("/")
    if normalized and path == normalized:
        return True
    if normalized:
        normalized_dir = normalized.rstrip("/")
        if normalized_dir and path.startswith(normalized_dir + "/"):
            return True
    if fnmatch.fnmatchcase(path, candidate) or (bool(normalized) and fnmatch.fnmatchcase(path, normalized)):
        return True
    if candidate_dir_glob and fnmatch.fnmatchcase(path, candidate + "*"):
        return True
    if normalized_dir_glob and fnmatch.fnmatchcase(path, normalized + "*"):
        return True
    return False


def classify_builder_output(
    content: str,
    *,
    normalized_ir: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    text = str(content or "")
    normalized_ir = dict(normalized_ir or {})
    strict_mode = _strict_mode(normalized_ir)
    if not text.strip():
        return {"ok": False, "kind": "builder_empty", "reason": "empty_output"}
    required_output_kind = str(normalized_ir.get("required_output_kind") or "").strip()
    result_packet = btc.build_result_packet(text, normalized_ir=normalized_ir)
    changed_files = [_normalize_changed_file_path(path) for path in list(result_packet.get("changed_files") or [])]
    changed_files = [path for path in changed_files if path]
    has_validation = bool(result_packet.get("validation_commands")) or bool(_VALIDATION_HEADER_RE.search(text) or _LOCAL_VERIFICATION_RE.search(text))
    if strict_mode and required_output_kind == "unified_diff" and "```diff" not in text:
        return {"ok": False, "kind": "builder_missing_diff", "reason": "missing_required_diff_block"}
    allowed_files_ok = _allowed_files_ok(changed_files, normalized_ir.get("allowed_files") or [])
    violation_reason, violation_details = btc.validate_builder_result_packet(
        result_packet,
        normalized_changed_files=changed_files,
        allowed_files_ok=allowed_files_ok,
    )
    if strict_mode and not has_validation and "validation_commands" not in list(result_packet.get("required_sections") or []):
        result_packet["required_sections"] = list(result_packet.get("required_sections") or []) + ["validation_commands"]
        result_packet["missing_sections"] = sorted(set(list(result_packet.get("missing_sections") or []) + ["validation_commands"]))
        violation_reason = "missing_validation_commands_section"
        violation_details = ("validation_commands",)
    if violation_reason:
        kind = "builder_missing_required_section" if violation_reason.startswith("missing_") else "builder_scope_fence_violation"
        if violation_reason == "changed_files_outside_allowed_scope":
            kind = "builder_out_of_scope_files"
        return {
            "ok": False,
            "kind": kind,
            "reason": violation_reason,
            "changed_files": changed_files,
            "result_packet": result_packet,
            "details": list(violation_details),
        }
    if has_validation or "```diff" in text or changed_files:
        return {
            "ok": True,
            "kind": "builder_structured_artifact",
            "reason": "recognized_builder_structure",
            "changed_files": changed_files,
            "required_output_kind": required_output_kind,
            "result_packet": result_packet,
        }
    return {"ok": False, "kind": "builder_unstructured", "reason": "missing_builder_structure"}


def classify_verifier_output(
    content: str,
    *,
    normalized_ir: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    text = str(content or "")
    normalized_ir = dict(normalized_ir or {})
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    if not text.strip():
        return {"ok": False, "kind": "verifier_empty", "reason": "empty_output"}
    first_line = text.splitlines()[0].strip() if text.splitlines() else ""
    match = _VERDICT_RE.search(text)
    if not match:
        return {"ok": False, "kind": "verifier_missing_verdict_header", "reason": "missing_verdict_header"}
    verdict = str(match.group(1) or "").strip()
    if not first_line.startswith("VERDICT:"):
        return {"ok": False, "kind": "verifier_noncanonical_prefix", "reason": "verdict_not_on_first_line", "verdict": verdict}
    required_output_kind = str(normalized_ir.get("required_output_kind") or "").strip()
    if normalized_ir.get("mode") == "verify" and required_output_kind not in {"verdict_report", "report", ""}:
        return {"ok": False, "kind": "verifier_wrong_output_kind", "reason": "invalid_verifier_output_kind", "verdict": verdict}
    result_packet = vrc.build_result_packet(text, normalized_ir=normalized_ir)
    verifier_contract_version = str(metadata.get("verifier_contract_version") or "").strip()
    verifier_result_packet_version = str(metadata.get("verifier_result_packet_version") or "").strip()
    if verifier_contract_version == "verifier_review_contract_v1" or verifier_result_packet_version == "verifier_review_packet_v1":
        violation_reason, violation_details = vrc.validate_verifier_result_packet(result_packet)
        if violation_reason:
            return {
                "ok": False,
                "kind": "verifier_missing_required_review_block" if violation_reason.startswith("missing_") else "verifier_fail_closed_violation",
                "reason": violation_reason,
                "verdict": verdict,
                "required_output_kind": required_output_kind,
                "result_packet": result_packet,
                "details": list(violation_details),
            }
    return {
        "ok": True,
        "kind": "verifier_structured_verdict",
        "reason": "recognized_verdict_header",
        "verdict": verdict,
        "required_output_kind": required_output_kind,
        "result_packet": result_packet,
    }
