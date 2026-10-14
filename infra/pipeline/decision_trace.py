from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


def _clean_text(value: Any, *, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)].rstrip() + "..."


def trace_entry(*, kind: str, summary: Any, evidence: Optional[Iterable[Any]] = None, confidence: Any = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "kind": str(kind or "decision"),
        "summary": _clean_text(summary),
    }
    items = [_clean_text(item) for item in list(evidence or []) if _clean_text(item)]
    if items:
        entry["evidence"] = items[:6]
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        entry["confidence"] = round(float(confidence), 4)
    elif isinstance(confidence, Mapping) and isinstance(confidence.get("score"), (int, float)):
        entry["confidence"] = round(float(confidence.get("score") or 0.0), 4)
    return entry


def make_stage_decision_trace(*, stage: str, entries: Iterable[Mapping[str, Any]], notes: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
    trace: List[Dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, Mapping):
            continue
        summary = _clean_text(raw.get("summary"))
        if not summary:
            continue
        trace.append(
            {
                "stage": str(stage or ""),
                **trace_entry(
                    kind=str(raw.get("kind") or "decision"),
                    summary=summary,
                    evidence=list(raw.get("evidence") or []),
                    confidence=raw.get("confidence"),
                ),
            }
        )
    for note in list(notes or []):
        txt = _clean_text(note)
        if txt:
            trace.append({"stage": str(stage or ""), **trace_entry(kind="note", summary=txt)})
    return trace


def build_task_classifier_decision_trace(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    task_profile = dict(result.get("task_profile") or {})
    profiles = dict(task_profile.get("planning_profiles") or {})
    entries = [
        {
            "kind": "task_mode",
            "summary": f"classified as {task_profile.get('task_archetype') or task_profile.get('primary_task_type') or 'unknown'}",
            "evidence": list(task_profile.get("rationale") or [])[:3],
            "confidence": task_profile.get("confidence"),
        },
        {
            "kind": "profile_selection",
            "summary": f"selected retrieval={profiles.get('retrieval_profile') or 'default'}, ranking={profiles.get('ranking_profile') or 'default'}, bundle={profiles.get('bundle_profile') or 'default'}",
            "evidence": list(task_profile.get("task_families") or [])[:4],
            "confidence": task_profile.get("confidence"),
        },
    ]
    return make_stage_decision_trace(stage="script_2_task_classifier", entries=entries)


def build_search_plan_decision_trace(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    search_plan = dict(result.get("search_plan") or {})
    lexical = list(search_plan.get("lexical_queries") or [])
    conceptual = list(search_plan.get("conceptual_queries") or [])
    negatives = list(search_plan.get("negative_targets") or [])
    entries = [
        {
            "kind": "query_strategy",
            "summary": f"planned {len(lexical)} lexical and {len(conceptual)} conceptual queries",
            "evidence": lexical[:2] + conceptual[:2],
            "confidence": search_plan.get("confidence"),
        },
        {
            "kind": "scope_constraints",
            "summary": f"recorded {len(negatives)} negative targets and {len(list(search_plan.get('subsystem_hypotheses') or []))} subsystem hypotheses",
            "evidence": negatives[:3],
            "confidence": search_plan.get("confidence"),
        },
    ]
    return make_stage_decision_trace(stage="script_3_search_plan", entries=entries)


def build_context_ranker_decision_trace(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    ranked_context = dict(result.get("ranked_context") or {})
    selected = list(ranked_context.get("selected_files") or [])
    implementation_targets = list(ranked_context.get("implementation_targets") or [])
    validation_targets = list(ranked_context.get("validation_targets") or [])
    reference_context = list(ranked_context.get("reference_context") or [])
    lane_views = dict(ranked_context.get("lane_views") or {})
    diagnostics = dict(ranked_context.get("rank_diagnostics") or {})
    entries = [
        {
            "kind": "selection_outcome",
            "summary": f"selected {len(selected)} files with {len(implementation_targets)} implementation targets, {len(validation_targets)} validation targets, and {len(reference_context)} reference files",
            "evidence": [str(item.get("path") or "") for item in selected[:4]],
            "confidence": ranked_context.get("confidence"),
        },
        {
            "kind": "lane_summary",
            "summary": f"lane counts target={len(list(lane_views.get('target') or []))}, validation={len(list(lane_views.get('validation') or []))}, support={len(list(lane_views.get('support') or []))}, reference={len(list(lane_views.get('reference') or []))}, witness={len(list(lane_views.get('witness') or []))}, passive={len(list(lane_views.get('passive') or []))}, suppressed={len(list(lane_views.get('suppressed') or []))}",
            "evidence": [f"target_purity={diagnostics.get('target_lane_purity')}", f"suppressed_topk={diagnostics.get('suppressed_topk_count')}", f"hub_topk={diagnostics.get('hotspot_topk_count')}"] ,
            "confidence": ranked_context.get("confidence"),
        },
    ]
    if selected:
        first = dict(selected[0])
        entries.append(
            {
                "kind": "top_candidate",
                "summary": f"top candidate {first.get('path') or 'unknown'} assigned to lane {first.get('primary_lane') or first.get('bundle_role') or first.get('role') or 'selected'}",
                "evidence": list(first.get("suppression_decisions") or [])[:2] + list(first.get("major_positive_signals") or [])[:2],
                "confidence": first.get("confidence") or ranked_context.get("confidence"),
            }
        )
    suppressed = list(lane_views.get("suppressed") or [])
    if suppressed:
        entries.append(
            {
                "kind": "suppression_summary",
                "summary": f"suppressed {len(suppressed)} candidates by hard caps or explicit demotion rules",
                "evidence": [f"{item.get('path') or ''}: {', '.join(list(item.get('suppression_decisions') or [])[:2])}" for item in suppressed[:3]],
                "confidence": ranked_context.get("confidence"),
            }
        )
    return make_stage_decision_trace(stage="script_5_context_ranker", entries=entries)



def build_context_bundle_decision_trace(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    context_bundle = dict(result.get("context_bundle") or {})
    files = list(context_bundle.get("files") or [])
    validation_targets = list(context_bundle.get("validation_targets") or [])
    summary = dict(context_bundle.get("bundle_summary") or {})
    entries = [
        {
            "kind": "bundle_outcome",
            "summary": f"assembled {len(files)} bundle files with quality {context_bundle.get('bundle_quality_status') or 'normal'}",
            "evidence": [str(item.get("path") or "") for item in files[:4]],
            "confidence": context_bundle.get("confidence"),
        },
        {
            "kind": "bundle_roles",
            "summary": f"owner_present={bool(summary.get('owner_present'))}, validation_targets={len(validation_targets)}, support={summary.get('support_count') or 0}, reference={summary.get('reference_count') or 0}, witness={summary.get('witness_count') or 0}, fallback_hub={summary.get('fallback_hub_count') or 0}",
            "evidence": list(summary.get("anti_collapse_actions_applied") or [])[:3] + list(summary.get("bundle_recovery_hints") or [])[:2],
            "confidence": context_bundle.get("confidence"),
        },
    ]
    return make_stage_decision_trace(stage="script_6_context_bundle", entries=entries)


def build_patch_plan_decision_trace(result: Mapping[str, Any], *, execution_contract: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    patch_plan = dict(result.get("patch_plan") or {})
    execution = dict(execution_contract.get("execution_contract") or {}) if isinstance(execution_contract, Mapping) else {}
    entries = [
        {
            "kind": "patch_mode",
            "summary": f"produced output contract {patch_plan.get('output_contract_mode') or execution.get('output_contract_mode') or 'patch_contract'} with {len(list(patch_plan.get('modify_files') or []))} modify files",
            "evidence": [str(item.get("path") or "") for item in list(patch_plan.get("modify_files") or [])[:4]],
            "confidence": patch_plan.get("planning_confidence"),
        }
    ]
    if execution:
        entries.append(
            {
                "kind": "execution_readiness",
                "summary": f"execution mode {execution.get('execution_mode') or 'review_only'}; generation_applicable={bool(execution.get('generation_applicable'))}",
                "evidence": list(execution.get("allowed_edit_files") or [])[:4] + list(execution.get("expected_new_files") or [])[:2],
                "confidence": patch_plan.get("planning_confidence"),
            }
        )
    notes = list(patch_plan.get("unresolved_uncertainties") or [])[:3]
    return make_stage_decision_trace(stage="script_7_patch_plan", entries=entries, notes=notes)
