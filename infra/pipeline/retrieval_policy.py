from __future__ import annotations

from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set

HUB_FAMILY_MAP = {
    "pipeline_planner_hub": "broad_planner",
    "pipeline_stage_hub": "broad_planner",
    "scheduler_hub": "generic_control_plane_hub",
    "webui_router_hub": "route_server_entry",
    "webui_orchestrator_hub": "broad_orchestrator",
    "benchmark_harness": "benchmark_reporting_surface",
}

PASSIVE_ROLE_SET = {"projection", "read_only_route", "local_contract_doc", "hub"}
NON_TARGET_CANDIDATE_ROLES = {"documentation", "governance_doc", "temporary_artifact"}
MUTATION_EVIDENCE_COVERS = {
    "state_mutation",
    "validation_path",
    "recovery_responsibility",
    "truth_boundary",
    "reconciliation_path",
    "task_register_flow",
    "transcript_flow",
    "ledger_evidence",
    "memory_retrieval",
    "writeback_boundary",
    "retrieval_owner",
    "dispatch_path",
    "ownership_control",
}
HUB_RESCUE_COVERS = {
    "validation_path",
    "dispatch_path",
    "recovery_responsibility",
    "reconciliation_path",
    "ownership_control",
    "truth_boundary",
    "retrieval_owner",
}


