from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .knowledge_assets import describe_knowledge_assets, load_knowledge_assets_with_meta, missing_required_knowledge_assets, resolve_knowledge_asset_paths
from .v2_foundation import confidence_payload, ensure_intake_assessment

PHASE1_ASSET_NAMES = [
    "task_families",
    "failure_taxonomy",
    "invariant_templates",
    "derived_requirements",
]
DEFAULT_GENERIC_FAMILY = "generic"
DEFAULT_FAMILY_LIMIT = 3
SECTION_WEIGHTS = {
    "title": 1.7,
    "objective": 1.4,
    "description": 1.0,
    "user_notes": 0.9,
    "constraints": 0.6,
    "acceptance_criteria": 0.5,
}
GENERIC_SINGLE_TOKEN_TRIGGERS = {
    "job",
    "run",
    "state",
    "status",
    "runtime",
    "task",
    "record",
    "write",
    "snapshot",
    "restart",
    "resume",
    "trigger",
    "step",
    "stage",
    "progress",
}
BROAD_SAFETY_CUES = {
    "safe",
    "safer",
    "safety",
    "harden",
    "hardening",
    "robust",
    "robustness",
    "reliable",
    "resilience",
    "resilient",
}
LOCAL_KNOWLEDGE_REPO_ROOT = Path(__file__).resolve().parents[2]


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
        if all(bool(exists.get(name)) for name in PHASE1_ASSET_NAMES):
            return candidate
    return LOCAL_KNOWLEDGE_REPO_ROOT.resolve()


def _load_phase1_assets(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    assets_repo_root = _resolve_assets_repo_root(repo_root, config)
    assets, _ = load_knowledge_assets_with_meta(assets_repo_root, config or {}, PHASE1_ASSET_NAMES)
    diagnostics = describe_knowledge_assets(assets_repo_root, config or {}, PHASE1_ASSET_NAMES)
    diagnostics["resolved_repo_root"] = str(assets_repo_root)
    missing = missing_required_knowledge_assets(assets_repo_root, config or {}, PHASE1_ASSET_NAMES)
    if missing:
        diagnostics["missing_required_details"] = missing
    return assets, diagnostics


def _prompt_sections(raw: Optional[Mapping[str, Any]], job_contract: Mapping[str, Any]) -> List[Tuple[str, str, float]]:
    raw_map = _coerce_mapping(raw)
    issue_context = _coerce_mapping(job_contract.get("issue_context"))
    sections = _dedupe_sections(
        [
            ("title", str(raw_map.get("title") or job_contract.get("title") or "")),
            ("objective", str(raw_map.get("objective") or job_contract.get("objective") or "")),
            ("description", str(raw_map.get("description") or job_contract.get("description") or "")),
            ("user_notes", str(raw_map.get("user_notes") or issue_context.get("user_notes") or "")),
            (
                "constraints",
                " ".join(str(item) for item in (_coerce_list(raw_map.get("constraints")) or _coerce_list(job_contract.get("constraints")))),
            ),
            (
                "acceptance_criteria",
                " ".join(str(item) for item in (_coerce_list(raw_map.get("acceptance_criteria")) or _coerce_list(job_contract.get("acceptance_criteria")))),
            ),
        ]
    )
    out: List[Tuple[str, str, float]] = []
    for name, text in sections:
        weight = float(SECTION_WEIGHTS.get(name, 0.5))
        if text:
            out.append((name, text, weight))
    return out


def _normalized_family_map(asset: Mapping[str, Any]) -> Dict[str, Any]:
    families = asset.get("families")
    if isinstance(families, Mapping):
        return dict(families)
    legacy = asset.get("task_families")
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return {}


def _trigger_variants(trigger: str) -> List[str]:
    raw = str(trigger or "").strip().lower()
    if not raw:
        return []
    variants = {raw}
    if re.fullmatch(r"[a-z0-9_]+", raw):
        variants.add(raw + "s")
        variants.add(raw + "es")
        variants.add(raw + "ed")
        variants.add(raw + "ing")
        if raw.endswith("e"):
            variants.add(raw[:-1] + "ing")
        if raw.endswith("y") and len(raw) > 2:
            variants.add(raw[:-1] + "ies")
    return sorted(variants)


def _variant_matches(text: str, variant: str) -> bool:
    haystack = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    needle = str(variant or "").strip().lower()
    if not haystack or not needle:
        return False
    if re.fullmatch(r"[a-z0-9_]+", needle):
        return re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack) is not None
    return needle in haystack


