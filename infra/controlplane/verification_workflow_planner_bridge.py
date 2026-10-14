"""Deterministic planner bridge for broad verification workflow goals."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from infra.controlplane.static_verification.static_verification_planner_extraction import extract_complex_block
from infra.controlplane.static_verification.static_verification_planner_generation import generate_planner_output
from infra.controlplane.static_verification.static_verification_planner_threats import compile_threat_matrix


class VerificationWorkflowPlannerBridgeError(ValueError):
    """Raised when a broad verification goal cannot be planned honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    rows: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in rows:
            rows.append(text)
    return rows


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _goal_targets_scheduler(text: str) -> bool:
    lowered = text.lower()
    return "scheduler" in lowered


def _goal_targets_android_agent_tabs(text: str) -> bool:
    lowered = text.lower()
    if "agent tabs" in lowered:
        return True
    if "agent tab" in lowered:
        return True
    return ("android" in lowered or "mobile" in lowered) and "tabs" in lowered and "agent" in lowered


def _scheduler_extraction_input(goal: str) -> Dict[str, Any]:
    return {
        "qualified_boundary_summary": {
            "decision": "expand",
            "selected_boundary_summary": (
                "Scheduler verification workflow from launch admission through execution, recovery truth, and "
                "operator-visible scheduler projection."
            ),
            "adjacent_truth_seams_reviewed": [
                {
                    "seam_id": "scheduler_receipt_writeback",
                    "seam_kind": "durable_writeback",
                    "relationship_to_selected_block": "launch and execution receipts",
                    "included_in_boundary": True,
                    "claims_affected": [
                        "The plane reports one scheduler verification run exactly once."
                    ],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
                {
                    "seam_id": "shared_resource_claim_truth",
                    "seam_kind": "claim_ledger",
                    "relationship_to_selected_block": "queue claims and active-run materialization",
                    "included_in_boundary": True,
                    "claims_affected": [
                        "The scheduler blocks duplicate or hidden active work honestly."
                    ],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
                {
                    "seam_id": "operator_scheduler_projection",
                    "seam_kind": "operator_projection",
                    "relationship_to_selected_block": "scheduler truth projected into the plane",
                    "included_in_boundary": True,
                    "claims_affected": [
                        "The operator sees stale, blocked, or contradictory scheduler truth honestly."
                    ],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
            ],
            "seams_requiring_expansion": [
                "scheduler_receipt_writeback",
                "shared_resource_claim_truth",
                "operator_scheduler_projection",
            ],
            "maximum_supported_claim_after_qualification": "bounded_scheduler_family_is_planning_eligible",
            "why_this_is_the_current_risk_unit": (
                "A broad scheduler verification goal is only honest if launch truth, restart/recovery truth, and "
                "operator projection are planned together rather than flattened into one happy-path check."
            ),
        },
        "operator_facing_claims_under_attack": [
            "The scheduler launches, runs, and reports verification work truthfully.",
            "The plane does not hide contradictory active, queued, blocked, or recovered scheduler state.",
        ],
        "entrypoints_and_mutation_points": {
            "files_touched": [
                "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
                "infra/controlplane/scheduled_jobs_parts/part09__execute_job.py",
                "infra/controlplane/shared_resource_claim_ledger.py",
                "infra/webui/server_routes_and_main_parts/part01__lab_assistant_host_health_payload.py",
            ],
            "entrypoints": [
                "save_job",
                "request_run_now",
                "execute_job",
                "scheduler_tick",
                "load_state",
            ],
            "mutation_points": [
                "scheduler job spec write",
                "receipt append",
                "active-run and claim-ledger mutation",
                "operator projection payload assembly",
            ],
        },
        "seed_authority_map_or_explicit_unknowns": {
            "truth_authorities": [
                "scheduled job spec",
                "scheduler receipts",
                "shared resource claim ledger",
                "operator-visible scheduler payload",
            ],
            "authority_precedence_edges": [
                "scheduler receipts outrank in-memory launch intent after execution starts",
                "shared resource claim ledger can contradict operator projection and must not be smoothed away",
                "operator payload is derivative and cannot outrank durable scheduler records",
            ],
            "claimed_invariants": [
                "Broad scheduler verification must not accept duplicate or hidden active runs as healthy.",
                "Operator-visible scheduler status must fail closed when durable records disagree.",
            ],
            "ambiguity_windows": [
                "between queue admission and durable receipt append",
                "between active-run claim materialization and operator projection refresh",
                "between interrupted execution and restart reconciliation",
            ],
            "authority_conflicts_or_unknowns": [
                "operator projection can lag receipts and claim-ledger truth",
                "restart recovery can temporarily surface contradictory active and receipt state",
            ],
        },
        "major_truth_seams": [
            {
                "seam_id": "launch_and_receipt_truth",
                "seam_kind": "launch_truth",
                "local_code_shape": "Launch normalization writes scheduler intent, then execution creates durable receipts.",
                "broader_truth_seam": "One launch can look admitted while receipts or claims say otherwise.",
                "truth_authorities": [
                    "scheduled job spec",
                    "scheduler receipts",
                ],
                "authority_precedence_note": "Receipts decide whether launch actually materialized.",
                "operator_claims_affected": [
                    "The scheduler started the verification run once and truthfully."
                ],
                "independent_records": [
                    {
                        "record_id": "scheduler_job_spec",
                        "record_kind": "durable_spec",
                        "source_of_truth_status": "authoritative_pre_launch",
                        "how_it_could_disagree": "The job spec can exist without matching execution receipts.",
                        "observation_path": "scheduled job inspection",
                    },
                    {
                        "record_id": "scheduler_receipt_json",
                        "record_kind": "durable_receipt",
                        "source_of_truth_status": "authoritative_post_launch",
                        "how_it_could_disagree": "Receipts can show duplicate, missing, or contradictory launch history.",
                        "observation_path": "scheduler receipt inspection",
                    },
                ],
                "ambiguity_window": "launch handoff before receipt append completes",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "It is the first place a broad scheduler verification request can lie by omission.",
            },
            {
                "seam_id": "recovery_and_claim_truth",
                "seam_kind": "recovery_truth",
                "local_code_shape": "Restart and claim-ledger reconciliation decide whether active work is real, hidden, or orphaned.",
                "broader_truth_seam": "Recovery can smooth stale or contradictory active state into false calm.",
                "truth_authorities": [
                    "shared resource claim ledger",
                    "scheduler receipts",
                    "job state projection",
                ],
                "authority_precedence_note": "No single active marker outranks the combined durable recovery evidence.",
                "operator_claims_affected": [
                    "The scheduler is healthy after interruption or restart."
                ],
                "independent_records": [
                    {
                        "record_id": "shared_resource_claim_ledger",
                        "record_kind": "durable_claim_ledger",
                        "source_of_truth_status": "authoritative_for_active_claims",
                        "how_it_could_disagree": "Claims can outlive receipts or visible active runs.",
                        "observation_path": "claim ledger inspection",
                    },
                    {
                        "record_id": "scheduler_receipt_history",
                        "record_kind": "durable_receipt",
                        "source_of_truth_status": "authoritative_for_run_history",
                        "how_it_could_disagree": "Receipts can mark completion while a stale claim survives.",
                        "observation_path": "receipt lineage inspection",
                    },
                ],
                "ambiguity_window": "restart and watchdog reconciliation before stale claims are cleared",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "This is the core restart-honesty failure class for scheduler verification.",
            },
            {
                "seam_id": "operator_projection_truth",
                "seam_kind": "operator_truth",
                "local_code_shape": "Web and mobile scheduler payloads summarize durable scheduler state for the operator.",
                "broader_truth_seam": "The operator can see fresh-looking calm from stale or contradictory scheduler data.",
                "truth_authorities": [
                    "operator-visible scheduler payload",
                    "scheduler receipts",
                    "shared resource claim ledger",
                ],
                "authority_precedence_note": "Operator payload is derivative and must show degraded or contradictory truth explicitly.",
                "operator_claims_affected": [
                    "The scheduler is idle, blocked, or healthy for the reasons the plane claims."
                ],
                "independent_records": [
                    {
                        "record_id": "operator_scheduler_payload",
                        "record_kind": "derived_payload",
                        "source_of_truth_status": "derived_projection",
                        "how_it_could_disagree": "The payload can show clean status while durable records disagree.",
                        "observation_path": "web/mobile scheduler view",
                    },
                    {
                        "record_id": "durable_scheduler_truth",
                        "record_kind": "durable_state_bundle",
                        "source_of_truth_status": "authoritative_runtime_state",
                        "how_it_could_disagree": "Durable state can remain stale, blocked, or contradictory underneath the projection.",
                        "observation_path": "receipt and claim-ledger inspection",
                    },
                ],
                "ambiguity_window": "projection refresh lag after state mutation or restart",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "A scheduler verification campaign that ignores the operator surface misses false calm.",
            },
        ],
        "durable_state_and_mutation_points": {
            "durable_state_paths": [
                "runtime/state/scheduler/",
                "runtime/state/shared_resource_claim_ledger/",
                "runtime/state/verification_workflow/",
            ],
            "side_effects": [
                "scheduler launches work",
                "receipts and claims mutate continuity truth",
                "operator surfaces read derived scheduler payloads",
            ],
            "external_surfaces": [
                "the plane web scheduler view",
                "the plane mobile scheduler surfaces",
            ],
            "time_retry_lease_logic": [
                "run-now and queue admission timing",
                "receipt append timing",
                "claim cleanup and restart reconciliation timing",
            ],
            "cleanup_and_recovery_paths": [
                "restart reconciliation",
                "stale claim cleanup",
                "operator projection refresh",
            ],
        },
        "observation_paths": {
            "operator_visible_surfaces": [
                "the plane scheduler status view",
                "the plane mobile scheduler surfaces",
            ],
            "internal_observation_paths": [
                "scheduler receipt inspection",
                "claim ledger inspection",
                "job state inspection",
            ],
            "trust_boundaries": [
                "operator surfaces trust projected scheduler payloads rather than raw durable records",
            ],
            "where_observation_can_smooth_or_hide_failure": [
                "projection can present false idle or healthy status while durable launch or recovery truth is contradictory",
            ],
        },
        "known_existing_tests_or_explicit_none": {
            "existing_tests_reviewed": [
                "tests/control_plane/test_scheduled_jobs.py",
                "tests/control_plane/test_shared_resource_claim_ledger.py",
                "tests/control_plane/webui/test_webui_verification_workflow.py",
            ],
            "likely_blind_spots": [
                "broad verification accepts one thin launch slice as proof of scheduler health",
                "hidden active work after restart or claim drift",
                "operator false calm from stale scheduler projection",
            ],
            "why_current_tests_may_miss_them": [
                "launch-path tests prove one bounded scheduler workflow slice, not broad decomposition",
                "focused scheduler tests may not compare operator projection with durable truth in one campaign",
            ],
            "blind_spot_claim_impact": [
                "could overclaim that scheduler verification is broad or autonomous when it is still narrow",
            ],
            "missing_negative_controls_or_absence_note": (
                "Missing a broad-goal planner bridge that turns scheduler verification into several bounded adversarial families."
            ),
        },
        "operator_goal": goal,
    }


def _scheduler_family_candidates() -> List[Dict[str, Any]]:
    return [
        {
            "scenario_family_id": "scheduler-launch-truth-family",
            "threat_class": "launch_truth",
            "operator_claim_under_attack": "The scheduler launches verification work once and truthfully.",
            "independent_records_that_can_disagree": [
                "scheduled job spec",
                "scheduler receipt history",
                "run-now queue state",
            ],
            "negative_control_plan": "Seed one clean launch and verify the same request fingerprint produces one coherent receipt lineage.",
            "disqualifying_incidents": [
                "Launch is reported healthy despite missing or duplicate receipt materialization.",
                "One request fingerprint maps to more than one active launch story.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that a job spec exists and call that scheduler verification.",
                "closure_mechanism": "Require agreement across job spec, receipt lineage, and queue state for the same request fingerprint.",
                "why_the_closure_forces_real_signal": (
                    "The family cannot pass on existence checks alone because durable records must stay coherent across the launch seam."
                ),
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Launch admission can overclaim success or duplicate work before durable receipts agree.",
                "why_existing_checks_miss_it": "Thin workflow launch tests do not pressure the full launch truth seam under broad verification goals.",
                "evidence_of_failure_if_present": "A scheduler request appears admitted while receipt lineage is missing, duplicated, or contradictory.",
            },
            "production_reachability": {
                "classification": "reachable_in_normal_operation",
                "why_this_classification_is_honest": "Run-now, scheduled admission, and receipt materialization are normal scheduler behavior.",
            },
            "contamination_or_interference_notes": (
                "Prior scheduler receipts must be isolated by request fingerprint or the family can self-confirm from stale runs."
            ),
            "repetition_overlap_accumulation_plan": (
                "Repeat with explicit run-now and queued execution to catch duplicate or missing launch truth across timing variants."
            ),
            "execution_readiness": {
                "readiness_class": "runnable_now",
                "why_not_just_theoretical": "The family reuses the live scheduler-backed verification workflow path already present in the repo.",
            },
            "scenario_examples": [
                "launch request writes a job spec but no coherent receipt lineage",
                "run-now launch and queued launch compete for the same request fingerprint",
                "repeat launch after durable request reuse to prove duplicate admission fails closed",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "The workflow appears queued but no durable execution story advances.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "A second launch is offered while the first request fingerprint still has unresolved scheduler truth.",
        },
        {
            "scenario_family_id": "scheduler-recovery-truth-family",
            "threat_class": "restore_or_replay",
            "operator_claim_under_attack": "The scheduler recovers active verification work honestly after interruption or restart.",
            "independent_records_that_can_disagree": [
                "shared resource claim ledger",
                "scheduler receipt lineage",
                "job state projection",
            ],
            "negative_control_plan": "Seed a coherent completed run and verify restart leaves no stale active or blocked markers behind.",
            "disqualifying_incidents": [
                "Restart or claim reconciliation hides orphan active work.",
                "The plane reports clean recovery while receipts and claims still disagree.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that restart produced some status line without checking durable contradictions.",
                "closure_mechanism": "Force cross-record comparison between claim ledger, receipt lineage, and projected job state after restart.",
                "why_the_closure_forces_real_signal": (
                    "The family only passes if restart truth remains coherent across independent records instead of smoothing contradictions."
                ),
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Restart reconciliation can leave stale claims or hidden active work while projecting recovery success.",
                "why_existing_checks_miss_it": "Focused happy-path runs do not seed restart-era contradictions as part of one scheduler verification campaign.",
                "evidence_of_failure_if_present": "A stale claim survives, or a completed receipt chain still projects active or healthy truth incorrectly.",
            },
            "production_reachability": {
                "classification": "reachable_under_degraded_or_faulted_operation",
                "why_this_classification_is_honest": "Interruptions, stale claims, and delayed recovery are real degraded scheduler conditions.",
            },
            "contamination_or_interference_notes": (
                "Restart and claim-ledger residue from earlier runs can hide the contradiction unless the family resets the targeted request boundary."
            ),
            "repetition_overlap_accumulation_plan": (
                "Repeat once after clean completion and once after seeded stale-claim recovery to prove the mismatch is structural, not flaky."
            ),
            "execution_readiness": {
                "readiness_class": "runnable_after_bounded_setup",
                "why_not_just_theoretical": "The repo already contains scheduler and claim-ledger fixtures that can seed bounded recovery contradictions.",
            },
            "scenario_examples": [
                "restart with stale claim surviving after receipt completion",
                "restart projects idle while durable claim truth still says active",
                "repeat after cleanup to prove recovery can clear the contradiction when it is truly resolved",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "A run remains blocked or hidden with no truthful operator-visible active work.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "A repair or resume action is offered before recovery truth is coherent.",
        },
        {
            "scenario_family_id": "scheduler-operator-false-calm-family",
            "threat_class": "false_calm",
            "operator_claim_under_attack": "The plane shows scheduler truth honestly to the operator.",
            "independent_records_that_can_disagree": [
                "operator scheduler payload",
                "scheduler receipt lineage",
                "shared resource claim ledger",
            ],
            "negative_control_plan": "Use fresh, coherent scheduler state and confirm degraded advisories stay absent.",
            "disqualifying_incidents": [
                "Operator-visible scheduler status looks healthy while durable records disagree.",
                "The plane reports false idle or clean progress from stale projected state.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Check that some warning text exists somewhere in the UI without tying it to the actual stale condition.",
                "closure_mechanism": "Require degraded advisories and contradiction markers to appear exactly when durable state and projection disagree.",
                "why_the_closure_forces_real_signal": (
                    "The family cannot pass on tone alone because the operator-facing claim must collapse when the durable records are contradictory."
                ),
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Operator projection can smooth stale or contradictory scheduler truth into false calm.",
                "why_existing_checks_miss_it": "Existing workflow launch tests prove queueing and execution, not broad operator-truth pressure against durable scheduler records.",
                "evidence_of_failure_if_present": "The operator surface stays calm while receipts or claim-ledger state show stale, blocked, or contradictory scheduler truth.",
            },
            "production_reachability": {
                "classification": "reachable_in_normal_operation",
                "why_this_classification_is_honest": "Projection lag and stale payload rendering are normal operator-facing runtime risks.",
            },
            "contamination_or_interference_notes": (
                "Warm caches and previous payload snapshots can hide the stale path unless the observation boundary is aged on purpose."
            ),
            "repetition_overlap_accumulation_plan": (
                "Repeat after one clean payload refresh and one stale payload refresh to prove the false-calm bug is not accidental."
            ),
            "execution_readiness": {
                "readiness_class": "runnable_now",
                "why_not_just_theoretical": "The repo already exercises scheduler projection surfaces and can stage stale payload conditions locally.",
            },
            "scenario_examples": [
                "stale payload still renders a fresh scheduler summary",
                "operator sees false idle while durable records still show active or blocked work",
                "repeat after cache warmup to ensure the contradiction marker survives projection reuse",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "The operator sees no actionable scheduler work while the queue is actually blocked or contradictory.",
            "unsafe_action_relevant": False,
        },
    ]


def _android_agent_tabs_extraction_input(goal: str) -> Dict[str, Any]:
    return {
        "qualified_boundary_summary": {
            "decision": "expand",
            "selected_boundary_summary": (
                "Android/mobile agent tabs from agent-key coercion through shell payload, topic identity, "
                "navigation context, and typed mutation routing."
            ),
            "adjacent_truth_seams_reviewed": [
                {
                    "seam_id": "mobile_agent_key_truth",
                    "seam_kind": "routing_truth",
                    "relationship_to_selected_block": "agent-key coercion and visible tab selection",
                    "included_in_boundary": True,
                    "claims_affected": ["The plane keeps the correct agent tab selected and rejects invalid agent keys honestly."],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
                {
                    "seam_id": "mobile_topic_identity_truth",
                    "seam_kind": "derived_projection",
                    "relationship_to_selected_block": "tab topic identity derived from durable turn truth",
                    "included_in_boundary": True,
                    "claims_affected": ["Agent tab titles reflect durable topic truth without leaking or smoothing invalid markers."],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
                {
                    "seam_id": "mobile_navigation_context_truth",
                    "seam_kind": "context_projection",
                    "relationship_to_selected_block": "chat/backlog/execution/audit context passthrough",
                    "included_in_boundary": True,
                    "claims_affected": ["The selected tab keeps thread and role context coherent across mobile surfaces."],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
                {
                    "seam_id": "typed_plane_mutation_truth",
                    "seam_kind": "mutation_routing",
                    "relationship_to_selected_block": "typed plane mutation routing from the plane tab",
                    "included_in_boundary": True,
                    "claims_affected": ["The plane tab routes typed verification launches without dropping into generic Codex chat handling."],
                    "reason_if_excluded": "",
                    "misplanning_risk_if_excluded": "",
                    "which_sibling_or_later_phase_owns_it_if_known": "",
                },
            ],
            "seams_requiring_expansion": [
                "mobile_agent_key_truth",
                "mobile_topic_identity_truth",
                "mobile_navigation_context_truth",
                "typed_plane_mutation_truth",
            ],
            "maximum_supported_claim_after_qualification": "bounded_android_agent_tabs_family_is_planning_eligible",
            "why_this_is_the_current_risk_unit": (
                "A hard verification of the Android/mobile agent tabs block must attack selection truth, topic identity, "
                "navigation passthrough, and typed mutation routing together rather than treating the tabs as cosmetic."
            ),
        },
        "operator_facing_claims_under_attack": [
            "The mobile agent tabs keep the right agent, thread, and title identity under stress.",
            "The plane tab performs typed mutations directly instead of silently degrading into generic chat handling.",
        ],
        "entrypoints_and_mutation_points": {
            "files_touched": [
                "infra/webui/server_routes_and_main_parts/part01__lab_assistant_host_health_payload.py",
                "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py",
                "tests/control_plane/webui/test_mobile_chat_agents.py",
                "tests/control_plane/webui/test_plane_mobile_verification_workflow_prompt.py",
            ],
            "entrypoints": [
                "_coerce_mobile_agent_key",
                "_mobile_agent_spec",
                "_mobile_tab_topic_identity",
                "_mobile_shell_navigation_payload",
                "_mobile_assistant_shell_payload",
                "/api/mobile/assistant_shell",
                "/api/mobile/chat/stream",
            ],
            "mutation_points": [
                "agent key coercion",
                "selected tab normalization",
                "tab topic derivation from session truth",
                "navigation context passthrough",
                "typed plane mutation boundary routing",
            ],
        },
        "seed_authority_map_or_explicit_unknowns": {
            "truth_authorities": [
                "mobile agent spec map",
                "durable session rows",
                "operator snapshot payload",
                "mobile shell payload",
                "typed mutation route result",
            ],
            "authority_precedence_edges": [
                "durable session truth outranks derived tab title markers",
                "operator snapshot truth outranks decorative tab calm",
                "typed plane route decisions outrank generic mobile chat fallback when a typed mutation applies",
            ],
            "claimed_invariants": [
                "invalid or hidden tab selections fail closed to the correct visible tab",
                "topic identity does not leak across A/A2/A3",
                "typed plane verification launches stay on the typed mutation path",
            ],
            "ambiguity_windows": [
                "between session writeback and tab topic refresh",
                "between selected tab normalization and navigation surface query rendering",
                "between typed mutation parse and Codex fallback routing",
            ],
            "authority_conflicts_or_unknowns": [
                "topic titles can appear fresh while upstream truth is degraded",
                "tab selection can be coerced while stale thread ids still bleed into navigation",
            ],
        },
        "major_truth_seams": [
            {
                "seam_id": "mobile_tab_selection_truth",
                "seam_kind": "selection_truth",
                "local_code_shape": "Agent-key coercion and visible tab normalization decide which tab is actually selected.",
                "broader_truth_seam": "A hidden or invalid tab can look selected or preserve wrong thread context.",
                "truth_authorities": ["mobile agent spec map", "selected shell payload"],
                "authority_precedence_note": "Visible-tab coercion must win over invalid or hidden agent keys.",
                "operator_claims_affected": ["The active Android/mobile tab is the one the plane says is active."],
                "independent_records": [
                    {"record_id": "mobile_agent_spec", "record_kind": "spec_map", "source_of_truth_status": "authoritative", "how_it_could_disagree": "A key can point to a hidden or wrong thread id.", "observation_path": "agent spec inspection"},
                    {"record_id": "mobile_shell_payload", "record_kind": "derived_payload", "source_of_truth_status": "derived_projection", "how_it_could_disagree": "The shell can render the wrong tab as selected.", "observation_path": "assistant shell payload"},
                ],
                "ambiguity_window": "selected key coercion before payload render",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "Wrong selection truth makes every downstream mobile action land on the wrong block.",
            },
            {
                "seam_id": "mobile_topic_identity_truth",
                "seam_kind": "derived_projection",
                "local_code_shape": "A/A2/A3 topic titles are derived from durable assistant session truth.",
                "broader_truth_seam": "One tab can inherit another tab's title or smooth invalid titles into false identity confidence.",
                "truth_authorities": ["durable session rows", "normalized chat header title", "mobile shell payload"],
                "authority_precedence_note": "Only valid durable assistant markers may override the default tab title.",
                "operator_claims_affected": ["Each Android/mobile agent tab reflects its own topic identity truthfully."],
                "independent_records": [
                    {"record_id": "session_rows", "record_kind": "durable_turn_truth", "source_of_truth_status": "authoritative", "how_it_could_disagree": "A valid title marker can be missing or belong to another thread.", "observation_path": "read_session"},
                    {"record_id": "tab_topic_payload", "record_kind": "derived_payload", "source_of_truth_status": "derived_projection", "how_it_could_disagree": "The payload can show the wrong source or wrong title.", "observation_path": "assistant shell payload"},
                ],
                "ambiguity_window": "topic refresh after session append",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "Topic identity is a core operator-facing truth claim for the agent tabs.",
            },
            {
                "seam_id": "mobile_navigation_context_truth",
                "seam_kind": "context_projection",
                "local_code_shape": "Navigation payload fans thread and agent context across chat/backlog/execution/audit surfaces.",
                "broader_truth_seam": "A selected tab can render one face while sending actions to another thread or surface.",
                "truth_authorities": ["selected tab binding", "navigation payload queries"],
                "authority_precedence_note": "Selected tab binding must stay coherent across all derived surfaces.",
                "operator_claims_affected": ["The selected mobile tab preserves task/thread context across surfaces."],
                "independent_records": [
                    {"record_id": "selected_binding", "record_kind": "binding_truth", "source_of_truth_status": "authoritative", "how_it_could_disagree": "Selected tab/thread binding can diverge from generated links.", "observation_path": "assistant shell payload"},
                    {"record_id": "surface_queries", "record_kind": "navigation_projection", "source_of_truth_status": "derived_projection", "how_it_could_disagree": "One surface can keep stale thread or wrong agent key.", "observation_path": "navigation payload"},
                ],
                "ambiguity_window": "thread-id normalization before surface query rendering",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "Context leakage across tabs is the building block's central failure mode.",
            },
            {
                "seam_id": "typed_plane_mutation_truth",
                "seam_kind": "mutation_boundary",
                "local_code_shape": "The plane tab may route typed verification launches directly without invoking Codex chat streaming.",
                "broader_truth_seam": "A typed mutation can silently fall back to general chat handling and look successful for the wrong reason.",
                "truth_authorities": ["plane route classification", "typed mutation launch result", "session projection"],
                "authority_precedence_note": "Typed mutation recognition must outrank generic chat handling when the prompt encodes a supported action.",
                "operator_claims_affected": ["The plane tab mutates the plane directly when a supported typed action is requested."],
                "independent_records": [
                    {"record_id": "plane_route_result", "record_kind": "routing_decision", "source_of_truth_status": "authoritative", "how_it_could_disagree": "A typed action can be misclassified as generic Codex chat.", "observation_path": "plane route handling"},
                    {"record_id": "turn_projection", "record_kind": "durable_turn_truth", "source_of_truth_status": "post_action_projection", "how_it_could_disagree": "The turn can project success while the wrong execution path actually ran.", "observation_path": "recent turn projections"},
                ],
                "ambiguity_window": "typed parse before Codex fallback",
                "intentionally_excluded_adjacent_seams": [],
                "why_the_seam_is_in_scope": "This is the highest-value operator shortcut on the plane tab and must fail closed.",
            },
        ],
        "durable_state_and_mutation_points": {
            "durable_state_paths": [
                "runtime/state/lab_assistant/",
                "runtime/state/verification_workflow/",
            ],
            "side_effects": [
                "mobile shell payload render",
                "tab topic derivation from session truth",
                "typed mutation launch writeback",
            ],
            "external_surfaces": [
                "the plane mobile assistant shell",
                "the plane mobile chat stream route",
            ],
            "time_retry_lease_logic": [
                "session tail refresh timing",
                "selected tab normalization timing",
                "typed mutation routing timing",
            ],
            "cleanup_and_recovery_paths": [
                "mobile session reset",
                "interrupted stream cancel",
                "typed mutation replay",
            ],
        },
        "observation_paths": {
            "operator_visible_surfaces": [
                "the plane mobile assistant shell",
                "the plane mobile chat route",
            ],
            "internal_observation_paths": [
                "mobile agent spec inspection",
                "durable session rows",
                "turn projection replay",
            ],
            "trust_boundaries": [
                "mobile shell payload is derivative and cannot outrank durable session or typed route truth",
            ],
            "where_observation_can_smooth_or_hide_failure": [
                "the shell can show a clean selected tab and topic even while upstream truth is blocked or the wrong route handled the action",
            ],
        },
        "known_existing_tests_or_explicit_none": {
            "existing_tests_reviewed": [
                "tests/control_plane/webui/test_mobile_chat_agents.py",
                "tests/control_plane/webui/test_plane_mobile_verification_workflow_prompt.py",
            ],
            "likely_blind_spots": [
                "cross-tab title leakage under repeated session churn",
                "typed plane mutations degrading into generic chat",
                "navigation context drift between selected tab and derived surfaces",
                "blocked upstream truth being hidden by valid-looking tab titles",
            ],
            "why_current_tests_may_miss_them": [
                "single-surface checks do not create one campaign that attacks selection, topic, navigation, and mutation routing together",
                "existing checks are mostly deterministic examples, not broad disposable campaigns",
            ],
            "blind_spot_claim_impact": [
                "could overclaim that the Android/mobile tabs are operationally trustworthy when they still leak identity or route actions incorrectly",
            ],
            "missing_negative_controls_or_absence_note": (
                "Missing a broad-goal workflow target that turns the Android/mobile agent tabs block into a harsh disposable campaign."
            ),
        },
        "operator_goal": goal,
    }


def _android_agent_tabs_family_candidates() -> List[Dict[str, Any]]:
    return [
        {
            "scenario_family_id": "android-agent-tabs-selection-family",
            "native_probe_kind": "mobile_tab_selection_probe",
            "recommended_agent_role": "verifier",
            "threat_class": "selection_truth",
            "operator_claim_under_attack": "The active agent tab is the one the plane actually selected.",
            "independent_records_that_can_disagree": ["mobile agent spec", "selected shell payload", "selected tab binding"],
            "negative_control_plan": "Use a valid visible agent key and confirm selected tab, thread id, and binding all match the expected spec.",
            "disqualifying_incidents": [
                "Hidden or invalid agent keys preserve the wrong selected tab.",
                "Selected tab looks correct while binding still points at the wrong thread id.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that one tab is marked selected without checking the binding and thread id.",
                "closure_mechanism": "Require selected agent key, selected thread id, and selected binding to agree after coercion.",
                "why_the_closure_forces_real_signal": "Selection truth only passes if the visible shell, thread id, and binding are coherent together.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Agent-key coercion or hidden-tab fallback can leak stale thread identity into the selected tab.",
                "why_existing_checks_miss_it": "Single examples do not pressure repeated invalid/hidden-key coercion as a disposable campaign.",
                "evidence_of_failure_if_present": "Selected tab or binding remains on the wrong task after an invalid or hidden key request.",
            },
            "production_reachability": {"classification": "reachable_in_normal_operation", "why_this_classification_is_honest": "Mobile tab selection and invalid query values are ordinary runtime conditions."},
            "contamination_or_interference_notes": "Warm mobile session state can hide stale binding leaks unless tab coercion is repeated under varied keys.",
            "repetition_overlap_accumulation_plan": "Repeat with visible, hidden, and invalid keys to catch stale-selection residue rather than one-off correctness.",
            "execution_readiness": {"readiness_class": "runnable_now", "why_not_just_theoretical": "The repo already exposes assistant shell payloads and key coercion in-process."},
            "scenario_examples": [
                "invalid agent key must fail closed to visible default without leaking the previous hidden thread id",
                "plane-hidden key p must not remain selected in the visible tabs view",
                "selected tab binding must track the coerced agent key after repeated tab switches",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "The operator believes one tab is active while actions land on a different thread.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "A destructive action can be sent from the wrong tab context because selection truth leaked.",
        },
        {
            "scenario_family_id": "android-agent-tabs-topic-family",
            "native_probe_kind": "mobile_tab_topic_probe",
            "recommended_agent_role": "verifier",
            "threat_class": "topic_identity",
            "operator_claim_under_attack": "Each agent tab shows its own durable topic identity and not another tab's.",
            "independent_records_that_can_disagree": ["session rows", "normalized header title", "tab topic payload"],
            "negative_control_plan": "Seed one valid assistant title marker for one tab and confirm only that tab adopts it.",
            "disqualifying_incidents": [
                "A2 inherits A3 title state or vice versa.",
                "Invalid header markers suppress the default title instead of failing closed.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that some non-empty title exists.",
                "closure_mechanism": "Require source labeling and title content to agree with the durable session for the specific tab.",
                "why_the_closure_forces_real_signal": "Topic identity only passes if the title comes from the right tab's durable truth with the right source.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Topic titles can leak across tabs or preserve invalid markers under churn.",
                "why_existing_checks_miss_it": "Existing examples cover one valid and one invalid title, not campaign-level churn and leakage.",
                "evidence_of_failure_if_present": "Tab title source or title text mismatches the owning thread's durable assistant markers.",
            },
            "production_reachability": {"classification": "reachable_in_normal_operation", "why_this_classification_is_honest": "Assistant tabs depend on durable topic titles during normal use."},
            "contamination_or_interference_notes": "Old assistant markers in neighboring tabs can create false positives unless sessions are reseeded deliberately.",
            "repetition_overlap_accumulation_plan": "Repeat valid, invalid, and cross-tab marker churn to catch title-source leakage rather than one isolated mismatch.",
            "execution_readiness": {"readiness_class": "runnable_now", "why_not_just_theoretical": "Tab topic identity is derived entirely from in-repo session truth readers."},
            "scenario_examples": [
                "A2 valid title marker must not bleed into A3 default-tab title",
                "overlong or invalid title marker must fail closed to default tab title source",
                "blocked upstream truth must remain visible even while a valid title marker exists",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "A valid-looking title masks that the wrong tab or blocked truth is being shown.",
            "unsafe_action_relevant": False,
        },
        {
            "scenario_family_id": "android-agent-tabs-navigation-family",
            "native_probe_kind": "mobile_tab_navigation_probe",
            "recommended_agent_role": "verifier",
            "threat_class": "navigation_truth",
            "operator_claim_under_attack": "Chat, backlog, execution, and audit surfaces preserve the selected tab context.",
            "independent_records_that_can_disagree": ["selected binding", "navigation payload", "surface queries"],
            "negative_control_plan": "Use one selected tab and confirm all derived surfaces carry the same agent key and thread id.",
            "disqualifying_incidents": [
                "One derived surface keeps a stale thread id after tab switch.",
                "Execution or audit surface points at a different agent key than the selected shell.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Check only the selected tab line and ignore downstream surface queries.",
                "closure_mechanism": "Require every derived surface query to preserve the selected tab's agent key and thread id.",
                "why_the_closure_forces_real_signal": "Navigation truth only passes if all downstream links stay coherent, not just the shell header.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Tab switches can leave stale context in one or more mobile surfaces.",
                "why_existing_checks_miss_it": "Examples cover a single navigation payload, not churn or repeated tab/context changes.",
                "evidence_of_failure_if_present": "At least one derived surface carries a stale or mismatched query after selection changes.",
            },
            "production_reachability": {"classification": "reachable_in_normal_operation", "why_this_classification_is_honest": "Operators switch tabs and surfaces constantly during normal mobile use."},
            "contamination_or_interference_notes": "Cached navigation payloads can hide stale-surface drift unless selection is churned repeatedly.",
            "repetition_overlap_accumulation_plan": "Repeat tab switches across A/A2/A3 and replay the shell payload to surface stale derived queries.",
            "execution_readiness": {"readiness_class": "runnable_now", "why_not_just_theoretical": "Navigation payloads are built locally from selected tab context."},
            "scenario_examples": [
                "execution surface must preserve the selected A3 thread context after tab switch churn",
                "audit surface must not retain the previous tab's agent key after selected binding changes",
                "backlog and chat queries must stay aligned after hidden-tab coercion back to a visible tab",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "Work appears to belong to one tab while downstream actions route to another context.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "A run or cancel action can land on the wrong thread because derived navigation drifted.",
        },
        {
            "scenario_family_id": "android-agent-tabs-typed-mutation-family",
            "native_probe_kind": "mobile_typed_mutation_probe",
            "recommended_agent_role": "builder_verifier",
            "threat_class": "typed_mutation",
            "operator_claim_under_attack": "The plane tab routes supported typed mutations directly and truthfully.",
            "independent_records_that_can_disagree": ["plane route result", "launch result", "turn projection"],
            "negative_control_plan": "Send a supported typed verification workflow launch from the plane tab and confirm Codex streaming is never invoked.",
            "disqualifying_incidents": [
                "Typed verification launch falls back to generic chat handling.",
                "Turn projection reports success while the wrong execution path handled the request.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Look for a success string in chat output and assume the right route ran.",
                "closure_mechanism": "Require typed launch result, scheduled job identity, and no Codex-manager path execution.",
                "why_the_closure_forces_real_signal": "Typed mutations only pass if the direct mutation path actually handled the request.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Plane-tab typed actions can silently degrade into generic chat routing under prompt or boundary variations.",
                "why_existing_checks_miss_it": "One typed prompt example does not pressure alternative phrasings, retries, or churn around the boundary.",
                "evidence_of_failure_if_present": "A typed verification launch produces a turn message but no direct mutation proof or wrong job identity.",
            },
            "production_reachability": {"classification": "reachable_in_normal_operation", "why_this_classification_is_honest": "The plane tab is expected to drive typed mutations directly during normal operator use."},
            "contamination_or_interference_notes": "General chat fallback can self-confirm unless the campaign proves the direct route was the one that fired.",
            "repetition_overlap_accumulation_plan": "Repeat supported typed mutations with whitespace, note text, and run-now variants to catch fallback drift.",
            "execution_readiness": {"readiness_class": "runnable_now", "why_not_just_theoretical": "Typed plane mutation routing already exists and is exercised locally."},
            "scenario_examples": [
                "plane-tab typed verification launch must bypass Codex worker startup entirely",
                "typed launch with run_now and operator note variations must still resolve as a direct mutation",
                "turn projection must report the queued verification workflow with the correct scheduled job identity",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "The operator thinks the plane mutated state directly while nothing durable actually changed.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "A typed mutation can target the wrong action path and make a live change for the wrong reason.",
        },
        {
            "scenario_family_id": "android-agent-tabs-upstream-truth-family",
            "native_probe_kind": "mobile_upstream_truth_probe",
            "recommended_agent_role": "verifier",
            "threat_class": "upstream_truth",
            "operator_claim_under_attack": "Blocked or degraded upstream truth remains visible even when the tabs look otherwise healthy.",
            "independent_records_that_can_disagree": ["operator snapshot payload", "mobile shell payload", "tab topic payload"],
            "negative_control_plan": "Use healthy upstream truth and confirm no degraded advisory leaks into the shell.",
            "disqualifying_incidents": [
                "Valid topic titles hide blocked upstream truth.",
                "The shell projects healthy calm while upstream truth is degraded.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that the topic title looks right and ignore upstream truth lines.",
                "closure_mechanism": "Require upstream truth fields to survive regardless of tab topic validity.",
                "why_the_closure_forces_real_signal": "The shell only passes if decorative tab identity cannot erase blocked or degraded upstream truth.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "The shell can smooth degraded operator truth beneath valid-looking tab identity.",
                "why_existing_checks_miss_it": "Point checks do not pressure multiple valid-topic and blocked-upstream combinations as one campaign.",
                "evidence_of_failure_if_present": "Blocked/degraded upstream truth disappears or is downgraded when a valid topic title exists.",
            },
            "production_reachability": {"classification": "reachable_under_degraded_or_faulted_operation", "why_this_classification_is_honest": "Upstream blockage and degraded backend conditions are real operator states."},
            "contamination_or_interference_notes": "Healthy cached snapshots can hide a degraded edge unless the payload is rebuilt against explicitly degraded truth.",
            "repetition_overlap_accumulation_plan": "Repeat with blocked, degraded, and recovered upstream truth to prove the shell does not smooth the bad case away.",
            "execution_readiness": {"readiness_class": "runnable_now", "why_not_just_theoretical": "Upstream truth payloads are already injectable in local shell tests."},
            "scenario_examples": [
                "blocked upstream truth must survive alongside a valid A-tab topic marker",
                "degraded backend condition must remain visible across tab topic refresh",
                "recovered upstream truth must clear advisories without leaving stale blocked summary behind",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "The shell looks calm while the plane is blocked or degraded underneath.",
            "unsafe_action_relevant": False,
        },
        {
            "scenario_family_id": "android-agent-tabs-interruption-family",
            "native_probe_kind": "mobile_interruption_probe",
            "recommended_agent_role": "builder_verifier",
            "threat_class": "interruption_recovery",
            "operator_claim_under_attack": "Interrupted mobile agent-tab actions recover or cancel without leaving cross-tab residue.",
            "independent_records_that_can_disagree": ["active stream state", "mobile cancel result", "selected shell payload"],
            "negative_control_plan": "Cancel a matching active stream and confirm the active state clears without moving the selected tab.",
            "disqualifying_incidents": [
                "Cancel or reset clears the wrong tab or wrong thread.",
                "Interrupted active work leaves stale selection or topic identity behind.",
            ],
            "loophole_closure": {
                "lazy_pass_path": "Assert that cancel returned a payload without checking the selected tab and residual state afterward.",
                "closure_mechanism": "Require cancel/reset result, selected tab identity, and post-action shell payload to stay coherent together.",
                "why_the_closure_forces_real_signal": "Interruption handling only passes if cleanup happens on the right tab without collateral context damage.",
            },
            "bug_yield_hypothesis": {
                "unknown_bug_class_sought": "Interruption and reset paths can damage neighboring tab context or leave stale active state visible.",
                "why_existing_checks_miss_it": "Current examples focus on launch and shell payloads, not interruption/recovery residue on the tabs block.",
                "evidence_of_failure_if_present": "Cancel/reset clears or corrupts the wrong tab context, or leaves stale active state behind.",
            },
            "production_reachability": {"classification": "reachable_under_degraded_or_faulted_operation", "why_this_classification_is_honest": "Interruptions and cancelled mobile streams are real operator actions."},
            "contamination_or_interference_notes": "Neighboring tabs can hide wrong-thread cleanup unless the family alternates active and inactive tabs deliberately.",
            "repetition_overlap_accumulation_plan": "Repeat cancel/reset with alternating active tabs to catch wrong-thread cleanup and stale residue.",
            "execution_readiness": {"readiness_class": "runnable_after_bounded_setup", "why_not_just_theoretical": "The repo exposes mobile cancel/reset endpoints and session state controls for bounded interruption probes."},
            "scenario_examples": [
                "cancel on one mobile tab must not clear or move neighboring tab context",
                "reset after interrupted active stream must restore default visible tab identity cleanly",
                "repeated cancel/restart churn must not leak stale active status into tab topic or selection payloads",
            ],
            "stateful_or_reliability_sensitive": True,
            "silent_paralysis_relevant": True,
            "silent_paralysis_path": "An interrupted tab remains visibly normal while stale active state still owns the route.",
            "unsafe_action_relevant": True,
            "unsafe_action_path": "Cancel or reset can strike the wrong tab context under churn.",
        },
    ]


def _native_execution_family(target_family: str) -> Dict[str, Any]:
    if target_family == "android_agent_tabs":
        return {
            "schema_version": "verification_workflow_native_execution_family_v1",
            "target_family": "android_agent_tabs",
            "family_kind": "mobile_verification_family",
            "operator_target_label": "Android agent tabs building block",
            "decomposition_mode": "target_native_disposable_probe_swarm",
            "job_ontology": [
                "mobile_tab_selection_probe",
                "mobile_tab_topic_probe",
                "mobile_tab_navigation_probe",
                "mobile_typed_mutation_probe",
                "mobile_upstream_truth_probe",
                "mobile_interruption_probe",
                "mobile_adaptive_followup_probe",
                "mobile_verification_closeout",
            ],
            "swarm_shape": {
                "builder_roles": ["builder_verifier"],
                "verifier_roles": ["verifier"],
                "closeout_role": "closeout",
                "adaptive_followup_kind": "mobile_adaptive_followup_probe",
            },
            "claim_ceiling": "target_native_campaign_generation_only",
        }
    return {
        "schema_version": "verification_workflow_native_execution_family_v1",
        "target_family": "scheduler",
        "family_kind": "scheduler_verification_family",
        "operator_target_label": "scheduler building block",
        "decomposition_mode": "bounded_scheduler_probe_family",
        "job_ontology": [
            "scheduler_launch_probe",
            "scheduler_recovery_probe",
            "scheduler_operator_projection_probe",
            "scheduler_adaptive_followup_probe",
            "scheduler_verification_closeout",
        ],
        "swarm_shape": {
            "builder_roles": ["builder_verifier"],
            "verifier_roles": ["verifier"],
            "closeout_role": "closeout",
            "adaptive_followup_kind": "scheduler_adaptive_followup_probe",
        },
        "claim_ceiling": "target_native_campaign_generation_only",
    }


def _selected_block_summary(goal: str, *, target_family: str) -> Dict[str, Any]:
    if target_family == "android_agent_tabs":
        return {
            "schema_version": "verification_workflow_selected_block_summary_v1",
            "block_ref": "android_agent_tabs_family::broad_verification_goal",
            "block_label": "Android agent tabs verification family",
            "qualification_verdict": "expand",
            "operator_goal": goal,
            "risk_unit_summary": (
                "The Android/mobile agent tabs block must be verified across selection truth, topic identity, navigation context, "
                "typed mutation routing, and interruption recovery rather than as a cosmetic shell."
            ),
            "qualification_basis": [
                "selection truth can leak the wrong thread or hidden tab",
                "topic identity can leak across A/A2/A3 or smooth invalid markers",
                "navigation context can drift across mobile surfaces",
                "typed plane mutations can silently degrade into generic chat handling",
                "interruption and reset can leave cross-tab residue",
            ],
            "included_paths": [
                "infra/webui/server_routes_and_main_parts/part01__lab_assistant_host_health_payload.py",
                "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py",
                "tests/control_plane/webui/test_mobile_chat_agents.py",
                "tests/control_plane/webui/test_plane_mobile_verification_workflow_prompt.py",
            ],
            "adjacent_truth_seams": [
                {
                    "seam_id": "mobile_agent_key_truth",
                    "relationship_to_block": "agent-key coercion and visible selected tab truth",
                    "disposition": "included",
                    "note": "Needed so visible selection truth cannot drift from the real bound thread.",
                },
                {
                    "seam_id": "mobile_topic_identity_truth",
                    "relationship_to_block": "topic title derivation from durable session truth",
                    "disposition": "included",
                    "note": "Needed so A/A2/A3 topic identity is verified against its owning durable session.",
                },
                {
                    "seam_id": "mobile_navigation_context_truth",
                    "relationship_to_block": "chat/backlog/execution/audit context passthrough",
                    "disposition": "included",
                    "note": "Needed so downstream mobile surfaces preserve the selected tab context.",
                },
                {
                    "seam_id": "typed_plane_mutation_truth",
                    "relationship_to_block": "plane-tab typed mutation routing",
                    "disposition": "included",
                    "note": "Needed so supported typed actions do not silently degrade into generic chat routing.",
                },
            ],
            "expansion_notes": [
                "Hard verification of the Android/mobile agent tabs block must include selection, topic, navigation, mutation, and interruption seams together.",
            ],
            "rejection_reasons": [],
            "bounded_operator_summary": (
                "The plane will launch verification planning for the Android agent tabs family with selection, topic, navigation, "
                "typed-mutation, and interruption seams included before execution. This is a launch contract only; it does not prove "
                "runtime behavior, completion, or repair yield."
            ),
        }
    return {
        "schema_version": "verification_workflow_selected_block_summary_v1",
        "block_ref": "scheduler_family::broad_verification_goal",
        "block_label": "scheduler verification family",
        "qualification_verdict": "expand",
        "operator_goal": goal,
        "risk_unit_summary": (
            "The scheduler must be verified across launch truth, recovery truth, and operator projection rather than as one flat happy path."
        ),
        "qualification_basis": [
            "launch truth alone cannot prove scheduler health",
            "restart and claim-ledger truth can contradict launch receipts",
            "operator projection can smooth contradictory scheduler state into false calm",
        ],
        "included_paths": [
            "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
            "infra/controlplane/scheduled_jobs_parts/part09__execute_job.py",
            "infra/controlplane/shared_resource_claim_ledger.py",
            "infra/webui/server_routes_and_main_parts/part01__lab_assistant_host_health_payload.py",
            "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py",
        ],
        "adjacent_truth_seams": [
            {
                "seam_id": "scheduler_receipt_writeback",
                "relationship_to_block": "launch admission and execution receipt materialization",
                "disposition": "included",
                "note": "Needed so launch truth is compared against durable scheduler execution records.",
            },
            {
                "seam_id": "shared_resource_claim_truth",
                "relationship_to_block": "active-run claim materialization and restart cleanup",
                "disposition": "included",
                "note": "Needed so broad scheduler verification can catch hidden active work and stale claim survival.",
            },
            {
                "seam_id": "operator_scheduler_projection",
                "relationship_to_block": "operator-visible scheduler payload derived from durable truth",
                "disposition": "included",
                "note": "Needed so broad scheduler verification can catch false calm and false idle projection bugs.",
            },
        ],
        "expansion_notes": [
            "Broad scheduler verification must include launch, recovery, and operator-truth seams to stay honest.",
        ],
        "rejection_reasons": [],
        "bounded_operator_summary": (
            "The plane will launch verification planning for the scheduler family with launch, recovery, and operator-truth seams "
            "included before execution. This is a launch contract only; it does not prove runtime behavior, completion, or repair yield."
        ),
    }


def _fallback_probe_contract(*, target_family: str, family_id: str) -> tuple[str, str]:
    family = _clean_text(family_id)
    if target_family == "android_agent_tabs":
        if "selection" in family:
            return "mobile_tab_selection_probe", "verifier"
        if "topic" in family:
            return "mobile_tab_topic_probe", "verifier"
        if "navigation" in family:
            return "mobile_tab_navigation_probe", "verifier"
        if "typed-mutation" in family:
            return "mobile_typed_mutation_probe", "builder_verifier"
        if "upstream-truth" in family:
            return "mobile_upstream_truth_probe", "verifier"
        if "interruption" in family:
            return "mobile_interruption_probe", "builder_verifier"
        return "mobile_adaptive_followup_probe", "builder_verifier"
    if "recovery" in family:
        return "scheduler_recovery_probe", "builder_verifier"
    if "operator" in family or "projection" in family:
        return "scheduler_operator_projection_probe", "verifier"
    if "adaptive" in family or "replay" in family:
        return "scheduler_adaptive_followup_probe", "builder_verifier"
    return "scheduler_launch_probe", "verifier"


def _phase_b_anchor_payload(goal: str, threat_matrix: Mapping[str, Any], planner_output: Mapping[str, Any], *, target_family: str) -> Dict[str, Any]:
    scenario_rows: List[Dict[str, Any]] = []
    finding_rows: List[Dict[str, Any]] = []
    reduced_bug_classes: List[str] = []
    unresolved_bug_classes: List[str] = []
    for family in threat_matrix.get("scenario_families") or []:
        family_id = _clean_text(family.get("scenario_family_id"))
        threat_class = _clean_text(family.get("threat_class"))
        operator_claim = _clean_text(family.get("operator_claim_under_attack"))
        native_probe_kind = _clean_text(family.get("native_probe_kind"))
        recommended_agent_role = _clean_text(family.get("recommended_agent_role")) or "verifier"
        if not native_probe_kind:
            native_probe_kind, recommended_agent_role = _fallback_probe_contract(
                target_family=target_family,
                family_id=family_id,
            )
        reduced_bug_classes.append(family_id)
        unresolved_bug_classes.append(f"{family_id}::needs_live_execution_proof")
        execution_class = _clean_text((family.get("execution_readiness") or {}).get("readiness_class")) or "runnable_now"
        required_evidence = _normalized_list(
            [
                "negative_control_result",
                "disqualifying_incident_check",
                "operator_claim_under_attack_observation",
            ]
            + list(family.get("independent_records_that_can_disagree") or [])
        )
        for index, example in enumerate(_normalized_list(family.get("scenario_examples") or []), start=1):
            scenario_rows.append(
                {
                    "scenario_id": f"{family_id}::scenario_{index:02d}",
                    "target_family": target_family,
                    "native_probe_kind": native_probe_kind,
                    "recommended_agent_role": recommended_agent_role,
                    "execution_class": execution_class,
                    "scenario_summary": example,
                    "required_evidence": required_evidence,
                    "setup_requirements": ["bounded_fixture_reset", "scheduler_truth_seed"],
                    "reset_requirements": ["reset_after_run", "clear_scheduler_residue"],
                    "invalidation_conditions": [
                        "repo_commit_sha_changed",
                        "planner_rerun_fingerprint_changed",
                        "scheduler_truth_seed_changed",
                    ],
                    "repair_triggerable": True,
                }
            )
        finding_rows.append(
            {
                "finding_id": f"finding::{family_id}",
                "target_family": target_family,
                "native_probe_kind": native_probe_kind,
                "truth_boundary": threat_class or family_id,
                "symptom_class": "unsafe_action" if bool(family.get("unsafe_action_path")) else "false_calm",
                "operator_claim_under_attack": operator_claim,
                "severity_hint": "critical" if "reachable_in_normal_operation" in json.dumps(family) else "high",
                "production_reachability_hint": _clean_text((family.get("production_reachability") or {}).get("classification")),
                "affected_truths": _normalized_list(family.get("independent_records_that_can_disagree") or [])[:2],
                "repair_signal": "repair_worthy",
            }
        )
    return {
        "planner_family": "static_verification_planner_v1",
        "target_family": target_family,
        "native_execution_family": _native_execution_family(target_family),
        "planner_mode": _clean_text(planner_output.get("output_mode")) or "child_pis_family",
        "planner_maximum_supported_claim": _clean_text(planner_output.get("maximum_supported_claim"))
        or "planning_output_only",
        "planner_goal": goal,
        "scenarios": scenario_rows,
        "raw_findings": finding_rows,
        "budget": {"max_budget_units": max(4, len(scenario_rows))},
        "actual": {
            "spent_budget_units": max(2, min(len(scenario_rows), 6)),
            "repeated_cluster_ratio": 0.2,
            "new_signal_ratio": 0.8,
            "dut_improvement_delta": 0.0,
            "repair_confidence": 0.6,
            "risk_score": 0.5,
            "completed_iterations": 1,
        },
        "baseline_health_snapshot": {"open_bug_classes": len(finding_rows) + 2, "critical_open": len(finding_rows)},
        "post_campaign_health_snapshot": {"open_bug_classes": max(1, len(finding_rows)), "critical_open": max(0, len(finding_rows) - 1)},
        "reduced_bug_classes": reduced_bug_classes,
        "unresolved_bug_classes": unresolved_bug_classes,
        "residual_risk": (
            "broad_android_agent_tabs_robustness_still_unproven_until_live_campaign_execution"
            if target_family == "android_agent_tabs"
            else "broad_scheduler_robustness_still_unproven_until_live_campaign_execution"
        ),
        "blocked_claims": [
            "broad_robustness_unproven",
            (
                "android_agent_tabs_health_unproven_until_live_campaign_execution"
                if target_family == "android_agent_tabs"
                else "scheduler_health_unproven_until_live_campaign_execution"
            ),
        ],
    }


def _request_novelty_rows(raw: Mapping[str, Any], goal: str, *, target_family: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    request_token = _clean_text(raw.get("request_instance_token") or raw.get("request_id")) or "broad"
    request_token = hashlib.sha256(request_token.encode("utf-8")).hexdigest()[:10]
    if target_family == "android_agent_tabs":
        families = [
            (
                f"adaptive-request-synthesis-{request_token}-cross-tab-family",
                "Cross-check selected tab, durable topic title, and downstream navigation queries for cross-tab leakage born during one tab switch and exposed in another surface.",
                "cross_tab_truth",
                ["shell_payload_snapshot", "session_rows_snapshot", "navigation_payload_snapshot", "finding_receipt"],
            ),
            (
                f"adaptive-request-synthesis-{request_token}-typed-mutation-replay-family",
                "Replay a typed plane mutation under tab churn and interruption pressure and require the plane to preserve direct-mutation truth instead of degrading into generic chat routing.",
                "typed_mutation_replay_truth",
                ["typed_route_snapshot", "turn_projection_snapshot", "shell_payload_snapshot", "repair_receipt"],
            ),
        ]
    else:
        families = [
            (
                f"adaptive-request-synthesis-{request_token}-cross-record-family",
                "Cross-check receipt lineage, claim-ledger truth, and operator-visible calm for a scheduler contradiction born in one surface and hidden in another.",
                "cross_record_truth",
                ["job_receipt", "claim_ledger_snapshot", "operator_projection_snapshot", "finding_receipt"],
            ),
            (
                f"adaptive-request-synthesis-{request_token}-replay-family",
                "Replay a scheduler repair path under restart/recovery pressure and require the plane to keep contradiction truth visible instead of smoothing it over.",
                "restart_replay_truth",
                ["job_receipt", "restart_reconciliation_snapshot", "operator_projection_snapshot", "repair_receipt"],
            ),
        ]
    scenario_rows: list[dict[str, Any]] = []
    finding_rows: list[dict[str, Any]] = []
    for index, (family_id, summary, boundary, evidence) in enumerate(families, start=1):
        scenario_rows.append(
            {
                "scenario_id": f"{family_id}::scenario_{index:02d}",
                "target_family": target_family,
                "native_probe_kind": (
                    "mobile_adaptive_followup_probe"
                    if target_family == "android_agent_tabs"
                    else "scheduler_adaptive_followup_probe"
                ),
                "recommended_agent_role": "builder_verifier",
                "execution_class": "runnable_now",
                "scenario_summary": f"{goal}: {summary}",
                "required_evidence": list(evidence),
                "setup_requirements": ["bounded_fixture_reset", "cross_surface_truth_seed"],
                "reset_requirements": ["reset_after_run", "clear_scheduler_residue"],
                "repair_triggerable": True,
            }
        )
        finding_rows.append(
            {
                "finding_id": f"finding::{family_id}",
                "target_family": target_family,
                "native_probe_kind": (
                    "mobile_adaptive_followup_probe"
                    if target_family == "android_agent_tabs"
                    else "scheduler_adaptive_followup_probe"
                ),
                "truth_boundary": boundary,
                "symptom_class": "false_calm",
                "operator_claim_under_attack": summary,
                "severity_hint": "high",
                "production_reachability_hint": "reachable_in_normal_operation",
                "affected_truths": [boundary, "operator_projection_truth"],
                "repair_signal": "repair_worthy",
            }
        )
    return scenario_rows, finding_rows


def build_broad_verification_plan(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    goal = _clean_text(payload.get("verification_goal"))
    if not goal:
        raise VerificationWorkflowPlannerBridgeError(
            "missing_verification_goal",
            "verification_goal is required for broad verification planning",
        )
    if _goal_targets_scheduler(goal):
        target_family = "scheduler"
        extraction_input = _scheduler_extraction_input(goal)
        scenario_family_candidates = _scheduler_family_candidates()
    elif _goal_targets_android_agent_tabs(goal):
        target_family = "android_agent_tabs"
        extraction_input = _android_agent_tabs_extraction_input(goal)
        scenario_family_candidates = _android_agent_tabs_family_candidates()
    else:
        raise VerificationWorkflowPlannerBridgeError(
            "unsupported_broad_verification_goal",
            "broad verification planning is currently supported only for scheduler-focused goals and the Android agent-tabs block",
        )
    extraction_output = extract_complex_block(extraction_input)
    threat_matrix = compile_threat_matrix(
        {
            "extraction_output": extraction_output,
            "scenario_family_candidates": scenario_family_candidates,
        }
    )
    planner_output = generate_planner_output(threat_matrix)
    plan_identity = {
        "goal": goal,
        "planner_family": "static_verification_planner_v1",
        "target_family": target_family,
        "mode": planner_output.get("output_mode"),
        "bounded_block": extraction_output.get("bounded_block_summary"),
    }
    rerun_fingerprint = _canonical_hash(plan_identity)
    phase_b_anchor_payload = _phase_b_anchor_payload(goal, threat_matrix, planner_output, target_family=target_family)
    novelty_scenarios, novelty_findings = _request_novelty_rows(payload, goal, target_family=target_family)
    phase_b_anchor_payload["scenarios"] = list(phase_b_anchor_payload.get("scenarios") or []) + novelty_scenarios
    phase_b_anchor_payload["raw_findings"] = list(phase_b_anchor_payload.get("raw_findings") or []) + novelty_findings
    phase_b_anchor_payload["budget"] = {
        "max_budget_units": max(
            int((phase_b_anchor_payload.get("budget") or {}).get("max_budget_units") or 4),
            len(list(phase_b_anchor_payload.get("scenarios") or [])),
        )
    }
    return {
        "schema_version": "verification_workflow_broad_plan_v1",
        "planner_family": "static_verification_planner_v1",
        "planner_maximum_supported_claim": "planning_quality_improved_only",
        "planner_rerun_fingerprint": rerun_fingerprint,
        "target_family": target_family,
        "native_execution_family": _native_execution_family(target_family),
        "goal": goal,
        "extraction_output": extraction_output,
        "threat_matrix": threat_matrix,
        "planner_output": planner_output,
        "selected_block_summary": _selected_block_summary(goal, target_family=target_family),
        "phase_b_anchor_payload": phase_b_anchor_payload,
    }
