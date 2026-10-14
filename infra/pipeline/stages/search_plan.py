from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.decision_trace import build_search_plan_decision_trace
from infra.pipeline.core import (
    PipelineError,
    build_envelope,
    emit_pipeline_error,
    load_json,
    load_pipeline_config,
    save_json_atomic,
    utc_now_iso,
)
from infra.pipeline.knowledge_assets import load_knowledge_assets, resolve_knowledge_asset_paths
from infra.pipeline.planning import (
    detect_global_replace,
    detect_mailbox_writeback_seam_focus,
    detect_mailbox_writeback_seam_focus_from_text,
    detect_task_families,
)
from infra.pipeline.schemas import validate_stage_input
from infra.pipeline.v2_foundation import (
    confidence_payload,    ensure_intake_assessment,
    ensure_search_plan_foundation,
    ensure_task_profile_foundation,
)

STAGE_KEY = "script_3_search_plan"
LOCAL_KNOWLEDGE_REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_PLAN_ASSET_NAMES = [
    "failure_taxonomy",
    "invariant_templates",
    "derived_requirements",
    "cross_cutting_flags",
    "conceptual_query_templates",
    "subsystem_map",
    "negative_targets",
]

IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
QUOTE_RE = re.compile(r'"([^"\n]{2,})"|\'([^\'\n]{2,})\'')
PATHLIKE_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
FILELIKE_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8}\b")
NON_PATH_FILTER_RE = re.compile(
    r"^\s*(#+\s+|[-*]\s*(tool_name|category|tier|determinism|failure_semantics|cache_policy|truth_boundary|audit_artifact|inputs|outputs|guarantees|non_guarantees|security_or_safety_notes|test_plan|example_invocation|example_output_envelope)\s*:)",
    re.IGNORECASE,
)

LOW_SIGNAL_IDENTIFIERS = {
    "improve", "improvement", "want", "need", "make", "with", "without", "from",
    "into", "this", "that", "these", "those", "mode", "modes", "thing", "stuff",
    "help", "helper", "using", "used", "work", "works", "working", "change",
    "consider", "against", "especially", "more", "behavior", "human", "task", "runs",
    "each", "them", "case", "cases", "mitigate", "again", "really", "very", "just",
    "acceptance", "assistant", "boundary", "chapter", "concrete", "context", "core",
    "cross", "define", "existing", "goal", "instruction", "title", "scope", "parent",
    "safe", "safer", "safety", "robust", "robustness", "reliable", "reliability",
}

DOMAIN_BIASED_TERMS = {
    "scheduler", "scheduling", "recovery", "failure", "failures", "execution", "watchdog",
    "queue", "retry", "operator", "pipeline", "maintenance", "semantic", "embedding",
    "embeddings", "ollama", "projflow", "planning", "context", "bundle", "patch",
    "verification", "healer", "start", "schedule", "pause", "resume", "cancel",
    "ledger", "provenance", "freshness", "transcript", "memory", "writeback", "truth",
    "ownership", "claim", "checkpoint", "dispatch", "validation", "guard", "audit",
}

FAILURE_FRAGMENT_PATTERNS: List[Tuple[str, str]] = [
    (r"\bstale claim\b", "stale claim"),
    (r"\borphan(?:ed)? run\b", "orphan"),
    (r"\breconcil(?:e|ed|ing)\b", "reconcile"),
    (r"\breclaim(?:ed|ing)?\b", "reclaim"),
    (r"\brestart(?:ed|ing)?\b", "restart"),
    (r"\bresume(?:d|ing)?\b", "resume"),
    (r"\bpause(?:d|ing)?\b", "pause"),
    (r"\bcancel(?:led|ed|ing)?\b", "cancel"),
    (r"\bdispatch(?:ed|ing)?\b", "dispatch"),
    (r"\bduplicate(?:d|s)?\b", "duplicate"),
    (r"\bclaim(?:ed|ing)?\b", "claim"),
    (r"\bcheckpoint(?:s)?\b", "checkpoint"),
    (r"\bfenc(?:e|ed|ing)\b", "fencing"),
    (r"\btimeout(?:s)?\b", "timeout"),
    (r"\bheartbeat\b", "heartbeat"),
]

NARROW_TASK_NOUN_MARKERS = {
    "button", "buttons", "checkbox", "endpoint", "route", "field", "page", "panel", "tab", "width",
    "layout", "spacing", "validation", "rule", "dialog", "modal", "tooltip", "label", "column",
}
NARROW_TASK_VERB_MARKERS = {
    "fix", "resize", "resized", "adjust", "align", "toggle", "show", "hide", "limit", "clamp",
    "rename", "validate", "disable", "enable", "tighten", "patch",
}
NARROW_UI_MARKERS = {
    "mobile", "responsive", "width", "layout", "button", "buttons", "tab", "checkbox", "start button",
    "too wide", "resize", "resized", "page",
}
NARROW_UI_IDENTIFIER_SUPPRESSIONS = {
    "button", "buttons", "example", "have", "mobile", "page", "size", "start", "tab", "view", "wide",
}

LAB_ASSISTANT_FAMILIES = {"lab_assistant_memory"}
TRUTH_CONTRACT_FAMILIES = {"assistant_truth_and_identity"}
NON_PIPELINE_TASK_NEGATIVE_TARGETS = [
    "infra/pipeline/knowledge/",
    "infra/pipeline/knowledge/pipeline_knowledge.yaml",
]
REQUIRED_CONCEPT_AXES = ("recovery", "concurrency", "operator", "state")
TYPE_PRIORITY = {"filename": 3, "test_name": 2, "symbol": 1, "literal": 0}

AXIS_TO_SUBSYSTEMS = {
    "recovery": {"recovery_mechanisms", "checkpoint_resume", "heartbeat_liveness", "retry_backoff", "cleanup_retention"},
    "concurrency": {"lease_claims", "concurrency_control", "leadership_coordination", "idempotency_store", "queue_dispatch"},
    "operator": {"operator_entrypoints", "validation", "audit_receipts", "manual_backfill"},
    "state": {"state_persistence", "validation", "scheduler_core", "checkpoint_resume", "configuration_versioning"},
    "observability": {"observability", "audit_receipts"},
}
AXIS_TO_COVER_TAG = {
    "recovery": "recovery_failure_modes",
    "concurrency": "concurrency_failure_modes",
    "operator": "operator_failure_modes",
    "state": "state_consistency",
    "observability": "observability",
}
AXIS_FLAG_HINTS = {
    "recovery": {"recovery_integrity", "stale_state_handling", "claim_integrity", "reconciliation_correctness", "idempotency"},
    "concurrency": {"concurrency_control", "ownership_control", "claim_integrity", "duplicate_suppression", "idempotency"},
    "operator": {"operator_safety", "auditability", "validation_rigor", "input_sanitization"},
    "state": {"state_consistency", "mutation_boundary", "stale_state_handling", "validation_rigor", "fail_closed_behavior"},
    "observability": {"observability", "auditability"},
}

CONCEPTUAL_FLAG_MATCHES: Dict[str, Set[str]] = {
    "duplicate_run_prevention": {"duplicate_suppression", "idempotency", "concurrency_control"},
    "stale_claim_recovery": {"recovery_integrity", "claim_integrity", "stale_state_handling"},
    "manual_vs_automatic_conflict": {"operator_safety", "validation_rigor", "auditability"},
    "invalid_state_transition": {"operator_safety", "validation_rigor", "state_consistency"},
    "auditability_of_manual_actions": {"operator_safety", "auditability"},
    "checkpoint_resume_integrity": {"recovery_integrity", "stale_state_handling"},
    "cleanup_vs_active_state": {"recovery_integrity", "auditability"},
    "stale_snapshot_usage": {"stale_state_handling", "state_consistency"},
}
SYSTEM_FAILURE_FALLBACK_QUERIES: List[Tuple[str, Tuple[str, ...]]] = [
    ("partial write recovery", ("partial", "write", "commit", "receipt", "persist", "durable", "checkpoint", "rollback", "writeback")),
    ("durable commit guarantee", ("durable", "commit", "receipt", "persist", "ack", "writeback")),
    ("resource exhaustion protection", ("resource", "exhaust", "capacity", "quota", "memory", "cpu", "slot", "pool", "no_space", "leak")),
    ("corruption detection and repair", ("corrupt", "corruption", "checksum", "digest", "integrity", "bitrot", "invalid_format")),
    ("crash consistency handling", tuple()),
]
SYSTEM_FAILURE_TEMPLATE_KEYS = {
    "invalid_state_transition",
    "missing_durable_receipt",
    "configuration_drift_protection",
    "event_ordering_corruption",
    "queue_ack_before_commit",
    "stale_snapshot_usage",
}
SYSTEM_FAILURE_PREFERRED_SUBSYSTEMS = ["state_persistence", "recovery_mechanisms", "validation", "operator_entrypoints"]
GENERIC_TEMPLATE_KEYS = {
    "invalid_state_transition",
    "stale_snapshot_usage",
    "cleanup_vs_active_state",
    "auditability_of_manual_actions",
}
GENERIC_FRAGMENT_TOKENS = {
    "accepted", "affected", "after", "before", "current", "derived", "during", "logical",
    "output", "record", "result", "visible", "side", "there", "their", "where", "while",
}
PREFERRED_RECOVERY_FAILURE_TERMS = {"reconcile", "reclaim", "orphan", "stale claim", "checkpoint", "resume"}
SYSTEM_FAILURE_SUBTYPE_RULES: Dict[str, Dict[str, Any]] = {
    "crash_failure": {
        "signals": (
            "crash", "restart", "resume", "shutdown", "halt", "kill", "reboot", "unclean shutdown",
            "crash consistency", "recovery marker",
        ),
        "fallback_query": "crash consistency handling",
    },
    "persistence_failure": {
        "signals": (
            "partial write", "durable", "commit", "receipt", "persist", "writeback", "checkpoint",
            "rollback", "ack before commit", "durable commit",
        ),
        "fallback_query": "partial write recovery",
    },
    "resource_failure": {
        "signals": (
            "resource", "exhaust", "capacity", "quota", "memory", "cpu", "slot", "pool", "no_space",
            "leak", "full",
        ),
        "fallback_query": "resource exhaustion protection",
    },
    "corruption_failure": {
        "signals": (
            "corruption", "corrupt", "bitrot", "checksum", "digest", "integrity", "invalid format",
            "invalid_format", "drift", "broken write",
        ),
        "fallback_query": "corruption detection and repair",
    },
}
SYSTEM_FAILURE_TEMPLATE_SUBTYPE_MAP: Dict[str, Tuple[str, ...]] = {
    "crash_consistency_handling": ("crash_failure", "persistence_failure"),
    "durable_commit_guarantee": ("persistence_failure",),
    "resource_exhaustion_protection": ("resource_failure",),
    "corruption_detection_and_repair": ("corruption_failure", "persistence_failure"),
    "missing_durable_receipt": ("persistence_failure",),
    "queue_ack_before_commit": ("persistence_failure",),
    "checkpoint_resume_integrity": ("crash_failure", "persistence_failure"),
    "event_ordering_corruption": ("corruption_failure",),
    "stale_snapshot_usage": ("corruption_failure",),
}
SYSTEM_FAILURE_TEMPLATE_KEYS = {
    "crash_consistency_handling",
    "durable_commit_guarantee",
    "resource_exhaustion_protection",
    "corruption_detection_and_repair",
}
LEXICAL_SOURCE_STRENGTHS = {
    "replace_spec": 4.8,
    "explicit_path_hint": 4.6,
    "stacktrace_symbol": 4.4,
    "failing_test": 4.0,
    "subsystem_map.filename": 3.8,
    "subsystem_map.alias": 3.1,
    "failure_taxonomy": 3.5,
    "conceptual_query_templates": 3.2,
    "fallback_subsystem_alias": 2.8,
    "domain_identifier": 2.4,
    "symbol_identifier": 2.5,
    "prompt_literal": 2.4,
    "prompt_token": 1.3,
    "literal_identifier": 1.1,
    "conceptual_query_templates.fallback": 2.2,
}
NARROW_LEXICAL_QUERY_TARGET = 26
DEFAULT_LEXICAL_QUERY_TARGET = 32
FALLBACK_LEXICAL_QUERY_TARGET = 34


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
    return out


def _sanitize_query_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if any(ch in text for ch in ("\n", "\r", "\t")):
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text.startswith(("- ", "* ", "#")):
        return ""
    if "`" in text and ("[" in text or "]" in text):
        return ""
    return text


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_match_key(value: Any) -> str:
    return re.sub(r"[_\-]+", " ", _normalize_text(value))


