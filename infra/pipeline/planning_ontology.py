from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rs", ".sh", ".ps1", ".c", ".cc", ".cpp", ".h", ".hpp", ".html", ".css", ".scss", ".sass"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf", ".properties", ".env"}
DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc"}
DATA_EXTENSIONS = {".csv", ".tsv", ".sqlite", ".db", ".parquet", ".avro", ".jsonl"}
LOG_EXTENSIONS = {".log", ".trace", ".out", ".err"}
PATHLIKE_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
FILELIKE_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8}\b")

TASK_ARCHETYPES: Tuple[str, ...] = (
    "implementation_change",
    "design_new_surface",
    "repo_assessment",
    "information_retrieval",
    "documentation_contract",
    "verification_test",
    "configuration_change",
)

PROFILE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "implementation_change": {
        "retrieval_profile": "owner_first_implementation",
        "ranking_profile": "edit_owner_focus",
        "bundle_profile": "execution_bundle",
        "output_profile": "patch_contract",
        "output_contract_mode": "patch_contract",
        "bundle_requirements": {"edit_target": 1, "validation_context": 0, "integration_context": 0},
        "role_weights": {
            "retrieval": {"implementation_code": 2.2, "test_code": 1.15, "config_build": 1.35, "schema_contract": 0.95, "documentation": 0.8, "data_fixture": 0.65, "generated_artifact": 0.15, "log_trace": 0.25, "report_summary": 0.2, "unknown": 0.55},
            "target": {"implementation_code": 2.4, "test_code": 1.2, "config_build": 1.55, "schema_contract": 0.55, "documentation": 0.3, "data_fixture": 0.4, "generated_artifact": 0.05, "log_trace": 0.1, "report_summary": 0.08, "unknown": 0.35},
            "reference": {"implementation_code": 0.8, "test_code": 0.95, "config_build": 1.05, "schema_contract": 1.15, "documentation": 1.1, "data_fixture": 0.7, "generated_artifact": 0.2, "log_trace": 0.35, "report_summary": 0.25, "unknown": 0.55},
        },
    },
    "design_new_surface": {
        "retrieval_profile": "mixed_design_bundle",
        "ranking_profile": "design_support",
        "bundle_profile": "new_file_support_bundle",
        "output_profile": "design_contract",
        "output_contract_mode": "design_contract",
        "bundle_requirements": {"new_file_support_context": 1, "integration_context": 1, "interface_contract_context": 1, "validation_context": 0},
        "role_weights": {
            "retrieval": {"implementation_code": 1.45, "test_code": 1.0, "config_build": 1.15, "schema_contract": 1.4, "documentation": 1.25, "data_fixture": 0.75, "generated_artifact": 0.25, "log_trace": 0.3, "report_summary": 0.45, "unknown": 0.7},
            "target": {"implementation_code": 1.15, "test_code": 0.75, "config_build": 1.0, "schema_contract": 0.5, "documentation": 0.35, "data_fixture": 0.35, "generated_artifact": 0.08, "log_trace": 0.1, "report_summary": 0.15, "unknown": 0.35},
            "reference": {"implementation_code": 1.0, "test_code": 1.05, "config_build": 1.0, "schema_contract": 1.35, "documentation": 1.25, "data_fixture": 0.75, "generated_artifact": 0.2, "log_trace": 0.25, "report_summary": 0.4, "unknown": 0.65},
        },
    },
    "repo_assessment": {
        "retrieval_profile": "representative_system_scan",
        "ranking_profile": "representative_signal_mix",
        "bundle_profile": "assessment_bundle",
        "output_profile": "assessment_report_contract",
        "output_contract_mode": "assessment_report_contract",
        "bundle_requirements": {"representative_code_surface": 1, "representative_test_surface": 1, "representative_config_surface": 1},
        "role_weights": {
            "retrieval": {"implementation_code": 1.1, "test_code": 1.1, "config_build": 1.0, "schema_contract": 1.0, "documentation": 0.95, "data_fixture": 0.7, "generated_artifact": 0.35, "log_trace": 0.75, "report_summary": 0.6, "unknown": 0.7},
            "target": {"implementation_code": 0.2, "test_code": 0.2, "config_build": 0.2, "schema_contract": 0.1, "documentation": 0.05, "data_fixture": 0.05, "generated_artifact": 0.0, "log_trace": 0.0, "report_summary": 0.0, "unknown": 0.05},
            "reference": {"implementation_code": 1.15, "test_code": 1.15, "config_build": 1.0, "schema_contract": 1.0, "documentation": 0.95, "data_fixture": 0.7, "generated_artifact": 0.35, "log_trace": 0.8, "report_summary": 0.7, "unknown": 0.75},
        },
    },
    "information_retrieval": {
        "retrieval_profile": "evidence_lookup",
        "ranking_profile": "evidence_first_lookup",
        "bundle_profile": "lookup_bundle",
        "output_profile": "retrieval_answer_contract",
        "output_contract_mode": "retrieval_answer_contract",
        "bundle_requirements": {"read_only_reference": 1},
        "role_weights": {
            "retrieval": {"implementation_code": 1.0, "test_code": 0.85, "config_build": 1.0, "schema_contract": 1.1, "documentation": 1.05, "data_fixture": 0.75, "generated_artifact": 0.25, "log_trace": 0.4, "report_summary": 0.35, "unknown": 0.7},
            "target": {"implementation_code": 0.15, "test_code": 0.1, "config_build": 0.12, "schema_contract": 0.05, "documentation": 0.02, "data_fixture": 0.03, "generated_artifact": 0.0, "log_trace": 0.0, "report_summary": 0.0, "unknown": 0.03},
            "reference": {"implementation_code": 1.0, "test_code": 0.85, "config_build": 1.0, "schema_contract": 1.15, "documentation": 1.1, "data_fixture": 0.8, "generated_artifact": 0.25, "log_trace": 0.45, "report_summary": 0.4, "unknown": 0.75},
        },
    },
    "documentation_contract": {
        "retrieval_profile": "documentation_contract_lookup",
        "ranking_profile": "contract_first_reference",
        "bundle_profile": "contract_bundle",
        "output_profile": "retrieval_answer_contract",
        "output_contract_mode": "retrieval_answer_contract",
        "bundle_requirements": {"interface_contract_context": 1},
        "role_weights": {
            "retrieval": {"implementation_code": 0.9, "test_code": 0.75, "config_build": 1.1, "schema_contract": 1.45, "documentation": 1.4, "data_fixture": 0.55, "generated_artifact": 0.2, "log_trace": 0.25, "report_summary": 0.4, "unknown": 0.7},
            "target": {"implementation_code": 0.25, "test_code": 0.1, "config_build": 0.35, "schema_contract": 0.2, "documentation": 0.08, "data_fixture": 0.03, "generated_artifact": 0.0, "log_trace": 0.0, "report_summary": 0.0, "unknown": 0.05},
            "reference": {"implementation_code": 0.95, "test_code": 0.75, "config_build": 1.15, "schema_contract": 1.45, "documentation": 1.35, "data_fixture": 0.55, "generated_artifact": 0.2, "log_trace": 0.25, "report_summary": 0.45, "unknown": 0.7},
        },
    },
    "verification_test": {
        "retrieval_profile": "verification_focus",
        "ranking_profile": "test_and_guard_focus",
        "bundle_profile": "verification_bundle",
        "output_profile": "patch_contract",
        "output_contract_mode": "patch_contract",
        "bundle_requirements": {"validation_context": 0},
        "role_weights": {
            "retrieval": {"implementation_code": 1.25, "test_code": 1.8, "config_build": 1.0, "schema_contract": 0.95, "documentation": 0.85, "data_fixture": 0.85, "generated_artifact": 0.15, "log_trace": 0.35, "report_summary": 0.25, "unknown": 0.6},
            "target": {"implementation_code": 1.3, "test_code": 2.0, "config_build": 0.9, "schema_contract": 0.45, "documentation": 0.2, "data_fixture": 0.45, "generated_artifact": 0.05, "log_trace": 0.1, "report_summary": 0.05, "unknown": 0.3},
            "reference": {"implementation_code": 0.85, "test_code": 1.25, "config_build": 0.85, "schema_contract": 0.95, "documentation": 0.85, "data_fixture": 1.0, "generated_artifact": 0.15, "log_trace": 0.35, "report_summary": 0.2, "unknown": 0.55},
        },
    },
    "configuration_change": {
        "retrieval_profile": "config_surface_focus",
        "ranking_profile": "config_owner_focus",
        "bundle_profile": "config_bundle",
        "output_profile": "patch_contract",
        "output_contract_mode": "patch_contract",
        "bundle_requirements": {"edit_target": 1, "integration_context": 0},
        "role_weights": {
            "retrieval": {"implementation_code": 1.0, "test_code": 0.85, "config_build": 2.0, "schema_contract": 1.15, "documentation": 0.7, "data_fixture": 0.6, "generated_artifact": 0.15, "log_trace": 0.2, "report_summary": 0.15, "unknown": 0.55},
            "target": {"implementation_code": 0.95, "test_code": 0.55, "config_build": 2.25, "schema_contract": 0.75, "documentation": 0.15, "data_fixture": 0.15, "generated_artifact": 0.02, "log_trace": 0.02, "report_summary": 0.02, "unknown": 0.25},
            "reference": {"implementation_code": 0.75, "test_code": 0.75, "config_build": 1.1, "schema_contract": 1.0, "documentation": 0.8, "data_fixture": 0.6, "generated_artifact": 0.15, "log_trace": 0.2, "report_summary": 0.15, "unknown": 0.5},
        },
    },
}

