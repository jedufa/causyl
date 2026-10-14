from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .knowledge_assets import describe_knowledge_assets, load_knowledge_assets_with_meta, missing_required_knowledge_assets, resolve_knowledge_asset_paths
from .v2_foundation import confidence_payload, ensure_intake_assessment, ensure_task_profile_foundation
from .planning import detect_mailbox_writeback_seam_focus_from_text, detect_task_families
from .planning_ontology import derive_planning_profiles, infer_task_archetype

PHASE2_ASSET_NAMES = [    "task_families",
    "task_profile_rules",
    "cross_cutting_flags",
]
LOCAL_KNOWLEDGE_REPO_ROOT = Path(__file__).resolve().parents[2]
SECTION_WEIGHTS = {
    "title": 1.7,
    "objective": 1.4,
    "description": 1.0,
    "user_notes": 0.9,
    "constraints": 0.6,
}
EXPLICIT_HINT_TYPES = {"bugfix", "config", "docs", "feature", "refactor", "test_fix", "truth_contract", "orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
TRUTH_CONTRACT_FAMILIES = {"assistant_truth_and_identity"}
ORCHESTRATION_CHECKPOINT_FAMILIES = {"orchestration_checkpointing"}
UI_TRUTH_PROJECTION_FAMILIES = {"ui_truth_projection"}
RETRIEVAL_RANKING_FAMILIES = {"retrieval_ranking_contract"}
ROUTED_PRIMARY_TYPES = ("truth_contract", "orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract")
TRUTH_CONTRACT_TERMS = {
    "source of truth",
    "truth contract",
    "precedence",
    "authoritative",
    "assistant identity",
    "runtime state",
    "append-only",
    "audit event",
    "write arbitration",
    "write generation",
    "recovery marker",
    "blocked state",
    "schema version",
}
FAMILY_PRIORITY = {
    "assistant_truth_and_identity": 130,
    "orchestration_checkpointing": 128,
    "ui_truth_projection": 126,
    "retrieval_ranking_contract": 124,
    "scheduler_safety": 118,
    "scheduler_or_control_plane": 114,
    "operator_control": 108,
    "pipeline_internal_modification": 106,
    "knowledge_and_retrieval_policy": 104,
    "reconciliation_recovery": 96,
    "workflow_recovery": 95,
    "heartbeat_liveness": 92,
    "stateful_workflow": 90,
    "lab_assistant_memory": 88,
    "generic": 0,
}
SCHEDULER_SIGNAL_TERMS = ("scheduler", "scheduled job", "watchdog", "healer", "runtime policy", "schedule")
SCHEDULER_CONTROL_TERMS = ("runtime policy", "control plane", "enable scheduled jobs", "disable scheduled jobs", "pause", "resume", "restart", "manual")
RETRIEVAL_CONTRACT_TERMS = ("retrieval ranking", "evidence-first", "answer assembly", "freshness", "missing evidence", "applicability")
RETRIEVAL_POLICY_TERMS = ("retrieval bias", "subsystem map", "responsibility terms", "passive relevance", "negative targets", "knowledge asset", "policy")
PIPELINE_STAGE_TERMS = ("planning.py", "search plan", "repo retriever", "context ranker", "context bundle", "patch plan")
LAB_ASSISTANT_ROUTE_SIGNAL_TERMS = (
    "lab assistant",
    "task register",
    "operator snapshot",
    "execution truth ownership",
    "execution reconciliation",
    "turn transcript",
    "fresh-turn",
    "fresh turn",
    "bootstrap",
    "cold-start",
    "cold start",
    "reattach",
    "takeover",
    "delegation",
)
LAB_ASSISTANT_TRUTH_ROUTE_TERMS = (
    "source of truth",
    "assistant identity",
    "tenancy",
    "approval",
    "operator authority",
    "high-impact approval",
    "task register",
    "open loop",
    "canonical store",
    "mutation log",
    "turn identity",
    "turn event log",
    "session commit",
    "chat session",
    "durable turn",
    "turn record",
    "event replay",
    "turn state",
    "turn truth",
)
LAB_ASSISTANT_BOOTSTRAP_ROUTE_TERMS = (
    "bootstrap",
    "fresh-turn",
    "fresh turn",
    "context reload",
    "cold-start",
    "cold start",
    "reattach",
    "takeover",
    "checkpoint",
    "restart",
    "reload",
    "reconstruction",
)
LAB_ASSISTANT_PROJECTION_ROUTE_TERMS = (
    "snapshot",
    "ui projection",
    "degraded",
    "unreachable",
    "reconnecting",
    "reachability",
    "operator view",
)
LAB_ASSISTANT_FORCE_EXECUTION_ROUTE_TERMS = (
    "authority",
    "tenancy",
    "approval",
    "assistant identity",
    "source of truth",
    "bootstrap",
    "fresh-turn",
    "fresh turn",
    "cold-start",
    "cold start",
    "reattach",
    "takeover",
)
PROMINENT_SECTION_NAMES = {"title", "objective"}
SCHEDULER_EXPLICIT_CONCEPT_TERMS = (
    "scheduler",
    "scheduled",
    "scheduled job",
    "watchdog",
    "healer",
    "runtime policy",
    "control plane",
    "cron",
    "overnight",
    "unattended",
)
SYSTEM_HARDENING_CONCEPT_TERMS = (
    "hardening",
    "operations",
    "operational",
    "proof",
    "preflight",
    "capability",
    "registry",
    "tooling",
    "decomposition",
    "clarification",
    "deterministic",
    "efficiency",
)
TRUTH_CONTRACT_CONCEPT_TERMS = (
    "transcript",
    "task register",
    "open loop",
    "mutation log",
    "canonical store",
    "competing copy",
    "durable turn",
    "turn identity",
    "turn event log",
    "session commit",
    "append-only turn",
    "turn truth",
    "reconciliation",
    "identity",
    "authority",
    "tenancy",
    "approval",
    "contradiction",
    "conflict resolution",
    "source of truth",
    "truth",
    "persistence",
    "state machine",
    "session",
    "commit",
    "committed",
)
ORCHESTRATION_CONCEPT_TERMS = (
    "resume after restart",
    "workflow resume",
    "server restart",
    "app restart",
    "restart",
    "resume",
    "checkpoint",
    "checkpointing",
    "bootstrap",
    "reattach",
    "lifecycle",
    "heartbeat",
    "timeout",
    "disconnect",
    "truncated",
    "duplicate replay",
    "coordination",
    "delegation",
    "initiative",
    "multi-assistant",
    "autonomy",
)
CONFIG_CONCEPT_TERMS = (
    "strictness",
    "recalibration",
    "verifier",
)
WEAK_CONFIG_TERMS = {"json", "properties", "verifier"}
TURN_TRUTH_CONCEPT_TERMS = (
    "durable turn",
    "turn identity",
    "turn event log",
    "session commit",
    "chat session",
    "turn record",
    "event replay",
    "turn state",
    "turn truth",
    "reattach",
)
PATHLIKE_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
FILELIKE_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8}\b")


