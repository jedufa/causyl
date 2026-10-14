"""Phase-1 execution boundary for the deterministic action plane."""

from __future__ import annotations

ACTION_FAMILY_IDS = (
    "patch_ingest_repair",
    "pis_assess_proof_gap",
    "job_inspect_bounded_recovery",
)

PHASE1_ACTION_IDS = (
    "patch_ingest_repair_v1",
    "pis_assess_proof_gap_v1",
    "job_inspect_bounded_recovery_v1",
)

FIRST_IMPLEMENTATION_ACTION_ID = "patch_ingest_repair_v1"
RULE_MAPPING_VERSION = "action_rule_mapping_v1"

OUT_OF_SCOPE_PHASE1 = (
    "general_planning",
    "domain_general_decomposition",
    "broad_multi_domain_action_coverage",
    "substrate_wide_execution_unification",
    "broad_automatic_surface_generation",
)