_ASSESSMENT_TERMS = {
    "state of my repo", "state of the repo", "how reliable", "how healthy", "repo health", "repository health",
    "maintainability", "technical debt", "biggest risks", "what would break", "codebase quality", "architecture review",
    "repo assessment", "repository assessment", "maturity of my repo", "quality of my repo", "how mature",
}

_LOOKUP_TERMS = {
    "where is", "where are", "which file", "find where", "find the file", "locate", "what uses", "what references",
    "what defines", "show me where", "search for", "where do we", "what is the contract", "what spec", "find the spec",
}

_MUTATION_TERMS = {"fix", "change", "modify", "implement", "patch", "update", "rewrite", "rename", "replace", "add", "remove", "create", "design", "introduce", "refactor"}

_DESIGN_TERMS = {"design", "proposal", "architecture", "new file", "non existing file", "non-existing file", "new component", "new surface", "greenfield", "support context"}

_DOC_TERMS = {"contract", "spec", "schema", "semantics", "precedence", "source of truth", "documentation", "readme", "invariant"}

_VERIFICATION_TERMS = {"verify", "verification", "regression", "test", "failing test", "proof", "assertion", "acceptance"}

_CONFIG_TERMS = {"config", "configuration", "yaml", "toml", "env var", "setting", "settings", "properties", "toggle", "flag"}