def _weighted_term_score(
    sections: Sequence[Tuple[str, str, float]],
    terms: Sequence[str],
    *,
    prominent_only: bool = False,
) -> float:
    score = 0.0
    for name, text, weight in sections:
        if prominent_only and name not in PROMINENT_SECTION_NAMES:
            continue
        section_bonus = 1.35 if name in {"title", "objective"} else 1.0
        for term in terms:
            if _section_contains(text, term):
                score += weight * section_bonus
    return round(score, 2)


def _section_term_hits(
    sections: Sequence[Tuple[str, str, float]],
    terms: Sequence[str],
    *,
    prominent_only: bool = False,
) -> List[str]:
    hits: List[str] = []
    for name, text, _ in sections:
        if prominent_only and name not in PROMINENT_SECTION_NAMES:
            continue
        for term in terms:
            if _section_contains(text, term):
                hits.append(str(term))
    return _dedupe_strings(hits)


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _dedupe_sections(sections: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    seen = set()
    for name, text in sections:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append((str(name), re.sub(r"\s+", " ", str(text or "")).strip()))
    return out


def _explicit_file_hints_from_sections(sections: Sequence[Tuple[str, str, float]]) -> List[str]:
    hints: List[str] = []
    for _name, text, _weight in sections:
        raw_text = str(text or "")
        for match in PATHLIKE_RE.finditer(raw_text):
            hints.append(match.group(0))
        for match in FILELIKE_RE.finditer(raw_text):
            hints.append(match.group(0))
    return _dedupe_strings(hints)


def _has_explicit_non_test_file_hint(sections: Sequence[Tuple[str, str, float]]) -> bool:
    candidates = _explicit_file_hints_from_sections(sections)
    for raw in _dedupe_strings(candidates):
        normalized = raw.replace("\\", "/").strip().lower()
        if not normalized:
            continue
        basename = Path(normalized).name.lower()
        if basename.startswith("test_") or normalized.startswith("tests/") or "/tests/" in f"/{normalized}":
            continue
        return True
    return False


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

    seen = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if explicit_knowledge_path:
            return candidate
        manifest = resolve_knowledge_asset_paths(candidate, config or {})
        exists = _coerce_mapping(manifest.get("exists"))
        if all(bool(exists.get(name)) for name in PHASE2_ASSET_NAMES):
            return candidate
    return LOCAL_KNOWLEDGE_REPO_ROOT.resolve()


def _load_phase2_assets(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    assets_repo_root = _resolve_assets_repo_root(repo_root, config)
    assets, _ = load_knowledge_assets_with_meta(assets_repo_root, config or {}, PHASE2_ASSET_NAMES)
    diagnostics = describe_knowledge_assets(assets_repo_root, config or {}, PHASE2_ASSET_NAMES)
    diagnostics["resolved_repo_root"] = str(assets_repo_root)
    missing = missing_required_knowledge_assets(assets_repo_root, config or {}, PHASE2_ASSET_NAMES)
    if missing:
        diagnostics["missing_required_details"] = missing
    return assets, diagnostics


def _prompt_sections(job_contract: Mapping[str, Any]) -> List[Tuple[str, str, float]]:
    issue_context = _coerce_mapping(job_contract.get("issue_context"))
    sections = _dedupe_sections(
        [
            ("title", str(job_contract.get("title") or "")),
            ("objective", str(job_contract.get("objective") or "")),
            ("description", str(job_contract.get("description") or "")),
            ("user_notes", str(issue_context.get("user_notes") or "")),
            ("constraints", " ".join(str(item) for item in _coerce_list(job_contract.get("constraints")))),
        ]
    )
    return [(name, text, float(SECTION_WEIGHTS.get(name, 1.0))) for name, text in sections]


def _combined_text(sections: Sequence[Tuple[str, str, float]]) -> str:
    return " ".join(text for _, text, _ in sections).lower()


def _normalized_family_map(asset: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return _coerce_mapping(asset.get("families"))


def _section_contains(text: str, trigger: str) -> bool:
    normalized_text = f" {str(text or '').lower()} "
    token = str(trigger or "").strip().lower()
    if not token:
        return False
    if " " in token or "-" in token or "_" in token:
        return token in normalized_text
    return re.search(rf"\b{re.escape(token)}s?\b", normalized_text) is not None


LEGACY_FAMILY_ALIAS_MAP = {
    "scheduler_hardening": "scheduler_safety",
    "workflow_recovery": "reconciliation_recovery",
}


def _fallback_detect_families(job_contract: Mapping[str, Any], task_families_asset: Mapping[str, Any]) -> Tuple[List[str], Dict[str, float]]:
    sections = _prompt_sections(job_contract)
    family_map = _normalized_family_map(task_families_asset)
    scored: List[Tuple[str, float]] = []
    family_scores: Dict[str, float] = {}
    for family_name, entry in family_map.items():
        family = str(family_name or "").strip()
        if not family or family == "generic":
            continue
        triggers = _dedupe_strings(_coerce_mapping(entry).get("triggers") or [])
        score = 0.0
        for _, text, weight in sections:
            for trigger in triggers:
                if _section_contains(text, trigger):
                    score += weight
        if score >= 1.0:
            family_scores[family] = round(score, 2)
            scored.append((family, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    families = [family for family, _ in scored[:4]]
    if not families:
        for legacy in detect_task_families(job_contract):
            mapped = LEGACY_FAMILY_ALIAS_MAP.get(str(legacy), str(legacy))
            if mapped in family_map and mapped not in families:
                families.append(mapped)
                family_scores[mapped] = max(float(family_scores.get(mapped) or 0.0), 1.0)
    if not families and "generic" in family_map:
        families = ["generic"]
    return families, family_scores


def _family_trigger_score(text: str, family_name: str, task_families_asset: Mapping[str, Any]) -> float:
    family_map = _normalized_family_map(task_families_asset)
    entry = _coerce_mapping(family_map.get(family_name))
    score = 0.0
    for trigger in _dedupe_strings(entry.get("triggers") or []):
        token = str(trigger or "").strip().lower()
        if not token:
            continue
        if _section_contains(text, token):
            score += 1.25 if (" " in token or "_" in token or "-" in token) else 1.0
    return round(score, 2)


def _sort_families_by_priority(active_families: Sequence[str], scores: Mapping[str, float]) -> List[str]:
    return sorted(
        _dedupe_strings(active_families),
        key=lambda family: (-int(FAMILY_PRIORITY.get(family, 50)), -float(scores.get(family) or 0.0), family),
    )


def _resolve_active_families(
    active_families: Sequence[str],
    *,
    text: str,
    task_families_asset: Mapping[str, Any],
    primary_task_type: str,
) -> Tuple[List[str], List[str], Dict[str, float]]:
    scores = {family: _family_trigger_score(text, family, task_families_asset) for family in _dedupe_strings(active_families)}
    resolved = _sort_families_by_priority(active_families, scores)
    notes: List[str] = []
    family_set = set(resolved)
    scheduler_signals = any(term in text for term in SCHEDULER_SIGNAL_TERMS)
    scheduler_control_signals = any(term in text for term in SCHEDULER_CONTROL_TERMS)
    retrieval_contract_signals = any(term in text for term in RETRIEVAL_CONTRACT_TERMS)
    retrieval_policy_signals = any(term in text for term in RETRIEVAL_POLICY_TERMS)
    pipeline_stage_signals = any(term in text for term in PIPELINE_STAGE_TERMS)

    if primary_task_type in ROUTED_PRIMARY_TYPES:
        resolved = _compact_routed_families(resolved, primary_task_type)
        notes.append(f"family_resolution=routed_primary:{primary_task_type}")
        if primary_task_type == "retrieval_ranking_contract" and any(
            family in {"pipeline_internal_modification", "knowledge_and_retrieval_policy"} for family in resolved
        ):
            notes.append("family_resolution=retrieval_contract_precedence")
        return resolved, notes, scores

    if scheduler_signals and family_set.intersection({"scheduler_safety", "scheduler_or_control_plane"}):
        keep: List[str] = ["scheduler_safety"]
        if scheduler_control_signals and "scheduler_or_control_plane" in family_set:
            keep.append("scheduler_or_control_plane")
        if scheduler_control_signals and "operator_control" in family_set:
            keep.append("operator_control")
        for family in resolved:
            if family in keep or family == "generic":
                continue
            if family in {"reconciliation_recovery", "workflow_recovery", "heartbeat_liveness", "stateful_workflow"}:
                if float(scores.get(family) or 0.0) >= float(scores.get("scheduler_safety") or 0.0) + 1.5:
                    keep.append(family)
                continue
            keep.append(family)
        resolved = _sort_families_by_priority(keep, scores)
        notes.append("family_resolution=scheduler_precedence")

    if "retrieval_ranking_contract" in family_set and retrieval_contract_signals:
        keep = ["retrieval_ranking_contract"]
        if retrieval_policy_signals and "knowledge_and_retrieval_policy" in family_set:
            keep.append("knowledge_and_retrieval_policy")
        if pipeline_stage_signals and "pipeline_internal_modification" in family_set:
            keep.append("pipeline_internal_modification")
        resolved = _sort_families_by_priority(keep, scores)
        notes.append("family_resolution=retrieval_contract_precedence")

    return resolved, notes, scores


def _design_signal(text: str, rules: Mapping[str, Any], sections: Sequence[Tuple[str, str, float]]) -> Tuple[bool, List[str], bool]:
    defaults = _coerce_mapping(rules.get("defaults"))
    terms = _dedupe_strings(defaults.get("design_intent_terms") or [])
    matched: List[str] = []
    strong = False
    for term in terms:
        if term and term in text:
            matched.append(term)
            if any(name in {"title", "objective"} and term in section_text.lower() for name, section_text, _ in sections):
                strong = True
    return bool(matched), _dedupe_strings(matched), strong


def _term_hits(text: str, values: Sequence[Any]) -> List[str]:
    matched: List[str] = []
    for value in values:
        term = str(value or "").strip().lower()
        if not term:
            continue
        if term in text:
            matched.append(term)
    return _dedupe_strings(matched)


def _truth_contract_hits(text: str, rules: Mapping[str, Any], family_rules: Sequence[Mapping[str, Any]]) -> List[str]:
    defaults = _coerce_mapping(rules.get("defaults"))
    configured = _dedupe_strings(defaults.get("truth_contract_terms") or [])
    terms = configured or sorted(TRUTH_CONTRACT_TERMS)
    hits = _term_hits(text, terms)
    family_triggered = any(str(_coerce_mapping(entry).get("primary_task_type") or "") == "truth_contract" for entry in family_rules)
    if family_triggered:
        hits.extend(term for term in sorted(TRUTH_CONTRACT_TERMS) if term in text)
    return _dedupe_strings(hits)


def _max_risk(current: str, candidate: str) -> str:
    return candidate if RISK_ORDER.get(candidate, 0) > RISK_ORDER.get(current, 0) else current


def _compact_routed_families(active_families: Sequence[str], primary_task_type: str) -> List[str]:
    families = [str(item or "").strip() for item in active_families if str(item or "").strip()]
    if primary_task_type == "truth_contract":
        focused = [family for family in families if family in TRUTH_CONTRACT_FAMILIES]
        return focused or ["assistant_truth_and_identity"]
    if primary_task_type == "orchestration_checkpointing":
        focused = [family for family in families if family in ORCHESTRATION_CHECKPOINT_FAMILIES]
        return focused or ["orchestration_checkpointing"]
    if primary_task_type == "ui_truth_projection":
        focused = [family for family in families if family in UI_TRUTH_PROJECTION_FAMILIES]
        return focused or ["ui_truth_projection"]
    if primary_task_type == "retrieval_ranking_contract":
        focused = [
            family for family in families
            if family in RETRIEVAL_RANKING_FAMILIES.union({"pipeline_internal_modification", "knowledge_and_retrieval_policy"})
        ]
        return focused or ["retrieval_ranking_contract"]
    return _dedupe_strings(families)


BIAS_INCLUDE_ALIASES = {
    "operator_entrypoints": ["operator_interfaces"],
    "state_persistence": ["state_management"],
    "recovery_mechanisms": ["recovery"],
}


def _expand_bias_aliases(values: Sequence[str], *, design_mode: bool = False) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        out.append(text)
        out.extend(BIAS_INCLUDE_ALIASES.get(text, []))
    if design_mode:
        out.append("architecture_surfaces")
    return _dedupe_strings(out)


def _merge_bias(defaults: Mapping[str, Any], families: Sequence[Mapping[str, Any]], *, design_rule: Optional[Mapping[str, Any]] = None, active_families: Sequence[str] = ()) -> Dict[str, List[str]]:
    default_bias = _coerce_mapping(defaults.get("retrieval_bias"))
    include: List[str] = list(default_bias.get("include") or [])
    exclude: List[str] = list(default_bias.get("exclude") or [])
    for entry in families:
        family_bias = _coerce_mapping(_coerce_mapping(entry).get("retrieval_bias"))
        include.extend(_coerce_list(family_bias.get("include")))
        exclude.extend(_coerce_list(family_bias.get("exclude")))
    active_family_set = {str(item or "").strip() for item in active_families if str(item or "").strip()}
    allow_docs_reference = design_rule is not None or "lab_assistant_memory" in active_family_set or bool(active_family_set.intersection(TRUTH_CONTRACT_FAMILIES))
    if design_rule:
        design_bias = _coerce_mapping(_coerce_mapping(design_rule).get("retrieval_bias"))
        include = list(design_bias.get("include") or []) + include
        exclude = list(design_bias.get("exclude") or []) + exclude
    if allow_docs_reference:
        exclude = [item for item in exclude if str(item) not in {"docs", "reports"}]
    return {"include": _expand_bias_aliases(include, design_mode=design_rule is not None), "exclude": _dedupe_strings(exclude)}


def _context_text_for_flags(job_contract: Mapping[str, Any], intake: Mapping[str, Any]) -> str:
    parts: List[str] = []
    sections = _prompt_sections(job_contract)
    parts.extend(text for _, text, _ in sections)
    failure_taxonomy = _coerce_mapping(intake.get("failure_taxonomy"))
    for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]:
        parts.extend(str(item) for item in _coerce_list(failure_taxonomy.get(key)))
    parts.extend(str(item) for item in _coerce_list(intake.get("invariants")))
    derived = _coerce_mapping(intake.get("derived_requirements"))
    for key in ["safety_requirements", "observability_requirements", "recovery_guarantees"]:
        parts.extend(str(item) for item in _coerce_list(derived.get(key)))
    return " ".join(parts).lower()


def _derive_cross_cutting_flags(active_families: Sequence[str], family_rules: Sequence[Mapping[str, Any]], cross_cutting_asset: Mapping[str, Any], job_contract: Mapping[str, Any], intake: Mapping[str, Any], *, design_mode: bool = False, design_rule: Optional[Mapping[str, Any]] = None) -> List[str]:
    flags_out: List[str] = []
    for entry in family_rules:
        flags_out.extend(_coerce_list(_coerce_mapping(entry).get("cross_cutting_flags")))
    if design_mode and design_rule:
        flags_out.extend(_coerce_list(_coerce_mapping(design_rule).get("cross_cutting_flags")))
    context_text = _context_text_for_flags(job_contract, intake)
    active_family_set = {str(item) for item in active_families}
    generic_only = active_family_set == {"generic"}
    for raw_flag in _coerce_list(cross_cutting_asset.get("flags")):
        flag = _coerce_mapping(raw_flag)
        key = str(flag.get("key") or "").strip()
        if not key:
            continue
        family_overlap = active_family_set.intersection({str(item) for item in _coerce_list(flag.get("families"))})
        clue_hits = [term for term in _coerce_list(flag.get("clue_terms")) if str(term).lower() in context_text]
        if family_overlap:
            flags_out.append(key)
            continue
        if generic_only:
            continue
        if len(clue_hits) >= 4:
            flags_out.append(key)
    return _dedupe_strings(flags_out)


def _classify_primary_type(
    text: str,
    rules: Mapping[str, Any],
    family_rules: Sequence[Mapping[str, Any]],
    *,
    active_family_names: Sequence[str],
    sections: Sequence[Tuple[str, str, float]],
    design_mode: bool,
    explicit_hint: str,
) -> Tuple[str, List[str]]:
    defaults = _coerce_mapping(rules.get("defaults"))
    rationale: List[str] = []
    if explicit_hint in EXPLICIT_HINT_TYPES:
        rationale.append(f"explicit_task_type_hint={explicit_hint}")
        return explicit_hint, rationale
    truth_contract_hits = _truth_contract_hits(text, rules, family_rules)
    family_types = _dedupe_strings(_coerce_mapping(entry).get("primary_task_type") for entry in family_rules)
    family_names = {
        str(_coerce_mapping(entry).get("key") or _coerce_mapping(entry).get("id") or "").strip()
        for entry in family_rules
        if str(_coerce_mapping(entry).get("key") or _coerce_mapping(entry).get("id") or "").strip()
    }
    hardening_hits = _term_hits(text, defaults.get("hardening_terms") or [])
    prominent_truth_hits = _section_term_hits(sections, TRUTH_CONTRACT_CONCEPT_TERMS, prominent_only=True)
    prominent_turn_truth_hits = _section_term_hits(sections, TURN_TRUTH_CONCEPT_TERMS, prominent_only=True)
    prominent_orchestration_hits = _section_term_hits(sections, ORCHESTRATION_CONCEPT_TERMS, prominent_only=True)
    prominent_hardening_hits = _section_term_hits(
        sections,
        list(SYSTEM_HARDENING_CONCEPT_TERMS) + list(defaults.get("hardening_terms") or []),
        prominent_only=True,
    )
    prominent_config_hits = _section_term_hits(
        sections,
        list(defaults.get("config_terms") or []) + list(CONFIG_CONCEPT_TERMS),
        prominent_only=True,
    )
    strong_config_hits = [hit for hit in prominent_config_hits if hit not in WEAK_CONFIG_TERMS]
    config_term_hits = _term_hits(text, defaults.get("config_terms") or [])
    strong_config_term_hits = [hit for hit in config_term_hits if hit not in WEAK_CONFIG_TERMS]
    truth_score = _weighted_term_score(sections, TRUTH_CONTRACT_CONCEPT_TERMS, prominent_only=True)
    orchestration_score = _weighted_term_score(sections, ORCHESTRATION_CONCEPT_TERMS, prominent_only=True)
    hardening_score = _weighted_term_score(
        sections,
        list(SYSTEM_HARDENING_CONCEPT_TERMS) + list(defaults.get("hardening_terms") or []),
        prominent_only=True,
    )
    config_score = _weighted_term_score(
        sections,
        list(defaults.get("config_terms") or []) + list(CONFIG_CONCEPT_TERMS),
        prominent_only=True,
    )
    scheduler_score = _weighted_term_score(sections, SCHEDULER_EXPLICIT_CONCEPT_TERMS, prominent_only=True)
    memory_retrieval_score = _weighted_term_score(
        sections,
        ("cross-project", "provenance", "freshness", "retrieval", "evidence ranking", "knowledge"),
        prominent_only=True,
    )
    lab_assistant_memory_active = "lab_assistant_memory" in set(active_family_names)
    lab_assistant_memory_design_precedence = bool(
        design_mode
        and lab_assistant_memory_active
        and memory_retrieval_score >= 1.4
        and any(term in text for term in ("research memory", "cross-project", "knowledge", "evidence ranking", "task register", "transcript", "ledger"))
    )
    if detect_mailbox_writeback_seam_focus_from_text(text):
        rationale.append("mailbox_writeback_seam_route")
        return "truth_contract", rationale
    if strong_config_hits and config_score >= max(1.8, hardening_score + 0.3):
        rationale.append("prominent_config_concepts=" + ",".join(prominent_config_hits[:6]))
        return "config", rationale
    if prominent_hardening_hits and hardening_score >= max(2.2, orchestration_score + 0.8, truth_score + 0.8):
        rationale.append("prominent_hardening_concepts=" + ",".join(prominent_hardening_hits[:6]))
        return "system_hardening", rationale
    if lab_assistant_memory_design_precedence:
        rationale.append("lab_assistant_memory_design_precedence")
        return "design", rationale
    if prominent_truth_hits and memory_retrieval_score < 2.2 and truth_score >= max(2.2, orchestration_score + 0.6):
        rationale.append("prominent_truth_concepts=" + ",".join(prominent_truth_hits[:6]))
        return "truth_contract", rationale
    if prominent_turn_truth_hits and truth_score >= max(1.8, orchestration_score):
        rationale.append("prominent_turn_truth_concepts=" + ",".join(prominent_turn_truth_hits[:6]))
        return "truth_contract", rationale
    if scheduler_score >= 1.8 and {"audit_evidence", "execution_lifecycle"}.intersection(set(active_family_names)):
        rationale.append("scheduler_operational_concepts")
        return "system_hardening", rationale
    strong_orchestration_hits = [hit for hit in prominent_orchestration_hits if hit not in {"resume", "restart"}]
    if (
        prominent_orchestration_hits
        and scheduler_score < 1.8
        and orchestration_score >= max(2.2, truth_score + 0.3)
        and (strong_orchestration_hits or len(prominent_orchestration_hits) >= 2)
    ):
        rationale.append("prominent_orchestration_concepts=" + ",".join(prominent_orchestration_hits[:6]))
        return "orchestration_checkpointing", rationale
    lab_assistant_route = any(term in text for term in LAB_ASSISTANT_ROUTE_SIGNAL_TERMS)
    lab_assistant_force_execution_route = any(term in text for term in LAB_ASSISTANT_FORCE_EXECUTION_ROUTE_TERMS)
    if lab_assistant_route and (not design_mode or lab_assistant_force_execution_route):
        truth_route_score = max(
            _weighted_term_score(sections, LAB_ASSISTANT_TRUTH_ROUTE_TERMS, prominent_only=True),
            truth_score,
        )
        bootstrap_route_score = _weighted_term_score(sections, LAB_ASSISTANT_BOOTSTRAP_ROUTE_TERMS, prominent_only=True)
        projection_route_score = _weighted_term_score(sections, LAB_ASSISTANT_PROJECTION_ROUTE_TERMS, prominent_only=True)
        if truth_route_score >= max(2.0, bootstrap_route_score + 0.4):
            rationale.append("lab_assistant_truth_route")
            return "truth_contract", rationale
        if bootstrap_route_score >= max(2.0, truth_route_score + 0.2):
            rationale.append("lab_assistant_bootstrap_route")
            return "orchestration_checkpointing", rationale
        if projection_route_score >= 1.6:
            rationale.append("lab_assistant_projection_route")
            return "ui_truth_projection", rationale
    if "truth_contract" in family_types and truth_contract_hits:
        rationale.append("family_rules_imply_truth_contract")
        rationale.append("truth_contract_terms_detected=" + ",".join(truth_contract_hits[:6]))
        return "truth_contract", rationale
    if "truth_contract" in family_types:
        rationale.append("family_rules_imply_truth_contract")
        return "truth_contract", rationale
    for routed_type in ("orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract"):
        if routed_type in family_types:
            rationale.append(f"family_rules_imply_{routed_type}")
            return routed_type, rationale
    if design_mode:
        rationale.append("design_intent_detected")
        return "design", rationale
    if "system_hardening" in family_types and (hardening_hits or any(kind in family_types for kind in ["system_hardening"])):
        if hardening_hits:
            rationale.append("hardening_terms_detected")
        rationale.append("family_rules_imply_system_hardening")
        return "system_hardening", rationale
    if strong_config_term_hits:
        rationale.append("config_terms_detected")
        return "config", rationale
    strong_signal_sections = [section for section in sections if section[0] in {"title", "objective"}]
    if _section_term_hits(strong_signal_sections, defaults.get("test_terms") or [], prominent_only=False):
        if _has_explicit_non_test_file_hint(strong_signal_sections):
            rationale.append("test_terms_demoted_by_explicit_non_test_file_hint")
        else:
            rationale.append("test_terms_detected")
            return "test_fix", rationale
    if hardening_hits:
        rationale.append("hardening_terms_detected")
        if "system_hardening" in family_types:
            return "system_hardening", rationale
        if "refactor" in family_types:
            return "refactor", rationale
        return str(defaults.get("primary_task_type") or "code_modification"), rationale
    if _term_hits(text, defaults.get("bugfix_terms") or []) and not _term_hits(text, defaults.get("feature_terms") or []):
        rationale.append("bugfix_terms_detected")
        return "bugfix", rationale
    if "system_hardening" in family_types:
        rationale.append("family_rules_imply_system_hardening")
        return "system_hardening", rationale
    if "refactor" in family_types and not _term_hits(text, defaults.get("feature_terms") or []):
        rationale.append("family_rules_imply_refactor")
        return "refactor", rationale
    if _term_hits(text, defaults.get("feature_terms") or []):
        rationale.append("feature_terms_detected")
        return "feature", rationale
    if _term_hits(text, defaults.get("refactor_terms") or []):
        rationale.append("refactor_terms_detected")
        return "refactor", rationale
    return str(defaults.get("primary_task_type") or "code_modification"), rationale


def _secondary_tags(active_families: Sequence[str], family_rules: Sequence[Mapping[str, Any]], *, design_mode: bool, replace_spec: Mapping[str, Any], primary_task_type: str) -> Tuple[List[str], List[str]]:
    tags: List[str] = list(active_families)
    aliases: List[str] = []
    for entry in family_rules:
        rule = _coerce_mapping(entry)
        tags.extend(_coerce_list(rule.get("secondary_tags")))
        aliases.extend(_coerce_list(rule.get("legacy_aliases")))
    if design_mode:
        tags.append("design_intent")
    if replace_spec:
        tags.append("global_replace")
    if primary_task_type == "system_hardening":
        tags.append("system_hardening")
    return _dedupe_strings(tags), _dedupe_strings(aliases)


def _expected_edit_scope(family_rules: Sequence[Mapping[str, Any]], *, design_mode: bool, replace_spec: Mapping[str, Any], changed_hints: Sequence[Any]) -> str:
    if replace_spec:
        return "multi_module"
    if design_mode:
        return "greenfield"
    scope = "single_file" if len(list(changed_hints)) <= 1 else "few_files"
    for entry in family_rules:
        candidate = str(_coerce_mapping(entry).get("expected_edit_scope") or "").strip() or scope
        if candidate == "multi_module":
            scope = "multi_module"
        elif candidate == "few_files" and scope == "single_file":
            scope = "few_files"
    return scope


def _classify_risk(family_rules: Sequence[Mapping[str, Any]], *, design_mode: bool, replace_spec: Mapping[str, Any], text: str, rules: Mapping[str, Any]) -> str:
    risk = "medium" if design_mode else "low"
    defaults = _coerce_mapping(rules.get("defaults"))
    if _term_hits(text, defaults.get("hardening_terms") or []):
        risk = _max_risk(risk, "medium")
    if replace_spec:
        risk = _max_risk(risk, "medium")
    for entry in family_rules:
        risk = _max_risk(risk, str(_coerce_mapping(entry).get("risk_level") or "low"))
    if any(term in text for term in ["security", "auth", "token", "quorum", "consensus", "fencing"]):
        risk = _max_risk(risk, "high")
    return risk


def _confidence_and_ambiguity(*, active_families: Sequence[str], intake: Mapping[str, Any], design_mode: bool, design_terms: Sequence[str], used_fallback: bool, explicit_hint: str, retrieval_bias: Mapping[str, Any]) -> Tuple[float, float, List[str]]:
    non_generic = [family for family in active_families if family != "generic"]
    intake_confidence = _coerce_mapping(intake.get("confidence"))
    intake_score = float(intake_confidence.get("score") or 0.0) if intake_confidence.get("computed") else 0.0
    notes: List[str] = []
    score = 0.34
    if non_generic:
        score += 0.18
    if len(non_generic) >= 2:
        score += 0.08
    if design_mode and design_terms:
        score += 0.14
        notes.append("explicit_design_intent")
    if explicit_hint in EXPLICIT_HINT_TYPES:
        score += 0.08
        notes.append(f"task_type_hint={explicit_hint}")
    if used_fallback:
        score -= 0.08
        notes.append("fallback_family_detection_used")
    if active_families == ["generic"]:
        score -= 0.12
        notes.append("generic_family_only")
    if len(non_generic) >= 4:
        score -= 0.06
        notes.append("broad_family_mix")
    if not retrieval_bias.get("include"):
        score -= 0.08
    score += min(0.14, intake_score * 0.2)
    score = max(0.22, min(0.93, round(score, 2)))

    ambiguity = 0.62
    if non_generic:
        ambiguity -= 0.18
    if len(non_generic) == 1:
        ambiguity -= 0.07
    if design_mode and design_terms:
        ambiguity -= 0.10
    if explicit_hint in EXPLICIT_HINT_TYPES:
        ambiguity -= 0.06
    if used_fallback:
        ambiguity += 0.07
    if active_families == ["generic"]:
        ambiguity += 0.08
    if len(non_generic) >= 3:
        ambiguity += 0.10
    ambiguity = max(0.12, min(0.82, round(ambiguity, 2)))
    return score, ambiguity, _dedupe_strings(notes)


def build_task_profile(job_contract: Mapping[str, Any], *, intake_assessment: Optional[Mapping[str, Any]] = None, repo_root: str | Path = ".", config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    intake = ensure_intake_assessment(intake_assessment)
    assets, asset_diagnostics = _load_phase2_assets(repo_root, config)
    missing_required = _coerce_mapping(asset_diagnostics.get("missing_required_details"))
    if missing_required:
        from .core import PipelineError

        raise PipelineError(
            "knowledge_assets_unavailable",
            "Required Phase 2 knowledge assets could not be loaded",
            details={
                "resolved_repo_root": asset_diagnostics.get("resolved_repo_root"),
                "missing_required": list(missing_required.keys()),
                "asset_diagnostics": asset_diagnostics,
            },
            exit_code=3,
        )
    rules = _coerce_mapping(assets.get("task_profile_rules"))
    family_rules_map = _normalized_family_map(rules)
    defaults = _coerce_mapping(rules.get("defaults"))
    cross_cutting_asset = _coerce_mapping(assets.get("cross_cutting_flags"))
    text_sections = _prompt_sections(job_contract)
    text = _combined_text(text_sections)
    stage1_families = _dedupe_strings(intake.get("task_families") or [])
    used_fallback = False
    family_scores: Dict[str, float] = {}
    if stage1_families:
        active_families = list(stage1_families)
        family_source = "intake_assessment"
    else:
        active_families, family_scores = _fallback_detect_families(job_contract, assets.get("task_families") or {})
        family_source = "raw_text_fallback"
        used_fallback = True
    design_mode, design_terms, strong_design_signal = _design_signal(text, rules, text_sections)
    execution_sheet_signal = any(
        phrase in text
        for phrase in [
            "expected files touched",
            "step-by-step edits",
            "acceptance tests",
            "rollback plan",
            "narrow validation expectations",
            "patch instruction sheet",
            "implementation plan",
            "regression suite",
            "child decomposition",
            "manual + targeted automated commands",
        ]
    )
    if design_mode and execution_sheet_signal and not strong_design_signal and "documentation_contracts" not in active_families:
        design_mode = False
        design_terms = [term for term in design_terms if term not in {"proposal", "study"}]
    replace_spec = _coerce_mapping(job_contract.get("replace_spec") or {})
    explicit_hint = str(job_contract.get("task_type_hint") or "unknown").strip().lower()
    family_rules = [_coerce_mapping(family_rules_map.get(name)) for name in active_families if _coerce_mapping(family_rules_map.get(name))]
    primary_task_type, type_notes = _classify_primary_type(
        text,
        rules,
        family_rules,
        active_family_names=active_families,
        sections=text_sections,
        design_mode=design_mode,
        explicit_hint=explicit_hint,
    )
    active_families, family_resolution_notes, family_scores = _resolve_active_families(
        active_families or ["generic"],
        text=text,
        task_families_asset=assets.get("task_families") or {},
        primary_task_type=primary_task_type,
    )
    if design_mode and primary_task_type in ROUTED_PRIMARY_TYPES:
        design_mode = False
        design_terms = [term for term in design_terms if term not in {"proposal", "study"}]
        type_notes.append("routed_execution_family_overrode_design_mode")
    task_mode = "design" if design_mode else ("global_replace" if replace_spec else str(defaults.get("task_mode") or "targeted_change"))
    risk_level = _classify_risk(family_rules, design_mode=design_mode, replace_spec=replace_spec, text=text, rules=rules)
    expected_edit_scope = _expected_edit_scope(family_rules, design_mode=design_mode, replace_spec=replace_spec, changed_hints=_coerce_list(_coerce_mapping(job_contract.get("issue_context")).get("changed_files_hint")))
    active_families = _compact_routed_families(active_families, primary_task_type)
    family_rules = [_coerce_mapping(family_rules_map.get(name)) for name in active_families if _coerce_mapping(family_rules_map.get(name))]
    design_rule = _coerce_mapping(rules.get("design_mode")) if design_mode else None
    retrieval_bias = _merge_bias(defaults, family_rules, design_rule=design_rule, active_families=active_families)
    if primary_task_type == "retrieval_ranking_contract":
        retrieval_bias["include"] = _dedupe_strings([item for item in retrieval_bias["include"] if item not in {"ledger_evidence", "truth_ownership", "execution_reconciliation", "task_register", "turn_transcripts", "writeback_projection", "recovery_mechanisms", "recovery", "state_persistence", "state_management", "checkpoint_resume"}] + ["intake_and_profile", "knowledge_retrieval", "policy_assets", "tests", "validation"])
        retrieval_bias["exclude"] = _dedupe_strings(list(retrieval_bias["exclude"]) + ["operator_entrypoints", "scheduler_core", "ui_shell_and_mobile", "runtime", "explicit_non_goal_surfaces"])
    if primary_task_type == "config":
        retrieval_bias["include"] = _dedupe_strings(list(retrieval_bias["include"]) + ["config_surfaces", "validation"])
    if primary_task_type == "test_fix":
        retrieval_bias["include"] = _dedupe_strings(list(retrieval_bias["include"]) + ["tests", "validation"])
    if primary_task_type == "truth_contract":
        retrieval_bias["include"] = _dedupe_strings(list(retrieval_bias["include"]) + ["tests", "validation"])
    if primary_task_type in {"orchestration_checkpointing", "ui_truth_projection", "retrieval_ranking_contract"}:
        retrieval_bias["include"] = _dedupe_strings(list(retrieval_bias["include"]) + ["tests", "validation"])
    if primary_task_type == "feature" and not design_mode:
        retrieval_bias["include"] = _dedupe_strings(list(retrieval_bias["include"]) + ["implementation_surfaces", "tests"])

    mailbox_writeback_seam_focus = detect_mailbox_writeback_seam_focus_from_text(text)
    if mailbox_writeback_seam_focus:
        retrieval_bias["include"] = _dedupe_strings(
            list(retrieval_bias["include"])
            + [
                "writeback_projection",
                "lab_assistant_delegation",
                "worker_runtime",
                "queue_dispatch",
                "lease_claims",
                "audit_receipts",
                "tests",
                "validation",
            ]
        )

    cross_cutting_flags = _derive_cross_cutting_flags(active_families, family_rules, cross_cutting_asset, job_contract, intake, design_mode=design_mode, design_rule=design_rule)
    secondary_tags, compatibility_aliases = _secondary_tags(active_families, family_rules, design_mode=design_mode, replace_spec=replace_spec, primary_task_type=primary_task_type)
    if mailbox_writeback_seam_focus:
        secondary_tags = _dedupe_strings(list(secondary_tags) + ["mailbox_writeback_seam"])
    task_families = _dedupe_strings(list(active_families) + list(compatibility_aliases))

    classifier_confidence, ambiguity_score, confidence_notes = _confidence_and_ambiguity(active_families=active_families, intake=intake, design_mode=design_mode, design_terms=design_terms, used_fallback=used_fallback, explicit_hint=explicit_hint, retrieval_bias=retrieval_bias)
    rationale = [
        f"family_source={family_source}",
        "active_task_families=" + ",".join(task_families),
        f"primary_task_type={primary_task_type}",
        f"task_mode={task_mode}",
        f"risk_level={risk_level}",
        f"expected_edit_scope={expected_edit_scope}",
        "retrieval_bias.include=" + ",".join(retrieval_bias["include"]),
        "retrieval_bias.exclude=" + ",".join(retrieval_bias["exclude"]),
        "cross_cutting_flags=" + ",".join(cross_cutting_flags),
        f"classifier_confidence={classifier_confidence}",
        f"ambiguity_score={ambiguity_score}",
    ]
    if family_scores:
        rationale.append("fallback_family_scores=" + ",".join(f"{name}:{score:.2f}" for name, score in sorted(family_scores.items(), key=lambda item: (-item[1], item[0]))))
    rationale.extend(family_resolution_notes)
    if compatibility_aliases:
        rationale.append("compatibility_family_aliases=" + ",".join(compatibility_aliases))
    if primary_task_type == "retrieval_ranking_contract":
        rationale.append("retrieval_ranking_compacted_families=" + ",".join(active_families))
    if design_terms:
        rationale.append("design_terms=" + ",".join(design_terms))
    if strong_design_signal:
        rationale.append("strong_design_signal=true")
    rationale.extend(type_notes)
    rationale.extend(confidence_notes)

    task_archetype = infer_task_archetype(job_contract, {
        "primary_task_type": primary_task_type,
        "task_mode": task_mode,
        "task_families": task_families,
        "secondary_tags": secondary_tags,
    }, intake_assessment=intake)
    planning_profiles = derive_planning_profiles(task_archetype)
    rationale.append(f"task_archetype={task_archetype}")
    rationale.append("planning_profiles=" + ",".join([
        f"retrieval:{planning_profiles['retrieval_profile']}",
        f"ranking:{planning_profiles['ranking_profile']}",
        f"bundle:{planning_profiles['bundle_profile']}",
        f"output:{planning_profiles['output_profile']}",
    ]))

    task_profile = {
        "primary_task_type": primary_task_type,
        "task_mode": task_mode,
        "task_archetype": task_archetype,
        "planning_profiles": planning_profiles,
        "output_contract_mode": planning_profiles.get("output_contract_mode"),
        "replace_spec": replace_spec or None,
        "secondary_tags": _dedupe_strings(secondary_tags),
        "task_families": task_families,
        "risk_level": risk_level,
        "expected_edit_scope": expected_edit_scope,
        "retrieval_bias": retrieval_bias,
        "cross_cutting_flags": cross_cutting_flags,
        "classifier_confidence": classifier_confidence,
        "ambiguity_score": ambiguity_score,
        "rationale": _dedupe_strings(rationale),
        "model_assisted": False,
        "knowledge_activation": asset_diagnostics,
    }
    return ensure_task_profile_foundation(task_profile)


def task_profile_confidence(task_profile: Mapping[str, Any]) -> Dict[str, Any]:
    score = float(task_profile.get("classifier_confidence") or 0.0) if task_profile.get("classifier_confidence") is not None else None
    notes = _coerce_list(task_profile.get("rationale"))
    return confidence_payload(stage="script_2_task_classifier", score=score, source="deterministic_task_profile_rules", notes=[str(item) for item in notes[:8]])