def _trigger_matches(text: str, trigger: str) -> bool:
    return any(_variant_matches(text, variant) for variant in _trigger_variants(trigger))


def _trigger_specificity_weight(trigger: str, *, trigger_freq: int) -> float:
    parts = [part for part in re.split(r"[\s/_-]+", str(trigger or "").strip().lower()) if part]
    if not parts:
        return 0.0
    weight = 1.0
    if len(parts) >= 2:
        weight += min(0.8, 0.2 * float(len(parts) - 1))
    else:
        token = parts[0]
        if token in GENERIC_SINGLE_TOKEN_TRIGGERS:
            weight *= 0.45
        elif len(token) <= 4:
            weight *= 0.7
    if trigger_freq > 1 and len(parts) == 1:
        weight *= 0.8
    return max(weight, 0.1)


def _section_bonus(section_names: Sequence[str], *, specificity: float) -> float:
    sections = {str(name or "") for name in section_names}
    bonus = 0.0
    if "title" in sections:
        bonus += 0.45 * specificity
    elif "objective" in sections:
        bonus += 0.25 * specificity
    if "description" in sections and len(sections) >= 2:
        bonus += 0.1 * specificity
    return bonus


def _trigger_frequency_map(task_family_asset: Mapping[str, Any]) -> Dict[str, int]:
    families = _normalized_family_map(task_family_asset)
    counts: Dict[str, int] = {}
    for family_name, family_data in families.items():
        family_cfg = _coerce_mapping(family_data)
        for trigger in _dedupe_strings(family_cfg.get("triggers") or []):
            key = str(trigger).strip().lower()
            if not key:
                continue
            counts[key] = int(counts.get(key, 0)) + 1
    return counts


def _match_task_families(
    task_family_asset: Mapping[str, Any],
    sections: Sequence[Tuple[str, str, float]],
    *,
    family_limit: int = DEFAULT_FAMILY_LIMIT,
    fallback_family: str = DEFAULT_GENERIC_FAMILY,
) -> Tuple[List[str], List[Dict[str, Any]], List[str]]:
    families = _normalized_family_map(task_family_asset)
    trigger_freq = _trigger_frequency_map(task_family_asset)
    matches: List[Dict[str, Any]] = []
    combined_text = " ".join(text for _, text, _ in sections).lower()

    for family_name, family_data in families.items():
        cfg = _coerce_mapping(family_data)
        if family_name == fallback_family or bool(cfg.get("fallback_only")):
            continue
        trigger_hits: List[str] = []
        matched_sections: List[str] = []
        score = 0.0
        for trigger in _dedupe_strings(cfg.get("triggers") or []):
            section_hits: List[Tuple[str, float]] = []
            for section_name, text, weight in sections:
                if _trigger_matches(text, trigger):
                    section_hits.append((section_name, weight))
            if not section_hits:
                continue
            trigger_hits.append(trigger)
            matched_sections.extend(section_name for section_name, _ in section_hits)
            best_weight = max(weight for _, weight in section_hits)
            frequency = int(max(1, trigger_freq.get(str(trigger).strip().lower(), 1)))
            specificity = _trigger_specificity_weight(trigger, trigger_freq=frequency)
            section_names = [section_name for section_name, _ in section_hits]
            score += (best_weight * specificity) / float(frequency)
            score += _section_bonus(section_names, specificity=specificity)
        if not trigger_hits:
            continue
        match = {
            "family": family_name,
            "matched_triggers": _dedupe_strings(trigger_hits),
            "matched_sections": _dedupe_strings(matched_sections),
            "score": round(score, 4),
        }
        matches.append(match)

    matches.sort(key=lambda item: (-float(item.get("score") or 0.0), -len(item.get("matched_triggers") or []), str(item.get("family") or "")))
    activated: List[str] = []
    for match in matches:
        if len(activated) >= max(1, int(family_limit or DEFAULT_FAMILY_LIMIT)):
            break
        if float(match.get("score") or 0.0) < 1.0:
            continue
        activated.append(str(match.get("family") or ""))

    notes: List[str] = []
    if not activated:
        if fallback_family in families:
            activated = [fallback_family]
            notes.append("generic_fallback_used")
        notes.append("no_task_family_match")
        if any(cue in combined_text for cue in BROAD_SAFETY_CUES):
            notes.append("weak_prompt_signal")
    elif len(activated) == 1 and float(matches[0].get("score") or 0.0) < 1.85 and fallback_family in families:
        activated.append(fallback_family)
        notes.append("broad_fallback_used")

    return activated, matches, _dedupe_strings(notes)


