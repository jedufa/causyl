"""Canonical builder turn section and result-packet contract helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple


CONTRACT_DOC_REL = Path("docs/contracts/contract_builder_turn_output.md")
RESULT_PACKET_VERSION = "builder_result_packet_v1"

SECTION_ALIASES: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "goal": ("goal",),
        "done": ("done", "change summary", "summary", "what changed"),
        "changed_files": ("changed files",),
        "validation_commands": ("validation commands run", "validation run", "local verification"),
        "assumptions": ("assumptions made", "assumptions", "blockers / assumptions"),
        "residual_risks": (
            "residual risks / gaps",
            "residual risks",
            "residual risk",
            "residual risks / proof gaps",
            "proof gaps",
        ),
        "next_owner": ("next owner",),
        "continuation_point": ("continuation point",),
        "notes": ("notes",),
    }
)

RESULT_PACKET_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "required_sections",
    "present_sections",
    "missing_sections",
    "changed_files",
    "validation_commands",
    "assumptions",
    "residual_risks",
    "next_owner",
    "continuation_point",
    "scope_fence",
    "claim_strength",
    "contract_violations",
)

_PATHLIKE_RE = re.compile(r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class BuilderTurnContractV1:
    version: str
    canonical_doc_rel: Path
    section_aliases: Mapping[str, Tuple[str, ...]]
    result_packet_fields: Tuple[str, ...]


def builder_turn_contract_v1() -> BuilderTurnContractV1:
    return BuilderTurnContractV1(
        version="builder_turn_contract_v1",
        canonical_doc_rel=CONTRACT_DOC_REL,
        section_aliases=SECTION_ALIASES,
        result_packet_fields=RESULT_PACKET_FIELDS,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def builder_turn_contract_revision(*, repo_root: Path | None = None) -> str:
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


def required_builder_sections(*, normalized_ir: Mapping[str, Any] | None = None) -> Tuple[str, ...]:
    normalized_ir = dict(normalized_ir or {})
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {}
    required: list[str] = []
    if str(normalized_ir.get("claim_scope") or "").strip() == "bounded_repo_change":
        required.append("changed_files")
    if str(normalized_ir.get("mode") or "").strip() == "build" or list(normalized_ir.get("validation_plan") or []):
        required.append("validation_commands")
    for raw in list(metadata.get("builder_required_sections") or []):
        key = canonical_section_key(str(raw))
        if key and key not in required:
            required.append(key)
    return tuple(required)


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
            item = stripped[1:].strip()
            if item.endswith(":"):
                continue
            items.append(item)
            continue
        items.append(stripped)
    return items


def _extract_text(lines: Sequence[str]) -> str:
    return "\n".join([str(line).strip() for line in lines if str(line).strip()]).strip()


def _validation_claim_strength(commands: Sequence[str]) -> str:
    normalized = [" ".join(str(cmd).strip().lower().split()) for cmd in commands if str(cmd).strip()]
    if not normalized:
        return "manual_or_missing_validation"
    broad_commands = []
    for cmd in normalized:
        if cmd in {"pytest", "pytest -q", "python -m pytest", "python -m pytest -q", "nox", "tox"}:
            broad_commands.append(cmd)
            continue
        if cmd.startswith("pytest ") or cmd.startswith("python -m pytest "):
            targeted_markers = (" tests/", "::", " -k ", " -m ", ".py", " --maxfail=", " --lf", " --ff")
            if not any(marker in cmd for marker in targeted_markers):
                broad_commands.append(cmd)
    if broad_commands and len(broad_commands) == len(normalized):
        return "broad_validation_scope"
    return "bounded_to_explicit_validation_commands"


def _extract_mutation_claim_paths(lines: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for raw in lines:
        stripped = str(raw or "").strip()
        if not stripped:
            continue
        for match in _PATHLIKE_RE.finditer(stripped):
            paths.append(str(match.group("path") or ""))
    return paths


def build_result_packet(
    content: str,
    *,
    normalized_ir: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_ir = dict(normalized_ir or {})
    sections = _iter_sections(content)
    required_sections = list(required_builder_sections(normalized_ir=normalized_ir))
    present_sections = [key for key in SECTION_ALIASES if key in sections and _extract_text(sections[key])]
    missing_sections = [key for key in required_sections if key not in present_sections]
    changed_files = _extract_bullets(sections.get("changed_files", []))
    validation_commands = _extract_bullets(sections.get("validation_commands", []))
    assumptions = _extract_bullets(sections.get("assumptions", []))
    residual_risks = _extract_bullets(sections.get("residual_risks", []))
    next_owner = _extract_text(sections.get("next_owner", []))
    continuation_point = _extract_text(sections.get("continuation_point", []))
    summary_paths = _extract_mutation_claim_paths(sections.get("done", []))
    return {
        "schema_version": RESULT_PACKET_VERSION,
        "required_sections": required_sections,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "changed_files": changed_files,
        "validation_commands": validation_commands,
        "assumptions": assumptions,
        "residual_risks": residual_risks,
        "next_owner": next_owner,
        "continuation_point": continuation_point,
        "scope_fence": {
            "claim_scope": str(normalized_ir.get("claim_scope") or ""),
            "allowed_files": list(normalized_ir.get("allowed_files") or []),
            "summary_mutation_claims": summary_paths,
        },
        "claim_strength": _validation_claim_strength(validation_commands),
        "contract_violations": [],
    }


def validate_builder_result_packet(
    packet: Mapping[str, Any],
    *,
    normalized_changed_files: Iterable[str] = (),
    allowed_files_ok: bool = True,
) -> Tuple[str, Tuple[str, ...]]:
    missing_sections = [str(item) for item in list(packet.get("missing_sections") or []) if str(item).strip()]
    if "changed_files" in missing_sections:
        return "missing_changed_files_section", tuple(missing_sections)
    if "validation_commands" in missing_sections:
        return "missing_validation_commands_section", tuple(missing_sections)
    if missing_sections:
        return "missing_required_sections", tuple(missing_sections)
    if not allowed_files_ok:
        return "changed_files_outside_allowed_scope", ()
    summary_claims = {str(item).strip().lstrip("./") for item in list(((packet.get("scope_fence") or {}).get("summary_mutation_claims") or [])) if str(item).strip()}
    changed_set = {str(item).strip().lstrip("./") for item in normalized_changed_files if str(item).strip()}
    if summary_claims and changed_set and not summary_claims.issubset(changed_set):
        return "mutation_claim_outside_changed_files", tuple(sorted(summary_claims - changed_set))
    return "", ()


def validate_builder_turn_contract_v1() -> Tuple[str, ...]:
    contract = builder_turn_contract_v1()
    issues = []
    for required_key in ("changed_files", "validation_commands", "assumptions", "residual_risks", "next_owner", "continuation_point"):
        if required_key not in contract.section_aliases:
            issues.append(f"missing canonical section alias for {required_key}")
    if contract.result_packet_fields[:4] != ("schema_version", "required_sections", "present_sections", "missing_sections"):
        issues.append("result packet field ordering drifted")
    return tuple(issues)
