"""Canonical verifier verdict/review block and result-packet helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Tuple


CONTRACT_DOC_REL = Path("docs/contracts/contract_verifier_review_output.md")
RESULT_PACKET_VERSION = "verifier_review_packet_v1"
ALLOWED_VERDICTS: Tuple[str, ...] = ("PASS", "FAIL", "UNKNOWN", "PASS_PENDING_ACTIONS")
_EXPLICIT_NONE_FINDINGS = frozenset(
    {
        "none",
        "none.",
        "no findings",
        "no material findings",
        "no actionable findings",
        "none identified",
    }
)
_NEGATIVE_FINDING_TOKENS = (
    "blocked",
    "contract",
    "drift",
    "error",
    "fail",
    "finding",
    "gap",
    "insufficient",
    "issue",
    "missing",
    "regression",
    "risk",
    "scope",
    "violation",
)

_NON_NEGATIVE_SEVERITY_HEADS = frozenset({"low", "info", "severity low", "severity info"})
_NON_NEGATIVE_FINDING_TAILS = frozenset(
    {
        "none",
        "none.",
        "no findings",
        "no material findings",
        "no actionable findings",
        "none identified",
        "no issues",
        "no issue",
        "no regressions",
        "no risk",
    }
)

SECTION_ALIASES: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "findings": ("findings", "findings by severity"),
        "boundary_review": ("boundary review",),
        "proof_review": ("proof review",),
        "validation_review": ("validation review", "validation run review"),
        "confidence_hints": ("confidence", "confidence hints", "convergence hints"),
        "one_iteration_assessment": ("one-iteration assessment",),
        "pending_actions": ("pending actions",),
        "stop_rule": ("stop rule",),
        "terminal_blockers": ("terminal blockers",),
    }
)

REVIEW_FIELDS: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "boundary_review": ("scope_alignment", "contract_alignment"),
        "proof_review": ("evidence_status", "pass_fail_closed_check"),
        "validation_review": ("commands_reviewed", "outcome_support"),
        "confidence_hints": ("advisory_only",),
        "one_iteration_assessment": ("disposition",),
    }
)

RESULT_PACKET_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "verdict",
    "required_sections",
    "present_sections",
    "missing_sections",
    "findings",
    "boundary_review",
    "proof_review",
    "validation_review",
    "confidence_hints",
    "pending_actions",
    "stop_rule",
    "terminal_blockers",
    "one_iteration_assessment",
    "contract_violations",
)

_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*([A-Z_]+)\b")


@dataclass(frozen=True)
class VerifierReviewContractV1:
    version: str
    canonical_doc_rel: Path
    allowed_verdicts: Tuple[str, ...]
    section_aliases: Mapping[str, Tuple[str, ...]]
    review_fields: Mapping[str, Tuple[str, ...]]
    result_packet_fields: Tuple[str, ...]


def verifier_review_contract_v1() -> VerifierReviewContractV1:
    return VerifierReviewContractV1(
        version="verifier_review_contract_v1",
        canonical_doc_rel=CONTRACT_DOC_REL,
        allowed_verdicts=ALLOWED_VERDICTS,
        section_aliases=SECTION_ALIASES,
        review_fields=REVIEW_FIELDS,
        result_packet_fields=RESULT_PACKET_FIELDS,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def verifier_review_contract_revision(*, repo_root: Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    payload = (root / CONTRACT_DOC_REL).read_text(encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_section_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(label or "").strip().lower()).strip()


def canonical_section_key(label: str) -> str:
    normalized = normalize_section_label(label)
    for key, aliases in SECTION_ALIASES.items():
        if normalized in {normalize_section_label(alias) for alias in aliases}:
            return key
    return ""


def required_verifier_sections(*, normalized_ir: Mapping[str, Any] | None = None) -> Tuple[str, ...]:
    normalized_ir = dict(normalized_ir or {})
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    contract_version = str(metadata.get("verifier_contract_version") or "").strip()
    result_packet_version = str(metadata.get("verifier_result_packet_version") or "").strip()
    if contract_version != "verifier_review_contract_v1" and result_packet_version != RESULT_PACKET_VERSION:
        return ()
    return ("findings", "boundary_review", "proof_review", "validation_review")


def _iter_sections(text: str) -> Dict[str, list[str]]:
    sections: Dict[str, list[str]] = {}
    current_key = ""
    for raw in str(text or "").splitlines():
        stripped = str(raw or "").strip()
        clean = stripped.lstrip("#").strip()
        if clean.startswith("**") and clean.endswith("**") and len(clean) > 4:
            clean = clean[2:-2].strip()
        matched_key = ""
        tail = ""
        lower = clean.lower()
        for key, aliases in SECTION_ALIASES.items():
            for alias in aliases:
                alias_lower = alias.lower()
                if lower == alias_lower:
                    matched_key = key
                    tail = ""
                    break
                if lower.startswith(alias_lower + ":"):
                    matched_key = key
                    tail = clean[len(alias) + 1 :].strip()
                    break
            if matched_key:
                break
        if matched_key:
            current_key = matched_key
            sections.setdefault(current_key, [])
            if tail:
                sections[current_key].append(tail)
            continue
        if current_key:
            sections[current_key].append(raw.rstrip("\n"))
    return sections


def _extract_bullets(lines: Sequence[str]) -> list[str]:
    items: list[str] = []
    for raw in lines:
        stripped = str(raw or "").strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*")):
            items.append(stripped[1:].strip())
            continue
        items.append(stripped)
    return items


def _extract_field_map(lines: Sequence[str]) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for item in _extract_bullets(lines):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        normalized = normalize_section_label(key).replace(" ", "_")
        if normalized and normalized not in fields:
            fields[normalized] = value.strip()
    return fields


def _field_value(fields: Mapping[str, str], aliases: Sequence[str]) -> str:
    for alias in aliases:
        value = str(fields.get(alias) or "").strip()
        if value:
            return value
    return ""


def _normalized_yes_no_status(value: str, *, yes_token: str, no_token: str) -> str:
    token = _leading_value_token(value)
    if token in {"yes", "true", "pass", "passed"}:
        return yes_token
    if token in {"no", "false", "fail", "failed"}:
        return no_token
    return ""


def _normalize_boundary_review(raw_fields: Mapping[str, str]) -> Dict[str, str]:
    fields = dict(raw_fields)
    if not str(fields.get("scope_alignment") or "").strip():
        in_scope = _field_value(fields, ("in_scope_only",))
        normalized = _normalized_yes_no_status(in_scope, yes_token="pass", no_token="fail")
        if normalized:
            suffix = f" - in_scope_only: {in_scope.strip()}" if in_scope.strip() else ""
            fields["scope_alignment"] = normalized + suffix
    if not str(fields.get("contract_alignment") or "").strip():
        overlap = _field_value(fields, ("sibling_overlap_found",))
        notes = _field_value(fields, ("boundary_notes",))
        status = _normalized_yes_no_status(overlap, yes_token="fail", no_token="pass")
        parts: list[str] = []
        if overlap.strip():
            parts.append(f"sibling_overlap_found: {overlap.strip()}")
        if notes.strip():
            parts.append(notes.strip())
        if status:
            fields["contract_alignment"] = status + (f" - {'; '.join(parts)}" if parts else "")
    return fields


def _normalize_proof_review(raw_fields: Mapping[str, str]) -> Dict[str, str]:
    fields = dict(raw_fields)
    if not str(fields.get("evidence_status") or "").strip():
        sufficient = _field_value(fields, ("validation_evidence_sufficient",))
        normalized = _normalized_yes_no_status(sufficient, yes_token="sufficient", no_token="insufficient")
        if normalized:
            suffix = f" - validation_evidence_sufficient: {sufficient.strip()}" if sufficient.strip() else ""
            fields["evidence_status"] = normalized + suffix
    if not str(fields.get("pass_fail_closed_check") or "").strip():
        remaining = _field_value(fields, ("remaining_proof_gaps",))
        token = _leading_value_token(remaining)
        if token == "none":
            fields["pass_fail_closed_check"] = "satisfied - remaining_proof_gaps: none"
        elif remaining.strip():
            fields["pass_fail_closed_check"] = f"unsatisfied - remaining_proof_gaps: {remaining.strip()}"
    return fields


def _normalize_validation_review(raw_fields: Mapping[str, str]) -> Dict[str, str]:
    fields = dict(raw_fields)
    if not str(fields.get("commands_reviewed") or "").strip():
        commands = _field_value(fields, ("commands_checked",))
        if commands:
            fields["commands_reviewed"] = commands
    if not str(fields.get("outcome_support") or "").strip():
        results = _field_value(fields, ("results_checked",))
        if results:
            fields["outcome_support"] = results
    return fields


def _normalized_review_fields(section_key: str, lines: Sequence[str]) -> Dict[str, str]:
    fields = _extract_field_map(lines)
    if section_key == "boundary_review":
        return _normalize_boundary_review(fields)
    if section_key == "proof_review":
        return _normalize_proof_review(fields)
    if section_key == "validation_review":
        return _normalize_validation_review(fields)
    return fields


def build_result_packet(
    content: str,
    *,
    normalized_ir: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_ir = dict(normalized_ir or {})
    lines = str(content or "").splitlines()
    first_line = lines[0] if lines else ""
    verdict_match = _VERDICT_RE.match(first_line)
    verdict = str(verdict_match.group(1) or "").strip() if verdict_match else ""
    sections = _iter_sections(content)
    required_sections = list(required_verifier_sections(normalized_ir=normalized_ir))
    present_sections = [key for key in SECTION_ALIASES if key in sections and _extract_bullets(sections[key])]
    missing_sections = [key for key in required_sections if key not in present_sections]
    return {
        "schema_version": RESULT_PACKET_VERSION,
        "verdict": verdict,
        "required_sections": required_sections,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "findings": _extract_bullets(sections.get("findings", [])),
        "boundary_review": _normalized_review_fields("boundary_review", sections.get("boundary_review", [])),
        "proof_review": _normalized_review_fields("proof_review", sections.get("proof_review", [])),
        "validation_review": _normalized_review_fields("validation_review", sections.get("validation_review", [])),
        "confidence_hints": _normalized_review_fields("confidence_hints", sections.get("confidence_hints", [])),
        "pending_actions": _extract_bullets(sections.get("pending_actions", [])),
        "stop_rule": _extract_bullets(sections.get("stop_rule", [])),
        "terminal_blockers": _extract_bullets(sections.get("terminal_blockers", [])),
        "one_iteration_assessment": _normalized_review_fields("one_iteration_assessment", sections.get("one_iteration_assessment", [])),
        "contract_violations": [],
    }


def _missing_field_reason(section_key: str) -> str:
    return f"missing_{section_key}_fields"


def _leading_value_token(value: str) -> str:
    normalized = normalize_section_label(value)
    if not normalized:
        return ""
    return normalized.split(" ", 1)[0]


def _explicit_findings_shape(findings: Sequence[str]) -> str:
    raw_items = [str(item).strip() for item in findings if str(item).strip()]
    normalized = [normalize_section_label(item) for item in raw_items if normalize_section_label(item)]
    if not normalized:
        return "missing"
    if len(normalized) == 1 and normalized[0] in _EXPLICIT_NONE_FINDINGS:
        return "explicit_none"
    only_non_negative_markers = True
    for raw_item, item in zip(raw_items, normalized):
        if "|" in raw_item:
            parts = [normalize_section_label(part) for part in raw_item.split("|")]
            head = parts[0] if parts else ""
            tail_parts = [part for part in parts[1:] if part]
            tail_ok = bool(tail_parts) and all(part in _NON_NEGATIVE_FINDING_TAILS for part in tail_parts)
            if head in {"critical", "high", "medium", "low", "info"}:
                if head in {"critical", "high", "medium"}:
                    return "explicit_negative"
                if tail_parts and not tail_ok:
                    return "explicit_negative"
                continue
            if head in _NON_NEGATIVE_SEVERITY_HEADS:
                if tail_parts and not tail_ok:
                    return "explicit_negative"
                continue
        if any(token in item for token in _NEGATIVE_FINDING_TOKENS):
            if item in _EXPLICIT_NONE_FINDINGS:
                continue
            return "explicit_negative"
        if item not in _EXPLICIT_NONE_FINDINGS:
            only_non_negative_markers = False
    if only_non_negative_markers:
        return "explicit_none"
    return "vague"


def validate_verifier_result_packet(packet: Mapping[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    verdict = str(packet.get("verdict") or "").strip().upper()
    if not verdict:
        return "missing_verdict_token", ("verdict",)
    if verdict not in ALLOWED_VERDICTS:
        return "invalid_verdict_token", (verdict,)

    missing_sections = [str(item) for item in list(packet.get("missing_sections") or []) if str(item).strip()]
    if missing_sections:
        first = missing_sections[0]
        return f"missing_{first}_section", tuple(missing_sections)

    findings = [str(item).strip() for item in list(packet.get("findings") or []) if str(item).strip()]
    findings_shape = _explicit_findings_shape(findings)
    if findings_shape == "missing":
        return "missing_findings_section", ("findings",)
    if findings_shape == "vague":
        return "invalid_findings_block", ("findings",)

    for section_key in ("boundary_review", "proof_review", "validation_review"):
        required = REVIEW_FIELDS.get(section_key, ())
        fields = packet.get(section_key) if isinstance(packet.get(section_key), dict) else {}
        missing = [field for field in required if not str(fields.get(field) or "").strip()]
        if missing:
            return _missing_field_reason(section_key), tuple(missing)

    if verdict == "PASS":
        proof_review = packet.get("proof_review") if isinstance(packet.get("proof_review"), dict) else {}
        validation_review = packet.get("validation_review") if isinstance(packet.get("validation_review"), dict) else {}
        evidence_status = _leading_value_token(str(proof_review.get("evidence_status") or ""))
        fail_closed = _leading_value_token(str(proof_review.get("pass_fail_closed_check") or ""))
        outcome_support = _leading_value_token(str(validation_review.get("outcome_support") or ""))
        if evidence_status in {"", "missing", "insufficient", "fail", "failed", "unknown", "none", "not run"}:
            return "pass_without_explicit_proof_review", ("evidence_status",)
        if fail_closed in {"", "missing", "unsatisfied", "fail", "failed", "false", "unknown", "none"}:
            return "pass_without_explicit_proof_review", ("pass_fail_closed_check",)
        if outcome_support in {"", "missing", "insufficient", "not run", "unknown", "none", "fail", "failed"}:
            return "pass_without_explicit_validation_review", ("outcome_support",)
        if findings_shape not in {"explicit_none", "explicit_negative"}:
            return "pass_without_explicit_findings_review", ("findings",)

    if verdict == "PASS_PENDING_ACTIONS":
        pending_actions = [str(item).strip() for item in list(packet.get("pending_actions") or []) if str(item).strip()]
        stop_rule = [str(item).strip() for item in list(packet.get("stop_rule") or []) if str(item).strip()]
        if not pending_actions:
            return "missing_pending_actions_section", ("pending_actions",)
        if not stop_rule:
            return "missing_stop_rule_section", ("stop_rule",)

    return "", ()


def validate_verifier_review_contract_v1() -> Tuple[str, ...]:
    contract = verifier_review_contract_v1()
    issues = []
    for required_key in ("findings", "boundary_review", "proof_review", "validation_review"):
        if required_key not in contract.section_aliases:
            issues.append(f"missing canonical section alias for {required_key}")
    if contract.allowed_verdicts != ALLOWED_VERDICTS:
        issues.append("allowed verdicts drifted")
    if contract.result_packet_fields[:4] != ("schema_version", "verdict", "required_sections", "present_sections"):
        issues.append("result packet field ordering drifted")
    return tuple(issues)