def _extract_invariants(entry: Any) -> List[str]:
    if isinstance(entry, list):
        return _dedupe_strings(entry)
    mapping = _coerce_mapping(entry)
    return _dedupe_strings(mapping.get("invariants") or [])


def _merge_failure_taxonomy(
    asset: Mapping[str, Any],
    families: Sequence[str],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, List[str]]:
    deterministic_v2 = _coerce_mapping((config or {}).get("deterministic_v2"))
    failure_cfg = _coerce_mapping(deterministic_v2.get("failure_taxonomy"))
    expand_enabled = bool(failure_cfg.get("enabled", True)) if config is not None else True
    buckets = {
        "operator_failure_modes": bool(failure_cfg.get("expand_operator_modes", True)) if config is not None else True,
        "system_failure_modes": bool(failure_cfg.get("expand_system_modes", True)) if config is not None else True,
        "concurrency_failure_modes": bool(failure_cfg.get("expand_concurrency_modes", True)) if config is not None else True,
        "recovery_failure_modes": bool(failure_cfg.get("expand_recovery_modes", True)) if config is not None else True,
    }
    out: Dict[str, List[str]] = {key: [] for key in buckets}
    if not expand_enabled:
        return out
    family_map = _normalized_family_map(asset)
    for family_name in families:
        family_entry = _coerce_mapping(family_map.get(family_name))
        for bucket, bucket_enabled in buckets.items():
            if not bucket_enabled:
                continue
            out[bucket].extend(_dedupe_strings(family_entry.get(bucket) or []))
    return {key: _dedupe_strings(values) for key, values in out.items()}


def _merge_invariants(asset: Mapping[str, Any], families: Sequence[str], *, config: Optional[Mapping[str, Any]] = None) -> List[str]:
    deterministic_v2 = _coerce_mapping((config or {}).get("deterministic_v2"))
    invariants_cfg = _coerce_mapping(deterministic_v2.get("invariants"))
    enabled = bool(invariants_cfg.get("enabled", True)) if config is not None else True
    if not enabled:
        return []
    family_map = _normalized_family_map(asset)
    out: List[str] = []
    for family_name in families:
        out.extend(_extract_invariants(family_map.get(family_name)))
    return _dedupe_strings(out)


def _merge_derived_requirements(
    asset: Mapping[str, Any],
    families: Sequence[str],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, List[str]]:
    deterministic_v2 = _coerce_mapping((config or {}).get("deterministic_v2"))
    invariants_cfg = _coerce_mapping(deterministic_v2.get("invariants"))
    derive_enabled = bool(invariants_cfg.get("derive_requirements", True)) if config is not None else True
    out = {
        "safety_requirements": [],
        "observability_requirements": [],
        "recovery_guarantees": [],
    }
    if not derive_enabled:
        return out
    family_map = _normalized_family_map(asset)
    for family_name in families:
        family_entry = _coerce_mapping(family_map.get(family_name))
        for key in out:
            out[key].extend(_dedupe_strings(family_entry.get(key) or []))
    return {key: _dedupe_strings(values) for key, values in out.items()}


def _merge_existing_intake(existing: Optional[Mapping[str, Any]], expanded: Mapping[str, Any]) -> Dict[str, Any]:
    base = ensure_intake_assessment(existing)
    out = ensure_intake_assessment(expanded)
    out["task_families"] = _dedupe_strings(list(base.get("task_families") or []) + list(out.get("task_families") or []))
    base_taxonomy = _coerce_mapping(base.get("failure_taxonomy"))
    out_taxonomy = _coerce_mapping(out.get("failure_taxonomy"))
    for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]:
        out_taxonomy[key] = _dedupe_strings(list(base_taxonomy.get(key) or []) + list(out_taxonomy.get(key) or []))
    out["failure_taxonomy"] = out_taxonomy
    out["invariants"] = _dedupe_strings(list(base.get("invariants") or []) + list(out.get("invariants") or []))
    base_requirements = _coerce_mapping(base.get("derived_requirements"))
    out_requirements = _coerce_mapping(out.get("derived_requirements"))
    for key in ["safety_requirements", "observability_requirements", "recovery_guarantees"]:
        out_requirements[key] = _dedupe_strings(list(base_requirements.get(key) or []) + list(out_requirements.get(key) or []))
    out["derived_requirements"] = out_requirements
    return out


