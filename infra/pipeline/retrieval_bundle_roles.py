from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from .retrieval_policy import normalize_task_archetype

OWNER_BUNDLE_ROLES = {"primary_target", "secondary_target", "config_target", "edit_target", "integration_target"}
SUPPORT_BUNDLE_ROLES = {"support_context", "validation_context", "validation_target", "integration_context", "new_file_support_context", "boundary_context"}
REFERENCE_BUNDLE_ROLES = {"contract_context", "interface_contract_context", "reference_context", "read_only_reference", "pattern_reference_context"}
WITNESS_BUNDLE_ROLES = {"paired_test", "witness_only"}



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



def classify_bundle_role(entry: Mapping[str, Any], *, task_archetype: str, target_index: int = 0) -> str:
    archetype = normalize_task_archetype(task_archetype)
    lane = str(entry.get("primary_lane") or entry.get("selection_lane") or "reference")
    lane = {
        "implementation_target": "target",
        "reference_context": "reference",
        "validation_context": "support",
    }.get(lane, lane)
    path = str(entry.get("path") or "").lower()
    ownership_role = str(entry.get("ownership_role") or "")
    context_intent_type = str(entry.get("context_intent_type") or "")
    hints = set(str(x) for x in list(entry.get("secondary_role_hints") or []))
    if lane == "target":
        if "config" in hints or path.endswith(".yaml") or path.endswith(".toml") or path.endswith(".json"):
            return "config_target"
        if archetype == "design_new_surface" and target_index > 0:
            return "support_context"
        return "primary_target" if target_index == 0 else "secondary_target"
    if context_intent_type == "witness_test":
        return "paired_test" if archetype == "implementation_change" else "witness_only"
    if context_intent_type == "contract_reference":
        return "contract_context"
    if context_intent_type == "validation_context":
        return "validation_context"
    if context_intent_type == "fallback_hub" and str(entry.get("hub_family") or ""):
        return "fallback_hub"
    if lane == "witness":
        return "paired_test" if archetype == "implementation_change" else "witness_only"
    if lane == "reference":
        if ownership_role == "local_contract_doc" or "reference" in hints:
            return "contract_context"
        return "validation_context" if "validation" in hints else "support_context"
    if lane == "passive":
        return "witness_only" if archetype == "repo_assessment" else "support_context"
    if str(entry.get("hub_family") or ""):
        return "fallback_hub"
    return "validation_context" if ("validation" in hints or context_intent_type == "broad_docs_reporting") else "support_context"