def _contains_phrase(text: Any, phrase: Any) -> bool:
    haystack = _normalize_match_key(text)
    needle = _normalize_match_key(phrase)
    if not haystack or not needle:
        return False
    if " " in needle:
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _resolve_assets_repo_root(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Path:
    candidates: List[Path] = []
    knowledge_cfg = _coerce_mapping((config or {}).get("knowledge_assets"))
    explicit_knowledge_path = bool(knowledge_cfg)
    derived = _coerce_mapping((config or {}).get("_derived"))
    derived_repo_root = str(derived.get("repo_root") or "").strip()
    if derived_repo_root:
        candidates.append(Path(derived_repo_root).resolve())
    if repo_root:
        candidates.append(Path(repo_root).resolve())
    candidates.append(LOCAL_KNOWLEDGE_REPO_ROOT.resolve())

    seen: Set[str] = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if explicit_knowledge_path:
            return candidate
        manifest = resolve_knowledge_asset_paths(candidate, config or {})
        exists = _coerce_mapping(manifest.get("exists"))
        if all(bool(exists.get(name)) for name in SEARCH_PLAN_ASSET_NAMES):
            return candidate
    return LOCAL_KNOWLEDGE_REPO_ROOT.resolve()


def _load_search_assets(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    assets_repo_root = _resolve_assets_repo_root(repo_root, config)
    return load_knowledge_assets(assets_repo_root, config or {}, SEARCH_PLAN_ASSET_NAMES)


def _normalized_family_map(asset: Mapping[str, Any]) -> Dict[str, Any]:
    families = asset.get("families")
    if isinstance(families, Mapping):
        return dict(families)
    legacy = asset.get("task_families")
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return {}


def _ordered_family_list(families: Sequence[str]) -> List[str]:
    ordered = _dedupe_strings(families)
    family_set = set(ordered)
    if "scheduler_safety" in family_set and "scheduler_hardening" not in family_set:
        ordered.append("scheduler_hardening")
    if "scheduler_hardening" in family_set and "scheduler_safety" not in family_set:
        ordered.append("scheduler_safety")
    if "reconciliation_recovery" in family_set and "workflow_recovery" not in family_set:
        ordered.append("workflow_recovery")
    if "workflow_recovery" in family_set and "reconciliation_recovery" not in family_set:
        ordered.append("reconciliation_recovery")
    return _dedupe_strings(ordered)


def _family_alias_set(families: Sequence[str]) -> Set[str]:
    return set(_ordered_family_list(families))


def _is_scheduler_family(families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    return bool(family_set.intersection({"scheduler_safety", "scheduler_hardening", "workflow_recovery", "reconciliation_recovery"}))


def _is_lab_assistant_memory_family(families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    return bool(family_set.intersection(LAB_ASSISTANT_FAMILIES))


def _is_truth_contract_family(families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    return bool(family_set.intersection(TRUTH_CONTRACT_FAMILIES))


def _is_retrieval_ranking_family(families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    return "retrieval_ranking_contract" in family_set


def _task_text_blob(job_contract: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(job_contract.get("title") or ""),
            str(job_contract.get("description") or ""),
            str(job_contract.get("objective") or ""),
            str(_coerce_mapping(job_contract.get("issue_context")).get("user_notes") or ""),
        ]
    ).lower()


def _looks_narrow_task(job_contract: Mapping[str, Any], task_profile: Mapping[str, Any], families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection({"scheduler_hardening", "scheduler_safety", "workflow_recovery", "reconciliation_recovery", "tool_spec_implementation", "documentation_contracts", "deterministic_patch_helper"}):
        return False
    if _is_lab_assistant_memory_family(families) or _is_truth_contract_family(families):
        return False
    expected_scope = str(task_profile.get("expected_edit_scope") or "").strip().lower()
    if expected_scope == "single_file":
        return True
    changed_files = [str(item or "").strip() for item in list(_coerce_mapping(job_contract.get("issue_context")).get("changed_files_hint") or []) if str(item or "").strip()]
    text = _task_text_blob(job_contract)
    tokens = {token.lower() for token in IDENT_RE.findall(text)}
    noun_hits = {token for token in tokens if token in NARROW_TASK_NOUN_MARKERS}
    verb_hits = {token for token in tokens if token in NARROW_TASK_VERB_MARKERS}
    explicit_narrow_phrase = any(marker in text for marker in ["mobile view", "too wide", "start button", "validation rule", "one endpoint", "one checkbox", "one page", "one panel"])
    broad_system_terms = {"architecture", "system", "pipeline", "orchestration", "scheduler", "workflow", "taxonomy", "refactor", "program"}
    broad_system_hits = tokens.intersection(broad_system_terms)
    narrow_shape = bool(explicit_narrow_phrase or (noun_hits and verb_hits) or (len(changed_files) <= 1 and (noun_hits or verb_hits)))
    if len(broad_system_hits) >= 2:
        return False
    if broad_system_hits and not (narrow_shape and len(changed_files) <= 1):
        return False
    return narrow_shape


def _is_narrow_ui_task(job_contract: Mapping[str, Any], task_profile: Mapping[str, Any], families: Sequence[str]) -> bool:
    if not _looks_narrow_task(job_contract, task_profile, families):
        return False
    text = _task_text_blob(job_contract)
    return any(marker in text for marker in NARROW_UI_MARKERS)


def _looks_low_signal_identifier(token: str) -> bool:
    raw = str(token or "").strip()
    value = raw.lower()
    if not value:
        return True
    if value in LOW_SIGNAL_IDENTIFIERS:
        return True
    if len(value) <= 3:
        return True
    if raw.isalpha() and raw[:1].isupper() and raw[1:].islower() and value not in DOMAIN_BIASED_TERMS:
        return True
    if value.islower() and not re.search(r"[aeiou]", value):
        return True
    return False


def _looks_symbol_like_identifier(token: str) -> bool:
    value = str(token or "").strip()
    if not value or _looks_low_signal_identifier(value):
        return False
    if any(ch.isdigit() for ch in value):
        return True
    if "_" in value or "-" in value:
        return True
    if any(ch.isupper() for ch in value[1:]):
        return True
    return value.lower() in DOMAIN_BIASED_TERMS


def _sanitize_path_filters(values: Sequence[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if NON_PATH_FILTER_RE.search(text):
            continue
        if len(text) > 220 or text.count(" ") > 2:
            continue
        if "/" not in text and "." not in Path(text).name:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
    return out


def _extract_terms(job_contract: Mapping[str, Any]) -> Dict[str, List[str]]:
    issue_context = _coerce_mapping(job_contract.get("issue_context"))
    text = " ".join(
        [
            str(job_contract.get("title") or ""),
            str(job_contract.get("description") or ""),
            str(job_contract.get("objective") or ""),
            str(issue_context.get("stack_trace") or ""),
            " ".join([str(x) for x in issue_context.get("failing_tests", [])]),
            " ".join([str(x) for x in issue_context.get("changed_files_hint", [])]),
            str(issue_context.get("user_notes") or ""),
        ]
    )
    quoted: List[str] = []
    for a, b in QUOTE_RE.findall(text):
        literal = _sanitize_query_text(a or b)
        if len(literal) < 4 or literal.count(" ") > 8:
            continue
        quoted.append(literal)
    identifiers: List[str] = []
    for match in IDENT_RE.finditer(text):
        token = match.group(0)
        if len(token) <= 3 or _looks_low_signal_identifier(token):
            continue
        if token not in identifiers:
            identifiers.append(token)
    file_hints = [str(x) for x in issue_context.get("changed_files_hint", []) if str(x).strip()]
    file_hints += [m.group(0) for m in PATHLIKE_RE.finditer(text) if "/" in m.group(0) and not m.group(0).startswith("pause/")][:10]
    file_hints += [m.group(0) for m in FILELIKE_RE.finditer(text) if "." in m.group(0) and "/" not in m.group(0)][:10]
    test_hints = [str(x) for x in issue_context.get("failing_tests", []) if str(x).strip()]
    stacktrace = str(issue_context.get("stack_trace") or "")
    stacktrace_symbols = []
    for part in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", stacktrace):
        if len(part) <= 3 or part in stacktrace_symbols:
            continue
        stacktrace_symbols.append(part)
    prompt_tokens: List[str] = []
    for match in IDENT_RE.finditer(_task_text_blob(job_contract)):
        token = match.group(0).lower()
        if _looks_low_signal_identifier(token):
            continue
        if token not in prompt_tokens:
            prompt_tokens.append(token)
    return {
        "quoted_literals": quoted[:6],
        "identifiers": identifiers[:24],
        "file_hints": _dedupe_strings(file_hints)[:12],
        "test_hints": _dedupe_strings(test_hints)[:8],
        "stacktrace_symbols": stacktrace_symbols[:20],
        "prompt_tokens": prompt_tokens[:12],
    }


def _merge_list_sections(base_values: Sequence[Any], asset_values: Sequence[Any]) -> List[str]:
    return _dedupe_strings(list(base_values or []) + list(asset_values or []))


def _select_asset_families(
    asset: Mapping[str, Any],
    families: Sequence[str],
    *,
    task_profile: Mapping[str, Any],
    narrow_task: bool,
) -> List[str]:
    family_map = _normalized_family_map(asset)
    if not family_map:
        return []
    active = [family for family in _family_alias_set(families) if family in family_map]
    if active:
        if "generic" in family_map and "generic" in _family_alias_set(families):
            active.append("generic")
        return _dedupe_strings(active)
    ambiguity_raw = task_profile.get("ambiguity_score")
    classifier_confidence_raw = task_profile.get("classifier_confidence")
    ambiguity = float(ambiguity_raw) if ambiguity_raw is not None else 0.0
    classifier_confidence = float(classifier_confidence_raw) if classifier_confidence_raw is not None else None
    if not narrow_task and (
        "generic" in _family_alias_set(families)
        or ambiguity >= 0.6
        or (classifier_confidence is not None and classifier_confidence <= 0.4)
    ):
        if "generic" in family_map:
            return ["generic"]
    return []


def _merge_failure_taxonomy_from_asset(asset: Mapping[str, Any], families: Sequence[str]) -> Dict[str, List[str]]:
    out = {
        "operator_failure_modes": [],
        "system_failure_modes": [],
        "concurrency_failure_modes": [],
        "recovery_failure_modes": [],
    }
    family_map = _normalized_family_map(asset)
    for family in families:
        entry = _coerce_mapping(family_map.get(family))
        for bucket in out:
            out[bucket].extend(_coerce_list(entry.get(bucket)))
    return {bucket: _dedupe_strings(values) for bucket, values in out.items()}


def _merge_invariants_from_asset(asset: Mapping[str, Any], families: Sequence[str]) -> List[str]:
    out: List[str] = []
    family_map = _normalized_family_map(asset)
    for family in families:
        entry = family_map.get(family)
        if isinstance(entry, list):
            out.extend(entry)
        else:
            out.extend(_coerce_list(_coerce_mapping(entry).get("invariants")))
    return _dedupe_strings(out)


def _merge_requirements_from_asset(asset: Mapping[str, Any], families: Sequence[str]) -> Dict[str, List[str]]:
    out = {
        "safety_requirements": [],
        "observability_requirements": [],
        "recovery_guarantees": [],
    }
    family_map = _normalized_family_map(asset)
    for family in families:
        entry = _coerce_mapping(family_map.get(family))
        for key in out:
            out[key].extend(_coerce_list(entry.get(key)))
    return {key: _dedupe_strings(values) for key, values in out.items()}


def _merge_stage1_context(
    intake_assessment: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    families: Sequence[str],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    narrow_task: bool,
) -> Tuple[Dict[str, Any], List[str]]:
    base = ensure_intake_assessment(intake_assessment)
    notes: List[str] = []
    asset_families = _select_asset_families(assets.get("failure_taxonomy") or {}, families, task_profile=task_profile, narrow_task=narrow_task)
    if asset_families:
        notes.append("asset_fallback_families=" + ",".join(asset_families))
    asset_taxonomy = _merge_failure_taxonomy_from_asset(assets.get("failure_taxonomy") or {}, asset_families)
    asset_invariants = _merge_invariants_from_asset(assets.get("invariant_templates") or {}, asset_families)
    asset_requirements = _merge_requirements_from_asset(assets.get("derived_requirements") or {}, asset_families)

    merged = ensure_intake_assessment(base)
    base_taxonomy = _coerce_mapping(base.get("failure_taxonomy"))
    merged_taxonomy: Dict[str, List[str]] = {}
    for bucket in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]:
        merged_bucket = _merge_list_sections(base_taxonomy.get(bucket) or [], asset_taxonomy.get(bucket) or [])
        merged_taxonomy[bucket] = merged_bucket
        if merged_bucket and not _coerce_list(base_taxonomy.get(bucket)):
            notes.append(f"asset_backfilled={bucket}")
    merged["failure_taxonomy"] = merged_taxonomy

    merged_invariants = _merge_list_sections(base.get("invariants") or [], asset_invariants)
    if merged_invariants and not _coerce_list(base.get("invariants")):
        notes.append("asset_backfilled=invariants")
    merged["invariants"] = merged_invariants

    base_requirements = _coerce_mapping(base.get("derived_requirements"))
    merged_requirements = {
        key: _merge_list_sections(base_requirements.get(key) or [], asset_requirements.get(key) or [])
        for key in ["safety_requirements", "observability_requirements", "recovery_guarantees"]
    }
    if any(merged_requirements.values()) and not any(_coerce_list(base_requirements.get(key)) for key in merged_requirements):
        notes.append("asset_backfilled=derived_requirements")
    merged["derived_requirements"] = merged_requirements
    return merged, _dedupe_strings(notes)


def _needs_fallback_widening(
    task_profile: Mapping[str, Any],
    families: Sequence[str],
    stage1_notes: Sequence[str],
) -> bool:
    family_set = set(_dedupe_strings(families))
    ambiguity_raw = task_profile.get("ambiguity_score")
    classifier_confidence_raw = task_profile.get("classifier_confidence")
    ambiguity = float(ambiguity_raw) if ambiguity_raw is not None else 0.0
    classifier_confidence = float(classifier_confidence_raw) if classifier_confidence_raw is not None else None
    if family_set == {"generic"}:
        return True
    if ambiguity >= 0.6 or (classifier_confidence is not None and classifier_confidence <= 0.4):
        return True
    return any(str(note or "").startswith("asset_fallback_families=generic") for note in stage1_notes)


def _active_flag_entries(asset: Mapping[str, Any], active_flags: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    for raw in _coerce_list(asset.get("flags")):
        item = _coerce_mapping(raw)
        key = str(item.get("key") or "").strip()
        if key:
            entries[key] = item
    return {flag: entries[flag] for flag in _dedupe_strings(active_flags) if flag in entries}


def _context_texts(job_contract: Mapping[str, Any], intake: Mapping[str, Any], flag_entries: Mapping[str, Mapping[str, Any]]) -> Dict[str, List[str]]:
    prompt_texts = [
        str(job_contract.get("title") or ""),
        str(job_contract.get("description") or ""),
        str(job_contract.get("objective") or ""),
        str(_coerce_mapping(job_contract.get("issue_context")).get("user_notes") or ""),
    ]
    failure_taxonomy = _coerce_mapping(intake.get("failure_taxonomy"))
    failure_groups = {
        bucket: _dedupe_strings(failure_taxonomy.get(bucket) or [])
        for bucket in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]
    }
    requirements = _coerce_mapping(intake.get("derived_requirements"))
    requirement_texts = {
        key: _dedupe_strings(requirements.get(key) or [])
        for key in ["safety_requirements", "observability_requirements", "recovery_guarantees"]
    }
    flag_clues: List[str] = []
    for item in flag_entries.values():
        flag_clues.extend(_coerce_list(item.get("clue_terms")))
    return {
        "prompt": _dedupe_strings(prompt_texts),
        "invariants": _dedupe_strings(intake.get("invariants") or []),
        "operator_failure_modes": failure_groups["operator_failure_modes"],
        "system_failure_modes": failure_groups["system_failure_modes"],
        "concurrency_failure_modes": failure_groups["concurrency_failure_modes"],
        "recovery_failure_modes": failure_groups["recovery_failure_modes"],
        "safety_requirements": requirement_texts["safety_requirements"],
        "observability_requirements": requirement_texts["observability_requirements"],
        "recovery_guarantees": requirement_texts["recovery_guarantees"],
        "flag_clues": _dedupe_strings(flag_clues),
    }


def _context_blob(texts: Mapping[str, Sequence[str]]) -> str:
    ordered: List[str] = []
    for key in [
        "prompt",
        "operator_failure_modes",
        "system_failure_modes",
        "concurrency_failure_modes",
        "recovery_failure_modes",
        "invariants",
        "safety_requirements",
        "observability_requirements",
        "recovery_guarantees",
        "flag_clues",
    ]:
        ordered.extend(list(texts.get(key) or []))
    return " ".join(str(item) for item in ordered if str(item).strip())


def _subsystem_entries(subsystem_asset: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [_coerce_mapping(raw) for raw in _coerce_list(subsystem_asset.get("subsystems")) if _coerce_mapping(raw).get("key")]


def _entry_terms(entry: Mapping[str, Any]) -> List[str]:
    return _dedupe_strings(
        [entry.get("key")] + list(entry.get("aliases") or []) + list(entry.get("filename_aliases") or []) + list(entry.get("doc_aliases") or [])
    )


def _match_subsystems_from_bias(include_values: Sequence[str], subsystem_asset: Mapping[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    normalized_values = [_normalize_match_key(value) for value in include_values if str(value or "").strip()]
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        terms = {_normalize_match_key(term) for term in _entry_terms(entry)}
        matched = [value for value in normalized_values if value in terms]
        if matched:
            scores[key] = max(scores.get(key, 0.0), 2.8 + 0.15 * min(4, len(matched)))
    return scores


def _semantic_subsystem_boosts(prompt_blob: str, context_blob: str, families: Sequence[str], *, title_blob: str = "") -> Dict[str, Tuple[float, str]]:
    text = _normalize_match_key(" ".join([prompt_blob, context_blob]))
    prompt_text = _normalize_match_key(title_blob or prompt_blob)
    boosts: Dict[str, Tuple[float, str]] = {}

    def add(subsystem: str, boost: float, reason: str) -> None:
        existing = boosts.get(subsystem)
        if existing is None or boost > existing[0]:
            boosts[subsystem] = (boost, reason)

    memory_like = _is_lab_assistant_memory_family(families) or _is_truth_contract_family(families)
    chat_persistence_like = any(
        phrase in text
        for phrase in [
            "chat persistence",
            "direct chat",
            "session snapshot",
            "hydrate",
            "hydration",
            "turn id",
            "workflow bubbles",
            "reply",
            "transcript archaeology",
            "turn history",
        ]
    )
    bootstrap_reconstruction_like = "bootstrap" in text and any(
        phrase in text
        for phrase in [
            "task register",
            "reconstruct",
            "reconstruction",
            "degraded bootstrap",
            "fresh turn",
            "reload assembly",
        ]
    )
    detached_job_lifecycle_like = any(
        phrase in text
        for phrase in [
            "heartbeat",
            "timeout",
            "timed out",
            "long running job",
            "long-running job",
            "detached job",
            "worker health",
            "stale heartbeat",
            "cancellation",
            "cancelled",
            "interrupted",
        ]
    )
    corruption_recovery_like = any(phrase in text for phrase in ["corruption", "corrupt", "checksum", "integrity"]) and any(
        phrase in text for phrase in ["restart", "persist", "state", "recovery"]
    )
    exact_scheduled_scenario_like = any(
        phrase in prompt_text
        for phrase in [
            "overnight",
            "morning result",
            "morning summary",
            "scheduled overnight",
            "unattended operation",
            "deferred background chain",
            "schedule deferred",
        ]
    )
    exact_cold_start_takeover_like = any(
        phrase in prompt_text
        for phrase in [
            "cold start",
            "cold-start",
            "replacement backend",
            "takeover compatibility",
            "replacement startup",
        ]
    )
    exact_authority_governance_like = any(
        phrase in prompt_text
        for phrase in [
            "high-impact approval",
            "cross-tenant",
            "approval provenance",
            "authority tier",
            "tenancy boundary",
            "hidden override",
        ]
    )
    exact_intake_decomposition_like = any(
        phrase in prompt_text
        for phrase in [
            "task intake",
            "work decomposition",
            "acceptance target",
            "missing information",
            "scope negotiation",
            "precedent lookup",
        ]
    )
    exact_transcript_idempotency_like = "idempotency" in prompt_text and any(
        phrase in prompt_text for phrase in ["transcript", "turn to session", "turn-to-session", "replay guard", "duplicate replay"]
    )
    exact_local_tooling_like = any(
        phrase in prompt_text
        for phrase in [
            "deterministic local tooling",
            "token-efficiency fabric",
            "token efficiency fabric",
            "tool contract",
            "category model",
            "local tooling layer",
        ]
    )
    exact_verifier_audit_like = any(
        phrase in prompt_text
        for phrase in [
            "quality audit",
            "verifier strictness",
            "strictness recalibration",
            "one-iteration pis",
            "one iteration pis",
            "strict verifier",
        ]
    )
    exact_ui_projection_like = any(
        phrase in prompt_text
        for phrase in [
            "ui projection",
            "operator-visible state",
            "operator visible state",
            "degraded state",
            "unreachable state",
            "staleness",
            "stale projection",
        ]
    )
    exact_cli_operator_experience_like = any(
        phrase in prompt_text
        for phrase in [
            "cli operator",
            "operator experience",
            "codex-like cli",
            "command-line operator",
            "terminal operator",
        ]
    )
    scheduled_flow_like = any(
        phrase in text
        for phrase in [
            "scheduled",
            "overnight",
            "morning result",
            "morning summary",
            "deferred",
            "background chain",
            "scheduler",
            "schedule deferred",
            "next run",
            "unattended",
        ]
    )
    mailbox_writeback_seam_like = detect_mailbox_writeback_seam_focus_from_text(text, title_text=title_blob or prompt_blob)
    if memory_like and any(phrase in text for phrase in ["task register", "open loop", "open-loop", "decomposition", "clarification", "bootstrap"]):
        add("task_register", 3.0, "semantic_task_register")
    if memory_like and any(phrase in text for phrase in ["transcript", "chat", "turn", "session", "reattach", "truncated stream"]):        add("turn_transcripts", 4.0, "semantic_turn_transcript")
    if memory_like and any(phrase in text for phrase in ["contradiction", "correction", "reconcile", "reconciliation", "conflict"]):
        add("execution_reconciliation", 3.2, "semantic_reconciliation")
    if memory_like and any(phrase in text for phrase in ["persist", "persistence", "restart", "disconnect", "recovery", "resume"]):
        add("state_persistence", 1.8, "semantic_persistence_recovery")
        add("checkpoint_resume", 1.8, "semantic_checkpoint_resume")
    if memory_like and any(phrase in text for phrase in ["authority", "approval", "tenancy", "operator", "admin"]):
        add("truth_ownership", 2.2, "semantic_operator_authority")
        add("operator_entrypoints", 2.0, "semantic_operator_authority")
        add("ledger_evidence", 1.8, "semantic_operator_authority")
    if memory_like and chat_persistence_like:
        add("turn_transcripts", 4.4, "semantic_chat_persistence")
        add("state_persistence", 2.6, "semantic_chat_persistence")
        add("checkpoint_resume", 3.2, "semantic_chat_persistence")
        add("recovery_mechanisms", 1.8, "semantic_chat_persistence")
    if memory_like and bootstrap_reconstruction_like:
        add("task_register", 4.0, "semantic_bootstrap_reconstruction")
        add("checkpoint_resume", 2.6, "semantic_bootstrap_reconstruction")
        add("recovery_mechanisms", 3.0, "semantic_bootstrap_reconstruction")
        add("state_persistence", 1.6, "semantic_bootstrap_reconstruction")
        add("truth_ownership", 1.4, "semantic_bootstrap_reconstruction")

    if any(phrase in text for phrase in ["multi assistant", "multi-assistant", "delegation", "supervisory", "coordination", "workcell"]):
        add("agent_orchestration", 3.0, "semantic_agent_orchestration")
        add("task_register", 1.4, "semantic_agent_orchestration")
        add("truth_ownership", 1.2, "semantic_agent_orchestration")

    if any(phrase in text for phrase in ["preflight", "capability", "dependency", "prerequisite", "eligibility", "endpoint registry", "capability registry"]):
        add("dependency_gating", 3.0, "semantic_dependency_gating")

    if "ui_truth_projection" in _family_alias_set(families) and any(phrase in text for phrase in ["degraded", "unreachable", "stale", "blocked", "status surface"]):
        add("operator_visible_state", 2.4, "semantic_operator_visible_state")
    if detached_job_lifecycle_like:
        add("heartbeat_liveness", 3.2, "semantic_detached_job_lifecycle")
        add("worker_runtime", 2.6, "semantic_detached_job_lifecycle")
        add("recovery_mechanisms", 2.0, "semantic_detached_job_lifecycle")
        add("lease_claims", 1.8, "semantic_detached_job_lifecycle")
        add("checkpoint_resume", 1.6, "semantic_detached_job_lifecycle")
    if mailbox_writeback_seam_like:
        add("writeback_projection", 4.0, "semantic_mailbox_writeback_seam")
        add("lab_assistant_delegation", 3.8, "semantic_mailbox_writeback_seam")
        add("worker_runtime", 3.2, "semantic_mailbox_writeback_seam")
        add("queue_dispatch", 3.0, "semantic_mailbox_writeback_seam")
        add("lease_claims", 2.8, "semantic_mailbox_writeback_seam")
        add("audit_receipts", 1.8, "semantic_mailbox_writeback_seam")
        add("validation", 1.6, "semantic_mailbox_writeback_seam")
    scheduler_resilience_like = _is_scheduler_family(families) and any(
        phrase in text
        for phrase in [            "failure",
            "failures",
            "human failure",
            "operator",
            "recovery",
            "watchdog",
            "healer",
            "robust",
            "robustness",
            "safe",
            "safer",
            "safety",
        ]
    )
    if scheduler_resilience_like:
        add("recovery_mechanisms", 2.6, "semantic_scheduler_resilience")
        add("operator_entrypoints", 1.4, "semantic_scheduler_resilience")
        add("audit_receipts", 1.0, "semantic_scheduler_resilience")
    if scheduled_flow_like:
        add("scheduler_core", 3.2, "semantic_scheduled_flow")
        add("schedule_calculation", 2.6, "semantic_scheduled_flow")
        add("worker_runtime", 1.8, "semantic_scheduled_flow")
        add("lease_claims", 1.4, "semantic_scheduled_flow")
        add("state_persistence", 1.2, "semantic_scheduled_flow")
    if exact_scheduled_scenario_like:
        add("scheduler_core", 3.8, "semantic_exact_scheduled_scenario")
        add("schedule_calculation", 3.2, "semantic_exact_scheduled_scenario")
        add("state_persistence", 2.0, "semantic_exact_scheduled_scenario")
        add("worker_runtime", 1.8, "semantic_exact_scheduled_scenario")
        add("audit_receipts", 1.4, "semantic_exact_scheduled_scenario")
    if exact_cold_start_takeover_like:
        add("leadership_coordination", 3.8, "semantic_exact_cold_start_takeover")
        add("lease_claims", 2.8, "semantic_exact_cold_start_takeover")
        add("heartbeat_liveness", 2.4, "semantic_exact_cold_start_takeover")
        add("checkpoint_resume", 1.8, "semantic_exact_cold_start_takeover")
    if exact_authority_governance_like:
        add("truth_ownership", 3.0, "semantic_exact_authority_governance")
        add("operator_entrypoints", 2.8, "semantic_exact_authority_governance")
        add("ledger_evidence", 2.8, "semantic_exact_authority_governance")
        add("audit_receipts", 1.6, "semantic_exact_authority_governance")
    if exact_intake_decomposition_like:
        add("task_register", 3.0, "semantic_exact_intake_decomposition")
        add("truth_ownership", 2.4, "semantic_exact_intake_decomposition")
        add("knowledge_retrieval", 2.6, "semantic_exact_intake_decomposition")
    if exact_transcript_idempotency_like:
        add("idempotency_store", 4.4, "semantic_exact_transcript_idempotency")
        add("turn_transcripts", 2.4, "semantic_exact_transcript_idempotency")
        add("execution_reconciliation", 2.2, "semantic_exact_transcript_idempotency")
    if exact_local_tooling_like:
        add("dependency_gating", 2.8, "semantic_exact_local_tooling")
        add("operator_entrypoints", 2.4, "semantic_exact_local_tooling")
        add("validation", 2.0, "semantic_exact_local_tooling")
        add("observability", 1.6, "semantic_exact_local_tooling")
        add("configuration_versioning", 1.4, "semantic_exact_local_tooling")
    if exact_verifier_audit_like:
        add("validation", 3.4, "semantic_exact_verifier_audit")
        add("audit_receipts", 3.0, "semantic_exact_verifier_audit")
        add("dependency_gating", 2.8, "semantic_exact_verifier_audit")
        add("operator_entrypoints", 1.8, "semantic_exact_verifier_audit")
        add("worker_runtime", 1.2, "semantic_exact_verifier_audit")
    if exact_ui_projection_like:
        add("operator_visible_state", 3.4, "semantic_exact_ui_projection")
        add("writeback_projection", 3.0, "semantic_exact_ui_projection")
        add("recovery_mechanisms", 2.2, "semantic_exact_ui_projection")
        add("validation", 1.5, "semantic_exact_ui_projection")
        add("checkpoint_resume", 1.4, "semantic_exact_ui_projection")
    if exact_cli_operator_experience_like:
        add("operator_entrypoints", 3.0, "semantic_exact_cli_operator_experience")
        add("validation", 2.4, "semantic_exact_cli_operator_experience")
        add("observability", 2.4, "semantic_exact_cli_operator_experience")
        add("queue_dispatch", 1.6, "semantic_exact_cli_operator_experience")
        add("checkpoint_resume", 1.0, "semantic_exact_cli_operator_experience")
    if corruption_recovery_like:
        add("state_persistence", 2.6, "semantic_corruption_recovery")
        add("recovery_mechanisms", 2.2, "semantic_corruption_recovery")
        add("validation", 1.4, "semantic_corruption_recovery")

    return boosts


def _semantic_subsystem_penalties(prompt_blob: str, context_blob: str, families: Sequence[str], *, title_blob: str = "") -> Dict[str, Tuple[float, str]]:
    text = _normalize_match_key(" ".join([prompt_blob, context_blob]))
    prompt_text = _normalize_match_key(title_blob or prompt_blob)
    penalties: Dict[str, Tuple[float, str]] = {}

    def add(subsystem: str, penalty: float, reason: str) -> None:
        existing = penalties.get(subsystem)
        if existing is None or penalty > existing[0]:
            penalties[subsystem] = (penalty, reason)

    memory_like = _is_lab_assistant_memory_family(families) or _is_truth_contract_family(families)
    chat_persistence_like = any(
        phrase in text
        for phrase in [
            "chat persistence",
            "direct chat",
            "session snapshot",
            "hydrate",
            "hydration",
            "turn id",
            "workflow bubbles",
            "reply",
            "transcript archaeology",
            "turn history",
        ]
    )
    bootstrap_reconstruction_like = "bootstrap" in text and any(
        phrase in text
        for phrase in [
            "task register",
            "reconstruct",
            "reconstruction",
            "degraded bootstrap",
            "fresh turn",
            "reload assembly",
        ]
    )
    detached_job_lifecycle_like = any(
        phrase in text
        for phrase in [
            "heartbeat",
            "timeout",
            "timed out",
            "long running job",
            "long-running job",
            "detached job",
            "worker health",
            "stale heartbeat",
            "cancellation",
            "cancelled",
            "interrupted",
        ]
    )
    corruption_recovery_like = any(phrase in text for phrase in ["corruption", "corrupt", "checksum", "integrity"]) and any(
        phrase in text for phrase in ["restart", "persist", "state", "recovery"]
    )
    exact_scheduled_scenario_like = any(
        phrase in prompt_text
        for phrase in [
            "overnight",
            "morning result",
            "morning summary",
            "scheduled overnight",
            "unattended operation",
            "deferred background chain",
            "schedule deferred",
        ]
    )
    exact_cold_start_takeover_like = any(
        phrase in prompt_text
        for phrase in [
            "cold start",
            "cold-start",
            "replacement backend",
            "takeover compatibility",
            "replacement startup",
        ]
    )
    exact_authority_governance_like = any(
        phrase in prompt_text
        for phrase in [
            "high-impact approval",
            "cross-tenant",
            "approval provenance",
            "authority tier",
            "tenancy boundary",
            "hidden override",
        ]
    )
    exact_intake_decomposition_like = any(
        phrase in prompt_text
        for phrase in [
            "task intake",
            "work decomposition",
            "acceptance target",
            "missing information",
            "scope negotiation",
            "precedent lookup",
        ]
    )
    exact_transcript_idempotency_like = "idempotency" in prompt_text and any(
        phrase in prompt_text for phrase in ["transcript", "turn to session", "turn-to-session", "replay guard", "duplicate replay"]
    )
    exact_local_tooling_like = any(
        phrase in prompt_text
        for phrase in [
            "deterministic local tooling",
            "token-efficiency fabric",
            "token efficiency fabric",
            "tool contract",
            "category model",
            "local tooling layer",
        ]
    )
    exact_verifier_audit_like = any(
        phrase in prompt_text
        for phrase in [
            "quality audit",
            "verifier strictness",
            "strictness recalibration",
            "one-iteration pis",
            "one iteration pis",
            "strict verifier",
        ]
    )
    exact_ui_projection_like = any(
        phrase in prompt_text
        for phrase in [
            "ui projection",
            "operator-visible state",
            "operator visible state",
            "degraded state",
            "unreachable state",
            "staleness",
            "stale projection",
        ]
    )
    exact_cli_operator_experience_like = any(
        phrase in prompt_text
        for phrase in [
            "cli operator",
            "operator experience",
            "codex-like cli",
            "command-line operator",
            "terminal operator",
        ]
    )
    scheduled_flow_like = any(
        phrase in text
        for phrase in [
            "scheduled",
            "overnight",
            "morning result",
            "morning summary",
            "deferred",
            "background chain",
            "scheduler",
            "schedule deferred",
            "next run",
            "unattended",
        ]
    )
    mailbox_writeback_seam_like = detect_mailbox_writeback_seam_focus_from_text(text, title_text=title_blob or prompt_blob)
    if memory_like and any(phrase in text for phrase in ["task register", "transcript", "contradiction", "correction", "reconcile", "open loop", "open-loop"]):
        add("assistant_runtime_state", 1.0, "semantic_specificity_penalty")
        add("truth_registry", 0.6, "semantic_specificity_penalty")
        add("serialization_replay", 0.8, "semantic_specificity_penalty")
    if memory_like and any(phrase in text for phrase in ["authority", "approval", "tenancy", "operator", "admin"]):
        add("task_register", 0.6, "semantic_authority_penalty")
        add("turn_transcripts", 0.6, "semantic_authority_penalty")
    if memory_like and chat_persistence_like:
        add("truth_ownership", 1.2, "semantic_chat_persistence_penalty")
        add("assistant_runtime_state", 0.8, "semantic_chat_persistence_penalty")
        add("serialization_replay", 0.7, "semantic_chat_persistence_penalty")
        add("lease_claims", 0.7, "semantic_chat_persistence_penalty")
        add("agent_orchestration", 1.2, "semantic_chat_persistence_penalty")
    if memory_like and bootstrap_reconstruction_like:
        add("agent_orchestration", 1.4, "semantic_bootstrap_reconstruction_penalty")
        add("serialization_replay", 0.8, "semantic_bootstrap_reconstruction_penalty")
        add("truth_ownership", 0.9, "semantic_bootstrap_reconstruction_penalty")
        add("lease_claims", 0.7, "semantic_bootstrap_reconstruction_penalty")
    if any(phrase in text for phrase in ["multi assistant", "multi-assistant", "delegation", "supervisory", "coordination", "workcell"]):
        add("state_persistence", 0.5, "semantic_orchestration_penalty")

    if any(phrase in text for phrase in ["preflight", "capability", "dependency", "prerequisite", "eligibility", "endpoint registry", "capability registry"]):
        add("state_persistence", 0.7, "semantic_dependency_penalty")
    if detached_job_lifecycle_like:
        add("agent_orchestration", 1.4, "semantic_detached_job_lifecycle_penalty")
    if mailbox_writeback_seam_like:
        add("truth_ownership", 2.8, "semantic_mailbox_writeback_seam_penalty")
        add("task_register", 2.6, "semantic_mailbox_writeback_seam_penalty")
        add("turn_transcripts", 1.4, "semantic_mailbox_writeback_seam_penalty")
        add("assistant_runtime_state", 1.2, "semantic_mailbox_writeback_seam_penalty")
        add("scheduler_core", 3.6, "semantic_mailbox_writeback_seam_penalty")
        add("schedule_calculation", 3.2, "semantic_mailbox_writeback_seam_penalty")
    if scheduled_flow_like:
        add("agent_orchestration", 1.6, "semantic_scheduled_flow_penalty")
    if exact_scheduled_scenario_like:
        add("ledger_evidence", 1.6, "semantic_exact_scheduled_scenario_penalty")
        add("task_register", 1.3, "semantic_exact_scheduled_scenario_penalty")
        add("turn_transcripts", 1.4, "semantic_exact_scheduled_scenario_penalty")
        add("execution_reconciliation", 1.0, "semantic_exact_scheduled_scenario_penalty")
    if exact_cold_start_takeover_like:
        add("agent_orchestration", 1.2, "semantic_exact_cold_start_takeover_penalty")
        add("validation", 0.5, "semantic_exact_cold_start_takeover_penalty")
        add("state_persistence", 0.5, "semantic_exact_cold_start_takeover_penalty")
    if exact_authority_governance_like:
        add("turn_transcripts", 0.8, "semantic_exact_authority_governance_penalty")
        add("assistant_runtime_state", 0.7, "semantic_exact_authority_governance_penalty")
        add("recovery_markers", 0.6, "semantic_exact_authority_governance_penalty")
    if exact_intake_decomposition_like:
        add("state_persistence", 0.9, "semantic_exact_intake_decomposition_penalty")
        add("worker_runtime", 0.7, "semantic_exact_intake_decomposition_penalty")
    if exact_transcript_idempotency_like:
        add("truth_ownership", 1.0, "semantic_exact_transcript_idempotency_penalty")
        add("assistant_runtime_state", 1.0, "semantic_exact_transcript_idempotency_penalty")
        add("state_persistence", 0.8, "semantic_exact_transcript_idempotency_penalty")
    if exact_local_tooling_like:
        add("state_persistence", 1.1, "semantic_exact_local_tooling_penalty")
        add("scheduler_core", 1.2, "semantic_exact_local_tooling_penalty")
        add("schedule_calculation", 1.4, "semantic_exact_local_tooling_penalty")
    if exact_verifier_audit_like:
        add("state_persistence", 1.6, "semantic_exact_verifier_audit_penalty")
        add("recovery_mechanisms", 1.2, "semantic_exact_verifier_audit_penalty")
        add("lease_claims", 1.0, "semantic_exact_verifier_audit_penalty")
        add("scheduler_core", 1.0, "semantic_exact_verifier_audit_penalty")
    if exact_ui_projection_like:
        add("state_persistence", 1.2, "semantic_exact_ui_projection_penalty")
        add("worker_runtime", 1.0, "semantic_exact_ui_projection_penalty")
        add("lease_claims", 0.9, "semantic_exact_ui_projection_penalty")
    if exact_cli_operator_experience_like:
        add("state_persistence", 1.0, "semantic_exact_cli_operator_experience_penalty")
        add("recovery_mechanisms", 0.8, "semantic_exact_cli_operator_experience_penalty")
        add("turn_transcripts", 0.8, "semantic_exact_cli_operator_experience_penalty")
        add("idempotency_store", 0.8, "semantic_exact_cli_operator_experience_penalty")
    if corruption_recovery_like:
        add("worker_runtime", 0.7, "semantic_corruption_recovery_penalty")
        add("heartbeat_liveness", 0.7, "semantic_corruption_recovery_penalty")

    return penalties


def _score_subsystems(
    task_profile: Mapping[str, Any],
    families: Sequence[str],
    subsystem_asset: Mapping[str, Any],
    templates_asset: Mapping[str, Any],
    texts: Mapping[str, Sequence[str]],
    *,
    extracted: Mapping[str, Sequence[str]],
    narrow_task: bool,
) -> Tuple[List[str], Dict[str, List[str]]]:
    context_blob = _context_blob(texts)
    prompt_blob = " ".join(texts.get("prompt") or [])
    prompt_entries = [str(item or "").strip() for item in list(texts.get("prompt") or []) if str(item or "").strip()]
    title_blob = prompt_entries[0] if prompt_entries else ""
    scores = _match_subsystems_from_bias(_coerce_mapping(task_profile.get("retrieval_bias")).get("include") or [], subsystem_asset)
    reasons: Dict[str, List[str]] = {key: ["retrieval_bias.include"] for key in scores}

    family_set = _family_alias_set(families)
    retrieval_contract_focus = _is_retrieval_ranking_family(families)
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        entry_families = {str(item or "").strip() for item in _coerce_list(entry.get("families")) if str(item or "").strip()}
        family_overlap = family_set.intersection(entry_families)
        if family_overlap:
            scores[key] = scores.get(key, 0.0) + 1.15 + 0.12 * len(family_overlap)
            reasons.setdefault(key, []).append("family=" + ",".join(sorted(family_overlap)))
        matched_prompt_terms = [term for term in list(entry.get("aliases") or [])[:8] if _contains_phrase(prompt_blob, term)]
        matched_context_terms = [term for term in list(entry.get("aliases") or [])[:8] if term not in matched_prompt_terms and _contains_phrase(context_blob, term)]
        if matched_prompt_terms:
            boost = min(1.7, 0.55 * len(matched_prompt_terms))
            scores[key] = scores.get(key, 0.0) + boost
            reasons.setdefault(key, []).append("prompt_terms=" + ",".join(matched_prompt_terms[:3]))
        elif matched_context_terms:
            boost = min(0.8, 0.16 * len(matched_context_terms))
            scores[key] = scores.get(key, 0.0) + boost
            reasons.setdefault(key, []).append("context_terms=" + ",".join(matched_context_terms[:3]))
        filename_hints = [Path(str(hint)).name.lower() for hint in _coerce_list(extracted.get("file_hints"))]
        matched_filenames = [name for name in list(entry.get("filename_aliases") or []) if Path(str(name)).name.lower() in filename_hints]
        matched_docs = [name for name in list(entry.get("doc_aliases") or []) if Path(str(name)).name.lower() in filename_hints]
        if matched_filenames or matched_docs:
            scores[key] = scores.get(key, 0.0) + 2.1
            reasons.setdefault(key, []).append("explicit_path_hint")

    for subsystem, (boost, reason) in _semantic_subsystem_boosts(prompt_blob, context_blob, families, title_blob=title_blob).items():
        scores[subsystem] = scores.get(subsystem, 0.0) + boost
        reasons.setdefault(subsystem, []).append(reason)
    for subsystem, (penalty, reason) in _semantic_subsystem_penalties(prompt_blob, context_blob, families, title_blob=title_blob).items():
        if subsystem not in scores:
            continue
        scores[subsystem] = scores.get(subsystem, 0.0) - penalty
        reasons.setdefault(subsystem, []).append(reason)

    operator_entrypoint_terms = [
        "pause",
        "resume",
        "manual pause",
        "manual resume",
        "enable",
        "disable",
        "postpone",
        "operator",
        "admin",
        "control endpoint",
    ]
    matched_operator_terms = [term for term in operator_entrypoint_terms if _contains_phrase(context_blob, term)]
    if matched_operator_terms and "operator_entrypoints" in {str(entry.get("key") or "").strip() for entry in _subsystem_entries(subsystem_asset)}:
        scores["operator_entrypoints"] = scores.get("operator_entrypoints", 0.0) + min(1.8, 0.65 * len(matched_operator_terms))
        reasons.setdefault("operator_entrypoints", []).append("explicit_operator_terms=" + ",".join(matched_operator_terms[:3]))

    for raw in _coerce_list(templates_asset.get("templates")):
        template = _coerce_mapping(raw)
        targets = _dedupe_strings(template.get("target_subsystems") or [])
        if not targets:
            continue
        matched_signals = [signal for signal in _coerce_list(template.get("signals")) if _contains_phrase(context_blob, signal)]
        if not matched_signals:
            continue
        for subsystem in targets:
            scores[subsystem] = scores.get(subsystem, 0.0) + min(1.2, 0.22 * len(matched_signals))
            reasons.setdefault(subsystem, []).append("template_signals=" + ",".join(matched_signals[:3]))

    ambiguity_raw = task_profile.get("ambiguity_score")
    classifier_confidence_raw = task_profile.get("classifier_confidence")
    ambiguity = float(ambiguity_raw) if ambiguity_raw is not None else 0.0
    classifier_confidence = float(classifier_confidence_raw) if classifier_confidence_raw is not None else None
    generic_like = family_set == {"generic"} or ("generic" in family_set and len(family_set) <= 2)
    if not narrow_task and (generic_like or ambiguity >= 0.6 or (classifier_confidence is not None and classifier_confidence <= 0.4)):
        for subsystem in ["validation", "observability", "state_persistence", "recovery_mechanisms", "operator_entrypoints"]:
            if subsystem in {entry.get("key") for entry in _subsystem_entries(subsystem_asset)}:
                scores[subsystem] = max(scores.get(subsystem, 0.0), 1.05 if generic_like else 0.85)
                reasons.setdefault(subsystem, []).append("fallback_widening")

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    explicit_bias_keys = [key for key, _ in ranked if "retrieval_bias.include" in reasons.get(key, [])]
    top_score = float(ranked[0][1]) if ranked else 0.0
    relative_floor = max(0.75, top_score * (0.38 if narrow_task else 0.34))
    ranked_candidates = [
        key for key, score in ranked if score >= relative_floor or "retrieval_bias.include" in reasons.get(key, [])
    ]
    if not ranked_candidates:
        ranked_candidates = [key for key, _ in ranked[:3]]

    base_keep = 4 if narrow_task else 6
    if narrow_task:
        max_keep = base_keep
    else:
        max_keep = max(base_keep, min(14, len(_dedupe_strings(explicit_bias_keys))))
        if _is_lab_assistant_memory_family(families):
            max_keep = max(max_keep, 12)
        if _is_truth_contract_family(families):
            max_keep = max(max_keep, 10)
    kept = ranked_candidates[:max_keep]
    if not kept:
        kept = [key for key, _ in ranked[:3]]

    if retrieval_contract_focus:
        # Retrieval-contract prompts often look narrow because the task is contract-shaped,
        # but Stage 4 still needs the owner/search/answer surfaces preserved explicitly.
        for subsystem in ["retrieval_ranker", "query_shaping", "answer_assembly"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)

    semantic_text = _normalize_match_key(" ".join([prompt_blob, context_blob]))
    prompt_semantic_text = _normalize_match_key(title_blob or prompt_blob)
    def prioritize_front(subsystems: Sequence[str]) -> None:
        nonlocal kept
        desired = [subsystem for subsystem in subsystems if subsystem in scores]
        if not desired:
            return
        front = [subsystem for subsystem in desired if subsystem in kept]
        front.extend(subsystem for subsystem in desired if subsystem not in kept)
        rest = [subsystem for subsystem in kept if subsystem not in set(desired)]
        kept = _dedupe_strings(front + rest)

    def demote_back(subsystems: Sequence[str]) -> None:
        nonlocal kept
        demoted = {subsystem for subsystem in subsystems if subsystem in kept}
        if not demoted:
            return
        front = [subsystem for subsystem in kept if subsystem not in demoted]
        back = [subsystem for subsystem in kept if subsystem in demoted]
        kept = _dedupe_strings(front + back)

    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "chat persistence",
            "direct chat",
            "session snapshot",
            "hydrate",
            "hydration",
            "turn id",
            "bootstrap reconstruction",
            "degraded bootstrap",
            "fresh turn",
            "reload assembly",
        ]
    ):
        prioritize_front(["state_persistence", "turn_transcripts", "checkpoint_resume", "recovery_mechanisms"])
        demote_back(["assistant_runtime_state", "truth_ownership", "runtime_state", "operator_visible_state", "serialization_replay"])
        for subsystem in ["checkpoint_resume", "recovery_mechanisms"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if "bootstrap" in prompt_semantic_text and "task register" in prompt_semantic_text and "task_register" in scores and "task_register" not in kept:
        kept.append("task_register")
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "overnight",
            "morning result",
            "morning summary",
            "scheduled overnight",
            "unattended operation",
            "deferred background chain",
            "schedule deferred",
        ]
    ):
        prioritize_front(["scheduler_core", "schedule_calculation", "state_persistence", "audit_receipts", "observability"])
        demote_back(["turn_transcripts", "task_register", "ledger_evidence", "execution_reconciliation", "writeback_projection", "truth_ownership", "knowledge_retrieval"])
        for subsystem in ["scheduler_core", "schedule_calculation", "audit_receipts"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "cold start",
            "cold-start",
            "replacement backend",
            "takeover compatibility",
            "replacement startup",
        ]
    ):
        for subsystem in ["leadership_coordination", "lease_claims", "heartbeat_liveness"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if "idempotency" in prompt_semantic_text and any(
        phrase in prompt_semantic_text for phrase in ["transcript", "turn to session", "turn-to-session", "replay guard", "duplicate replay"]
    ):
        prioritize_front(["turn_transcripts", "execution_reconciliation", "idempotency_store", "audit_receipts"])
        demote_back(["validation", "truth_ownership", "assistant_runtime_state", "operator_visible_state", "runtime_state"])
        for subsystem in ["idempotency_store", "turn_transcripts", "execution_reconciliation"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "deterministic local tooling",
            "token-efficiency fabric",
            "token efficiency fabric",
            "tool contract",
            "category model",
            "local tooling layer",
        ]
    ):
        prioritize_front(["dependency_gating", "operator_entrypoints", "validation", "observability", "configuration_versioning"])
        demote_back(["state_persistence", "scheduler_core", "schedule_calculation", "recovery_mechanisms", "lease_claims"])
        for subsystem in ["dependency_gating", "operator_entrypoints", "observability", "configuration_versioning"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "multi-assistant coordination",
            "assistant-to-assistant",
            "specialized assistant collaboration",
            "typed workers",
            "lead assistant",
            "specialization",
        ]
    ):
        prioritize_front(["agent_orchestration", "task_register", "truth_ownership", "operator_entrypoints", "execution_reconciliation"])
        demote_back(["state_persistence", "recovery_mechanisms", "checkpoint_resume", "validation"])
        for subsystem in ["agent_orchestration", "task_register", "truth_ownership", "operator_entrypoints"]:
            if subsystem in scores and subsystem not in kept:
                kept.append(subsystem)
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "quality audit",
            "verifier strictness",
            "strictness recalibration",
            "one-iteration pis",
            "one iteration pis",
            "strict verifier",
        ]
    ):
        prioritize_front(["validation", "audit_receipts", "dependency_gating", "operator_entrypoints", "worker_runtime"])
        demote_back(["state_persistence", "recovery_mechanisms", "lease_claims", "scheduler_core"])
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "ui projection",
            "operator-visible state",
            "operator visible state",
            "degraded state",
            "unreachable state",
            "staleness",
            "stale projection",
        ]
    ):
        prioritize_front(["operator_visible_state", "writeback_projection", "recovery_mechanisms", "validation", "checkpoint_resume"])
        demote_back(["state_persistence", "worker_runtime", "lease_claims", "scheduler_core", "schedule_calculation"])
    if any(
        phrase in prompt_semantic_text
        for phrase in [
            "cli operator",
            "operator experience",
            "codex-like cli",
            "command-line operator",
            "terminal operator",
        ]
    ):
        prioritize_front(["operator_entrypoints", "validation", "observability", "queue_dispatch", "checkpoint_resume"])
        demote_back(["state_persistence", "recovery_mechanisms", "turn_transcripts", "idempotency_store"])

    active_axes = [axis for axis in REQUIRED_CONCEPT_AXES if axis in _active_axes(texts, task_profile.get("cross_cutting_flags") or [], fallback_widening=False)]
    for axis in active_axes:
        if any(axis in _axes_for_subsystems([key]) for key in kept):
            continue
        candidate = next((key for key, _ in ranked if axis in _axes_for_subsystems([key])), None)
        if candidate and candidate not in kept:
            kept.append(candidate)

    return _dedupe_strings(kept), {key: _dedupe_strings(value) for key, value in reasons.items()}


def _query_type_for_text(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return "literal"
    if "/" in text or text.endswith((
        '.py', '.md', '.yaml', '.yml', '.json', '.js', '.ts', '.tsx', '.jsx'
    )):
        return "filename"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        if "_" in text or any(ch.isdigit() for ch in text) or any(ch.isupper() for ch in text[1:]):
            return "symbol"
        return "literal"
    return "literal"


def _axes_for_flags(flags: Sequence[str]) -> Set[str]:
    active = {str(flag or "").strip() for flag in flags if str(flag or "").strip()}
    out: Set[str] = set()
    for axis, flag_set in AXIS_FLAG_HINTS.items():
        if active.intersection(flag_set):
            out.add(axis)
    return out


def _axes_for_subsystems(subsystems: Sequence[str]) -> Set[str]:
    subsystem_set = {str(item or "").strip() for item in subsystems if str(item or "").strip()}
    axes: Set[str] = set()
    for axis, allowed in AXIS_TO_SUBSYSTEMS.items():
        if subsystem_set.intersection(allowed):
            axes.add(axis)
    return axes


def _covers_for_subsystems(subsystems: Sequence[str]) -> List[str]:
    axes = _axes_for_subsystems(subsystems)
    return _dedupe_strings(AXIS_TO_COVER_TAG[axis] for axis in axes if axis in AXIS_TO_COVER_TAG)


def _active_axes(texts: Mapping[str, Sequence[str]], active_flags: Sequence[str], *, fallback_widening: bool) -> Set[str]:
    axes = set(_axes_for_flags(active_flags))
    if texts.get("recovery_failure_modes") or texts.get("recovery_guarantees"):
        axes.add("recovery")
    if texts.get("concurrency_failure_modes"):
        axes.add("concurrency")
    if texts.get("operator_failure_modes"):
        axes.add("operator")
    if texts.get("system_failure_modes") or texts.get("invariants") or texts.get("safety_requirements"):
        axes.add("state")
    if texts.get("observability_requirements"):
        axes.add("observability")
    if fallback_widening and not axes:
        axes.update({"state", "recovery", "operator"})
    return axes


def _template_axes(template: Mapping[str, Any]) -> Set[str]:
    return _axes_for_subsystems(_dedupe_strings(template.get("target_subsystems") or []))


def _flags_for_axes(active_flags: Sequence[str], axes: Sequence[str]) -> List[str]:
    active = {str(flag or "").strip() for flag in active_flags if str(flag or "").strip()}
    requested = {str(axis or "").strip() for axis in axes if str(axis or "").strip()}
    out: List[str] = []
    for axis in requested:
        out.extend(sorted(active.intersection(AXIS_FLAG_HINTS.get(axis, set()))))
    return _dedupe_strings(out)


def _register_query_coverage(
    coverage_map: Dict[str, Dict[str, Any]],
    query: str,
    *,
    source: str,
    covers: Optional[Sequence[str]] = None,
    subsystems: Optional[Sequence[str]] = None,
    flags: Optional[Sequence[str]] = None,
    matched_terms: Optional[Sequence[str]] = None,
    template_key: Optional[str] = None,
    activation_score: Optional[float] = None,
) -> None:
    key = str(query or "").strip()
    if not key:
        return
    entry = coverage_map.setdefault(
        key,
        {
            "source": source,
            "covers": [],
            "subsystem": [],
            "cross_cutting_flags": [],
            "matched_terms": [],
        },
    )
    entry["source"] = source
    entry["covers"] = _dedupe_strings(list(entry.get("covers") or []) + list(covers or []))
    entry["subsystem"] = _dedupe_strings(list(entry.get("subsystem") or []) + list(subsystems or []))
    entry["cross_cutting_flags"] = _dedupe_strings(list(entry.get("cross_cutting_flags") or []) + list(flags or []))
    entry["matched_terms"] = _dedupe_strings(list(entry.get("matched_terms") or []) + list(matched_terms or []))
    entry["subsystems"] = list(entry["subsystem"])
    entry["flags"] = list(entry["cross_cutting_flags"])
    if template_key:
        entry["template_key"] = template_key
    if activation_score is not None:
        entry["activation_score"] = round(max(float(entry.get("activation_score") or 0.0), float(activation_score)), 4)


def _matched_inventory_terms(text: str, candidates: Sequence[str], *, limit: int) -> List[str]:
    out: List[str] = []
    for candidate in candidates:
        if _contains_phrase(text, candidate):
            out.append(str(candidate))
            if len(out) >= limit:
                break
    return _dedupe_strings(out)


def _canonical_fragment_token(token: str) -> str:
    canonical = str(token or "").strip().lower()
    if canonical.endswith("ies") and len(canonical) > 5:
        canonical = canonical[:-3] + "y"
    elif canonical.endswith("s") and len(canonical) > 5 and not canonical.endswith(("ss", "us", "is")):
        canonical = canonical[:-1]
    return canonical


def _failure_fragments(text: str, *, limit: int) -> List[str]:
    normalized = _normalize_match_key(text)
    out: List[str] = []
    for pattern, canonical in FAILURE_FRAGMENT_PATTERNS:
        if re.search(pattern, normalized):
            out.append(canonical)
            if len(out) >= limit:
                return _dedupe_strings(out)
    if len(out) < limit:
        words = [
            token
            for token in re.findall(r"[a-z0-9_]+", normalized)
            if len(token) > 3 and token not in LOW_SIGNAL_IDENTIFIERS
        ]
        for token in words:
            canonical = _canonical_fragment_token(token)
            if canonical in LOW_SIGNAL_IDENTIFIERS or canonical in GENERIC_FRAGMENT_TOKENS:
                continue
            out.append(canonical)
            if len(out) >= limit:
                break
    return _dedupe_strings(out)


def _signal_word_set(value: Any) -> Set[str]:
    out: Set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", _normalize_match_key(value)):
        if len(token) <= 2 or token in LOW_SIGNAL_IDENTIFIERS:
            continue
        out.add(token)
    return out


def _lexical_source_strength(source: str) -> float:
    normalized = str(source or "").strip()
    if not normalized:
        return 0.0
    for prefix, value in LEXICAL_SOURCE_STRENGTHS.items():
        if normalized == prefix or normalized.startswith(prefix + "."):
            return float(value)
    return 0.8


def _looks_weak_literal_query(text: str) -> bool:
    normalized = _normalize_match_key(text)
    tokens = [token for token in re.findall(r"[a-z0-9_]+", normalized) if token]
    if not tokens:
        return True
    if len(tokens) > 1:
        return False
    token = _canonical_fragment_token(tokens[0])
    if len(token) <= 4:
        return True
    if token in LOW_SIGNAL_IDENTIFIERS or token in GENERIC_FRAGMENT_TOKENS:
        return True
    return False


def _lexical_dedupe_key(text: str, query_type: str) -> str:
    normalized = _normalize_match_key(text)
    if query_type == "filename":
        return Path(str(text or "")).name.lower()
    if query_type in {"symbol", "test_name"}:
        return normalized.replace(" ", "")
    tokens = [
        token if token in DOMAIN_BIASED_TERMS else _canonical_fragment_token(token)
        for token in re.findall(r"[a-z0-9_]+", normalized)
        if token
    ]
    return " ".join(tokens[:4]) if tokens else normalized


def _system_failure_subtypes_from_text(text: Any) -> List[str]:
    normalized = _normalize_match_key(text)
    ranked: List[Tuple[float, str]] = []
    for subtype, rule in SYSTEM_FAILURE_SUBTYPE_RULES.items():
        hits = 0.0
        for signal in rule.get("signals", ()):
            if _contains_phrase(normalized, signal):
                hits += 1.5 if " " in str(signal) else 1.0
        if hits > 0:
            ranked.append((hits, subtype))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [subtype for _, subtype in ranked]


def _active_system_failure_subtypes(texts: Mapping[str, Sequence[str]]) -> List[str]:
    context = " ".join(
        list(texts.get("prompt") or [])
        + list(texts.get("system_failure_modes") or [])
        + list(texts.get("invariants") or [])
        + list(texts.get("safety_requirements") or [])
        + list(texts.get("recovery_guarantees") or [])
    )
    return _system_failure_subtypes_from_text(context)


def _system_failure_subtypes_for_query(
    query_text: str,
    *,
    meta: Optional[Mapping[str, Any]] = None,
    texts: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[str]:
    subtypes: List[str] = []
    template_key = str(_coerce_mapping(meta).get("template_key") or "").strip()
    subtypes.extend(SYSTEM_FAILURE_TEMPLATE_SUBTYPE_MAP.get(template_key, ()))
    signal_text = " ".join([str(query_text or "")] + list(_coerce_mapping(meta).get("matched_terms") or []))
    subtypes.extend(_system_failure_subtypes_from_text(signal_text))
    if not subtypes and texts is not None and (template_key == "system_failure_fallback" or "system_failure_modes" in _coerce_list(_coerce_mapping(meta).get("covers"))):
        subtypes.extend(_active_system_failure_subtypes(texts)[:2])
    return _dedupe_strings(subtypes)


def _covers_for_failure_text(bucket: str, axes: Sequence[str], text: Any, query_text: Any) -> List[str]:
    covers = list(_covers_for_failure_bucket(bucket, axes))
    if str(bucket or "").strip() == "system_failure_modes":
        covers.extend(_system_failure_subtypes_from_text(" ".join([str(text or ""), str(query_text or "")])))
    return _dedupe_strings(covers)


def _filter_query_coverage_map(
    query_coverage_map: Mapping[str, Mapping[str, Any]],
    *,
    kept_queries: Sequence[Mapping[str, Any]],
    conceptual_queries: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    keep = {str(item.get("query") or "").strip() for item in kept_queries if isinstance(item, Mapping)}
    keep.update(str(query or "").strip() for query in conceptual_queries if str(query or "").strip())
    return {str(key): dict(_coerce_mapping(value)) for key, value in query_coverage_map.items() if str(key or "").strip() in keep}


def _prune_lexical_queries(
    queries: Sequence[Mapping[str, Any]],
    *,
    query_coverage_map: Mapping[str, Mapping[str, Any]],
    extracted: Mapping[str, Sequence[str]],
    subsystem_hypotheses: Sequence[str],
    texts: Mapping[str, Sequence[str]],
    narrow_task: bool,
    fallback_widening: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    lexical_items = [dict(item) for item in queries if isinstance(item, Mapping) and str(item.get("query") or "").strip()]
    if not lexical_items:
        return [], []

    target_count = NARROW_LEXICAL_QUERY_TARGET if narrow_task else (FALLBACK_LEXICAL_QUERY_TARGET if fallback_widening else DEFAULT_LEXICAL_QUERY_TARGET)
    top_subsystems = {str(item or "").strip() for item in list(subsystem_hypotheses or [])[:3] if str(item or "").strip()}
    active_subtypes = set(_active_system_failure_subtypes(texts))
    required_buckets = {"system_failure_modes", "recovery_failure_modes", "operator_failure_modes", "concurrency_failure_modes"}
    prompt_terms = _signal_word_set(" ".join(texts.get("prompt") or []))
    explicit_paths = {Path(str(item or "")).name.lower() for item in _coerce_list(extracted.get("file_hints"))}
    explicit_symbols = {str(item or "").strip().lower() for item in _coerce_list(extracted.get("stacktrace_symbols"))}
    explicit_tests = {str(item or "").strip().lower() for item in _coerce_list(extracted.get("test_hints"))}

    scored: List[Tuple[float, str, Dict[str, Any], Dict[str, Any]]] = []
    for item in lexical_items:
        query_text = str(item.get("query") or "").strip()
        query_type = str(item.get("query_type") or "literal")
        meta = _coerce_mapping(query_coverage_map.get(query_text))
        source = str(item.get("source") or meta.get("source") or "")
        covers = {str(value or "").strip() for value in _coerce_list(meta.get("covers")) if str(value or "").strip()}
        subsystems = {str(value or "").strip() for value in _coerce_list(meta.get("subsystems") or meta.get("subsystem")) if str(value or "").strip()}
        score = 1.35 * float(item.get("weight") or 1.0) + _lexical_source_strength(source)
        if query_type == "filename":
            score += 3.8
        elif query_type == "symbol":
            score += 2.7
        elif query_type == "test_name":
            score += 2.35
        elif " " in _normalize_match_key(query_text):
            score += 1.7
        else:
            score += 0.45
        token_count = len(_signal_word_set(query_text))
        score += min(1.05, 0.18 * token_count)
        if len(query_text) >= 12:
            score += 0.2
        if covers.intersection(required_buckets):
            score += 1.1
        if covers.intersection(active_subtypes):
            score += 0.7
        if top_subsystems and subsystems.intersection(top_subsystems):
            score += 0.45
        if query_type == "literal" and _looks_weak_literal_query(query_text):
            score -= 2.1
        elif query_type == "literal" and source in {"prompt_token", "literal_identifier"}:
            score -= 0.6
        if source.startswith("failure_taxonomy") and query_type == "literal" and " " not in _normalize_match_key(query_text) and query_text.lower() not in prompt_terms and not covers.intersection(active_subtypes):
            score -= 0.95
        dedupe_key = _lexical_dedupe_key(query_text, query_type)
        scored.append((round(score, 4), dedupe_key, item, meta))

    best_by_key: Dict[str, Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}
    for score, dedupe_key, item, meta in scored:
        existing = best_by_key.get(dedupe_key)
        if existing is None or score > existing[0] or (score == existing[0] and str(item.get("query") or "") < str(existing[1].get("query") or "")):
            best_by_key[dedupe_key] = (score, item, meta)

    reduced = [(score, item, meta) for score, item, meta in best_by_key.values()]
    reduced.sort(key=lambda entry: (-entry[0], str(entry[1].get("query") or "")))

    kept_texts: Set[str] = set()
    kept: List[Dict[str, Any]] = []
    notes: List[str] = []
    preserved_failure_counts: Dict[str, int] = {}
    preserved_prompt_domain_terms: Set[str] = set()

    def keep(item: Mapping[str, Any]) -> None:
        query_text = str(item.get("query") or "").strip()
        if not query_text or query_text in kept_texts:
            return
        kept_texts.add(query_text)
        kept.append(dict(item))

    for _, item, meta in reduced:
        query_text = str(item.get("query") or "").strip()
        query_type = str(item.get("query_type") or "literal")
        source = str(item.get("source") or meta.get("source") or "")
        covers = {str(value or "").strip() for value in _coerce_list(meta.get("covers")) if str(value or "").strip()}
        lower = query_text.lower()
        basename = Path(query_text).name.lower()
        if source in {"replace_spec", "explicit_path_hint", "stacktrace_symbol", "failing_test"}:
            keep(item)
            continue
        if query_type == "filename" and basename in explicit_paths:
            keep(item)
            continue
        if query_type == "symbol" and lower in explicit_symbols:
            keep(item)
            continue
        if query_type == "test_name" and lower in explicit_tests:
            keep(item)
            continue
        if source in {"prompt_token", "safety_requirements", "literal_identifier"} and lower in prompt_terms and lower in DOMAIN_BIASED_TERMS and len(preserved_prompt_domain_terms) < 2:
            keep(item)
            preserved_prompt_domain_terms.add(lower)
            continue
        failure_buckets = sorted(covers.intersection(required_buckets))
        active_failure_hits = sorted(covers.intersection(active_subtypes))
        if source == "failure_taxonomy.recovery_failure_modes" and query_text in PREFERRED_RECOVERY_FAILURE_TERMS:
            keep(item)
            continue
        if failure_buckets or active_failure_hits:
            failure_key = active_failure_hits[0] if active_failure_hits else failure_buckets[0]
            keep_limit = 1 if query_type == "literal" and " " not in _normalize_match_key(query_text) else 2
            if failure_key == "recovery_failure_modes":
                keep_limit += 1
            if active_failure_hits:
                keep_limit += 1
            if preserved_failure_counts.get(failure_key, 0) < keep_limit:
                keep(item)
                preserved_failure_counts[failure_key] = preserved_failure_counts.get(failure_key, 0) + 1
                continue
        if source == "subsystem_map.doc":
            keep(item)
            continue
        if source == "subsystem_map.filename" and len(kept) < max(5, min(target_count, len(top_subsystems) + 3)):
            keep(item)

    prompt_domain_targets = [
        str(token or "").strip().lower()
        for token in _coerce_list(extracted.get("prompt_tokens"))
        if str(token or "").strip().lower() in DOMAIN_BIASED_TERMS
    ]
    for token in _dedupe_strings(prompt_domain_targets)[:3]:
        for _, item, _ in reduced:
            if str(item.get("query") or "").strip().lower() != token:
                continue
            keep(item)
            break

    for _, item, meta in reduced:
        if len(kept) >= target_count:
            break
        keep(item)

    if len(kept) < min(10, len(reduced)):
        for _, item, _ in reduced:
            if len(kept) >= min(10, len(reduced)):
                break
            keep(item)

    pruned_count = max(0, len(lexical_items) - len(kept))
    if pruned_count:
        notes.append(f"lexical_query_pruning={pruned_count}")
    notes.append(f"lexical_query_count={len(kept)}")
    return kept, notes


def _covers_for_failure_bucket(bucket: str, axes: Sequence[str]) -> List[str]:
    covers = [str(bucket or "").strip()]
    covers.extend(AXIS_TO_COVER_TAG.get(axis) for axis in axes if AXIS_TO_COVER_TAG.get(axis))
    return _dedupe_strings(covers)


def _query_coverage_contains_bucket(query_coverage_map: Mapping[str, Mapping[str, Any]], bucket: str) -> bool:
    target = str(bucket or "").strip()
    if not target:
        return False
    for meta in query_coverage_map.values():
        if target in _coerce_list(_coerce_mapping(meta).get("covers")):
            return True
    return False


def _template_section_matches(signals: Sequence[str], section_texts: Sequence[str]) -> List[str]:
    out: List[str] = []
    for signal in signals:
        if any(_contains_phrase(text, signal) for text in section_texts):
            out.append(str(signal))
    return _dedupe_strings(out)


def _conceptual_prompt_alignment(prompt_tokens: Set[str], query_text: str, key: str, targets: Sequence[str], signals: Sequence[str]) -> Tuple[List[str], List[str]]:
    query_tokens = _signal_word_set(" ".join([query_text, key] + list(targets) + list(signals)))
    token_hits = sorted(prompt_tokens.intersection(query_tokens))
    prompt_signal_hits = [signal for signal in signals if any(token in prompt_tokens for token in _signal_word_set(signal))]
    return token_hits[:6], _dedupe_strings(prompt_signal_hits)


def _best_system_failure_conceptual_query(
    conceptual_meta: Mapping[str, Mapping[str, Any]],
    *,
    active_subtypes: Optional[Sequence[str]] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    ranked: List[Tuple[float, str, Dict[str, Any]]] = []
    active_subtype_set = {str(item or "").strip() for item in list(active_subtypes or []) if str(item or "").strip()}
    for query_text, raw_meta in conceptual_meta.items():
        meta = _coerce_mapping(raw_meta)
        axes = {str(axis or "").strip() for axis in _coerce_list(meta.get("axes")) if str(axis or "").strip()}
        if "state" not in axes and not active_subtype_set:
            continue
        subsystems = _dedupe_strings(meta.get("subsystems") or [])
        score = float(meta.get("activation_score") or 0.0)
        template_key = str(meta.get("template_key") or "")
        if template_key in SYSTEM_FAILURE_TEMPLATE_KEYS or template_key in SYSTEM_FAILURE_TEMPLATE_SUBTYPE_MAP:
            score += 2.2
        query_subtypes = set(_system_failure_subtypes_for_query(str(query_text), meta=meta))
        if query_subtypes.intersection(active_subtype_set):
            score += 1.0
        if _coerce_list(meta.get("matched_terms")):
            score += 0.8
        if set(subsystems).intersection(SYSTEM_FAILURE_PREFERRED_SUBSYSTEMS):
            score += 0.6
        ranked.append((score, str(query_text), meta))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked[0][0] <= 0.0:
        return None
    return ranked[0][1], ranked[0][2]


def _system_failure_fallback_query(texts: Mapping[str, Sequence[str]]) -> str:
    active_subtypes = _active_system_failure_subtypes(texts)
    for subtype in active_subtypes:
        preferred = str(_coerce_mapping(SYSTEM_FAILURE_SUBTYPE_RULES.get(subtype)).get("fallback_query") or "").strip()
        if preferred:
            return preferred
    context = " ".join(
        list(texts.get("system_failure_modes") or [])
        + list(texts.get("invariants") or [])
        + list(texts.get("safety_requirements") or [])
        + list(texts.get("recovery_guarantees") or [])
    )
    lowered = _normalize_match_key(context)
    for query_text, hints in SYSTEM_FAILURE_FALLBACK_QUERIES:
        if hints and any(hint in lowered for hint in hints):
            return query_text
    return SYSTEM_FAILURE_FALLBACK_QUERIES[-1][0]


def _system_failure_subsystems(
    subsystem_hypotheses: Sequence[str],
    conceptual_meta: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    out: List[str] = []
    for subsystem in list(subsystem_hypotheses or []):
        if subsystem in SYSTEM_FAILURE_PREFERRED_SUBSYSTEMS:
            out.append(subsystem)
    for meta in conceptual_meta.values():
        for subsystem in _coerce_list(_coerce_mapping(meta).get("subsystems")):
            if subsystem in SYSTEM_FAILURE_PREFERRED_SUBSYSTEMS:
                out.append(str(subsystem))
    out.extend(SYSTEM_FAILURE_PREFERRED_SUBSYSTEMS)
    return _dedupe_strings(out)[:3]


def _ensure_system_failure_query_coverage(
    *,
    conceptual_queries: Sequence[str],
    conceptual_meta: Mapping[str, Mapping[str, Any]],
    query_coverage_map: Dict[str, Dict[str, Any]],
    texts: Mapping[str, Sequence[str]],
    active_flags: Sequence[str],
    subsystem_hypotheses: Sequence[str],
    fallback_widening: bool,
) -> Tuple[List[str], Dict[str, Dict[str, Any]], List[str]]:
    updated_meta: Dict[str, Dict[str, Any]] = {str(query): dict(_coerce_mapping(meta)) for query, meta in conceptual_meta.items()}
    active_subtypes = _active_system_failure_subtypes(texts)
    conceptual_query_set = {str(query or "").strip() for query in conceptual_queries if str(query or "").strip()}
    conceptual_bucket_covered = False
    if conceptual_query_set:
        for query_text, meta in query_coverage_map.items():
            if str(query_text or "").strip() not in conceptual_query_set:
                continue
            covers = _coerce_list(_coerce_mapping(meta).get("covers"))
            if "system_failure_modes" in covers:
                conceptual_bucket_covered = True
                break
    if conceptual_bucket_covered and _query_coverage_contains_bucket(query_coverage_map, "system_failure_modes"):
        return list(conceptual_queries), updated_meta, []

    if not fallback_widening:
        existing = _best_system_failure_conceptual_query(updated_meta, active_subtypes=active_subtypes)
        if existing is not None:
            query_text, meta = existing
            covers = _dedupe_strings(["system_failure_modes", "state_consistency"] + _system_failure_subtypes_for_query(query_text, meta=meta, texts=texts))
            _register_query_coverage(
                query_coverage_map,
                query_text,
                source="conceptual_query_templates",
                covers=covers,
                subsystems=meta.get("subsystems") or [],
                flags=_dedupe_strings(list(meta.get("flags") or []) + _flags_for_axes(active_flags, ["state"])),
                matched_terms=meta.get("matched_terms") or [],
                template_key=str(meta.get("template_key") or "") or None,
                activation_score=float(meta.get("activation_score") or 0.0),
            )
            return list(conceptual_queries), updated_meta, ["system_failure_coverage=attached_existing_concept"]

    fallback_query = _system_failure_fallback_query(texts)
    updated_queries = _dedupe_strings(list(conceptual_queries) + [fallback_query])
    fallback_meta = {
        "template_key": "system_failure_fallback",
        "subsystems": _system_failure_subsystems(subsystem_hypotheses, updated_meta),
        "axes": ["state", "recovery"],
        "flags": _flags_for_axes(active_flags, ["state", "recovery"]),
        "matched_terms": (
            list(texts.get("system_failure_modes") or [])
            + list(texts.get("invariants") or [])
            + list(texts.get("safety_requirements") or [])
        )[:2],
        "activation_score": 0.55,
    }
    updated_meta[fallback_query] = fallback_meta
    covers = _dedupe_strings(["system_failure_modes", "state_consistency"] + _system_failure_subtypes_for_query(fallback_query, meta=fallback_meta, texts=texts))
    _register_query_coverage(
        query_coverage_map,
        fallback_query,
        source="conceptual_query_templates.fallback",
        covers=covers,
        subsystems=fallback_meta["subsystems"],
        flags=fallback_meta["flags"],
        matched_terms=fallback_meta["matched_terms"],
        template_key="system_failure_fallback",
        activation_score=float(fallback_meta["activation_score"]),
    )
    notes = [f"system_failure_fallback_query={fallback_query}"]
    if active_subtypes:
        notes.append("system_failure_subtypes=" + ",".join(active_subtypes[:3]))
    return updated_queries, updated_meta, notes


def _lexical_inventory(
    subsystem_asset: Mapping[str, Any],
    flag_entries: Mapping[str, Mapping[str, Any]],
    templates_asset: Mapping[str, Any],
    subsystem_hypotheses: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    inventory: Dict[str, Dict[str, Any]] = {}
    subsystem_set = {str(item or "").strip() for item in subsystem_hypotheses if str(item or "").strip()}
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        if not key or (subsystem_set and key not in subsystem_set):
            continue
        for term in list(entry.get("aliases") or []):
            inventory.setdefault(str(term), {"source": "subsystem_map.alias", "subsystems": [key], "flags": []})
        for term in list(entry.get("filename_aliases") or []):
            inventory.setdefault(str(term), {"source": "subsystem_map.filename", "subsystems": [key], "flags": []})
        for term in list(entry.get("doc_aliases") or []):
            inventory.setdefault(str(term), {"source": "subsystem_map.doc", "subsystems": [key], "flags": []})
    for flag_key, entry in flag_entries.items():
        for term in list(entry.get("clue_terms") or []):
            inventory.setdefault(str(term), {"source": "cross_cutting_flags", "subsystems": [], "flags": [flag_key]})
    for raw in _coerce_list(templates_asset.get("templates")):
        template = _coerce_mapping(raw)
        targets = _dedupe_strings(template.get("target_subsystems") or [])
        if subsystem_set and not set(targets).intersection(subsystem_set):
            continue
        for term in list(template.get("signals") or []):
            inventory.setdefault(str(term), {"source": "conceptual_signals", "subsystems": targets, "flags": []})
    return inventory


def _derive_conceptual_queries(
    templates_asset: Mapping[str, Any],
    subsystem_hypotheses: Sequence[str],
    active_flags: Sequence[str],
    texts: Mapping[str, Sequence[str]],
    *,
    narrow_task: bool,
    fallback_widening: bool,
) -> Tuple[List[str], Dict[str, Dict[str, Any]], List[str]]:
    subsystem_set = {str(item or "").strip() for item in subsystem_hypotheses if str(item or "").strip()}
    primary_subsystems = [str(item or "").strip() for item in list(subsystem_hypotheses or [])[:2] if str(item or "").strip()]
    axes_active = _active_axes(texts, active_flags, fallback_widening=fallback_widening)
    prompt_blob = " ".join(texts.get("prompt") or [])
    prompt_tokens = _signal_word_set(prompt_blob)
    failure_sections = [
        "operator_failure_modes",
        "system_failure_modes",
        "concurrency_failure_modes",
        "recovery_failure_modes",
    ]
    requirement_sections = ["safety_requirements", "observability_requirements", "recovery_guarantees", "invariants"]
    active_system_subtypes = _active_system_failure_subtypes(texts)
    ranked: List[Tuple[float, str, Dict[str, Any]]] = []
    per_axis: Dict[str, List[Tuple[float, str, Dict[str, Any]]]] = {axis: [] for axis in REQUIRED_CONCEPT_AXES}
    active_flag_set = {str(flag or "").strip() for flag in active_flags if str(flag or "").strip()}

    for raw in _coerce_list(templates_asset.get("templates")):
        template = _coerce_mapping(raw)
        query_text = str(template.get("query_template") or "").strip()
        key = str(template.get("key") or "").strip()
        targets = _dedupe_strings(template.get("target_subsystems") or [])
        if not query_text or not targets:
            continue
        overlap = [name for name in targets if name in subsystem_set]
        primary_overlap = [name for name in overlap if name in primary_subsystems]
        axes = _template_axes(template)
        signals = _dedupe_strings(template.get("signals") or [])
        axis_overlap = axes.intersection(axes_active)
        prompt_token_hits, prompt_signal_hits = _conceptual_prompt_alignment(prompt_tokens, query_text, key, targets, signals)
        failure_signal_hits = _dedupe_strings(
            signal
            for section in failure_sections
            for signal in _template_section_matches(signals, texts.get(section) or [])
        )
        requirement_signal_hits = _dedupe_strings(
            signal
            for section in requirement_sections
            for signal in _template_section_matches(signals, texts.get(section) or [])
        )
        flag_signal_hits = _template_section_matches(signals, texts.get("flag_clues") or [])
        concept_flags = active_flag_set.intersection(CONCEPTUAL_FLAG_MATCHES.get(key, set()))
        matched_terms = _dedupe_strings(failure_signal_hits + requirement_signal_hits + prompt_signal_hits + flag_signal_hits)
        template_subtypes = set(SYSTEM_FAILURE_TEMPLATE_SUBTYPE_MAP.get(key, ()))
        active_subtype_hits = sorted(template_subtypes.intersection(active_system_subtypes))
        if not overlap and not matched_terms and not axis_overlap and not prompt_token_hits and not concept_flags and not active_subtype_hits:
            continue
        if narrow_task and not overlap and not matched_terms and not prompt_token_hits and not active_subtype_hits:
            continue

        support_score = (
            len(failure_signal_hits)
            + len(requirement_signal_hits)
            + len(prompt_signal_hits)
            + min(3, len(prompt_token_hits))
            + len(flag_signal_hits)
            + len(concept_flags)
            + len(active_subtype_hits)
        )
        overlap_only = bool(overlap) and support_score == 0 and not active_subtype_hits

        score = 0.0
        score += 0.62 * min(2, len(overlap))
        score += 0.38 * len(primary_overlap)
        score += 1.08 * len(failure_signal_hits)
        score += 0.84 * len(requirement_signal_hits)
        score += 0.72 * len(prompt_signal_hits)
        score += 0.28 * min(4, len(prompt_token_hits))
        score += 0.24 * len(flag_signal_hits)
        score += 0.34 * len(axis_overlap)
        if concept_flags:
            score += min(0.9, 0.28 * len(concept_flags))
        if active_subtype_hits:
            score += 0.9 + 0.35 * min(2, len(active_subtype_hits))
        if key == "duplicate_run_prevention" and ("scheduler_core" in overlap or "lease_claims" in overlap):
            score += 0.35
        if key == "heartbeat_liveness_decision" and ("recovery_mechanisms" in overlap or "heartbeat_liveness" in overlap):
            score += 0.35
        if key in SYSTEM_FAILURE_TEMPLATE_KEYS and not failure_signal_hits and not requirement_signal_hits and len(prompt_token_hits) + len(prompt_signal_hits) < 2:
            score -= 0.7
        if not overlap:
            score -= 0.3
        if support_score == 0:
            score -= 1.35
        elif not failure_signal_hits and not requirement_signal_hits and not prompt_signal_hits and not concept_flags and not active_subtype_hits:
            score -= 0.45
        if overlap_only:
            score -= 1.55 if not fallback_widening else 1.0
        if key in GENERIC_TEMPLATE_KEYS:
            if support_score == 0:
                score -= 1.2
            elif not failure_signal_hits and not requirement_signal_hits and len(prompt_signal_hits) + len(prompt_token_hits) < 2 and not concept_flags:
                score -= 0.65
        if not overlap and support_score < 2 and not active_subtype_hits:
            score -= 0.35
        if template_subtypes and not active_subtype_hits and not fallback_widening:
            score -= 0.5
        if not fallback_widening and score < 1.05 and support_score == 0 and not active_subtype_hits:
            continue
        if fallback_widening and score < 0.6 and support_score == 0 and not overlap and not active_subtype_hits:
            continue

        meta = {
            "template_key": key,
            "subsystems": overlap or targets[:3],
            "axes": sorted(axis_overlap or axes),
            "flags": _flags_for_axes(active_flags, list(axis_overlap or axes)),
            "matched_terms": matched_terms,
            "activation_score": round(score, 4),
            "system_failure_subtypes": active_subtype_hits,
        }
        ranked.append((score, query_text, meta))
        for axis in REQUIRED_CONCEPT_AXES:
            if axis in axes:
                per_axis.setdefault(axis, []).append((score, query_text, meta))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    for axis in per_axis:
        per_axis[axis].sort(key=lambda item: (-item[0], item[1]))

    selected: List[str] = []
    coverage_meta: Dict[str, Dict[str, Any]] = {}
    selection_notes: List[str] = []
    target_axes = [axis for axis in REQUIRED_CONCEPT_AXES if axis in axes_active or fallback_widening]
    if not target_axes:
        target_axes = ["state"]
    preferred_template_keys: List[str] = []
    if "scheduler_core" in subsystem_set and ("lease_claims" in subsystem_set or "queue_dispatch" in subsystem_set):
        preferred_template_keys.append("duplicate_run_prevention")
    if "recovery_mechanisms" in subsystem_set and ({"restart", "resume", "pause", "recovery", "recoverable"}.intersection(prompt_tokens) or "heartbeat_liveness" in subsystem_set):
        preferred_template_keys.append("heartbeat_liveness_decision")
    if "retry_backoff" in subsystem_set and "retry" in prompt_tokens:
        preferred_template_keys.append("retry_budget_enforcement")

    for axis in target_axes:
        for score, query_text, meta in per_axis.get(axis, []):
            if query_text in selected:
                continue
            selected.append(query_text)
            coverage_meta[query_text] = meta
            selection_notes.append(f"concept_axis={axis}:{meta.get('template_key')}")
            break

    for preferred_key in preferred_template_keys:
        for score, query_text, meta in ranked:
            if str(meta.get("template_key") or "") != preferred_key:
                continue
            if query_text in selected:
                break
            selected.append(query_text)
            coverage_meta[query_text] = meta
            selection_notes.append(f"preferred_concept={preferred_key}")
            break

    max_keep = 5 if narrow_task else (6 if fallback_widening else 7)
    minimum_activation = 1.1 if not fallback_widening else 0.75
    for score, query_text, meta in ranked:
        if len(selected) >= max_keep:
            break
        if query_text in selected:
            continue
        if score < minimum_activation:
            continue
        selected.append(query_text)
        coverage_meta[query_text] = meta

    if not selected:
        for score, query_text, meta in ranked[:2]:
            if query_text in selected:
                continue
            selected.append(query_text)
            coverage_meta[query_text] = meta

    if fallback_widening and len(selected) < min(4, max_keep):
        selection_notes.append("fallback_conceptual_expansion=true")

    return selected, coverage_meta, _dedupe_strings(selection_notes)


def _named_negative_rule_map(asset: Mapping[str, Any]) -> Dict[str, List[str]]:
    rules = _coerce_mapping(asset.get("rules"))
    named_rules = _coerce_mapping(rules.get("named_rules"))
    return {str(key): _dedupe_strings(value or []) for key, value in named_rules.items()}


def _derive_negative_targets(
    negative_targets_asset: Mapping[str, Any],
    exclude_values: Sequence[str],
    *,
    families: Sequence[str],
) -> List[str]:
    named_rules = _named_negative_rule_map(negative_targets_asset)
    out: List[str] = []
    for value in _dedupe_strings(exclude_values):
        out.extend(named_rules.get(str(value), []))
        if str(value).startswith(("*", "/")) or "/" in str(value) or "." in Path(str(value)).name:
            out.append(str(value))
    if "documentation_contracts" not in {str(item or "").strip() for item in families if str(item or "").strip()}:
        out.extend(
            [
                "infra/pipeline/knowledge_drafts/",
                "infra/pipeline/knowledge_drafts/benchmark-gpt/",
                "tests/benchmarks/pipeline/",
                "tests/benchmarks/pipeline/benchmark-gpt/",
                "infra/pipeline/knowledge_drafts/pis_datasets/",
                "*/scorecard_*.md",
                "*/gpt_benchmark_prompt_*.md",
                "*.dataset.yaml",
                "*.dataset.md",
            ]
        )
    if _is_lab_assistant_memory_family(families) or _is_truth_contract_family(families):
        forbidden = {pattern.lower() for pattern in named_rules.get("docs", []) + named_rules.get("reports", [])}
        out = [pattern for pattern in out if str(pattern).lower() not in forbidden]
    return _dedupe_strings(out)


def _build_semantic_query_text(
    job_contract: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    families: Sequence[str],
    lexical_queries: Sequence[str],
    conceptual_queries: Sequence[str],
    subsystem_hypotheses: Sequence[str],
) -> str:
    parts = [
        str(job_contract.get("objective") or ""),
        str(job_contract.get("description") or ""),
        str(_coerce_mapping(job_contract.get("issue_context")).get("user_notes") or ""),
    ]
    if _is_scheduler_family(families):
        parts.append("scheduler watchdog pipeline healer recovery assistant schedule start job pause resume cancel retry heartbeat orphaned operator failure modes")
    if "tool_spec_implementation" in _family_alias_set(families):
        parts.append("pipeline planning context bundle patch plan execution contract semantic handler embedding go comparison runner")
    if _is_lab_assistant_memory_family(families):
        parts.append("lab assistant research memory truth ownership execution reconciliation task register transcript ledger provenance freshness correction contradiction writeback supporting sources cross-project")
    if _is_truth_contract_family(families):
        parts.append("assistant identity runtime state source of truth precedence append-only event log write generation compare and swap recovery marker blocked state fail closed schema version transcript non canonical")
    if _is_retrieval_ranking_family(families):
        parts.append("retrieval ranking evidence-first answer assembly freshness applicability missing evidence query shaping search plan repo retriever context ranker")
    retrieval_bias = _coerce_mapping(task_profile.get("retrieval_bias"))
    if retrieval_bias:
        parts.append(" ".join(_dedupe_strings(list(retrieval_bias.get("include") or []) + list(retrieval_bias.get("exclude") or []))))
    if subsystem_hypotheses:
        parts.append(" ".join(subsystem_hypotheses[:8]))
    if lexical_queries:
        parts.append(" ".join(list(lexical_queries)[:10]))
    if conceptual_queries:
        parts.extend(list(conceptual_queries)[:4])
    text = " ".join(part for part in parts if part).strip()
    return text[:1400]


def _search_plan_confidence(
    *,
    families: Sequence[str],
    queries: Sequence[Mapping[str, Any]],
    conceptual_queries: Sequence[str],
    conceptual_meta: Mapping[str, Mapping[str, Any]],
    subsystem_hypotheses: Sequence[str],
    active_failure_buckets: Sequence[str],
    query_coverage_map: Mapping[str, Mapping[str, Any]],
    texts: Mapping[str, Sequence[str]],
    fallback_notes: Sequence[str],
) -> Dict[str, Any]:
    family_count = len([family for family in _dedupe_strings(families) if family != "generic"])
    lexical_queries = _dedupe_strings(item.get("query") for item in queries if isinstance(item, Mapping))
    lexical_count = len(lexical_queries)
    conceptual_count = len(_dedupe_strings(conceptual_queries))
    subsystem_count = len(_dedupe_strings(subsystem_hypotheses))

    prompt_terms = _signal_word_set(" ".join(texts.get("prompt") or []))
    prompt_focus = min(1.0, len(prompt_terms) / 4.0)
    aligned_query_terms: Set[str] = set()
    structured_query_count = 0
    for query in queries:
        if not isinstance(query, Mapping):
            continue
        query_text = str(query.get("query") or "")
        query_type = str(query.get("query_type") or "literal")
        if query_type in {"filename", "symbol", "test_name"}:
            structured_query_count += 1
        aligned_query_terms.update(prompt_terms.intersection(_signal_word_set(query_text)))
    lexical_quality = min(1.0, (len(aligned_query_terms) + 0.35 * structured_query_count) / 4.5)

    conceptual_strengths: List[float] = []
    for query_text in conceptual_queries:
        meta = _coerce_mapping(conceptual_meta.get(str(query_text)) or query_coverage_map.get(str(query_text)) or {})
        strength = float(meta.get("activation_score") or 0.0)
        if strength <= 0.0:
            strength += 0.45 * len(_coerce_list(meta.get("matched_terms")))
            strength += 0.25 * len(_coerce_list(meta.get("subsystems") or meta.get("subsystem")))
            strength += 0.15 * len(_coerce_list(meta.get("cross_cutting_flags") or meta.get("flags")))
            if meta.get("template_key"):
                strength += 0.25
        conceptual_strengths.append(strength)
    conceptual_strengths.sort(reverse=True)
    conceptual_strength = min(1.0, (sum(conceptual_strengths[:4]) / max(1, min(4, len(conceptual_strengths)))) / 5.25)

    required_buckets = {"system_failure_modes", "recovery_failure_modes", "operator_failure_modes", "concurrency_failure_modes"}
    active_buckets = set(_dedupe_strings(active_failure_buckets)).union(required_buckets)
    covered_buckets: Set[str] = set()
    for meta in query_coverage_map.values():
        covered_buckets.update(str(item) for item in _coerce_list(_coerce_mapping(meta).get("covers")))
    bucket_hits = len(required_buckets.intersection(covered_buckets))
    coverage_completeness = bucket_hits / float(len(required_buckets))

    subsystem_coherence = {
        0: 0.0,
        1: 0.96,
        2: 0.94,
        3: 0.9,
        4: 0.82,
        5: 0.7,
        6: 0.58,
    }.get(subsystem_count, 0.46)
    if set(_dedupe_strings(families)) == {"generic"}:
        subsystem_coherence *= 0.82
    elif coverage_completeness >= 1.0 and lexical_quality >= 0.8 and conceptual_strength >= 0.85 and prompt_focus >= 0.5:
        subsystem_coherence = max(subsystem_coherence, 0.82)

    fallback_penalty = 0.0
    for note in _dedupe_strings(fallback_notes):
        if note in {"low_confidence_breadth_expansion=true", "fallback_conceptual_expansion=true"}:
            fallback_penalty += 0.16
        elif str(note).startswith("system_failure_fallback_query="):
            fallback_penalty += 0.08
        elif note == "asset_fallback_families=generic":
            fallback_penalty += 0.08
    fallback_penalty = min(0.32, fallback_penalty)
    generic_penalty = 0.08 if set(_dedupe_strings(families)) == {"generic"} else 0.0

    score = 0.06
    score += 0.28 * coverage_completeness
    score += 0.20 * subsystem_coherence
    score += 0.14 * lexical_quality
    score += 0.10 * conceptual_strength
    score += 0.10 * prompt_focus
    score += 0.05 * min(1.0, family_count / 2.0)
    score -= fallback_penalty
    score -= generic_penalty
    score = max(0.12, min(0.93, round(score, 4)))

    notes = [
        f"active_task_families={len(_dedupe_strings(families))}",
        f"lexical_queries={lexical_count}",
        f"conceptual_queries={conceptual_count}",
        f"subsystem_hypotheses={subsystem_count}",
        f"required_bucket_coverage={bucket_hits}/{len(required_buckets)}",
        f"query_quality={round(lexical_quality, 3)}",
        f"conceptual_strength={round(conceptual_strength, 3)}",
        f"subsystem_coherence={round(subsystem_coherence, 3)}",
    ]
    notes.extend(_dedupe_strings(fallback_notes))
    return confidence_payload(stage=STAGE_KEY, score=score, source="deterministic_search_plan_engine", notes=_dedupe_strings(notes)[:10])


def _prioritize_mailbox_writeback_seam_queries(
    queries: Sequence[Mapping[str, Any]],
    *,
    mailbox_writeback_seam_focus: bool,
) -> List[Dict[str, Any]]:
    ordered_queries = [dict(item) for item in queries if isinstance(item, Mapping)]
    if not mailbox_writeback_seam_focus or not ordered_queries:
        return ordered_queries

    preferred_order = [
        "server_lab_assistant_mailbox.py",
        "server_lab_assistant_job_lifecycle.py",
        "server_lab_assistant_writeback.py",
        "test_lab_assistant_mailbox.py",
        "test_lab_assistant_job_lifecycle.py",
        "test_lab_assistant_writeback.py",
    ]
    by_query = {
        str(item.get("query") or "").strip().lower(): dict(item)
        for item in ordered_queries
        if str(item.get("query") or "").strip()
    }
    prioritized: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for query_text in preferred_order:
        item = by_query.get(query_text.lower())
        if item is None:
            continue
        normalized = str(item.get("query") or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        prioritized.append(dict(item))
        seen.add(normalized)
    for item in ordered_queries:
        normalized = str(item.get("query") or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        prioritized.append(dict(item))
        seen.add(normalized)
    return prioritized


def build_search_plan(
    job_contract: Dict[str, Any],
    task_profile: Dict[str, Any],    max_candidates: int,
    *,
    intake_assessment: Optional[Dict[str, Any]] = None,
    repo_root: str | Path = ".",
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    families = list(task_profile.get("task_families") or ensure_intake_assessment(intake_assessment).get("task_families") or detect_task_families(job_contract))
    families = _ordered_family_list(families) or ["generic"]
    replace_spec = detect_global_replace(job_contract) or dict(task_profile.get("replace_spec") or {})
    is_global_replace = bool(replace_spec) or "global_replace" in list(task_profile.get("secondary_tags") or [])
    narrow_task = _looks_narrow_task(job_contract, task_profile, families)
    narrow_ui_task = _is_narrow_ui_task(job_contract, task_profile, families)

    assets = _load_search_assets(job_contract.get("repo_root") or repo_root, config)
    merged_intake, stage1_notes = _merge_stage1_context(
        intake_assessment or {},
        task_profile,
        families,
        assets,
        narrow_task=narrow_task,
    )
    active_flags = _dedupe_strings(task_profile.get("cross_cutting_flags") or [])
    flag_entries = _active_flag_entries(assets.get("cross_cutting_flags") or {}, active_flags)
    texts = _context_texts(job_contract, merged_intake, flag_entries)
    extracted = _extract_terms(job_contract)

    fallback_widening = _needs_fallback_widening(task_profile, families, stage1_notes)
    subsystem_hypotheses, subsystem_reasons = _score_subsystems(
        task_profile,
        families,
        assets.get("subsystem_map") or {},
        assets.get("conceptual_query_templates") or {},
        texts,
        extracted=extracted,
        narrow_task=narrow_task,
    )
    mailbox_writeback_seam_focus = detect_mailbox_writeback_seam_focus(
        job_contract,
        task_profile=task_profile,
        search_plan={"subsystem_hypotheses": subsystem_hypotheses},
        query_terms=extracted.get("prompt_tokens") or [],
    )

    conceptual_queries, conceptual_meta, conceptual_notes = _derive_conceptual_queries(        assets.get("conceptual_query_templates") or {},
        subsystem_hypotheses,
        active_flags,
        texts,
        narrow_task=narrow_task,
        fallback_widening=fallback_widening,
    )

    queries: List[Dict[str, Any]] = []
    query_coverage_map: Dict[str, Dict[str, Any]] = {}
    query_lookup: Dict[str, Dict[str, Any]] = {}

    def add_query(
        query: str,
        query_type: str,
        weight: float,
        *,
        source: str,
        covers: Optional[Sequence[str]] = None,
        subsystems: Optional[Sequence[str]] = None,
        flags: Optional[Sequence[str]] = None,
        matched_terms: Optional[Sequence[str]] = None,
        template_key: Optional[str] = None,
    ) -> None:
        text = _sanitize_query_text(query)
        if not text:
            return
        normalized = text.lower()
        existing = query_lookup.get(normalized)
        if existing is None:
            payload = {"query": text, "query_type": query_type, "weight": round(float(weight), 4), "source": source}
            queries.append(payload)
            query_lookup[normalized] = payload
        else:
            existing["weight"] = round(max(float(existing.get("weight") or 0.0), float(weight)), 4)
            if TYPE_PRIORITY.get(query_type, 0) > TYPE_PRIORITY.get(str(existing.get("query_type") or "literal"), 0):
                existing["query_type"] = query_type
            if _lexical_source_strength(source) > _lexical_source_strength(str(existing.get("source") or "")):
                existing["source"] = source
        _register_query_coverage(
            query_coverage_map,
            text,
            source=source,
            covers=covers,
            subsystems=subsystems,
            flags=flags,
            matched_terms=matched_terms,
            template_key=template_key,
        )

    if is_global_replace and replace_spec:
        old = str(replace_spec.get("old") or "").strip()
        if old:
            add_query(old, "literal", 1.2, source="replace_spec", covers=["state_consistency"], subsystems=subsystem_hypotheses[:2], flags=active_flags[:2])
        for path in extracted["file_hints"][:5]:
            add_query(path, "filename", 0.9, source="explicit_path_hint", covers=["state_consistency"], subsystems=subsystem_hypotheses[:2], flags=active_flags[:2])
    else:
        for literal in extracted["quoted_literals"][:5]:
            add_query(literal, "literal", 1.0, source="prompt_literal", covers=["state_consistency"], subsystems=subsystem_hypotheses[:2], flags=active_flags[:2])
        for symbol in extracted["stacktrace_symbols"][:5]:
            add_query(symbol, "symbol", 0.95, source="stacktrace_symbol", covers=["state_consistency"], subsystems=subsystem_hypotheses[:2], flags=active_flags[:2])
        for name in extracted["test_hints"][:5]:
            add_query(name, "test_name", 0.9, source="failing_test", covers=["state_consistency"], subsystems=subsystem_hypotheses[:2], flags=active_flags[:2])
        for path in extracted["file_hints"][:6]:
            add_query(path, "filename", 0.9, source="explicit_path_hint", covers=["state_consistency"], subsystems=subsystem_hypotheses[:3], flags=active_flags[:2])
        prompt_token_limit = 4 if narrow_task else 7
        for token in extracted["prompt_tokens"][:prompt_token_limit]:
            add_query(token, "literal", 0.6 if token not in DOMAIN_BIASED_TERMS else 0.76, source="prompt_token", covers=[], subsystems=[], flags=[])
        strong_identifiers = [i for i in extracted["identifiers"] if i.lower() in DOMAIN_BIASED_TERMS]
        symbolic_identifiers = [i for i in extracted["identifiers"] if i.lower() not in DOMAIN_BIASED_TERMS and _looks_symbol_like_identifier(i)]
        literal_identifiers = [i for i in extracted["identifiers"] if i.lower() not in DOMAIN_BIASED_TERMS and not _looks_symbol_like_identifier(i)]
        if narrow_ui_task:
            strong_identifiers = [ident for ident in strong_identifiers if ident.lower() not in NARROW_UI_IDENTIFIER_SUPPRESSIONS]
            symbolic_identifiers = [ident for ident in symbolic_identifiers if ident.lower() not in NARROW_UI_IDENTIFIER_SUPPRESSIONS]
            literal_identifiers = [ident for ident in literal_identifiers if ident.lower() not in NARROW_UI_IDENTIFIER_SUPPRESSIONS]
        for ident in strong_identifiers[: (4 if narrow_task else 8)]:
            add_query(ident, _query_type_for_text(ident), 0.82, source="domain_identifier", covers=[], subsystems=[], flags=[])
        for ident in symbolic_identifiers[: (2 if narrow_task else 5)]:
            add_query(ident, "symbol", 0.72, source="symbol_identifier", covers=[], subsystems=[], flags=[])
        for ident in literal_identifiers[: (2 if narrow_task else 4)]:
            add_query(ident, "literal", 0.42, source="literal_identifier", covers=[], subsystems=[], flags=[])

    if mailbox_writeback_seam_focus:
        seam_query_specs = [
            ("server_lab_assistant_mailbox.py", ["queue_dispatch", "lease_claims", "lab_assistant_delegation"], ["concurrency_failure_modes", "state_consistency"]),
            ("server_lab_assistant_job_lifecycle.py", ["worker_runtime", "lease_claims", "audit_receipts"], ["recovery_failure_modes", "state_consistency"]),
            ("server_lab_assistant_writeback.py", ["writeback_projection", "audit_receipts"], ["state_consistency", "observability"]),
            ("test_lab_assistant_mailbox.py", ["queue_dispatch", "lease_claims"], ["concurrency_failure_modes"]),
            ("test_lab_assistant_job_lifecycle.py", ["worker_runtime", "audit_receipts"], ["recovery_failure_modes"]),
            ("test_lab_assistant_writeback.py", ["writeback_projection"], ["state_consistency"]),
        ]
        for query_text, subsystems, covers in seam_query_specs:
            add_query(
                query_text,
                "filename",
                1.04 if query_text.startswith("server_") else 0.92,
                source="mailbox_writeback_seam.filename",
                covers=covers,
                subsystems=subsystems,
                flags=_flags_for_axes(active_flags, ["concurrency", "recovery", "state"]),
            )

    # Asset-backed lexical expansion from subsystems, failure taxonomy, invariants, and requirements.
    inventory = _lexical_inventory(
        assets.get("subsystem_map") or {},        flag_entries,
        assets.get("conceptual_query_templates") or {},
        subsystem_hypotheses,
    )
    inventory_terms = list(inventory.keys())

    bucket_to_axes = {
        "operator_failure_modes": ["operator"],
        "system_failure_modes": ["state"],
        "concurrency_failure_modes": ["concurrency"],
        "recovery_failure_modes": ["recovery"],
    }

    for bucket, axes in bucket_to_axes.items():
        limit = 2 if narrow_task else 4
        for text in list(texts.get(bucket) or [])[:limit]:
            matched_terms = _matched_inventory_terms(text, inventory_terms, limit=3 if not narrow_task else 2)
            failure_terms = _failure_fragments(text, limit=2 if narrow_task else 3)
            for term in _dedupe_strings(matched_terms + failure_terms):
                meta = inventory.get(term, {})
                subsystems = _dedupe_strings(list(meta.get("subsystems") or []) or subsystem_hypotheses[:2])
                flags = _dedupe_strings(list(meta.get("flags") or []) + _flags_for_axes(active_flags, axes))
                covers = _covers_for_failure_text(bucket, axes, text, term)
                add_query(
                    term,
                    _query_type_for_text(term),
                    0.88 if " " in term else 0.82,
                    source=f"failure_taxonomy.{bucket}",
                    covers=covers,
                    subsystems=subsystems,
                    flags=flags,
                    matched_terms=[text],
                )

    for section_name, axes in {
        "invariants": ["state"],
        "safety_requirements": ["state", "operator"],
        "observability_requirements": ["observability"],
        "recovery_guarantees": ["recovery"],
    }.items():
        for text in list(texts.get(section_name) or [])[: (2 if narrow_task else 4)]:
            matched_terms = _matched_inventory_terms(text, inventory_terms, limit=2 if narrow_task else 3)
            for term in matched_terms:
                meta = inventory.get(term, {})
                subsystems = _dedupe_strings(list(meta.get("subsystems") or []) or subsystem_hypotheses[:2])
                covers = _dedupe_strings([AXIS_TO_COVER_TAG.get(axis) for axis in axes if AXIS_TO_COVER_TAG.get(axis)])
                add_query(
                    term,
                    _query_type_for_text(term),
                    0.78 if " " in term else 0.72,
                    source=section_name,
                    covers=covers,
                    subsystems=subsystems,
                    flags=_flags_for_axes(active_flags, axes),
                    matched_terms=[text],
                )

    for subsystem in subsystem_hypotheses:
        entry = next((item for item in _subsystem_entries(assets.get("subsystem_map") or {}) if str(item.get("key") or "") == subsystem), None)
        if not entry:
            continue
        filename_limit = 1 if narrow_task else 3
        for alias in list(entry.get("filename_aliases") or [])[:filename_limit]:
            add_query(
                alias,
                "filename",
                0.96,
                source="subsystem_map.filename",
                covers=_covers_for_subsystems([subsystem]),
                subsystems=[subsystem],
                flags=_flags_for_axes(active_flags, list(_axes_for_subsystems([subsystem]))),
            )
        if _is_lab_assistant_memory_family(families) or _is_truth_contract_family(families):
            for alias in list(entry.get("doc_aliases") or [])[:1]:
                add_query(
                    alias,
                    "filename",
                    0.78,
                    source="subsystem_map.doc",
                    covers=_covers_for_subsystems([subsystem]),
                    subsystems=[subsystem],
                    flags=_flags_for_axes(active_flags, list(_axes_for_subsystems([subsystem]))),
                )
        alias_limit = 1 if narrow_task else 2
        for alias in list(entry.get("aliases") or [])[:alias_limit]:
            if _looks_low_signal_identifier(str(alias).replace(" ", "")) and " " not in str(alias):
                continue
            add_query(
                alias,
                _query_type_for_text(str(alias)),
                0.74 if " " in str(alias) else 0.78,
                source="subsystem_map.alias",
                covers=_covers_for_subsystems([subsystem]),
                subsystems=[subsystem],
                flags=_flags_for_axes(active_flags, list(_axes_for_subsystems([subsystem]))),
            )

    for query_text, meta in conceptual_meta.items():
        covers = _dedupe_strings(
            [AXIS_TO_COVER_TAG.get(axis) for axis in meta.get("axes", []) if AXIS_TO_COVER_TAG.get(axis)]
            + _system_failure_subtypes_for_query(query_text, meta=meta, texts=texts)
        )
        _register_query_coverage(
            query_coverage_map,
            query_text,
            source="conceptual_query_templates",
            covers=covers,
            subsystems=meta.get("subsystems") or [],
            flags=meta.get("flags") or [],
            matched_terms=meta.get("matched_terms") or [],
            template_key=str(meta.get("template_key") or "") or None,
            activation_score=float(meta.get("activation_score") or 0.0),
        )

    conceptual_queries, conceptual_meta, system_failure_notes = _ensure_system_failure_query_coverage(
        conceptual_queries=conceptual_queries,
        conceptual_meta=conceptual_meta,
        query_coverage_map=query_coverage_map,
        texts=texts,
        active_flags=active_flags,
        subsystem_hypotheses=subsystem_hypotheses,
        fallback_widening=fallback_widening,
    )
    conceptual_notes = _dedupe_strings(list(conceptual_notes) + list(system_failure_notes))

    def apply_lexical_pruning(*, fallback_mode: bool) -> List[str]:
        nonlocal queries, query_coverage_map, query_lookup
        kept_queries, pruning_notes = _prune_lexical_queries(
            queries,
            query_coverage_map=query_coverage_map,
            extracted=extracted,
            subsystem_hypotheses=subsystem_hypotheses,
            texts=texts,
            narrow_task=narrow_task,
            fallback_widening=fallback_mode,
        )
        queries = kept_queries
        query_lookup = {
            str(item.get("query") or "").strip().lower(): item
            for item in queries
            if isinstance(item, Mapping) and str(item.get("query") or "").strip()
        }
        query_coverage_map = _filter_query_coverage_map(
            query_coverage_map,
            kept_queries=queries,
            conceptual_queries=conceptual_queries,
        )
        return pruning_notes

    pruning_notes = apply_lexical_pruning(fallback_mode=fallback_widening)

    if mailbox_writeback_seam_focus and not _is_scheduler_family(families):
        filtered_queries = [
            item
            for item in queries
            if not any(
                token in str(item.get("query") or "").lower()
                for token in ["scheduler_pipeline_healer", "scheduler_recovery_assistant", "scheduler_watchdog", "scheduled_jobs.py"]
            )
        ]
        if len(filtered_queries) != len(queries):
            queries = filtered_queries
            query_lookup = {
                str(item.get("query") or "").strip().lower(): item
                for item in queries
                if isinstance(item, Mapping) and str(item.get("query") or "").strip()
            }
            query_coverage_map = _filter_query_coverage_map(
                query_coverage_map,
                kept_queries=queries,
                conceptual_queries=conceptual_queries,
            )
            pruning_notes.append("mailbox_writeback_seam_removed_scheduler_filename_queries")

    queries = _prioritize_mailbox_writeback_seam_queries(
        queries,
        mailbox_writeback_seam_focus=mailbox_writeback_seam_focus,
    )
    query_lookup = {
        str(item.get("query") or "").strip().lower(): item
        for item in queries
        if isinstance(item, Mapping) and str(item.get("query") or "").strip()
    }
    query_coverage_map = _filter_query_coverage_map(
        query_coverage_map,
        kept_queries=queries,
        conceptual_queries=conceptual_queries,
    )

    if not queries:
        raise PipelineError("no_searchable_terms", "No searchable terms could be produced", exit_code=5)
    lexical_queries = _dedupe_strings(entry.get("query") for entry in queries if isinstance(entry, Mapping))
    active_failure_buckets = [bucket for bucket in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"] if texts.get(bucket)]
    confidence = _search_plan_confidence(
        families=families,
        queries=queries,
        conceptual_queries=conceptual_queries,
        conceptual_meta=conceptual_meta,
        subsystem_hypotheses=subsystem_hypotheses,
        active_failure_buckets=active_failure_buckets,
        query_coverage_map=query_coverage_map,
        texts=texts,
        fallback_notes=stage1_notes + conceptual_notes + pruning_notes,
    )

    confidence_floor = float(_coerce_mapping(config or {}).get("planning", {}).get("planning_confidence_floor", 0.55))
    notes = ["deterministic_only=true"]
    if is_global_replace:
        notes.append("search_mode=exhaustive_literal_replace")
    if families:
        notes.append("task_families=" + ",".join(families))
    retrieval_bias = _coerce_mapping(task_profile.get("retrieval_bias"))
    if retrieval_bias.get("include"):
        notes.append("retrieval_bias.include=" + ",".join(_dedupe_strings(retrieval_bias.get("include") or [])))
    if retrieval_bias.get("exclude"):
        notes.append("retrieval_bias.exclude=" + ",".join(_dedupe_strings(retrieval_bias.get("exclude") or [])))
    if active_flags:
        notes.append("cross_cutting_flags=" + ",".join(active_flags))
    if narrow_task:
        notes.append("narrow_task_suppression=true")
    if stage1_notes:
        notes.extend(stage1_notes)
    if conceptual_notes:
        notes.extend(conceptual_notes)
    if pruning_notes:
        notes.extend(pruning_notes)
    if _is_lab_assistant_memory_family(families):
        notes.append("docs_reference_allowed_for_lab_assistant_memory=true")
    if _is_truth_contract_family(families):
        notes.append("docs_reference_allowed_for_truth_contract=true")
    if mailbox_writeback_seam_focus:
        notes.append("mailbox_writeback_seam=true")
    if float(confidence.get("score") or 0.0) < confidence_floor:
        notes.append("low_confidence_breadth_expansion=true")
        notes.append("fallback_expansion=deterministic_asset_widening")
        top_fallback_subsystems = [
            subsystem
            for subsystem in subsystem_hypotheses[:2]
            if str(subsystem or "").strip()
        ]
        if not top_fallback_subsystems:
            top_fallback_subsystems = _dedupe_strings(
                subsystem
                for meta in conceptual_meta.values()
                for subsystem in list(_coerce_mapping(meta).get("subsystems") or [])[:1]
            )[:2]
        for subsystem in top_fallback_subsystems:
            entry = next((item for item in _subsystem_entries(assets.get("subsystem_map") or {}) if str(item.get("key") or "") == subsystem), None)
            if not entry:
                continue
            for alias in list(entry.get("aliases") or [])[2:4]:
                add_query(
                    alias,
                    _query_type_for_text(str(alias)),
                    0.68 if " " in str(alias) else 0.72,
                    source="fallback_subsystem_alias",
                    covers=_covers_for_subsystems([subsystem]),
                    subsystems=[subsystem],
                    flags=_flags_for_axes(active_flags, list(_axes_for_subsystems([subsystem]))),
                )
        fallback_pruning_notes = apply_lexical_pruning(fallback_mode=True)
        lexical_queries = _dedupe_strings(entry.get("query") for entry in queries if isinstance(entry, Mapping))
        confidence = _search_plan_confidence(
            families=families,
            queries=queries,
            conceptual_queries=conceptual_queries,
            conceptual_meta=conceptual_meta,
            subsystem_hypotheses=subsystem_hypotheses,
            active_failure_buckets=active_failure_buckets,
            query_coverage_map=query_coverage_map,
            texts=texts,
            fallback_notes=stage1_notes + conceptual_notes + pruning_notes + ["low_confidence_breadth_expansion=true"] + fallback_pruning_notes,
        )
        pruning_notes = _dedupe_strings(pruning_notes + fallback_pruning_notes)

    include_paths = _sanitize_path_filters(list(job_contract.get("allowed_paths") or []))
    exclude_paths = _sanitize_path_filters(list(job_contract.get("forbidden_paths") or []))
    exclude_paths.extend(_derive_negative_targets(assets.get("negative_targets") or {}, retrieval_bias.get("exclude") or [], families=families))
    if "tool_spec_implementation" not in _family_alias_set(families) and "deterministic_patch_helper" not in _family_alias_set(families):
        exclude_paths.extend(NON_PIPELINE_TASK_NEGATIVE_TARGETS)
    exclude_paths = _dedupe_strings(exclude_paths)

    strategies = ["ripgrep_literal", "ripgrep_regex", "filename_glob"]
    if any(str(entry.get("query_type") or "") == "symbol" for entry in queries):
        strategies.append("ast_grep")
    if extracted["test_hints"]:
        strategies.append("failing_test_expansion")

    effective_max = max(int(max_candidates), 500) if is_global_replace else min(int(max_candidates), 60 if narrow_task else int(max_candidates))
    semantic_query_text = _build_semantic_query_text(job_contract, task_profile, families, lexical_queries, conceptual_queries, subsystem_hypotheses)
    task_archetype = str(task_profile.get("task_archetype") or "implementation_change")
    search_mode = "exhaustive_literal_replace" if is_global_replace else {
        "design_new_surface": "mixed_design_bundle",
        "repo_assessment": "representative_assessment_bundle",
        "information_retrieval": "evidence_lookup",
        "documentation_contract": "contract_lookup",
        "verification_test": "verification_bundle",
        "configuration_change": "config_owner_bundle",
    }.get(task_archetype, "targeted_ranked_context")
    search_plan = {
        "queries": queries,
        "lexical_queries": lexical_queries,
        "conceptual_queries": conceptual_queries,
        "negative_targets": exclude_paths,
        "subsystem_hypotheses": subsystem_hypotheses,
        "query_coverage_map": query_coverage_map,
        "strategies": strategies,
        "max_candidates": effective_max,
        "notes": _dedupe_strings(notes + [f"task_archetype={task_archetype}", f"search_mode={search_mode}"]),
        "search_mode": search_mode,
        "replace_spec": replace_spec or None,
        "semantic_query_text": semantic_query_text,
        "confidence": confidence,
    }
    return {
        "intake_assessment": ensure_intake_assessment(intake_assessment),
        "job_contract": job_contract,
        "task_profile": ensure_task_profile_foundation(task_profile),
        "search_plan": ensure_search_plan_foundation(search_plan, negative_targets=exclude_paths),
        "extracted_terms": extracted,
        "policy": {
            "max_file_candidates": int(max_candidates),
            "max_snippet_candidates": int(max_candidates),
            "path_filters": {"include": include_paths, "exclude": exclude_paths},
        },
        "subsystem_reasoning": subsystem_reasons,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Stage 3 search plan")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract"])
        task_profile = dict(
            env.result.get("task_profile")
            or {
                "primary_task_type": str(env.result.get("job_contract", {}).get("task_type_hint") or "unknown"),
                "secondary_tags": [],
                "risk_level": "low",
                "expected_edit_scope": "single_file",
                "classifier_confidence": 0.0,
                "ambiguity_score": 0.0,
                "rationale": ["classifier_stage_skipped=true"],
                "model_assisted": False,
            }
        )
        result = build_search_plan(
            dict(env.result["job_contract"]),
            task_profile,
            int(config.get("limits", {}).get("max_file_candidates", 100)),
            intake_assessment=dict(env.result.get("intake_assessment") or {}),
            repo_root=args.repo_root,
            config=config,
        )
        retry_contract = dict(env.result.get("retry_contract") or {})
        if retry_contract:
            search_plan = dict(result.get("search_plan") or {})
            queries = list(search_plan.get("queries") or [])
            lexical_queries = list(search_plan.get("lexical_queries") or [])
            conceptual_queries = list(search_plan.get("conceptual_queries") or [])
            feedback_bundle = dict(retry_contract.get("feedback_bundle") or {})
            failure_signature = dict(retry_contract.get("failure_signature") or {})
            for path in list(feedback_bundle.get("affected_files") or [])[:4]:
                basename = Path(str(path or "")).name.strip()
                if basename:
                    lexical_queries.append(basename)
            lexical_queries.extend(list(failure_signature.get("key_error_tokens") or [])[:6])
            if str(retry_contract.get("selected_strategy") or "") == "task_reinterpretation":
                conceptual_queries.extend(
                    [
                        "alternative interpretation of the requested behavior",
                        "adjacent implementation and tests that define the intended contract",
                    ]
                )
            else:
                conceptual_queries.append("supporting validation files for the requested behavior")
            lexical_queries = _dedupe_strings(lexical_queries)
            conceptual_queries = _dedupe_strings(conceptual_queries)
            existing_pairs = {(str(item.get("query") or ""), str(item.get("query_type") or "")) for item in queries if isinstance(item, Mapping)}
            for query_text in lexical_queries:
                pair = (query_text, "literal")
                if pair in existing_pairs:
                    continue
                existing_pairs.add(pair)
                queries.append({"query": query_text, "query_type": "literal", "source": "retry_contract"})
            for query_text in conceptual_queries:
                pair = (query_text, "conceptual")
                if pair in existing_pairs:
                    continue
                existing_pairs.add(pair)
                queries.append({"query": query_text, "query_type": "conceptual", "source": "retry_contract"})
            search_plan["queries"] = queries
            search_plan["lexical_queries"] = lexical_queries
            search_plan["conceptual_queries"] = conceptual_queries
            search_plan["notes"] = _dedupe_strings(list(search_plan.get("notes") or []) + ["retry_contract_applied=true"])
            result["search_plan"] = ensure_search_plan_foundation(search_plan, negative_targets=list(search_plan.get("negative_targets") or []))
            result["retry_contract"] = retry_contract
        result["decision_trace"] = build_search_plan_decision_trace(result)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:  # pragma: no cover - CLI error path
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