def _build_confidence(
    activated_families: Sequence[str],
    matches: Sequence[Mapping[str, Any]],
    notes: Sequence[str],
) -> Dict[str, Any]:
    non_generic = [family for family in activated_families if family != DEFAULT_GENERIC_FAMILY]
    match_by_family = {str(item.get("family") or ""): item for item in matches}
    matched_triggers = _dedupe_strings(
        trigger
        for family in non_generic
        for trigger in list(_coerce_mapping(match_by_family.get(family)).get("matched_triggers") or [])
    )
    matched_sections = _dedupe_strings(
        section
        for family in non_generic
        for section in list(_coerce_mapping(match_by_family.get(family)).get("matched_sections") or [])
    )
    strongest_score = max((float(_coerce_mapping(match_by_family.get(family)).get("score") or 0.0) for family in non_generic), default=0.0)
    title_or_objective_signal = any(section in {"title", "objective"} for section in matched_sections)
    confidence_notes = list(notes)
    if activated_families:
        confidence_notes.append("matched_task_families=" + ",".join(str(item) for item in activated_families))
    if matched_triggers:
        confidence_notes.append("matched_triggers=" + ",".join(matched_triggers))
    if non_generic and len(non_generic) == 1:
        confidence_notes.append("single_family_activation")
    if non_generic and not title_or_objective_signal:
        confidence_notes.append("supporting_signal_only")

    if not non_generic:
        source = "deterministic_generic_fallback"
        score = 0.34 if "weak_prompt_signal" in notes else 0.26
    else:
        evidence_score = min(1.0, strongest_score / 4.0)
        trigger_score = min(1.0, len(matched_triggers) / 5.0)
        family_score = min(1.0, len(non_generic) / 3.0)
        score = 0.35 + 0.25 * evidence_score + 0.18 * trigger_score + 0.10 * family_score
        if title_or_objective_signal:
            score += 0.10
        if DEFAULT_GENERIC_FAMILY in activated_families:
            score -= 0.08
        score = max(0.25, min(0.91, round(score, 2)))
        source = "deterministic_task_family_activation"

    return confidence_payload(
        stage="script_1_job_intake",
        score=score,
        source=source,
        notes=_dedupe_strings(confidence_notes),
    )


def expand_intake_assessment(
    raw: Optional[Mapping[str, Any]],
    job_contract: Mapping[str, Any],
    *,
    repo_root: str | Path,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    deterministic_v2 = _coerce_mapping((config or {}).get("deterministic_v2"))
    if config is not None and deterministic_v2 and not bool(deterministic_v2.get("enabled", True)):
        return ensure_intake_assessment(_coerce_mapping(raw).get("intake_assessment"))

    assets, asset_diagnostics = _load_phase1_assets(repo_root, config)
    missing_required = _coerce_mapping(asset_diagnostics.get("missing_required_details"))
    if missing_required:
        from .core import PipelineError

        raise PipelineError(
            "knowledge_assets_unavailable",
            "Required Phase 1 knowledge assets could not be loaded",
            details={
                "resolved_repo_root": asset_diagnostics.get("resolved_repo_root"),
                "missing_required": list(missing_required.keys()),
                "asset_diagnostics": asset_diagnostics,
            },
            exit_code=3,
        )
    sections = _prompt_sections(raw, job_contract)
    activated_families, matches, notes = _match_task_families(assets.get("task_families") or {}, sections)
    failure_taxonomy = _merge_failure_taxonomy(assets.get("failure_taxonomy") or {}, activated_families, config=config)
    invariants = _merge_invariants(assets.get("invariant_templates") or {}, activated_families, config=config)
    derived_requirements = _merge_derived_requirements(assets.get("derived_requirements") or {}, activated_families, config=config)
    expanded = {
        "task_families": list(activated_families),
        "failure_taxonomy": failure_taxonomy,
        "invariants": invariants,
        "derived_requirements": derived_requirements,
        "confidence": _build_confidence(activated_families, matches, notes),
        "knowledge_activation": asset_diagnostics,
    }
    existing = _coerce_mapping(_coerce_mapping(raw).get("intake_assessment"))
    return _merge_existing_intake(existing, expanded)
