from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence



def compute_bundle_diagnostics(
    bundle_files: Sequence[Mapping[str, Any]],
    *,
    bundle_summary: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    summary = dict(bundle_summary or {})
    paths = [str(x.get("path") or "") for x in bundle_files]
    owner_present = any(str(x.get("bundle_role") or x.get("role") or "") in {"primary_target", "secondary_target", "config_target"} for x in bundle_files)
    family_keys = [str(x.get("duplicate_family_key") or x.get("path") or "") for x in bundle_files]
    unique_family_count = len({x for x in family_keys if x})
    fallback_hub_count = sum(1 for x in bundle_files if str(x.get("bundle_role") or x.get("role") or "") == "fallback_hub")
    anti_collapse_actions = list(summary.get("anti_collapse_actions_applied") or [])
    overflow_actions = [str(item) for item in anti_collapse_actions if "cap" in str(item)]
    recovery_hints = list(summary.get("bundle_recovery_hints") or [])
    return {
        "bundle_owner_present": owner_present,
        "bundle_family_diversity_score": round(unique_family_count / max(1, len(bundle_files)), 4),
        "bundle_hub_overflow_count": max(0, len(overflow_actions)),
        "fallback_hub_usage_rate": 1.0 if fallback_hub_count else 0.0,
        "bundle_paths": paths,
        "owner_uncertainty_flag": bool(summary.get("owner_uncertainty_flag")),
        "bundle_quality_status": str(summary.get("bundle_quality_status") or "normal"),
        "bundle_recovery_hint_count": len(recovery_hints),
    }



def compute_rank_diagnostics(ranked_files: Sequence[Mapping[str, Any]], top_k: int = 5) -> Dict[str, Any]:
    top = list(ranked_files[:top_k])
    hotspot = sum(1 for x in top if str(x.get("hub_family") or ""))
    duplicate = max(0, len([str(x.get("duplicate_family_key") or "") for x in top]) - len({str(x.get("duplicate_family_key") or "") for x in top if str(x.get("duplicate_family_key") or "")}))
    passive = sum(1 for x in top if bool(x.get("passive_surface_flag")))
    suppressed = sum(1 for x in top if str(x.get("primary_lane") or "") == "suppressed" or bool(x.get("selection_cap_suppressed")))
    target_slots = [x for x in top if str(x.get("primary_lane") or "") == "target"]
    target_purity = 0.0
    if target_slots:
        owner_like = sum(1 for x in target_slots if str(x.get("candidate_role_guess") or "") == "owner-like")
        target_purity = round(owner_like / len(target_slots), 4)
    return {
        "hotspot_topk_count": hotspot,
        "duplicate_family_topk_count": duplicate,
        "passive_surface_topk_count": passive,
        "target_lane_purity": target_purity,
        "suppressed_topk_count": suppressed,
    }
