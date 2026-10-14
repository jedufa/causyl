"""Phase-2 controlled composition boundary and constants."""

from __future__ import annotations

PHASE2_ACTION_FAMILY_IDS = (
    "build_verify",
    "status_reconcile",
    "patch_apply",
)

PHASE2_ENTRYPOINT_IDS = (
    "api_action_execute",
    "cli_action_execute",
    "scheduler_action_dispatch",
    "snapshot_drift_trigger",
)

PHASE2_PLAYBOOK_IDS = (
    "build_verify_loop_v1",
    "status_reconcile_v1",
    "patch_apply_chain_v1",
)

PHASE2_VERIFICATION_OWNER = "scheduler_verifier"
PHASE2_MAX_CONTEXT_TOKENS = 20000