_GENERATED_NAME_PATTERNS = [
    re.compile(r"\b(report|summary|results?|trace|benchmark|scores?|delta_analysis|evaluation|snapshot)\b"),
    re.compile(r"\b(artifact|artifacts|generated|normalized)\b"),
    re.compile(r"\b(20\d{2}[._-]\d{2}[._-]\d{2})\b"),
]

_GENERATED_CONTENT_PATTERNS = [
    re.compile(r"\b(auto-?generated|generated on|generated at|benchmark result|pipeline progress report|delta analysis|normalized pipeline trace)\b", re.I),
    re.compile(r"\b(score|report|summary|artifact registry|job_state\.json|comparison_report)\b", re.I),
]

_DRAFT_BENCHMARK_PATH_MARKERS = (
    "infra/pipeline/knowledge_drafts/",
    "tests/benchmarks/pipeline/",
    "/benchmark-gpt/",
    "/pis_datasets/",
    "/_results/",
)


def _is_draft_benchmark_surface(rel: str) -> bool:
    rel_lower = str(rel or "").replace("\\", "/").lower()
    return any(marker in rel_lower for marker in _DRAFT_BENCHMARK_PATH_MARKERS)


def _normalized_path_hint(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _is_test_path_hint(value: Any) -> bool:
    rel = _normalized_path_hint(value)
    if not rel:
        return False
    if Path(rel).name.startswith("test_"):
        return True
    return rel.startswith("tests/") or "/tests/" in f"/{rel}"


def _text_blob(job_contract: Mapping[str, Any]) -> str:
    issue_context = dict(job_contract.get("issue_context") or {})
    return " ".join(
        str(x or "")
        for x in [
            job_contract.get("title"),
            job_contract.get("objective"),
            job_contract.get("description"),
            issue_context.get("user_notes"),
            " ".join(str(v) for v in list(job_contract.get("constraints") or [])),
        ]
    ).lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def infer_task_archetype(
    job_contract: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    intake_assessment: Mapping[str, Any] | None = None,
) -> str:
    text = _text_blob(job_contract)
    primary_task_type = str(task_profile.get("primary_task_type") or "").strip().lower()
    legacy_task_mode = str(task_profile.get("task_mode") or "").strip().lower()
    explicit_file_hints = [m.group(0) for m in PATHLIKE_RE.finditer(text)] + [m.group(0) for m in FILELIKE_RE.finditer(text)]

    def _normalized_hint(raw: Any) -> str:
        return str(raw or "").replace("\\", "/").strip().lower()

    has_explicit_non_test_file_hint = any(
        not (
            Path(_normalized_hint(raw)).name.startswith("test_")
            or _normalized_hint(raw).startswith("tests/")
            or "/tests/" in f"/{_normalized_hint(raw)}"
        )
        for raw in explicit_file_hints
    )
    if _contains_any(text, _ASSESSMENT_TERMS):
        return "repo_assessment"

    if _contains_any(text, _DESIGN_TERMS) or legacy_task_mode == "design" or primary_task_type == "design":
        return "design_new_surface"

    lookup_signal = _contains_any(text, _LOOKUP_TERMS)
    mutation_signal = _contains_any(text, _MUTATION_TERMS)
    if lookup_signal and not mutation_signal:
        if _contains_any(text, _DOC_TERMS):
            return "documentation_contract"
        return "information_retrieval"

    if primary_task_type in {"truth_contract", "orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract"}:
        return "implementation_change"

    if primary_task_type == "config" or _contains_any(text, _CONFIG_TERMS):
        return "configuration_change"

    if primary_task_type == "test_fix" or (
        _contains_any(text, _VERIFICATION_TERMS)
        and not has_explicit_non_test_file_hint
        and not _contains_any(text, _DESIGN_TERMS)
        and primary_task_type not in {"truth_contract", "orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract"}
    ):
        return "verification_test"

    if legacy_task_mode == "planning_only":
        return "information_retrieval"

    return "implementation_change"


def derive_planning_profiles(task_archetype: str) -> Dict[str, Any]:
    profile = dict(PROFILE_REGISTRY.get(task_archetype) or PROFILE_REGISTRY["implementation_change"])
    return {
        "task_archetype": task_archetype,
        "retrieval_profile": profile["retrieval_profile"],
        "ranking_profile": profile["ranking_profile"],
        "bundle_profile": profile["bundle_profile"],
        "output_profile": profile["output_profile"],
        "output_contract_mode": profile["output_contract_mode"],
        "bundle_requirements": dict(profile.get("bundle_requirements") or {}),
        "role_weights": {k: dict(v) for k, v in dict(profile.get("role_weights") or {}).items()},
    }


def classify_file_role(path: str, text_sample: str = "") -> str:
    rel = str(path or "").replace("\\", "/").strip().lower()
    basename = Path(rel).name.lower()
    suffix = Path(rel).suffix.lower()
    sample = str(text_sample or "")[:4000].lower()

    if not rel:
        return "unknown"

    if suffix in LOG_EXTENSIONS or any(token in basename for token in ("log", "trace", "transcript")):
        return "log_trace"

    if suffix in DATA_EXTENSIONS or any(token in basename for token in ("fixture", "fixtures", "golden", "dataset", "sample")):
        return "data_fixture"

    if rel.startswith(("tmp/", "runtime/", "jobs/")):
        return "generated_artifact"

    if _is_draft_benchmark_surface(rel):
        if suffix in DOC_EXTENSIONS or suffix in CONFIG_EXTENSIONS or suffix in DATA_EXTENSIONS:
            return "report_summary"
        return "generated_artifact"

    if any(part in {"artifacts", "reports", "runs", "snapshots", "benchmarks"} for part in Path(rel).parts):
        if suffix in DOC_EXTENSIONS or suffix in CONFIG_EXTENSIONS:
            return "report_summary"
        return "generated_artifact"

    if suffix in DOC_EXTENSIONS:
        if any(p.search(basename) for p in _GENERATED_NAME_PATTERNS) or any(p.search(sample) for p in _GENERATED_CONTENT_PATTERNS):
            return "report_summary"
        return "documentation"

    if suffix in CONFIG_EXTENSIONS:
        if any(token in basename for token in ("schema", "contract", "spec", "manifest")):
            return "schema_contract"
        return "config_build"

    if rel.startswith("tests/") or "/test_" in rel or basename.startswith("test_") or basename.endswith("_test.py"):
        return "test_code"

    if suffix in CODE_EXTENSIONS:
        return "implementation_code"

    return "unknown"


def file_role_weight(task_archetype: str, file_role: str, purpose: str) -> float:
    profile = PROFILE_REGISTRY.get(task_archetype) or PROFILE_REGISTRY["implementation_change"]
    purpose_map = dict(profile.get("role_weights") or {}).get(purpose) or {}
    return float(purpose_map.get(file_role, purpose_map.get("unknown", 0.5)))


def compatible_candidate_role(file_role: str) -> str:
    if file_role == "implementation_code":
        return "implementation_code"
    if file_role == "test_code":
        return "test_file"
    if file_role in {"config_build", "schema_contract"}:
        return "config_file"
    if file_role == "documentation":
        return "documentation"
    if file_role in {"generated_artifact", "log_trace", "report_summary"}:
        return "temporary_artifact"
    return "other"


def derive_bundle_role(
    task_archetype: str,
    *,
    file_role: str,
    selection_lane: str,
    edit_allowed: bool,
    lane_rank: int,
    max_primary_targets: int,
    ownership_role: str = "",
) -> Tuple[str, bool]:
    lane = str(selection_lane or "reference_context")
    owner = str(ownership_role or "")
    editable = bool(edit_allowed)

    if task_archetype == "repo_assessment":
        if file_role == "implementation_code":
            return "representative_code_surface", False
        if file_role == "test_code":
            return "representative_test_surface", False
        if file_role in {"config_build", "schema_contract"}:
            return "representative_config_surface", False
        return "representative_reference_surface", False

    if task_archetype == "information_retrieval":
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        return "read_only_reference", False

    if task_archetype == "documentation_contract":
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        if file_role == "implementation_code":
            return "integration_context", False
        return "read_only_reference", False

    if task_archetype == "design_new_surface":
        if editable and lane == "implementation_target" and lane_rank <= max_primary_targets and file_role in {"implementation_code", "config_build"}:
            return "integration_target", True
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        if file_role == "test_code":
            return "validation_context", False
        if file_role in {"implementation_code", "config_build"}:
            return "new_file_support_context" if owner != "core_owner" else "integration_context", False
        return "pattern_reference_context", False

    if task_archetype == "verification_test":
        if editable and file_role == "test_code":
            return "validation_target", True
        if editable and file_role in {"implementation_code", "config_build"} and lane == "implementation_target":
            return "edit_target", True
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        return "validation_context", False

    if task_archetype == "configuration_change":
        if editable and file_role in {"config_build", "schema_contract"} and lane == "implementation_target":
            return "config_target", True
        if editable and file_role == "implementation_code" and lane == "implementation_target" and lane_rank <= max_primary_targets:
            return "integration_target", True
        if file_role == "test_code":
            return "validation_context", False
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        return "integration_context", False

    # implementation_change default
    if not editable or lane != "implementation_target":
        if file_role in {"documentation", "schema_contract"}:
            return "interface_contract_context", False
        if file_role == "test_code":
            return "validation_context", False
        return "read_only_reference", False
    if file_role in {"config_build", "schema_contract"}:
        return "config_target", True
    if file_role == "test_code":
        return "validation_target", True
    if owner == "core_owner" or lane_rank <= max_primary_targets:
        return "edit_target", True
    return "integration_target", True


def evaluate_bundle_composition(task_archetype: str, files: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    role_counts: Dict[str, int] = {}
    file_role_counts: Dict[str, int] = {}
    editable_count = 0
    for entry in files:
        role = str(entry.get("bundle_role") or entry.get("role") or "")
        file_role = str(entry.get("file_role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        file_role_counts[file_role] = file_role_counts.get(file_role, 0) + 1
        if bool(entry.get("edit_allowed")):
            editable_count += 1

    profile = PROFILE_REGISTRY.get(task_archetype) or PROFILE_REGISTRY["implementation_change"]
    requirements = dict(profile.get("bundle_requirements") or {})
    weaknesses: List[str] = []
    for role, minimum in requirements.items():
        if int(role_counts.get(role, 0)) < int(minimum):
            weaknesses.append(f"Missing required bundle role: {role}")

    if task_archetype == "implementation_change":
        if editable_count <= 0:
            weaknesses.append("No editable implementation target in implementation bundle")
        if file_role_counts.get("implementation_code", 0) + file_role_counts.get("config_build", 0) <= 0:
            weaknesses.append("No implementation/config surface present for implementation task")
        ready = editable_count > 0 and not weaknesses
    elif task_archetype == "design_new_surface":
        ready = role_counts.get("new_file_support_context", 0) + role_counts.get("integration_context", 0) + role_counts.get("integration_target", 0) > 0 and role_counts.get("interface_contract_context", 0) > 0
        if not ready:
            weaknesses.append("Design bundle lacks both implementation support and interface/contract context")
    elif task_archetype == "repo_assessment":
        diversity = sum(1 for key in ("implementation_code", "test_code", "config_build", "documentation", "schema_contract") if file_role_counts.get(key, 0) > 0)
        ready = diversity >= 3
        if not ready:
            weaknesses.append("Assessment bundle lacks representative diversity across repo surfaces")
    elif task_archetype == "information_retrieval":
        ready = len(files) > 0
    elif task_archetype == "documentation_contract":
        ready = file_role_counts.get("documentation", 0) + file_role_counts.get("schema_contract", 0) > 0
        if not ready:
            weaknesses.append("Documentation/contract task lacks documentation/schema evidence")
    elif task_archetype == "verification_test":
        ready = file_role_counts.get("test_code", 0) > 0 and (role_counts.get("validation_context", 0) + role_counts.get("validation_target", 0) > 0)
        if not ready:
            weaknesses.append("Verification bundle lacks test validation surfaces")
    elif task_archetype == "configuration_change":
        ready = file_role_counts.get("config_build", 0) + file_role_counts.get("schema_contract", 0) > 0
        if not ready:
            weaknesses.append("Configuration task lacks config/schema surfaces")
    else:
        ready = len(files) > 0

    confidence = 0.86 if ready and not weaknesses else (0.64 if ready else 0.3)
    return {
        "role_counts": role_counts,
        "file_role_counts": file_role_counts,
        "bundle_ready_for_task": bool(ready),
        "bundle_weakness_reasons": weaknesses,
        "bundle_confidence": round(confidence, 3),
    }