def _dedupe(items: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_task_archetype(task_archetype: str) -> str:
    text = str(task_archetype or "implementation_change").strip().lower()
    mapping = {
        "design_spec": "design_new_surface",
        "design/spec": "design_new_surface",
        "design-spec": "design_new_surface",
        "repo assessment": "repo_assessment",
        "information retrieval": "information_retrieval",
        "documentation contract": "documentation_contract",
        "narrow_ui_config": "implementation_change",
    }
    return mapping.get(text, text or "implementation_change")


def infer_hub_family(path: str, hub_kind: str, candidate_role: str) -> str:
    p = (path or "").lower()
    hub = str(hub_kind or "").strip()
    if hub:
        return HUB_FAMILY_MAP.get(hub, "generic_control_plane_hub")
    if "/planning.py" in p or p.endswith("/planning.py"):
        return "broad_planner"
    if "/search_plan.py" in p or "/context_ranker.py" in p or "/repo_retriever.py" in p or "/context_bundle.py" in p:
        return "broad_planner"
    if p.endswith("server_routes_and_main.py"):
        return "route_server_entry"
    if p.endswith("server_agents_orchestrator.py"):
        return "broad_orchestrator"
    if p.endswith("scheduled_jobs.py"):
        return "generic_control_plane_hub"
    if "benchmark" in p or "selection_manifest" in p:
        return "benchmark_reporting_surface"
    if candidate_role == "test_file" and ("integration" in p or "e2e" in p or "end_to_end" in p):
        return "broad_integration_test"
    return ""


def _is_scheduler_cross_cutting_surface(path: str) -> bool:
    p = str(path or "").lower()
    return bool(
        p.startswith("infra/bin/scheduler_")
        or p.startswith("infra/controlplane/scheduler_")
        or p.endswith("scheduled_jobs.py")
        or any(token in p for token in ["scheduler_watchdog", "scheduler_pipeline_healer", "scheduler_recovery_assistant"])
    )


def _is_memory_contract_cross_cutting_surface(path: str, covers: Sequence[str]) -> bool:
    p = str(path or "").lower()
    cover_set = {str(item or "").strip().lower() for item in covers if str(item or "").strip()}
    if cover_set.intersection(
        {
            "truth_boundary",
            "reconciliation_path",
            "task_register_flow",
            "transcript_flow",
            "ledger_evidence",
            "writeback_boundary",
            "memory_retrieval",
            "memory_ownership",
        }
    ):
        if any(
            token in p
            for token in [
                "server_lab_assistant_",
                "/ledger/",
                "server_ledger_",
                "truth_ownership",
                "reconciliation",
                "task_register",
                "turns",
                "transcript",
                "writeback",
            ]
        ):
            return True
    return False



def infer_duplicate_family_key(path: str, hub_family: str) -> str:
    p = (path or "").lower()
    if hub_family:
        return f"hub:{hub_family}"
    if "/tests/" in p or p.startswith("tests/"):
        stem = p.split("/")[-1]
        if stem.startswith("test_"):
            stem = stem[5:]
        return f"test:{stem}"
    if "/docs/" in p or p.startswith("docs/"):
        return f"doc:{p.split('/')[-1]}"
    return f"base:{p.split('/')[-1]}"



def infer_candidate_role_guess(candidate_role: str, ownership_role: str, passive_flag: bool, hub_family: str) -> str:
    if passive_flag:
        return "passive-like"
    if ownership_role == "core_owner":
        return "owner-like"
    if ownership_role == "test_surface" or candidate_role == "test_file":
        return "test-witness-like"
    if ownership_role in {"local_contract_doc", "boundary_sibling"} or candidate_role in {"documentation", "governance_doc"}:
        return "contract/reference-like"
    if hub_family:
        return "hub-like"
    return "support-like"



def secondary_role_hints(candidate_role: str, ownership_role: str, path: str) -> List[str]:
    hints: List[str] = []
    p = (path or "").lower()
    if candidate_role in {"implementation_code", "config_file"}:
        hints.append("target")
    if candidate_role == "test_file" or ownership_role == "test_surface":
        hints.append("witness")
    if ownership_role in {"boundary_sibling", "local_contract_doc"}:
        hints.append("reference")
    if "config" in p:
        hints.append("config")
    if "validate" in p or "contract" in p or "schema" in p:
        hints.append("validation")
    if "/ui/" in p or "screen" in p or "viewmodel" in p:
        hints.append("ui")
    return _dedupe(hints)



def _candidate_covers(candidate: Mapping[str, Any]) -> Set[str]:
    return {str(item or "").strip() for item in list(candidate.get("covers") or []) if str(item or "").strip()}


def _subsystem_alignment_snapshot(entry: Mapping[str, Any]) -> Dict[str, Any]:
    candidate_subsystems = [str(item or "").strip() for item in list(entry.get("subsystems") or []) if str(item or "").strip()]
    active_subsystems = [
        str(item or "").strip()
        for item in list(entry.get("active_subsystems") or entry.get("search_plan_subsystems") or [])
        if str(item or "").strip()
    ]
    candidate_set = set(candidate_subsystems)
    active_set = set(active_subsystems)
    matched = [item for item in candidate_subsystems if item in active_set]
    primary = active_subsystems[:2]
    primary_hit = any(item in primary for item in matched)
    score = 0.0
    if matched:
        score += 0.75 + 0.2 * min(2, len(matched))
        if primary_hit:
            score += 0.35
    elif candidate_subsystems and not active_subsystems:
        score += 0.15
    return {
        "candidate_subsystems": candidate_subsystems,
        "active_subsystems": active_subsystems,
        "matched": matched,
        "primary_hit": primary_hit,
        "score": round(score, 4),
        "has_active_subsystems": bool(active_subsystems),
        "fits_active_subsystems": bool(matched),
    }



def _is_narrow_ui_or_config(entry: Mapping[str, Any]) -> bool:
    path = str(entry.get("path") or "").lower()
    hints = {str(item or "").strip().lower() for item in list(entry.get("secondary_role_hints") or []) if str(item or "").strip()}
    return bool(
        {"config", "ui"}.intersection(hints)
        or path.endswith((".yaml", ".yml", ".toml", ".json"))
        or "/ui/" in path
        or "screen" in path
        or "viewmodel" in path
    )



def generic_structural_family_tags(entry: Mapping[str, Any]) -> List[str]:
    path = str(entry.get("path") or "").lower()
    covers = _candidate_covers(entry)
    hints = {str(item or "").strip().lower() for item in list(entry.get("secondary_role_hints") or []) if str(item or "").strip()}
    subsystems = {str(item or "").strip().lower() for item in list(entry.get("subsystems") or []) if str(item or "").strip()}
    tags: List[str] = []
    if covers.intersection({"dispatch_path", "ownership_control", "recovery_responsibility"}) or str(entry.get("hub_family") or "") in {"generic_control_plane_hub", "broad_orchestrator", "route_server_entry"} or any(token in path for token in ["orchestr", "route", "server", "scheduler", "control"]):
        tags.append("orchestration_control")
    if covers.intersection({"state_mutation", "ledger_evidence"}) or subsystems.intersection({"state_persistence", "checkpoint_resume", "execution_reconciliation", "truth_registry", "task_register", "ledger_evidence", "governance_ledger"}) or any(token in path for token in ["store", "state", "checkpoint", "resume", "ledger", "register"]):
        tags.append("persistence_state")
    if hints.intersection({"ui"}) or subsystems.intersection({"ui_shell_and_mobile", "page_handlers", "ui_render_only", "frontend_ui", "visualization_ui"}) or any(token in path for token in ["/ui/", "screen", "viewmodel", "render", "mobile", "frontend"]):
        tags.append("ui_projection")
    if str(entry.get("candidate_role") or entry.get("file_role") or "") == "test_file" or str(entry.get("ownership_role") or "") == "test_surface" or hints.intersection({"validation"}) or covers.intersection({"validation_path"}) or any(token in path for token in ["test", "verify", "validation", "benchmark", "proof"]):
        tags.append("validation_benchmark")
    if hints.intersection({"config", "reference"}) or str(entry.get("ownership_role") or "") == "local_contract_doc" or any(token in path for token in ["config", "contract", "schema", "defaults", "governance"]):
        tags.append("config_boundary")
    return _dedupe(tags)



def infer_candidate_intent(entry: Mapping[str, Any], *, passive_flag: bool, hub_family: str) -> Dict[str, Any]:
    path = str(entry.get("path") or "").lower()
    candidate_role = str(entry.get("file_role") or entry.get("candidate_role") or "")
    ownership_role = str(entry.get("ownership_role") or "")
    family_owner_fit = float(entry.get("family_owner_fit") or 0.0)
    subsystem_alignment_score = float(entry.get("subsystem_alignment_score") or 0.0)
    path_specificity = float(entry.get("path_specificity_score") or 0.0)
    hints = {str(item or "").strip().lower() for item in list(entry.get("secondary_role_hints") or []) if str(item or "").strip()}

    if ownership_role == "test_surface" or candidate_role == "test_file":
        return {"candidate_intent_lane": "context_only", "context_intent_type": "witness_test"}
    if ownership_role == "local_contract_doc":
        return {"candidate_intent_lane": "context_only", "context_intent_type": "contract_reference"}
    if candidate_role in {"documentation", "governance_doc"}:
        intent = "validation_context" if "validation" in hints else "contract_reference"
        return {"candidate_intent_lane": "context_only", "context_intent_type": intent}
    if passive_flag:
        intent = "validation_context" if "validation" in hints else "broad_docs_reporting"
        return {"candidate_intent_lane": "context_only", "context_intent_type": intent}
    if candidate_role in {"implementation_code", "config_file"} and (
        ownership_role == "core_owner"
        or family_owner_fit >= 0.75
        or subsystem_alignment_score >= 0.65
        or path_specificity >= 0.95
        or "config" in hints
    ):
        return {"candidate_intent_lane": "edit_likely", "context_intent_type": ""}
    if hub_family:
        if "validation" in hints or ownership_role == "boundary_sibling":
            return {"candidate_intent_lane": "context_only", "context_intent_type": "validation_context"}
        return {"candidate_intent_lane": "context_only", "context_intent_type": "fallback_hub"}
    if ownership_role in {"boundary_sibling", "read_only_route"}:
        return {"candidate_intent_lane": "context_only", "context_intent_type": "validation_context"}
    if candidate_role in {"implementation_code", "config_file"}:
        return {"candidate_intent_lane": "context_only", "context_intent_type": "validation_context"}
    if "validation" in hints:
        return {"candidate_intent_lane": "context_only", "context_intent_type": "validation_context"}
    if any(token in path for token in ["report", "summary", "projection", "view"]):
        return {"candidate_intent_lane": "context_only", "context_intent_type": "broad_docs_reporting"}
    return {"candidate_intent_lane": "context_only", "context_intent_type": "validation_context"}



def target_actionability_snapshot(
    entry: Mapping[str, Any],
    *,
    task_archetype: str = "implementation_change",
    narrow_task: bool = False,
    policy: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    archetype = normalize_task_archetype(task_archetype)
    policy_map = dict(policy or {})
    require_subsystem_fit = bool(policy_map.get("target_requires_subsystem_fit", True))
    candidate_role = str(entry.get("candidate_role") or entry.get("file_role") or "")
    ownership_role = str(entry.get("ownership_role") or "")
    path = str(entry.get("path") or "")
    path_lower = path.lower()
    hub_family = str(entry.get("hub_family") or "")
    passive_flag = bool(entry.get("passive_surface_flag"))
    explicit_path_request = bool(entry.get("explicit_path_request"))
    candidate_intent_lane = str(entry.get("candidate_intent_lane") or ("edit_likely" if str(entry.get("candidate_role") or entry.get("file_role") or "") in {"implementation_code", "config_file"} else "context_only"))
    context_intent_type = str(entry.get("context_intent_type") or "")
    owner_signal = float(entry.get("owner_signal_strength") or 0.0)
    owner_fit = float(entry.get("family_owner_fit") or 0.0)
    support_signal = float(entry.get("support_signal_strength") or 0.0)
    path_specificity = float(entry.get("path_specificity_score") or 0.0)
    covers = _candidate_covers(entry)
    subsystem_alignment = _subsystem_alignment_snapshot(entry)
    subsystem_fit = bool(subsystem_alignment.get("fits_active_subsystems"))
    active_subsystems_present = bool(subsystem_alignment.get("has_active_subsystems"))

    owner_candidate = ownership_role == "core_owner"
    mutation_capable_role = candidate_role in {"implementation_code", "config_file"}
    mutation_evidence = bool(
        owner_candidate
        or (
            mutation_capable_role
            and (
                explicit_path_request
                or owner_signal >= 1.0
                or owner_fit >= 1.05
                or (path_specificity >= 1.0 and not hub_family)
                or bool(covers.intersection(MUTATION_EVIDENCE_COVERS))
            )
        )
    )
    validation_or_control_rescue = bool(covers.intersection(HUB_RESCUE_COVERS))
    rescued_hub = bool(
        hub_family
        and (
            explicit_path_request
            or owner_signal >= 1.25
            or owner_fit >= 1.2
            or validation_or_control_rescue
        )
    )
    non_target_role = candidate_role in NON_TARGET_CANDIDATE_ROLES or ownership_role in {"projection", "read_only_route", "local_contract_doc"}
    typed_context_only = candidate_intent_lane == "context_only"
    aligned_mutation_path = bool(
        mutation_evidence
        and (
            owner_candidate
            or explicit_path_request
            or subsystem_fit
            or not active_subsystems_present
            or not require_subsystem_fit
        )
    )
    eligible_for_target = bool(
        not passive_flag
        and not non_target_role
        and not typed_context_only
        and (
            aligned_mutation_path
            or (hub_family and rescued_hub and (subsystem_fit or explicit_path_request or not active_subsystems_present or not require_subsystem_fit))
        )
    )
    fallback_allowed = bool(
        not eligible_for_target
        and not passive_flag
        and not non_target_role
        and (
            (hub_family and (validation_or_control_rescue or support_signal >= 1.0))
            or (typed_context_only and context_intent_type in {"fallback_hub", "validation_context"} and (validation_or_control_rescue or explicit_path_request))
        )
        and (subsystem_fit or explicit_path_request or not active_subsystems_present or not require_subsystem_fit)
    )

    reasons: List[str] = []
    if owner_candidate:
        reasons.append("core owner evidence")
    if mutation_capable_role and explicit_path_request:
        reasons.append("explicit path evidence")
    if covers.intersection(MUTATION_EVIDENCE_COVERS):
        reasons.append("mutation or control-boundary coverage")
    if hub_family and rescued_hub:
        reasons.append(f"hub rescued by concrete evidence:{hub_family}")
    if hub_family and not rescued_hub:
        reasons.append(f"hub lacks target rescue:{hub_family}")
    if subsystem_fit:
        reasons.append("aligned with active subsystem hypotheses")
    elif active_subsystems_present and mutation_capable_role and not explicit_path_request and not owner_candidate:
        reasons.append("missing active subsystem alignment for target lane")
    if passive_flag:
        reasons.append("passive surface blocks target lane")
    if non_target_role:
        reasons.append("non-target candidate role")
    if typed_context_only:
        reasons.append(f"typed context candidate blocks direct target lane:{context_intent_type or 'context_only'}")
    if not eligible_for_target and mutation_capable_role and not non_target_role and not passive_flag:
        reasons.append("lacked explicit mutation evidence")

    preferred_target_cap = 2
    if archetype == "implementation_change" and (narrow_task or _is_narrow_ui_or_config(entry)):
        preferred_target_cap = 1
    elif archetype in {"design_new_surface", "documentation_contract", "information_retrieval"}:
        preferred_target_cap = 1
    elif archetype == "repo_assessment":
        preferred_target_cap = 0
    if (
        archetype == "implementation_change"
        and subsystem_fit
        and mutation_evidence
        and _is_scheduler_cross_cutting_surface(path_lower)
    ):
        preferred_target_cap = 4
    if (
        archetype == "design_new_surface"
        and subsystem_fit
        and mutation_evidence
        and not hub_family
        and _is_memory_contract_cross_cutting_surface(path_lower, covers)
    ):
        preferred_target_cap = 4

    return {
        "eligible_for_target": bool(eligible_for_target),
        "fallback_allowed": bool(fallback_allowed),
        "owner_candidate": bool(owner_candidate or mutation_evidence),
        "rescued_hub": bool(rescued_hub),
        "validation_or_control_rescue": bool(validation_or_control_rescue),
        "preferred_target_cap": int(preferred_target_cap),
        "decision_reasons": _dedupe(reasons),
        "narrow_owner_pressure": bool(archetype == "implementation_change" and (narrow_task or _is_narrow_ui_or_config(entry))),
        "target_owner_score": round(owner_signal + owner_fit + (0.75 if owner_candidate else 0.0), 4),
        "mutation_evidence": bool(mutation_evidence),
        "non_target_role": bool(non_target_role),
        "subsystem_fit": bool(subsystem_fit),
        "subsystem_alignment_score": float(subsystem_alignment.get("score") or 0.0),
        "active_subsystems_present": bool(active_subsystems_present),
        "candidate_intent_lane": candidate_intent_lane,
        "context_intent_type": context_intent_type,
        "gate_failure_reason": next((reason for reason in reasons if 'blocks target lane' in reason or 'lacked explicit mutation evidence' in reason or 'missing active subsystem alignment' in reason), ""),
        "rescue_reasons": [reason for reason in reasons if 'rescued' in reason or 'aligned with active subsystem hypotheses' in reason],
        "path": path_lower,
    }



def annotate_stage4_candidate(candidate: MutableMapping[str, Any], *, narrow_task: bool = False) -> MutableMapping[str, Any]:
    path = str(candidate.get("path") or "")
    candidate_role = str(candidate.get("file_role") or candidate.get("candidate_role") or "")
    ownership_role = str(candidate.get("ownership_role") or "")
    hub_kind = str(candidate.get("hub_kind") or "")
    passive_score = float((candidate.get("passive_relevance") or {}).get("score") or candidate.get("passive_relevance_penalty") or 0.0)
    superficial = float(candidate.get("superficial_match_penalty") or 0.0)
    family_owner_fit = float(candidate.get("family_owner_fit") or 0.0)
    targetability = float(candidate.get("targetability_score") or candidate.get("task_role_fit") or 0.0)
    subsystem_alignment = _subsystem_alignment_snapshot(candidate)
    subsystem_alignment_score = float(subsystem_alignment.get("score") or 0.0)

    hub_family = infer_hub_family(path, hub_kind, candidate_role)
    passive_flag = bool(
        ownership_role in {"projection", "read_only_route"}
        or candidate_role == "temporary_artifact"
        or (passive_score >= 0.95 and ownership_role not in {"core_owner", "test_surface", "local_contract_doc"} and family_owner_fit < 1.0)
    )
    duplicate_key = infer_duplicate_family_key(path, hub_family)
    role_guess = infer_candidate_role_guess(candidate_role, ownership_role, passive_flag, hub_family)

    owner_signal_strength = max(0.0, family_owner_fit + (1.0 if ownership_role == "core_owner" else 0.0) + subsystem_alignment_score * (0.45 if ownership_role == "core_owner" else 0.2))
    support_signal_strength = max(0.0, float(candidate.get("coverage_score") or 0.0) + (0.6 if ownership_role in {"boundary_sibling", "test_surface"} else 0.0) + subsystem_alignment_score * 0.2)
    raw_hub_penalty = (1.2 if hub_family else 0.0) + (0.6 if narrow_task and hub_family else 0.0)
    if hub_family and subsystem_alignment_score > 0 and (ownership_role == "core_owner" or family_owner_fit >= 1.0):
        raw_hub_penalty = max(0.0, raw_hub_penalty - min(0.55, subsystem_alignment_score * 0.35))
    hub_penalty = max(0.0, raw_hub_penalty)
    path_specificity = max(0.0, 1.5 - min(1.5, path.count("/") * 0.08) + (0.4 if ownership_role == "core_owner" else 0.0) + min(0.3, subsystem_alignment_score * 0.18))

    explicit_candidate_intent_lane = str(candidate.get("candidate_intent_lane") or "").strip()
    explicit_context_intent_type = str(candidate.get("context_intent_type") or "").strip()
    if explicit_candidate_intent_lane:
        intent = {
            "candidate_intent_lane": explicit_candidate_intent_lane,
            "context_intent_type": explicit_context_intent_type,
        }
    else:
        intent = infer_candidate_intent({**candidate, "secondary_role_hints": secondary_role_hints(candidate_role, ownership_role, path), "subsystem_alignment_score": subsystem_alignment_score, "path_specificity_score": max(0.0, 1.5 - min(1.5, path.count('/') * 0.08) + (0.4 if ownership_role == 'core_owner' else 0.0) + min(0.3, subsystem_alignment_score * 0.18)), "hub_family": hub_family}, passive_flag=passive_flag, hub_family=hub_family)
    reasons = _dedupe([
        *[str(x) for x in list(candidate.get("reasons") or [])[:4]],
        f"role:{role_guess}",
        f"intent:{intent.get('candidate_intent_lane') or 'context_only'}",
        f"context:{intent.get('context_intent_type')}" if intent.get('context_intent_type') else "",
        f"hub:{hub_family}" if hub_family else "",
        f"subsystems:{','.join(list(subsystem_alignment.get('matched') or [])[:2])}" if subsystem_alignment.get("matched") else "",
        "passive-surface" if passive_flag else "",
    ])

    confidence = 0.45
    if ownership_role == "core_owner":
        confidence += 0.3
    if passive_flag:
        confidence += 0.1
    if hub_family:
        confidence += 0.05
    confidence = round(max(0.0, min(1.0, confidence)), 4)

    candidate["candidate_role_guess"] = role_guess
    candidate["candidate_role_confidence"] = confidence
    candidate["hub_family"] = hub_family
    candidate["passive_surface_flag"] = bool(passive_flag)
    candidate["duplicate_family_key"] = duplicate_key
    candidate["targetability_score"] = round(targetability, 4)
    candidate["annotation_reasons"] = reasons
    candidate["owner_signal_strength"] = round(owner_signal_strength, 4)
    candidate["support_signal_strength"] = round(support_signal_strength, 4)
    candidate["hub_penalty"] = round(hub_penalty, 4)
    candidate["path_specificity_score"] = round(path_specificity, 4)
    candidate["secondary_role_hints"] = secondary_role_hints(candidate_role, ownership_role, path)
    candidate["candidate_intent_lane"] = str(intent.get("candidate_intent_lane") or "context_only")
    context_intent_type = str(intent.get("context_intent_type") or "").strip()
    candidate["context_intent_type"] = context_intent_type or None
    candidate["structural_family_tags"] = generic_structural_family_tags({**candidate, "secondary_role_hints": candidate["secondary_role_hints"], "hub_family": hub_family})
    candidate["lane_scores_seed"] = {
        "target": round(max(0.0, targetability + owner_signal_strength - hub_penalty), 4),
        "support": round(max(0.0, support_signal_strength + path_specificity * 0.25), 4),
        "reference": round(max(0.0, float(candidate.get("reference_value_score") or 0.0) + (0.4 if passive_flag else 0.0)), 4),
    }
    candidate["subsystem_alignment_score"] = round(subsystem_alignment_score, 4)
    candidate["genericity_score"] = round(min(2.0, (1.2 if hub_family else 0.0) + superficial * 0.2), 4)
    candidate["internal_subscores"] = {
        "family_owner_fit": round(family_owner_fit, 4),
        "passive_score": round(passive_score, 4),
        "superficial_penalty": round(superficial, 4),
        "subsystem_alignment": round(subsystem_alignment_score, 4),
    }
    return candidate



def enforce_actionability_policy(
    ranked_entries: Sequence[Mapping[str, Any]],
    *,
    task_archetype: str,
    narrow_task: bool,
    policy: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    archetype = normalize_task_archetype(task_archetype)
    policy_map = dict(policy or {})
    archetype_policies = dict(policy_map.get("task_archetype_specific_bundle_policies") or {})
    archetype_policy = dict(archetype_policies.get(archetype) or {})
    target_cap = int(archetype_policy.get("rank_target_cap") or archetype_policy.get("target_cap") or 0)
    if target_cap <= 0:
        target_cap = 1 if (archetype == "implementation_change" and narrow_task) else (2 if archetype == "implementation_change" else 1)
    fallback_allowance = max(0, int(policy_map.get("target_fallback_allowance") or 1))

    out: List[Dict[str, Any]] = []
    target_candidates: List[Dict[str, Any]] = []
    for raw in ranked_entries:
        item = dict(raw)
        decision = target_actionability_snapshot(item, task_archetype=archetype, narrow_task=narrow_task, policy=policy_map)
        item["actionability_decision"] = decision
        item.setdefault("why_not_other_roles", [])
        if str(item.get("selection_lane") or "") == "implementation_target":
            target_candidates.append(item)
        out.append(item)

    strong_targets = [
        item for item in target_candidates
        if bool((item.get("actionability_decision") or {}).get("eligible_for_target"))
        or str(item.get("candidate_role") or "") == "test_file"
        or str(item.get("ownership_role") or "") == "test_surface"
    ]
    fallback_targets = [
        item for item in target_candidates
        if item not in strong_targets and bool((item.get("actionability_decision") or {}).get("fallback_allowed"))
    ]
    preferred_target_cap = max(
        [int((item.get("actionability_decision") or {}).get("preferred_target_cap") or 0) for item in strong_targets],
        default=0,
    )
    if preferred_target_cap > 0:
        target_cap = max(target_cap, preferred_target_cap)

    strong_targets.sort(
        key=lambda item: (
            0 if bool((item.get("actionability_decision") or {}).get("owner_candidate")) else 1,
            0 if bool((item.get("actionability_decision") or {}).get("subsystem_fit")) else 1,
            0 if not str(item.get("hub_family") or "") else 1,
            -float(item.get("targetability_score") or item.get("score") or 0.0),
            -float(item.get("owner_signal_strength") or 0.0),
            str(item.get("path") or ""),
        )
    )
    fallback_targets.sort(
        key=lambda item: (
            0 if bool((item.get("actionability_decision") or {}).get("subsystem_fit")) else 1,
            0 if bool((item.get("actionability_decision") or {}).get("validation_or_control_rescue")) else 1,
            -float(item.get("targetability_score") or item.get("score") or 0.0),
            -float(item.get("support_signal_strength") or 0.0),
            str(item.get("path") or ""),
        )
    )

    keep_paths: Set[str] = set()
    if strong_targets:
        for item in strong_targets[:max(1, target_cap)]:
            keep_paths.add(str(item.get("path") or ""))
    elif fallback_allowance > 0:
        for item in fallback_targets[:fallback_allowance]:
            keep_paths.add(str(item.get("path") or ""))
            decision = dict(item.get("actionability_decision") or {})
            decision["fallback_target"] = True
            item["actionability_decision"] = decision

    final: List[Dict[str, Any]] = []
    for item in out:
        decision = dict(item.get("actionability_decision") or {})
        path = str(item.get("path") or "")
        if str(item.get("selection_lane") or "") == "implementation_target" and path not in keep_paths:
            reasons = list(decision.get("decision_reasons") or [])
            if not decision.get("eligible_for_target"):
                reasons.append("demoted from target lane without explicit mutation evidence")
            else:
                reasons.append("demoted by target cap to preserve actionability")
            decision["decision_reasons"] = _dedupe(reasons)
            item["selection_lane"] = "reference_context"
            if str(item.get("candidate_role") or "") != "test_file" and str(item.get("ownership_role") or "") != "test_surface":
                item["edit_allowed"] = False
            why_not = list(item.get("why_not_other_roles") or [])
            why_not.append("target lane reserved for stronger owner-like candidate")
            item["why_not_other_roles"] = _dedupe(why_not)
        elif str(item.get("selection_lane") or "") == "implementation_target" and path in keep_paths and decision.get("fallback_allowed") and not decision.get("eligible_for_target"):
            reasons = list(decision.get("decision_reasons") or [])
            reasons.append("fallback target retained because no stronger owner survived")
            decision["decision_reasons"] = _dedupe(reasons)
            decision["fallback_target"] = True
        item["actionability_decision"] = decision
        final.append(item)
    return final



def lane_metadata_for_ranked(entry: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    role_guess = str(entry.get("candidate_role_guess") or "")
    ownership_role = str(entry.get("ownership_role") or "")
    lane = str(entry.get("selection_lane") or "reference_context")
    passive_flag = bool(entry.get("passive_surface_flag"))
    hub_family = str(entry.get("hub_family") or "")
    targetability = float(entry.get("targetability_score") or entry.get("score") or 0.0)
    reference_value = float(entry.get("reference_value_score") or entry.get("score") or 0.0)
    actionability = dict(entry.get("actionability_decision") or {})
    cap_suppressed = bool(entry.get("selection_cap_suppressed"))

    primary_lane = "reference"
    if ownership_role == "test_surface" or str(entry.get("candidate_role") or "") == "test_file":
        primary_lane = "witness"
    elif passive_flag:
        primary_lane = "passive"
    elif cap_suppressed:
        primary_lane = "suppressed"
    elif lane == "implementation_target":
        primary_lane = "target"
    elif role_guess == "support-like":
        primary_lane = "support"

    if hub_family and primary_lane == "target" and ownership_role != "core_owner" and not bool(actionability.get("rescued_hub") or actionability.get("fallback_target")):
        primary_lane = "support"
    if not bool(actionability.get("eligible_for_target", lane == "implementation_target")) and primary_lane == "target" and ownership_role != "core_owner":
        primary_lane = "support" if role_guess in {"support-like", "hub-like"} else "reference"

    secondary: List[str] = []
    if primary_lane != "support" and role_guess in {"support-like", "hub-like"}:
        secondary.append("support")
    if primary_lane != "reference" and str(entry.get("candidate_role") or "") in {"documentation", "governance_doc"}:
        secondary.append("reference")
    if primary_lane != "witness" and (ownership_role == "test_surface" or str(entry.get("candidate_role") or "") == "test_file"):
        secondary.append("witness")
    if passive_flag and primary_lane != "passive":
        secondary.append("passive")
    if (hub_family or cap_suppressed) and primary_lane != "suppressed" and primary_lane != "target":
        secondary.append("suppressed")

    lane_scores = {
        "target": round(max(0.0, targetability + float(entry.get("owner_signal_strength") or 0.0) - float(entry.get("hub_penalty") or 0.0)), 4),
        "support": round(max(0.0, float(entry.get("support_signal_strength") or 0.0) + reference_value * 0.35), 4),
        "reference": round(max(0.0, reference_value + (0.3 if passive_flag else 0.0)), 4),
        "witness": round(max(0.0, reference_value + (0.75 if ownership_role == "test_surface" else 0.0)), 4),
        "passive": round(max(0.0, float((entry.get("passive_relevance") or {}).get("score") or 0.0)), 4),
        "suppressed": round(max(0.0, float(entry.get("hub_penalty") or 0.0) + (0.3 if hub_family else 0.0) + (0.5 if cap_suppressed else 0.0)), 4),
    }
    positives = _dedupe([
        *[str(x) for x in list(entry.get("reasons") or [])[:3]],
        "owner-signal" if float(entry.get("owner_signal_strength") or 0.0) > 0 else "",
        "support-signal" if float(entry.get("support_signal_strength") or 0.0) > 0 else "",
        "subsystem-alignment" if bool(actionability.get("subsystem_fit")) else "",
        "rescued-hub" if bool(actionability.get("rescued_hub")) else "",
        "fallback-target" if bool(actionability.get("fallback_target")) else "",
        f"intent:{str(entry.get('candidate_intent_lane') or '')}" if str(entry.get('candidate_intent_lane') or '') else "",
        f"context:{str(entry.get('context_intent_type') or '')}" if str(entry.get('context_intent_type') or '') else "",
    ])
    penalties = _dedupe([
        "hub-penalty" if float(entry.get("hub_penalty") or 0.0) > 0 else "",
        "passive-surface" if passive_flag else "",
        "subsystem-mismatch" if bool(actionability.get("active_subsystems_present")) and not bool(actionability.get("subsystem_fit")) and not bool(entry.get("explicit_path_request")) else "",
        "context-only" if str(entry.get("candidate_intent_lane") or "") == "context_only" else "",
        "superficial-penalty" if float(entry.get("superficial_match_penalty") or 0.0) > 0.5 else "",
        "hard-cap-suppressed" if cap_suppressed else "",
    ])
    suppression = []
    if hub_family:
        suppression.append(f"hub-family:{hub_family}")
    if passive_flag:
        suppression.append("passive-demotion")
    suppression.extend(str(item) for item in list(actionability.get("decision_reasons") or []) if str(item).strip())
    suppression.extend(str(item) for item in list(entry.get("selection_cap_reasons") or []) if str(item).strip())

    why_not: List[str] = list(entry.get("why_not_other_roles") or [])
    if primary_lane != "target" and lane == "implementation_target":
        why_not.append("target lane rejected by actionability-first policy")
    if primary_lane == "support" and hub_family:
        why_not.append("hub retained as support instead of target")
    if primary_lane == "passive":
        why_not.append("passive surface retained only for recall")

    entry["primary_lane"] = primary_lane
    entry["secondary_lanes"] = _dedupe(secondary)
    entry["lane_scores"] = lane_scores
    entry["major_positive_signals"] = positives
    entry["major_penalties"] = penalties
    entry["suppression_decisions"] = _dedupe(suppression)
    entry["why_not_other_roles"] = _dedupe(why_not)
    return entry