def bundle_policy_for_task(
    task_archetype: str,
    policy_config: Optional[Mapping[str, Any]] = None,
    *,
    narrow_task: bool = False,
) -> Dict[str, Any]:
    archetype = normalize_task_archetype(task_archetype)
    task_cfg = dict((policy_config or {}).get("task_archetype_specific_bundle_policies") or {}).get(archetype) or {}
    base: Dict[str, Any] = {
        "implementation_change": {
            "target_cap": 2,
            "support_cap": 2,
            "reference_cap": 2,
            "witness_cap": 1,
            "fallback_hub_cap": 0,
            "passive_cap": 1,
            "duplicate_family_cap": 1,
            "basename_cap": 1,
            "omnibus_test_cap": 1,
            "preserve_owner": True,
            "min_bundle_files": 3,
            "min_support_if_owner": 1,
            "prefer_witness_with_owner": True,
        },
        "design_new_surface": {
            "target_cap": 1,
            "support_cap": 2,
            "reference_cap": 4,
            "witness_cap": 1,
            "fallback_hub_cap": 1,
            "passive_cap": 1,
            "duplicate_family_cap": 1,
            "basename_cap": 1,
            "omnibus_test_cap": 1,
            "preserve_owner": False,
            "min_bundle_files": 2,
            "prefer_contract_context": True,
        },
        "repo_assessment": {
            "target_cap": 0,
            "support_cap": 3,
            "reference_cap": 4,
            "witness_cap": 2,
            "fallback_hub_cap": 1,
            "passive_cap": 1,
            "duplicate_family_cap": 1,
            "basename_cap": 1,
            "omnibus_test_cap": 1,
            "preserve_owner": False,
            "min_bundle_files": 3,
        },
        "information_retrieval": {
            "target_cap": 0,
            "support_cap": 2,
            "reference_cap": 4,
            "witness_cap": 1,
            "fallback_hub_cap": 1,
            "passive_cap": 1,
            "duplicate_family_cap": 1,
            "basename_cap": 1,
            "omnibus_test_cap": 1,
            "preserve_owner": False,
            "min_bundle_files": 2,
        },
        "documentation_contract": {
            "target_cap": 0,
            "support_cap": 2,
            "reference_cap": 4,
            "witness_cap": 1,
            "fallback_hub_cap": 1,
            "passive_cap": 1,
            "duplicate_family_cap": 1,
            "basename_cap": 1,
            "omnibus_test_cap": 1,
            "preserve_owner": False,
            "min_bundle_files": 2,
        },
    }.get(archetype, {
        "target_cap": 1,
        "support_cap": 2,
        "reference_cap": 3,
        "witness_cap": 1,
        "fallback_hub_cap": 1,
        "passive_cap": 1,
        "duplicate_family_cap": 1,
        "basename_cap": 1,
        "omnibus_test_cap": 1,
        "preserve_owner": False,
        "min_bundle_files": 2,
        "min_support_if_owner": 0,
    })
    base.update({str(k): v for k, v in dict(task_cfg).items() if v is not None})
    if narrow_task and archetype == "implementation_change":
        base["target_cap"] = min(int(base.get("target_cap") or 1), 1)
        base["reference_cap"] = min(int(base.get("reference_cap") or 1), 1)
        base["support_cap"] = min(int(base.get("support_cap") or 2), 2)
        base["fallback_hub_cap"] = 0
    return base



def _role_group(role: str) -> str:
    if role in OWNER_BUNDLE_ROLES:
        return "target"
    if role in REFERENCE_BUNDLE_ROLES:
        return "reference"
    if role in WITNESS_BUNDLE_ROLES:
        return "witness"
    if role == "fallback_hub":
        return "fallback_hub"
    if role in SUPPORT_BUNDLE_ROLES:
        return "support"
    return "support"



def _is_owner_candidate(entry: Mapping[str, Any], role: str) -> bool:
    return bool(
        role in OWNER_BUNDLE_ROLES
        or str(entry.get("ownership_role") or "") == "core_owner"
        or str(entry.get("candidate_role_guess") or "") == "owner-like"
        or (str(entry.get("primary_lane") or "") == "target" and str(entry.get("hub_family") or "") == "")
    )



def _is_omnibus_test(entry: Mapping[str, Any]) -> bool:
    path = str(entry.get("path") or "").lower()
    candidate_role = str(entry.get("candidate_role") or "")
    return bool(
        candidate_role == "test_file"
        and any(token in path for token in ["integration", "e2e", "end_to_end", "workflow", "scenario", "omnibus"])
    )



def _bundle_entry_priority(entry: Mapping[str, Any], role: str) -> Tuple[int, int, float, float, str]:
    group = _role_group(role)
    return (
        0 if group == "target" else 1 if group == "witness" else 2 if group == "support" else 3 if group == "reference" else 4,
        0 if _is_owner_candidate(entry, role) else 1,
        -float(entry.get("targetability_score") or entry.get("selection_score") or entry.get("score") or 0.0),
        -float(entry.get("reference_value_score") or entry.get("selection_score") or entry.get("score") or 0.0),
        str(entry.get("path") or ""),
    )


