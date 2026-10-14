"""Campaign DAG, retry, and backpressure planning for verification runs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping


class CampaignDAGError(ValueError):
    """Raised when a campaign DAG would be unsafe or misleading."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def build_campaign_dag(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    jobs = list(payload.get("compiled_jobs") or [])
    if not jobs:
        raise CampaignDAGError("compiled_jobs_missing", "compiled jobs are required")
    repo_shas = {_clean_text(job.get("repo_commit_sha") or payload.get("repo_commit_sha")) for job in jobs}
    repo_shas.discard("")
    if len(repo_shas) > 1:
        raise CampaignDAGError("mixed_commit_fanout_blocked", "campaign fanout cannot mix multiple repo commits")
    job_ids = [_clean_text(job.get("job_id")) for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise CampaignDAGError("duplicate_launch_blocked", "duplicate job ids are not allowed in one campaign DAG")
    verification_edges = [{"from": "campaign_start", "to": job_id} for job_id in job_ids]
    return {
        "schema_version": "verification_campaign_dag_v1",
        "campaign_id": _clean_text(payload.get("campaign_id") or "campaign::verification"),
        "child_job_types": ["verify_scenario", "normalize_findings", "prioritize_findings", "synthesize_repair"],
        "dependency_edges": verification_edges + [
            {"from": job_id, "to": "normalize_findings"} for job_id in job_ids
        ] + [
            {"from": "normalize_findings", "to": "prioritize_findings"},
            {"from": "prioritize_findings", "to": "synthesize_repair"},
        ],
        "batch_strategy": "fanout_verifiers_then_rejoin_for_normalization",
        "max_concurrency": min(len(job_ids), 4),
        "backpressure_rules": [
            "stop new verifier fanout when normalize queue depth exceeds active verifier count",
            "block reruns while mixed-commit or stale-artifact conditions remain unresolved",
        ],
        "retry_policy": {
            "transient_flake": {"max_retries": 1, "action": "retry_same_job_once"},
            "likely_real_defect": {"max_retries": 0, "action": "promote_to_finding"},
        },
        "rerun_edges_after_repair": [
            {"from": "accepted_repair", "to": "targeted_revalidation"},
            {"from": "accepted_repair", "to": "family_level_rerun"},
        ],
        "maximum_supported_claim": "campaign_dag_defined_only",
    }