def _owner_subsystems(entries: Sequence[Mapping[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for entry in entries:
        if not _is_owner_candidate(entry, str(entry.get("bundle_role") or "")):
            continue
        for item in list(entry.get("subsystems") or []):
            text = str(item or "").strip()
            if text:
                out.add(text)
    return out


def _same_owner_neighborhood(entry: Mapping[str, Any], owner_subsystems: Set[str], owner_paths: Set[str]) -> bool:
    if owner_subsystems.intersection({str(item or "").strip() for item in list(entry.get("subsystems") or []) if str(item or "").strip()}):
        return True
    path = str(entry.get("path") or "").lower()
    owner_dirs = {str(Path(path_value).parent).lower() for path_value in owner_paths if str(path_value).strip()}
    return any(owner_dir and path.startswith(owner_dir + "/") for owner_dir in owner_dirs)


def _structure_candidate_key(
    entry: Mapping[str, Any],
    role: str,
    *,
    owner_subsystems: Set[str],
    owner_paths: Set[str],
) -> Tuple[int, int, int, int, float, float, str]:
    context_intent_type = str(entry.get("context_intent_type") or "")
    role_group = _role_group(role)
    typed_match = 1
    if role_group == "witness" and context_intent_type == "witness_test":
        typed_match = 0
    elif role_group == "reference" and context_intent_type == "contract_reference":
        typed_match = 0
    elif role_group == "support" and context_intent_type in {"validation_context", "broad_docs_reporting"}:
        typed_match = 0
    elif role_group == "fallback_hub" and context_intent_type == "fallback_hub":
        typed_match = 0
    return (
        0 if _same_owner_neighborhood(entry, owner_subsystems, owner_paths) else 1,
        typed_match,
        0 if str(entry.get("hub_family") or "") == "" else 1,
        0 if not bool(entry.get("passive_surface_flag")) else 1,
        -float(entry.get("support_signal_strength") or entry.get("reference_value_score") or entry.get("score") or 0.0),
        -float(entry.get("targetability_score") or entry.get("score") or 0.0),
        str(entry.get("path") or ""),
    )


def _eviction_index_for_structure(accepted: Sequence[Mapping[str, Any]], *, preserve_owner: bool = True) -> Optional[int]:
    preferred_groups = {"fallback_hub", "reference", "support"}
    for idx in range(len(accepted) - 1, -1, -1):
        entry = accepted[idx]
        role = str(entry.get("bundle_role") or "support_context")
        group = _role_group(role)
        if preserve_owner and _is_owner_candidate(entry, role):
            continue
        if bool(entry.get("passive_surface_flag")):
            return idx
        if group in preferred_groups:
            return idx
    for idx in range(len(accepted) - 1, -1, -1):
        entry = accepted[idx]
        role = str(entry.get("bundle_role") or "support_context")
        if preserve_owner and _is_owner_candidate(entry, role):
            continue
        return idx
    return None



def select_bundle_candidates(
    prioritized_files: Sequence[Mapping[str, Any]],
    *,
    task_archetype: str,
    max_bundle_files: int,
    policy_config: Optional[Mapping[str, Any]] = None,
    narrow_task: bool = False,
) -> Dict[str, Any]:
    archetype = normalize_task_archetype(task_archetype)
    policy = bundle_policy_for_task(archetype, policy_config, narrow_task=narrow_task)
    target_index = 0
    proposed: List[Tuple[Dict[str, Any], str]] = []
    for raw in prioritized_files:
        entry = dict(raw)
        role = classify_bundle_role(entry, task_archetype=archetype, target_index=target_index)
        if role in OWNER_BUNDLE_ROLES:
            target_index += 1
        proposed.append((entry, role))

    accepted: List[Dict[str, Any]] = []
    actions: List[str] = []
    counts = {"target": 0, "support": 0, "reference": 0, "witness": 0, "fallback_hub": 0, "passive": 0}
    family_counts: Dict[str, int] = {}
    basename_counts: Dict[str, int] = {}
    omnibus_count = 0
    target_index = 0

    for entry, initial_role in proposed:
        path = str(entry.get("path") or "")
        basename = Path(path).name.lower()
        family_key = str(entry.get("duplicate_family_key") or path)
        hub_family = str(entry.get("hub_family") or "")
        passive_flag = bool(entry.get("passive_surface_flag"))
        role = initial_role
        if archetype in {"repo_assessment", "information_retrieval", "documentation_contract"} and role in OWNER_BUNDLE_ROLES:
            role = "support_context"
        if role in OWNER_BUNDLE_ROLES:
            target_index += 1
            role = classify_bundle_role(entry, task_archetype=archetype, target_index=max(0, target_index - 1))

        group = _role_group(role)
        quota = int(policy.get(f"{group}_cap") or max_bundle_files)
        duplicate_cap = int(policy.get("duplicate_family_cap") or 1)
        basename_cap = int(policy.get("basename_cap") or 1)
        omnibus_cap = int(policy.get("omnibus_test_cap") or 1)
        rescue = _is_owner_candidate(entry, role)

        reject_reason = ""
        if len(accepted) >= max_bundle_files:
            reject_reason = "bundle-file-cap"
        elif group == "fallback_hub" and counts["fallback_hub"] >= int(policy.get("fallback_hub_cap") or 0):
            reject_reason = "fallback-hub-cap"
        elif passive_flag and counts["passive"] >= int(policy.get("passive_cap") or 0) and not rescue:
            reject_reason = "passive-surface-cap"
        elif quota >= 0 and counts[group] >= quota and not rescue:
            reject_reason = f"role-cap:{group}"
        elif family_counts.get(family_key, 0) >= duplicate_cap and not rescue:
            reject_reason = f"duplicate-family-cap:{family_key}"
        elif basename_counts.get(basename, 0) >= basename_cap and not rescue:
            reject_reason = f"basename-cap:{basename}"
        elif _is_omnibus_test(entry) and omnibus_count >= omnibus_cap and not rescue:
            reject_reason = "omnibus-test-cap"
        elif hub_family and group in {"support", "fallback_hub"} and family_counts.get(f"hub:{hub_family}", 0) >= int(policy.get("fallback_hub_cap") or 1) and not rescue:
            reject_reason = f"hub-family-cap:{hub_family}"

        if reject_reason:
            actions.append(f"{reject_reason}:{path}")
            continue

        entry["bundle_role"] = role
        accepted.append(entry)
        counts[group] += 1
        if passive_flag:
            counts["passive"] += 1
        family_counts[family_key] = family_counts.get(family_key, 0) + 1
        if hub_family:
            family_counts[f"hub:{hub_family}"] = family_counts.get(f"hub:{hub_family}", 0) + 1
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
        if _is_omnibus_test(entry):
            omnibus_count += 1

    recovery_hints: List[str] = []

    def _recompute_accept_state() -> None:
        nonlocal omnibus_count
        counts.update({"target": 0, "support": 0, "reference": 0, "witness": 0, "fallback_hub": 0, "passive": 0})
        family_counts.clear()
        basename_counts.clear()
        omnibus_count = 0
        for item in accepted:
            role = str(item.get("bundle_role") or "support_context")
            group = _role_group(role)
            path = str(item.get("path") or "")
            family_key = str(item.get("duplicate_family_key") or path)
            basename = Path(path).name.lower()
            hub_family = str(item.get("hub_family") or "")
            counts[group] += 1
            if bool(item.get("passive_surface_flag")):
                counts["passive"] += 1
            family_counts[family_key] = family_counts.get(family_key, 0) + 1
            if hub_family:
                family_counts[f"hub:{hub_family}"] = family_counts.get(f"hub:{hub_family}", 0) + 1
            basename_counts[basename] = basename_counts.get(basename, 0) + 1
            if _is_omnibus_test(item):
                omnibus_count += 1

    def _insert_structured_candidate(
        *,
        wanted_groups: Set[str],
        action_prefix: str,
        exact_roles: Optional[Set[str]] = None,
    ) -> bool:
        owner_paths = {
            str(item.get("path") or "")
            for item in accepted
            if _is_owner_candidate(item, str(item.get("bundle_role") or ""))
        }
        owner_subsystems = _owner_subsystems(accepted)
        selected_paths = {str(item.get("path") or "") for item in accepted}
        pool: List[Tuple[Dict[str, Any], str]] = []
        for entry, role in proposed:
            path = str(entry.get("path") or "")
            if not path or path in selected_paths:
                continue
            if exact_roles is not None:
                if role not in exact_roles:
                    continue
            elif _role_group(role) not in wanted_groups:
                continue
            pool.append((dict(entry), role))
        if not pool:
            return False
        pool.sort(key=lambda item: _structure_candidate_key(item[0], item[1], owner_subsystems=owner_subsystems, owner_paths=owner_paths))
        chosen, chosen_role = pool[0]
        chosen["bundle_role"] = chosen_role
        if len(accepted) >= max_bundle_files:
            removable_idx = _eviction_index_for_structure(accepted, preserve_owner=True)
            if removable_idx is None:
                recovery_hints.append(f"widen bundle budget to preserve {action_prefix.replace('-', ' ')}")
                return False
            removed = accepted.pop(removable_idx)
            actions.append(f"{action_prefix}-evict:{removed.get('path') or ''}")
        accepted.append(chosen)
        actions.append(f"{action_prefix}-insert:{chosen.get('path') or ''}")
        _recompute_accept_state()
        return True

    owner_present = any(_is_owner_candidate(entry, str(entry.get("bundle_role") or "")) for entry in accepted)
    owner_candidates = [entry for entry, role in proposed if _is_owner_candidate(entry, role)]
    if bool(policy.get("preserve_owner")) and not owner_present and owner_candidates:
        candidate = sorted(owner_candidates, key=lambda entry: _bundle_entry_priority(entry, classify_bundle_role(entry, task_archetype=archetype, target_index=0)))[0]
        removable_idx = next(
            (
                idx for idx in range(len(accepted) - 1, -1, -1)
                if not _is_owner_candidate(accepted[idx], str(accepted[idx].get("bundle_role") or ""))
            ),
            None,
        )
        inserted = dict(candidate)
        inserted_role = classify_bundle_role(inserted, task_archetype=archetype, target_index=0)
        inserted["bundle_role"] = inserted_role
        if removable_idx is not None:
            removed = accepted.pop(removable_idx)
            actions.append(f"owner-preservation-evict:{removed.get('path') or ''}")
        elif len(accepted) >= max_bundle_files:
            recovery_hints.append("widen bundle budget to preserve at least one owner-like file")
        if len(accepted) < max_bundle_files and not any(str(item.get("path") or "") == str(inserted.get("path") or "") for item in accepted):
            accepted.insert(0, inserted)
            actions.append(f"owner-preservation-insert:{inserted.get('path') or ''}")
            owner_present = True
            _recompute_accept_state()

    owner_present = any(_is_owner_candidate(entry, str(entry.get("bundle_role") or "")) for entry in accepted)
    if owner_present and archetype == "implementation_change":
        min_support_if_owner = max(0, int(policy.get("min_support_if_owner") or 0))
        current_support = sum(1 for entry in accepted if _role_group(str(entry.get("bundle_role") or "support_context")) == "support")
        if current_support < min_support_if_owner:
            _insert_structured_candidate(wanted_groups={"support"}, action_prefix="support-preservation")
        witness_present = any(_role_group(str(entry.get("bundle_role") or "support_context")) == "witness" for entry in accepted)
        if bool(policy.get("prefer_witness_with_owner")) and not witness_present:
            inserted = _insert_structured_candidate(wanted_groups={"witness"}, action_prefix="paired-test-preservation")
            if not inserted:
                _insert_structured_candidate(exact_roles={"validation_context", "contract_context"}, wanted_groups={"support", "reference"}, action_prefix="constraint-substitution")

    if archetype == "design_new_surface" and bool(policy.get("prefer_contract_context")) and not any(str(entry.get("bundle_role") or "") in REFERENCE_BUNDLE_ROLES for entry in accepted):
        _insert_structured_candidate(wanted_groups={"reference"}, exact_roles=set(REFERENCE_BUNDLE_ROLES), action_prefix="contract-preservation")

    if not owner_present and archetype == "implementation_change":
        recovery_hints.append("widen retrieval for owner-like implementation files before patch generation")
        recovery_hints.append("relax hub suppression only if no concrete owner candidate emerges")
    if len(accepted) < int(policy.get("min_bundle_files") or 1):
        recovery_hints.append("expand adjacent support/reference context because bundle is context sparse")

    accepted.sort(key=lambda entry: _bundle_entry_priority(entry, str(entry.get("bundle_role") or "support_context")))
    return {
        "selected_entries": accepted[:max_bundle_files],
        "anti_collapse_actions": _dedupe(actions),
        "policy": policy,
        "recovery_hints": _dedupe(recovery_hints),
    }



def summarize_bundle(
    files: Sequence[Mapping[str, Any]],
    anti_collapse_actions: Sequence[str],
    *,
    task_archetype: str = "implementation_change",
    candidate_pool: Optional[Sequence[Mapping[str, Any]]] = None,
    recovery_hints: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    archetype = normalize_task_archetype(task_archetype)
    owner_count = 0
    support_count = 0
    reference_count = 0
    witness_count = 0
    fallback_hub_count = 0
    passive_count = 0
    family_keys: Set[str] = set()
    for entry in files:
        role = str(entry.get("bundle_role") or entry.get("role") or "")
        family_keys.add(str(entry.get("duplicate_family_key") or entry.get("path") or ""))
        if role in OWNER_BUNDLE_ROLES:
            owner_count += 1
        elif role in SUPPORT_BUNDLE_ROLES:
            support_count += 1
        elif role in REFERENCE_BUNDLE_ROLES:
            reference_count += 1
        elif role in WITNESS_BUNDLE_ROLES:
            witness_count += 1
        elif role == "fallback_hub":
            fallback_hub_count += 1
        if bool(entry.get("passive_surface_flag")):
            passive_count += 1
    owner_present = owner_count > 0
    owner_candidates_available = any(
        _is_owner_candidate(entry, str(entry.get("bundle_role") or classify_bundle_role(entry, task_archetype=archetype, target_index=0)))
        for entry in list(candidate_pool or [])
    )
    owner_uncertainty_flag = not owner_present and archetype == "implementation_change"
    if owner_uncertainty_flag and owner_candidates_available:
        owner_uncertainty_reason = "owner-like candidates existed but were not preserved in the final bundle"
    elif owner_uncertainty_flag:
        owner_uncertainty_reason = "no convincing owner-like file preserved in final bundle"
    else:
        owner_uncertainty_reason = ""
    family_diversity = round(len([x for x in family_keys if x]) / max(1, len(files)), 4)
    bundle_quality_status = "normal"
    if owner_uncertainty_flag:
        bundle_quality_status = "degraded_owner_uncertain"
    elif len(files) <= 1 or (archetype == "implementation_change" and len(files) <= 2 and (support_count + reference_count + witness_count) == 0):
        bundle_quality_status = "degraded_context_sparse"
    return {
        "owner_present": owner_present,
        "owner_count": owner_count,
        "support_count": support_count,
        "reference_count": reference_count,
        "witness_count": witness_count,
        "fallback_hub_count": fallback_hub_count,
        "passive_count": passive_count,
        "family_diversity_score": family_diversity,
        "anti_collapse_actions_applied": list(dict.fromkeys(str(x) for x in anti_collapse_actions if str(x).strip())),
        "owner_uncertainty_flag": owner_uncertainty_flag,
        "owner_uncertainty_reason": owner_uncertainty_reason,
        "bundle_quality_status": bundle_quality_status,
        "bundle_recovery_hints": _dedupe(list(recovery_hints or [])),
        "bundle_strategy": archetype,
    }
